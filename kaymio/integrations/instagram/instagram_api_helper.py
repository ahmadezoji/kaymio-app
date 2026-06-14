"""Instagram Graph helpers for publishing feed posts, stories, and reels."""
from __future__ import annotations

import logging
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse, unquote

import requests

from kaymio.wordpress.wordpress_api_helper import upload_media_to_wordpress_ext

logger = logging.getLogger(__name__)
FACEBOOK_GRAPH_API_BASE = "https://graph.facebook.com/v21.0"
INSTAGRAM_GRAPH_API_BASE = "https://graph.instagram.com/v21.0"
MEDIA_PREFIX = "/media/"
TEMPLATE_IMAGES_ROOT = Path(__file__).resolve().parents[1] / "template_images"
PUBLISH_STATUS_TIMEOUT_SECONDS = 60
PUBLISH_STATUS_POLL_SECONDS = 3
INSTAGRAM_ANALYTICS_CACHE_TTL_SECONDS = int(os.getenv("INSTAGRAM_ANALYTICS_CACHE_TTL_SECONDS", "180"))
INSTAGRAM_ANALYTICS_MAX_MEDIA = int(os.getenv("INSTAGRAM_ANALYTICS_MAX_MEDIA", "15"))
INSTAGRAM_ANALYTICS_MAX_PER_TYPE = int(os.getenv("INSTAGRAM_ANALYTICS_MAX_PER_TYPE", "5"))
INSTAGRAM_ANALYTICS_WORKERS = int(os.getenv("INSTAGRAM_ANALYTICS_WORKERS", "6"))
_INSTAGRAM_ANALYTICS_CACHE: Dict[str, object] = {"expires_at": 0.0, "payload": None}


def _uses_instagram_login_flow(payload: Dict[str, str]) -> bool:
    auth_flow = str(payload.get("AUTH_FLOW") or "").strip().lower()
    if auth_flow == "instagram_login":
        return True
    if auth_flow == "facebook_login":
        return False
    return _token_payload_looks_like_login_only(payload)


def _token_payload_looks_like_login_only(payload: Dict[str, str]) -> bool:
    if not payload:
        return False
    required_publish_keys = (
        "INSTAGRAM_PAGE_ACCESS_TOKEN",
        "FACEBOOK_PAGE_ID",
        "FB_PAGE_ID",
        "FB_LONG_LIVED_USER_ACCESS_TOKEN",
    )
    return not any(payload.get(key) for key in required_publish_keys)


def _resolve_graph_api_base(token_payload: Optional[Dict[str, str]] = None) -> str:
    payload = token_payload if token_payload is not None else _load_token_from_db()
    return INSTAGRAM_GRAPH_API_BASE if _uses_instagram_login_flow(payload) else FACEBOOK_GRAPH_API_BASE


def _load_token_from_db() -> Dict[str, str]:
    """Load Instagram credentials from the oauth_credentials table."""
    try:
        from kaymio.database.oauth import load_oauth_credential
        cred = load_oauth_credential("instagram")
        if cred:
            return {
                "INSTAGRAM_ACCESS_TOKEN": cred.get("access_token", ""),
                "INSTAGRAM_USER_ID": cred.get("user_id", ""),
                "AUTH_FLOW": "instagram_login",
            }
    except Exception as e:
        logger.debug("Failed to load Instagram credentials from DB: %s", e)
    return {}


def _get_instagram_credentials() -> Dict[str, str]:
    token_payload = _load_token_from_db()
    # The oauth_credentials table is the live source of truth (refreshed via the
    # OAuth flow); env vars are a fallback for environments without a DB row.
    access_token = token_payload.get("INSTAGRAM_ACCESS_TOKEN") or os.getenv("INSTAGRAM_ACCESS_TOKEN")
    user_id = token_payload.get("INSTAGRAM_USER_ID") or os.getenv("INSTAGRAM_USER_ID")
    if not access_token or not user_id:
        raise RuntimeError(
            "INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_USER_ID must be configured "
            "(env vars or the oauth_credentials table)."
        )
    return {"access_token": access_token, "user_id": user_id}


