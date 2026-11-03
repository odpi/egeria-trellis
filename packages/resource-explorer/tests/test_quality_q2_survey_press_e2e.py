"""Quality slice Q2, end to end through the real route: the Survey press on DatabaseQualitySurvey.

POST /api/survey-definitions/database/{slug}/run enqueues a `survey_definition_run`; the queue's worker claims it and
runs the committed definition's three steps through the real executor, the real database adapter runners, the real
value profile, propose and run-checks code, the real registry (temp SQLite) and the real database survey publisher.

Faked only at the boundaries:
- the surveyed Postgres: `FakeConn` (the value-profile tests' canned answers);
- Egeria: every pyegeria client RE builds is `create_autospec(<real class>, spec_set=True)` behind
  `FakeEgeria` (test_engine_host_e4), whose annotation create keeps only the fields Egeria 6.2's bean has;
- reading the definition: `SurveyDefinitionReader.find_process_guid_by_name` / `.fetch` (autospec) answer from the
  COMMITTED document, which is what Egeria holds once the coordinator has applied it.

The first press has no accepted check: its third step ends with the "no accepted checks" sentence, and the report it
leaves holds the value profile and ONE request for action ("review N proposed quality checks in Resource Explorer"),
never an empty report. The second, after three proposals are accepted, adds three QualityAnnotations to its report,
which the test reads back by GUID: the words are counted from that read, the annotations are linked to the columns they
describe, and the report is owned by the person who pressed.
"""
from __future__ import annotations

import json
import re
from unittest.mock import patch

import pytest
from tests.qn_lookup_bridge import convert_answer
from fastapi.testclient import TestClient

from resource_explorer.surveyors.database.quality.run_checks import NO_ACCEPTED_CHECKS
from tests.egeria_annotation_beans import ACCEPTED_FIELDS
from tests.test_engine_host_e4 import _run_queue_once, egeria  # noqa: F401 - fixtures reused
from tests.test_value_profile_quality_checks import (  # noqa: F401 - fixtures and helpers reused
    PROFILE,
    SLUG,
    FakeConn,
    _connect,
    _orders_measure,
    registry,
)

QUALITY = "DatabaseQualitySurvey"
PRESSER = "peterprofile"


@pytest.fixture(autouse=True)
def _no_live_database(monkeypatch):
    """Nothing in this file may reach a database: psycopg2 itself refuses."""
    import psycopg2

    def refuse(*a, **k):
        raise AssertionError("a test opened a real PostgreSQL connection")

    monkeypatch.setattr(psycopg2, "connect", refuse)


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    registry.update_database_credentials(SLUG, "surveyor", "stored-pass-0123456789")
    monkeypatch.setattr(type(registry), "has_assigned_egeria_project", lambda self, *a, **k: True)
    # this process runs the queue loop that takes the row (the embedded worker), so the press holds the session
    monkeypatch.setattr("resource_explorer.run_queue.consumes_kind_here", lambda kind: True)
    # signed in: the press publishes as the person who pressed it
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    monkeypatch.setattr("resource_explorer.auth.get_current_user",
                        lambda request: {"user_id": PRESSER, "egeria_token": "t"})
    from resource_explorer.web.app import app

    return TestClient(app)


@pytest.fixture
def world(registry, egeria, monkeypatch):
    """The Postgres and definition boundaries, and a connection that records every statement."""
    from resource_explorer.surveyors.database import connection
    from resource_explorer.surveyors.survey_definition_docs import documented_definitions
    from resource_explorer.surveyors.survey_definition_reader import (
        StepLink,
        SurveyDefinition,
        SurveyDefinitionReader,
        SurveyStep,
    )

    conn = FakeConn(profile=PROFILE, checks={("sales", "orders"): {"record_ct": 1000, "measure": _orders_measure}})
    monkeypatch.setattr(connection, "database_connection", lambda *a, **k: _connect(conn)())

    doc = documented_definitions()[QUALITY]
    steps = [SurveyStep(guid=f"guid-{k}", display_name=doc.step_info[k]["display_name"],
                        qualified_name=doc.step_info[k]["qualified_name"], executes_at=doc.step_info[k]["executes_at"],
                        re_analysis_step=doc.step_info[k]["re_analysis_step"],
                        supported_technology_type=doc.technology_type) for k in doc.steps]
    definition = SurveyDefinition(
        process_guid="guid-process", display_name=doc.display_name, qualified_name=f"GovActionProcess::{QUALITY}",
        supported_technology_type=doc.technology_type, steps=steps,
        links=[StepLink(f"guid-{a}", f"guid-{b}", guard) for a, b, guard in doc.links], survey_kind=doc.survey_kind)
    with patch.object(SurveyDefinitionReader, "find_process_guid_by_name", autospec=True,
                      side_effect=lambda self, name, *a, **k: "guid-process" if name == QUALITY else "") as find, \
            patch.object(SurveyDefinitionReader, "fetch", autospec=True,
                         side_effect=lambda self, guid, *a, **k: definition) as fetch:
        yield conn
    assert find.called and fetch.called


