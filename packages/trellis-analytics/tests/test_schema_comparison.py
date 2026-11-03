"""schema_comparison (DESIGN-RESOURCE-COMPARISON-QUESTIONS-2026-10-30, CMP1): "is the schema of database 1 the same as
the schema of database 2?" as a relation with a degree, never yes/no, run by the wrapper as a set step over a real
(scratch SQLite) registry through the ODS reader's typed read only.

Each relation (identical, superset, subset, overlapping, disjoint) from known inventories; the degree "N of M tables",
the column degree and the differing column named; the categories adding up to the tables on the union; one side
unsurveyed or unregistered is not established, with its sentence and no number; a set that is not two databases is not
applicable."""
import re
import tomllib
from pathlib import Path

import pytest

STEP = Path(__file__).resolve().parents[1] / "examples" / "steps" / "schema_comparison"
SET = "wl_compare"
AT1, AT2 = "2026-10-24T10:00:00", "2026-10-30T09:00:00"


@pytest.fixture
def installed(tmp_path, monkeypatch):
    """The step installed the way pip would: a dist-info carrying its pyproject's entry point, its source on the path."""
    project = tomllib.loads((STEP / "pyproject.toml").read_text())["project"]
    version = re.search(r'__version__ = "([^"]+)"',
                        (STEP / "src" / "schema_comparison" / "__init__.py").read_text()).group(1)
    d = tmp_path / "site" / f"trellis_step_schema_comparison-{version}.dist-info"
    d.mkdir(parents=True)
    (d / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {project['name']}\nVersion: {version}\n")
    (d / "entry_points.txt").write_text("[pyegeria.analytics]\n" + "".join(
        f"{k} = {v}\n" for k, v in project["entry-points"]["pyegeria.analytics"].items()))
    monkeypatch.syspath_prepend(str(tmp_path / "site"))
    monkeypatch.syspath_prepend(str(STEP / "src"))
    yield
    from resource_explorer.surveyors import survey_step_host
    monkeypatch.undo()
    survey_step_host.refresh("database")


#: public.<table>: [(column, data_type)]
BASE = {"customer": [("id", "integer"), ("name", "text"), ("email", "text")],
        "orders": [("id", "integer"), ("customer_id", "integer"), ("amount", "numeric")],
        "product": [("id", "integer"), ("title", "text")],
        "store": [("id", "integer"), ("city", "text")]}


def _survey(reg, slug, tables, at, *, nullable=None):
    reg.record_database_survey(slug, 1, len(tables), sum(len(c) for c in tables.values()), {}, surveyed_at=at)
    reg.write_detail_rows("database_tables", slug, at, rows=[
        {"schema_name": "public", "table_name": t, "table_type": "BASE TABLE"} for t in tables])
    reg.write_detail_rows("database_columns", slug, at, rows=[
        {"schema_name": "public", "table_name": t, "column_name": c, "ordinal_position": i + 1, "data_type": ty,
         "is_nullable": (nullable or {}).get(f"{t}.{c}", 1), "is_primary_key": int(c == "id")}
        for t, cols in tables.items() for i, (c, ty) in enumerate(cols)])


@pytest.fixture
def pair(make_registry):
    """(registry, url) with two registered databases, db1 and db2, neither surveyed."""
    from resource_explorer.registry import DatabaseEntity

    reg, url = make_registry("db1")
    reg.register_database(DatabaseEntity(slug="db2", display_name="db2", db_type="postgresql", host="localhost",
                                         port=5442, database_name="db2"))
    return reg, url


def _run(url, members=("db1", "db2")):
    from trellis_analytics.discovery import discover
    from trellis_analytics.readers.ods import OdsReader
    from trellis_analytics.step import HostContext, SurveyStep

    host = HostContext(resource_type="work_list", slug=SET, producing_run="run-c", ods=OdsReader(url),
                       members=tuple(("database", m) for m in members), set_slug=SET)
    anns = SurveyStep(discover()["schema_comparison"]).run(host)
    assert all(a.summary for a in anns)
    return {a.scope: a for a in anns}


def _state(a) -> str:
    return a.envelope.result_state.value


def test_the_manifest_is_a_set_step_answering_the_schema_questions(installed):
    from trellis_analytics.discovery import discover

    m = discover()["schema_comparison"]
    assert m.subject == "set" and m.resource_types == ["database"] and m.inputs == ["ods"]
    assert m.credential_tier == "none" and m.questions == ["Q-0110", "Q-0112"]
    d = m.declaration
    assert d.is_complete() and d.categories.exclusive and d.categories.sums_to == "tables_on_union"
    [measure] = d.measures
    assert measure.value_field == "schema_similarity" and measure.basis.denominator_field == "tables_on_union"


def test_a_copy_with_one_table_dropped_and_one_column_retyped_is_a_superset_with_its_degrees(installed, pair):
    reg, url = pair
    _survey(reg, "db1", BASE, AT1)
    copy = {t: list(c) for t, c in BASE.items() if t != "store"}
    copy["orders"] = [("id", "integer"), ("customer_id", "integer"), ("amount", "text")]
    _survey(reg, "db2", copy, AT2)
    out = _run(url)
    roll = out[SET]
    assert _state(roll) == "measured"
    assert roll.summary.startswith("db1 is a superset of db2: 3 of 4 tables match (75%); db1 adds 1 table · "
                                   "columns: 7 of 8 columns match by name and type (88%) in the 3 tables both hold · "
                                   "differ: public.orders.amount: numeric → text")
    p = roll.resource_properties
    assert (p["schema_relation"], p["schema_similarity"], p["tables_on_union"]) == ("superset", "3", "4")
    assert (p["column_similarity"], p["columns_compared"], p["columns_differing"]) == ("7", "8", "1")
    assert p["tables_only_in_1_names"] == "public.store" and "tables_only_in_2_names" not in p
    assert p["columns_differing_names"] == "public.orders.amount: numeric → text"
    # The categories are exclusive and add up to the tables on the union.
    cats = [int(p[k]) for k in ("identical_shape", "same_name_different_shape", "only_in_database_1",
                                "only_in_database_2")]
    assert cats == [2, 1, 1, 0] and sum(cats) == int(p["tables_on_union"])
    assert (p["as_of_1"], p["as_of_2"], p["asOf"]) == (AT1, AT2, AT1)
    # Each member carries the comparison as seen from it, naming the other.
    assert out["db1"].resource_properties["compared_with"] == "db2"
    assert out["db2"].resource_properties["compared_with"] == "db1"
    assert out["db2"].summary == roll.summary


def test_the_other_order_is_a_subset(installed, pair):
    reg, url = pair
    _survey(reg, "db1", BASE, AT1)
    _survey(reg, "db2", {t: c for t, c in BASE.items() if t != "store"}, AT2)
    roll = _run(url, ("db2", "db1"))[SET]
    assert roll.summary.startswith("db2 is a subset of db1: 3 of 4 tables match (75%); db1 adds 1 table · "
                                   "columns: 8 of 8 columns match")
    assert roll.resource_properties["schema_relation"] == "subset"


def test_identical_schemas(installed, pair):
    reg, url = pair
    _survey(reg, "db1", BASE, AT1)
    _survey(reg, "db2", BASE, AT2)
    roll = _run(url)[SET]
    assert roll.summary.startswith("db1 and db2 have identical schemas: 4 of 4 tables match (100%) · "
                                   "columns: 10 of 10 columns match by name and type (100%)")
    assert roll.resource_properties["identical_shape"] == "4"


def test_a_nullability_difference_is_named_and_keeps_the_pair_from_being_called_identical(installed, pair):
    reg, url = pair
    _survey(reg, "db1", BASE, AT1)
    _survey(reg, "db2", BASE, AT2, nullable={"customer.email": 0})
    roll = _run(url)[SET]
    p = roll.resource_properties
    assert p["schema_relation"] == "overlapping" and p["column_similarity"] == "10"
    assert "other differences: public.customer.email: nullable yes → no" in roll.summary
    assert roll.summary.startswith("db1 and db2 overlap: 4 of 4 tables match (100%) by name and type, and 1 column "
                                   "differ in nullability or key")


def test_overlapping_and_disjoint(installed, pair):
    reg, url = pair
    _survey(reg, "db1", {"customer": BASE["customer"], "orders": BASE["orders"]}, AT1)
    _survey(reg, "db2", {"customer": BASE["customer"], "store": BASE["store"]}, AT2)
    roll = _run(url)[SET]
    assert roll.summary.startswith("db1 and db2 overlap: 1 of 3 tables match (33%); db1 adds 1 table; db2 adds 1 table")
    _survey(reg, "db2", {"store": BASE["store"]}, "2026-10-30T10:00:00")
    roll = _run(url)[SET]
    assert roll.resource_properties["schema_relation"] == "disjoint"
    assert roll.summary.startswith("db1 and db2 are disjoint: 0 of 3 tables match (0%); db1 has 2 tables, db2 has 1 table")


def test_the_percentage_is_never_rounded_up_to_all(installed, pair):
    reg, url = pair
    many = {f"t{i:03d}": [("id", "integer")] for i in range(200)}
    _survey(reg, "db1", many, AT1)
    _survey(reg, "db2", {k: v for k, v in many.items() if k != "t000"}, AT2)
    assert "199 of 200 tables match (99%)" in _run(url)[SET].summary


def test_one_side_unsurveyed_is_not_established_with_its_sentence_and_no_number(installed, pair):
    reg, url = pair
    _survey(reg, "db1", BASE, AT1)
    out = _run(url)
    roll = out[SET]
    assert _state(roll) == "not_established"
    assert roll.summary == "Not established: db2 has no schema inventory in RE yet — survey it, then compare."
    assert "db2 has no schema inventory" in roll.envelope.result_state_reason
    for a in out.values():
        assert not any(v.isdigit() for v in a.resource_properties.values()), a.resource_properties   # no zero, no number
        assert a.resource_properties["schema_relation"] == "not_established"
    assert out["db2"].summary == roll.summary
    assert out["db1"].summary.startswith("Not established: compared with db2, which RE cannot compare yet.")


def test_an_unregistered_member_is_said_so(installed, pair):
    reg, url = pair
    _survey(reg, "db1", BASE, AT1)
    roll = _run(url, ("db1", "ghost"))[SET]
    assert _state(roll) == "not_established" and roll.summary == "Not established: ghost is not registered in RE."


def test_a_set_that_is_not_two_databases_is_not_applicable(installed, pair):
    reg, url = pair
    out = _run(url, ("db1",))
    assert _state(out[SET]) == "not_applicable"
    assert out[SET].summary == "Not applicable: the two members are not both databases."
    out = _run(url, ("db1", "db2", "db3"))
    assert _state(out[SET]) == "not_applicable" and "has 3 database members" in out[SET].summary


def test_two_inventories_with_no_tables_have_nothing_to_compare():
    from schema_comparison.step import compare

    empty = {"surveyed_at": AT1, "tables": []}
    assert compare(empty, empty)["tables_on_union"] == 0


def test_the_inventory_read_tells_unsurveyed_from_unregistered(pair):
    from trellis_analytics.readers.ods import OdsReader

    reg, url = pair
    _survey(reg, "db1", BASE, AT1)
    ods = OdsReader(url)
    inv = ods.schema_inventory("db1")
    assert inv["registered"] and inv["surveyed_at"] == AT1 and len(inv["tables"]) == 4
    orders = next(t for t in inv["tables"] if t["table_name"] == "orders")
    assert [c["column_name"] for c in orders["columns"]] == ["id", "customer_id", "amount"]
    assert (orders["columns"][2]["data_type"], orders["columns"][0]["is_primary_key"]) == ("numeric", 1)
    assert ods.schema_inventory("db2") == {"registered": True, "surveyed_at": None, "source": None, "tables": []}
    assert ods.schema_inventory("nope")["registered"] is False
