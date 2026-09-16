"""T04 — Stateless 2026-07-28 era suite for internet-archive-mcp (committed RED).

Spec source: REFERENCE.md §1–§7 (/home/kimbo/.hermes/plans/mcp-2x-migration/REFERENCE.md)
plus the chain card _chain.md "Era suite contents (T04; 12 era + 5 regression = 17)".
transitous-mcp's on-disk tests no longer exist (fleet reset — _chain.md residue fact), so
the §7 binary skeleton is copied VERBATIM from REFERENCE §7's inline code, never from a
commit in another repo.

TDD status: RED by design until T07 greens this file. The legacy fastmcp server under
$PYO cannot satisfy the era shape (server/discover answered -32602, initialize answers
instead of -32601, tools/list carries outputSchema and no era triple, stateless tools/call
rejected, phantom notifications/message lines break garbage/non-dict/binary tests). The
exact failed/passed split with per-test reasons is recorded in the commit message; tests
that legitimately pass under legacy's lenient corners are honest, not tuned.

Zero network: the only tools/call invocations are get_item_thumbnail (pure string URL
builder — legacy client.py:679-683, sync, no HTTP), wayback_snapshots match_type="domain"
(validation raises before any request — client.py:492-496), and save_page url=""
(validation raises before auth/HTTP — client.py:699-706).
"""

import json
import os
import select
import subprocess
from pathlib import Path

# ---------------------------------------------------------------------------
# Module constants (single grep-visible flip point per card T04)
# ---------------------------------------------------------------------------
SERVER = "internet_archive_mcp.server"
# $PYO — current production interpreter (3.11 + fastmcp 3.4.7 + httpx). The package runs
# here pre-rewrite AND post-rewrite (stdlib-only imports still resolve through this venv's
# editable install); flipped to $PY2 (mcp-venvs/internet-archive-mcp-v2) in a single-line
# edit at T19. NEVER sys.executable: $PYH only imports this package by accident of its
# editable-install soup (_chain.md "Era suite contents").
PROD_PY = "/mnt/HC_Volume_105667182/kimbo/mcp-venvs/internet-archive-mcp/bin/python3"

ERA_VERSION = "2026-07-28"  # REFERENCE §1 pinned era constant

# Repo-root/golden/internet-archive.tools.json — 13 tools captured from the legacy server
# at T00 (tracked characterization spec, REFERENCE §4 golden discipline). Loaded
# unconditionally: a missing golden is a hard error, never a skip.
GOLDEN = Path(__file__).resolve().parent.parent / "golden" / "internet-archive.tools.json"

# save_page must see no credentials (test ⑩). Env is cleared at spawn (server reads
# IA_ACCESS_KEY/IA_SECRET_KEY at call time — _chain.md env-var contract row).
CLEAN_ENV = {k: v for k, v in os.environ.items() if k not in ("IA_ACCESS_KEY", "IA_SECRET_KEY")}

# Fleet F9 (the fleet's read-line-timeout lesson, _chain.md .pth-shadow row): pytest-timeout
# is NOT installed under $PYH (verified pip list 2026-09-15: pytest + pytest-asyncio only),
# so EVERY line read from a spawned server — including the REFERENCE §7 verbatim skeleton in
# test ⑰ — goes through this select()-based guard. A hung spawn must fail with a clear
# assert, never wedge the suite. This is a timeout guard, NOT a behavior deviation (no
# DEVIATION tag warranted — REFERENCE §1 deviation rule covers loop-structure drift only).
READ_TIMEOUT = 5  # seconds; legacy server startup measured ~1.6 s to first response


def read_line_with_timeout(f, sec=READ_TIMEOUT):
    """One line from a pipe, or None if nothing arrives within `sec`. Never blocks forever."""
    ready, _, _ = select.select([f], [], [], sec)
    if not ready:
        return None
    return f.readline()


