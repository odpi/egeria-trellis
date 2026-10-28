"""The host side of a user step (extensibility brief 2c): RE's `SurveyStepHost`, the generated catalog entry it
attaches beside DATABASE_STEP_REGISTRY, and the generic reader for ResourceMeasureAnnotation (R7's first
instance) that the Analysis pane reads through.

These run without the step's package installed, which is this suite's normal state: the catalog entry is
committed, the package is not. The end-to-end run with it installed is in trellis-analytics'
tests/test_database_staleness_step.py.
"""
import pytest

from resource_explorer.registry import DatabaseEntity, ProjectRegistry


@pytest.fixture
def reg(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "reg.sqlite"))
    r.register_database(DatabaseEntity(slug="shop", display_name="shop", db_type="postgresql",
                                       host="localhost", port=5432, database_name="shop"))
    return r


def _env(state, reason="", run="run-1"):
    return {"resultState": state, "resultStateReason": reason, "producingRun": run,
            "measuredAt": "2026-10-13T00:00:00Z", "producerVersion": "0.1.0", "scope": ""}


def _ann(scope, state, *, reason="", rows_changed=None, run="run-1"):
    props = {} if rows_changed is None else {"rowsChanged": str(rows_changed)}
    return {"scope": scope, "annotation_type": "ResourceMeasureAnnotation", "summary": f"{scope}: {state}",
            "explanation": reason, "resourceProperties": props,
            "additionalProperties": {**_env(state, reason, run), "scope": scope}}


def test_the_generated_entry_is_attached_beside_the_step_registry_even_when_not_installed():
    from resource_explorer.surveyors.database.survey_definition_adapter import (
        DATABASE_ANALYSIS_KINDS, DATABASE_ANALYSIS_RE_STEP_MAP, DATABASE_STEP_REGISTRY)
    from resource_explorer.surveyors.survey_definition_executor import get_adapter

    assert DATABASE_ANALYSIS_RE_STEP_MAP["database_staleness"] == ["database_staleness"]
    assert "database_staleness" in DATABASE_STEP_REGISTRY
    assert DATABASE_ANALYSIS_KINDS["database_staleness"].results.live_read is True
    assert "database_staleness" in get_adapter("database").re_analysis_steps


@pytest.fixture
def not_installed(monkeypatch):
    """The step's package absent, whatever this environment has; put back as it really is afterwards."""
    from resource_explorer.surveyors import survey_step_host

    monkeypatch.setattr(survey_step_host, "discover_steps", lambda entity_type: {})
    survey_step_host.refresh("database")
    yield
    monkeypatch.undo()
    survey_step_host.refresh("database")


def test_a_step_not_installed_here_says_so_with_the_package_name(reg, not_installed):
    from resource_explorer.surveyors import survey_step_host
    from resource_explorer.surveyors.survey_definition_executor import SurveyDefinitionExecutor, get_adapter

    runner = get_adapter("database").re_analysis_steps["database_staleness"]
    assert isinstance(runner, survey_step_host.NotInstalledStep)
    out = SurveyDefinitionExecutor(reg).run_synthetic_step(
        "database", "shop", "database_staleness", "resource-explorer", engine_override="resource-explorer")
    [entry] = [s for s in out["steps"] if s["re_analysis_step"] == "database_staleness"]
    assert entry["status"] == "error"
    assert any("step not installed here" in e and "trellis-step-database-staleness" in e for e in out["errors"])


def test_the_step_annotations_table_is_created_by_schema_init(reg):
    with reg._conn() as conn:
        cols = reg._get_table_columns(conn, "step_annotations")
    assert {"entity_type", "slug", "step_key", "package", "producer_version", "run_id", "annotation_type",
            "scope", "result_state", "detail_json", "created_at"} <= set(cols)
    # a second schema init over the same file is a no-op (IF NOT EXISTS)
    ProjectRegistry(db_path=reg.database_url.removeprefix("sqlite:///"))


def test_step_annotations_round_trip_and_the_latest_run_is_read(reg):
    reg.record_step_annotations("database", "shop", "database_staleness", run="run-1",
                                measured_at="2026-10-01T00:00:00Z", package="pkg", producer_version="0.1.0",
                                annotations=[_ann("public.orders", "measured", rows_changed=3, run="run-1")])
    reg.record_step_annotations("database", "shop", "database_staleness", run="run-2",
                                measured_at="2026-10-08T00:00:00Z", package="pkg", producer_version="0.1.0",
                                annotations=[_ann("public.orders", "nothing_found", rows_changed=0, run="run-2"),
                                             _ann("public.v", "not_applicable", reason="a view", run="run-2")])
    run, rows = reg.query_step_annotations("database", "shop", "database_staleness")
    assert run == "run-2" and [r["scope"] for r in rows] == ["public.orders", "public.v"]
    first = rows[0]
    assert (first["package"], first["producer_version"], first["run_id"]) == ("pkg", "0.1.0", "run-2")
    assert first["result_state"] == "nothing_found"
    assert first["detail"]["additionalProperties"]["resultState"] == "nothing_found"
    assert first["detail"]["resourceProperties"] == {"rowsChanged": "0"}


