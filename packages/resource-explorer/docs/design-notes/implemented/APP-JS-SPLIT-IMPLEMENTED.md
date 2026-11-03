# app.js split into per-stage modules — implemented

PLAN-FINISH-REPOS.md, Part 2 §1 ("Split `app.js` so streams can run in
parallel"), on top of Part 2 §0 (the unbuilt-stages honesty fix, already on
this branch as `5cd4e48f`). Scope: a refactor only — no behavior change, no
bug fixes, no feature work. Where I noticed something that looked broken, I
left it alone and noted it below rather than touching it.

`app.js` went from 6,861 lines to 6,022. Nine new files exist under
`next/stages/`, one per canonical stage id in `app.js`'s own `STAGES` array
(`investigation, scouting, discovery, assessment, analysis, enrichment,
understanding, curate, automate`), plus a tenth (`activity.js`) matching the
task's own example list, for the reasons given below.

## What moved out, file by file

**`next/stages/enrichment.js`** (274 lines) — the whole Enrichment pane:
`JUDGEMENTS`, `OBSERVATIONS`, `ENRICHMENT_EVIDENCE`, `evidenceSnapshot`,
`movedSince`, `fieldControlHtml`, `fieldRowHtml`, `ownerNoteHtml`,
`proposedFrom`, and the two exported entry points `renderEnrichment` /
`renderEnrichmentEvidence`. `renderEnrichment` is what app.js's generic
Questions-checklist engine calls when `state.stage === 'enrichment'`.
Imports `ago`/`whenMs` from `format.js`, `getBulkFacts`/`saveEnrichmentField`
from `re-api.js`, and `state`/`esc`/`$`/`tnum`/`factGlyph`/
`ensureRailShowing`/`railClaim` from `app.js`.

**`next/stages/curate.js`** (532 lines) — the whole Curate pane, including
the component-tree/branch/leaf review that lives inside it: `CURATE_COLUMNS`,
`curateRowHtml`, `curateWritesHtml`, `curateRecordHtml`, `renderCurate`,
`renderCatalogueDepthOffer`, `verdictBadge`, `portsWords`, `openPortsInRail`,
`branchRowHtml`, `leafRowHtml`, `renderComponentTree`, `renderComponentDiagram`,
`recordVerdicts`. Only `renderCurate` is exported — it is the sole function
app.js's generic engine calls directly (`state.stage === 'curate'`);
everything else here is reached only from within this module. Imports `ago`
from `format.js`; `openDialog`/`closeCellDetail` from `worklist.js`; the
curate/component/verdict API functions from `re-api.js`; and
`state`/`esc`/`$`/`icon`/`tnum`/`factGlyph`/`ensureRailShowing`/`railClaim`/
`railFrame`/`openMembers`/`fmtSeconds`/`tokens`/`mermaidForKroki`/
`themeSvgElement` from `app.js`.

**`next/stages/understanding.js`** (131 lines) — `loadChartsPane`, the
entire Understanding pane, exported and called directly by `loadPane()`
before the generic engine runs (Understanding never reaches it — see
app.js's own comment there). Imports `REPO_CHARTS`/`getChart` from
`re-api.js` and `state`/`esc`/`$`/`paneMessage`/`bindSubTabs`/
`resourceHeaderHtml`/`bindResourceHeader`/`asFigure`/`allPointDates`/
`chartIsStale`/`drawChart` from `app.js`.

**Six stub modules** (`investigation.js`, `scouting.js`, `discovery.js`,
`assessment.js`, `analysis.js`, `automate.js`) — one per remaining canonical
stage id. Each is a short comment plus `export {};`, because there is
currently **no** stage-specific rendering code for any of these six anywhere
in app.js: `STAGES` marks none of them `built`, so `loadPane()`'s dispatch
renders each as the shared "not in /next" placeholder and never reaches
anything unique to that stage. Scouting is the one exception worth spelling
out: it IS built and live, but its pane is the generic, stage-parameterised
Questions-checklist engine — the same code path Discovery/Assessment/
Analysis are meant to use once built — so there is no Scouting-*specific*
code to move either; that engine is shared infrastructure and stays in
app.js (see "What stayed" below). Each stub file says exactly what a future
session needs to touch to build that stage.

**`next/stages/activity.js`** (a tenth file, not one of the nine canonical
stage ids) — created because the task's own instructions named it as an
example alongside enrichment/understanding/curate/automate. It documents
that Activity is *not* in `STAGES` (per `CLAUDE.md`, the 📋 Activity log is a
header-level surface like ⚙ Admin, decoupled from the eight-intent nav, not
a ninth stage) and that there is no Activity-specific /next rendering code
anywhere in app.js today to move.

## What stayed in app.js, and why

**Routing, shared state, chrome** — as specified: `loadPane()` and its stage
dispatch, the `state` object, `STAGES`/`SUB_TABS`, the sidebar, top bar,
perspective row, work-list nav, rail machinery's frame (`railFrame`,
`ensureRailShowing`, `railClaim`), and all the DOM/formatting primitives
(`esc`, `tnum`, `icon`, `$`, `paneMessage`, `bindSubTabs`,
`resourceHeaderHtml`, `bindResourceHeader`, `subTabsHtml`, `factGlyph`,
`openMembers`, `fmtSeconds`).