class Rpc:
    """Pump one request line, read one response line. No asyncio, no framework.

    Stdout is held in BINARY mode and decoded per line so garbage bytes never reach a
    text-mode decoder on our side; the server-side reconfigure guard (REFERENCE §1) is
    what test ⑰ exercises.
    """

    def __init__(self, env=None):
        self.p = subprocess.Popen(
            [PROD_PY, "-m", SERVER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # REFERENCE §7 skeleton: logs must never pollute stdout reads
            env=CLEAN_ENV if env is None else env,
        )

    def send_line(self, raw):
        """Write one raw line (bytes or str) and flush — a buffered request = a hung test."""
        if isinstance(raw, str):
            raw = raw.encode()
        self.p.stdin.write(raw + b"\n")
        self.p.stdin.flush()

    def send(self, obj):
        self.send_line(json.dumps(obj))

    def read(self, sec=READ_TIMEOUT):
        """Next stdout line parsed as JSON, or None (timeout / EOF). F9 guard applied."""
        line = read_line_with_timeout(self.p.stdout, sec)
        if line is None:
            return None
        return json.loads(line.decode())

    def request(self, obj, sec=READ_TIMEOUT):
        """Send one id-bearing request, return the single response line (asserts it arrives).

        REFERENCE §1: every request with an id gets exactly one response — a missing line
        is a hang and fails here with a clear message, never a wedge.
        """
        self.send(obj)
        resp = self.read(sec)
        assert resp is not None, f"no response within {sec}s to {obj.get('method')!r} id={obj.get('id')!r}"
        return resp

    def alive(self):
        return self.p.poll() is None

    def kill(self):
        if self.p.poll() is None:
            self.p.kill()
            self.p.wait()


# --- helpers for result-shape asserts (small, greppable) -------------------

def assert_era_triple(result, where):
    """REFERENCE §1/§2/§4/§5 D3: every result carries the defensive triple."""
    assert result.get("resultType") == "complete", f"{where}: resultType={result.get('resultType')!r}"
    assert result.get("ttlMs") == 0, f"{where}: ttlMs={result.get('ttlMs')!r}"
    assert result.get("cacheScope") == "private", f"{where}: cacheScope={result.get('cacheScope')!r}"


def discover_req(rid, with_params=True):
    req = {"jsonrpc": "2.0", "id": rid, "method": "server/discover"}
    if with_params:
        req["params"] = {}
    return req


# ===========================================================================
# ERA TESTS (12) — REFERENCE §1–§7 gates
# ===========================================================================

def test_era_01_discover_shape():
    """① REFERENCE §2: supportedVersions == ["2026-07-28"], capabilities.tools present,
    era triple on the result."""
    rpc = Rpc()
    try:
        resp = rpc.request(discover_req(1))
        assert "result" in resp, f"discover errored: {resp.get('error')!r}"
        result = resp["result"]
        assert result.get("supportedVersions") == [ERA_VERSION], f"supportedVersions={result.get('supportedVersions')!r}"
        caps = result.get("capabilities")
        assert isinstance(caps, dict) and "tools" in caps, f"capabilities={caps!r}"
        assert_era_triple(result, "discover")
    finally:
        rpc.kill()


def test_era_02_discover_paramless():
    """② REFERENCE §2: 'The request may arrive with no `params` at all; must answer
    either way.' (Legacy fastmcp answers -32602 — era RED.)"""
    rpc = Rpc()
    try:
        resp = rpc.request(discover_req(2, with_params=False))
        assert "result" in resp, f"paramless discover errored: {resp.get('error')!r}"
        assert resp["result"].get("supportedVersions") == [ERA_VERSION]
    finally:
        rpc.kill()


def test_era_03_initialize_rejected_then_discover_same_pipe():
    """③ REFERENCE §3 (PLAN D2): initialize → -32601, never hang, never close — the
    same-pipe server/discover must then succeed and the process stay alive."""
    rpc = Rpc()
    try:
        resp = rpc.request({
            "jsonrpc": "2.0", "id": 10, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "era-suite", "version": "0"}},
        })
        assert "error" in resp, f"initialize was answered, not rejected -32601: keys={sorted(resp)!r}"
        assert resp["error"].get("code") == -32601, f"code={resp['error'].get('code')!r}"
        assert resp["id"] == 10, f"id not echoed: {resp['id']!r}"
        assert rpc.alive(), "process died after initialize rejection (§3: never exit)"
        resp2 = rpc.request(discover_req(11))
        assert "result" in resp2, f"same-pipe discover failed: {resp2.get('error')!r}"
        assert resp2["result"].get("supportedVersions") == [ERA_VERSION]
        assert rpc.alive(), "process died after §3 sequence"
    finally:
        rpc.kill()


