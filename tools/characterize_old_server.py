#!/usr/bin/env python3
"""Characterize the LEGACY (pre-T03) server: wire URLs + tool return shapes.

Pre-rewrite capture ONLY — must run under $PYO (fastmcp 3.4.7 + httpx 0.28.1)
against the async ArchiveClient as it exists at tag pre-migration/20260914:

    timeout 30 <PYO> tools/characterize_old_server.py

(F9 fleet standard: the outer `timeout 30` supplies the deadline. Everything
runs IN-PROCESS over httpx.MockTransport, so there are zero spawned-server
stdout reads and zero real network — save_page uses the fake literal creds
"AK"/"SK" only.)

Outputs (tracked golden per .gitignore `!golden/*.json`):
- golden/legacy-requests.json — for every param branch of ArchiveClient._request:
  the exact method / path / RAW query bytes httpx put on the wire, the call-site
  param list, and lowercased headers. tests/test_encoder_differential.py replays
  transport.encode_params(params) against raw_query as the standing
  encoder-parity gate (recipe/§5 "derive encoding from the legacy server").
- golden/legacy-behavior.json — the fastmcp tool-result WIRE shapes: content
  blocks, is_error, structured-content presence. The R3 fact (empty-list
  wayback_snapshots -> content: [] with ZERO blocks) is fixture-captured here.

After T03 deletes the async client / httpx seam, this script can never run
again — the committed JSON files ARE the frozen truth (chain stance).

Failure stance: loud. Any unexpected request path, wrong request count, or
shape assertion exits nonzero.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess

import fastmcp
import httpx

from internet_archive_mcp import server
from internet_archive_mcp.client import DEFAULT_USER_AGENT, ArchiveClient

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN_DIR = os.path.join(REPO_ROOT, "golden")

HTTPX_VERSION = httpx.__version__
try:
    TAG_SHA = subprocess.run(
        ["git", "rev-parse", "--short", "pre-migration/20260914"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
except subprocess.CalledProcessError as exc:
    raise SystemExit(f"characterize_old_server: cannot resolve tag sha: {exc}")


def make_client(handler):
    """ArchiveClient over MockTransport, mirroring the legacy default client
    (tag client.py l.55-59: UA header, follow_redirects=True, timeout=30.0)."""
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"User-Agent": DEFAULT_USER_AGENT},
        follow_redirects=True,
        timeout=30.0,
    )
    return ArchiveClient(client=http_client, min_request_interval=0)


def compact(obj):
    """The legacy wire encoding rule for non-str results (fastmcp 3.4.7):
    JSON with separators=(',', ':'), ensure_ascii=False."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------------------
# Canned responses per endpoint family (path-prefix allow-lists)
# ---------------------------------------------------------------------------

def _docs_response(num_found=3, docs=None):
    return httpx.Response(
        200,
        json={"response": {"numFound": num_found, "start": 0,
                           "docs": docs if docs is not None
                           else [{"identifier": "d1", "title": "T"}]}},
    )


CDX_HEADER = ["urlkey", "timestamp", "original", "mimetype", "statuscode"]
CDX_ROWS = [["com,example)/page", "20200101120000", "https://example.com/page",
             "text/html", "200"]]
# header row + data rows + the empty [] separator + [resumeKey] row the CDX
# API appends when showResumeKey=true and more results exist (tag client.py
# l.517-539 parses exactly this shape).
CDX_BODY = [CDX_HEADER] + CDX_ROWS + [[]] + [["RK+abc/1"]]

SEARCH = {"/advancedsearch.php": lambda r: _docs_response()}
SCRAPE = {"/services/search/v1/scrape": lambda r: httpx.Response(
    200, json={"items": [{"identifier": "d1"}], "total": 1, "count": 1,
               "cursor": "c1"})}
METADATA = {"/metadata/": lambda r: httpx.Response(
    200, json={"identifier": "greatest_gen",
                "metadata": {"title": "T", "mediatype": "texts"},
                "server": "ia-server-1",
                "files": [{"name": "f.txt", "format": "Text PDF",
                           "size": "10"}]})}
CDX = {"/cdx/search/cdx": lambda r: httpx.Response(
    200, json=CDX_BODY)}
AVAIL = {"/wayback/available": lambda r: httpx.Response(
    200, json={"url": "https://example.com", "archived_snapshots": {}})}
FETCH = {"/web/": lambda r: httpx.Response(200, text="<html>archived</html>")}
SPN2 = {"/save/status/": lambda r: httpx.Response(
    200, json={"status": "success", "job_id": "AK20200101T000000X"}),
    "/save/": lambda r: httpx.Response(
    200, json={"url": "https://example.com/page",
                "job_id": "AK20200101T000000X", "status": "scheduled"})}


