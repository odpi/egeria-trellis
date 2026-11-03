"""Curate's publish table (declared extensibility slice 1, PR 2; DESIGN-DECLARED-EXTENSIBILITY-RENDERING-CHAT-
CONFIDENCE.md §5 and the gate of §7).

The gate, by use, with no RE code written for the analysis: a step declared with two measures and no annotation
type appears as two rows reading "generic measure · no native type declared"; one is chosen Publish, rides in RE's
survey report, and reads back by name; adding the annotation type to the declaration changes the row's "writes as"
to the native type, and the next publish writes it so. Never reaches Egeria and never the shared registry: every
registry is a tmp SQLite file, and Egeria is a fake at the pyegeria client boundary (the surveyor's own publish and
read-back code is real).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from trellis_analytics.declaration import Declaration

from resource_explorer import publish_table as pt
from resource_explorer.facts import Fact
from resource_explorer.registry import DatabaseEntity, ProjectRegistry

SLUG = "coco_pharma"
SURVEYED_AT = "2026-10-27T09:00:00"
ABSENCE = {"not_measured": "Example step x has not run on this database.",
           "not_permitted": "The credential cannot read the tables example step x samples.",
           "not_applicable": "This database has no tables for example step x.",
           "measured_nothing": "Example step x ran and found no buckets."}


KIND_BLOCKS = {"distribution": {"categories": {"exclusive": True}},
               "proposal": {"proposal": {"candidate_field": "bucket", "decided_by": "curator"}},
               "list": {"listing": {"item_fields": ["bucket", "count"], "order": "by count", "key_field": "bucket"}}}


def _declaration(annotation: dict | None = None, kind: str = "distribution") -> Declaration:
    second = {"name": "rows_sampled", "unit": "rows", "as_of": "asOf", "value_field": "rows_sampled"}
    if annotation:
        second["annotation"] = annotation
    return Declaration.model_validate({
        "result_kind": kind,
        "measures": [{"name": "buckets", "unit": "buckets", "as_of": "asOf", "value_field": "buckets[]"}, second],
        "absence": ABSENCE,
        "extents": [{"name": "top", "fields": ["bucket", "count"], "rows": 10, "order": "by count, largest first",
                     "truncation": "{shown} of {total} buckets shown", "token_cost": 120,
                     "citation": "{analysis} · {scope} · as of {as_of}"}],
        "confidence": {"form": "estimated", "basis": "sample_ratio", "method": "one row in ten, sampled"},
        "answers": ["q-example-x"], "scope": "resource",
        **KIND_BLOCKS.get(kind, {})})


def _entry(declaration, id_="example_step_x", name="Example Step X", source="local", provided_by=None):
    return SimpleNamespace(id=id_, name=name, declaration=declaration, source=source,
                           provided_by=dict(provided_by or {}))


VALUE = {"buckets": [{"bucket": "a", "count": 3}, {"bucket": "b", "count": 2}], "rows_sampled": 1200,
         "sample_ratio": 0.1, "asOf": "2026-10-27T08:30:00"}


class FakeLayer:
    """The fact layer, answering from a dict: analysis id -> Fact."""

    def __init__(self, facts: dict[str, Fact]):
        self.facts = facts

    def fact(self, slug, analysis_id, level="resource"):
        return self.facts.get(analysis_id) or Fact(analysis_id=analysis_id, state="never_run")


@pytest.fixture
def registry(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    reg.register_database(DatabaseEntity(slug=SLUG, display_name="Coco Pharma", db_type="postgresql",
                                         host="localhost", egeria_host="host.docker.internal", port=5442,
                                         database_name="coco_pharma"))
    return reg


@pytest.fixture
def declared(monkeypatch):
    """The catalogue the table reads: one entry, its declaration swappable; the fact layer answers VALUE."""
    world = {"entries": [_entry(_declaration())],
             "facts": {"example_step_x": Fact(analysis_id="example_step_x", state="measured", value=dict(VALUE),
                                               last_run_at="2026-10-27T08:31:00")}}
    monkeypatch.setattr(pt, "_entries", lambda entity_type: world["entries"])
    monkeypatch.setattr("resource_explorer.facts.FactLayer", lambda registry, resource_type: FakeLayer(world["facts"]))
    return world


def _rows(registry):
    return {r["measure"]: r for r in pt.build_rows(registry, "database", SLUG)["rows"]}


# ── the rows ────────────────────────────────────────────────────────────────────────────────────────────────────


def test_two_declared_measures_are_two_generic_rows_with_unit_as_of_confidence_and_scope(registry, declared):
    rows = _rows(registry)
    assert set(rows) == {"buckets", "rows_sampled"}
    b, r = rows["buckets"], rows["rows_sampled"]
    assert b["value"] == 2 and b["value_words"] == "2 buckets"            # `buckets[]`: the list, counted
    assert r["value"] == 1200 and r["value_words"] == "1200 rows"
    for row in (b, r):
        assert row["analysis"] == "Example Step X"
        assert row["writes_as"]["words"] == pt.GENERIC_WORDS == "generic measure · no native type declared"
        assert row["as_of"] == "2026-10-27T08:30:00"
        assert row["confidence"]["words"] == "estimated · one row in ten, sampled"
        assert row["scope"] == "resource"
        assert row["publishable"] and row["choice"] is None
        assert row["published"] == {"state": "local", "words": "local · not published"}


def test_a_proposal_is_not_on_the_table(registry, declared):
    declared["entries"] = [_entry(_declaration(kind="proposal"))]
    assert pt.build_rows(registry, "database", SLUG)["rows"] == []


def test_a_never_run_analysis_shows_its_declared_sentence_and_cannot_publish(registry, declared):
    declared["facts"] = {}
    row = _rows(registry)["buckets"]
    assert row["value"] is None and row["value_words"] == "—"
    assert row["absence"] == "not_measured" and row["absence_words"] == ABSENCE["not_measured"]
    assert not row["publishable"] and row["why_not"] == f"nothing to publish · {ABSENCE['not_measured']}"


def test_a_measured_result_without_the_field_is_measured_nothing_not_never_run(registry, declared):
    declared["facts"]["example_step_x"].value = {"asOf": "2026-10-27T08:30:00", "other": 1}
    row = _rows(registry)["rows_sampled"]
    assert row["absence"] == "measured_nothing" and row["absence_words"] == ABSENCE["measured_nothing"]


def test_a_count_its_own_check_did_not_measure_is_not_measured_nothing(registry, declared):
    """F42: `checks.<x>.count` is None because the check's own state says so; the row says that, not 'found nothing'."""
    dec = declared["entries"][0].declaration
    dec.measures[1].value_field = "checks.docs.count"
    declared["facts"]["example_step_x"].value = {"asOf": "2026-10-27T08:30:00", "buckets": [],
        "checks": {"docs": {"state": "not_measured", "count": None, "total": 4,
                            "explanation": "Not one table carries a description."}}}
    row = _rows(registry)["rows_sampled"]
    assert row["absence"] == "not_measured"
    assert row["absence_words"].endswith("Not one table carries a description.")
    declared["facts"]["example_step_x"].value["checks"]["docs"].update(state="measured", explanation="")
    assert _rows(registry)["rows_sampled"]["absence"] == "measured_nothing"


