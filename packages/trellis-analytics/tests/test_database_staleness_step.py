"""Brief 2c: the user step `database_staleness`, an installable package outside RE
(`examples/steps/database_staleness/`), run by RE through its orchestrator.

`staleness_installed` makes the package installed the way pip would (a dist-info carrying the entry points its
own pyproject declares, and its source on the path), so the entry point under test is the declared one.
"""
import re
import tomllib
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

from trellis_analytics.envelope import Envelope

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "steps" / "database_staleness"
REPO = ROOT.parents[1]
RE_PKG = REPO / "packages" / "resource-explorer"
CATALOG = RE_PKG / "resource_explorer" / "configdata" / "analysis_catalog.yaml"
DECLARATION = RE_PKG / "docs" / "dr-egeria" / "engine-host" / "library" / "database_staleness.md"
DECLARATION_STEPS = DECLARATION.with_name("database_staleness.pyegeria-steps.json")


@pytest.fixture
def reattach_after():
    """Requested before anything that changes sys.path, so its teardown runs after theirs: RE's attached steps
    go back to what this environment really has installed, and no other test sees this one's install."""
    yield
    from resource_explorer.surveyors import survey_step_host
    survey_step_host.refresh("database")


@pytest.fixture
def staleness_installed(tmp_path, monkeypatch):
    project = tomllib.loads((EXAMPLE / "pyproject.toml").read_text())["project"]
    eps = project["entry-points"]["trellis.analytics"]
    version = re.search(r'__version__ = "([^"]+)"',
                        (EXAMPLE / "src" / "database_staleness" / "__init__.py").read_text()).group(1)
    d = tmp_path / f"trellis_step_database_staleness-{version}.dist-info"
    d.mkdir()
    (d / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {project['name']}\nVersion: {version}\n")
    (d / "entry_points.txt").write_text("[trellis.analytics]\n" + "".join(f"{k} = {v}\n" for k, v in eps.items()))
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.syspath_prepend(str(EXAMPLE / "src"))
    return version


def _survey(reg, slug, at, tables, *, invalid=False):
    """One stored survey: a database_surveys row and its database_tables rows.
    tables: (schema, table, table_type, row_count)."""
    reg.record_database_survey(slug, 1, len(tables), 0, {}, surveyed_at=at)
    reg.write_detail_rows("database_tables", slug, at, rows=[
        {"schema_name": s, "table_name": t, "table_type": tt, "row_count": n, "column_count": 1}
        for s, t, tt, n in tables])
    if invalid:
        import sqlite3
        c = sqlite3.connect(reg.database_url.removeprefix("sqlite:///"))
        c.execute("UPDATE database_surveys SET invalid_at = ?, invalid_reason = 'test' WHERE surveyed_at = ?",
                  (at, at))
        c.commit()
        c.close()


def _run_step(url, slug="shop"):
    from trellis_analytics.discovery import discover
    from trellis_analytics.readers.ods import OdsReader
    from trellis_analytics.step import HostContext, SurveyStep

    m = discover()["database_staleness"]
    host = HostContext(resource_type="database", slug=slug, producing_run="run-x", ods=OdsReader(url))
    return {a.scope: a for a in SurveyStep(m).run(host)}


# ── the four states, from four fixtures, plus a view ──

def test_measured_two_snapshots_with_counts_present(staleness_installed, make_registry):
    reg, url = make_registry("shop")
    _survey(reg, "shop", "2026-10-01T00:00:00", [("public", "orders", "BASE TABLE", 100)])
    _survey(reg, "shop", "2026-10-08T00:00:00", [("public", "orders", "BASE TABLE", 130)])
    a = _run_step(url)["public.orders"]
    assert a.envelope.result_state.value == "measured"
    assert a.resource_properties["rowsChanged"] == "30"
    assert (a.resource_properties["rowsBefore"], a.resource_properties["rowsAfter"]) == ("100", "130")
    assert a.resource_properties["previousSurvey"] == "2026-10-01T00:00:00"


def test_nothing_found_two_snapshots_no_change(staleness_installed, make_registry):
    reg, url = make_registry("shop")
    _survey(reg, "shop", "2026-10-01T00:00:00", [("public", "orders", "BASE TABLE", 100)])
    _survey(reg, "shop", "2026-10-08T00:00:00", [("public", "orders", "BASE TABLE", 100)])
    a = _run_step(url)["public.orders"]
    assert a.envelope.result_state.value == "nothing_found"
    assert a.resource_properties["rowsChanged"] == "0"


def test_not_established_with_only_one_snapshot_names_the_reason(staleness_installed, make_registry):
    reg, url = make_registry("shop")
    _survey(reg, "shop", "2026-10-01T00:00:00", [("public", "orders", "BASE TABLE", 100)])
    # a survey marked invalid is not a snapshot: still only one
    _survey(reg, "shop", "2026-10-08T00:00:00", [("public", "orders", "BASE TABLE", 999)], invalid=True)
    a = _run_step(url)["public.orders"]
    assert a.envelope.result_state.value == "not_established"
    assert "one survey" in a.envelope.result_state_reason
    assert "rowsChanged" not in a.resource_properties


def test_not_established_when_counts_are_unmeasured_names_which_survey(staleness_installed, make_registry):
    reg, url = make_registry("shop")
    _survey(reg, "shop", "2026-10-01T00:00:00", [("public", "orders", "BASE TABLE", 100)])
    _survey(reg, "shop", "2026-10-08T00:00:00", [("public", "orders", "BASE TABLE", None)])
    a = _run_step(url)["public.orders"]
    assert a.envelope.result_state.value == "not_established"
    assert "not measured" in a.envelope.result_state_reason and "2026-10-08" in a.envelope.result_state_reason


def test_a_view_is_not_applicable(staleness_installed, make_registry):
    reg, url = make_registry("shop")
    for at, n in (("2026-10-01T00:00:00", None), ("2026-10-08T00:00:00", None)):
        _survey(reg, "shop", at, [("public", "orders", "BASE TABLE", 5), ("public", "v_sales", "VIEW", n)])
    a = _run_step(url)["public.v_sales"]
    assert a.envelope.result_state.value == "not_applicable"
    assert "view" in a.envelope.result_state_reason


def test_never_surveyed_is_one_not_established_for_the_database(staleness_installed, make_registry):
    reg, url = make_registry("shop")
    anns = _run_step(url)
    assert list(anns) == ["shop"]
    assert anns["shop"].envelope.result_state.value == "not_established"
    assert "no survey" in anns["shop"].envelope.result_state_reason


# ── engine host step 4: two measures, kept distinct (design ruling) ──
# Staleness keeps its definition (two stored snapshots); the survey report the host hands in (`source_report`) adds
# a second, separate measure, "writes since statistics reset", and never stands in for staleness.

REPORT_GUID = "cb92eedc-5e24-4da7-ba5e-fc11d1707d8f"


def _table_measurement(qualified, table_type="Table", inserted="0", updated="0", deleted="0"):
    """One native PostgreSQLSurvey table measurement, as RE's reader (`annotations_from_report`) flattens it; the
    property names are the live ones (2026-10-15)."""
    props = {"Table Qualified Name": qualified, "Table Name": qualified.rsplit(".", 1)[-1], "Table Type": table_type}
    for name, v in (("Number Of Rows Inserted", inserted), ("Number Of Rows Updated", updated),
                    ("Number Of Rows Deleted", deleted)):
        if v is not None:
            props[name] = v
    return {"annotation_type": "Capture Database Table Measurements", "resource_properties": props}


def _report():
    return {"guid": REPORT_GUID, "at": "2026-10-15T08:00:00Z", "annotations": [
        _table_measurement("shop.public.orders", inserted="200", updated="40", deleted="10"),
        _table_measurement("shop.public.weight"),
        _table_measurement("shop.public.v_sales", table_type="View", inserted=None, updated=None, deleted=None),
        _table_measurement("shop.public.odd", deleted=None),
        {"annotation_type": "Capture Database Column Measurements", "resource_properties": {}},
    ]}


def _run_both(url, report, slug="shop"):
    """{(measure, scope): annotation} for one run with an ODS reader and, when given, a source report."""
    from trellis_analytics.discovery import discover
    from trellis_analytics.readers.ods import OdsReader
    from trellis_analytics.step import HostContext, SurveyStep

    m = discover()["database_staleness"]
    host = HostContext(resource_type="database", slug=slug, producing_run="run-x", ods=OdsReader(url),
                       source_report=report)
    return {(a.measure, a.scope): a for a in SurveyStep(m).run(host)}


WRITES = "writes_since_statistics_reset"


def test_with_a_report_and_two_snapshots_both_measures_appear_and_staleness_is_unchanged(
        staleness_installed, make_registry):
    reg, url = make_registry("shop")
    _survey(reg, "shop", "2026-10-01T00:00:00", [("public", "orders", "BASE TABLE", 100)])
    _survey(reg, "shop", "2026-10-08T00:00:00", [("public", "orders", "BASE TABLE", 130)])
    anns = _run_both(url, _report())
    stale = anns[("", "public.orders")]
    without = _run_both(url, None)[("", "public.orders")]
    # staleness: exactly step 3's figure, from the snapshots; nothing of the report in it
    assert stale.envelope.result_state.value == "measured"
    assert stale.resource_properties == without.resource_properties
    assert stale.resource_properties["rowsChanged"] == "30" and "writesSinceStatisticsReset" not in stale.resource_properties
    assert "measure" not in stale.additional_properties()
    # the second measure: its own annotations, the report's totals, as-of time and caveat
    writes = anns[(WRITES, "public.orders")]
    assert writes.envelope.result_state.value == "measured"
    assert writes.additional_properties()["measure"] == WRITES
    p = writes.resource_properties
    assert (p["rowsInserted"], p["rowsUpdated"], p["rowsDeleted"], p["writesSinceStatisticsReset"]) == (
        "200", "40", "10", "250")
    assert p["asOf"] == "2026-10-15T08:00:00Z" and p["sourceReport"] == REPORT_GUID
    assert "not a rate" in p["basis"] and "statistics were last reset" in p["basis"]
    assert "not a rate" in writes.summary and "rowsChanged" not in p
    assert anns[(WRITES, "public.weight")].envelope.result_state.value == "nothing_found"
    assert anns[(WRITES, "public.v_sales")].envelope.result_state.value == "not_applicable"
    odd = anns[(WRITES, "public.odd")]
    assert odd.envelope.result_state.value == "not_established"
    assert "Number Of Rows Deleted" in odd.envelope.result_state_reason


def test_with_a_report_and_one_snapshot_staleness_is_not_measured_and_writes_are_present(
        staleness_installed, make_registry):
    reg, url = make_registry("shop")
    _survey(reg, "shop", "2026-10-08T00:00:00", [("public", "orders", "BASE TABLE", 130)])
    anns = _run_both(url, _report())
    stale = anns[("", "public.orders")]
    assert stale.envelope.result_state.value == "not_established"
    assert stale.envelope.result_state_reason.startswith("not measured · one snapshot only")
    assert stale.resource_properties == {}                    # no substitute figure
    assert anns[(WRITES, "public.orders")].resource_properties["writesSinceStatisticsReset"] == "250"


def test_with_no_report_there_is_no_writes_annotation_and_staleness_is_step_3s(staleness_installed, make_registry):
    reg, url = make_registry("shop")
    _survey(reg, "shop", "2026-10-01T00:00:00", [("public", "orders", "BASE TABLE", 100)])
    _survey(reg, "shop", "2026-10-08T00:00:00", [("public", "orders", "BASE TABLE", 130)])
    anns = _run_both(url, None)
    # standalone: the second measure is absent and the absence is recorded
    assert list(anns) == [("", "public.orders"), (WRITES, "shop")]
    w = anns[(WRITES, "shop")]
    assert w.envelope.result_state.value == "not_established"
    assert w.summary == "writes since statistics reset: not measured · no survey report in this run"
    assert anns[("", "public.orders")].resource_properties["rowsChanged"] == "30"


def test_a_report_without_table_measurements_is_one_not_established_writes_result(staleness_installed,
                                                                                    make_registry):
    reg, url = make_registry("shop")
    anns = _run_both(url, {"guid": "g-1", "annotations": []})
    writes = anns[(WRITES, "shop")]
    assert writes.envelope.result_state.value == "not_established"
    assert "g-1 carries no table measurements" in writes.envelope.result_state_reason
    assert anns[("", "shop")].envelope.result_state.value == "not_established"     # never surveyed: staleness


def test_a_result_of_an_undeclared_measure_is_refused():
    from trellis_analytics.manifest import StepManifest
    from trellis_analytics.step import HostContext, StepRefused, SurveyStep

    m = StepManifest(name="s", function="x.y", description="d", source="ods", version="1",
                     resource_types=["database"], measures=["s", "other"])
    host = HostContext(resource_type="database", slug="x", producing_run="r")
    ok = SurveyStep(m, code=lambda i: [{"scope": "x", "measure": "other", "state": "nothing_found"}]).run(host)
    assert ok[0].measure == "other"
    with pytest.raises(StepRefused, match="does not declare"):
        SurveyStep(m, code=lambda i: [{"scope": "x", "measure": "sneaky", "state": "nothing_found"}]).run(host)
    with pytest.raises(ValueError, match="must include the step's own measure"):
        StepManifest(name="s", function="x.y", description="d", source="ods", version="1",
                     resource_types=["database"], measures=["other"])


def test_source_report_is_handed_only_to_a_step_that_declares_it():
    from trellis_analytics.manifest import StepManifest
    from trellis_analytics.step import HostContext, SurveyStep

    seen = {}

    def code(inputs):
        seen["report"] = inputs.source_report
        return [{"scope": "x", "state": "nothing_found"}]

    host = HostContext(resource_type="database", slug="x", producing_run="r", source_report={"guid": "g"})
    for inputs, expected in ((["ods"], None), (["ods", "source_report"], {"guid": "g"})):
        m = StepManifest(name="s", function="x.y", description="d", source="ods", version="1",
                         resource_types=["database"], inputs=inputs)
        SurveyStep(m, code=code).run(host)
        assert seen["report"] == expected


def test_the_manifest_is_the_wrappers_declaration(staleness_installed):
    from trellis_analytics.discovery import discover
    from trellis_analytics.manifest import StepManifest

    m = discover()["database_staleness"]
    assert isinstance(m, StepManifest)
    assert (m.kind, m.source, m.resource_types, m.annotation_type) == (
        "step", "ods", ["database"], "ResourceMeasureAnnotation")
    assert m.version == staleness_installed
    assert m.questions == ["Q-0041"] and m.purposes == ["Maintain"]
    assert m.credential_tier == "none" and m.inputs == ["ods", "source_report"]
    assert m.measure_names() == ["database_staleness", "writes_since_statistics_reset"]


# ── generated, not hand-written ──

@pytest.fixture
def library_installed(tmp_path, monkeypatch):
    """Every example step installed the way pip would; the catalog block holds all of them, not only staleness."""
    for pkg in sorted(p for p in EXAMPLE.parent.iterdir() if (p / "pyproject.toml").is_file()):
        project = tomllib.loads((pkg / "pyproject.toml").read_text())["project"]
        version = re.search(r'__version__ = "([^"]+)"', (pkg / "src" / pkg.name / "__init__.py").read_text()).group(1)
        d = tmp_path / f"{project['name'].replace('-', '_')}-{version}.dist-info"
        d.mkdir()
        (d / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {project['name']}\nVersion: {version}\n")
        (d / "entry_points.txt").write_text("[trellis.analytics]\n" + "".join(
            f"{k} = {v}\n" for k, v in project["entry-points"]["trellis.analytics"].items()))
        monkeypatch.syspath_prepend(str(pkg / "src"))
    monkeypatch.syspath_prepend(str(tmp_path))


def test_the_catalog_entries_in_analysis_catalog_yaml_are_the_generated_ones(reattach_after, library_installed):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "gen_external_steps", RE_PKG / "scripts" / "generate_external_step_entries.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)

    text = CATALOG.read_text()
    assert gen.render_catalog_block("database") in text, "analysis_catalog.yaml is stale: run the generator"
    cards = {e["id"]: e for e in yaml.safe_load(text)["database_analyses"] if "provided_by" in e}
    from trellis_analytics.discovery import discover
    steps = {n: m for n, m in discover().items() if m.kind == "step" and "database" in m.resource_types}
    assert set(cards) == set(steps) == {"database_staleness", "statistics_freshness", "hub_tables", "change_rates"}

    for name, m in steps.items():
        entry = cards[name]
        # what the entry says is what the manifest says
        assert entry["name"] == m.display_name and entry["description"] == m.description
        assert entry["resource_types"] == m.resource_types and entry["annotation_types"] == [m.annotation_type]
        assert entry["intent"] == m.stage and entry["perspectives"] == m.perspectives
        assert entry["provided_by"]["step"] == m.name and entry["provided_by"]["version"] == m.version
        assert entry["provided_by"]["questions"] == m.questions and entry["provided_by"]["purposes"] == m.purposes
        assert entry["availability"] == "queued"      # a step never runs in the web process
        # a card must say what "not measured" means: an absence is never shown as a zero
        assert "not measured" in entry["description"], name

    assert cards["database_staleness"]["provided_by"] == {
        "package": "trellis-step-database-staleness", "entry_point": "database_staleness",
        "step": "database_staleness", "version": steps["database_staleness"].version, "kind": "step", "source": "ods",
        "credential_tier": "none", "questions": ["Q-0041"], "purposes": ["Maintain"]}


def test_the_engine_host_declaration_is_generated_from_the_manifest(reattach_after, library_installed):
    """The step's Egeria declaration is RE's engine host's (Mandy, Q-M2; egeria-python ISSUE-150), one document per
    step, generated from the manifest. The provisional analytic-functions document is retired."""
    import importlib.util
    import json

    from trellis_analytics.declare import manifest_properties
    from trellis_analytics.discovery import discover, origin

    from resource_explorer.surveyors import survey_step_host

    survey_step_host.refresh("database")
    spec = importlib.util.spec_from_file_location(
        "gen_engine_host_decl", RE_PKG / "scripts" / "generate_engine_host_declaration.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    want = gen.render_library()
    assert DECLARATION.read_text() == want[DECLARATION], "the declaration is stale: run the generator"
    assert DECLARATION_STEPS.read_text() == want[DECLARATION_STEPS], "the pyegeria steps are stale"
    assert not (RE_PKG / "docs" / "dr-egeria" / "analytic-functions" / "database_staleness.md").exists()

    m = discover()["database_staleness"]
    doc = DECLARATION.read_text()
    assert f"### Qualified Name\nResourceExplorerSurvey::{m.request_type}" in doc
    assert f"### Request Type\n{m.request_type}" in doc
    for k, v in manifest_properties(m, origin("database_staleness")):
        assert f"| {k} | {v} |" in doc, (k, v)
    assert f"| nothing_to_derive_guard | {m.nothing_to_derive_guard} |" in doc
    assert "Delete" not in doc and "DEPRECATED" in doc     # retirement is a status, never a delete
    link = json.loads(DECLARATION_STEPS.read_text())["steps"][-1]
    assert (link["command"], link["request_type"]) == ("Link Supported Governance Service", m.request_type)


def test_the_manifest_declares_its_request_type_and_nothing_to_derive_guard(staleness_installed):
    from trellis_analytics.discovery import discover

    m = discover()["database_staleness"]
    assert (m.request_type, m.nothing_to_derive_guard) == ("re-database-staleness", "survey-completed")


# ── RE runs it ──

@contextmanager
def _spy_observe(monkeypatch, seen):
    from resource_explorer.surveyors import step_cost_observer
    real = step_cost_observer.observe

    @contextmanager
    def spy(step_key, *a, **kw):
        seen.append(step_key)
        with real(step_key, *a, **kw) as out:
            yield out
    monkeypatch.setattr(step_cost_observer, "observe", spy)
    yield


def test_re_runs_the_step_end_to_end_and_the_analysis_pane_reads_the_envelope(
        reattach_after, staleness_installed, make_registry, monkeypatch):
    import database_staleness.step as user_code
    from resource_explorer.surveyors import survey_step_host
    from resource_explorer.surveyors.survey_definition_executor import SurveyDefinitionExecutor, get_adapter
    from resource_explorer.workflows.stage_page import build_measurements

    reg, _ = make_registry("shop")
    _survey(reg, "shop", "2026-10-01T00:00:00", [
        ("public", "orders", "BASE TABLE", 100), ("public", "customers", "BASE TABLE", 50),
        ("public", "items", "BASE TABLE", 7), ("public", "v_sales", "VIEW", None)])
    _survey(reg, "shop", "2026-10-08T00:00:00", [
        ("public", "orders", "BASE TABLE", 130), ("public", "customers", "BASE TABLE", 50),
        ("public", "items", "BASE TABLE", None), ("public", "v_sales", "VIEW", None)])

    survey_step_host.refresh("database")        # discovery beside STEP_REGISTRY, after the install above
    adapter = get_adapter("database")
    assert isinstance(adapter.re_analysis_steps["database_staleness"], survey_step_host.SurveyStepHost)

    handed = []
    real_run = user_code.run
    monkeypatch.setattr(user_code, "run", lambda inputs: handed.append(inputs) or real_run(inputs))
    observed: list = []
    with _spy_observe(monkeypatch, observed):
        out = SurveyDefinitionExecutor(reg).run_synthetic_step(
            "database", "shop", "database_staleness", "resource-explorer",
            engine_override="resource-explorer", db_user="reader", db_pwd="hunter2-secret")

    assert [s["status"] for s in out["steps"] if s["re_analysis_step"] == "database_staleness"] == ["ok"], out
    # RE's step observer wrapped it as it wraps built-in steps (the OpenLineage hook lives there), with proof
    assert "database_staleness" in observed
    assert reg.query_step_runs(slug="shop", step_key="database_staleness", entity_type="database")
    # the user's code got the ODS, never the credential
    assert len(handed) == 1 and "hunter2-secret" not in repr(vars(handed[0])) and "reader" not in repr(vars(handed[0]))

    run, rows = reg.query_step_annotations("database", "shop", "database_staleness")
    assert len(rows) == 4 and run
    # the run id is the step_runs row's surveyed_at: the link between the two
    assert reg.query_step_runs(slug="shop", step_key="database_staleness", surveyed_at=run, entity_type="database")
    for r in rows:
        assert r["annotation_type"] == "ResourceMeasureAnnotation"
        assert (r["package"], r["producer_version"]) == ("trellis-step-database-staleness", staleness_installed)
        assert r["result_state"] == r["detail"]["additionalProperties"]["resultState"]
        env = Envelope.from_additional_properties(r["detail"]["additionalProperties"])
        assert env.producer_version == staleness_installed and env.producing_run == run

    m = build_measurements(reg, "shop", "database_staleness", entity_type="database")
    words = {row["name"]: row["state_words"] for row in m["measurements"]}
    assert words == {"public.orders": "measured", "public.customers": "nothing found",
                     "public.items": "not established", "public.v_sales": "not applicable"}
    items = next(row for row in m["measurements"] if row["name"] == "public.items")
    assert "not measured" in items["reason"]


def test_survey_step_host_is_named_and_aliased_as_the_brief_names_it():
    from resource_explorer.surveyors import survey_step_host
    assert survey_step_host.ExternalStep is survey_step_host.SurveyStepHost


# ── a state on screen carries short words from the stored data: no annotation of any state has an empty summary ──

def test_one_snapshot_says_exactly_not_measured_one_snapshot_only(staleness_installed, make_registry):
    reg, url = make_registry("shop")
    _survey(reg, "shop", "2026-10-01T00:00:00", [("public", "orders", "BASE TABLE", 100)])
    a = _run_step(url)["public.orders"]
    assert a.summary == "not measured · one snapshot only"


def test_every_state_has_a_summary_sentence(staleness_installed, make_registry):
    reg, url = make_registry("shop")
    # never surveyed
    never = _run_both(url, None)
    # two snapshots: measured, nothing found, a table gone from the previous one, an unmeasured count, a view
    _survey(reg, "shop", "2026-10-01T00:00:00", [
        ("public", "a", "BASE TABLE", 1), ("public", "b", "BASE TABLE", 5), ("public", "d", "BASE TABLE", None),
        ("public", "v", "VIEW", None)])
    _survey(reg, "shop", "2026-10-08T00:00:00", [
        ("public", "a", "BASE TABLE", 9), ("public", "b", "BASE TABLE", 5), ("public", "c", "BASE TABLE", 3),
        ("public", "d", "BASE TABLE", 4), ("public", "v", "VIEW", None)])
    two = _run_both(url, _report())
    states = {a.envelope.result_state.value for a in two.values()}
    assert states == {"measured", "nothing_found", "not_established", "not_applicable"}
    for a in list(never.values()) + list(two.values()):
        assert a.summary.strip(), (a.measure, a.scope, a.envelope.result_state.value)
    assert two[("", "public.c")].summary == "not measured · table not in the previous survey"
    assert two[("", "public.d")].summary == "not measured · row count missing in a survey"
    assert two[("", "public.v")].summary == "not applicable · a view holds no rows"
    assert never[("", "shop")].summary == "not measured · no survey has counted rows yet"
    assert two[(WRITES, "public.odd")].summary == "not measured · a write counter is missing in the survey report"


def test_a_report_with_no_table_measurements_has_a_summary(staleness_installed, make_registry):
    reg, url = make_registry("shop")
    anns = _run_both(url, {"guid": REPORT_GUID, "at": "x", "annotations": []})
    assert anns[(WRITES, "shop")].summary == "not measured · the survey report has no table measurements"


def test_the_library_never_stores_an_empty_summary_for_a_step_that_gave_none(staleness_installed):
    from trellis_analytics.envelope import ResultState
    from trellis_analytics.step import HostContext, SurveyStep
    from trellis_analytics.manifest import StepManifest
    from trellis_analytics.discovery import discover

    m = discover()["database_staleness"]
    step = SurveyStep(m)
    host = HostContext(resource_type="database", slug="s", producing_run="r")
    for state, reason, want in (("not_established", "why", "not measured · why"),
                                ("not_applicable", "because", "not applicable · because"),
                                ("nothing_found", "", "nothing found"), ("measured", "", "measured")):
        a = step._annotate({"scope": "x", "state": state, "reason": reason}, host, "2026-10-15T00:00:00Z")
        assert a.summary == want
