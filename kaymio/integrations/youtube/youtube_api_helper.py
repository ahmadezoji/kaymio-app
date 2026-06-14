"""YouTube Data API helper for uploading Shorts."""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
from pathlib import Path
from typing import Dict, Iterable, Optional

import requests

logger = logging.getLogger(__name__)
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def _read_token_file(path: Path) -> Optional[Dict[str, str]]:
    try:
        raw = path.read_text().strip()
        if not raw:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        return {
            "access_token": str(data.get("access_token") or ""),
            "refresh_token": str(data.get("refresh_token") or ""),
        }
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None


def _persist_access_token_to_db(token: str, refresh_token: str) -> None:
    """Save YouTube token to database (new approach)."""
    try:
        from kaymio.database.oauth import save_oauth_credential
        save_oauth_credential(
            platform="youtube",
            access_token=token,
            refresh_token=refresh_token,
            token_type="bearer",
        )
    except Exception as e:
        logger.debug("Failed to persist YouTube token to DB: %s", e)


def _persist_access_token(token: str, refresh_token: str) -> None:
    """Save YouTube token to database and legacy files (for backward compat)."""
    _persist_access_token_to_db(token, refresh_token)
    # Also save to files for backward compatibility
    module_path = Path(__file__).resolve().parent / "youtube_access_token.txt"
    root_path = Path.cwd() / "youtube_access_token.txt"
    payload = json.dumps({"access_token": token, "refresh_token": refresh_token})
    try:
        module_path.write_text(payload)
    except Exception:
        pass
    try:
        root_path.write_text(payload)
    except Exception:
        pass


def _get_youtube_token_from_db() -> Optional[str]:
    """Get YouTube access token from database."""
    try:
        from kaymio.database.oauth import load_oauth_credential
        cred = load_oauth_credential("youtube")
        if cred and cred.get("access_token"):
            token = cred["access_token"]
            # Handle case where token is stored as JSON string (legacy format)
            if isinstance(token, str) and token.startswith("{"):
                try:
                    token_obj = json.loads(token)
                    return token_obj.get("access_token", token)
                except json.JSONDecodeError:
                    return token
            return token
    except Exception as e:
        logger.debug("Failed to load YouTube token from DB: %s", e)
    return None


def _get_youtube_token() -> str:
    # Try database first
    db_token = _get_youtube_token_from_db()
    if db_token:
        return db_token

    # Fall back to files for backward compatibility
    module_token = _read_token_file(Path(__file__).resolve().parent / "youtube_access_token.txt")
    if module_token and module_token.get("access_token"):
        return module_token["access_token"]
    root_token = _read_token_file(Path.cwd() / "youtube_access_token.txt")
    if root_token and root_token.get("access_token"):
        return root_token["access_token"]
    return refresh_youtube_access_token()


def _get_refresh_token_from_db() -> Optional[str]:
    """Get YouTube refresh token from database or extract from access_token JSON."""
    try:
        from kaymio.database.oauth import load_oauth_credential
        cred = load_oauth_credential("youtube")

        # Try refresh_token column first
        if cred and cred.get("refresh_token"):
            return cred["refresh_token"]

        # Fall back to extracting from access_token if it's a JSON string (legacy format)
        if cred and cred.get("access_token"):
            token = cred["access_token"]
            if isinstance(token, str) and token.startswith("{"):
                try:
                    token_obj = json.loads(token)
                    refresh = token_obj.get("refresh_token")
                    if refresh:
                        return refresh
                except json.JSONDecodeError:
                    pass
    except Exception as e:
        logger.debug("Failed to load YouTube refresh token from DB: %s", e)
    return None


def _get_refresh_token() -> Optional[str]:
    # Try database first
    db_token = _get_refresh_token_from_db()
    if db_token:
        return db_token

    # Fall back to files for backward compatibility
    module_token = _read_token_file(Path(__file__).resolve().parent / "youtube_access_token.txt")
    if module_token and module_token.get("refresh_token"):
        return module_token["refresh_token"]
    root_token = _read_token_file(Path.cwd() / "youtube_access_token.txt")
    if root_token and root_token.get("refresh_token"):
        return root_token["refresh_token"]
    return None