def test_a_number_without_its_as_of_does_not_publish(registry, declared):
    declared["facts"]["example_step_x"].value = {"rows_sampled": 5, "buckets": []}
    row = _rows(registry)["rows_sampled"]
    assert row["as_of"] == "" and not row["publishable"]
    assert row["why_not"] == "no as-of: a number never travels without its as-of"


def test_a_native_type_this_publisher_cannot_write_is_named_and_offers_keep_only(registry, declared):
    declared["entries"] = [_entry(_declaration({"type": "DataClassAnnotation"}))]
    row = _rows(registry)["rows_sampled"]
    assert row["writes_as"]["words"] == "DataClassAnnotation" and row["writes_as"]["route"] == "native"
    assert not row["publishable"] and "does not write DataClassAnnotation from a declaration yet" in row["why_not"]
    with pytest.raises(pt.ChoiceRefused, match="cannot publish"):
        pt.save_choice(registry, "database", SLUG, analysis_id="example_step_x", measure="rows_sampled",
                       choice=pt.PUBLISH, author="dwolfson")
    kept = pt.save_choice(registry, "database", SLUG, analysis_id="example_step_x", measure="rows_sampled",
                          choice=pt.KEEP, author="dwolfson")
    assert kept["choice"]["choice"] == pt.KEEP


def test_run_fields_resolve_from_the_run_the_reader_carried(registry, declared):
    """`run.read_snapshot`: a zero-fetch derivation's as-of is the newest stored row it read, carried by the reader
    under `_run`; `run.surveyed_at` falls back to the fact layer's run time."""
    dec = _declaration()
    dec.measures[0].as_of = "run.read_snapshot"
    dec.measures[1].as_of = "run.surveyed_at"
    declared["entries"] = [_entry(dec)]
    declared["facts"]["example_step_x"].value = {**VALUE, "_run": {"read_snapshot": "2026-10-20T07:00:00"}}
    rows = _rows(registry)
    assert rows["buckets"]["as_of"] == "2026-10-20T07:00:00"
    # A derivation has no measurement time of its own: the fact layer's run time is not a stand-in (F18).
    assert rows["rows_sampled"]["as_of"] == ""
    declared["facts"]["example_step_x"].value = {**VALUE}
    assert _rows(registry)["rows_sampled"]["as_of"] == "2026-10-27T08:31:00"


def test_a_derived_rows_measured_date_is_its_read_snapshot_never_the_recompute_clock(registry, declared):
    """F18: `_run.computed_at` is stamped on every read; the second date a derived row shows is `read_snapshot`."""
    declared["entries"] = [_entry(_declaration())]
    declared["facts"]["example_step_x"].value = {**VALUE, "_run": {"read_snapshot": "2026-10-20T07:00:00",
                                                                   "computed_at": "2026-10-29T18:48:00"}}
    assert _rows(registry)["buckets"]["measured_at"] == "2026-10-20T07:00:00"
    assert _rows(registry)["buckets"]["derived"] is True
    declared["facts"]["example_step_x"].value = {**VALUE, "_run": {"read_snapshot": "", "computed_at": "2026-10-29T18:48:00"}}
    assert _rows(registry)["buckets"]["measured_at"] == ""


def test_the_shipped_catalogue_builds_a_table_with_no_proposal_rows(registry):
    """Every database declaration in analysis_catalog.yaml, against a database with nothing surveyed: rows are
    built, none is a proposal, and with nothing measured none is publishable and each says why."""
    from resource_explorer.surveyors.analysis_catalog_reader import _load

    table = pt.build_rows(registry, "database", SLUG)
    proposals = {e.id for e in _load()["database"] if e.declaration and e.declaration.result_kind == "proposal"}
    assert table["rows"] and not {r["analysis_id"] for r in table["rows"]} & proposals
    for r in table["rows"]:
        assert r["writes_as"]["words"]
        assert r["publishable"] or r["why_not"]


# ── the choice ──────────────────────────────────────────────────────────────────────────────────────────────────


