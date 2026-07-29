"""Shared fixtures for internet-archive-mcp tests."""

from __future__ import annotations

import httpx
import pytest

from internet_archive_mcp.client import ArchiveClient


def make_mock_client(handler):
    """Create an ArchiveClient backed by a MockTransport handler."""
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    return ArchiveClient(client=http_client, min_request_interval=0, backoff_base=0.01)


def json_response(data, status_code=200):
    """Build a mock httpx.Response with JSON body."""
    return httpx.Response(
        status_code=status_code,
        json=data,
        headers={"Content-Type": "application/json"},
    )
