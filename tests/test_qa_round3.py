"""Tests for QA round 3 fixes — systematic defensive typing, cache cap, docs."""

from __future__ import annotations

import pytest

from internet_archive_mcp.client import ArchiveClient
from conftest import make_mock_client, json_response, text_response


# ---------------------------------------------------------------------------
# Finding 1: CDX rows with non-list elements → TypeError
# ---------------------------------------------------------------------------


class TestCDXNonListRows:
    """CDX data rows that are not lists must be skipped, not crash."""

    def test_cdx_non_list_row_skipped(self):
        """Non-list row (e.g. string) in CDX data should be skipped."""
        def handler(request):
            return json_response([
                ["urlkey", "timestamp"],
                "not-a-list",  # non-list row
                ["com,example)/", "20200101"],  # valid
            ])

        ac = make_mock_client(handler)
        result = ac.wayback_snapshots("example.com")
        assert len(result) == 1
        assert result[0]["timestamp"] == "20200101"

    def test_cdx_dict_row_skipped(self):
        """Dict row in CDX data should be skipped."""
        def handler(request):
            return json_response([
                ["urlkey", "timestamp"],
                {"urlkey": "x", "timestamp": "y"},  # dict, not list
                ["com,example)/", "20200101"],
            ])

        ac = make_mock_client(handler)
        result = ac.wayback_snapshots("example.com")
        assert len(result) == 1

    def test_cdx_int_row_skipped(self):
        """Integer row in CDX data should be skipped."""
        def handler(request):
            return json_response([
                ["urlkey", "timestamp"],
                42,
                ["com,example)/", "20200101"],
            ])

        ac = make_mock_client(handler)
        result = ac.wayback_snapshots("example.com")
        assert len(result) == 1

    def test_cdx_non_list_row_show_resume_key(self):
        """Non-list rows with show_resume_key=True should also be skipped."""
        def handler(request):
            return json_response([
                ["urlkey", "timestamp"],
                "garbage",
                ["com,example)/", "20200101"],
            ])

        ac = make_mock_client(handler)
        result = ac.wayback_snapshots("example.com", show_resume_key=True)
        assert len(result["snapshots"]) == 1
        assert result["resume_key"] is None

    def test_cdx_all_non_list_rows(self):
        """All non-list rows → empty result."""
        def handler(request):
            return json_response([
                ["urlkey", "timestamp"],
                "bad",
                42,
                None,
            ])

        ac = make_mock_client(handler)
        result = ac.wayback_snapshots("example.com")
        assert result == []


# ---------------------------------------------------------------------------
# Finding 2: list_item_files — files non-list or entries non-dict
# ---------------------------------------------------------------------------


class TestListItemFilesDefensive:
    """list_item_files must handle non-list files and non-dict entries."""

    def test_files_non_list_string(self):
        """files key is a string → should return []."""
        def handler(request):
            return json_response({
                "metadata": {"identifier": "test"},
                "files": "not-a-list",
            })

        ac = make_mock_client(handler)
        result = ac.list_item_files("test")
        assert result == []

    def test_files_non_list_dict(self):
        """files key is a dict → should return []."""
        def handler(request):
            return json_response({
                "metadata": {"identifier": "test"},
                "files": {"name": "a.txt"},
            })

        ac = make_mock_client(handler)
        result = ac.list_item_files("test")
        assert result == []

    def test_files_entries_non_dict_skipped(self):
        """Non-dict entries in files list should be skipped."""
        def handler(request):
            return json_response({
                "metadata": {"identifier": "test"},
                "files": [
                    "just-a-string",
                    42,
                    {"name": "good.pdf", "format": "PDF"},
                    None,
                ],
            })

        ac = make_mock_client(handler)
        result = ac.list_item_files("test")
        assert len(result) == 1
        assert result[0]["name"] == "good.pdf"

    def test_files_entries_non_dict_with_filter(self):
        """Non-dict entries should be skipped even with format_filter."""
        def handler(request):
            return json_response({
                "metadata": {"identifier": "test"},
                "files": [
                    "bad",
                    {"name": "a.pdf", "format": "PDF"},
                    {"name": "b.mp3", "format": "MP3"},
                ],
            })

        ac = make_mock_client(handler)
        result = ac.list_item_files("test", format_filter="PDF")
        assert len(result) == 1
        assert result[0]["name"] == "a.pdf"


