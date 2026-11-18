"""EA dashboards draw TABLE with the shared renderer (brief T1).

- A TABLE placement on a find spec asks pyegeria for its structured table (output TABLE,
  table_shape="structured") and the tile carries it as is: no DICT-then-markdown round trip
  (no ReportPipeline._format_output call for TABLE).
- An analytic spec's TABLE stays DICT (counts, charts, saved-query answers); REPORT is unchanged.
- exec_spec_raw sends table_shape to exec_report_spec only when asked; otherwise not at all.
- trellis-ui's egeria-table.js is the Portal's file, byte for byte (the pinned sha256).

Real shapes: fixtures/dashboards/structured_table_laz_2026_11_12.json, pyegeria 6.2.39 structured TABLE
results captured read-only on laz for the CDO sheet's specs (the Portal's P1a fixture, copied unchanged).
"""
from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

from advisor import egeria_dashboards as ed
from advisor.report_pipeline import ReportPipeline

from test_dashboard_tiles import LIVE, RawPipeline, tile  # noqa: F401
from test_egeria_dashboards import DEMO, PETER, PETER_TOKEN, viewers  # noqa: F401

FIX = pathlib.Path(__file__).parent / "fixtures" / "dashboards"
STRUCTURED = json.loads((FIX / "structured_table_laz_2026_11_12.json").read_text())
REPO = pathlib.Path(__file__).resolve().parents[4]
VENDORED = REPO / "packages" / "trellis-ui" / "trellis_ui" / "static" / "egeria-table.js"
SOURCE = json.loads((VENDORED.parent / "egeria-table.js.source.json").read_text())

PINNED_COMMIT = "3b8ac7e2836ec24eec469be14bf36e35dc1d4bbc"
PINNED_SHA256 = "5ad395feb3e7402fd58825a7ceddccc1c9ae1883edf343d56880ebb12ea95069"


# ── the vendored renderer ─────────────────────────────────────────────────────

def test_vendored_egeria_table_is_the_portals_file_unchanged():
    """Drift is a failure, not a surprise: the copy must be the pinned Portal file. To update it, copy the
    new file unchanged and change the pin here and in egeria-table.js.source.json together."""
    body = VENDORED.read_bytes()
    assert hashlib.sha256(body).hexdigest() == PINNED_SHA256, (
        "trellis-ui/static/egeria-table.js differs from odpi/egeria-workspaces@" + PINNED_COMMIT
        + ": never edit the vendored copy")
    assert SOURCE["commit"] == PINNED_COMMIT and SOURCE["sha256"] == PINNED_SHA256
    assert SOURCE["repo"] == "odpi/egeria-workspaces"
    assert SOURCE["path"] == "compose-configs/egeria-quickstart/PyegeriaWebHandler/static/egeria-table.js"
    assert len(body.decode().splitlines()) == SOURCE["lines"] == 549


def test_the_page_loads_the_renderer_before_the_dashboards():
    html = (REPO / "packages" / "egeria-advisor" / "advisor" / "web" / "static" / "index.html").read_text()
    assert html.index('src="/static/trellis-ui/egeria-table.js') < html.index('src="/static/dashboards_view.js')


# ── tiles ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("spec", [k for k in STRUCTURED if not k.startswith("_")])
def test_a_structured_table_is_a_table_tile_as_is(spec):
    raw = dict(STRUCTURED[spec], spec_source={"tier": "builtin", "label": spec})
    t = tile(raw, "TABLE", spec)
    assert t["kind"] == "table" and "markdown" not in t
    for k in ("columns", "rows", "total", "total_basis", "shown", "notes"):
        assert t["table"][k] == STRUCTURED[spec][k]
    assert t["table"]["kind"] == "table"


def test_a_table_tile_never_goes_through_format_output(monkeypatch):
    class NoMarkdown(RawPipeline):
        @staticmethod
        def _format_output(*a, **k):
            raise AssertionError("no markdown round trip for a structured TABLE")
    t = ed._tile(STRUCTURED["Digital-Products"], "TABLE", "Digital-Products", NoMarkdown)
    assert t["kind"] == "table"


# ── what is asked of pyegeria ─────────────────────────────────────────────────

