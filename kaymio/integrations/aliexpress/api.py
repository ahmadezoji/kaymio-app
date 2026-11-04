"""AliExpress Affiliate (Open Platform) API client.

Uses the signed ``/sync`` gateway (HMAC-SHA256). With the default "Standard API
for Publishers" permission group the useful calls are:

* ``aliexpress.affiliate.product.query``   – search by keywords/category, sortable by sales
* ``aliexpress.affiliate.productdetail.get`` – details for up to 50 product ids
* ``aliexpress.affiliate.link.generate``   – affiliate links (needs a tracking id)
* ``aliexpress.affiliate.category.get``    – category tree

``aliexpress.affiliate.hotproduct.query`` needs the "Advanced API" group; we
try it when ``ALIEXPRESS_USE_HOT_PRODUCTS=1`` and fall back to sorting
``product.query`` by recent sales volume, which is the same "hot" signal.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

import requests

logger = logging.getLogger(__name__)

GATEWAY_URL = "https://api-sg.aliexpress.com/sync"
SORT_BY_SALES = "LAST_VOLUME_DESC"
ITEM_ID_RE = re.compile(r"aliexpress\.[a-z.]+/item/(?:[^/]*/)?(\d{6,20})\.html", re.IGNORECASE)


class AliExpressApiError(RuntimeError):
    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(f"{code}: {message}" if code else message)
        self.code = code


@dataclass(frozen=True)
class AliExpressSettings:
    app_key: str
    app_secret: str
    tracking_id: str = ""
    currency: str = "USD"
    language: str = "EN"
    ship_to_country: str = "US"

    @classmethod
    def from_env(cls) -> "AliExpressSettings":
        return cls(
            app_key=os.getenv("ALIEXPRESS_APP_KEY", "").strip(),
            app_secret=os.getenv("ALIEXPRESS_APP_SECRET", "").strip(),
            tracking_id=os.getenv("ALIEXPRESS_TRACKING_ID", "").strip(),
            currency=os.getenv("ALIEXPRESS_TARGET_CURRENCY", "").strip() or "USD",
            language=os.getenv("ALIEXPRESS_TARGET_LANGUAGE", "").strip() or "EN",
            ship_to_country=os.getenv("ALIEXPRESS_SHIP_TO_COUNTRY", "").strip() or "US",
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.app_key and self.app_secret)


class AliExpressClient:
    def __init__(self, settings: AliExpressSettings, *, timeout: float = 30.0) -> None:
        self.settings = settings
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "AliExpressClient":
        return cls(AliExpressSettings.from_env())

    # ------------------------------------------------------------------ #
    # Low level
    # ------------------------------------------------------------------ #
    def sign(self, params: Dict[str, str]) -> str:
        payload = "".join(f"{key}{params[key]}" for key in sorted(params))
        return hmac.new(
            self.settings.app_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest().upper()

    def call(self, method: str, **business_params: Any) -> Dict[str, Any]:
        """Call ``method`` and return its ``resp_result.result`` object."""
        if not self.settings.is_configured:
            raise AliExpressApiError("ALIEXPRESS_APP_KEY / ALIEXPRESS_APP_SECRET are not set.")
        params = {
            "app_key": self.settings.app_key,
            "method": method,
            "timestamp": str(int(time.time() * 1000)),
            "sign_method": "sha256",
            "format": "json",
            "v": "2.0",
        }
        params.update({key: str(value) for key, value in business_params.items() if value not in (None, "")})
        params["sign"] = self.sign(params)

        response = requests.post(GATEWAY_URL, data=params, timeout=self.timeout)
        try:
            body = response.json()
        except ValueError:
            raise AliExpressApiError(f"HTTP {response.status_code}: {response.text[:300]}") from None

        if "error_response" in body:
            error = body["error_response"]
            raise AliExpressApiError(str(error.get("msg") or error), str(error.get("code") or ""))

        envelope = body.get(method.replace(".", "_") + "_response") or {}
        resp_result = envelope.get("resp_result") or {}
        code = resp_result.get("resp_code")
        if code not in (None, 200, "200"):
            raise AliExpressApiError(str(resp_result.get("resp_msg") or "request failed"), str(code))
        return resp_result.get("result") or {}

    def _locale_params(self) -> Dict[str, str]:
        return {
            "target_currency": self.settings.currency,
            "target_language": self.settings.language,
            "tracking_id": self.settings.tracking_id,
        }

    # ------------------------------------------------------------------ #
    # Business calls
    # ------------------------------------------------------------------ #
    def query_products(
        self,
        *,
        keywords: str = "",
        category_ids: str = "",
        sort: str = SORT_BY_SALES,
        page_no: int = 1,
        page_size: int = 50,
        min_sale_price: Optional[float] = None,
        max_sale_price: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        result = self.call(
            "aliexpress.affiliate.product.query",
            keywords=keywords,
            category_ids=category_ids,
            sort=sort,
            page_no=page_no,
            page_size=page_size,
            min_sale_price=_cents(min_sale_price),
            max_sale_price=_cents(max_sale_price),
            ship_to_country=self.settings.ship_to_country,
            **self._locale_params(),
        )
        return _products(result)

    def query_hot_products(
        self,
        *,
        keywords: str = "",
        category_ids: str = "",
        page_size: int = 50,
        max_sale_price: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Requires the Advanced API permission group."""
        result = self.call(
            "aliexpress.affiliate.hotproduct.query",
            keywords=keywords,
            category_ids=category_ids,
            sort=SORT_BY_SALES,
            page_size=page_size,
            max_sale_price=_cents(max_sale_price),
            ship_to_country=self.settings.ship_to_country,
            **self._locale_params(),
        )
        return _products(result)

    def product_details(self, product_ids: Iterable[str]) -> List[Dict[str, Any]]:
        result = self.call(
            "aliexpress.affiliate.productdetail.get",
            product_ids=",".join(str(pid) for pid in product_ids),
            country=self.settings.ship_to_country,
            **self._locale_params(),
        )
        return _products(result)

    def generate_affiliate_link(self, url: str) -> str:
        if not self.settings.tracking_id:
            raise AliExpressApiError("ALIEXPRESS_TRACKING_ID is required to generate affiliate links.")
        result = self.call(
            "aliexpress.affiliate.link.generate",
            promotion_link_type=0,
            source_values=url,
            tracking_id=self.settings.tracking_id,
        )
        links = ((result.get("promotion_links") or {}).get("promotion_link")) or []
        link = links[0].get("promotion_link") if links else ""
        if not link:
            raise AliExpressApiError(f"No affiliate link returned for {url}")
        return str(link)

    def categories(self) -> List[Dict[str, Any]]:
        result = self.call("aliexpress.affiliate.category.get")
        return list(((result.get("categories") or {}).get("category")) or [])


def _cents(price: Optional[float]) -> Optional[int]:
    """The API's price filters are in cents of the target currency."""
    return int(round(price * 100)) if price else None


def _products(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(((result.get("products") or {}).get("product")) or [])


def extract_item_ids(text: str) -> List[str]:
    """AliExpress product ids found in ``.../item/<id>.html`` URLs."""
    return ITEM_ID_RE.findall(text or "")


def get_aliexpress_client() -> AliExpressClient:
    return AliExpressClient.from_env()
