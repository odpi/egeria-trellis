# db_derived step: implemented

**Replies to:** `COORDINATOR-BRIEF-MULTI-RESOURCE.md`, Phase 1 slice #9 —
*"`db_derived` zero-fetch step: classification, grain, fingerprint,
conventions checks, change rates, proposed scope as a measured annotation
(design §5.3, §7 of the support doc for key names)."* Gate: slices 3
(structured tables, PR #180) and 7 (`postgres_schema_and_stats` extension,
PR #191) — both merged and on `main`, so this branch is cut from `main`
directly and its diff is slice 9's changes only.

**Implements:** `multi-resource-questions-design.md` §5.3 (the whole Discovery
row set except `sql_analysis`, which exists, and `db_hub_tables`, which is
scoped out below), §5.4's `schema_conventions`, `db_change_rates` and
"what is the data's scope in time" → proposed `DataScope` rows, and §5.7's
`db_derived` line ("reads stored rows only — none / low — Classification,
DataGrain, Fingerprint, conventions checks, change rates, proposed
DataScope"). Follows `egeria-support-for-multi-resource.md` §3's
project-owner decision on `contentStatus: DRAFT` for proposals and §7's
`DataScopeProperties` key-name convention for measured scope.

**Branch:** `re/db-derived-step`.

**Tests:** 63 new tests in `tests/test_db_derived_step.py`, plus additions to
`tests/test_annotation_check_names.py` (see below — a pre-existing guard was
found to be skipping three annotation classes entirely). Full
resource-explorer suite: **5,408 passed, 103 skipped, 1 failed** — the one
failure proven pre-existing (see "What could not be tested").

---

## The shape of the step, and why it is not a `DatabaseSurveyor` step

Slices 7 and 8 added steps inside `DatabaseSurveyor.survey()`.
**This one could not go there**, and that is the single most important
structural decision in the slice.

`survey()` opens `with database_connection(self.db_entity, self.credentials)`
*before* it dispatches any step, and every step runs inside that block. A
zero-fetch step living there would open a connection it never uses — paying
the cost design §5.7 prices at `none` — and, worse, would be **unable to run
at all** for a database whose credentials are missing or whose server is
down, which is precisely the situation in which reasoning over already-stored
rows is most valuable.

So `db_derived` is a module of its own,
`resource_explorer/surveyors/database/db_derived.py`, entered through
`run_db_derived(registry, slug)` — a registry and a slug, nothing else. Three
consequences, each wired deliberately:

- **The adapter handler takes no credentials.**
  `survey_definition_adapter._run_db_derived(db_entity, registry, **_)` has no
  `db_user`/`db_pwd` parameters at all, unlike every other handler in that
  module. A test asserts that by signature inspection: a handler that accepts
  credentials invites a future edit to use them.
- **The web per-card route bypasses the credentials check.**
  `web/routes/databases.py`'s per-card `run` action refuses any analysis
  without stored `db_user`/`db_password`. The six `db_derived` ids are
  dispatched *before* that check, so they answer for a database RE can no
  longer connect to. A test covers it (`test_it_answers_for_a_database_with_
  no_credentials`).
- **`DATABASE_ANALYSIS_STEP_MAP` is untouched.** It maps an analysis id to
  `DatabaseSurveyor.survey()` steps; adding these ids to it would route them
  straight back into the connection-opening path. `DB_DERIVED_ANALYSES` in
  the new module is the one list the route and the adapter both read.

`TestZeroFetch::test_no_connection_is_ever_opened` monkeypatches
`database_connection` to raise, then runs the whole step and asserts it
produced annotations. That test was confirmed to fail when the property is
broken (see "The guards were made to fail on purpose").

---

## What was built, per check

### 1. `db_classification` — and how the confidence is actually computed

Four **signal families**, each weighted, each contributing an evidence score
in [0,1] per candidate kind (`transactional`, `analytical`, `reference_data`,
`staging`, `copy`):

| Family | Weight | Reads |
|---|---|---|
| `structure` | 3 | PK coverage, FK density per table, average column width, row-count skew, small-table share |
| `naming` | 2 | table-name prefixes/suffixes (`stg_`, `dim_`/`fact_`, `ref_`/`lookup_`, `_copy`/`_bak`/`_v2`) |
| `activity` | 3 | read/write mix from the stored tuple counters: mutate share, insert share, seq-vs-index scan share, load-then-truncate churn |
| `fingerprint` | 2 | best structural similarity to another database in the registry (check 4's own output) |

**A family with no usable data is excluded from the denominator, never scored
as zeros.** This is the mechanism behind the brief's named failure mode: an
absent tuple counter must not read as "no writes and no reads", which is the
staging/idle signature. Confidence is then

```
confidence = 95 × coverage × (0.35 + 0.65 × margin) × min(1, 2 × best_score)
```

— three separately disputable factors: `coverage` (how much of the total
signal weight was available), `margin` ((best − second) / best, how clearly
the winner won) and the absolute strength of the winning evidence. Below
confidence 25 the step reports **no kind at all** rather than naming the
front-runner, and 95 is a hard ceiling: an inference never reaches certainty.
`test_confidence_never_reaches_certainty` pins the ceiling and
`test_missing_tuple_counters_lower_confidence_and_do_not_mean_staging` pins
the mechanism by classifying the same schema twice, with and without counters.

### 2. `db_relationship_graph`

FK edges come from `database_columns.foreign_key_json` (shape
`{foreign_schema, foreign_table, foreign_column}`, written by the blob
back-fill from `connection.py`'s `fk_lookup`). The step builds the undirected
adjacency, counts connected components, lists isolated tables, and ranks
tables by FK in-degree. Verdicts: `data_model` (≥70% of tables connected),
`partial_model`, `bag_of_tables`.

Two details worth naming. A FK whose target table is not in this snapshot is
recorded as a **dangling reference**, not as an edge — it is a cross-schema
target the survey did not cover, not a broken constraint. And views and
materialised views are excluded from every structural check
(`_is_base_table`), because a view has no grain to propose and no primary key
to be missing, and counting it as "a table with no PK" would produce a
conventions finding nobody can act on.

### 3. `grain_determination`

Per table, in order of strength:

1. **Declared primary key** → `one row per <pk cols>`, basis `primary_key`,
   confidence 90. A date column *inside* the key sets `interval`.
2. **A near-unique column from the stored profile** → `one row per <col>`,
   basis `unique_column_exact` (≥0.99 distinct/row, confidence 65) or
   `unique_column_estimated` (≥0.95, confidence 50).
3. Otherwise one of two *different* non-answers — see the absence table.

**A real correctness trap handled here:** `pg_stats.n_distinct` is **negative
to mean a fraction of the row count**, with −1 meaning every value is
distinct. Slice 7 stores the raw figure (`"distinct_count":
stat.get("n_distinct")`), so a consumer reading it as a plain count sees −1
distinct values in a perfectly unique column — the exact opposite of the
truth. `_distinct_estimate()` resolves the sign convention against the stored
row count, and returns `None` (not a number) when a negative fraction has no
row count to resolve against, because an unresolvable estimate is not a
measurement. `test_a_negative_n_distinct_is_a_fraction_not_a_count` covers it.

Each determined grain is published as a `DataGrainAnnotation` with
`contentStatus: DRAFT` — a **proposal**, per support doc §3.

### 4. `db_fingerprint`

Signature = the sorted set of `schema.table.column:base_type` strings, SHA-256
digested (stable and order-independent — tested). Compared against every
*other* database in the registry that has stored schema rows; still zero-fetch,
since the peers' signatures come from their own stored rows.

Both **Jaccard** and **containment** are computed, because they catch
different things and the design asks for a similarity *measure*:

| Verdict | Rule |
|---|---|
| `likely_copy` | column Jaccard ≥ 0.95 |
| `likely_subset_of` | containment(this in peer) ≥ 0.90 and the peer is larger |
| `likely_superset_of` | the mirror of the above |
| `shares_structure` | Jaccard ≥ 0.50 |
| `incidental_overlap` | Jaccard ≥ 0.30 |
| not reported | below 0.30 |

Jaccard alone cannot detect a subset — a true 3-table subset of a 20-table
database has Jaccard 0.15 by construction — which is why containment is there;
`test_a_subset_is_detected_by_containment_not_jaccard` asserts exactly that
case, with Jaccard < 0.5 and containment 1.0. The thresholds travel on the
annotation (`json_properties["thresholds"]`) so a disputed "likely copy" shows
the line it crossed.

### 5. `schema_conventions` — structural only, and **not** unused indexes

Five checks, each its own labelled annotation with its own `check_name`:
`tables_without_primary_key`, `tables_without_foreign_key` (labelled `info`,
not `gap` — a reference or log table has no business declaring one),
`tables_without_comment`, `column_comment_coverage`, `naming_convention`
(not lower `snake_case`, i.e. names that must be double-quoted in every
statement that touches them).

**Unused indexes are deliberately excluded**, and the exclusion is a stored
field on the result (`excluded_checks`) rather than an omission: slice 7
already raises an unused-index `RequestForActionAnnotation` from a live
`pg_stat_user_indexes` read, index usage is carried by no structured table so
it is not derivable here at all, and two RFAs for one problem is the bug.
`test_unused_indexes_are_deliberately_not_checked_here` asserts no annotation
from this step mentions an index. **This slice raises no `RequestForAction` of
any kind** — also asserted.

One judgement call worth flagging: a database where **not one** table and
**not one** column carries a description is reported as `unverified`
("comments were probably never captured"), not as 100% undocumented. A single
documented column anywhere flips it to a real measurement, and then the
undocumented ones are genuine gaps. Both directions are tested.

### 6. `db_change_rates`

Finds the two most recent snapshots that carry `database_table_activity` rows
(enumerated from `database_surveys` via `get_database_surveys`, so **no new
registry method and no new table**), then differences per table:
`rows_inserted`/`rows_updated`/`rows_deleted`, plus `size_bytes` and
`row_count` drift from `database_tables`, plus per-day rates from the interval
between the two `surveyed_at` values, plus schema churn (tables added and
removed).

Four per-table outcomes that are **not** rates, each distinct:

- `counters_reset` — `stats_reset` moved between snapshots, **or** a counter
  went backwards with no recorded reset (an older Postgres, or a back-filled
  row). `registry.py`'s own `stats_reset` comment is explicit about why this
  matters: the subtraction would faithfully draw "−40,000 inserts" as a real
  number. The entry carries **no `deltas` key at all**, so a consumer cannot
  read a rate that does not exist.
- `counters_not_measured` — both snapshots have the counters NULL.
- `new_table` — present in the newer snapshot only; no rate yet.
- `insufficient_history` (whole-check) — fewer than two snapshots.

**No new structured table was added.** The brief asked whether one was
warranted; the answer is no, and `test_per_table_series_need_no_new_table`
demonstrates why by reading the series straight out of
`database_table_activity` across two `surveyed_at` values. What is *not* built
is any chart over it — see "Scoped out".

### Proposed `DataScope`

Earliest/latest across date and timestamp columns, published as a
`ResourceMeasureAnnotation` whose `resourceProperties` use
`DataScopeProperties`' **own key names** (`dataCoverageStartTime`,
`dataCoverageEndTime`) plus `confidence` and `basis` — support doc §7's
convention verbatim, so a Curate prefill is a key-for-key copy — and with
`contentStatus: DRAFT`, because measured is not declared.

Two bases, and the difference is visible in the output:
`profile_min_max` (exact, confidence 80) and `histogram_bounds` (Postgres's
ANALYZE-time estimates of the extremes, confidence 50). The fallback exists
because `min_value`/`max_value` are written **only** by the native-survey
read-back path — see "Scoped out".

---

## Absence discipline — the states, and where each is tested

The module's governing distinction, stated in its docstring: *a measured
negative is a real finding; "not established" is not a negative.* Every check
has both, and several have three states that must not collapse.

| Check | Not established (no answer) | Measured negative (a real answer) |
|---|---|---|
| `db_classification` | no signal family has data → no kind, confidence 0, "insufficient signal" | — (a low-margin result reports "too close to call", which is also not a kind) |
| `db_relationship_graph` | **every `is_primary_key` is NULL** → keys were never captured, so whether the tables relate is unknown | keys *were* captured and there are zero FKs → `bag_of_tables`, "a real finding" |
| `grain_determination` | no key and no stored profile → `unverified` | profile present, nothing near-unique → `gap`, a modelling gap in the data |
| `db_fingerprint` | no other database has stored schema → `unverified`, confidence 0 | N comparable peers, none matching → `no_match`, confidence 100 |
| `schema_conventions` | keys never captured → key checks `unverified`, `count: None`; nothing documented anywhere → comments `unverified` | every table has a PK → `pass`; 1 of 2 undocumented → a real count |
| `db_change_rates` | <2 snapshots → `insufficient_history`; counters reset; counters NULL | two snapshots, counters unmoved → `idle`, "genuinely idle" |
| proposed `DataScope` | date columns exist but none profiled → `unverified` | **no date columns at all** → "a real finding, not missing data" |

The `keys_were_captured` test deserves its own note, because it is the one an
implementation gets wrong by default. `result_materializer` writes
`is_primary_key = None` for a native Egeria survey read-back — its own comment
says *"Native does not report keys; leave is_primary_key absent rather than…"*
— and 0/1 for a local survey. Without checking for that, a native-only survey
would report **every table as lacking a primary key, with total confidence,
having never looked**. `DerivedInputs.keys_were_captured` is the guard, and
three tests cover it across two checks.

## The guards were made to fail on purpose

All 62 tests then written passed on the first run, which is not evidence that
any of them would catch a regression. Three behaviours were mutated
deliberately and the corresponding tests confirmed to fail:

| Mutation | Test that caught it |
|---|---|
| `_activity_evidence` returns zeros instead of `None` when no counters exist | `test_missing_tuple_counters_lower_confidence_and_do_not_mean_staging` |
| `keys_were_captured` always returns `True` | `test_keys_never_captured_is_not_a_finding`, `test_keys_never_captured_leaves_the_key_checks_unverified` |
| `run_db_derived` opens a connection | `test_no_connection_is_ever_opened` |

## A pre-existing guard found a real hole in itself

`tests/test_annotation_check_names.py` gates annotation sites on naming their
`check_name` and on declaring shared names as mutually exclusive — by walking
the AST for calls whose function name is in a **hand-maintained**
`ANNOTATION_CTORS` set. Slice 8's `ResourcePhysicalStatusAnnotation` was never
added to it, and neither were this slice's two new classes, so **every check
in that file silently skipped their call sites while reporting green**.

Adding all three to the set immediately surfaced a genuine undeclared shared
check name (`db_fingerprint`) that had been invisible — which is the evidence
this is a real gap and not a tidiness point. The four legitimately-shared
names in `db_derived` (`db_classification`, `db_relationship_graph`,
`db_change_rates`, `proposed_data_scope`, plus `db_fingerprint`) were then
declared in `KNOWN_EXCLUSIVE` after reading each helper to confirm the absence
branch is an early `return` and the two branches cannot both run.

A second, smaller hole in the same file: the shared-name check reads only
`ast.Constant` keyword values, so `check_name=SOME_CONSTANT` is skipped
entirely. `proposed_data_scope`'s two sites were changed to spell the literal
**so the guard could see them**, with a test pinning the literal against the
module constant so the two cannot drift — satisfying the guard rather than
dodging it. Both holes, and a stale `DEFERRED` entry that still excludes
`database/database_surveyor.py` on a 2026-09-02 note, are logged to
`docs/Backlog.md` with a candidate fix (derive the set from `survey_report`'s
own `Annotation` subclasses).

---

## Two new annotation types, and one new field on the base class

- **`DataGrainAnnotation`** → Egeria's `DataGrainAnnotationProperties`, with
  **typed** fields, because the names are known: support doc §3 quotes
  `granularityBasis`, `grainStatement`, `interval`,
  `candidateDataGrainGUIDs` from the Java source.
  `candidate_data_grain_guids` stays empty by design — the whole point of the
  finding is that no `DataGrain` element exists to point at.
- **`FingerprintAnnotation`** → `FingerprintAnnotationProperties`, payload in
  **`additionalProperties`**, because the native field names could not be
  verified here (no Egeria Java checkout on this machine; probe 5 established
  only that `create_annotation` accepts the type). Same fallback convention
  slice 8 used. Logged to `docs/Backlog.md` as a one-probe follow-up rather
  than guessed at.
- **`Annotation.content_status`** — a new optional field on the base
  dataclass, emitted by `annotation_props.build_annotation_props` as
  `contentStatus` **only when set**, so every pre-existing annotation's
  published body is byte-identical to before (tested both ways). This is the
  mechanism the project owner's 2026-09-21 decision settled on for proposing
  governance elements that do not exist yet, replacing the earlier plan to
  carry proposals as an RFA convention or to ask Egeria for new
  `candidate…Specification` fields.

Both types were added to `ANNOTATION_TYPES_REGISTRY` (the Admin-visible
annotation-types list), so the two new kinds appear where the other eight do.

## A generated file needed regenerating, not hand-editing

Same mechanical step slice 8 documented: adding six ids to
`analysis_catalog.yaml` made `configdata/question_catalog.yaml` stale, because
the generator resolves each CSV row's "Answering Analysis" free text against
the *live* set of catalog ids, and `docs/dr-egeria/resource_questions.csv`
already named all six in its `GAP: <id> (proposed)` notes. Two guard tests
correctly failed; the fix was to re-run the generator:

```
uv run python scripts/csv_to_question_catalog_yaml.py \
    docs/dr-egeria/resource_questions.csv \
    --output resource_explorer/configdata/question_catalog.yaml
```

`docs/dr-egeria/resource_questions.csv` itself was **not** edited (stream 4
owns it; this slice's brief forbids it). The consequence is that nine database
rows now carry a populated `analysis_ids` beside prose still saying the
analysis does not exist — flagged in `docs/Backlog.md`, extending slice 8's
own entry for the same reason.

---

## Scoped out

- **`db_hub_tables`** (design §5.3). Not in the brief's six, not built. The
  relationship graph already computes FK in-degree and returns the top ten as
  `most_referenced`, and its other three inputs are in rows this step already
  reads — logged to `docs/Backlog.md` as cheap for a later slice.
- **The semi-structured-columns check** (design §5.3's *"which columns hold
  JSON/JSONB/XML/arrays/hstore, and how much of the data is in them?"*). The
  design assigns it to `schema_inventory [check: semi_structured_columns]`,
  not to `db_derived`, and the "how much of the data is in them" half needs
  column sizes that no structured table carries. Left with its owner.
- **The change detector** (§5.3's *"how much has changed since the last
  survey?"*). Design §9 owns it and slice 14 implements the comparators;
  `db_change_rates` here is the per-table tuple-counter half, not the
  general detector.
- **Exact date ranges on a local-only survey.** `min_value`/`max_value` are
  written only by `result_materializer`'s native read-back path;
  `pg_stats` has no min/max column and slice 7 does not read values. Rather
  than half-build it, the proposal falls back to stored `histogram_bounds`,
  labels the `basis` and halves the confidence. Logged with two candidate
  fixes (derive min/max from the histogram at write time; or a bounded
  `SELECT min(), max()`, which is a scope-of-fetch decision, not a free one).
- **Understanding-tier charts over the change-rate series.** The data is
  reachable without a new table (tested), but nothing renders a trend, and
  only the two most recent snapshots are differenced rather than a longer
  series. Logged — and flagged as the honest limit on the design's
  "per-table series → Understanding charts" claim.
- **Composite-key inference beyond the declared PK.** Where no PK is declared,
  the fallback looks for a *single* near-unique column, not pairs. Inferring a
  composite key from stored per-column distinct counts is not sound —
  `n_distinct` per column says nothing about the cardinality of a pair — so it
  was not attempted rather than guessed.
- **`db_classification`'s `pg_stat_statements` input.** Design §5.3 lists
  "query mix if `pg_stat_statements`" as a classification signal. The
  `query_stats` capability is still `False` on every engine and nothing reads
  that view (slice 8's write-up says the same), so the query-mix family does
  not exist here. Its absence lowers no confidence, because a family that was
  never part of the weighting cannot: the four implemented families are the
  denominator. Worth revisiting if `pg_stat_statements` is ever read.

## What could not be tested

- **Anything against live data.** Every test drives the step through rows
  written into a temporary SQLite registry by hand-built row builders, not
  through rows a real survey produced. The row *shapes* are taken from
  `registry.py`'s DDL and from `result_materializer`'s own writers, so the
  column names and the NULL conventions are right by construction — but no
  test confirms that a real `coco_ods` survey produces rows this step reads
  correctly end to end. A single local survey of `coco_ods` followed by
  `run_db_derived` would close that, and is the natural companion to slice 6's
  re-catalogue work.
- **`db_change_rates` against two genuinely different real surveys.** The
  two-snapshot tests construct both snapshots directly. In particular, the
  counter-reset path was exercised by writing a different `stats_reset` value,
  not by observing a real `pg_stat_reset()` between two surveys of a live
  database.
- **A native-source snapshot produced by an actual Egeria read-back.** The
  "keys were never captured" tests write `is_primary_key = None` rows directly
  with `source='egeria'`, which is what `result_materializer` does — but they
  do not run the materialiser over a real native survey report to get there.
- **Publishing.** `build_annotation_props` is tested for both new types and for
  `contentStatus`, but nothing in this slice publishes to Egeria, so no test
  confirms Egeria *accepts* a `DataGrainAnnotation` with these typed fields or
  a `FingerprintAnnotation` with this `additionalProperties` bag. Probe 5
  established that `create_annotation` accepts both type names; it did not
  establish anything about the field payloads. Related open question, now live
  because this slice is the first to publish DRAFT: does any ordinary read
  path surface `contentStatus` at all? Logged.

**Full test suite:** 5,408 passed, 103 skipped, 1 failed. The single failure —
`tests/test_egeria_live_smoke.py::TestTheByNameFallbackWorks::
test_a_cataloged_database_is_findable_by_name` — was **proven pre-existing,
not assumed**: a throwaway worktree was created at pristine `origin/main`
(`ca0388b9`, with none of this slice's changes) and the same test failed there
with the identical assertion (`assert '' == '45a75724-dbd...-203db34265db'`).
It asserts against live Egeria deployment state — a by-name lookup returning
empty for a database whose cached GUID is still on the row — and touches no
code this slice modifies.