def _run(fmt, *, analytic=False, raw=None):
    pipe = RawPipeline(raw=raw or STRUCTURED["Risk-DrE-Basic"])
    out = ed._execute("Risk-DrE-Basic", analytic=analytic, params={"search_string": "Risk::"}, fmt=fmt,
                      creds=PETER, pipeline=pipe, title="Risk Register")
    return out, pipe.calls[0][1]


def test_a_table_placement_asks_for_the_structured_table():
    out, kw = _run("TABLE")
    assert kw["output_type"] == "TABLE" and kw["table_shape"] == "structured"
    assert kw["search_string"] == "Risk::"
    assert out["state"] == "ok" and out["tile"]["kind"] == "table" and out["response"] is None


def test_an_analytic_table_stays_dict_with_no_table_shape():
    out, kw = _run("TABLE", analytic=True, raw=LIVE["count_int"]["result"])
    assert kw["output_type"] == "DICT" and "table_shape" not in kw
    assert out["tile"]["kind"] == "kpi"


@pytest.mark.parametrize("fmt,sent", [("REPORT", "REPORT"), ("MARKDOWN", "REPORT"), ("DICT", "DICT"),
                                      ("LIST", "LIST"), ("PIE", "PIE")])
def test_other_formats_are_unchanged(fmt, sent):
    raw = {"kind": "text", "mime": "text/markdown", "content": "# Risks\n\n- one"}
    out, kw = _run(fmt, raw=raw)
    assert kw["output_type"] == sent and "table_shape" not in kw
    assert out["tile"]["kind"] == "markdown"


def test_a_table_placement_end_to_end(viewers):
    """run_placement on the vendored in-memory Egeria: the placement's TABLE reaches pyegeria as TABLE,
    structured, and the page gets the table."""
    peter = viewers[PETER_TOKEN]
    props = peter.entities[peter.guids["peter"]]["props"]["additionalProperties"]
    props["outputFormat"] = "TABLE"
    pipe = RawPipeline(raw=STRUCTURED["Collections"])
    out = ed.run_placement(PETER, DEMO, 2, pipeline=pipe)
    assert out["output_format"] == "TABLE" and out["tile"]["kind"] == "table"
    assert pipe.calls[0][1]["output_type"] == "TABLE" and pipe.calls[0][1]["table_shape"] == "structured"
    assert out["tile"]["table"]["total"] is None and out["tile"]["table"]["shown"] == 100   # "more may exist"


def test_a_drill_down_asks_for_the_structured_table_too(viewers):
    from pyegeria.view._output_dashboard_sheet_models import Placement
    from test_dashboard_tiles import _sheet_with
    _sheet_with(viewers[PETER_TOKEN], "drill-t1", [Placement(ref="Public Collections", detail_spec="Collections")])
    pipe = RawPipeline(raw=STRUCTURED["Collections"])
    out = ed.run_detail(PETER, "drill-t1", 1, pipeline=pipe)
    assert out["tile"]["kind"] == "table" and pipe.calls[0][1]["table_shape"] == "structured"


# ── exec_spec_raw passes table_shape only when asked ──────────────────────────

@pytest.fixture
def pipeline(monkeypatch):
    import pyegeria
    sent = {}

    def fake_exec(name, **kw):
        sent.update(kw, name=name)
        return {"kind": "empty"}

    monkeypatch.setattr(pyegeria, "exec_report_spec", fake_exec)
    p = ReportPipeline.__new__(ReportPipeline)
    p._default_page_size, p._starts_with_on_filter = 100, True
    p._read_pyegeria_connection = lambda egeria_credentials=None: {
        "view_server": "v", "platform_url": "https://p:9443", "user_id": "peterprofile", "user_pwd": "",
        "token": "tok"}
    return p, sent


def test_exec_spec_raw_sends_table_shape_when_asked(pipeline):
    p, sent = pipeline
    assert p.exec_spec_raw("Risk-DrE-Basic", output_type="TABLE", table_shape="structured") == ({"kind": "empty"}, None)
    assert sent["output_format"] == "TABLE" and sent["table_shape"] == "structured" and sent["token"] == "tok"


def test_exec_spec_raw_sends_no_table_shape_otherwise(pipeline):
    p, sent = pipeline
    p.exec_spec_raw("Risk-DrE-Basic", output_type="DICT")
    assert "table_shape" not in sent and sent["output_format"] == "DICT"