**The generic Questions-checklist engine** (the large block inside
`loadPane()` that calls `getQuestions()`, builds `resourceHeaderHtml`, and
renders `rowShell`/`rowInner`/`bodyLines`/`provenanceLine`/`loadAnswer` per
row) — this is genuinely shared infrastructure, not any one stage's own
code. It is what Scouting renders through today, what Curate's and
Enrichment's stage-specific hooks (`if (state.stage === 'curate')
renderCurate(slug);` / `if (state.stage === 'enrichment')
renderEnrichment(slug);`) are called *from*, and what Discovery/Assessment/
Analysis are meant to render through once built (app.js's own comment: "ONE
pane, parameterised by stage — not eight panes"). Moving it into any single
stage's file would misattribute ownership of code every future built stage
needs; it stays in app.js as the shared substrate the per-stage modules plug
into.

**The chart-rendering machinery below the pane level** (`drawChart`,
`chartLayout`, `chartAxes`, `timeAxisData`, `tokens`, `asFigure`,
`allPointDates`, `chartIsStale`, `CHART_CAVEATS`) — kept in app.js and
exported, rather than moved into `understanding.js`, because `drawChart`/
`chartLayout`/`tokens` are also called by app.js's own `promoteToPane()` —
the chat "promote an answer into the pane" feature, which can promote a
chart from *any* stage's chat turn, not only Understanding's. Only the
actual per-stage entry point, `loadChartsPane`, is Understanding's alone and
moved out.

## Files created

- `next/stages/investigation.js`
- `next/stages/scouting.js`
- `next/stages/discovery.js`
- `next/stages/assessment.js`
- `next/stages/analysis.js`
- `next/stages/enrichment.js`
- `next/stages/understanding.js`
- `next/stages/curate.js`
- `next/stages/automate.js`
- `next/stages/activity.js`

## Files modified

- `next/app.js` — three function bodies removed (replaced with a one-line
  comment pointing at the new module) and three import lines added at the
  top; roughly two dozen existing top-level `function`/`const` declarations
  gained an `export` keyword so the new stage modules can import them; a
  handful of `re-api.js` imports that were only used by the moved code were
  dropped (`getBulkFacts`, `getCuratePlan`, `postBranchVerdicts`,
  `getCatalogueDepthOffer`, `postCatalogueDepthOfferOutcome`, `curateCommit`,
  `getCuration`, `getComponentTree`, `getComponentLeaves`,
  `saveEnrichmentField` — each now imported directly by the module that
  actually calls it). No other line changed: `STAGES`, `SUB_TABS`,
  `loadPane()`'s dispatch order, and every call site's arguments are
  byte-for-byte what they were before, other than the three relocated
  bodies.

## What I scoped out / could not verify

- **No browser testing was possible in this environment** — there are no
  Egeria login credentials available here, and `/next` requires signing in
  (Egeria is the identity provider). I verified every new and modified file
  parses as valid ES module JavaScript
  (`node --input-type=module --check < <file>`, run on `app.js` and all ten
  new `stages/*.js` files, all passing), and traced every moved function's
  call sites and every helper it references by grep, confirming each import
  list is complete and each removed `re-api.js` import is genuinely unused
  in app.js afterward. I did **not** load the page, click through a stage,
  or confirm the browser resolves the new `/static/next/stages/*.js` URLs
  at runtime — that needs a running, authenticated session this environment
  doesn't have.
- I noticed the Curate/Enrichment stage hooks in the generic engine
  (`if (state.stage === 'curate') renderCurate(slug);` and the equivalent
  for enrichment) are currently **unreachable dead code** under today's
  `STAGES` flags — neither stage is marked `built: true`, so `loadPane()`
  returns the "not in /next" placeholder before ever reaching them. That
  looks like exactly the kind of read-vs-write gap `DEFECT-UNBUILT-STAGES-RENDER-AS-BUILT.md` describes, but fixing it (flipping `built: true` for
  curate/enrichment) is a behavior change and Part 3 items 1 and 3 name
  Enrichment and Curate as separate, not-yet-done work items with their own
  done tests — so I moved the code as-is, dead branches and all, and did not
  flip the flag.
- I did not run any linter (`ruff`/`black` are Python tools; there is no JS
  lint config in this package I could find) — only the Node syntax check
  above.
- I did confirm, by reading `web/app.py`, that the new `next/stages/`
  subdirectory needs no server-side change: static assets are served by a
  single recursive `StaticFiles(directory=_STATIC)` mount at `/static`
  (`app.py:208-209`), not an allowlist of specific files, so
  `/static/next/stages/enrichment.js` resolves the same way
  `/static/next/worklist.js` already does. The only other logic touching
  `/static/next/...` paths is a cache-control middleware keyed on the path
  prefix, which the new files fall under identically — it doesn't gate what
  gets served.

## Addendum (same day): the split broke 15 Python tests, now fixed

Missed above: four test files locate app.js's JS functions by raw string
search against the file's text, and 15 tests across them broke when the
functions they searched for moved into `stages/*.js` — a `ValueError`
(marker not found at all) or a silently empty slice (a start/end marker
pair split across two files, so the concatenation put the end marker
*before* the start marker). Full pytest run would have caught this
immediately; I only ran the syntax check above and didn't think to run
the existing suite before calling the refactor done. Fixed in a follow-up
commit on this branch:

- `test_next_component_review.py`, `test_next_curate_pane.py`,
  `test_next_rail_states.py` — their `_app()` helper now concatenates
  `app.js` with every `stages/*.js` file, so a name search doesn't need
  to know which file a function ended up in.
- Several slices used `rowKey(i)` (stayed in app.js) as an end boundary
  for content now entirely inside `curate.js` — rebound to the actual
  next function in that file (`portsWords`/`recordVerdicts`/
  `curateWritesHtml`/`renderCatalogueDepthOffer`, picked per test by
  checking which function's body the asserted strings actually live in
  — two needed `renderCatalogueDepthOffer` specifically because the
  checked content spans from `renderCurate`/`curateRowHtml` through the
  click-handler wiring inside `renderCurate` itself).
- `test_next_enrichment_fidelity.py`'s `proposedFrom`/`renderEnrichment`
  slice now reads `stages/enrichment.js` directly; its
  `test_all_shell_modules_parse` discovers `stages/*.js` via glob instead
  of a hardcoded list, so a future new stage module gets syntax-checked
  without anyone remembering to add it here.

All 81 tests across the four affected files pass after this fix
(`uv run pytest tests/test_next_component_review.py
tests/test_next_depth_offer.py tests/test_next_rail_states.py
tests/test_next_enrichment_fidelity.py tests/test_frontend_render_modes.py
tests/test_next_journal_fidelity.py tests/test_next_run_choice.py
tests/test_next_curate_pane.py tests/test_next_records.py -q`). A full
`pytest tests/` run for the whole package was still in progress when this
was written; if it surfaces anything else, it'll be a separate note, not
a silent edit to this paragraph.

## The done test, traced for one stage

Picked **Enrichment**, since it already has real code (unlike the six stub
stages) but the pane's DOM anchors and the generic engine's plumbing are
still in app.js, so it exercises the actual seam.

To add or change something in Enrichment today, a session touches:

1. **`next/stages/enrichment.js`** — every line of Enrichment's own logic:
   the judgement/observation field definitions, the save handlers, the
   evidence rail. All of it lives here now.
2. **`next/app.js`**, exactly one line — `import { renderEnrichment } from
   '/static/next/stages/enrichment.js';` — already present; a session adding
   a *second* exported function from `enrichment.js` (say, a new helper the
   generic engine should call directly) would add its name to that same
   import line, not a new line elsewhere.

Nothing else in app.js needs to change: the DOM slot Enrichment renders into
(`#enrichment-form`, `#rail-evidence`) is created by the shared engine
before `renderEnrichment` is called, and the call site itself
(`if (state.stage === 'enrichment') renderEnrichment(slug);`) doesn't change
unless the *dispatch* changes, which is routing, not Enrichment's own
concern. A second session building, say, Discovery in the same window would
be creating `next/stages/discovery.js` from scratch and adding one new
`import` line of its own — no shared file, no overlapping hunk, matching the
plan's done test: "two sessions can build two different stages without
touching the same file except for one import line each."
