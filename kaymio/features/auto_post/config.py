"""Environment defaults for the Amazon -> Telegram approval -> website + Instagram task.

Enabled/time/image count/rating thresholds/source lists can be overridden at
runtime from the Automation page; the rest are deployment-level settings.
"""
from __future__ import annotations

import os
import re
from typing import List

from kaymio.config import TRUTHY_VALUES

TASK_KEY = "instagram_auto_post"

DEFAULT_SOURCE_URLS = (
    "https://www.amazon.com/gp/movers-and-shakers",
    "https://www.amazon.com/gp/new-releases",
    "https://www.amazon.com/gp/bestsellers",
)

ENABLED = os.getenv("INSTAGRAM_AUTO_POST_ENABLED", "0").lower() in TRUTHY_VALUES
RUN_TIME = os.getenv("INSTAGRAM_AUTO_POST_TIME", "10:00")
IMAGE_COUNT = int(os.getenv("INSTAGRAM_AUTO_POST_IMAGE_COUNT", "5"))
MIN_RATING = float(os.getenv("AMAZON_TRENDING_MIN_RATING", "4.3"))
MIN_REVIEWS = int(os.getenv("AMAZON_TRENDING_MIN_REVIEWS", "100"))
SOURCE_URLS = os.getenv("AMAZON_TRENDING_SOURCE_URLS", ",".join(DEFAULT_SOURCE_URLS))

# Upper bound on product pages crawled per run (each is one Crawl4AI request).
MAX_DETAIL_FETCHES = int(os.getenv("AMAZON_TRENDING_MAX_DETAIL_FETCHES", "5"))
# Fill gaps (missing price/images) from the Canopy API when CANOPY_API_KEY is set.
CANOPY_FALLBACK = os.getenv("AMAZON_PRODUCT_CANOPY_FALLBACK", "1").lower() in TRUTHY_VALUES

APPROVAL_TIMEOUT_HOURS = float(os.getenv("INSTAGRAM_AUTO_POST_APPROVAL_TIMEOUT_HOURS", "24"))
CAPTION_CTA = os.getenv("INSTAGRAM_AUTO_POST_CAPTION_CTA", "").strip()
WEBSITE_BOOST_PROMPT = os.getenv(
    "INSTAGRAM_AUTO_POST_WEBSITE_BOOST_PROMPT",
    "Lead with the main benefit, say who it is for, and mention it is trending on Amazon.",
).strip()

# Instagram feed portrait size; product photos are padded (never cropped) to fit.
FEED_IMAGE_SIZE = (1080, 1350)

# AI hero image generated from the product's primary photo after Telegram approval.
# It leads the Instagram carousel and is the only image pinned to Pinterest; the
# website product keeps the original Amazon images.
AI_IMAGE_ENABLED = os.getenv("INSTAGRAM_AUTO_POST_AI_IMAGE_ENABLED", "1").lower() in TRUTHY_VALUES
DEFAULT_AI_IMAGE_PROMPT = (
    "Place the EXACT product from the reference photo into a new, scroll-stopping scene. "
    "The product itself must stay identical to the original photo: same shape, proportions, "
    "angle, colors, materials, buttons, labels, logos and packaging. Do not redraw, restyle, "
    "resize unevenly, add effects, glow, reflections, or any changes to the product or its front "
    "face. Only change the environment around it: background, surfaces, props, lighting, and "
    "people or results that show the product's benefit in real use. Keep the product sharp, fully "
    "visible and centered with generous space above and below it. Photorealistic, premium "
    "advertising photography. No text, captions, prices, watermarks or extra logos."
)
# Empty values in .env fall back to the defaults.
AI_IMAGE_MODEL = os.getenv("INSTAGRAM_AUTO_POST_AI_IMAGE_MODEL", "").strip() or "gpt-image-2"
AI_IMAGE_PROMPT = os.getenv("INSTAGRAM_AUTO_POST_AI_IMAGE_PROMPT", "").strip() or DEFAULT_AI_IMAGE_PROMPT
AI_IMAGE_QUALITY = os.getenv("INSTAGRAM_AUTO_POST_AI_IMAGE_QUALITY", "").strip() or "high"
# "high" asks OpenAI to preserve the reference product's details as closely as possible.
AI_IMAGE_INPUT_FIDELITY = os.getenv("INSTAGRAM_AUTO_POST_AI_IMAGE_INPUT_FIDELITY", "").strip() or "high"
# 2:3 portrait suits Pinterest; Instagram gets a centered 4:5 crop of the same image.
AI_IMAGE_ASPECT_RATIO = "2:3"
PINTEREST_IMAGE_SIZE = (1000, 1500)
PINTEREST_ENABLED = os.getenv("INSTAGRAM_AUTO_POST_PINTEREST_ENABLED", "1").lower() in TRUTHY_VALUES
INSTAGRAM_CAROUSEL_MAX_ITEMS = 10


def split_source_urls(raw: str) -> List[str]:
    return [url for url in (part.strip() for part in re.split(r"[\s,]+", raw or "")) if url]
