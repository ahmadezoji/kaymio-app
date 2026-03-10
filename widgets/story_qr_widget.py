"""Story QR overlay widget for affiliate links."""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from urllib.parse import quote_plus

import requests
from PIL import Image, ImageDraw, ImageFont


@dataclass(frozen=True)
class StoryQrWidgetConfig:
    watermark_text: str = "https://kaymio.com"
    safe_right_ratio: float = 0.08
    safe_bottom_ratio: float = 0.12
    min_qr_size_px: int = 100
    qr_size_ratio: float = 0.18
    request_timeout_seconds: int = 30


def _download_image_bytes(image_url: str, timeout_seconds: int) -> bytes:
    response = requests.get(image_url, timeout=timeout_seconds)
    response.raise_for_status()
    return response.content


def _build_qr_image(affiliate_link: str, size: int, timeout_seconds: int) -> Image.Image:
    qr_url = (
        "https://api.qrserver.com/v1/create-qr-code/"
        f"?size={size}x{size}&margin=0&data={quote_plus(affiliate_link)}"
    )
    qr_bytes = _download_image_bytes(qr_url, timeout_seconds)
    qr_image = Image.open(BytesIO(qr_bytes)).convert("RGBA")
    return qr_image.resize((size, size), Image.LANCZOS)


def _build_panel_dimensions(
    width: int,
    height: int,
    cfg: StoryQrWidgetConfig,
) -> tuple[int, int, int, int, int, int]:
    margin = max(16, int(min(width, height) * 0.025))
    max_panel_w = max(120, width - (2 * margin))
    max_panel_h = max(120, height - (2 * margin))
    qr_size = max(cfg.min_qr_size_px, int(min(width, height) * cfg.qr_size_ratio))

    while True:
        panel_padding = max(10, int(qr_size * 0.09))
        watermark_height = max(20, int(qr_size * 0.18))
        panel_w = qr_size + (2 * panel_padding)
        panel_h = qr_size + watermark_height + (2 * panel_padding)
        if panel_w <= max_panel_w and panel_h <= max_panel_h:
            return qr_size, margin, panel_padding, watermark_height, panel_w, panel_h
        if qr_size <= 64:
            return qr_size, margin, panel_padding, watermark_height, panel_w, panel_h
        qr_size = max(64, int(qr_size * 0.92))


def compose_story_image_with_affiliate_qr(
    *,
    source_image_url: str,
    affiliate_link: str,
    config: StoryQrWidgetConfig | None = None,
) -> bytes:
    cfg = config or StoryQrWidgetConfig()
    source_bytes = _download_image_bytes(source_image_url, cfg.request_timeout_seconds)
    with Image.open(BytesIO(source_bytes)).convert("RGBA") as base:
        width, height = base.size
        qr_size, margin, panel_padding, watermark_height, panel_w, panel_h = _build_panel_dimensions(
            width,
            height,
            cfg,
        )

        qr_image = _build_qr_image(affiliate_link, qr_size, cfg.request_timeout_seconds)

        panel = Image.new("RGBA", (panel_w, panel_h), (0, 0, 0, 0))
        panel_draw = ImageDraw.Draw(panel)
        corner_radius = max(12, int(panel_w * 0.09))
        panel_draw.rounded_rectangle(
            (0, 0, panel_w - 1, panel_h - 1),
            radius=corner_radius,
            fill=(255, 255, 255, 228),
        )
        qr_x = panel_padding
        qr_y = panel_padding
        panel.paste(qr_image, (qr_x, qr_y), qr_image)

        try:
            font_size = max(14, int(watermark_height * 0.55))
            font = ImageFont.truetype("Arial.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()

        text_bbox = panel_draw.textbbox((0, 0), cfg.watermark_text, font=font)
        text_w = max(1, text_bbox[2] - text_bbox[0])
        text_h = max(1, text_bbox[3] - text_bbox[1])
        text_x = max(0, (panel_w - text_w) // 2)
        text_y = qr_y + qr_size + max(2, (watermark_height - text_h) // 2)
        panel_draw.text((text_x, text_y), cfg.watermark_text, fill=(33, 33, 33, 255), font=font)

        safe_bottom = max(margin, int(height * cfg.safe_bottom_ratio))
        safe_side = max(margin, int(width * cfg.safe_right_ratio))
        centered_x = (width - panel_w) // 2
        pos_x = min(max(safe_side, centered_x), max(safe_side, width - panel_w - safe_side))
        pos_y = min(max(margin, height - panel_h - safe_bottom), max(margin, height - panel_h - margin))
        base.paste(panel, (pos_x, pos_y), panel)

        output = BytesIO()
        base.convert("RGB").save(output, format="JPEG", quality=94)
        return output.getvalue()
