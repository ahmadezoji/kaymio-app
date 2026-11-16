"""What kaymio.com already sells, loaded once per run and shared by every source.

Used to skip products that are already on the site (by Amazon ASIN or AliExpress
id) and products whose title is too similar to an existing one -- a cheap word
overlap check, no AI involved.
"""
from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import dataclass, field
from typing import FrozenSet, List, Optional, Sequence, Set

from kaymio.integrations.aliexpress import extract_item_ids
from kaymio.integrations.amazon.amazon_crawler import extract_asins_from_text
from kaymio.wordpress.wordpress_api_helper import _fetch_all_woocommerce_products

ALIEXPRESS_SKU_PREFIX = "AE-"
# Only these product fields are needed; asking for less keeps listing a large shop fast.
CATALOG_FIELDS = ("id", "name", "external_url", "sku", "permalink")
BARE_ASIN_RE = re.compile(r"\b(B0[A-Z0-9]{8})\b")
TOKEN_RE = re.compile(r"[a-z]+")
# Share of distinct title words two products must have in common to count as "the same".
SIMILAR_TITLE_THRESHOLD = float(os.getenv("AUTO_POST_SIMILAR_TITLE_THRESHOLD", "0.6"))
STOP_WORDS = frozenset(
    "the and for with from your you our this that set pack pcs piece pieces new kit "
    "inch inches size large small mini black white".split()
)


def title_tokens(title: str) -> FrozenSet[str]:
    return frozenset(
        word for word in TOKEN_RE.findall((title or "").lower()) if len(word) > 2 and word not in STOP_WORDS
    )


@dataclass
class WebsiteCatalog:
    amazon_asins: Set[str] = field(default_factory=set)
    aliexpress_ids: Set[str] = field(default_factory=set)
    title_token_sets: List[FrozenSet[str]] = field(default_factory=list)

    def has_similar_title(self, title: str, threshold: float = SIMILAR_TITLE_THRESHOLD) -> bool:
        tokens = title_tokens(title)
        if len(tokens) < 2:
            return False
        for existing in self.title_token_sets:
            if existing and len(tokens & existing) / len(tokens | existing) >= threshold:
                return True
        return False


def load_website_catalog() -> WebsiteCatalog:
    """Read every kaymio.com product once.

    Raises RuntimeError when WooCommerce can't be read: publishing a duplicate is
    worse than skipping a run.
    """
    result = _fetch_all_woocommerce_products(fields=CATALOG_FIELDS)
    if result.get("error"):
        raise RuntimeError(f"Cannot check existing kaymio.com products: {result['error']}")
    catalog = WebsiteCatalog()
    for product in result.get("items") or []:
        sku = str(product.get("sku") or "")
        links = " ".join(str(product.get(key) or "") for key in ("external_url", "sku", "permalink"))
        catalog.amazon_asins.update(extract_asins_from_text(links))
        catalog.amazon_asins.update(BARE_ASIN_RE.findall(links.upper()))
        if sku.upper().startswith(ALIEXPRESS_SKU_PREFIX):
            catalog.aliexpress_ids.add(sku[len(ALIEXPRESS_SKU_PREFIX):])
        catalog.aliexpress_ids.update(extract_item_ids(links))
        tokens = title_tokens(str(product.get("name") or ""))
        if tokens:
            catalog.title_token_sets.append(tokens)
    return catalog


def rotate(items: Sequence[str], today: Optional[dt.date] = None) -> List[str]:
    """Start from a different entry each day so runs cover different niches."""
    if not items:
        return []
    start = (today or dt.date.today()).toordinal() % len(items)
    return list(items[start:]) + list(items[:start])
