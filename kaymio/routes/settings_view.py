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
    "instagram":  ("App ID",     "App Secret"),
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
        "Meta for Developers → your App → Facebook Login for Business → Settings → "
        "Valid OAuth Redirect URIs",
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
        "This connects via Facebook (required for Instagram Business accounts linked to a "
        "Facebook Page). Clicking Connect opens Facebook's consent screen — approve the "
        "permissions there and it will grant access to your linked Instagram Business account. "
        "The resulting token is valid for 60 days and is refreshed automatically."
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
            # Step 2: exchange the short-lived token for a long-lived one.
            # For Instagram Business (Facebook OAuth), the user_id in the
            # short-lived response is the Facebook user ID; we fetch it below.
            short_token = str(token_response.get("access_token") or "")
            user_id = str(token_response.get("user_id") or "") or None
            long_response = exchange_for_long_lived_token(
                provider,
                app_secret=client_config["client_secret"],
                short_token=short_token,
                client_id=client_config.get("client_id"),
            )
            access_token = long_response.get("access_token")
            refresh_token = None
            expires_in = long_response.get("expires_in")
            scope = long_response.get("scope") or token_response.get("scope")
            raw_data = {"short_lived": token_response, "long_lived": long_response}

            # For Facebook-based Instagram auth, we need the Instagram Business
            # Account ID (not the Facebook user ID) — that's what the Graph API
            # publishing endpoints use as the {user-id} path segment.
            # Fetch it by looking up the connected Facebook Page's IG account.
            if access_token:
                try:
                    import requests as _req
                    pages_resp = _req.get(
                        "https://graph.facebook.com/me/accounts",
                        params={
                            "access_token": access_token,
                            "fields": "id,access_token,instagram_business_account",
                        },
                        timeout=15,
                    )
                    if pages_resp.status_code == 200:
                        for page in pages_resp.json().get("data", []):
                            ig = page.get("instagram_business_account")
                            if isinstance(ig, dict) and ig.get("id"):
                                user_id = str(ig["id"])
                                break
                except Exception:
                    pass
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
