"""OpenAI helpers for copywriting and image generation."""
from __future__ import annotations

import base64
import io
import json
import logging
import math
import os
import time
from typing import Dict, List, Optional, Sequence, Tuple, Union

import requests
from openai import OpenAI

from image_generation_config import (
    build_reference_preserving_prompt,
    get_openai_image_models,
    resolve_image_generation_choice,
)
from video_generation_config import (
    build_reference_preserving_video_prompt,
    get_openai_video_models,
    normalize_openai_video_seconds,
    resolve_video_generation_choice,
    resolve_video_size,
)

logger = logging.getLogger(__name__)

_client: OpenAI | None = None

try:  # Optional dependency
    from PIL import Image, ImageOps  # type: ignore
except ImportError:  # pragma: no cover
    Image = None  # type: ignore
    ImageOps = None  # type: ignore


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        _client = OpenAI(api_key=api_key)
    return _client


def _safe_json_loads(payload: str) -> Dict[str, str]:
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        logger.warning("OpenAI response was not valid JSON: %s", payload)
        return {}


def _coerce_image_bytes(
    base_image: Union[bytes, bytearray, str, "Image.Image"],
) -> bytes:
    if isinstance(base_image, (bytes, bytearray)):
        return bytes(base_image)
    if isinstance(base_image, str):
        with open(base_image, "rb") as handle:
            return handle.read()
    if Image is not None and isinstance(base_image, Image.Image):
        buffer = io.BytesIO()
        base_image.save(buffer, format="PNG")
        return buffer.getvalue()
    raise TypeError("base_image must be bytes, a file path, or a PIL image")


def _detect_image_upload_meta(image_bytes: bytes, *, fallback_index: int) -> Tuple[str, str]:
    mime_type = "image/png"
    extension = "png"
    if Image is None:
        return mime_type, f"reference_{fallback_index}.{extension}"
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image_format = (image.format or "PNG").upper()
        mime_type = Image.MIME.get(image_format, mime_type)
        extension = "jpg" if image_format == "JPEG" else image_format.lower()
    except Exception:
        logger.debug("Unable to infer image type for OpenAI upload; defaulting to PNG.")
    return mime_type, f"reference_{fallback_index}.{extension}"


def _aspect_ratio_to_openai_size(aspect_ratio: Optional[str]) -> str:
    if not aspect_ratio or ":" not in aspect_ratio:
        return "1024x1024"
    try:
        width_raw, height_raw = aspect_ratio.split(":", 1)
        width = float(width_raw)
        height = float(height_raw)
        if width <= 0 or height <= 0:
            return "1024x1024"
        ratio = width / height
    except (TypeError, ValueError):
        return "1024x1024"
    if ratio < 0.95:
        return "1024x1536"
    if ratio > 1.05:
        return "1536x1024"
    return "1024x1024"


def _prepare_video_reference_image(
    image: Union[bytes, bytearray, str, "Image.Image"],
    *,
    size: str,
    reference_images: Optional[Sequence[Union[bytes, bytearray, str, "Image.Image"]]] = None,
) -> Tuple[bytes, str, str]:
    width_str, height_str = size.split("x", 1)
    target_size = (int(width_str), int(height_str))
    normalized_images: List[bytes] = [_coerce_image_bytes(image)]
    for reference_image in reference_images or []:
        try:
            normalized_images.append(_coerce_image_bytes(reference_image))
        except Exception:
            logger.debug("Skipping invalid OpenAI video reference image input.", exc_info=True)
            continue
        if len(normalized_images) >= 4:
            break

    image_bytes = normalized_images[0]

    if Image is None:
        return image_bytes, "input_reference.png", "image/png"

    try:
        images: List["Image.Image"] = []
        for raw_bytes in normalized_images:
            with Image.open(io.BytesIO(raw_bytes)) as source_image:
                images.append(source_image.convert("RGB"))

        if len(images) == 1:
            prepared = ImageOps.fit(images[0], target_size, method=Image.LANCZOS)
        else:
            prepared = _build_video_reference_sheet(images, target_size)

        output = io.BytesIO()
        prepared.save(output, format="JPEG", quality=92, optimize=True)
        return output.getvalue(), "input_reference.jpg", "image/jpeg"
    except Exception:
        logger.debug("Unable to normalize OpenAI video reference image; using original bytes.")
        mime_type, file_name = _detect_image_upload_meta(image_bytes, fallback_index=1)
        return image_bytes, file_name, mime_type


