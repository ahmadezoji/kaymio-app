"""API routes for the Instagram FEED Collections publisher (Phase 1: core/API only)."""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, render_template, request

from kaymio.database.collections import (
    create_collection,
    delete_collection,
    get_collection,
    list_collections,
    set_collection_products,
    update_collection,
)
from kaymio.features.collections_helper import (
    create_collection_landing_page_html,
    find_related_products,
    generate_collection_metadata,
    publish_collection_to_instagram,
    publish_collection_to_wordpress,
)

logger = logging.getLogger(__name__)

collections_bp = Blueprint("collections", __name__, url_prefix="/api/collections")
collections_page_bp = Blueprint("collections_page", __name__)


@collections_page_bp.route("/collections", methods=["GET"])
def collections_page():
    return render_template("collections.html")


@collections_bp.route("", methods=["GET"])
def api_list_collections():
    return jsonify({"collections": list_collections()})


@collections_bp.route("", methods=["POST"])
def api_create_collection():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Collection name is required."}), 400

    collection = create_collection(name=name, description=(payload.get("description") or "").strip())
    return jsonify({"collection": collection}), 201


@collections_bp.route("/<int:collection_id>", methods=["GET"])
def api_get_collection(collection_id: int):
    collection = get_collection(collection_id)
    if not collection:
        return jsonify({"error": "Collection not found."}), 404
    return jsonify({"collection": collection})


@collections_bp.route("/<int:collection_id>", methods=["PUT"])
def api_update_collection(collection_id: int):
    payload = request.get_json(silent=True) or {}
    collection = update_collection(collection_id, payload)
    if not collection:
        return jsonify({"error": "Collection not found."}), 404
    return jsonify({"collection": collection})


@collections_bp.route("/<int:collection_id>", methods=["DELETE"])
def api_delete_collection(collection_id: int):
    if not delete_collection(collection_id):
        return jsonify({"error": "Collection not found."}), 404
    return jsonify({"ok": True})


@collections_bp.route("/<int:collection_id>/generate", methods=["POST"])
def api_generate_collection(collection_id: int):
    """Fetch related WooCommerce products and generate AI captions/description/landing page."""
    collection = get_collection(collection_id)
    if not collection:
        return jsonify({"error": "Collection not found."}), 404

    name = collection["name"]
    description = collection.get("description") or ""

    products = find_related_products(name, description, limit=5)
    if not products:
        return jsonify({"error": "No related products found for this collection."}), 422

    metadata = generate_collection_metadata(name, description, products)
    landing_html = create_collection_landing_page_html(name, metadata["landing_copy"], products)

    collection = set_collection_products(collection_id, products)
    collection = update_collection(collection_id, {
        "caption": metadata["caption"],
        "hashtags": metadata["hashtags"],
        "landing_page_html": landing_html,
    })

    return jsonify({"collection": collection})


@collections_bp.route("/<int:collection_id>/publish", methods=["POST"])
def api_publish_collection(collection_id: int):
    """Publish the collection's landing page to WordPress and the carousel to Instagram."""
    collection = get_collection(collection_id)
    if not collection:
        return jsonify({"error": "Collection not found."}), 404

    products = collection.get("products") or []
    if len(products) < 2:
        return jsonify({"error": "A collection needs at least 2 products before it can be published."}), 422
    if not collection.get("landing_page_html"):
        return jsonify({"error": "Generate the collection content before publishing."}), 422

    wp_result = publish_collection_to_wordpress(
        collection["name"], collection["slug"], collection["landing_page_html"]
    )
    if wp_result.get("error"):
        return jsonify({"error": f"WordPress publish failed: {wp_result['error']}"}), 502

    page_url = wp_result.get("url") or ""
    caption = collection.get("caption") or ""
    if page_url:
        caption = f"{caption}\n\n{page_url}"

    # Persist the WordPress result immediately so the landing page URL isn't
    # lost if the Instagram publish step below fails.
    collection = update_collection(collection_id, {
        "wordpress_page_id": str(wp_result.get("id") or ""),
        "wordpress_page_url": page_url,
    })

    image_urls = [p["image_url"] for p in products if p.get("image_url")]
    try:
        ig_result = publish_collection_to_instagram(image_urls, caption, collection.get("hashtags") or [])
    except Exception as exc:
        logger.exception("Instagram carousel publish failed for collection %s", collection_id)
        return jsonify({
            "error": f"Instagram publish failed: {exc}",
            "collection": collection,
        }), 502

    updated = update_collection(collection_id, {
        "instagram_media_id": str(ig_result.get("id") or ""),
        "mark_published": True,
    })

    return jsonify({"collection": updated})
