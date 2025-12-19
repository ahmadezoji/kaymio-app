"""Helper script to mint Instagram Graph API credentials for server deployments.

Run this manually when you need a fresh long-lived access token and Instagram
Business Account ID. The flow:
1) Open the auth URL, log in, and grant access.
2) Paste the returned authorization code.
3) The script exchanges it for a long-lived user access token.
4) It then looks up the connected Instagram Business Account ID.

Store the outputs in .env or your server secrets manager.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
from pathlib import Path

import requests

GRAPH_VERSION = "v21.0"
AUTH_URL = f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth"
TOKEN_URL = f"https://graph.facebook.com/{GRAPH_VERSION}/oauth/access_token"
DEFAULT_REDIRECT_URI = "https://kaymio.mardomvpn.store/instagram/callback"
SCOPES = [
    "instagram_basic",
    "instagram_content_publish",
    "pages_show_list",
    "pages_read_engagement",
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


def _write_token_file(
    *,
    access_token: str,
    user_id: str,
    page_id: str,
    expires_in: object,
) -> Path:
    token_path = Path(__file__).with_name("instagram_token.json")
    payload = {
        "INSTAGRAM_ACCESS_TOKEN": access_token,
        "INSTAGRAM_USER_ID": user_id,
        "FB_LONG_LIVED_USER_ACCESS_TOKEN": access_token,
        "FB_PAGE_ID": page_id,
        "EXPIRES_IN": expires_in,
    }
    token_path.write_text(json.dumps(payload, indent=2))
    return token_path


def _request_json(url: str, *, params: dict) -> dict:
    resp = requests.get(url, params=params, timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError(f"Request failed: {resp.status_code} - {resp.text}")
    return resp.json()


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
    params = {
        "client_id": app_id,
        "client_secret": app_secret,
        "redirect_uri": redirect_uri,
        "code": code,
    }
    return _request_json(TOKEN_URL, params=params)


def exchange_for_long_lived_token(app_id: str, app_secret: str, short_token: str) -> dict:
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": short_token,
    }
    return _request_json(TOKEN_URL, params=params)


def fetch_pages(access_token: str) -> list[dict]:
    url = f"https://graph.facebook.com/{GRAPH_VERSION}/me/accounts"
    data = _request_json(url, params={"access_token": access_token, "limit": 200})
    return data.get("data", [])


def fetch_instagram_business_account(page_id: str, access_token: str) -> dict:
    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{page_id}"
    data = _request_json(
        url,
        params={"access_token": access_token, "fields": "instagram_business_account"},
    )
    return data


def _choose_page(pages: list[dict]) -> dict:
    if not pages:
        raise RuntimeError(
            "No Facebook Pages returned. Ensure the user has a Page connected to the Instagram Business account."
        )
    if len(pages) == 1:
        return pages[0]

    print("\nSelect the Facebook Page connected to your Instagram Business account:\n")
    for idx, page in enumerate(pages, start=1):
        name = page.get("name", "Unknown")
        page_id = page.get("id", "Unknown")
        print(f"{idx}. {name} ({page_id})")

    while True:
        choice = input("\nEnter page number: ").strip()
        if not choice.isdigit():
            print("Please enter a number.")
            continue
        index = int(choice)
        if 1 <= index <= len(pages):
            return pages[index - 1]
        print("Invalid selection.")


def main() -> int:
    _load_dotenv()
    try:
        app_id = _require_env("FB_APP_ID")
        app_secret = _require_env("FB_APP_SECRET")
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1

    redirect_uri = os.getenv("INSTAGRAM_REDIRECT_URI", DEFAULT_REDIRECT_URI)
    auth_url = build_auth_url(app_id, redirect_uri)
    print("\nVisit this URL to authorize Instagram publishing:\n")
    print(auth_url)
    print(
        "\nAfter granting access, Facebook will redirect to the redirect URI with a `code` parameter.\n"
        "Copy that `code` and paste it below (the redirect URL can fail to load locally; you only need the code).\n"
    )
    auth_code = input("Paste authorization code here: ").strip()
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
        if not short_token:
            raise RuntimeError("Short-lived token exchange did not return an access token.")

        long_payload = exchange_for_long_lived_token(app_id, app_secret, short_token)
        long_token = long_payload.get("access_token")
        if not long_token:
            raise RuntimeError("Long-lived token exchange did not return an access token.")

        pages = fetch_pages(long_token)
        page = _choose_page(pages)
        page_id = page.get("id")
        if not page_id:
            raise RuntimeError("Selected page did not include an ID.")

        ig_data = fetch_instagram_business_account(page_id, long_token)
        ig_account = ig_data.get("instagram_business_account") or {}
        ig_user_id = ig_account.get("id")
        if not ig_user_id:
            raise RuntimeError(
                "No Instagram Business Account found for the selected Page. "
                "Confirm the Page is connected to the Instagram account and the account is Business or Creator."
            )

    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1

    token_path = _write_token_file(
        access_token=long_token,
        user_id=ig_user_id,
        page_id=page_id,
        expires_in=long_payload.get("expires_in", "unknown"),
    )

    print("\nSuccess! Store these values in your environment (.env on the server):\n")
    print(f"INSTAGRAM_ACCESS_TOKEN={long_token}")
    print(f"INSTAGRAM_USER_ID={ig_user_id}")
    print(f"FB_LONG_LIVED_USER_ACCESS_TOKEN={long_token}")
    print(f"FB_PAGE_ID={page_id}")
    print(f"Expires in: {long_payload.get('expires_in', 'unknown')} seconds")
    print(f"\nSaved JSON credentials to: {token_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
