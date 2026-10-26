"""Instagram caption assembly shared by manual publishing and automated posts.

Every feed/reel caption leads with the "comment <trigger> for the link" CTA so
the comment webhook can DM the affiliate link, then ends with the hashtags.
"""
from __future__ import annotations

from typing import Iterable

from kaymio.config import INSTAGRAM_COMMENT_REPLY_DISPLAY_TRIGGER


def build_comment_reply_caption_cta() -> str:
    trigger = str(INSTAGRAM_COMMENT_REPLY_DISPLAY_TRIGGER or "buy").strip().upper()
    return f'COMMENT "{trigger}" FOR PRODUCT INFO + LINK'


def prepend_comment_reply_caption_cta(caption: str) -> str:
    base_caption = str(caption or "").strip()
    cta = build_comment_reply_caption_cta()
    if base_caption.casefold().startswith(cta.casefold()):
        return base_caption
    return f"{cta}\n\n{base_caption}" if base_caption else cta


def compose_caption_with_hashtags(caption: str, hashtags: Iterable[str]) -> str:
    """Append a ``#tag #tag`` block to the caption (tags are normalized, spaces removed)."""
    normalized_tags = [str(tag).lstrip("#").replace(" ", "") for tag in hashtags if tag]
    base_caption = str(caption or "").strip()
    if not normalized_tags:
        return base_caption
    tags_block = " ".join(f"#{tag}" for tag in normalized_tags)
    return f"{base_caption}\n\n{tags_block}" if base_caption else tags_block
