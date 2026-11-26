"""The promo event as used by discovery, copywriting and publishing."""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

DEFAULT_CAPTION_LINE = "🔥 {event} deal: -{discount}%"


def split_lines(raw: str) -> List[str]:
    return [line.strip() for line in re.split(r"[\n]+", raw or "") if line.strip()]


@dataclass(frozen=True)
class PromoEvent:
    id: Optional[int]
    name: str
    start_date: dt.date
    end_date: dt.date
    amazon_lists: List[str]
    aliexpress_queries: List[str]
    hashtags: List[str]
    caption_line: str
    website_category: str
    min_discount_percent: int

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PromoEvent":
        return cls(
            id=data.get("id"),
            name=str(data.get("name") or "").strip(),
            start_date=dt.date.fromisoformat(str(data["start_date"])),
            end_date=dt.date.fromisoformat(str(data["end_date"])),
            amazon_lists=split_lines(data.get("amazon_lists") or ""),
            aliexpress_queries=split_lines(data.get("aliexpress_queries") or ""),
            hashtags=[tag.lstrip("#") for tag in re.split(r"[\s,]+", data.get("hashtags") or "") if tag.strip("#")],
            caption_line=str(data.get("caption_line") or "").strip() or DEFAULT_CAPTION_LINE,
            website_category=str(data.get("website_category") or "").strip(),
            min_discount_percent=int(data.get("min_discount_percent") or 0),
        )

    def caption_for(self, discount_percent: Optional[int]) -> str:
        """The event line for a caption; the discount part is dropped when it's unknown."""
        if discount_percent:
            return self.caption_line.format(event=self.name, discount=discount_percent)
        # Unknown discount: keep only the part before the discount, e.g. "🖤 Black Friday deal".
        head = self.caption_line.split("{discount}", 1)[0].rstrip(" :-−–")
        return head.format(event=self.name).strip()
