"""Kaymio Flask app factory and initialization."""
from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask

logger = logging.getLogger(__name__)


def create_app(debug: bool | None = None) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__, instance_relative_config=False)

    # Load configuration
    from . import config

    app.config["SECRET_KEY"] = config.SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH

    # Create storage directories
    app_root = Path(app.root_path).parent
    storage_root = config.get_storage_root(str(app_root))
    for subdir in ("originals", "generated", "videos"):
        (storage_root / subdir).mkdir(parents=True, exist_ok=True)

    # Create state directory
    state_dir = config.get_state_dir(str(app_root))
    state_dir.mkdir(parents=True, exist_ok=True)

    # Initialize database
    from kaymio.database import init_db

    init_db()

    # Register blueprints
    from kaymio.routes import register_blueprints

    register_blueprints(app)

    # Start background tasks (scheduler)
    if debug is None:
        debug = int(app.config.get("DEBUG", 0))
    if not debug:
        from kaymio.tasks import start_instagram_story_scheduler

        start_instagram_story_scheduler()

    logger.info("Kaymio Flask app initialized.")
    return app
