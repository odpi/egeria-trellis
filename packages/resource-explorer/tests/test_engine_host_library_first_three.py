"""Engine host, end to end, for the slice's first three Analytic Library steps (SLICE-ANALYTIC-LIBRARY-STEPS-NEXT-FIVE.md):
`re-statistics-freshness`, `re-hub-tables`, `re-change-rates`.

Each is installed the way pip would (tests/library_step_install.py), claimed by RE's engine host from an engine action
with its request type, run through the step host on the stored rows (typed reads only), published on one SurveyReport
and completed from the stored proof. Only the pyegeria boundary is faked, with the autospec'd `FakeEgeria` of
test_engine_host_e4 (`create_autospec(spec_set=True)`: real signatures).

Per step: measured (the annotation type, measure names, properties and envelope on the report); nothing to derive
(the database has no rows the step reads: survey-completed, the absence published with its sentence, as the
2026-10-15 ruling says, never survey-invalid). Hub tables: the proposal is a ClassificationAnnotation (DRAFT), never
a classification on an element.
"""
from __future__ import annotations

import pytest

from tests.library_step_install import install_steps  # noqa: F401 - the fixture
from tests.test_engine_host_e4 import (  # noqa: F401 - fixtures and helpers reused
    DB_GUID,
    EMPTY_DB_GUID,
    _daemon_user,
    _row,
    _run_queue_once,
    _sweep,
    egeria,
    no_connection,
    reg,
)

T0, T1 = "2026-10-11T00:00:00", "2026-10-15T00:00:00"


def _activity(reg, at, rows, slug="coco_pharma"):
    reg.record_database_survey(slug=slug, schema_count=1, table_count=len(rows), column_count=0, survey_data={},
                               source="local", surveyed_at=at)
    reg.write_detail_rows("database_table_activity", slug, at, source="local",
                          rows=[{"schema_name": "public", "table_name": t, **cols} for t, cols in rows])


def _bodies(egeria, report_guid) -> list[dict]:
    return [egeria.elements[r["end2"]]["body"]["properties"] for r in egeria.relationships
            if r["end1"] == report_guid and egeria.elements.get(r["end2"], {}).get("type") == "Annotation"]


def _complete(reg, egeria, guid, request_type, target=DB_GUID):
    egeria.add_action(guid, target=target, request_type=request_type)
    assert _sweep(reg).claimed == [guid]
    assert _run_queue_once(reg) is not None
    done = egeria.actions[guid]["completion"]
    assert done["status"] == "ACTIONED" and done["guards"] == ["survey-completed"], done
    [(report_guid, body)] = egeria.reports()
    assert body["properties"]["purpose"] == request_type and body["parentGUID"] == target
    assert done["targets"] == [{"actionTargetName": "surveyReport", "actionTargetGUID": report_guid}]
    outcome = _row(reg)["target"]["outcome"]
    assert (outcome["guard"], outcome["report_guid"]) == ("survey-completed", report_guid)
    bodies = _bodies(egeria, report_guid)
    assert outcome["annotation_count"] == len(bodies) == egeria.annotations_under(report_guid)
    assert all(b["summary"] for b in bodies)                  # every state carries a sentence
    # every write is the engine host daemon's; no classification lands on any element but the report itself
    assert all(g == report_guid for _kind, g, _b in egeria.classifications), egeria.classifications
    return report_guid, bodies


def _envelope(b) -> dict:
    return b["additionalProperties"]


# ══ re-statistics-freshness ═══════════════════════════════════════════════════════

