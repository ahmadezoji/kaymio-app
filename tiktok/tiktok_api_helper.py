"""TikTok Direct Post helper for publishing organic videos."""
from __future__ import annotations

import logging
import os
from typing import Dict

import requests

logger = logging.getLogger(__name__)
API_BASE = "https://open.tiktokapis.com/v2/post/publish"


def _get_tiktok_credentials() -> Dict[str, str]:
    token = os.getenv("TIKTOK_ACCESS_TOKEN")
    open_id = os.getenv("TIKTOK_USER_ID")
    if not token or not open_id:
        raise RuntimeError("TIKTOK_ACCESS_TOKEN and TIKTOK_USER_ID must be configured")
    return {"access_token": token, "open_id": open_id}


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
