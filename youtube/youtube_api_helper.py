"""YouTube Data API helper for uploading Shorts."""
from __future__ import annotations

import logging
import os
from typing import Dict, Iterable, Optional

import requests

logger = logging.getLogger(__name__)
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"


def _get_youtube_token() -> str:
    token = os.getenv("YOUTUBE_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("YOUTUBE_ACCESS_TOKEN is required to publish videos")
    return token


def publish_short_video(
    video_bytes: bytes,
    *,
    title: str,
    description: str,
    tags: Optional[Iterable[str]] = None,
    privacy_status: str = "unlisted",
) -> Dict[str, str]:
    """Upload a short-form MP4 to YouTube using the resumable upload flow."""

    token = _get_youtube_token()
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
        logger.error("YouTube upload failed: %s - %s", upload_resp.status_code, upload_resp.text)
        upload_resp.raise_for_status()

    data = upload_resp.json()
    return {
        "status": data.get("status", {}).get("uploadStatus", "uploaded"),
        "video_id": data.get("id"),
        "url": f"https://youtube.com/watch?v={data.get('id')}" if data.get("id") else None,
    }
