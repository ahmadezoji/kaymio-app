import base64
import datetime as dt
import json
import os
import sys
import threading
import time
from io import BytesIO
from pathlib import Path
import random
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlparse
from uuid import uuid4

import requests
from dotenv import load_dotenv
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_from_directory, url_for

from kaymio.integrations.amazon.amazon_api import build_affiliate_link, extract_asin, fetch_product_from_canopy
from kaymio.utils.gemeni_api_helper import edit_image as edit_image_with_gemini, generate_video_from_image as generate_video_with_gemini
from kaymio.utils.image_generation_config import build_image_generation_options, resolve_image_generation_choice
from kaymio.integrations.instagram.instagram_api_helper import (
    get_latest_instagram_media_id,
    publish_instagram_post,
    publish_random_profile_story,
    publish_instagram_reel,
    publish_instagram_story,
    send_instagram_comment_reply,
    send_instagram_message,
    send_instagram_private_reply,
)
from kaymio.utils.openai_helper import (
    edit_image as edit_image_with_openai,
    generate_video_from_image as generate_video_with_openai,
    generate_caption_for_instagram,
    generate_caption_for_tiktok,
    generate_hashtags_for_instagram,
    generate_hashtags_for_tiktok,
    generate_instagram_comment_acknowledgement,
    generate_tags_for_product_for_pintrest,
    generate_text,
    generate_youtube_metadata,
)
from kaymio.utils.video_generation_config import (
    build_video_generation_options,
    normalize_openai_video_seconds,
    resolve_video_generation_choice,
)
from kaymio.integrations.pintrest.pinterest_helper import create_pinterest_pin, create_pinterest_video_pin
from kaymio.integrations.tiktok.tiktok_api_helper import publish_tiktok_post
from kaymio.integrations.youtube.youtube_api_helper import publish_short_video
from kaymio.wordpress.wordpress_api_helper import (
    create_woocommerce_product,
    find_wordpress_nearest_category,
    list_woocommerce_products,
    update_affiliate_links,
)
from kaymio.database import (
    get_product_entry as db_get_product_entry,
    init_db,
    load_app_state as db_load_app_state,
    save_app_state as db_save_app_state,
    save_product_entry as db_save_product_entry,
)
from kaymio.database.instagram_state import (
    load_instagram_scheduler_state,
    save_instagram_scheduler_state,
    load_instagram_media_reply_routes,
    save_instagram_media_reply_routes,
    upsert_instagram_media_reply_route,
    load_instagram_comment_reply_states,
    save_instagram_comment_reply_states,
    upsert_instagram_comment_reply_state,
)
from PIL import Image, ImageOps
from kaymio.routes.analytics_view import analytics_bp
from kaymio.routes.collections_view import collections_bp, collections_page_bp
from kaymio.routes.file_manager_view import file_manager_bp
from kaymio.routes.kaymio_wp_admin_view import kaymio_wp_admin_bp
from kaymio.routes.settings_view import settings_bp
from kaymio.widgets.story_qr_widget import (
    StoryCtaWidgetConfig,
    StoryQrWidgetConfig,
    compose_story_image_with_cta,
    compose_story_image_with_cta_and_affiliate_qr,
)

load_dotenv()

app = Flask(__name__)
app.register_blueprint(analytics_bp)
app.register_blueprint(collections_bp)
app.register_blueprint(collections_page_bp)
app.register_blueprint(file_manager_bp)
app.register_blueprint(kaymio_wp_admin_bp)
app.register_blueprint(settings_bp)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
ALLOWED_VIDEO_EXTENSIONS = {"mp4", "mov", "m4v", "webm"}
STORAGE_ROOT = Path(app.root_path) / "template_images"
ORIGINALS_DIR = STORAGE_ROOT / "originals"
GENERATED_DIR = STORAGE_ROOT / "generated"
VIDEOS_DIR = STORAGE_ROOT / "videos"
for directory in (ORIGINALS_DIR, GENERATED_DIR, VIDEOS_DIR):
    directory.mkdir(parents=True, exist_ok=True)
STATE_DIR = Path(app.root_path) / "data"
STATE_DIR.mkdir(parents=True, exist_ok=True)
MARKET_OPTIONS = [
    "Shein",
    "Amazon",
    "AliExpress",
    "Temu",
    "Etsy",
    "eBay",
    "Walmart",
]
RESETTABLE_PLATFORMS = {"pinterest", "instagram", "youtube", "tiktok"}
PREVIEW_BINARY_FIELDS = {"image_data", "instagram_image_data"}
TRUTHY_VALUES = {"1", "true", "yes", "on"}
IMAGE_GENERATION_OPTIONS = build_image_generation_options()
VIDEO_GENERATION_OPTIONS = build_video_generation_options()
VIDEO_DURATION_DEFAULT = 8
VIDEO_DURATION_MIN = 4
VIDEO_DURATION_MAX = 60
PUBLIC_APP_BASE_URL = os.getenv("PUBLIC_APP_BASE_URL", "").rstrip("/")
STORY_AUTOPUBLISH_TIME = os.getenv("INSTAGRAM_STORY_AUTOPUBLISH_TIME", "11:45")
STORY_AUTOPUBLISH_COUNT = int(os.getenv("INSTAGRAM_STORY_AUTOPUBLISH_COUNT", "2"))
STORY_AUTOPUBLISH_ENABLED = os.getenv("INSTAGRAM_STORY_AUTOPUBLISH_ENABLED", "1") in TRUTHY_VALUES
STORY_AUTOPUBLISH_STATE_FILE = STATE_DIR / "instagram_story_scheduler.json"
INSTAGRAM_MEDIA_REPLY_ROUTE_FILE = STATE_DIR / "instagram_story_routes.json"
INSTAGRAM_COMMENT_REPLY_STATE_FILE = STATE_DIR / "instagram_comment_reply_state.json"
INSTAGRAM_WEBHOOK_VERIFY_TOKEN = os.getenv("INSTAGRAM_WEBHOOK_VERIFY_TOKEN", "")
INSTAGRAM_PUBLIC_COMMENT_REPLY_ENABLED = (
    os.getenv("INSTAGRAM_PUBLIC_COMMENT_REPLY_ENABLED", "1") in TRUTHY_VALUES
)
INSTAGRAM_COMMENT_REPLY_TRIGGER_TOKENS = [
    token.strip()
    for token in os.getenv("INSTAGRAM_COMMENT_REPLY_TRIGGER_VALUES", "buy").split(",")
    if token.strip()
]
INSTAGRAM_COMMENT_REPLY_TRIGGER_VALUES = {
    token.casefold()
    for token in INSTAGRAM_COMMENT_REPLY_TRIGGER_TOKENS
}
INSTAGRAM_COMMENT_REPLY_DISPLAY_TRIGGER = (
    INSTAGRAM_COMMENT_REPLY_TRIGGER_TOKENS[0]
    if INSTAGRAM_COMMENT_REPLY_TRIGGER_TOKENS
    else "buy"
)
INSTAGRAM_COMMENT_REPLY_ALLOWED_MEDIA_TYPES = {"FEED", "REELS"}
STORY_QR_WATERMARK = os.getenv("INSTAGRAM_STORY_QR_WATERMARK", "https://kaymio.com")
STORY_QR_SAFE_RIGHT_RATIO = float(os.getenv("INSTAGRAM_STORY_QR_SAFE_RIGHT_RATIO", "0.08"))
STORY_QR_SAFE_BOTTOM_RATIO = float(os.getenv("INSTAGRAM_STORY_QR_SAFE_BOTTOM_RATIO", "0.12"))
STORY_QR_MIN_SIZE_PX = int(os.getenv("INSTAGRAM_STORY_QR_MIN_SIZE_PX", "100"))
STORY_QR_SIZE_RATIO = float(os.getenv("INSTAGRAM_STORY_QR_SIZE_RATIO", "0.18"))
STORY_QR_WIDGET_CONFIG = StoryQrWidgetConfig(
    watermark_text=STORY_QR_WATERMARK,
    safe_right_ratio=STORY_QR_SAFE_RIGHT_RATIO,
    safe_bottom_ratio=STORY_QR_SAFE_BOTTOM_RATIO,
    min_qr_size_px=STORY_QR_MIN_SIZE_PX,
    qr_size_ratio=STORY_QR_SIZE_RATIO,
)
STORY_CTA_WIDGET_CONFIG = StoryCtaWidgetConfig(
    safe_bottom_ratio=STORY_QR_SAFE_BOTTOM_RATIO,
)


def _empty_app_state() -> Dict[str, Any]:
    return {"products": {}, "last_product_id": ""}


def _load_story_scheduler_state() -> Dict[str, Any]:
    return load_instagram_scheduler_state()


def _save_story_scheduler_state(payload: Dict[str, Any]) -> None:
    save_instagram_scheduler_state(payload)


def _load_instagram_media_reply_route_state() -> Dict[str, Any]:
    return load_instagram_media_reply_routes()


def _save_instagram_media_reply_route_state(payload: Dict[str, Any]) -> None:
    save_instagram_media_reply_routes(payload)


def _load_instagram_comment_reply_state() -> Dict[str, Any]:
    return load_instagram_comment_reply_states()


def _save_instagram_comment_reply_state(payload: Dict[str, Any]) -> None:
    save_instagram_comment_reply_states(payload)


def _update_instagram_comment_reply_state(comment_id: str, **fields: Any) -> Dict[str, Any]:
    normalized_comment_id = str(comment_id or "").strip()
    if not normalized_comment_id:
        return {}
    state = _load_instagram_comment_reply_state()
    entry = dict(state.get(normalized_comment_id) or {})
    entry.update(
        {
            key: value
            for key, value in fields.items()
            if value not in (None, "")
        }
    )
    if "created_at" not in entry:
        entry["created_at"] = dt.datetime.utcnow().isoformat()
    entry["updated_at"] = dt.datetime.utcnow().isoformat()
    return upsert_instagram_comment_reply_state(normalized_comment_id, entry)


def _register_instagram_media_reply_route(
    media_id: str,
    candidate: Dict[str, Any],
    *,
    reply_surface: str,
) -> None:
    normalized_media_id = str(media_id or "").strip()
    if not normalized_media_id:
        return
    affiliate_link = str(candidate.get("affiliate_link") or "").strip()
    if not affiliate_link:
        return
    route_data = {
        "product_id": str(candidate.get("product_id") or ""),
        "affiliate_link": affiliate_link,
        "product_url": str(candidate.get("product_url") or "").strip(),
        "title": str(candidate.get("title") or ""),
        "description": str(candidate.get("description") or ""),
        "reply_surface": reply_surface,
        "published_at": dt.datetime.utcnow().isoformat(),
    }
    upsert_instagram_media_reply_route(normalized_media_id, route_data)
    routes = _load_instagram_media_reply_route_state()
    app.logger.info(
        "Registered Instagram media reply route: media_id=%s surface=%s product_id=%s affiliate_link_present=%s total_routes=%s",
        normalized_media_id,
        reply_surface,
        str(candidate.get("product_id") or ""),
        bool(affiliate_link),
        len(routes),
    )


def _register_story_reply_route(media_id: str, candidate: Dict[str, Any]) -> None:
    _register_instagram_media_reply_route(media_id, candidate, reply_surface="story")


def _build_instagram_reply_candidate(
    *,
    product_id: str,
    form_values: Dict[str, Any],
    preview_payload: Optional[Dict[str, Any]],
) -> Dict[str, str]:
    preview = preview_payload or {}
    return {
        "product_id": product_id,
        "affiliate_link": str(form_values.get("affiliate_link") or ""),
        "product_url": get_website_product_url(product_id) or "",
        "title": str(
            form_values.get("title_input")
            or form_values.get("title")
            or preview.get("title")
            or ""
        ),
        "description": str(
            form_values.get("description_input")
            or form_values.get("description")
            or preview.get("description")
            or ""
        ),
    }


def _build_instagram_media_reply_entry_from_state(media_id: str) -> Dict[str, str]:
    if not media_id:
        return {}
    state = load_app_state()
    products = state.get("products", {}) or {}
    for product_id, entry in products.items():
        platforms = entry.get("platforms") or {}
        website_state = platforms.get("website") or {}
        instagram_group = platforms.get("instagram") or {}
        grouped_candidates = (
            ("instagram_feed", instagram_group.get("instagram_feed") or {}),
            ("instagram_story", instagram_group.get("instagram_story") or {}),
            ("instagram_reel", instagram_group.get("instagram_reel") or {}),
        )
        flat_candidates = tuple(
            (key, platforms.get(key) or {})
            for key in ("instagram_feed", "instagram_story", "instagram_reel")
        )
        for surface_key, platform_state in (*grouped_candidates, *flat_candidates):
            stored_media_id = str(
                platform_state.get("instagram_media_id") or platform_state.get("media_id") or ""
            )
            if not stored_media_id or stored_media_id != str(media_id):
                continue
            return {
                "product_id": str(product_id),
                "affiliate_link": str(
                    platform_state.get("affiliate_link")
                    or website_state.get("affiliate_link")
                    or ""
                ).strip(),
                "product_url": str(website_state.get("product_url") or "").strip(),
                "title": str(
                    platform_state.get("title")
                    or website_state.get("title")
                    or ""
                ),
                "description": str(
                    platform_state.get("description")
                    or website_state.get("description")
                    or ""
                ),
                "reply_surface": surface_key.replace("instagram_", ""),
            }
    return {}


def _reply_entry_for_instagram_media(media_id: str) -> Dict[str, str]:
    if not media_id:
        return {}
    route_state = _load_instagram_media_reply_route_state()
    route_entry = route_state.get(str(media_id))
    base_entry = dict(route_entry) if isinstance(route_entry, dict) else {}
    fallback_entry = _build_instagram_media_reply_entry_from_state(media_id)
    merged_entry: Dict[str, str] = {}
    for key in ("product_id", "affiliate_link", "product_url", "title", "description", "reply_surface"):
        value = str(base_entry.get(key) or fallback_entry.get(key) or "").strip()
        if value:
            merged_entry[key] = value
    return merged_entry


def _build_instagram_affiliate_reply(route_entry: Dict[str, Any]) -> str:
    title = str(route_entry.get("title") or "").strip()
    affiliate_link = str(route_entry.get("affiliate_link") or "").strip()
    product_url = str(route_entry.get("product_url") or "").strip()
    parts: List[str] = []
    if title:
        parts.append(title)
    if affiliate_link:
        parts.append(affiliate_link)
    if product_url:
        parts.append(f"You can also find it on our website:\n{product_url}")
    return "\n\n".join(part for part in parts if part)


def _build_instagram_comment_public_acknowledgement(route_entry: Dict[str, Any]) -> str:
    return generate_instagram_comment_acknowledgement(
        str(route_entry.get("title") or "").strip(),
        trigger_text=INSTAGRAM_COMMENT_REPLY_DISPLAY_TRIGGER,
    )


def _random_affiliate_link_from_state() -> Optional[str]:
    state = load_app_state()
    products = state.get("products", {})
    links: List[str] = []
    for entry in products.values():
        platforms = entry.get("platforms") or {}
        for platform_name in ("pinterest", "website", "instagram_reel", "youtube", "tiktok"):
            p_state = platforms.get(platform_name) or {}
            candidate = p_state.get("affiliate_link")
            if candidate:
                links.append(candidate)
    return random.choice(links) if links else None


def _affiliate_link_for_instagram_media(media_id: str) -> Optional[str]:
    if not media_id:
        return None
    route_entry = _reply_entry_for_instagram_media(media_id)
    affiliate_link = str(route_entry.get("affiliate_link") or "").strip()
    return affiliate_link or None


def _story_content_for_instagram_media(media_id: str) -> Dict[str, str]:
    if not media_id:
        return {}
    route_entry = _reply_entry_for_instagram_media(media_id)
    if not route_entry:
        return {}
    return {
        "title": str(route_entry.get("title") or ""),
        "description": str(route_entry.get("description") or ""),
    }


def _resolve_story_source_image(entry: Dict[str, Any], feed_state: Dict[str, Any]) -> Dict[str, str]:
    assets = entry.get("assets") or {}
    relative_candidates = [
        str(feed_state.get("instagram_image_path") or ""),
        str(feed_state.get("generated_image_path") or ""),
        str(assets.get("instagram_image_path") or ""),
        str(assets.get("generated_image_path") or ""),
        str(assets.get("original_image_path") or ""),
    ]
    for relative_path in relative_candidates:
        storage_path = resolve_storage_path(relative_path)
        if storage_path:
            return {
                "compose_source": storage_path,
                "publish_source": f"/media/{relative_path}",
            }
    external_candidates = list(assets.get("source_image_urls") or []) + list(assets.get("amazon_image_urls") or [])
    for image_url in external_candidates:
        if image_url:
            return {
                "compose_source": str(image_url),
                "publish_source": str(image_url),
            }
    return {}


def _scheduled_story_candidates_from_state() -> List[Dict[str, str]]:
    state = load_app_state()
    products = state.get("products", {}) or {}
    candidates: List[Dict[str, str]] = []
    for product_id, entry in products.items():
        platforms = entry.get("platforms") or {}
        instagram = platforms.get("instagram") or {}
        website_state = platforms.get("website") or {}
        feed_state = instagram.get("instagram_feed") or {}
        if not feed_state:
            continue
        image_sources = _resolve_story_source_image(entry, feed_state)
        compose_source = image_sources.get("compose_source") or ""
        publish_source = image_sources.get("publish_source") or ""
        if not compose_source or not publish_source:
            continue
        affiliate_link = str(
            feed_state.get("affiliate_link")
            or (platforms.get("website") or {}).get("affiliate_link")
            or ""
        )
        candidates.append(
            {
                "product_id": str(product_id),
                "title": str(feed_state.get("title") or ""),
                "description": str(feed_state.get("description") or ""),
                "caption": str(feed_state.get("caption") or ""),
                "affiliate_link": affiliate_link,
                "product_url": str(website_state.get("product_url") or ""),
                "compose_source": compose_source,
                "publish_source": publish_source,
            }
        )
    return candidates


