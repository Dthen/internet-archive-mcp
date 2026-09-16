"""Tests for MCP tool functions in the server module.

Ported to era dispatch (T17a): each test drives the public ``handle_call`` entry
point and asserts on the WIRE payload (``payload["content"]`` blocks), not the
Python function return value. This strengthens the suite from "python function
returns object" to "wire text bytes correct" — exactly what the
golden/legacy-behavior.json shapes pin.
"""

from __future__ import annotations

import json

import internet_archive_mcp.server as server_module
from conftest import make_mock_client, json_response, text_response


def _call(name, **args):
    """Call a tool via the public dispatch entry point and return the wire payload.

    Replicates the tools/call arm in ``server.main()``: ``handle_call`` returns
    the raw handler result; ``encode_tool_result`` converts it to content blocks;
    ``isError`` fires only for ``{"error": ...}`` dicts (unknown-tool dispatch),
    never for "Error: ..." strings (legacy probe-verified ``isError: False``).
    """
    result = server_module.handle_call(name, args)
    is_err = isinstance(result, dict) and ("error" in result or "transport_error" in result)
    content = server_module.encode_tool_result(result)
    payload = {"content": content}
    if is_err:
        payload["isError"] = True
    return payload


# ---------------------------------------------------------------------------
# Tier 1 — Core tools
# ---------------------------------------------------------------------------


class TestSearchArchiveTool:
    def test_returns_results(self, monkeypatch):
        def handler(request):
            return json_response({
                "response": {
                    "numFound": 1,
                    "start": 0,
                    "docs": [{"identifier": "test-item", "title": "Test"}],
                }
            })

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        payload = _call("search_archive", query="test query")
        result = json.loads(payload["content"][0]["text"])
        assert isinstance(result, dict)
        assert result["numFound"] == 1
        assert len(result["docs"]) == 1

    def test_empty_query_returns_error(self, monkeypatch):
        monkeypatch.setattr(server_module, "_client", make_mock_client(lambda r: json_response({})))
        payload = _call("search_archive", query="")
        text = payload["content"][0]["text"]
        assert text.startswith("Error:")

    def test_api_error_returns_error_string(self, monkeypatch):
        def handler(request):
            return (500, {}, b"Internal Server Error")

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        payload = _call("search_archive", query="test")
        text = payload["content"][0]["text"]
        assert text.startswith("Error:")


class TestGetItemMetadataTool:
    def test_returns_dict(self, monkeypatch):
        def handler(request):
            return json_response({
                "metadata": {"identifier": "test-item", "title": "Test Item"},
            })

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        payload = _call("get_item_metadata", identifier="test-item")
        result = json.loads(payload["content"][0]["text"])
        assert isinstance(result, dict)
        assert result["metadata"]["identifier"] == "test-item"

    def test_not_found_returns_error(self, monkeypatch):
        def handler(request):
            return json_response({})

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        payload = _call("get_item_metadata", identifier="nonexistent")
        text = payload["content"][0]["text"]
        assert "Error:" in text


class TestListItemFilesTool:
    def test_returns_list(self, monkeypatch):
        def handler(request):
            return json_response({
                "metadata": {"identifier": "test-item"},
                "files": [
                    {"name": "file1.mp3", "format": "VBR MP3"},
                    {"name": "file2.pdf", "format": "Text PDF"},
                ],
            })

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        payload = _call("list_item_files", identifier="test-item")
        result = json.loads(payload["content"][0]["text"])
        assert isinstance(result, list)
        assert len(result) == 2

    def test_format_filter_works(self, monkeypatch):
        def handler(request):
            return json_response({
                "metadata": {"identifier": "test-item"},
                "files": [
                    {"name": "file1.mp3", "format": "VBR MP3"},
                    {"name": "file2.pdf", "format": "Text PDF"},
                ],
            })

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        payload = _call("list_item_files", identifier="test-item", format_filter="VBR MP3")
        result = json.loads(payload["content"][0]["text"])
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["name"] == "file1.mp3"


class TestGetItemReviewsTool:
    def test_returns_list(self, monkeypatch):
        def handler(request):
            return json_response({
                "metadata": {"identifier": "test-item"},
                "reviews": [
                    {"reviewer": "alice", "stars": 5, "reviewtitle": "Great"},
                ],
            })

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        payload = _call("get_item_reviews", identifier="test-item")
        result = json.loads(payload["content"][0]["text"])
        assert isinstance(result, list)
        assert len(result) == 1

    def test_empty_reviews_returns_empty_list(self, monkeypatch):
        """R3 tagged branch: empty-list result emits ``content: []`` (zero blocks).

        Cross-ref golden/legacy-behavior.json case ``snapshots_empty``.
        """
        def handler(request):
            return json_response({
                "metadata": {"identifier": "test-item"},
            })

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        payload = _call("get_item_reviews", identifier="test-item")
        assert payload["content"] == []


