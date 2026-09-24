"""Tests for QA round 1 fixes — malformed responses, validation, caching, URL encoding."""

from __future__ import annotations

import pytest

from conftest import json_response, make_mock_client, text_response


# ---------------------------------------------------------------------------
# Finding 21: Malformed API response tests
# ---------------------------------------------------------------------------


class TestMalformedResponses:
    """Tests for malformed/unexpected API response shapes."""

    def test_file_entry_missing_name_key(self):
        """File without 'name' key should not crash; download_url omitted."""
        def handler(request):
            return json_response({
                "metadata": {"identifier": "test-item"},
                "files": [
                    {"format": "PDF", "size": "123"},  # no 'name'
                    {"name": "good.pdf", "format": "PDF"},
                ],
            })

        ac = make_mock_client(handler)
        files = ac.list_item_files("test-item")
        assert len(files) == 2
        # File without name should NOT have download_url
        assert "download_url" not in files[0]
        # File with name should have it
        assert "download_url" in files[1]

    def test_cdx_row_shorter_than_header(self):
        """CDX row with fewer columns than header should be skipped."""
        def handler(request):
            return json_response([
                ["urlkey", "timestamp", "original", "statuscode"],
                ["com,example)/", "20200101"],  # too short — should be skipped
                ["com,example)/", "20200102", "http://example.com/", "200"],  # valid
            ])

        ac = make_mock_client(handler)
        result = ac.wayback_snapshots("example.com")
        assert len(result) == 1
        assert result[0]["timestamp"] == "20200102"

    def test_cdx_row_longer_than_header(self):
        """CDX row with more columns than header should be skipped."""
        def handler(request):
            return json_response([
                ["urlkey", "timestamp"],
                ["com,example)/", "20200101", "EXTRA", "COLS"],  # too long
                ["com,example)/", "20200102"],  # valid
            ])

        ac = make_mock_client(handler)
        result = ac.wayback_snapshots("example.com")
        assert len(result) == 1
        assert result[0]["timestamp"] == "20200102"

    def test_search_response_missing_response_key(self):
        """Search response without 'response' key returns empty results."""
        def handler(request):
            return json_response({"responseHeader": {"status": 0}})

        ac = make_mock_client(handler)
        result = ac.search_archive("test")
        assert result["numFound"] == 0
        assert result["docs"] == []
        assert result["total_pages"] == 0

    def test_metadata_response_missing_metadata_key(self):
        """Metadata response without 'metadata' key still returns the data."""
        def handler(request):
            return json_response({"files": [{"name": "a.txt"}], "server": "x"})

        ac = make_mock_client(handler)
        result = ac.get_item_metadata("test-item")
        # Should not crash; returns whatever the API gave
        assert "server" in result


# ---------------------------------------------------------------------------
# Finding 1 & 2: Cache pollution tests
# ---------------------------------------------------------------------------


class TestCachePollution:
    def test_list_item_files_does_not_pollute_metadata_cache(self):
        """list_item_files adding download_url must not leak into cached metadata."""
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return json_response({
                "metadata": {"identifier": "test-item"},
                "files": [{"name": "file1.mp3", "format": "VBR MP3"}],
            })

        ac = make_mock_client(handler)
        # First: list_item_files (adds download_url)
        files = ac.list_item_files("test-item")
        assert "download_url" in files[0]

        # Second: get_item_metadata (should NOT have download_url)
        meta = ac.get_item_metadata("test-item", include_files=True)
        assert call_count == 1  # cache hit
        assert "download_url" not in meta["files"][0]

    def test_get_collection_info_does_not_pollute_metadata_cache(self):
        """get_collection_info adding _note must not leak into cached metadata."""
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return json_response({
                "metadata": {"identifier": "test-item", "mediatype": "texts"},
            })

        ac = make_mock_client(handler)
        # First: get_collection_info (adds _note since mediatype != collection)
        info = ac.get_collection_info("test-item")
        assert "_note" in info

        # Second: get_item_metadata (should NOT have _note)
        meta = ac.get_item_metadata("test-item")
        assert call_count == 1  # cache hit
        assert "_note" not in meta


# ---------------------------------------------------------------------------
# Finding 3: wayback_fetch double-slash URL
# ---------------------------------------------------------------------------


class TestWaybackFetchUrl:
    def test_no_double_slash_without_timestamp(self):
        """raw=False, no timestamp → /web/http://... (not /web//http://...)."""
        seen_urls: list[str] = []

        def handler(request):
            seen_urls.append(str(request.url))
            return text_response("content")

        ac = make_mock_client(handler)
        ac.wayback_fetch("http://example.com", raw=False)
        # Should be /web/http:// not /web//http://
        assert "/web/http" in seen_urls[0]
        assert "/web//http" not in seen_urls[0]

    def test_raw_no_timestamp_uses_id_prefix(self):
        """raw=True, no timestamp → /web/id_/url."""
        seen_urls: list[str] = []

        def handler(request):
            seen_urls.append(str(request.url))
            return text_response("content")

        ac = make_mock_client(handler)
        ac.wayback_fetch("http://example.com", raw=True)
        assert "/web/id_/http" in seen_urls[0]


# ---------------------------------------------------------------------------
# Finding 4: Negative char_limit validation
# ---------------------------------------------------------------------------


