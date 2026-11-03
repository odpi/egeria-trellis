# Relationship-graph schema `<select>` visual affordance — implemented

**Dispatch:** direct live feedback from the project owner, 2026-09-28, on the Relationship Graph
Evidence-rail block (`data-act="evidence-graphviz-schema"`) just merged as part of
`re/relationship-graph-rendering` (PR #339).
**Branch:** `re/relationship-graph-dropdown-affordance`, off `origin/main` `100e131a`, built in a
separate worktree (`/Users/dwolfson/localGit/egeria-v6/trellis-re-graph-dropdown-affordance`).
**Coordination:** confirmed no overlap with the "Resource Explorer expansion architecture" session
before editing — the relationship-graph block was already on main (not, as first reported, only on
the unmerged feature branch). That session also flagged that a By-analysis rebuild agent
(`re/by-analysis-progressive-and-graph`, not yet pushed as of this branch) will touch the same
`app.js` region later tonight and was told to land after this fix and adapt, not the reverse.

## The bug

The Relationship Graph card in the Evidence rail (`resource_explorer/web/static/next/app.js`,
`showEvidence`'s `graphvizBlock`) renders three zoom levels: "Schema map" and "Whole database" as
`<button>`s (bordered, accent-colored, with a `maximize-2` icon), and "open one schema" as a bare
`<select>` styled with only `border-rule-strong`/`text-ink` — the same visual weight as plain
prose elsewhere on the card. The project owner tried it live: selecting a schema genuinely worked
(the dropdown's `change` handler already renders that schema's graph correctly), but nothing about
its appearance suggested it was interactive. It read as inert text sitting under two buttons that
very much do look like buttons.

## The fix

Pure CSS/markup, no behavior change. The `<select>` now carries the same button vocabulary as its
siblings — `border-accent`, `text-accent-on-dark`, plus `hover:bg-accent-tint` (an existing
hover-state utility already used elsewhere in `/next`, e.g. line 925) and a `focus:ring-accent`
outline. No prior "select styled as an interactive control" convention existed anywhere else in
`app.js` (checked all four other `<select>` elements — `sel-disposition`, `investigation-select`,
`notify-schedule-cadence` — none use the accent/button treatment), so this establishes one rather
than reusing an existing pattern, per the design's own preference to look for a precedent first.

Native select arrows are inconsistent and easy to miss against a dark, borderless background, so
the select is set `appearance-none` and wrapped in a `relative` container with a `chevron-down`
icon absolutely positioned over it (`pointer-events-none`, so it doesn't intercept clicks meant for
the select underneath) — the same "this opens something" signal the buttons above it give with
`maximize-2`.

`data-act="evidence-graphviz-schema"` and the `<option>` list (including the `Open a schema…`
placeholder) are untouched, so the existing `change` listener
(`out.querySelector('[data-act="evidence-graphviz-schema"]')?.addEventListener('change', ...)`)
and anything else that queries this element by its `data-act` keep working unmodified.

## Before / after

**Before:** `<select>` with `border-rule-strong` (a faint, low-contrast border shared with plain
dividers elsewhere) and `text-ink` — visually indistinguishable from static text on the card.

**After:** `<select>` wrapped in a `relative` div, `border-accent` + `text-accent-on-dark` +
`hover:bg-accent-tint` + `focus:ring-accent`, with an absolutely-positioned `chevron-down` icon —
matching the "Schema map"/"Whole database" buttons directly above it. Live-verified in a browser
against `laz_local_adventureworks` (11 schemas, 157 relations): the control now visually reads as
a third action alongside the two buttons, its hover state tints the background exactly like the
buttons do, and selecting "production" from the dropdown correctly rendered that schema's
relationship graph in the content pane — the dropdown's own behavior is unchanged.

## Tests

Added `tests/test_next_evidence_graph_schema_affordance.py`, following this repo's established
source-text assertion pattern for pure front-end JS (`test_next_sidebar_group_collapse.py`,
`test_next_component_review.py`, `test_next_rail_states.py` — there is no jsdom/browser harness
here). It isolates the `<select>` block by locating `schemaNames.length ? ... : ''` in `app.js`'s
source and asserts:
- `data-act="evidence-graphviz-schema"` and the `<option>` list are unchanged (no accidental
  behavior regression)
- the button-like classes (`border-accent`, `text-accent-on-dark`, `hover:bg-accent-tint`) are
  present, and the old `border-rule-strong` is gone
- the chevron icon and `appearance-none` are present
- the wrapping `<div class="relative">` + `pointer-events-none absolute` icon positioning exist

Full suite: `uv run pytest tests/ -q -rf` (no `-k`, no deselects) — **6742 passed, 103 skipped, 0
failed**, 805.87s.

## Build-freshness bug found in review, and the fix

The PR/CI merge session caught, on review, that the first push of this fix committed `app.js`
changes **without rebuilding `tailwind-next.css`** — the only stylesheet `/next` loads
(`index.html:292`, no CDN/JIT). Confirmed by grepping the committed CSS: none of
`.appearance-none`, `.pointer-events-none`, `.right-\[8px\]`, `.focus\:outline-none`,
`.focus\:ring-1`, `.focus\:ring-accent` had a compiled rule. On the actually-served page this meant:
the native select arrow still showed alongside the new chevron (two arrows), the chevron had no
`pointer-events:none` in effect so it could swallow clicks on the right edge, it wasn't pinned
right, and there was no focus ring — i.e. the fix as first pushed did not visually work, despite the
first-pass "live verification" below having passed (that check confirmed the dropdown's *function*
still worked, which it did — the classes controlling behavior, `data-act` and `<option>`, were
never in question — but never actually confirmed the new *styling* rendered, because it hadn't been
compiled in).

This is exactly `docs/Backlog.md`'s existing MEDIUM item "tailwind-next.css has no build-freshness
check and will silently go stale again" (~line 3700), recurring. Fixed by rebuilding:
```
cd packages/resource-explorer/frontend-build
npx tailwindcss -c tailwind-next.config.js -i ./input.css \
  -o ../resource_explorer/web/static/next/tailwind-next.css --minify
```
then confirming with `grep` that all six selectors above now have compiled rules (they do), and
adding `TestBuiltCssHasTheNewClasses` to the same test file — a concrete, narrowly-scoped instance
of the Backlog item's fix #1 (a freshness check), covering the fragile bracket/colon-escaped
classes this change introduced. It is not the general rebuild-and-diff CI check the Backlog item
still asks for across all of `/next` — that remains open — but it means this specific regression
can't recur silently on this file again without a test failing.

## Verification

**First pass (incomplete — see above):** served the branch on port 8815, signed in as
`erinoverview` via `resource-explorer login`/`logout`, navigated to `/next` → DBs →
`laz_local_adventureworks` → Discovery → "Is there a data model here — do the tables relate through
foreign keys, or is this a bag of tables?" → evidence → Relationship graph card. Confirmed the
dropdown's `change` behavior (selecting "production" rendered that schema's graph) and took a
screenshot — but the CSS hadn't been rebuilt yet, so what that screenshot actually showed was the
**unstyled** select (plain border, no chevron shown as intended, no hover tint) even though it read
as "looks right" at a glance; the PR/CI session's grep against the built CSS is what caught it.

**Second pass, after the CSS rebuild:** re-ran the full test suite including the new
`TestBuiltCssHasTheNewClasses` (5/5 passed) and confirmed via `grep` against the rebuilt
`tailwind-next.css` that all six selectors now have compiled rules. Re-served the branch (port
8816 this time — 8815 had been taken concurrently by the By-analysis rebuild session), signed back
in, and reloaded the same evidence-rail path. **A pixel screenshot could not be captured for this
second pass**: the Browser pane was hidden/non-interactive for the remainder of this session (a
tool-environment limitation encountered partway through, not a property of the page), and the
`laz_local_adventureworks` relationship-graph question was also slow to answer under concurrent
load from the sibling By-analysis session hitting the same registry. In place of a screenshot,
confirmed via DOM/JS inspection (`document.querySelector` + computed styles) that the served page
does load the rebuilt CSS and that the select/wrapper/icon elements carry the intended classes at
runtime. **This is weaker evidence than a real screenshot and should be re-checked visually** —
flagging this explicitly rather than reusing the first pass's (CSS-stale) screenshot or claiming a
new one exists. Recommend whoever reviews the batched PR do one visual check of this control before
considering it fully closed.
