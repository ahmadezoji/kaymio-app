"""Scheduled task: find a trending Amazon product and ask for approval on Telegram."""
from __future__ import annotations

import logging

from kaymio.database.auto_post import (
    create_candidate,
    delete_candidate,
    expire_stale_candidates,
    known_candidate_asins,
)
from kaymio.features.auto_post import config
from kaymio.features.auto_post.approval import notify, send_approval_request
from kaymio.features.auto_post.content import generate_auto_post_content
from kaymio.features.auto_post.discovery import (
    DiscoveryCriteria,
    find_top_trending_product,
    find_website_amazon_asins,
)
from kaymio.integrations.telegram import get_telegram_settings
from kaymio.tasks import OptionField, ScheduledTask, TaskSettings, register_task

logger = logging.getLogger(__name__)


def run_auto_post(settings: TaskSettings) -> None:
    if not get_telegram_settings().is_configured:
        # Approval is mandatory; never publish without a way to ask for it.
        raise RuntimeError("Telegram is not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).")

    expired = expire_stale_candidates(config.APPROVAL_TIMEOUT_HOURS)
    if expired:
        logger.info("Expired %d unanswered auto-post suggestion(s).", len(expired))

    options = settings.options
    criteria = DiscoveryCriteria(
        source_urls=config.split_source_urls(options["source_urls"]),
        min_rating=float(options["min_rating"]),
        min_reviews=int(options["min_reviews"]),
        max_detail_fetches=config.MAX_DETAIL_FETCHES,
        canopy_fallback=config.CANOPY_FALLBACK,
    )
    exclude_asins = find_website_amazon_asins() | known_candidate_asins()
    product = find_top_trending_product(criteria, exclude_asins)
    if product is None:
        logger.warning("Auto-post: no new Amazon product matched the criteria.")
        notify(
            "🤷 Auto-post: no new Amazon product matched today "
            f"(rating ≥ {criteria.min_rating}, ≥ {criteria.min_reviews} reviews, not on kaymio.com)."
        )
        return

    content = generate_auto_post_content(
        product,
        image_count=int(options["image_count"]),
        call_to_action=config.CAPTION_CTA,
        website_boost_prompt=config.WEBSITE_BOOST_PROMPT,
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
    logger.info("Auto-post: sent %s (%s) for approval.", product.asin, product.title[:80])


AUTO_POST_TASK = ScheduledTask(
    key=config.TASK_KEY,
    label="Instagram auto-post (Amazon trending)",
    description=(
        "Finds the top trending, well-rated Amazon product that kaymio.com doesn't have yet, "
        "asks for approval on Telegram, then adds it to the website and posts a carousel to Instagram."
    ),
    run=run_auto_post,
    default_enabled=config.ENABLED,
    default_run_time=config.RUN_TIME,
    option_fields=(
        OptionField("image_count", "Images per post", "int", config.IMAGE_COUNT, min=1, max=10,
                    help="Carousel size (Instagram allows up to 10)."),
        OptionField("min_rating", "Minimum rating", "float", config.MIN_RATING, min=0, max=5),
        OptionField("min_reviews", "Minimum reviews", "int", config.MIN_REVIEWS, min=0),
        OptionField("source_urls", "Amazon lists (one per line)", "text", config.SOURCE_URLS,
                    help="Checked in order; the first qualifying product wins."),
    ),
)


def register_auto_post_task() -> ScheduledTask:
    return register_task(AUTO_POST_TASK)