class TestWaybackSnapshotsTool:
    def test_returns_list(self, monkeypatch):
        def handler(request):
            return json_response([
                ["urlkey", "timestamp", "original", "statuscode"],
                ["com,example)/", "20200101", "http://example.com/", "200"],
            ])

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        payload = _call("wayback_snapshots", url="example.com")
        result = json.loads(payload["content"][0]["text"])
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["statuscode"] == "200"

    def test_domain_match_type_returns_error(self, monkeypatch):
        monkeypatch.setattr(server_module, "_client", make_mock_client(lambda r: json_response([])))
        payload = _call("wayback_snapshots", url="example.com", match_type="domain")
        text = payload["content"][0]["text"]
        assert "Error:" in text


class TestWaybackAvailabilityTool:
    def test_returns_dict(self, monkeypatch):
        def handler(request):
            return json_response({
                "archived_snapshots": {
                    "closest": {
                        "timestamp": "20200101120000",
                        "url": "http://web.archive.org/web/20200101120000/http://example.com",
                    }
                }
            })

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        payload = _call("wayback_availability", url="http://example.com")
        result = json.loads(payload["content"][0]["text"])
        assert isinstance(result, dict)
        assert "archived_snapshots" in result


class TestWaybackFetchTool:
    def test_returns_content_dict(self, monkeypatch):
        def handler(request):
            return text_response("<html>Hello World</html>")

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        payload = _call("wayback_fetch", url="http://example.com")
        result = json.loads(payload["content"][0]["text"])
        assert isinstance(result, dict)
        assert "content" in result
        assert result["truncated"] is False

    def test_truncated_flag(self, monkeypatch):
        def handler(request):
            return text_response("x" * 100000)

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        payload = _call("wayback_fetch", url="http://example.com", char_limit=100)
        result = json.loads(payload["content"][0]["text"])
        assert isinstance(result, dict)
        assert result["truncated"] is True
        assert len(result["content"]) == 100
        assert result["content_length"] == 100000


# ---------------------------------------------------------------------------
# Tier 2 — Differentiator tools
# ---------------------------------------------------------------------------


class TestBrowseCollectionTool:
    def test_returns_results(self, monkeypatch):
        def handler(request):
            return json_response({
                "response": {
                    "numFound": 2,
                    "start": 0,
                    "docs": [
                        {"identifier": "item1", "title": "Item 1"},
                        {"identifier": "item2", "title": "Item 2"},
                    ],
                }
            })

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        payload = _call("browse_collection", collection="opensource_audio")
        result = json.loads(payload["content"][0]["text"])
        assert isinstance(result, dict)
        assert result["numFound"] == 2

    def test_empty_collection_returns_error(self, monkeypatch):
        monkeypatch.setattr(server_module, "_client", make_mock_client(lambda r: json_response({})))
        payload = _call("browse_collection", collection="")
        text = payload["content"][0]["text"]
        assert "Error:" in text


class TestGetCollectionInfoTool:
    def test_returns_metadata(self, monkeypatch):
        def handler(request):
            return json_response({
                "metadata": {
                    "identifier": "opensource_audio",
                    "mediatype": "collection",
                    "title": "Open Source Audio",
                },
            })

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        payload = _call("get_collection_info", identifier="opensource_audio")
        result = json.loads(payload["content"][0]["text"])
        assert isinstance(result, dict)
        assert result["metadata"]["mediatype"] == "collection"


class TestGetItemThumbnailTool:
    def test_returns_url_string(self, monkeypatch):
        payload = _call("get_item_thumbnail", identifier="nightofthelivingdead")
        text = payload["content"][0]["text"]
        assert "nightofthelivingdead" in text
        assert text.startswith("https://archive.org/services/img/")

    def test_empty_identifier_returns_error(self, monkeypatch):
        payload = _call("get_item_thumbnail", identifier="")
        text = payload["content"][0]["text"]
        assert "Error:" in text


class TestSearchArchiveDeepTool:
    def test_returns_items(self, monkeypatch):
        def handler(request):
            return json_response({
                "items": [{"identifier": "item1"}, {"identifier": "item2"}],
                "total": 500,
                "count": 100,
                "cursor": "abc123",
            })

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        payload = _call("search_archive_deep", query="jazz")
        result = json.loads(payload["content"][0]["text"])
        assert isinstance(result, dict)
        assert len(result["items"]) == 2
        assert result["cursor"] == "abc123"

    def test_low_count_returns_error(self, monkeypatch):
        monkeypatch.setattr(server_module, "_client", make_mock_client(lambda r: json_response({})))
        payload = _call("search_archive_deep", query="test", count=50)
        text = payload["content"][0]["text"]
        assert "Error:" in text


