"""SQLAlchemy engine, session, and schema bootstrap for the Kaymio MySQL store."""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model."""


def _build_database_url() -> str:
    """Assemble the MySQL connection URL from environment variables."""
    host = os.getenv("DB_HOST", "127.0.0.1")
    port = os.getenv("DB_PORT", "3306")
    name = os.getenv("DB_NAME", "kaymio")
    user = os.getenv("DB_USER", "kaymio")
    password = os.getenv("DB_PASSWORD", "")
    return (
        f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port}/{name}?charset=utf8mb4"
    )


def get_engine() -> Engine:
    """Return the lazily-initialised process-wide engine."""
    global engine, SessionLocal
    if engine is None:
        engine = create_engine(
            _build_database_url(),
            pool_pre_ping=True,
            pool_recycle=280,
            future=True,
        )
        SessionLocal.configure(bind=engine)
    return engine


# Engine is created lazily so importing the package never forces a DB connection.
engine: Engine | None = None
SessionLocal = sessionmaker(autocommit=False, autoflush=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional scope around a series of operations."""
    get_engine()
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create all tables (if missing) and seed the default admin + app_meta."""
    # Import models so they register on Base.metadata before create_all.
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=get_engine())
    _seed_default_admin()
    _migrate_instagram_states_from_json()
    logger.info("Database initialised (tables ensured, admin seeded).")


def _seed_default_admin() -> None:
    """Insert a single default admin user if the users table is empty."""
    from werkzeug.security import generate_password_hash

    from .models import User

    username = os.getenv("ADMIN_DEFAULT_USERNAME", "admin")
    password = os.getenv("ADMIN_DEFAULT_PASSWORD", "admin")
    email = os.getenv("ADMIN_DEFAULT_EMAIL", "admin@kaymio.com")

    with session_scope() as session:
        existing = session.query(User).filter(User.username == username).one_or_none()
        if existing is not None:
            return
        session.add(
            User(
                username=username,
                password_hash=generate_password_hash(password),
                email=email,
                role="admin",
                is_active=True,
            )
        )
        logger.info("Seeded default admin user '%s'.", username)


def _migrate_instagram_states_from_json() -> None:
    """Auto-migrate Instagram state files from JSON to database on first run."""
    from pathlib import Path

    from .instagram_state import (
        migrate_scheduler_state_from_json,
        migrate_media_routes_from_json,
        migrate_comment_reply_states_from_json,
    )

    state_dir = Path(os.getenv("STATE_DIR", "data"))
    if not state_dir.exists():
        return

    try:
        migrate_scheduler_state_from_json(state_dir / "instagram_story_scheduler.json")
        migrate_media_routes_from_json(state_dir / "instagram_story_routes.json")
        migrate_comment_reply_states_from_json(state_dir / "instagram_comment_reply_state.json")
    except Exception as e:
        logger.warning("Instagram state migration encountered an error: %s", e)
