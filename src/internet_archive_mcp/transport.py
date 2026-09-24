"""Raw urllib transport seam for the httpx->urllib port (stdlib only).

This module is the ONE network-touching surface in the package: every caller
goes through :func:`fetch_raw`, and :data:`_urlopen` is the single
monkeypatch site for test fakes (T01-T21). It owns fetch + exception
normalization ONLY -- the 429 retry ladder lives in the client (T03), exactly
like legacy.

Legacy grounding (tag pre-migration/20260914,
``git show pre-migration/20260914:src/internet_archive_mcp/client.py``):
- client.py l.130-152: the ONLY retried status is 429 (Retry-After honored,
  else exponential backoff); NO transport errors are ever retried -- timeout/
  connect/protocol errors propagate un-caught to the server handlers'
  ``except httpx.HTTPError`` fold (server.py l.64-67:
  ``return f"Error: API request failed — {e}"``). The URLError normalization
  below keeps converted errors landing in that SAME fold (R1: py3.11
  ``socket.timeout is TimeoutError`` is NOT a URLError subclass, so a naive
  port leaks to -32603/crash instead of the friendly text).
- client.py l.55-59: ``httpx.AsyncClient(timeout=30.0,
  follow_redirects=True)`` -- 30.0 is EXPLICIT (client.py l.58), not the httpx
  default 5.0; the seam carries the same default.
- client.py l.28: UA literal carried byte-stable (wire identity; version
  coherence at T19 does NOT touch it).
"""

from __future__ import annotations

import email.message
import http.client
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

# Default User-Agent (IA requires a descriptive one on all automated
# requests). Byte-stable copy of tag client.py:28 -- wire identity.
DEFAULT_USER_AGENT = (
    "internet-archive-mcp/0.1.0 (https://github.com/dthen/internet-archive-mcp)"
)

# The ONE monkeypatch site for every test fake from here to T21: nothing else
# in the package may call urllib.request.urlopen directly.
_urlopen = urllib.request.urlopen


class _CaseInsensitiveHeaders:
    """Dict- or Message-backed header bag with case-insensitive ``.get``.

    The client ladder reads ``response.headers.get("Retry-After")`` (tag
    client.py l.143) -- wrapping keeps that working whether the underlying
    source is ``email.message.Message`` (urlopen/HTTPError) or a plain dict
    (test fakes).
    """

    def __init__(self, source=None):
        self._store: dict[str, str] = {}
        if source is None:
            return
        if hasattr(source, "items"):  # email.message.Message also has .items()
            pairs = list(source.items())
        else:
            pairs = list(source)
        for name, value in pairs:
            self._store[str(name).lower()] = value

    def get(self, name, default=None):
        return self._store.get(str(name).lower(), default)

    def __contains__(self, name):
        return str(name).lower() in self._store


class _SeamRequest:
    """Minimal ``response.request`` stand-in exposing ``.url``.

    httpx messages built from ``response.request`` (the ladder's
    HTTPStatusError, tag client.py l.139-142) keep working: legacy only ever
    reads the URL off it.
    """

    __slots__ = ("url",)

    def __init__(self, url: str):
        self.url = url


class SeamResponse:
    """httpx.Response-parity surface for the seam (httpx 0.28.1 subset)."""

    def __init__(self, status_code: int, headers, text: str, url: str):
        self.status_code = int(status_code)
        self.headers = (
            headers if isinstance(headers, _CaseInsensitiveHeaders)
            else _CaseInsensitiveHeaders(headers)
        )
        # utf-8 decode with errors="replace". httpx .text honors the
        # charset header; this fleet's endpoints all answer UTF-8
        # JSON/text, so "good enough" is the standing rule -- if a T02
        # fixture disagrees, fix the decode HERE, not in callers.
        self.text = text
        self.request = _SeamRequest(url)

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        """Parity role of httpx.HTTPStatusError (subset of httpx.HTTPError,
        verified) -- handlers fold it into the friendly text. urllib.error.
        HTTPError is a subclass of OSError but is raised here, never
        swallowed: the ladder/tests catch it explicitly."""
        if 400 <= self.status_code < 600:
            msg = email.message.Message()
            for name, value in self.headers._store.items():
                msg[name] = value
            raise urllib.error.HTTPError(
                self.request.url, self.status_code, self.text[:200],
                msg, None,
            )

    @classmethod
    def from_urlopen(cls, resp, url: str) -> "SeamResponse":
        """Build from a live urlopen success object (http.client.HTTPResponse
        or a test fake shaped like one)."""
        body = resp.read()
        if isinstance(body, str):  # fakes may hand text directly
            text = body
        else:
            text = body.decode("utf-8", errors="replace")
        status = getattr(resp, "status", None) or getattr(resp, "code", 200)
        geturl = getattr(resp, "geturl", None)
        return cls(
            status,
            getattr(resp, "headers", None),
            text,
            str(geturl()) if callable(geturl) else url,
        )

    @classmethod
    def from_http_error(cls, err: urllib.error.HTTPError) -> "SeamResponse":
        """urlopen raises HTTPError on 4xx/5xx; HTTPError IS a readable
        response (tag-parity: httpx returns the 429 as a Response for the
        ladder -- status control flow must stay in the client, not raise)."""
        fp = getattr(err, "fp", None)
        if fp is not None:
            try:
                body = err.read()
            except (OSError, http.client.HTTPException):
                # Closed/unreadable/truncated fp -> reason fallback below.
                body = b""
            text = (
                body if isinstance(body, str)
                else body.decode("utf-8", errors="replace")
            )
        else:
            text = ""
        if not text:
            # raise_for_status()/synthetic errors carry the body in .reason
            # (no fp) — keep it readable for the friendly fold.
            text = str(getattr(err, "reason", "") or "")
        return cls(err.code, getattr(err, "headers", None), text, err.url)