def test_statistics_freshness_is_claimed_run_published_and_completed(install_steps, reg, egeria, no_connection):
    install_steps("statistics_freshness")
    from resource_explorer import engine_host

    assert engine_host.supported_request_types()["re-statistics-freshness"] == "statistics_freshness"
    _activity(reg, T1, [
        ("orders", dict(live_tuples=10000, pending_changes=2000, last_autoanalyze="2026-10-10T00:00:00")),
        ("customer", dict(live_tuples=100, pending_changes=1, last_analyze="2026-10-14T00:00:00")),
        ("staging", dict(live_tuples=4, pending_changes=4))])
    _rg, bodies = _complete(reg, egeria, "ea-fresh", "re-statistics-freshness")
    tables = {_envelope(b)["scope"]: b for b in bodies if b["annotationType"] == "Table Statistics Freshness"}
    assert set(tables) == {"public.orders", "public.customer", "public.staging"}
    orders = tables["public.orders"]
    assert (orders["class"], orders["analysisStep"]) == ("ResourceMeasureAnnotationProperties", "statistics_freshness")
    assert orders["resourceProperties"]["Statistics Status"] == "stale"
    assert orders["resourceProperties"]["Days Since Analyze"] == "5.0" and orders["resourceProperties"]["asOf"] == T1
    assert _envelope(orders)["resultState"] == "measured"
    assert tables["public.staging"]["resourceProperties"]["Last Analyze"] == "never"
    [summary] = [b for b in bodies if b["annotationType"] == "Statistics Freshness Summary"]
    assert _envelope(summary)["measure"] == "statistics_freshness_summary"
    assert summary["resourceProperties"]["Stale"] == "1" and "of 3 tables" in summary["summary"]
    _run, rows = reg.query_step_annotations("database", "coco_pharma", "statistics_freshness")
    assert {r["scope"] for r in rows} == set(tables)


def test_statistics_freshness_with_nothing_stored_completes_with_the_absence(install_steps, reg, egeria,
                                                                             no_connection):
    install_steps("statistics_freshness")
    _rg, bodies = _complete(reg, egeria, "ea-fresh0", "re-statistics-freshness", target=EMPTY_DB_GUID)
    assert len(bodies) == 2
    for b in bodies:
        assert _envelope(b)["resultState"] == "not_established"
        assert b["summary"] == "not measured · no table statistics stored"
        assert "no survey has stored table statistics" in b["explanation"]


# ══ re-change-rates ════════════════════════════════════════════════════════════════

def test_change_rates_is_claimed_run_published_and_completed(install_steps, reg, egeria, no_connection):
    install_steps("change_rates")
    c = dict(live_tuples=1)
    _activity(reg, T0, [("orders", dict(rows_inserted=100, rows_updated=0, rows_deleted=0, **c)),
                        ("customer", dict(rows_inserted=5, rows_updated=5, rows_deleted=5, **c))])
    _activity(reg, T1, [("orders", dict(rows_inserted=500, rows_updated=40, rows_deleted=20, **c)),
                        ("customer", dict(rows_inserted=5, rows_updated=5, rows_deleted=5, **c))])
    _rg, bodies = _complete(reg, egeria, "ea-rates", "re-change-rates")
    by = {_envelope(b)["scope"]: b for b in bodies}
    assert set(by) == {"public.orders", "public.customer"}
    orders = by["public.orders"]
    assert (orders["class"], orders["annotationType"], orders["analysisStep"]) == (
        "ResourceMeasureAnnotationProperties", "Table Change Rates", "change_rates")
    p = orders["resourceProperties"]
    assert (p["Writes In Interval"], p["Writes Per Day"], p["Interval Days"]) == ("460", "115.0", "4.0")
    assert (p["fromAsOf"], p["toAsOf"]) == (T0, T1) and "counter reset" in p["basis"]
    assert _envelope(by["public.customer"])["resultState"] == "nothing_found"


def test_change_rates_with_one_snapshot_completes_with_the_absence(install_steps, reg, egeria, no_connection):
    install_steps("change_rates")
    _activity(reg, T1, [("orders", dict(rows_inserted=1, rows_updated=1, rows_deleted=1, live_tuples=1))])
    _rg, [b] = _complete(reg, egeria, "ea-rates1", "re-change-rates")
    assert _envelope(b)["resultState"] == "not_established" and _envelope(b)["scope"] == "coco_pharma"
    assert b["summary"] == "not measured · one activity snapshot only; a rate needs two"


