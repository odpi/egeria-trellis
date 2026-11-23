"""Unit-test isolation for the feedback store (Phase 2, A-F1).

Every vote is written to the trellis-feedback store. Without this, a test that posts a vote with no store
configured would write to EA's real Postgres (`advisor/feedback_store.py`'s default). Each test gets
a SQLite file of its own; a test that wants Postgres sets the URL and schema itself, on a throwaway
schema (`tests/conftest.py`'s `ea_test_schema`).
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(autouse=True)
def _feedback_store_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("ADVISOR_FEEDBACK_DATABASE_URL", f"sqlite:///{tmp_path / 'feedback.db'}")
    monkeypatch.delenv("ADVISOR_FEEDBACK_SCHEMA", raising=False)
    yield
    mod = sys.modules.get("advisor.feedback_store")
    if mod is not None:
        mod.reset_stores()



# ── TR-102: unit tests never open a real Postgres (the guard itself is in pg_guard.py, with its tests) ─────────────
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from pg_guard import guard_applies, install_pg_guard  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_postgres(request, monkeypatch):
    if not guard_applies(request):
        yield
        return
    hits = install_pg_guard(monkeypatch, request.node.nodeid)
    request.node.pg_guard_hits = hits
    yield
    if hits:
        pytest.fail(f"TR-102: {request.node.nodeid} opened a real Postgres connection "
                    f"({len(hits)}): {'; '.join(hits[:3])}. Stub the store it reached.", pytrace=False)
