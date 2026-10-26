"""Instagram state management (scheduler, media routes, comment replies) backed by database."""
from __future__ import annotations

import datetime as dt
import json
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


STORY_SCHEDULER_STATE_KEY = "instagram_story_scheduler"


def load_scheduler_state(key: str) -> Dict[str, Any]:
    """Load a scheduler's run state (last_run_date, last result, ...) by key."""
    with session_scope() as session:
        row = session.query(InstagramSchedulerState).filter_by(key=key).first()

        if row and row.value:
            try:
                return json.loads(row.value)
            except json.JSONDecodeError:
                return {}
    return {}


def save_scheduler_state(key: str, state: Dict[str, Any]) -> None:
    """Replace a scheduler's run state."""
    with session_scope() as session:
        row = session.query(InstagramSchedulerState).filter_by(key=key).first()

        if row:
            row.value = json.dumps(state)
        else:
            row = InstagramSchedulerState(key=key, value=json.dumps(state))
            session.add(row)
        session.commit()


def update_scheduler_state(key: str, **fields: Any) -> Dict[str, Any]:
    """Merge fields into a scheduler's run state and return the result."""
    state = load_scheduler_state(key)
    state.update(fields)
    save_scheduler_state(key, state)
    return state


def load_instagram_scheduler_state() -> Dict[str, Any]:
    """Load the story scheduler state from database (last_run_date, etc.)."""
    return load_scheduler_state(STORY_SCHEDULER_STATE_KEY)


def save_instagram_scheduler_state(state: Dict[str, Any]) -> None:
    """Save the story scheduler state to database."""
    save_scheduler_state(STORY_SCHEDULER_STATE_KEY, state)


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


def register_media_reply_route(
    media_id: str,
    candidate: Dict[str, Any],
    *,
    reply_surface: str,
) -> bool:
    """Map a published media id to its product so comment/DM replies can send the link.

    Returns False (and stores nothing) when the media id or affiliate link is missing.
    """
    normalized_media_id = str(media_id or "").strip()
    affiliate_link = str(candidate.get("affiliate_link") or "").strip()
    if not normalized_media_id or not affiliate_link:
        return False
    upsert_instagram_media_reply_route(
        normalized_media_id,
        {
            "product_id": str(candidate.get("product_id") or ""),
            "affiliate_link": affiliate_link,
            "product_url": str(candidate.get("product_url") or "").strip(),
            "title": str(candidate.get("title") or ""),
            "description": str(candidate.get("description") or ""),
            "reply_surface": reply_surface,
            "published_at": dt.datetime.utcnow().isoformat(),
        },
    )
    return True


def prune_old_story_reply_routes(max_age_hours: int = 24) -> int:
    """Delete story reply routes older than max_age_hours. Returns number of deleted rows."""
    cutoff = dt.datetime.utcnow() - dt.timedelta(hours=max_age_hours)
    with session_scope() as session:
        deleted = (
            session.query(InstagramMediaReplyRoute)
            .filter(
                InstagramMediaReplyRoute.reply_surface == "story",
                InstagramMediaReplyRoute.created_at < cutoff,
            )
            .delete(synchronize_session=False)
        )
        session.commit()
    return deleted


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


