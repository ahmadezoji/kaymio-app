"""Instagram Graph helpers for publishing feed posts and stories."""
from __future__ import annotations

import logging
import os
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)
GRAPH_API_BASE = "https://graph.facebook.com/v21.0"


def _get_instagram_credentials() -> Dict[str, str]:
    access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    user_id = os.getenv("INSTAGRAM_USER_ID")
    if not access_token or not user_id:
        raise RuntimeError("INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_USER_ID must be configured")
    return {"access_token": access_token, "user_id": user_id}


def _create_media_container(
    *,
    image_url: str,
    caption: Optional[str],
    media_type: str = "IMAGE",
    share_link: Optional[str] = None,
) -> Dict[str, str]:
    creds = _get_instagram_credentials()
    payload = {
        "access_token": creds["access_token"],
        "image_url": image_url,
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
