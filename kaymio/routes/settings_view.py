"""Settings page: connect external platforms via OAuth, stored in the DB.

Currently wires up YouTube end-to-end; the route shapes are written against
the generic kaymio.integrations.oauth_providers registry so adding another
platform later means registering a ProviderConfig and appending to
SUPPORTED_PLATFORMS, not writing new routes.
"""
from __future__ import annotations

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
from kaymio.integrations.oauth_providers import build_authorize_url, exchange_code_for_tokens, get_provider

import datetime as dt

settings_bp = Blueprint("settings", __name__)

SUPPORTED_PLATFORMS = ["youtube"]


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


def _platform_view_model(platform: str) -> dict:
    provider = get_provider(platform)
    status = get_token_status(platform)
    cred = load_oauth_credential(platform) or {}
    redirect_uri = url_for("settings.oauth_callback", platform=platform, _external=True)
    return {
        "platform": platform,
        "display_name": provider.display_name,
        "status": status,
        "client_id": cred.get("client_id") or "",
        "has_client_secret": bool(cred.get("client_secret")),
        "redirect_uri": redirect_uri,
    }


@settings_bp.route("/settings", methods=["GET"])
def settings_view():
    platforms = [_platform_view_model(platform) for platform in SUPPORTED_PLATFORMS]
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
    # Blank secret on submit means "keep existing" — the form never renders
    # the real secret back, so a blank value unambiguously means unchanged.
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
        flash("Save a client ID and client secret first.", "error")
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
        flash("Client ID/secret are no longer configured for this platform.", "error")
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
    except Exception as exc:
        flash(f"Token exchange failed: {exc}", "error")
        return redirect(url_for("settings.settings_view"))

    existing = load_oauth_credential(platform) or {}
    access_token = token_response.get("access_token")
    # Google only returns a refresh_token on first consent (or when consent
    # is forced); preserve the existing one if this response omits it.
    refresh_token = token_response.get("refresh_token") or existing.get("refresh_token")
    expires_in = token_response.get("expires_in")
    expires_at = dt.datetime.utcnow() + dt.timedelta(seconds=expires_in) if expires_in else None

    save_oauth_credential(
        platform,
        access_token=access_token,
        refresh_token=refresh_token,
        token_type=token_response.get("token_type", "bearer"),
        expires_at=expires_at,
        scope=token_response.get("scope"),
        raw_data=token_response,
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
