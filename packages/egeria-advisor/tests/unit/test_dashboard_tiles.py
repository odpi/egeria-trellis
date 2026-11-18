"""EA Dashboards, parity read side (brief B7a): tiles, the per-credential result cache, ⚙ overrides,
drill-down, nested sheets.

Fixtures are real shapes, never hand-made (house rule):
  - fixtures/dashboards/live_results_2026-11-11.json: exec_report_spec results captured read-only on laz
    from the live sheets (a count, PIE/BAR/SERIES charts, dicts of numbers, records, text);
  - fixtures/dashboards/pyegeria_6234_metric_trend.json and pyegeria_6234_saved_query.json: produced by
    pyegeria 6.2.34's own code over its own test fakes (W2 trend points; Route B answers).
The sheet and visibility machinery is test_egeria_dashboards.py's: pyegeria's real resolver over the
vendored in-memory Egeria, one fake per viewer, chosen by the token their client carries.
"""
from __future__ import annotations

import json
import pathlib
import threading
import time

import pytest

from advisor import egeria_dashboards as ed
from advisor.report_pipeline import ReportPipeline

from test_egeria_dashboards import (  # noqa: F401 -- fixtures and the viewer fakes
    DEMO, ERIN, ERIN_TOKEN, PETER, PETER_TOKEN, _isolated, _report, _write, viewers,
)

FIX = pathlib.Path(__file__).parent / "fixtures" / "dashboards"
LIVE = json.loads((FIX / "live_results_2026-11-11.json").read_text())
TREND = json.loads((FIX / "pyegeria_6234_metric_trend.json").read_text())
SAVED = json.loads((FIX / "pyegeria_6234_saved_query.json").read_text())
STRUCTURED = json.loads((FIX / "structured_table_laz_2026_11_12.json").read_text())
GE = "≥"


def tile(raw, fmt="DICT", spec="spec"):
    return ed._tile(raw, fmt, spec, ReportPipeline)


# ── tiles from real results ───────────────────────────────────────────────────

def test_a_count_is_a_kpi():
    t = tile(LIVE["count_int"]["result"])
    assert t == {"kind": "kpi", "value": 68739, "display": "68739"}


@pytest.mark.parametrize("key", ["vega_pie", "vega_bar", "vega_series_growth"])
def test_a_vega_lite_result_is_a_chart_drawn_as_pyegeria_made_it(key):
    raw = LIVE[key]["result"]
    t = tile(raw, LIVE[key]["output_format"])
    assert t["kind"] == "chart" and t["spec"] is raw["data"]


def test_a_dict_of_numbers_is_a_kpi_set():
    t = tile(LIVE["kpis_dict"]["result"])
    assert t["kind"] == "kpis"
    assert [(i["label"], i["display"]) for i in t["items"]] == [("total", "1065"), ("defined", "540"),
                                                               ("undefinedPct", "49")]


def test_an_unmeasured_count_is_said_and_never_a_number():
    t = tile(LIVE["kpis_with_unmeasured"]["result"])
    shown = {i["label"]: i["display"] for i in t["items"]}
    assert shown == {"contextualisedCount": "272", "assetTotal": "not measured", "contextualisedPct": "not measured"}
    assert t["unmeasured"] and "at least 500" in t["unmeasured"][0]


def test_nested_dicts_and_records_are_tables():
    for key in ("nested_dict", "record_list"):
        t = tile(LIVE[key]["result"])
        assert t["kind"] == "markdown" and "|" in t["markdown"], key


def test_text_kinds_are_markdown_as_written():
    t = tile(LIVE["text_mermaid"]["result"], "MERMAID")
    assert t["kind"] == "markdown" and "```mermaid" in t["markdown"]
    t = tile(LIVE["text_list"]["result"], "LIST")
    assert t["kind"] == "markdown" and t["markdown"].startswith("### Projects Table")


# ── metric_trend points: W2 (pyegeria 6.2.34) ─────────────────────────────────

