import sys
from flask import logging
import requests
import base64
from dotenv import load_dotenv
import os
import base64
import datetime
import shutil
import uuid









load_dotenv()


# WordPress site configuration
wp_url = os.getenv('WORDPRESS_API_URL', 'https://yoursite.com')
wp_username = os.getenv('WORDPRESS_USERNAME')
wp_password = os.getenv('WORDPRESS_PASSWORD')

wc_url = os.getenv('WORDPRESS_API_URL')  # Your base site URL
consumer_key = os.getenv('WC_CONSUMER_KEY')
consumer_secret = os.getenv('WC_CONSUMER_SECRET')

all_categories = []

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


def create_woocommerce_product(name, description, price, image_path=None, tags=None, images=None, affiliate_link=None, category_id=None):
    if not all([consumer_key, consumer_secret]):
        print("WooCommerce API credentials not found")
        return None

    # Prepare basic auth header
    auth = (consumer_key, consumer_secret)

    # Upload image if provided
    image_urls = []
    if image_path and os.path.exists(image_path):
        image_url = upload_wordpress_media(
            image_path, wp_url, wp_username, wp_password)
        if image_url:
            image_urls.append({"src": image_url})
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
        "images": images or [],
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
        from gpt_api.open_ai import find_nearest_category

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
    



if __name__ == "__main__":
    # ppid  = get_category_id_by_name("🧒 Kids & Baby")
    print(f"Category ID for '🧒 Kids & Baby':")

   

