"""The per-card Run for a database analysis provided by an Analytic Library step.

Seen live 2026-10-20: the Database Staleness card offered an enabled `Run →` and the route refused it ("no local
survey step(s) mapped"), because the button (`stage_page.runnable_and_reason`) and the route
(`database_analysis_has_runner`) were two predicates. They are one now; these tests hold that, and hold that the
route runs the step through the executor that runs one inside a survey, with no stored credential.
"""
from __future__ import annotations

import inspect
import json

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import DatabaseEntity, ProjectRegistry


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    # No db_user / db_password: a library step must not need them.
    r.register_database(DatabaseEntity(
        slug="nocreds", display_name="No Creds", db_type="postgresql",
        host="localhost", port=5432, database_name="nocreds"))
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    from resource_explorer.web.app import app
    return TestClient(app)


from tests.library_step_install import library_installed  # noqa: E402,F401 - the fixture


def _library_ids() -> list[str]:
    from resource_explorer.surveyors.analysis_catalog_reader import get_analyses

    return [a["id"] for a in get_analyses("database", include_egeria_live=False)
            if (a.get("provided_by") or {}).get("kind") == "step"]


def test_the_catalog_has_library_steps_to_test():
    assert {"change_rates", "database_staleness", "hub_tables", "statistics_freshness"} <= set(_library_ids())


def test_the_button_and_the_route_agree_for_every_database_catalog_id():
    """A loop over the whole catalog, so a new library step (or any id) cannot drift between the two."""
    from resource_explorer.surveyors.analysis_catalog_reader import get_analyses
    from resource_explorer.surveyors.database.database_surveyor import database_analysis_has_runner
    from resource_explorer.workflows.stage_page import _credential_use, runnable_and_reason

    ids = [a["id"] for a in get_analyses("database", include_egeria_live=False)]
    assert ids
    for analysis_id in ids:
        runnable, reason = runnable_and_reason(analysis_id, "database")
        # A library step whose package is absent has a runner (the route dispatches it) but its card's Run is
        # disabled with the "not installed here" words, before a press.
        absent = analysis_id in _library_ids() and not runnable
        assert runnable == (database_analysis_has_runner(analysis_id) and not absent), analysis_id
        assert bool(reason) == (not runnable), analysis_id
        if absent:
            assert reason.startswith("step not installed here"), analysis_id
        # The credential words say "stored" only for a run that opens a connection.
        assert (_credential_use("database", analysis_id) != "") == database_analysis_has_runner(analysis_id), analysis_id


def test_an_installed_library_steps_card_is_runnable_with_no_credential(library_installed):
    from resource_explorer.workflows.stage_page import _credential_use, runnable_and_reason

    for analysis_id in _library_ids():
        assert runnable_and_reason(analysis_id, "database") == (True, ""), analysis_id
        assert _credential_use("database", analysis_id) == "none"


def test_the_route_never_refuses_what_the_button_offers(client, registry):
    """For every id the button offers, the route does not 400 on the analysis (a credential-less database
    is refused only where the run really needs a credential)."""
    from resource_explorer.surveyors.analysis_catalog_reader import get_analyses
    from resource_explorer.workflows.stage_page import _credential_use, runnable_and_reason

    for a in get_analyses("database", include_egeria_live=False):
        if not runnable_and_reason(a["id"], "database")[0]:
            continue
        resp = client.post(f"/api/databases/nocreds/analyses/{a['id']}/run")
        if _credential_use("database", a["id"]) == "stored":
            assert resp.status_code == 400 and "No stored database credentials" in resp.json()["detail"], a["id"]
        else:
            assert resp.status_code == 200, (a["id"], resp.text)


def _fake_library_step(calls):
    """A fake bound to the real runner's signature (SurveyStepHost.__call__ without self)."""
    from resource_explorer.surveyors.survey_step_host import SurveyStepHost

    def fake(entity, registry, **runner_kwargs):
        calls.append((entity.slug, sorted(runner_kwargs)))
        return {"step_annotations": [{"scope": "", "annotation_type": "ResourceMeasureAnnotation"}] * 2,
                "annotation_count": 2, "state_counts": {"measured": 2},
                "producer": {"step": "database_staleness", "version": "0", "package": "fake"}}

    real = list(inspect.signature(SurveyStepHost.__call__).parameters.values())[1:]
    assert [p.name for p in inspect.signature(fake).parameters.values()] == [p.name for p in real]
    return fake


def test_pressing_run_on_database_staleness_runs_that_one_step_and_records_it(client, registry, monkeypatch):
    from resource_explorer.surveyors.survey_definition_executor import get_adapter

    calls: list = []
    monkeypatch.setitem(get_adapter("database").re_analysis_steps, "database_staleness", _fake_library_step(calls))

    resp = client.post("/api/databases/nocreds/analyses/database_staleness/run")
    assert resp.status_code == 200, resp.text          # no credential demanded
    started = resp.json()
    assert started["status"] == "started" and started["activity_id"] and started["run_id"]

    # The worker's side of the same run.
    from resource_explorer.workflows.analysis import execute_and_record_database_analysis

    result = execute_and_record_database_analysis("nocreds", "database_staleness", started["activity_id"],
                                                  registry=registry)
    assert result.status == "ok" and result.summary == "2 annotation(s)."
    assert calls == [("nocreds", ["scan_surveyed_at"])]  # no db_user / db_pwd reached the step

    entry = client.get(f"/api/activity/{started['activity_id']}").json()
    assert entry["status"] == "ok"
    assert json.loads(entry["detail"])["analysis_id"] == "database_staleness"

    [step_run] = [r for r in registry.query_step_runs(entity_type="database", slug="nocreds")
                  if r["step_key"] == "database_staleness"]
    assert step_run["executor"] == "local"

    # The card's "last run" reads it like a survey-run one.
    last = registry.get_analysis_last_run("database", "nocreds")["database_staleness"]
    assert last["last_run_status"] == "ok"
    assert registry.list_runs(state="queued")[0]["kind"] == "database_analysis_run"


def test_a_library_step_whose_package_is_not_installed_says_so_never_ok(registry, monkeypatch):
    from resource_explorer.surveyors import survey_step_host
    from resource_explorer.workflows.analysis import run_database_analysis

    monkeypatch.setattr(survey_step_host, "discover_steps", lambda entity_type: {})
    survey_step_host.refresh("database")
    try:
        result = run_database_analysis("nocreds", "database_staleness", registry=registry)
    finally:
        monkeypatch.undo()
        survey_step_host.refresh("database")
    assert result.status == "error"
    assert "step not installed here" in result.error


def test_a_step_that_raises_is_an_error_not_an_ok(registry, monkeypatch):
    from resource_explorer.surveyors.survey_definition_executor import get_adapter
    from resource_explorer.workflows.analysis import run_database_analysis

    def boom(entity, registry, **runner_kwargs):
        raise RuntimeError("the step blew up")

    monkeypatch.setitem(get_adapter("database").re_analysis_steps, "database_staleness", boom)
    result = run_database_analysis("nocreds", "database_staleness", registry=registry)
    assert result.status == "error" and "the step blew up" in result.error


def test_a_typed_credential_for_a_library_step_is_refused_as_for_db_derived(client, registry):
    resp = client.post("/api/databases/nocreds/analyses/database_staleness/run",
                       json={"db_user": "u", "db_pwd": "p"})
    assert resp.status_code == 400
    assert "opens no connection" in resp.json()["detail"]
