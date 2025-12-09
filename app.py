import base64
import json
import os
from pathlib import Path
from typing import Dict, List
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
MARKET_OPTIONS = [
    "Shein",
    "Amazon",
    "AliExpress",
    "Temu",
    "Etsy",
    "eBay",
    "Walmart",
]


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _store_image(bytes_data: bytes, directory: Path, suffix: str) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}{suffix}"
    destination = directory / filename
    with open(destination, "wb") as file_handle:
        file_handle.write(bytes_data)
    return destination.relative_to(STORAGE_ROOT).as_posix()


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


def extract_form_defaults(raw_form_values: Dict[str, str]) -> Dict[str, str]:
    return {
        key: raw_form_values.get(key, "")
        for key in ("market", "sku_or_url", "title", "description", "affiliate_link", "pinterest_extra")
    }


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
    return render_template("home.html", markets=MARKET_OPTIONS)


@app.route("/generate-pinterest", methods=["POST"])
def generate_pinterest():
    raw_form_values = {key: value.strip() for key, value in request.form.items()}
    original_image_path = raw_form_values.pop("original_image_path", "")
    form_values = dict(raw_form_values)
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
        return render_template(
            "home.html",
            markets=MARKET_OPTIONS,
            form_values=form_values,
        )

    if image_file is not None and image_file.filename:
        original_bytes = image_file.read()
        if not original_bytes:
            flash("The uploaded image appears to be empty.", "error")
            return render_template(
                "home.html",
                markets=MARKET_OPTIONS,
                form_values=form_values,
            )
        original_image_path = save_original_image(original_bytes, image_file.filename)
    else:
        try:
            original_bytes = load_stored_image(original_image_path)
        except Exception:
            flash("Unable to load the previously uploaded image. Please upload again.", "error")
            return render_template(
                "home.html",
                markets=MARKET_OPTIONS,
                form_values=form_values,
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
                "Design a polished product visual with modern typography, a clear focus on the hero item, "
                "and scroll-stopping composition that can work across social platforms."
            ),
            aspect_ratio="2:3",
        )
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
            "video_prompt": (
                f"Create a dynamic short-form video for {refined_title}. Include upbeat pacing, text overlays "
                f"highlighting the benefits, and close with a CTA to tap the affiliate link."
            ),
        }

    except Exception as exc:  # pragma: no cover - guard for runtime issues
        app.logger.exception("Pinterest generation failed")
        flash(f"Unable to generate Pinterest pin: {exc}", "error")
        return render_template(
            "home.html",
            markets=MARKET_OPTIONS,
            form_values=form_values,
        )
    flash("Preview generated. Choose where to publish your content.", "info")
    return render_template(
        "home.html",
        markets=MARKET_OPTIONS,
        form_values=form_values,
        preview=preview_payload,
    )


@app.route("/confirm-pinterest", methods=["POST"])
def confirm_pinterest():
    raw_form_values = {key: value.strip() for key, value in request.form.items()}
    generated_image_path = raw_form_values.get("generated_image_path")
    original_image_path = raw_form_values.get("original_image_path")
    form_values = extract_form_defaults(raw_form_values)
    tags = parse_tags_payload(raw_form_values.get("tags"))

    try:
        image_bytes = load_stored_image(generated_image_path)
    except Exception:
        flash("Unable to load the generated image. Please regenerate it.", "error")
        return render_template(
            "home.html",
            markets=MARKET_OPTIONS,
            form_values=form_values,
        )

    try:
        pin_response = create_pinterest_pin(
            image_bytes,
            raw_form_values.get("title", ""),
            raw_form_values.get("description", ""),
            raw_form_values.get("affiliate_link"),
            tags,
        )
    except Exception as exc:
        app.logger.exception("Confirm Pinterest pin failed")
        flash(f"Unable to publish Pinterest pin: {exc}", "error")
        preview_payload = rebuild_preview_payload(raw_form_values)
        return render_template(
            "home.html",
            markets=MARKET_OPTIONS,
            form_values=form_values,
            preview=preview_payload,
        )

    flash("Pinterest pin published successfully!", "success")
    result = {
        "title": raw_form_values.get("title", ""),
        "description": raw_form_values.get("description", ""),
        "tags": tags,
        "pin_id": pin_response.get("id"),
        "pin_url": pin_response.get("url"),
        "status": pin_response.get("status"),
    }
    return render_template(
        "home.html",
        markets=MARKET_OPTIONS,
        form_values=form_values,
        result=result,
    )


