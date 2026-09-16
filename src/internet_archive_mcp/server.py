#!/usr/bin/env python3
"""Stdlib stateless-era (2026-07-28) MCP server for the Internet Archive."""
import json, os, sys
import urllib.error
from pathlib import Path

if hasattr(sys.stdin, "reconfigure"):            # binary/undecodable bytes must not kill the loop
    sys.stdin.reconfigure(errors="replace")      # invalid UTF-8 → U+FFFD → lands in the json.loads except

from internet_archive_mcp.client import ArchiveClient

# ---------------------------------------------------------------------------
# Era constants (REFERENCE §1 constants block — copy exactly)
# ---------------------------------------------------------------------------
ERA_VERSION = "2026-07-28"
SERVER_INFO = {"name": "internet-archive", "version": "0.3.0"}
ERA_RESULT_FIELDS = {"resultType": "complete", "ttlMs": 0, "cacheScope": "private"}
RESULT_META = {"io.modelcontextprotocol/serverInfo": SERVER_INFO}  # spec-RECOMMENDED stamp, optional


def era_result(payload):
    """A result carrying the era-strict fields D3 mandates on every response."""
    out = dict(payload)
    out.update(ERA_RESULT_FIELDS)
    out["_meta"] = RESULT_META
    return out


# ---------------------------------------------------------------------------
# Client singleton
# ---------------------------------------------------------------------------
# Single eager module-level instance (T03's client is sync and cheap to construct —
# there is no async connect, so nothing is gained by lazy import). This mirrors the
# legacy module-level singleton and keeps cache/rate-limit state in ONE instance.
# T15b/T17a ported tests monkeypatch `server_module._client` directly, which requires
# the attribute to exist at import.
_client = ArchiveClient()

