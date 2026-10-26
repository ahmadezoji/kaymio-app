"""Telegram bot service: outgoing messages, inline buttons, and update dispatching."""
from .bot_client import (
    InlineButton,
    TelegramApiError,
    TelegramBotClient,
    TelegramSettings,
    inline_keyboard,
)
from .dispatcher import CallbackQuery, IncomingMessage, dispatcher
from .updates import (
    WEBHOOK_PATH,
    get_telegram_bot,
    get_telegram_settings,
    handle_update,
    is_valid_webhook_secret,
    start_telegram_updates,
)

__all__ = [
    "CallbackQuery",
    "IncomingMessage",
    "InlineButton",
    "TelegramApiError",
    "TelegramBotClient",
    "TelegramSettings",
    "WEBHOOK_PATH",
    "dispatcher",
    "get_telegram_bot",
    "get_telegram_settings",
    "handle_update",
    "inline_keyboard",
    "is_valid_webhook_secret",
    "start_telegram_updates",
]
