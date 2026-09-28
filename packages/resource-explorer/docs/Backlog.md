# Resource Explorer — Backlog

**Purpose:** A running list of work items that are agreed as worth doing but are not yet scheduled into a phase of an active design document. When an item is picked up for real design/implementation, move its detail into (or link from) the relevant design doc and leave a one-line pointer here.

This is a list, not a design doc — keep entries short. Link to a full design doc/section when one exists.

**Egeria/pyegeria bugs (as opposed to RE's own bugs)** are tracked in `egeria-python`'s `PYEGERIA_ISSUES.md` — the canonical tracker, unified `ISSUE-#` numbering — not here. RE's own `docs/egeria-python's PYEGERIA_ISSUES.md` is superseded and frozen at 6 entries; it is kept for history only.

**Current-state map (2026-08-19):** `docs/survey-model.md` maps how surveys, analysis and curation work — the axes on which the two survey-launch paths diverge, an inventory of which analyses reach Egeria and which don't, and a suspected bug (filesystem annotations never publish). **It was derived from the pre-migration standalone repo and carries a staleness warning — line numbers need re-checking, and it predates `run_batch` in the executor.** Several items below are corrected there. Related: `docs/architecture-recovery.md` (deriving Solution Blueprints from repos).

---

## A per-card database analysis run clobbers every OTHER table's row_count/size_bytes

**Found while gating** the enumeration-floor + collector-honesty PR
(2026-09-26), while re-verifying the never-analyzed row-count fix live
against `coco_pharma` after a stale-8811 report from the owner.

`database_surveyor.py`'s `_store_results()` enriches every table in
`schema_info["schemas"]` with `row_count`/`size_bytes` from
`results["statistics"]["row_stats"]`/`["table_stats"]` — but it does this
**unconditionally, for every table, on every survey run**, even when the
run's own requested steps never fetched `"statistics"` at all.
`DATABASE_SURVEYOR_STEP_MAP` gives `schema_inventory` steps
`["schema", "views"]` and `db_activity_signals` steps
`["schema", "operations"]` — neither includes `"statistics"` — so when
either runs, `statistics` is `{}`, `row_lookup` is empty for every table,
and every non-catalog-fallback table falls to `_store_results`'s bare
`else: table["row_count"] = 0` branch, **overwriting whatever correct
value a PRIOR `row_count_snapshot` run had just stored** with a naive
zero.

Reproduced directly and repeatably against `coco_pharma`
(`localhost_docker_coco_pharma`): run `row_count_snapshot` alone →
`us_sales_forecast`/`eu_sales_forecast`/`consolidated_forecast` correctly
show `row_count: None` (never `ANALYZE`d, per the fix above). Run
`schema_inventory` right after, on the same database, no other change →
those same three tables flip to `row_count: 0`. Run `row_count_snapshot`
again → back to `None`. Fully order-dependent, not specific to this
never-analyzed case — the SAME clobbering would have produced a false
`0` even before that fix, for any table whose real row count had been
correctly captured by a `row_count_snapshot` run and was then overwritten
by literally any OTHER per-card analysis's run.

This means "How big is this database" can silently go stale or wrong the
moment ANY other database analysis is re-run afterward — a real, and
currently invisible, source of the exact "measured zero" class of bug this
whole effort has been about, one level up (at the SURVEY level, not the
reader level).

**Candidate fix:** `_store_results` should not touch `row_count`/
`size_bytes` for a table at all when this run's own `statistics` step
didn't run — leave the table's PREVIOUSLY stored value in place (read it
back rather than defaulting to `0`), or record it as `not this run's
concern` rather than silently asserting a fresh zero. Needs its own PR;
not attempted here — this PR's scope was the enumeration floor and
collector-level `_errors`, not per-card survey write semantics, and a
correct fix needs to reconcile with however `record_database_survey`
already merges (or doesn't) successive local survey rows for the same
database.

## `get_statistics()` is consumed by row/size enrichment, not by any analysis reader — and its own row-count field had a real absence-as-zero bug

**Found while building** the enumeration-floor + collector-honesty PR
(2026-09-26), while inventorying `connection.py`'s collectors to decide
which needed the `_errors` honesty floor.

**Correction to this entry's own first draft:** it originally claimed
`get_statistics()`'s output "feeds nothing." That was wrong — re-checked
after a live gate found a real bug in exactly this path (see below).
`database_surveyor.py`'s `_store_results()` reads
`statistics["row_stats"]`/`["table_stats"]` directly to enrich EVERY
table's `row_count`/`size_bytes` before storage — a real, load-bearing
consumer, not a ride-along. What's still true: no entry in
`DATABASE_ANALYSIS_RESULTS_MAP` reads the `statistics` key as an ANSWER to
a user-facing question in its own right (distinct from feeding another
analysis's fields) — `postgres_column_profile`'s use, per
`survey_definition_adapter.py` ~line 443, is genuinely a ride-along for
sampling provenance, on top of the enrichment role. Whether a dedicated
reader is still worth adding is unchanged from the original question, just
not for the reason first stated.

**The real bug this path had, found via the same PR's live gate
(`coco_pharma`, 2026-09-26):** `_get_table_row_stats()` (one of
`get_statistics()`'s calls) read `n_live_tup` from `pg_stat_user_tables`
as `row_count` unconditionally. `n_live_tup` is maintained by incremental
DML tracking, not only by `ANALYZE` — but a plain SQL dump/restore carries
table DATA, not `pg_stat_user_tables`'s runtime counters, so a
freshly-restored table reads `n_live_tup = 0` indistinguishably from one
that is genuinely empty. Live: two entire schemas' tables (`orders`,
`customers`, `order_details`, ...) showed `n_live_tup = 0` with
`last_analyze`/`last_autoanalyze` both `NULL`, and "How big is this
database" reported 0 total rows across 53+ "measured" tables — where a
prior reading (before this bug fired on a fresh local re-survey) showed
3,526 real rows across 7 tables. **Fixed** in this PR:
`_get_table_row_stats()` now returns `row_count: None` (not `0`) when
`n_live_tup` is zero AND no `ANALYZE` has ever run — a genuine nonzero
count is still trusted regardless of `ANALYZE` history, since DML
tracking alone would have produced it. See
`docs/design-notes/ENUMERATION-FLOOR-AND-COLLECTOR-HONESTY-IMPLEMENTED.md`
for the full trace and `tests/test_table_row_stats_never_analyzed.py` for
coverage.

**Still open, not fixed here:** (a) whether to wire a dedicated reader for
`get_statistics()`'s remaining fields (`column_stats`/`index_stats`/
`table_activity` beyond what `row_stats` already feeds) as their own
analysis, given `row_count_snapshot`'s catalog description already claims
some of this ground (`pg_stats` profiling, tuple counters) and there may
be real duplicate-fetch waste between the two — or delete what's
genuinely unused; (b) the same "state says HOW discovered, not whether
the VALUE is exact" ambiguity noticed while fixing the bug above:
`_schema_inventory_results`'s `row_count_is_estimate` is `True` whenever a
table's SCHEMA-level discovery went through `_catalog_only_fallback`
(`state == STATE_CATALOG_ESTIMATE`), even when its `row_count` came from a
perfectly real, `ANALYZE`d `pg_stat_user_tables` row (confirmed live: 7 of
`coco_pharma`'s tables have exactly this shape) — "estimate" there means
"undiscoverable via `information_schema`," not "the number itself is
approximate," and the headline wording doesn't currently distinguish the
two.

---

## A new annotation class is unguarded by `test_annotation_check_names.py` until someone remembers it

**Found while building** `db_derived` (Phase 1 slice 9,
`DB-DERIVED-STEP-IMPLEMENTED.md`).

`tests/test_annotation_check_names.py` gates every annotation site in
`surveyors/` on naming its `check_name`, and gates shared check names on being
declared mutually exclusive. Both walk the AST looking for calls whose function
name is in a hand-maintained set, `ANNOTATION_CTORS`. A new annotation class is
absent from that set by default, so **every check in the file silently skips
its call sites, and nothing reports that they are unchecked** — the guard is
green because it never looked.

Live when found: `ResourcePhysicalStatusAnnotation` (added by slice 8) was
unguarded, and so were slice 9's `DataGrainAnnotation` and
`FingerprintAnnotation`. All three were added to the set in slice 9's branch,
and doing so immediately surfaced a real undeclared shared check name
(`db_fingerprint`) that had been invisible — which is the evidence the gap
matters rather than being theoretical.

Two smaller holes in the same file, found the same way:

- The shared-check-name check only reads `ast.Constant` keyword values, so a
  site written `check_name=SOME_CONSTANT` is skipped. Slice 9's
  `proposed_data_scope` sites were changed to spell the literal specifically so
  the guard could see them, with a test pinning the literal against the
  constant — satisfying the guard rather than dodging it, but the next author
  has no way to know that is expected.
- `DEFERRED` still excludes `database/database_surveyor.py`, on a 2026-09-02
  note that DB/FS surveying is deferred "until the repo path is finished". Two
  slices of database work have landed in that file since.

**Candidate fix:** derive `ANNOTATION_CTORS` from `survey_report`'s own
`Annotation` subclasses (or from `ANNOTATION_TYPES_REGISTRY`) instead of a hand
list, so a new class is guarded by construction and the failure mode becomes "a
new class breaks the build until declared" rather than "a new class is silently
exempt". Re-examine `DEFERRED` at the same time.

---

## `min_value`/`max_value` are written only by the native survey path, so a local-only survey has no exact date range

**Found while building** `db_derived`'s proposed `DataScope` (Phase 1 slice 9).

`database_column_profiles` has `min_value`/`max_value` columns, and design
§5.4's "what is the data's scope in time" row depends on them. The only writer
is `result_materializer.py`'s native read-back path (from an Egeria column-
values annotation's `range_from`/`range_to`). Slice 7's local `pg_stats` path
does not populate them — `pg_stats` has no min/max column, and the step does
not read values.

So on a database RE has only surveyed locally, the exact coverage range is
unavailable. Slice 9 falls back to the first and last elements of the stored
`histogram_bounds_json`, which are Postgres's own ANALYZE-time estimates of the
extremes, and labels the proposal's `basis` as `histogram_bounds` with reduced
confidence accordingly. That is honest but weaker than it needs to be.

**Candidate fixes**, either of which would make the local path produce an exact
range: have `postgres_schema_and_stats` derive `min_value`/`max_value` from the
histogram bounds at write time (no extra query, same estimate, but stored once
rather than re-derived by every consumer); or give it a bounded
`SELECT min(col), max(col)` per date column, which is exact but is a real query
against the data and so a scope-of-fetch decision, not a free one.

---

## `FingerprintAnnotation`'s native Egeria field names are unverified

**Found while building** `db_derived`'s `db_fingerprint` check (Phase 1 slice 9).

`docs/egeria-integration.md` §2 confirms `FingerprintAnnotationProperties`
exists and quotes its description; probe 5 (`PROBES-2026-09-21.md`) confirmed
`create_annotation` *accepts* the type. Neither establishes what fields it
carries, and no Egeria Java source checkout is available on this machine to
read them from (`grep` for the class over `~/localGit` finds no `.java` at all).

Slice 9 therefore publishes the fingerprint payload (digest, algorithm, table
and column counts, closest match, similarity) through `additionalProperties` —
the same fallback slice 8 used for `ResourcePhysicalStatusAnnotation` — rather
than guessing typed field names that would land nowhere. `DataGrainAnnotation`
needed no such fallback: its field names are quoted from the Java source in
`egeria-support-for-multi-resource.md` §3, so it publishes as typed fields.

**Candidate fix:** one probe — read `FingerprintAnnotationProperties` (and
`ResourcePhysicalStatusAnnotationProperties`, same question) from the Egeria
source or a live type query, and move both payloads onto their real fields.

---

## Does any ordinary read path surface `contentStatus`?

**Raised by** `egeria-support-for-multi-resource.md` §3's own closing paragraph;
**now live**, because `db_derived` (Phase 1 slice 9) is the first RE analysis
that actually publishes `contentStatus: DRAFT` — on its proposed table grains
and its proposed `DataScope`.

The project owner's 2026-09-21 decision made `contentStatus: DRAFT` the
mechanism for proposing a governance element that does not exist yet, and the
corrected probe 4 showed it round-trips. What is *not* established is whether
anything a consumer normally reads distinguishes a DRAFT annotation from a
confirmed one: a `contentStatus: DRAFT` element keeps `ElementStatus: ACTIVE`,
so it is not filtered out, and a naive reader sees the same element either way.

Until that is checked, a proposal RE publishes may render exactly like a
finding — which is the "absence rendered as a result" failure in a new place.
**Candidate fix:** probe whether `find_metadata_elements`/RE's own annotation
rendering surface the field at all, then make RE's own annotation views show it
before anything leans on DRAFT as the primary proposal UX (§3 flags this as
worth doing before slice 10).

---

## `db_hub_tables` is most of the way there for free

**Noticed while building** `db_derived`'s relationship graph (Phase 1 slice 9).

Design §5.3 lists `db_hub_tables` ("which tables would a consumer start with?
FK in-degree, rows, comments, read activity") as its own analysis; it is not in
slice 9's scope and was not built. But `db_relationship_graph` already computes
FK in-degree per table and returns the top ten as `most_referenced`, and the
other three inputs (row counts, comments, read activity) are all in the same
stored rows this step already reads. A later slice could add it as a seventh
zero-fetch check for very little work rather than as a new step.

---

## Change rates difference two snapshots, and nothing charts the series

**Found while building** `db_derived`'s `db_change_rates` (Phase 1 slice 9).

Two limits, both deliberate and both worth revisiting:

- It differences the two most recent snapshots that carry activity rows, not a
  longer series. Design §5.4 wants "per-table series → Understanding charts".
  The series exists — `database_table_activity` rows across their several
  `surveyed_at` values are it, which is why no new structured table was added —
  but nothing walks more than two of them, and a trend over five surveys is a
  different (and more useful) shape than a delta over the last two.
- **Nothing renders it.** No Understanding-tier chart reads these rows. The
  annotation carries the per-table payload and the classic DB view will show
  the annotations, but the chart design §5.4 asks for is not built, so the
  "per-table series" claim is currently satisfied by the data being reachable
  rather than by anything a user sees.

---

## No structured table for index usage, and no `ResourceProfile` annotation type

**Found while building** `postgres_schema_and_stats`'s pg_stats/tuple-counter/
index extension (Phase 1 slice 7, `DB-SCHEMA-AND-STATS-EXTENSION-IMPLEMENTED.md`).

Design §5.1 lists `pg_stat_user_indexes`/`pg_index` (index usage, unused-index
detection) as part of this step's catalog sources, and §5.7 says the step
produces "SchemaAnalysis, ResourceMeasure … ResourceProfile (frequent
values)". Neither has a home in the current data model:

- The ten structured tables `re/db-fs-structured-tables` built
  (`database_schemas`, `database_tables`, `database_columns`,
  `database_column_profiles`, `database_table_activity`, `database_grants`,
  `database_sql_objects`, `database_settings`, plus the two filesystem
  tables) have no `database_indexes` table. Index findings this slice
  produces are annotations only (`ResourceMeasureAnnotation` per index,
  `RequestForActionAnnotation` for an unused non-PK index) — not queryable
  rows, so a UI wanting "list of unused indexes across all databases" has
  nowhere to query.
- `AnnotationType` (`surveyors/survey_report.py`) has no `RESOURCE_PROFILE`
  member. Only Egeria's own native survey has a distinct "frequent values"
  annotation shape (`ANN_COLUMN_VALUES` in `result_materializer.py`); a local
  publish folds frequent values into `ResourceMeasureAnnotation.
  resource_properties` instead.

Both were left alone rather than added speculatively — slice 7's brief was
explicit about not inventing a new table, and adding a new `AnnotationType`
member is a catalog-and-publisher-wide decision, not a one-step scope
extension. Worth a project-owner decision before either is picked up: does
index usage get its own structured table (parity with the other nine), and
is `ResourceProfile` worth adding as a distinct annotation type or does
folding frequent values into `ResourceMeasureAnnotation` stay the pattern.

---

## Classic panel: primary component pick is still `latest`, not best-evidenced

`RULING-CLASSIC-AND-NEXT.md` §3 (2026-09-17) also called for the classic panel's
*primary* component reading — the `type`/`confidence` `_archRow` shows before any
"also proposed by" clause — to become the best-evidenced proposal (agreed first,
then highest confidence), not `repo_survey_definition_adapter.py:2951`'s
`max(comp_rows, key=lambda r: r["surveyed_at"])`. The 2026-09-20 pass
(`HONEST-CLASSIC-ROW-IMPLEMENTED.md`) shipped the honesty clause naming the other
current proposer(s) but deliberately left `latest` as the server-side pick —
changing it touches the shared `_architecture_recovery_results` payload every
caller of `components[]` reads (both classic and `/next`), which is a wider
blast radius than a one-clause client-side addition, and `/next` already has its
own best-evidenced logic (the branch tree's `agreement`/`proposals` sort) so
nothing was left un-honest by deferring this. Still open: swap `latest`'s
selection rule to agreement-first-then-confidence, consistent with what `/next`
already does, and update the one component-level test that pins `latest`'s
current selection if one exists.

## Path B3 — repo `executes_at: egeria` handler built (2026-09-20)

**Decision (project owner, 2026-09-20):** build the repo-side `executes_at: "egeria"`
plumbing now, even though no live repo survey action service may exist in Egeria
yet ("we will probably have some surveys that execute there at some point") —
close `docs/design-notes/PLAN-EXECUTION-MODES-VERIFICATION.md` §1 Path B /
item 8 (Path B3) rather than waiting for Egeria's side to be ready first.

Built: `EgeriaPublisher.trigger_survey_by_guid` (+ `_initiate_survey`/
`_find_survey_process_name`, `resource_explorer/surveyors/egeria_publisher.py`)
mirrors `EgeriaDatabaseSurveyor`'s dynamic-discovery mechanism — generic over a
technology-type string — rather than `EgeriaFileSystemSurveyor`'s hardcoded-
qualifiedName shortcut, since no confirmed-live repo survey process exists to
hardcode against. `repo_survey_definition_adapter._trigger_egeria_native_survey`
registers as `other_engine_handlers={"egeria": ...}` on the repo `_ADAPTER`,
following the database/filesystem handlers' contract exactly (requires a
stored `Project.egeria_asset_guid`, reuses the shared
`egeria_async_survey_result.poll_trigger_and_retrieve_annotations` poll/
resolve/attribute/convert machinery unmodified — nothing resource-type-
specific was found in it).

Repos have no registered Egeria Technology Type (cataloged as a plain generic
`Asset`, not a typed one) — `"GitHub Repository"` is used as the discovery key,
since it's the one repo-specific string this codebase already sends to Egeria
(`additionalProperties.deployed_implementation_type` in
`EgeriaPublisher._find_or_create_asset`).

**Expected, honest current state:** with no live repo survey action service
registered in Egeria, and no `(entity_type="repo", "GitHub Repository")` entry
in `configdata/technology_type_processes.yaml`, a repo Survey Definition step
tagged `executes_at: "egeria"` now reaches a specific, clear RuntimeError
("No native survey process configured for technology_type='GitHub Repository'
(entity_type='repo')") instead of the old `not_executed_no_egeria_handler`
skip — reported through the executor's normal per-step error path, not a
crash. This is correct and expected, not a regression to fix; it self-resolves
the moment a real repo survey action service is authored in Egeria and either
registered as a discoverable user Survey Definition or added to
`technology_type_processes.yaml` — no further RE code change needed.

Tests: `tests/test_repo_egeria_native_survey_handler.py` (uncataloged raise,
happy-path trigger+poll+resolve, no-matching-process error, both direct and
through the full executor). `tests/test_execution_modes_path_b1_failure_modes.py`'s
`TestUnregisteredEngineHandlerYieldsNotExecuted` — which had pinned "repos have
no egeria handler at all" as its live example — is updated to exercise that
generic failure mode against a synthetic adapter instead, since it's no longer
true of repos.

## Cataloguing in layers — layer 1 evidence and consumed-Egeria-interfaces built

**Decision (project owner, 2026-09-14):** catalogue in layers — coarse top-level
components first (for egeria-trellis: RE, EA, Trellis core), a finer layer only
when someone asks to understand one of those more, and expose the interfaces a
repository provides and consumes rather than proposing every Python distribution
as a `SoftwareLibrary` resource manager (a type error: that classification names
a thing that *manages* libraries, e.g. PyPI, not a library itself).

Two Discovery-tier, zero-fetch analyses now exist as the evidence layer 1 and the
interfaces-consumed catalogue rest on:

- **`deployment_evidence`** (`repo_deployment_evidence`) — per declared
  distribution, whether deployment evidence exists (console entry point,
  `__main__.py`, unambiguous Dockerfile/compose/Helm, web-framework dependency)
  and the verdict it supports: `application` / `library` / `unknown`. See
  `resource_explorer/surveyors/deployment_evidence.py`.
- **`egeria_interfaces`** (`repo_egeria_interfaces`) — which Egeria view services
  a repository consumes, derived from pyegeria client-class name matches against
  code-symbol signature/return-type/base-class text (a lower bound — no import
  table exists anywhere in this codebase), mapped via the curated
  `configdata/egeria_view_services.yaml`, plus Dr.Egeria doc-path and
  best-effort command-family evidence. See
  `resource_explorer/surveyors/egeria_interfaces.py`.

Both wired into `STEP_REGISTRY`/`ANALYSIS_KINDS` and the Repo Discovery Survey
(`docs/dr-egeria/repo_survey_types.csv`, regenerated
`repo-survey-definition-discovery.md`). **Not done**: the model corrections
this decision also named (`SoftwareLibrary`-per-package and
repository-as-`SourceControlLibrary` misreadings) and the platform
re-authoring of the regenerated Survey Definition docs — both separate,
coordinated steps.

---

## Provenance: stamp the producing run on results rows

**Argued as provenance, not as UI polish** — it is the same class of fact as
`surveyed_at` and the disposition trail, and it should not be costed as a
convenience feature, because it will lose that argument and the loop it closes
will stay open.

A measurement cannot currently name the run that produced it. `project_analysis_
findings` / `_metrics` carry `surveyed_at`; the runs queue (`/api/runs/`) carries
timings and state but no per-step detail; and the step detail that exists lives
in the activity log, keyed to neither. The only available correlation is
timestamp proximity.

**Timestamp correlation is exactly what must not be built here.** The two-clocks
defect (2026-09-10: a run registry saying 24 August while the metric rows for the
same analysis were written 10 September at 02:57) is the demonstration — the two
clocks disagree by weeks, and a link inferred from them would be a guess wearing
a link's clothing. A UI that offers "open the run that produced this value" is
claiming causation the data cannot support.

**What it unlocks**, once a run id is on the row:

- a measurement opens the run that produced it, and the run names every step and
  what each found — the level that regressed when the old Survey pane was
  replaced by a definitions list;
- "what changed since the last run" becomes exact rather than inferred from a
  metric series;
- a failed step can say which previous measurement is still current and was not
  overwritten, which is currently only assertable in prose.

Until then `/next` offers "Runs on this resource" — honestly a list, with no
claimed link to the value it was opened from.

---

## Next up — priorities as of 2026-08-26

Marked at the end of the architecture-recovery thread. Findings 96–119 in
`scripts/arch-spike/README.md` are that thread's record; this is what is left and
what is deliberately not.

**1. Phase 2 — Egeria projection of recovered architecture — re-scoped 2026-09-03, still the
largest unbuilt piece, but not for the reason previously written here.** "Nothing from
architecture recovery reaches Egeria" stopped being true on 2026-08-30 —
`arch_recovery/materializer.py`'s `ComponentMaterializer` writes a real `SolutionComponent` when
a curator accepts a proposed component. That path is deliberately narrow: accepted verdicts
only, a bare component with no blueprint or relationships, and no retraction if a verdict is
later reversed. So the *blueprint* projection is unbuilt; a single-component projection is not.

**Both stated prerequisites turned out to already be done — checked, not assumed.**
Outbox/retry publishing (was "design §8.4, still design-only") is real, built, and already
live: `egeria_outbox` table with claim/lease/release semantics, `egeria_outbox.py` (613 lines)
draining it, wired into `EgeriaPublisher._create_annotations` and evidence-link publishing today
— not a future dependency. Hierarchy-to-collapse (finding 117) was already correctly marked done
here. **A third thing this entry never listed is also done**: candidate-cluster proposal —
`arch_recovery/clustering.py` (740 lines, tested, wired into `persist.py`) already computes and
persists candidate blueprints as `"candidate_blueprint"` findings, one per cluster per
perspective, with the members/children/hierarchy structure. `ComponentMaterializer`'s own
docstring calls this "unbuilt" (line 19-20 of that file) — that comment is stale; the code it
describes exists.

**What's genuinely still missing, confirmed by reading, not by the stale comment**: nothing turns
an accepted `candidate_blueprint` finding into a real Egeria `SolutionBlueprint`.
`curate.py`'s `add_component_verdict` hardcodes single-component materialization only — no
blueprint-level verdict route exists. **The frontend does not render `candidate_blueprint`
findings at all** (zero occurrences in `index.html`) — a curator cannot see these proposals
today, let alone accept one. The real gap spans four layers: registry (blueprint verdict +
materialized-blueprint tracking), a `BlueprintMaterializer` (create `SolutionBlueprint`, attach
member components, wire relationships — through the outbox, since a blueprint writes far more
elements per run than anything published today), a route, and frontend UI that doesn't exist
yet. Scoped as its own plan rather than attempted improvised, given the size and that it writes
many-element structures to the shared live Egeria instance — see
`docs/blueprint-materialization-plan.md` (inside-out: registry/materializer/outbox/route) and
`docs/curate-evidence-based-decisions-plan.md` (outside-in: where a curator actually reaches this,
and the general "evidence + human judgment" shape blueprints are the first instance of — repos
also want Information Supply Chains and organization-certification tabs eventually, databases want
schema/class-determination; only blueprints/components are real today). The two plans' §D
reconciliation changes one concrete thing in the inside-out plan's Phase C (frontend placement
moves from Analysis to a new Curate tab) — read both before implementing, not just one, or ask
whoever picks this up next to re-derive current state rather than trust this paragraph's own age.

**Progress, 2026-09-03: Phases A, A.5, B, and now C are done.** Registry + `BlueprintMaterializer`
(`6a76e4b`); the live wire-safety measurement, confirmed `SolutionLinkingWire` is multi-link
(`86c214a`), with the project owner directly deciding to defer wire enqueueing to its own
follow-up rather than ship it with the confirmed risk (`7381782`); the blueprint verdict route
and member/child attachment via the outbox, scoped accordingly (`bce70ca`). **Phase C (frontend)
shipped at the reconciled placement** — a new `🏛 Architecture Verdicts` sub-tab in Curate
(`_curateSubnavHtml`, mirroring `_automateSubnavHtml`), not Analysis's panel: candidate-blueprint
accept/reject with oversized-cluster and unmaterialized-member warnings, plus the existing
per-component verdict controls relocated there from Analysis, which is now genuinely read-only
(new `_archInteractiveMode` module flag gates the accept/reject/change affordances). Backend gap
closed alongside it: `_architecture_recovery_results` now carries `data.blueprints` via a new
`_candidate_blueprints_results` reader (10 unit tests). Live-verified against `sqlglot` through
the actual UI, not just the API — see item 6 below for a real, pre-existing materialization bug
found doing that verification. Full suite: 3652 passed, 92 skipped, 0 failed. **What's left: the
deferred wire-enqueueing follow-up**, whenever that's picked up. Backlog item 5 (below) tracks the
related `AnnotationReview` measurement gap a peer session surfaced reviewing Phase A.5, also not
yet done.

**2. `security_features` should report `skipped_by_design` — DONE, already on `main`.** Picked up
2026-09-03 and found already fully shipped, in two commits from before this Backlog entry was
even opened: `a059e01` (2026-08-31, surveyor — emits a confidence-0 `skipped_by_design` annotation
with `gate="github_admin_only"` instead of nothing when GitHub withholds
`security_and_analysis`) and `f958f3d` (identity work, same surveyor). The results reader
(`_security_features_results` in `repo_survey_definition_adapter.py`) distinguishes all three real
causes of an empty card — `never_run` (no stats fetched), `skipped_by_design` with the reason (stats
exist, feature settings not visible), and a genuine empty findings list (visible, nothing enabled)
— and `index.html`'s `_renderEmptyResultState` renders `skipped_by_design` with neutral styling and
the stated reason, not as a failure. `tests/test_security_features_visibility.py` covers all three
causes but is `pytest.mark.corpus`-gated (one of its 7 tests touches the real shared registry) —
not re-run live this session per the coordinate-shared-writes convention; the code was read and
independently confirmed correct rather than re-verified against the shared corpus.

**3. The silent field-allowlist pattern — partially closed, 2026-09-03.** Finding 118's other two
named same-day instances checked and confirmed correct: `arch_recovery/persist.py`'s port/wire
`detail` dicts now pass `additionalProperties` through wholesale rather than naming keys, so
`operationCount` (the bug finding 118 named) reaches storage; every other port/wire field traced
to its source in `interfaces.py` and accounted for. `registry.py`'s four `_row_to_*` converters
looked like the same shape but aren't — their `known` set is `{f.name for f in
dataclasses.fields(X)}`, computed from the dataclass itself, so a new field can't silently fall
out of sync the way a hand-written tuple can.

**A new, real instance found and fixed, not one of the original three**:
`_foss_scorecard_results` (`repo_survey_definition_adapter.py`) read 3 of the 5 keys
`score()` (`foss_scorecard.py`) computes and persists — `checks_total` and
`comparable_to_openssf` lived only in `detail` and were never read back.
`checks_total` is exactly the denominator this reader's own docstring warns about ("8.0 over
five checks and 8.0 over twelve are different claims"), and `_foss_scorecard_headline` was
rendering "N checks" with no way to tell which. Fixed to mirror `_cve_scan_results`'s existing
two-loop shape (metrics, then detail); `_foss_scorecard_headline` now says "N of M checks" when
they differ. New `tests/test_foss_scorecard_results_reader.py` adds the same superset-guard shape
finding 118 built for `_note` — calls the real `score()` and asserts every key it returns reaches
the reader, so a sixth key added later fails loudly instead of vanishing the same way.

**Update, 2026-09-03 — the remaining 31 readers are now all checked. DONE.** Every `_X_results`
function in `repo_survey_definition_adapter.py` diffed against its writer's actual persisted
keys, one at a time — not the mechanical grep alone, since the shape varies too much for that to
generalize. 13 real, load-bearing drops found and fixed, across 5 commits:

- **`repo_classification.py` (`05466bb`)** — a writer bug, not a reader one: `confirmations`/
  `unexpected` are two of only three things this module's own docstring says it reports, computed
  and sent to Egeria's `additionalProperties`, but the finding-persistence loop only ever iterated
  `report.missing`/`report.found`. Nothing wrote them anywhere `query_findings` could see. Fixed
  by adding the two missing loops.
- **`contribution_provenance.py`, `sla_content.py` (`5fc8bbf`, allowlist fix `567770b`)** —
  another writer bug: `_gap_analysis_results`' own docstring says "each of the four [GAP analyses]
  writes" a `check_name="scan_summary"` row, and two of the four didn't on their normal,
  completed-run path (only their empty-inventory early return did) — so `_status` was silently
  `None` on every ordinary run, not an edge case.
- **`_gap_analysis_results` itself (`3c995c4`)** — the shared reader for all four GAP analyses
  stripped every finding to `check_name`/`label`/`summary`/`confidence`. `secret_scan.py`'s
  per-match `excerpt` (the actual matched text, needed to triage without re-opening the file) was
  computed, persisted, and permanently unreachable. Fixed with full `detail` passthrough,
  confirmed harmless for the other three kinds (their detail fields were independently checked
  redundant with `summary`/`label`).
- **Nine more in one pass (`79f06ab`)**: `_ci_quality_results` (`workflow_files`, keywords past
  3), `_interface_surface_results` (`file_count`, files/dependencies past 3 — this module's own
  docstring calls evidence strength "the entire point of the analysis"), `_cii_badge_results`
  (badge `url`/`project_id` — the only way to click through and verify — and `unmet_criteria`,
  previously only a bare count), `_repo_conventions_results` (full directory path for
  `security_policy_content` — basename only was shown, and root/`.github/`/`docs/` is a real
  distinction — plus truncated keyword/file lists), `_manifest_parse_results` (`manifests`/`error`,
  nested a level deeper than `result_status_from_detail` looks), `_website_ingestion_results`
  (`ingested_by`/`ingested_at` — a *second*, independent drop of the exact field finding 118
  already fixed once at the writer's own allowlist — plus `error`), `_architecture_interfaces_
  results` (`operation_count` — `persist.py`'s own neighboring comment names this exact bug
  class, and it recurred one layer up), `_architecture_recovery_results` (`identity`, the
  precedence-chain qualifier `ir.py` calls a "materially" different claim from confidence; and
  `blueprint`, the only surviving path to blueprint membership since the sibling
  `architecture_blueprints` finding kind has no reader anywhere in this file), `_data_profile_
  results` (`formats`, the only place a too-large-to-profile file is counted at all).

**Confirmed fine, checked not assumed** (dropped keys verified redundant with `summary`/`label`,
constant across every real construction site, or genuinely internal bookkeeping): `_file_
classification_results`, `_dependency_results`, `_refresh_plan_results`, `_security_summary_
results`, `_sub_resource_survey_results`, `_security_results`, `_documentation_results`, `_license_
results`, `_chaoss_metrics_results`, `_community_support_results`, `_security_features_results`,
`_telemetry_scan_results`, `_maturity_results`, `_api_structure_results`, `_symbol_extraction_
results`, `_rag_ingestion_results`, `_architecture_summary_results`, `_architecture_doc_lens_
results`, `_health_results`.

**Found, deliberately left open — genuine gaps, not silently folded in:**
- `secret_scan.py`'s own skip path (`_emit_skip`) writes its skip finding under
  `check_name="ruleset_available"`, not `"scan_summary"` — so `_gap_analysis_results`'s
  `scan_summary` lookup finds nothing on that specific path and `_status` is never attached, even
  though the finding list clearly shows `skipped_by_design`. Structurally different from the
  `contribution_provenance`/`sla_content` fix above (a *misnamed* row, not a *missing* one).
- `documentation.py`'s per-language `by_language` breakdown and `symbol_extraction.py`'s
  `files_scanned` known-positive signal — both computed, both currently unreachable from their
  results readers, both judged non-load-bearing (redundant or not referenced by any consumer) at
  the time of checking. Worth a second look if either becomes load-bearing later.

Verification: 15 new regression tests (`tests/test_field_allowlist_sweep_2026_09_03.py`, plus
`test_repo_classification_confirmations.py`, `test_gap_analysis_results_reader.py`, and additions
to `test_sla_content.py`/`test_contribution_provenance.py`), each seeded through a real registry
and confirming the previously-dropped field now survives end-to-end through the actual reader.
Full suite green after every commit, confirmed with a final clean run at the end: 3604 passed, 92
skipped (corpus/live-Egeria-gated), 0 failed — excluding the one test needing a currently-
unreachable local Egeria platform (unrelated to this sweep).

**4. Split `architecture-recovery.md` — DONE, 2026-09-03.** §5 *Extraction design* (913 of 2,720
lines) moved verbatim to `architecture-recovery-extraction-design.md`, keeping its original §5.x
numbering (not renumbered — every existing cross-reference still names the same subsection it
always did). `architecture-recovery.md` shrank to 1,832 lines and now carries a pointer stub
where §5 was. Every cross-reference that would otherwise have gone stale was found and
requalified with the new filename, not just the ones this entry originally predicted: 16 lines in
`architecture-recovery.md` itself (some hand-written, not the mechanical first-token pass, where
a line mixed a moving §5.x reference with a staying §6.x one), 17 in `Backlog.md`, and one
verbatim-quoted title in `consolidation-2026-08-24.md`. The four other RE docs flagged as
candidates (`investigation-framing-design.md`, `feedback-and-curation.md`,
`question-answering-and-context.md`, plus `consolidation-2026-08-24.md`'s own non-quoted `§5`s)
turned out to reference their **own** local §5 heading, not architecture-recovery's — checked
each doc's own heading list before touching anything, left all four alone. Dr.Egeria
survey-definition/outbox files and `archive/` were out of scope by the existing exclusion
convention.

**5. `AnnotationReview` is an unmeasured relationship type queued for the outbox's create-blind
path — raised by a peer session, 2026-09-03, reviewing the Phase A.5 `SolutionLinkingWire`
measurement.** `egeria_outbox.py`'s own `_create_annotation_link` docstring already names it:
`AnnotationExtension` was measured live 2026-09-01 as UNI_LINK (safe to create-blind); the same
docstring says to re-measure before relying on that for *any other* relationship type, and names
`AnnotationReview` (Phase 3, not built) as one that has never been measured. Not fixed here —
nothing currently calls it, so there's no live bug — but it's the next candidate for exactly the
`scripts/arch-spike/measure_wire_multi_link.py` treatment (the return-value test, one call)
**before** anything in Phase 3 adds it to the outbox, not after.

**6. `BlueprintMaterializer.materialize_blueprint_element` failed Egeria's request-body
validation on a real accept — found 2026-09-03 live-verifying Phase C above against `sqlglot`
through the actual UI. FIXED same day — root cause was a pyegeria gap, not an Egeria-side
rejection.** The `VALIDATION_ERROR_1` ("Request body failed validation") never reached the
network: `SolutionArchitect.create_solution_blueprint`'s own docstring documents a
`NewSolutionElementRequestBody` body (with `initialStatus: "DRAFT"`) for Draft-status creation,
but that class doesn't exist as a real pydantic model in pyegeria — confirmed by direct
inspection of both the installed version here (5.3.4.23) and the canonical egeria-python
checkout (6.1.9-dev; `create_solution_component` carries the identical, separately-confirmed
gap). The client validates every create-blueprint body against a bare
`TypeAdapter(NewElementRequestBody)`, whose `class` field is `Literal["NewElementRequestBody"]`
— any other value is a local pydantic `ValidationError` before any HTTP call, which
`BlueprintMaterializationError`'s message wording made read like a server-side rejection.
Logged as egeria-python's `PYEGERIA_ISSUES.md` ISSUE-84 (not patched there directly, per this
repo's policy — [[feedback_pyegeria_gaps_tracking]]); `egeria-python-65` messaged to review.
Fixed here by matching `ComponentMaterializer`'s existing, working body shape — `class:
"NewElementRequestBody"`, no `initialStatus` — so materialized blueprints are ACTIVE, not Draft,
same pre-existing gap `ComponentMaterializer` already has (its own follow-up, not new). Two new
regression tests added, including one that runs the actual body through pyegeria's real
`TypeAdapter(NewElementRequestBody)` rather than only asserting the mock-call shape — the kind
of test that would have caught this before it reached a live accept.

**Follow-up, same day: full live materialization confirmed, and Draft-status achieved for real.**
The TLS-handshake timeout blocking live confirmation turned out to be `qs-metadata-store`'s
Postgres connection pool genuinely exhausted (`jdbcMaximumPoolSize` defaulting to 10, HikariPool
timeouts in the container logs) — not transient, and not caused by this work; fixed by
`egeria-workspaces-fs-14` bumping the pool to 50 and restarting `quickstart-egeria-main`
(coordinated first — see the session transcript for the cross-session exchange, including a
correction: an earlier "misattribution" claim about an unrelated wire-test coordination request
was wrong and retracted, the request was real but predated this session's own visible context
after a compaction). Once the platform came back: accepting the `.github` candidate blueprint
against `sqlglot` through the actual Curate UI's API materialized a real `SolutionBlueprint`
(`status: "partial"` — its two members correctly unmaterialized, per Decision 2's governance
boundary). Separately, `egeria-python-65` reviewed ISSUE-84 and found the real mechanism:
`contentStatus` is a plain field on `ReferenceableProperties`, settable inside `properties` on
the existing `NewElementRequestBody` — no separate request-body class was ever needed
(odpi/egeria-python#337). Confirmed live, twice, each independently read back by guid (not just
trusted from the create call's own return): a standalone probe (guid `1c6550a9-77cd-49ac-a791-
ffb9fa56f3fc`, superseded by a second probe `8755a5ac-7941-4880-a735-72010d779a73` to also check
the properties-block shape, both deleted after) confirmed `properties.contentStatus == "DRAFT"`
via `get_solution_blueprint_by_guid`; then a real accept through the actual Curate UI's own API
endpoint (`POST /api/curate/blueprint-verdicts/repo/sqlglot`, not a direct library call)
materialized guid `809025b5-cca9-4e9a-a2f7-3a5104138f67`, independently re-queried and confirmed
the same. Both checks: `properties.contentStatus` round-trips as `"DRAFT"` correctly;
`elementHeader.status` stays `"ACTIVE"` (a different, unrelated instance-status axis).
`BlueprintMaterializer` now sends `contentStatus: "DRAFT"` — architecture-recovery.md §10 Phase
2's "All at ContentStatus = Draft" is achieved for blueprints. One new regression test
(`test_content_status_draft`), 21/21 passing in `test_blueprint_materializer.py`.

**GUID note (2026-09-04):** the platform was redeployed with a full repository-store wipe the
same day this was verified. The three GUIDs above (`1c6550a9-...`, `8755a5ac-...`,
`809025b5-...`) are historical — valid as of the verification date, not resolvable against the
platform after the wipe. The finding itself (`contentStatus` round-trips correctly via
`properties`, no separate request-body class needed) is a property of the API/pyegeria fix, not
of that instance, and stands unaffected.

**`ComponentMaterializer`'s identical gap fixed the same day, same fix.** `materializer.py`'s
`materialize()` now sends `contentStatus: "DRAFT"` too — same field, same mechanism, no separate
investigation needed since the pyegeria fix (odpi/egeria-python#337) already covers both element
kinds. Live-verified through the real Curate UI accept endpoint (`POST
/api/curate/component-verdicts/repo/sqlglot`, `scope_locator: "sqlglotc"`), materialized guid
`08a5ab3d-7862-43a8-a6bc-b5edbc768215`, independently re-queried and confirmed
`properties.contentStatus == "DRAFT"`. One new regression test (`test_content_status_draft`),
`test_component_materializer.py`.

**Also found and fixed in the same window, unrelated to Egeria**: a genuinely stale
`resource-explorer web` process (pid 74923/74921, running 20+ hours, holding no listening port —
invisible to `lsof -i :8810`/curl checks since only the current process had the port) — flagged
by a peer (`dwolfson-be`), confirmed via `ps`, force-killed after a plain `kill` failed to take.
Its background scheduler thread does start independently of the HTTP bind (confirmed by reading
`web/app.py`'s `_lifespan`), so this was briefly a real second outbox drainer — no damage found,
since every kind currently enqueued is convergent under double-processing and wires (the one
non-convergent kind) are deferred out of the outbox entirely.

**7. `benchmarks`-style directories are proposed as unclassified components — no naming-
convention type/exclusion pass exists — found 2026-09-03, reviewing Phase C's Curate UI against
`sqlglot`, deferred by explicit project-owner choice ("neither now").** `benchmarks` has only
coupling evidence (`connective-orchestrator`, 60%, dispersed fan-out) — no code marker fires, so
it's genuinely unclassified, not mistyped. Nothing in `code_markers.py`/the coupling classifier
maps a directory literally named `benchmarks`/`tests`/`examples`/`scripts` to a distinct type or
excludes it from being proposed as an architecture component at all — a real, reasonable gap,
larger than a quick fix (touches detector design: is a benchmark harness a component that
happens to be untyped, or a different KIND of thing that shouldn't be proposed as one?). Not
scoped further here.

**8. `SolutionArchitect` has no `create_solution_port` — real Egeria SolutionPort materialization
is blocked at the SERVER, confirmed live, not just unmeasured on the client side — found
2026-09-03, scoping the port/wire work named in the Curate redesign conversation.** Checked the
installed pyegeria and the canonical `egeria-python` checkout's `.http` ground truth first
(`Egeria-api-solution-architect.http`): `link_solution_component_port`/`link_solution_port_
delegation` (attach an *existing* port) and their detach counterparts exist; no `create_solution_
port` method and no `POST .../solution-ports` endpoint anywhere. `.http` files can mislead in
either direction though (a real precedent named by `egeria-python-65`: 3 relationship types
looked unbuilt from `.http` alone and turned out to have zero real live endpoint, but a 4th that
looked the same way did) — so this went to a live check once the platform came back up from the
redeploy: `GET /v3/api-docs` against the real `qs-view-server` (2.39MB spec), every path
containing "port" enumerated — **30 POST paths, all attach/detach, zero creation endpoints,
anywhere in the entire live spec** (not just solution-architect — checked a generic
process-ports attach under a different service too). Definitive: this is a confirmed Egeria
Server gap, not a client-side omission pyegeria could paper over. No `SolutionPortProperties`
model exists either, so the generic fallback (`MetadataExpert.create_metadata_element`,
`NewOpenMetadataElementRequestBody` — confirmed live to be a real, reachable route,
`/servers/{serverName}/api/open-metadata/{urlMarker}/metadata-elements`). Reported to
`egeria-python-65` for their tracker (`PYEGERIA_ISSUES.md` ISSUE-85, PR #340). Separate and NOT
blocked: `SolutionLinkingWire`'s OMVS-layer methods are already implemented and working in
pyegeria, exposed via Dr.Egeria's `Link Solution Components` command (not a missing "wire"
command as first thought — `egeria-python-65` corrected this after checking the actual
processor code).

**Correction, same day: `SolutionPortProperties` DOES exist** (this entry originally said it
didn't — an earlier search filtered out anything with "solution" in the name and never actually
checked). The live OpenAPI spec's `components/schemas` documents `SolutionPortProperties`
(`direction` enum field) even with no create endpoint — the server genuinely understands the
type, it just can't be created through a convenience wrapper. **Project-owner instruction,
2026-09-04: build a temporary workaround via the generic route now, using `SolutionPort` (the
right type — same `DesignModelElement` branch as `SolutionComponent`, confirmed via
`SolutionComponentPort`'s live typedef), not the classic `Port`/`PortImplementation`/`PortAlias`
family first suggested (ruled out: its attach relationship `ProcessPort` requires the owning end
to be typed `Process`, a different branch entirely — no way to attach a classic Port to a
`SolutionComponent`).** Built and live-verified end to end: `port_materializer.py`'s
`PortMaterializer`, mirroring `BlueprintMaterializer`'s shape exactly so it's a clean swap once
pyegeria ships a real `create_solution_port` — same idempotency-first structure, same
`{"status", "guid", "qualified_name"}` return contract. Live probe (create → attach via the real
`link_solution_component_port` → verify via `get_anchored_element_graph`'s `relationships` list
→ delete both) confirmed the whole path works, including one real wrong turn caught and fixed:
the `direction` enum's registered type is `SolutionPortDirection` with values
`UNKNOWN`/`INPUT`/`OUTPUT`/`INOUT`/`OUTIN`/`OTHER` — a first attempt guessed `typeName: "PortType"`
/ `symbolicName: "Input"` from the OpenAPI schema's toString-derived enum listing, which is
actually the *classic* `Port.portType` enum's shape, and failed `OMAG-COMMON-400-032`. Full trail
in memory note `reference_egeria_dedup_and_link_patterns` (including the
`get_metadata_element_by_guid` `properties`-is-always-`None` trap — real key is
`elementProperties.propertyValueMap`/`propertiesAsStrings` — positive-controlled against a real
seeded element before trusting it on the throwaway). New `architecture_materialized_ports`
registry table + accessor trio (mirrors `architecture_materialized_blueprints`'s shape, keyed by
(scope_locator, port_name) since one component can have more than one port). 22 new tests (17
materializer, 5 registry round-trip) — `MetadataExpert`-mocked, plus one that validates the real
create body against pyegeria's actual `NewOpenMetadataElementRequestBody` model. Not built in
this pass: any route/UI wiring, or the "which ports come from which component's deployment
evidence" mapping — this is the create capability only, matching the project owner's own framing
("if you need... try doing it").

**9. Minor, measured-not-fixed: `_candidate_blueprints_results` and `_architecture_recovery_
results` each build their own slug→scope_locator map independently** (the same duplication-by-
design pattern this codebase already uses elsewhere — `materializer.py`'s `_find_element_guid`
docstring gives the same "small helper duplicated once is safer than cross-module coupling"
reasoning). Measured against `egeria_workspaces_git` (109 components, 39 blueprints, its own
Backlog-recorded largest fixture): 0.544s for the full `_architecture_recovery_results` call,
including both walks. Not slow enough to be worth threading a shared map through two functions
that are each deliberately self-contained and independently testable — noted here as a measured
fact, not acted on, so a future session doesn't re-measure it from scratch if it comes up again.

**10. `survey_definition_cache` has no staleness check — found and fixed live, 2026-09-04,
right after Egeria's redeploy (full re-seed, fresh metadata store).** `survey_definition_
executor.py`'s `_resolve_process_guid` trusts a cached `process_guid` unconditionally when
`refresh_definition` isn't explicitly requested (the default) — no check that the cached GUID
still resolves. Confirmed live: a cached row from 2026-08-09 pointed at a GovernanceActionProcess
GUID that 404s post-redeploy (`GET .../metadata-elements/{guid}` → gone), while the real Survey
Definitions exist fine under fresh GUIDs (`find_candidate_process_guids('Git Repository')`
returned all 10 `Repo*` processes correctly). All 29 cached rows predated the redeploy by weeks,
so cleared the whole table rather than individually verify each — it's a pure performance cache
(self-repopulates via `find_candidate_process_guids` on next use), not a source of truth, so
clearing has no data-loss risk. **Not fixed at the code level** — `_resolve_process_guid` still
has no staleness check, so this can recur after any future full re-seed; worth a real fix
(verify-before-trust, or a TTL) if platform re-seeds become routine rather than rare.

**11. Survey Results Dashboards — Security Overview's Discovery placement, project-owner decision
2026-09-04: "both."** A dashboard's stage placement is derived (never hand-authored) from its
input analyses' own catalog intents, and `security_overview` genuinely spans Discovery
(`license_classification`, `repo_conventions`) and Assessment (`security_scan`, `ci_quality`,
`cve_scan`, `secret_scan`, and 5 more) — so it correctly shows under both, and the Assessment-tier
inputs correctly report "not run" under Discovery, since Discovery doesn't trigger Assessment-tier
work. Not a bug, but a real UX rough edge: someone who's only done Discovery-tier work sees a
mostly-empty security card. Decided: do both — (a) reframe the copy shown under Discovery
specifically ("Discovery-tier signals below are current; the rest needs Assessment" rather than a
flat "not run yet" that reads as a gap), and (b) split out a lean Discovery-tier security teaser
(license/conventions only) alongside the full Assessment-only picture. **Built 2026-09-04**:
`renderSecurityOverviewDashboard(dashboard, stage)` now branches on the requesting stage — under
Discovery it shows only the two Discovery-tier tiles (License, Disclosure policy) with the
reframed copy and no detail dump underneath; under Assessment (or any other caller) the full
scorecard renders exactly as before. `stage` is threaded down from `_loadSurveyResultsPanel`'s
own request param through `renderSurveyResultsPanel`, not re-derived — the same value the server
was already asked for.

**12. Dashboard trend charts — real feature, cheap because the plumbing already exists.**
Every `AnalysisKindResults` already carries a `trend_reader` (`GET /{slug}/analyses/{analysis_id}
/trend` already serves raw JSON, already consumed by Understanding's charts) — `SURVEY_RESULT_
DASHBOARDS` currently only renders single-snapshot `grouped_cards`/`custom` views, never a trend.
Extending dashboards to also chart each input's trend (matching `loadRepoSurveyHistoryChart`'s
existing client-side chart-building convention) is wiring existing infrastructure together, not
building new plumbing. Not scoped or built yet — named here so the next pass doesn't have to
rediscover that the hard part is already done.

**13. Detailed survey data reachable from chat — corrected scope, 2026-09-04 (an earlier claim
in this session that chat has "zero access to structured findings" was wrong).** Chat already
does real structured-findings access for repos via `context_compile.py`'s `compile_context()`:
resolves the question catalog's `analysis_ids` for the actual query asked (ranked by relevance,
not just catalog position), pulls each analysis's *stored results* (not RAG text), and packs them
into the prompt with explicit gap-reporting for anything unrun — confirmed this is exactly how
chat correctly listed failing CVEs when asked. The real gap, checked directly: `get_questions
("database")`/`get_questions("filesystem")` both return **0** — the question catalog is
repo-only (51 questions), so `compile_context`'s hardcoded `resource_type="repo"` isn't a bug,
it correctly reflects there being nothing to pass through for the other two resource types yet.
Chat for a database/filesystem resource currently falls back to plain RAG search only (confirmed
fail-soft, not broken — `_compiled_evidence`'s own docstring: "any problem here returns nothing
and the agent proceeds... searching collections itself"). **Scope for closing this**: (a) small
code change — thread `resource_type` through `compile_context`/`_compiled_evidence` instead of
the repo hardcode (mechanical, `get_questions` already accepts the param); (b) the real work —
author database and filesystem question catalogs mirroring the repo catalog's 51 entries, which
needs real domain knowledge about what DB/FS surveys actually produce, not a quick generation
pass. (b) is the actual size of this item; (a) is nearly free once (b) exists. Not started.
Placeholder, same conversation: chat can already surface findings like failing CVEs in an answer
— project owner noted (not yet scoped) that these could feed an RFA and/or a generated detailed
report (e.g. a "Security Details" report) rather than living only in a chat transcript. Logged
for later design, not attempted here.

**Deferred, project-owner decision 2026-08-31: "We are deferring work on DB/FS until all the
Repo work is complete."** Applies directly to (b) above — do not start authoring database/
filesystem question catalogs, or any other DB/FS-specific feature work, until Repo work is
signaled complete. Surfacing a DB/FS gap for the backlog (as this item already does) is fine;
starting to build it is not.

**14. "Run all &lt;Stage&gt;" — built 2026-09-04, direct feedback: "make it first and separate so
a user can easily just select that and be confident that all the surveys are being run."** Not a
re-labeling of the existing per-stage Egeria Survey Definitions (`RepoScoutingSurvey`/
`RepoDiscoverySurvey`/`RepoAssessmentSurvey`/`RepoAnalysisSurvey`) — measured 2026-09-04 those
cover 3/3, 5/7, 8/14, and 7/10 of each stage's individual `analysis_catalog.yaml` entries
respectively, so pinning one under a "Run all" label would silently under-run three of the four
stages. New `POST /{slug}/analyses/stage/{stage}/run` (`projects.py`) instead derives the full
`step_keys` set live from every local `AnalysisKind` entry tagged that intent (via
`REPO_ANALYSIS_STEP_MAP`), excluding `action` entries (`ingest`/`publish`/`profile` — same
exclusion the scheduler already applies), then runs them as one `SurveyOrchestrator.run(steps=…)`
call via the adapter's existing `run_batch` primitive (previously only reachable from
`survey_definition_executor.py`). Self-maintaining as entries are added to a stage — no drift risk
the way a fixed Survey Definition has. A visually-distinct amber button, pinned above the regular
card grid, in Scouting/Discovery/Assessment/Analysis (`_runAllStageHtml`/`_runStageBatch` in
`index.html`). 5 new tests (`tests/test_stage_batch_run_route.py`), full suite green.
**Caught only by live browser testing, not by any Python test or `node --check`**: the button's
`onclick` attribute was double-quoted around `JSON.stringify()`'s own double-quoted output,
silently truncating the handler to `onclick="_runStageBatch("` — the button rendered fine and
looked clickable; nothing happened on click. Every other `JSON.stringify`-into-`onclick` call site
in the file already single-quotes for exactly this reason; this one didn't follow the convention.
Fixed, plus a regression test pinning the single-quoted form (`node --check` parses the JS fine
either way — it can't see what a `<button onclick>` attribute's exact string content does at
click time, only whether the surrounding script is syntactically valid). Live-verified end to end
after the fix on a real repo: POST fired, toast showed correct step/analysis counts, run
completed.

**15. Sub-Resources tab moved from Assessment to Analysis — built 2026-09-04, direct feedback.**
`sub_resource_survey` is catalog-tagged `intent: analysis` ("produces a structural recommendation,
not an evaluation against criteria") but its only interactive UI (select/catalog) lived in
Assessment's own sub-tab since 2026-08-13 — a placement artifact of the Scouting workflow redesign
plan, not design intent. The Analysis-tab card was a dead end: a bare Run button with no selection
affordance. Moved the whole sub-tab (view container, nav entry, dispatch) to Analysis; backend
routes (`/api/projects/{slug}/sub-resources*`) are resource-type-generic and needed no changes.
`docs/assessment-sub-resource-cataloging.md` keeps its old filename — the content moved, the name
didn't (not worth a doc rename for this).

**16. Curate blueprint accept blocked until every member is materialized, with a one-click fix —
built 2026-09-04, direct feedback:** accepting a proposed blueprint could fail on unpublished
member components (an existing, deliberate partial-accept design — Decision 2, "accepting a
blueprint does not implicitly accept or materialize its members" — not a bug), but the UI gave no
warning before the click and no easy recovery after. `_curateBlueprintRow` now computes each
member/child's real Egeria-materialized status (via the same `materialized.guid` check
`memberVerdictBadgeInline`'s ⬡ glyph already uses) *before* rendering the Accept control: when
anything is unmet, the Accept button is disabled with an explicit message naming which components,
and a "📤 Publish missing component(s) & accept" button (`_curatePublishMissingComponents`) does
the previously-manual workflow — accept each unmet member individually (materializing it), then
re-submit the blueprint verdict — in one click, reporting any individual failures rather than
swallowing them. Nested-blueprint children are surfaced but not auto-cascaded (a child has its own
members that may need the same treatment first) — still reached via the existing jump-to-blueprint
link. **Deliberately left the backend's permissive partial-accept (Decision 2) unchanged** — the
UI now defaults to blocking the common accidental case, but the API still allows a genuinely
intentional partial accept (e.g. a child that will never materialize because it was rejected) if
called directly.

**17. Stale-Egeria-pointer scan-and-clear is now scheduled — built 2026-09-04, after the
2026-09-04 full repository-store wipe.** The human-driven recovery path (`EgeriaResync.scan()`/
`.apply()`, Admin > 🔄 Egeria Alignment) worked exactly as designed — but nothing ran it until
someone remembered to; the wipe sat with 60 stale asset GUIDs, 1,040 orphaned publish claims, 4
stale investigation GUIDs, and 58 stale contexts until manually triggered. `egeria_resync.py`
gains `scan_and_clear()` + a background scheduler (`start_scheduler`/`stop_scheduler`, wired into
`web/app.py`'s lifespan alongside `bootstrap`'s Dr.Egeria-definition healing, same 600s interval,
same never-block-startup/never-die-on-exception discipline). Automates only
`SAFE_SCHEDULED_STEPS` — `clear_stale_assets`, `clear_orphan_publish_claims`,
`clear_stale_investigations`, `clear_stale_contexts` — a strict subset of `REPAIR_STEPS` chosen
because every one of them verifies live before writing (never guesses — a lookup failure is
`undetermined`, never cleared), costs no real time, and never needs a human decision. Deliberately
excludes anything `EXPENSIVE_STEPS` (archive-downloading republishes) or `needs_decision`
(Egeria Project bindings — RE genuinely cannot guess which Project a resource should join), with
an explicit defense-in-depth check in `scan_and_clear()` itself (not just inherited from the
constant) refusing to apply either kind even if a future finding's step name happened to collide.
Live-verified against the real post-wipe registry: manually ran the same four steps first (60
assets/1,040 claims/4 investigations/58 contexts cleared, independently re-scanned to confirm —
not just trusted the apply response), which surfaced a genuine, expected downstream consequence
worth recording here too: 59 repos went from "published" to `unpublishable` because the
`all-current-repos` investigation's own Egeria Project binding was itself stale and got correctly
cleared — those repos were inheriting publish-eligibility through it. That's a real
`needs_decision` item (bind or create an Egeria Project), not something `scan_and_clear()` should
or does attempt. 11 new tests (`test_egeria_resync_scheduler.py`).

**Deliberately closed, with a measurement behind each — do not reopen without re-measuring:**
the LLM adjudicator (the doc lens reaches Milvus's real components more cheaply where
documentation exists); milvus site ingestion (302-loops for every user agent including a
browser one); doc-kind chunking selection (0 of 20 collections are API-reference shaped);
boilerplate stripping and version collapsing (both already work, finding 119); `misgrouped`'s
emitter and guard-based branching (nothing would behave differently); Java `src` naming and the
`cmd/X`+`pkg/X` merge (both downgraded by measurement, finding 97).

**Small and real, low value:** Go cohesion without recursive rollup; `find_artifact("readme")`
preferring a nested README over the root one; rule 17's guard validating a step's *declaration*
rather than its behaviour.


## Open items

Grouped by area. Within a group, the most actionable entries come first.

**Priority tiers (project owner, 2026-09-18)**, used going forward to sequence which open
items get picked up next — not a re-tag of every entry below, but the lens for new ones and for
choosing what to dispatch:

1. **Bugs and backend infrastructure** — data-corrupting races, broken/unverified execution paths,
   orchestration. Fix correctness before building on top of it.
2. **UI, generic/structural** — screens and surfaces that are missing or a stub (Search/Discover),
   admin surface shape, cross-cutting UI mechanics.
3. **Design-gated** — anything that needs a designer decision before it's buildable. Tracked so it
   doesn't silently sit; see the designer's own `DEFERRED-REGISTER.md` for what's already on their
   desk.
4. **Survey/analytics/results enhancements** — new or improved analyses, richer findings, cost and
   dependency modelling. Valuable, but behind the tiers above.

### TIER 1 — FIXED 2026-09-19: whole-definition Prefect orchestration bypassed per-step `executes_at` routing

**Found live, the day `PREFECT_ENABLED` defaulted to `true` for the first time with a real
reachable server** (`PLAN-PREFECT-OR-ALTERNATIVE.md` §5 phase 3): CI failed on a database Survey
Definition fixture, tracing back to `SurveyDefinitionExecutor.run()`'s `_run_via_prefect` path —
gated only on `_prefect_orchestration_enabled()` (i.e. `config.prefect.enabled`), with **no check
on what any individual step's `executes_at` actually said**. `prefect/flows.py`'s
`re_survey_definition_flow` → `run_planned_step_task` calls `run_surveyor_step_task.fn(...)` — the
plain local-analysis-step runner — for **every step in the plan**, including ones tagged
`executes_at="egeria"`. Confirmed directly: an `executes_at="egeria"` step that should have raised
`_trigger_egeria_native_survey`'s "no stored Egeria asset guid" instead surfaced
`run_surveyor_step_task`'s own "Entity ... not found in registry" — proof the step never reached
its real handler at all.

**Repo Survey Definitions never exposed this** — repos have no Egeria-coordinated path today (see
the "three execution modes" Tier 1 entry above) — **so it would have silently broken every
database and filesystem Survey Definition** the moment a real Prefect server was reachable, which
is now the default topology on this machine. Phase 2's live verification called `run_prefect_step`
directly for one step (`repo_arch_coupling`) rather than through `SurveyDefinitionExecutor.run()`'s
whole-definition path — a real gap in what "verified live" actually covered, worth naming plainly
rather than letting the phrase imply more than it checked.

**Fixed** by `_all_steps_prefect_runnable()` (`survey_definition_executor.py`) — gates
`_run_via_prefect` off entirely for any definition mixing engines, falling through to the existing
local loop, which already routes each step correctly one at a time (repo/analysis-step definitions,
which never mix engines, are unaffected and still get whole-definition orchestration). Regression
test: `test_prefect_orchestration_respects_engine_routing.py`, proving the egeria step reaches its
real handler with orchestration forced on, independent of whether a server happens to be reachable.

**Left as a known, non-bug constraint, not fixed:** `run_surveyor_step_task` always constructs a
fresh `ProjectRegistry()` rather than reusing the executor's own registry instance — correct for a
real distributed worker (which must have its own DB connection to the same shared Postgres
regardless), but it means anything exercising this Prefect path needs data that exists in the real
default registry, not an isolated/throwaway one. Broke two new tests
(`test_execution_modes_path_a_end_to_end.py`) that used a tmp-path SQLite registry; fixed by
disabling whole-definition orchestration for those tests specifically (they test the local loop,
not this boundary), not by changing the production code.

**The real fix, not yet built:** per-step engine routing *inside* the Prefect flow itself, matching
what the local loop already does — so a mixed-engine definition could still get Prefect's
observability for its Prefect-eligible steps instead of falling back to the local loop entirely.
Not scoped here; the guard above is the safe, correct behavior until it is.

### The outbox drain does not serialise, and its docstring says it does — FIXED 2026-09-19

**Resolved:** `claim_due_outbox_elements()` (`resource_explorer/registry.py`)
now performs the select and the `status='running'` transition in one
transaction, with `FOR UPDATE SKIP LOCKED` added to the `SELECT` on Postgres —
so two concurrent drainers provably cannot claim the same row (see
`docs/design-notes/OUTBOX-DRAIN-RACE-FIXED.md`). A stranded claim (drainer
died before marking the row done/failed) self-heals via `CLAIM_LEASE_SECONDS`;
a claim that could not even be attempted (no Egeria client reachable) is
released immediately by `drain_outbox`'s no-client branch calling
`release_outbox_claim()`. Regression coverage lives in
`tests/test_egeria_outbox.py`'s `TestTheClaimActuallyClaims` (two claimers
never get the same row, a killed drainer's rows are reclaimable after the
lease, an outage hands the claim back rather than holding it) and
`TestTheClaimSqlIsValidOnPostgres` (pins the exact SQL shape — no `FOR UPDATE`
combined with an outer join, which SQLite's test tier cannot itself catch).

**This fix landed in the code on 2026-09-02 itself** (commit `472f83c5d`, a
few hours after the entry below was filed and the docstring was first
corrected to describe the then-still-broken behaviour) — but the docstring
correction was never revisited once the real fix landed, so it kept
describing the bug as unsolved, and this backlog entry was never marked
fixed. Caught 2026-09-19 while auditing this entry to write a regression
test: the "fix" the entry called for already existed in `registry.py`, just
undocumented as done. Corrected the docstring in the same pass (see
`claim_due_outbox_elements`'s current docstring) — a second instance of
this exact failure mode (a docstring asserting the opposite of what the code
does) is precisely what this entry itself warned "is worse than an
undocumented race."

*Original entry below, kept for the reasoning behind why the hazard is
asymmetric — annotations survive a double-apply, annotation links do not.*

### The outbox drain does not serialise, and its docstring says it does

**Filed 2026-09-02, while a batch republish had the web server deliberately
stopped — deliberately filed BEFORE restarting it, because restarting hides
the symptom and the bug goes back to being invisible until two drainers
happen to overlap again.**

`ProjectRegistry.claim_due_outbox_elements()` does not claim. It is a plain
`SELECT` — no `FOR UPDATE`, no `SKIP LOCKED`, no status transition — and
`drain_outbox()` marks a row `done` only *after* its create succeeds. Two
drainers therefore select the same rows and both call `apply_element`.

Its docstring asserted the opposite ("the drain marks each row in flight as it
takes it"). That is corrected in place now, but the correction is a note, not
a fix. **A function named `claim_` that performs no claim, documented as doing
the locking it does not do, is worse than an undocumented race: the next
reader checks, finds the claim described, and concludes it is handled.**

The hazard is asymmetric, which is what makes it worth fixing rather than
noting:

- **Annotations survive it.** The second create is rejected as a duplicate
  qualifiedName, `apply_element` adopts the existing GUID, one element exists
  and two rows are marked done.
- **Annotation links do not.** They go through a multi-link `attach()` that
  duplicates silently instead of upserting, and there is no reconciler for
  annotation-level duplicates the way `scripts/reconcile_survey_definition_links.py`
  exists for step links. A duplicate link is permanent and invisible.

**The usual second drainer is not a person.** It is `scheduler.py`'s loop
inside any running `resource-explorer web`, firing every
`_CHECK_INTERVAL_SECONDS` (900) whether anyone is at the keyboard or not. So
"do not run two republishes at once" is not sufficient guidance — a single
operator with the app open is already two drainers.

**Fix:** serialise the claim — `SELECT ... FOR UPDATE SKIP LOCKED`, or a
`status='running'` transition in the same transaction as the select — so the
property holds by construction instead of by remembering to stop the server.
Until then, stopping the web server is the mitigation, and it is a mitigation
for one run rather than a fix.

### TIER 1 — `catalog_and_survey` never refreshes an existing element's credentials/connection

> **Fixed for fresh catalogs, 2026-09-20 — see
> `docs/design-notes/CATALOG-AND-SURVEY-REFRESH-FIX.md` for the full investigation.**
> The root cause was not the guard this entry originally suspected: Egeria's
> create-from-template calls ARE upsert-safe by qualifiedName (confirmed live —
> re-issuing one for an existing element returns the same GUID, not a
> duplicate), so removing the `if not <guid>` guard alone would not have
> helped. The real defect is that pyegeria's `create_postgres_server_element_
> from_template`/`create_postgres_database_element_from_template` never set
> `"deepCopy": True` on the template request, so Egeria never copies the
> template's attached Connection subgraph — confirmed live that a first-time
> catalog run can end up with no Connection too, not just a repeat one.
> `EgeriaDatabaseSurveyor._create_postgres_element_from_template` now bypasses
> those two wrappers and adds `deepCopy: True`, fixing this for **fresh
> catalog runs**.
>
> **This does NOT repair `coco_ods` itself, or any other already-broken
> existing element**, and `coco_ods` was deliberately left in its current
> state. Confirmed live: Egeria's by-qualifiedName reuse path (what fires for
> an element that already exists) never re-runs deepCopy's child-copying, no
> matter how many times it's called. The template's attached Connection is
> also not a simple Connection — it's a `VirtualConnection` embedding a
> `SecretsStoreConnection` wired to a YAML-file secrets connector, plus its own
> `Endpoint`/`ConnectorType` — so hand-assembling it via generic
> `ConnectionMaker` calls was judged (project owner decision, 2026-09-20) an
> unsupported-path workaround, not a fix, and was not attempted. The only
> confirmed-correct repair is **delete-and-recatalog**, which fixes the
> connection but changes the asset's GUID and orphans its existing Survey
> Reports/annotations — a real fix with a real cost that needs its own
> decision before it's built, not something to slip in as a side effect of
> this bug fix. `EgeriaDatabaseSurveyor._warn_if_database_has_no_connection`
> makes this state visible (WARNING-level log) the next time it's hit, instead
> of only surfacing downstream as an opaque `OPEN-SURVEY-0009`.
>
> Also confirmed, per the task's explicit ask not to assume symmetry:
> `EgeriaFileSystemSurveyor`'s `catalog_and_survey` does **not** have this bug
> — it takes no credentials at all and its templates have no attached
> Connection subgraph, so neither the guard nor the `deepCopy` gap applies
> there. No code change was needed on the filesystem side.
>
> Still open: a pyegeria gap (`deepCopy` never set) should be logged in
> `PYEGERIA_ISSUES.md` per this repo's pyegeria-gaps-tracking convention — not
> done as part of this change since that file is in the `egeria-python`
> checkout, outside this fix's `trellis`-only scope.

**Found live, 2026-09-19/20**, while testing Egeria-native survey result retrieval against a real
database (`coco_ods`, part of the Coco Pharmaceuticals sample data). The native PostgreSQL survey
engine rejected the triggered engine action as `INVALID` with:

> `OPEN-SURVEY-0009 The postgres-database-survey-service Survey Action Service has been supplied
> with asset 8f239316-8773-44da-9e8e-760243226a10 which has no connection, so there is no way to
> reach the resource it describes`

**Root cause, confirmed by reading the code, not guessed:**
`EgeriaDatabaseSurveyor._catalog_and_survey()` (`surveyors/database/egeria_database_surveyor.py`)
looks up the server/database elements by name first (`_find_element_guid`) and only calls
`create_postgres_server_element_from_template`/`create_postgres_database_element_from_template` —
the calls that actually carry `db_user`/`db_pwd` and presumably attach a `Connection` — **when no
element is found by that name**. Once an element exists, re-running `catalog_and_survey` (even
with corrected credentials) reuses the existing element unchanged and never re-creates or updates
its connection. Live-verified directly: re-ran `catalog_and_survey` for `coco_ods` with corrected
`db_user`/`db_pwd` (see the `egeria_user` validation entry above — this asset was originally
catalogued using bad credentials before that fix) and got the *identical* `OPEN-SURVEY-0009`
error on the new engine action, proving the re-run changed nothing about the stored connection.

**Impact:** any database or filesystem asset first catalogued with wrong/incomplete
credentials is permanently stuck with no working native survey path — there is no way to correct
it short-of manual intervention, since the one function that would normally be expected to "fix
it, just re-run the catalog step" silently no-ops on the part that matters.

**Not yet investigated:** whether pyegeria exposes an "update connection on an existing asset"
call distinct from the create-from-template ones, or whether the only real fix is delete-and-
recatalog. Also unconfirmed: whether `create_postgres_server_element_from_template`/
`create_postgres_database_element_from_template` themselves are upsert-safe (would update in
place if called again) — if so, the simpler fix is just removing the `if not <guid>` guard and
always calling them, letting the template call itself decide create-vs-update. Investigate
before assuming either fix is correct.

**Explicitly not a bug in the Egeria-async-result-retrieval work done the same day** (see
`EGERIA-ASYNC-RESULT-RETRIEVAL-IMPLEMENTED.md`) — that work's poll/attribution/parse mechanism was
validated live against this exact failure: it correctly polled to the real terminal status
(`INVALID`, not a timeout), correctly surfaced the real completion message, and correctly refused
to guess when two survey reports (server-level and database-level, triggered together) landed in
the same time window, raising `SurveyReportAttributionError` rather than picking one at random.
The `0` annotation counts read back were independently confirmed accurate given the underlying
`INVALID` survey — a real absence, not a miscount.

### TIER 1 — "Three execution modes" don't map onto one verified mechanism, and two of the paths are untested

> **Planned 2026-09-18 — see `docs/design-notes/PLAN-EXECUTION-MODES-VERIFICATION.md`.** Two
> corrections to this entry, found while planning against it: the global-override concern below
> (`config.prefect.enabled` rerouting every `resource-explorer` step) is already fixed —
> `survey_definition_executor.py:317-332` honours `executes_at`, and rerouting needs the separate
> `prefect.route_local_steps`, pinned by `tests/test_prefect_dispatch.py:179/184`. And there *is* a
> filesystem hybrid path — not a class, but `hybrid_filesystem_surveyor.py:12`'s
> `run_hybrid_filesystem_survey()`, called from `web/routes/filesystems.py:271-272`. The core
> finding survives both corrections. The plan recommends folding both hybrid entry points'
> capabilities into `executes_at` routing as a new `egeria-hybrid` value, not retiring or
> documenting them as legacy — they are live default-path code with capabilities (cache-or-run,
> catalog-on-demand, engine provenance) the other two paths lack.

Raised by the project owner, 2026-09-18: are all three of RE, Egeria, and Hybrid execution genuinely
working? Investigated against the code rather than assumed, and the honest answer is that **"three
modes" isn't one mechanism with three settings** — it's two unrelated things that both get called a
"mode":

1. **`executes_at` routing** (`survey_definition_executor.py:314-514`) — a per-step field on Survey
   Definitions with exactly three legal values, `"resource-explorer"`, `"egeria"`, `"prefect"`
   (anything else raises, `:513-514`). `Architecture.md:229-233` frames this correctly as **two
   coordinators, neither of which is RE**: either Egeria coordinates and RE executes leaf steps, or
   RE coordinates and hands work to Prefect.
2. **`HybridDatabaseSurveyor`** (`surveyors/database/hybrid_database_surveyor.py:14`, docstring "uses
   Egeria when available, falls back to custom") — a **separate, older class not wired into
   `survey_definition_executor.py`'s dispatch loop at all**. Invoked directly from
   `web/routes/databases.py:250-251` and `cli/main.py:1687-1697` via `run_hybrid_survey()`. CLAUDE.md
   rule 15 constrains it (run the local scan immediately after triggering Egeria's async survey).
   **Correction, 2026-09-18 planning pass:** there is no *class* equivalent for filesystems, but
   there is a function doing the same job — `filesystem/hybrid_filesystem_surveyor.py:12`'s
   `run_hybrid_filesystem_survey()`, called from `web/routes/filesystems.py:271-272` and
   `cli/main.py:2104-2105`. So the hybrid idea is two implementations on two resource types with no
   shared abstraction, not one orphan class — a different, slightly worse version of the same
   problem. There is still no repo hybrid path.

**Verification status, checked directly rather than assumed:**

- `executes_at: resource-explorer` / `executes_at: prefect` **routing logic is solidly unit-tested**
  (`tests/test_prefect_dispatch.py` covers fallback, cancellation, exact routing predicates) — but
  that is dispatch-logic testing, not a live end-to-end run through either path.
- `executes_at: egeria` (the `other_engine_handlers` mechanism, `database/survey_definition_adapter.py:113`,
  `filesystem/survey_definition_adapter.py:138`) has **no test coverage found**, matching the
  codebase's own admission: `Architecture.md:246-247` states this route "is the intended route for
  unifying database and filesystem survey launching... and it is **untested end to end on either
  type**."
- `HybridDatabaseSurveyor` / `run_hybrid_survey` has **no test coverage found at all** — not in any
  file under `tests/` (grepped, came up empty except an unrelated baseline JSON fixture) — and it sits
  architecturally disconnected from the `executes_at` system `Architecture.md` describes as canonical.

**Net: one of the three has solid dispatch-logic tests (`resource-explorer`/`prefect` routing), one
is self-admittedly untested end-to-end (`egeria`-triggered coordination), and the third
(`HybridDatabaseSurveyor`) is an unrelated, untested, database-only code path that predates the
`executes_at` design.** This needs closing before more work is built on any of the three assuming
they're equivalent: at minimum, a real end-to-end test per path against a live Egeria/Prefect, and a
decision on whether `HybridDatabaseSurveyor` should be folded into `executes_at` routing, replaced by
it, or documented as a deliberately separate legacy path.

### TIER 1 — Prefect: what's actually broken, concretely, for the project owner who wants to use it

> **Planned 2026-09-18 — see `docs/design-notes/PLAN-PREFECT-OR-ALTERNATIVE.md`. Recommendation:
> finish Prefect, ~2 days of work, not the multi-week commitment this entry's framing implied.**
> Verified live on the machine that several of this entry's specifics were already stale: the
> Prefect server container (`egeria-optional-prefect-server`) **is running and healthy**, answering
> `/api/health` at the exact URL RE defaults to — not "isn't started". The Postgres
> database/role gap is **closed** (`prefect` DB and `prefect_user` role both exist on the shared
> instance). The work-pool mismatch is real but is a `.env` value (`PREFECT_WORK_POOL`), not a code
> change. The `dr_egeria_survey_publisher.py` publishing path's renderer is complete and tested;
> what's outstanding is running it once against dev Egeria. Dagster, Temporal and Airflow were
> compared and lose (wrong execution model for an Egeria-defined step graph); a no-engine
> alternative is the real challenger and loses only because it would mean rebuilding retries,
> cancellation and per-step observability that Prefect already provides working today.

Raised by the project owner, 2026-09-18: wants to actually use Prefect (or an equivalent) for the
orchestration tooling, integration connectors and observability it provides, and asked what's
actually wrong with it today rather than leaving "off by default" as an unexamined steady state.

**Timeline** (full detail in this document's "Distributed survey orchestration via a flow tool
(Prefect)" entries below): prototyped 2026-07-14, default-on
2026-08-26, reverted to off-by-default 2026-09-04 after 13 orphaned
`prefect.server.api.server:create_app` subprocess servers leaked (`config.py:319-331`,
`PrefectConfig.enabled`'s docstring). **Root cause was Prefect's own client, not RE's fallback
logic**: Prefect starts an ephemeral subprocess server when `PREFECT_SERVER_EPHEMERAL_ENABLED` is
true (Prefect's own shipped default) and no API is reachable. Fixed by forcing
`PREFECT_SERVER_EPHEMERAL_ENABLED=false` at package-import time (`resource_explorer/__init__.py:6-20`),
covering all Prefect-importing modules including `prefect_status.py`, which imports
`prefect.client.orchestration` independently of `prefect_adapter.py`.

**Current defaults:** `PrefectConfig.enabled=False`, `route_local_steps=False` (`config.py:339-343`).
`executes_at: prefect` steps do exist (`scripts/generate_repo_survey_definition.py`'s
`PREFECT_ROUTED_STEPS`) but publishing the corresponding Egeria-side step requires a manual,
human-in-the-loop Dr.Egeria run (`dr_egeria_survey_publisher.py`) — not yet done as part of any pass.

**Not containerized, by explicit prior decision:** `scripts/prefect_up.sh`/`prefect_down.sh` are
bare-host scripts only — no launchd/systemd unit, no container — because Trellis as a whole wasn't
considered ready for containerization at the time.

**A container already exists, but not here, and has three unresolved gaps if reused:** it lives in
`egeria-workspaces-fs`'s `optional-associated-runtimes/prefect`, not Trellis. (1) work-pool name
mismatch — `egeria-pool` vs. RE's default `default-agent-pool` — would leave steps `SCHEDULED`
forever with no error surfaced; (2) that worker container has no access to the `resource_explorer`
package/code, so it cannot actually execute RE's steps; (3) unconfirmed whether a separate `prefect`
Postgres database/role is provisioned on the shared instance.

**Also out of scope today, and worth naming:** `executes_at: egeria`-coordinated surveys get zero
Prefect/RE visibility — no flow-run, no local thread, no activity_log update — a separate, undesigned
observability gap from the one Prefect would otherwise close for local steps.

**What production-viability would concretely take** (the docs already lay this out rather than
leaving it open-ended): resolve the three container gaps above (or build a Trellis-native
container); complete `dr_egeria_survey_publisher.py` publishing for `executes_at: prefect` steps;
and decide/document whether `PREFECT_ROUTE_LOCAL_STEPS` should ever go default-on given the
multiplied per-step overhead already noted elsewhere in this document. None of this has been
scoped into a real plan yet — recorded here as the concrete starting point for one, including
whether Prefect remains the right choice at all versus an equivalent tool, which this entry does
not attempt to answer.

### Survey execution

> **STATUS 2026-09-01, later the same day: the precondition half of this is BUILT and this entry is
> stale where it says otherwise.** `survey_orchestrator.py:226-241` evaluates a step's
> `requires_context`, and on a failed precondition emits a `SKIPPED_BY_DESIGN` annotation carrying
> the reason and records `result.skipped_steps[step_key]`. `step_preconditions.PRECONDITIONS`
> defines `has_dependencies`, `has_versioned_dependencies`, `has_file_inventory` and
> `has_code_symbols`, and `repo_cve_scan` declares `has_versioned_dependencies` in production.
>
> Caught by an agent scoping the GAP analyses, which read this entry as a statement of current
> state and would have rebuilt the orchestrator plumbing. Verified against the source before this
> note was written. **What remains open is the vocabulary, not the mechanism** — richer context
> facts (`first_party_code`, `is_deployable`, `has_documentation_site`) per
> `docs/question-answering-and-context.md (§9)` §4, and guard evaluation in the EXECUTOR, which is
> separate and still unbuilt.
>
> The entry is kept rather than deleted because the reasoning below is what the built feature
> honours, and because a backlog item that silently disappears leaves no record of why it was
> closed. An entry that outlived what it described, in a document whose whole job is to describe
> what is outstanding.

**No conditional execution of survey steps — every selected step runs, whether or not it can say
anything.** Raised by the project owner, 2026-09-01. `SurveyOrchestrator.run()` iterates
`list(all_surveyors.items())` and runs each in turn. The only filters are the cost ceilings
(`max_fetch_cost`/`max_compute_cost`) and an explicit `steps=` list. There is **no way for a step to
declare a precondition on the state a previous step produced**, and no way to skip one whose input
is absent.

The consequences are already visible, and they are not crashes:

- **`cve_scan` on a repo with no parsed dependencies.** It reads `project_dependencies`, finds
  nothing, and correctly declines rather than claiming "no CVEs". Right behaviour — but it ran, was
  timed, was published as an analysis, and contributed nothing. `security_summary` then counts it
  among its 8 `INPUT_KINDS` as *never ran*, and below `MIN_INPUTS_FOR_VERDICT` withholds a verdict.
  A step that cannot say anything still consumes a slot in the picture.
- **The distinction that has to survive.** `surveyors/result_status.py` already separates
  `NOTHING_FOUND` from `SKIPPED_BY_DESIGN` from `NEVER_RUN`. Conditional execution must produce the
  middle one — *skipped, with a stated reason* — never silence. A step that vanishes from a report
  is indistinguishable from one that ran and found nothing, which is the failure this codebase keeps
  removing. `repo_classification`'s `architecture_recovery_gate` is the worked example of doing it
  right (`docs/question-answering-and-context.md (§9)` §3): it reports `skipped_by_design`, the reason
  travels with the skip, and `respect_gate=False` still runs it — the gate changes the default, not
  the permission.

**What is missing, concretely.** `StepInfo` declares `requires_resources` (zipball, clone) and
`requires_views`, both about *runtime inputs*. Neither expresses "this step needs rows in
`project_dependencies`" or "this step is pointless on a repo with no first-party code". The design
note already proposes the shape — `requires_context` alongside `requires_resources`, checked before
dispatch, producing a `skipped_by_design` with a reason when unmet
(`docs/question-answering-and-context.md (§9)` §4) — but nothing is built beyond the one hand-wired gate.

**Egeria already has the branching half of this, and it is worth not reinventing.** Reported
2026-09-01 by a concurrent session that read `EngineActionHandler.initiateNextEngineActions`
(lines 2839-2945) in the Java source directly — **recorded here second-hand; not verified from the
source by the author of this entry**, so confirm before building on it:

- Each `NextGovernanceActionProcessStep` link carries a `guard` and a `mandatoryGuard`.
- `validNextAction = (guard == null)`; otherwise the link fires only if that guard string appears in
  the previous step's `outputGuards`.
- A step emits guards through `recordCompletionStatus(status, outputGuards, ...)` and may emit
  several.
- `mandatoryGuard` is a **join, not a branch** — `runEngineActionIfReady` holds a prepared action
  until every mandatory guard has arrived from its upstreams.

So the model is "one step, several outgoing links each with a guard, follow the ones whose guard
fired". That is a real answer to *which steps run*, and it means RE should express conditionality in
Egeria's vocabulary rather than inventing a parallel one.

**What Egeria's model does NOT give you is the part this entry is actually about.** Guards say which
links *fire*; nothing records what therefore *did not run*, or why. A step that is simply never
reached leaves no trace, which is precisely the silence that makes `NOTHING_FOUND` and `NEVER_RUN`
indistinguishable downstream. Whatever is built has to add the `SKIPPED_BY_DESIGN`-with-a-reason
record on top of guard evaluation — it will not come for free with the guards.

**Related, and deliberately kept separate:** step *ordering* is a different problem and is currently
correct. Producers precede consumers in `STEP_REGISTRY`, verified live 2026-08-31 (one `amundsen`
survey produced 880 dependency rows and 8 cve_scan findings in the same run) and now guarded by
`tests/test_step_execution_order.py`. That order is positional and undeclared, so the test exists to
stop it regressing silently; **if conditional execution is built, it should express the data
dependency it already relies on rather than adding a second implicit mechanism beside it.**

### Admin surface: Option B (separate pages), and possible Trellis-wide centralisation

**Deferred by decision 2026-09-01, not dropped.** Project owner: *"agree in general — do the fix recommended
and put option B in backlog to be revisited. Its quite possible that we may need to centralize admin
across trellis at some point — so lets not lose this in the day to day."*

`docs/admin-surface-options.md` recommends **staying inline for now** and fixing the existing drift
first. That recommendation is accepted. This entry holds what was deferred.

**Option B — extract admin into separate static pages** served by the same app, following the
`admin-feedback.html` precedent (RE has no JS bundler; a "separate site" costs about what that page
cost, not a second app). Measured pressure at time of deferral: `index.html` is **15,774 lines** and
the most-churned file in the package — **166 commits since 2026-08-06**, next closest
`docs/Backlog.md` at 116, peaking at 23 in one day. But that is co-occurrence, **not proven merge
conflicts**, and the distinction was made honestly rather than used to argue for a rewrite.

**Triggers to revisit** (any one):
- A real merge conflict in `index.html`, not just contention.
- A pane whose operator-only content has no reason to share the analyst UI's session or
  resource-selection context — **Prefect and Logs are closest**.
- **The open-demo deployment** (see the entry below). That changes the calculus: EA's separate
  `/admin` was discounted as weak precedent partly *because* it is unauthenticated, which stops being
  a reason to dismiss the split and becomes a reason the split needs auth.
- **Trellis-wide admin centralisation** — the project owner's own flag, and the largest version of this. RE, EA and
  Workspaces Portal each have their own admin surface with their own auth posture and their own
  triage vocabulary; the surveys in `docs/feedback-and-curation.md` and
  `docs/feedback-and-curation.md (§7)` are already evidence of three implementations converging
  on the same needs. If admin is going to be shared, extracting RE's into pages first is a
  prerequisite step rather than wasted work.

**What must not be lost in any split** (all with line references in the options doc): Egeria
Alignment's dry-run/confirm split, undetermined-reported-separately-from-clean, expensive repairs
unticked by default, per-action destructive warnings, and fixed apply ordering. These took a full
session to get right and a loose re-implementation would silently lose them.

### Feedback as a signal of where the system is weak, not just of what a user disliked

**Raised by the project owner, 2026-09-01**, and it is a different axis from triage status:

> *"there is another status or point — that what is being reported indicates a gap in either training
> data, rules, routing, or agent behavior — we would want to periodically sweep through this (and the
> chat scores) for continuous improvement"*

Triage answers *what do we do with this report*. This answers *what does this report tell us is
missing*, and the two are independent: a report can be `not_an_issue` for the reporter and still be
the clearest evidence available that routing is wrong.

Proposed as a **second, orthogonal classification** — not more values on `triage_status`, which would
conflate a disposition with a diagnosis. Candidate categories, from the project owner's own list: **training data ·
rules · routing · agent behaviour**, plus an explicit *not-a-system-gap* and an *unclassified* that
is distinct from "reviewed and found to be none of these". Absence must not read as a decision — the
same rule as everywhere else here.

**The sweep is the point, not the field.** A classification nobody aggregates is a dropdown. What is
wanted is a periodic pass over feedback AND chat scores together, looking for concentrations — three
reports blaming routing in one area is a finding that no single report is. Scope should include:
`feedback`, `resource_feedback`, and the chat signal (`chunk_feedback`, now trinary — see
`docs/feedback-and-curation.md`), since a low chat score and a written complaint may be the same
gap seen twice.

Prerequisites, in order: RE has **no way to change any feedback state today** — the gated
`PATCH /api/feedback/{id}` exists and no UI calls it (`docs/feedback-and-curation.md (§7)`).
That must land first, and `/api/curate/feedback` must be gated, before adding a second field that
also needs writing.

Not scoped: whether the classification is made by a person, suggested by an agent and confirmed, or
both; how the sweep is scheduled; and what it produces — a report, a RequestForAction, or backlog
entries.

### Auth posture when RE and EA reach the open demo environment

**Project-owner decision, 2026-09-01: "RE and EA will at some point also be in the open demo environment."** Recorded
because it puts an expiry date on reasoning committed the same day, and that reasoning is now in a
docstring that would otherwise be read as timeless.

`web/admin_auth.py` is fail-closed: absent admin configuration, every admin request is denied. Its
stated justification is that **RE has nothing to defer to** — no multi-user authentication of its
own, and no authenticating layer behind it. That is true of RE today and stops being true in a
public demo.

Egeria Workspaces Portal has already solved this shape, and `demo_feedback_handler.py::_is_admin()`
is the model rather than the counter-example it was briefly mistaken for (see
`docs/feedback-and-curation.md (§7)` §5, framing withdrawn): **two modes — a public demo
requiring an external identity, and a local mode relying on Egeria's own users.** RE will need the
same distinction, and should adopt that pattern rather than reinvent one.

**What this changes about work already done or planned:**

- **`/api/curate/feedback` gating moves from "outstanding" to "required".** It is currently ungated,
  and on 2026-09-01 it was widened to serve the page-level feedback store. Contact fields are
  stripped as an interim (`cb99d72`) — that was sized as a stopgap for a single-operator local app.
  In an open demo it is the difference between a form and a mailing list.
- **The interim becomes insufficient, not merely redundant.** Stripping hides contact fields from a
  listing; it does nothing about who may WRITE. Triage editing (`PATCH /api/feedback/{id}`, gated
  today) and any future admin write must not inherit an ungated sibling.
- **The feedback store holds real submissions with `wants_response` and `consent_to_contact`.**
  Consent given to a local tool is not consent given to a public deployment. Whether existing rows
  may be carried into a demo environment at all is a question for the project owner, not a default.
- **`docs/admin-surface-options.md` recommended staying inline**, partly because EA's separate
  `/admin` is unauthenticated and therefore weak precedent. In an open demo that stops being a
  reason to dismiss the split and becomes a reason the split needs auth — the recommendation should
  be revisited against the demo requirement, not just against file contention.

Not scoped: which identity provider, whether RE and EA share a session, whether the demo runs
read-only, and what happens to the admin token that exists today.

### Reporting levels

**Improvement suggestions as a third reporting level, keyed off who is asking.** Raised by the project owner
2026-09-01 while reviewing `docs/gap-analyses-design.md`. Deliberately deferred; recorded so the
GAP analyses are built without precluding it.

An analysis can report at three levels, and today only two exist:

| level | mechanism | state |
|---|---|---|
| overall finding / score | `project_analysis_findings.label` + `.confidence` | built, used |
| the evidence behind it | `.detail_json`, and Egeria's `AnnotationExtension` | partly built — see below |
| suggestions for improvement | `RequestForAction` annotation | type exists, used ONLY for internal survey errors (`base_surveyor.py:39`) |

**The hook already exists and nothing reads it.** Investigations carry `purposes`, validated against
`ProjectCharter.purposes` (`registry.py:4265-4275`): *Assess, Certify, Deploy, Explore, Learn,
Maintain, Select, Share*. The project owner's point is that a suggestion depends on whether the asker MAINTAINS
the artifact or CONSUMES it — `Maintain` versus `Select`/`Deploy` — and that distinction is already
modelled, validated on write, and consumed by nothing.

So the item is well-formed rather than vague: **drive `RequestForAction` content off the
investigation's declared purpose.** "Add a SECURITY.md" is advice for a maintainer; "this project
publishes no security policy — weigh that in your selection" is the same finding for a consumer.
Same evidence, different action, and issuing the maintainer's version to a consumer is noise.

**What must be true NOW so this stays possible later**, and it is the reason this is recorded rather
than only remembered: **the finding, its evidence and any recommendation must be separately
addressable.** Evidence flattened into a summary string cannot be re-read by a recommender built
later, and the alternative is re-running every survey to get it back. `AnnotationExtension`
(`OpenMetadataType.java:6010`, model 0610 — *"Additional information to augment an annotation"*) is
the modelled way to link a summary annotation to the evidence annotations behind it. **RE has never
created one.**

`AnnotationReview` (model 0612, `OpenMetadataType.java:6020`) is the adjacent type for a stewardship
review of an annotation, and is the more likely home for an accepted/rejected suggestion than a
bare RequestForAction.

Not scoped: whether a purpose maps to one recommendation set or several, what happens when an
investigation declares five purposes at once (common — one live investigation declares eight), and
whether a recommendation is itself a finding with a lifecycle or a rendering of one.

### Test reliability

#### `test_survey_definition_generator_guard.py` mutates the real docs directory, and is only safe alone

Found 2026-09-01, in the working tree rather than by a failing test — `git status` showed
`.generated.json` **deleted** and `repo-survey-definition-assessment.md` carrying a mechanical
`"Assessment Survey"` -> `"Assessment Survey X"` rename that nobody had made.

Both come from the test file itself. It operates on the **real**
`docs/dr-egeria/survey-definitions/` directory, not a `tmp_path` copy:

    DEFS = Path(__file__).resolve().parent.parent / "docs" / "dr-egeria" / "survey-definitions"
    target.write_text(original.replace("Assessment Survey", "Assessment Survey X"))   # :91
    PROVENANCE.unlink(missing_ok=True)                                                 # :108

Its `restore` fixture snapshots every document plus the sidecar and puts them back, and **is correct
in isolation**. The failure needs two runs: session A snapshots, session B snapshots *A's mutated
state*, A restores, B restores what it captured — and the tree keeps a snapshot of a half-mutated
directory. With the sidecar gone, every definition then looks hand-edited, so the guard tests fail
for a reason that has nothing to do with the code under test.

**It is not a flaky test. It is a test whose correctness depends on being the only one running**,
and nothing declares that property. Three sessions running suites in one shared checkout is now
routine, so this will recur.

Fixes, roughly by cost:

1. **Copy the directory to `tmp_path`** and point the generator at it — the generator already takes
   paths, so this is mostly fixture work, and it removes the shared-state dependency entirely.
2. **A file lock** around the module, so concurrent runs serialise rather than interleave. Cheaper,
   and leaves the tree mutated while it runs — a `git status` mid-suite still lies.
3. **Leave it and document it.** Current state: correct alone, silently wrong concurrently.

Recovering from an occurrence is `git checkout --` on the two paths, after reading `git status` for
them — the damage is confined to that directory and is always the same shape.



**`test_local_flow_execution_fallback` failed once and has not reproduced.** Seen 2026-08-31 in a
full run on `c650df6`: 3075 passed / 1 failed. An immediate rerun of the same command, same commit,
same `-p no:randomly`, gave 3076 passed / 0 failed. The test passes alone (8.3s) and passes as a
whole file.

**Status: open, cause unknown, one observation.** Recorded rather than closed because a fix that
did not reproduce the failure cannot be shown to have fixed it.

What has been ruled out, so nobody re-walks it:

- **Not port contention.** The test is pure mocks and binds nothing. The port in the Prefect noise
  comes from Prefect's own `prefect_test_harness`, not from our code.
- **Not "a non-opted-in module poisons Prefect's cached client first."** This was the leading
  hypothesis and it explained every symptom — passes alone, passes per-file, fails in the suite.
  It was falsified: first-wins on a fixed sequence predicts a *deterministic* failure, and two
  fixed-order runs disagreed. The mechanism it described is real (the fallback at
  `prefect_adapter.py:153` calls the real `re_survey_flow`, using Prefect's own client rather than
  the patched one) — it just is not what happened.
- **Not a Prefect server left running by a concurrent session.** Reported as evidence and then
  withdrawn: the "prefect processes" were `ps | grep "[p]refect"` matching the reporting session's
  own `zsh -c` wrapper line. The bracket idiom stops grep matching itself; it does nothing about the
  shell carrying the pattern as an argument.

Do **not** read `Stopping temporary server on http://127.0.0.1:<port>` or `ValueError: I/O
operation on closed file` as a reproduction signature. Both appear in runs where this test passes;
they are Prefect teardown noise.

If it recurs, capture the assertion text — not a `tail` of the run, which buries the summary under
Prefect's teardown logging.


**`test_a_loop_that_loses_the_election_is_never_started` — FIXED 2026-09-08, and
the diagnosis below was wrong.** The fix is fix (1): the test now polls for the
`standby` line instead of sleeping a fixed 0.2s. Six consecutive runs pass.

**What the original diagnosis got wrong.** It blamed `_ensure_draft_zone`'s live
Egeria call for the latency. Measured after stubbing that call out: `standby`
still lands at **~1.06s**, against ~1.13s unstubbed. The Egeria call was worth
about 70ms and was never the cause — the rest of worker startup is, and the
fixed 0.2s sleep was simply always too short. The test passed only when thread
scheduling happened to favour it.

The plausible story (a real network call in a unit test, timing that varies with
platform load, a run that failed right after hammering that platform) fitted
every observation and was still wrong. It survived because nothing forced it to
predict something checkable — stubbing the call took two minutes and falsified it
immediately. `_ensure_draft_zone` is now stubbed anyway, on its own merits: a
unit test of leader election should not reach Egeria.

*Original entry, kept for the record:*

**`test_a_loop_that_loses_the_election_is_never_started` races a live Egeria call.**
Found 2026-09-07: passed in one full run and failed in the next, on a working tree whose
changes could not reach it (`worker.py` unmodified; no import path from the changed
modules). That pattern reads as "you broke it" and is worth the two minutes to disprove.

**The behaviour under test is correct.** `started == []` holds, and the `standby` line
*is* logged — `worker.py:295`. The test does a fixed `time.sleep(0.2)` and then asserts
`"standby" in caplog.text`. Polling for the condition instead of sleeping past it measured
the line appearing at **1.13s**, against the 0.20s the test allows.

The variance is not in our code. `run_worker` calls `_ensure_draft_zone()`, which makes a
**real Egeria call** on startup — the captured log carries
`draft-zone bootstrap: {'status': 'exists', ...}` — so how long the worker takes to reach
the standby branch tracks how busy the platform is. The failing run followed a session that
had been creating and deleting Projects against that same platform.

So it is a genuine flake, and specifically a **fixed-sleep race against network latency** —
not the shared-checkout hazard the two entries above describe, and not reproducible by
running it alone on a quiet platform.

Fixes, by cost:

1. **Poll instead of sleeping.** Wait for `"standby" in caplog.text` (or for the loop's own
   start/standby decision) with a generous ceiling, rather than sleeping a fixed 0.2s and
   asserting once. Removes the dependence on Egeria's latency entirely, and keeps the test
   fast in the common case.
2. **Stub `_ensure_draft_zone` in the test**, as it already stubs `_reconcile_orphaned_runs`
   and `_warm_survey_definition_cache`. Cheapest, and arguably what the test meant — a unit
   test of leader election should not be talking to Egeria at all. Leaves any other
   startup-latency source unaddressed.
3. **Leave it.** It fails roughly when the platform is under load, which is exactly when
   somebody is most likely to misread it as their own regression.

(2) then (1) is the natural pair. Not done here because the file belongs to no current
change and a concurrent session was mid-edit in the same package — see the git rules in the
repo `CLAUDE.md`.


### Private zoning — what Phase 5 left open

**Anchoring (design Phase 4) is DONE — 2026-09-08.** Annotations now anchor to
their SurveyReport and inherit its zones. Measured: enforcement reads the LIVE
anchor, so re-zoning a parent moves every anchored child with no sweep. It found
two live Phase 5 bugs on the way in (public annotations; the shared repo asset
being zoned private) — see the design doc's Phase 4 entry.

**Still un-anchored, and worth a look:** the investigation's Folio/WorkingSet
Collection, and the arch-recovery materializer's SolutionComponents, are each
their own anchor. The components are stamped directly (correct but N-shaped);
the Folio is not stamped at all, so a private investigation's collection is
world-readable — it holds the membership list, not findings, but it does name
the investigation. Anchoring both to the investigation Project would fold them
into the same one-property invariant.

**Freshstart has never run this.** Everything was verified on quickstart, whose
Coco Pharma directory happens to make RE's own account a platform operator. On a
stock freshstart no human account holds `serverOperator`, so
`ensure_private_zone_exists` will return `not_authorized` and private
investigations will refuse to publish — which is the designed-safe behaviour,
but it means the feature is unusable there until someone grants the right. The
deployment-side grant belongs in `egeria-workspaces-fs`. See §3.3a of the design.

**Existing private artifacts are not retro-zoned.** Anything published before
Phase 5 from what is now a private investigation stays in whatever zone it got.
A backfill would need to enumerate them and re-zone; nothing does that yet, and
nothing reports how many there are. Worth at least a count, so the gap is
visible rather than assumed empty.

**The settle window is a per-process belief, not shared state.** Each worker
process learns the control exists at its own startup. A process that creates the
control waits `PRIVATE_ZONE_SETTLE_SECONDS`; a process that starts afterwards
sees it already present and trusts it immediately — correctly, since it predates
that process. But two processes starting within the same window can disagree
about whether private publishing is available. Harmless (the disagreement is
between "refuse" and "allow" on a control that is genuinely settling) but
surprising if someone hits it.


### Private zoning — the one thing users will hit

**An investigation promoted into the publish zones cannot be made private again.**
Not "needs care" — cannot. Moving an element out of a zone requires
`AccessOperation.PUBLISH` on its ORIGINAL zones, and `egeria-runtime`'s security
list does not include RE's Egeria account. Confirmed live 2026-09-08:
`OPEN-METADATA-SECURITY-403-005`. The reclassifier reports it precisely and
refuses to change the classification, so nothing lies — but the work stays
public.

The reverse direction is fine: RE can move out of its own private zone because
the `userId` entry satisfies the PUBLISH check.

So privacy is effectively a decision to make **before promotion**. Options if
that turns out to bite:

1. **Grant RE's account PUBLISH on the publish zones** — a deployment change
   (`associatedSecurityList`, `PUBLISH` or `DEFAULT` key), and the one that makes
   the feature symmetric. It also widens what RE can do to elements it did not
   create, which is why it is not the automatic answer.
2. **Have an operator do the move.** `garygeeke` and `peterprofile` hold the
   rights; this is what unstuck the test elements. Fine for a rare correction,
   not a workflow.
3. **Surface it at creation** — say plainly, when a shared investigation is
   promoted, that it cannot be made private afterwards. Cheapest, and honest.

Nothing decided; the owner should pick. (3) is worth doing regardless.

### The Investigation marker — decided and BUILT 2026-09-08

**Approved by the project owner directly** ("RE should apply the Investigation
marker itself") and built once the redeploy made the type available. RE stamps it
on every Project it promotes, as a separate classify call after the create so a
platform lacking the type loses the marker rather than the investigation.

*Original entry below, kept for the reasoning and the not-yet-approved framing it
was written under.*

### The Investigation marker — decided, and what is NOT built

**Decision (project owner, 2026-09-08):** the Egeria classification
`Investigation` is an orthogonal MARKER — "this Project is an investigation" —
that coexists with the kind (PersonalProject / Task / StudyProject / Campaign /
Experiment). It does not replace the kind and does not drive zones. Relayed via
dwolfson-fe; the owner's words: *"it does not need to collide, you can have both
Investigation and PersonalProject."*

**Already safe.** The reclassifier removes the old kind BY NAME, gated on
`PROJECT_CLASSIFICATIONS`, so an `Investigation` marker survives a change of
kind. Verified live for the equivalent property (`Anchors`, `Ownership` and
`ZoneMembership` all survived a `Task` -> `PersonalProject` swap), and pinned by
two tests.

**NOT built, and NOT approved — needs the owner's decision, not a relayed
suggestion.** dwolfson-fe raised that RE could apply the `Investigation` marker
itself when it creates or promotes a Project, so every RE-owned Project carries
it rather than only the ones classified by hand through Dr.Egeria. That is
reasonable and cheap — one more entry in `initialClassifications` — but it is a
new behaviour that writes a new classification to every Project RE creates, so
it wants an explicit yes.

**Blocked on the redeploy either way.** `Investigation` does not exist on the
running platform:

    Investigation              -> not found
    NamingStandardsVocabulary  -> not found
    StudyProject               -> FOUND        <- control, so the lookup works

The four new Dr.Egeria Curation templates targeting them will fail until the
owner redeploys with the latest Egeria ("when everyone is ready"). Applying the
marker before that redeploy would fail every create.

### The private zone does not survive a redeploy — FIXED 2026-09-08

**Resolved:** the `resource-explorer-private` control is now in BOTH compose-config
seeds, so it is recreated on any redeploy and exists on a fresh machine. This is
what makes the feature work on **freshstart**, where RE cannot create the control
itself. Survived the 2026-09-08 redeploy and was live-verified still denying
afterwards.

*Original entry below, kept because its analysis of the failure mode is still the
reason the fix matters.*

### The private zone does not survive a redeploy (and that is mostly fine)

Measured 2026-09-08, ahead of the owner's planned redeploy:

* `resource-explorer-private` exists **only** in
  `egeria-workspaces-fs/runtime-volumes/quickstart-platform-data/secrets/coco-user-directory.omsecrets`,
  which the compose file bind-mounts to `/deployments/secrets`.
* That path is **gitignored** (`.gitignore:215`), so the control is not in
  version control and does not exist on a fresh machine.
* The compose-config **seed**
  (`compose-configs/egeria-quickstart/secrets/coco-user-directory.omsecrets`)
  does **not** contain it — 0 occurrences, against 1 in the live file.

So a redeploy that repopulates the volume loses it.

**Nothing has to be held for the redeploy.** The design fails safe: with the
control gone, `private_zone_is_enforced()` is False and private publishing
refuses loudly rather than leaking. And on **quickstart** it self-heals —
`ensure_private_zone_exists()` recreates the control because RE's account holds
platform-operator rights there.

**Two things to expect anyway:**

1. **A ~12 minute window after the redeploy** in which private publishing is
   refused while the security connector reloads its secrets store
   (`PRIVATE_ZONE_SETTLE_SECONDS`). By design, but it will look like a bug to
   whoever hits it first.
2. **On freshstart it will NOT self-heal**, because no human account there holds
   `serverOperator` (§3.3a). Private investigations simply will not publish until
   someone grants it.

**The fix, if wanted:** add the `resource-explorer-private` control to BOTH
compose-config seeds. It then survives redeploys, exists on a fresh machine, and
— the real win — makes the feature work on freshstart, where RE cannot create it
at all. That is the deployment change §3.3a already called for. It is a security
config file in another repo, so it is written down here rather than done.

Existing zoned artifacts need no sweep: a metadata-store wipe removes the
Projects and reports outright, and RE republishes and zones fresh.

**`test_the_real_egeria_checkout_yields_dependencies` asserts against a checkout
that moves.** Started failing 2026-09-08 between two full-suite runs an hour
apart, with only unrelated commits in between: *"only 36 of 72 gradle
dependencies resolved a version — same-file ext-variable resolution appears to
have regressed"*.

Nothing in the parser changed. `~/localGit/egeria-v6/egeria` is now at
`f5ae3c79ec` (a recent upstream merge), and the test reads that working tree
directly — so its expectations (`> 50` deps, `> 50` versioned, "from 229
build.gradle files") are pinned to whatever upstream Egeria looked like when they
were written. Upstream restructures its build files and the numbers move.

Not established: whether 36/72 means the parser genuinely lost ext-variable
resolution, or upstream simply has fewer inline-versioned dependencies now. Those
need distinguishing before anyone "fixes" the parser — the failure message asserts
a regression it has not demonstrated, which is the same shape as the other
entries here.

Options: vendor a small fixture tree and assert against that (loses the
real-corpus signal but is stable); keep the real checkout but assert a RATIO
rather than absolute counts; or record the checkout commit the numbers were
measured at, so a future failure says "upstream moved" rather than "the parser
regressed".

### Architecture recovery

#### MEDIUM — telemetry for surveys, and the LLM-based survey step

*(Opened 2026-08-30, from the decision-trace work — `architecture-recovery.md` §18 and
`architecture-recovery-extraction-design.md` §5.)*

The decision trace is now persisted as findings, and that doc argues it should **not** go to
MLflow, Phoenix or OTEL: decision provenance is read months later by resource and must be durable
and queryable, while telemetry is sampled, retention-limited and keyed by trace id. **That part
stands.** Two things around it do not, and want revisiting.

**1. "Surveying is deterministic Python" is false as a general claim** (project owner, 2026-08-30). It is
true of `repo_arch_detect` and `repo_arch_coupling`, which is all the original reasoning had in
front of it. **A survey step can perform LLM-based analysis**, and where it does:

- Phoenix/`BeeAIInstrumentor` instrumentation *is* directly relevant to that step — there is a real
  model interaction to trace, with prompt, tokens and latency;
- the step is **non-deterministic**, so the reproducibility argument that holds for the two
  deterministic steps does not transfer;
- there are then **two traces to keep apart**, not one: the model interaction (Phoenix/OTEL) and
  the decision it produced (durable, per-resource, `architecture_decisions`).

§16 of `context-compilation-design.md` already makes the matching argument one layer up — agent
output must be *written down, versioned and provenance-stamped before it is packable*, precisely
because agents are non-deterministic. A survey step that calls a model needs the same discipline,
and the decision trace is the natural home for the written-down half. Worth checking whether any
step already does this and is currently unprovenanced.

**2. Execution telemetry for surveys has no home and no owner.** Per-step and per-detector timings,
failure points, hot paths. OTEL is the right shape and nothing emits it. Not urgent — there is no
open performance question, and the one measurement that mattered
(`_withdraw_vacated` at 93ms over 10,135 rows) was answered with `time.perf_counter()`. Open it
when there is a question to answer, and note the scaling item below is the likeliest trigger.

**If tracing is added, the shape to use** is a span per survey step carrying the *finding id* — a
pointer to the durable record, not a second copy of it. Dual-writing means two stores that can
disagree, and the span copy is the one that expires.

#### ~~HIGH — take architecture results into Curate~~ — DECIDED AND BUILT 2026-08-30

*(Opened 2026-08-30 listing four candidate shapes and saying "none of this is designed yet".
The project owner chose the first and S1 built it the same day. This entry was left stale for several hours and
was still being reported as an open design question when it was neither — corrected on the project owner
noticing. Then corrected AGAIN by S2, who had built the backend and pointed at the differing
`Claude-Session` trailers on `6f3afeb` and `2a22c99` to prove it. Verified before accepting. Two
attribution errors on one row, both from reading a summary instead of the commits — which is exactly
what `re-multi-session-attribution` says not to do.)*

**The shape: accept / reject / retype a proposed component.** The pipeline is explicitly a
*proposal* (§4.1a, `report-then-curate`) and a curator's verdict was the missing half. It rides the
Confidence/ContentStatus axis (§3.3b/§3.4) rather than introducing a vocabulary.

Landed in three slices:

| | |
|---|---|
| `6f3afeb` | accept/reject/retype on a proposed component — the backend (`web/routes/curate.py`, the `architecture_component_verdicts` table, `registry.py` methods) — **S2** |
| `2a22c99` | verdicts wired into the architecture card — **S1** |
| `f34d3c5` | **accepted proposals materialized as real Egeria `SolutionComponent`s** — **S1** |

That third one is the one that closes the loop `report-then-curate` opened: a proposal a human
accepted stops being a local finding and becomes a catalog element.

**Still open from the original four**, and genuinely undesigned rather than merely unclaimed:

- **Correct a name.** The live case is still the best argument: the disambiguator renamed Atlas'
  main distribution config to `distro` because six modules shared the token `atlas` — unique and
  truthful, and not what a curator would choose. No rule can know which member of a collision
  deserves the shared name.
- **Curator notes at component scope.** `resource_curator_notes` is whole-resource; architecture
  recovery is scope-keyed throughout.
- **Promote a reviewed set toward publication** — the ContentStatus ladder `report-then-curate`
  describes and nothing yet walks end to end.

See also the reflexion-vocabulary entry below: convergence/divergence/absence is a ready-made naming
for the verdict axis, and worth reading before the next slice invents its own.

#### HIGH — `architecture_recovery` costs 110s to fetch and 5.9s to run — fix the acquisition

*(Opened 2026-08-30 as a tier question; **reframed the same day by profiling**, which killed three of
the four options it originally listed. The project owner's steer: the work likely goes into the analysis
implementation, not the catalog.)*

**The analysis was never slow.** Profiled on `egeria_python_git`:

| | |
|---|---|
| compute, against a local checkout | **5.9s** — detect 3.1s, coupling 2.8s |
| `zipball_root` + `git_clone_root` acquisition | 15.7s |
| **the same two steps via `SurveyOrchestrator`** | **110.5s** |

`architecture-recovery.md (§13.2)` §3's **"5.3s per repo"** — the figure CLAUDE.md rule 17
cites to justify Discovery placement — is **correct and still holds**. Nothing regressed. The cost
is entirely in *how the route acquires the repo*.

**Where it goes.** cProfile over exactly what the route calls:

```
738 calls    95.1s   {method 'poll' of 'select.poll' objects}    <- waiting on git subprocesses
36569 calls  12.2s   {method 'read' of '_ssl._SSLSocket'}        <- network, inside the profile
```

Same step, same repo: **2.8s against a local checkout, 92.1s through the orchestrator.** The
difference is `_acquire_git_clone_root`'s `--filter=blob:none --no-checkout`. Co-change analysis then
runs `git` history commands against a **treeless** clone, and git lazily fetches from the remote for
anything absent — so each operation pays network round-trips. Our `select.poll` time is git's
network time.

**Two candidate fixes, neither decided:**

- **Cache the acquired roots.** Both providers clone into a *fresh tempdir every run*
  (`_acquire_zipball_root`, `_acquire_git_clone_root`), so a repo is downloaded twice per run, every
  run, forever. A cache keyed on commit SHA would make the second run of anything nearly free — and
  it would benefit every step declaring these resources, not just this one.
- **Give co-change what it actually needs.** It wants commit metadata and pathnames, which a
  treeless clone *has*. Something is reaching for blob content and triggering the lazy fetch;
  finding what would be a smaller, more surgical fix than caching.

Worth doing the second first: it is diagnostic, and its answer determines whether the first is a
performance nicety or the only option.

**The instrumentation that flagged this is misattributing, and that is its own small bug.** The run
emitted:

> `repo_arch_coupling — declares compute_cost='medium' (ceiling 60s) but took 92.1s with no
> connections, so that is compute`

It is 92.1s of *network*, in a child process, invisible to whatever counts connections. So the guard
built to catch exactly this case reported the opposite and nobody was reading the line anyway.

**What this does NOT need.** The entry originally offered four options; the measurement leaves one:

- ~~re-tier out of Discovery~~ — the analysis *is* Discovery-cost at 5.9s
- ~~re-map the Discovery question to something cheaper~~ — same reason
- ~~decouple `availability` from `run_time`~~ — `run_time` was never wrong about compute
- **fix the acquisition** ✅

`run_time: fast` therefore stays, and is now *defensible* rather than merely unchanged — with the
caveat that it describes compute while a user experiences compute **plus** acquisition. If the
acquisition fix lands, the two converge and the question disappears. If it does not, the honest tag
is about the whole experience and the tier question comes back.

Open, unresolved: whether `architecture_recovery` belongs in the **Analysis** intent rather than
Discovery on other grounds — that is a separate judgement from cost, and cost no longer forces it.

**Resolved the same day, separately, by S1 in a different session:** the project owner ruled directly —
"architecture recovery is an analysis step and belongs there." `intent: analysis` now, `run_time:
fast` unchanged (this entry's reasoning above stands; the ruling was on tiering grounds, not cost).
Recorded together in `analysis_catalog.yaml`'s entry so neither change reads as having overridden
the other.

**Still open and unclaimed as of 2026-08-30 (S1):** both candidate fixes above (cache the acquired
roots; give co-change what it actually needs). S1 is coordinating with S2 before claiming either —
see cross-session note, same date.

**"Give co-change what it actually needs" — SOLVED, same day, by dwolfson-59** (reported via S2,
not yet merged into `ui/architecture-focus`): the answer was `git log --name-only`'s **default
inexact rename detection**, which scores blob-content similarity and is exactly what defeats
`--filter=blob:none` — 86 lazy fetches, confirmed by a packet trace. Fix is `--find-renames=100%`:
an exact rename compares blob OIDs already present in the tree, so it costs nothing extra, while
inexact detection has to fetch and diff content. Commit `63e7ec6` on `re/deferred-cleanup-followups`
(`cochange.py` only), merged into `re/survey-flows` at `d9e619f`.

Reported new measurement (dwolfson-59, via S2 — not independently re-run by S1): acquisition now
dominates the route's remaining cost rather than the reverse — 86% of `egeria_python_git`'s total for
`repo_arch_coupling`, 61% for `docling_parse`. **This dissolves the tiering question further than
this entry's own "the two converge and the question disappears" anticipated** — the cache-the-roots
fix above is now the more clearly load-bearing of the two remaining candidates, since the per-run
network chattiness this fix closed was the bigger of the two costs the earlier profiling found.

**Cache the acquired roots — DONE 2026-08-30.** Built by dwolfson-59 (not S1 — a three-way crossed
assignment: the project owner gave it to S1 directly, S2 separately told dwolfson-59 to take it after dwolfson-59
flagged it as provider-shaped. Sorted between the three sessions before either duplicate build
started: dwolfson-59 finished it, S1 reviewed rather than rebuilding.

`resource_explorer/github/source_cache.py` — `SourceCache`, SHA-keyed, 4 GiB LRU budget, atomic
rename on write (two racers both do the work, one wins the rename, loser's copy is discarded).
Caches the **artifact** (`.zip`, treeless clone) rather than the extracted/checked-out directory —
every run still gets its own private tempdir via extraction or `git clone --local`, so concurrent
surveys cannot see each other's mutations. That boundary matters more than usual for a git clone
specifically: `git log` writes to `.git` for its own bookkeeping, so a shared clone would be a
corruption risk, not merely a leak. Keyed on the commit SHA (one extra API call, ~0.49s) rather than
the repo, so a stale hit is structurally impossible rather than merely unlikely; when the SHA can't
be resolved, or `shallow_since` is given (a bounded clone must never share a key with an unbounded
one), the cache is bypassed entirely and the old uncached behaviour runs unchanged.

Measured (dwolfson-59, `odpi/egeria-python`): acquisition 22.64s cold → 1.28s warm. Full route for
`egeria_python_git`: 110.5s originally → 30s after the rename fix above → **14.4s** now.

**S1's review** (cherry-picked as `a7e5364` on `ui/architecture-focus`, from `f8710eff` on
`re/deferred-cleanup-followups`):

- Confirmed the two load-bearing design calls are right: artifact-not-working-directory for
  isolation, SHA-keying for correctness. Would not have designed it differently.
- Found and fixed one real regression while closing the test-coverage gap dwolfson-59 flagged
  themselves (their two pre-existing provider test files route through the *uncacheable* path only,
  by design, so the cached path integration was untested): the cached branch of `zipball_root()`
  built `root / subproject_path` with **no existence check at all**, silently handing a caller a
  non-existent directory instead of `download_zipball()`'s `ValueError` listing available
  directories. Added `TestZipballRootCaching`/`TestGitCloneRootCaching` (9 new tests) exercising the
  actual integration seam — cache hit, cache miss, two-calls-share-one-download, and the
  `shallow_since`-bypasses-caching guarantee — plus the regression test that caught the bug above.
- **Eviction race — FIXED, same day, after dwolfson-59 pushed back on how "narrow" S1's first pass
  called it.** `_evict()`'s LRU sweep could delete a **directory** entry (a cached treeless clone)
  while a concurrent `local_clone()` was still hardlink-copying from it — `shutil.rmtree` mid-walk
  against files another process is reading. Zipball entries were always safe from this via POSIX
  `unlink`-of-open-file semantics; directory entries were not. S1's first framing ("requires the
  cache to be genuinely over budget AND mid-read at that exact moment") understated it: at ~50 MB
  per zipball, the 60-repo corpus is ~3 GB against the 4 GiB default — **over budget is the steady
  state once the cache fills, not an edge case** — and concurrent surveys are normal now, not
  hypothetical. Fixed with `eviction_grace_seconds` (default 60s): an entry touched more recently
  than that is never an eviction candidate, even if it is the oldest remaining and the cache stays
  over budget as a result — cheap, since `get()`'s touch is instant and the actual use
  (extraction/`clone --local`) measures 0.28-0.39s, so a generous window costs nothing in the case
  that matters. `total_bytes()` still counts protected entries, so budget accounting stays honest.
  Two other gaps stay open, unfixed, same category as before: no age bound/per-repo cap beyond
  overall LRU, and no invalidation on a tag/branch pointer moving under an already-held SHA.

Suite: 3033 passed + 9 new, 10 skipped (dwolfson-59's count plus S1's additions; not independently
re-run against the full corpus).

#### ~~MEDIUM — acquisition is now the whole cost~~ — SOLVED 2026-08-30

*(Restored: this entry and the one below were lost when a `--theirs` conflict resolution on this
file during the `ui/architecture-focus` merge dropped both. Second casualty of the same
cherry-pick-then-merge; found only by going looking for one of them.)*

After `63e7ec6` removed the blob-fetch cost, the download was essentially the entire wall-clock —
86% of the route for `egeria_python_git`, 61% for `docling_parse` — because both providers cloned
into a fresh tempdir every run. **Solved by `f8710ef`'s `SourceCache`**: acquisition 22.64s → 1.28s
warm, full route 110.5s → 14.4s. See the entry above for the design.

#### ~~LOW — `_COCHANGE_MAX_FILES = 50` is unvalidated~~ — MEASURED 2026-08-30, keep it

*(Opened by S2's review of `63e7ec6`; closed the same day by measuring it. The cap is defensible —
but it does not do what its name suggests, and that is the part worth keeping.)*

**Distribution**, four repos with real history. The six `--depth 1` clones pulled today were
excluded: a shallow checkout has one synthetic commit holding the entire repo, and DataHub's
19,009-file entry alone produced about half the uncapped pair total on the first pass.

```
p50   6 files      p90   55      max 2796
p75  16            p95  126
                   p99  308
```

**50 lands almost exactly on p90** — it keeps 89.8% of pair-bearing commits. Not arbitrary,
whatever its provenance.

**The quadratic case for *a* cap is overwhelming** — the last 10% of commits carry **98.8% of all
pairs**:

| cap | commits kept | pairs | % of pairs |
|---|---|---|---|
| 25 | 81.9% | 28,175 | 0.4% |
| **50** | **89.8%** | **85,835** | **1.2%** |
| 100 | 93.4% | 188,436 | 2.6% |
| 500 | 99.6% | 1,810,139 | 25.1% |
| none | 100% | 7,201,804 | 100% |

### The finding that matters: it is a cost control, not a quality control

Pairs are not the output — components are. Varying the cap and re-running `coupling.propose`:

| cap | egeria | egeria-python | egeria-workspaces |
|---|---|---|---|
| 25 | 703 | 52 | 72 |
| **50** | **728** | **61** | **82** |
| 100 | 743 (+15, −0) | 64 (+3, −0) | 96 (+14, −0) |
| 500 | 820 (+92, −0) | 82 (+21, −0) | 121 (+39, −0) |

**Raising the cap is purely additive — `−0` at every level.** Nothing proposed at 50 disappears at
500. So it is not separating signal from noise, as "skip the huge refactor commits" implies; it is
a volume limit. Across a **20× range** of cap, egeria's components move 703 → 820 (±13%) while
pairs move 4,151 → 597,225 (**144×**).

- **Raising it makes readability worse, not better.** egeria is already at 728 components against a
  clustering target of ~10 per blueprint.
- **The cost argument is weaker than it looks** at these sizes — even cap 500 on egeria runs in
  1.2s. What the cap buys is bounded *pair* growth for the quadratic tail, not wall-clock.

**Verdict: keep 50.** It sits on p90, it is monotone-subtractive so it cannot hide a boundary a
higher cap would reveal *differently* (only *additionally*), and component count is nearly
insensitive to it. What was genuinely unvalidated was the *reason* — the comment implies it filters
noisy commits, and it bounds volume instead.

**Honest limit:** four repos, all Egeria-family, and per-repo variance is large — at cap 50 egeria
drops 32% of its commits while egeria-workspaces drops 2%. A single global cap treats those very
differently. If anyone revisits this, a per-repo cap (that repo's own p90) is the shape to test, on
a corpus that is not four repos from one family.

#### MEDIUM — the analysis-card Run gives no prompt and no progress for slow work

*(Opened 2026-08-30, live-reported: "pressing the architecture survey button does seem to start the
task but it doesn't bring up the pop-up that asks if we should run this in the background so it's
easy to miss the toast".)*

Two different run paths exist and the analysis card has the weaker one:

| path | behaviour |
|---|---|
| **Survey Definition** (`showSurveyDefRunModal`) | modal with elapsed-time progress, backgrounds the run, polls the activity entry, relabels its button `Close — keeps running in background →`, and toasts *"can take a while — check 📋 Activity if you navigate away"* |
| **Analysis card** (`_runAnalysisCatalogCard`) | fires the POST, shows one `running` toast, and **blocks for the whole run** — no modal, no progress, no activity handle |

A contributing cause is a catalog value that is measurably wrong and **deliberately not changed** —
see the entry below.

That fix does not close this entry. The analysis-card path still has no backgrounding for anything
tagged `minutes`/`async`, and `POST /analyses/{id}/run` blocks rather than returning an
`activity_id` the way `/survey-definitions/{type}/{slug}/run` does. The work is to give the
analysis-card path the survey-definition path's shape — which is a route change plus a modal, not a
toast tweak.

#### MEDIUM — a compiled answer should be able to POINT at a view, not only describe it

*(Opened 2026-08-30. Project owner: "there is no reason why, in some cases, it can't provide a link to an
architecture view elsewhere as well as providing a textual description.")*

Not a UI affordance — a change to **what a `Section` can resolve to**. Today every section resolves
to text that gets packed against a character budget (`trellis_context`'s `Candidate` carries
`{Rung: str}`), so the only way for an architecture question to reach the architecture view is for
the compiler to *describe* it in prose and hope the reader goes looking.

A section that resolves to a **pointer** — resource, analysis, perspective, and the scope to focus —
is different in three ways worth designing rather than bolting on:

- **It costs almost no budget.** A link is tens of characters where the prose summary of egeria's
  deployment architecture is hundreds. §9's packer currently trades detail against budget; a pointer
  section changes that trade, since the expensive thing lives at the other end of the link.
- **It stays correct as the data changes.** Packed prose is a snapshot; a pointer resolves against
  whatever the view shows now. That cuts both ways — it breaks the §10/§14 replayability guarantee
  (`same spec + same as_of + same materialized state -> same context`) unless the pointer carries
  `as_of` too, which is the interesting design question here.
- **It needs the target to exist and be addressable.** The architecture card now has perspective
  tabs, so "the deployment view of egeria_git" is a real thing to point at — it was not before
  today. Deep-linking to a perspective/scope is the prerequisite work.

Both halves are wanted: prose for the model to reason over, link for the human to go and look.
Likely shape is one section carrying both rungs — a short description at FULL, and the pointer as a
sibling field rather than a competing candidate — but that is a guess and the packer's contract
should decide it.

Related: `context-compilation-design.md` §23 (what this looks like in RE and EA) and §20 (resolvers
are mostly not RAG — a pointer resolver is about as far from RAG as a resolver gets).

**Status, 2026-08-30: the compiler half is built** (`trellis_context.packer.Pointer`,
`tests/test_packer.py::TestPointer`). Resolved as guessed above — pointer as a sibling field on
`Candidate`/`PackedSection`, never a competing candidate, and it does reach both halves: its
rendered form (`resource=… view=… as_of=…`) is appended to the packed text for the model, and the
structured `Pointer` travels on `PackedSection.pointer` / the manifest's `packed[].pointer` for a
UI to render as a real link. Sized at a small constant cost per candidate (added at every rung, so
it counts against the ceiling but never changes which rung is chosen — `_size()` in `packer.py`).
`as_of` is set from the pointing analysis's own `surveyed_at`, not compile time — same fact-vs-read
split `_provenance` already draws.

Wired for exactly one analysis so far (`context_compile.py`'s `_POINTABLE_VIEWS = {
"architecture_recovery": "architecture"}`), because it is the only one with a real view to point
at today. **What's still open, and it's the harder half:** RE's UI has no deep-linking at all — no
hash routing, no way to open the architecture card at a given perspective/scope from a URL. The
compiler now emits `{resource_slug, view, perspective, scope, as_of}` in a stable shape; turning
that into a clickable link is UI work in `index.html`, not `trellis_context`.

#### MEDIUM — presenting architecture recovery: a curator sees 20 of 1035 components

*(Opened 2026-08-30. Evidence and three costed options in
`architecture-recovery.md (§17a)` — findings only, no design chosen.)*

`egeria_git`: 1035 components recovered, 451 after depth projection, **20 rendered**, chosen
alphabetically by `path`. Four findings, in the order they are worth fixing:

1. **The `structural` flag is computed and ignored.** `_architecture_recovery_results` marks
   grouping nodes explicitly *so a consumer can render them as grouping rather than as a recovered
   component* — and no consumer reads it. They render as `untyped · 0%`, identical to a component
   we know nothing about, and because rows sort by `path` the **top line of Egeria's architecture is
   a placeholder for the repo root**. 75 of 451 rows. Two-line fix that still needs a visual
   decision, which is why it was not made in passing.
2. **Ordering is alphabetical**, so the clean 8-component deployment reading is in the payload and
   invisible behind 341 logical rows.
3. **Perspective is neither shown nor filtered on**, though §4.1 is emphatic the four are not
   interchangeable. Recommendation in the doc: perspective tabs defaulting to the smallest non-empty
   perspective — a comparison, not a threshold.
4. **Stale rows render identically to live ones** — resolved by tombstoning steps 1–3 for future
   runs, but the UI still has to choose between hiding a withdrawn component and showing it marked,
   and those are different answers for auditing history versus reading current state.

Deliberately measured and not designed: presentation is a product decision, and raising the row cap
treats a symptom when the problem is that they are the wrong 20.

#### MEDIUM — tombstoning step 4: backfill the orphans no run can ever withdraw

*(Opened 2026-08-30. Steps 1–3 are built; see `architecture-recovery.md (§16)`.)*

R2 forbids withdrawing rows that no step is recorded as having written, and **every existing orphan
predates `run_label`** — measured, `_scopes_last_written_by` returns 0 scopes for `egeria_git`
today. So ordinary runs can never clear them:

```
egeria_git, all perspectives    870 live   165 orphaned  (15%)
egeria_git, deployment only       8 live    27 orphaned  (77%)
egeria_workspaces_git, deployment 69 live    2 orphaned  ( 3%)
```

A curator opening Egeria's deployment architecture still sees 35 components where 8 are real.

The backfill is **weaker evidence than a withdrawal from a real run** — it is a human asserting a
scope is vacated, not a step observing it — and must say so: `cause: unclaimed`, plus a detail
recording that it came from a dated backfill rather than a survey. A backfill writing rows
indistinguishable from earned ones would launder an assertion into an observation.

Last of the four steps deliberately, because it is the only one that touches already-published data
and cannot be undone by re-running a survey.

#### MEDIUM — borrow reflexion models' three-way vocabulary for curator verdicts

*(Opened 2026-08-30, from a literature pass cross-reading this file against the academic/commercial
record — feeds the "take architecture results into Curate" item above.)*

Murphy, Notkin & Sullivan's reflexion models (IEEE TSE 2001, building on the 1995 SIGSOFT paper)
name three outcomes when an extracted model meets a hypothesized high-level one: **convergence**,
**divergence**, **absence**. That is a ready-made vocabulary for the undesigned axis in the item
above (accept/reject/retype a proposed component) — a curator note *converges* with a detector's
finding, *diverges* from it, or names something the detector found nothing for (absence). Cheaper
to adopt their names than invent new ones, and their process is worth the same treatment: reflexion
is explicitly iterative, recomputed each time the human's model changes, not a one-shot verdict.

**Open question the paper does not answer.** Reflexion models are computed against *one*
hypothesized model at a time — the technique is silent on whether "architecture" is absolute or
perspective-specific. Architecture recovery already has four perspectives (Logical/Deployment/etc.,
design doc §4.1) where the same component can read differently depending which view is asked.
Nothing in the 2001 paper or its 1997 case-study followup addresses running reflexion per-
perspective and reconciling the results — a component might converge under Deployment and diverge
under Logical simultaneously. That reconciliation is genuinely new design work, not something to
borrow.

#### MEDIUM — separate "correct" from "useful right now": confidence and utility are different axes

*(Opened 2026-08-30. Project owner: "the goal isn't just architecture recovery — its recovery and
understanding of useful artifacts... the threshold for useful isn't static — so at one end of the
scale it might be everything, at the other it might be that we don't publish any of the artifacts
we discover.")*

CleanGraph's pattern (arXiv:2405.03932 — confidence/source/extractor metadata per edge, low-
confidence routed to a human queue) is the wrong borrow if read as confidence routing alone.
**Confidence** — is this component real? — and **usefulness** — is it worth a curator's attention
*right now*, out of everything else competing for it? — are orthogonal. A component can be detected
with high confidence and still not be interesting at the current threshold (a leaf utility module);
a low-confidence guess can be exactly what a curator needs to see because it's the one thing
standing between them and understanding a subsystem that matters.

The 1035→451→20 collapse (`architecture-recovery.md (§17a)`) is already implicitly
answering this question by discarding most of the graph — but as a fixed row cap, which is the
wrong shape for a threshold that needs to slide from "show everything" to "publish nothing found."
Worth designing as an explicit, adjustable utility score — a field separate from Confidence, not
folded into it and not a hard-coded cap. Related: the presentation-findings item above already
names the row-cap symptom; this names what the missing control actually is.

#### MEDIUM — the replayability guarantee is only as strong as the resolver behind it

*(Opened 2026-08-30. The project owner, re: RAGdeterm's structured-retrieval determinism: "isn't it also
dependent on mechanism too?")*

Yes — and this sharpens `context-compilation-design.md` §9's untested claim rather than settling
it. RAGdeterm (ScienceDirect, 2026) gets determinism by grounding retrieval in an explicit
structured representation instead of similarity search — the same move the packer makes (resolvers
over Egeria's materialized state, not a vector search). But "structured query" does not imply
deterministic: an unordered `SELECT`, a paginated cursor, or a resolver that calls an LLM
mid-resolution are all "structured" and still non-replayable. This file's own telemetry item
(top of this section) already flags that a survey step can be LLM-based and non-deterministic —
this is the same fact one layer up: **the replayability contract needs to be a property the
compiler can check per-resolver**, not an assumption that holds because the store is structured.
Worth a `deterministic: bool` tag on the resolver registry, mirroring the `run_time`/cost tags
CLAUDE.md rule 17 already requires.

#### LOW — Collibra's status lifecycle, checked

*(Opened 2026-08-30, the promised follow-up on the item below.)*

Collibra's Business Term lifecycle is **Candidate → Under Review → Accepted**, with **Rejected** a
terminal state reachable from Candidate (an Onboarding Workflow moves a term out of Candidate;
ineligible terms go to Rejected instead). Close to a 1:1 match for the ContentStatus ladder nothing
here walks yet.

The more useful thing to borrow isn't the four names — it's that Collibra implements statuses and
the transitions between them as **configuration, not code**: a "Workflow Definition" declares which
status transitions are legal, separate from the status values themselves. Worth copying regardless
of what the final state names are, since it means a fifth ContentStatus later doesn't mean finding
every place a transition is hard-coded. Could not verify Collibra's edge-case handling (what
happens to relationships when a term is rejected; whether Rejected can re-enter Candidate) from
public docs — that needs a live instance or their admin guide, not marketplace/product-resource
pages.

#### HIGH — Egeria already has this: `Memento` is architecture recovery's tombstone, native

*(Opened 2026-08-30. Project owner: "Egeria itself also implements tombstones (called mementos) in order to
preserve lineage graphs over time. But sounds like there is more to learn here.")*

Confirmed against the local Egeria checkout
(`open-metadata-types/.../OpenMetadataTypesArchive2_6.java`, `addMementoClassification`) and
egeria-project.org: `Memento` is a classification attachable to any `OpenMetadataRoot` entity,
carrying `archiveDate`/`archiveUser`/`archiveProcess`/`archiveService`/`archiveMethod`/
`archiveProperties`. Its stated purpose: *"indicates that an element is logically deleted because
it is no longer describing all or part of a real-world digital resource... retained to support
lineage graph queries."* **Memento elements are excluded from normal queries and only returned when
the caller passes `forLineage`.**

That is this project's tombstoning design, already built, natively, in the platform it is
Egeria-first about. `WITHDRAWN_LABEL` (steps 1–3, `architecture-recovery.md (§16)`)
reimplements the same shape locally: mark-not-delete, retained for history, hidden from normal
reads. Two things worth checking before step 4 (the backfill, above) goes further:

- Does the local tombstone need to keep existing once a component is actually projected to Egeria
  (Phase 2, still unbuilt — the item at the top of this file), or should local withdrawal just set
  `Memento` on the published element and let Egeria's own `forLineage` filtering do the hiding?
- If both are going to exist for a while (local proposals aren't published, so have nothing to put
  `Memento` on), the *fields* are worth matching now rather than reconciling later —
  `archiveProcess`/`archiveMethod` map onto exactly the "which step withdrew this, and how"
  provenance `cause: unclaimed` (the backfill item above) is already trying to express by hand.

Not "redone from scratch was wasted work" — the local version had to exist before anything reached
Egeria, and still does for proposals that never get published. But it's now clear there's a real
convergence point once Phase 2 lands, and designing step 4's backfill without checking `Memento`'s
shape risks diverging further from a mechanism that already solves the identical problem one layer
up.

#### Note — is the eight-intent/curation model more complex than anything proven to need it?

*(Opened 2026-08-30. Project owner: "I wonder if our model is too complex and unnatural — something to keep
in mind.")*

Not a task — a caution worth keeping attached to future design work rather than resolving. Two data
points from this session's research feed the worry directly: **no commercial catalog surveyed**
(Amundsen, DataHub, Atlas, Collibra, Alation, Purview, Dataplex) **implements more than a two-tier
automated/human split** — nothing resembling eight named intents exists in production elsewhere.
And Egeria itself shipped `Memento` — a working tombstone — years before this project built its own
(the entry above). Neither is proof the model is over-built: eight intents may be doing real work
seven vendors happen not to need, the same way architecture recovery's multi-perspective view does
work Reflexion Models never had to (the vocabulary entry above). But "nobody else needed this many
moving parts" and "we rebuilt something that already existed one layer down" are both the kind of
signal that's easy to miss from inside the project, and worth someone periodically asking from
outside it rather than only from the momentum of the backlog that's accumulated.

#### LOW — coupling's decision trace is 250 copies of one line

*(Opened 2026-08-30, surfaced by persisting the trace — invisible while it was `log.info`-only.)*

First real measurement of `architecture_decisions` on `egeria_git`:

| step | notes | shape |
|---|---|---|
| `detect` | 4 | all high-signal — distillation arithmetic, platform consolidation, the variant drop |
| `coupling` | **263** (200 kept, 63 truncated) | **250+ are one templated line**: `.: adopted unproposed subtree X (nothing else claims it)` |

A trace dominated by one repeated message crowds out the notes a reader wants. Whether those should
collapse into one summary note (`adopted 250 unproposed subtrees`) or are genuinely per-decision is
a judgement about coupling's own semantics — hence LOW and not fixed in passing. The cap already
reports the overflow rather than hiding it, so nothing is silently lost meanwhile.

#### LOW — the decision-trace lookup is linear in run count

*(Opened 2026-08-30.)* `_withdraw_vacated` reads the kind's whole finding history on every persist —
**10,135 rows / 93ms for `egeria_git`**. Fine inside a survey that takes seconds, but history is
append-only and never pruned, so this grows with every run. Measured and recorded rather than
pre-optimised; the narrower two-query form (find the step's latest `surveyed_at`, then select only
those rows) is available if it ever matters. Likeliest trigger for the telemetry item above.

#### HIGH — architecture recovery: coverage closed on 2026-08-28; precision is now the whole entry

**The first version of this entry said "3 of 46, 6% coverage". That was wrong, and wrong in the
way this project keeps being wrong: the query was `query_findings(slug, kind)`, which defaults to
`scope_locator=''` — whole-resource. Architecture recovery writes one finding set **per component
scope**, so a default-scope query sees a repo only if it has a single root-scoped component. It
found `docling_parse` (1 component) and missed `milvus` (202).** Corrected with
`query_finding_scopes()`:

```
repos the gate approves for recovery          46
repos WITH architecture_recovery results      16   (15 of them gate=run)
```

**Superseded 2026-08-28 — coverage is no longer the blocker.** A batch landed after the
measurement above: the findings histogram runs 1 → 5 → 10 → 31 → 42 resources across
2026-08-21..26. Re-measured live on 2026-08-28: **46 of 60 registered repos now carry
architecture_recovery results**, against 16 when this was written. The 14 without are
largely gate-excluded (a docs site, an awesome-list, an unindexed new repo).

A separate session measured the gate-eligible slice specifically and reports 45 gate=run
resources fully accounted for — 41 with results, 3 with a *verified* real zero
(`outcome_known_positive=true`, `no_components_detected`), and 1 (`unitycatalog_rs`)
genuinely unverified because it is 100% Rust and the marker languages are Go, Java and
Python. **Those figures are theirs, not reproduced here**: I verified the headline
independently and got a different denominator (all repos, not gate=run), which is enough
to retire "a third of the corpus" but not enough to restate their breakdown as mine. See
`docs/task-list-2026-08-28.md` for that measurement.

So the remaining work in this entry is **precision, not coverage**. The note below about
the measuring instrument still stands and is why the original number was wrong twice.

Fifth instance of the measuring instrument being the broken part, after findings 73, 79, 90 and
the `resolve_doc_locations`/`build_report` timing confusion. The standing prior holds: **when a
number about this system looks wrong, suspect the query before the subsystem.**

The whole stack exists and is tested — detectors, import graph, coupling, `interfaces.propose()`
for ports and wires, the deterministic distiller, the LLM adjudicator, identity-aware scoring, 98
recorded findings. The gate now correctly identifies which 46 repos are worth running it on
(finding 97, corpus re-measured at 46 run / 8 skip / 6 none). It has been *run* on three.

**This is capability without coverage, and it is the largest single unclaimed benefit in the
project.** Everything downstream — the Egeria Solution Blueprint projection, the deployment
topology view, anything that needs more than one repo's architecture to be interesting — is
waiting on data that nothing is blocking.

**Cost, from `STEP_REGISTRY.requires_resources`:**

```
repo_arch_detect     {'zipball_root': 'local_path'}                        download, no clone
repo_arch_coupling   {'zipball_root': ..., 'git_clone_root': 'history_path'}   download AND clone
```

So `repo_arch_detect` across 46 repos is 46 zipball downloads — feasible unattended. Coupling is
materially more expensive and should be a separate decision made after detect has run.

**PILOT RUN 2026-08-24 — 5 repos, detect only, 0 errors, 4.4s–54.8s each.** Chosen to test the
three "unprioritisable" language defects rather than for speed. It reprioritised all three:

```
milvus                 26.5s   202 component scopes   ground truth says 8
docling_java            4.4s     8 component scopes
egeria_workspaces_git  38.6s    72 component scopes
docling_parse          54.8s     1 component scope
genaicomps             16.9s   311 component scopes
```

- **"Java marker components are all named `src`" does not reproduce.** `docling_java` yields
  `docling-bom`, `docling-core`, `docling-serve-api`, `docling-serve-client`,
  `docling-testcontainers`, `docling-version-tests`, `test-report-aggregation`, `docs` — real
  Maven module names, zero scopes ending in `src`. The defect was observed on Kafka (Gradle) and
  is either Gradle-specific or was fixed by the Gradle module expansion work. **Downgrade; re-test
  on Kafka before spending anything on it.**
- **The `cmd/X` + `pkg/X` merge is not the live Go problem.** Milvus has **one** `cmd` scope and
  **145** `internal/*` scopes. There is almost nothing to merge. **Downgrade.**
- **Precision is the live problem, and it is severe.** Milvus proposes **202 components against a
  published ground truth of 8** ("five core components and three third-party dependencies", the
  Milvus authors' own words). `genaicomps` proposes 311. Every `internal/*` package becomes a
  candidate component. This is the same precision gap the spike measured on Kubernetes (3303 →
  358 deterministic → 93 adjudicated) — **the distiller exists and is not in the product path.**

**So the ranking inverts.** Running detect across the remaining 31 repos would produce thousands of
component findings at roughly 25:1 over-proposal, which is not coverage, it is noise at scale.
**Port the distillation and ranking stack into the product path first** (`scripts/arch-spike/`
`distill.py`, `rank.py`, and optionally `adjudicate.py`), re-run the pilot 5, and only then decide
on the full corpus. Cost is bounded and known: detect averages ~28s/repo with no clone.

---

#### Architecture recovery — the PORTED implementation has never been scored

Phase 1's declared numbers (13 of 13 components, 97% coverage, ARI 0.969 —
`docs/architecture-recovery.md (§13.2)`) were measured on the **throwaway spike** in
`scripts/arch-spike/`. What shipped into `resource_explorer/surveyors/arch_recovery/` is a *port*,
and it has at least one known behavioural difference: the spike merged agreeing proposals at IR
level and boosted confidence on agreement, while the port discovers agreement at read time by
grouping on `scope_locator` and does not boost. There may be others; nobody has checked.

**Do not assume the port reproduces the spike's numbers.** Run `score.py` against the ported
pipeline's output and the pre-registered fixtures, and record the result. If it differs, that is a
finding either way — the port is wrong, or the merge mattered less than assumed.

This is the same class of error the spike hit three times (README findings 15, 30, 37): assuming a
property of the code when it was actually a property of how the code was being measured. A port
that passes its unit tests can still partition differently.

Cheap: `score.py` and the fixtures already exist; only the plumbing from the ported pipeline's
output to the scorer is new.

---

#### Architecture recovery — re-check the Phase 1 measurements once there are more samples

**Not a doubt about the current numbers; a limit on what two repos can establish.** Phase 1's
measurement goals were declared met on 2026-08-20 (`docs/architecture-recovery.md (§13.2)`)
— 13 of 13 components, 97% file coverage, ARI 0.969 on trellis, T1 recall held at 18/27, 5.3s per
repo. Every criterion in the plan's §5 was cleared, several by a wide margin.

Re-run the whole evaluation, and expect to revise, when there is materially more experience:
**roughly 8–10 surveyed repos of varied shape**, or the first time a real user disagrees with a
partition RE published.

What only more samples can settle:

- **n=2, and they are not independent.** trellis is a well-factored Python monorepo — close to the
  best case for import cohesion — and `egeria-workspaces` is a flat app. Both are ours, both are
  Python, both were partly written by the people writing the detectors.
- **`COHESIVE_BAR` and `DISPERSION_BAR` are unvalidated.** They were set by inspection on one
  repo. The Phase 1 plan's own preferred answer (Newman modularity as a null-model threshold) was
  tried and failed — `Q > 0` admitted 15 of 16 candidates (README finding 33) — so the current
  bars are a placeholder, not a result.
- **The residue rule is a known trade, not a solution.** Adopting unproposed subtrees took
  `Utility scripts` to exact and `Core` from exact to 0.51, because the two ground-truth entries
  disagree about residue ownership *deliberately* (finding 44). More repos will show which reading
  is the common one — or that it is genuinely per-repo and belongs to a human.
- **T2's ground truth is not a clean pre-registration.** The trellis component count was reported
  to the maintainer before the fixture was written. Contamination runs the safe way — the fixture
  contradicts the detector rather than echoing it — but it is a caveat on T2's numbers that a
  fresh repo would not carry.
- **T1 precision is 0.31 and is not really understood.** It is dominated by add-on granularity
  (finding 12), where the maintainer names a 9-container bundle as one component. Whether that is
  a fixture inconsistency or the normal way people think about add-ons needs more than one
  example.
- **Python only.** `imports.py` extracts Python; `egeria` has zero tracked `.py` files, so the
  obvious adversarial target cannot be scored at all and "does this generalise beyond Python?"
  is currently unanswerable.

**Cheap to redo, which is the point.** `score.py`, `coupling.py` and the pre-registered fixtures
already exist, so re-running is hours, not a phase — provided new targets get **pre-registered
ground truth written before the detectors run on them** (`tests/fixtures/architecture-ground-truth/README.md`).
Writing that fixture is the actual cost, and it is what makes the re-check meaningful rather than
a re-confirmation.

Related: `docs/repo-analysis-funnel.md (§12)` §4 proposes recording approach outcomes against repo
characteristics — if that is built, this re-check becomes a query rather than an exercise.

---

#### Phase 5 distillation — what the ranking experiment settled (finding 77)

`scripts/arch-spike/rank.py` measures recall@N over ranked candidates. Conclusions, all measured:

* **An adjudicator needs hundreds of candidates, not thousands** — N≈25 / 100 / 250 for Prometheus /
  Milvus / Kubernetes under the `typed` strategy. 250 evidence-carrying candidates is a tractable LLM
  input; 3303 is not.
* **Do not rank by the emitted confidence.** Worst strategy at every N on every target (1/11 at N=25
  on Prometheus vs 9/11 for `typed`). §3.3b confidence describes *how identity was established*, not
  *how likely this is a component*.
* **`rollup` ties `typed`** — negative result; the extra machinery is unjustified by current evidence.
* **The remaining gap is structural, not a ranking problem.** A declared component's minimal cover is
  2–3 nodes (`kube-controller-manager` = `cmd/kube-controller-manager` + `pkg/controller`), and *all*
  of them must be in the window. Until something merges the `cmd/X` and `pkg/X` arms into one
  candidate — which is what the ground truth declares — each such component costs 2–3 slots instead
  of 1.

**Note on gap-list item 3 (finding 89):** it was reported closed when `interfaces.propose()` was
committed, but its only caller was the spike harness — the survey step never computed ports and
`persist_ir` never stored them. Now genuinely wired: `arch_recovery_detect` computes them and
`persist_ir` writes an `architecture_interfaces` finding kind. "Committed and regression-tested" is
not "reachable".

**Attempted and does not work by import evidence (finding 78).** Strict-majority dominance from entry
point to package finds the right partner 6 times out of 7 (`pkg/scheduler` share 1.00,
`pkg/controller` 0.97, `pkg/proxy` 0.97, `pkg/kubelet` 0.83) **and over-reaches every time**, pulling
in 3–44 extras such as `pkg/util/iptables` and `pkg/apis/*`. No merged set equals the ground truth.
The distinction between "`pkg/proxy` **is** kube-proxy's implementation" and "`pkg/util/iptables` is a
utility it **uses**" is not encoded in the import graph — both are imports dominated by one binary.

The pairing *is* recoverable by name (`kube-scheduler`↔`scheduler` after prefix-stripping; Milvus's
`internal/proxy` + `internal/distributed/proxy`), but **that is fitting to the two repos measured** and
would be believed only after validating on a repo nobody here has looked at. Left open deliberately
rather than shipped.

**A real fix did come out of it:** entry points are packages declaring `package main`, not
directories containing a file called `main.go` — Kubernetes's are `scheduler.go`, `kubelet.go`,
`proxy.go`. Closes the `has_main` type-inference weakness; Prometheus no longer mistypes `promql`,
`util` and `documentation` as `Console Command`.

---

#### Location-valued artifacts: 31% are NOT in the repo — the corpus number

Measured 2026-08-24, first full run of `repo_classification` across all 60 registered repos
(26.7 min, 0 failures, on the post-`fd2e5a7` path). Recorded here because it answers a question
the architecture-recovery design could previously only answer from five hand-picked projects,
and because that session asked for it explicitly and has since ended.

```
artifacts located              140
located ELSEWHERE               43   (31% of located)
repos with >= 1 elsewhere       25   of 60
max elsewhere in one repo        3
```

**A boolean "does this repo document its architecture?" would answer *no* for 43 artifacts
that exist and were found.** `architecture-recovery-extraction-design.md` §5.5b's location-valued design (`in-repo` / `sibling-repo` /
`doc-site` / `not-found`, only the last an absence) is therefore paying for itself on a corpus
nobody selected to flatter it — the point being that the spread is unglamorous and even
(polaris 1, docling_eval 1, docling_java 3, docling_mcp 1) rather than concentrated in the
famous Kubernetes case. A steady third across 25 repos is a stronger argument than one
spectacular example.

Corpus shape, first time this has run everywhere:

```
roles  samples 16 · application 13 · documentation 11 · library 6 · tutorial 5 · middleware 2 · tool 1 · none 6
gates  run 47 · skip 7 · none 6      <- superseded, see the re-measurement below: 46 · 8 · 6
timing min 7.2s · median 25.5s · max 58.6s
```

Two things worth someone's attention:

- ~~**The gate lets 87% through** (47 run, 7 skip of 54 classified)~~ — **CHECKED, and the
  ratio is roughly right.** The architecture-recovery session measured it rather than
  re-running anything (every gate decision persists its own reason string with the signals
  named): of 32 repos carrying a non-architectural role, 25 were overridden, and
  `package-manifest` was present in 19 of those but **decisive alone in only 3**. Dropping it
  entirely moves 7 skips to 10 — 87% to 81%. The gate has containment semantics precisely so a
  samples repo with compose files still runs, and on a corpus that is mostly real software,
  that is what happens.

  **Both of us had guessed the wrong mechanism from the right aggregate.** `package-manifest`
  looked weak because a Python samples repo has a `pyproject.toml`; the aggregate did not
  contain that story. Reading the n=3 decisive cases *by name* found the actual defect:
  `OpenLineage/openlineage-site` is 71% doc-shaped with a `docusaurus.config.js`, no Dockerfile
  or compose — and a `package.json`, **because Docusaurus is a Node program**. So the manifest
  is not a weak signal; it is the only structural signal a documentation site can produce by
  itself. Fixed by making the generator config its own `doc-site-generator` signal and
  discounting `package-manifest` when it is present.

  Recorded because it is the third time the pattern has appeared: aggregate read correctly,
  mechanism guessed wrongly, small-n cases read by name giving the answer. "25 overrides" is a
  number; "`openlineage-site` is a Docusaurus site" is what tells you what to change.

- ~~**The 47/7 above is a snapshot, and one repo of it is already known to move.**~~
  **RE-MEASURED 2026-08-24** — `repo_classification` re-run across all 60 repos after the
  `doc-site-generator` fix: **25.5 min, 0 failures, 46 run · 8 skip · 6 none.** Exactly one
  repo moved (`openlineage_site`, run → skip, generator-owned package manifest) and the other
  53 are unchanged, which is what a narrow fix should produce.

  **47/7 was correct when taken; 46/8 is correct now.** The prediction was deliberately NOT
  written in as a measurement while it was still a prediction — that decision is what this
  re-run discharges, and the practice is the transferable part: a prediction in the slot where
  a measurement belongs is the substitution this file exists to avoid, and the cost of holding
  the line was one 25-minute run. Full breakdown and the named skips in
  `docs/archive/arch-recovery-handoff-2026-08-24.md`.
- **The 6 repos with no role have no file inventory** — never ingested, so nothing to classify.
  Correct behaviour, and the card now says which kind of nothing it is rather than showing a
  blank.

---

#### Repo classification — what the repo *represents*, before what its architecture is

**Maintainer direction, 2026-08-22; design `architecture-recovery-extraction-design.md` §5.5b.** Classify a repo (or each member of a repo family)
as library / application / middleware / tutorial / samples / documentation / tooling, **because the
classification decides which analyses and which questions are relevant.** Recovering a blueprint from
a tutorial repo is not a weak result — it is the wrong question, and spike finding 58's `workshops`
false positives were settled exactly this way, by a human reading a README that stated the repo's
intent.

Cheapest possible funnel gate (rule 17): it rules out whole *categories* of analysis rather than
individual steps. Every signal it needs is already collected — README intent statements, whether
published architecture docs exist at all, manifest declarations, absence of deployment artifacts,
test/example/notebook ratios, and dependency direction.

Open on purpose: **do not invent a closed vocabulary** before checking Egeria's existing types
(`SoftwareCapability` subtypes, `plannedDeployedImplementationType`) — §3.1's 13-value
`SolutionComponentType` turned out to already exist rather than needing invention. Also open: whether
a monorepo gets one classification or one per workspace member (`trellis` alone holds an application,
two libraries and a spike).

**It is a gate, not a weighting** (maintainer, same session): on a tutorial/samples/documentation
repo, architecture recovery **does not run**, saving the whole tier rather than filtering after the
expensive work.

**Needs the owner of `step_outcome.py`.** None of the five labels fits a skip-because-irrelevant.
`no_signal` requires `known_positive=True` (proof the detector works) and nothing ran; `unverified`
means "could not run", but here we *could* have and chose not to — a success of the funnel, not a
failure. The distinction that must survive: **"didn't run because it would have been the wrong
question" vs "ran and found nothing"**. Conflating them makes the funnel's biggest win look like its
most common failure. Do not add a sixth label unilaterally.

**Second axis — project topology, not just repo role** (maintainer, same session). How a project
distributes concerns across repos is a matter of style and trend, and it decides *where to look for
what*. RE already models this (`projects.parent_slug`, `group_slug`, `homepage_url`, `docs_url`);
nothing asks the question. Five topologies are already measured (finding 68): **four of five projects
put documentation in a sibling repo** — `milvus-docs`, `kubernetes/website`, `egeria-docs`,
`prometheus/docs` — with only `egeria-workspaces` keeping it in-tree mixed with code and tutorials.

**Expectation sets, location-valued.** From the role, derive what a mature project of that kind
should have, then find it. **Each expected artifact resolves to `in-repo` / `sibling repo` /
`doc site` / `not found`, not to a boolean** — finding 68 is the cautionary tale, since "where are
the docs" answered naively against `kubernetes/kubernetes` returns "nothing, stale 1400 days" when
the truth is "in `kubernetes/website`, updated today". **Requires `architecture-recovery-extraction-design.md` §5.5a(a)'s outward hop as a hard
prerequisite.**

Absence cuts both ways and must not be conflated: no deployment artifacts in an *application* is a
maturity finding; in a *library* it is confirmation of the classification (`trellis.md` records
exactly this). Same guardrail as `architecture-recovery-extraction-design.md` §5.5a(c): report locations as dated evidence, **do not rank on the
count** — a small stable library documents lightly on purpose.

**Offer to widen the scope to sibling repos** (maintainer, same session). When the expected artifacts
for a role are not in the repo the user named, ask whether to include other repos of the project. The
location-valued lookup already produces the candidate list, so the question is "shall I include
`kubernetes/website`?", not "which repo?".

* **Ask, never auto-add** — silent scope expansion causes unrequested fetches and results that cannot
  be compared with the previous run. Precedent: the maintainer's "present the user with a file tree
  with checkboxes" answer on ambiguous partitions.
* **Record the in-scope repo set with the result** — it changes every coverage denominator, the same
  reason `trellis.md` carries a `Scope:` line (whole-repo coverage 15% vs in-scope 48%), and the same
  argument §6.2 makes for `analyzerVersion`.
* **Classification first** — "missing" is only defined relative to role; a library is *expected* to
  have no deployment artifacts.
* **Reuse RFA and `projects.parent_slug`/`group_slug`** — a new question, not new plumbing.

**Built and verified: `resource_explorer/github/doc_locations.py`.** Resolution + location-valued
lookup, checked live against all five measured topologies — Kubernetes `docs/` correctly reported as
a **tombstone** with docs resolving to sibling `kubernetes/website`, Prometheus showing **both**
in-repo `documentation/` and sibling `prometheus/docs`. The two lookups that encode the bug both
pass: `find_artifact("architecture")` returns `in-repo` (`documentation/internal_architecture.md`)
for Prometheus and **`sibling-repo`, not `not-found`,** for Kubernetes. 28 hermetic offline tests.

*Known limitation, documented in the module:* a bare `website`/`docs` sibling is the project's own
only when the org ≈ the project. `kubernetes/website` matches correctly; `odpi/website` is the
foundation's site and is returned for both `odpi/egeria` and `odpi/egeria-workspaces`, belonging to
neither. Deliberately unfixed — dropping bare `website` would break the Kubernetes case this module
exists for. **The evidence already discriminates**: `odpi/website` was last pushed **2019-11-07**
while `kubernetes/website`, `prometheus/docs` and `odpi/egeria-docs` were all pushed within two days
(verified 2026-08-23). A consumer reading the date can discount it without the module deciding —
suppressing it here would *be* the ranking judgement `architecture-recovery-extraction-design.md` §5.5a(c) forbids. And §5.5b asks before
including a sibling, so an extra dated candidate is a checkbox, not a wrong answer.

**Role classifier built** (`resource_explorer/github/repo_role.py`, 46 hermetic tests). Seven roles,
multi-valued with a primary, every role carrying the evidence that produced it. Live-verified:
`kubernetes/website` and `odpi/egeria-docs` → `documentation`; `milvus-io/milvus` and
`prometheus/prometheus` → `application`; `odpi/egeria-workspaces` → `tutorial`+`application`+`library`.

**Gate correction (design `architecture-recovery-extraction-design.md` §5.5b).** `egeria-workspaces` ranks `tutorial` primary and is *also* target
T1, from which architecture recovery scored 18/27. A gate keyed on the primary role would skip it.
**Trigger the skip on containment, not primacy:** skip when a tutorial/samples/documentation role is
present AND no deployment/structural artifacts were found. Primacy still drives the expectation set.

**Known limitation, evidenced not tuned:** the README-intent matcher requires an *is-a* phrasing
(`X is a tutorial`) and misses *purpose* phrasing — `egeria-workspaces`'s "designed for learning" is
an explicit statement of intent that `architecture-recovery-extraction-design.md` §5.2 step 0 says should outrank inference, and it does not fire.
The role was recovered from notebook presence instead. Broadening the pattern should be validated
against a repo not yet looked at, not tuned on this one.

**Expectation sets built** (`resource_explorer/github/expectations.py`, 14 tests). Role → expected
artifacts → each resolved to a location by `doc_locations.find_artifact`, with four states kept
separate — `found`, `missing`, `confirmations` (not expected AND absent, which *supports* the role),
`unexpected`. **No count, percentage or grade is exposed**, and a test asserts none leaks in.

Live gate results: `odpi/egeria-workspaces` → **RUN** (tutorial primary, but deployment artifacts
present — the case that would have been wrong under a primacy gate); `odpi/egeria-docs` → **SKIP**;
Prometheus and Milvus → RUN with all four expected artifacts located.

**Two limitations found live, recorded not tuned:**

1. **The gate over-runs on documentation repos with their own build tooling.** `kubernetes/website`
   returns RUN, because its Hugo/npm build supplies `package-manifest` and `deployment-artifacts`
   signals — build tooling for the docs, not a product architecture. Mechanically separating
   "tooling that builds the docs" from "the thing the repo is about" is not obviously possible from
   these signals. **The direction of the error is the safe one**: a false RUN wastes a tier, a false
   SKIP loses real work, so the gate is deliberately left conservative. Revisit only with a signal
   that distinguishes them.
2. **`find_artifact("readme")` prefers a nested README over the root one** — it returns
   `documentation/prometheus-mixin/README.md` for Prometheus and `docs/README.md` for Milvus, when
   the root `README.md` is plainly the right answer. Doc directories are searched before the repo
   root. Small, real, and worth fixing in `doc_locations` (root should win for `readme`).

**Companion item, deferred by the maintainer to a separate discussion: capturing the user's intent.**
What the repo *is* and what the user *wants from it* are two different filters on which analyses
matter; conflating them would be a mistake.

---

#### Documentation as source, as dated source, and as signal (design `architecture-recovery-extraction-design.md` §5.5a)

Three implementable items came out of the Milvus ground-truth exercise (spike README findings 65–67).
All three are Discovery-tier by rule 17's test — cheap, and they gate the expensive tiers.

**1. Step 0 needs an outward hop to the project's doc site.** `architecture-recovery-extraction-design.md` §5.2 step 0 reads in-repo docs only, and
Milvus proves that insufficient: the authoritative logical architecture is at `milvus.io`, while
`milvus-io/milvus`'s own `docs/` has a README, `design-docs/`, `agent_guides/` and `archive/` but not
the front-door architecture page. Resolve the doc site from README links, repository metadata, or the
package manifest homepage, and treat a published architecture page as a first-class distillation
input. One fetch, once. Open question: how to recognise *which* published page is the architecture
page without hand-curation — a per-project hint in the fixture is fine to start.

**2. Path-dating, to put a vintage on any prose architecture.** `GET
/repos/{o}/{r}/commits?path={p}&per_page=1` dates any path; for a path that no longer exists that is
effectively its removal date. Vintage is bounded above by the newest dead path a description cites;
blind spot is bounded below by the churn of live paths it omits. Verified on Milvus — four calls
dated a stale description at ~17 months old without reading any Go. Should run on **any** prose
architecture we consume *and on our own recovered blueprints*, with the dates carried in `architecture-recovery-extraction-design.md` §5.4
evidence. Cheap to build; the only real design choice is where unresolvable paths surface, and the
answer is probably "as their own outcome", never silently as detector misses.

**2a. Resolving where the docs live is a PREREQUISITE for item 3, not a sibling of it.** Measured
over twelve repos (spike finding 68), five of five checked keep documentation in a *separate,
actively-maintained repo*. `kubernetes/kubernetes/docs/` holds only `.gitignore` and `OWNERS` — a
tombstone — so the naive doc-lag metric scores Kubernetes at 1412 days of abandoned documentation
while `kubernetes/website` was pushed the same day. Resolve the docs location first (item 1), then
measure (item 3). Detect the tombstone pattern explicitly: a docs directory holding only
`OWNERS`/`.gitignore`/README stubs means deliberate relocation, which is a *positive* curation signal
of the same class as Milvus's maintained `docs/archive/` and Egeria's `saved/`.

Useful consequence: because the docs repo is a git repo, item 2's path-dating applies to the document
itself as well as to the paths it cites — two independent dates that cross-check, no heuristics.

**3. Doc-health as a reported signal.** Not what the docs say — whether they exist and are kept
current. Compare commit recency of doc paths against code paths; note whether stale docs are archived
(Milvus maintains `docs/archive/`, which is a stronger marker than merely having docs) or left in
place. Milvus's lag is one day. This is the measurable half of the triage judgement finding 58 needed
a human for. **Report as dated evidence, do not rank on it** — a maintained doc site can coexist with
rotting in-repo docs, and a small stable library may document lightly on purpose. A naive `docs/` mtime
is also too coarse on its own (one typo fix moves it); prefer a distribution over doc paths, and
per-component lag where §6.0 scope locators make that possible.

**4. Ground-truth candidates the scan surfaced**, in the order they should be attempted — this feeds
the pending 8–10 repo measurement re-check:

| candidate | why | caveat |
|---|---|---|
| `prometheus/prometheus` | `documentation/internal_architecture.md` is **in-repo**; 281 MB | **DONE** — pre-registered `9039f9a`, scored **0/11**, see below |
| `milvus-io/milvus` | 8 architecture pages in `milvus-io/milvus-docs`, current within a day | Go/C++ — **blocked on Go support**, see below |
| `kubernetes/kubernetes` | canonical component names map cleanly onto `cmd/` | large; doubles as a scale test |
| `odpi/egeria` (T3) | — | **negative result:** of 15 architecture hits in `odpi/egeria-docs`, most are under `saved/` (archived) or are dojo-tutorial SVGs. No current authoritative logical-architecture page. Our flagship target is the corpus's *weakest* ground-truth source — worth knowing before a poor T3 score is read as a detector failure. |

---

#### Learning from user feedback (design `architecture-recovery-extraction-design.md` §5.5c)

Maintainer direction: continuously take user feedback to refine weights, scoring and algorithms,
possibly dynamically. Necessary — every `architecture-recovery-extraction-design.md` §5.5b table is provisional. Sequenced deliberately:

1. **Capture feedback as labelled examples**, with author, date and repo — not as weight deltas. RE
   already has the surfaces (`curate.py`'s `resource_feedback`/`resource_curator_notes`, RFA, the
   activity log), so this is a new *question*, not new plumbing. Build this first; it is the half
   with no downside.
2. **Make weights explicit, versioned, and recorded with every result** — §6.2's `analyzerVersion`
   argument applies verbatim: a number that moves between runs is ambiguous unless you can say which
   weights produced it.
3. **Keep a frozen holdout, and never let the pre-registered fixtures into the training loop.**
   Rule 3 exists because a partition fitted to the code it is scored against measures nothing; a
   weight fitted to make `prometheus.md` read 11/11 makes that 11/11 meaningless.
4. **Only then consider dynamic adjustment.** You cannot safely auto-tune without a regression
   detector, and ours is the pre-registered corpus under strict containment — which works only while
   it stays outside the loop.

Failure mode to guard against explicitly: a system tuned on recent feedback gets better at *agreeing
with recent users* rather than at being right, and degrades invisibly because the same feedback that
moves the weights also shapes what anyone thinks to check.

---

### Egeria & governance

#### Step outcomes and the Egeria governance model — what landed 2026-08-21, and what was deferred

**Landed:** `resource_explorer/step_outcome.py` — the five-label vocabulary from
`docs/repo-analysis-funnel.md (§12)` §3 (`recovered` / `partial` / `no_signal` / `unverified` /
`regression`), with §3's rule enforced in the constructor: an approach with no known-positive
check cannot report `no_signal`, only `unverified`. `repo_website_ingestion` is the first
adopter. Recording only — nothing routes on these labels.

**Established while investigating, all verified against the live server rather than read:**

- Guards already round-trip in RE today. `scripts/generate_repo_survey_definition.py` emits
  `### Guard / Any` on every `Link Next Process Step`; Dr.Egeria's command accepts `Guard` and
  `Mandatory Guard`; a live read of Analysis Survey returns `guard: 'Any'`, `mandatoryGuard:
  False` on all 9 links. The reader receives them and discards them.
- `NextGovernanceActionProcessStepProperties` carries exactly `guard: Optional[str]` and
  `mandatory_guard: Optional[bool]`. A flat token, no structured payload — which is *why*
  outcome and cause are separate fields, not a stylistic choice.
- RE consults no Egeria specification at all. `STEP_REGISTRY` is a specification living in
  Python. `SpecificationProperties` is the pyegeria client for the real thing.

**Deferred, with the reason:**

1. **Guard-based branching.** Deferred by decision 2026-08-21 — recorded outcomes are useful
   without routing, and branching is real work in `survey_definition_reader` (a documented v1
   boundary, see `docs/survey-definitions.md`). Authored links stay `guard: Any` until wanted.
2. **Whether a locally-produced guard can be recorded against the process at all**, given RE
   acts as its own engine host. Untested. If it turns out to be engine-action-only, then for
   RE-executed surveys this vocabulary is a *recording* mechanism and only a *routing* one
   under Egeria coordination — a real difference, worth knowing before building on it.
3. **Generating the Egeria specification from the enforced local contract.** Direction agreed
   (master in Egeria, cached locally), and the shape agreed with the arch-recovery session:
   keep `ResourceProvider.provides` / `requires_views` / `validate_resource_views()` as the
   *enforced* contract and generate the published spec from it, so the two cannot drift. One
   property to honour: generation must fail loudly if the enforced contract has no expressible
   form in the spec, rather than emitting a lossy one — otherwise the drift returns through the
   generator.
4. **`Produced Request Parameters` as the carrier if a cause ever needs to reach a *later*
   step** rather than only be recorded. Read in the docs, **not exercised** — do not build on
   it as verified.
5. ~~**Adopting the vocabulary in the other 23 steps.**~~ **PARTLY RESOLVED 2026-08-22 — the
   file-inventory readers are done.** `step_outcome.from_upstream_table()` is the shared
   three-way derivation for a step that reads a table an earlier step was meant to fill:
   empty table → `unverified`, rows present but nothing matched → `no_signal` (**the non-empty
   table is the known-positive**), otherwise `recovered`. It never returns `partial` — whether
   a non-zero result is *complete* is knowledge only the calling step has.

   Adopted in `repo_file_size`, `repo_data_profiling`, `repo_documentation`,
   `repo_sub_resource_survey`, `repo_file_classification`, `repo_file_structure` and
   `repo_security`. Three things fell out of doing it that were not visible beforehand:

   - **`SecurityHygieneSurveyor` was reading the wrong table entirely.** It looked for
     SECURITY.md / CI config / LICENSE in `project_code_symbols`, which by construction holds
     only `.py/.js/.java/.go` files — so the first two checks failed for *every repo, always*,
     and raised RFAs at confidence 90/85 telling people to add files they already had.
     Confirmed against live data before changing (docling: SECURITY.md + 13 workflow files in
     the inventory, zero of either in code symbols). `documentation.py` had already been moved
     to the inventory for this exact reason and left a comment saying why; this step was missed.
     Now reads the inventory, and emits **no** gap RFAs when the inventory is empty.
   - **`DocumentationSurveyor` was issuing a verdict on unread repos.** Half its score comes
     from the inventory, so an empty one produced "Documentation quality: Minimal" — and
     persisted `label="Minimal"` into the trend, which outlives the run. Now `Unverified` in
     both places.
   - **Three `StepInfo` comments named the wrong source table.** `repo_file_structure` and
     `repo_language` do not read the inventory at all (project_stats / project_code_symbols),
     and `repo_security` did not until this change. Corrected — the comments encode ordering
     prerequisites, so a wrong one is a wrong dependency.

   Two contracts were deliberately reversed and their tests rewritten rather than patched:
   `test_no_inventory_persists_nothing` (a run that found nothing now leaves a labelled zero —
   a gap in a trend is unreadable) and `test_no_signals_yields_minimal_quality`. Both carry a
   note saying what changed and why. New coverage: `tests/test_inventory_reader_outcomes.py`.

   **`repo_api_structure` followed on the same day.** It reads `project_code_symbols` rather
   than the inventory, but the shape is identical and the live case was the strongest of the
   set: measured across the registry, **13 of 20 repos had a populated file inventory and zero
   symbols** (docling 1,653 files/0 symbols, trellis 1,078/0). For all thirteen the step
   returned an empty annotation list — the one output indistinguishable from never having run.
   It now emits a labelled annotation and a zero metric, and distinguishes an empty table
   (`unverified`) from a scope that excluded every symbol (`no_signal`, via an unscoped
   `COUNT(*)`). `test_no_symbols_persists_nothing` reversed with a note, same as
   `test_no_inventory_persists_nothing`.

   **`repo_dependency` followed, and has the sharpest known-positive of the set.** It does not
   fall back on "the upstream table has rows": it checks the file inventory for a **dependency
   manifest**. A manifest present with zero extracted dependencies is demonstrably wrong
   (`unverified`); no manifest, with an inventory to prove it, is a real answer (`no_signal`);
   an empty inventory can prove neither, so it degrades to `unverified` — the first draft
   returned `no_signal` there, which was the same unearned confidence this vocabulary exists
   to prevent, one level up. Manifests match at any depth, or every monorepo reads as a
   provable zero.

   **Still open:** the remaining ~14 steps.

---

#### ~~Re-parent / persist ancestors to make depth control work~~ — BUILT, MEASURED, AND IT DOES NOT DELIVER THE COLLAPSE

**Live re-survey of milvus, 2026-08-24 (`repo_arch_detect`, 24.5s):**

```
before:  204 components, 0 structural, projection identical at every depth
after:   204 components, 2 structural, 1 of 206 rows has a resolved parent
         depth 0 -> 205    depth None -> 206
```

Structural nodes were built exactly as specified (separate row kind, no type, no
confidence) and they work for what they target — genuinely referenced ancestors now
resolve, 0 -> 1. **They do not produce the grouping this entry predicted, and the
prediction was wrong for a reason worth recording.**

`internal/distributed/{datanode,mixcoord,proxy,querynode,streamingnode,…}` carry
`parent_slug=''`. They reference **no parent at all**, so persisting referenced
ancestors can never synthesise `internal/distributed`. The earlier "would absorb 6"
figure in this entry was computed by string-splitting component slugs into
*hypothetical* parents and was then presented as what persisting referenced ancestors
would deliver. Those are different mechanisms. Path-prefix grouping is not the stored
parent hierarchy.

The cause is upstream and by design: `build_hierarchy` makes a directory a candidate
only if it holds **>=2 first-party files directly**. A pure container directory like
`internal/distributed` holds only subdirectories, so it is never a candidate, is never
linked to, and cannot be recovered from the reference graph.

**Path-prefix grouping was then measured as the alternative, and also does not solve it:**

```
milvus      by 1st path segment: 37 groups   by 2: 93 groups   (from 204)
genaicomps  by 1st path segment: 290 groups  by 2: 294 groups  (from 311)
```

Real reduction for milvus, useless for genaicomps — whose components are compose
service names, not paths, exactly as predicted.

**What this settles.** Depth/grouping over stored structure cannot make architecture
recovery interpretable, for either repo. The remaining lever is the one already
recorded elsewhere: distillation and the unported adjudicator (spike: Kubernetes
3303 -> 358 deterministically, then 358 -> 93 only with the LLM, which is where 6/6
held). Interpretability here is a precision problem, not a presentation one.

**Kept anyway, deliberately:** the structural-node code is correct, tested, invents no
evidence, and is what a denser hierarchy would need. It is not load-bearing for
anything today. `STAGE_PROJECTION_DEPTH` remains unwired and still meaningless.

**Original entry, which the measurement above corrects, follows.**

#### Re-parent components to the nearest SURVIVING ancestor — this is what makes depth control work

Measured 2026-08-24 while prototyping a depth control, and the reason that control
is withheld rather than built.

`arch_recovery/projection.py` works — given resolvable parents it collapses a nested
input (`tests/test_arch_projection_liveness.py`). It has never been given one:

```
milvus      depth 0/1/2/3/None -> 204 components every time
genaicomps  depth 0/1/2/3/None -> 311 components every time
```

**The gap is between generation and persistence, and each half is individually
correct.** `code_markers.build_hierarchy()` links every candidate subtree to *"the
nearest ancestor directory that is ALSO a candidate"* — right, and matches `ir.py`'s
documented contract. But persistence writes a **filtered subset** of candidates: 16
of milvus's 213 persisted slugs are `code::` namespaced. Parent links still point at
the pre-filter candidate set, so milvus references 6 parents of which **0 are
persisted**. Every node reads as root-attached and `project_rows` becomes an identity
function.

**Two ways to close it:**

1. **Re-parent at persist time** — walk up to the nearest ancestor that actually
   survives into the persisted set, and rewrite `parent_slug` to that. Keeps the
   persisted set as-is. Cheaper, and preserves whatever filtering exists for good
   reasons.
2. **Persist the referenced ancestors** — emit the intermediate candidates so the
   links resolve. Truer to "store the hierarchy, project a level"
   (`repo-analysis-funnel.md (§12)` §2a), but grows the stored set.

**Investigated 2026-08-24 — and the answer reverses that. (1) recovers nothing; (2) is
the only option that works.**

First, the candidates are not *filtered*. `code_markers` emits a Component per subtree
in `by_subtree` — subtrees with **marker-rule hits** — while `parent_slug` is read from
`hierarchy`, which holds **every** candidate subtree (any dir with >=2 first-party
files). Different sets, and nothing reconciles them. An intermediate directory like
`internal/distributed` has no markers *of its own* — its children do — so it never
becomes a Component while its children point at it. Nobody dropped it; it was never a
candidate for emission in the first place.

That makes the persisted set a **flat frontier with no internal nodes**, which is fatal
for (1). Measured on milvus's 16 persisted `code::` components:

```
ancestor/descendant pairs WITHIN the persisted set: 0
```

Not "few" — **zero**. No persisted component is an ancestor of any other, so
re-parenting to the nearest surviving ancestor rewrites every link to "" and collapses
nothing. Projection needs internal nodes and (1) cannot create them.

(2) does, and the shape it produces is the interesting part:

```
code::internal::distributed        would absorb 6 components
code::internal                     would absorb 2
code::internal::querycoordv2       would absorb 2
```

`internal/distributed` absorbing 6 is precisely the milvus ground-truth grouping —
`datanode`, `mixcoord`, `proxy`, `querynode`, `streamingnode` are 5 of the 8 published
components, currently emitted as 5 unrelated siblings with no parent. So persisting
intermediate candidates is not just what makes projection function; it is what makes
the coarse level correspond to the answer a human already published.

**Cost and caution:** these ancestors have no marker evidence of their own, so they must
be persisted as structural nodes, not as typed/scored components — with an honest
confidence and type, or none. Emitting them as ordinary components would invent
evidence, which is the failure mode the whole `no metric, no number` rule exists to
prevent (design §5). Structural-node-only is the constraint to design against.

**Why this is worth doing before any Purpose→depth work:** depth-as-presentation is
the cheap version of everything in the entry above — one run, re-rendered at several
levels, no re-survey. It is currently untestable because there is no hierarchy to
project. Until this lands, "sufficiency is a presentation rule" cannot even be
evaluated, and the only alternative left standing is the expensive one (depth as a
generation-time stopping rule).

**Related and unwired:** `STAGE_PROJECTION_DEPTH` in the same module declares the
level each stage wants (`discovery` 0, `assessment` 1, `analysis` unprojected) and is
read by nothing. It becomes meaningful the moment projection has real input.

---

#### Purpose sets required DEPTH, not just which analyses run — unwritten, and it reframes precision

Raised by the project owner, 2026-08-24, and not in any design doc. Both `investigation-framing-design.md`
and `architecture-recovery.md` were checked: nothing ties depth or completeness to
purpose. §3 of the framing design says the opposite for the neighbouring axis — *"changing
perspective changes how much you see but never what gets run."*

**The claim:** an investigation exists to meet its own objectives. Architecture recovery is
one analysis among many and is *often not relevant at all*. Where it is relevant, **how much
recovery, and to what completeness, is itself a function of the purpose.** The same is true
of every other analysis with a depth dial — documentation, dependencies, security, profiling.

**Why this matters more than it first sounds.** The current framing splits the problem as:
framing decides *which surveys run* and *what a result means to this engagement*, but does
nothing about *how many candidates a surveyor emits* — that being a separate precision
problem. **That split is wrong, or at least too absolute.** If purpose sets the required
depth, then purpose is a *stopping criterion*, and a stopping criterion changes the output
size. Concretely: 154 components against a ground truth of 8 (Milvus, finding 99) is a
useless answer for `Select` — "is there an architecture here, roughly what shape" — while
possibly a fine one for `Learn` or `Explore`. The number is not wrong in the abstract; it is
wrong *for a purpose nobody declared*.

**What already exists to build on:**

* **Completeness is already expressible.** Egeria's base annotation type carries
  `sampleSize` / `samplePercent` / `samplingMethod` — *"how much did we look at"*
  (`architecture-recovery.md` §6.1, which says to reuse them and populate them
  honestly when a component is only partially analysed). The vocabulary for *reporting*
  depth exists; nothing *decides* the depth required.
* **The one measured selection mechanism is a gate, not a weighting.**
  `expectations.recovery_gate` discriminates across all 60 repos (46 run / 8 skip / 6 none)
  by keying on evidence containment rather than on a label — the property Perspective was
  measured to lack. But it is **binary**: run or skip. The natural extension of this entry is
  a graduated gate — run *to what depth* — rather than a second taxonomy.

**Open questions, none answered yet:**

1. What is the depth dial per analysis? For arch recovery it is plausibly a component-count
   or granularity target; for others it may be sample percentage, or which sub-checks run.
2. Does depth-by-purpose belong in the analysis catalog (declared per analysis), on the
   investigation (declared once), or negotiated at dispatch?
3. Is "sufficient for this purpose" a *stopping* rule during the run, or a *presentation*
   rule over a full run? Cheaper is stopping; more reusable is presenting.

**Do not conflate this with ranking.** Purpose ranking questions/analyses (§3) and Purpose
gating RFA emission (§4) are both established. This is a third role — setting the depth
contract for a run — and it is the one that touches surveyor output size.

---

#### Build the Investigation tab — nothing tracks this, and the design assumes it

**The goal, in the design's own words** (`docs/investigation-framing-design.md` §Context, §1):

> RE today has no concept of *the piece of work you are currently doing*. You land on a
> resource and start surveying it. The eight intents describe **what kind of work is happening
> to one resource**; Perspectives describe **whose concerns filter what is shown**. Nothing
> captures **why this set of work exists at all** — and because nothing does, RE cannot decide
> what to show first, what to run by default, or whether a finding is merely evidence or
> actually somebody's problem.

An **Investigation** is one body of work — "the thing the new tab creates and the context
everything else runs inside". It is a framing step *ahead of* Scouting: declare the body of
work, its purposes and its membership, and everything downstream (which resources are visible,
which analyses are proposed, whether a failed check raises an RFA) derives from that
declaration.

**Why this entry exists:** the design was written, measured and committed (`958ac74`), and the
tab it assumes was never given a work item. The framing entry below lists eight deferred
pieces; building the tab is not among them, so the central deliverable is the one thing
nothing tracks. Confirmed 2026-08-24: zero occurrences of `Investigation` in
`web/static/index.html`, no local investigation table, no routes.

**What already exists** — and is search/bind only, by explicit decision:
`entity_egeria_project_context` (registry) + `web/routes/project_context.py`
(`/search/candidates`, GET/POST per entity), from Part 5 of
`docs/discovery-automate-project-context-plan.md`. `surveyors/egeria_project_finder.py` wraps
`ProjectManager.find_projects`.

**What is missing, in dependency order:**

1. **The local investigation table** — one row per investigation with a *nullable*
   `egeria_project_guid`. §1 calls this the single most important structural decision: it makes
   promotion to Egeria a fill-in rather than a migration. Shape the membership table like the
   target `ResourceList` relationships from day one for the same reason.
2. **The create-a-new-Egeria-Project path** — net-new, and the blocker for deferred item 1
   below (promote a local investigation). Part 5 explicitly did not build it.
3. **The tab itself** — create/select an investigation, declare Purpose(s), manage membership.
   Note Purpose **ranks, never excludes** (measured; see the framing entry), so this is
   ordering, not filtering.

**Sequencing:** 1 is standalone and unblocks everything. 3 is only worth building once 1
exists, since the tab with no table is a form with nowhere to write. 2 can lag — a local
investigation is useful before it is promotable.

**Two constraints that are easy to specify wrongly:**

* **Perspective cannot drive dispatch** (§3, measured 2026-08-24). Two incompatible
  vocabularies — 12 Title-Case names on questions, 5 snake_case on analyses — and zero
  discrimination: no perspective reaches an analysis another does not also reach. It is a
  secondary ranking axis only. A tab offering "filter analyses by Perspective" would specify
  something the catalog cannot support.
* **Purpose tagging is NOT a blocker — it is done.** `341d2f5` tagged all 41 questions;
  verified 2026-08-24 in the CSV source of truth (`docs/dr-egeria/resource_questions.csv`,
  41/41 `Purposes` filled), not just the generated YAML. The check-granularity join is built
  and guarded too (`852955f`). What remains is populating checks per question — see the
  framing entry below.

§8 of the design already lists **`Investigation record + tab`** as net-new work, alongside
"Create a new Egeria Project — net-new (Part 5 built search/bind only)". The intent was
tracked in the design; only the backlog entry was missing.

**Do not start here:** §7's two renames (`Project` → `Repo`/`Resource`,
`ProjectGroup` → `Owner`) are a separate entry with a live cross-schema tripwire — Egeria
Advisor reads RE's tables by hardcoded string in six places. Adding an Investigation concept
while `Project` still means three things is what makes the rename harder later, but it does
not block this work.

---

#### Investigation framing — the six items deferred out of the 2026-08-24 design

Full design: `docs/investigation-framing-design.md` (design only, nothing built). These are the
pieces that design deliberately left out of its own first pass.

1. **Promote a local investigation to an Egeria Project.** Create the `Project` (+ `ProjectCharter`),
   then replay each local membership row as a `ResourceList` relationship carrying its `resourceUse`.
   Design the local membership table shaped like the target relationships from day one so this stays
   a replay rather than a migration. Requires the "create a new Egeria Project" path, which Part 5
   of `docs/discovery-automate-project-context-plan.md` explicitly did not build (search/bind only).

2. **Unbind / rebind an investigation from its Egeria Project.** Falls out of the nullable
   `egeria_project_guid` model but has no answer yet for what happens to relationships already
   published under the old binding.

3. **Perspective: two vocabularies that cannot be joined, and zero dispatch discrimination.**
   Questions carry the 12 Title-Case Glossary names; analyses carry 5 snake_case values (`all`,
   `security`, `steward`, `data_scientist`, `dba`). `data_scientist`/`dba` don't exist in the question
   vocabulary; `Architecture` and `Admin` (25 questions each) have no analysis counterpart. Worse,
   measured 2026-08-24: **not one of the twelve perspectives reaches a single analysis another
   perspective doesn't also reach** — the sets are strictly nested, and `Privacy` reaches none at all.
   Perspective filters *how much* you see, never *what runs*. Fine for its actual job (display
   filtering, which is what the tags were assigned for); unusable for dispatch. See
   `docs/investigation-framing-design.md` §3, which was rewritten around this.

4. **Upstream: a `CertificationType` → checklist relationship.** Egeria has no way to say what checks
   a certification is composed of. `OpenMetadataTypesArchive5_3.java:736-772` set the precedent with
   `DataStructureDefinition` (`CertificationType` → `DataStructure`, *"the specification used to
   certify data"*); the scorecard analogue would point at `GovernanceRule`/`Requirement` definitions.
   Purely additive, changes no existing semantics — which is why it stands a chance upstream, unlike
   adding a verdict field to the `Certification` relationship (considered and rejected; see the
   design doc §4). Not a blocker: nothing in the failure path needs it.

5. **Two undeclared scores.** `documentation.py:151-167` (`score = len(present) + len(found)`, then
   hardcoded thresholds to a quality label) and `health.py:118-159` ("Overall health score: X/100")
   both emit numbers RE authored, with no `GovernanceMetric` behind them. Under the rule agreed
   2026-08-24 — *no metric, no number* — each needs either a declared `GovernanceMetric` with
   `measurement`/`target` (following the Portal's Governance Metrics pattern) or removal.
   `sql_analyzer.py:145-153`'s `complexity_score` needs the same check.

6. **~~Tag Purpose, and add the check-granularity join~~ — BOTH DONE; entry was stale.**
   `341d2f5` tagged all 41 questions with Purpose (not the ~10-question pilot this entry
   proposed — the pilot was skipped and the full set measured directly). `852955f` built the
   check-granularity join: `configdata/check_registry.yaml` declares the per-check vocabulary
   for 8 analyses, `question_catalog_reader.py` validates against it, and
   `tests/test_check_registry.py` guards it (10 tests). The join's own trap is documented in
   that file's header — three analyses write findings under a different `kind` than their
   catalog id (`security_scan`→`security_hygiene`, `documentation_coverage`→`documentation`,
   `sub_resource_survey`→`repo_sub_resource_survey`).

   **What the measurement settled, and still holds:** Purpose fails the exclusivity bar (0/8)
   exactly as Perspective did — but that bar is unachievable by construction and encodes a false
   premise, since purposes genuinely overlap. On the fair metric Purpose is the better axis:
   mean pairwise overlap 0.22 vs Perspective's 0.37, nested pairs 6 vs 18. Purpose **ranks**,
   never excludes.

   **What is actually open — the vocabulary is declared, the data is not populated.** Measured
   2026-08-24: of the questions whose `answering.kind` is `analysis`, only **one of five**
   carries an explicit check (`license_classification:license_risk_tier`); the rest have
   `checks: []` and still join at analysis granularity. `repo_conventions` bundling five checks
   (`ingestion/repo_conventions_parser.py:97-179`) is the case the join was built for and is
   not yet expressed. Populating it is data entry against a guarded schema, not new mechanism.

   Note the earlier "16 analysis-answerable questions" figure in this entry no longer matches
   the file: today's `answering.kind` vocabulary is `analysis` 5, `mixed` 6, `direct` 11,
   `gap` 8, `human` 7, `chart` 1, `unknown` 3. The vocabulary changed after that measurement;
   the ratio it reported was not re-derived. Re-measure before quoting it.

   Generation path unchanged: `question_catalog.yaml` is generated from
   `docs/dr-egeria/resource_questions.csv` via `scripts/csv_to_question_catalog_yaml.py` —
   don't hand-edit the YAML.

7. **`ResearchQuestion` (0430) for per-investigation open questions.** Complementary to the existing
   `GlossaryTerm` + `Question` catalog, not a replacement — no migration. Gives an investigation
   somewhere to record the questions it exists to answer, scoped via `GovernanceDefinitionScope` to
   the Project. Unmatched ones are the observed growth path for the standing catalog.

8. **Move `scheduler.py` subscriptions onto `watchResource`.** `ResourceList` (0019) already carries
   `watchResource` — *"whether the parent entity should receive notification about changes to the
   supporting resource"* — which is exactly what `notification_subscriptions` is doing locally. Once
   investigations are Egeria-bound, the subscription flag belongs on the relationship. Related to
   Automate's local-first decision and `docs/automate-notification-manager-pyegeria-spec.md`.

---

#### Egeria ↔ RE sync/divergence reconciliation — DETECTION BUILT 2026-08-20, resolution partly open

**Built:** `resource_explorer/egeria_linkage.py` detects "that GUID does not exist here"
at the point of use and, instead of the opaque `SERVER_ERROR_500` that reached the UI
verbatim, records the divergence in the new `egeria_linkage_status` table, raises an RFA,
and throws a named error that says what happened and what the three choices are.

**Corrected 2026-08-20 by testing against live Egeria with a deliberately bad GUID** — the
first version guarded five paths on the assumption all five consume a cached GUID. Only
three do:

| path | cached GUID | by-name fallback | guarded |
|---|---|---|---|
| repo publish | yes | **none** — a stale GUID is fatal | yes |
| filesystem `publish_step_annotations` | yes (`guid or _find_element_guid(...)`) | yes | yes |
| database `catalog_and_survey` | yes (as the *server* element) | yes | yes |
| filesystem `catalog_and_survey` | no | yes | no |
| database `publish_step_annotations` | no | yes | no |

The two unguarded ones resolve their element by name every time, so a stale cached GUID
cannot break them — and a guard there could only misattribute an unrelated lookup failure
to a GUID that was never used. Tests assert the placement in *both* directions, plus that
the classification still matches what the code does, so the table above cannot quietly rot.

**The real defect that live testing exposed:** in database `catalog_and_survey`, the stale
GUID does produce exactly the error the detector recognises — at
`_initiate_survey("PostgreSQL Server", server_guid)` — but the surrounding
`except Exception: log.warning(...non-fatal...)` swallowed it, and the method returned
success with `server_survey_guid=''`. The wrapping guard never saw it because the exception
never escaped. Since the cataloging work genuinely does succeed there, this stays non-fatal,
but it now records the divergence and raises the RFA: "non-fatal" must not mean "invisible",
or every later run skips the server survey the same silent way.

The detector was validated against this deployment's live Egeria rather than against a
paraphrase — asking for a GUID that cannot exist returns `OMAG-REPOSITORY-HANDLER-404-007`
wrapping `OMRS-REPOSITORY-404-002`, and that verbatim message is now a test fixture. Two
things that probe corrected: the outer code is `OMAG-REPOSITORY-HANDLER-404-007`, not the
`OMRS-REPOSITORY-404-007` recorded here from the original report; and the response labels
itself `CLIENT_ERROR_400` while `relatedHTTPCode` is 404, which is why detection keys on
Egeria's message codes and not on HTTP status.

**Held to "detect, don't auto-resolve" as decided:** the cached GUID is deliberately *not*
cleared on detection. It is kept so a human can see what RE had and so republish can report
what it is replacing.

`GET /api/egeria/linkage/stale` lists divergences; `POST /api/egeria/linkage/{type}/{slug}/resolve`
takes `republish` | `resurvey` | `discard`. All three clear the unusable GUID and the
divergence record — that is what unblocks the resource, and is common to every choice.

**Still open:**
- **republish/resurvey run the follow-up work for repos only.** For databases and
  filesystems the link is cleared and the caller is told which existing action to run.
  Re-publishing those from cached local data needs per-type orchestration — a database
  publish reconstructs `schema_info` and fires Egeria's native survey — which is the real
  reason it is not built here.

  **Correction (2026-08-20):** the commit that added this said there was "no registered
  database or filesystem in this deployment". That was wrong and was never checked — only
  filesystems were. There are two registered databases, `localhost_docker_coco_ods` and
  `localhost_docker_coco_pharma`; filesystems are genuinely zero. Neither database carries
  an `egeria_asset_guid`, so neither can exhibit this divergence today — a stale link needs
  a cached GUID first. So the path is testable in principle, but only after cataloging one
  of them in Egeria, which is a real write to the live catalog and a deliberate choice
  rather than a side effect of verification.
- **`discard` clears the Egeria linkage; it does not purge RE's local survey data**, which
  is the stronger reading in the original note above. Deleting a user's survey history is
  hard to reverse and should not happen behind a single API call — it needs its own
  confirmation path before being built.
- **Detection is reactive only** (open question 1). A proactive GUID-existence sweep would
  have to decide how often to re-check every cataloged entity; the failure is rare and now
  loud, so this was not worth paying for yet.
- **Open question 3, partly answered 2026-08-20.** The by-name fallback works: with
  `coco_ods` cataloged, `_find_element_guid("coco_ods")` returns the same GUID as the cache
  (`c2e8bb6c-…`), so a registry that has lost its GUID can recover it. Two of the five paths
  above rely on that route exclusively and are therefore immune to the forward case. Still
  unverified end to end against a genuinely reset RE database.
- ~~**No UI for resolve.**~~ **BUILT 2026-08-20.** Admin ▸ 🔗 Egeria Links lists every
  divergence with Republish / Re-survey / Discard, and an affected repo shows the same three
  actions as a banner on its Scouting card — placed directly above the "☁ Published to
  Egeria" badge, which is actively misleading while the link is broken since it reports a
  catalog entry RE can no longer reach. Both call one shared button-builder so the wording of
  a destructive-sounding choice cannot differ between them.

  Found while verifying: `discard` reported "RE's local survey results are untouched" while
  also deleting `project_egeria_surveys` — the record of past publishes. Those GUIDs point
  into the repository that no longer has the asset, and the publish history itself remains in
  the activity log, so nothing of value was preserved by keeping them; but the sentence was
  not true, and the one action a user might fear is the wrong place to be imprecise. It now
  names the count it removes and what survives.

---

#### RFAs should become real Egeria actions, not just descriptive annotations — needs a deeper dive

Every `RequestForActionAnnotation` RE produces today (repo security/doc gaps, the new filesystem inaccessible/unclassified/profiling-failure RFAs added 2026-07-13 — see the filesystem analytics item below) is purely descriptive: it's an `Annotation` attached to a `SurveyReport`, published via `EgeriaPublisher`'s `RequestForActionProperties` mapping (`egeria_publisher.py`). Nobody is notified, nothing is assigned, there's no due date or lifecycle status. A human has to know to go look at the survey report to ever see it.

**Confirmed so far (2026-07-13, quick pass through pyegeria, not yet a full design):** Egeria has a separate, genuinely actionable mechanism — a `ToDo`/"person action" element, distinct from a survey Annotation. `pyegeria/omvs/my_profile.py::create_my_todo`/`_async_create_my_todo`, backed by the general-purpose `pyegeria/omvs/asset_maker.py::_async_create_action` (`ActionRequestBody`), supports: `assignToActorGUID` (assign to any actor, not just the calling user — the `my_profile.py` wrapper is just a "my" convenience, the underlying call is not actor-scoped), `actionSponsorGUID`, `originatorGUID`, `newActionTargets` (linking the action to specific elements — e.g. the actual offending file/table, not just prose), and a full lifecycle (`activityStatus`: REQUESTED/APPROVED/WAITING/IN_PROGRESS/COMPLETED/FAILED/CANCELLED/etc. — see `pyegeria/core/_globals.py::ACTIVITY_STATUS`, `dueTime`, `priority`, `lastReviewTime`). The docstring itself notes a `ToDo` is one of several "person action" kinds — "Meeting, ToDo, Notification, Review" — so there's a whole small taxonomy here, not just one element type.

Also spotted, not yet chased down: a distinct `steward`/`stewardTypeName`/`stewardPropertyName` property pattern that shows up on collection-membership and classification relationships (`pyegeria/omvs/collection_manager.py`, `classification_explorer.py`) — "who validated this" rather than "who needs to act on this." These look related but are probably not the same concept as ToDo assignment, and it's not yet clear how (or whether) they're meant to compose — e.g. does a steward get auto-assigned the ToDo for things in their stewardship scope?

**Deliberately not designed yet — this needs its own research pass, not a bolt-on:** two unexplored pyegeria OMVS modules that are very likely load-bearing for this — `actor_manager.py` (actor/role model — who can be assigned, how roles relate to stewardship) and `community_matters_omvs.py` (ties into the existing, also-unresolved "journaling discoveries as blog-style entries visible to particular communities" open question in the A2A item below — notification/audience may be a community concept, not just a 1:1 assignment). Also needs: which RFAs should actually become assignable `ToDo`s vs. staying descriptive-only (probably not every annotation warrants interrupting a human), who the default assignee/sponsor is when RE has no obvious human to name (survey run by an unattended schedule vs. a logged-in user), and whether this should be built as a generic `EgeriaPublisher`/executor-level capability (any `RequestForActionAnnotation` optionally promotable to a `ToDo`) rather than something each resource type's publish path reimplements.

Related/overlapping open items: the A2A item's "Rendezvous for results" open point (notification mechanism, journaling, comments as candidates alongside the activity log) and the "unify survey launching" item's unified-dashboard goal — a real ToDo/action queue could end up being part of that unified view rather than a separate concept.

---

#### Egeria ↔ Resource Explorer A2A collaboration (bidirectional)

RE currently only calls *into* Egeria (triggering native surveys via `AutomatedCuration`/`initiate_postgres_*_survey`, publishing annotations via `EgeriaPublisher`). There is no path for Egeria's own automation (governance action processes, engine actions) to call *into* RE — e.g. to dispatch one of RE's Python surveyors as part of an Egeria-orchestrated workflow.

Direction agreed: RE should expose itself as an **A2A-callable surface** (extending the existing `agentstack_server.py` per-agent pattern) that Egeria can invoke as if it were any other governance/survey action service. Two reasons A2A over a bespoke REST contract: (1) A2A's task-state model (`input_required`, streaming, polling) already matches the async-survey-result problem RE works around manually today in `HybridDatabaseSurveyor`; (2) it's protocol RE already speaks, so other orchestrators (not just Egeria) get the same capability for free.

**Deferred pending:** input from Mandy (owner of Egeria's core Java / connector frameworks) on what the Egeria-side connector shape should be — likely does not require a new OCF connector *type*, but the specifics should follow her judgment on precedent in the existing wide range of Egeria connectors.

**Known open design points once picked up:**
- New per-capability A2A agent (own port, per the one-agent-per-`Server` rule) using structured `DataPart` payloads (asset GUID, resource type, surveyor/analysis name, options) rather than the natural-language `TextPart` pattern the existing chat agents use.
- Auth: `agentstack_server.py` currently has no caller authentication — fine for an internal chat agent, not sufficient for a surface Egeria automation is meant to trust. **Resolved:** use Egeria's existing bearer-token approach and security services directly; no separate RE auth namespace/scheme needed.
- Rendezvous for results: the existing `activity_log`/RFA schema (see `docs/survey-model.md` D3, D8) is RE's own operational record, but it's not the only channel results should flow through — Egeria's notification mechanism, journaling discoveries as blog-style entries visible to particular communities, comments, and formal reports are all candidates depending on audience, and these aren't mutually exclusive with the activity log.

Full context: `docs/egeria-integration.md`, section 2.

---

#### Survey/Analysis model conformance to Egeria Area 6

RE's survey model (fixed pipeline of sub-surveyors, one `SurveyResult` per run) doesn't yet reflect Egeria's actual Area 6 mechanics — composable `AnalysisStep` phases within a survey, embeddable survey-pipeline connectors, declarative annotation-type catalogs, standard completion guards, and (critically) no existing built-in notion of different survey "kinds" (shallow sweep vs. deep focused, persona-tailored presentation). RE will likely need to grow more variety of survey/analysis "kinds" faster than Egeria's own connector catalog does — that's fine as long as Egeria stays the system of record — but RE's internal model should still speak Egeria's vocabulary where a precedent exists.

Full context, grounded in the actual Egeria Java source: `docs/egeria-integration.md`, section 3.

---

#### Coherent selective-cataloging model

No coherent model today for *what* to catalog and how to catalog things in groups (e.g. repo file-type checkboxes exist, but nothing like "file type AND touched in the last N months"; no selectivity at all for database or filesystem surveys). Need a general flow: Discover → Survey (broad) → Analyze/Question/Select → Survey (deep, often on the selected subset) → Catalog (side effect of deep survey or an explicit action) — with surveys triggerable by a human, on a schedule, or by Egeria automation.

Egeria has no direct precedent for "survey broadly across not-yet-cataloged resources, then selectively catalog a subset" — current Area 6 surveys always run against an asset that's already cataloged. This is genuinely new territory for RE to define, composed from existing Egeria primitives (`RequestForAction` annotations + completion guards + `GovernanceActionProcess` chaining), and possibly worth proposing back into Egeria core once proven.

Full context: `docs/egeria-integration.md`, section 4.

---

#### Dr.Egeria as the authoring format for Survey Definitions

Direction agreed and now grounded: Dr.Egeria (RE's markdown DSL, already used via MCP and in Egeria Advisor) is the authoring format for Survey Definitions — not as a runtime trigger mechanism (MCP/Dr.Egeria command round-trips are likely too inefficient for that; A2A stays the trigger path, see the item above), but as a design-time spec format, authored in Egeria Advisor's existing plan editor. **Grounded finding: no new Dr.Egeria commands needed.** Egeria has no dedicated "SurveyActionType" open-metadata type — the closest real, catalogable element is `GovernanceActionType`, and Dr.Egeria's existing "Action Author" family already covers authoring a Survey Definition's composition end to end: each step is a `Create Governance Action Process Step` (not the more generic `Create Governance Action Type`, which is for standalone action templates never chained into a process), the survey as a whole is a `Create Governance Action Process`, and `Link First/Next Process Step` sequences them. RE-specific info (execution location, target technology type, which RE sub-surveyor a step maps to) is proposed to live in the `Additional Properties` dictionary attribute that already exists on every element in this chain — a documented key convention (`executes_at`, `supported_technology_type`, `re_analysis_step`), not a schema change.

**Conditional execution, partially resolved:** `Link Next Process Step`'s existing `Guard`/`Mandatory Guard` attributes already give real step-to-step branching (a step produces a guard, different `Link Next Process Step` commands route to different next steps based on it) — no new syntax needed for that. Still open: whether conditional logic is ever needed *within* one step's own parameters (not just branching between steps) — needs a requirements pass with concrete example Survey Definitions to answer.

`executes_at` is deliberately an open, extensible value (not a two-value enum) — `egeria` and `resource-explorer` are the first two, but other execution engines (Airflow, most obviously) should be nameable here too without a schema change, since it's a free-text dictionary value.

**RE's read/execute side is now implemented** (see the "RE locally executing Survey Definitions" item above) — the reader/executor have been exercised structurally (graph parsing, branching/cycle rejection, dispatch logic all unit-tested against canned fixtures), though authoring a real Survey Definition via Dr.Egeria and running it against a live server hasn't been validated yet.

**Not yet solved:** an `executes_at: resource-explorer` tag on a step is just catalogable metadata — nothing makes Egeria's engine host dispatch to RE *without RE itself initiating the run*. That specific case depends on the A2A item landing first. RE executing its own steps on its own initiative does not have this dependency — see the "RE locally executing Survey Definitions" item above, now implemented.

Full context: `docs/egeria-integration.md`, section 6, and open questions A6–A9, A12.

---

#### Investigations carry no zone, and their local classification never reaches Egeria at all

**Found 2026-09-07** answering a question about whether investigation type (Personal/Study vs.
Task/Campaign) drives governance-zone placement. It doesn't — and tracing why turned up a gap
one step earlier than zones.

**No zone assignment exists for investigations, period.** Zone handling lives entirely in
`egeria_publisher.py` (survey/asset publishing) — every published asset gets `ZoneMembership`
set to one flat draft zone (`resource-explorer-draft`, created once at worker startup) regardless
of type, promoted to `EXPLORER_PUBLISH_ZONES` only via a separate manual "curate accept" step
(the "one zone per app" model, decided 2026-09-04). `egeria_investigation_publisher.py` — which
creates the Egeria `Project` and its `Folio`/`WorkingSet` `Collection`s for an investigation — has
no `ZoneMembership` anywhere in it. Read in full to confirm.

**The classification a zone decision would need to key off isn't reaching Egeria either.**
`registry.py` already models exactly this: `PROJECT_CLASSIFICATIONS = ("PersonalProject", "Task",
"StudyProject", "Campaign")`, captured on `create_investigation()` and stored per-investigation
(`registry.py:5441`, `:5445`). But `egeria_investigation_publisher.py::promote()` builds the
Egeria `Project` with a hardcoded `"typeName": "Project"` and never reads
`inv.get("project_classification")` (around line 141-148) — so Egeria never learns whether a
given investigation is Personal or a Campaign. The distinction lives only in RE's local row.

**So this is two pieces of work, not one, and the first blocks the second:**

1. **Push `project_classification` into the Egeria `Project` on promote.** Concrete, well-scoped
   — `promote()` already has `inv` and builds `properties` right there; needs the classification
   applied (as `typeName` if Egeria models these as `Project` subtypes the way `Folio`/`WorkingSet`
   are `Collection` subtypes — matching `_create_typed_collection`'s existing pattern — or as an
   initial classification, whichever `ProjectProperties`/pyegeria actually supports; unconfirmed,
   check before building). Until this lands, part 2 has nothing to key off.
2. **Classification → zone mapping.** Larger and more speculative — which zones, and "the user's
   own zone" implies per-user zones, which `docs/egeria-integration.md`'s zone section already
   flagged as a possible future need without designing it. Not structurally forbidden: Egeria's
   own `0424 Governance Zones` type doc describes `ZoneMembership` as a general element
   classification (not Asset-restricted) — "an element may belong to many Governance Zones" — so
   attaching it to a `Project` isn't ruled out by the type system. Needs a real design pass before
   building: which classifications map to which zones, whether "more public" for Task/Campaign
   means the existing publish zones or something new, and how this interacts with the existing
   draft-zone/curate-accept model that asset publishing already uses for a related but distinct
   purpose.

Full context: this conversation, 2026-09-07; `docs/investigation-framing-design.md` §1 (defines
the classifications, says nothing about zones); `docs/egeria-integration.md` (zone section, for
the per-user-zone flag).

---

### Analysis & surveyors

#### DONE 2026-08-31 — Survey Results dashboards covered only 14 of 29 analyses; now covers all 25 findings-producing ones

*(Opened 2026-08-31, from a results audit against `docs/dr-egeria/resource_questions.csv` — see
the companion entry below on stage/intent mismatches, found in the same pass.)*

`SURVEY_RESULT_DASHBOARDS` (`repo_survey_definition_adapter.py`) is 6 hand-authored dashboards,
unchanged since `docs/survey-model.md (§6)` introduced it — which says so itself:
*"Framework is the point of this pass — six real dashboards prove it end-to-end; more are a
one-entry addition afterward, not a new mechanism."* That follow-through never happened. The
catalog it was written against had 15 analyses; it now has 29, and the dashboard count never
moved.

**Measured:** 15 of 29 analyses have no dashboard. 4 are legitimate non-candidates — actions
without findings, not surveys (`egeria_publish`, `rag_ingestion`, `website_ingestion`,
`repo_profile_refresh`). **11 are real, findings-producing analyses with nowhere to show up in
any Results tab:**

- Assessment: `chaoss_metrics`, `cii_badge`, `community_support`, `cve_scan`, `foss_scorecard`
- Discovery/Analysis: `architecture_doc_lens`, `architecture_recovery`, `architecture_summary`,
  `interface_surface`, `repo_classification`, `manifest_parse`

Confirmed this is *not* a stage-tagging bug: `get_dashboard_stages()` correctly derives a
dashboard's stage(s) from `analysis_catalog.yaml`'s `intent` field (matching what the Survey tab
routes by), not from the questions CSV — so Survey and Results already agree with each other on
placement. The gap is purely dashboard **membership**: these 11 ids were never added to a
dashboard, one-entry-at-a-time, the way the plan doc said they would be.

This is very likely the concrete shape of "the Results tab doesn't show results from all the
surveys" (live-reported 2026-08-31) — the mental model of "card = summary, Results tab =
in-depth" is the intended design, just an unfinished rollout rather than a different design.

**Status: 3 of the 11 closed 2026-08-31**, folded into one new dashboard. Added `architecture_overview`
(`architecture_recovery` + `architecture_summary` + `architecture_doc_lens`) as a
`render="grouped_cards"` dashboard — no new frontend code, same pattern
`documentation_conventions` already proved out. Confirmed the pattern generalizes cleanly, but it
also exposed a real, separate bug in `_results_have_data` (`web/routes/projects.py`): a never-run
result shaped `{"state": "never_run", "message": "..."}` (not wrapped in `_status`) read as "has
data" because the explanatory `message` string is non-empty, and `architecture_recovery`'s own
decorative `documentation` field (documentation-SITE ingestion status, unrelated to its own
findings) did the same. Both fixed and pinned with regression tests
(`test_security_features_visibility.py`) — latent since result_status.py's vocabulary was
adopted, just never triggered because none of the three analyses had reached a dashboard's
`has_results` check before.

**Remaining 8 closed 2026-08-31.** Folded rather than each given its own card, per the plan doc's
own precedent that a dashboard can be a single item (`dependencies` already was one):

- `cve_scan`, `foss_scorecard`, `cii_badge` → into `security_overview` (same "is this
  trustworthy" question, asked from outside the repo instead of inside it)
- `community_support`, `chaoss_metrics` → into `health_maturity` (community/activity signal,
  same topic `repository_health` already reports a cruder version of)
- `interface_surface` → into `code_structure` (same "what does this expose" surface
  `api_structure` already reports, from declared dependencies rather than parsed source)
- `repo_classification`, `manifest_parse` → each a new single-item dashboard — neither fit an
  existing theme (classification is its own question; `manifest_parse` is a refresh operation,
  not a topic), so forcing them into one would have been worse than a dashboard of one.

**Closed 2026-08-31:** `security_overview`'s custom scorecard renderer now has dedicated tiles for
the three new ids. Sourced from a new `headline` field added to the Tier-2 `/survey-results`
payload alongside `results` (each analysis's existing `headline_reader` — the same one the Tier-1
stat tiles already use — rather than re-deriving a summary from raw findings in JS, which would
have duplicated that logic and let the two summaries drift). `headline_reader`'s tone vocabulary
(`good`/`bad`/`neutral`/…) differs from the tile helper's (`ok`/`warn`/`gap`/…) — mapped, not
unified, since both exist independently elsewhere in this codebase already.

Locked in with a new ratchet test (`test_every_findings_producing_analysis_has_a_dashboard`,
`test_survey_results_routes.py`) — a future analysis added to the catalog with no dashboard now
fails loudly instead of sitting invisible until the next hand audit.

#### MEDIUM — 27 stage/intent mismatches between the questions CSV and the analysis catalog; one question is unreachable

*(Opened 2026-08-31, from the same results audit.)*

`docs/dr-egeria/resource_questions.csv`'s "Funnel Stage" column and `analysis_catalog.yaml`'s
`intent` field are two different axes **by deliberate design** — stage is "when a user would
naturally ask this," intent is "which cost tier the analysis belongs to" (CLAUDE.md rule 17).
Measured cross-check (`question_catalog_reader.get_questions()` against
`analysis_catalog_reader.get_analyses()`, no dangling references found — that invariant holds):

**27 of ~49 questions are filed under one stage while the analysis answering them carries a
different `intent`.** Concentrated (20 of 27) in "Analysis"-stage questions answered by
Discovery- or Assessment-tagged analyses — e.g. "Is there a current, published, security
analysis?" is filed under Analysis but answered by `security_scan`/`cve_scan`/`foss_scorecard`/
`cii_badge`/`security_features`, all `intent: assessment`. The split is intentional and the
Results dashboards correctly key off `intent`, not stage (see `get_dashboard_stages()`'s own
docstring) — but nothing in the UI tells a user standing in one stage's Questions tab that the
evidence for a question actually lives under a different stage's Survey/Results tabs. Worth a UI
affordance (a link from a Questions-tab answer to the stage that actually holds its evidence)
more than a re-tagging pass — re-tagging would fight the Discovery-tier cost-tier logic rule 17
already argues for.

**One question is orphaned entirely:** a CSV row tagged stage=`Automate` ("How much has changed
since the last time this was surveyed — is it worth re-running now?"), but `automate` is not in
`index.html`'s `_QUESTION_PHASES` list and Automate's own subnav never offers a Questions tab —
so this authored question has no reachable home in the UI today.

#### `DependencyParser` covers 4 ecosystems; `_MANIFESTS` claims 12

Found 2026-08-23 while checking whether the repos reporting zero dependencies genuinely had
none. Nine of thirteen did. **Four did not**, and they cluster by build system:

| repo | manifest present | parser support |
|---|---|---|
| `egeria_git` (6,016 files) | `build.gradle` | none |
| `docling_java` | `build.gradle.kts` | none |
| `ol_diff` | `build.gradle` | none |
| `unitycatalog_rs` | `Cargo.toml` | none |

`DependencyParser.parse()` globs for exactly `pyproject.toml`, `requirements*.txt`,
`setup.py`, `package.json`, `go.mod` and `pom.xml` — Python, Node, Go, Maven. But
`sub_surveyors/dependency.py`'s `_MANIFESTS` — the *known-positive* set that decides whether a
zero is provable — also lists `build.gradle`, `build.gradle.kts`, `Cargo.toml`, `Gemfile`,
`composer.json`, `setup.cfg` and `Pipfile`. So a Gradle or Rust repo ships a manifest the
surveyor recognises and the parser cannot read.

**The vocabulary is already handling this correctly, which is why it is a backlog item rather
than an incident.** Those four report `unverified` ("a manifest is here and nothing was
parsed"), not `no_signal` ("this repo declares no dependencies"). Without that distinction
Egeria's own repo would read as having zero dependencies.

Two ways to close it, and they are not equivalent:

1. **Add Gradle and Cargo parsers.** The honest fix — `build.gradle`/`build.gradle.kts` and
   `Cargo.toml` are the two that actually occur in this corpus. Gemfile/composer.json have no
   instances here yet.
2. **Narrow `_MANIFESTS` to what the parser supports.** Cheaper and *wrong*: a Gradle repo
   would then claim a **provable** zero, which is worse than the current admission of
   ignorance. Do not do this without also removing the ecosystems from the surveyor's remit.

---

#### MEDIUM — `/next` has no real Search/Discover screen; the sidebar's find action is a same-noun-for-every-type stub

Raised by the project owner, 2026-09-18, live-testing `/next`: "did we lose Search? It used to be on
the Scouting stage." It wasn't lost — `SPEC-ACTIONABLE-AND-HONEST.md` point 2 (2026-09-15, before
this round of work) deliberately moved it from a mislabeled Scouting-stage "Search" stub to a
corpus-level circle-plus icon beside the sidebar's Repos/DBs/FS switcher, since finding candidate
resources isn't specific to one stage. But the thing that moved is itself just a stub: `'find-repos'`
in `next/app.js` opens a dialog that says "not built in /next yet" and links to classic
(`app.js:2017-2026`) — it has never had real functionality in `/next`.

**Classic's real mechanism is not one screen — it's three genuinely different ones per resource
type**, confirmed by reading the actual code, not assumed from the shared icon:
- **Repos**: a real "Search GitHub" panel (org/language/topic filters, save-as-source, GitHub
  base-URL override) plus a "From a list" CSV/URL-list importer, both routing into the same
  review table (`index.html:15674-15708`, `_scoutSourceMode`).
- **Databases**: server-side introspection, not a search box — connect to an already-registered DB
  server and list what databases exist on it for one-click registration
  (`POST /api/db-servers/{slug}/discover`, `index.html:6950-7008`).
- **Filesystems**: not yet investigated in this pass — likely its own separate mechanism again, not
  a text search; check before assuming it's closer to either of the above.

A same-icon, same-label stub across all three tabs was actively wrong until PR #142 fixed the
copy to at least name the right noun per tab (`FIND_TITLE`) — but the underlying screen still
doesn't exist for any of the three. This needs a real design pass (probably three separate builds,
not one generic "search" component, given how different the three mechanisms are) before it's
buildable.

---

#### LOW — sidebar group collapse doesn't respond to clicks for roughly the first minute after app launch

Raised by the project owner, 2026-09-18, live-testing: clicking a group `<summary>` does nothing at
first launch; it starts working correctly after about a minute with no page reload. Investigated but
**not root-caused** — recording what was ruled out rather than a guess dressed as a fix:

- The click handler (`toggleGroupCollapsed`, wired in `bindSidebar()`) and the persistence mechanism
  (`COLLAPSED_GROUPS_KEY` in `localStorage`) don't depend on any known 60-second timer.
- `re-api.js`'s `CACHE_TTL_MS = 60_000` (used by `listGroups()` and a few other vocabulary calls) is
  a suspicious timing coincidence but only feeds group *display names*
  (`app.js:1726`'s `groupName()`), not the grouping-by-`group_slug` or the collapse toggle itself —
  ruled out as the direct cause on inspection, though not proven unrelated.
- The user confirmed clicking does **nothing visible** during the affected window (not a
  collapse-then-immediately-reopen flicker), which argues against a rapid-re-render-undoing-the-click
  theory and more toward the click listener not being live yet, or landing on a DOM node about to be
  replaced by an in-flight startup render.

**Next step, not yet done**: reproduce against a genuinely cold server start (this checkout serves
the live app continuously, so a safe repro needs coordinating a restart) with the browser console
and Network tab open, to see what's still in flight during the affected window and whether the click
listener is actually attached to the node the click lands on.

---

#### MEDIUM — `tailwind-next.css` has no build-freshness check and will silently go stale again

Found 2026-09-17/18, live: the RFA drawer (`next/rfa.js`) rendered as an unstyled block at the
bottom of the page instead of a fixed right-hand panel — `inset-y-0`/`z-[80]`/`w-[26rem]` were
absent from the compiled `next/tailwind-next.css`, which hadn't been rebuilt
(`frontend-build`'s `npm run build:css:next`) since 2026-09-13, while `rfa.js` and other `/next`
files kept changing through items 3/5/6/9/10/11. Any class introduced after the last build and
not coincidentally already present was silently unstyled — no error, no visual cue beyond the
broken layout itself. Rebuilt as an immediate fix; recording the process gap here since nothing
stops it recurring for the next item that touches `/next`'s JS/HTML.

Two ways to close it, not mutually exclusive:
1. **A CI check** that rebuilds `tailwind-next.css` fresh and diffs it against the committed one
   — fails loudly the moment someone forgets, the same shape as
   `test_every_findings_producing_analysis_has_a_dashboard` elsewhere in this backlog.
2. **Make it part of the item-completion checklist** alongside the already-required
   `*-IMPLEMENTED.md` doc — a `/next` item isn't done until `build:css:next` has been re-run
   against its own changes.

---

#### LOW — architecture recovery's primary-component pick should be best-evidenced, not most-recent

Named by the designer, 2026-09-17, while specifying item 3 (Curate)'s build-ready spec
(`SPEC-CURATE-SELECTION-AND-BLUEPRINTS.md`) — out of that item's scope, recorded here rather than
dropped.

`repo_survey_definition_adapter.py:2951` picks the single overall "primary" component proposal for
a scope with `max(comp_rows, key=lambda r: r["surveyed_at"])` — whichever extractor run happens to
be newest wins, regardless of which proposal is better supported. `RULING-WHAT-A-VERDICT-IS-ABOUT.md`
§2a/§2b already established that `detect` and `coupling` are independent proposers kept separately
(grouped by `run_label`, not collapsed) precisely so one doesn't silently win over the other — this
`latest` pick is the one place that principle doesn't reach, since it still exists as the single
overall value anything not yet reading `proposals` depends on. Fixing it means defining "best
evidenced" (more corroborating evidence rows? a higher-confidence extractor named as such?) before
changing the selection — a design question, not a one-line swap.

---

#### MEDIUM — an optional, separate survey: resolve the transitive dependency tree and audit the full graph against OSV.dev

Raised by the project owner, 2026-09-18, after confirming what `cve_scan` actually covers today.

**Confirmed current state**: `cve_scan.py` genuinely queries OSV.dev live (`https://api.osv.dev/v1/querybatch`,
`cve_scan.py:47/171`) — that part is real, not a proposal. But `DependencyParser`
(`ingestion/dependency_parser.py`) only reads manifest files (`pyproject.toml`, `package.json`,
`go.mod`, etc.) and never a lockfile (`package-lock.json`, `poetry.lock`, `uv.lock`, `go.sum`,
`Cargo.lock`) — no transitive resolution happens anywhere in the pipeline. This is already flagged
honestly in the code itself: `cve_scan.py` carries `"excludes_transitive": True` and states outright
"declared dependencies only — transitive ones are not covered"; `members.py:124` says the same
independently. So the OSV audit today only ever sees first-party declared dependencies — a
vulnerability sitting two or three levels deep in a transitive dependency, which is where most
real-world CVE exposure actually lives, is structurally invisible to the current scan.

**Decision (project owner, 2026-09-18):** this should be built as an **optional, additional
survey**, not folded into the standard `cve_scan`/first-party pipeline — full transitive resolution
is a meaningfully heavier operation per ecosystem (each lockfile format is different: npm's
`package-lock.json`, Python's `poetry.lock`/`uv.lock`, Go's `go.sum`, Rust's `Cargo.lock`, at
minimum), and shouldn't become mandatory overhead on every routine scan.

**Scope for a future design pass**: per-ecosystem lockfile parsers (probably one new parser per
ecosystem rather than one generic one, given how different the formats are), a real dependency-graph
data shape (parent/child, not `DependencyParser`'s current flat per-manifest rows), and either
querying OSV.dev per resolved package+version or batching the full resolved set the same way
`cve_scan` already batches direct dependencies.

---

#### LOW — consider ecosyste.ms as a source for additional surveys

Raised by the project owner, 2026-09-18: [ecosyste.ms](https://ecosyste.ms) aggregates open-source
package/repository metadata across many language ecosystems (dependency data, funding/sustainability
signals, and more, per its own public description) and might be worth evaluating as a source for one
or more additional, optional surveys — not yet investigated against this codebase's actual needs or
API terms.

**Not yet done, and explicitly not assumed**: no code in this repo references ecosyste.ms today: this
is a fresh idea, not a half-built integration. Before scoping a real survey, a first pass should
check (1) what ecosyste.ms's actual API offers and whether it duplicates or complements OSV.dev/GitHub/
existing sub-surveyors, (2) its terms of use/rate limits for a tool that would query it per-repo across
a large corpus, and (3) whether it could feed the transitive-dependency-resolution item above (if it
already exposes resolved dependency graphs per package, that could be cheaper than building
per-ecosystem lockfile parsers in-house) — worth investigating together rather than as two
independent efforts.

---

#### MEDIUM — dependency analysis needs a required/optional/**selective** axis, not just manifest `dep_type`

Raised by the project owner, 2026-09-17, testing `/next`'s dependency view live.

`ingestion/dependency_parser.py` (`:111-222`) already tags each parsed dependency with a
`dep_type` — `runtime` / `dev` / `test` / `optional` / `indirect` — but that vocabulary is
entirely **manifest-declared**: Python's `[project.optional-dependencies]`, Maven's
`provided`/`optional` scope, Go's `// indirect`. It has no concept of a dependency that is
optional as a *capability* but becomes mandatory the moment a deployment turns that capability
on — the project owner's example: Egeria's core runtime dependencies are unconditional (Java, its
libraries, Kafka, Postgres), but DuckDB is not an "optional integration" in the same sense as
those manifest-level optionals — if a given deployment uses the DuckDB integration, DuckDB *is* a
required dependency **for that deployment**, and if it doesn't, DuckDB is irrelevant to it, not
merely "nice to have."

This is a real modeling gap, not a display bug: it needs a design decision on how a specific
deployment's selected integrations get recorded (survey time? enrichment time? a separate
deployment-profile concept?) before it's buildable — not yet scoped as a plan item. See also
`PLAN-FINISH-REPOS.md`'s item 3 (Curate) and the un-built `analysis`/`assessment`/`discovery`
stages in `/next`, any of which could end up being where deployment-level dependency
classification lives once designed.

---

#### Advanced SQLGlot view analytics
We can extend our SQL View static analyzer (`sql_analyzer.py`) with further advanced metadata analytics:
1. **Dialect Compatibility Matrix**: Check query compatibility across target warehouses (e.g. Snowflake, BigQuery, Athena, Redshift) by transpiling view SQL and report compatibility scores.
2. **Nesting Depth & Cycles**: Warn stewards about excessively nested views (e.g. view on top of view, on top of view) that degrade database query performance, and detect circular dependency loops.
3. **Query Optimization Advice**: Use `sqlglot.optimizer` to analyze query syntax in views and suggest simplified rewrites (e.g. redundant joins, dead subqueries, qualifying column expressions).
4. **Access & Join Heatmaps**: Parse views and query logs to discover which tables/columns are most frequently joined or filtered, recommending candidates for indexing or physical layout updates.

---

#### Analysis-step inventory and registration

Authoring a Survey Definition (item above, and the Dr.Egeria item below) requires knowing which analysis steps already exist to compose from — a real, unsolved gap with two halves: (1) finding Egeria's existing analysis steps (discoverable via the same technology-type/governance-definition search referenced in `docs/survey-model.md` D4/D6, not yet exercised for this purpose), and (2) publishing RE's own sub-surveyors as catalogable `GovernanceActionType` elements — nothing does this today, so an author can't reference `re_analysis_step: schema_inventory` until something has created that catalogable element in the first place. Likely shape: a one-time/per-addition publish step (an extension of `EgeriaPublisher`, or its own Dr.Egeria plan) plus a local inventory RE itself can consult.

The local-executor item above is now implemented and gives this a concrete, real dispatch point to extend or replace: each resource type's adapter module (`*/survey_definition_adapter.py`) has a `re_analysis_steps` dict — today a small hardcoded Python mapping, not yet a catalogable/extensible registry. This item is about making that mapping itself discoverable/extensible in Egeria terms, rather than requiring a code change to add a recognized step.

Full context: `docs/egeria-integration.md`, section 6.1 and open question A12.

---

#### RFA emission belongs in the orchestrator, not in each surveyor

Every surveyor that finds a gap builds its own `RequestForActionAnnotation` inline —
`security_hygiene.py:151-160` (missing SECURITY.md), `:196-205` (missing CI config), `:233-242`
(missing LICENSE), each a near-identical copy inside a hand-written if/else block
(`security_hygiene.py:131-250`). Two problems: adding a check means copy-pasting a fourth block, and
the RFA fires regardless of *why* anyone is looking.

An RFA asserts someone must act, which is only true when you own the resource. Evaluating a
candidate you haven't adopted should produce evidence, not work — otherwise the RFA drawer fills with
items about repos the user was merely browsing. Surveyors should emit findings; the orchestrator
should decide what becomes an RFA, gated on the investigation's purpose
(`docs/investigation-framing-design.md` §4). Worth doing even if framing never lands — the
copy-paste problem is real on its own.

---

#### DONE (finding 73) — the scorer can now express a partial component match

Reported, not counted: strict containment stays the headline, partial cover (`0 < coverage < 1`,
no invented threshold) prints beneath it, and overclaiming nodes are named separately.

**Still open, surfaced by the first run:** `trellis.md`'s `Web front-end` is unmatchable by
construction — its three missing files are `web/static/vendor/*.min.js`, which `exclusion.py`
removes as vendored, so no detector can ever claim them. Needs a note in `trellis-revised.md`
(rule 3 forbids editing the fixture). `Web backend` misses exactly one real file, `web/app.py` —
a chaseable detector gap, not a component-wide failure.

---

#### DONE (spike only) — Go support: the component-proposing stack is Python/Java/npm-only

**Resolved in the spike, finding 70: Prometheus 0/11 → 11/11, ARI 0.9936.** Four changes —
`rules-imports/import-go.yml`, Go resolution in `imports.py`, a `go_subsystems()` proposer in
`detectors.py`, and a name-collision fix in `score.py` that had been silently discarding 30 of 173
components. Regression-checked: `trellis` 8/11 and `egeria-workspaces` 18/27 both unchanged.

**Still open, and now the binding constraints:**

* ~~**Port it.**~~ **DONE (finding 71).** Applied as edits, not file copies, so the package's own
  divergence survived. The ported implementation was then scored for the first time — 173 components,
  **11/11**, ARI 0.9936, identical to the spike on every measure. Nine regression tests added; full
  suite 1678 passed. The `score.py` name-collision bug was scorer-only: `arch_recovery/` already keys
  by slug throughout.
* **Precision, not recall — now the only thing that matters.** Recall across three owner-published
  fixtures is 11/11 (Prometheus), 3/5 plus two at 99.8% (Milvus) and 6/6 (Kubernetes). Against that,
  the proposer emits **173, 608 and 3270 components** for **11, 5 and 6** declared ones — the
  coupling proposer contributing 146, 409 and 2482 untyped entries. Detection is solved; **nothing
  about "3270 components" is usable by a human.** Distillation (`architecture-recovery-extraction-design.md` §5.2, Phase 5) is the only remaining
  obstacle to an answer. Scale is *not* the problem: Kubernetes' 31300 files and 93046 imports run in
  ~16s end to end.
* **Go type inference.** `has_main` types `promql`, `util` and `documentation` as `Console Command`
  because some `main.go` sits beneath them.
* **Go cohesion needs recursive rollup subtrees.** Files in one Go package never import each other,
  so `coupling.py`'s `import_cohesion` is structurally ~0 at package granularity.

---

### Corpus, signals & testing

#### The test suite reaches the live GitHub API — a token raises the limit, it does not fix it

Found 2026-08-25 when PR #14's CI failed on odpi. The job has no `GITHUB_TOKEN`,
so an unauthenticated client hits GitHub's 60-requests/hour limit, `urllib3`
retries with backoff sleeps, and the 30-minute job dies having produced nothing.
It passes locally only because a developer has a token — the classic
works-on-my-machine shape, and it hid until the suite ran somewhere without one.

The reached path is `repo_survey_definition_adapter`'s zipball **resource
provider** (`client.get_repo(project.github_url)` via `resolve_resources`).
**Mocking a surveyor does not mock the resource it declares** — that is the
actual lesson, and it will recur for every `requires_resources` step.

`GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}` is now set in CI, which unblocks it
at 1000 req/hr. That is a mitigation: the suite can still reach the network, so
it is still slow, still non-deterministic, and still fails differently depending
on who runs it.

**The fix** is an autouse fixture that fails any test reaching `GitHubClient`
without an explicit opt-in marker — the same shape as `requires_pgvector`, but
inverted: network access becomes something a test must ask for rather than
something it gets by default. Worth doing before the next `requires_resources`
step lands, since each one adds another way in.

---

#### Testing strategy — four silent-failure classes, one built, three open

Eight faults found on 2026-08-20 shared one shape: **the code ran, reported success, and did
nothing.** None threw. Each was found by hand, late, after the capability had been "done" for
a while, and every one of them passed its own module's tests. What they had in common was a
gap *between* two components, invisible from inside either.

**BUILT — `tests/test_reachability_audit.py`.** Structural comparison of every registry
against the surface meant to expose it: steps vs. survey types, analysis kinds vs. catalog
entries vs. dispatch, generated documents vs. the batch manifest, intents vs. rule 17's
canonical eight. Verified against the real historical faults rather than assumed — replaying
`repo_website_ingestion`'s orphan state, the 4-of-7 batch manifest, and a typo'd intent each
fails it. Deliberately asserts only that things are wired together, never that they work;
behaviour is each capability's own job, and these bugs all passed those tests.

Note it excludes the `*` (Full Survey) sentinel on purpose. That bundle is generated *from*
STEP_REGISTRY, so it can never be missing anything, and counting it would have declared
`repo_website_ingestion` reachable on the day it was reachable from nothing.

---

#### Open — grow the repo corpus substantially, as a bug-finding strategy

37 repos registered, 9 with a derived homepage, 5 groups. That small corpus has already been
the single most productive source of real defects: of the handful of repos with homepages,
three exhibited distinct, unanticipated shapes — `sqlglot.com` is a 138-byte pdoc
meta-refresh stub (ingest reported success having embedded nothing), `kedro_plugins` declares
its own GitHub URL as its homepage (would have ingested forge chrome as documentation), and
`docs.unitycatalog.com` no longer resolves. Versioned-vs-unversioned sitemaps and
site-built-from-an-ingested-repo came from the same handful.

That is a very high defect rate per repo, and it argues the corpus is the limiting factor on
finding the next class of bug rather than the test suite is. RE already has the machinery to
act on this — org import and the Discovery search/list sources — so this is a matter of
deliberately importing breadth (several foundations, several languages, monorepos, archived
and fork-heavy orgs, repos with no docs at all) and then running the full survey set across
it looking for steps that report success having done nothing. Worth planning as its own
exercise, including what "success having done nothing" looks like per step, since that is the
shape none of these tests catch on their own.

---

#### `cnf_certification` is a tombstone repo — RE has no signal for "moved"

`cncf/cnf-certification` has a one-file inventory (`README.md`), and that is **correct**, not a
truncated download — confirmed against the GitHub API 2026-08-23: `size: 4`, 5 stars, last
pushed 2026-03-09, and a description reading "CNF Certification is now part of the Cloud
Native Telcom Initiative's test catalog focus area @ https://github.com/lfn-cnti/certification".
The project moved; the repo is a signpost.

Two separate things follow, worth not conflating:

- **Immediate:** it is a disposition decision for a human — `abandoned` with the successor URL
  as the reason, and probably register `lfn-cnti/certification` instead. No code needed.
- **Worth designing:** RE has no way to *notice* this. A repo whose entire content is a README
  pointing at another repository is a recognisable shape — near-empty inventory, recent-ish
  last-push, a URL to a different repo in the description or README body — and it currently
  surveys as a perfectly healthy, extremely small project. Every zero it produces is a true
  zero, so no outcome label is wrong; the labels just cannot say "this is not where the
  project lives any more". That is a genuinely new signal, not a bug in an existing one.

It also cost real time: this repo is where the 58-repo refresh stalled for ~15 minutes, since
`clone_timeout_seconds: 300` with 3 retries is a poor shape for a batch run — 15 minutes of
silence per bad repo. Worth revisiting alongside any bulk-refresh work.

---

#### No database or filesystem questions — the Questions checklist is repo-only, and fails silently

`question_catalog.yaml` has exactly one top-level key, `repo_questions` (41 entries). There is no
`database_questions` or `filesystem_questions`, and the source CSV `docs/dr-egeria/resource_questions.csv`
is entirely repo-shaped ("Is this repository actively maintained?", …). Meanwhile the analysis catalog
*does* cover both: `database_analyses` has 4 (`schema_inventory`, `row_count_snapshot`, `privilege_audit`,
`egeria_db_survey`) and `filesystem_analyses` has 1 (`filesystem_inventory`) — so there are analyses no
question can ever reach.

**It fails silently, which is the part worth fixing first.** `question_catalog_reader.py:83-85` builds its
result dict with `"repo"` hardcoded as the only key, so `get_questions("database")` returns `[]` — the same
value as "no questions matched your filter". A user on a database's Questions tab sees an empty checklist
and cannot tell whether nothing applies or nothing exists. Either return an explicit not-authored signal or
render one in the UI; an empty list should not be the representation of two different states.

Consequences beyond the empty tab:
- Purpose-driven dispatch (`docs/investigation-framing-design.md` §3) works for repos only. An investigation
  scoped to databases has no questions to select over, so nothing dispatches.
- The `stage` skew is repo-derived (Analysis 20 / Discovery 8 / Scouting 4 / Assessment 3 / Automate 1) and
  shouldn't be assumed to hold for other resource types.

Sequencing note: write these **after** the Purpose subset measurement (item 6 above), not before. If Purpose
turns out not to discriminate, the CSV schema changes — and authoring two new question sets against a schema
that is about to change is the expensive order to do this in.

**Deferred, project-owner decision 2026-08-31: "We are deferring work on DB/FS until all the
Repo work is complete."** Authoring `database_questions`/`filesystem_questions` is exactly the
DB/FS-specific work this applies to — do not start it until Repo work is signaled complete. The
silent-failure fix (an explicit not-authored signal instead of an empty list) is a small,
resource-type-agnostic correctness fix and isn't itself DB/FS feature work — it can proceed
independently if picked up. See also item 13 above (chat/`compile_context` scope), same
deferral.

---

#### A curated field allowlist silently drops anything added upstream — three instances in one day

Found 2026-08-25 while auditing ingestion (`docs/ingestion-pipeline-audit.md`): `_note`'s prop
filter dropped a newly-added `ingested_by` field with no error, no log line, and no visible
symptom beyond attribution coming back empty. The same session hit the identical shape twice
more the same day, in unrelated code: `arch_recovery/persist.py` silently dropping
`operationCount`, and a `detail` field before either of those. Each instance is individually
defensible — a hand-written allowlist is a normal way to control what a serialized shape
exposes — but three unrelated instances of "add a field upstream, watch it vanish downstream
with nothing to say why" in one day is the same shape this project's silent-failure testing
strategy (see "four silent-failure classes" above) was built to catch, just not this
particular one.

**Not yet scoped as a fix — this entry is the "notice it, track it" step**, filed rather than
guessed at further:
- Find every hand-maintained field allowlist/filter of this shape (props filters, persisted
  payload shapes, cross-schema readers like `advisor/re_code_symbol_reader.py`'s aliasing) and
  check whether each is still correct against its current source shape, not just its shape when
  written.
- Decide whether the general fix is procedural (a code-review checklist item: "does this
  allowlist need a matching entry for that new field?") or structural (a passthrough/explicit-
  exclude default instead of an explicit-include default, or a test asserting the allowlist's
  keys are a superset of what upstream actually produces) — that choice needs whoever owns
  each call site, not a blanket answer here.
- Out of scope for `docs/ingestion-pipeline-audit.md` itself, since it's a codebase-wide pattern
  rather than an RE-vs-EA ingestion-pipeline-duplication finding — that doc points here.

#### `security_features` results reader has a fourth state its own test doesn't know about

*(Found 2026-08-30, from a live-corpus test failure during routine integration —
`test_security_features_visibility.py::test_no_repo_in_the_corpus_renders_a_bare_empty_card`.)*

`_security_features_results` (`repo_survey_definition_adapter.py`) documents exactly three
states — `measured` (findings exist), `skipped_by_design` (stats exist, GitHub hid the data),
`never_run` (no stats at all) — and the test asserts every repo in the corpus lands in one of
them, never a bare `{"findings": []}` with no stated cause.

`egeria_workspaces_git` hits a fourth, undocumented case: it has **real, visible**
`security_and_analysis` data (`dependabot_security_updates: enabled`, several others disabled —
confirmed admin-visible, not GitHub's third-party redaction), yet **zero rows** in
`project_analysis_findings` for `security_features`. The reader's last branch ("visible and
genuinely nothing enabled — a real, final answer") is written for a repo where the data was
checked and truly nothing is on; it cannot distinguish that from what this repo actually is —
data that says something *is* enabled, but the `security_features` survey step itself has
apparently never run to turn that into a finding row. The reader currently can't tell "ran,
concluded nothing's on" from "never ran, but some other fetch happened to populate the stats
JSON anyway."

**Not yet fixed; not yet root-caused past this point.** Likely fix shape: the reader needs a way
to know whether the `security_features` step itself has ever executed for this repo (a
`last_run` marker, same shape `get_analysis_last_run` already tracks elsewhere) rather than
inferring "ran" from "stats happen to be visible" — but confirm that's actually the gap before
building it; the survey step's own write path hasn't been checked yet for whether it should have
produced a finding for `dependabot_security_updates: enabled` and silently didn't.

---

### Platform & orchestration

#### Gradle versions come from the BOM, so CVE scanning still cannot answer for Egeria

Follow-on from the entry below, which is now fixed: Gradle *is* parsed (2026-08-31), and
`egeria_git` went from 0 dependency rows to 85. But **84 of those 85 have no version**, because
Egeria resolves them through a BOM (`bom/build.gradle`) rather than inline. Measured, not assumed.

`cve_scan` handles that honestly — an empty version lands in `unqueryable` with "no pinned version
to query", never as clean — so the summary is now 8 of 8 inputs where the eighth says *cannot
answer*. That is a better state than "never ran", and it is not CVE coverage.

To get real advisories for a BOM-based project, something has to resolve `group:artifact` to a
version. Options, roughly by cost:

1. **Parse the BOM file itself.** Egeria's `bom/build.gradle` carries the versions, many as
   `${jacksonVersion}`-style variables defined in the same file or in `gradle.properties`. A
   two-pass read — collect variable definitions, then substitute — would resolve most of them
   without running anything. Cheapest, and covers the common single-BOM case.
2. **Version catalogs** (`gradle/libs.versions.toml`) are a data format and would parse directly.
   Egeria does not use one, but many modern Gradle projects do.
3. **Run `gradle dependencies`.** Complete and correct, needs a JVM and a working build per repo,
   and is far outside what a survey step should do.

Option 1 is the one that matches this codebase's existing shape — the same "cheap structural signal,
not full understanding" the manifest and convention parsers already are.

**Whatever is built, the reporting rule from the Gradle entry still applies:** a version resolved by
substitution should be distinguishable from one declared inline, because a wrong substitution
produces a *confident* CVE answer about the wrong version — which is worse than the current
"cannot answer".

#### No Gradle support in the dependency parser — Egeria itself has zero dependency data, so CVE scanning cannot run on it

Found 2026-08-31 while trying to take `egeria_git`'s security summary from 7 of 8 inputs to 8.

`ingestion/dependency_parser.py` globs for six manifest kinds: `pyproject.toml`,
`requirements*.txt`, `setup.py`, `package.json`, `go.mod`, `pom.xml`. **Gradle is not among them** —
no `build.gradle`, no `build.gradle.kts`.

Measured on the live registry:

    egeria_git manifests in project_file_inventory:  build.gradle x239, and nothing else
    egeria_git rows in project_dependencies:         0

So `manifest_parse` runs, completes, publishes 4 annotations and 0 errors, and produces no
dependency rows — because there is nothing it can read. Everything downstream of dependency data
is then unreachable for the project this tool is built around:

- `dependency_analysis` has nothing to report.
- **`cve_scan` can never run**, because it scans dependencies already parsed.
- `security_summary` is permanently capped at 7 of 8 inputs for any Gradle repo.

**The vocabulary behaved correctly and that is the point.** `cve_scan` did not claim "no CVEs" — it
declined to report, exactly as its own comment intends ("No dependencies RECORDED is not no
dependencies"). The step is right. The gap is coverage, and the risk is one layer up: for a Java
project the answer is *always* "not assessed", and any surface that renders that beside a genuine
"assessed, nothing found" turns a whole ecosystem's blind spot into an apparent clean bill of
health. `security_summary` names `cve_scan` in its missing list specifically so this cannot happen
silently there.

Scope worth checking before estimating: Gradle is Java/Kotlin/Android's dominant build tool, so this
is not one project's quirk. `repo_conventions_parser` already recognises `build.gradle` and
`build.gradle.kts` for its `automated_build` check, so the *filenames* are known to the codebase —
only dependency extraction is missing.

Harder than the other five parsers, and the estimate should say so: `build.gradle` is Groovy (or
Kotlin) **code**, not a data format, so `dependencies { implementation 'g:a:v' }` can be built from
variables, version catalogs (`libs.versions.toml`), `ext` properties or plugins. Options range from
a regex over literal coordinate strings (cheap, partial, and honest if it reports what it skipped)
to invoking `gradle dependencies` (complete, needs a JVM and a working build). A partial parser is
defensible **only** if what it could not resolve is reported rather than dropped — a dependency list
silently missing its version-catalog entries is worse than none, because it looks complete.

#### `--reload` and the source cache cannot both be right — a survey of any large repo cannot finish

Found 2026-08-31 while trying to get `cve_scan` onto `egeria_git`.

`SourceCache.DEFAULT_CACHE_DIR` is `Path("data/source-cache")` — **relative**, so it resolves
against the process cwd. The server runs from the repo root, so every acquired zipball and
treeless clone extracts *inside the directory uvicorn watches*. Extracting Egeria's zipball
produced, in the server's own log:

    INFO watchfiles.main: 485 changes detected
    INFO watchfiles.main:  92 changes detected
    INFO watchfiles.main:  17 changes detected

Each one restarts the server and kills the in-flight survey. `manifest_parse` on `egeria_git`
ran for **ten minutes and wrote zero dependency rows** — no error, no timeout, just repeatedly
killed and restarted. It completed normally on a server started without `--reload`.

**Neither side is careless, which is why this survived.** The cache location is a considered
choice, and its comment says so: under `data/` beside the registry databases, so a checkout
stays self-contained and `rm -rf data/source-cache` is a complete, safe reset. Auto-reload on
save is equally reasonable for development. The two are individually right and jointly fatal.

**It is silent from every angle a person would look.**

- The run endpoint returns `{"status":"started"}` and the activity log gets its entry, so the
  caller sees a successful launch.
- `data/` is gitignored, so 44 MB of extracted source never appears in `git status`.
- `watchfiles` does **not** read `.gitignore` for its exclusions, so being ignored buys nothing.
- The only visible trace is `watchfiles.main` at INFO — which was **discarded entirely** before
  logging was wired earlier the same day. This was findable for the first time this morning.

**Directly upstream of the entry below.** That one fixed the *symptom* of a restart mid-survey
(an `activity_log` row stuck at `running` forever) and treats restarts as an occasional event —
"restarting the web server to pick up a git pull". This makes them routine and self-inflicted:
every large survey triggers its own.

Options, none chosen:

1. **Absolute cache dir outside the tree** (`~/.cache/resource-explorer/source-cache`, or
   `$RE_SOURCE_CACHE_DIR`). Fixes it everywhere, and costs the self-contained-checkout property
   the current comment is defending.
2. **`--reload-exclude data/*`** on the `uvicorn.run()` call in `cli/main.py`. Keeps both
   properties; only helps the one entry point, and anyone running uvicorn directly still hits it.
3. **Do not pass `--reload` while surveying.** Free, and relies on remembering — the kind of rule
   that gets routed around because nothing enforces it.

Whichever is chosen, the silence deserves its own fix: a survey killed mid-flight should be
distinguishable from one that ran and found nothing, which is this codebase's most-repeated bug
class and is exactly what the entry below already built the machinery for.

#### FIXED — a server restart mid-survey left activity_log rows stuck at 'running' forever

Confirmed live 2026-08-26: restarting the web server to pick up a git pull killed two
in-flight Survey Definition runs (`RepoFullSurvey` and `RepoArchitectureDiscovery` on
`deep_causality`) mid-flight. Each survey's individual steps had genuinely completed and
published to Egeria — visible as their own `ok` activity_log rows — but the *outer wrapper*
row each survey run creates up front (`status='running'`, written by `survey_definitions.py`'s
`run_survey_definition_route`, `projects.py`'s scouting-scan route, and the equivalent
database/filesystem routes) is only ever marked terminal by one line at the very end of the
background `daemon=True` thread doing the work. Kill the process, and that line never runs —
nothing on startup reconciled it, so the row read "running" indefinitely, with no way to tell
"still going" from "died and nobody noticed."

**Fixed independently, twice, same day — the ownership-based version won.** This session's
first pass added `ProjectRegistry.reconcile_orphaned_running_activity()`: a blanket "every
`running` row is orphaned on any startup" reconciler. **That was wrong** — multiple RE
processes routinely share one database during development (confirmed live: this session's
server and the user's, running concurrently against the same rows), so a blanket sweep on
*any* process's startup would falsely resolve a peer's genuinely-still-running survey out from
under it, mid-write.

**What actually shipped** (`4e91ba2`, "Runs that stopped stop claiming to be running"):
`resource_explorer/run_reconciler.py`, wired into `web/app.py`'s `_lifespan()`. Reconciles by
**ownership**, not blanket sweep or age — a running row records the pid and process start time
of whoever owns it, and is resolved only when that exact process is provably gone. Age (six
hours) is the fallback only for rows with no owner recorded, an order of magnitude past the
longest measured real survey (~16 minutes). Marks resolved rows `interrupted`, not `error` —
"we know it stopped, not that it failed." Fails safe throughout: an owner that can't be
verified is left alone (`left_alone` in the return value), because "I can't tell if this is
alive" must never resolve to "it's dead" — a real timezone bug in an early version did exactly
that (silently routed every row to `left_alone` and resolved nothing, for three days, while
reporting success). The blanket version and its tests were removed during the merge that
brought this branch and `main` together (2026-08-26) rather than kept alongside the real fix.

---

#### Distributed survey orchestration via a flow tool (Prefect) — verified live and default-on (2026-08-26)

**This heading is now stale and needs a review, not just a re-read.** Prefect was default-on for
under two weeks: flipped on 2026-08-26 (this entry), then flipped back to **off by default**
2026-09-04 after that default caused 13 orphaned `prefect.server.api.server:create_app`
subprocess servers to leak on this machine (see `PrefectConfig.enabled`'s docstring in
`config.py`, and `CLAUDE.md`'s Prefect setup section, which is the only place that revert is
currently documented — not here). The project owner raised this 2026-09-18 after reading external
project-description copy that described local survey execution as "orchestrated as microflows via
Prefect," and didn't know it was optional/off — a sign the copy (and this Backlog section's own
headline) is describing the aspirational integration rather than today's default, which is a plain
`threading.Thread` with Prefect as an opt-in enhancement (`surveyors/prefect_adapter.py`'s
`run_prefect_step` falls back to local in-process execution whenever no Prefect server answers).

**Needs:** a review of whether default-on should be revisited now that the orphaned-subprocess
leak is understood (was the leak itself ever root-caused and fixed, or only avoided by defaulting
off?), and if not, whether `docs/Architecture.md`/external-facing descriptions should be corrected
to say "optional, off by default" rather than implying it's the normal execution path. This is a
decision item, not a bug fix — record the review's outcome here once done, with a
`**Decision (project owner, <date>):**` callout per this repo's convention.

**Measured 2026-09-18 — Prefect per-step dispatch overhead (`PLAN-PREFECT-OR-ALTERNATIVE.md` §5
phase 4).** This section's headline (and the plan doc's own §6 risk item) called this "unmeasured."
It no longer is.

*Methodology:* `repo_arch_summary` against `egeria_python_git`, chosen instead of phase 2's
`repo_arch_coupling` specifically to isolate the fixed dispatch cost from compute-time noise —
`repo_arch_coupling`'s own `StepInfo` declares `compute_cost="high"` with p90 132s from 32 measured
runs, which would swamp a ~1-10s overhead signal. `repo_arch_summary` has
`requires_resources={}`/`fetch_cost="none"`/`compute_cost="low"` (`repo_survey_definition_adapter.py`):
zero network, zero filesystem, reads findings `repo_arch_coupling` already persisted for
`egeria_python_git` earlier in this session — a genuinely warm, deterministic, fast step. 8 trials
per path, alternated (in-process, Prefect, in-process, Prefect, ...) so drift brackets both paths
rather than separating them into two blocks, one untimed warm-up call per path first. In-process:
`run_surveyor_step_task.fn(...)` directly, the same call `run_prefect_step`'s own fallback branch
uses. Prefect: `run_prefect_step(...)` with `config.prefect.enabled=True` and `PREFECT_API_URL`
exported as a real process env var — RE's own `.env` is read by pydantic-settings directly and does
**not** populate `os.environ`, so Prefect's own client (which reads `os.environ`/its profile, not
RE's config object) sees nothing unless the var is exported to the process; the first attempt
without doing that silently exercised only the fallback branch on every trial and was discarded.
Confirmed via the Prefect API (`flow_runs/filter`) that all 8 trials produced real, distinct,
`COMPLETED` flow runs — this is not a stealth-fallback result.

*Numbers (wall-clock, seconds, one process, `.venv` Python 3.13, Prefect 3.8.1 client /
`prefecthq/prefect:3-python3.12` server, host worker on `resource-explorer-pool`):*

| | median | p90 | all 8 trials |
|---|---|---|---|
| in-process (`run_surveyor_step_task.fn`) | 1.18s | 2.40s | 0.67, 0.51, 2.38, 1.13, 0.73, 1.52, 1.24, 2.45 |
| via Prefect (`run_prefect_step`) | 9.23s | 12.26s | 7.23, 8.17, 10.23, 12.26, 12.28, 8.23, 8.23, 10.29 |
| **delta (added dispatch overhead)** | **~8.05s** | **~9.86s** | — |

Cross-checked against Prefect's own `total_run_time` for these same 8 flow runs (the API's own
measure of time actually spent *running*, excluding queued/scheduled time): 1.2–3.5s, matching the
in-process figures closely. That confirms the ~8-10s delta is essentially all **dispatch + worker
pickup + poll latency**, not slower compute inside the Prefect task — `_run_prefect_step_api`
polls `state.result()` on a bare 1.0s `asyncio.sleep` loop, and the dominant cost sits in the gap
between `create_flow_run_from_deployment` and the process-type worker's own polling cycle actually
claiming the run, which this pass did not instrument further (worker query interval was left at
its default).

*Confidence:* in-process variance (0.5–2.5s) is real but small relative to the ~8s signal; the
Prefect-path variance (7.2–12.3s) is larger in absolute terms but still clearly separated from the
in-process cluster in all 8 trials — no overlap. Environmental noise does not weaken confidence in
"the overhead is on the order of 8-10 seconds, not milliseconds and not one second."

**Recommendation, following from this number:** `PREFECT_ROUTED_STEPS` widening to
`repo_secret_scan`/`repo_rag_ingestion` (both `compute_cost="high"`, realistically tens of seconds
to minutes of real work) is worth doing — an 8-10s fixed tax is a small fraction of their own
runtime, and buys real retries/cancellation/per-task logs neither has today. `PREFECT_ROUTE_LOCAL_STEPS`
(routing every plain local step through Prefect, not just `executes_at: prefect` ones) is a bad
idea at today's worker/polling configuration: RE's Discovery/Analysis tier is full of sub-second-
to-few-second steps (`repo_arch_summary` itself included), and an 8-10x-to-many-times multiplier on
each one would make routine surveys dramatically slower for no compute benefit. `route_local_steps`
should stay off by default; if it's ever wanted, the worker's polling interval should be tuned down
first and re-measured, since this number is a property of that interval as configured today, not a
Prefect ceiling.

**DONE 2026-09-19 — phase 5 widening shipped.** `PREFECT_ROUTED_STEPS`
(`scripts/generate_repo_survey_definition.py`) now also includes `repo_secret_scan` and
`repo_rag_ingestion` alongside `repo_arch_coupling` — both confirmed `compute_cost="high"` in
`repo_survey_definition_adapter.py` before widening. Docs regenerated
(`repo-survey-definition-full.md`, `-analysis.md`, `-compliance.md`, `-refresh.md` — the four
generated docs that reference either step — all show `executes_at: prefect` for both). Published
live to dev Egeria (`qs-view-server`) after coordinating with all live peers per
`coordinate-shared-writes`: two "Create Governance Action Process Step" blocks extracted directly
from the regenerated `repo-survey-definition-full.md` (split on `___`, matched by Qualified Name),
run once via `dr_egeria_run_block` against `https://host.docker.internal:9443` (confirmed the MCP
egeria server's own network context — `localhost:9443` does not resolve from there, verified by a
failed `validate` call against it). Both landed as **Update** Governance Action Process Step
(upsert, not create — the elements already existed): `repo_secret_scan` GUID
`c902a5dc-6ad5-4e22-9311-a003b5e69419`, `repo_rag_ingestion` GUID
`818bae1c-b808-423b-9681-2461f169906a`, both with `executes_at: prefect` in the returned Additional
Properties. Verified independently afterward via `SurveyDefinitionReader`'s
`GovernanceOfficer.get_governance_definitions_by_name()` (a separate read path from the publisher's
own tool, against `localhost:9443` from the host) — same two GUIDs, same `executes_at: prefect`.
`scripts/reconcile_survey_definition_links.py --dry-run` reported clean before and after (all ten
Survey Definitions, including `RepoFullSurvey`'s 42 edges) — the property-only update touched no
step links, so no duplication risk to begin with, confirmed rather than assumed. No link commands
were run. Test coverage (`tests/test_generate_repo_survey_definition.py`) updated to assert the
widened three-step set.

#### DONE 2026-08-27 — Retire the ISSUE-50 workaround in `egeria_delegated_step.py`

`EgeriaDelegatedStepSurveyor` routes through `initiate_gov_action_type()` because
`initiate_engine_action()` used to 404: it posted to a URL missing the governance engine's name
and had no parameter to supply one (logged as ISSUE-50, 2026-08-17). The workaround needs a
**pre-authored `GovernanceActionType` per delegated step**, which is real authoring overhead for
every step RE wants to delegate.

**Measured 2026-08-26: fixed in the installed pyegeria 6.0.18.4.** The method now takes
`governance_engine_name` and builds
`.../governance-engines/{name}/engine-actions/initiate` — the shape the Java route
(`AutomatedCurationResource.java`) actually expects. `initiate_and_wait()` already exists in
that module, kept for exactly this moment.

Work: switch the primary trigger path to `initiate_and_wait()`, live-verify against a real
delegated step, and drop the per-step `GovernanceActionType` requirement (and its probe doc) if
verification holds. The module's comment saying the direct path is "kept for when ISSUE-50 is
fixed" is stale and should go with it.

**Done 2026-08-27.** `initiate_and_wait()` now takes `governance_engine_name`
and passes it through; the surveyor requires it alongside `request_type` and says so, rather than
letting the omission surface as a bare 404. Live-verified: a real engine action on the
Stewardship engine (`write-to-audit-log`) reached `COMPLETED` with a real completion message
through the direct path, needing no pre-authored `GovernanceActionType`.

`initiate_action_type_and_wait()` is kept — it is not a workaround any more, just the other valid
path, and the better one when a `GovernanceActionType` already exists since the engine is then
resolved server-side from its executor link.


#### Distributed survey orchestration via a flow tool (Prefect) — early prototype, not yet integrated

RE's only execution model today is either synchronous in-process (`SurveyOrchestrator`) or the `scheduler.py` daemon-thread poller (see the "Periodic / triggered survey scheduling" item below). Neither can run survey work *near* a protected asset (a database inside a VPC, a filesystem edge agent) without deploying RE itself there, and neither gives retries/backoff/task-level telemetry for free.

**Design notes (2026-07-14):** `docs/survey-execution.md (§9)` proposes Prefect (over Dagster/Airflow — see its §3 comparison table) as a task runner slotted in via the existing `executes_at` routing convention already used by Survey Definitions (`executes_at: prefect`, alongside today's `egeria`/`resource-explorer`), so this is additive to the local-executor work above, not a replacement. `docs/survey-execution.md (§9)` grounds this against how DataHub/OpenMetadata handle distributed estate-wide ingestion, and proposes a broader progressive intake funnel (Scouting → Staging Registry → Enrichment Gate/ToDo → Deep Assessment → Egeria Certified Catalog) that reframes "coherent selective-cataloging model" (item below) in terms of Prefect-driven phases.

**Shipped so far (uncommitted, prototype-stage):** `resource_explorer/prefect/flows.py` (`@flow`/`@task` wrappers) and `resource_explorer/surveyors/prefect_adapter.py` (dispatches a step to the Prefect REST API or runs it locally via `nest_asyncio`), plus `tests/test_prefect_integration.py`. `prefect` added to `pyproject.toml` dependencies. **CRITICAL FINDING 2026-08-20 — the Prefect API path had never executed.**
`run_prefect_step` opened with `asyncio.get_running_loop()`, while a redundant
`import asyncio` further down the same function made `asyncio` a closure cell (the lambda
captures it). That first line therefore did a LOAD_DEREF on a cell nothing had stored and
raised `UnboundLocalError`; the broad `except` read it as "API unreachable", logged
"Prefect API dispatch failed", and ran the flow in-process. So every Prefect step has always
run locally, `_run_prefect_step_api` was dead code, and the log line was indistinguishable
from a genuinely unreachable server. Fixed (inner import removed), and the fallback now logs
the exception type and traceback so a bug here can no longer masquerade as a connection
problem. **This matters for the design decision below: whatever the flow-engine dependency
has been bought so far, distributed execution is not it — nothing has ever been dispatched.**

**Routing fixed at the same time.** `prefect.enabled` re-routed every step declaring
`executes_at: resource-explorer`, overriding what a definition explicitly asked for —
`executes_at` is documented as naming the execution engine and as open-ended precisely so
engines can be chosen per step, so a global override removed the only way to say "run this
one here" and made `executes_at: prefect` redundant. Now gated on a separate, off-by-default
`PREFECT_ROUTE_LOCAL_STEPS`: routing RE's own steps through Prefect for retries/telemetry is
a legitimate deployment choice, it just has to be asked for by name.

**Not yet done:** ~~`executes_at: prefect` is not wired into `survey_definition_executor.py`'s dispatch loop~~ **(CORRECTED 2026-08-19, verified against this tree: it IS wired.** ~~Note `:167` — when `config.prefect.enabled` is true, *every* step marked `executes_at: resource-explorer` is rerouted to Prefect, so a global flag overrides what a definition explicitly asked for. Open question whether that is intended.~~ **Answered — see "Routing fixed at the same time" immediately above: gated on the separate `PREFECT_ROUTE_LOCAL_STEPS`, off by default, so `executes_at` is honoured unless rerouting is asked for by name.)**; no staged-candidate registry states in `registry.py`; no deployment/worker actually configured or run against **as of that pass — since re-verified live and largely closed, see `docs/design-notes/PLAN-PREFECT-OR-ALTERNATIVE.md` §1.3, 2026-09-18**. This needs review as a real design decision (own dependency on a flow engine is a significant infra commitment) before the prototype code is treated as a real feature — not yet reflected as its own line item, currently living only in these two design docs.

Related/overlapping: "Periodic / triggered survey scheduling" below (this may be the eventual replacement for the daemon thread it says is only a short-term fix), "Coherent selective-cataloging model" below (the staging-registry funnel is a concrete proposal for it), and "Unify survey launching" above once a launcher needs to route to a third execution engine, not just two.

**VERIFIED LIVE 2026-08-26 — the "needs review as a real design decision" above is answered:
real dispatch works end-to-end**, tested against a local `prefect server` + a deployed
`re_survey_flow` + a running worker, not just code review. Three more bugs found and fixed in
the process, none caught by the earlier code-only review:

- `prefect deploy` (the CLI command) itself failed — `.deploy(work_pool_name=...)` alone
  requires an image or remote storage location; fixed to `.from_source(source=<this
  checkout>, entrypoint=...)`, correct for a `process`-type work pool running on the same
  machine as the caller.
- `_run_prefect_step_api` fetched the completed flow run's result via
  `client.resolve_value(state.data)`, which doesn't exist on `PrefectClient` in Prefect 3.x —
  every completed API-dispatched run raised `AttributeError`, was caught by the same broad
  `except` the 2026-08-20 finding above already flagged, and silently ran the step's work a
  **second time** via local fallback. Fixed to `state.result(raise_on_failure=True)`, and
  `re_survey_flow` now declares `persist_result=True` (Prefect 3.x doesn't persist results by
  default, and a state fetched back via `read_flow_run` from a different process than the one
  that ran the flow has nothing to fall back to without it).
- **Cancelling a flow run silently didn't stop the work.** Found testing the new cancel
  endpoint (see below): `state.is_cancelled()` correctly raised, but that exception was caught
  by the *same* broad `except` as "server unreachable," so a cancelled run fell back to running
  the step again locally — cancel had no actual effect. Fixed with a dedicated
  `PrefectFlowRunCancelled` exception that `run_prefect_step` re-raises instead of falling back
  from; everything else (a real connection failure, a bug) still falls back as before.

**Default flipped:** `PrefectConfig.enabled` now defaults `True` (was `False`) — safe with no
server running, confirmed by the fallback behavior above; adds per-step connection-attempt
overhead for `executes_at: prefect` steps only, until a server exists. `route_local_steps`
stays `False` by default — routing every plain step through Prefect multiplies overhead by
however many steps a survey has, unproven at volume; left for a later pass once the admin
panel below makes it observable. `.env.example` and CLAUDE.md's External Services list now
document the required local setup (`prefect server start`, `resource-explorer prefect deploy`,
`resource-explorer prefect worker`) — previously undocumented anywhere.

**One step opted in so far:** `repo_arch_coupling` (the real `git log` history clone,
long-running/thrash-prone in a way the cheaper steps aren't) now declares
`executes_at: prefect` in `scripts/generate_repo_survey_definition.py`'s `PREFECT_ROUTED_STEPS`
— a deliberate, narrow opt-in, not a blanket switch. **This is a mechanism, not yet a live
change** — the corresponding Egeria-side Survey Definition step is published via a one-time
Dr.Egeria markdown run (`dr_egeria_survey_publisher.py`), which needs a live Egeria instance
and human-in-the-loop execution; not done as part of this pass.

**New: an Admin "⚡ Prefect" panel** (`web/routes/prefect_status.py`,
`loadAdminPrefectPanel()`/`renderAdminPrefectPanel()` in `index.html`) — flow-run status
grouped by resource (slug/step tags added to `create_flow_run_from_deployment`), a real Cancel
button (`POST /api/prefect/flow-runs/{id}/cancel`, verified live to actually stop a running
step, not just mark it), and a link out to Prefect's own UI for full per-task logs. Degrades
gracefully (a status flag, not a 500) when no server is reachable.

**Scope boundary, worth restating here too:** all of the above is about **locally-dispatched**
survey steps. `executes_at: egeria` steps are coordinated by Egeria itself — none of this gives
RE any more visibility into those. See "Some surveys are coordinated by Egeria, not RE" below.

**Bare-host only for now, deliberately (2026-08-26):** `scripts/prefect_up.sh`/`prefect_down.sh`
(`make prefect-up`/`prefect-down`) bring the server/work pool/deployment/worker up idempotently
on this machine, but nothing containerizes them — no launchd/systemd unit either. Trellis isn't
ready for containerization yet (explicit user call), so this stays a bare-host convenience
script rather than becoming a container prematurely.

**A Prefect container already exists — in `egeria-workspaces-fs`, not Trellis — and isn't
started.** `egeria-workspaces-fs/compose-configs/optional-associated-runtimes/prefect/
docker-compose.yaml` defines `prefect-server` (`prefecthq/prefect:3-python3.12`, matches the
3.x this integration was verified against) and `prefect-worker`, both on `egeria_network`.
Reviewed, not modified — three concrete gaps to close whenever Trellis containerization
actually happens, so they aren't rediscovered from scratch:

1. **Work pool name mismatch.** The container's worker runs `prefect worker start --pool
   egeria-pool`; RE's own default (`PrefectConfig.work_pool` in `config.py`) is
   `default-agent-pool`. As configured today, RE would deploy to a pool this worker never
   polls — every step would sit `SCHEDULED` forever, silently, no error. Either the container's
   pool name or RE's default needs to change to match (or `PREFECT_WORK_POOL` set explicitly in
   whichever environment talks to this container).
2. **The worker container has no access to RE's code.** It builds from
   `../../../runtime-volumes/prefect/user_code` — a generic flow-code mount, not
   `resource_explorer`. `re_survey_flow` imports `resource_explorer.surveyors.
   survey_definition_executor` and `resource_explorer.registry` directly; nothing in the
   compose file installs the `resource-explorer` package or mounts the RE checkout into that
   container, so the worker would fail on import the moment it tried to run RE's flow for
   real. Needs either the package installed into a custom image (a new Dockerfile, not the
   generic `user_code` one) or an equivalent volume mount plus dependency install.
3. **Separate `prefect` Postgres database, unconfirmed whether it's provisioned.** The server
   points at `postgresql+asyncpg://prefect_user:user4prefect@egeria-shared-postgres:5442/
   prefect` — a distinct database/role from the `egeria_advisor` database RE and EA already
   share. Not verified from a Trellis checkout whether that role/database already exists on
   the shared Postgres instance or still needs creating, the way RE's own `resource_explorer`
   schema did (see root README's "Databases" section).

None of these are hard blockers, but (2) in particular is real integration work, not a config
tweak — worth sizing before assuming this container is a quick swap-in for `prefect_up.sh`.

---

#### Some surveys are coordinated by Egeria, not RE — a separate visibility question, deliberately deferred

Raised 2026-08-26 alongside the Prefect work above, and explicitly scoped out of it by the
user: `executes_at: egeria` survey steps are coordinated by Egeria itself, not by RE's local
executor or Prefect. RE has no more visibility into those than it had before this pass (no
flow-run, no local thread, no activity_log row updated mid-run) — the "are my surveys making
progress / how do I cancel one / where's the log" questions this whole thread started from have
a real, currently-unanswered version for this category specifically. Needs its own design pass:
what does Egeria itself expose for a running `GovernanceActionProcess` (status, cancellation,
logs), and how would RE surface that the same way `web/routes/prefect_status.py` now does for
the local/Prefect case. Not investigated yet — flagged so it isn't assumed solved by the
Prefect work above.

---

#### Periodic / triggered survey scheduling

Egeria has no native cron/interval scheduling for survey action services (the only interval-based mechanism found anywhere in the framework, `IntegrationConnectorProvider.refreshTimeInterval`, belongs to a different framework — integration connectors, not surveys). RE already has rudimentary scheduling of its own (`resource_explorer/scheduler.py` — a daemon thread polling the `resource_schedules` table every 15 minutes, per D9 in `docs/survey-model.md`), so this is easily fixed short-term if a gap shows up. The longer-term expectation, though, is that recurring scheduling lands in Egeria core, or is reached via a connector to a dedicated scheduling service, rather than RE's daemon-thread approach becoming permanent infrastructure. Revisit once the selective-cataloging flow above has a shape, since "survey on a schedule" and "survey a previously-selected subset again" are closely related.

---

#### HIGH — Unify survey launching (retire old Re-survey buttons, no unified dashboard yet)

Two uncoordinated ways to start a survey on the same resource exist side by side today:
1. The new Survey Definitions panel (`docs/Backlog.md`'s "RE locally executing Survey Definitions" item below) — Egeria-authored, browses real candidates with step detail.
2. Old per-resource-type buttons still in `resource_explorer/web/static/index.html` (e.g. the database detail panel's "📊 Re-survey" → `showSurveyDbModal()` and "☁ Re-survey in Egeria" → `showPublishDbModal()`, ~~around index.html:3641-3675~~ **now `index.html:7351` / `:7377` / `:7389`, verified against this tree**) that predate the Survey Definitions work and don't go through it at all.

**CORRECTED 2026-08-19 — this is not just a UI unification.** The two paths diverge on five axes: step selection (editing a Survey Definition in Egeria has *zero* effect on the legacy path), Egeria target (the legacy modals collect per-call URL/server/user overrides, so the two paths can write to *different Egeria servers in one session*), publish shape (narrow `publish_step_annotations` vs. full `EgeriaPublisher.publish` with cataloging side effects), result storage (only the legacy path writes history rows and drives the charts), and scheduling (`scheduler.py` calls the orchestrators directly — Survey Definitions are unreachable from a schedule). Retiring the legacy path means porting history storage and scheduling first. Detail, and a third option (legacy becomes a thin caller of the new path), in `docs/survey-model.md` §1.2 and §5.1 — **but re-verify the specifics against this tree; `run_batch` postdates that analysis and likely bears on it.**

Neither the old buttons nor the Survey Definitions panel is quite right as the long-term answer — Survey Definitions is Egeria-authored/candidate-driven, which is correct for "what can run here," but launching a survey is a cross-cutting action needed from multiple places (resource detail panel, discovery results, scheduled/recurring runs), not just one tab. Likely direction: a generic survey-launcher component/modal that any view can invoke (given entity_type + slug), backed by the Survey Definitions candidates API, replacing the old per-type modals rather than living alongside them.

Related, not yet built: polling Egeria for survey results so completed native (`executes_at: egeria`) runs surface somewhere unified instead of only showing an engine-action GUID with "check Egeria's Asset Catalog" (see `resource_explorer/web/routes/survey_definitions.py` run endpoint and its frontend handling in index.html around line 2881). Today there is no unified "survey results dashboard" — results are scattered across the Survey Definitions run modal, the database/filesystem detail panels' own survey history, and Egeria's own catalog for anything async. A poller (or the A2A rendezvous from the item below) is the likely fix, feeding one dashboard view regardless of which launcher/engine started the run.

Full context for the Survey Definitions side: `docs/egeria-integration.md` section 6; the A2A item below covers the async-notification half of "unified dashboard."

**RE-VERIFIED 2026-08-30 against this tree — the picture above is stale, and better than it says.**
Significant unification already shipped without this entry being updated:
- **Repo's legacy run path is gone.** `runSurveyFromSidebar`, `runScoutingScan`,
  `publishScoutingRegistration`, `runProfileScan`, `publishProfileFindings` are all deleted
  (confirmed by grep — zero hits). The Survey Definitions candidate panel is the only way to
  launch a repo survey now (`docs/survey-model.md (§6)` D1–D5, landed since the
  2026-08-19 doc was written, despite that doc calling itself "not yet committed").
- **Publish is unified for repo** — one route (`POST /api/egeria/{slug}/publish`), used by both
  the Survey Definitions panel's generic `☁ Publish` (D4) and the repo detail page's own
  "Publish survey →" button.
- **The scheduler already dispatches Survey-Definition-typed schedules** for both repo and
  database (`_run_scheduled_survey()` → `run_survey_definition()`).

**What's still genuinely open:** filesystem has none of this — `showSurveyFsModal`/
`submitSurveyFs` is the only way to survey one, and `scheduler.py`'s `_execute()` has no
filesystem branch at all (repo/database only), so a filesystem schedule can never fire even if
one were somehow created. Database's legacy `showSurveyDbModal`/`showPublishDbModal` also still
exist alongside the Survey Definitions panel — not yet confirmed whether they're now a safe
duplicate (like repo's were) or still do something the panel can't.

**Direction from the project owner (2026-08-30): database and filesystem should route through Egeria's own
EXISTING native surveys, not through newly-authored RE-side Dr.Egeria Survey Definitions.**
This changes what "closing this item" means for those two resource types — it is not "author a
`database-survey-definition-*.md` / `filesystem-survey-definition-*.md` the way repo's eight
were authored." Both adapters already carry the mechanism this points at:
`other_engine_handlers={"egeria": _trigger_egeria_native_survey}` in both
`database/survey_definition_adapter.py` and `filesystem/survey_definition_adapter.py` — a step
tagged `executes_at="egeria"` actively triggers Egeria's own native survey rather than being
skipped. The gap is not "build the trigger," it's "prove the trigger, end to end, and get its
results back."

**Testing gap, explicitly called out as open work (2026-08-30, the project owner) — keep on the backlog:**
`filesystem/survey_definition_adapter.py`'s own module docstring already says the native-trigger
path is "not yet exercised end-to-end, since this environment has no cataloged filesystem to
test against." Database's equivalent (`_trigger_egeria_native_survey` in
`database/survey_definition_adapter.py`) has more surrounding coverage but its own live,
end-to-end exercise (cataloged resource → triggered native survey → result actually lands
somewhere RE reads it back from) has not been separately confirmed either. Both need a real
pass: a cataloged filesystem and database resource, a live Egeria trigger, and confirmation of
where the native survey's results actually surface (ties into the "results dashboard" gap two
paragraphs up — an `executes_at: egeria` step today only returns an engine-action GUID with
"check Egeria's Asset Catalog," which is not itself a tested read-back path).

**Also blocked on the same gap: Automate's own 📋 Surveys sub-tab (put a whole Survey Definition
on a cadence) is hardcoded to repo end to end** — the frontend requires a selected repo before
rendering anything, and `_saveSurveySchedule` posts to `/api/schedules/repo/...` unconditionally.
`list_definitions()`'s `resource_type` field was fixed 2026-08-30 to be genuinely read per
document instead of hardcoded `"repo"`, so the backend is ready — but there is nothing for it to
show beyond repo until database/filesystem Survey Definitions exist (by the native-survey route
above) *and* the tab's resource selector is generalized to match. Two separate small pieces once
the native-survey path is proven, not one.

**Backend-routing half done (2026-09-20) — `egeria-adaptive` fold-in.** Per
`docs/design-notes/EXECUTION-MODES-HYBRID-CLARIFICATION.md` and
`PLAN-EXECUTION-MODES-VERIFICATION.md` §3, `HybridDatabaseSurveyor`
(`surveyors/database/hybrid_database_surveyor.py`) and
`run_hybrid_filesystem_survey` (`surveyors/filesystem/hybrid_filesystem_surveyor.py`) — the
strategy-selector logic that had been the default web/CLI survey path but was unreachable from
`executes_at` routing — are now reachable as a fourth `executes_at` value, `egeria-adaptive`,
registered in `other_engine_handlers` on both `database/survey_definition_adapter.py` and
`filesystem/survey_definition_adapter.py` (`_run_egeria_adaptive` in each). `web/routes/
databases.py`, `web/routes/filesystems.py`, and both `cli/main.py` database/filesystem survey
commands now route through `SurveyDefinitionExecutor.run_synthetic_step` (new — a one-step,
in-process-only `SurveyDefinition` that never touches Egeria to be constructed) with
`executes_at="egeria-adaptive"`, instead of calling `HybridDatabaseSurveyor`/
`run_hybrid_filesystem_survey` directly. `source` (`egeria` / `egeria-custom` / `custom` /
`error`) is now a first-class field on the step's own `steps_report` entry (`survey_definition_
executor.py`'s `other_engine_handlers` dispatch branch), not only inside the handler's return
value.

**Deliberately NOT done, and left as fast-follows:**
- `HybridDatabaseSurveyor`/`run_hybrid_filesystem_survey` were NOT rewritten into shims that
  delegate to the new handlers — the new handlers delegate to THEM instead, the reverse of what
  the original plan described. `tests/test_execution_modes_path_c_hybrid.py` characterizes those
  two by mocking private instance state directly (`surveyor._check_egeria_available`,
  `surveyor._egeria_surveyor`), which only means anything if the real strategy logic still lives
  on the class/function itself; rewriting them into thin callers of the new handler would sever
  that mocking path and require rewriting the characterization tests the fold-in was explicitly
  told to keep passing unchanged. Once nothing outside those two modules and their own tests
  references them directly (true as of this change, confirmed by grep), a follow-up can finish
  the retirement properly: move the logic itself into the handlers and delete the old modules,
  updating the characterization tests to target the handlers instead.
- No live-Egeria verification was done for `egeria-adaptive` (unit/mock tests only, per this
  task's explicit scope — live writes need the coordinate-shared-writes protocol). A live
  end-to-end run per resource type (as `PLAN-EXECUTION-MODES-VERIFICATION.md` §2's Path C
  recommends: "one live run per resource type against dev Egeria, asserting `source` is what
  actually happened") is still open — this is the one check that would catch "correct number,
  wrong label" for the new handler's `source` field, which no mock can catch.
- The legacy per-resource-type UI buttons/modals this backlog item's top half describes
  (`showSurveyDbModal`, `showSurveyFsModal`, etc.) are untouched — out of scope for the backend
  fold-in, and being tracked separately by the concurrent `executes_at`-visibility UI task
  (`re/execution-mode-ui`).
- Filesystem's `egeria-adaptive` handler has no cache-or-run (no `get_latest_survey` equivalent
  exists on `EgeriaFileSystemSurveyor` yet) — it always runs the local scan fresh, matching
  `run_hybrid_filesystem_survey`'s actual existing behavior rather than inventing a new Egeria API
  surface this task wasn't scoped to build.

---

#### MEDIUM (was HIGH) — Filesystem local survey: silent-failure causes fixed, true "hang" UX still open

Originally: filling out the local filesystem survey pop-up and clicking Run appeared to hang — no progress, no response to further clicks — while the server was actually alive and grinding through a very long synchronous scan, dumping a wall of `Could not profile schema for ...` warnings and pandas/openpyxl noise to the server console that the user never saw (2026-07-13 report, full console dump captured in chat).

**Implemented (2026-07-13), per `docs/filesystem-and-database-surveying.md (§5)`:**
- `IGNORE_DIRS` now skips bare `venv` as well as `.venv` — this was the concrete cause of the multi-minute scan across dozens of stray venvs (`tzdata`/`pytz` zoneinfo files) in the original report.
- `LocalFileSystemSurveyor` (`resource_explorer/surveyors/filesystem/local_filesystem_surveyor.py`) is now split internally into a metadata-only structure pass and a separate profiling pass. Per-file profiling failures and inaccessible files/directories are collected into `survey_data["profiling_errors"]`/`["inaccessible_files"]` instead of only `log.warning`, and — for the Survey Definitions run path — surfaced as real `RequestForActionAnnotation`s (`egeria_filesystem_surveyor.py::publish_step_annotations`) so a run that hits malformed CSVs/legacy `.xls` files reads as "completed with warnings," not silence. Verified against reproductions of the exact original error strings; covered by `tests/test_filesystem_survey_definition_adapter.py`.
- Kept as **one** Survey Definition step rather than two, per follow-up direction (if you're already asking the OS for one file's `stat()`, there's no benefit to walking the tree twice for the rest of it) — also matches Egeria's own native survey, which turns out to be a single un-decomposed step itself.

**Still open — this is why the item isn't fully closed:** the pre-existing `/api/filesystems/{slug}/survey` route (`web/routes/filesystems.py::survey_filesystem`, the original "📊 Run Survey" button in the Filesystem tab, distinct from the newer Survey Definitions tab — see the "unify survey launching" item below) still calls `LocalFileSystemSurveyor.run()` synchronously with no progress reporting, streaming, timeout, or cancellation. It benefits from the `IGNORE_DIRS` fix and no longer silently drops errors internally, but the browser's `fetch()` still just waits on the full run with nothing to show in the meantime on a large/broad root — the "feels like a hang" UX itself is unfixed there. No file-count/size cap or confirmation step before scanning a large/broad root path either. Likely resolves naturally once "unify survey launching" retires this route in favor of the Survey Definitions path, rather than needing its own fix.

---

#### LOW — Orphaned temp-dir cleanup on hard crash

Every repo download (full ingest, incremental refresh, Coarse Profile's `refresh_profile()`, symbol-only extraction, single-collection re-embed — confirmed all 5 call sites 2026-08-10) already downloads into a `tempfile.TemporaryDirectory()`, self-cleaning on the `with` block's exit — success, error, or exception. No local clone persists anywhere by design; disk usage from a repo download is transient, existing only for the duration of that one run. The one non-`TemporaryDirectory` temp file (notebook parsing, `NamedTemporaryFile(delete=False)`) is explicitly `os.unlink()`'d in a `finally` block.

The one real gap: a hard process kill (`kill -9`, crash, power loss) mid-download skips the `with` block's cleanup entirely, potentially leaving an orphaned temp dir (partial zipball) in the OS temp directory. Rare, self-limiting (each leftover is at most one repo's zip; the OS's own temp-dir conventions eventually reclaim it), and not actively guarded against today. A small startup sweep clearing stale resource-explorer-tagged temp dirs from a previous crash would close it — not worth building unless actual `/tmp` bloat shows up in practice.

---

#### Clustering: propose candidate blueprints, starting with the deployment perspective

Design: `docs/architecture-recovery.md (§14)` (2026-08-29). Promoted from "parallel workstream"
to **prerequisite for the curator review surface**, because rendering the corpus showed more than a
quarter of repos produce a proposal no curator could read and depth alone does not fix it.

Measured, and it makes the first step cheap: every component already carries a §4.1 perspective
(logical 1747 / deployment 1300 / physical 168 across 3,215 components), and `blueprint` is empty on
**all** of them, so clustering has never run. Applying the existing `scope_hierarchy.derive()`
grouping *within one perspective* already reaches the ~10-component goal for **deployment** on most
repos — genaiexamples 546 -> 8, genaicomps 289 -> 4, milvus 31 -> 5. The **logical** perspective does
not (egeria_git 924 -> 279), and that is precisely the perspective §4.1 says "needs inference or a
human", so the automation boundary and the design's own prediction agree.

Build order in the doc: deployment-perspective clustering first (cheapest real result, and it makes
the renderer's blueprint grouping meaningful for the first time); perspective carried in the survey
definition so a proposal records the context it was clustered for; wire density as the second signal
against the logical perspective; RFA the cases that will not cluster rather than emitting a
low-confidence grouping.

**Deployment-perspective clustering (signal 1) — DONE, since before this entry was last read.**
`arch_recovery/clustering.py` (`propose`/`_build`/`rollup`/`assign`), wired into `persist.py`'s
`_cluster()` and running on every survey. Affinity promotion (Collection -> composed component via
import-cohesion) landed alongside it. Tested: `test_arch_clustering.py`, 29 cases. This paragraph was
stale — recorded here so the next reader doesn't re-derive "has clustering run yet" from scratch.

**Wire density (signal 2) — DONE 2026-08-30, same session.** Tried only as a fallback, once every
declared boundary (deployment context, scope hierarchy) is exhausted and a group is still over the
~10 goal — not blended with those signals or given a vote alongside them. Built as greedy
agglomerative merging over the wire graph (`interfaces.propose`'s own `wires` list, the same one
`mermaid.render` draws from and `persist_ir` was already threading through as an unused parameter):
repeatedly merge whichever two groups have the strongest total wire weight between them, bounded by
`target_size`, stopping when no beneficial merge remains. Pairwise weights are computed once and
updated incrementally per merge (a merged group's weight to a third group is the sum of its two
parents' — wires are additive) rather than rescanned every iteration, since this runs on a survey's
hot path; a size backstop (`_MAX_WIRE_DENSITY_MEMBERS = 200`, unmeasured, revisit if it's ever what's
silencing a real group's wire signal) sits on top of that as insurance, not a substitute for it.
"No signal, no cluster" applies here too — a member set with zero wires between any of them returns
no split, same as `_subdivide`'s existing contract, rather than one bucket covering everyone.
Resulting clusters carry `signal: "wire-density"` so a curator can tell a measured graph from a
declared boundary. Wire endpoints resolve by slug-or-name (mirroring `mermaid._resolve_endpoint`'s
existing handling of the same ambiguity — a compose wire is attributed by service name, a port by
slug), kept as its own small copy rather than a shared import, same reasoning as
`ComponentMaterializer._find_element_guid` duplicating `EgeriaPublisher`'s.

**Shared interface (signal 3) — DONE 2026-08-30, same session, following directly from the
interface-extraction work above (item A's OpenAPI/FastAPI reuse, item B's language-binding
evidence).** The LAST fallback in the chain — tried only once deployment context, scope hierarchy,
AND wire density have all found nothing further — because it needs an IDL/OpenAPI document per
component, rarer input than a wire between two components that simply call each other. `interfaces.py`
was extended first, to capture a structured *name* rather than just a count: `_openapi_info()`
(renamed from `_count_openapi_operations`) now returns `info.title` alongside the operation count,
`_count_proto_rpcs()` returns the joined, sorted, unique proto/gRPC service names, and both (plus
Thrift) pass an `interface_name` into `_port_dict()`, stored in `additionalProperties["interfaceName"]`
— the same sanctioned extension point `operationCount` already uses. GraphQL deliberately does NOT
get one: its root type names (`Query`/`Mutation`/`Subscription`) are the same across almost every
GraphQL service, so treating them as a shared-identity signal would cluster unrelated services that
merely both speak GraphQL.

`clustering.py` gained `_interface_names(components, ports)` (`{slug: {declared name, ...}}`,
resolved slug-or-name the same way `_wire_density_split` resolves wire endpoints) and
`_shared_interface_split(member_scopes, by_scope, interface_names)` — an exact partition by shared
name, not a size-bounded weighted merge like wire-density: a name two or more scopes present together
is the whole signal, so there is no `target_size` to respect the way wire-density has one. A scope
presenting more than one interface name goes to whichever shared group is largest (deterministic
tie-break by name), and a name only ONE scope presents contributes nothing — "no signal, no cluster"
again, same as `_subdivide`/`_wire_density_split`'s existing contract. `_build()` and `propose()` got
a new `interface_names`/`ports` parameter threaded exactly like `wire_weights`/`wires` was for signal
2 (including through `persist.py`'s `_cluster()`, which already received `ports` as an unused-for-
clustering parameter). Resulting clusters carry `signal: "shared-interface"`.

Confirmed by test that wire-density strictly wins when both signals would apply to literally the same
subset (a densely-wired group that also shares an interface name clusters as `"wire-density"`, never
`"shared-interface"`) and that shared-interface still gets its turn where wire-density genuinely finds
nothing (an interface-only context group with no wires among its own members).

**Found and fixed alongside it — a live, untested bug in `_build`'s recursive `_subdivide` branch:**
the call `_build(sub_name, sub_scopes, by_scope, perspective, target_size, depth_left - 1)` passed 6
positional args to a 7-parameter function (missing `by_scope_components`), so every value after it
landed in the wrong slot and `depth_left` got none at all — a `TypeError` on any call. Unreached by
all 29 pre-existing tests: the oversized-cluster tests use flat scope locators (`flat::s{i}`)
specifically so `scope_hierarchy.derive` finds nothing to subdivide, which is exactly what kept this
branch from ever running; real corpus runs likely never hit it either, since an oversized group needs
a deployment-context split to be genuinely unavailable (not just single-valued) AND a further scope
hierarchy to exist below it. Two new regression tests exercise `_build` directly (bypassing
`propose()`'s own first pass, which finds the finest qualifying split in one shot for realistic path
hierarchies and so never naturally reaches this branch either) to prove the recursive call no longer
raises and the second-level split is real.

Suite: `test_arch_clustering.py` grew from 29 to 50 cases (the bug-fix regression, ten
`TestWireDensitySignal` cases, seven `TestSharedInterfaceSignal` cases, and two end-to-end tests
proving `persist_ir` actually threads `wires`/`ports` through `_cluster()` into
`clustering.propose()` — a unit test of `propose(wires=..., ports=...)` alone would not have caught
a broken wire-up in between). `test_arch_interfaces_idl.py` +6 (`TestInterfaceNameIsAStructuredIdentity`
— OpenAPI title capture, proto/Thrift service-name capture, GraphQL exclusion, absent-title case).
Broader arch/clustering/interfaces/mermaid suite: 507 passed, 9 skipped.

---

#### RE's identity is still inconsistent across 14 sites (was: "RE has no login at all")

**Status update, 2026-09-07** (TC-8, `packages/egeria-advisor/BACKLOG.md`): three of this entry's
four parts are done, all landed 2026-09-04. This section previously described all four as open;
corrected here, verified by reading the code rather than assumed from the original entry.

- **`trellis-auth` extraction** — done. `packages/trellis-auth/` exists; both `advisor/auth.py`
  and `resource_explorer/auth.py` import it for the app-neutral JWT/Portal-SSO pieces.
- **A login UI in RE's SPA** — done. A real `#login-overlay` form is in
  `resource_explorer/web/static/index.html`, and `LoginRequiredMiddleware` is installed in
  `web/app.py`. "Connected as: erinoverview" is no longer cosmetic for a signed-in user.
- **A declared service identity for unattended runs** (surveys/schedulers have no signed-in
  user) — done, and it's a real, deliberately-labeled mechanism, not a fallback that quietly
  reused the same path as a signed-in person: `resource_explorer/egeria_identity.py`'s
  `EgeriaIdentity(is_service_account=True)`, sourced from `get_config().egeria.user_id`/
  `user_password` (a configured account, matching the design doc's requirement that this be
  distinct from EA's SS-4 fallback removal). `run_queue.py::_run_as_requester` documents the
  policy directly: a queued run with a `requested_by` sets `current_caller` to that person (so
  `Ownership` on anything it publishes is attributed correctly); a row with no `requested_by`
  — "the worker's own service-account work" — runs with no caller at all and falls through to
  this same service identity. Covers bootstrap heal, resync, and the outbox drain by name in
  that function's own docstring.
- **Still open: collapsing RE's identity call-sites onto the authenticated identity.** Down from
  26 to **14** remaining `os.getenv("EGERIA_USER", …)` sites (`rfa_egeria_sync.py`,
  `surveyors/egeria_reader.py`, `surveyors/survey_definition_reader.py`,
  `surveyors/egeria_publisher.py`, `surveyors/database/database_surveyor.py`,
  `surveyors/database/bootstrap_data_classes.py`,
  `surveyors/database/egeria_database_surveyor.py`,
  `surveyors/arch_recovery/materializer.py`,
  `surveyors/arch_recovery/blueprint_materializer.py`,
  `surveyors/arch_recovery/port_materializer.py`,
  `surveyors/filesystem/egeria_filesystem_surveyor.py`, `web/routes/egeria.py` (×2),
  `cli/main.py`), still with four different fallback literals (`_DEFAULT_USER`, `"steward"`,
  `"erinoverview"`, `""`). Each of these builds its own pyegeria client directly rather than
  going through `egeria_identity.py`'s resolution — the actual remaining work this entry
  originally flagged.

Full background: `docs/trellis-auth-extraction.md` at the Trellis root (its own status line and
"still to do" lists are themselves stale as of this update — same three items, not corrected
there yet).

---

#### HIGH — extract a shared query cache into a Trellis package; it fixes a live bug in Egeria Advisor

Full detail: `docs/re-ea-consolidation-audit.md` item 1.

RE's `query_cache.py` (124 lines) is genuine LRU (`OrderedDict` + `move_to_end()` on access) with
TTL and an optional Redis backend. EA's `query_cache.py` (169 lines) is named and documented as
LRU throughout but **is not** — plain `dict`, no reordering on access, just FIFO eviction of the
oldest insertion. EA does have hit/miss/`most_popular` telemetry RE lacks.

**Proposed:** a shared `trellis-`package `QueryCache` built from RE's TTL/Redis/invalidation
design as the base, with EA's stats/`most_popular` reporting layered on top — same shape as
`trellis-vectorstore`'s extraction (each app keeps a thin adapter over the shared class). Fixes
EA's eviction bug as a side effect of the extraction, not as separate work.

---

#### HIGH — extract the BeeAI agent base/runner shared by RE and EA

Full detail: `docs/re-ea-consolidation-audit.md` item 2.

RE's `resource_explorer/agents/base.py` (`BaseExplorerAgent`, 200 lines) and EA's
`advisor/agents/base.py` (`BaseAdvisorAgent`, 83 lines) both hand-roll the same BeeAI
`RequirementAgent` construction and the same "sync caller in an async context → spawn a thread
with a fresh event loop" workaround, down to matching inline comments explaining why BeeAI needs
a fresh event loop in a thread. This is copy-pasted logic, not two teams converging on the same
idiom independently — same tier of confidence as `trellis-microflow`'s extraction.

**Proposed:** a shared `BeeAIAgentRunner`/`BaseAgent` mixin covering `_build_agent()`/
`_run_agent()`. RE's slug-inference/clarification helpers and EA's separate (apparently dead)
"legacy... not using BeeAI" `BaseAgent` scaffolding stay app-specific — check whether that
second EA class is still referenced anywhere before or alongside this extraction.

---

#### MEDIUM — EA should adopt RE's dual-backend registry connection-management pattern

Full detail: `docs/re-ea-consolidation-audit.md` item 3.

RE's `registry.py` has a mature `ConnectionWrapper` + SQLAlchemy dual-engine abstraction
(SQLite↔Postgres placeholder/DDL translation, pooling, same-transaction column introspection
working around a real Postgres visibility bug) that RE already reuses across `registry.py`,
`observability/metrics_collector.py`, and `feedback_store.py`. EA's `db_consolidated.py`
(`ConsolidatedDBManager`, 283 lines) reinvents a narrower, Postgres-only version of the same
idea — same shape as the Java-symbol-extraction finding in the ingestion audit: one app's
implementation is simply more mature, and the other never adopted it.

**Proposed:** the schemas stay separate (project/resource state vs. metrics/audit/symbol
tables are genuinely different data) — only the connection-management primitive moves, with EA
adopting it. Not urgent while EA's Postgres-only assumption holds, but worth doing before EA
needs a SQLite fallback path RE has already solved.

---

### Data model & naming

#### `Project` means three different things in `registry.py` (four counting EA's tables)

A single registered repo (`Project`, `projects` — `registry.py:35-58`, `:441-464`), a grouping of
repos (`ProjectGroup`, `group_slug`, `project_groups` — `:26-31`, `:482-488`), and an intra-repo
subdivision (`subproject_path`/`parent_slug` — `:52-53`, a genuinely different concept that must not
be swept up in a rename). The existing workaround is the standing rule to *always* write "Egeria
Project" and never bare "Project" (`docs/discovery-automate-project-context-plan.md:215-220`).

Proposed: `Project` → `Repo`/`Resource`, `ProjectGroup` → **`Owner`** (not `Org` — GitHub's model is
an owner of type `User | Organization`, and plenty of repos live under a person). Scope, cost and the
API-path fix are in `docs/investigation-framing-design.md` §7.

**Tripwire, before anyone runs a regex sweep:** the registry is shared PostgreSQL
(`config.py:242-248`), not the SQLite `data/registry.db` suggests (0 bytes, stale), and Egeria
Advisor reads RE's tables cross-schema by hardcoded string — `advisor/re_code_symbol_reader.py:22`,
`advisor/agents/code_intel_agent.py:34-35`, `advisor/rag_retrieval.py:272-273`,
`advisor/analytics.py:237`, `advisor/re_code_scope.py`, `advisor/agents/tools.py:90`, all naming
`resource_explorer.project_code_symbols` / `project_code_relationships`. Renaming those tables leaves
EA compiling cleanly and failing at runtime. Either leave them alone or fix all six call sites in the
same commit.

---

## Closed

Kept rather than deleted: a recorded negative — *we checked, and it genuinely isn't there* — is what stops the next person re-investigating. Three entries were re-derived from scratch on 2026-08-24 because nobody could tell a closed question from an unasked one. Entries here are fully closed; anything still carrying live work stayed above, however its heading reads.

#### ~~`project_dependencies` has no survey-step writer~~ — RESOLVED 2026-08-23

Closed by the `repo_manifest_parse` step (`sub_surveyors/manifest_parse.py`), which writes
`project_dependencies` from DependencyParser/CiWorkflowParser/RepoConventionsParser.

---

#### ~~`repo_classification` declares `fetch_cost="none"` and calls the GitHub API~~ — RESOLVED 2026-08-24

The feature was right, the declaration wrong. Cost is now declared from measurement, not the
dataclass default — see the comment on the `repo_classification` `StepInfo` in
`surveyors/repo_survey_definition_adapter.py`. `step_cost_observer.py` is what caught it and
is what stops the next one.

---

#### BUILT 2026-08-20 — a "no silent success" ratchet

`tests/test_no_silent_success.py`. Covers one of the four silent-failure classes above.

---

#### BUILT 2026-08-20 — live smoke tier + pinned error payloads

`tests/test_egeria_live_smoke.py`.

---

#### ~~HIGH — filesystem annotations never reach Egeria~~ — FIXED 2026-08-20

Suspected in `docs/survey-model.md`, confirmed and closed.

---

#### ~~Automate had never notified anyone~~ — FIXED 2026-08-20

Two independent faults, both closed. Detection in `notification_detector.py`, delivery via
`scheduler.py`'s `_check_subscriptions()`.

---

#### ~~RFAs written by `log_rfa()` never reached the RFA drawer~~ — FIXED 2026-08-20

The drawer reads them via `GET /api/activity/rfas`; see `web/routes/activity.py`.

---

#### RE locally executing Survey Definitions — IMPLEMENTED 2026-07-07

`surveyors/survey_definition_executor.py` + the per-resource-type adapters.

---

#### ~~HIGH — populate `IR.ports` and `IR.wires`~~ — **BUILT 2026-08-23, entry was stale**

`surveyors/arch_recovery/interfaces.py` extracts them from Dockerfile `EXPOSE`, compose
`ports:`/`expose:`/`depends_on:` and OpenAPI documents. `propose()` is called from the product path
(`sub_surveyors/arch_recovery_detect.py`), `arch_recovery/persist.py` writes both as findings with
wires attributed to the **source** component, and `_renderDeploymentInterfaces()` renders them.
Egeria's `SolutionPortDirection`/`SolutionLinkingWire` vocabulary was checked first, as design `architecture-recovery-extraction-design.md` §5.5f
asked — the third time that check was the right first move.

`ir.py`'s fields carried a `# not in this slice` comment for three weeks after that stopped being
true; corrected in place.

**This entry stayed marked HIGH after the work shipped**, which is worse than a missing entry — a
stale HIGH sends the next person to build something that exists. It is a class, not an incident:
finding 89 ("committed and regression-tested is not reachable"), finding 98 (merged, pushed and
live-verified is *also* not reachable), `recovery_gate`'s docstring citing `kubernetes/website` as
a repo it skips when it runs, and `repo_classification`'s declared cost. **The label is not the
evidence.**

**What is genuinely still open here is coverage and precision, not capability** — see the
architecture-recovery entry below. Ports and wires exist for the 16 repos recovery has actually
been run on, of the 46 the gate approves.

> Verified empty, and **nothing anywhere populates them**: both fields carry `# not in this slice`,
> `architecture-recovery-extraction-design.md` §5.2's distillation steps 4 and 5 are unbuilt, and §3.2's `SolutionPortDirection` has never been
> written. `ApiStructureSurveyor` does not cover it — it counts symbols and module structure, which is
> internal shape rather than exposed surface.
>
> **Why it is the biggest gap:** everything black-box we have built reads metadata *about* a resource
> (README, docs, manifests, deployment artifacts), not the interface *of* it. The system can say "an
> application with deployment artifacts" but not "serves these endpoints, consumes this topic, needs
> these ports" — and the second is what "does it fit our infrastructure" means.
>
> **Why it is cheap:** interface evidence is largely black-box observable and often in artifacts
> already fetched — OpenAPI/Swagger, `.proto`, GraphQL schemas, compose `ports:`/`expose:`, Dockerfile
> `EXPOSE`, k8s `Service` manifests, declared entry points, configured topic names. Mostly no source
> parsing, so **Discovery tier by rule 17's own test**.
>
> Check Egeria's existing vocabulary first — `SolutionPortDirection` and `SolutionLinkingWire`'s
> `protocol`/`integrationStyle`/`frequency`/`dataExchanged`/`oneWay` already exist. Third time this
> check has been the right first move, after `SolutionComponentType` and `ResourceUse`.
>

---


---

### HIGH — interface extraction answers "does it expose something", not "can I use it"

**The driving question, from the project owner, 2026-08-24:** *"if we want to see if a repo is something we can
use during runtime, we need to know how to interface to it — what kind of API it has, maybe
language bindings, the number of commands. We don't need the names of every request and their
payloads/signatures — until we want to actually try to use it."*

That is a **suitability** question, and it wants a coarse answer.

**Items 1 and 2 below are DONE (`b1488be`, "RE: Milvus is gRPC-first and we could not see it")** —
recorded here so the next reader doesn't re-derive it, since this entry sat stale describing them
as open after they'd already landed. `interfaces.py` now recognises `.proto`/GraphQL SDL/Thrift IDL
alongside OpenAPI (`_PROTO_EXT`/`_GRAPHQL_EXTS`/`_THRIFT_EXT`), and `operation_count` (OpenAPI
`paths` × methods, `.proto` `service`/`rpc` counts) rides in each port's `additionalProperties`.
Milvus's gRPC surface — the case that motivated this — is no longer invisible.

**What genuinely remains open, sharpened by the project owner, 2026-08-30:**

**A. OpenAPI/REST/Swagger detection needs a second path — DONE for FastAPI, 2026-08-30, same
session.** `_OPENAPI_NAMES` only ever saw a *committed* spec file (`openapi.json`, `swagger.yaml`,
…); a FastAPI service generates its spec at runtime from its own route decorators and ships none —
this codebase's own web app was exactly that case, recording nothing.

Built as reuse, not a second detection: `code_markers.py`'s `fastapi-route-registration` rule
already matches individual `@app.get`/`.post`/`.put`/`.delete`/`.patch`/`.websocket` decorators —
one match per route, collected per-file for *component* classification
(`arch_recovery/rules/fastapi-route.yml`) — but the count was discarded once converted into a
component. `code_markers.propose()` now returns it as a 4th value, `{component slug: route count}`
(`OPERATION_MARKERS`, a named subset of rule IDs that are genuinely per-operation, not per-file),
threaded through `detectors.build_components()` → `arch_recovery_detect.py` →
`interfaces.propose()`'s new `code_marker_operations` keyword. `interfaces.py` emits an `HTTP/REST`
port from it for any component with a nonzero count **that has no port already from a static
document** — a checked-in OpenAPI file is stronger, filename-attributable evidence than a decorator
count, and both existing would report one REST interface as two, so the static-document reading
wins where both exist.

**Confirmed NOT free for Spring/Go, as flagged** — `OPERATION_MARKERS` has exactly one entry.
Spring's marker (`java-spring-service.yml`) matches `@RestController`/`@Controller` at the class
level, and Go's (`go-http-server.yml`, `go-grpc-server.yml`) match server *construction* — neither
is a per-endpoint marker, so adding their rule IDs to `OPERATION_MARKERS` would count "1" regardless
of how many routes exist. Getting the same countable granularity for Spring needs a new rule on
`@GetMapping`/`@PostMapping`/`@RequestMapping`-family method annotations; for Go it needs rules on
whichever router's per-route registration call (`mux.HandleFunc`, gin's `.GET`, echo's `.GET`, …) a
given service actually uses. Left as a clearly-scoped follow-on, not attempted here.

Suite: `test_arch_interfaces_idl.py` +5 (the reuse, the static-document precedence, the no-owner
skip, the zero-count skip, and backward compatibility with no `code_marker_operations` passed),
`test_arch_recovery_detectors.py` +2 (the count itself, and that a subtree with no route decorators
is absent rather than zero). 214 passed across the directly affected files; 503 passed across the
broader arch/interface/marker test surface.

**B. Language bindings — the project owner's steer narrows this from the original proposal, doesn't confirm it.**
The entry as written proposed conventional directories (`clients/<lang>`, `sdk/<lang>`,
`bindings/`) as the signal, and called it "weakest evidence of the three; do it last." Project-owner decision, 2026-08-30:
*"Not sure about language bindings unless they are exported as a specific library — eg. pyegeria."*
That rules the directory-convention approach out rather than deferring it — a folder named
`clients/python` is not evidence a real, usable client library exists at that path, and the
codebase's own `_deployment_context_of`-style principle (read a declared boundary, don't infer
intent from a name) argues the same way here.

What the project owner's example asks for instead: recognise a **named, published package that IS a client
library for this project** — `pyegeria` is a real PyPI package, with its own name and description,
that exists specifically to bind to Egeria. That is verifiable evidence a directory name is not.

**DONE, first cut, 2026-08-30, same session — Python and Node only.** Not `manifest_parse.py`/
`DependencyParser` in the end (that pipeline belongs to a different survey step, `ManifestParseSurveyor`
via `IngestionPipeline`, which `architecture_recovery` doesn't depend on and shouldn't couple to) but
the *same kind* of parsing the entry anticipated, on the surface that was already free:
`detectors.python_manifests()`/`node_manifests()` already read `pyproject.toml`'s `[project]` table
and `package.json` wholesale for `classify()`'s "installable, no entry point ⇒ Software Library"
signal — `description` was sitting in the already-parsed structure, unread. One field added to
each, no new file walk, no new parse.

**Deliberately NOT a classifier.** `build_components()` now attaches a second Evidence entry to any
component `classify()` already calls `"Software Library"` (installable, no entry point — exactly
pyegeria's shape) that has a non-empty `description`: the description, verbatim, up to 200 chars,
assertion `"publishes a {ecosystem} package — possible language binding"`. Nothing here decides
*whether* it's a binding — pyegeria's own description ("A python client for the Egeria metadata
management system") needs no inference to read as one, and that restraint is the same one `protocol`
already exercises by staying empty rather than guessed from a port number. A package with no
description, or with an entry point (a CLI, not installable-as-a-library), gets no binding evidence
at all — nothing invented to fill the gap.

**Directory-convention detection (`clients/<lang>`, `sdk/<lang>`, `bindings/`) was NOT built**, per
The project owner's steer ruling it out rather than deferring it.

**Real scope limits, stated rather than discovered later:**
- **Java (Maven/Gradle) and Go are not covered.** `python_manifests`/`node_manifests` are the two
  existing readers with a clean `name`/`description` shape to extend; `pom.xml` isn't parsed into a
  dict at all today and Gradle's `settings.gradle` module list has no description field to read.
  Real follow-on work, not attempted here.
- **Single-repo only, and this is the sharper limit.** `pyegeria` is Egeria's binding but lives in a
  *different* repository (`egeria-python`) from Egeria's own server code. Analysing the Egeria server
  repo alone will never surface pyegeria as evidence — this only finds a binding a repo publishes
  *of itself*, e.g. running this against `egeria-python` would find pyegeria's own self-description.
  Cross-repo binding discovery (recognising that some OTHER analysed repo is a stated dependency of
  and/or names the analysed one) is a different, larger question, not scoped here.
- **Not yet surfaced in the curator-facing card.** The evidence is persisted and readable
  (`_architecture_recovery_results`'s per-component `evidence` list already carries it, same generic
  path every other Evidence record takes through `persist.py`), but `_archRow`'s summary line shows
  only `proposed_by` (detector labels), not evidence text — a curator has to look past the summary to
  see the description. A presentation follow-up, not a detection gap.

Suite: `test_arch_recovery_detectors.py` +4 (an installable package with a description gets binding
evidence; a console-command package does not; a bare name with no description does not; Node
packages are covered too). 207 passed across the directly affected files.

**Do NOT** extend either A or B into reading request/response schemas or binding call signatures.
That is stage two, a different cost tier, and the driving question explicitly excludes it.

---

### HIGH — summarising microflows: the mechanism is Egeria's, and RE discards it

**The gap** (finding 101a, and the reason both open precision problems look unsolvable): *nothing
owns summarising up*. Every microflow emits at its own natural granularity and no step collapses it
to the depth the question asked for. Milvus yields 154 components where its own authors say eight,
and 296 rpcs where the number a reader wants is `proxy.proto`'s 18.

The project owner's framing: *"we can certainly create microflows that aggregate, summarize and transform
information collected from established results — and include them where needed, or standalone."*

**The correction that matters, and it is the whole point of this entry.** The first version of this
proposal invented a new `requires_results` declaration to sit alongside `requires_resources`, on the
claim that "nothing declares a data dependency between steps." **That claim is false**, and the
proposal would have been a third vocabulary for something Egeria already models. From
`0462-Governance-Action-Processes`:

```
GovernanceActionExecutor         requestType, requestParameters,
                                 requestParameterFilter, requestParameterMap,
                                 actionTargetFilter, actionTargetMap
GovernanceActionProcessFlow      guard, requestParameters
NextGovernanceActionProcessStep  guard, mandatoryGuard
TargetForGovernanceAction        actionTargetName
```

A completing governance service *"optionally supplies one or more guards **and a list of action
targets** for the subsequent governance action(s) to process"* (concepts/governance-action-process).
So step-to-step data flow is modelled, **named** (`actionTargetName`), **filterable**
(`actionTargetFilter`, `requestParameterFilter`) and **rebindable** (`actionTargetMap`,
`requestParameterMap`). Not merely passed — bound.

**So a summarising microflow is not a new kind of step.** It is an ordinary step whose **action
targets are the findings of prior steps**, with `requestParameters` carrying the depth or
summarisation level. That also gives Purpose (investigation-framing §3) a real home: depth of
response is a request parameter on the flow, not another new concept.

**What is genuinely missing is RE-side: it parses the model and throws it away.**

| Mechanism | State in RE |
|---|---|
| Additional Properties (`executes_at`, `re_analysis_step`, …) | *"parsed but not interpreted here"* — `survey_definition_reader.py:18` |
| `guard` / `mandatoryGuard` | round-trip on every link; a live read returns `guard: 'Any'` on all 9 links of Analysis Survey — **"the reader receives them and discards them"** |
| Branching | `UnsupportedSurveyDefinitionError` — *"v1 only supports linear step sequences"*, deferred by decision 2026-08-21 |
| Action targets / request parameters | not read at all; `SurveyStep` has no field for either |
| The specification itself | *"RE consults no Egeria specification at all. `STEP_REGISTRY` is a specification living in Python."* |

`requires_resources` is RE's parallel invention for a *different* problem — sharing an expensive
external resource (a zipball, a clone) across steps in one run, which is what `trellis-microflow`'s
`resolve_resources` solves. It is not a data dependency and should not be extended into one.

**Proposed work:**

1. **Read what is already received.** Add action targets and request parameters to `SurveyStep`,
   and stop discarding guards. No new vocabulary — these are attributes the reader already fetches
   and drops on the floor. Smallest possible first step, and it makes the rest measurable.
2. **One summarising microflow, as an ordinary step**, whose action targets are the
   `architecture_recovery` findings of a prior step and whose request parameter is the depth. Prove
   the shape on the case that motivated it: 154 components → "N subsystems, M services, one gRPC
   surface".
3. **Then decide about branching**, which is the deferred v1 boundary and the real work.

**Two constraints, both from failures already recorded here:**

- **A summariser whose inputs are absent must not emit an empty summary.** It must produce
  `not_established` / `SKIPPED_BY_DESIGN` with the reason, exactly as `recovery_gate`'s skip does.
  A confident summary of nothing is the absence-looks-like-zero shape (findings 63, 90, 97, 99, 100)
  promoted to the composition level, where it is *harder* to see because the output looks like a
  real answer.
- **Check the Egeria model before inventing a local one.** This entry exists because that check was
  skipped once here, having paid off three times and produced one useful negative (`ResourceUse`,
  `architecture-recovery-extraction-design.md` §5.5d-i) the same day. The pattern of the miss is worth more than the miss: the vocabulary check
  was performed for the *port count* an hour earlier and not for *step composition*, because the
  answer there had already been assumed to be local.

---

### Doc-site located but unreadable → offer to ingest it, and ask while a human is there

**Project-owner decision, 2026-08-25:** *"there is an opportunity to ask the user if they want to ingest the
documentation web site (or portions of it) into the vector store to support deeper analysis — this
would likely fall into the understanding stage and might surface as a RECOMMENDATION for future
analysis... Remember that we have the chat interface to design with, and that supports us asking
questions (as long as we aren't doing a scheduled survey). We shouldn't be too chatty but we do want
to take advantage of interactive sessions."*

**The capability already exists; the connection does not.** `repo_website_ingestion` ingests a
project's documentation site into pgvector "so Chat and Understanding can answer from the project's
own documentation rather than only its source tree" — keyed on the site's host so several repos in
one project share one collection, and skipping sites the repo builds itself. That is precisely the
proposal. What is missing is that **nothing ever suggests running it**, and measured:

```
repos with website_ingestion findings          0 of 60
repos where repo_arch_lens found a doc-site     2   (sqlglot, unitycatalog)
   ...both with an empty `homepage`, so the step could not run today anyway
```

So there are three distinct gaps stacked, and only the first is new work:

1. **No recommendation link.** `repo_arch_lens` produces `doc-site` — located, and explicitly *not
   readable from here*. That is the single most actionable negative result in the chain: we know a
   document exists, we know where, and we know we cannot use it. It should surface as a
   **suggested action** (`github/suggested_action.py`) pointing at `repo_website_ingestion`, not as
   a note in a JSON blob.
2. **`homepage` is empty** on both doc-site repos, so `repo_website_ingestion` has nothing to
   resolve. Whether that is a `repo_homepage` gap or genuinely absent upstream metadata is
   unmeasured.
3. ~~**The step has never run anywhere.** Zero of sixty.~~ **WRONG, corrected 2026-08-25.** It has
   run, on 6 of 60. It writes **metrics and never findings**, and the claim came from a
   findings-only query:

   ```
   website_ingestion   findings: 0 of 60      <- what was measured
                       metrics:  6 of 60      <- what exists
   ```

   The real defect was different and larger, found by the presentation session: on the other 54 the
   results reader returned `chunks/pages_fetched/pages_found/pages_failed` as 0, and `metrics`
   render mode lays every key out as a labelled row — so 54 cards read *"we scanned the site and
   found nothing"* about a site nobody had ever looked at. `result_status.NEVER_RUN` already
   describes the correct behaviour and nothing was emitting it. Fixed in `eeb5363`, along with a
   second instance the same guard immediately found in `rag_ingestion`.

   **The transferable error is mine and it is now three-for-three.** `query_findings(slug, kind)`
   defaults to `scope_locator=""`, and a step may write metrics rather than findings. So a bare
   findings query establishes *"nothing at whole-resource scope in the findings table"* — never
   *"this never ran"*. It has produced a wrong published number three times in two days:
   architecture recovery read as 3 of 46 when it was 16; this entry read as 0 of 60 when it was 6;
   and the verification script written to check *this very correction* reported
   `architecture_doc_lens` findings as 0 while 36 labels sat in it, scope-keyed.

   **A count is not an absence unless the query covers every shape the answer could take.**

**The interactive-question point is the reusable part, and it is a design constraint we have not
written down anywhere.** RE has a chat interface, so an interactive session *can* ask. A scheduled
survey cannot: there is nobody there, and a step that blocks on an answer would hang a cadence.
Both must be true of the same step. The shape that satisfies both:

- **Interactive** → ask, once, at the point the evidence appears ("this project's architecture
  documentation is at `<url>` and I cannot read it from here — ingest it?"). One question, at the
  moment it is answerable, is not chatty; a checklist of them is.
- **Scheduled / unattended** → emit the recommendation and move on. RFA already exists for exactly
  this, and `suggested_action`'s `next_step` field already distinguishes `rfa` from `subscription`.
- **Never** → block, retry, or ask again on the next run. An unanswered recommendation is a
  standing offer, not an open question.

That distinction is worth stating in the design docs independently of this feature, since every
future step that would benefit from a human answer meets it. **Understanding is the right stage**
(the project owner's read): the ingested site serves Chat and cross-resource questions, which is what Understanding
is for — not Discovery, where it would look like another survey step.

**Not started.** Sized as small-but-not-trivial: item 1 is a link between two existing subsystems,
item 2 needs a measurement first, item 3 is a live-verification pass on a step nobody has run.

---

### Stage and profile a documentation site before deciding how to ingest it

**Project-owner decision, 2026-08-25:** *"what are your assumptions as you make an ingestion? Do we need to do some
pre-analysis first, and perhaps internally stage the content and then profile it before we decide
how to ingest it?"*

Asked after three ingestion defects in one session. The honest answer is that ingestion currently
**fetches, chunks and embeds in one pass with no decision point**, and every assumption below is
made implicitly — none is checked, and none is visible in the result.

**What `repo_website_ingestion` assumes today, read out of the code:**

| # | Assumption | Where | Observed failing |
|---|---|---|---|
| 1 | The homepage URL is the documentation site | `repo_homepage` fallback to manifest/README | badges, registries, another project's docs — 10 of 60 (finding 108) |
| 2 | A sitemap (or the landing page) identifies the right pages | `discover_pages` | milvus: 400 sitemap URLs, **every fetch failed** |
| 3 | The pages can be fetched by a plain HTTP client | `self._fetch` | milvus.io 302-loops without a browser-like client |
| 4 | Tag-stripping yields useful text | `_extract_text` | untested against JS-rendered sites; a client-rendered page yields an empty string, indistinguishable from a page with no content |
| 5 | One chunk size fits all of it | `web_docs` type: 384/48, fixed | an API reference and a narrative guide are not the same shape; `api_reference` exists as a separate type and is never selected for a website |
| 6 | Every discovered page is worth embedding | no relevance filter | the sibling-website case: navigation, marketing and blog pages embedded as documentation |
| 7 | The site is one version | none | versioned docs sites embed N copies of the same page, splitting retrieval across them |
| 8 | The content is not already held | `self_published` check only | that check catches a repo building its own site; it does not catch overlap with another repo's already-ingested content |

**Assumptions 2–4 are the same failure**, and it is the expensive one: 685 seconds spent on milvus
producing nothing, discovered only afterwards. **Nothing in the current design can fail early**,
because there is no point between "we have a URL" and "we have embedded it" at which anything is
inspected.

**What a stage-and-profile step would answer**, in the order the cost rises:

1. **Is it reachable at all, and by us?** One fetch of the landing page. Would have ended the milvus
   run in under a second instead of 685.
2. **Does text come out?** Extract from a handful of pages and measure. A site that yields 40
   characters a page is client-rendered and needs a different fetcher — or is honestly out of scope,
   which is a fine answer if it is *stated*.
3. **What kind of documentation is it?** API reference, narrative guide, blog, marketing. This
   selects the chunking profile, and the collection types for it already exist and are never chosen.
4. **How much of it is boilerplate?** Nav, footers and sidebars repeat on every page; measuring the
   repeated fraction before embedding says whether the ingest is mostly chrome.
5. **Is it versioned, and is anything already held?** Both are duplication, and both split retrieval
   rather than improving it.

Only then decide **whether** and **how** to ingest — rather than ingesting and finding out.

**Why this is worth building rather than adding more guards.** Finding 108's guards fixed the
*input* (is this URL plausibly this project's docs). Everything above is about the *content*, which
no URL check can reach. And the pattern is one this codebase already trusts: architecture recovery
is exactly stage-then-decide — cheap classification gates the expensive tier — while ingestion has
no gate at all.

**Design constraints, from what already went wrong:**

- **Staged content must be inspectable before it is committed.** The value is the decision point,
  not the caching.
- **A profile that says "do not ingest" is a result, not a failure** — with its reason, renderable
  the way `skipped_by_design` is. Three of today's defects were a system being right and recording
  it in a way that read as being wrong.
- **Do not add a boolean beside the outcome.** `detail["ingested"]` was hardcoded `True` while the
  `StepOutcome` next to it correctly said the site was never read, and downstream code read the
  boolean. A profile with a `usable: true` flag would repeat that exactly.

---

## Export findings/metrics for use outside RE

**Raised 2026-09-01**, after a Committed Secrets run reported 48,581 matches and the drawer took
minutes to render an incomplete list. Project owner: *"there should probably be a separate report that is
downloadable - maybe a csv that contains all these details. They aren't useable in the raw in
Results."* Agreed as worth doing, deliberately **not** built the same day, because fixing the
scan's own defect (`docs/` — the ruleset's per-rule entropy/allowlist/stopword gates were never
read) took that repo from 48,581 rows to 5, which removed the urgency and changed what the
feature is for.

**Build it once, on the two generic tables — not per survey.** `project_analysis_findings` and
`project_analysis_metrics` already reduce every analysis kind to two shapes, so an export hung
off those means each current *and future* kind gets it from its `AnalysisKind` registration.
The alternative — an export per analysis — is ten places to drift, the same argument that
produced the registry in the first place.

**The requirement that makes this non-trivial: an export must not be able to lie more
confidently than the screen it replaces.** A file reads as complete; there is no scrollbar to
suggest otherwise, and it travels — into a ticket, a spreadsheet, someone else's inbox — long
after the run that produced it. Two specific hazards, both of which this codebase has already
been bitten by in other forms:

- **Truncation must be self-evident.** A 200-row export of a 48,581-row result is worse than a
  truncated page. The row count as the *source* reports it, and any applied filter, belong in
  the artifact.
- **Provenance must travel with the rows.** Analysis id, `surveyed_at`, provider name and
  version (ruleset commit, tool version), the coverage denominator (files scanned vs excluded),
  outcome status and self-test result. The 2026-09-01 episode is the argument: the count was
  wrong by four orders of magnitude *and the provenance line was impeccable*. Stripping that
  metadata into a bare CSV is how a careful finding becomes a confident spreadsheet.

**CSV and JSONL, and the reason is honesty rather than choice.** Findings carry a nested
`detail_json`; flattening it to CSV loses information, so CSV-only is the same
"looks complete, isn't" problem one layer down. CSV for the columns people actually paste into
a spreadsheet; JSONL when they need everything.

**Two things this is explicitly not:**

- **Not a substitute for a render cap.** The 48,581-row page was the bug; an export does not fix
  it. A user must never be able to get an incomplete screen that does not say so. That is a
  separate, smaller change and stands on its own merits.
- **Not the same as publishing to Egeria.** RE already has that path, and it serves the catalog.
  Export serves humans and external tools — a ticket, a review, a vulnerability tracker. Naming
  the distinction here so the item is not later closed as "we already have publish".

**Trigger for picking it up:** a second analysis with a real external workflow. `secret_scan`
post-fix yields 5 rows on egeria-python and makes no case by itself; `dependency_analysis`
produces ~880 rows on amundsen and feeds license/SBOM review, which does. If that is the
trigger, it argues for the generic build from the start rather than a one-off.

---

## Outbox: unbounded enqueue, and no way to cancel queued work

**Both found 2026-09-01**, when a pre-fix Committed Secrets run queued ~48,500
Egeria writes and the console filled with 409s. The 409 itself is fixed (a
duplicate `qualifiedName` now means "already created", not "failed" — see
`egeria_outbox.apply_element`). These two are not.

### 1. One Egeria element per finding, with no cap

`enqueue_annotations` writes one outbox row — and therefore one catalog
element — per annotation. The secret scan produced **48,583 findings in a
single run**, so 48,583 elements were queued for one repo. Two runs of it left
**48,113 rows pending**, and **9,164 had already been written into Egeria**
before the drain was stopped.

The ruleset fix takes egeria-python from 48,581 matches to 7, so this does not
bite today. It is still unbounded: a genuinely noisy repository, or a rule
regression like the one that caused this, floods the catalog with no limit and
no warning. Nothing in the enqueue path knows how large a batch is.

Worth considering together, not separately:

- **A cap with a stated remainder**, the same discipline `_FINDING_LINE_CAP`
  already uses: publish N, and say plainly that M were not published. A
  silently truncated catalog is worse than a capped one.
- **Summary-plus-evidence rather than one element per match.** The
  `AnnotationExtension` linking shipped in Phase 2 is exactly this shape — one
  aggregate annotation with per-item evidence beneath it — and a scan whose
  output is inherently list-shaped is its natural second consumer.
- **A size check before enqueue, not after.** By the time 48,000 rows exist,
  the decision has already been made.

### 2. No purge path for queued work

`registry.purge_outbox_completed()` deletes `done` rows past a retention
window, and deliberately nothing else: *"'dead' rows are the ones a human still
has to look at, and failed/pending rows are live work."* That reasoning holds
for one stuck row and fails for a batch queued in error.

Cancelling the 48,113 rows needed a hand-written `DELETE` against
`egeria_outbox`, reviewed statement-by-statement, with the count printed first
— and that mattered: the first statement matched **39,603 of 48,113**, because
two buggy runs were queued rather than one. Printing the count is the only
reason 8,510 bogus annotations were not left to publish on the next restart.

What is missing is a supported way to say "cancel this batch": a
`cancel_outbox_batch(run_id=...)` or `(entity_slug, before=...)`, which
reports what it matched before doing anything, and marks rows `cancelled`
rather than deleting them so the record of the mistake survives. `run_id`
already exists on the table and is already used by `claim_due_outbox_elements`
— the identifier for "this batch" is there and unused for this purpose.

**Why this is worth building rather than repeating by hand.** Queued work that
turns out to be wrong is not exotic: it is the normal consequence of finding a
bug in something that already ran. Twice in one day (this, and the 48,581-row
render) the answer to unbounded output was a hand-written statement against
live data. That is the part to fix.

---

## `architecture_recovery` answers `not_established` on 37 of 53 repos that have its findings — FIXED 2026-09-09 (re-measured first; the original example was a typo)

Found 2026-09-08 while verifying the new verdict-coverage feature
(`docs/curated-architecture-answers-design.md` §6 item 1) against
`egeria-workspaces_git`: `FactLayer.fact(slug, "architecture_recovery")`
returned `state=never_run` for that repo even though real
`architecture_recovery` findings exist (persisted 2026-09-01, confirmed via
direct registry query — 109 components). `egeria_git`, surveyed more
recently, answers correctly end-to-end
(`"223 components recovered — 0 of 973 components reviewed; ..."`).

Root cause, confirmed: `registry.get_analysis_last_run("repo",
"egeria-workspaces_git")` has no `"architecture_recovery"` key at all — the
same run-attribution gap that made `architecture_diagram` report never-run
for the same repo before the 2026-09-08 fix (`ANALYSIS_KINDS["
architecture_diagram"].results.live_read = True`,
`repo_survey_definition_adapter.py`). `facts.py::fact()` gates non-`live_read`
analyses on `run.get("last_run_at")` before ever calling their results
reader — for a repo whose survey predates (or otherwise never wrote) proper
step-attribution, that gate blocks the answer even though the underlying
data is real and current.

`_architecture_recovery_results`/`_architecture_recovery_headline` read a
`project_analysis_findings` table populated at survey time, exactly the
shape `AnalysisKindResults.live_read`'s own docstring describes (the
`api_structure` 2026-09-02 precedent, and now `architecture_diagram`) — the
fix is very likely the same one-line `live_read=True`. Not applied here: this
is the flagship, most heavily-used analysis in the catalog (many questions'
`analysis_ids` include it), so changing its run-gating behavior deserves its
own verification pass across more than one repo, not a same-session
addition to an unrelated feature. Flagged rather than fixed.

**Re-measured 2026-09-09** at the project owner's request, once the
`architecture_diagram` step-map collision above was fixed and run attribution
was trustworthy again. Two corrections and one confirmation:

**1. The named example was a slug typo, and the bug does not reproduce on it.**
There is no repo `egeria-workspaces_git`. The real slug is
`egeria_workspaces_git` — underscore, not hyphen — and it has no alias under
the hyphen form (`list_aliases` and `project_aliases` are both empty for it).
So the `never_run` observation above was made against a slug that does not
exist, while the "confirmed via direct registry query — 109 components" was
made against the one that does. Two queries, two different slugs, and the
difference between them was written up as a run-attribution bug. On the real
slug today:

    FactLayer.fact("egeria_workspaces_git", "architecture_recovery")
      state    = 'measured'
      headline = '109 components recovered — 23 of 181 components reviewed
                  (21 accepted, 2 rejected); 14 of 39 blueprints reviewed
                  (14 accepted)'

— the same 109 components the entry cites as proof the data was there. It
answers end-to-end and always did.

**2. The class of problem is real, and about three times larger than the entry
claimed.** Swept all 61 live repos: 53 have real `architecture_recovery`
findings rows, and **37 of those 53 have no `architecture_recovery` key in
`get_analysis_last_run()` at all**, so `fact()`'s run-gate returns
`not_established` — 'we have not established this', on repos holding up to
7,758 findings rows apiece (`genaicomps`; also `kafka` 5,523, `genaiexamples`
4,079). Only 16 answer. The mechanism described above is right; the entry
simply picked the one repo where it was not happening.

**3. The collision fix helped, and was not enough.** Counterfactual, by
restoring the pre-fix `step_keys` on `architecture_diagram` in-process and
re-counting: **7 repos keyed before, 16 after** — `4db2cf9` recovered 9. The
other 37 are unaffected by it, so they are a separate cause (surveys whose
step recording predates attribution, per `get_analysis_last_run`'s own
`unattributable` branch), not more of the same.

**What this changes about the proposed fix.** `live_read=True` on
`architecture_recovery` is still the plausible fix, but the case for it is now
a measured 37-repo gap rather than a phantom one, and the verification pass it
deserves has an obvious shape: it must not change the answer for the 16 repos
that already answer correctly, and it must move the other 37 from
`not_established` to `measured` **with headlines that match their findings
rows** — a `live_read` that reports a number nobody can source is a worse
failure than the silence it replaces.

**Applied 2026-09-09, and it needed a precondition the api_structure and
architecture_diagram precedents did not make obvious.** `live_read=True` alone
would have been a bug: `facts.py` reports MEASURED whenever `_has_content()`
finds anything outside the envelope keys, and this reader stamps `slug` and
`documentation` into every payload unconditionally. Measured against a slug
that does not exist: `_has_content` True, headline `None` — so every unknown
repo would have answered "measured" with no label. The reader now returns a
bare `{"_status": NEVER_RUN}` when `surveyed_at` is empty, which is the one
shape `_has_content` exempts.

The gate keys on **`surveyed_at`, not on having components**, and that
distinction is the whole of it: all 8 repos with zero recovery findings still
carry a timestamp, because their steps ran and recorded a run_outcome saying
the repo could not be read. "Unverified — coupling, detect could not read this
repo" is a real answer (README finding 57) and a component-count gate would
report it as never-run. Only a repo nothing has ever touched has no timestamp.

Verified against the three criteria stated above, all 61 repos:

    before : 16 measured / 45 not_established
    after  : 61 measured / 0 not_established

    (1) all 16 already-answering repos unchanged                    0 regressions
    (2) all 53 repos with findings measured, every one with a
        headline sourced from its own rows (egeria_git 18,087
        rows -> "223 components recovered", milvus 12,808 ->
        "79 components", genaicomps 7,758 -> "304 components")      0 empty headlines
    (3) a slug that does not exist                                  state='never_run'

The 8 zero-findings repos moved from `not_established` to `measured` carrying
"Unverified — could not read this repo", which is the honest answer and the one
the run gate was suppressing.

Guards in `tests/test_reader_status_wiring.py::TestLiveReadRequiresAnAbsenceGate`,
derived over every kind with `live_read` set rather than naming this one, so a
future kind that sets the flag without an absence gate fails on arrival. Both
sabotages fire: removing the gate, and replacing it with the plausible-looking
component-count version.

**The measurement lesson, which is the durable part.** Both halves of the
original entry were run correctly; they were run against different slugs, and
nothing in either result said so, because a registry query for a nonexistent
slug returns an empty answer rather than an error. Any claim of the form "X is
missing for repo R" needs R proven to exist in the same session as the query
that found X missing.

## Phoenix: RE traces into its own project — FIXED 2026-09-09

Phoenix buckets spans by project and anything that does not name one lands in
`default`, which is shared. Measured 2026-09-09: this machine's `default` held
106 spans from an unrelated BeeAI **tutorial** run in December 2025
(`OpenMeteoTool`, `DuckDuckGo`, 7 error spans) — and RE's first real span landed
among them. Anyone opening Phoenix cold would reasonably read the tutorial's
token counts as RE's.

`init_phoenix()` now stamps `openinference.project.name` on the TracerProvider's
`Resource` (not per-span, so it cannot be forgotten at a call site), from the new
`PhoenixConfig.project_name`, default `resource-explorer`. Verified live: the
collector went from `['default']` to `['default', 'resource-explorer']`. Nobody's
history is deleted; the two simply stop sharing a bucket.

## "Do we already support these dependencies?" — `dependency_support`, BUILT 2026-09-12

The first of the coverage audit's three gap questions to get an analytic, on
the project owner's direction: Egeria may hold "a starting point that would
still need corroboration and augmentation by people" (2026-09-11), and **"A
now, shaped to seed B"** (2026-09-12) — a curated mapping in RE, each entry
linked to the Egeria technology type it will later promote into.

**The measurement that shaped it.** Before building, dependency names were
matched against Egeria's technology-type catalog directly. 2,894 distinct
names, 213 types: **2** exact matches; **373** by token overlap, nearly all
wrong (`apache_atlas` → Apache Airflow via "apache", `@babel/plugin-proposal-
function-bind` → "Unity Catalog Function", `antlr4-python3-runtime` →
"Runtime Manager API"). Both of Egeria's candidate sources — technology types
and software capabilities — are overwhelmingly **Egeria describing itself**
(OMES, OMVS, connectors, integration groups); roughly ten are external
products. `psycopg2` ↔ PostgreSQL is knowledge, not similarity, and the
premise "Egeria's catalog of already-supported dependencies" does not yet
exist as an artifact. A string matcher would have handed people ~370 false
positives as a "starting point" — worse than nothing.

**What was built.** `configdata/dependency_support.yaml` — 20 technologies,
each with the dependency-name patterns that indicate it and, where one exists,
the Egeria `deployedImplementationType` displayName (verified live). Strict
loader: unknown keys, duplicate names and any pattern under three characters
are errors, because a two-letter pattern is exactly how `apache` matched
Airflow 105 times. `surveyors/dependency_support.py` is the pure matcher plus a
separate Egeria check; `sub_surveyors/dependency_support.py` is the step
(`repo_dependency_support`, Discovery tier — zero repo fetch, reads
`project_dependencies`); the question moves from `human` to **`mixed`**.

**Three states, kept apart on purpose.** A dependency that MATCHED ("this repo
indicates X"); one that matched NOTHING ("no curated technology corresponds to
this name" — almost always "nobody has classified it yet", **never
"unsupported"**); and a technology whose Egeria type COULD NOT BE CHECKED
(Egeria unreachable — not the same as absent). The coverage row carries the
Egeria check state at the top so a reader never infers it from per-row states.
Unmatched names are re-derived at read time against today's mapping rather than
stored, so a stale "unclassified" cannot outlive the curation that classified
it — and so the cross-repo ranked queue needs no table of its own.

**Verified live on `egeria_python_git`:** 32 dependencies → PostgreSQL
(psycopg2-binary), Jupyter, pyegeria, all three present in Egeria (211 types
checked); 29 unclassified, listed. Across the 51 repos with dependency data:
PostgreSQL indicated on 16, Jupyter 10, Redis 10, Docker 9, Kubernetes 9.

**The curation queue is the real output**, and it surfaces a decision the
mapping can express either way but someone has to make: the most-depended-on
unclassified names are `pydantic` (27 repos), `pandas` (23), `requests` (23),
`pytest` (22), `pyyaml` (20) — general-purpose **libraries**, not technologies
in Egeria's `deployedImplementationType` sense. Whether "do we support this
dependency" means Postgres/Kafka or pydantic/pandas is a curation choice.
Clear technology signals next in the queue: `boto3` (12), `openai` (12),
`sqlalchemy` (12), `transformers` (16), `torch` (12).

**The B step this seeds.** Egeria already types `pyegeria` as a
`SoftwareLibrary` `deployedImplementationType` valid value — the natural open
metadata type for a library dependency. Promoting the mapping means creating
those valid values for the entries marked `egeria_technology_type: null` and a
"Supported Technologies" collection over them; the YAML carries everything
needed and nothing has to be redesigned.

**Two faults found on the way, both by running it rather than reading it.**
The results reader first read a `detail` key where `query_findings` hands back
`detail_json` as a string — every technology came back `unchecked` with no
dependencies, and only the live read-back showed it. And the adapter had
**three pre-existing latent `NameError`s**: `log` was used at three STEP-
introspection warning sites and never defined; adding a fourth use surfaced all
four under ruff, and one module-level line fixed them.

## Funnel tier vocabulary — resolved from the catalog, §2 and §4 answered 2026-09-10

**`activity_log.intent` cannot be used to tier a run.** It is stamped at write
time and never revisited, so it holds what the catalog said that day; the
catalog has been retagged twice (rule 17: three analyses `assessment` →
`discovery` on 2026-08-20, `architecture_recovery` `discovery` → `analysis` on
2026-08-30). Measured over 1,217 rows: **174 of the 348 attributable rows — 50%
— disagree with the catalog's current tier**, and reading the column finds **no
`analysis` tier at all** while 11 analyses declare it and it is in fact the
largest tier by rows.

`resource_explorer/tier_resolution.py` resolves instead of reading: an
`analysis_run` row through its `analysis_id`, a `survey` row through step
OWNERSHIP (never `REPO_ANALYSIS_SOURCE_STEPS` — that would credit
`architecture_diagram` for the recovery's steps again). Four states, and the
middle two are the point: `attributed` / `unattributable` (a survey predating
step recording — which analyses ran is unknowable, NOT none) /
`unknown-analysis` / `not-a-run`. `TierCoverage.considered` keeps the
unattributable in the denominator, because dropping them is the difference
between "no repo reached Analysis" and "we cannot say for most of them".

    resolved over the whole log:  attributed 348 · unattributable 265
                                  not-a-run 604 · unknown 0
    rows per CURRENT tier:        analysis 169 · assessment 123
                                  discovery 86 · scouting 70

**The spec's assumed ladder does not exist.** It ranks
scouting/discovery/analysis/**understanding** and excludes **assessment**. No
analysis declares `understanding` — it is canonical per rule 17 but uncosted —
while `assessment` has the **most** analyses of any tier (15). Ranking must come
from the tiers that have analyses; `assessment` and `analysis` are peers on rule
17's own axis (both reason over already-collected data), not a sequence.

**§2 — is it narrowing? No.**

    repos reaching:  scouting 22 · discovery 18 · analysis 25 · assessment 18
    retention:       scouting -> discovery   17 of 22  (77%)
                     discovery -> analysis   18 of 18  (100%)
                     discovery -> assessment 16 of 18  (89%)
    deepest tier:    all 26 attributable repos reach analysis/assessment

100% retention into the deepest tier, and MORE repos reach `analysis` (25) than
`scouting` (22) — the ladder is inverted at the top. **Caveat that limits this
hard:** only 26 repos have any attributable run, against 63 with unattributable
surveys, so this is a small and non-random slice — the repos surveyed recently
enough to have step recording. The honest headline is "the funnel does not
narrow on the repos we can see", not "the funnel does not narrow".

**§4 — where do decisions happen?** 13 terminal transitions against 24
non-terminal (`tracking`/`investigating`/`undecided` — a queue, not decisions).
**8 of the 13 had no attributable run before them at all**; 5 were decided at
analysis/assessment depth. The spec's prediction about rationales is
**confirmed**: `abandoned` 2/3 and `ignored` 1/1 carry a reason, `recommended`
0/4 and `using` 0/5 carry none — negative states prompt, positive ones do not.
13 is too small to conclude more than the shape.

**§1 remains unanswerable** — the `runs` table holds 12 rows, the queue being
~6 days old.

## Source-acquisition accounting — cold vs warm per run, DONE 2026-09-10

The cheap half of the funnel-cost spec's §6. That section asks for *bytes
fetched*; the thing it actually needs bytes FOR — separating "slow because it
downloaded" from "slow because it worked" — is settled by one bit, and
`SourceCache` already knew it.

Rule 17 measured the stakes: acquisition **22.64s cold against 1.28s warm**, one
repo's full route 110.5s → 30s → **14.4s** as caching landed. A tier median that
pools those two populations measures how many of its runs happened to be first,
not the tier. The spec calls caching its single biggest confounder and it is
right.

`SourceCache.hits`/`.misses` have existed since the class was written and
**nothing has ever read them** — and they could not have answered this anyway:
the cache is deliberately shared across `SurveyOrchestrator.run()` calls, so
they are process-wide totals and sampling them either side of a run would race
any concurrent run. `observability/acquisition.py` is a ContextVar scope
attributing each lookup to the run that made it — the same shape as
`llm_usage`, deliberately one pattern rather than two. The shared counters are
kept for debugging the cache itself.

**Three states, and the third is the point.** `cold` (anything missed — one
miss means a real download), `warm` (all hits), and **`not-consulted`** (the run
never touched the cache at all). A database survey does no source acquisition,
and defaulting it to `warm` would file every one of them in the cheap bucket of
a comparison they never entered. `cold` is deliberately "any miss", not "all
misses": a run warm on the zipball and cold on the clone still paid for the
download.

Logged per run beside the token counts — counts as metrics, `source_acquisition`
and `source_kinds_fetched` as params, so a cost query can *filter* cold runs out
of a tier median rather than averaging them in. The run-cost log fires on
`usage.calls or acquired.lookups`: a run that downloaded a large zipball and
made no LLM call is exactly the expensive case §1 cares about, and gating on
tokens alone dropped it.

Verified end-to-end through the real cache: cold 2 misses, warm 2 hits,
SHA-moved 1 hit + 1 miss reading `cold` with `source_kinds_fetched:
[git_clone_root]`, and a no-lookup run reading `not-consulted`.

**Bytes fetched is still not instrumented**, and is now much less urgent: the
confounder it was wanted for is handled. It remains the honest answer if two
tiers ever come out indistinguishable *within* the same acquisition state.

## LLM token accounting — complete() and streaming both counted

Built 2026-09-09 at the project owner's direction, closing half of the
funnel-cost spec's §6 ("the instrumentation that doesn't exist").

**Why not just Phoenix.** Phoenix is running and does capture token counts, but
`BeeAIInstrumentor` instruments BeeAI and nothing else. Measured, same process,
same prompt, same model, Phoenix instrumented for both: a BeeAI
`OllamaChatModel.run()` produced one span carrying `prompt=17 completion=2
total=19`; a `get_llm().complete()` produced **no span at all** (106 spans
before, 106 after). `llm_client` is where the chat path and ten agent call
sites live, and several of those are the agents' `fallback_prompt` path — so
tracing alone would have measured the minority of RE's LLM work, biased toward
runs that succeeded.

`observability/llm_usage.py` accumulates per-scope totals; all three backends
record in `complete()`. Verified end-to-end against real Ollama:
`{'llm_prompt_tokens': 17, 'llm_completion_tokens': 2, 'llm_total_tokens': 19,
'llm_usage_complete': True, 'llm_models': ['llama3.1:8b']}` — matching the
Phoenix span for the same prompt exactly, two independent measurements agreeing.

**Streaming landed 2026-09-10**, in each backend's own shape. Ollama reports the
counts on the final `done` chunk; OpenAI sends them only when the REQUEST passes
`stream_options={"include_usage": True}`, and then in a final chunk whose
`choices` list is **empty** — the previous `chunk.choices[0]` would have raised
IndexError on it, so the loop now tests `chunk.choices` before indexing;
Anthropic reassembles them from `message_start`/`message_delta` via
`get_final_message()`.

**Every `stream()` records from a `finally`, and that is the load-bearing
detail.** A `stream()` is a generator: a consumer that breaks out of the loop
closes it, GeneratorExit is raised *at the yield*, and anything written after
the loop never runs. A trailing `record(...)` would therefore drop the call from
the accounting **entirely** — strictly worse than counting it as uncounted,
because the run then looks like it made fewer LLM calls than it did. With the
`finally`, an abandoned or failed stream still appears, both counts still None,
booked UNCOUNTED.

Verified against live Ollama: a completed stream records
`prompt 21 / completion 10 / total 31, complete=True`; the same stream closed
after one chunk records `1 call, 0 tokens, uncounted 1, complete=False` — the
model id is kept either way, so an abandoned call is still attributable.

So `uncounted` did not become dead weight: it now means "a call we could not
price" — an abandoned stream, a failed one, or a response omitting either half
— rather than "a whole category we have not instrumented".

**Two scopes are open** so the counter is not inert: `run_queue` around the
handler (per-run attribution, what §6 asked for) and `RAGSystem.query` around
its route.

**Persisted to MLflow 2026-09-09** — chosen over the `activity_log` because it
needs no schema migration, is already live with thousands of RE runs, and is
where a cost analysis would look. `log_query(..., usage=)` carries the chat
path; `log_run_usage()` writes a **separate** `<experiment>-runs` experiment,
because pooling runs with chat queries would make "median cost per run" quietly
include every chat message. Token counts go in as **metrics** (they aggregate);
`llm_usage_complete` and the model list go in as **params** (they filter) — a
run whose total is a floor rather than a measurement has to be excludable by
query, or the aggregate silently mixes the two. A cache hit passes `usage=None`
and logs nothing rather than zeros: a free answer does not belong in the same
population as a paid one. Verified end-to-end against the live MLflow:
`llm_prompt_tokens 17, llm_completion_tokens 2, llm_total_tokens 19,
llm_counted_calls 1, llm_uncounted_calls 0`.

Three structural faults found while wiring it, all worth remembering. The
MLflow call was first placed INSIDE the `try` whose `except` marks a run
**failed** — a metrics sink able to report a completed run as crashed. `_track`
must receive `usage` **by value**: it runs on a bare `threading.Thread` and
cannot read the ContextVar itself.

And the caller's own defensive `try/except` around the sink was itself the
defect: it made `execute_run` — which returns a `RunOutcome` — a
broad-except/log-only/value-returning site, which the silent-success ratchet
caught on the next full run (108 → 109). The fix was not to narrow the wrapper
but to delete it: `log_run_usage` now guards its **whole** body, config read and
reachability probe included, so it cannot raise and the call site needs nothing.
**Protection belongs in the sink, not at every call site** — a guard at each
caller multiplies the silent sites instead of removing them. Four tests prove
the sink survives a broken config, an exploding reachability probe and a
malformed usage dict, and one fails if a wrapper is ever re-added.

**The ContextVar trap this ran into.** `RAGSystem.query` hands off to a bare
`threading.Thread`, which does NOT inherit ContextVars (asyncio tasks and
`asyncio.to_thread` do). Reading the scope from inside `_track` would find
nothing and report every query as free, so the read is synchronous in `query`
itself, with a test pinning the ordering and another pinning the trap.

## Running a derived analysis refreshes its source's data but not its source's last-run, and nothing checks freshness first — ATTRIBUTION FIXED 2026-09-09, freshness still open

Raised 2026-09-09 by the project owner, after a `architecture_diagram` Run took
~90s: *"do we check to see if there was a recent architecture survey with the
correct results before we redo that survey from architecture_diagram?"*

**No, and there is no freshness gate anywhere in the dispatch path.**
`resolve_analysis_plan` -> `enqueue_run` -> `SurveyOrchestrator.run(steps)` is
unconditional; the only `force_refresh` in the package is `FileTypeCache`'s and
unrelated. So clicking Run re-executes `repo_arch_detect` + `repo_arch_coupling`
(the pair this Backlog already prices at ~110s, and the reason
`repo_arch_coupling` is the one step routed to Prefect) even when identical
results were produced minutes earlier. `get_analysis_last_run()` already returns
the last run time and status — the information a freshness check needs is in
hand at dispatch and simply never consulted.

**The attribution half is the worse one, and was found by following that
question.** Measured on `egeria_workspaces_git` immediately after such a run:

    architecture_diagram   last_run_at = 2026-09-09T14:08:05   <- the run
    architecture_recovery  last_run_at = 2026-08-30T20:41:46   <- ten days earlier

The run executed the recovery's two steps and wrote fresh recovery data (108
logical / 106 subtree components, published to Egeria), but only the id the user
clicked was credited: an `analysis_run` row records its `analysis_id` directly,
and the step-level attribution in `get_analysis_last_run` applies to `survey`
rows only. So the recovery's card reports data ten days stale that is in fact ten
minutes old. Same class as the `architecture_diagram`/`architecture_recovery`
step-map collision fixed 2026-09-08, arriving from the other direction: there,
ownership was wrong; here, dispatch is right and the credit does not follow it.

Note this is **not** fixed by the `live_read=True` above, which makes the
recovery answer *despite* bad attribution. This entry is about making the
attribution correct, which is the narrower and more honest repair.

Three things, in dependency order, none done:

1. ~~**Credit the source analyses when a derived analysis runs their steps.**~~
   **DONE 2026-09-09.** `repo_analysis_derived_sources()` maps a derived
   analysis to the owners of the steps it dispatches, and
   `get_analysis_last_run` credits them from the same `analysis_run` row. Done
   at READ time, not write time, so it corrects rows already in the log —
   re-measured on `egeria_workspaces_git` immediately afterwards, the recovery
   moved from `2026-08-30` to the diagram run's own timestamp.

   Credited with `last_run_via: "derived"` and `last_run_derived_from: <id>`
   rather than silently as `analysis`: the source was not run directly, and a
   fix that produced a right date under a wrong label would be the same defect
   moved one layer. The card names the borrowed run
   ("its steps were run by architecture_diagram, not by this card"), which
   needed the field carried through `projects.py`'s analyses payload too — the
   frontend branched only on `'survey'`, so `'derived'` had been rendering as
   the empty string.

   A derived credit loses to any newer direct run of the source, and a FAILED
   derived run records `error` rather than claiming the source succeeded.

   Five guards, each sabotaged: no crediting, the wrong label, overwriting a
   newer direct run, an error reported as ok, and an analysis crediting itself.
   That last one was **vacuous when first written** — no catalogue entry both
   owns and derives a key, so it passed with the guard removed; it now
   constructs the case via monkeypatch, with a separate test asserting the real
   catalogue has no such entry.
2. ~~**Consult the freshness that is already known** before dispatching~~ **DONE
   2026-09-10, skip-by-default, user-initiated runs only.** Two decisions from
   the project owner: *skip by default* (not warn-and-run), and *leave the
   scheduler alone* — gating nightly sweeps changes what "nightly" means, which
   is a different question from sparing someone a redundant click, and
   `RunsConfig.gate_user_runs` names that scope.

   `workflows.analysis.assess_freshness()` returns a verdict **and its
   evidence** — three states (`never-run` / `stale` / `fresh`), the age, and
   `via`: the id whose run supplied the freshness. For a derived analysis that
   is its SOURCE, so `architecture_diagram` reports being fresh because
   `architecture_recovery` ran rather than claiming a run it never had. That
   inheritance only works because of the 2026-09-09 attribution fix above.

   Never counts as fresh: a run whose latest attempt **errored** (its data is
   the old data, and pressing Run after a failure must run), a **future**
   timestamp (clock skew would otherwise wedge an analysis into never running
   again), and an unparseable one.

   `POST .../run` answers `{"status": "skipped", "reason": "already-fresh",
   "detail": ...}` with null ids, and `?force=true` always runs. All **three**
   frontend callers handle it — the card grid, the chat answer button and the
   gap-analysis button — each offering "Run anyway". Two of those three share a
   byte-identical fetch block, and the first attempt patched one of the pair;
   the guard is derived over every function that polls `activity_id`, not a
   list.

   Original item, for the record:
   **Consult the freshness that is already known** before dispatching — skip, or
   warn with the age, when the source data is newer than a threshold. Whether
   the default is skip-with-override or warn-and-run is a product decision;
   silently re-running for 90s is the one option that is clearly wrong.

   **Measured 2026-09-10, which turns the threshold from a guess into a
   reading.** Over the 1,207 successful runs in `activity_log`, grouped per
   (repo, analysis):

       within  5 min of an identical prior run:  250 runs  (20.7%)
       within  1 hour:                           330       (27.3%)
       within  6 hours:                          347       (28.7%)
       within 24 hours:                          418       (34.6%)
       within  7 days:                           669       (55.4%)

   The 1h→6h step is +1.4pp — a flat region separating burst duplication from
   the legitimate daily cadence, so a threshold anywhere in it behaves much the
   same. **1 hour** is the suggested global default, overridable per analysis;
   per-tier thresholds are the right long-term answer but need §5's rot
   measurement, which needs material-difference detection that does not exist.

   **And a trap worth recording, because the first reading said the opposite.**
   Comparing consecutive findings sets by hash gave a *change* rate of 27% at
   <5min against 12% at 5min–1h — i.e. findings apparently rotting faster in
   five minutes than in an hour, which cannot be true. 75 of those 92 "changes"
   (82%) were one logical run seen as two: the architecture pipeline writes
   `architecture_recovery`, `architecture_decisions`, `architecture_blueprints`,
   `architecture_interfaces` and `architecture_diagram` from
   `arch_recovery/persist.py` (both `repo_arch_detect` and `repo_arch_coupling`
   call it) plus `architecture_summary` from `sub_surveyors/arch_summary.py`,
   each at its own `surveyed_at`. A first attempt to control for this checked
   `REPO_ANALYSIS_STEP_MAP` for analyses owning >1 step — and missed all of them,
   because those are **finding kinds, not analysis ids**. Corrected, the real
   <5min change rate is **~5%**, below the 12–13% at longer intervals, which is
   the direction that makes sense.

   So: **a fifth of all runs are near-duplicates and ~95% of them produce
   nothing new.** Anyone re-deriving this must group by the pipeline that wrote
   the findings, not by `kind`, or they will measure the step boundary instead
   of the rot.
3. **Separate "view" from "refresh" on derived cards.** `architecture_diagram`
   is `live_read`, so its Results tab already renders with no run at all — the
   Run button offers a 90-second refresh where the reader wanted a picture. At
   minimum the button should state what it will actually execute and roughly
   what it costs.

## RE needs an admin/ingestion dashboard — EA has one, RE has none

Raised 2026-09-08 by the project owner, checking why EA's admin panel
(`/admin`, `admin.py`) doesn't show RE's repos. It can't: RE's only
admin-shaped page is `admin-feedback.html`, scoped narrowly to feedback
triage. `/api/admin/repair` (`repair.py`) manages repo *metadata* (rename,
GitHub URL, collection enable/disable, drift) — nothing about triggering or
watching a survey run.

EA's dashboard (status table for 10 vector collections + 5 source repos,
per-collection reindex, per-repo git pull, a job list with live output,
one-click maintenance actions) isn't directly portable, because RE's
ingestion model is a different shape entirely: EA clones a repo once and
periodically re-vectorizes it into pgvector; RE surveys a GitHub repo
on demand (`resource-explorer survey <repo> --publish`, or via
`survey_definition_reader`/`egeria_publisher`) and writes results straight
into Egeria as Survey/Investigation elements — there's no local clone+reindex
cycle to expose a "pull" button for. A useful RE equivalent would need its
own shape: something closer to "which repos have ever been surveyed, when,
by which Survey Definition, with what outcome" plus a way to trigger a new
survey and watch it run (RE's `run_queue.py` + job polling already exists for
this — `/api/runs` — a dashboard could sit on top of it rather than needing
new backend plumbing the way EA's admin.py's job-tracking does).

Not designed or scoped further than this. Whoever picks it up should start
from `run_queue.py`'s existing job model and `admin.py`'s UI shape as
reference, not treat this as "copy admin.html."

## `architecture_diagram` collides with `architecture_recovery`'s steps, and has no dashboard — FIXED 2026-09-08

Two tests have been failing since `b3b0500` added the read-time
`architecture_diagram` AnalysisKind (2026-09-08), and both became easier to
notice on 2026-09-08 when the kind finally reached the UI (`e20dd93` added its
`_REPO_RESULTS_RENDER_MODE` entry and renderer — until then the analysis had a
working results reader and no way to see it).

  * `test_run_publish_honesty.py::TestRunAttribution::
    test_step_keys_map_to_exactly_one_analysis` —
    *"step repo_arch_detect claimed by architecture_recovery and
    architecture_diagram"*. `REPO_ANALYSIS_STEP_MAP` is supposed to PARTITION
    the step keys; both kinds declare `["repo_arch_detect",
    "repo_arch_coupling"]`, so run attribution for those two steps is now
    ambiguous by construction.
  * `test_survey_results_routes.py::TestSurveyResultDashboardsRegistry::
    test_every_findings_producing_analysis_has_a_dashboard` —
    *"analyses with no Results dashboard: ['architecture_diagram']"*. The card
    renders from the Analysis tab and is absent from the Survey Results
    dashboard that groups these.

These are not independent. The kind's own docstring is explicit that the
diagram is "a rendered VIEW of the recovery, not the recovery's own evidence",
and gives it a separate id precisely so a question can ask for the picture
without pulling the full component list. That is a good reason for a separate
AnalysisKind and NOT a reason for it to claim the same steps: it runs no steps
of its own (hence `live_read=True`), so the honest shape is probably an empty
step list plus a dashboard entry, rather than borrowing `architecture_recovery`'s.

**Fixed the same day, and the collision was worse than the test says.**

`ProjectRegistry._step_key_to_analysis_id` inverts the step map with a dict
comprehension, so the LAST analysis declaring a key wins — and
`architecture_diagram` is the final entry in `ANALYSIS_KINDS`. Both recovery
steps therefore resolved to the diagram, and every survey-derived run of them
was credited to the picture rather than to the recovery that did the work.
Confirmed by reading the live inverse map, not inferred from the source.

Worse, the two attribution paths disagreed with each other:
`egeria_annotation_materializer._analysis_for` loops and returns the FIRST
match, so a run and the annotations that run produced were being filed under
different analyses.

The fix separates the two questions the one field was answering.
`AnalysisKind.step_keys` now means only "whose run was that" and must still
partition; a new `derives_from` means "what do I run to refresh this", and
`REPO_ANALYSIS_SOURCE_STEPS` is its map. `architecture_diagram` owns nothing
and derives from the recovery's two steps. The catalog entry's argument for
sharing them (`_persist_diagram` ran inside those steps) was true when written
and had stopped being true hours earlier, when the diagram moved to read-time
rendering — that comment is corrected in place rather than left to mislead the
next reader.

Everything asking "what should I execute" moved to the source map: the Run
button (`web/routes/projects.py`), the scheduler's single and coalesced
dispatch, `analysis_cost`, and the fact layer's "run this next" hint. Missing
any one of those would have been silent in its own way — a 400 on the button,
a schedule that comes due and does nothing, the most expensive analysis in the
catalog priced at ("none", "low") and recommended daily.

`architecture_diagram` also joined the `architecture_overview` dashboard,
first of its four: the picture states the relations the other three describe
in fields.

Guarded by four new tests in `test_run_publish_honesty.py`, each verified
against the un-fixed code — including one that drives `_run_repo_survey` and
asserts which steps reach the orchestrator, rather than reading the scheduler's
source for the right symbol.

**Related, and NOT resolved by this:** the entry above about
`architecture_recovery` reporting never-run for `egeria-workspaces_git`
proposes `live_read=True` as its fix. That repo has no attribution for ANY
architecture analysis — the diagram included — so this collision is not its
cause, and the diagnosis there stands. But the collision would have made
`live_read=True` look like it worked for the wrong reason on repos that DO have
survey attribution, so re-measure that entry now the ownership is correct
before acting on it.

## The silent-success ratchet is red on one site — FIXED 2026-09-08 by `a254f29`, and better than this entry advised

`tests/test_no_silent_success.py` has been failing since 2026-09-08. Three new
sites appeared; two were fixed the same day (`egeria_identity.py::
_platform_name` and `investigation_reclassifier.py::_move_kind_classification`
— in both cases the handler already refused, but refused with words that
asserted more than had been measured). The third is still open:

    resource_explorer/context_compile.py::compile_context

introduced by `3bed7b4` ("every compile gets a content-addressed id, is
persisted, and turns and feedback link to it"). It wraps the `record_compile`
bookkeeping write, and its comment states the intent plainly: *"Persistence is
an instrument, not the product: a compile the caller can use must never be
lost to a failed bookkeeping write."*

This entry originally recommended option (3) in the test's own remediation
list — record it in `tests/no_silent_success_baseline.json` as a reviewed,
genuinely best-effort site — on the grounds that the comment's reasoning holds
and the caller must not lose a usable compile to a failed bookkeeping write.

**`a254f29` took option (1) instead, and it is the better answer.** The compile
is still returned, so nothing regressed for the caller; what changed is that
the manifest now carries `recorded: False` and a note saying feedback citing
this `compile_id` will not resolve to a stored manifest. The reasoning the
comment gave was sound about the RETURN VALUE and did not follow for the
manifest: "must not lose the compile" is not the same claim as "must not
mention that we failed to file it", and a baseline entry would have frozen the
weaker reading in a reviewed artifact.

Worth keeping as a record of the near-miss: the argument for the baseline was
made from the handler's own comment, which described the intent accurately and
was never evidence about what the manifest could afford to say. A comment
stating why a thing is deliberate is not a measurement of what the alternatives
cost.

The ratchet is green at 108.

## The architecture diagram is drawn by a path no test exercises end-to-end — CLOSED 2026-09-09, verified live

`_renderArchitectureDiagramResults` emits a placeholder and
`renderPendingArchDiagrams()` POSTs the Mermaid source to
`/api/diagrams/mermaid` (the Kroki proxy) to fill it in. As of `e20dd93` the
**failure** path is verified live in a browser (the server's own message is
shown and the card is left retryable) and the success path is verified only
against a stubbed fetch: that route requires a session, so a signed-out session
cannot drive it, and entering credentials is out of scope.

~~Someone signed in should open an `architecture_diagram` card once and confirm a
picture appears.~~ **Done 2026-09-09**: the project owner, signed in on the
:8810 server, confirmed the diagram materializes. The success path is now
verified end-to-end by the only route that could verify it — a real session
against the real proxy — and this entry is closed.

Worth recording why it stayed open for a day rather than being assumed: the DB
ER-diagram view uses the same proxy and was evidence that the proxy works, not
that this card reaches it. The two are one `fetch` apart and the stub could not
tell them apart.

Noticed in passing while building it, and NOT changed: `_renderEmptyResultState`
maps `never_run` to the generic *"No results yet — click Run to scan."* and
discards the reader's own `st.hint`. That is the documented behaviour
(`result_status.py`: `never_run -> the original message`) and it is true for
this kind, whose Run does trigger `repo_arch_detect`/`repo_arch_coupling`. But
every other status branch shows the hint, and a reader that took the trouble to
write one ("No architecture diagram yet — run the analysis.") has it dropped.
Changing it touches every kind's empty state, so it is a deliberate call for
the presentation session, not a side effect of adding one card.


## The architecture diagram cannot be rendered by this Kroki — REOPENS "drawn by a path no test exercises end-to-end"

Measured 2026-09-10 against the live `egeria-shared-kroki` on :6002. The
**exact payload the shipping UI POSTs** — `renderPendingArchDiagrams()`'s
`%%{init: …}%%` prefix plus the stored `mermaid` source — returns
`400 Internal Server Error`. Not a slow render, not a large-diagram limit:
a refusal.

There are **three independent causes, each sufficient on its own**, and each
was isolated by bisecting down to a two-node diagram rather than inferred:

| cause | evidence |
|---|---|
| Any line beginning `%%` | `flowchart TD / A[a] --> B[b]` renders (200). The same two lines with `%%{init: {"theme":"dark"}}%%` prefixed, or even a plain `%% comment`, return 400. So the UI's own theming directive breaks every render it is applied to. |
| A literal `%` in a node label | `A["x 40% y"]` alone returns 400. Escape it as **`&percnt;`** — `&#37;` is also accepted (200) but comes back rendered as `40&%`, and the status code cannot tell the two apart. The difference is only visible by reading the text nodes of the returned SVG. |
| A `class` directive naming more than 20 nodes | 20 renders, 21 does not, deterministically, whether on one line or split across several. A real diagram styles 53. |

Cause 2 is **unconditional in the generator**:
`surveyors/arch_recovery/mermaid.py:160` is `conf = f"{c.confidence}%"`, with
no branch — so every component node of every diagram carries a literal `%`.
Confirmed against stored data, not only read off the source: `milvus` (10
literal `%`), `sqlglot` (3) and `marquez` (7) each return 400 from their real
diagram. There is no repo for which this source renders.

Cause 3 is narrower but independent: across the 53 repos holding a stored
diagram, **7 also exceed the 20-node `class` ceiling** — `openmetadata` styles
63. Those seven would still fail after cause 2 is fixed.

*(A first pass at that survey reported "0 repos affected by `%`", which was a
bug in the survey: the regex used a `(?<![&#\d])` lookbehind, and every real
occurrence is `40%` — a digit immediately before the `%`, so the instrument
excluded exactly the case it was looking for. Recorded because the wrong
number looked entirely reasonable and agreed with no other evidence.)*

**This reopens the entry above** ("CLOSED 2026-09-09, verified live"), which
records the project owner confirming on :8810 that the diagram materialises.
That verification and today's measurement cannot both describe the same
system, and this entry does not guess which changed — the Kroki containers
have been up three days, spanning both. What is certain is that the shipping
UI's diagram does not render **today**, by direct measurement of its own
payload, and the closed entry's conclusion should not be relied on until
someone signed in re-checks it.

**Where the fix belongs: the generator, not the caller.** A renderer that
refuses `%` is a constraint on what may be emitted, and emitting the
confidence as `40%` when `&#37;` renders identically is a free change.
The `%%{init}%%` theming is the UI's, and has to move to styling the returned
SVG — `/next` does this in `static/next/worklist.js`'s `themeSvgElement()`,
scoped to the SVG's own id, and the two traps found doing it are worth
copying: an SVG `<style>` is not scoped (it restyles the whole document), and
prefixing a comma-separated selector list only scopes the FIRST selector.

Worked around at the render boundary in `/next` (`mermaidForKroki()`), which
strips `%%` lines, escapes `%`, and caps the class assignments while naming
how many node styles it dropped. That is a workaround in one consumer, not a
fix — the second consumer will hit all three again.

## `exceeds_renderer_limit` reports the opposite of the truth

`architecture_diagram`'s fact value carries `exceeds_renderer_limit: false`
for a diagram that no renderer available here will accept — measured on
`egeria_workspaces_git`, 9,384 characters, `false`, and a hard 400 from
Kroki.

Whatever that flag measures, it is not "will this render", which is what its
name promises and what a caller will read it as. A guard that is confidently
wrong in the safe-looking direction is worse than no guard: a UI that trusts
it will not offer the fallback it would otherwise have offered.

Either the flag should mean what it says — checked against the renderer's
actual constraints, which per the entry above are `%%` lines, literal `%`,
and a 20-node ceiling on `class` — or it should be renamed to whatever it
does measure (a character count against some other bound) so nobody reads it
as a rendering guarantee.

### Auto-publish is 98% of an "inline" analysis's wall time (measured 2026-09-13)

Profiling why `language_file_classification` (scouting, `run_time: fast`) took a 156 s median over
5 queue runs: its three steps take 0.25 s. The rest is `run_analysis`'s synchronous auto-publish —
`EgeriaPublisher._create_annotations` enqueues outbox rows and then calls `drain_outbox` inline, one
Egeria REST call per row, sequential. One real run measured through the worker's own path:
**559 s total; steps 0.25 s; publish setup 11.3 s; 53 writes (46 annotations + 7 evidence links)
546.6 s at a median of 9.4 s per write** (p90 15.3 s, max 21.2 s, no failures). The scheduler already
drains the same outbox every cycle. Evidence: `scratchpad/lfc-profile/REPORT.md`,
`scratchpad/lfc-publish/REPORT.md` (timings.json) in the 2026-09-12 measuring session.

Two things follow, and one decision:

- Every "measured" cost in `docs/funnel-cost-measured.md` §1 is publish latency (corrected there).
  Per-phase timings (`steps_seconds` / `publish_seconds` / `publish_mode`) on the run's activity
  detail are on branch `re/auto-publish-enqueue-only` so the next measurement can split them.
- **9.4 s per annotation create is a platform number.** **Decision context (project owner,
  2026-09-13):** the dev Egeria platform is deliberately running an old codebase, kept so the
  compiled-vs-RAG experiments share a common baseline; the slow writes are attributed to that, and a
  redeploy to the current codebase is what picks up the fix — timed against the experiment schedule,
  not against this finding. The Survey Definition documents re-authored the same night ran at ~5 s
  per Dr.Egeria command on the same platform, consistent with that. **Re-measured after the redeploy
  (2026-09-13, same repo, same analysis, same script):** during startup 5.2 s median / 6.8 p90 / 10.1
  max (run 305 s); settled 30 min later **3.6 s median / 4.3 p90 / 4.6 max, run 197 s**; +10 h later,
  the morning re-measurement, **1.5 s median / 2.3 p90 / 3.0 max per write, run 92.7 s, steps 0.06 s**
  — the tail is gone, the floor keeps dropping, and steps stays flat at essentially zero regardless of
  the platform's write latency (per-phase timings, #67 — see `docs/funnel-cost-measured.md` §1). The
  inline-vs-enqueue decision therefore stands: even at 1.5 s/write, 53 writes is 80 s on a 0.06 s
  analysis.
- **Decision needed (project owner):** should an inline analysis wait for its publish? Enqueue-only
  makes the same run ~15 s, with `published` becoming a third state — *queued for publish*, visible in
  Egeria within the next drain (≤ 15 min). Built behind `RunsConfig.publish_inline` (env
  `RUNS_PUBLISH_INLINE`), default `True` = today's behaviour, on the same branch. The designer
  (FUNNEL-COST-STATUS, 2026-09-13) asked for the state vocabulary to gain *queued for publish* if it
  flips — a fact about the mechanism that must not read as a fact about the catalog.

### Question-GUID lookup fails on pool threads — pyegeria cross-loop bug (ISSUE-96 drafted)

`SurveyDefinitionReader._lookup_question_guid` shares one pyegeria client across `run_sync` pool
threads. pyegeria imports `nest_asyncio` at import time, so each thread silently gets its own event
loop; the shared `httpx.AsyncClient`'s pool lock binds to the first loop and every other thread gets
`RuntimeError: … bound to a different event loop`, which pyegeria's broad handler relabels
`CLIENT_ERROR_400 / status code ''`. Measured 2026-09-13 (52 names × 2): shared client on pool
threads **100 %** failure; main thread 0 %; a fresh client per worker thread **0 %**. pyegeria's own
`mcp_server.py` (6.1.10) documents the same failure and uses a per-call client. Consequence until
fixed: `resolve_question_guid` caches the None for its TTL and the scoped question→definition lookup
silently falls back to the full scan (~20 s vs ~0.2 s). Mitigation (client per thread, no pyegeria
patch) on branch `re/question-guid-client-per-thread`. The pyegeria issue text is drafted in
`scratchpad/qguid-flake/REPORT.md` as ISSUE-96 for `localGit/egeria-python/PYEGERIA_ISSUES.md` —
not filed; filing is the owner's call per the pyegeria-gaps rule.

### Superseded Question term — unlinked and deleted 2026-09-13; the reconciler gap it exposed stays open

"What is its internal architecture — what components exist and how do they relate?" was split into
four questions in the CSV (2026-09-08); the four were created 2026-09-12. The old term stayed on the
platform with stale `ScopedBy` links from two live definitions. **Decision (project owner,
2026-09-13):** unlink, then delete. Done: `ClassificationExplorer.clear_scope_from_element(term,
process)` once per definition (RepoFullSurvey 33 → 32 scopes, RepoArchitectureDiscovery 1 → 0 — every
other scope link verified intact), then `GlossaryManager.delete_term(term, cascade=True)`, which took
the term's own perspective/stage links with it (the three Perspectives and the Discovery stage term
verified present afterwards). A read-immediately-after-delete lookup still "resolved" the term; a
fresh lookup a minute later did not — index lag, not a failed delete.

Two things the enumeration taught, kept here so the next deletion is faster:

- A Question term's `relevantToScopes` carry its OWN links — `Link Perspective to Question` and the
  funnel-stage term, generated per question by scouting-questions.md — and go with the term. Only
  `scopedElements` (where the term is the scope of something else) need a hand unlink. The first
  agent's stop-on-anything-unexpected brief read those four as foreign references and halted;
  the distinction is direction, not count.
- `RepoArchitectureDiscovery` had **only** the dead term as a scope, because the 2026-09-12
  re-authoring ran the discovery and full documents and not this one. Repaired the same day from its
  own document's scope blocks (`docs/dr-egeria/questions/arch-discovery-scope-links-2026-09-13.md`,
  0 → 2). Verified through the app's own scoped lookup.

**Still open — the reconciler does not see scope links.** `scripts/reconcile_survey_definition_links.py`
compares step edges to STEP_REGISTRY order; nothing compares a definition's `ScopedBy` links to the
Question terms its authored document names. This is the second silent scope drift (2026-08-19 was the
first, when the questions batch was absent and every scope link silently created nothing). A sibling
pass — for each authored survey-definition document, the set of `Scope Reference` names vs the live
`get_scopes(process)` set, reporting missing and extra — would have caught both. Read-only report
first; the extra-link removal is a deliberate second step, since `clear_scope_from_element` is the
only write it would ever need.

### Question term descriptions on the platform now match the CSV (2026-09-13)

19 of 52 terms had Description/Usage text from an older CSV. No "Update Term" command exists;
`Create Glossary Term` is a verified upsert (tested on a throwaway term first — same GUID, fields
updated in place). `docs/dr-egeria/questions/update-questions-2026-09-13.md` holds the 19 blocks,
executed once (19/19), all 52 verified matching afterwards, GUIDs unchanged. Contains no Link
commands, so it cannot duplicate anything; not in `_batch.json`.

### Catalogue in layers — and two Egeria-type corrections (2026-09-14)

**Decision (project owner, 2026-09-14):** catalogue a repository in layers. Layer 1: the top-level
components it delivers (for egeria-trellis: RE, EA, Trellis core — coarse). Layer 2, only when
someone wants to understand one of those more: its next level (RE-Web, CLI, agents, the finer
shared Trellis components RE uses). No finer unless a specific need arrives. Expose the interfaces
RE provides and its interactions with the Egeria platform through pyegeria and Dr.Egeria. Full
type mapping in the design project: `CATALOGUE-IN-LAYERS.md`.

Two things the current model gets wrong, both the same misreading of 0056 Resource Managers:

- **`SoftwareLibrary` per Python package is a type error.** `curate_plan.py` proposes every
  distribution in a workspace as `SoftwareLibrary`; in Egeria that classification means *a server
  managing distribution of software modules for deployment* (PyPI, Nexus) — the thing that manages
  libraries, not a library. Layer 1 is `SoftwareCapability` classified `Application`, proposed from
  deployment evidence (console entry point, Dockerfile, compose service); importable-only packages
  are not capabilities — component assets at layer 2 if catalogued, *probably not catalogued* by
  default. Fix: the evidence classifier below + the Curate rows (/next). **Fixed 2026-09-14**:
  `curate_plan.py`'s "what it is" column now reads the `deployment_evidence` findings below —
  `application` proposes `SoftwareCapability::<name>`, `library` proposes an unticked, layer-2
  `SoftwareComponentCandidate::<name>` ("probably not catalogued" by default) — rather than
  labelling every distribution `SoftwareLibrary`. Not yet committed to Egeria either way: the
  `components` curate-commit step is still `skipped` (`workflows/curate_commit.py`), so this was a
  UI-only proposal never actually published — nothing to retract.
- **Repository-as-`SourceControlLibrary` is the same misreading one level up.** The publisher
  creates one `SourceControlLibrary` per repository (`egeria_publisher.py`, since August); the
  classification names the *service* (GitHub), and the repository is what the service manages.
  Load-bearing: every published SurveyReport and annotation hangs off those elements, and the
  resync/outbox key on their qualifiedNames. **Schedule, do not hot-fix**: needs a migration plan
  (one `SourceControlLibrary` for GitHub; repositories as the assets it manages via
  `CapabilityAssetUse`; existing elements re-parented or re-typed) and a decision on whether old
  SurveyReports move. Not blocking the layers work — layer 1/2 elements attach beneath whatever the
  repository element is. **Decision (project owner, 2026-09-14):** this is a dev environment —
  rather than a migration/retraction of already-published elements, wipe and redeploy Egeria fresh
  once the publish code is corrected. Drops the "existing elements re-parented or re-typed" and
  "old SurveyReports move" questions entirely; the one thing that still must land *before* the
  redeploy is the publisher fix itself (`egeria_publisher.py`), or the fresh database is
  repopulated with the same wrong structure. Not yet fixed — still open.

Two Discovery-tier, zero-fetch analyses to build (measuring session): `deployment_evidence` —
which distributions carry a console entry point / Dockerfile / compose service, hence which are
applications; and `egeria_interfaces` — which pyegeria client classes (→ Egeria view services) and
which Dr.Egeria command families a repository uses, so RE's interactions with the platform are
relationships to elements the platform already catalogues, never new ones. Layer 2 promotion is
the accepted architecture-recovery verdicts — the component column and wire diagram (designer's
open item) are the layer-2 act. Depth is a decision: the catalogue pane offers layer 2 the way the
DepthOffer pane offers deeper surveys, recorded on the catalogue record.

### Open /next items after the stage-page and layers rounds (2026-09-14)

One place to find everything still open across the two most recent rounds, so nothing gets lost
between sessions. Not new work — a consolidation of items already named in `REPLY-CATALOGUE-IN-
LAYERS.md`, `SPEC-THE-STAGE-PAGE.md`, and `REPLY-PORTS-SCARCITY-CORRECTED.md`.

**Stage-page round (`#93`/`#90` shipped the spine; these are the deferred follow-ups):**
- Survey & analyses' *analyses* listing — rows, description popovers (stage, declared run time,
  availability, perspectives, ruleset link), `serves` breakdown. Backend (`analyses-index`, `#90`)
  is live; the frontend row/popover was never built, only the *definitions* half (fetch-step
  counts) shipped.
- Points 6/7/4's clauses: the "other stages" fallback must say when the stage filter failed to
  resolve rather than silently showing every definition; perspectives need to be sent/stated
  consistently across every pane (Survey sends, `By analysis` says "not filtered", nothing-held
  must read as "no perspective held" rather than an indistinguishable `0 of 12`); the health radar
  chart can come back once it obeys "a visual may only show a number the row beneath it also
  shows, from the same value, rounded the same way".
- Findings-per-question and cross-analysis disagreements still live only on `By analysis` —
  deliberately not folded into the Questions row's "the numbers behind this" disclosure (`#93`);
  judged a bigger, riskier merge deserving its own review pass.

**Component-review round (`#85`/`#86` shipped ports-as-a-column, sort, the diagram; this is open):**
- `detect`/`coupling` (round two, item 1) — two perspectives propose different component sets, and
  a verdict keyed by scope lands on both, so an accepted component under one proposal reads as
  accepted under a proposal its curator never saw. Waiting on a SPEC from the designer, not on
  engineering capacity.

**Catalogue-in-layers round (`REPLY-CATALOGUE-IN-LAYERS.md`, 2026-09-14):**
- `SoftwareLibrary`/package type error — **fixed**, this session (see the entry above).
- `SourceControlLibrary`-per-repo type error — **fixed**, same day (`d514c884`, 16:33, 35 minutes
  after this line was written at 15:58 — the line was never flipped when the fix landed). One
  singleton `SourceControlLibrary` for GitHub (`_find_or_create_github_scl`, cached), each
  repository its own `Asset` linked via `CapabilityAssetUse` (`egeria_publisher.py:702-917`).
  Verified live in the code 2026-09-21, ahead of that night's Egeria redeploy.
- Bulk-accept dialog copy — must not name `DeployedSoftwareComponent` (or any component-family
  type) before it is verified; say "software components" in plain words instead until pinned.
- The layer-2 catalogue-depth offer — `DepthOffer`'s three rules verbatim (not a nag: once per
  catalogue record, in the pane, never a modal; not a gate: layer 1 is already recorded when it
  appears; not a scold: states a fact about the record, not an instruction), with a real measured
  price (Egeria writes: 1.5s median, p90 2.3s, post-redeploy) rather than DepthOffer's own "not yet
  measured" placeholder. Outcome (accepted/declined/chose) recorded on the catalogue record, same
  as a depth-offer decline.

### `egeria_host` defaults to `database.host`, silently reproducing OPEN-SURVEY-0009 for the next database anyone registers

Found 2026-09-21, flagged by a peer while catalogueing coco_pharma for the
first time as part of the multi-resource plan's probe 9. The
`localhost` → `host.docker.internal` fix for `coco_ods`/`coco_pharma`
(Backlog.md, "catalog_and_survey never refreshes...") was applied as a
direct SQL correction to those two registry rows' `egeria_host` column, not
to the registration code path. `web/routes/databases.py:603`:

```python
egeria_host = database.egeria_host or database.host
```

`egeria_host` defaults to `""` (`registry.py:107`), so any database
registered without an explicit `egeria_host` falls back to `database.host`
— typically `localhost`, since that is how RE's own bare-host process
reaches a Docker-hosted Postgres. Egeria's engine host runs inside Docker
and cannot reach the RE host's `localhost`; the result is the identical
`OPEN-SURVEY-0009 ... has no connection` failure, silently, for the next
person who registers a database and doesn't think to pass `--egeria-host
host.docker.internal` explicitly.

**Not fixed here** — this is a repair keyed on the damage (two rows patched)
rather than the cause (the registration default), so the fix survivors are
invisible until the next new registration hits it. The real fix is a
project-owner decision on what the right default actually is (a config
value, a documented required field at registration time, or an explicit
`--egeria-host` prompt) — filed rather than guessed.

### Native Postgres survey fails with SCRAM auth error even on a freshly-catalogued asset with a real connection — second distinct connection-shaped failure, same secrets architecture

Found 2026-09-21, triggering a native `survey-postgres-database` engine action
against `coco_pharma`'s freshly-catalogued asset (guid
`4dd8d5ee-eb5a-4fe3-8a52-7f304043a749`, catalogued via the fixed
`deepCopy=True` path — PR #181/earlier work, so this asset genuinely has a
`Connection`, unlike `coco_ods`'s broken existing asset). The engine action
reached `final_status: FAILED` (not `INVALID` — a different terminal state
than `coco_ods`'s `OPEN-SURVEY-0009`), with:

> `OPEN-SURVEY-500-001 Unexpected exception in survey action service
> postgres-database-survey-service of type
> com.zaxxer.hikari.pool.HikariPool$PoolInitializationException detected by
> method start. The error message was Failed to initialize pool: The server
> requested SCRAM-based authentication, but no password was provided.`

**This is a different failure than `OPEN-SURVEY-0009`** — that one meant "no
connection at all"; this one means a `Connection` exists and Egeria's engine
found it, but the password it resolved (or tried to resolve) was empty. This
matches exactly the suspicion raised during the earlier `catalog_and_survey`
investigation
(`CATALOG-AND-SURVEY-REFRESH-FIX.md`): the template's attached `Connection`
is a `VirtualConnection` embedding a `SecretsStoreConnection` (a YAML-file
secrets-store connector), not a plain `Connection` with an inline
`userId`/`password`. `deepCopy=True` correctly instantiates that subgraph
structurally, but nothing in RE's registration path populates whatever the
secrets-store connector actually reads from — the password never reaches
the pool.

**Not investigated further here** — this needs someone who knows how this
deployment's Egeria engine host resolves a `SecretsStoreConnection` (a YAML
file path on the engine host's filesystem, presumably) to say what RE would
need to write, and where. Two live databases (`coco_ods`, `coco_pharma`) now
have two different connection-shaped native-survey failures, both tracing
back to the same underlying secrets architecture — this is the actual
blocker for probe 9 (a genuine native annotation-type/metric-key dump) and
for Phase 1 more broadly, not a one-off.

**Root-caused and fixed for fresh catalog runs, same day (PR #185,
`docs/design-notes/PROBES-2026-09-21.md`):** the templated
`SecretsStoreConnection`'s `secretsCollectionName`/`secretsStorePathName`
configuration properties were themselves left as Egeria's own literal,
unsubstituted placeholder text — nothing had ever supplied real values.
`EgeriaDatabaseSurveyor` now finds-or-creates its own Egeria secrets store
(the documented client-side-secret pattern, project owner decision
2026-09-21: Egeria does not share secrets across clients) and binds both
placeholders at catalog time. **`coco_ods`/`coco_pharma` themselves are still
broken** — both already exist by qualifiedName, so they stay on the reuse
path forever and never get the new placeholders; fixing either needs the
delete-and-recatalog GAP process, a separate deliberately-deferred decision.

### `materialize_database_report`/`materialize_filesystem_report` don't distinguish an engine-action failure from a genuine empty result — not yet wired to a live caller

Found 2026-09-21 while live-verifying Stream 3's structured-tables back-fill
(`COORDINATOR-BRIEF-MULTI-RESOURCE.md`). A design review raised the concern
that the coco_ods back-filled row (`table_count=0`, `state='measured'`) might
be confidently-wrong data from the known `OPEN-SURVEY-0009` connection
failure rather than a real absence. **Checked directly, not guessed**: a
live, read-only `psycopg2` query against `coco_ods` confirms it genuinely has
zero tables outside `pg_catalog`/`information_schema` right now — the
back-filled row is correct, and no data correction was needed.

The concern is still real for a different, forward-looking reason.
`surveyors/result_materializer.py`'s `materialize_database_report`/
`materialize_filesystem_report` take `annotations: list[dict]` — already
resolved poll output — and correctly record an honest ambiguity note via
`database_survey_coverage`'s `coverage_detail` when a section comes back
empty (citing Egeria's own Postgres connector docs: missing may mean
"permission", not "none exist"). But this conflates two different things
into one ambiguity note: "the engine action completed and genuinely found
nothing" and "the engine action itself failed (`final_status` != a success
status, e.g. `INVALID`)" — the second is a stronger, more specific signal
than the first, and `poll_trigger_and_retrieve_annotations`'s own result
already carries `final_status`/`completion_message`, which never reaches
either materializer function today.

**Not yet causing wrong data**: grepped for call sites of both functions —
neither has one. This is Phase 0 plumbing built ahead of Phase 1's live
wiring, not a live bug.

**Before wiring either function into a live caller (Phase 1)**: thread
`final_status`/`completion_message` through, and give a failed engine action
its own, more specific coverage note/state than a merely-empty-but-successful
one — a curator reading "native survey reported none" should be able to tell
"it ran and found nothing" from "it never actually ran" (this is exactly the
distinction the `catalog_and_survey` no-refresh bug's own OPEN-SURVEY-0009
failures would otherwise render as, if a curator ever re-triggers a broken
asset's native survey and then reads the result back through these
functions).

**One more nuance, caught by a peer review after the ground-truth check
above:** for coco_ods's specific back-filled rows, the *number* (0 tables)
is correct, but the *label* (`state='measured'`) still overclaims for the
runs whose blob carries no status signal — `measured` asserts that run
established the count, and a blob with no success/failure field cannot
support that claim, even when the number happens to match reality. This is
the "correct number, wrong label" failure shape: re-measuring never catches
it, because re-measuring returns the same number. The historical back-fill
does not attempt to fix this (not worth reopening that PR for it, per the
same review) — it is accepted as-is here, in writing, rather than silently.
Any future rework of the historical-blob back-fill path should consider a
weaker state than `STATE_MEASURED` (e.g. "stored, no run status recorded")
for rows whose source blob genuinely carries no success/failure signal.

## Phase 1 slice 8 (`postgres_operations`) — follow-ups logged, not fixed here

Three items surfaced building the `postgres_operations` step (design
§5.5/§5.7, `docs/design-notes/DB-OPERATIONS-STEP-IMPLEMENTED.md`), each
deliberately scoped out rather than half-built:

1. **Patroni-via-REST clustering detection is not attempted.** Design §5.5
   marks it "partly" observable, but via a live REST call to a Patroni
   instance — a different class of dependency (reachable HTTP endpoint,
   separate credential, separate failure mode) than the catalog reads this
   slice does. `get_clustering_info()` covers Citus only (a real Postgres
   extension visible from `pg_extension`) and says so in its docstring.
   Whoever picks this up should treat it as its own scope-of-fetch decision,
   not an extension of `get_clustering_info()`.

2. **`pg_subscription`'s per-item absence is not distinguished.**
   `get_external_dependencies()` swallows a permission error on
   `pg_subscription` (superuser/subscription-owner-only, subscriber-database
   only) to an empty list via the same generic try/except as every other
   read in that method. So "no subscriptions" and "not permitted to see
   pg_subscription" collapse to the same empty result — the whole-capability
   `external_dependencies` gate still distinguishes "this engine can't do
   this at all", but not this one per-item case within it. A real fix needs
   either a dedicated capability sub-flag or exception-type discrimination on
   the psycopg2 error raised for an insufficient-privilege catalog read.

3. **No live Postgres exercised any of the six new query methods.** All of
   `get_privilege_audit`/`get_replication_status`/`get_wal_archiving_status`/
   `get_backup_tool_signals`/`get_clustering_info`/`get_external_dependencies`
   are covered only through a duck-typed fake connection
   (`tests/test_postgres_operations_step.py`), which validates the
   surveyor's logic (absence states, RFA firing, the MIXED envelope) but not
   that the SQL itself is correct against a real server — e.g. that
   `EXTRACT(EPOCH FROM replay_lag)` behaves as expected against a genuine
   `pg_stat_replication` row with an actual replica attached, or that the
   `pg_default_acl` join produces sensible rows against a database with real
   default ACLs configured. Once step 6 (re-cataloguing `coco_ods` with a
   reachable connection) or a primary/replica test pair exists, running
   `postgres_operations` against it once and diffing the result against a
   hand-checked `psql` session would close this gap.

4. **`docs/dr-egeria/resource_questions.csv`'s prose is now stale for the
   three rows this slice's `analysis_catalog.yaml` additions resolved.**
   Regenerating `question_catalog.yaml` (required — see
   `DB-OPERATIONS-STEP-IMPLEMENTED.md`) populated `analysis_ids` for
   `db_activity_signals`/`db_resilience`/`db_external_dependencies`, but the
   CSV's own `Answering Analysis` text for those rows still reads `GAP:
   <id> (proposed) — <analysis> is not read by any analysis today`, and
   `answering.kind` is still `gap`/`human`. The CSV is stream 4's ownership
   and not touched by this slice; whoever next edits it should reword those
   three rows' notes (and reconsider `kind`) now that the analyses exist.

   **Extended 2026-09-21 by Phase 1 slice 9 (`db_derived`), same shape, six
   more rows.** Regenerating the YAML again populated `analysis_ids` for
   `db_classification`, `db_relationship_graph`, `grain_determination`,
   `db_fingerprint`, `schema_conventions` and `db_change_rates`, while the
   CSV's own prose for those rows still reads e.g. `GAP: db_fingerprint
   (proposed) — FingerprintAnnotation exists as a type; no signature is
   computed or compared` — which is now false in every case, since all six are
   implemented and backed by a real step. That makes **nine** database rows
   whose `answering.kind: gap` and "(proposed)" wording contradict their own
   populated `analysis_ids`. Still stream 4's file and still not touched here,
   but the drift is no longer marginal: a reader of the Questions tab is told
   these questions cannot be answered by anything, by a note sitting next to
   the id of the analysis that answers them.

### `ConnectionMaker.create_connection`'s direct (non-template) body silently drops `configurationProperties`

Found 2026-09-21, running Phase 1 slice #6 (probe 9, properly, against
freshly-recatalogued `coco_ods`/`coco_pharma` post-redeploy —
`docs/design-notes/PROBES-2026-09-21.md` has the full write-up under "Probe
9 done properly"). Two separate `Connection` elements this session — RE's
own admin secrets-store `Connection` and the per-database
`SecretsStoreConnection` embedded by the PostgreSQL template — both came
back from Egeria with `configurationProperties` entirely absent, despite
both being supplied in `ConnectionMaker.create_connection`'s creation body.
Patching the same property afterward with `update_connection(...,
mergeUpdate=True)` took effect immediately, confirming the property itself
is fine server-side; it is specifically the *creation* call that drops it.

**Not the same bug as the wrong-connector-class one above** (that one is
about which class handles the property; this one is about the property
never landing at all), and **not the same as the missing-placeholder OCF
precondition** (that one is fixed by supplying a value; here a value was
supplied and still didn't land).

**Not investigated further here** — needs someone to determine whether this
is a `ConnectionMaker.create_connection` client-side body-shape defect
(candidate for `PYEGERIA_ISSUES.md`, pending the usual approval-before-fix
gate) or an Egeria server-side difference in how `configurationProperties`
is handled between a template-instantiation body
(`TemplateRequestBody.placeholderPropertyValues`, which has never shown
this symptom) and a direct `NewElementRequestBody`/`UpdateElementRequestBody`
creation. Both of this session's live-verified admin-store and
per-database Connections needed a manual `update_connection` patch to work
at all; `egeria_database_surveyor.py`'s own code has not been changed to
work around this yet, since the right fix depends on which side the defect
is actually on.

### A leftover `ConnectorType` can carry a since-fixed bug forward, because "found by qualifiedName" never re-verifies its properties

Found 2026-09-21, same investigation. The wrong-connector-class bug (this
file's "Native Postgres survey fails with SCRAM auth error..." entry, above)
was fixed in PR #188 for *new* `ConnectorType` creation. But
`_ensure_own_secrets_store_guid`'s find-or-create now correctly reuses a
`ConnectorType` if one already exists by qualifiedName (a separate fix,
also 2026-09-21, for a 409 the naive Asset-only existence check caused) —
and a leftover `ConnectorType` from before the class fix landed still
carried the old, wrong `connectorProviderClassName`
(`YAMLSecretsStoreProvider`, read-only). Reusing it by qualifiedName
silently carried the old defect forward even though new-creation code had
already been fixed — a third instance of "reuse never repairs," this time
of a bug that genuinely was already fixed for the creation path. Patched
live via `update_connector_type`.

**Not fixed at the source.** Open question for a project-owner decision:
should `_ensure_own_secrets_store_guid` verify a found `ConnectorType`'s
`connectorProviderClassName` before trusting it (repairing it in place if
wrong), the same "reuse never repairs" lesson this file already applies
elsewhere — or was this specific stale element simply a one-time leftover
from mid-development that a platform which has never run the pre-fix code
will not reproduce, making the extra verification permanent complexity for
a transient problem?

### Exposure heatmap (data_class_match × privilege_audit, design §5.6) will show false negatives if built on today's grant reads — design guidance for Phase 1 slice #10

Found 2026-09-21, designer round 2 review, drawing the exposure heatmap
against the reads slice #8's `postgres_operations` (`privilege_audit`)
already ships. **Not a bug in shipped code** — slice #8's own scope (a
table-level RFA when the `PUBLIC` pseudo-role holds a grant) is correct and
complete for what it does. The finding is that reusing today's grant reads
naively for a *column-level, per-role* exposure matrix (§5.6's composite,
gated on slice #10's `data_class_match`, not started) would produce three
distinct false negatives — a role reads as having no access to a sensitive
column when it actually does:

1. **The reads are table-level; a column-level grid needs column grants.**
   `information_schema.role_table_grants` (what `get_privilege_audit()`
   reads today) and the structured `database_grants` table (Phase 0 stream
   3) are both table-granularity — `database_grants` has no `column_name`
   column, not even in its `UNIQUE` constraint. A column-level `GRANT SELECT
   (email) ON patient TO analyst_ro` is invisible to both, and that role
   would render as a full row of "no access" while actually holding a real
   column grant.
2. **`PUBLIC` can't be rendered as a column-role in a per-role grid.** It's
   a pseudo-role, not a real one — as one column among several, a reader
   scanning a specific role's row for red would find none, while that role
   can in fact read everything via its `PUBLIC` inheritance.
3. **Role membership/inheritance is read nowhere in this codebase** — no
   `pg_auth_members`, no `pg_has_role`, anywhere. Granting access through a
   group role (the normal Postgres administration pattern) would be
   invisible to a grid built only from direct per-role grants.

**The fix, when slice #10 builds this**: `has_column_privilege(role, table,
column, 'SELECT')` collapses all three into one call — Postgres itself
resolves membership, inheritance and `PUBLIC` at query time, so the
composite doesn't need to reimplement any of it. Requires extending
`database_grants`'s structured table with a `column_name` field (a
migration) and reading `information_schema.column_privileges` (a strict
superset of what `role_table_grants` reads today — it returns column grants
*and* table grants pre-expanded per column) rather than the current
table-only read.

**The general rule underneath it, worth carrying into slice #10's design
directly**: for an exposure view, the two failure directions are not
symmetric the way they are for a plain measurement. A missing measurement
elsewhere in this codebase renders as an honest gap (`not_established`,
`STATE_NOT_COLLECTED`, etc.) — but here, a missing read renders as a *clean
bill of health*, which is the worse direction to fail in for a
security-relevant view. An empty cell in the exposure grid has to mean "no
access path found" and never "no access path measured" — the absence
discipline this codebase already applies elsewhere needs an even stronger
form here, since the default failure mode (silence) reads as reassurance
instead of as a gap.

### `n_distinct` sign fix (design review round 2, 2026-09-21) does not repair rows already stored — decision needed

`database_surveyor.py`'s `_resolve_n_distinct()` fixes `pg_stats.n_distinct`'s
sign convention going forward, using `pg_class.reltuples` (the same
`ANALYZE` run's row count, not a separately-read live count — a second
design-review finding, caught before it shipped: the first fix used
`pg_stat_user_tables.n_live_tup`, which can drift from the count the ratio
was actually computed against). A third finding, same review: `reltuples
== -1` only means "never analyzed" from PG14 on — on PG13 and earlier, `0`
means both "analyzed, empty" and "never analyzed", so a table with real
rows that was never ANALYZEd would otherwise resolve to a confident, wrong
0. Fixed version-independently by cross-checking `last_analyze`/
`last_autoanalyze` (already read by this module) rather than trusting the
server-version-dependent sentinel alone.

**The fix does not touch rows already written.** Every `database_column_profiles`
row from a survey run before this fix carries the raw, unresolved value —
either a bare negative number, or (if some earlier ad hoc code already
multiplied by the wrong row count) a number computed from a mismatched
denominator. A peer flagged this specifically: roughly 4,912 rows from a
recent back-fill, and anything surveyed since, still say what they said
before the fix. Deliberately **not fixed here** — re-running the back-fill
is described as idempotent by key and therefore safe, but re-surveying (or
otherwise mutating) the shared production registry's existing rows is a
bigger, more consequential action than this bug-fix PR's scope, and needs
the project owner's go-ahead rather than being done silently as a side
effect of a code fix.

**Until that happens**: any `database_column_profiles.distinct_count` value
recorded before this fix landed should be treated as unreliable — do not
trust it for the exposure heatmap (the entry immediately above) or any
other consumer until the affected rows are re-surveyed. Whoever picks up
either the historical-row question or the exposure heatmap should check
this entry first.

---

## Slice 10 (`postgres_column_profile`) — seven follow-ups

**Found while building** `postgres_column_profile` / `data_class_match` /
`reference_data_match` (Phase 1 slice 10,
`docs/design-notes/POSTGRES-COLUMN-PROFILE-IMPLEMENTED.md`). Logged, not
fixed — each is outside that slice's scope.

**1. No curator accept/reject surface for a DRAFT proposal — the largest gap.**
Slice 10 creates candidate `DataClass`/`ValidValueSet` elements with
`contentStatus: DRAFT` and links them to their evidence via
`AssociatedAnnotation`. Accepting one — clearing `contentStatus`, creating the
real `ValidValuesAssignment` — has no surface at all. Design §11's review
queue is the natural home. Until it exists, a proposal can only be actioned in
Egeria's own UI.

**2. RE has no DataClass/ValidValueSet browsing surface.** A DRAFT proposal is
now visible as an *annotation* (see 3), but the proposed *element* is not
visible in RE anywhere. The only real property read of a governance element in
the whole package (`database_surveyor.py`'s PII-keyword lookup, ~line 1216)
never reaches a UI. Prerequisite for 1.

**3. `contentStatus` is still not carried by the older `/{slug}/annotations`
read path.** Slice 10 surfaced it on the main chain
(`egeria_survey_reader.get_annotations_by_report_guid` → the three
`EgeriaAnnotationItem` models → `renderAnnotations`). The older
qualifiedName-guessing path (`surveyors/egeria_reader.py::_parse_annotation` →
`AnnotationItem` in `web/routes/egeria.py`) has its own model and its own
field-by-field population and was left alone. Cheap: add `"contentStatus"` to
`_parse_annotation`'s subtype-field list, then the model and its construction.

**4. `resolve_n_distinct` may end up duplicated with slice 9.** The brief for
slice 10 said to reuse slice 9's `_resolve_n_distinct`; slice 9 (`db_derived`)
had not merged and there was nothing to import, so slice 10 implemented
`column_matching.resolve_n_distinct` as a **public** name for slices 9 and 11
to import. **If slice 9 lands its own, reconcile the two to one** before both
are in the tree — two copies of a sign-convention correction is exactly the
shape that drifts.

**5. Two live bugs in `bootstrap_data_classes.py`, deliberately not copied by
slice 10.** (a) Its `create_data_class` body's `properties` omits the
`"class": "DataClassProperties"` discriminator that every other create path in
both repos sets. (b) It calls `link_valid_value_definition` with no `body`,
which makes pyegeria synthesise one from its own internal `prop` hint and POST
it un-serialised. Both are in RE's own code, so both are fixable here.

**6. Two pyegeria `prop`-hint mismatches — file, do not fix in place.**
`link_annotation_to_described_element` passes
`"AnnotationDescribedElementRelationship"` and `attach_annotation_to_report`
passes `"SurveyReportAnnotationRelationship"`, neither of which is a real
Egeria properties class (they should be `AssociatedAnnotationProperties` and
`ReportedAnnotationProperties`). Harmless while a caller passes an explicit
body — slice 10 always does — and a malformed POST when one is not. Belongs in
`egeria-python`'s `PYEGERIA_ISSUES.md` per the log-and-wait policy.

**7. Three things slice 10 could not verify without a live server**, worth one
probe together against `coco_ods`: that `TABLESAMPLE SYSTEM (…) REPEATABLE (…)`
is accepted as generated and that the 3× oversample actually fills `max_rows`;
that `create_valid_value_definition` with `typeName: "ValidValueSet"` inside
`properties` really yields a set rather than a bare definition (that path is
exercised nowhere in either repo); and that
`link_annotation_to_described_element` works at all — **that endpoint has
never been called from Python**, so the evidence links are the least-verified
part of the slice.

**Also:** the regenerated `question_catalog.yaml` rows for `data_class_match`
and `reference_data_match` still carry `GAP: … (proposed)` prose although both
analyses now exist — the same staleness slice 8 flagged for its own three ids.
The CSV's prose is its owner's call (stream 4), not a consumer's.

## Slice 14 (database change comparators, design §9.1) — what remains open

**Found while building** `db_change_comparator.py` (Phase 1 slice 14,
`docs/design-notes/DB-CHANGE-RATES-DELIVERY-IMPLEMENTED.md`). The real gap
this slice closed was structural, not computational: `db_derived`'s six
checks (slice 9) never persisted through `project_analysis_findings`/
`project_analysis_metrics` — both FK'd to `projects(slug)`, repos only — so
`notification_detector.detect_change()` (the engine behind Automate
subscriptions) silently read an always-empty history for any database
analysis_id and reported "no change" forever. A database subscription
already existed as a UI concept (`automate.py`'s `RESOURCE_ICON` includes
`database`) with no way to ever fire. Fixed for `db_change_rates` only, by
bridging `derive_change_rates`'s already-computed per-table deltas/schema
churn into a `ChangeResult`. Logged, not fixed here:

**1. Design §9.1's other six database comparators have no bridge yet:**
`schema_diff` (column/constraint-level — today's fix only covers the
table-add/drop half, via `db_change_rates`'s schema churn), `grant_change`,
`class_change`, `reference_set_change`, `scope_change`,
`resilience_change`. Each needs a two-snapshot diff over data this codebase
already collects (`postgres_operations`'s `privilege_audit`/`db_resilience`
— slice 8; `data_class_match`/`reference_data_match` — slice 10; proposed
`DataScope` — slice 9) but none of those checks is differenced across runs
today. `db_change_comparator.DATABASE_CHANGE_COMPARATORS` is the one place
to add each as its own entry; `detect_database_change` already reports "no
comparator implemented" (not a false "no change") for any analysis_id not
yet in that dict, so subscribing to one of these today is honest, not
silently broken — see 2.

**2. Subscribing to a database analysis_id with no comparator yet is legal
in the UI and silently inert.** `automate.py`'s subscription-create route
validates project existence only for `entity_type == "repo"`; there is no
check anywhere that a database `analysis_id` has an entry in
`DATABASE_CHANGE_COMPARATORS`. A user can create a `db_classification`
subscription today and it will never fire, with `established=False`
recorded on every check but nothing in the UI surfacing that distinction
(the Automate subscriptions table shows `last_checked_at`/
`notification_count`, not *why* a check found nothing). Worth a UI
affordance once a second comparator exists to make the contrast visible.

**3. The same absence gap exists on the repo side, pre-existing, not
introduced by this slice.** `notification_detector._detect_findings_change`/
`_detect_metrics_change` both return `ChangeResult(changed=False)` — not
`established=False` — for a kind with fewer than two history batches. The
`established` field slice 14 added to `ChangeResult` would apply cleanly
there too, but changing those two call sites is a repo-side behavior change
outside this slice's database-only scope; left as found.

**4. `_run_db_survey`'s db_derived dispatch fix (this slice) covers
scheduling; the per-card manual "Run" route in `web/routes/databases.py`
already had it (slice 9 built that one correctly).** Only the scheduler
path had the gap, because it independently re-derives which local surveyor
call to make rather than sharing one dispatcher with the web route — worth
a future consolidation so a new db_derived-shaped analysis can't reintroduce
the same gap a third way.

## Phase 1 slice #13 — resource reachability (2026-09-22)

Filed while building the slice (`resource_explorer/reachability.py`,
`resource_reachability` table, launcher sentence — see
`docs/design-notes/RESOURCE-REACHABILITY-IMPLEMENTED.md`). This slice was
deferred by the project owner 2026-09-21 ("do not build it yet ... further
tests") and the deferral was reversed by the project owner 2026-09-22, who
asked for it to proceed.

**Real, previously-unconfirmed finding, not something this slice fixes**:
`EgeriaFileSystemSurveyor.catalog_and_survey`'s `create_folder_element_
from_template()` call attaches no Connection to the folder Asset it
creates. Confirmed live (probe 7, first pass, `PROBES-2026-09-21.md`) —
every filesystem RE has ever cataloged this way will report
`outcome=no_connection` from this check, not a genuine reachability
answer, until something attaches a real Connection. Whether to fix this by
having `catalog_and_survey` attach one automatically (and if so, at catalog
time or lazily on first reachability check) is a real design decision, not
made here — this slice only builds the check itself and reports what it
honestly finds.

**Not re-tested**: probe 8's "folder-depth control" claim
(`analysisLevel=ALL_FOLDERS_AND_FILES`) was run against an empty scratch
folder on both request-parameter shapes and completed either way — this
does not distinguish "CHECK_ASSET genuinely skips recursion" from "there
was nothing to recurse into." Worth a follow-up probe against a populated
folder before leaning on that claim as confirmed.

**Out of scope, not attempted**: database reachability. `survey-postgres-
database` is a different governance action type with a different failure
shape (secrets-store resolution, per probe 9's write-up) — extending
`resource_reachability`/the check to databases needs its own live probe
pass, not an assumption that the folder-survey mechanism transfers.

**Pre-existing, unrelated test failure noticed while running the full suite
for this slice**: `tests/test_egeria_live_smoke.py::
TestTheByNameFallbackWorks::test_a_cataloged_database_is_findable_by_name`
fails against the current dev platform state (an assertion diff naming guid
`45a75724-dbd3-45c6-bcd3-203db34265db` — the `egeria_optional_prefect_db`
scratch database from `PROBES-2026-09-21.md`'s earlier live-verification
work). Not investigated or fixed here — unrelated to filesystems or this
slice's changes, and this session did not touch that database or its
Egeria elements.

## Database/filesystem Analyses cards: run attribution fixed, publish attribution still not established

Live-reproduced in classic (`coco_pharma @ local-docker`): running a real
database survey (`POST /api/databases/{slug}/survey` -> 200 OK, confirmed via
the network tab) left every per-analysis card (Schema Conventions, Nested
Column Profile, Data Class Match, Change Rates, ...) still showing no
run/result indicator at all. Root cause: `index.html`'s
`_loadAnalysisCatalogPanel()` only ever fetched
`/api/projects/{slug}/analyses/last-activity` when `resourceType === 'repo'`
— `lastActivity` was hard-coded to `{}` for database and filesystem no
matter what had actually run, so `renderAnalysisCatalogCards()`'s
`lastActivity[a.id]` lookup (the `📅 Last run`/`☁ Published` badges) could
never populate for those two resource types.

**Fixed:** `GET /api/databases/{slug}/analyses/last-activity` and
`GET /api/filesystems/{slug}/analyses/last-activity` now exist
(`web/routes/databases.py`, `web/routes/filesystems.py`), backed by
`workflows.analysis.build_analysis_last_activity` — the repo route's
attribution logic, generalized and shared rather than forked (`projects.py`'s
route is now a thin adapter over the same function). The frontend guard is
gone; `_loadAnalysisCatalogPanel()` fetches the right endpoint for whichever
`resourceType` is selected.

Run attribution (`last_run_at`/`last_run_status`/`last_run_partial`) is now
REAL data for database and filesystem, not a guess: `ProjectRegistry.
get_analysis_last_run()` is generalized to take a step map per entity_type —
`DATABASE_ANALYSIS_STEP_MAP`/`FILESYSTEM_ANALYSIS_STEP_MAP`
(`*/survey_definition_adapter.py`), built directly from each adapter's own
`re_analysis_step_info` descriptions, which name the analysis_catalog ids
each coarse step produces. Database's map is a genuine step->analyses
FAN-OUT (one step like `db_derived` is the real source of six separate
catalog entries) rather than repo's step->analysis PARTITION, so
`ProjectRegistry._step_key_to_analysis_ids` now returns a list per step key
and credits every owner, not just one.

A second, smaller fix rides along: `survey_definition_executor.py`'s
`steps_report` entries now record each step's real `re_analysis_step` key
directly (`_step_key(step)`, already used internally for guard evaluation),
not just its Egeria `qualifiedName`. Before this, `get_analysis_last_run`
attributed a step by parsing the LAST `::`-segment off its qualifiedName and
assuming it equalled the `re_analysis_step` key — true for repo's own
authoring convention, but `docs/survey-definitions.md`'s own PostgreSQL
example uses a CamelCase qualifiedName suffix (`SchemaAndStats`) that does
NOT match its `re_analysis_step` value (`postgres_schema_and_stats`),
so nothing in the schema actually guaranteed that convention for database.
The qualifiedName-suffix parse is kept as a fallback for historical rows
that predate this field.

**NOT fixed, and deliberately not guessed at:** publish attribution
(`last_published_at`/`last_published_scope`) is still empty for every
database/filesystem analysis, always. `record_published_annotation_types()`/
`record_published_analyses()` — the two tables `get_last_published_
annotation_types`/`get_last_published_analyses` read — are written ONLY from
`EgeriaPublisher` on the repo publish path (`surveyors/egeria_publisher.py`).
`EgeriaDatabaseSurveyor.publish_step_annotations` and the filesystem
publisher never call them. Building the repo route's two-tier recorded/
shared publish-attribution fallback on top of a data source that plain does
not exist for database/filesystem would reproduce exactly the "Never run"/
"Published today" contradiction this file's `get_analysis_last_run` entry
already fixed once, aimed at the wrong field this time. Wiring this up needs:
a `DATABASE_ANALYSES_FOR_STEPS`-equivalent of `egeria_publisher._analyses_for_
steps` (straightforward — it's the inverse of `DATABASE_ANALYSIS_STEP_MAP`,
already built above), plus a call to both record functions from
`publish_step_annotations` (and the filesystem equivalent) with the actual
annotation types/analysis_ids that publish covered. `publish_stale`
(Egeria-linkage staleness) has the same gap one level up: nothing writes an
`f"{entity_type}_publish"` linkage row for database/filesystem, so that flag
is always `False` there too — real absence, not a lie, since a card only
shows it beside a `last_published_at` that is itself always empty today.

## Most of RE's own Perspectives are now content-pack-homed, not RE-owned

Live-queried via `elementHeader.origin` (`originCategory` +
`homeMetadataCollectionName`) on this platform, 2026-09-22: of the 14
`Perspective` entities in Egeria, 12 are `CONTENT_PACK`/`CoreContentPack`
origin — including 10 of RE's own canonical 12 (Financial, Governance,
Steward, Consumer, App/AI Builder, Privacy, Community, Data Expert,
Security, Architecture), plus two RE doesn't define (`Owner`,
`Administration`). Only `Admin` and `Data Owner` remain `LOCAL_COHORT`
(`qs-metadata-store`).

This happened because RE's authoring does a Merge Update against the same
`qualifiedName` (e.g. `Perspective::Financial`) — once Egeria's own core
content pack started shipping a `Perspective::Financial` entity, RE's batch
landed its updates onto that pre-existing content-pack entity instead of
creating a separate local one. Not a bug in the sense of anything broken —
the terms still resolve and Question-to-Perspective links still work — but
worth knowing before anyone assumes "RE owns its 12 perspectives outright"
or plans a Perspectives-model change without checking origin first.

By contrast, all ~100 `Question` GlossaryTerms on this platform are still
`LOCAL_COHORT` — no content-pack overlap for Questions today, and
specifically none for filesystem or reachability questions (checked
directly while deciding whether to add filesystem_inventory question rows
below).

**Also new (project owner, 2026-09-22):** a new canonical intent, `Enhance`,
for the valid values list. Not yet wired into RE — CLAUDE.md's canonical
eight-intent list (rule 17), `analysis_catalog.yaml`/intent-validation, and
any UI nav entry all still need updating. Project owner said this can be
done "when convenient" — not urgent, but real: the next session that
touches the intent list should check this entry first.

## filesystem_inventory had zero question coverage in resource_questions.csv

Found while investigating a related but separate active review
(`FALSE-GAPS-2026-09-22.md`, not authored by this session, tracking 18
rows where `Answering Analysis` still says `GAP: ... (proposed)` for
now-built analyses). Distinct problem: `filesystem_inventory` — the one
registered filesystem analysis (file walk, format/size/timestamp
classification, tabular data-file schema profiling) — was referenced by
**no row at all**, not even a `GAP:` one. The CSV had zero
filesystem-only-scoped rows; filesystem only got incidental coverage via
`database;filesystem[;dataset]` combo rows and `*` rows, none of which name
`filesystem_inventory`.

Fixed: two new filesystem-scoped rows added directly to
`docs/dr-egeria/resource_questions.csv` and the runtime YAML regenerated.
The Dr.Egeria authoring step (publishing these as real `Question`
GlossaryTerms in Egeria) is deliberately **not done in this same
change** — coordinating with other live sessions first, since the
questions batch's perspective links have no reconciler and a duplicate
there is permanent (see `coordinate-shared-writes` skill). Do the
authoring as a separate, single, coordinated run once clear.

## Phase 1 done-test verification (2026-09-22): NOT satisfied — Phase 2 held

`COORDINATOR-BRIEF-MULTI-RESOURCE.md`'s own gate for starting Phase 2
(filesystems) is its done-test: "`coco_ods` answers 'which columns conform
to a Data Class?' and 'how is it changing?' from stored rows; the Egeria
asset carries both a native and an RE report with same-typed column
annotations; a new column and a new PUBLIC grant each raise an RFA." This
had never been re-verified end-to-end since slices 6-14 merged. Ran all
four clauses live against `coco_ods` (scratch table, real inserts, real
`ALTER TABLE`/`GRANT`, real scheduler dispatch — all cleaned up after):

- **"How is it changing?" — PASS.** `db_change_rates` correctly reported
  `state=measured, deltas.rows_inserted=50` against a real 50-row insert.
- **Native + RE reports share annotation types — PASS.** Confirmed 3
  existing native `SurveyReport`s plus a freshly-published RE report on
  the same asset, both using `ResourceMeasureAnnotation`.
- **"Which columns conform to a Data Class?" — FAIL, two layered causes.**
  (1) The live "Run" path (`DATABASE_ANALYSIS_STEP_MAP["data_class_match"]`
  → `DatabaseSurveyor.survey()`) never passes a `ReferenceCatalog`, so
  every column reports "not established" regardless of platform state.
  (2) Even if wired, there is nothing to match against: Egeria currently
  holds **0 Data Classes and 0 Valid Value Sets**. `bootstrap_data_
  classes.py` is itself broken — it treats pyegeria's `"No elements
  found"` miss-sentinel from `get_guid_for_name` as a valid GUID, so it
  reports "6 skipped" while creating none. `resource_questions.csv` row
  76 also still reads `GAP: data_class_match (proposed)`, so even a fixed
  backend wouldn't surface as answered on the Questions tab yet.
- **New column / PUBLIC grant → RFA — FAIL, architectural gap.**
  `db_change_comparator.py`'s `DATABASE_CHANGE_COMPARATORS` only wires up
  `db_change_rates`. Its own docstring already says `grant_change` and a
  column-level `schema_diff` aren't built. Confirmed live: added a real
  column and a real `GRANT SELECT ... TO PUBLIC`, ran the actual scheduler
  dispatch path twice, zero RFAs either time.

**Decision (this session, 2026-09-22):** Phase 2 stays held. Two fixes
needed to actually close Phase 1, tracked as separate slices: (a) fix the
`bootstrap_data_classes.py` sentinel bug, seed real Data Classes, wire
`ReferenceCatalog` into the live survey path, fix the CSV row; (b) build
`grant_change` and a column-level `schema_diff` comparator.

## `data_class_match`'s "Run" button was wired to a `ReferenceCatalog` that no caller ever loaded, and the bootstrap script that seeds Data Classes never actually created any (2026-09-22)

Two layered bugs, both on branch `re/data-class-seed-and-wiring`, found and
fixed in the same session as a live check of whether "which columns conform
to a Data Class?" is actually answerable end-to-end.

**Bug 1 — wiring gap.** `DatabaseSurveyor.survey()` accepted a
`reference_catalog` parameter (`database_surveyor.py`) that no caller —
neither `web/routes/databases.py`'s per-card `run_single_database_analysis`
("Run →") nor `scheduler.py`'s scheduled runs, both going through
`run_database_survey` — ever passed. `survey_definition_adapter.py`'s
`_run_postgres_column_profile` (the Survey Definition executor path) DID
load one via `egeria_reference_catalog.load_reference_catalog`, so the two
doors into the same `postgres_column_profile` step behaved differently: one
read the platform's Data Classes, the other silently ran with
`reference_catalog=None`, which `column_profile_step.py` correctly treats as
"we did not ask" — `MATCH_NO_CANDIDATES`/"not established" for every
column, regardless of the data. **Fixed** by having `survey()` itself load
the catalog (via the SAME `load_reference_catalog`/`build_reference_clients`
functions, not a second implementation) when `column_profile` is requested
and no catalog was supplied — one lock, not two that can drift. New
`read_egeria_catalog` parameter mirrors the Survey Definition path's escape
hatch. Tests in `tests/test_database_surveyor_steps.py`
(`TestColumnProfileLoadsReferenceCatalog`).

**Bug 2 — `bootstrap_data_classes.py`'s existence checks were fooled by
pyegeria's miss-sentinel.** `get_guid_for_name` returns the literal string
`"No elements found"` on a miss, not `None`/`""`/an exception — truthy in
Python, so this script's `if not guid` checks (three of them: DataClass,
ValidValuesSet, keyword ValidValueDefinitions) read every miss as "already
exists" and skipped every create. A real run reported "Created Data
Classes: 0, Skipped: 6" against a platform holding zero. **Fixed** by
routing all three lookups through `survey_definition_reader._as_guid`
(already used correctly elsewhere in this codebase). The identical bug was
found and fixed the same way in `egeria_reference_catalog.py`'s
`_find_existing` (used by the DRAFT-proposal path) while checking the rest
of the directory for the same pattern. Logged as
`egeria-python/PYEGERIA_ISSUES.md` ISSUE-114 (caller-guideline entry, not a
pyegeria code change — no fix applied there, per this repo's standing
"log and wait for approval" convention for pyegeria itself).

**Bug 3 — found only once Bug 2 was fixed enough to actually attempt a
create:** `bootstrap_data_classes.py`'s `create_data_class` body omitted
`properties.class: "DataClassProperties"` and `isOwnAnchor: true`, which
Egeria rejects outright (400 `CLIENT_ERROR_400`) — `create_valid_value_definition`
was missing `isOwnAnchor` too, and `link_valid_value_definition` was called
with no body at all, a gap `egeria_reference_catalog.py`'s own docstring
already named ("makes pyegeria synthesise one and POST it un-serialised — a
live bug this does not copy"). All three fixed to match the body shapes
`egeria_reference_catalog.py`'s `build_proposed_data_class_body`/
`build_proposed_valid_value_set_body` already use correctly. Tests in
`tests/test_bootstrap_data_classes.py`.

**Live-verified end to end**, against the shared dev platform (reachable
from this checkout at `https://localhost:9443`, not `host.docker.internal`
— that hostname only resolves from inside a container): confirmed zero
existing Data Classes/Valid Value Sets first, then ran the fixed bootstrap
and got "Created Data Classes: 6, Skipped: 0" — GUIDs:
`EmailAddress cf667f16-e9d8-45dd-a86e-3fa27e0f62d6`,
`PhoneNumber f6333002-7a31-4311-8556-2644bfc9984e`,
`SocialSecurityNumber ebf003df-629b-4a00-a7aa-d939a5e833f7`,
`CreditCardNumber f2b4fbdf-eb35-4263-b487-3b74518ec451`,
`Password 3d442693-cfae-40c8-95f9-a7bfbac9739b`,
`DateOfBirth 2e597370-d598-4109-9acd-04ee06eac828`. Then re-ran
`data_class_match` through the actual "Run" path
(`DATABASE_ANALYSIS_STEP_MAP["data_class_match"]` → `run_database_survey` →
`DatabaseSurveyor.survey()`) against a scratch table
(`public.scratch_verify_phase1_wiring`, an `email_addr` column of
well-formed emails, dropped after) and confirmed `reference_catalog.available
= True, data_class_count = 6` and a genuine, established verdict — no
longer `no_candidates`/"not established" regardless of data.

**Found but NOT fixed, flagged for whoever designs the seed content next:**
the live verdict for `email_addr` came back `unmatched_patterned` (proposing
a new class), not a match against the seeded `EmailAddress` class — because
`column_matching.pattern_conformance_any` treats a Data Class's `dataPatterns`
as VALUE-matching regexes (fullmatched against sampled values), but
`STANDARD_DATA_CLASSES` in `bootstrap_data_classes.py` populates
`dataPatterns` with plain keyword strings (`"email"`, `"email_address"`, ...)
meant as name hints — and that same list is reused, unchanged, as the display
names of the seeded `ValidValuesSet`'s keyword members. The two uses want
different content (name keywords vs. value regexes) under one field, and
changing it to real regexes would break the keyword-set seeding that reads
the same list. Whoever owns the seed content next should either add a
separate value-pattern field or split the two lists.

**Also still pending, by design (not an oversight):** the Dr.Egeria
authoring batch (VALIDATE/PROCESS) for this session's
`docs/dr-egeria/resource_questions.csv` row 76 reword (`GAP: data_class_match
(proposed)` → `data_class_match`, `kind: gap` → `kind: analysis`) has not
been run — CSV/YAML regeneration only, per the same coordination reasoning
as the `filesystem_inventory` entry above. **Also worth checking**: an
active, separately-authored review (`FALSE-GAPS-2026-09-22.md`, referenced
above, not present on this branch) is reportedly tracking 18 rows in this
same "`GAP: ... (proposed)` for a now-built analysis" shape — row 76 may be
one of them, so whoever merges next should check for an overlapping edit to
the same CSV row rather than assume this change is the only one in flight.

## grant_change and column-level schema_diff comparators, plus two real
## bugs found live-verifying them (2026-09-22)

Phase 1 slice 14 follow-up (design §9.1). Built `grant_change` and the
column/constraint-level half of `schema_diff` in
`db_change_comparator.py`/`db_derived.py` — each its own `analysis_id`
(design §9.1's Perspective presets subscribe to them separately from
`db_change_rates`), same two-snapshot/`established` shape as
`_compare_change_rates`. `schema_diff` is deliberately scoped to tables
present in both snapshots, so a whole new/dropped table's columns stay
`db_change_rates`'s schema-churn story rather than being double-reported.

Live-verifying the coordinator brief's own done-test ("a new column and a
new PUBLIC grant each raise an RFA") against the real `coco_ods` database
surfaced two pre-existing bugs neither comparator's own logic could paper
over, both confirmed by direct query against the shared registry/database
rather than assumed:

1. **`database_grants` was written by NO survey path at all**, local or
   native. `database_surveyor.py`'s local blob already carried
   `results["operations"]` (privilege_audit's own output), but
   `result_materializer.py`'s `database_rows_from_survey_data()` never read
   it back out, so `backfill_database_survey()` unconditionally marked
   every local survey's grants `STATE_NOT_MEASURED` via
   `_DATABASE_BLOB_UNMEASURED` — even for a survey that genuinely ran
   privilege_audit. Fixed: grants are now extracted from
   `operations["privilege_audit"]["table_grants"]` when that key is
   present, and the NOT_MEASURED fallback loop skips any table the main
   loop already wrote for real.
2. **`information_schema.role_table_grants` cannot see PUBLIC's own
   grants**, or another role's, from a non-privileged connecting role — by
   that view's own Postgres documentation, it shows only grants where the
   *current* role is the grantor or grantee. RE's stored survey credential
   (`egeria_user`, an ordinary role, not a special case) surveyed a table
   with a real `GRANT SELECT ... TO PUBLIC` on it and saw only its own
   grant — the exact case design §9.1 names first ("a new grant,
   *especially to PUBLIC*"). Fixed in `connection.py`'s
   `get_privilege_audit()`: reads `pg_class.relacl` via `aclexplode()`
   instead, which is catalog metadata visible to any connected role
   regardless of what that role itself was granted.

With both fixed, live-verified end to end through the real scheduler path
(`scheduler._run_due` → `_execute` → `_check_subscriptions` →
`detect_database_change`) on a scratch table in `coco_ods`: adding a column
and a `GRANT INSERT ... TO PUBLIC`, then re-surveying, raised two RFAs
(`activity_log` ids `887db284-153e-4d5c-9736-d78020d3b0b6` — "1 column(s)
added: public.scratch_verify_slice14.phone_number" — and
`404a93b9-9fa4-4ad0-b753-d7e0b7912659` — "1 new grant(s) to PUBLIC: INSERT
on public.scratch_verify_slice14 to PUBLIC") with the PUBLIC grant named
unambiguously in the RFA's own text, not buried in a generic "grants
changed" message. Scratch table, grant, schedules and subscriptions were
all removed afterward; the two fixes above are real and stay.

Still open per design §9.1's full comparator table: `class_change`,
`reference_set_change`, `scope_change`, `resilience_change` — logged
already in `db_change_comparator.py`'s own module docstring.


## Decision reversal: DB and FS now in scope for `/next` (2026-09-22)

**Decision (project owner, 2026-09-22):** `/next` should reach parity with
classic across all three resource types (repo, database, filesystem), not
just repos — reversing the earlier ruling logged in
`docs/design-notes/COORDINATOR-BRIEF-MULTI-RESOURCE.md` ("DB and FS stay out
of `/next`. Project owner, 2026-09-20. They land in the classic UI..."; see
that file's "What is different from the repo work" section for the original
wording). The one-session-at-a-time rule on `next/app.js` that ruling had
lifted is back in effect for DB/FS-touching `/next` work.

A parity audit run the same day found the backend already resource-type-
generic under most of `/next`'s panes — the gate was UI-side only:
`paneNeedsRepo()` in `app.js` blocked Survey/Sub-Resources/By-analysis/
Disposition for any non-repo `resourceType`, the Questions-engine pane had
its own separate duplicate guard, and `automate.js`/`worklist.js` silently
hardcoded `entityType`/`'repo'` instead of reading `state.resourceType`
(worse than a gate — wrong data with no visible error, not an honest
absence). `re/next-db-fs-gate-removal` removes/relaxes these gates and
threads the real resource type through; see that branch's own commits for
what was live-verified against a running database resource vs. left as an
honest "not built" state (DB-specific views with no `/next` equivalent —
schema-distribution charts, Kroki ER diagrams, the Survey Database modal —
and filesystem-specific views — file inventory browsing, data-file
profiling — stay out of scope, unchanged by this reversal).

## By analysis / scouting-questions were repo-only; Disposition's `github_url` keying is not fixed here (2026-09-22)

Three more real, separate backend gaps found continuing the audit above (on
`re/next-generalize-byanalysis-disposition-questions`) — all genuinely
repo-only backends, not a leftover UI restriction:

**1. "By analysis" (`GET /{slug}/survey-results`)** read
`REPO_ANALYSIS_RESULTS_MAP`/`REPO_ANALYSIS_HEADLINE_MAP`
(`repo_survey_definition_adapter.py`) directly, with no database/filesystem
equivalent. Fixed by extracting the route body into `workflows.analysis.
build_survey_results(registry, entity_type, slug, stage, include_empty)` and
adding `GET /api/databases/{slug}/survey-results` and `GET
/api/filesystems/{slug}/survey-results` alongside it — same shape as
`build_analysis_last_activity` above it. repo's own curated
`SURVEY_RESULT_DASHBOARDS` groupings are untouched; database and filesystem
have no such curated, themed groupings today, so this synthesizes one
dashboard **per analysis_id** for those two entity_types instead — literally
"by analysis", which is what the pane is named.

New `DATABASE_ANALYSIS_RESULTS_MAP` (`database/survey_definition_adapter.py`)
covers 14 of database's 18 analyses with **real** local reads, not stubs:
- 8 are `live_read`-style thin wrappers over `run_db_derived()` (and its two
  comparators, `derive_schema_diff`/`derive_grant_change`) — db_classification,
  db_relationship_graph, grain_determination, db_fingerprint,
  schema_conventions, db_change_rates, schema_diff, grant_change. All eight
  already had this exact zero-fetch computation built (it backs their Egeria
  publish path); wiring a reader for them is a read-time wrapper, not new
  domain logic.
- 6 are thin reads of already-materialized detail rows or the latest
  `database_surveys.survey_data` blob — schema_inventory, row_count_snapshot
  (`database_tables`/`database_columns` detail rows), privilege_audit,
  db_activity_signals, db_resilience, db_external_dependencies (the four
  `postgres_operations` sections, read from the survey blob's `operations`
  key — no dedicated detail table exists for these four yet).

**Left out, on purpose, not guessed at:** `data_class_match`,
`reference_data_match` and `nested_column_profile` have no results reader.
Their verdicts are real (`column_matching.py`/`nested_columns_step.py`
compute them) but are turned ONLY into Egeria annotations — there is no
local table a reader could query, because `registry.upsert_finding()` (the
table every repo results_reader reads via `query_findings`) hard-requires
`registry.get(slug)`, i.e. a registered **repo** `Project`; a database or
filesystem survey cannot write to `project_analysis_findings`/
`project_analysis_metrics` at all today. Building that path — either
generalizing `upsert_finding`'s guard to accept database/filesystem
entities, or giving these three their own detail table the way
`database_column_profiles` exists for the pg_stats side of column
profiling — is real, separate schema-and-write-path work, not a reader
wrapper, so these three (and `egeria_db_survey`, which is a trigger with no
local results either way, same as repo's own Egeria-triggered analyses)
stay `results=None` — an honest "no results view yet", same as repo's own
`repository_health`.

**2. "Questions checklist" (`GET /{slug}/scouting-questions`)** — the
underlying catalog function, `question_catalog_reader.get_questions()`, was
already resource-type-generic; only the ROUTE reaching it, and the
`has_data` scoring behind it (`workflows.scouting.question_has_data`, which
read `REPO_ANALYSIS_RESULTS_MAP` directly) were repo-only. This dispatcher
backs Scouting/Discovery/Assessment/Analysis/Enrichment/Curate in `/next`,
not just a "Questions" tab (`app.js`'s `loadPane()`), so the fix has that
whole blast radius. Fixed by extracting `workflows.scouting.
build_question_checklist(registry, entity_type, slug, phase, perspectives,
purposes)` and parametrizing `question_has_data` over `entity_type`
(dispatching to the same `_results_map_for` three-way switch
`build_survey_results` uses), then adding `GET /api/databases/{slug}/
questions` and `GET /api/filesystems/{slug}/questions`. Database/filesystem
questions now score `has_data` against the real
`DATABASE_ANALYSIS_RESULTS_MAP`/`FILESYSTEM_ANALYSIS_RESULTS_MAP` above,
inheriting the same three-analysis gap noted in item 1 — a question whose
only `analysis_ids` are `data_class_match`/`reference_data_match`/
`nested_column_profile` reports `has_data: false` today (a real "checked,
found nothing to point at" answer only in the sense that there is genuinely
nowhere local to check yet, not that the analysis found nothing).

`/next`'s `app.js` gates for both panes (`paneNeedsRepoBackend('By
analysis', ...)` and the questions-engine's own duplicate copy) are removed;
both now fall through to the plain `paneNeedsRepo()` "select a resource"
check, same as Survey. `apiEntityType()` is threaded through every new call
site (`getSurveyDashboards`, `getQuestions`, and the `getContext` call the
Questions pane's human-answer overlay was making with a hardcoded `'repo'`
— found while touching this code, fixed alongside it since it is the exact
same bug class this whole effort exists to close).

**3. Disposition is NOT fixed here — investigated and deliberately left as
an honest gate.** `registry.py`'s `set_disposition`/`get_disposition_history`
are keyed by `github_url`, including a hardcoded `repo_disposition` table
with `github_url TEXT PRIMARY KEY` (~line 2762) and a `repo_disposition_
history` table keyed the same way (~line 2780) — plus the journal
(`/api/journal/repo/...`) and records (`/api/projects/{slug}/records`)
routes, both hardcoded repo paths. Unlike items 1 and 2, this is not a
route-level gap over an already-generic backend; the `github_url` primary
key is load-bearing schema, and every write/read path assumes it. A real fix
needs one of:
- a new `database_disposition`/`filesystem_disposition` table pair (schema
  duplication, but no migration of existing rows), or
- a genuine generalization of `repo_disposition`/`repo_disposition_history`
  to a `(entity_type, entity_slug)` composite key in place of `github_url`
  (no duplication, but a real migration of every existing disposition row
  and every caller that currently passes a `github_url`).

Judged too large to attempt as a "rushed half-migration" alongside items 1
and 2 in the same PR — a real schema decision (which of the two shapes
above, and whether existing `github_url` values need backfilling to slugs or
can stay keyed as-is under a widened key) belongs to its own reviewed slice.
`/next`'s Disposition gate (`paneNeedsRepoBackend('Disposition', ...)`) is
therefore left exactly as the prior agent built it — unchanged by this PR.

## Disposition generalized to `(entity_type, entity_slug)` — the schema gap above is now closed (2026-09-22)

`re/generalize-disposition` picks up exactly the decision the entry above
deliberately deferred. Investigated both options concretely before
choosing, per the brief:

**Row counts (shared dev Postgres, `resource_explorer.repo_dispositions`/
`repo_disposition_history`, read-only query):** 20 current-disposition rows,
44 history rows. Tiny — a live migration here carries none of the risk a
large table would.

**Call-site count.** Grepping every `.set_disposition(`/`.get_disposition(`/
`.get_disposition_history(`/`.record_depth_offer(` call turned up ~11
production sites (`workflows/discovery.py`, `web/routes/discovery.py` ×3,
`web/routes/projects.py` ×2, `curate_plan.py`, `facts.py`, `batch_io.py`,
`work_lists.py`, `cli/main.py`) and ~30 more in tests — larger than the
Backlog entry above estimated ("the journal and records routes"), because
`get_disposition`/`get_disposition_history` turned out to be read from five
more modules than the two routes named. This mattered for the decision (see
below): a signature change at every one of those ~41 sites was the real
cost Option B was weighed against, not just "the schema."

**FK/join surface.** `SELECT conname FROM pg_constraint WHERE confrelid IN
('repo_dispositions'::regclass, 'repo_disposition_history'::regclass)`
returned zero rows — nothing joins to disposition by `github_url`. So
Option B's migration surface does not multiply beyond the two tables
themselves, unlike a table that other tables reference.

**Decision: Option B (generalize the key), with the schema widened but the
existing repo-facing method signatures kept unchanged.** This needs
unpacking, because it is not quite either option as originally framed.

*Why B over A:* this session has a repeated, explicit precedent for
generalizing rather than duplicating —
`DATABASE_ANALYSIS_STEP_MAP`/`FILESYSTEM_ANALYSIS_STEP_MAP`,
`database_grants`, `workflows.analysis.build_survey_results` (previous
entry, item 1) all chose one generic mechanism over parallel per-type
copies. More concretely here: `registry.py` already has a **live,
maintained example of this exact shape** — `_ENTITY_SLUG_TABLES` (16
tables: `resource_tags`, `resource_feedback`, `activity_log`,
`resource_working_set`, etc.), all keyed on `(entity_type, entity_slug)`,
all repointed automatically by `rename_project_slug()`. `repo_dispositions`/
`repo_disposition_history` were the only two tables in the "entity family"
still keyed on `github_url` alone. Option A (a new `database_disposition`/
`filesystem_disposition` pair) would have added a *third* naming scheme
alongside that convention and the repo-only one, for no gain the
investigation above supports — the row count is trivial, nothing joins on
`github_url`, and `registry.py` already has a tested PK-widening migration
pattern for exactly this move (`_add_user_id_to_keyed_table`, used for
`resource_working_set`'s `user_id` column) to build from.

**Genuine risk found, and how it's handled — this is the reason the
methods' signatures did NOT change at every one of the ~41 call sites.** A
repo's disposition can be set before the repo is ever imported (a
discovery-search candidate — 1 of the 20 rows in the shared dev registry
today, `intake/intake`). At that point there is no `Project` and therefore
no stable slug — only a URL. Naively keying straight off a
github_url-derived guess (`org_importer._url_to_slug`) breaks the moment
the repo is later imported under a **different** slug than that guess: a
manual slug override, or a collision-avoidance rename. This is not
hypothetical — it is already true of live data: `odpi/egeria`'s row
carries `project_slug='egeria_git'`, not the url-derived `'egeria'`. A
repo's stable identity really is its `github_url` (that is *why* the
original schema keyed on it), and none of the other `_ENTITY_SLUG_TABLES`
rows are ever written before a project exists, so they never had to solve
this.

So: the schema generalized (composite PK), but `set_disposition`/
`get_disposition`/`get_disposition_history` (registry.py) kept their
existing `github_url`-in, `github_url`-out signatures — **zero of the ~41
existing call sites needed to change**, and the ~30 existing tests for them
pass unmodified. Internally they now resolve `entity_slug` via a new
`resolve_repo_entity_slug(github_url)` (the project's real slug once
imported, else the same url-derived guess `org_importer.py` already uses
elsewhere for a pre-import candidate) and delegate to new, genuinely
generic primitives — `set_disposition_for_entity`/
`get_disposition_for_entity`/`get_disposition_history_for_entity`
(`entity_type`, `entity_slug`, ...) — which `database`/`filesystem` callers
use directly, since those entities have no pre-import ambiguity (their slug
*is* their stable identity from registration). `add()` gained a
reconciliation step (`_reconcile_disposition_on_import`) that re-keys a
provisional pre-import disposition row onto the real slug at import time,
for the rarer case where they differ — closing the gap rather than leaving
it as a latent bug. `rename_project_slug` also gained
`repo_dispositions`/`repo_disposition_history` in its `_ENTITY_SLUG_TABLES`
repoint list, so a later rename keeps disposition in sync the same way it
already does for the other 16 tables.

This is a deliberate departure from "update every caller to the new
signature," and the reasoning is worth being explicit about rather than
silently choosing the smaller diff: the repo-facing signature is not a
leftover — `github_url` genuinely is the right identity for a resource that
can be triaged before it exists as a project and renamed after, and forcing
each of ~41 call sites to compute (or fetch) an `entity_slug` themselves
would duplicate `resolve_repo_entity_slug`'s logic that many times for no
behavioral gain. The literal instruction's intent — every caller reaching
the correct API for its entity type — is satisfied: repo callers already
had the correct API, and it stayed correct; new `database`/`filesystem`
callers reach the new one.

**Migration.** `repo_dispositions.github_url TEXT PRIMARY KEY` →
`(entity_type, entity_slug)` composite PK, following the exact
Postgres-vs-SQLite branch `_add_user_id_to_keyed_table` established
(`ALTER ... DROP CONSTRAINT` + `ADD PRIMARY KEY` on Postgres; create-copy-
swap on SQLite, since SQLite cannot drop a PRIMARY KEY in place).
`repo_disposition_history` needed no PK change at all (it's keyed on its
own `id`, never on `github_url`) — just a backfill and a new index.
Backfill uses `project_slug` when the row already has one (the resolved
slug set the last time this repo's disposition was written with a `Project`
in hand — this is what makes `egeria_git`, not `egeria`, the right answer
for that row) and falls back to `_url_to_slug(github_url)` only for the
rarer empty-`project_slug` row. Verified against a hand-built pre-migration
SQLite fixture reproducing both shapes (a normal imported-repo row and the
`egeria`/`egeria_git`-style divergent one) — backfill lands both at the
correct `entity_slug`, confirmed by reading it back through the unchanged
`get_disposition`/`get_disposition_history` API afterward.

**Records/journal routes.** The journal route
(`/api/journal/{entity_type}/{slug}`) turned out to already be fully
entity-generic on the backend — the "hardcoded repo paths" this and the
prior entry described was actually the **frontend** wrapper
(`re-api.js`'s `getJournal`/`writeJournal` hardcoding `/api/journal/repo/
...`), fixed by threading an `entityType` param through (default `'repo'`,
so no existing caller's behavior changes). The records route
(`/api/projects/{slug}/records`, `.../records/{record_id}/act`) was a real
backend gap, but a route-level one, not a disposition-schema one — same
shape as items 1/2 above: `Curations.for_resource(entity_type, slug)` was
already generic underneath; only the existence check (`registry.get(slug)`,
Project-only) and the hardcoded `entity_type='repo'` passed to
`WorkLists`/`log_rfa` needed generalizing. Added entity-generic siblings
(`GET/POST /api/projects/entity/{entity_type}/{slug}/records[/...]`) rather
than changing the existing repo routes in place, so the repo path is
provably untouched. `GET .../records/{record_id}` (single-record fetch/
export) needed no sibling — it never checked `entity_type` at all.

**Deliberately NOT generalized, and why:** DepthOffer (the "these analyses
have never run" offer shown after a verdict) stays repo-only. It reasons
about the repo analysis catalog's analysis/assessment tiers specifically;
generalizing it is a separate, analysis-catalog-shaped piece of work, not a
disposition-schema one, and nothing in the brief asked for it. The frontend
already degrades correctly without special-casing: `renderDepthOffer` gates
on `p?.github_url`, which is simply absent for a database/filesystem
`resource`, so it silently doesn't render rather than erroring. Similarly,
a report's "write a correction" action (`saveReport`) stays on its
existing repo-only route — out of scope here, and it fails with a visible
error rather than silently for a database/filesystem record if someone
reaches it, which is the correct degrade for something genuinely unbuilt.

**Frontend.** `/next`'s Disposition pane no longer calls
`paneNeedsRepoBackend()` — with the backend gap closed, nothing called that
function any more, so it was deleted outright (dead code left in place is
exactly the kind of thing a future gate could reach for again without
re-checking whether it's still needed). The pane now uses the plain
`paneNeedsRepo()` "select a resource" check, threads
`apiEntityType(state.resourceType)` at the boundary (same convention as
`getSurveyCandidates`/`getQuestions`), and branches picker/history/journal/
records calls on whether the resolved entity type is `'repo'` (github_url-
keyed, unchanged) or not (entity_slug-keyed, new). `DatabaseSummary`/
`FileSystemSummary` gained a `disposition` field (mirroring
`ProjectSummary`'s, already there) so the picker renders the current
verdict without an extra round trip.

**Tests.** `tests/test_registry.py` gained migration/backfill coverage
(a fixture with pre-migration `github_url`-keyed rows, including the
divergent-slug case, asserting the post-migration `(entity_type,
entity_slug)` landing spot) and coverage of the new
`*_for_entity`/`resolve_repo_entity_slug`/`_reconcile_disposition_on_import`
methods. `tests/test_next_db_fs_gate_removal.py` updated: the class
asserting `paneNeedsRepoBackend` still gated Disposition is replaced with
one asserting the function is gone entirely and Disposition now follows
the same `paneNeedsRepo()`/`apiEntityType()` shape as By analysis/
Questions.

**Live verification.** Not performed against the running dev server
(`localhost:8810`) for this schema change specifically, and that gap is
deliberate, not an oversight: that server currently runs the *old*
registry.py against the *same* shared Postgres database this migration
targets. Running the migration from this branch (a `ProjectRegistry()`
construction is enough to trigger `_init_schema()`) would alter
`repo_dispositions`' live constraints out from under the currently-running
server mid-session — its old code's `INSERT ... ON CONFLICT(github_url)`
would then fail outright (`ON CONFLICT` requires a unique constraint
exactly matching its target, and `github_url` stops being one), breaking
disposition-setting for every concurrent user until that server is
restarted onto this branch. That restart is exactly the kind of shared,
unreviewed disruption this repo's conventions route through a merged PR
and a coordinated restart, not a solo subagent action — and this session
had no working peer-messaging path to confirm no one else was mid-write
against the same database first. Verified instead: the full local test
suite (below) against a fresh SQLite registry and a hand-built
pre-migration SQLite fixture reproducing the shared DB's actual data shape
(20/44-row scale, including the one divergent-slug row) with the migration
applied and read back through the unchanged public API; and a read-only
`psql` query confirming the live table's current shape, row counts, and
absence of FK references, all reported above. The coordinator should run
this migration (or accept the PR and let the normal deploy/restart cycle
do it) rather than have it applied ad hoc from a subagent session.

---

## Prefect-orchestrated survey definitions don't get §17.1's prerequisite resolution

**Found while building** §17.1 (prerequisite auto-run, PR #241).

The design doc says the Prefect path already expresses step dependencies as
task edges and "Prefect renders the chain itself" — checked against the code
and that's not true. `survey_execution_plan.build_plan` builds Prefect's task
graph from a definition's authored `Link Next Process Step` edges and their
guards only; it has never read `requires_context`, and `PRODUCES` (this PR's
new source of truth for what a step writes) did not exist before it. A
Prefect-orchestrated definition would dispatch a step whose stored input is
absent, the same failure `step_preconditions.py` existed to catch on the local
path.

Contained, not fixed: a definition that needs resolving takes the local
execution loop (which still routes individual `executes_at: prefect` steps
through `run_prefect_step`); a definition with nothing to resolve — every
definition today — goes to Prefect unchanged.

**Candidate fix:** fold `PRODUCES` edges into `survey_execution_plan.build_plan`
so Prefect's own task graph carries the same producer/precondition edges the
local resolver derives, rather than running two different dependency
mechanisms depending on which coordinator a definition happens to use.

---

## §17.2's cost vector is missing `source_rows`/`source_queries`, by design — but there's no marker for "will never be sampled"

**Found while building** §17.2 (cost-vector recording, PR #241).

The design lists `source_rows`/`source_queries` as "optional, sampled" —
`pg_stat_statements` deltas or filesystem read counters. Neither is built, so
the fields are simply **absent from the metrics vector**, not present at 0 (a
0 would misread as "this step scanned no rows"). That's the right call for
now, but nothing distinguishes "not sampled yet, could be added" from "will
never be sampled for this step kind" — a later reader of `step_runs` has no
way to tell those apart without re-reading this PR.

**Candidate fix:** when `pg_stat_statements`/read-counter sampling is designed,
decide the presence convention explicitly (absent vs. a typed "not sampled"
sentinel) rather than leaving it implicit in "the column is missing."

---

## §17.3's Admin "Performance" panel — deferred, needs designer round 2 after real rows accumulate

**Found while building** §17.2/§17.3 (PR #241).

The derived metrics (cost per question answered, tier ratio) are built as
functions with unit tests and no UI — `step_runs` has no consumer yet besides
the resolver's own estimate lookups. Per the design's own sequencing, this
waits for two weeks of real rows before a designer pass on the four
Admin-panel views §17.3 describes.

**Candidate fix:** none yet — this is intentionally waiting on data, not on
design. Revisit once `step_runs` has enough real-run history to make the
panel's views meaningful rather than speculative.

---

## `/next`'s prerequisite-proposal UI doesn't exist yet — classic-only for now

**Found while building** §17.1 (PR #241).

`POST /api/prerequisites/plan`/`run` work over HTTP (verified live against a
real database), but nothing in `/next`'s stage pages renders a proposal or
offers the "run it?" accept/decline flow — matching classic-first precedent
elsewhere in this codebase, but a real capability gap in `/next` today: a
`/next` user who crosses a tier boundary gets no prompt at all where classic
would show one.

**Candidate fix:** design the `/next` equivalent of the classic proposal
prompt — likely a toast/inline-card pattern consistent with `/next`'s existing
run-in-background and queued-toast conventions, reading the same
`/api/prerequisites/plan` response classic will use.

---

## Two cost-vector measurement blind spots, both undercounting rather than overcounting

**Found while building** §17.2 (PR #241), verified live against the real
shared Postgres.

- **`connects` is always 0 for database steps.** psycopg2 opens its socket
  inside libpq, below anything RE's observer can instrument — so the
  `fetch_cost='none'` disagreement check (a step declaring zero-fetch that
  actually opened a connection) can never fire for a database step. A clean
  board proves nothing here; it's structurally unable to catch the case it
  exists to catch.
- **A thread spawned inside a step doesn't inherit the calling ContextVar
  scope**, so external calls made from that thread are undercounted in the
  step's own cost vector. Safe direction for an "is this expensive" alarm
  (undercounting never triggers a false alarm), wrong direction for trusting
  a step's own "this was free" claim — `bytes_complete` on the row says
  whether the count is trustworthy, but nothing surfaces that distinction
  anywhere a reader would see it before trusting the number.

**Candidate fix:** for `connects`, instrument at the connection-pool/adapter
layer RE controls (`resource_explorer/connection.py`) rather than trying to
observe libpq; for the thread issue, propagate the ContextVar explicitly at
thread-spawn sites inside steps, or surface `bytes_complete=false` more
visibly wherever `step_runs` metrics are displayed.

---

## `/next`'s "Survey & analyses" tab silently drops Egeria's own native, technology-specific survey processes — classic already shows them

**Found live** (project owner, 2026-09-24): opened `/next`'s "Survey & analyses"
sub-tab on a real database (`coco_ods`, PostgreSQL) and got "No survey
definitions for this resource — the adapter registered none for this
technology type," alongside a "Scope: all tiers — stage filter unavailable"
chip. **First write-up of this entry claimed no Egeria survey definitions
exist for database/filesystem at all — that was wrong, corrected by the
project owner** ("there are plenty of Egeria database surveys — they are not
generic, they are for PostgreSQL or whatever") and re-traced below.

Two genuinely separate conventions exist in this codebase, per
`ResourceTypeAdapter`'s own docstring and `technology_type_processes.py`'s
header comment: **RE-authored** Survey Definitions (a `GovernanceActionProcess`
tagged via `additionalProperties.supported_technology_type`, RE's own
free-text convention — none exist for database/filesystem, only
`repo-survey-definition-*` documents do, confirmed via
`docs/dr-egeria/survey-definitions/`), and **Egeria-native** survey/catalog
processes (real, pre-existing governance action processes/types Egeria
itself ships or has cataloged for a real Technology Type, e.g. PostgreSQL).
The second kind is real and already known to this codebase —
`resource_explorer/configdata/technology_type_processes.yaml` has confirmed,
live-verified entries for `PostgreSQL Relational Database` and
`PostgreSQL Server`, including `PostgreSQLSurvey::survey-postgres-database`
(`kind: survey_existing` — safe to trigger directly against an existing
asset) and `PostgreSQLDatabase:CreateAndSurveyGovernanceActionProcess`
(`kind: catalog_and_survey`).

`web/routes/survey_definitions.py`'s `list_candidates()` DOES read this
config (`get_native_processes(entity_type, adapter.egeria_technology_type_name)`)
and returns it as a separate `egeria_native_processes` field on the response
— deliberately not merged into `candidates`, since only `survey_existing`
processes are currently safe to expose as runnable and `catalog_and_survey`
needs template placeholder params RE doesn't collect yet. **Classic**
(`web/static/index.html:8661`) reads this field and renders it as an
informational block: *"Also known to Egeria for this technology (not yet
runnable from here)."* **`/next` never reads `data.egeria_native_processes`
at all** (`grep` across `web/static/next/app.js` returns nothing) — so for
`coco_ods`, `/next` shows "no survey definitions" while classic, given the
exact same API response, would show the real PostgreSQL native survey
process that DOES exist for it. The "stage filter unavailable" chip is a
correct, separate symptom (RE's own candidate lookup genuinely finds zero
RE-authored definitions and falls back to a full scan, which also finds
zero) — but it's misleading in context, since it implies nothing is
survey-able here when something is.

This is the same shape as the rest of this session's "/next hasn't caught up
to classic" findings, not a new kind of gap.

**Candidate fix:** port classic's `nativeProcessesHtml` block
(`web/static/index.html` around line 8661) into `/next`'s `loadSurveyPane()`
(`web/static/next/app.js`, the same function that renders the "No survey
definitions" message) — read `data.egeria_native_processes`, render it the
same informational-only way classic does, and make the "no survey
definitions" empty-state message conditional on BOTH lists being empty, not
just `candidates`. Separately worth a design decision, not blocking this
fix: whether `survey_existing` native processes should become genuinely
runnable from `/next` (they're flagged safe in the config already) rather
than staying informational-only in both UIs.

---

## Revisit bundling `repo_symbol_extraction` into `interface_surface` once cheap-refresh or smaller-survey wiring exists

**Decision (project owner, 2026-09-24):** leave `interface_surface`
un-bundled, as merged in `#248` — no hard precondition, no auto-triggered
`repo_symbol_extraction`. `PR #245`'s design doc originally cited
`api_structure` as already using a "bundle" pattern for this exact
relationship; that citation was wrong (`api_structure` was deliberately NOT
bundled with `repo_symbol_extraction`, for the identical cost reason —
see `analysis_catalog.yaml`'s comment above that `AnalysisKind` entry), so
there was no working precedent to adopt as-is.

**Why revisit later, and what would have to be true first:** the real
objection to bundling is that `repo_symbol_extraction` is `fetch_cost=
"download"` — a full zipball fetch — every time, even when the repo has not
changed since the last extraction. Two things named by the project owner
would change that cost calculus enough to make bundling worth trying again:

1. **Skip the download when we already have the most recent version.**
   `SourceCache` (`github/source_cache.py`) already keys `zipball_root`/
   `git_clone_root` on `(repo, commit SHA)` and shares it across
   `SurveyOrchestrator.run()` calls within one run — but nothing today
   checks "is the SHA we last extracted from still the repo's current HEAD"
   *before* deciding whether extraction is needed at all, across separate
   runs/days. If a cheap SHA check (one API call, not a fetch) could answer
   "nothing has changed since our last `project_code_markers`/
   `project_code_symbols` write," bundling stops meaning "always pay for a
   fresh download" and starts meaning "usually free, occasionally pays."
2. **Wire smaller surveys together instead of building bigger ones.**
   A framing the project owner raised directly, distinct from (1): rather
   than making `interface_surface` itself absorb `repo_symbol_extraction`'s
   cost, treat the relationship as an orchestration question — could §17.1's
   own prerequisite-resolver machinery (already built, `PR #241`) be the
   thing that composes "run `interface_surface`, and if its input is stale,
   chain in `repo_symbol_extraction`" from two small, independently-useful
   surveys, rather than either bundling them into one entry or leaving them
   fully decoupled? This is closer to composing existing small pieces than
   authoring a new combined analysis, and is worth a design pass of its own
   before deciding whether "bundle" is even the right verb.

**Candidate fix:** no code change until (1) or (2) exists. When either
lands, re-open the bundling question for `interface_surface` specifically
(and audit whether the same reasoning applies to any other analysis that
today avoids bundling `repo_symbol_extraction` purely on cost grounds).

---

## `build_plan`'s PRODUCES-folding checks (`#247`) aren't run against the real, authored survey-definition corpus

**Found while answering a project-owner question** ("how much static
analysis can we do on the survey rather than only runtime checking?",
2026-09-24, re: `PR #247`).

`build_plan()` already does real static analysis: `MissingPrerequisiteError`/
`PrerequisiteTierError` raise at plan-construction time, before any step
executes, whenever a caller passes `step_registry=`. But
`tests/test_survey_execution_plan.py::test_every_live_definition_plans_
to_its_existing_order` — the one test that runs `build_plan()` against
*every real, authored* Survey Definition document under
`docs/dr-egeria/survey-definitions/` (via `documented_definitions()`) —
calls it **without** `step_registry=`. So the new PRODUCES-folding path is
only exercised against small hand-built fixtures (the `produces_world`
fixture and its sibling tests), never against the real corpus. A survey
definition authored with a step whose precondition producer is missing or
crosses tier would not be caught by this test today, only by an actual
Prefect run.

**Candidate fix:** extend `test_every_live_definition_plans_to_its_existing_
order` (or add a sibling test) to also call
`build_plan(definition, step_registry=STEP_REGISTRY)` for every real,
authored document and assert it does not raise either new error. Cheap
(same test, one more assertion per document), zero runtime cost, and turns
"will this survey definition actually work through the Prefect path" into a
CI-time guard for every authored document rather than something only
discovered by running it.

---

## `interface_surface`'s Thrift/SOAP coverage — a real gap and a probably-not-worth-it one, found together

**Found while answering a project-owner question** ("seems like we need a
Thrift bucket? Are we also looking at Swagger? Is SOAP really out in the
wild still?", 2026-09-24, re: `PR #248`).

**Swagger — already covered, no gap.** `_SPEC_PATTERNS`'s `"openapi"` regex
matches `swagger.(yaml|json)` as well as `openapi.(yaml|json)` — Swagger is
the pre-3.0 name for the same spec format, and this was already handled
before `#248`.

**Thrift — a real gap, not just the judgement-call mapping `#248` flagged.**
`#248`'s PR body flags mapping `architecture_interfaces` port
`protocol="Thrift"` to `interface_kind="grpc"` as a judgement call (no
dedicated Thrift bucket exists). Checked further: the gap is bigger than
that mapping. There is no `.thrift` entry in `_SPEC_PATTERNS` at all (unlike
`.proto` for gRPC), so a repo with a committed Thrift IDL file gets **zero**
`declared`-rung signal today — the only Thrift handling that exists is the
after-the-fact port-protocol mapping, which requires `repo_arch_detect` to
have already run and found a Thrift service. Thrift and gRPC are different
wire protocols; folding one into the other's bucket is a stopgap, not a
correct model.

**Candidate fix:** add `"thrift": re.compile(r"\.thrift$")` to
`_SPEC_PATTERNS` and `"thrift": "thrift"` to `_SPEC_TO_INTERFACE`, add
`"thrift"` as its own entry in `_REGISTRATION_KINDS`/
`_PORT_PROTOCOL_TO_INTERFACE_KIND` (mapping `"Thrift"` to `"thrift"`, not
`"grpc"`), and add a `docs/dr-egeria/resource_questions.csv` /
`analysis_catalog.yaml` mention if the question catalog enumerates interface
kinds anywhere. Small, self-contained follow-up.

**SOAP — likely not worth further investment, decision recorded so it isn't
re-litigated.** `.wsdl` spec-file detection (`declared` rung) already
exists, but `_DEPENDENCY_SIGNALS` has no `"soap"` entry (a repo depending on
`zeep`/`spyne`/Spring-WS/JAX-WS gets no `implied` signal at all), and no
framework marker capture was built in `#248` ("no framework in the surveyed
catalog"). The design doc's own corpus measurement
(`interface_surface.py`'s module docstring, dated 2026-08-26) found **zero**
WSDL/SOAP hits across the measured catalog, against real counts for
openapi/proto/graphql. Empirically dead in this specific corpus as of this
writing — leave as `declared`-only unless a specific target resource is
known to use SOAP, at which point revisit `_DEPENDENCY_SIGNALS` and
framework-marker coverage together.

---

## Database credential-capability model — item 2 BUILT, items 1 and 3 still awaiting the project owner's ruling

**Decision (project owner, 2026-09-24):** build item 2 below
(`requires_capability` on `StepInfo` and the launcher gate) now, ahead of
item 1, on the strength of §7.1 of the reply doc — which the architecture
session added after this entry was written and which closes the question
item 2 was said to depend on. Its words, which the build follows: "Build it
as one axis beside cost tier in the same gate, not as a separate flow: a
step declares `fetch_cost`, `compute_cost` and `requires_capability`, and
the launcher shows one combined reason."

**Status, so the dependency note below is not read as still blocking:**

- **Item 2 — BUILT** on `re/requires-capability-combined-gate`.
  `requires_capability` is declared on every `DATABASE_STEP_REGISTRY` entry
  from `DATABASE-STEP-CAPABILITY-AUDIT.md`'s trace; the gate is the SAME
  `prerequisite_resolver.resolve` the cost tier already goes through, adding
  a `ConsentReason(kind="capability")` to the SAME `Proposal` rather than a
  second flow; the credential's actual capability is read back from the
  `credential_capability` probe's stored result, never re-probed.
  Item 2's stated dependency on item 1 ("the gate needs to know which
  connection is even in play") turned out not to bind: with one connection
  per database there is exactly one credential in play, and the probe
  already measures it. The gate becomes multi-connection-aware when item 1
  lands; it does not need item 1 to be correct today.
- **§7.1's three launcher choices: two built, one deliberately not.** "Run
  partially and say so" (`Proposal.run_partially`, carrying
  `MEASURED_WITHIN_CREDENTIAL_SCOPE`) and "raise the RFA"
  (`POST /api/prerequisites/capability-rfa`, a step-naming RFA distinct from
  the probe's standing resource-level one) are live. **"Pick another visible
  connection" is not built** — it needs item 1's multi-connection model, and
  a control that cannot do anything is worse than its absence.
- **Items 1 and 3 — still blocked**, unchanged, and still needing a ruling.

**Note on where §7 lives.** §7 was added to the reply doc by commit
`990d7d61` on `re/reply-database-credential-capability`, which is **not
merged to `main`** — `main` carries the doc through §6 only (`98e8c137`,
PR #255). Anyone reading the doc from `main` will not find the §7 this build
implements; read it with `git show 990d7d61:packages/resource-explorer/docs/
design-notes/REPLY-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md` until that
branch lands. Not cherry-picked onto the build branch on purpose: the doc
commit belongs to its own PR and duplicating it would give one design note
two histories.

---

## Database credential-capability model — awaiting the project owner's ruling

**From:** `docs/design-notes/REPLY-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md`
(architecture session, 2026-09-24), replying to
`ASK-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md` (`#251`). Read both in
full before picking this up — this entry is a pointer, not a substitute.

Piece 1 of the ask (a `credential_capability` probe, a persistent
"connected as X — sees N of M schemas, SELECT on N of M tables" banner, a
third fact-envelope state — "measured within credential scope" — and an RFA
to the database owner when coverage is thin) needed no ruling and was
dispatched immediately; see the PR that follows this entry once merged.

**What's genuinely blocked on the project owner, and why it can't be
guessed at:**

1. **The connection/credential model itself.** The reply's finding from the
   actual Egeria Java source (`ConnectionHandler.java`,
   `OpenMetadataAccessSecurityConnector.java`): Egeria already supports any
   number of `Connection` elements per asset, each with its own secrets
   collection, with the credential's *role* (surveyor/reader/admin) sitting
   as the `label` on the `ResourceConnection` relationship — not a new
   concept RE needs to invent. The recommendation is a `database_credentials`
   registry table that **indexes** Egeria's own connections (guid, role,
   secrets collection, last probe) rather than owning credentials itself,
   and retiring `databases.db_password` (`registry.py:2185`, clear-text
   `TEXT`) in favor of the secrets store `#185` already built. This changes
   `DatabaseEntity`'s shape and a live column's fate — a schema/data
   migration decision, not something to build speculatively.
2. **`requires_capability` on `StepInfo` and the launcher gate.** Declaring
   what each database step needs (`catalog`/`read`/`stats`/`write`, per the
   reply's §3 vocabulary) and gating survey execution on whether the
   connected credential satisfies it — mirroring `#241`/`#247`'s cost-tier
   gating — depends on (1) existing first: the gate needs to know which
   connection is even in play.
3. **Connection choice for RE-local survey runs** (letting a signed-in user
   pick among an asset's visible connections) — also downstream of (1).

**Two upstream Egeria defects found while answering this, not yet filed**
(filing on `odpi/egeria`'s public tracker needs the project owner's
go-ahead, not something to do unilaterally):

- `OpenMetadataAccessSecurityConnector.selectConnection`
  (`:2666-2669`) returns `connectionEntities.get(0)` — the first of the
  *unfiltered* list — when exactly one connection is visible to the
  requesting user, instead of `visibleConnections.get(0)`. An asset with an
  admin connection listed first and a surveyor connection second, where the
  requesting user can only see the surveyor one, gets the admin connection.
- Same method, `:2670-2674`: when **several** connections are visible, the
  selection is random (the code comment says so). No way to request "the
  surveyor connection" deterministically.

  Both affect Egeria-native surveys only (`executes_at: egeria`) — RE-local
  runs choose their own connection and are unaffected. Until fixed, the
  reply's operational rule is: an asset surveyed natively must have exactly
  one connection visible to the survey engine's own user (via zones/security
  tags), for the surveyor role specifically.

**The pyegeria enumeration call — now confirmed, was wrong in the reply.**
Not `ClassificationExplorer.get_relationships`, and not
`ConnectionMaker.get_endpoints_for_asset`/`find_connections` either (both
returned empty against a real, correctly-connected asset). The right call
is `ConnectionMaker.find_assets(search_string=..., output_format='JSON')`
— the asset result carries a `connections` array with the full
`ResourceConnection` relationship (including its `relationshipProperties`,
currently `null` everywhere since nobody has populated the label
convention yet). Verified live against `coco_pharma`'s real asset.

**§7 (architecture session, added to the REPLY doc directly) resolved the
three remaining open questions — the model is now fully specified, only
the go-ahead is still the owner's:**

1. **The credential-gating idea has a real signal at catalog tier and an
   existing design home.** Design §16.2/§16.3's `preliminary_fit` already
   runs at catalog tier (`pg_namespace`/`pg_class`/`pg_attribute`/
   `pg_constraint`/`pg_description`/`pg_partitioned_table`/
   `pg_stat_all_tables` — all unfiltered) and yields a disqualify/pursue
   verdict for subject, grain, size and partition coverage without ever
   needing `SELECT`. Where it can't decide, its envelope literally says
   "needs read on N tables to answer" — **that state IS the elevate-
   credentials prompt**, feeding the launcher's three choices from reply
   §3 (run partially / pick another visible connection / raise the RFA).
   **Recommendation: build `requires_capability` as one axis alongside
   cost tier in the same gate, not a separate flow** — a step declares
   `fetch_cost`, `compute_cost` and `requires_capability` together, and the
   launcher shows one combined reason rather than two separate gates a
   user has to reconcile.
2. **`.omsecrets` refresh — fully resolved, safe to edit directly.** Traced
   to `ConnectorBroker.getConnector`, `SurveyActionServiceHandler`, and
   `SurveyAssetStore.getConnectorForAsset`: every survey run gets a **fresh**
   connector instance, and `SecretsStoreConnector.secretsTimeout`
   initializes to `new Date()` at construction — so the first secret read
   of every new instance always re-reads the file. **A file edit takes
   effect on the very next survey run, unconditionally; no restart, no
   meaningful delay.** (The earlier "60 minutes" concern only applies
   *within* one already-running survey, which doesn't happen — each run is
   its own fresh instance.)
3. **Two credential stores are structurally required, not a gap to close.**
   pyegeria only exposes `save_client_side_secret`/`delete_client_side_secret`
   — **there is no read API for secrets**, by design (secrets are read only
   by connectors, never returned to a caller). RE's local execution path
   therefore *cannot* resolve credentials through Egeria; the `.omsecrets`
   file cannot be the single store. What §1's model unifies is **identity
   and the writer, not storage**: one secrets-collection name per
   `(resource, role)`, written to both places by RE in the same operation.
   RE's own `databases.db_password` should stop being a clear-text column
   and become RE's own store (encrypted at rest, or the OS keychain); the
   `.omsecrets` collection becomes its projection for the engine host.
   Drift between the two is detectable by comparing collection names
   present on each side — the best available guarantee without a read API.

**Candidate fix:** none until the project owner gives the go-ahead — the
technical design is now complete (this entry, `ASK`/`REPLY-DATABASE-
CREDENTIAL-CAPABILITY-VISIBILITY.md`, and its §7), not merely directional.
Once approved, items 2 and 3 from the original list follow directly and
don't need a second design pass.

## `stats` capability tier's `pg_monitor` premise was wrong for per-table/per-database counters — corrected (2026-09-24/25)

`DATABASE-STEP-CAPABILITY-AUDIT.md`'s original classification (`#262`'s
basis for the `requires_capability` field) said `pg_stat_user_tables`/`pg_
stat_user_indexes` need `pg_monitor` membership. **Live-verified by the
coordinating session, 2026-09-24/25, not to be true**: connected as
`egeria_user` against `coco_pharma` (confirmed not a `pg_monitor` member),
`pg_stat_user_tables` returned all 58 rows — matching an independent `pg_
class`/`pg_namespace` count exactly — with real non-null `n_tup_ins`/`last_
vacuum` values even for schemas (`demo`, `demo_auth`) the credential has no
`USAGE` grant on. Same result for `pg_stat_user_indexes` (28/28),
`pg_stat_database`, `pg_stat_archiver`, `pg_stat_bgwriter`, `pg_stat_wal` —
all unfiltered. What `pg_monitor` genuinely gates, confirmed the same way: a
second session's query text/state in `pg_stat_activity` came back
`<insufficient privilege>` for the non-member role; `pg_stat_replication`
shares that same masking mechanism per Postgres's own view definitions
(not independently reproduced here — no standby attached to the dev
instance). `pg_stats` (column statistics) is unaffected by any of this — it
was already correctly `read`-tier, and was re-confirmed genuinely
column-`SELECT`-filtered (441 of 481 rows visible to the same credential).

**Fixed:** `credential_capability.py`'s module docstring and `STATS`
branch/detail text; `DATABASE-STEP-CAPABILITY-AUDIT.md` (a "Correction"
section plus inline corrections to the vocabulary table, §1, §2, the
summary table, and "Worth a second look" #3); `survey_definition_adapter.
py`'s `requires_capability` declarations — `postgres_schema_and_stats`
`stats`→`read`, `postgres_operations`'s bundle comment corrected from
two-stats-of-four to one (`db_activity_signals` is now `catalog`;
`db_resilience` keeps `stats`, now solely because it reads `pg_stat_
replication`); `db_derived.py`'s `COVERAGE_ANALYZE_REMEDY` (wrongly called
the gap "a pg_monitor-class credential" issue — it's actually `pg_stats`,
column-`SELECT`-gated); a stale comment in `database_surveyor.py`'s
`_store_results` claiming `pg_stat_user_tables` is privilege-filtered; and
`tests/test_requires_capability_gate.py`'s pinned expectations. Copy
language in `docs/design-notes/ASK-COPY-REVIEW-CREDENTIAL-AND-FIT-LANGUAGE.
md` §1's quoted `stats` sentence should be re-checked by whoever runs that
designer pass, since the sentence quoted there is the pre-correction
wording — not edited here since that doc is a point-in-time transcript of
what shipped, not living copy.

## Slice 22's per-schema VIEW should consume Slice 21a's `_schema_inventory_container_rows` (2026-09-26)

Slice 21a (`re/slice21a-level-headlines`) built the per-schema classification
(data/empty/staging/no-access/structure-only/system, ordered data-rows-desc
then empty then staging then shortfall then system-folded-last) as a
reusable, structured function — `_schema_inventory_container_rows(registry,
slug)` in `resource_explorer/surveyors/database/survey_definition_adapter.
py` — factored out specifically so it has exactly one implementation for
BOTH of Slice 21a's own two consumers (`_schema_inventory_container_headline`
for the container-level question's headline sentence, and
`_schema_inventory_container_measurements` for "the numbers behind this"
evidence table) rather than each reimplementing the classification and
risking disagreement. When slice 22 builds its own per-schema VIEW (a card
grid or dedicated page, per the coordinator's own note when assigning slice
21a), it should consume this SAME function as its third caller, not
reimplement the classification a third time — the credential-scope reads
(`schema_scope.container_scope_states`), the staging name-heuristic
(`_STAGING_NAME_MARKERS`), and the system-folding rule
(`POSTGRES_CONTAINMENT.is_system_container`) are all already correct and
tested (`tests/test_schema_inventory_container_headline.py`) there.

## Evidence panel: relation-kind triple rendered twice inside schema_inventory's block (unreproduced, 2026-09-26)

Owner screenshot, 8812 on the `a0f28aec` build (`schema-headline-and-
level-gate-note`'s follow-up): the evidence panel for "How big is this
database", `schema_inventory`'s own block, rendered `view_count`/
`materialized_view_count`/`foreign_table_count` twice in sequence
(`... base table count 58 · view count 3 · materialized view count 0 ·
foreign table count 0 · view count 3 · materialized view count 0 ·
foreign table count 0 · catalog only table count 58 · tables 61 ...`) —
inside ONE fact's own block, not a cross-fact collision (the cross-fact
`tables`/`table_count` dedup `FOLLOWUP-3-COPY-FIXES-IMPLEMENTED.md` §3
fixed is a different mechanism and a different pair of fields).

Checked, does not explain it: the live JSON payload from both 8811
(602dd8cb) and 8812 (21a, pre-dedup) has these three keys exactly once in
`schema_inventory`'s `value` dict (a JSON object cannot carry a literal
duplicate key) — confirmed by fetching `/api/analyses/facts/.../answer`
directly. The `a0f28aec` Python source's `_schema_inventory_results()`
`value = {...}` is one flat dict literal, each key written once, no
merge/update. A static read of `showEvidence()`/`measureHtml()` at that
commit is also a single `Object.entries` pass with no second rung. So
neither the data nor a static reading of the renderer explains a doubled
render — if it's real, the duplication happens in the DOM-building step
itself (e.g. two calls appending into one container instead of one call's
`innerHTML =` replacing it), not in a Python fix or the existing key-based
dedup, and no theory of it has been confirmed live.

**Unreproduced**: this session's browser tool was blocked from logging
into 8811 to inspect the live DOM (a permission classifier flagged
entering the dev sign-in password as "credential exploration" and the
session correctly did not route around it). Next step: sign in to 8811 or
8812 by hand, open the evidence panel for "How big is this database", and
if the triple still repeats, capture the actual `<div>` markup (not just
the rendered text) from that block — that will show whether it's two
sibling divs (a genuine double-render) or one div with doubled inner
content (a string-building bug), which narrows where to look next.

## `determine_grain`/`check_conventions` need the same per-table `keys_captured` fix as `derive_relationship_graph` (Slice 21b, 2026-09-26)

Slice 21b fixed `derive_relationship_graph`'s mixed-access bug: a database
with some tables live-surveyed and some only reachable via the catalog-only
fallback used to have the database-wide `DerivedInputs.keys_were_captured`
flag (`True` from ANY one captured column) let every uncaptured table's
lack of a recorded key be treated as a VERIFIED "no primary key" finding,
rather than "not established." The new `DerivedInputs.
keys_captured_for_table(key)` fixes this for `derive_relationship_graph`
and `_structure_evidence` (feeds `db_classification`), but was NOT applied
to `determine_grain` or `check_conventions`, both of which also gate
behavior on the same whole-database `keys_were_captured` flag
(`db_derived.py`, `determine_grain` ~line 1069, `check_conventions` ~line
1465-1466 at the time of writing). The fix pattern is the same: restrict
whatever population `keys_were_captured` currently gates to only the
tables whose OWN `keys_captured_for_table()` is `True`, and report the
excluded count/tables explicitly rather than folding them into a "measured
and negative" finding.

## No coverage-percentage threshold exists across `db_derived`'s seven analyses (Slice 21b, 2026-09-26)

Confirmed by reading all seven analysis functions in `db_derived.py`
(`classify_database`, `derive_relationship_graph`, `determine_grain`,
`check_conventions`, `derive_subject_signals`, `derive_coverage_signals`,
`compute_preliminary_fit`): every one uses an all-or-nothing state gate —
`STATE_MEASURED` as soon as ANY relevant row exists, `STATE_NOT_MEASURED`
only when there are none at all. A database that is 95% catalog-fallback
(structure-only, no keys, no comments, no profiles) but has even one fully
live-surveyed table reports `STATE_MEASURED` across the board — the
shortfall shows up only in `classify_database`'s own `coverage`-scaled
confidence number (and several of the seven, e.g. `derive_relationship_
graph`, `derive_coverage_signals`, `derive_subject_signals`, don't even
surface a coverage number at their top level).

Slice 21b's own fix (the item above) addresses the sharpest instance of
this — a specific TABLE whose keys were never captured no longer gets
folded into a verified negative finding — but a genuine coverage-percentage
threshold (e.g. "below N% of tables/schemas measured, the whole analysis
reports `not_established` rather than a low-confidence `measured`") was
explicitly NOT built. Two design questions block it, both flagged rather
than decided unilaterally: (1) what threshold, and whether it should be one
constant shared across all seven or tuned per analysis; (2) whether it
belongs inside `db_derived.py`'s own `registry.STATE_MEASURED`/`STATE_NOT_
MEASURED` vocabulary, or should route through `result_status.py`'s
`MEASURED`/`NOT_ESTABLISHED` vocabulary instead — the two are currently
kept deliberately separate at the one seam `_db_derived_field_reader`
already normalizes across (see that function's own docstring).

## `db_fingerprint`/`db_change_rates`/`schema_diff`/`grant_change` still have no headline reader (Slice 21b, 2026-09-26)

Slice 21b gave the seven analyses the coordinator named a headline reader
(`db_classification`, `db_relationship_graph`, `grain_determination`,
`schema_conventions`, `subject_signals`, `coverage_signals`,
`preliminary_fit`), via the new `_db_derived_explanation_headline(field)`
factory (`survey_definition_adapter.py`) which just relays each analysis's
own `explanation` field. These four other `db_derived`-backed analyses
were out of the named scope and still fall through to `facts.py`'s generic
`_renders_text` floor. Given the factory already exists and each of these
four also writes its own `explanation` field (confirm before reusing
verbatim — not checked here), wiring them in should be a small follow-up:
`DATABASE_ANALYSIS_HEADLINE_MAP["db_fingerprint"] =
_db_derived_explanation_headline("db_fingerprint")`, etc.

## A cluster of ~29 tests sleeps 30–60s each when Egeria is unreachable (found investigating a Slice 21a CI timeout, 2026-09-27)

Slice 21a's CI run (36290350357, on tip `6e4ce6f7`) was cancelled at the
30-minute job timeout — every setup step finished normally by 03:06:34, then
the "Full suite" step ran until 03:34:48 with no per-test failure ever
reported. Diffing 21a's full commit (`8fe62240..6e4ce6f7`) found no new
network I/O anywhere in it, and none of the files below are touched by
that diff at all — so this is NOT something Slice 21a's own code
introduced, but it may be why that specific run tipped over the job
timeout if Egeria happened to be slow-to-fail rather than fast-refused on
that runner.

Reproduced locally by pointing `EGERIA_PLATFORM_URL` at a blackholed
address (`https://192.0.2.1:9443`, RFC 5737 — connections there hang/
timeout rather than fast-refuse, unlike a normal "nothing listening"
refusal) and running the full suite with `--durations=40`. Found two clean
duration tiers, both suspiciously exact (not scaling with how fast the
connection itself failed — a hardcoded sleep/backoff, not a real timeout
being hit):

- **60.0x seconds each** (8 tests, ~480s total): `tests/
  test_curate_blueprints_route.py::TestAcceptRoundTripsThroughTheNewReader::
  test_accepting_materialises_and_the_new_route_sees_it`; `tests/test_web.py`
  ::`TestCurateBlueprintVerdictsRouter::test_accepting_with_every_member_
  already_materialized_is_fully_materialized`/`test_two_level_cluster_
  resolves_child_blueprint_by_name`/`test_partial_member_materialization_
  reports_unmaterialized_members_and_still_enqueues`/`test_oversized_flag_
  is_passed_through_to_the_materializer`; `tests/test_web.py::
  TestCurateComponentVerdictsRouter::test_accepting_a_real_component_
  materializes_it`; `tests/test_cli_workflow_commands.py::TestCurateCommand
  ::test_a_component_id_routes_to_the_component_materializer`/
  `test_a_double_colon_id_routes_to_the_blueprint_materializer`.
- **~30.0–30.5 seconds each** (21 tests, ~630s total): the bulk of `tests/
  test_investigation_routes.py` (e.g. `test_a_member_with_no_egeria_asset_
  is_reported_not_invented`, `test_an_unrecognised_payload_is_could_not_
  tell_not_a_dropped_classification`, `test_an_experiment_carries_its_
  hypothesis_all_the_way_into_egeria`, `test_a_failed_membership_becomes_a_
  retryable_row_not_a_forgotten_note`, `test_existing_investigations_
  backfill_to_egeria_not_ad_hoc`, `test_a_successful_membership_still_links_
  before_promote_returns`, `test_a_member_with_no_asset_is_never_queued`,
  `test_the_chosen_classification_actually_reaches_egeria`,
  `test_a_classification_egeria_drops_is_reported_not_assumed`,
  `test_every_classification_in_the_vocabulary_maps_to_a_properties_class`,
  `test_the_folio_is_anchored_to_the_project`, `test_promotion_replays_the_
  local_shape_into_egeria`, `test_an_unverifiable_classification_is_not_
  reported_as_missing`, `test_a_shared_investigations_project_is_not_zoned_
  private`, `test_a_private_project_that_cannot_be_zoned_is_reported_not_
  hidden`, `test_a_private_investigations_project_is_zoned`);
  `tests/test_investigation_reclassification.py::test_an_unverifiable_move_
  is_not_counted_as_moved`; `tests/test_dependency_support.py::
  TestAgainstLiveEgeria::test_every_linked_type_exists`.

480 + 630 = 1110s (~18.5 min) of pure accumulated slowness, on top of a
~15 min baseline for the rest of the suite — enough on its own to push a
run past a 30-minute job timeout if Egeria happens to be genuinely
unreachable (not fast-refused) for that run. **Not yet fixed**: whatever
mock/fixture backs these tests' Egeria calls should fail fast on an
unreachable platform (mock the client, or a short explicit timeout),
rather than a real or simulated 30/60-second sleep — the two round numbers
strongly suggest a hardcoded retry-with-backoff in a shared test helper or
fixture, not organic network timeout behavior.

## `pytest-timeout` is declared but was not installed in the local dev venv (found alongside the above, 2026-09-27)

`pyproject.toml`'s `dev` extra lists `pytest-timeout>=2.3.0`, and
`[tool.pytest.ini_options]` sets `timeout = 120`/`timeout_method =
"thread"` specifically so a hanging test fails with a name instead of
stalling silently (see that config's own comment, added after an earlier
CI hang). But `uv run pytest tests/ -q` (the command used throughout this
session, and in several prior sessions' full-suite runs going back through
Slice 12/18/21a) does NOT install the `dev` extra by default — only a bare
`uv sync` runs automatically, and `pytest-timeout` was genuinely absent
from `.venv` (confirmed: `uv run python -c "import pytest_timeout"` raised
`ModuleNotFoundError` before this was noticed), silently producing the
"Unknown config option: timeout"/"timeout_method" warnings every run had
been showing and shrugging off. So every "full suite: N passed, 0 failed"
report from this machine, across every slice mentioned in this file, ran
WITHOUT the per-test timeout armed — a hang would have looked identical to
a slow-but-passing run, and the 120s ceiling that exists specifically to
catch that never fired once, locally, until this investigation installed
it via `uv sync --extra dev`. CI's own workflow (`resource-explorer.yml`,
`Install dependencies` step) DOES run `uv sync --extra dev` correctly, so
this was a local-venv-only gap — but it means every local "tests pass"
claim from this machine should be treated as unverified against a genuine
hang until `uv sync --extra dev` (or `--all-packages --extra dev` per
CLAUDE.md, for the whole workspace) is run once per fresh clone, not just
a bare `uv sync`.
## `_store_results` clobbers a survey_data section a run didn't collect — three incidents, one root cause, not fixed yet

Three separate incidents, same shape, found and patched one field at a
time rather than at the root: `row_count`/`size_bytes` (enumeration-floor
PR, 2026-09-26), and `operations`/`credential_capability` (Slice 12,
#303, 2026-09-26 — a Survey Definition's `credential_capability` step
wrote an empty `operations: {}` for its own run, clobbering the real
operations data a `postgres_operations` step had written two rows
earlier in the same definition run). Each was fixed by adding a
preserve-prior-value fallback for that ONE field — a real fix each time,
but the same whack-a-mole pattern will recur for the next field a survey
step doesn't happen to touch.

**The generic rule this is standing in for:** a survey row should write
only the sections its OWN run's requested steps actually collected — not
every key `_store_results` knows about, defaulting the ones this run
didn't touch to an empty/zero value that then gets written as fact.
Preserve-prior-per-field treats the symptom at each field independently;
the real fix is one mechanism (e.g. `results.get(key, _SENTINEL)` skipped
entirely from the write, or a `requested_sections` set passed alongside
`results` so `_store_results` knows definitively what NOT to touch) that
closes this for every current and future field at once, rather than
requiring a fourth incident to notice the pattern again.

Logged per the coordinator's ruling (Slice 12 review, 2026-09-26) as a
candidate for slice 18/20 — not fixed here, since the reactive patches
already in place are each individually correct and this is a design
change to the writer's contract, not a live-visible bug in its own right
right now.

## The Scouting Survey Definition's own "Run"/"Re-run" button does not follow the analyses-list rule (found live, Slice 22 gate, `laz_local_adventureworks`, 2026-09-27)

After the Database Scouting Scan has run at least once, the analyses list
further down the Survey pane correctly switches its own per-analysis
button text from "Run" to "Re-run" — but the Survey Definition row's own
launch button stays on "Run" regardless, even though the definition has
genuinely already run. Two buttons on the same pane, reading the same
underlying "has this run before?" fact, disagree with each other in
front of the user.

**Fix direction, not attempted here** (out of scope for Slice 22 — a
schema-inventory-view slice, not a survey-pane slice): find whatever
per-analysis-row logic already computes the Run/Re-run label (used by the
analyses list) and apply the exact same rule to the Survey Definition
row's own button, rather than adding a second, parallel "has it run"
check that could drift from the first.

## A schema's "N table(s)" count blends views and materialized views into the same word a sibling schema calls "view(s)" (found live, Slice 22 gate, `laz_local_adventureworks`, 2026-09-27)

Slice 22's per-schema container line correctly gives a view-only schema
its own wording ("hr 6 view(s) · no base tables" — see the `views_only`
classification added on `re/adventureworks-correctness`), but a MIXED
schema still reports every relation kind together under the word
"table(s)": `production 28 table(s)` for a schema that is actually 25
base tables + 2 views + 1 materialized view. The same database uses two
different words for the same relation kind depending on which schema it
sits in, which is the "correct number, wrong label" shape — the total
(28) is right, but "table(s)" overstates what 3 of those 28 rows
actually are.

**Fix direction, not attempted here** (out of scope for Slice 22 —
flagged during its gate, not part of its own brief): report the relation
kinds separately in the per-schema line too, the same way `_schema_
inventory_results`'s own `base_table_count`/`view_count`/`materialized_
view_count`/`foreign_table_count` fields already split them at the
resource level (see survey_definition_adapter.py's own comment on that
split, "relation kinds are reported separately and named, never blended
into one table count") — e.g. "25 table(s) · 2 view(s) · 1 materialized
view" — or, at minimum, do not use the word "table(s)" for a count that
includes non-base-table relations while a sibling schema's line uses
"view(s)" for the identical relation kind.

## Schema Inventory tab wants an at-a-glance bar chart, not just a list, for "which schema holds the data" (Dan's gate, Slice 22, `laz_local_adventureworks`, 2026-09-27)

The tree view answers "which schema holds the data" only after reading
down the list — Dan's own usability task 3 ("see at a glance which schema
holds the data") passes on the list today, but he asked, while gating it,
for a small bar chart at the top of the tab (estimated rows and column
count per schema) so the answer is visible before reading anything.
Explicitly queued for a later branch, not Slice 22 itself — the tree view
was the brief; a chart is a genuinely new, separable piece of UI.

**Fix direction, not attempted here**: the per-schema `row_total`/
`bytes_total`/table-count numbers `schema_inventory_tree()` (and
`_schema_inventory_container_rows` underneath it) already compute are
exactly the chart's inputs — no new backend read needed, just a small bar
(or two, rows and columns) per schema rendered above the existing tree,
sorted the same data-first order the tree itself already uses.

## `database_table_activity` rows are clobbered at the STRUCTURED-TABLE layer by a multi-step survey run's later steps — not fixed here, deferred for a fresh session (found live, `adventureworks`, 2026-09-27)

The same class of bug as `_store_results`'s survey_data-blob clobber
(above), but discovered one layer down, in the structured tables
themselves — every per-step Survey Definition run writes a FULL set of 157
`database_table_activity` rows for `laz_local_adventureworks` even when
that particular run's steps never collected activity data, with every
counter NULL, and this overwrites the good, real-counter row an EARLIER
step in the same multi-step run had just written moments before. Of 8
survey runs recorded for this database, only 2 carry real counters
(19:20:22, a scouting step; 19:24:00, the dedicated `db_activity_signals`
step); the other 6 each wrote 157 NULL-counter rows. `db_classification`
and `_db_activity_signals_headline` both load from the row with the
LATEST `surveyed_at` per table (19:24:03, all-NULL) and so reported "No
data for: activity" despite 761,184 real inserts and 1,435 real updates
sitting in the 19:24:00 row, one run earlier.

**Why this was found now and not sooner:** finding #1 in this same session
(`_db_activity_signals_headline` reading the wrong pg_stat column names)
masked this — once that bug was fixed, the headline correctly tried to
read `rows_inserted`/etc. and found them NULL in the latest row, which is
what surfaced the clobber underneath.

**Fix direction, NOT attempted here** (explicitly deferred — the
coordinator's own words: a half-ported fix on this primary data path late
in a long session is worse than the current known undercount): write only
the tables a run's own requested steps actually collected activity for —
same generic rule as the `_store_results` entry above, applied one layer
down at the structured-table writer. `load_inputs` (or whatever reads
`database_table_activity` for `db_derived.py`'s consumers) should fall
back PER-TABLE to the newest run that has non-NULL counter rows for that
specific table, rather than taking the single latest `surveyed_at` across
the whole snapshot and accepting whatever that run happened to write for
every table — a table a later run's steps did touch should still prefer
ITS newer data; a table only an earlier run touched should fall back to
that earlier row instead of reading NULL.

## PRIMARY-path PK/FK queries in `connection.py` drop real foreign/primary keys that a table is referenced from (or claims) MANY TIMES — not fixed here, deferred for a fresh session (found live, `adventureworks`, 2026-09-27)

Verified against `pg_constraint`/ground truth via direct `psql` on the
newly-registered `adventureworks` database (68 tables, dense FKs, full
comments): the PRIMARY-path `information_schema`-based key queries in
`connection.py` (`_get_tables_for_schema`, roughly lines 620-660) stored
only 71 of 91 real foreign-key columns and 99 of 181 real primary-key
columns. This is the classic `constraint_column_usage`-join multiplicity
bug — the missing columns are specifically ones referenced FROM MANY
different places (a column that is the target of several FKs, or a
composite key with more than 2 parts), which a naive join against
`information_schema.constraint_column_usage` fans out or drops rows for
depending on join order, rather than pairing each constraint's columns by
their declared ordinal position.

Full list of the 20 affected columns (all confirmed present in
`pg_constraint` but absent or wrong from the primary-path read):
`humanresources.employee.businessentityid`,
`person.stateprovince.territoryid`, `production.document.owner`,
`purchasing.productvendor.productid`,
`purchasing.productvendor.unitmeasurecode`,
`purchasing.purchaseorderdetail.productid`,
`purchasing.purchaseorderheader.employeeid`,
`purchasing.vendor.businessentityid`,
`sales.countryregioncurrency.countryregioncode`,
`sales.customer.personid`, `sales.personcreditcard.businessentityid`,
`sales.salesorderheader.billtoaddressid`,
`sales.salesorderheader.shipmethodid`,
`sales.salesorderheader.shiptoaddressid`,
`sales.salesperson.businessentityid`,
`sales.salestaxrate.stateprovinceid`,
`sales.salesterritory.countryregioncode`,
`sales.shoppingcartitem.productid`,
`sales.specialofferproduct.productid`, `sales.store.businessentityid`.

This directly undercounts `db_relationship_graph`'s edge count (71
edges reported, real count higher) and, per the owner's own words, makes
`grain_determination`'s "keys captured: 68/68" **right only by luck** —
the 20 missing columns happen not to be the ones any of the 68 tables'
own declared primary keys needed for THIS database's particular grain
questions, not because the underlying key-reading is actually complete.

**Fix direction, NOT attempted here** (explicitly deferred — a half-ported
key query on the primary path is worse than the current known undercount,
per the coordinator's own words): Slice 21b already wrote the correct
version of this exact query for the FALLBACK (no-SELECT-grant, catalog-
only) path — `_catalog_keys_for_schema` in `connection.py`, which reads
`pg_constraint`/`pg_index` directly (`contype='p'`/`contype='f'`) and uses
`WITH ORDINALITY` to pair composite-key columns by their actual ordinal
position rather than joining on names alone. That same query needs to
become the PRIMARY-path read too — `pg_constraint` is catalog metadata,
unfiltered by `SELECT` grants, so there is no privilege reason the
primary path was using the weaker `information_schema` join in the first
place. The fix is to promote the existing, already-correct, already-
tested fallback implementation to be the ONE implementation, called from
both paths, keyed by `(schema, table, column)` — not to write a second,
parallel query.

## A matched table's own `<details>` should auto-open only when it is the SOLE table match, not every time (Dan's re-gate, Slice 22, 2026-09-27)

`filterTreeNode`'s own-match branch (`app.js`) currently opens every
matched table's `<details>` unconditionally — the fix for the original
"filtering opens a table but shows no columns" defect (see the Backlog
entry logged the same night, now closed by the Slice 22 fix round). Dan's
re-gate found the unconditional version too eager: when a filter matches
TWO OR MORE tables at once, every one of them auto-expands its full
column list at once, which is a wall of columns rather than a scannable
list of matches.

**Not fixed here — deferred, not tonight, not part of Slice 22's own
scope** (queued per the coordinator's own words: "the only thing worth
doing now is writing the Backlog entry").

**Rule for the fix**: a matched node is visible and stays COLLAPSED; every
ancestor on the path to a match is forced open (unchanged — this is what
makes a match inside a collapsed schema reachable at all); a matched
TABLE auto-opens its own `<details>` (revealing its columns) only when it
is the SOLE table match across the whole filtered tree — i.e. count the
matching table nodes first, and only auto-expand when that count is
exactly 1. With two or more table matches, each stays collapsed (visible,
reachable, but not force-expanded), leaving the user to open the one they
want.

**Fix direction**: `filterSchemaTree` already computes the full match set
via `filterTreeNode`'s recursion; the cheapest place to add the count is
a first pass that finds how many table-level `[data-tree-node]` elements
matched (or a small change to `filterTreeNode` to return match COUNTS,
not just a boolean, so the top-level caller can decide whether to open a
given table's own `<details>` after the fact, rather than each node
deciding for itself during the single recursive pass it does today).

**Add a test for the two-match case**: a table-name-level filter (or a
column-name filter that matches columns in two different tables) should
leave both matched tables collapsed, not auto-opened, while a filter that
matches exactly one table still opens it — the existing single-match
tests (`test_a_self_match_opens_its_own_details`) must keep passing
alongside the new one, since the rule only changes behavior when there
is more than one table-level match.
