"""Tests for QA round 2 fixes — malformed response guards, validation gaps, caching, docs."""

from __future__ import annotations

import urllib.error

import pytest

from internet_archive_mcp.client import ArchiveClient
from internet_archive_mcp.server import handle_call
from conftest import make_mock_client, json_response, text_response


# ---------------------------------------------------------------------------
# Findings 1-3: CDX malformed response guards
# ---------------------------------------------------------------------------


class TestCDXMalformedResponses:
    """CDX endpoint returning non-list JSON must not crash or produce garbage."""

    def test_cdx_dict_response_returns_empty(self):
        """Finding 1: CDX returning a JSON dict → should return [], not KeyError."""
        def handler(request):
            return json_response({"error": "something went wrong"})

        ac = make_mock_client(handler)
        result = ac.wayback_snapshots("example.com")
        assert result == []

    def test_cdx_dict_response_show_resume_key(self):
        """Finding 1b: CDX dict with show_resume_key → empty snapshots dict."""
        def handler(request):
            return json_response({"error": "oops"})

        ac = make_mock_client(handler)
        result = ac.wayback_snapshots("example.com", show_resume_key=True)
        assert result == {"snapshots": [], "resume_key": None}

    def test_cdx_integer_response_returns_empty(self):
        """Finding 2: CDX returning an integer → should return [], not TypeError."""
        def handler(request):
            return json_response(42)

        ac = make_mock_client(handler)
        result = ac.wayback_snapshots("example.com")
        assert result == []

    def test_cdx_string_response_returns_empty(self):
        """Finding 3: CDX returning a string → should return [], not garbage dicts."""
        def handler(request):
            return json_response("some error message")

        ac = make_mock_client(handler)
        result = ac.wayback_snapshots("example.com")
        assert result == []

    def test_cdx_null_response_returns_empty(self):
        """CDX returning null → should return []."""
        def handler(request):
            return (200, {"Content-Type": "application/json"}, b"null")

        ac = make_mock_client(handler)
        result = ac.wayback_snapshots("example.com")
        assert result == []


# ---------------------------------------------------------------------------
# Finding 4: search_archive non-dict response
# ---------------------------------------------------------------------------


class TestSearchArchiveMalformedResponse:
    def test_list_response_raises_valueerror(self):
        """Finding 4: advancedsearch returning a list → ValueError, not AttributeError."""
        def handler(request):
            return json_response(["unexpected", "list"])

        ac = make_mock_client(handler)
        with pytest.raises(ValueError, match="Unexpected API response format"):
            ac.search_archive("test")

    def test_string_response_raises_valueerror(self):
        """advancedsearch returning a string → ValueError."""
        def handler(request):
            return json_response("error string")

        ac = make_mock_client(handler)
        with pytest.raises(ValueError, match="Unexpected API response format"):
            ac.search_archive("test")

    def test_integer_response_raises_valueerror(self):
        """advancedsearch returning an int → ValueError."""
        def handler(request):
            return json_response(500)

        ac = make_mock_client(handler)
        with pytest.raises(ValueError, match="Unexpected API response format"):
            ac.search_archive("test")


# ---------------------------------------------------------------------------
# Finding 5: get_item_metadata non-dict response
# ---------------------------------------------------------------------------


class TestMetadataMalformedResponse:
    def test_list_response_raises_valueerror(self):
        """Finding 5: metadata returning a list → ValueError, not TypeError."""
        def handler(request):
            return json_response(["unexpected"])

        ac = make_mock_client(handler)
        with pytest.raises(ValueError, match="Unexpected API response format"):
            ac.get_item_metadata("test-item")

    def test_string_response_raises_valueerror(self):
        """metadata returning a string → ValueError."""
        def handler(request):
            return json_response("not a dict")

        ac = make_mock_client(handler)
        with pytest.raises(ValueError, match="Unexpected API response format"):
            ac.get_item_metadata("test-item")


# ---------------------------------------------------------------------------
# Finding 6: wayback_snapshots page validation
# ---------------------------------------------------------------------------


