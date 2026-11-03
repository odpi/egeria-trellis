"""Curate is a generic `/next` nav item (reachable for any resource type),
but everything it does is built entirely against repo-only
`/api/projects/{slug}/...` routes (curate_plan.py, `registry.get(slug)` — the
repo-only `projects` table). Clicking Curate for a database/filesystem used
to reach `getCuratePlan(slug)` and 404 with no explanation — indistinguishable
from a real failure.

Fix: `renderCurate` in `web/static/next/stages/curate.js` now detects the
resource type first (`apiEntityType(state.resourceType)`) and, for anything
other than `repo`, renders an honest "not available for this type yet"
message instead of making any repo-only call — the same pattern
`understanding.js`'s `loadChartsPane`/`nonRepoChartIndexHtml` already uses
for its own deliberately-repo-only chart kinds.

These tests pin the fix at the source level (this codebase's established
technique for JS with no test runner wired in — see
`test_fact_answer_rendering.py`), by brace-matching the function bodies out
of the raw source text rather than executing them.
"""
from __future__ import annotations

from pathlib import Path

CURATE_JS = (Path(__file__).resolve().parents[1] / "resource_explorer" / "web"
             / "static" / "next" / "stages" / "curate.js")


def _fn(src: str, name: str) -> str:
    """Extract one top-level `function name(...) { ... }` (or `async
    function`) body by brace matching."""
    marker = f"function {name}("
    start = src.index(marker)
    brace = src.index("{", start)
    depth = 1
    i = brace + 1
    while depth:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
        i += 1
    return src[start:i]


def test_render_curate_checks_entity_type_before_any_repo_only_call():
    src = CURATE_JS.read_text()
    fn = _fn(src, "renderCurate")
    assert "apiEntityType" in fn, (
        "renderCurate must resolve the real entity type before deciding "
        "whether to make any /api/projects/{slug}/... call")
    # The entity-type check must appear textually before the first call to
    # getCuratePlan, so the honest branch is a real gate, not dead code after
    # the repo-only fetch already ran.
    gate_pos = fn.index("apiEntityType")
    fetch_pos = fn.index("getCuratePlan(")
    assert gate_pos < fetch_pos, (
        "the entity-type gate must run BEFORE getCuratePlan, not after — "
        "otherwise the repo-only call still fires and the honest message "
        "only decorates a failure that already happened")


def test_non_repo_curate_body_is_the_kinds_band_two_not_a_bare_404():
    """CURATE-UI-DATABASES: the old 'isn't available' body is replaced by the
    kind's own band 2 (database: two sections that say what they wait for;
    file system: one sentence). The honest-about-what-is-unbuilt standard stays."""
    src = CURATE_JS.read_text()
    fn = _fn(src, "nonRepoCurateHtml")
    assert "databaseWorkHtml" in fn and "filesystemWorkHtml" in fn
    bands = (CURATE_JS.parent / "curate-bands.js").read_text()
    assert "needs data classes per column (data_class_match)" in bands
    assert "needs the logical schemas Egeria knows to be read" in bands
    assert "Nothing to review for file systems yet. Group, tags, ratings and notes above and below apply." in bands


def test_a_database_slug_never_reaches_getcurateplan():
    """Simulates the gate: for any non-repo entityType, renderCurate's first
    action must be to render the honest message and `return` — never call
    the repo-only endpoint."""
    src = CURATE_JS.read_text()
    fn = _fn(src, "renderCurate")
    gate_block_start = fn.index("if (entityType !== 'repo')")
    gate_block = fn[gate_block_start:fn.index("getCuratePlan(")]
    assert "return;" in gate_block, (
        "the non-repo branch must return before falling through to the "
        "repo-only getCuratePlan() call below it")
    assert "nonRepoCurateHtml" in gate_block
