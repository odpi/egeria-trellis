# §16's Scouting and Discovery rows — `subject_signals`, `coverage_signals`, the time-grain extension, `preliminary_fit`

**Built against:** `multi-resource-questions-design.md` §16 (§16.1–§16.7), `main`
at `5e210a4d` (the merge of `#266`).
**Scope:** §16.3's table, **Scouting and Discovery rows only**, **databases
only** — the same database-first scoping `#254`/`#257`/`#266` used.

**Not built, deliberately:**

- The full `DataLens` Egeria governance definition — its versioning, its
  Curate-tier "make this my lens" actions, investigation-framing integration
  (§16.5 points 3–5). §16.6 is the authority for splitting it out:
  *"`preliminary_fit` works before it exists, as a comparison across
  resources"*, and §16.5 point 2 says the same. What is built takes a plain
  **ad-hoc lens mapping**, using §16.6's own `scopeElements` key names, or
  none at all. See §4 below.
- `coverage_profile`, `quality_dimensions`, `requirement_fit` (§16.3's
  Analysis/Assessment rows) — later phases per §16.6.
- Filesystem and dataset variants: Parquet/Feather footer statistics (§16.2
  row 5) and DCAT/Croissant descriptors (row 6) are Phase 2
  `filesystem_structure` work. `coverage_signals` reports them as
  not-attempted for a database rather than silently omitting them.
- §16.7 item 2's *resource-location* group on `coverage_signals` (endpoint
  host, cloud region, jurisdiction) — a different fact from the data's own
  extent, and its own slice.
- No new UI surface. All three appear as ordinary catalog analyses with
  ordinary envelopes, which is what `/next`'s Questions checklist reads.

## 1 · They are `db_derived` checks, not a new step

All three reason over rows RE already stored and open no connection, which is
exactly `db_derived`'s contract, so they were added to it (three new entries in
`DB_DERIVED_ANALYSES`, three new `derived` keys, three new annotation
builders) rather than as a new step.

**Consequence for `#262`'s `requires_capability`:** there is no new step, and
`db_derived`'s declaration stays **undeclared (`""`)**. That is the correct
answer, not a gap, and it is `DATABASE-STEP-CAPABILITY-AUDIT.md` §4's own
reasoning: the weakest tier, `catalog`, still implies a live connection to
something, and this step opens none. Declaring `catalog` to satisfy a
"everything here is catalog-tier" instinct would state a requirement the step
does not have — a small instance of the collapse the capability axis exists to
prevent. `tests/test_fit_and_coverage_signals.py::
TestStepIntegration::test_db_derived_still_declares_no_capability_requirement`
pins it.

**What the *inputs* need is a separate matter, and it is where the capability
work actually bites.** `coverage_signals`' temporal block reads `pg_stats`
histogram bounds, which `postgres_schema_and_stats` stores and which needs the
`stats` capability. So a credential without `pg_monitor` yields
`stats_not_populated` — and `preliminary_fit` renders that as
`could_not_check`, naming both remedies (run `ANALYZE`; the credential may not
be able to see `pg_stats` however often it runs). No precondition was added
for it: the only producer of a *profile* row is `postgres_column_profile`,
which is `api_heavy`/`read`, so declaring a `requires_context` on it would make
the cheapest step in the catalog demand the most expensive one and cross the
tier `prerequisite_resolver` guards. The gap is reported in-band instead.

## 2 · Schema containment (`#266`)

`subject_signals`, `coverage_signals` **and** `preliminary_fit` are all
per-container as well as whole-database, joining the four structural checks in
`apply_container_grain`. `#266`'s own note anticipated this ("those analyses do
not exist yet; they inherit the grain when they are built against the same
declaration"), and each has a reason of its own:

| Analysis | Rollup kind | Why the flat answer is wrong |
|---|---|---|
| `subject_signals` | `union_of_terms` | a `sales` schema and an `hr` schema have two subjects; one flat term list reads as one confused subject |
| `coverage_signals` | `widest_span` | the database-wide window is MIN start / MAX end, so one schema covering 2019 and another 2026 render as a span **no schema actually covers** |
| `preliminary_fit` | `verdict_counts` | the expensive pass runs per schema, and "one of six schemas fits" is a materially different answer from "the database fits" |

