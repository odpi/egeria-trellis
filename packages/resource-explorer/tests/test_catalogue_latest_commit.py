"""A reload must not forget the commit the page was watching: `GET /api/catalogue-scope/{slug}/commits/latest`.

The newest catalog commit for a database, read from the REGISTRY (the curation record's steps and run state, the
proof-derived schema states), so a second tab and a reload see the same thing. Read-only: it writes nothing and never
contacts Egeria. Everything here runs against a temp SQLite registry."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_catalogue_commit import (  # noqa: E402,F401
    ME, _zones, choose, client, entity, fake, press, registry, step, view, world)

from resource_explorer import catalogue_commit as cc  # noqa: E402
from resource_explorer.curate_plan import Curations  # noqa: E402


@pytest.fixture(autouse=True)
def _zone_access_open(monkeypatch):
    """Brief Z2: this write path asks `curation_access` (the one decision). These tests are about something else,
    so no zone is in use: the access reads are faked open at the client boundary (`tests/zone_fakes.open_access`)."""
    from tests.zone_fakes import open_access

    return open_access(monkeypatch)

# Brief I: a catalog commit is a person's own action; it runs as a signed-in caller.
pytestmark = pytest.mark.usefixtures("signed_in_caller")


def _iso(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).replace(tzinfo=None).isoformat(timespec="seconds")


def _commit(world, *, state: str, hours_ago: float = 0.0, steps=None):
    """A curation record with the given run state, steps and age (the registry's own rows)."""
    cur = Curations(world["registry"])
    rec = cur.create("database", "db", author=ME, selection={"attach": []}, manifest={}, steps=list(cc.STEPS_DB))
    for name, st, detail in (steps or []):
        cur.set_step(rec["id"], name, st, detail)
    with world["registry"]._conn() as conn:
        conn.execute("UPDATE resource_curation SET state = ?, requested_at = ?, finished_at = ? WHERE id = ?",
                     (state, _iso(hours_ago), _iso(hours_ago) if state in ("done", "failed") else "", rec["id"]))
    return rec["id"]


def latest(client):
    r = client.get("/api/catalogue-scope/db/commits/latest")
    assert r.status_code == 200, r.text
    return r.json()


def test_no_commit_yet_says_none_and_writes_nothing(client, world, registry):
    j = latest(client)
    assert j == {"commit": None, "terminal": None, "age_hours": None, "stale_unfinished": False, "states": {}}
    with registry._conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM resource_curation").fetchone()["n"] == 0


def test_the_newest_commit_wins_and_carries_its_steps(client, world):
    old = _commit(world, state="done", hours_ago=30)
    new = _commit(world, state="running", hours_ago=0.2, steps=[("publish_elements", "done", "server s"), ("survey", "submitted", "submitted · 10-05 09:00")])
    j = latest(client)
    assert j["commit"]["id"] == new and j["commit"]["id"] != old
    by = {s["name"]: s["state"] for s in j["commit"]["steps"]}
    assert by["publish_elements"] == "done" and by["survey"] == "submitted"
    assert j["terminal"] is False and j["age_hours"] < 1 and j["stale_unfinished"] is False


@pytest.mark.parametrize("state,terminal", [("queued", False), ("running", False), ("done", True), ("failed", True)])
def test_each_run_state_is_reported_as_terminal_or_not(client, world, state, terminal):
    _commit(world, state=state, hours_ago=1)
    j = latest(client)
    assert j["commit"]["state"] == state and j["terminal"] is terminal


def test_a_commit_that_never_finished_after_six_hours_is_stale_not_running(client, world):
    _commit(world, state="running", hours_ago=7)
    j = latest(client)
    assert j["terminal"] is False and j["stale_unfinished"] is True


def test_the_states_are_derived_from_proof_rows_not_from_the_record(client, world, fake):
    choose(world, "sales", "catalogue")
    press(world, fake, refresh=True)                    # a real commit: proof rows for sales exist
    j = latest(client)
    assert j["commit"]["state"] in ("done", "failed")
    assert j["states"]["sales"]["state"] == "catalogued"
    assert "words" in j["states"]["sales"]


def test_reading_it_twice_changes_nothing_and_a_second_reader_sees_the_same(client, world, registry):
    _commit(world, state="running", hours_ago=0.1)
    a, b = latest(client), latest(client)                # "two tabs": both read the registry
    assert a == b
    with registry._conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM resource_curation").fetchone()["n"] == 1


def test_only_catalog_commits_count_not_other_records(client, world):
    cur = Curations(world["registry"])
    # a report-kind record newer than the commit must not be returned as "the last commit"
    commit_id = _commit(world, state="done", hours_ago=5)
    rep = cur.create("database", "db", author=ME, selection={}, manifest={}, steps=[])
    with world["registry"]._conn() as conn:
        conn.execute("UPDATE resource_curation SET kind = 'report', requested_at = ? WHERE id = ?", (_iso(0.1), rep["id"]))
    assert latest(client)["commit"]["id"] == commit_id


def test_the_route_is_declared_before_the_id_route():
    from resource_explorer.web.routes import catalogue_scope as r
    paths = [getattr(x, "path", "") for x in r.router.routes]
    assert paths.index("/{slug}/commits/latest") < paths.index("/{slug}/commits/{curation_id}")
