"""Hermetic tests for the raw urllib seam (T01).

Zero real sockets: every fake replaces ``transport._urlopen`` (the ONE
monkeypatch site). Fakes never sleep.
"""

import email.message
import http.client
import io
import json
import urllib.error
import urllib.request

import pytest

from internet_archive_mcp import transport
from internet_archive_mcp.transport import (
    DEFAULT_USER_AGENT,
    SeamResponse,
    encode_params,
    fetch_raw,
)

# Recorded literal from the live httpx 0.28.1 probe (chain _chain.md
# "Query-encoder parity", re-run at T01): the byte-identical target for
# encode_params on the legacy tuple-list shape.
HTTPX_RAW_QUERY = (
    "q=creator%3Aellis+AND+%28title%3Ajazz+piano%29%27s%21&output=json"
    "&rows=20&fl%5B%5D=identifier&fl%5B%5D=title&sort=downloads+desc"
    "&filter=mediatype%3Atexts&filter=format%3Aepub&page=1"
)

PROBE_PARAMS = [
    ("q", "creator:ellis AND (title:jazz piano)'s!"),
    ("output", "json"),
    ("rows", 20),
    ("fl[]", "identifier"),
    ("fl[]", "title"),
    ("sort", "downloads desc"),
    ("filter", "mediatype:texts"),
    ("filter", "format:epub"),
    ("page", 1),
]


class FakeResponse:
    """Duck-types http.client.HTTPResponse as returned by urlopen."""

    def __init__(self, body=b"{}", status=200, headers=None, url="http://fake/"):
        self._body = body
        self.status = status
        self.code = status
        self._url = url
        msg = email.message.Message()
        for k, v in (headers or {}).items():
            msg[k] = v
        self.headers = msg

    def read(self):
        return self._body

    def geturl(self):
        return self._url


def install(monkeypatch, side_effect=None, response=None):
    """Replace transport._urlopen; record the (request, timeout) it saw."""
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append((req, timeout))
        if side_effect is not None:
            raise side_effect
        return response

    monkeypatch.setattr(transport, "_urlopen", fake_urlopen)
    return calls


# -- R1/R2 normalization pins ----------------------------------------------


def test_timeout_maps_to_urlerror(monkeypatch):
    """R1 pin (REQUIRED): py3.11 socket.timeout is TimeoutError, NOT a
    URLError subclass — the seam must convert it or it leaks past the
    handlers' httpx.HTTPError-parity fold to -32603."""
    install(monkeypatch, side_effect=TimeoutError("read timed out"))
    with pytest.raises(urllib.error.URLError) as excinfo:
        fetch_raw("GET", "https://archive.org/x")
    assert isinstance(excinfo.value.reason, TimeoutError)
    assert excinfo.value.__cause__ is not None  # raise ... from e


def test_socket_timeout_is_pinned_same_object():
    """R1 grounding: the tuple names socket.timeout because py3.11 aliases it
    to TimeoutError (verified here rather than asserted in prose)."""
    import socket

    assert socket.timeout is TimeoutError


def test_remote_disconnected_maps_to_urlerror(monkeypatch):
    """R2: http.client.HTTPException-class sibling -> URLError."""
    install(monkeypatch, side_effect=http.client.RemoteDisconnected("closed"))
    with pytest.raises(urllib.error.URLError) as excinfo:
        fetch_raw("GET", "https://archive.org/x")
    assert isinstance(excinfo.value.reason, http.client.HTTPException)
    assert excinfo.value.__cause__ is not None


def test_connection_reset_maps_to_urlerror(monkeypatch):
    """R2: ConnectionResetError -> URLError."""
    install(monkeypatch, side_effect=ConnectionResetError(104, "reset by peer"))
    with pytest.raises(urllib.error.URLError) as excinfo:
        fetch_raw("GET", "https://archive.org/x")
    assert isinstance(excinfo.value.reason, ConnectionResetError)
    assert excinfo.value.__cause__ is not None


def test_oserror_maps_to_urlerror_with_chain(monkeypatch):
    """Broad OSError arm; `from e` chain preserved."""
    boom = OSError("network down")
    install(monkeypatch, side_effect=boom)
    with pytest.raises(urllib.error.URLError) as excinfo:
        fetch_raw("GET", "https://archive.org/x")
    assert excinfo.value.__cause__ is boom
    assert excinfo.value.reason is boom


def test_http_error_body_incomplete_read_falls_back_to_reason(monkeypatch):
    """An HTTPError body truncated by the peer still becomes a response."""
    class IncompleteBody:
        def read(self):
            raise http.client.IncompleteRead(b"partial", 7)

        def close(self):
            pass

    err = urllib.error.HTTPError(
        "https://archive.org/x", 503, "Service Unavailable", None, IncompleteBody()
    )
    install(monkeypatch, side_effect=err)

    response = fetch_raw("GET", "https://archive.org/x")

    assert isinstance(response, SeamResponse)
    assert response.status_code == 503
    assert response.text == "Service Unavailable"


