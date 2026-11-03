# Design: value profile and quality checks for databases

**Status:** design, 2026-10-19. Written by the TestGen evaluation session at the
owner's request ("Design value profile & quality checks for RE").

**Built (2026-10-20), slice 1's local steps only:** the vendored catalogue
(`configdata/quality/testgen/`) and `scripts/sync_quality_catalog.py --check`; the three steps
(`surveyors/database/quality/`) with `database_value_profiles`, `database_quality_proposals`,
`database_quality_verdicts` and `database_quality_results`; the analysis-catalog, check-registry and
engine-host entries. **Not built yet:** Publish (§7: `DataQualityRule`s, the
`database_quality_rules` cache, `egeria_rules.py`), result annotations (§8: `QualityAnnotation`), so
`run_quality_checks` labels every result `rule_source = unpublished`; adding `postgres_value_profile`
to `annotations_from_step_outputs` (§4's known gap); the "Postgres quality" Survey Definition (§6);
the Curate UI (§10). Choices made while building: a column the profile marked `not_applicable` (a
view or foreign table) makes its checks not applicable rather than not established; a proposal row
can also be `accepted` (with the drift sentence) or `suppressed` (a live rejection), so the
rejected-verdict "selection outcome flipped" rule can be read from the batches; the measure is
compared with the threshold in Python rather than in a SQL `CASE`, which is what keeps a NULL
measure from passing. After the gate review (2026-10-20): a table of unknown size (never analysed, no
stored count) is `not_measured` rather than scanned without a bound; a partitioned parent is
`not_applicable` (its partitions are read); row-level security that filters the account is
`not_permitted`; each table is read in its own `READ ONLY` transaction; and the engine host does not
declare `propose_quality_checks` or `run_quality_checks` until results are published
(`engine_host.HELD_FROM_DECLARATION`), since they would complete with empty reports.

**Inputs:**
- `TRELLIS-EVALUATION.md` and `TRELLIS-QUALITY-DESIGN-BRIEF.md`, both in the owner's TestGen
  clone `~/localGit/egeria-v6/dataops-testgen`, branch `trellis/evaluation-note`;
- `REQUIREMENTS-RE-DATA-CHANNELS.md` §5 and §7 item 3;
- `investigation-framing-design.md` ("Failed check → RFA, conditionally").

**Companion:** `SPEC-PYEGERIA-DATA-QUALITY-RULE.md`, the pyegeria convenience API this design
needs.

## 0. Decisions already made by the owner

| # | Decision |
|---|---|
| D1 | Option C, a hybrid. DataKitchen TestGen's test-type catalogue (YAML plus per-dialect SQL) is **vendored as data**. RE executes it. TestGen's engine, metadata DB, scheduler, UI, auth and MCP are not used, and TestGen is not run as a service. |
| D2 | RE is not the coordinator. Either Egeria coordinates, through a Survey Definition whose steps run on RE's engine host, or RE's `SurveyDefinitionExecutor` runs the steps on Prefect or locally. There are three linear steps: **value profile → propose checks → run checks**. |
| D3 | Check definitions are **proposed in RE and accepted into Egeria**. An accepted check becomes one `DataQualityRule` (0450), linked by `GovernedBy`. |
| D4 | Results are `QualityAnnotation`s (0640) in the run's `SurveyReport`. |
| D5 | The first slice covers Postgres only, with a small fixed set of checks. |
| D6 | Follow Egeria's types. Gaps go in `additionalProperties` or `jsonProperties` and are raised with the Egeria team. No new types are invented. |

## 1. What this adds, in one paragraph

Today `row_count_snapshot` *estimates* null fraction and distinct count from `pg_stats`, and
`data_class_match` samples values only in order to match them against data classes. Nothing
measures values, and nothing tests them. This design adds three database steps:

- **`postgres_value_profile`** measures per-column counts and dates over the whole table.
- **`propose_quality_checks`** applies TestGen's selection rules to that profile and writes
  proposals into a registry table.
- **`run_quality_checks`** executes the *accepted* checks, using TestGen's vendored
  Postgres SQL against the target, and publishes one `QualityAnnotation` per evaluated check.

A person accepts or rejects proposals in Curate. A separate Publish action turns accepted
proposals into Egeria `DataQualityRule`s. This is the split that architecture components
already use: a verdict is local, and publishing is the step that writes to Egeria.

## 2. The first slice: seven checks, not eight

The evaluation proposed eight checks. **Recency moves to slice 2.** Its TestGen selection
criteria include `NOT functional_data_type IN ('Future Date', 'Schedule Date')`, and
`functional_data_type` comes from TestGen's inference, which slice 1 does not port. In a
column with no value, `NOT NULL IN (…)` evaluates to NULL. So the rule would be applied
verbatim and **never propose anything, with no error**. That is the absence bug this
codebase most needs to avoid. The rule will not be weakened to compensate. *Owner,
2026-10-19: "ok - slice 2".*

Each vendored check below gives TestGen's Postgres measure, its failure condition (`measure
op condition` means fail), the selection rule run against the profile, and the defaults.

| Test type (TestGen id) | Dimension | Fails when (Postgres) | Selection (profile) | Defaults |
|---|---|---|---|---|
| Missing_Pct (1022) | Completeness | Cohen's H between the baseline non-null ratio and the current one, `ABS(2·ASIN(√(BVC/BC)) − 2·ASIN(√(COUNT(col)/NULLIF(COUNT(*),0))))`, is **≥ threshold** | `record_ct <> value_ct` | BC=`record_ct`, BVC=`value_ct`, threshold 2; Warning |
| Required (1030) | Completeness | `COUNT(*) − COUNT(col)` **> threshold** | `record_ct = value_ct AND record_ct > 10 AND NOT COALESCE(general_type='A' AND zero_length_ct=0 AND filled_value_ct=0 AND sample_ratio IS NULL, FALSE)` | threshold 0; Fail |
| Required_No_Placeholder (1520) | Completeness | `SUM(CASE WHEN col IS NULL OR col IN (placeholder list) THEN 1 ELSE 0 END)` **> threshold** | `record_ct = value_ct AND record_ct > 10 AND general_type='A' AND zero_length_ct=0 AND filled_value_ct=0 AND sample_ratio IS NULL` | placeholder list (`n/a`, `tbd`, `(null)`, …), threshold 0; Fail |
| Unique (1034) | Uniqueness | `COUNT(*) − COUNT(DISTINCT col)` **> threshold** | `record_ct > 500 AND record_ct = distinct_value_ct AND value_ct > 0` | threshold 0; Fail |
| Future_Date (1015) | Timeliness | `SUM(GREATEST(0, SIGN(col::DATE − run_date::DATE)))` **> threshold** | `general_type='D' AND future_date_ct = 0` | threshold 0; Fail |
| Min_Date (1020) | Validity | `SUM(CASE WHEN col < baseline THEN 1 ELSE 0 END)` **> threshold** | `general_type='D' AND min_date IS NOT NULL AND distinct_value_ct > 1` | baseline=`min_date`, threshold 0; Fail |
| Row_Ct_Pct (1032) | Completeness | `ABS(ROUND(100·(COUNT(*) − BC)/BC, 2))` **> threshold** | none in TestGen, which makes it manual-only. **RE proposes it** for every table with `record_ct > 0`. | BC=`record_ct`, threshold from the setting `quality_checks.row_ct_pct_default_threshold` (default **10**; RE's own default, TestGen has none); Fail |

Row_Ct_Pct is the one check RE proposes on its own initiative. Its proposals carry
`origin=re` rather than `origin=vendored`, so nothing claims TestGen made them.

## 3. Vendoring the catalogue

- **Where the data goes:** `resource_explorer/configdata/quality/testgen/`.
  - `test_types/<Name>.yaml`: the seven files, copied unchanged.
  - `postgresql/profile_measures.sql`: the subset of
    `flavors/postgresql/profiling/project_profiling_query.sql` named in §4.
  - `postgresql/general_type.sql`: the `general_type` CASE from
    `flavors/postgresql/data_chars/get_schema_ddf.sql`.
  - `PROVENANCE.md`: the upstream repository, the commit (`3606859c`, release 5.134.0), and
    the per-file list.
  - `NOTICE` (DataKitchen's three lines) and `LICENSE` (Apache-2.0).
- **Where the code goes:** `resource_explorer/surveyors/database/quality/`. It does not go
  in a `trellis-analytics` library step, because library steps cannot open a connection or
  emit anything other than `ResourceMeasureAnnotation`.
- **Naming.** The engine and analysis names are RE's own: "value profile" and "quality
  checks". **"TestGen" is never used as a product name** in a headline, a row's words or a rule's
  name, in the UI or in Egeria, because Apache-2.0 grants no trademark. `PROVENANCE.md` and the
  docs say where the catalogue came from.

  **Decision (project owner, 2026-10-30):** "I think at this point we can mention some measures
  as inspired by or implementing measures defined by Testgen in a detailed description."
  This narrows the 2026-10-19 rule above: **a detailed description may say where a check comes
  from; nothing else may.** The two places are the proposal row's title on Curate › Structure and
  the `description` of the published `DataQualityRule`. The wording is decided from what is on the
  proposal (`origin`, `catalogue_ref`), in `resource_explorer/quality_words.py`:
  - a vendored check whose statement pin matches the vendored catalogue's: "Implements TestGen
    test type <id> (catalog commit <sha>)";
  - a check RE proposes itself (`origin=re`), or a vendored one whose pin no longer matches:
    "Inspired by TestGen's test type <id>; <what Resource Explorer does differently>";
  - an imported TestGen-only check, a type with no TestGen id, or a pin that cannot be read: no
    line (nothing is claimed without the data to claim it).
  A republish of an already published rule updates its `description` (the proof hash covers it);
  it is not reported as "edited in Egeria", which compares only operator, threshold and severity.
- **Sync.** `scripts/sync_quality_catalog.py --check` reads a TestGen checkout given by
  `--upstream`. It diffs each vendored YAML's `selection_criteria`, `default_parm_values`,
  `default_parm_columns` and Postgres `cat_test_conditions`, and the profile SQL subset, and
  lists new test types. It writes nothing. Adopting an upstream change is a deliberate
  commit that updates `PROVENANCE.md`.

### Selection rules: run as Python, pinned to the YAML text

TestGen evaluates `selection_criteria` as SQL over `profile_results` in its own Postgres.
RE's registry can be SQLite, the supported fallback that the tests use. These fragments are
Postgres dialect: `::` casts, `INTERVAL`, a `DATEDIFF` stored function. So they cannot run
verbatim against the registry.

**Slice 1 ports each of the seven fragments to a Python predicate.** Each predicate is
paired with the SHA-256 of the YAML fragment it was ported from. A test asserts that the
hash still matches the vendored YAML, so a change upstream that the sync script adopts
fails the test until someone re-ports the predicate. That keeps the upgrade cost a visible
data diff rather than a silent divergence.

The profile keeps **TestGen's column names**: `record_ct`, `value_ct`, and so on. This keeps
each port one-to-one and leaves open a later move to running the fragments verbatim (for
example in-process DuckDB, which accepts this dialect almost as is) without renaming
anything.

The target-side SQL, meaning the measure, the operator and the condition, **does** run
verbatim against the surveyed Postgres. That is where the vendoring pays off.

## 4. Step 1: `postgres_value_profile`

**Registration.** Add a `StepInfo` in `DATABASE_STEP_REGISTRY`:

```python
"postgres_value_profile": StepInfo(
    "postgres_value_profile", None,
    "Measured (not estimated) per-column counts and date bounds, whole table.",
    ["ResourceProfileAnnotation", "ResourceMeasureAnnotation"],
    requires_context={"has_schema_inventory": "profiles the columns a stored inventory names"},
    fetch_cost="api_heavy", compute_cost="medium",
    requires_capability="read",
),
```

It also needs:
- an entry in `re_analysis_steps`;
- `"value_profile": ["postgres_value_profile"]` in `DATABASE_ANALYSIS_RE_STEP_MAP`;
- a reader in `DATABASE_ANALYSIS_RESULTS_MAP`;
- the generated `engine-host/steps/` block, regenerated by
  `generate_engine_host_declaration.py`.

**What it measures.** One aggregate `SELECT` per table, assembled from the vendored SQL
subset with the per-column `TG-IF` blocks toggled by `general_type`:

| Column (TestGen name) | Applies to | Why slice 1 needs it |
|---|---|---|
| `record_ct` | table | Missing_Pct, Required, Unique, Row_Ct_Pct |
| `value_ct` | all | Missing_Pct, Required, Required_No_Placeholder, Unique |
| `distinct_value_ct` | all except X | Unique, Min_Date |
| `null_value_ct` | all | reported; it equals `record_ct − value_ct` |
| `zero_length_ct`, `filled_value_ct` | A | Required, Required_No_Placeholder |
| `min_date`, `max_date`, `future_date_ct` | D | Future_Date, Min_Date |
| `general_type` | all | derived from the catalog type by the vendored CASE (A, B, D, T, N, X), never measured |
| `sample_ratio` | all | always NULL in slice 1 (no sampling); see below |

**No sampling in slice 1. The full-table scan is bounded and refused, never shrunk.** Two of
the seven selection rules require `sample_ratio IS NULL`. TestGen does not trust the
placeholder counts from a sample. And every one of the checks that follow runs over the full
table anyway. So slice 1 profiles whole tables, with two bounds:

- **A row ceiling.** `value_profile.max_table_rows`, default 5,000,000, judged by
  `pg_class.reltuples`. A table over the ceiling is not profiled; it is recorded as
  `not_measured` with the reason "over the full-profile row ceiling (N > M)".
- **A time limit.** A per-table statement timeout (`SET LOCAL statement_timeout`), default
  60 s, run through `execute_query_isolated` so that one failure does not abort the
  transaction. A timeout or an error is `not_measured` with the error text.

Sampled measures, using `sampling.py`'s strategies and TestGen's `sample_ratio` calibration,
are slice 2. That is when shape measures such as patterns and lengths arrive and a sample
becomes worth having.

**Why sampling would buy nothing in slice 1** (the owner asked, 2026-10-19). TestGen itself
never samples the measures slice 1 uses. Its Postgres profiling query reads `record_ct`,
`value_ct`, `distinct_value_ct` and `null_value_ct` in `whole_table_measures`, a scan of the
whole table, and every date and numeric measure likewise. Only text-shape measures
(`zero_length_ct`, `filled_value_ct`, patterns, case and character counts) come from its
`TABLESAMPLE BERNOULLI` CTE. Of those, slice 1 needs only `zero_length_ct` and
`filled_value_ct`. They feed exactly the two checks whose selection rules refuse a sampled
profile (`sample_ratio IS NULL`). A sample in slice 1 would therefore save no scan, and it
would take away the two checks that need it. Sampling arrives with slice 2's shape measures,
which is where TestGen uses it.

**Every bound is a setting, not a constant.** `value_profile.max_table_rows`,
`value_profile.statement_timeout_seconds` and
`quality_checks.row_ct_pct_default_threshold` live in RE's settings (`config.py`), with the
defaults above. A person can also change any single check's threshold before accepting it, and
afterwards in Egeria (§6). The settings decide only what a *proposal* starts with.

**Storage.** A detail table in the `_DB_FS_DETAIL_TABLE_DDL` family, written by
`write_detail_rows` and read by `query_detail_rows`, which returns the latest batch:

```sql
CREATE TABLE IF NOT EXISTS database_value_profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT, database_slug TEXT NOT NULL,
  surveyed_at TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'local',
  schema_name TEXT NOT NULL, table_name TEXT NOT NULL, column_name TEXT NOT NULL,
  general_type TEXT NOT NULL DEFAULT 'X',
  record_ct BIGINT DEFAULT NULL, value_ct BIGINT DEFAULT NULL,
  distinct_value_ct BIGINT DEFAULT NULL, null_value_ct BIGINT DEFAULT NULL,
  zero_length_ct BIGINT DEFAULT NULL, filled_value_ct BIGINT DEFAULT NULL,
  min_date TEXT DEFAULT NULL, max_date TEXT DEFAULT NULL, future_date_ct BIGINT DEFAULT NULL,
  sample_ratio REAL DEFAULT NULL,
  state TEXT NOT NULL DEFAULT 'measured', reason TEXT DEFAULT '',
  UNIQUE(database_slug, surveyed_at, source, schema_name, table_name, column_name),
  FOREIGN KEY (database_slug) REFERENCES databases(slug))
