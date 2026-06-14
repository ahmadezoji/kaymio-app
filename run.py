"""Entry point for the Kaymio Flask application."""
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from kaymio.app import app, start_instagram_story_scheduler  # noqa: E402
from kaymio.database import init_db  # noqa: E402
from kaymio.tasks import start_token_refresh_scheduler  # noqa: E402

if __name__ == "__main__":
    debug_enabled = os.getenv("FLASK_DEBUG", "0") in {"1", "true", "yes", "on"}
    print("DEBUG: Initializing database...", file=sys.stderr)
    sys.stderr.flush()
    init_db()
    print("DEBUG: Starting Instagram story scheduler...", file=sys.stderr)
    sys.stderr.flush()
    start_instagram_story_scheduler()
    print("DEBUG: Starting OAuth token refresh scheduler...", file=sys.stderr)
    sys.stderr.flush()
    start_token_refresh_scheduler()
    print("DEBUG: Starting Flask app...", file=sys.stderr)
    sys.stderr.flush()
    app.run(debug=debug_enabled, host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
