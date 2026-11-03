"""Quality slice Q3: the DatabaseQualitySurvey across a work list, priced first, and on a schedule that skips the
unchanged (DESIGN-QUALITY-AT-SCALE-AND-TESTGEN.md §2.2, §3 row Q3).

The gate, as a test: a work list of coco_pharma and adventureworks run as one batch with two priced rows; a second
scheduled run skips the unchanged one and says so.

Temp SQLite registry; nothing reaches Egeria, a database or the shared Postgres. The queue rows are inspected, not
executed: what a queued `survey_definition_run` does is Q2's, and its own tests cover it.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from resource_explorer import quality_batch
from resource_explorer.quality_batch import QUALITY_DEFINITION, WORK_LIST_ENTITY
from resource_explorer.registry import TARGET_SURVEY, DatabaseEntity, ProjectRegistry
from resource_explorer.work_lists import WorkLists

PHARMA, WORKS = "coco_pharma", "adventureworks"
T0 = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


def _at(hours: float) -> str:
    return (T0 + timedelta(hours=hours)).isoformat()


#: (schema, table) -> (row_count, size_bytes, [(column, type)])
PHARMA_INV = {("public", "drugs"): (120, 81920, [("id", "integer"), ("name", "text")]),
              ("public", "trials"): (40, 16384, [("id", "integer"), ("started", "date")])}
WORKS_INV = {("sales", "orders"): (31465, 4_194_304, [("order_id", "integer"), ("placed", "timestamp")]),
             ("sales", "notes"): (12, None, [("note", "text")])}


def _inventory(reg, slug: str, at: str, inv: dict) -> None:
    tables, columns = [], []
    for (schema, table), (rows, size, cols) in inv.items():
        tables.append({"schema_name": schema, "table_name": table, "table_type": "BASE TABLE",
                       "row_count": rows, "size_bytes": size})
        for i, (name, dtype) in enumerate(cols, start=1):
            columns.append({"schema_name": schema, "table_name": table, "column_name": name,
                            "ordinal_position": i, "data_type": dtype, "base_type": dtype})
    reg.write_detail_rows("database_tables", slug, at, rows=tables)
    reg.write_detail_rows("database_columns", slug, at, rows=columns)


def _quality_run(reg, slug: str, at: str, inv: dict) -> None:
    """What a finished quality run leaves for skip-unchanged to read: a value-profile batch."""
    rows = [{"schema_name": s, "table_name": t, "column_name": c, "general_type": "A", "record_ct": n}
            for (s, t), (n, _size, cols) in inv.items() for c, _ in cols]
    reg.write_detail_rows("database_value_profiles", slug, at, rows=rows)


@pytest.fixture
def registry(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "q3.db"))
    for slug, name in ((PHARMA, "Coco Pharma"), (WORKS, "AdventureWorks")):
        reg.register_database(DatabaseEntity(slug=slug, display_name=name, db_type="postgresql", host="localhost",
                                             port=5432, database_name=slug, db_user="surveyor", db_password="p"))
    _inventory(reg, PHARMA, _at(0), PHARMA_INV)
    _inventory(reg, WORKS, _at(0), WORKS_INV)
    return reg


@pytest.fixture
def work_list(registry):
    return WorkLists(registry).create("Quality targets", [PHARMA, WORKS], entity_type="database",
                                      created_by="steward")["slug"]


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    # No Egeria project is assigned, so nothing asks Egeria whether the caller may publish (Brief Z3's own rule).
    monkeypatch.setattr(type(registry), "has_assigned_egeria_project", lambda self, *a, **k: False)
    from resource_explorer.web.app import app

    return TestClient(app)


def _queued(reg) -> list[dict]:
    rows = [r for r in reg.list_runs(kind="survey_definition_run") if r["state"] == "queued"]
    for r in rows:
        r["target"] = json.loads(r["target"])
    return rows


def _finish_all(reg, seconds: float = 90.0) -> None:
    """The queue ran every queued row: each succeeded and took `seconds`."""
    with reg._conn() as conn:
        for r in _queued(reg):
            conn.execute("UPDATE runs SET state='succeeded', started_at=?, finished_at=? WHERE id=?",
                         (_at(1), (T0 + timedelta(hours=1, seconds=seconds)).isoformat(), r["id"]))


# ── The price line ──────────────────────────────────────────────────────────────────────────────────────

def test_two_priced_rows_before_anything_is_queued_each_figure_saying_what_it_is(client, registry, work_list):
    resp = client.post("/api/quality-batch/price", json={"work_list_slug": work_list})
    assert resp.status_code == 200, resp.text
    price = resp.json()
    assert _queued(registry) == []                      # the price queues nothing
    rows = {r["entity_slug"]: r for r in price["rows"]}
    assert set(rows) == {PHARMA, WORKS} and price["runnable"] == 2
    assert rows[PHARMA]["tables"]["count"] == 2 and rows[PHARMA]["size"]["words"] == "96.0 KB"
    # a table with no size is said, never added as zero
    assert rows[WORKS]["size"]["unknown_tables"] == 1
    assert rows[WORKS]["size"]["words"] == "4.0 MB · 1 table(s) size unknown · never analyzed"
    for r in rows.values():
        assert r["time"] == {"seconds": None, "basis": "not measured", "runs": 0,
                             "words": "time not measured yet · the first run sets it"}
        assert r["budget_words"] == "each table's profile stops at 5 min"
        assert r["change"]["words"] == "never had a quality run" and r["change"]["unchanged"] is False
    assert price["total"]["words"] == "not measured yet · the first batch sets the price"


def test_the_measured_wall_time_of_a_run_becomes_the_next_estimate(client, registry, work_list):
    client.post("/api/quality-batch/run", json={"work_list_slug": work_list})
    _finish_all(registry, seconds=90)
    rows = {r["entity_slug"]: r for r in quality_batch.price_batch(registry, [PHARMA, WORKS])["rows"]}
    assert rows[PHARMA]["time"]["basis"] == "measured"
    assert rows[PHARMA]["time"]["words"] == "about 1m 30s · measured over 1 run(s)"
    total = quality_batch.price_batch(registry, [PHARMA, WORKS])["total"]
    assert total["seconds"] == 180 and total["words"].endswith("· measured")


def test_a_database_with_no_stored_credential_cannot_run_and_is_never_queued(client, registry, work_list):
    registry.update_database_credentials(WORKS, "", "")
    row = quality_batch.price_row(registry, WORKS)
    assert row["can_run"] is False
    assert row["words"] == "cannot run · no stored credential · register one before a batch can run it"
    out = client.post("/api/quality-batch/run", json={"work_list_slug": work_list}).json()
    outcomes = {m["entity_slug"]: m["outcome"] for m in out["members"]}
    assert outcomes == {PHARMA: "queued", WORKS: "cannot run"}
    assert [r["target"]["slug"] for r in _queued(registry)] == [PHARMA]
    assert out["summary"] == ("1 queued · 1 cannot run: adventureworks cannot run · no stored credential · "
                              "register one before a batch can run it")


# ── The batch ───────────────────────────────────────────────────────────────────────────────────────────

def test_one_batch_is_one_queued_definition_run_per_database_watched_as_a_run_set(client, registry, work_list):
    resp = client.post("/api/quality-batch/run", json={"work_list_slug": work_list})
    assert resp.status_code == 200, resp.text
    out = resp.json()
    queued = _queued(registry)
    assert sorted(r["target"]["slug"] for r in queued) == [WORKS, PHARMA]
    for r in queued:
        assert r["target"]["entity_type"] == "database"
        # Queued under the definition's qualified name: the bare name does not resolve in Egeria (2026-10-30).
        assert r["target"]["params"]["survey_definition_ref"] == f"GovActionProcess::{QUALITY_DEFINITION}"
        assert r["result_ref"]                           # each run has its own activity row, as a press does
    assert all(m["outcome"] == "queued" and m["words"].startswith("queued · 2 tables") for m in out["members"])
    progress = client.get(f"/api/work-lists/runs/sets/{out['set_id']}").json()
    assert progress["total"] == 2 and progress["counts"] == {"queued": 2}
    assert {r["analysis_id"] for r in progress["runs"]} == {QUALITY_DEFINITION}


def test_a_refused_member_is_reported_by_name_and_the_rest_run(registry):
    out = quality_batch.enqueue_quality_batch(
        registry, [PHARMA, WORKS], refusal=lambda s: "not permitted · zone 'finance'" if s == WORKS else "")
    assert {m["entity_slug"]: m["words"] for m in out["members"] if m["outcome"] == "not permitted"} == {
        WORKS: "not permitted · zone 'finance'"}
    assert [r["target"]["slug"] for r in _queued(registry)] == [PHARMA]


def test_a_work_list_of_another_kind_is_refused_with_its_kind(client, registry):
    repo_list = WorkLists(registry).create("Repos", ["x"], entity_type="repo")["slug"]
    resp = client.post("/api/quality-batch/run", json={"work_list_slug": repo_list})
    assert resp.status_code == 422 and "runs on databases" in resp.json()["detail"]
    assert client.post("/api/quality-batch/run", json={"work_list_slug": "nope"}).status_code == 404


def test_a_press_never_skips_an_unchanged_database_it_only_says_so(registry):
    _quality_run(registry, WORKS, _at(1), WORKS_INV)
    out = quality_batch.enqueue_quality_batch(registry, [WORKS], skip_unchanged=False)
    [member] = out["members"]
    assert member["outcome"] == "queued" and member["price"]["change"]["unchanged"] is True


# ── Skip-unchanged ──────────────────────────────────────────────────────────────────────────────────────

def test_no_new_inventory_since_the_last_quality_run_is_unchanged(registry):
    _quality_run(registry, WORKS, _at(1), WORKS_INV)
    v = quality_batch.inventory_change(registry, WORKS)
    assert v.unchanged is True
    assert v.words == ("unchanged since its last quality run on 2026-10-20 10:00Z · no new inventory since "
                       "2026-10-20 09:00Z")


def test_a_new_inventory_that_matches_is_unchanged_and_one_that_differs_is_not(registry):
    _quality_run(registry, WORKS, _at(1), WORKS_INV)
    _inventory(registry, WORKS, _at(2), WORKS_INV)
    same = quality_batch.inventory_change(registry, WORKS)
    assert same.unchanged is True and same.words.endswith("· the inventory of 2026-10-20 11:00Z matches")

    grown = {**WORKS_INV, ("sales", "orders"): (31470, 4_194_304, WORKS_INV[("sales", "orders")][2])}
    grown[("sales", "returns")] = (3, 8192, [("order_id", "integer")])
    _inventory(registry, WORKS, _at(3), grown)
    changed = quality_batch.inventory_change(registry, WORKS)
    assert changed.unchanged is False
    assert changed.words == ("changed since its last quality run on 2026-10-20 10:00Z · 1 table(s) added; "
                             "1 column(s) added; row counts changed on 1 table(s)")


def test_a_row_count_that_was_not_measured_is_cannot_tell_and_runs(registry):
    _quality_run(registry, PHARMA, _at(1), PHARMA_INV)
    blind = {k: (None if k == ("public", "drugs") else v[0], v[1], v[2]) for k, v in PHARMA_INV.items()}
    _inventory(registry, PHARMA, _at(2), blind)
    v = quality_batch.inventory_change(registry, PHARMA)
    assert v.unchanged is False
    assert v.words == "cannot tell whether it changed · row count not measured on 1 table(s)"


def test_a_retyped_column_is_a_change(registry):
    _quality_run(registry, PHARMA, _at(1), PHARMA_INV)
    retyped = {**PHARMA_INV, ("public", "trials"): (40, 16384, [("id", "integer"), ("started", "timestamp")])}
    _inventory(registry, PHARMA, _at(2), retyped)
    assert quality_batch.inventory_change(registry, PHARMA).words.endswith("· 1 column(s) retyped")


def test_no_inventory_at_all_is_cannot_tell_never_unchanged(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "bare.db"))
    reg.register_database(DatabaseEntity(slug="bare", display_name="Bare", db_type="postgresql", host="h",
                                         port=5432, database_name="bare", db_user="u", db_password="p"))
    _quality_run(reg, "bare", _at(1), PHARMA_INV)
    v = quality_batch.inventory_change(reg, "bare")
    assert v.unchanged is False and v.words == "no inventory stored · cannot tell whether it changed"
    assert quality_batch.price_row(reg, "bare")["tables"]["words"] == "tables not recorded · no survey stored yet"


# ── The schedule ────────────────────────────────────────────────────────────────────────────────────────

def test_a_work_list_is_scheduled_through_the_ordinary_schedules_api(client, registry, work_list):
    resp = client.post(f"/api/schedules/{WORK_LIST_ENTITY}/{work_list}",
                       json={"analysis_id": QUALITY_DEFINITION, "schedule": "weekly", "target_kind": "survey"})
    assert resp.status_code == 200, resp.text
    [row] = registry.get_schedules(WORK_LIST_ENTITY, work_list)
    assert row["analysis_id"] == QUALITY_DEFINITION and row["target_kind"] == TARGET_SURVEY and row["next_run"]


@pytest.mark.parametrize("body, status, words", [
    ({"analysis_id": "db_fingerprint", "schedule": "weekly"}, 422, "DatabaseQualitySurvey survey only"),
    ({"analysis_id": QUALITY_DEFINITION, "schedule": "weekly", "target_kind": "analysis"}, 422, "survey only"),
])
def test_a_work_list_schedule_is_for_the_quality_survey_only(client, work_list, body, status, words):
    resp = client.post(f"/api/schedules/{WORK_LIST_ENTITY}/{work_list}", json=body)
    assert resp.status_code == status and words in resp.json()["detail"]


def test_a_schedule_on_a_missing_work_list_is_refused_when_it_is_made(client):
    resp = client.post(f"/api/schedules/{WORK_LIST_ENTITY}/nope",
                       json={"analysis_id": QUALITY_DEFINITION, "schedule": "weekly", "target_kind": "survey"})
    assert resp.status_code == 404


def _make_due(reg, work_list: str) -> None:
    with reg._conn() as conn:
        conn.execute("UPDATE resource_schedules SET next_run=? WHERE entity_type=? AND entity_slug=?",
                     ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), WORK_LIST_ENTITY, work_list))


def _fire(registry, monkeypatch) -> dict:
    """One scheduler pass over the due schedules, against this registry. Returns the work-list schedule's activity."""
    from resource_explorer import scheduler

    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setattr(type(registry), "has_assigned_egeria_project", lambda self, *a, **k: False)
    scheduler._run_due()
    [row] = [s for s in registry.list_all_schedules() if s["entity_type"] == WORK_LIST_ENTITY]
    return {"schedule": row, "activity": registry.get_activity(row["last_run_activity_id"])}


