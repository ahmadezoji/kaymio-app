"""Story QR overlay widget for affiliate links."""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import random
from urllib.parse import quote_plus

import requests
from PIL import Image, ImageDraw, ImageFont
from openai_helper import generate_story_cta_text


@dataclass(frozen=True)
class StoryQrWidgetConfig:
    watermark_text: str = "https://kaymio.com"
    safe_right_ratio: float = 0.08
    safe_bottom_ratio: float = 0.12
    min_qr_size_px: int = 100
    qr_size_ratio: float = 0.18
    request_timeout_seconds: int = 30


@dataclass(frozen=True)
class StoryCtaWidgetConfig:
    safe_bottom_ratio: float = 0.12
    max_width_ratio: float = 0.78
    min_height_ratio: float = 0.12
    max_height_ratio: float = 0.22
    title_font_ratio: float = 0.22
    body_font_ratio: float = 0.12
    combined_target_width_px: int = 840
    combined_target_height_px: int = 210
    vertical_center_ratio: float = 0.68
    request_timeout_seconds: int = 30


@dataclass(frozen=True)
class StoryPanelTheme:
    panel_fill: tuple[int, int, int, int]
    panel_outline: tuple[int, int, int, int]
    highlight_fill: tuple[int, int, int, int]
    shadow_fill: tuple[int, int, int, int]
    qr_box_fill: tuple[int, int, int, int] = (255, 255, 255, 236)
    title_color: tuple[int, int, int, int] = (255, 255, 255, 255)
    body_color: tuple[int, int, int, int] = (238, 241, 247, 236)


_STORY_PANEL_THEMES: tuple[StoryPanelTheme, ...] = (
    StoryPanelTheme(
        panel_fill=(20, 42, 102, 214),
        panel_outline=(190, 216, 255, 70),
        highlight_fill=(94, 177, 255, 82),
        shadow_fill=(10, 19, 52, 122),
    ),
    StoryPanelTheme(
        panel_fill=(129, 48, 73, 214),
        panel_outline=(255, 212, 221, 72),
        highlight_fill=(255, 158, 165, 82),
        shadow_fill=(78, 17, 45, 124),
    ),
    StoryPanelTheme(
        panel_fill=(16, 96, 99, 214),
        panel_outline=(184, 243, 236, 72),
        highlight_fill=(111, 222, 204, 78),
        shadow_fill=(8, 58, 62, 122),
    ),
    StoryPanelTheme(
        panel_fill=(122, 69, 17, 214),
        panel_outline=(255, 224, 174, 72),
        highlight_fill=(255, 180, 85, 84),
        shadow_fill=(73, 36, 7, 122),
    ),
    StoryPanelTheme(
        panel_fill=(85, 40, 118, 214),
        panel_outline=(226, 203, 255, 72),
        highlight_fill=(181, 138, 255, 82),
        shadow_fill=(49, 18, 72, 122),
    ),
    StoryPanelTheme(
        panel_fill=(32, 91, 55, 214),
        panel_outline=(202, 240, 213, 72),
        highlight_fill=(127, 214, 157, 80),
        shadow_fill=(15, 55, 31, 122),
    ),
)


def _download_image_bytes(image_url: str, timeout_seconds: int) -> bytes:
    local_candidate = Path(image_url)
    if local_candidate.exists() and local_candidate.is_file():
        return local_candidate.read_bytes()
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


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = [word for word in text.split() if word]
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if draw.textbbox((0, 0), trial, font=font)[2] <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _truncate_lines(lines: list[str], limit: int) -> list[str]:
    if len(lines) <= limit:
        return lines
    kept = lines[:limit]
    kept[-1] = kept[-1].rstrip(" .,!?:;") + "..."
    return kept


