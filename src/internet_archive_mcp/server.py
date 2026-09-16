#!/usr/bin/env python3
"""Stdlib stateless-era (2026-07-28) MCP server for the Internet Archive."""
import json, sys
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


def handle_call(name, arguments):
    """Dispatch a tools/call by name to its handler (T06–T07 installs the 13).

    T05 placeholder: every tool is unknown, so the §5 fleet rule for unknown
    tools is already the live path — return an {"error": ...} dict (⇒ isError
    true at the dispatch site), never raise.
    """
    return {"error": f"Unknown tool: {name}"}


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
                is_err = isinstance(result, dict) and ("error" in result or "transport_error" in result)
                # NOTE (T05): the text encoding below is REFERENCE §5's generic snippet
                # verbatim; D4's byte-freeze derives the FINAL encoding from the legacy
                # framework server (compact, ensure_ascii=False; str passthrough; the one
                # content:[] DEVIATION tag) — that lands with the handlers at T06–T07.
                payload: dict = {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}
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
