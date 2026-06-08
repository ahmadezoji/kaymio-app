import sys
from pathlib import Path
import logging
import requests
import base64
from dotenv import load_dotenv
import os
import base64
import datetime
import shutil
import uuid
import re

if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from kaymio.utils.openai_helper import find_nearest_category




load_dotenv()


# WordPress site configuration
wp_url = os.getenv('WORDPRESS_API_URL', 'https://yoursite.com')
wp_username = os.getenv('WORDPRESS_USERNAME')
wp_password = os.getenv('WORDPRESS_PASSWORD')

wc_url = os.getenv('WORDPRESS_API_URL')  # Your base site URL
consumer_key = os.getenv('WC_CONSUMER_KEY')
consumer_secret = os.getenv('WC_CONSUMER_SECRET')

all_categories = []
ALIEXPRESS_MATCH_TOKENS = (
    "aliexpress.",
    "ali express",
    "AliExpress",
)
AMAZON_MATCH_TOKENS = (
    "amazon.",
    "amzn.",
)
DEFAULT_ALIEXPRESS_CATEGORY_PATTERNS = (
    "Kids & Baby",
    "Kids' Furniture",
    "Kids’ Furniture",
)
USE_DEFAULT_CATEGORY_PATTERNS = object()

def create_wordpress_post(title, content, featured_image_path=None, tags=None):
    """
    Create a WordPress post using REST API

    Args:
        title (str): Post title
        content (str): Post content (HTML)
        featured_image_path (str): Path to featured image
        tags (list): List of tags

    Returns:
        str: Post URL if successful, None otherwise
    """

    if not all([wp_username, wp_password]):
        print("WordPress credentials not found in environment variables")
        return None

    try:
        # Upload featured image if provided
        featured_media_id = None
        if featured_image_path and os.path.exists(featured_image_path):
            featured_media_id = upload_wordpress_media(
                featured_image_path, wp_url, wp_username, wp_password)

        # If tags are provided, ensure they are valid IDs
        # or whatever you had in post_data["tags"]
        tag_names = ["trending", "shein"]
        tag_ids = []

        for tag in tag_names:
            tag_id = get_tag_id(tag, wp_url, wp_username, wp_password)
            if tag_id:
                tag_ids.append(tag_id)
        # Prepare post data
        post_data = {
            'title': title,
            'content': content,
            'status': 'publish',
            'tags': tag_ids or [],
            'featured_media': featured_media_id or None
        }

        # Create post
        response = requests.post(
            f"{wp_url}/wp-json/wp/v2/posts",
            json=post_data,
            auth=(wp_username, wp_password),
            headers={'Content-Type': 'application/json'}
        )

        if response.status_code == 201:
            response_data = response.json()
            return response_data.get('link')
        else:
            print(f"Failed to create WordPress post: {response.text}")
            return None

    except Exception as e:
        print(f"Error creating WordPress post: {e}")
        return None

def get_tag_id(tag_name, wp_url, wp_username, wp_password):
    response = requests.get(
        f"{wp_url}/wp-json/wp/v2/tags",
        params={'search': tag_name},
        auth=(wp_username, wp_password)
    )
    data = response.json()

    # Tag exists → return ID
    if data:
        return data[0]['id']

    # Tag doesn't exist → create it
    response = requests.post(
        f"{wp_url}/wp-json/wp/v2/tags",
        json={'name': tag_name},
        auth=(wp_username, wp_password),
        headers={'Content-Type': 'application/json'}
    )
    if response.status_code == 201:
        return response.json()['id']
    else:
        print(f"❌ Could not create tag '{tag_name}' — {response.text}")
        return None

def upload_media_to_wordpress_ext(image_path):
    if image_path and os.path.exists(image_path):
        image_url = upload_wordpress_media(
            image_path, wp_url, wp_username, wp_password)
        return image_url
    return None
        
