"""The Curate pane in /next: what a Python suite can hold it to without a
browser. The screen itself was verified on a branch server (2026-09-12)."""
from __future__ import annotations

import re
from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"


def _app():
    """app.js plus every stages/*.js module, concatenated -- see the
    identical helper's docstring in test_next_component_review.py."""
    src = (NEXT / "app.js").read_text(encoding="utf-8")
    for f in sorted((NEXT / "stages").glob("*.js")):
        src += "\n" + f.read_text(encoding="utf-8")
    return src


class TestTheScreenHoldsTheDesignRules:
    def test_it_renders_whether_or_not_the_catalog_has_rows_for_the_stage(self):
        app = _app()
        i = app.index("renderCurate(slug).catch(")
        j = app.index("if (!state.questions.length) {", i)
        assert i < j, "Curate must render before the empty-question early return"

    def test_only_what_it_is_is_confirmed_row_by_row(self):
        app = _app()
        cols = app[app.index("const CURATE_COLUMNS = ["):app.index("];", app.index("const CURATE_COLUMNS = ["))]
        assert cols.count("pick: true") == 1 and "key: 'what_it_is'" in cols.split("pick: true")[0].rsplit("{", 1)[-1]

    def test_the_population_rule_is_said_and_the_button_is_gated_not_hidden(self):
        app = _app()
        # renderCurate moved to stages/curate.js; bound to its own next
        # function (renderCatalogueDepthOffer), not app.js's rowKey.
        body = app[app.index("async function renderCurate("):app.index("async function renderCatalogueDepthOffer(")]
        assert "Only worthy things get curated" in body
        # The button now lives in the commit panel (stages/repo-manifest.js, brief section 2): gated by
        # every blocker, never hidden, with the reason directly under it.
        panel = (NEXT / "stages" / "repo-manifest.js").read_text(encoding="utf-8")
        assert "data-curate-go ${off ? 'disabled' : ''}" in panel
        assert "sign in to publish" in panel and "not in Curate’s population" in panel

    def test_every_count_opens_its_members_and_the_manifest_names_the_three_rules(self):
        app = _app()
        # Spans curateRowHtml (the button's HTML) through renderCurate (the
        # click wiring for it), so bounded at the next function after
        # renderCurate, not immediately after curateRowHtml.
        body = app[app.index("function curateRowHtml("):app.index("async function renderCatalogueDepthOffer(")]
        assert "data-curate-members" in body and "openMembers({ slug, analysisId: b.dataset.curateMembers" in body
        assert "testimony copied · measurements linked · unresolved things travel" in body
        assert "Reversing this needs a correction, which stays on the record." in body

    def test_the_commit_is_polled_and_the_record_redrawn_per_step(self):
        app = _app()
        body = app[app.index("async function renderCurate("):app.index("async function renderCatalogueDepthOffer(")]
        assert "await pollActivity(out.activity_id" in body and "getCuration(slug, out.curation.id)" in body
        assert "runs in the worker, not here" in app