def _scheduled_story_candidates_from_website(limit: int = 100) -> List[Dict[str, str]]:
    result = list_woocommerce_products(page=1, per_page=max(1, min(limit, 100)))
    if result.get("error"):
        app.logger.warning("Unable to load WooCommerce products for scheduled stories: %s", result["error"])
        return []

    candidates: List[Dict[str, str]] = []
    for product in result.get("items", []):
        if not isinstance(product, dict):
            continue
        images = product.get("images") or []
        image_url = ""
        if images and isinstance(images[0], dict):
            image_url = str(images[0].get("src") or "")
        if not image_url:
            continue

        affiliate_link = str(product.get("external_url") or product.get("permalink") or "")
        candidates.append(
            {
                "product_id": f"wc:{product.get('id')}",
                "title": str(product.get("name") or ""),
                "description": str(
                    product.get("short_description")
                    or product.get("description")
                    or ""
                ),
                "caption": "",
                "affiliate_link": affiliate_link,
                "product_url": str(product.get("permalink") or ""),
                "compose_source": image_url,
                "publish_source": image_url,
            }
        )
    return candidates


def _scheduled_story_candidates() -> List[Dict[str, str]]:
    candidates = _scheduled_story_candidates_from_state()
    candidates.extend(_scheduled_story_candidates_from_website())
    return candidates


def _build_public_media_url(relative_path: str) -> str:
    """Build an externally-reachable /media/ URL for the scheduler thread.

    The scheduler runs outside any Flask request, so url_for(_external=True)
    has no request/host to build from and raises RuntimeError. PUBLIC_APP_BASE_URL
    must be set to this app's publicly reachable origin for scheduled stories to
    include the composed image (CTA/QR) instead of falling back to the raw product photo.
    """
    if not PUBLIC_APP_BASE_URL:
        raise RuntimeError(
            "PUBLIC_APP_BASE_URL is not configured; cannot build a public media URL "
            "from the background scheduler."
        )
    return f"{PUBLIC_APP_BASE_URL}/media/{relative_path}"


def _publish_scheduled_instagram_stories() -> None:
    posted = 0
    print("DEBUG: Getting WooCommerce candidates for scheduled stories", file=sys.stderr, flush=True)
    candidates = _scheduled_story_candidates_from_website()
    print(f"DEBUG: Found {len(candidates)} WooCommerce candidates", file=sys.stderr, flush=True)
    if not candidates:
        print("DEBUG: No WooCommerce candidates found for scheduled stories.", file=sys.stderr, flush=True)
        app.logger.warning("No WooCommerce candidates found for scheduled stories.")
        return
    random.shuffle(candidates)
    used_products: set[str] = set()
    while posted < STORY_AUTOPUBLISH_COUNT:
        try:
            candidate = None
            for item in candidates:
                product_id = str(item.get("product_id") or "")
                if product_id not in used_products:
                    candidate = item
                    used_products.add(product_id)
                    break
            if not candidate:
                break
            caption = str(candidate.get("caption") or "")
            title = str(candidate.get("title") or "")
            description = str(candidate.get("description") or "")
            affiliate_link = str(candidate.get("affiliate_link") or "")
            compose_source = str(candidate.get("compose_source") or "")
            image_url = str(candidate.get("publish_source") or "")
            story_copy = caption or description or title
            if affiliate_link and compose_source:
                try:
                    story_image = compose_story_image_with_cta_and_affiliate_qr(
                        source_image_url=compose_source,
                        affiliate_link=affiliate_link,
                        product_title=title,
                        caption=caption,
                        description=description or story_copy,
                        qr_config=STORY_QR_WIDGET_CONFIG,
                        cta_config=STORY_CTA_WIDGET_CONFIG,
                    )
                    relative_path = save_generated_image(story_image)
                    image_url = _build_public_media_url(relative_path)
                except Exception:
                    app.logger.exception("Unable to compose scheduled story image with CTA + affiliate QR.")
            elif compose_source:
                try:
                    story_image = compose_story_image_with_cta(
                        source_image_url=compose_source,
                        product_title=title,
                        caption=caption,
                        description=description or story_copy,
                        config=STORY_CTA_WIDGET_CONFIG,
                    )
                    relative_path = save_generated_image(story_image)
                    image_url = _build_public_media_url(relative_path)
                except Exception:
                    app.logger.exception("Unable to compose scheduled story image with CTA.")
            
            print(f"DEBUG: Publishing story with image_url={image_url}, affiliate_link={affiliate_link}", file=sys.stderr, flush=True)
            response = publish_instagram_story(
                image_url=image_url,
                caption=None,
                share_link=affiliate_link,
            )
            print(f"DEBUG: Instagram story publish response: {response}", file=sys.stderr, flush=True)
            media_id = response.get("id") or get_latest_instagram_media_id()
            print(f"DEBUG: Story media_id: {media_id}", file=sys.stderr, flush=True)
            if media_id:
                _register_story_reply_route(str(media_id), candidate)
                print(f"DEBUG: Registered story reply route for media_id={media_id}", file=sys.stderr, flush=True)

            # Clean up temporary story image from server (stories are temporary, no need to persist)
            try:
                # Extract relative path from full URL (e.g., "generated/filename.png" from "http://server:8081/media/generated/filename.png")
                if "/media/" in image_url:
                    relative_path = image_url.split("/media/", 1)[1]
                    full_path = STORAGE_ROOT / relative_path
                    if full_path.exists():
                        full_path.unlink()
                        print(f"DEBUG: Deleted temporary story image: {relative_path}", file=sys.stderr, flush=True)
                        app.logger.info("Deleted temporary story image: %s", relative_path)
            except Exception as e:
                print(f"DEBUG: Failed to delete story image: {e}", file=sys.stderr, flush=True)
                app.logger.warning("Failed to delete story image: %s", e)

            print(f"Scheduled story publish response: {response}")
            app.logger.info("Scheduled Instagram story published: %s", response)
            posted += 1
            time.sleep(8)
        except Exception as exc:
            print(f"DEBUG: Story publish exception: {exc}", file=sys.stderr, flush=True)
            import traceback
            traceback.print_exc(file=sys.stderr)
            app.logger.exception("Scheduled Instagram story publish failed: %s", exc)
            break


def _find_nested_value(payload: Any, target_keys: set[str]) -> Optional[str]:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in target_keys and value not in (None, ""):
                return str(value)
            nested = _find_nested_value(value, target_keys)
            if nested:
                return nested
    elif isinstance(payload, list):
        for item in payload:
            nested = _find_nested_value(item, target_keys)
            if nested:
                return nested
    return None


def _extract_story_media_id(event: Dict[str, Any]) -> Optional[str]:
    candidate_paths = [
        (("message", "reply_to", "story", "id"),),
        (("reply_to", "story", "id"),),
        (("message", "story", "id"),),
        (("story", "id"),),
        (("message", "media", "id"),),
        (("media", "id"),),
        (("mentioned_media", "id"),),
    ]
    for path_group in candidate_paths:
        for path in path_group:
            current: Any = event
            found = True
            for key in path:
                if not isinstance(current, dict) or key not in current:
                    found = False
                    break
                current = current[key]
            if found and current not in (None, ""):
                return str(current)
    return _find_nested_value(event, {"media_id", "story_id"})


def _is_instagram_comment_reply_trigger(comment_text: str) -> bool:
    normalized_text = str(comment_text or "").strip().casefold()
    if not normalized_text:
        return False
    return normalized_text in INSTAGRAM_COMMENT_REPLY_TRIGGER_VALUES


def _build_instagram_comment_reply_caption_cta() -> str:
    trigger = str(INSTAGRAM_COMMENT_REPLY_DISPLAY_TRIGGER or "buy").strip().upper()
    return f'COMMENT "{trigger}" FOR PRODUCT INFO + LINK'


def _prepend_instagram_comment_reply_caption_cta(caption: str) -> str:
    base_caption = str(caption or "").strip()
    cta = _build_instagram_comment_reply_caption_cta()
    if base_caption.casefold().startswith(cta.casefold()):
        return base_caption
    return f"{cta}\n\n{base_caption}" if base_caption else cta


def _handle_instagram_messaging_event(event: Dict[str, Any]) -> None:
    sender = event.get("sender") or {}
    recipient_id = str(sender.get("id") or "")
    app.logger.warning(
        "Instagram messaging event received: sender_id=%s event_keys=%s has_message=%s",
        recipient_id or "missing",
        sorted(event.keys()),
        isinstance(event.get("message"), dict),
    )
    if not recipient_id:
        app.logger.warning("Instagram messaging event skipped: sender.id missing. event=%s", event)
        return
    media_id = _extract_story_media_id(event)
    if not media_id:
        app.logger.warning("Instagram messaging event skipped: no story media id present. event=%s", event)
        return
    app.logger.warning("Instagram messaging event extracted media_id=%s", media_id)
    route_entry = _load_instagram_media_reply_route_state().get(media_id)
    if not isinstance(route_entry, dict):
        app.logger.warning(
            "Instagram messaging event skipped: no route for media_id=%s. known_route_ids=%s",
            media_id,
            sorted(_load_instagram_media_reply_route_state().keys()),
        )
        return
    reply_text = _build_instagram_affiliate_reply(route_entry)
    if not reply_text:
        app.logger.warning("Instagram messaging event skipped: empty reply text for media_id=%s", media_id)
        return
    app.logger.warning(
        "Instagram messaging event matched route: media_id=%s product_id=%s affiliate_link_present=%s",
        media_id,
        route_entry.get("product_id", ""),
        bool(route_entry.get("affiliate_link")),
    )
    send_instagram_message(recipient_id=recipient_id, text=reply_text)
    app.logger.info("Instagram DM auto-reply sent for media id %s.", media_id)


def _handle_instagram_comment_change(change: Dict[str, Any]) -> None:
    value = change.get("value") or {}
    comment_id = str(value.get("id") or "")
    comment_text = str(value.get("text") or "").strip()
    media_id = str(
        (value.get("media") or {}).get("id")
        or value.get("media_id")
        or ""
    )
    media_product_type = str(
        (value.get("media") or {}).get("media_product_type")
        or value.get("media_product_type")
        or ""
    ).upper()
    if not comment_id or not media_id:
        app.logger.warning(
            "Instagram comment webhook skipped: missing comment_id/media_id. value=%s",
            value,
        )
        return
    if media_product_type and media_product_type not in INSTAGRAM_COMMENT_REPLY_ALLOWED_MEDIA_TYPES:
        app.logger.warning(
            "Instagram comment webhook skipped: unsupported media_product_type=%s media_id=%s comment_id=%s",
            media_product_type,
            media_id,
            comment_id,
        )
        return
    if not _is_instagram_comment_reply_trigger(comment_text):
        app.logger.info(
            "Instagram comment webhook skipped: comment text did not match trigger. media_id=%s comment_id=%s text=%r",
            media_id,
            comment_id,
            comment_text,
        )
        return
    route_entry = _reply_entry_for_instagram_media(media_id)
    if not route_entry:
        app.logger.warning(
            "Instagram comment webhook skipped: no registered reply route for media_id=%s comment_id=%s",
            media_id,
            comment_id,
        )
        return
    reply_text = _build_instagram_affiliate_reply(route_entry)
    if not reply_text:
        app.logger.warning(
            "Instagram comment webhook skipped: empty reply text for media_id=%s comment_id=%s",
            media_id,
            comment_id,
        )
        return
    reply_status = _load_instagram_comment_reply_state().get(comment_id) or {}
    private_reply_sent = bool(
        reply_status.get("private_reply_message_id") or reply_status.get("private_reply_sent_at")
    )
    public_reply_sent = bool(reply_status.get("public_reply_id") or reply_status.get("public_reply_sent_at"))
    if private_reply_sent and (public_reply_sent or not INSTAGRAM_PUBLIC_COMMENT_REPLY_ENABLED):
        app.logger.info(
            "Instagram comment webhook skipped: reply already processed. media_id=%s comment_id=%s",
            media_id,
            comment_id,
        )
        return

    if not private_reply_sent:
        try:
            private_result = send_instagram_private_reply(comment_id=comment_id, text=reply_text)
        except Exception as exc:
            _update_instagram_comment_reply_state(
                comment_id,
                media_id=media_id,
                comment_text=comment_text,
                reply_surface=str(route_entry.get("reply_surface") or ""),
                last_error=f"private_reply_failed: {exc}",
            )
            raise
        _update_instagram_comment_reply_state(
            comment_id,
            media_id=media_id,
            comment_text=comment_text,
            reply_surface=str(route_entry.get("reply_surface") or ""),
            private_reply_message_id=str(private_result.get("message_id") or ""),
            private_reply_recipient_id=str(private_result.get("recipient_id") or ""),
            private_reply_sent_at=dt.datetime.utcnow().isoformat(),
        )
        app.logger.info(
            "Instagram private reply sent for comment %s on media %s (surface=%s text=%r).",
            comment_id,
            media_id,
            route_entry.get("reply_surface", ""),
            comment_text,
        )
        private_reply_sent = True

    if not INSTAGRAM_PUBLIC_COMMENT_REPLY_ENABLED or public_reply_sent:
        return

    public_reply_text = _build_instagram_comment_public_acknowledgement(route_entry)
    if not public_reply_text:
        app.logger.warning(
            "Instagram public comment reply skipped: empty acknowledgement text for media_id=%s comment_id=%s",
            media_id,
            comment_id,
        )
        return

    try:
        public_result = send_instagram_comment_reply(comment_id=comment_id, text=public_reply_text)
    except Exception as exc:
        _update_instagram_comment_reply_state(
            comment_id,
            media_id=media_id,
            comment_text=comment_text,
            reply_surface=str(route_entry.get("reply_surface") or ""),
            last_error=f"public_reply_failed: {exc}",
        )
        app.logger.exception(
            "Instagram public comment reply failed for comment %s on media %s.",
            comment_id,
            media_id,
        )
        return
    _update_instagram_comment_reply_state(
        comment_id,
        media_id=media_id,
        comment_text=comment_text,
        reply_surface=str(route_entry.get("reply_surface") or ""),
        public_reply_id=str(public_result.get("id") or ""),
        public_reply_text=public_reply_text,
        public_reply_sent_at=dt.datetime.utcnow().isoformat(),
    )
    app.logger.info(
        "Instagram public comment reply sent for comment %s on media %s (surface=%s).",
        comment_id,
        media_id,
        route_entry.get("reply_surface", ""),
    )


def _instagram_story_scheduler_loop() -> None:
    import sys
    print(f"🔔 Instagram story scheduler started for {STORY_AUTOPUBLISH_TIME}", file=sys.stderr, flush=True)
    app.logger.info("Instagram story scheduler started for %s", STORY_AUTOPUBLISH_TIME)
    while True:
        try:
            now = dt.datetime.now()
            target_hour, target_minute = [int(part) for part in STORY_AUTOPUBLISH_TIME.split(":", 1)]
            state = _load_story_scheduler_state()
            last_run_date = state.get("last_run_date")
            today = now.date().isoformat()
            should_run = (
                now.hour == target_hour
                and now.minute >= target_minute
                and last_run_date != today
            )
            # Log every minute for debugging
            if now.minute % 10 == 0:  # Log every 10 minutes to avoid spam
                print(f"DEBUG scheduler: now={now.time()}, target={target_hour}:{target_minute:02d}, last_run={last_run_date}, today={today}, should_run={should_run}", file=sys.stderr, flush=True)
            if should_run:
                print(f"DEBUG: TRIGGERING story publish at {now.time()}", file=sys.stderr, flush=True)
                _publish_scheduled_instagram_stories()
                _save_story_scheduler_state({"last_run_date": today})
            time.sleep(20)
        except Exception:
            print(f"DEBUG: Scheduler loop exception", file=sys.stderr, flush=True)
            import traceback
            traceback.print_exc(file=sys.stderr)
            app.logger.exception("Instagram scheduler loop error")
            time.sleep(30)


def start_instagram_story_scheduler() -> None:
    import sys
    enabled = STORY_AUTOPUBLISH_ENABLED
    print(f"DEBUG: STORY_AUTOPUBLISH_ENABLED={enabled}", file=sys.stderr, flush=True)
    if not enabled:
        print("DEBUG: Instagram story scheduler disabled.", file=sys.stderr, flush=True)
        app.logger.info("Instagram story scheduler disabled.")
        return
    print("DEBUG: Starting Instagram scheduler thread...", file=sys.stderr, flush=True)
    thread = threading.Thread(
        target=_instagram_story_scheduler_loop,
        name="instagram-story-scheduler",
        daemon=True,
    )
    thread.start()
    print("DEBUG: Instagram scheduler thread started (daemon).", file=sys.stderr, flush=True)


def _json_safe(data: Any) -> Any:
    if data is None:
        return None
    return json.loads(json.dumps(data))


def _sanitize_preview(preview: Dict[str, Any]) -> Dict[str, Any]:
    sanitized = {key: value for key, value in preview.items() if key not in PREVIEW_BINARY_FIELDS}
    return _json_safe(sanitized)


PINTEREST_PREVIEW_KEYS = {
    "title",
    "description",
    "tags",
    "tags_payload",
    "generated_image_path",
    "original_image_path",
    "affiliate_link",
    "market",
    "sku_or_url",
    "pinterest_extra",
    "title_input",
    "description_input",
    "generated_image_url",
    "image_public_url",
    "category",
    "price",
    "website_boost_prompt",
    "youtube_boost_prompt",
    "use_affiliate_link",
    "video_prompt",
    "generated_video_path",
    "video_url",
    "video_public_url",
    "video_duration_seconds",
}

INSTAGRAM_PREVIEW_KEYS = {
    "video_hook_style",
    "instagram_caption",
    "instagram_hashtags",
    "instagram_hashtags_payload",
    "instagram_image_path",
    "instagram_image_url",
    "instagram_image_public_url",
    "instagram_image_data",
    "instagram_boost_prompt",
}