def refresh_youtube_access_token() -> str:
    """Exchange a stored refresh token for a short-lived access token."""

    client_id = os.getenv("YOUTUBE_CLIENT_ID")
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")
    refresh_token = _get_refresh_token()
    if not all([client_id, client_secret, refresh_token]):
        raise RuntimeError(
            "Missing YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, or YOUTUBE_REFRESH_TOKEN in environment"
        )

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    response = requests.post(TOKEN_URL, data=payload, timeout=30)
    if response.status_code >= 400:
        logger.error(
            "Failed to refresh YouTube access token: %s - %s", response.status_code, response.text
        )
        response.raise_for_status()

    data = response.json()
    access_token = data.get("access_token")
    if not access_token:
        raise RuntimeError("YouTube token response did not include an access_token")

    # Persist for future requests.
    _persist_access_token(access_token, refresh_token)
    return access_token


def publish_short_video(
    video_bytes: bytes,
    *,
    title: str,
    description: str,
    tags: Optional[Iterable[str]] = None,
    privacy_status: str = "public",
) -> Dict[str, str]:
    """Upload a short-form MP4 to YouTube using the resumable upload flow."""
    metadata = {
        "snippet": {
            "title": title[:100] or "Untitled Short",
            "description": description[:5000],
            "categoryId": "22",
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }
    if tags:
        metadata["snippet"]["tags"] = [str(tag)[:30] for tag in tags]

    for attempt in range(2):
        token = _get_youtube_token()
        init_headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(len(video_bytes)),
        }
        init_resp = requests.post(
            f"{UPLOAD_URL}?uploadType=resumable&part=snippet,status",
            headers=init_headers,
            json=metadata,
            timeout=30,
        )
        if init_resp.status_code >= 400:
            if init_resp.status_code == 401 and attempt == 0:
                logger.info("YouTube token expired during init upload, refreshing token.")
                refresh_youtube_access_token()
                continue
            logger.error("YouTube init upload failed: %s - %s", init_resp.status_code, init_resp.text)
            init_resp.raise_for_status()

        upload_endpoint = init_resp.headers.get("Location")
        if not upload_endpoint:
            raise RuntimeError("YouTube did not return an upload location")

        upload_headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "video/mp4",
        }
        upload_resp = requests.put(
            upload_endpoint,
            headers=upload_headers,
            data=video_bytes,
            timeout=120,
        )
        if upload_resp.status_code >= 400:
            if upload_resp.status_code == 401 and attempt == 0:
                logger.info("YouTube token expired during upload, refreshing token.")
                refresh_youtube_access_token()
                continue
            logger.error("YouTube upload failed: %s - %s", upload_resp.status_code, upload_resp.text)
            upload_resp.raise_for_status()

        data = upload_resp.json()
        return {
            "status": data.get("status", {}).get("uploadStatus", "uploaded"),
            "video_id": data.get("id"),
            "url": f"https://youtube.com/watch?v={data.get('id')}" if data.get("id") else None,
        }

    raise RuntimeError("Unable to publish to YouTube after refreshing the access token")


YOUTUBE_ANALYTICS_API = "https://youtubeanalytics.googleapis.com/v2/reports"
YOUTUBE_VIDEOS_API = "https://www.googleapis.com/youtube/v3/videos"


def _get_access_token_from_file() -> Optional[str]:
    token_path = Path("youtube_access_token.txt")
    try:
        raw = token_path.read_text().strip()
    except FileNotFoundError:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data.get("access_token")
    except json.JSONDecodeError:
        return raw


def _parse_iso8601_duration_seconds(duration: str) -> int:
    if not duration:
        return 0
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def _fetch_video_metadata(token: str, video_ids: Iterable[str]) -> Dict[str, Dict[str, object]]:
    ids = [vid for vid in video_ids if vid]
    if not ids:
        return {}
    metadata: Dict[str, Dict[str, object]] = {}
    headers = {"Authorization": f"Bearer {token}"}
    for start in range(0, len(ids), 50):
        chunk = ids[start:start + 50]
        response = requests.get(
            YOUTUBE_VIDEOS_API,
            headers=headers,
            params={
                "part": "contentDetails,snippet",
                "id": ",".join(chunk),
                "maxResults": "50",
            },
            timeout=30,
        )
        if response.status_code >= 400:
            logger.warning("YouTube videos metadata error: %s - %s", response.status_code, response.text)
            continue
        for item in (response.json().get("items") or []):
            video_id = item.get("id")
            if not video_id:
                continue
            snippet = item.get("snippet") or {}
            content_details = item.get("contentDetails") or {}
            metadata[video_id] = {
                "title": snippet.get("title") or video_id,
                "duration_seconds": _parse_iso8601_duration_seconds(content_details.get("duration") or ""),
            }
    return metadata


