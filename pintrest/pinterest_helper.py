"""Pinterest helper utilities."""
from __future__ import annotations

import base64
import datetime as dt
import logging
import os
from pathlib import Path
from typing import Dict, Iterable, Optional

import requests

logger = logging.getLogger(__name__)
API_URL = "https://api.pinterest.com/v5/pins"


PINTEREST_ANALYTICS_URL = "https://api.pinterest.com/v5/user_account/analytics"


def _load_pinterest_access_token() -> Optional[str]:
    for candidate in ("pintrest/access_token.txt", "pintrest_access_token.txt", "access_token.txt"):
        try:
            token = Path(candidate).read_text().strip()
            if token:
                return token
        except FileNotFoundError:
            continue
    return None


def fetch_pinterest_analytics(days: int = 30) -> Dict[str, object]:
    token = _load_pinterest_access_token()
    if not token:
        return {"error": "Missing Pinterest access token."}

    end_date = dt.date.today()
    start_date = end_date - dt.timedelta(days=days)
    metrics = [
        "IMPRESSION",
        "ENGAGEMENT",
        "OUTBOUND_CLICK",
        "SAVE",
        "TOTAL_AUDIENCE",
        "ENGAGED_AUDIENCE",
    ]
    params = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "from_claimed_content": "BOTH",
        "pin_format": "ALL",
        "app_types": "ALL",
        "content_type": "ALL",
        "source": "ALL",
        "metric_types": ",".join(metrics),
        "split_field": "NO_SPLIT",
    }
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.get(PINTEREST_ANALYTICS_URL, params=params, headers=headers, timeout=30)
        if response.status_code >= 400:
            return {"error": f"Pinterest API error: {response.status_code} - {response.text}"}
        payload = response.json()
    except Exception as exc:
        return {"error": f"Pinterest request failed: {exc}"}

    daily = payload.get("all", {}).get("daily_metrics", [])
    totals = {name: 0 for name in metrics}
    trend = []
    for row in daily:
        metric_values = row.get("metrics", {})
        for name in metrics:
            value = metric_values.get(name)
            if isinstance(value, (int, float)):
                totals[name] += value
        if "IMPRESSION" in metric_values:
            trend.append(metric_values.get("IMPRESSION") or 0)

    return {
        "period": f"Last {days} days",
        "metrics": {
            "Impressions": totals.get("IMPRESSION"),
            "Engagements": totals.get("ENGAGEMENT"),
            "Outbound clicks": totals.get("OUTBOUND_CLICK"),
            "Saves": totals.get("SAVE"),
            "Total audience": totals.get("TOTAL_AUDIENCE"),
            "Engaged audience": totals.get("ENGAGED_AUDIENCE"),
        },
        "trend": trend,
    }


def create_pinterest_pin(
    image_bytes: bytes,
    title: str,
    description: str,
    affiliate_link: Optional[str],
    tags: Optional[Iterable[str]] = None,
) -> Dict[str, str]:
    """Upload a pin to Pinterest using the v5 API."""

    access_token = _load_pinterest_access_token()

    if not access_token:
        logger.warning("Pinterest access token not found")
        return None
    
    board_id = os.getenv("PINTEREST_BOARD_ID") or get_default_board_id()
    
    if not access_token or not board_id:
        logger.warning("Pinterest credentials missing; returning local-only payload.")
        return {
            "status": "skipped",
            "id": None,
            "url": None,
        }

    tags = list(tags or [])
    if tags:
        hashtag_block = " ".join(f"#{kw.replace(' ', '')}" for kw in tags if kw)
        if hashtag_block:
            description = f"{description} {hashtag_block}".strip()

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
        payload["note"] = ", ".join(tags)[:250]

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    response = requests.post(API_URL, headers=headers, json=payload, timeout=30)
    if response.status_code >= 400:
        logger.error("Pinterest API error: %s - %s", response.status_code, response.text)
        response.raise_for_status()

    data = response.json()
    pin_id = data.get("id") or data.get("pin_id")
    pin_url = data.get("url")
    if not pin_url and pin_id:
        pin_url = f"https://www.pinterest.com/pin/{pin_id}/"

    return {
        "status": data.get("status") or "created",
        "id": pin_id,
        "url": pin_url,
    }


def get_default_board_id():
    try:
        access_token = _load_pinterest_access_token()
        if not access_token:
            logger.warning("Pinterest access token not found")
            return None
        response = requests.get(
            'https://api.pinterest.com/v5/boards',
            headers={'Authorization': f'Bearer {access_token}'}
        )

        if response.status_code == 200:
            boards = response.json().get('items', [])
            if boards:
                return boards[0].get('id')

        return None

    except Exception as e:
        logger.warning("Error getting boards: %s", e)
        return None