@app.route("/generate-instagram-image", methods=["POST"])
def generate_instagram_image():
    raw_form_values = {key: value.strip() for key, value in request.form.items()}
    form_values = extract_form_defaults(raw_form_values)
    preview_payload = rebuild_preview_payload(raw_form_values)
    base_image_path = raw_form_values.get("original_image_path") or raw_form_values.get(
        "generated_image_path"
    )

    if not base_image_path:
        flash("Upload a product image before generating Instagram visuals.", "error")
        return render_template(
            "home.html",
            markets=MARKET_OPTIONS,
            form_values=form_values,
            preview=preview_payload,
        )

    try:
        base_bytes = load_stored_media(base_image_path)
    except Exception:
        flash("Unable to load the base image. Please regenerate your creative first.", "error")
        return render_template(
            "home.html",
            markets=MARKET_OPTIONS,
            form_values=form_values,
            preview=preview_payload,
        )

    variant = raw_form_values.get("instagram_variant", "feed").lower()
    aspect_ratio = "4:5" if variant == "feed" else "9:16"
    variant_label = "story" if variant == "story" else "feed"
    context_prompt = build_prompt_context({**form_values, "title": raw_form_values.get("title", "")})
    inst_prompt = (
        "Design a high-performing Instagram {variant} visual with bold typography, trending color grading, "
        "and a magnetic focus on the hero product."
    ).format(variant=variant_label)

    try:
        instagram_image = edit_image(
            base_bytes,
            context=context_prompt,
            prompt=inst_prompt,
            aspect_ratio=aspect_ratio,
        )
    except Exception as exc:
        flash(f"Unable to generate the Instagram visual: {exc}", "error")
        return render_template(
            "home.html",
            markets=MARKET_OPTIONS,
            form_values=form_values,
            preview=preview_payload,
        )

    instagram_image_path = save_generated_image(instagram_image)
    preview_payload = preview_payload or {}
    preview_payload["instagram_image_path"] = instagram_image_path
    preview_payload["instagram_image_url"] = url_for("serve_media", filename=instagram_image_path)
    preview_payload["instagram_image_public_url"] = url_for(
        "serve_media", filename=instagram_image_path, _external=True
    )
    preview_payload["instagram_image_data"] = base64.b64encode(instagram_image).decode("utf-8")

    flash(f"Instagram {variant_label} visual refreshed.", "success")
    return render_template(
        "home.html",
        markets=MARKET_OPTIONS,
        form_values=form_values,
        preview=preview_payload,
    )


@app.route("/publish-instagram", methods=["POST"])
def publish_instagram():
    raw_form_values = {key: value.strip() for key, value in request.form.items()}
    form_values = extract_form_defaults(raw_form_values)
    preview_payload = rebuild_preview_payload(raw_form_values)
    generated_image_path = raw_form_values.get("instagram_image_path") or raw_form_values.get(
        "generated_image_path"
    )

    if not generated_image_path:
        flash("Missing generated creative. Please run the generator first.", "error")
        return render_template(
            "home.html",
            markets=MARKET_OPTIONS,
            form_values=form_values,
            preview=preview_payload,
        )

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
    except Exception as exc:
        app.logger.exception("Instagram publish failed")
        flash(f"Unable to publish to Instagram: {exc}", "error")

    return render_template(
        "home.html",
        markets=MARKET_OPTIONS,
        form_values=form_values,
        preview=preview_payload,
    )


