"""Persistence for promo events (Black Friday, Christmas, ...), edited on the Automation page."""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

from .db import session_scope
from .models import PromoEvent

EDITABLE_FIELDS = (
    "name",
    "start_date",
    "end_date",
    "enabled",
    "amazon_lists",
    "aliexpress_queries",
    "hashtags",
    "caption_line",
    "website_category",
    "min_discount_percent",
    "source",
)


def _to_dict(row: PromoEvent) -> Dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "start_date": row.start_date.isoformat() if row.start_date else None,
        "end_date": row.end_date.isoformat() if row.end_date else None,
        "enabled": bool(row.enabled),
        "amazon_lists": row.amazon_lists or "",
        "aliexpress_queries": row.aliexpress_queries or "",
        "hashtags": row.hashtags or "",
        "caption_line": row.caption_line or "",
        "website_category": row.website_category or "",
        "min_discount_percent": int(row.min_discount_percent or 0),
        "source": row.source or "manual",
    }


def list_promo_events() -> List[Dict[str, Any]]:
    with session_scope() as session:
        rows = session.query(PromoEvent).order_by(PromoEvent.start_date.desc()).all()
        return [_to_dict(row) for row in rows]


def get_promo_event(event_id: int) -> Optional[Dict[str, Any]]:
    with session_scope() as session:
        row = session.get(PromoEvent, event_id)
        return _to_dict(row) if row else None


def active_promo_events(today: dt.date) -> List[Dict[str, Any]]:
    """Enabled events covering ``today``, the one that started most recently first."""
    with session_scope() as session:
        rows = (
            session.query(PromoEvent)
            .filter(
                PromoEvent.enabled.is_(True),
                PromoEvent.start_date <= today,
                PromoEvent.end_date >= today,
            )
            .order_by(PromoEvent.start_date.desc(), PromoEvent.id.desc())
            .all()
        )
        return [_to_dict(row) for row in rows]


def save_promo_event(fields: Dict[str, Any], event_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Create (no ``event_id``) or update an event from already-validated fields."""
    with session_scope() as session:
        row = session.get(PromoEvent, event_id) if event_id else PromoEvent()
        if row is None:
            return None
        for key in EDITABLE_FIELDS:
            if key in fields:
                setattr(row, key, fields[key])
        session.add(row)
        session.flush()
        return _to_dict(row)


def delete_promo_event(event_id: int) -> bool:
    with session_scope() as session:
        return session.query(PromoEvent).filter_by(id=event_id).delete() > 0
