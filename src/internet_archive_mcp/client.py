"""Async HTTP client for Internet Archive APIs with caching and rate limiting."""

from __future__ import annotations

import asyncio
import copy
import math
import time
from typing import Any
from urllib.parse import quote

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

    # -- Search methods (Task 3) -----------------------------------------------

    async def search_archive(
        self,
        query: str,
        *,
        mediatype: str | None = None,
        collection: str | None = None,
        fields: list[str] | None = None,
        sort: list[str] | None = None,
        rows: int = 20,
        page: int = 1,
    ) -> dict:
        """Search via advancedsearch.php. Returns {numFound, start, docs, total_pages}."""
        if rows < 1:
            raise ValueError("rows must be >= 1")
        if page < 1:
            raise ValueError("page must be >= 1")
        # Build Lucene query with optional filters prepended.
        parts: list[str] = []
        if mediatype:
            parts.append(f"mediatype:({mediatype})")
        if collection:
            parts.append(f"collection:({collection})")
        if query and query.strip():
            parts.append(query)
        if not parts:
            raise ValueError("Search query must not be empty")
        q = " AND ".join(parts)

        if fields is None:
            fields = [
                "identifier", "title", "mediatype", "creator",
                "downloads", "publicdate", "year",
            ]

        # Build params as list of tuples for repeated keys.
        params: list[tuple[str, Any]] = [
            ("q", q),
            ("output", "json"),
            ("rows", rows),
            ("page", page),
        ]
        for f in fields:
            params.append(("fl[]", f))
        if sort:
            for s in sort[:3]:
                params.append(("sort[]", s))

        sort_key = ",".join(sort[:3]) if sort else ""
        cache_key = f"search:{q}:{rows}:{page}:{','.join(fields)}:{sort_key}"
        cached = self._get_cached(cache_key, TTL_HOUR)
        if cached is not None:
            return cached

        resp = await self._request("GET", f"{IA_BASE}/advancedsearch.php", params=params)
        resp.raise_for_status()
        data = resp.json()

        if not isinstance(data, dict):
            raise ValueError("Unexpected API response format")

        response = data.get("response", {})
        num_found = response.get("numFound", 0)
        start = response.get("start", 0)
        docs = response.get("docs", [])
        if not isinstance(docs, list):
            docs = []

        # Trim fav-* entries from collection arrays.
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            if "collection" in doc and isinstance(doc["collection"], list):
                doc["collection"] = [
                    c for c in doc["collection"] if not c.startswith("fav-")
                ]

        total_pages = math.ceil(num_found / rows) if rows > 0 else 0

        result: dict[str, Any] = {
            "numFound": num_found,
            "start": start,
            "docs": docs,
            "total_pages": total_pages,
        }
        if num_found > 10000:
            result["_warning"] = (
                "Sorted results are capped at 10,000 by the API. "
                "Use search_archive_deep for deeper paging."
            )
        self._set_cached(cache_key, result)
        return result

    async def search_archive_deep(
        self,
        query: str,
        *,
        fields: list[str] | None = None,
        sorts: list[str] | None = None,
        count: int = 100,
        cursor: str | None = None,
        total_only: bool = False,
    ) -> dict:
        """Scraping API cursor-based deep paging. Returns {items, total, count, cursor?}.

        When *total_only* is True the API returns only the total count
        without any item records — useful for quick cardinality checks.
        """
        if not query or not query.strip():
            raise ValueError("Search query must not be empty")
        if count < 100:
            raise ValueError("count must be >= 100 for the scraping API")

        params: list[tuple[str, Any]] = [
            ("q", query),
            ("count", count),
        ]
        if fields:
            params.append(("fields", ",".join(fields)))
        sorts_note: str | None = None
        if sorts:
            # Deduplicate while preserving order.
            sorts = list(dict.fromkeys(sorts))
            # API contract: 'identifier' must be last if present.
            if "identifier" in sorts and sorts[-1] != "identifier":
                sorts = [s for s in sorts if s != "identifier"] + ["identifier"]
                sorts_note = "sorts reordered: 'identifier' moved to last position per API contract"
            params.append(("sorts", ",".join(sorts)))
        if cursor:
            params.append(("cursor", cursor))
        if total_only:
            params.append(("total_only", "true"))

        resp = await self._request(
            "GET", f"{IA_BASE}/services/search/v1/scrape", params=params
        )
        resp.raise_for_status()
        data = resp.json()

        if not isinstance(data, dict):
            raise ValueError("Unexpected API response format")

        result: dict[str, Any] = {
            "items": data.get("items", []),
            "total": data.get("total", 0),
            "count": data.get("count", 0),
        }
        if sorts_note:
            result["_note"] = sorts_note
        if "cursor" in data and data["cursor"]:
            result["cursor"] = data["cursor"]
        return result

    # -- Metadata methods (Task 4) ---------------------------------------------

    async def get_item_metadata(
        self, identifier: str, *, include_files: bool = False
    ) -> dict:
        """Fetch item metadata. Raises ValueError for nonexistent items (API returns {})."""
        if not identifier or not identifier.strip():
            raise ValueError("Item identifier must not be empty")

        cache_key = f"metadata:{identifier}:{include_files}"
        cached = self._get_cached(cache_key, TTL_HOUR)
        if cached is not None:
            return cached

        resp = await self._request("GET", f"{IA_BASE}/metadata/{quote(identifier, safe='')}")
        resp.raise_for_status()
        data = resp.json()

        if not isinstance(data, dict):
            raise ValueError("Unexpected API response format")

        if not data:
            raise ValueError(f"Item not found: {identifier}")

        if not include_files:
            data.pop("files", None)
            data.pop("files_count", None)

        self._set_cached(cache_key, data)
        return copy.deepcopy(data)

    async def list_item_files(
        self, identifier: str, *, format_filter: str | None = None
    ) -> list[dict]:
        """List files for an item, optionally filtered by format (case-insensitive)."""
        data = await self.get_item_metadata(identifier, include_files=True)
        files = data.get("files", [])
        if not isinstance(files, list):
            files = []
        if format_filter:
            fmt_lower = format_filter.lower()
            files = [
                f for f in files
                if isinstance(f, dict) and f.get("format", "").lower() == fmt_lower
            ]
        else:
            files = [f for f in files if isinstance(f, dict)]
        for f in files:
            name = f.get("name", "")
            if name:
                f["download_url"] = f"{IA_BASE}/download/{quote(identifier, safe='')}/{quote(name, safe='')}"
        return files

    async def get_item_reviews(self, identifier: str) -> list[dict]:
        """Get reviews for an item. Returns [] if no reviews (absent key or null)."""
        data = await self.get_item_metadata(identifier, include_files=False)
        reviews = data.get("reviews") or []
        if not isinstance(reviews, list):
            reviews = []
        return reviews

    # -- Collection methods (Task 5) -------------------------------------------

    async def browse_collection(
        self,
        collection: str,
        *,
        rows: int = 20,
        page: int = 1,
        sort: list[str] | None = None,
    ) -> dict:
        """Browse items in a collection. Wraps search_archive with collection filter."""
        if not collection or not collection.strip():
            raise ValueError("Collection name must not be empty")
        if sort is None:
            sort = ["downloads desc"]
        return await self.search_archive(
            "",  # empty base query — collection filter is the query
            collection=collection,
            rows=rows,
            page=page,
            sort=sort,
        )

    async def get_collection_info(self, identifier: str) -> dict:
        """Get metadata for a collection item. Adds _note if not mediatype=collection."""
        data = await self.get_item_metadata(identifier, include_files=False)
        meta = data.get("metadata", {})
        if not isinstance(meta, dict):
            meta = {}
        if meta.get("mediatype") != "collection":
            data["_note"] = (
                f"Item '{identifier}' has mediatype "
                f"'{meta.get('mediatype', 'unknown')}', "
                f"not 'collection'."
            )
        return data

    # -- Wayback methods (Task 6) ----------------------------------------------

    async def wayback_snapshots(
        self,
        url: str,
        *,
        match_type: str = "exact",
        from_year: int | None = None,
        to_year: int | None = None,
        limit: int = 25,
        filter_expr: str | list[str] | None = None,
        collapse: str | None = None,
        fields: list[str] | None = None,
        page: int | None = None,
        show_resume_key: bool = False,
        resume_key: str | None = None,
        newest: bool = False,
        fast_latest: bool = False,
    ) -> list[dict] | dict:
        """CDX API search. Returns list of dicts keyed by the header row.

        When *show_resume_key* is True the return value is a dict
        ``{"snapshots": [...], "resume_key": str | None}`` so callers can
        page through large result sets.  When False (default) a plain
        ``list[dict]`` is returned for backward compatibility.
        """
        if not url or not url.strip():
            raise ValueError("URL must not be empty")
        _valid_match_types = {"exact", "prefix", "host", "domain"}
        if match_type not in _valid_match_types:
            raise ValueError(
                f"match_type must be one of {sorted(_valid_match_types)}, got '{match_type}'"
            )
        if match_type == "domain":
            raise ValueError(
                "match_type='domain' requires authentication. "
                "Use access/secret keys from https://archive.org/account/s3.php"
            )
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if page is not None and page < 1:
            raise ValueError("page must be >= 1")

        # Build cache key from all parameters that affect the response.
        filter_key = ""
        if filter_expr:
            if isinstance(filter_expr, str):
                filter_key = filter_expr
            else:
                filter_key = ",".join(filter_expr)
        fields_key = ",".join(fields) if fields else ""
        cdx_cache_key = (
            f"cdx:{url}:{match_type}:{from_year}:{to_year}:{limit}:"
            f"{filter_key}:{collapse}:{fields_key}:{page}:"
            f"{show_resume_key}:{resume_key}:{newest}:{fast_latest}"
        )
        cached = self._get_cached(cdx_cache_key, TTL_HOUR)
        if cached is not None:
            return cached

        params: list[tuple[str, Any]] = [
            ("url", url),
            ("output", "json"),
            ("matchType", match_type),
            ("limit", limit),
        ]
        if from_year is not None:
            params.append(("from", from_year))
        if to_year is not None:
            params.append(("to", to_year))
        if filter_expr:
            if isinstance(filter_expr, str):
                filter_expr = [filter_expr]
            for f in filter_expr:
                params.append(("filter", f))
        if collapse:
            params.append(("collapse", collapse))
        if fields:
            params.append(("fl", ",".join(fields)))
        if page is not None:
            params.append(("page", page))
        if show_resume_key:
            params.append(("showResumeKey", "true"))
        if resume_key:
            params.append(("resumeKey", resume_key))
        if newest:
            params.append(("newest", "true"))
        if fast_latest:
            params.append(("fastLatest", "true"))

        resp = await self._request(
            "GET", f"{WAYBACK_BASE}/cdx/search/cdx", params=params
        )
        resp.raise_for_status()
        data = resp.json()

        if not isinstance(data, list):
            if show_resume_key:
                return {"snapshots": [], "resume_key": None}
            return []

        if not data or len(data) < 2:
            if show_resume_key:
                return {"snapshots": [], "resume_key": None}
            return []

        headers = data[0]
        rows = data[1:]

        if show_resume_key:
            # CDX appends an empty [] separator row then a [resumeKey] row
            # when showResumeKey=true and more results are available.
            separator_idx: int | None = None
            for i, row in enumerate(rows):
                if row == []:
                    separator_idx = i
                    break

            parsed_resume_key: str | None = None
            if separator_idx is not None:
                data_rows = rows[:separator_idx]
                resume_rows = rows[separator_idx + 1:]
                if resume_rows and len(resume_rows[0]) >= 1:
                    parsed_resume_key = resume_rows[0][0]
            else:
                data_rows = rows

            snapshots = [dict(zip(headers, row)) for row in data_rows if isinstance(row, list) and len(row) == len(headers)]
            result = {"snapshots": snapshots, "resume_key": parsed_resume_key}
            self._set_cached(cdx_cache_key, result)
            return result

        result_list = [dict(zip(headers, row)) for row in rows if isinstance(row, list) and len(row) == len(headers)]
        self._set_cached(cdx_cache_key, result_list)
        return result_list

    async def wayback_availability(self, url: str) -> dict:
        """Quick availability check via the Wayback availability API."""
        if not url or not url.strip():
            raise ValueError("URL must not be empty")
        cache_key = f"availability:{url}"
        cached = self._get_cached(cache_key, TTL_MINUTE)
        if cached is not None:
            return cached
        resp = await self._request(
            "GET", f"{IA_BASE}/wayback/available", params={"url": url}
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError("Unexpected API response format")
        self._set_cached(cache_key, data)
        return data

    async def wayback_fetch(
        self,
        url: str,
        *,
        timestamp: str | None = None,
        raw: bool = True,
        char_limit: int = 50000,
    ) -> dict:
        """Fetch archived content from the Wayback Machine."""
        if not url or not url.strip():
            raise ValueError("URL must not be empty")
        if char_limit < 1:
            raise ValueError("char_limit must be >= 1")

        # Cache keyed on url+timestamp+raw (not char_limit — truncate on read).
        cache_key = f"fetch:{url}:{timestamp}:{raw}"
        cached = self._get_cached(cache_key, TTL_HOUR)
        if cached is not None:
            content = cached["content"]
            content_length = cached["content_length"]
            truncated = content_length > char_limit
            return {
                "url": url,
                "content": content[:char_limit] if truncated else content,
                "truncated": truncated,
                "content_length": content_length,
            }

        ts = timestamp or ""
        if raw and ts:
            ts = ts + "id_"
        elif raw and not ts:
            ts = "id_"

        if ts:
            fetch_url = f"{WAYBACK_BASE}/web/{ts}/{url}"
        else:
            fetch_url = f"{WAYBACK_BASE}/web/{url}"
        resp = await self._request("GET", fetch_url)
        resp.raise_for_status()
        content = resp.text

        content_length = len(content)

        # Cache full content; truncate on read so different char_limits share entry.
        # Cap cached content at 2 MB to avoid unbounded memory growth from large
        # archived pages (e.g. PDFs rendered as text). Content over the cap is
        # still returned to the caller in full — we just skip caching it.
        _MAX_CACHE_CONTENT_BYTES = 2 * 1024 * 1024  # 2 MB
        if content_length <= _MAX_CACHE_CONTENT_BYTES:
            self._set_cached(cache_key, {"content": content, "content_length": content_length})

        truncated = content_length > char_limit
        if truncated:
            content = content[:char_limit]

        return {
            "url": url,
            "content": content,
            "truncated": truncated,
            "content_length": content_length,
        }

    # -- Thumbnail + Save Page Now (Task 7) ------------------------------------

    def get_item_thumbnail_url(self, identifier: str) -> str:
        """Return the thumbnail URL for an item (synchronous — just builds a URL)."""
        if not identifier or not identifier.strip():
            raise ValueError("Item identifier must not be empty")
        return f"{IA_BASE}/services/img/{quote(identifier, safe='')}"

    async def save_page(
        self,
        url: str,
        *,
        access_key: str | None = None,
        secret_key: str | None = None,
    ) -> dict:
        """Save Page Now (SPN2). Requires IA S3 access/secret keys."""
        if not url or not url.strip():
            raise ValueError("URL must not be empty")
        if not access_key or not secret_key:
            raise ValueError(
                "Save Page Now requires authentication. "
                "Provide access_key and secret_key from "
                "https://archive.org/account/s3.php"
            )

        headers = {
            "Authorization": f"LOW {access_key}:{secret_key}",
            "Accept": "application/json",
        }
        resp = await self._request(
            "POST", f"{WAYBACK_BASE}/save/{url}", headers=headers
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError("Unexpected API response format")
        return data

    async def save_page_status(self, job_id: str) -> dict:
        """Poll the status of a Save Page Now (SPN2) job.

        Returns a dict with job status information including whether the
        save has completed and the resulting Wayback URL.
        """
        if not job_id or not job_id.strip():
            raise ValueError("Job ID must not be empty")
        resp = await self._request(
            "GET", f"{WAYBACK_BASE}/save/status/{quote(job_id, safe='')}"
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError("Unexpected API response format")
        return data