```

A column that was not profiled gets a row with all measures NULL and
`state ∈ {not_measured, not_permitted, not_applicable}` (views and foreign tables are
`not_applicable`). **It is never a row of zeros.** A table with zero rows is `measured` with
`record_ct = 0`. That is a real zero, and it is what makes Row_Ct_Pct and Missing_Pct
undefined later (§6).

**Annotations.** One `ResourceProfileAnnotation` (0620) per measured column, with
`profileCounts` = the counts and `profileDates` = the date bounds, plus a per-table
`ResourceMeasureAnnotation` for each table that was not profiled, carrying the reason. Their
`item_key` is `schema.table.column`.

The step returns them as a **top-level `"annotations"` list**, which is the shape the
engine host's `publisher()` collects. **Known gap to close in the same change:**
`annotations_from_step_outputs` and `build_step_annotations` in
`egeria_database_surveyor.py` read only five named keys. So a new step's annotations do not
reach Egeria on the Survey Definition `_publish` path unless the step key is added there.
The same omission was why `postgres_column_profile`'s own annotations were never published.
That is a separate bug, fixed by PR #750 (§12). The fix passes through a
step output's own `annotations`, so it should be extended to the new step keys rather than
copied.

## 5. Step 2: `propose_quality_checks` and the proposal tables

**This step is zero-fetch.** It reads the latest `database_value_profiles` batch and the
current verdicts. It opens no connection (`requires_capability=""`), like `db_derived`.

For each (column, vendored test type) it evaluates the ported predicate. Each evaluation
gives one of three outcomes:

| Predicate result | Recorded as |
|---|---|
| true | a proposal |
| false | nothing (a counter, `not_applicable_ct`, for the run summary) |
| cannot be evaluated, because an input is NULL, for example a column that was not profiled | `not_established`, counted and listed **by column**. **Never a silent false.** |

For each table with `record_ct > 0` it proposes Row_Ct_Pct.

**The proposal table.** This is the analysis-results family, append-only per propose run:

```sql
CREATE TABLE IF NOT EXISTS database_quality_proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, database_slug TEXT NOT NULL,
  surveyed_at TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'local',
  check_key TEXT NOT NULL,          -- schema.table.column::Test_Type  (table checks: schema.table::Test_Type)
  schema_name TEXT NOT NULL, table_name TEXT NOT NULL, column_name TEXT NOT NULL DEFAULT '',
  test_type TEXT NOT NULL, origin TEXT NOT NULL,           -- vendored | re
  profile_surveyed_at TEXT NOT NULL,                       -- the profile batch it came from
  params_json TEXT NOT NULL,        -- {baseline_ct, baseline_value_ct, baseline_value, threshold_value, severity}
  catalogue_ref TEXT NOT NULL,      -- PROVENANCE commit + YAML fragment sha256
  state TEXT NOT NULL DEFAULT 'proposed',                  -- proposed | not_established
  reason TEXT DEFAULT '',
  UNIQUE(database_slug, surveyed_at, source, check_key),
  FOREIGN KEY (database_slug) REFERENCES databases(slug))