TIKTOK_PREVIEW_KEYS = {
    "video_hook_style",
    "tiktok_caption",
    "tiktok_hashtags",
    "tiktok_hashtags_payload",
}

YOUTUBE_PREVIEW_KEYS = {
    "video_hook_style",
    "youtube_title",
    "youtube_description",
    "youtube_keywords",
    "youtube_keywords_payload",
    "generated_video_path",
    "video_url",
    "video_public_url",
    "video_download_url",
    "video_duration_seconds",
    "youtube_boost_prompt",
    "video_prompt",
}

WEBSITE_PREVIEW_KEYS = {"title", "description", "tags", "category", "price", "website_boost_prompt"}

FORM_COMMON_KEYS = {
    "market",
    "sku_or_url",
    "title",
    "description",
    "affiliate_link",
    "category",
    "price",
    "pinterest_extra",
    "website_boost_prompt",
    "youtube_boost_prompt",
    "instagram_boost_prompt",
    "use_affiliate_link",
    "selected_original_image",
    "selected_original_images",
    "original_image_path",
    "image_generation_model",
    "video_generation_model",
    "video_duration_seconds",
    "video_hook_style",
}


def _hydrate_preview(preview: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not preview:
        return preview
    hydrated = dict(preview)
    if "tags" in hydrated:
        hydrated["tags"] = sanitize_tag_list(hydrated.get("tags") or [])
        hydrated["tags_payload"] = json.dumps(hydrated["tags"])
    if "instagram_hashtags" in hydrated:
        hydrated["instagram_hashtags"] = sanitize_tag_list(hydrated.get("instagram_hashtags") or [])
        hydrated["instagram_hashtags_payload"] = json.dumps(hydrated["instagram_hashtags"])
    if "tiktok_hashtags" in hydrated:
        hydrated["tiktok_hashtags"] = sanitize_tag_list(hydrated.get("tiktok_hashtags") or [])
        hydrated["tiktok_hashtags_payload"] = json.dumps(hydrated["tiktok_hashtags"])
    if "youtube_keywords" in hydrated:
        hydrated["youtube_keywords"] = sanitize_tag_list(hydrated.get("youtube_keywords") or [])
        hydrated["youtube_keywords_payload"] = json.dumps(hydrated["youtube_keywords"])
    image_path = hydrated.get("generated_image_path")
    if image_path and not hydrated.get("image_data"):
        try:
            bytes_data = load_stored_media(image_path)
            hydrated["image_data"] = base64.b64encode(bytes_data).decode("utf-8")
            hydrated.setdefault("generated_image_url", url_for("serve_media", filename=image_path))
            hydrated.setdefault("generated_image_download_url", url_for("download_media", filename=image_path))
            hydrated.setdefault(
                "image_public_url", url_for("serve_media", filename=image_path, _external=True)
            )
        except Exception:
            hydrated["image_data"] = ""
    insta_path = hydrated.get("instagram_image_path")
    if insta_path and not hydrated.get("instagram_image_data"):
        try:
            insta_bytes = load_stored_media(insta_path)
            hydrated["instagram_image_data"] = base64.b64encode(insta_bytes).decode("utf-8")
            hydrated.setdefault("instagram_image_url", url_for("serve_media", filename=insta_path))
            hydrated.setdefault("instagram_image_download_url", url_for("download_media", filename=insta_path))
            hydrated.setdefault(
                "instagram_image_public_url", url_for("serve_media", filename=insta_path, _external=True)
            )
        except Exception:
            hydrated["instagram_image_data"] = ""
    video_path = hydrated.get("generated_video_path")
    if video_path:
        hydrated.setdefault("video_url", url_for("serve_media", filename=video_path))
        hydrated.setdefault("video_download_url", url_for("download_media", filename=video_path))
        hydrated.setdefault(
            "video_public_url", url_for("serve_media", filename=video_path, _external=True)
        )
    return hydrated


def load_app_state() -> Dict[str, Any]:
    data = db_load_app_state()
    if not isinstance(data, dict):
        return _empty_app_state()
    data.setdefault("products", {})
    data.setdefault("last_product_id", "")
    return data


def save_app_state(state: Dict[str, Any]) -> None:
    db_save_app_state(state)


def normalize_product_id(raw_id: str) -> str:
    return (raw_id or "").strip()


def get_product_state(product_id: str) -> Dict[str, Any]:
    return db_get_product_entry(product_id)


def get_last_product_state() -> Dict[str, Any]:
    state = load_app_state()
    last_id = state.get("last_product_id") or ""
    if not last_id:
        return {}
    return state.get("products", {}).get(last_id, {})


def get_platform_result(product_id: str, platform: str) -> Optional[Dict[str, Any]]:
    if not product_id:
        return None
    entry = get_product_state(product_id)
    return get_platform_state(entry, platform)


def get_website_result(product_id: str) -> Optional[Dict[str, Any]]:
    return get_platform_result(product_id, "website")


def get_website_product_url(product_id: str) -> Optional[str]:
    website_result = get_website_result(product_id)
    if not website_result:
        return None
    return website_result.get("product_url")


def get_platform_state(entry: Dict[str, Any], platform: str) -> Dict[str, Any]:
    platforms = entry.get("platforms") or {}
    if platform.startswith("instagram_"):
        instagram = platforms.get("instagram") or {}
        return instagram.get(platform) or {}
    return platforms.get(platform) or {}


def set_platform_state(entry: Dict[str, Any], platform: str, payload: Dict[str, Any]) -> None:
    platforms = entry.setdefault("platforms", {})
    if platform.startswith("instagram_"):
        instagram = platforms.get("instagram") or {}
        current = instagram.get(platform) or {}
        current.update(_json_safe(payload))
        instagram[platform] = current
        platforms["instagram"] = instagram
    else:
        current = platforms.get(platform) or {}
        current.update(_json_safe(payload))
        platforms[platform] = current
    entry["platforms"] = platforms


def _merge_platform_payloads(entry: Dict[str, Any], payloads: Dict[str, Dict[str, Any]]) -> None:
    for name, payload in payloads.items():
        if not payload:
            continue
        set_platform_state(entry, name, payload)


def _build_form_values_from_platforms(entry: Dict[str, Any]) -> Dict[str, str]:
    platforms = entry.get("platforms") or {}
    pinterest = platforms.get("pinterest") or {}
    website = platforms.get("website") or {}
    source = pinterest or website
    form_values: Dict[str, str] = {}
    for key in FORM_COMMON_KEYS:
        value = source.get(key)
        if value is not None:
            form_values[key] = value
    return form_values


def _build_preview_from_platforms(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    platforms = entry.get("platforms") or {}
    preview: Dict[str, Any] = {}

    website = platforms.get("website") or {}
    for key in WEBSITE_PREVIEW_KEYS:
        if key in website:
            preview[key] = website.get(key)

    pinterest = platforms.get("pinterest") or {}
    for key in PINTEREST_PREVIEW_KEYS:
        if key in pinterest:
            preview[key] = pinterest.get(key)

    instagram = platforms.get("instagram") or {}
    instagram_feed = instagram.get("instagram_feed") or {}
    for key in INSTAGRAM_PREVIEW_KEYS:
        if key in instagram_feed:
            preview[key] = instagram_feed.get(key)

    youtube = platforms.get("youtube") or {}
    for key in YOUTUBE_PREVIEW_KEYS:
        if key in youtube:
            preview[key] = youtube.get(key)

    tiktok = platforms.get("tiktok") or {}
    for key in TIKTOK_PREVIEW_KEYS:
        if key in tiktok:
            preview[key] = tiktok.get(key)

    return _hydrate_preview(preview)


def _get_instagram_state(entry: Dict[str, Any]) -> Dict[str, Any]:
    instagram = (entry.get("platforms") or {}).get("instagram") or {}
    return (
        instagram.get("instagram_feed")
        or instagram.get("instagram_story")
        or instagram.get("instagram_reel")
        or {}
    )


def update_product_state(
    product_id: str,
    *,
    form_values: Optional[Dict[str, str]] = None,
    preview: Optional[Dict[str, Any]] = None,
    platforms: Optional[Dict[str, Dict[str, Any]]] = None,
    assets: Optional[Dict[str, Any]] = None,
    results: Optional[Dict[str, Any]] = None,
) -> None:
    if not product_id:
        return
    entry = db_get_product_entry(product_id) or {"platforms": {}, "assets": {}}
    entry.setdefault("platforms", {})
    entry.setdefault("assets", {})
    if assets:
        stored_assets = entry.get("assets", {})
        stored_assets.update(_json_safe(assets))
        entry["assets"] = stored_assets
    if results:
        _merge_platform_payloads(entry, _json_safe(results))
    if platforms:
        _merge_platform_payloads(entry, _json_safe(platforms))
    if form_values or preview:
        safe_preview = _sanitize_preview(preview or {})
        safe_form = _json_safe(form_values or {})
        # Pinterest
        pinterest_payload = {k: safe_preview.get(k) for k in PINTEREST_PREVIEW_KEYS if k in safe_preview}
        pinterest_payload.update({k: safe_form.get(k) for k in FORM_COMMON_KEYS if k in safe_form})
        _merge_platform_payloads(entry, {"pinterest": pinterest_payload})
        # Instagram
        instagram_payload = {k: safe_preview.get(k) for k in INSTAGRAM_PREVIEW_KEYS if k in safe_preview}
        instagram_payload.update({k: safe_form.get(k) for k in FORM_COMMON_KEYS if k in safe_form})
        if instagram_payload:
            _merge_platform_payloads(
                entry,
                {
                    "instagram_feed": instagram_payload,
                    "instagram_story": instagram_payload,
                    "instagram_reel": instagram_payload,
                },
            )
        # YouTube
        youtube_payload = {k: safe_preview.get(k) for k in YOUTUBE_PREVIEW_KEYS if k in safe_preview}
        youtube_payload.update({k: safe_form.get(k) for k in FORM_COMMON_KEYS if k in safe_form})
        _merge_platform_payloads(entry, {"youtube": youtube_payload})
        # TikTok
        tiktok_payload = {k: safe_preview.get(k) for k in TIKTOK_PREVIEW_KEYS if k in safe_preview}
        tiktok_payload.update({k: safe_form.get(k) for k in FORM_COMMON_KEYS if k in safe_form})
        _merge_platform_payloads(entry, {"tiktok": tiktok_payload})
        # Website
        website_payload = {k: safe_preview.get(k) for k in WEBSITE_PREVIEW_KEYS if k in safe_preview}
        website_payload.update({k: safe_form.get(k) for k in FORM_COMMON_KEYS if k in safe_form})
        _merge_platform_payloads(entry, {"website": website_payload})
    db_save_product_entry(product_id, entry)


def resolve_product_id(values: Dict[str, str]) -> str:
    raw = values.get("sku_or_url", "")
    market = (values.get("market") or "").strip().lower()
    if market == "amazon" and raw:
        try:
            return normalize_product_id(extract_asin(raw))
        except ValueError:
            return normalize_product_id(raw)
    return normalize_product_id(raw)


def collect_form_values(form_data) -> Dict[str, str]:
    raw: Dict[str, str] = {}
    for key in form_data.keys():
        values = form_data.getlist(key)
        if not values:
            continue
        raw[key] = values[-1].strip()
    return raw


def redirect_home_view():
    return redirect(url_for("home"))


def _merge_saved_form_values(
    saved_form_values: Dict[str, str],
    current_form_values: Optional[Dict[str, str]],
) -> Dict[str, str]:
    if not saved_form_values:
        return current_form_values or {}
    if not current_form_values:
        return dict(saved_form_values)
    merged = dict(saved_form_values)
    merged.update(current_form_values)
    return merged


def normalize_image_generation_model(raw_model: str) -> str:
    _, resolved_model = resolve_image_generation_choice(raw_model)
    return resolved_model


def normalize_video_generation_model(raw_model: str) -> str:
    _, resolved_model = resolve_video_generation_choice(raw_model)
    return resolved_model


def parse_selected_original_images_payload(
    raw_payload: str,
    *,
    fallback: str = "",
) -> List[str]:
    candidates: List[str] = []
    normalized_payload = str(raw_payload or "").strip()
    if normalized_payload:
        try:
            decoded = json.loads(normalized_payload)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, list):
            for item in decoded:
                item_value = str(item or "").strip()
                if item_value:
                    candidates.append(item_value)
        elif normalized_payload:
            candidates.append(normalized_payload)

    fallback_value = str(fallback or "").strip()
    if fallback_value and fallback_value not in candidates:
        candidates.insert(0, fallback_value)

    selected_values: List[str] = []
    for candidate in candidates:
        if candidate and candidate not in selected_values:
            selected_values.append(candidate)
    return selected_values


def serialize_selected_original_images_payload(selected_values: Sequence[str]) -> str:
    normalized = [str(value).strip() for value in selected_values if str(value).strip()]
    deduped: List[str] = []
    for value in normalized:
        if value not in deduped:
            deduped.append(value)
    return json.dumps(deduped)


def load_generation_reference_images(
    saved_state: Dict[str, Any],
    primary_image_path: str,
    *,
    limit: int = 2,
) -> List[bytes]:
    reference_bytes: List[bytes] = []
    for image_path in resolve_original_image_paths(saved_state, primary_image_path):
        if not image_path or image_path == primary_image_path:
            continue
        try:
            reference_bytes.append(load_stored_media(image_path))
        except Exception as exc:
            app.logger.warning("Unable to load reference image %s: %s", image_path, exc)
            continue
        if len(reference_bytes) >= limit:
            break
    return reference_bytes


def generate_platform_image_with_selected_model(
    base_image_bytes: bytes,
    *,
    saved_state: Dict[str, Any],
    primary_image_path: str,
    image_generation_model: str,
    prompt: str,
    context: str,
    aspect_ratio: str,
    reference_images: Optional[Sequence[bytes]] = None,
) -> bytes:
    provider, resolved_model = resolve_image_generation_choice(image_generation_model)
    if reference_images is None:
        resolved_reference_images = load_generation_reference_images(saved_state, primary_image_path)
    else:
        resolved_reference_images = list(reference_images)
    if provider == "gemini":
        return edit_image_with_gemini(
            base_image_bytes,
            context=context,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            model=resolved_model,
            reference_images=resolved_reference_images,
        )
    return edit_image_with_openai(
        base_image_bytes,
        context=context,
        prompt=prompt,
        aspect_ratio=aspect_ratio,
        model=resolved_model,
        reference_images=resolved_reference_images,
    )


def generate_platform_video_with_selected_model(
    base_image_bytes: bytes,
    *,
    video_generation_model: str,
    prompt: str,
    context: str,
    duration_seconds: int,
    aspect_ratio: str,
    resolution: str,
    reference_images: Optional[Sequence[bytes]] = None,
) -> bytes:
    provider, resolved_model = resolve_video_generation_choice(video_generation_model)
    if provider == "openai":
        return generate_video_with_openai(
            prompt=prompt,
            image=base_image_bytes,
            reference_images=reference_images,
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            model=resolved_model,
            context=context,
        )
    return generate_video_with_gemini(
        prompt=prompt,
        image=base_image_bytes,
        reference_images=reference_images,
        duration_seconds=duration_seconds,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        model=resolved_model,
        context=context,
    )


def _video_platform_state_key(target: str) -> str:
    return "instagram_reel" if target == "instagram" else target


def _video_generation_processing_payload(target: str) -> Dict[str, Any]:
    if target == "pinterest":
        return {"generation_status": "processing", "generation_error": ""}
    return {"generation_status": "processing", "generation_error": "", "status": "processing"}


def _video_generation_ready_payload(
    *,
    target: str,
    video_path: str,
    base_image_path: str,
    use_affiliate_link_flag: bool,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "generation_status": "ready",
        "generation_error": "",
        "video_path": video_path,
        "base_image_path": base_image_path,
        "use_affiliate_link": use_affiliate_link_flag,
    }
    if target == "pinterest":
        payload["video_status"] = "pending"
    else:
        payload["status"] = "pending"
    return payload


def _video_generation_error_payload(target: str, message: str) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "generation_status": "error",
        "generation_error": message,
    }
    if target != "pinterest":
        payload["status"] = "pending"
    return payload


def _complete_video_generation_state(
    *,
    target: str,
    product_id: str,
    form_values: Dict[str, str],
    preview_payload: Dict[str, Any],
    video_path: str,
    duration_seconds: int,
    boost_prompt: str,
    base_image_path: str,
    use_affiliate_link_flag: bool,
) -> None:
    if target == "youtube":
        preview_payload["youtube_boost_prompt"] = boost_prompt
    elif target == "instagram":
        preview_payload["instagram_boost_prompt"] = boost_prompt
    preview_payload["generated_video_path"] = video_path
    preview_payload["video_duration_seconds"] = str(duration_seconds)
    try:
        preview_payload["video_url"] = url_for("serve_media", filename=video_path)
        preview_payload["video_public_url"] = url_for(
            "serve_media", filename=video_path, _external=True
        )
        preview_payload["video_download_url"] = url_for("download_media", filename=video_path)
    except RuntimeError:
        preview_payload["video_url"] = ""
        preview_payload["video_public_url"] = ""
        preview_payload["video_download_url"] = ""

    update_product_state(
        product_id,
        form_values=form_values,
        preview=preview_payload,
        platforms={
            _video_platform_state_key(target): _video_generation_ready_payload(
                target=target,
                video_path=video_path,
                base_image_path=base_image_path,
                use_affiliate_link_flag=use_affiliate_link_flag,
            )
        },
        assets={"generated_video_path": video_path},
    )