def create_woocommerce_product(name, description, price, image_path=None, tags=None, images=None, affiliate_link=None, category_id=None):
    if not all([consumer_key, consumer_secret]):
        print("WooCommerce API credentials not found")
        return None

    # Prepare basic auth header
    auth = (consumer_key, consumer_secret)

    # Upload images if provided
    image_payloads = []
    if image_path and os.path.exists(image_path):
        image_url = upload_wordpress_media(image_path, wp_url, wp_username, wp_password)
        if image_url:
            image_payloads.append({"src": image_url})
    for item in images or []:
        if isinstance(item, dict):
            if item:
                image_payloads.append(item)
            continue
        if not item or not isinstance(item, str):
            continue
        if os.path.exists(item):
            image_url = upload_wordpress_media(item, wp_url, wp_username, wp_password)
            if image_url:
                image_payloads.append({"src": image_url})
        elif item.startswith(("http://", "https://")):
            image_payloads.append({"src": item})
    # category_id = get_category_id_by_name(category_name)
    # tag_ids = []

    # for tag in tags:
    #     tag_id = get_tag_id(tag, wp_url, wp_username, wp_password)
    #     if tag_id:
    #         tag_ids.append(tag_id)

    # Prepare product data
    product_data = {
        "name": name,
        "type": "external",  # Set product type to external/affiliate
        "regular_price": str(price),
        "description": description,
        "images": image_payloads,
        "tags": [{"name": tag} for tag in tags] if tags else [],
        # Use category ID instead of name
        "categories": [{"id": category_id}] if category_id else [],
        "external_url": affiliate_link,
        "button_text": "Buy Product"  # Button text
    }

    # Create product
    response = requests.post(
        f"{wc_url}/wp-json/wc/v3/products",
        json=product_data,
        auth=auth,
        headers={'Content-Type': 'application/json'}
    )

    if response.status_code == 201:
        return response.json()['permalink']
    else:
        print(f"Failed to create product: {response.text}")
        return None

def upload_wordpress_media(file_path, wp_url, username, password):
    """
    Upload media file to WordPress

    Returns:
        int: Media ID if successful, None otherwise
    """
    try:
        with open(file_path, 'rb') as f:
            files = {
                'file': (os.path.basename(file_path), f, 'image/png')
            }

            response = requests.post(
                f"{wp_url}/wp-json/wp/v2/media",
                files=files,
                auth=(username, password)
            )

            if response.status_code == 201:
                return response.json().get("source_url")
            else:
                print(f"Failed to upload media: {response.text}")
                return None

    except Exception as e:
        print(f"Error uploading media: {e}")
        return None

def create_woocommerce_category(name, parent_id=None):
    url = f"{wc_url}/wp-json/wc/v3/products/categories"
    data = {"name": name}
    if parent_id:
        data["parent"] = parent_id

    response = requests.post(
        url,
        json=data,
        auth=(consumer_key, consumer_secret),
        headers={"Content-Type": "application/json"}
    )

    if response.status_code == 201:
        return response.json()["id"]
    else:
        print(f"❌ Failed to create category '{name}': {response.text}")
        return None

def upload_images_to_wordpress(product_id, image_urls, keep_temp_folder=False):
    folder_name = f"{product_id}"
    folder_path = f"temp_images/{folder_name}"
    uploaded_urls = []
    local_paths = []
    try:
        os.makedirs(folder_path, exist_ok=True)
        for i, image_url in enumerate(image_urls):
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Referer": "https://www.aliexpress.com/",
            }
            response = requests.get(image_url, headers=headers, timeout=10)
            response.raise_for_status()

            # Give each image a unique name to avoid WP caching the old one
            image_filename = f"image_{product_id}_{i}.jpg"
            image_path = os.path.join(folder_path, image_filename)
            
            with open(image_path, "wb") as f:
                f.write(response.content)

            local_paths.append(image_path)
            uploaded_url = upload_wordpress_media(image_path, wp_url, wp_username, wp_password)
            if uploaded_url:
                uploaded_urls.append({"src": uploaded_url})

    except Exception as e:
        print(f"Error uploading images to WordPress: {e}")
    finally:
        if not keep_temp_folder:
            shutil.rmtree(folder_path, ignore_errors=True)

    return uploaded_urls, local_paths

