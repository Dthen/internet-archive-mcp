"""Async HTTP client for Internet Archive APIs with caching and rate limiting."""

from __future__ import annotations

import asyncio
import copy
import time
from typing import Any

import httpx

# Base URLs for the different API families.
IA_BASE = "https://archive.org"
WAYBACK_BASE = "https://web.archive.org"

# Cache TTL presets (seconds).
TTL_MINUTE = 60
TTL_HOUR = 3600
TTL_DAY = 24 * 3600

# Maximum cache entries before eviction.
MAX_CACHE_SIZE = 256

# Default User-Agent (IA requires a descriptive one on all automated requests).
DEFAULT_USER_AGENT = (
    "internet-archive-mcp/0.1.0 (https://github.com/dthen/internet-archive-mcp)"
)

# Rate limiting defaults.
DEFAULT_MIN_INTERVAL = 0.5  # seconds between requests
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 1.0  # seconds


class ArchiveClient:
    """Async client for Internet Archive APIs.

    Features:
    - Mandatory descriptive User-Agent on every request (IA policy).
    - Bounded in-memory TTL cache (deep-copied on read).
    - Simple rate limiter (minimum interval between requests).
    - Automatic retry with exponential backoff on 429 / Retry-After.
    """

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        min_request_interval: float = DEFAULT_MIN_INTERVAL,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            headers={"User-Agent": user_agent},
            follow_redirects=True,
            timeout=30.0,
        )
        self._owns_client = client is None
        self._min_interval = min_request_interval
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._last_request_time: float = 0.0
        self._cache: dict[str, tuple[float, Any]] = {}

    async def __aenter__(self) -> "ArchiveClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def clear_cache(self) -> None:
        self._cache.clear()

    # -- Cache helpers -------------------------------------------------------

    def _get_cached(self, key: str, ttl: int) -> Any | None:
        """Return a deep copy of cached data if fresh, else None."""
        if ttl <= 0:
            return None
        entry = self._cache.get(key)
        if entry is None:
            return None
        ts, data = entry
        if time.monotonic() - ts >= ttl:
            return None
        return copy.deepcopy(data)

    def _set_cached(self, key: str, data: Any) -> None:
        self._cache[key] = (time.monotonic(), data)
        self._evict_if_needed()

    def _evict_if_needed(self) -> None:
        if len(self._cache) <= MAX_CACHE_SIZE:
            return
        ordered = sorted(self._cache, key=lambda k: self._cache[k][0])
        excess = len(self._cache) - MAX_CACHE_SIZE
        for key in ordered[:excess]:
            del self._cache[key]

    # -- Rate limiting -------------------------------------------------------

    async def _wait_for_rate_limit(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_request_time = time.monotonic()

    # -- Core request with retry ---------------------------------------------

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | list[tuple[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Make an HTTP request with rate limiting and 429 retry.

        On 429, retries with exponential backoff (honoring Retry-After header).
        Raises httpx.HTTPStatusError after max_retries exhausted.
        """
        for attempt in range(self._max_retries + 1):
            await self._wait_for_rate_limit()
            response = await self._client.request(
                method, url, params=params, headers=headers
            )
            if response.status_code == 429:
                if attempt == self._max_retries:
                    raise httpx.HTTPStatusError(
                        f"Rate limited after {self._max_retries + 1} attempts",
                        request=response.request,
                        response=response,
                    )
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = float(retry_after)
                    except ValueError:
                        delay = self._backoff_base * (2**attempt)
                else:
                    delay = self._backoff_base * (2**attempt)
                await asyncio.sleep(delay)
                continue
            return response

        raise RuntimeError("Exhausted retries")  # pragma: no cover
