"""Sync HTTP client for Internet Archive APIs with caching and rate limiting.

T03 port of the legacy transport client (tag pre-migration/20260914,
``git show pre-migration/20260914:src/internet_archive_mcp/client.py``,
679 lines -- line cites below are to THAT file). Transport internals now go
through :mod:`.transport` (raw urllib seam, T01); the ONLY behavioral diffs
from the tag are coroutine->plain calls and the ``_request`` internals.
Every endpoint method body (URL builds, param lists, quote() calls, fav-trim,
_warning/_note branches, CDX resume-key parsing, 2 MB cache cap, truncation-on-read)
is copied from the tag. Cache keys retain the same component inputs but use
JSON tuple serialization so delimiter-bearing values cannot collide. Ordinary
inputs retain the legacy key text for compatibility; ambiguous components use a
reserved ``structured:`` namespace. The internal key representation is not part
of the wire contract.
"""

from __future__ import annotations

import copy
import email.message
import json
import math
import time
import urllib.error
from typing import Any
from urllib.parse import quote

from . import transport
from .transport import fetch_raw

# Base URLs for the different API families.
# Byte-stable at tag values (tag client.py l.15-16).
IA_BASE = "https://archive.org"
WAYBACK_BASE = "https://web.archive.org"

# Cache TTL presets (seconds).
# Byte-stable at tag values (tag client.py l.19-21).
TTL_MINUTE = 60
TTL_HOUR = 3600
TTL_DAY = 24 * 3600  # unused, public surface via import

# Maximum cache entries before eviction.
# Byte-stable at tag value (tag client.py l.24).
MAX_CACHE_SIZE = 256

# Default User-Agent (IA requires a descriptive one on all automated requests).
# Byte-stable at tag value (tag client.py l.27-29) -- 0.1.0 wire identity;
# version coherence at T19 does NOT touch this literal.
DEFAULT_USER_AGENT = (
    "internet-archive-mcp/0.1.0 (https://github.com/dthen/internet-archive-mcp)"
)

# Rate limiting defaults.
# Byte-stable at tag values (tag client.py l.32-34).
DEFAULT_MIN_INTERVAL = 0.5  # seconds between requests
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 1.0  # seconds

# Module-level sleep seam. Legacy used ``asyncio.sleep`` (tag l.112, l.150);
# the sync port uses ``time.sleep`` behind this name so tests can patch
# ``client._sleep`` to observe/zero the ladder's backoff without real wall
# time. Documented for the T09-T13 test port: the legacy rate/backoff tests
# (test_client.py l.144-155, l.200-222) monkeypatched NOTHING and measured
# elapsed time -- the ported tests keep measurable behavior by running with
# backoff_base=0.01-scale intervals exactly like the legacy versions.
_sleep = time.sleep


