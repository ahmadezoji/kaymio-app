"""Helper script to mint Instagram Login credentials for server deployments."""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
from pathlib import Path

import requests

AUTH_URL = "https://api.instagram.com/oauth/authorize"
TOKEN_URL = "https://api.instagram.com/oauth/access_token"
LONG_TOKEN_URL = "https://graph.instagram.com/access_token"
SCOPES = [
    "instagram_business_basic",
    "instagram_business_content_publish",
    "instagram_business_manage_comments",
    "instagram_business_manage_messages",
    "instagram_business_manage_insights",
]


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"{name} is required. Set it in your environment or .env file before running this script."
        )
    return value


def _load_dotenv() -> None:
    """Load environment variables from .env if present."""
    try:
        from dotenv import load_dotenv  # type: ignore

        if load_dotenv():
            return
    except Exception:
        pass

    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parents[1] / ".env"):
        if not candidate.exists():
            continue
        for line in candidate.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            os.environ.setdefault(key, value)
        break


def _raise_for_status_with_body(response: requests.Response) -> None:
    if response.status_code >= 400:
        print(response.text, file=sys.stderr)
    response.raise_for_status()


def _normalize_auth_code(raw_value: str) -> str:
    cleaned = raw_value.strip()
    if not cleaned:
        return ""

    parsed = urllib.parse.urlparse(cleaned)
    query_code = urllib.parse.parse_qs(parsed.query).get("code")
    if query_code:
        cleaned = query_code[0]
    elif "code=" in cleaned:
        cleaned = cleaned.split("code=", 1)[1].split("&", 1)[0]

    return urllib.parse.unquote(cleaned).replace("#_", "").strip()


def _write_token_file(*, access_token: str, user_id: str, expires_in: object) -> Path:
    token_path = Path(__file__).with_name("instagram_token.json")
    payload = {
        "INSTAGRAM_ACCESS_TOKEN": access_token,
        "INSTAGRAM_USER_ID": user_id,
        "EXPIRES_IN": expires_in,
    }
    token_path.write_text(json.dumps(payload, indent=2) + "\n")
    return token_path


def build_auth_url(app_id: str, redirect_uri: str) -> str:
    params = {
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": ",".join(SCOPES),
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code_for_short_lived_token(
    app_id: str,
    app_secret: str,
    code: str,
    redirect_uri: str,
) -> dict:
    response = requests.post(
        TOKEN_URL,
        data={
            "client_id": app_id,
            "client_secret": app_secret,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code": code,
        },
        timeout=30,
    )
    _raise_for_status_with_body(response)
    return response.json()


def exchange_for_long_lived_token(app_secret: str, short_token: str) -> dict:
    response = requests.get(
        LONG_TOKEN_URL,
        params={
            "grant_type": "ig_exchange_token",
            "client_secret": app_secret,
            "access_token": short_token,
        },
        timeout=30,
    )
    _raise_for_status_with_body(response)
    return response.json()


def main() -> int:
    _load_dotenv()

    try:
        app_id = _require_env("INSTAGRAM_APP_ID")
        app_secret = _require_env("INSTAGRAM_APP_SECRET")
        redirect_uri = _require_env("INSTAGRAM_REDIRECT_URI")
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1

    auth_url = build_auth_url(app_id, redirect_uri)
    print("\nVisit this URL to authorize Instagram Login:\n")
    print(auth_url)
    print(
        "\nAfter granting access, Instagram redirects to your redirect URI with a `code` parameter.\n"
        "Paste either the full redirect URL or just the code below.\n"
    )

    auth_code = _normalize_auth_code(input("Paste authorization code here: "))
    if not auth_code:
        print("No code provided. Aborting.", file=sys.stderr)
        return 1

    try:
        short_payload = exchange_code_for_short_lived_token(
            app_id=app_id,
            app_secret=app_secret,
            code=auth_code,
            redirect_uri=redirect_uri,
        )
        short_token = short_payload.get("access_token")
        user_id = short_payload.get("user_id")
        if not short_token:
            raise RuntimeError("Short-lived token exchange did not return an access token.")
        if not user_id:
            raise RuntimeError("Short-lived token exchange did not return a user_id.")

        long_payload = exchange_for_long_lived_token(app_secret, short_token)
        long_token = long_payload.get("access_token")
        if not long_token:
            raise RuntimeError("Long-lived token exchange did not return an access token.")
    except (requests.RequestException, RuntimeError) as exc:
        print(exc, file=sys.stderr)
        return 1

    token_path = _write_token_file(
        access_token=long_token,
        user_id=str(user_id),
        expires_in=long_payload.get("expires_in", "unknown"),
    )

    print("\nSuccess! Store these values in your environment:\n")
    print(f"INSTAGRAM_ACCESS_TOKEN={long_token}")
    print(f"INSTAGRAM_USER_ID={user_id}")
    print(f"Expires in: {long_payload.get('expires_in', 'unknown')} seconds")
    print(f"\nSaved JSON credentials to: {token_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