def _run_openai_video_generation_async(
    *,
    target: str,
    product_id: str,
    form_values: Dict[str, str],
    preview_payload: Dict[str, Any],
    base_image_bytes: bytes,
    reference_images: Sequence[bytes],
    prompt: str,
    context: str,
    duration_seconds: int,
    base_image_path: str,
    boost_prompt: str,
    use_affiliate_link_flag: bool,
) -> None:
    try:
        with app.app_context():
            video_bytes = generate_platform_video_with_selected_model(
                base_image_bytes,
                prompt=prompt,
                video_generation_model=form_values.get("video_generation_model", ""),
                context=context,
                duration_seconds=duration_seconds,
                aspect_ratio="9:16",
                resolution="720p",
                reference_images=reference_images,
            )
            video_path = save_generated_video(video_bytes)
            _complete_video_generation_state(
                target=target,
                product_id=product_id,
                form_values=form_values,
                preview_payload=preview_payload,
                video_path=video_path,
                duration_seconds=duration_seconds,
                boost_prompt=boost_prompt,
                base_image_path=base_image_path,
                use_affiliate_link_flag=use_affiliate_link_flag,
            )
    except Exception as exc:
        app.logger.exception("Async OpenAI video generation failed for %s", target)
        with app.app_context():
            update_product_state(
                product_id,
                form_values=form_values,
                preview=preview_payload,
                platforms={
                    _video_platform_state_key(target): _video_generation_error_payload(
                        target,
                        str(exc),
                    )
                },
            )


def _start_openai_video_generation_job(
    *,
    target: str,
    product_id: str,
    form_values: Dict[str, str],
    preview_payload: Dict[str, Any],
    base_image_bytes: bytes,
    reference_images: Sequence[bytes],
    prompt: str,
    context: str,
    duration_seconds: int,
    base_image_path: str,
    boost_prompt: str,
    use_affiliate_link_flag: bool,
) -> None:
    update_product_state(
        product_id,
        form_values=form_values,
        preview=preview_payload,
        platforms={
            _video_platform_state_key(target): _video_generation_processing_payload(target)
        },
    )

    thread = threading.Thread(
        target=_run_openai_video_generation_async,
        kwargs={
            "target": target,
            "product_id": product_id,
            "form_values": dict(form_values),
            "preview_payload": dict(preview_payload),
            "base_image_bytes": bytes(base_image_bytes),
            "reference_images": list(reference_images),
            "prompt": prompt,
            "context": context,
            "duration_seconds": duration_seconds,
            "base_image_path": base_image_path,
            "boost_prompt": boost_prompt,
            "use_affiliate_link_flag": use_affiliate_link_flag,
        },
        name=f"openai-video-{target}-{product_id[:24]}",
        daemon=True,
    )
    thread.start()


def render_home_view(
    form_values: Optional[Dict[str, str]],
    preview_payload: Optional[Dict[str, Any]] = None,
    pinterest_result: Optional[Dict[str, Any]] = None,
    *,
    product_id: str = "",
    website_result: Optional[Dict[str, Any]] = None,
    platform_states: Optional[Dict[str, Any]] = None,
):
    state = load_app_state()
    product_ids = list((state.get("products") or {}).keys())
    last_product_id = state.get("last_product_id") or ""
    state_entry: Optional[Dict[str, Any]] = None
    if product_id:
        state_entry = get_product_state(product_id)
    if state_entry:
        saved_form_values, saved_preview_payload, saved_pinterest_result = build_render_payload(state_entry)
        form_values = _merge_saved_form_values(saved_form_values, form_values)
        if preview_payload is None:
            preview_payload = saved_preview_payload
        if pinterest_result is None:
            pinterest_result = saved_pinterest_result
    if website_result is None:
        if state_entry:
            website_result = get_platform_state(state_entry, "website")
        elif product_id:
            website_result = get_website_result(product_id)
    if platform_states is None:
        if state_entry:
            platform_states = state_entry.get("platforms") or {}
        else:
            platform_states = {}
    effective_form_values = dict(form_values or {})
    effective_form_values["image_generation_model"] = normalize_image_generation_model(
        effective_form_values.get("image_generation_model", "")
    )
    effective_form_values["video_generation_model"] = normalize_video_generation_model(
        effective_form_values.get("video_generation_model", "")
    )
    product_image_choices, selected_original_image, selected_original_images = build_product_image_choices(
        effective_form_values, preview_payload, product_id
    )
    effective_form_values["selected_original_image"] = selected_original_image
    effective_form_values["selected_original_images"] = serialize_selected_original_images_payload(
        selected_original_images
    )
    return render_template(
        "home.html",
        markets=MARKET_OPTIONS,
        form_values=effective_form_values,
        preview=preview_payload,
        result=pinterest_result,
        website_result=website_result,
        platform_states=platform_states or {},
        product_image_choices=product_image_choices,
        selected_original_image=selected_original_image,
        selected_original_images=selected_original_images,
        product_ids=product_ids,
        last_product_id=last_product_id,
        image_generation_options=IMAGE_GENERATION_OPTIONS,
        video_generation_options=VIDEO_GENERATION_OPTIONS,
    )


def build_render_payload(entry: Dict[str, Any]):
    if not entry:
        return {}, None, None
    form_values = _build_form_values_from_platforms(entry)
    preview_payload = _build_preview_from_platforms(entry)
    assets_snapshot = entry.get("assets") or {}
    if preview_payload and assets_snapshot:
        stored_video_path = assets_snapshot.get("generated_video_path")
        if stored_video_path and not preview_payload.get("generated_video_path"):
            preview_payload["generated_video_path"] = stored_video_path
        if stored_video_path and not preview_payload.get("video_url"):
            preview_payload["video_url"] = url_for("serve_media", filename=stored_video_path)
        if stored_video_path and not preview_payload.get("video_download_url"):
            preview_payload["video_download_url"] = url_for("download_media", filename=stored_video_path)
        if stored_video_path and not preview_payload.get("video_public_url"):
            preview_payload["video_public_url"] = url_for(
                "serve_media", filename=stored_video_path, _external=True
            )
    pinterest_state = get_platform_state(entry, "pinterest")
    return form_values, preview_payload, pinterest_state


def reset_product_platform(product_id: str, platform: str) -> None:
    if not product_id or platform not in RESETTABLE_PLATFORMS:
        return
    state = load_app_state()
    products = state.get("products", {})
    entry = products.get(product_id)
    if not entry:
        return
    platform = platform.lower()
    if platform == "pinterest":
        platforms = entry.get("platforms") or {}
        platforms.pop("pinterest", None)
        entry["platforms"] = platforms
        products[product_id] = entry
        save_app_state(state)
        return

    platforms = entry.setdefault("platforms", {})
    assets = entry.setdefault("assets", {})

    if platform == "instagram":
        instagram = platforms.get("instagram") or {}
        for alias in ("instagram_feed", "instagram_story", "instagram_reel"):
            instagram.pop(alias, None)
        if instagram:
            platforms["instagram"] = instagram
        else:
            platforms.pop("instagram", None)
        assets.pop("instagram_image_path", None)
    elif platform == "youtube":
        platforms.pop("youtube", None)
    elif platform == "tiktok":
        platforms.pop("tiktok", None)

    entry["platforms"] = platforms
    entry["assets"] = assets
    products[product_id] = entry
    save_app_state(state)

def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def allowed_video_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_VIDEO_EXTENSIONS


def _store_image(bytes_data: bytes, directory: Path, suffix: str) -> str:
    print(f"DEBUG _store_image: bytes_data type={type(bytes_data)}, len={len(bytes_data) if bytes_data else 0}", file=sys.stderr, flush=True)
    directory.mkdir(parents=True, exist_ok=True)
    print(f"DEBUG _store_image: directory created/ensured at {directory}", file=sys.stderr, flush=True)
    filename = f"{uuid4().hex}{suffix}"
    destination = directory / filename
    print(f"DEBUG _store_image: writing to {destination}", file=sys.stderr, flush=True)
    with open(destination, "wb") as file_handle:
        bytes_written = file_handle.write(bytes_data)
        print(f"DEBUG _store_image: wrote {bytes_written} bytes", file=sys.stderr, flush=True)
    print(f"DEBUG _store_image: file exists after write? {destination.exists()}", file=sys.stderr, flush=True)
    relative_path = destination.relative_to(STORAGE_ROOT).as_posix()
    print(f"DEBUG _store_image: returning {relative_path}", file=sys.stderr, flush=True)
    return relative_path


def resolve_storage_path(relative_path: str) -> Optional[str]:
    if not relative_path:
        return None
    target = (STORAGE_ROOT / relative_path).resolve()
    storage_root = STORAGE_ROOT.resolve()
    if not str(target).startswith(str(storage_root)) or not target.exists():
        return None
    return str(target)


def save_original_image(bytes_data: bytes, original_filename: str) -> str:
    suffix = Path(original_filename).suffix.lower() or ".png"
    return _store_image(bytes_data, ORIGINALS_DIR, suffix)


def store_uploaded_product_image(
    image_file,
    saved_state: Dict[str, Any],
) -> tuple[str, Dict[str, Any]]:
    if image_file is None or not image_file.filename:
        raise ValueError("Choose an image file to upload first.")
    if not allowed_file(image_file.filename):
        raise ValueError("Unsupported image type. Use PNG, JPG, JPEG, GIF, or WEBP.")

    original_bytes = image_file.read()
    if not original_bytes:
        raise ValueError("The uploaded image appears to be empty.")

    original_image_path = save_original_image(original_bytes, image_file.filename)
    return original_image_path, {
        "original_image_path": original_image_path,
        "original_image_paths": merge_original_image_paths(saved_state, original_image_path),
    }


def save_generated_image(bytes_data: bytes) -> str:
    return _store_image(bytes_data, GENERATED_DIR, ".png")


def save_generated_video(bytes_data: bytes) -> str:
    return _store_image(bytes_data, VIDEOS_DIR, ".mp4")


def save_uploaded_video(bytes_data: bytes, original_filename: str) -> str:
    suffix = Path(original_filename).suffix.lower() or ".mp4"
    if suffix.lstrip(".") not in ALLOWED_VIDEO_EXTENSIONS:
        suffix = ".mp4"
    return _store_image(bytes_data, VIDEOS_DIR, suffix)


def load_stored_media(relative_path: str) -> bytes:
    if not relative_path:
        raise FileNotFoundError("Missing image path.")
    target = (STORAGE_ROOT / relative_path).resolve()
    storage_root = STORAGE_ROOT.resolve()
    if not str(target).startswith(str(storage_root)):
        raise ValueError("Invalid image path.")
    if not target.exists():
        raise FileNotFoundError(f"Image not found at {relative_path}")
    with open(target, "rb") as file_handle:
        return file_handle.read()


def load_stored_image(relative_path: str) -> bytes:
    """Backward-compatible alias for image loading."""

    return load_stored_media(relative_path)


def parse_tags_payload(raw_tags: str) -> List[str]:
    if not raw_tags:
        return []
    try:
        loaded = json.loads(raw_tags)
        if isinstance(loaded, list):
            return sanitize_tag_list([str(item) for item in loaded])
    except json.JSONDecodeError:
        pass
    normalized = str(raw_tags).replace("\n", ",")
    return sanitize_tag_list(normalized.split(","))


def sanitize_tag_list(raw_items: List[str]) -> List[str]:
    cleaned: List[str] = []
    seen = set()
    for raw_item in raw_items:
        token = str(raw_item or "").strip()
        if not token:
            continue
        token = token.lstrip("#").strip()
        token = token.strip(" \t\r\n.,:;!?/\\|()[]{}<>\"'`")
        if not token or token in {"&", "+", "-", "_"}:
            continue
        normalized_key = token.casefold()
        if normalized_key in seen:
            continue
        cleaned.append(token)
        seen.add(normalized_key)
    return cleaned


def resolve_destination_url(product_id: str, affiliate_link: str, use_affiliate_link: bool) -> str:
    if use_affiliate_link or not product_id:
        return affiliate_link
    product_url = get_website_product_url(product_id)
    return product_url or affiliate_link


def build_prompt_context(form_values: Dict[str, str]) -> str:
    context_bits = []
    for field in (
        "market",
        "sku_or_url",
        "title",
        "description",
        "affiliate_link",
        "pinterest_extra",
    ):
        value = form_values.get(field)
        if value:
            context_bits.append(f"{field.replace('_', ' ').title()}: {value}")
    return " | ".join(context_bits)


def build_website_description(title: str, base_description: str, boost_prompt: str) -> str:
    if not boost_prompt:
        return base_description
    payload = (
        f"Title: {title}\nExisting Description: {base_description}\n"
        f"Boost Prompt: {boost_prompt}"
    )
    enhanced = generate_text(
        payload,
        context=(
            "You are an e-commerce copywriter. Expand concise marketing copy into "
            "a compelling store-ready product description in under 180 words."
        ),
        user_prompt=(
            "Rewrite the e-commerce product description using the boost prompt guidance:\n{text}"
        ),
        max_tokens=220,
        temperature=0.6,
    )
    return enhanced or base_description


def resolve_amazon_domain(raw_url: str) -> str:
    if not raw_url:
        return "US"
    parsed = urlparse(raw_url)
    host = (parsed.netloc or "").lower()
    if not host:
        return "US"
    if "amazon." not in host:
        return "US"
    suffix = host.split("amazon.", 1)[-1]
    if not suffix:
        return "US"
    marketplace_map = {
        "com": "US",
        "co.uk": "UK",
        "com.tr": "TR",
        "de": "DE",
        "fr": "FR",
        "it": "IT",
        "es": "ES",
        "ca": "CA",
        "com.mx": "MX",
        "com.br": "BR",
        "co.jp": "JP",
        "nl": "NL",
        "pl": "PL",
        "se": "SE",
        "ae": "AE",
        "sa": "SA",
        "sg": "SG",
        "com.au": "AU",
    }
    return marketplace_map.get(suffix, suffix.split(".")[-1].upper())


def guess_filename_from_url(raw_url: str, default_name: str = "amazon") -> str:
    parsed = urlparse(raw_url)
    suffix = Path(parsed.path or "").suffix.lower()
    if suffix.lstrip(".") not in ALLOWED_EXTENSIONS:
        suffix = ".jpg"
    return f"{default_name}{suffix}"


def download_image_bytes(image_url: str) -> bytes:
    resp = requests.get(image_url, timeout=15)
    if resp.status_code != 200:
        raise ValueError(f"Image download failed ({resp.status_code}).")
    return resp.content


def resolve_source_image_urls(saved_state: Dict[str, Any]) -> List[str]:
    assets = (saved_state.get("assets") or {}) if saved_state else {}
    return assets.get("source_image_urls") or assets.get("amazon_image_urls") or []


def build_product_image_choices(
    form_values: Dict[str, str],
    preview_payload: Optional[Dict[str, Any]],
    product_id: str,
) -> tuple[List[Dict[str, str]], str, List[str]]:
    saved_state = get_product_state(product_id) if product_id else {}
    source_urls = resolve_source_image_urls(saved_state)
    choices: List[Dict[str, str]] = []
    seen = set()
    selected_value = form_values.get("selected_original_image", "")
    selected_values = parse_selected_original_images_payload(
        form_values.get("selected_original_images", ""),
        fallback=selected_value,
    )
    if source_urls:
        for url in source_urls:
            if url and url not in seen:
                choices.append({"value": f"url:{url}", "url": url})
                seen.add(url)
    candidate_relative = ""
    if preview_payload:
        candidate_relative = preview_payload.get("original_image_path", "")
    if not candidate_relative:
        candidate_relative = form_values.get("original_image_path", "")
    if not selected_values and candidate_relative:
        selected_values = [f"path:{candidate_relative}"]
    if selected_values:
        selected_value = selected_values[0]
    for path in resolve_original_image_paths(saved_state, candidate_relative):
        image_url = url_for("serve_media", filename=path)
        if image_url not in seen:
            choices.append({"value": f"path:{path}", "url": image_url})
            seen.add(image_url)
    valid_values = {choice["value"] for choice in choices}
    selected_values = [value for value in selected_values if value in valid_values]
    if choices and not selected_values:
        selected_values = [choices[0]["value"]]
    selected_value = selected_values[0] if selected_values else ""
    return choices, selected_value, selected_values


def resolve_selected_original_images(
    raw_form_values: Dict[str, str],
    form_values: Dict[str, str],
    product_id: str,
    preview_payload: Optional[Dict[str, Any]],
) -> List[tuple[str, Optional[bytes]]]:
    saved_state = get_product_state(product_id) if product_id else {}
    selected_values = parse_selected_original_images_payload(
        raw_form_values.get("selected_original_images", ""),
        fallback=raw_form_values.get("selected_original_image", ""),
    )
    if not selected_values:
        return []

    resolved_sources: List[tuple[str, Optional[bytes]]] = []
    normalized_selected_values: List[str] = []

    for selected in selected_values:
        if selected.startswith("url:"):
            image_url = selected[4:]
            if not image_url:
                continue
            image_bytes = download_image_bytes(image_url)
            filename = guess_filename_from_url(image_url)
            image_relative = save_original_image(image_bytes, filename)
            normalized_selected_values.append(f"path:{image_relative}")
            resolved_sources.append((image_relative, image_bytes))
            continue

        if selected.startswith("path:"):
            image_relative = selected[5:]
            if resolve_storage_path(image_relative):
                normalized_selected_values.append(f"path:{image_relative}")
                resolved_sources.append((image_relative, None))

    deduped_sources: List[tuple[str, Optional[bytes]]] = []
    seen_paths = set()
    deduped_selected_values: List[str] = []
    for path, image_bytes in resolved_sources:
        if not path or path in seen_paths:
            continue
        seen_paths.add(path)
        deduped_sources.append((path, image_bytes))
        deduped_selected_values.append(f"path:{path}")

    if not deduped_sources:
        return []

    primary_image_path = deduped_sources[0][0]
    form_values["original_image_path"] = primary_image_path
    form_values["selected_original_image"] = deduped_selected_values[0]
    form_values["selected_original_images"] = serialize_selected_original_images_payload(
        deduped_selected_values
    )
    update_product_state(
        product_id,
        form_values=form_values,
        assets={
            "original_image_path": primary_image_path,
            "original_image_paths": merge_original_image_paths(
                saved_state,
                *[path for path, _ in deduped_sources],
            ),
        },
    )
    if preview_payload is not None:
        preview_payload["original_image_path"] = primary_image_path
    return deduped_sources


