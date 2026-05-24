"""File manager view for generated media under template_images."""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for

file_manager_bp = Blueprint("file_manager", __name__)

IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
VIDEO_EXTENSIONS = {"mp4", "mov", "m4v", "webm"}
PATH_COMPANION_FIELDS = {
    "generated_image_path": (
        "generated_image_url",
        "generated_image_download_url",
        "image_public_url",
        "image_data",
    ),
    "generated_video_path": (
        "video_url",
        "video_download_url",
        "video_public_url",
    ),
    "instagram_image_path": (
        "instagram_image_url",
        "instagram_image_download_url",
        "instagram_image_public_url",
        "instagram_image_data",
    ),
}


@dataclass
class MediaItem:
    relative_path: str
    name: str
    directory: str
    kind: str
    size_bytes: int
    size_label: str
    modified_epoch: float
    modified_at: str
    preview_url: str
    download_url: str


def _storage_root() -> Path:
    return Path(current_app.root_path) / "template_images"


def _state_file() -> Path:
    return Path(current_app.root_path) / "data" / "app_state.json"


def _normalize_kind(raw_value: str) -> str:
    candidate = (raw_value or "all").strip().lower()
    return candidate if candidate in {"all", "image", "video"} else "all"


def _normalize_sort(raw_value: str) -> str:
    candidate = (raw_value or "newest").strip().lower()
    return candidate if candidate in {"newest", "oldest"} else "newest"


def _format_bytes(size_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(size_bytes)
    unit = units[0]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            break
        value /= 1024
    if unit == "B":
        return f"{int(value)} {unit}"
    return f"{value:.1f} {unit}"


def _detect_kind(path: Path) -> str | None:
    suffix = path.suffix.lower().lstrip(".")
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    return None


def _resolve_media_path(relative_path: str) -> Tuple[str, Path]:
    root = _storage_root().resolve()
    safe_path = (root / relative_path).resolve()
    if not str(safe_path).startswith(str(root)) or not safe_path.exists() or not safe_path.is_file():
        abort(404)
    return safe_path.relative_to(root).as_posix(), safe_path


def _build_media_items() -> List[MediaItem]:
    root = _storage_root()
    if not root.exists():
        return []

    items: List[MediaItem] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        kind = _detect_kind(path)
        if not kind:
            continue

        stat = path.stat()
        relative_path = path.relative_to(root).as_posix()
        items.append(
            MediaItem(
                relative_path=relative_path,
                name=path.name,
                directory=path.parent.relative_to(root).as_posix() if path.parent != root else "/",
                kind=kind,
                size_bytes=int(stat.st_size),
                size_label=_format_bytes(int(stat.st_size)),
                modified_epoch=float(stat.st_mtime),
                modified_at=dt.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                preview_url=url_for("serve_media", filename=relative_path),
                download_url=url_for("download_media", filename=relative_path),
            )
        )

    return items


def _sort_media_items(items: List[MediaItem], sort_order: str) -> List[MediaItem]:
    reverse = sort_order != "oldest"
    return sorted(items, key=lambda item: item.modified_epoch, reverse=reverse)


def _group_media_items(items: List[MediaItem]) -> Dict[str, List[MediaItem]]:
    grouped = {"image": [], "video": []}
    for item in items:
        grouped[item.kind].append(item)
    return grouped


def _media_summary(items: List[MediaItem]) -> Dict[str, int]:
    image_count = sum(1 for item in items if item.kind == "image")
    video_count = sum(1 for item in items if item.kind == "video")
    total_bytes = sum(item.size_bytes for item in items)
    return {
        "total_count": len(items),
        "image_count": image_count,
        "video_count": video_count,
        "total_bytes": total_bytes,
        "total_size_label": _format_bytes(total_bytes),
    }


def _load_state_payload() -> Dict[str, Any]:
    state_file = _state_file()
    if not state_file.exists():
        return {}
    try:
        payload = json.loads(state_file.read_text())
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_state_payload(payload: Dict[str, Any]) -> None:
    _state_file().write_text(json.dumps(payload, indent=2))


def _prune_media_references(node: Any, relative_path: str) -> bool:
    changed = False
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if key == "selected_original_image" and value == f"path:{relative_path}":
                node[key] = ""
                changed = True
                continue
            if key == "original_image_paths" and isinstance(value, list):
                filtered = [item for item in value if item != relative_path]
                if filtered != value:
                    node[key] = filtered
                    changed = True
                continue
            if isinstance(value, str) and value == relative_path:
                node[key] = ""
                changed = True
                for companion_key in PATH_COMPANION_FIELDS.get(key, ()):
                    if node.get(companion_key):
                        node[companion_key] = ""
                        changed = True
                continue
            changed = _prune_media_references(value, relative_path) or changed
    elif isinstance(node, list):
        retained_items = []
        for item in node:
            if isinstance(item, str) and item == relative_path:
                changed = True
                continue
            changed = _prune_media_references(item, relative_path) or changed
            retained_items.append(item)
        if retained_items != node:
            node[:] = retained_items
    return changed


def _cleanup_deleted_media_references(relative_path: str) -> bool:
    state = _load_state_payload()
    if not state:
        return False
    changed = _prune_media_references(state, relative_path)
    if changed:
        _save_state_payload(state)
    return changed


@file_manager_bp.route("/file-manager", methods=["GET"])
def file_manager_view():
    kind_filter = _normalize_kind(request.args.get("kind", "all"))
    sort_order = _normalize_sort(request.args.get("sort", "newest"))
    all_media_items = _sort_media_items(_build_media_items(), sort_order)
    media_items = [
        item for item in all_media_items if kind_filter == "all" or item.kind == kind_filter
    ]
    grouped_items = _group_media_items(media_items)
    summary = _media_summary(all_media_items)
    filtered_summary = _media_summary(media_items)
    return render_template(
        "file_manager.html",
        active_kind=kind_filter,
        active_sort=sort_order,
        media_items=media_items,
        image_items=grouped_items["image"],
        video_items=grouped_items["video"],
        summary=summary,
        filtered_summary=filtered_summary,
    )


@file_manager_bp.route("/file-manager/delete", methods=["POST"])
def file_manager_delete():
    kind_filter = _normalize_kind(request.form.get("kind", "all"))
    sort_order = _normalize_sort(request.form.get("sort", "newest"))
    relative_path = (request.form.get("relative_path") or "").strip()
    if not relative_path:
        flash("Missing file path.", "error")
        return redirect(url_for("file_manager.file_manager_view", kind=kind_filter, sort=sort_order))

    safe_relative_path, safe_path = _resolve_media_path(relative_path)
    safe_path.unlink(missing_ok=False)
    references_cleaned = _cleanup_deleted_media_references(safe_relative_path)
    if references_cleaned:
        flash(f"Deleted {safe_relative_path} and cleaned saved references.", "success")
    else:
        flash(f"Deleted {safe_relative_path}.", "success")
    return redirect(url_for("file_manager.file_manager_view", kind=kind_filter, sort=sort_order))
