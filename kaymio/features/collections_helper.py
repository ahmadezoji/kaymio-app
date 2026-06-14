"""Core logic for the Instagram FEED "Collections" publisher.

A collection bundles ~5 related WooCommerce products into a single Instagram
carousel post plus a WordPress landing page at /collections/{slug}/.
"""
from __future__ import annotations

import html
import logging
import re
from typing import Any, Dict, List, Optional

from kaymio.utils.openai_helper import _generate_response_text
from kaymio.wordpress.wordpress_api_helper import (
    _fetch_all_woocommerce_products,
    create_wordpress_page,
)

logger = logging.getLogger(__name__)


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())


def _product_score(product: Dict[str, Any], keywords: List[str]) -> int:
    haystacks = [
        _normalize(product.get("name", "")),
        _normalize(product.get("short_description", "")),
        _normalize(product.get("description", "")),
    ]
    for category in product.get("categories") or []:
        haystacks.append(_normalize(category.get("name", "")))

    score = 0
    for keyword in keywords:
        for haystack in haystacks:
            if keyword and keyword in haystack:
                score += 1
    return score


def find_related_products(collection_name: str, description: str = "", limit: int = 5) -> List[Dict[str, Any]]:
    """Search WooCommerce products for the top `limit` matches to a collection definition."""
    result = _fetch_all_woocommerce_products()
    if result.get("error") and not result.get("items"):
        return []

    keywords = [
        word for word in _normalize(f"{collection_name} {description}").split()
        if len(word) > 2
    ]
    if not keywords:
        return []

    scored = []
    for product in result.get("items") or []:
        score = _product_score(product, keywords)
        if score > 0:
            scored.append((score, product))

    scored.sort(key=lambda pair: pair[0], reverse=True)

    matches = []
    for _score, product in scored[:limit]:
        images = product.get("images") or []
        matches.append({
            "wc_product_id": product.get("id"),
            "title": product.get("name"),
            "product_url": product.get("permalink"),
            "image_url": images[0].get("src") if images else None,
        })
    return matches


def generate_collection_metadata(collection_name: str, description: str, products: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Generate an Instagram caption, hashtags, and landing page description via OpenAI."""
    product_lines = "\n".join(f"- {p.get('title')}" for p in products)

    caption_prompt = (
        f"Collection name: {collection_name}\n"
        f"Collection description: {description}\n"
        f"Featured products:\n{product_lines}\n\n"
        "Write an engaging Instagram caption (under 2200 characters) announcing this curated "
        "collection. Mention the vibe/theme, tease the products, and end with a call to action "
        "to view the full collection via the link in bio."
    )
    caption = _generate_response_text(
        "You write engaging Instagram captions for curated e-commerce product collections, "
        "mixing emoji, short punchy lines, and a clear call to action.",
        caption_prompt,
        temperature=0.7,
        max_output_tokens=300,
    ) or f"✨ Introducing our {collection_name} collection! Shop the full edit via the link in bio."

    hashtags_prompt = (
        f"Collection name: {collection_name}\nDescription: {description}\n"
        "Return a JSON array of up to 12 short, niche Instagram hashtags for this collection."
    )
    hashtags_text = _generate_response_text(
        "Provide concise Instagram hashtags as a JSON array of strings.",
        hashtags_prompt,
        temperature=0.4,
        max_output_tokens=150,
    )
    hashtags: List[str] = []
    if hashtags_text:
        try:
            import json
            hashtags = [str(tag).strip().lstrip("#") for tag in json.loads(hashtags_text) if str(tag).strip()][:12]
        except Exception:
            hashtags = []

    landing_prompt = (
        f"Collection name: {collection_name}\n"
        f"Collection description: {description}\n"
        f"Featured products:\n{product_lines}\n\n"
        "Write 2-3 short paragraphs of marketing copy for a website landing page introducing "
        "this curated collection. Friendly, modern tone."
    )
    landing_copy = _generate_response_text(
        "You write modern, friendly marketing copy for e-commerce landing pages.",
        landing_prompt,
        temperature=0.7,
        max_output_tokens=400,
    ) or description or f"Discover our {collection_name} collection."

    return {
        "caption": caption,
        "hashtags": hashtags,
        "landing_copy": landing_copy,
    }


def create_collection_landing_page_html(
    collection_name: str,
    landing_copy: str,
    products: List[Dict[str, Any]],
) -> str:
    """Build a modern, responsive HTML landing page for a collection."""
    name = html.escape(collection_name)
    copy_html = "".join(
        f"<p>{html.escape(paragraph.strip())}</p>"
        for paragraph in (landing_copy or "").split("\n")
        if paragraph.strip()
    )

    cards = []
    for product in products:
        title = html.escape(product.get("title") or "")
        url = html.escape(product.get("product_url") or "#", quote=True)
        image = html.escape(product.get("image_url") or "", quote=True)
        cards.append(f"""
        <a class="kaymio-collection-card" href="{url}" target="_blank" rel="noopener">
            <div class="kaymio-collection-card-image" style="background-image: url('{image}');"></div>
            <div class="kaymio-collection-card-title">{title}</div>
        </a>""")

    return f"""
<div class="kaymio-collection">
  <style>
    .kaymio-collection {{
        max-width: 1100px;
        margin: 0 auto;
        padding: 2rem 1rem;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        color: #1a1a1a;
    }}
    .kaymio-collection h1 {{
        font-size: 2.25rem;
        font-weight: 700;
        margin-bottom: 1rem;
        text-align: center;
    }}
    .kaymio-collection-copy {{
        max-width: 700px;
        margin: 0 auto 2.5rem;
        text-align: center;
        line-height: 1.6;
        color: #4a4a4a;
    }}
    .kaymio-collection-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        gap: 1.25rem;
    }}
    .kaymio-collection-card {{
        display: block;
        text-decoration: none;
        color: inherit;
        border-radius: 14px;
        overflow: hidden;
        background: #f7f7f7;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
        box-shadow: 0 1px 4px rgba(0,0,0,0.06);
    }}
    .kaymio-collection-card:hover {{
        transform: translateY(-4px);
        box-shadow: 0 8px 20px rgba(0,0,0,0.12);
    }}
    .kaymio-collection-card-image {{
        width: 100%;
        aspect-ratio: 1 / 1;
        background-size: cover;
        background-position: center;
        background-color: #e0e0e0;
    }}
    .kaymio-collection-card-title {{
        padding: 0.85rem 1rem;
        font-weight: 600;
        font-size: 0.95rem;
        text-align: center;
    }}
  </style>
  <h1>{name}</h1>
  <div class="kaymio-collection-copy">{copy_html}</div>
  <div class="kaymio-collection-grid">{''.join(cards)}</div>
</div>
""".strip()


def publish_collection_to_wordpress(collection_name: str, slug: str, html_content: str) -> Dict[str, Any]:
    """Publish the landing page as /collections/{slug}/ on the WordPress site."""
    return create_wordpress_page(
        title=collection_name,
        content=html_content,
        slug=slug,
        parent_slug="collections",
    )


def publish_collection_to_instagram(image_urls: List[str], caption: str, hashtags: List[str]) -> Dict[str, Any]:
    """Publish the collection's product images as a multi-image Instagram FEED carousel."""
    from kaymio.integrations.instagram.instagram_api_helper import publish_instagram_carousel

    full_caption = caption
    if hashtags:
        full_caption = f"{caption}\n\n" + " ".join(f"#{tag}" for tag in hashtags)

    return publish_instagram_carousel(image_urls=image_urls, caption=full_caption)
