"""
Tests for advisor.config.resolve_advisor_data_root() / ensure_writable_dir()
— the fix for EA writing a relative ``./data`` path at container startup and
failing with "[Errno 13] Permission denied: 'data'" (docs/packaging.md "EA
gaps").

Covers:
  - resolve_advisor_data_root() honours ADVISOR_DATA_PATH when set, and
    falls back to ~/egeria-advisor-data otherwise (it was "./data" until
    2026-11-10; the git-checkout refusal is in test_ops_defaults.py)
  - AdvisorSettings.advisor_cache_dir's default follows the same resolution
    (ADVISOR_CACHE_DIR still wins outright when set)
  - ensure_writable_dir() creates missing parents idempotently
  - ensure_writable_dir() raises a clear, actionable error naming both the
    path and the env var when creation fails
"""
from pathlib import Path

import pytest

from advisor.config import (
    AdvisorSettings,
    DEFAULT_ADVISOR_DATA_ROOT,
    resolve_advisor_data_root,
    ensure_writable_dir,
)


# ---------------------------------------------------------------------------
# resolve_advisor_data_root()
# ---------------------------------------------------------------------------

def test_data_root_defaults_to_home_egeria_advisor_data(monkeypatch):
    monkeypatch.delenv("ADVISOR_DATA_PATH", raising=False)
    assert resolve_advisor_data_root() == DEFAULT_ADVISOR_DATA_ROOT == Path.home() / "egeria-advisor-data"


def test_data_root_honours_env_var(monkeypatch, tmp_path):
    target = tmp_path / "advisor-data"
    monkeypatch.setenv("ADVISOR_DATA_PATH", str(target))
    assert resolve_advisor_data_root() == target


def test_data_root_expands_user(monkeypatch):
    monkeypatch.setenv("ADVISOR_DATA_PATH", "~/egeria-advisor-data")
    result = resolve_advisor_data_root()
    assert "~" not in str(result)
    assert result == Path.home() / "egeria-advisor-data"


def test_data_root_ignores_blank_env_var(monkeypatch):
    monkeypatch.setenv("ADVISOR_DATA_PATH", "   ")
    assert resolve_advisor_data_root() == DEFAULT_ADVISOR_DATA_ROOT


# ---------------------------------------------------------------------------
# AdvisorSettings.advisor_cache_dir default
# ---------------------------------------------------------------------------

def test_cache_dir_default_follows_data_path_when_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("ADVISOR_CACHE_DIR", raising=False)
    target = tmp_path / "advisor-data"
    monkeypatch.setenv("ADVISOR_DATA_PATH", str(target))
    s = AdvisorSettings()
    assert s.advisor_cache_dir == target / "cache"


def test_cache_dir_default_is_under_default_data_root_when_nothing_set(monkeypatch):
    monkeypatch.delenv("ADVISOR_CACHE_DIR", raising=False)
    monkeypatch.delenv("ADVISOR_DATA_PATH", raising=False)
    s = AdvisorSettings()
    assert s.advisor_cache_dir == DEFAULT_ADVISOR_DATA_ROOT / "cache"


def test_cache_dir_env_var_wins_outright(monkeypatch, tmp_path):
    cache_target = tmp_path / "explicit-cache"
    data_target = tmp_path / "advisor-data"
    monkeypatch.setenv("ADVISOR_CACHE_DIR", str(cache_target))
    monkeypatch.setenv("ADVISOR_DATA_PATH", str(data_target))
    s = AdvisorSettings()
    assert s.advisor_cache_dir == cache_target


# ---------------------------------------------------------------------------
# ensure_writable_dir()
# ---------------------------------------------------------------------------

def test_ensure_writable_dir_creates_missing_parents(tmp_path):
    target = tmp_path / "a" / "b" / "c"
    result = ensure_writable_dir(target, "ADVISOR_DATA_PATH")
    assert result == target
    assert target.is_dir()


def test_ensure_writable_dir_is_idempotent(tmp_path):
    target = tmp_path / "already-there"
    target.mkdir()
    # Should not raise on a second call against an existing directory.
    ensure_writable_dir(target, "ADVISOR_DATA_PATH")
    assert target.is_dir()


def test_ensure_writable_dir_raises_clear_error_naming_path_and_env_var(tmp_path):
    # A file where a directory is expected makes mkdir() fail with
    # FileExistsError (an OSError subclass) regardless of permissions,
    # giving a deterministic, platform-independent failure to assert on.
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory")
    target = blocker / "child"

    with pytest.raises(RuntimeError) as exc_info:
        ensure_writable_dir(target, "ADVISOR_CACHE_DIR")

    message = str(exc_info.value)
    assert str(target) in message
    assert "ADVISOR_CACHE_DIR" in message
    assert exc_info.value.__cause__ is not None
