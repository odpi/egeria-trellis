"""A Report's parameters reach pyegeria in snake_case, as the Portal sends them (found live on 8880, 2026-11-12).

Egeria holds a Report's parameters as additionalProperties in camelCase. The Portal snake-cases every key
before it runs a placement (local-dashboards.html, snakeKey); EA passed them through unchanged, so the
"Tasks" TABLE tile on local-dashboards-next-steps (spec Collection Members) failed with "Give
collection_guid, collection_qname or collection_name" while the Portal drew 5 rows for the same tile.

Real shape: the Tasks Report as LOCAL_DASHBOARDS_NEXT_STEPS_REPORTS.dr-egeria.md created it
(`collection_qname : Coco Pharmaceuticals::WorkItemList::Local-Dashboards---Next-Steps::1.0`), which Egeria
holds as `collectionQname`, with `pageSize` and `graphQueryDepth` beside it as the Architect's read-back saw
them. pyegeria's own Collection Members spec is the check on the names that go out.
"""
from __future__ import annotations

import pytest
from test_dashboard_structured_table import STRUCTURED
from test_dashboard_tiles import RawPipeline
from test_egeria_dashboards import DEMO, PETER, PETER_TOKEN, _isolated, viewers  # noqa: F401

from advisor import egeria_dashboards as ed
from advisor.report_pipeline import ReportPipeline

TASKS_QN = "Coco Pharmaceuticals::WorkItemList::Local-Dashboards---Next-Steps::1.0"
TASKS_REPORT = {"reportSpec": "Collection Members", "outputFormat": "TABLE",
                "collectionQname": TASKS_QN, "pageSize": "0", "graphQueryDepth": "3"}


def _tasks_placement(viewers):
    peter = viewers[PETER_TOKEN]
    props = peter.entities[peter.guids["peter"]]["props"]["additionalProperties"]
    props.clear()
    props.update(TASKS_REPORT)


def test_the_tasks_tile_sends_collection_qname_in_snake_case(viewers):
    _tasks_placement(viewers)
    pipe = RawPipeline(raw=STRUCTURED["Collections"])
    out = ed.run_placement(PETER, DEMO, 2, pipeline=pipe)
    assert out["state"] == "ok" and out["tile"]["kind"] == "table", out
    name, kw = pipe.calls[0]
    assert name == "Collection Members"
    assert kw["extra_params"] == {"collection_qname": TASKS_QN, "page_size": "0", "graph_query_depth": "3"}
    assert kw["search_string"] == "*"
    assert not any(k[0].islower() and any(c.isupper() for c in k) for k in kw["extra_params"]), "camelCase left"


def test_the_names_sent_are_the_ones_pyegeria_s_collection_members_spec_takes():
    from pyegeria.view.base_report_formats import select_report_format
    action = select_report_format("Collection Members", "ANY")["action"]
    takes = set(action["required_params"]) | set(action["optional_params"])
    sent = ed._snake_params({"collectionQname": TASKS_QN, "pageSize": "0"})
    assert set(sent) <= takes, set(sent) - takes


def test_exec_report_spec_receives_snake_case_keys(viewers, monkeypatch):
    """Through the real ReportPipeline.exec_spec_raw: pyegeria's executor gets collection_qname, and the
    stored page_size (a string, as the Portal also sends it) replaces EA's default, not sits beside it."""
    import pyegeria
    sent = {}

    def fake_exec(name, **kw):
        sent.update(kw, name=name)
        return STRUCTURED["Collections"]
    monkeypatch.setattr(pyegeria, "exec_report_spec", fake_exec)
    p = ReportPipeline.__new__(ReportPipeline)
    p._default_page_size, p._starts_with_on_filter = 100, True
    p._read_pyegeria_connection = lambda egeria_credentials=None: {
        "view_server": "v", "platform_url": "https://p:9443", "user_id": "peterprofile", "user_pwd": "",
        "token": "tok"}
    _tasks_placement(viewers)
    out = ed.run_placement(PETER, DEMO, 2, pipeline=p)
    assert out["state"] == "ok", out
    params = sent["params"]
    assert params["collection_qname"] == TASKS_QN and "collectionQname" not in params
    assert params["page_size"] == "0" and "pageSize" not in params
    assert params["search_string"] == "*" and sent["table_shape"] == "structured"


def test_a_viewer_override_of_a_camel_case_parameter_is_snake_cased_too(viewers):
    _tasks_placement(viewers)
    pipe = RawPipeline(raw=STRUCTURED["Collections"])
    other = "Coco Pharmaceuticals::WorkItemList::Other::1.0"
    ed.run_placement(PETER, DEMO, 2, pipeline=pipe, params={"collectionQname": other})
    assert pipe.calls[-1][1]["extra_params"]["collection_qname"] == other


def test_a_stored_search_string_in_either_spelling_is_the_search_string(viewers):
    peter = viewers[PETER_TOKEN]
    props = peter.entities[peter.guids["peter"]]["props"]["additionalProperties"]
    props.pop("search_string", None)
    props["searchString"] = "Coco"
    pipe = RawPipeline()
    ed.run_placement(PETER, DEMO, 2, pipeline=pipe)
    assert pipe.calls[0][1]["search_string"] == "Coco" and not pipe.calls[0][1]["extra_params"]


@pytest.mark.parametrize("given,want", [
    ({"collectionQname": "q"}, {"collection_qname": "q"}),
    ({"search_string": "a", "searchString": "b"}, {"search_string": "a"}),   # the snake spelling wins
    ({"searchString": "b", "search_string": "a"}, {"search_string": "a"}),
    ({"type_name": "Asset"}, {"type_name": "Asset"}),
    ({"startsWith": "true", "pageSize": "0"}, {"starts_with": "true", "page_size": "0"}),   # values as stored
])
def test_snake_params_is_the_portals_snake_key(given, want):
    assert ed._snake_params(given) == want


def test_analytic_params_are_not_renamed(viewers):
    """An analytic spec's params are its function's own argument names; the Portal sends them as is."""
    peter = viewers[PETER_TOKEN]
    props = peter.entities[peter.guids["peter"]]["props"]["additionalProperties"]
    props["reportSpec"] = "Analytic Demo - Element Count by Type"
    props["analyticParams"] = '{"type_name": "Asset"}'
    from test_dashboard_tiles import LIVE
    pipe = RawPipeline(raw=LIVE["count_int"]["result"])
    ed.run_placement(PETER, DEMO, 2, pipeline=pipe)
    assert pipe.calls[0][1]["extra_params"] == {"type_name": "Asset"} and pipe.calls[0][1]["exact_params"]