def canned_handler(allow):
    """MockTransport handler answering one family allow-list; loud on surprises."""
    def handler(request):
        path = request.url.path
        for prefix, respond in allow.items():
            if path.startswith(prefix):
                return respond(request)
        raise AssertionError(
            f"unexpected request {request.method} {path} "
            f"(allowed prefixes: {sorted(allow)})")
    return handler


# ---------------------------------------------------------------------------
# Part 1 — legacy-requests.json: exact wire bytes per param branch
# ---------------------------------------------------------------------------

# (id, allowed-families, coroutine built against a fresh client)
REQUEST_CASES = [
    ("search_default", SEARCH,
     lambda c: c.search_archive("jazz piano")),
    ("search_fields_and_3sorts_cap", SEARCH,
     lambda c: c.search_archive(
         "creator:ellis AND title:'jazz piano'!",
         fields=["identifier", "title"],
         sort=["downloads desc", "year asc", "title", "fourth-dropped-by-cap"])),
    ("search_mediatype_collection_prefixes", SEARCH,
     lambda c: c.search_archive("einstein", mediatype="texts",
                                collection="gutenbergetexts")),
    ("deep_fields_join_sorts_dedupe_identifier_last", SCRAPE,
     lambda c: c.search_archive_deep(
         "mediaType:etree", fields=["identifier", "title"],
         sorts=["downloads desc", "identifier", "year asc", "identifier"])),
    ("deep_cursor", SCRAPE,
     lambda c: c.search_archive_deep("tag:soundtrack",
                                     cursor="bG9uZzpjdXJzb3I=")),
    ("deep_total_only", SCRAPE,
     lambda c: c.search_archive_deep("format:pdf", total_only=True)),
    ("metadata_plain", METADATA,
     lambda c: c.get_item_metadata("greatest_gen")),
    ("metadata_needsquote_identifier", METADATA,
     lambda c: c.get_item_metadata("a b/c:d!e'f")),
    ("list_item_files_format_filter", METADATA,
     lambda c: c.list_item_files("greatest_gen", format_filter="text pdf")),
    ("snapshots_minimal", CDX,
     lambda c: c.wayback_snapshots("example.com/page")),
    ("snapshots_all_optionals", CDX,
     lambda c: c.wayback_snapshots(
         "example.com/page", match_type="prefix", from_year=2019, to_year=2021,
         limit=10, filter_expr=["statuscode:200", "mimetype:text/html"],
         collapse="urlkey", fields=["timestamp", "original"], page=2,
         show_resume_key=True, resume_key="rk+1", newest=True, fast_latest=True)),
    ("snapshots_filter_str", CDX,
     lambda c: c.wayback_snapshots("example.com",
                                    filter_expr="statuscode:200", limit=1)),
    ("availability", AVAIL,
     lambda c: c.wayback_availability("https://example.com/x?a=1&b=2")),
    ("fetch_raw_with_timestamp", FETCH,
     lambda c: c.wayback_fetch("https://example.com/page",
                               timestamp="20200101120000", raw=True)),
    ("fetch_raw_no_timestamp_id_only", FETCH,
     lambda c: c.wayback_fetch("https://example.com/page", raw=True)),
    ("fetch_nonraw_with_timestamp", FETCH,
     lambda c: c.wayback_fetch("https://example.com/page",
                               timestamp="20200101120000", raw=False)),
    ("fetch_nonraw_no_timestamp_plain_web_shape", FETCH,
     lambda c: c.wayback_fetch("https://example.com/page", raw=False)),
    ("save_page_post_fake_creds", SPN2,
     lambda c: c.save_page("https://example.com/page",
                           access_key="AK", secret_key="SK")),
    ("save_status_quoted_job_id", SPN2,
     lambda c: c.save_page_status("spn1:20200101T000000Z:example.com")),
    ("browse_collection_sort_default", SEARCH,
     lambda c: c.browse_collection("librivoxaudio", rows=5, page=2)),
    ("get_item_reviews", METADATA,
     lambda c: c.get_item_reviews("greatest_gen")),
    ("get_collection_info", METADATA,
     lambda c: c.get_collection_info("gutenberg")),
]