def _build_video_reference_sheet(
    images: Sequence["Image.Image"],
    target_size: Tuple[int, int],
) -> "Image.Image":
    width, height = target_size
    gap = max(8, min(width, height) // 60)

    if len(images) == 2:
        columns, rows = 1, 2
    else:
        columns = 2
        rows = int(math.ceil(len(images) / columns))

    canvas = Image.new("RGB", target_size, color=(248, 248, 248))
    inner_width = width - gap * (columns + 1)
    inner_height = height - gap * (rows + 1)
    tile_width = max(1, inner_width // columns)
    tile_height = max(1, inner_height // rows)

    for index, image in enumerate(images):
        row = index // columns
        col = index % columns
        tile = ImageOps.fit(image, (tile_width, tile_height), method=Image.LANCZOS)
        left = gap + col * (tile_width + gap)
        top = gap + row * (tile_height + gap)
        canvas.paste(tile, (left, top))

    return canvas


def edit_image(
    base_image: Union[bytes, bytearray, str, "Image.Image"],
    *,
    prompt: Optional[str] = None,
    context: Optional[str] = None,
    aspect_ratio: Optional[str] = None,
    output_path: Optional[str] = None,
    model: Optional[str] = None,
    reference_images: Optional[Sequence[Union[bytes, bytearray, str, "Image.Image"]]] = None,
    quality: Optional[str] = None,
) -> bytes:
    """Edit an image with OpenAI using the source product image as an exact reference."""

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    resolved_provider, resolved_model = resolve_image_generation_choice(model or "")
    if resolved_provider != "openai" or not resolved_model:
        openai_models = get_openai_image_models()
        if not openai_models:
            raise RuntimeError("No OpenAI image model is configured")
        resolved_model = openai_models[0]

    original_bytes = _coerce_image_bytes(base_image)
    requested_quality = (quality or os.getenv("OPENAI_IMAGE_QUALITY", "medium")).strip() or "medium"
    final_prompt = build_reference_preserving_prompt(prompt or "", context=context or "")
    size = _aspect_ratio_to_openai_size(aspect_ratio)

    normalized_references: List[bytes] = [original_bytes]
    for image in reference_images or []:
        try:
            coerced = _coerce_image_bytes(image)
        except Exception:
            logger.debug("Skipping invalid OpenAI reference image input.", exc_info=True)
            continue
        if coerced:
            normalized_references.append(coerced)
        if len(normalized_references) >= 4:
            break

    files = []
    for index, image_bytes in enumerate(normalized_references, start=1):
        mime_type, file_name = _detect_image_upload_meta(image_bytes, fallback_index=index)
        files.append(("image[]", (file_name, image_bytes, mime_type)))

    try:
        response = requests.post(
            "https://api.openai.com/v1/images/edits",
            headers={"Authorization": f"Bearer {api_key}"},
            data={
                "model": resolved_model,
                "prompt": final_prompt,
                "size": size,
                "quality": requested_quality,
                "output_format": "png",
            },
            files=files,
            timeout=120,
        )
        response.raise_for_status()
    except requests.HTTPError as exc:  # pragma: no cover - network/runtime guard
        detail = ""
        try:
            detail = response.text
        except Exception:
            detail = ""
        raise RuntimeError(
            f"OpenAI image edit failed ({getattr(response, 'status_code', 'unknown')}): {detail or exc}"
        ) from exc
    except requests.RequestException as exc:  # pragma: no cover
        raise RuntimeError(f"OpenAI image edit request failed: {exc}") from exc

    try:
        payload = response.json()
    except ValueError as exc:  # pragma: no cover
        raise RuntimeError("OpenAI image edit returned invalid JSON.") from exc

    data_items = payload.get("data") or []
    if not data_items:
        raise RuntimeError("OpenAI image edit returned no image data.")
    image_base64 = str(data_items[0].get("b64_json") or "").strip()
    if not image_base64:
        raise RuntimeError("OpenAI image edit did not include a base64 image result.")

    image_bytes = base64.b64decode(image_base64)
    if output_path:
        with open(output_path, "wb") as handle:
            handle.write(image_bytes)
    return image_bytes


def generate_video_from_image(
    prompt: str,
    image: Union[bytes, bytearray, str, "Image.Image"],
    reference_images: Optional[Sequence[Union[bytes, bytearray, str, "Image.Image"]]] = None,
    duration_seconds: int = 8,
    aspect_ratio: str = "9:16",
    resolution: str = "720p",
    output_path: Optional[str] = None,
    poll_interval: float = 10.0,
    model: Optional[str] = None,
    context: Optional[str] = None,
) -> bytes:
    """Generate a video with OpenAI Sora using the product image as an input reference."""

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    resolved_provider, resolved_model = resolve_video_generation_choice(model or "")
    if resolved_provider != "openai" or not resolved_model:
        video_models = get_openai_video_models()
        if not video_models:
            raise RuntimeError("No OpenAI video model is configured")
        resolved_model = video_models[0]

    size = resolve_video_size(aspect_ratio=aspect_ratio, resolution=resolution)
    normalized_seconds = normalize_openai_video_seconds(duration_seconds)
    final_prompt = build_reference_preserving_video_prompt(prompt, context=context or "")
    if reference_images:
        final_prompt = (
            f"{final_prompt} Additional reference angles are included in the uploaded reference sheet. "
            "Preserve the same product details consistently across all provided views."
        )
    reference_bytes, file_name, mime_type = _prepare_video_reference_image(
        image,
        size=size,
        reference_images=reference_images,
    )

    try:
        create_response = requests.post(
            "https://api.openai.com/v1/videos",
            headers={"Authorization": f"Bearer {api_key}"},
            data={
                "model": resolved_model,
                "prompt": final_prompt,
                "size": size,
                "seconds": str(normalized_seconds),
            },
            files={
                "input_reference": (file_name, reference_bytes, mime_type),
            },
            timeout=120,
        )
        create_response.raise_for_status()
    except requests.HTTPError as exc:  # pragma: no cover
        detail = ""
        try:
            detail = create_response.text
        except Exception:
            detail = ""
        raise RuntimeError(
            f"OpenAI video create failed ({getattr(create_response, 'status_code', 'unknown')}): {detail or exc}"
        ) from exc
    except requests.RequestException as exc:  # pragma: no cover
        raise RuntimeError(f"OpenAI video request failed: {exc}") from exc

    job_payload = create_response.json()
    video_id = str(job_payload.get("id") or "").strip()
    if not video_id:
        raise RuntimeError("OpenAI video create did not return a video id.")

    status_payload = job_payload
    while str(status_payload.get("status") or "").strip() in {"queued", "in_progress"}:
        time.sleep(max(5.0, poll_interval))
        try:
            status_response = requests.get(
                f"https://api.openai.com/v1/videos/{video_id}",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=60,
            )
            status_response.raise_for_status()
            status_payload = status_response.json()
        except requests.RequestException as exc:  # pragma: no cover
            raise RuntimeError(f"OpenAI video polling failed: {exc}") from exc

    final_status = str(status_payload.get("status") or "").strip()
    if final_status != "completed":
        error_payload = status_payload.get("error") or {}
        error_message = error_payload.get("message") or final_status or "unknown error"
        raise RuntimeError(f"OpenAI video generation failed: {error_message}")

    try:
        content_response = requests.get(
            f"https://api.openai.com/v1/videos/{video_id}/content",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=120,
        )
        content_response.raise_for_status()
    except requests.HTTPError as exc:  # pragma: no cover
        detail = ""
        try:
            detail = content_response.text
        except Exception:
            detail = ""
        raise RuntimeError(
            f"OpenAI video download failed ({getattr(content_response, 'status_code', 'unknown')}): {detail or exc}"
        ) from exc
    except requests.RequestException as exc:  # pragma: no cover
        raise RuntimeError(f"OpenAI video download failed: {exc}") from exc

    video_bytes = content_response.content
    if output_path:
        with open(output_path, "wb") as handle:
            handle.write(video_bytes)
    return video_bytes


def generate_text(
    title: str,
    context: str = "You are a helpful assistant that extracts short product concepts.",
    user_prompt: Optional[str] = None,
    *,
    max_tokens: int = 40,
    temperature: float = 0.7,
) -> str:
    """General-purpose text generator that can adapt via the context prompt."""

    try:
        client = _get_client()
    except RuntimeError:
        logger.warning("OPENAI_API_KEY missing; unable to call generate_text.")
        return (title or "Unknown Concept").strip() or "Unknown Concept"

    try:
        request_template = user_prompt or (
            "Extract a concise concept or keyword from this product title: '{text}'. "
            "Make it short and suitable as a product name."
        )
        user_message = request_template.format(text=title)
    except Exception as exc:  # pragma: no cover - format guard
        logger.exception("generate_text prompt formatting failed: %s", exc)
        user_message = title

    try:
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": context},
                {"role": "user", "content": user_message},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        choice = response.choices[0].message.content if response.choices else ""
        concept = (choice or "").strip()
        concept = concept.replace('"', "").replace("'", "")
        return concept or (title or "Unknown Concept")
    except Exception as exc:  # pragma: no cover - runtime guard
        logger.exception("generate_text failed: %s", exc)
        return (title or "Unknown Concept").strip() or "Unknown Concept"


def _generate_response_text(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0.6,
    max_output_tokens: int = 250,
) -> Optional[str]:
    """Helper to call the Responses API for free-form text outputs."""

    try:
        client = _get_client()
    except RuntimeError:
        return None

    try:
        response = client.responses.create(
            model=os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini"),
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                {"role": "user", "content": [{"type": "input_text", "text": user_prompt}]},
            ],
        )
        text = getattr(response, "output_text", "")
        return text.strip() or None
    except Exception as exc:  # pragma: no cover
        logger.exception("OpenAI caption helper failed: %s", exc)
        return None


def extract_concept_from_text(title: str, description: str, extra_text: str) -> Dict[str, str]:
    """Use OpenAI to polish a Pinterest-friendly title/description."""

    fallback = {
        "title": (title or "Untitled Product").strip(),
        "description": (description or """Discover why shoppers love this find.""").strip(),
    }

    try:
        client = _get_client()
    except RuntimeError:
        logger.warning("OPENAI_API_KEY missing; falling back to user-provided copy.")
        return fallback

    system_prompt = (
        "You are an affiliate marketing copywriter creating compelling Pinterest pin copy. "
        "Return strict JSON with keys 'title' and 'description'."
    )
    user_prompt = (
        "Write a 70-character Pinterest-ready title and a 2 sentence description "
        "highlighting benefits, urgency, and relevance."
    )

    content = {
        "type": "input_text",
        "text": (
            f"Title: {title}\nDescription: {description}\nExtra Pinterest Context: {extra_text}"
        ),
    }

    try:
        response = client.responses.create(
            model=os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini"),
            temperature=0.5,
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                {"role": "user", "content": [content, {"type": "input_text", "text": user_prompt}]},
            ],
        )
        data = _safe_json_loads(response.output_text)
    except Exception as exc:  # pragma: no cover - guard for runtime
        logger.exception("OpenAI extract_concept_from_text failed: %s", exc)
        return fallback

    return {
        "title": data.get("title", fallback["title"]),
        "description": data.get("description", fallback["description"]),
    }


def generate_tags_for_product_for_pintrest(title: str, description: str) -> List[str]:
    """Generate 6-8 Pinterest SEO tags using OpenAI."""

    try:
        client = _get_client()
    except RuntimeError:
        logger.warning("OPENAI_API_KEY missing; returning keyword fallback.")
        return [
            keyword.strip()
            for keyword in (title or "Lifestyle Find").split()
            if keyword.strip()
        ][:6]

    system_prompt = (
        "Provide a JSON array of concise Pinterest SEO tags that would help the pin rank."
    )
    prompt = f"Title: {title}\nDescription: {description}"

    try:
        response = client.responses.create(
            model=os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini"),
            temperature=0.2,
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                {"role": "user", "content": [{"type": "input_text", "text": prompt}]},
            ],
        )
        text = response.output_text
        tags = json.loads(text)
        return [str(tag).strip() for tag in tags if str(tag).strip()][:8]
    except Exception as exc:  # pragma: no cover
        logger.exception("OpenAI generate_tags_for_product_for_pintrest failed: %s", exc)
        return [
            keyword.strip()
            for keyword in (title or "Lifestyle Find").split()
            if keyword.strip()
        ][:6]


