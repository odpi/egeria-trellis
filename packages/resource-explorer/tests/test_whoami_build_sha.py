"""PI-137: the connection popover shows the build the server was started from."""
from __future__ import annotations

import subprocess

import pytest

from resource_explorer.web.routes import egeria as egeria_routes


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(egeria_routes, "_BUILD_SHA_READ", False)
    monkeypatch.setattr(egeria_routes, "_BUILD_SHA", None)
    monkeypatch.delenv("RE_BUILD_SHA", raising=False)


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("RE_BUILD_SHA", "abc123")
    assert egeria_routes.build_sha() == "abc123"


def test_reads_git_head(monkeypatch):
    class _Out:
        stdout = "deadbeef\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Out())
    assert egeria_routes.build_sha() == "deadbeef"


def test_unreadable_is_none_not_empty(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", boom)
    assert egeria_routes.build_sha() is None


def test_whoami_carries_the_build_sha(monkeypatch):
    monkeypatch.setenv("RE_BUILD_SHA", "feedface")
    body = egeria_routes.whoami()
    assert body["build_sha"] == "feedface"
    assert {"user_id", "view_server", "platform_url"} <= set(body)
