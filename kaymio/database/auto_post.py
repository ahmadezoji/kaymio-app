"""Persistence for auto-post candidates (Amazon product -> approval -> website + Instagram)."""
from __future__ import annotations

import datetime as dt
import json
from typing import Any, Dict, Iterable, List, Optional, Set

from .db import session_scope
from .models import AutoPostCandidate

STATUS_PENDING = "pending_approval"
STATUS_REJECTED = "rejected"
STATUS_EXPIRED = "expired"
STATUS_PUBLISHING = "publishing"
STATUS_PUBLISHED = "published"
STATUS_FAILED = "failed"

_JSON_FIELDS = ("product_data", "content")


def _loads(raw: Optional[str]) -> Dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _iso(value: Optional[dt.datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _to_dict(row: AutoPostCandidate) -> Dict[str, Any]:
    return {
        "id": row.id,
        "asin": row.asin,
        "status": row.status,
        "title": row.title,
        "product_url": row.product_url,
        "image_url": row.image_url,
        "product_data": _loads(row.product_data),
        "content": _loads(row.content),
        "telegram_chat_id": row.telegram_chat_id,
        "telegram_message_id": row.telegram_message_id,
        "website_product_url": row.website_product_url,
        "instagram_media_id": row.instagram_media_id,
        "error": row.error,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "decided_at": _iso(row.decided_at),
        "published_at": _iso(row.published_at),
    }


def _apply_fields(row: AutoPostCandidate, fields: Dict[str, Any]) -> None:
    for key, value in fields.items():
        if key in _JSON_FIELDS and not isinstance(value, str):
            value = json.dumps(value)
        setattr(row, key, value)


def create_candidate(
    *,
    asin: str,
    title: str,
    product_url: str,
    image_url: str,
    product_data: Dict[str, Any],
    content: Dict[str, Any],
) -> Dict[str, Any]:
    with session_scope() as session:
        row = AutoPostCandidate(asin=asin, status=STATUS_PENDING)
        _apply_fields(row, {
            "title": title,
            "product_url": product_url,
            "image_url": image_url,
            "product_data": product_data,
            "content": content,
        })
        session.add(row)
        session.flush()
        return _to_dict(row)


def get_candidate(candidate_id: int) -> Optional[Dict[str, Any]]:
    with session_scope() as session:
        row = session.get(AutoPostCandidate, candidate_id)
        return _to_dict(row) if row else None


def list_candidates(limit: int = 50) -> List[Dict[str, Any]]:
    with session_scope() as session:
        rows = (
            session.query(AutoPostCandidate)
            .order_by(AutoPostCandidate.created_at.desc())
            .limit(limit)
            .all()
        )
        return [_to_dict(row) for row in rows]


def known_candidate_asins() -> Set[str]:
    """Every ASIN ever proposed, so a product is never suggested twice."""
    with session_scope() as session:
        return {asin for (asin,) in session.query(AutoPostCandidate.asin).all()}


def update_candidate(candidate_id: int, **fields: Any) -> Optional[Dict[str, Any]]:
    with session_scope() as session:
        row = session.get(AutoPostCandidate, candidate_id)
        if row is None:
            return None
        _apply_fields(row, fields)
        session.flush()
        return _to_dict(row)


def transition_candidate(
    candidate_id: int,
    *,
    from_statuses: Iterable[str],
    to_status: str,
    **fields: Any,
) -> Optional[Dict[str, Any]]:
    """Atomically move a candidate between statuses.

    Returns the updated candidate, or None if it was not in one of
    ``from_statuses`` (e.g. a second click on an already-approved button).
    """
    with session_scope() as session:
        updated = (
            session.query(AutoPostCandidate)
            .filter(
                AutoPostCandidate.id == candidate_id,
                AutoPostCandidate.status.in_(list(from_statuses)),
            )
            .update({"status": to_status, "updated_at": dt.datetime.utcnow()}, synchronize_session=False)
        )
        if not updated:
            return None
        row = session.get(AutoPostCandidate, candidate_id)
        session.refresh(row)
        _apply_fields(row, fields)
        session.flush()
        return _to_dict(row)


def expire_stale_candidates(max_age_hours: float) -> List[Dict[str, Any]]:
    """Mark pending candidates older than ``max_age_hours`` as expired and return them."""
    cutoff = dt.datetime.utcnow() - dt.timedelta(hours=max_age_hours)
    with session_scope() as session:
        rows = (
            session.query(AutoPostCandidate)
            .filter(
                AutoPostCandidate.status == STATUS_PENDING,
                AutoPostCandidate.created_at < cutoff,
            )
            .all()
        )
        for row in rows:
            row.status = STATUS_EXPIRED
            row.decided_at = dt.datetime.utcnow()
        session.flush()
        return [_to_dict(row) for row in rows]


def delete_candidate(candidate_id: int) -> None:
    with session_scope() as session:
        session.query(AutoPostCandidate).filter_by(id=candidate_id).delete()
