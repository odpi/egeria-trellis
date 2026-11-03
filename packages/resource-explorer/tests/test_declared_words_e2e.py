"""Capped, basis and categories (brief 2026-10-30, Parts A and B), end to end: the real readers over a fixture result.

Every sentence here is derived from the declaration and the stored fields. Change the denominator in the fixture and the
sentence changes; the step's own prose in the fixture is never what is read back.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from trellis_analytics.declaration import LOWER_BOUND_WORDS, Declaration

from resource_explorer import declared_context as dc
from resource_explorer import declared_words as dw
from resource_explorer import publish_table as pt
from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.surveyors import analysis_catalog_reader as catalog
from resource_explorer.surveyors import annotation_readers as ar
from tests.library_step_install import install_steps  # noqa: F401 - the fixture

ABSENCE = {"not_measured": "Not measured: nobody looked.", "not_permitted": "Not permitted.",
           "not_applicable": "Not applicable.", "measured_nothing": "Measured: nothing there."}
NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"


def _decl(measure: dict, **over) -> Declaration:
    return Declaration.model_validate({"result_kind": "measure", "absence": ABSENCE, "scope": "resource",
                                       "measures": [{"name": "rows_read", "unit": "rows", "as_of": "asOf",
                                                     "value_field": "rows", **measure}], **over})


def _registry(tmp_path, step: str, props: dict, summary: str = "the step's own prose, never read back"):
    reg = ProjectRegistry(db_path=str(tmp_path / "db.db"))
    reg.register_database(DatabaseEntity(slug="coco", display_name="Coco", db_type="postgresql", host="localhost",
                                         egeria_host="h", port=5442, database_name="coco"))
    reg.record_step_annotations("database", "coco", step, run="r1", measured_at="2026-10-30T10:00:00",
                                annotations=[{"annotation_type": "ResourceMeasureAnnotation", "scope": "coco",
                                              "summary": summary, "resourceProperties": props,
                                              "additionalProperties": {"resultState": "measured"}}])
    return reg


@pytest.fixture
def declared_step(monkeypatch):
    """The catalogue holds one library step whose declaration a test swaps."""
    world = {"dec": None}

    def load():
        entry = SimpleNamespace(id="rows_step", declaration=world["dec"], provided_by={"step": "rows_step"})
        return {"database": [entry]}

    monkeypatch.setattr(catalog, "_load", load)
    return world


def _headline(reg):
    return ar.headline_reader("database", "rows_step", "ResourceMeasureAnnotation")(reg, "coco")


# ── basis ──────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_basis_renders_n_of_m_from_the_fields_and_the_sentence_follows_the_denominator(declared_step, tmp_path):
    declared_step["dec"] = _decl({"unit": "columns", "value_field": "measured_count", "as_of": "asOf",
                                  "basis": {"denominator_field": "column_count", "denominator_label": "columns"}})
    (tmp_path / "a").mkdir()
    first = _headline(_registry(tmp_path / "a", "rows_step", {"measured_count": 456, "column_count": 1236, "asOf": "x"}))
    assert first["label"] == "measured · 456 of 1,236 columns (37%)"
    (tmp_path / "b").mkdir()
    second = _headline(_registry(tmp_path / "b", "rows_step", {"measured_count": 456, "column_count": 2000, "asOf": "x"}))
    assert second["label"] == "measured · 456 of 2,000 columns (23%)"
    assert "prose" not in first["label"] + second["label"]


def test_plurals_follow_the_number():
    m = _decl({"unit": "columns", "basis": {"denominator_field": "n", "denominator_label": "columns"}}).measures[0]
    assert dw.measure_words(m, {"rows": 1, "n": 1})["text"] == "1 of 1 column"
    assert dw.measure_words(m, {"rows": 1, "n": 2})["text"] == "1 of 2 columns"


def test_a_basis_whose_whole_the_result_lacks_leaves_the_plain_number_not_an_invented_whole():
    m = _decl({"basis": {"denominator_field": "n", "denominator_label": "columns"}}).measures[0]
    assert dw.measure_words(m, {"rows": 5})["text"] == "5 rows"


# ── capped ─────────────────────────────────────────────────────────────────────────────────────────────────────

CAPPED = {"capped_field": "capped", "basis": {"denominator_field": "total", "denominator_label": "rows in the table"}}


def test_a_capped_measure_reads_at_least_and_withholds_the_derived_share(declared_step, tmp_path):
    declared_step["dec"] = _decl(CAPPED)
    head = _headline(_registry(tmp_path, "rows_step", {"rows": 500, "total": 2000, "capped": "true", "asOf": "x"}))
    assert head["label"] == f"measured · at least 500 of 2,000 rows in the table · {LOWER_BOUND_WORDS}"
    assert head["capped"] is True
    assert "%" not in head["label"], "no share is derived from a lower bound"


def test_an_uncapped_measure_renders_the_plain_number_with_its_share(declared_step, tmp_path):
    declared_step["dec"] = _decl(CAPPED)
    head = _headline(_registry(tmp_path, "rows_step", {"rows": 500, "total": 2000, "capped": "false", "asOf": "x"}))
    assert head["label"] == "measured · 500 of 2,000 rows in the table (25%)" and head["capped"] is False
    assert "at least" not in head["label"] and "not derived" not in head["label"]


def test_a_capped_count_with_no_basis_says_at_least_and_not_derived():
    m = _decl({"capped_field": "capped"}).measures[0]
    sentence, capped = dw.measure_sentence(m, {"rows": 500, "capped": True})
    assert capped and sentence == f"at least 500 rows · {LOWER_BOUND_WORDS}"
    assert dw.measure_sentence(m, {"rows": 500, "capped": False}) == ("500 rows", False)


def test_the_chat_line_for_a_capped_measure_says_at_least():
    dec = _decl({"capped_field": "capped"})
    lines = dc._measure_lines(dec, {"rows": 500, "capped": True, "asOf": "2026-10-30"}, {})
    assert lines == [f"- rows_read: at least 500 rows · {LOWER_BOUND_WORDS} (as of 2026-10-30)"]


class _Layer:
    def __init__(self, value):
        self.value = value

    def fact(self, slug, analysis_id, level="resource"):
        from resource_explorer.facts import Fact
        return Fact(analysis_id=analysis_id, state="measured", value=dict(self.value), last_run_at="2026-10-30T10:00:00")


@pytest.fixture
def table(monkeypatch, tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    reg.register_database(DatabaseEntity(slug="coco", display_name="Coco", db_type="postgresql", host="localhost",
                                         egeria_host="h", port=5442, database_name="coco"))

    def rows(value, dec):
        entry = SimpleNamespace(id="rows_step", name="Rows Step", declaration=dec, source="local", provided_by={})
        monkeypatch.setattr(pt, "_entries", lambda entity_type: [entry])
        monkeypatch.setattr("resource_explorer.facts.FactLayer", lambda registry, resource_type: _Layer(value))
        return pt.build_rows(reg, "database", "coco")["rows"]
    return rows


def test_the_publish_row_of_a_capped_measure_carries_capped_true_and_the_words(table):
    [row] = table({"rows": 500, "capped": True, "asOf": "2026-10-30"}, _decl({"capped_field": "capped"}))
    assert row["capped"] is True and row["value"] == 500 and row["value_words"] == "at least 500 rows"
    ann = pt.annotation_for(row)
    assert ann.additional_properties["capped"] == "true"
    assert "at least 500 rows" in ann.summary


def test_the_publish_row_of_an_uncapped_measure_is_unchanged(table):
    [row] = table({"rows": 500, "capped": False, "asOf": "2026-10-30"}, _decl({"capped_field": "capped"}))
    assert row["capped"] is False and row["value_words"] == "500 rows"
    assert "capped" not in pt.annotation_for(row).additional_properties


# ── categories ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_overlapping_categories_are_never_summed_and_the_headline_never_says_of_n():
    m = _decl({"unit": "views", "basis": {"denominator_field": "n", "denominator_label": "views"}},
              result_kind="graph", categories={"exclusive": False}).measures[0]
    dec = _decl({}, result_kind="graph", categories={"exclusive": False})
    assert dw.allows_of(dec) is False
    assert dw.measure_words(m, {"rows": 3, "n": 9}, allow_of=False)["text"] == "3 views"
    assert dw.categories_line(dec, {}) == f"categories: {dw.OVERLAP_WORDS}" == "categories: overlapping categories (not summable)"


def test_the_stated_total_is_read_from_its_field_not_summed():
    dec = _decl({}, result_kind="distribution", categories={"exclusive": True, "sums_to": "counts.total"})
    value = {"counts": {"total": 2098, "a": 1500, "b": 509}}      # #603's case: 1,500 + 509 = 2,009, stated 2,098
    assert dw.stated_total(dec, value) == 2098
    assert "2,098" in dw.categories_line(dec, value) and "2,009" not in dw.categories_line(dec, value)
    assert dw.stated_total(dec, {}) is None


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_graph_renderer_stacks_exclusive_categories_by_the_stated_total_only(tmp_path):
    # The renderer imports the table block (envelope.js, which imports format.js) and the glyphs: copy the four, with
    # their root-relative imports pointed at the copies, so the registration travels with the renderer.
    for name in ("graph-renderer.js", "envelope.js", "format.js", "glyphs.js"):
        src = (NEXT / name).read_text(encoding="utf-8")
        for dep in ("envelope", "format", "glyphs"):
            src = src.replace(f"'/static/next/{dep}.js'", f"'./{dep}.mjs'")
        (tmp_path / name.replace("graph-renderer.js", "g.mjs").replace(".js", ".mjs")).write_text(src, encoding="utf-8")
    script = ("import * as G from '" + (tmp_path / "g.mjs").as_uri() + "';"
              "const items=[{label:'a',count:1500},{label:'b',count:509}];"
              "const ex=G.categoriesHtml(G.categoriesSpec({categories:{exclusive:true,sums_to:'t'}},{t:2098},items));"
              "const ov=G.categoriesHtml(G.categoriesSpec({categories:{exclusive:false}},{t:2098},items));"
              "const tb=G.renderByKind('table',{table:{stored:false},not_stored:'nothing yet'});"
              "console.log(JSON.stringify({ex,ov,tb}));")
    out = json.loads(subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True,
                                    check=True).stdout)
    assert "data-categories-stack" in out["ex"] and "2,098" in out["ex"] and "2,009" not in out["ex"]
    assert "data-categories-stack" not in out["ov"] and "categories overlap" in out["ov"]
    assert "data-table-not-stored" in out["tb"], "the table block is registered with the renderers"


# ── the non-numeric kinds (Part B): one headline each ──────────────────────────────────────────────────────────────

def _kind(kind: str, block: dict, rows: list[dict], fields: list[str], attr: str | None = None, **over):
    ext = {"name": "rows", "fields": fields, "rows": 10, "order": "as given", "truncation": "{shown} of {total} shown",
           "token_cost": 10, "citation": "{analysis}"}
    dec = Declaration.model_validate({"result_kind": kind, "absence": ABSENCE, attr or kind: block, "extents": [ext],
                                      "result_kind_note": "none fits", **over})
    return dw.kind_headline(dec, {"rows": rows}), dec


def test_a_finding_headline_is_the_count_the_highest_severity_and_the_first_what():
    rows = [{"summary": "no primary key", "where": "t1", "sev": "low"},
            {"summary": "PUBLIC can read t2", "where": "t2", "sev": "high"}]
    head, _ = _kind("finding", {"what_field": "summary", "where_field": "where", "severity_field": "sev",
                                "severities": ["low", "medium", "high"]}, rows, ["summary", "where", "sev"])
    assert head == "2 findings · high: PUBLIC can read t2"
    one, _ = _kind("finding", {"what_field": "summary", "where_field": "where"}, rows[:1], ["summary", "where"])
    assert one == "1 finding: no primary key"


def test_a_proposal_headline_counts_each_decision_state():
    rows = [{"c": "x", "s": "proposed"}, {"c": "y", "s": "proposed"}, {"c": "z", "s": "accepted"}]
    head, _ = _kind("proposal", {"candidate_field": "c", "decided_by": "curator", "state_field": "s"}, rows, ["c", "s"])
    assert head == "2 proposed · 1 accepted"


def test_a_list_headline_is_the_count_of_keys_and_the_order():
    head, _ = _kind("list", {"item_fields": ["t"], "order": "largest first", "key_field": "table_name"},
                    [{"table_name": "a"}, {"table_name": "b"}], ["table_name"], attr="listing")
    assert head == "2 table names, largest first"
    one, _ = _kind("list", {"item_fields": ["t"], "order": "largest first", "key_field": "table_name",
                            "item_label": "tables needing attention"}, [{"table_name": "a"}], ["table_name"], attr="listing")
    assert one == "1 table needing attention, largest first"


def test_a_table_headline_is_rows_by_columns():
    head, _ = _kind("table", {"columns": ["a", "b", "c"], "key_fields": ["a"]}, [{"a": 1}, {"a": 2}], ["a", "b", "c"])
    assert head == "2 rows × 3 columns"


def test_a_kind_whose_key_field_the_result_does_not_hold_says_nothing_not_zero():
    head, _ = _kind("finding", {"what_field": "summary"}, [{"other": 1}], ["other"])
    assert head == ""
    none_at_all, dec = _kind("finding", {"what_field": "summary"}, [], ["summary"])
    assert none_at_all == ABSENCE["measured_nothing"]          # rows read and empty: the declared sentence, not "0 findings"


def test_a_finding_with_an_action_routes_to_a_request_for_action_and_a_proposal_to_a_draft():
    _, finding = _kind("finding", {"what_field": "s", "action_field": "todo"}, [], ["s"])
    _, proposal = _kind("proposal", {"candidate_field": "c", "decided_by": "curator"}, [], ["c"])
    assert pt.kind_route(finding)["type"] == "RequestForActionAnnotation"
    route = pt.kind_route(proposal)
    assert route["type"] == "ClassificationAnnotation" and route["status"] == "DRAFT" and "never a fact" in route["words"]
    assert pt.kind_route(_decl({})) is None


def test_a_library_steps_list_headline_reaches_the_reader(install_steps, tmp_path):
    """statistics_freshness declares a list block; with no further summary the per-table rows are '<n> tables…'."""
    install_steps("statistics_freshness")
    reg = ProjectRegistry(db_path=str(tmp_path / "db.db"))
    reg.register_database(DatabaseEntity(slug="coco", display_name="Coco", db_type="postgresql", host="localhost",
                                         egeria_host="h", port=5442, database_name="coco"))
    for scope in ("public.a", "public.b"):
        reg.record_step_annotations("database", "coco", "statistics_freshness", run="r1", measured_at="2026-10-30T10:00:00",
                                    annotations=[{"annotation_type": "ResourceMeasureAnnotation", "scope": scope,
                                                  "summary": "s", "resourceProperties": {"Statistics Status": "stale"},
                                                  "additionalProperties": {"resultState": "measured"}}])
    head = ar.headline_reader("database", "statistics_freshness", "ResourceMeasureAnnotation")(reg, "coco")
    assert head["label"] == "2 tables, stale first, then never analyzed, then oldest ANALYZE first"