def test_a_rerun_of_the_same_run_writes_no_duplicates(reg):
    args = dict(run="run-2", measured_at="2026-10-08T00:00:00Z")
    anns = [_ann("public.orders", "nothing_found", rows_changed=0, run="run-2"),
            _ann("public.v", "not_applicable", reason="a view", run="run-2")]
    assert reg.record_step_annotations("database", "shop", "s", annotations=anns, **args) == 2
    assert reg.record_step_annotations("database", "shop", "s", annotations=anns[:1], **args) == 2
    assert len(reg.query_step_annotations("database", "shop", "s")[1]) == 2


def test_step_annotations_filter_by_state(reg):
    reg.record_step_annotations("database", "shop", "s", run="r1", measured_at="2026-10-01T00:00:00Z", annotations=[
        _ann("a.t1", "measured", rows_changed=1), _ann("a.t2", "nothing_found", rows_changed=0),
        _ann("a.t3", "measured", rows_changed=9)])
    assert [r["scope"] for r in reg.query_step_annotations("database", "shop", "s", state="measured")[1]] == [
        "a.t1", "a.t3"]
    assert reg.query_step_annotations("database", "shop", "s", state="not_applicable") == ("r1", [])


def test_native_survey_readers_never_see_step_rows(reg):
    reg.record_step_annotations("database", "shop", "s", run="r1", measured_at="2026-10-01T00:00:00Z",
                                annotations=[_ann("a.t1", "measured", rows_changed=1)])
    assert reg.list_native_survey_runs("database", "shop") == []
    with reg._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM native_survey_annotations").fetchone()[0] == 0


def test_the_run_id_is_the_link_to_the_step_runs_row(reg):
    """The step_runs row is written after the step returns; its surveyed_at is the run id the rows carry."""
    reg.record_step_run("shop", "s", "2026-10-08T00:00:00", entity_type="database", metrics={"wall_ms": 1})
    reg.record_step_annotations("database", "shop", "s", run="2026-10-08T00:00:00",
                                measured_at="2026-10-08T00:00:01Z", annotations=[_ann("a.t1", "measured")])
    run, _ = reg.query_step_annotations("database", "shop", "s")
    assert reg.query_step_runs(slug="shop", step_key="s", surveyed_at=run, entity_type="database")


def test_the_reader_derives_the_state_word_from_the_envelope_never_from_the_value(reg):
    """Brief section 5: change the value, not the state, and the word does not change."""
    from resource_explorer.surveyors.annotation_readers import measurement_rows

    reg.record_step_annotations("database", "shop", "s", run="r1", measured_at="2026-10-01T00:00:00Z", annotations=[
        _ann("a.t1", "measured", rows_changed=0),            # a zero value, measured: still "measured"
        _ann("a.t2", "nothing_found", rows_changed=500),    # a big value, nothing found: still "nothing found"
        _ann("a.t3", "not_established", reason="only one survey has counted rows"),
    ])
    rows = {r["name"]: r for r in measurement_rows(reg, "database", "shop", "s", "ResourceMeasureAnnotation")}
    assert rows["a.t1"]["state_words"] == "measured" and rows["a.t2"]["state_words"] == "nothing found"
    assert rows["a.t3"]["state_words"] == "not established"
    assert rows["a.t3"]["reason"] == "only one survey has counted rows"


def test_a_stored_row_without_an_envelope_says_so_and_is_never_given_a_state_from_its_value(reg):
    from resource_explorer.surveyors.annotation_readers import measurement_rows

    bare = _ann("a.t", "measured", rows_changed=4)
    bare["additionalProperties"] = {}
    reg.record_step_annotations("database", "shop", "s", run="r1", measured_at="2026-10-01T00:00:00Z",
                                annotations=[bare])
    [row] = measurement_rows(reg, "database", "shop", "s", "ResourceMeasureAnnotation")
    assert row["state"] == "" and row["state_words"] == "no envelope"


def test_build_measurements_shows_the_rows_with_their_state_words(reg):
    from resource_explorer.workflows.stage_page import build_measurements

    reg.record_step_annotations("database", "shop", "database_staleness", run="r1",
                                measured_at="2026-10-01T00:00:00Z", annotations=[
                                    _ann("public.orders", "measured", rows_changed=30),
                                    _ann("public.v", "not_applicable", reason="a view holds no rows of its own")])
    m = build_measurements(reg, "shop", "database_staleness", entity_type="database")
    assert [(r["name"], r["state_words"]) for r in m["measurements"]] == [
        ("public.orders", "measured"), ("public.v", "not applicable")]
    assert m["not_applicable"] is False


def test_build_measurements_before_any_run_says_not_run_not_zero(reg):
    from resource_explorer.workflows.stage_page import build_measurements

    m = build_measurements(reg, "shop", "database_staleness", entity_type="database")
    assert m["measurements"] == [] and "has not recorded" in m["reason"]


def test_the_analysis_pane_renders_the_state_words_with_static_classes():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next" / "app.js").read_text()
    start = js.index("async function toggleMeasurementsInPlace")
    body = js[start:js.index("\n}\n", start)]
    assert "m.state_words" in body and "m.reason" in body
