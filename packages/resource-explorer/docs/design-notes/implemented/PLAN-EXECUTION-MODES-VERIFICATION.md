# PLAN — Closing the verification gap on RE's three execution paths

**Status:** proposal, 2026-09-18. Nothing here is built. Written against branch
`re/plan-execution-modes-and-prefect` (worktree `wt-orchestration-plan`, branched from `647cd1b4`).

Answers the Backlog entry *"TIER 1 — 'Three execution modes' don't map onto one verified mechanism,
and two of the paths are untested"*, which the project owner raised on 2026-09-18 by asking whether
RE-local, Egeria-coordinated and Hybrid execution all genuinely work.

Every claim below was checked against the source in this worktree. Where the Backlog entry and the
code disagree, the code wins and the disagreement is called out rather than smoothed over.

---

## 0. Two corrections to the Backlog entry, before planning against it

The Backlog entry is a good starting point and two of its statements are stale. Recording them here
so the plan is built on the tree rather than on the summary.

**(a) The global-override concern is already fixed.** The entry (and the older Prefect entry at
`Backlog.md:3661`) warns that "when `config.prefect.enabled` is true, *every* step marked
`executes_at: resource-explorer` is rerouted to Prefect, so a global flag overrides what a
definition explicitly asked for". That is no longer what the code does.
`survey_definition_executor.py:317-332` (`_use_prefect`) honours `executes_at`, and rerouting a
`resource-explorer` step requires the separate, off-by-default `prefect.route_local_steps`
(`config.py:344-347`). `tests/test_prefect_dispatch.py:179` and `:184` pin exactly that behaviour.
The Backlog entry's `:162-167` line reference points at code that has since moved.

**(b) There *is* a filesystem hybrid path.** The entry says `HybridDatabaseSurveyor` "is
database-only — there is no `HybridFilesystemSurveyor` or repo equivalent on the same mechanism".
There is no *class*, but there is a function that does the same job:
`surveyors/filesystem/hybrid_filesystem_surveyor.py:12` `run_hybrid_filesystem_survey()`, called
from `web/routes/filesystems.py:271-272` and `cli/main.py:2104-2105`. So the hybrid idea is two
implementations on two resource types with no shared abstraction, which is a slightly different
(and slightly worse) problem than one orphan class. The repo half of the claim holds: there is no
repo hybrid path.

The entry's core finding survives both corrections: **"three modes" is not one mechanism with three
settings.** It is `executes_at` routing (three legal values, one dispatch loop) plus two unrelated
hybrid entry points that never enter that loop.

---

## 1. What each path actually is, and what "working" would mean

### Path A — `executes_at: resource-explorer` (RE-local)

`survey_definition_executor.py:449-472`. Looks the step's `re_analysis_step` up in the registered
`ResourceTypeAdapter.re_analysis_steps` and calls it in-process. Consecutive local steps are
batched through `adapter.run_batch` where the adapter supports it (`:367-425`; repo does,
database/filesystem do not).

**Working means:** a real Survey Definition read from a live Egeria, dispatched step by step,
producing annotations that land in the step's own table and publish to Egeria with unique
qualified names.

**Coverage today:** the dispatch *logic* is well covered, and there is substantial adjacent
coverage across the 311 files in `tests/`. What is not covered is the whole route end to end from
a live Egeria-hosted definition.

### Path B — `executes_at: egeria` (Egeria-coordinated)

Two branches, and the difference matters:

- `:473-486` — the step's engine is in `adapter.other_engine_handlers`. Both the database and
  filesystem adapters register `{"egeria": _trigger_egeria_native_survey}`
  (`database/survey_definition_adapter.py:113`, `filesystem/survey_definition_adapter.py:138`).
  The handler requires an already-catalogued asset (`egeria_asset_guid`), calls
  `trigger_survey_by_guid`, and returns **only an engine-action GUID** — the survey is async and
  nothing in RE waits for or reconciles the result. The step reports `status: "triggered"`.
- `:487-506` — no handler registered for this entity type (i.e. repos). The step is recorded as
  `not_executed_no_egeria_handler` and counted in `errors`. This is the 2026-08-24 "closing the
  stub" fix and it is correct behaviour; it is also the honest statement that repos have no
  Egeria-coordinated path at all.