#: sales.orders.note has no column element in Egeria (the cataloguer has not made it); the others do, and so does the
#: table. Read by the surveyor's by-name look-ups.
COL_ORDER_ID = "0c0c0c0c-0000-4000-8000-000000000001"
COL_PLACED = "0c0c0c0c-0000-4000-8000-000000000002"
TBL_ORDERS = "0c0c0c0c-0000-4000-8000-000000000003"
ELEMENTS_IN_EGERIA = {"orders.order_id": COL_ORDER_ID, "orders.placed": COL_PLACED, "orders": TBL_ORDERS}


@pytest.fixture
def rig(registry, egeria):
    """What the e2e fake leaves out and the quality publish needs: by-name look-ups that find the database, the
    catalogued column and table elements and the annotations already published, an `AssociatedAnnotation` create that
    records its relationship, and a read of those relationships. Installed behind FakeEgeria's own wiring, so every
    call still binds the real pyegeria signature (autospec, spec_set)."""
    from resource_explorer.catalogue_gateway import database_qualified_name, schema_qualified_name, server_name_for
    from tests.test_engine_host_e4 import DB_GUID, _uuid

    entity = registry.get_database(SLUG)
    server = server_name_for(entity)
    schema_qn = schema_qualified_name(server, entity.database_name, "sales")
    present = {f"{schema_qn}::{path.replace('.', '::')}": guid for path, guid in ELEMENTS_IN_EGERIA.items()}
    db_qn = database_qualified_name(server, entity.database_name)

    def lookup(name, *a, **k):
        if name == db_qn:
            return DB_GUID
        if name in present:
            return present[name]
        if name.startswith("Annotation::"):
            for guid, element in egeria.elements.items():
                if element["type"] == "Annotation" and element["body"]["properties"].get("qualifiedName") == name:
                    return guid
        return "No elements found"

    def link(annotation_guid, element_guid, body):
        egeria.relationships.append({"type": "AssociatedAnnotation", "end1": element_guid, "end2": annotation_guid,
                                     "guid": _uuid()})

    original_related = egeria._related

    def related(guid, relationship_type=None, body=None, *args, starting_at_end=0, **kwargs):
        if relationship_type != "AssociatedAnnotation":
            return original_related(guid, relationship_type, body, *args, starting_at_end=starting_at_end, **kwargs)
        near, far = ("end2", "end1") if starting_at_end == 2 else ("end1", "end2")
        hits = [r for r in egeria.relationships if r["type"] == relationship_type and r[near] == guid]
        if not hits or kwargs.get("start_from"):
            return "No elements found"
        return {"elementList": [{"relationshipGUID": r.get("guid", ""), "element": {"elementGUID": r[far]}}
                                for r in hits]}

    original_wire = egeria._wire

    def wire(cls, spec):
        original_wire(cls, spec)
        for name, method, fn in (("MetadataExpert", "get_metadata_element_by_unique_name",
                                  lambda name=None, property_name="qualifiedName", body=None, **k:
                                  convert_answer(lookup(name))),
                                 ("DataDiscovery", "link_annotation_to_described_element", link),
                                 ("MetadataExpert", "get_related_metadata_elements", related)):
            if cls.__name__ == name:
                def inner(*args, _m=method, _n=name, _f=fn, **kwargs):
                    egeria._record(_n, _m)
                    return _f(*args, **kwargs)
                getattr(spec, method).side_effect = inner

    egeria._wire = wire
    return present


def _press(client, registry, *, as_engine: str = "resource-explorer") -> dict:
    """The Survey press: the real route, then the queue worker runs what the route queued. Returns the activity
    entry's stored detail, which is what the run modal renders."""
    resp = client.post(f"/api/survey-definitions/database/{SLUG}/run",
                       json={"survey_definition_ref": QUALITY, "engine": as_engine})
    assert resp.status_code == 200, resp.text
    queued = resp.json()
    assert queued["status"] == "started" and queued["run_id"] and queued["activity_id"]
    row = registry.list_runs(state="queued")[0]
    assert row["kind"] == "survey_definition_run" and json.loads(row["target"])["slug"] == SLUG
    assert _run_queue_once(registry) is not None
    entry = registry.get_activity(queued["activity_id"])
    return {"status": entry["status"], "summary": entry["summary"], **json.loads(entry["detail"])}


