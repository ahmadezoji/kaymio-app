"""Generic OAuth2 authorization-code provider registry.

Each platform that uses a standard OAuth2 "redirect -> consent -> code ->
token exchange" flow gets a ProviderConfig entry here. The Settings blueprint
(kaymio/routes/settings_view.py) is written entirely against this
abstraction and contains no platform-specific logic.

Platform notes:
- YouTube: standard body-param token exchange, space-separated scopes
- Pinterest: Basic Auth token exchange (Authorization: Basic base64(id:secret)),
  comma-separated scopes
- Instagram: two-step flow — standard body-param exchange returns a short-lived
  token; a second GET to long_token_url upgrades it to a 60-day long-lived token.
  Comma-separated scopes.
"""
from __future__ import annotations

import base64
import urllib.parse
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import requests


@dataclass(frozen=True)
class ProviderConfig:
    platform: str          # matches OAuthCredential.platform, e.g. "youtube"
    display_name: str
    authorize_url: str
    token_url: str
    scopes: List[str]
    extra_authorize_params: Dict[str, str] = field(default_factory=dict)
    # "body": client_secret sent in POST body (Google/YouTube standard)
    # "basic": client_id:secret sent as Authorization: Basic header (Pinterest)
    token_auth_style: str = "body"
    # Scope list is joined with this separator before being URL-encoded
    scope_separator: str = " "
    # Instagram only: URL for the short-lived → long-lived token exchange (GET)
    long_token_url: Optional[str] = None


def build_authorize_url(config: ProviderConfig, *, client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": config.scope_separator.join(config.scopes),
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
    """Exchange an authorization code for tokens.

    Handles both body-param (Google) and Basic Auth (Pinterest) styles.
    """
    payload = {
        "code": code,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    if config.token_auth_style == "basic":
        raw = f"{client_id}:{client_secret}".encode()
        auth_header = base64.b64encode(raw).decode()
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {auth_header}",
        }
    else:
        payload["client_secret"] = client_secret
        headers = {}

    resp = requests.post(config.token_url, data=payload, headers=headers, timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError(f"Token exchange failed: {resp.status_code} - {resp.text}")
    return resp.json()


def exchange_for_long_lived_token(
    config: ProviderConfig,
    *,
    app_secret: str,
    short_token: str,
) -> dict:
    """Instagram: upgrade a short-lived access token to a 60-day long-lived token.

    Raises ValueError if the provider doesn't support this exchange.
    """
    if not config.long_token_url:
        raise ValueError(f"Provider '{config.platform}' does not support long-lived token exchange.")
    resp = requests.get(
        config.long_token_url,
        params={
            "grant_type": "ig_exchange_token",
            "client_secret": app_secret,
            "access_token": short_token,
        },
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Long-lived token exchange failed: {resp.status_code} - {resp.text}")
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
    "instagram": ProviderConfig(
        platform="instagram",
        display_name="Instagram",
        authorize_url="https://api.instagram.com/oauth/authorize",
        token_url="https://api.instagram.com/oauth/access_token",
        long_token_url="https://graph.instagram.com/access_token",
        scopes=[
            "instagram_business_basic",
            "instagram_business_content_publish",
            "instagram_business_manage_comments",
            "instagram_business_manage_insights",
        ],
        scope_separator=",",
    ),
    "pinterest": ProviderConfig(
        platform="pinterest",
        display_name="Pinterest",
        authorize_url="https://www.pinterest.com/oauth/",
        token_url="https://api.pinterest.com/v5/oauth/token",
        scopes=["boards:read", "boards:write", "pins:read", "pins:write", "user_accounts:read"],
        scope_separator=",",
        token_auth_style="basic",
    ),
}


def get_provider(platform: str) -> ProviderConfig:
    config = PROVIDERS.get(platform)
    if not config:
        raise ValueError(f"Unknown OAuth provider: {platform}")
    return config


def list_providers() -> List[ProviderConfig]:
    return list(PROVIDERS.values())
