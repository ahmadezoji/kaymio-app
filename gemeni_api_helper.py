"""Utilities for working with Google/Gemini image editing."""
from __future__ import annotations

import io
import logging
import os
from typing import Optional, Union

logger = logging.getLogger(__name__)

try:  # Optional dependency
    from PIL import Image  # type: ignore
except ImportError:  # pragma: no cover
    Image = None  # type: ignore


def _coerce_image_bytes(base_image: Union[bytes, bytearray, str, "Image.Image"]) -> bytes:
    """Convert supported image inputs into raw bytes."""

    if isinstance(base_image, (bytes, bytearray)):
        return bytes(base_image)

    if isinstance(base_image, str):
        with open(base_image, "rb") as handle:
            return handle.read()

    if Image is not None and isinstance(base_image, Image.Image):
        buf = io.BytesIO()
        base_image.save(buf, format="PNG")
        return buf.getvalue()

    raise TypeError("base_image must be bytes/bytearray, path string, or PIL.Image.Image")


def edit_image(
    base_image: Union[bytes, bytearray, str, "Image.Image"],
    *,
    prompt: Optional[str] = None,
    context: Optional[str] = None,
    aspect_ratio: Optional[str] = None,
    output_path: Optional[str] = None,
) -> bytes:
    """General-purpose Gemini image edit helper.

    Parameters
    ----------
    base_image:
        Bytes, a filesystem path, or a PIL Image. Converted to bytes before sending to Gemini.
    prompt / context:
        Text instructions that can be tailored for Pinterest, Instagram, product thumbnails, etc.
        Both strings are optional; the final instruction set concatenates them.
    aspect_ratio:
        Optional aspect ratio hint (e.g. "1:1", "3:4") supported by Gemini image models.
    output_path:
        Optional local file destination to persist the edited PNG bytes.
    """

    original_bytes = _coerce_image_bytes(base_image)

    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GOOGLE_API_KEY/GEMINI_API_KEY is not configured; returning original image.")
        return original_bytes

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.warning("google-generativeai package not installed; returning original image.")
        return original_bytes

    genai.configure(api_key=api_key)
    model_name = os.getenv("GEMINI_IMAGE_MODEL", "imagen-3.0")
    model = genai.GenerativeModel(model_name=model_name)

    base_context = (
        "You are a senior brand designer creating high-performing visuals for affiliate marketing."
    )
    if context:
        base_context = f"{base_context}\nContext: {context}"

    final_prompt = prompt or (
        "Enhance the uploaded photo with vibrant lighting, clean typography overlays, and platform-friendly framing."
    )

    image_config = None
    if aspect_ratio:
        image_config = types.ImageConfig(aspect_ratio=aspect_ratio)

    config = types.GenerateContentConfig(
        response_modalities=["IMAGE"],
        image_config=image_config,
    )

    try:
        response = model.generate_content(
            contents=[
                {"role": "system", "parts": [{"text": base_context}]},
                {
                    "role": "user",
                    "parts": [
                        {"text": final_prompt},
                        {"inline_data": {"mime_type": "image/png", "data": original_bytes}},
                    ],
                },
            ],
            generation_config=config,
        )

        edited_bytes: Optional[bytes] = None
        for part in getattr(response, "parts", []):
            inline_data = getattr(part, "inline_data", None)
            if inline_data:
                edited_bytes = inline_data.get("data")
                if edited_bytes:
                    break

        if not edited_bytes and hasattr(response, "generated_images"):
            # Backwards compatibility for imagen-3 style responses
            candidates = getattr(response, "generated_images", [])
            if candidates:
                edited_bytes = candidates[0].data

        if not edited_bytes:
            raise RuntimeError("No image bytes returned from Gemini image editing.")

        if output_path:
            with open(output_path, "wb") as file_handle:
                file_handle.write(edited_bytes)

        return edited_bytes

    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.exception("Gemini image edit failed: %s", exc)
        return original_bytes
