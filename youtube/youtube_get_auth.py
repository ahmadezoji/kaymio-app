"""Helper script to mint YouTube OAuth credentials for server deployments.

Run this manually when you need to capture a fresh refresh token. It prints an
authorization URL, you sign in with the YouTube channel, grant access, and then
paste the returned authorization code back into the CLI. The script exchanges
that code for both an access token and a long-lived refresh token that you can
copy into your environment (.env or host secrets manager).
"""
from __future__ import annotations

import argparse
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


def write_access_token_to_file(access_token: str):
    """Write the access token to a file."""
    with open("youtube_access_token.txt", "w") as token_file:
        token_file.write(access_token)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate YouTube OAuth tokens.")
    parser.add_argument("--client-id", required=True, help="YouTube API client ID")
    parser.add_argument("--client-secret", required=True, help="YouTube API client secret")
    parser.add_argument("--redirect-uri", default=DEFAULT_REDIRECT_URI, help="Redirect URI (optional)")
    args = parser.parse_args()

    client_id = args.client_id
    client_secret = args.client_secret
    redirect_uri = args.redirect_uri

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

    access_token = token_payload.get("access_token", "")
    refresh_token = token_payload.get("refresh_token", "")
    print("\nSuccess! Store these values in your environment (.env on the server):\n")
    print(f"YOUTUBE_ACCESS_TOKEN={access_token}")
    print(f"YOUTUBE_REFRESH_TOKEN={refresh_token}")
    print(f"Expires in: {token_payload.get('expires_in', 'unknown')} seconds")
    print("\nOnly the refresh token needs to persist long term; the service will refresh access tokens automatically.")

    # Write the access token to a file
    write_access_token_to_file(access_token)
    print("\nAccess token written to youtube_access_token.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
