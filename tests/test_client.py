"""Tests for the ArchiveClient HTTP layer."""

from __future__ import annotations

import time

import httpx
import pytest

from internet_archive_mcp.client import (
    DEFAULT_USER_AGENT,
    MAX_CACHE_SIZE,
    ArchiveClient,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok_client(**kwargs) -> ArchiveClient:
    """ArchiveClient backed by a MockTransport that always returns 200."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return ArchiveClient(client=http, min_request_interval=0, **kwargs)


# ---------------------------------------------------------------------------
# User-Agent tests
# ---------------------------------------------------------------------------


def test_default_user_agent_contains_marker() -> None:
    assert "internet-archive-mcp" in DEFAULT_USER_AGENT


async def test_default_user_agent_sent() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    ac = ArchiveClient(min_request_interval=0)
    ac._client = httpx.AsyncClient(
        transport=transport, headers={"User-Agent": DEFAULT_USER_AGENT}
    )
    ac._owns_client = True
    await ac._request("GET", "https://archive.org/x")
    assert "internet-archive-mcp" in seen["ua"]
    await ac.aclose()


async def test_custom_user_agent_respected() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    custom = "my-custom-agent/9.9"
    ac = ArchiveClient(user_agent=custom, min_request_interval=0)
    ac._client = httpx.AsyncClient(transport=transport, headers={"User-Agent": custom})
    ac._owns_client = True
    await ac._request("GET", "https://archive.org/x")
    assert seen["ua"] == custom
    await ac.aclose()


# ---------------------------------------------------------------------------
# Cache tests
# ---------------------------------------------------------------------------


def test_cache_miss_returns_none() -> None:
    ac = ArchiveClient(min_request_interval=0)
    assert ac._get_cached("nope", ttl=60) is None


def test_cache_hit_returns_deep_copy() -> None:
    ac = ArchiveClient(min_request_interval=0)
    data = {"items": [1, 2, 3], "nested": {"a": 1}}
    ac._set_cached("k", data)

    first = ac._get_cached("k", ttl=60)
    assert first == data
    # Mutate the returned copy.
    first["items"].append(999)
    first["nested"]["a"] = 42

    # Cache must be unaffected.
    second = ac._get_cached("k", ttl=60)
    assert second == data
    assert second["items"] == [1, 2, 3]
    assert second["nested"]["a"] == 1


def test_expired_cache_entry_returns_none() -> None:
    ac = ArchiveClient(min_request_interval=0)
    ac._set_cached("k", {"v": 1})
    # ttl=0 or negative bypasses / expires immediately.
    assert ac._get_cached("k", ttl=0) is None
    assert ac._get_cached("k", ttl=-1) is None


def test_ttl_zero_bypasses_cache() -> None:
    ac = ArchiveClient(min_request_interval=0)
    ac._set_cached("k", {"v": 1})
    # Even with a fresh entry, ttl=0 must always return None.
    for _ in range(3):
        assert ac._get_cached("k", ttl=0) is None


def test_eviction_at_max_cache_size() -> None:
    ac = ArchiveClient(min_request_interval=0)
    for i in range(MAX_CACHE_SIZE + 10):
        ac._set_cached(f"key-{i}", {"i": i})
    assert len(ac._cache) <= MAX_CACHE_SIZE


def test_clear_cache_empties_everything() -> None:
    ac = ArchiveClient(min_request_interval=0)
    for i in range(5):
        ac._set_cached(f"key-{i}", i)
    assert len(ac._cache) == 5
    ac.clear_cache()
    assert len(ac._cache) == 0
    assert ac._get_cached("key-0", ttl=60) is None


# ---------------------------------------------------------------------------
# Rate limiter tests
# ---------------------------------------------------------------------------


async def test_first_call_is_immediate() -> None:
    ac = ArchiveClient(min_request_interval=0.5)
    start = time.monotonic()
    await ac._wait_for_rate_limit()
    elapsed = time.monotonic() - start
    assert elapsed < 0.1


async def test_second_call_within_interval_is_delayed() -> None:
    ac = ArchiveClient(min_request_interval=0.2)
    await ac._wait_for_rate_limit()  # first, immediate
    start = time.monotonic()
    await ac._wait_for_rate_limit()  # second, must wait ~0.2s
    elapsed = time.monotonic() - start
    assert elapsed >= 0.15


# ---------------------------------------------------------------------------
# Retry tests (httpx.MockTransport)
# ---------------------------------------------------------------------------


async def test_success_on_first_try_no_retry() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    ac = ArchiveClient(client=http, min_request_interval=0, backoff_base=0.01)

    resp = await ac._request("GET", "https://archive.org/x")
    assert resp.status_code == 200
    assert call_count == 1


async def test_429_with_retry_after_succeeds_on_second_attempt() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, headers={"Retry-After": "0.01"})
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    ac = ArchiveClient(client=http, min_request_interval=0, backoff_base=0.01)

    resp = await ac._request("GET", "https://archive.org/x")
    assert resp.status_code == 200
    assert call_count == 2


async def test_429_without_retry_after_uses_exponential_backoff() -> None:
    call_count = 0
    timestamps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        timestamps.append(time.monotonic())
        if call_count <= 2:
            return httpx.Response(429)  # no Retry-After
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    ac = ArchiveClient(client=http, min_request_interval=0, backoff_base=0.05)

    resp = await ac._request("GET", "https://archive.org/x")
    assert resp.status_code == 200
    assert call_count == 3
    # First backoff ~0.05 (0.05*2**0), second ~0.10 (0.05*2**1).
    gap1 = timestamps[1] - timestamps[0]
    gap2 = timestamps[2] - timestamps[1]
    assert gap1 >= 0.04
    assert gap2 >= 0.08
    assert gap2 > gap1  # exponential growth


async def test_429_every_attempt_raises_after_max_retries() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(429, headers={"Retry-After": "0.01"})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    max_retries = 3
    ac = ArchiveClient(
        client=http,
        min_request_interval=0,
        max_retries=max_retries,
        backoff_base=0.01,
    )

    with pytest.raises(httpx.HTTPStatusError):
        await ac._request("GET", "https://archive.org/x")
    assert call_count == max_retries + 1


async def test_non_429_error_not_retried() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(500, json={"error": "boom"})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    ac = ArchiveClient(client=http, min_request_interval=0, backoff_base=0.01)

    resp = await ac._request("GET", "https://archive.org/x")
    assert resp.status_code == 500
    assert call_count == 1  # no retry on 500


# ---------------------------------------------------------------------------
# Context manager tests
# ---------------------------------------------------------------------------


async def test_aenter_returns_self() -> None:
    ac = _ok_client()
    async with ac as entered:
        assert entered is ac


async def test_aexit_closes_owned_client() -> None:
    ac = ArchiveClient(min_request_interval=0)  # owns its client
    assert ac._owns_client is True
    inner = ac._client
    async with ac:
        pass
    assert inner.is_closed


async def test_external_client_not_closed_on_aexit() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"ok": True})
    )
    external = httpx.AsyncClient(transport=transport)
    ac = ArchiveClient(client=external, min_request_interval=0)
    assert ac._owns_client is False
    async with ac:
        pass
    assert not external.is_closed
    await external.aclose()  # cleanup


# ---------------------------------------------------------------------------
# Helper for method-level tests
# ---------------------------------------------------------------------------


def make_client(handler) -> ArchiveClient:
    """ArchiveClient backed by a MockTransport with the given handler."""
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    return ArchiveClient(client=http_client, min_request_interval=0, backoff_base=0.01)


# ---------------------------------------------------------------------------
# Task 3: Search methods
# ---------------------------------------------------------------------------


SEARCH_RESPONSE = {
    "responseHeader": {"status": 0},
    "response": {
        "numFound": 100,
        "start": 0,
        "docs": [
            {
                "identifier": "test",
                "title": "Test",
                "collection": ["opensource", "fav-bob"],
            }
        ],
    },
}


class TestSearchArchive:
    async def test_returns_parsed_docs_with_total_pages(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=SEARCH_RESPONSE)

        ac = make_client(handler)
        result = await ac.search_archive("test query", rows=20)
        assert result["numFound"] == 100
        assert result["start"] == 0
        assert len(result["docs"]) == 1
        assert result["total_pages"] == 5  # ceil(100/20)

    async def test_empty_query_raises(self) -> None:
        ac = make_client(lambda r: httpx.Response(200, json={}))
        with pytest.raises(ValueError, match="must not be empty"):
            await ac.search_archive("")

    async def test_whitespace_only_query_raises(self) -> None:
        ac = make_client(lambda r: httpx.Response(200, json={}))
        with pytest.raises(ValueError, match="must not be empty"):
            await ac.search_archive("   ")

    async def test_fav_trimmed_from_collections(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=SEARCH_RESPONSE)

        ac = make_client(handler)
        result = await ac.search_archive("test")
        assert result["docs"][0]["collection"] == ["opensource"]

    async def test_mediatype_prepended_to_query(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, json=SEARCH_RESPONSE)

        ac = make_client(handler)
        await ac.search_archive("cats", mediatype="texts")
        assert "mediatype%3A%28texts%29+AND+cats" in seen["url"] or \
               "mediatype:(texts) AND cats" in seen["url"] or \
               "mediatype%3A(texts)%20AND%20cats" in seen["url"]

    async def test_collection_prepended_to_query(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, json=SEARCH_RESPONSE)

        ac = make_client(handler)
        await ac.search_archive("dogs", collection="opensource")
        assert "collection" in seen["url"]
        assert "opensource" in seen["url"]

    async def test_correct_params_built(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, json=SEARCH_RESPONSE)

        ac = make_client(handler)
        await ac.search_archive("test", rows=10, page=2, sort=["downloads desc"])
        url = seen["url"]
        assert "advancedsearch.php" in url
        assert "output=json" in url
        assert "rows=10" in url
        assert "page=2" in url

    async def test_caching(self) -> None:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=SEARCH_RESPONSE)

        ac = make_client(handler)
        r1 = await ac.search_archive("cached query")
        r2 = await ac.search_archive("cached query")
        assert call_count == 1
        assert r1 == r2


class TestSearchArchiveDeep:
    async def test_returns_items_and_cursor(self) -> None:
        scrape_resp = {
            "items": [{"identifier": "a"}, {"identifier": "b"}],
            "total": 500,
            "count": 2,
            "cursor": "abc123",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=scrape_resp)

        ac = make_client(handler)
        result = await ac.search_archive_deep("test")
        assert result["items"] == [{"identifier": "a"}, {"identifier": "b"}]
        assert result["total"] == 500
        assert result["count"] == 2
        assert result["cursor"] == "abc123"

    async def test_no_cursor_when_absent(self) -> None:
        scrape_resp = {"items": [], "total": 0, "count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=scrape_resp)

        ac = make_client(handler)
        result = await ac.search_archive_deep("test")
        assert "cursor" not in result

    async def test_count_below_100_raises(self) -> None:
        ac = make_client(lambda r: httpx.Response(200, json={}))
        with pytest.raises(ValueError, match="count must be >= 100"):
            await ac.search_archive_deep("test", count=50)

    async def test_empty_query_raises(self) -> None:
        ac = make_client(lambda r: httpx.Response(200, json={}))
        with pytest.raises(ValueError, match="must not be empty"):
            await ac.search_archive_deep("")

    async def test_params_include_fields_and_sorts(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, json={"items": [], "total": 0, "count": 0})

        ac = make_client(handler)
        await ac.search_archive_deep(
            "test", fields=["identifier", "title"], sorts=["downloads desc"]
        )
        url = seen["url"]
        assert "scrape" in url
        assert "identifier" in url
        assert "title" in url


# ---------------------------------------------------------------------------
# Task 4: Metadata methods
# ---------------------------------------------------------------------------


METADATA_RESPONSE = {
    "metadata": {"identifier": "test", "mediatype": "texts", "title": "Test"},
    "files": [{"name": "test.pdf", "format": "PDF", "size": "1234"}],
    "files_count": 1,
}


class TestGetItemMetadata:
    async def test_returns_parsed_dict(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=METADATA_RESPONSE)

        ac = make_client(handler)
        result = await ac.get_item_metadata("test", include_files=True)
        assert result["metadata"]["identifier"] == "test"
        assert len(result["files"]) == 1

    async def test_empty_dict_raises_not_found(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={})

        ac = make_client(handler)
        with pytest.raises(ValueError, match="Item not found: nonexistent"):
            await ac.get_item_metadata("nonexistent")

    async def test_include_files_false_strips_files(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=METADATA_RESPONSE)

        ac = make_client(handler)
        result = await ac.get_item_metadata("test", include_files=False)
        assert "files" not in result
        assert "files_count" not in result
        assert "metadata" in result

    async def test_empty_identifier_raises(self) -> None:
        ac = make_client(lambda r: httpx.Response(200, json={}))
        with pytest.raises(ValueError, match="must not be empty"):
            await ac.get_item_metadata("")

    async def test_caching(self) -> None:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=METADATA_RESPONSE)

        ac = make_client(handler)
        await ac.get_item_metadata("test")
        await ac.get_item_metadata("test")
        assert call_count == 1


class TestListItemFiles:
    async def test_returns_file_list(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=METADATA_RESPONSE)

        ac = make_client(handler)
        files = await ac.list_item_files("test")
        assert len(files) == 1
        assert files[0]["name"] == "test.pdf"

    async def test_format_filter_case_insensitive(self) -> None:
        resp = {
            "metadata": {"identifier": "test"},
            "files": [
                {"name": "a.pdf", "format": "PDF"},
                {"name": "b.txt", "format": "Text"},
                {"name": "c.pdf", "format": "pdf"},
            ],
            "files_count": 3,
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=resp)

        ac = make_client(handler)
        files = await ac.list_item_files("test", format_filter="pdf")
        assert len(files) == 2
        assert all(f["format"].lower() == "pdf" for f in files)

    async def test_not_found_propagates(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={})

        ac = make_client(handler)
        with pytest.raises(ValueError, match="Item not found"):
            await ac.list_item_files("nonexistent")


class TestGetItemReviews:
    async def test_returns_reviews_list(self) -> None:
        resp = {
            "metadata": {"identifier": "test"},
            "reviews": [{"reviewer": "bob", "stars": 5}],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=resp)

        ac = make_client(handler)
        reviews = await ac.get_item_reviews("test")
        assert len(reviews) == 1
        assert reviews[0]["reviewer"] == "bob"

    async def test_absent_reviews_key_returns_empty(self) -> None:
        resp = {"metadata": {"identifier": "test"}}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=resp)

        ac = make_client(handler)
        reviews = await ac.get_item_reviews("test")
        assert reviews == []

    async def test_null_reviews_returns_empty(self) -> None:
        resp = {"metadata": {"identifier": "test"}, "reviews": None}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=resp)

        ac = make_client(handler)
        reviews = await ac.get_item_reviews("test")
        assert reviews == []


# ---------------------------------------------------------------------------
# Task 5: Collection methods
# ---------------------------------------------------------------------------


class TestBrowseCollection:
    async def test_wraps_search_correctly(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, json=SEARCH_RESPONSE)

        ac = make_client(handler)
        result = await ac.browse_collection("opensource")
        assert result["numFound"] == 100
        assert "collection" in seen["url"]
        assert "opensource" in seen["url"]

    async def test_empty_collection_raises(self) -> None:
        ac = make_client(lambda r: httpx.Response(200, json={}))
        with pytest.raises(ValueError, match="must not be empty"):
            await ac.browse_collection("")

    async def test_default_sort_is_downloads(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, json=SEARCH_RESPONSE)

        ac = make_client(handler)
        await ac.browse_collection("opensource")
        assert "downloads" in seen["url"]


class TestGetCollectionInfo:
    async def test_returns_metadata_for_collection(self) -> None:
        resp = {
            "metadata": {"identifier": "opensource", "mediatype": "collection"},
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=resp)

        ac = make_client(handler)
        result = await ac.get_collection_info("opensource")
        assert result["metadata"]["mediatype"] == "collection"
        assert "_note" not in result

    async def test_non_collection_gets_note(self) -> None:
        resp = {
            "metadata": {"identifier": "test", "mediatype": "texts"},
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=resp)

        ac = make_client(handler)
        result = await ac.get_collection_info("test")
        assert "_note" in result
        assert "texts" in result["_note"]


# ---------------------------------------------------------------------------
# Task 6: Wayback methods
# ---------------------------------------------------------------------------


CDX_RESPONSE = [
    ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"],
    ["com,example)/", "20020120142510", "http://example.com/", "text/html", "200", "ABC", "1792"],
]


class TestWaybackSnapshots:
    async def test_parses_cdx_header_and_rows(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=CDX_RESPONSE)

        ac = make_client(handler)
        result = await ac.wayback_snapshots("example.com")
        assert len(result) == 1
        assert result[0]["urlkey"] == "com,example)/"
        assert result[0]["timestamp"] == "20020120142510"
        assert result[0]["statuscode"] == "200"

    async def test_domain_match_type_raises(self) -> None:
        ac = make_client(lambda r: httpx.Response(200, json=[]))
        with pytest.raises(ValueError, match="domain"):
            await ac.wayback_snapshots("example.com", match_type="domain")

    async def test_empty_url_raises(self) -> None:
        ac = make_client(lambda r: httpx.Response(200, json=[]))
        with pytest.raises(ValueError, match="must not be empty"):
            await ac.wayback_snapshots("")

    async def test_filter_and_collapse_params_passed(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, json=CDX_RESPONSE)

        ac = make_client(handler)
        await ac.wayback_snapshots(
            "example.com",
            filter_expr="statuscode:200",
            collapse="urlkey",
        )
        url = seen["url"]
        assert "filter" in url
        assert "collapse" in url

    async def test_empty_response_returns_empty_list(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[])

        ac = make_client(handler)
        result = await ac.wayback_snapshots("example.com")
        assert result == []

    async def test_header_only_response_returns_empty(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[["urlkey", "timestamp"]])

        ac = make_client(handler)
        result = await ac.wayback_snapshots("example.com")
        assert result == []


AVAILABILITY_RESPONSE = {
    "url": "example.com",
    "archived_snapshots": {
        "closest": {
            "status": "200",
            "available": True,
            "url": "http://web.archive.org/web/2026/example.com",
            "timestamp": "20260101",
        }
    },
}


class TestWaybackAvailability:
    async def test_returns_response_dict(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=AVAILABILITY_RESPONSE)

        ac = make_client(handler)
        result = await ac.wayback_availability("example.com")
        assert result["url"] == "example.com"
        assert result["archived_snapshots"]["closest"]["available"] is True

    async def test_empty_url_raises(self) -> None:
        ac = make_client(lambda r: httpx.Response(200, json={}))
        with pytest.raises(ValueError, match="must not be empty"):
            await ac.wayback_availability("")


class TestWaybackFetch:
    async def test_returns_content(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>Hello</html>")

        ac = make_client(handler)
        result = await ac.wayback_fetch("example.com", timestamp="20260101")
        assert result["content"] == "<html>Hello</html>"
        assert result["truncated"] is False
        assert result["content_length"] == len("<html>Hello</html>")

    async def test_raw_adds_id_suffix(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, text="content")

        ac = make_client(handler)
        await ac.wayback_fetch("example.com", timestamp="20260101", raw=True)
        assert "20260101id_" in seen["url"]

    async def test_no_raw_no_id_suffix(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, text="content")

        ac = make_client(handler)
        await ac.wayback_fetch("example.com", timestamp="20260101", raw=False)
        assert "id_" not in seen["url"]

    async def test_truncation_works(self) -> None:
        long_content = "x" * 1000

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=long_content)

        ac = make_client(handler)
        result = await ac.wayback_fetch("example.com", char_limit=100)
        assert result["truncated"] is True
        assert len(result["content"]) == 100
        assert result["content_length"] == 1000

    async def test_empty_url_raises(self) -> None:
        ac = make_client(lambda r: httpx.Response(200, text=""))
        with pytest.raises(ValueError, match="must not be empty"):
            await ac.wayback_fetch("")


# ---------------------------------------------------------------------------
# Task 7: Thumbnail + Save Page Now
# ---------------------------------------------------------------------------


class TestGetItemThumbnailUrl:
    def test_returns_correct_url(self) -> None:
        ac = make_client(lambda r: httpx.Response(200))
        url = ac.get_item_thumbnail_url("test_item")
        assert url == "https://archive.org/services/img/test_item"

    def test_empty_identifier_raises(self) -> None:
        ac = make_client(lambda r: httpx.Response(200))
        with pytest.raises(ValueError, match="must not be empty"):
            ac.get_item_thumbnail_url("")


class TestSavePage:
    async def test_missing_keys_raises_with_guidance(self) -> None:
        ac = make_client(lambda r: httpx.Response(200, json={}))
        with pytest.raises(ValueError, match="s3.php"):
            await ac.save_page("https://example.com")

    async def test_missing_secret_key_raises(self) -> None:
        ac = make_client(lambda r: httpx.Response(200, json={}))
        with pytest.raises(ValueError, match="s3.php"):
            await ac.save_page("https://example.com", access_key="AK")

    async def test_with_keys_sends_correct_auth_header(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("authorization", "")
            seen["accept"] = request.headers.get("accept", "")
            seen["method"] = request.method
            return httpx.Response(200, json={"url": "example.com", "job_id": "123"})

        ac = make_client(handler)
        result = await ac.save_page(
            "https://example.com",
            access_key="AK",
            secret_key="SK",
        )
        assert seen["auth"] == "LOW AK:SK"
        assert seen["accept"] == "application/json"
        assert seen["method"] == "POST"
        assert result["job_id"] == "123"

    async def test_empty_url_raises(self) -> None:
        ac = make_client(lambda r: httpx.Response(200, json={}))
        with pytest.raises(ValueError, match="must not be empty"):
            await ac.save_page("", access_key="AK", secret_key="SK")
