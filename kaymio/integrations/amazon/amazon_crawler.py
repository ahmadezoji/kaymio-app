"""Amazon list/product scraping on top of the self-hosted Crawl4AI service.

Two page types are supported:
  * ranked lists Amazon itself promotes (Best Sellers, Movers & Shakers,
    New Releases, ...) -> ``fetch_listing``
  * a product detail page (``/dp/<ASIN>``) -> ``fetch_product``

Crawl4AI only fetches the page; parsing happens here so selectors can be
tuned without touching the crawler container.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from kaymio.integrations.amazon.amazon_api import build_affiliate_link
from kaymio.integrations.crawl4ai import Crawl4AIClient, get_crawl4ai_client

logger = logging.getLogger(__name__)

ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
DP_LINK_RE = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})", re.IGNORECASE)
RATING_RE = re.compile(r"(\d(?:[.,]\d)?)\s+out of\s+5", re.IGNORECASE)
PRICE_RE = re.compile(r"[$£€]\s?\d[\d,]*(?:\.\d{2})?")
HIRES_IMAGE_RE = re.compile(r'"hiRes"\s*:\s*"(https://[^"]+)"')
LARGE_IMAGE_RE = re.compile(r'"large"\s*:\s*"(https://[^"]+)"')
BLOCKED_MARKERS = ("validateCaptcha", "Type the characters you see", "api-services-support@amazon.com")


class AmazonCrawlError(RuntimeError):
    """Raised when Amazon could not be crawled or the page could not be parsed."""


@dataclass(frozen=True)
class AmazonListingItem:
    asin: str
    rank: int
    title: str = ""
    rating: Optional[float] = None
    review_count: Optional[int] = None
    price: str = ""


@dataclass
class AmazonProduct:
    asin: str
    title: str
    product_url: str
    affiliate_link: str
    price: str = ""
    rating: Optional[float] = None
    review_count: Optional[int] = None
    image_urls: List[str] = field(default_factory=list)
    bullets: List[str] = field(default_factory=list)
    description: str = ""
    category: str = ""
    brand: str = ""
    source_list_url: str = ""
    source_rank: Optional[int] = None

    @property
    def price_value(self) -> Optional[float]:
        return parse_price_value(self.price)

    @property
    def is_publishable(self) -> bool:
        return bool(self.title and self.image_urls and self.price_value)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "AmazonProduct":
        known = {name for name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in payload.items() if key in known})


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def fetch_listing(list_url: str, *, client: Optional[Crawl4AIClient] = None) -> List[AmazonListingItem]:
    """Crawl a ranked Amazon list and return its products in Amazon's order."""
    html = _crawl_html(list_url, client)
    items = parse_listing_html(html)
    if not items:
        raise AmazonCrawlError(f"No products found on Amazon list {list_url}")
    return items


def fetch_product(
    asin: str,
    *,
    marketplace_url: str = "https://www.amazon.com",
    client: Optional[Crawl4AIClient] = None,
) -> AmazonProduct:
    """Crawl and parse a product detail page."""
    origin = _origin(marketplace_url)
    product_url = f"{origin}/dp/{asin}"
    html = _crawl_html(product_url, client)
    return parse_product_html(html, asin=asin, product_url=product_url)