**Working means:** trigger succeeds against a live Egeria, the engine action actually runs there,
and the resulting `SurveyReport`/annotations are later retrievable and attributable to the RE run
that triggered them. That last clause is the part nothing implements — `survey-model.md` §D5 still
lists async result retrieval as *Proposed*, and Backlog notes there is no unified results view.

**Coverage today:** none found for either branch.

### Path C — the hybrid surveyors

`HybridDatabaseSurveyor` (`surveyors/database/hybrid_database_surveyor.py:14`) and
`run_hybrid_filesystem_survey` (`.../filesystem/hybrid_filesystem_surveyor.py:12`). Neither is
reachable from `survey_definition_executor.py`. Entry points are
`web/routes/databases.py:250-251`, `cli/main.py:1687-1697`, `web/routes/filesystems.py:271-272`,
`cli/main.py:2104-2105`.

**What it actually does** — this is the part the decision in §3 has to rest on, so it is worth
being precise. `HybridDatabaseSurveyor.survey()` (`:62-126`) is a **strategy selector**, not a step
runner:

1. `_check_egeria_available()` (`:43-50`) caches a reachability probe.
2. If Egeria is reachable and `refresh` is false, it asks for the *latest existing* survey
   (`get_latest_survey`) and, if one exists, returns it — re-using a previous Egeria survey rather
   than running anything (`:110-113` → `_retrieve_egeria_survey`, `:249-279`).
3. Otherwise `_run_egeria_survey` (`:128-210`) runs the **local** psycopg2 survey first and then
   *publishes those local results into Egeria* (`publish_local_survey`), reporting
   `source: "egeria-custom"`. Only when there is no local result does it fall through to Egeria's
   own native `catalog_and_survey` (`:176-183`) — and that branch also *catalogues* the database as
   a side effect, which the `executes_at: egeria` handler deliberately refuses to do.
4. Any failure degrades to `_run_custom_survey` (`:212-247`) with `source: "custom"`.

So the hybrid path carries three capabilities the `executes_at` system genuinely does not have:

- **Cache-or-run**: reuse an existing Egeria survey instead of re-running (step 2).
- **Catalog-on-demand**: `catalog_and_survey` will create the asset; `_trigger_egeria_native_survey`
  raises if the asset does not already exist (`database/survey_definition_adapter.py:62-66`).
- **Provenance in the result**: a `source` field (`egeria` / `egeria-custom` / `custom` / `error`)
  telling the caller which engine the numbers came from. `executes_at` dispatch records the engine
  per step in `steps_report`, but not as a property of the returned survey.

CLAUDE.md rule 15 ("must run the local scan immediately after triggering the Egeria native survey")
describes step 3 and is a real constraint, not a historical note: Egeria's native survey is async
and returns no schema data, so without the local scan the UI would have nothing to show.

**Coverage today:** none. No file under `tests/` references `run_hybrid_survey`,
`HybridDatabaseSurveyor` or `run_hybrid_filesystem_survey` (grepped across `tests/`, zero hits) —
and both are on the default web path for "survey this database/filesystem", so this is untested
code users hit routinely, not untested code nobody reaches.

---

## 2. What a real end-to-end test looks like per path

The principle: **mock what is expensive and deterministic, use the real thing for the contract you
are actually unsure about.** RE's uncertainty is not "does our dispatch loop branch correctly" —
that is already unit-tested. It is "does Egeria accept what we send it, and does what we get back
mean what we think". Mocks cannot answer that, because the mock is written from the same belief the
code is.

### Path A — RE-local: fake Egeria's *read*, run the steps for real

**Infrastructure:** none beyond what CI already has, if the definition is loaded from a fixture.
A live Egeria is *not* required for correctness here, because the only Egeria involvement is
reading the Survey Definition graph and (optionally) publishing at the end.

- Fixture a Survey Definition as `SurveyDefinitionReader` would return it (multi-step, at least one
  `run_batch`-eligible consecutive pair, at least one guard).
- Run `SurveyDefinitionExecutor` against a small real repo/filesystem fixture with publishing off.
- Assert: every step in `steps_report`; annotations actually written to the step tables;
  `_stamp_definition_provenance` gave keyless annotations the definition's qualified name
  (`survey_definition_executor.py:325-345`) — the collision guard this exists for is real and
  `survey_report.assert_unique_qualified_names` raises on it.
