"""B1: a table result's rows are stored, read through the declaration, shown capped, published as a summary only, and
quoted by Chat in the declared order (DESIGN-B1-MEMBER-SCOPED-TABLE-RESULT-2026-11-02 §4-5)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from trellis_analytics.declaration import Declaration

from resource_explorer import declared_context as dc
from resource_explorer import publish_table as pt
from resource_explorer import table_result as tr
from resource_explorer.facts import Fact
from resource_explorer.registry import ProjectRegistry
from resource_explorer.surveyors.result_materializer import write_table_rows

ABSENCE = {"not_measured": "Not profiled yet.", "not_permitted": "Not permitted.", "not_applicable": "Not applicable.",
           "measured_nothing": "Measured: no columns."}
SLUG = "shop"
RUN = "2026-11-02T10:00:00+00:00"


def declaration(max_rows=5) -> Declaration:
    def measure(name, unit, **over):
        return {"name": name, "unit": unit, "as_of": "surveyed_at", "value_field": name, "scope": "resource", **over}

    return Declaration.model_validate({
        "result_kind": "table", "result_kind_note": "one row per column", "scope": "member", "absence": ABSENCE,
        "confidence": {"form": "measured"}, "answers": [],
        "measures": [measure("columns_total", "columns"),
                     measure("columns_measured", "columns",
                             basis={"denominator_field": "columns_total", "denominator_label": "columns"}),
                     measure("nulls_total", "nulls")],
        "table": {"key_fields": ["schema", "table", "column"],
                  "columns": ["schema", "table", "column",
                              {"name": "state", "type": "state", "absence": "not profiled",
                               "states": ["measured", "not_measured", "not_permitted", "measured_nothing"]},
                              {"name": "null_count", "type": "integer", "absence": "not counted",
                               "measure_ref": "nulls_total"},
                              {"name": "size", "type": "bytes", "unit": "bytes", "absence": "not read"}],
                  "max_rows": max_rows, "order": "schema, table, column as the catalog lists them",
                  "truncation": "the first {shown} of {total} columns; every column is stored",
                  "summary": ["columns_measured", "nulls_total"]},
    })


def rows(n=12):
    return [{"schema": "public", "table": f"t{i // 4}", "column": f"c{i}",
             "state": "measured" if i % 3 else "not_permitted",
             "null_count": (i if i % 3 else None), "size": (str(i * 1024) if i % 2 else None)} for i in range(n)]


@pytest.fixture
def reg(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "t.db"))


RUN_OF = {"resource_kind": "database", "slug": SLUG, "analysis_id": "value_profile", "run_id": RUN}


# ── storing: every row, the cap is for showing ─────────────────────────────────────────────────────────────────

def test_every_row_is_stored_and_the_cap_applies_to_showing_only(reg):
    assert write_table_rows(reg, RUN_OF, declaration(), rows(12)) == 12
    got = tr.table_rows(reg, "database", SLUG, "value_profile", declaration())
    assert got["stored"] and got["run_id"] == RUN
    assert (got["total"], got["shown"]) == (12, 5)
    assert got["truncation"] == "the first 5 of 12 columns; every column is stored"
    assert [r["column"] for r in got["rows"]] == ["c0", "c1", "c2", "c3", "c4"]
    every = tr.table_rows(reg, "database", SLUG, "value_profile", declaration(), limit=None)
    assert every["shown"] == 12


def test_a_rerun_of_the_same_run_stores_the_rows_once(reg):
    write_table_rows(reg, RUN_OF, declaration(), rows(12))
    assert write_table_rows(reg, RUN_OF, declaration(), rows(7)) == 7


def test_the_latest_run_is_read(reg):
    write_table_rows(reg, RUN_OF, declaration(), rows(12))
    write_table_rows(reg, {**RUN_OF, "run_id": "2026-11-03T10:00:00+00:00"}, declaration(), rows(3))
    assert tr.table_rows(reg, "database", SLUG, "value_profile", declaration())["total"] == 3


def test_types_are_applied_and_an_empty_cell_reads_its_absence_word_never_blank(reg):
    write_table_rows(reg, RUN_OF, declaration(), rows(4))
    r0, r1 = tr.table_rows(reg, "database", SLUG, "value_profile", declaration())["rows"][:2]
    assert r0["null_count"] == "not counted" and r0["size"] == "not read" and r0["state"] == "not_permitted"
    assert set(r0["absent"]) == {"null_count", "size"}
    assert r1["null_count"] == 1 and r1["size"] == 1024 and r1["absent"] == []
    assert all(v != "" for r in (r0, r1) for v in r.values())


def test_nothing_stored_is_not_measured_no_rows(reg):
    got = tr.table_rows(reg, "database", SLUG, "value_profile", declaration())
    assert not got["stored"] and got["total"] == 0 and got["truncation"] == ""


@pytest.mark.parametrize("bad, said", [
    ({"colour": "red"}, "carries colour, which the table does not declare"),
    ({"column": None}, "has no column: a member row is keyed"),
    ({"state": "sort of"}, "state 'sort of', which is not one of its states"),
])
def test_a_row_that_does_not_read_through_the_declaration_is_refused(reg, bad, said):
    with pytest.raises(ValueError, match=said):
        write_table_rows(reg, RUN_OF, declaration(), [{**rows(1)[0], **bad}])


# ── the summary, the Questions row and the publish band ────────────────────────────────────────────────────────

SOURCE = {"columns_total": 12, "columns_measured": 8, "nulls_total": 40, "surveyed_at": RUN}


def test_the_question_line_is_the_summary_citing_the_table():
    assert tr.question_line(declaration(), SOURCE) == "8 of 12 columns · 40 nulls · the table lists them"
    assert tr.question_line(declaration(), {}) == ""


def test_the_payload_carries_headline_measures_rows_and_band(reg):
    write_table_rows(reg, RUN_OF, declaration(), rows(12))
    p = tr.payload(reg, "database", SLUG, "value_profile", declaration(), SOURCE)
    assert p["headline"] == "8 of 12 columns · 40 nulls"
    assert [m["name"] for m in p["measures"]] == ["columns_measured", "nulls_total"]
    assert p["band"] == "rows stay in Resource Explorer · summary published" and p["table"]["shown"] == 5


class _Layer:
    def fact(self, slug, analysis_id, level="resource"):
        return Fact(analysis_id=analysis_id, state="measured", value=SOURCE, last_run_at=RUN)


def test_publish_sends_the_summary_and_no_row(reg):
    write_table_rows(reg, RUN_OF, declaration(), rows(12))
    entry = SimpleNamespace(id="value_profile", name="Value Profile", declaration=declaration(), provided_by=None,
                            annotation_types=["ResourceMeasureAnnotation"])
    got = pt.build_rows(reg, "database", SLUG, layer=_Layer(), entries=[entry])["rows"]
    assert [r["measure"] for r in got] == ["columns_total", "columns_measured", "nulls_total"]
    assert all(r["rows_band"] == tr.ROWS_STAY for r in got)
    assert {r["value"] for r in got} == {12, 8, 40}


# ── chat compile: the stored rows, in the declared order, and what was not quoted ─────────────────────────────

def test_compile_quotes_the_stored_rows_in_order_and_counts_every_row(reg):
    write_table_rows(reg, RUN_OF, declaration(), rows(12))
    table = tr.table_rows(reg, "database", SLUG, "value_profile", declaration())
    fact = {"state": "measured", "value": SOURCE, "last_run_at": RUN, "as_of": ""}
    rungs, seen = dc.section_rungs("value_profile", declaration(), fact, {}, table=table)
    full = str(next(iter(rungs.values())))
    assert full.index("c0") < full.index("c4") and "c5" not in full
    assert "5 of 12 rows quoted; the table lists the rest" in full
    assert seen["extents"]["rows"]["FULL"]["total"] == 12 and seen["extents"]["rows"]["FULL"]["shown"] == 5


def test_compile_quotes_about_2000_tokens_of_rows_never_fewer_than_five():
    """Architect, 2026-11-02: rows = about 2,000 tokens / token_cost_per_row, in declared order, at least 5; the
    extent's whole-table token_cost is the full-quote cost and is never spent."""
    narrow = declaration(max_rows=1000).extents[0]            # 6 fields x about 8 = 48 tokens a row
    assert narrow.token_cost_per_row == 48 and narrow.token_cost == 48_000
    assert dc.table_quote_rows(narrow) == 2000 // 48 == 41
    wide = narrow.model_copy(update={"token_cost_per_row": 31 * 8})   # value_profile's width
    assert dc.table_quote_rows(wide) == 8
    huge = narrow.model_copy(update={"token_cost_per_row": 1000})
    assert dc.table_quote_rows(huge) == dc.TABLE_QUOTE_MIN_ROWS == 5
    few = declaration(max_rows=3).extents[0]
    assert dc.table_quote_rows(few) == 3


def test_re_installs_its_read_as_the_table_rows_entry_point(reg):
    """Found wherever RE is installed, not only in a process that imported it (the LS2 seam's pattern)."""
    from importlib.metadata import entry_points

    from trellis_analytics.readers.table_rows import TABLE_ROWS

    [ep] = [e for e in entry_points(group="trellis_analytics.readers") if e.name == TABLE_ROWS]
    assert ep.load() is tr.RegistryTableRows
    write_table_rows(reg, RUN_OF, declaration(), rows(12))
    via_seam = ep.load()(reg).table_rows(dict(RUN_OF), declaration())
    assert via_seam == tr.table_rows(reg, "database", SLUG, "value_profile", declaration(), run=RUN)
