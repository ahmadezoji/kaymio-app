"""Settings page: connect external platforms via OAuth, stored in the DB.

Platforms are wired up through the generic kaymio.integrations.oauth_providers
registry. Adding a new platform means:
  1. Register a ProviderConfig in oauth_providers.PROVIDERS
  2. Append the platform key to SUPPORTED_PLATFORMS below

Instagram is special: its OAuth flow returns a short-lived token that must be
exchanged for a 60-day long-lived token before saving. This is handled
automatically in oauth_callback by checking provider.long_token_url.
"""
from __future__ import annotations

import datetime as dt
import os
import secrets
from typing import Optional

import requests
from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from kaymio.database.oauth import (
    clear_oauth_tokens,
    get_platform_client_config,
    load_oauth_credential,
    save_oauth_client_config,
    save_oauth_credential,
)
from kaymio.database.token_manager import get_token_status
from kaymio.integrations.oauth_providers import (
    build_authorize_url,
    exchange_code_for_tokens,
    exchange_for_long_lived_token,
    get_provider,
)

settings_bp = Blueprint("settings", __name__)

SUPPORTED_PLATFORMS = ["youtube", "instagram", "pinterest"]

# Per-platform labels shown in the credentials form
_CREDENTIAL_LABELS = {
    "youtube":    ("Client ID",  "Client Secret"),
    "instagram":  ("Instagram App ID", "Instagram App Secret"),
    "pinterest":  ("App ID",     "App Secret"),
}

# Where the user must register the redirect URI
_CONSOLE_NOTES = {
    "youtube": (
        "Google Cloud Console",
        "Google Cloud Console → APIs & Services → Credentials → your OAuth client → "
        "Authorized redirect URIs",
    ),
    "instagram": (
        "Meta for Developers",
        "Meta for Developers → your App → Use cases → Instagram API → API setup with "
        "Instagram login → Set up Instagram business login → Business login settings → "
        "OAuth redirect URIs",
    ),
    "pinterest": (
        "Pinterest Developers",
        "Pinterest Developers → My apps → your App → Authentication → "
        "Redirect URIs",
    ),
}

# Informational note shown below each card
_PLATFORM_NOTES = {
    "youtube": (
        "If this disconnects every few days, your Google Cloud OAuth client is likely in "
        '"Testing" publishing status — Google auto-revokes refresh tokens after 7 days for '
        "apps in that state. Move the consent screen to Production (or add your account as "
        "a permanent Test User) to stop that."
    ),
    "instagram": (
        "This connects with Instagram Login — sign in with the Instagram professional account "
        "itself; no Facebook Page is needed. Use the Instagram App ID and Instagram App Secret "
        "from Meta → Instagram API → API setup with Instagram login (not the Facebook app ID). "
        "The token is valid for 60 days and is refreshed automatically."
    ),
    "pinterest": (
        "Pinterest access tokens expire after 30 days. Click Connect again here when that "
        "happens — your App ID and App Secret are already saved so you only need to go "
        "through the Pinterest consent screen."
    ),
}


# --------------------------------------------------------------------------- #
# Password gate
# --------------------------------------------------------------------------- #
@settings_bp.before_request
def _require_settings_password():
    if request.endpoint == "settings.unlock":
        return None

    configured_password = os.getenv("SETTINGS_ACCESS_PASSWORD")
    if not configured_password:
        return (
            "Settings page is locked: set SETTINGS_ACCESS_PASSWORD in .env to enable access.",
            503,
        )

    if not session.get("settings_unlocked"):
        return render_template("settings.html", locked=True, platforms=[])
    return None


@settings_bp.route("/settings/unlock", methods=["POST"])
def unlock():
    configured_password = os.getenv("SETTINGS_ACCESS_PASSWORD")
    if configured_password and request.form.get("password") == configured_password:
        session["settings_unlocked"] = True
        return redirect(url_for("settings.settings_view"))
    flash("Incorrect password.", "error")
    return render_template("settings.html", locked=True, platforms=[])


