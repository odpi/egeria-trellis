# Slice 12 — Database Survey Definitions, implemented

**Coordinator brief:** Phase 1b, Slice 12 from the brief, owner-approved.
**PR:** #303 (`re/slice12-database-survey-definitions`).
**Final local full-suite count** (after every fix below, non-overlapping
run): 6481 passed, 104 skipped, 0 failed.

**Merge order:** this branch is based on `re/schema-headline-and-level-gate-note`'s
tip (`0e1a7417`), not `main` — merge that branch's PR FIRST. This branch's
own PR diff will shrink to just Slice 12's commits once the base merges;
don't read its extra (schema-headline) commits as part of Slice 12.
`re/is-published-honours-linkage` is based on `main` directly and is
independent of both — merges third, in any order relative to the other two.

## What this adds

Three Dr.Egeria Survey Definition documents for "PostgreSQL Database",
generated, published live to the dev Egeria platform, and reconciled —
the first database Survey Definitions RE has ever authored (repos and
filesystems already had theirs):

- **Database Scouting Scan** (`GovActionProcess::DatabaseScoutingSurvey`) —
  `postgres_schema_and_stats` → `postgres_operations` → `credential_capability`.
- **Database Analysis Survey** (`GovActionProcess::DatabaseAnalysisSurvey`) —
  `postgres_column_profile` → `postgres_nested_columns` → `db_derived` →
  `postgres_operations`.
- **Database Assessment Survey** (`GovActionProcess::DatabaseAssessmentSurvey`) —
  `postgres_operations` alone.

Live GUIDs (dev platform, published 2026-09-26): scouting
`1ac5ed3d-b809-4246-96a1-4663e4db539a`, analysis
`4395adeb-5d85-4c7d-b70e-5643390b1644`, assessment
`fb673734-1978-410d-9359-cdc8d7caaef1`.

## Why `postgres_operations` is chained into all three