def test_a_choice_is_signed_dated_and_the_newest_wins(registry, declared):
    pt.save_choice(registry, "database", SLUG, analysis_id="example_step_x", measure="buckets",
                   choice=pt.PUBLISH, author="ann")
    row = pt.save_choice(registry, "database", SLUG, analysis_id="example_step_x", measure="buckets",
                         choice=pt.KEEP, author="bob")
    assert row["choice"]["choice"] == pt.KEEP and row["choice"]["by"] == "bob" and row["choice"]["at"]
    assert pt.build_rows(registry, "database", SLUG)["chosen"] == 0
    # The choice is not scope: the selection record's scope reader never sees it.
    assert registry.current_resource_scope("database", SLUG) == {}


def test_a_choice_for_a_measure_no_declaration_names_is_refused(registry, declared):
    with pytest.raises(pt.ChoiceRefused, match="declares no measure 'nope'"):
        pt.save_choice(registry, "database", SLUG, analysis_id="example_step_x", measure="nope",
                       choice=pt.PUBLISH, author="ann")
    with pytest.raises(pt.ChoiceRefused, match="not one of"):
        pt.save_choice(registry, "database", SLUG, analysis_id="example_step_x", measure="buckets",
                       choice="maybe", author="ann")


def test_a_file_system_publishes_through_its_own_survey_report(tmp_path, declared):
    """The wait is lifted (2026-10-29): a file system's rows publish like a database's, and say where they go."""
    reg = ProjectRegistry(db_path=str(tmp_path / "fs.db"))
    table = pt.build_rows(reg, "filesystem", "share")
    assert table["press"] == pt.FS_PRESS_WORDS and "file-system survey report" in pt.FS_PRESS_WORDS
    assert table["rows"] and all(r["why_not"] != pt.NOT_FOR_KIND_WORDS for r in table["rows"])
    assert any(r["publishable"] for r in table["rows"])


def test_a_file_systems_proof_rows_are_its_own_and_read_back_as_published(tmp_path, declared):
    reg = ProjectRegistry(db_path=str(tmp_path / "fs.db"))
    row = next(r for r in pt.build_rows(reg, "filesystem", "share")["rows"] if r["publishable"])
    pt.save_choice(reg, "filesystem", "share", analysis_id=row["analysis_id"], measure=row["measure"],
                   choice=pt.PUBLISH, author="ann")
    sent = pt.annotations_for(reg, "share", "filesystem")
    assert sent["state"] == "sent" and len(sent["annotations"]) == 1
    pt.record_proofs(reg, "share", [{"analysis_id": row["analysis_id"], "measure": row["measure"],
                                     "value": row["value"], "as_of": row["as_of"], "guid": "g-1",
                                     "type": row["writes_as"]["type"], "route": row["writes_as"]["route"]}],
                     report_guid="rep-1", recorded_by="ann", entity_type="filesystem")
    again = next(r for r in pt.build_rows(reg, "filesystem", "share")["rows"] if r["locator"] == row["locator"])
    assert again["published"]["state"] == "published" and "published · read back" in again["published"]["words"]
    # a database of the same slug does not see the file system's proof
    assert pt.published_states(reg, "share", "database") == {}


# ── the press: published, read back, and the native type ───────────────────────────────────────────────────────


class _Discovery:
    def __init__(self, held):
        self.held = held

    def create_annotation(self, body):
        qn = body["properties"]["qualifiedName"]
        self.held[qn] = {"guid": f"ann-{len(self.held)}", "body": body}
        return self.held[qn]["guid"]


class _Maker:
    def __init__(self):
        self.reports = {}

    def create_asset(self, body):
        guid = f"report-{len(self.reports)}"
        self.reports[body["properties"]["qualifiedName"]] = guid
        return guid


def _surveyor(monkeypatch, *, readable=True):
    """The real EgeriaDatabaseSurveyor down to `publish_annotations` and `_read_back`; the pyegeria clients are fakes
    and the report read lists what the fake holds (None: the read could not be made)."""
    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor

    s = EgeriaDatabaseSurveyor.__new__(EgeriaDatabaseSurveyor)
    held: dict = {}
    s._asset_maker, s._discovery = _Maker(), _Discovery(held)
    s._find_element_guid = lambda name: (held.get(name) or {}).get("guid", "")
    s._report_annotations = (lambda guid: [{"qualified_name": q, "guid": h["guid"]} for q, h in held.items()]) \
        if readable else (lambda guid: None)
    monkeypatch.setattr(EgeriaDatabaseSurveyor, "catalog_and_survey", lambda self, **k: {"database_guid": "db-1"})
    return s, held


def _press(s, registry, surveyed_at=SURVEYED_AT):
    """The commit's step 3 as it runs it: publish the report with the declared measures, then record the proofs."""
    out = s.publish_local_survey(registry.get_database(SLUG), schema_info={}, schema_count=0, table_count=0,
                                 column_count=0, surveyed_at=surveyed_at, registry=registry,
                                 survey_after_catalog=False, declared_measures=True)
    counts = pt.record_proofs(registry, SLUG, out["declared_measures"]["sent"],
                              report_guid=out["report_element_guid"], curation_id="c1", recorded_by="dwolfson")
    return out, counts


