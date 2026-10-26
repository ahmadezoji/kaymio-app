"""Tag / hashtag normalization shared across platforms."""
from __future__ import annotations

from typing import Iterable, List


def sanitize_tag_list(raw_items: Iterable[str]) -> List[str]:
    """Strip '#', punctuation, and case-insensitive duplicates from a list of tags."""
    cleaned: List[str] = []
    seen = set()
    for raw_item in raw_items:
        token = str(raw_item or "").strip()
        if not token:
            continue
        token = token.lstrip("#").strip()
        token = token.strip(" \t\r\n.,:;!?/\\|()[]{}<>\"'`")
        if not token or token in {"&", "+", "-", "_"}:
            continue
        normalized_key = token.casefold()
        if normalized_key in seen:
            continue
        cleaned.append(token)
        seen.add(normalized_key)
    return cleaned