def _steps(detail: dict) -> dict:
    return {s["re_analysis_step"]: s for s in detail["steps"]}


def _accept_three_proposals(registry) -> list[str]:
    """What a steward does in Curate: accept three of the proposals the second step made."""
    proposals = {r["check_key"]: r for r in registry.query_detail_rows("database_quality_proposals", SLUG)}
    keys = ["sales.orders.order_id::Unique", "sales.orders.placed::Required", "sales.orders.note::Missing_Pct"]
    for key in keys:
        assert proposals[key]["state"] == "proposed", key
        registry.record_quality_verdict(SLUG, key, "accepted", proposals[key]["params_json"],
                                        proposal_id=proposals[key]["id"], decided_by="steward")
    return keys


def _annotation_bodies(egeria, report_guid) -> list[dict]:
    """The annotation properties Egeria holds under a report, as the fake keeps them (what the bean keeps)."""
    return [egeria.elements[r["end2"]]["body"]["properties"] for r in egeria.relationships
            if r["end1"] == report_guid and r["type"] == "ReportedAnnotation"]


def _by_class(bodies) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for body in bodies:
        out.setdefault(body["class"], []).append(body)
    return out


def _utc_minute(at: str) -> str:
    """A report time as the card names it: date and time in UTC with Z (round 2, ruling (a))."""
    from datetime import UTC, datetime

    instant = datetime.fromisoformat(at)
    instant = instant.replace(tzinfo=UTC) if instant.tzinfo is None else instant.astimezone(UTC)   # RE writes UTC
    return instant.strftime("%Y-%m-%d %H:%MZ")


def _proposed_count(registry) -> int:
    return sum(1 for r in registry.query_detail_rows("database_quality_proposals", SLUG) if r["state"] == "proposed")


def test_the_press_with_no_accepted_check_leaves_the_profile_and_one_request_never_an_empty_report(
        client, registry, egeria, world, rig):
    detail = _press(client, registry)
    steps = _steps(detail)
    assert steps["postgres_value_profile"]["status"] == "ok" and steps["propose_quality_checks"]["status"] == "ok"
    # the third step is declined with the design's sentence: not run, not "ran and found nothing"
    assert steps["run_quality_checks"]["status"] == "skipped_by_design"
    assert steps["run_quality_checks"]["detail"] == NO_ACCEPTED_CHECKS
    assert NO_ACCEPTED_CHECKS.startswith("no accepted checks")
    assert not any(s.startswith("SELECT COUNT(*) AS record_ct, ") for s in world.statements)   # no check was run
    assert registry.query_detail_rows("database_quality_results", SLUG) == []
    # The report is NOT empty: the value profile's annotations and ONE RequestForAction for the proposals (A.1).
    [(report_guid, _)] = egeria.reports()
    assert detail["egeria_report_guid"] == report_guid
    classes = _by_class(_annotation_bodies(egeria, report_guid))
    assert "QualityAnnotationProperties" not in classes and classes["ResourceProfileAnnotationProperties"]
    [request] = classes["RequestForActionProperties"]
    proposed = _proposed_count(registry)
    assert proposed > 0
    assert request["actionRequested"] == f"review {proposed} proposed quality checks in Resource Explorer"
    batch = registry.query_detail_rows("database_quality_proposals", SLUG)[0]["surveyed_at"]
    assert request["additionalProperties"]["proposedChecks"] == str(proposed)
    assert request["additionalProperties"]["runId"] == batch
    assert egeria.dropped == []
    # the words come from reading the report back: every annotation sent is in it
    total = sum(len(v) for v in classes.values())
    # the first profile publish: every measure created, nothing to compare with (profile publish reshape)
    profile = len(classes["ResourceProfileAnnotationProperties"])
    assert detail["publish_state"] == (f"published to Egeria · {total} of {total} annotations in Egeria"
                                       f" · {profile} measures published · first profile published")
    assert detail["published"] is True


def test_the_value_profile_annotations_are_published_by_a_press(client, registry, egeria, world, rig):
    """M3: `_publish` used to ignore the step's `annotations`, so only the engine host ever published them."""
    _press(client, registry)
    [(report_guid, _)] = egeria.reports()
    profile = _by_class(_annotation_bodies(egeria, report_guid))["ResourceProfileAnnotationProperties"]
    measured = [r for r in registry.query_detail_rows("database_value_profiles", SLUG) if r["state"] == "measured"]
    assert len(profile) == len(measured) and {p["analysisStep"] for p in profile} == {"postgres_value_profile"}


