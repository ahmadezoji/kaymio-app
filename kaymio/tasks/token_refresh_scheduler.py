"""Background scheduler for automatic OAuth token refresh.

Runs every 6 hours to refresh tokens that are expiring soon.
"""
from __future__ import annotations

import logging
import threading
import time

from kaymio.database.token_manager import refresh_all_expiring_tokens

logger = logging.getLogger(__name__)

# Refresh tokens every 6 hours (21600 seconds)
TOKEN_REFRESH_INTERVAL_SECONDS = 6 * 3600


def _token_refresh_loop() -> None:
    """Background loop that refreshes tokens periodically."""
    logger.info("Token refresh scheduler started (interval: %d seconds)", TOKEN_REFRESH_INTERVAL_SECONDS)

    while True:
        try:
            logger.debug("Checking for expiring tokens...")
            results = refresh_all_expiring_tokens()

            # Log results
            for platform, result in results.items():
                if result is True:
                    logger.info("✓ Successfully refreshed %s token", platform)
                elif result is False:
                    logger.warning("✗ Failed to refresh %s token", platform)
                else:
                    logger.debug("%s token is still valid", platform)

            time.sleep(TOKEN_REFRESH_INTERVAL_SECONDS)

        except Exception:
            logger.exception("Error in token refresh loop")
            time.sleep(60)  # Retry after 1 minute on error


def start_token_refresh_scheduler() -> None:
    """Start the background token refresh scheduler."""
    thread = threading.Thread(
        target=_token_refresh_loop,
        name="token-refresh-scheduler",
        daemon=True,
    )
    thread.start()
    logger.info("Token refresh scheduler thread started")