# ---------------------------------------------------------------------------
# Tools surface (T06) — frozen from the golden capture, never hand-copied
# ---------------------------------------------------------------------------
# D4: golden/internet-archive.tools.json (T00 capture) is the ONLY sanctioned
# source of the 13 name/description/inputSchema triples; the legacy-framework
# keys outputSchema and _meta are dropped at projection. Drift is structurally
# impossible because the golden IS the source — edit the capture, never this
# projection. Repo-root path via __file__ walk (src/<pkg>/server.py -> parents[2]);
# the editable install keeps this resolvable from the repo venv (T19 probe gate).
def _load_tools():
    path = Path(__file__).resolve().parents[2] / "golden" / "internet-archive.tools.json"
    if not path.is_file():
        raise RuntimeError(f"golden tools capture missing: {path} — refuse to serve an empty surface")
    tools = json.loads(path.read_text())
    if len(tools) != 13:
        raise RuntimeError(f"golden tools capture holds {len(tools)} tools, expected 13: {path}")
    return [{"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
            for t in tools]


TOOLS = _load_tools()   # D4: frozen surface — edit the capture, never this projection


# ---------------------------------------------------------------------------
# Tool handlers (T07) — thin sync wrappers over T03's client
# ---------------------------------------------------------------------------
# Argument mapping is byte-copied from the legacy tag server.py
# (git show pre-migration/20260914:src/internet_archive_mcp/server.py, l.54-63 …
# l.403-407): same client method, same positional first arg, same keyword names,
# same defaults. Handlers take the raw `arguments` dict from the wire and fall
# back to the legacy signature defaults via .get(). TOOLS is read-only here
# (T06 review note): no handler may touch the shared golden-backed schema objects.


def _h_search_archive(a):
    try:
        return _client.search_archive(
            a["query"],
            mediatype=a.get("mediatype"),
            collection=a.get("collection"),
            fields=a.get("fields"),
            sort=a.get("sort"),
            rows=a.get("rows", 20),
            page=a.get("page", 1),
        )
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return f"Error: {e}"
    # R1: seam normalizes TimeoutError/HTTPException/reset → URLError so read-timeouts fold exactly like legacy httpx.HTTPError
    except (urllib.error.URLError, TimeoutError) as e:  # ⊃ HTTPError; seam + raise_for_status land 4xx/5xx here too (legacy: httpx.HTTPError). TimeoutError folded here too: defensive — even if a client raises it directly (bypassing the seam), it lands in the same friendly "API request failed" text.
        return f"Error: API request failed — {e}"


def _h_get_item_metadata(a):
    try:
        return _client.get_item_metadata(
            a["identifier"], include_files=a.get("include_files", False)
        )
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return f"Error: {e}"
    # R1: seam normalizes TimeoutError/HTTPException/reset → URLError so read-timeouts fold exactly like legacy httpx.HTTPError
    except (urllib.error.URLError, TimeoutError) as e:  # ⊃ HTTPError; seam + raise_for_status land 4xx/5xx here too (legacy: httpx.HTTPError). TimeoutError folded here too: defensive — even if a client raises it directly (bypassing the seam), it lands in the same friendly "API request failed" text.
        return f"Error: API request failed — {e}"


def _h_list_item_files(a):
    try:
        return _client.list_item_files(
            a["identifier"], format_filter=a.get("format_filter")
        )
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return f"Error: {e}"
    # R1: seam normalizes TimeoutError/HTTPException/reset → URLError so read-timeouts fold exactly like legacy httpx.HTTPError
    except (urllib.error.URLError, TimeoutError) as e:  # ⊃ HTTPError; seam + raise_for_status land 4xx/5xx here too (legacy: httpx.HTTPError). TimeoutError folded here too: defensive — even if a client raises it directly (bypassing the seam), it lands in the same friendly "API request failed" text.
        return f"Error: API request failed — {e}"


def _h_get_item_reviews(a):
    try:
        return _client.get_item_reviews(a["identifier"])
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return f"Error: {e}"
    # R1: seam normalizes TimeoutError/HTTPException/reset → URLError so read-timeouts fold exactly like legacy httpx.HTTPError
    except (urllib.error.URLError, TimeoutError) as e:  # ⊃ HTTPError; seam + raise_for_status land 4xx/5xx here too (legacy: httpx.HTTPError). TimeoutError folded here too: defensive — even if a client raises it directly (bypassing the seam), it lands in the same friendly "API request failed" text.
        return f"Error: API request failed — {e}"


def _h_wayback_snapshots(a):
    try:
        return _client.wayback_snapshots(
            a["url"],
            match_type=a.get("match_type", "exact"),
            from_year=a.get("from_year"),
            to_year=a.get("to_year"),
            limit=a.get("limit", 25),
            filter_expr=a.get("filter_expr"),
            collapse=a.get("collapse"),
            fields=a.get("fields"),
            page=a.get("page"),
            show_resume_key=a.get("show_resume_key", False),
            resume_key=a.get("resume_key"),
            newest=a.get("newest", False),
            fast_latest=a.get("fast_latest", False),
        )
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return f"Error: {e}"
    # R1: seam normalizes TimeoutError/HTTPException/reset → URLError so read-timeouts fold exactly like legacy httpx.HTTPError
    except (urllib.error.URLError, TimeoutError) as e:  # ⊃ HTTPError; seam + raise_for_status land 4xx/5xx here too (legacy: httpx.HTTPError). TimeoutError folded here too: defensive — even if a client raises it directly (bypassing the seam), it lands in the same friendly "API request failed" text.
        return f"Error: API request failed — {e}"


def _h_wayback_availability(a):
    try:
        return _client.wayback_availability(a["url"])
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return f"Error: {e}"
    # R1: seam normalizes TimeoutError/HTTPException/reset → URLError so read-timeouts fold exactly like legacy httpx.HTTPError
    except (urllib.error.URLError, TimeoutError) as e:  # ⊃ HTTPError; seam + raise_for_status land 4xx/5xx here too (legacy: httpx.HTTPError). TimeoutError folded here too: defensive — even if a client raises it directly (bypassing the seam), it lands in the same friendly "API request failed" text.
        return f"Error: API request failed — {e}"


def _h_wayback_fetch(a):
    try:
        return _client.wayback_fetch(
            a["url"],
            timestamp=a.get("timestamp"),
            raw=a.get("raw", True),
            char_limit=a.get("char_limit", 50000),
        )
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return f"Error: {e}"
    # R1: seam normalizes TimeoutError/HTTPException/reset → URLError so read-timeouts fold exactly like legacy httpx.HTTPError
    except (urllib.error.URLError, TimeoutError) as e:  # ⊃ HTTPError; seam + raise_for_status land 4xx/5xx here too (legacy: httpx.HTTPError). TimeoutError folded here too: defensive — even if a client raises it directly (bypassing the seam), it lands in the same friendly "API request failed" text.
        return f"Error: API request failed — {e}"


def _h_browse_collection(a):
    try:
        return _client.browse_collection(
            a["collection"], rows=a.get("rows", 20), page=a.get("page", 1),
            sort=a.get("sort"),
        )
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return f"Error: {e}"
    # R1: seam normalizes TimeoutError/HTTPException/reset → URLError so read-timeouts fold exactly like legacy httpx.HTTPError
    except (urllib.error.URLError, TimeoutError) as e:  # ⊃ HTTPError; seam + raise_for_status land 4xx/5xx here too (legacy: httpx.HTTPError). TimeoutError folded here too: defensive — even if a client raises it directly (bypassing the seam), it lands in the same friendly "API request failed" text.
        return f"Error: API request failed — {e}"


def _h_get_collection_info(a):
    try:
        return _client.get_collection_info(a["identifier"])
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return f"Error: {e}"
    # R1: seam normalizes TimeoutError/HTTPException/reset → URLError so read-timeouts fold exactly like legacy httpx.HTTPError
    except (urllib.error.URLError, TimeoutError) as e:  # ⊃ HTTPError; seam + raise_for_status land 4xx/5xx here too (legacy: httpx.HTTPError). TimeoutError folded here too: defensive — even if a client raises it directly (bypassing the seam), it lands in the same friendly "API request failed" text.
        return f"Error: API request failed — {e}"


def _h_get_item_thumbnail(a):
    # Legacy tag l.318-321: sync, no HTTP — keeps ONLY the first except tuple (no URLError arm).
    try:
        return _client.get_item_thumbnail_url(a["identifier"])
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return f"Error: {e}"


def _h_search_archive_deep(a):
    try:
        return _client.search_archive_deep(
            a["query"], fields=a.get("fields"), sorts=a.get("sorts"),
            count=a.get("count", 100), cursor=a.get("cursor"),
            total_only=a.get("total_only", False),
        )
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return f"Error: {e}"
    # R1: seam normalizes TimeoutError/HTTPException/reset → URLError so read-timeouts fold exactly like legacy httpx.HTTPError
    except (urllib.error.URLError, TimeoutError) as e:  # ⊃ HTTPError; seam + raise_for_status land 4xx/5xx here too (legacy: httpx.HTTPError). TimeoutError folded here too: defensive — even if a client raises it directly (bypassing the seam), it lands in the same friendly "API request failed" text.
        return f"Error: API request failed — {e}"


def _h_save_page(a):
    # Env-var contract (_chain verified fact table; pinned by T18): IA_ACCESS_KEY /
    # IA_SECRET_KEY are read at CALL time, not import time — legacy tag l.381-382.
    ak = a.get("access_key") or os.environ.get("IA_ACCESS_KEY")
    sk = a.get("secret_key") or os.environ.get("IA_SECRET_KEY")
    try:
        return _client.save_page(a["url"], access_key=ak, secret_key=sk)
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return f"Error: {e}"
    # R1: seam normalizes TimeoutError/HTTPException/reset → URLError so read-timeouts fold exactly like legacy httpx.HTTPError
    except (urllib.error.URLError, TimeoutError) as e:  # ⊃ HTTPError; seam + raise_for_status land 4xx/5xx here too (legacy: httpx.HTTPError). TimeoutError folded here too: defensive — even if a client raises it directly (bypassing the seam), it lands in the same friendly "API request failed" text.
        return f"Error: API request failed — {e}"


def _h_save_page_status(a):
    try:
        return _client.save_page_status(a["job_id"])
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return f"Error: {e}"
    # R1: seam normalizes TimeoutError/HTTPException/reset → URLError so read-timeouts fold exactly like legacy httpx.HTTPError
    except (urllib.error.URLError, TimeoutError) as e:  # ⊃ HTTPError; seam + raise_for_status land 4xx/5xx here too (legacy: httpx.HTTPError). TimeoutError folded here too: defensive — even if a client raises it directly (bypassing the seam), it lands in the same friendly "API request failed" text.
        return f"Error: API request failed — {e}"


_dispatch = {
    "search_archive": _h_search_archive,
    "get_item_metadata": _h_get_item_metadata,
    "list_item_files": _h_list_item_files,
    "get_item_reviews": _h_get_item_reviews,
    "wayback_snapshots": _h_wayback_snapshots,
    "wayback_availability": _h_wayback_availability,
    "wayback_fetch": _h_wayback_fetch,
    "browse_collection": _h_browse_collection,
    "get_collection_info": _h_get_collection_info,
    "get_item_thumbnail": _h_get_item_thumbnail,
    "search_archive_deep": _h_search_archive_deep,
    "save_page": _h_save_page,
    "save_page_status": _h_save_page_status,
}


def handle_call(name, arguments):
    """Dispatch a tools/call by name to its handler (T07 installs the 13).

    Unknown tool → {"error": ...} dict (⇒ isError true at the dispatch site,
    REFERENCE §5 fleet rule), never raise. Missing required arguments surface
    as KeyError *inside* the handler's own fold (legacy had framework
    validation upstream; the fold text "Error: '<name>'" is the closest
    legacy-shaped result). Exceptions escaping the handler folds entirely are
    dispatch-level → -32603 at the tools/call arm.
    """
    handler = _dispatch.get(name)
    if handler is None:
        return {"error": f"Unknown tool: {name}"}
    return handler(arguments)


# ---------------------------------------------------------------------------
# Wire encoding (T07) — REFERENCE §5, derive-from-legacy (golden/legacy-behavior.json)
# ---------------------------------------------------------------------------

def encode_tool_result(result):
    """The ONLY wire-shaping point: tool return value → list of content blocks.

    structuredContent is deliberately ABSENT on every response — §4 trap/D3:
    no tool declares an outputSchema, so a structuredContent block would make
    strict 2.0 clients reject the text result.
    """
    if isinstance(result, list) and not result:
        # DEVIATION from REFERENCE §5 (justified: legacy fastmcp emits content:[] for empty-list results — golden/legacy-behavior.json case 'snapshots_empty'; matching legacy bytes preserves consumer shape)
        return []
    if isinstance(result, str):
        return [{"type": "text", "text": result}]   # str passthrough verbatim (§5 rule, R3: not tagged)
    # dict / non-empty list: COMPACT non-ASCII-preserving JSON (legacy fastmcp
    # framework encoding — §5 derive-from-legacy rule, fixtures pin the bytes;
    # NOT tagged per R3).
    return [{"type": "text", "text": json.dumps(result, separators=(",", ":"), ensure_ascii=False)}]


def send(resp):
    sys.stdout.write(json.dumps(resp) + "\n")   # exactly one object per line
    sys.stdout.flush()                          # a buffered reply = a client timeout


def main():
    for line in sys.stdin:                      # EOF on stdin ends the loop (see §7)
        line = line.strip()
        if not line: continue
        try: req = json.loads(line)
        except Exception: continue              # garbage lines: skip, NEVER die (§7)
        if not isinstance(req, dict): continue  # valid JSON, not an object ("5", null, [1,2]): skip, NEVER die (§7)
        rid = req.get("id")                     # str or int; absent ⇒ notification
        method = req.get("method")              # null/42/etc must not crash .startswith below
        if not isinstance(method, str): method = ""   # route as unknown-method
        if method == "server/discover":
            send({"jsonrpc":"2.0","id":rid,"result":era_result({
                "supportedVersions":[ERA_VERSION],
                "capabilities":{"tools":{}}})})
        elif method == "tools/list":
            send({"jsonrpc":"2.0","id":rid,"result":era_result({"tools":TOOLS})})
        elif method == "tools/call":
            params = req.get("params")
            if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                send({"jsonrpc":"2.0","id":rid,"error":{"code":-32602,
                    "message":"missing required param: params (with string 'name')"}})
                continue
            try:
                result = handle_call(params["name"], params.get("arguments", {}))
                # §5 rule: isError fires ONLY for {"error": ...} DICTS (unknown-tool
                # dispatch). Legacy's "Error: …" strings are successful results —
                # probe-verified isError: False (golden/legacy-behavior.json).
                is_err = isinstance(result, dict) and ("error" in result or "transport_error" in result)
                payload: dict = {"content": encode_tool_result(result)}
                if is_err:
                    payload["isError"] = True
                send({"jsonrpc":"2.0","id":rid,"result":era_result(payload)})
            except Exception as e:                       # dispatch-level only (shouldn't happen)
                send({"jsonrpc":"2.0","id":rid,"error":{"code":-32603,"message":str(e)}})
        elif method.startswith("notifications/"): pass    # §6: id-less ⇒ never respond
        elif method == "ping":
            send({"jsonrpc":"2.0","id":rid,"result":{}})  # §6
        else:
            # includes legacy `initialize` (D2) and every other unknown method, per JSON-RPC
            if rid is None and "id" not in req: continue   # no-id = notification: never respond
            send({"jsonrpc":"2.0","id":rid,"error":{"code":-32601,"message":f"Method not found: {method}"}})
    # §7: EOF ends the for-loop; main() returns; interpreter exits 0. No sys.exit anywhere.


if __name__ == "__main__":
    main()
