"""Amazon/AliExpress product -> Telegram approval -> kaymio.com + Instagram carousel."""
from .aliexpress import ALIEXPRESS_AUTO_POST_TASK, register_aliexpress_auto_post_task
from .approval import register_approval_handlers, start_publish
from .task import AUTO_POST_TASK, register_auto_post_task


def register_auto_post() -> None:
    """Register the scheduled task and the Telegram button handlers."""
    register_auto_post_task()
    register_aliexpress_auto_post_task()
    register_approval_handlers()


__all__ = ["ALIEXPRESS_AUTO_POST_TASK", "AUTO_POST_TASK", "register_auto_post", "start_publish"]
