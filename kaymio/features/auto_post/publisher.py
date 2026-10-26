"""Publish an approved candidate to kaymio.com (WooCommerce) and Instagram.

Each step stores its result as soon as it succeeds, so a retry after a
partial failure (e.g. website done, Instagram failed) only redoes what's left.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Dict, List

from kaymio.database.auto_post import (
    STATUS_FAILED,
    STATUS_PUBLISHED,
    get_candidate,
    update_candidate,
)
from kaymio.database.instagram_state import register_media_reply_route
from kaymio.features.auto_post.config import FEED_IMAGE_SIZE
from kaymio.features.auto_post.content import AutoPostContent
from kaymio.integrations.amazon.amazon_crawler import AmazonProduct
from kaymio.integrations.instagram.instagram_api_helper import (
    get_latest_instagram_media_id,
    publish_instagram_carousel,
    publish_instagram_post,
)
from kaymio.utils.image_utils import pad_to_canvas
from kaymio.utils.media_storage import (
    build_public_media_url,
    delete_media,
    download_image_bytes,
    save_generated_image,
)
from kaymio.wordpress.wordpress_api_helper import (
    create_woocommerce_product,
    find_wordpress_nearest_category,
)

logger = logging.getLogger(__name__)


def publish_candidate(candidate_id: int) -> Dict[str, Any]:
    """Run the website + Instagram publish for a candidate already marked ``publishing``.

    Returns the updated candidate; on failure the candidate is marked ``failed``
    with the error and the exception is re-raised.
    """
    candidate = get_candidate(candidate_id)
    if candidate is None:
        raise LookupError(f"Auto-post candidate {candidate_id} not found.")
    product = AmazonProduct.from_dict(candidate["product_data"])
    content = AutoPostContent.from_dict(candidate["content"])

    try:
        website_url = candidate.get("website_product_url") or ""
        if not website_url:
            website_url = publish_to_website(product, content)
            update_candidate(candidate_id, website_product_url=website_url)

        media_id = candidate.get("instagram_media_id") or ""
        if not media_id:
            media_id = publish_to_instagram(product, content, website_url=website_url)
            update_candidate(candidate_id, instagram_media_id=media_id)
    except Exception as exc:
        logger.exception("Auto-post publish failed for candidate %s", candidate_id)
        update_candidate(candidate_id, status=STATUS_FAILED, error=f"{type(exc).__name__}: {exc}")
        raise

    return update_candidate(
        candidate_id,
        status=STATUS_PUBLISHED,
        error=None,
        published_at=dt.datetime.utcnow(),
    )


def publish_to_website(product: AmazonProduct, content: AutoPostContent) -> str:
    """Create the WooCommerce external product and return its permalink."""
    price_value = product.price_value
    if not price_value:
        raise ValueError(f"Product {product.asin} has no usable price ({product.price!r}).")

    category_id = None
    if product.category:
        try:
            category_id = find_wordpress_nearest_category(product.category)
        except Exception as exc:
            logger.warning("Category lookup failed for %r: %s", product.category, exc)

    # WooCommerce sideloads remote image URLs itself, so no local download is needed.
    product_url = create_woocommerce_product(
        name=content.title,
        description=content.website_description,
        price=f"{price_value:.2f}",
        tags=content.website_tags,
        images=list(content.image_urls),
        affiliate_link=product.affiliate_link,
        category_id=category_id,
    )
    if not product_url:
        raise RuntimeError("WooCommerce did not create the product (check WC credentials and logs).")
    logger.info("Auto-post: created WooCommerce product %s for %s", product_url, product.asin)
    return product_url


def publish_to_instagram(product: AmazonProduct, content: AutoPostContent, *, website_url: str) -> str:
    """Publish the product images as a feed carousel (single post if only one image)."""
    relative_paths: List[str] = []
    try:
        for image_url in content.image_urls:
            try:
                framed = pad_to_canvas(download_image_bytes(image_url), FEED_IMAGE_SIZE)
            except Exception as exc:
                logger.warning("Skipping image %s: %s", image_url, exc)
                continue
            relative_paths.append(save_generated_image(framed, suffix=".jpg"))
        if not relative_paths:
            raise RuntimeError("None of the product images could be prepared for Instagram.")

        public_urls = [build_public_media_url(path) for path in relative_paths]
        if len(public_urls) >= 2:
            response = publish_instagram_carousel(image_urls=public_urls, caption=content.instagram_caption)
        else:
            response = publish_instagram_post(image_url=public_urls[0], caption=content.instagram_caption)
    finally:
        # Instagram has fetched the images once the container is published.
        for path in relative_paths:
            delete_media(path)

    media_id = str(response.get("id") or get_latest_instagram_media_id() or "")
    if not media_id:
        raise RuntimeError("Instagram did not return a media id.")

    # Lets "comment BUY" auto-replies DM the affiliate link for this post.
    register_media_reply_route(
        media_id,
        {
            "product_id": f"amazon:{product.asin}",
            "affiliate_link": product.affiliate_link,
            "product_url": website_url,
            "title": content.title,
            "description": product.bullets[0] if product.bullets else product.description,
        },
        reply_surface="feed",
    )
    logger.info("Auto-post: published Instagram media %s for %s", media_id, product.asin)
    return media_id