- One **live** variant, run on demand rather than in CI (marker `@pytest.mark.live_egeria`): read a
  definition from the dev Egeria by GUID and publish the result, asserting a `SurveyReport` with the
  expected annotation count comes back.

**Verdict: mockable, with one opt-in live run.** Effort: ~1 day for the fixture-based test, ~0.5 day
for the live variant.

### Path B — Egeria-coordinated: the live run is the whole point

**Infrastructure: a live Egeria is mandatory.** A mocked `trigger_survey_by_guid` proves only that
RE calls a function it already calls. Every question worth answering here is on Egeria's side: does
the engine action get created, does the engine host pick it up, does it produce a `SurveyReport`,
and can RE find that report afterwards and tie it to the run it triggered.

Per memory and the project owner's 2026-09-13 ruling, **writing to the dev Egeria platform is an
ordinary shared write**, so this is not blocked — it does need the usual coordination with live
peers before it runs.

- **B1 (mocked, CI):** the failure modes only. Uncatalogued asset raises with the
  "no stored Egeria asset guid" message; an unregistered engine yields
  `not_executed_no_egeria_handler` **and** an `errors` entry (`:487-506`); an unknown `executes_at`
  yields `unrecognized_engine` (`:507-514`). These are cheap and pin the "never silently succeeds"
  property this codebase cares about.
- **B2 (live, on demand):** catalogue a throwaway database and filesystem in dev Egeria, run a
  one-step definition with `executes_at: egeria` on each, assert a real engine-action GUID, then
  **poll Egeria until the SurveyReport exists** and assert it is attributable. Expect this to fail
  first time at the *attribution* step, not the trigger step — there is no code that resolves an
  engine action back to its report.
- **B3:** repos have no handler. Either accept that permanently and document it, or add a repo
  `other_engine_handlers["egeria"]`. Do not leave it as an accident.

**Verdict: correctness requires the real thing.** Effort: ~0.5 day for B1; ~2-3 days for B2
including the poll/attribution helper it will force into existence; B3 depends on the §3 decision.

### Path C — hybrid: test the strategy selector, then decide its fate

Because §3 recommends folding this in, the tests here are deliberately cheap and are mostly a
**characterisation harness**: they capture what today's behaviour is so a refactor cannot change it
by accident.

- Table-driven tests over `survey()` with `_check_egeria_available` and the Egeria surveyor faked:
  Egeria unreachable → `custom`; reachable with an existing survey and `refresh=False` → the cached
  Egeria report is returned *and no survey runs*; `refresh=True` with credentials → local scan then
  publish, `source: "egeria-custom"`; publish raises → `source: "custom"` with the Egeria error
  appended, not swallowed; no credentials → the explicit `source: "error"` dict, never an empty
  success.
- The same shape for `run_hybrid_filesystem_survey`: assert the local survey is saved with
  `source="local"` *before* the Egeria attempt (`hybrid_filesystem_surveyor.py:39-45`) and that a
  publish failure records the error on the entity rather than losing the survey (`:69-76`).
- One live run per resource type against dev Egeria, asserting `source` is what actually happened —
  this is the one that catches "correct number, wrong label".

**Verdict: mockable for the selector logic; one live run each to confirm the `source` labels are
truthful.** Effort: ~1.5 days.

### Prefect

Not a fourth path — it is where Path A's steps execute when routed. Its verification belongs to
`PLAN-PREFECT-OR-ALTERNATIVE.md` §4. Note for scoping: a real Prefect server **is** reachable on
this machine right now (see that document), so the "needs infrastructure nobody has" objection does
not apply.

---

## 3. Recommendation on `HybridDatabaseSurveyor`

**Recommendation: fold the *capabilities* into `executes_at` routing; retire the classes once the
capabilities land. Do not keep it as documented legacy, and do not retire it before the
capabilities exist.**

Reasoning, in order of weight:

1. **It is not legacy in the sense of "unused".** It is the default web path for surveying a
   database (`web/routes/databases.py:250`) and, in its function form, a filesystem
   (`web/routes/filesystems.py:271`). Documenting it as legacy would be a sign on live code.
2. **It does three things `executes_at` cannot** (§1 Path C): cache-or-run, catalog-on-demand, and
   engine provenance on the result. Retiring it in favour of the current `executes_at: egeria`
   handler would be a straight capability loss — that handler refuses to run at all on an
   uncatalogued asset, which is exactly the common case the hybrid path exists to absorb.
