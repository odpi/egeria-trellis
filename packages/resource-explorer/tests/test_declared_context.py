"""Declared extensibility, slice 2 (DESIGN-DECLARED-EXTENSIBILITY-RENDERING-CHAT-CONFIDENCE §3, §4): a declared
analysis enters a chat compile in its declared shape and no other — its measures with unit and as-of, its extents at
the declared fields and rows with the truncation sentence filled, its confidence form beside the numbers — and in
its own words when nothing is stored; a section the budget leaves out is said with its declared cost; the manifest
records what each section showed and the weakest confidence form the answer rests on."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from trellis_analytics.declaration import Declaration
from trellis_artifact_tree.model import Rung

from resource_explorer import declared_context as dc
from resource_explorer.context_compile import compile_context
from resource_explorer.facts import Fact, FactLayer
from resource_explorer.surveyors import analysis_catalog_reader as reader

ABSENCE = {"not_measured": "Not measured: nobody has looked yet.", "not_permitted": "Not permitted: no grant.",
           "not_applicable": "Not applicable here.", "measured_nothing": "Measured: no tables at all."}
EXTENT = {"name": "tables", "fields": ["table", "rows"], "rows": 4, "order": "by rows, most first",
          "truncation": "{shown} of {total} tables shown, by rows", "token_cost": 120,
          "citation": "{analysis} · {scope} · as of {as_of}"}


def declaration(**over) -> Declaration:
    body = {"result_kind": "list", "absence": ABSENCE, "scope": "resource", "answers": [],
            "measures": [{"name": "table_count", "unit": "tables", "as_of": "run.read_snapshot",
                          "value_field": "tables[]"}],
            "listing": {"item_fields": ["table", "rows"], "order": "by rows, most first", "key_field": "table"},
            "extents": [EXTENT], "confidence": {"form": "measured"}, **over}
    return Declaration.model_validate(body)


def fact(state="measured", value=None, last_run_at="2026-10-28T10:00:00"):
    return {"state": state, "value": value or {}, "last_run_at": last_run_at, "as_of": ""}


TABLES = [{"table": f"t{i}", "rows": 100 - i, "extra": "never shown"} for i in range(6)]


# ── where an extent's rows are ──────────────────────────────────────────────

def _extent(**over):
    return declaration(extents=[{**EXTENT, **over}]).extents[0]


def test_rows_are_under_the_extents_name_by_default():
    assert dc.extent_rows({"tables": TABLES}, _extent()) == TABLES


def test_a_declared_source_is_read_and_dot_is_the_result_as_one_row():
    assert dc.extent_rows({"hubs": TABLES}, _extent(source="hubs")) == TABLES
    assert dc.extent_rows({"table": "x", "rows": 1}, _extent(source=["."])) == [{"table": "x", "rows": 1}]


def test_a_mapping_of_mappings_is_a_row_per_entry_keyed_by_the_first_field():
    rows = dc.extent_rows({"tables": {"a": {"rows": 1}, "b": {"rows": 2}}}, _extent())
    assert rows == [{"table": "a", "rows": 1}, {"table": "b", "rows": 2}]


def test_joined_sources_tag_each_row_and_put_a_bare_value_in_the_first_untagged_field():
    ext = _extent(fields=["change", "table", "rows"], tag="change", source=["added", "retyped"])
    rows = dc.extent_rows({"added": ["s.a"], "retyped": [{"table": "s.b", "rows": 3}]}, ext)
    assert rows == [{"change": "added", "table": "s.a"}, {"change": "retyped", "table": "s.b", "rows": 3}]


def test_joined_nested_sources_are_told_apart_by_the_part_that_differs():
    ext = _extent(fields=["check", "item"], tag="check",
                  source=["checks.no_comment.items", "checks.naming.items"])
    rows = dc.extent_rows({"checks": {"no_comment": {"items": ["t1"]}, "naming": {"items": ["T2"]}}}, ext)
    assert rows == [{"check": "no_comment", "item": "t1"}, {"check": "naming", "item": "T2"}]


def test_rows_not_in_the_result_are_none_not_an_empty_list():
    assert dc.extent_rows({"other": []}, _extent()) is None
    assert dc.extent_rows({"tables": []}, _extent()) == []


def test_a_library_steps_records_are_its_rows_unwrapped():
    value = dc.library_rows({"measurement_rows": [{"name": "public.t", "properties": {"Writes Per Day": "4"}}],
                             "state_counts": {}})
    ext = _extent(name="tables_by_write_rate", fields=["scope", "Writes Per Day"])
    assert dc.extent_rows(value, ext) == [{"scope": "public.t", "Writes Per Day": "4"}]


def test_a_tag_that_is_not_a_field_is_refused():
    with pytest.raises(ValueError, match="tags its rows with 'kind', which is not one of its fields"):
        _extent(tag="kind", source=["a", "b"])


# ── the section ─────────────────────────────────────────────────────────────

def test_a_known_result_shows_measures_with_unit_and_as_of_then_the_declared_fields_only():
    rungs, seen = dc.section_rungs("tables_a", declaration(), fact(value={
        "tables": TABLES, "_run": {"read_snapshot": "2026-10-27T09:00:00"}}))
    full = rungs[Rung.FULL]
    assert full.startswith("## tables_a\n- table_count: 6 tables (as of 2026-10-27T09:00:00)")
    assert "confidence: measured" in full
    assert "tables (by rows, most first):\n- table: t0 · rows: 100" in full
    assert "never shown" not in full
    assert "4 of 6 tables shown, by rows" in full
    assert "cite as: tables_a · resource · as of 2026-10-27T09:00:00" in full
    assert seen["extents"]["tables"]["FULL"] == {"total": 6, "shown": 4, "as_of": "2026-10-27T09:00:00",
                                                 "rows": ["t0 · 100", "t1 · 99", "t2 · 98", "t3 · 97"],
                                                 "token_cost": 120}


def test_summary_shows_fewer_rows_and_identifiers_the_measures_alone():
    rungs, seen = dc.section_rungs("tables_a", declaration(), fact(value={"tables": TABLES}))
    assert "3 of 6 tables shown" in rungs[Rung.SUMMARY] and "cite as" not in rungs[Rung.SUMMARY]
    assert rungs[Rung.IDENTIFIERS] == "## tables_a\n- table_count: 6 tables (as-of not recorded)"
    assert seen["extents"]["tables"]["SUMMARY"]["shown"] == dc.SUMMARY_ROWS


def test_a_measured_zero_carries_its_declared_sentence():
    rungs, _ = dc.section_rungs("tables_a", declaration(), fact(state="nothing_found", value={"tables": []}))
    assert "Measured: no tables at all." in rungs[Rung.FULL]
    assert "- (no rows)" in rungs[Rung.FULL]


def test_an_extent_missing_from_the_result_says_so():
    rungs, seen = dc.section_rungs("tables_a", declaration(), fact(value={"other": 1}))
    assert "tables: not in the stored result" in rungs[Rung.FULL]
    assert seen["extents"]["tables"]["FULL"]["total"] is None


def test_an_inferred_score_says_whether_it_is_calibrated():
    dec = declaration(confidence={"form": "inferred", "basis": "signals", "method": "rules matched",
                                  "score": "confidence", "calibrated": False})
    rungs, seen = dc.section_rungs("k", dec, fact(value={"tables": []}))
    assert "confidence: inferred · rules matched · score is a rule count, not a probability" in rungs[Rung.FULL]
    assert seen["confidence"] == "inferred"


def test_a_member_scoped_measure_points_at_its_rows():
    dec = declaration(scope="member", measures=[{"name": "rows", "unit": "rows", "as_of": "run.surveyed_at",
                                                 "value_field": "rows"}])
    rungs, _ = dc.section_rungs("k", dec, fact(value={"tables": TABLES}))
    assert "- rows: one value per member, in rows, in the rows below" in rungs[Rung.FULL]


def test_absences_are_said_in_the_analyses_own_words():
    dec = declaration()
    absent = {"a": dc.absence(dec, "never_run"), "b": dc.absence(dec, "nothing_found")}
    full, summary = dc.absence_lines(absent)
    assert "- a: Not measured: nobody has looked yet." in full and "- b: Measured: no tables at all." in full
    assert summary == "Nothing stored for: a (not measured), b (measured nothing)\n"
    assert dc.absence_lines({}) == ("", "")


def test_more_absences_than_fit_are_named_not_dropped():
    dec = declaration()
    absent = {f"a{i}": dc.absence(dec, "never_run") for i in range(dc.MAX_ABSENCE_SENTENCES + 2)}
    full, _ = dc.absence_lines(absent)
    assert "also nothing stored for: a6, a7" in full


def test_the_weakest_link_is_reported_never_an_average():
    assert dc.weakest(["measured", "inferred", "estimated"]) == "inferred"
    assert dc.weakest(["measured", "judged"]) == "judged"
    assert dc.weakest([""]) == ""


# ── the catalogue ───────────────────────────────────────────────────────────

def test_every_joined_extent_in_the_catalog_tags_its_rows():
    reader.clear_cache()
    for t in ("database", "filesystem"):
        for e in reader._load()[t]:
            for x in (e.declaration.extents if e.declaration else []):
                if len(x.source) > 1:
                    assert x.tag, (e.id, x.name)


# ── through compile_context ────────────────────────────────────────────────

class _NullMock(MagicMock):
    def _get_child_mock(self, **kw):
        child = _NullMock(**kw)
        child.return_value = None
        return child


def _registry():
    r = _NullMock()
    r.query_findings.side_effect = lambda *a, **k: []
    return r


ROWS = [{"schema_name": "public", "table_name": f"t{i}", "row_count": 1000 - i, "size_bytes": 8192,
         "row_count_is_estimate": True} for i in range(25)]


@pytest.fixture
def facts(monkeypatch):
    """row_count_snapshot measured; every other analysis never run."""
    def fake(self, slug, analysis_id, level="resource"):
        if analysis_id == "row_count_snapshot":
            return Fact(analysis_id, "measured", value={"tables": ROWS, "total_row_count": 24700,
                                                        "total_size_bytes": 204800, "estimated_count": 25},
                        last_run_at="2026-10-28T09:00:00")
        return Fact(analysis_id, "never_run")
    monkeypatch.setattr(FactLayer, "fact", fake)


Q = "How big is this database — schemas, tables, views, columns, rows and bytes?"


def test_a_declared_section_is_packed_in_its_declared_shape(facts):
    c = compile_context(_registry(), "coco", Q, resource_type="database", budget=12000)
    assert "row_count_snapshot" in {p["key"] for p in c.manifest["packed"]}
    assert "- total_rows: 24700 rows (as of 2026-10-28T09:00:00)" in c.text
    assert "confidence: estimated · PostgreSQL's row-count estimate, not a count of the rows" in c.text
    assert "20 of 25 tables shown, by row count" in c.text
    assert "cite as: row_count_snapshot · resource · as of 2026-10-28T09:00:00" in c.text
    seen = c.manifest["declared"]["row_count_snapshot"]
    assert seen["rung"] == "FULL" and seen["confidence"] == "estimated"
    assert seen["extents"]["tables_by_rows"]["FULL"]["rows"][0] == "public · t0"
    assert c.manifest["lists"]["row_count_snapshot"]["tables_by_rows"] == {
        "total": 25, "shown": {"FULL": 20, "SUMMARY": 3}}
    assert c.manifest["confidence"] == {"weakest": "estimated", "by_section": {"row_count_snapshot": "estimated"}}


def test_a_declared_analysis_with_nothing_stored_is_said_in_its_own_words(facts):
    c = compile_context(_registry(), "coco", Q, resource_type="database", budget=12000)
    declared = reader._load()["database"]
    sentence = next(e for e in declared if e.id == "schema_inventory").declaration.absence.not_measured
    gaps = {g["key"]: g for g in c.manifest["gaps"]}
    assert gaps["schema_inventory"]["sentence"] == sentence
    assert gaps["schema_inventory"]["absence"] == "not_measured"
    assert "schema_inventory" in c.manifest["absences_in_context"]
    assert f"- schema_inventory: {sentence}" in c.text
    # Absences are not stored results: the rail counts `packed` as exactly those.
    assert "schema_inventory" not in {p["key"] for p in c.manifest["packed"]}


def test_a_declared_section_left_out_is_named_with_its_cost(facts):
    c = compile_context(_registry(), "coco", Q, resource_type="database", budget=12000, max_sections=1)
    assert c.manifest["omitted_in_context"]
    assert "Left out of this context (open them to see):" in c.text
    assert "tokens): not read: ranked below the section cap of 1" in c.text
    assert "- and " in c.text and " more (about " in c.text
    assert c.manifest["used"] <= c.manifest["budget"]


def test_an_omission_that_does_not_fit_is_a_note_not_an_overrun(facts):
    c = compile_context(_registry(), "coco", Q, resource_type="database", budget=6000, max_sections=1)
    assert c.manifest["used"] <= c.manifest["budget"]
    if not c.manifest["omitted_in_context"]:
        assert any("for want of room" in n for n in c.manifest["notes"])


def test_a_repo_compile_is_unchanged_by_declarations():
    c = compile_context(_registry(), "egeria", "How well documented is it?", budget=4000)
    assert c.manifest["declared"] == {} and c.manifest["absences_in_context"] == []
    assert c.manifest["confidence"] == {"weakest": "", "by_section": {}}
    assert "Left out of this context" not in c.text


# ── what the stored compile keeps, and what the journal says of it ─────────

def test_the_stored_compile_carries_the_text_the_model_was_given(facts):
    stored = {}
    r = _registry()
    r.record_compile.side_effect = lambda cid, slug, q, manifest, derivation, **k: stored.update(manifest)
    c = compile_context(r, "coco", Q, resource_type="database", budget=12000)
    assert stored["text"] == c.text
    assert "text" not in c.manifest


def test_a_journal_entry_names_the_weakest_link():
    from resource_explorer.journal import _evidence_of

    row = {"manifest": {"packed": [{"key": "row_count_snapshot"}, {"key": "privilege_audit"}], "gaps": [],
                        "confidence": {"weakest": "estimated",
                                       "by_section": {"row_count_snapshot": "estimated",
                                                      "privilege_audit": "measured"}}}}
    _, _, confidence = _evidence_of(row)
    assert confidence == ("answered from 2 stored results; rests at weakest on an estimated result "
                          "(row_count_snapshot)")
    _, _, before = _evidence_of({"manifest": {"packed": [{"key": "a"}], "gaps": []}})
    assert before == "answered from 1 stored result"


# ── the owner's answers of 2026-10-28 in the compile ───────────────────────

def test_a_not_built_analysis_reads_planned_not_measured():
    a = dc.absence(declaration(built=False), "never_run")
    assert a["absence"] == "not_built"
    assert a["sentence"] == "Planned, not built yet: not measured. Not measured: nobody has looked yet."


def test_a_not_built_analysis_is_said_as_planned_in_a_compile(facts):
    c = compile_context(_registry(), "coco", "What does the documentation say about this database?",
                        resource_type="database", budget=12000)
    gaps = {g["key"]: g for g in c.manifest["gaps"]}
    planned = {"doc_evidence_check", "semantic_suggestions"} & set(gaps)
    assert planned
    for aid in planned:
        assert gaps[aid]["absence"] == "not_built"
        assert gaps[aid]["sentence"].startswith("Planned, not built yet: not measured.")


def test_a_measure_with_its_own_confidence_says_it_beside_its_value():
    dec = declaration(confidence={"form": "estimated", "method": "an estimate"},
                      measures=[{"name": "table_count", "unit": "tables", "as_of": "run.read_snapshot",
                                 "value_field": "tables[]", "confidence": {"form": "measured"}},
                                {"name": "rows", "unit": "rows", "as_of": "run.read_snapshot", "value_field": "n"}])
    rungs, _ = dc.section_rungs("k", dec, fact(value={"tables": TABLES, "n": 5}))
    assert "- table_count: 6 tables (as-of not recorded) · measured" in rungs[Rung.FULL]
    assert "- rows: 5 rows (as-of not recorded)\n" in rungs[Rung.FULL]
    assert "confidence: estimated · an estimate (the weakest of its measures)" in rungs[Rung.FULL]


def test_row_count_snapshot_declares_its_byte_size_exact():
    reader.clear_cache()
    dec = {e.id: e for e in reader._load()["database"]}["row_count_snapshot"].declaration
    assert dec.confidence_of(dec.measure("total_size")).form == "measured"
    assert dec.confidence.form == "estimated"


# ── sort keys applied (project owner, 2026-10-28) ──────────────────────────

def test_declared_sort_keys_order_the_rows_before_any_are_left_out():
    ext = _extent(order="", sort=["rows desc"])
    rows = [{"table": "a", "rows": "9"}, {"table": "b", "rows": "10"}, {"table": "c"}, {"table": "d", "rows": 3}]
    # Numbers written as text sort by value, and a row without the field goes last whatever the direction.
    assert [r["table"] for r in dc.sorted_rows(rows, ext)] == ["b", "a", "d", "c"]


def test_several_keys_sort_first_key_first():
    ext = _extent(order="", sort=["rows desc", "table asc"])
    rows = [{"table": "b", "rows": 1}, {"table": "a", "rows": 1}, {"table": "c", "rows": 2}]
    assert [r["table"] for r in dc.sorted_rows(rows, ext)] == ["c", "a", "b"]


def test_a_section_shows_the_sorted_rows_and_the_derived_order_words():
    dec = declaration(extents=[{**EXTENT, "order": "", "sort": ["rows asc"], "rows": 2}])
    rungs, seen = dc.section_rungs("k", dec, fact(value={"tables": TABLES}))
    assert "tables (by rows, ascending):\n- table: t5 · rows: 95\n- table: t4 · rows: 96" in rungs[Rung.FULL]
    assert seen["extents"]["tables"]["FULL"]["rows"] == ["t5 · 95", "t4 · 96"]


def test_the_catalogue_sorts_row_count_snapshot_by_row_count():
    reader.clear_cache()
    x = {e.id: e for e in reader._load()["database"]}["row_count_snapshot"].declaration.extents[0]
    assert x.sort == ["row_count desc"] and x.order == ""


# ── a resource-scoped library step: each measure stored under its own key ─────────────────────

@pytest.fixture
def fs_activity_ran(tmp_path, monkeypatch):
    """fs_activity installed as pip would, run by RE's own host on a walked tree: its records are where the host's
    store puts them (the step's own measure under `fs_activity`, the other under `fs_activity:fs_access_activity`)."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors import survey_step_host
    from resource_explorer.workflows.analysis import run_filesystem_analysis
    from tests.library_step_install import _entries
    from tests.test_files_derived_steps_host import _register, _tree, _walk

    for entry in reversed(_entries(tmp_path, ("fs_activity",))):
        monkeypatch.syspath_prepend(entry)
    survey_step_host.refresh("filesystem")
    registry = ProjectRegistry(db_path=str(tmp_path / "fs.db"))
    _walk(registry, _register(registry, _tree(tmp_path)))
    assert run_filesystem_analysis("tree", "fs_activity", registry=registry).status == "ok"
    yield registry
    monkeypatch.undo()
    survey_step_host.refresh("filesystem")


def _stored(registry, measure: str) -> dict:
    """What the Files card's own reader (`derived_view`) says the host stored for one measure."""
    from resource_explorer.surveyors.filesystem.walk_derived import derived_view

    step = next(s for s in derived_view(registry, "tree")["steps"] if s["step"] == "fs_activity")
    return next(r for r in step["results"] if r["measure"] == measure)


def test_each_measure_of_a_resource_scoped_step_is_read_from_where_the_host_stored_it(fs_activity_ran):
    registry = fs_activity_ran
    dec = dc.declarations("filesystem")["fs_activity"]
    records = dc.measure_records(registry, "filesystem", "tree", "fs_activity", dec, "fs_activity")
    assert set(records) == {"fs_activity", "fs_access_activity"}
    for measure in records:
        stored = _stored(registry, measure)
        assert records[measure]["state"] == stored["state"] and records[measure]["reason"] == stored["reason"]
        assert {k: v for k, v in records[measure]["value"].items() if k != "scope"} == stored["values"]

    fact = FactLayer(registry, resource_type="filesystem").fact("tree", "fs_activity").as_dict()
    rungs, _ = dc.section_rungs("fs_activity", dec, fact, records)
    full = rungs[Rung.FULL]
    access = _stored(registry, "fs_access_activity")
    if access["state"] == "measured":
        assert f"- fs_access_activity: {access['values']['Cold Files']} files not read" in full
    else:
        assert f"- fs_access_activity: {dc._STATE_WORDS[access['state']]}: {access['reason']}" in full
    own = _stored(registry, "fs_activity")
    assert f"- fs_activity: {own['values']['Cold Files']} files not modified" in full
    assert "not in the stored result" not in full.split("activity_bands")[0]


def test_a_member_scoped_or_stepless_declaration_has_no_measure_records():
    registry = MagicMock()
    dec = dc.declarations("database")["change_rates"]
    assert dc.measure_records(registry, "database", "db", "change_rates", dec, "change_rates") == {}
    assert dc.measure_records(registry, "filesystem", "tree", "", dc.declarations("filesystem")["fs_activity"],
                              "fs_activity") == {}
    registry.query_step_annotations.assert_not_called()


def test_the_shared_reader_says_which_measure_each_row_is():
    from resource_explorer.surveyors import annotation_readers as ar

    registry = MagicMock()
    registry.query_step_annotations.return_value = ("run-1", [
        {"scope": "share-a", "summary": "", "annotation_type": "ResourceProfileAnnotation",
         "detail": {"measure": "", "additionalProperties": {"resultState": "measured"}}},
        {"scope": "share-a", "summary": "", "annotation_type": "ResourceProfileAnnotation",
         "detail": {"additionalProperties": {"resultState": "measured", "measure": "fs_access_activity"}}}])
    _, rows = ar.read_resource_measure_rows(registry, "filesystem", "share-a", "fs_activity")
    assert [r["measure"] for r in rows] == ["", "fs_access_activity"]


def test_a_nothing_found_measure_quotes_the_steps_sentence_never_a_zero():
    dec = dc.declarations("filesystem")["fs_permissions"]
    name = "world_readable_sensitive"
    record = {"value": {"World Readable Secret Named Files": 0, "asOf": "2026-10-29"}, "state": "nothing_found",
              "reason": "", "run": {}, "summary": "World Readable Secret Named Files: (none)"}
    lines = dc._measure_lines(dec, {}, {}, {name: record})
    line = next(x for x in lines if x.startswith(f"- {name}:"))
    assert "World Readable Secret Named Files: (none)" in line
    assert ": 0 " not in line