# ══ re-hub-tables ══════════════════════════════════════════════════════════════════

def _keys(reg, refs: dict[str, list[str]], *, captured=True):
    """A newer column survey of coco_pharma: every table an id key, one FK column per referenced table."""
    reg.record_database_survey(slug="coco_pharma", schema_count=1, table_count=len(refs), column_count=0,
                               survey_data={}, source="local", surveyed_at=T1)
    reg.write_detail_rows("database_tables", "coco_pharma", T1, source="local", rows=[
        {"schema_name": "public", "table_name": t, "table_type": "BASE TABLE"} for t in refs])
    cols = []
    for t, targets in refs.items():
        cols.append({"schema_name": "public", "table_name": t, "column_name": "id",
                     "is_primary_key": 1 if captured else None})
        cols += [{"schema_name": "public", "table_name": t, "column_name": f"{r}_id",
                  "is_primary_key": 0 if captured else None,
                  "foreign_key_json": {"foreign_schema": "public", "foreign_table": r, "foreign_column": "id"}}
                 for r in targets]
    reg.write_detail_rows("database_columns", "coco_pharma", T1, source="local", rows=cols)


def test_hub_tables_publishes_degrees_and_a_proposal_never_a_classification(install_steps, reg, egeria,
                                                                            no_connection):
    install_steps("hub_tables")
    _keys(reg, {"customer": [], "store": [], "orders": ["customer", "store"], "returns": ["customer", "orders"],
                "loyalty": ["customer"]})
    _rg, bodies = _complete(reg, egeria, "ea-hubs", "re-hub-tables")
    degrees = {_envelope(b)["scope"]: b for b in bodies if b["annotationType"] == "Hub Tables"}
    assert set(degrees) == {"public.customer", "public.store", "public.orders", "public.returns", "public.loyalty"}
    customer = degrees["public.customer"]
    assert customer["class"] == "ResourceMeasureAnnotationProperties" and customer["analysisStep"] == "hub_tables"
    assert (customer["resourceProperties"]["Foreign Keys In"], customer["resourceProperties"]["Foreign Keys Out"]) \
        == ("3", "0")
    [hub] = [b for b in bodies if b["annotationType"] == "Hub Table Proposal"]
    assert hub["class"] == "ClassificationAnnotationProperties"
    # Egeria 6.2's own fields for the type (the fake refuses any other, as Egeria would silently drop it)
    assert hub["classificationName"] == "hub table" and hub["contentStatus"] == "DRAFT"
    assert "candidateClassifications" not in hub and "jsonProperties" not in hub
    assert [d for d in egeria.dropped if d["analysisStep"] == "hub_tables"] == []     # nothing Egeria would drop
    assert hub["properties"]["Foreign Keys In"] == "3" and hub["properties"]["Hub Threshold"] == "3"
    assert hub["analysisStep"] == "hub_tables" and "referenced by 3 other tables" in hub["summary"]
    assert _envelope(hub)["scope"] == "public.customer" and _envelope(hub)["measure"] == "hub_table_proposal"
    assert _envelope(hub)["resultState"] == "measured"
    # the proposal is an annotation only: nothing classified any table, no classification call at all on a table
    assert not [c for c in egeria.calls if "classif" in c[1].lower() and c[1] not in (
        "add_ownership_to_element", "add_zone_membership")], egeria.calls
    _run, rows = reg.query_step_annotations("database", "coco_pharma", "hub_tables:hub_table_proposal")
    [stored] = rows
    assert stored["annotation_type"] == "ClassificationAnnotation"
    assert stored["detail"]["candidateClassifications"] == ["hub table"]
    # and it reads back from Egeria's fields as the proposal it is
    from resource_explorer.surveyors.egeria_reader import _parse_annotation

    back = _parse_annotation({"guid": "g", "type": {"typeName": "ClassificationAnnotation"}}, hub)
    assert back["subtype_data"]["classificationName"] == "hub table"
    assert back["subtype_data"]["candidateClassifications"] == ["hub table"]
    assert back["subtype_data"]["properties"]["Foreign Keys In"] == "3"
    assert back["additional_properties"]["measure"] == "hub_table_proposal"