def test_the_report_a_press_makes_is_owned_by_the_person_who_pressed_it(client, registry, egeria, world, rig):
    """M1, 2026-10-21 (architect ruling, option a): the queued run publishes AS the person who pressed it, with their
    held session, so Egeria's provenance names them and the Ownership on the report names them too. Before: the queue's
    service account committed it (`createdBy = resourceexplorernpa`) with the presser only recorded as the requester."""
    _press(client, registry)
    [(report_guid, _)] = egeria.reports()
    owners = [b for kind, g, b in egeria.classifications if kind == "Ownership" and g == report_guid]
    assert owners and owners[0]["properties"]["owner"] == PRESSER, owners
    writers = [(w.kind, w.user_id, w.token, w.is_service_account)
               for cls, method, w in egeria.calls if method == "add_ownership_to_element"]
    assert writers and set(writers) == {("caller", PRESSER, "t", False)}


def test_accept_three_proposals_press_again_and_read_three_quality_annotations_back_by_guid(
        client, registry, egeria, world, rig):
    first = _press(client, registry)
    assert _steps(first)["run_quality_checks"]["status"] == "skipped_by_design"
    # the first report's run date ("SurveyReport::PostgreSQL::<slug>::<surveyed_at>"): what the card compares with
    first_date = _utc_minute(egeria.reports()[0][1]["properties"]["qualifiedName"].rsplit("::", 1)[1])
    keys = _accept_three_proposals(registry)

    detail = _press(client, registry)
    assert _steps(detail)["run_quality_checks"]["status"] == "ok"
    (_, _), (report_guid, report_body) = egeria.reports()
    assert detail["egeria_report_guid"] == report_guid and detail["published"] is True
    bodies = _annotation_bodies(egeria, report_guid)
    # the words come from reading the report back: "N of M", every annotation this publish sent, linked to 2 of the
    # 3 elements (sales.orders.note has no element in Egeria, and that is said, not an error)
    assert detail["publish_state"] == (f"published to Egeria · {len(bodies)} of {len(bodies)} annotations in Egeria"
                                       " · 3 checks on the report · linked to 2 of 3 elements"
                                       " · 1 column not yet cataloged in Egeria, so not linked to it"
                                       # nothing changed in the database: every measure re-used, none created
                                       f" · 5 measures published · 0 changed since {first_date} · 5 re-used"
                                       " · 0 no longer measured")
    assert detail["status"] != "error" and not detail["publish_state"].startswith("publish failed")

    # read the three annotations back by GUID, as the live gate does
    quality_guids = [r["end2"] for r in egeria.relationships if r["end1"] == report_guid
                     and r["type"] == "ReportedAnnotation"
                     and egeria.elements[r["end2"]]["body"]["properties"]["class"] == "QualityAnnotationProperties"]
    assert len(quality_guids) == 3
    stored = {egeria.elements[g]["body"]["properties"]["additionalProperties"]["checkKey"]:
              egeria.elements[g]["body"] for g in quality_guids}
    assert set(stored) == set(keys)
    for key, body in stored.items():
        assert body["anchorGUID"] == report_guid and body["isOwnAnchor"] is False
        props = body["properties"]
        assert props["class"] == "QualityAnnotationProperties"
        assert props["analysisStep"] == "run_quality_checks" and props["annotationType"] == "Database Quality Check"
        assert not set(props) - ACCEPTED_FIELDS["QualityAnnotationProperties"]
        assert props["sampleSize"] == 1000                       # the records the check read
    unique = stored["sales.orders.order_id::Unique"]["properties"]
    assert (unique["qualityDimension"], unique["qualityScore"]) == ("uniqueness", 100)
    assert unique["additionalProperties"]["checkState"] == "passed"
    required = stored["sales.orders.placed::Required"]["properties"]
    assert (required["qualityDimension"], required["qualityScore"]) == ("completeness", 0)
    assert required["additionalProperties"]["checkState"] == "failed"
    missing = stored["sales.orders.note::Missing_Pct"]["properties"]
    assert (missing["qualityDimension"], missing["qualityScore"]) == ("completeness", 0)
    assert egeria.dropped == [], "Egeria's bean would have dropped fields RE sent"
    # Identity (owner rule "run as the initiating user"): the queued press commits AS the person who pressed it (their
    # session, held in memory for the run: `presser_hold`), never as RE's queue identity and never as the engine host.
    writers = [who for cls, method, who in egeria.calls if method == "create_annotation"]
    assert writers and all((w.kind, w.user_id, w.token, w.is_service_account) == ("caller", PRESSER, "t", False)
                           for w in writers)
    # and the results the annotations summarise are the registry's result rows
    results = {r["check_key"]: r["status"] for r in registry.query_detail_rows("database_quality_results", SLUG)}
    assert results == {"sales.orders.order_id::Unique": "pass", "sales.orders.placed::Required": "fail",
                       "sales.orders.note::Missing_Pct": "warning"}
    # M2: each is linked to the column it describes (AssociatedAnnotation, element at end 1), once; the column with no
    # element is not linked and is not an error
    linked = {r["end1"]: egeria.elements[r["end2"]]["body"]["properties"]["additionalProperties"]["checkKey"]
              for r in egeria.relationships if r["type"] == "AssociatedAnnotation"}
    assert linked == {COL_ORDER_ID: "sales.orders.order_id::Unique", COL_PLACED: "sales.orders.placed::Required"}


