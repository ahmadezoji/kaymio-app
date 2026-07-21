"""Inspect or manage Instagram webhook subscriptions for the current account."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

import requests

FACEBOOK_GRAPH_API_BASE = "https://graph.facebook.com/v21.0"
INSTAGRAM_GRAPH_API_BASE = "https://graph.instagram.com/v21.0"
DEFAULT_FIELDS = "messages,comments"


def _load_dotenv() -> None:
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


def _load_token_from_db() -> Dict[str, Any]:
    try:
        from kaymio.database.oauth import load_oauth_credential
        cred = load_oauth_credential("instagram")
        if cred:
            return {
                "INSTAGRAM_ACCESS_TOKEN": cred.get("access_token", ""),
                "INSTAGRAM_USER_ID": cred.get("user_id", ""),
                "AUTH_FLOW": "instagram_login",
            }
    except Exception:
        pass
    return {}


def _token_payload_looks_like_login_only(payload: Dict[str, Any]) -> bool:
    if not payload:
        return False
    required_publish_keys = (
        "INSTAGRAM_PAGE_ACCESS_TOKEN",
        "FACEBOOK_PAGE_ID",
        "FB_PAGE_ID",
        "FB_LONG_LIVED_USER_ACCESS_TOKEN",
    )
    return not any(payload.get(key) for key in required_publish_keys)


def _uses_instagram_login_flow(payload: Dict[str, Any]) -> bool:
    auth_flow = str(payload.get("AUTH_FLOW") or "").strip().lower()
    if auth_flow == "instagram_login":
        return True
    if auth_flow == "facebook_login":
        return False
    return _token_payload_looks_like_login_only(payload)


def _resolve_credentials() -> Dict[str, str]:
    payload = _load_token_from_db()
    use_instagram_login = _uses_instagram_login_flow(payload)
    user_id = str(os.getenv("INSTAGRAM_USER_ID") or payload.get("INSTAGRAM_USER_ID") or "").strip()

    access_token = ""
    if use_instagram_login:
        access_token = str(
            os.getenv("INSTAGRAM_ACCESS_TOKEN") or payload.get("INSTAGRAM_ACCESS_TOKEN") or ""
        ).strip()
    else:
        access_token = str(
            os.getenv("INSTAGRAM_PAGE_ACCESS_TOKEN")
            or payload.get("INSTAGRAM_PAGE_ACCESS_TOKEN")
            or payload.get("FB_LONG_LIVED_USER_ACCESS_TOKEN")
            or ""
        ).strip()

    if not user_id or not access_token:
        raise RuntimeError("Missing Instagram credentials in env or the oauth_credentials table.")

    return {
        "user_id": user_id,
        "access_token": access_token,
        "graph_api_base": INSTAGRAM_GRAPH_API_BASE if use_instagram_login else FACEBOOK_GRAPH_API_BASE,
        "flow": "instagram_login" if use_instagram_login else "facebook_login",
    }


def _request(method: str, path: str, *, params: Dict[str, str]) -> Dict[str, Any]:
    response = requests.request(method, path, params=params, timeout=30)
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw_text": response.text}
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {json.dumps(payload, indent=2)}")
    return payload if isinstance(payload, dict) else {"payload": payload}


def get_subscriptions() -> Dict[str, Any]:
    creds = _resolve_credentials()
    payload = _request(
        "GET",
        f"{creds['graph_api_base']}/{creds['user_id']}/subscribed_apps",
        params={"access_token": creds["access_token"]},
    )
    payload["_debug"] = {
        "flow": creds["flow"],
        "graph_api_base": creds["graph_api_base"],
        "user_id": creds["user_id"],
    }
    return payload


def subscribe(fields: str) -> Dict[str, Any]:
    creds = _resolve_credentials()
    payload = _request(
        "POST",
        f"{creds['graph_api_base']}/{creds['user_id']}/subscribed_apps",
        params={
            "subscribed_fields": fields,
            "access_token": creds["access_token"],
        },
    )
    payload["_debug"] = {
        "flow": creds["flow"],
        "graph_api_base": creds["graph_api_base"],
        "user_id": creds["user_id"],
        "subscribed_fields": fields,
    }
    return payload


def main() -> int:
    _load_dotenv()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--subscribe",
        action="store_true",
        help="Subscribe the current app to webhook fields for the current Instagram account.",
    )
    parser.add_argument(
        "--fields",
        default=DEFAULT_FIELDS,
        help=f"Comma-separated webhook fields to subscribe. Default: {DEFAULT_FIELDS}",
    )
    args = parser.parse_args()

    try:
        payload = subscribe(args.fields) if args.subscribe else get_subscriptions()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
