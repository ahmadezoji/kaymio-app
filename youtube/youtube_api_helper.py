"""YouTube Data API helper for uploading Shorts."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, Iterable, Optional

import requests

logger = logging.getLogger(__name__)
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def _read_token_file(path: Path) -> Optional[str]:
    try:
        token = path.read_text().strip()
        return token or None
    except FileNotFoundError:
        return None


def _persist_access_token(token: str) -> None:
    module_path = Path(__file__).resolve().parent / "youtube_access_token.txt"
    root_path = Path.cwd() / "youtube_access_token.txt"
    module_path.write_text(token)
    root_path.write_text(token)


def _get_youtube_token() -> str:
    module_token = _read_token_file(Path(__file__).resolve().parent / "youtube_access_token.txt")
    if module_token:
        return module_token
    root_token = _read_token_file(Path.cwd() / "youtube_access_token.txt")
    if root_token:
        return root_token
    return refresh_youtube_access_token()


def refresh_youtube_access_token() -> str:
    """Exchange a stored refresh token for a short-lived access token."""

    client_id = os.getenv("YOUTUBE_CLIENT_ID")
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")
    refresh_token = os.getenv("YOUTUBE_REFRESH_TOKEN")
    if not all([client_id, client_secret, refresh_token]):
        raise RuntimeError(
            "Missing YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, or YOUTUBE_REFRESH_TOKEN in environment"
        )

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    response = requests.post(TOKEN_URL, data=payload, timeout=30)
    if response.status_code >= 400:
        logger.error(
            "Failed to refresh YouTube access token: %s - %s", response.status_code, response.text
        )
        response.raise_for_status()

    data = response.json()
    access_token = data.get("access_token")
    if not access_token:
        raise RuntimeError("YouTube token response did not include an access_token")

    # Persist for future requests.
    _persist_access_token(access_token)
    return access_token


def publish_short_video(
    video_bytes: bytes,
    *,
    title: str,
    description: str,
    tags: Optional[Iterable[str]] = None,
    privacy_status: str = "public",
) -> Dict[str, str]:
    """Upload a short-form MP4 to YouTube using the resumable upload flow."""
    metadata = {
        "snippet": {
            "title": title[:100] or "Untitled Short",
            "description": description[:5000],
            "categoryId": "22",
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }
    if tags:
        metadata["snippet"]["tags"] = [str(tag)[:30] for tag in tags]

    for attempt in range(2):
        token = _get_youtube_token()
        init_headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(len(video_bytes)),
        }
        init_resp = requests.post(
            f"{UPLOAD_URL}?uploadType=resumable&part=snippet,status",
            headers=init_headers,
            json=metadata,
            timeout=30,
        )
        if init_resp.status_code >= 400:
            if init_resp.status_code == 401 and attempt == 0:
                logger.info("YouTube token expired during init upload, refreshing token.")
                refresh_youtube_access_token()
                continue
            logger.error("YouTube init upload failed: %s - %s", init_resp.status_code, init_resp.text)
            init_resp.raise_for_status()

        upload_endpoint = init_resp.headers.get("Location")
        if not upload_endpoint:
            raise RuntimeError("YouTube did not return an upload location")

        upload_headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "video/mp4",
        }
        upload_resp = requests.put(
            upload_endpoint,
            headers=upload_headers,
            data=video_bytes,
            timeout=120,
        )
        if upload_resp.status_code >= 400:
            if upload_resp.status_code == 401 and attempt == 0:
                logger.info("YouTube token expired during upload, refreshing token.")
                refresh_youtube_access_token()
                continue
            logger.error("YouTube upload failed: %s - %s", upload_resp.status_code, upload_resp.text)
            upload_resp.raise_for_status()

        data = upload_resp.json()
        return {
            "status": data.get("status", {}).get("uploadStatus", "uploaded"),
            "video_id": data.get("id"),
            "url": f"https://youtube.com/watch?v={data.get('id')}" if data.get("id") else None,
        }

    raise RuntimeError("Unable to publish to YouTube after refreshing the access token")
