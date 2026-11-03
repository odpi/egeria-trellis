"""Leg 9 of the declaration gate on 8813 (READ-B1531EE0, 2026-10-30): the publish table's summary row over a REAL stored
record (F33), the declared words on that surface (F34), the categories legend on the step line (F35) and US spelling on
adjacent screens (W18).

The stored record below is the shape read from the registry on 2026-10-30 for coco_pharma's 2026-10-29 13:37:59Z run: the
version of the step that wrote it did not write "Needs Attention", and it wrote "never analysed" in its summary."""
from __future__ import annotations

import json

import pytest

from resource_explorer import publish_table as pt
from resource_explorer.facts import Fact
from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.surveyors import annotation_readers as ar
from resource_explorer.surveyors import analysis_catalog_reader as catalog
from tests.library_step_install import install_steps  # noqa: F401 - the fixture

SLUG = "coco"
STEP = "statistics_freshness"
SUMMARY_KEY = "statistics_freshness:statistics_freshness_summary"

# As stored by the 2026-10-29 13:37:59Z run (resourceProperties only; values are strings): no "Needs Attention".
OLD_PROPS = {"Tables": "266", "Never Analyzed": "226", "Stale": "0", "Current": "37", "Changes Since Analyze Not Read": "0",
             "Not Read": "3", "Oldest Analyze Days": "2.4", "asOf": "2026-10-24T21:09:01.907439",
             "basis": "ages are relative to the snapshot's as-of, not to now"}
OLD_SUMMARY = "of 266 tables: 226 never analysed, 0 stale, 37 current, 3 not read (as of 2026-10-24T21:09:01.907439)"
NEW_PROPS = {**OLD_PROPS, "Needs Attention": "226"}


class _Layer:
    def fact(self, slug, analysis_id, level="resource"):
        return Fact(analysis_id=analysis_id, state="measured", value={"measurement_rows": []},
                    last_run_at="2026-10-29T13:38:00")


def _registry(tmp_path, props, summary):
    reg = ProjectRegistry(db_path=str(tmp_path / "db.db"))
    reg.register_database(DatabaseEntity(slug=SLUG, display_name="Coco", db_type="postgresql", host="localhost",
                                         egeria_host="h", port=5442, database_name="coco"))
    reg.record_step_annotations("database", SLUG, SUMMARY_KEY, run="r1", measured_at="2026-10-29T13:37:59",
                                annotations=[{"annotation_type": "ResourceMeasureAnnotation", "scope": SLUG,
                                              "summary": summary, "resourceProperties": props,
                                              "measure": "statistics_freshness_summary",
                                              "additionalProperties": {"resultState": "measured"}}])
    return reg


def _summary_row(reg):
    entry = next(e for e in catalog._load()["database"] if e.id == STEP)
    rows = pt.build_rows(reg, "database", SLUG, layer=_Layer(), entries=[entry])["rows"]
    return next(r for r in rows if r["measure"] == "statistics_freshness_summary")


def test_a_stored_summary_without_the_declared_field_is_not_recorded_never_measured_nothing(install_steps, tmp_path):
    install_steps(STEP)
    row = _summary_row(_registry(tmp_path, OLD_PROPS, OLD_SUMMARY))
    assert row["value"] is None and row["absence"] == pt.NOT_RECORDED
    assert "does not record 'Needs Attention'" in row["absence_words"] and "run the step again" in row["absence_words"]
    assert "Measured: no table" not in row["absence_words"], "a real 226 is never reported as nothing found"
    assert not row["publishable"]


def test_a_stored_summary_that_holds_the_field_reads_n_of_m_in_the_declared_words(install_steps, tmp_path):
    install_steps(STEP)
    row = _summary_row(_registry(tmp_path, NEW_PROPS, OLD_SUMMARY))
    assert row["value"] == "226" or row["value"] == 226
    assert row["value_words"] == "226 of 266 tables (85%)"
    assert row["capped"] is False


def test_the_same_words_on_every_surface(install_steps, tmp_path):
    install_steps(STEP)
    reg = _registry(tmp_path, NEW_PROPS, OLD_SUMMARY)
    table = _summary_row(reg)["value_words"]
    head = ar.headline_reader("database", STEP, "ResourceMeasureAnnotation")
    # the step's own per-table rows are absent in this fixture; the declared summary alone is the headline
    reg.record_step_annotations("database", SLUG, STEP, run="r1", measured_at="2026-10-29T13:38:00",
                                annotations=[{"annotation_type": "ResourceMeasureAnnotation", "scope": "public.t",
                                              "summary": "s", "resourceProperties": {"Statistics Status": "stale"},
                                              "additionalProperties": {"resultState": "measured"}}])
    assert head(reg, SLUG)["label"] == f"measured · {table}"


def test_a_capped_row_publishes_the_at_least_words_without_a_share(table_world):
    row = table_world({"rows": 500, "total": 2000, "capped": True, "asOf": "2026-10-30"})
    assert row["capped"] is True and row["value_words"] == "at least 500 of 2,000 rows in the table"
    assert "%" not in row["value_words"]


