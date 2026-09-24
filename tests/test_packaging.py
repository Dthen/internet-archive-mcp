"""Packaging/runtime fixture tests for the built package."""

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import internet_archive_mcp.server as server


def test_built_wheel_contains_runtime_tool_capture(tmp_path):
    """The frozen capture must be present in the distributable wheel."""
    project_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(tmp_path),
            str(project_root),
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        assert "internet_archive_mcp/data/internet-archive.tools.json" in wheel.namelist()


def test_runtime_tool_capture_is_packaged_and_matches_golden():
    """The server must not depend on a repository-only golden path at runtime."""
    package_root = Path(server.__file__).resolve().parent
    packaged = package_root / "data" / "internet-archive.tools.json"
    repo_golden = package_root.parents[1] / "golden" / "internet-archive.tools.json"

    assert packaged.is_file(), f"packaged tool capture is missing: {packaged}"
    assert json.loads(packaged.read_text()) == json.loads(repo_golden.read_text())
