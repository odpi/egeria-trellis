# Implemented: Enrichment E1 — the Context tab

**Replies to:** `REPLY-DESIGNER-ENRICHMENT-STAGE-IA.md` §1 (the strip and
Context's five sections), §0.2 as amended by the project owner 2026-09-29
(analyses whose prerequisite is a human input run at Enrichment).
**Builds on:** `ENRICHMENT-E0-ROW-ANATOMY-IMPLEMENTED.md` — the shared row
anatomy (`row-anatomy.js`'s `personRowLineHtml`) and the inline
question-answer editor E0 built with no Context tab to live in yet.
**Read against:** `origin/re/enrichment-e0-row-anatomy` at `bbd2049b`; the
reply as committed on `origin/re/design-enrichment-ia` (`e25d0663`, not
merged to `main`, read via `git show` — same situation E0 hit).
**Scope:** E1 only. E2 (re-seating PR #348's Documentation Sources block
once #348 merges) is a separate, already-scoped slice, explicitly not
attempted here.

---

## The strip restructure

`Context · Questions · Survey & analyses · By analysis · Disposition`, plus
`Schema Inventory` for databases. Context is the new first tab.

`SUB_TABS` (`app.js`) gained a `stages` field, the same shape `resourceTypes`
already used to gate Schema Inventory to databases — `subTabsHtml()`'s filter
now checks both. Context is gated `stages: ['enrichment']`: it does not grey
out on other stages, it is absent from the strip entirely, since (unlike a
merely-unbuilt tab) there is no per-stage Context content to defer to off
Enrichment.

**Default tab.** The stage-nav click handler and the URL-restore path both
redirect the module-level default (`'questions'`) to `'context'` when landing
on Enrichment — narrowly, so a subTab a person deliberately chose on another
stage (Disposition, Survey & analyses, …) survives the switch unchanged, the
same as every other stage-to-stage navigation. `writeUrl()`'s own
`tab=`-omission rule became stage-aware for the same reason (Enrichment's
default is `context`, everyone else's stays `questions`).

**The old merged pane is gone.** Before E1, `state.stage === 'enrichment'`
unconditionally rendered the judgement/observation form (`renderEnrichment`,
writing to `#enrichment-form`) INSIDE the Questions tab, regardless of
sub-tab — there was only one sub-tab reaching it. That branch and the
`#enrichment-form` div are removed from the generic Questions-engine pane;
Questions under Enrichment now renders exactly like every other stage's
Questions tab (the full catalog checklist, all kinds, unchanged). The form
moved to Context (`stages/context.js`'s `renderContext`, via a new
`loadContextPane()` in `app.js`).

`renderEnrichment`/`renderEnrichmentForm` (`stages/enrichment.js`) still exist
and are unchanged in behaviour — nothing calls `renderEnrichment` from
`app.js` any more, but Context reuses its `JUDGEMENTS`/`OBSERVATIONS`
constants and `fieldRowHtml`, and a newly extracted
`wireEnrichmentFieldControls(host, slug, rerender)` (the save/confirm/
interim-owner click wiring, pulled out of `renderEnrichmentForm` so both
that function and Context's own render path share one implementation).

## Context's five sections, in the reply's own order

1. **What we judge** — `JUDGEMENTS` via `fieldRowHtml`, unchanged rendering,
   reused verbatim from `stages/enrichment.js`.
2. **What you're looking for** — the lens row (below).
3. **What we record** — `OBSERVATIONS` via `fieldRowHtml`, same reuse.
4. **What only you can answer** — the catalog's human questions, with E0's
   inline editor now given a real home (below).
5. **Where it's documented** — a labeled placeholder slot for PR #348's
   Documentation Sources block (below).

Every judgement/observation/human-question row carries a `feeds →` line.

### The "feeds →" derivation

For a catalog human question (section 4): `question.analysis_ids.join(', ')`
when the catalog names any, else the free-text `answering_mechanism`/`note`
(the CSV's "Answering Analysis" column) — the brief's own instruction, no new
YAML added.

For judgements/observations (sections 1, 3): these are **not**
`question_catalog.yaml` rows — they are `EnrichmentField` keys on
`context.py`'s fixed record, so there is no per-field "Answering Analysis"
metadata to derive from at all. That is the brief's own named escape hatch
("derive... rather than adding new YAML unless derivation proves genuinely
impossible for a given row"). What IS recorded, directly in
`enrichment.js`'s own header comment (unchanged since E0): *"Nothing here is
written to the catalogue until Curate."* That is a real, verifiable
downstream consumer, so every judgement/observation row reads
`feeds → Curate (catalogue record)`. `licence` additionally names its survey
source via the catalog's own already-existing `fromAnalysis` field
(`OBSERVATIONS`, E0-era), not a fabricated cross-reference:
`feeds → Curate (catalogue record) · sourced from license_classification`.

### The lens row (section 2)

**Finding, stated plainly:** no investigation-level lens-declaration
mechanism exists anywhere in this codebase. Searched directly (`grep
lens_declared|DataLens|lens_source` across the whole package, both Python and
JS): `db_derived.py`'s own header comment on `preliminary_fit` says a full,
versioned, investigation-level `DataLens` with a framing-form UI is
explicitly out of scope for that change (§16.5 points 3–5, "None of that is
built here"). `lens_source: ad_hoc` is only a label
`compute_preliminary_fit` stamps on a supplied-but-unlabeled lens dict — no
code path anywhere supplies one; there is no click affordance. Neither
`stages/investigation.js` nor `web/routes/investigations.py` mentions a lens
at all.

So the brief's own premise — "find the existing investigation-level lens
mechanism" and "the existing `lens_source: ad_hoc` mechanism (find and
reuse it)" — does not hold: neither exists as a built UI. This is the same
class of finding E0's own doc recorded under "What I could not find."

**What was built instead, honestly:** the row reads the ONE real, already-
stored signal that exists — `preliminary_fit`'s own last-read
`lens_declared` flag (a resource-scoped proxy for what should be
investigation-scoped; documented as a limitation in `lensRowHtml`'s own
comment) — and renders:

- `"no lens declared for <investigation> · declare it on the investigation
  ›"` when absent, and `"a lens is declared for <investigation>"` when
  present, plus
- a quiet `"try one on this resource only"` link.

**Both links are marked `not built in /next`**, the same dashed-underline,
title-tooltip convention every other deferred affordance in this codebase
uses (the sub-tab rail's own deferred buttons, `deferredAttrs`) — neither is
a real click target, because neither target exists. This is a judgment call
worth flagging for design/engineering review: building either link for real
(an investigation lens-declaration form, and an ad-hoc-lens-on-one-resource
control) is follow-on work this slice does not attempt.

### Section 4: the human-question editor's new home

E0 built the inline textarea+save editor (replacing `window.prompt()`) but
had no Context tab to put it in, so it opened on the question's own row
inside the Questions tab as an interim measure. That control's *shape*
(inline input+save, same as judgements/observations) is preserved; its
*location* is now Context, independent of the Questions tab's own
`rowShell`/`bodyLines`/`wireHumanAnswers` machinery. **Deliberately not
sharing that machinery**: those functions are i-indexed against the FULL
`state.questions` array and Questions' own `#question-rows` DOM ids
(`qrow-${i}`) — reusing them for a FILTERED subset in a second, independent
pane would need two live copies of the same array sharing index-keyed DOM
ids, which is fragile. Instead, `context.js` builds its own small renderer
(`humanQuestionRowHtml`/`wireHumanQuestionControls`) that reuses the actual
SHARED component the brief asks for — `personRowLineHtml`
(`row-anatomy.js`) — and the same `saveQuestionAnswer` API call, keyed by
question text rather than array position. The Questions tab's own rendering
of human-question rows is completely unchanged.

### Section 5: the doc-sources slot

**Placeholder only, confirmed not a duplicate build.** `docSourcesSlotHtml()`
in `context.js` renders a labeled, dashed-border section
("Where it's documented · sources, not answers · Documentation sources — not
built in /next yet on this branch") with a `TODO(ENRICHMENT-E2)` comment
naming the real target: PR #348's `renderDocSources()`
(`stages/enrichment.js` on `re/doc-sources-declare-and-probe`, confirmed by
reading that branch's own harness test doc comment — `doc-sources-
enrichment.test.mjs` — without reading deeper into its implementation, per
the brief's instruction). Nothing from #348 is merged, cherry-picked, or
reimplemented here.

## The amendment: analyses unlocked by human input

The project owner corrected the reply's own §0.2 premise ("no analysis runs
at the Enrichment stage") the same day it was written: a class of analysis
DOES run there, when its prerequisite is a human input rather than a survey
read.

### Catalog changes (`analysis_catalog.yaml`)

- `AnalysisCatalogEntry` gained `requires_input: str = ""` (analysis_
  catalog_reader.py) — the field/source kind that unlocks an analysis, in
  the vocabulary `lens` | `documentation_source` | `ingested_documentation`
  | `confirmed_glossary_term`.
- `preliminary_fit` retagged `intent: discovery` → `intent: enrichment`,
  `requires_input: lens`. It already took a `lens` runtime parameter
  (`db_derived.compute_preliminary_fit`) — a declared lens is exactly the
  human input this class is gated on. Discovery's own zero-fetch-tier test
  (`test_discovery_is_the_zero_fetch_derivation_tier`) iterates repo
  analyses only and is unaffected (`preliminary_fit` is database-only).
  **Flagged for coordinator review**: this retag changes which stage
  `preliminary_fit` is filtered under across the UI (Discovery → Enrichment)
  and there is a concurrently active branch,
  `re/preliminary-fit-scouting-signals`, touching the same analysis — worth
  a merge-order conversation before both land.
- Three new stub entries, database-only, `intent: enrichment`,
  `availability: queued`, each with a `requires_input` and a description
  starting "Not built. Placeholder registration only": `doc_source_ingestion`
  (`requires_input: documentation_source`), `doc_evidence_check`
  (`ingested_documentation`), `semantic_suggestions`
  (`confirmed_glossary_term`). None is implemented — no results reader, no
  run-route step. Scoped to `database_analyses` only (every worked example
  in the reply and the amendment is database-scoped); broadening to
  repo/filesystem is follow-on work once #348 and a glossary-confirmation
  mechanism exist there.

### Gap guards extended, not weakened

Three existing guards fired correctly against the new entries — each was a
real, deliberate inconsistency worth recording, not a false positive to
silence:

1. **`test_analysis_catalog_reader.py::test_enrichment_and_automate_have_no_
   entries_by_design`** (the actual "enrichment has zero entries" gap guard
   — CLAUDE.md rule 17's premise) — rewritten to name the four new
   `ENRICHMENT_HUMAN_INPUT_ANALYSES` ids explicitly (the same
   `DISCOVERY_FETCHES_ANYWAY`-style named-exception pattern this codebase
   already uses), asserts each carries `requires_input`, and confirms repo/
   filesystem genuinely stay empty. **CLAUDE.md rule 17 itself is now
   slightly stale** ("`enrichment`... intentionally have zero entries in the
   analysis catalog — that's by design, not a gap") — left as-is rather than
   edited here, since amending a repo-wide convention doc is outside this
   slice's scope; flagged for whoever next touches that file.
2. **`test_stage_page.py::TestSlice17DbDerivedRunnabilityFromCatalog::
   test_every_local_survey_database_id_has_a_re_step_map_entry`** — this
   guard's real purpose is "a catalog entry that claims `source: local,
   action: survey` must actually be runnable via the standard Run route,"
   and correctly caught that my three stubs are not. Extended with a named
   `ENRICHMENT_STUBS_WITH_NO_RUNNER_YET` set and a companion assertion that
   each one's `runnable_and_reason()` honestly reports `False` with a
   reason — the TRUTH for a genuinely unimplemented id, distinct from the
   slice-17 bug this class guards (an id that WAS runnable reading as
   not-runnable because two hand-maintained maps disagreed).
3. **`test_no_silent_success.py::TestRatchet`** — flagged two new best-effort
   `except Exception` sites: `step_preconditions.py`'s `_needs_human_input`
   (an unreadable/missing table — the common case here, since none of the
   three human-input tables exist yet — degrades to "not present," the safe
   direction) and `context.py::get_enrichment_analyses`'s per-row
   `fl.fact()` call (a stub id has no results reader; degrades to
   `"unlocked"`, never overclaims `"measured"`). Both added to
   `no_silent_success_baseline.json` as deliberate, reviewed best-effort
   sites — the third of the test's own three named remedies.
4. **`test_next_analysis_subresources.py::TestNoFifthTabIsAdded::
   test_sub_tabs_are_the_current_canonical_set`** — this is a SUB_TABS
   count-and-membership pin, not directly a gap guard, but it fired for the
   same reason: it named exactly five ids. Its own class docstring already
   anticipated a further addition ("not four forever"); updated to six,
   including `context`.
5. **`test_run_publish_honesty.py::TestProposedPerspectives::
   test_no_analysis_is_left_without_a_perspective`** — the three stubs
   initially declared `perspectives: []`; given real ones
   (`["Data Expert", "Steward"]`, matching this codebase's existing
   vocabulary) rather than exempted.

### The prerequisite resolver's new precondition kind

`step_preconditions.py` gained `HUMAN_INPUT_CHECKS` (a sibling dict to
`PRECONDITIONS`, not folded into it) and `human_input_state(registry,
project, requires_input)`. Kept separate from `PRECONDITIONS` because the
two answer different questions for a caller: `PRECONDITIONS` says "may a
SURVEY STEP run," and its `Precondition.remedy()`/`produced_by()` assume a
step produces the missing table; this dict says "is a human input PRESENT,"
with no step to name as a remedy.

Every check reads real stored data where a real table exists (this module's
own vocabulary is unchanged — "deliberately about stored data," just data a
person writes rather than a survey), and is **deliberately conservative**
where one does not: `documentation_sources`/`confirmed_glossary_terms` don't
exist in this checkout (#348 unmerged; no glossary-confirmation mechanism
built at all), so those checks read NOT SATISFIED — the reverse direction
from `_needs_rows`'s "cannot tell, run it anyway" for a survey step. Getting
this backwards would mean an unbuilt human-input mechanism reads as
"present," which is the wrong direction for a human input specifically.

A new route, `GET /api/context/{entity_type}/{slug}/enrichment-analyses`
(`context.py`), computes the map server-side: every `intent: enrichment`
catalog entry for the resource type, each with `unlocked`/`reason`/`state`
(`locked` | `unlocked` | `measured`) from `human_input_state` plus a
`FactLayer` read for whether it has already run. `stages/enrichment.js`'s
existing glyph vocabulary is reused for the three card states (no new
glyphs): `unrun` (○) for unlocked-with-Run-available, `no-surveyor` (◌) for
locked (closest existing meaning to "cannot be answered here yet"),
`measured` (✓) for already run.

## Rendering: Survey & analyses and By analysis on Enrichment

Both intercepted in `app.js`'s `loadPane()` dispatch, ahead of the generic
per-stage panes, when `state.stage === 'enrichment'`:

- **Survey & analyses** (`loadEnrichmentAnalysesMapPane`): "No analysis runs
  at the Enrichment stage from a survey read. What you supply here
  unlocks:" followed by one row per Enrichment-tier analysis — name,
  "unlocked by: <requires_input in words>", state glyph, and a Run button
  when unlocked-and-not-measured (wired to the existing `runAnalysis` API;
  a stub id's Run click fails gracefully with "not built yet: …", the
  honest degradation for a placeholder registration, not a bug).
- **By analysis** (`loadEnrichmentByAnalysisSummaryPane`): one summary line
  ("what you supply here unlocks N of M analyses today") linking back to
  Survey & analyses, per the reply's own instruction that it "says the same
  in one summary line and links there."

Schema Inventory is untouched — still present for databases, unaffected by
the strip restructure (confirmed by the SUB_TABS diff and the passing
`resourceTypes` filter test).

## Gate verification

**Test-level, not live** — same reasoning E0 recorded: this environment has
no Egeria sign-in credentials for `laz_local_adventureworks`
(`docs/runtime-architecture-plan.md` §4, Egeria as identity provider since
2026-09-04).

Covered instead by:

- **Backend** (`tests/test_enrichment_human_input_preconditions.py`, 10
  tests): `human_input_state()` for all four `requires_input` kinds,
  including the amendment's own gate scenario built against a real
  temporary `documentation_sources` table (the stub #348 isn't merged to
  provide) — one declared source unlocks `doc_source_ingestion`; a declared-
  but-not-ingested source does NOT unlock `doc_evidence_check` (a
  different human input); no lens leaves `preliminary_fit` locked with
  `"declare a lens on the investigation"`. Plus the catalog changes
  (`preliminary_fit`'s retag, the three stubs' registration) and the full
  route (`GET .../enrichment-analyses`) against the exact gate scenario.
- **Frontend** (`frontend-build/test-harness/enrichment-context-tab.test.mjs`
  and `context-tab-sections.test.mjs`, 7 tests, real DOM via jsdom, real
  unmodified render functions): the strip order and Context-as-default; the
  five sections rendering in order with `feeds →` lines; the human-question
  section excluding non-human catalog rows; the lens row's both states
  (declared/not) and both links marked deferred; the doc-sources slot as a
  placeholder, not a duplicate build; the map's locked/unlocked/measured
  glyph states and the gate's exact wording
  ("declare a lens on the investigation").

Both suites, plus the full pre-existing suites, pass — see "Tests run"
below. Worth a live check once this branch is reachable from a signed-in
session: open `laz_local_adventureworks`'s Enrichment stage, confirm it
opens on Context, and walk through the amendment's gate scenario for real.

## Tests run

- `frontend-build`: `npm run test:harness` (Node 20 via `nvm use 20`, and
  `npm install` — this worktree's `node_modules` didn't have `jsdom`
  installed yet) — **24 passed, 0 failed**, including the 7 new tests above
  and all 17 pre-existing ones.
- Python: `uv run pytest tests/` — full suite, **6888 passed, 0 failed, 103
  skipped** (after fixing 6 regressions this change caused — see "Gap
  guards extended, not weakened" above and the tailwind-CSS rebuild note
  below).
- `npx tailwindcss -c tailwind-next.config.js -i ./input.css -o
  ../resource_explorer/web/static/next/tailwind-next.css --minify` — rebuilt
  after adding `cursor-not-allowed` (the lens row's deferred-link styling),
  since `test_tailwind_next_class_coverage.py` checks class literals against
  the COMMITTED, already-built CSS file, not a live build.

## The addendum note to the designer

**Owner's explicit instruction (item 4 of the amendment):** add
`"Addendum 2026-09-29: analyses whose prerequisite is a human input do run
at Enrichment; the tab lists them as unlocked."` under the reply document
itself, correcting its §0.2 premise in place.

**Status: NOT done directly — flagged for the coordinator, as instructed.**
`REPLY-DESIGNER-ENRICHMENT-STAGE-IA.md` does not exist anywhere in this
branch's tree (`re/enrichment-e1-context-tab`, based on
`re/enrichment-e0-row-anatomy`). It exists only on `origin/re/design-
enrichment-ia` (commit `e25d0663`), which is not merged to `main` and was
never merged into this branch either — E0's own doc recorded the identical
situation and recommended merging that branch (or cherry-picking the reply
doc) so it becomes readable from `main` normally. Editing a file that isn't
on this branch would mean either (a) merging an unrelated branch into this
one, which this task explicitly does not ask for, or (b) fabricating the
addendum against a copy this branch doesn't actually carry, which would not
reach the real document. Neither is done. **The coordinator needs to land
this one line on `re/design-enrichment-ia` (or wherever the reply doc ends
up living once merged) directly.**

## Judgment calls, for review

1. **The lens row's both links are dead (marked "not built in /next")**
   rather than pointing at something real, because neither an investigation
   lens-declaration UI nor an ad-hoc-lens-on-one-resource control exists
   anywhere in this codebase — see "The lens row (section 2)" above. This
   is the same class of finding as E0's "What I could not find," not a
   shortcut taken here.
2. **`preliminary_fit`'s intent retag** (discovery → enrichment) is a
   meaningful behavioural change to an existing, working analysis with an
   active concurrent branch touching it (`re/preliminary-fit-scouting-
   signals`) — flagged explicitly above for a merge-order conversation,
   not silently landed.
3. **The three enrichment-tier stubs are scoped to databases only**, not
   repo/filesystem, since every worked example in the reply and the
   amendment is database-scoped and neither doc-sources nor glossary-
   confirmation exist for other resource types yet. Broadening is
   straightforward follow-on work, not a gap in this slice.
4. **CLAUDE.md rule 17's "enrichment... intentionally have zero entries" is
   now stale** and was deliberately left unedited (out of this slice's
   scope) — flagged for whoever next touches that file.
5. **The addendum note could not be applied directly** — see above; this is
   the one explicitly-flagged item the task brief anticipated might need
   the coordinator's help, and it does.
