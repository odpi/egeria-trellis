# Coordinator brief — databases, filesystems, open data and models

**You are the coordinating session for the multi-resource plan.** Read this,
then `docs/multi-resource-questions-design.md` (the design and the decisions)
and `docs/egeria-support-for-multi-resource.md` (what Egeria and pyegeria
already provide, and the probes). `COORDINATOR-BRIEF.md` in this folder still
applies in full — worktrees, `*-IMPLEMENTED.md` replies, landing with
`--ff-only` — and this brief adds to it rather than replacing it.

Written 2026-09-20 by the session that wrote the two design documents. The
decisions in those documents are the project owner's rulings; do not reopen
them. Questions the documents leave open go to the project owner, not to a
guess.

---

## What is different from the repo work

- **DB and FS stay out of `/next`.** Project owner, 2026-09-20. They land in
  the classic UI (`index.html`, which already has DB and FS report views) and
  in the shared layers. `next/app.js` is not in play here, which removes the
  one-session-at-a-time rule for this plan.
- **The contention is `registry.py` and `facts.py` instead.** Both are large
  and shared. Each is owned by exactly one stream below, and nobody else
  edits them while that stream is open.
- **Egeria writes to the dev platform are ordinary.** The quickstart is the
  dev environment. Probes and materialiser tests may write there; use
  scratch-named elements and clean up.
- **Do not fix pyegeria in place.** Log in
  `/Users/dwolfson/localGit/egeria-python/PYEGERIA_ISSUES.md` and wait for
  the owner's approval. Two items are already identified
  (`egeria-support-for-multi-resource.md §8.1`, §4).

---

## Streams for the first two days

Run 1 to 4 in parallel. 5 onward waits for 2 and 3.

| # | Stream | Worktree / branch | Model | Owns | Must not touch | Depends on | Done-test |
|---|---|---|---|---|---|---|---|
| 1 | **Probes** — the ten in `egeria-support-for-multi-resource.md §9`, one script each under `packages/resource-explorer/scripts/probes/`, printing exists / works / fails with the response | `wt-probes` / `re/probes-multi-resource` | any, with the project owner present for Egeria | `scripts/probes/` | everything else | live Egeria | a `PROBES-2026-09-21.md` in this folder with one row per probe: result, response excerpt, what it decides |
| 2 | **Phase 0 plumbing, items 1–4** — reader keyed by resource type with an explicit "not authored for this type" state; generator reads every `*_analyses` section and emits one YAML key per type; `NON_PERSPECTIVE_COLUMNS` gains `Resource Types`; `Funnel Stage` vocabulary drops `Automate`; `ResourceTypeAdapter` gains `analysis_results_map` and `state_sources` and `FactLayer` dispatches through it; one `RESOURCE_TYPES` constant replacing the five re-declarations, with `dataset` and `model` included | `wt-plumbing` / `re/question-catalog-multi-type` | Sonnet | `surveyors/question_catalog_reader.py`, `question_catalog_writer.py`, `facts.py`, `scripts/csv_to_question_catalog_yaml.py`, `scripts/csv_to_dr_egeria_questions.py`, `configdata/question_catalog.yaml`, the five constant sites, `docs/dr-egeria/resource_questions_guide.md` | `registry.py`, the CSV's *content* (stream 4 owns rows; stream 2 owns columns and the one Automate row's stage) | nothing | repo CSV round-trips unchanged through the new generator; `test_a_clean_tree_regenerates_to_nothing` passes; `get_questions("database")` returns a "not authored" state, not `[]`; the §4 cross-type rows render on the classic Questions tab for `coco_ods` with honest envelopes |
| 3 | **Structured tables and the read-back materialiser** — `database_schemas`, `database_tables`, `database_columns`, `database_column_profiles`, `database_table_activity`, `database_grants`, `database_sql_objects`, `database_settings`, and the FS equivalents (`filesystem_entries`, `filesystem_data_files`), all keyed `(slug, surveyed_at, source)`; a materialiser that turns native annotations read back through `egeria_survey_reader.py` into the same rows with `source='egeria'`; a back-fill from existing `survey_data` blobs | `wt-tables` / `re/db-fs-structured-tables` | Opus | `registry.py` (DB/FS section only), `surveyors/egeria_survey_reader.py`, a new `surveyors/result_materializer.py`, migration | `facts.py`, question catalog files | nothing to start; the FS/DB column set should be checked against probe 9's dump before the PR merges | back-fill of the two live `database_surveys` rows populates the tables; `get_database_diff` (`web/routes/databases.py:655`) reads rows, not JSON; a native survey report read back lands as rows with `source='egeria'` |
| 4 | **Question authoring** — reword the eighteen cross-type rows per design §4; author the database rows per design §5.2–5.6 with `Answering Analysis` written to the guide's conventions (`GAP:` where no analysis exists yet) and `Resource Types` filled; Perspectives and Purposes tagged | `wt-questions` / `re/db-questions-csv` | Opus, or the design session | `docs/dr-egeria/resource_questions.csv` rows only | the generator, the YAML (regenerated by stream 2's tool when both land) | validates against stream 2 | every new row parses to the right column count; every `Answering Analysis` resolves to a known `kind`; no `unknown` kinds |
| 5 | **Designer round 1** — send `SPEC-MULTI-RESOURCE-REPRESENTATIONS.md` (this folder); ask for a review reply, not drawings | — | designer session | — | — | nothing | a `REVIEW-*` or `REPLY-*` from the designer in this folder |

