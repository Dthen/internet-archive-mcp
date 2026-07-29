"""Tests for the ArchiveClient HTTP layer."""

from __future__ import annotations

import time

import httpx
import pytest

from internet_archive_mcp.client import (
    DEFAULT_USER_AGENT,
    MAX_CACHE_SIZE,
    ArchiveClient,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok_client(**kwargs) -> ArchiveClient:
    """ArchiveClient backed by a MockTransport that always returns 200."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return ArchiveClient(client=http, min_request_interval=0, **kwargs)


# ---------------------------------------------------------------------------
# User-Agent tests
# ---------------------------------------------------------------------------


def test_default_user_agent_contains_marker() -> None:
    assert "internet-archive-mcp" in DEFAULT_USER_AGENT


async def test_default_user_agent_sent() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    ac = ArchiveClient(min_request_interval=0)
    ac._client = httpx.AsyncClient(
        transport=transport, headers={"User-Agent": DEFAULT_USER_AGENT}
    )
    ac._owns_client = True
    await ac._request("GET", "https://archive.org/x")
    assert "internet-archive-mcp" in seen["ua"]
    await ac.aclose()


async def test_custom_user_agent_respected() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    custom = "my-custom-agent/9.9"
    ac = ArchiveClient(user_agent=custom, min_request_interval=0)
    ac._client = httpx.AsyncClient(transport=transport, headers={"User-Agent": custom})
    ac._owns_client = True
    await ac._request("GET", "https://archive.org/x")
    assert seen["ua"] == custom
    await ac.aclose()


# ---------------------------------------------------------------------------
# Cache tests
# ---------------------------------------------------------------------------


def test_cache_miss_returns_none() -> None:
    ac = ArchiveClient(min_request_interval=0)
    assert ac._get_cached("nope", ttl=60) is None


def test_cache_hit_returns_deep_copy() -> None:
    ac = ArchiveClient(min_request_interval=0)
    data = {"items": [1, 2, 3], "nested": {"a": 1}}
    ac._set_cached("k", data)

    first = ac._get_cached("k", ttl=60)
    assert first == data
    # Mutate the returned copy.
    first["items"].append(999)
    first["nested"]["a"] = 42

    # Cache must be unaffected.
    second = ac._get_cached("k", ttl=60)
    assert second == data
    assert second["items"] == [1, 2, 3]
    assert second["nested"]["a"] == 1


def test_expired_cache_entry_returns_none() -> None:
    ac = ArchiveClient(min_request_interval=0)
    ac._set_cached("k", {"v": 1})
    # ttl=0 or negative bypasses / expires immediately.
    assert ac._get_cached("k", ttl=0) is None
    assert ac._get_cached("k", ttl=-1) is None


def test_ttl_zero_bypasses_cache() -> None:
    ac = ArchiveClient(min_request_interval=0)
    ac._set_cached("k", {"v": 1})
    # Even with a fresh entry, ttl=0 must always return None.
    for _ in range(3):
        assert ac._get_cached("k", ttl=0) is None


def test_eviction_at_max_cache_size() -> None:
    ac = ArchiveClient(min_request_interval=0)
    for i in range(MAX_CACHE_SIZE + 10):
        ac._set_cached(f"key-{i}", {"i": i})
    assert len(ac._cache) <= MAX_CACHE_SIZE


def test_clear_cache_empties_everything() -> None:
    ac = ArchiveClient(min_request_interval=0)
    for i in range(5):
        ac._set_cached(f"key-{i}", i)
    assert len(ac._cache) == 5
    ac.clear_cache()
    assert len(ac._cache) == 0
    assert ac._get_cached("key-0", ttl=60) is None


# ---------------------------------------------------------------------------
# Rate limiter tests
# ---------------------------------------------------------------------------


async def test_first_call_is_immediate() -> None:
    ac = ArchiveClient(min_request_interval=0.5)
    start = time.monotonic()
    await ac._wait_for_rate_limit()
    elapsed = time.monotonic() - start
    assert elapsed < 0.1


async def test_second_call_within_interval_is_delayed() -> None:
    ac = ArchiveClient(min_request_interval=0.2)
    await ac._wait_for_rate_limit()  # first, immediate
    start = time.monotonic()
    await ac._wait_for_rate_limit()  # second, must wait ~0.2s
    elapsed = time.monotonic() - start
    assert elapsed >= 0.15


# ---------------------------------------------------------------------------
# Retry tests (httpx.MockTransport)
# ---------------------------------------------------------------------------


async def test_success_on_first_try_no_retry() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    ac = ArchiveClient(client=http, min_request_interval=0, backoff_base=0.01)

    resp = await ac._request("GET", "https://archive.org/x")
    assert resp.status_code == 200
    assert call_count == 1


async def test_429_with_retry_after_succeeds_on_second_attempt() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, headers={"Retry-After": "0.01"})
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    ac = ArchiveClient(client=http, min_request_interval=0, backoff_base=0.01)

    resp = await ac._request("GET", "https://archive.org/x")
    assert resp.status_code == 200
    assert call_count == 2


async def test_429_without_retry_after_uses_exponential_backoff() -> None:
    call_count = 0
    timestamps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        timestamps.append(time.monotonic())
        if call_count <= 2:
            return httpx.Response(429)  # no Retry-After
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    ac = ArchiveClient(client=http, min_request_interval=0, backoff_base=0.05)

    resp = await ac._request("GET", "https://archive.org/x")
    assert resp.status_code == 200
    assert call_count == 3
    # First backoff ~0.05 (0.05*2**0), second ~0.10 (0.05*2**1).
    gap1 = timestamps[1] - timestamps[0]
    gap2 = timestamps[2] - timestamps[1]
    assert gap1 >= 0.04
    assert gap2 >= 0.08
    assert gap2 > gap1  # exponential growth


async def test_429_every_attempt_raises_after_max_retries() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(429, headers={"Retry-After": "0.01"})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    max_retries = 3
    ac = ArchiveClient(
        client=http,
        min_request_interval=0,
        max_retries=max_retries,
        backoff_base=0.01,
    )

    with pytest.raises(httpx.HTTPStatusError):
        await ac._request("GET", "https://archive.org/x")
    assert call_count == max_retries + 1


async def test_non_429_error_not_retried() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(500, json={"error": "boom"})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    ac = ArchiveClient(client=http, min_request_interval=0, backoff_base=0.01)

    resp = await ac._request("GET", "https://archive.org/x")
    assert resp.status_code == 500
    assert call_count == 1  # no retry on 500


# ---------------------------------------------------------------------------
# Context manager tests
# ---------------------------------------------------------------------------


async def test_aenter_returns_self() -> None:
    ac = _ok_client()
    async with ac as entered:
        assert entered is ac


async def test_aexit_closes_owned_client() -> None:
    ac = ArchiveClient(min_request_interval=0)  # owns its client
    assert ac._owns_client is True
    inner = ac._client
    async with ac:
        pass
    assert inner.is_closed


async def test_external_client_not_closed_on_aexit() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"ok": True})
    )
    external = httpx.AsyncClient(transport=transport)
    ac = ArchiveClient(client=external, min_request_interval=0)
    assert ac._owns_client is False
    async with ac:
        pass
    assert not external.is_closed
    await external.aclose()  # cleanup
