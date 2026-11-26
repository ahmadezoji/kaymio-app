"""Which promo event is active, and validation of events edited in the UI.

Any feature can ask ``get_active_event()`` and adapt (discovery, captions,
website categories, ...). Events can come from presets, manual entry, or in
future from AI suggestions (``source="ai"``) -- they're all just rows.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Dict, Optional

from kaymio.database.promo_events import active_promo_events
from kaymio.features.events.model import PromoEvent

logger = logging.getLogger(__name__)

TEXT_FIELDS = ("amazon_lists", "aliexpress_queries", "hashtags", "caption_line", "website_category")


def get_active_event(today: Optional[dt.date] = None) -> Optional[PromoEvent]:
    """The active event for ``today`` (the most recently started one if several overlap)."""
    try:
        events = active_promo_events(today or dt.date.today())
    except Exception:
        logger.exception("Could not load promo events; continuing without one.")
        return None
    return PromoEvent.from_dict(events[0]) if events else None


def validate_event_payload(payload: Dict[str, Any], *, partial: bool = False) -> Dict[str, Any]:
    """Clean an event submitted from the UI. Raises ValueError with a readable message."""
    fields: Dict[str, Any] = {}
    if "name" in payload or not partial:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("Event name is required.")
        fields["name"] = name[:120]
    for key in ("start_date", "end_date"):
        if key in payload or not partial:
            try:
                fields[key] = dt.date.fromisoformat(str(payload.get(key) or ""))
            except ValueError:
                raise ValueError(f"{key.replace('_', ' ').capitalize()} must be a date (YYYY-MM-DD).") from None
    if "start_date" in fields and "end_date" in fields and fields["end_date"] < fields["start_date"]:
        raise ValueError("End date must be on or after the start date.")
    if "enabled" in payload:
        fields["enabled"] = bool(payload["enabled"])
    for key in TEXT_FIELDS:
        if key in payload:
            fields[key] = str(payload.get(key) or "").strip()
    if "min_discount_percent" in payload:
        try:
            value = int(payload.get("min_discount_percent") or 0)
        except (TypeError, ValueError):
            raise ValueError("Minimum discount must be a whole number.") from None
        if not 0 <= value <= 90:
            raise ValueError("Minimum discount must be between 0 and 90.")
        fields["min_discount_percent"] = value
    if "source" in payload and payload["source"] in ("manual", "preset", "ai"):
        fields["source"] = payload["source"]
    return fields
