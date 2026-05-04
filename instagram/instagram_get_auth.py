"""Mint Instagram Graph credentials for publishing and DM automation.

This repository's Instagram helpers expect a long-lived user token for content
publishing plus the connected Page access token for reply automation. The
script uses Facebook Login, resolves the connected Instagram professional
account, and writes all required values to instagram_token.json.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
from pathlib import Path
from typing import Optional

import requests

GRAPH_VERSION = "v21.0"
AUTH_URL = f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth"
TOKEN_URL = f"https://graph.facebook.com/{GRAPH_VERSION}/oauth/access_token"
SCRIPT_VERSION = "facebook-login-publish-and-messaging-2026-05-04"
SCOPES = [
    "instagram_basic",
    "instagram_content_publish",
    "instagram_manage_comments",
    "instagram_manage_messages",
    "instagram_manage_insights",
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_metadata",
]


def _read_env(name: str) -> Optional[str]:
    value = os.getenv(name)
    if not value:
        return None
    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ("'", '"'):
        cleaned = cleaned[1:-1]
    return cleaned or None


def _require_env(name: str) -> str:
    value = _read_env(name)
    if value:
        return value
    raise RuntimeError(
        f"{name} is required. Set it in your environment or .env file before running this script."
    )


def _resolve_app_credentials() -> tuple[str, str, str]:
    fb_app_id = _read_env("FB_APP_ID")
    fb_app_secret = _read_env("FB_APP_SECRET")
    if fb_app_id and fb_app_secret:
        return fb_app_id, fb_app_secret, "FB_APP_ID/FB_APP_SECRET"

    instagram_app_id = _read_env("INSTAGRAM_APP_ID")
    instagram_app_secret = _read_env("INSTAGRAM_APP_SECRET")
    if instagram_app_id and instagram_app_secret:
        return instagram_app_id, instagram_app_secret, "INSTAGRAM_APP_ID/INSTAGRAM_APP_SECRET"

    raise RuntimeError(
        "App credentials are required. Configure FB_APP_ID/FB_APP_SECRET "
        "(preferred) or INSTAGRAM_APP_ID/INSTAGRAM_APP_SECRET."
    )


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
            os.environ.setdefault(key.strip(), value.strip())
        break


def _raise_for_status_with_body(response: requests.Response) -> None:
    if response.status_code >= 400:
        request = response.request
        print(
            f"HTTP {response.status_code} for {request.method} {request.url}",
            file=sys.stderr,
        )
        print(response.text, file=sys.stderr)
    response.raise_for_status()


def _normalize_auth_code(raw_value: str) -> str:
    cleaned = raw_value.strip()
    if cleaned.startswith("http"):
        parsed = urllib.parse.urlparse(cleaned)
        cleaned = urllib.parse.parse_qs(parsed.query).get("code", [""])[0]
    cleaned = cleaned.split("#", 1)[0]
    return urllib.parse.unquote(cleaned).strip()


def _request_json(url: str, *, params: Optional[dict] = None) -> dict:
    response = requests.get(url, params=params, timeout=30)
    _raise_for_status_with_body(response)
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected response payload from {url}")
    return payload


def _write_token_file(
    *,
    access_token: str,
    user_id: str,
    page_access_token: str,
    page_id: str,
    expires_in: object,
) -> Path:
    token_path = Path(__file__).with_name("instagram_token.json")
    payload = {
        "AUTH_FLOW": "facebook_login_publish_and_messaging",
        "INSTAGRAM_ACCESS_TOKEN": access_token,
        "INSTAGRAM_USER_ID": user_id,
        "INSTAGRAM_PAGE_ACCESS_TOKEN": page_access_token,
        "FACEBOOK_PAGE_ID": page_id,
        "FB_LONG_LIVED_USER_ACCESS_TOKEN": access_token,
        "FB_PAGE_ID": page_id,
        "EXPIRES_IN": expires_in,
    }
    token_path.write_text(json.dumps(payload, indent=2) + "\n")
    return token_path


def _extract_page_id(page: dict) -> str:
    page_id = str(page.get("id") or "").strip()
    if not page_id:
        raise RuntimeError("Selected page did not include an ID.")
    return page_id


def _extract_page_access_token(page: dict, user_access_token: str) -> str:
    page_access_token = str(page.get("access_token") or "").strip()
    if page_access_token:
        return page_access_token

    page_id = _extract_page_id(page)
    payload = _request_json(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{page_id}",
        params={"access_token": user_access_token, "fields": "access_token"},
    )
    page_access_token = str(payload.get("access_token") or "").strip()
    if not page_access_token:
        raise RuntimeError(
            "Selected Facebook Page did not return an access_token. Confirm the app has "
            "the required permissions and the user has page access."
        )
    return page_access_token


def _extract_instagram_user_id(page: dict, user_access_token: str) -> str:
    instagram_business_account = page.get("instagram_business_account") or {}
    instagram_user_id = str(instagram_business_account.get("id") or "").strip()
    if instagram_user_id:
        return instagram_user_id

    page_id = _extract_page_id(page)
    payload = _request_json(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{page_id}",
        params={"access_token": user_access_token, "fields": "instagram_business_account"},
    )
    instagram_business_account = payload.get("instagram_business_account") or {}
    instagram_user_id = str(instagram_business_account.get("id") or "").strip()
    if not instagram_user_id:
        raise RuntimeError(
            "No Instagram professional account found for the selected Page. Confirm the Page "
            "is connected to the Instagram account and the account is Business or Creator."
        )
    return instagram_user_id


def _print_runtime_summary(app_id: str, redirect_uri: str, credential_source: str) -> None:
    print(f"Script: {Path(__file__).resolve()}")
    print(f"Version: {SCRIPT_VERSION}")
    print(f"App credentials: {credential_source}")
    print(f"APP_ID={app_id}")
    print(f"INSTAGRAM_REDIRECT_URI={redirect_uri}")


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
    return _request_json(
        TOKEN_URL,
        params={
            "client_id": app_id,
            "client_secret": app_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        },
    )


def exchange_for_long_lived_token(app_id: str, app_secret: str, short_token: str) -> dict:
    return _request_json(
        TOKEN_URL,
        params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": short_token,
        },
    )


def fetch_pages(access_token: str) -> list[dict]:
    payload = _request_json(
        f"https://graph.facebook.com/{GRAPH_VERSION}/me/accounts",
        params={
            "access_token": access_token,
            "limit": 200,
            "fields": "id,name,access_token,tasks,instagram_business_account",
        },
    )
    pages = payload.get("data", [])
    if not isinstance(pages, list):
        return []
    return [page for page in pages if isinstance(page, dict)]


def _choose_page(pages: list[dict]) -> dict:
    if not pages:
        raise RuntimeError(
            "No Facebook Pages returned. Ensure the user has a Page connected to the Instagram professional account."
        )
    if len(pages) == 1:
        return pages[0]

    print("\nSelect the Facebook Page connected to your Instagram professional account:\n")
    for idx, page in enumerate(pages, start=1):
        name = page.get("name", "Unknown")
        page_id = page.get("id", "Unknown")
        has_instagram = bool((page.get("instagram_business_account") or {}).get("id"))
        tasks = ", ".join(str(task) for task in (page.get("tasks") or []))
        print(
            f"{idx}. {name} ({page_id}) "
            f"[instagram_connected={'yes' if has_instagram else 'no'} tasks={tasks or 'unknown'}]"
        )

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
        app_id, app_secret, credential_source = _resolve_app_credentials()
        redirect_uri = _require_env("INSTAGRAM_REDIRECT_URI")
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1

    _print_runtime_summary(app_id, redirect_uri, credential_source)
    auth_url = build_auth_url(app_id, redirect_uri)
    print("\nVisit this URL to authorize Instagram publishing and messaging:\n")
    print(auth_url)
    print(
        "\nAfter granting access, Facebook redirects to your redirect URI with a `code` parameter.\n"
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
        short_token = str(short_payload.get("access_token") or "").strip()
        if not short_token:
            raise RuntimeError("Short-lived token exchange did not return an access token.")

        long_payload = exchange_for_long_lived_token(app_id, app_secret, short_token)
        long_token = str(long_payload.get("access_token") or "").strip()
        if not long_token:
            raise RuntimeError("Long-lived token exchange did not return an access token.")

        pages = fetch_pages(long_token)
        page = _choose_page(pages)
        page_id = _extract_page_id(page)
        instagram_user_id = _extract_instagram_user_id(page, long_token)
        page_access_token = _extract_page_access_token(page, long_token)
    except (requests.RequestException, RuntimeError) as exc:
        print(exc, file=sys.stderr)
        return 1

    token_path = _write_token_file(
        access_token=long_token,
        user_id=instagram_user_id,
        page_access_token=page_access_token,
        page_id=page_id,
        expires_in=long_payload.get("expires_in", "unknown"),
    )

    print("\nSuccess! Store these values in your environment:\n")
    print(f"INSTAGRAM_ACCESS_TOKEN={long_token}")
    print(f"INSTAGRAM_USER_ID={instagram_user_id}")
    print(f"INSTAGRAM_PAGE_ACCESS_TOKEN={page_access_token}")
    print(f"FACEBOOK_PAGE_ID={page_id}")
    print(f"FB_LONG_LIVED_USER_ACCESS_TOKEN={long_token}")
    print(f"FB_PAGE_ID={page_id}")
    print(f"Expires in: {long_payload.get('expires_in', 'unknown')} seconds")
    print(f"\nSaved JSON credentials to: {token_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