def test_the_gate_two_priced_rows_then_a_scheduled_run_that_skips_the_unchanged_one_and_says_so(
        registry, work_list, monkeypatch):
    registry.save_schedule(WORK_LIST_ENTITY, work_list, QUALITY_DEFINITION, "weekly", target_kind=TARGET_SURVEY,
                           created_by="steward")
    # First firing: neither has had a quality run, so both run.
    _make_due(registry, work_list)
    first = _fire(registry, monkeypatch)
    assert sorted(r["target"]["slug"] for r in _queued(registry)) == [WORKS, PHARMA]
    assert all(r["requested_by"] == "steward" for r in _queued(registry))      # the schedule runs as its creator
    assert all(r["target"]["params"]["publish_as_presser"] is False for r in _queued(registry))
    assert first["schedule"]["last_run_status"] == "ok"
    assert first["activity"]["summary"].endswith(": 2 queued")
    # The runs finish; each leaves its value profile. Then coco_pharma is re-surveyed and has grown.
    _finish_all(registry)
    _quality_run(registry, PHARMA, _at(1), PHARMA_INV)
    _quality_run(registry, WORKS, _at(1), WORKS_INV)
    grown = {**PHARMA_INV, ("public", "drugs"): (125, 81920, PHARMA_INV[("public", "drugs")][2])}
    _inventory(registry, PHARMA, _at(2), grown)
    # Second firing: coco_pharma runs; adventureworks is skipped and the run says so, by name and why.
    _make_due(registry, work_list)
    second = _fire(registry, monkeypatch)
    assert [r["target"]["slug"] for r in _queued(registry)] == [PHARMA]
    summary = second["activity"]["summary"]
    assert summary == (f"Scheduled batch of '{QUALITY_DEFINITION}' across work list '{work_list}': 1 queued · "
                       "1 skipped: adventureworks skipped · unchanged since its last quality run on "
                       "2026-10-20 10:00Z · no new inventory since 2026-10-20 09:00Z")
    detail = json.loads(second["activity"]["detail"])
    assert {m["entity_slug"]: m["outcome"] for m in detail["members"]} == {PHARMA: "queued", WORKS: "skipped"}
    assert second["schedule"]["last_run_status"] == "ok"