`grain_determination` keeps its existing `aggregation` marker — already per
table, nothing to roll up.

## 3 · `grain_determination`: what was already built, and what is new

**Already built** (confirmed by reading `determine_grain`, not assumed):

- entity grain from the declared primary key, with its own confidence (90);
- §16.2's *"a date column **in** the PK means per-period grain"* — `_grain_from_pk`
  already set `interval` from a PK date column;
- the profile fallback (a column whose `n_distinct` approaches the row count);
- per-table output, and the three absence states (`no_candidate_key` as a real
  modelling gap, `insufficient_signal` as an absence).

**New**, per §16.3's *"extended with time grain from naming"*:

- a period word in the **table name** (`daily_sales`, `orders_hourly`);
- a **date-shaped table suffix** (`events_202503`, `events_2025`) — Postgres's
  child-partition naming habit;
- a column **named** like a date whatever its type (`day integer`,
  `period text`) — the warehouse case a type-only test reads as "no date
  column at all";
- and the part that makes all of them usable: an explicit
  `interval_basis`, `interval_confidence` and `interval_signals` on every
  entry. Before this, "monthly" from a primary key and "monthly" guessed from
  a table's name were the same bare string. A disagreement between signals is
  now recorded rather than silently outranked.

**Judgement call:** §16.2 names *"partition keys `year=/month=/day=`"*. That
literal form is a Hive/Parquet **directory layout** — a filesystem signal that
no stored database row carries — so it is not looked for here; the database
equivalent above is. Real partition *bounds* (`pg_partitioned_table`, child
`CHECK` constraints) would be exact and free, and **nothing in RE collects
them**, so `coverage_signals` reports that as a collection gap with the step
that would close it, never as "this database is not partitioned".

## 4 · `preliminary_fit`'s four verdicts

`fits` · `does_not_fit` · `could_not_check` · `no_requirement_declared`, and
the third is why the analysis needed care rather than an afternoon:

- an input that is itself not established (§16.2's ANALYZE case, a schema the
  credential cannot see per `#266`) produces `could_not_check` — **never** a
  miss and never a pass, with the input's own remedy carried up to the
  verdict;
- with **no lens** the verdict is `no_requirement_declared` and the payload
  carries `achievable`: what this resource *could* satisfy (§16.5 point 2).
  Never a vacuous pass. This is the default on every code path in the tree
  today, because nothing stores a lens yet;
- a lens declaring **only** criteria this tier cannot compare (regions, data
  classes, a grain statement) is also `no_requirement_declared`, with each
  criterion reported in `deferred_criteria` naming the analysis that answers
  it — not a pass reached by skipping the test;
- `lens_version` and `lens_source` are recorded (§16.5 point 4). An ad-hoc
  lens has no version and the field is empty, which is the honest answer
  rather than a fabricated one. Wiring a stored, versioned `DataLens` later
  changes the **caller**, not `compute_preliminary_fit`.

Subject non-overlap is a **weak** disqualifier (confidence 25) because §16.2
grades name-derived subject low–medium; a database holding the subject under
unfamiliar names looks identical. A *measured* absence of date columns is a
**strong** one (90), because that is a fact about the schema.

## 5 · Where the questions landed

Three new rows in `docs/dr-egeria/resource_questions.csv` (→
`question_catalog.yaml`, regenerated by
`scripts/csv_to_question_catalog_yaml.py`, never hand-edited), and three new
`database_analyses` entries in `analysis_catalog.yaml`.

**Stage, and a divergence from §16.3 worth stating.** §16.3 files
`subject_signals` and `coverage_signals` under **Scouting**; both are recorded
here as **Discovery**, with `intent: discovery`. CLAUDE.md rule 17's axis is
*does this collect, or reason over what is already collected* — these reason,
over stored rows, and §16.2's "Scouting" placement is a claim about the
**signal being free**, which it is: no fetch is added anywhere. The same
judgement already placed `grain_determination` — also a §16.3 Scouting row — in
Discovery before this change. Recorded in each row's `Catalog History` so the
divergence is visible rather than looking like an error.