`DATABASE_STEP_REGISTRY`'s `postgres_operations` step bundles four
analyses at different `analysis_catalog.yaml` intent tiers:
`privilege_audit` (assessment), `db_activity_signals`/`db_resilience`
(scouting), `db_external_dependencies` (analysis). The step cannot be
split apart without a larger refactor of `survey_definition_adapter.py`'s
step registry, which is out of this slice's scope — so it is chained into
all three Survey Definitions, each as its own qualified
`GovActionProcessStep` (direct precedent: the repo generator's
`automate_full` already reuses scouting/discovery's own steps this way).

`SPEC_ANALYSIS_SCOPE` (`scripts/generate_database_survey_definition.py`)
keeps each document's `Link Element To Scope` (ScopedBy) links honest
despite the shared step: Scouting only claims the questions
`db_activity_signals`/`db_resilience` answer, Analysis only claims
`db_external_dependencies`'s, Assessment only claims `privilege_audit`'s
— never all four just because the step that runs them is shared.

## New files

- `docs/dr-egeria/database_survey_types.csv` — the 7-row survey-kind/step
  spec table `generate_database_survey_definition.py` reads, mirroring
  the repo generator's own CSV-driven design.
- `scripts/generate_database_survey_definition.py` — clone/adaptation of
  `generate_repo_survey_definition.py`; same guard/provenance machinery
  (`.generated_database.json` sidecar, hand-authored-guard preservation).
- `docs/dr-egeria/survey-definitions/database-survey-definition-{scouting,analysis,assessment}.md`
  — the generated documents themselves, run through Dr.Egeria to publish.
- `scripts/reconcile_database_survey_definition_links.py` — near-verbatim
  clone of `reconcile_survey_definition_links.py` (step-edge reconciler).
- `scripts/reconcile_database_survey_definition_scopes.py` — clone of
  `reconcile_survey_definition_scopes.py` (ScopedBy reconciler), reading
  its file list from the generator's own `SPECS` rather than a separate
  `_batch.json` manifest — bootstrap-healing registration for database
  Survey Definitions is deliberately **not** attempted here; a platform
  wipe/reset will not auto-heal these three documents today. Logged as a
  follow-up, not fixed in this slice.

Both reconcilers were run live (`--dry-run` / report-only) against the dev
platform immediately after publish: links reconciler reported
`kept 2/3/0 edge(s), would remove 0 duplicate, 0 stale` across the three
documents (expected for a first-time authoring); scopes reconciler
reported `kept 8/11/2, missing 0, extra 0, unresolvable 0` — every
`ScopedBy` link authored matches what each document names, nothing extra.

## Two copy fixes, same `/next` Survey & analyses pane (`app.js`)

**(a)** `loadSurveyPane()`'s red "Scope: all tiers — stage filter
unavailable · retry" badge rendered even with zero candidates at all, where
retry cannot help. Both the badge-class and label ternaries now gate on
`data.scoping === 'full-scan' && all.length`; a neutral stage label renders
instead when there is nothing to scope.

**(b)** "No local or RE-authored survey definitions for this resource" /
"The adapter registered none for this technology type." read as though the
resource itself was the problem. Replaced with "No Survey Definitions have
been authored for `<technology type>` yet" / "The analyses below still run
individually; a Survey Definition only bundles them into an
Egeria-launchable process." The separately-rendered "Also known to Egeria"
section (`nativeProcessesSectionHtml`) is unchanged and still renders
unconditionally.

## A real bug found live, not by this slice's own authoring: credential fallback missing on the Survey Definition run path

Gating the Scouting definition live on `coco_pharma` (below) found that
**every** database Survey Definition run through this pane fails on its
first step, regardless of which analyses it bundles:

```
RE step 'postgres_schema_and_stats' failed: Database credentials are
required to connect ('user'/'password' both missing or empty) — there is
no other fallback (env vars, config) inside this function; callers must
resolve credentials themselves first (e.g. from
DatabaseEntity.db_user/db_password).
```

Root cause: `resource_explorer/web/routes/survey_definitions.py`'s run
endpoint passes the request body's `db_user`/`db_pwd` straight through to
`SurveyDefinitionExecutor.run()` with no fallback, while the plain
(non-Survey-Definition) survey route in `databases.py` already falls back
to the entity's own stored credentials
(`resolved_db_user = req.db_user or database.db_user`, `databases.py`
line ~809). This path never had that fallback, and nothing had ever
exercised it for a database before this slice — no database Survey
Definition existed to run. Fixed generically in
`SurveyDefinitionExecutor.run()` (not per-route, so repo/filesystem
callers are unaffected and gain nothing they didn't have): when neither
`db_user` nor `db_pwd` is supplied, fall back to
`getattr(entity, "db_user", "")`/`getattr(entity, "db_password", "")` —
a no-op for entity types that carry no such attributes.

This is a pre-existing gap in shared executor code, not something this
slice's own new files introduced — flagged rather than silently expanded
in scope, but fixed here since it directly blocked verifying "Run
executes it," the gate's own third criterion, and the fix is a small,
narrowly-scoped, generically-safe one-line fallback matching an
already-established pattern elsewhere in the codebase.

## Live gate (port 8811, `coco_pharma`, branch tip)

1. `/next` (not the legacy `/` UI — a wrong turn worth naming: the legacy
   UI serves a different "Scouting" dashboard for databases entirely,
   with no Survey & analyses pane at all) → DBs → `coco_pharma` → **1
   Scouting** → **Survey & analyses**.
2. `SCOUTING · 1` — **Database Scouting Scan**, 3 steps, no red badge,
   `Run →` present.
3. Clicked Run → confirmed → after the credential fix, **3 of 3 steps ran
   `ok`** (verified via the Activity log's JSON detail, not just the
   summary line). The one remaining error — "Database 'coco_pharma' is
   not yet cataloged in Egeria — cannot publish a Survey Definition
   step's results without an existing asset to attach the SurveyReport
   to" — is a pre-existing fact about this particular dev database (it
   was never separately cataloged as an Egeria Asset, only surveyed
   locally), not a defect in this slice or the credential fix; publishing
   is a distinct action from running the steps.
4. Questions tab, before and after the run: "7 schema(s) (coco_ods,
   coco_sus, demo, demo_auth, eu_sales, target_sales, us_sales), 6 of 8
   visible to this credential · 61 table(s) (58 base, 3 view) · 479
   column(s)" — unchanged, as expected (the run re-measured the same
   already-surveyed database).