def _get_instagram_messaging_credentials() -> Dict[str, str]:
    token_payload = _load_token_from_db()
    env_instagram_access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    env_page_access_token = os.getenv("INSTAGRAM_PAGE_ACCESS_TOKEN")
    env_instagram_user_id = os.getenv("INSTAGRAM_USER_ID")
    use_instagram_login = _uses_instagram_login_flow(token_payload)

    db_page_access_token = token_payload.get("INSTAGRAM_PAGE_ACCESS_TOKEN")
    db_fallback_access_token = token_payload.get("FB_LONG_LIVED_USER_ACCESS_TOKEN")
    db_instagram_access_token = token_payload.get("INSTAGRAM_ACCESS_TOKEN")
    db_instagram_user_id = token_payload.get("INSTAGRAM_USER_ID")

    access_token = (
        (db_instagram_access_token or env_instagram_access_token)
        if use_instagram_login
        else (db_page_access_token or db_fallback_access_token or env_page_access_token)
    )
    instagram_user_id = db_instagram_user_id or env_instagram_user_id

    if not access_token or not instagram_user_id:
        raise RuntimeError(
            "Instagram messaging credentials must be configured via "
            "INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_USER_ID for Instagram Login, "
            "or INSTAGRAM_PAGE_ACCESS_TOKEN/FB_LONG_LIVED_USER_ACCESS_TOKEN plus "
            "INSTAGRAM_USER_ID for Facebook Login."
        )
    return {
        "access_token": access_token,
        "instagram_user_id": instagram_user_id,
        "graph_api_base": _resolve_graph_api_base(token_payload),
    }


def _post_instagram_message(payload: Dict[str, object]) -> Dict[str, str]:
    creds = _get_instagram_messaging_credentials()
    response = requests.post(
        f"{creds['graph_api_base']}/{creds['instagram_user_id']}/messages",
        params={"access_token": creds["access_token"]},
        json=payload,
        timeout=30,
    )
    if response.status_code >= 400:
        logger.error("Instagram message send failed: %s - %s", response.status_code, response.text)
        response.raise_for_status()
    return response.json()


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


def _ensure_public_media_url(media_url: str) -> str:
    local_path = _resolve_local_media_path(media_url)
    if not local_path:
        return media_url
    try:
        uploaded_url = upload_media_to_wordpress_ext(local_path)
        if uploaded_url:
            return uploaded_url
        logger.warning("WordPress upload failed, falling back to local media URL.")
    except Exception:
        logger.exception("Unable to upload Instagram media to WordPress")
    return media_url


def _ensure_public_image_url(image_url: str) -> str:
    return _ensure_public_media_url(image_url)


def _ensure_public_video_url(video_url: str) -> str:
    return _ensure_public_media_url(video_url)


def _create_media_container(
    *,
    image_url: str,
    caption: Optional[str],
    media_type: str = "IMAGE",
    share_link: Optional[str] = None,
) -> Dict[str, str]:
    creds = _get_instagram_credentials()
    graph_api_base = _resolve_graph_api_base()
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

    creation_url = f"{graph_api_base}/{creds['user_id']}/media"
    response = requests.post(creation_url, data=payload, timeout=30)
    if response.status_code >= 400:
        logger.error("Instagram media creation failed: %s - %s", response.status_code, response.text)
        response.raise_for_status()

    creation_id = response.json().get("id")
    if not creation_id:
        raise RuntimeError("Instagram media creation did not return an ID")

    if not _wait_for_media_ready(creation_id, creds["access_token"]):
        raise RuntimeError("Instagram media is not ready to publish yet.")

    publish_url = f"{graph_api_base}/{creds['user_id']}/media_publish"
    publish_payload = {"creation_id": creation_id, "access_token": creds["access_token"]}
    publish_response = requests.post(publish_url, data=publish_payload, timeout=30)
    if publish_response.status_code >= 400:
        logger.error("Instagram media publish failed: %s - %s", publish_response.status_code, publish_response.text)
        publish_response.raise_for_status()

    return publish_response.json()


