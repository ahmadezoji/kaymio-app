"""Pinterest helper utilities."""
from __future__ import annotations

import base64
import datetime as dt
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import requests

logger = logging.getLogger(__name__)
API_URL = "https://api.pinterest.com/v5/pins"
MEDIA_URL = "https://api.pinterest.com/v5/media"


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


def _extract_metric_total(payload: Dict[str, object], metric_name: str) -> float:
    all_block = payload.get("all", {}) if isinstance(payload, dict) else {}
    if isinstance(all_block, dict):
        summary_metrics = all_block.get("summary_metrics", {})
        if isinstance(summary_metrics, dict):
            value = summary_metrics.get(metric_name)
            if isinstance(value, (int, float)):
                return float(value)
        daily_metrics = all_block.get("daily_metrics", [])
        if isinstance(daily_metrics, list):
            total = 0.0
            found = False
            for row in daily_metrics:
                if not isinstance(row, dict):
                    continue
                metrics = row.get("metrics", {})
                if not isinstance(metrics, dict):
                    continue
                value = metrics.get(metric_name)
                if isinstance(value, (int, float)):
                    total += float(value)
                    found = True
            if found:
                return total
    summary_metrics = payload.get("summary_metrics", {}) if isinstance(payload, dict) else {}
    if isinstance(summary_metrics, dict):
        value = summary_metrics.get(metric_name)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _short_pin_label(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return "Pin"
    word = text.split()[0].strip(".,:;!?()[]{}\"'`")
    return word[:20] if word else "Pin"


def _fetch_pin_insights(
    token: str,
    pin: Dict[str, object],
    start_date: dt.date,
    end_date: dt.date,
) -> Optional[Dict[str, object]]:
    pin_id = str(pin.get("id") or "")
    if not pin_id:
        return None
    params = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "metric_types": "IMPRESSION,ENGAGEMENT",
        "split_field": "NO_SPLIT",
    }
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(
        f"{API_URL}/{pin_id}/analytics",
        params=params,
        headers=headers,
        timeout=30,
    )
    if response.status_code >= 400:
        logger.warning("Pinterest pin analytics failed for %s: %s - %s", pin_id, response.status_code, response.text)
        return None
    payload = response.json() or {}
    views = int(_extract_metric_total(payload, "IMPRESSION"))
    engagement = int(_extract_metric_total(payload, "ENGAGEMENT"))
    title = _short_pin_label(str(pin.get("title") or pin_id))
    return {
        "pin_id": pin_id,
        "label": title[:60],
        "created_at": str(pin.get("created_at") or ""),
        "views": views,
        "engagement": engagement,
    }


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
        "PIN_CLICK",
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

    pin_rows: List[Dict[str, object]] = []
    try:
        pins_resp = requests.get(
            API_URL,
            params={"page_size": "25"},
            headers=headers,
            timeout=30,
        )
        if pins_resp.status_code < 400:
            items = (pins_resp.json() or {}).get("items") or []
            pins = [item for item in items if isinstance(item, dict)]
            pins.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
            candidates = pins[:5]
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = [
                    executor.submit(_fetch_pin_insights, token, pin, start_date, end_date)
                    for pin in candidates
                ]
                for future in as_completed(futures):
                    row = future.result()
                    if row:
                        pin_rows.append(row)
            pin_rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    except Exception:
        logger.exception("Pinterest last-5 pin insights fetch failed.")

    pin_views_series = [int(row.get("views") or 0) for row in reversed(pin_rows)]
    pin_engagement_series = [int(row.get("engagement") or 0) for row in reversed(pin_rows)]

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
        "pins": {
            "period": f"Last {len(pin_rows)} pins" if pin_rows else "Last 5 pins",
            "metrics": {
                "Views": sum(int(row.get("views") or 0) for row in pin_rows),
                "Engagement": sum(int(row.get("engagement") or 0) for row in pin_rows),
            },
            "series": {
                "views": pin_views_series,
                "engagement": pin_engagement_series,
            },
            "rows": [
                {
                    "label": str(row.get("label") or f"Pin {idx}"),
                    "views": str(int(row.get("views") or 0)),
                    "engagement": str(int(row.get("engagement") or 0)),
                }
                for idx, row in enumerate(pin_rows, start=1)
            ],
        },
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