def test_the_gate_one_measure_publishes_as_a_generic_measure_and_reads_back(registry, declared, monkeypatch):
    s, held = _surveyor(monkeypatch)
    pt.save_choice(registry, "database", SLUG, analysis_id="example_step_x", measure="rows_sampled",
                   choice=pt.PUBLISH, author="dwolfson")
    assert pt.build_rows(registry, "database", SLUG)["chosen"] == 1

    out, counts = _press(s, registry)

    assert out["declared_measures"]["state"] == "sent" and counts == {"sent": 1, "read_back": 1}
    assert pt.step_words(counts) == "1 declared measure read back"
    qn = out["declared_measures"]["sent"][0]["qualified_name"]
    assert qn == f"Annotation::PostgreSQL::{SLUG}::{SURVEYED_AT}::declared_measure.example_step_x.rows_sampled"
    props = held[qn]["body"]["properties"]
    assert props["class"] == "ResourceMeasureAnnotationProperties"
    assert props["resourceProperties"] == {"rows_sampled": "1200", "unit": "rows"}
    extra = props["additionalProperties"]
    assert extra["declaredRoute"] == "generic" and extra["declaredRouteWords"] == pt.GENERIC_WORDS
    assert extra["unit"] == "rows" and extra["asOf"] == "2026-10-27T08:30:00" and extra["confidenceBasis"] == "one row in ten, sampled"
    # Only the chosen measure went: the other row was not chosen and stays in RE.
    assert not [q for q in held if q.endswith("declared_measure.example_step_x.buckets")]

    rows = _rows(registry)
    assert rows["rows_sampled"]["published"]["state"] == "published"
    assert rows["rows_sampled"]["published"]["words"].startswith("published · read back ")
    assert rows["rows_sampled"]["published"]["guid"] == held[qn]["guid"]
    assert rows["buckets"]["published"]["state"] == "local"


def test_the_gate_adding_the_annotation_type_makes_the_row_and_the_next_publish_native(registry, declared,
                                                                                       monkeypatch):
    s, held = _surveyor(monkeypatch)
    pt.save_choice(registry, "database", SLUG, analysis_id="example_step_x", measure="rows_sampled",
                   choice=pt.PUBLISH, author="dwolfson")
    _press(s, registry)

    declared["entries"] = [_entry(_declaration({"type": "ResourceMeasureAnnotation",
                                                "properties": {"rows_sampled": "rowsSampled"}}))]
    row = _rows(registry)["rows_sampled"]
    assert row["writes_as"]["words"] == "ResourceMeasureAnnotation" and row["writes_as"]["route"] == "native"
    assert _rows(registry)["buckets"]["writes_as"]["words"] == pt.GENERIC_WORDS     # the other is unchanged

    out, counts = _press(s, registry, surveyed_at="2026-10-28T09:00:00")
    assert counts == {"sent": 1, "read_back": 1}
    props = held[out["declared_measures"]["sent"][0]["qualified_name"]]["body"]["properties"]
    assert props["class"] == "ResourceMeasureAnnotationProperties"
    assert props["resourceProperties"]["rowsSampled"] == "1200"            # the declared property map
    assert props["additionalProperties"]["declaredRoute"] == "native"
    assert "declaredRouteWords" not in props["additionalProperties"]
    assert out["declared_measures"]["sent"][0]["route"] == "native"


def test_a_report_that_cannot_be_read_back_leaves_sent_not_read_back_never_published(registry, declared,
                                                                                    monkeypatch):
    s, _ = _surveyor(monkeypatch, readable=False)
    pt.save_choice(registry, "database", SLUG, analysis_id="example_step_x", measure="rows_sampled",
                   choice=pt.PUBLISH, author="dwolfson")
    _, counts = _press(s, registry)
    assert counts == {"sent": 1, "read_back": 0}
    assert pt.step_words(counts) == "0 of 1 declared measure read back"
    pub = _rows(registry)["rows_sampled"]["published"]
    assert pub["state"] == "not_read_back" and pub["why"] == "the report could not be read back"


def test_a_value_that_moved_since_the_publish_reads_changed_since_published(registry, declared, monkeypatch):
    s, _ = _surveyor(monkeypatch)
    pt.save_choice(registry, "database", SLUG, analysis_id="example_step_x", measure="rows_sampled",
                   choice=pt.PUBLISH, author="dwolfson")
    _press(s, registry)
    declared["facts"]["example_step_x"].value = {**VALUE, "rows_sampled": 1300, "asOf": "2026-10-28T08:30:00"}
    pub = _rows(registry)["rows_sampled"]["published"]
    assert pub["state"] == "changed" and pub["words"].startswith("changed since published")


# ── the owner's four answers, 2026-10-28 (DECLARED-EXTENSIBILITY-SLICE1-PR2-PUBLISH-TABLE.md §4a) ──────────


def _choose(registry, measure="rows_sampled", choice=pt.PUBLISH):
    return pt.save_choice(registry, "database", SLUG, analysis_id="example_step_x", measure=measure, choice=choice,
                          author="dwolfson")


def test_answer1_an_analysis_egeria_runs_itself_is_already_in_egeria_and_offers_keep_only(registry, declared):
    declared["entries"] = [_entry(_declaration(), source="egeria")]
    row = _rows(registry)["rows_sampled"]
    assert row["elsewhere"]["route"] == "egeria_survey"
    assert not row["publishable"]
    assert row["why_not"] == ("already in Egeria via Egeria's own survey report; publishing it here would write the "
                              "same number twice")
    with pytest.raises(pt.ChoiceRefused, match="write the same number twice"):
        _choose(registry)
    assert _choose(registry, choice=pt.KEEP)["choice"]["choice"] == pt.KEEP      # Keep in RE is still offered


