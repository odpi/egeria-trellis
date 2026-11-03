# Slice 17: runnability from the catalog — implemented

**Coordinator brief:** Phase 1b, slice 17 (`re/coordinator-brief-phase-1b`).
**Replying to:** `REVIEW-SURVEY-PANE-285.md` §4 point 5, §6 (the "has no mapped
survey step(s)" bug on `subject_signals`/`coverage_signals`/`preliminary_fit`,
and the "✓ means the mapped analysis ran, not that the question was answered"
finding in its small-findings list).
**Branch / PR:** `re/slice17-unify-runnability` (see bottom for push/PR status).

## What the real single-source-of-truth derivation turned out to be

The brief asked to find the real link between an analysis id and what
actually runs it, rather than a third hand-listed dict. It already existed,
in two pieces, both real and both already correct:

1. **`db_derived.DB_DERIVED_ANALYSES`** — the tuple of every analysis_catalog
   id the zero-fetch `db_derived` step backs. Already complete and already
   correct: it has carried `subject_signals`/`coverage_signals`/
   `preliminary_fit` since the day they were added (2026-09-24), and both the
   run route (`databases.py::run_single_database_analysis`) and the actual
   run function (`workflows.analysis.run_database_analysis`) already checked
   membership in it directly — which is exactly why running these three
   worked when called directly, live, per the review's own finding.
2. **`survey_definition_adapter.DATABASE_ANALYSIS_RESULTS_MAP`** — already
   had a real reader for all three from the start too (`_db_derived_field_
   reader`, the same reader every other `db_derived`-backed id uses).

