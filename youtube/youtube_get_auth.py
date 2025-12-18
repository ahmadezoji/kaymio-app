"""Helper script to mint YouTube OAuth credentials for server deployments.

Run this manually when you need to capture a fresh refresh token. It prints an
authorization URL, you sign in with the YouTube channel, grant access, and then
paste the returned authorization code back into the CLI. The script exchanges
that code for both an access token and a long-lived refresh token that you can
copy into your environment (.env or host secrets manager).
"""
from __future__ import annotations

import os
import sys
import urllib.parse

import requests

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DEFAULT_REDIRECT_URI = "http://localhost:8080/oauth2callback"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"{name} is required. Set it in your environment or .env file before running this script."
        )
    return value


def build_auth_url(client_id: str, redirect_uri: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code_for_tokens(
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
    resp = requests.post(TOKEN_URL, data=payload, timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError(f"Token exchange failed: {resp.status_code} - {resp.text}")
    return resp.json()


def main() -> int:
    try:
        client_id = _require_env("YOUTUBE_CLIENT_ID")
        client_secret = _require_env("YOUTUBE_CLIENT_SECRET")
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1

    redirect_uri = os.getenv("YOUTUBE_REDIRECT_URI", DEFAULT_REDIRECT_URI)
    auth_url = build_auth_url(client_id, redirect_uri)
    print("\nVisit this URL in your browser to authorize YouTube uploads:\n")
    print(auth_url)
    print(
        "\nAfter granting access, Google will redirect to the redirect URI with a `code` parameter.\n"
        "Copy that `code` and paste it below (the redirect URL can fail to load locally; you only need the code).\n"
    )
    auth_code = input("Paste authorization code here: ").strip()
    if not auth_code:
        print("No code provided. Aborting.", file=sys.stderr)
        return 1

    try:
        token_payload = exchange_code_for_tokens(
            client_id=client_id,
            client_secret=client_secret,
            code=auth_code,
            redirect_uri=redirect_uri,
        )
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1

    print("\nSuccess! Store these values in your environment (.env on the server):\n")
    print(f"YOUTUBE_ACCESS_TOKEN={token_payload.get('access_token', '')}")
    print(f"YOUTUBE_REFRESH_TOKEN={token_payload.get('refresh_token', '')}")
    print(f"Expires in: {token_payload.get('expires_in', 'unknown')} seconds")
    print("\nOnly the refresh token needs to persist long term; the service will refresh access tokens automatically.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
