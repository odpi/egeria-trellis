"""Regression tests for the false-zero publish hotfix (2026-09-30).

A Survey step whose local survey failed (bad credential) still reported "ok";
the executor's publish step then wrote a `database_surveys` row (source
"egeria-published") with 0/0/0 that became the newest "survey" and would have
been pushed to Egeria by the classic Publish route. Rules under test:

1. a publish step never writes a survey row
2. publish reads the latest MEASURED row (one predicate, `is_measured_survey`)
3. false rows are marked invalid (not deleted) by a dry-run-by-default repair
4. the failing adaptive step is "failed" with the real error, and no publish follows
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import (
    DatabaseEntity,
    ProjectRegistry,
    is_false_zero_survey,
    is_measured_survey,
)

SLUG = "coco"
REAL = {"schema_info": {"schemas": [{"name": f"s{i}"} for i in range(8)],
                        "total_tables": 61, "total_columns": 479},
        "statistics": {"x": 1}}
EMPTY = {"schema_info": {}, "statistics": {}, "views": [], "operations": {}}
AUTH_ERR = 'password authentication failed for user "erinoverview"'


def _row(source="local", data=None, invalid_at=None):
    return {"source": source, "survey_data": json.dumps(data if data is not None else REAL),
            "invalid_at": invalid_at}



@pytest.fixture(autouse=True)
def _curation_access_open(monkeypatch):
    """Brief Z round 2: the publish route checks curation access first; here no zone is in use."""
    from tests.zone_fakes import permit_publish_routes

    return permit_publish_routes(monkeypatch)

@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    for slug in (SLUG, "other"):
        r.register_database(DatabaseEntity(
            slug=slug, display_name=slug, db_type="postgresql", host="localhost",
            port=5432, database_name=slug, db_user="u", db_password="p"))
    return r


def _add(r, slug, surveyed_at, source="local", data=None, counts=(8, 61, 479)):
    r.record_database_survey(slug, *counts, data if data is not None else REAL,
                             source=source, surveyed_at=surveyed_at)


def _summary(r, slug=SLUG):
    with r._conn() as c:
        row = c.execute("SELECT schema_count, table_count, column_count, last_surveyed_at "
                        "FROM databases WHERE slug = ?", (slug,)).fetchone()
    return tuple(row[i] for i in range(4))


def _all(r, slug=SLUG):
    return r.get_database_surveys(slug, include_invalid=True)


# ── rule 2: the single predicate ─────────────────────────────────────────────

@pytest.mark.parametrize("row,expected", [
    (_row("local", REAL), True),
    (_row("egeria", REAL), True),
    (_row("egeria-custom", REAL), True),
    (_row("local", EMPTY), False),                       # surveyor, but nothing measured
    (_row("egeria-published", EMPTY), False),            # the false row
    (_row("egeria-published", REAL), False),             # a publish is never evidence
    (_row("local", REAL, invalid_at="2026-09-30"), False),
    (_row("local", {}), False),                          # no schema_info key at all
    ({"source": "local", "survey_data": "not json"}, False),
    (None, False),
])
def test_is_measured_survey_table(row, expected):
    assert is_measured_survey(row) is expected


def test_is_false_zero_is_keyed_on_the_damage():
    assert is_false_zero_survey(_row("egeria-published", EMPTY))
    assert not is_false_zero_survey(_row("egeria-published", REAL))
    assert not is_false_zero_survey(_row("local", EMPTY))


def test_latest_measured_skips_newer_empty_rows(registry):
    _add(registry, SLUG, "2026-09-28T03:00:00")                        # real, older
    _add(registry, SLUG, "2026-09-30T12:00:00", "egeria-published", EMPTY, (0, 0, 0))
    _add(registry, SLUG, "2026-09-30T12:05:00", "local", EMPTY, (0, 0, 0))
    # the plain "latest" is the empty row; the measured one is the older, real one
    assert registry.get_latest_database_survey(SLUG)["surveyed_at"] == "2026-09-30T12:05:00"
    m = registry.latest_measured_database_survey(SLUG)
    assert m["surveyed_at"] == "2026-09-28T03:00:00" and m["table_count"] == 61


def test_latest_measured_none_when_nothing_measured(registry):
    _add(registry, SLUG, "2026-09-30T12:00:00", "egeria-published", EMPTY, (0, 0, 0))
    assert registry.latest_measured_database_survey(SLUG) is None


# ── rule 3: mark, do not delete; repair is dry-run by default ────────────────

def _damage(registry):
    _add(registry, SLUG, "2026-09-28T03:00:00")
    for t in ("2026-09-30T12:17:19", "2026-09-30T12:18:57"):
        _add(registry, SLUG, t, "egeria-published", EMPTY, (0, 0, 0))
    _add(registry, "other", "2026-09-30T13:00:00", "egeria-published", EMPTY, (0, 0, 0))


def test_dry_run_plans_any_slug_and_writes_nothing(registry):
    from resource_explorer.repair import format_false_zero_plan, plan_false_zero_survey_repair

    _damage(registry)
    plan = plan_false_zero_survey_repair(registry)
    assert [(p["slug"], p["surveyed_at"]) for p in plan] == [
        (SLUG, "2026-09-30T12:17:19"), (SLUG, "2026-09-30T12:18:57"),
        ("other", "2026-09-30T13:00:00")]
    assert all("no measurement: publish step ran without a schema step" in p["reason"]
               for p in plan)
    text = format_false_zero_plan(plan, apply=False)
    assert text.startswith("WOULD MARK INVALID (dry run, nothing written): 3 row(s)")
    assert "slugs: coco, other" in text
    assert "source=egeria-published" in text
    assert all(r.get("invalid_at") is None for r in _all(registry))       # nothing written


def test_apply_marks_but_never_deletes_and_excludes_everywhere(registry):
    from resource_explorer.repair import (apply_false_zero_survey_repair,
                                          format_false_zero_plan, plan_false_zero_survey_repair)

    _damage(registry)
    # the false rows overwrote the summary on the databases row
    assert _summary(registry)[1] == 0
    plan = plan_false_zero_survey_repair(registry)
    assert apply_false_zero_survey_repair(plan, registry) == 3
    assert format_false_zero_plan(plan, apply=True).startswith("MARKED INVALID: 3 row(s)")

    assert len(_all(registry)) == 3                                        # still in the table
    marked = [r for r in _all(registry) if r["invalid_at"]]
    assert len(marked) == 2 and all(r["invalid_reason"] for r in marked)
    # excluded from latest / history / last-surveyed
    assert [s["surveyed_at"] for s in registry.get_database_surveys(SLUG)] == ["2026-09-28T03:00:00"]
    assert registry.get_latest_database_survey(SLUG)["table_count"] == 61
    assert _summary(registry) == (8, 61, 479, "2026-09-28T03:00:00")
    assert registry.find_latest_database_survey_with_key(SLUG, "schema_info")["table_count"] == 61
    # idempotent: second plan is empty, second apply marks nothing
    assert plan_false_zero_survey_repair(registry) == []


def test_cache_is_invalidated_by_marking(registry):
    _damage(registry)
    assert len(registry.get_database_surveys(SLUG)) == 3                   # warm the cache
    for p in registry.find_false_zero_database_surveys():
        registry.mark_database_survey_invalid(p["database_slug"], p["surveyed_at"],
                                              p["source"], "x")
    assert len(registry.get_database_surveys(SLUG)) == 1


def test_cli_command_is_dry_run_by_default(registry, monkeypatch):
    from typer.testing import CliRunner
    from resource_explorer.cli.main import app

    _damage(registry)
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    res = CliRunner().invoke(app, ["repair", "mark-false-zero-surveys"])
    assert res.exit_code == 0, res.output
    assert "WOULD MARK INVALID (dry run, nothing written): 3 row(s)" in res.output
    assert all(r.get("invalid_at") is None for r in _all(registry) + _all(registry, "other"))
    res = CliRunner().invoke(app, ["repair", "mark-false-zero-surveys", "--apply"])
    assert "MARKED INVALID: 3 row(s)" in res.output
    assert len([r for r in _all(registry) + _all(registry, "other") if r["invalid_at"]]) == 3


# ── rule 1: a publish writes no survey row ───────────────────────────────────

def _publisher_patches():
    from resource_explorer.surveyors.database import egeria_database_surveyor as m
    cls = m.EgeriaDatabaseSurveyor
    return (patch.object(cls, "connect"),
            patch.object(cls, "_find_by_qualified_name", return_value="db-guid"),
            patch.object(cls, "_create_annotations"),
            patch.object(cls, "_publish_column_lineage"))


def test_publish_step_annotations_writes_no_survey_row(registry):
    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor

    _add(registry, SLUG, "2026-09-28T03:00:00")
    before = len(_all(registry))
    p1, p2, p3, p4 = _publisher_patches()
    with p1, p2, p3, p4, patch(
            "resource_explorer.surveyors.database.database_surveyor.DatabaseSurveyor."
            "_create_schema_annotations", return_value=[]):
        s = EgeriaDatabaseSurveyor.__new__(EgeriaDatabaseSurveyor)
        s._asset_maker = MagicMock()
        s._asset_maker.create_asset.return_value = "report-1"
        out = s.publish_step_annotations(registry.get_database(SLUG), REAL["schema_info"], {},
                                         "2026-09-30T12:00:00", registry)
    assert out["report_guid"] == "report-1"
    assert len(_all(registry)) == before


def test_publish_local_survey_writes_no_row_and_stamps_the_measured_row(registry):
    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor

    _add(registry, SLUG, "2026-09-28T03:00:00")
    registry.record_database_survey_published(SLUG, "2026-09-28T03:00:00", "guid-9")
    row = _all(registry)[0]
    assert row["egeria_report_guid"] == "guid-9" and row["published_at"]
    assert len(_all(registry)) == 1
    # a publish-sourced row is never stamped (no such row can be "the measured one")
    _add(registry, SLUG, "2026-09-30T00:00:00", "egeria-published", EMPTY, (0, 0, 0))
    assert registry.record_database_survey_published(SLUG, "2026-09-30T00:00:00", "g") == 0
    assert EgeriaDatabaseSurveyor  # imported for the reader's benefit


def test_step_publish_with_no_schema_step_reads_the_measured_row_and_adds_no_row(registry):
    from resource_explorer.surveyors.database import survey_definition_adapter as ad

    _add(registry, SLUG, "2026-09-28T03:00:00")
    _add(registry, SLUG, "2026-09-30T12:00:00", "egeria-published", EMPTY, (0, 0, 0))
    before = len(_all(registry))
    seen = {}

    def fake_publish(self, entity, schema_info, statistics, surveyed_at, registry, **kw):
        seen["schema_info"] = schema_info
        return {"report_guid": "rg-1", "annotation_count": 3}

    with patch("resource_explorer.surveyors.database.egeria_database_surveyor."
               "EgeriaDatabaseSurveyor.publish_step_annotations", fake_publish), \
         patch("resource_explorer.surveyors.database.egeria_database_surveyor."
               "EgeriaDatabaseSurveyor.__init__", lambda self: None):
        for _ in range(2):        # "running that control again does NOT add another row"
            guid = ad._publish(registry.get_database(SLUG), [], "2026-09-30T13:00:00", registry)
    assert guid == "rg-1"
    assert seen["schema_info"]["total_tables"] == 61                      # the REAL 8/61/479
    assert len(_all(registry)) == before                                  # no row, twice
    runs = registry.query_step_runs(SLUG, step_key="egeria_publish")
    assert len(runs) == 2 and runs[0]["metrics"]["report_guid"] == "rg-1"
    assert registry.latest_measured_database_survey(SLUG)["egeria_report_guid"] == "rg-1"


def test_step_publish_with_nothing_measured_raises_and_publishes_nothing(registry):
    from resource_explorer.surveyors.database import survey_definition_adapter as ad
    from resource_explorer.surveyors.database.egeria_database_surveyor import (
        EgeriaDatabaseSurveyorError)

    _add(registry, SLUG, "2026-09-30T12:00:00", "egeria-published", EMPTY, (0, 0, 0))
    called = MagicMock()
    with patch("resource_explorer.surveyors.database.egeria_database_surveyor."
               "EgeriaDatabaseSurveyor.publish_step_annotations", called), \
         patch("resource_explorer.surveyors.database.egeria_database_surveyor."
               "EgeriaDatabaseSurveyor.__init__", lambda self: None):
        with pytest.raises(EgeriaDatabaseSurveyorError, match="Nothing measured"):
            ad._publish(registry.get_database(SLUG), [], "2026-09-30T13:00:00", registry)
    called.assert_not_called()
    assert len(_all(registry)) == 1


# ── rule 4 (swallowed error): failed status, real text, no publish after ─────

HYBRID = ("resource_explorer.surveyors.database.hybrid_database_surveyor."
          "HybridDatabaseSurveyor.survey")
FAILED_LOCAL = {"source": "egeria", "database_slug": SLUG, "surveyed_at": "2026-09-30T12:16:56",
                "errors": [f"Custom survey failed: connection to server ... {AUTH_ERR}"],
                "engine_action_guid": "ea-1",
                "message": "Egeria survey triggered. Local survey has errors; displaying local schema info."}


def test_adaptive_handler_reports_failed_with_the_real_error():
    from resource_explorer.surveyors.database.survey_definition_adapter import _run_egeria_adaptive

    step = MagicMock(re_analysis_step="postgres_schema_and_stats", qualified_name="q")
    with patch(HYBRID, return_value=dict(FAILED_LOCAL)):
        out = _run_egeria_adaptive(MagicMock(slug=SLUG), MagicMock(), step, db_user="u", db_pwd="p")
    assert out["status"] == "failed"
    assert out["errors"] and AUTH_ERR in out["errors"][0]


def test_failing_survey_then_no_publish_step_and_no_row(registry, monkeypatch):
    from resource_explorer.surveyors.survey_definition_executor import SurveyDefinitionExecutor

    monkeypatch.setattr(registry, "has_assigned_egeria_project", lambda *a, **k: True)
    publish = MagicMock()
    with patch(HYBRID, return_value=dict(FAILED_LOCAL)), patch(
            "resource_explorer.surveyors.database.egeria_database_surveyor."
            "EgeriaDatabaseSurveyor.publish_step_annotations", publish), patch(
            "resource_explorer.surveyors.database.survey_definition_adapter._publish") as pub:
        res = SurveyDefinitionExecutor(registry).run_synthetic_step(
            entity_type="database", slug=SLUG, re_analysis_step="postgres_schema_and_stats",
            executes_at="egeria-adaptive", db_user="u", db_pwd="p")
    assert res["steps"][0]["status"] == "failed"
    assert any(AUTH_ERR in e for e in res["errors"])
    assert not res.get("published")
    publish.assert_not_called()
    pub.assert_not_called()
    assert _all(registry) == []


def test_classic_survey_route_shows_the_auth_failure_and_writes_no_row(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setattr(registry, "has_assigned_egeria_project", lambda *a, **k: True)
    from resource_explorer.web.app import app

    with patch(HYBRID, return_value=dict(FAILED_LOCAL)):
        r = TestClient(app).post(f"/api/databases/{SLUG}/survey",
                                 json={"use_egeria": True, "refresh": True})
    body = r.json()
    assert body["status"] == "error"
    assert AUTH_ERR in body["error"]
    assert _all(registry) == []


# ── rule 2 at the classic Publish route ──────────────────────────────────────

def test_publish_route_pushes_the_measured_counts_not_the_newest_empty_row(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    from resource_explorer.web.app import app

    _add(registry, SLUG, "2026-09-28T03:00:00")
    _add(registry, SLUG, "2026-09-30T12:00:00", "egeria-published", EMPTY, (0, 0, 0))
    got = {}

    def fake(self, **kw):
        got.update(kw)
        return {"server_guid": "s", "asset_guid": "a", "report_guid": "r", "annotation_count": 1}

    with patch("resource_explorer.surveyors.database.egeria_database_surveyor."
               "EgeriaDatabaseSurveyor.publish_local_survey", fake), patch(
            "resource_explorer.surveyors.database.egeria_database_surveyor."
            "EgeriaDatabaseSurveyor.__init__", lambda self, **k: None):
        from resource_explorer.auth import create_access_token

        # Brief I: Publish runs as a signed-in user with an Egeria token.
        token = create_access_token(user_id="dan", egeria_token="tok-dan")
        r = TestClient(app, headers={"Authorization": f"Bearer {token}"}).post(
            f"/api/databases/{SLUG}/publish", json={})
    assert r.status_code == 200, r.text
    assert (got["schema_count"], got["table_count"], got["column_count"]) == (8, 61, 479)
    assert got["surveyed_at"] == "2026-09-28T03:00:00"


def test_publish_route_refuses_when_nothing_is_measured(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    from resource_explorer.web.app import app

    _add(registry, SLUG, "2026-09-30T12:00:00", "egeria-published", EMPTY, (0, 0, 0))
    r = TestClient(app).post(f"/api/databases/{SLUG}/publish", json={})
    assert r.status_code == 404 and "measured" in r.text


# ── classic modal wording (the only control on this path) ────────────────────

def test_classic_survey_modal_confirms_the_publish_before_running():
    html = (Path(__file__).resolve().parent.parent / "resource_explorer" / "web" / "static"
            / "index.html").read_text()
    body = html[html.index("async function submitSurveyDb()"):]
    body = body[:body.index("Only require credentials")]
    assert "window.confirm" in body and "useEgeria" in body
    assert "postgres_schema_and_stats" in body and "publishes to Egeria" in body
