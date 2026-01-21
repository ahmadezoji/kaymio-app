"""Utilities for working with Google/Gemini image editing."""
from __future__ import annotations

import io
import logging
import os
import time
from typing import Optional, Union
from google.genai import types as gemtype

logger = logging.getLogger(__name__)
from dotenv import load_dotenv
load_dotenv()

try:  # Optional dependency
    from PIL import Image  # type: ignore
except ImportError:  # pragma: no cover
    Image = None  # type: ignore

try:  # Attempt to import Gemini SDK once at module import
    import google.generativeai as genai  # type: ignore
except ImportError:  # pragma: no cover
    genai = None  # type: ignore

try:  # Newer Google GenAI SDK exposes video helpers here
    from google import genai as google_genai  # type: ignore
except ImportError:  # pragma: no cover
    google_genai = None  # type: ignore


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

    if genai is None:
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

    types_module = getattr(genai, "types", None)
    image_config = None
    generation_config = None

    if types_module:
        if aspect_ratio:
            image_config_cls = getattr(types_module, "ImageConfig", None) or getattr(
                types_module, "ImageGenerationConfig", None
            )
            if image_config_cls:
                try:
                    image_config = image_config_cls(aspect_ratio=aspect_ratio)
                except Exception as exc:  # pragma: no cover - SDK mismatch handling
                    logger.warning("Gemini SDK rejected aspect_ratio hint: %s", exc)
            else:
                logger.debug("Gemini SDK does not expose ImageConfig; skipping aspect_ratio hint.")

        gen_config_cls = getattr(types_module, "GenerateContentConfig", None)
        if gen_config_cls:
            try:
                generation_config = gen_config_cls(
                    response_modalities=["IMAGE"],
                    image_config=image_config,
                )
            except Exception:
                try:
                    generation_config = gen_config_cls()
                except Exception:
                    generation_config = None

        if generation_config is None:
            gen_config_cls = getattr(types_module, "GenerationConfig", None)
            if gen_config_cls:
                try:
                    generation_config = gen_config_cls()
                except Exception:
                    generation_config = None
    elif aspect_ratio:
        logger.debug("Gemini types module unavailable; cannot pass aspect_ratio hints.")

    try:
        generation_kwargs = {}
        if generation_config is not None:
            generation_kwargs["generation_config"] = generation_config

        combined_prompt = f"{base_context}\n\nInstructions:\n{final_prompt}"
        response = model.generate_content(
            contents=[
                {
                    "role": "user",
                    "parts": [
                        {"text": combined_prompt},
                        {"inline_data": {"mime_type": "image/png", "data": original_bytes}},
                    ],
                }
            ],
            **generation_kwargs,
        )

        edited_bytes: Optional[bytes] = None
        for part in getattr(response, "parts", []):
            inline_data = getattr(part, "inline_data", None)
            if inline_data and getattr(inline_data, "data", None):
                edited_bytes = inline_data.data
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


def generate_video_from_image(
    prompt: str,
    image: Union[bytes, Image.Image, str],
    duration_seconds: int = 5,
    aspect_ratio: str = "9:16",
    resolution: str = "720p",
    output_path: Optional[str] = None,
    poll_interval: float = 5.0,
) -> bytes:
    """Generate a short-form video from an image + prompt using Gemini Veo."""

    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY or GEMINI_API_KEY is required for video generation")

    if google_genai is not None and hasattr(google_genai, "Client"):
        return _generate_video_with_modern_sdk(
            api_key=api_key,
            prompt=prompt,
            image=image,
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            output_path=output_path,
            poll_interval=poll_interval,
        )

    if genai is None:
        raise RuntimeError("google-generativeai package not available")

    types_module = getattr(genai, "types", None)
    client_cls = getattr(genai, "Client", None)
    if not types_module or not client_cls:
        raise RuntimeError(
            "Gemini SDK is missing required video helpers. Install the google-genai package "
            "(`pip install google-genai` or re-run `pip install -r requirements.txt`)."
        )

    return _generate_video_with_legacy_sdk(
        api_key=api_key,
        prompt=prompt,
        image=image,
        duration_seconds=duration_seconds,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        output_path=output_path,
        poll_interval=poll_interval,
        types_module=types_module,
        client_cls=client_cls,
    )