def _create_video_container(
    *,
    video_url: str,
    caption: Optional[str],
    media_type: str = "REELS",
    share_to_feed: bool = True,
) -> Dict[str, str]:
    creds = _get_instagram_credentials()
    graph_api_base = _resolve_graph_api_base()
    public_video_url = _ensure_public_video_url(video_url)
    payload = {
        "access_token": creds["access_token"],
        "video_url": public_video_url,
        "media_type": media_type.upper(),
    }
    if caption:
        payload["caption"] = caption[:2200]
    if media_type.upper() == "REELS":
        payload["share_to_feed"] = "true" if share_to_feed else "false"

    creation_url = f"{graph_api_base}/{creds['user_id']}/media"
    response = requests.post(creation_url, data=payload, timeout=30)
    if response.status_code >= 400:
        logger.error("Instagram media creation failed: %s - %s", response.status_code, response.text)
        response.raise_for_status()

    creation_id = response.json().get("id")
    if not creation_id:
        raise RuntimeError("Instagram media creation did not return an ID")

    if not _wait_for_media_ready(creation_id, creds["access_token"]):
        raise RuntimeError("Instagram media is not ready to publish yet.")

    publish_url = f"{graph_api_base}/{creds['user_id']}/media_publish"
    publish_payload = {"creation_id": creation_id, "access_token": creds["access_token"]}
    publish_response = requests.post(publish_url, data=publish_payload, timeout=30)
    if publish_response.status_code >= 400:
        logger.error("Instagram media publish failed: %s - %s", publish_response.status_code, publish_response.text)
        publish_response.raise_for_status()

    return publish_response.json()


def _wait_for_media_ready(creation_id: str, access_token: str) -> bool:
    status_url = f"{_resolve_graph_api_base()}/{creation_id}"
    deadline = time.time() + PUBLISH_STATUS_TIMEOUT_SECONDS
    last_status = None
    while time.time() < deadline:
        response = requests.get(
            status_url,
            params={"fields": "status_code", "access_token": access_token},
            timeout=30,
        )
        if response.status_code >= 400:
            logger.warning("Instagram status check failed: %s - %s", response.status_code, response.text)
            return False
        status = response.json().get("status_code")
        last_status = status
        if status == "FINISHED":
            return True
        if status in {"ERROR", "EXPIRED"}:
            logger.error("Instagram media creation failed with status: %s", status)
            return False
        time.sleep(PUBLISH_STATUS_POLL_SECONDS)
    logger.warning("Instagram media not ready after polling (last status: %s)", last_status)
    return False


def _create_carousel_item_container(image_url: str) -> str:
    """Create a non-published carousel child item container. Returns its creation ID."""
    creds = _get_instagram_credentials()
    graph_api_base = _resolve_graph_api_base()
    public_image_url = _ensure_public_image_url(image_url)
    payload = {
        "access_token": creds["access_token"],
        "image_url": public_image_url,
        "is_carousel_item": "true",
    }
    creation_url = f"{graph_api_base}/{creds['user_id']}/media"
    response = requests.post(creation_url, data=payload, timeout=30)
    if response.status_code >= 400:
        logger.error("Instagram carousel item creation failed: %s - %s", response.status_code, response.text)
        response.raise_for_status()

    creation_id = response.json().get("id")
    if not creation_id:
        raise RuntimeError("Instagram carousel item creation did not return an ID")
    return creation_id


def publish_instagram_carousel(
    *,
    image_urls: List[str],
    caption: Optional[str] = None,
) -> Dict[str, str]:
    """Publish a multi-image Instagram FEED post (carousel) via the Graph API."""
    if len(image_urls) < 2:
        raise ValueError("Instagram carousels require at least 2 images")
    if len(image_urls) > 10:
        raise ValueError("Instagram carousels support at most 10 images")

    creds = _get_instagram_credentials()
    graph_api_base = _resolve_graph_api_base()

    child_ids = [_create_carousel_item_container(image_url) for image_url in image_urls]
    for child_id in child_ids:
        if not _wait_for_media_ready(child_id, creds["access_token"]):
            raise RuntimeError("Instagram carousel item is not ready to publish yet.")

    parent_payload = {
        "access_token": creds["access_token"],
        "media_type": "CAROUSEL",
        "children": ",".join(child_ids),
    }
    if caption:
        parent_payload["caption"] = caption[:2200]

    creation_url = f"{graph_api_base}/{creds['user_id']}/media"
    response = requests.post(creation_url, data=parent_payload, timeout=30)
    if response.status_code >= 400:
        logger.error("Instagram carousel creation failed: %s - %s", response.status_code, response.text)
        response.raise_for_status()

    creation_id = response.json().get("id")
    if not creation_id:
        raise RuntimeError("Instagram carousel creation did not return an ID")

    if not _wait_for_media_ready(creation_id, creds["access_token"]):
        raise RuntimeError("Instagram carousel is not ready to publish yet.")

    publish_url = f"{graph_api_base}/{creds['user_id']}/media_publish"
    publish_payload = {"creation_id": creation_id, "access_token": creds["access_token"]}
    publish_response = requests.post(publish_url, data=publish_payload, timeout=30)
    if publish_response.status_code >= 400:
        logger.error("Instagram carousel publish failed: %s - %s", publish_response.status_code, publish_response.text)
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
    # Story publishing is strict: caption support is limited and share link can
    # be rejected for some accounts/app configurations. Retry once without link.
    try:
        return _create_media_container(
            image_url=image_url,
            caption=None,
            share_link=share_link,
            media_type="STORIES",
        )
    except Exception:
        logger.warning("Instagram story publish with share link failed; retrying without share link.")
        return _create_media_container(
            image_url=image_url,
            caption=None,
            share_link=None,
            media_type="STORIES",
        )


