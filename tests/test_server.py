"""Tests for server registration and startup."""

from __future__ import annotations


def test_server_has_12_tools():
    """Verify all 12 tools are registered."""
    from internet_archive_mcp.server import mcp

    # FastMCP stores tools internally — access the tool manager
    tools = mcp._tool_manager._tools
    assert len(tools) == 12, f"Expected 12 tools, got {len(tools)}: {list(tools.keys())}"


def test_server_imports_cleanly():
    """Server module imports without errors."""
    import internet_archive_mcp.server  # noqa: F401


def test_main_function_exists():
    """main() is callable."""
    from internet_archive_mcp.server import main

    assert callable(main)


def test_tool_names():
    """All expected tool names are registered."""
    from internet_archive_mcp.server import mcp

    expected = {
        "search_archive",
        "get_item_metadata",
        "list_item_files",
        "get_item_reviews",
        "wayback_snapshots",
        "wayback_availability",
        "wayback_fetch",
        "browse_collection",
        "get_collection_info",
        "get_item_thumbnail",
        "search_archive_deep",
        "save_page",
    }
    actual = set(mcp._tool_manager._tools.keys())
    assert actual == expected
