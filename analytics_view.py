"""Analytics view model + routes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from flask import Blueprint, render_template

from analytics_providers import fetch_instagram_analytics, fetch_pinterest_analytics, fetch_youtube_analytics


@dataclass
class Metric:
    label: str
    value: str
    delta: str | None = None
    trend: str | None = None


@dataclass
class PlatformAnalytics:
    name: str
    subtitle: str
    accent: str
    metrics: List[Metric]
    sparkline: str
    period: str


analytics_bp = Blueprint("analytics", __name__)


def _sparkline_points(values: List[float], width: int = 240, height: int = 80, pad: int = 6) -> str:
    if not values:
        return ""
    min_val = min(values)
    max_val = max(values)
    span = max(max_val - min_val, 1e-6)
    step = (width - 2 * pad) / max(len(values) - 1, 1)
    points = []
    for idx, val in enumerate(values):
        x = pad + idx * step
        y = height - pad - ((val - min_val) / span) * (height - 2 * pad)
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


def _build_metric_list(raw_metrics: List[Dict]) -> List[Metric]:
    metrics = []
    for item in raw_metrics:
        value = item.get("value")
        if value is None or value == "":
            continue
        metrics.append(
            Metric(
                label=str(item.get("label", "")),
                value=str(value),
                delta=item.get("delta"),
                trend=item.get("trend"),
            )
        )
    return metrics


def _build_platform_cards(raw: Dict) -> List[PlatformAnalytics]:
    platforms = []
    for platform in raw.get("platforms", []):
        trend = platform.get("trend", [])
        sparkline = _sparkline_points(trend)
        platforms.append(
            PlatformAnalytics(
                name=platform.get("name", ""),
                subtitle=platform.get("subtitle", ""),
                accent=platform.get("accent", "#4f46e5"),
                metrics=_build_metric_list(platform.get("metrics", [])),
                sparkline=sparkline,
                period=platform.get("period", "Last 30 days"),
            )
        )
    return platforms


def build_analytics_view_model() -> Dict:
    platforms_raw = []
    errors = []

    pinterest = fetch_pinterest_analytics()
    if pinterest.get("error"):
        errors.append(f"Pinterest: {pinterest['error']}")
    else:
        platforms_raw.append({
            "name": "Pinterest",
            "subtitle": "Account overview",
            "accent": "#e11d48",
            "period": pinterest.get("period", "Last 30 days"),
            "metrics": [
                {"label": k, "value": v} for k, v in (pinterest.get("metrics") or {}).items()
            ],
            "trend": pinterest.get("trend", []),
        })

    instagram = fetch_instagram_analytics()
    if instagram.get("error"):
        errors.append(f"Instagram: {instagram['error']}")
    else:
        platforms_raw.append({
            "name": "Instagram",
            "subtitle": "Latest post insights",
            "accent": "#f97316",
            "period": instagram.get("period", "Latest post"),
            "metrics": [
                {"label": k, "value": v} for k, v in (instagram.get("metrics") or {}).items()
            ],
            "trend": instagram.get("trend", []),
        })

    # youtube = fetch_youtube_analytics()
    # if youtube.get("error"):
    #     errors.append(f"YouTube: {youtube['error']}")
    # else:
    #     platforms_raw.append({
    #         "name": "YouTube",
    #         "subtitle": "Channel overview",
    #         "accent": "#ef4444",
    #         "period": youtube.get("period", "Last 28 days"),
    #         "metrics": [
    #             {"label": k, "value": v} for k, v in (youtube.get("metrics") or {}).items()
    #         ],
    #         "trend": youtube.get("trend", []),
    #     })

    platforms = _build_platform_cards({"platforms": platforms_raw})
    headline = "Live analytics snapshot"
    updated_at = "Pulled from platform APIs"
    return {
        "headline": headline,
        "updated_at": updated_at,
        "platforms": platforms,
        "errors": errors,
    }


@analytics_bp.route("/analytics", methods=["GET"])
def analytics_view():
    context = build_analytics_view_model()
    return render_template("analytics.html", **context)