# --------------------------------------------------------------------------- #
# View model
# --------------------------------------------------------------------------- #
def _platform_view_model(platform: str) -> dict:
    provider = get_provider(platform)
    status = get_token_status(platform)
    cred = load_oauth_credential(platform) or {}
    redirect_uri = url_for("settings.oauth_callback", platform=platform, _external=True)

    id_label, secret_label = _CREDENTIAL_LABELS.get(platform, ("Client ID", "Client Secret"))
    console_short, console_detail = _CONSOLE_NOTES.get(platform, ("your developer console", ""))
    platform_note = _PLATFORM_NOTES.get(platform, "")

    return {
        "platform": platform,
        "display_name": provider.display_name,
        "status": status,
        "client_id": cred.get("client_id") or "",
        "has_client_secret": bool(cred.get("client_secret")),
        "redirect_uri": redirect_uri,
        "id_label": id_label,
        "secret_label": secret_label,
        "console_short": console_short,
        "console_detail": console_detail,
        "platform_note": platform_note,
    }


# --------------------------------------------------------------------------- #
# Instagram Login helpers
# --------------------------------------------------------------------------- #
_INSTAGRAM_GRAPH = "https://graph.instagram.com/v21.0"
_INSTAGRAM_WEBHOOK_FIELDS = "comments,messages"


def _unwrap_instagram_token_response(token_response: dict) -> dict:
    """The code exchange returns either a flat object or ``{"data": [{...}]}``."""
    data = token_response.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return token_response


def _fetch_instagram_account(access_token: Optional[str]) -> dict:
    """Return ``{"user_id", "username"}`` of the connected professional account (empty on failure)."""
    if not access_token:
        return {}
    try:
        response = requests.get(
            f"{_INSTAGRAM_GRAPH}/me",
            params={"fields": "user_id,username", "access_token": access_token},
            timeout=15,
        )
        body = response.json() if response.status_code == 200 else {}
    except (requests.RequestException, ValueError):
        return {}
    return {
        "user_id": str(body.get("user_id") or body.get("id") or ""),
        "username": str(body.get("username") or ""),
    }


def _subscribe_instagram_webhooks(access_token: Optional[str]) -> Optional[str]:
    """Subscribe the account to comment/message webhooks. Returns an error message or None."""
    if not access_token:
        return "no access token"
    try:
        response = requests.post(
            f"{_INSTAGRAM_GRAPH}/me/subscribed_apps",
            params={"subscribed_fields": _INSTAGRAM_WEBHOOK_FIELDS, "access_token": access_token},
            timeout=15,
        )
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        return str(exc)
    if response.status_code >= 400 or not body.get("success"):
        return str((body.get("error") or {}).get("message") or body)
    return None


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@settings_bp.route("/settings", methods=["GET"])
def settings_view():
    platforms = [_platform_view_model(p) for p in SUPPORTED_PLATFORMS]
    return render_template("settings.html", locked=False, platforms=platforms)


@settings_bp.route("/settings/<platform>/credentials", methods=["POST"])
def save_credentials(platform: str):
    try:
        get_provider(platform)
    except ValueError:
        flash(f"Unknown platform: {platform}", "error")
        return redirect(url_for("settings.settings_view"))

    client_id = (request.form.get("client_id") or "").strip()
    submitted_secret = (request.form.get("client_secret") or "").strip()

    existing = load_oauth_credential(platform) or {}
    # Blank secret on submit means "keep existing" — the form never renders the
    # real secret, so a blank value unambiguously means unchanged.
    final_secret = submitted_secret or existing.get("client_secret") or ""

    save_oauth_client_config(platform, client_id, final_secret)
    flash(f"Saved {get_provider(platform).display_name} credentials.", "success")
    return redirect(url_for("settings.settings_view"))


@settings_bp.route("/settings/<platform>/connect", methods=["GET"])
def connect(platform: str):
    try:
        provider = get_provider(platform)
    except ValueError:
        flash(f"Unknown platform: {platform}", "error")
        return redirect(url_for("settings.settings_view"))

    client_config = get_platform_client_config(platform)
    if not client_config or not client_config.get("client_secret"):
        id_label = _CREDENTIAL_LABELS.get(platform, ("App ID",))[0]
        flash(f"Save a {id_label} and App Secret first.", "error")
        return redirect(url_for("settings.settings_view"))

    state = secrets.token_urlsafe(32)
    session[f"oauth_state_{platform}"] = state
    redirect_uri = url_for("settings.oauth_callback", platform=platform, _external=True)

    authorize_url = build_authorize_url(
        provider,
        client_id=client_config["client_id"],
        redirect_uri=redirect_uri,
        state=state,
    )
    return redirect(authorize_url)