def generate_caption_for_instagram(
    title: str,
    description: str,
    call_to_action: Optional[str] = None,
) -> str:
    """Compose an Instagram-friendly caption with soft CTA + emoji hooks."""

    fallback = (description or title or "Instagram caption").strip()
    prompt = (
        f"Product title: {title}\nDescription: {description}\nCTA: {call_to_action or 'Shop via the link in bio.'}\n"
        "Write a playful, benefit-first Instagram caption under 2200 characters with spaced lines."
    )
    result = _generate_response_text(
        "You write engaging Instagram captions that mix emoji dividers, social proof, and urgency.",
        prompt,
        temperature=0.7,
        max_output_tokens=300,
    )
    return result or fallback


def generate_hashtags_for_instagram(title: str, description: str) -> List[str]:
    """Return up to 15 Instagram hashtags aligned with the product angle."""

    fallback = [
        keyword.strip().lower().replace(" ", "")
        for keyword in (title or "trending find").split()
        if keyword.strip()
    ][:10]

    prompt = f"Title: {title}\nDescription: {description}\nReturn JSON array of short, niche hashtags."
    text = _generate_response_text(
        "Provide concise Instagram hashtags that can help reach shoppers.",
        prompt,
        temperature=0.4,
        max_output_tokens=200,
    )
    if not text:
        return fallback

    try:
        tags = json.loads(text)
        return [str(tag).strip().lstrip("#") for tag in tags if str(tag).strip()][:15]
    except json.JSONDecodeError:
        logger.warning("Instagram hashtags response was not JSON: %s", text)
        return fallback


