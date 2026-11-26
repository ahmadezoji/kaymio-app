"""Promo events (Black Friday, Christmas, ...) that steer discovery, copy and publishing."""
from .model import PromoEvent
from .presets import build_preset, list_presets
from .service import get_active_event, validate_event_payload

__all__ = ["PromoEvent", "build_preset", "get_active_event", "list_presets", "validate_event_payload"]