def test_the_fake_drops_what_egeria_drops(egeria):
    """The guard behind the hub test: a ClassificationAnnotation field Egeria 6.2's bean does not have is stored
    without it (as Egeria does, silently) and the drop is recorded."""
    from tests.test_engine_host_e4 import _CLASSIFICATION_ANNOTATION_TYPEDEF

    assert _CLASSIFICATION_ANNOTATION_TYPEDEF == ("classificationName", "properties")
    guid = egeria._create_annotation({"properties": {"class": "ClassificationAnnotationProperties",
                                                     "analysisStep": "x", "classificationName": "hub table",
                                                     "candidateClassifications": ["hub table"]}})
    assert egeria.elements[guid]["body"]["properties"] == {"class": "ClassificationAnnotationProperties",
                                                           "analysisStep": "x", "classificationName": "hub table"}
    assert egeria.dropped == [{"class": "ClassificationAnnotationProperties", "analysisStep": "x",
                               "fields": ["candidateClassifications"]}]


def test_hub_tables_with_no_keys_captured_completes_with_the_absence(install_steps, reg, egeria, no_connection):
    install_steps("hub_tables")
    _keys(reg, {"customer": [], "orders": ["customer"]}, captured=False)
    _rg, bodies = _complete(reg, egeria, "ea-hubs0", "re-hub-tables")
    assert {b["annotationType"] for b in bodies} == {"Hub Tables", "Hub Table Proposal"}
    for b in bodies:
        assert _envelope(b)["resultState"] == "not_established"
        assert b["summary"] == "not measured · no keys captured; hubs cannot be derived"


def test_hub_tables_is_admitted_with_its_classification_measure_and_an_unpublishable_type_still_is_not(
        install_steps, monkeypatch):
    from trellis_analytics.manifest import StepManifest

    from resource_explorer.surveyors import survey_step_host

    install_steps("hub_tables")
    assert survey_step_host.library_steps()["re-hub-tables"].step_key == "hub_tables"
    graded = StepManifest(name="graded", function="x.y", description="d", source="ods", version="1",
                          resource_types=["database"], measures=["graded", "grade"],
                          measure_annotation_types={"grade": "QualityAnnotation"})
    real = survey_step_host.discover_steps
    monkeypatch.setattr(survey_step_host, "discover_steps",
                        lambda entity_type: {**real(entity_type), "graded": graded})
    survey_step_host.refresh("database")
    assert "re-graded" not in survey_step_host.library_steps()
    assert "QualityAnnotation" in survey_step_host.library_step_refusals()["graded"]


@pytest.mark.parametrize("step", ["statistics_freshness", "hub_tables", "change_rates"])
def test_each_step_reads_only_through_the_typed_reads(install_steps, reg, egeria, no_connection, monkeypatch, step):
    """What the step's code is handed is the typed reads and nothing else; a call to anything else is refused."""
    import importlib

    from resource_explorer.surveyors.survey_step_host import STEP_ODS_METHODS

    install_steps(step)
    user_code = importlib.import_module(f"{step}.step")
    seen = {}
    real = user_code.run

    def spy(inputs):
        seen["attrs"] = {a for a in dir(inputs.ods) if not a.startswith("_")}
        try:
            inputs.ods.query("select 1")
        except AttributeError as exc:
            seen["query"] = str(exc)
        return real(inputs)

    monkeypatch.setattr(user_code, "run", spy)
    request_type = "re-" + step.replace("_", "-")
    egeria.add_action("ea-spy", request_type=request_type)
    _sweep(reg)
    _run_queue_once(reg)
    assert seen["attrs"] == set(STEP_ODS_METHODS) and "query" in seen["query"]
    assert egeria.actions["ea-spy"]["completion"]["guards"] == ["survey-completed"]
