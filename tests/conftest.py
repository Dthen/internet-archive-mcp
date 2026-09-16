"""Shared pytest harness for the urllib-seam port (T08).

Why this shape: minimize family-port diffs. Legacy tests drove the client
through the legacy mock transport plus a ``client=`` kwarg that T03 deleted;
this rebuilds the SAME helper names (``make_mock_client``,
``json_response``) on the ONE monkeypatch site -- ``transport._urlopen`` --
so T09-T17 ports stay mechanical (change the harness ONCE, not 200 times).

A handler ``handler(request) -> SeamResponse | HTTPError | tuple`` mirrors
legacy ``handler(request) -> legacy-transport Response``. It receives a
:class:`FakeRequest` whose surface covers everything the ported handlers
introspect: ``str(request.url)`` byte-for-byte the full wire URL (FakeURL
subclasses ``str``), ``request.url.params.get/get_list/multi_items``
(parse_qsl, query order preserved), case-insensitive
``request.headers.get(...)``, ``request.method``. Answers are normalized to
a REAL ``transport.SeamResponse`` (reused, not duplicated) so client code
paths (``.json()``, ``.text``, ``.status_code``,
``.headers.get("retry-after")``, ``.raise_for_status()``, the 429 ladder)
run unmodified. Canned status >= 400 goes through the same raise/convert
path a live urlopen uses (HTTPError with readable fp -> the seam's
HTTPError arm rebuilds the SeamResponse), so status control flow stays in
the client exactly like production.

Installation is flag-free: ``make_mock_client`` swaps ``transport._urlopen``
at call time and an autouse fixture restores the pristine real-urllib value
at teardown. stdlib + pytest + package imports only; no HTTP client library
anywhere (grep gate).
"""

from __future__ import annotations

import email.message
import io
import json
import urllib.error
import urllib.parse

import pytest

from internet_archive_mcp import transport
from internet_archive_mcp.client import ArchiveClient


class FakeParams:
    """Legacy-URL ``.params`` shim over the query string.

    ``parse_qsl(..., keep_blank_values=True)`` keeps repeated keys and the
    original query ORDER, so ``multi_items()`` replays pairs in query order
    (legacy dict-order assertions depend on it) and ``get("q")`` /
    ``get_list("fl[]")`` behave like the legacy handlers expect.
    """

    def __init__(self, query: str):
        self._pairs = list(urllib.parse.parse_qsl(query, keep_blank_values=True))

    def get(self, key, default=None):
        for name, value in self._pairs:
            if name == key:
                return value
        return default

    def get_list(self, key):
        return [value for name, value in self._pairs if name == key]

    def multi_items(self):
        return list(self._pairs)


class FakeURL(str):
    """Full wire URL that is ALSO a legacy-URL-like shim.

    Subclasses ``str`` so ``str(request.url)`` AND ``request.url == "..."``
    reproduce the full URL (scheme://host + path + ``?query``) byte-for-byte
    exactly as the legacy transport rendered it, while ``.params`` / ``.path`` / ``.host`` /
    ``.scheme`` / ``.query`` keep structured access working for ported
    handlers.
    """

    __slots__ = ("params", "path", "host", "scheme", "query")

    def __new__(cls, full_url: str):
        self = super().__new__(cls, full_url)
        split = urllib.parse.urlsplit(full_url)
        self.params = FakeParams(split.query)
        self.path = split.path
        self.host = split.netloc
        self.scheme = split.scheme
        self.query = split.query
        return self


class FakeRequest:
    """the legacy Request-shaped view of the urllib Request the seam built.

    ``.method`` / ``.url`` (:class:`FakeURL`: str + ``.params``) /
    ``.url_parsed`` (same shim, explicit legacy-style handle) / ``.headers``
    (case-insensitive ``.get`` -- urllib capitalizes header keys, legacy
    handlers read them lowercase) / ``.content`` (body bytes or None) /
    ``.timeout`` (as the seam received it).
    """

    def __init__(self, urllib_request, timeout=None):
        self.method = urllib_request.get_method()
        self.url = FakeURL(urllib_request.full_url)
        self.url_parsed = self.url
        self.content = urllib_request.data
        self.timeout = timeout
        self.headers = transport._CaseInsensitiveHeaders(
            list(urllib_request.headers.items())
        )


class _FakeHTTPResponse:
    """Duck-types ``http.client.HTTPResponse`` the way urlopen hands one back.

    Kept minimal ON PURPOSE: ``fetch_raw`` runs its real
    ``SeamResponse.from_urlopen`` over this object, so the canned answer is
    converted by production code (status/.headers/.geturl()/.read() are the
    only surface that function touches) and the client receives a genuine
    ``transport.SeamResponse``.
    """

    def __init__(self, status: int, headers, body: bytes, url: str):
        self.status = self.code = int(status)
        self.headers = headers if hasattr(headers, "items") else _message(headers)
        self._body = body
        self._url = url

    def read(self):
        return self._body

    def geturl(self):
        return self._url


