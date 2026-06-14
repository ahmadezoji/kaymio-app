"""Shared configuration and prompts for product video generation."""
from __future__ import annotations

import os
from typing import Dict, List, Tuple

DEFAULT_GEMINI_VIDEO_MODELS = [
    os.getenv("GEMINI_VIDEO_MODEL", "veo-3.1-generate-preview"),
]
DEFAULT_OPENAI_VIDEO_MODELS = [
    "sora-2",
    "sora-2-pro",
]
OPENAI_VIDEO_ALLOWED_SECONDS = (4, 8, 12)


def _normalize_model_list(raw_value: str, fallbacks: List[str]) -> List[str]:
    models = [item.strip() for item in str(raw_value or "").split(",") if item.strip()]
    for fallback in fallbacks:
        if fallback and fallback not in models:
            models.append(fallback)
    return models


def get_gemini_video_models() -> List[str]:
    configured = os.getenv("GEMINI_VIDEO_MODELS", "")
    return _normalize_model_list(
        configured,
        [os.getenv("GEMINI_VIDEO_MODEL", DEFAULT_GEMINI_VIDEO_MODELS[0]), *DEFAULT_GEMINI_VIDEO_MODELS],
    )


def get_openai_video_models() -> List[str]:
    configured = os.getenv("OPENAI_VIDEO_MODELS", "")
    return _normalize_model_list(
        configured,
        [os.getenv("OPENAI_VIDEO_MODEL", DEFAULT_OPENAI_VIDEO_MODELS[0]), *DEFAULT_OPENAI_VIDEO_MODELS],
    )


def resolve_video_generation_choice(model_name: str) -> Tuple[str, str]:
    requested = (model_name or "").strip()
    gemini_models = get_gemini_video_models()
    openai_models = get_openai_video_models()

    if requested in gemini_models:
        return "gemini", requested
    if requested in openai_models:
        return "openai", requested

    if gemini_models:
        return "gemini", gemini_models[0]
    if openai_models:
        return "openai", openai_models[0]
    return "", ""


def get_default_video_generation_model() -> str:
    default_model = (os.getenv("DEFAULT_VIDEO_GENERATION_MODEL") or "").strip()
    if default_model:
        provider, normalized = resolve_video_generation_choice(default_model)
        if provider and normalized:
            return normalized
    gemini_models = get_gemini_video_models()
    if gemini_models:
        return gemini_models[0]
    openai_models = get_openai_video_models()
    return openai_models[0] if openai_models else ""


def build_video_generation_options() -> Dict[str, object]:
    gemini_models = get_gemini_video_models()
    openai_models = get_openai_video_models()
    default_model = get_default_video_generation_model()
    return {
        "default_model": default_model,
        "openai_allowed_seconds": list(OPENAI_VIDEO_ALLOWED_SECONDS),
        "groups": [
            {"provider": "gemini", "label": "Gemini", "models": gemini_models},
            {"provider": "openai", "label": "OpenAI", "models": openai_models},
        ],
    }


def build_reference_preserving_video_prompt(
    prompt: str,
    *,
    context: str = "",
) -> str:
    prompt_parts = [
        "Create a product-focused affiliate video using the uploaded product image as the exact first-frame reference.",
        "Preserve the same product identity, silhouette, proportions, materials, colors, finish, markings, packaging details, and functional design cues throughout the video.",
        "Do not redesign, replace, morph, stylize away, duplicate, or remove parts of the product.",
        "The product in every shot must remain recognizably the same real item from the source image because affiliate accuracy matters.",
        "You may animate the scene with camera movement, lighting shifts, environmental context, hand interaction without visible faces, props, and platform-specific pacing, but the hero product itself must stay faithful to the reference.",
        "Keep the product clear, centered in the narrative, and free from obstructive overlays, fake logos, or irrelevant objects.",
        "Avoid human faces, celebrity likenesses, copyrighted characters, and unrelated brand assets.",
    ]
    normalized_context = str(context or "").strip()
    normalized_prompt = str(prompt or "").strip()
    if normalized_context:
        prompt_parts.append(f"Product context: {normalized_context}")
    if normalized_prompt:
        prompt_parts.append(f"Creative direction: {normalized_prompt}")
    return " ".join(prompt_parts)


def normalize_openai_video_seconds(duration_seconds: int) -> int:
    target = max(OPENAI_VIDEO_ALLOWED_SECONDS[0], int(duration_seconds or OPENAI_VIDEO_ALLOWED_SECONDS[0]))
    return min(OPENAI_VIDEO_ALLOWED_SECONDS, key=lambda candidate: abs(candidate - target))


def resolve_video_size(aspect_ratio: str = "9:16", resolution: str = "720p") -> str:
    normalized_ratio = (aspect_ratio or "9:16").strip()
    normalized_resolution = (resolution or "720p").strip().lower()
    portrait = normalized_ratio == "9:16"
    high_resolution = normalized_resolution == "1080p"

    if portrait:
        return "1024x1792" if high_resolution else "720x1280"
    return "1792x1024" if high_resolution else "1280x720"