def publish_instagram_reel(
    *,
    video_url: str,
    caption: Optional[str] = None,
    share_to_feed: bool = True,
) -> Dict[str, str]:
    """Publish an Instagram Reel via the Graph API."""

    return _create_video_container(
        video_url=video_url,
        caption=caption,
        media_type="REELS",
        share_to_feed=share_to_feed,
    )


def send_instagram_message(*, recipient_id: str, text: str) -> Dict[str, str]:
    """Send a text reply to an Instagram Messaging conversation."""
    return _post_instagram_message(
        {
            "recipient": {"id": recipient_id},
            "message": {"text": text[:1000]},
        }
    )


def send_instagram_private_reply(*, comment_id: str, text: str) -> Dict[str, str]:
    """Send the one-time private reply supported for IG comments/live comments."""
    return _post_instagram_message(
        {
            "recipient": {"comment_id": comment_id},
            "message": {"text": text[:1000]},
        }
    )


def send_instagram_comment_reply(*, comment_id: str, text: str) -> Dict[str, str]:
    """Post a public reply under an Instagram comment."""

    creds = _get_instagram_messaging_credentials()
    response = requests.post(
        f"{creds['graph_api_base']}/{comment_id}/replies",
        headers={"Authorization": f"Bearer {creds['access_token']}"},
        json={"message": text[:1000]},
        timeout=30,
    )
    if response.status_code >= 400:
        logger.error("Instagram comment reply failed: %s - %s", response.status_code, response.text)
        response.raise_for_status()
    return response.json()


def list_story_media_candidates(limit: int = 25) -> List[Dict[str, str]]:
    """Fetch recent FEED posts and return story media candidates."""
    creds = _get_instagram_credentials()
    params = {
        "fields": (
            "id,media_type,media_product_type,media_url,thumbnail_url,permalink,caption"
        ),
        "limit": str(max(1, min(limit, 50))),
        "access_token": creds["access_token"],
    }
    response = requests.get(
        f"{_resolve_graph_api_base()}/{creds['user_id']}/media",
        params=params,
        timeout=30,
    )
    if response.status_code >= 400:
        logger.error("Instagram media list failed: %s - %s", response.status_code, response.text)
        response.raise_for_status()

    data = response.json().get("data", [])
    candidates: List[Dict[str, str]] = []
    for item in data:
        if (item.get("media_product_type") or "").upper() != "FEED":
            continue
        media_type = item.get("media_type")
        permalink = item.get("permalink") or ""
        caption = item.get("caption") or ""
        if media_type == "IMAGE" and item.get("media_url"):
            candidates.append(
                {
                    "media_id": item.get("id") or "",
                    "image_url": item["media_url"],
                    "permalink": permalink,
                    "caption": caption,
                }
            )
            continue
        if media_type == "VIDEO" and item.get("thumbnail_url"):
            candidates.append(
                {
                    "media_id": item.get("id") or "",
                    "image_url": item["thumbnail_url"],
                    "video_url": item.get("media_url") or "",
                    "permalink": permalink,
                    "caption": caption,
                }
            )
    return candidates


