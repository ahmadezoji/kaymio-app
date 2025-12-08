import base64
import json
import os
from pathlib import Path
from typing import Dict, List
from uuid import uuid4

from dotenv import load_dotenv
from flask import Flask, flash, render_template, request

from gemeni_api_helper import edit_image
from openai_helper import generate_tags_for_product_for_pintrest, generate_text
from pintrest.pinterest_helper import create_pinterest_pin

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20MB uploads
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
STORAGE_ROOT = Path(app.root_path) / "template_images"
ORIGINALS_DIR = STORAGE_ROOT / "originals"
GENERATED_DIR = STORAGE_ROOT / "generated"
for directory in (ORIGINALS_DIR, GENERATED_DIR):
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


def load_stored_image(relative_path: str) -> bytes:
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

        context_prompt = build_prompt_context({**form_values, "title": refined_title})
        generated_image = edit_image(
            original_bytes,
            context=context_prompt,
            prompt=(
                "Design a polished Pinterest pin with modern typography, clear focus on the product, "
                "and scroll-stopping composition."
            ),
            aspect_ratio="2:3",
        )
        generated_image_path = save_generated_image(generated_image)
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
        }

    except Exception as exc:  # pragma: no cover - guard for runtime issues
        app.logger.exception("Pinterest generation failed")
        flash(f"Unable to generate Pinterest pin: {exc}", "error")
        return render_template(
            "home.html",
            markets=MARKET_OPTIONS,
            form_values=form_values,
        )
    flash("Preview generated. Confirm before publishing to Pinterest.", "info")
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
    form_values = {
        key: raw_form_values.get(key, "")
        for key in ("market", "sku_or_url", "title", "description", "affiliate_link", "pinterest_extra")
    }
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
        preview_payload = {
            "title": raw_form_values.get("title", ""),
            "description": raw_form_values.get("description", ""),
            "tags": tags,
            "image_data": base64.b64encode(image_bytes).decode("utf-8"),
            "generated_image_path": generated_image_path,
            "original_image_path": original_image_path,
            "affiliate_link": raw_form_values.get("affiliate_link"),
            "market": raw_form_values.get("market"),
            "sku_or_url": raw_form_values.get("sku_or_url"),
            "pinterest_extra": raw_form_values.get("pinterest_extra"),
            "title_input": raw_form_values.get("title", ""),
            "description_input": raw_form_values.get("description", ""),
            "tags_payload": raw_form_values.get("tags", json.dumps(tags or [])),
        }
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


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
