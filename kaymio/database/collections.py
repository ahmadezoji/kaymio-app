"""Database access for Instagram FEED collections."""
from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any, Dict, List, Optional

from .db import session_scope
from .models import Collection, CollectionProduct


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "collection"


def _unique_slug(session, name: str, exclude_id: Optional[int] = None) -> str:
    base_slug = _slugify(name)
    slug = base_slug
    suffix = 2
    while True:
        query = session.query(Collection).filter(Collection.slug == slug)
        if exclude_id is not None:
            query = query.filter(Collection.id != exclude_id)
        if query.first() is None:
            return slug
        slug = f"{base_slug}-{suffix}"
        suffix += 1


def _collection_to_dict(row: Collection) -> Dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "slug": row.slug,
        "description": row.description,
        "status": row.status,
        "caption": row.caption,
        "hashtags": json.loads(row.hashtags) if row.hashtags else [],
        "landing_page_html": row.landing_page_html,
        "wordpress_page_id": row.wordpress_page_id,
        "wordpress_page_url": row.wordpress_page_url,
        "instagram_media_id": row.instagram_media_id,
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "products": [
            {
                "id": p.id,
                "wc_product_id": p.wc_product_id,
                "title": p.title,
                "product_url": p.product_url,
                "image_url": p.image_url,
                "position": p.position,
            }
            for p in row.products
        ],
    }


def list_collections() -> List[Dict[str, Any]]:
    with session_scope() as session:
        rows = session.query(Collection).order_by(Collection.updated_at.desc()).all()
        return [_collection_to_dict(row) for row in rows]


def get_collection(collection_id: int) -> Optional[Dict[str, Any]]:
    with session_scope() as session:
        row = session.query(Collection).filter_by(id=collection_id).first()
        return _collection_to_dict(row) if row else None


def create_collection(name: str, description: str = "") -> Dict[str, Any]:
    with session_scope() as session:
        slug = _unique_slug(session, name)
        row = Collection(name=name, slug=slug, description=description, status="draft")
        session.add(row)
        session.flush()
        return _collection_to_dict(row)


def update_collection(collection_id: int, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Update scalar fields on a collection. Recognized keys: name, description, status,
    caption, hashtags (list), landing_page_html, wordpress_page_id, wordpress_page_url,
    instagram_media_id, published_at (bool flag to set to now)."""
    with session_scope() as session:
        row = session.query(Collection).filter_by(id=collection_id).first()
        if not row:
            return None

        if "name" in fields and fields["name"] and fields["name"] != row.name:
            row.name = fields["name"]
            row.slug = _unique_slug(session, fields["name"], exclude_id=collection_id)
        if "description" in fields:
            row.description = fields["description"]
        if "status" in fields:
            row.status = fields["status"]
        if "caption" in fields:
            row.caption = fields["caption"]
        if "hashtags" in fields:
            row.hashtags = json.dumps(fields["hashtags"])
        if "landing_page_html" in fields:
            row.landing_page_html = fields["landing_page_html"]
        if "wordpress_page_id" in fields:
            row.wordpress_page_id = fields["wordpress_page_id"]
        if "wordpress_page_url" in fields:
            row.wordpress_page_url = fields["wordpress_page_url"]
        if "instagram_media_id" in fields:
            row.instagram_media_id = fields["instagram_media_id"]
        if fields.get("mark_published"):
            row.published_at = dt.datetime.utcnow()
            row.status = "published"

        session.flush()
        return _collection_to_dict(row)


def delete_collection(collection_id: int) -> bool:
    with session_scope() as session:
        row = session.query(Collection).filter_by(id=collection_id).first()
        if not row:
            return False
        session.delete(row)
        return True


def set_collection_products(collection_id: int, products: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Replace the product list for a collection.

    Each item in `products` should have: wc_product_id, title, product_url, image_url.
    """
    with session_scope() as session:
        row = session.query(Collection).filter_by(id=collection_id).first()
        if not row:
            return None

        session.query(CollectionProduct).filter_by(collection_id=collection_id).delete()

        for position, product in enumerate(products):
            session.add(
                CollectionProduct(
                    collection_id=collection_id,
                    wc_product_id=int(product["wc_product_id"]),
                    title=product.get("title"),
                    product_url=product.get("product_url"),
                    image_url=product.get("image_url"),
                    position=position,
                )
            )

        session.flush()
        session.refresh(row)
        return _collection_to_dict(row)