class TestWaybackSnapshotsPageValidation:
    def test_page_zero_raises(self):
        """Finding 6: page=0 should raise ValueError."""
        ac = make_mock_client(lambda r: json_response([]))
        with pytest.raises(ValueError, match="page must be >= 1"):
            ac.wayback_snapshots("example.com", page=0)

    def test_page_negative_raises(self):
        """Finding 6: page=-1 should raise ValueError."""
        ac = make_mock_client(lambda r: json_response([]))
        with pytest.raises(ValueError, match="page must be >= 1"):
            ac.wayback_snapshots("example.com", page=-1)

    def test_page_none_ok(self):
        """page=None (default) should not raise."""
        def handler(request):
            return json_response([["timestamp"], ["20200101"]])

        ac = make_mock_client(handler)
        result = ac.wayback_snapshots("example.com", page=None)
        assert len(result) == 1

    def test_page_one_ok(self):
        """page=1 should work fine."""
        def handler(request):
            return json_response([["timestamp"], ["20200101"]])

        ac = make_mock_client(handler)
        result = ac.wayback_snapshots("example.com", page=1)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Finding 7: sorts deduplication
# ---------------------------------------------------------------------------


class TestSortsDeduplication:
    def test_duplicate_identifier_deduped(self):
        """Finding 7: duplicate 'identifier' in sorts should be deduped."""
        seen_urls: list[str] = []

        def handler(request):
            seen_urls.append(str(request.url))
            return json_response({"items": [], "total": 0, "count": 0})

        ac = make_mock_client(handler)
        result = ac.search_archive_deep(
            "test", sorts=["identifier", "downloads desc", "identifier"]
        )
        url = seen_urls[0]
        # sorts param should have identifier only once
        # The URL-encoded sorts param: identifier,downloads+desc,identifier → deduped
        assert url.count("identifier") == 1 or "identifier%2Cdownloads" in url

    def test_duplicate_non_identifier_deduped(self):
        """Duplicate non-identifier sorts should also be deduped."""
        seen_urls: list[str] = []

        def handler(request):
            seen_urls.append(str(request.url))
            return json_response({"items": [], "total": 0, "count": 0})

        ac = make_mock_client(handler)
        ac.search_archive_deep(
            "test", sorts=["downloads desc", "downloads desc", "identifier"]
        )
        url = seen_urls[0]
        # "downloads desc" should appear only once in the sorts param
        # Count occurrences of "downloads" in the URL
        assert url.count("downloads") == 1


# ---------------------------------------------------------------------------
# Finding 8: sort cache key uses truncated list
# ---------------------------------------------------------------------------


class TestSortCacheKey:
    def test_sort_cache_key_uses_truncated_list(self):
        """Finding 8: sort[:3] for API and cache key should match → no unnecessary misses."""
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return json_response({
                "response": {"numFound": 1, "start": 0, "docs": [{"identifier": "x"}]}
            })

        ac = make_mock_client(handler)
        sort_4 = ["a asc", "b desc", "c asc", "d desc"]
        sort_3 = ["a asc", "b desc", "c asc"]

        r1 = ac.search_archive("test", sort=sort_4)
        r2 = ac.search_archive("test", sort=sort_3)
        # Both should hit the same cache entry since API only gets first 3
        assert call_count == 1
        assert r1 == r2


# ---------------------------------------------------------------------------
# Finding 10: save_page_status job_id URL encoding
# ---------------------------------------------------------------------------


class TestSavePageStatusEncoding:
    def test_job_id_url_encoded(self):
        """Finding 10: job_id with special chars should be URL-encoded."""
        seen_urls: list[str] = []

        def handler(request):
            seen_urls.append(str(request.url))
            return json_response({"status": "success"})

        ac = make_mock_client(handler)
        ac.save_page_status("job/123 test")
        url = seen_urls[0]
        assert "job/123 test" not in url
        assert "job%2F123%20test" in url


# ---------------------------------------------------------------------------
# Finding 11: wayback_fetch caching
# ---------------------------------------------------------------------------


class TestWaybackFetchCaching:
    def test_wayback_fetch_cached(self):
        """Finding 11: wayback_fetch should cache results."""
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return text_response("archived content here")

        ac = make_mock_client(handler)
        r1 = ac.wayback_fetch("http://example.com", timestamp="20200101")
        r2 = ac.wayback_fetch("http://example.com", timestamp="20200101")
        assert call_count == 1
        assert r1["content"] == r2["content"]

    def test_wayback_fetch_different_char_limits_share_cache(self):
        """Different char_limits should share the same cache entry (truncate on read)."""
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return text_response("A" * 100)

        ac = make_mock_client(handler)
        r1 = ac.wayback_fetch("http://example.com", timestamp="20200101", char_limit=50)
        r2 = ac.wayback_fetch("http://example.com", timestamp="20200101", char_limit=80)
        assert call_count == 1  # same cache entry
        assert len(r1["content"]) == 50
        assert r1["truncated"] is True
        assert len(r2["content"]) == 80
        assert r2["truncated"] is True
        assert r1["content_length"] == 100
        assert r2["content_length"] == 100

    def test_wayback_fetch_different_urls_not_cached(self):
        """Different URLs should not share cache."""
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return text_response(f"content {call_count}")

        ac = make_mock_client(handler)
        ac.wayback_fetch("http://example.com/a", timestamp="20200101")
        ac.wayback_fetch("http://example.com/b", timestamp="20200101")
        assert call_count == 2

    def test_wayback_fetch_full_content_cached_truncated_on_read(self):
        """Cache stores full content; char_limit=large gets full content."""
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return text_response("B" * 200)

        ac = make_mock_client(handler)
        r1 = ac.wayback_fetch("http://example.com", timestamp="20200101", char_limit=10)
        r2 = ac.wayback_fetch("http://example.com", timestamp="20200101", char_limit=50000)
        assert call_count == 1
        assert len(r1["content"]) == 10
        assert r1["truncated"] is True
        assert len(r2["content"]) == 200
        assert r2["truncated"] is False