@pytest.fixture
def table_world(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from trellis_analytics.declaration import Declaration

    dec = Declaration.model_validate({
        "result_kind": "measure", "scope": "resource",
        "absence": {"not_measured": "a", "not_permitted": "b", "not_applicable": "c", "measured_nothing": "d"},
        "measures": [{"name": "rows_read", "unit": "rows", "as_of": "asOf", "value_field": "rows",
                      "capped_field": "capped", "basis": {"denominator_field": "total",
                                                          "denominator_label": "rows in the table"}}]})
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    reg.register_database(DatabaseEntity(slug=SLUG, display_name="Coco", db_type="postgresql", host="localhost",
                                         egeria_host="h", port=5442, database_name="coco"))

    def build(value):
        entry = SimpleNamespace(id="rows_step", name="Rows Step", declaration=dec, source="local", provided_by={})

        class L:
            def fact(self, slug, analysis_id, level="resource"):
                return Fact(analysis_id=analysis_id, state="measured", value=dict(value), last_run_at="2026-10-30T10:00:00")
        return pt.build_rows(reg, "database", SLUG, layer=L(), entries=[entry])["rows"][0]
    return build


# ── F35 / W18 ──────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_headline_carries_the_categories_legend_and_stored_prose_reads_in_us_spelling(install_steps, tmp_path):
    install_steps("change_rates")
    reg = ProjectRegistry(db_path=str(tmp_path / "c.db"))
    reg.register_database(DatabaseEntity(slug=SLUG, display_name="Coco", db_type="postgresql", host="localhost",
                                         egeria_host="h", port=5442, database_name="coco"))
    reg.record_step_annotations("database", SLUG, "change_rates", run="r1", measured_at="2026-10-29T13:38:00",
                                annotations=[{"annotation_type": "ResourceMeasureAnnotation", "scope": "public.t",
                                              "summary": "never analysed; 3 writes a day", "resourceProperties": {},
                                              "additionalProperties": {"resultState": "measured"}}])
    head = ar.headline_reader("database", "change_rates", "ResourceMeasureAnnotation")(reg, SLUG)
    assert head["categories"] == "each item is in one category"
    assert "analysed" not in head["label"] and "never analyzed" in head["label"]


def test_overlapping_categories_have_a_legend_that_says_not_stacked():
    from trellis_analytics.declaration import Declaration

    from resource_explorer import declared_words as dw

    dec = Declaration.model_validate({
        "result_kind": "graph", "categories": {"exclusive": False},
        "absence": {"not_measured": "a", "not_permitted": "b", "not_applicable": "c", "measured_nothing": "d"},
        "measures": [{"name": "m", "unit": "views", "as_of": "asOf", "value_field": "n"}]})
    assert dw.categories_legend(dec, {}) == "categories overlap · not stacked or added up"
    stated = Declaration.model_validate({**json.loads(dec.model_dump_json()),
                                         "categories": {"exclusive": True, "sums_to": "counts.total"}})
    assert dw.categories_legend(stated, {"counts": {"total": 2098}}) == "each item is in one category · stated total 2,098"


def test_us_spelling_only_changes_the_known_words():
    from resource_explorer.declared_words import us_spelling

    assert us_spelling("226 never analysed, 0 stale") == "226 never analyzed, 0 stale"
    assert us_spelling("Analysing the analyses") == "Analyzing the analyses"      # the plural noun is already US
    assert us_spelling("colour") == "colour" and us_spelling(None) is None


# ── leg 10 (READ-74AE485F): a missing count says whether it was measured; a missing value is not a change ───────────

def test_sibling_state_reads_the_checks_own_state_beside_a_missing_count():
    value = {"checks": {"tables_without_comment": {"state": "not_measured", "count": None, "total": 4,
                                                   "explanation": "Not one table carries a description."}}}
    assert pt.sibling_state(value, "checks.tables_without_comment.count") == (
        "not_measured", "Not one table carries a description.")
    assert pt.sibling_state(value, "checks.missing.count") == ("", "")
    assert pt.sibling_state(value, "count") == ("", "")
    assert pt.sibling_state(value, "run.surveyed_at") == ("", "")


def test_a_missing_value_is_not_a_change_from_the_published_one():
    proof = {"proof": pt.P_MEASURE_READ_BACK, "read_at": "2026-10-29T23:59:10",
             "detail": {"value": "226", "as_of": "2026-10-24"}, "element_guid": "g", "node_kind": "database"}
    missing = pt._published_word(proof, None, "")
    assert missing["state"] == "published"
    assert missing["words"] == "published 2026-10-29 23:59 · not re-measured since"
    assert "changed" not in missing["words"]
    # a real different value is still a change
    assert pt._published_word(proof, "300", "2026-10-24")["state"] == "changed"
    # and the same value is still plain published
    assert pt._published_word(proof, "226", "2026-10-24")["words"] == "published · read back 2026-10-29 23:59"