**The bug was narrower than "two duplicate maps disagree" made it sound.**
`database_surveyor.py`'s `DATABASE_ANALYSIS_STEP_MAP` (the `DatabaseSurveyor.
survey()`-step map) was never broken for these three ids — by design, it
never listed *any* `db_derived`-backed id, because `db_derived` never calls
`DatabaseSurveyor.survey()` at all (it opens no connection). Every caller of
that map already checks `DB_DERIVED_ANALYSES` membership FIRST and only
falls through to it for what remains. The one genuinely broken artifact was
`survey_definition_adapter.py`'s **other**, differently-shaped
`DATABASE_ANALYSIS_STEP_MAP` (re_analysis_step keys, not `DatabaseSurveyor`
step names) — a SEPARATE hand-maintained dict that fed `resolve_analysis_
plan()` → `runnable_and_reason()` (the Survey & Analyses pane's precheck) and
`ProjectRegistry.get_analysis_last_run()`'s attribution. It had all eight of
the *older* `db_derived` ids, but was never updated when the three newest
ones were added to `DB_DERIVED_ANALYSES` on the same day — because nothing
ties the two together. That is the actual defect: not "two maps disagree
today" but "two maps *can* disagree, structurally, forever."

## What was built

1. **Renamed, not just fixed**, `database_surveyor.py`'s map to
   `DATABASE_SURVEYOR_STEP_MAP` — it was never wrong, but sharing an
   identical name with a completely different dict in a different module,
   with different consumers and a different key vocabulary, is the exact
   condition that let the real bug happen unnoticed. Distinct names now.
2. **Deleted** `survey_definition_adapter.py`'s hand-maintained
   `DATABASE_ANALYSIS_STEP_MAP` outright and replaced it with
   `DATABASE_ANALYSIS_RE_STEP_MAP`, built by `_build_database_analysis_re_
   step_map()`: every id in `DB_DERIVED_ANALYSES` is mapped to `["db_derived"]`
   automatically; the remaining ~10 ids that call a real `DatabaseSurveyor`
   step or a dedicated handler stay hand-authored (there is no single
   catalog field today naming which physical `re_analysis_step` a
   connection-based analysis needs — see the constant's own docstring for
   why the `db_derived` half is the only half that could be derived). A
   future id added to `DB_DERIVED_ANALYSES` cannot silently leave this map
   behind again — it is now the same set by construction.
3. **Updated every consumer** of the old name: `registry.py`'s
   `_analysis_step_map("database")` (last-run attribution),
   `survey_definition_adapter.py`'s own `analysis_source_steps` provider and
   `DATABASE_ANALYSIS_KINDS` construction. `databases.py`'s run route,
   `workflows/analysis.py`'s `run_database_analysis`, and `scheduler.py`'s
   `_run_local_db_survey` now import the renamed `DATABASE_SURVEYOR_STEP_MAP`
   (same content, new name, no behavior change — they were never the buggy
   ones).
4. **Verified the catalog cross-check the brief asked for**, live against
   `analysis_catalog.yaml`: every database entry with `source: local, action:
   survey` (21 ids) is now a subset of `DATABASE_ANALYSIS_RE_STEP_MAP`'s keys
   — pinned as a standing test
   (`TestSlice17DbDerivedRunnabilityFromCatalog::
   test_every_local_survey_database_id_has_a_re_step_map_entry`,
   `tests/test_stage_page.py`), not just a one-off assertion for the three
   ids named in the review.
5. **Item 2 (Questions tab shows Run for every answerable question):**
   traced `rerun()` in `next/app.js` — it calls `runAnalysis(slug, analysisId,
   entityType)` directly against the run route with **no** runnability
   precheck at all; `provenanceLine()`'s "run"/"re-run" button is gated only
   on `entry.analysis_ids.length`, not on `runnable_and_reason`. So the
   Questions tab's Run button was **never** broken by this bug — only the
   separate Survey & Analyses pane's `build_analyses_index` (via
   `runnable_and_reason`) was. Confirmed rather than assumed: added
   `TestSlice17DbDerivedRunnabilityFromCatalog::
   test_build_analyses_index_reports_them_runnable` (three parametrized
   cases) proving the index now reports `runnable: true` for all three ids,
   which is the thing that actually needed fixing for this item.
6. **Item 3 (a ✓ requires the answer to address the question's level):**
   `Envelope` gains `level_mismatch: bool` and `level_note: str`.
   `FactLayer.answer()` calls a new `_check_level()`: when a question's
   `levels` (design §18.3; `QuestionCatalogEntry.levels`, from #290) include
   anything below `resource` (`container`/`member`/`field`) and **every**
   fact that answered it comes from an analysis whose `analysis_catalog.yaml`
   `target_shape` is `whole_resource_only`, the envelope is marked
   `level_mismatch` — `answerable` stays true (something real WAS measured),
   but the checkmark is withheld. This is the signal available TODAY, from a
   field the catalog already carries; design §18.3 itself says the FULL
   guard (did the run actually PRODUCE per-member rows, not just "is the
   analysis capable of it") needs slice 20's `scopes` declaration to be
   checkable, and is explicitly deferred there — this is a real, if partial,
   step in that direction, not a guess at slice 20's shape.
   - `/next`'s `rowState()` returns a new `'partial'` state (glyph and tone
     already existed, unused, in `app.js` — reused rather than inventing a
     new one) when `env.answerable && env.level_mismatch`. `isFullyAnswered()`
     is the new single predicate ("answerable and not level-mismatched")
     that `updateAnsweredCount()` now uses instead of bare `env.answerable`,
     so the row glyph and the "N of M answered" count cannot disagree. The
     row keeps its evidence/numbers-behind-this/re-run actions (design
     §18.3: a level mismatch is not "nothing was measured" — it is a real
     answer that cannot name the level it was asked at) and gets an extra
     caveat line from `env.level_note`.
   - Confirmed against the real catalog, not only a fixture:
     `subject_signals`/`coverage_signals`/`preliminary_fit` are exactly the
     case — `container`/`member`-level questions per `question_catalog.yaml`,
     answered by `target_shape: whole_resource_only` analyses — pinned in
     `TestRealCatalogAgreesWithTheLiveBugReport`.

## What was deliberately NOT done

- **The full per-run "did this analysis actually produce a member row"
  guard.** Design §18.3 says that guard "lands with slice 20's `scopes`
  declaration, which is what makes it checkable" — it is not built here, and
  `target_shape` (a static, per-analysis catalog declaration, not a per-run
  fact) is a real but coarser signal than what slice 20 will provide. An
  analysis whose `target_shape` allows per-member output but whose LAST run
  happened not to produce any member rows for this specific resource is not
  caught by this slice's gate — flagged, not fixed, same as the design doc's
  own note.
- **Filesystem's parallel `FILESYSTEM_ANALYSIS_STEP_MAP`.** Checked per the
  ground rules: filesystem has exactly one local analysis and one
  `re_analysis_step` today (a genuine 1:1 map, not a fan-out), so there is no
  live instance of this bug there. Flagged in that constant's own comment
  (`surveyors/filesystem/survey_definition_adapter.py`) as a real risk if
  filesystem grows a second, `db_derived`-shaped analysis later — out of this
  slice's database-only scope per the coordinator brief.
- **`resolve_analysis_plan`'s repo-path callers that pass `entity_type=
  "database"` into `execute_and_record_analysis`/`run_analysis`** (the
  repo-only batch-run path, reached from `work_lists.py` for a database work
  list). This looked, on inspection, like a pre-existing separate gap (
  `run_analysis`'s own `registry.get(slug)` is repo-only regardless of
  entity_type) — unrelated to slice 17's bug and out of scope; not touched,
  not fixed, noted here so it is not mistaken for something this slice
  addressed.

## Tests

- `tests/test_stage_page.py`: new `TestSlice17DbDerivedRunnabilityFromCatalog`
  (6 cases: the three named ids are individually runnable via
  `runnable_and_reason`; the same three report `runnable: true` from
  `build_analyses_index`; every `DB_DERIVED_ANALYSES` id agrees with the
  derived map by construction; every real `source: local, action: survey`
  database catalog id has an entry and resolves runnable).
- `tests/test_slice17_question_level_gate.py`: new, 10 cases — the level
  gate withholds the tick for container/member-level questions answered
  only by whole-resource rollups (including the real `preliminary_fit` row's
  four contributing ids), is exempt at `resource` level and when `levels` is
  absent, is satisfied by a single per-member-capable analysis even when a
  rollup also contributed, and is confirmed against the real on-disk catalog
  for `subject_signals`.
- `tests/test_slice17_questions_tab_level_gate_js.py`: new, 6 structural
  cases against `next/app.js` (this repo's existing convention for testing
  frontend logic — no Node runtime in the suite): `rowState` checks
  `level_mismatch`, `isFullyAnswered` exists and is what `updateAnsweredCount`
  uses, `'partial'` has a glyph/tone/legend entry, and a `'partial'` row
  keeps its secondary actions and renders `level_note`.
- Updated in place (content/name changes, not new behavior asserted):
  `tests/test_database_surveyor_steps.py` (renamed import),
  `tests/test_db_fs_analysis_last_activity.py` (renamed import; assertion
  widened from 8 to 11 db_derived-owned ids, now asserted equal to
  `DB_DERIVED_ANALYSES` directly rather than a second hand-typed set),
  `tests/test_resolve_analysis_plan_resource_type_dispatch.py`,
  `tests/test_work_lists_batch_entity_type.py`,
  `tests/test_db_fs_results_and_questions.py` (renamed import; comment
  updated — the asserted set difference was already correct and unaffected
  by the fix, since all eleven `db_derived` ids are on both sides of it now),
  `tests/test_tier_resolution_entity_type_dispatch.py`,
  `tests/test_database_analysis_run_route.py` (comment only).
- Full suite: **6362 passed, 102 skipped, 1 failed** — the same
  pre-existing `test_egeria_live_smoke.py` live-Egeria environment failure
  slice 16 also reported (confirmed independent of this change: it needs a
  live Egeria connection this sandboxed run does not have).

## Live signed-in gate

**Not run — explicitly, per this slice's own instructions to say so rather
than skip it silently.** `http://localhost:8810/` answered `200` (the shared
dev server is up), but every `/api/*` route returned `{"error":
"login_required", ...}` — this session has no Egeria user id/password to
sign in with, and this worktree's own branch is not what that shared server
is running in any case (it runs the shared checkout's current `main`, not
this branch). Confirming this slice's fix live needs a session with
credentials for `coco_pharma @ local-docker`, running THIS branch, to:

- Open a database's Survey & Analyses pane (or Questions tab) for
  `coco_pharma` and confirm `subject_signals`, `coverage_signals` and
  `preliminary_fit` show an enabled Run button, not "has no mapped survey
  step(s)".
- `POST /api/databases/{slug}/analyses/subject_signals/run` (and the other
  two ids) against a real registered slug and confirm 200/started, not 400.
- `GET /api/projects/{slug}/analyses-index?entity_type=database` and confirm
  `runnable: true` for all three ids.
- Open the Questions tab, find a question whose `levels` include `container`/
  `member` and whose only answering analysis is one of these three (e.g.
  "Could this be in scope for what I am looking for?"), and confirm it shows
  the new withheld-tick (`◐`, "ran, but not at this level") state rather than
  a plain ✓, with the level_note caveat visible under the answer.

Whoever runs this: append the outcome here, one sentence per screen, per the
coordinator brief's own gate convention.

## Push / PR status

Committed on `re/slice17-unify-runnability`, pushed to
`dwolfson/trellis`. `gh pr create --repo dwolfson/trellis` attempted — see
the session's final report for the exact outcome (branch pushed regardless
of whether the PR create call itself succeeded; not retried in a loop if it
hung on auth, per this slice's own ground rules).
