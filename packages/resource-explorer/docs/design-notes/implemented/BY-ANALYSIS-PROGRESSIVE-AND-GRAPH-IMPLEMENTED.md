# Implemented: By-analysis panel rebuild — progressive render + Relationship Graph in the card body

**Replies to:** `BRIEF-BY-ANALYSIS-PANEL-USABILITY.md` (including its 2026-09-28
"Progressive render" addendum) and `REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md`
§2. **Branch:** `re/by-analysis-progressive-and-graph`, off `origin/main` at
`100e131a` (includes `re/relationship-graph-rendering`, merged as #339).
**Date:** 2026-09-28.

Two problems, both live-confirmed by the project owner on `laz_local_adventureworks`:

1. `loadByAnalysisPane` showed only "Reading the dashboards…" for up to 109s
   (the code's own comment named the cost and did nothing about it).
2. The Relationship Graph card showed only numeric counts, never the graph
   `factGraphviz` already knows how to render (wired into the Questions-tab
   Evidence rail only).

## What changed

### 1. Contents board renders immediately, before any dashboard read

`loadByAnalysisPane` (`app.js`) no longer makes one big `getSurveyDashboards`
call. It now:

- Builds the board list from `state.analyses` (already fetched at boot,
  `listAnalyses()`) for database/filesystem entity types — **zero network
  call** for the very first paint, since a board's id equals its
  `analysis_id` there. repo's dashboards group several analyses under one id
  `state.analyses` doesn't carry, so repo alone falls back to a new cheap
  endpoint (below).
- New endpoint `GET /{slug}/survey-results/boards` (`workflows.analysis.
  list_survey_result_boards`, wired into `projects.py`/`databases.py`/
  `filesystems.py`) returns `[{id, title, description, analysis_ids,
  stages}]` — **catalog metadata only, no results/headline reader is
  called** (a real test, `TestListSurveyResultBoardsIsCatalogOnly`, asserts
  the function's own source never calls `_read_analyses`/`results_reader`/
  `headline_reader(`).
- The contents board (glyph + headline placeholder + run-time placeholder,
  sticky, click-to-jump) and every card's skeleton paint from this list
  synchronously, before a single board's own data has been requested.

### 2. Each board's real data streams in independently, several at a time

`build_survey_results` (`workflows/analysis.py`) now takes an optional
`board_id` — every route that already exposed it (`GET .../survey-results`
on all three entity types) passes it through. `getSurveyDashboards(slug,
stage, { ..., boardId })` (`re-api.js`) lets the client fetch one board at a
time instead of the old full sweep.

`loadByAnalysisPane` fires these against a small work queue
(`BY_ANALYSIS_MAX_CONCURRENT_READS = 3`), not literally all at once — found
necessary **live, not theoretically**: an earlier version fired every board
at once via `Promise.all`, and switching stages quickly left the abandoned
stage's `db_derived`-backed board reads running server-side (their RESULT
was discarded via the existing `dashToken`/`live()` guard, but the
*request* kept running), which starved even the cheap boards-catalog call
for the *new* stage behind them in the thread pool. The bounded queue also
checks `live()` **before starting** each new read, so switching away stops
new reads from being queued at all.

Each board's card fills in — headline, grouped COUNTS, findings, diagram —
the moment its own read lands; a slow board no longer holds up a fast one.
A "still reading N of M" line is visible near the top of the contents board
while any read is outstanding (`renderByAnalysisContents`), and once
everything settles the never-run boards move to the end of the list
(`neverRunLast`), applied once so cards don't reshuffle mid-read.

### 2a. A real, pre-existing bug found and fixed along the way

Live thread dumps (`kill -USR1 <pid>`, this codebase's own SIGUSR1 handler)
during timing verification showed the **main/event-loop thread itself**
blocked inside a synchronous `registry.execute()` call, not a worker
thread — meaning the whole server (including this pane's own
`to_thread`-wrapped board reads) stalled behind it. The culprit:
`GET /{slug}/questions` on all three entity types (`databases.py`'s
`get_database_questions`, `filesystems.py`'s `get_filesystem_questions`,
`projects.py`'s `get_scouting_questions`) called `build_question_checklist`
**directly**, not wrapped in `asyncio.to_thread` the way the sibling
`/survey-results` routes already are — even though `question_has_data`
(`workflows/scouting.py`) reaches the exact same expensive `db_derived`-
backed readers. This pane's own background "order boards by question order"
call (`getQuestions()`, fired right after the first paint) was hitting this
route, so the bug directly affected this feature even though it long
predates it. Fixed by wrapping all three routes in `asyncio.to_thread`, same
pattern already used for `/survey-results`/`/schema-inventory-tree`. This is
a real behavior change outside the By-analysis pane's own files (repo's
Questions tab benefits too), called out explicitly since it wasn't asked
for by name — the coordinating session was told before pushing.

### 3. Headline-first cards, description collapsed

`byAnalysisCardHtml` renders the card's headline **first**
(`boardHeadlineHtml`, reading `a.headline.label` — the exact same
`headline_reader` function object the Questions tab's `FactLayer.
_headline_for` resolves for the same `analysis_id`, confirmed by an
`is`-identity test, not a behavioral guess — see Tests below), run through
the same `firstSentence()` one-sentence rule the Questions row uses. The
catalog description sits behind a closed-by-default `<details>` ("what it
does ▸"). The card boundary follows REPLY §2.1: a band, not a box — `mt-s6`
space, a `border-t-2 border-rule-strong` top rule, name-size heading — so a
page of collapsed cards reads as a clean list rather than a grid of nested
hairlines.

### 4. COUNTS grouped: Result / Coverage / Diagnostics

`countGroupFor(key)` classifies each scalar/boolean field by name (Coverage:
table/schema/column/measured/estimated/unmeasured/captured/readable;
Result: edge/component/isolated/determined/grain/confidence/overall/`*_count`;
everything else Diagnostics), rendered as up to three small-caps-labelled
tables instead of one flat list.

### 5. Shared names, once — and `preliminary_fit`'s 0 fixed

`collectMeasures` walks every SETTLED board's numeric fields once, across
the whole pane, and `sharedNamesHtml` renders each disputed name in a single
block after the contents board (not per-card — each card's own COUNTS row
keeps a small `≠` mark pointing back at it instead of repeating the
sentence). Per REPLY §2.3, `preliminary_fit`'s `confidence: 0` (no lens
declared, not a real measurement) is excluded from the *comparison* that
decides whether a name disagrees, and rendered as `— (no lens declared)`
wherever it's shown, never a bare misleading `0`.

### 6. Cards in question order

`orderBoardsByQuestions` reads the stage's own question list (fetched in
the background, `getQuestions()`, after the first paint — ordering is a
nicety, nothing above blocks on it) and orders boards by the first question
that names them, using `leadAnalysisId`/`entry.note` the same way the
Questions row does (not a bare `analysis_ids[0]`). A board no question names
keeps its catalog position, appended after every named board.

### 7. Relationship Graph card body renders the actual graph

`boardDiagramHtml(board)` wraps a board's own `a.results` in the
`{facts: [{value}]}` shape `factGraphviz`/`factMermaid` (envelope.js) already
read from a Questions-tab envelope — no backend change and no new reader
were needed, since the same `results_reader` backs both a Questions
envelope's fact and a board's `a.results` (confirmed via `test_by_analysis_
headline_matches_questions.py`'s sibling assertion). **Not hardcoded to
`db_relationship_graph`**: the first board whose results carry a `graphviz`
field gets the three-zoom-level treatment (schema map / whole database or
its own named fallback / per-schema select); the first board whose results
carry a `mermaid` field instead gets an inline "open diagram in pane" mount
using the same shared renderer.

`renderDiagramInto(container, turn, uid)` is a **new, shared** function —
the Kroki fetch/SVG-inject/pan-zoom/extreme-aspect-framing logic that used
to live only inside `promoteToPane`'s diagram branch is now one function
both `promoteToPane` (the Evidence rail's "open diagram in pane") and the
By-analysis card body call. This is the literal answer to "not a
reimplementation" — a static-source test (`test_render_diagram_into_is_
declared_exactly_once`) pins that there is exactly one declaration.

### 7a. Per-schema select styling — coordinated, not duplicated

`re/relationship-graph-dropdown-affordance` (commit `ecfb7eca`, pushed to
origin but not yet merged to `main` as of this branch) restyles the Evidence
rail's own per-schema `<select>` (dark-chrome ground: `appearance-none`,
`chevron-down` icon, `border-accent`/`text-accent-on-dark`/`hover:bg-
accent-tint`). Confirmed via `git merge-tree --write-tree` that this branch
merges cleanly against it (no conflict). The By-analysis card's own select
sits on **paper**, not dark chrome, so the literal classes don't transfer —
the same pattern does: `appearance-none` + absolutely-positioned
`chevron-down` + `border-accent`/`text-accent-ink`/`hover:bg-accent-tint`
(paper-ground equivalents already used elsewhere in `/next`, e.g. the work-
list row hover). Coordinated with the "Resource Explorer expansion
architecture" session before building this piece, per the dispatch's
requirement.

## Backend changes

- `workflows/analysis.py`: new `list_survey_result_boards(registry,
  entity_type, slug, stage)`; `build_survey_results(...)` gained an optional
  `board_id` filter parameter.
- `web/routes/projects.py` / `databases.py` / `filesystems.py`: new
  `GET /{slug}/survey-results/boards` route on all three; existing
  `GET /{slug}/survey-results` gained `board_id`; **all three `/{slug}/
  questions` routes now run `build_question_checklist` via `asyncio.
  to_thread`** (§2a above — a real bug fix, not new scope, called out
  separately).
- `web/static/re-api.js`: `getSurveyDashboards` gained `boardId`; new
  `listSurveyResultBoards`.

## Tests

- `tests/test_next_by_analysis_progressive_and_graph.py` — static-source
  assertions (the pattern `test_next_schema_inventory_filter.py`/
  `test_next_db_server_discovery.py` established, no browser harness with a
  signed-in session): contents board paints before the progressive fetch
  starts; the initial catalog read never calls the slow `getSurveyDashboards`
  path; boards fetch several at a time through a bounded, `live()`-checked
  queue; the "still reading N of M" line exists; the contents board is
  sticky with click-to-jump; the Shared Names block renders exactly once,
  with `preliminary_fit`'s 0 excluded from the comparison and rendered as
  "— (no lens declared)"; card headline renders before the collapsed
  description `<details>`; COUNTS are grouped into three named tables; cards
  order by `leadAnalysisId`/question order with never-run boards moved to
  the end only after everything settles; the graphviz card body calls
  `factGraphviz`/`factMermaid` over the board's own results (not a
  reimplementation) and mounts through the one shared `renderDiagramInto`
  the Evidence rail also uses; the pattern generalizes beyond
  `db_relationship_graph` by name.
- `tests/test_by_analysis_headline_matches_questions.py` — the brief's own
  backend ask: an `is`-identity check that `DATABASE_ANALYSIS_HEADLINE_MAP`/
  `REPO_ANALYSIS_HEADLINE_MAP`'s reader for each `analysis_id` is the exact
  same function object `analysis_kinds[analysis_id].results.headline_reader`
  resolves — the function `FactLayer._headline_for` (Questions tab) calls —
  not two independently-written functions that happen to agree today.
  (filesystem is excluded — it does not register a `analysis_kinds=lambda:
  ...` FactLayer provider the same way, so there is no equivalent claim to
  pin.) Plus: `list_survey_result_boards` never touches a results/headline
  reader, and its board ids match `build_survey_results`' own synthesized
  set; `build_survey_results` genuinely has a `board_id` parameter.
- `tests/test_next_analysis_subresources.py` — one pre-existing test updated
  (not weakened): the generic per-board findings loop moved from
  `loadByAnalysisPane`'s own body into `boardFindingsHtml(board)`; the
  assertion now points at that function, same behavior pinned.

**Full suite:** `uv run pytest tests/ -q -rf` — **6776 passed, 103 skipped, 0 failed**
(785.27s / 13m05s). Run twice: the first pass (kicked off before the
`asyncio.to_thread` questions-route fix and the bounded-concurrency fix
landed) caught 5 failures — 4 were this branch's own new tests, stale
against an in-flight rewrite (re-ran green once the file settled), and one
was a genuine pre-existing test (`test_next_analysis_subresources.py`)
that needed updating because a code block it string-matched moved into a
new helper function during the refactor — fixed by pointing the assertion
at the new function, same behavior pinned, not weakened (see Tests below).
The second, final run above is clean.

## Live verification

Server run from this worktree (`git worktree add
.../trellis-re-by-analysis-rebuild`), port **8815** (a scratch port, not
8810/8813), `EGERIA_PLATFORM_URL=https://localhost:9443` (this Mac's local
Egeria, reachable), signed in as `erinoverview` per the task's demo
credentials; `resource-explorer logout` run afterward.

**laz_local_adventureworks**, Discovery stage, By-analysis sub-tab:

- **Contents board visible before any dashboard read, repeatedly confirmed
  live**: clicking "By analysis" and reading the page immediately after
  (well under the tool round-trip's own ~1s) showed the full contents
  board — 7 rows (Database Classification, Relationship Graph, Table Grain,
  Schema Fingerprint, Subject Signals, Coverage Signals, Preliminary Fit,
  in question order), each with a loading glyph and "reading…" placeholder,
  plus every card's skeleton (headline placeholder + "what it does ▸") —
  with **zero board reads yet resolved**. This is the literal fix for the
  109s-blank-wait problem and the gate's "contents board visible within
  ~2 seconds" requirement; verified on a cold page load and again after a
  server restart, both times the same. Network log confirms the sequence:
  `GET .../survey-results/boards` (catalog-only) resolves first, THEN the
  per-board `GET .../survey-results?board_id=...` calls fire.
- **Last card filled**: measured twice (once mid-session, once again after
  a hard reload to rule out a stale cached module — see caveat below), on
  Discovery (Database Classification, Relationship Graph, Table Grain,
  Schema Fingerprint, Subject Signals, Coverage Signals, Preliminary Fit —
  7 boards). Second, clean-module run: 3 of 7 settled by ~40s, 6 of 7 by
  ~50–60s, the 7th (Preliminary Fit, which itself calls the same
  `db_derived` machinery a second time) trailing a bit further. **This ran
  concurrently with a full `pytest tests/` suite from this same session, and
  a SEPARATE session's own full `pytest tests/` run, both hitting the same
  shared Postgres (`localhost:5442`) at the same time** — real, uncontrolled
  contention. Against the documented 109s single-sweep baseline (for a
  different, larger board set), this is a plausible, not dramatic,
  improvement in raw completion time under load — the actual fix is that
  the pane is never blank while it happens: the contents board and every
  card's headline/COUNTS/graph fill in incrementally and remain fully
  interactive throughout, rather than one 60–90s wait with nothing shown.
- **Relationship Graph card body**: confirmed rendering the real graph, not
  just numbers — `edge count 91 · component count 2 · largest component 67`
  (headline: "91 foreign keys connect 67 of 68 key-captured tables into 2
  component(s), the largest holding 67" — matches the design brief's own
  worked example almost exactly), with a real 1067×314 SVG mounted inline
  (verified via `mount.querySelector('svg')` and the rendered "at full
  size" note text), the Schema map/Whole database buttons, and the
  per-schema select (5 schemas, each with its own table/key count).
- **Shared names, once**: `SHARED NAMES · 1 DISAGREE` — `confidence ·
  db_classification 67 · subject_signals 60` — rendered exactly once, after
  the contents board, not per-card.

**localhost_docker_coco_pharma**, Discovery stage, By-analysis sub-tab —
same pattern, same-session verification:

- Contents board visible immediately, 0 of 7 boards resolved at first read,
  same as above.
- Full settle: 3 of 7 by ~40s, 6 of 7 by ~70s, 7 of 7 (including Preliminary
  Fit) by ~80s — under the same concurrent test-suite load noted above.
- Relationship Graph card body: real SVG confirmed (`hasSvg: true`,
  11,311-byte mount, "Generated by graphviz version 14.1.3"), headline "13
  foreign keys connect only 15 of 53 key-captured tables; 38 stand alone",
  same three-zoom-level affordances, 7 schemas each with their own
  table/key counts.
- **Shared Names block, twice confirmed as coco_pharma's data settled**:
  first `SHARED NAMES · 2 DISAGREE` (`confidence · db_classification 60 ·
  subject_signals 25` and `table count · db_relationship_graph 53 ·
  grain_determination 58 · db_fingerprint 58`); once Preliminary Fit itself
  landed, the confidence row updated in place to `confidence ·
  db_classification 60 · subject_signals 25 · preliminary_fit — (no lens
  declared)` — **the exact rendering the coordinating session's requirement
  4 specified**, confirmed live, not just by the static-source tests.
- coco_pharma's Preliminary Fit board itself rendered ⚠ (needs-a-person
  family), not a false ✓, with headline "NO REQUIREMENT DECLARED — no lens
  was supplied, so fit is not a question that has an answer here" — task 4
  of the gate's "not-established cards must read as such" is satisfied for
  this row; the broader claim (every not-established card reading right,
  and the tab not looking emptier than Questions for the same stage) was
  not separately screenshotted.

## What I could not fully verify

- **A clean, contention-free "last card filled" number.** Both databases'
  full-settle timings above were measured while this session's own full
  test suite (`uv run pytest tests/ -q -rf`, ~17 minutes) AND a separate
  concurrent session's full test suite were both running against the same
  shared Postgres. A live thread dump during the wait confirmed genuine,
  progressing (not deadlocked) SQL execution in the worker threads
  throughout, so the slowness was real contention, not a hang — but the
  numbers above are not a clean baseline measurement. Reported as measured,
  with the confound named, rather than as a clean number it wasn't.
- Gate task 2 (jump-to-Table-Grain from the contents board, read its
  headline, open/close "what it does", return to top with no scrolling past
  another card's prose) was exercised through the static-source tests
  (`data-jump-board` click handler, description inside a closed `<details>`)
  but not re-walked as a live click sequence with a screenshot.
- No screenshots were captured for this report — the Browser pane was
  hidden on the host side for this session throughout verification
  (`tabs_context` reported "the Browser pane is currently hidden"), so
  `computer{action:"screenshot"}` timed out every time it was tried. All
  verification above was done via `get_page_text` (full rendered text,
  confirms every number and label actually reached the DOM) and targeted
  `javascript_exec` DOM checks (confirms real `<svg>` presence, not just
  the text saying so). I could not produce the screenshots the dispatch
  asked go into this doc; flagging that explicitly rather than omitting the
  numbers.
- One methodology note, not a functional gap: the very first laz_local_
  adventureworks measurement was taken on a browser tab that had been open
  since before the last two server restarts, so its in-memory ES module was
  stale and its Relationship Graph card showed no SVG (`hasSvg: false`).
  Re-verified on a freshly-loaded tab (same URL, hard reload) immediately
  after and the graph rendered correctly — recorded above. Flagging the
  false negative and its cause rather than silently dropping it.
- Gate task 2 (jump-to-Table-Grain, open/close "what it does", return to
  top with no scrolling past another card's prose) and task 3 (shared-names
  screenshot) were not captured as screenshots in this pass — the contents
  board's click-to-jump and the description `<details>` are exercised by
  the static-source tests above but not re-verified with a live click
  sequence and screenshot. Flagging this rather than claiming it was done.
- coco_pharma's not-established (◐/?) rendering in the contents board
  specifically (gate task 4) was not separately screenshotted.

## Coordination

- Messaged "Resource Explorer expansion architecture" before starting, with
  the exact files/regions to be touched, and again before building the
  graph-card-body piece to confirm `re/relationship-graph-dropdown-
  affordance`'s status. That session confirmed: build now, `git merge-tree
  --write-tree` against the branch (clean, confirmed above) is the
  condition for pushing, not waiting for it to land on `main` first.
- Not opening a PR — per dispatch, the "Resource-explorer PR/CI merge"
  session batches PRs. Reporting the pushed tip there, noting explicitly:
  "merges after `re/relationship-graph-dropdown-affordance`".

## Follow-up fix round (2026-09-28, PR #344 held pending this): four bugs found on the owner's timed gate

Four real bugs found on `laz_local_adventureworks`/`localhost_docker_coco_pharma`
after the slice above shipped, all fixed on this same branch before the PR
was allowed to proceed.

### 1. Contents-row headline came only from the board's own slow read

The contents board painted immediately (the slice above's own fix), but each
row's HEADLINE stayed `entry.status === 'done' ? boardHeadlineText(...) :
''` — blank while a board's read (10-26s each, several in parallel) was
still outstanding. Live evidence: the owner's screenshot at ~60s showed
every row, including Relationship Graph, still "reading…" with no headline.

**What was actually available, and what wasn't.** The obvious fix reads as
"use the data `getQuestions()` already resolves" — but `getQuestions()`
(`build_question_checklist`) does **not** carry a resolved headline sentence
per question; its `has_data` field is a boolean, and the headline readers
(`DATABASE_ANALYSIS_HEADLINE_MAP` etc.) are each their OWN independent
registry read, not derived from data `question_has_data` already fetched.
Measured directly (see "getQuestions() timing" below): `build_question_
checklist` itself takes 48-52s on these two databases, so treating it as a
cheap, ready-made headline source would have been wrong on its own terms.

**What this fix actually does**: `envelopeHeadlineText(question, answers)`
(app.js) reads `state.answers` — the Questions tab's own per-question
envelope cache, populated by `getAnswer()`/`loadAnswer()` when the Questions
tab has been visited for this slug/stage, in this browser session. This
makes NO new network call. `boardQuestionMap(boards, questions)` matches
each board to its question with the exact same `leadAnalysisId`-then-
`analysis_ids` preference `orderBoardsByQuestions` already used (pulled out
so both share one matching rule). The contents row now reads: `entry.status
=== 'done' ? boardHeadlineText(entry.board) : envelopeHeadlineText(
boardQuestions.get(b.id), state.answers)`.

**Honest limit, stated plainly**: this only has something to show when the
Questions tab was already visited for this slug/stage in the same session —
a cold load that goes straight to By-analysis with no prior Questions visit
still shows a blank headline while boards load, same as before this fix.
That is the accurate reading of "the row is never blank while loading IF the
envelope already has an answer" — not a claim that every row is now
populated on every load. `leadAnalysisId`/`firstSentence` (envelope.js) are
reused, not reimplemented, per the dispatch's own instruction.

Reuses `firstSentence`'s one-sentence truncation exactly the same way
`boardHeadlineText` does, so a multi-sentence envelope answer still shows
only its first sentence in this row.

### 2. Shared Names block: "N DISAGREE" was not interpretable

Owner's live coco_pharma feedback: "I don't know what '1 DISAGREE' means."
`collectMeasures` now returns two sets instead of one: `shared` (every name
carried by more than one analysis — the block's actual inclusion criterion)
and `disagreeing` (the narrower subset whose comparable values genuinely
differ — still the right, and only, set for the per-card `≠` mark, whose own
title text says "reported with different values elsewhere"). The header now
reads "Shared names · N names carried by more than one analysis", and each
row states in words whether its values differ ("different measures share a
name -- not necessarily wrong, likely worth a rename") or agree ("these
measures share a name and agree"). No "DISAGREE" string appears anywhere in
the block any more.

### 3. Preliminary Fit's card COUNTS table disagreed with the Shared Names block

The Shared Names block correctly rendered `preliminary_fit`'s no-lens
`confidence: 0` as "— (no lens declared)" (`measureDisplay`). The
Preliminary Fit CARD's own COUNTS table (`boardCountsHtml`) computed its
display text separately with a bare `fmtScalar`, so the identical value
showed as a plain "0" right next to the block that said otherwise — two
renderings of one number, disagreeing with each other on the same screen.
Fixed by tracking `noLens` per COUNTS row the same way `collectMeasures`
does, and rendering through the same `measureDisplay` helper both places
now share.

### 4. Not-established boards never showed the not-established glyph

`boardStateKey` recognized `needs-lens` (preliminary_fit's own marker) and
`unrun`/`measured`, but never `result_status.py`'s `not_established` state —
a board that measured something but could not settle a result (`has_
results: true`, an analysis's `results.state === 'not_established'`) fell
straight through to the plain `measured` case and showed a ✓, the exact
"confident wrong answer" shape gate task 4 exists to catch. Fixed: `board
StateKey` now checks `(board.analyses || []).some((a) => a.results &&
a.results.state === 'not_established')` before the `unrun`/`measured`
fallthrough, returning glyphs.js's own `not_established` state (`?`).

**Not verifiable live**: neither gate database has a not-established
analysis today, so this is verified with a harness fixture instead of a
live screenshot — design's own call for this fix round.

## getQuestions() timing — a separate, NOT-yet-fixed problem, flagged not fixed

Since fix #1 above makes the row headline depend on `state.answers`, which
in turn depends on the Questions tab's `getAnswer()` calls (and ordering
depends on `getQuestions()` itself), `getQuestions()`'s own speed matters
for "readable within 10s" in a way it didn't before. Measured directly
against the server-side function (`build_question_checklist`, `workflows/
scouting.py`) — not through the HTTP route, so this is the number with
network/ASGI overhead subtracted, i.e. a floor, not a ceiling:

```
laz_local_adventureworks database/discovery:      48.16s, 22 questions
localhost_docker_coco_pharma database/discovery:  52.07s, 22 questions
```

**This is slow — 48-52s for a single checklist call — and confirms PR/CI's
"observed at 30s+ under load" note.** Root cause: `question_has_data`
(`workflows/scouting.py`) calls a `results_reader(registry, slug)` for every
`analysis_id` on every question with `kind in (analysis, partial, mixed)` —
the same `db_derived`-backed, recompute-from-stored-rows-on-every-call cost
the board reads themselves pay, just paid once per (question × analysis_id)
pair instead of once per board. The `asyncio.to_thread` fix this branch
already shipped (§2a above) keeps this off the event loop so it no longer
blocks OTHER requests while it runs, but does nothing about its own
wall-clock cost.

**This is flagged, not fixed, per the dispatch's explicit instruction**
("do not silently try to fix it too... scope creep here risks missing the
actual ask"). A real fix would need either caching `question_has_data`'s
per-analysis result across the checklist's own questions (several questions
share the same `analysis_ids`, so the current loop very likely re-reads the
same analysis's results multiple times per call — unconfirmed, not measured
separately in this pass) or a cheaper existence check than a full
`results_reader` call. Left for a follow-up.

## Tests, this round

- `frontend-build/test-harness/by-analysis-headline-and-glyphs.test.mjs` —
  real jsdom/DOM tests (PR #346's harness, merged to `main` during this
  round — merged into this branch before writing these): the contents row
  shows the envelope headline while the board's own fetch is stubbed to
  never resolve (status stays `'loading'`); the row stays genuinely blank
  (not a fabricated line) when no envelope has resolved; a `not_established`
  board fixture renders the `?` glyph and names the reason in its headline.
- `frontend-build/test-harness/by-analysis-shared-names.test.mjs` — real DOM
  tests: the Shared Names header never says "DISAGREE" and names the actual
  criterion; a name reported by only one analysis is excluded from the
  block; the Preliminary Fit card's own COUNTS table renders the identical
  "— (no lens declared)" text the Shared Names block uses for the same
  value (not a bare "0").
- `tests/test_next_by_analysis_progressive_and_graph.py` — new classes
  `TestRowHeadlineFallsBackToTheQuestionsEnvelope`,
  `TestNotEstablishedBoardsReadAsSuch`, plus new/updated cases in
  `TestSharedNamesRenderedOnce` — static-source assertions matching the
  DOM-level proof above, same pattern this file already used.

**Full suite**: `uv run pytest tests/ -q -rf` and `npm run test:harness`
(Node 20 via `nvm use 20`) both run clean — see this branch's push report
for the exact counts.

## Coordination, this round

- Design (via the coordinating session) added items 2, 3 and 4 above mid-task,
  each confirmed as a genuine bug found on the owner's own live gate check,
  not a scope guess — folded into this same fix round on the same branch/PR
  rather than opened separately, per the coordinator's explicit instruction.
- PR #346 (`re/next-render-harness`) was not yet merged to `main` when this
  round started; confirmed merged (`a90ce17c`) partway through, and `origin/
  main` was merged into this branch before writing the harness tests above.
