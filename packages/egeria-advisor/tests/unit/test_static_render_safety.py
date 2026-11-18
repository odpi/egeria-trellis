"""EA Phase 0, S2: no unsanitised server or LLM text in EA's pages.

Source-level guards on advisor/web/static (the pages themselves are exercised by the node harness
advisor/web/static/test/render.test.mjs, which the last test here runs when node and jsdom exist):
- no script, stylesheet or font is loaded from a CDN;
- marked.parse is called only inside render.js, so every Markdown render goes through DOMPurify;
- no inline on*="..." handler is built from a ${...} value in the files S2 owns.

The node harness needs node and jsdom. jsdom is found through EA_JSDOM_DIR (a node_modules directory,
e.g. <trellis>/packages/resource-explorer/frontend-build/node_modules), or that same directory in this
or the main trellis checkout. Without them test_node_render_harness skips, unless
EA_RENDER_HARNESS_REQUIRED=1: then a missing node or jsdom, or any skipped harness test, FAILS.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "advisor" / "web" / "static"
VENDOR = STATIC / "vendor"
CDN = re.compile(r"cdn\.|jsdelivr|unpkg|cdnjs|googleapis\.com", re.I)

# admin.html is owned by S1 in Phase 0 (it still loads Tailwind from the CDN). Listed here so the
# exemption is visible and is removed when S1's page lands: the test below fails once it is clean.
CDN_EXEMPT = {"admin.html": "S1 owns admin.html in Phase 0; its Tailwind <script> is still on the CDN"}

S2_FILES = ["index.html", "plan_editor.js", "report_spec_canvas.js", "render.js", "dashboards_view.js",
            "dashboards_author.js"]


def _page_files():
    return sorted(p for p in STATIC.glob("*.html")) + sorted(p for p in STATIC.glob("*.js"))


def _cdn_refs(path: Path):
    return [(n, line.strip()) for n, line in enumerate(path.read_text().splitlines(), 1)
            if CDN.search(line) and re.search(r"(src|href)\s*=|import\s|fetch\(", line)]


def test_no_cdn_urls_in_static_html():
    offenders = {p.name: _cdn_refs(p) for p in STATIC.glob("*.html") if p.name not in CDN_EXEMPT}
    assert {k: v for k, v in offenders.items() if v} == {}


def test_cdn_exemptions_are_still_needed():
    for name in CDN_EXEMPT:
        path = STATIC / name
        assert path.exists() and _cdn_refs(path), (
            f"{name} no longer loads from a CDN: remove it from CDN_EXEMPT")


def test_index_loads_vendored_libraries_and_render_js_in_order():
    html = (STATIC / "index.html").read_text()
    srcs = re.findall(r'<script\s+src="([^"]+)"', html)
    order = [s.split("?")[0] for s in srcs]
    for lib in ("/static/vendor/marked.min.js", "/static/vendor/purify.min.js", "/static/render.js"):
        assert lib in order, lib
    assert order.index("/static/vendor/marked.min.js") < order.index("/static/render.js")
    assert order.index("/static/vendor/purify.min.js") < order.index("/static/render.js")
    import trellis_ui   # /static/trellis-ui/ is the shared package's, mounted by app.py (A-U3)
    for s in order:
        if s.startswith(trellis_ui.STATIC_URL + "/"):
            assert (trellis_ui.static_dir() / s[len(trellis_ui.STATIC_URL) + 1:]).is_file(), \
                f"{s} is referenced but not in trellis-ui"
        elif s.startswith("/static/"):
            assert (STATIC / s[len("/static/"):]).is_file(), f"{s} is referenced but not in static/"


def test_vendored_files_have_licences():
    files = {p.name for p in VENDOR.glob("*.js")}
    assert {"marked.min.js", "purify.min.js"} <= files
    licences = {p.name for p in (VENDOR / "licenses").iterdir()}
    for f in files:
        stem = f.split(".min.js")[0].replace("purify", "dompurify").replace("tailwindcss-play", "tailwindcss")
        assert f"{stem}-LICENSE" in licences, f"no licence for {f}"
    assert "3.4.17" in (VENDOR / "purify.min.js").read_text()[:200]


def test_marked_parse_only_inside_render_js():
    hits = []
    for p in _page_files():
        for n, line in enumerate(p.read_text().splitlines(), 1):
            if "marked.parse(" in line or re.search(r"\bmarked\s*\(", line):
                hits.append((p.name, n))
    assert hits and {name for name, _ in hits} == {"render.js"}, hits


def test_every_markdown_innerhtml_goes_through_render_markdown():
    """The sites the brief lists: each now assigns renderMarkdown(...) output."""
    index = (STATIC / "index.html").read_text()
    # Two of the brief's sites (a dashboard text placement, a dashboard run's output) moved with the
    # dashboards UI to dashboards_view.js (B7a), where every Markdown render is md() -> renderMarkdown.
    assert index.count("renderMarkdown(") >= 4
    dash = (STATIC / "dashboards_view.js").read_text()
    assert "root.renderMarkdown(pre)" in dash and dash.count("md(") >= 3
    assert "renderMarkdown(_ped.outcome)" in (STATIC / "plan_editor.js").read_text()


@pytest.mark.parametrize("name", S2_FILES)
def test_no_inline_handler_built_from_values(name):
    text = (STATIC / name).read_text()
    bad = [(n, line.strip()) for n, line in enumerate(text.splitlines(), 1)
           if re.search(r"""\bon[a-z]+\s*=\s*["'][^"']*\$\{""", line)
           and "encodeURIComponent(_ped.doc_id)" not in line]  # static HTML, evaluated JS, no server text
    assert bad == []


