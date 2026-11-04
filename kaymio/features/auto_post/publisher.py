"""Publish an approved candidate: kaymio.com, AI hero image, Instagram, Pinterest.

Order: website (original product images) -> AI hero image generated from the
primary photo (only when "Use AI image" was ticked on Telegram) -> Instagram
carousel (AI image first when used, then the originals) -> Pinterest pin (the AI
image, or the original main image, linking to the affiliate URL).

Each step stores its result as soon as it succeeds, so a retry after a
partial failure (e.g. website done, Instagram failed) only redoes what's left.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Dict, List, Optional

from kaymio.database.auto_post import (
    STATUS_FAILED,
    STATUS_PUBLISHED,
    get_candidate,
    update_candidate,
)
from kaymio.database.instagram_state import register_media_reply_route
from kaymio.features.auto_post import config
from kaymio.features.auto_post.content import AutoPostContent
from kaymio.integrations.amazon.amazon_crawler import AmazonProduct
from kaymio.integrations.instagram.instagram_api_helper import (
    get_latest_instagram_media_id,
    publish_instagram_carousel,
    publish_instagram_post,
)
from kaymio.integrations.pintrest.pinterest_helper import create_pinterest_pin
from kaymio.utils.image_utils import cover_to_size, pad_to_canvas
from kaymio.utils.media_storage import (
    build_public_media_url,
    delete_media,
    download_image_bytes,
    load_media,
    save_generated_image,
)
from kaymio.utils.openai_helper import edit_image, generate_product_scene_concept
from kaymio.wordpress.wordpress_api_helper import (
    create_woocommerce_product,
    find_wordpress_nearest_category,
)

logger = logging.getLogger(__name__)


def publish_candidate(candidate_id: int) -> Dict[str, Any]:
    """Run every publish step for a candidate already marked ``publishing``.

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

        ai_image: Optional[bytes] = None
        if config.AI_IMAGE_ENABLED and content.use_ai_image:
            ai_image = _ensure_ai_image(candidate_id, candidate.get("ai_image_path") or "", product, content)

        media_id = candidate.get("instagram_media_id") or ""
        if not media_id:
            media_id = publish_to_instagram(product, content, website_url=website_url, lead_image=ai_image)
            update_candidate(candidate_id, instagram_media_id=media_id)

        if config.PINTEREST_ENABLED and not candidate.get("pinterest_pin_url"):
            pin_url = publish_to_pinterest(product, content, ai_image)
            update_candidate(candidate_id, pinterest_pin_url=pin_url)
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
        # AliExpress affiliate links are opaque short links; the SKU keeps the id for de-duplication.
        sku=f"AE-{product.asin}" if product.source == "aliexpress" else None,
    )
    if not product_url:
        raise RuntimeError("WooCommerce did not create the product (check WC credentials and logs).")
    logger.info("Auto-post: created WooCommerce product %s for %s", product_url, product.asin)
    return product_url


def build_ai_image_prompt(product: AmazonProduct, content: AutoPostContent) -> str:
    """Strict keep-the-product-identical prompt plus the product-specific scene concept."""
    concept = content.ai_image_concept or generate_product_scene_concept(content.title, _summary(product))
    if not concept:
        return config.AI_IMAGE_PROMPT
    return f"{config.AI_IMAGE_PROMPT}\nScene concept (environment only, product unchanged): {concept}"


def generate_ai_image(product: AmazonProduct, content: AutoPostContent) -> bytes:
    """Place the product's primary (hero) photo into a new scene with the configured OpenAI model.

    Only the hero image is sent, so the model has a single, unambiguous product reference.
    """
    if not content.image_urls:
        raise RuntimeError("Product has no image to generate the AI hero image from.")
    hero = download_image_bytes(content.image_urls[0])
    return edit_image(
        hero,
        prompt=build_ai_image_prompt(product, content),
        context=f"{content.title}. {_summary(product)}",
        aspect_ratio=config.AI_IMAGE_ASPECT_RATIO,
        model=config.AI_IMAGE_MODEL,
        quality=config.AI_IMAGE_QUALITY,
        input_fidelity=config.AI_IMAGE_INPUT_FIDELITY,
    )


def _summary(product: AmazonProduct) -> str:
    return " ".join(product.bullets[:3]) or product.description or product.title


def _ensure_ai_image(
    candidate_id: int,
    stored_path: str,
    product: AmazonProduct,
    content: AutoPostContent,
) -> bytes:
    """Reuse the stored AI image on retries; generate and store it otherwise."""
    existing = load_media(stored_path)
    if existing:
        return existing
    image = generate_ai_image(product, content)
    update_candidate(candidate_id, ai_image_path=save_generated_image(image))
    logger.info("Auto-post: generated AI hero image for %s with %s", product.asin, config.AI_IMAGE_MODEL)
    return image


def publish_to_pinterest(product: AmazonProduct, content: AutoPostContent, ai_image: Optional[bytes]) -> str:
    """Pin the AI hero image, or the original main photo when no AI image is used.

    Links to the affiliate URL. Returns the pin URL.
    """
    if ai_image:
        pin_image = cover_to_size(ai_image, config.PINTEREST_IMAGE_SIZE)
    else:
        if not content.image_urls:
            raise RuntimeError("Product has no image to pin on Pinterest.")
        # Pad (never crop) the original so the whole product stays visible.
        pin_image = pad_to_canvas(download_image_bytes(content.image_urls[0]), config.PINTEREST_IMAGE_SIZE)
    response = create_pinterest_pin(
        pin_image,
        content.pinterest_title or content.title,
        content.pinterest_description or (product.bullets[0] if product.bullets else content.title),
        product.affiliate_link,
        content.website_tags,
    )
    if not response or response.get("status") == "skipped":
        raise RuntimeError("Pinterest is not configured (access token or board missing).")
    pin_url = str(response.get("url") or "")
    if not pin_url:
        raise RuntimeError("Pinterest did not return a pin URL.")
    logger.info("Auto-post: published Pinterest pin %s for %s", pin_url, product.asin)
    return pin_url


def publish_to_instagram(
    product: AmazonProduct,
    content: AutoPostContent,
    *,
    website_url: str,
    lead_image: Optional[bytes] = None,
) -> str:
    """Publish a feed carousel: ``lead_image`` (if any) first, then the original product photos."""
    relative_paths: List[str] = []
    try:
        if lead_image:
            relative_paths.append(
                save_generated_image(cover_to_size(lead_image, config.FEED_IMAGE_SIZE), suffix=".jpg")
            )
        max_originals = config.INSTAGRAM_CAROUSEL_MAX_ITEMS - len(relative_paths)
        for image_url in content.image_urls[:max_originals]:
            try:
                framed = pad_to_canvas(download_image_bytes(image_url), config.FEED_IMAGE_SIZE)
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
            "product_id": f"{product.source}:{product.asin}",
            "affiliate_link": product.affiliate_link,
            "product_url": website_url,
            "title": content.title,
            "description": product.bullets[0] if product.bullets else product.description,
        },
        reply_surface="feed",
    )
    logger.info("Auto-post: published Instagram media %s for %s", media_id, product.asin)
    return media_id
