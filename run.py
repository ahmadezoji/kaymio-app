"""Entry point for the Kaymio Flask application."""
import os

from dotenv import load_dotenv

load_dotenv()

from kaymio.app import app, start_instagram_story_scheduler  # noqa: E402
from kaymio.database import init_db  # noqa: E402

if __name__ == "__main__":
    debug_enabled = os.getenv("FLASK_DEBUG", "0") in {"1", "true", "yes", "on"}
    init_db()
    start_instagram_story_scheduler()
    app.run(debug=debug_enabled, host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
