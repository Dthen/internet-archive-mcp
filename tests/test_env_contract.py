"""T18 — Env-var contract: IA_ACCESS_KEY/IA_SECRET_KEY read at call time.

Verifies that the save_page handler reads os.environ at CALL time, not import
time. An import-time capture would freeze an empty environment — a real
behavioral regression risk (hermes spawn may set vars after import ordering
differences).

Zero real-network auth: FAKEAK/FAKESK are fake literals — no vault, no real
creds, no network.
"""

import json
import os
import select
import subprocess
import sys
from pathlib import Path

import pytest

import internet_archive_mcp.server as server_module
import internet_archive_mcp.transport as transport
from conftest import make_mock_client, json_response

# Fake credentials — literals only, no vault, no real creds, no network.
FAKE_AK = "FAKEAK"
FAKE_SK = "FAKESK"

# Guidance message from client.py:702-706 (byte-exact).
GUIDANCE_MSG = (
    "Error: Save Page Now requires authentication. "
    "Provide access_key and secret_key from "
    "https://archive.org/account/s3.php"
)

# Production spawn constants (PROD_PY is the same pin as test_stateless_era.py).
#
# PROD_PY is byte-exact the live Hermes config command for this server
# (mcp_servers.internet-archive):
#     /mnt/.../mcp-venvs/internet-archive-mcp-v2/bin/python3
#         -m internet_archive_mcp.server
# The unsuffixed sibling (…/mcp-venvs/internet-archive-mcp/) is the PRE-MIGRATION
# fat venv, kept on purpose as a rollback artifact: it installs httpx, mcp,
# pydantic and fastmcp and carries package version 0.1.0. Spawning it under a
# name like PROD_PY makes this file assert nothing about the production
# interpreter, so the pin stays on -v2 and
# TestProductionVenvIsolation below keeps it honest.
PROD_PY = "/mnt/HC_Volume_105667182/kimbo/mcp-venvs/internet-archive-mcp-v2/bin/python3"
SERVER = "internet_archive_mcp.server"
READ_TIMEOUT = 5

# Third-party runtime deps the production venv must never be able to import.
# The migrated package is stdlib-only (pyproject: dependencies = []), so any of
# these resolving under PROD_PY is a regression — either a package installed
# into the venv, or a source-level import smuggled back in.
FORBIDDEN_RUNTIME_MODULES = ("fastmcp", "httpx", "mcp", "pydantic", "requests")

# Stdlib-only probe, written to a tmp file and spawned by PROD_PY. Two distinct
# questions, deliberately asked separately:
#   "findable" — importlib can LOCATE the module in this venv (catches a
#                package installed into it, before any of our code runs);
#   "resident" — the module is LOADED once the server is imported (catches a
#                source-level `import httpx` reappearing in the package).
# Both answer empty for a correctly isolated production venv.
_ISOLATION_PROBE = '''\
import importlib.util
import json
import sys

forbidden = {forbidden!r}

findable = [name for name in forbidden if importlib.util.find_spec(name) is not None]

import internet_archive_mcp.server  # noqa: F401  -- the code under test

resident = sorted(name for name in forbidden if name in sys.modules)

print(json.dumps({{
    "executable": sys.executable,
    "findable": sorted(findable),
    "resident": resident,
}}))
'''


def _read_line_with_timeout(f, sec=READ_TIMEOUT):
    """One line from a pipe, or None if nothing arrives within `sec`."""
    ready, _, _ = select.select([f], [], [], sec)
    if not ready:
        return None
    return f.readline()


def _recorder_handler(record):
    """Build a FakeTransport handler that records the request and returns success."""
    def handler(request):
        record.append({
            "method": request.method,
            "url": str(request.url),
            "auth": request.headers.get("authorization", ""),
            "accept": request.headers.get("accept", ""),
        })
        return json_response({"url": "https://example.com", "job_id": "fake-job-123"})
    return handler