def test_an_exact_trend():
    t = tile(TREND["exact_DICT"])
    assert t["kind"] == "trend" and t["latest"] == "1065" and t["bounds"] == 0 and t["inexact"] == 0
    assert [p["display"] for p in t["points"]] == ["200", "200", "1065"]
    assert all(p["exact"] is True for p in t["points"])


def test_a_capped_point_is_a_lower_bound_never_a_bare_number():
    t = tile(TREND["capped_DICT"])
    assert t["kind"] == "trend" and t["bounds"] == 2
    past = t["points"][:2]
    for p in past:
        assert p["display"] == f"{GE}500" and p["value"] is None and p["capped"] and p["at_least"] == 500
    assert t["points"][2]["display"] == "1065"
    assert t["unmeasured"]


def test_a_capped_point_keeps_its_marker_even_if_the_producer_dropped_display():
    raw = json.loads(json.dumps(TREND["capped_DICT"]))
    for p in raw["data"]:
        p.pop("display", None)
    raw["data"][0]["display"] = "500"          # a bare number for a lower bound: W2 forbids it
    t = tile(raw)
    assert [p["display"] for p in t["points"]][:2] == [f"{GE}500", f"{GE}500"]


def test_an_unmeasured_point_says_so():
    t = tile(TREND["not_measured_DICT"])
    assert [p["display"] for p in t["points"]][:2] == ["not measured", "not measured"]
    assert all(p["value"] is None for p in t["points"][:2])


def test_trend_charts_and_absent_trends_from_the_executor():
    assert tile(TREND["capped_SERIES"], "SERIES")["kind"] == "chart"
    t = tile(TREND["not_measured_SERIES"], "SERIES")
    assert t["kind"] == "absent" and "not measured" in t["message"]


def test_a_point_before_6_2_34_is_shown_but_not_claimed_exact():
    raw = json.loads(json.dumps(TREND["exact_DICT"]))
    for p in raw["data"]:
        p.pop("measurement"), p.pop("display")
    t = tile(raw)
    assert [p["display"] for p in t["points"]] == ["200", "200", "1065"]
    assert all(p["exact"] is None for p in t["points"])


# ── Route B: saved_query answers ──────────────────────────────────────────────

def test_a_cached_saved_query_is_a_kpi_with_its_as_of_time():
    t = tile({"kind": "json", "data": SAVED["cached_count"]})
    assert (t["kind"], t["display"], t["mode"], t["as_of"]) == ("kpi", "37", "cached", "2026-11-10T18:02:11+00:00")


def test_a_count_answer_has_no_member_table_under_it():
    # Real shape: pyegeria's count answer always sends "elements": None. The tile must not render
    # "No results returned" under the number (live finding F1, 2026-11-11).
    t = tile({"kind": "json", "data": SAVED["cached_count"]})
    assert "markdown" not in t


def test_a_list_answer_keeps_its_member_table():
    t = tile({"kind": "json", "data": SAVED["cached_list"]})
    assert t["kind"] == "kpi" and t.get("markdown")


def test_a_capped_saved_query_is_a_lower_bound():
    t = tile({"kind": "json", "data": SAVED["cached_capped"]})
    assert t["display"] == f"{GE}30" and t["value"] is None and t["capped"] is True


def test_a_never_refreshed_saved_query_is_absent_not_zero():
    t = tile({"kind": "json", "data": SAVED["never_refreshed"]})
    assert t["kind"] == "absent" and "never been refreshed" in t["message"]


def test_a_saved_query_list_shows_its_members():
    t = tile({"kind": "json", "data": SAVED["cached_list"]})
    assert t["display"] == "2" and "Asset 0" in t["markdown"]


def test_empty_and_absent_say_why():
    assert tile({"kind": "empty"})["message"] == "'spec' ran and found nothing."
    assert tile({"kind": "absent", "content": "Not applicable here."})["message"] == "Not applicable here."


# ── formats ───────────────────────────────────────────────────────────────────

def test_output_formats():
    assert ed._display_format("SERIES", None) == ("SERIES", None)
    assert ed._display_format("DICT", None) == ("DICT", None)
    fmt, note = ed._display_format("SPREADSHEET", None)
    assert fmt == "TABLE" and "cannot show" in note
    assert ed._display_format("DICT", "pie") == ("PIE", None)


