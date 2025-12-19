import base64
import json
import os
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from dotenv import load_dotenv
from flask import Flask, abort, flash, render_template, request, send_from_directory, url_for

from gemeni_api_helper import edit_image, generate_video_from_image
from instagram.instagram_api_helper import publish_instagram_post, publish_instagram_story
from openai_helper import (
    generate_caption_for_instagram,
    generate_caption_for_tiktok,
    generate_hashtags_for_instagram,
    generate_hashtags_for_tiktok,
    generate_tags_for_product_for_pintrest,
    generate_text,
    generate_youtube_metadata,
)
from pintrest.pinterest_helper import create_pinterest_pin
from tiktok.tiktok_api_helper import publish_tiktok_post
from youtube.youtube_api_helper import publish_short_video
from kaymio.kaymio import create_woocommerce_product, find_wordpress_nearest_category
from PIL import Image, ImageOps

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20MB uploads
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
STORAGE_ROOT = Path(app.root_path) / "template_images"
ORIGINALS_DIR = STORAGE_ROOT / "originals"
GENERATED_DIR = STORAGE_ROOT / "generated"
VIDEOS_DIR = STORAGE_ROOT / "videos"
for directory in (ORIGINALS_DIR, GENERATED_DIR, VIDEOS_DIR):
    directory.mkdir(parents=True, exist_ok=True)
STATE_DIR = Path(app.root_path) / "Data"
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = STATE_DIR / "app_state.json"
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
VIDEO_DURATION_DEFAULT = 8
VIDEO_DURATION_MIN = 4
VIDEO_DURATION_MAX = 60


def _empty_app_state() -> Dict[str, Any]:
    return {"products": {}, "last_product_id": ""}


def _json_safe(data: Any) -> Any:
    if data is None:
        return None
    return json.loads(json.dumps(data))


def _sanitize_preview(preview: Dict[str, Any]) -> Dict[str, Any]:
    sanitized = {key: value for key, value in preview.items() if key not in PREVIEW_BINARY_FIELDS}
    return _json_safe(sanitized)


