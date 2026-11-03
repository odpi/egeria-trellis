"""The database Value profile screen's two reads and its container reader (DESIGN-VALUE-PROFILE-SCREEN-2026-10-30 §9).

Real routes, real registry (a temp SQLite), real reader (`_value_profile_results`). The profile rows are written as the
value-profile step stores them, for four tables: one measured, one measured and empty, one not measured (over its time
budget) and one view. Pinned: the header counts, the per-table lines, the four cell cases of §4 (a NULL length on a
number column is "—" with the type title; a NULL frequency_state on an eligible text column is "not asked for"; a 0 is
"0"; a column that was not profiled is a sentence, not cells), and the container reader's per-table rows, not-measured
last, each with `opens`.
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import DatabaseEntity, ProjectRegistry

SLUG = "coco_ods"
AT = "2026-10-30T11:12:58.965452"
BUDGET = "profile incomplete · exceeded 5 min · counts not measured"
VIEW = "a view, not a table: only tables are read"


def _col(table, column, general_type, **measures):
    return {"schema_name": "sales", "table_name": table, "column_name": column, "general_type": general_type,
            "state": "measured", "reason": "", "ran_as": "surveyor", **measures}


def _absent(table, column, state, reason):
    return {"schema_name": "sales", "table_name": table, "column_name": column, "general_type": "X", "state": state,
            "reason": reason, "ran_as": "surveyor"}


PROFILE = [
    _col("orders", "id", "N", record_ct=4, value_ct=4, distinct_value_ct=4, null_value_ct=0, min_value=1, max_value=4,
         zero_value_ct=0),
    _col("orders", "placed", "D", record_ct=4, value_ct=3, distinct_value_ct=3, null_value_ct=1,
         min_date="2008-04-30 00:00:00", max_date="2027-02-08 00:00:00", future_date_ct=1, before_20yr_date_ct=1),
    # eligible for frequent values (2 distinct, short) but none were collected: "not asked for"
    _col("orders", "status", "A", record_ct=4, value_ct=4, distinct_value_ct=2, null_value_ct=0, zero_length_ct=0,
         min_length=4, max_length=7, avg_length=5.5, frequency_state=None),
    _col("orders", "channel", "A", record_ct=4, value_ct=4, distinct_value_ct=5, null_value_ct=0, zero_length_ct=1,
         min_length=0, max_length=5, avg_length=3.2, frequency_state="read"),
    _col("orders", "paid", "B", record_ct=4, value_ct=4, distinct_value_ct=2, null_value_ct=0, boolean_true_ct=3),
    _col("empty_t", "id", "N", record_ct=0, value_ct=0, distinct_value_ct=0, null_value_ct=0),
    _col("empty_t", "label", "A", record_ct=0, value_ct=0, distinct_value_ct=0, null_value_ct=0, zero_length_ct=0),
    _absent("big", "id", "not_measured", BUDGET),
    _absent("big", "amount", "not_measured", BUDGET),
    _absent("v_orders", "id", "not_applicable", VIEW),
]
FREQUENT = [
    {"schema_name": "sales", "table_name": "orders", "column_name": "channel", "value_rank": i, "value": v, "value_ct": n,
     "other_distinct_ct": None}
    for i, (v, n) in enumerate([("web", 2), ("shop", 1), ("phone", 1), ("", 1)], start=1)
] + [{"schema_name": "sales", "table_name": "orders", "column_name": "channel", "value_rank": 11, "value": None,
      "value_ct": 1, "other_distinct_ct": 1}]
PROPOSALS = [
    {"check_key": f"sales.orders.id::{t}", "schema_name": "sales", "table_name": "orders", "column_name": "id",
     "test_type": t, "origin": "vendored", "profile_surveyed_at": AT, "params_json": "{}", "catalogue_ref": "x",
     "state": state}
    for t, state in (("Required", "proposed"), ("Unique", "proposed"), ("Min_Val", "not_established"))
]


@pytest.fixture
def registry(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "vp.db"))
    reg.register_database(DatabaseEntity(slug=SLUG, display_name="Coco ODS", db_type="postgresql",
                                         host="localhost", port=5442, database_name="coco_ods"))
    reg.write_detail_rows("database_value_profiles", SLUG, AT, rows=PROFILE)
    reg.write_detail_rows("database_value_profile_frequent_values", SLUG, AT, rows=FREQUENT)
    reg.write_detail_rows("database_quality_proposals", SLUG, AT, rows=PROPOSALS)
    return reg


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    from resource_explorer.web.app import app

    return TestClient(app)


BASE = f"/api/databases/{SLUG}/value-profile"


def test_header_counts_and_table_lines(client):
    d = client.get(BASE).json()
    assert d["profiled"] is True
    assert d["header"] == ("Value profile · 7 of 10 columns measured · 2 tables · profiled 2026-10-30 11:12 as surveyor · "
                           "3 columns not measured: 1 table over the 5-minute budget, 1 view")
    lines = [t["line"] for t in d["tables"]]
    # measured first (schema order), then not measured, then not applicable
    assert lines == [
        "sales.empty_t · 0 rows · empty table · 2 columns measured · profiled 11:12",
        "sales.orders · 4 rows · 5 columns measured · profiled 11:12",
        f"sales.big · not measured · {BUDGET}",
        f"sales.v_orders · not applicable · {VIEW}",
    ]
    assert [t["openable"] for t in d["tables"]] == [True, True, False, False]
    assert {s["value"]: s["count"] for s in d["filters"]["states"]} == {"measured": 7, "not_measured": 2,
                                                                        "not_applicable": 1}
    assert {s["label"] for s in d["filters"]["states"]} == {"measured", "not measured", "not applicable"}
    assert {t["value"]: t["count"] for t in d["filters"]["types"]} == {"text": 3, "number": 2, "date": 1, "boolean": 1}


def _cells(client, table):
    body = client.get(f"{BASE}/sales/{table}").json()
    return {c["column_name"]: c for c in body["columns"]}


def test_the_four_cell_cases(client):
    cols = _cells(client, "orders")
    # 2. not a measure of this type: a NULL length on a number column is a muted "—" titled by the type
    rng = cols["id"]["cells"]["common"]
    assert (rng["text"], rng["muted"]) == ("—", True)
    assert cols["paid"]["cells"]["common"]["title"] == "most common values are read for text columns only"
    # 3. applies, not taken: an eligible text column with no frequency_state
    common = cols["status"]["cells"]["common"]
    assert (common["text"], common["muted"]) == ("not asked for", True)
    # 4. measured zero: "0", plain, and an empty table's zeros are not flags
    empty = _cells(client, "empty_t")
    assert empty["id"]["cells"]["distinct"] == {"text": "0", "title": "", "muted": False}
    assert empty["id"]["cells"]["filled"]["text"] == "0 of 0"
    assert empty["label"]["cells"]["flags"]["text"] == ""
    # 1. not profiled: a sentence, no cells
    big = _cells(client, "big")
    assert "cells" not in big["id"] and big["id"]["sentence"] == BUDGET and big["id"]["state_words"] == "not measured"


def test_measured_cells_in_words(client):
    cols = _cells(client, "orders")
    c = {k: v["text"] for k, v in cols["id"]["cells"].items()}
    assert c == {"type": "number", "filled": "4 of 4 · 100%", "distinct": "4 · all distinct", "range": "1 → 4",
                 "common": "—", "flags": "", "checks": "2 proposed · 1 not established"}
    placed = cols["placed"]["cells"]
    assert placed["range"]["text"] == "2008-04-30 → 2027-02-08"
    assert placed["flags"]["text"] == "1 future date · 1 before 2006"
    assert placed["filled"]["text"] == "3 of 4 · 75%"
    assert cols["paid"]["cells"]["range"]["text"] == "true 3 · false 1"
    assert cols["status"]["cells"]["range"]["text"] == "lengths 4 → 7 (avg 5.5)"
    assert cols["status"]["cells"]["checks"]["text"] == "—"
    channel = cols["channel"]["cells"]
    assert channel["common"]["text"] == "web ×2 · shop ×1 · phone ×1 · +2 more"
    assert channel["common"]["values"][-1] == {"value": "1 other value", "count": "1"}
    assert {"value": "(empty)", "count": "1"} in channel["common"]["values"]
    assert channel["flags"]["text"] == "1 empty string"


def test_no_null_iso_or_underscore_words_on_screen(client):
    d = client.get(BASE).json()
    words = [d["header"]] + [t["line"] for t in d["tables"]] + [s["label"] for s in d["filters"]["states"]]
    for table in ("orders", "empty_t", "big", "v_orders"):
        for c in client.get(f"{BASE}/sales/{table}").json()["columns"]:
            words += [c.get("sentence", ""), c["state_words"]] + [v["text"] for v in (c.get("cells") or {}).values()]
    for w in words:
        assert w.strip().lower() not in ("null", "none") and "None" not in w, w
        assert not re.search(r"\d{4}-\d\d-\d\dT", w), w                       # no raw ISO timestamp
        assert "_" not in w.replace("empty_t", "").replace("v_orders", ""), w   # only real names carry underscores
        assert not re.search(r"\b1 (columns|rows|tables|views)\b|\(s\)", w), w     # plurals by number


def test_unknown_table_and_unknown_database_are_404(client):
    assert client.get(f"{BASE}/sales/nope").status_code == 404
    assert client.get("/api/databases/nope/value-profile").status_code == 404


def test_a_database_never_profiled_says_so(tmp_path, monkeypatch):
    reg = ProjectRegistry(db_path=str(tmp_path / "none.db"))
    reg.register_database(DatabaseEntity(slug=SLUG, display_name="Coco ODS", db_type="postgresql",
                                         host="localhost", port=5442, database_name="coco_ods"))
    from resource_explorer.value_profile_screen import build_summary, container_rows

    d = build_summary(reg, SLUG)
    assert d["profiled"] is False and "no value profile yet" in d["sentence"]
    assert container_rows(reg, SLUG) is None


def test_container_reader_one_row_per_table_not_measured_last(registry):
    from resource_explorer.surveyors.database.survey_definition_adapter import DATABASE_ANALYSIS_CONTAINER_RESULTS_MAP

    rows = DATABASE_ANALYSIS_CONTAINER_RESULTS_MAP["value_profile"](registry, SLUG)
    assert [r["name"] for r in rows] == ["sales.empty_t", "sales.orders", "sales.big", "sales.v_orders"]
    assert rows[1] == {"name": "sales.orders", "value": "4 rows · 5 of 5 columns measured", "note": "",
                       "opens": "/next?type=db&resource=coco_ods&tab=value_profile&table=sales.orders"}
    assert rows[0]["value"] == "0 rows · empty table · 2 of 2 columns measured"
    assert (rows[2]["value"], rows[2]["note"]) == ("not measured", BUDGET)
    assert all(r["opens"] for r in rows)


def test_q0079_field_level_numbers_read_the_container_rows(registry):
    """The measurements route at level field (Q-0079's level) returns the per-table rows, not the four scalars."""
    from resource_explorer.workflows.stage_page import build_measurements

    out = build_measurements(registry, SLUG, "value_profile", entity_type="database", level="field")
    assert [m["name"] for m in out["measurements"]] == ["sales.empty_t", "sales.orders", "sales.big", "sales.v_orders"]


def test_q0079_facts_say_which_analysis_has_numbers_at_the_field_level(registry):
    """§5: the row's own button opens Q-0079's first analysis (data_class_match); the value profile's per-table numbers
    are reachable because its fact says it has numbers at the question's level, and only at a sub-resource level."""
    from resource_explorer.facts import FactLayer

    layer = FactLayer(registry, resource_type="database")
    at_field = {f.analysis_id: f for f in layer.facts(SLUG, ["data_class_match", "value_profile"], "field")}
    assert at_field["value_profile"].numbers_at_level is True
    assert at_field["value_profile"].as_dict()["numbers_at_level"] is True
    assert at_field["data_class_match"].numbers_at_level is False
    at_resource = layer.facts(SLUG, ["value_profile"], "resource")
    assert at_resource[0].numbers_at_level is False


def test_titles_say_the_type_in_words(client):
    """VP-4: never "a other column", never TestGen's letter."""
    cols = _cells(client, "orders")
    assert cols["id"]["cells"]["type"]["title"] == "number: integer and decimal values"
    from resource_explorer.value_profile_screen import range_cell, distinct_cell

    other = {"general_type": "X", "value_ct": 4, "distinct_value_ct": None}
    assert range_cell(other)["title"] == "not a measure of a column of another type"
    assert distinct_cell(other)["title"] == "not a measure of a column of another type"
    assert range_cell({"general_type": "T", "value_ct": 2})["title"] == "not a measure of a time column"
    for c in cols.values():
        for cell in c["cells"].values():
            assert not re.search(r"\b[ANDBTX]\b", cell["title"]), cell["title"]


def test_tables_carry_what_they_are_and_their_rows_in_words(client):
    """VP-5: the list counts views apart from tables; a filtered card line needs the rows in words."""
    d = client.get(BASE).json()
    assert [(t["table_name"], t["kind"], t["rows_words"]) for t in d["tables"]] == [
        ("empty_t", "table", "0 rows · empty table"), ("orders", "table", "4 rows"),
        ("big", "table", "rows not counted"), ("v_orders", "view", "rows not counted")]


def test_the_q0079_headline_counts_in_words(registry):
    from resource_explorer.surveyors.database.survey_definition_adapter import _value_profile_headline

    assert _value_profile_headline(registry, SLUG)["label"] == \
        "7 of 10 columns profiled · 1 not applicable · 2 not measured."
    rows = [dict(PROFILE[0], table_name=f"t{i}", column_name="id") for i in range(1236)]
    registry.write_detail_rows("database_value_profiles", SLUG, "2026-10-31T00:00:00", rows=rows)
    assert _value_profile_headline(registry, SLUG)["label"] == "1,236 of 1,236 columns profiled."
