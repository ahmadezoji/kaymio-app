"""Shared configuration and prompts for product image generation."""
from __future__ import annotations

import os
from typing import Dict, List, Tuple

DEFAULT_OPENAI_IMAGE_MODELS = [
    "gpt-image-2",
    "gpt-image-1.5",
    "gpt-image-1",
    "gpt-image-1-mini",
]
DEFAULT_GEMINI_IMAGE_MODELS = [
    os.getenv("GEMINI_IMAGE_MODEL", "imagen-3.0"),
]


def _normalize_model_list(raw_value: str, fallbacks: List[str]) -> List[str]:
    models = [item.strip() for item in str(raw_value or "").split(",") if item.strip()]
    for fallback in fallbacks:
        if fallback and fallback not in models:
            models.append(fallback)
    return models


def get_openai_image_models() -> List[str]:
    configured = os.getenv("OPENAI_IMAGE_MODELS", "")
    return _normalize_model_list(
        configured,
        [os.getenv("OPENAI_IMAGE_MODEL", DEFAULT_OPENAI_IMAGE_MODELS[0]), *DEFAULT_OPENAI_IMAGE_MODELS],
    )


def get_gemini_image_models() -> List[str]:
    configured = os.getenv("GEMINI_IMAGE_MODELS", "")
    return _normalize_model_list(
        configured,
        [os.getenv("GEMINI_IMAGE_MODEL", DEFAULT_GEMINI_IMAGE_MODELS[0]), *DEFAULT_GEMINI_IMAGE_MODELS],
    )


def get_default_image_generation_model() -> str:
    default_model = (os.getenv("DEFAULT_IMAGE_GENERATION_MODEL") or "").strip()
    if default_model:
        provider, normalized = resolve_image_generation_choice(default_model)
        if provider and normalized:
            return normalized
    openai_models = get_openai_image_models()
    if openai_models:
        return openai_models[0]
    gemini_models = get_gemini_image_models()
    return gemini_models[0] if gemini_models else ""


def resolve_image_generation_choice(model_name: str) -> Tuple[str, str]:
    requested = (model_name or "").strip()
    openai_models = get_openai_image_models()
    gemini_models = get_gemini_image_models()

    if requested in openai_models:
        return "openai", requested
    if requested in gemini_models:
        return "gemini", requested

    if openai_models:
        return "openai", openai_models[0]
    if gemini_models:
        return "gemini", gemini_models[0]
    return "", ""


def build_image_generation_options() -> Dict[str, object]:
    openai_models = get_openai_image_models()
    gemini_models = get_gemini_image_models()
    default_model = get_default_image_generation_model()
    return {
        "default_model": default_model,
        "groups": [
            {"provider": "openai", "label": "OpenAI", "models": openai_models},
            {"provider": "gemini", "label": "Gemini", "models": gemini_models},
        ],
    }


def build_reference_preserving_prompt(
    prompt: str,
    *,
    context: str = "",
) -> str:
    prompt_parts = [
        "Create a polished affiliate marketing visual using the uploaded product photos as exact references.",
        "Preserve the same product identity, silhouette, proportions, materials, colorway, finish, visible markings, labels, logos, packaging details, and functional design cues.",
        "Do not redesign, replace, morph, duplicate, remove, or hallucinate parts of the product.",
        "The hero product must remain recognizably the same real item from the source image, because product accuracy matters for affiliate conversion.",
        "You may improve styling around the product with better lighting, background, props, framing, composition, camera crop, mood, and platform-specific art direction.",
        "Keep the product as the main focus and avoid covering important product details.",
        "If text is explicitly requested, keep it secondary, tasteful, and away from the hero product.",
    ]
    normalized_context = str(context or "").strip()
    normalized_prompt = str(prompt or "").strip()
    if normalized_context:
        prompt_parts.append(f"Product context: {normalized_context}")
    if normalized_prompt:
        prompt_parts.append(f"Creative direction: {normalized_prompt}")
    return " ".join(prompt_parts)