def _hydrate_preview(preview: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not preview:
        return preview
    hydrated = dict(preview)
    image_path = hydrated.get("generated_image_path")
    if image_path and not hydrated.get("image_data"):
        try:
            bytes_data = load_stored_media(image_path)
            hydrated["image_data"] = base64.b64encode(bytes_data).decode("utf-8")
            hydrated.setdefault("generated_image_url", url_for("serve_media", filename=image_path))
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
            hydrated.setdefault(
                "instagram_image_public_url", url_for("serve_media", filename=insta_path, _external=True)
            )
        except Exception:
            hydrated["instagram_image_data"] = ""
    video_path = hydrated.get("generated_video_path")
    if video_path:
        hydrated.setdefault("video_url", url_for("serve_media", filename=video_path))
        hydrated.setdefault(
            "video_public_url", url_for("serve_media", filename=video_path, _external=True)
        )
    return hydrated


def load_app_state() -> Dict[str, Any]:
    if not STATE_FILE.exists():
        return _empty_app_state()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return _empty_app_state()
    if not isinstance(data, dict):
        return _empty_app_state()
    data.setdefault("products", {})
    data.setdefault("last_product_id", "")
    return data


def save_app_state(state: Dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def normalize_product_id(raw_id: str) -> str:
    return (raw_id or "").strip()


def get_product_state(product_id: str) -> Dict[str, Any]:
    state = load_app_state()
    return state.get("products", {}).get(product_id, {})


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
    results = entry.get("results") or {}
    return results.get(platform)


def get_website_result(product_id: str) -> Optional[Dict[str, Any]]:
    return get_platform_result(product_id, "website")


def get_website_product_url(product_id: str) -> Optional[str]:
    website_result = get_website_result(product_id)
    if not website_result:
        return None
    return website_result.get("product_url")


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
    state = load_app_state()
    products = state.setdefault("products", {})
    entry = products.get(product_id, {"platforms": {}, "assets": {}, "results": {}})
    if form_values is not None:
        entry["form_values"] = _json_safe(form_values)
    if preview is not None:
        entry["preview"] = _sanitize_preview(preview)
    if assets:
        stored_assets = entry.get("assets", {})
        stored_assets.update(_json_safe(assets))
        entry["assets"] = stored_assets
    if results:
        stored_results = entry.get("results", {})
        stored_results.update(_json_safe(results))
        entry["results"] = stored_results
    if platforms:
        stored_platforms = entry.get("platforms", {})
        for platform_name, payload in platforms.items():
            snapshot = stored_platforms.get(platform_name, {})
            snapshot.update(_json_safe(payload))
            stored_platforms[platform_name] = snapshot
        entry["platforms"] = stored_platforms
    products[product_id] = entry
    state["last_product_id"] = product_id
    save_app_state(state)


def resolve_product_id(values: Dict[str, str]) -> str:
    return normalize_product_id(values.get("sku_or_url", ""))


def collect_form_values(form_data) -> Dict[str, str]:
    raw: Dict[str, str] = {}
    for key in form_data.keys():
        values = form_data.getlist(key)
        if not values:
            continue
        raw[key] = values[-1].strip()
    return raw


def render_home_view(
    form_values: Optional[Dict[str, str]],
    preview_payload: Optional[Dict[str, Any]] = None,
    pinterest_result: Optional[Dict[str, Any]] = None,
    *,
    product_id: str = "",
    website_result: Optional[Dict[str, Any]] = None,
    platform_states: Optional[Dict[str, Any]] = None,
):
    state_entry: Optional[Dict[str, Any]] = None
    if product_id and (website_result is None or platform_states is None):
        state_entry = get_product_state(product_id)
    if website_result is None:
        if state_entry:
            website_result = (state_entry.get("results") or {}).get("website")
        elif product_id:
            website_result = get_website_result(product_id)
    if platform_states is None:
        if state_entry:
            platform_states = state_entry.get("platforms") or {}
        else:
            platform_states = {}
    return render_template(
        "home.html",
        markets=MARKET_OPTIONS,
        form_values=form_values or {},
        preview=preview_payload,
        result=pinterest_result,
        website_result=website_result,
        platform_states=platform_states or {},
    )


def build_render_payload(entry: Dict[str, Any]):
    if not entry:
        return {}, None, None
    form_values = entry.get("form_values") or {}
    preview_payload = entry.get("preview")
    if preview_payload:
        preview_payload = _hydrate_preview(preview_payload)
        assets_snapshot = entry.get("assets") or {}
        if assets_snapshot:
            stored_video_path = assets_snapshot.get("generated_video_path")
            if stored_video_path and not preview_payload.get("generated_video_path"):
                preview_payload["generated_video_path"] = stored_video_path
            if stored_video_path and not preview_payload.get("video_url"):
                preview_payload["video_url"] = url_for("serve_media", filename=stored_video_path)
            if stored_video_path and not preview_payload.get("video_public_url"):
                preview_payload["video_public_url"] = url_for(
                    "serve_media", filename=stored_video_path, _external=True
                )
    results = entry.get("results") or {}
    return form_values, preview_payload, results.get("pinterest")


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
        products.pop(product_id, None)
        if state.get("last_product_id") == product_id:
            state["last_product_id"] = ""
        save_app_state(state)
        return

    platforms = entry.setdefault("platforms", {})
    assets = entry.setdefault("assets", {})
    preview = entry.get("preview") or {}
    results = entry.get("results") or {}

    if platform == "instagram":
        for alias in ("instagram_feed", "instagram_story"):
            platforms.pop(alias, None)
        for field in (
            "instagram_caption",
            "instagram_hashtags",
            "instagram_hashtags_payload",
            "instagram_image_path",
            "instagram_image_url",
            "instagram_image_public_url",
            "instagram_image_data",
        ):
            preview.pop(field, None)
        assets.pop("instagram_image_path", None)
    elif platform == "youtube":
        platforms.pop("youtube", None)
        results.pop("youtube", None)
        for field in (
            "youtube_title",
            "youtube_description",
            "youtube_keywords",
            "youtube_keywords_payload",
        ):
            preview.pop(field, None)
    elif platform == "tiktok":
        platforms.pop("tiktok", None)
        results.pop("tiktok", None)
        for field in (
            "tiktok_caption",
            "tiktok_hashtags",
            "tiktok_hashtags_payload",
        ):
            preview.pop(field, None)

    entry["platforms"] = platforms
    entry["assets"] = assets
    entry["preview"] = preview
    entry["results"] = results
    products[product_id] = entry
    save_app_state(state)

def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _store_image(bytes_data: bytes, directory: Path, suffix: str) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}{suffix}"
    destination = directory / filename
    with open(destination, "wb") as file_handle:
        file_handle.write(bytes_data)
    return destination.relative_to(STORAGE_ROOT).as_posix()


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


def save_generated_image(bytes_data: bytes) -> str:
    return _store_image(bytes_data, GENERATED_DIR, ".png")


def save_generated_video(bytes_data: bytes) -> str:
    return _store_image(bytes_data, VIDEOS_DIR, ".mp4")


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
            return [str(item).strip() for item in loaded if str(item).strip()]
    except json.JSONDecodeError:
        pass
    return [segment.strip() for segment in raw_tags.split(",") if segment.strip()]


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
            "use_affiliate_link",
        )
    }
    defaults["website_boost_prompt"] = (
        raw_form_values.get("website_boost_prompt")
        or raw_form_values.get("website_boost_prompt_pref")
        or defaults.get("website_boost_prompt")
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
        "youtube_boost_prompt": raw_form_values.get("youtube_boost_prompt", ""),
        "use_affiliate_link": (
            raw_form_values.get("use_affiliate_link")
            or raw_form_values.get("use_affiliate_link_pref")
            or "0"
        ),
    }

    image_relative = raw_form_values.get("generated_image_path", "")
    if image_relative:
        payload["generated_image_url"] = url_for("serve_media", filename=image_relative)
        payload["image_public_url"] = url_for("serve_media", filename=image_relative, _external=True)
    
    video_path = raw_form_values.get("generated_video_path", "")
    payload["generated_video_path"] = video_path
    if video_path:
        payload["video_url"] = url_for("serve_media", filename=video_path)
        payload["video_public_url"] = url_for("serve_media", filename=video_path, _external=True)
    else:
        payload["video_url"] = ""
        payload["video_public_url"] = ""

    instagram_image_path = raw_form_values.get("instagram_image_path", "")
    if instagram_image_path:
        payload["instagram_image_path"] = instagram_image_path
        try:
            ig_bytes = load_stored_media(instagram_image_path)
            payload["instagram_image_url"] = url_for("serve_media", filename=instagram_image_path)
            payload["instagram_image_public_url"] = url_for(
                "serve_media", filename=instagram_image_path, _external=True
            )
            payload["instagram_image_data"] = base64.b64encode(ig_bytes).decode("utf-8")
        except Exception:
            payload["instagram_image_url"] = ""
            payload["instagram_image_public_url"] = ""
            payload["instagram_image_data"] = ""
    else:
        payload["instagram_image_url"] = ""
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