def test_era_04_notification_initialized_swallowed():
    """④ REFERENCE §6: id-less notifications/initialized gets NO response; the next
    request's id on the next line proves correlation was not corrupted."""
    rpc = Rpc()
    try:
        rpc.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        resp = rpc.request(discover_req(42))  # first line read must be THIS response
        assert resp["id"] == 42, f"phantom response to the notification: {resp!r}"
        assert "result" in resp, f"discover after notification errored: {resp.get('error')!r}"
    finally:
        rpc.kill()


def test_era_05_ping_returns_empty_object():
    """⑤ REFERENCE §6: ping is a REQUEST with an id; answer exactly `{}` (the form the
    spec pins; Hermes keepalive mcp_tool_health.py:168)."""
    rpc = Rpc()
    try:
        resp = rpc.request({"jsonrpc": "2.0", "id": 5, "method": "ping", "params": {}})
        assert resp.get("result") == {}, f"ping result={resp.get('result')!r} error={resp.get('error')!r}"
    finally:
        rpc.kill()


def test_era_06_unknown_method_gets_32601():
    """⑥ REFERENCE §3/§6: era-absent resources/list (no such capability advertised) →
    -32601, not -32602/-32603."""
    rpc = Rpc()
    try:
        resp = rpc.request({"jsonrpc": "2.0", "id": 6, "method": "resources/list", "params": {}})
        assert "error" in resp, f"resources/list answered with a result: {sorted(resp.get('result', {}))!r}"
        assert resp["error"].get("code") == -32601, f"code={resp['error'].get('code')!r} (legacy fastmcp says -32602)"
        assert resp["id"] == 6
    finally:
        rpc.kill()


