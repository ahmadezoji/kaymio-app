"""Local media storage shared by request handlers and background jobs.

Files live under ``kaymio/template_images`` and are served by the ``/media/``
route in ``kaymio.app``. Background jobs (schedulers, Telegram-approved
publishes) have no Flask request context, so they build public URLs from
``PUBLIC_APP_BASE_URL`` instead of ``url_for(_external=True)``.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from uuid import uuid4

import requests

logger = logging.getLogger(__name__)

STORAGE_ROOT = Path(__file__).resolve().parents[1] / "template_images"
ORIGINALS_DIR = STORAGE_ROOT / "originals"
GENERATED_DIR = STORAGE_ROOT / "generated"
VIDEOS_DIR = STORAGE_ROOT / "videos"

PUBLIC_APP_BASE_URL = os.getenv("PUBLIC_APP_BASE_URL", "").rstrip("/")
IMAGE_DOWNLOAD_TIMEOUT_SECONDS = 15


def store_media(bytes_data: bytes, directory: Path, suffix: str) -> str:
    """Write bytes under ``directory`` with a random name; return the storage-relative path."""
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{uuid4().hex}{suffix}"
    destination.write_bytes(bytes_data)
    return destination.relative_to(STORAGE_ROOT).as_posix()


def save_generated_image(bytes_data: bytes, suffix: str = ".png") -> str:
    return store_media(bytes_data, GENERATED_DIR, suffix)


def delete_media(relative_path: str) -> None:
    """Best-effort removal of a stored file; never raises."""
    if not relative_path:
        return
    target = (STORAGE_ROOT / relative_path).resolve()
    if not str(target).startswith(str(STORAGE_ROOT.resolve())):
        return
    try:
        target.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Failed to delete media file %s: %s", relative_path, exc)


def build_public_media_url(relative_path: str) -> str:
    """Externally reachable ``/media/`` URL, usable outside a Flask request."""
    if not PUBLIC_APP_BASE_URL:
        raise RuntimeError(
            "PUBLIC_APP_BASE_URL is not configured; cannot build a public media URL "
            "from a background job."
        )
    return f"{PUBLIC_APP_BASE_URL}/media/{relative_path}"


def download_image_bytes(image_url: str) -> bytes:
    resp = requests.get(image_url, timeout=IMAGE_DOWNLOAD_TIMEOUT_SECONDS)
    if resp.status_code != 200:
        raise ValueError(f"Image download failed ({resp.status_code}).")
    return resp.content
