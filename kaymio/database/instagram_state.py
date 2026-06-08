"""Instagram state management (scheduler, media routes, comment replies) backed by database.

Handles backward compatibility: auto-migrates from JSON files on first run.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from .db import session_scope
from .models import (
    InstagramSchedulerState,
    InstagramMediaReplyRoute,
    InstagramCommentReplyState,
)


# ==============================================================================
# Instagram Story Scheduler State
# ==============================================================================


def load_instagram_scheduler_state() -> Dict[str, Any]:
    """Load scheduler state from database (last_run_date, etc.)."""
    with session_scope() as session:
        row = session.query(InstagramSchedulerState).filter_by(
            key="instagram_story_scheduler"
        ).first()

        if row and row.value:
            try:
                return json.loads(row.value)
            except json.JSONDecodeError:
                return {}
    return {}


def save_instagram_scheduler_state(state: Dict[str, Any]) -> None:
    """Save scheduler state to database."""
    with session_scope() as session:
        row = session.query(InstagramSchedulerState).filter_by(
            key="instagram_story_scheduler"
        ).first()

        if row:
            row.value = json.dumps(state)
        else:
            row = InstagramSchedulerState(
                key="instagram_story_scheduler",
                value=json.dumps(state)
            )
            session.add(row)
        session.commit()


def migrate_scheduler_state_from_json(json_file: Path) -> None:
    """Auto-migrate from JSON file if DB is empty."""
    if not json_file.exists():
        return

    with session_scope() as session:
        existing = session.query(InstagramSchedulerState).filter_by(
            key="instagram_story_scheduler"
        ).first()

    if existing:
        return

    try:
        data = json.loads(json_file.read_text())
        if isinstance(data, dict):
            save_instagram_scheduler_state(data)
    except Exception:
        pass


# ==============================================================================
# Instagram Media Reply Routes
# ==============================================================================


def load_instagram_media_reply_routes() -> Dict[str, Any]:
    """Load all media reply routes: {media_id → {product_id, affiliate_link, ...}}."""
    with session_scope() as session:
        rows = session.query(InstagramMediaReplyRoute).all()

        result = {}
        for row in rows:
            result[row.media_id] = {
                "product_id": row.product_id or "",
                "affiliate_link": row.affiliate_link or "",
                "product_url": row.product_url or "",
                "title": row.title or "",
                "description": row.description or "",
                "reply_surface": row.reply_surface or "",
                "published_at": row.published_at or "",
            }
    return result


def save_instagram_media_reply_routes(routes: Dict[str, Any]) -> None:
    """Replace all media reply routes with the provided dict."""
    with session_scope() as session:
        session.query(InstagramMediaReplyRoute).delete()

        for media_id, route_data in routes.items():
            row = InstagramMediaReplyRoute(
                media_id=str(media_id or "").strip(),
                product_id=str(route_data.get("product_id") or "").strip() or None,
                affiliate_link=str(route_data.get("affiliate_link") or "").strip() or None,
                product_url=str(route_data.get("product_url") or "").strip() or None,
                title=str(route_data.get("title") or "").strip() or None,
                description=str(route_data.get("description") or "").strip() or None,
                reply_surface=str(route_data.get("reply_surface") or "").strip() or None,
                published_at=str(route_data.get("published_at") or "").strip() or None,
            )
            session.add(row)

        session.commit()


def upsert_instagram_media_reply_route(media_id: str, route_data: Dict[str, Any]) -> None:
    """Insert or update a single media reply route."""
    with session_scope() as session:
        row = session.query(InstagramMediaReplyRoute).filter_by(
            media_id=str(media_id or "").strip()
        ).first()

        if row:
            row.product_id = str(route_data.get("product_id") or "").strip() or None
            row.affiliate_link = str(route_data.get("affiliate_link") or "").strip() or None
            row.product_url = str(route_data.get("product_url") or "").strip() or None
            row.title = str(route_data.get("title") or "").strip() or None
            row.description = str(route_data.get("description") or "").strip() or None
            row.reply_surface = str(route_data.get("reply_surface") or "").strip() or None
            row.published_at = str(route_data.get("published_at") or "").strip() or None
        else:
            row = InstagramMediaReplyRoute(
                media_id=str(media_id or "").strip(),
                product_id=str(route_data.get("product_id") or "").strip() or None,
                affiliate_link=str(route_data.get("affiliate_link") or "").strip() or None,
                product_url=str(route_data.get("product_url") or "").strip() or None,
                title=str(route_data.get("title") or "").strip() or None,
                description=str(route_data.get("description") or "").strip() or None,
                reply_surface=str(route_data.get("reply_surface") or "").strip() or None,
                published_at=str(route_data.get("published_at") or "").strip() or None,
            )
            session.add(row)

        session.commit()


def migrate_media_routes_from_json(json_file: Path) -> None:
    """Auto-migrate from JSON file if DB is empty."""
    if not json_file.exists():
        return

    with session_scope() as session:
        existing = session.query(InstagramMediaReplyRoute).first()

    if existing:
        return

    try:
        data = json.loads(json_file.read_text())
        if isinstance(data, dict):
            save_instagram_media_reply_routes(data)
    except Exception:
        pass


# ==============================================================================
# Instagram Comment Reply States
# ==============================================================================


def load_instagram_comment_reply_states() -> Dict[str, Any]:
    """Load all comment reply states: {comment_id → {status, created_at, ...}}."""
    with session_scope() as session:
        rows = session.query(InstagramCommentReplyState).all()

        result = {}
        for row in rows:
            try:
                reply_data = json.loads(row.reply_data or "{}")
            except json.JSONDecodeError:
                reply_data = {}
            result[row.comment_id] = reply_data
    return result


def save_instagram_comment_reply_states(states: Dict[str, Any]) -> None:
    """Replace all comment reply states with the provided dict."""
    with session_scope() as session:
        session.query(InstagramCommentReplyState).delete()

        for comment_id, state_data in states.items():
            row = InstagramCommentReplyState(
                comment_id=str(comment_id or "").strip(),
                reply_data=json.dumps(state_data),
            )
            session.add(row)

        session.commit()


def upsert_instagram_comment_reply_state(
    comment_id: str,
    state_data: Dict[str, Any],
) -> Dict[str, Any]:
    """Insert or update a single comment reply state."""
    normalized_comment_id = str(comment_id or "").strip()
    if not normalized_comment_id:
        return {}

    with session_scope() as session:
        row = session.query(InstagramCommentReplyState).filter_by(
            comment_id=normalized_comment_id
        ).first()

        if row:
            row.reply_data = json.dumps(state_data)
        else:
            row = InstagramCommentReplyState(
                comment_id=normalized_comment_id,
                reply_data=json.dumps(state_data),
            )
            session.add(row)

        session.commit()

    return state_data


def migrate_comment_reply_states_from_json(json_file: Path) -> None:
    """Auto-migrate from JSON file if DB is empty."""
    if not json_file.exists():
        return

    with session_scope() as session:
        existing = session.query(InstagramCommentReplyState).first()

    if existing:
        return

    try:
        data = json.loads(json_file.read_text())
        if isinstance(data, dict):
            save_instagram_comment_reply_states(data)
    except Exception:
        pass
