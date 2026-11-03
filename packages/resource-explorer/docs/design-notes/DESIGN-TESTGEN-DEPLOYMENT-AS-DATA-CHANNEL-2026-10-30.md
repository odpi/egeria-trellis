# Design — an existing TestGen deployment as a data channel (2026-10-30)

> Superseded in part by Q2(b), 2026-10-31: analytics are found through the `pyegeria.analytics` entry point (`trellis.analytics` is only an alias), installing the package registers it, and report specs name functions by registered name, not dotted path or `PYEGERIA_REPORT_SPEC_MODULES`. See `packages/trellis-analytics/README.md`, "How analytics are found".

Owner's words (architect chat, 2026-10-30): "I think at some point (not urgently) we should plan on integrating with
existing TestGen deployments so that we can gain access to its existing data rather than resurveying - it is another
way to expand our reach by using what other tools have learned."

Design only; not urgent; no code, no DDL in this note. Written by the Architect for docs/design-notes/.
Companion notes: REQUIREMENTS-RE-DATA-CHANNELS.md (why one channel is never enough), DESIGN-QUALITY-AT-SCALE-AND-TESTGEN.md
and DESIGN-QUALITY-Q4-PORT-PLAN-AND-CALIBRATION.md §6.5 (what RE already takes from TestGen), INTEGRATION-TESTGEN-RE-EGERIA.md.

## 0. The relationship today, so the change is visible

| TestGen's… | RE today | After this note |
|---|---|---|
| test-type catalogue (YAML + SQL) | vendored as data, pinned by hash, executed by RE | unchanged |
| engine, scheduler, UI, auth, MCP | not used, never run as a service | unchanged |
| **collected data** (profile_results, test_results) | not used; an export FILE can seed proposals for calibration only (`testgen_import.py`, §6.5) | **read live, read-only, from a deployment's metadata database, as a source of profile and result facts** |

The 2026-10-21 ruling stands: RE's slice is the engine of record for what RE runs. This note adds a second origin of
facts, not a second engine. Nothing RE learns from TestGen is re-run, re-scored or republished as RE's own.

## 1. What a TestGen deployment holds that RE wants

TestGen keeps one Postgres metadata database per install (schema from `testgen/template/dbsetup/*.sql`, revision in
`tg_revision`). The tables that matter, with the RE table each maps to:

| TestGen table | Grain | RE table | Note |
|---|---|---|---|
| `connections` | one per surveyed database server (`sql_flavor`, host, port, db; password encrypted) | the RE database resource (`databases`) | match by host:port/db to an existing RE resource; never read the credential |
| `table_groups` | a schema (or set) inside a connection, with include/exclude masks and sampling settings | the resource's schema scope (`resource_scope_events`) | TestGen's "table group" ≈ RE's included schema set |
| `profiling_runs` | one profile run: start time, table/column/record counts, anomaly count, dq score | a survey/step run (`step_runs`, executor `testgen`) | as-of for everything below |
| `profile_results` | one row per column per run, ~75 measures | `database_value_profiles` | **same column names for 60+ measures** (record_ct, value_ct, distinct_value_ct, null_value_ct, min/max/avg_*, future_date_ct, functional_data_type, frequent_patterns, frequent_values, distinct_value_hash, sample_ratio…) — this is the payoff of keeping TestGen's names in slice 1 |
| `profile_anomaly_results` + `profile_anomaly_types` | TestGen's hygiene findings per column | **no RE table today**; candidates for `database_quality_proposals` with origin `testgen` or for a new findings family | decide in §6 |
| `test_definitions` | one per test on a column/table in a suite, with baseline/threshold parameters | `database_quality_proposals` (origin `testgen`) | already handled by `testgen_import.build_rows` from an export file; the live read replaces the file |
| `test_runs` + `test_results` | one row per test per run: result_status (Passed/Failed/Warning/Error/Log), result_measure, threshold_value, dq_prevalence | `database_quality_results` | status words map 1:1 to RE's passed/failed/warning/error; TestGen "Log" → RE `not_established` with the message as reason |
| `data_table_chars`, `data_column_chars` | TestGen's latest-known structure per table/column | `database_schema_inventory`-style rows | optional; RE's own inventory is cheap |
| `score_*`, `test_suites`, `job_*`, `auth_users`, `notification_*` | TestGen's product features | not read | out of scope |

