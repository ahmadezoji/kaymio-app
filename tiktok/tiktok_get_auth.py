"""TikTok OAuth/token helpers following the YouTube token-file pattern."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import secrets
import sys
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)
OAUTH_AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
OAUTH_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
MODULE_TOKEN_FILE = Path(__file__).resolve().parent / "tiktok_access_token.txt"
ROOT_TOKEN_FILE = Path.cwd() / "tiktok_access_token.txt"


def _clean_credential(value: object) -> str:
    cleaned = str(value or "").strip()
    lowered = cleaned.lower()
    if not cleaned:
        return ""
    if lowered.startswith("your_"):
        return ""
    if lowered in {
        "null",
        "none",
        "changeme",
        "your_tiktok_access_token",
        "your_tiktok_user_id",
        "your_client_key",
        "your_client_secret",
    }:
        return ""
    return cleaned


def _read_token_file(path: Path) -> Optional[Dict[str, str]]:
    try:
        raw = path.read_text().strip()
    except FileNotFoundError:
        return None
    except Exception:
        logger.exception("Unable to read TikTok token file: %s", path)
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"access_token": _clean_credential(raw)}
    if not isinstance(data, dict):
        return None
    return {
        "access_token": _clean_credential(data.get("access_token")),
        "refresh_token": _clean_credential(data.get("refresh_token")),
        "open_id": _clean_credential(data.get("open_id")),
    }


def write_access_token_to_file(access_token: str, refresh_token: str = "", open_id: str = "") -> None:
    payload = json.dumps(
        {
            "access_token": _clean_credential(access_token),
            "refresh_token": _clean_credential(refresh_token),
            "open_id": _clean_credential(open_id),
        }
    )
    MODULE_TOKEN_FILE.write_text(payload)
    ROOT_TOKEN_FILE.write_text(payload)


def load_tiktok_token_payload() -> Dict[str, str]:
    module_token = _read_token_file(MODULE_TOKEN_FILE) or {}
    root_token = _read_token_file(ROOT_TOKEN_FILE) or {}
    return {
        "access_token": _clean_credential(module_token.get("access_token") or root_token.get("access_token")),
        "refresh_token": _clean_credential(module_token.get("refresh_token") or root_token.get("refresh_token")),
        "open_id": _clean_credential(module_token.get("open_id") or root_token.get("open_id")),
    }


def _client_key() -> str:
    return _clean_credential(os.getenv("TIKTOK_CLIENT_KEY"))


def _client_secret() -> str:
    return _clean_credential(os.getenv("TIKTOK_CLIENT_SECRET"))


def _redirect_uri() -> str:
    return _clean_credential(os.getenv("TIKTOK_REDIRECT_URI"))


def generate_tiktok_pkce_pair() -> Dict[str, str]:
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode("utf-8")).digest()
    ).decode("utf-8").rstrip("=")
    return {"code_verifier": code_verifier, "code_challenge": code_challenge}


def build_tiktok_oauth_url(state: str, code_challenge: str) -> str:
    client_key = _client_key()
    redirect_uri = _redirect_uri()
    scope = _clean_credential(os.getenv("TIKTOK_OAUTH_SCOPE")) or "user.info.basic,video.publish"
    if not client_key or not redirect_uri or not code_challenge:
        raise RuntimeError("TIKTOK_CLIENT_KEY, TIKTOK_REDIRECT_URI, and code_challenge must be configured")
    query = urlencode(
        {
            "client_key": client_key,
            "response_type": "code",
            "scope": scope,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "disable_auto_auth": "1",
        }
    )
    return f"{OAUTH_AUTHORIZE_URL}?{query}"


def exchange_tiktok_oauth_code(code: str, code_verifier: str) -> Dict[str, str]:
    client_key = _client_key()
    client_secret = _client_secret()
    redirect_uri = _redirect_uri()
    if not client_key or not client_secret or not redirect_uri or not code_verifier:
        raise RuntimeError(
            "TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, TIKTOK_REDIRECT_URI, and code_verifier must be configured"
        )
    response = requests.post(
        OAUTH_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
        timeout=30,
    )
    if response.status_code >= 400:
        logger.error("TikTok token exchange failed: %s - %s", response.status_code, response.text)
        response.raise_for_status()
    payload = response.json() or {}
    access_token = _clean_credential(payload.get("access_token"))
    refresh_token = _clean_credential(payload.get("refresh_token"))
    open_id = _clean_credential(payload.get("open_id"))
    if not access_token or not open_id:
        raise RuntimeError(f"TikTok token response missing access_token/open_id: {payload}")
    write_access_token_to_file(access_token, refresh_token, open_id)
    return payload


def refresh_tiktok_access_token() -> str:
    client_key = _client_key()
    client_secret = _client_secret()
    token_payload = load_tiktok_token_payload()
    refresh_token = token_payload.get("refresh_token", "")
    if not all([client_key, client_secret, refresh_token]):
        raise RuntimeError("Missing TikTok client credentials or refresh token for token refresh")

    response = requests.post(
        OAUTH_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    if response.status_code >= 400:
        logger.error("TikTok token refresh failed: %s - %s", response.status_code, response.text)
        response.raise_for_status()

    payload = response.json() or {}
    access_token = _clean_credential(payload.get("access_token"))
    next_refresh_token = _clean_credential(payload.get("refresh_token")) or refresh_token
    open_id = _clean_credential(payload.get("open_id")) or token_payload.get("open_id", "")
    if not access_token:
        raise RuntimeError(f"TikTok refresh response missing access_token: {payload}")
    write_access_token_to_file(access_token, next_refresh_token, open_id)
    return access_token


def get_tiktok_credentials() -> Dict[str, str]:
    token_payload = load_tiktok_token_payload()
    token = token_payload.get("access_token") or _clean_credential(os.getenv("TIKTOK_ACCESS_TOKEN"))
    open_id = token_payload.get("open_id") or _clean_credential(os.getenv("TIKTOK_USER_ID"))
    if not token and token_payload.get("refresh_token"):
        token = refresh_tiktok_access_token()
        token_payload = load_tiktok_token_payload()
        open_id = token_payload.get("open_id") or open_id
    if not token or not open_id:
        raise RuntimeError("TikTok access token/open_id missing. Connect TikTok again.")
    return {"access_token": token, "open_id": open_id}


def has_tiktok_credentials() -> bool:
    try:
        creds = get_tiktok_credentials()
    except Exception:
        return False
    return bool(creds.get("access_token") and creds.get("open_id"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate TikTok OAuth tokens.")
    parser.add_argument("--client-key", required=True, help="TikTok app client key")
    parser.add_argument("--client-secret", required=True, help="TikTok app client secret")
    parser.add_argument("--redirect-uri", required=True, help="TikTok OAuth redirect URI")
    parser.add_argument("--scope", default="user.info.basic,video.publish", help="TikTok OAuth scope")
    args = parser.parse_args()

    pkce = generate_tiktok_pkce_pair()
    query = urlencode(
        {
            "client_key": args.client_key,
            "response_type": "code",
            "scope": args.scope,
            "redirect_uri": args.redirect_uri,
            "state": "manual-cli-auth",
            "code_challenge": pkce["code_challenge"],
            "code_challenge_method": "S256",
            "disable_auto_auth": "1",
        }
    )
    auth_url = f"{OAUTH_AUTHORIZE_URL}?{query}"

    print("\nVisit this URL in your browser to authorize TikTok publishing:\n")
    print(auth_url)
    print(
        "\nAfter granting access, TikTok will redirect to the redirect URI with a `code` parameter.\n"
        "Copy that `code` and paste it below.\n"
    )
    auth_code = input("Paste authorization code here: ").strip()
    if not auth_code:
        print("No code provided. Aborting.", file=sys.stderr)
        return 1

    os.environ["TIKTOK_CLIENT_KEY"] = args.client_key
    os.environ["TIKTOK_CLIENT_SECRET"] = args.client_secret
    os.environ["TIKTOK_REDIRECT_URI"] = args.redirect_uri
    os.environ["TIKTOK_OAUTH_SCOPE"] = args.scope

    try:
        token_payload = exchange_tiktok_oauth_code(auth_code, pkce["code_verifier"])
    except Exception as exc:
        print(exc, file=sys.stderr)
        return 1

    print("\nSuccess! Store these values in your environment if needed:\n")
    print(f"TIKTOK_ACCESS_TOKEN={token_payload.get('access_token', '')}")
    print(f"TIKTOK_USER_ID={token_payload.get('open_id', '')}")
    print(f"TIKTOK_REFRESH_TOKEN={token_payload.get('refresh_token', '')}")
    print("\nAccess token written to tiktok_access_token.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
