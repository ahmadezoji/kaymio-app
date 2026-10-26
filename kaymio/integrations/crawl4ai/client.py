"""HTTP client for the self-hosted Crawl4AI service.

Crawl4AI runs as its own container (``crawl4ai``) on the shared Docker network,
so Kaymio reaches it at ``http://crawl4ai:11235`` with a bearer token. The token
stays server-side; nothing here is ever exposed to the browser.

    client = get_crawl4ai_client()
    page = client.crawl_one("https://example.com")
    page.html, page.markdown
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence

import requests

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class Crawl4AIError(RuntimeError):
    """Raised when the Crawl4AI service is unreachable, rejects a request, or a crawl fails."""


@dataclass(frozen=True)
class CrawlResult:
    url: str
    success: bool
    status_code: Optional[int]
    html: str
    markdown: str
    error: str

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "CrawlResult":
        return cls(
            url=str(payload.get("url") or ""),
            success=bool(payload.get("success", True)),
            status_code=payload.get("status_code"),
            html=str(payload.get("html") or payload.get("cleaned_html") or ""),
            markdown=_extract_markdown(payload.get("markdown")),
            error=str(payload.get("error_message") or ""),
        )


def _extract_markdown(raw: Any) -> str:
    """``markdown`` is an object in recent Crawl4AI versions and a plain string in older ones."""
    if isinstance(raw, dict):
        return str(raw.get("raw_markdown") or raw.get("fit_markdown") or "")
    return str(raw or "")


class Crawl4AIClient:
    def __init__(
        self,
        base_url: str,
        api_token: str,
        *,
        timeout_seconds: float = 120.0,
        max_retries: int = 2,
        bypass_cache: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)
        self.bypass_cache = bypass_cache
        self._session = requests.Session()

    @classmethod
    def from_env(cls) -> "Crawl4AIClient":
        return cls(
            base_url=os.getenv("CRAWL4AI_BASE_URL", "http://crawl4ai:11235"),
            api_token=os.getenv("CRAWL4AI_API_TOKEN", ""),
            timeout_seconds=float(os.getenv("CRAWL4AI_TIMEOUT_SECONDS", "120")),
            max_retries=int(os.getenv("CRAWL4AI_MAX_RETRIES", "2")),
            bypass_cache=os.getenv("CRAWL4AI_BYPASS_CACHE", "1").lower() in {"1", "true", "yes", "on"},
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.api_token)

    def health(self) -> Dict[str, Any]:
        """Return the service's ``/health`` payload; raises Crawl4AIError when it is down."""
        response = self._request("GET", "/health", timeout=10, retries=0)
        try:
            return response.json()
        except ValueError:
            return {"status": "ok", "raw": response.text[:200]}

    def crawl(
        self,
        urls: Sequence[str],
        *,
        crawler_params: Optional[Dict[str, Any]] = None,
        browser_params: Optional[Dict[str, Any]] = None,
    ) -> List[CrawlResult]:
        """Crawl one or more URLs and return one result per URL (failed pages included)."""
        if not urls:
            return []
        body: Dict[str, Any] = {"urls": list(urls)}
        run_params = dict(crawler_params or {})
        if self.bypass_cache:
            run_params.setdefault("cache_mode", "bypass")
        if run_params:
            body["crawler_config"] = {"type": "CrawlerRunConfig", "params": run_params}
        if browser_params:
            body["browser_config"] = {"type": "BrowserConfig", "params": browser_params}

        response = self._request("POST", "/crawl", json=body)
        try:
            payload = response.json()
        except ValueError as exc:
            raise Crawl4AIError(f"Crawl4AI returned non-JSON response: {response.text[:200]}") from exc

        if not isinstance(payload, dict) or not payload.get("success", False):
            raise Crawl4AIError(f"Crawl4AI crawl failed: {str(payload)[:300]}")
        results = payload.get("results") or []
        return [CrawlResult.from_payload(item) for item in results if isinstance(item, dict)]

    def crawl_one(self, url: str, **kwargs: Any) -> CrawlResult:
        """Crawl a single URL and raise Crawl4AIError unless the page was fetched successfully."""
        results = self.crawl([url], **kwargs)
        if not results:
            raise Crawl4AIError(f"Crawl4AI returned no result for {url}")
        result = results[0]
        if not result.success or not (result.html or result.markdown):
            raise Crawl4AIError(
                f"Crawl4AI could not fetch {url} (status={result.status_code}): {result.error or 'empty page'}"
            )
        return result

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
    ) -> requests.Response:
        if not self.is_configured:
            raise Crawl4AIError("Crawl4AI is not configured (set CRAWL4AI_BASE_URL and CRAWL4AI_API_TOKEN).")

        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {self.api_token}"}
        attempts = 1 + (self.max_retries if retries is None else retries)
        last_error = ""
        for attempt in range(1, attempts + 1):
            try:
                response = self._session.request(
                    method,
                    url,
                    json=json,
                    headers=headers,
                    timeout=timeout or self.timeout_seconds,
                )
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code < 400:
                    return response
                last_error = f"HTTP {response.status_code}: {response.text[:300]}"
                if response.status_code not in RETRYABLE_STATUS_CODES:
                    break
            if attempt < attempts:
                backoff = 2 ** attempt
                logger.warning(
                    "Crawl4AI %s %s failed (attempt %s/%s): %s; retrying in %ss",
                    method, path, attempt, attempts, last_error, backoff,
                )
                time.sleep(backoff)
        raise Crawl4AIError(f"Crawl4AI {method} {path} failed: {last_error}")


@lru_cache(maxsize=1)
def get_crawl4ai_client() -> Crawl4AIClient:
    """Process-wide client configured from the environment."""
    return Crawl4AIClient.from_env()
