"""TikTok Direct Post helper for publishing organic videos."""
from __future__ import annotations

import logging
import time
from typing import Dict

import requests

from tiktok.tiktok_get_auth import get_tiktok_credentials, refresh_tiktok_access_token

logger = logging.getLogger(__name__)
API_BASE = "https://open.tiktokapis.com/v2/post/publish"
STATUS_FETCH_URL = f"{API_BASE}/status/fetch/"
TIKTOK_MAX_CHUNK_SIZE = 64 * 1024 * 1024
TIKTOK_UNAUDITED_PRIVATE_ONLY_CODE = "unaudited_client_can_only_post_to_private_accounts"
TIKTOK_STATUS_POLL_ATTEMPTS = 10
TIKTOK_STATUS_POLL_SECONDS = 2


def _normalize_privacy_level(value: str) -> str:
    normalized = (value or "").strip().upper()
    mapping = {
        "PUBLIC": "PUBLIC_TO_EVERYONE",
        "FRIENDS": "MUTUAL_FOLLOW_FRIENDS",
        "PRIVATE": "SELF_ONLY",
        "PUBLIC_TO_EVERYONE": "PUBLIC_TO_EVERYONE",
        "MUTUAL_FOLLOW_FRIENDS": "MUTUAL_FOLLOW_FRIENDS",
        "FOLLOWER_OF_CREATOR": "FOLLOWER_OF_CREATOR",
        "SELF_ONLY": "SELF_ONLY",
    }
    return mapping.get(normalized, "PUBLIC_TO_EVERYONE")


def _build_source_info(video_bytes: bytes) -> Dict[str, int | str]:
    video_size = len(video_bytes)
    if video_size <= 0:
        raise RuntimeError("TikTok video upload is empty.")
    chunk_size = min(video_size, TIKTOK_MAX_CHUNK_SIZE)
    total_chunk_count = max(1, (video_size + chunk_size - 1) // chunk_size)
    return {
        "source": "FILE_UPLOAD",
        "video_size": video_size,
        "chunk_size": chunk_size,
        "total_chunk_count": total_chunk_count,
    }


def _poll_publish_status(headers: Dict[str, str], publish_id: str) -> Dict[str, str]:
    last_payload: Dict[str, str] = {}
    for poll_index in range(TIKTOK_STATUS_POLL_ATTEMPTS):
        response = requests.post(
            STATUS_FETCH_URL,
            headers=headers,
            json={"publish_id": publish_id},
            timeout=30,
        )
        if response.status_code >= 400:
            logger.error("TikTok status fetch failed: %s - %s", response.status_code, response.text)
            response.raise_for_status()

        payload = response.json().get("data", {}) or {}
        last_payload = payload
        status = str(payload.get("status") or "").upper()
        if status == "PUBLISH_COMPLETE":
            return payload
        if status == "FAILED":
            fail_reason = payload.get("fail_reason") or "unknown_error"
            raise RuntimeError(f"TikTok publish failed after upload: {fail_reason}")
        if poll_index < TIKTOK_STATUS_POLL_ATTEMPTS - 1:
            time.sleep(TIKTOK_STATUS_POLL_SECONDS)

    return last_payload


def publish_tiktok_post(
    video_bytes: bytes,
    *,
    caption: str,
    privacy_level: str = "PUBLIC",
) -> Dict[str, str]:
    """Upload + publish a TikTok video via the Direct Post flow."""

    for attempt in range(2):
        creds = get_tiktok_credentials()
        headers = {"Authorization": f"Bearer {creds['access_token']}", "Content-Type": "application/json"}
        source_info = _build_source_info(video_bytes)
        init_payload = {
            "post_info": {
                "title": caption[:2200],
                "privacy_level": _normalize_privacy_level(privacy_level),
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": source_info,
            "open_id": creds["open_id"],
        }
        init_resp = requests.post(f"{API_BASE}/video/init/", headers=headers, json=init_payload, timeout=30)
        if init_resp.status_code >= 400:
            if init_resp.status_code == 403:
                try:
                    error_payload = init_resp.json().get("error", {})
                except ValueError:
                    error_payload = {}
                if (
                    error_payload.get("code") == TIKTOK_UNAUDITED_PRIVATE_ONLY_CODE
                    and _normalize_privacy_level(privacy_level) != "SELF_ONLY"
                ):
                    logger.info("TikTok app is unaudited; retrying publish as private.")
                    return publish_tiktok_post(
                        video_bytes,
                        caption=caption,
                        privacy_level="PRIVATE",
                    )
            if init_resp.status_code == 401 and attempt == 0:
                logger.info("TikTok token rejected during init upload, refreshing token.")
                refresh_tiktok_access_token()
                continue
            logger.error("TikTok init upload failed: %s - %s", init_resp.status_code, init_resp.text)
            init_resp.raise_for_status()

        payload = init_resp.json().get("data", {})
        upload_url = payload.get("upload_url")
        publish_id = payload.get("publish_id")
        if not upload_url or not publish_id:
            raise RuntimeError("TikTok init upload missing upload_url/publish_id")

        video_size = len(video_bytes)
        upload_headers = {
            "Content-Type": "video/mp4",
            "Content-Length": str(video_size),
            "Content-Range": f"bytes 0-{video_size - 1}/{video_size}",
        }
        upload_resp = requests.put(upload_url, headers=upload_headers, data=video_bytes, timeout=120)
        if upload_resp.status_code >= 400:
            if upload_resp.status_code == 401 and attempt == 0:
                logger.info("TikTok token rejected during upload, refreshing token.")
                refresh_tiktok_access_token()
                continue
            logger.error("TikTok video upload failed: %s - %s", upload_resp.status_code, upload_resp.text)
            upload_resp.raise_for_status()

        try:
            status_payload = _poll_publish_status(headers, publish_id)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 401 and attempt == 0:
                logger.info("TikTok token rejected during status polling, refreshing token.")
                refresh_tiktok_access_token()
                continue
            raise

        return {
            "publish_id": publish_id,
            "status": status_payload.get("status") or "PROCESSING_UPLOAD",
            "post_id": (status_payload.get("publicaly_available_post_id") or [None])[0],
            "uploaded_bytes": status_payload.get("uploaded_bytes"),
        }

    raise RuntimeError("Unable to publish to TikTok after refreshing the access token")
