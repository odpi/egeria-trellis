"""The Relationship Graph Evidence-rail block's "open one schema" zoom level
(`data-act="evidence-graphviz-schema"`, added in `re/relationship-graph-rendering`,
merged as PR #339) rendered as a bare `<select>` with no visual affordance that
it's interactive -- no border/hover/focus distinct from surrounding text, no
icon suggesting "this opens a menu". It read as inert text next to its sibling
"Schema map"/"Whole database" buttons, which ARE styled as buttons (bordered,
accent-colored, with an icon). The project owner tried it live and flagged
this.

Fix is pure CSS/markup: give the select the same button-like treatment as its
siblings (`border-accent`/`text-accent-on-dark`/hover state) plus a chevron
icon, wrapped in a relative container so the icon can sit inside the control.
`data-act` and the `<option>` list are untouched, so existing/future behavior
wiring (the `change` listener in `renderEvidenceRail`) and any option-content
assertions are unaffected.

These are source-text assertions, matching this repo's established pattern
for pure front-end JS behaviour (see test_next_sidebar_group_collapse.py,
test_next_component_review.py, test_next_rail_states.py) -- there is no
jsdom/browser harness here, so live rendering was verified manually in a
browser instead; see
RELATIONSHIP-GRAPH-SCHEMA-SELECT-AFFORDANCE-IMPLEMENTED.md.

First pass of this fix committed WITHOUT rebuilding `tailwind-next.css`
(`frontend-build/npm run build:css:next`) -- `docs/Backlog.md`'s existing
MEDIUM item "tailwind-next.css has no build-freshness check and will
silently go stale again" (~line 3700), caught this time by the PR/CI
review session grepping the built CSS rather than by any test here, since
the source-text tests above only look at app.js and cannot see whether the
classes they assert exist were ever compiled. TestBuiltCssHasTheNewClasses
below is a narrow, concrete instance of that Backlog item's fix #1 (a
freshness check) -- scoped to just the fragile bracket/colon classes this
fix introduced (the ones with no "coincidentally already present" escape
hatch), not the general rebuild-and-diff CI check the Backlog item still
asks for."""
from __future__ import annotations

from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"


def _app():
    return (NEXT / "app.js").read_text(encoding="utf-8")


def _built_css():
    return (NEXT / "tailwind-next.css").read_text(encoding="utf-8")


def _schema_select_block():
    app = _app()
    marker = 'data-act="evidence-graphviz-schema"'
    assert marker in app, "the schema-select control moved or was removed"
    start = app.rindex("schemaNames.length", 0, app.index(marker))
    end = app.index("</div>` : ''}", app.index(marker)) + len("</div>` : ''}")
    return app[start:end]


class TestSchemaSelectVisualAffordance:
    def test_data_act_and_options_unchanged(self):
        block = _schema_select_block()
        assert 'data-act="evidence-graphviz-schema"' in block
        assert '<option value="">Open a schema…</option>' in block
        assert "schemaNames.map((s) =>" in block

    def test_select_carries_button_like_classes(self):
        """Same visual vocabulary as the sibling `data-act="evidence-graphviz"`
        buttons above it in the same block (border-accent / text-accent-on-dark),
        not the plain `border-rule-strong` / `text-ink` it had before."""
        block = _schema_select_block()
        assert "border-accent" in block
        assert "text-accent-on-dark" in block
        assert "hover:bg-accent-tint" in block
        assert "border-rule-strong" not in block

    def test_select_has_a_chevron_indicating_a_menu(self):
        block = _schema_select_block()
        assert "chevron-down" in block
        assert "appearance-none" in block

    def test_select_is_wrapped_so_the_icon_can_be_positioned_over_it(self):
        block = _schema_select_block()
        assert '<div class="relative">' in block
        assert "pointer-events-none absolute" in block


class TestBuiltCssHasTheNewClasses:
    """`/next` loads exactly one stylesheet, `tailwind-next.css`, built ahead
    of time (index.html:292 -- no CDN/JIT). A class introduced in app.js that
    was never compiled in renders as nothing: no error, no visual cue beyond
    the missing style. This checks the built CSS actually carries a rule for
    each of the classes this fix's `<select>`/wrapper/icon added that Tailwind
    escapes specially (bracket values, `focus:`/`hover:` variants) -- the ones
    most likely to be silently dropped by a stale build, and the ones a plain
    string-in-app.js test can't catch since app.js is right regardless of
    whether the CSS was ever rebuilt from it.

    Escaping note: Tailwind's CSS output escapes `[`, `]`, `:` and `.` in the
    selector with a literal backslash (e.g. `.right-\\[8px\\]{right:8px}`,
    `.focus\\:ring-accent:focus{...}`) -- matched literally below rather than
    as regex metacharacters."""

    NEW_SELECTORS = [
        r".appearance-none{",
        r".pointer-events-none{",
        r".right-\[8px\]{",
        r".focus\:outline-none:focus{",
        r".focus\:ring-1:focus{",
        r".focus\:ring-accent:focus{",
        r".hover\:bg-accent-tint:hover{",
    ]

    def test_every_new_selector_has_a_compiled_rule(self):
        css = _built_css()
        missing = [sel for sel in self.NEW_SELECTORS if sel not in css]
        assert not missing, (
            f"tailwind-next.css is stale -- missing compiled rules for {missing}. "
            "Rebuild with: cd packages/resource-explorer/frontend-build && "
            "npx tailwindcss -c tailwind-next.config.js -i ./input.css "
            "-o ../resource_explorer/web/static/next/tailwind-next.css --minify "
            "(see docs/Backlog.md's 'tailwind-next.css has no build-freshness "
            "check' item)."
        )