@app.route("/", methods=["GET"])
def home() -> str:
    state = load_app_state()
    last_product_id = state.get("last_product_id") or ""
    saved_state = state.get("products", {}).get(last_product_id, {})
    form_values, preview_payload, pinterest_result = build_render_payload(saved_state)
    website_result = (saved_state.get("results") or {}).get("website") if saved_state else None
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
    form_values = extract_form_defaults(raw_form_values)
    product_id = resolve_product_id(form_values)

    if not product_id:
        flash("Provide a SKU or product link before resetting a platform.", "error")
    elif not platform or platform not in RESETTABLE_PLATFORMS:
        flash("Choose a valid platform to reset.", "error")
    else:
        reset_product_platform(product_id, platform)
        flash(f"{platform.title()} state reset.", "info")

    saved_state = get_product_state(product_id) if product_id else {}
    form_values, preview_payload, pinterest_result = build_render_payload(saved_state)
    website_result = (saved_state.get("results") or {}).get("website") if saved_state else None
    return render_home_view(
        form_values,
        preview_payload,
        pinterest_result,
        product_id=product_id,
        website_result=website_result,
        platform_states=(saved_state.get("platforms") or {}),
    )


@app.route("/save-draft", methods=["POST"])
def save_draft():
    raw_form_values = collect_form_values(request.form)
    form_values = extract_form_defaults(raw_form_values)
    product_id = resolve_product_id(form_values)

    if not product_id:
        flash("Provide a SKU or product link before saving your progress.", "error")
        return render_home_view(form_values, product_id=product_id)

    image_file = request.files.get("product_image")
    assets = {}
    if image_file and image_file.filename:
        if not allowed_file(image_file.filename):
            flash("Unsupported image type. Use PNG, JPG, JPEG, GIF, or WEBP.", "error")
            return render_home_view(form_values, product_id=product_id)
        original_bytes = image_file.read()
        if not original_bytes:
            flash("The uploaded image appears to be empty.", "error")
            return render_home_view(form_values, product_id=product_id)
        original_image_path = save_original_image(original_bytes, image_file.filename)
        form_values["original_image_path"] = original_image_path
        raw_form_values["original_image_path"] = original_image_path
        assets["original_image_path"] = original_image_path

    preview_payload = rebuild_preview_payload(raw_form_values)
    update_product_state(
        product_id,
        form_values=form_values,
        preview=preview_payload,
        assets=assets,
    )
    flash("Draft saved. You can return later to continue.", "success")
    saved_state = get_product_state(product_id)
    form_values, preview_payload, pinterest_result = build_render_payload(saved_state)
    website_result = (saved_state.get("results") or {}).get("website") if saved_state else None
    return render_home_view(
        form_values,
        preview_payload,
        pinterest_result,
        product_id=product_id,
        website_result=website_result,
        platform_states=(saved_state.get("platforms") or {}),
    )