def publish_random_profile_story(
    *,
    share_link: Optional[str] = None,
    caption: Optional[str] = None,
) -> Dict[str, str]:
    """Pick a random profile media candidate and publish it as a story."""
    candidates = list_story_media_candidates()
    if not candidates:
        raise RuntimeError("No recent Instagram media candidates found for story sharing.")
    selected = random.choice(candidates)
    return publish_instagram_story(
        image_url=selected["image_url"],
        caption=caption or selected.get("caption"),
        share_link=share_link or selected.get("permalink"),
    )


def _get_latest_media_id(access_token: str, user_id: str) -> Optional[str]:
    response = requests.get(
        f"{_resolve_graph_api_base()}/{user_id}/media",
        params={"fields": "id,media_type,timestamp", "limit": 1, "access_token": access_token},
        timeout=30,
    )
    if response.status_code >= 400:
        logger.warning("Instagram media list failed: %s - %s", response.status_code, response.text)
        return None
    data = response.json().get("data", [])
    if not data:
        return None
    return data[0].get("id")


def _extract_metric_value(metric_item: Dict[str, object]) -> Optional[float]:
    values = metric_item.get("values")
    if not isinstance(values, list) or not values:
        return None
    raw_value = values[0].get("value") if isinstance(values[0], dict) else None
    if isinstance(raw_value, (int, float)):
        return float(raw_value)
    return None


def _fetch_media_insights(access_token: str, media_id: str, metrics: List[str]) -> Dict[str, Optional[float]]:
    results: Dict[str, Optional[float]] = {metric: None for metric in metrics}
    graph_api_base = _resolve_graph_api_base()
    bulk_response = requests.get(
        f"{graph_api_base}/{media_id}/insights",
        params={"metric": ",".join(metrics), "access_token": access_token},
        timeout=30,
    )
    if bulk_response.status_code < 400:
        payload = bulk_response.json().get("data", [])
        for metric_item in payload:
            metric_name = metric_item.get("name")
            if metric_name in results:
                results[metric_name] = _extract_metric_value(metric_item)
        return results

    logger.warning(
        "Instagram bulk insights failed for %s: %s - %s; retrying metric-by-metric.",
        media_id,
        bulk_response.status_code,
        bulk_response.text,
    )

    for metric in metrics:
        response = requests.get(
            f"{graph_api_base}/{media_id}/insights",
            params={"metric": metric, "access_token": access_token},
            timeout=30,
        )
        if response.status_code >= 400:
            logger.warning(
                "Instagram insights metric '%s' failed for %s: %s - %s",
                metric,
                media_id,
                response.status_code,
                response.text,
            )
            continue
        data = response.json().get("data", [])
        if not data:
            continue
        value = _extract_metric_value(data[0])
        results[metric] = value
    return results


def _build_instagram_segment(rows: List[Dict[str, object]], label: str) -> Dict[str, object]:
    if not rows:
        return {}
    limited = rows[:5]
    latest = limited[0]
    trend_views = [row.get("views") or 0 for row in reversed(limited)]
    trend_engagement = [row.get("engagement") or 0 for row in reversed(limited)]
    return {
        "label": label,
        "period": f"Last {len(limited)} {label}",
        "metrics": {
            "Views": int(latest.get("views") or 0),
            "Reach": int(latest.get("reach") or 0) if latest.get("reach") is not None else None,
            "Engagement": int(latest.get("engagement") or 0),
            "Saves": int(latest.get("saved") or 0),
            "Shares": int(latest.get("shares") or 0),
            "Likes": int(latest.get("likes") or 0),
            "Comments": int(latest.get("comments") or 0),
        },
        "trend": trend_views,
        "series": {
            "views": trend_views,
            "engagement": trend_engagement,
        },
        "posts": limited,
    }


def get_latest_instagram_media_id() -> Optional[str]:
    """Return latest media id for the authenticated Instagram account."""
    creds = _get_instagram_credentials()
    access_token = creds.get("access_token")
    user_id = creds.get("user_id")
    if not access_token or not user_id:
        return None
    return _get_latest_media_id(access_token, user_id)