def test_a_table_check_is_linked_to_its_table_element(client, registry, egeria, world, rig):
    _press(client, registry)
    proposals = {r["check_key"]: r for r in registry.query_detail_rows("database_quality_proposals", SLUG)}
    key = "sales.orders::Row_Ct_Pct"
    assert proposals[key]["state"] == "proposed"
    registry.record_quality_verdict(SLUG, key, "accepted", proposals[key]["params_json"],
                                    proposal_id=proposals[key]["id"], decided_by="steward")
    detail = _press(client, registry)
    assert "· 1 check on the report · linked to 1 of 1 elements ·" in detail["publish_state"]
    linked = [(r["end1"], egeria.elements[r["end2"]]["body"]["properties"]["additionalProperties"]["checkKey"])
              for r in egeria.relationships if r["type"] == "AssociatedAnnotation"]
    assert linked == [(TBL_ORDERS, key)]


def test_the_press_says_two_of_n_when_egeria_keeps_all_but_one(client, registry, egeria, world, rig):
    """One quality annotation's create is refused. The words and the flag both follow the read-back, so neither says
    'published'."""
    _press(client, registry)
    _accept_three_proposals(registry)
    before = len(egeria.method_calls("create_annotation"))
    sent_before_quality = _report_size_before_quality(egeria)
    # the second press RE-LINKS the unchanged profile measures instead of creating them (profile publish reshape), and
    # the unchanged not-profiled tables' measures (round 2); the request changed (three accepted), so it is created
    [(first, _)] = egeria.reports()[:1]
    classes = _by_class(_annotation_bodies(egeria, first))
    relinked = len(classes["ResourceProfileAnnotationProperties"]) + sum(
        1 for b in classes.get("ResourceMeasureAnnotationProperties", []) if b["analysisStep"] == "postgres_value_profile")
    egeria.fail_nth["create_annotation"] = (before + sent_before_quality - relinked + 2,
                                            RuntimeError("Egeria refused the second quality annotation"))

    detail = _press(client, registry)
    (_, _), (report_guid, _) = egeria.reports()
    held = len(_annotation_bodies(egeria, report_guid))
    sent = sent_before_quality + 3
    assert held == sent - 1
    assert detail["publish_state"].startswith(f"publish failed · {held} of {sent} annotations in Egeria")
    assert any("did not all reach Egeria" in e for e in detail["errors"])
    assert detail["status"] == "error"
    assert detail["published"] is False          # the flag is derived from the same read-back as the words


def _report_size_before_quality(egeria) -> int:
    """How many non-quality annotations a press sends: what the first press's report held."""
    [(first, _)] = egeria.reports()[:1]
    return len(_annotation_bodies(egeria, first))


def test_a_read_back_that_cannot_be_made_is_could_not_be_counted_never_published(client, registry, egeria, world, rig):
    """A GUID returned by a create is not a read-back: with the read refused the run does not say it published."""
    _press(client, registry)
    _accept_three_proposals(registry)
    # the second press reads the report's list first (before re-linking the unchanged profile measures: two pages,
    # the second one empty, which is how the paged reader knows it reached the end), then reads it back: the
    # READ-BACK's first page is the one refused
    before = len(egeria.method_calls("get_related_metadata_elements"))
    egeria.fail_nth["get_related_metadata_elements"] = (before + 3, RuntimeError("Egeria would not answer the read"))
    detail = _press(client, registry)
    assert detail["publish_state"].startswith("publish failed · annotations in Egeria could not be counted")
    assert detail["published"] is False
