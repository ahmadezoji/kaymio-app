"""Ready-made events whose dates are computed for a given year.

Add a new preset here (name, date rule, defaults) and it appears on the
Automation page; nothing else needs to change.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple


def _thanksgiving(year: int) -> dt.date:
    """Fourth Thursday of November (US)."""
    first = dt.date(year, 11, 1)
    first_thursday = first + dt.timedelta(days=(3 - first.weekday()) % 7)
    return first_thursday + dt.timedelta(weeks=3)


def _black_friday(year: int) -> Tuple[dt.date, dt.date]:
    friday = _thanksgiving(year) + dt.timedelta(days=1)
    return friday - dt.timedelta(days=1), friday + dt.timedelta(days=2)  # Thu .. Sun


def _cyber_monday(year: int) -> Tuple[dt.date, dt.date]:
    monday = _thanksgiving(year) + dt.timedelta(days=4)
    return monday, monday + dt.timedelta(days=1)  # Mon .. Tue


def _christmas(year: int) -> Tuple[dt.date, dt.date]:
    return dt.date(year, 12, 1), dt.date(year, 12, 24)


@dataclass(frozen=True)
class EventPreset:
    key: str
    name: str
    dates: Callable[[int], Tuple[dt.date, dt.date]]
    amazon_lists: str
    aliexpress_queries: str
    hashtags: str
    caption_line: str
    website_category: str
    min_discount_percent: int = 0

    def build(self, year: int) -> Dict[str, object]:
        start, end = self.dates(year)
        return {
            "name": self.name,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "enabled": True,
            "amazon_lists": self.amazon_lists,
            "aliexpress_queries": self.aliexpress_queries,
            "hashtags": self.hashtags.format(year=year),
            "caption_line": self.caption_line,
            "website_category": self.website_category,
            "min_discount_percent": self.min_discount_percent,
            "source": "preset",
        }


PRESETS: List[EventPreset] = [
    EventPreset(
        key="black_friday",
        name="Black Friday",
        dates=_black_friday,
        amazon_lists="https://www.amazon.com/gp/goldbox",
        aliexpress_queries="black friday\nblack friday gadgets\ngift ideas",
        hashtags="BlackFriday, BlackFridayDeals, BlackFriday{year}, DealOfTheDay",
        caption_line="🖤 Black Friday deal: -{discount}% off",
        website_category="Black Friday Deals",
        min_discount_percent=10,
    ),
    EventPreset(
        key="cyber_monday",
        name="Cyber Monday",
        dates=_cyber_monday,
        amazon_lists="https://www.amazon.com/gp/goldbox",
        aliexpress_queries="cyber monday\ntech gadgets\nsmart home gadgets",
        hashtags="CyberMonday, CyberMondayDeals, TechDeals",
        caption_line="💻 Cyber Monday deal: -{discount}% off",
        website_category="Cyber Monday Deals",
        min_discount_percent=10,
    ),
    EventPreset(
        key="christmas",
        name="Christmas",
        dates=_christmas,
        amazon_lists="https://www.amazon.com/gp/goldbox",
        aliexpress_queries="christmas gifts\nchristmas decorations\nstocking stuffers",
        hashtags="ChristmasGifts, GiftIdeas, HolidayDeals, Christmas",
        caption_line="🎄 Christmas gift idea: -{discount}% off",
        website_category="Christmas Gifts",
    ),
]


def list_presets(year: int) -> List[Dict[str, object]]:
    return [{"key": preset.key, **preset.build(year)} for preset in PRESETS]


def build_preset(key: str, year: int) -> Dict[str, object]:
    for preset in PRESETS:
        if preset.key == key:
            return preset.build(year)
    raise KeyError(f"Unknown event preset: {key}")