def resolve_selected_original_image(
    raw_form_values: Dict[str, str],
    form_values: Dict[str, str],
    product_id: str,
    preview_payload: Optional[Dict[str, Any]],
) -> Optional[tuple[str, Optional[bytes]]]:
    selected_sources = resolve_selected_original_images(
        raw_form_values,
        form_values,
        product_id,
        preview_payload,
    )
    return selected_sources[0] if selected_sources else None


def load_resolved_reference_images(
    resolved_sources: Sequence[tuple[str, Optional[bytes]]],
    *,
    primary_image_path: str = "",
    limit: Optional[int] = 3,
) -> List[bytes]:
    reference_bytes: List[bytes] = []
    for image_path, image_bytes in resolved_sources:
        if not image_path or image_path == primary_image_path:
            continue
        resolved_bytes = image_bytes
        if resolved_bytes is None:
            try:
                resolved_bytes = load_stored_media(image_path)
            except Exception as exc:
                app.logger.warning("Unable to load selected reference image %s: %s", image_path, exc)
                continue
        reference_bytes.append(resolved_bytes)
        if limit is not None and len(reference_bytes) >= limit:
            break
    return reference_bytes


def resolve_original_image_paths(
    saved_state: Dict[str, Any],
    fallback_path: Optional[str] = None,
) -> List[str]:
    assets = (saved_state.get("assets") or {}) if saved_state else {}
    candidates: List[str] = []
    if fallback_path:
        candidates.append(fallback_path)
    stored_paths = assets.get("original_image_paths") or []
    if isinstance(stored_paths, list):
        candidates.extend(path for path in stored_paths if path)
    stored_single = assets.get("original_image_path")
    if stored_single:
        candidates.append(stored_single)
    resolved: List[str] = []
    seen = set()
    for path in candidates:
        if not path or path in seen:
            continue
        if resolve_storage_path(path):
            resolved.append(path)
            seen.add(path)
    return resolved


def merge_original_image_paths(
    saved_state: Dict[str, Any],
    *paths: Optional[str],
) -> List[str]:
    assets = (saved_state.get("assets") or {}) if saved_state else {}
    existing_paths = assets.get("original_image_paths") or []
    existing_single = assets.get("original_image_path")
    candidates: List[str] = []
    for path in (*paths, existing_single, *existing_paths):
        if not path or path in candidates:
            continue
        candidates.append(path)
    return candidates


def ensure_downloaded_original_image(
    product_id: str,
    form_values: Dict[str, str],
    saved_state: Dict[str, Any],
    preview_payload: Optional[Dict[str, Any]] = None,
) -> Optional[tuple[str, bytes]]:
    image_urls = resolve_source_image_urls(saved_state)
    if not image_urls:
        return None
    image_url = image_urls[0]
    image_bytes = download_image_bytes(image_url)
    filename = guess_filename_from_url(image_url)
    image_relative = save_original_image(image_bytes, filename)
    resolved = resolve_storage_path(image_relative)
    if not resolved:
        return None
    form_values["original_image_path"] = image_relative
    update_product_state(
        product_id,
        form_values=form_values,
        assets={
            "original_image_path": image_relative,
            "original_image_paths": merge_original_image_paths(saved_state, image_relative),
        },
    )
    if preview_payload is not None:
        preview_payload["original_image_path"] = image_relative
    return image_relative, image_bytes


def ensure_downloaded_original_images(
    product_id: str,
    form_values: Dict[str, str],
    saved_state: Dict[str, Any],
    preview_payload: Optional[Dict[str, Any]] = None,
    preferred_primary_path: Optional[str] = None,
) -> List[str]:
    existing_paths = resolve_original_image_paths(saved_state, preferred_primary_path)
    image_urls = resolve_source_image_urls(saved_state)
    if not image_urls:
        return existing_paths
    downloaded_paths: List[str] = []
    for index, image_url in enumerate(image_urls):
        try:
            image_bytes = download_image_bytes(image_url)
        except Exception as exc:
            app.logger.warning("Source image download failed for %s: %s", image_url, exc)
            continue
        filename = guess_filename_from_url(image_url, default_name=f"amazon_{index + 1}")
        image_relative = save_original_image(image_bytes, filename)
        downloaded_paths.append(image_relative)

    merged_paths: List[str] = []
    for path in (preferred_primary_path, *existing_paths, *downloaded_paths):
        if not path or path in merged_paths:
            continue
        if resolve_storage_path(path):
            merged_paths.append(path)

    if not merged_paths:
        return []
    primary_image = merged_paths[0]
    form_values["original_image_path"] = primary_image
    update_product_state(
        product_id,
        form_values=form_values,
        assets={"original_image_path": primary_image, "original_image_paths": merged_paths},
    )
    if preview_payload is not None:
        preview_payload["original_image_path"] = primary_image
    return merged_paths


def extract_form_defaults(raw_form_values: Dict[str, str]) -> Dict[str, str]:
    defaults = {
        key: raw_form_values.get(key, "")
        for key in (
            "market",
            "sku_or_url",
            "title",
            "description",
            "affiliate_link",
            "pinterest_extra",
            "category",
            "price",
            "website_boost_prompt",
            "youtube_boost_prompt",
            "instagram_boost_prompt",
            "use_affiliate_link",
            "selected_original_image",
            "selected_original_images",
            "image_generation_model",
            "video_generation_model",
            "video_duration_seconds",
        )
    }
    defaults["website_boost_prompt"] = (
        raw_form_values.get("website_boost_prompt")
        or raw_form_values.get("website_boost_prompt_pref")
        or defaults.get("website_boost_prompt")
        or ""
    )
    defaults["instagram_boost_prompt"] = (
        raw_form_values.get("instagram_boost_prompt")
        or raw_form_values.get("instagram_boost_prompt_pref")
        or defaults.get("instagram_boost_prompt")
        or ""
    )
    prefer_order = [
        raw_form_values.get("use_affiliate_link"),
        raw_form_values.get("use_affiliate_link_pref"),
        defaults.get("use_affiliate_link"),
        "0",
    ]
    defaults["use_affiliate_link"] = next(
        (val for val in prefer_order if val not in (None, "")),
        "0",
    )
    defaults["image_generation_model"] = normalize_image_generation_model(
        raw_form_values.get("image_generation_model", defaults.get("image_generation_model", ""))
    )
    defaults["video_generation_model"] = normalize_video_generation_model(
        raw_form_values.get("video_generation_model", defaults.get("video_generation_model", ""))
    )
    defaults["selected_original_images"] = serialize_selected_original_images_payload(
        parse_selected_original_images_payload(
            raw_form_values.get("selected_original_images", defaults.get("selected_original_images", "")),
            fallback=defaults.get("selected_original_image", ""),
        )
    )
    return defaults


def rebuild_preview_payload(raw_form_values: Dict[str, str]):
    generated_image_path = raw_form_values.get("generated_image_path")
    if not generated_image_path:
        return None
    try:
        image_bytes = load_stored_media(generated_image_path)
    except Exception:
        return None

    tags_payload = raw_form_values.get("tags_payload") or raw_form_values.get("tags", "[]")
    instagram_payload = raw_form_values.get("instagram_hashtags_payload", "[]")
    tiktok_payload = raw_form_values.get("tiktok_hashtags_payload", "[]")
    youtube_payload = raw_form_values.get("youtube_keywords_payload", "[]")

    payload = {
        "title": raw_form_values.get("title", ""),
        "description": raw_form_values.get("description", ""),
        "tags": parse_tags_payload(tags_payload),
        "tags_payload": tags_payload,
        "image_data": base64.b64encode(image_bytes).decode("utf-8"),
        "generated_image_path": generated_image_path,
        "original_image_path": raw_form_values.get("original_image_path", ""),
        "affiliate_link": raw_form_values.get("affiliate_link"),
        "market": raw_form_values.get("market"),
        "sku_or_url": raw_form_values.get("sku_or_url"),
        "pinterest_extra": raw_form_values.get("pinterest_extra"),
        "title_input": raw_form_values.get("title", ""),
        "description_input": raw_form_values.get("description", ""),
        "instagram_caption": raw_form_values.get("instagram_caption", ""),
        "instagram_hashtags": parse_tags_payload(instagram_payload),
        "instagram_hashtags_payload": instagram_payload,
        "tiktok_caption": raw_form_values.get("tiktok_caption", ""),
        "tiktok_hashtags": parse_tags_payload(tiktok_payload),
        "tiktok_hashtags_payload": tiktok_payload,
        "youtube_title": raw_form_values.get("youtube_title", raw_form_values.get("title", "")),
        "youtube_description": raw_form_values.get("youtube_description", raw_form_values.get("description", "")),
        "youtube_keywords": parse_tags_payload(youtube_payload),
        "youtube_keywords_payload": youtube_payload,
        "video_prompt": raw_form_values.get("video_prompt", ""),
        "instagram_image_path": raw_form_values.get("instagram_image_path", ""),
        "category": raw_form_values.get("category", ""),
        "price": raw_form_values.get("price", ""),
        "website_boost_prompt": raw_form_values.get("website_boost_prompt", ""),
        "instagram_boost_prompt": raw_form_values.get("instagram_boost_prompt", ""),
        "video_hook_style": raw_form_values.get("video_hook_style", ""),
        "youtube_boost_prompt": raw_form_values.get("youtube_boost_prompt", ""),
        "video_duration_seconds": raw_form_values.get("video_duration_seconds", ""),
        "selected_original_images": raw_form_values.get("selected_original_images", ""),
        "use_affiliate_link": (
            raw_form_values.get("use_affiliate_link")
            or raw_form_values.get("use_affiliate_link_pref")
            or "0"
        ),
    }

    image_relative = raw_form_values.get("generated_image_path", "")
    if image_relative:
        payload["generated_image_url"] = url_for("serve_media", filename=image_relative)
        payload["generated_image_download_url"] = url_for(
            "download_media", filename=image_relative
        )
        payload["image_public_url"] = url_for("serve_media", filename=image_relative, _external=True)
    
    video_path = raw_form_values.get("generated_video_path", "")
    payload["generated_video_path"] = video_path
    if video_path:
        payload["video_url"] = url_for("serve_media", filename=video_path)
        payload["video_download_url"] = url_for("download_media", filename=video_path)
        payload["video_public_url"] = url_for("serve_media", filename=video_path, _external=True)
    else:
        payload["video_url"] = ""
        payload["video_download_url"] = ""
        payload["video_public_url"] = ""

    instagram_image_path = raw_form_values.get("instagram_image_path", "")
    if instagram_image_path:
        payload["instagram_image_path"] = instagram_image_path
        try:
            ig_bytes = load_stored_media(instagram_image_path)
            payload["instagram_image_url"] = url_for("serve_media", filename=instagram_image_path)
            payload["instagram_image_download_url"] = url_for(
                "download_media", filename=instagram_image_path
            )
            payload["instagram_image_public_url"] = url_for(
                "serve_media", filename=instagram_image_path, _external=True
            )
            payload["instagram_image_data"] = base64.b64encode(ig_bytes).decode("utf-8")
        except Exception:
            payload["instagram_image_url"] = ""
            payload["instagram_image_download_url"] = ""
            payload["instagram_image_public_url"] = ""
            payload["instagram_image_data"] = ""
    else:
        payload["instagram_image_url"] = ""
        payload["instagram_image_download_url"] = ""
        payload["instagram_image_public_url"] = ""
        payload["instagram_image_data"] = ""

    return payload


@app.route("/media/<path:filename>")
def serve_media(filename: str):
    safe_path = (STORAGE_ROOT / filename).resolve()
    if not str(safe_path).startswith(str(STORAGE_ROOT.resolve())) or not safe_path.exists():
        abort(404)
    relative_path = safe_path.relative_to(STORAGE_ROOT)
    return send_from_directory(STORAGE_ROOT, relative_path.as_posix())


@app.route("/media-download/<path:filename>")
def download_media(filename: str):
    safe_path = (STORAGE_ROOT / filename).resolve()
    if not str(safe_path).startswith(str(STORAGE_ROOT.resolve())) or not safe_path.exists():
        abort(404)
    relative_path = safe_path.relative_to(STORAGE_ROOT)
    return send_from_directory(STORAGE_ROOT, relative_path.as_posix(), as_attachment=True)


@app.route("/api/tokens/status", methods=["GET"])
def get_token_status():
    """Get status of all OAuth tokens."""
    from kaymio.database.token_manager import get_all_token_status
    try:
        status = get_all_token_status()
        return jsonify({
            "success": True,
            "tokens": status,
        })
    except Exception as e:
        app.logger.exception("Error getting token status")
        return jsonify({
            "success": False,
            "error": str(e),
        }), 500


@app.route("/api/tokens/refresh", methods=["POST"])
def refresh_token_endpoint():
    """Manually refresh a specific platform's token."""
    from kaymio.database.token_manager import refresh_token

    platform = request.form.get("platform", "").lower().strip()
    if not platform:
        return jsonify({"success": False, "error": "Missing platform parameter"}), 400

    try:
        success = refresh_token(platform)
        if success:
            return jsonify({
                "success": True,
                "message": f"{platform.capitalize()} token refreshed successfully",
            })
        else:
            return jsonify({
                "success": False,
                "error": f"Failed to refresh {platform} token. Check logs for details.",
            }), 400
    except Exception as e:
        app.logger.exception("Error refreshing token for %s", platform)
        return jsonify({
            "success": False,
            "error": str(e),
        }), 500


@app.route("/", methods=["GET"])
def home() -> str:
    state = load_app_state()
    last_product_id = state.get("last_product_id") or ""
    saved_state = state.get("products", {}).get(last_product_id, {})
    form_values, preview_payload, pinterest_result = build_render_payload(saved_state)
    website_result = get_platform_state(saved_state, "website") if saved_state else None
    platform_states = saved_state.get("platforms") or {}
    return render_home_view(
        form_values,
        preview_payload,
        pinterest_result,
        product_id=last_product_id,
        website_result=website_result,
        platform_states=platform_states,
    )


@app.route("/reset-platform", methods=["POST"])
def reset_platform():
    raw_form_values = collect_form_values(request.form)
    platform = (raw_form_values.get("platform") or "").lower()
    if platform == "workflow":
        state = load_app_state()
        state["last_product_id"] = ""
        save_app_state(state)
        flash("Workflow reset. Ready for a new product.", "info")
        return render_home_view({}, None, None, product_id="")
    state = load_app_state()
    product_id = state.get("last_product_id") or ""

    if not product_id:
        flash("Select a product before resetting a platform.", "error")
    elif not platform or platform not in RESETTABLE_PLATFORMS:
        flash("Choose a valid platform to reset.", "error")
    else:
        reset_product_platform(product_id, platform)
        flash(f"{platform.title()} state reset.", "info")

    saved_state = get_product_state(product_id) if product_id else {}
    form_values, preview_payload, pinterest_result = build_render_payload(saved_state)
    website_result = get_platform_state(saved_state, "website") if saved_state else None
    return render_home_view(
        form_values,
        preview_payload,
        pinterest_result,
        product_id=product_id,
        website_result=website_result,
        platform_states=(saved_state.get("platforms") or {}),
    )


@app.route("/select-product", methods=["POST"])
def select_product():
    raw_form_values = collect_form_values(request.form)
    selected_product = (raw_form_values.get("product_id") or "").strip()
    state = load_app_state()
    products = state.get("products") or {}
    if selected_product and selected_product in products:
        state["last_product_id"] = selected_product
        save_app_state(state)
    return redirect(url_for("home"))


@app.route("/fetch-amazon-product", methods=["POST"])
def fetch_amazon_product():
    raw_form_values = collect_form_values(request.form)
    form_values = extract_form_defaults(raw_form_values)
    market = (form_values.get("market") or "").strip()
    sku_or_url = (form_values.get("sku_or_url") or "").strip()
    product_id = resolve_product_id(form_values)

    if not market or market.lower() != "amazon":
        flash("Choose Amazon before fetching marketplace data.", "error")
        return render_home_view(form_values, product_id=product_id)
    if not sku_or_url:
        flash("Provide an Amazon ASIN or product URL before fetching.", "error")
        return render_home_view(form_values, product_id=product_id)

    try:
        asin = extract_asin(sku_or_url)
    except ValueError as exc:
        flash(str(exc), "error")
        return render_home_view(form_values, product_id=product_id)

    try:
        marketplace = resolve_amazon_domain(sku_or_url)
        fetched = fetch_product_from_canopy(
            asin,
            marketplace,
            product_url=sku_or_url,
        )
    except Exception as exc:
        app.logger.exception("Amazon product fetch failed")
        flash(f"Unable to fetch Amazon product data: {exc}", "error")
        return render_home_view(form_values, product_id=product_id)

    updated_values = dict(form_values)
    candidate_values = {
        "title": fetched.get("title") or "",
        "description": fetched.get("description") or "",
        "category": fetched.get("category") or "",
        "price": fetched.get("price") or "",
        "affiliate_link": build_affiliate_link(asin),
    }
    for key, value in candidate_values.items():
        if not updated_values.get(key):
            updated_values[key] = value

    assets = {}
    image_urls = fetched.get("image_urls") or []
    if image_urls:
        assets["amazon_image_urls"] = image_urls
        assets["source_image_urls"] = image_urls

    update_product_state(
        product_id,
        form_values=updated_values,
        assets=assets,
    )
    flash("Amazon product data loaded. Review and edit the fields below.", "success")
    return redirect_home_view()


