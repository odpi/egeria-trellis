# Prefect prerequisite resolution — implemented (§E)

**Brief:** `BRIEF-KEYS-AND-ACTIVITY-CLOBBER.md` §E, "The
Database Analysis Survey fails on the default engine."

**Branch:** `re/prefect-prerequisite-resolution`, off `origin/main` (`019c2796`).
Built in an isolated worktree (`git worktree add -b
re/prefect-prerequisite-resolution <path> origin/main`), not in the shared
checkout at `~/localGit/egeria-v6/trellis` — per the architecture session's
2026-09-27 ruling that branches must be checked out only in a separate
worktree/clone, never in that shared directory (a prior agent's branch was
left checked out there and served stale files on port 8810).

**PR:** not opened by this session — the architecture session opens PRs for
this brief; tip reported to it separately.

## Root cause

`GovActionProcess::DatabaseAnalysisSurvey`, run with no `engine_override`
(the Prefect path, the default), failed in under two seconds:

> step 'postgres_column_profile' needs 'has_schema_inventory', produced by
> 'postgres_schema_and_stats', but 'postgres_schema_and_stats' is not one of
> this definition's own steps.

Two correctness checks disagreed, and the wrong one won:

1. `SurveyDefinitionExecutor._any_step_needs_prerequisites` (existing code,
   §17.1) asks `prerequisite_resolver.resolve()`, per entity, whether the
   demanding step's precondition is already satisfied by **stored data**.
   When Scouting had run 20 minutes earlier, it correctly said SATISFIED —
   `step_preconditions.unmet()` counts rows in `database_tables` for this
   slug and found some, of any age. That routed the run to
   `_run_via_prefect` instead of the local loop (the local loop is only
   used when the resolver finds real work to do).
2. `_run_via_prefect` then called `survey_execution_plan.build_plan(...,
   step_registry=...)`, whose `_add_produces_edges` is a purely
   **structural** check — "is the producer step even authored into this
   definition?" — with no registry/entity awareness at all, by original
   design (`build_plan` is meant to be built once per definition and reused
   across entities, so there was "no registry/entity here to ask"). Since
   `postgres_schema_and_stats` is Scouting's own step, not one of
   `DatabaseAnalysisSurvey`'s, it raised `MissingPrerequisiteError`
   regardless of what check (1) had just established.

The same definition with `engine_override: "resource-explorer"` succeeded,
because that override skips `_run_via_prefect` entirely and never reaches
the structural check.