def test_a_scheduled_batch_follows_membership(registry, work_list, monkeypatch):
    registry.save_schedule(WORK_LIST_ENTITY, work_list, QUALITY_DEFINITION, "weekly", target_kind=TARGET_SURVEY)
    WorkLists(registry).remove_member(work_list, WORKS)
    _make_due(registry, work_list)
    _fire(registry, monkeypatch)
    assert [r["target"]["slug"] for r in _queued(registry)] == [PHARMA]


def test_a_schedule_whose_list_was_deleted_fails_with_its_words(registry, work_list, monkeypatch):
    registry.save_schedule(WORK_LIST_ENTITY, work_list, QUALITY_DEFINITION, "weekly", target_kind=TARGET_SURVEY)
    WorkLists(registry).delete(work_list)
    _make_due(registry, work_list)
    fired = _fire(registry, monkeypatch)
    assert fired["schedule"]["last_run_status"] == "error"
    assert "not found — schedule may be stale" in fired["activity"]["summary"] + (fired["activity"]["detail"] or "")


def test_a_member_with_its_own_schedule_keeps_it_and_the_list_says_so(registry, work_list, monkeypatch):
    """REPLY-DESIGNER-GROUP-SCHEDULING-AND-FIRST-INDEX-2026-10-19.md §3: the resource's own schedule wins."""
    registry.save_schedule(WORK_LIST_ENTITY, work_list, QUALITY_DEFINITION, "weekly", target_kind=TARGET_SURVEY)
    registry.save_schedule("database", WORKS, QUALITY_DEFINITION, "daily", target_kind=TARGET_SURVEY)
    with registry._conn() as conn:       # only the list's schedule is due; the database's own is not
        conn.execute("UPDATE resource_schedules SET next_run=? WHERE entity_type='database'",
                     ((datetime.now(UTC) + timedelta(days=1)).isoformat(),))
    _make_due(registry, work_list)
    fired = _fire(registry, monkeypatch)
    assert [r["target"]["slug"] for r in _queued(registry)] == [PHARMA]
    assert fired["activity"]["summary"].endswith(
        "1 queued · 1 own schedule: adventureworks own schedule · runs daily on its own, not with this list")