def test_answer1_a_measure_res_survey_report_writes_is_not_sent_again_even_if_chosen_before(registry, declared,
                                                                                         monkeypatch):
    _choose(registry)                                       # chosen while it was still publishable
    monkeypatch.setattr(pt, "IN_SURVEY_REPORT", frozenset({("example_step_x", "rows_sampled")}))
    rows = _rows(registry)
    assert rows["rows_sampled"]["elsewhere"]["route"] == "survey_report"
    assert rows["rows_sampled"]["why_not"].startswith("already in Egeria via Resource Explorer's survey report")
    assert rows["buckets"]["elsewhere"] is None and rows["buckets"]["publishable"]   # per measure, not per analysis
    assert pt.chosen_rows(registry, "database", SLUG) == []                          # the press sends nothing
    assert pt.build_rows(registry, "database", SLUG)["chosen"] == 0


def test_the_survey_reports_own_measures_are_named_in_the_shipped_catalogue():
    """`IN_SURVEY_REPORT` names real declared measures: a renamed measure must not silently become publishable."""
    from resource_explorer.surveyors.analysis_catalog_reader import _load

    declared = {(e.id, m.name) for e in _load()["database"] if e.declaration for m in e.declaration.measures}
    assert pt.IN_SURVEY_REPORT <= declared


def _engine_run(registry, *, slug=SLUG, step="example_step_x", guard="survey-completed", report="report-9"):
    run = registry.enqueue_run("engine_action", {"slug": slug, "step_key": step, "entity_type": "database"})
    registry.merge_run_target(run, {"outcome": {"guard": guard, "report_guid": report,
                                                "decided_at": "2026-10-26T07:15:00"}})
    return run


def test_answer1_a_step_the_engine_host_ran_and_published_is_already_in_egeria(registry, declared):
    _engine_run(registry, guard="survey-failed")                 # a failed run published nothing
    _engine_run(registry, slug="another_db")                     # another database's run is not this one's
    assert all(r["elsewhere"] is None for r in _rows(registry).values())
    _engine_run(registry)
    row = _rows(registry)["rows_sampled"]
    run_id = str(registry.list_runs(kind="engine_action")[0]["id"])      # the record the words name (F16)
    assert row["elsewhere"] == {"route": "engine_host", "words": "already in Egeria via the engine host · report "
                                f"2026-10-26 07:15 · engine action run {run_id}", "report_guid": "report-9",
                                "at": "2026-10-26T07:15:00", "run_id": run_id}
    assert not row["publishable"]


def test_answer1_a_library_step_is_matched_by_its_step_key_not_its_catalogue_id(registry, declared):
    declared["entries"] = [_entry(_declaration(), provided_by={"step": "x_step", "package": "trellis-step-x"})]
    _engine_run(registry, step="x_step")
    assert _rows(registry)["rows_sampled"]["elsewhere"]["route"] == "engine_host"


def test_answer2_a_schema_analysis_measure_publishes_natively_and_reads_back(registry, declared, monkeypatch):
    import json

    declared["entries"] = [_entry(_declaration({"type": "SchemaAnalysisAnnotation"}))]
    row = _rows(registry)["rows_sampled"]
    assert row["writes_as"] == {"route": "native", "type": "SchemaAnalysisAnnotation",
                                "words": "SchemaAnalysisAnnotation", "writable": True, "properties": {}, "why": ""}
    s, held = _surveyor(monkeypatch)
    _choose(registry)
    out, counts = _press(s, registry)
    assert counts == {"sent": 1, "read_back": 1}
    props = held[out["declared_measures"]["sent"][0]["qualified_name"]]["body"]["properties"]
    assert props["class"] == "SchemaAnalysisAnnotationProperties"
    carried = json.loads(props["jsonProperties"])
    assert carried["rows_sampled"] == "1200" and carried["declaredRoute"] == "native"
    assert carried["unit"] == "rows" and carried["asOf"] == "2026-10-27T08:30:00"
    assert "schemaName" not in props                       # nothing maps a field to it: never invented
    assert _rows(registry)["rows_sampled"]["published"]["state"] == "published"


def test_answer2_a_schema_analysis_property_map_fills_its_typed_field(registry, declared):
    declared["entries"] = [_entry(_declaration({"type": "SchemaAnalysisAnnotation",
                                                "properties": {"rows_sampled": "schemaType"}}))]
    ann = pt.annotation_for(_rows(registry)["rows_sampled"])
    assert ann.schema_type == "1200" and ann.json_properties["schemaType"] == "1200"


def test_answer2_physical_status_waits_for_a_later_slice_and_says_so(registry, declared):
    declared["entries"] = [_entry(_declaration({"type": "ResourcePhysicalStatusAnnotation"}))]
    row = _rows(registry)["rows_sampled"]
    assert row["writes_as"]["route"] == "native" and not row["writes_as"]["writable"]
    assert row["why_not"] == "Resource Explorer does not write ResourcePhysicalStatusAnnotation from a declaration yet"


def test_answer4_a_publish_choice_stands_across_surveys_and_the_row_says_so(registry, declared, monkeypatch):
    s, _ = _surveyor(monkeypatch)
    row = _choose(registry)
    assert row["choice"]["standing"] == pt.STANDING_WORDS == "sent by every commit until Keep in RE"
    _press(s, registry)
    declared["facts"]["example_step_x"].value = {**VALUE, "rows_sampled": 1300, "asOf": "2026-10-28T08:30:00"}
    pub = _rows(registry)["rows_sampled"]["published"]
    assert pub["state"] == "changed" and pub["words"].endswith(" · the next commit sends the new value")
    _, counts = _press(s, registry, surveyed_at="2026-10-28T09:00:00")     # the next survey's commit, no new choice
    assert counts == {"sent": 1, "read_back": 1}
    assert _rows(registry)["rows_sampled"]["published"]["state"] == "published"
    _choose(registry, choice=pt.KEEP)
    assert "standing" not in _rows(registry)["rows_sampled"]["choice"]
    _, counts = _press(s, registry, surveyed_at="2026-10-29T09:00:00")
    assert counts["sent"] == 0                                            # Keep in RE ends it