@app.route("/generate-pinterest", methods=["POST"])
def generate_pinterest():
    raw_form_values = collect_form_values(request.form)
    original_image_path = raw_form_values.pop("original_image_path", "")
    form_values = dict(raw_form_values)
    saved_state = get_product_state(resolve_product_id(form_values))
    if not original_image_path and saved_state:
        assets = saved_state.get("assets") or {}
        original_image_path = assets.get("original_image_path", "")
    form_values.setdefault("use_affiliate_link", "0")
    use_affiliate_link_flag = str(form_values.get("use_affiliate_link", "0")).lower() in TRUTHY_VALUES
    product_id = resolve_product_id(form_values)
    image_file = request.files.get("product_image")
    errors = []

    if not form_values.get("market"):
        errors.append("Please choose a marketplace before continuing.")

    if not form_values.get("sku_or_url"):
        errors.append("Please provide a SKU, ASIN, or product link.")

    if not form_values.get("title"):
        errors.append("Please provide a product title for Pinterest.")

    if not form_values.get("affiliate_link"):
        errors.append("Affiliate link is required so shoppers can reach the product.")

    if (image_file is None or image_file.filename == "") and not original_image_path:
        errors.append("Upload at least one product image so we can craft a pin.")
    elif image_file is not None and image_file.filename and not allowed_file(image_file.filename):
        errors.append("Unsupported image type. Use PNG, JPG, JPEG, GIF, or WEBP.")

    if errors:
        for message in errors:
            flash(message, "error")
        return render_home_view(form_values, product_id=product_id)

    if image_file is not None and image_file.filename:
        original_bytes = image_file.read()
        if not original_bytes:
            flash("The uploaded image appears to be empty.", "error")
            return render_home_view(form_values, product_id=product_id)
        original_image_path = save_original_image(original_bytes, image_file.filename)
    else:
        try:
            original_bytes = load_stored_image(original_image_path)
        except Exception:
            flash("Unable to load the previously uploaded image. Please upload again.", "error")
            return render_home_view(form_values, product_id=product_id)

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

        tags = generate_tags_for_product_for_pintrest(
            refined_title,
            generated_description,
        )
        instagram_caption = generate_caption_for_instagram(
            refined_title,
            generated_description,
            call_to_action=form_values.get("pinterest_extra"),
        )
        instagram_hashtags = generate_hashtags_for_instagram(refined_title, generated_description)
        tiktok_caption = generate_caption_for_tiktok(refined_title, generated_description)
        tiktok_hashtags = generate_hashtags_for_tiktok(refined_title, generated_description)
        youtube_metadata = generate_youtube_metadata(refined_title, generated_description)

        context_prompt = build_prompt_context({**form_values, "title": refined_title})
        generated_image = edit_image(
            original_bytes,
            context=context_prompt,
            prompt=(
                "Design a polished product visual with a clear hero focus, scroll-stopping composition, "
                "and cohesive lighting suitable for social platforms. Do not add any on-screen text—the output must be a pure image."
            ),
            aspect_ratio="2:3",
        )
        generated_image = ensure_dimensions(generated_image, (1000, 1500))
        generated_image_path = save_generated_image(generated_image)
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
            "generated_image_path": generated_image_path,
        },
    )
    return render_home_view(form_values, preview_payload, product_id=product_id)

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
    return render_home_view(form_values, pinterest_result=result, product_id=product_id)