# --------------------------------------------------------------------------- #
# Parsing (pure functions, unit-testable with saved HTML)
# --------------------------------------------------------------------------- #
def parse_listing_html(html: str) -> List[AmazonListingItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: List[AmazonListingItem] = []
    seen: set[str] = set()

    # Outer product cards come before nested elements in document order, so the
    # first node for an ASIN is the one holding its title/rating/price.
    for node in soup.select("[data-asin]"):
        asin = str(node.get("data-asin") or "").strip().upper()
        if not ASIN_RE.match(asin) or asin in seen:
            continue
        seen.add(asin)
        items.append(
            AmazonListingItem(
                asin=asin,
                rank=len(items) + 1,
                title=_listing_title(node),
                rating=parse_rating(node.get_text(" ", strip=True)),
                review_count=_listing_review_count(node),
                price=_first_price(node.get_text(" ", strip=True)),
            )
        )

    if items:
        return items

    # Fallback for layouts without data-asin: keep /dp/ link order.
    for match in DP_LINK_RE.finditer(html):
        asin = match.group(1).upper()
        if asin not in seen:
            seen.add(asin)
            items.append(AmazonListingItem(asin=asin, rank=len(items) + 1))
    return items


def parse_product_html(html: str, *, asin: str, product_url: str) -> AmazonProduct:
    soup = BeautifulSoup(html, "html.parser")
    title = _text(soup.select_one("#productTitle"))
    if not title:
        raise AmazonCrawlError(f"Amazon product page for {asin} has no title (layout changed or blocked).")

    rating_node = soup.select_one("#acrPopover")
    rating_text = (rating_node.get("title") if rating_node else "") or _text(
        soup.select_one("#averageCustomerReviews .a-icon-alt")
    )

    bullets = [
        _clean_whitespace(node.get_text(" ", strip=True))
        for node in soup.select("#feature-bullets li span.a-list-item")
    ]
    breadcrumbs = [_text(node) for node in soup.select("#wayfinding-breadcrumbs_feature_div li a")]

    return AmazonProduct(
        asin=asin,
        title=title,
        product_url=product_url,
        affiliate_link=build_affiliate_link(asin),
        price=_product_price(soup),
        rating=parse_rating(rating_text or ""),
        review_count=parse_int(_text(soup.select_one("#acrCustomerReviewText"))),
        image_urls=_product_images(html, soup),
        bullets=[bullet for bullet in bullets if bullet][:8],
        description=_text(soup.select_one("#productDescription")),
        category=next((crumb for crumb in reversed(breadcrumbs) if crumb), ""),
        brand=_clean_brand(_text(soup.select_one("#bylineInfo"))),
    )


def parse_rating(text: str) -> Optional[float]:
    match = RATING_RE.search(text or "")
    return float(match.group(1).replace(",", ".")) if match else None


def parse_int(text: str) -> Optional[int]:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


def parse_price_value(price: str) -> Optional[float]:
    match = re.search(r"\d[\d,]*(?:\.\d+)?", price or "")
    if not match:
        return None
    try:
        value = float(match.group(0).replace(",", ""))
    except ValueError:
        return None
    return value if value > 0 else None


def extract_asins_from_text(text: str) -> List[str]:
    return [match.upper() for match in DP_LINK_RE.findall(text or "")]


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
def _crawl_html(url: str, client: Optional[Crawl4AIClient]) -> str:
    result = (client or get_crawl4ai_client()).crawl_one(url)
    html = result.html
    if any(marker in html for marker in BLOCKED_MARKERS):
        raise AmazonCrawlError(f"Amazon returned a bot-check page for {url}")
    return html


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme or 'https'}://{parsed.netloc or 'www.amazon.com'}"


def _text(node: Optional[Tag]) -> str:
    return _clean_whitespace(node.get_text(" ", strip=True)) if node else ""


def _clean_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _clean_brand(value: str) -> str:
    value = re.sub(r"^(Visit the|Brand:)\s*", "", value, flags=re.IGNORECASE)
    return re.sub(r"\s*Store$", "", value).strip()


def _first_price(text: str) -> str:
    match = PRICE_RE.search(text or "")
    return match.group(0).replace(" ", "") if match else ""


def _listing_title(node: Tag) -> str:
    for selector in ('[class*="line-clamp"]', "h2", ".a-link-normal span"):
        title = _text(node.select_one(selector))
        if title:
            return title
    image = node.select_one("img[alt]")
    return _clean_whitespace(image.get("alt", "")) if image else ""


def _listing_review_count(node: Tag) -> Optional[int]:
    reviews_link = node.select_one('a[href*="product-reviews"], a[href*="customerReviews"]')
    if reviews_link:
        count = parse_int(reviews_link.get_text(" ", strip=True))
        if count is not None:
            return count
    labelled = node.select_one('[aria-label*="ratings"], [aria-label*="reviews"]')
    return parse_int(labelled.get("aria-label", "")) if labelled else None


def _product_price(soup: BeautifulSoup) -> str:
    for selector in (
        "#corePrice_feature_div .a-offscreen",
        "#corePriceDisplay_desktop_feature_div .a-offscreen",
        "#apex_desktop .a-price .a-offscreen",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        ".a-price .a-offscreen",
    ):
        price = _first_price(_text(soup.select_one(selector)))
        if price:
            return price
    return ""


def _product_images(html: str, soup: BeautifulSoup) -> List[str]:
    """High-resolution gallery images in display order."""
    urls = HIRES_IMAGE_RE.findall(html) or LARGE_IMAGE_RE.findall(html)
    if not urls:
        landing = soup.select_one("#landingImage, #imgBlkFront")
        if landing:
            dynamic = landing.get("data-a-dynamic-image")
            if dynamic:
                try:
                    urls = list(json.loads(dynamic).keys())
                except ValueError:
                    urls = []
            urls = [landing.get("data-old-hires") or ""] + urls
    return list(dict.fromkeys(url for url in urls if url))