# ---------------------------------------------------------------------------
# Defense in depth: server tool except clauses (T15b — ported from T15a)
# ---------------------------------------------------------------------------
# Post-T07 these folds are sync and live in the era server's handler functions.
# We exercise them via the REAL era dispatch (handle_call) with a BrokenClient
# swapped into server_module._client — the same attribute the legacy tests
# patched (legacy was framework-managed coroutines; the era fold is plain sync).


class TestServerExceptionHandling:
    """Server tools should catch KeyError, TypeError, AttributeError too."""

    def test_search_archive_catches_attribute_error(self, monkeypatch):
        """Server search_archive should return error string on AttributeError."""
        import internet_archive_mcp.server as srv

        class BrokenClient:
            def search_archive(self, *a, **kw):
                raise AttributeError("mock broken")

        original = srv._client
        monkeypatch.setattr(srv, "_client", BrokenClient())
        result = handle_call("search_archive", {"query": "test"})
        assert isinstance(result, str)
        assert "Error:" in result

    def test_get_item_metadata_catches_type_error(self, monkeypatch):
        """Server get_item_metadata should return error string on TypeError."""
        import internet_archive_mcp.server as srv

        class BrokenClient:
            def get_item_metadata(self, *a, **kw):
                raise TypeError("mock broken")

        original = srv._client
        monkeypatch.setattr(srv, "_client", BrokenClient())
        result = handle_call("get_item_metadata", {"identifier": "test"})
        assert isinstance(result, str)
        assert "Error:" in result

    def test_wayback_snapshots_catches_key_error(self, monkeypatch):
        """Server wayback_snapshots should return error string on KeyError."""
        import internet_archive_mcp.server as srv

        class BrokenClient:
            def wayback_snapshots(self, *a, **kw):
                raise KeyError(0)

        original = srv._client
        monkeypatch.setattr(srv, "_client", BrokenClient())
        result = handle_call("wayback_snapshots", {"url": "example.com"})
        assert isinstance(result, str)
        assert "Error:" in result


# ---------------------------------------------------------------------------
# R1 handler-fold pins (T15b — formal home of R1's handler-level pin)
# ---------------------------------------------------------------------------
# Seam (T01) and client (T09) already prove TimeoutError → URLError at the
# transport/client layers. These pins prove the SERVER layer's except clause
# folds the same errors into the legacy "Error: API request failed —" text —
# the defensive surface that catches anything escaping the client.


def test_urllib_urlerror_folds_to_api_request_failed(monkeypatch):
    """R1 handler pin (URLError): client raising urllib.error.URLError must reach
    the handler's transport-error fold → 'Error: API request failed —' prefix,
    never escape to a -32603."""
    import internet_archive_mcp.server as srv

    class BrokenClient:
        def search_archive(self, *a, **kw):
            raise urllib.error.URLError("boom")

    monkeypatch.setattr(srv, "_client", BrokenClient())
    result = handle_call("search_archive", {"query": "test"})
    assert isinstance(result, str)
    assert result.startswith("Error: API request failed —")


def test_read_timeout_folds_to_api_request_failed_at_handler(monkeypatch):
    """R1 handler pin (TimeoutError): a client raising TimeoutError directly
    (bypassing the seam — defensive surface) must land in the SAME
    'Error: API request failed —' fold, never leak as a raw TimeoutError."""
    import internet_archive_mcp.server as srv

    class BrokenClient:
        def wayback_fetch(self, *a, **kw):
            raise TimeoutError("read timed out")

    monkeypatch.setattr(srv, "_client", BrokenClient())
    result = handle_call("wayback_fetch", {"url": "http://example.com"})
    assert isinstance(result, str)
    assert result.startswith("Error: API request failed —")
