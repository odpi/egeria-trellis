# CURATE-PUBLISH-FRESHNESS-IMPLEMENTED — Catalogue's publish step re-surveys only what's stale

**Status:** built, 2026-09-20. Branch `re/curate-publish-freshness`, worktree
`wt-curate-freshness`, branched from `main`.

## The problem

`resource_explorer/workflows/curate_commit.py`'s `execute_curation()`, in its
`publish_asset` step, called `SurveyOrchestrator(registry=registry).run(slug,
steps=None)` unconditionally. `steps=None` runs every sub-surveyor
(`survey_orchestrator.py`'s own docstring: "None (default) runs every
sub-surveyor, exactly as before this parameter existed"), so every press of
the Catalogue button did a full, unconditional re-survey of the repository —
re-fetching and re-running everything, taking minutes on a large repo,
regardless of whether fresh data already existed.

**Decision (project owner, 2026-09-20):** "it shouldn't have to execute all
of the surveys anyway — its likely that the surveys that the user is
interested in have already been published to Egeria? And why would we
execute and run surveys that the user isn't interested in?"

Two asks followed: wire in the existing freshness gate, and never run an
analysis that has never been run before just because it's in the default
step catalog.

## Investigation findings

1. **`registry.get_analysis_last_run(entity_type, slug)`** (`registry.py`
   ~line 7920) returns `{analysis_id: {last_run_at, last_run_status, ...}}`,
   built from `activity_log` rows (`operation` in `analysis_run`/`survey`).
   An analysis with no row at all is simply absent from this dict — there is
   no "never run" entry to filter out, it's just not a key. This is the
   natural "has run history" test: `analysis_id in history`.

2. **`analysis_id` vs. `re_analysis_step` vocabulary.** `assess_freshness`
   and `get_analysis_last_run` both key on `analysis_id`
   (`repo_health`, `architecture_recovery`, ...) —
   `SurveyOrchestrator.run(steps=[...])` instead expects `re_analysis_step`
   keys (`repo_conventions`, `architecture_doc_lens_recovery`, ...). Two
   translation tables exist in `repo_survey_definition_adapter.py`:
   - `REPO_ANALYSIS_STEP_MAP` — the ownership *partition* (each step key
     belongs to exactly one analysis; used for run attribution).
   - `REPO_ANALYSIS_SOURCE_STEPS` — "what to run to refresh this analysis";
     identical to the partition for every analysis that owns its own steps,
     but for a `derives_from` analysis that owns none (`architecture_diagram`,
     which renders a view of `architecture_recovery`'s data) it resolves to
     the *source's* steps instead of `[]`.

   `tests/test_run_publish_honesty.py`'s
   `TestOwnershipMapIsOnlyUsedForAttribution` exists specifically to catch a
   new module reading `REPO_ANALYSIS_STEP_MAP` for this "what do I run"
   question — my first pass did exactly that, the test caught it immediately
   (`git log` shows the fix), and `_resurvey_plan` now uses
   `REPO_ANALYSIS_SOURCE_STEPS`.

3. **Auto-publish already covers most of the ground.** `workflows/analysis.py`
   (~line 220-270): any Assessment/Analysis "Run →" against a repo with
   `registry.has_assigned_egeria_project("repo", slug)` — Catalogue's own
   precondition — already auto-publishes that run's results, scoped to
   exactly the steps that ran. So by the time a curator reaches Catalogue,
   most analyses that have ever run have typically already been published;
   freshness (not "has this ever reached Egeria") is what's actually left to
   decide most of the time. The edge case this doesn't cover — an analysis
   ran before the Egeria project was ever assigned to this repo, so its data
   is fresh but was never published — is handled explicitly (point 4 below).

4. **`EgeriaPublisher.publish()`** (~line 196) always calls
   `_find_or_create_asset` then `_create_survey_report`, regardless of
   annotation count — `_find_or_create_asset` is idempotent (verify-then-
   search-then-create). So when nothing is stale but this repo has no cached
   asset guid yet, the cheapest correct thing is: run `SurveyOrchestrator
   .run(slug, steps=[])` (an empty, essentially free survey) and call
   `publish()` on the resulting near-empty `SurveyResult` purely so the asset
   gets created. Building a separate "just create the asset" path in
   `egeria_publisher.py` was out of scope for this task (which is
   `curate_commit.py`'s call site only) and the idempotent find-or-create
   makes the defensive call cheap enough not to need one.

5. **`SurveyOrchestrator.run(slug, steps=[])`** — confirmed by reading the
   method (`survey_orchestrator.py`): `step_keys_to_run = set(steps) &
   STEP_REGISTRY.keys()` is the empty set for `steps=[]`, `selected` is
   `[]`, no surveyor runs, `result.steps_run = []`, and the method returns
   a normal `SurveyResult` with zero annotations — no error, no fallback to
   "run everything". Confirmed live via `test_curate_commit.py`'s
   `test_all_fresh_without_existing_asset_still_ensures_asset_exists`.

## What was built

`_resurvey_plan(registry, slug)` (new function in `curate_commit.py`)
decides what `publish_asset` re-surveys:

- **No run history at all** → `steps=None` (full survey, unchanged from
  before this change) — there's nothing to be selective about on a genuine
  first catalogue, and running a full survey once is exactly what "a repo
  that's never been catalogued still needs a first real survey" means.
- **Some history** → for each `analysis_id` with a row (regardless of
  whether that row is a success or an `error`), call `assess_freshness`.
  Only STALE ones (including error-only ones — `assess_freshness` already
  treats an error run as not-fresh) contribute their
  `REPO_ANALYSIS_SOURCE_STEPS` step keys to the re-survey list. An
  `analysis_id` never run at all is never a candidate, full stop — it never
  even enters the fresh/stale split.
- **`execute_curation`** then branches on the plan's result and the
  currently-cached asset guid (`registry.get_egeria_asset_guid(slug)`):
  - stale steps exist, OR no history at all (full survey), OR everything's
    fresh but no asset guid cached yet → run the survey (with the computed
    `steps`) and call `EgeriaPublisher.publish()`.
  - nothing stale AND an asset guid already exists → skip the survey and
    the publish call entirely; the step still reports `"done"`.
- The `publish_asset` step's detail message now says which of these
  happened (e.g. "1 of 2 previously-run analyses already fresh and
  skipped; re-surveying 1 stale one(s) · asset ... · survey report ... · N
  annotations linked", or "all N previously-run analyses already fresh —
  nothing to re-survey; asset ... already published — nothing
  re-published") instead of the old generic "surveying first, then
  publishing" message that was true regardless of what actually happened.

## Tests

`tests/test_curate_commit.py` (new) — `TestResurveyPlan` covers the pure
decision function (no history → full survey; mixed stale/fresh/never-run →
only stale steps; all fresh → `[]`; error-last-run → counts as history but
not fresh, so it's re-run). `TestExecuteCurationPublishAssetStep` covers the
full wiring with `SurveyOrchestrator`/`EgeriaPublisher` mocked the way
`tests/test_analysis_run_auto_publish.py` mocks them: stale+fresh mix only
re-runs the stale steps; all-fresh-with-existing-asset skips both calls
entirely; all-fresh-without-existing-asset still runs an empty survey and
publishes to create the asset; no history runs a full survey.

`tests/test_curate.py`'s pre-existing
`TestTheCommitSteps::test_each_step_writes_its_outcome` needed one
assertion updated: its `_seed()` fixture stamps 7 analyses as having just
run successfully and mocks `get_egeria_asset_guid` to already return a
guid, which is now correctly recognized as "already fresh, already
published, nothing to do" rather than manufacturing a new survey report —
exactly the behavior this change exists to produce. Its other assertions
(classifications, sub_resources, components) are untouched, since those
steps don't read `asset_guid`'s provenance, only its value.

Full suite: `uv run pytest tests/ -q` → 5187 passed, 103 skipped, 0 failed.

## What was explicitly not touched

`assess_freshness()`, `workflows/analysis.py`'s auto-publish, and
`SurveyOrchestrator.run()`'s own semantics are all unchanged — this is
scoped to `curate_commit.py`'s call site reusing what already existed. The
`classifications`/`sub_resources`/`components` steps in `execute_curation`
are untouched.
