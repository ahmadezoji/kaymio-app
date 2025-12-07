"""Pinterest helper utilities."""
from __future__ import annotations

import base64
import logging
import os
from typing import Dict, Iterable, Optional

import requests

logger = logging.getLogger(__name__)
API_URL = "https://api.pinterest.com/v5/pins"


def create_pinterest_pin(
    image_bytes: bytes,
    title: str,
    description: str,
    affiliate_link: Optional[str],
    tags: Optional[Iterable[str]] = None,
) -> Dict[str, str]:
    """Upload a pin to Pinterest using the v5 API."""

    access_token = os.getenv("PINTEREST_ACCESS_TOKEN")
    board_id = os.getenv("PINTEREST_BOARD_ID")
    if not access_token or not board_id:
        logger.warning("Pinterest credentials missing; returning local-only payload.")
        return {
            "status": "skipped",
            "id": None,
            "url": None,
        }

    encoded_image = base64.b64encode(image_bytes).decode("utf-8")
    payload = {
        "board_id": board_id,
        "title": title[:100],
        "description": description[:500],
        "link": affiliate_link,
        "media_source": {
            "source_type": "image_base64",
            "content_type": "image/jpeg",
            "data": encoded_image,
        },
        "alt_text": description[:500],
    }
    if tags:
        payload["note"] = ", ".join(list(tags))[:250]

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    response = requests.post(API_URL, headers=headers, json=payload, timeout=30)
    if response.status_code >= 400:
        logger.error("Pinterest API error: %s - %s", response.status_code, response.text)
        response.raise_for_status()

    data = response.json()
    return {
        "status": "created",
        "id": data.get("id") or data.get("pin_id"),
        "url": data.get("link") or data.get("url"),
    }