5. Assessment and Analysis tabs' Survey & analyses panes also show
   **Database Assessment Survey** and **Database Analysis Survey** as
   RE-authored candidates with no red badge, confirming the shared
   `postgres_operations` step's scope-narrowing works across all three
   documents, not just Scouting.

## Test suite note: publishing live data broke 5 pre-existing tests' isolation, not their logic

`tests/test_survey_definitions_routes.py`'s `TestListCandidates` mocks
only the full-scan lookup (`find_candidate_process_guids`), not the
questions-scoped one (`find_candidate_process_guids_by_questions`) that
`list_candidates` tries first whenever `get_questions(resource_type=...)`
resolves any rows — which it always does for `entity_type="database"`,
independent of the `mydb` fixture. Before this slice, no live database
Survey Definition existed, so the unmocked questions-scoped lookup
harmlessly returned nothing; once this slice published three real ones
with real `ScopedBy` links to those exact questions, the same unmocked
call started returning real candidates, and 5 tests asserting an empty or
single-candidate list failed. Not a code regression — a live-Egeria test
isolation gap the live publish exposed for the first time. Fixed by
pinning `find_candidate_process_guids_by_questions` to `return_value=[]`
in all 5, matching the pattern `test_phase_param_uses_scoped_lookup_when_questions_resolve`
already used correctly in the same file.

## Bootstrap-healing registration: not deferred after all — a real CI failure, fixed

The first full-suite run after the credential fix showed 3 failures, not
1: alongside the already-known pre-existing
`test_egeria_live_smoke.py::TestTheByNameFallbackWorks::
test_a_cataloged_database_is_findable_by_name`, two were real and caused
by this slice:

**`test_reachability_audit.py::TestSurveyDefinitionsAreRestorable::
test_every_generated_document_is_in_the_batch_manifest`** — the "deferred"
bootstrap-healing gap noted in an earlier draft of this doc turned out to
be a hard CI gate, not a soft gap: `bootstrap.py` is one batch per
directory (`BATCH_MANIFEST_FILE`), and this test pins that every `.md` in
`docs/dr-egeria/survey-definitions/` is listed in that directory's single
`_batch.json` — which is the REPO batch's manifest, with a repo-specific
canary and repo-specific `post_heal` reconciler scripts that know nothing
about database Survey Definitions. Fixed by moving the 3 generated
documents (and their `.generated_database.json` sidecar) into their own
`docs/dr-egeria/survey-definitions-database/` directory, with its own
`_batch.json` (canary `GovActionProcess::DatabaseAnalysisSurvey`, the
largest of the three; `post_heal`/`post_heal_checks` pointing at this
slice's own two reconcile scripts), and adding that directory to
`docs/dr-egeria/_folder_order.json` right after `survey-definitions` (both
depend on `questions`, neither on the other). `generate_database_survey_definition.py`'s
`SURVEY_DEFS_DIR` and `reconcile_database_survey_definition_scopes.py`'s
`_SURVEY_DEFS_DIR` updated to match; the links reconciler needed no path
change (it derives everything from the generator's own `SPECS`).

This split, however, would have silently broken something it depends on:
`survey_definition_docs.py`'s `documented_definitions()` — the LOCAL
document list `survey_definition_reader.py`'s questions-scoped fast path
(`find_candidate_process_guids_by_questions`) and the survey-definition
cache warmer both read — hardcoded ONE directory
(`definition_docs_dir()`), because every resource type's documents used to
coexist there by filename prefix
(`{resource_type}-survey-definition-*.md`). Generalized it: a new
`_extension_docs_dirs()` finds every `survey-definitions-*` sibling
directory next to the primary one and merges its documents in, but ONLY
when `documented_definitions()` is called with no explicit `directory`
argument (every existing test passes its own fixture directory and must
keep seeing only what it wrote). Verified live: the server's
"survey-definition cache warmed" log line went from 10 to 13 definitions
after the split, and re-checking `coco_pharma`'s Scouting/Assessment/
Analysis panes on 8811 still showed `Scope: scouting`/`assessment`/
`analysis` (never the "all tiers" full-scan fallback) — the fast local-
match path survived the restructuring rather than silently degrading
every database Survey Definition lookup to a live Egeria full scan.

**`test_schedule_survey_target.py::TestDefinitionsListRoute::
test_resource_type_is_read_not_hardcoded`** — this test's own docstring
predicted this exact moment: it asserted `resource_type == "repo"` for
every entry `list_definitions()` returns, with a comment noting that would
break "the first database/filesystem Survey Definition ever authored."
That's this slice. Rewrote the assertion to check specific documents'
resource_type individually (`RepoFullSurvey` → `"repo"`,
`DatabaseAnalysisSurvey` → `"database"`) rather than asserting a blanket
constant across all of them — the assertion the test was always really
testing.

New tests: `test_survey_definition_docs.py` gained
`TestExtensionDirectoriesAreMergedWhenNoDirectoryIsGiven` (3 tests: merge
happens by default, an explicit directory argument is NOT merged with
extensions, no extension directories present is a no-op) covering
`_extension_docs_dirs`/the generalized `documented_definitions()`.

## Cross-repo grep for other consumers of the old path (a move across a boundary)

Before calling the directory split done, grepped the WHOLE trellis repo
(not just this package) for `survey-definitions` in every place a
greppable or ancestor-naming consumer could live:

- **Dockerfile\*, .dockerignore, docker-compose\*/\*.yml/\*.yaml, Makefile,
  .github/workflows/**: nothing. No build, container, or CI config
  references this path at all.
- **egeria-workspaces-fs** (the live bind-mounted checkout,
  `/Users/dwolfson/localGit/egeria-v6/egeria-workspaces-fs`): nothing.
- **Docs that tell a human where definitions live**, found and fixed:
  - `docs/open-stack-checklist.md` §"What has to come back, and in this
    order" — the platform-reset recovery checklist. Added the new
    `survey-definitions-database` batch and its reconciler as steps 5–6,
    and corrected "does 1–3 by canary" to name every folder in
    `_folder_order.json` now that there are two survey-definition
    batches, not one. This was the one that mattered most: a human
    following the old checklist after a platform wipe would not have
    known to also re-run the database batch, and would have seen a
    healthy repo canary and assumed recovery was complete.
  - `docs/multi-resource-questions-design.md` — corrected "No survey
    definitions exist for either type" (now false for database) with a
    dated note, rather than deleting the sentence — the surrounding
    paragraph is otherwise still accurate for filesystem.
- **Checked, found still accurate, left alone**: `docs/Architecture.md`,
  `docs/admin-guide.md`, `docs/egeria-operations.md` (only link to
  `open-stack-checklist.md`, already fixed above) — no path references.
  `docs/Backlog.md`'s three hits are historical/dated entries describing
  past states or a different, repo-only test
  (`test_survey_definition_generator_guard.py`) — none make a claim that
  is now false, so none were touched.
- **`tests/test_survey_execution_plan.py::
  test_every_live_definition_plans_to_its_existing_order`** — flagged by
  the Backlog grep as a test that walks `documented_definitions()` against
  "every real, authored Survey Definition document" — exactly the function
  this slice generalized. Confirmed it still passes with the 3 database
  documents now merged in (`build_plan()` accepts them the same as the
  repo ones); a genuinely separate, already-logged gap in that same test
  (missing `step_registry=`, `docs/Backlog.md` "PR #247" entry) is
  pre-existing and untouched.

## A second, real defect found investigating the pre-existing by-name smoke-test failure — fixed on `re/is-published-honours-linkage`, not this branch

`test_egeria_live_smoke.py::TestTheByNameFallbackWorks::
test_a_cataloged_database_is_findable_by_name` had failed on every run for
days, excused each time as "pre-existing, hits the live platform." Told to
stop excusing it and find the actual cause: this deployment has TWO
databases with a cached `egeria_asset_guid` —
`localhost_docker_coco_pharma` (linkage correctly recorded `stale` since
2026-09-22) and `egeria_optional_prefect_db` (linkage: no row AT ALL). The
test's selection filter (`... .get("status") != "stale"`) treats "never
checked" the same as "confirmed healthy," so it picked the second one —
whose cached GUID `45a75724-...` turns out to ALSO 404 when resolved
directly (confirmed live: `_get_element_by_guid_` raises
`PyegeriaNotFoundException`), just never detected, because nothing had
recently surveyed/published through this rarely-used database to trip the
reactive `guard_linkage` check.

The test's own skip message already said "run `resource-explorer
egeria-recheck`" first — it just never did so itself. **Fixed on
`re/is-published-honours-linkage`** (per the coordinator's ruling — that
branch is what makes the resulting `stale` records user-visible, so it
owns this fix, not Slice 12), by calling
`egeria_linkage.recheck_all_linkages(registry, entity_types=["database"])`
at the top of the test. Diagnosed and first reproduced from this branch's
own live gate, so recorded here too for the full story, but the code
change itself lives on the other branch — this branch's own diff does not
include it. The `recheck_all_linkages` call proactively marked
`egeria_optional_prefect_db` `stale` in the SHARED dev registry the first
time it ran, which is why this branch's full-suite runs below (both taken
after that point) already see the by-name test skip rather than fail, even
though this branch's own test file was reverted back to its pre-fix form.

Second full-suite run, after the directory split + `documented_definitions()`
generalization: 6477 passed, 103 skipped, 1 failed (only the by-name test —
confirms the directory split and `test_schedule_survey_target.py` fix left
nothing else broken). Final run, after the shared registry was corrected
(via the is-published branch's fix, above) but with this branch's own test
file unchanged: 6477 passed, 104 skipped, 0 failed — the by-name test now
skips on its own pre-existing filter, since the underlying data is honest
again; this branch introduces no code change to make that happen.

## A fourth defect, found by the owner's post-merge gate: a Survey Definition run degraded answers the per-analysis path had already produced (#303)

Reported by the owner ~2h after the Database Scouting Scan run above:
`db_activity_signals` (which had read "0 writes and 6 reads since
statistics collection began" after an earlier per-analysis re-run) and
`db_resilience` both flipped to "ran and found nothing," and the answered
count fell from 4 of 9 to 3 of 9 (8812) / 1 of 9 (8813) — on every port,
all reading the same shared registry. The only write in between was this
branch's own Scouting Survey Definition run.

**Root cause, confirmed by diffing the two latest stored `database_surveys`
rows for `localhost_docker_coco_pharma`:** `SurveyDefinitionExecutor`
dispatches each Survey Definition step as its OWN separate
`DatabaseSurveyor.survey()` call — a 3-step Scouting definition
(`postgres_schema_and_stats` -> `postgres_operations` ->
`credential_capability`) writes THREE survey rows a couple of seconds
apart, not one combined row the way a per-analysis run does.
`_store_results` wrote `"operations": results.get("operations", {})`
unconditionally — correct for the `postgres_operations` step's OWN run,
but the LAST step, `credential_capability`, collects no `operations` at
all, and its `{}` became the newest row's value, silently shadowing the
real operations data `postgres_operations` had written two rows earlier.
`db_activity_signals`/`db_resilience` read the operations section off the
single latest row, so they read nothing — a per-analysis-path answer
clobbered by an unrelated LATER step in the same Survey Definition run,
never a real re-measurement.

Exactly the "correct number, wrong label" / prior-value-preservation class
`_store_results`'s own docstring already documents for `row_count`/
`size_bytes` (the `_store_results` fix earlier in this session, ported from
the enumeration-floor PR) — just never extended to the `operations`/
`credential_capability` keys. **Fixed the same way**: before writing,
`_store_results` now reads back the LATEST prior survey row and falls back
to its `operations`/`credential_capability` values when THIS run's own
results have none (`{}` unambiguously means "this run's requested steps
didn't include it," the same "step didn't run" convention the two
docstrings already state — never a genuine empty measurement, so no risk
of masking a real "found nothing" answer).

Live repair: re-ran the Scouting Survey Definition against `coco_pharma`
directly via `run_survey_definition()` after the fix — all 3 steps `ok`,
and the fresh row now carries BOTH the real `operations` section (real
`activity_signals`/`resilience`/`external_dependencies`/`privilege_audit`
data) AND `credential_capability` together, correctly. Confirmed on 8811:
`db_activity_signals` reads "0 writes and 6 reads since statistics
collection began" again, `db_resilience` reads "primary; no replicas; WAL
archiving off; no backup tool detected" again, answered count back to 4
of 9. (8812/8813 will pick up the same repaired row automatically — it's
the same shared registry — no separate action needed there.)

Tests: `test_store_results_preserves_prior_operations.py` (new, 4 tests) —
operations survives a later credential_capability-only run; the symmetric
case (credential_capability survives a later operations-only run); a
genuine first-ever run with neither step run yet still correctly reports
empty (not a blanket "never empty" rule); and an end-to-end test through
`SurveyDefinitionExecutor`'s own `_run_postgres_operations`/
`_run_credential_capability` adapters, run in the same order a real
Scouting definition's steps chain would, asserting the surviving
operations section is byte-for-byte identical in shape to what the
per-analysis path itself wrote — the literal #303 regression, reproduced
and pinned. 95 tests across the affected files re-run clean
(`test_database_surveyor_steps.py`, `test_store_results_preserves_prior_stats.py`,
the new file, `test_egeria_database_surveyor_secrets.py`,
`test_credential_capability_step.py`, `test_collector_honesty.py`,
`test_survey_definition_executor.py`).

## CI failure (PR #303): two more incomplete mocks in `test_survey_definitions_routes.py`

CI reported `2 failed, 6448 passed, 131 skipped` on this branch:
`test_egeria_tech_type_catalog_failure_does_not_break_listing` and
`test_egeria_native_processes_excludes_delete_kind`, both
`assert resp.status_code == 200` → got 400. Both passed in every local run
(6477–6481 passed) because this dev environment has a real reachable
Egeria/registry to silently fall back to; CI has neither, so the SAME
incomplete-mock gap the 5 tests earlier in this branch were fixed for
(only mocking the full-scan `find_candidate_process_guids`, not the
questions-scoped `find_candidate_process_guids_by_questions` that
`list_candidates` tries FIRST for `entity_type="database"`) reached a real
network call in CI and 400'd, instead of silently succeeding against a
live platform the way it did here. Missed these two originally because
neither asserts on the candidate list itself (one checks a step's
annotation-type enrichment, the other native-process kinds), so an extra
real candidate slipping in locally didn't visibly break either assertion.

Fixed the same way as the other 5: added the missing
`find_candidate_process_guids_by_questions` mock (`return_value=[]`) to
both, and added `resp.text` to both status-code assertion messages per the
coordinator's ask, so a future CI failure here shows the 400 body directly
rather than needing a second round-trip to find it. Verified against a
genuinely unreachable Egeria endpoint locally
(`EGERIA_PLATFORM_URL=https://127.0.0.1:1`), not just re-run against the
live one, to confirm both are now actually isolated rather than still
depending on a fallback that happened to work here.

## Deferred

- Nothing in this slice touched pyegeria; no PYEGERIA_ISSUES entry was
  needed.
