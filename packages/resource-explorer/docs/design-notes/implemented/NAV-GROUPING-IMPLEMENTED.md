# Nav grouping — implemented

**Replies to:** `docs/design-notes/rulings/RULING-NAV-GROUPING.md` (2026-09-18) — the
ruling answering a peer critique of the nine-item `/next` intent row.
**Branch:** `re/nav-grouping`, worktree `.claude/worktrees/wt-nav-grouping`.
Nothing was written in the main checkout.

---

## What the ruling asked for, verified against the actual code first

Before building, I re-checked the ruling's own factual claims against the
source it cites, rather than taking them on faith:

- **"`frame: true` renders Investigation in `text-accent-on-dark`, and the
  comment beside Work lists says they are a FRAME..."** — confirmed. Both
  were exactly as described in `app.js`'s `STAGES` array and the comment
  above the Work-lists `<span>` in the old `renderIntentNav()`.
- **"`STAGES`' comment... is now wrong in its count... six ordered intents,
  not eight."** — confirmed; the old comment read *"The eight intents, in
  their canonical order, plus Investigation as the frame."*
- **"`tailwind-next.config.js` states the governing rule — 'colour is never
  the sole channel here'"** — the quote is real, at
  `frontend-build/tailwind-next.config.js:90`, but it is stated specifically
  about the `state-warn`/gold luminance collision (glyph + wording as the
  second channel there), not about the nav's frame/stage distinction. The
  ruling extends that doctrine to the nav by analogy rather than the config
  file itself mentioning the nav — worth being precise about, since the
  ruling's phrasing reads as if the file says so directly. The extension is
  reasonable (the same principle, applied to a second place hue is used
  alone) and is what this change implements, but it's the ruling's inference,
  not a second citation of the same sentence.
- **`app.js:145`'s "renders charts now" comment** — still present (now at a
  different line number after edits), unchanged, matches the ruling's
  citation.

No claim in the ruling turned out to be wrong; the one above is a precision
note, not a correction.

## 1 · `STAGES` now declares `class`, not `frame`/nothing

Each `STAGES` entry gets a `class` field: `'frame'` (Investigation),
`'run'` (Scouting, Discovery, Assessment, Analysis, Enrichment, Curate — in
that array order), or `'cross-cutting'` (Understanding, Automate). The old
`frame: true` boolean is gone; every read site (`renderIntentNav()`'s
grouping, and `loadPane()`'s `stageDef?.class === 'frame'` gate that shows
"not in /next" for Investigation) now reads the same field. The stale
"eight intents" comment is rewritten to describe the three classes.

Understanding's array position stays between Enrichment and Curate (moving
it would be a bigger, unnecessary diff); its `class: 'cross-cutting'` is what
pulls it out of the run's sequence for rendering and numbering, not its
position in the source array. A `RUN_ORDER` map, built by
`STAGES.filter((s) => s.class === 'run')`, is the only source of the run's
1–6 numbering — not a second literal id list.

## 2 · The nav renderer derives grouping from `STAGES.class`

`renderIntentNav()` was rewritten around a new `navItemHtml(stage, {number})`
helper (one per-item renderer, used for all three classes) plus three
`STAGES.filter()` calls (`frameItems`/`runItems`/`crossItems`). Chevrons
(`NAV_CHEVRON`, `›`) join items inside `runItems`; middots (`NAV_MIDDOT`,
`·`) join `crossItems` to each other and separate the run from the
cross-cutting group; a middot also now precedes the Work-lists control,
which is not itself a `STAGES` entry but is conceptually frame-class per the
ruling and gets the same "no sequence relationship to what precedes it"
treatment. Numbering (`RUN_ORDER.get(s.id)`) is passed only for run items.

Live-verified (see §5): the rendered strip reads `Investigation | 1 Scouting
› 2 Discovery › 3 Assessment › 4 Analysis › 5 Enrichment › 6 Curate ·
Understanding · Automate · ▦ Work lists`, matching the ruling's example
exactly.

Regression coverage: `tests/test_next_nav_grouping.py` asserts the `class`
field on every `STAGES` entry (including that Understanding does **not**
carry `class: 'run'` — the specific defect this ruling fixes), that
`RUN_ORDER` is computed rather than duplicated, that the renderer's grouping
comes from `STAGES.filter` rather than a second hardcoded id list, and that
the chevron/middot placement matches the ruling's separator rule.

## 3 · Investigation header — the visibility half, not the control half

The ruling's §3 says Investigation "additionally becomes a scope control in
the header row, showing the active investigation and whether it is ad hoc
or bound to an Egeria Project. That binding deserves permanent visibility."

**Built:** a new `#investigation-scope` badge next to the existing
`#investigation-name` header span, reading `· ad hoc` or `· bound to Egeria
Project` off the current investigation's `egeria_binding` field
(`ProjectRegistry.BINDING_LOCAL`/`BINDING_EGERIA`, `registry.py:6407`), with
the Egeria-side qualified name as a hover title when present. This is the
"permanent visibility" half of the sentence, and it was genuinely absent
before — the header showed the investigation's name only, never its binding.

**One correction made during live verification, not in the original
diff:** my first pass gated the "bound" label on `egeria_binding === 'egeria'
&& egeria_project_guid` (non-empty). Live-testing against a real
investigation (`docling-family`) showed `egeria_binding: "egeria"` with
`egeria_project_guid: ""` — and `registry.py`'s own comment on that column
says `BINDING_EGERIA` means "has one, or is meant to," specifically because
"a promotion that has not run yet and one that will never run look identical
from a null GUID, and only [`egeria_binding`] records intent." Gating on the
GUID would have mislabeled every not-yet-linked Egeria-bound investigation as
"ad hoc." Fixed to key off `egeria_binding` alone.