def _generate_video_with_modern_sdk(
    *,
    api_key: str,
    prompt: str,
    image: Union[bytes, Image.Image, str],
    duration_seconds: int,
    aspect_ratio: str,
    resolution: str,
    output_path: Optional[str],
    poll_interval: float,
) -> bytes:
    """Use google.genai Client to run Veo generation."""

    if google_genai is None:
        raise RuntimeError("google.genai SDK not available.")

    types_module = getattr(google_genai, "types", None)
    if types_module is None:
        raise RuntimeError("google.genai SDK missing types module.")

    client_cls = getattr(google_genai, "Client", None)
    if client_cls is None:
        raise RuntimeError("google.genai Client class not available.")

    client = client_cls(api_key=api_key)
    image_bytes = _coerce_image_bytes(image)
    image_cls = getattr(types_module, "Image", None)
    if image_cls is None:
        raise RuntimeError("google.genai SDK missing Image helper.")
    image_obj = image_cls(image_bytes=image_bytes, mime_type="image/png")

    source_cls = getattr(types_module, "GenerateVideosSource", None)
    if source_cls is None:
        raise RuntimeError("google.genai SDK missing GenerateVideosSource helper.")
    source = source_cls(prompt=prompt, image=image_obj)

    config_cls = getattr(types_module, "GenerateVideosConfig", None)
    config = None
    if config_cls:
        config = config_cls(
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            duration_seconds=duration_seconds,
        )

    model_name = os.getenv("GEMINI_VIDEO_MODEL", "veo-3.1-generate-preview")
    model_client = getattr(client, "models", None)
    if model_client is None or not hasattr(model_client, "generate_videos"):
        raise RuntimeError("google.genai client does not expose video generation endpoint")

    operation = model_client.generate_videos(
        model=model_name,
        source=source,
        config=config,
    )

    operations_client = getattr(client, "operations", None)
    operation = _poll_video_operation(operation, operations_client, poll_interval)
    return _finalize_modern_video(operation, client, output_path)


def _generate_video_with_legacy_sdk(
    *,
    api_key: str,
    prompt: str,
    image: Union[bytes, Image.Image, str],
    duration_seconds: int,
    aspect_ratio: str,
    resolution: str,
    output_path: Optional[str],
    poll_interval: float,
    types_module,
    client_cls,
) -> bytes:
    """Fallback to google.generativeai Client for older SDKs."""

    genai.configure(api_key=api_key)
    client = client_cls(api_key=api_key)

    image_cls = getattr(types_module, "Image", None)
    if image_cls is None:
        raise RuntimeError("Gemini SDK does not expose Image helper for video generation")

    if Image is not None and isinstance(image, Image.Image):
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        image_bytes = buffer.getvalue()
        image_obj = image_cls.from_bytes(image_bytes, mime_type="image/png")
    elif isinstance(image, (bytes, bytearray)):
        image_obj = image_cls.from_bytes(bytes(image), mime_type="image/png")
    elif isinstance(image, str):
        image_obj = image_cls.from_file(location=image)
    else:
        raise TypeError("image must be bytes, a path string, or PIL.Image.Image instance")

    config_cls = getattr(types_module, "GenerateVideosConfig", None)
    if config_cls is None:
        raise RuntimeError("Gemini SDK missing GenerateVideosConfig. Update SDK to use Veo.")
    config = config_cls(
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        duration_seconds=duration_seconds,
    )

    model_name = os.getenv("GEMINI_VIDEO_MODEL", "veo-3.1-generate-preview")
    model_client = getattr(client, "models", None)
    if model_client is None or not hasattr(model_client, "generate_videos"):
        raise RuntimeError("Gemini client does not expose video generation endpoint")

    operation = model_client.generate_videos(
        model=model_name,
        prompt=prompt,
        image=image_obj,
        config=config,
    )

    operations_client = getattr(client, "operations", None)
    operation = _poll_video_operation(operation, operations_client, poll_interval)

    result = _resolve_operation_result(operation)
    generated_videos = getattr(result, "generated_videos", None) if result else None
    if not generated_videos:
        raise RuntimeError("No videos returned from Veo video generation.")

    video_container = generated_videos[0]
    video_obj = getattr(video_container, "video", None) or video_container

    files_client = getattr(client, "files", None)
    video_bytes = getattr(video_obj, "video_bytes", None)
    if video_bytes is None and files_client and hasattr(files_client, "download"):
        video_bytes = files_client.download(file=video_obj)

    if video_bytes is None and hasattr(video_obj, "save"):
        buffer = io.BytesIO()
        video_obj.save(buffer)
        video_bytes = buffer.getvalue()

    if video_bytes is None:
        raise RuntimeError("Video bytes unavailable in Veo response.")

    if output_path:
        with open(output_path, "wb") as f_handle:
            f_handle.write(video_bytes)

    return video_bytes


