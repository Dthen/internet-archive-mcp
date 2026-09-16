"""Encoder differential: golden/legacy-requests.json vs the seam's encode_params.

Standing proof for chain §5 "derive encoding from the legacy server": for every
frozen legacy case, urllib's urlencode (via transport.encode_params — the ONLY
encoder the migrated client will use) must reproduce BYTE-IDENTICALLY the raw
query httpx 0.28.1 actually put on the wire. Captured pre-rewrite by
tools/characterize_old_server.py under the legacy async client; the fixture is
now the frozen truth (post-T03 the harness can never re-run — chain stance).

Hermetic: stdlib + seam only (no fastmcp, no httpx, no network, no async
client). Runs under $PYH.
"""

import json
from pathlib import Path

import pytest

from internet_archive_mcp.transport import encode_params

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUESTS = REPO_ROOT / "golden" / "legacy-requests.json"
BEHAVIOR = REPO_ROOT / "golden" / "legacy-behavior.json"

_requests = json.loads(REQUESTS.read_text(encoding="utf-8"))
CASES = {c["id"]: c for c in _requests["cases"]}


def _case_ids():
    ids = sorted(CASES)
    assert len(ids) >= 20, f"fixture lost coverage: {len(ids)} cases"
    assert len(CASES) == len(_requests["cases"]), "duplicate case ids in fixture"
    return ids


@pytest.mark.parametrize("case_id", _case_ids())
def test_encoder_byte_equality_with_legacy_wire(case_id):
    case = CASES[case_id]
    # Rebuild the SAME param list the legacy call site handed httpx (frozen as
    # [key, str(value)] pairs; urlencode stringifies non-str values exactly
    # this way, so the replay is faithful).
    params = [tuple(p) for p in case["params"]]
    assert encode_params(params) == case["raw_query"], (
        f"{case_id}: seam encoder drifted from the legacy wire bytes\n"
        f"  encode_params: {encode_params(params)!r}\n"
        f"  httpx raw:     {case['raw_query']!r}"
    )


def test_method_and_full_path_frozen():
    # The port's method mix: GET everywhere, POST only for save_page, and the
    # POST path keeps the unquoted target URL (tag client.py l.655-657).
    post = [c for c in _requests["cases"] if c["method"] == "POST"]
    assert [c["id"] for c in post] == ["save_page_post_fake_creds"]
    for c in post:
        assert c["path"] == "/save/https://example.com/page"
        assert c["raw_query"] == ""  # legacy POSTs carry no body/query
        assert c["headers"]["authorization"] == "LOW AK:SK"
        assert c["headers"]["accept"] == "application/json"
    assert all(c["method"] == "GET" for c in _requests["cases"]
               if c["method"] != "POST")
    # Full on-wire path (path + raw query) is stable per case.
    for c in _requests["cases"]:
        full = c["path"] + ("?" + c["raw_query"] if c["raw_query"] else "")
        assert full.startswith("/")


def test_query_encoding_parity_shapes():
    # Repeated bracketed keys + colon/quote escapes came off httpx's wire in
    # the same shape urllib produces (the chain's live-probe claim, now
    # fixture-backed case by case).
    blob = " ".join(c["raw_query"] for c in _requests["cases"])
    assert "fl%5B%5D=" in blob
    assert "sort%5B%5D=" in blob
    assert "filter=" in blob
    assert "%3A" in blob  # ':' encoded, urlencode parity


def test_r3_empty_list_fact_fixture_backed():
    # R3: legacy emits content: [] (ZERO blocks) for empty-list results —
    # golden/legacy-behavior.json is the evidence pinned by the card; T07's
    # DEVIATION tag test references this case id.
    behavior = json.loads(BEHAVIOR.read_text(encoding="utf-8"))
    ids = [c["id"] for c in behavior["cases"]]
    assert "snapshots_empty" in ids
    empty = next(c for c in behavior["cases"] if c["id"] == "snapshots_empty")
    assert empty["content_blocks"] == []
    assert empty["is_error"] is False
