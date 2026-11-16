"""Registry + runner for daily background tasks (Instagram stories, auto-post, ...).

A feature registers a ``ScheduledTask`` once at import/startup. Defaults come
from .env; the Automation page can override ``enabled``, ``run_time`` and the
task's declared options at runtime (stored in ``scheduled_task_settings``), so
changes apply on the next tick without a redeploy.

Semantics match the original story scheduler: a task fires once per day
during the hour of its HH:MM time, at or after that minute. The run date is
recorded *before* the task starts, so a slow or crashing run never repeats.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from kaymio.database.instagram_state import load_scheduler_state, update_scheduler_state
from kaymio.database.scheduled_tasks import (
    delete_task_settings_override,
    load_task_settings_override,
    save_task_settings_override,
)

logger = logging.getLogger(__name__)

TICK_SECONDS = 20
MIN_EVERY_DAYS, MAX_EVERY_DAYS = 1, 30
WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
RUN_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


@dataclass(frozen=True)
class OptionField:
    """A task-specific setting shown and validated on the Automation page."""

    name: str
    label: str
    kind: str  # int | float | text
    default: Any
    min: Optional[float] = None
    max: Optional[float] = None
    help: str = ""

    def coerce(self, raw: Any) -> Any:
        if self.kind == "text":
            return str(raw if raw is not None else "").strip()
        try:
            value = int(raw) if self.kind == "int" else float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{self.label} must be a number.") from None
        if self.min is not None and value < self.min:
            raise ValueError(f"{self.label} must be at least {self.min:g}.")
        if self.max is not None and value > self.max:
            raise ValueError(f"{self.label} must be at most {self.max:g}.")
        return value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "kind": self.kind,
            "default": self.default,
            "min": self.min,
            "max": self.max,
            "help": self.help,
        }


@dataclass(frozen=True)
class TaskSettings:
    enabled: bool
    run_time: str
    options: Dict[str, Any]
    overridden: bool = False
    every_days: int = 1  # 1 = every day, 2 = every other day, ...
    weekdays: Tuple[int, ...] = ()  # 0=Mon .. 6=Sun; empty = any day

    @property
    def hour_minute(self) -> Tuple[int, int]:
        hour, minute = self.run_time.split(":", 1)
        return int(hour), int(minute)


@dataclass(frozen=True)
class ScheduledTask:
    key: str
    label: str
    description: str
    run: Callable[[TaskSettings], None]
    default_enabled: bool
    default_run_time: str
    option_fields: Tuple[OptionField, ...] = field(default_factory=tuple)
    default_every_days: int = 1
    default_weekdays: Tuple[int, ...] = ()

    @property
    def state_key(self) -> str:
        return f"{self.key}_scheduler"

    def default_settings(self) -> TaskSettings:
        return TaskSettings(
            enabled=self.default_enabled,
            run_time=normalize_run_time(self.default_run_time),
            options={f.name: f.default for f in self.option_fields},
            every_days=self.default_every_days,
            weekdays=self.default_weekdays,
        )


_registry: Dict[str, ScheduledTask] = {}
_running: Dict[str, threading.Lock] = {}
_ticker_started = False
_ticker_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
def register_task(task: ScheduledTask) -> ScheduledTask:
    _registry[task.key] = task
    _running.setdefault(task.key, threading.Lock())
    return task


def get_task(task_key: str) -> ScheduledTask:
    try:
        return _registry[task_key]
    except KeyError:
        raise KeyError(f"Unknown scheduled task: {task_key}") from None


def list_tasks() -> List[ScheduledTask]:
    return list(_registry.values())


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def normalize_run_time(raw: str) -> str:
    value = str(raw or "").strip()
    if re.fullmatch(r"\d:\d\d", value):
        value = f"0{value}"
    if not RUN_TIME_RE.match(value):
        raise ValueError(f"Run time must be HH:MM (24h), got {raw!r}.")
    return value


def normalize_every_days(raw: Any) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError("Run every N days must be a whole number.") from None
    if not MIN_EVERY_DAYS <= value <= MAX_EVERY_DAYS:
        raise ValueError(f"Run every N days must be between {MIN_EVERY_DAYS} and {MAX_EVERY_DAYS}.")
    return value


def parse_weekdays(raw: Any) -> Tuple[int, ...]:
    """Accept "0,3", "mon,thu", or a list of ints/names; empty means any day."""
    if raw in (None, ""):
        return ()
    items = raw if isinstance(raw, (list, tuple)) else str(raw).split(",")
    days = set()
    for item in items:
        token = str(item).strip().lower()[:3]
        if not token:
            continue
        if token.isdigit() and int(token) in range(7):
            days.add(int(token))
        elif token in WEEKDAY_NAMES:
            days.add(WEEKDAY_NAMES.index(token))
        else:
            raise ValueError(f"Unknown weekday {item!r}.")
    return tuple(sorted(days)) if len(days) < 7 else ()


def resolve_settings(task: ScheduledTask) -> TaskSettings:
    defaults = task.default_settings()
    override = load_task_settings_override(task.key)
    if not override:
        return defaults
    options = dict(defaults.options)
    for option in task.option_fields:
        if option.name in override["options"]:
            try:
                options[option.name] = option.coerce(override["options"][option.name])
            except ValueError:
                logger.warning("Ignoring invalid saved option %s.%s", task.key, option.name)
    try:
        run_time = normalize_run_time(override["run_time"])
    except ValueError:
        run_time = defaults.run_time
    try:
        every_days = normalize_every_days(override.get("every_days") or defaults.every_days)
        weekdays = parse_weekdays(override.get("weekdays")) if override.get("weekdays") is not None else defaults.weekdays
    except ValueError:
        every_days, weekdays = defaults.every_days, defaults.weekdays
    return TaskSettings(
        enabled=override["enabled"],
        run_time=run_time,
        options=options,
        overridden=True,
        every_days=every_days,
        weekdays=weekdays,
    )


def update_settings(task_key: str, payload: Dict[str, Any]) -> TaskSettings:
    """Validate and persist settings edited in the UI. Raises ValueError on bad input."""
    task = get_task(task_key)
    current = resolve_settings(task)
    raw_options = payload.get("options") or {}
    options = dict(current.options)
    for option in task.option_fields:
        if option.name in raw_options:
            options[option.name] = option.coerce(raw_options[option.name])
    enabled = bool(payload["enabled"]) if "enabled" in payload else current.enabled
    run_time = normalize_run_time(payload["run_time"]) if "run_time" in payload else current.run_time
    every_days = normalize_every_days(payload["every_days"]) if "every_days" in payload else current.every_days
    weekdays = parse_weekdays(payload["weekdays"]) if "weekdays" in payload else current.weekdays
    save_task_settings_override(
        task_key,
        enabled=enabled,
        run_time=run_time,
        options=options,
        every_days=every_days,
        weekdays=",".join(str(day) for day in weekdays),
    )
    return resolve_settings(task)


def reset_settings(task_key: str) -> TaskSettings:
    task = get_task(task_key)
    delete_task_settings_override(task_key)
    return task.default_settings()


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
def is_task_running(task_key: str) -> bool:
    return _running[task_key].locked()


def get_task_state(task_key: str) -> Dict[str, Any]:
    return load_scheduler_state(get_task(task_key).state_key)


def run_task_now(task_key: str) -> bool:
    """Start a task in the background immediately. Returns False if it is already running."""
    task = get_task(task_key)
    return _start_worker(task, resolve_settings(task), trigger="manual")


def _start_worker(task: ScheduledTask, settings: TaskSettings, *, trigger: str) -> bool:
    lock = _running[task.key]
    if not lock.acquire(blocking=False):
        logger.info("Scheduled task %s is already running; skipping %s trigger.", task.key, trigger)
        return False
    threading.Thread(
        target=_run_worker,
        args=(task, settings, trigger, lock),
        name=f"task-{task.key}",
        daemon=True,
    ).start()
    return True


def _run_worker(task: ScheduledTask, settings: TaskSettings, trigger: str, lock: threading.Lock) -> None:
    started_at = dt.datetime.now()
    logger.info("Scheduled task %s started (%s trigger).", task.key, trigger)
    try:
        update_scheduler_state(task.state_key, last_started_at=started_at.isoformat(), last_trigger=trigger)
        task.run(settings)
        result, error = "ok", ""
    except Exception as exc:
        logger.exception("Scheduled task %s failed.", task.key)
        result, error = "error", f"{type(exc).__name__}: {exc}"
    finally:
        lock.release()
    update_scheduler_state(
        task.state_key,
        last_finished_at=dt.datetime.now().isoformat(),
        last_result=result,
        last_error=error,
    )
    logger.info("Scheduled task %s finished: %s", task.key, result)


def _is_due(settings: TaskSettings, state: Dict[str, Any], now: dt.datetime) -> bool:
    target_hour, target_minute = settings.hour_minute
    if not (settings.enabled and now.hour == target_hour and now.minute >= target_minute):
        return False
    if settings.weekdays and now.weekday() not in settings.weekdays:
        return False
    last_run_date = state.get("last_run_date")
    if not last_run_date:
        return True
    try:
        days_since = (now.date() - dt.date.fromisoformat(last_run_date)).days
    except ValueError:
        return True
    return days_since >= settings.every_days


def _tick(now: dt.datetime) -> None:
    for task in list_tasks():
        try:
            settings = resolve_settings(task)
            if not _is_due(settings, load_scheduler_state(task.state_key), now):
                continue
            update_scheduler_state(task.state_key, last_run_date=now.date().isoformat())
            logger.info("Triggering scheduled task %s at %s", task.key, now.time())
            _start_worker(task, settings, trigger="schedule")
        except Exception:
            logger.exception("Scheduler tick failed for task %s", task.key)


def _ticker_loop() -> None:
    logger.info("Task scheduler started for: %s", ", ".join(_registry) or "(none)")
    while True:
        _tick(dt.datetime.now())
        time.sleep(TICK_SECONDS)


def start_scheduled_tasks() -> None:
    """Start the single scheduler ticker thread (idempotent)."""
    global _ticker_started
    with _ticker_lock:
        if _ticker_started:
            return
        _ticker_started = True
    threading.Thread(target=_ticker_loop, name="task-scheduler", daemon=True).start()