@app.route("/save-draft", methods=["POST"])
def save_draft():
    raw_form_values = collect_form_values(request.form)
    form_values = extract_form_defaults(raw_form_values)
    product_id = resolve_product_id(form_values)
    saved_state = get_product_state(product_id) if product_id else {}

    if not product_id:
        flash("Provide a SKU or product link before saving your progress.", "error")
        return render_home_view(form_values, product_id=product_id)

    image_file = request.files.get("product_image")
    assets = {}
    if image_file and image_file.filename:
        try:
            original_image_path, assets = store_uploaded_product_image(image_file, saved_state)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_home_view(form_values, product_id=product_id)
        form_values["original_image_path"] = original_image_path
        form_values["selected_original_image"] = f"path:{original_image_path}"
        form_values["selected_original_images"] = serialize_selected_original_images_payload(
            [f"path:{original_image_path}"]
        )
        raw_form_values["original_image_path"] = original_image_path
        raw_form_values["selected_original_image"] = f"path:{original_image_path}"
        raw_form_values["selected_original_images"] = serialize_selected_original_images_payload(
            [f"path:{original_image_path}"]
        )

    preview_payload = rebuild_preview_payload(raw_form_values)
    update_product_state(
        product_id,
        form_values=form_values,
        preview=preview_payload,
        assets=assets,
    )
    flash("Draft saved. You can return later to continue.", "success")
    return redirect_home_view()


@app.route("/upload-product-image", methods=["POST"])
def upload_product_image():
    raw_form_values = collect_form_values(request.form)
    form_values = extract_form_defaults(raw_form_values)
    product_id = resolve_product_id(form_values)
    saved_state = get_product_state(product_id) if product_id else {}

    if not product_id:
        flash("Fetch or enter a product first before adding gallery images.", "error")
        return render_home_view(form_values, product_id=product_id)

    image_file = request.files.get("product_image")
    try:
        original_image_path, assets = store_uploaded_product_image(image_file, saved_state)
    except ValueError as exc:
        flash(str(exc), "error")
        return render_home_view(form_values, product_id=product_id)

    form_values["original_image_path"] = original_image_path
    form_values["selected_original_image"] = f"path:{original_image_path}"
    form_values["selected_original_images"] = serialize_selected_original_images_payload(
        [f"path:{original_image_path}"]
    )
    update_product_state(
        product_id,
        form_values=form_values,
        assets=assets,
    )

    flash("Image added to the product gallery.", "success")
    return redirect_home_view()


@app.route("/generate-pinterest", methods=["POST"])
def generate_pinterest():
    raw_form_values = collect_form_values(request.form)
    original_image_path = raw_form_values.pop("original_image_path", "")
    form_values = dict(raw_form_values)
    form_values["image_generation_model"] = normalize_image_generation_model(
        raw_form_values.get("image_generation_model", "")
    )
    saved_state = get_product_state(resolve_product_id(form_values))
    if not original_image_path and saved_state:
        assets = saved_state.get("assets") or {}
        original_image_path = assets.get("original_image_path", "")
    auto_image_urls = resolve_source_image_urls(saved_state)
    form_values.setdefault("use_affiliate_link", "0")
    use_affiliate_link_flag = str(form_values.get("use_affiliate_link", "0")).lower() in TRUTHY_VALUES
    product_id = resolve_product_id(form_values)
    image_file = request.files.get("product_image")
    errors = []
    selected_sources: List[tuple[str, Optional[bytes]]] = []

    if not form_values.get("market"):
        errors.append("Please choose a marketplace before continuing.")

    if not form_values.get("sku_or_url"):
        errors.append("Please provide a SKU, ASIN, or product link.")

    if not form_values.get("title"):
        errors.append("Please provide a product title for Pinterest.")

    if not form_values.get("affiliate_link"):
        errors.append("Affiliate link is required so shoppers can reach the product.")

    if (image_file is None or image_file.filename == "") and not original_image_path and not auto_image_urls:
        errors.append("Upload at least one product image so we can craft a pin.")
    elif image_file is not None and image_file.filename and not allowed_file(image_file.filename):
        errors.append("Unsupported image type. Use PNG, JPG, JPEG, GIF, or WEBP.")

    if errors:
        for message in errors:
            flash(message, "error")
        return render_home_view(form_values, product_id=product_id)

    if raw_form_values.get("selected_original_image") or raw_form_values.get("selected_original_images"):
        try:
            selected_sources = resolve_selected_original_images(
                raw_form_values, form_values, product_id, preview_payload=None
            )
        except Exception as exc:
            app.logger.exception("Selected image download failed")
            flash(f"Unable to download the selected image: {exc}", "error")
            return render_home_view(form_values, product_id=product_id)

    if image_file is not None and image_file.filename:
        try:
            original_image_path, _ = store_uploaded_product_image(image_file, saved_state)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_home_view(form_values, product_id=product_id)
        form_values["selected_original_image"] = f"path:{original_image_path}"
        form_values["selected_original_images"] = serialize_selected_original_images_payload(
            [f"path:{original_image_path}"]
        )
        original_bytes = load_stored_image(original_image_path)
    elif selected_sources:
        original_image_path, original_bytes = selected_sources[0]
    else:
        if not original_image_path:
            try:
                downloaded = ensure_downloaded_original_image(
                    product_id, form_values, saved_state, preview_payload=None
                )
            except Exception as exc:
                app.logger.exception("Source image download failed")
                flash(f"Unable to download the product image: {exc}", "error")
                return render_home_view(form_values, product_id=product_id)
            if downloaded:
                original_image_path, original_bytes = downloaded
            else:
                flash("Unable to locate a product image. Please upload one.", "error")
                return render_home_view(form_values, product_id=product_id)
        else:
            try:
                original_bytes = load_stored_image(original_image_path)
            except Exception:
                flash("Unable to load the previously uploaded image. Please upload again.", "error")
                return render_home_view(form_values, product_id=product_id)
    if original_image_path and original_bytes is None:
        try:
            original_bytes = load_stored_image(original_image_path)
        except Exception:
            flash("Unable to load the selected image. Please try again.", "error")
            return render_home_view(form_values, product_id=product_id)
    selected_reference_images = load_resolved_reference_images(
        selected_sources,
        primary_image_path=original_image_path,
    )

    raw_title = form_values.get("title", "")
    raw_description = form_values.get("description", "")
    extra_context = form_values.get("pinterest_extra", "")

    try:
        title_payload = (
            f"Title: {raw_title}\nDescription: {raw_description}\nExtra Pinterest Context: {extra_context}"
        )
        refined_title = generate_text(
            title_payload,
            context=(
                "You are a Pinterest SEO copywriter. Craft concise, keyword-rich product titles "
                "that entice shoppers in under 70 characters."
            ),
            user_prompt=(
                "Generate a Pinterest-ready product title (max 70 characters) from this context:\n{text}"
            ),
        )
        if not refined_title or refined_title.lower() == "unknown concept":
            refined_title = raw_title

        description_payload = (
            f"Product Title: {refined_title or raw_title}\nDescription: {raw_description}\n"
            f"Extra Pinterest Context: {extra_context}"
        )
        generated_description = generate_text(
            description_payload,
            context=(
                "You are an affiliate marketing copywriter who writes compelling Pinterest pin "
                "descriptions that highlight benefits, urgency, and relevance."
            ),
            user_prompt=(
                "Write a two sentence Pinterest pin description from this information. "
                "Make it descriptive, energetic, and conversion-focused:\n{text}"
            ),
            max_tokens=120,
            temperature=0.5,
        )
        if not generated_description or generated_description.lower() == "unknown concept":
            generated_description = (
                raw_description or "Compelling Pinterest-ready description."
            )

        tags = sanitize_tag_list(generate_tags_for_product_for_pintrest(
            refined_title,
            generated_description,
        ))
        instagram_caption = generate_caption_for_instagram(
            refined_title,
            generated_description,
            call_to_action=form_values.get("pinterest_extra"),
        )
        instagram_caption = _prepend_instagram_comment_reply_caption_cta(instagram_caption)
        instagram_hashtags = sanitize_tag_list(
            generate_hashtags_for_instagram(refined_title, generated_description)
        )
        tiktok_caption = generate_caption_for_tiktok(refined_title, generated_description)
        tiktok_hashtags = sanitize_tag_list(
            generate_hashtags_for_tiktok(refined_title, generated_description)
        )
        youtube_metadata = generate_youtube_metadata(refined_title, generated_description)
        youtube_metadata["keywords"] = sanitize_tag_list(youtube_metadata.get("keywords") or [])

        context_prompt = build_prompt_context({**form_values, "title": refined_title})
        current_saved_state = get_product_state(product_id) if product_id else saved_state
        print(f"DEBUG: About to generate image for Pinterest product_id={product_id}", file=sys.stderr, flush=True)
        generated_image = generate_platform_image_with_selected_model(
            original_bytes,
            saved_state=current_saved_state,
            primary_image_path=original_image_path,
            image_generation_model=form_values.get("image_generation_model", ""),
            context=context_prompt,
            prompt=(
                "Design a polished product visual with a clear hero focus, scroll-stopping composition, "
                "and cohesive lighting suitable for social platforms. Do not add any on-screen text—the output must be a pure image."
            ),
            aspect_ratio="2:3",
            reference_images=(selected_reference_images if selected_sources else None),
        )
        print(f"DEBUG: Image generated, type={type(generated_image)}, len={len(generated_image) if generated_image else 0}", file=sys.stderr, flush=True)
        generated_image = ensure_dimensions(generated_image, (1000, 1500))
        print(f"DEBUG: Dimensions ensured, type={type(generated_image)}, len={len(generated_image) if generated_image else 0}", file=sys.stderr, flush=True)
        generated_image_path = save_generated_image(generated_image)
        print(f"DEBUG: Image saved to {generated_image_path}", file=sys.stderr, flush=True)
        generated_image_url = url_for("serve_media", filename=generated_image_path)
        image_public_url = url_for("serve_media", filename=generated_image_path, _external=True)

        preview_payload = {
            "title": refined_title,
            "description": generated_description,
            "tags": tags,
            "image_data": base64.b64encode(generated_image).decode("utf-8"),
            "generated_image_path": generated_image_path,
            "original_image_path": original_image_path,
            "affiliate_link": form_values.get("affiliate_link"),
            "market": form_values.get("market"),
            "sku_or_url": form_values.get("sku_or_url"),
            "pinterest_extra": form_values.get("pinterest_extra"),
            "title_input": form_values.get("title"),
            "description_input": form_values.get("description"),
            "tags_payload": json.dumps(tags or []),
            "generated_image_url": generated_image_url,
            "image_public_url": image_public_url,
            "instagram_caption": instagram_caption,
            "instagram_hashtags": instagram_hashtags,
            "instagram_hashtags_payload": json.dumps(instagram_hashtags or []),
            "instagram_image_path": generated_image_path,
            "instagram_image_url": generated_image_url,
            "instagram_image_public_url": image_public_url,
            "instagram_image_data": base64.b64encode(generated_image).decode("utf-8"),
            "tiktok_caption": tiktok_caption,
            "tiktok_hashtags": tiktok_hashtags,
            "tiktok_hashtags_payload": json.dumps(tiktok_hashtags or []),
            "youtube_title": youtube_metadata["title"],
            "youtube_description": youtube_metadata["description"],
            "youtube_keywords": youtube_metadata["keywords"],
            "youtube_keywords_payload": json.dumps(youtube_metadata["keywords"] or []),
            "generated_video_path": raw_form_values.get("generated_video_path", ""),
            "video_url": "",
            "video_public_url": "",
            "video_duration_seconds": str(VIDEO_DURATION_DEFAULT),
            "video_prompt": (
                f"Create a dynamic short-form video for {refined_title}. Include upbeat pacing, text overlays "
                f"highlighting the benefits, and close with a CTA to tap the affiliate link."
            ),
            "category": form_values.get("category", ""),
            "price": form_values.get("price", ""),
            "website_boost_prompt": form_values.get("website_boost_prompt", ""),
            "youtube_boost_prompt": form_values.get("youtube_boost_prompt", ""),
            "use_affiliate_link": form_values.get("use_affiliate_link", "0") or "0",
        }

    except Exception as exc:  # pragma: no cover - guard for runtime issues
        print(f"DEBUG: Pinterest generation exception: {exc}", file=sys.stderr, flush=True)
        import traceback
        traceback.print_exc(file=sys.stderr)
        app.logger.exception("Pinterest generation failed")
        flash(f"Unable to generate Pinterest pin: {exc}", "error")
        return render_home_view(form_values, product_id=product_id)
    flash("Preview generated. Choose where to publish your content.", "info")

    update_product_state(
        product_id,
        form_values=form_values,
        preview=preview_payload,
        platforms={
            "pinterest": {
                "status": "pending",
                "title": refined_title,
                "description": generated_description,
                "tags": tags,
                "use_affiliate_link": use_affiliate_link_flag,
            }
        },
        assets={
            "original_image_path": original_image_path,
            "original_image_paths": merge_original_image_paths(saved_state, original_image_path),
            "generated_image_path": generated_image_path,
        },
    )
    return redirect_home_view()


def ensure_dimensions(image_bytes: bytes, size: tuple[int, int]) -> bytes:
    """Resize image bytes to the requested size without distorting the aspect ratio."""
    try:
        image = Image.open(BytesIO(image_bytes))
        if image.size == size:
            return image_bytes
        # ImageOps.fit crops from the center so we hit the exact Pinterest/Instagram
        # dimensions while avoiding stretched visuals.
        fitted = ImageOps.fit(image, size, method=Image.LANCZOS)
        output = BytesIO()
        fitted.save(output, format=image.format or "PNG")
        return output.getvalue()
    except Exception:
        return image_bytes


def _validate_pinterest_publish_response(
    payload: Optional[Dict[str, Any]],
    *,
    asset_label: str,
) -> Optional[str]:
    if not isinstance(payload, dict):
        return f"Pinterest did not return a valid {asset_label} publish response."
    status = str(payload.get("status") or "").strip().lower()
    if status == "skipped":
        return (
            f"Pinterest {asset_label} publish was skipped because the Pinterest credentials "
            "or board configuration are missing."
        )
    if not payload.get("id") and not payload.get("url"):
        return f"Pinterest did not return a {asset_label} ID or URL."
    return None