# ---------------------------------------------------------------------------
# Tier 3 — Auth-gated tools
# ---------------------------------------------------------------------------


class TestSavePageTool:
    def test_no_keys_returns_guidance_error(self, monkeypatch):
        monkeypatch.delenv("IA_ACCESS_KEY", raising=False)
        monkeypatch.delenv("IA_SECRET_KEY", raising=False)
        monkeypatch.setattr(server_module, "_client", make_mock_client(lambda r: json_response({})))
        payload = _call("save_page", url="http://example.com")
        text = payload["content"][0]["text"]
        assert "Error:" in text
        assert "authentication" in text.lower() or "s3.php" in text

    def test_with_keys_calls_api(self, monkeypatch):
        seen_headers: dict[str, str] = {}

        def handler(request):
            seen_headers["auth"] = request.headers.get("authorization", "")
            return json_response({"url": "http://example.com", "job_id": "abc"})

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        payload = _call("save_page", url="http://example.com", access_key="AK", secret_key="SK")
        result = json.loads(payload["content"][0]["text"])
        assert isinstance(result, dict)
        assert result["job_id"] == "abc"
        assert "LOW AK:SK" in seen_headers["auth"]

    def test_env_var_fallback(self, monkeypatch):
        monkeypatch.setenv("IA_ACCESS_KEY", "ENV_AK")
        monkeypatch.setenv("IA_SECRET_KEY", "ENV_SK")
        seen_headers: dict[str, str] = {}

        def handler(request):
            seen_headers["auth"] = request.headers.get("authorization", "")
            return json_response({"url": "http://example.com", "job_id": "xyz"})

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        payload = _call("save_page", url="http://example.com")
        result = json.loads(payload["content"][0]["text"])
        assert isinstance(result, dict)
        assert "LOW ENV_AK:ENV_SK" in seen_headers["auth"]


class TestSavePageStatusTool:
    def test_returns_status(self, monkeypatch):
        def handler(request):
            return json_response({"status": "success", "original_url": "http://example.com"})

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        payload = _call("save_page_status", job_id="job123")
        result = json.loads(payload["content"][0]["text"])
        assert isinstance(result, dict)
        assert result["status"] == "success"

    def test_empty_job_id_returns_error(self, monkeypatch):
        monkeypatch.setattr(server_module, "_client", make_mock_client(lambda r: json_response({})))
        payload = _call("save_page_status", job_id="")
        text = payload["content"][0]["text"]
        assert "Error:" in text


class TestWaybackSnapshotsPaginationTool:
    def test_show_resume_key_returns_dict(self, monkeypatch):
        cdx_with_resume = [
            ["urlkey", "timestamp"],
            ["com,example)/", "20200101"],
            [],
            ["resume_key_abc"],
        ]

        def handler(request):
            return json_response(cdx_with_resume)

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        payload = _call("wayback_snapshots", url="example.com", show_resume_key=True)
        result = json.loads(payload["content"][0]["text"])
        assert isinstance(result, dict)
        assert "snapshots" in result
        assert result["resume_key"] == "resume_key_abc"

    def test_fields_param_accepted(self, monkeypatch):
        def handler(request):
            return json_response([
                ["timestamp", "statuscode"],
                ["20200101", "200"],
            ])

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        payload = _call("wayback_snapshots", url="example.com", fields=["timestamp", "statuscode"])
        result = json.loads(payload["content"][0]["text"])
        assert isinstance(result, list)
        assert result[0]["timestamp"] == "20200101"


class TestSearchArchiveDeepTotalOnlyTool:
    def test_total_only_returns_count(self, monkeypatch):
        def handler(request):
            return json_response({"total": 999, "count": 0, "items": []})

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        payload = _call("search_archive_deep", query="jazz", total_only=True)
        result = json.loads(payload["content"][0]["text"])
        assert isinstance(result, dict)
        assert result["total"] == 999


class TestListItemFilesDownloadUrl:
    def test_files_have_download_url(self, monkeypatch):
        def handler(request):
            return json_response({
                "metadata": {"identifier": "test-item"},
                "files": [{"name": "file1.mp3", "format": "VBR MP3"}],
            })

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        payload = _call("list_item_files", identifier="test-item")
        result = json.loads(payload["content"][0]["text"])
        assert isinstance(result, list)
        assert result[0]["download_url"] == "https://archive.org/download/test-item/file1.mp3"