def generate_caption_for_tiktok(title: str, description: str) -> str:
    """Generate a punchy TikTok caption focusing on hooks and CTA."""

    fallback = (description or title or "TikTok caption").strip()
    prompt = (
        f"Product title: {title}\nDescription: {description}\n"
        "Write a short TikTok caption (<150 chars) with a hook + CTA."
    )
    result = _generate_response_text(
        "You craft Gen Z friendly TikTok captions with emoji hooks and urgency.",
        prompt,
        temperature=0.8,
        max_output_tokens=120,
    )
    return result or fallback


def generate_hashtags_for_tiktok(title: str, description: str) -> List[str]:
    """Return hashtag set optimized for TikTok search trends."""

    fallback = [
        keyword.strip().lower().replace(" ", "")
        for keyword in (title or "viral find").split()
        if keyword.strip()
    ][:6]

    prompt = f"Title: {title}\nDescription: {description}\nReturn JSON array of TikTok hashtags."
    text = _generate_response_text(
        "Provide discoverable TikTok hashtags mixing niche + broad search terms.",
        prompt,
        temperature=0.5,
        max_output_tokens=150,
    )
    if not text:
        return fallback
    try:
        tags = json.loads(text)
        return [str(tag).strip().lstrip("#") for tag in tags if str(tag).strip()][:10]
    except json.JSONDecodeError:
        logger.warning("TikTok hashtags were not JSON: %s", text)
        return fallback


