"""Tests for server registration and startup."""

from __future__ import annotations

import asyncio


def _registered_tools(server):
    """Return the server's {name: tool} dict, or {} if internals changed."""
    # fastmcp 3.x exposes the public async list_tools(); prefer it, fall
    # back to the pre-3.x private _tool_manager._tools dict for older envs.
    list_tools = getattr(server, "list_tools", None)
    if list_tools is not None:
        try:
            tools = asyncio.run(list_tools())
            return {t.name: t for t in tools}
        except Exception:
            pass
    tool_manager = getattr(server, "_tool_manager", None)
    tools = getattr(tool_manager, "_tools", None) if tool_manager is not None else None
    return tools if isinstance(tools, dict) else {}


def test_server_has_13_tools():
    """Verify all 13 tools are registered."""
    from internet_archive_mcp.server import mcp

    tools = _registered_tools(mcp)
    assert len(tools) == 13, f"Expected 13 tools, got {len(tools)}: {list(tools.keys())}"


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
        "save_page_status",
    }
    actual = set(_registered_tools(mcp).keys())
    assert actual == expected