def test_nothing_chosen_adds_nothing_to_the_report_and_no_caller_but_the_commit_asks(registry, declared,
                                                                                    monkeypatch):
    s, held = _surveyor(monkeypatch)
    out, counts = _press(s, registry)
    assert out["declared_measures"] == {"state": "none_chosen", "sent": []} and counts["sent"] == 0
    pt.save_choice(registry, "database", SLUG, analysis_id="example_step_x", measure="rows_sampled",
                   choice=pt.PUBLISH, author="dwolfson")
    # Any other publish of the survey (the retired route, the hybrid surveyor) does not ask for them.
    out = s.publish_local_survey(registry.get_database(SLUG), schema_info={}, schema_count=0, table_count=0,
                                 column_count=0, surveyed_at="2026-10-29T09:00:00", registry=registry,
                                 survey_after_catalog=False)
    assert out["declared_measures"]["state"] == "not_asked"
    assert not [q for q in held if "declared_measure." in q]


# ── the as-of seam (PR 1's gap §2.1) ─────────────────────────────────────────────────────────────────────────────


def test_the_zero_fetch_reader_carries_the_runs_read_snapshot(monkeypatch):
    from resource_explorer.surveyors.database import db_derived
    from resource_explorer.surveyors.database import survey_definition_adapter as sda

    monkeypatch.setattr(db_derived, "run_db_derived", lambda registry, slug: {
        "surveyed_at": "2026-10-27T12:00:00", "read_snapshot": "2026-10-20T07:00:00", "table_coverage": None,
        "derived": {"schema_diff": {"columns_added": [], "explanation": "x"}}})
    monkeypatch.setattr(sda, "_attach_container_credential_scope", lambda *a, **k: None)
    monkeypatch.setattr(sda, "_attach_coverage_status", lambda *a, **k: None)
    data = sda._db_derived_field_reader("schema_diff")(None, SLUG)
    assert data["_run"] == {"read_snapshot": "2026-10-20T07:00:00", "computed_at": "2026-10-27T12:00:00"}


def test_the_run_beside_the_payload_is_not_content_and_is_the_facts_as_of():
    from resource_explorer.facts import _has_content
    from resource_explorer.workflows.scouting import results_have_data

    only_run = {"_run": {"read_snapshot": "2026-10-20T07:00:00"}}
    assert not _has_content(only_run) and not results_have_data(only_run)
    f = Fact(analysis_id="schema_diff", state="measured", value={"n": 1, **only_run})
    assert f.as_of == "2026-10-20T07:00:00" and f.as_dict()["as_of"] == "2026-10-20T07:00:00"
    assert Fact(analysis_id="x", state="measured", last_run_at="2026-10-01").as_dict()["as_of"] == "2026-10-01"


def test_a_chosen_row_that_cannot_be_built_is_named_on_the_step_never_silently_dropped(registry, declared,
                                                                                      monkeypatch):
    pt.save_choice(registry, "database", SLUG, analysis_id="example_step_x", measure="rows_sampled",
                   choice=pt.PUBLISH, author="dwolfson")

    def boom(row):
        raise ValueError("no property map")

    monkeypatch.setattr(pt, "annotation_for", boom)
    got = pt.annotations_for(registry, SLUG)
    assert got["annotations"] == [] and got["not_built"] == [{"locator": "example_step_x/rows_sampled",
                                                             "why": "no property map"}]
    assert pt.step_words({"sent": 0, "read_back": 0}, got["not_built"]) == \
        "1 chosen not sent · could not be built (example_step_x/rows_sampled)"


# ── the routes ──────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(registry, declared, monkeypatch):
    from fastapi.testclient import TestClient

    from resource_explorer import auth
    from resource_explorer.web.app import app
    from resource_explorer.web.routes import catalogue_scope as scope_routes

    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setattr(scope_routes, "get_current_user", auth.get_current_user)
    authorized: list = []

    async def authorize(reg, entity_type, slug, publish=False):
        authorized.append((entity_type, slug, publish))

    monkeypatch.setattr("resource_explorer.web.routes.curate.authorize_resource_write", authorize)
    c = TestClient(app)
    c.authorized = authorized
    return c


def _as(user):
    from resource_explorer.auth import create_access_token

    return {"Authorization": "Bearer " + create_access_token(user_id=user, egeria_token="t")}


def test_the_table_reads_open_and_a_choice_needs_a_signed_in_author(client, registry):
    j = client.get(f"/api/publish-table/database/{SLUG}").json()
    assert [r["measure"] for r in j["rows"]] == ["buckets", "rows_sampled"]
    assert client.get("/api/publish-table/database/nope").status_code == 404
    assert client.get(f"/api/publish-table/repo/{SLUG}").status_code == 404

    body = {"analysis_id": "example_step_x", "measure": "rows_sampled", "choice": "publish"}
    r = client.put(f"/api/publish-table/database/{SLUG}/choice", json=body)
    assert r.status_code == 401 and "Sign in to" in r.json()["detail"]
    assert pt.current_choices(registry, "database", SLUG) == {}

    r = client.put(f"/api/publish-table/database/{SLUG}/choice", json={**body, "author": "mallory"},
                   headers=_as("alice"))
    assert r.status_code == 200 and r.json()["choice"]["by"] == "alice"           # a body author is ignored
    assert client.authorized == [("database", SLUG, False)]


def test_a_refused_choice_is_422_with_the_reason(client):
    r = client.put(f"/api/publish-table/database/{SLUG}/choice",
                   json={"analysis_id": "example_step_x", "measure": "nope", "choice": "publish"}, headers=_as("alice"))
    assert r.status_code == 422 and "declares no measure 'nope'" in r.json()["detail"]


