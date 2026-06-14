"""OAuth credential management backed by database.

Handles storage and retrieval of API tokens for external platforms.
"""
from __future__ import annotations

import json
from datetime import datetime
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
