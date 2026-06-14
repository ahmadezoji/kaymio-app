"""OAuth credential management backed by database.

Handles storage and retrieval of API tokens for external platforms.
Auto-migrates from legacy JSON/TXT files on first run.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .db import session_scope
from .models import OAuthCredential


def load_oauth_credential(platform: str) -> Optional[Dict[str, Any]]:
    """Load OAuth credential for a specific platform."""
    with session_scope() as session:
        row = session.query(OAuthCredential).filter_by(platform=platform).first()

        if not row:
            return None

        return {
            "platform": row.platform,
            "access_token": row.access_token,
            "refresh_token": row.refresh_token,
            "token_type": row.token_type,
            "user_id": row.user_id,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "scope": row.scope,
            "raw_data": json.loads(row.raw_data) if row.raw_data else None,
        }

    return None


def save_oauth_credential(
    platform: str,
    access_token: str,
    refresh_token: Optional[str] = None,
    token_type: str = "bearer",
    user_id: Optional[str] = None,
    expires_at: Optional[datetime] = None,
    scope: Optional[str] = None,
    raw_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Save or update OAuth credential for a platform."""
    with session_scope() as session:
        row = session.query(OAuthCredential).filter_by(platform=platform).first()

        if row:
            row.access_token = access_token
            row.refresh_token = refresh_token
            row.token_type = token_type
            row.user_id = user_id
            row.expires_at = expires_at
            row.scope = scope
            row.raw_data = json.dumps(raw_data) if raw_data else None
        else:
            row = OAuthCredential(
                platform=platform,
                access_token=access_token,
                refresh_token=refresh_token,
                token_type=token_type,
                user_id=user_id,
                expires_at=expires_at,
                scope=scope,
                raw_data=json.dumps(raw_data) if raw_data else None,
            )
            session.add(row)

        session.commit()

    return load_oauth_credential(platform) or {}


def get_platform_access_token(platform: str) -> Optional[str]:
    """Convenience method to get just the access token for a platform."""
    cred = load_oauth_credential(platform)
    return cred.get("access_token") if cred else None


def get_platform_user_id(platform: str) -> Optional[str]:
    """Get the user ID for a platform."""
    cred = load_oauth_credential(platform)
    return cred.get("user_id") if cred else None


def delete_oauth_credential(platform: str) -> bool:
    """Delete OAuth credential for a platform."""
    with session_scope() as session:
        row = session.query(OAuthCredential).filter_by(platform=platform).first()
        if row:
            session.delete(row)
            session.commit()
            return True
    return False


# ==============================================================================
# Auto-migration from legacy JSON/TXT files
# ==============================================================================


def migrate_instagram_token_from_json(json_file: Path) -> None:
    """Auto-migrate Instagram token from instagram_token.json."""
    if not json_file.exists():
        return

    with session_scope() as session:
        existing = session.query(OAuthCredential).filter_by(platform="instagram").first()

    if existing:
        return

    try:
        data = json.loads(json_file.read_text())
        save_oauth_credential(
            platform="instagram",
            access_token=data.get("INSTAGRAM_ACCESS_TOKEN", ""),
            user_id=data.get("INSTAGRAM_USER_ID"),
            raw_data=data,
        )
    except Exception:
        pass


def migrate_youtube_token_from_file(token_file: Path) -> None:
    """Auto-migrate YouTube token from youtube_access_token.txt."""
    if not token_file.exists():
        return

    with session_scope() as session:
        existing = session.query(OAuthCredential).filter_by(platform="youtube").first()

    if existing:
        return

    try:
        token = token_file.read_text().strip()
        if token:
            save_oauth_credential(platform="youtube", access_token=token)
    except Exception:
        pass


def migrate_tiktok_token_from_file(token_file: Path) -> None:
    """Auto-migrate TikTok token from tiktok_access_token.txt."""
    if not token_file.exists():
        return

    with session_scope() as session:
        existing = session.query(OAuthCredential).filter_by(platform="tiktok").first()

    if existing:
        return

    try:
        token = token_file.read_text().strip()
        if token:
            save_oauth_credential(platform="tiktok", access_token=token)
    except Exception:
        pass


def migrate_pinterest_token_from_file(token_file: Path) -> None:
    """Auto-migrate Pinterest token from pintrest_access_token.txt."""
    if not token_file.exists():
        return

    with session_scope() as session:
        existing = session.query(OAuthCredential).filter_by(platform="pinterest").first()

    if existing:
        return

    try:
        token = token_file.read_text().strip()
        if token:
            save_oauth_credential(platform="pinterest", access_token=token)
    except Exception:
        pass


def migrate_all_oauth_from_files(state_dir: Path) -> None:
    """Auto-migrate all OAuth tokens from legacy files to database.

    Checks both data/ directory and kaymio/integrations/* directories.
    """
    # Instagram: check both data/ and kaymio/integrations/instagram/
    instagram_paths = [
        state_dir / "instagram_token.json",
        Path("kaymio/integrations/instagram/instagram_token.json"),
    ]
    for path in instagram_paths:
        if path.exists():
            migrate_instagram_token_from_json(path)
            break

    # YouTube: check both data/ and kaymio/integrations/youtube/
    youtube_paths = [
        state_dir / "youtube_access_token.txt",
        Path("kaymio/integrations/youtube/youtube_access_token.txt"),
    ]
    for path in youtube_paths:
        if path.exists():
            migrate_youtube_token_from_file(path)
            break

    # TikTok: check both data/ and kaymio/integrations/tiktok/
    tiktok_paths = [
        state_dir / "tiktok_access_token.txt",
        Path("kaymio/integrations/tiktok/tiktok_access_token.txt"),
    ]
    for path in tiktok_paths:
        if path.exists():
            migrate_tiktok_token_from_file(path)
            break

    # Pinterest: check both data/ and kaymio/integrations/pintrest/
    pinterest_paths = [
        state_dir / "pintrest_access_token.txt",
        Path("kaymio/integrations/pintrest/pintrest_access_token.txt"),
    ]
    for path in pinterest_paths:
        if path.exists():
            migrate_pinterest_token_from_file(path)
            break
