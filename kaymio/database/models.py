"""Fully-normalized ORM models mirroring the legacy app_state.json shape.

Source of truth per product is ``platforms`` + ``assets`` + the global
``last_product_id``. The legacy ``preview`` / ``form_values`` / ``results``
blobs are derived and intentionally not persisted.
"""
from __future__ import annotations

import datetime as dt
from typing import List

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.utcnow()


class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(191), primary_key=True)
    market: Mapped[str | None] = mapped_column(String(64))
    sku_or_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    platforms: Mapped[List["ProductPlatform"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    assets: Mapped[List["ProductAsset"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ProductPlatform(Base):
    __tablename__ = "product_platforms"
    __table_args__ = (UniqueConstraint("product_id", "platform", name="uq_product_platform"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[str] = mapped_column(
        String(191), ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    # pinterest | instagram_feed | instagram_story | instagram_reel | youtube | tiktok | website
    platform: Mapped[str] = mapped_column(String(32))

    # Promoted, commonly-queried scalar columns (nullable; not every platform sets them).
    status: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    caption: Mapped[str | None] = mapped_column(Text)
    affiliate_link: Mapped[str | None] = mapped_column(Text)
    product_url: Mapped[str | None] = mapped_column(Text)
    pin_id: Mapped[str | None] = mapped_column(String(191))
    pin_url: Mapped[str | None] = mapped_column(Text)
    instagram_media_id: Mapped[str | None] = mapped_column(String(191))
    generated_image_path: Mapped[str | None] = mapped_column(Text)
    generated_video_path: Mapped[str | None] = mapped_column(Text)
    video_url: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    product: Mapped["Product"] = relationship(back_populates="platforms")
    attributes: Mapped[List["PlatformAttribute"]] = relationship(
        back_populates="platform_row",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    tags: Mapped[List["PlatformTag"]] = relationship(
        back_populates="platform_row",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class PlatformAttribute(Base):
    """Long-tail scalar key/values for a platform that aren't promoted to columns."""

    __tablename__ = "platform_attributes"
    __table_args__ = (
        UniqueConstraint("platform_id", "attr_key", name="uq_platform_attr"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("product_platforms.id", ondelete="CASCADE"), index=True
    )
    attr_key: Mapped[str] = mapped_column(String(191))
    attr_value: Mapped[str | None] = mapped_column(Text)

    platform_row: Mapped["ProductPlatform"] = relationship(back_populates="attributes")


class PlatformTag(Base):
    """List-valued platform fields (tags / hashtags / keywords)."""

    __tablename__ = "platform_tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("product_platforms.id", ondelete="CASCADE"), index=True
    )
    # tags | instagram_hashtags | tiktok_hashtags | youtube_keywords
    tag_type: Mapped[str] = mapped_column(String(48))
    value: Mapped[str] = mapped_column(String(255))
    position: Mapped[int] = mapped_column(Integer, default=0)

    platform_row: Mapped["ProductPlatform"] = relationship(back_populates="tags")


class ProductAsset(Base):
    """Per-product media references. Scalars use position 0; lists use 0..n."""

    __tablename__ = "product_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[str] = mapped_column(
        String(191), ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    asset_key: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, default=0)

    product: Mapped["Product"] = relationship(back_populates="assets")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(191), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(191))
    role: Mapped[str] = mapped_column(String(32), default="admin")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class AppMeta(Base):
    """Global key/value config (e.g. last_product_id)."""

    __tablename__ = "app_meta"

    key: Mapped[str] = mapped_column(String(191), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
