"""Generic OAuth2 authorization-code provider registry.

Each platform that uses a standard OAuth2 "redirect -> consent -> code ->
token exchange" flow gets a ProviderConfig entry here. The Settings blueprint
(kaymio/routes/settings_view.py) is written entirely against this
abstraction and contains no platform-specific logic.

Instagram's real-world flow needs a second step (short-lived token ->
long-lived token exchange) that doesn't fit this shape; that's intentionally
out of scope here and would need its own handling when added.
"""
from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import requests


@dataclass(frozen=True)
class ProviderConfig:
    platform: str  # matches OAuthCredential.platform, e.g. "youtube"
    display_name: str
    authorize_url: str
    token_url: str
    scopes: List[str]
    extra_authorize_params: Dict[str, str] = field(default_factory=dict)


def build_authorize_url(config: ProviderConfig, *, client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(config.scopes),
        "state": state,
        **config.extra_authorize_params,
    }
    return f"{config.authorize_url}?{urllib.parse.urlencode(params)}"


def exchange_code_for_tokens(
    config: ProviderConfig,
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> dict:
    payload = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    resp = requests.post(config.token_url, data=payload, timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError(f"Token exchange failed: {resp.status_code} - {resp.text}")
    return resp.json()


PROVIDERS: Dict[str, ProviderConfig] = {
    "youtube": ProviderConfig(
        platform="youtube",
        display_name="YouTube",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scopes=[
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/yt-analytics.readonly",
        ],
        extra_authorize_params={"access_type": "offline", "prompt": "consent"},
    ),
    # Future: "instagram", "pinterest", "tiktok" — add a ProviderConfig entry
    # here once their web-based connect flow is implemented.
}


def get_provider(platform: str) -> ProviderConfig:
    config = PROVIDERS.get(platform)
    if not config:
        raise ValueError(f"Unknown OAuth provider: {platform}")
    return config


def list_providers() -> List[ProviderConfig]:
    return list(PROVIDERS.values())