class ArchiveClient:
    """Sync client for Internet Archive APIs.

    Features (all carried from tag client.py l.38-45):
    - Mandatory descriptive User-Agent on every request (IA policy) -- sent
      per-request by the seam (transport.fetch_raw; legacy set it as an
      AsyncClient header default, tag l.56).
    - Bounded in-memory TTL cache (deep-copied on read).
    - Simple rate limiter (minimum interval between requests).
    - Automatic retry with exponential backoff on 429 / Retry-After.

    Port notes vs tag l.47-75: the ``client=`` injection kwarg is DROPPED
    (the persistent-client seam is gone; tests inject via
    ``transport._urlopen`` monkeypatch, not a client object), and
    ``_owns_client`` / ``aclose`` / ``__aenter__`` / ``__aexit__`` are
    DELETED -- urlopen is per-request, there is no persistent connection to
    close (the lifespan-rationale ground fact for T05; the 3
    context-manager tests retire with a ledger line at T09).
    """

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        min_request_interval: float = DEFAULT_MIN_INTERVAL,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
    ) -> None:
        # Tag l.47-65 minus the client= injection branch (l.55-60); the UA is
        # stored and handed to the seam per request (legacy folded it into
        # the AsyncClient header defaults).
        self._user_agent = user_agent
        self._min_interval = min_request_interval
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._last_request_time: float = 0.0
        self._cache: dict[str, tuple[float, Any]] = {}

    def clear_cache(self) -> None:
        self._cache.clear()

    # Cache helpers -------------------------------------------------------
    # Tag behavior is preserved (monotonic, deepcopy on hit, ttl<=0 bypass,
    # evict-oldest-by-ts over MAX_CACHE_SIZE). Ordinary keys retain their legacy
    # text; delimiter-bearing components use a structured JSON namespace so they
    # cannot alias one another.

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
        self._cache[key] = (time.monotonic(), copy.deepcopy(data))
        self._evict_if_needed()

    @staticmethod
    def _cache_key(namespace: str, *parts: Any) -> str:
        """Keep legacy keys for ordinary inputs; escape ambiguous components."""
        def is_ambiguous(value: Any) -> bool:
            if value is None:
                return True
            if isinstance(value, str):
                return value == "" or ":" in value or "," in value
            if isinstance(value, (list, tuple)):
                return any(is_ambiguous(item) for item in value)
            return False

        if any(is_ambiguous(part) for part in parts):
            encoded = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
            return f"{namespace}:structured:{encoded}"

        def legacy_part(value: Any) -> str:
            if isinstance(value, (list, tuple)):
                return ",".join(str(item) for item in value)
            return str(value)

        legacy = f"{namespace}:{':'.join(legacy_part(part) for part in parts)}"
        structured_prefix = f"{namespace}:structured:"
        if legacy.startswith(structured_prefix):
            encoded = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
            return f"{structured_prefix}{encoded}"
        return legacy

    def _evict_if_needed(self) -> None:
        if len(self._cache) <= MAX_CACHE_SIZE:
            return
        ordered = sorted(self._cache, key=lambda k: self._cache[k][0])
        excess = len(self._cache) - MAX_CACHE_SIZE
        for key in ordered[:excess]:
            del self._cache[key]

    # -- Rate limiting -------------------------------------------------------

    def _wait_for_rate_limit(self) -> None:
        # Tag l.108-113 with asyncio.sleep -> _sleep (time.sleep seam).
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            _sleep(self._min_interval - elapsed)
        self._last_request_time = time.monotonic()

    # -- Core request with retry ---------------------------------------------

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | list[tuple[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> transport.SeamResponse:
        """Make an HTTP request with rate limiting and 429 retry.

        Ladder shape EXACTLY as tag l.130-152:
        - ``for attempt in range(max_retries + 1)`` (4 attempts at default).
        - Rate-limit wait BEFORE every attempt, including retries (tag l.131).
        - 429 on the LAST attempt raises urllib.error.HTTPError -- the parity
          role of legacy's HTTPStatusError (tag l.137-141; also a URLError
          subclass, so the server handlers' error fold keeps catching it).
          Message byte-shape ``f"Rate limited after {n} attempts"`` kept
          (tag l.138): it is folded into ``f"Error: API request failed — {e}"``
          at handlers and the exhaustion-count is asserted by the ported test.
        - 429 otherwise: honor Retry-After via float-parse (ValueError ->
          ``backoff_base * 2**attempt``), else exponential backoff
          (tag l.142-149); ``_sleep(delay)`` replaces ``asyncio.sleep``
          (tag l.150); ``continue``.
        - Non-429 returns the response (tag l.152).

        NO transport-error except here -- tag fact (chain R2 re-verification):
        legacy NEVER retried transport errors; they propagate (post-T01, as
        urllib.error.URLError normalized at the seam, R1) to the handlers.

        Header merge: legacy passed ``User-Agent`` once via the AsyncClient
        defaults with per-request ``headers=`` overriding (tag l.55-59,
        l.132-134); fetch_raw merges the same way (caller-supplied wins
        case-insensitively), so the effective UA per request is identical.
        """
        merged = {"User-Agent": self._user_agent}
        if headers:
            merged.update(headers)
        for attempt in range(self._max_retries + 1):
            self._wait_for_rate_limit()
            response = fetch_raw(method, url, params=params, headers=merged)
            if response.status_code == 429:
                if attempt == self._max_retries:
                    hdrs = email.message.Message()
                    for name, value in response.headers._store.items():
                        hdrs[name] = value
                    raise urllib.error.HTTPError(
                        url, 429, f"Rate limited after {self._max_retries + 1} attempts",
                        hdrs, None,
                    )
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = float(retry_after)
                    except ValueError:
                        delay = self._backoff_base * (2**attempt)
                else:
                    delay = self._backoff_base * (2**attempt)
                _sleep(delay)
                continue
            return response

        raise RuntimeError("Exhausted retries")  # pragma: no cover -- dead tail kept from tag l.154 (zero behavior: the loop always returns or raises)

    # -- Search methods (Task 3) -----------------------------------------------

    def search_archive(
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

        sort_key = sort[:3] if sort else []
        cache_key = self._cache_key(
            "search", q, rows, page, fields, sort_key
        )
        cached = self._get_cached(cache_key, TTL_HOUR)
        if cached is not None:
            return cached

        resp = self._request("GET", f"{IA_BASE}/advancedsearch.php", params=params)
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

    def search_archive_deep(
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

        resp = self._request(
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

    def get_item_metadata(
        self, identifier: str, *, include_files: bool = False
    ) -> dict:
        """Fetch item metadata. Raises ValueError for nonexistent items (API returns {})."""
        if not identifier or not identifier.strip():
            raise ValueError("Item identifier must not be empty")

        cache_key = self._cache_key("metadata", identifier, include_files)
        cached = self._get_cached(cache_key, TTL_HOUR)
        if cached is not None:
            return cached

        resp = self._request("GET", f"{IA_BASE}/metadata/{quote(identifier, safe='')}")
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

    def list_item_files(
        self, identifier: str, *, format_filter: str | None = None
    ) -> list[dict]:
        """List files for an item, optionally filtered by format (case-insensitive)."""
        data = self.get_item_metadata(identifier, include_files=True)
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

    def get_item_reviews(self, identifier: str) -> list[dict]:
        """Get reviews for an item. Returns [] if no reviews (absent key or null)."""
        data = self.get_item_metadata(identifier, include_files=False)
        reviews = data.get("reviews") or []
        if not isinstance(reviews, list):
            reviews = []
        return reviews

    # -- Collection methods (Task 5) -------------------------------------------

    def browse_collection(
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
        return self.search_archive(
            "",  # empty base query — collection filter is the query
            collection=collection,
            rows=rows,
            page=page,
            sort=sort,
        )

    def get_collection_info(self, identifier: str) -> dict:
        """Get metadata for a collection item. Adds _note if not mediatype=collection."""
        data = self.get_item_metadata(identifier, include_files=False)
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

    def wayback_snapshots(
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
        filter_key = filter_expr if isinstance(filter_expr, str) else list(filter_expr or [])
        fields_key = list(fields) if fields else []
        cdx_cache_key = self._cache_key(
            "cdx", url, match_type, from_year, to_year, limit,
            filter_key, collapse, fields_key, page, show_resume_key,
            resume_key, newest, fast_latest,
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

        resp = self._request(
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
                if resume_rows and isinstance(resume_rows[0], list) and len(resume_rows[0]) >= 1:
                    candidate = resume_rows[0][0]
                    parsed_resume_key = candidate if isinstance(candidate, str) else None
            else:
                data_rows = rows

            snapshots = [dict(zip(headers, row)) for row in data_rows if isinstance(row, list) and len(row) == len(headers)]
            result = {"snapshots": snapshots, "resume_key": parsed_resume_key}
            self._set_cached(cdx_cache_key, result)
            return result

        result_list = [dict(zip(headers, row)) for row in rows if isinstance(row, list) and len(row) == len(headers)]
        self._set_cached(cdx_cache_key, result_list)
        return result_list

    def wayback_availability(self, url: str) -> dict:
        """Quick availability check via the Wayback availability API."""
        if not url or not url.strip():
            raise ValueError("URL must not be empty")
        cache_key = self._cache_key("availability", url)
        cached = self._get_cached(cache_key, TTL_MINUTE)
        if cached is not None:
            return cached
        resp = self._request(
            "GET", f"{IA_BASE}/wayback/available", params={"url": url}
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError("Unexpected API response format")
        self._set_cached(cache_key, data)
        return data

    def wayback_fetch(
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
        cache_key = self._cache_key("fetch", url, timestamp, raw)
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
        resp = self._request("GET", fetch_url)
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

    def save_page(
        self,
        url: str,
        *,
        access_key: str | None = None,
        secret_key: str | None = None,
    ) -> dict:
        """Save Page Now (SPN2). Requires IA S3 access/secret keys.

        GET-vs-POST mix from the tag preserved: this is the ONLY POST in the
        client (tag l.655-657) -- unquoted ``/save/{url}`` path,
        ``Authorization: LOW {ak}:{sk}`` + ``Accept: application/json``
        headers, no body (the seam sends data=b"" -> Content-Length: 0).
        """
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
        resp = self._request(
            "POST", f"{WAYBACK_BASE}/save/{url}", headers=headers
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError("Unexpected API response format")
        return data

    def save_page_status(self, job_id: str) -> dict:
        """Poll the status of a Save Page Now (SPN2) job.

        Returns a dict with job status information including whether the
        save has completed and the resulting Wayback URL.
        """
        if not job_id or not job_id.strip():
            raise ValueError("Job ID must not be empty")
        resp = self._request(
            "GET", f"{WAYBACK_BASE}/save/status/{quote(job_id, safe='')}"
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError("Unexpected API response format")
        return data