def generate_youtube_metadata(title: str, description: str) -> Dict[str, str]:
    """Produce title/description/keyword block tailored for YouTube Shorts."""

    fallback = {
        "title": (title or "Untitled Short").strip()[:100],
        "description": (description or "Discover why creators love this find.").strip(),
        "keywords": [
            keyword.strip()
            for keyword in (title or "shorts find").split()
            if keyword.strip()
        ][:8],
    }

    prompt = (
        "Return JSON with keys title, description, keywords. Title < 100 chars, description < 500 chars. "
        f"Product title: {title}\nDescription: {description}\nFocus on YouTube Shorts shoppers."
    )
    text = _generate_response_text(
        "You create compelling metadata for YouTube Shorts and return strict JSON.",
        prompt,
        temperature=0.4,
        max_output_tokens=300,
    )
    if not text:
        return fallback

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("YouTube metadata response not JSON: %s", text)
        return fallback

    return {
        "title": (data.get("title") or fallback["title"]).strip()[:100],
        "description": (data.get("description") or fallback["description"]).strip(),
        "keywords": [
            str(item).strip()
            for item in data.get("keywords", fallback["keywords"])
            if str(item).strip()
        ][:12],
    }


def generate_story_cta_text(product_title: str = "", description: str = "", caption: str = "") -> Dict[str, str]:
    """Produce compact CTA copy suitable for a visual story overlay."""

    source_text = " ".join(part.strip() for part in (product_title, caption, description) if part and part.strip())
    cleaned_source = source_text.strip()
    words = [word.strip(".,!?:;\"'()[]{}") for word in cleaned_source.split() if word.strip()]
    strong_words = [word for word in words if len(word) > 3][:4]
    keyword_phrase = " ".join(strong_words[:2]).strip()
    fallback_title = keyword_phrase.title() if keyword_phrase else "Style Worth Saving"
    fallback_body = "Find it in our bio."

    prompt = (
        "Return strict JSON with keys headline and body. "
        "Write premium, catchy CTA overlay copy for an Instagram story selling a product. "
        "Do not repeat the input wording or end with generic words like upgrade, product, item, deal, style unless absolutely necessary. "
        "Extract the product category, mood, benefit, or transformation and write fresh marketing copy. "
        "Headline rules: max 4 words, 2-4 words preferred, punchy, stylish, product-aware, no punctuation unless essential. "
        "Body rules: max 8 words, action-oriented, short, human. "
        "If you reference next step, direct users to bio or website, not a story tap. "
        "Do not use hashtags. Do not use quotation marks. Do not mention Instagram. "
        "Avoid generic lines like Discover this product or Elevate Your Wardrobe.\n"
        f"Product title: {product_title}\n"
        f"Caption: {caption}\n"
        f"Product description: {description}"
    )
    text = _generate_response_text(
        "You write short, high-performing CTA copy for story overlays. "
        "Your copy must feel like ad creative, not a summary. "
        "Every headline should feel category-specific and visually distinctive.",
        prompt,
        temperature=0.95,
        max_output_tokens=90,
    )
    if not text:
        return {"headline": fallback_title, "body": fallback_body}

    data = _safe_json_loads(text)
    headline = str(data.get("headline") or fallback_title).strip().replace('"', "")
    body = str(data.get("body") or fallback_body).strip().replace('"', "")
    if headline.lower() in cleaned_source.lower():
        headline = fallback_title
    if body.lower() in cleaned_source.lower():
        body = fallback_body
    return {
        "headline": " ".join(headline.split()[:4]) or fallback_title,
        "body": " ".join(body.split()[:8]) or fallback_body,
    }


def find_nearest_category(title, categories):
    try:
        try:
            client = _get_client()
        except RuntimeError:
            return None
        if categories and isinstance(categories[0], dict):
            category_pairs = [
                f"{cat['name']} (id: {cat['id']})" for cat in categories]
        else:
            category_pairs = [
                f"{name} (id: {cat_id})" for name, cat_id in categories]
        prompt = (
            f"Given the following product title: '{title}', "
            f"determine the most relevant sub category from this list: {', '.join(category_pairs)}. "
            f"Return only the related sub category id."
        )
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that matches product titles to categories."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=60,
            temperature=0.7,
            stop=["\n"]
        )
        category_id = response.choices[0].message.content.strip()
        if not category_id.isdigit():
            category_id = '14'  # Default to 'Health and beauty' if invalid
        return category_id
    except Exception as e:
        print(f"Error finding nearest category: {e}")
        return None
