"""AliExpress hot product -> Telegram approval -> kaymio.com + Instagram + Pinterest.

Discovery uses the official AliExpress Affiliate API (not Crawl4AI): AliExpress
pages are rendered client-side and guarded by slider captchas, while the API
returns sales volume, feedback rate, images, price and a commission-tracked
``promotion_link`` directly. Everything after discovery (AI copy, Telegram
approval, website, AI hero image, Instagram, Pinterest) is the shared auto-post
pipeline.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from kaymio.config import TRUTHY_VALUES
from kaymio.database.auto_post import known_candidate_asins
from kaymio.features.auto_post import config
from kaymio.features.auto_post.approval import notify
from kaymio.features.auto_post.task import expire_and_propose
from kaymio.integrations.aliexpress import (
    AliExpressApiError,
    AliExpressClient,
    extract_item_ids,
    get_aliexpress_client,
)
from kaymio.integrations.amazon.amazon_crawler import AmazonProduct
from kaymio.integrations.telegram import get_telegram_settings
from kaymio.tasks import OptionField, ScheduledTask, TaskSettings, register_task
from kaymio.wordpress.wordpress_api_helper import _fetch_all_woocommerce_products

logger = logging.getLogger(__name__)

TASK_KEY = "aliexpress_auto_post"
SKU_PREFIX = "AE-"
# AliExpress product ids are 16 digits; the candidate key column holds 16 chars.
MAX_PRODUCT_ID_LENGTH = 16

DEFAULT_QUERIES = (
    "kitchen gadgets",
    "home organization",
    "phone accessories",
    "smart home gadgets",
    "beauty tools",
    "car accessories",
    "pet supplies",
    "desk gadgets",
)

ENABLED = os.getenv("ALIEXPRESS_AUTO_POST_ENABLED", "0").lower() in TRUTHY_VALUES
RUN_TIME = os.getenv("ALIEXPRESS_AUTO_POST_TIME", "16:00")
QUERIES = os.getenv("ALIEXPRESS_AUTO_POST_QUERIES", "\n".join(DEFAULT_QUERIES))
MIN_ORDERS = int(os.getenv("ALIEXPRESS_AUTO_POST_MIN_ORDERS", "500"))
MIN_POSITIVE_RATE = float(os.getenv("ALIEXPRESS_AUTO_POST_MIN_POSITIVE_RATE", "90"))
MIN_PRICE = float(os.getenv("ALIEXPRESS_AUTO_POST_MIN_PRICE", "3"))
MAX_PRICE = float(os.getenv("ALIEXPRESS_AUTO_POST_MAX_PRICE", "80"))
# Try the Advanced-API hot product list first (falls back automatically when not granted).
USE_HOT_PRODUCTS = os.getenv("ALIEXPRESS_USE_HOT_PRODUCTS", "1").lower() in TRUTHY_VALUES
# Bulk/reseller listings look bad on the store; skip titles containing any of these words.
EXCLUDED_TITLE_WORDS = tuple(
    word.strip().lower()
    for word in os.getenv("ALIEXPRESS_AUTO_POST_EXCLUDED_WORDS", "wholesale,bulk,dropshipping,lot of").split(",")
    if word.strip()
)
WEBSITE_BOOST_PROMPT = os.getenv(
    "ALIEXPRESS_AUTO_POST_WEBSITE_BOOST_PROMPT",
    "Lead with the main benefit, say who it is for, and mention it is a best-seller on AliExpress.",
).strip()


@dataclass(frozen=True)
class AliExpressCriteria:
    queries: Sequence[str]
    min_orders: int
    min_positive_rate: float
    min_price: float
    max_price: float


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def split_queries(raw: str) -> List[str]:
    return [line.strip() for line in re.split(r"[\n,]+", raw or "") if line.strip()]


def find_website_aliexpress_ids() -> Set[str]:
    """AliExpress ids already on kaymio.com (our ``AE-<id>`` SKUs or item URLs)."""
    result = _fetch_all_woocommerce_products()
    if result.get("error"):
        raise RuntimeError(f"Cannot check existing kaymio.com products: {result['error']}")
    ids: Set[str] = set()
    for product in result.get("items") or []:
        sku = str(product.get("sku") or "")
        if sku.upper().startswith(SKU_PREFIX):
            ids.add(sku[len(SKU_PREFIX):])
        haystack = " ".join(str(product.get(key) or "") for key in ("external_url", "permalink", "description"))
        ids.update(extract_item_ids(haystack))
    return ids


def rotate(items: Sequence[str], today: Optional[dt.date] = None) -> List[str]:
    """Start from a different query each day so posts cover different niches."""
    if not items:
        return []
    start = (today or dt.date.today()).toordinal() % len(items)
    return list(items[start:]) + list(items[:start])


def find_top_aliexpress_product(
    criteria: AliExpressCriteria,
    exclude_ids: Iterable[str],
    *,
    client: Optional[AliExpressClient] = None,
) -> Optional[AmazonProduct]:
    """Best-selling product of the first query (in today's rotation) that passes every rule."""
    client = client or get_aliexpress_client()
    excluded = {str(pid) for pid in exclude_ids}
    for query in rotate(criteria.queries):
        try:
            items = _search(client, query, criteria)
        except AliExpressApiError as exc:
            logger.warning("AliExpress query %r failed: %s", query, exc)
            continue
        logger.info("AliExpress query %r returned %d products", query, len(items))
        for rank, item in enumerate(items, start=1):
            product = product_from_api(item, query=query, rank=rank)
            if product is None or product.asin in excluded:
                continue
            if _passes(product, criteria):
                return product
    return None


def _search(client: AliExpressClient, query: str, criteria: AliExpressCriteria) -> List[Dict[str, Any]]:
    # A purely numeric line is treated as an AliExpress category id.
    search = {"category_ids": query} if query.isdigit() else {"keywords": query}
    if USE_HOT_PRODUCTS:
        try:
            return client.query_hot_products(max_sale_price=criteria.max_price, **search)
        except AliExpressApiError as exc:
            if exc.code != "InsufficientPermission":
                raise
            logger.debug("Hot product API not granted; using product.query sorted by sales.")
    items = client.query_products(
        min_sale_price=criteria.min_price or None,
        max_sale_price=criteria.max_price or None,
        **search,
    )
    # The API sorts by volume already; re-sort defensively (hot API ignores sort sometimes).
    return sorted(items, key=lambda item: _int(item.get("lastest_volume")) or 0, reverse=True)


def _passes(product: AmazonProduct, criteria: AliExpressCriteria) -> bool:
    if not product.is_publishable:
        return False
    if any(word in product.title.lower() for word in EXCLUDED_TITLE_WORDS):
        return False
    if (product.orders or 0) < criteria.min_orders:
        return False
    if product.positive_rate is None or product.positive_rate < criteria.min_positive_rate:
        return False
    price = product.price_value or 0
    if criteria.min_price and price < criteria.min_price:
        return False
    if criteria.max_price and price > criteria.max_price:
        return False
    return True


def product_from_api(item: Dict[str, Any], *, query: str = "", rank: Optional[int] = None) -> Optional[AmazonProduct]:
    """Map an API product to the shared product model (``asin`` = AliExpress id)."""
    product_id = str(item.get("product_id") or "").strip()
    if not product_id.isdigit() or len(product_id) > MAX_PRODUCT_ID_LENGTH:
        return None
    images = [item.get("product_main_image_url")] + list(
        ((item.get("product_small_image_urls") or {}).get("string")) or []
    )
    image_urls = list(dict.fromkeys(url for url in images if url))
    category = item.get("second_level_category_name") or item.get("first_level_category_name") or ""
    product_url = f"https://www.aliexpress.com/item/{product_id}.html"
    return AmazonProduct(
        asin=product_id,
        title=str(item.get("product_title") or "").strip(),
        product_url=product_url,
        affiliate_link=str(item.get("promotion_link") or product_url),
        price=_format_price(item),
        image_urls=image_urls,
        category=str(category),
        brand=str(item.get("shop_name") or ""),
        source_list_url=f"aliexpress:{query}" if query else "",
        source_rank=rank,
        source="aliexpress",
        orders=_int(item.get("lastest_volume")),
        positive_rate=_percent(item.get("evaluate_rate")),
        commission_rate=str(item.get("commission_rate") or ""),
    )


def _format_price(item: Dict[str, Any]) -> str:
    value = item.get("target_sale_price") or item.get("target_app_sale_price") or item.get("sale_price")
    currency = item.get("target_sale_price_currency") or item.get("sale_price_currency") or ""
    if not value:
        return ""
    return f"${value}" if currency == "USD" else f"{value} {currency}".strip()


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _percent(value: Any) -> Optional[float]:
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    return float(match.group(0)) if match else None


# --------------------------------------------------------------------------- #
# Scheduled task
# --------------------------------------------------------------------------- #
def run_aliexpress_auto_post(settings: TaskSettings) -> None:
    if not get_telegram_settings().is_configured:
        raise RuntimeError("Telegram is not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).")
    client = get_aliexpress_client()
    if not client.settings.is_configured:
        raise RuntimeError("AliExpress is not configured (ALIEXPRESS_APP_KEY / ALIEXPRESS_APP_SECRET).")
    if not client.settings.tracking_id:
        logger.warning("ALIEXPRESS_TRACKING_ID is empty: links may not be credited to your tracking id.")

    options = settings.options
    criteria = AliExpressCriteria(
        queries=split_queries(options["queries"]),
        min_orders=int(options["min_orders"]),
        min_positive_rate=float(options["min_positive_rate"]),
        min_price=float(options["min_price"]),
        max_price=float(options["max_price"]),
    )
    exclude = find_website_aliexpress_ids() | known_candidate_asins()
    product = find_top_aliexpress_product(criteria, exclude, client=client)
    if product is None:
        notify(
            "🤷 AliExpress auto-post: no new product matched today "
            f"(≥ {criteria.min_orders} orders, ≥ {criteria.min_positive_rate:g}% positive, "
            f"${criteria.min_price:g}–${criteria.max_price:g}, not on kaymio.com)."
        )
        return
    expire_and_propose(product, image_count=int(options["image_count"]), website_boost_prompt=WEBSITE_BOOST_PROMPT)


ALIEXPRESS_AUTO_POST_TASK = ScheduledTask(
    key=TASK_KEY,
    label="Instagram auto-post (AliExpress hot products)",
    description=(
        "Finds the best-selling, well-reviewed AliExpress product (Affiliate API) that kaymio.com "
        "doesn't have yet, asks for approval on Telegram, then adds it to the website with your "
        "AliExpress affiliate link, generates an AI hero image, posts an Instagram carousel and "
        "pins the AI image on Pinterest."
    ),
    run=run_aliexpress_auto_post,
    default_enabled=ENABLED,
    default_run_time=RUN_TIME,
    option_fields=(
        OptionField("image_count", "Images per post", "int", config.IMAGE_COUNT, min=1, max=10,
                    help="Carousel size (Instagram allows up to 10)."),
        OptionField("min_orders", "Minimum recent orders", "int", MIN_ORDERS, min=0),
        OptionField("min_positive_rate", "Minimum positive feedback (%)", "float", MIN_POSITIVE_RATE,
                    min=0, max=100),
        OptionField("min_price", "Minimum price (USD)", "float", MIN_PRICE, min=0),
        OptionField("max_price", "Maximum price (USD)", "float", MAX_PRICE, min=0),
        OptionField("queries", "Keywords or category ids (one per line)", "text", QUERIES,
                    help="A different line leads each day; the best seller that qualifies wins."),
    ),
)


def register_aliexpress_auto_post_task() -> ScheduledTask:
    return register_task(ALIEXPRESS_AUTO_POST_TASK)