def get_all_categories():
    """
    Fetch all WooCommerce categories using the REST API.
    Uses a global cache to avoid repeated API calls.
    """
    global all_categories
    if all_categories:
        return all_categories
    try:
        page = 1
        categories = []
        while True:
            response = requests.get(
                f"{wc_url}/wp-json/wc/v3/products/categories",
                params={"page": page, "per_page": 100},
                auth=(consumer_key, consumer_secret),
                headers={"Content-Type": "application/json"}
            )
            if response.status_code == 200:
                page_cats = response.json()
                if not page_cats:
                    break
                categories.extend(page_cats)
                page += 1
            else:
                print(f"Failed to fetch categories: {response.text}")
                break
        all_categories = categories
    except Exception as e:
        print(f"Error fetching categories: {e}")
        return []
    return all_categories

def find_wordpress_nearest_category(category_name):
    try:

        all_categories = get_all_categories()
        real_categories = [{"name": cat["name"], "id": cat["id"]}
                            for cat in all_categories]
        nearest_category_id = find_nearest_category(
            category_name, categories=real_categories)
        return nearest_category_id
    except Exception as e:
        logging.error(f"Error finding nearest category: {e}")

def get_parent_category_name(category_name):
    """
    Given a category name, return its parent category name from all_categories.
    Returns None if not found or if it's a parent itself.
    """
    categoryId = find_wordpress_nearest_category(category_name) 
    cats = get_all_categories()
    for cat in cats:
        if cat["id"] == int(categoryId) :
            input_cat_parent_id = cat.get("parent")
            if input_cat_parent_id == 0:
                return cat["name"]
            parent_cat = next((c for c in cats if c["id"] == input_cat_parent_id), None)
            if parent_cat:
                return parent_cat["name"]
            else: 
                return cat["name"]
    return None
    
def update_affiliate_links():
    """
    Fetch all WooCommerce products, find products with Amazon affiliate links
    containing the old tag, and update them with the new tag.
    """
    old_tag = "kaymio-20"
    new_tag = "kaymio09-20"
    amazon_url_pattern = r"https://www\.amazon\.com/dp/([A-Z0-9]{10})\?tag=" + old_tag

    try:
        page = 1
        while True:
            # Fetch products page by page
            response = requests.get(
                f"{wc_url}/wp-json/wc/v3/products",
                params={"page": page, "per_page": 100},
                auth=(consumer_key, consumer_secret),
                headers={"Content-Type": "application/json"}
            )
            if response.status_code != 200:
                print(f"Failed to fetch products: {response.text}")
                break

            products = response.json()
            if not products:
                break

            for product in products:
                external_url = product.get("external_url", "")
                match = re.match(amazon_url_pattern, external_url)
                if match:
                    asin = match.group(1)
                    new_url = f"https://www.amazon.com/dp/{asin}?tag={new_tag}"
                    product_id = product["id"]

                    # Update product with the new URL
                    update_response = requests.put(
                        f"{wc_url}/wp-json/wc/v3/products/{product_id}",
                        json={"external_url": new_url},
                        auth=(consumer_key, consumer_secret),
                        headers={"Content-Type": "application/json"}
                    )

                    if update_response.status_code == 200:
                        print(f"Updated product {product_id} with new URL: {new_url}")
                    else:
                        print(f"Failed to update product {product_id}: {update_response.text}")

            page += 1

    except Exception as e:
        print(f"Error updating affiliate links: {e}")

