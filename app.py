import os
from typing import Dict

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
    form_values = {key: value.strip() for key, value in request.form.items()}
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

    if image_file is None or image_file.filename == "":
        errors.append("Upload at least one product image so we can craft a pin.")
    elif not allowed_file(image_file.filename):
        errors.append("Unsupported image type. Use PNG, JPG, JPEG, GIF, or WEBP.")

    if errors:
        for message in errors:
            flash(message, "error")
        return render_template(
            "home.html",
            markets=MARKET_OPTIONS,
            form_values=form_values,
        )

    original_bytes = image_file.read()
    if not original_bytes:
        flash("The uploaded image appears to be empty.", "error")
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

        pin_response = create_pinterest_pin(
            generated_image,
            refined_title,
            generated_description,
            form_values.get("affiliate_link"),
            tags,
        )

    except Exception as exc:  # pragma: no cover - guard for runtime issues
        app.logger.exception("Pinterest generation failed")
        flash(f"Unable to generate Pinterest pin: {exc}", "error")
        return render_template(
            "home.html",
            markets=MARKET_OPTIONS,
            form_values=form_values,
        )

    flash("Pinterest pin generated successfully!", "success")
    result = {
        "title": refined_title,
        "description": generated_description,
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
