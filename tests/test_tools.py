"""Tests for MCP tool functions in the server module."""

from __future__ import annotations

import httpx

import internet_archive_mcp.server as server_module
from conftest import make_mock_client, json_response


# ---------------------------------------------------------------------------
# Tier 1 — Core tools
# ---------------------------------------------------------------------------


class TestSearchArchiveTool:
    async def test_returns_results(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response({
                "response": {
                    "numFound": 1,
                    "start": 0,
                    "docs": [{"identifier": "test-item", "title": "Test"}],
                }
            })

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        result = await server_module.search_archive("test query")
        assert isinstance(result, dict)
        assert result["numFound"] == 1
        assert len(result["docs"]) == 1

    async def test_empty_query_returns_error(self, monkeypatch):
        monkeypatch.setattr(server_module, "_client", make_mock_client(lambda r: json_response({})))
        result = await server_module.search_archive("")
        assert isinstance(result, str)
        assert result.startswith("Error:")

    async def test_api_error_returns_error_string(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal Server Error")

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        result = await server_module.search_archive("test")
        assert isinstance(result, str)
        assert result.startswith("Error:")


class TestGetItemMetadataTool:
    async def test_returns_dict(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response({
                "metadata": {"identifier": "test-item", "title": "Test Item"},
            })

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        result = await server_module.get_item_metadata("test-item")
        assert isinstance(result, dict)
        assert result["metadata"]["identifier"] == "test-item"

    async def test_not_found_returns_error(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response({})

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        result = await server_module.get_item_metadata("nonexistent")
        assert isinstance(result, str)
        assert "Error:" in result


class TestListItemFilesTool:
    async def test_returns_list(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response({
                "metadata": {"identifier": "test-item"},
                "files": [
                    {"name": "file1.mp3", "format": "VBR MP3"},
                    {"name": "file2.pdf", "format": "Text PDF"},
                ],
            })

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        result = await server_module.list_item_files("test-item")
        assert isinstance(result, list)
        assert len(result) == 2

    async def test_format_filter_works(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response({
                "metadata": {"identifier": "test-item"},
                "files": [
                    {"name": "file1.mp3", "format": "VBR MP3"},
                    {"name": "file2.pdf", "format": "Text PDF"},
                ],
            })

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        result = await server_module.list_item_files("test-item", format_filter="VBR MP3")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["name"] == "file1.mp3"


class TestGetItemReviewsTool:
    async def test_returns_list(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response({
                "metadata": {"identifier": "test-item"},
                "reviews": [
                    {"reviewer": "alice", "stars": 5, "reviewtitle": "Great"},
                ],
            })

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        result = await server_module.get_item_reviews("test-item")
        assert isinstance(result, list)
        assert len(result) == 1

    async def test_empty_reviews_returns_empty_list(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response({
                "metadata": {"identifier": "test-item"},
            })

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        result = await server_module.get_item_reviews("test-item")
        assert isinstance(result, list)
        assert result == []


class TestWaybackSnapshotsTool:
    async def test_returns_list(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response([
                ["urlkey", "timestamp", "original", "statuscode"],
                ["com,example)/", "20200101", "http://example.com/", "200"],
            ])

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        result = await server_module.wayback_snapshots("example.com")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["statuscode"] == "200"

    async def test_domain_match_type_returns_error(self, monkeypatch):
        monkeypatch.setattr(server_module, "_client", make_mock_client(lambda r: json_response([])))
        result = await server_module.wayback_snapshots("example.com", match_type="domain")
        assert isinstance(result, str)
        assert "Error:" in result


class TestWaybackAvailabilityTool:
    async def test_returns_dict(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response({
                "archived_snapshots": {
                    "closest": {
                        "timestamp": "20200101120000",
                        "url": "http://web.archive.org/web/20200101120000/http://example.com",
                    }
                }
            })

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        result = await server_module.wayback_availability("http://example.com")
        assert isinstance(result, dict)
        assert "archived_snapshots" in result


class TestWaybackFetchTool:
    async def test_returns_content_dict(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>Hello World</html>")

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        result = await server_module.wayback_fetch("http://example.com")
        assert isinstance(result, dict)
        assert "content" in result
        assert result["truncated"] is False

    async def test_truncated_flag(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="x" * 100000)

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        result = await server_module.wayback_fetch("http://example.com", char_limit=100)
        assert isinstance(result, dict)
        assert result["truncated"] is True
        assert len(result["content"]) == 100
        assert result["content_length"] == 100000


# ---------------------------------------------------------------------------
# Tier 2 — Differentiator tools
# ---------------------------------------------------------------------------


class TestBrowseCollectionTool:
    async def test_returns_results(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
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
        result = await server_module.browse_collection("opensource_audio")
        assert isinstance(result, dict)
        assert result["numFound"] == 2

    async def test_empty_collection_returns_error(self, monkeypatch):
        monkeypatch.setattr(server_module, "_client", make_mock_client(lambda r: json_response({})))
        result = await server_module.browse_collection("")
        assert isinstance(result, str)
        assert "Error:" in result


class TestGetCollectionInfoTool:
    async def test_returns_metadata(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response({
                "metadata": {
                    "identifier": "opensource_audio",
                    "mediatype": "collection",
                    "title": "Open Source Audio",
                },
            })

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        result = await server_module.get_collection_info("opensource_audio")
        assert isinstance(result, dict)
        assert result["metadata"]["mediatype"] == "collection"


class TestGetItemThumbnailTool:
    def test_returns_url_string(self, monkeypatch):
        result = server_module.get_item_thumbnail("nightofthelivingdead")
        assert isinstance(result, str)
        assert "nightofthelivingdead" in result
        assert result.startswith("https://archive.org/services/img/")

    def test_empty_identifier_returns_error(self, monkeypatch):
        result = server_module.get_item_thumbnail("")
        assert isinstance(result, str)
        assert "Error:" in result


class TestSearchArchiveDeepTool:
    async def test_returns_items(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response({
                "items": [{"identifier": "item1"}, {"identifier": "item2"}],
                "total": 500,
                "count": 100,
                "cursor": "abc123",
            })

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        result = await server_module.search_archive_deep("jazz")
        assert isinstance(result, dict)
        assert len(result["items"]) == 2
        assert result["cursor"] == "abc123"

    async def test_low_count_returns_error(self, monkeypatch):
        monkeypatch.setattr(server_module, "_client", make_mock_client(lambda r: json_response({})))
        result = await server_module.search_archive_deep("test", count=50)
        assert isinstance(result, str)
        assert "Error:" in result


# ---------------------------------------------------------------------------
# Tier 3 — Auth-gated tools
# ---------------------------------------------------------------------------


class TestSavePageTool:
    async def test_no_keys_returns_guidance_error(self, monkeypatch):
        monkeypatch.delenv("IA_ACCESS_KEY", raising=False)
        monkeypatch.delenv("IA_SECRET_KEY", raising=False)
        monkeypatch.setattr(server_module, "_client", make_mock_client(lambda r: json_response({})))
        result = await server_module.save_page("http://example.com")
        assert isinstance(result, str)
        assert "Error:" in result
        assert "authentication" in result.lower() or "s3.php" in result

    async def test_with_keys_calls_api(self, monkeypatch):
        seen_headers: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen_headers["auth"] = request.headers.get("authorization", "")
            return json_response({"url": "http://example.com", "job_id": "abc"})

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        result = await server_module.save_page(
            "http://example.com", access_key="AK", secret_key="SK"
        )
        assert isinstance(result, dict)
        assert result["job_id"] == "abc"
        assert "LOW AK:SK" in seen_headers["auth"]

    async def test_env_var_fallback(self, monkeypatch):
        monkeypatch.setenv("IA_ACCESS_KEY", "ENV_AK")
        monkeypatch.setenv("IA_SECRET_KEY", "ENV_SK")
        seen_headers: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen_headers["auth"] = request.headers.get("authorization", "")
            return json_response({"url": "http://example.com", "job_id": "xyz"})

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        result = await server_module.save_page("http://example.com")
        assert isinstance(result, dict)
        assert "LOW ENV_AK:ENV_SK" in seen_headers["auth"]


class TestSavePageStatusTool:
    async def test_returns_status(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response({"status": "success", "original_url": "http://example.com"})

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        result = await server_module.save_page_status("job123")
        assert isinstance(result, dict)
        assert result["status"] == "success"

    async def test_empty_job_id_returns_error(self, monkeypatch):
        monkeypatch.setattr(server_module, "_client", make_mock_client(lambda r: json_response({})))
        result = await server_module.save_page_status("")
        assert isinstance(result, str)
        assert "Error:" in result


class TestWaybackSnapshotsPaginationTool:
    async def test_show_resume_key_returns_dict(self, monkeypatch):
        cdx_with_resume = [
            ["urlkey", "timestamp"],
            ["com,example)/", "20200101"],
            [],
            ["resume_key_abc"],
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(cdx_with_resume)

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        result = await server_module.wayback_snapshots("example.com", show_resume_key=True)
        assert isinstance(result, dict)
        assert "snapshots" in result
        assert result["resume_key"] == "resume_key_abc"

    async def test_fields_param_accepted(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response([
                ["timestamp", "statuscode"],
                ["20200101", "200"],
            ])

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        result = await server_module.wayback_snapshots(
            "example.com", fields=["timestamp", "statuscode"]
        )
        assert isinstance(result, list)
        assert result[0]["timestamp"] == "20200101"


class TestSearchArchiveDeepTotalOnlyTool:
    async def test_total_only_returns_count(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response({"total": 999, "count": 0, "items": []})

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        result = await server_module.search_archive_deep("jazz", total_only=True)
        assert isinstance(result, dict)
        assert result["total"] == 999


class TestListItemFilesDownloadUrl:
    async def test_files_have_download_url(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response({
                "metadata": {"identifier": "test-item"},
                "files": [{"name": "file1.mp3", "format": "VBR MP3"}],
            })

        monkeypatch.setattr(server_module, "_client", make_mock_client(handler))
        result = await server_module.list_item_files("test-item")
        assert isinstance(result, list)
        assert result[0]["download_url"] == "https://archive.org/download/test-item/file1.mp3"
