"""Telegram approval step: nothing is published until the user taps Approve.

Message: the product's top image + title/price/rating/Amazon link + caption
preview, with ✅ Approve / ❌ Cancel buttons (callback ``autopost:<action>:<id>``).
"""
from __future__ import annotations

import datetime as dt
import html
import logging
import threading
from typing import Any, Dict, Optional

from kaymio.database.auto_post import (
    STATUS_EXPIRED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_PUBLISHED,
    STATUS_PUBLISHING,
    STATUS_REJECTED,
    get_candidate,
    transition_candidate,
    update_candidate,
)
from kaymio.features.auto_post.config import APPROVAL_TIMEOUT_HOURS
from kaymio.features.auto_post.publisher import publish_candidate
from kaymio.integrations.telegram import (
    CallbackQuery,
    InlineButton,
    TelegramApiError,
    dispatcher,
    get_telegram_bot,
    get_telegram_settings,
    inline_keyboard,
)

logger = logging.getLogger(__name__)

CALLBACK_NAMESPACE = "autopost"
ACTION_APPROVE = "approve"
ACTION_CANCEL = "cancel"
CAPTION_PREVIEW_CHARS = 350


# --------------------------------------------------------------------------- #
# Outgoing
# --------------------------------------------------------------------------- #
def send_approval_request(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Send the approval message and store its Telegram ids on the candidate."""
    settings = get_telegram_settings()
    keyboard = inline_keyboard([[
        InlineButton("✅ Approve & publish", f"{CALLBACK_NAMESPACE}:{ACTION_APPROVE}:{candidate['id']}"),
        InlineButton("❌ Cancel", f"{CALLBACK_NAMESPACE}:{ACTION_CANCEL}:{candidate['id']}"),
    ]])
    message = get_telegram_bot().send_photo(
        settings.chat_id,
        candidate["image_url"],
        caption=build_approval_caption(candidate),
        reply_markup=keyboard,
    )
    return update_candidate(
        candidate["id"],
        telegram_chat_id=settings.chat_id,
        telegram_message_id=int(message["message_id"]),
    )


def build_approval_caption(candidate: Dict[str, Any]) -> str:
    product = candidate.get("product_data") or {}
    content = candidate.get("content") or {}
    esc = html.escape

    facts = []
    if product.get("price"):
        facts.append(f"💲 {esc(str(product['price']))}")
    if product.get("rating") is not None:
        reviews = product.get("review_count")
        facts.append(f"⭐ {product['rating']}" + (f" ({reviews:,} ratings)" if reviews else ""))
    if product.get("source_rank"):
        facts.append(f"🔥 #{product['source_rank']} on Amazon list")

    preview = str(content.get("instagram_caption") or "")
    if len(preview) > CAPTION_PREVIEW_CHARS:
        preview = preview[:CAPTION_PREVIEW_CHARS].rstrip() + "…"

    lines = [
        f"🆕 <b>New product suggestion #{candidate['id']}</b>",
        "",
        f"<b>{esc(str(content.get('title') or candidate.get('title') or ''))}</b>",
        " · ".join(facts),
        f"🏷 {esc(str(product.get('category') or 'Uncategorized'))} · ASIN {esc(candidate['asin'])}",
        f"🔗 {esc(str(candidate.get('product_url') or ''))}",
        "",
        f"📝 <i>Instagram caption:</i>\n{esc(preview)}",
        "",
        f"Approve to add it to kaymio.com and post {len(content.get('image_urls') or [])} image(s) to Instagram.",
    ]
    return "\n".join(lines)


def notify(text: str, *, reply_to_message_id: Optional[int] = None) -> None:
    """Best-effort status message to the approval chat."""
    try:
        get_telegram_bot().send_message(
            get_telegram_settings().chat_id, text, reply_to_message_id=reply_to_message_id
        )
    except TelegramApiError:
        logger.exception("Failed to send Telegram notification.")


# --------------------------------------------------------------------------- #
# Publishing (shared by the Telegram button and the UI retry)
# --------------------------------------------------------------------------- #
def start_publish(candidate_id: int, *, allow_from_failed: bool = False) -> Optional[Dict[str, Any]]:
    """Claim the candidate for publishing and run it in the background.

    Returns the claimed candidate, or None if it isn't in a publishable state
    (already published, being published, rejected, ...).
    """
    from_statuses = {STATUS_PENDING, STATUS_FAILED} if allow_from_failed else {STATUS_PENDING}
    candidate = transition_candidate(
        candidate_id,
        from_statuses=from_statuses,
        to_status=STATUS_PUBLISHING,
        decided_at=dt.datetime.utcnow(),
        error=None,
    )
    if candidate is not None:
        threading.Thread(
            target=_publish_and_report,
            args=(candidate_id, candidate.get("telegram_message_id")),
            name=f"autopost-publish-{candidate_id}",
            daemon=True,
        ).start()
    return candidate


def _publish_and_report(candidate_id: int, reply_to_message_id: Optional[int]) -> None:
    try:
        result = publish_candidate(candidate_id)
    except Exception as exc:
        notify(
            f"⚠️ Publishing #{candidate_id} failed:\n<code>{html.escape(str(exc))[:800]}</code>\n"
            "You can retry from Kaymio → Automation.",
            reply_to_message_id=reply_to_message_id,
        )
        return
    notify(
        f"🎉 #{candidate_id} published!\n"
        f"🛒 Website: {html.escape(result.get('website_product_url') or '-')}\n"
        f"📸 Instagram media id: {html.escape(result.get('instagram_media_id') or '-')}",
        reply_to_message_id=reply_to_message_id,
    )


# --------------------------------------------------------------------------- #
# Incoming button clicks
# --------------------------------------------------------------------------- #
def _handle_callback(query: CallbackQuery) -> None:
    bot = get_telegram_bot()
    action, _, raw_id = query.payload.partition(":")
    try:
        candidate_id = int(raw_id)
    except ValueError:
        bot.answer_callback_query(query.id, "Unknown request.")
        return

    candidate = get_candidate(candidate_id)
    if candidate is None:
        bot.answer_callback_query(query.id, "This suggestion no longer exists.")
        return
    if candidate["status"] == STATUS_PENDING and _is_stale(candidate):
        transition_candidate(candidate_id, from_statuses={STATUS_PENDING}, to_status=STATUS_EXPIRED)

    if action == ACTION_APPROVE:
        claimed = start_publish(candidate_id)
        reply = "Approved — publishing now…" if claimed else _status_reply(candidate_id)
    elif action == ACTION_CANCEL:
        claimed = transition_candidate(
            candidate_id,
            from_statuses={STATUS_PENDING},
            to_status=STATUS_REJECTED,
            decided_at=dt.datetime.utcnow(),
        )
        reply = "Cancelled. This product won't be suggested again." if claimed else _status_reply(candidate_id)
    else:
        bot.answer_callback_query(query.id, "Unknown action.")
        return

    bot.answer_callback_query(query.id, reply)
    if query.message_id:
        try:
            bot.remove_inline_keyboard(query.chat_id, query.message_id)
        except TelegramApiError:
            logger.debug("Could not remove inline keyboard (already removed?).", exc_info=True)
        bot.send_message(
            query.chat_id,
            f"{'⏳' if action == ACTION_APPROVE and claimed else 'ℹ️'} #{candidate_id}: {html.escape(reply)}",
            reply_to_message_id=query.message_id,
        )


def _is_stale(candidate: Dict[str, Any]) -> bool:
    created_at = dt.datetime.fromisoformat(candidate["created_at"])
    return dt.datetime.utcnow() - created_at > dt.timedelta(hours=APPROVAL_TIMEOUT_HOURS)


def _status_reply(candidate_id: int) -> str:
    candidate = get_candidate(candidate_id) or {}
    return {
        STATUS_PUBLISHING: "Already being published.",
        STATUS_PUBLISHED: "Already published.",
        STATUS_REJECTED: "This suggestion was cancelled.",
        STATUS_EXPIRED: "This suggestion expired.",
        STATUS_FAILED: "Publishing failed earlier — retry from Kaymio → Automation.",
    }.get(candidate.get("status"), "This suggestion can't be changed any more.")


def register_approval_handlers() -> None:
    dispatcher.register_callback(CALLBACK_NAMESPACE, _handle_callback)
