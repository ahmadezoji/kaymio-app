"""Flask app configuration and global constants."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Flask configuration
SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")
MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB

# File handling
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
ALLOWED_VIDEO_EXTENSIONS = {"mp4", "mov", "m4v", "webm"}

# Markets
MARKET_OPTIONS = [
    "Shein",
    "Amazon",
    "AliExpress",
    "Temu",
    "Etsy",
    "eBay",
    "Walmart",
]

# Platform management
RESETTABLE_PLATFORMS = {"pinterest", "instagram", "youtube", "tiktok"}
PREVIEW_BINARY_FIELDS = {"image_data", "instagram_image_data"}

# Utility
TRUTHY_VALUES = {"1", "true", "yes", "on"}

# Video defaults
VIDEO_DURATION_DEFAULT = 8
VIDEO_DURATION_MIN = 4
VIDEO_DURATION_MAX = 60

# Instagram story auto-publish configuration
STORY_AUTOPUBLISH_TIME = os.getenv("INSTAGRAM_STORY_AUTOPUBLISH_TIME", "11:45")
STORY_AUTOPUBLISH_COUNT = int(os.getenv("INSTAGRAM_STORY_AUTOPUBLISH_COUNT", "2"))
STORY_AUTOPUBLISH_ENABLED = os.getenv("INSTAGRAM_STORY_AUTOPUBLISH_ENABLED", "1") in TRUTHY_VALUES

# Instagram webhook & comment reply configuration
INSTAGRAM_WEBHOOK_VERIFY_TOKEN = os.getenv("INSTAGRAM_WEBHOOK_VERIFY_TOKEN", "")
INSTAGRAM_PUBLIC_COMMENT_REPLY_ENABLED = (
    os.getenv("INSTAGRAM_PUBLIC_COMMENT_REPLY_ENABLED", "1") in TRUTHY_VALUES
)
INSTAGRAM_COMMENT_REPLY_TRIGGER_TOKENS = [
    token.strip()
    for token in os.getenv("INSTAGRAM_COMMENT_REPLY_TRIGGER_VALUES", "buy").split(",")
    if token.strip()
]
INSTAGRAM_COMMENT_REPLY_TRIGGER_VALUES = {
    token.casefold()
    for token in INSTAGRAM_COMMENT_REPLY_TRIGGER_TOKENS
}
INSTAGRAM_COMMENT_REPLY_DISPLAY_TRIGGER = (
    INSTAGRAM_COMMENT_REPLY_TRIGGER_TOKENS[0]
    if INSTAGRAM_COMMENT_REPLY_TRIGGER_TOKENS
    else "buy"
)
INSTAGRAM_COMMENT_REPLY_ALLOWED_MEDIA_TYPES = {"FEED", "REELS"}

# Instagram story QR widget configuration
STORY_QR_WATERMARK = os.getenv("INSTAGRAM_STORY_QR_WATERMARK", "https://kaymio.com")
STORY_QR_SAFE_RIGHT_RATIO = float(os.getenv("INSTAGRAM_STORY_QR_SAFE_RIGHT_RATIO", "0.08"))
STORY_QR_SAFE_BOTTOM_RATIO = float(os.getenv("INSTAGRAM_STORY_QR_SAFE_BOTTOM_RATIO", "0.12"))
STORY_QR_MIN_SIZE_PX = int(os.getenv("INSTAGRAM_STORY_QR_MIN_SIZE_PX", "100"))
STORY_QR_SIZE_RATIO = float(os.getenv("INSTAGRAM_STORY_QR_SIZE_RATIO", "0.18"))


def get_storage_root(app_root_path: str) -> Path:
    """Return the template_images storage root."""
    return Path(app_root_path) / "template_images"


def get_state_dir(app_root_path: str) -> Path:
    """Return the data directory for JSON state files."""
    return Path(app_root_path) / "data"


def get_state_file_paths(app_root_path: str) -> dict:
    """Return paths to all JSON state files."""
    state_dir = get_state_dir(app_root_path)
    return {
        "app_state": state_dir / "app_state.json",
        "instagram_story_scheduler": state_dir / "instagram_story_scheduler.json",
        "instagram_media_reply_routes": state_dir / "instagram_story_routes.json",
        "instagram_comment_replies": state_dir / "instagram_comment_reply_state.json",
    }
