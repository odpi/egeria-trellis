# Beyond repositories — questions, analyses and surveys for databases, filesystems, open data and models

**Status:** design and planning input. First draft 2026-09-20; revised the same
day after project-owner review (27 points, recorded inline as decisions and as
new sections). Nothing here is built.

**Decision (project owner, 2026-09-20):** the DB/FS freeze of 2026-08-31 /
2026-09-02 is lifted for both design and implementation. `/next` stays
repo-only until repositories are complete; DB/FS work lands in the classic UI
and the shared layers, with requirements handed to the UI designer (§11).

This document does five things:

1. States what carries over from the repository path and what does not (§1–§2).
2. Draws the Egeria-native vs. RE-local line, adds the efficiency and local-copy
   rules, and names the remote-coordinator case (§3).
3. Proposes question sets, per funnel stage, for four resource types —
   database, filesystem/file, open dataset, AI model — with the analysis that
   answers each and whether Egeria or RE computes it (§4–§8).
4. Specifies the pieces that are not analyses: change and notification (§9),
   Purpose in Egeria (§10), UI representations for the designer (§11), and
   the Egeria extensions worth asking for (§12).
5. Sequences the work (§13) and records the decisions taken and still open (§14).

Facts about the current code are from a read on 2026-09-20 and carry
`file:line` references so they can be re-checked.

---

## 1. Where things stand

### 1.1 The question model is already resource-type-agnostic — except in five places

The Egeria side is shared by design: one *User Questions* glossary, one *Funnel
Stages* glossary, twelve Perspectives, the `ScopedBy` / `Asked At` pattern
(`docs/dr-egeria/foundations/foundations.md`). Nothing there names repositories.
Purposes (`Explore, Select, Assess, Maintain, Share, Learn, Certify, Remediate,
Attest, Deploy`) live only in the CSV and YAML — not modelled in Egeria at all
(`scripts/csv_to_dr_egeria_questions.py:97-101` excludes the column
deliberately). §10 specifies how to model them.

The RE side hardcodes `repo` in exactly five places:

| # | Where | What |
|---|---|---|
| 1 | `scripts/csv_to_question_catalog_yaml.py:74` | `_load_known_analysis_ids()` reads only `repo_analyses`; a database question naming `schema_inventory` silently becomes `kind: unknown` |
| 2 | `scripts/csv_to_question_catalog_yaml.py:345` | emits a literal `{"repo_questions": ...}` |
| 3 | `surveyors/question_catalog_reader.py:122-133` | `_load()` returns `{"repo": [...]}`; `get_questions("database")` returns `[]`, indistinguishable from "filtered to nothing" |
| 4 | `surveyors/question_catalog_writer.py:56-59` | one CSV/YAML/lock path |
| 5 | `facts.py:589-618`, `:565-569`, `:647-659` | `RESOURCE_STATE_SOURCES` keys are repo question text; `FactLayer` imports `REPO_ANALYSIS_RESULTS_MAP` unconditionally |

Plus the survey-definition generator (`scripts/generate_repo_survey_definition.py:95`,
`TECHNOLOGY_TYPE = "Git Repository"`), which is per-type by construction and
will be cloned rather than generalised.

`get_questions(resource_type, phase, perspectives, purposes)` itself is generic.
The Backlog already has this item (`docs/Backlog.md:3692-3724`, `:530-559`).

**Decision (project owner, 2026-09-20):** one CSV, with a `Resource Types`
column (`;`-separated, `*` for all). Existing repo questions may be reworded to
be resource-independent.

### 1.2 The 52 repo questions, by shape

| Stage | Count | Notes |
|---|---|---|
| Scouting | 5 | maintained? what is it? who? adoption? languages |
| Discovery | 12 | catalogued? surveyed? existing use? feedback? worth it? which definition? kind of thing? components? |
| Analysis | 20 | dependencies, licence, deployment, integrations, docs, secrets, telemetry, CVEs |
| Analysis/Enrichment | 7 | human-supplied fit questions (cost, skills, monitoring, security, governance) |
| Assessment | 7 | maturity, licence risk, scorecard, badge, bus factor, size/complexity |
| *(Automate)* | 1 | "changed since last survey?" — **not a stage**; see §2.1. Moves to Discovery, cross-type |

`kind` distribution in the generated YAML: analysis 23, direct 11, mixed 7,
human 6, gap 3, chart 1, partial 1. Twenty-one distinct `Answering Mechanism`
values, all combinations of eleven primitives.

**Roughly 18 of the 52 are not about repositories at all.** They are about *a
resource we are considering*: catalogued? surveyed? existing use? feedback?
restrictions? replaces something we have? similar things? cost to run? skills?
fits our governance/security/monitoring? worth investigating? changed since
last time? These are the seed of the cross-type set in §4.

### 1.3 What RE already has for databases and filesystems

**Database (PostgreSQL only).** Schema/table/column introspection with PK, FK,
comments, sizes, row counts and vacuum/analyze state
(`surveyors/database/connection.py`); view DDL parsed with SQLGlot for
dependencies, column lineage and complexity (`sql_analyzer.py`); PII data-class
matching **over view lineage leaf columns only** (`database_surveyor.py:390-445`);
a `bootstrap_data_classes.py` that seeds six PII Data Classes in Egeria; three
execution routes (`resource-explorer`, `egeria`, `egeria-adaptive`) in
`survey_definition_adapter.py`; native-survey trigger and read-back; two
registered databases (`coco_ods`, `coco_pharma`) in the live inventory. Four
catalog entries: `schema_inventory`, `row_count_snapshot`, `privilege_audit`,
`egeria_db_survey`. **No** value-level column profiling, **no** reading of
`pg_stats` / `pg_stat_user_tables` tuple counters, **no** PII scan of base
tables, **no** DuckDB/other engines.

**Filesystem.** One `stat()`-per-file walk with counts, sizes, flags,
timestamps, inaccessible-file capture and Egeria reference-data classification
(`filesystem/local_filesystem_surveyor.py:114`), explicitly built for parity
with the native `survey-folder` annotations; pandas/pyarrow profiling of
CSV/TSV/Excel/Parquet/Feather (`sub_surveyors/data_profiler.py`); Egeria
publish creating a `DataFolder` plus one `DataFile` asset per data file. One
catalog entry, `filesystem_inventory`. **JSON/JSONL is classified but never
profiled. `docling` is a dependency but nothing on the filesystem path parses
documents.** The design's two-step split (`filesystem_structure` vs
`filesystem_data_profiling`, §5 of the FS/DB doc) is *not* what shipped —
there is one combined step.

**Shared gaps, both types.**

