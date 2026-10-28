"""Chart labels, measured in a real headless Chrome.

Draws the Understanding charts with the real Plotly build, the real
stylesheet and the real `chart-labels.js`, at the two container widths the
page is used at, then measures every rendered label box: none may leave the
SVG, no two tick labels on one axis may overlap, and an axis caption may not
sit on its tick labels. Data is shaped like the owner's coco_pharma charts
(13 long column types, 8 schemas, 30 runs).

Skipped, not passed, when no Chrome is installed.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static"
NEXT = STATIC / "next"

CHROME = next((p for p in (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    shutil.which("google-chrome") or "", shutil.which("chromium") or "") if p and Path(p).exists()), None)
pytestmark = pytest.mark.skipif(CHROME is None, reason="no headless Chrome on this machine")

TYPES = ["character varying", "timestamp without time zone", "integer", "numeric", "text",
         "date", "double precision", "boolean", "bytea", "character", "uuid", "smallint",
         "timestamp with time zone"]
SCHEMAS = [f"{n}_schema" for n in ("public", "clinical", "regulatory_affairs", "manufacturing",
                                   "pharmacovigilance", "sales", "hr", "finance_and_accounting")]
RUNS = [f"2026-09-{d:02d}T09:00:00" for d in range(1, 29)] + [
    "2026-10-01T09:00:00", "2026-10-01T14:02:00"]


def figures():
    return {
        "column_types": {
            "data": [{"type": "bar", "x": TYPES, "y": [200 - 11 * i for i in range(len(TYPES))]}],
            "layout": {"yaxis": {"title": {"text": "Columns"}}}},
        "schema_distribution": {
            "data": [{"type": "bar", "orientation": "h", "name": "Tables", "y": SCHEMAS,
                      "x": [30 - 3 * i for i in range(8)], "xaxis": "x", "yaxis": "y"},
                     {"type": "bar", "orientation": "h", "name": "Columns", "y": SCHEMAS,
                      "x": [200 - 20 * i for i in range(8)], "xaxis": "x2", "yaxis": "y"}],
            "layout": {"xaxis": {"domain": [0, 0.45], "title": {"text": "Tables"}},
                       "xaxis2": {"domain": [0.55, 1], "title": {"text": "Columns"}},
                       "yaxis": {"autorange": "reversed"}, "showlegend": False}},
        "survey_history": {
            "data": [{"type": "scatter", "mode": "lines+markers", "name": n, "x": RUNS,
                      "y": [k * (i + 1) for i in range(len(RUNS))]}
                     for n, k in (("Schemas", 1), ("Tables", 3), ("Columns", 9))],
            "layout": {"xaxis": {"type": "date"}}},
        "table_growth": {
            "data": [{"type": "scatter", "mode": "lines+markers", "name": f"public.big_table_{t}",
                      "x": RUNS, "y": [1000 * (t + 1) + i for i in range(len(RUNS))]}
                     for t in range(3)],
            "layout": {"xaxis": {"type": "date"}}},
    }


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="file://{css}">
<script src="file://{plotly}"></script></head>
<body style="margin:0"><div id="host" style="width:{width}px"></div>
<script>
{labels_js}
const FIGS = {figs};
const W = {width};
const out = {{}};
(async () => {{
  for (const [kind, fig] of Object.entries(FIGS)) {{
    const div = document.createElement('div');
    div.style.cssText = 'width:' + W + 'px;height:260px';
    document.getElementById('host').appendChild(div);
    const fit = fitFigure(fig, W, kind);
    const layout = Object.assign(fit.layout, {{ width: W, height: 260,
      font: {{ size: 12 }}, xaxis: Object.assign({{ tickfont: {{ size: 11 }} }}, fit.layout.xaxis),
      yaxis: Object.assign({{ tickfont: {{ size: 11 }} }}, fit.layout.yaxis) }});
    if (layout.xaxis2) layout.xaxis2.tickfont = {{ size: 11 }};
    await Plotly.newPlot(div, fig.data, layout, {{ staticPlot: true }});
    annotateTicks(div, fit.alt, fit.summary);
    const svg = div.querySelector('svg.main-svg').getBoundingClientRect();
    // The label's own (unrotated) box carried through its transform: four
    // corners, so a rotated label is a parallelogram, not its loose AABB.
    const corners = (el) => {{
      const b = el.getBBox(); const m = el.getScreenCTM();
      return [[b.x, b.y], [b.x + b.width, b.y], [b.x + b.width, b.y + b.height], [b.x, b.y + b.height]]
        .map(([x, y]) => [m.a * x + m.c * y + m.e - svg.left, m.b * x + m.d * y + m.f - svg.top]);
    }};
    const box = corners;
    const own = (t) => Array.from(t.childNodes).filter((n) => n.nodeName !== 'title')
      .map((n) => n.textContent).join('');
    const labels = [];
    div.querySelectorAll('.xtick text, .ytick text').forEach((t) => {{
      const grp = t.closest('[class*=axislayer]');
      labels.push({{ axis: (t.closest('.xtick') ? 'x' : 'y') + ':' + (grp ? Array.from(
        grp.parentNode.children).indexOf(grp) + ':' + grp.parentNode.className.baseVal : ''),
        text: own(t), box: box(t), title: !!t.querySelector('title') }});
    }});
    const titles = [];
    div.querySelectorAll('.g-xtitle, .g-x2title, .g-ytitle').forEach((t) => {{
      if (t.textContent.trim()) titles.push({{ text: t.textContent, box: box(t) }}); }});
    out[kind] = {{ w: svg.width, h: svg.height, labels, titles,
      aria: div.getAttribute('aria-label') }};
  }}
  document.body.insertAdjacentHTML('beforeend', '<pre id="result">' +
    JSON.stringify(out).replace(/</g, '\\\\u003c') + '</pre>');
}})();
</script></body></html>"""