**Deferred, and why:** turning this into an actual *interactive* scope
control — one that lets you switch or (re)bind an investigation from the
header itself — is not built here. The ruling's own wording ("becomes a
scope control... showing...") reads as aspirational for the badge described,
and the interactive half already exists elsewhere: the sidebar's
`#investigation-select` switches the current investigation, and the New
Investigation form carries the actual three-mode binding flow (bind
existing / create new / stay local) `investigations.py`'s own module
docstring describes. Building a *second*, header-level interactive control
for the same two actions would be new surface area this ruling doesn't
specify the shape of (a dropdown? a modal? does it duplicate or replace the
sidebar control?), and the task brief for this round is nav grouping/chrome,
not a redesign of investigation switching. If the project owner wants the
header control itself to become interactive, that's a follow-on design
round with its own spec, not a judgment call to make silently here.

## 4 · The per-tab state dot — deferred in full, and why

RULING-NAV-GROUPING.md §4 discusses a proposed three-state
(measured/partial/never-run) dot for "the funnel tabs" (the run), with two
constraints if built: it must not collapse never-run into ran-and-found-
nothing (matching the charts pane's three-way distinction — measured
figure / "nothing recorded yet" / failed call, per `app.js`'s `rowState()`
and the `MEASURED`/`NOTHING_FOUND`/`NEVER_RUN` constants), and it stays off
Understanding/Automate now that Understanding is heading toward a per-user
configured surface rather than a per-corpus one.

**Nothing here was built.** I searched the whole `/next` frontend
(`app.js` and `stages/*.js`) for any existing per-tab dot, badge, or status
indicator on the intent nav and found none — no `state-dot`, no glyph
keyed off a stage's aggregate run status anywhere near `renderIntentNav()`.
The ruling's own language — "was proposed," "endorsed, with two
constraints" — confirms this is a critique's proposal being *ruled on*, not
an existing feature this task was asked to regroup. Building a new per-tab
status indicator now would be feature-completeness work on top of nav
chrome, which the task brief explicitly rules out ("this is a
navigation/grouping change, not a feature-completeness change"). The two
constraints are recorded above so whoever builds the dot doesn't have to
re-derive them from the ruling doc.

`tests/test_next_nav_grouping.py::TestDeferredStateDotIsNotBuiltHere` pins
this as a regression guard — if a future change silently adds this dot to
`renderIntentNav()`, that test names the two constraints it must satisfy at
the point someone next touches this function.

## 5 · Verification

- **Full test suite:** `uv run pytest tests/ -q` from
  `packages/resource-explorer/`, run to completion three times while
  fixing an unrelated infra hiccup (the shared Postgres/Docker stack was
  briefly down mid-session — confirmed via `docker ps`/`nc -z localhost
  5442` failing, unrelated to this change, and recovered by restarting
  Docker Desktop). The clean, uncontended run: **5050 passed, 103 skipped,
  0 failed**, in 510s. Four pre-existing tests needed updates because they
  asserted the exact old source text this ruling intentionally changes
  (`frame: true`, the old `renderIntentNav` body, the `curate` STAGES
  literal) — `tests/test_next_automate_pane.py`,
  `tests/test_next_curate_selection_and_blueprints.py` (2 tests), and
  `tests/test_next_discovery_assessment_analysis_stages.py`; all four now
  assert the equivalent invariant against the new `class`-based source.
  New coverage: `tests/test_next_nav_grouping.py` (16 tests).
- **`node --check --input-type=module`** on `app.js` — passes (it's an ES
  module; plain `node --check` fails on the `import` statement regardless of
  correctness, so this repo's check needs the `--input-type=module` flag).
- **`tailwind-next.css` staleness:** the two new classes introduced
  (`px-1` for the separator spans; `text-chrome-muted` was already compiled)
  — `px-1` was **not** in the compiled CSS. Ran `npm install` (no
  `node_modules` in this worktree) then `npm run build:css:next` from
  `frontend-build/`; the rebuilt `tailwind-next.css` now has `.px-1{...}`.
  Committed the regenerated file.
- **Live verification:** started a throwaway dev server on port 8824 (added
  a `resource-explorer-wt-nav-grouping` entry to `~/.claude/launch.json`,
  the file the Browser tool actually reads — appended, did not touch any
  existing entry; the worktree's own `packages/resource-explorer/.claude/
  launch.json` edit was reverted since it wasn't the file consulted), using
  `TRELLIS_ANONYMOUS_READ=true` and "Continue without signing in" — no
  password was entered anywhere. Confirmed via the live DOM
  (`document.getElementById('intent-nav').innerHTML`) that the rendered
  markup is exactly `Investigation, 1 Scouting › 2 Discovery › 3 Assessment
  › 4 Analysis › 5 Enrichment › 6 Curate · Understanding · Automate · ▦ Work
  lists`, and confirmed the Investigation header badge reads "ad hoc" or
  "bound to Egeria Project" correctly by switching to a real investigation
  (`docling-family`) via the sidebar's investigation selector. Stopped the
  server afterward.
  **Limits:** no automated browser/jsdom harness in this repo for `/next`
  (the established pattern here is source-text assertions plus manual
  verification, per the other `*-IMPLEMENTED.md` docs) — the live check
  above was manual, once, against one investigation with `egeria_binding:
  "egeria"`; no investigation with `egeria_binding: "local"` existed in
  this dataset to visually confirm the "ad hoc" label live, though the same
  code path was exercised in the no-investigation-selected state (badge
  correctly renders empty, not "ad hoc," when nothing is selected).