# ── running placements: the result cache ──────────────────────────────────────

class RawPipeline:
    """EA's pipeline as the dashboards module uses it: exec_spec_raw (the raw result) and _format_output."""

    def __init__(self, raw=None, error=None, delay=0.0):
        self.raw = raw if raw is not None else LIVE["record_list"]["result"]
        self.error, self.delay = error, delay
        self.calls = []
        self.lock = threading.Lock()

    def exec_spec_raw(self, report_name, **kw):
        with self.lock:
            self.calls.append((report_name, kw))
        if self.delay:
            time.sleep(self.delay)
        return (None, self.error) if self.error else (self.raw, None)

    _format_output = staticmethod(ReportPipeline._format_output)

    def _read_pyegeria_connection(self, egeria_credentials=None):
        return {"view_server": "fake-view", "platform_url": "https://fake:9443",
                "user_id": egeria_credentials["user_id"], "user_pwd": "", "token": egeria_credentials["token"]}


def test_a_result_is_cached_per_credential_never_shared(viewers):
    pipe = RawPipeline()
    first = ed.run_placement(PETER, DEMO, 1, pipeline=pipe)
    assert first["state"] == "ok" and first["cache"]["state"] == "miss"
    again = ed.run_placement(PETER, DEMO, 1, pipeline=pipe)
    assert again["cache"]["state"] == "hit" and len(pipe.calls) == 1
    erin = ed.run_placement(ERIN, DEMO, 1, pipeline=pipe)
    assert erin["cache"]["state"] == "miss" and len(pipe.calls) == 2      # another credential: its own run
    assert pipe.calls[1][1]["egeria_credentials"] is ERIN
    assert ed.run_placement(PETER, DEMO, 1, pipeline=pipe)["cache"]["state"] == "hit"   # peter's is still his


def test_refresh_runs_now(viewers):
    pipe = RawPipeline()
    ed.run_placement(PETER, DEMO, 1, pipeline=pipe)
    out = ed.run_placement(PETER, DEMO, 1, pipeline=pipe, refresh=True)
    assert out["cache"]["state"] == "refresh" and len(pipe.calls) == 2


def test_a_failed_run_is_never_cached(viewers):
    pipe = RawPipeline(error="the report timed out")
    out = ed.run_placement(PETER, DEMO, 1, pipeline=pipe)
    assert out["state"] == "failed" and "timed out" in out["message"]
    ed.run_placement(PETER, DEMO, 1, pipeline=pipe)
    assert len(pipe.calls) == 2


def test_stale_is_served_at_once_and_refreshed_once_in_the_background(viewers, monkeypatch):
    pipe = RawPipeline()
    ed.run_placement(PETER, DEMO, 1, pipeline=pipe)
    key = next(iter(ed._results))
    wall, mono, payload = ed._results[key]
    ed._results[key] = (wall, mono - ed.RESULT_FRESH_S - 1, payload)          # now stale
    stale = ed.run_placement(PETER, DEMO, 1, pipeline=pipe)
    assert stale["cache"]["state"] == "stale" and stale["cache"]["age_s"] > ed.RESULT_FRESH_S
    ed._refresher().submit(lambda: None).result(timeout=5)                     # let the refresh finish
    deadline = time.time() + 5
    while key in ed._refreshing and time.time() < deadline:
        time.sleep(0.01)
    assert len(pipe.calls) == 2
    assert ed.run_placement(PETER, DEMO, 1, pipeline=pipe)["cache"]["state"] == "hit"