# ---------------------------------------------------------------------------
# Finding 3: search_archive_deep — no isinstance(data, dict) guard
# ---------------------------------------------------------------------------


class TestSearchArchiveDeepGuard:
    """search_archive_deep must guard against non-dict JSON responses."""

    def test_list_response_raises(self):
        """Scrape API returning a list → ValueError."""
        def handler(request):
            return json_response(["unexpected"])

        ac = make_mock_client(handler)
        with pytest.raises(ValueError, match="Unexpected API response format"):
            ac.search_archive_deep("test")

    def test_string_response_raises(self):
        """Scrape API returning a string → ValueError."""
        def handler(request):
            return json_response("error")

        ac = make_mock_client(handler)
        with pytest.raises(ValueError, match="Unexpected API response format"):
            ac.search_archive_deep("test")

    def test_int_response_raises(self):
        """Scrape API returning an int → ValueError."""
        def handler(request):
            return json_response(500)

        ac = make_mock_client(handler)
        with pytest.raises(ValueError, match="Unexpected API response format"):
            ac.search_archive_deep("test")

    def test_null_response_raises(self):
        """Scrape API returning null → ValueError."""
        def handler(request):
            return text_response("null", headers={"Content-Type": "application/json"})

        ac = make_mock_client(handler)
        with pytest.raises(ValueError, match="Unexpected API response format"):
            ac.search_archive_deep("test")


# ---------------------------------------------------------------------------
# Finding 4: search_archive — docs non-list or entries non-dict
# ---------------------------------------------------------------------------


class TestSearchArchiveDocsDefensive:
    """search_archive must handle non-list docs and non-dict entries."""

    def test_docs_non_list_string(self):
        """docs is a string → should be treated as []."""
        def handler(request):
            return json_response({
                "response": {"numFound": 0, "start": 0, "docs": "bad"}
            })

        ac = make_mock_client(handler)
        result = ac.search_archive("test")
        assert result["docs"] == []

    def test_docs_non_list_int(self):
        """docs is an int → should be treated as []."""
        def handler(request):
            return json_response({
                "response": {"numFound": 0, "start": 0, "docs": 42}
            })

        ac = make_mock_client(handler)
        result = ac.search_archive("test")
        assert result["docs"] == []

    def test_docs_entries_non_dict_skipped(self):
        """Non-dict entries in docs should be skipped during fav-* trim."""
        def handler(request):
            return json_response({
                "response": {
                    "numFound": 3,
                    "start": 0,
                    "docs": [
                        "string-entry",
                        {"identifier": "good", "collection": ["fav-x", "real"]},
                        42,
                    ],
                }
            })

        ac = make_mock_client(handler)
        result = ac.search_archive("test")
        # Non-dict entries should be skipped in the fav-* trim loop
        # but still present in docs (we only guard the loop, not filter docs)
        # Actually the fix is: guard the loop body with isinstance check
        # The docs list itself should still contain all entries
        # But the loop should not crash on non-dict entries
        assert result["numFound"] == 3


# ---------------------------------------------------------------------------
# Finding 5: wayback_availability, save_page, save_page_status — no guard
# ---------------------------------------------------------------------------


