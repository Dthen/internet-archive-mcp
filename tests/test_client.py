"""Tests for the ArchiveClient HTTP layer."""

from __future__ import annotations

import time
import urllib.error

import httpx
import pytest

from internet_archive_mcp.client import (
    DEFAULT_USER_AGENT,
    MAX_CACHE_SIZE,
    ArchiveClient,
)

import internet_archive_mcp.transport as transport
from conftest import json_response, make_mock_client, text_response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok_client(**kwargs) -> ArchiveClient:
    """ArchiveClient backed by the fake seam that always returns 200.

    T09 sync rewrite onto the T08 conftest contract (fake-urlopen-backed via
    make_mock_client; the legacy MockTransport + client= injection is gone).
    """
    return make_mock_client(lambda request: json_response({"ok": True}), **kwargs)


# ---------------------------------------------------------------------------
# User-Agent tests
# ---------------------------------------------------------------------------


def test_default_user_agent_contains_marker() -> None:
    assert "internet-archive-mcp" in DEFAULT_USER_AGENT


def test_default_user_agent_sent() -> None:
    seen: dict[str, str] = {}

    def handler(request):
        seen["ua"] = request.headers.get("user-agent", "")
        return json_response({"ok": True})

    ac = make_mock_client(handler)
    ac._request("GET", "https://archive.org/x")
    assert "internet-archive-mcp" in seen["ua"]


def test_custom_user_agent_respected() -> None:
    seen: dict[str, str] = {}

    def handler(request):
        seen["ua"] = request.headers.get("user-agent", "")
        return json_response({"ok": True})

    custom = "my-custom-agent/9.9"
    ac = make_mock_client(handler, user_agent=custom)
    ac._request("GET", "https://archive.org/x")
    assert seen["ua"] == custom


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


def test_first_call_is_immediate() -> None:
    ac = ArchiveClient(min_request_interval=0.5)
    start = time.monotonic()
    ac._wait_for_rate_limit()
    elapsed = time.monotonic() - start
    assert elapsed < 0.1


def test_second_call_within_interval_is_delayed() -> None:
    ac = ArchiveClient(min_request_interval=0.2)
    ac._wait_for_rate_limit()  # first, immediate
    start = time.monotonic()
    ac._wait_for_rate_limit()  # second, must wait ~0.2s
    elapsed = time.monotonic() - start
    assert elapsed >= 0.15


# ---------------------------------------------------------------------------
# Retry tests (fake-urlopen seam)
# ---------------------------------------------------------------------------


def test_success_on_first_try_no_retry() -> None:
    call_count = 0

    def handler(request):
        nonlocal call_count
        call_count += 1
        return json_response({"ok": True})

    ac = make_mock_client(handler)

    resp = ac._request("GET", "https://archive.org/x")
    assert resp.status_code == 200
    assert call_count == 1


