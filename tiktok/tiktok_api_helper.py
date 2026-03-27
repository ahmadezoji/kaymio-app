"""TikTok Direct Post helper for publishing organic videos."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)
API_BASE = "https://open.tiktokapis.com/v2/post/publish"
OAUTH_AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
OAUTH_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
TOKEN_FILE = Path(__file__).with_name("tiktok_token.json")


def _load_token_file() -> Dict[str, str]:
    try:
        payload = json.loads(TOKEN_FILE.read_text())
    except FileNotFoundError:
        return {}
    except Exception:
        logger.exception("Unable to parse TikTok token file")
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_token_file(payload: Dict[str, str]) -> None:
    TOKEN_FILE.write_text(json.dumps(payload, indent=2))


def _get_tiktok_credentials() -> Dict[str, str]:
    token_payload = _load_token_file()
    token = token_payload.get("access_token") or os.getenv("TIKTOK_ACCESS_TOKEN")
    open_id = token_payload.get("open_id") or os.getenv("TIKTOK_USER_ID")
    if not token or not open_id:
        raise RuntimeError("TIKTOK_ACCESS_TOKEN and TIKTOK_USER_ID must be configured")
    return {"access_token": token, "open_id": open_id}


def has_tiktok_credentials() -> bool:
    try:
        creds = _get_tiktok_credentials()
    except Exception:
        return False
    return bool(creds.get("access_token") and creds.get("open_id"))


def build_tiktok_oauth_url(state: str) -> str:
    client_key = os.getenv("TIKTOK_CLIENT_KEY")
    redirect_uri = os.getenv("TIKTOK_REDIRECT_URI")
    scope = os.getenv("TIKTOK_OAUTH_SCOPE", "user.info.basic,video.publish")
    if not client_key or not redirect_uri:
        raise RuntimeError("TIKTOK_CLIENT_KEY and TIKTOK_REDIRECT_URI must be configured")
    query = urlencode(
        {
            "client_key": client_key,
            "response_type": "code",
            "scope": scope,
            "redirect_uri": redirect_uri,
            "state": state,
            "disable_auto_auth": "1",
        }
    )
    return f"{OAUTH_AUTHORIZE_URL}?{query}"


def exchange_tiktok_oauth_code(code: str) -> Dict[str, str]:
    client_key = os.getenv("TIKTOK_CLIENT_KEY")
    client_secret = os.getenv("TIKTOK_CLIENT_SECRET")
    redirect_uri = os.getenv("TIKTOK_REDIRECT_URI")
    if not client_key or not client_secret or not redirect_uri:
        raise RuntimeError(
            "TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, and TIKTOK_REDIRECT_URI must be configured"
        )
    response = requests.post(
        OAUTH_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    if response.status_code >= 400:
        logger.error("TikTok token exchange failed: %s - %s", response.status_code, response.text)
        response.raise_for_status()
    payload = response.json() or {}
    if not payload.get("access_token") or not payload.get("open_id"):
        raise RuntimeError(f"TikTok token response missing access_token/open_id: {payload}")
    _save_token_file(payload)
    return payload


def publish_tiktok_post(
    video_bytes: bytes,
    *,
    caption: str,
    privacy_level: str = "PUBLIC",
) -> Dict[str, str]:
    """Upload + publish a TikTok video via the Direct Post flow."""

    creds = _get_tiktok_credentials()
    headers = {"Authorization": f"Bearer {creds['access_token']}", "Content-Type": "application/json"}
    init_payload = {
        "source_info": {"source": "FILE_UPLOAD"},
        "open_id": creds["open_id"],
        "post_info": {
            "caption": caption[:2200],
            "privacy_level": privacy_level,
            "disable_duet": False,
            "disable_comment": False,
        },
    }
    init_resp = requests.post(f"{API_BASE}/video/init/", headers=headers, json=init_payload, timeout=30)
    if init_resp.status_code >= 400:
        logger.error("TikTok init upload failed: %s - %s", init_resp.status_code, init_resp.text)
        init_resp.raise_for_status()

    payload = init_resp.json().get("data", {})
    upload_url = payload.get("upload_url")
    publish_id = payload.get("publish_id")
    if not upload_url or not publish_id:
        raise RuntimeError("TikTok init upload missing upload_url/publish_id")

    upload_headers = {"Authorization": f"Bearer {creds['access_token']}", "Content-Type": "video/mp4"}
    upload_resp = requests.put(upload_url, headers=upload_headers, data=video_bytes, timeout=120)
    if upload_resp.status_code >= 400:
        logger.error("TikTok video upload failed: %s - %s", upload_resp.status_code, upload_resp.text)
        upload_resp.raise_for_status()

    publish_payload = {
        "publish_id": publish_id,
        "open_id": creds["open_id"],
    }
    publish_resp = requests.post(f"{API_BASE}/", headers=headers, json=publish_payload, timeout=30)
    if publish_resp.status_code >= 400:
        logger.error("TikTok publish failed: %s - %s", publish_resp.status_code, publish_resp.text)
        publish_resp.raise_for_status()

    return publish_resp.json().get("data", {})