@pytest.mark.parametrize("name", ["plan_editor.js", "report_spec_canvas.js", "dashboards_view.js",
                                  "dashboards_author.js"])
def test_no_inline_onclick_in_js_modules(name):
    assert 'onclick="' not in (STATIC / name).read_text()


def test_doc_id_banners_are_escaped():
    index = (STATIC / "index.html").read_text()
    assert "${result.doc_id}" not in index
    assert index.count("${_esc(result.doc_id)}.md</code>") == 4


def test_esc_escapes_quotes():
    body = re.search(r"function _esc\(s\) \{(.*?)\n\}", (STATIC / "index.html").read_text(), re.S).group(1)
    assert "&quot;" in body and "&#39;" in body


def test_feedback_click_checks_the_response():
    index = (STATIC / "index.html").read_text()
    fn = index[index.index("async function feedbackClick"):index.index("// ── Utils ──")]
    assert "res.ok" in fn and "Not recorded" in fn and "Sending…" in fn
    assert ".catch(() => {})" not in fn
    assert "dataset.responseText" not in index, "response text must be read at click time"


def _jsdom_available() -> bool:
    repo = STATIC.parents[4]
    dirs = [os.environ.get("EA_JSDOM_DIR"),
            repo / "packages/resource-explorer/frontend-build/node_modules",
            repo.parent / "trellis/packages/resource-explorer/frontend-build/node_modules"]
    return any(d and (Path(d) / "jsdom" / "package.json").exists() for d in dirs)


def test_node_render_harness():
    required = os.environ.get("EA_RENDER_HARNESS_REQUIRED") == "1"
    missing = ("node is not installed" if shutil.which("node") is None
               else None if _jsdom_available() else "jsdom not found (set EA_JSDOM_DIR)")
    if missing:
        if required:
            pytest.fail(f"EA_RENDER_HARNESS_REQUIRED=1 but {missing}: the security harness did not run")
        pytest.skip(missing)
    # Every harness file: render.test.mjs (S2), look.test.mjs (A-U3), dashboards_view.test.mjs (B7a),
    # dashboards_author.test.mjs (B7b), and any added later.
    files = sorted(str(f) for f in (STATIC / "test").glob("*.test.mjs"))
    assert any(f.endswith("render.test.mjs") for f in files)
    r = subprocess.run(["node", "--test", *files], capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
    assert re.search(r"^# fail 0$", r.stdout, re.M) and re.search(r"^# skipped 0$", r.stdout, re.M), r.stdout[-2000:]
    assert re.search(r"^# pass [1-9]\d*$", r.stdout, re.M), r.stdout[-2000:]


def test_required_harness_fails_instead_of_skipping(monkeypatch):
    """EA_RENDER_HARNESS_REQUIRED=1 turns a missing jsdom into a failure, not a skip."""
    monkeypatch.setitem(globals(), "_jsdom_available", lambda: False)
    monkeypatch.setenv("EA_RENDER_HARNESS_REQUIRED", "1")
    with pytest.raises(pytest.fail.Exception, match="security harness did not run"):
        test_node_render_harness()
    monkeypatch.delenv("EA_RENDER_HARNESS_REQUIRED")
    with pytest.raises(pytest.skip.Exception):
        test_node_render_harness()