**Merge order:** 2, then 3, then regenerate the YAML from 4 on top of both
and merge 4. 1 merges whenever it is done; it touches nothing shared.

---

## Phase 1, after streams 2 and 3 land

In this order; each is one worktree, one PR, one `*-IMPLEMENTED.md`.

| # | Slice | Model | Gate |
|---|---|---|---|
| 6 | Re-catalogue `coco_ods` with a reachable connection; run native `survey-postgres-database`; **dump every annotation type and metric key** into `PROBES-*.md` (this is probe 9, but it is also Phase 1 step 1 — do it once, properly) | any, owner present | none |
| 7 | Extend `postgres_schema_and_stats` to read `pg_stats`, `pg_stat_user_tables` tuple counters, indexes; add the engine capability declaration on `DatabaseConnection` (design §5.1) | Sonnet | 3 |
| 8 | `postgres_operations` step: privileges, activity signals, resilience, external dependencies (design §5.5, §5.7) | Sonnet | 3, 7 |
| 9 | `db_derived` zero-fetch step: classification, grain, fingerprint, conventions checks, change rates, proposed scope as a measured annotation (design §5.3, §7 of the support doc for key names) | Opus | 3, 7 |
| 10 | `postgres_column_profile` with sampling config (design §5.8), `data_class_match`, `reference_data_match`, RFA proposal convention (support doc §3) | Opus | probes 4, 5; 7 |
| 11 | `postgres_nested_columns` | Sonnet | 10 (shares the inference core) |
| 12 | Database survey definitions: `generate_database_survey_definition.py` cloned from the repo generator; `scouting`, `analysis`, `assessment` documents; publish through Dr.Egeria | Sonnet | 2, 4 — **built (#303, 2026-09-26)**, see `docs/design-notes/SLICE-12-DATABASE-SURVEY-DEFINITIONS-IMPLEMENTED.md` |
| 13 | Reachability probe (`finalAnalysisStep=CHECK_ASSET` via `initiate_gov_action_type` directly), `resource_reachability` table, launcher sentence in the classic UI | Sonnet | probe 7 — **built 2026-09-22, filesystem-scoped**, see `docs/design-notes/RESOURCE-REACHABILITY-IMPLEMENTED.md` (this slice was deferred 2026-09-21, reversed by the project owner 2026-09-22) |
| 14 | Database change comparators on the local delivery path (design §9.1) | Sonnet | 3, 9 |
| 15 | Designer round 2 — real drawings against the rows from 6 and 7 | designer | 6, 7 |

Rows 6–15 above are the plan as of 2026-09-20. Most have landed (see the
`*-IMPLEMENTED.md` files in this folder); the sequence below supersedes
their ordering for whatever remains.

## Phase 1b — after the #285 revert: honest states, scope, then the pane (2026-09-25)

The live test of `coco_pharma` (`REVIEW-SURVEY-PANE-285.md`) and the two
design sections it produced (`multi-resource-questions-design.md` §18
*Scope, focus and clusters* and §19 *Two entry paths*) change the order.
**#285 is reverted first** (project owner, 2026-09-25). Then, in this
order — each one worktree, one PR, one `*-IMPLEMENTED.md`, and **each gated
by a live, signed-in run against `coco_pharma` written into that document
as a sentence per screen** (the rule in the review §5; unit tests gate
logic, not whether a person can use the screen):

| # | Slice | Design | Model | Gate |
|---|---|---|---|---|
| 16 | **Honest absence.** An analysis with no results reader renders "ran; no summary reader yet" with the ✓ withheld — never "ran and found nothing — a measured zero". Delete the generic key-walking summariser ("5 rankeds", "56 per tables"); the result line comes from existing readers only. Fix the chat evidence footer to render only lists that exist, and add the stored capability probe to `context_compile`'s evidence | review §6.1, §6.2, §4 point 3 | Opus | on `coco_pharma`: no screen shows "found nothing" for an analysis without a reader; Credential Capability's line reads its real probe; chat answers "what can this credential see" |
| 17 | **Runnability from the catalog.** Delete both `DATABASE_ANALYSIS_STEP_MAP` copies (`database_surveyor.py:50`, `survey_definition_adapter.py:823`); derive analysis → steps from `analysis_catalog.yaml` and the step registry (`PRODUCES`, design §17.1); the Questions tab shows Run for every analysis that answers a question; a ✓ requires the answer to address the question's level, not merely that a mapped analysis ran | review §4 point 5, §6 | Sonnet | every question with an answerable analysis shows Run; `subject_signals`, `coverage_signals`, `preliminary_fit` are runnable; "which schemas carry the data" is not ✓ until it names schemas |
| 18 | **Identity and read-back first.** Resolve a registration to existing Egeria assets at every containment level and adopt the GUIDs (never a second asset; reuse repairs partial finds); read back structure, survey reports, declarations and `maintained_by` into RE's store as `source='egeria'` before any local survey; provenance on every fact. **Pulled forward (#304, 2026-09-26):** `is_published`/linkage-staleness honesty for databases and filesystems (`egeria_linkage.describe_publish_status`) — a narrow slice of this row's own "read-back before trusting a cached GUID" principle, done early because a Slice 12 gate found `coco_pharma`'s header claiming "published to Egeria" for days after the linkage was recorded stale. The rest of this row (asset resolution at every containment level, survey-report/declaration read-back) is NOT done. Also **candidate for this row or slice 20** (not fixed, logged in `docs/Backlog.md`): `_store_results` writes a survey_data section only when THIS run's own steps collected it, generically — three incidents patched field-by-field so far (`row_count`/`size_bytes`; `operations`/`credential_capability`, #303) without the generic mechanism ever being built | design §19.3 rules 1–2, 5; §16.7 | Opus | registering `coco_pharma` again creates nothing new in Egeria; the Questions tab answers from Egeria's rows with "from Egeria (…)" provenance before any local run |
| 19 | **Scope model.** `sub_resources` rows per containment level from the inventory (engine-declared, `REPLY-SCHEMA-AS-SUB-RESOURCE.md` §5); `investigation_scope` and the focus crumb; the left-nav tree with counts, search and paging; focus behaves like being on a node's page | design §18.1, §18.5, §18.8 item 1 | Opus | the tree shows `coco_pharma`'s 8 schemas with table counts and credential visibility; clicking `coco_ods` sets the crumb and every stage page follows it |
| 20 | **Scoped analyses.** `scopes` on database analyses, a locator-set parameter, results keyed by scope (rule D key gains `scope`), `schema_scope.py` as the filter; convert `schema_inventory`, `row_count_snapshot`, `db_relationship_graph` first; rollups labelled as rollups and naming their parts. **Also candidate for this row** (see slice 18's own note, not fixed here): `_store_results`'s generic "write only the sections THIS run's steps collected" rule — a scope-keyed rewrite of survey storage is a natural place to also close this, rather than patching a fourth field-specific incident | design §18.4; `REPLY-SCHEMA-AS-SUB-RESOURCE.md` §1 | Opus | Relationship Graph on `coco_ods` shows its FK components, not "53 isolated tables"; the database answer names the schemas it rolled up |
| 21 | **Questions carry a level; answers carry a distribution.** `Level` column on the CSV; per-type `Answering Analysis` for cross-type rows (or the not-authored state until it exists — no repository prose on a database); the envelope gains `scope` and `shown_of`; table-level questions answer at schema level as a ranked distribution | design §18.3; review §6.3 | Opus, with the design session for the CSV | "how many rows" on `coco_ods` lists tables with counts, top 10 and "13 more"; "what is this resource" on a database shows no README or GitHub text |
| 22 | **The Schema Inventory result view in `/next`**, per schema, from the structured tables: schemas → tables → columns with types, keys, comments; "not measured" for bytes; the estimate stamp per row | review §4 point 4; design §5.1a | Sonnet | the owner can read `coco_pharma`'s tables and columns in `/next` with the credential banner above them |
| 23 | **Include/exclude and per-table policy.** Include/exclude name lists at registration using Egeria's names (`includeSchemaNames` …); sample strategy, bounds and schedule persisted per table; connector-maintained include/exclude read back so deliberate exclusions are not reported as gaps | design §18.5; §19.4 | Sonnet | registering with `excludeSchemaNames=[demo, demo_auth]` never lists them, and the probe's denominator says so |
| 24 | **Ranking and clusters.** Discovery ranks tables from activity counters, FK degree, query statistics where held, OpenLineage where present, and `DataScope`/`DataGrain` fit against the lens; `db_relationship_graph` components become cluster proposals; accept in Curate as `SubjectArea` / `Collection` / `DigitalProduct` | design §18.2, §18.6, §18.9 | Opus | Discovery on `coco_pharma` shows a ranked table list and proposes `coco_ods` as one cluster; accepting it creates a `Collection` in Egeria |
| 25 | **Coexisting with the integration daemon.** `maintained_by` on read-back; source-vs-catalog drift comparator and RFA; survey only the gaps (a fresh Egeria answer satisfies the prerequisite resolver); Egeria as a discovery source ("known to Egeria, never surveyed by RE") | design §19.4–19.6 | Opus | a table added to the source shows as "Egeria's catalog lags the source by 1 table" until the connector refreshes |
| 26 | **The unified pane, re-landed per focus**, per the corrected `REPLY-SURVEY-ANALYSES-PANE-USER-FACING-MODEL.md` (stage-scoped list, result line from real readers, engine as a secondary tag, honest empty state) | review §4 point 3 | Opus | Scouting and Discovery tabs list different surveys for `coco_ods`; each row's second line is a real result or "no summary reader yet"; nothing on the screen is an unnamed rollup |

Slices 16 and 17 unblock everything visible and go first, in that order.
18–21 are the scope model and may run as two parallel worktrees (18+19,
20+21) once 16 and 17 are on main. 22 and 23 are independent of each other.
24 and 25 follow 20. 26 is last and is not started until 16–22 have each
passed their gate.

**File ownership for this phase:** `registry.py` and `sub_resources` — slice
19; `schema_scope.py` and the analysis catalog `scopes` — slice 20; the
question CSV and generators — slice 21; `next/app.js` and `next/stages/*` —
one slice at a time, 22 then 26; `chat.js` — slice 16 only.

Phase 2 (filesystems) mirrors the database slices with the walk split first;
the design doc §13 has the list. Do not start it until Phase 1's done-test passes:
`coco_ods` answers "which columns conform to a Data Class?" and "how is it
changing?" from stored rows; the Egeria asset carries both a native and an
RE report with same-typed column annotations; a new column and a new PUBLIC
grant each raise an RFA.

---

## Rules that bite here specifically

- **Every commit `-s`, every `gh` call `--repo dwolfson/trellis`.** Bare `gh`
  resolves to the upstream remote and the PR numbers collide.
- **Merge on `test` green; do not wait for the Docker image builds.** Project
  owner's standing rule.
- **Before every commit, `list_sessions`** and message any running Trellis
  session that shares a file with you. Running means `isRunning: true`; a
  recent timestamp is not consent.
- **`uv sync --all-packages --extra dev`, never bare.**
- **Restart the web server after regenerating the question catalog**; the
  loader is `lru_cache`d and the new questions will not appear otherwise.
- **Open the thing the user opens.** For every slice the done-test is a
  screen or a `FactLayer.answer()` call, not a table row. Four extensions
  were "done" this month with nothing visible; the check that catches it is
  in `extending-resource-explorer.md`.
- **A user surface is not done until a person has used it.** For any PR
  that changes what a user sees, the `*-IMPLEMENTED.md` carries a sentence
  per screen saying what the implementer saw, signed in, on a real resource.
  1327 green tests shipped a pane nobody could use (`REVIEW-SURVEY-PANE-285.md`).
- **Absence is a result.** Missing schemas in a native Postgres survey mean
  the survey user lacks permission; `pg_stats` empty means `ANALYZE` never
  ran; an empty question list for a type means not authored. Each of those
  renders differently from "measured, and there was nothing". Run the
  `find-absence-as-answer` skill on every reducer before you call it done.

---

## What to write back

One `*-IMPLEMENTED.md` per slice, naming the design section it implements,
what was scoped out and why, and what could not be tested. Plus one
`PROBES-<date>.md` from stream 1. The design session that wrote this brief
may not exist when you finish; the reply documents are how the decisions
survive.