def measure(tmp_path, width, labels_js):
    page = tmp_path / f"p{width}.html"
    page.write_text(PAGE.format(
        css=NEXT / "tailwind-next.css", plotly=STATIC / "vendor" / "plotly.min.js",
        labels_js=labels_js, figs=json.dumps(figures()), width=width))
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--allow-file-access-from-files",
           f"--window-size={width + 40},1400", "--virtual-time-budget=8000",
           "--dump-dom", f"file://{page}"]
    for attempt in range(2):      # headless Chrome occasionally stalls at start-up
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            break
        except subprocess.TimeoutExpired:
            if attempt:
                raise
    m = re.search(r'<pre id="result">(\{.*?)</pre>', r.stdout, re.S)
    assert m, f"chart page did not finish drawing: {r.stderr[-400:]}"
    return json.loads(m.group(1).replace("&quot;", '"').replace("&amp;", "&"))


def inline_labels():
    src = (NEXT / "chart-labels.js").read_text()
    return re.sub(r"^export ", "", src, flags=re.M)


def aabb(poly):
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def overlap(a, b):
    """Do two convex quadrilaterals (4 corner points) overlap? Separating axis."""
    for poly in (a, b):
        for i in range(4):
            (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % 4]
            nx, ny = y0 - y1, x1 - x0
            pa = [nx * x + ny * y for x, y in a]
            pb = [nx * x + ny * y for x, y in b]
            if max(pa) <= min(pb) + 0.01 or max(pb) <= min(pa) + 0.01:
                return False
    return True


def problems(result):
    bad = []
    for kind, c in result.items():
        for item in c["labels"] + c["titles"]:
            x0, y0, x1, y1 = aabb(item["box"])
            if x0 < -0.5 or y0 < -0.5 or x1 > c["w"] + 0.5 or y1 > c["h"] + 0.5:
                bad.append(f"{kind}: '{item['text']}' leaves the chart {item['box']} in {c['w']}x{c['h']}")
        by_axis = {}
        for item in c["labels"]:
            by_axis.setdefault(item["axis"], []).append(item)
        for axis, items in by_axis.items():
            for i, a in enumerate(items):
                for b in items[i + 1:]:
                    if overlap(a["box"], b["box"]):
                        bad.append(f"{kind}: '{a['text']}' overlaps '{b['text']}' on {axis}")
        xt = [i for i in c["labels"] if i["axis"].startswith("x")]
        for t in c["titles"]:
            for i in xt:
                if overlap(t["box"], i["box"]):
                    bad.append(f"{kind}: caption '{t['text']}' sits on tick label '{i['text']}'")
        # x tick labels of different panels count together: panel two's ticks
        # must not touch panel one's.
        for i, a in enumerate(xt):
            for b in xt[i + 1:]:
                if a["axis"] != b["axis"] and overlap(a["box"], b["box"]):
                    bad.append(f"{kind}: '{a['text']}' touches '{b['text']}' across panels")
    return bad


@pytest.mark.parametrize("width", [1100, 760])
def test_no_label_is_clipped_or_overlaps(tmp_path, width):
    result = measure(tmp_path, width, inline_labels())
    assert set(result) == {"column_types", "schema_distribution", "survey_history", "table_growth"}
    assert all(c["labels"] for c in result.values()), "a chart drew no tick labels at all"
    assert problems(result) == []


@pytest.mark.parametrize("width", [1100, 760])
def test_long_names_are_shortened_with_the_full_name_kept(tmp_path, width):
    result = measure(tmp_path, width, inline_labels())
    col = result["column_types"]
    shown = [i["text"] for i in col["labels"] if i["axis"].startswith("x")]
    assert any(s.endswith("…") for s in shown), shown
    titled = [i for i in col["labels"] if i["text"].endswith("…")]
    assert titled and all(i["title"] for i in titled), "a shortened label has no <title> tooltip"
    for name in ("timestamp without time zone", "timestamp with time zone", "character varying"):
        assert name in col["aria"], "the full name is missing from the chart's text alternative"
    # the first and last categories stay labelled
    assert shown[0].startswith("character var") and shown[-1].startswith("timestamp with")


@pytest.mark.parametrize("width", [1100, 760])
def test_history_x_axis_keeps_first_and_last_and_is_thinned(tmp_path, width):
    result = measure(tmp_path, width, inline_labels())
    for kind in ("survey_history", "table_growth"):
        xs = [i["text"] for i in result[kind]["labels"] if i["axis"].startswith("x")]
        assert xs[0].startswith("09-01") and xs[-1].startswith("10-01"), xs
        assert len(xs) < len(RUNS), "30 runs must be thinned, not all labelled"


def test_the_legacy_layout_fails_these_checks(tmp_path):
    """The same measurement against the pre-fix layout (no fitting) must find
    problems: proves the assertions can fail."""
    legacy = ("function fitFigure(fig){ return {layout: Object.assign({}, fig.layout, "
              "{margin:{l:56,r:16,t:12,b:40}}), alt: [], summary: ''}; }"
              "function annotateTicks(){}")
    assert problems(measure(tmp_path, 760, legacy)), "the old layout measured clean; the check is blind"