def fetch_instagram_analytics() -> Dict[str, object]:
    cache_payload = _INSTAGRAM_ANALYTICS_CACHE.get("payload")
    cache_expires_at = float(_INSTAGRAM_ANALYTICS_CACHE.get("expires_at") or 0)
    if cache_payload and time.time() < cache_expires_at:
        return cache_payload  # type: ignore[return-value]

    creds = _get_instagram_credentials()
    access_token = creds.get("access_token")
    user_id = creds.get("user_id")
    if not access_token or not user_id:
        return {"error": "Missing Instagram access token or user id."}

    metrics = ["views", "reach", "saved", "shares", "likes", "comments"]
    try:
        media_response = requests.get(
            f"{_resolve_graph_api_base()}/{user_id}/media",
            params={
                "fields": "id,caption,timestamp,media_type,media_product_type,permalink",
                "limit": str(max(5, INSTAGRAM_ANALYTICS_MAX_MEDIA)),
                "access_token": access_token,
            },
            timeout=30,
        )
        if media_response.status_code >= 400:
            return {
                "error": f"Instagram media list error: {media_response.status_code} - {media_response.text}"
            }
        media_items = media_response.json().get("data", [])
        if not media_items:
            return {"error": "No Instagram posts found for analytics."}
    except Exception as exc:
        return {"error": f"Instagram media fetch failed: {exc}"}

    feed_rows: List[Dict[str, object]] = []
    reels_rows: List[Dict[str, object]] = []
    candidates: List[Dict[str, object]] = []
    feed_candidate_count = 0
    reels_candidate_count = 0
    max_per_type = max(1, INSTAGRAM_ANALYTICS_MAX_PER_TYPE)

    for media in media_items:
        media_id = str(media.get("id") or "")
        if not media_id:
            continue
        media_product_type = str(media.get("media_product_type") or "").upper()
        if media_product_type not in {"FEED", "REELS"}:
            continue
        if media_product_type == "FEED" and feed_candidate_count >= max_per_type:
            continue
        if media_product_type == "REELS" and reels_candidate_count >= max_per_type:
            continue
        candidates.append(media)
        if media_product_type == "FEED":
            feed_candidate_count += 1
        else:
            reels_candidate_count += 1
        if feed_candidate_count >= max_per_type and reels_candidate_count >= max_per_type:
            break

    workers = max(1, INSTAGRAM_ANALYTICS_WORKERS)
    futures = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for media in candidates:
            media_id = str(media.get("id") or "")
            futures[executor.submit(_fetch_media_insights, access_token, media_id, metrics)] = media

        for future in as_completed(futures):
            media = futures[future]
            media_id = str(media.get("id") or "")
            media_product_type = str(media.get("media_product_type") or "").upper()
            try:
                metrics_map = future.result()
            except Exception:
                logger.exception("Instagram insights request failed for media %s", media_id)
                continue

            likes = metrics_map.get("likes") or 0
            comments = metrics_map.get("comments") or 0
            saves = metrics_map.get("saved") or 0
            shares = metrics_map.get("shares") or 0
            engagement_value = float(likes) + float(comments) + float(saves) + float(shares)
            views_value = metrics_map.get("views")
            if views_value is None:
                views_value = 0.0

            row = {
                "media_id": media_id,
                "timestamp": media.get("timestamp"),
                "media_product_type": media_product_type,
                "url": media.get("permalink") or f"https://www.instagram.com/p/{media_id}/",
                "views": views_value,
                "engagement": engagement_value,
                "reach": metrics_map.get("reach"),
                "likes": likes,
                "comments": comments,
                "saved": saves,
                "shares": shares,
            }
            if media_product_type == "FEED":
                feed_rows.append(row)
            elif media_product_type == "REELS":
                reels_rows.append(row)

    feed_rows.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
    reels_rows.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)

    if not feed_rows and not reels_rows:
        return {"error": "Unable to load Instagram FEED/REELS insights for latest posts."}

    segments: Dict[str, object] = {}
    feed_segment = _build_instagram_segment(feed_rows, "feed posts")
    if feed_segment:
        segments["feed"] = feed_segment

    reels_segment = _build_instagram_segment(reels_rows, "reels")
    if reels_segment:
        segments["reels"] = reels_segment

    payload = {
        "period": "Last 5 per type",
        "segments": segments,
    }
    _INSTAGRAM_ANALYTICS_CACHE["payload"] = payload
    _INSTAGRAM_ANALYTICS_CACHE["expires_at"] = time.time() + max(10, INSTAGRAM_ANALYTICS_CACHE_TTL_SECONDS)
    return payload