def test_429_with_retry_after_succeeds_on_second_attempt() -> None:
    call_count = 0

    def handler(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return text_response("", 429, {"Retry-After": "0.01"})
        return json_response({"ok": True})

    ac = make_mock_client(handler)

    resp = ac._request("GET", "https://archive.org/x")
    assert resp.status_code == 200
    assert call_count == 2


def test_429_without_retry_after_uses_exponential_backoff() -> None:
    call_count = 0
    timestamps: list[float] = []

    def handler(request):
        nonlocal call_count
        call_count += 1
        timestamps.append(time.monotonic())
        if call_count <= 2:
            return text_response("", 429)  # no Retry-After
        return json_response({"ok": True})

    # backoff_base=0.05 exactly like the legacy test (gaps ~0.05 / ~0.10).
    ac = make_mock_client(handler, backoff_base=0.05)

    resp = ac._request("GET", "https://archive.org/x")
    assert resp.status_code == 200
    assert call_count == 3
    # First backoff ~0.05 (0.05*2**0), second ~0.10 (0.05*2**1).
    gap1 = timestamps[1] - timestamps[0]
    gap2 = timestamps[2] - timestamps[1]
    assert gap1 >= 0.04
    assert gap2 >= 0.08
    assert gap2 > gap1  # exponential growth


def test_429_every_attempt_raises_after_max_retries() -> None:
    call_count = 0

    def handler(request):
        nonlocal call_count
        call_count += 1
        return text_response("", 429, {"Retry-After": "0.01"})

    max_retries = 3
    ac = make_mock_client(handler, max_retries=max_retries)

    # urllib.error.HTTPError is the seam-parity class for legacy's
    # HTTPStatusError (both land in the handlers' friendly fold).
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        ac._request("GET", "https://archive.org/x")
    assert call_count == max_retries + 1
    # NEW pin (T09; legacy asserted ONLY the count above — "Rate limited" had
    # zero hits in the tag tests): pins T03's byte shape
    # f"Rate limited after {max_retries + 1} attempts".
    assert "Rate limited after 4 attempts" in str(excinfo.value)


def test_non_429_error_not_retried() -> None:
    call_count = 0

    def handler(request):
        nonlocal call_count
        call_count += 1
        return json_response({"error": "boom"}, 500)

    ac = make_mock_client(handler)

    resp = ac._request("GET", "https://archive.org/x")
    assert resp.status_code == 500
    assert call_count == 1  # no retry on 500


# ---------------------------------------------------------------------------
# R1 fold-echo pin: a read timeout normalizes at the seam and is NEVER retried
# ---------------------------------------------------------------------------


def test_read_timeout_folds_to_urlerror_at_client(monkeypatch) -> None:
    """Chain R1 (client level): fake _urlopen raising TimeoutError must reach
    _request as urllib.error.URLError — no TimeoutError leak (which would
    escape the handlers' transport-error fold to a -32603) and no
    retry (tag fact: legacy retried 429 only; transport errors propagated).
    """
    calls: list[str] = []

    def timeouting_urlopen(request, timeout=None):
        calls.append(str(request.full_url))
        raise TimeoutError("read timed out")

    monkeypatch.setattr(transport, "_urlopen", timeouting_urlopen)
    ac = ArchiveClient(min_request_interval=0, backoff_base=0.01)

    with pytest.raises(urllib.error.URLError):
        ac._request("GET", "https://archive.org/x")
    assert len(calls) == 1  # single attempt: the seam converts, no ladder


# ---------------------------------------------------------------------------
# Helper for method-level tests
# ---------------------------------------------------------------------------


def make_client(handler) -> ArchiveClient:
    """ArchiveClient whose every request flows through the fake seam with the
    given handler.

    T09 sync rewrite in conftest.make_mock_client shape (defaults identical to
    the legacy helper: min_request_interval=0, backoff_base=0.01), so every
    ported class in T10a-T13 calls the already-sync helper.
    """
    return make_mock_client(handler)


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
    def test_returns_parsed_docs_with_total_pages(self) -> None:
        def handler(request):
            return json_response(SEARCH_RESPONSE)

        ac = make_client(handler)
        result = ac.search_archive("test query", rows=20)
        assert result["numFound"] == 100
        assert result["start"] == 0
        assert len(result["docs"]) == 1
        assert result["total_pages"] == 5  # ceil(100/20)

    def test_empty_query_raises(self) -> None:
        ac = make_client(lambda r: json_response({}))
        with pytest.raises(ValueError, match="must not be empty"):
            ac.search_archive("")

    def test_whitespace_only_query_raises(self) -> None:
        ac = make_client(lambda r: json_response({}))
        with pytest.raises(ValueError, match="must not be empty"):
            ac.search_archive("   ")

    def test_fav_trimmed_from_collections(self) -> None:
        def handler(request):
            return json_response(SEARCH_RESPONSE)

        ac = make_client(handler)
        result = ac.search_archive("test")
        assert result["docs"][0]["collection"] == ["opensource"]

    def test_mediatype_prepended_to_query(self) -> None:
        seen: dict = {}

        def handler(request):
            seen["url"] = str(request.url)
            return json_response(SEARCH_RESPONSE)

        ac = make_client(handler)
        ac.search_archive("cats", mediatype="texts")
        assert "mediatype%3A%28texts%29+AND+cats" in seen["url"] or \
               "mediatype:(texts) AND cats" in seen["url"] or \
               "mediatype%3A(texts)%20AND%20cats" in seen["url"]

    def test_collection_prepended_to_query(self) -> None:
        seen: dict = {}

        def handler(request):
            seen["url"] = str(request.url)
            return json_response(SEARCH_RESPONSE)

        ac = make_client(handler)
        ac.search_archive("dogs", collection="opensource")
        assert "collection" in seen["url"]
        assert "opensource" in seen["url"]

    def test_correct_params_built(self) -> None:
        seen: dict = {}

        def handler(request):
            seen["url"] = str(request.url)
            return json_response(SEARCH_RESPONSE)

        ac = make_client(handler)
        ac.search_archive("test", rows=10, page=2, sort=["downloads desc"])
        url = seen["url"]
        assert "advancedsearch.php" in url
        assert "output=json" in url
        assert "rows=10" in url
        assert "page=2" in url

    def test_caching(self) -> None:
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return json_response(SEARCH_RESPONSE)

        ac = make_client(handler)
        r1 = ac.search_archive("cached query")
        r2 = ac.search_archive("cached query")
        assert call_count == 1
        assert r1 == r2


class TestSearchArchiveDeep:
    def test_returns_items_and_cursor(self) -> None:
        scrape_resp = {
            "items": [{"identifier": "a"}, {"identifier": "b"}],
            "total": 500,
            "count": 2,
            "cursor": "abc123",
        }

        def handler(request):
            return json_response(scrape_resp)

        ac = make_client(handler)
        result = ac.search_archive_deep("test")
        assert result["items"] == [{"identifier": "a"}, {"identifier": "b"}]
        assert result["total"] == 500
        assert result["count"] == 2
        assert result["cursor"] == "abc123"

    def test_no_cursor_when_absent(self) -> None:
        scrape_resp = {"items": [], "total": 0, "count": 0}

        def handler(request):
            return json_response(scrape_resp)

        ac = make_client(handler)
        result = ac.search_archive_deep("test")
        assert "cursor" not in result

    def test_count_below_100_raises(self) -> None:
        ac = make_client(lambda r: json_response({}))
        with pytest.raises(ValueError, match="count must be >= 100"):
            ac.search_archive_deep("test", count=50)

    def test_empty_query_raises(self) -> None:
        ac = make_client(lambda r: json_response({}))
        with pytest.raises(ValueError, match="must not be empty"):
            ac.search_archive_deep("")

    def test_params_include_fields_and_sorts(self) -> None:
        seen: dict = {}

        def handler(request):
            seen["url"] = str(request.url)
            return json_response({"items": [], "total": 0, "count": 0})

        ac = make_client(handler)
        ac.search_archive_deep(
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
    def test_returns_parsed_dict(self) -> None:
        def handler(request):
            return json_response(METADATA_RESPONSE)

        ac = make_client(handler)
        result = ac.get_item_metadata("test", include_files=True)
        assert result["metadata"]["identifier"] == "test"
        assert len(result["files"]) == 1

    def test_empty_dict_raises_not_found(self) -> None:
        def handler(request):
            return json_response({})

        ac = make_client(handler)
        with pytest.raises(ValueError, match="Item not found: nonexistent"):
            ac.get_item_metadata("nonexistent")

    def test_include_files_false_strips_files(self) -> None:
        def handler(request):
            return json_response(METADATA_RESPONSE)

        ac = make_client(handler)
        result = ac.get_item_metadata("test", include_files=False)
        assert "files" not in result
        assert "files_count" not in result
        assert "metadata" in result

    def test_empty_identifier_raises(self) -> None:
        ac = make_client(lambda r: json_response({}))
        with pytest.raises(ValueError, match="must not be empty"):
            ac.get_item_metadata("")

    def test_caching(self) -> None:
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return json_response(METADATA_RESPONSE)

        ac = make_client(handler)
        ac.get_item_metadata("test")
        ac.get_item_metadata("test")
        assert call_count == 1


class TestListItemFiles:
    def test_returns_file_list(self) -> None:
        def handler(request):
            return json_response(METADATA_RESPONSE)

        ac = make_client(handler)
        files = ac.list_item_files("test")
        assert len(files) == 1
        assert files[0]["name"] == "test.pdf"

    def test_format_filter_case_insensitive(self) -> None:
        resp = {
            "metadata": {"identifier": "test"},
            "files": [
                {"name": "a.pdf", "format": "PDF"},
                {"name": "b.txt", "format": "Text"},
                {"name": "c.pdf", "format": "pdf"},
            ],
            "files_count": 3,
        }

        def handler(request):
            return json_response(resp)

        ac = make_client(handler)
        files = ac.list_item_files("test", format_filter="pdf")
        assert len(files) == 2
        assert all(f["format"].lower() == "pdf" for f in files)

    def test_not_found_propagates(self) -> None:
        def handler(request):
            return json_response({})

        ac = make_client(handler)
        with pytest.raises(ValueError, match="Item not found"):
            ac.list_item_files("nonexistent")


class TestGetItemReviews:
    def test_returns_reviews_list(self) -> None:
        resp = {
            "metadata": {"identifier": "test"},
            "reviews": [{"reviewer": "bob", "stars": 5}],
        }

        def handler(request):
            return json_response(resp)

        ac = make_client(handler)
        reviews = ac.get_item_reviews("test")
        assert len(reviews) == 1
        assert reviews[0]["reviewer"] == "bob"

    def test_absent_reviews_key_returns_empty(self) -> None:
        resp = {"metadata": {"identifier": "test"}}

        def handler(request):
            return json_response(resp)

        ac = make_client(handler)
        reviews = ac.get_item_reviews("test")
        assert reviews == []

    def test_null_reviews_returns_empty(self) -> None:
        resp = {"metadata": {"identifier": "test"}, "reviews": None}

        def handler(request):
            return json_response(resp)

        ac = make_client(handler)
        reviews = ac.get_item_reviews("test")
        assert reviews == []


# ---------------------------------------------------------------------------
# Task 5: Collection methods
# ---------------------------------------------------------------------------


class TestBrowseCollection:
    def test_wraps_search_correctly(self) -> None:
        seen: dict = {}

        def handler(request):
            seen["url"] = str(request.url)
            return json_response(SEARCH_RESPONSE)

        ac = make_client(handler)
        result = ac.browse_collection("opensource")
        assert result["numFound"] == 100
        assert "collection" in seen["url"]
        assert "opensource" in seen["url"]

    def test_empty_collection_raises(self) -> None:
        ac = make_client(lambda r: json_response({}))
        with pytest.raises(ValueError, match="must not be empty"):
            ac.browse_collection("")

    def test_default_sort_is_downloads(self) -> None:
        seen: dict = {}

        def handler(request):
            seen["url"] = str(request.url)
            return json_response(SEARCH_RESPONSE)

        ac = make_client(handler)
        ac.browse_collection("opensource")
        assert "downloads" in seen["url"]


class TestGetCollectionInfo:
    def test_returns_metadata_for_collection(self) -> None:
        resp = {
            "metadata": {"identifier": "opensource", "mediatype": "collection"},
        }

        def handler(request):
            return json_response(resp)

        ac = make_client(handler)
        result = ac.get_collection_info("opensource")
        assert result["metadata"]["mediatype"] == "collection"
        assert "_note" not in result

    def test_non_collection_gets_note(self) -> None:
        resp = {
            "metadata": {"identifier": "test", "mediatype": "texts"},
        }

        def handler(request):
            return json_response(resp)

        ac = make_client(handler)
        result = ac.get_collection_info("test")
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
    def test_parses_cdx_header_and_rows(self) -> None:
        def handler(request):
            return json_response(CDX_RESPONSE)

        ac = make_client(handler)
        result = ac.wayback_snapshots("example.com")
        assert len(result) == 1
        assert result[0]["urlkey"] == "com,example)/"
        assert result[0]["timestamp"] == "20020120142510"
        assert result[0]["statuscode"] == "200"

    def test_domain_match_type_raises(self) -> None:
        ac = make_client(lambda r: json_response([]))
        with pytest.raises(ValueError, match="domain"):
            ac.wayback_snapshots("example.com", match_type="domain")

    def test_empty_url_raises(self) -> None:
        ac = make_client(lambda r: json_response([]))
        with pytest.raises(ValueError, match="must not be empty"):
            ac.wayback_snapshots("")

    def test_filter_and_collapse_params_passed(self) -> None:
        seen: dict = {}

        def handler(request):
            seen["url"] = str(request.url)
            return json_response(CDX_RESPONSE)

        ac = make_client(handler)
        ac.wayback_snapshots(
            "example.com",
            filter_expr="statuscode:200",
            collapse="urlkey",
        )
        url = seen["url"]
        assert "filter" in url
        assert "collapse" in url

    def test_empty_response_returns_empty_list(self) -> None:
        def handler(request):
            return json_response([])

        ac = make_client(handler)
        result = ac.wayback_snapshots("example.com")
        assert result == []

    def test_header_only_response_returns_empty(self) -> None:
        def handler(request):
            return json_response([["urlkey", "timestamp"]])

        ac = make_client(handler)
        result = ac.wayback_snapshots("example.com")
        assert result == []


class TestWaybackSnapshotsPagination:
    def test_show_resume_key_parses_separator_and_key(self) -> None:
        """When showResumeKey=true, CDX appends [] then [resumeKey]."""
        cdx_with_resume = [
            ["urlkey", "timestamp", "original"],
            ["com,example)/", "20200101", "http://example.com/"],
            ["com,example)/", "20200201", "http://example.com/"],
            [],
            ["resume_abc123"],
        ]

        def handler(request):
            return json_response(cdx_with_resume)

        ac = make_client(handler)
        result = ac.wayback_snapshots("example.com", show_resume_key=True)
        assert isinstance(result, dict)
        assert len(result["snapshots"]) == 2
        assert result["resume_key"] == "resume_abc123"

    def test_show_resume_key_no_more_pages(self) -> None:
        """When no separator row, resume_key should be None."""
        cdx_no_resume = [
            ["urlkey", "timestamp"],
            ["com,example)/", "20200101"],
        ]

        def handler(request):
            return json_response(cdx_no_resume)

        ac = make_client(handler)
        result = ac.wayback_snapshots("example.com", show_resume_key=True)
        assert isinstance(result, dict)
        assert len(result["snapshots"]) == 1
        assert result["resume_key"] is None

    def test_show_resume_key_empty_response(self) -> None:
        def handler(request):
            return json_response([])

        ac = make_client(handler)
        result = ac.wayback_snapshots("example.com", show_resume_key=True)
        assert result == {"snapshots": [], "resume_key": None}

    def test_page_param_passed(self) -> None:
        seen: dict = {}

        def handler(request):
            seen["url"] = str(request.url)
            return json_response(CDX_RESPONSE)

        ac = make_client(handler)
        ac.wayback_snapshots("example.com", page=3)
        assert "page=3" in seen["url"]

    def test_resume_key_param_passed(self) -> None:
        seen: dict = {}

        def handler(request):
            seen["url"] = str(request.url)
            return json_response(CDX_RESPONSE)

        ac = make_client(handler)
        ac.wayback_snapshots("example.com", resume_key="abc123")
        assert "resumeKey=abc123" in seen["url"]

    def test_show_resume_key_param_passed(self) -> None:
        seen: dict = {}

        def handler(request):
            seen["url"] = str(request.url)
            return json_response(CDX_RESPONSE)

        ac = make_client(handler)
        ac.wayback_snapshots("example.com", show_resume_key=True)
        assert "showResumeKey=true" in seen["url"]

    def test_newest_param_passed(self) -> None:
        seen: dict = {}

        def handler(request):
            seen["url"] = str(request.url)
            return json_response(CDX_RESPONSE)

        ac = make_client(handler)
        ac.wayback_snapshots("example.com", newest=True)
        assert "newest=true" in seen["url"]

    def test_fast_latest_param_passed(self) -> None:
        seen: dict = {}

        def handler(request):
            seen["url"] = str(request.url)
            return json_response(CDX_RESPONSE)

        ac = make_client(handler)
        ac.wayback_snapshots("example.com", fast_latest=True)
        assert "fastLatest=true" in seen["url"]

    def test_default_returns_list_not_dict(self) -> None:
        """Backward compat: without show_resume_key, returns plain list."""
        def handler(request):
            return json_response(CDX_RESPONSE)

        ac = make_client(handler)
        result = ac.wayback_snapshots("example.com")
        assert isinstance(result, list)


class TestSavePageStatus:
    async def test_returns_status_dict(self) -> None:
        status_resp = {"status": "success", "original_url": "http://example.com"}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=status_resp)

        ac = make_client(handler)
        result = await ac.save_page_status("job123")
        assert result["status"] == "success"

    async def test_correct_url(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, json={"status": "pending"})

        ac = make_client(handler)
        await ac.save_page_status("job456")
        assert "/save/status/job456" in seen["url"]

    async def test_empty_job_id_raises(self) -> None:
        ac = make_client(lambda r: httpx.Response(200, json={}))
        with pytest.raises(ValueError, match="must not be empty"):
            await ac.save_page_status("")


class TestDownloadUrl:
    def test_files_include_download_url(self) -> None:
        def handler(request):
            return json_response(METADATA_RESPONSE)

        ac = make_client(handler)
        files = ac.list_item_files("test")
        assert files[0]["download_url"] == "https://archive.org/download/test/test.pdf"

    def test_download_url_with_format_filter(self) -> None:
        resp = {
            "metadata": {"identifier": "myitem"},
            "files": [
                {"name": "a.mp3", "format": "VBR MP3"},
                {"name": "b.pdf", "format": "Text PDF"},
            ],
            "files_count": 2,
        }

        def handler(request):
            return json_response(resp)

        ac = make_client(handler)
        files = ac.list_item_files("myitem", format_filter="VBR MP3")
        assert len(files) == 1
        assert files[0]["download_url"] == "https://archive.org/download/myitem/a.mp3"


class TestSearchArchiveWarning:
    def test_warning_when_numfound_exceeds_10000(self) -> None:
        big_response = {
            "response": {
                "numFound": 50000,
                "start": 0,
                "docs": [{"identifier": "x"}],
            }
        }

        def handler(request):
            return json_response(big_response)

        ac = make_client(handler)
        result = ac.search_archive("big query")
        assert "_warning" in result
        assert "10,000" in result["_warning"]
        assert "search_archive_deep" in result["_warning"]

    def test_no_warning_when_under_10000(self) -> None:
        def handler(request):
            return json_response(SEARCH_RESPONSE)

        ac = make_client(handler)
        result = ac.search_archive("small query")
        assert "_warning" not in result


class TestSearchArchiveDeepTotalOnly:
    def test_total_only_param_passed(self) -> None:
        seen: dict = {}

        def handler(request):
            seen["url"] = str(request.url)
            return json_response({"total": 42, "count": 0, "items": []})

        ac = make_client(handler)
        result = ac.search_archive_deep("test", total_only=True)
        assert "total_only=true" in seen["url"]
        assert result["total"] == 42

    def test_total_only_false_not_in_params(self) -> None:
        seen: dict = {}

        def handler(request):
            seen["url"] = str(request.url)
            return json_response({"items": [], "total": 0, "count": 0})

        ac = make_client(handler)
        ac.search_archive_deep("test", total_only=False)
        assert "total_only" not in seen["url"]


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
    def test_returns_response_dict(self) -> None:
        def handler(request):
            return json_response(AVAILABILITY_RESPONSE)

        ac = make_client(handler)
        result = ac.wayback_availability("example.com")
        assert result["url"] == "example.com"
        assert result["archived_snapshots"]["closest"]["available"] is True

    def test_empty_url_raises(self) -> None:
        ac = make_client(lambda r: json_response({}))
        with pytest.raises(ValueError, match="must not be empty"):
            ac.wayback_availability("")


class TestWaybackFetch:
    def test_returns_content(self) -> None:
        def handler(request):
            return text_response("<html>Hello</html>")

        ac = make_client(handler)
        result = ac.wayback_fetch("example.com", timestamp="20260101")
        assert result["content"] == "<html>Hello</html>"
        assert result["truncated"] is False
        assert result["content_length"] == len("<html>Hello</html>")

    def test_raw_adds_id_suffix(self) -> None:
        seen: dict = {}

        def handler(request):
            seen["url"] = str(request.url)
            return text_response("content")

        ac = make_client(handler)
        ac.wayback_fetch("example.com", timestamp="20260101", raw=True)
        assert "20260101id_" in seen["url"]

    def test_no_raw_no_id_suffix(self) -> None:
        seen: dict = {}

        def handler(request):
            seen["url"] = str(request.url)
            return text_response("content")

        ac = make_client(handler)
        ac.wayback_fetch("example.com", timestamp="20260101", raw=False)
        assert "id_" not in seen["url"]

    def test_truncation_works(self) -> None:
        long_content = "x" * 1000

        def handler(request):
            return text_response(long_content)

        ac = make_client(handler)
        result = ac.wayback_fetch("example.com", char_limit=100)
        assert result["truncated"] is True
        assert len(result["content"]) == 100
        assert result["content_length"] == 1000

    def test_empty_url_raises(self) -> None:
        ac = make_client(lambda r: text_response(""))
        with pytest.raises(ValueError, match="must not be empty"):
            ac.wayback_fetch("")


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
