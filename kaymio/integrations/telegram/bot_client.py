"""Thin wrapper around the Telegram Bot HTTP API.

Only the calls Kaymio needs are wrapped; add new methods here as the bot grows.
Every method raises TelegramApiError on failure so callers can decide whether
a failed notification should abort their workflow.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import requests

logger = logging.getLogger(__name__)

CAPTION_LIMIT = 1024
MESSAGE_LIMIT = 4096


class TelegramApiError(RuntimeError):
    """Raised when the Bot API is unreachable or returns ``ok: false``."""


@dataclass(frozen=True)
class TelegramSettings:
    bot_token: str
    chat_id: str
    api_base_url: str = "https://api.telegram.org"
    updates_mode: str = "polling"  # polling | webhook | off
    webhook_secret: str = ""
    poll_timeout_seconds: int = 30
    request_timeout_seconds: float = 15.0

    @classmethod
    def from_env(cls) -> "TelegramSettings":
        return cls(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
            api_base_url=os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org").rstrip("/"),
            updates_mode=os.getenv("TELEGRAM_UPDATES_MODE", "polling").strip().lower(),
            webhook_secret=os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip(),
            poll_timeout_seconds=int(os.getenv("TELEGRAM_POLL_TIMEOUT_SECONDS", "30")),
            request_timeout_seconds=float(os.getenv("TELEGRAM_REQUEST_TIMEOUT_SECONDS", "15")),
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)


@dataclass(frozen=True)
class InlineButton:
    text: str
    callback_data: str


def inline_keyboard(rows: Sequence[Sequence[InlineButton]]) -> Dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": button.text, "callback_data": button.callback_data} for button in row]
            for row in rows
        ]
    }


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class TelegramBotClient:
    def __init__(self, settings: TelegramSettings) -> None:
        self.settings = settings
        self._session = requests.Session()

    def call(self, method: str, payload: Optional[Dict[str, Any]] = None, *, timeout: Optional[float] = None) -> Any:
        if not self.settings.bot_token:
            raise TelegramApiError("TELEGRAM_BOT_TOKEN is not configured.")
        url = f"{self.settings.api_base_url}/bot{self.settings.bot_token}/{method}"
        try:
            response = self._session.post(
                url,
                json=payload or {},
                timeout=timeout or self.settings.request_timeout_seconds,
            )
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            # Never log the URL: it contains the bot token.
            raise TelegramApiError(f"Telegram {method} request failed: {type(exc).__name__}") from exc
        if not body.get("ok"):
            raise TelegramApiError(
                f"Telegram {method} failed: {body.get('error_code')} {body.get('description')}"
            )
        return body.get("result")

    # -- outgoing ---------------------------------------------------------- #
    def get_me(self) -> Dict[str, Any]:
        return self.call("getMe")

    def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        reply_to_message_id: Optional[int] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
        parse_mode: Optional[str] = "HTML",
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": truncate(text, MESSAGE_LIMIT),
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_to_message_id:
            payload["reply_parameters"] = {"message_id": reply_to_message_id, "allow_sending_without_reply": True}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self.call("sendMessage", payload)

    def send_photo(
        self,
        chat_id: str,
        photo_url: str,
        *,
        caption: str = "",
        reply_markup: Optional[Dict[str, Any]] = None,
        parse_mode: Optional[str] = "HTML",
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "photo": photo_url,
            "caption": truncate(caption, CAPTION_LIMIT),
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self.call("sendPhoto", payload)

    def remove_inline_keyboard(self, chat_id: str, message_id: int) -> None:
        self.call(
            "editMessageReplyMarkup",
            {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}},
        )

    def answer_callback_query(self, callback_query_id: str, text: str = "", *, show_alert: bool = False) -> None:
        self.call(
            "answerCallbackQuery",
            {"callback_query_id": callback_query_id, "text": truncate(text, 200), "show_alert": show_alert},
        )

    # -- incoming ---------------------------------------------------------- #
    def get_updates(self, offset: Optional[int], timeout_seconds: int) -> List[Dict[str, Any]]:
        payload: Dict[str, Any] = {
            "timeout": timeout_seconds,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        return self.call("getUpdates", payload, timeout=timeout_seconds + 10) or []

    def set_webhook(self, url: str, secret_token: str = "") -> None:
        payload: Dict[str, Any] = {"url": url, "allowed_updates": ["message", "callback_query"]}
        if secret_token:
            payload["secret_token"] = secret_token
        self.call("setWebhook", payload)

    def delete_webhook(self) -> None:
        self.call("deleteWebhook", {"drop_pending_updates": False})
