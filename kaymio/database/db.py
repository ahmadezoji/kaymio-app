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


def _ensure_columns(table_name: str, column_specs: dict[str, str]) -> None:
    """Idempotently add missing columns to an already-existing table.

    create_all() only creates missing tables, never adds columns to ones that
    already exist, so a column added to a model after a table has shipped to
    a live database needs this instead. Safe to call on every boot.
    """
    from sqlalchemy import inspect, text

    engine = get_engine()
    inspector = inspect(engine)
    if table_name not in inspector.get_table_names():
        return  # create_all() will create it fresh with all columns already.
    existing_columns = {col["name"] for col in inspector.get_columns(table_name)}
    with engine.begin() as conn:
        for column_name, ddl_type in column_specs.items():
            if column_name in existing_columns:
                continue
            logger.info("Adding missing column %s.%s", table_name, column_name)
            conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl_type}"))


def init_db() -> None:
    """Create all tables (if missing) and seed the default admin + app_meta."""
    # Import models so they register on Base.metadata before create_all.
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=get_engine())
    _ensure_columns("oauth_credentials", {"client_id": "TEXT", "client_secret": "TEXT"})
    _seed_default_admin()
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


