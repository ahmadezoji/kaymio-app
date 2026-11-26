"""Pick the best trending, well-rated Amazon products that kaymio.com does not sell yet."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

from kaymio.features.auto_post.catalog import WebsiteCatalog, rotate
from kaymio.features.auto_post.ranking import ai_rerank, amazon_listing_score, prefer_discounts
from kaymio.integrations.amazon.amazon_api import build_affiliate_link, fetch_product_from_canopy
from kaymio.integrations.amazon.amazon_crawler import (
    AmazonCrawlError,
    AmazonListingItem,
    AmazonProduct,
    fetch_listing,
    fetch_product,
    parse_price_value,
)
from kaymio.integrations.crawl4ai import Crawl4AIError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoveryCriteria:
    source_urls: Sequence[str]
    min_rating: float
    min_reviews: int
    max_detail_fetches: int
    canopy_fallback: bool
    lists_per_run: int = 3
    # Set while a promo event is active: its lists are crawled first, and discounted
    # products (>= min_discount_percent first) are preferred.
    priority_urls: Sequence[str] = ()
    prefer_discount: bool = False
    min_discount_percent: int = 0


def find_top_trending_products(
    criteria: DiscoveryCriteria,
    catalog: WebsiteCatalog,
    exclude_asins: Iterable[str],
    *,
    limit: int,
) -> List[AmazonProduct]:
    """Best ``limit`` products from today's Amazon lists that pass every rule.

    1. Crawl up to ``lists_per_run`` lists, rotating which ones lead each day.
    2. Score every new listing item (rank, rating, reviews, list type) -- no AI.
    3. Optionally let one cheap AI call re-order the top few by appeal.
    4. Open product pages in that order until ``limit`` products qualify.
    """
    excluded = {asin.upper() for asin in exclude_asins} | catalog.amazon_asins
    pool: List[Tuple[float, AmazonListingItem, str]] = []
    base_urls = rotate([url for url in criteria.source_urls if url not in criteria.priority_urls])
    for source_url in list(criteria.priority_urls) + base_urls[: max(1, criteria.lists_per_run)]:
        try:
            listing = fetch_listing(source_url)
        except (AmazonCrawlError, Crawl4AIError) as exc:
            logger.warning("Skipping Amazon list %s: %s", source_url, exc)
            continue
        logger.info("Amazon list %s returned %d products", source_url, len(listing))
        for item in listing:
            if item.asin in excluded or not _listing_passes(item, criteria):
                continue
            if item.title and catalog.has_similar_title(item.title):
                continue
            excluded.add(item.asin)
            pool.append((amazon_listing_score(item, source_url, len(listing)), item, source_url))

    pool.sort(key=lambda entry: entry[0], reverse=True)
    pool = ai_rerank(pool, [item.title or item.asin for _, item, _ in pool])

    products: List[AmazonProduct] = []
    max_fetches = max(criteria.max_detail_fetches, limit * 3)
    # During an event collect a few extra so the best discounts can be picked.
    wanted = limit * 2 if criteria.prefer_discount else limit
    for fetches, (_, item, source_url) in enumerate(pool):
        if len(products) >= wanted or fetches >= max_fetches:
            break
        product = _load_product(item, source_url, criteria.canopy_fallback)
        if product and _product_passes(product, criteria) and not catalog.has_similar_title(product.title):
            products.append(product)
    if criteria.prefer_discount:
        products = prefer_discounts(products, criteria.min_discount_percent)
    logger.info("Amazon discovery: %d candidate(s), %d qualified.", len(pool), len(products))
    return products[:limit]


def _listing_passes(item: AmazonListingItem, criteria: DiscoveryCriteria) -> bool:
    # Unknown values are checked again on the product page.
    if item.rating is not None and item.rating < criteria.min_rating:
        return False
    if item.review_count is not None and item.review_count < criteria.min_reviews:
        return False
    return True


def _product_passes(product: AmazonProduct, criteria: DiscoveryCriteria) -> bool:
    if not product.is_publishable:
        logger.info("Skipping %s: missing title, price or images.", product.asin)
        return False
    if product.rating is None or product.rating < criteria.min_rating:
        logger.info("Skipping %s: rating %s < %s.", product.asin, product.rating, criteria.min_rating)
        return False
    if (product.review_count or 0) < criteria.min_reviews:
        logger.info("Skipping %s: %s reviews < %s.", product.asin, product.review_count, criteria.min_reviews)
        return False
    return True


def _load_product(item: AmazonListingItem, source_url: str, canopy_fallback: bool) -> Optional[AmazonProduct]:
    product: Optional[AmazonProduct] = None
    try:
        product = fetch_product(item.asin, marketplace_url=source_url)
    except (AmazonCrawlError, Crawl4AIError) as exc:
        logger.warning("Crawling product %s failed: %s", item.asin, exc)

    if canopy_fallback and (product is None or not product.is_publishable):
        product = _fill_from_canopy(item, product)
    if product is None:
        return None

    # Listing data fills anything the product page didn't expose.
    product.source_list_url = source_url
    product.source_rank = item.rank
    if product.rating is None:
        product.rating = item.rating
    if product.review_count is None:
        product.review_count = item.review_count
    if not product.price:
        product.price = item.price
    return product


def _fill_from_canopy(item: AmazonListingItem, product: Optional[AmazonProduct]) -> Optional[AmazonProduct]:
    if not os.getenv("CANOPY_API_KEY"):
        return product
    try:
        canopy = fetch_product_from_canopy(item.asin, "US")
    except Exception as exc:
        logger.warning("Canopy fallback failed for %s: %s", item.asin, exc)
        return product

    if product is None:
        product = AmazonProduct(
            asin=item.asin,
            title=canopy.get("title") or item.title,
            product_url=f"https://www.amazon.com/dp/{item.asin}",
            affiliate_link=build_affiliate_link(item.asin),
        )
    if not product.image_urls:
        product.image_urls = list(canopy.get("image_urls") or [])
    if not parse_price_value(product.price):
        product.price = str(canopy.get("price") or "")
    if not product.category:
        product.category = str(canopy.get("category") or "")
    if not product.description and not product.bullets:
        product.description = str(canopy.get("description") or "")
    return product