@app.route("/generate-instagram-image", methods=["POST"])
def generate_instagram_image():
    raw_form_values = collect_form_values(request.form)
    form_values = extract_form_defaults(raw_form_values)
    use_affiliate_link_flag = str(form_values.get("use_affiliate_link", "0")).lower() in TRUTHY_VALUES
    product_id = resolve_product_id(form_values)
    preview_payload = rebuild_preview_payload(raw_form_values)
    base_image_path = raw_form_values.get("original_image_path") or raw_form_values.get(
        "generated_image_path"
    )

    if not base_image_path:
        flash("Upload a product image before generating Instagram visuals.", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    try:
        base_bytes = load_stored_media(base_image_path)
    except Exception:
        flash("Unable to load the base image. Please regenerate your creative first.", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    variant = raw_form_values.get("instagram_variant", "feed").lower()
    aspect_ratio = "4:5" if variant == "feed" else "9:16"
    variant_label = "story" if variant == "story" else "feed"
    context_prompt = build_prompt_context({**form_values, "title": raw_form_values.get("title", "")})
    inst_prompt = (
        "Design a high-performing Instagram {variant} visual with trending color grading, "
        "dynamic lighting, and a magnetic focus on the hero product, but do not add any on-screen text—the output must be a pure image."
    ).format(variant=variant_label)

    try:
        instagram_image = edit_image(
            base_bytes,
            context=context_prompt,
            prompt=inst_prompt,
            aspect_ratio=aspect_ratio,
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
    return render_home_view(form_values, preview_payload, product_id=product_id)


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

    target = raw_form_values.get("target", "feed")
    try:
        media_url = url_for("serve_media", filename=generated_image_path, _external=True)
        if target == "story":
            publish_instagram_story(image_url=media_url, caption=caption.strip())
        else:
            publish_instagram_post(image_url=media_url, caption=caption_to_publish)
        flash("Instagram content published successfully!", "success")
        platform_name = f"instagram_{target.lower()}"
        update_product_state(
            product_id,
            form_values=form_values,
            preview=preview_payload,
            platforms={platform_name: {"status": "published", "use_affiliate_link": use_affiliate_link_flag}},
        )
    except Exception as exc:
        app.logger.exception("Instagram publish failed")
        flash(f"Unable to publish to Instagram: {exc}", "error")

    return render_home_view(form_values, preview_payload, product_id=product_id)


@app.route("/publish-website", methods=["POST"])
def publish_website():
    raw_form_values = collect_form_values(request.form)
    form_values = extract_form_defaults(raw_form_values)
    product_id = resolve_product_id(form_values)
    preview_payload = rebuild_preview_payload(raw_form_values)
    saved_state = get_product_state(product_id) if product_id else {}

    if not preview_payload and saved_state.get("preview"):
        preview_payload = _hydrate_preview(saved_state.get("preview"))

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
    resolved_image_path = resolve_storage_path(image_relative) if image_relative else None
    if not resolved_image_path:
        flash("Upload an original product image before publishing to Kaymio.", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

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
    pinterest_result = get_platform_result(product_id, "pinterest")
    return render_home_view(
        form_values,
        preview_payload,
        pinterest_result=pinterest_result,
        product_id=product_id,
        website_result=website_result,
    )


@app.route("/generate-video/<platform>", methods=["POST"])
def generate_platform_video(platform: str):
    supported = {"youtube", "tiktok"}
    target = platform.lower()
    if target not in supported:
        abort(404)

    raw_form_values = collect_form_values(request.form)
    form_values = extract_form_defaults(raw_form_values)
    use_affiliate_link_flag = str(form_values.get("use_affiliate_link", "0")).lower() in TRUTHY_VALUES
    product_id = resolve_product_id(form_values)
    preview_payload = rebuild_preview_payload(raw_form_values)
    base_image_path = (
        raw_form_values.get("original_image_path")
        or raw_form_values.get("instagram_image_path")
        or raw_form_values.get("generated_image_path")
    )

    if not base_image_path:
        flash("Generate an image first to feed the video workflow.", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    try:
        base_bytes = load_stored_media(base_image_path)
    except Exception:
        flash("Unable to load the base visual. Please regenerate it.", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    title = raw_form_values.get("title") or form_values.get("title") or "this product"
    prompt_templates = {
        "youtube": (
            "Create a vertical YouTube Short for '{title}' with upbeat pacing, dynamic camera moves, "
            "and a CTA to tap the affiliate link, but do not add any on-screen text—the output must be pure video."
        ),
        "tiktok": (
            "Create a TikTok-ready vertical video for '{title}' using trendy motion graphics, quick cuts, "
            "and camera moves that highlight the wow factor, but keep the footage clean with no text or overlays."
        ),
    }
    prompt = prompt_templates[target].format(title=title)
    boost_prompt = raw_form_values.get("youtube_boost_prompt") or form_values.get("youtube_boost_prompt", "")
    if target == "youtube":
        form_values["youtube_boost_prompt"] = boost_prompt
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

    try:
        video_bytes = generate_video_from_image(
            prompt=prompt,
            image=base_bytes,
            duration_seconds=duration_seconds,
            aspect_ratio="9:16",
            resolution="720p",
        )
    except Exception as exc:
        flash(f"Unable to generate the {target} video: {exc}", "error")
        return render_home_view(form_values, preview_payload, product_id=product_id)

    video_path = save_generated_video(video_bytes)
    preview_payload = preview_payload or {}
    if target == "youtube":
        preview_payload["youtube_boost_prompt"] = boost_prompt
    preview_payload["generated_video_path"] = video_path
    preview_payload["video_duration_seconds"] = str(duration_seconds)
    preview_payload["video_url"] = url_for("serve_media", filename=video_path)
    preview_payload["video_public_url"] = url_for(
        "serve_media", filename=video_path, _external=True
    )

    flash(f"{target.title()} video generated.", "success")
    update_product_state(
        product_id,
        form_values=form_values,
        preview=preview_payload,
        platforms={
            target: {
                "status": "pending",
                "video_path": video_path,
                "base_image_path": base_image_path,
                "use_affiliate_link": use_affiliate_link_flag,
            }
        },
        assets={"generated_video_path": video_path},
    )
    return render_home_view(form_values, preview_payload, product_id=product_id)


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


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
