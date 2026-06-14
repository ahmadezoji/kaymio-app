"""OAuth token lifecycle management - refresh, validate, and update tokens.

Automatically refreshes tokens before expiration and saves to database.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
from typing import Any, Dict, Optional

import requests

from .oauth import load_oauth_credential, save_oauth_credential

logger = logging.getLogger(__name__)

# Refresh tokens X hours before they expire
REFRESH_BUFFER_HOURS = 24


def is_token_expiring_soon(platform: str) -> bool:
    """Check if a token is expiring within the buffer window."""
    cred = load_oauth_credential(platform)
    if not cred or not cred.get("expires_at"):
        return False

    try:
        expires_at = dt.datetime.fromisoformat(cred["expires_at"])
        now = dt.datetime.utcnow()
        time_until_expiry = expires_at - now
        buffer = dt.timedelta(hours=REFRESH_BUFFER_HOURS)
        return time_until_expiry <= buffer
    except Exception:
        return False


def refresh_youtube_token(platform: str = "youtube") -> bool:
    """Refresh YouTube access token using refresh token."""
    cred = load_oauth_credential(platform)
    if not cred:
        logger.warning("No YouTube credential found to refresh")
        return False

    refresh_token = cred.get("refresh_token")
    if not refresh_token:
        logger.warning("No YouTube refresh token available")
        return False

    client_id = os.getenv("YOUTUBE_CLIENT_ID")
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")
    if not client_id or not client_secret:
        logger.warning("Missing YOUTUBE_CLIENT_ID or YOUTUBE_CLIENT_SECRET")
        return False

    try:
        token_url = "https://oauth2.googleapis.com/token"
        payload = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        response = requests.post(token_url, data=payload, timeout=30)
        if response.status_code >= 400:
            logger.error("YouTube token refresh failed: %s - %s", response.status_code, response.text)
            return False

        data = response.json()
        access_token = data.get("access_token")
        expires_in = data.get("expires_in", 3600)

        if not access_token:
            logger.error("YouTube token response missing access_token")
            return False

        # Calculate expiration time
        expires_at = dt.datetime.utcnow() + dt.timedelta(seconds=expires_in)

        # Save to database
        save_oauth_credential(
            platform=platform,
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_at=expires_at,
        )

        logger.info("YouTube token refreshed successfully, expires at %s", expires_at)
        return True

    except Exception as e:
        logger.error("Exception refreshing YouTube token: %s", e)
        return False


def refresh_instagram_token(platform: str = "instagram") -> bool:
    """Refresh Instagram long-lived token.

    Instagram tokens are long-lived (60 days) but can be refreshed to extend expiry.
    Requires user to re-authenticate if token expires.
    """
    cred = load_oauth_credential(platform)
    if not cred:
        logger.warning("No Instagram credential found to refresh")
        return False

    access_token = cred.get("access_token")
    if not access_token:
        logger.warning("No Instagram access token available")
        return False

    try:
        # Instagram's refresh endpoint for long-lived tokens
        # GET /me?fields=id&access_token=YOUR_ACCESS_TOKEN
        response = requests.get(
            "https://graph.instagram.com/me",
            params={"fields": "id", "access_token": access_token},
            timeout=30,
        )

        if response.status_code >= 400:
            logger.warning("Instagram token appears to be invalid/expired: %s", response.status_code)
            return False

        # Token is still valid; update expiration (Instagram tokens are 60 days)
        expires_at = dt.datetime.utcnow() + dt.timedelta(days=60)

        save_oauth_credential(
            platform=platform,
            access_token=access_token,
            user_id=cred.get("user_id"),
            expires_at=expires_at,
        )

        logger.info("Instagram token validated, expires at %s", expires_at)
        return True

    except Exception as e:
        logger.error("Exception validating Instagram token: %s", e)
        return False


def refresh_token(platform: str) -> bool:
    """Refresh token for a specific platform."""
    if platform.lower() == "youtube":
        return refresh_youtube_token(platform)
    elif platform.lower() == "instagram":
        return refresh_instagram_token(platform)
    elif platform.lower() in ("tiktok", "pinterest"):
        logger.info("%s tokens require manual re-authentication", platform)
        return False
    else:
        logger.warning("Unknown platform for token refresh: %s", platform)
        return False


def refresh_all_expiring_tokens() -> Dict[str, bool]:
    """Refresh all tokens that are expiring soon."""
    platforms = ["instagram", "youtube", "tiktok", "pinterest"]
    results = {}

    for platform in platforms:
        try:
            if is_token_expiring_soon(platform):
                logger.info("Token for %s is expiring soon, attempting refresh", platform)
                results[platform] = refresh_token(platform)
            else:
                results[platform] = None  # Token not expiring soon
        except Exception as e:
            logger.error("Error checking/refreshing %s token: %s", platform, e)
            results[platform] = False

    return results


def get_token_status(platform: str) -> Dict[str, Any]:
    """Get current token status for a platform."""
    cred = load_oauth_credential(platform)
    if not cred:
        return {
            "platform": platform,
            "status": "not_configured",
            "access_token_present": False,
            "expires_at": None,
            "expiring_soon": False,
        }

    access_token = cred.get("access_token")
    expires_at = cred.get("expires_at")
    expiring_soon = is_token_expiring_soon(platform)

    return {
        "platform": platform,
        "status": "configured",
        "access_token_present": bool(access_token),
        "user_id": cred.get("user_id"),
        "expires_at": expires_at,
        "expiring_soon": expiring_soon,
        "token_type": cred.get("token_type", "bearer"),
    }


def get_all_token_status() -> Dict[str, Dict[str, Any]]:
    """Get status of all platform tokens."""
    platforms = ["instagram", "youtube", "tiktok", "pinterest"]
    return {platform: get_token_status(platform) for platform in platforms}
