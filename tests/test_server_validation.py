"""Dispatch-level validation tests for the stateless tool surface."""

import internet_archive_mcp.server as server


def test_unknown_tool_argument_is_rejected_before_handler():
    result = server.handle_call("search_archive", {"query": "jazz", "bogus": True})

    assert isinstance(result, dict)
    assert "error" in result
    assert "bogus" in result["error"]


def test_missing_required_argument_is_a_tool_error_dict():
    result = server.handle_call("get_item_metadata", {})

    assert isinstance(result, dict)
    assert "error" in result
    assert "identifier" in result["error"]


def test_wrong_scalar_argument_type_is_a_tool_error_dict():
    result = server.handle_call("search_archive", {"query": "jazz", "rows": "20"})

    assert isinstance(result, dict)
    assert "error" in result
    assert "rows" in result["error"]


def test_wrong_array_item_type_is_a_tool_error_dict():
    result = server.handle_call("search_archive", {"query": "jazz", "fields": ["title", 7]})

    assert isinstance(result, dict)
    assert "error" in result
    assert "fields" in result["error"]


def test_booleans_are_not_integers_for_schema_validation():
    result = server.handle_call("search_archive", {"query": "jazz", "rows": True})

    assert isinstance(result, dict)
    assert "error" in result
    assert "rows" in result["error"]


def test_nullable_optional_argument_is_rejected_when_schema_disallows_null():
    error = server._validate_tool_arguments(
        "get_item_metadata", {"identifier": "item", "include_files": None}
    )
    assert error is not None
    assert "include_files" in error


def test_non_dict_arguments_are_rejected():
    result = server.handle_call("get_item_thumbnail", ["item"])

    assert isinstance(result, dict)
    assert "error" in result


def test_valid_arguments_pass_validation_without_mutating_them():
    arguments = {
        "query": "creator:ellis",
        "fields": ["identifier", "title"],
        "rows": 20,
        "page": 1,
    }
    original = {**arguments, "fields": list(arguments["fields"])}

    assert server._validate_tool_arguments("search_archive", arguments) is None
    assert arguments == original


def test_unknown_tool_validation_is_not_used():
    assert server._validate_tool_arguments("not_a_tool", {}) is None