def list_woocommerce_products(page: int = 1, per_page: int = 100):
    """Return paginated WooCommerce products with total headers."""
    if not all([wc_url, consumer_key, consumer_secret]):
        return {"error": "WooCommerce API credentials are missing."}
    try:
        response = requests.get(
            f"{wc_url}/wp-json/wc/v3/products",
            params={"page": max(1, int(page)), "per_page": max(1, min(int(per_page), 100))},
            auth=(consumer_key, consumer_secret),
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
    except Exception as exc:
        return {"error": f"Unable to fetch WooCommerce products: {exc}"}

    if response.status_code >= 400:
        return {"error": f"WooCommerce products list failed: {response.status_code} - {response.text}"}

    items = response.json() if isinstance(response.json(), list) else []
    total = int(response.headers.get("X-WP-Total", "0") or 0)
    total_pages = int(response.headers.get("X-WP-TotalPages", "1") or 1)
    return {
        "items": items,
        "page": max(1, int(page)),
        "per_page": max(1, min(int(per_page), 100)),
        "total": total,
        "total_pages": max(1, total_pages),
    }

def delete_woocommerce_product(product_id: int):
    """Delete WooCommerce product permanently."""
    if not all([wc_url, consumer_key, consumer_secret]):
        return {"error": "WooCommerce API credentials are missing."}
    try:
        response = requests.delete(
            f"{wc_url}/wp-json/wc/v3/products/{int(product_id)}",
            params={"force": "true"},
            auth=(consumer_key, consumer_secret),
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
    except Exception as exc:
        return {"error": f"Unable to delete WooCommerce product: {exc}"}

    if response.status_code >= 400:
        return {"error": f"WooCommerce product delete failed: {response.status_code} - {response.text}"}
    return {"ok": True, "product_id": int(product_id)}


def delete_wordpress_media(media_id: int):
    """Delete WordPress media permanently."""
    if not all([wp_url, wp_username, wp_password]):
        return {"error": "WordPress API credentials are missing."}
    try:
        response = requests.delete(
            f"{wp_url}/wp-json/wp/v2/media/{int(media_id)}",
            params={"force": "true"},
            auth=(wp_username, wp_password),
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
    except Exception as exc:
        return {"error": f"Unable to delete WordPress media: {exc}"}

    if response.status_code >= 400:
        return {"error": f"WordPress media delete failed: {response.status_code} - {response.text}"}
    return {"ok": True, "media_id": int(media_id)}


def _string_contains_aliexpress(value):
    if value is None:
        return False
    normalized_value = str(value).casefold()
    return any(token.casefold() in normalized_value for token in ALIEXPRESS_MATCH_TOKENS)


def _string_contains_any_token(value, tokens):
    if value is None:
        return False
    normalized_value = str(value).casefold()
    return any(str(token).casefold() in normalized_value for token in tokens)


def _product_external_link_matches(product, supplier_tokens):
    return _string_contains_any_token(product.get("external_url"), supplier_tokens)


def _normalize_like_text(value):
    normalized_value = str(value or "").casefold()
    normalized_value = normalized_value.replace("’", "'").replace("`", "'")
    normalized_value = re.sub(r"\s+", " ", normalized_value)
    return normalized_value.strip()


def _text_like_match(value, patterns):
    normalized_value = _normalize_like_text(value)
    if not normalized_value:
        return False
    for pattern in patterns or []:
        normalized_pattern = _normalize_like_text(pattern)
        if not normalized_pattern:
            continue
        if normalized_pattern in normalized_value or normalized_value in normalized_pattern:
            return True
    return False


def _build_category_id_to_name_map():
    category_map = {}
    for category in get_all_categories():
        if not isinstance(category, dict):
            continue
        try:
            category_id = int(category.get("id"))
        except (TypeError, ValueError):
            continue
        category_name = str(category.get("name") or "").strip()
        if category_name:
            category_map[category_id] = category_name
    return category_map


def _resolve_product_category_names(product, category_map=None):
    category_names = []
    category_map = category_map or {}
    for category in product.get("categories") or []:
        if not isinstance(category, dict):
            continue
        category_name = str(category.get("name") or "").strip()
        if category_name:
            category_names.append(category_name)
            continue
        try:
            category_id = int(category.get("id"))
        except (TypeError, ValueError):
            continue
        resolved_name = category_map.get(category_id, "").strip()
        if resolved_name:
            category_names.append(resolved_name)
    return category_names


def _product_matches_category_patterns(product, category_name_patterns, category_map=None):
    if not category_name_patterns:
        return True
    category_names = _resolve_product_category_names(product, category_map=category_map)
    return any(_text_like_match(category_name, category_name_patterns) for category_name in category_names)


def _extract_product_price_value(product):
    for field_name in ("price", "regular_price", "sale_price"):
        raw_value = product.get(field_name)
        if raw_value in (None, ""):
            continue
        normalized_value = re.sub(r"[^0-9.,-]", "", str(raw_value)).replace(",", "")
        if not normalized_value:
            continue
        try:
            return float(normalized_value)
        except ValueError:
            continue
    return None


def _product_matches_price_max(product, price_max=None):
    if price_max is None:
        return True
    product_price = _extract_product_price_value(product)
    if product_price is None:
        return False
    return product_price < float(price_max)


def _product_has_aliexpress_supplier(product):
    searchable_fields = [
        product.get("external_url"),
        product.get("permalink"),
        product.get("name"),
        product.get("short_description"),
        product.get("description"),
    ]
    if any(_string_contains_aliexpress(field) for field in searchable_fields):
        return True

    for metadata in product.get("meta_data") or []:
        if not isinstance(metadata, dict):
            continue
        if _string_contains_aliexpress(metadata.get("key")):
            return True
        if _string_contains_aliexpress(metadata.get("value")):
            return True
    return False


def _extract_product_media_ids(product):
    media_ids = []
    for image in product.get("images") or []:
        if not isinstance(image, dict):
            continue
        try:
            media_id = int(image.get("id"))
        except (TypeError, ValueError):
            continue
        if media_id > 0:
            media_ids.append(media_id)
    return media_ids


def _fetch_all_woocommerce_products(per_page: int = 100):
    products = []
    page = 1
    while True:
        result = list_woocommerce_products(page=page, per_page=per_page)
        if result.get("error"):
            return {"error": result["error"], "items": products}
        page_items = result.get("items") or []
        if not page_items:
            break
        products.extend(page_items)
        if page >= result.get("total_pages", page):
            break
        page += 1
    return {"items": products}


def count_products_with_amazon_or_aliexpress_links():
    """
    Count WooCommerce products whose external product link points to Amazon or AliExpress.
    """
    product_result = _fetch_all_woocommerce_products()
    if product_result.get("error"):
        return {
            "amazon_count": 0,
            "aliexpress_count": 0,
            "total_count": 0,
            "errors": [product_result["error"]],
        }

    amazon_count = 0
    aliexpress_count = 0

    for product in product_result.get("items", []):
        if not isinstance(product, dict):
            continue

        has_amazon_link = _product_external_link_matches(product, AMAZON_MATCH_TOKENS)
        has_aliexpress_link = _product_external_link_matches(product, ALIEXPRESS_MATCH_TOKENS)

        if has_amazon_link:
            amazon_count += 1
        if has_aliexpress_link:
            aliexpress_count += 1

    return {
        "amazon_count": amazon_count,
        "aliexpress_count": aliexpress_count,
        "total_count": amazon_count + aliexpress_count,
        "errors": [],
    }


def _resolve_category_name_patterns(category_name_patterns):
    if category_name_patterns is USE_DEFAULT_CATEGORY_PATTERNS:
        return list(DEFAULT_ALIEXPRESS_CATEGORY_PATTERNS)
    if category_name_patterns is None:
        return []
    return list(category_name_patterns)


def _find_aliexpress_products_by_category_names_details(
    category_name_patterns=USE_DEFAULT_CATEGORY_PATTERNS,
    price_max=None,
):
    """
    Find AliExpress products whose categories partially match any provided category pattern.
    Matching is case-insensitive and behaves like a local LIKE %%pattern%% search.
    When price_max is provided, only products with a price strictly lower than that value are matched.
    """
    category_name_patterns = _resolve_category_name_patterns(category_name_patterns)
    product_result = _fetch_all_woocommerce_products()
    if product_result.get("error"):
        return {
            "matched_products": [],
            "matched_count": 0,
            "category_patterns": category_name_patterns,
            "price_max": price_max,
            "errors": [product_result["error"]],
        }

    category_map = _build_category_id_to_name_map()
    matched_products = []
    for product in product_result.get("items", []):
        if not isinstance(product, dict):
            continue
        product_id = product.get("id")
        product_link = str(product.get("external_url") or product.get("permalink") or "")
        category_names = _resolve_product_category_names(product, category_map=category_map)
        product_price = _extract_product_price_value(product)
        is_aliexpress_match = _product_has_aliexpress_supplier(product)
        is_category_match = _product_matches_category_patterns(
            product,
            category_name_patterns,
            category_map=category_map,
        )
        is_price_match = _product_matches_price_max(product, price_max)
        is_match = is_aliexpress_match and is_category_match and is_price_match
        print(
            f"AliExpress category match check | product_id={product_id} | "
            f"matched={is_match} | aliexpress={is_aliexpress_match} | "
            f"category_match={is_category_match} | price_match={is_price_match} | "
            f"price={product_price} | link={product_link} | categories={category_names}"
        )
        if not is_match:
            continue
        matched_products.append(
            {
                "id": product_id,
                "name": str(product.get("name") or ""),
                "link": product_link,
                "price": product_price,
                "categories": category_names,
                "product": product,
            }
        )

    return {
        "matched_products": matched_products,
        "matched_count": len(matched_products),
        "category_patterns": category_name_patterns,
        "price_max": price_max,
        "errors": [],
    }


def find_aliexpress_products_by_category_names(
    category_name_patterns=USE_DEFAULT_CATEGORY_PATTERNS,
    price_max=None,
):
    """Return only the matched count for AliExpress products in matching categories."""
    result = _find_aliexpress_products_by_category_names_details(category_name_patterns, price_max=price_max)
    if result.get("errors"):
        for error in result["errors"]:
            print(error)
        return 0
    print(f"Matched AliExpress products count: {result['matched_count']}")
    return result["matched_count"]


def remove_aliexpress_products_and_media(
    category_name_patterns=USE_DEFAULT_CATEGORY_PATTERNS,
    price_max=None,
):
    """
    Delete AliExpress WooCommerce products and remove their media.
    When category_name_patterns is provided, only products in matching categories are deleted.
    When price_max is provided, only products with a price lower than that value are deleted.
    """
    match_result = _find_aliexpress_products_by_category_names_details(
        category_name_patterns,
        price_max=price_max,
    )
    if match_result.get("errors"):
        return {
            "deleted_products": 0,
            "deleted_media": 0,
            "matched_products": 0,
            "errors": list(match_result["errors"]),
        }

    matched_products = [item.get("product") for item in match_result.get("matched_products", [])]
    if not matched_products:
        print(
            "No WooCommerce products matched the AliExpress cleanup for categories: "
            f"{match_result.get('category_patterns', [])}"
        )
        return {
            "deleted_products": 0,
            "deleted_media": 0,
            "matched_products": 0,
            "errors": [],
        }

    deleted_products = 0
    deleted_media = 0
    deleted_media_ids = set()
    errors = []

    for product in matched_products:
        product_id = product.get("id")
        if not isinstance(product_id, int) or product_id <= 0:
            errors.append(f"Skipping invalid product id: {product_id}")
            continue

        media_ids = _extract_product_media_ids(product)
        product_name = product.get("name") or f"Product {product_id}"

        delete_product_result = delete_woocommerce_product(product_id)
        if delete_product_result.get("error"):
            errors.append(f"Product {product_id} delete failed: {delete_product_result['error']}")
            continue

        deleted_products += 1
        print(f"Deleted WooCommerce product {product_id}: {product_name}")

        for media_id in media_ids:
            if media_id in deleted_media_ids:
                continue
            delete_media_result = delete_wordpress_media(media_id)
            if delete_media_result.get("error"):
                errors.append(f"Media {media_id} delete failed: {delete_media_result['error']}")
                continue
            deleted_media_ids.add(media_id)
            deleted_media += 1
            print(f"Deleted WordPress media {media_id} linked to product {product_id}")

    print(
        "AliExpress cleanup complete. "
        f"Matched: {len(matched_products)}, "
        f"Deleted products: {deleted_products}, "
        f"Deleted media: {deleted_media}, "
        f"Errors: {len(errors)}"
    )
    return {
        "deleted_products": deleted_products,
        "deleted_media": deleted_media,
        "matched_products": len(matched_products),
        "errors": errors,
    }


def main() -> int:
    matched_count = find_aliexpress_products_by_category_names()
    print(f"Final matched count: {matched_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
