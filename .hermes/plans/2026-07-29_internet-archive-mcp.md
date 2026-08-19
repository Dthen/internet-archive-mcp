# Internet Archive MCP Server — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build a full-coverage Internet Archive MCP server — the only one that combines IA collections/search/metadata with full Wayback Machine access in a single server.

**Architecture:** Python package `internet_archive_mcp` with three modules: `client.py` (async HTTP client with mandatory User-Agent, bounded TTL cache, rate limiting, 429/Retry-After backoff), `server.py` (FastMCP server exposing 12 tools across 3 tiers), and `__init__.py`. Tests mirror the module structure. All read APIs are anonymous; Save Page Now is auth-gated via env vars.

**Tech Stack:** Python 3.10+, `mcp>=1.0` (FastMCP), `httpx>=0.27`, `pytest` + `pytest-asyncio`

**Reference:** `~/projects/internet-archive-mcp/RESEARCH.md` (396 lines, live-tested 2026-07-28)

**Established patterns** (from nager-date-mcp, crtsh-mcp, trove-scot-mcp):
- `src/<pkg>/client.py` — async httpx client, bounded cache (MAX_CACHE_SIZE=256), TTL presets, `_handle_response` mapping status codes
- `src/<pkg>/server.py` — FastMCP instance, module-level `_client`, tools return `list[dict] | dict | str`, errors caught as `ValueError`/`httpx.HTTPError` → friendly strings
- `tests/test_client.py`, `tests/test_server.py`, `tests/test_tools.py`
- `pyproject.toml` with setuptools, `[tool.pytest.ini_options] asyncio_mode = "auto"`

---

## Task 1: Project Scaffolding

**Objective:** Create the project skeleton matching the established MCP server layout.

**Files:**
- Create: `pyproject.toml`
- Create: `src/internet_archive_mcp/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/test_client.py` (empty placeholder)
- Create: `tests/test_server.py` (empty placeholder)
- Create: `tests/test_tools.py` (empty placeholder)

**Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "internet-archive-mcp"
version = "0.1.0"
description = "MCP server for the Internet Archive — search, metadata, collections, Wayback Machine"
readme = "README.md"
requires-python = ">=3.10"
dependencies = [
    "mcp>=1.0",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = [
    "pytest",
    "pytest-asyncio",
]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

**Step 2: Create package init**

```python
# src/internet_archive_mcp/__init__.py
"""Internet Archive MCP server — search, metadata, collections, and Wayback Machine."""
```

**Step 3: Create empty test files**

Each with a single docstring placeholder.

**Step 4: Install in dev mode and verify**

Run: `cd ~/projects/internet-archive-mcp && pip install -e ".[dev]"`
Expected: successful install, `pytest tests/ -q` collects 0 tests, exits 5 (no tests).

**Step 5: Commit**

```bash
git add -A && git commit -m "chore: scaffold internet-archive-mcp project"
```

---

## Task 2: Client Foundation — Base HTTP Layer

**Objective:** Build the core `ArchiveClient` with mandatory User-Agent, bounded TTL cache, rate limiting, and 429/Retry-After backoff. This is the shared foundation every tool depends on.

**Files:**
- Create: `src/internet_archive_mcp/client.py`
- Test: `tests/test_client.py`

### Step 1: Write failing tests for the base client

Tests to write in `tests/test_client.py`:

```python
"""Tests for the ArchiveClient base HTTP layer."""
import asyncio
import time
import httpx
import pytest
from internet_archive_mcp.client import ArchiveClient, MAX_CACHE_SIZE, TTL_HOUR


class TestUserAgent:
    """Every request MUST carry a descriptive User-Agent (IA policy)."""

    async def test_default_user_agent_set(self):
        client = ArchiveClient()
        assert "internet-archive-mcp" in client._client.headers["User-Agent"]

    async def test_custom_user_agent(self):
        client = ArchiveClient(user_agent="my-tool/2.0")
        assert client._client.headers["User-Agent"] == "my-tool/2.0"


class TestCache:
    """Bounded in-memory TTL cache."""

    async def test_cache_hit_returns_copy(self):
        """Cached data is deep-copied so callers can't mutate the cache."""
        client = ArchiveClient()
        # Seed the cache directly
        client._cache["test-key"] = (time.monotonic(), {"a": [1, 2]})
        result = client._get_cached("test-key", ttl=TTL_HOUR)
        assert result == {"a": [1, 2]}
        result["a"].append(3)
        # Cache must be unmodified
        _, cached = client._cache["test-key"]
        assert cached == {"a": [1, 2]}

    async def test_cache_miss_returns_none(self):
        client = ArchiveClient()
        assert client._get_cached("nonexistent", ttl=TTL_HOUR) is None

    async def test_cache_expired_returns_none(self):
        client = ArchiveClient()
        client._cache["old"] = (time.monotonic() - 9999, {"stale": True})
        assert client._get_cached("old", ttl=1) is None

    async def test_cache_eviction_at_max_size(self):
        client = ArchiveClient()
        now = time.monotonic()
        for i in range(MAX_CACHE_SIZE + 10):
            client._cache[f"key-{i}"] = (now + i, {"i": i})
        client._evict_if_needed()
        assert len(client._cache) <= MAX_CACHE_SIZE

    async def test_clear_cache(self):
        client = ArchiveClient()
        client._cache["x"] = (time.monotonic(), {"data": 1})
        client.clear_cache()
        assert len(client._cache) == 0


class TestRateLimiter:
    """Simple token-bucket / delay-based rate limiter."""

    async def test_rate_limiter_delays_when_needed(self):
        client = ArchiveClient(min_request_interval=0.1)
        start = time.monotonic()
        await client._wait_for_rate_limit()
        await client._wait_for_rate_limit()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.1  # second call was delayed

    async def test_rate_limiter_no_delay_first_call(self):
        client = ArchiveClient(min_request_interval=1.0)
        start = time.monotonic()
        await client._wait_for_rate_limit()
        elapsed = time.monotonic() - start
        assert elapsed < 0.5  # first call is immediate


class TestRetryWithBackoff:
    """429 + Retry-After handling with exponential backoff."""

    async def test_retry_on_429_with_retry_after(self, httpx_mock):
        """Server sends 429 with Retry-After; client retries and succeeds."""
        # This test uses a mock transport — see conftest or inline mock
        pass  # Implementation detail: use httpx.MockTransport

    async def test_retry_exhaustion_raises(self):
        """After max retries, raise a clear error."""
        pass
```

### Step 2: Run tests to verify failure

Run: `pytest tests/test_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'internet_archive_mcp.client'`

### Step 3: Implement the base client

```python
# src/internet_archive_mcp/client.py
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
DEFAULT_USER_AGENT = "internet-archive-mcp/0.1.0 (https://github.com/dthen/internet-archive-mcp)"

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
        params: dict[str, Any] | None = None,
        cache_ttl: int = 0,
        cache_key: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Make an HTTP request with rate limiting, caching, and retry.

        GET requests with cache_ttl > 0 are cached by URL+params.
        On 429, retries with exponential backoff (honoring Retry-After).
        """
        # Build cache key for GET requests.
        if method.upper() == "GET" and cache_ttl > 0:
            key = cache_key or f"{url}?{sorted((params or {}).items())}"
            cached = self._get_cached(key, cache_ttl)
            if cached is not None:
                # Return a synthetic response-like object? No — we cache
                # parsed data at the caller level. The _request method
                # returns raw responses; caching of parsed JSON happens
                # in the endpoint wrappers.
                pass  # Cache is handled at the wrapper level.

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
                    delay = float(retry_after)
                else:
                    delay = self._backoff_base * (2 ** attempt)
                await asyncio.sleep(delay)
                continue
            return response

        # Unreachable, but satisfies type checkers.
        raise RuntimeError("Exhausted retries")  # pragma: no cover
```

**Key design decisions:**
- Caching of *parsed JSON* happens in each endpoint wrapper (not in `_request`), because different endpoints parse responses differently (CDX returns arrays, metadata returns dicts, thumbnails return bytes).
- `_request` handles the cross-cutting concerns: UA, rate limit, 429 retry.
- `follow_redirects=True` handles the thumbnail 302 → notfound.png case.

### Step 4: Run tests to verify pass

Run: `pytest tests/test_client.py -v`
Expected: all PASS

### Step 5: Commit

```bash
git add -A && git commit -m "feat: client foundation — UA, cache, rate limiter, retry"
```

---

## Task 3: Client — Search API Methods

**Objective:** Add `search_archive` and `search_archive_deep` (scraping API) methods to the client.

**Files:**
- Modify: `src/internet_archive_mcp/client.py`
- Test: `tests/test_client.py`

### Step 1: Write failing tests

```python
class TestSearchArchive:
    """advancedsearch.php wrapper."""

    async def test_search_builds_correct_params(self, httpx_mock):
        """Verify query, fields, sort, rows, page are assembled correctly."""
        # Mock the HTTP layer, call client.search_archive(...),
        # assert the request URL and params match expectations.

    async def test_search_returns_parsed_docs(self, httpx_mock):
        """Returns {numFound, start, docs, total_pages}."""

    async def test_search_empty_query_raises(self):
        """Empty/whitespace query raises ValueError."""

    async def test_search_trims_fav_collections(self, httpx_mock):
        """fav-* entries are stripped from collection arrays in docs."""


class TestSearchArchiveDeep:
    """Scraping API (cursor-based) wrapper."""

    async def test_deep_search_returns_items_and_cursor(self, httpx_mock):
        """Returns {items, total, count, cursor}."""

    async def test_deep_search_no_cursor_means_last_page(self, httpx_mock):
        """When response has no cursor, result indicates last page."""

    async def test_deep_search_min_count_enforced(self):
        """count < 100 raises ValueError (API minimum)."""
```

### Step 2: Run tests — verify FAIL

### Step 3: Implement

Add to `ArchiveClient`:

```python
    # -- Search API (advancedsearch.php) ------------------------------------

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
    ) -> dict[str, Any]:
        """Search the Internet Archive via advancedsearch.php.

        Returns {numFound, start, docs, total_pages}.
        """
        if not query or not query.strip():
            raise ValueError("Search query must not be empty")

        # Build the Lucene query with optional filters.
        q = query.strip()
        if mediatype:
            q = f"mediatype:({mediatype}) AND {q}"
        if collection:
            q = f"collection:({collection}) AND {q}"

        # Default fields — sensible set, not everything.
        fl = fields or [
            "identifier", "title", "mediatype", "creator",
            "downloads", "publicdate", "year",
        ]

        params: list[tuple[str, str]] = [("q", q), ("output", "json"),
                                          ("rows", str(rows)), ("page", str(page))]
        for f in fl:
            params.append(("fl[]", f))
        if sort:
            for s in sort[:3]:  # API allows max 3
                params.append(("sort[]", s))

        url = f"{IA_BASE}/advancedsearch.php"
        cache_key = f"search:{q}:{fl}:{sort}:{rows}:{page}"
        cached = self._get_cached(cache_key, TTL_HOUR)
        if cached is not None:
            return cached

        response = await self._request("GET", url, params=params)
        response.raise_for_status()
        data = response.json()

        resp = data.get("response", {})
        docs = resp.get("docs", [])
        num_found = resp.get("numFound", 0)

        # Trim fav-* noise from collection arrays.
        for doc in docs:
            if isinstance(doc.get("collection"), list):
                doc["collection"] = [
                    c for c in doc["collection"] if not c.startswith("fav-")
                ]

        import math
        result = {
            "numFound": num_found,
            "start": resp.get("start", 0),
            "docs": docs,
            "total_pages": math.ceil(num_found / rows) if rows > 0 else 0,
        }
        self._set_cached(cache_key, result)
        return copy.deepcopy(result)

    # -- Scraping API (cursor-based deep paging) ----------------------------

    async def search_archive_deep(
        self,
        query: str,
        *,
        fields: list[str] | None = None,
        sorts: list[str] | None = None,
        count: int = 100,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Deep-paging search via the Scraping API.

        Returns {items, total, count, cursor (absent on last page)}.
        """
        if not query or not query.strip():
            raise ValueError("Search query must not be empty")
        if count < 100:
            raise ValueError("Scraping API requires count >= 100")

        params: dict[str, Any] = {
            "q": query.strip(),
            "fields": ",".join(fields or ["identifier", "title"]),
            "count": str(count),
        }
        if sorts:
            params["sorts"] = ",".join(sorts)
        if cursor:
            params["cursor"] = cursor

        url = f"{IA_BASE}/services/search/v1/scrape"
        response = await self._request("GET", url, params=params)
        response.raise_for_status()
        return response.json()
```

### Step 4: Run tests — verify PASS

### Step 5: Commit

```bash
git add -A && git commit -m "feat: client search_archive + search_archive_deep"
```

---

## Task 4: Client — Metadata, Files, Reviews Methods

**Objective:** Add `get_item_metadata`, `list_item_files`, and `get_item_reviews` to the client.

**Files:**
- Modify: `src/internet_archive_mcp/client.py`
- Test: `tests/test_client.py`

### Step 1: Write failing tests

```python
class TestGetItemMetadata:
    async def test_metadata_returns_parsed_dict(self, httpx_mock): ...
    async def test_metadata_empty_object_is_not_found(self, httpx_mock):
        """HTTP 200 with body {} → raises ValueError('not found')."""
    async def test_metadata_include_files_false_omits_files(self, httpx_mock):
        """When include_files=False, 'files' key is stripped from result."""

class TestListItemFiles:
    async def test_list_files_returns_file_dicts(self, httpx_mock): ...
    async def test_list_files_format_filter(self, httpx_mock):
        """format_filter='PDF' returns only PDF-format files."""
    async def test_list_files_not_found(self, httpx_mock):
        """Nonexistent identifier → ValueError."""

class TestGetItemReviews:
    async def test_reviews_returns_list(self, httpx_mock): ...
    async def test_reviews_absent_key_returns_empty(self, httpx_mock):
        """Item with no reviews key → returns []."""
    async def test_reviews_not_found(self, httpx_mock): ...
```

### Step 2: Run tests — verify FAIL

### Step 3: Implement

```python
    # -- Metadata API --------------------------------------------------------

    async def get_item_metadata(
        self,
        identifier: str,
        *,
        include_files: bool = False,
    ) -> dict[str, Any]:
        """Fetch full metadata for an item.

        Raises ValueError for nonexistent/dark items (API returns {}).
        When include_files is False (default), the potentially huge 'files'
        array is stripped from the result.
        """
        if not identifier or not identifier.strip():
            raise ValueError("Identifier must not be empty")

        url = f"{IA_BASE}/metadata/{identifier.strip()}"
        cache_key = f"metadata:{identifier}:{include_files}"
        cached = self._get_cached(cache_key, TTL_HOUR)
        if cached is not None:
            return cached

        response = await self._request("GET", url)
        response.raise_for_status()
        data = response.json()

        # Critical pitfall: API returns {} (HTTP 200) for nonexistent items.
        if not data:
            raise ValueError(f"Item not found: {identifier}")

        if not include_files:
            data.pop("files", None)
            data.pop("files_count", None)

        self._set_cached(cache_key, data)
        return copy.deepcopy(data)

    async def list_item_files(
        self,
        identifier: str,
        *,
        format_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """List files for an item, optionally filtered by format.

        Raises ValueError for nonexistent items.
        """
        data = await self.get_item_metadata(identifier, include_files=True)
        files = data.get("files", [])
        if format_filter:
            fmt = format_filter.strip().lower()
            files = [f for f in files if f.get("format", "").lower() == fmt]
        return files

    async def get_item_reviews(self, identifier: str) -> list[dict[str, Any]]:
        """Get reviews for an item. Returns [] if no reviews exist.

        Raises ValueError for nonexistent items.
        """
        data = await self.get_item_metadata(identifier, include_files=False)
        reviews = data.get("reviews")
        # Reviews key is absent/null when there are none (not an empty list).
        if not reviews:
            return []
        return reviews
```

### Step 4: Run tests — verify PASS

### Step 5: Commit

```bash
git add -A && git commit -m "feat: client metadata, files, reviews methods"
```

---

## Task 5: Client — Collection Methods

**Objective:** Add `browse_collection` and `get_collection_info` to the client.

**Files:**
- Modify: `src/internet_archive_mcp/client.py`
- Test: `tests/test_client.py`

### Step 1: Write failing tests

```python
class TestBrowseCollection:
    async def test_browse_returns_search_results(self, httpx_mock):
        """browse_collection('prelinger') → search q=collection:prelinger."""
    async def test_browse_empty_identifier_raises(self): ...

class TestGetCollectionInfo:
    async def test_collection_info_returns_metadata(self, httpx_mock):
        """Collection identifiers resolve via the metadata endpoint."""
    async def test_collection_info_not_found(self, httpx_mock): ...
```

### Step 2: Run tests — verify FAIL

### Step 3: Implement

```python
    # -- Collections ---------------------------------------------------------

    async def browse_collection(
        self,
        collection: str,
        *,
        rows: int = 20,
        page: int = 1,
        sort: list[str] | None = None,
    ) -> dict[str, Any]:
        """Browse items in a collection. Wraps search_archive with
        q=collection:{identifier}."""
        if not collection or not collection.strip():
            raise ValueError("Collection identifier must not be empty")
        return await self.search_archive(
            f"collection:({collection.strip()})",
            rows=rows,
            page=page,
            sort=sort or ["downloads desc"],
        )

    async def get_collection_info(self, identifier: str) -> dict[str, Any]:
        """Get metadata for a collection (collections are items too)."""
        data = await self.get_item_metadata(identifier, include_files=False)
        if data.get("metadata", {}).get("mediatype") != "collection":
            # Still return it — the caller may want to know it's not a
            # collection. But add a note.
            data["_note"] = f"'{identifier}' is not a collection (mediatype: {data.get('metadata', {}).get('mediatype', 'unknown')})"
        return data
```

### Step 4: Run tests — verify PASS

### Step 5: Commit

```bash
git add -A && git commit -m "feat: client collection browse + info"
```

---

## Task 6: Client — Wayback Methods (CDX, Availability, Fetch)

**Objective:** Add `wayback_snapshots`, `wayback_availability`, and `wayback_fetch` to the client.

**Files:**
- Modify: `src/internet_archive_mcp/client.py`
- Test: `tests/test_client.py`

### Step 1: Write failing tests

```python
class TestWaybackSnapshots:
    async def test_snapshots_returns_parsed_rows(self, httpx_mock):
        """CDX JSON → list of dicts keyed by header row."""
    async def test_snapshots_default_match_type_exact(self, httpx_mock): ...
    async def test_snapshots_domain_match_type_warns(self):
        """matchType='domain' requires auth — raise ValueError with guidance."""
    async def test_snapshots_empty_url_raises(self): ...
    async def test_snapshots_filter_and_collapse_params(self, httpx_mock): ...

class TestWaybackAvailability:
    async def test_availability_returns_snapshot_info(self, httpx_mock): ...
    async def test_availability_no_snapshot(self, httpx_mock):
        """URL with no archive → returns {available: False}."""

class TestWaybackFetch:
    async def test_fetch_returns_html_content(self, httpx_mock): ...
    async def test_fetch_raw_uses_id_suffix(self, httpx_mock):
        """raw=True → timestamp gets 'id_' suffix for original bytes."""
    async def test_fetch_char_limit_truncates(self, httpx_mock):
        """Content longer than char_limit is truncated with a note."""
```

### Step 2: Run tests — verify FAIL

### Step 3: Implement

```python
    # -- Wayback CDX API -----------------------------------------------------

    async def wayback_snapshots(
        self,
        url: str,
        *,
        match_type: str = "exact",
        from_year: str | None = None,
        to_year: str | None = None,
        limit: int = 25,
        filter_expr: list[str] | None = None,
        collapse: str | None = None,
        fields: list[str] | None = None,
    ) -> list[dict[str, str]]:
        """Search Wayback Machine snapshots via the CDX API.

        Returns a list of dicts, each keyed by the CDX header columns.
        Raises ValueError for empty URL or matchType='domain' (requires auth).
        """
        if not url or not url.strip():
            raise ValueError("URL must not be empty")
        if match_type == "domain":
            raise ValueError(
                "matchType='domain' requires Internet Archive authentication "
                "(S3-like keys). Use 'exact', 'prefix', or 'host' instead."
            )

        params: list[tuple[str, str]] = [
            ("url", url.strip()),
            ("output", "json"),
            ("matchType", match_type),
            ("limit", str(limit)),
        ]
        if from_year:
            params.append(("from", from_year))
        if to_year:
            params.append(("to", to_year))
        if filter_expr:
            for f in filter_expr:
                params.append(("filter", f))
        if collapse:
            params.append(("collapse", collapse))
        if fields:
            params.append(("fl", ",".join(fields)))

        cdx_url = f"{WAYBACK_BASE}/cdx/search/cdx"
        response = await self._request("GET", cdx_url, params=params)
        response.raise_for_status()
        rows = response.json()

        if not rows or len(rows) < 2:
            return []

        # First row is the header; rest are data.
        header = rows[0]
        return [dict(zip(header, row)) for row in rows[1:]]

    # -- Wayback Availability API --------------------------------------------

    async def wayback_availability(self, url: str) -> dict[str, Any]:
        """Quick check: is there an archived snapshot of this URL?

        Returns the availability API response with {url, archived_snapshots}.
        """
        if not url or not url.strip():
            raise ValueError("URL must not be empty")

        api_url = f"{IA_BASE}/wayback/available"
        response = await self._request(
            "GET", api_url, params={"url": url.strip()}
        )
        response.raise_for_status()
        return response.json()

    # -- Wayback Content Fetch -----------------------------------------------

    async def wayback_fetch(
        self,
        url: str,
        *,
        timestamp: str | None = None,
        raw: bool = True,
        char_limit: int = 50000,
    ) -> dict[str, Any]:
        """Fetch archived page content from the Wayback Machine.

        Args:
            url: The original URL to fetch from the archive.
            timestamp: Wayback timestamp (e.g. '20200101'). Partial OK.
                Omit → resolves to nearest snapshot.
            raw: If True (default), use 'id_' suffix for original bytes
                (no Wayback toolbar injection).
            char_limit: Truncate content to this many characters.

        Returns {url, timestamp, content, truncated}.
        """
        if not url or not url.strip():
            raise ValueError("URL must not be empty")

        ts = timestamp or ""
        if raw and ts:
            ts = f"{ts}id_"
        elif raw and not ts:
            ts = "id_"

        fetch_url = f"{WAYBACK_BASE}/web/{ts}/{url.strip()}"
        response = await self._request("GET", fetch_url)
        response.raise_for_status()

        content = response.text
        truncated = len(content) > char_limit
        if truncated:
            content = content[:char_limit]

        return {
            "url": str(response.url),
            "content": content,
            "truncated": truncated,
            "content_length": len(response.text),
        }
```

### Step 4: Run tests — verify PASS

### Step 5: Commit

```bash
git add -A && git commit -m "feat: client wayback CDX, availability, fetch"
```

---

## Task 7: Client — Thumbnail + Save Page Now Methods

**Objective:** Add `get_item_thumbnail` and `save_page` (auth-gated) to the client.

**Files:**
- Modify: `src/internet_archive_mcp/client.py`
- Test: `tests/test_client.py`

### Step 1: Write failing tests

```python
class TestGetItemThumbnail:
    async def test_thumbnail_returns_url(self):
        """Returns the services/img URL (client doesn't download bytes)."""
    async def test_thumbnail_empty_identifier_raises(self): ...

class TestSavePage:
    async def test_save_page_without_keys_raises(self):
        """No access/secret keys → ValueError with guidance."""
    async def test_save_page_builds_auth_header(self, httpx_mock):
        """With keys, sends Authorization: LOW access:secret."""
    async def test_save_page_returns_job_info(self, httpx_mock): ...
```

### Step 2: Run tests — verify FAIL

### Step 3: Implement

```python
    # -- Thumbnails ----------------------------------------------------------

    def get_item_thumbnail_url(self, identifier: str) -> str:
        """Return the thumbnail URL for an item.

        The URL redirects to notfound.png for items without images.
        """
        if not identifier or not identifier.strip():
            raise ValueError("Identifier must not be empty")
        return f"{IA_BASE}/services/img/{identifier.strip()}"

    # -- Save Page Now (auth-gated) ------------------------------------------

    async def save_page(
        self,
        url: str,
        *,
        access_key: str | None = None,
        secret_key: str | None = None,
    ) -> dict[str, Any]:
        """Submit a URL to Save Page Now (SPN2).

        Requires Internet Archive S3-like keys from
        https://archive.org/account/s3.php.
        """
        if not url or not url.strip():
            raise ValueError("URL must not be empty")
        if not access_key or not secret_key:
            raise ValueError(
                "Save Page Now requires Internet Archive API keys. "
                "Get them from https://archive.org/account/s3.php "
                "and set IA_ACCESS_KEY and IA_SECRET_KEY environment variables."
            )

        save_url = f"{WAYBACK_BASE}/save/{url.strip()}"
        headers = {
            "Authorization": f"LOW {access_key}:{secret_key}",
            "Accept": "application/json",
        }
        response = await self._request(
            "POST", save_url, headers=headers
        )
        response.raise_for_status()
        return response.json()
```

### Step 4: Run tests — verify PASS

### Step 5: Commit

```bash
git add -A && git commit -m "feat: client thumbnail URL + save_page (auth-gated)"
```

---

## Task 8: Server — Tier 1 Tools (Search, Metadata, Files)

**Objective:** Build the FastMCP server with the first 4 tools: `search_archive`, `get_item_metadata`, `list_item_files`, `get_item_reviews`.

**Files:**
- Create: `src/internet_archive_mcp/server.py`
- Test: `tests/test_server.py`, `tests/test_tools.py`

### Step 1: Write failing tests

```python
# tests/test_tools.py
"""Integration-style tests for MCP tools (mocked HTTP layer)."""

class TestSearchArchiveTool:
    async def test_search_returns_results(self, mock_client): ...
    async def test_search_empty_query_returns_error_string(self): ...
    async def test_search_api_error_returns_error_string(self, mock_client): ...

class TestGetItemMetadataTool:
    async def test_metadata_returns_dict(self, mock_client): ...
    async def test_metadata_not_found_returns_error_string(self, mock_client): ...

class TestListItemFilesTool:
    async def test_files_returns_list(self, mock_client): ...
    async def test_files_format_filter(self, mock_client): ...

class TestGetItemReviewsTool:
    async def test_reviews_returns_list(self, mock_client): ...
    async def test_reviews_empty(self, mock_client): ...
```

### Step 2: Run tests — verify FAIL

### Step 3: Implement server.py with Tier 1 tools

```python
# src/internet_archive_mcp/server.py
"""FastMCP server for the Internet Archive."""

from __future__ import annotations

import os

import httpx
from fastmcp import FastMCP

from internet_archive_mcp.client import ArchiveClient

mcp = FastMCP(
    "internet-archive",
    instructions=(
        "Search the Internet Archive, browse collections, get item metadata "
        "and files, read reviews, and access the Wayback Machine. "
        "All read operations are anonymous; Save Page Now requires API keys."
    ),
)

_client = ArchiveClient()


@mcp.tool()
async def search_archive(
    query: str,
    mediatype: str | None = None,
    collection: str | None = None,
    fields: list[str] | None = None,
    sort: list[str] | None = None,
    rows: int = 20,
    page: int = 1,
) -> dict | str:
    """Search the Internet Archive's full catalog — texts, audio, video, software, images.

    Args:
        query: Search query (Lucene syntax: field:value, AND/OR/NOT, ranges).
        mediatype: Filter by type: texts, audio, movies, software, image, data, collection.
        collection: Filter by collection identifier (e.g. 'prelinger', 'nasa').
        fields: Fields to return. Default: identifier, title, mediatype, creator, downloads, publicdate, year.
        sort: Sort expressions, max 3 (e.g. ['downloads desc', 'publicdate asc']).
        rows: Results per page (default 20).
        page: Page number (1-indexed).

    Returns {numFound, start, docs, total_pages}. Sorted results cap at 10,000 —
    use search_archive_deep for deeper paging.
    """
    try:
        return await _client.search_archive(
            query, mediatype=mediatype, collection=collection,
            fields=fields, sort=sort, rows=rows, page=page,
        )
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"


@mcp.tool()
async def get_item_metadata(
    identifier: str,
    include_files: bool = False,
) -> dict | str:
    """Get full metadata for an Internet Archive item.

    Args:
        identifier: Item identifier (e.g. 'goodytwoshoes00newyiala').
        include_files: Include the file listing (can be very large — 188+ files).
            Default False. Use list_item_files for filtered file access.

    Returns metadata dict with creator, date, description, subject, collection, etc.
    """
    try:
        return await _client.get_item_metadata(identifier, include_files=include_files)
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"


@mcp.tool()
async def list_item_files(
    identifier: str,
    format_filter: str | None = None,
) -> list[dict] | str:
    """List files in an Internet Archive item.

    Args:
        identifier: Item identifier.
        format_filter: Filter by format name (e.g. 'PDF', 'DjVu', 'VBR MP3').
            Case-insensitive. Omit for all files.

    Returns a list of file dicts with name, size, format, md5, etc.
    """
    try:
        return await _client.list_item_files(identifier, format_filter=format_filter)
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"


@mcp.tool()
async def get_item_reviews(identifier: str) -> list[dict] | str:
    """Get user reviews for an Internet Archive item.

    Args:
        identifier: Item identifier.

    Returns a list of review dicts with reviewtitle, reviewbody, reviewer,
    stars, reviewdate. Returns [] if the item has no reviews.
    """
    try:
        return await _client.get_item_reviews(identifier)
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"
```

### Step 4: Run tests — verify PASS

### Step 5: Commit

```bash
git add -A && git commit -m "feat: server Tier 1 — search, metadata, files, reviews"
```

---

## Task 9: Server — Tier 1 Wayback Tools

**Objective:** Add `wayback_snapshots`, `wayback_availability`, `wayback_fetch` to the server.

**Files:**
- Modify: `src/internet_archive_mcp/server.py`
- Test: `tests/test_tools.py`

### Step 1: Write failing tests

```python
class TestWaybackSnapshotsTool:
    async def test_snapshots_returns_list(self, mock_client): ...
    async def test_snapshots_domain_returns_error(self, mock_client): ...

class TestWaybackAvailabilityTool:
    async def test_availability_returns_dict(self, mock_client): ...

class TestWaybackFetchTool:
    async def test_fetch_returns_content(self, mock_client): ...
    async def test_fetch_truncated_flag(self, mock_client): ...
```

### Step 2: Run tests — verify FAIL

### Step 3: Implement

```python
@mcp.tool()
async def wayback_snapshots(
    url: str,
    match_type: str = "exact",
    from_year: str | None = None,
    to_year: str | None = None,
    limit: int = 25,
    filter_expr: list[str] | None = None,
    collapse: str | None = None,
) -> list[dict] | str:
    """Search Wayback Machine snapshots for a URL via the CDX API.

    Args:
        url: Target URL (e.g. 'example.com' or 'example.com/page').
        match_type: 'exact' (default), 'prefix' (URL prefix), 'host' (all paths on host).
            'domain' requires auth and is not supported anonymously.
        from_year: Start year filter (e.g. '2020').
        to_year: End year filter (e.g. '2023').
        limit: Max results (default 25). Popular domains have millions of captures.
        filter_expr: CDX filters (e.g. ['statuscode:200', 'mimetype:text/html']).
            Negate with '!' prefix (e.g. '!statuscode:200').
        collapse: Deduplicate on a field (e.g. 'digest' for unique captures,
            'urlkey' for unique URLs).

    Returns a list of snapshot dicts with timestamp, original, statuscode,
    mimetype, digest, length.
    """
    try:
        return await _client.wayback_snapshots(
            url, match_type=match_type, from_year=from_year,
            to_year=to_year, limit=limit, filter_expr=filter_expr,
            collapse=collapse,
        )
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"


@mcp.tool()
async def wayback_availability(url: str) -> dict | str:
    """Quick check: is there a Wayback Machine snapshot of this URL?

    Args:
        url: The URL to check.

    Returns {url, archived_snapshots} with the closest snapshot's status,
    availability flag, Wayback URL, and timestamp. Cheaper than a full CDX query.
    """
    try:
        return await _client.wayback_availability(url)
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"


@mcp.tool()
async def wayback_fetch(
    url: str,
    timestamp: str | None = None,
    raw: bool = True,
    char_limit: int = 50000,
) -> dict | str:
    """Fetch archived page content from the Wayback Machine.

    Args:
        url: The original URL to retrieve from the archive.
        timestamp: Wayback timestamp (e.g. '20200101120000'). Partial OK ('2020').
            Omit → resolves to the nearest snapshot.
        raw: If True (default), fetch original bytes without the Wayback toolbar.
        char_limit: Truncate content to this many characters (default 50000).

    Returns {url, content, truncated, content_length}.
    """
    try:
        return await _client.wayback_fetch(
            url, timestamp=timestamp, raw=raw, char_limit=char_limit,
        )
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"
```

### Step 4: Run tests — verify PASS

### Step 5: Commit

```bash
git add -A && git commit -m "feat: server Tier 1 wayback — snapshots, availability, fetch"
```

---

## Task 10: Server — Tier 2 Tools (Collections, Thumbnail, Deep Search)

**Objective:** Add `browse_collection`, `get_collection_info`, `get_item_thumbnail`, `search_archive_deep` to the server.

**Files:**
- Modify: `src/internet_archive_mcp/server.py`
- Test: `tests/test_tools.py`

### Step 1: Write failing tests

```python
class TestBrowseCollectionTool:
    async def test_browse_returns_results(self, mock_client): ...
    async def test_browse_empty_returns_error(self): ...

class TestGetCollectionInfoTool:
    async def test_info_returns_metadata(self, mock_client): ...

class TestGetItemThumbnailTool:
    async def test_thumbnail_returns_url(self): ...

class TestSearchArchiveDeepTool:
    async def test_deep_returns_items(self, mock_client): ...
    async def test_deep_low_count_returns_error(self): ...
```

### Step 2: Run tests — verify FAIL

### Step 3: Implement

```python
@mcp.tool()
async def browse_collection(
    collection: str,
    rows: int = 20,
    page: int = 1,
    sort: list[str] | None = None,
) -> dict | str:
    """Browse items in an Internet Archive collection.

    Args:
        collection: Collection identifier (e.g. 'prelinger', 'nasa', 'opensource').
        rows: Results per page (default 20).
        page: Page number (1-indexed).
        sort: Sort expressions (default: downloads desc).

    Returns {numFound, start, docs, total_pages}.
    """
    try:
        return await _client.browse_collection(
            collection, rows=rows, page=page, sort=sort,
        )
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"


@mcp.tool()
async def get_collection_info(identifier: str) -> dict | str:
    """Get metadata about an Internet Archive collection.

    Args:
        identifier: Collection identifier (e.g. 'prelinger').

    Returns the collection's metadata (title, description, etc.).
    Includes a _note if the identifier is not actually a collection.
    """
    try:
        return await _client.get_collection_info(identifier)
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"


@mcp.tool()
async def get_item_thumbnail(identifier: str) -> str:
    """Get the thumbnail image URL for an Internet Archive item.

    Args:
        identifier: Item identifier.

    Returns a URL that serves the item's thumbnail image (JPEG/PNG).
    Items without images redirect to a placeholder.
    """
    try:
        return _client.get_item_thumbnail_url(identifier)
    except ValueError as e:
        return f"Error: {e}"


@mcp.tool()
async def search_archive_deep(
    query: str,
    fields: list[str] | None = None,
    sorts: list[str] | None = None,
    count: int = 100,
    cursor: str | None = None,
) -> dict | str:
    """Deep-paging search via the Internet Archive Scraping API.

    Use this instead of search_archive when you need to page beyond the
    10,000 sorted-result cap. Returns a cursor for fetching the next page.

    Args:
        query: Search query (same Lucene syntax as search_archive).
        fields: Comma-separated fields (default: identifier, title).
        sorts: Sort expressions. If 'identifier' is included, it must be last.
        count: Results per page (minimum 100, default 100).
        cursor: Cursor from a previous response to get the next page.

    Returns {items, total, count, cursor}. When cursor is absent, you've
    reached the last page.
    """
    try:
        return await _client.search_archive_deep(
            query, fields=fields, sorts=sorts, count=count, cursor=cursor,
        )
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"
```

### Step 4: Run tests — verify PASS

### Step 5: Commit

```bash
git add -A && git commit -m "feat: server Tier 2 — collections, thumbnail, deep search"
```

---

## Task 11: Server — Tier 3 Tool (Save Page Now) + main()

**Objective:** Add the auth-gated `save_page` tool and the `main()` entry point.

**Files:**
- Modify: `src/internet_archive_mcp/server.py`
- Test: `tests/test_tools.py`

### Step 1: Write failing tests

```python
class TestSavePageTool:
    async def test_save_page_no_keys_returns_guidance(self):
        """Without env vars, returns a helpful error string."""
    async def test_save_page_with_keys_calls_api(self, mock_client, monkeypatch):
        """With IA_ACCESS_KEY and IA_SECRET_KEY set, calls the API."""
```

### Step 2: Run tests — verify FAIL

### Step 3: Implement

```python
@mcp.tool()
async def save_page(
    url: str,
    access_key: str | None = None,
    secret_key: str | None = None,
) -> dict | str:
    """Save a URL to the Wayback Machine via Save Page Now (SPN2).

    Requires Internet Archive API keys. Set them via environment variables
    IA_ACCESS_KEY and IA_SECRET_KEY (get them from
    https://archive.org/account/s3.php), or pass them directly.

    Args:
        url: The URL to archive.
        access_key: IA S3-like access key (or set IA_ACCESS_KEY env var).
        secret_key: IA S3-like secret key (or set IA_SECRET_KEY env var).

    Returns the SPN2 job response with a job ID for tracking.
    """
    try:
        ak = access_key or os.environ.get("IA_ACCESS_KEY")
        sk = secret_key or os.environ.get("IA_SECRET_KEY")
        return await _client.save_page(url, access_key=ak, secret_key=sk)
    except ValueError as e:
        return f"Error: {e}"
    except httpx.HTTPError as e:
        return f"Error: API request failed — {e}"


def main() -> None:
    """Entry point for running the server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
```

### Step 4: Run tests — verify PASS

### Step 5: Commit

```bash
git add -A && git commit -m "feat: server Tier 3 — save_page + main entry point"
```

---

## Task 12: Test Infrastructure — Mock Transport & Fixtures

**Objective:** Build the shared test fixtures (mock httpx transport, mock client) that all tool tests use. This should actually be built BEFORE Tasks 8-11 but is listed here for plan clarity — the implementer should create this as part of Task 8.

**Files:**
- Create: `tests/conftest.py`

### Implementation

```python
# tests/conftest.py
"""Shared fixtures for internet-archive-mcp tests."""

import json
import pytest
import httpx
from internet_archive_mcp.client import ArchiveClient


def make_mock_transport(handler):
    """Create an httpx.MockTransport from a handler function."""
    return httpx.MockTransport(handler)


@pytest.fixture
def mock_client():
    """An ArchiveClient wired to a mock transport.

    Tests override the handler by patching _client._client.
    """
    client = ArchiveClient(min_request_interval=0)  # no delay in tests
    return client


def json_response(data, status_code=200):
    """Build a mock httpx.Response with JSON body."""
    return httpx.Response(
        status_code=status_code,
        json=data,
        headers={"Content-Type": "application/json"},
    )
```

---

## Task 13: README

**Objective:** Write a comprehensive README.md.

**Files:**
- Create: `README.md`

### Content outline

- Project name + one-line description
- Feature list (12 tools, 3 tiers)
- Quick start (install, config for Hermes/Claude Desktop)
- Tool reference table (name, args, returns)
- Configuration (env vars: IA_ACCESS_KEY, IA_SECRET_KEY)
- Rate limiting & caching behavior
- Development (install dev deps, run tests)
- Link to RESEARCH.md

### Commit

```bash
git add -A && git commit -m "docs: comprehensive README"
```

---

## Task 14: Full Test Suite Pass + Server Startup Verification

**Objective:** Run the complete test suite, verify the server starts cleanly, and confirm all 12 tools are registered.

**Files:** None (verification only)

### Step 1: Run full test suite

Run: `cd ~/projects/internet-archive-mcp && pytest tests/ -v --tb=short`
Expected: ALL tests pass, 0 failures.

### Step 2: Verify server starts

Run: `timeout 3 python -m internet_archive_mcp.server < /dev/null 2>&1; echo "exit: $?"`
Expected: exit 0 (clean startup, no import errors).

### Step 3: Verify tool count

Run a quick Python snippet that imports the server and counts registered tools.
Expected: 12 tools.

### Step 4: Commit

```bash
git add -A && git commit -m "chore: full test pass, server verified"
```

---

## Task 15: Full API Coverage Audit

**Objective:** Verify the server covers EVERY endpoint documented in RESEARCH.md. Cross-reference the research findings against the implemented tools and client methods. Identify any gaps.

**Files:**
- Read: `RESEARCH.md`, `src/internet_archive_mcp/client.py`, `src/internet_archive_mcp/server.py`

### Checklist (from RESEARCH.md)

- [ ] Advanced Search API (§1) → `search_archive` tool
- [ ] Scraping API (§1b) → `search_archive_deep` tool
- [ ] Metadata API (§2) → `get_item_metadata` tool
- [ ] Files from metadata (§2) → `list_item_files` tool
- [ ] Reviews from metadata (§2) → `get_item_reviews` tool
- [ ] Collections as items (§2) → `get_collection_info` tool
- [ ] CDX API (§3) → `wayback_snapshots` tool
- [ ] CDX pagination/resume keys (§3) → exposed or documented
- [ ] Save Page Now (§4) → `save_page` tool
- [ ] Thumbnail/image API (§5a) → `get_item_thumbnail` tool
- [ ] Availability API (§5b) → `wayback_availability` tool
- [ ] Fetch archived content (§5c) → `wayback_fetch` tool
- [ ] Download/file access (§5d) → covered by `list_item_files` + direct URL pattern
- [ ] Related items (§5f) → documented as low-priority/excluded with reason
- [ ] Rate limits & UA (§6) → enforced in client
- [ ] 429/Retry-After (§6) → retry with backoff in client
- [ ] All mediatypes documented (§8) → in tool docstrings
- [ ] `matchType=domain` auth limitation (§3) → clear error message
- [ ] Metadata `{}` pitfall (§2) → handled as not-found
- [ ] `fav-*` trimming (§9) → implemented in search

### Output

A coverage report: what's covered, what's intentionally excluded (with reason), and any genuine gaps that need fixing before QA.

---

## QA Process (post-implementation)

After all tasks are complete, the QA loop runs:

1. **Dispatch independent adversarial reviewer** (fresh subagent, did NOT build any of this).
2. Reviewer examines ALL code, ALL tests, runs the full suite, tries edge cases.
3. Reviewer reports ALL findings — every single one, regardless of severity.
4. **Fix every finding** (dispatch fix subagent).
5. **Re-review** with a fresh reviewer.
6. **Repeat until the review report is completely EMPTY** — zero findings of any kind.
7. Only then: SHIP IT.

**QA rules (non-negotiable):**
- Never pre-label issues as "polish" / "non-blocking" / "enhancement" in QA prompts.
- Never wave through a known issue.
- The bar is an EMPTY review report, not "mostly clean".
- Reviewer must actually run the code, not just read it.

---

## Config Integration (post-QA)

After SHIP IT, wire into `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  internet-archive:
    command: /absolute/path/to/internet-archive-mcp/.venv/bin/python3
    args: ["-m", "internet_archive_mcp.server"]
```

Restart Hermes → tools appear as `mcp_internet_archive_*`.

---

## Risk Register

| Risk | Mitigation |
|------|-----------|
| CDX for popular domains returns millions of rows | Default limit=25, recommend collapse/filter in docstring |
| Metadata `files` array can be 188+ entries | Files opt-in via include_files=False default |
| IA returns `{}` for dark/nonexistent items | Explicit check → ValueError("not found") |
| `matchType=domain` returns 403 anonymously | Client raises ValueError with guidance before hitting API |
| SPN2 requires auth | Graceful error with setup instructions |
| `fav-*` collections pollute search results | Trimmed in client before returning |
| Rate limiting / 429 | Built-in retry with backoff + Retry-After |
| Archived page HTML can be enormous | char_limit truncation with truncated flag |
