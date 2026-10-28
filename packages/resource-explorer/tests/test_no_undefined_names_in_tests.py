"""No test file may reference an undefined name.

Tests that need pgvector skip in most environments, so a NameError inside one
(2026-10-05: `tmp_path` used in a test that did not request it) passes every
local run and fails only where Postgres is reachable. Undefined names are
found statically, without running the test.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).parent


def _ruff() -> list[str] | None:
    exe = shutil.which("ruff")
    if exe:
        return [exe]
    probe = subprocess.run([sys.executable, "-m", "ruff", "--version"], capture_output=True)
    return [sys.executable, "-m", "ruff"] if probe.returncode == 0 else None


def _undefined(path: Path) -> str:
    cmd = _ruff()
    if cmd is None:
        pytest.skip("ruff not installed")
    r = subprocess.run(cmd + ["check", "--select", "F821", "--no-cache", "--isolated", str(path)],
                       capture_output=True, text=True)
    return "" if r.returncode == 0 else r.stdout + r.stderr


def test_a_test_using_an_unrequested_fixture_name_is_caught(tmp_path):
    bad = tmp_path / "t.py"
    bad.write_text("def test_x(monkeypatch):\n    print(tmp_path)\n")
    assert "F821" in _undefined(bad)


def test_no_undefined_names_in_any_test_file():
    problems = _undefined(TESTS)
    assert problems == "", problems