def create_pinterest_video_pin(
    video_bytes: bytes,
    title: str,
    description: str,
    affiliate_link: Optional[str],
    tags: Optional[Iterable[str]] = None,
    cover_image_url: Optional[str] = None,
) -> Dict[str, str]:
    """Create a Pinterest video pin via Pinterest media upload + video_id pin flow."""
    access_token = _load_pinterest_access_token()
    if not access_token:
        logger.warning("Pinterest access token not found")
        return {"status": "skipped", "id": None, "url": None}

    board_id = os.getenv("PINTEREST_BOARD_ID") or get_default_board_id()
    if not board_id:
        logger.warning("Pinterest board id missing; returning local-only payload.")
        return {"status": "skipped", "id": None, "url": None}

    tags = list(tags or [])
    if tags:
        hashtag_block = " ".join(f"#{kw.replace(' ', '')}" for kw in tags if kw)
        if hashtag_block:
            description = f"{description} {hashtag_block}".strip()

    # 1) Register video upload and receive signed upload URL/fields.
    register_resp = requests.post(
        MEDIA_URL,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={"media_type": "video"},
        timeout=30,
    )
    if register_resp.status_code >= 400:
        logger.error(
            "Pinterest media register failed: %s - %s",
            register_resp.status_code,
            register_resp.text,
        )
        register_resp.raise_for_status()
    media_payload = register_resp.json()
    media_id = media_payload.get("media_id")
    upload_url = media_payload.get("upload_url")
    upload_parameters = media_payload.get("upload_parameters") or {}
    if not media_id or not upload_url:
        raise RuntimeError("Pinterest did not return media_id/upload_url for video upload.")

    # 2) Upload raw video bytes to signed storage endpoint.
    upload_data = dict(upload_parameters)
    upload_resp = requests.post(
        upload_url,
        data=upload_data,
        files={"file": ("video.mp4", video_bytes, "video/mp4")},
        timeout=120,
    )
    if upload_resp.status_code >= 400:
        logger.error(
            "Pinterest media upload failed: %s - %s",
            upload_resp.status_code,
            upload_resp.text,
        )
        upload_resp.raise_for_status()

    # 3) Poll media status until succeeded.
    media_status = "registered"
    for _ in range(20):
        status_resp = requests.get(
            f"{MEDIA_URL}/{media_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        if status_resp.status_code >= 400:
            logger.error(
                "Pinterest media status check failed: %s - %s",
                status_resp.status_code,
                status_resp.text,
            )
            status_resp.raise_for_status()
        media_status = (status_resp.json() or {}).get("status") or media_status
        if media_status == "succeeded":
            break
        if media_status == "failed":
            raise RuntimeError("Pinterest video processing failed.")
        time.sleep(3)
    if media_status != "succeeded":
        raise RuntimeError(f"Pinterest video still not ready (status: {media_status}).")

    # 4) Create pin using source_type=video_id.
    payload = {
        "board_id": board_id,
        "title": (title or "Video pin")[:100],
        "description": (description or "")[:500],
        "link": affiliate_link,
        "media_source": {
            "source_type": "video_id",
            "media_id": media_id,
        },
        "alt_text": (description or "")[:500],
    }
    if cover_image_url:
        payload["media_source"]["cover_image_url"] = cover_image_url
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    response = requests.post(API_URL, headers=headers, json=payload, timeout=30)
    if response.status_code >= 400:
        logger.error("Pinterest video API error: %s - %s", response.status_code, response.text)
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