def _poll_video_operation(operation, operations_client, poll_interval: float):
    """Poll a long-running video operation until completion."""

    def is_done(op):
        d = getattr(op, "done", None)
        if callable(d):
            return bool(d())
        return bool(d)

    while not is_done(operation):
        time.sleep(poll_interval)
        if operations_client and hasattr(operations_client, "get"):
            operation = operations_client.get(operation)
        else:
            break

    if getattr(operation, "error", None):
        raise RuntimeError(f"Video generation failed: {operation.error}")

    # Log the operation object if it claims to be done but result is empty
    # This helps you see if 'safety_ratings' or other reasons blocked it.
    logger.debug(f"Operation completed. Metadata: {getattr(operation, 'metadata', 'N/A')}")

    return operation



def _resolve_operation_result(operation):
    """
    Robustly extracts the result from a long-running operation.
    Handles differences between SDK versions and response structures.
    """
    if operation is None:
        return None

    # 1. Try checking for a direct 'result' attribute/method (Modern SDK)
    result_attr = getattr(operation, "result", None)
    if result_attr is not None:
        return result_attr() if callable(result_attr) else result_attr

    # 2. Try checking for 'response' (Common in legacy or gapic-based operations)
    response_attr = getattr(operation, "response", None)
    if response_attr is not None:
        return response_attr() if callable(response_attr) else response_attr

    # 3. Check for 'metadata' which sometimes holds error details or partial results
    return getattr(operation, "metadata", None)

def _finalize_modern_video(operation, client, output_path: Optional[str]) -> bytes:
    """Download generated video bytes with improved error handling for safety blocks."""

    result = _resolve_operation_result(operation)
    
    # Check if the result exists
    if result is None:
        raise RuntimeError(f"Veo operation completed but returned no result. Op State: {operation}")

    # Access generated videos
    generated_videos = getattr(result, "generated_videos", None)
    
    # Check for safety filters if the video list is empty
    if not generated_videos:
        safety_ratings = getattr(result, "safety_ratings", "No safety info provided")
        raise RuntimeError(
            f"No videos returned from Veo. This usually indicates a safety filter block.\n"
            f"Safety Info: {safety_ratings}"
        )

    video_container = generated_videos[0]
    video_obj = getattr(video_container, "video", None) or video_container
    
    # Standard byte extraction
    video_bytes = getattr(video_obj, "video_bytes", None)
    files_client = getattr(client, "files", None)
    
    if video_bytes is None and files_client and hasattr(files_client, "download"):
        video_bytes = files_client.download(file=video_obj)

    if video_bytes is None:
        raise RuntimeError("Video bytes unavailable in Veo response.")

    if output_path:
        with open(output_path, "wb") as f_handle:
            f_handle.write(video_bytes)

    return video_bytes
