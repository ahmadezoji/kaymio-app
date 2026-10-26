"""AI copy for an auto-post: store title/description/tags and the Instagram caption.

Mirrors the manual workflow (``generate_pinterest`` + ``publish_instagram``):
OpenAI caption -> "COMMENT <trigger>" CTA -> hashtags. Generated before the
Telegram approval so the user approves exactly what will be published.
"""
from __future__ import annotations

import html
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from kaymio.features.instagram_caption import (
    compose_caption_with_hashtags,
    prepend_comment_reply_caption_cta,
)
from kaymio.integrations.amazon.amazon_crawler import AmazonProduct
from kaymio.utils.openai_helper import (
    build_website_description,
    generate_caption_for_instagram,
    generate_hashtags_for_instagram,
    generate_tags_for_product_for_pintrest,
    generate_text,
)
from kaymio.utils.tags import sanitize_tag_list

STORE_TITLE_MAX_CHARS = 70


@dataclass
class AutoPostContent:
    title: str
    website_description: str
    website_tags: List[str] = field(default_factory=list)
    instagram_caption: str = ""  # final caption incl. CTA + hashtags
    image_urls: List[str] = field(default_factory=list)  # images used for website + carousel

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "AutoPostContent":
        known = {name for name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in payload.items() if key in known})


def generate_auto_post_content(
    product: AmazonProduct,
    *,
    image_count: int,
    call_to_action: str = "",
    website_boost_prompt: str = "",
) -> AutoPostContent:
    title = _store_title(product.title)
    summary = _product_summary(product)

    caption = generate_caption_for_instagram(title, summary, call_to_action=call_to_action or None)
    hashtags = sanitize_tag_list(generate_hashtags_for_instagram(title, summary))
    instagram_caption = compose_caption_with_hashtags(
        prepend_comment_reply_caption_cta(caption),
        hashtags,
    )

    return AutoPostContent(
        title=title,
        website_description=_website_description(title, summary, product.bullets, website_boost_prompt),
        website_tags=sanitize_tag_list(generate_tags_for_product_for_pintrest(title, summary)),
        instagram_caption=instagram_caption,
        image_urls=product.image_urls[: max(1, image_count)],
    )


def _store_title(amazon_title: str) -> str:
    """Amazon titles are keyword-stuffed; ask for a clean product name instead."""
    short_title = generate_text(
        amazon_title,
        context=(
            "You are an e-commerce copywriter. You turn long marketplace listings into "
            "clean, catchy product names."
        ),
        user_prompt=(
            f"Rewrite this Amazon product title as a store product name under {STORE_TITLE_MAX_CHARS} "
            "characters. Keep the brand if present, drop keyword lists, sizes and colors. "
            "Return only the name:\n{text}"
        ),
        max_tokens=40,
        temperature=0.4,
    ).strip()
    if not short_title or len(short_title) > STORE_TITLE_MAX_CHARS * 2:
        return amazon_title[:STORE_TITLE_MAX_CHARS].rstrip(" ,-|")
    return short_title


def _product_summary(product: AmazonProduct) -> str:
    parts = [product.description] + product.bullets[:5]
    return " ".join(part for part in parts if part)[:1500] or product.title


def _website_description(title: str, summary: str, bullets: List[str], boost_prompt: str) -> str:
    intro = build_website_description(title, summary, boost_prompt)
    if not bullets:
        return intro
    items = "".join(f"<li>{html.escape(bullet)}</li>" for bullet in bullets[:6])
    return f"{intro}\n\n<h4>Key features</h4>\n<ul>{items}</ul>"