class TestEnvContract:
    """IA_ACCESS_KEY/IA_SECRET_KEY are read at call time, not import time."""

    def test_save_page_reads_env_at_call_time(self, monkeypatch):
        """Env flip between two calls in the SAME imported module proves call-time lookup.

        An import-time capture would still show the guidance error on the second call.
        """
        record = []
        monkeypatch.setattr(
            server_module, "_client",
            make_mock_client(_recorder_handler(record))
        )
        # Start WITHOUT IA_* env vars
        monkeypatch.delenv("IA_ACCESS_KEY", raising=False)
        monkeypatch.delenv("IA_SECRET_KEY", raising=False)

        # First call: no creds → error guidance text
        result1 = server_module.handle_call("save_page", {"url": "https://example.com"})
        assert isinstance(result1, str)
        assert "Error:" in result1
        assert "s3.php" in result1
        assert record == []  # no HTTP request made

        # Flip env: set fake creds
        monkeypatch.setenv("IA_ACCESS_KEY", FAKE_AK)
        monkeypatch.setenv("IA_SECRET_KEY", FAKE_SK)

        # Second call: env creds → POST with auth header, NO error
        result2 = server_module.handle_call("save_page", {"url": "https://example.com"})
        assert isinstance(result2, dict)  # success, not error string
        assert result2["job_id"] == "fake-job-123"
        assert len(record) == 1
        assert record[0]["method"] == "POST"
        assert record[0]["auth"] == f"LOW {FAKE_AK}:{FAKE_SK}"
        assert record[0]["accept"] == "application/json"

    def test_explicit_args_win_over_env(self, monkeypatch):
        """Both env vars set AND args passed → header carries the ARGS."""
        record = []
        monkeypatch.setattr(
            server_module, "_client",
            make_mock_client(_recorder_handler(record))
        )
        # Set env vars
        monkeypatch.setenv("IA_ACCESS_KEY", "ENVAK")
        monkeypatch.setenv("IA_SECRET_KEY", "ENVSK")

        # Call with explicit args → args win
        result = server_module.handle_call(
            "save_page",
            {"url": "https://example.com", "access_key": "ARGAK", "secret_key": "ARGSK"}
        )
        assert isinstance(result, dict)
        assert len(record) == 1
        assert record[0]["auth"] == "LOW ARGAK:ARGSK"

    def test_no_env_and_no_args_leaks_no_request(self, monkeypatch):
        """Zero _urlopen calls, guidance text preserved byte-exact (s3.php message)."""
        record = []
        monkeypatch.setattr(
            server_module, "_client",
            make_mock_client(_recorder_handler(record))
        )
        monkeypatch.delenv("IA_ACCESS_KEY", raising=False)
        monkeypatch.delenv("IA_SECRET_KEY", raising=False)

        result = server_module.handle_call("save_page", {"url": "https://example.com"})
        assert result == GUIDANCE_MSG
        assert record == []  # zero HTTP requests

    def test_secret_never_in_tools_list_or_discover(self, monkeypatch):
        """Hygiene: with env creds set, discover + tools/list responses contain no 'FAKE' substring.

        Spawns the era server exactly like the era tests do (subprocess) and sends
        server/discover + tools/list, asserting on the serialized response bytes.
        """
        # Build env with fake creds
        env = dict(os.environ)
        env["IA_ACCESS_KEY"] = FAKE_AK
        env["IA_SECRET_KEY"] = FAKE_SK

        p = subprocess.Popen(
            [PROD_PY, "-m", SERVER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        try:
            # Send discover
            discover_req = {"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {}}
            p.stdin.write(json.dumps(discover_req).encode() + b"\n")
            p.stdin.flush()
            line1 = _read_line_with_timeout(p.stdout)
            assert line1, "no response to discover"
            resp1 = json.loads(line1.decode())
            assert "result" in resp1, f"discover errored: {resp1.get('error')!r}"
            discover_text = json.dumps(resp1)
            assert "FAKE" not in discover_text, "discover response contains FAKE"

            # Send tools/list
            tools_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
            p.stdin.write(json.dumps(tools_req).encode() + b"\n")
            p.stdin.flush()
            line2 = _read_line_with_timeout(p.stdout)
            assert line2, "no response to tools/list"
            resp2 = json.loads(line2.decode())
            assert "result" in resp2, f"tools/list errored: {resp2.get('error')!r}"
            tools_text = json.dumps(resp2)
            assert "FAKE" not in tools_text, "tools/list response contains FAKE"

            # Clean shutdown
            p.stdin.close()
            assert p.wait(timeout=5) == 0
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()


class TestProductionVenvIsolation:
    """The pinned PROD_PY is the production interpreter AND stays dependency-free.

    Guards the migration's whole point. A constant merely NAMED PROD_PY proved
    nothing while it pointed at the pre-migration fat venv (httpx/mcp/pydantic/
    fastmcp present), so a regression reintroducing a third-party import would
    have passed silently. These assertions run inside the pinned interpreter
    itself, so they hold for the venv that actually serves production.
    """

    def test_prod_py_matches_live_config_command(self):
        """PROD_PY is the -v2 production venv, not its pre-migration sibling.

        The config pin is duplicated here as a literal (tests must not import
        the live Hermes config); this assertion is what keeps the two in sync.
        """
        assert PROD_PY.endswith(
            "mcp-venvs/internet-archive-mcp-v2/bin/python3"
        ), f"PROD_PY must pin the production -v2 venv, got {PROD_PY}"
        assert "-v2" in Path(PROD_PY).parent.parent.name

    def test_prod_py_interpreter_exists_and_is_executable(self):
        """The pin must name a real interpreter — a typo'd path silently skips nothing."""
        assert Path(PROD_PY).is_file(), f"pinned production interpreter missing: {PROD_PY}"
        assert os.access(PROD_PY, os.X_OK), f"pinned production interpreter not executable: {PROD_PY}"

    def test_production_venv_imports_no_third_party_runtime_modules(self, tmp_path):
        """NEGATIVE: the production interpreter exposes no third-party runtime deps.

        Spawns the pinned PROD_PY (never sys.executable) with a stdlib-only
        probe and asserts BOTH halves of isolation:
          * findable == []  — none are installed in the production venv;
          * resident == []  — importing the server pulls none into sys.modules.
        The second half is what catches a source-level `import httpx` sneaking
        back in, which the first half alone would miss.
        """
        probe = tmp_path / "isolation_probe.py"
        probe.write_text(_ISOLATION_PROBE.format(forbidden=list(FORBIDDEN_RUNTIME_MODULES)))

        result = subprocess.run(
            [PROD_PY, str(probe)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, (
            f"isolation probe failed under {PROD_PY}:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        report = json.loads(result.stdout)
        assert report["executable"] == PROD_PY, (
            f"probe ran under the wrong interpreter: {report['executable']} != {PROD_PY}"
        )
        assert report["findable"] == [], (
            "production venv must not contain third-party runtime modules; "
            f"found installed: {report['findable']} under {PROD_PY}"
        )
        assert report["resident"] == [], (
            "importing internet_archive_mcp.server under the production venv "
            f"loaded third-party modules: {report['resident']}"
        )
