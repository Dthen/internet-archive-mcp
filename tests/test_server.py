"""Tests for server registration and startup (era rewrite, T17b).

Ported from the legacy fastmcp-internals suite (mcp object, _registered_tools
via list_tools(), main() callable). Era replacements (count preserved 4→4):
  ① import smoke under $PYH (no fastmcp in the graph);
  ② len(TOOLS) == 13 + names-set == the 13 expected (verbatim name list);
  ③ callable(main) AND not a coroutine;
  ④ SERVER_INFO == {"name":"internet-archive","version":"0.3.0"}.
"""

from __future__ import annotations

import inspect

import internet_archive_mcp.server as server_module


def test_server_imports_cleanly():
    """Server module imports without errors (no fastmcp in the graph)."""
    import internet_archive_mcp.server  # noqa: F401


def test_server_has_13_tools():
    """Verify the public TOOLS surface holds exactly 13 tools with the expected names."""
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
    actual = {t["name"] for t in server_module.TOOLS}
    assert len(server_module.TOOLS) == 13, (
        f"Expected 13 tools, got {len(server_module.TOOLS)}: {list(actual)}"
    )
    assert actual == expected


def test_main_is_callable_not_coroutine():
    """main() is callable and NOT a coroutine (sync stdio loop)."""
    assert callable(server_module.main)
    assert not inspect.iscoroutinefunction(server_module.main)


def test_server_info():
    """SERVER_INFO carries name + era target version 0.3.0 (D7)."""
    assert server_module.SERVER_INFO == {
        "name": "internet-archive",
        "version": "0.3.0",
    }