def _message(headers=None):
    msg = email.message.Message()
    for name, value in (headers or {}).items():
        msg[name] = value
    return msg


class FakeTransport:
    """Seam-shaped fake urlopen: records every call, answers the handler.

    Signature matches ``urllib.request.urlopen(request, timeout=...)``.
    Each call is recorded as a :class:`FakeRequest` (see ``.requests`` --
    legacy tests count calls via handler closure or this list). The answer
    is the handler's return (``handler(fake)``) or, when constructed with a
    ``responses`` FIFO, its next canned entry -- "pops the next canned
    response". Canned shapes: ``(status, headers, body_bytes)`` tuple (as
    built by :func:`json_response` / :func:`text_response`), a
    ``transport.SeamResponse``, plain text, or an http.client-like object
    (returned untouched). ``urllib.error.HTTPError`` raised by the handler
    passes through. Canned status >= 400 is raised AS an HTTPError with a
    readable fp so the real seam conversion + ``raise_for_status()`` arms
    stay exercised.
    """

    def __init__(self, handler=None, responses=None):
        self.handler = handler
        self.responses = list(responses) if responses is not None else None
        self.requests: list[FakeRequest] = []

    def __call__(self, request, timeout=None):
        fake = FakeRequest(request, timeout)
        self.requests.append(fake)
        if self.responses is not None:
            if not self.responses:
                raise AssertionError("conftest fake: canned responses exhausted")
            result = self.responses.pop(0)
        else:
            if self.handler is None:
                raise AssertionError("conftest fake: no handler or responses configured")
            result = self.handler(fake)
        if isinstance(result, urllib.error.HTTPError):
            raise result
        if hasattr(result, "read") and not isinstance(result, (bytes, str, tuple)):
            return result  # already http.client-shaped (test_transport's FakeResponse style)
        status, headers, body = self._normalize(result)
        url = str(fake.url)
        if status >= 400:
            # Live-urlopen parity: raise HTTPError with readable fp; the seam's
            # HTTPError arm rebuilds it into a SeamResponse (from_http_error).
            raise urllib.error.HTTPError(url, status, "Bad Response", _message(headers), io.BytesIO(body))
        return _FakeHTTPResponse(status, headers, body, url)

    @staticmethod
    def _normalize(result):
        """Coerce handler output to (status, headers, body_bytes)."""
        if isinstance(result, tuple) and len(result) == 3:
            status, headers, body = result
            body = body if isinstance(body, bytes) else str(body).encode("utf-8")
            return int(status), headers, body
        if isinstance(result, transport.SeamResponse):
            return (
                result.status_code,
                dict(result.headers._store),
                result.text.encode("utf-8"),
            )
        if isinstance(result, (str, bytes)):
            body = result if isinstance(result, bytes) else result.encode("utf-8")
            return 200, {}, body
        raise TypeError(f"conftest fake: unsupported handler result {type(result)!r}")


# The pristine seam value, captured at conftest import (before ANY test has
# run, so this is the real urllib.request.urlopen). make_mock_client installs
# fakes only during test bodies; the autouse fixture below restores this
# single known-good value at teardown -- no install flags, no bookkeeping.
_PRISTINE_URLOPEN = transport._urlopen


@pytest.fixture(autouse=True)
def _restore_seam_fake():
    """Restore ``transport._urlopen`` after every test.

    Runs its teardown LAST among function-scoped fixtures (autouse sets up
    first), so it also back-stops the ``monkeypatch`` fixture's own undo in
    suites that patch the seam directly (test_transport style).
    """
    yield
    if transport._urlopen is not _PRISTINE_URLOPEN:
        transport._urlopen = _PRISTINE_URLOPEN


def make_mock_client(handler=None, responses=None, **kwargs):
    """ArchiveClient whose every request flows through a FakeTransport seam fake.

    Flag-free install: swaps ``transport._urlopen`` (the ONE patch site) at
    call time; the autouse fixture above restores it. Returns the client --
    legacy ``ac = make_mock_client(handler)`` call sites transfer unchanged
    (legacy tests introspected via handler closures). Defaults mirror the
    legacy helper -- zero rate-limit wait and backoff_base=0.01 (no sleeps
    beyond that scale) -- and caller ``**kwargs`` (``user_agent=``,
    ``max_retries=``, ...) win.
    """
    transport._urlopen = FakeTransport(handler=handler, responses=responses)
    client_kwargs = {"min_request_interval": 0, "backoff_base": 0.01}
    client_kwargs.update(kwargs)
    return ArchiveClient(**client_kwargs)


def json_response(data, status_code=200, headers=None):
    """Canned (status, headers, body=json bytes) tuple -- legacy helper name."""
    merged = {"Content-Type": "application/json"}
    if headers:
        merged.update(headers)
    return (int(status_code), merged, json.dumps(data).encode("utf-8"))


def text_response(text, status_code=200, headers=None):
    """Canned text tuple (wayback_fetch legs read ``.text``)."""
    merged = dict(headers or {})
    return (int(status_code), merged, text.encode("utf-8"))
