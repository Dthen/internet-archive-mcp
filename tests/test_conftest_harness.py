"""Contract tests for the T08 conftest harness (fake-urlopen seam).

These are this task's green teeth: the harness must reproduce the legacy
httpx.MockTransport affordances the T09-T17 family ports rely on.
"""

from __future__ import annotations

import urllib.error

import pytest

import internet_archive_mcp.transport as transport
from conftest import FakeRequest, FakeTransport, json_response, make_mock_client, text_response
from internet_archive_mcp.client import DEFAULT_USER_AGENT, ArchiveClient
from internet_archive_mcp.transport import SeamResponse, fetch_raw


def test_handler_sees_get_full_url_params_and_ua() -> None:
    """① handler receives GET with full url + params + UA header."""
    seen: dict = {}

    def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["params"] = request.url.params
        seen["ua"] = request.headers.get("user-agent", "")
        return json_response({"response": {"numFound": 0, "start": 0, "docs": []}})

    ac = make_mock_client(handler)
    result = ac.search_archive("test", rows=10, page=2, fields=["identifier"])
    assert result["numFound"] == 0
    assert seen["method"] == "GET"
    # Byte-for-byte full URL (scheme://host + path + ?query), like httpx showed.
    assert seen["url"].startswith("https://archive.org/advancedsearch.php?q=")
    assert "output=json" in seen["url"] and "rows=10" in seen["url"] and "page=2" in seen["url"]
    assert seen["params"].get("rows") == "10"
    assert seen["params"].get_list("fl[]") == ["identifier"]
    assert seen["ua"] == DEFAULT_USER_AGENT
    # url IS the string (str subclass), so ==-comparisons transfer too.
    assert seen["url"] == str(seen["url"])
    # The fake is the live seam patch site while installed (one patch site;
    # the autouse fixture restores the real urllib function at teardown).
    live = transport._urlopen
    assert isinstance(live, FakeTransport) and len(live.requests) == 1
    assert isinstance(live.requests[0], FakeRequest)


def test_json_response_roundtrip_via_seamresponse() -> None:
    """② json_response -> SeamResponse .json() roundtrip through the seam."""
    payload = {"docs": [{"identifier": "abc", "title": "Museum für Kunst"}], "n": 3}
    status, headers, body = json_response(payload)
    resp = SeamResponse(status, headers, body.decode("utf-8"), "https://archive.org/x")
    assert resp.json() == payload
    # And the same canned tuple, served by the fake, lands as a SeamResponse.
    resp2 = make_client_once(payload)
    assert isinstance(resp2, SeamResponse)
    assert resp2.json() == payload
    assert resp2.status_code == 200


def make_client_once(payload):
    ac = make_mock_client(lambda r: json_response(payload))
    return ac._request("GET", "https://archive.org/x")


def test_429_retry_after_header_visible_case_insensitively() -> None:
    """③ status 429 + Retry-After readable via .headers.get('retry-after')."""
    ac = make_mock_client(
        lambda r: json_response({"err": "slow down"}, 429, {"Retry-After": "0.01"})
    )
    # At the seam level, the 429 comes back as a readable SeamResponse...
    resp = fetch_raw("GET", "https://archive.org/x")
    assert isinstance(resp, SeamResponse) and resp.status_code == 429
    assert resp.headers.get("retry-after") == "0.01"
    assert resp.headers.get("Retry-After") == "0.01"
    # ...and the ladder exhausts into an urllib HTTPError carrying the headers.
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        ac._request("GET", "https://archive.org/x")
    assert excinfo.value.code == 429
    assert excinfo.value.headers.get("retry-after") == "0.01"


def test_httperror_raise_arm_reachable_via_raise_for_status() -> None:
    """④ handler returns json_response({}, 500) -> raise_for_status raises HTTPError."""
    ac = make_mock_client(lambda r: json_response({}, 500))
    resp = ac._request("GET", "https://archive.org/x")  # 500 is not retried (429-only ladder)
    assert resp.status_code == 500
    assert resp.json() == {}
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        resp.raise_for_status()
    assert excinfo.value.code == 500
    # text_response leg (wayback_fetch reads .text)
    ac2 = make_mock_client(lambda r: text_response("<html>hi</html>", 200))
    assert ac2._request("GET", "https://web.archive.org/x").text == "<html>hi</html>"
    # ArchiveClient is the sync seam-backed client the helper builds.
    assert isinstance(ac2, ArchiveClient)