class TestAvailabilitySaveGuards:
    """wayback_availability, save_page, save_page_status need isinstance guards."""

    def test_availability_non_dict_raises(self):
        """wayback_availability with non-dict response -> ValueError."""
        def handler(request):
            return json_response(["unexpected"])

        ac = make_mock_client(handler)
        with pytest.raises(ValueError, match="Unexpected API response format"):
            ac.wayback_availability("http://example.com")

    def test_availability_string_raises(self):
        def handler(request):
            return json_response("error")

        ac = make_mock_client(handler)
        with pytest.raises(ValueError, match="Unexpected API response format"):
            ac.wayback_availability("http://example.com")

    def test_save_page_non_dict_raises(self):
        """save_page with non-dict response -> ValueError."""
        def handler(request):
            return json_response(["unexpected"])

        ac = make_mock_client(handler)
        with pytest.raises(ValueError, match="Unexpected API response format"):
            ac.save_page("http://example.com", access_key="ak", secret_key="sk")

    def test_save_page_status_non_dict_raises(self):
        """save_page_status with non-dict response -> ValueError."""
        def handler(request):
            return json_response("error string")

        ac = make_mock_client(handler)
        with pytest.raises(ValueError, match="Unexpected API response format"):
            ac.save_page_status("job123")

    def test_availability_dict_ok(self):
        """wayback_availability with valid dict should work."""
        def handler(request):
            return json_response({"archived_snapshots": {"closest": {"timestamp": "2020"}}})

        ac = make_mock_client(handler)
        result = ac.wayback_availability("http://example.com")
        assert "archived_snapshots" in result

    def test_save_page_dict_ok(self):
        """save_page with valid dict should work."""
        def handler(request):
            return json_response({"url": "http://example.com", "job_id": "abc"})

        ac = make_mock_client(handler)
        result = ac.save_page("http://example.com", access_key="ak", secret_key="sk")
        assert result["job_id"] == "abc"

    def test_save_page_status_dict_ok(self):
        """save_page_status with valid dict should work."""
        def handler(request):
            return json_response({"status": "success"})

        ac = make_mock_client(handler)
        result = ac.save_page_status("job123")
        assert result["status"] == "success"


# ---------------------------------------------------------------------------
# Finding 6: get_item_reviews — non-list truthy reviews
# ---------------------------------------------------------------------------


class TestGetItemReviewsDefensive:
    """get_item_reviews must handle non-list truthy reviews values."""

    def test_reviews_string_returns_empty(self):
        """reviews is a truthy string → should return []."""
        def handler(request):
            return json_response({
                "metadata": {"identifier": "test"},
                "reviews": "not-a-list",
            })

        ac = make_mock_client(handler)
        result = ac.get_item_reviews("test")
        assert result == []

    def test_reviews_dict_returns_empty(self):
        """reviews is a truthy dict → should return []."""
        def handler(request):
            return json_response({
                "metadata": {"identifier": "test"},
                "reviews": {"reviewer": "someone"},
            })

        ac = make_mock_client(handler)
        result = ac.get_item_reviews("test")
        assert result == []

    def test_reviews_int_returns_empty(self):
        """reviews is a truthy int → should return []."""
        def handler(request):
            return json_response({
                "metadata": {"identifier": "test"},
                "reviews": 42,
            })

        ac = make_mock_client(handler)
        result = ac.get_item_reviews("test")
        assert result == []

    def test_reviews_valid_list_returned(self):
        """Valid reviews list should be returned as-is."""
        reviews = [{"reviewer": "bob", "stars": 5}]

        def handler(request):
            return json_response({
                "metadata": {"identifier": "test"},
                "reviews": reviews,
            })

        ac = make_mock_client(handler)
        result = ac.get_item_reviews("test")
        assert result == reviews

    def test_reviews_none_returns_empty(self):
        """reviews=None → should return []."""
        def handler(request):
            return json_response({
                "metadata": {"identifier": "test"},
                "reviews": None,
            })

        ac = make_mock_client(handler)
        result = ac.get_item_reviews("test")
        assert result == []

    def test_reviews_absent_returns_empty(self):
        """No reviews key → should return []."""
        def handler(request):
            return json_response({"metadata": {"identifier": "test"}})

        ac = make_mock_client(handler)
        result = ac.get_item_reviews("test")
        assert result == []


# ---------------------------------------------------------------------------
# Finding 7: get_collection_info — metadata non-dict
# ---------------------------------------------------------------------------


