"""Pick the top trending, well-rated Amazon product that kaymio.com does not sell yet."""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Set

from kaymio.integrations.amazon.amazon_api import build_affiliate_link, fetch_product_from_canopy
from kaymio.integrations.amazon.amazon_crawler import (
    AmazonCrawlError,
    AmazonListingItem,
    AmazonProduct,
    extract_asins_from_text,
    fetch_listing,
    fetch_product,
    parse_price_value,
)
from kaymio.integrations.crawl4ai import Crawl4AIError
from kaymio.wordpress.wordpress_api_helper import _fetch_all_woocommerce_products

logger = logging.getLogger(__name__)

BARE_ASIN_RE = re.compile(r"\b(B0[A-Z0-9]{8})\b")


@dataclass(frozen=True)
class DiscoveryCriteria:
    source_urls: Sequence[str]
    min_rating: float
    min_reviews: int
    max_detail_fetches: int
    canopy_fallback: bool


def find_website_amazon_asins() -> Set[str]:
    """ASINs of every WooCommerce product whose affiliate/SKU data points to Amazon.

    Raises RuntimeError when WooCommerce can't be read: publishing a duplicate is
    worse than skipping a run.
    """
    result = _fetch_all_woocommerce_products()
    if result.get("error"):
        raise RuntimeError(f"Cannot check existing kaymio.com products: {result['error']}")
    asins: Set[str] = set()
    for product in result.get("items") or []:
        haystack = " ".join(
            str(product.get(key) or "") for key in ("external_url", "sku", "permalink")
        )
        asins.update(extract_asins_from_text(haystack))
        asins.update(match.upper() for match in BARE_ASIN_RE.findall(haystack.upper()))
    return asins


def find_top_trending_product(
    criteria: DiscoveryCriteria,
    exclude_asins: Iterable[str],
) -> Optional[AmazonProduct]:
    """Walk Amazon's lists in order and return the first product passing every rule.

    Source lists are tried in configured order (hottest first by default) and
    products in Amazon's own rank order, so "top 1" means the highest-ranked
    product that is new to the site, rated >= min_rating with >= min_reviews,
    and has a price and images.
    """
    excluded = {asin.upper() for asin in exclude_asins}
    detail_fetches = 0
    for source_url in criteria.source_urls:
        try:
            listing = fetch_listing(source_url)
        except (AmazonCrawlError, Crawl4AIError) as exc:
            logger.warning("Skipping Amazon list %s: %s", source_url, exc)
            continue
        logger.info("Amazon list %s returned %d products", source_url, len(listing))

        for item in listing:
            if item.asin in excluded or not _listing_passes(item, criteria):
                continue
            if detail_fetches >= criteria.max_detail_fetches:
                logger.info("Reached max product detail fetches (%d).", criteria.max_detail_fetches)
                return None
            detail_fetches += 1
            excluded.add(item.asin)

            product = _load_product(item, source_url, criteria.canopy_fallback)
            if product and _product_passes(product, criteria):
                return product
    return None


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