@app.route("/confirm-pinterest", methods=["POST"])
def confirm_pinterest():
    raw_form_values = collect_form_values(request.form)
    generated_image_path = raw_form_values.get("generated_image_path")
    original_image_path = raw_form_values.get("original_image_path")
    form_values = extract_form_defaults(raw_form_values)
    product_id = resolve_product_id(form_values)
    tags = parse_tags_payload(raw_form_values.get("tags"))
    preview_payload = rebuild_preview_payload(raw_form_values)
    use_affiliate_link = raw_form_values.get("use_affiliate_link", form_values.get("use_affiliate_link", "0"))
    use_affiliate_link_flag = str(use_affiliate_link or "0").lower() in TRUTHY_VALUES

    try:
        image_bytes = load_stored_image(generated_image_path)
    except Exception:
        flash("Unable to load the generated image. Please regenerate it.", "error")
        return render_home_view(form_values, product_id=product_id)

    destination_link = resolve_destination_url(
        product_id,
        raw_form_values.get("affiliate_link", form_values.get("affiliate_link", "")),
        use_affiliate_link_flag,
    )
    try:
        pin_response = create_pinterest_pin(
            image_bytes,
            raw_form_values.get("title", ""),
            raw_form_values.get("description", ""),
            destination_link,
            tags,
        )
    except Exception as exc:
        app.logger.exception("Confirm Pinterest pin failed")
        flash(f"Unable to publish Pinterest pin: {exc}", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    publish_error = _validate_pinterest_publish_response(pin_response, asset_label="pin")
    if publish_error:
        flash(publish_error, "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    flash("Pinterest pin published successfully!", "success")
    result = {
        "title": raw_form_values.get("title", ""),
        "description": raw_form_values.get("description", ""),
        "tags": tags,
        "pin_id": pin_response.get("id"),
        "pin_url": pin_response.get("url"),
        "status": pin_response.get("status"),
    }
    update_product_state(
        product_id,
        form_values=form_values,
        preview=preview_payload,
        results={"pinterest": result},
        platforms={
            "pinterest": {
                "status": "published",
                "pin_id": pin_response.get("id"),
                "pin_url": pin_response.get("url"),
                "published": True,
                "use_affiliate_link": use_affiliate_link_flag,
            }
        },
        assets={
            "original_image_path": original_image_path,
            "generated_image_path": generated_image_path,
        },
    )
    return redirect_home_view()


@app.route("/publish-pinterest-video", methods=["POST"])
def publish_pinterest_video():
    raw_form_values = collect_form_values(request.form)
    form_values = extract_form_defaults(raw_form_values)
    use_affiliate_link_flag = str(form_values.get("use_affiliate_link", "0")).lower() in TRUTHY_VALUES
    product_id = resolve_product_id(form_values)
    preview_payload = rebuild_preview_payload(raw_form_values)
    video_path = raw_form_values.get("generated_video_path")

    if not video_path:
        flash("Generate the Pinterest video first, then publish.", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    try:
        # Load local generated video for Pinterest media upload.
        video_bytes = load_stored_media(video_path)
    except Exception:
        flash("Unable to load the generated video. Please regenerate it.", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    title = raw_form_values.get("title") or form_values.get("title", "")
    description = raw_form_values.get("description") or form_values.get("description", "")
    tags = parse_tags_payload(raw_form_values.get("tags_payload") or raw_form_values.get("tags", "[]"))
    destination_link = resolve_destination_url(
        product_id,
        raw_form_values.get("affiliate_link", form_values.get("affiliate_link", "")),
        use_affiliate_link_flag,
    )
    cover_image_url = preview_payload.get("image_public_url") if preview_payload else None

    try:
        pin_response = create_pinterest_video_pin(
            video_bytes=video_bytes,
            title=title,
            description=description,
            affiliate_link=destination_link,
            tags=tags,
            cover_image_url=cover_image_url,
        )
        publish_error = _validate_pinterest_publish_response(pin_response, asset_label="video pin")
        if publish_error:
            flash(publish_error, "error")
            return render_home_view(form_values, preview_payload, product_id=product_id)
        flash("Pinterest video published successfully!", "success")
        update_product_state(
            product_id,
            form_values=form_values,
            preview=preview_payload,
            platforms={
                "pinterest": {
                    "video_status": "published",
                    "video_pin_id": pin_response.get("id"),
                    "video_pin_url": pin_response.get("url"),
                    "use_affiliate_link": use_affiliate_link_flag,
                }
            },
        )
    except Exception as exc:
        app.logger.exception("Pinterest video publish failed")
        flash(f"Unable to publish video to Pinterest: {exc}", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    return redirect_home_view()


@app.route("/generate-instagram-image", methods=["POST"])
def generate_instagram_image():
    raw_form_values = collect_form_values(request.form)
    form_values = extract_form_defaults(raw_form_values)
    form_values["image_generation_model"] = normalize_image_generation_model(
        raw_form_values.get("image_generation_model", form_values.get("image_generation_model", ""))
    )
    use_affiliate_link_flag = str(form_values.get("use_affiliate_link", "0")).lower() in TRUTHY_VALUES
    product_id = resolve_product_id(form_values)
    preview_payload = rebuild_preview_payload(raw_form_values)
    saved_state = get_product_state(product_id) if product_id else {}
    base_image_path = raw_form_values.get("original_image_path") or raw_form_values.get(
        "generated_image_path"
    )

    base_bytes = None
    selected_sources: List[tuple[str, Optional[bytes]]] = []
    if raw_form_values.get("selected_original_image") or raw_form_values.get("selected_original_images"):
        try:
            selected_sources = resolve_selected_original_images(
                raw_form_values, form_values, product_id, preview_payload
            )
        except Exception as exc:
            app.logger.exception("Selected image download failed")
            flash(f"Unable to download the selected image: {exc}", "error")
            return render_home_view(form_values, preview_payload, product_id=product_id)
        if selected_sources:
            base_image_path, base_bytes = selected_sources[0]
    if not base_image_path:
        try:
            downloaded = ensure_downloaded_original_image(
                product_id, form_values, saved_state, preview_payload
            )
        except Exception as exc:
            app.logger.exception("Source image download failed")
            flash(f"Unable to download the product image: {exc}", "error")
            return render_home_view(form_values, preview_payload, product_id=product_id)
        if downloaded:
            base_image_path, base_bytes = downloaded
        else:
            flash("Upload a product image before generating Instagram visuals.", "error")
            return render_home_view(form_values, preview_payload, product_id=product_id)

    if base_bytes is None:
        try:
            base_bytes = load_stored_media(base_image_path)
        except Exception:
            flash("Unable to load the base image. Please regenerate your creative first.", "error")
            return render_home_view(form_values, preview_payload, product_id=product_id)
    selected_reference_images = load_resolved_reference_images(
        selected_sources,
        primary_image_path=base_image_path,
    )

    variant = raw_form_values.get("instagram_variant", "feed").lower()
    aspect_ratio = "4:5" if variant == "feed" else "9:16"
    variant_label = "story" if variant == "story" else "feed"
    context_prompt = build_prompt_context({**form_values, "title": raw_form_values.get("title", "")})
    inst_prompt = (
        "Design a high-performing Instagram {variant} visual with trending color grading, "
        "dynamic lighting, and a magnetic focus on the hero product. "
        "Include a short, tasteful CTA on the image (e.g., \"Shop now\", \"Tap to learn more\", \"Limited drop\"). "
        "Do not include any URLs or affiliate links in the text."
    ).format(variant=variant_label)
    boost_prompt = (
        raw_form_values.get("instagram_boost_prompt")
        or (preview_payload.get("instagram_boost_prompt") if preview_payload else "")
        or _get_instagram_state(saved_state).get("instagram_boost_prompt", "")
    )
    if boost_prompt:
        inst_prompt = (
            f"{inst_prompt}\n\nAdditional creator guidance (safe content details): {boost_prompt.strip()}"
        )

    try:
        current_saved_state = get_product_state(product_id) if product_id else saved_state
        instagram_image = generate_platform_image_with_selected_model(
            base_bytes,
            saved_state=current_saved_state,
            primary_image_path=base_image_path,
            image_generation_model=form_values.get("image_generation_model", ""),
            context=context_prompt,
            prompt=inst_prompt,
            aspect_ratio=aspect_ratio,
            reference_images=(selected_reference_images if selected_sources else None),
        )
        target_dimensions = (1080, 1350) if variant_label == "feed" else (1080, 1920)
        instagram_image = ensure_dimensions(instagram_image, target_dimensions)
    except Exception as exc:
        flash(f"Unable to generate the Instagram visual: {exc}", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    instagram_image_path = save_generated_image(instagram_image)
    preview_payload = preview_payload or {}
    preview_payload["instagram_image_path"] = instagram_image_path
    preview_payload["instagram_image_url"] = url_for("serve_media", filename=instagram_image_path)
    preview_payload["instagram_image_public_url"] = url_for(
        "serve_media", filename=instagram_image_path, _external=True
    )
    preview_payload["instagram_image_data"] = base64.b64encode(instagram_image).decode("utf-8")

    flash(f"Instagram {variant_label} visual refreshed.", "success")
    platform_name = f"instagram_{variant_label}"
    update_product_state(
        product_id,
        form_values=form_values,
        preview=preview_payload,
        platforms={
            platform_name: {
                "status": "pending",
                "image_path": instagram_image_path,
                "variant": variant_label,
                "use_affiliate_link": use_affiliate_link_flag,
            }
        },
        assets={"instagram_image_path": instagram_image_path},
    )
    return redirect_home_view()


@app.route("/publish-instagram", methods=["POST"])
def publish_instagram():
    raw_form_values = collect_form_values(request.form)
    form_values = extract_form_defaults(raw_form_values)
    use_affiliate_link_flag = str(form_values.get("use_affiliate_link", "0")).lower() in TRUTHY_VALUES
    product_id = resolve_product_id(form_values)
    preview_payload = rebuild_preview_payload(raw_form_values)
    generated_image_path = raw_form_values.get("instagram_image_path") or raw_form_values.get(
        "generated_image_path"
    )

    if not generated_image_path:
        flash("Missing generated creative. Please run the generator first.", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    caption = raw_form_values.get("instagram_caption", "")
    caption_with_comment_cta = _prepend_instagram_comment_reply_caption_cta(caption)
    hashtags = parse_tags_payload(raw_form_values.get("instagram_hashtags_payload", "[]"))
    normalized_tags = [tag.lstrip("#").replace(" ", "") for tag in hashtags if tag]
    if normalized_tags:
        tags_block = " ".join(f"#{tag}" for tag in normalized_tags)
        caption_to_publish = caption_with_comment_cta.strip()
        caption_to_publish = (
            f"{caption_to_publish}\n\n{tags_block}" if caption_to_publish else tags_block
        )
    else:
        caption_to_publish = caption_with_comment_cta.strip()

    target = raw_form_values.get("target", "feed")
    try:
        media_url = url_for("serve_media", filename=generated_image_path, _external=True)
        if target == "story":
            response = publish_instagram_story(image_url=media_url, caption=caption.strip())
        else:
            response = publish_instagram_post(image_url=media_url, caption=caption_to_publish)
        media_id = response.get("id") or get_latest_instagram_media_id()
        flash("Instagram content published successfully!", "success")
        platform_name = f"instagram_{target.lower()}"
        update_product_state(
            product_id,
            form_values=form_values,
            preview=preview_payload,
            platforms={
                platform_name: {
                    "status": "published",
                    "use_affiliate_link": use_affiliate_link_flag,
                    "instagram_media_id": media_id,
                    "affiliate_link": form_values.get("affiliate_link"),
                }
            },
        )
        if target == "story" and media_id:
            _register_instagram_media_reply_route(
                str(media_id),
                _build_instagram_reply_candidate(
                    product_id=product_id,
                    form_values=form_values,
                    preview_payload=preview_payload,
                ),
                reply_surface="story",
            )
        elif target == "feed" and media_id:
            _register_instagram_media_reply_route(
                str(media_id),
                _build_instagram_reply_candidate(
                    product_id=product_id,
                    form_values=form_values,
                    preview_payload=preview_payload,
                ),
                reply_surface="feed",
            )
    except Exception as exc:
        app.logger.exception("Instagram publish failed")
        flash(f"Unable to publish to Instagram: {exc}", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    return redirect_home_view()


@app.route("/webhooks/instagram", methods=["GET"])
def verify_instagram_webhook():
    mode = request.args.get("hub.mode", "")
    token = request.args.get("hub.verify_token", "")
    challenge = request.args.get("hub.challenge", "")
    if mode == "subscribe" and token and token == INSTAGRAM_WEBHOOK_VERIFY_TOKEN:
        return challenge, 200
    abort(403)


@app.route("/webhooks/instagram", methods=["POST"])
def receive_instagram_webhook():
    payload = request.get_json(silent=True) or {}
    entry_count = len(payload.get("entry", [])) if isinstance(payload.get("entry", []), list) else 0
    app.logger.warning(
        "Instagram webhook POST received: top_level_keys=%s object=%s entry_count=%s",
        sorted(payload.keys()) if isinstance(payload, dict) else [],
        payload.get("object") if isinstance(payload, dict) else "",
        entry_count,
    )
    app.logger.warning("Instagram webhook payload: %s", payload)
    for entry in payload.get("entry", []):
        app.logger.warning(
            "Instagram webhook entry: entry_keys=%s messaging_count=%s changes_count=%s",
            sorted(entry.keys()) if isinstance(entry, dict) else [],
            len(entry.get("messaging", [])) if isinstance(entry.get("messaging", []), list) else 0,
            len(entry.get("changes", [])) if isinstance(entry.get("changes", []), list) else 0,
        )
        for event in entry.get("messaging", []):
            try:
                _handle_instagram_messaging_event(event)
            except Exception:
                app.logger.exception("Instagram messaging webhook handler failed.")
        comment_changes: List[Dict[str, Any]] = []
        if entry.get("field") in {"comments", "live_comments"} and isinstance(entry.get("value"), dict):
            comment_changes.append(
                {
                    "field": entry.get("field"),
                    "value": entry.get("value"),
                }
            )
        for change in entry.get("changes", []):
            if isinstance(change, dict):
                comment_changes.append(change)
        for change in comment_changes:
            app.logger.warning(
                "Instagram webhook change received: field=%s value_keys=%s",
                change.get("field", "") if isinstance(change, dict) else "",
                sorted((change.get("value") or {}).keys()) if isinstance(change, dict) else [],
            )
            if change.get("field") not in {"comments", "live_comments"}:
                continue
            try:
                _handle_instagram_comment_change(change)
            except Exception:
                app.logger.exception("Instagram comment webhook handler failed.")
    return {"status": "ok"}, 200


@app.route("/publish-instagram-reel", methods=["POST"])
def publish_instagram_reel_route():
    raw_form_values = collect_form_values(request.form)
    form_values = extract_form_defaults(raw_form_values)
    use_affiliate_link_flag = str(form_values.get("use_affiliate_link", "0")).lower() in TRUTHY_VALUES
    product_id = resolve_product_id(form_values)
    preview_payload = rebuild_preview_payload(raw_form_values)
    video_path = raw_form_values.get("generated_video_path")

    if not video_path:
        flash("Generate the Instagram Reel video first, then publish.", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    caption = _prepend_instagram_comment_reply_caption_cta(
        raw_form_values.get("instagram_caption", "")
    )
    hashtags = parse_tags_payload(raw_form_values.get("instagram_hashtags_payload", "[]"))
    normalized_tags = [tag.lstrip("#").replace(" ", "") for tag in hashtags if tag]
    if normalized_tags:
        tags_block = " ".join(f"#{tag}" for tag in normalized_tags)
        caption_to_publish = caption.strip()
        caption_to_publish = (
            f"{caption_to_publish}\n\n{tags_block}" if caption_to_publish else tags_block
        )
    else:
        caption_to_publish = caption.strip()

    try:
        media_url = url_for("serve_media", filename=video_path, _external=True)
        response = publish_instagram_reel(video_url=media_url, caption=caption_to_publish)
        media_id = response.get("id") or get_latest_instagram_media_id()
        flash("Instagram Reel published successfully!", "success")
        update_product_state(
            product_id,
            form_values=form_values,
            preview=preview_payload,
            platforms={
                "instagram_reel": {
                    "status": "published",
                    "use_affiliate_link": use_affiliate_link_flag,
                    "instagram_media_id": media_id,
                    "affiliate_link": form_values.get("affiliate_link"),
                }
            },
        )
        if media_id:
            _register_instagram_media_reply_route(
                str(media_id),
                _build_instagram_reply_candidate(
                    product_id=product_id,
                    form_values=form_values,
                    preview_payload=preview_payload,
                ),
                reply_surface="reel",
            )
    except Exception as exc:
        app.logger.exception("Instagram Reel publish failed")
        flash(f"Unable to publish the Instagram Reel: {exc}", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    return redirect_home_view()


@app.route("/publish-website", methods=["POST"])
def publish_website():
    raw_form_values = collect_form_values(request.form)
    form_values = extract_form_defaults(raw_form_values)
    product_id = resolve_product_id(form_values)
    preview_payload = rebuild_preview_payload(raw_form_values)
    saved_state = get_product_state(product_id) if product_id else {}
    if not product_id:
        state = load_app_state()
        fallback_id = state.get("last_product_id") or ""
        if fallback_id:
            product_id = fallback_id
            saved_state = state.get("products", {}).get(product_id, {})
            fallback_values = _build_form_values_from_platforms(saved_state)
            for key, value in fallback_values.items():
                if not form_values.get(key):
                    form_values[key] = value

    if not preview_payload and saved_state:
        preview_payload = _build_preview_from_platforms(saved_state)

    if not product_id:
        flash("Provide a SKU or product link before publishing to Kaymio.", "error")
        return render_home_view(form_values, preview_payload)

    affiliate_link = form_values.get("affiliate_link") or raw_form_values.get("affiliate_link")
    price = form_values.get("price") or raw_form_values.get("price")
    category_name = form_values.get("category") or raw_form_values.get("category")
    boost_prompt = form_values.get("website_boost_prompt") or raw_form_values.get("website_boost_prompt", "")
    if not affiliate_link:
        flash("Affiliate link is required for WooCommerce external products.", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)
    if not price:
        flash("Add a price before publishing to Kaymio.", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    def _resolve_image_path():
        if preview_payload:
            path = preview_payload.get("original_image_path") or preview_payload.get("generated_image_path")
            if path:
                return path
        direct = raw_form_values.get("original_image_path") or form_values.get("original_image_path")
        if direct:
            return direct
        assets = (saved_state.get("assets") or {}) if saved_state else {}
        return assets.get("original_image_path") or assets.get("generated_image_path")

    image_relative = _resolve_image_path()
    image_paths = resolve_original_image_paths(saved_state, image_relative)
    source_image_urls = resolve_source_image_urls(saved_state)
    if source_image_urls:
        try:
            image_paths = ensure_downloaded_original_images(
                product_id,
                form_values,
                saved_state,
                preview_payload,
                preferred_primary_path=image_relative,
            )
        except Exception as exc:
            app.logger.exception("Source image download failed")
            flash(f"Unable to download product images: {exc}", "error")
        image_paths = [path for path in image_paths if resolve_storage_path(path)]
    elif not image_paths:
        try:
            image_paths = ensure_downloaded_original_images(
                product_id,
                form_values,
                saved_state,
                preview_payload,
                preferred_primary_path=image_relative,
            )
        except Exception as exc:
            app.logger.exception("Source image download failed")
            flash(f"Unable to download product images: {exc}", "error")
            image_paths = []
        if image_paths:
            image_paths = [path for path in image_paths if resolve_storage_path(path)]
    if not image_paths:
        flash("Upload an original product image before publishing to Kaymio.", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)
    image_relative = image_paths[0]
    resolved_image_path = resolve_storage_path(image_relative)
    additional_image_paths = []
    for path in image_paths[1:]:
        resolved = resolve_storage_path(path)
        if resolved:
            additional_image_paths.append(resolved)

    if not preview_payload:
        preview_payload = {
            "title": form_values.get("title", ""),
            "description": form_values.get("description", ""),
            "tags": parse_tags_payload(form_values.get("tags", "[]")),
            "original_image_path": image_relative,
            "generated_image_path": "",
            "category": form_values.get("category", ""),
            "price": form_values.get("price", ""),
            "website_boost_prompt": form_values.get("website_boost_prompt", ""),
        }

    title = preview_payload.get("title") or form_values.get("title")
    description = preview_payload.get("description") or form_values.get("description", "")
    if not title or not description:
        flash("Missing product title or description. Please provide them before publishing.", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    enriched_description = build_website_description(title, description, boost_prompt or "")
    tags = preview_payload.get("tags") or []
    category_id = None
    if category_name:
        try:
            category_id = find_wordpress_nearest_category(category_name)
        except Exception as exc:
            app.logger.warning("Failed to resolve WordPress category '%s': %s", category_name, exc)
            category_id = None

    try:
        product_url = create_woocommerce_product(
            name=title,
            description=enriched_description,
            price=price,
            image_path=resolved_image_path,
            tags=tags,
            affiliate_link=affiliate_link,
            category_id=category_id,
            images=additional_image_paths,
        )
    except Exception as exc:
        app.logger.exception("WooCommerce publish failed")
        flash(f"Unable to publish to Kaymio: {exc}", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    if not product_url:
        flash("Failed to publish product to Kaymio. Check WooCommerce credentials.", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    flash("Product published to Kaymio successfully!", "success")
    website_result = {
        "product_url": product_url,
        "title": title,
        "description": enriched_description,
        "price": price,
        "category": category_name or "",
    }
    update_product_state(
        product_id,
        form_values=form_values,
        preview=preview_payload,
        platforms={
            "website": {
                "status": "published",
                "product_url": product_url,
            }
        },
        results={"website": website_result},
    )
    return redirect_home_view()


@app.route("/generate-video/<platform>", methods=["POST"])
def generate_platform_video(platform: str):
    supported = {"youtube", "tiktok", "instagram", "pinterest"}
    target = platform.lower()
    if target not in supported:
        abort(404)

    raw_form_values = collect_form_values(request.form)
    form_values = extract_form_defaults(raw_form_values)
    form_values["video_generation_model"] = normalize_video_generation_model(
        raw_form_values.get("video_generation_model", form_values.get("video_generation_model", ""))
    )
    use_affiliate_link_flag = str(form_values.get("use_affiliate_link", "0")).lower() in TRUTHY_VALUES
    product_id = resolve_product_id(form_values)
    preview_payload = rebuild_preview_payload(raw_form_values)
    saved_state = get_product_state(product_id) if product_id else {}
    if product_id:
        active_video_state = get_platform_state(saved_state, _video_platform_state_key(target))
        if active_video_state.get("generation_status") == "processing":
            flash("A video generation job is already running for this platform. Refresh in a moment.", "info")
            return render_home_view(form_values, preview_payload, product_id=product_id)
    base_image_path = (
        raw_form_values.get("original_image_path")
        or raw_form_values.get("instagram_image_path")
        or raw_form_values.get("generated_image_path")
    )

    base_bytes = None
    selected_sources: List[tuple[str, Optional[bytes]]] = []
    if raw_form_values.get("selected_original_image") or raw_form_values.get("selected_original_images"):
        try:
            selected_sources = resolve_selected_original_images(
                raw_form_values, form_values, product_id, preview_payload
            )
        except Exception as exc:
            app.logger.exception("Selected image download failed")
            flash(f"Unable to download the selected image: {exc}", "error")
            return render_home_view(form_values, preview_payload, product_id=product_id)
        if selected_sources:
            base_image_path, base_bytes = selected_sources[0]
    if not base_image_path:
        try:
            downloaded = ensure_downloaded_original_image(
                product_id, form_values, saved_state, preview_payload
            )
        except Exception as exc:
            app.logger.exception("Source image download failed")
            flash(f"Unable to download the product image: {exc}", "error")
            return render_home_view(form_values, preview_payload, product_id=product_id)
        if downloaded:
            base_image_path, base_bytes = downloaded
        else:
            flash("Generate an image first to feed the video workflow.", "error")
            return render_home_view(form_values, preview_payload, product_id=product_id)

    if base_bytes is None:
        try:
            base_bytes = load_stored_media(base_image_path)
        except Exception:
            flash("Unable to load the base visual. Please regenerate it.", "error")
            return render_home_view(form_values, preview_payload, product_id=product_id)
    selected_reference_images = load_resolved_reference_images(
        selected_sources,
        primary_image_path=base_image_path,
    )

    title = raw_form_values.get("title") or form_values.get("title") or "this product"
    product_context = title
    category = form_values.get("category") or raw_form_values.get("category")
    price = form_values.get("price") or raw_form_values.get("price")
    if category or price:
        details = ", ".join([item for item in [category, price] if item])
        product_context = f"{title} ({details})"

    hook_style_raw = (
        raw_form_values.get("video_hook_style")
        or (preview_payload.get("video_hook_style") if preview_payload else "")
        or get_platform_state(saved_state, target if target != "instagram" else "instagram_reel").get(
            "video_hook_style", ""
        )
    )
    hook_styles = {
        "curiosity": "Open with a surprising detail that makes viewers ask 'why is that happening?'",
        "problem": "Open by showing a common pain point or frustration the product solves.",
        "comparison": "Open with a quick side-by-side contrast (before/after or old vs new).",
        "transformation": "Open with an instant transformation moment (messy -> clean, dull -> shiny).",
        "demo": "Open by immediately demonstrating the core feature in action.",
        "aesthetic": "Open with a beautiful, cinematic hero shot that feels premium and real.",
    }
    hook_style = hook_style_raw.strip().lower() if hook_style_raw else "auto"
    if hook_style not in hook_styles:
        hook_style = "auto"
    if hook_style == "auto":
        hook_style = random.choice(list(hook_styles.keys()))
    hook_note = hook_styles[hook_style]
    form_values["video_hook_style"] = hook_style

    camera_moves = ["push-in", "whip-pan", "macro detail", "over-shoulder", "handheld orbit", "top-down"]
    settings = [
        "bright kitchen counter",
        "cozy bedroom corner",
        "natural window light desk",
        "street-style outdoor scene",
        "clean studio tabletop",
        "bathroom vanity",
    ]
    lighting = ["soft daylight", "warm tungsten", "high-contrast dramatic", "even diffused"]
    cut_styles = [
        "6-9 quick cuts",
        "5-7 fast cuts with a speed ramp",
        "7-10 cuts with a slow-motion payoff",
    ]
    move_list = ", ".join(random.sample(camera_moves, k=3))
    setting_choice = random.choice(settings)
    lighting_choice = random.choice(lighting)
    cut_style = random.choice(cut_styles)

    prompt_templates = {
        "pinterest": (
            "Create a Pinterest-optimized vertical product video for {product}. Hook: {hook}. "
            "Lead with an eye-catching opening shot, then show clear lifestyle use-cases and close-up details. "
            "Use {cut_style} and varied camera moves: {moves}. Setting: {setting}. Lighting: {lighting}. "
            "No on-screen text, no logos, no voiceover, no speech. Choose suitable music only."
        ),
        "youtube": (
            "Create a vertical YouTube Short for {product}. Make it feel like authentic UGC, not an ad. "
            "Hook: {hook}. Setting: {setting}. Lighting: {lighting}. "
            "Show a clear problem -> use -> satisfying result. Use {cut_style} and vary camera moves: {moves}. "
            "No on-screen text, no logos, no voiceover, no speech. Choose suitable music only."
        ),
        "tiktok": (
            "Create a TikTok-ready vertical video for {product}. Hook: {hook}. "
            "Use {cut_style}, tactile close-ups, and fast angle changes with camera moves: {moves}. "
            "Include a quick before/after or transformation moment. Setting: {setting}. Lighting: {lighting}. "
            "Keep it clean with no text or overlays. No voiceover or speech; choose suitable music only."
        ),
        "instagram": (
            "Create an Instagram Reels-ready vertical video for {product}. Hook: {hook}. "
            "Lead with a thumb-stopping opening frame, then show close-ups, usage, and lifestyle context. "
            "Use {cut_style} and varied camera moves: {moves}. Setting: {setting}. Lighting: {lighting}. "
            "No on-screen text or logos. No voiceover or speech; choose suitable music only."
        ),
    }
    prompt = prompt_templates[target].format(
        product=product_context,
        hook=hook_note,
        setting=setting_choice,
        lighting=lighting_choice,
        cut_style=cut_style,
        moves=move_list,
    )
    video_context_prompt = build_prompt_context({**form_values, "title": raw_form_values.get("title", "")})
    boost_prompt = ""
    if target == "youtube":
        boost_prompt = (
            raw_form_values.get("youtube_boost_prompt")
            or (preview_payload.get("youtube_boost_prompt") if preview_payload else "")
            or get_platform_state(saved_state, "youtube").get("youtube_boost_prompt", "")
        )
        form_values["youtube_boost_prompt"] = boost_prompt
    elif target == "pinterest":
        boost_prompt = (
            raw_form_values.get("pinterest_extra")
            or (preview_payload.get("pinterest_extra") if preview_payload else "")
            or get_platform_state(saved_state, "pinterest").get("pinterest_extra", "")
        )
        form_values["pinterest_extra"] = boost_prompt
    elif target == "instagram":
        boost_prompt = (
            raw_form_values.get("instagram_boost_prompt")
            or (preview_payload.get("instagram_boost_prompt") if preview_payload else "")
            or _get_instagram_state(saved_state).get("instagram_boost_prompt", "")
        )
        form_values["instagram_boost_prompt"] = boost_prompt
    if boost_prompt:
        prompt = (
            f"{prompt}\n\nAdditional creator guidance (safe content details): {boost_prompt.strip()}"
        )

    duration_value = (
        raw_form_values.get("video_duration_seconds")
        or (preview_payload.get("video_duration_seconds") if preview_payload else None)
        or str(VIDEO_DURATION_DEFAULT)
    )
    try:
        duration_seconds = int(float(duration_value))
    except (TypeError, ValueError):
        duration_seconds = VIDEO_DURATION_DEFAULT
    duration_seconds = max(VIDEO_DURATION_MIN, min(VIDEO_DURATION_MAX, duration_seconds))
    video_provider, resolved_video_model = resolve_video_generation_choice(
        form_values.get("video_generation_model", "")
    )
    if video_provider == "openai":
        normalized_openai_seconds = normalize_openai_video_seconds(duration_seconds)
        if normalized_openai_seconds != duration_seconds:
            duration_seconds = normalized_openai_seconds
            flash(
                f"OpenAI video generation supports 4, 8, or 12 seconds. Kaymio used {duration_seconds} seconds.",
                "info",
            )
    if (
        video_provider == "gemini"
        and selected_reference_images
        and resolved_video_model == "veo-3.1-generate-preview"
        and duration_seconds != 8
    ):
        duration_seconds = 8
        flash(
            "Gemini Veo multi-image reference mode currently generates 8-second clips, so Kaymio used 8 seconds.",
            "info",
        )

    fitted_base_bytes = ensure_dimensions(base_bytes, (720, 1280))
    preview_payload = preview_payload or {}
    if video_provider == "openai" and product_id:
        _start_openai_video_generation_job(
            target=target,
            product_id=product_id,
            form_values=form_values,
            preview_payload=preview_payload,
            base_image_bytes=fitted_base_bytes,
            reference_images=selected_reference_images,
            prompt=prompt,
            context=video_context_prompt,
            duration_seconds=duration_seconds,
            base_image_path=base_image_path,
            boost_prompt=boost_prompt,
            use_affiliate_link_flag=use_affiliate_link_flag,
        )
        flash(
            f"{('Instagram Reel' if target == 'instagram' else target.title())} video generation started. Refresh in a moment to see the result.",
            "info",
        )
        return redirect_home_view()

    try:
        video_bytes = generate_platform_video_with_selected_model(
            fitted_base_bytes,
            prompt=prompt,
            video_generation_model=form_values.get("video_generation_model", ""),
            context=video_context_prompt,
            duration_seconds=duration_seconds,
            aspect_ratio="9:16",
            resolution="720p",
            reference_images=selected_reference_images,
        )
    except Exception as exc:
        flash(f"Unable to generate the {target} video: {exc}", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    video_path = save_generated_video(video_bytes)
    target_label = "Instagram" if target == "instagram" else target.title()
    platform_key = "instagram_reel" if target == "instagram" else target
    flash(f"{target_label} video generated.", "success")
    _complete_video_generation_state(
        target=target,
        product_id=product_id,
        form_values=form_values,
        preview_payload=preview_payload,
        video_path=video_path,
        duration_seconds=duration_seconds,
        boost_prompt=boost_prompt,
        base_image_path=base_image_path,
        use_affiliate_link_flag=use_affiliate_link_flag,
    )
    return redirect_home_view()


@app.route("/upload-video/<platform>", methods=["POST"])
def upload_platform_video(platform: str):
    supported = {"youtube", "tiktok", "instagram", "pinterest"}
    target = platform.lower()
    if target not in supported:
        abort(404)

    raw_form_values = collect_form_values(request.form)
    form_values = extract_form_defaults(raw_form_values)
    use_affiliate_link_flag = str(form_values.get("use_affiliate_link", "0")).lower() in TRUTHY_VALUES
    product_id = resolve_product_id(form_values)
    preview_payload = rebuild_preview_payload(raw_form_values)
    upload = request.files.get("external_video")

    if not upload or not upload.filename:
        flash("Choose a video file to upload first.", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)
    if not allowed_video_file(upload.filename):
        flash("Unsupported video format. Upload MP4, MOV, M4V, or WEBM.", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    video_bytes = upload.read()
    if not video_bytes:
        flash("Uploaded video was empty.", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    video_path = save_uploaded_video(video_bytes, upload.filename)
    preview_payload = preview_payload or {}
    preview_payload["generated_video_path"] = video_path
    preview_payload["video_url"] = url_for("serve_media", filename=video_path)
    preview_payload["video_download_url"] = url_for("download_media", filename=video_path)
    preview_payload["video_public_url"] = url_for("serve_media", filename=video_path, _external=True)

    target_label = "Instagram" if target == "instagram" else target.title()
    platform_key = "instagram_reel" if target == "instagram" else target
    platform_payload = {
        "status": "pending",
        "video_path": video_path,
        "use_affiliate_link": use_affiliate_link_flag,
        "video_source": "upload",
    }
    if target == "pinterest":
        platform_payload.pop("status", None)
        platform_payload["video_status"] = "pending"

    flash(f"{target_label} video uploaded.", "success")
    update_product_state(
        product_id,
        form_values=form_values,
        preview=preview_payload,
        platforms={platform_key: platform_payload},
        assets={"generated_video_path": video_path},
    )
    return redirect_home_view()


@app.route("/publish-youtube", methods=["POST"])
def publish_youtube():
    raw_form_values = collect_form_values(request.form)
    form_values = extract_form_defaults(raw_form_values)
    use_affiliate_link_flag = str(form_values.get("use_affiliate_link", "0")).lower() in TRUTHY_VALUES
    product_id = resolve_product_id(form_values)
    preview_payload = rebuild_preview_payload(raw_form_values)
    video_path = raw_form_values.get("generated_video_path")

    if not video_path:
        flash("Generate the YouTube Short first, then publish.", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    try:
        video_bytes = load_stored_media(video_path)
    except Exception:
        flash("Unable to load the generated video. Please regenerate it.", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    base_title = raw_form_values.get("title") or form_values.get("title", "")
    base_description = raw_form_values.get("description") or form_values.get("description", "")
    title = raw_form_values.get("youtube_title") or base_title
    description = raw_form_values.get("youtube_description") or base_description
    keywords_payload_raw = raw_form_values.get("youtube_keywords_payload", "[]")
    keywords = parse_tags_payload(keywords_payload_raw)

    needs_metadata = not title or not description or not keywords
    if needs_metadata:
        metadata = generate_youtube_metadata(base_title, base_description)
        title = title or metadata.get("title", "")
        description = description or metadata.get("description", "")
        if not keywords:
            keywords = metadata.get("keywords", [])
        if preview_payload:
            preview_payload["youtube_title"] = title
            preview_payload["youtube_description"] = description
            preview_payload["youtube_keywords"] = keywords
            preview_payload["youtube_keywords_payload"] = json.dumps(keywords or [])

    try:
        response = publish_short_video(
            video_bytes,
            title=title,
            description=description,
            tags=keywords,
            privacy_status=raw_form_values.get("privacy_status", "public"),
        )
        flash(
            f"YouTube Short uploaded (video url: {response.get('url', 'n/a')}).",
            "success",
        )
        update_product_state(
            product_id,
            form_values=form_values,
            preview=preview_payload,
            platforms={
                "youtube": {
                    "status": "published",
                    "video_id": response.get("video_id"),
                    "use_affiliate_link": use_affiliate_link_flag,
                }
            },
            results={
                "youtube": {
                    "title": title,
                    "description": description,
                    "video_id": response.get("video_id"),
                }
            },
        )
    except Exception as exc:
        app.logger.exception("YouTube publish failed")
        flash(f"Unable to publish to YouTube: {exc}", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    return redirect_home_view()


@app.route("/publish-tiktok", methods=["POST"])
def publish_tiktok():
    raw_form_values = collect_form_values(request.form)
    form_values = extract_form_defaults(raw_form_values)
    use_affiliate_link_flag = str(form_values.get("use_affiliate_link", "0")).lower() in TRUTHY_VALUES
    product_id = resolve_product_id(form_values)
    preview_payload = rebuild_preview_payload(raw_form_values)
    video_path = raw_form_values.get("generated_video_path")

    if not video_path:
        flash("Generate the TikTok video first, then publish.", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    try:
        video_bytes = load_stored_media(video_path)
    except Exception:
        flash("Unable to load the generated video. Please regenerate it.", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    caption = raw_form_values.get("tiktok_caption", "")
    hashtags = parse_tags_payload(raw_form_values.get("tiktok_hashtags_payload", "[]"))
    normalized_tags = [tag.lstrip("#").replace(" ", "") for tag in hashtags if tag]
    if normalized_tags:
        tags_block = " ".join(f"#{tag}" for tag in normalized_tags)
        caption_to_publish = (
            f"{caption} {tags_block}" if caption else tags_block
        ).strip()
    else:
        caption_to_publish = caption.strip()

    try:
        publish_tiktok_post(
            video_bytes,
            caption=caption_to_publish,
            privacy_level=raw_form_values.get("privacy_level", "PUBLIC"),
        )
        flash("TikTok video published successfully!", "success")
        update_product_state(
            product_id,
            form_values=form_values,
            preview=preview_payload,
            platforms={"tiktok": {"status": "published", "use_affiliate_link": use_affiliate_link_flag}},
            results={
                "tiktok": {
                    "caption": caption_to_publish,
                    "privacy_level": raw_form_values.get("privacy_level", "PUBLIC"),
                }
            },
        )
    except Exception as exc:
        app.logger.exception("TikTok publish failed")
        flash(f"Unable to publish to TikTok: {exc}", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    return redirect_home_view()


if __name__ == "__main__":
    debug_enabled = os.getenv("FLASK_DEBUG", "0") in TRUTHY_VALUES
    init_db()
    if not debug_enabled or os.getenv("WERKZEUG_RUN_MAIN") == "true":
        start_instagram_story_scheduler()
    app.run(debug=debug_enabled, host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
    # update_affiliate_links()