def test_no_declared_confidence_shows_a_field_path_to_a_person():
    """The catalog's own declarations: whatever the basis field is, the words a person reads never contain it."""
    from resource_explorer.publish_table import confidence_words
    from resource_explorer.surveyors.analysis_catalog_reader import _load

    seen = 0
    for entries in _load().values():
        for e in entries:
            conf = e.declaration.confidence if e.declaration else None
            if conf is None or not conf.basis:
                continue
            seen += 1
            words = confidence_words(conf)["words"]
            assert conf.basis not in words, f"{e.id}: {words}"
    assert seen >= 10
    row_count = next(e for es in _load().values() for e in es if e.id == "row_count_snapshot")
    assert confidence_words(row_count.declaration.confidence)["words"] == (
        "estimated · PostgreSQL's row-count estimate, not a count of the rows")


# ── slice 2's declaration fields (DECLARED-EXTENSIBILITY-SLICE2-CHAT-COMPILE.md) ────────────────────────────────


def test_each_row_reads_its_own_measures_confidence_else_the_declarations(registry, declared):
    dec = _declaration().model_dump()
    dec["measures"][1]["confidence"] = {"form": "measured"}
    declared["entries"] = [_entry(Declaration.model_validate(dec))]
    rows = _rows(registry)
    assert rows["rows_sampled"]["confidence"]["words"] == "measured"
    assert rows["buckets"]["confidence"]["words"] == "estimated · one row in ten, sampled"
    sent = pt.annotation_for(rows["rows_sampled"])
    assert sent.additional_properties["confidenceForm"] == "measured"


def test_an_analysis_not_built_yet_says_planned_whatever_is_stored_and_cannot_publish(registry, declared):
    declared["entries"] = [_entry(_declaration().model_copy(update={"built": False}))]
    rows = _rows(registry)
    for row in rows.values():
        assert row["value"] is None and row["absence"] == "not_built"
        assert row["absence_words"] == f"Planned, not built yet: not measured. {ABSENCE['not_measured']}"
        assert not row["publishable"] and row["why_not"] == "nothing to publish · Planned, not built yet: not measured."
    with pytest.raises(pt.ChoiceRefused, match="Planned, not built yet"):
        pt.save_choice(registry, "database", SLUG, analysis_id="example_step_x", measure="buckets",
                       choice=pt.PUBLISH, author="alice")


def test_the_shipped_unbuilt_analyses_are_planned_rows(registry, declared):
    from resource_explorer.surveyors.analysis_catalog_reader import _load

    unbuilt = [e for e in _load()["database"] if e.declaration and not e.declaration.built]
    assert {e.id for e in unbuilt} >= {"doc_source_ingestion", "doc_evidence_check", "semantic_suggestions"}
    declared["entries"] = unbuilt
    rows = pt.build_rows(registry, "database", SLUG)["rows"]
    assert rows and all(r["absence"] == "not_built" and not r["publishable"] for r in rows)


def test_a_library_step_member_measure_is_one_value_per_member_not_found_nothing(registry, declared):
    """A library step's result comes back from the shared reader wrapped as `measurement_rows`; its measure has one
    value per table, so the row says so rather than "ran and found nothing"."""
    from resource_explorer.surveyors.analysis_catalog_reader import _load

    entry = next(e for e in _load()["database"] if e.id == "change_rates")
    declared["entries"] = [entry]
    declared["facts"] = {"change_rates": Fact(
        analysis_id="change_rates", state="measured", last_run_at="2026-10-27T08:31:00",
        value={"measurement_rows": [{"name": "public.orders", "properties": {"Writes Per Day": "40"}},
                                    {"name": "public.items", "properties": {"Writes Per Day": "4"}}]})}
    row = pt.build_rows(registry, "database", SLUG)["rows"][0]
    assert row["absence"] == pt.PER_MEMBER != "measured_nothing"
    assert row["absence_words"] == "one value per member, not one for the resource · see tables_by_write_rate"
    assert not row["publishable"] and row["why_not"].startswith("nothing to publish · one value per member")


def test_a_library_step_value_at_the_top_of_the_record_still_resolves(registry, declared):
    """Unwrapping touches only `measurement_rows`; a resource-level field beside it reads as before."""
    declared["facts"]["example_step_x"] = Fact(
        analysis_id="example_step_x", state="measured", last_run_at="2026-10-27T08:31:00",
        value={**VALUE, "measurement_rows": [{"name": "public.t", "properties": {"x": 1}}]})
    assert _rows(registry)["rows_sampled"]["value"] == 1200


