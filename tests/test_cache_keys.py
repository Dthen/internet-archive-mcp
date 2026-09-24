"""Focused tests for collision-free cache keys."""

from internet_archive_mcp.client import ArchiveClient
from conftest import json_response, make_mock_client, text_response


def test_fresh_result_mutation_does_not_pollute_cache():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return json_response({
            "response": {
                "numFound": 1,
                "start": 0,
                "docs": [{"identifier": "original"}],
            }
        })

    ac = make_mock_client(handler)
    first = ac.search_archive("test")
    first["docs"].append({"identifier": "caller-added"})

    second = ac.search_archive("test")

    assert len(calls) == 1
    assert second["docs"] == [{"identifier": "original"}]


def test_cache_key_format_is_stable_for_legacy_inputs():
    ac = ArchiveClient(min_request_interval=0)

    assert ac._cache_key("search", "test", 20, 1, ["identifier"], []) == (
        "search:test:20:1:identifier:"
    )


def test_cache_key_distinguishes_none_from_string_none():
    ac = ArchiveClient(min_request_interval=0)

    assert ac._cache_key("metadata", None) != ac._cache_key("metadata", "None")


def test_cache_key_distinguishes_empty_list_from_empty_string_item():
    ac = ArchiveClient(min_request_interval=0)

    assert ac._cache_key("search", []) != ac._cache_key("search", [""])


def test_search_cache_key_does_not_collide_on_delimiters():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return json_response({
            "response": {"numFound": len(calls), "start": 0, "docs": []}
        })

    ac = make_mock_client(handler)
    first = ac.search_archive("x:1", rows=2, page=3, fields=["a"])
    second = ac.search_archive("x", rows=1, page=2, fields=["3:a"])

    assert first["numFound"] == 1
    assert second["numFound"] == 2
    assert len(calls) == 2


def test_cdx_cache_key_distinguishes_string_filter_from_one_item_list():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return json_response([["timestamp"], ["20200101"]])

    ac = make_mock_client(handler)
    ac.wayback_snapshots("https://example.com/x", filter_expr="a")
    ac.wayback_snapshots("https://example.com/x", filter_expr=["a"])

    assert len(calls) == 2


def test_cdx_cache_key_does_not_collide_on_delimiters():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return json_response([["timestamp"], ["20200101"]])

    ac = make_mock_client(handler)
    first = ac.wayback_snapshots(
        "https://example.com/x", filter_expr="a:b", collapse="c"
    )
    second = ac.wayback_snapshots(
        "https://example.com/x", filter_expr="a", collapse="b:c"
    )

    assert first == [{"timestamp": "20200101"}]
    assert second == [{"timestamp": "20200101"}]
    assert len(calls) == 2


def test_wayback_fetch_cache_key_does_not_collide_on_delimiters():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return text_response("same body")

    ac = make_mock_client(handler)
    first = ac.wayback_fetch("https://example.com/a", timestamp="x:True")
    second = ac.wayback_fetch("https://example.com/a:x", timestamp="True")

    assert first["content"] == second["content"] == "same body"
    assert len(calls) == 2


def test_metadata_cache_key_does_not_collide_on_delimiters():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return json_response({"metadata": {"identifier": "x"}})

    ac = make_mock_client(handler)
    first = ac.get_item_metadata("x:True")
    second = ac.get_item_metadata("x", include_files=True)

    assert first["metadata"]["identifier"] == "x"
    assert second["metadata"]["identifier"] == "x"
    assert len(calls) == 2
