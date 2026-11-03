"""Brief Z2 round 3 addendum (owner, 2026-10-11, "Yes to column"): `resource_schedules.created_by`.

A schedule records who created (or last enabled) it, and a timer run runs as that person, so the run-time access
re-check judges THEIR rights. A schedule with no creator follows the no-requester rule. The migration is the
registry's own idempotent ADD COLUMN.

Registries here are a tmp SQLite file, or the throwaway Postgres test schema (`pg_registry`); never the real one.
No Egeria: access reads faked at the client boundary (`tests/zone_fakes.py`).
"""
from __future__ import annotations

import pytest

from resource_explorer.registry import Project, ProjectRegistry
from tests.zone_fakes import element as el
from tests.zone_fakes import install

SECURED = "finance-zone"


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "sched.db"))
    assert str(tmp_path) in r.database_url                                    # never the shared registry
    r.add(Project(slug="p", display_name="P", github_url="https://github.com/x/p", description=""))
    r.set_egeria_asset_guid("p", "g-p")
    return r


def _columns(reg) -> list[str]:
    with reg._conn() as conn:
        return sorted(reg._get_table_columns(conn, "resource_schedules"))


# ── the migration ───────────────────────────────────────────────────────────────────────────────────────

def test_the_column_is_added_once_and_a_second_init_is_a_no_op(tmp_path):
    path = str(tmp_path / "twice.db")
    first = _columns(ProjectRegistry(db_path=path))
    second = _columns(ProjectRegistry(db_path=path))
    assert "created_by" in first and first == second


def test_the_migration_is_idempotent_on_postgres(pg_registry, monkeypatch):
    from resource_explorer.registry import ProjectRegistry as PR

    assert "resource_explorer_test" in pg_registry.database_url                # the throwaway schema only
    before = _columns(pg_registry)
    # A process initialises a Postgres URL once (`_pg_schema_ready`); forget that, so the init really runs again.
    monkeypatch.setattr(PR, "_pg_schema_ready", set())
    again = PR(database_url=pg_registry.database_url)                         # the init runs its migrations again
    assert "created_by" in before and _columns(again) == before


# ── the creator is recorded ─────────────────────────────────────────────────────────────────────────────

def test_the_route_records_the_signed_in_creator(registry, monkeypatch):
    from fastapi.testclient import TestClient

    install(monkeypatch)                                                       # no zone in use: open
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    monkeypatch.setattr("resource_explorer.auth.get_current_user",
                        lambda request: {"user_id": "dana", "egeria_token": "t"})
    from resource_explorer.web.app import app

    r = TestClient(app).post("/api/schedules/repo/p", json={"analysis_id": "repo_health", "schedule": "daily"})
    assert r.status_code == 200, r.text
    assert [s["created_by"] for s in registry.get_schedules("repo", "p")] == ["dana"]


def test_enabling_takes_the_creator_and_disabling_keeps_it(registry):
    registry.save_schedule("repo", "p", "repo_health", "daily", enabled=True, created_by="dana")
    registry.save_schedule("repo", "p", "repo_health", "daily", enabled=False, created_by="erin")
    assert registry.get_schedules("repo", "p")[0]["created_by"] == "dana"
    registry.save_schedule("repo", "p", "repo_health", "daily", enabled=True, created_by="erin")
    assert registry.get_schedules("repo", "p")[0]["created_by"] == "erin"


# ── a timer run runs as the creator, and the re-check judges them ───────────────────────────────────────

def _due(registry, created_by):
    registry.save_schedule("repo", "p", "repo_health", "daily", enabled=True, created_by=created_by)
    with registry._conn() as conn:
        conn.execute("UPDATE resource_schedules SET next_run = '2000-01-01T00:00:00+00:00'")


def _run_due_judging(registry, monkeypatch):
    """`scheduler._run_due` with its one analysis replaced by the run-time re-check the real one makes before
    auto-publishing (`person_write_refusal`): what it answers, and who the caller was."""
    from resource_explorer import scheduler
    from resource_explorer.a2a_auth import caller
    from resource_explorer.workflows.curate import person_write_refusal

    seen = []

    def execute(entity_type, slug, analysis_id, reg, next_run, target_kind=None):
        who = caller()
        seen.append((who.user_id if who else None, person_write_refusal(reg, entity_type, slug)))
        return slug, "", []
    monkeypatch.setattr(scheduler, "_execute", execute)
    monkeypatch.setattr(scheduler, "_coalesce_repo_surveys", lambda due, reg: {})
    monkeypatch.setattr(scheduler, "_check_subscriptions", lambda *a, **k: None)
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    scheduler._run_due()
    return seen


def test_a_timer_run_runs_as_the_creator_and_their_rights_are_judged(registry, monkeypatch):
    fake = install(monkeypatch)
    fake.elements["g-p"] = el("g-p", [SECURED])
    fake.controls[SECURED] = {"associatedSecurityList": {"DEFAULT": ["dana"]}}
    _due(registry, "dana")
    assert _run_due_judging(registry, monkeypatch) == [("dana", "")]           # dana is granted: it publishes


def test_a_timer_run_for_a_creator_without_the_grant_is_refused_in_their_name(registry, monkeypatch):
    fake = install(monkeypatch)
    fake.elements["g-p"] = el("g-p", [SECURED])
    fake.controls[SECURED] = {"associatedSecurityList": {"DEFAULT": ["dbas"]}}
    _due(registry, "erin")
    assert _run_due_judging(registry, monkeypatch) == [
        ("erin", f"not permitted · zone {SECURED} does not grant erin update in Egeria")]


def test_a_schedule_with_no_creator_follows_the_no_requester_rule(registry, monkeypatch):
    from resource_explorer.workflows.curate import NO_REQUESTER_WORDS

    fake = install(monkeypatch)
    fake.elements["g-p"] = el("g-p", [SECURED])
    fake.controls[SECURED] = {"associatedSecurityList": {"DEFAULT": ["dbas"]}}
    _due(registry, None)
    assert _run_due_judging(registry, monkeypatch) == [
        (None, f"not permitted · {NO_REQUESTER_WORDS}, and zone {SECURED} is in use")]


def test_two_processes_starting_together_both_add_the_column_safely(pg_registry, monkeypatch):
    """Round 4: both see the column missing; the second ADD must not fail (ADD COLUMN IF NOT EXISTS)."""
    from resource_explorer.registry import ProjectRegistry as PR

    assert "resource_explorer_test" in pg_registry.database_url                # the throwaway schema only
    real = PR._get_table_columns

    def blind(self, conn, table):
        cols = real(self, conn, table)
        return cols - {"created_by"} if table == "resource_schedules" else cols
    monkeypatch.setattr(PR, "_get_table_columns", blind)
    monkeypatch.setattr(PR, "_pg_schema_ready", set())                         # a second process: init again
    again = PR(database_url=pg_registry.database_url)                          # the column is already there
    monkeypatch.setattr(PR, "_get_table_columns", real)
    assert "created_by" in _columns(again)
