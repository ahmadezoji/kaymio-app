"""Scheduled task: find a trending Amazon product and ask for approval on Telegram."""
from __future__ import annotations

import logging
from typing import Optional, Sequence

from kaymio.database.auto_post import (
    create_candidate,
    delete_candidate,
    expire_stale_candidates,
    known_candidate_asins,
)
from kaymio.features.auto_post import config
from kaymio.features.auto_post.approval import notify, send_approval_request
from kaymio.features.auto_post.content import generate_auto_post_content
from kaymio.features.auto_post.catalog import load_website_catalog
from kaymio.features.auto_post.discovery import DiscoveryCriteria, find_top_trending_products
from kaymio.features.events import PromoEvent, get_active_event
from kaymio.integrations.amazon.amazon_crawler import AmazonProduct
from kaymio.integrations.telegram import get_telegram_settings
from kaymio.tasks import OptionField, ScheduledTask, TaskSettings, register_task
from kaymio.tasks.scheduler import parse_weekdays

logger = logging.getLogger(__name__)


def run_auto_post(settings: TaskSettings) -> None:
    if not get_telegram_settings().is_configured:
        # Approval is mandatory; never publish without a way to ask for it.
        raise RuntimeError("Telegram is not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).")

    options = settings.options
    event = get_active_event()
    criteria = DiscoveryCriteria(
        source_urls=config.split_source_urls(options["source_urls"]),
        min_rating=float(options["min_rating"]),
        min_reviews=int(options["min_reviews"]),
        max_detail_fetches=config.MAX_DETAIL_FETCHES,
        canopy_fallback=config.CANOPY_FALLBACK,
        lists_per_run=config.LISTS_PER_RUN,
        priority_urls=event.amazon_lists if event else (),
        prefer_discount=event is not None,
        min_discount_percent=event.min_discount_percent if event else 0,
    )
    products = find_top_trending_products(
        criteria,
        load_website_catalog(),
        known_candidate_asins(),
        limit=int(options["suggestions_per_run"]),
    )
    if not products:
        logger.warning("Auto-post: no new Amazon product matched the criteria.")
        notify(
            "🤷 Auto-post: no new Amazon product matched today "
            f"(rating ≥ {criteria.min_rating}, ≥ {criteria.min_reviews} reviews, not on kaymio.com)."
        )
        return

    propose_products(
        products,
        image_count=int(options["image_count"]),
        website_boost_prompt=config.WEBSITE_BOOST_PROMPT,
        event=event,
    )


def propose_products(
    products: Sequence[AmazonProduct],
    *,
    image_count: int,
    website_boost_prompt: str,
    event: Optional[PromoEvent] = None,
) -> int:
    """Shared by every source: expire old suggestions, then send each product for approval.

    One failing product doesn't stop the others. Returns how many were sent.
    """
    expired = expire_stale_candidates(config.APPROVAL_TIMEOUT_HOURS)
    if expired:
        logger.info("Expired %d unanswered auto-post suggestion(s).", len(expired))
    sent = 0
    for product in products:
        try:
            propose_product(
                product, image_count=image_count, website_boost_prompt=website_boost_prompt, event=event
            )
            sent += 1
        except Exception:
            logger.exception("Auto-post: could not propose %s %s.", product.source_label, product.asin)
    if products and not sent:
        raise RuntimeError("None of the selected products could be sent for approval (see logs).")
    return sent


def propose_product(
    product: AmazonProduct,
    *,
    image_count: int,
    website_boost_prompt: str,
    event: Optional[PromoEvent] = None,
) -> None:
    """Write the copy, store the candidate and ask on Telegram."""
    content = generate_auto_post_content(
        product,
        image_count=image_count,
        call_to_action=config.CAPTION_CTA,
        website_boost_prompt=website_boost_prompt,
        include_ai_image_concept=config.AI_IMAGE_ENABLED,
        event=event,
    )
    candidate = create_candidate(
        asin=product.asin,
        title=product.title,
        product_url=product.product_url,
        image_url=product.image_urls[0],
        product_data=product.to_dict(),
        content=content.to_dict(),
    )
    try:
        send_approval_request(candidate)
    except Exception:
        # Unsent suggestions must never become publishable; drop it so it can be proposed again.
        delete_candidate(candidate["id"])
        raise
    logger.info(
        "Auto-post: sent %s %s (%s) for approval.", product.source_label, product.asin, product.title[:80]
    )


AUTO_POST_TASK = ScheduledTask(
    key=config.TASK_KEY,
    label="Instagram auto-post (Amazon trending)",
    description=(
        "Finds the best trending, well-rated Amazon products that kaymio.com doesn't have yet "
        "(scored, niches rotate daily), asks for approval on Telegram, then adds it to the website, generates an AI hero image, "
        "posts a carousel to Instagram (AI image first) and pins the AI image on Pinterest."
    ),
    run=run_auto_post,
    default_enabled=config.ENABLED,
    default_run_time=config.RUN_TIME,
    default_every_days=config.EVERY_DAYS,
    default_weekdays=parse_weekdays(config.WEEKDAYS),
    option_fields=(
        OptionField("suggestions_per_run", "Suggestions per run", "int", config.SUGGESTIONS_PER_RUN,
                    min=1, max=10, help="Each one arrives as its own Telegram approval message."),
        OptionField("image_count", "Images per post", "int", config.IMAGE_COUNT, min=1, max=10,
                    help="Carousel size (Instagram allows up to 10)."),
        OptionField("min_rating", "Minimum rating", "float", config.MIN_RATING, min=0, max=5),
        OptionField("min_reviews", "Minimum reviews", "int", config.MIN_REVIEWS, min=0),
        OptionField("source_urls", "Amazon lists (one per line)", "text", config.SOURCE_URLS,
                    help="Lists rotate daily; the best-scoring products across them are suggested."),
    ),
)


def register_auto_post_task() -> ScheduledTask:
    return register_task(AUTO_POST_TASK)
