"""MySQL persistence layer for the Kaymio app.

Exposes the SQLAlchemy engine/session plumbing plus the dict-shaped state API
(`load_app_state`, `save_app_state`, ...) that replaces the old JSON files.
"""
from .db import Base, SessionLocal, engine, get_engine, init_db, session_scope
from .state_store import (
    get_product_entry,
    load_app_state,
    save_app_state,
    save_product_entry,
)

__all__ = [
    "Base",
    "SessionLocal",
    "engine",
    "get_engine",
    "init_db",
    "session_scope",
    "load_app_state",
    "save_app_state",
    "get_product_entry",
    "save_product_entry",
]
