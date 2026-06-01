"""Compatibility wrapper for analytics providers."""
from __future__ import annotations

from typing import Dict

from kaymio.integrations.instagram.instagram_api_helper import fetch_instagram_analytics
from kaymio.integrations.pintrest.pinterest_helper import fetch_pinterest_analytics
from kaymio.integrations.youtube.youtube_api_helper import fetch_youtube_analytics

__all__ = [
    "fetch_pinterest_analytics",
    "fetch_instagram_analytics",
    "fetch_youtube_analytics",
]
