"""Automation page: pause/edit/run scheduled tasks and review auto-post suggestions."""
from __future__ import annotations

import logging
from typing import Any, Dict

from flask import Blueprint, jsonify, render_template, request

from kaymio.database.auto_post import STATUS_FAILED, get_candidate, list_candidates
from kaymio.features.auto_post import start_publish
from kaymio.integrations.crawl4ai import Crawl4AIError, get_crawl4ai_client
from kaymio.integrations.telegram import TelegramApiError, get_telegram_bot, get_telegram_settings
from kaymio.tasks.scheduler import (
    ScheduledTask,
    get_task,
    get_task_state,
    is_task_running,
    list_tasks,
    reset_settings,
    resolve_settings,
    run_task_now,
    update_settings,
)

logger = logging.getLogger(__name__)

automation_page_bp = Blueprint("automation_page", __name__)
automation_bp = Blueprint("automation", __name__, url_prefix="/api/automation")


def _task_to_dict(task: ScheduledTask) -> Dict[str, Any]:
    settings = resolve_settings(task)
    return {
        "key": task.key,
        "label": task.label,
        "description": task.description,
        "enabled": settings.enabled,
        "run_time": settings.run_time,
        "options": settings.options,
        "overridden": settings.overridden,
        "option_fields": [field.to_dict() for field in task.option_fields],
        "defaults": {
            "enabled": task.default_enabled,
            "run_time": task.default_run_time,
        },
        "running": is_task_running(task.key),
        "state": get_task_state(task.key),
    }


def _get_task_or_404(task_key: str):
    try:
        return get_task(task_key), None
    except KeyError:
        return None, (jsonify({"error": f"Unknown task '{task_key}'."}), 404)


@automation_page_bp.route("/automation", methods=["GET"])
def automation_page():
    return render_template("automation.html")


@automation_bp.route("/tasks", methods=["GET"])
def api_list_tasks():
    return jsonify({"tasks": [_task_to_dict(task) for task in list_tasks()]})


@automation_bp.route("/tasks/<task_key>", methods=["PUT"])
def api_update_task(task_key: str):
    task, error = _get_task_or_404(task_key)
    if error:
        return error
    try:
        update_settings(task_key, request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"task": _task_to_dict(task)})


@automation_bp.route("/tasks/<task_key>/settings", methods=["DELETE"])
def api_reset_task(task_key: str):
    task, error = _get_task_or_404(task_key)
    if error:
        return error
    reset_settings(task_key)
    return jsonify({"task": _task_to_dict(task)})


@automation_bp.route("/tasks/<task_key>/run", methods=["POST"])
def api_run_task(task_key: str):
    task, error = _get_task_or_404(task_key)
    if error:
        return error
    if not run_task_now(task_key):
        return jsonify({"error": "This task is already running.", "task": _task_to_dict(task)}), 409
    return jsonify({"task": _task_to_dict(task)}), 202


@automation_bp.route("/auto-post/candidates", methods=["GET"])
def api_list_candidates():
    limit = request.args.get("limit", default=30, type=int)
    return jsonify({"candidates": list_candidates(limit=max(1, min(limit, 200)))})


@automation_bp.route("/auto-post/candidates/<int:candidate_id>/retry", methods=["POST"])
def api_retry_candidate(candidate_id: int):
    candidate = get_candidate(candidate_id)
    if candidate is None:
        return jsonify({"error": "Suggestion not found."}), 404
    if candidate["status"] != STATUS_FAILED:
        return jsonify({"error": "Only failed suggestions can be retried."}), 409
    claimed = start_publish(candidate_id, allow_from_failed=True)
    if claimed is None:
        return jsonify({"error": "Suggestion is no longer in a retryable state."}), 409
    return jsonify({"candidate": claimed}), 202


@automation_bp.route("/health", methods=["GET"])
def api_health():
    crawl4ai = get_crawl4ai_client()
    crawl_status: Dict[str, Any] = {"configured": crawl4ai.is_configured, "base_url": crawl4ai.base_url}
    if crawl4ai.is_configured:
        try:
            crawl_status["health"] = crawl4ai.health()
            crawl_status["ok"] = True
        except Crawl4AIError as exc:
            crawl_status.update(ok=False, error=str(exc))

    telegram_settings = get_telegram_settings()
    telegram_status: Dict[str, Any] = {
        "configured": telegram_settings.is_configured,
        "updates_mode": telegram_settings.updates_mode,
    }
    if telegram_settings.bot_token:
        try:
            telegram_status["bot_username"] = get_telegram_bot().get_me().get("username")
            telegram_status["ok"] = telegram_settings.is_configured
        except TelegramApiError as exc:
            telegram_status.update(ok=False, error=str(exc))

    return jsonify({"crawl4ai": crawl_status, "telegram": telegram_status})