```

**Verdicts.** These copy `architecture_component_verdicts`: append-only, where the latest
row per key wins, and `decided_by` is attribution.

```sql
CREATE TABLE IF NOT EXISTS database_quality_verdicts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, database_slug TEXT NOT NULL,
  check_key TEXT NOT NULL, verdict TEXT NOT NULL,          -- accepted | rejected | retired
  params_json TEXT NOT NULL,        -- the parameters AS ACCEPTED (a person may edit thresholds)
  proposal_id INTEGER, note TEXT DEFAULT '', decided_by TEXT NOT NULL, created_at TEXT NOT NULL)
```

**How proposals and verdicts interact:**

- **Re-proposal.** A proposal whose `check_key` has a live verdict is not shown as new.
  - *Accepted*: the step records only whether the new profile has drifted from the accepted
    baseline. Drift is a fact for the reviewer and is never re-applied silently.
  - *Rejected*: the proposal is suppressed until the column's `general_type` changes or the
    selection outcome flips. "Rejected" means "not for this column", not "not this week".
- **Superseding.** An open proposal (with no verdict) is superseded by the next propose run,
  because the latest batch wins. Nothing is deleted.
- **Retired.** An accepted check whose column has disappeared is never removed silently.
  The run-checks step reports it as `not_established` ("column no longer in inventory"),
  and Curate offers a retire verdict.

**Materialisation cache.** This copies `architecture_materialized_components`:
`database_quality_rules(database_slug, check_key, rule_guid, rule_qualified_name,
governed_element_guid, governed_at, published_at, published_by)`. Here `governed_at` is
`column` or `asset`, as §7 explains.

**Catalogue entries.**
- `analysis_catalog.yaml`: `value_profile` (intent `analysis`) and `quality_checks` (intent
  `assessment`, because it evaluates against criteria). Both have `resource_types:
  [database]`, `action: survey`, `availability: queued`, and `target_shape:
  whole_resource_only`.
- `check_registry.yaml`: a `quality_checks` entry. Its `checks:` are the seven test-type
  codes. Its destinations are `{default: context, labels: {fail: task, warning: task}}`.

## 6. Step 3: `run_quality_checks`

**The input is the accepted checks, and Egeria wins.** For each accepted `check_key`, the
step reads the rule from Egeria through the cache's `rule_guid`. Egeria's
`thresholdValues`, `comparisonOperator` and `severity` override RE's verdict row. This is
how an edit made in Egeria reaches the run, which is TestGen's `lock_refresh` meaning. Each
case is reported as a fact on the run:

| Situation | What the run does |
|---|---|
| A value in Egeria differs from RE's verdict | Runs with Egeria's value; records "rule edited in Egeria since acceptance" |
| Egeria is unreachable | Runs from the verdict row; labels the run "rules read from local cache" |
| Accepted, not yet published | Runs from the verdict row; labels it `unpublished` |
| Published, but the rule is gone from Egeria | `not_established`, "rule not found in Egeria", and **does not run** |

**Nothing from Egeria is executed as SQL.** The SQL always comes from the vendored
catalogue, keyed by `method` (the test-type code). Values read from Egeria are validated by
type before substitution:

- threshold values must parse as numbers;
- `baseline_value` for Min_Date must parse as an ISO date;
- the placeholder list is escaped item by item as string literals.

A value that fails validation is `not_established`, with the bad value quoted in the reason.
A `DataQualityRule` with `checkType=sql`, or an `expression` not written by RE, is
**ignored**. It is never run. Other engines own those rules.

**Execution.** Checks are batched **one aggregate SELECT per table**, which is TestGen's CAT
pattern:

```sql
SELECT m1, CASE WHEN m1 > t1 THEN 0 ELSE 1 END, m2, … FROM "schema"."table"
```

The batch runs through `execute_query_isolated` under the same row ceiling and timeout as
the profile. If a batch errors, each check in it is re-run alone, as TestGen does, so that
one bad check does not hide the others.

**Absence: TestGen's NULL-means-pass trap must not be copied.** TestGen builds its result
code as `CASE WHEN measure op condition THEN '0' ELSE '1' END`. When the measure is NULL,
the condition is NULL and the result **falls through to pass**. For Missing_Pct on an empty
table, `NULLIF(COUNT(*), 0)` makes the measure NULL, so the check *passes*. Row_Ct_Pct with a
baseline of 0 divides by zero. RE reads the measure itself and maps it to a status:

| Measure | Status |
|---|---|
| NULL, or division by zero | `not_established`, "measure undefined (empty table / zero baseline)" |
| Query error or timeout | `error`, with the message |
| Measure fails the condition, rule severity error | `fail` |
| Measure fails the condition, rule severity warning | `warning` |
| Measure fails the condition, rule severity info | `logged` (TestGen's "Log") |
| Measure satisfies the condition | `pass` |
| Table over the ceiling | `not_measured` |

**Results storage.** A detail table, `database_quality_results`, keyed by
`(database_slug, surveyed_at, source, check_key)`, with these columns:
- `measure`, `status`, `reason`;
- `threshold_values_json` and `comparison_operator`, as applied;
- `rule_source`: `egeria`, `local_cache` or `unpublished`;
- `rule_guid`.

**Engine host.** Neither step takes `requestParameters`; both read their inputs from the
registry and from Egeria. That suits the engine host's current limit, which stores
`requestParameters` and never forwards them. A Survey Definition, "Postgres quality", is
three process steps (`re-postgres-value-profile` → `re-propose-quality-checks` →
`re-run-quality-checks`), authored through the normal generated Dr.Egeria documents.
Because "Link Next Process Step" is not idempotent, it is authored once and then reconciled.

**Ordering by data, not by timer.** The run step uses the last *accepted* baseline, never the
current profile. A scheduled run of `run_quality_checks` on its own, with no new profile, is
therefore valid. Each result records which baseline it was measured against (the
`profile_surveyed_at` in the accepted parameters).

## 7. Publishing accepted checks as `DataQualityRule`s

**Accepting is local. Publish is the only action that writes rules to Egeria.** This is the
current Curate pattern: the verdict route makes no Egeria call, and Publish is the verb that
does. Publish runs as the signed-in user, through `egeria_identity.apply_identity`, and stamps
`Ownership` with that user.

**`GovernedBy` target.** *Corrected 2026-10-20.* RE does not create column elements itself,
but a Curate **catalogue commit** does lead to them: it attaches a schema-kind catalog target
per chosen schema, and Egeria's JDBC cataloguer then creates that schema's `RelationalTable`
and `RelationalColumn` elements. RE's proof rows record schemas and tables, not columns, so a
column's GUID is found by walking the committed schema at publish time
(`catalogue_commit._walk_schema`: schema → schema type → table → `NestedSchemaAttribute`).
See `DESIGN-PUBLISHING-COLUMN-VERDICTS.md` §0. The first draft of this note said RE has no
column elements at all, which was wrong for committed schemas. The target is chosen in this
order:

1. **the column element**, for a column in a committed schema (found by that walk), or one
   Egeria's own survey created;
2. otherwise, **ask the person whether to add the object to Egeria** (owner, 2026-10-20). The
   value profile has found a column, or another resource, that Egeria does not know. Publish
   asks whether to add it, by committing its schema on the Curate **Contents** tab, and says
   explicitly **when the analysis that found it ran** (owner, confirmed with Mandy 2026-10-20:
   "the time that TestGen ran the analysis not when we imported from Testgen"). In this design
   that is the value-profile run's own `surveyed_at`. If TestGen is later used as a service
   channel, it is TestGen's own profiling or test-run timestamp. It is **never** the time RE
   imported, published or showed the result. The same time is recorded on the accepted check, so
   a stale object is never added. If the person adds it, the next Publish governs the new column
   element.
3. **If the person declines, the object is ignored: no rule is written for it.** The accepted
   check stays in RE, marked "not added to Egeria · <date>", and is not published. The earlier
   draft's fallback, the database Asset with `scope` set to the column path, is not used.

The cache records the target (`governed_at = column`). The rule's qualified name is rooted at
the database Asset (below), so it never depends on how the column element came to exist.

**The property mapping.** This follows the Bitol mapper (`DataContractMapper.getDataQualityRuleProperties`)
wherever ODCS has an equivalent:

| `DataQualityRule` | Value |
|---|---|
| `qualifiedName` | `<databaseAssetQN>::Quality::<schema>.<table>[.<column>]::<Test_Type>`. This is the Bitol `<elementQN>::Quality::<id>` shape, rooted at the asset so that it is stable. |
| `displayName` | e.g. "Unique Values: sales.orders.order_id" (the YAML `test_name_short`) |
| `summary` / `description` | the YAML `test_description` plus `usage_notes` |
| `scope` | `schema.table.column` |
| `qualityDimension` | Completeness → `completeness`, Uniqueness → `uniqueness`, Timeliness → `timeliness`, **Validity → `accuracy`** (owner, 2026-10-20: "conformity and validity are not equivalent - accuracy is closer"), with `originalDimension: Validity` in `additionalProperties` until a `validity` valid value is added, which is on the backlog |
| `checkType` | `library` where an ODCS standard metric means exactly this check (Required, Unique, Required_No_Placeholder); otherwise `custom` |
| `metric` | Required → `nullValues`; Unique → `duplicateValues`; Required_No_Placeholder → `missingValues`; the others: none |
| `method` | the test-type code, always. **This is the executor's key.** |
| `qualityEngine` | `resource-explorer` |
| `comparisonOperator`, `thresholdValues` | TestGen's *fail-when* operator inverted to ODCS *must-be*: `>` → `mustBeLessOrEqualTo`, `>=` → `mustBeLessThan`, `<` → `mustBeGreaterOrEqualTo`. The threshold is stringified, as Bitol does. |
| `severity` | Fail → `error`, Warning → `warning`, Log → `info` |
| `units` | the YAML `measure_uom` |
| `expression` | **not set.** RE never stores runnable SQL in Egeria (§6). |
| `schedule`, `scheduler` | not set in slice 1. Scheduling is RE's own or the Survey Definition's. |
| `additionalProperties` | `baselineCt`, `baselineValueCt`, `baselineValue`, `profileSurveyedAt`, `catalogueRef`, `origin`, `originalDimension` (e.g. "Validity") |
| `contentStatus` | `ACTIVE`, because a person accepted it. Zones are configured-only, following the existing Curate rule (`EXPLORER_PUBLISH_ZONES`, no default). |

**Read-back.** Publish re-reads every created rule by GUID and checks `method`,
`comparisonOperator` and `thresholdValues` before it writes the cache row. A rule that does
not read back is reported as `unverified` and gets no cache row. `materialize()` has no
read-back today; this design adds one rather than copying that gap.

**The pyegeria path.** `create_governance_definition` with a `DataQualityRuleProperties`
body and `attach_governed_by_definition` already exist and should work. The class is
registered server-side, and pyegeria passes the `properties` dict through unvalidated. Both
are untested for this type, so they **need a live create-and-read-back before slice 1
relies on them**. The convenience API RE should call instead is specified in
`SPEC-PYEGERIA-DATA-QUALITY-RULE.md`. Until that API lands, RE wraps the generic calls in
one module (`surveyors/database/quality/egeria_rules.py`), so they can be swapped in one
place.

## 8. Result annotations

**One `QualityAnnotation` per check that ran to a conclusion** (`pass`, `warning`, `fail`
or `logged`), in that run's `SurveyReport`. **Checks that did not reach a conclusion get no
`QualityAnnotation`.** That covers `not_established`, `error` and `not_measured`. The owner
agreed after discussing it with Mandy (2026-10-20): "Maybe we shouldn't save the
QualityAnnotation at all if it can't be run?" **Still open:** whether such a check instead
produces a `ResourceMeasureAnnotation` named `QualityCheckNotEvaluated` carrying the
`check_key` and the reason. If it doesn't, the reason is kept only in RE
(`database_quality_results`, with its `status` and `reason`).

The reason is that `qualityScore` is a Java `int` defaulting to 0. A `QualityAnnotation` for a
check that never ran would therefore read as "scored zero". With this rule, a consumer that
reads only `QualityAnnotation`s sees only evaluated checks. Whether Egeria also carries the
reason, in a `QualityCheckNotEvaluated` sibling, is the open question above.

| Field | Value |
|---|---|
| `qualifiedName` | `Annotation::PostgreSQL::<slug>::<surveyed_at>::Quality::<check_key>` (RE's annotation qualified-name scheme) |
| `annotationType` | `DataQualityCheck` |
| `qualityDimension` | the rule's `qualityDimension` |
| `qualityScore` | **0-100 conformance, one meaning only.** It is the percentage of rows that are *not* offending, `100·(1 − measure/record_ct)`, for the row-count checks (Required, Required_No_Placeholder, Unique, Future_Date, Min_Date). For the statistic checks (Missing_Pct, Row_Ct_Pct), conformance is not a row fraction, so the score is 100 for pass and 0 otherwise, and `qualityDescription` says so. |
| `qualityDescription` | `pass`, `warning`, `fail` or `logged`, plus one human sentence: "3 of 12,340 rows null (threshold 0)" |
| `expression` | the comparison as applied: `measure 3 mustBeLessOrEqualTo 0` |
| `explanation` | the YAML `test_description` |
| `analysisStep` | `run_quality_checks` |
| `jsonProperties` | `{status, measure, comparisonOperator, thresholdValues, severity, ruleGUID, ruleQualifiedName, ruleSource, checkKey, baselineProfileSurveyedAt, recordCt}` |

- **Linking the annotation to the rule.** **`GovernedBy`** from the `QualityAnnotation` to the
  `DataQualityRule` it evaluated. The owner revised this on 2026-10-20 after discussing it with
  Mandy ("Use governedBy for Annotation to Rule"); it replaces the earlier `AssociatedAnnotation`
  answer.
  - **Checked against the 6.2 types.** `GovernedBy`'s end 1 is the `GovernanceDefinition` (the
    rule) and its end 2 is any `Referenceable`. `Annotation` derives from
    `AuthoredReferenceable`, which derives from `Referenceable` (`OpenMetadataTypesArchive1_2`),
    so the annotation is a valid end 2 (`OpenMetadataTypesArchive2_4`).
  - **The call:** pyegeria's
    `attach_governed_by_definition(element_guid=<annotation>, definition_guid=<rule>)`, linked
    once through `egeria_link_once`, reading from the annotation.
  - The rule is **also** named in `jsonProperties` (`ruleGUID`, `ruleQualifiedName`).
- **Linking the annotation to the column.** `AssociatedAnnotation` to the column element
  only when §7 found one; otherwise the `item_key` and `checkKey` carry the column.

**RE changes:**
- A new `DataQualityCheckAnnotation(Annotation)` dataclass, mapped to
  `QualityAnnotationProperties` in `EGERIA_PROPERTIES_CLASSES`, which sends
  `qualityDimension`, `qualityScore` and `qualityDescription` as top-level fields;
- an `ANNOTATION_TYPES_REGISTRY` entry for it;
- the existing `QualityScoreAnnotation` is left as it is (but see §12).

**RFAs and guards: none in slice 1.** The Purpose-gated rule in
`investigation-framing-design.md` says a failed check is an RFA only for the Assess,
Maintain, Certify and Remediate purposes. **That gate is not built**: RFAs are emitted
inline by each surveyor today. Slice 1 emits **no RFA** from check results. Failures reach
people as `task` destinations through `check_registry.yaml` and `destinations.py`, so they
show as work in the UI without filling the RFA drawer. RFA emission arrives with the
Purpose gate, and is not hard-coded into this step.

Likewise, slice 1 does not produce the `data-certified` or `data-not-certified` guards. A
certification needs a `CertificationType` and the investigation design, and the only users
of those guards today are Egeria samples.

## 9. Identity and credentials

| Action | Runs as | Egeria attribution |
|---|---|---|
| Profile, propose, or run checks, interactive | the signed-in user's run, queued | worker publishes as the service account with `Ownership` = requester (the known queued-run gap; `run_queue._run_as_requester`) |
| Same, Egeria-coordinated | `Daemon(ENGINE_HOST, requested_by)` | as for every engine-host step |
| Accept or reject a proposal | the signed-in user | none (local verdict; `decided_by`) |
| Publish rules | the signed-in user, synchronous | the user's own bearer token; `Ownership` = user |

Target-database credentials are unchanged: per registration, Fernet-encrypted, plus a
per-run override for interactive runs. The steps need the `read` tier. A credential that
lacks it makes the step `not_established` through `_capability_refusal` and the
prerequisite resolver. It is never counted as a zero.

## 10. UI (wireframe level)

- **Assessment.** A "Quality checks" card using the generic By-analysis engine. Today
  `data_class_match` shows "ran; no summary reader yet", so this card needs a results reader,
  a headline reader, and `render="custom"`. Its headline counts by status, for example
  "41 pass · 2 fail · 1 warning · 3 not evaluated (why)". The breakdown is by table, with the
  failing measure and threshold.
- **Analysis.** A "Value profile" card: one row per column with the measured counts, and the
  not-profiled columns listed with reasons.
- **Curate, the database Structure tab.** Today it is a placeholder (`DB_WORK_SECTIONS`). It
  gains a "Quality checks" section:
  - proposals grouped by table, each with the test type, the parameters and the profile it
    came from;
  - accept, edit-then-accept, and reject, using the `verdictBadge` and verdict-button
    pattern from architecture components;
  - "Publish accepted checks", which shows per rule whether it was published, which target
    it was governed at (`column` or `asset`), and whether it read back.

## 11. Open questions

**For the owner** (answers of 2026-10-19 recorded):

1. ~~Recency to slice 2?~~ **Yes** ("ok - slice 2").
2. **Full-table, no-sampling profile with a 5M-row ceiling and a 60 s timeout?** The owner
   asked why not sample. The answer is now in §4: TestGen itself does not sample these
   measures. **Open until the owner accepts that answer.**
3. ~~The `GovernedBy` fallback to the database Asset?~~ **Replaced** (owner, 2026-10-20): ask
   whether to add the object, showing when it was found; if declined, write no rule (§7). The owner asked whether the gap comes from
   TestGen using a more privileged account. It does not: in option C TestGen does not run.
   The gap is smaller than first stated (corrected 2026-10-20): Egeria's cataloguer creates
   the column elements for every schema a Curate catalogue commit chose, so the fallback
   applies only to columns in schemas that were never committed. The owner's underlying point stands separately: the account
   that profiles a database decides what it can see. The profile and run steps therefore
   record the account they ran as (`ran_as`). Any result is
   `measured_within_credential_scope` when the stored `credential_capability` probe shows
   partial access, and an unreadable table is never a measured zero. Egeria's own surveys
   use the engine host's account, which can differ: coco_pharma's is `surveyor`. The
   credential banner and `security-model.md` already describe that pair; this design adds
   nothing to it.
4. ~~Row_Ct_Pct's default of 10%?~~ **Yes, as a setting** ("seems reasonable - but is it
   parameterized?"). It is now `quality_checks.row_ct_pct_default_threshold`, and every
   check's threshold can also be edited before acceptance and in Egeria (§4).
5. ~~`ACTIVE` or `DRAFT`?~~ **`ACTIVE`**.

**For the Egeria team:**

These went to the review with Mandy as Q1-Q4. Answered 2026-10-20 (owner):

6. ~~An annotation-to-rule relationship?~~ **`GovernedBy`**, annotation to rule (revised
   2026-10-20 after the discussion with Mandy; it was `AssociatedAnnotation`), with
   `jsonProperties` as well (§8).
7. ~~A status and a nullable score on `QualityAnnotation`?~~ **The plan stands for now**:
   status in `jsonProperties`, and no `QualityAnnotation` for an unevaluated check, which Mandy
   confirmed. **Open:** whether a `ResourceMeasureAnnotation` saying why is kept in Egeria (§8).
   The status field and nullable score stay for future consideration, with A8 and A11.
8. ~~`scope` for an uncatalogued column?~~ **Replaced**: ask the person, and write no rule if
   they decline (§7).
9. ~~"validity" → `conformity`?~~ **No: `accuracy`**, with `originalDimension: Validity`, until
   a `validity` value is added (§7).

**For pyegeria:** see `SPEC-PYEGERIA-DATA-QUALITY-RULE.md`.

## 12. Found while designing (separate from this design)

- **Confirmed and fixed by trellis PR #750 (merged 2026-10-19 as `55c5c6ea`): `postgres_column_profile`'s and `postgres_nested_columns`' annotations were
  never published.** The step returns them nested under `column_profile["annotations"]`,
  but `build_step_annotations` and `annotations_from_step_outputs` read only the schema,
  statistics, views, operations and credential_capability outputs. None of the three
  database publish paths (Survey Definition `_publish`, the engine host's `publisher`, and
  `publish_local_survey`) reached them. `publish_proposals` in `column_profile_step.py`
  still has no caller.
- **Confirmed and fixed by the same PR: `QualityScoreAnnotation`'s `qualityScores` map
  was dropped by Egeria.** `QualityAnnotationProperties` has an `int qualityScore` and
  `ignoreUnknown=true`. The bug was already pinned as a strict xfail in `KNOWN_DROPS`
  (`test_classification_annotation_fields.py`). The map now rides in `jsonProperties`, and
  `egeria_reader` restores it under the old key.
- **The sampling configuration is not wired.** No caller passes `sampling=`, so
  `resolve_sampling_config` always returns its defaults.
- **The engine host never forwards `requestParameters`.** They are stored on the target and
  never passed to the step runner.
- **The brief misattributed CLAUDE.md rule 12.** That rule concerns annotation class names,
  not the generic `create_governance_definition`. The distrust of the generic call comes from
  the Automate section ("untested-for-this-type").

## 13. Slice 2 and later (one paragraph each)

**Slice 2: shape.** Port TestGen's secondary profiling and functional-type inference
(`functional_data_type`, `std_pattern_match`, frequent values and patterns, lengths). That
unlocks Recency, Email_Format, Pattern_Match, LOV_Match, Min_Val, Distinct_Value_Ct,
Unique_Pct and Valid_US_Zip. It also brings sampled measures through `sampling.py`, with
`sample_ratio` calibration, and lets the "sampled" label reach the UI.

**Monitors.** Freshness_Trend, Volume_Trend and Metric_Trend with statsmodels SARIMAX
thresholds. These need RE to keep a per-check result history, which
`database_quality_results` already provides, and a "training" status that is distinct from
`pass`.

**User-authored checks.** Dupe_Rows, Text_Length_Range, Max_Val, Max_Date, and CUSTOM (still
from vendored SQL, with user parameters). User-written SQL needs its own security design and
is out of scope.

**Other engines.** These come through the `EngineCapabilities` seam: the vendored catalogue
already has 11 dialects, so a new engine adds a connection adapter, not new checks.
`DataQualityRule`s from other engines (`checkType=sql`, Soda, Great Expectations, or ODCS
imports) stay theirs to run. RE could later read and display their results, but never run
their SQL.