def test_era_07_tools_list_golden_identity_and_no_output_schema():
    """⑦ REFERENCE §4: triple + 13 tools byte-identical to golden (stripping
    outputSchema + _meta from the OLD capture) + outputSchema ABSENT everywhere
    (the D3 trap: a declared schema makes the 2.0 client reject every text result)."""
    golden = json.loads(GOLDEN.read_text())  # unconditional load — tracked spec, never skip
    assert len(golden) == 13, f"golden holds {len(golden)} tools"

    def frozen(tool):
        # .gitignore keeps golden tracked; strip framework-era keys per REFERENCE §4:
        # old fastmcp goldens HAVE outputSchema (13/13 at capture) and _meta.fastmcp —
        # neither is frozen surface, both are stripped before the byte-compare.
        return {k: tool[k] for k in ("name", "description", "inputSchema") if k in tool}

    by_name = {t["name"]: frozen(t) for t in golden}
    rpc = Rpc()
    try:
        resp = rpc.request({"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {}})
        assert "result" in resp, f"stateless tools/list errored (legacy needs initialize): {resp.get('error')!r}"
        result = resp["result"]
        assert_era_triple(result, "tools/list")
        tools = result.get("tools")
        assert isinstance(tools, list) and len(tools) == 13, f"{len(tools) if isinstance(tools, list) else tools!r} tools"
        for t in tools:
            assert "outputSchema" not in t, f"§4 trap: {t.get('name')!r} declares outputSchema"
        names = {t["name"] for t in tools}
        assert names == set(by_name), f"name set drift: missing={set(by_name) - names} extra={names - set(by_name)}"
        for t in tools:  # order-insensitive by name
            assert frozen(t) == by_name[t["name"]], f"byte-identity broken for {t['name']!r}"
    finally:
        rpc.kill()


def test_era_08_tools_call_thumbnail_str_passthrough():
    """⑧ REFERENCE §5: stateless tools/call works with NO initialize handshake; triple
    rides the result; get_item_thumbnail's str result passes through VERBATIM as the text
    block (no JSON quotes/escapes — §5 'String results pass through verbatim')."""
    rpc = Rpc()
    try:
        resp = rpc.request({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                            "params": {"name": "get_item_thumbnail",
                                       "arguments": {"identifier": "greatest_gen"}}})
        assert "result" in resp, f"stateless tools/call errored: {resp.get('error')!r}"
        result = resp["result"]
        assert_era_triple(result, "tools/call")
        content = result.get("content")
        assert isinstance(content, list) and len(content) >= 1, f"content={content!r}"
        block = content[0]
        assert block.get("type") == "text", f"block={block!r}"
        assert block.get("text") == "https://archive.org/services/img/greatest_gen", \
            f"text not verbatim str passthrough: {block.get('text')!r}"
        assert result.get("isError") is not True
    finally:
        rpc.kill()


def test_era_09_domain_match_auth_error_is_plain_result():
    """⑨ REFERENCE §5 + _chain R3 evidence: legacy folds the ValueError into a plain
    'Error: ...' STRING result — success-shaped, NOT isError-true. Validation raises
    before any HTTP: network-free. (Legacy needs initialize first, so it errors -32602
    here — the era shape must answer stateless.)"""
    rpc = Rpc()
    try:
        resp = rpc.request({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                            "params": {"name": "wayback_snapshots",
                                       "arguments": {"url": "https://example.com",
                                                     "match_type": "domain"}}})
        assert "result" in resp, f"stateless call errored: {resp.get('error')!r}"
        result = resp["result"]
        content = result.get("content")
        assert isinstance(content, list) and content, f"content={content!r}"
        text = content[0].get("text", "")
        assert text.startswith("Error: match_type='domain' requires authentication"), f"text={text!r}"
        assert result.get("isError") is not True, "legacy parity: error STRING is a successful result (R3)"
    finally:
        rpc.kill()


def test_era_10_save_page_empty_url_no_env():
    """⑩ tools/call save_page(url="") spawned with IA_* cleared → the legacy fold text
    ('URL must not be empty' before the auth check — client.py:699-706): still an error
    STRING result, no isError flip, no network attempt."""
    rpc = Rpc(env=CLEAN_ENV)
    try:
        resp = rpc.request({"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                            "params": {"name": "save_page", "arguments": {"url": ""}}})
        assert "result" in resp, f"stateless call errored: {resp.get('error')!r}"
        result = resp["result"]
        content = result.get("content")
        assert isinstance(content, list) and content, f"content={content!r}"
        text = content[0].get("text", "")
        assert text.startswith("Error:"), f"text={text!r}"
        assert "must not be empty" in text or "authentication" in text, \
            f"neither empty-url nor auth guidance: {text!r}"
        assert result.get("isError") is not True
    finally:
        rpc.kill()


def test_era_11_unknown_tool_is_error_result():
    """⑪ REFERENCE §5 fleet rule: unknown tool → {'error': 'Unknown tool: …'}-shaped
    RESULT with isError true — never a JSON-RPC error, never a crash."""
    rpc = Rpc()
    try:
        resp = rpc.request({"jsonrpc": "2.0", "id": 11, "method": "tools/call",
                            "params": {"name": "no_such_tool", "arguments": {}}})
        assert "result" in resp, f"unknown tool raised a JSON-RPC error: {resp.get('error')!r}"
        result = resp["result"]
        assert result.get("isError") is True, f"isError={result.get('isError')!r}"
        content = result.get("content")
        assert isinstance(content, list) and content, f"content={content!r}"
        assert "Unknown tool" in content[0].get("text", ""), f"text={content[0].get('text')!r}"
        assert_era_triple(result, "unknown-tool result")
    finally:
        rpc.kill()


def test_era_12_tools_call_missing_params_32602():
    """⑫ REFERENCE §5: tools/call whose params object or string 'name' is missing is a
    DISPATCH failure → -32602 (checked upfront, never a KeyError-32603), then the same
    pipe stays live."""
    rpc = Rpc()
    try:
        resp = rpc.request({"jsonrpc": "2.0", "id": 12, "method": "tools/call",
                            "params": {"arguments": {}}})  # name missing
        assert "error" in resp, f"missing-name call answered a result: {sorted(resp)!r}"
        assert resp["error"].get("code") == -32602, f"code={resp['error'].get('code')!r}"
        resp2 = rpc.request({"jsonrpc": "2.0", "id": 13, "method": "tools/call"})  # no params at all
        assert resp2["error"].get("code") == -32602, f"code={resp2.get('error', {}).get('code')!r}"
        resp3 = rpc.request({"jsonrpc": "2.0", "id": 14, "method": "ping", "params": {}})
        assert resp3.get("result") == {}, f"pipe died after -32602: {resp3!r}"
        assert rpc.alive()
    finally:
        rpc.kill()


# ===========================================================================
# REGRESSION TESTS (5) — REFERENCE §1/§7 server-killer guards
# ===========================================================================

def test_reg_13_garbage_line_then_valid_discover():
    """⓬ REFERENCE §7: a non-JSON line is skipped, NEVER fatal; the next valid request's
    response is the FIRST line back (a phantom response breaks correlation) and the
    process is alive."""
    rpc = Rpc()
    try:
        rpc.send_line(b"this is not json {{{")
        resp = rpc.request(discover_req(21))
        assert resp["id"] == 21, f"phantom line consumed the response: {resp!r}"
        assert "result" in resp, f"discover after garbage errored: {resp.get('error')!r}"
        assert rpc.alive()
    finally:
        rpc.kill()


def test_reg_14_non_dict_json_lines_are_skipped():
    """⓭ REFERENCE §1 isinstance-req-dict guard: valid JSON that isn't an object
    (5, null, [1,2]) is skipped like garbage — no crash, no response, next real
    request answers on the very next line. (Legacy fastmcp emits a
    notifications/message 'Internal Server Error' phantom line per bad input — RED.)"""
    rpc = Rpc()
    try:
        for raw in (b"5", b"null", b"[1,2]"):
            rpc.send_line(raw)
        resp = rpc.request(discover_req(22))
        assert resp.get("id") == 22 and "result" in resp, \
            f"first line back after non-dict garbage is not the discover response: {resp!r}"
        assert rpc.alive()
    finally:
        rpc.kill()


def test_reg_15_null_and_non_string_method_route_as_32601():
    """⓮ REFERENCE §1: method null/42 must route as unknown-method (§3 -32601), not
    crash the .startswith dispatch. Both must answer with an error, in order, alive."""
    rpc = Rpc()
    try:
        resp = rpc.request({"jsonrpc": "2.0", "id": 23, "method": None})
        assert "error" in resp, f"null-method answered a result: {resp!r}"
        assert resp["error"].get("code") == -32601, f"code={resp['error'].get('code')!r}"
        resp2 = rpc.request({"jsonrpc": "2.0", "id": 24, "method": 42, "params": {}})
        assert resp2["error"].get("code") == -32601, f"code={resp2.get('error', {}).get('code')!r}"
        assert rpc.alive()
    finally:
        rpc.kill()


def test_reg_16_id_less_unknown_method_is_silent():
    """⓯ REFERENCE §1/§3: an UNKNOWN method with no id is still a notification — never
    respond. The next request's id on the FIRST line proves silence."""
    rpc = Rpc()
    try:
        rpc.send({"jsonrpc": "2.0", "method": "bogus/nothing"})
        resp = rpc.request({"jsonrpc": "2.0", "id": 25, "method": "ping", "params": {}})
        assert resp["id"] == 25, f"server answered an id-less request: {resp!r}"
        assert resp.get("result") == {}
        assert rpc.alive()
    finally:
        rpc.kill()


def test_reg_17_binary_garbage_stream_survives_and_exits_zero():
    """⓰ REFERENCE §7 binary skeleton — copied VERBATIM (G→D→G→L, stderr=DEVNULL,
    id-correlation, rc==0 on stdin EOF), with the mandated F9 read_line_with_timeout
    wrapper on every stdout read (REQUIRED: pytest-timeout absent under $PYH) and
    `-m SERVER` module invocation per PROD_PY. Proves the §1 reconfigure(errors=
    'replace') guard: invalid UTF-8 becomes a garbage line, not a UnicodeDecodeError."""
    p = subprocess.Popen([PROD_PY, "-m", SERVER], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        p.stdin.write(b"\xff\xfe\x00garbage\n")                      # G — pre-stream invalid UTF-8
        discover_line = json.dumps(discover_req(1))
        p.stdin.write(discover_line.encode() + b"\n"); p.stdin.flush()               # D
        raw = read_line_with_timeout(p.stdout, READ_TIMEOUT)
        assert raw is not None, "no response to discover after leading binary garbage (F9 guard)"
        resp = json.loads(raw)
        assert resp["id"] == 1 and resp["result"]["supportedVersions"] == [ERA_VERSION]
        p.stdin.write(b"\x00\xff\n")                                 # G — mid-stream garbage
        tools_line = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        p.stdin.write(tools_line.encode() + b"\n"); p.stdin.flush()                  # L
        raw2 = read_line_with_timeout(p.stdout, READ_TIMEOUT)
        assert raw2 is not None, "no response to tools/list after mid-stream garbage (F9 guard)"
        resp2 = json.loads(raw2)                                     # id==2 proves no phantom response to G
        assert resp2["id"] == 2 and "result" in resp2
        assert p.poll() is None
        p.stdin.close(); assert p.wait(timeout=5) == 0               # clean EOF exit
    finally:
        if p.poll() is None: p.kill(); p.wait()
