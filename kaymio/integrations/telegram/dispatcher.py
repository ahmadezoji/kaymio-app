"""Routes incoming Telegram updates to feature handlers.

Features register handlers instead of the transport (polling/webhook) knowing
about them:

    dispatcher.register_callback("autopost", handle_autopost_click)
    dispatcher.register_command("start", handle_start)

Inline-button ``callback_data`` must be ``"<namespace>:<payload>"``; the
handler receives the payload part. Updates from any chat other than the
configured TELEGRAM_CHAT_ID are ignored (except commands marked public).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CallbackQuery:
    id: str
    namespace: str
    payload: str
    chat_id: str
    message_id: Optional[int]
    from_username: str


@dataclass(frozen=True)
class IncomingMessage:
    chat_id: str
    message_id: int
    text: str
    from_username: str


CallbackHandler = Callable[[CallbackQuery], None]
CommandHandler = Callable[[IncomingMessage, str], None]


class TelegramUpdateDispatcher:
    def __init__(self) -> None:
        self._callback_handlers: Dict[str, CallbackHandler] = {}
        self._command_handlers: Dict[str, tuple[CommandHandler, bool]] = {}

    def register_callback(self, namespace: str, handler: CallbackHandler) -> None:
        if ":" in namespace:
            raise ValueError("Callback namespace must not contain ':'")
        self._callback_handlers[namespace] = handler

    def register_command(self, command: str, handler: CommandHandler, *, public: bool = False) -> None:
        self._command_handlers[command.lstrip("/").lower()] = (handler, public)

    def dispatch(self, update: Dict[str, Any], *, authorized_chat_id: str) -> None:
        """Handle one Bot API update; handler errors are logged, never raised."""
        try:
            if "callback_query" in update:
                self._dispatch_callback(update["callback_query"], authorized_chat_id)
            elif "message" in update:
                self._dispatch_message(update["message"], authorized_chat_id)
        except Exception:
            logger.exception("Telegram update handler failed (update_id=%s)", update.get("update_id"))

    def _dispatch_callback(self, raw: Dict[str, Any], authorized_chat_id: str) -> None:
        message = raw.get("message") or {}
        chat_id = str((message.get("chat") or {}).get("id") or "")
        if chat_id != authorized_chat_id:
            logger.warning("Ignoring Telegram callback from unauthorized chat %s", chat_id)
            return
        namespace, _, payload = str(raw.get("data") or "").partition(":")
        handler = self._callback_handlers.get(namespace)
        if handler is None:
            logger.warning("No Telegram callback handler for namespace %r", namespace)
            return
        handler(
            CallbackQuery(
                id=str(raw.get("id") or ""),
                namespace=namespace,
                payload=payload,
                chat_id=chat_id,
                message_id=message.get("message_id"),
                from_username=str((raw.get("from") or {}).get("username") or ""),
            )
        )

    def _dispatch_message(self, raw: Dict[str, Any], authorized_chat_id: str) -> None:
        text = str(raw.get("text") or "").strip()
        if not text.startswith("/"):
            return
        command_token, _, args = text.partition(" ")
        command = command_token.lstrip("/").split("@", 1)[0].lower()
        registered = self._command_handlers.get(command)
        if registered is None:
            return
        handler, public = registered
        chat_id = str((raw.get("chat") or {}).get("id") or "")
        if not public and chat_id != authorized_chat_id:
            logger.warning("Ignoring Telegram command /%s from unauthorized chat %s", command, chat_id)
            return
        handler(
            IncomingMessage(
                chat_id=chat_id,
                message_id=int(raw.get("message_id") or 0),
                text=text,
                from_username=str((raw.get("from") or {}).get("username") or ""),
            ),
            args.strip(),
        )


dispatcher = TelegramUpdateDispatcher()
