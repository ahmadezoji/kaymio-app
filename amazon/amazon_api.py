import re
import requests
from typing import Any, Dict, Optional

# ============ CONFIGURATION =============

CANOPY_API_KEY = "0f4b7573-08b4-4e7f-b771-fe75e16535fb"
CANOPY_REST_ENDPOINT = "https://rest.canopyapi.co/api/amazon/product"


STORE_ID = "kaymio09-20"


# ============ HELPERS =============

def extract_asin(amazon_url: str) -> str:
    """
    Extract ASIN from common Amazon product URL forms.
    """
    raw = (amazon_url or "").strip()
    if re.fullmatch(r"[A-Za-z0-9]{10}", raw):
        return raw.upper()
    # Many possible forms: /dp/ASIN, /gp/product/ASIN, /product/ASIN, etc.
    m = re.search(r"/dp/([A-Z0-9]{10})", amazon_url, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    # fallback: maybe “/gp/product/ASIN”
    m2 = re.search(r"/gp/product/([A-Z0-9]{10})", amazon_url, re.IGNORECASE)
    if m2:
        return m2.group(1).upper()
    m3 = re.search(r"/product/([A-Z0-9]{10})", amazon_url, re.IGNORECASE)
    if m3:
        return m3.group(1).upper()
    raise ValueError(f"Could not extract ASIN from URL: {amazon_url}")


def build_affiliate_link(asin: str) -> str:
    return f"https://www.amazon.com/dp/{asin}?tag={STORE_ID}"


# ============ CANOPY API =============

def _extract_canopy_product_payload(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    data_block = payload.get("data")
    if isinstance(data_block, dict):
        amazon_product = data_block.get("amazonProduct")
        if isinstance(amazon_product, dict):
            return amazon_product
    if isinstance(payload.get("amazonProduct"), dict):
        return payload["amazonProduct"]
    if any(key in payload for key in ("title", "imageUrls", "images", "price", "categories", "featureBullets")):
        return payload
    return None


def _normalize_canopy_marketplace(domain: str) -> str:
    raw = (domain or "").strip().lower()
    if not raw:
        return "US"
    market_aliases = {
        "com": "US",
        "us": "US",
        "co.uk": "UK",
        "uk": "UK",
        "com.tr": "TR",
        "tr": "TR",
        "de": "DE",
        "fr": "FR",
        "it": "IT",
        "es": "ES",
        "ca": "CA",
        "com.mx": "MX",
        "mx": "MX",
        "com.br": "BR",
        "br": "BR",
        "co.jp": "JP",
        "jp": "JP",
        "nl": "NL",
        "pl": "PL",
        "se": "SE",
        "ae": "AE",
        "sa": "SA",
        "sg": "SG",
        "com.au": "AU",
        "au": "AU",
    }
    if raw in market_aliases:
        return market_aliases[raw]
    suffix = raw.split(".")[-1].upper()
    return market_aliases.get(suffix.lower(), suffix or "US")


def fetch_product_from_canopy(
    asin: str,
    ship_to_country: str = "US",
    *,
    product_url: str = "",
) -> dict:
    headers = {
        "API-KEY": CANOPY_API_KEY,
        "Content-Type": "application/json"
    }
    marketplace = _normalize_canopy_marketplace(ship_to_country)
    params = {
        "domain": marketplace,
    }
    if product_url:
        params["url"] = product_url
    else:
        params["asin"] = asin
    resp = requests.get(CANOPY_REST_ENDPOINT, headers=headers, params=params)
    if resp.status_code != 200:
        raise Exception(f"Canopy API error: {resp.status_code} / {resp.text}")
    payload = resp.json()

    product_data = _extract_canopy_product_payload(payload)
    if not isinstance(product_data, dict):
        raise ValueError(
            f"Canopy returned no product data for ASIN {asin} on marketplace {marketplace}."
        )

    title = product_data.get("title") or ""
    image_urls = product_data.get("imageUrls") or product_data.get("images") or []
    if isinstance(image_urls, str):
        image_urls = [image_urls]
    category = None
    categories = product_data.get("categories")
    if categories and isinstance(categories, list) and categories:
        last_category = categories[-1]
        category = last_category.get("name") if isinstance(last_category, dict) else str(last_category)
    description = None
    feature_bullets = product_data.get("featureBullets")
    if feature_bullets and isinstance(feature_bullets, list) and feature_bullets:
        description = feature_bullets[0]
    price = None
    price_obj = product_data.get("price")
    if price_obj and isinstance(price_obj, dict):
        price = (
            price_obj.get("display")
            or price_obj.get("displayAmount")
            or price_obj.get("value")
        )
    original_link = product_data.get("url") or product_data.get("productUrl") or product_url or ""
    # Fallbacks
    if not description:
        description = product_data.get("subtitle") or ""
    if not price:
        price = ""
    if not title and not image_urls and not description:
        raise ValueError(
            f"Canopy returned an incomplete product payload for ASIN {asin} on marketplace {marketplace}."
        )

    return {
        "title": title,
        "image_urls": image_urls,
        "category": category,
        "original_link": original_link,
        "description": description,
        "price": price
    }


def fetch_amazon_product_details(asin: str,ship_to_country:str) -> dict:
    """
    Entire flow: URL -> ASIN -> affiliate link -> fetch metadata -> Pinterest Pin.
    """
    # asin = extract_asin(amazon_url)
    affiliate_url = build_affiliate_link(asin)
    meta = fetch_product_from_canopy(asin, ship_to_country)
    return {
        "affiliate_url": affiliate_url,
        **meta
    }
