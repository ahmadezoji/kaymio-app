"""OpenAI helpers for Pinterest copywriting."""
from __future__ import annotations

import json
import logging
import os
from typing import Dict, List, Optional

from openai import OpenAI

logger = logging.getLogger(__name__)

_client: OpenAI | None = None


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
                {"role": "system", "content": [{"type": "text", "text": system_prompt}]},
                {"role": "user", "content": [{"type": "text", "text": user_prompt}]},
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
        "type": "text",
        "text": (
            f"Title: {title}\nDescription: {description}\nExtra Pinterest Context: {extra_text}"
        ),
    }

    try:
        response = client.responses.create(
            model=os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini"),
            temperature=0.5,
            input=[
                {"role": "system", "content": [{"type": "text", "text": system_prompt}]},
                {"role": "user", "content": [content, {"type": "text", "text": user_prompt}]},
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
                {"role": "system", "content": [{"type": "text", "text": system_prompt}]},
                {"role": "user", "content": [{"type": "text", "text": prompt}]},
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