def fetch_youtube_analytics(days: int = 28) -> Dict[str, object]:
    # token = _get_access_token_from_file() or _get_youtube_token()
    token = _get_youtube_token()
    if not token:
        return {"error": "Missing YouTube access token."}

    end_date = dt.date.today()
    start_date = end_date - dt.timedelta(days=days)
    params = {
        "ids": "channel==MINE",
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "metrics": "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,subscribersGained",
    }
    headers = {"Authorization": f"Bearer {token}"}

    def _query(params_override: Dict) -> Optional[Dict]:
        query_params = {**params, **params_override}
        response = requests.get(YOUTUBE_ANALYTICS_API, headers=headers, params=query_params, timeout=30)
        if response.status_code == 401:
            refresh_youtube_access_token()
            new_token = _get_youtube_token()
            if new_token:
                headers["Authorization"] = f"Bearer {new_token}"
                response = requests.get(YOUTUBE_ANALYTICS_API, headers=headers, params=query_params, timeout=30)
        if response.status_code >= 400:
            logger.warning("YouTube analytics error: %s - %s", response.status_code, response.text)
            return None
        return response.json()

    summary = _query({})
    if not summary:
        return {"error": "Unable to fetch YouTube analytics."}

    row = (summary.get("rows") or [])[0] if summary.get("rows") else []
    metrics = summary.get("columnHeaders") or []
    metric_map: Dict[str, object] = {}
    for idx, header in enumerate(metrics):
        name = header.get("name")
        metric_map[name] = row[idx] if idx < len(row) else None

    trend_payload = _query({"dimensions": "day", "metrics": "views", "sort": "day"})
    trend = []
    if trend_payload and trend_payload.get("rows"):
        trend = [item[1] for item in trend_payload["rows"] if len(item) > 1]

    video_payload = _query(
        {
            "dimensions": "video",
            "metrics": "views,likes,comments",
            "sort": "-views",
            "maxResults": "50",
        }
    )
    short_rows = []
    if video_payload and video_payload.get("rows"):
        raw_rows = video_payload.get("rows") or []
        video_ids = [str(item[0]) for item in raw_rows if item and len(item) > 0]
        metadata_map = _fetch_video_metadata(token, video_ids)
        for item in raw_rows:
            if len(item) < 4:
                continue
            video_id = str(item[0])
            views = int(item[1] or 0)
            likes = int(item[2] or 0)
            comments = int(item[3] or 0)
            meta = metadata_map.get(video_id) or {}
            duration_seconds = int(meta.get("duration_seconds") or 0)
            if 0 < duration_seconds <= 60:
                short_rows.append(
                    {
                        "video_id": video_id,
                        "label": str(meta.get("title") or video_id)[:60],
                        "url": f"https://www.youtube.com/watch?v={video_id}",
                        "views": views,
                        "engagement": likes + comments,
                    }
                )

        if not short_rows:
            # Fallback: keep top 5 videos if Shorts filter cannot be inferred.
            for item in raw_rows[:5]:
                if len(item) < 4:
                    continue
                video_id = str(item[0])
                views = int(item[1] or 0)
                likes = int(item[2] or 0)
                comments = int(item[3] or 0)
                meta = metadata_map.get(video_id) or {}
                short_rows.append(
                    {
                        "video_id": video_id,
                        "label": str(meta.get("title") or video_id)[:60],
                        "url": f"https://www.youtube.com/watch?v={video_id}",
                        "views": views,
                        "engagement": likes + comments,
                    }
                )

    short_rows = short_rows[:5]
    short_views = [row["views"] for row in reversed(short_rows)]
    short_engagement = [row["engagement"] for row in reversed(short_rows)]

    return {
        "period": f"Last {days} days",
        "metrics": {
            "Views": metric_map.get("views"),
            "Watch time (min)": metric_map.get("estimatedMinutesWatched"),
            "Avg view duration": metric_map.get("averageViewDuration"),
            "Avg % viewed": metric_map.get("averageViewPercentage"),
            "Likes": metric_map.get("likes"),
            "Subscribers gained": metric_map.get("subscribersGained"),
        },
        "trend": trend,
        "shorts": {
            "period": "Last 5 Shorts",
            "metrics": {
                "Views": sum(row["views"] for row in short_rows),
                "Engagement": sum(row["engagement"] for row in short_rows),
            },
            "series": {
                "views": short_views,
                "engagement": short_engagement,
            },
            "rows": [
                {
                    "label": row["label"],
                    "views": str(row["views"]),
                    "engagement": str(row["engagement"]),
                    "url": row.get("url") or "",
                }
                for row in short_rows
            ],
        },
    }
