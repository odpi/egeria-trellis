"""EA's startup and shutdown hooks run (Phase 1 Q1).

The regression this catches has happened: TC-5 slice 1 (2026-09) deleted app.py's startup and
shutdown handlers along with the block being moved out, and nothing failed. Every test used a bare
`TestClient(app)`, which never runs lifespan events. A peer only noticed by diffing a redeployed
container's startup behaviour (see the `_startup` docstring in advisor/web/app.py).

Here the app is entered with `with TestClient(app):`, so startup and shutdown run, and each hook is
replaced by a recorder:
  - PgVectorStore.provision_schema   (pgvector tables exist before the first query)
  - the MCP warm-up                  (ReportPipeline._ensure_agent, in a background thread)
  - start_background_sync            (report specs held in Egeria, brief B3)
  - shutdown_mcp_agent               (no orphaned MCP subprocess on reload)

No Postgres, no MCP server, no Egeria: nothing real is started. This test only imports app.py.
"""
from __future__ import annotations

import threading

import pytest

# Opens a real Postgres connection on construction (the metrics store): needs the password (TR-12 R1).
pytestmark = pytest.mark.requires_pgvector


@pytest.fixture
def recorded(monkeypatch):
    ran: dict = {}
    warmed = threading.Event()

    class FakeStore:
        def __init__(self, *a, **k):
            pass

        def provision_schema(self, *a, **k):
            ran["provision_schema"] = True

    class FakePipeline:
        def _ensure_agent(self):
            ran["mcp_warm_up"] = True
            warmed.set()

    def fake_sync(*a, **k):
        ran["start_background_sync"] = True

    async def fake_shutdown():
        ran["shutdown_mcp_agent"] = True

    monkeypatch.setattr("advisor.vector_store_pg.PgVectorStore", FakeStore)
    monkeypatch.setattr("advisor.report_pipeline.get_report_pipeline", lambda: FakePipeline())
    monkeypatch.setattr("advisor.egeria_spec_sync.start_background_sync", fake_sync)
    monkeypatch.setattr("advisor.mcp_agent.shutdown_mcp_agent", fake_shutdown)
    return ran, warmed


def test_startup_runs_all_three_hooks_and_shutdown_reaps_mcp(recorded):
    from fastapi.testclient import TestClient
    from advisor.web.app import app

    ran, warmed = recorded
    with TestClient(app):
        assert warmed.wait(5), "the MCP warm-up thread never ran"
        assert ran.get("provision_schema"), "startup did not provision the pgvector schema"
        assert ran.get("mcp_warm_up"), "startup did not warm the MCP agent"
        assert ran.get("start_background_sync"), "startup did not start the report-spec sync"
        assert "shutdown_mcp_agent" not in ran
    assert ran.get("shutdown_mcp_agent"), "shutdown did not reap the MCP agent"


def test_a_failing_hook_does_not_stop_the_others(monkeypatch, recorded):
    """Startup never blocks on a service: one hook failing still lets the rest run."""
    from fastapi.testclient import TestClient
    from advisor.web.app import app

    ran, warmed = recorded

    class BrokenStore:
        def __init__(self, *a, **k):
            raise ConnectionError("pgvector not reachable")
    monkeypatch.setattr("advisor.vector_store_pg.PgVectorStore", BrokenStore)

    with TestClient(app):
        assert warmed.wait(5)
        assert ran.get("start_background_sync")
        assert "provision_schema" not in ran
