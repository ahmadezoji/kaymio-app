"""Persistence for scheduled-task settings overrides (edited from the Automation page)."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from .db import session_scope
from .models import ScheduledTaskSetting


def load_task_settings_override(task_key: str) -> Optional[Dict[str, Any]]:
    """Return ``{enabled, run_time, every_days, weekdays, options}`` saved from the UI, or None."""
    with session_scope() as session:
        row = session.get(ScheduledTaskSetting, task_key)
        if row is None:
            return None
        try:
            options = json.loads(row.options or "{}")
        except json.JSONDecodeError:
            options = {}
        return {
            "enabled": bool(row.enabled),
            "run_time": row.run_time,
            "every_days": row.every_days,
            "weekdays": row.weekdays,
            "options": options if isinstance(options, dict) else {},
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }


def save_task_settings_override(
    task_key: str,
    *,
    enabled: bool,
    run_time: str,
    options: Dict[str, Any],
    every_days: int = 1,
    weekdays: str = "",
) -> None:
    with session_scope() as session:
        row = session.get(ScheduledTaskSetting, task_key)
        if row is None:
            row = ScheduledTaskSetting(task_key=task_key)
            session.add(row)
        row.enabled = enabled
        row.run_time = run_time
        row.every_days = every_days
        row.weekdays = weekdays
        row.options = json.dumps(options)


def delete_task_settings_override(task_key: str) -> None:
    with session_scope() as session:
        session.query(ScheduledTaskSetting).filter_by(task_key=task_key).delete()
