"""The read-only registry stand-in answers exactly what a real ProjectRegistry answers, and cannot write."""
import sqlite3

import pytest

from conftest import add_curation_rows, write_local_survey, write_native_survey

from trellis_analytics.readers.ods import OdsReader                     # noqa: E402
from trellis_analytics.readers.registry_view import COVERED_METHODS     # noqa: E402


@pytest.fixture
def world(make_registry):
    reg, url = make_registry("demo")
    reg.record_database_survey("demo", 3, 6, 6, {"x": 1}, source="local", surveyed_at="2026-10-01T00:00:00Z")
    reg.record_database_survey("demo", 4, 8, 8, {"y": 2}, source="egeria", surveyed_at="2026-10-04T00:00:00Z")
    write_local_survey(reg, "demo", ["loc_a", "shared", "information_schema"], tables=("t", "u"))
    write_native_survey(reg, "demo", ["shared", "nat_a", "nat_b"], tables_per=2, at="2026-10-04T06:00:00", guid="rep-9")
    view = OdsReader(url).registry_view()
    return reg, view


def test_covered_methods_are_exactly_the_ones_resolve_node_set_needs():
    # resolve_node_set may call only these on its registry; each is answered below
    assert set(COVERED_METHODS) == {"_conn", "_normalize_slug", "query_detail_rows", "get_database_surveys",
                                    "list_native_survey_runs", "count_native_survey_annotations",
                                    "query_native_survey_annotations"}


def test_normalize_slug(world):
    reg, view = world
    assert view._normalize_slug("Coco-Pharma") == reg._normalize_slug("Coco-Pharma") == "coco_pharma"


def test_query_detail_rows(world):
    reg, view = world
    for table in ("database_tables", "database_columns", "database_table_activity"):
        assert view.query_detail_rows(table, "demo") == reg.query_detail_rows(table, "demo")
    assert view.query_detail_rows("database_tables", "demo")                      # not vacuous
    assert view.query_detail_rows("database_tables", "demo", None, "local") == reg.query_detail_rows("database_tables", "demo", None, "local")


def test_get_database_surveys(world):
    reg, view = world
    got = view.get_database_surveys("demo")
    assert got == reg.get_database_surveys("demo") and len(got) == 2
    assert view.get_database_surveys("demo", include_invalid=True) == reg.get_database_surveys("demo", include_invalid=True)


def test_native_survey_reads(world):
    reg, view = world
    runs = view.list_native_survey_runs("database", "demo")
    assert runs == reg.list_native_survey_runs("database", "demo") and len(runs) == 1
    guid = runs[0]["survey_report_guid"]
    assert view.count_native_survey_annotations(guid) == reg.count_native_survey_annotations(guid) > 0
    assert view.query_native_survey_annotations(guid) == reg.query_native_survey_annotations(guid)


def test_resolve_node_set_is_identical_through_the_view(world):
    from resource_explorer.catalogue_scope import resolve_node_set
    reg, view = world
    assert resolve_node_set(view, "demo") == resolve_node_set(reg, "demo")


def test_the_view_cannot_write_and_nothing_else_is_reachable(world):
    reg, view = world
    with view._conn() as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM database_tables")
    with pytest.raises(AttributeError):
        view.append_catalogue_scope_event
    assert reg.query_detail_rows("database_tables", "demo")          # and the rows are still there
