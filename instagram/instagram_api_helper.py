"""Instagram Graph helpers for publishing feed posts and stories."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse, unquote

import requests

from kaymio.kaymio import upload_media_to_wordpress_ext

logger = logging.getLogger(__name__)
GRAPH_API_BASE = "https://graph.facebook.com/v21.0"
MEDIA_PREFIX = "/media/"
TEMPLATE_IMAGES_ROOT = Path(__file__).resolve().parents[1] / "template_images"
TOKEN_FILE = Path(__file__).with_name("instagram_token.json")


def _load_token_file() -> Dict[str, str]:
    if not TOKEN_FILE.exists():
        return {}
    try:
        payload = json.loads(TOKEN_FILE.read_text())
    except json.JSONDecodeError:
        logger.warning("Invalid JSON in %s", TOKEN_FILE)
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _get_instagram_credentials() -> Dict[str, str]:
    access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    user_id = os.getenv("INSTAGRAM_USER_ID")
    if not access_token or not user_id:
        token_payload = _load_token_file()
        access_token = access_token or token_payload.get("INSTAGRAM_ACCESS_TOKEN")
        user_id = user_id or token_payload.get("INSTAGRAM_USER_ID")
    if not access_token or not user_id:
        raise RuntimeError(
            "INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_USER_ID must be configured "
            "(env vars or instagram/instagram_token.json)."
        )
    return {"access_token": access_token, "user_id": user_id}


def _resolve_local_media_path(media_url: str) -> Optional[str]:
    if not media_url:
        return None
    try:
        parsed = urlparse(media_url)
    except ValueError:
        return None
    relative_token = parsed.path.split(MEDIA_PREFIX, 1)
    if len(relative_token) != 2:
        return None
    relative_path = Path(unquote(relative_token[1]))
    candidate = (TEMPLATE_IMAGES_ROOT / relative_path).resolve()
    storage_root = TEMPLATE_IMAGES_ROOT.resolve()
    if not str(candidate).startswith(str(storage_root)) or not candidate.exists():
        return None
    return str(candidate)


def _ensure_public_image_url(image_url: str) -> str:
    local_path = _resolve_local_media_path(image_url)
    if not local_path:
        return image_url
    try:
        uploaded_url = upload_media_to_wordpress_ext(local_path)
        if uploaded_url:
            return uploaded_url
        logger.warning("WordPress upload failed, falling back to local media URL.")
    except Exception:
        logger.exception("Unable to upload Instagram media to WordPress")
    return image_url


def _create_media_container(
    *,
    image_url: str,
    caption: Optional[str],
    media_type: str = "IMAGE",
    share_link: Optional[str] = None,
) -> Dict[str, str]:
    creds = _get_instagram_credentials()
    public_image_url = _ensure_public_image_url(image_url)
    payload = {
        "access_token": creds["access_token"],
        "image_url": public_image_url,
    }
    if caption:
        payload["caption"] = caption[:2200]
    if share_link:
        payload["share_to_story_link"] = share_link
    if media_type.upper() == "STORIES":
        payload["media_type"] = "STORIES"

    creation_url = f"{GRAPH_API_BASE}/{creds['user_id']}/media"
    response = requests.post(creation_url, data=payload, timeout=30)
    if response.status_code >= 400:
        logger.error("Instagram media creation failed: %s - %s", response.status_code, response.text)
        response.raise_for_status()

    creation_id = response.json().get("id")
    if not creation_id:
        raise RuntimeError("Instagram media creation did not return an ID")

    publish_url = f"{GRAPH_API_BASE}/{creds['user_id']}/media_publish"
    publish_payload = {"creation_id": creation_id, "access_token": creds["access_token"]}
    publish_response = requests.post(publish_url, data=publish_payload, timeout=30)
    if publish_response.status_code >= 400:
        logger.error("Instagram media publish failed: %s - %s", publish_response.status_code, publish_response.text)
        publish_response.raise_for_status()

    return publish_response.json()


def publish_instagram_post(
    *,
    image_url: str,
    caption: Optional[str] = None,
    share_link: Optional[str] = None,
) -> Dict[str, str]:
    """Publish an Instagram feed post via the Graph API."""

    return _create_media_container(image_url=image_url, caption=caption, share_link=share_link)


def publish_instagram_story(
    *,
    image_url: str,
    caption: Optional[str] = None,
    share_link: Optional[str] = None,
) -> Dict[str, str]:
    """Publish an Instagram story asset (STORIES media type)."""

    return _create_media_container(
        image_url=image_url,
        caption=caption,
        share_link=share_link,
        media_type="STORIES",
    )