@app.route("/generate-video/<platform>", methods=["POST"])
def generate_platform_video(platform: str):
    supported = {"youtube", "tiktok"}
    target = platform.lower()
    if target not in supported:
        abort(404)

    raw_form_values = {key: value.strip() for key, value in request.form.items()}
    form_values = extract_form_defaults(raw_form_values)
    preview_payload = rebuild_preview_payload(raw_form_values)
    base_image_path = raw_form_values.get("instagram_image_path") or raw_form_values.get(
        "generated_image_path"
    )

    if not base_image_path:
        flash("Generate an image first to feed the video workflow.", "error")
        return render_template(
            "home.html",
            markets=MARKET_OPTIONS,
            form_values=form_values,
            preview=preview_payload,
        )

    try:
        base_bytes = load_stored_media(base_image_path)
    except Exception:
        flash("Unable to load the base visual. Please regenerate it.", "error")
        return render_template(
            "home.html",
            markets=MARKET_OPTIONS,
            form_values=form_values,
            preview=preview_payload,
        )

    title = raw_form_values.get("title") or form_values.get("title") or "this product"
    prompt_templates = {
        "youtube": (
            "Create a vertical YouTube Short for '{title}' with upbeat pacing, animated text callouts, "
            "and a CTA to tap the affiliate link."
        ),
        "tiktok": (
            "Create a TikTok-ready vertical video for '{title}' using trendy motion graphics, quick cuts, "
            "and bold overlays that highlight the wow factor."
        ),
    }
    prompt = prompt_templates[target].format(title=title)

    try:
        video_bytes = generate_video_from_image(
            prompt=prompt,
            image=base_bytes,
            duration_seconds=6,
            aspect_ratio="9:16",
            resolution="720p",
        )
    except Exception as exc:
        flash(f"Unable to generate the {target} video: {exc}", "error")
        return render_template(
            "home.html",
            markets=MARKET_OPTIONS,
            form_values=form_values,
            preview=preview_payload,
        )

    video_path = save_generated_video(video_bytes)
    preview_payload = preview_payload or {}
    preview_payload["generated_video_path"] = video_path
    preview_payload["video_url"] = url_for("serve_media", filename=video_path)
    preview_payload["video_public_url"] = url_for(
        "serve_media", filename=video_path, _external=True
    )

    flash(f"{target.title()} video generated.", "success")
    return render_template(
        "home.html",
        markets=MARKET_OPTIONS,
        form_values=form_values,
        preview=preview_payload,
    )


@app.route("/publish-youtube", methods=["POST"])
def publish_youtube():
    raw_form_values = {key: value.strip() for key, value in request.form.items()}
    form_values = extract_form_defaults(raw_form_values)
    preview_payload = rebuild_preview_payload(raw_form_values)
    video_path = raw_form_values.get("generated_video_path")

    if not video_path:
        flash("Generate the YouTube Short first, then publish.", "error")
        return render_template(
            "home.html",
            markets=MARKET_OPTIONS,
            form_values=form_values,
            preview=preview_payload,
        )

    try:
        video_bytes = load_stored_media(video_path)
    except Exception:
        flash("Unable to load the generated video. Please regenerate it.", "error")
        return render_template(
            "home.html",
            markets=MARKET_OPTIONS,
            form_values=form_values,
            preview=preview_payload,
        )

    title = raw_form_values.get("youtube_title", raw_form_values.get("title", ""))
    description = raw_form_values.get("youtube_description", raw_form_values.get("description", ""))
    keywords = parse_tags_payload(raw_form_values.get("youtube_keywords_payload", "[]"))

    try:
        response = publish_short_video(
            video_bytes,
            title=title,
            description=description,
            tags=keywords,
            privacy_status=raw_form_values.get("privacy_status", "unlisted"),
        )
        flash(
            f"YouTube Short uploaded (video id: {response.get('video_id', 'n/a')}).",
            "success",
        )
    except Exception as exc:
        app.logger.exception("YouTube publish failed")
        flash(f"Unable to publish to YouTube: {exc}", "error")

    return render_template(
        "home.html",
        markets=MARKET_OPTIONS,
        form_values=form_values,
        preview=preview_payload,
    )


@app.route("/publish-tiktok", methods=["POST"])
def publish_tiktok():
    raw_form_values = {key: value.strip() for key, value in request.form.items()}
    form_values = extract_form_defaults(raw_form_values)
    preview_payload = rebuild_preview_payload(raw_form_values)
    video_path = raw_form_values.get("generated_video_path")

    if not video_path:
        flash("Generate the TikTok video first, then publish.", "error")
        return render_template(
            "home.html",
            markets=MARKET_OPTIONS,
            form_values=form_values,
            preview=preview_payload,
        )

    try:
        video_bytes = load_stored_media(video_path)
    except Exception:
        flash("Unable to load the generated video. Please regenerate it.", "error")
        return render_template(
            "home.html",
            markets=MARKET_OPTIONS,
            form_values=form_values,
            preview=preview_payload,
        )

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
    except Exception as exc:
        app.logger.exception("TikTok publish failed")
        flash(f"Unable to publish to TikTok: {exc}", "error")

    return render_template(
        "home.html",
        markets=MARKET_OPTIONS,
        form_values=form_values,
        preview=preview_payload,
    )


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
