"""GET /api/databases/ must not block the event loop.

Found live 2026-09-29 round 2 (PER-REQUEST-SERVER-LATENCY-ROUND-2-IMPLEMENTED.md), while measuring that round's gate numbers:
`list_databases`'s `_to_summary` calls several synchronous registry methods
directly inside the `async def` handler, unwrapped — including the
round-2 `find_latest_database_survey_with_key`, a real query. A concurrent
boot-sequence measurement caught `/api/auth/me` (touches no database at
all) at 871ms, queued behind this route on the shared event loop. Same bug
class and same fix as `list_projects` (round 1,
test_list_projects_off_the_loop.py) and `/questions`
(BY-ANALYSIS-PROGRESSIVE-AND-GRAPH-IMPLEMENTED.md Sec2a) — this test follows
`test_list_projects_off_the_loop.py`'s own shape.
"""
from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import DatabaseEntity, ProjectRegistry

SLEEP_SECONDS = 2.0


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.register_database(DatabaseEntity(
        slug="slow-db", display_name="Slow DB", db_type="postgresql",
        host="localhost", port=5432, database_name="slow-db",
    ))
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setenv("EXPLORER_EMBED_WORKER", "false")
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None, database_url=None: (
            setattr(self, "__dict__", registry.__dict__) or None
        ),
    )

    def _slow_is_working_set_hidden(self, entity_type, slug):
        time.sleep(SLEEP_SECONDS)
        return False

    monkeypatch.setattr(
        ProjectRegistry, "is_working_set_hidden", _slow_is_working_set_hidden,
    )

    from resource_explorer.web.app import app

    # MUST be `with TestClient(app) as client` — see test_ask_route_off_the_loop.py's
    # comment: starlette's TestClient only shares one blocking-portal event-loop
    # thread across requests when entered as a context manager.
    with TestClient(app) as c:
        yield c


class TestListDatabasesDoesNotBlockTheLoop:
    def test_a_trivial_endpoint_answers_while_list_databases_is_still_running(self, client):
        results: dict[str, float] = {}

        def _do_list_databases() -> None:
            t0 = time.monotonic()
            resp = client.get("/api/databases/")
            results["list_status"] = resp.status_code
            results["list_elapsed"] = time.monotonic() - t0

        lister = threading.Thread(target=_do_list_databases)
        lister.start()
        time.sleep(SLEEP_SECONDS / 3)

        t0 = time.monotonic()
        health_resp = client.get("/health")  # pure liveness — touches nothing slow
        health_elapsed = time.monotonic() - t0

        lister.join(timeout=SLEEP_SECONDS * 3)

        assert health_resp.status_code == 200
        assert health_elapsed < SLEEP_SECONDS / 2, (
            f"/health took {health_elapsed:.2f}s while list_databases() was "
            f"sleeping {SLEEP_SECONDS}s — the event loop was blocked"
        )
        assert results["list_status"] == 200
        assert results["list_elapsed"] >= SLEEP_SECONDS