class TestGetCollectionInfoDefensive:
    """get_collection_info must handle non-dict metadata."""

    def test_metadata_non_dict_string(self):
        """metadata is a string → should not crash with AttributeError."""
        def handler(request):
            return json_response({
                "metadata": "just-a-string",
            })

        ac = make_mock_client(handler)
        result = ac.get_collection_info("test")
        # Should add _note since mediatype != collection
        assert "_note" in result

    def test_metadata_non_dict_list(self):
        """metadata is a list → should not crash."""
        def handler(request):
            return json_response({
                "metadata": ["unexpected"],
            })

        ac = make_mock_client(handler)
        result = ac.get_collection_info("test")
        assert "_note" in result

    def test_metadata_non_dict_int(self):
        """metadata is an int → should not crash."""
        def handler(request):
            return json_response({
                "metadata": 42,
            })

        ac = make_mock_client(handler)
        result = ac.get_collection_info("test")
        assert "_note" in result

    def test_metadata_absent(self):
        """No metadata key → should not crash."""
        def handler(request):
            return json_response({"server": "x"})

        ac = make_mock_client(handler)
        result = ac.get_collection_info("test")
        assert "_note" in result

    def test_metadata_valid_collection_no_note(self):
        """Valid collection metadata → no _note."""
        def handler(request):
            return json_response({
                "metadata": {"identifier": "test", "mediatype": "collection"},
            })

        ac = make_mock_client(handler)
        result = ac.get_collection_info("test")
        assert "_note" not in result


# ---------------------------------------------------------------------------
# Finding 8: wayback_fetch — unbounded cache
# ---------------------------------------------------------------------------


class TestWaybackFetchCacheCap:
    """wayback_fetch must not cache content larger than 2MB."""

    def test_small_content_cached(self):
        """Content under 2MB should be cached normally."""
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return text_response("small content")

        ac = make_mock_client(handler)
        r1 = ac.wayback_fetch("http://example.com", timestamp="20200101")
        r2 = ac.wayback_fetch("http://example.com", timestamp="20200101")
        assert call_count == 1  # cached
        assert r1["content"] == r2["content"]

    def test_large_content_not_cached(self):
        """Content over 2MB should NOT be cached but still returned."""
        call_count = 0
        large_content = "X" * (2 * 1024 * 1024 + 1)  # 2MB + 1 byte

        def handler(request):
            nonlocal call_count
            call_count += 1
            return text_response(large_content)

        ac = make_mock_client(handler)
        r1 = ac.wayback_fetch("http://example.com", timestamp="20200101")
        r2 = ac.wayback_fetch("http://example.com", timestamp="20200101")
        assert call_count == 2  # NOT cached — two HTTP calls
        assert r1["content_length"] == len(large_content)
        assert r2["content_length"] == len(large_content)

    def test_exactly_2mb_cached(self):
        """Content exactly 2MB should be cached (at the boundary)."""
        call_count = 0
        content_2mb = "Y" * (2 * 1024 * 1024)  # exactly 2MB

        def handler(request):
            nonlocal call_count
            call_count += 1
            return text_response(content_2mb)

        ac = make_mock_client(handler)
        r1 = ac.wayback_fetch("http://example.com", timestamp="20200101")
        r2 = ac.wayback_fetch("http://example.com", timestamp="20200101")
        assert call_count == 1  # cached at boundary
        assert r1["content_length"] == 2 * 1024 * 1024

    def test_large_content_still_returned_full(self):
        """Large content should still be returned to the caller in full."""
        large_content = "Z" * (2 * 1024 * 1024 + 100)

        def handler(request):
            return text_response(large_content)

        ac = make_mock_client(handler)
        result = ac.wayback_fetch(
            "http://example.com", timestamp="20200101", char_limit=len(large_content) + 1000
        )
        assert result["content_length"] == len(large_content)
        assert len(result["content"]) == len(large_content)
        assert result["truncated"] is False
