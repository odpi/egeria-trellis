"""GET /api/projects/ must not block the event loop.

2026-09-29 per-request-latency investigation
(PER-REQUEST-SERVER-LATENCY-IMPLEMENTED.md): a live
SIGUSR1 thread dump during a concurrent by-analysis-pane load caught the
main/event-loop thread itself parked inside `is_working_set_hidden` ->
`ProjectRegistry._conn` -> a Postgres connection-pool checkout, called
directly from `list_projects`'s per-project `_to_summary(p, registry)` loop
-- not wrapped in `asyncio.to_thread` the way the sibling `/survey-results`
routes already are. Every other in-flight request, including ones touching
no database at all (`/api/auth/me`), queued behind it. Same bug class as
the `/questions` fix in BY-ANALYSIS-PROGRESSIVE-AND-GRAPH-IMPLEMENTED.md
Sec2a, and this test follows that fix's own regression test
(test_ask_route_off_the_loop.py) line for line.

Updated 2026-09-29 round 2 (PER-REQUEST-SERVER-LATENCY-ROUND-2-IMPLEMENTED.md):
`_list_projects_sync` no longer calls the singular
`is_working_set_hidden` per project at all — it batch-fetches via
`get_working_set_hidden_for_entities`, one call for the whole list. The
event-loop-blocking property this test pins is about `asyncio.to_thread`
wrapping `list_projects`'s BODY, not about which specific registry method
is slow — so the slow-call injection point moved to the batch method the
route actually calls now, keeping the same test shape and the same
"a trivial concurrent request must not wait behind it" assertion.
"""
from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import Project, ProjectRegistry

SLEEP_SECONDS = 2.0


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.add(Project(
        slug="slow-repo",
        display_name="Slow Repo",
        github_url="https://github.com/my-org/slow-repo",
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

    def _slow_get_working_set_hidden_for_entities(self, entity_type, entity_slugs, **kwargs):
        time.sleep(SLEEP_SECONDS)
        return {slug: False for slug in entity_slugs}

    monkeypatch.setattr(
        ProjectRegistry,
        "get_working_set_hidden_for_entities",
        _slow_get_working_set_hidden_for_entities,
    )

    from resource_explorer.web.app import app

    # MUST be `with TestClient(app) as client` — see test_ask_route_off_the_loop.py's
    # comment: starlette's TestClient only shares one blocking-portal event-loop
    # thread across requests when entered as a context manager.
    with TestClient(app) as c:
        yield c


class TestListProjectsDoesNotBlockTheLoop:
    def test_a_trivial_endpoint_answers_while_list_projects_is_still_running(self, client):
        results: dict[str, float] = {}

        def _do_list_projects() -> None:
            t0 = time.monotonic()
            resp = client.get("/api/projects/")
            results["list_status"] = resp.status_code
            results["list_elapsed"] = time.monotonic() - t0

        lister = threading.Thread(target=_do_list_projects)
        lister.start()
        # Give list_projects time to actually start (and, before the fix, to
        # seize the loop thread inside is_working_set_hidden) before firing
        # the trivial request.
        time.sleep(SLEEP_SECONDS / 3)

        t0 = time.monotonic()
        health_resp = client.get("/health")  # pure liveness — touches nothing slow
        health_elapsed = time.monotonic() - t0

        lister.join(timeout=SLEEP_SECONDS * 3)

        assert health_resp.status_code == 200
        # The whole point: /health must not have waited behind the slow
        # list_projects call. Before the fix this reliably took >= SLEEP_SECONDS
        # because both requests shared one blocked event-loop thread.
        assert health_elapsed < SLEEP_SECONDS / 2, (
            f"/health took {health_elapsed:.2f}s while list_projects() was "
            f"sleeping {SLEEP_SECONDS}s — the event loop was blocked"
        )
        assert results["list_status"] == 200
        assert results["list_elapsed"] >= SLEEP_SECONDS