- Findings live only as opaque `survey_data` JSON blobs on `database_surveys` /
  `filesystem_surveys`. There is no `database_tables`, `database_columns` or
  `file_inventory` table. Repo findings have fourteen structured detail tables.
  Without structured rows, per-object questions ("which tables have no
  primary key?"), diffs, trending and change detection all have to re-parse
  JSON (`web/routes/databases.py:655` already does).
- No survey definitions exist for either type **(no longer current — Slice
  12, 2026-09-26, authored three for database: Scouting/Analysis/
  Assessment, in their own `docs/dr-egeria/survey-definitions-database/`
  directory)**; the ten in `docs/dr-egeria/survey-definitions/` are still
  all `repo-*`, and filesystem has none yet.
- `executes_at: egeria` has no test coverage on either type; the hybrid
  surveyors have none (`Backlog.md:875-935`, Tier 1).
- The `next` UI is repo-only by decision (`next/app.js:121`, `:1833`); the
  classic UI has full DB and FS report views.
- There is no `ResourceType` enum; the tuple `("repo","database","filesystem")`
  is re-declared in at least five modules.

### 1.4 What Egeria provides natively

Read from the Java source on 2026-09-20 (`egeria-docs` is not checked out
locally; the connector code is authoritative for behaviour):

| Service / request type | Technology type | Analysis steps | Produces |
|---|---|---|---|
| `folder-survey-service` / `survey-folder` (+ `-and-files`, `-all-folders`, `-all-folders-and-files`) | File System Directory, Data Folder | CHECK_ASSET, MEASURE_RESOURCE, PRODUCE_ACTIONS, PRODUCE_INVENTORY | ResourceMeasure (18 `FileDirectoryMetric`s), ResourceProfile ×4 (extensions, file types, asset types, deployed impl types), ResourceProfileLog (file names → external CSV), RFA ×2 (missing reference data, inaccessible) |
| `data-file-survey-service` / `survey-data-file` | Data File | CHECK_ASSET, MEASURE_RESOURCE | ResourceMeasure (11 `FileMetric`s), SchemaAnalysis |
| `csv-file-survey-service` / `survey-csv-file` | CSV File | + SCHEMA_EXTRACTION, PROFILE_DATA | same pair, CSV variant |
| `postgres-database-survey-service` / `survey-postgres-database` | PostgreSQL Relational Database | CHECK_ASSET, PROFILING_ASSOCIATED_RESOURCES | schema/table/column lists, table sizes, database/schema/table/column measurements, **frequent values per column** |
| `postgres-server-survey-service` / `survey-postgres-server` | PostgreSQL Server | + PRODUCE_INVENTORY | server-level subset |
| DuckDB, Oracle, MSSQL, Kafka, Unity Catalog (server / catalog / schema / volume), Apache Atlas | — | same shape | — |

**Ground truth from a live run (2026-09-21, `design-notes/PROBES-2026-09-21.md`).**
The table above was read from the annotation-type enums. The first native
`survey-postgres-database` that completed end to end (against the Prefect
server's own database, 36 tables, engine action COMPLETED in about 225 s)
produced 222 annotations, **all `ResourceMeasureAnnotation`**, in four
annotation types — database, schema, table (×38) and column (×182)
measurements — every one under analysis step *Profiling Associated
Resources*, with 37 distinct `resourceProperties` keys across the four
levels. **No `ResourceProfileAnnotation` appeared, and none ever will from
this service**: `PostgresDatabaseStatsExtractor.java` has exactly four
`ResourceMeasureAnnotation` call sites and never builds a profile
annotation. "Frequent Values for Column" is real but lives as two
`resourceProperties` keys on the column-level measure (*Most Common Values*
and *Most Common Values Frequency*, sourced from `pg_stats.most_common_vals`
/ `most_common_freqs`), present with real content in the dump. So frequent
values are **native, rule A**, with one precondition RE's envelope must
report: Postgres's own `ANALYZE` has to have run on the table, or the keys
are absent — absent meaning "no statistics", not "no common values".
Rule A's key set for databases is that dump, not this table. The run also
bound the endpoint as `host.docker.internal:5442`, which works only because
the quickstart is a single container; a multi-host deployment needs the
container-network name, the `network_unreachable` case rule B still has to
handle.

Universal request parameters: `finalAnalysisStep`, `ignoreAnalysisSteps`;
folder surveys take `analysisLevel`. Completion guards: `survey-completed`,
`survey-invalid`, `data-certified`, `data-not-certified`,
`missing-certification-type`, `missing-schema-type`.

**Two caveats the Java documentation states that RE's local surveyors must
preserve:** the Postgres and Unity annotations say that *missing* schemas,
tables, columns or volumes indicate **the survey userId lacks permission**, not
absence; and `Missing File Reference Data` means Egeria's classification
reference data did not match, not that the file is uninteresting. Both are the
`find-absence-as-answer` shape.

RE already knows how to trigger and read these: `egeria_tech_type_catalog.py`
reads real `producedAnnotationType` specs; `technology_type_processes.yaml`
names the Postgres and folder processes; `egeria_survey_reader.py` walks
`ReportedAnnotation`. ISSUE-79 is still open server-side but its recorded
cause was **disproved** on 2026-08-30: a template-created folder does get a
connection and the native folder survey passes an FVT suite end to end. The
remaining failure mode is a legible `OPEN-SURVEY-0009 NO_ASSET_CONNECTOR`,
which `coco_ods` currently hits because it was catalogued with placeholder
connection values (`Backlog.md:834-870`).

**Egeria types the later sections lean on** (verified in `OpenMetadataType.java`):

| Concern | Type | Shape |
|---|---|---|
| Data classification | `DataClass`, `DataClassAnnotation`, `DataClassAssignment` | match assessment between a data class and stored values |
| Reference data | `ValidValueDefinition` / set, `ValidValuesAssignment`, `ReferenceValueAssignment` | controlled vocabularies; the second relationship tags any Referenceable with a valid value |
| Grain | `DataGrain` (`granularityBasis`, `grainStatement`, `interval`), `DataGrainAnnotation` | "one row per …", model 0541 / 0626 |
| Scope in space and time | `DataScope` classification (lat/long bounding box, collection / validity / coverage start–end, `scopeElements` map) | model 0210; the sovereignty carrier |
| Lens | `DataLens` (governance definition) | scope of data for a type of processing |
| Ownership | `Ownership` classification | owner + owner type |
| Licence / agreement | `License`, `LicenseType`, `Agreement`, `DataSharingAgreement` | |
| Products | `DigitalProduct`, `DigitalProductCatalog`, `DigitalSubscription` | the demand side lives on subscriptions |
| Models | `DeployedAnalyticsModel`, `AnalyticsModelRun`, `ExternalModelSource` (model 0265, Analytics Assets) | extendable as needed (project owner, 2026-09-20) |
| Notification | `NotificationType`, `MonitoredResource`, `NotificationSubscriber` | pyegeria has link/detach methods; creation of the type has no dedicated method (`docs/automate-notification-manager-pyegeria-spec.md`) |

There is no model-card type; §12 covers what to ask for.

---

## 2. What carries over and what does not

**Carries over unchanged:** the funnel stages (Scouting, Discovery, Analysis,
Assessment, Enrichment, Understanding, Curate); the twelve Perspectives; the
Purposes vocabulary; the CSV → YAML → Egeria-term pipeline; the `FactLayer`
envelope with its `state` (measured / nothing_found / not_established /
partial), `last_run_at`, `can_run`; the "questions are the composition key"
rule; the two independent cost axes; the `analysis` vs `step` vs `survey
definition` distinction; the sub-resource model; Curate, Enrichment, RFAs.

**Carries over as a pattern, needs a per-type instance:** the results-map that
turns an `analysis_id` into a value; the state-source resolvers; the step
registry and survey-definition generator; the structured detail tables.

**Does not carry over:** the forty repo steps. Egeria has seventeen native
survey services for files, databases, catalogs and platforms and none for
repositories; that asymmetry is why the repo path built everything itself.

### 2.1 Automation is a property of any survey, not a stage

**Decision (project owner, 2026-09-20):** Automate is not a funnel stage. Any
survey can be scheduled; changes produce notifications.

Consequences for this design:

- The CSV's one `Automate` row becomes a cross-type **Discovery** question
  ("how much has changed since the last survey?"), because it is asked *before*
  deciding to spend on a re-run.
- `Funnel Stage` values in the CSV are the seven above. The `automate` intent
  tab in the UI is unaffected — it is where schedules and subscriptions are
  *managed*, which is a different thing from a stage a resource passes
  through. CLAUDE.md's "eight intents" text should say so explicitly when this
  lands.
- Every analysis gets the same three automation affordances — schedule it,
  subscribe to its changes, see its history — instead of some analyses being
  "automate" analyses. This is already how `resource_schedules` and
  `notification_subscriptions` are keyed (`analysis_id`), so it is a
  presentation change, not a model change.
- What "changed" means is per analysis and per resource type, and is the
  subject of §9.

### 2.2 The one principle to add

**Every finding RE publishes for a database or file must use the annotation
type and metric keys Egeria's own service would use for the same finding.**
`FileDirectoryMetric`, `FileMetric`, `RelationalDatabaseMetric`,
`RelationalSchemaMetric`, `RelationalTableMetric`, `RelationalColumnMetric` are
enumerated in the Java source and should be copied verbatim. The FS/DB doc
records the repo path's own version of this mistake (code findings published as
generic `ResourceMeasureAnnotation` while `CodeAnalysisAnnotation` exists).

---

## 3. Who computes what, and where results live

### Rule A — if Egeria has a native service for the finding, that service's output shape is canonical

Whoever computes it, the annotation type, metric keys, `analysisStep` label
and summary wording match the native service. Provenance goes in `source`
(`egeria` / `egeria-custom` / `custom`, which `hybrid_database_surveyor.py`
already labels) and in the survey report's engine identity.

### Rule B — reachability decides *whether* RE must run locally; efficiency decides *whether it should*

**Decision (project owner, 2026-09-20):** generally yes to the reachability
rule; and there is an efficiency question alongside it.

**Credentials, identities and capabilities are specified in
`docs/security-model.md` (2026-09-25)**, which supersedes the credential
details below and in rule C: two identities (catalog at registration, data
at the gate), a four-value capability vocabulary bound per step, a
structural floor declared per engine, and the operational rule for which
connection Egeria's own surveys receive.

*Reachability.* The engine host runs in a container on the Egeria network. A
database at `localhost:5442` from the laptop is reachable to the container only
under a different hostname; a laptop directory is unreachable unless mounted;
a database behind a VPN is out of reach permanently. `coco_ods` failing with
`NO_ASSET_CONNECTOR` is this problem in miniature. The procedure:

1. Is the asset catalogued with a connection the engine host can open?
   (`_warn_if_database_has_no_connection`, `egeria_database_surveyor.py:185`,
   checks the first half; actually probing from the engine host's side is new
   and needs a tiny "check asset" engine action, which `CHECK_ASSET` already
   is — run the native survey with `finalAnalysisStep=CHECK_ASSET`.)
2. Reachable → eligible for native.
3. Not reachable → RE local mirror, published under native shapes, plus an
   RFA "Egeria cannot reach this resource" so the gap is visible.

**Amended 2026-09-21 — reachable means network *and* a resolvable secret.**
Probe 9 (`design-notes/PROBES-2026-09-21.md`) showed that "has a connection"
is not "can open it". `coco_pharma` was catalogued with a real
`VirtualConnection`, the existence check passed, and the native survey still
failed with a SCRAM authentication error, because the embedded
`SecretsStoreConnection`'s `secretsCollectionName` was the literal template
placeholder — RE's catalogue path bound the user id and password placeholders
and never that one (`egeria_database_surveyor.py:254-265`, `:277-288`). Two
failure signatures now exist and both must render as "Egeria cannot reach
this", never as an empty survey:

| Signature | Meaning | Where it is caught |
|---|---|---|
| `OPEN-SURVEY-0009 NO_ASSET_CONNECTOR` | no connection on the asset at all | step 1's existence check |
| `HikariPool … SCRAM-based authentication, but no password was provided` | connection exists; its secret does not resolve on the engine host | only by running `CHECK_ASSET` (step 1's second half), or by RE checking the collection name it bound |

**Decision (project owner, 2026-09-21):** Egeria does not share its bundled
secrets, so **RE keeps its own secrets store** using Egeria's client-side-secret
structure — one RE-owned secrets-store Connection (YAML provider, file under
`/deployments/secrets/`, readable by the engine host), one collection per
registered resource, written through `save_client_side_secret` at
registration, and the collection name bound into the template placeholder on
the fresh-create path. Implemented in dwolfson/trellis#185 (branch `re/own-secrets-store`). Two
consequences:

- The reuse-by-qualified-name path never re-runs the template, so assets
  catalogued before the fix (`coco_ods`, `coco_pharma`) keep the literal
  placeholder until they are deleted and re-created.
- Step 1 gains a third check RE can do without an engine action: does the
  collection RE bound for this resource exist in RE's store? Missing means
  not reachable, and the RFA should say "no secret", not "no connection".

*Efficiency*, when both can run. The axes that decide it, all measurable:

| Axis | Favours Egeria native | Favours RE local |
|---|---|---|
| Data locality | engine host co-located with the data; RE across a WAN | RE on the same box as the data (the developer-laptop case) |
| Latency to first result | — | RE is synchronous; a native survey is an engine action with polling (`egeria_async_survey_result.py`) |
| Load on the source | one connection from the engine host | RE already holds a connection for the interactive session |
| Compute the finding needs | native has it (folder counts, Postgres profiling) | native lacks it (rule C) — no choice |
| Concurrency | engine host queues; good for batch of many resources | RE threads / Prefect; good for one resource now |
| Result provenance | report authored by a governance engine — stronger for Certify/Attest | fine for Explore/Select |

Proposed policy, simple enough to explain in the UI: **native by default when
reachable and the Purpose is Certify/Attest/Maintain; local by default when
the user is interactive (Explore/Select/Assess) and RE is on the data's side
of the network; either way the other is one click.** Record both the declared
and observed cost per run (`step_cost_observer.py` exists) so this policy is
tuned from numbers, not guessed.

### Rule C — RE owns the findings Egeria does not compute

| Finding | Why Egeria lacks it | RE has it today? |
|---|---|---|
| Data-class conformance over **base tables** (name + value sampling against `DataClass`es) and **proposal of new data classes** | native survey lists columns, does not classify them | only over view-lineage columns |
| Reference-data conformance (low-cardinality columns vs `ValidValueSet`s) and **proposal of new sets** | not a native concern | no |
| Grain determination (`DataGrainAnnotation`: one row per what?) | type exists, no service | no |
| SQL view / function dependency and column lineage | needs a SQL parser | yes (SQLGlot) |
| Privilege and role audit | outside survey scope | catalog entry exists |
| Change over time (tuple insert/update/delete rates, size drift) | native is point-in-time | partly (`row_count_snapshot`) |
| Operational resilience (replication, HA, backup evidence) | outside survey scope | no |
| Naming and documentation conventions | not a native concern | no |
| JSON / XML column and file structure | native CSV only | no |
| Duplicate detection (`FingerprintAnnotation`) | type exists, no service | no |
| Document content (PDF, DOCX, Markdown) via `docling` | no native service | no, on the FS path |
| Glossary-term suggestion (`SemanticAnnotation`) | type exists, no service | no |
| Sovereignty and scope (`DataScope` proposal from data content and connection metadata) | classification exists, nothing populates it from a survey | no |
| Data-product readiness and demand | composite | no |

### Rule D — RE keeps a local copy of every result, whoever ran the survey

**Decision (project owner, 2026-09-20):** yes, a local copy in the RE
operational data store even when Egeria runs the survey, for trending and the
rest.

So the structured detail tables (§5.7) are fed from two directions: RE's own
steps write them directly, and the Egeria read-back (`egeria_survey_reader.py`)
materialises native annotations into the same rows with `source = egeria`.
Egeria stays the catalog of record; RE's store is the operational history.
The rule that follows: **a row is keyed by `(slug, surveyed_at, source)`**, so
a native and a local run of the same day do not overwrite each other and a
trend chart can show either or both.

### Rule E — remote coordinators are a fifth permutation, and the step contract must allow for them

The project owner's future goal: RE coordinates surveys that execute on remote
infrastructure such as Airflow, to reach resources neither RE nor the engine
host can. `docs/survey-execution.md §7` has four permutations; this is a
fifth — *RE initiates, RE orchestrates, steps execute on a third party's
executor*. Not urgent, but it constrains the step contract now:

- A step's inputs must be **serialisable by reference** (resource key, step
  key, parameters), never a live connection or a Python object. The
  `ResourceProvider` / `requires_resources` design (survey-execution §8 D6)
  already points that way.
- A step's output is **annotations plus a guard**, as JSON, which is already
  the adapter return shape.
- The executor is an interface with three implementations: local thread,
  Prefect (exists), Airflow (later). `prefect_adapter.py` is the seam.
- Results from a remote executor arrive asynchronously, like native surveys —
  so the read-back path in rule D is the same path.

---

## 4. The cross-type question set

These apply to any registered resource, authored once, tagged `*` or with a
type list. The reworded repo rows plus the additions from the review:

| Stage | Question (type-neutral wording) | Mechanism | Notes |
|---|---|---|---|
| Scouting | What is this resource, and what is it for? | Direct Field | per-type source field |
| Scouting | **Who owns this data (accountable owner), and who administers the resource?** | Egeria `Ownership` + Enrichment | owner ≠ admin; both matter (project owner, point 18). Repos derive maintainers from git; DB/FS/datasets need Ownership or Enrichment |
| Scouting | **Under what licence or agreement may it be used?** | Direct Field + Egeria `License`/`DataSharingAgreement` + Agent | already a repo row; becomes cross-type and gains the agreement half |
| Discovery | Has this resource already been catalogued in Egeria, and when? | Egeria Queries | |
| Discovery | Has it already been surveyed at any tier, and what did earlier signals reveal? | Direct Field | |
| Discovery | Is there any existing use within our organisation? | Local Registry + Egeria | |
| Discovery | Any known feedback? | Local Registry + Egeria | |
| Discovery | Does it replace or extend something we already have? | Registry + RAG | |
| Discovery | Which Survey Definition should I run, and is there one for this technology type at all? | Direct Field | two existing rows |
| Discovery | Based on what is already known, is this worth investigating further? | Direct Field | dispositions need generalising beyond repos |
| Discovery | **How much has changed since the last survey — is it worth re-running now?** | Change Detection | moved from "Automate" |
| Discovery | **Can Egeria's survey engine reach this resource, or must Resource Explorer survey it locally — and which is cheaper?** | Egeria + local probe | rule B surfaced |
| Discovery / Analysis | **Where is the data physically located, under which jurisdiction, and are there residency or transfer constraints (sovereignty)?** | Connection metadata + Enrichment + `DataScope` | project owner, point 16. Machine part: host/region from the connection endpoint, cloud metadata, file path; human part: legal controller, residency rule. Published as a proposed `DataScope` with `scopeElements` {jurisdiction, region, controller} |
| Analysis | **What is the scope of the data in time — collection period, validity, coverage?** | Profiling (min/max of date columns, mtime range) + Enrichment | the other half of `DataScope`; machine-proposable. **Superseded in detail by §16**, which adds the sought side (`DataLens`), grain, gaps, and fit, and moves the cheap estimate to Scouting |
| Analysis | Are there any restrictions for use beyond the licence (classification, zone, terms)? | Direct Field + Agent | |
| Analysis | What are similar resources, and how does this differ? | Agent over pgvector | gap for every type |
| Analysis/Enrichment | What does it cost to run, host or license? | Human | |
| Analysis/Enrichment | Do we have the skills to support it? | Human | |
| Analysis/Enrichment | Does it fit our governance, security and monitoring frameworks? | Human + Egeria | three existing rows |
| Assessment | **Is this resource ready to be offered as a data product — owner, description, licence or agreement, schema, freshness, contact, scope?** | Composite | the supply side |
| Assessment | **Is there a defined need or market for it — subscriptions, requests, existing use, feedback, similar-search hits, open RFAs asking for this kind of data?** | Egeria `DigitalSubscription` + registry + RFAs | the demand side (project owner, point 15). Both must hold before `DigitalProduct` is proposed |

---

## 5. Databases

Resource: a PostgreSQL database (today) and DuckDB (second; a compose script
exists). Servers are a separate registered resource (`db_servers`) and get the
server-level subset.

**Decision (project owner, 2026-09-20):** server, database, schema and table
are each addressable as resources, the same "sub-resource by default,
first-class on direct registration" rule as files (§6).

### 5.1 Strategy: extract everything the catalog already knows before reading a row

**Decision (project owner, 2026-09-20):** extract as much as possible from the
database catalog, including engine-specific catalog extensions, rather than
relying on standard JDBC-style metadata.

What this buys on Postgres, per source, none of it reading table data:

| Catalog source | Gives | Answers |
|---|---|---|
| `information_schema` / `pg_catalog` (`pg_class`, `pg_attribute`, `pg_constraint`, `pg_description`) | schemas, tables, columns, types, PK/FK/unique/check, comments | schema questions (exists) |
| `pg_stats` (from `ANALYZE`) | per column: `null_frac`, `n_distinct`, `most_common_vals`, `most_common_freqs`, `histogram_bounds`, `avg_width`, `correlation` | **column profiling without sampling** — null share, cardinality, frequent values, value range, width. Staleness known from `last_analyze` |
| `pg_stat_user_tables` | `n_tup_ins/upd/del/hot_upd`, `n_live_tup`, `n_dead_tup`, `last_vacuum/autovacuum/analyze`, `seq_scan`, `idx_scan` | **change over time**, freshness, bloat, whether anyone reads it |
| `pg_stat_user_indexes`, `pg_index` | index usage, unused indexes | modelling quality, admin |
| `pg_roles`, `role_table_grants`, `pg_default_acl` | privileges | `privilege_audit` |
| `pg_settings`, `pg_stat_replication`, `pg_is_in_recovery()`, `pg_stat_archiver`, `pg_stat_bgwriter` | replication role, standbys, WAL archiving, checkpoint activity | **resilience** (§5.5) |
| `pg_extension`, `pg_foreign_server`, `pg_foreign_table`, `pg_publication`/`pg_subscription` | extensions, FDWs, logical replication | external dependencies |
| `pg_stat_statements` (if installed), `pg_stat_database` | query mix, xact and block stats | workload character, db classification |
| `pg_matviews`, `pg_views`, `pg_proc`, `pg_trigger` | views, materialised views, functions, triggers | `sql_analysis` inputs |
| Engine-specific: DuckDB `duckdb_columns()`, `duckdb_tables()`, `duckdb_constraints()`, `duckdb_extensions()`; Postgres `pg_stat_kcache`, `pgaudit`, TimescaleDB/Citus catalogs when present | equivalents, plus extension-owned metadata | same questions, different engine |

Design consequence: the connection layer gains a **capability declaration**
per engine (`supports: {column_stats, tuple_counters, replication_status,
query_stats, ...}`), and each catalog-fed analysis states which capability it
needs. A finding whose capability is absent is `not_established`, not
`nothing_found` — the `pg_stats` route also has a second absence mode, "stats
never collected", which must render as "run ANALYZE" rather than as "no
values". Value sampling (`postgres_column_profile`, §5.7) becomes the
*fallback* for columns whose stats are stale or missing, and the only route for
data-class and reference-data matching, which need actual values.

### 5.1a Catalog values are estimates as of the last utility run — stamp them, and get actuals later

**Project owner, 2026-09-25:** `pg_stats` and its relatives are refreshed
only when a utility runs (`ANALYZE`, `VACUUM`, index builds), so the best
they can say is "an estimate as of the last run". Everything in §5.1's
catalog-first strategy is therefore an *estimate*, and the design needs
three things it did not have: a freshness stamp on every estimate, a
vocabulary for how stale it is, and the later pass that produces actuals.

**The stamp is itself a catalog-tier read.** The same unprivileged activity
view identity A already reads carries `last_analyze`, `last_autoanalyze`
and `n_mod_since_analyze` (rows modified since statistics were taken), so
every estimate is stamped "as of *date*, *N* rows changed since" — a
quantified freshness, not a caveat. `reltuples`/`relpages` refresh on the
same events; `n_live_tup`/`n_dead_tup` are running counters (more current,
still approximate). Per engine, the freshness source goes in the engine
declaration: SQL Server `sys.dm_db_stats_properties` (`last_updated`,
`modification_counter`); Oracle `DBA_TAB_STATISTICS.LAST_ANALYZED` and
`STALE_STATS`; MySQL `mysql.innodb_table_stats.last_update`; DuckDB
computes on the fly, so no staleness.

**Four freshness states**, shown in every envelope and annotation built
from a catalog value: `fresh` · `stale` (with the modification count and
age) · `never_collected` (statistics absent — "run ANALYZE") ·
`not_visible` (this identity cannot see them — §9 of the credentials
reply). A `stats_staleness` comparator (§9.1) fires when age or
modification count crosses a threshold, because "statistics are three
months old" is a finding in its own right.

**Actuals come from the Analysis-tier pushdown pass**, bound to identity
B and labelled *measured*: exact `COUNT(*)`, `COUNT(*) - COUNT(col)`
(estimates can never assert zero nulls), `COUNT(DISTINCT)` or HyperLogLog,
`MIN`/`MAX`, key uniqueness for grain, exact coverage gaps, pattern
conformance — sampled and time-boxed per §5.8. The gate then reads
"estimated from statistics 40 days old (4,120 rows changed since) — run
the measured pass?", which is a better prompt than a bare Run.

**A third basis: recorded at write.** Estimated and measured are not the
only two. Some sources maintain statistics as a byproduct of writing, so
the numbers are exact as of the write and readable without touching data:
Delta Lake and Iceberg keep per-file min, max and null counts in the
transaction log or manifests, stamped with a version or snapshot id;
Parquet and ORC footers carry per-row-group column bounds; Snowflake
micro-partition metadata and BigQuery storage metadata give exact row
counts with no `ANALYZE` concept; Unity Catalog exposes column summaries
to `BROWSE` users alone; and OpenLineage run facets carry output row
counts and sizes emitted by the writing pipeline, which Egeria's Lovelace
service already consumes. For these there is no estimate-versus-actual
gap. So every value carries one of three bases — `estimated` (catalog,
with the freshness stamp above), `measured` (pushdown, identity B, sampled
and time-boxed), `recorded_at_write` (format or pipeline metadata, exact,
with the version or snapshot stamp where one exists) — and the envelope
names it. `recorded_at_write` needs no data identity, is the cheapest
source of exact numbers where it exists, and a comparator over its
version stamp is exact change detection for free. The engine and format
declaration says which basis each source can offer.

**Two consequences.** Egeria's native Postgres survey reads `pg_stats` too
(`PROBES-2026-09-21.md`: *Most Common Values* comes from
`pg_stats.most_common_vals`), so native annotations are estimates with the
same staleness and the read-back labels them so — a native result is not
authoritative because it is native. And refreshing statistics is not a
survey identity's job (`ANALYZE` needs ownership or `MAINTAIN`), so stale
statistics raise an RFA to the DBA, never a write.

Perspectives: **Data Expert, Steward, Privacy, Security, Admin, Data Owner,
Architecture, Governance** carry most rows. No new Perspective is needed.

Legend for the "Rule" column: **A** = native service computes it;
**A/B** = native if reachable and efficient, RE mirror otherwise; **C** = RE only.

### 5.2 Scouting — cheap, whole-database, catalog reads only

| Question | Answering analysis (proposed id) | Mechanism | Rule | Perspectives |
|---|---|---|---|---|
| What is this database, and what is it for? | `N/A — direct field` (`pg_description` on the database, Enrichment fallback) | Direct Field + Human | — | all |
| How big is it — schemas, tables, views, columns, rows, bytes? | `schema_inventory` (exists) + `row_count_snapshot` (exists) | DB Catalog | A/B | Data Expert, Admin, Financial |
| Which schemas carry the data, and which are system, empty or staging? | `schema_inventory` | DB Catalog | A/B | Data Expert |
| Is it alive — inserts/updates/deletes since stats reset, last write, last vacuum/analyse, is anything reading it? | `db_activity_signals` (new; `pg_stat_user_tables`, `pg_stat_database`) | DB Catalog | C | Admin, Steward |
| Is it documented — what fraction of tables and columns have comments? | `db_documentation_coverage` (new) | DB Catalog | C | Steward, Consumer |
| What engine and version, which extensions, is the version supported? | `db_server_profile` (new; server-level) | DB Catalog | A/B | Admin, Security |
| Is it primary or replica, clustered, and is WAL archiving / backup configured? | `db_resilience` (new; §5.5) | DB Catalog | C | Admin, Data Owner |
| Where is it physically (host, region, cloud metadata) — sovereignty first pass? | cross-type sovereignty question; connection endpoint | Connection Metadata | C | Governance, Privacy |

### 5.3 Discovery — reason over what Scouting stored, zero new fetch

| Question | Answering analysis | Mechanism | Rule | Perspectives |
|---|---|---|---|---|
| What kind of database is this — transactional, analytical, reference data, staging, a copy? | `db_classification` (new; table shapes, FK density, naming, read/write ratios from tuple stats, query mix if `pg_stat_statements`) | Analysis | C | Architecture, Data Expert |
| Is there a data model — do the tables relate (FK graph), or is it a bag of tables? | `db_relationship_graph` (new) | Analysis | C | Architecture, Data Expert |
| **What is the grain of each table — one row per what?** | `grain_determination` (new; PK composition, column names, `n_distinct` ≈ row count, date columns → `DataGrainAnnotation` with a `grainStatement` and confidence) | Analysis | C | Data Expert, Steward |
| Does this look like a copy or subset of a database we already know? | `db_fingerprint` (new; `FingerprintAnnotation` over schema signature) | Analysis + Registry | C | Architecture, Financial |
| Are there views, materialised views, functions or triggers that encode business logic? | `sql_analysis` (exists) | Code Analysis | C | Data Expert, App/AI Builder |
| Which tables would a consumer start with? | `db_hub_tables` (new; FK in-degree, rows, comments, read activity) | Analysis | C | Consumer, App/AI Builder |
| Which columns hold semi-structured data (JSON/JSONB/XML/arrays/hstore), and how much of the data is in them? | `schema_inventory` [check: semi_structured_columns] | Analysis | C | Data Expert, App/AI Builder |
| How much has changed since the last survey? | change detector (§9) | Change Detection | C | all |

### 5.4 Analysis — structural and quantitative, may read data

| Question | Answering analysis | Mechanism | Rule | Perspectives |
|---|---|---|---|---|
| What is the full schema — tables, columns, types, keys, constraints, indexes? | `schema_inventory` | DB Catalog | A/B | Data Expert |
| What do the columns actually contain — nulls, distinct counts, most-common values, ranges, widths? | `column_profile` (new; `pg_stats` first, sampling fallback; matches `RelationalColumnMetric` + native frequent-values) | DB Catalog / Profiling | A/B | Data Expert, Steward |
| **Which columns conform to a known Data Class (PII and otherwise), with what confidence — and which look like a class we do not have yet?** | `data_class_match` (new; generalises `pii_scan`: name + type + value-pattern sampling against every Egeria `DataClass`, `DataClassAnnotation` per column; unmatched-but-patterned columns → RFA "propose Data Class" with the observed pattern) | Profiling + Egeria | C | Privacy, Steward, Security |
| **Which low-cardinality columns conform to a known reference-data set, and which should become one?** | `reference_data_match` (new; `n_distinct` ≤ threshold → distinct values vs every `ValidValueSet`; match → `ValidValuesAssignment` advice; partial → RFA listing the unmatched values; no set → RFA "propose ValidValueSet" carrying the values) | Profiling + Egeria | C | Steward, Data Expert, Governance |
| **What is inside the JSON/JSONB/XML columns — keys, nesting, types, consistency across rows?** | `nested_column_profile` (new; JSONB: `jsonb_object_keys`, `jsonb_typeof` over a sample, key frequency, depth; XML: root element and xpath key sampling; produces a `SchemaAnalysisAnnotation` with an inferred nested schema, reused by §6's `nested_schema_profile`) | Profiling | C | Data Expert, App/AI Builder |
| Which tables have no primary key, no FK, no comment, unused indexes, or names that break the convention? | `schema_conventions` (new; check-granular like `repo_conventions`) | Analysis | C | Steward, Data Expert |
| How do views and functions depend on tables, and what column lineage do they imply? | `sql_analysis` | Code Analysis | C | Data Expert, Architecture |
| What does it depend on outside itself (FDWs, dblink, extensions, publications)? | `db_external_dependencies` (new) | DB Catalog | C | Architecture, Admin |
| Who can read and write what — roles, grants, superusers, PUBLIC grants, sensitive columns readable broadly? | `privilege_audit` (exists) × `data_class_match` | DB Catalog | C | Security, Admin, Privacy |
| **How is it changing — rows inserted/updated/deleted per table per period, size drift, schema churn?** | `db_change_rates` (new; deltas of `n_tup_*` and sizes between snapshots; per-table series → Understanding charts) | Trend | C | Steward, Admin, Data Owner |
| What is the data's scope in time (earliest/latest dates per date column)? | `column_profile` [date ranges] → proposed `DataScope` | Profiling | C | Governance, Data Expert |
| Which glossary terms do these columns probably mean? | `semantic_suggestions` (new; `SemanticAnnotation` from names, data classes, reference sets) | Egeria + Analysis | C | Steward, Consumer |

### 5.5 Operational resilience (Scouting/Analysis rows above, expanded)

**Decision (project owner, 2026-09-20):** yes, we care whether a database is
clustered, supports HA, and is regularly backed up — for the Admin and Data
Owner perspectives, and for the data-product readiness composite.

What is machine-observable from inside the database, and what is not:

| Question | Observable | Source |
|---|---|---|
| Primary or standby? | yes | `pg_is_in_recovery()` |
| Are there replicas, and how far behind? | yes, from the primary | `pg_stat_replication` (`state`, `replay_lag`) |
| Is WAL archiving on, and is it succeeding? | yes | `pg_settings.archive_mode`, `pg_stat_archiver.failed_count`, `last_archived_time` |
| Is a backup tool present? | partly | `pg_extension` / roles for pgBackRest, barman, wal-g; otherwise Enrichment |
| Is it clustered (Patroni, Citus, Cloud HA)? | partly | Citus catalogs; Patroni via REST if reachable; managed services via Enrichment |
| When was the last successful backup, and was a restore ever tested? | **no** | Enrichment — and these are the two questions a Data Owner actually asks |

So `db_resilience` is a `MIXED` analysis: catalog facts plus an Enrichment
section ("Backup and recovery") with last-backup date, restore-test date and
RPO/RTO. The envelope must say which half answered.

### 5.6 Assessment — scored against criteria

| Question | Answering analysis | Mechanism | Rule | Perspectives |
|---|---|---|---|---|
| How complete and consistent is the documentation? | `db_documentation_coverage` + `schema_conventions` [checks] | Analysis | C | Steward |
| What is the sensitive-data exposure — sensitive columns × who can read them? | `data_class_match` × `privilege_audit` | Analysis | C | Privacy, Security, Governance |
| How well-modelled is it (keys, constraints, FK coverage, orphan tables, grain clarity)? | `schema_conventions` + `grain_determination` [checks] | Analysis | C | Architecture, Data Expert |
| How well-governed is its reference data (share of low-cardinality columns bound to a set)? | `reference_data_match` [checks] | Analysis | C | Steward, Governance |
| Is it maintained and healthy (vacuum recency, dead-tuple ratio, unused indexes)? | `db_activity_signals` | DB Catalog | C | Admin |
| Is it resilient (replica, archiving, backup evidence, tested restore)? | `db_resilience` | Mixed | C | Admin, Data Owner |
| Is it ready to be a data product, and is there demand? | cross-type composites (§4) | Composite | C | Data Owner, Governance, Consumer |
| Does it meet a named standard? | Certify path — no new analysis | Certify | — | Governance |

### 5.7 Steps and structured storage

Steps at the cost boundary:

| Step | Reads | Cost (fetch / compute) | Produces |
|---|---|---|---|
| `postgres_schema_and_stats` (exists; extended to read `pg_stats`, tuple counters, indexes) | catalog | api / low | SchemaAnalysis, ResourceMeasure (all four `Relational*Metric` sets), ResourceProfile (frequent values) |
| `postgres_operations` (new; folds `privilege_audit`, `db_activity_signals`, `db_resilience`, `db_external_dependencies`) | `pg_stat_*`, `pg_roles`, `pg_settings` | api / low | ResourceMeasure, ResourcePhysicalStatus, RFA on PUBLIC grants |
| `postgres_column_profile` (new; sampling fallback + value-based matching) | sampled rows, bounded by rows and bytes per column | api_heavy / medium | ResourceMeasure per column, DataClass, ValidValues advice, Semantic, RFAs proposing classes/sets |
| `postgres_nested_columns` (new) | sampled JSON/XML values | api_heavy / medium | SchemaAnalysis (nested) |
| `sql_analysis` (exists) | DDL | api / low | RelationshipAdvice, Quality, RFA, DataClass |
| `db_derived` (new; zero-fetch) | stored rows only | none / low | Classification (db kind), DataGrain, Fingerprint, conventions checks, change rates, proposed DataScope |

### 5.8 Sampling strategy is configuration, and the result records which one ran

**Decision (project owner, 2026-09-20):** if we sample, the strategy needs
configuring. Proposal:

| Setting | Values | Default | Why it matters |
|---|---|---|---|
| `strategy` | `catalog_stats_only` · `head` · `random` · `systematic` (every n-th) · `stratified` (by a column, e.g. partition key or date) · `full` | `catalog_stats_only` for measures; `random` for value matching | `head` is fast and biased (oldest rows); `random` on Postgres is `TABLESAMPLE SYSTEM`/`BERNOULLI`, cheap and unbiased; `stratified` is what a date-range or reference-set question needs; `full` is a deliberate choice, never a default |
| `max_rows` / `max_bytes` per table, `max_values` per column | integers | 10 000 rows, 64 MB, 1 000 values | the bound that keeps the cheap tier cheap |
| `seed` | integer | fixed per resource | reproducible samples, so two runs differ because the data did |
| `time_budget` | seconds per step | 60 | observed, not trusted (`step_cost_observer.py`) |
| scope | global → per resource type → per resource → per run | | a production database gets a stricter default than a sample one, which is an Enrichment fact (§5.5) |

Two rules that follow. The **envelope carries the strategy**: an answer reads
"from a random sample of 10 000 of 4.2 M rows (seed 7, 2026-09-20)", so a
consumer can tell a sampled null-fraction from a `pg_stats` one from a full
scan. And **matching thresholds are stated against the sample**: "conforms to
Data Class X with 0.98 of sampled values" — never an unqualified "conforms".
Files use the same settings with `max_bytes` doing most of the work, and
`stratified` meaning "n rows per file in a partitioned set".

Structured tables, keyed by `(slug, surveyed_at, source)` (rule D):
`database_schemas`, `database_tables`, `database_columns`,
`database_column_profiles`, `database_table_activity` (tuple counters per
snapshot), `database_grants`, `database_sql_objects`, `database_settings`. The
existing `survey_data` blob stays as the raw record. Without these, every
per-object question, diff, trend and change detector re-parses JSON.

---

## 6. Filesystems and files

Two resources: a **filesystem** (a directory root, `file_systems`) and a
**file** as a sub-resource of it (`sub_resources`), promoted to first-class
when registered directly. **Decision (project owner, 2026-09-20):** both.

Three cost-bounded steps: `filesystem_structure` (one `stat()` per file),
`filesystem_content` (opens data files), `document_content` (`docling`, per
format allowlist, separate because its cost is another order of magnitude).

### 6.1 Scouting

| Question | Answering analysis | Mechanism | Rule | Perspectives |
|---|---|---|---|---|
| What is in here — files, folders, bytes, depth? | `filesystem_structure` | File Walk | A/B | all |
| What kinds of files — by extension, file type, asset type, deployed implementation type? | same; Egeria reference data | File Walk + Reference Data | A/B | Data Expert, Architecture |
| How much is data, documents, code, binaries? | `file_kind_breakdown` (new) | Analysis | C | Data Expert, Consumer |
| Is it accessible — unreadable files or folders? | RFA from the walk (exists) | File Walk | A/B | Admin |
| Is it alive — modification-time distribution? | `filesystem_structure` histogram | File Walk | C | Steward, Admin |
| Are there files Egeria's reference data cannot classify? | RFA (exists) | File Walk | A | Steward |
| Where is it physically (host, mount, cloud bucket region)? | cross-type sovereignty; mount metadata | — | C | Governance |

### 6.2 Discovery

| Question | Answering analysis | Mechanism | Rule | Perspectives |
|---|---|---|---|---|
| What kind of place — dataset drop, document library, archive, working directory, backup? | `filesystem_classification` (new) | Analysis | C | Architecture, Consumer |
| Is there a structure or convention (dated folders, `year=2024/` partitions, naming)? | `path_conventions` (new) | Analysis | C | Data Expert |
| Does anything describe the contents — README, manifest, data dictionary, checksums? | `descriptor_detection` (new) | Analysis | C | Consumer, Steward |
| Obvious duplicates or versions (`_v2`, `_final`)? | `file_fingerprint` (new) | Content (bounded) | C | Steward, Financial |
| Which files would a consumer start with? | `hub_files` (new) | Analysis | C | Consumer |
| How much has changed since the last survey? | change detector (§9) | Change Detection | C | all |

### 6.3 Analysis

| Question | Answering analysis | Mechanism | Rule | Perspectives |
|---|---|---|---|---|
| Schema of each tabular data file (columns, types, rows)? | `data_file_profiling` (exists for repos; FS variant) | Content | A/B (native CSV only) | Data Expert |
| Structure of JSON / JSONL / XML / YAML files? | `nested_schema_profile` (new; shares the inference core with §5's `nested_column_profile`) | Content | C | Data Expert, App/AI Builder |
| Which columns in data files conform to Data Classes, or suggest new ones? | `data_class_match` (shared with DB) | Content | C | Privacy, Steward |
| Which columns conform to reference-data sets, or suggest new ones? | `reference_data_match` (shared) | Content | C | Steward |
| What is the grain of each data file? | `grain_determination` (shared) | Analysis | C | Data Expert |
| Secrets or credentials in files? | `secret_scan` (exists; reuse) | Content | C | Security |
| What do the documents say — titles, sections, languages, entities? | `document_content` (new; `docling` → RAG corpus) | Document Parsing | C | Consumer, App/AI Builder |
| Are partitioned sets internally consistent (same schema across shards)? | `schema_consistency` (new) | Analysis | C | Data Expert, Steward |
| Files too large, corrupt or unreadable to profile? | profiling-failure RFA (exists) | Content | C | Admin |
| How is it changing — bytes and files added/removed/modified per period? | `fs_change_rates` (new; snapshot deltas) | Trend | C | Admin, Financial |
| Scope in time (date ranges in data, mtime range)? | profiling → proposed `DataScope` | Analysis | C | Governance |

### 6.4 Assessment

| Question | Answering analysis | Perspectives |
|---|---|---|
| How well-described is it? | `descriptor_detection` + profiling [checks] | Steward, Consumer |
| Sensitive-data exposure (classes found × world-readable flags)? | `data_class_match` + `secret_scan` + accessibility | Privacy, Security |
| How much is redundant? | `file_fingerprint` | Financial, Steward |
| How stale is it? | mtime histogram | Data Owner, Financial |
| Ready to be a data product, and is there demand? | cross-type composites | Data Owner, Governance |

### 6.5 File as a resource

The rows collapse to: what is it, how big, when modified, what schema or
structure, what classes and sets, what grain, what content, is it a duplicate.
Same analyses with `target_shape: single_container`. Nothing new to author.

---

## 7. Open-source data (datasets on portals)

Resource type `dataset`: a **published dataset descriptor** on a portal —
Hugging Face Datasets first (**decision, project owner, 2026-09-20**), then
CKAN/DCAT portals (data.gov, EU Open Data Portal), Zenodo, Kaggle, schema.org
`Dataset` and Croissant descriptors.

**Scouting and Discovery run on the descriptor with no download; Analysis
downloads a distribution into a managed folder and registers it as a
filesystem/file resource; Assessment reasons over both.** HF datasets and
models are git repositories with LFS, so `git_clone_root` and `SourceCache`
apply; cards are Markdown with YAML front matter.

### 7.1 Scouting (descriptor only)

| Question | Answering analysis | Perspectives |
|---|---|---|
| What is this dataset about, and who publishes it? | `dataset_descriptor` (new; DCAT / schema.org / HF card → normalised record) | all |
| Who owns the data (publisher vs. rights holder vs. contact)? | `dataset_descriptor` → Egeria `Ownership` | Data Owner, Governance |
| What licence, and is it a standard open licence? | `dataset_descriptor` + `license_classification` (reuse) | Consumer, Governance, App/AI Builder |
| Formats, distributions, size? | `dataset_descriptor` | Data Expert, Financial |
| Last updated, and how often? | `dataset_descriptor` (`modified`, `accrualPeriodicity`) | Steward, Consumer |
| How widely used (downloads, likes, citations, DOI)? | `dataset_attention` (new) | Community, Consumer |
| Where does the data come from geographically and in time (DCAT `spatial`, `temporal`)? | `dataset_descriptor` → proposed `DataScope` | Governance, Privacy |

### 7.2 Discovery (no download)

| Question | Answering analysis | Perspectives |
|---|---|---|
| Schema or data dictionary published (Croissant, `dataset_info`, DCAT `schema`)? | `dataset_descriptor` [check] | Data Expert |
| Derivative of a dataset we know? | `dataset_provenance` (new; `isBasedOn`, `source_datasets`, card prose) | Governance, Data Expert |
| Restrictions beyond the licence — gated, terms, geography, purpose? | `dataset_descriptor` [checks] | Governance, Privacy |
| Does the card claim personal data, and what mitigation? | `dataset_card_lens` (new; RAG over card sections) | Privacy |
| Worth downloading? | disposition (cross-type) | Financial |

### 7.3 Analysis (downloads a distribution)

Registers the download as a `filesystem`/`file` and runs §6.3, plus:

| Question | Answering analysis |
|---|---|
| Does the data match its descriptor — schema, rows, splits, formats, spatial/temporal scope? | `descriptor_conformance` (new) |

### 7.4 Assessment

| Question | Answering analysis | Perspectives |
|---|---|---|
| How FAIR is it (a checklist, not a score)? | `fair_checks` (new; check-granular) | Governance, Data Expert |
| Licence compatible with intended use (commercial, redistribution, training)? | `license_classification` [checks] + Enrichment | Governance, App/AI Builder |
| PII risk given card claims *and* scan findings? | `dataset_card_lens` × `data_class_match` | Privacy |
| Maintained (cadence vs. declared periodicity)? | `dataset_descriptor` + `dataset_attention` | Steward |
| Suitable as training or evaluation data for our use case? | card lens + nested profile + Enrichment | App/AI Builder |
| Sovereignty: can we hold and process it where we operate? | `DataScope` × Enrichment | Governance, Privacy |

Egeria: `DataSet` / `DataFile` under a `DataFolder`, `License` + `LicenseType`,
`Ownership`, `DataScope`, `ExternalReference` for the portal record and DOI,
`DigitalProduct` on adoption.

---

## 8. AI models

Resource type `model`, Hugging Face model repositories first, later
Ollama-local and vendor-hosted models where only the card is reachable.

Most of this is the repo path with a different classifier: `repo_classification`
gains `model`; `license_classification` splits *weights* from *code* (the
existing `GAP` row "For AI/ML assets, what licensing or usage constraints
apply…" is this); `data_file_profiling` learns weight files (count, format,
parameter estimate from safetensors headers without downloading tensors). New
is the card.

### 8.1 Scouting

| Question | Answering analysis | Perspectives |
|---|---|---|
| What is this model — task, architecture, base model, parameters? | `model_card` (new; front matter + `config.json` + safetensors header) | all |
| Who owns it, and who publishes it? Is it gated? | `model_card` → `Ownership` | Governance |
| What licence covers the weights, and what covers the code? | `model_card` + `license_classification` [checks: weights_license, code_license, custom_terms, RAIL clauses] | Governance, App/AI Builder |
| How widely used (downloads, likes, derivatives, spaces)? | `model_attention` (new) | Community, Consumer |
| How big to host (bytes, formats, quantisations)? | `model_card` | Financial, Admin |

### 8.2 Discovery

| Question | Answering analysis | Perspectives |
|---|---|---|
| Fine-tune, merge, quantisation or base — of what? | `model_lineage` (new; `base_model`, card prose, HF model tree) | Architecture, Governance |
| Trained on what, and are those datasets known to us (§7)? | `model_lineage` + registry | Governance, Privacy |
| Is the card complete (intended use, limitations, training data, evaluation, bias, environmental impact)? | `model_card_completeness` (new; check-granular) | Governance, App/AI Builder |
| What evaluation results are claimed, on which benchmarks? | `model_card` (`model-index`) | App/AI Builder, Data Expert |

### 8.3 Analysis / Assessment

| Question | Answering analysis | Perspectives |
|---|---|---|
| Runnable code shipped, and does it need `trust_remote_code`? | `repo_conventions` + `model_card` [check] | Security |
| Weight format safe (safetensors vs. pickle)? | `model_artifact_scan` (new) | Security |
| Evaluations reproducible (scripts or references present)? | `model_card_completeness` [check] | Data Expert |
| Can we run it (hardware, formats we serve)? | Enrichment + `model_card` | Admin, App/AI Builder |
| Regulatory obligations (EU AI Act tier, licence use restrictions)? | `model_card` [checks] + Enrichment | Governance, Privacy |
| Maintained (last commit, discussions, responsiveness)? | `repository_health` (exists) | Community, Steward |

Egeria: `DeployedAnalyticsModel` (model 0265, Analytics Assets — extendable),
`ExternalModelSource`, `License`/`LicenseType` twice, `Ownership`,
`DigitalProduct` on adoption. Card sections as `ResourceProfileAnnotation`s
until a card type exists (§12).

---

## 9. Change and notification

**Decision (project owner, 2026-09-20):** changes result in notifications; how
this integrates with Egeria's notification mechanisms is undesigned.

### 9.1 What "changed" means, per resource type

Change detection compares two snapshots of the structured tables (rule D), so
it works whether Egeria or RE produced them. Proposed comparators, each a
named check so a subscription can be specific:

| Type | Comparator | Signal |
|---|---|---|
| database | `schema_diff` | tables/columns/constraints added, dropped, retyped |
| database | `row_drift` | live-tuple delta beyond a per-table threshold; `n_tup_del` spike |
| database | `grant_change` | new grant, especially to PUBLIC or on a sensitive column |
| database | `class_change` | a column newly matching a Data Class |
| database / file | `reference_set_change` | a bound reference set newly violated, or new distinct values in a bound column |
| any | `scope_change` | a measured date range or location falls outside the declared `DataScope` |
| database | `resilience_change` | archiving off, replica lost, role change |
| filesystem | `inventory_diff` | files added/removed/modified; new unclassified types |
| filesystem | `size_drift`, `class_change`, `access_change` (world-readable) | |
| dataset / model | `descriptor_diff` | licence, gating, base model, card sections, new version |
| any | `survey_absence` | a scheduled run did not happen or failed — absence is a change too |

`notification_detector.py` exists for repos with a generic "compare last two
results" shape; these are its per-type instances.

### 9.2 Delivery: local now, Egeria-integrated in three steps

Today: RE's scheduler runs the analysis, the detector compares, and delivery is
an RFA in RE's drawer. That stays as the baseline.

The Egeria mechanism is `NotificationType` (a governance definition with
interval and count properties) → `MonitoredResource` (what is watched) →
`NotificationSubscriber` (who is told). pyegeria has dedicated
link/detach methods for the two relationships; creating the type has no
dedicated method and goes through the untested generic
`create_governance_definition` — which is the gap
`docs/automate-notification-manager-pyegeria-spec.md` proposes an API for.

Proposed integration order:

1. **Mirror subscriptions.** When an RE subscription is created, also create
   (once the pyegeria method exists) a `NotificationType` per comparator and
   link the asset as `MonitoredResource` and the user's `UserIdentity` /
   `Team` as `NotificationSubscriber`. `notification_subscriptions.egeria_notification_type_guid`
   already waits for this.
2. **Deliver through Egeria too.** On a detected change, RE raises the RFA as
   now *and* records a `ToDo` against the subscriber via Egeria (the RFA →
   ToDo integration in `docs/egeria-integration.md §11`), so consumers who
   never open RE still see it.
3. **Let Egeria detect what Egeria surveyed.** For native surveys, Egeria's
   own watchdog engine services could raise the notification; RE would then
   *consume* it (the read-back path) rather than compare. This is the piece
   that needs the most design and the one this document does not attempt.

### 9.3 Granularity: notifications go to actors, and the grain is configurable

**Decision (project owner, 2026-09-20):** notifications go to *actors* — a
person, a team, or an automated process — and granularity should be
configurable. Worked examples to test the model against:

| Subscriber (actor) | Watches | Comparators | Grain | Delivery |
|---|---|---|---|---|
| A steward (person) | one database | `schema_diff`, `class_change` | per resource, two comparators, digest daily | RE RFA + Egeria ToDo |
| The Privacy team (`Team`) | every database in the `production` group | `class_change`, `grant_change` on sensitive columns | per group, immediate | Egeria ToDo to the team; RE RFA to whoever opens it |
| A Prefect flow (automated process) | one filesystem | `inventory_diff` | per resource, every run, no digest | engine action / webhook — the flow re-profiles new files |
| A data owner (person) | one data product's sources | `resilience_change`, `survey_absence` | per product (a set of resources), weekly digest | Egeria ToDo |
| RE itself (automated process) | every scheduled analysis | `survey_absence` | global | RE activity log + RFA to the Admin perspective's owner |

What the examples show: the subscription's key is *(actor, scope, comparator
set, cadence)*, where scope is a resource, a group, or a product; comparator
sets can be presets ("privacy watch", "schema watch"); cadence is immediate or
digest. That maps onto Egeria as one `NotificationType` per *(scope,
comparator set)* with the actor as `NotificationSubscriber` — many precise
types rather than one per resource — and `notification_interval` carrying the
cadence. Automated-process subscribers are the reason delivery cannot be only
a drawer: an actor with no screen needs an engine action or a webhook.

---

## 10. Purpose in Egeria — a spec

**Decision (project owner, 2026-09-20):** spec it; a set of Terms or a Valid
Value set are acceptable starting points.

**Decision (project owner, 2026-09-25):** Purposes are being modelled in
Egeria as a **valid values list**, and will be used, in part, **to select
the right connection for the right purpose**. Two consequences: the
recommendation below (a `ValidValueSet` with `ReferenceValueAssignment`
from Question terms) is confirmed rather than proposed, and Purpose gains a
second consumer beyond question ranking — the connection choice in
`security-model.md` §5, where an investigation's purpose (Explore, Assess,
Certify …) selects among the labelled connections on an asset. `Level`
(§18.3) rides the same mechanism as a second small set.

Recommendation: **a `ValidValueSet` named `Resource Explorer Purposes`, one
`ValidValueDefinition` per purpose, and `ReferenceValueAssignment` from each
Question term to its purposes.**

Why a valid-value set rather than glossary terms:

- `investigation-framing-design.md §2` already positions Purpose as a
  *controlled, org-extensible* vocabulary and names `ProjectCharter.purposes`
  as the place an investigation records it. A valid-value set is exactly that
  kind of vocabulary in Egeria, and the same set can back both the question
  tagging and the charter field.
- `ReferenceValueAssignment` is the relationship Egeria defines for "this
  Referenceable is tagged with this valid value" — it carries `confidence`,
  `steward` and `notes`, which a Term-to-Term link does not.
- Glossary terms would collide with the *User Questions* glossary's semantics
  (terms there *are* questions) and with the Perspective pattern, which already
  needed explicit qualified names to avoid display-name collisions.

Shape:

```
ValidValueSet  "Resource Explorer Purposes"   qualifiedName: ValidValueSet::RE::Purposes
  ValidValueDefinition  Explore    qualifiedName: ValidValue::RE::Purpose::Explore
  ...                   Select, Assess, Maintain, Share, Learn, Certify, Remediate, Attest, Deploy
Question term  --ReferenceValueAssignment-->  ValidValueDefinition   (one per purpose in the CSV cell)
ProjectCharter.purposes  = ["Select", "Certify"]                     (strings from the same set)
```

Pipeline change: `csv_to_dr_egeria_questions.py` stops excluding the
`Purposes` column and emits the assignments; `foundations.md` gains the set,
created once like the Perspectives. Dr.Egeria needs a "Link Reference Value"
command or the generic relationship command; check which exists before
committing to the generator change (same class of check as §14's
reconciler item).

What it buys: "which questions serve Certify?" becomes an Egeria query;
Egeria Advisor can rank surveys by an investigation's charter without RE's
YAML; and a purpose added by an organisation needs no rebuild.

---

## 11. Representations in the UI — requirements for the designer

**Decision (project owner, 2026-09-20):** DB/FS does not go into `/next` until
repositories are complete; provide the designer with advice and requirements.

The principle from the repo work holds: the person's surface is the one that
counts, and an answer that exists only as a metric row is not an answer. Each
question family below names the representation that actually answers it,
graphical where the question is about *shape* or *change*, textual where it is
about *a fact*. The Kroki service already renders Mermaid server-side and is
used for the database ER view today.

| Question family | Representation | Notes |
|---|---|---|
| What is here, how big (Scouting, all types) | **Treemap** by schema→table or folder→file, area = bytes or rows, colour = kind or age | one picture answers size, structure and staleness; the classic FS report is a list |
| Data model / relationships | **ER diagram** (Kroki, exists) with FK graph; hub tables emphasised; orphan tables greyed | add "grain" as a label under each table name |
| Column contents | **Per-column profile card**: null bar, cardinality, top values as a mini bar chart, range; Data Class and reference-set badges with confidence | the same card for a DB column and a data-file column |
| Sensitive data exposure | **Heatmap** columns × roles: which sensitive columns are readable by which roles | the Assessment composite in one view |
| Change over time | **Small multiples** per table of tuple insert/update/delete rates; **schema-diff timeline** with add/drop markers; FS bytes-and-files series | Understanding tier; this is where rule D pays off |
| Lineage | **Graph** of views → tables → columns (SQLGlot output), with the option to expand into Egeria's lineage | already partly in the classic "Views & Lineage" tab |
| Sovereignty and scope | **Map** with the `DataScope` bounding box and a timeline strip for collection / validity / coverage; textual jurisdiction and controller | one panel per resource; the same for datasets from DCAT `spatial`/`temporal` |
| Resilience | **Status strip**: primary/replica, archiving, backup evidence, restore test — each with the "answered by catalog / answered by a person / not established" state visible | the honesty affordance matters more than the graphic |
| Conformance proposals | **Review queue**: proposed Data Classes and ValidValueSets with the observed values and a one-click "create in Egeria" / "dismiss" | this is a Curate surface, and the RFA drawer is the wrong shape for it |
| Data-product readiness and demand | **Two-sided checklist**: supply checks left, demand signals right, with the composite verdict and its state | verdict must say which checks were not established |
| Dataset / model cards | **Rendered card with check badges** inline per section (present / missing / thin) | the card is the resource's own description; do not re-summarise it |
| Reachability and cost | **A sentence** on the survey launcher: "Egeria can reach this (checked 2 min ago); local run est. 4 s, native est. 40 s" with the choice | rule B made legible |

Cross-cutting requirements:

- Every visual carries the envelope state. Unmeasured is drawn differently
  from measured-empty, not omitted.
- Every visual is per snapshot, with a snapshot picker, because rule D keeps
  history and the questions are often "vs. last time".
- Sub-resource navigation (server → database → schema → table → column;
  filesystem → folder → file) is the same control for both families, since
  the project owner has made them the same rule.
- The question checklist stays the entry point; each visual is reached from a
  question, not from a menu, so a stage's page is its questions with their
  answers rendered.

---

## 12. Egeria extensions worth asking for

**Decision (project owner, 2026-09-20):** discuss; extensions are generally
feasible.

**Validated the same day against the Egeria and pyegeria source — see
`docs/egeria-support-for-multi-resource.md`, which supersedes this list.**
Headline: items 1, 4 and the declared half of `DataScope` already exist end
to end (types, endpoints, pyegeria methods, Dr.Egeria commands); item 4 has a
probable server bug that blocks the Purpose spec; item 2 is the one real type
ask; items 3, 5 and 6 are partial. The list below is kept as the original
framing.

Ordered by how soon this plan needs them, with what each would carry:

1. **A dedicated pyegeria method to create a `NotificationType`** — already
   specified in `docs/automate-notification-manager-pyegeria-spec.md`; blocks
   §9 step 1.
2. **A model-card carrier on `DeployedAnalyticsModel`** (or a `ModelCard`
   subtype of `Referenceable` linked to it): intended use, out-of-scope uses,
   limitations, training-data references (links to `DataSet`s), evaluation
   results (benchmark, metric, value, date), bias and safety notes, compute
   and environmental impact, base-model lineage (a `DerivedFrom`-style
   relationship between models), weights vs. code licence (two `License`
   links with a role qualifier). Until then §8 publishes profile annotations.
3. **Survey annotations that *propose* governance elements**: today a
   `DataClassAnnotation` asserts a match with an existing class. A
   `ProposedDataClass` / `ProposedValidValueSet` shape — or a convention on
   `RequestForActionAnnotation` with the proposal in `jsonProperties` that a
   stewardship action can turn into the real element — would make §5.4's
   proposals first-class rather than free text. Same for a proposed
   `DataScope` and proposed `DataGrain`.
4. **`ReferenceValueAssignment` authoring through Dr.Egeria** for §10, if it
   is not already there.
5. **A "resource reachability" record**: where a survey engine last succeeded
   or failed to open a connection, so rule B's probe is not repeated by every
   client. Could be a classification on the `Connection` or an annotation
   type; low priority.
6. **Survey services for DuckDB files reached through a folder survey**, so a
   `.duckdb` file found by `survey-folder` can be surveyed as a database in
   the same process. Egeria has both halves; the link between them is what
   is missing.

---

## 13. Sequencing

Each phase has a done-test that is a user-visible surface.

### Phase 0 — type-agnostic plumbing (no new questions yet)

1. `question_catalog_reader._load()` keyed by resource type; explicit "not
   authored for this type" signal distinct from an empty filter result.
2. Generator reads every `*_analyses` section for known ids; emits one YAML key
   per resource type; `NON_PERSPECTIVE_COLUMNS` gains `Resource Types`; the
   `Funnel Stage` vocabulary drops `Automate`; the one Automate row moves to
   Discovery with `*`.
3. `ResourceTypeAdapter` gains `analysis_results_map` and `state_sources`;
   `FactLayer` dispatches through the adapter.
4. One `RESOURCE_TYPES` constant, consumed by the five places that re-declare
   the tuple; `dataset` and `model` added now so nothing else hardcodes three.
5. Structured DB/FS detail tables (§5.7) keyed with `source`, plus the
   Egeria read-back materialiser (rule D) and a back-fill from existing
   `survey_data` blobs.
6. The engine capability declaration on `DatabaseConnection`.

**Done-test:** the cross-type questions (§4) render on the classic Questions
tab for `coco_ods` with honest envelopes, and the repo CSV round-trips
unchanged through the new generator.

### Phase 1 — databases

1. Re-catalogue `coco_ods` with a reachable connection; run the native
   `survey-postgres-database`; dump every annotation type and metric key it
   emits. Ground truth for rule A, never read from a live run.
2. Extend `postgres_schema_and_stats` to `pg_stats` and tuple counters;
   build `postgres_operations`; build `db_derived` (classification, grain,
   fingerprint, conventions, change rates, proposed DataScope).
3. Author §4 and §5 questions into the CSV; generate the database survey
   definitions (`scouting`, `analysis`, `assessment`).
4. Build `postgres_column_profile` with `data_class_match` and
   `reference_data_match`, and `postgres_nested_columns`.
5. Reachability probe via `finalAnalysisStep=CHECK_ASSET`, the cost record,
   and the efficiency policy in rule B.
6. Change comparators for databases (§9.1) on the local delivery path.

**Done-test:** for `coco_ods`, the Questions tab answers "which columns conform
to a Data Class?" and "how is it changing?" from stored rows; the Egeria asset
carries both a native and an RE survey report with same-typed column
annotations; a new column and a new PUBLIC grant each raise an RFA.

### Phase 2 — filesystems and files

Split the walk; add `document_content` behind an allowlist; native
`survey-folder` ground-truth dump; JSON/XML/YAML inference shared with
Phase 1's nested-column core; `file_fingerprint`; shared `data_class_match`,
`reference_data_match`, `grain_determination`; sub-resource rows per data
file; file-as-resource registration; FS change comparators.

**Done-test:** a folder with CSV, Parquet, JSON, XML and PDF: Scouting answers
in under a minute without opening a file; Analysis profiles the tabular files,
infers the nested schemas, proposes one reference set, and the PDF's sections
are answerable in chat.

### Phase 3 — open datasets (Hugging Face, then CKAN/DCAT)

`dataset_descriptor` (with `spatial`/`temporal` → DataScope), `dataset_attention`,
`dataset_card_lens`, `dataset_provenance`; download hands off to Phase 2;
`descriptor_conformance`; `fair_checks`.

### Phase 4 — AI models

`model_card`, `model_card_completeness`, `model_lineage`, `model_artifact_scan`;
`repo_classification` gains `model`; `license_classification` splits weights
from code. Egeria extension request 2 goes upstream with a concrete field list.

### Alongside

- Purpose in Egeria (§10) — small, independent, and unblocks charter-driven
  ranking; can go first.
- Notification integration step 1 (§9.2) once the pyegeria method exists.
- The repo `CodeAnalysisAnnotation` / `ContributorAnalysisAnnotation`
  migration — before Phase 1 publishes, so one convention exists.
- `PLAN-FINISH-REPOS.md`'s ten items, in worktrees, untouched by this.
- Physical → logical schema mapping: fundamentals exist; **not needed yet**
  (project owner, 2026-09-20). The bridge when it is needed is
  `semantic_suggestions` → `SemanticAssignment`; nothing here precludes it.

---

## 14. Decisions

Taken 2026-09-20 (project owner):

| # | Decision |
|---|---|
| D1 | Freeze lifted for design and implementation; `/next` stays repo-first; designer gets requirements (§11) |
| D2 | One CSV with a `Resource Types` column; repo questions may be reworded to be resource-independent |
| D3 | Files, and equally server / database / schema / table, are sub-resources by default and first-class on direct registration |
| D4 | DuckDB is the second engine; a compose script exists |
| D5 | Hugging Face is the first portal |
| D6 | Reachability rule B — generally yes; efficiency added as a second axis |
| D7 | Egeria extensions — discuss; generally feasible (§12) |
| D8 | Purpose — spec it (§10) |
| — | Automate is not a stage |
| — | Local copy of every result in RE's store, whoever ran the survey |
| — | Catalog-first extraction, engine-specific extensions included |
| — | Resilience (clustering, HA, backup) is in scope |
| — | Physical → logical mapping deferred |

Resolved on review, 2026-09-20 (project owner), with the resulting design:

- **Notification granularity** → actors (person, team, automated process),
  configurable grain; §9.3 has the model and worked examples.
- **`DataScope` authorship** → two distinct things, kept apart. *Measured*: the
  survey records what it saw (earliest date x, latest date y, bounding box)
  as an annotation on the survey report — a fact with a timestamp and a
  confidence. *Declared*: a curator states what the scope *is* (earliest x,
  latest "current"; jurisdiction; controller) as the `DataScope`
  classification on the asset. The measured value is evidence offered to the
  curator, prefilled in the Curate surface, never written as the declaration.
  A later measurement outside the declared scope is a `scope_change`
  comparator (§9.1). This is the same shape as `investigation-framing-design.md`
  §4's "certification is an award, not a verdict".
- **Sampling** → yes, the strategy is configurable, not only the bounds. See
  §5.8.
- **Reconciler tolerance** → not an issue. `survey_definition_reconciler.py`
  reconciles scope links *per process*: it diffs the live `ScopedBy` links on
  one Survey Definition against that definition's own document
  (`expected_scopes_from_document`, `diff_scopes`), matched on the Question's
  display name. A Question term scoped by a repo definition and a database
  definition is two independent reconciliations that never see each other.
  The step-link reconciler (`survey-execution.md §1.2`) is about
  `NextGovernanceActionProcessStep` edges inside one definition and is not
  touched by this design. The one real consequence of D2 remains: rewording
  a published question creates a new term, so reworded rows must be
  republished and the old term's links retired, which
  `scripts/reconcile_survey_definition_scopes.py` already handles.

Agreed on review (project owner, 2026-09-20), as proposed:

- **Default subscriptions by Perspective.** None by default.
  Perspectives are lenses, not roles, so "all Stewards" is not an actor.
  Instead, each Perspective carries a *preset comparator set* offered at
  subscribe time (Privacy → `class_change`, `grant_change`; Admin →
  `resilience_change`, `survey_absence`; Steward → `schema_diff`,
  `class_change`, `reference_set_change`; Data Owner → `survey_absence`,
  `resilience_change`, `scope_change`). A person subscribes as themselves; a
  team subscribes as a `Team`. The preset is a convenience, the subscription
  is always explicit.
**Amended 2026-09-21 — how a survey proposes an element.** The project owner
reviewed `docs/egeria-support-for-multi-resource.md` and pointed at two
mechanisms Egeria already has, both confirmed in the Java source (the seven
decision callouts are in that document as of dwolfson/trellis#189):
`contentStatus`, a *domain property* on `AuthoredReferenceableProperties`
that both `AnnotationProperties` and `DataClassProperties` inherit, with
`DRAFT` among its values (`ContentStatus.java:37`); and `AssociatedAnnotation`
(`OpenMetadataType.java:6039`), which links any element to an annotation
directly, distinct from `ReportedAnnotation`. So the proposal path in §5.4
and §6.3 changes from "an RFA carrying a spec in a string map" to: **the
survey creates the real candidate element — `DataClass`, `ValidValueSet`,
`DataGrain`, `DataScope` values — with `contentStatus: DRAFT`, and links it
by `AssociatedAnnotation` to the evidence annotation on the report.** The
curator's accept sets `contentStatus` to the confirmed value; dismiss
deletes the draft. This keeps measured-vs-declared intact — `DRAFT` content
*is* the measured state — and removes the "annotations that propose" type
ask in the support doc's §3 and §7. Note that `contentStatus` is
independent of Egeria's generic `ElementStatus` (entity persistence), which
stays `ACTIVE` throughout. The review queue below is unchanged in purpose:
it lists draft-content elements with their evidence instead of RFAs.

*Probe results, 2026-09-21 (dwolfson/trellis#194).* Probe 5 passed: all
five proposal-shaped annotation types (`DataClassAnnotation`,
`DataGrainAnnotation`, `ResourceProfileAnnotation`, `SemanticAnnotation`,
`FingerprintAnnotation`) can be created from Python and attached to a
report. Probe 4 passed on the second attempt: `contentStatus: DRAFT` in the
properties round-trips cleanly for both a `DataClass` and a
`DataClassAnnotation`. The first attempt tested the wrong field —
`initialStatus`/`ElementStatus`, which pyegeria drops client-side and the
server ignores (ISSUE-113, real but not this path's blocker; the project
owner caught the mix-up). **Status: the draft-element path is the design;
the RFA convention is retired for proposals.** One check remains before
slice 10 leans on it: whether an ordinary consumer read path surfaces
`contentStatus`, or renders a draft-content element identically to a
confirmed one — which is the visibility item below, restated.

- **Draft visibility (open, needs the project owner).** A draft-content
  element is a real, `ACTIVE`-status element, and `contentStatus` is a
  property, so **no status filter on find calls can hide it** — the earlier
  suggestion to default RE's queries to `ElementStatus` `ACTIVE` does not
  apply (`QueryOptions.limitResultsByStatus` filters entity status, not
  content status). The RFA path never had this exposure. Two fixes, not
  mutually exclusive: RE's own query layer filters on `contentStatus`
  wherever it reads governance elements for consumers, and renders the
  status as a badge wherever it shows one; and drafts are placed in a
  governance zone consumers do not see until confirmed. Recommendation: do
  both, because only the second covers consumers that are not RE. Before
  slice 10: confirm whether Egeria's own consumer surfaces (the portal's
  catalog views, Egeria Advisor) display `contentStatus` at all, since a
  draft that renders identically to a confirmed class is the failure mode.
- **Proposal acceptance surface.** Proposed Data Classes, reference sets,
  grains and scopes need a review queue (§11) before Phase 1 step 4 is worth
  building; otherwise proposals accumulate as unread RFAs.

---

## 15. Things to push back on, or measure first

- **Perspective count.** Twelve is enough; resist adding "Legal" — it is
  Governance + Privacy in practice.
- **Do not build a scoring layer** for FAIR, resilience or AI-Act tiers.
  Check-granular results plus the Certify / GovernanceMetric path cover it.
- **Bound every content-reading step before it exists**, and observe the
  declared cost. The filesystem "hang" came from exactly this class of step;
  `pg_stats`-first is how most column questions avoid it entirely.
- **Measure what the native surveys emit** before writing any RE annotation
  for the same finding. Phase 1 step 1 and Phase 2's dump are cheap and have
  never been done.
- **Measured is not declared.** A survey records what it saw — a proposed
  Data Class match, reference set, grain, or date range — as an annotation
  with evidence and confidence. A curator declares what the resource *is* —
  the classification or assignment on the asset. The Curate surface prefills
  the declaration from the measurement; nothing writes the declaration
  unattended. Where a resource has no declaration at all, the measured value
  is shown labelled as measured, which is better than nothing and honest
  about what it is.

---

## 16. Coverage, grain and quality — does the data fit what we are looking for?

**Added 2026-09-22 at the project owner's direction.** A key requirement for
any data resource — database, file, dataset — is that the data and its values
fall within the *scope* and *grain* being sought. Not "this is sales
transaction data" but "sales transactions **per day**, for **these regions**,
covering **last year**", and then the flavours of quality on top. §4 and §5
had the pieces scattered — a sovereignty row, one "period covered" row, an
entity-grain analysis, seven quality signals with no frame — and nothing
that states what is *sought*, so nothing could answer *fit*. This section
adds the requirement side, organises the signals by funnel stage on cost,
and names the analyses.

### 16.1 The model: sought versus held, and Egeria already has both halves

Checked in the Java property classes on 2026-09-22:

| Side | Egeria element | Carries | Who sets it |
|---|---|---|---|
| **Sought** | `DataLens` — a governance definition, "the scope of data for a particular type of processing" | bounding box (`min/maxLongitude`, `min/maxLatitude`, heights), `dataCollectionStart/End`, `dataValidityStart/End`, `dataCoverageStart/End`, `scopeElements` map | the investigation, at framing |
| **Held** | `DataScope` classification on the asset | **the identical field list** | a curator, declaring from what was measured (§14) |
| Grain, both sides | `DataGrain` — `granularityBasis`, `grainStatement`, `interval` | entity grain ("one row per order line") *and* time grain (`interval`, e.g. one day) | sought: on the requirement; held: proposed by a survey, declared by a curator |
| Quality | `QualityAnnotation` — `qualityDimension`, `qualityScore`, `qualityDescription` | one per dimension per resource or field | the survey; unused by RE today |

`DataLens` and `DataScope` having the same fields is the design: **fit is a
field-by-field comparison between a lens and a scope**, with grain
compatibility and quality thresholds alongside. Subject ("sales
transactions") is the one axis neither carries as a field; it goes in
`scopeElements` as glossary-term qualified names and data-class names, the
same vocabulary `semantic_suggestions` and `data_class_match` already
produce, so the subject test is a set intersection over things RE already
computes.

**The requirement is a data requirement on the investigation** (design
`investigation-framing-design.md`): subject terms, entity grain, time
interval, regions or bounding box, time window, and quality thresholds by
dimension. One `DataLens` per investigation, linked to its project, editable
in the framing step. The three questions "what is this about", "at what
grain", "covering what" are answered *descriptively* whether or not a
requirement exists; the fourth, "does it fit", renders **"no requirement
declared"** when there is none — absence as an answer, never a vacuous pass —
or, when the user supplies the criteria in the question itself, answers as a
comparison across resources (§16.5).

### 16.2 Funnel placement — the analysis is not cheap, so what is free comes first

The full answer needs a pass over the data. But most of the *signal* is
available from catalogs, file metadata and names before any row is read,
and that is what lets Scouting and Discovery triage "could this be in
scope?" and gate the expensive pass. Placement follows the stage rule in
CLAUDE.md rule 17 — *does this collect, or reason over what is collected* —
with the cost of each signal stated.

| Signal | Source | Cost | Stage | Confidence |
|---|---|---|---|---|
| **Subject from names and comments** — table, column, file and folder names; `pg_description`; README and descriptor text; DCAT `theme`/`keyword`; card tags | catalog / walk / descriptor | none beyond what Scouting already reads | Scouting | low–medium; a name is a claim |
| **Time grain from naming** — columns `*_date`, `*_ts`, `day`, `hour`, `period`; tables `daily_*`, `*_hourly`; partition keys `year=/month=/day=`; file names carrying dates (`sales_2025-03.parquet`) | same | none | Scouting | medium for partitions and file names, low for column names |
| **Entity grain from keys** — PK composition; a date column *in* the PK means per-period grain | catalog | none | Scouting | medium–high |
| **Coverage from partition bounds** — `pg_partitioned_table` / check constraints give exact ranges for partitioned tables | catalog | none | Scouting | high; only for partitioned tables |
| **Coverage from column statistics** — `pg_stats.histogram_bounds` on date and timestamp columns gives min and max without a row scan (after `ANALYZE`) — **but `pg_stats` is filtered by column `SELECT`, so this needs the data identity, not the catalog identity** (corrected 2026-09-25 per the credentials reply §9 and settled by a live probe the same day: `pg_read_all_stats` alone yields 0 `pg_stats` rows for a table without `SELECT`; the earlier wording called it free at Scouting) | data identity (B) | tiny once B exists | Discovery, when B is held; otherwise deferred to Analysis | high when stats are fresh; **absent means "run ANALYZE" or "no column access", and the envelope must say which** |
| **Coverage from file metadata** — Parquet and Feather footers carry per-row-group min/max per column, so date range comes from the footer alone; ORC likewise | file footer read, no data | tiny | Scouting | high |
| **Coverage from descriptors** — DCAT `temporal` and `spatial`; Croissant; HF card front matter; DataScope already declared on the asset | descriptor | none | Scouting | as good as the publisher |
| **Geography from names and classes** — columns named country, region, state, postcode, lat/lon; data-class matches by *name only* (ISO country code, postcode) | catalog + class registry | none | Scouting | low–medium |
| **Preliminary fit** — the above against the requirement: subject overlap, grain estimate compatible, catalog-bound coverage overlaps the window | stored rows | none | **Discovery** | stated per input; this is the gate |
| **Measured cadence and gaps** — one aggregate query per date column (`date_trunc(period), count(*) group by 1`) rather than sampling: one scan, exact; gaps = missing periods inside the range; per-region gaps by grouping on the region column too | data read, single aggregate pass per column | api_heavy / medium; bounded by the sampling config (§5.8) when the table is large | **Analysis** | high |
| **Measured spatial extent** — min/max of lat/lon columns; distinct values of region-typed columns matched to a reference set (`reference_data_match`) | data read | api_heavy / low–medium | Analysis | high |
| **Measured entity grain** — `n_distinct` of candidate key ≈ row count, from `pg_stats` first (data identity), sample second | data | tiny, then medium | Analysis (confirms Scouting's estimate) | high |
| **Quality by dimension** (§16.4) | mostly already-stored profiles | low once profiles exist | Analysis | per dimension |
| **Fit** — lens versus scope, grain compatibility, thresholds | stored rows | none | **Assessment** | states which inputs were measured vs estimated |

So the shape is: **Scouting collects the free estimates and names them as
estimates; Discovery computes a preliminary fit from them and decides
whether the aggregate pass is worth running; Analysis runs it; Assessment
compares against the lens.** For files, Parquet's footer statistics make the
Scouting estimate nearly as good as the measurement; for CSV there is no
free signal beyond names and the file's date, so CSV is where the
Discovery gate earns its keep. For databases, what the *catalog identity*
can see (structure, keys, names, comments, partition bounds, activity
counters) is the Scouting estimate; column statistics and everything
value-derived need the *data identity* and so arrive at Discovery only when
that identity is held — the two-identity model in
`design-notes/REPLY-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md` §9.

### 16.3 The questions

Cross-type (`*`), except where marked. Rule column as in §5.

| Stage | Question | Answering analysis | Mechanism | Rule | Perspectives |
|---|---|---|---|---|---|
| Scouting | **What is this data about — which subjects, business terms or data classes does it appear to hold?** | `subject_signals` (new; names, comments, descriptors → candidate glossary terms and classes; extends `semantic_suggestions` to whole-resource level) | Catalog + Egeria glossary | C | Consumer, Data Expert, Steward |
| Scouting | **At what grain does it look like it is recorded — one row per what, and per what period?** | `grain_determination` (exists, extended with time grain from naming, partitions and PK date columns; emits a `DataGrain` proposal with `interval`) | Catalog | C | Data Expert, Consumer |
| Scouting | **What period and places does it appear to cover, from catalog statistics, file footers and descriptors alone?** | `coverage_signals` (new; `pg_stats` bounds, partition bounds, Parquet footers, descriptor `temporal`/`spatial`, file-name dates; emits a measured-scope annotation with `basis` per field) | Catalog / footer / descriptor | C | Consumer, Governance |
| Discovery | **Could this be in scope for what I am looking for — worth the full pass?** | `preliminary_fit` (new; zero-fetch; the three Scouting estimates against the investigation's `DataLens`; per-input confidence; renders "no requirement declared" when none) | Analysis | C | Consumer, Financial |
| Discovery | Does the declared scope on the asset (if any) agree with the catalog estimate? | `coverage_signals` vs `DataScope` | Analysis | C | Steward |
| Analysis | **What is the actual cadence, and where are the gaps — missing days, missing regions, missing months?** | `coverage_profile` (new; aggregate pass per date column and per region column; period counts, gap list, completeness ratio; bounded by §5.8) | Profiling | C | Data Expert, Steward, Consumer |
| Analysis | **What is the actual spatial extent — bounding box, or the set of regions present?** | `coverage_profile` + `reference_data_match` | Profiling | C | Governance, Consumer |
| Analysis | Is the grain what the keys claim — is the candidate key actually unique per period? | `grain_determination` (measured, `n_distinct` / sample) | Catalog + Profiling | C | Data Expert |
| Analysis | **How good is it, by dimension — completeness, validity, consistency, uniqueness, timeliness, coverage completeness, accuracy?** | `quality_dimensions` (new; composite over stored profiles; one `QualityAnnotation` per dimension) | Analysis | C | Steward, Consumer |
| Assessment | **Does it fit what I am looking for — subject, grain, coverage and quality — and where exactly does it fall short?** | `requirement_fit` (new; lens vs scope field by field; grain compatibility; thresholds; per-input state) | Analysis | C | Consumer, App/AI Builder, Data Owner |
| Assessment | Is the coverage complete enough to declare (no gaps above the threshold in the window)? | `coverage_profile` [checks] | Analysis | C | Steward |
| Curate | Declare the scope and grain (from the measured proposal) | `DataScope` classification + `DataGrain` assignment, prefilled | Human | — | Steward, Data Owner |
| Enrichment | Supply subject, grain or coverage where nothing derivable exists (a bare CSV with no dates) | Enrichment form, feeds the same fields | Human | — | Data Owner |
| Discovery (automation) | Has the coverage moved — a new period appeared, a region dropped, a gap opened? | `coverage_change` comparator (§9.1) | Change Detection | C | Steward, Consumer |

For **datasets** (§7) the Scouting rows are answered from the descriptor
and the Analysis rows after download, through §6; `descriptor_conformance`
gains coverage as one of the things it checks. For **models** (§8) the
subject and coverage questions apply to the *training data*, through
`model_lineage` to §7 datasets; a model has no grain.

### 16.4 Quality as named dimensions

The seven signals the design already had, organised so "how good is it"
has one shape, and each published as a `QualityAnnotation` with
`qualityDimension` set. No composite score (§15): the answer is a table of
dimensions, each with its state.

| Dimension | Measured by | Already in the design as | Needs |
|---|---|---|---|
| Completeness | null fractions per column; required-column presence | `column_profile` | nothing new |
| Validity | conformance to data classes and reference sets, type conformance | `data_class_match`, `reference_data_match` | nothing new |
| Consistency | same schema across shards or partitions; FK integrity; cross-column rules | `schema_consistency`, `schema_conventions` | FK orphan check on DB side |
| Uniqueness | duplicate rows or files; key uniqueness | `file_fingerprint`, `grain_determination` | row-level duplicate count on DB side |
| Timeliness | freshness: last write vs now, vs declared cadence | `db_activity_signals`, mtime histogram, `dataset_descriptor` cadence | a declared cadence to compare against (from the lens or the descriptor) |
| **Coverage completeness** | gaps inside the covered range, by period and by region | **`coverage_profile` (new)** | the new analysis |
| Accuracy | agreement with a reference source | — | **a reference**; not measurable without one, and rendered as `not_established` rather than assumed |

### 16.5 The requirement is optional, often discovered, and refined as the investigation goes

**Project owner, 2026-09-22:** there is not always a specific requirement
known a priori, and requirements are refined over the course of an
investigation. §16.1's "one `DataLens` per investigation, set at framing"
is therefore the *end state* of a lens, not its starting point. Four
consequences for the design:

1. **Description comes first and stands alone.** The Scouting, Discovery
   and Analysis rows in §16.3 answer "what is this about, at what grain,
   covering what, how good" for every resource whether or not any lens
   exists. Those answers are the primary output, stored per snapshot as
   measured scope, grain and quality. A lens is never a precondition for
   surveying.
2. **Fit is a query over stored measurements, not a survey.** `preliminary_fit`
   and `requirement_fit` read the stored scope, grain and quality rows and
   compare them with whatever lens exists *now*. Changing the lens
   recomputes fit across every already-surveyed resource at zero fetch
   cost. This is what makes refinement cheap: the expensive pass is
   spent once per resource, the comparison as often as the requirement
   moves. It also means the same question works with no lens at all as a
   *comparison across resources* — "which of these cover 2025 at daily
   grain?" is fit with the lens supplied ad hoc by the question.
3. **Lenses are discovered as much as declared.** Three ways a lens comes
   into being besides the framing form: *from a resource* ("make this
   resource's held scope my requirement", the common case when the first
   good candidate defines what good looks like); *from a set* (the
   intersection or union of the held scopes of the resources in the
   working set, shown as "what is achievable with what we have", so the
   requirement is negotiated against reality rather than written in a
   vacuum); and *from a question* (the ad hoc lens in point 2, kept if the
   user says so). Each of these is a Curate-tier action on the
   investigation, and the framing form is its editor, not its only source.
4. **The lens has history.** Refinement means the lens changes, and a fit
   verdict is only meaningful against the lens version it was computed
   with. So the lens is versioned like everything else here — `DataLens`
   is a governance definition and carries the standard version
   properties — and every fit result records the lens version it used.
   "This resource fitted last week and does not now" must be answerable
   by showing what changed: the data, or the requirement.

5. **A survey serves every investigation, not the one that ran it.**
   Measured scope, grain and quality are facts about the *resource*,
   stored once per snapshot (rule D); the lens is the only thing that
   belongs to an investigation. So the match runs both ways: a resource
   that does not meet one investigation's lens is matched, with no new
   survey, against every other open lens — "this does not cover 2025 for
   EMEA, but it fits the North America study exactly" — and a new
   investigation begins by querying what has already been measured before
   it registers anything. Two consequences. The investigation's
   membership (`investigation-framing-design.md` §6, many-to-many) gains a
   *suggested* state: resources whose stored measurements fit the lens but
   were surveyed for someone else. And the demand side of the data-product
   question in §4 gets a concrete signal: a resource that fits several
   open lenses has demonstrated demand, before anyone subscribes. Publishing
   the declared `DataScope` and `DataGrain` to Egeria is what makes this
   work beyond RE — any consumer that can express a lens can find the
   resource.

What this changes elsewhere: `investigation-framing-design.md`'s
`ResearchQuestion` (§3 there, "the investigation's own open questions")
is the natural home for a lens that is still forming — a research question
whose answer *is* the eventual lens. The designer brief's fit summary
(§11) gets a fifth state alongside measured and estimated: *no lens; here
is what the working set could satisfy*.

### 16.6 Cost and what to build

| Piece | Cost tier | Phase |
|---|---|---|
| `subject_signals`, `coverage_signals`, time-grain extension of `grain_determination` | none / low (catalog, footers, names) | Phase 1 with `db_derived`; Parquet footer read joins Phase 2's `filesystem_structure` (it is metadata, not content) |
| Data requirement on the investigation, as a `DataLens` — optional, discoverable from a resource or a set, versioned (§16.5) | none (a form, three "make this my lens" actions, one Egeria write) | Phase 1; `preliminary_fit` works before it exists, as a comparison across resources |
| `preliminary_fit` | none | Phase 1, Discovery gate |
| `coverage_profile` | api_heavy / medium, one aggregate pass per date or region column, bounded | Phase 1 after `postgres_column_profile`; FS variant in Phase 2 |
| `quality_dimensions`, `requirement_fit` | low (composites) | end of Phase 1 |
| `coverage_change` comparator | low | with the other comparators |
| Designer: calendar heat strip for cadence and gaps; map with held and sought extents; the dimension table | — | round 2 of the designer brief |

**Egeria asks: none.** `DataLens`, `DataScope`, `DataGrain` and
`QualityAnnotation` cover it. The one convention to fix in `foundations.md`
is the `scopeElements` key set shared by lens and scope: `subjectTerms`,
`dataClasses`, `regions`, `jurisdiction`, `controller`, `grainStatement`,
`interval`, so that fit compares like with like.

### 16.7 The six cross-type gaps from the DB/FS coverage audit — one read-back layer, not six items

The DB/FS coverage audit (§2.2 there, 2026-09-23) found six cross-type
questions that all sit on Egeria-native elements — `DataScope`,
`DigitalSubscription`, `DigitalProduct`, zones and classifications — with
no working read or write path from RE. Its read that this is one plumbing
gap rather than per-type work is right. What §16 already answers, what it
extends, and what belongs elsewhere:

| # | Question | §16 status | What closes it |
|---|---|---|---|
| 1 | Can Egeria's engine reach this resource, or must RE survey locally, and which is cheaper? | **Not §16.** Rule B (§3) and Phase 1 slice 13 (`resource_reachability`, `CHECK_ASSET`-only engine action, cost record), in progress | slice 13; nothing here |
| 2 | Where is the resource physically — host, region, jurisdiction, residency? | **Partly.** §16's `coverage_signals` measures the *data's* spatial extent; the *resource's* location is a different fact, though both land in `DataScope.scopeElements` (§4's sovereignty row) | **extend `coverage_signals`** with a resource-location group, all free at Scouting: endpoint host and port from the Connection, cloud region from instance metadata where present, mount or bucket region for filesystems, portal country for datasets; jurisdiction and controller stay human-supplied (Enrichment) and are declared, never inferred |
| 3 | What is the data's scope in time — collection, validity, coverage? | **Answered.** The audit calls it "blocked on column-level date-range profiling"; it is not — `pg_stats` histogram bounds, partition bounds and Parquet footers give the estimate at Scouting with no profiling (§16.2), and `coverage_profile` measures it in Analysis | build order in §16.6 |
| 4 | Restrictions beyond the licence — zone, confidentiality classification, non-standard terms, agreements? | **Not §16, but the same read path.** These are classifications and relationships already on the asset in Egeria; nothing reads them back into the question layer | the read-back layer below |
| 5 | Is there a defined need or market? | **Answered by §16.5, corrected 2026-09-23.** The primary mechanism is `DataLens` fit: open lenses whose declared scope the resource's measured scope satisfies *are* the demand, and that needs no Egeria read beyond the lenses. `DigitalSubscription` does **not** apply to the resource directly — per Egeria's model a subscription attaches to a `DigitalProduct`, and a raw surveyed resource has no product wrapper (project owner, 2026-09-23) | lens fit (§16.5); an RFA index by requested data kind (a local query over `rfa_actions`, small); and the *indirect* subscription read below, which answers a different question |
| 6 | Ready to be offered as a data product? | **Composite over 1–5**, plus §16.4's quality dimensions and the declared `DataScope`/`DataGrain` as inputs | unblocked when the read-back layer and slice 13 land; the composite itself is a day |

**The read-back layer.** One analysis, `governance_context_readback`,
`source: egeria`, rule A in spirit (Egeria is the record; RE mirrors), rule D
in mechanics (rows in RE's store, keyed `(slug, read_at)`, so the question
layer and the change comparators read local rows). For the asset and its
anchored elements it reads:

| Read | Egeria source | Feeds question |
|---|---|---|
| `DataScope` classification | classification-explorer (`get` side of the same endpoint `add_data_scope` writes) | 2, 3 — the *declared* half of measured-vs-declared |
| Governance zones, confidentiality / criticality / retention classifications | classification-explorer | 4 |
| `License`, `DataSharingAgreement`, `Agreement` relationships and their terms | classification-explorer / governance-officer | 4, and the cross-type licence row in §4 |
| `DigitalProduct` membership of *this* asset, if any | digital-product view service | 6 |
| Existing `DigitalProduct`s, their member assets, and each product's `DigitalSubscription` count | digital-product view service | 5′ below — "is something like this already productised?", not "is there demand for this resource" |
| `Ownership` classification | classification-explorer | §4's ownership row |
| `DataGrain` assignment | data-designer | §16's declared grain |

Everything in that table has a pyegeria read method already
(`egeria-support-for-multi-resource.md` §7 for `DataScope`; the
classification-explorer family for the rest; `DigitalSubscription` is the
one to verify with a probe before building on it). The write side —
declaring `DataScope` and `DataGrain` from the Curate surface — is §16.1
and §14; this is its mirror image, and the two should be one slice so the
round trip is tested together: declare, read back, render, change the
declaration, see the comparator fire.

**5′ — what the subscription read actually answers.** Reading existing
products and their subscriptions does not measure demand for the resource
under review; it answers three neighbouring questions the project owner
named on 2026-09-23:

| Stage | Question | How |
|---|---|---|
| Discovery | Do assets already packaged in data products hold similar data — similar subject, scope, grain — to this resource? | derive a lens from each product's member assets (§16.5 point 3, "lens from a set": the union of their declared `DataScope`s, grains and subject terms) and run `preliminary_fit` of *this* resource against it |
| Analysis | What is different between this resource and the ones we already productise? | the same comparison, reported field by field: subject overlap, grain compatibility, coverage overlap, quality dimensions side by side |
| Discovery | Do we already have a similar data product — and how subscribed is it? | the product-derived lens that fits best, with its `DigitalSubscription` count as the "how wanted is that shape of data" signal |

This is **not a new analysis**. It is the §16.5 fit machinery with the lens
supplied from a product's assets instead of from an investigation, so it
lands as the read above plus one "lens from a product" action alongside the
existing "lens from a resource" and "lens from a set". It is also distinct
from the coverage audit's open similarity-search item (§2.6 there): that is
*content* similarity over pgvector embeddings; this is *scope* similarity
over declared and measured structure, cheap and exact, and the two
complement rather than overlap — content similarity finds candidates whose
scope has never been declared, scope fit ranks the ones whose scope has.
A product with many subscriptions whose member assets fit this resource
well is the strongest available signal that the resource *could* be
productised into an existing family, which is the input Q6 wants.

**Where it sits in the plan:** Phase 1, after the structured tables
(stream 3), as its own slice, cross-type by construction. It is not a
database or filesystem item and should not be filed per type. It is the
first analysis whose source is `egeria` for *every* resource type,
including repositories, so its results reader is the template for any
later Egeria-mirrored analysis.

### 16.8 Scope relations are per axis — "similar" is not one number

**Project owner, 2026-09-26:** two databases may both hold "customer data"
and differ entirely — one for pre-sales, one for post-sales support; one
for the US, one for New York, where the second is a subset of the first.
The scope-similarity question (§16.3, the three-way split of "similar
resources") therefore answers with a **relation per axis**, and
"substitutable" is derived from those relations, never scored.

**The axes**, each with its own source and its own comparison:

| Axis | What it captures | Held side | Sought side | Comparison |
|---|---|---|---|---|
| Subject | what the data is about — glossary terms, data classes | `scopeElements.subjectTerms`, `dataClasses` (declared) or `subject_signals` (measured) | the lens's terms | set relation |
| **Population** | *which* of that subject — customers in which lifecycle stage, which segment, which business process (pre-sales vs post-sales) | `scopeElements.population`, `lifecycleStage`, `businessProcess`; usually **declared**, occasionally inferable from names (`presales_customers`) | the lens's `DataLens` *processing type* — Egeria's own definition of a lens is "the scope of data for a particular type of processing", which is exactly this axis | set relation; **unknown when undeclared**, never assumed equal because the subject matches |
| Space | where — regions, bounding box, jurisdiction | `DataScope` bbox, `scopeElements.regions` | lens bbox / regions | containment |
| Time | collection, validity, coverage windows | `DataScope` times | lens times | interval containment |
| Grain | one row per what, per what interval | `DataGrain` | lens grain | finer / same / coarser |
| Structure | the schema or file signature | `db_fingerprint` / `file_fingerprint` | another resource's signature | same / subset / superset (this is the *structural* member of the split, §16.3) |

**The relation vocabulary**, the same on every axis: `same`, `contains`,
`contained_by`, `overlaps`, `disjoint`, `not_established`. The last is a
real value: an undeclared population axis renders as "population: not
established — the two may be the same customers or different ones", which
is the honest answer to the pre-sales/post-sales pair until someone
declares it, and the reason two "customer" databases must never be called
similar on subject alone.

**Derived answers**, computed from the per-axis relations and shown with
them, not instead of them:

- *Can B stand in for A?* — every axis `same` or `contains` in B's favour,
  grain `same` or finer, structure at least `subset`-compatible with what
  A's consumers use. One `disjoint` or `not_established` axis and the
  answer is "not without checking *axis*".
- *Is B a subset of A?* — structure `same` and one or more of space, time,
  population `contained_by`, the rest `same`. NY-of-US is: subject same,
  population same, structure same, space contained_by, time same.
- *Are these the same data twice?* — every axis `same` including structure;
  a candidate for the copy finding (§5.3) and for consolidation.

**What it changes.** `requirement_fit` and `preliminary_fit` (§16.3) report
per axis, and the designer's two-extent map and calendar strip (§11) are
the space and time axes drawn; the population axis has no drawing and is a
labelled line. The `scopeElements` key convention in §16.6 gains
`population`, `lifecycleStage` and `businessProcess`. `DataLens`'s
processing type is the sought side of population and should be set when
an investigation is framed. The structural and scope members of the
"similar" split are asked together on one row of the answer, because
"same structure, subset of rows by geography" is the precise statement
about the NY/US pair and neither half says it alone.

## 17. Prerequisites run themselves, and every run says what it cost

**Added 2026-09-23 at the project owner's direction**, from two questions:
should prerequisite steps run automatically when a step needs them, and
should every survey or step report what it actually cost, on a dashboard,
given that the funnel's whole argument is that early stages are cheaper
than later ones.

### 17.1 Prerequisites: yes, within the budget the user chose, and always as a result

**What happens today.** `surveyors/step_preconditions.py` checks, before a
step is dispatched, whether the stored data it reads exists (`cve_scan`
needs rows in `project_dependencies`). If not, the step is **skipped with a
named reason**, emitted as a real annotation so the skip is visible. That is
the right honesty and the wrong outcome for a person who asked the
question: they get "skipped: no parsed dependencies" when what they wanted
was the answer, one cheap step away.

**Decision (project owner, 2026-09-23):** prerequisite surveys or steps are
executed automatically as needed, telling the user.

Three conditions make that safe, and they are the design:

1. **Within the budget, unasked; beyond it, proposed.** Surveys are already
   composed by cost tier (`survey-model.md` Part II §3). A prerequisite whose
   declared `fetch_cost` and `compute_cost` fall within the tier the user
   selected runs without asking. One that crosses the tier, needs a download
   or clone, needs credentials the executor cannot resolve (rule B), or has a
   `human` answering kind becomes a **proposal**: "answering this needs
   `dependency_analysis` first — estimated 40 s, one download; run it?" The
   budget is the consent. A user who picked Scouting never triggers an
   Analysis-tier fetch by accident, and a user who picked Analysis is not
   nagged about a 200 ms catalog read.
2. **A producer map, because preconditions name data, not steps.** The
   precondition names are deliberately about *stored data*
   (`step_preconditions.py`'s own docstring: so the check does not encode an
   execution order it cannot enforce). Nothing today knows which step fills
   `project_dependencies`. Add a small declarative registry —
   `PRODUCES` on `StepInfo`, the tables a step writes — and derive the
   inverse at import. The resolver then walks precondition → producing step
   → *its* preconditions, with a **cycle guard** and two stop rules: a
   producer that already ran on this snapshot and found nothing is not
   re-run (its `nothing_found` is the answer, and re-running it is the
   loop); and a producer whose own precondition is a proposal turns the
   whole chain into one proposal listing every step and the summed cost.
3. **An auto-run is a result, not an omission.** Same principle as the
   skip. The report carries an annotation "ran `dependency_analysis`
   because `cve_scan` required `project_dependencies`", the activity log
   gets an entry (CLAUDE.md rule 16), and the cost (§17.2) is attributed to
   the *demanding* step as well as recorded on the producer, so "why did
   Scouting take three minutes" has an answer.

Where it lives per executor: on the **Prefect** path this is task
dependencies and Prefect renders the chain itself; on the **local thread**
path it is the resolver above in `survey_definition_executor.py`; on
**Egeria-orchestrated** definitions it is not RE's concern — the graph and
its guards say what runs, and a missing input there is a `survey-invalid`
guard, not a precondition. Rule E's remote executors get the same resolver
output as a serialised plan.

### 17.2 Cost: time is one axis of a vector, and yield is the denominator

**What exists.** `surveyors/step_cost_observer.py` already wraps every
local step: wall time, connection count, annotation count, and a
declared-versus-observed **disagreement** check (a step declaring
`fetch_cost: none` that opened a connection is flagged). Results are stored
as per-project metrics (`<step>_elapsed`, `observed_connects`, …).
`docs/funnel-cost-measured.md` is the one measurement made from them. That
is the seed, and it measures one thing — seconds — that the funnel argument
is not really about.

**The cost vector per step run**, cheap ones on by default, expensive ones
optional and sampled:

| Axis | Measures | Why it is not the same as time | Collector | Default |
|---|---|---|---|---|
| `wall_ms` | elapsed | what the user waits | exists | on |
| `cpu_ms` | process CPU time in the step | *compute*, which is what tier placement claims; a step that waits on the network is cheap in CPU and slow in wall time | `resource.getrusage` delta | on |
| `bytes_fetched` | bytes over the network into RE | download and API payload; the acquisition half of "110 s to fetch, 5.9 s to run" | counting wrapper on the HTTP client and `git` (already partly in `SourceCache`) | on |
| `api_calls` | external API requests by host | **for repositories this is the scarce resource**: the GitHub rate budget, not seconds | counting wrapper | on |
| `source_rows`, `source_queries` | rows scanned and queries issued against the surveyed database; files opened and bytes read for a filesystem | load *on the source*, which the source's owner cares about and time does not show | `pg_stat_statements` delta when installed, else query count; `os.stat` and read counters in the walk | optional, sampled |
| `llm_tokens_in/out` | tokens for agent and RAG steps | real money and wholly invisible today | the LLM client already reports usage | on |
| `egeria_calls` | pyegeria requests | load on the platform; distinguishes a native survey's cost from RE's | counting wrapper on the client | on |
| `cache_hits` | `SourceCache` and query-cache hits | so a warm run is not mistaken for a cheap step (22.6 s cold → 1.3 s warm is one step's real range) | exists in `SourceCache`; expose | on |
| `annotations`, `questions_answered` | yield | **the denominator** — a step that produces nothing was not cheap at any price | exists (annotations); questions from the catalog's `analysis_ids` inverse | on |
| `executor`, `source` | local / prefect / egeria / remote; whose engine | so native and remote runs sit on the same board; Egeria engine actions and Prefect flow runs both carry start and completion times | exists | on |

Two **derived** metrics are the ones that test the funnel's premise, and
they need the vector, not just seconds:

- **Cost per question answered**, per step and per survey definition. A
  Scouting definition that answers five questions for 2 s and 0 API calls,
  and an Analysis definition that answers twelve for 90 s and 340 calls,
  are both fine; the same Analysis definition answering three is the one to
  look at.
- **Tier ratio per resource**: Scouting cost as a fraction of Analysis
  cost, on each axis. The funnel promises this is small. Where it is not,
  a step is mis-tiered or under-declared, which is exactly the disagreement
  check generalised from "did it open a connection" to every axis.

**Storage.** One `step_runs` table, `(slug, step_key, surveyed_at, source,
executor, demanded_by, metrics jsonb, declared jsonb, disagreement text)`,
written by the observer instead of scattering `<step>_elapsed` metrics
across project rows. `demanded_by` is the §17.1 attribution. The survey-level
row is a rollup over its steps, so a survey definition's cost is a query,
not a second measurement. Prefect flow-run ids and Egeria engine-action
GUIDs go in `executor_ref` so the board can link out.

### 17.3 The dashboard: under Admin, and it exists to tune three things

Not an intent — it configures how the system behaves, which is the Admin
rule in CLAUDE.md. Four views, all over `step_runs`:

| View | Shows | What it is for |
|---|---|---|
| **Per step** | median and tail of each axis across all resources; disagreement rate; auto-run rate (how often it ran as a prerequisite vs on request) | finding under-declared and mis-tiered steps |
| **Per resource type × tier** | the tier ratio on each axis | validating the funnel; the number the design keeps asserting and has measured once |
| **Per survey definition** | total cost vector, cost per question answered, yield | which definitions earn their cost; which questions are expensive to answer |
| **Native vs local** | for steps with both a rule-A native and a rule-B local route: time, source load, Egeria calls, yield side by side | **tuning rule B's efficiency policy from numbers**, which §3 promised and nothing yet delivers |

Plus trend over time and an outliers list (the slowest, hungriest, and
lowest-yield runs of the week, with their `demanded_by`). The dataviz
conventions in the designer brief apply; this is round 2 material, after
the vector has a few weeks of rows in it.

### 17.4 Sequencing

| Piece | Cost | Phase |
|---|---|---|
| `PRODUCES` on `StepInfo` + inverse map + resolver with cycle guard, local path | a day | Phase 1, with the DB steps, because `postgres_column_profile` is the first step with a real chain (needs `schema_inventory`, which needs the catalog read) |
| Proposal rendering: "answering this needs X first; run it?" with summed cost | half a day, classic UI | same slice |
| `step_runs` table and the observer writing the vector (wall, CPU, bytes, API calls, Egeria calls, cache hits, yield, executor) | a day; the counting wrappers are the work | Phase 1, before the native-vs-local comparison is worth reading |
| Optional collectors: `pg_stat_statements` delta, LLM tokens | half a day each, behind config | when the first board shows a gap |
| Admin "Performance" panel, four views | designer round 2 | after two weeks of rows |

## 18. Scope, focus and clusters — working below the database

**Added 2026-09-25 at the project owner's direction**, after the live test of
`coco_pharma`: *"many of the questions and surveys are talking about the
database level but really the discussion should be (or include) the schema
and table level — e.g. #rows … users [need] to focus on specific schemas for
some of their surveys rather than entire databases … When faced with a large
database, like a data warehouse, there could be hundreds of tables — in
later funnel stages you probably will focus on a few at a time — a logical
cluster of related tables. The design as is doesn't support more than a few
of anything."*

That last sentence is accurate, and the catalog shows why: **49 of 62
analyses declare `target_shape: whole_resource_only`**, 12 `corpus`, 1
`single_container`. The question rows are phrased at database level. The
left navigation lists resources and never their parts. So every answer is a
rollup of the whole database, every survey runs against all of it, and a
warehouse with 400 tables produces the same one-line answers as a demo with
three. The §16 coverage and grain work made this worse by adding more
whole-database questions to a model that could only answer at that level.

### 18.1 Three things, kept distinct

| Concept | What it is | Persisted as | Made by |
|---|---|---|---|
| **Containment level** | the engine's declared hierarchy: server → database → schema → table → column (per engine, `REPLY-SCHEMA-AS-SUB-RESOURCE.md` §5) | `sub_resources` rows, `kind` = the level, `locator` = the path, created deterministically from the inventory | the survey, not the user |
| **Focus** | *what the funnel pages currently show and surveys currently run against*: the whole database, one schema, a set of tables, or a cluster. A selection, not a resource | a locator set on the investigation (`investigation_scope`: resource, locators, set at, by whom); the current focus is a URL state and a header crumb | the user, by clicking in the tree or accepting a proposed cluster |
| **Proposed cluster** | a logical set of related tables that Discovery *infers* from structure and use — the database analogue of architecture recovery's *components* for repositories, and the one piece here the field does not do (§18.9) | a Discovery finding: `sub_resources` row with `kind = proposed_cluster`, members and evidence in `detail_json` | Discovery; never a decision |
| **Domain / data product** | the *accepted* form of a cluster: what every catalog calls a domain (organisational, owned, hierarchical) or a data product (curated, contracted, subscribed). Not a third concept | in Egeria, exactly its existing types: a `Collection` with `CollectionMembership` over the table assets, a `SubjectArea` classification (model 0425) where the grouping is by meaning, a `DigitalProduct` when it is offered (§4); locally the same `sub_resources` row promoted to `kind = domain` with the Egeria GUID | a curator, in Curate, from a proposal or by hand |

Focus is not registration. A schema or cluster in focus is still a
sub-resource of the database; D3's *first-class on direct registration*
remains the only way it becomes a top-level resource. What changes is that
**every stage page, question, survey row and answer is scoped to the focus**,
and says so.

### 18.2 The funnel narrows scope as it goes

| Stage | Scope | What the page shows |
|---|---|---|
| Scouting | the whole database, **always broken down by containment level** — never a rollup without its parts | the tree: schemas with table counts, rows, bytes, activity, credential visibility per schema; system schemas folded away |
| Discovery | per schema, **tables ranked by importance**, then **proposed clusters** | first a ranked list of tables — the entry point at scale is not the tree (§18.9) — from signals RE already has or can read cheaply: activity counters and scan counts (catalog identity), FK degree from `db_relationship_graph`, row estimates, query statistics where `stats` is held, OpenLineage run facets where an emitter exists, and **declared or measured `DataScope` and `DataGrain`** (a table whose scope and grain match the investigation's lens ranks above one that merely has traffic — the project owner's point that scope and grain are strong focus signals, §16.5). Then `db_relationship_graph` components become cluster proposals (FK-connected sets, naming prefixes, shared key columns, co-access); `db_classification` per schema; `preliminary_fit` per schema and per cluster. The *worth pursuing* verdict tells the user where to focus |
| Analysis | a cluster or a few tables | column profiles, data-class and reference-set matches, coverage and grain per table; the cost vector per focus so a 400-table warehouse is never profiled whole by accident |
| Assessment | a cluster | quality dimensions, exposure, fit against the lens, readiness — per cluster, with the tables listed |
| Curate | a table or cluster | declare scope, grain, classes, ownership per table; accept or reshape clusters; promote to first-class if wanted |
| Automate | the focus | comparators scoped to the tables the user cares about, not the database |

This is the repository path's *scope narrowing* (`scoping.py`,
`target_shape: corpus` with a `scope_locator` path-prefix filter;
architecture recovery's components → blueprints) applied to databases with
the engine's containment levels as the axis. The mechanism exists; the
catalog tagging, the questions and the navigation do not use it.

### 18.3 Questions carry a level, and answers carry a distribution

Add a **`Level`** column to the question CSV: `database`, `schema`,
`table`, `column`, or several. "How many rows?" is a *table* question; at
database or schema level its answer is a **distribution**, not a sum: "23
tables in `coco_ods`: `customers` 20 rows … top 10 shown, 13 more; 3,526
rows across the schema (7 of 23 catalog estimates as of 2026-09-18)". The
envelope gains `scope` (the locator set answered for) and `shown_of`
(N of M), and the rule from §16.3 stands: **every answer names its scope**.
A whole-database ✓ on a question whose level is `schema` is not an answer.

**Specification (2026-09-25, built the same day on `re/questions-level-column`).**

- **Column:** `Level`, one column, `;`-separated, validated against a
  controlled vocabulary the way Purposes are — a typo stops the build. Blank
  means `resource`. Registered in both generators' non-perspective lists
  (`NON_PERSPECTIVE_COLUMNS`, `OPTIONAL_LEAD_COLUMNS`), because any column
  they do not know becomes a phantom Perspective.
- **Vocabulary, engine-neutral:** `resource`, `container`, `member`,
  `field`. "Schema" does not exist on MySQL and the level names come from
  each engine's containment declaration, so the CSV uses the abstract four
  and the guide maps them per resource type: database = database / schema /
  table / column; filesystem = root / folder / file / field; dataset =
  dataset / distribution / file / field; repository = repository /
  component / file / symbol.
- **Semantics:** the level(s) at which the answer is a *single value*.
  Asked above its level, a question answers as a ranked distribution ("top
  10 and 13 more, total across the scope") — nothing in the CSV enumerates
  the combinations. A row may carry two levels when it is natural at both
  ("how big is this database" is `resource;container`: one figure for the
  database, and a breakdown per schema that is not a derived distribution
  but the same answer at the next level).
- **Consumers:** the YAML entry gains `levels`; `QuestionCatalogEntry.levels`
  defaults to `["resource"]` for entries generated before the column
  existed; the Questions tab filters by the current focus (§18.1) and rolls
  up the levels below it; the envelope gains `scope` and `shown_of`
  (slice 21's second half). Survey-definition generation is unaffected —
  `ScopedBy` links are per question, not per level (verified: regenerating
  after the column landed changed nothing).
- **Egeria:** not published yet. Level rides the same valid-value-set
  mechanism as Purposes (§10, decided 2026-09-25), a second small set, so
  one server fix unblocks both.
- **The second extension slice 21 still owes:** per-type `Answering
  Analysis`. Cross-type rows answer a database with repository prose today
  (`REVIEW-SURVEY-PANE-285.md` §6.3). Rather than five new columns, the one
  cell allows per-type segments with a type prefix and a type-neutral
  default — `N/A — direct field || database: N/A — direct field
  (pg_description) || filesystem: descriptor_detection` — and a type with
  neither segment nor default renders *not authored for this type*.
- **Guards:** unknown level → build fails (built). A row below `resource`
  whose analysis produces no per-member rows is the "answered with counts,
  no schema named" failure; that guard lands with slice 20's `scopes`
  declaration, which is what makes it checkable.

Cross-type questions (§4) carry `resource`; §5's database questions were
levelled row by row on 2026-09-25 (9 `container`, 7 `member`, 7 `field`,
plus the dual-level rows); the answering analysis for a `member`-level
question must produce per-table rows, which the structured tables (§5.7)
already hold.

### 18.4 Analyses accept a scope

`target_shape: whole_resource_only` becomes the exception, not the default,
for database analyses. Each declares the levels it can run at (`scopes:
[database, schema, cluster, table]`), the API takes a locator set, results
are stored **with the scope on the row** (rule D's key gains `scope`), and
`schema_scope.py`'s grouping by containment level is the filter. Rollups
are computed from scoped rows and labelled as rollups (`REPLY-SCHEMA-AS-SUB-
RESOURCE.md` §1); nothing runs whole-database because a whole-database run
was the only shape available.

### 18.5 Scale rules, so hundreds of tables are a normal case

- The left navigation is a **tree with counts**, not a list: database →
  schemas (table count, rows, visibility) → tables, with search, paging past
  50, and clusters shown first once they exist. Nothing renders hundreds of
  rows flat.
- Every list says **N of M shown**; every survey row says what scope it ran
  on; every rollup names what it rolled up.
- Sampling (§5.8) and the cost vector (§17.2) are per table and per focus;
  "profile the warehouse" is a proposal with a summed cost, never a click.
- Clusters are the unit of work in Analysis and beyond; a table outside any
  cluster is reachable by search, not by scrolling.
- **Include and exclude patterns at registration**, at schema, table, view
  and column level — the one control every crawler and profiler in the
  field exposes and ours has none. Egeria already defines the vocabulary
  (`includeSchemaNames`/`excludeSchemaNames`, `…TableNames`, `…ViewNames`,
  `…ColumnNames` on the JDBC integration connector; catalog/schema/table on
  Unity) and RE should use the same names so a pattern set travels with the
  asset into Egeria's own cataloguing. Note the Postgres *survey* service
  takes only the generic `finalAnalysisStep`/`ignoreAnalysisSteps`; the
  include/exclude lives on the cataloguing side, which is where scope is
  decided anyway.
- **A per-table profiling policy** persisted on the sub-resource — sample
  strategy and bounds (§5.8), schedule, or *never* — so cost is set once per
  table the way every profiler does it, instead of per resource and
  analysis.

### 18.6 Egeria already has most of this — use it rather than mirror it

**Project owner, 2026-09-25:** Egeria addresses several of these issues
itself. Mapped, with what each is for in this model:

| Need | Egeria mechanism | Where it applies |
|---|---|---|
| the levels | `DeployedDatabaseSchema`, `RelationalTable`, `RelationalColumn` assets — the native Postgres survey creates and annotates them per schema and table; `sub_resources.egeria_guid` links each local row to its asset | Scouting onward |
| scoping what is catalogued and surveyed | include/exclude name lists on the JDBC integration connector and Unity catalog config (schema, table, view, column; catalog for Unity) — **which RE does not use today** | registration |
| accepted clusters by meaning | `SubjectArea` classification (0425) with `SubjectAreaHierarchy`; `SubjectAreaDefinition` as the governance definition behind it | Curate |
| accepted clusters as bundles | `Collection` + `CollectionMembership` (the `blueprint_materializer.py` shape) | Curate |
| offered clusters | `DigitalProduct`, `DigitalProductCatalog`, `DigitalSubscription` (§4, §16.7) | Curate → product |
| meaning on tables and columns | `SemanticAssignment` to glossary terms, `DataClassAssignment`, `ValidValuesAssignment` — the accepted forms of `semantic_suggestions`, `data_class_match`, `reference_data_match` (§5.4); applies once catalogued, so it is Curate work | Curate |
| who may see a scope | governance zones and security tags on the assets and collections — the same mechanism §5 of `security-model.md` relies on for connections | Curate; also the draft-visibility answer for proposals (§14) |
| what other tools know | OpenLineage: Egeria's event-receiver integration connector ingests runs, and Lovelace derives `DataScope`, run profiles and data-quality summaries from them. A co-located Marquez is the cheap way to have that history for resources RE surveys. **Nothing says this must wait for cataloguing**: RE can ask Marquez what it knows about a table by name during Scouting, as a signal, and Egeria consumes the same events after cataloguing | Scouting (pre-catalogue signal) and Curate (post) |

Declared scope and grain (§16) land on the table asset; lens fit (§16.5)
runs per cluster or domain, which is where "does this data fit what I am
looking for" is actually answerable.

### 18.7 For the designer

The tree navigation with focus; a **focus crumb** in the header
("coco_pharma › coco_ods › 23 tables") that every page carries; distribution
rendering (top N with "and M more", the calendar strip and profile card per
table from §11); cluster proposals as a Discovery result the user accepts,
edits or dismisses; the survey pane rows scoped to the focus and saying so.

### 18.9 How other products handle this, and what is borrowed

Reviewed 2026-09-25 at the project owner's request before executing, from
knowledge of the products rather than fresh verification. We are not
unique; the shape of the answer is stable across the field.

| Question | What the field does | Borrowed into §18 |
|---|---|---|
| Navigating below the database | every catalog (Unity, Purview, Alation, Atlan, DataHub, OpenMetadata) renders the engine hierarchy as a tree with each level a first-class page; trees collapse past a threshold and rely on search and facets; Alation and Atlan show usage-derived popularity on the node | the tree with counts and search (§18.5); focus behaves like *being on a node's page*, persisted only as where you were, not as a mode |
| Scoping what is profiled | include/exclude patterns at schema and table level on every crawler (OpenMetadata filter patterns, Purview scan rule sets, Glue include paths, DataHub allow/deny); profiling on a chosen subset with sampling and a size cap; a per-table schedule | include/exclude at registration using Egeria's own names; per-table profiling policy (§18.5) |
| Logical clusters | three distinct mechanisms, kept distinct: **domains** (organisational, owned, hierarchical: DataHub, Atlan, OpenMetadata, Collibra, Purview collections), **data products** (curated, contracted, subscribed: DataHub, OpenMetadata, Atlan), **subject areas** (data-modelling tools: erwin, ER/Studio submodels, drawn by a modeller). None *infers* clusters from FK graphs; schema-summarisation research does, and is not productised | proposed cluster stays (RE surveys the unknown and has nobody to assign domains yet); the accepted form is Egeria's SubjectArea / Collection / DigitalProduct, not a third concept (§18.1, §18.6) |
| Level of answers | catalogs show table statistics on the table page and only counts and lists above it; observability tools (Monte Carlo, Bigeye, Elementary) keep row count, freshness and volume as per-table series and make the database view a **ranked list**, never an aggregate | the distribution answer with ranking (§18.3) |
| Prioritising at scale | Monte Carlo key assets, Bigeye importance, Alation popularity, DataHub usage, Select Star "most queried / most joined": rank by query volume, lineage fan-out, recency of use | Discovery's ranked table list first (§18.2), from activity counters, FK degree, query statistics where held, OpenLineage — **and DataScope / DataGrain fit against the lens**, which the field does not have and which is RE's differentiator |
| Meaning and stewardship | glossary assignment, classification and ownership are curation steps after cataloguing, everywhere | Egeria's `SemanticAssignment`, `DataClassAssignment`, zones (§18.6) as the Curate-tier acceptance of what Discovery and Analysis proposed |

### 18.8 Where it goes in the plan

Before any re-land of the survey pane (`REVIEW-SURVEY-PANE-285.md` §4),
because the pane, the Questions tab and the answers all take a scope:

1. `investigation_scope` and the focus crumb; the tree navigation over
   `sub_resources` for the levels the inventory already produces.
2. `scopes` on the database analyses and the locator-set parameter; results
   keyed by scope; `schema_scope.py` as the filter. Convert the §5 analyses
   from `whole_resource_only` one by one, starting with `schema_inventory`,
   `row_count_snapshot`, `db_relationship_graph`.
3. `Level` on the question CSV and the distribution envelope; the Questions
   tab scoped to the focus.
4. Include/exclude at registration (Egeria's names) and the per-table
   profiling policy.
5. Table ranking in Discovery from the signals RE holds; then cluster
   proposals from `db_relationship_graph`; accept in Curate as
   `SubjectArea` / `Collection` / `DigitalProduct`.
6. Then the pane re-land, per focus.

## 19. Two entry paths: a resource Egeria does not know, and one it does

**Added 2026-09-25 at the project owner's direction.** RE has two roles that
look alike and are not: *determine new resources worthy of cataloguing and
use*, and *further explore, survey and analyse things Egeria already knows
about, at least in part*. Some RE surveys augment analyses Egeria performs
itself through its integration daemon and integration connectors, so that
path has to be designed through, not assumed to be the unknown path with a
GUID attached.

Pieces already in place: rule A (native results are canonical in shape),
rule D (every result is mirrored locally), the governance read-back layer
(§16.7), reuse-by-qualified-name in the catalogue step
(`egeria_database_surveyor.py:285`), and a Discovery question "has this
been catalogued in Egeria, and when?". What was missing is the entry path
itself, and what "known" means per containment level.

### 19.1 "Known" is per level and per depth, not a flag

Egeria's knowledge of a database is a matrix, and RE's job differs per
cell:

| Level (§18.1) | absent | catalogued (structure exists) | surveyed (annotations exist) | curated (scope, grain, classes, owner declared) |
|---|---|---|---|---|
| server, database | RE registers and offers Catalog & Survey | RE adopts the GUID, reads back | RE reads back the reports; surveys only what is missing | RE reads back and respects the declarations |
| schema, table, column | RE's inventory is the only structure; sub-resources local until published | **an integration connector probably maintains these** (§19.3): RE reads, never writes structure | RE mirrors annotations per table (rule D) | declarations bind RE's proposals: a declared grain is not re-proposed |

The Discovery question becomes: *"How much of this does Egeria already know
— catalogued, surveyed, curated — at which levels, maintained by what, and
when was it last refreshed?"* Its answer is a small table, not a yes.

### 19.2 Path A — unknown to Egeria

Unchanged, and the security model's registration case: the user supplies
the catalog identity; RE scouts locally; Discovery decides worth; *Catalog
& Survey* creates the assets from Egeria's templates (server, database, the
levels the inventory found, connections per identity per `security-model.md`
§4) and runs the native survey; RE's own findings publish as annotations
on those assets. Everything RE created, RE may later repair or delete.

### 19.3 Path B — known to Egeria, in whole or in part

Five rules, in the order they run:

1. **Resolve identity before anything else.** Match the registration to an
   existing asset by endpoint (host, port, database) and by the qualified
   name convention, at every level, and adopt the GUIDs. **Never create a
   second asset for a resource Egeria has.** The reuse path exists for the
   database; it has to exist for schemas and tables too, and the
   `coco_pharma` lesson applies: reuse must *repair* what it finds partial
   (a connection with an unbound placeholder) rather than skip it.
2. **Read back before surveying.** `governance_context_readback` (§16.7),
   the existing survey reports and their annotations, the structure the
   connector maintains, the declared `DataScope`, `DataGrain`, classes,
   terms and ownership — all into RE's store as rows with `source =
   egeria` (rule D). The Questions tab answers from those rows first.
3. **Survey the gaps only.** With Egeria's knowledge in the store, the
   question layer knows which questions are already answered and at what
   freshness; the prerequisite resolver (§17.1) treats a fresh Egeria
   answer as a satisfied producer and proposes only the steps whose
   questions are unanswered or stale. A native survey report from
   yesterday is not re-run locally today because RE has a local step for
   it.
4. **Write only what RE owns.** RE publishes survey reports, annotations,
   proposals (`contentStatus: DRAFT`), RFAs, and — through Curate — the
   declarations a curator makes. RE **never edits structural elements it
   did not create**: a schema, table or column maintained by an integration
   connector belongs to the connector, which will overwrite RE's edit on
   its next refresh anyway.
5. **Show provenance.** Every fact on the page says where it came from:
   *from Egeria (connector X, refreshed 3 h ago)*, *from Egeria's survey
   (report of 2026-09-21)*, *measured here (as `egeria_user`, just now)*,
   *declared by a curator*. The four are different kinds of truth and the
   design's honesty rules apply to each.

### 19.4 Coexisting with the integration daemon

Egeria's integration connectors (the JDBC integration connector for
schemas, tables and columns; the Postgres server connector for databases;
Unity, Kafka and file connectors for theirs) run in the integration daemon
on their own schedule and **own the structural catalog** of what they
maintain. Consequences for RE:

- **Structure is rule A** when a connector maintains the asset: Egeria's
  structure is canonical; RE's local inventory is for RE's own store and
  for the gap check, not a competing truth. A `maintained_by` fact
  (connector, last refresh) is part of the read-back and shown with the
  structure.
- **Drift between source and catalog is a finding.** RE reads the source
  directly and Egeria's structure through the read-back; their difference
  ("Egeria's catalog lags the source by 3 tables since the last refresh") is
  a comparator and an RFA to whoever runs the connector — the one thing RE
  can tell that neither the connector nor the source can.
- **Include/exclude travel with the asset.** The connector's own include
  and exclude lists (§18.5) define what Egeria will ever know; RE reads
  them and does not report as "missing from Egeria" what was excluded on
  purpose.
- **RE's steps augment, in the same conventions.** Where a native survey
  exists, RE reads it back; where RE's step adds what no native service
  computes (rule C), it publishes onto the same asset under the same
  report conventions, so a consumer sees one survey history. Longer term,
  RE's steps register as governance services so Egeria's own processes
  can call them (execution permutation 2), and the integration daemon's
  refresh can trigger RE's gap survey through an engine action rather
  than a person.

### 19.5 Egeria as a discovery source

Discovery sources today are repository-shaped (GitHub organisations,
quick lists). Egeria itself is the natural source for Path B: enumerate its
assets by technology type (`find_assets` with the technology type, which
already returns the connections §4.1 of the security model needs), diff
against RE's registry, and queue *"known to Egeria, never surveyed by RE"*
and *"surveyed by Egeria, never read by RE"* as Discovery work — with
Egeria's notifications (§9.2) telling RE when a connector adds an asset, so
the queue fills itself.

### 19.6 Questions this adds

| Stage | Question | Answered by |
|---|---|---|
| Discovery | How much of this does Egeria already know — at which levels, maintained by what, refreshed when? | read-back (§16.7) + `maintained_by` |
| Discovery | What has Egeria surveyed that I have not read yet? | survey-report read-back vs local rows |
| Discovery | Does Egeria's catalog match the source, or has it drifted? | inventory vs read-back comparator |
| Analysis | Which of my questions are already answered by Egeria's surveys, and which need a local run? | prerequisite resolver over the store |
| Curate | What has been declared on this already (scope, grain, classes, terms, owner), and by whom? | read-back of classifications and assignments |

### 19.7 Where it goes in the plan

Identity resolution at every level and the read-back-before-survey rule go
with §18.8's first item, because focus and the tree are built over
`sub_resources` rows that must carry Egeria's GUIDs when they exist.
`maintained_by` and the drift comparator go with the read-back slice
(§16.7). Egeria as a discovery source is its own small slice after those.

*Inventory sources for §1: three read-only sweeps on 2026-09-20 over
`resource_explorer/surveyors/{database,filesystem,file_classifier,sub_surveyors}`,
`facts.py`, `registry.py`, `configdata/analysis_catalog.yaml`, both question
generators, the Egeria Java survey connectors and `OpenMetadataType.java`,
and pyegeria's `automated_curation.py` / `notification_manager.py`.*
