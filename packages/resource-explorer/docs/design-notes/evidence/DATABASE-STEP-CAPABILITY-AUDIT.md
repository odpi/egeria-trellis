# Database step capability audit — catalog / read / stats / write, traced to actual SQL

## Correction (live-verified by the coordinating session, 2026-09-24/25)

**This audit's `stats` classification for `pg_stat_user_tables`/`pg_stat_
user_indexes` was wrong, and everything below that relied on it has been
corrected in place rather than rewritten from scratch, so the "worth a
second look" section's original numbering stays intact for anyone diffing
against history.** The premise — "`stats` = needs `pg_monitor` membership or
ownership" applied to the per-table/per-index activity views — does not
hold. Those views carry no ACL predicate at all.

**Empirically verified**, connected as `egeria_user` against `coco_pharma`
(confirmed not a `pg_monitor` member: `pg_has_role('egeria_user',
'pg_monitor', 'member')` → `false`), via `SET ROLE egeria_user` from a
superuser session on the shared dev Postgres (`egeria-shared-postgres`,
port 5442):

| View | Visible to `egeria_user` (non-`pg_monitor`)? | Tier |
|---|---|---|
| `pg_stat_user_tables` | Yes — all 58 rows, exactly matching an independent `pg_class`/`pg_namespace` count, with real non-null `n_tup_ins`/`last_vacuum` even for schemas (`demo`, `demo_auth`) `egeria_user` has no `USAGE` grant on | **catalog** (not `stats`) |
| `pg_stat_user_indexes` | Yes — all 28 rows, same unfiltered behaviour | **catalog** (not `stats`) |
| `pg_stat_database` | Yes — full row for `coco_pharma` (`numbackends`, `xact_commit`, …), identical to the superuser's read | **catalog** (not `stats`) |
| `pg_stat_archiver` | Yes — full single-row global counters, identical to superuser | **catalog** (not `stats`) |
| `pg_stat_bgwriter` | Yes — full single-row global counters, identical to superuser | **catalog** (not `stats`) |
| `pg_stat_wal` | Yes — full single-row global counters, identical to superuser | **catalog** (not `stats`) |
| `pg_stat_activity` | **Masked** — another session's `query` and `state` columns came back `<insufficient privilege>`/null for a non-`pg_monitor` role; only `pid`/`usename`/`application_name` were visible | **stats**, confirmed |
| `pg_stat_replication` | Not independently testable in this environment (0 rows — no standby attached to the dev instance), but built on the same `pg_stat_get_activity()`/per-session masking mechanism `pg_stat_activity` uses, per Postgres's own view definitions | **stats**, by mechanism (not directly reproduced here) |
| `pg_stat_statements` | Not testable — extension not installed on this instance | **stats**, per Postgres documentation (query text/per-query stats need `pg_read_all_stats`/`pg_monitor`), not independently re-verified here |
| `pg_stats` (column statistics) | **Partially filtered** — 441 of 481 rows visible to `egeria_user`, narrower than the superuser's 481; confirmed the missing rows are columns on tables (e.g. `demo_auth.users`) `egeria_user` cannot `SELECT` | **read** (unchanged — genuinely column-`SELECT`-gated, not `pg_monitor`-gated) |

**The corrected model:** `pg_monitor`/`pg_read_all_stats` gate visibility
into *other sessions'/connections'* activity (`pg_stat_activity` query
text/state for rows that are not your own, `pg_stat_replication`, and —
per documentation, not reproduced here — `pg_stat_statements`). They do
**not** gate per-object/per-database counters (`pg_stat_user_tables`,
`pg_stat_user_indexes`, `pg_stat_database`, `pg_stat_archiver`, `pg_stat_
bgwriter`, `pg_stat_wal`), which behave exactly like `pg_namespace`/`pg_
class` — visible to any role that can connect. `pg_stats` keeps its
existing `read` classification, unaffected by any of this — it was never
part of the `pg_monitor` claim; it is gated by column-level `SELECT`, and
that gating was re-confirmed live above.

**What changed as a result:**
- `postgres_schema_and_stats` (`survey_definition_adapter.py`): `requires_
  capability` corrected from `stats` to `read` — its declared output never
  depended on a `pg_monitor`-gated view; the strongest tier it still touches
  is `read` (`information_schema.*` enumeration, `pg_stats` column profile).
- `postgres_operations`: `db_activity_signals` (`pg_stat_user_tables`, `pg_
  stat_database`) is reclassified from `stats` to `catalog`. `db_resilience`
  keeps `stats`, now for one reason instead of two: it reads `pg_stat_
  replication`, which the correction above confirms is still genuinely
  gated; its other three queries (`pg_is_in_recovery()`, `SHOW archive_
  mode`, `pg_stat_archiver`, `pg_extension`) are `catalog`, consistent with
  what §2 already said about them individually. The step-level declaration
  for `postgres_operations` stays `stats` because `db_resilience` alone is
  now the sole reason, not because two of four sub-analyses need it.
- `credential_capability.py`'s `STATS` branch and module docstring, and
  `db_derived.py`'s `COVERAGE_ANALYZE_REMEDY` (which wrongly called
  `postgres_schema_and_stats` "a pg_monitor-class credential" requirement —
  the coverage gap it describes is actually `pg_stats`, which is `read`-tier)
  were fixed to match.

The vocabulary table, §1, §2, the summary table and "Worth a second look"
below are left as originally written except where corrected inline, so the
audit trail of what was believed and what was found stays legible.

**For:** the `requires_capability` build, once the project owner's ruling on
the credential-model question lands (`docs/Backlog.md`, "Database
credential-capability model — awaiting the project owner's ruling").
**Date:** 2026-09-24 · **Read against:** `main` at `1c0ad31f`.
**Scope:** every entry in `DATABASE_STEP_REGISTRY`
(`resource_explorer/surveyors/database/survey_definition_adapter.py`), every
database-facing method in `resource_explorer/surveyors/database/connection.py`,
and the calling code in `database_surveyor.py` that wires them together.
**Not in scope:** no `requires_capability` field was added to `StepInfo`, no
gating/execution logic changed, `EngineCapabilities` untouched. This is a
factual trace, not a design decision — that decision belongs to
`REPLY-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md` §3 (on the
`re/reply-database-credential-capability` branch — see the note in §0 below)
and to whatever the project owner rules on the bigger credential-model
question.

**Vocabulary** (as proposed in the reply doc, reproduced here for reference,
not re-derived):

| Value | Means | Postgres check |
|---|---|---|
| `catalog` | system-catalog reads only | visible to any connected role regardless of grants |
| `read` | needs `SELECT` on target tables | Postgres privilege-filters the view/query by the querying role's actual grants |
| `stats` | needs `pg_monitor` membership for visibility into OTHER sessions/connections | `pg_stat_activity` (other rows' query/state), `pg_stat_replication`, `pg_stat_statements` — **NOT** `pg_stat_user_tables`/`pg_stat_user_indexes`/`pg_stat_database`/`pg_stat_archiver`/`pg_stat_bgwriter`/`pg_stat_wal`, which are unfiltered (corrected 2026-09-24/25 — see "Correction" above; this row originally said the per-table/per-database counters needed it too) |
| `write` | never exercised by a survey, only probed | `has_table_privilege(..., 'INSERT')`, checked, never executed |

---

## 0 · A note on where this vocabulary actually lives

The task that produced this vocabulary (`REPLY-DATABASE-CREDENTIAL-
CAPABILITY-VISIBILITY.md`, replying to `ASK-DATABASE-CREDENTIAL-CAPABILITY-
VISIBILITY.md`, #251) is **not on `main`** as of this audit — it lives on
`re/reply-database-credential-capability` (commit `ec189d60`), which has not
been merged. What *did* land on `main` (`#252`, `#253`) is the `credential_
capability` probe itself, the visibility banner, the third envelope state,
and an RFA — "Piece 1" of the ask, which the reply doc says explicitly does
not wait on a ruling. The `requires_capability`/gating piece ("Piece 2")
is still open on both counts: the branch with its design is unmerged, and
the ruling on the bigger multi-credential question is separately pending.
This audit was written against the reply doc's content directly (read via
`git show ec189d60:...`) since that is where the vocabulary is defined; it
does not assume the doc's merge status changes anything about the SQL
traced below.

---

## 1 · `postgres_schema_and_stats` — mixed, and more mixed than its name suggests

**Registry entry:** produces `database_schemas`, `database_tables`,
`database_columns`; `fetch_cost: api`, `compute_cost: low`.

**What it actually runs.** `_run_postgres_schema_and_stats` constructs a
`DatabaseSurveyor` and calls `.survey()` with **no `steps` argument**, which
resolves to `_ALL_STEPS = ("schema", "statistics", "views")`
(`database_surveyor.py:266`, `:321`). So despite the registry description
("Schema, table and column inventory plus row-count/size statistics"), the
code path this step actually exercises is the full three-step default
survey — schema, statistics, *and* views — not just the two the name
implies. `sql_analysis` (§5 below) runs the identical default and differs
only in which keys of the same result dict it returns.

| Sub-piece | Source | Postgres objects | Tier | Why |
|---|---|---|---|---|
| Schema/table/column enumeration (`get_schema_info`, `_get_tables_for_schema`) | `connection.py:176-328` | `information_schema.schemata`, `information_schema.tables`, `.columns`, `.table_constraints`, `.key_column_usage`, `.constraint_column_usage` | **read** | `information_schema` views are privilege-filtered by the querying role's actual grants — this is exactly the mechanism the incident that prompted the reply doc traced live: `egeria_user` saw 6 of 8 real schemas via `information_schema.schemata` |
| Schema descriptions (`_get_schema_descriptions`) | `connection.py:202-214` | `pg_namespace`, `obj_description()` | **catalog** | `pg_namespace` is catalog metadata, readable by any connected role regardless of grants |
| Table/column comments (`obj_description`/`col_description` in `_get_tables_for_schema`'s main query) | `connection.py:262-278` | `(schema.table)::regclass` cast into `pg_class`/`pg_attribute` via the comment functions | **catalog** | same functions design rule 13 already documents as needing the qualified-regclass form; the comment catalog itself (`pg_description`) is not grant-filtered |
| Database size (`_get_database_size`) | `connection.py:907-915` | `pg_database_size()`, `pg_size_pretty()` | **catalog** | a function call over catalog metadata (database size), not a per-table grant check; requires only `CONNECT` on the database, which the credential already has by virtue of connecting |
| Per-table byte size (`_get_table_statistics`) | `connection.py:917-931` | `pg_tables`, filtered `... AND has_schema_privilege(schemaname, 'USAGE')` | **catalog, self-filtered** | `pg_tables` itself is unfiltered catalog metadata (like `pg_class`); the query *chooses* to narrow it with an explicit `has_schema_privilege` predicate rather than Postgres filtering it for free. Worth flagging on its own — see §6 |
| Row-count/last-activity for display (`_get_table_row_stats`) | `connection.py:933-961` | `pg_stat_user_tables` (`n_live_tup`, `last_analyze`/`last_autoanalyze`, `last_vacuum`/`last_autovacuum`, `n_mod_since_analyze`) | **catalog** *(corrected 2026-09-24/25 — was `stats`; live-verified unfiltered, see "Correction" above)* | no ACL predicate at all — visible to any connected role, same as `pg_class` |
| Column profile (`get_column_stats`, feeds `_survey_extended_statistics`'s `column_profile_rows`) | `connection.py:388-429` | `pg_stats` (joined to `pg_class.reltuples` for the row-count-at-analyze-time) | **read** | `pg_stats` is defined `WHERE has_column_privilege(...)` — it is genuinely privilege-filtered by the querying role's grants, not just by convention |
| Table activity rows (`get_table_activity`, feeds `_survey_extended_statistics`'s `table_activity_rows`) | `connection.py:431-477` | `pg_stat_user_tables` (full column set: tuple counters, scan counts, vacuum/analyze recency) | **catalog** *(corrected 2026-09-24/25 — was `stats`)* | same unfiltered view as the row-stats read above, fuller projection |
| Index usage (`get_index_stats`, part of `_survey_extended_statistics`) | `connection.py:496-519` | `pg_stat_user_indexes` joined to `pg_index` | **catalog** *(corrected 2026-09-24/25 — was `stats`; live-verified 28 of 28 rows visible to `egeria_user`)* | no ACL predicate — same unfiltered family as `pg_stat_user_tables` |
| Stats-reset timestamp (`get_stats_reset`) | `connection.py:479-494` | `pg_stat_database` | **catalog** *(this row's original "catalog-like, but not the strict stats gate" hedge is now resolved — live-verified fully visible to `egeria_user`, identical to superuser)* | `pg_stat_database` is a database-wide, non-per-object view with no per-user sensitive content, and — per the 2026-09-24/25 correction — the `stats` tier's `pg_monitor` gate never applied to it at all; the gate is about OTHER SESSIONS' activity (`pg_stat_activity`/`pg_stat_replication`), not per-database counters |
| SQL view analysis (`_survey_views`) | `database_surveyor.py:1616-1633` | `information_schema.views` | **read** | privilege-filtered the same way `information_schema.tables` is |

**Conclusion for this step as a whole:** it is not one tier. Its
declared-purpose core (schema/table/column enumeration) is **read**-tier
per the incident's own live trace. But the same step, as actually coded
via the default `survey()` call, also touches **catalog**-tier data
(schema descriptions, comments, database/table size) and **stats**-tier
data (row activity, column profile via `pg_stats`, index usage) in the
same fetch. A `requires_capability` field on this `StepInfo` would have to
either declare the strictest tier it touches (`stats`, since that's the
weakest link a credential could fail) or split the step — the design reply
doc doesn't propose the split, and this audit doesn't either; it only
documents that "one step, one tier" does not hold here without a decision
about which failure mode the field is meant to describe.

**The one place this step sits stricter than the data requires — flagged
per the task brief, not fixed here:** the schema/table/column enumeration
goes through `information_schema.*` (**read**-tier) even though the same
structural facts — table/column existence, names, types — are also
obtainable from `pg_class`/`pg_attribute`/`pg_namespace` at **catalog**-tier,
and row-count *estimates* (not exact counts) are obtainable from
`pg_class.reltuples` at **catalog**-tier too (the very column `get_column_
stats` already joins in for a different purpose, at `connection.py:419`).
This is the gap a parallel task is understood to be hardening with a
catalog-only fallback; this audit names it as fact for that task to build
against, without proposing the fix itself.

---

## 2 · `postgres_operations` — a genuine four-way bundle

**Registry entry:** "privilege_audit, db_activity_signals, db_resilience,
db_external_dependencies"; produces `database_grants`; `fetch_cost: api`,
`compute_cost: low`.

Backed by `_survey_operations` (`database_surveyor.py:872-916`), which is
explicitly structured as four independently capability-gated
sub-analyses. Each is documented per-piece below rather than given one
blended tier, per the task brief's instruction.

| Sub-analysis | Source | Postgres objects | Tier | Why |
|---|---|---|---|---|
| `privilege_audit` | `get_privilege_audit`, `connection.py:532-608` | `pg_roles`; `pg_class.relacl` via `aclexplode()`, joined to `pg_namespace`; `pg_default_acl` joined to `pg_namespace` | **catalog** | the method's own comment (`connection.py:569-573`) states this explicitly and gives the reason: `pg_class` ACLs are catalog metadata, visible to any connected role regardless of that role's own grants — confirmed live against a real PUBLIC grant on `coco_ods`, where `information_schema.role_table_grants` (which the code deliberately does NOT use) would have hidden it |
| `db_activity_signals` | `get_table_activity` + `get_stats_reset`, `connection.py:431-494`, called from `_survey_operations:895-899` | `pg_stat_user_tables`, `pg_stat_database` | **catalog** *(corrected 2026-09-24/25 — was `stats`)* | both views are live-verified unfiltered (see "Correction" above) — no ACL predicate, visible to any connected role. `capabilities.tuple_counters` still gates the sub-analysis on whether the engine supports it at all, not on `pg_monitor` |
| `db_resilience` | `get_replication_status` + `get_wal_archiving_status` + `get_backup_tool_signals` + `get_clustering_info`, `connection.py:708-828` | `pg_is_in_recovery()`, `pg_stat_replication`; `SHOW archive_mode`, `pg_stat_archiver`; `pg_extension` (backup-tool markers); `pg_extension` (Citus) | **stats, with a catalog-tier tail** *(narrowed 2026-09-24/25: `pg_stat_archiver` is now confirmed `catalog`, not `stats` — see "Correction" above; `pg_stat_replication` is the only genuinely `pg_monitor`-gated query left in this bundle)* | `pg_is_in_recovery()` and `SHOW archive_mode` are function/setting reads any connected role can make; `pg_stat_archiver` is live-verified unfiltered (full single-row global counters, identical to superuser); `pg_extension` (used twice, for backup-tool detection and Citus detection) is ordinary catalog metadata. `pg_stat_replication` is the one query in this sub-analysis that is genuinely gated — built on the same per-session masking mechanism confirmed live against `pg_stat_activity` (not independently reproduced for `pg_stat_replication` itself, since this dev instance has no standby attached). The sub-analysis is gated as one unit on `capabilities.resilience`, so the weakest-link tier for the bundle is still `stats`, now because of one query instead of two |
| `db_external_dependencies` | `get_external_dependencies`, `connection.py:830-892` | `pg_extension`; `pg_foreign_server`/`pg_foreign_data_wrapper`; `pg_foreign_table`/`pg_class`/`pg_namespace`; `pg_publication`; `pg_subscription` | **catalog** | every object queried is catalog metadata with no grant-based row filtering. One caveat the method's own comment (`connection.py:872-880`) already flags: `pg_subscription` is visible only to a superuser or the subscription-owning role on the *subscriber* database — a permission failure there degrades silently to an empty list, indistinguishable from "no subscriptions," which is a real absence-collapse the method's comment names but does not fix. That's a `find-absence-as-answer` gap independent of the capability-tier question, not something this audit's scope covers fixing |

**Conclusion:** `postgres_operations` is a real four-way bundle exactly as
the task brief anticipated. **Corrected 2026-09-24/25:** the tiers land
three-and-one, not two-and-two as originally written here — `privilege_
audit`, `db_external_dependencies` **and `db_activity_signals`** are all
`catalog`; only `db_resilience` is `stats` (and only because of its
`pg_stat_replication` read — its other three queries are also `catalog`).

---

## 3 · `credential_capability` — catalog-tier by design, and confirmed live

**Registry entry:** "Read-only catalog/privilege introspection: what this
credential can see and do"; `fetch_cost: api`, `compute_cost: low`.

Backed by `get_credential_capability` (`connection.py:610-706`), gated on
`capabilities.credential_introspection`.

| Query | Postgres objects | Tier | Why |
|---|---|---|---|
| `current_user` | none (session variable) | catalog | always available |
| Schema visibility | `pg_namespace` + `has_schema_privilege(current_user, ..., 'USAGE')` | catalog | `pg_namespace` unfiltered; `has_schema_privilege` is a privilege-*check* function, callable by anyone about any role/object, not a privilege-*filtered view* |
| Table select/insert visibility | `pg_class` + `pg_namespace` + `has_table_privilege(..., 'SELECT')` + `has_table_privilege(..., 'INSERT')` | catalog | same reasoning — `pg_class` unfiltered, the `has_table_privilege` calls are checks, not filters |
| Stats-role membership | `pg_has_role(current_user, 'pg_monitor', 'MEMBER')` | catalog | a role-membership check function, callable by any role about itself |

**This is the step whose whole point is to measure the boundary between
`catalog` and the other tiers**, using only `catalog`-tier reads to do it —
the method's own docstring calls this out ("Every read is catalog metadata
or a privilege-CHECK function call"). The write probe
(`has_table_privilege(..., 'INSERT')`) is checked for every visible table
and never exercised — this is the concrete code backing the `write`
value's definition in the vocabulary table above: probed, never run.

**Independently confirmed, not just documented.** The `ASK-`/`REPLY-`
design pair this step was built from records a live verification against a
real Postgres database (`coco_pharma`, connected as the exact credential
RE uses, `egeria_user`): `pg_namespace` and `pg_class` returned the full,
unfiltered object counts while `information_schema.schemata` and `has_
table_privilege` showed the same credential's narrower, grant-filtered
view of the same objects — the two-tier split (catalog vs. read) this
audit relies on for every other step's classification is not an assumption
made from Postgres documentation alone; it was reproduced against a real
database in this codebase's own history.

---

## 4 · `db_derived` — no connection, no tier

**Registry entry:** "Zero-fetch derivation over already-stored rows";
`fetch_cost: none`, `compute_cost: low`.

`_run_db_derived` (`survey_definition_adapter.py:96-113`) never
constructs a `DatabaseSurveyor` and never opens a connection — its own
docstring makes this explicit ("credentials are not merely unused here —
there is nothing to give them to"). It reads only rows RE has already
stored in its own registry (`db_classification`, `db_relationship_graph`,
`grain_determination`, `db_fingerprint`, `schema_conventions`, `db_
change_rates`, `schema_diff`, `grant_change`, a proposed `DataScope`).

**This step has no capability tier at all**, not `catalog` (the weakest
tier still implies a live connection to *some* database). Any `requires_
capability` field would need a fifth value, or an explicit "none/n/a," to
avoid mis-stating this step as needing even the cheapest live check — a
step that opens no connection cannot be said to need any of the four
values, and forcing it into `catalog` would be a small instance of the same
collapse the credential-capability work exists to prevent elsewhere.

---

## 5 · `postgres_column_profile` — the only steps that read real table data

**Registry entry:** "Bounded value sampling, data_class_match,
reference_data_match"; `fetch_cost: api_heavy`, `compute_cost: medium`.

`_run_postgres_column_profile` calls `survey(steps=["column_profile"])`,
which (per `database_surveyor.py:332-333`) also pulls in `"statistics"`
because the sampling provenance needs row-count context, plus `"schema"`
unconditionally. So this step's actual fetch is schema (§1's mixed
catalog/read tiers) + statistics (§1's mixed catalog/read/stats tiers) +
the column sample itself.

The sample is built by `sampling.py`'s `build_column_sample_sql` and
issued via `column_profile_step.py:231`'s `conn.execute_query(query.sql)`
— literal `SELECT <col> FROM <table> [TABLESAMPLE SYSTEM|BERNOULLI (...)
REPEATABLE (...)] [WHERE ...] [LIMIT ...]` against the actual table.

| Sub-piece | Tier | Why |
|---|---|---|
| Value sampling itself | **read** | a direct `SELECT` against a user table needs `SELECT` on that exact table — the strongest form of `read`, not merely a privilege-filtered system view |
| Row-count-at-analyze context (from `statistics`) | mixed, see §1 | riding along because the step requests `"statistics"` |
| Schema/column catalog (from `schema`) | mixed, see §1 | riding along because `"schema"` always runs |

**Conclusion:** the step's declared purpose (bounded sampling, data-class
and reference-data matching) is unambiguously `read`-tier — it is
literally impossible without `SELECT` on the target table, unlike every
other step audited here, which can at worst be *misclassified* as
stricter than necessary. This is the one step where `read` is not just the
current implementation's choice but the actual floor.

---

## 6 · `postgres_nested_columns` — same floor as column_profile, same reason

**Registry entry:** "Bounded sampling and nested-schema inference for
JSON/JSONB/XML columns"; `fetch_cost: api_heavy`, `compute_cost: medium`.

Its own docstring says it "reuses `postgres_column_profile`'s exact
sampling machinery" — confirmed: `nested_columns_step.py` builds no SQL of
its own beyond what `sampling.py`/`column_profile_step.py` already issue;
it finds candidate JSON/XML columns from the stored schema catalog
(`_iter_nested_columns`, reading `schema_info`, not the database) and then
samples their real values the same way `postgres_column_profile` does.

**Tier: read**, for the same reason as §5 — real value sampling needs
`SELECT` on the target table. Same schema/statistics ride-along as §5.

---

## 7 · `sql_analysis` — read-tier by declared purpose, but runs the full default survey underneath

**Registry entry:** "SQL view dependencies, column-level lineage and
complexity scores"; `fetch_cost: api`, `compute_cost: low`.

`_run_postgres_sql_analysis` calls `.survey()` with **no `steps`
argument** — the exact same call `_run_postgres_schema_and_stats` makes
(§1). It runs schema + statistics + views and returns `schema_info`,
`statistics`, and `views` — but the registry only claims this step
produces SQL/view analysis. In other words: **this step's code path is
identical to `postgres_schema_and_stats`'s**, differing only in which keys
of the shared result dict the two adapter functions happen to extract.

| Sub-piece | Tier | Why |
|---|---|---|
| View definitions + parsing (`_survey_views` → `SqlAnalyzer`) | **read** | `information_schema.views` is privilege-filtered the same way `information_schema.tables` is (§1) |
| Schema/statistics (riding along, unused by this step's own output) | mixed, see §1 | the same full default survey `postgres_schema_and_stats` runs |

**Conclusion:** the step's own declared purpose is `read`-tier
(view definitions are only visible for views the connecting role can
access), which matches the task brief's expectation. But because it shares
`DatabaseSurveyor.survey()`'s no-argument default with `postgres_schema_
and_stats` rather than requesting only `["views"]`, it also touches every
`stats`-tier query `_survey_extended_statistics` runs (`pg_stat_user_
tables`, `pg_stat_user_indexes`) even though nothing in `sql_analysis`'s
own output uses that data. A `requires_capability: read` on this step
would be honest about what the step *needs*, but would understate what the
step's current code *actually does* against the database — see "worth a
second look" below.

---

## Summary table

| Step / sub-analysis | Postgres sources | Tier | Notes |
|---|---|---|---|
| `postgres_schema_and_stats` — table/column enumeration | `information_schema.{schemata,tables,columns,table_constraints,key_column_usage,constraint_column_usage}` | read | privilege-filtered live-verified |
| `postgres_schema_and_stats` — schema descriptions | `pg_namespace` | catalog | unfiltered |
| `postgres_schema_and_stats` — table/column comments | `pg_description` via `regclass` | catalog | unfiltered |
| `postgres_schema_and_stats` — database size | `pg_database_size()` | catalog | function call |
| `postgres_schema_and_stats` — per-table size | `pg_tables` + self-filter | catalog (self-filtered) | see §6 below |
| `postgres_schema_and_stats` — row/last-activity display | `pg_stat_user_tables` | catalog *(was stats, corrected)* | live-verified unfiltered |
| `postgres_schema_and_stats` — column profile | `pg_stats` | read | privilege-filtered view (re-verified live: 441/481 rows) |
| `postgres_schema_and_stats` — table activity rows | `pg_stat_user_tables` | catalog *(was stats, corrected)* | live-verified unfiltered |
| `postgres_schema_and_stats` — index usage | `pg_stat_user_indexes` | catalog *(was stats, corrected)* | live-verified unfiltered, 28/28 rows |
| `postgres_schema_and_stats` — stats-reset timestamp | `pg_stat_database` | catalog | live-verified unfiltered (was hedged "not gated" — now confirmed) |
| `postgres_schema_and_stats` — view SQL (from default survey) | `information_schema.views` | read | privilege-filtered |
| `postgres_operations` — privilege_audit | `pg_roles`, `pg_class.relacl`, `pg_default_acl` | catalog | confirmed by in-code comment |
| `postgres_operations` — db_activity_signals | `pg_stat_user_tables`, `pg_stat_database` | catalog *(was stats, corrected)* | live-verified unfiltered |
| `postgres_operations` — db_resilience | `pg_stat_replication`, `pg_stat_archiver`, `pg_is_in_recovery()`, `SHOW archive_mode`, `pg_extension` | stats (bundle, now for one query), catalog-tail | only `pg_stat_replication` is genuinely gated; `pg_stat_archiver` is now confirmed catalog too — three of four queries are individually catalog |
| `postgres_operations` — db_external_dependencies | `pg_extension`, `pg_foreign_server`, `pg_foreign_table`, `pg_publication`, `pg_subscription` | catalog | `pg_subscription` visibility caveat noted |
| `credential_capability` | `pg_namespace`, `pg_class`, `has_schema_privilege`, `has_table_privilege`, `pg_has_role` | catalog | live-verified against `coco_pharma` |
| `db_derived` | none — no connection | n/a | no tier applies |
| `postgres_column_profile` | real table `SELECT`/`TABLESAMPLE` + schema/statistics ride-along | read | the floor, not a choice |
| `postgres_nested_columns` | same sampling machinery as column_profile | read | same floor |
| `sql_analysis` | `information_schema.views` + schema/statistics ride-along | read (declared), stats (actual reach) | identical code path to postgres_schema_and_stats |

**Tier breakdown across the 19 rows above** (counting each sub-analysis
once, `db_derived` counted separately as "no tier"). **Corrected
2026-09-24/25** — four rows moved from `stats` to `catalog`, so the counts
below no longer match the original write-up; see "Correction" above for why:

- `catalog`: 13 (schema descriptions, comments, database size, per-table
  size, stats-reset timestamp, privilege_audit, db_external_dependencies,
  credential_capability, plus db_resilience's catalog-tail queries folded
  into its own row rather than double-counted — **plus, corrected, row/
  last-activity display, table activity rows, index usage, and
  db_activity_signals**, all live-verified unfiltered)
- `read`: 6 (table/column enumeration, column profile via pg_stats, view
  SQL twice over — schema_and_stats and sql_analysis — column_profile,
  nested_columns)
- `stats`: 0 standalone rows now — only `db_resilience` as a stats-gated
  bundle remains, and only because of its `pg_stat_replication` read
- `write`: 0 — confirmed nowhere in the database step family is a write
  ever exercised; the only `write`-shaped code is `credential_capability`'s
  `has_table_privilege(..., 'INSERT')` **probe**, which the vocabulary's
  own definition says is exactly what `write` should be: checked, never
  run
- `n/a` (no connection): 1 (`db_derived`)

These counts are per distinct query/sub-analysis, not per `StepInfo`
registry entry — several registry entries (`postgres_schema_and_stats`,
`postgres_operations`, `sql_analysis`) bundle multiple tiers under one
step id, which is the central finding of this audit (see the next
section).

---

## Worth a second look

1. **`postgres_schema_and_stats` and `sql_analysis` run byte-for-byte the
   same default survey.** Both adapter functions call `DatabaseSurveyor.
   survey()` with no `steps` argument, which resolves to the same
   `_ALL_STEPS = ("schema", "statistics", "views")`. The two registry
   entries differ only in which keys of the identical result dict they
   extract. This means `sql_analysis`, whose declared job is read-tier
   view analysis, silently pays for and is exposed to every `stats`-tier
   query `postgres_schema_and_stats` runs (`pg_stat_user_tables`, `pg_stat_
   user_indexes`) — not because it needs that data, but because nothing
   scopes its `survey()` call to `steps=["views"]`. If `requires_
   capability` becomes a real gate, this is a step that would either need
   its own narrower `survey()` call (a code change, not a doc problem) or
   would have to declare `stats` for a step whose own output never uses
   stats data — the same over-strict-vs-actual-code question flagged for
   `postgres_schema_and_stats` in §1, but here the mismatch runs the other
   direction: the *output* is `read`-tier but the *code path* reaches
   `stats`-tier.

2. **`_get_table_statistics`'s self-filtering is a third pattern, distinct
   from both the plain-catalog and plain-privilege-filtered cases.** Most
   catalog-tier reads in this audit (`pg_namespace`, `pg_class`, `pg_
   roles`) are unfiltered by Postgres and read as-is. Most read-tier reads
   (`information_schema.*`, `pg_stats`) are filtered *by Postgres itself*.
   `_get_table_statistics` (`connection.py:917-931`) is neither: it reads
   the unfiltered `pg_tables` catalog view and then adds its own `WHERE
   has_schema_privilege(schemaname, 'USAGE')` predicate in application SQL.
   The result looks read-tier (only tables the credential can reach come
   back) but the underlying view is catalog-tier — a future gate that
   inspects "which system view does this query touch" rather than "what
   does the query actually filter to" would misclassify this one in either
   direction depending on which signal it trusts.

3. **RESOLVED 2026-09-24/25 — was: "`pg_stat_database` doesn't fit either
   `catalog` or `stats` cleanly."** The live verification above settles this
   rather than just deciding it by convention: `pg_stat_database` (and, it
   turns out, `pg_stat_user_tables`/`pg_stat_user_indexes`/`pg_stat_archiver`/
   `pg_stat_bgwriter`/`pg_stat_wal`) are unfiltered — the `stats` tier's
   `pg_monitor` gate was never about "looks like a `pg_stat_*` view", it is
   specifically about visibility into OTHER sessions' activity
   (`pg_stat_activity`, `pg_stat_replication`). `pg_stat_database` is
   `catalog`, on the same footing as `pg_class`, not an edge case the
   four-value vocabulary lacks a slot for.

4. **`db_external_dependencies`'s `pg_subscription` read has its own
   silent absence-collapse**, independent of capability tiering: a
   permission failure and "genuinely no subscriptions" both come back as
   an empty list. The method's own comment already names this
   (`connection.py:872-880`) as a known simplification defended by the
   *step-level* capability gate rather than fixed at the query level. Not
   this audit's job to fix, but worth flagging since it's exactly the
   `find-absence-as-answer` shape the credential-capability work elsewhere
   in this codebase exists to close.

5. **The reply doc this vocabulary comes from is not merged to `main`.**
   Anyone picking up the `requires_capability` build should first check
   whether `re/reply-database-credential-capability` (`ec189d60`) has
   landed, and if not, treat this audit — not that unmerged file — as the
   traceable source for what SQL backs each tier claim, since the doc
   itself may still change before merge.
