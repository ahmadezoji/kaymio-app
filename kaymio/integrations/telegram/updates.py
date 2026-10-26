"""Receives Telegram updates via long polling (default) or a webhook.

TELEGRAM_UPDATES_MODE:
  * ``polling`` - a daemon thread calls getUpdates; no public URL needed.
  * ``webhook`` - Telegram POSTs to ``{PUBLIC_APP_BASE_URL}/webhooks/telegram``,
                  verified with TELEGRAM_WEBHOOK_SECRET.
  * ``off``     - outgoing messages only; button clicks are not processed.
"""
from __future__ import annotations

import hmac
import logging
import os
import threading
import time
from functools import lru_cache
from typing import Any, Dict, Optional

from .bot_client import TelegramApiError, TelegramBotClient, TelegramSettings
from .dispatcher import IncomingMessage, dispatcher

logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/webhooks/telegram"
_started = False
_start_lock = threading.Lock()


@lru_cache(maxsize=1)
def get_telegram_settings() -> TelegramSettings:
    return TelegramSettings.from_env()


@lru_cache(maxsize=1)
def get_telegram_bot() -> TelegramBotClient:
    return TelegramBotClient(get_telegram_settings())


def handle_update(update: Dict[str, Any]) -> None:
    dispatcher.dispatch(update, authorized_chat_id=get_telegram_settings().chat_id)


def is_valid_webhook_secret(received_secret: Optional[str]) -> bool:
    expected = get_telegram_settings().webhook_secret
    return bool(expected) and hmac.compare_digest(expected, received_secret or "")


def start_telegram_updates() -> None:
    """Start receiving updates according to TELEGRAM_UPDATES_MODE (idempotent)."""
    global _started
    settings = get_telegram_settings()
    with _start_lock:
        if _started:
            return
        _started = True
    if not settings.bot_token:
        logger.info("Telegram bot disabled: TELEGRAM_BOT_TOKEN is not set.")
        return
    if settings.updates_mode == "off":
        logger.info("Telegram update receiving is off (TELEGRAM_UPDATES_MODE=off).")
        return
    if settings.updates_mode == "webhook":
        _register_webhook(settings)
        return
    threading.Thread(target=_poll_forever, name="telegram-poller", daemon=True).start()


def _register_webhook(settings: TelegramSettings) -> None:
    base_url = os.getenv("PUBLIC_APP_BASE_URL", "").rstrip("/")
    if not base_url or not settings.webhook_secret:
        logger.error("Telegram webhook mode needs PUBLIC_APP_BASE_URL and TELEGRAM_WEBHOOK_SECRET.")
        return
    try:
        get_telegram_bot().set_webhook(f"{base_url}{WEBHOOK_PATH}", settings.webhook_secret)
        logger.info("Telegram webhook registered at %s%s", base_url, WEBHOOK_PATH)
    except TelegramApiError:
        logger.exception("Telegram webhook registration failed.")


def _poll_forever() -> None:
    bot = get_telegram_bot()
    settings = get_telegram_settings()
    try:
        # getUpdates is rejected while a webhook is set.
        bot.delete_webhook()
    except TelegramApiError:
        logger.warning("Could not clear Telegram webhook before polling.", exc_info=True)
    logger.info("Telegram long-polling started.")
    offset: Optional[int] = None
    while True:
        try:
            for update in bot.get_updates(offset, settings.poll_timeout_seconds):
                offset = int(update["update_id"]) + 1
                handle_update(update)
        except TelegramApiError as exc:
            logger.warning("Telegram polling error: %s", exc)
            time.sleep(10)
        except Exception:
            logger.exception("Unexpected Telegram polling error")
            time.sleep(10)


def _handle_start_command(message: IncomingMessage, _args: str) -> None:
    """Public /start: tells the user their chat id so TELEGRAM_CHAT_ID can be configured."""
    authorized = message.chat_id == get_telegram_settings().chat_id
    status = "This chat is authorized for Kaymio approvals." if authorized else (
        "Set TELEGRAM_CHAT_ID to this value in Kaymio's .env to receive approvals here."
    )
    get_telegram_bot().send_message(
        message.chat_id,
        f"👋 Kaymio bot is online.\nChat id: <code>{message.chat_id}</code>\n{status}",
    )


dispatcher.register_command("start", _handle_start_command, public=True)