def encode_params(params: dict | list[tuple] | None) -> str:
    """URL-encode the SAME param shapes the legacy client passes (list of
    tuples for repeated ``fl[]``/``sort[]``/``filter`` keys, dicts, ints
    stringified).

    VERIFIED LIVE (chain _chain.md + T01 probe): httpx 0.28.1 and
    ``urllib.parse.urlencode`` produce byte-identical wire form for every
    legacy param shape (space->``+``, ``:``->``%3A``, ``(``->``%28``,
    ``'``->``%27``, ``!``->``%21``, ``[``/``]``->``%5B``/``%5D``, insertion
    order preserved, repeated bare keys). No custom encoder. T02's
    differential fixture is the standing proof; if it ever disagrees, that is
    a seam bug -- fix HERE, not in callers.
    """
    if not params:
        return ""
    pairs = params.items() if isinstance(params, dict) else params
    return urllib.parse.urlencode(list(pairs))


def fetch_raw(
    method: str,
    url: str,
    *,
    params: dict | list[tuple] | None = None,
    headers: dict | None = None,
    timeout: float = 30.0,
) -> SeamResponse:
    """The ONLY network-touching function in the package (grep gate).

    GET and POST: POST sends an EMPTY body (legacy ``save_page`` posts with no
    body -- tag client.py l.655-657); method mix and byte-shape preserved at
    the client layer. ``url`` passes through UNTOUCHED -- callers build full
    literal URLs (``{IA_BASE}/metadata/{quote(...)}`` etc.), so there is no
    base-merge trap and no urljoin here. ``timeout`` default mirrors tag
    client.py:58 (``timeout=30.0``, EXPLICIT -- not the httpx default 5.0).

    follow_redirects=True parity (tag client.py:57): urllib's default
    HTTPRedirectHandler follows GET/HEAD redirects (301/302/303/307/308),
    which legacy relied on for ``/wayback/available`` and download paths.
    POST-redirect method-transition parity: checked at implementation time
    against CPython's HTTPRedirectHandler.redirect_request -- POST +
    301/302/303 follows with the method transitioning to GET (body dropped);
    POST + 307/308 raises HTTPError instead of redirecting. httpx's
    browser-convention redirect does the same POST->GET transition on
    301/302/303. SPN2 POSTs are not observed to redirect in fixtures, so the
    307/308 gap is accepted (unverified-vs-fixture note carried per chain).
    """
    query = encode_params(params)
    full_url = url + ("?" + query if query else "")

    merged: dict[str, str] = {"User-Agent": DEFAULT_USER_AGENT}
    if headers:
        # Caller-supplied wins, byte-for-byte, over the UA default; fold the
        # merge case-insensitively so a differently-cased UA can't duplicate.
        lower = {k.lower(): k for k in merged}
        for key, value in headers.items():
            dupe = lower.pop(str(key).lower(), None)
            if dupe is not None:
                del merged[dupe]
            merged[key] = value
            lower[str(key).lower()] = key

    # data=b"" (not None) forces the POST line with an empty body; urllib
    # then sends Content-Length: 0, matching httpx's bodyless POST.
    body = b"" if method.upper() == "POST" else None
    request = urllib.request.Request(full_url, data=body, method=method.upper())
    for key, value in merged.items():
        request.add_header(key, value)

    try:
        resp = _urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as e:
        # ARM ORDER = BUG GATE: HTTPError is a subclass of OSError, so this
        # arm MUST precede the normalization arm or 4xx/5xx (incl. the
        # ladder's 429 input, tag client.py l.130-152) would be swallowed
        # into URLError and never reach the status control flow. urlopen
        # raises it on 4xx/5xx; HTTPError IS a readable response -- return it
        # as a SeamResponse so status handling stays in the client ladder
        # exactly like legacy.
        return SeamResponse.from_http_error(e)
    except (TimeoutError, socket.timeout, http.client.HTTPException,
            ConnectionResetError, OSError) as e:
        # R1 normalization tuple (mirrors REFERENCE §5): py3.11
        # ``socket.timeout is TimeoutError ⊂ OSError`` (verified), so the
        # names are redundant-but-documenting -- KEEP all of them. Legacy
        # client.py l.130-152 retried ONLY 429 and let these propagate
        # un-caught to the handlers' ``except httpx.HTTPError`` fold
        # (server.py l.64-67); URLError (⊃ the fold's catch) keeps them in
        # the SAME fold.
        # intentional R2 deviation with friendly-text parity — legacy ProtocolError-class siblings
        # were never retried (429-only, tag-verified); converted errors route through the same
        # single-attempt fold.
        raise urllib.error.URLError(e) from e

    # Reading the body is part of the network seam too. Keep it inside the same
    # normalization boundary as urlopen so a mid-stream reset cannot leak raw
    # ConnectionResetError past the handlers' friendly transport fold.
    try:
        return SeamResponse.from_urlopen(resp, full_url)
    except (TimeoutError, socket.timeout, http.client.HTTPException,
            ConnectionResetError, OSError) as e:
        raise urllib.error.URLError(e) from e