## 2. What "channel" means here (REQUIREMENTS-RE-DATA-CHANNELS §3, one contract, several channels)

RE's capability contract for a database is: inventory, profile, propose, run, publish. A channel is how the FACTS for
a capability arrive. Today there are two: RE's own survey (reads the database) and Egeria's survey reports (reads
Egeria). This note adds a third, **a quality tool's metadata store**, with TestGen as the first adapter, on the same
footing as the CKAN adapter is for open-data portals: a kind of source with a small adapter per tool.

Rules carried over from the channel requirements and the two-publish-channels ruling:
- A fact says where it came from. Every imported row carries `source = "testgen:<connection name>@<host>"`, `ran_as`
  = the TestGen run's identity if recorded, else "testgen", and `surveyed_at` = **TestGen's run time**, never the import time.
- Absence stays absence. A column TestGen did not profile is not a row; a measure TestGen's dialect does not take
  (`NULL` in profile_results means not applicable) renders with RE's existing "—"/"not collected" words, never 0.
- No re-publication by RE of what TestGen published. If the deployment already pushes results somewhere, RE does not
  mirror them to Egeria as its own facts; RE's publish of a DataQualityRule stays tied to a check a person accepted in Curate.

## 3. Connection and registration (Admin)

A TestGen deployment is registered in Admin like a database credential, under a new section "Quality tools":
name, Postgres connection to the TestGen metadata database (read-only role; RE's standing rule for table preview and
value profile: the signed-in person's own credential, stored per person), TestGen project code(s) to read. Nothing
is written to TestGen's database; the connection test asserts the role has SELECT only, and the adapter refuses to
start otherwise (SQL-text validation is not a guard; the ROLE is).

Matching deployments to RE resources: for each `connections` row, the adapter proposes a match to an RE database
resource by host, port and database name, and a person confirms on the resource's Scouting tab ("TestGen knows this
database as <connection_name>: link ›"). An unmatched TestGen connection is listed, not auto-registered — Scouting
stays a person's step (the same rule as open-data portal results → work list → scout).

## 4. The read: one step per capability, same shape as RE's own steps

Three library-style steps, declared through the existing `trellis.analytics` entry point so they appear in
Survey & analyses with the same cues as fs_activity or statistics_freshness, and so the extensibility gate covers them:

1. **`testgen_profile_read`** (profile capability): for a linked resource, read the latest `profiling_runs` per table
   group and its `profile_results`; write them as a `database_value_profiles` batch with `source = testgen:…`,
   `surveyed_at` = profiling_starttime. Measures RE does not store are dropped and counted ("12 measures TestGen takes
   that RE does not keep"); measures RE stores but TestGen left NULL get RE's not_applicable state with reason
   "TestGen's <flavor> profile does not take this".
2. **`testgen_results_read`** (run capability): read `test_definitions` for the resource's suites into proposals with
   origin `testgen` (the live form of `testgen_import.build_rows`; same rules: only ported types become proposals,
   verdicts win, parameters validated), and `test_results` for the latest `test_runs` into `database_quality_results`
   with `rule_source = "testgen"`. A result of a test RE never proposed is still a result: it answers Q-0097 ("which
   checks hold, which fail") and is labelled "run by TestGen on <date>".
3. **`testgen_anomaly_read`** (optional, §6): `profile_anomaly_results` → findings.

Each step is read-only on both ends except RE's own registry writes, runs under the initiating person (action
identity rule), and prices itself from `profiling_runs.column_ct` before running.

## 5. What a person sees

- **Value profile tab**: the header gains the source: "Value profile · 456 of 1,236 columns measured · profiled
  2026-10-30 11:12 **by TestGen (prod-dq@dq-host)**" when the latest batch is TestGen's; a resource with both an RE
  batch and a TestGen batch shows a Source select beside State and Type (default: the newer). Cells are unchanged;
  the absence words already distinguish not-applicable from not-taken.
- **Questions (Q-0079, Q-0096, Q-0097)**: the step line names the source ("testgen_profile_read · run 3h ago · from
  TestGen's run of 2026-10-29 02:00"); the headline reader is the same `_value_profile_headline`.
- **Curate › Structure quality checks**: proposals with origin `testgen` carry the existing origin cue; results say
  "run by TestGen" in the Profile-of column's title. Accepting a TestGen proposal makes it RE's check from then on
  (RE runs it); that is already §6.5's rule.
- **Publish**: unchanged; a TestGen-run result is evidence on the row, not a thing RE publishes.
- **Admin › Quality tools**: the registration, the connection test, the matched/unmatched connections list, and the
  last read per resource.

## 6. Open questions for the owner

1. **Anomalies.** TestGen's `profile_anomaly_results` (e.g. "Suggested_Type", "Potential_PII", "Unlikely_Date") are
   hygiene findings RE has no table for. Options: (a) a findings family on the resource (RFAs/Understanding) with
   origin testgen; (b) map the ones that correspond to a vendored test type into proposals; (c) skip in slice 1.
   Architect's pick: (c) now, (a) when the files work needs a findings family anyway.
2. **Which run is "the" profile.** Latest per table group is the obvious default; TestGen also keeps history. RE's
   change-rate reader could use two TestGen runs as the two points — a second slice.
3. **Credential model.** Per-person read-only credential (the table-preview rule) vs one service credential stored
   by Admin, since a TestGen metadata DB is itself a system store, not a business database. Architect's pick: per
   person, same as everything else, until the owner rules otherwise.
4. **Scale.** `profile_results` for a large deployment is millions of rows; the first read should be one resource at
   a time from Scouting, never a whole-deployment import, and the step should page by table group.

## 7. DDL

None for slice 1: `source`, `ran_as`, `surveyed_at`, `rule_source` already exist on the three tables, and a TestGen
registration can live in the existing credentials store under a new kind. Anomalies (§6.1 option a) would need a table.

## 8. Slices and gate

- **TG1** Admin registration + connection test + matching list (read-only; proves the role and the match).
- **TG2** `testgen_profile_read` + Value profile header/source select + Q-0079 step line. Gate: an e2e test against
  a TestGen schema fixture (dbsetup SQL applied to a throwaway Postgres, a few profile_results rows) asserting the
  batch, the source words, two dates, one not-applicable measure rendered as absence; the Architect's live read on a
  real deployment the owner names.
- **TG3** `testgen_results_read` (replaces the export-file path; `testgen_import.py` keeps `build_rows` and gains a
  live reader) + Q-0097 words "run by TestGen".
- **TG4** anomalies, if §6.1 says so.
Each slice: no TestGen write (asserted by the role), US spelling, status words from proof rows, attribution words per
the 2026-10-30 ruling ("implements / inspired by TestGen" in detailed descriptions only).

## 9. Owner rulings on §6 (architect chat, 2026-10-30)

Owner's words: "skip anomalies for now, per person credential, latest run".

| § | Ruling | Effect on the note |
|---|---|---|
| 6.1 anomalies | **skip for now** | `testgen_anomaly_read` and slice TG4 are deferred; `profile_anomaly_results` is not read; no findings table |
| 6.2 which run | **latest run** | `testgen_profile_read` and `testgen_results_read` take the latest `profiling_runs` / `test_runs` per table group; history is not read; the change-rate idea waits for a later ruling |
| 6.3 credential | **per person** | the TestGen metadata-database credential is the signed-in person's own, stored per person as table preview and value profile do; no Admin service credential |
| 6.4 scale | (no ruling needed) | stays: one resource at a time from Scouting, paged by table group |

Slices are therefore TG1 (Admin registration + match), TG2 (profile read + Value profile source), TG3 (results read + Q-0097 words). No DDL.
