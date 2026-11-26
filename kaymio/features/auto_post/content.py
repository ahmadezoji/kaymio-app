"""AI copy for an auto-post: store title/description/tags and the Instagram caption.

Mirrors the manual workflow (``generate_pinterest`` + ``publish_instagram``):
OpenAI caption -> "COMMENT <trigger>" CTA -> hashtags. Generated before the
Telegram approval so the user approves exactly what will be published.
"""
from __future__ import annotations

import html
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from kaymio.features.instagram_caption import (
    compose_caption_with_hashtags,
    prepend_comment_reply_caption_cta,
)
from kaymio.features.events import PromoEvent
from kaymio.integrations.amazon.amazon_crawler import AmazonProduct
from kaymio.utils.openai_helper import (
    build_website_description,
    generate_caption_for_instagram,
    generate_hashtags_for_instagram,
    generate_product_marketing_bundle,
    generate_pinterest_description,
    generate_pinterest_title,
    generate_product_scene_concept,
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
    image_urls: List[str] = field(default_factory=list)  # original images: website + carousel
    pinterest_title: str = ""
    pinterest_description: str = ""  # hashtags from website_tags are appended on publish
    ai_image_concept: str = ""  # scene for the AI hero image (environment only, product unchanged)
    # Promo event active when the copy was written (empty outside events).
    event_name: str = ""
    event_line: str = ""  # e.g. "🖤 Black Friday deal: -35% off"
    event_category: str = ""  # extra website category, e.g. "Black Friday Deals"
    # Set from the "Use AI image" toggle on the Telegram approval message (off by default).
    use_ai_image: bool = False

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
    include_ai_image_concept: bool = False,
    event: Optional[PromoEvent] = None,
) -> AutoPostContent:
    """Write all copy with one OpenAI request; any missing piece falls back to its own helper.

    During a promo event the event name is passed to that same request (no extra AI call),
    and the event line + hashtags are added deterministically.
    """
    summary = _product_summary(product)
    bundle = generate_product_marketing_bundle(
        product.title,
        summary,
        source_label=product.source_label,
        call_to_action=call_to_action,
        website_boost_prompt=website_boost_prompt,
        include_scene_concept=include_ai_image_concept,
        occasion=event.name if event else "",
    )
    event_line = event.caption_for(product.discount_percent) if event else ""

    title = _bundle_text(bundle, "store_title")
    if not title or len(title) > STORE_TITLE_MAX_CHARS * 2:
        title = _store_title(product.title, product.source_label)
    caption = _bundle_text(bundle, "instagram_caption") or generate_caption_for_instagram(
        title, summary, call_to_action=call_to_action or None
    )
    hashtags = sanitize_tag_list(
        (event.hashtags if event else [])
        + (_bundle_list(bundle, "instagram_hashtags") or generate_hashtags_for_instagram(title, summary))
    )
    if event_line:
        caption = f"{event_line}\n\n{caption}"
    pinterest_title = _bundle_text(bundle, "pinterest_title") or generate_pinterest_title(title, summary)
    concept = ""
    if include_ai_image_concept:
        concept = _bundle_text(bundle, "scene_concept") or generate_product_scene_concept(title, summary)

    return AutoPostContent(
        title=title,
        website_description=_website_description(
            _bundle_text(bundle, "website_description") or build_website_description(title, summary, website_boost_prompt),
            product.bullets,
        ),
        website_tags=sanitize_tag_list(
            _bundle_list(bundle, "tags") or generate_tags_for_product_for_pintrest(title, summary)
        ),
        instagram_caption=compose_caption_with_hashtags(prepend_comment_reply_caption_cta(caption), hashtags),
        image_urls=product.image_urls[: max(1, image_count)],
        pinterest_title=pinterest_title,
        pinterest_description=_with_event_line(
            event_line,
            _bundle_text(bundle, "pinterest_description") or generate_pinterest_description(pinterest_title, summary),
        ),
        ai_image_concept=concept,
        event_name=event.name if event else "",
        event_line=event_line,
        event_category=event.website_category if event else "",
    )


def _with_event_line(event_line: str, text: str) -> str:
    return f"{event_line} {text}".strip() if event_line else text


def _bundle_text(bundle: Dict[str, Any], key: str) -> str:
    value = bundle.get(key)
    return value.strip() if isinstance(value, str) else ""


def _bundle_list(bundle: Dict[str, Any], key: str) -> List[str]:
    value = bundle.get(key)
    return [str(item) for item in value if str(item).strip()] if isinstance(value, list) else []


def _store_title(amazon_title: str, source_label: str = "Amazon") -> str:
    """Marketplace titles are keyword-stuffed; ask for a clean product name instead."""
    short_title = generate_text(
        amazon_title,
        context=(
            "You are an e-commerce copywriter. You turn long marketplace listings into "
            "clean, catchy product names."
        ),
        user_prompt=(
            f"Rewrite this {source_label} product title as a store product name under {STORE_TITLE_MAX_CHARS} "
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


def _website_description(intro: str, bullets: List[str]) -> str:
    if not bullets:
        return intro
    items = "".join(f"<li>{html.escape(bullet)}</li>" for bullet in bullets[:6])
    return f"{intro}\n\n<h4>Key features</h4>\n<ul>{items}</ul>"
