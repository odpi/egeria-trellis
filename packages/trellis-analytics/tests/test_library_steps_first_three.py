"""The first three Analytic Library steps of the slice (SLICE-ANALYTIC-LIBRARY-STEPS-NEXT-FIVE.md): statistics
freshness, hub tables and change rates, each an installable package under examples/steps/, run by the wrapper over a
real (scratch SQLite) registry through the ODS reader's typed reads only.

Per step: measured from known rows; the absence cases each with its sentence; nothing to derive (every result an
absence) with the manifest's guard; the annotation type and properties. Every result carries a summary.
"""
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STEPS = ROOT / "examples" / "steps"


def _install(tmp_path, monkeypatch, name):
    """The step installed the way pip would: a dist-info carrying its pyproject's entry points, its source on the path."""
    example = STEPS / name
    project = tomllib.loads((example / "pyproject.toml").read_text())["project"]
    eps = project["entry-points"]["trellis.analytics"]
    version = re.search(r'__version__ = "([^"]+)"', (example / "src" / name / "__init__.py").read_text()).group(1)
    d = tmp_path / f"{project['name'].replace('-', '_')}-{version}.dist-info"
    d.mkdir()
    (d / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {project['name']}\nVersion: {version}\n")
    (d / "entry_points.txt").write_text("[trellis.analytics]\n" + "".join(f"{k} = {v}\n" for k, v in eps.items()))
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.syspath_prepend(str(example / "src"))
    return version


@pytest.fixture
def reattach_after():
    yield
    from resource_explorer.surveyors import survey_step_host
    survey_step_host.refresh("database")


def _run(url, name, slug="shop"):
    """[(measure, scope, annotation)] for one run of the installed step through the wrapper."""
    from trellis_analytics.discovery import discover
    from trellis_analytics.readers.ods import OdsReader
    from trellis_analytics.step import HostContext, SurveyStep

    m = discover()[name]
    host = HostContext(resource_type="database", slug=slug, producing_run="run-x", ods=OdsReader(url))
    anns = SurveyStep(m).run(host)
    assert all(a.summary for a in anns), [a.scope for a in anns if not a.summary]   # never an empty sentence
    return {(a.measure, a.scope): a for a in anns}


def _state(a) -> str:
    return a.envelope.result_state.value


def _activity(reg, slug, at, rows, source="local", *, invalid=False):
    """One stored activity snapshot. rows: dicts with schema_name, table_name and any activity columns."""
    reg.record_database_survey(slug, 1, len(rows), 0, {}, surveyed_at=at, source=source)
    reg.write_detail_rows("database_table_activity", slug, at, source=source, rows=rows)
    if invalid:
        import sqlite3
        c = sqlite3.connect(reg.database_url.removeprefix("sqlite:///"))
        c.execute("UPDATE database_surveys SET invalid_at = ?, invalid_reason = 'test' WHERE surveyed_at = ?",
                  (at, at))
        c.commit()
        c.close()


def _act(table, **kw):
    return {"schema_name": "public", "table_name": table, **kw}


# ══ statistics_freshness ═════════════════════════════════════════════════════════

@pytest.fixture
def freshness(reattach_after, tmp_path, monkeypatch):
    return _install(tmp_path, monkeypatch, "statistics_freshness")


AS_OF = "2026-10-15T00:00:00"


def test_freshness_measured_from_the_newest_activity_snapshot(freshness, make_registry):
    reg, url = make_registry("shop")
    _activity(reg, "shop", "2026-10-01T00:00:00", [_act("orders", live_tuples=1, rows_inserted=1)])
    _activity(reg, "shop", AS_OF, [
        # stale: 2,000 changes > 50 + 0.1 * 10,000 = 1,050
        _act("orders", live_tuples=10000, pending_changes=2000, last_analyze="2026-10-05T00:00:00",
             last_autoanalyze="2026-10-10T00:00:00", last_autovacuum="2026-10-12T00:00:00"),
        # current
        _act("customers", live_tuples=500, pending_changes=3, last_autoanalyze="2026-10-14T12:00:00"),
        # never analysed: Postgres NULL for both
        _act("staging", live_tuples=40, pending_changes=40),
        # analysed, changes since not read
        _act("archive", live_tuples=9, last_analyze="2026-09-15T00:00:00"),
        # not read: no live-row count
        _act("ghost", rows_inserted=5),
    ])
    anns = _run(url, "statistics_freshness")
    orders = anns[("", "public.orders")]
    assert _state(orders) == "measured" and orders.annotation_type == "ResourceMeasureAnnotation"
    p = orders.resource_properties
    assert p["Statistics Status"] == "stale"
    assert p["Last Auto Analyze"] == "2026-10-10T00:00:00" and p["Last Analyze"] == "2026-10-05T00:00:00"
    assert p["Days Since Analyze"] == "5.0" and p["Days Since Vacuum"] == "3.0"   # from the as-of, not now
    assert p["Analyze Threshold"] == "1050.0" and p["Number Of Rows Changed Since Analyze"] == "2000"
    assert p["Last Vacuum"] == "never" and p["asOf"] == AS_OF and "PostgreSQL's default" in p["basis"]
    assert "stale" in orders.summary and "2,000" in orders.summary and AS_OF in orders.summary
    assert anns[("", "public.customers")].resource_properties["Statistics Status"] == "current"
    staging = anns[("", "public.staging")]
    assert staging.resource_properties["Statistics Status"] == "never analyzed"
    assert staging.resource_properties["Last Analyze"] == "never" and "Days Since Analyze" not in \
        staging.resource_properties                                     # never is a finding, never a 0
    assert anns[("", "public.archive")].resource_properties["Statistics Status"] == \
        "analysed; changes since not read"
    ghost = anns[("", "public.ghost")]
    assert _state(ghost) == "not_established" and ghost.resource_properties == {}
    assert ghost.summary == "not measured · statistics not read for this table"
    summary = anns[("statistics_freshness_summary", "shop")]
    assert _state(summary) == "measured"
    s = summary.resource_properties
    assert (s["Tables"], s["Stale"], s["Current"], s["Never Analyzed"], s["Not Read"]) == ("5", "1", "1", "1", "1")
    assert s["Oldest Analyze Days"] == "30.0"
    assert summary.additional_properties()["measure"] == "statistics_freshness_summary"


def test_freshness_with_no_stored_statistics_records_the_absence_for_both_measures(freshness, make_registry):
    reg, url = make_registry("shop")
    # a snapshot with every counter NULL measured nothing, and an invalid survey is not a snapshot
    _activity(reg, "shop", "2026-10-01T00:00:00", [_act("orders", last_analyze="2026-09-01T00:00:00")])
    _activity(reg, "shop", "2026-10-02T00:00:00", [_act("orders", live_tuples=5)], invalid=True)
    anns = _run(url, "statistics_freshness")
    assert set(anns) == {("", "shop"), ("statistics_freshness_summary", "shop")}
    for a in anns.values():
        assert _state(a) == "not_established" and a.summary == "not measured · no table statistics stored"
        assert "no survey has stored table statistics" in a.envelope.result_state_reason


def test_freshness_a_native_snapshot_is_not_read_never_never_analysed(freshness, make_registry):
    reg, url = make_registry("shop")
    _activity(reg, "shop", AS_OF, [_act("orders", rows_inserted=3, live_tuples=7)], source="egeria")
    anns = _run(url, "statistics_freshness")
    assert _state(anns[("", "public.orders")]) == "not_established"
    assert _state(anns[("statistics_freshness_summary", "shop")]) == "not_established"
    assert anns[("statistics_freshness_summary", "shop")].summary == "not measured · no table statistics read"


def test_freshness_manifest(freshness):
    from trellis_analytics.discovery import discover

    m = discover()["statistics_freshness"]
    assert (m.request_type, m.nothing_to_derive_guard, m.version) == (
        "re-statistics-freshness", "survey-completed", freshness)
    assert m.annotation_types() == ["ResourceMeasureAnnotation"]
    assert m.measure_names() == ["statistics_freshness", "statistics_freshness_summary"]
    assert (m.source, m.credential_tier, m.inputs) == ("ods", "none", ["ods"])


# ══ change_rates ══════════════════════════════════════════════════════════════════

@pytest.fixture
def rates(reattach_after, tmp_path, monkeypatch):
    return _install(tmp_path, monkeypatch, "change_rates")


def _counters(i, u, d, **kw):
    return dict(rows_inserted=i, rows_updated=u, rows_deleted=d, live_tuples=1, **kw)


def test_change_rates_measured_over_the_interval_with_both_as_ofs(rates, make_registry):
    reg, url = make_registry("shop")
    _activity(reg, "shop", "2026-10-01T00:00:00", [
        _act("orders", **_counters(100, 10, 0)), _act("idle", **_counters(5, 5, 5)),
        _act("reset", **_counters(900, 0, 0, stats_reset="2026-09-01")), _act("back", **_counters(500, 0, 0)),
        _act("half", **_counters(1, None, 1))])
    _activity(reg, "shop", "2026-10-05T00:00:00", [
        _act("orders", **_counters(500, 30, 20)), _act("idle", **_counters(5, 5, 5)),
        _act("reset", **_counters(950, 0, 0, stats_reset="2026-10-03")), _act("back", **_counters(10, 0, 0)),
        _act("half", **_counters(2, 1, 1)), _act("fresh", **_counters(1, 0, 0))])
    anns = _run(url, "change_rates")
    orders = anns[("", "public.orders")]
    assert _state(orders) == "measured" and orders.annotation_type == "ResourceMeasureAnnotation"
    p = orders.resource_properties
    assert (p["Rows Inserted In Interval"], p["Rows Updated In Interval"], p["Rows Deleted In Interval"]) == (
        "400", "20", "20")
    assert p["Writes In Interval"] == "440" and p["Writes Per Day"] == "110.0" and p["Interval Days"] == "4.0"
    assert (p["fromAsOf"], p["toAsOf"]) == ("2026-10-01T00:00:00", "2026-10-05T00:00:00")
    assert "counter reset" in p["basis"]
    assert orders.summary.startswith("110.0 writes per day over 4.00 days")
    idle = anns[("", "public.idle")]
    assert _state(idle) == "nothing_found" and idle.resource_properties["Writes Per Day"] == "0.0"
    assert idle.summary.startswith("no inserts, updates or deletes over 4.00 days")
    # a counter reset is no rate, never the negative difference
    for t, why in (("reset", "reset time moved"), ("back", "Rows Inserted went backwards")):
        a = anns[("", f"public.{t}")]
        assert _state(a) == "not_established" and a.resource_properties == {}, t
        assert why in a.envelope.result_state_reason
        assert a.summary == "not measured · statistics were reset between snapshots"
    half = anns[("", "public.half")]
    assert _state(half) == "not_established" and "Rows Updated not measured in one snapshot" in \
        half.envelope.result_state_reason
    assert _state(anns[("", "public.fresh")]) == "not_established"
    assert anns[("", "public.fresh")].summary == "not measured · table not in the earlier snapshot"


@pytest.mark.parametrize("old_reset, new_reset, words", [
    # NULL then a time: a reset the older snapshot could not show. Never a rate across it (review, 2026-10-15).
    (None, "2026-10-03T00:00:00", "not measured · statistics were reset between snapshots"),
    ("2026-09-01T00:00:00", None, "not measured · statistics reset time not read in the newer snapshot"),
    ("2026-09-01T00:00:00", "2026-09-01T00:00:00", None),       # the same reset: a rate
    (None, None, None),                                          # no reset evidence, counters forward: a rate
])
def test_change_rates_never_computes_a_rate_across_a_possible_reset(rates, make_registry, old_reset, new_reset,
                                                                    words):
    reg, url = make_registry("shop")
    _activity(reg, "shop", "2026-10-01T00:00:00", [_act("orders", **_counters(100, 0, 0, stats_reset=old_reset))])
    _activity(reg, "shop", "2026-10-05T00:00:00", [_act("orders", **_counters(140, 0, 0, stats_reset=new_reset))])
    a = _run(url, "change_rates")[("", "public.orders")]
    if words is None:
        assert _state(a) == "measured" and a.resource_properties["Writes In Interval"] == "40"
    else:
        assert _state(a) == "not_established" and a.summary == words and a.resource_properties == {}


@pytest.mark.parametrize("snapshots, words", [
    (0, "not measured · no activity snapshot yet; a rate needs two"),
    (1, "not measured · one activity snapshot only; a rate needs two"),
])
def test_change_rates_with_fewer_than_two_snapshots_records_the_absence(rates, make_registry, snapshots, words):
    reg, url = make_registry("shop")
    if snapshots:
        _activity(reg, "shop", "2026-10-01T00:00:00", [_act("orders", **_counters(1, 1, 1))])
        # an all-NULL snapshot and an invalid survey are not snapshots
        _activity(reg, "shop", "2026-10-02T00:00:00", [_act("orders")])
        _activity(reg, "shop", "2026-10-03T00:00:00", [_act("orders", **_counters(9, 9, 9))], invalid=True)
    anns = _run(url, "change_rates")
    assert list(anns) == [("", "shop")]
    assert _state(anns[("", "shop")]) == "not_established" and anns[("", "shop")].summary == words


def test_change_rates_two_snapshots_at_one_time_have_no_interval(rates, make_registry):
    reg, url = make_registry("shop")
    _activity(reg, "shop", "2026-10-01T00:00:00", [_act("orders", **_counters(1, 1, 1))], source="local")
    _activity(reg, "shop", "2026-10-01T00:00:00", [_act("orders", **_counters(2, 1, 1))], source="egeria")
    anns = _run(url, "change_rates")
    assert list(anns) == [("", "shop")] and "no interval" in anns[("", "shop")].summary


def test_change_rates_manifest(rates):
    from trellis_analytics.discovery import discover

    m = discover()["change_rates"]
    assert (m.request_type, m.nothing_to_derive_guard, m.annotation_types()) == (
        "re-change-rates", "survey-completed", ["ResourceMeasureAnnotation"])


# ══ hub_tables ════════════════════════════════════════════════════════════════════

@pytest.fixture
def hubs(reattach_after, tmp_path, monkeypatch):
    return _install(tmp_path, monkeypatch, "hub_tables")


KEYS_AT = "2026-10-10T00:00:00"


def _schema(reg, slug, tables, at=KEYS_AT, *, captured=True, uncaptured=(), views=()):
    """tables: {name: [referenced table names]}. Every table gets an id column; one FK column per reference."""
    reg.record_database_survey(slug, 1, len(tables), 0, {}, surveyed_at=at)
    reg.write_detail_rows("database_tables", slug, at, rows=[
        {"schema_name": "public", "table_name": t, "table_type": "VIEW" if t in views else "BASE TABLE"}
        for t in tables])
    cols = []
    for t, refs in tables.items():
        pk = None if (not captured or t in uncaptured or t in views) else 1
        cols.append({"schema_name": "public", "table_name": t, "column_name": "id", "is_primary_key": pk})
        for r in refs:
            cols.append({"schema_name": "public", "table_name": t, "column_name": f"{r}_id",
                         "is_primary_key": None if pk is None else 0,
                         "foreign_key_json": {"foreign_schema": "public", "foreign_table": r, "foreign_column": "id"}})
    reg.write_detail_rows("database_columns", slug, at, rows=cols)


SCHEMA = {"customer": [], "product": [], "store": [],
          "orders": ["customer", "store", "product"], "returns": ["orders", "customer", "store"],
          "reviews": ["customer", "product", "reviews"], "loyalty": ["customer", "store"]}


def test_hub_tables_measured_degrees_and_a_proposal_per_hub(hubs, make_registry):
    reg, url = make_registry("shop")
    _schema(reg, "shop", {**SCHEMA, "v_sales": []}, views=("v_sales",))
    anns = _run(url, "hub_tables")
    customer = anns[("", "public.customer")]
    assert _state(customer) == "measured" and customer.annotation_type == "ResourceMeasureAnnotation"
    p = customer.resource_properties
    assert (p["Foreign Keys In"], p["Foreign Keys Out"]) == ("4", "0")
    assert p["Referencing Tables"] == "public.loyalty, public.orders, public.returns, public.reviews"
    assert p["asOf"] == KEYS_AT and "proposal" in p["basis"]
    reviews = anns[("", "public.reviews")]
    assert reviews.resource_properties["Foreign Keys Out"] == "2"                 # its self-reference not counted
    assert anns[("", "public.orders")].resource_properties["Foreign Keys In"] == "1"
    assert _state(anns[("", "public.v_sales")]) == "not_applicable"
    # hubs: referenced by >= 3 other tables -> ClassificationAnnotation proposals, nothing else
    proposals = {s: a for (m, s), a in anns.items() if m == "hub_table_proposal"}
    assert set(proposals) == {"public.customer", "public.store"}
    hub = proposals["public.customer"]
    assert hub.annotation_type == "ClassificationAnnotation" and hub.candidate_classifications == ["hub table"]
    assert _state(hub) == "measured" and hub.summary.startswith("proposed: hub table · referenced by 4 other tables")
    assert hub.resource_properties["Hub Threshold"] == "3"
    assert hub.to_record()["candidateClassifications"] == ["hub table"]
    assert hub.additional_properties()["measure"] == "hub_table_proposal"
    assert "candidateClassifications" not in customer.to_record()


def test_hub_tables_no_hub_is_one_nothing_found_proposal(hubs, make_registry):
    reg, url = make_registry("shop")
    _schema(reg, "shop", {"a": [], "b": ["a"], "c": []})
    anns = _run(url, "hub_tables")
    [(key, none)] = [(k, a) for k, a in anns.items() if k[0] == "hub_table_proposal"]
    assert key == ("hub_table_proposal", "shop") and _state(none) == "nothing_found"
    assert none.candidate_classifications == [] and none.annotation_type == "ClassificationAnnotation"
    assert none.summary.startswith("no table is referenced by 3 or more other tables")
    assert _state(anns[("", "public.c")]) == "nothing_found"                     # no keys in or out: a finding


def test_hub_tables_with_no_keys_captured_records_the_absence(hubs, make_registry):
    reg, url = make_registry("shop")
    _schema(reg, "shop", SCHEMA, captured=False)
    anns = _run(url, "hub_tables")
    assert set(anns) == {("", "shop"), ("hub_table_proposal", "shop")}
    for a in anns.values():
        assert _state(a) == "not_established"
        assert a.summary == "not measured · no keys captured; hubs cannot be derived"


def test_hub_tables_with_no_columns_stored_records_the_absence(hubs, make_registry):
    _reg, url = make_registry("shop")
    anns = _run(url, "hub_tables")
    assert {a.summary for a in anns.values()} == {"not measured · no columns stored"} and len(anns) == 2


def test_hub_tables_partly_captured_keys_report_a_lower_bound_and_propose_the_hubs_it_reaches(hubs, make_registry):
    reg, url = make_registry("shop")
    _schema(reg, "shop", SCHEMA, uncaptured=("loyalty",))
    anns = _run(url, "hub_tables")
    customer = anns[("", "public.customer")]
    p = customer.resource_properties
    assert p["Foreign Keys In"] == "3" and p["Referenced By Is Lower Bound"] == "true"   # loyalty's keys unread
    assert p["Foreign Keys Out"] == "0"
    assert customer.summary.startswith("references 0 other tables; referenced by at least 3 other tables · hub "
                                       "(lower bound)")
    loyalty = anns[("", "public.loyalty")]
    assert _state(loyalty) == "not_established" and loyalty.summary == "not measured · keys not captured for this table"
    # store: 2 captured referencers (orders, returns), below 3: a lower bound, never a confident no
    store = anns[("", "public.store")]
    assert store.resource_properties["Foreign Keys In"] == "2" and "could reach the threshold" in store.summary
    proposals = {s: a for (m, s), a in anns.items() if m == "hub_table_proposal"}
    assert _state(proposals["public.customer"]) == "measured"
    assert proposals["public.customer"].candidate_classifications == ["hub table"]
    assert proposals["public.customer"].resource_properties["Referenced By Is Lower Bound"] == "true"
    assert "at least 3 other tables, a lower bound" in proposals["public.customer"].summary
    assert "public.store" not in proposals
    [verdict] = [a for s, a in proposals.items() if s == "shop"]
    assert _state(verdict) == "not_established" and "not established" in verdict.summary


def test_hub_tables_missing_keys_below_the_threshold_is_never_nothing_found_or_not_a_hub(hubs, make_registry):
    reg, url = make_registry("shop")
    _schema(reg, "shop", {"a": [], "b": ["a"], "c": [], "d": []}, uncaptured=("d",))
    anns = _run(url, "hub_tables")
    a = anns[("", "public.a")]
    assert _state(a) == "measured" and a.resource_properties["Foreign Keys In"] == "1"
    assert "at least 1 other table " in a.summary and "could reach the threshold" in a.summary
    c = anns[("", "public.c")]                                 # lower bound 0 says nothing about foreign keys in
    assert "Foreign Keys In" not in c.resource_properties and "referenced-by not established" in c.summary
    proposals = [(m, s, x) for (m, s), x in anns.items() if m == "hub_table_proposal"]
    [(_m, scope, verdict)] = proposals
    assert scope == "shop" and _state(verdict) == "not_established" and verdict.candidate_classifications == []
    assert "no table is referenced" not in verdict.summary


def test_hub_tables_all_keys_captured_stays_exact(hubs, make_registry):
    reg, url = make_registry("shop")
    _schema(reg, "shop", SCHEMA)
    anns = _run(url, "hub_tables")
    customer = anns[("", "public.customer")]
    assert "Referenced By Is Lower Bound" not in customer.resource_properties
    assert "at least" not in customer.summary and "referenced by 4 other tables" in customer.summary


def test_hub_tables_reads_its_threshold_from_the_manifest_parameter(hubs, make_registry, monkeypatch):
    """The threshold is the manifest's declared `hub_min_referencing`, handed as inputs.parameters: changing the
    manifest changes the hubs, and the code holds no number of its own."""
    import hub_tables.step as code
    from trellis_analytics.discovery import discover
    from trellis_analytics.readers.ods import OdsReader
    from trellis_analytics.step import HostContext, SurveyStep

    assert not hasattr(code, "HUB_MIN_REFERENCING")
    reg, url = make_registry("shop")
    _schema(reg, "shop", SCHEMA)
    m = discover()["hub_tables"]
    assert m.parameters == {"hub_min_referencing": 3}
    host = HostContext(resource_type="database", slug="shop", producing_run="r", ods=OdsReader(url))

    def hubs_with(params):
        anns = SurveyStep(m.model_copy(update={"parameters": params})).run(host)
        return sorted(a.scope for a in anns if a.measure == "hub_table_proposal" and a.candidate_classifications)

    assert hubs_with({"hub_min_referencing": 3}) == ["public.customer", "public.store"]
    assert hubs_with({"hub_min_referencing": 4}) == ["public.customer"]
    assert hubs_with({"hub_min_referencing": 1}) == ["public.customer", "public.orders", "public.product",
                                                     "public.store"]
    with pytest.raises(ValueError, match="hub_min_referencing"):
        SurveyStep(m.model_copy(update={"parameters": {}})).run(host)


def test_hub_tables_manifest_declares_the_proposal_as_a_classification_annotation(hubs):
    from trellis_analytics.declare import manifest_properties
    from trellis_analytics.discovery import discover

    m = discover()["hub_tables"]
    assert (m.request_type, m.nothing_to_derive_guard) == ("re-hub-tables", "survey-completed")
    assert m.annotation_type_for("") == m.annotation_type_for("hub_tables") == "ResourceMeasureAnnotation"
    assert m.annotation_type_for("hub_table_proposal") == "ClassificationAnnotation"
    assert m.annotation_types() == ["ResourceMeasureAnnotation", "ClassificationAnnotation"]
    rows = manifest_properties(m, {"package": "p", "entry_point": "hub_tables"})
    assert ("measure_annotation_types", "hub_table_proposal: ClassificationAnnotation") in rows
    assert ("parameters", "hub_min_referencing=3") in rows


# ══ the library pieces the three need ════════════════════════════════════════════

def test_measure_annotation_types_must_name_declared_measures():
    from trellis_analytics.manifest import StepManifest

    base = dict(name="s", function="x.y", description="d", source="ods", version="1", resource_types=["database"])
    with pytest.raises(ValueError, match="does not declare"):
        StepManifest(**base, measures=["s"], measure_annotation_types={"other": "ClassificationAnnotation"})
    with pytest.raises(ValueError, match="no annotation type"):
        StepManifest(**base, measures=["s", "o"], measure_annotation_types={"o": " "})


def test_a_proposal_in_a_type_that_cannot_carry_it_is_refused():
    from trellis_analytics.manifest import StepManifest
    from trellis_analytics.step import HostContext, StepRefused, SurveyStep

    m = StepManifest(name="s", function="x.y", description="d", source="ods", version="1",
                     resource_types=["database"])
    host = HostContext(resource_type="database", slug="x", producing_run="r")
    with pytest.raises(StepRefused, match="only a ClassificationAnnotation carries them"):
        SurveyStep(m, code=lambda i: [{"scope": "x", "state": "measured",
                                       "candidate_classifications": ["hub table"]}]).run(host)
    with pytest.raises(StepRefused, match="list of non-empty strings"):
        SurveyStep(m, code=lambda i: [{"scope": "x", "state": "measured",
                                       "candidate_classifications": "hub table"}]).run(host)


def test_a_step_without_measure_types_declares_exactly_what_it_did(tmp_path, monkeypatch):
    """The declaration row is added only when a step has another type: database_staleness's rows are unchanged."""
    from trellis_analytics.declare import manifest_properties
    from trellis_analytics.manifest import StepManifest

    m = StepManifest(name="s", function="x.y", description="d", source="ods", version="1",
                     resource_types=["database"])
    assert "measure_annotation_types" not in dict(manifest_properties(m, {}))
    assert "parameters" not in dict(manifest_properties(m, {}))


def test_a_result_proposes_at_most_one_classification():
    """Egeria's ClassificationAnnotation names ONE classification (classificationName)."""
    from trellis_analytics.manifest import StepManifest
    from trellis_analytics.step import HostContext, StepRefused, SurveyStep

    m = StepManifest(name="s", function="x.y", description="d", source="ods", version="1",
                     resource_types=["database"], measures=["s", "p"],
                     measure_annotation_types={"p": "ClassificationAnnotation"})
    host = HostContext(resource_type="database", slug="x", producing_run="r")
    with pytest.raises(StepRefused, match="names one"):
        SurveyStep(m, code=lambda i: [{"scope": "x", "measure": "p", "state": "measured",
                                       "candidate_classifications": ["a", "b"]}]).run(host)