@settings_bp.route("/settings/<platform>/callback", methods=["GET"])
def oauth_callback(platform: str):
    try:
        provider = get_provider(platform)
    except ValueError:
        flash(f"Unknown platform: {platform}", "error")
        return redirect(url_for("settings.settings_view"))

    error = request.args.get("error")
    if error:
        flash(f"Connection cancelled: {error}", "error")
        return redirect(url_for("settings.settings_view"))

    expected_state = session.pop(f"oauth_state_{platform}", None)
    state = request.args.get("state")
    if not expected_state or state != expected_state:
        flash("OAuth state mismatch — please try connecting again.", "error")
        return redirect(url_for("settings.settings_view"))

    code = request.args.get("code")
    if not code:
        flash("No authorization code returned by the provider.", "error")
        return redirect(url_for("settings.settings_view"))

    client_config = get_platform_client_config(platform)
    if not client_config or not client_config.get("client_secret"):
        flash("Client credentials are no longer configured for this platform.", "error")
        return redirect(url_for("settings.settings_view"))

    redirect_uri = url_for("settings.oauth_callback", platform=platform, _external=True)

    try:
        token_response = exchange_code_for_tokens(
            provider,
            client_id=client_config["client_id"],
            client_secret=client_config["client_secret"],
            code=code,
            redirect_uri=redirect_uri,
        )

        if provider.long_token_url:
            # Instagram Login: short-lived token -> 60-day long-lived token.
            short_lived = _unwrap_instagram_token_response(token_response)
            long_response = exchange_for_long_lived_token(
                provider,
                app_secret=client_config["client_secret"],
                short_token=str(short_lived.get("access_token") or ""),
                client_id=client_config.get("client_id"),
            )
            access_token = long_response.get("access_token")
            refresh_token = None
            expires_in = long_response.get("expires_in")
            permissions = short_lived.get("permissions")
            scope = ",".join(permissions) if isinstance(permissions, list) else permissions
            account = _fetch_instagram_account(access_token)
            # The API's {ig-user-id} is /me.user_id; the token response's user_id
            # is app-scoped and only a fallback.
            user_id = account.get("user_id") or str(short_lived.get("user_id") or "") or None
            raw_data = {"AUTH_FLOW": "instagram_login", "username": account.get("username", "")}

            error = _subscribe_instagram_webhooks(access_token)
            if error:
                flash(f"Connected, but subscribing to comment/message webhooks failed: {error}", "error")
        else:
            existing = load_oauth_credential(platform) or {}
            access_token = token_response.get("access_token")
            user_id = token_response.get("user_id") or None
            # Google only returns a refresh_token on first consent or when
            # prompt=consent is forced; preserve the existing one if omitted.
            refresh_token = token_response.get("refresh_token") or existing.get("refresh_token")
            expires_in = token_response.get("expires_in")
            scope = token_response.get("scope")
            raw_data = token_response

    except Exception as exc:
        flash(f"Token exchange failed: {exc}", "error")
        return redirect(url_for("settings.settings_view"))

    expires_at = (
        dt.datetime.utcnow() + dt.timedelta(seconds=int(expires_in))
        if expires_in
        else None
    )

    save_oauth_credential(
        platform,
        access_token=access_token,
        refresh_token=refresh_token,
        token_type=token_response.get("token_type", "bearer"),
        user_id=user_id,
        expires_at=expires_at,
        scope=scope,
        raw_data=raw_data,
    )
    flash(f"{provider.display_name} connected successfully.", "success")
    return redirect(url_for("settings.settings_view"))


@settings_bp.route("/settings/<platform>/disconnect", methods=["POST"])
def disconnect(platform: str):
    try:
        provider = get_provider(platform)
    except ValueError:
        flash(f"Unknown platform: {platform}", "error")
        return redirect(url_for("settings.settings_view"))

    clear_oauth_tokens(platform)
    flash(f"Disconnected {provider.display_name}. Saved credentials were kept.", "success")
    return redirect(url_for("settings.settings_view"))