A second, independent gap surfaced verifying the fix live (see "Additional
fix" below): a whole-definition Prefect run wrote **no `step_runs` rows at
all**, for any step, success or failure — `step_cost_observer.record` had
only ever been called from the local dispatch loop.

## Fix

### 1. A fresh stored answer short-circuits the structural check (items 1–2)

`build_plan()`/`_add_produces_edges()` (`survey_execution_plan.py`) now take
optional `registry`/`entity`. `_run_via_prefect` passes both — it already
has `entity` in scope (it is called once per entity, not once per
definition, so the "no entity here" argument in the original module
docstring does not hold for this specific caller). When both are given, a
precondition whose producer is not one of this definition's own steps is
checked against a new `step_preconditions.fresh_hit()` **before** raising:

- A hit within a freshness window needs no edge at all, regardless of
  whether the producer is even part of this definition, and stamps
  `PlannedStep.satisfied_by_stored[name] = surveyed_at` — the provenance the
  brief asked for ("schema inventory from 20:59:29"). This reached the
  report as `satisfied_by_stored` on the step entry, threaded through
  `serialise()` → `prefect/flows.py::run_planned_step_task`/
  `re_survey_definition_flow`.
- No hit (missing or stale) still raises `MissingPrerequisiteError` exactly
  as before — now carrying `precondition_name`/`producer_key` attributes so
  the caller can enrich the message (see item 3).
- Omitting `registry`/`entity` (every other call site) keeps the function
  byte-identical to before this change.

**Freshness window.** `step_preconditions.py` gained
`DEFAULT_FRESHNESS_WINDOW_HOURS = 24`, `_latest_surveyed_at()`,
`_hours_since()`, `Precondition.freshness()`, and the module-level
`fresh_hit()` both the resolver and the plan builder now share. **Judgement
call, documented explicitly per the brief's own instruction**: design §5.1a
(`multi-resource-questions-design.md`) defines freshness for *catalog
statistics* (`pg_stats`: fresh/stale/never_collected/not_visible, judged by
modification count and age) — a different question from "does a structural
inventory row still count as current". §5.1a gives no numeric window for
the second question. 24 hours is chosen so a same-day Scouting → Analysis
sequence (the brief's own worked example, 20 minutes apart) is always
satisfied, while a week-old inventory is treated as worth refreshing rather
than trusted forever. This is a new, narrower policy than what
`step_preconditions.unmet()` already does elsewhere (any row of any age
satisfies a precondition, with no window at all) — deliberately: the
Prefect structural check is the one place a completely absent producer
means "cannot run this at all", so it is the one place worth asking "how
current" rather than only "does it exist". `unmet()`/`evaluate()`
(runtime, per-step skip decisions) are untouched.

### 2. A named next step instead of a bare failure (item 3)

`SurveyDefinitionExecutor._run_via_prefect` now catches
`MissingPrerequisiteError` (separately from `PrerequisiteTierError`, whose
existing "not a fallback case" comment is unchanged) and calls
`_propose_producer_definition()`: it looks up every Survey Definition for
this entity's technology type (`self.reader.find_candidate_process_guids` +
`fetch`, the same calls the rest of this module already makes) and checks
whether any of them declares the missing producer as one of its own steps.
When one does, the raised `SurveyDefinitionExecutorError`'s message becomes:

> `<original message>` Proposed next step: run 'Scouting Survey' first — it
> includes 'postgres_schema_and_stats' — then re-run this definition.

Best-effort: any failure reaching Egeria (unreachable, no match) falls back
to the original message, which still names the missing producer — never
worse than before, only better when the lookup succeeds. This message
reaches both the CLI (`SurveyDefinitionExecutorError` is already caught and
printed there) and the classic UI's survey-run error surface, which reads
the same exception's text — no separate UI wiring was needed since both
already display this exception's message.

### 3. `requires:` alongside `produces:` (item 4)

`StepInfo.requires` (`repo_survey_definition_adapter.py`, shared by every
adapter including the database one) is a new **property**, not a second
hand-typed field — it derives the producer step keys from
`requires_context` via `step_preconditions.PRECONDITIONS[name].
produced_by()`, the same inversion `produces`'s own docstring already
describes for the opposite direction. A separately-maintained tuple would
drift from `requires_context` exactly the way that module's docstring
warns a hand-typed producer string drifts from `produces`.

`survey_execution_plan.authoring_gaps(survey_def, step_registry)` is the
generator-side reader: for each of a definition's own steps, is every step
it `requires` also one of the definition's own steps? Same question
`_add_produces_edges` asks structurally, without the side effect of
raising, so an author (or a lint step over Survey Definition YAML) can list
every unmet-by-design prerequisite in one pass. Not wired into a CLI/report
surface yet — no such generator command exists today to hang it off; this
change adds the reader function and leaves wiring it into one as a
follow-up (`docs/Backlog.md` candidate, not filed as a separate task here
since it has no live incident behind it).

### 4. Additional fix found during live verification: Prefect-orchestrated steps wrote no `step_runs` rows

Not one of the brief's four `Fix` bullets, but required by its own gate
("the Analysis-tier `step_runs` rows must appear"). Verified live (see
below): a whole-definition Prefect run completed successfully and wrote
**zero** `step_runs` rows, for any step. `step_cost_observer.observe`/
`record` were only ever called from `SurveyDefinitionExecutor`'s own local
dispatch loop (three call sites, all inside the `while` loop or
`_auto_run_producer`) — never from `prefect/flows.py`. A definition routed
to Prefect (the default, whenever nothing needs a local-loop resolution)
has therefore never had its steps' costs measured or its runs turn up in
the `step_runs`-backed dashboard (§17.2/§17.3) at all, independent of
today's prerequisite bug.

Fixed: `run_planned_step_task` (`prefect/flows.py`) now wraps the actual
step call in `step_cost_observer.observe(..., executor="prefect",
source="prefect")` and records it via the same `step_cost_observer.record`
the local loop uses, under the SAME run-wide `surveyed_at` the local loop
stamps (`SurveyDefinitionExecutor.run()`'s `surveyed_at`, threaded through
`_run_via_prefect` → `re_survey_definition_flow` → `run_planned_step_task`,
none of which carried it before this change — the Prefect path had no
shared timestamp of its own). Declared cost tiers are looked up fresh per
step from the same step registry the local loop reads (`StepInfo` is not
JSON-serialisable, so it isn't threaded through the plan itself). Recording
is best-effort/never fatal, matching `_propose_producer_definition`'s own
policy — a step's real output must never be lost over an observability
side-channel; added to `tests/no_silent_success_baseline.json` as a
reviewed, deliberate best-effort site (its own docstring explains why).

## Live verification (laz_local_adventureworks, real shared registry, real Prefect server)

Per the architecture session's explicit answer: dev-platform writes are
ordinary here, and the real Prefect server/worker were exercised directly.
**Caveat also flagged by that session and confirmed true**: the run-queue
leader (port 8810) and the bare-host Prefect worker both run `main`'s
installed code, so exercising the fix through the web UI or the deployed
worker would have run the OLD, unfixed planner/executor. Verification was
instead done **in-process**, importing this worktree's own code directly
against the real shared registry and the real Prefect API server
(`PREFECT_API_URL=http://localhost:4200/api`, confirmed reachable) — the
step BODIES that actually touch the database (`run_surveyor_step_task`) are
unchanged by this fix, so running them for real, through the real Prefect
flow/task engine, exercises exactly the same code path a live UI-driven run
would, using this branch's planner/executor instead of main's.

Ran `GovActionProcess::DatabaseScoutingSurvey` (stored credentials,
`db_user='dwolfson'`), which completed via the local fallback (this
Python process had no `PREFECT_API_URL` exported for the connection check
`get_config()` reports vs. what Prefect's own client reads from the
environment — a pre-existing, unrelated quirk: RE's config object defaults
`api_url` to `http://localhost:4200/api` but Prefect's client library reads
`PREFECT_API_URL` from the process environment directly, not from RE's
config; re-run with it exported below). `surveyed_at =
2026-09-28T03:08:26`. Three steps ok: `postgres_schema_and_stats`,
`postgres_operations`, `credential_capability`.

Then, with `PREFECT_API_URL` exported, ran
`GovActionProcess::DatabaseAnalysisSurvey` with `engine_override=None` (the
exact failing case from the brief's incident, run 997e93b3) —
**succeeded**, `surveyed_at = 2026-09-28T03:13:57.661456`:

```
postgres_column_profile   ok   engine=prefect   satisfied_by_stored={'has_schema_inventory': '2026-09-28T03:09:17...'}
postgres_nested_columns   ok   engine=prefect   satisfied_by_stored={'has_schema_inventory': '2026-09-28T03:09:17...'}
db_derived                ok   engine=prefect   satisfied_by_stored={'has_schema_inventory': '2026-09-28T03:09:17...'}
postgres_operations       ok   engine=prefect
```

`registry.query_step_runs(slug='laz_local_adventureworks',
surveyed_at='2026-09-28T03:13:57.661456')` — **4 rows**, one per step above,
all `executor='prefect'`, `source='prefect'`, each with real, non-zero
`wall_ms`/`cpu_ms` and (for `postgres_column_profile`) `connects: 4` —
confirming both the fix (success on the default engine) and the additional
fix (real `step_runs` rows from the Prefect path) simultaneously.

**One real, pre-existing (not caused by this change) disagreement the newly-
wired observer surfaced immediately**: `db_derived` — "declares
fetch_cost='none' but opened 1 connection(s)". Genuine finding, out of
scope for this section, left as-is (visible now precisely because the
Prefect path is finally being measured at all).

**Column-profile questions "answer"**: checked directly against
`database_column_profiles` rather than through a report view (no report
view of the column-profile questions was quicker to reach than the table
itself in the time available). 468 rows `state='measured'` / 768
`state='not_collected'` for `laz_local_adventureworks`, for this run's
own timestamp — the measured rows do answer. **But**, verified across
every run today (eight distinct `surveyed_at` stamps, 19:20:22 through
03:14:06), **the 468/768 split is byte-identical in every one of them,
including the very first Scouting-only run before `postgres_column_profile`
had ever executed on this database.** This confirms the brief's own
"Observation for whoever takes this" was right to be suspicious: the 468
"measured" values come entirely from `pg_stats`, read by
`postgres_schema_and_stats` (Scouting), and `postgres_column_profile`'s own
per-value sampling has not added anything observable in any run today. The
proximate cause was visible in this session's own run: sampling errors
("TABLESAMPLE clause can only be applied to tables and materialized views",
hit on a view) abort the surrounding transaction, and Postgres then fails
every subsequent sampling attempt in the same transaction
("current transaction is aborted, commands ignored until end of transaction
block") — all silently, with the step still reporting `status: "ok"`. This
is a real, separate defect from §E's own fix (it exists identically whether
the definition is run via Prefect or the local loop, and predates this
change), so it is **not fixed here** — flagged as a background task
(`task_3ce22015`, "Fix postgres_column_profile silently sampling nothing on
views") for a dedicated session, per this section's gate instruction to
state explicitly what was found rather than silently work around it.

### Never-scouted case: tested against a fixture, not live

Per the architecture session's own suggestion, and because "pick a database
that has genuinely never been scouted" cannot be done safely against the
shared registry without either disturbing another session's use of a real
entity or fabricating one that looks like real shared state — this was
tested as an integration test against a fixture, not live:

- `tests/test_survey_execution_plan.py`:
  `test_a_stale_stored_answer_still_raises_the_build_time_error` and
  `test_no_stored_answer_at_all_still_raises_the_build_time_error` build a
  fixture registry (a fake `_conn()` answering `MAX(surveyed_at)` with
  either an old timestamp or `None`) and a definition missing the producer,
  and assert `MissingPrerequisiteError` still raises, carrying
  `precondition_name`/`producer_key`.
- `tests/test_survey_definition_executor.py::TestProposeProducerDefinition`
  drives `_propose_producer_definition` directly with a mocked
  `SurveyDefinitionReader` (candidates + `fetch`, same pattern the rest of
  that test file already uses) and asserts the enriched "Proposed next
  step: run 'Scouting Survey' first" text, the fallback to the plain
  message when no candidate declares the producer, and the fallback when
  the reader itself fails.

## Tests

New/changed:
- `tests/test_step_preconditions.py` — `fresh_hit`/`Precondition.freshness`:
  a recent row is a hit; a stale row is not a hit but still reports its
  timestamp; no row and an unparseable timestamp are both "not fresh, can't
  tell" (the conservative reading); an unknown precondition name is not a
  hit.
- `tests/test_survey_execution_plan.py` — a fresh stored answer needs no
  producer edge even when the producer is outside the definition, and
  stamps `satisfied_by_stored`; a stale or missing answer still raises with
  the new exception attributes; omitting `registry`/`entity` keeps the old
  behaviour unchanged; `serialise()` carries `satisfied_by_stored`;
  `StepInfo.requires` derives from `requires_context`; `authoring_gaps()`
  names a missing producer and is empty when the producer is also authored.
  One pre-existing test (`test_serialise_carries_exactly_what_the_flow_needs`)
  updated for the new `satisfied_by_stored` key in `serialise()`'s output.
- `tests/test_survey_definition_executor.py::TestProposeProducerDefinition` —
  four tests, described above.
- `tests/no_silent_success_baseline.json` — one new deliberate best-effort
  entry, `_propose_producer_definition` (Egeria lookup failure falls back to
  the plain message, which is itself informative).

## CI red — root cause was this branch's own tests, not shared infrastructure

**Correction, 2026-09-28.** The three `TestListCandidates` failures above
were first diagnosed as pre-existing, order-dependent flakiness unrelated
to this change (confirmed passing in isolation, and CI was already red on
other branches at the time). That diagnosis was **wrong** — or at least
incomplete. After `re/tests-clear-candidates-cache` (#323) landed on main
fixing the ACTUAL pre-existing flakiness (a `_candidates_cache`/
`_fetch_cache` staleness issue in `survey_definition_reader.py`, confirmed
by that branch's own clean 6605/0 run), every other branch merged the same
day ran clean (F 6615/0, G3/empty-state-split 6612/0, G2 6621/0) — but
`-rf` on this branch, merged up to the same point, still showed the exact
same three failures. That ruled out shared test infrastructure: the
polluter had to be in this branch's own diff.

**Root cause, found by bisection** (`pytest tests/test_survey_definition_executor.py
tests/test_survey_definitions_routes.py -q` reproduced it in ~12s; narrowed
from there): `TestProposeProducerDefinition`'s test helper `_executor()`
called `register_adapter(ResourceTypeAdapter(entity_type="database", ...,
re_analysis_steps={}))` — registering a **zero-step stub** under the REAL
`"database"` key in `survey_definition_executor._ADAPTERS`, a module-level,
process-lifetime dict with no test-scoped reset (`register_adapter`'s own
docstring: "called once at import time by each resource type's ... module" —
a production assumption, not a test-safe one). Three of these four new
tests called it, permanently replacing the real database adapter (imported
once at process start, with its real `step_registry`) for every test that
ran afterward in the same pytest process — including
`test_survey_definitions_routes.py::TestListCandidates`, whose `/candidates`
route calls `get_adapter("database")` and got this stub back: zero steps,
so an empty `annotation_types` list, a missing
`egeria_produced_annotation_types` key (never reached, nothing to
enrich), and an empty `kinds` set. Every other pre-existing test in
`test_survey_definition_executor.py` already avoids this by registering
under `entity_type="fake"` — these four were the only ones in the whole
file to use the real `"database"` key, which is exactly why nothing broke
until they were added.

**Fix:** `TestProposeProducerDefinition._ENTITY_TYPE = "fake"`, used
everywhere the class previously hardcoded `"database"` (both in the
`ResourceTypeAdapter` it registers and in the `_propose_producer_definition`
calls under test — the method only reads `technology_type` off whatever
adapter comes back, so the entity_type string itself carries no test
semantics). Verified: `pytest tests/test_survey_definition_executor.py
tests/test_survey_definitions_routes.py -q` — 59 passed, 0 failed (was 3
failed, 56 passed) — with the fix alone, `tests/conftest.py`'s tech-type-
catalog reset (below) REMOVED. The adapter fix is what makes the trio pass;
the singleton reset is a separate, independently-justified hardening, not
required for this.

**A second, independently-justified fix, found while investigating (kept,
not required for the trio):** `web/routes/survey_definitions.py` keeps its
own module-level `_tech_type_catalog` — a lazily-constructed
`EgeriaTechTypeCatalog`, built once and reused ("callers should share one
instance rather than refetch per request", per its own comment). Right for
production; a process-lifetime singleton with instance-level caches
(`_all_types_cache`/`_detail_cache`) that no test resets is a latent risk
for any FUTURE test that patches `EgeriaTechTypeCatalog`'s methods after an
earlier test has already warmed the real singleton. Added a second autouse
fixture in `tests/conftest.py`, `clear_tech_type_catalog_singleton`,
resetting it to `None` before and after every test — same shape as
`clear_survey_definition_reader_caches` just above it, same file, same
reasoning, but confirmed NOT the cause of the trio's failure (verified by
toggling it on/off against the reproduction above).

**No test on this branch reaches a live Egeria platform.** Every reader
interaction in this branch's new tests goes through `MagicMock()` or the
existing `_fake_reader()` test helper — confirmed by grep
(`SurveyDefinitionReader(`/`EGERIA_PLATFORM_URL`/the real platform URL/view
server name do not appear in any of `test_step_preconditions.py`,
`test_survey_execution_plan.py`, or `test_survey_definition_executor.py`
outside mock construction). The three live-verification runs described
earlier in this doc (`laz_local_adventureworks`) were ad hoc `python3 -c`
scripts run directly, never part of the pytest suite CI runs.

Full suite (`uv run pytest tests/ -rf`), with both fixes applied: **6665
passed, 103 skipped, 0 failed** in 828.38s — clean, including the trio.

## Files touched

- `resource_explorer/surveyors/step_preconditions.py` — freshness window,
  `fresh_hit()`, `Precondition.freshness()`, `slug_column` on `Precondition`.
- `resource_explorer/surveyors/survey_execution_plan.py` — `registry`/
  `entity` on `build_plan`/`_add_produces_edges`; `satisfied_by_stored` on
  `PlannedStep` and `serialise()`; `MissingPrerequisiteError`'s new
  attributes; `authoring_gaps()`.
- `resource_explorer/surveyors/survey_definition_executor.py` —
  `_run_via_prefect` threads `registry`/`entity`/`surveyed_at`;
  `_propose_producer_definition()`.
- `resource_explorer/surveyors/repo_survey_definition_adapter.py` —
  `StepInfo.requires` property.
- `resource_explorer/prefect/flows.py` — `run_planned_step_task`/
  `re_survey_definition_flow` carry `satisfied_by_stored`/`surveyed_at`;
  `_step_info_for()`/`_record_step_cost()`.
- `tests/test_step_preconditions.py`, `tests/test_survey_execution_plan.py`,
  `tests/test_survey_definition_executor.py`,
  `tests/no_silent_success_baseline.json` — see Tests above.
