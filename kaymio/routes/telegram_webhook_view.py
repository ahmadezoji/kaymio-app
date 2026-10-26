"""Telegram webhook endpoint (used only when TELEGRAM_UPDATES_MODE=webhook)."""
from __future__ import annotations

from flask import Blueprint, abort, request

from kaymio.integrations.telegram import WEBHOOK_PATH, handle_update, is_valid_webhook_secret

telegram_webhook_bp = Blueprint("telegram_webhook", __name__)


@telegram_webhook_bp.route(WEBHOOK_PATH, methods=["POST"])
def receive_telegram_update():
    if not is_valid_webhook_secret(request.headers.get("X-Telegram-Bot-Api-Secret-Token")):
        abort(403)
    update = request.get_json(silent=True)
    if isinstance(update, dict):
        handle_update(update)
    return {"ok": True}, 200