def test_identical_runs_in_flight_run_once(viewers):
    pipe = RawPipeline(delay=0.3)
    out = []
    threads = [threading.Thread(target=lambda: out.append(ed.run_placement(PETER, DEMO, 1, pipeline=pipe)))
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(pipe.calls) == 1 and all(o["state"] == "ok" for o in out)
    assert sorted(o["cache"]["state"] for o in out) == ["miss", "shared", "shared", "shared"]


def test_the_viewer_sees_a_kpi_tile_for_an_analytic_placement(viewers):
    peter = viewers[PETER_TOKEN]
    props = peter.entities[peter.guids["peter"]]["props"]["additionalProperties"]
    props["reportSpec"] = "Analytic Demo - Element Count by Type"
    props["analyticParams"] = '{"type_name": "Asset"}'
    pipe = RawPipeline(raw=LIVE["count_int"]["result"])
    out = ed.run_placement(PETER, DEMO, 2, pipeline=pipe)
    assert out["tile"] == {"kind": "kpi", "value": 68739, "display": "68739"} and out["analytic"] is True
    assert pipe.calls[0][1]["extra_params"] == {"type_name": "Asset"} and pipe.calls[0][1]["exact_params"]


def test_a_spec_fetched_as_the_viewer_runs_under_the_name_pyegeria_gave_it(viewers, monkeypatch):
    """pyegeria 6.2.34: a spec no tier holds is fetched from Egeria as the viewer and runs as
    "egeria:<label>@v<N>" (ResolvedPlacement.fetched_as). Run by its bare label, the executor says
    "Unknown report spec" -- what the live 'Analytic - Generic Metric Trend' placement did before."""
    real = ed._resolved

    def fetched(*a, **kw):
        resolved, answer = real(*a, **kw)
        if resolved is not None:
            pl = next(p for p in resolved.placements if p.position == 1)
            pl.fetched_as = f"egeria:{pl.spec}@v2"
        return resolved, answer
    monkeypatch.setattr(ed, "_resolved", fetched)
    pipe = RawPipeline()
    out = ed.run_placement(PETER, DEMO, 1, pipeline=pipe)
    assert out["state"] == "ok" and pipe.calls[0][0] == f"egeria:{out['spec']}@v2"
    assert not out["spec"].startswith("egeria:")                 # the tile still names the spec's label
    assert ed.run_placement(PETER, DEMO, 1, pipeline=pipe)["cache"]["state"] == "hit"


# ── ⚙ overrides ───────────────────────────────────────────────────────────────

def test_overrides_change_the_run_and_its_cache_key(viewers):
    pipe = RawPipeline()
    ed.run_placement(PETER, DEMO, 1, pipeline=pipe)
    out = ed.run_placement(PETER, DEMO, 1, pipeline=pipe, params={"search_string": "Sales"})
    assert out["cache"]["state"] == "miss" and out["overridden"] is True
    assert pipe.calls[-1][1]["search_string"] == "Sales"


def test_an_override_for_a_parameter_the_placement_lacks_is_refused(viewers):
    out = ed.run_placement(PETER, DEMO, 1, pipeline=RawPipeline(), params={"collectionGuid": "x"})
    assert out["state"] == "refused" and "not a parameter of this placement" in out["message"]


def test_analytic_overrides_keep_their_type():
    base = {"type_name": "Asset", "points": 6, "type_map": [["A", "Asset"]]}
    assert ed._apply_overrides(base, {"points": "3"}, True)["points"] == 3
    assert ed._apply_overrides(base, {"type_name": "GlossaryTerm"}, True)["type_name"] == "GlossaryTerm"
    with pytest.raises(ed.ParamRefused, match="give it as JSON"):
        ed._apply_overrides(base, {"type_map": "Asset"}, True)
    with pytest.raises(ed.ParamRefused, match="must stay a int"):
        ed._apply_overrides(base, {"points": '"six"'}, True)
    with pytest.raises(ed.ParamRefused, match="not a parameter"):
        ed._apply_overrides(base, {"searchString": "x"}, True)     # an analytic spec has no search string
    with pytest.raises(ed.ParamRefused, match="control characters"):
        ed._apply_overrides(base, {"type_name": "A\x00"}, True)
    assert ed._apply_overrides(base, {"type_name": ""}, True)["type_name"] == "Asset"   # empty keeps stored


def test_stored_list_junk_is_not_a_parameter():
    class R:
        params = {"searchString": "*", "limitResultByStatus": "['ACTIVE']"}
        analytic_params = {}

    class P:
        report = R()
    assert ed._base_params(P(), False) == {"searchString": "*"}


# ── drill-down and nested sheets ──────────────────────────────────────────────

def _sheet_with(fake, name, placements):
    import asyncio
    from pyegeria.view._output_dashboard_sheet_models import DashboardSheet
    asyncio.run(_write(fake, DashboardSheet(name=name, heading=name, placements=placements)))


def test_drill_down_runs_the_detail_spec_as_the_viewer(viewers):
    from pyegeria.view._output_dashboard_sheet_models import Placement
    peter = viewers[PETER_TOKEN]
    _sheet_with(peter, "drill", [Placement(ref="Public Collections", detail_spec="Collections")])
    shown = ed.show_sheet(PETER, "drill")
    assert shown["sheet"]["placements"][0]["detail_spec"] == "Collections"
    pipe = RawPipeline()
    out = ed.run_detail(PETER, "drill", 1, pipeline=pipe)
    assert out["state"] == "ok" and out["detail_spec"] == "Collections" and out["tile"]["kind"] == "markdown"
    assert pipe.calls[0][0] == "Collections" and pipe.calls[0][1]["egeria_credentials"] is PETER


def test_no_drill_down_and_an_unknown_drill_down_spec_are_said(viewers):
    from pyegeria.view._output_dashboard_sheet_models import Placement
    peter = viewers[PETER_TOKEN]
    _sheet_with(peter, "drill2", [Placement(ref="Public Collections"),
                                  Placement(ref="Peter Only", detail_spec="No Such Spec")])
    assert "has no drill-down" in ed.run_detail(PETER, "drill2", 1, pipeline=RawPipeline())["message"]
    out = ed.run_detail(PETER, "drill2", 2, pipeline=RawPipeline())
    assert out["state"] == "refused" and "'No Such Spec' is not in EA's report catalog" in out["message"]
    assert "restricted" in ed.run_detail(ERIN, DEMO, 2, pipeline=RawPipeline())["message"]


def test_a_placement_naming_a_visible_sheet_is_a_nested_sheet(viewers):
    from pyegeria.view._output_dashboard_sheet_models import Placement
    for token in (PETER_TOKEN, ERIN_TOKEN):
        fake = viewers[token]
        _report(fake, "Inner Sheet", "Collections")          # a placement field, linked later
        _sheet_with(fake, "inner", [Placement(ref="Public Collections")])
        _sheet_with(fake, "outer", [Placement(ref="inner", span="full")])
    viewers[ERIN_TOKEN].hidden.add(viewers[ERIN_TOKEN].by_qn("Dashboard::inner"))   # erin cannot see it
    peter = ed.show_sheet(PETER, "outer")["sheet"]["placements"][0]
    assert peter["kind"] == "sheet" and peter["sheet_ref"] == "inner" and "nested sheet" in peter["note"]
    erin = ed.show_sheet(ERIN, "outer")["sheet"]["placements"][0]
    assert erin["kind"] == "missing" and "sheet_ref" not in erin


# ── HTTP ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def http(viewers, monkeypatch):
    from fastapi.testclient import TestClient
    from advisor import auth
    from advisor.web.app import app
    monkeypatch.setattr(auth, "_auth_enabled", lambda: True)
    client = TestClient(app, raise_server_exceptions=False)

    def as_user(user, token):
        return {"Authorization": f"Bearer {auth.create_access_token(user_id=user, egeria_token=token)}"}
    return client, as_user


def test_http_run_with_params_and_detail(http, monkeypatch):
    client, as_user = http
    peter = as_user("peterprofile", PETER_TOKEN)
    pipe = RawPipeline(raw=LIVE["kpis_dict"]["result"])
    monkeypatch.setattr("advisor.report_pipeline.get_report_pipeline", lambda: pipe)
    out = client.post(f"/api/dashboards/{DEMO}/placements/1/run", headers=peter,
                      json={"params": {"search_string": "Sales"}, "refresh": True}).json()
    assert out["state"] == "ok" and out["tile"]["kind"] == "kpis" and out["cache"]["state"] == "refresh"
    bad = client.post(f"/api/dashboards/{DEMO}/placements/1/run", headers=peter, json={"params": "x"})
    assert bad.status_code == 422
    nodetail = client.post(f"/api/dashboards/{DEMO}/placements/1/detail", headers=peter, json={}).json()
    assert nodetail["state"] == "refused"
    assert client.post(f"/api/dashboards/{DEMO}/placements/1/detail", json={}).status_code == 401


# ── the JS harness's tiles are the server's ───────────────────────────────────
# advisor/web/static/test/dashboards_view.test.mjs draws these tiles. They are made here by the real
# _tile() from the real-shape fixtures, so the page is tested on what the server sends, not on shapes
# typed for the test. Regenerate after a deliberate change: EA_REGEN_TILE_FIXTURE=1 pytest -k js_harness

TILES_FOR_JS = FIX / "tiles_for_js.json"


def _tiles_for_js():
    return {
        "_source": "Made by advisor.egeria_dashboards._tile() from the fixtures in this directory; see test_dashboard_tiles.py.",
        "kpi_count": tile(LIVE["count_int"]["result"]),
        "kpis_unmeasured": tile(LIVE["kpis_with_unmeasured"]["result"]),
        "chart_pie": tile(LIVE["vega_pie"]["result"], "PIE"),
        "markdown_mermaid": tile(LIVE["text_mermaid"]["result"], "MERMAID"),
        "markdown_records": tile(LIVE["record_list"]["result"]),
        "trend_exact": tile(TREND["exact_DICT"]),
        "trend_capped": tile(TREND["capped_DICT"]),
        "trend_not_measured": tile(TREND["not_measured_DICT"]),
        "absent_trend": tile(TREND["not_measured_SERIES"], "SERIES"),
        "saved_cached": tile({"kind": "json", "data": SAVED["cached_count"]}),
        "saved_capped": tile({"kind": "json", "data": SAVED["cached_capped"]}),
        "saved_live": tile({"kind": "json", "data": SAVED["live_count"]}),
        "empty": tile({"kind": "empty"}, spec="Collections"),
        # T1: structured TABLE results (pyegeria 6.2.39, laz), drawn by trellis-ui's egeria-table.js
        "table_risk": tile(STRUCTURED["Risk-DrE-Basic"], "TABLE", "Risk-DrE-Basic"),
        "table_digital_products": tile(STRUCTURED["Digital-Products"], "TABLE", "Digital-Products"),
        "table_collections": tile(STRUCTURED["Collections"], "TABLE", "Collections"),
    }


def test_js_harness_tiles_are_what_the_server_makes():
    import os
    made = json.loads(json.dumps(_tiles_for_js(), ensure_ascii=False))
    if os.environ.get("EA_REGEN_TILE_FIXTURE") == "1":
        TILES_FOR_JS.write_text(json.dumps(made, indent=1, ensure_ascii=False))
    assert json.loads(TILES_FOR_JS.read_text()) == made, (
        "tiles_for_js.json is out of date: regenerate with EA_REGEN_TILE_FIXTURE=1 pytest -k js_harness")


def test_the_live_cdo_sheet_serialises_as_the_page_expects():
    """The real resolved sheet (laz, read-only): kinds in sheet order, drill-downs, stored list junk dropped."""
    from pyegeria.view.dashboard_resolver import ResolvedSheet
    data = json.loads((FIX / "resolved_sheet_cdo_2026-11-11.json").read_text())
    data.pop("_source")
    sheet = ResolvedSheet(**data)
    placements = [ed._placement_dict(p) for p in sheet.placements]
    kinds = "".join("t" if p["kind"] == "text" else "r" for p in placements)
    assert kinds == "ttrrrtrrrrttrrtrrrtrrr"
    assert {p["position"]: p["detail_spec"] for p in placements if p["detail_spec"]} == {
        4: "Governance-Metric-DrE-Basic", 16: "Digital-Products", 18: "Information-Supply-Chain-DrE-Basic"}
    assert all(p["runnable"] for p in placements if p["kind"] == "report")
    for p in placements:
        assert not any(isinstance(v, str) and v.startswith("[") for v in p["params"].values()), p["params"]
