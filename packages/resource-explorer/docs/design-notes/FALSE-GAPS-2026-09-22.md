# False gaps in the question catalogue — 2026-09-22

**For:** the coordinating session, and stream 4 (`docs/dr-egeria/resource_questions.csv` rows).
**From:** a review session, working from `main` at `e8270c95`.
**Action needed:** 18 CSV rows. No code change.

## What is wrong

18 questions render `kind: gap` — the UI shows *"No mechanism exists for this yet"* and
offers **no Run button** (`can_run` is empty) — while the analysis each one names is built
and registered in `analysis_catalog.yaml`. Sixteen database analyses are registered; the
catalogue admits to almost none of them.

The reverse check is clean: **every** analysis id named by a `gap` question exists. There
are no genuinely-unbuilt named analyses left in database, filesystem or dataset.

## Why the existing guards miss it

Nothing here is any one slice's mistake. The rows were authored 2026-09-20 with
`GAP: <id> (proposed)` where no analysis existed *then* — correct at the time. Every
implementation slice since is forbidden from editing the CSV
(`COORDINATOR-BRIEF-MULTI-RESOURCE.md`, stream 4 owns rows;
`POSTGRES-NESTED-COLUMNS-IMPLEMENTED.md` §5 observes that restriction "to the letter" and
refreshes only the generated YAML). So regeneration picks up `analysis_ids: [<id>]` while
`kind` stays `gap`, because kind derives from the `GAP:` prefix in the CSV the slice may
not touch.

`test_a_clean_tree_regenerates_to_nothing` cannot catch this: the YAML *is* exactly what
the CSV generates. Both artifacts agree with each other, and both are wrong about the code.

## The fix, per row

Replace `GAP: <id> (proposed) …` in `Answering Analysis` with the bare id — or `MIXED:` /
`PARTIAL:` where the shipped analysis only answers part of the question — then regenerate:

```bash
python scripts/csv_to_question_catalog_yaml.py docs/dr-egeria/resource_questions.csv \
    --output resource_explorer/configdata/question_catalog.yaml
```

Worth deciding per row whether the analysis answers the whole question or half of it;
several of these read like `MIXED:` rather than a bare id.

## New guard

`tests/test_question_catalog_gap_guard.py` fails when a question claims `gap` for an id
present in `analysis_catalog.yaml`. It fails today with all 18, and keeps the loop closed
as filesystem, dataset and model fill in.

## Also worth a look

- **Eleven database analyses are named by no non-`gap` question at all**, including
  `egeria_db_survey`, which nothing in the question layer references.
- `docs/dr-egeria/resource_questions_guide.md` is stale in three places: its
  `Answering Mechanism` list omits `Database Catalog Query`, `Value Profiling` and
  `External Service Query`, all in use; `KNOWN_ANALYSIS_IDS` is documented as 15 ids
  against 56 registered; and the closing bullet still says the file "only covers `repo`
  questions today".
- The `[checks: <analysis>:<check>]` suffix is used in 7 rows and documented nowhere.

## The rows

### database questions — 18

| Question | Built analysis it names | `Answering Analysis` today |
|---|---|---|
| Is this database alive — writes since the statistics were reset, last va | `db_activity_signals` | GAP: db_activity_signals (proposed) — the tuple and scan counters are not read by any analys… |
| Is this database a primary or a replica, is it clustered, and is WAL arc | `db_resilience` | GAP: db_resilience (proposed) — none of the replication, archiving or clustering catalog rea… |
| What kind of database is this — transactional, analytical, reference dat | `db_classification` | GAP: db_classification (proposed) — the inputs are all available and nothing derives a class… |
| Is there a data model here — do the tables relate through foreign keys,  | `db_relationship_graph`, `schema_inventory` | GAP: db_relationship_graph (proposed) — schema_inventory records the constraints but builds … |
| What is the grain of each table — one row per what? | `grain_determination` | GAP: grain_determination (proposed) — Egeria's DataGrain and DataGrainAnnotation types exist… |
| Does this look like a copy or a subset of a database we already know? | `db_fingerprint` | GAP: db_fingerprint (proposed) — FingerprintAnnotation exists as a type; no signature is com… |
| Which columns hold semi-structured data (JSON, JSONB, XML, arrays or hst | `schema_inventory` | GAP: schema_inventory records the column types but derives no semi-structured-column check f… |
| Which columns conform to a known Data Class, with what confidence, and w | `data_class_match` | GAP: data_class_match (proposed) — today's matching runs over view-lineage leaf columns only… |
| Which low-cardinality columns conform to a known reference-data set, and | `reference_data_match` | GAP: reference_data_match (proposed) — nothing compares distinct values against ValidValueSe… |
| What is inside the JSON, JSONB and XML columns — keys, nesting, types, a | `nested_column_profile` | GAP: nested_column_profile (proposed) — semi-structured column contents are never sampled or… |
| Which tables have no primary key, no foreign key, no comment, unused ind | `schema_conventions` | GAP: schema_conventions (proposed) — none of these checks exists for databases.… |
| What does this database depend on outside itself — foreign data wrappers | `db_external_dependencies` | GAP: db_external_dependencies (proposed) — none of these catalogs is read today.… |
| How is this database changing — rows inserted, updated and deleted per t | `db_change_rates`, `row_count_snapshot` | GAP: db_change_rates (proposed) — row_count_snapshot records a point in time and nothing der… |
| How complete and consistent is this database's documentation? | `schema_conventions` | GAP: neither the proposed database comment-coverage measure nor schema_conventions exists ye… |
| How well-modelled is this database — keys, constraints, foreign-key cove | `grain_determination`, `schema_conventions` | GAP: schema_conventions and grain_determination are both proposed.… |
| How well-governed is this database's reference data — what share of its  | `reference_data_match` | GAP: reference_data_match (proposed) supplies the checks, and nothing measures the bound sha… |
| Is this database maintained and healthy — vacuum recency, dead-tuple rat | `db_activity_signals` | GAP: db_activity_signals (proposed) — vacuum recency and dead-tuple ratios sit in pg_stat_us… |
| Is this database resilient — replica present, archiving on, backup evide | `db_resilience` | GAP: db_resilience (proposed) combines catalog facts with an Enrichment "Backup and recovery… |

### filesystem questions — 3

| Question | Built analysis it names | `Answering Analysis` today |
|---|---|---|
| What is the grain of each table — one row per what? | `grain_determination` | GAP: grain_determination (proposed) — Egeria's DataGrain and DataGrainAnnotation types exist… |
| Which columns conform to a known Data Class, with what confidence, and w | `data_class_match` | GAP: data_class_match (proposed) — today's matching runs over view-lineage leaf columns only… |
| Which low-cardinality columns conform to a known reference-data set, and | `reference_data_match` | GAP: reference_data_match (proposed) — nothing compares distinct values against ValidValueSe… |

### dataset questions — 1

| Question | Built analysis it names | `Answering Analysis` today |
|---|---|---|
| Which columns conform to a known Data Class, with what confidence, and w | `data_class_match` | GAP: data_class_match (proposed) — today's matching runs over view-lineage leaf columns only… |
