"""Entry point for the Kaymio Flask application."""
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from kaymio.app import app, start_background_services  # noqa: E402
from kaymio.database import init_db  # noqa: E402

if __name__ == "__main__":
    debug_enabled = os.getenv("FLASK_DEBUG", "0") in {"1", "true", "yes", "on"}
    print("DEBUG: Initializing database...", file=sys.stderr)
    sys.stderr.flush()
    init_db()
    # With the debug reloader the parent process only watches files; start
    # background threads once, in the serving child, so the Telegram poller
    # doesn't run twice.
    if not debug_enabled or os.getenv("WERKZEUG_RUN_MAIN") == "true":
        print("DEBUG: Starting schedulers and Telegram listener...", file=sys.stderr)
        sys.stderr.flush()
        start_background_services()
    print("DEBUG: Starting Flask app...", file=sys.stderr)
    sys.stderr.flush()
    app.run(debug=debug_enabled, host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
