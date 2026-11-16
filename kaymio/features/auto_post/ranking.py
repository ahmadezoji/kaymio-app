"""Rank discovery candidates so the best ones are suggested, not just the first match.

Scores are plain formulas (no AI). One optional, batched OpenAI call re-orders
only the top few by "is this an exciting, Instagram-worthy product?" -- a single
short text request per run (fractions of a cent).
"""
from __future__ import annotations

import logging
import math
import os
from typing import List, Optional, Sequence, TypeVar

from kaymio.config import TRUTHY_VALUES
from kaymio.integrations.amazon.amazon_crawler import AmazonListingItem
from kaymio.utils.openai_helper import rank_titles_by_appeal

logger = logging.getLogger(__name__)

AI_RERANK_ENABLED = os.getenv("AUTO_POST_AI_RERANK", "1").lower() in TRUTHY_VALUES
AI_RERANK_TOP_N = int(os.getenv("AUTO_POST_AI_RERANK_TOP_N", "8"))

# Movers & Shakers = fastest-rising (most "hot"), then new releases, then all-time best sellers.
AMAZON_LIST_WEIGHTS = (
    ("movers-and-shakers", 1.0),
    ("new-releases", 0.9),
    ("bestsellers", 0.8),
)
DEFAULT_LIST_WEIGHT = 0.85

T = TypeVar("T")


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def amazon_listing_score(item: AmazonListingItem, source_url: str, list_size: int) -> float:
    """0..1: Amazon's own rank in the list, star rating and review volume, weighted by list type."""
    rank_score = 1.0 - (item.rank - 1) / max(list_size, 1)
    rating_score = _clamp((item.rating - 3.5) / 1.5) if item.rating is not None else 0.5
    reviews_score = _clamp(math.log10((item.review_count or 0) + 1) / 5) if item.review_count is not None else 0.4
    weight = next((w for marker, w in AMAZON_LIST_WEIGHTS if marker in source_url), DEFAULT_LIST_WEIGHT)
    return weight * (0.45 * rank_score + 0.30 * rating_score + 0.25 * reviews_score)


def aliexpress_score(orders: Optional[int], positive_rate: Optional[float], commission_rate: str) -> float:
    """0..1: recent orders (log scale), positive feedback and commission."""
    orders_score = _clamp(math.log10((orders or 0) + 1) / 5)
    feedback_score = _clamp(((positive_rate or 0) - 85) / 15)
    try:
        commission = float(str(commission_rate).rstrip("%") or 0)
    except ValueError:
        commission = 0.0
    commission_score = _clamp(commission / 15)
    return 0.5 * orders_score + 0.3 * feedback_score + 0.2 * commission_score


def ai_rerank(candidates: Sequence[T], titles: Sequence[str]) -> List[T]:
    """Re-order the top ``AI_RERANK_TOP_N`` candidates by AI appeal; the rest keep their order.

    Falls back to the given (score) order when disabled or when the AI answer is unusable.
    """
    if not AI_RERANK_ENABLED or len(candidates) < 2:
        return list(candidates)
    head = list(candidates[:AI_RERANK_TOP_N])
    order = rank_titles_by_appeal(list(titles[: len(head)]))
    if sorted(order) != list(range(len(head))):
        logger.info("AI re-rank skipped (unusable answer); keeping score order.")
        return list(candidates)
    return [head[index] for index in order] + list(candidates[len(head):])
