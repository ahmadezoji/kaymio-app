import re
import requests

# ============ CONFIGURATION =============

CANOPY_API_KEY = "0f4b7573-08b4-4e7f-b771-fe75e16535fb"
CANOPY_REST_ENDPOINT = "https://rest.canopyapi.co/api/amazon/product"


STORE_ID = "kaymio-20"


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

def fetch_product_from_canopy(asin: str ,ship_to_country : str = "US") -> dict:
    headers = {
        "API-KEY": CANOPY_API_KEY,
        "Content-Type": "application/json"
    }
    params = {
        "asin": asin,
        "domain": ship_to_country
    }
    resp = requests.get(CANOPY_REST_ENDPOINT, headers=headers, params=params)
    if resp.status_code != 200:
        raise Exception(f"Canopy API error: {resp.status_code} / {resp.text}")
    data = resp.json()

    # Extract required fields
    source = data.get("data")
    data = source.get("amazonProduct")
    title = data.get("title") or data.get("data") or data.get("amazonProduct")
    image_urls = data.get("imageUrls") or data.get("images") or []
    if isinstance(image_urls, str):
        image_urls = [image_urls]
    category = None
    categories = data.get("categories")
    if categories and isinstance(categories, list) and categories:
        last_category = categories[-1]
        category = last_category.get("name") if isinstance(last_category, dict) else str(last_category)
    description = None
    feature_bullets = data.get("featureBullets")
    if feature_bullets and isinstance(feature_bullets, list) and feature_bullets:
        description = feature_bullets[0]
    price = None
    price_obj = data.get("price")
    if price_obj and isinstance(price_obj, dict):
        price = price_obj.get("display") or price_obj.get("value")
    original_link = data.get("url") or data.get("productUrl") or ""
    # Fallbacks
    if not description:
        description = data.get("subtitle") or ""
    if not price:
        price = ""

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
    meta = fetch_product_from_canopy(asin,ship_to_country)
    return {
        "affiliate_url": affiliate_url,
        **meta
    }