async def capture_requests():
    cases = []
    for cid, allow, invoke in REQUEST_CASES:
        recorded = []

        def handler(request, cid=cid, allow=allow, recorded=recorded):
            # Defaults bind the loop variables per iteration (B023 hygiene).
            raw = request.url.raw_path.decode("utf-8", errors="surrogatepass")
            path, _, query = raw.partition("?")
            recorded.append({
                "id": cid,
                "method": request.method,
                "path": path,
                "raw_query": query,
                "headers": {k.lower(): v for k, v in request.headers.items()},
            })
            return canned_handler(allow)(request)

        client = make_client(handler)
        # Spy on the call site to freeze the params list / literal URL the
        # legacy client hands to httpx (the differential replays encode_params
        # on exactly this list; raw_query stays httpx's own wire bytes).
        orig_request = client._client.request
        calls = []

        async def spied(method, url, *, params=None, headers=None,
                        calls=calls, orig_request=orig_request, **kw):
            calls.append((method, url, params))
            return await orig_request(method, url, params=params,
                                      headers=headers, **kw)

        client._client.request = spied
        try:
            await invoke(client)
        finally:
            await client.aclose()
        if len(recorded) != 1 or len(calls) != 1:
            raise AssertionError(
                f"case {cid}: expected exactly 1 request, got "
                f"{len(recorded)} wire / {len(calls)} call-site")
        (_method, url, params) = calls[0]
        if params is None:
            pairs = []
        elif isinstance(params, dict):
            pairs = [list(kv) for kv in params.items()]
        else:
            pairs = [list(p) for p in params]
        rec = recorded[0]
        rec["url"] = url
        rec["params"] = [[k, str(v)] for k, v in pairs]
        cases.append(rec)
    return cases


# ---------------------------------------------------------------------------
# Part 2 — legacy-behavior.json: fastmcp tool-result wire shapes
# ---------------------------------------------------------------------------

def _fail(cid, msg):
    raise AssertionError(f"behavior case {cid}: {msg}")


def _one_text(blocks, texts, cid):
    if len(blocks) != 1 or blocks[0].get("type") != "text":
        _fail(cid, f"expected 1 text block, got {blocks!r}")


def _assert_compact(cid, texts, kind):
    """Exactly one text block whose payload re-serializes byte-identically
    under the legacy compact rule (separators=(',',':'), ensure_ascii=False)."""
    obj = json.loads(texts[0])
    if not isinstance(obj, kind):
        _fail(cid, f"expected {kind.__name__} payload, got {type(obj).__name__}")
    if texts[0] != compact(obj):
        _fail(cid, f"not compact: {texts[0][:120]!r}")


def check_snapshots_empty(cid, blocks, texts, res):
    if blocks != []:
        _fail(cid, f"R3 fact violated, blocks={blocks!r}")


def check_snapshots_nonempty(cid, blocks, texts, res):
    _one_text(blocks, texts, cid)
    _assert_compact(cid, texts, list)


def check_search_basic(cid, blocks, texts, res):
    _one_text(blocks, texts, cid)
    _assert_compact(cid, texts, dict)


def check_search_warning(cid, blocks, texts, res):
    check_search_basic(cid, blocks, texts, res)
    if '"_warning"' not in texts[0]:
        _fail(cid, f"_warning missing: {texts[0][:200]!r}")


def check_search_nonascii(cid, blocks, texts, res):
    check_search_basic(cid, blocks, texts, res)
    if "für" not in texts[0] or "\\u" in texts[0]:
        _fail(cid, f"non-ASCII not raw UTF-8: {texts[0][:200]!r}")


def check_thumbnail(cid, blocks, texts, res):
    _one_text(blocks, texts, cid)
    if texts != ["https://archive.org/services/img/greatest_gen"]:
        _fail(cid, f"not verbatim str: {texts!r}")


def check_metadata_notfound(cid, blocks, texts, res):
    _one_text(blocks, texts, cid)
    if texts != ["Error: Item not found: greatest_gen"]:
        _fail(cid, f"not error string: {texts!r}")
    if res.is_error is not False:
        _fail(cid, f"isError leaked: {res.is_error!r}")


def check_save_no_creds(cid, blocks, texts, res):
    _one_text(blocks, texts, cid)
    if ("Save Page Now requires authentication" not in texts[0]
            or "s3.php" not in texts[0]):
        _fail(cid, f"not auth guidance: {texts!r}")


def check_save_fake_creds(cid, blocks, texts, res):
    _one_text(blocks, texts, cid)
    _assert_compact(cid, texts, dict)
    if '"job_id"' not in texts[0]:
        _fail(cid, f"job_id missing: {texts[0]!r}")