class TestCharLimitValidation:
    def test_negative_char_limit_raises(self):
        ac = make_mock_client(lambda r: text_response("hello"))
        with pytest.raises(ValueError, match="char_limit must be >= 1"):
            ac.wayback_fetch("http://example.com", char_limit=-5)

    def test_zero_char_limit_raises(self):
        ac = make_mock_client(lambda r: text_response("hello"))
        with pytest.raises(ValueError, match="char_limit must be >= 1"):
            ac.wayback_fetch("http://example.com", char_limit=0)


# ---------------------------------------------------------------------------
# Finding 6: URL encoding
# ---------------------------------------------------------------------------


class TestUrlEncoding:
    def test_thumbnail_url_encodes_spaces(self):
        ac = make_mock_client(lambda r: json_response({}))
        url = ac.get_item_thumbnail_url("my item")
        assert " " not in url
        assert "my%20item" in url

    def test_thumbnail_url_encodes_slashes(self):
        ac = make_mock_client(lambda r: json_response({}))
        url = ac.get_item_thumbnail_url("a/b")
        assert url == "https://archive.org/services/img/a%2Fb"

    def test_download_url_encodes_special_chars(self):
        def handler(request):
            return json_response({
                "metadata": {"identifier": "test item"},
                "files": [{"name": "my file.pdf", "format": "PDF"}],
            })

        ac = make_mock_client(handler)
        files = ac.list_item_files("test item")
        assert " " not in files[0]["download_url"]
        assert "test%20item" in files[0]["download_url"]
        assert "my%20file.pdf" in files[0]["download_url"]


# ---------------------------------------------------------------------------
# Finding 7: match_type validation
# ---------------------------------------------------------------------------


class TestMatchTypeValidation:
    def test_invalid_match_type_raises(self):
        ac = make_mock_client(lambda r: json_response([]))
        with pytest.raises(ValueError, match="match_type must be one of"):
            ac.wayback_snapshots("example.com", match_type="foobar")

    def test_valid_match_types_accepted(self):
        def handler(request):
            return json_response([["timestamp"], ["20200101"]])

        ac = make_mock_client(handler)
        # These should not raise
        ac.wayback_snapshots("example.com", match_type="exact")
        ac.wayback_snapshots("example.com", match_type="prefix")
        ac.wayback_snapshots("example.com", match_type="host")


# ---------------------------------------------------------------------------
# Finding 8: rows/limit/page validation
# ---------------------------------------------------------------------------


class TestParameterValidation:
    def test_zero_rows_raises(self):
        ac = make_mock_client(lambda r: json_response({}))
        with pytest.raises(ValueError, match="rows must be >= 1"):
            ac.search_archive("test", rows=0)

    def test_negative_rows_raises(self):
        ac = make_mock_client(lambda r: json_response({}))
        with pytest.raises(ValueError, match="rows must be >= 1"):
            ac.search_archive("test", rows=-5)

    def test_zero_page_raises(self):
        ac = make_mock_client(lambda r: json_response({}))
        with pytest.raises(ValueError, match="page must be >= 1"):
            ac.search_archive("test", page=0)

    def test_negative_limit_raises(self):
        ac = make_mock_client(lambda r: json_response([]))
        with pytest.raises(ValueError, match="limit must be >= 1"):
            ac.wayback_snapshots("example.com", limit=-5)

    def test_zero_limit_raises(self):
        ac = make_mock_client(lambda r: json_response([]))
        with pytest.raises(ValueError, match="limit must be >= 1"):
            ac.wayback_snapshots("example.com", limit=0)


# ---------------------------------------------------------------------------
# Finding 9: sorts ordering auto-fix
# ---------------------------------------------------------------------------


class TestSortsOrdering:
    def test_identifier_moved_to_end(self):
        seen_urls: list[str] = []

        def handler(request):
            seen_urls.append(str(request.url))
            return json_response({"items": [], "total": 0, "count": 0})

        ac = make_mock_client(handler)
        result = ac.search_archive_deep(
            "test", sorts=["identifier", "downloads desc"]
        )
        # identifier should be last in the sorts param
        url = seen_urls[0]
        assert "downloads+desc" in url or "downloads%20desc" in url
        # Result should have a note about reordering
        assert "_note" in result
        assert "identifier" in result["_note"]

    def test_identifier_already_last_no_note(self):
        def handler(request):
            return json_response({"items": [], "total": 0, "count": 0})

        ac = make_mock_client(handler)
        result = ac.search_archive_deep(
            "test", sorts=["downloads desc", "identifier"]
        )
        assert "_note" not in result


# ---------------------------------------------------------------------------
# Finding 18: Caching for wayback_snapshots and wayback_availability
# ---------------------------------------------------------------------------


class TestWaybackCaching:
    def test_wayback_snapshots_cached(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return json_response([["timestamp"], ["20200101"]])

        ac = make_mock_client(handler)
        r1 = ac.wayback_snapshots("example.com")
        r2 = ac.wayback_snapshots("example.com")
        assert call_count == 1
        assert r1 == r2

    def test_wayback_availability_cached(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return json_response({"archived_snapshots": {}})

        ac = make_mock_client(handler)
        r1 = ac.wayback_availability("http://example.com")
        r2 = ac.wayback_availability("http://example.com")
        assert call_count == 1
        assert r1 == r2


# ---------------------------------------------------------------------------
# Finding 16: __version__ export
# ---------------------------------------------------------------------------


class TestVersion:
    def test_version_defined(self):
        import internet_archive_mcp
        assert internet_archive_mcp.__version__ == "0.3.0"