def test_http_error_429_becomes_response_not_urlerror(monkeypatch):
    """HTTPError arm ordered BEFORE the OSError arm (HTTPError ⊂ OSError —
    ordering is the bug gate): the ladder's 429 input must arrive as a
    readable SeamResponse with Retry-After case-insensitively accessible
    (legacy read: tag client.py l.143 headers.get("Retry-After"))."""
    hdrs = email.message.Message()
    hdrs["Retry-After"] = "17"
    err = urllib.error.HTTPError(
        "https://web.archive.org/save/x", 429, "Too Many Requests",
        hdrs, io.BytesIO(b'{"err":"rate limited"}'),
    )
    install(monkeypatch, side_effect=err)
    resp = fetch_raw("POST", "https://web.archive.org/save/x")
    assert isinstance(resp, SeamResponse)
    assert resp.status_code == 429
    assert resp.headers.get("Retry-After") == "17"
    assert resp.headers.get("retry-after") == "17"  # case-insensitive
    assert resp.json() == {"err": "rate limited"}
    assert resp.request.url == "https://web.archive.org/save/x"


# -- 200 success path --------------------------------------------------------


def test_response_body_read_failure_maps_to_urlerror(monkeypatch):
    """A transport failure while reading a successful response is normalized too."""
    class ReadFailureResponse:
        status = 200
        code = 200
        headers = email.message.Message()

        def read(self):
            raise ConnectionResetError("body reset")

        def geturl(self):
            return "https://archive.org/x"

    install(monkeypatch, response=ReadFailureResponse())
    with pytest.raises(urllib.error.URLError) as excinfo:
        fetch_raw("GET", "https://archive.org/x")
    assert isinstance(excinfo.value.reason, ConnectionResetError)
    assert excinfo.value.__cause__ is not None


def test_success_json_text_and_url(monkeypatch):
    payload = {"response": {"docs": [{"identifier": "foo"}]}}
    fake = FakeResponse(json.dumps(payload).encode(), status=200,
                        url="https://archive.org/advancedsearch.php?output=json")
    calls = install(monkeypatch, response=fake)
    resp = fetch_raw("GET", "https://archive.org/advancedsearch.php",
                     params={"output": "json"})
    assert resp.status_code == 200
    assert resp.json() == payload
    assert resp.text == json.dumps(payload)
    assert resp.request.url == fake.geturl()  # geturl echo survives redirect
    req, timeout = calls[0][0], calls[0][1]
    assert req.full_url == "https://archive.org/advancedsearch.php?output=json"
    assert req.get_method() == "GET"
    assert timeout == 30.0  # tag client.py:58 EXPLICIT default, not httpx 5.0


def test_default_ua_and_caller_header_merge(monkeypatch):
    calls = install(monkeypatch, response=FakeResponse(b"{}"))
    fetch_raw("POST", "https://web.archive.org/save/x",
              headers={"Accept": "application/json"})
    req = calls[0][0]
    assert req.get_header("User-agent") == (
        "internet-archive-mcp/0.1.0 (https://github.com/dthen/internet-archive-mcp)"
    )
    assert DEFAULT_USER_AGENT == (
        "internet-archive-mcp/0.1.0 (https://github.com/dthen/internet-archive-mcp)"
    )
    assert req.get_header("Accept") == "application/json"
    assert req.data == b""  # POST: empty body, tag client.py l.655-657
    assert req.get_method() == "POST"


def test_get_sends_no_body(monkeypatch):
    calls = install(monkeypatch, response=FakeResponse(b"{}"))
    fetch_raw("GET", "https://archive.org/metadata/foo")
    assert calls[0][0].data is None


# -- raise_for_status parity --------------------------------------------------


def test_raise_for_status_on_4xx():
    resp = SeamResponse(500, {"x": "1"}, "boom", "https://archive.org/x")
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        resp.raise_for_status()
    assert excinfo.value.code == 500
    # folds into the friendly handler text: URLError ⊃ HTTPError ⊃ OSError,
    # and str(excinfo) is what "Error: API request failed — {e}" renders.
    assert isinstance(excinfo.value, urllib.error.URLError)


def test_raise_for_status_passes_on_2xx_3xx():
    SeamResponse(200, None, "ok", "https://archive.org/x").raise_for_status()
    SeamResponse(302, None, "", "https://archive.org/x").raise_for_status()


# -- encode_params byte-check --------------------------------------------------


def test_encode_params_byte_identical_to_httpx_literal():
    """Live probe pair from _chain.md: urlencode(tuple-list) == httpx 0.28.1
    raw_path query (recorded literal above)."""
    assert encode_params(PROBE_PARAMS) == HTTPX_RAW_QUERY
    # dict shape parity on the same keys/order
    assert encode_params({"q": "creator:ellis AND (title:jazz piano)'s!"}) == (
        "q=creator%3Aellis+AND+%28title%3Ajazz+piano%29%27s%21")
    assert encode_params({"q": "creator:ellis AND (title:jazz piano)'s!",
                          "output": "json", "rows": 20, "page": 1}) == (
        "q=creator%3Aellis+AND+%28title%3Ajazz+piano%29%27s%21"
        "&output=json&rows=20&page=1")
    assert encode_params(None) == ""
    assert encode_params({}) == ""
    assert encode_params([]) == ""


def test_no_params_no_question_mark(monkeypatch):
    calls = install(monkeypatch, response=FakeResponse(b"{}"))
    fetch_raw("GET", "https://archive.org/metadata/foo")
    assert calls[0][0].full_url == "https://archive.org/metadata/foo"