def _load_font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    font_candidates = ["Arial Bold.ttf", "DejaVuSans-Bold.ttf"] if bold else ["Arial.ttf", "DejaVuSans.ttf"]
    for font_name in font_candidates:
        try:
            return ImageFont.truetype(font_name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _pick_story_panel_theme() -> StoryPanelTheme:
    return random.choice(_STORY_PANEL_THEMES)


def _build_panel_background(
    width: int,
    height: int,
    *,
    corner_radius: int,
    outline_width: int,
    theme: StoryPanelTheme,
) -> Image.Image:
    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    mask = Image.new("L", (width, height), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(
        (0, 0, width - 1, height - 1),
        radius=corner_radius,
        fill=255,
    )

    background = Image.new("RGBA", (width, height), theme.panel_fill)
    accent_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    accent_draw = ImageDraw.Draw(accent_layer)
    accent_draw.ellipse(
        (
            int(width * 0.52),
            -int(height * 0.34),
            width + int(width * 0.18),
            int(height * 0.62),
        ),
        fill=theme.highlight_fill,
    )
    accent_draw.ellipse(
        (
            -int(width * 0.24),
            int(height * 0.34),
            int(width * 0.42),
            height + int(height * 0.42),
        ),
        fill=theme.shadow_fill,
    )
    background = Image.alpha_composite(background, accent_layer)
    panel.paste(background, (0, 0), mask)

    panel_draw = ImageDraw.Draw(panel)
    panel_draw.rounded_rectangle(
        (0, 0, width - 1, height - 1),
        radius=corner_radius,
        outline=theme.panel_outline,
        width=outline_width,
    )
    return panel


def _build_qr_panel(
    width: int,
    height: int,
    affiliate_link: str,
    cfg: StoryQrWidgetConfig,
    *,
    size_ratio: float | None = None,
) -> tuple[Image.Image, int, int]:
    margin = max(16, int(min(width, height) * 0.025))
    qr_ratio = size_ratio if size_ratio is not None else cfg.qr_size_ratio
    panel_cfg = StoryQrWidgetConfig(
        watermark_text=cfg.watermark_text,
        safe_right_ratio=cfg.safe_right_ratio,
        safe_bottom_ratio=cfg.safe_bottom_ratio,
        min_qr_size_px=max(72, min(cfg.min_qr_size_px, 90)),
        qr_size_ratio=qr_ratio,
        request_timeout_seconds=cfg.request_timeout_seconds,
    )
    qr_size, _, panel_padding, watermark_height, panel_w, panel_h = _build_panel_dimensions(
        width,
        height,
        panel_cfg,
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
    font = _load_font(max(14, int(watermark_height * 0.55)))
    text_bbox = panel_draw.textbbox((0, 0), cfg.watermark_text, font=font)
    text_w = max(1, text_bbox[2] - text_bbox[0])
    text_h = max(1, text_bbox[3] - text_bbox[1])
    text_x = max(0, (panel_w - text_w) // 2)
    text_y = qr_y + qr_size + max(2, (watermark_height - text_h) // 2)
    panel_draw.text((text_x, text_y), cfg.watermark_text, fill=(33, 33, 33, 255), font=font)
    return panel, panel_w, panel_h


def _build_cta_panel(
    width: int,
    height: int,
    product_title: str,
    caption: str,
    description: str,
    cfg: StoryCtaWidgetConfig,
) -> tuple[Image.Image, int, int]:
    cta_copy = generate_story_cta_text(
        product_title=product_title,
        caption=caption,
        description=description,
    )
    margin = max(16, int(min(width, height) * 0.025))
    panel_w = width - (2 * margin)
    panel_h = min(max(int(height * cfg.min_height_ratio), 120), int(height * cfg.max_height_ratio))
    corner_radius = max(18, int(panel_h * 0.2))
    theme = _pick_story_panel_theme()
    panel = _build_panel_background(
        panel_w,
        panel_h,
        corner_radius=corner_radius,
        outline_width=max(1, int(panel_h * 0.01)),
        theme=theme,
    )
    panel_draw = ImageDraw.Draw(panel)
    inner_padding_x = max(18, int(panel_w * 0.06))
    inner_padding_y = max(14, int(panel_h * 0.12))
    text_width = panel_w - (2 * inner_padding_x)
    title_font = _load_font(max(26, int(panel_h * cfg.title_font_ratio)), bold=True)
    body_font = _load_font(max(18, int(panel_h * cfg.body_font_ratio)))
    title_text = (cta_copy.get("headline") or "Discover this product").strip()
    body_text = (cta_copy.get("body") or description or "").strip()
    title_lines = _truncate_lines(_fit_text(panel_draw, title_text, title_font, text_width), 2) or [
        "Discover this product"
    ]
    body_lines = _truncate_lines(_fit_text(panel_draw, body_text, body_font, text_width), 2)

    current_y = inner_padding_y
    for line in title_lines:
        panel_draw.text((inner_padding_x, current_y), line, fill=theme.title_color, font=title_font)
        bbox = panel_draw.textbbox((0, 0), line, font=title_font)
        current_y += max(18, bbox[3] - bbox[1] + 4)
    if body_lines:
        current_y += max(6, int(panel_h * 0.07))
    for line in body_lines:
        panel_draw.text((inner_padding_x, current_y), line, fill=theme.body_color, font=body_font)
        bbox = panel_draw.textbbox((0, 0), line, font=body_font)
        current_y += max(14, bbox[3] - bbox[1] + 3)
    return panel, panel_w, panel_h


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
        margin = max(16, int(min(width, height) * 0.025))
        panel, panel_w, panel_h = _build_qr_panel(width, height, affiliate_link, cfg)
        safe_bottom = max(margin, int(height * cfg.safe_bottom_ratio))
        safe_side = max(margin, int(width * cfg.safe_right_ratio))
        centered_x = (width - panel_w) // 2
        pos_x = min(max(safe_side, centered_x), max(safe_side, width - panel_w - safe_side))
        pos_y = min(max(margin, height - panel_h - safe_bottom), max(margin, height - panel_h - margin))
        base.paste(panel, (pos_x, pos_y), panel)

        output = BytesIO()
        base.convert("RGB").save(output, format="JPEG", quality=94)
        return output.getvalue()


def compose_story_image_with_cta(
    *,
    source_image_url: str,
    product_title: str = "",
    caption: str = "",
    description: str,
    config: StoryCtaWidgetConfig | None = None,
) -> bytes:
    cfg = config or StoryCtaWidgetConfig()
    source_bytes = _download_image_bytes(source_image_url, cfg.request_timeout_seconds)
    with Image.open(BytesIO(source_bytes)).convert("RGBA") as base:
        width, height = base.size
        margin = max(16, int(min(width, height) * 0.025))
        safe_bottom = max(margin, int(height * cfg.safe_bottom_ratio))
        panel, panel_w, panel_h = _build_cta_panel(width, height, product_title, caption, description, cfg)
        panel_x = max(margin, (width - panel_w) // 2)
        bottom_y = height - panel_h - safe_bottom
        target_center_y = int(height * cfg.vertical_center_ratio)
        target_y = target_center_y - (panel_h // 2)
        panel_y = max(margin, min(bottom_y, target_y))
        base.paste(panel, (panel_x, panel_y), panel)
        output = BytesIO()
        base.convert("RGB").save(output, format="JPEG", quality=94)
        return output.getvalue()


def compose_story_image_with_cta_and_affiliate_qr(
    *,
    source_image_url: str,
    affiliate_link: str,
    product_title: str = "",
    caption: str = "",
    description: str,
    qr_config: StoryQrWidgetConfig | None = None,
    cta_config: StoryCtaWidgetConfig | None = None,
) -> bytes:
    qr_cfg = qr_config or StoryQrWidgetConfig()
    cta_cfg = cta_config or StoryCtaWidgetConfig()
    source_bytes = _download_image_bytes(source_image_url, qr_cfg.request_timeout_seconds)
    with Image.open(BytesIO(source_bytes)).convert("RGBA") as base:
        width, height = base.size
        margin = max(16, int(min(width, height) * 0.025))
        safe_side = max(margin, int(width * 0.055))
        safe_width = max(180, width - (2 * safe_side))
        cta_copy = generate_story_cta_text(
            product_title=product_title,
            caption=caption,
            description=description,
        )
        panel_w = min(
            max(220, cta_cfg.combined_target_width_px),
            width - (2 * safe_side),
        )
        panel_h = min(
            max(120, cta_cfg.combined_target_height_px),
            height - (2 * margin),
        )
        panel_x = safe_side + max(0, (safe_width - panel_w) // 2)
        panel_y = max(margin, height - panel_h - margin)

        corner_radius = max(20, int(panel_h * 0.18))
        theme = _pick_story_panel_theme()
        panel = _build_panel_background(
            panel_w,
            panel_h,
            corner_radius=corner_radius,
            outline_width=max(1, int(panel_h * 0.01)),
            theme=theme,
        )
        panel_draw = ImageDraw.Draw(panel)

        inner_padding_x = max(18, int(panel_w * 0.04))
        inner_padding_y = max(16, int(panel_h * 0.12))
        qr_size = min(int(panel_h * 0.5), int(panel_w * 0.18))
        qr_size = max(72, qr_size)
        qr_box_size = min(
            qr_size + max(18, int(qr_size * 0.2)),
            int(panel_w * 0.24),
        )
        qr_box_x = panel_w - inner_padding_x - qr_box_size
        qr_box_y = max(12, (panel_h - qr_box_size) // 2)

        panel_draw.rounded_rectangle(
            (qr_box_x, qr_box_y, qr_box_x + qr_box_size, qr_box_y + qr_box_size),
            radius=max(16, int(qr_box_size * 0.14)),
            fill=theme.qr_box_fill,
        )
        qr_image = _build_qr_image(affiliate_link, qr_size, qr_cfg.request_timeout_seconds)
        qr_x = qr_box_x + (qr_box_size - qr_size) // 2
        qr_y = qr_box_y + max(8, int(qr_box_size * 0.08))
        panel.paste(qr_image, (qr_x, qr_y), qr_image)
        watermark_font = _load_font(max(12, int(panel_h * 0.08)))
        watermark_text = qr_cfg.watermark_text
        watermark_bbox = panel_draw.textbbox((0, 0), watermark_text, font=watermark_font)
        watermark_w = max(1, watermark_bbox[2] - watermark_bbox[0])
        watermark_x = qr_box_x + max(0, (qr_box_size - watermark_w) // 2)
        watermark_y = qr_y + qr_size + max(4, int(panel_h * 0.02))
        # panel_draw.text(
        #     (watermark_x, watermark_y),
        #     watermark_text,
        #     fill=(44, 44, 44, 255),
        #     font=watermark_font,
        # )

        text_column_x = inner_padding_x
        text_column_w = max(110, qr_box_x - text_column_x - max(12, int(panel_w * 0.03)))
        title_font = _load_font(max(26, int(panel_h * cta_cfg.title_font_ratio)), bold=True)
        body_font = _load_font(max(18, int(panel_h * cta_cfg.body_font_ratio)))
        title_text = (cta_copy.get("headline") or "Discover this product").strip()
        body_text = (cta_copy.get("body") or description or "").strip()
        title_lines = _truncate_lines(_fit_text(panel_draw, title_text, title_font, text_column_w), 2) or [
            "Discover this product"
        ]
        body_lines = _truncate_lines(_fit_text(panel_draw, body_text, body_font, text_column_w), 2)

        current_y = inner_padding_y
        for line in title_lines:
            panel_draw.text((text_column_x, current_y), line, fill=theme.title_color, font=title_font)
            bbox = panel_draw.textbbox((0, 0), line, font=title_font)
            current_y += max(18, bbox[3] - bbox[1] + 5)
        if body_lines:
            current_y += max(6, int(panel_h * 0.04))
        for line in body_lines:
            panel_draw.text((text_column_x, current_y), line, fill=theme.body_color, font=body_font)
            bbox = panel_draw.textbbox((0, 0), line, font=body_font)
            current_y += max(14, bbox[3] - bbox[1] + 3)

        base.paste(panel, (panel_x, panel_y), panel)
        output = BytesIO()
        base.convert("RGB").save(output, format="JPEG", quality=94)
        return output.getvalue()