3. **Keeping it separate is what produced the bug class the entry is about.** Two mechanisms called
   "hybrid" — one a class, one a loose function — with no shared abstraction, one tested and one
   not, is how "three modes" stopped being one mechanism in the first place.

Concretely, folding in means:

- A fourth legal `executes_at` value, **`egeria-hybrid`**, registered in `other_engine_handlers` on
  the database and filesystem adapters, whose handler is the strategy selector currently inside
  `HybridDatabaseSurveyor.survey()`: check for an existing Egeria survey → reuse; else run the local
  step and publish; else trigger native; degrade to local. Extending `other_engine_handlers` is the
  designed extension point and needs no change to the dispatch loop, which already routes any key
  present in that dict (`:473`).
- Catalog-on-demand becomes an explicit property of that handler rather than an implicit side
  effect of falling into `catalog_and_survey`.
- `source` survives into `steps_report` as a first-class field, so a survey's provenance is visible
  in the run report and not only in the hybrid function's return dict.
- The web/CLI call sites move to the executor with a one-step synthetic definition; the old modules
  become thin deprecation shims for one release, then go.
- CLAUDE.md rule 15 moves with the code — it is a real constraint on the new handler, not a note
  about a deleted class.

**Decision needed (project owner):** whether `egeria-hybrid` is acceptable as a fourth `executes_at`
value. `executes_at` is documented as deliberately open-ended (`Backlog.md:2806`), so this is
additive — but it is the project owner's vocabulary and it is about to grow.

**If the answer is no**, the honest fallback is *keep as documented legacy* with the characterisation
tests from §2 Path C and an explicit note in `Architecture.md` saying why it is separate — but that
leaves two mechanisms permanently and does not close the entry's actual complaint.

---

## 4. Sequencing and effort

Ordered so that each phase is independently useful and nothing depends on a decision that has not
been made yet. Effort figures are working days for one person, and they are estimates.

| Phase | What | Effort | Depends on |
|---|---|---|---|
| **0** | Correct the Backlog entry on the two points in §0 (stale `:162-167` claim; the filesystem hybrid exists). Cheap, and stops the next reader planning against it. | 0.5 d | — |
| **1** | Path C characterisation tests (§2 Path C, mocked half only). Locks current behaviour before anything moves. | 1.5 d | — |
| **2** | Path B1 failure-mode tests (§2 Path B). Pure unit work, no infrastructure. | 0.5 d | — |
| **3** | Path A end-to-end from a fixtured definition, publishing off. | 1 d | — |
| **4** | Live-run harness: a `live_egeria` pytest marker, a dev-Egeria fixture that catalogues and tears down a throwaway database/filesystem, and the coordination note for shared writes. This is the infrastructure phases 5-6 both need. | 1.5 d | dev Egeria |
| **5** | Path B2 live: trigger, poll, attribute. Expect the attribution helper to be new work discovered here. | 2-3 d | 4 |
| **6** | Path A/C live variants (§2). | 1 d | 4 |
| **7** | The §3 fold-in, if approved: `egeria-hybrid` handler, `source` in `steps_report`, call sites moved, shims. | 3-4 d | §3 decision, 1 |
| **8** | Path B3: decide and implement (or document) the repo `executes_at: egeria` handler. | 1-2 d | §3 decision |

**Total: ~12-15 days** with the fold-in, ~8-10 without.

Phases 0-3 are worth doing regardless of any decision in §3 or in the Prefect document — they need
no infrastructure, no approval, and no coordination, and they convert "no test coverage found" into
a known state for two of the three paths.

---

## 5. What this plan does not claim

- **It does not claim the paths are broken.** Paths B and C have no test coverage; that is a
  statement about evidence, not about behaviour. Path C in particular is exercised by hand every
  time someone surveys a database from the UI. "Untested" and "broken" are different findings and
  this plan only establishes the first.
- **It does not verify Egeria's side.** Whether the dev Egeria's engine host actually picks up and
  completes an engine action triggered by `trigger_survey_by_guid` has not been observed in this
  pass. Phase 5 is where that becomes known, and it is the phase most likely to overrun.
- **The `run_batch` asymmetry is noted, not planned.** The repo adapter supports batching and
  database/filesystem do not (`survey_definition_executor.py:371-373`), so a mixed-type comparison
  of Path A is not apples to apples. Out of scope here.