# (id, tool, arguments, http-families or None, shape-check(blocks, texts, result))
# A check raising AssertionError fails the capture loudly.
BEHAVIOR_CASES = [
    ("snapshots_empty", "wayback_snapshots",
     {"url": "example.com/nothing"},
     {"/cdx/search/cdx": lambda r: httpx.Response(200, json=[])},
     check_snapshots_empty),
    ("snapshots_nonempty_compact_json", "wayback_snapshots",
     {"url": "example.com/page"}, CDX, check_snapshots_nonempty),
    ("search_basic_compact_dict", "search_archive",
     {"query": "jazz piano"}, SEARCH, check_search_basic),
    ("search_warning_numfound_cap", "search_archive",
     {"query": "einstein"},
     {"/advancedsearch.php": lambda r: _docs_response(num_found=12345)},
     check_search_warning),
    ("search_nonascii_raw_utf8", "search_archive",
     {"query": "museum"},
     {"/advancedsearch.php": lambda r: _docs_response(
         docs=[{"identifier": "m1", "naam": "Museum für Kunst"}])},
     check_search_nonascii),
    ("thumbnail_verbatim_str", "get_item_thumbnail",
     {"identifier": "greatest_gen"}, None, check_thumbnail),
    ("metadata_notfound_error_str", "get_item_metadata",
     {"identifier": "greatest_gen"},
     {"/metadata/": lambda r: httpx.Response(200, json={})},
     check_metadata_notfound),
    ("save_page_missing_creds_auth_guidance", "save_page",
     {"url": "https://example.com"}, None, check_save_no_creds),
    ("save_page_fake_creds_post", "save_page",
     {"url": "https://example.com/page", "access_key": "AK", "secret_key": "SK"},
     SPN2, check_save_fake_creds),
]


def _blocks(result):
    return [b.model_dump(mode="json", exclude_none=True)
            for b in result.content]


async def capture_behavior():
    cases = []
    for cid, tool, args, allow, check in BEHAVIOR_CASES:
        handler = canned_handler(allow) if allow else None
        client = make_client(handler) if handler else \
            ArchiveClient(min_request_interval=0)
        server._client = client
        client.clear_cache()
        try:
            result = await server.mcp.call_tool(tool, args)
        finally:
            await client.aclose()
        blocks = _blocks(result)
        texts = [b.get("text", "") for b in blocks]
        check(cid, blocks, texts, result)
        cases.append({
            "id": cid,
            "tool": tool,
            "arguments": args,
            "content_blocks": blocks,
            "is_error": bool(result.is_error),
            "has_structured_content": result.structured_content is not None,
        })
    return cases


# ---------------------------------------------------------------------------

def main():
    # Deterministic env for the missing-creds case (server reads at call time,
    # tag server.py l.381-382 — fake creds only, zero real auth traffic).
    os.environ.pop("IA_ACCESS_KEY", None)
    os.environ.pop("IA_SECRET_KEY", None)

    request_cases = asyncio.run(capture_requests())
    behavior = asyncio.run(capture_behavior())

    note = {
        "httpx": HTTPX_VERSION,
        "fastmcp": fastmcp.__version__,
        "captured": "2026-09-16",
        "tag_sha": TAG_SHA,
        "capture": "in-process legacy ArchiveClient over httpx.MockTransport "
                   "(zero real network, zero spawned subprocesses — F9 bound "
                   "supplied by the `timeout 30` wrapper)",
    }
    os.makedirs(GOLDEN_DIR, exist_ok=True)
    with open(os.path.join(GOLDEN_DIR, "legacy-requests.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"note": note, "cases": request_cases}, fh,
                  indent=2, ensure_ascii=False)
        fh.write("\n")

    with open(os.path.join(GOLDEN_DIR, "legacy-behavior.json"), "w",
              encoding="utf-8") as fh:
        json.dump({
            "note": note,
            "legacy_notes": [
                ("capture method: in-process fastmcp mcp.call_tool -> ToolResult; "
                 "result.content blocks are wire-identical to the stdio "
                 "CallToolResult content blocks (verified live against the "
                 "spawned server, 2026-09-14, _chain.md)."),
                ("fastmcp additionally emitted structuredContent "
                 "{'result': ...} on every sampled call (including the empty "
                 "content: [] case) — legacy-framework plumbing ONLY. The "
                 "migrated server emits NONE (D3 trap): has_structured_content "
                 "is documentation, never an assertion target post-T07."),
                ("R3 fact: empty-list wayback_snapshots -> content: [] (ZERO "
                 "blocks) — case id 'snapshots_empty', referenced by T07's "
                 "DEVIATION tag test."),
                ("is_error=False on error-string results is legacy behavior "
                 "(handlers return 'Error: ...' strings as successful results)."),
                ("compact-JSON rule (dict/list results): separators=(',',':'), "
                 "ensure_ascii=False — asserted byte-equality in this harness; "
                 "str results pass through VERBATIM, no JSON wrap."),
            ],
            "cases": behavior,
        }, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    print(f"wrote golden/legacy-requests.json ({len(request_cases)} cases) "
          f"+ golden/legacy-behavior.json ({len(behavior)} cases)")


if __name__ == "__main__":
    main()
