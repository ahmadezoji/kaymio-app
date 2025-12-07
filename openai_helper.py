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
