"""WooCommerce admin list/delete view for Kaymio."""
from __future__ import annotations

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from kaymio.wordpress.wordpress_api_helper import delete_woocommerce_product, list_woocommerce_products

kaymio_wp_admin_bp = Blueprint("kaymio_wp_admin", __name__)


def _safe_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@kaymio_wp_admin_bp.route("/kaymio-wp-admin", methods=["GET"])
def kaymio_wp_admin_view():
    page = max(1, _safe_int(request.args.get("page", "1"), 1))
    return render_template(
        "kaymio_wp_admin.html",
        page=page,
    )


@kaymio_wp_admin_bp.route("/kaymio-wp-admin/data", methods=["GET"])
def kaymio_wp_admin_data():
    page = max(1, _safe_int(request.args.get("page", "1"), 1))
    per_page = 100
    result = list_woocommerce_products(page=page, per_page=per_page)
    if result.get("error"):
        return jsonify({"error": result["error"], "items": [], "page": page, "total_pages": 1, "total": 0})

    items = []
    for product in result.get("items", []):
        images = product.get("images") or []
        image_src = images[0].get("src") if images and isinstance(images[0], dict) else ""
        product_url = product.get("permalink") or product.get("external_url") or ""
        items.append(
            {
                "id": product.get("id"),
                "name": product.get("name") or "",
                "image_src": image_src or "",
                "product_url": product_url or "",
            }
        )

    return jsonify(
        {
            "items": items,
            "page": result.get("page", page),
            "per_page": result.get("per_page", per_page),
            "total_pages": result.get("total_pages", 1),
            "total": result.get("total", 0),
            "error": None,
        }
    )


@kaymio_wp_admin_bp.route("/kaymio-wp-admin/delete", methods=["POST"])
def kaymio_wp_admin_delete():
    product_id = _safe_int(request.form.get("product_id", "0"), 0)
    page = max(1, _safe_int(request.form.get("page", "1"), 1))
    if product_id <= 0:
        flash("Invalid product id.", "error")
        return redirect(url_for("kaymio_wp_admin.kaymio_wp_admin_view", page=page))

    result = delete_woocommerce_product(product_id)
    if result.get("error"):
        flash(result["error"], "error")
    else:
        flash(f"Product {product_id} deleted.", "success")
    return redirect(url_for("kaymio_wp_admin.kaymio_wp_admin_view", page=page))
