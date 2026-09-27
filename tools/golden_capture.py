#!/usr/bin/env python3
"""Golden capture for internet-archive-mcp (migration step 0).

Spawns the CURRENT (legacy fastmcp) server over stdio under the production
interpreter ($PYO), performs a full MCP initialize / tools/list handshake, and
writes the FULL tools array verbatim to golden/internet-archive.tools.json.

This is a pre-rewrite characterization tool: it must run under the CURRENT
production interpreter (3.11 + fastmcp + httpx), never the target venv.

Interpreter selection: argv[1] if given, else env GOLDEN_PYTHON, else
$PROD_PY_INTERNET_ARCHIVE, else the .prod_py.old pointer file (gitignored).
default_python() resolves per-machine so no absolute host path is committed
here. Output path is derived from __file__ (repo root = parent of tools/), so
the script works from any cwd.

Stdlib only. Every stdout read from the spawned server goes through
read_line_with_timeout (F9: select-based deadline read — a silent server can
never hang the capture). The whole script is additionally meant to be run
under `timeout 30 <interp> tools/golden_capture.py` per the F9 fleet standard.

Expected surface (verified live, 2026-09-14): exactly 13 tools, each carrying
`outputSchema` and `_meta {"fastmcp": {"tags": []}}`. Any JSON-RPC error
response or a tool count != 13 exits nonzero with a message.
"""

import json
import os
import select
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_POINTER = os.path.join(REPO_ROOT, ".prod_py.old")  # gitignored, untracked
GOLDEN_PATH = os.path.join(REPO_ROOT, "golden", "internet-archive.tools.json")
EXPECTED_TOOL_COUNT = 13
LINE_TIMEOUT = 20.0  # per stdout line; total runtime is bounded by `timeout 30`
TERM_TIMEOUT = 10.0  # final terminate-and-reap bound


def default_python():
    """Resolve the PRE-MIGRATION (fastmcp + httpx) capture interpreter.

    Lazy, not a module constant, so an explicit argv[1] or $GOLDEN_PYTHON wins
    without this raising on import. Deliberately separate from the suite's
    $PROD_PY: this tool characterizes the OLD server, so it must not silently
    follow the production pin over to the zero-dependency v2 venv.
    """
    env = os.environ.get("PROD_PY_INTERNET_ARCHIVE")
    if env:
        return env
    if os.path.isfile(_POINTER):
        with open(_POINTER) as fh:
            resolved = fh.read().strip()
        if resolved:
            return resolved
    raise CaptureError(
        f"Capture interpreter not configured. Pass it as argv[1], set $GOLDEN_PYTHON, or "
        f"write the pre-migration interpreter path to {_POINTER} (gitignored). No "
        "sys.executable fallback: the golden fixture must be captured from the real legacy "
        "fastmcp interpreter, not whichever interpreter ran this script."
    )


class CaptureError(Exception):
    """Fatal capture failure -> nonzero exit with message on stderr."""


def read_line_with_timeout(proc, deadline=LINE_TIMEOUT):
    """Read one stdout line, or return None if the deadline passes first.

    select-based (F9 fleet standard); never blocks forever on a silent server.
    """
    fd = proc.stdout.fileno()
    buf = b""
    while True:
        ready, _, _ = select.select([fd], [], [], deadline)
        if not ready:
            if buf:
                # partial line then silence: treat as EOF-style failure below
                raise CaptureError(
                    f"server stdout: {deadline}s deadline expired mid-line: {buf!r}"
                )
            return None
        ch = os.read(fd, 1)
        if not ch:  # EOF
            return None
        if ch == b"\n":
            return buf
        buf += ch


def send(proc, payload):
    line = json.dumps(payload) + "\n"
    proc.stdin.write(line.encode("utf-8"))
    proc.stdin.flush()


def rpc_read(proc, want_id):
    """Return the next JSON-RPC response dict matching want_id.

    Ignores notifications and non-matching ids (servers may interleave);
    raises CaptureError on deadline/EOF or malformed JSON.
    """
    while True:
        raw = read_line_with_timeout(proc)
        if raw is None:
            raise CaptureError(
                f"server closed stdout / no response for request id={want_id}"
            )
        if not raw.strip():
            continue
        try:
            msg = json.loads(raw)
        except ValueError as exc:
            raise CaptureError(f"non-JSON line from server: {raw[:200]!r} ({exc})")
        if not isinstance(msg, dict):
            raise CaptureError(f"non-object JSON-RPC message: {raw[:200]!r}")
        if msg.get("id") == want_id:
            return msg


def check_error(msg, stage):
    if "error" in msg:
        err = msg["error"]
        raise CaptureError(
            f"{stage}: JSON-RPC error {err.get('code')}: {err.get('message')}"
        )


def main(argv):
    python = (
        argv[1]
        if len(argv) > 1
        else os.environ.get("GOLDEN_PYTHON") or default_python()
    )
    if not os.path.exists(python):
        raise CaptureError(f"interpreter not found: {python}")

    proc = subprocess.Popen(
        [python, "-m", "internet_archive_mcp.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=REPO_ROOT,
    )
    try:
        # 1. initialize — FULL valid params; an incomplete params object
        #    makes fastmcp answer -32602 (verified live).
        send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "clientInfo": {"name": "capture", "version": "0"},
                    "capabilities": {"roots": {"listChanged": False}},
                },
            },
        )
        init = rpc_read(proc, 1)
        check_error(init, "initialize")

        # 2. notifications/initialized — no id, expect no response.
        send(
            proc,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

        # 3. tools/list
        send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        listed = rpc_read(proc, 2)
        check_error(listed, "tools/list")

        try:
            tools = listed["result"]["tools"]
        except (KeyError, TypeError) as exc:
            raise CaptureError(f"tools/list result malformed: {exc}")

        if len(tools) != EXPECTED_TOOL_COUNT:
            raise CaptureError(
                f"expected {EXPECTED_TOOL_COUNT} tools, got {len(tools)}: "
                f"{[t.get('name') for t in tools]}"
            )
        for t in tools:
            if "outputSchema" not in t:
                raise CaptureError(
                    f"tool {t.get('name')!r} lacks outputSchema — legacy server "
                    "self-reports it for all 13 tools; surface changed unexpectedly"
                )

        os.makedirs(os.path.dirname(GOLDEN_PATH), exist_ok=True)
        with open(GOLDEN_PATH, "w", encoding="utf-8") as fh:
            json.dump(tools, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        rel = os.path.relpath(GOLDEN_PATH, REPO_ROOT)
        print(f"wrote {rel} ({len(tools)} tools)")
    finally:
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            proc.terminate()
            proc.wait(timeout=TERM_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=TERM_TIMEOUT)
        except OSError:
            pass


if __name__ == "__main__":
    try:
        main(sys.argv)
    except CaptureError as exc:
        print(f"golden_capture: FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