def test_a_resource_scoped_library_step_reads_each_measure_from_where_the_host_stored_it(tmp_path, monkeypatch):
    """fs_activity run by RE's own host on a walked tree, read by the table through the real fact layer: each measure
    is read from its own stored key, so fs_access_activity shows its value, or its state with the step's reason,
    never "ran and found nothing". A measure it could not establish offers no Publish."""
    from resource_explorer.surveyors import survey_step_host
    from resource_explorer.surveyors.analysis_catalog_reader import _load
    from resource_explorer.surveyors.filesystem.walk_derived import derived_view
    from resource_explorer.workflows.analysis import run_filesystem_analysis
    from tests.library_step_install import _entries
    from tests.test_files_derived_steps_host import _register, _tree, _walk

    for entry in reversed(_entries(tmp_path, ("fs_activity",))):
        monkeypatch.syspath_prepend(entry)
    survey_step_host.refresh("filesystem")
    try:
        reg = ProjectRegistry(db_path=str(tmp_path / "fs.db"))
        _walk(reg, _register(reg, _tree(tmp_path)))
        assert run_filesystem_analysis("tree", "fs_activity", registry=reg).status == "ok"
        entry = next(e for e in _load()["filesystem"] if e.id == "fs_activity")
        rows = {r["measure"]: r for r in pt.build_rows(reg, "filesystem", "tree", entries=[entry])["rows"]}
        stored = {r["measure"]: r for s in derived_view(reg, "tree")["steps"] if s["step"] == "fs_activity"
                  for r in s["results"]}
    finally:
        monkeypatch.undo()
        survey_step_host.refresh("filesystem")

    assert rows["fs_activity"]["value"] == stored["fs_activity"]["values"]["Cold Files"]
    access, held = rows["fs_access_activity"], stored["fs_access_activity"]
    if held["state"] == "measured":
        assert access["value"] == held["values"]["Cold Files"]
    else:
        assert access["value"] is None and access["absence"] != "measured_nothing"
        assert access["absence_words"].endswith(f" · {held['reason']}")
    if access["value"] is None:
        # find-absence-as-answer: not established is "not measured this run", never a zero, and offers no Publish
        assert not access["publishable"] and access["why_not"].startswith(pt.NOT_MEASURED_WORDS)


# ── F14 (2026-10-29): a measure is read where ITS scope says ─────────────────────────────────────────────────

def _member_step_with_summary():
    """A member-scoped step (one row per table) with one resource-scoped summary measure, as statistics_freshness."""
    from trellis_analytics.manifest import StepManifest

    absence = dict(ABSENCE)
    per = {"name": "per_table", "unit": "days", "as_of": "asOf", "value_field": "Days"}
    summary = {"name": "per_table_summary", "unit": "tables with stale statistics", "as_of": "asOf",
               "value_field": "Stale", "scope": "resource"}
    return StepManifest(
        name="per_table", function="a.b", source="ods", version="1", resource_types=["database"],
        annotation_type="ResourceMeasureAnnotation", measures=["per_table", "per_table_summary"],
        declaration=Declaration.model_validate({
            "result_kind": "list", "measures": [per, summary], "absence": absence,
            "extents": [{"name": "tables", "fields": ["scope", "Days"], "rows": 5, "order": "oldest first",
                         "truncation": "{shown} of {total} tables shown", "token_cost": 50,
                         "citation": "{analysis} · {scope} · as of {as_of}"}],
            "listing": {"item_fields": ["scope", "Days"], "order": "oldest first", "key_field": "scope"},
            "confidence": {"form": "measured"}, "scope": "member"}))


def test_a_resource_scoped_summary_of_a_member_scoped_step_is_read_by_its_own_key(registry, declared):
    from resource_explorer.surveyors.survey_step_host import measure_step_key

    m = _member_step_with_summary()
    declared["entries"] = [_entry(m.declaration, id_="per_table", name="Per Table",
                                  provided_by={"step": "per_table", "package": "trellis-step-x"})]
    declared["facts"] = {"per_table": Fact(analysis_id="per_table", state="measured", last_run_at="2026-10-29T10:00:00",
                                          value={"measurement_rows": [{"name": "public.t", "properties": {"Days": 3}}]})}
    registry.record_step_annotations(
        "database", SLUG, measure_step_key("per_table", "per_table_summary"), run="2026-10-29T10:00:00.1",
        measured_at="2026-10-29T10:00:00", annotations=[{
            "annotation_type": "ResourceMeasureAnnotation", "scope": "", "summary": "of 4 tables: 4 never analyzed",
            "resourceProperties": {"Stale": 0, "asOf": "2026-10-25T01:36:00"},
            "additionalProperties": {"resultState": "measured"}}])
    rows = _rows(registry)
    summary, per = rows["per_table_summary"], rows["per_table"]
    # the summary: its value, its own as-of beside the date RE measured it, its scope, the manifest's type, Publish on
    assert summary["value"] == 0 and summary["as_of"] == "2026-10-25T01:36:00"
    assert summary["measured_at"] == "2026-10-29T10:00:00"
    assert summary["scope"] == "resource" and summary["writes_as"]["type"] == "ResourceMeasureAnnotation"
    assert summary["writes_as"]["route"] == "native" and summary["publishable"] and summary["why_not"] == ""
    # the per-table measure stays per member, with its sentence
    assert per["scope"] == "member" and per["value"] is None and per["absence"] == pt.PER_MEMBER
    assert not per["publishable"] and "one value per member" in per["why_not"]


def test_a_measure_with_no_scope_of_its_own_inherits_the_declarations():
    m = _member_step_with_summary()
    assert [m.declaration.scope_of(x) for x in m.declaration.measures] == ["member", "resource"]
    assert Declaration.model_validate({**m.declaration.model_dump(), "scope": "resource"}).scope_of(
        m.declaration.measures[0]) == "resource"


def test_a_measure_scope_outside_the_levels_is_refused():
    with pytest.raises(ValueError, match="scope 'galaxy' is not one of the levels"):
        Declaration.model_validate({**_member_step_with_summary().declaration.model_dump(mode="json"),
                                    "measures": [{"name": "x", "unit": "u", "as_of": "a", "value_field": "v",
                                                  "scope": "galaxy"}]})


def test_the_shipped_statistics_freshness_summary_is_resource_scoped():
    from resource_explorer.surveyors.analysis_catalog_reader import _load

    dec = next(e for e in _load()["database"] if e.id == "statistics_freshness").declaration
    assert [dec.scope_of(m) for m in dec.measures] == ["member", "resource"]
    assert dec.measures[1].annotation.type == "ResourceMeasureAnnotation"       # F11 with it: native, not generic
