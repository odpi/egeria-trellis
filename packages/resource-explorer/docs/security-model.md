# Security model — identities, capabilities and credentials for surveying data resources

**Status:** consolidated model, current as of 2026-09-25. This is the single
place the strategy is stated; the design notes it draws on
(`design-notes/REPLY-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md`,
`design-notes/DATABASE-ENGINE-CATALOG-CAPABILITY-SURVEY.md`,
`design-notes/DATABASE-STEP-CAPABILITY-AUDIT.md`,
`design-notes/RESOURCE-REACHABILITY-IMPLEMENTED.md`) are the history and the
evidence, and this document supersedes them where they differ.

**Decision (project owner, 2026-09-25):** database access is modelled as
**two identities**, a catalog identity and a data identity, not one
credential with more or fewer grants. This document is that decision worked
through: what each identity holds per engine, how a step declares what it
needs, where credentials live in Egeria and in Resource Explorer, how a
credential is chosen for a run, and what the user is told.

**Scope.** Access *by Resource Explorer to the resources it surveys*:
databases first, filesystems and portals where the same shape applies. Access
*to Resource Explorer itself* (sign-in through Egeria, session tokens,
governance zones for what RE publishes) is unchanged and documented in
`admin-guide.md` under *Authentication* and *Governance zones*.

---

## 0 · What is built and what is designed

Honest state on 2026-09-25, so nobody reads a plan as a feature.

| Piece | State | Where |
|---|---|---|
| Capability probe: what the connected credential can see, with an exact denominator | **built** (#253, #257) | `surveyors/credential_capability.py`; step `credential_capability` |
| "Measured within credential scope" envelope state and banner | **built** (#257) | `surveyors/result_status.py`, `web/routes/databases.py` |
| `requires_capability` on database steps; the launcher gate reads the stored probe | **built** (#262, corrected 2026-09-25) | `surveyors/database/survey_definition_adapter.py`, `surveyors/prerequisite_resolver.py` |
| Database password encrypted at rest in the registry | **built** | `credential_crypto.py` (`RE_DB_CREDENTIAL_KEY`) |
| Credential projected into RE's own `.omsecrets` file for Egeria's engine host | **built** (#185, #257) | `omsecrets_store.py`, `EGERIA_SECRETS_STORE_LOCAL_PATH` |
| Reachability probe (`CHECK_ASSET`-only engine action) and outcome vocabulary | **built** for filesystems (slice 13) | `reachability.py` |
| Two identities on one asset: two Connections, labelled `catalog` / `data`, per-role secrets collections | **designed**, awaiting the §5 model change | credentials reply §1, §9 |
| Steps binding to an identity rather than a capability; the gate asking for the data identity | **designed** | §3 below |
| `database_credentials` index; retirement of the single `db_user`/`db_password` pair | **designed** | §4 below |
| Per-engine declaration (`structural_floor`, privilege checks, containment levels) | **designed**; Postgres is the only engine implemented | §2, §3 |
| Estimate freshness stamps and the recorded-at-write basis | **designed** (#283) | `multi-resource-questions-design.md` §5.1a |
| Two upstream Egeria defects in connection selection | **logged**, not yet filed | §5 |

---

## 1 · Principles

1. **Least privilege, by identity.** A survey identity holds the minimum
   for its tier and nothing more. Broader access is a *different identity*
   with a different owner, not a bigger grant on the same one.
2. **Absence has a reason, and a denominator where one exists.** A
   credential that sees 3 of 26 tables reports "3 of 26", never "3". Where
   the total cannot be known (§3.4), the report says "total not
   established", never a fraction.
3. **Estimated, measured, recorded at write.** Every number carries its
   basis. Catalog statistics are estimates as of the last utility run and
   are stamped with their freshness; pushdown results are measured;
   write-time metadata (Parquet footers, Delta and Iceberg logs, warehouse
   storage metadata, OpenLineage output facets) is exact as of the write.
4. **No trial writes, ever.** Write capability is inferred from grants and
   never exercised. A "test connection" button opens a read-only session.
5. **No clear-text secrets.** RE stores a secret only if RE created it,
   encrypted at rest; for identities Egeria already holds, RE stores the
   collection name and the role.
6. **Identity is provenance.** Every survey row and every published
   annotation records the identity that produced it. Two runs under
   different identities are never compared as if the resource changed.
7. **Announce yourself.** Every connection sets an application name
   ("resource-explorer scouting as egeria_user") so the target's own audit
   log shows who and why. This is what makes owners willing to grant the
   catalog identity at all.
8. **Elevation is not RE's job.** When an identity is insufficient, RE
   raises a request for action to the resource owner naming the exact
   grant; it never escalates, and never asks Egeria to.

---

## 2 · The identities

| Identity | Holds | Row access | Owner, lifecycle | When asked for |
|---|---|---|---|---|
| **A — catalog** | topology, definitions, keys, comments, ownership metadata, row and partition *estimates*, activity and session statistics (`catalog` + `stats` capabilities, §3) | none | infrastructure; granted once, held long-term; **the identity Egeria's own surveys use** | **at registration, always.** "Surveying the unknown" — a resource Egeria does not know — asks the user for this and nothing more |
| **B — data** | `SELECT` on target tables **including column statistics** (histograms, most-common values), which are derived from values and leak them (`read` capability) | scoped to schemas or tables; default/future privileges so it survives migrations | the data owner; often time-limited; revocable independently of A | **at the gate**, when a `read`-tier step needs it and the user can see why |
| (C — export) | bulk extraction | a different risk class from B; its own connection and, where available, a replica | — | out of scope for surveys; appears only as dataset download from portals |
| `write` | — | belongs to no survey identity | — | never |

Two identities can be the same account on a small system; the model still
holds, because the *bindings* stay separate: a catalog-tier step never
uses B, and a read-tier step never runs with A and quietly under-reports.

### 2.1 What the catalog identity needs, per engine

From the engine survey (`DATABASE-ENGINE-CATALOG-CAPABILITY-SURVEY.md`) and
the live probes of 2026-09-24/25. The **structural floor** column says
whether A can see that a table exists without being able to read it, which
is what gives the probe its denominator.

| Engine | Identity A grants | Structural floor | Column statistics without table read? | Native Egeria survey |
|---|---|---|---|---|
| PostgreSQL | `CONNECT`; `USAGE` on schemas; `pg_read_all_stats` for the monitor views | **unprivileged** — `pg_class`, `pg_namespace`, `pg_attribute`, `pg_constraint`, `pg_description`, `pg_stat_all_tables/_indexes` are readable by any role | **no** — `pg_stats` is filtered by column `SELECT`; `pg_read_all_stats` does *not* unlock it (live probe, 2026-09-25: 0 rows, then 9 rows after `SELECT` alone). `pg_read_all_data` does, and is table read | yes |
| DuckDB, SQLite | opening the file | unprivileged — no per-user privileges exist | trivially yes: everything collapses to "file opens" / "file writable" | DuckDB yes; SQLite no |
| Oracle | `SELECT_CATALOG_ROLE` (or `SELECT ANY DICTIONARY`) | **role grant** — with it, `ALL_*`/`DBA_*` are fully visible with no row access | no — histograms need object access or DBA roles | yes |
| SQL Server | `VIEW ANY DEFINITION`; `VIEW SERVER STATE` (or `VIEW DATABASE PERFORMANCE STATE`) | **role grant** — `VIEW DEFINITION` at database scope | no — `DBCC SHOW_STATISTICS` / `sys.dm_db_stats_histogram` need `SELECT` or elevated roles | yes |
| MySQL / MariaDB | `PROCESS`, `SHOW VIEW`; in practice a reader account | **none** — `information_schema` shows only objects the user holds some privilege on; "`SELECT` on `information_schema.*`" is not grantable | no | no |
| Snowflake, BigQuery | `USAGE` on database and schema; `ACCOUNT_USAGE` / `INFORMATION_SCHEMA` | role grant; metered reads | partly — micro-partition and storage metadata carry exact counts and bounds (recorded at write) | Unity yes; Snowflake, BigQuery not yet audited |

### 2.2 What the data identity needs

A dedicated reader role with `SELECT` on the schemas and tables in scope,
granted with default or future privileges so a migration does not silently
shrink it; `statement_timeout` and memory quotas set on the role; a
read replica or analytical cluster where one exists. RE's own guardrails
on top: sampling (`TABLESAMPLE`, §5.8 of the design), per-step time
budgets, and the cost vector (design §17.2) so load on the source is
observed, not assumed.

---

## 3 · Capabilities: what a step declares, and what the probe measures

### 3.1 The vocabulary, and which identity holds each value

| Capability | Means | Identity | Postgres check (live-verified) |
|---|---|---|---|
| `catalog` | system-catalog reads: structure, keys, comments, estimates, **per-table activity counters** | A | always true for a role that connected — `pg_stat_all_tables` has no ACL predicate (verified 2026-09-24 as `egeria_user`, 58 of 58 tables) |
| `stats` | the **monitor-gated** views only: `pg_stat_replication` standby rows, `pg_stat_statements`, other sessions' query text in `pg_stat_activity` | A (with `pg_read_all_stats` / `pg_monitor`) | `pg_has_role(current_user, 'pg_monitor', 'MEMBER')` |
| `read` | `SELECT` on target tables, **and** `pg_stats` column statistics | B | `has_schema_privilege(…, 'USAGE')` per schema; `has_table_privilege(…, 'SELECT')` per table |
| `write` | never for a survey | none | inferred from grants; never exercised |

There is **no ladder**: `stats` does not imply `read` and `read` does not
imply `stats`. A monitoring role with no table grants satisfies `stats` and
fails `read`; the incident credential was the reverse on three tables. Each
value is its own predicate.

### 3.2 Declaration: per step, binding to an identity

`requires_capability` is declared on `StepInfo`, next to `fetch_cost` and
`compute_cost`, because steps are what open connections; the analysis
catalog inherits the strongest requirement of its steps for display. Under
the two-identity model the declaration resolves to a **binding**:
`catalog` and `stats` → identity A; `read` → identity B. A survey that
spans both binds two connections.

Current database steps (after the 2026-09-25 correction):

| Step | Requires | Binds to |
|---|---|---|
| `credential_capability` (the probe, and the catalog-only structural inventory it stores — the "structure with zero `SELECT`" fallback of #257) | `catalog` | A |
| `postgres_schema_and_stats` (schema/table/column enumeration via `information_schema.*`, which is privilege-filtered, plus `pg_stats` column statistics) | `read` (#274; checked against `main` 2026-09-25) | B |
| `postgres_operations` (bundles `db_activity_signals`, `privilege_audit`, `db_external_dependencies` — all catalog-tier — with `db_resilience`, which reads `pg_stat_replication`; a step declares the strongest requirement of its bundle) | `stats` | A |
| `db_derived` (classification, grain from keys, fingerprint, conventions, change rates) | none — reads stored rows | — |
| `postgres_column_profile`, `postgres_nested_columns`, `sql_analysis`, `data_class_match`, `reference_data_match`, `coverage_profile` | `read` | B |
| any repair or write step (none exist for surveys) | `write` | never a survey identity |

### 3.3 The probe

`credential_capability` runs at registration and on demand, at Scouting
tier, catalog reads only, no trial writes. It records, per identity:
`connected_as`; schemas visible of total; tables with `SELECT` of total,
by schema; `stats` role membership; write inferred; probed at. The gate
(`prerequisite_resolver`) **reads the stored probe and never re-probes** —
a gate that opened a connection to decide whether to open a connection
would be the cost it exists to avoid. "Never probed" is a third answer and
does not block: on RE's own failure to establish something, the step
runs.

### 3.4 Denominators, and the fourth completeness state

Where the engine has a structural floor (§2.1), the probe's fractions are
exact: "SELECT on 3 of 26 tables in 6 of 8 schemas". Where it has none
(MySQL), or the role grant is not held (Oracle, SQL Server), the total is
unknown and the report reads **"3 tables visible; total not
established"** — never a fraction. Every envelope for a database fact
therefore carries one of four states: *measured*, *measured within
credential scope* (with the fraction), *measured within credential scope,
total unknown*, *not established*.

---

## 4 · Where credentials live

### 4.1 In Egeria — the model of record

- **One `Connection` per identity on the asset.** The `ResourceConnection`
  relationship allows any number of connections per asset; each wraps its
  own `SecretsStoreConnection` naming a secrets collection.
- **The role is the link's label.** `ResourceConnection` is a labelled
  relationship; `catalog` / `data` go on `label`, with `description` for
  scope and owner. Verified readable through
  `ConnectionMaker.find_assets(...)['connections']` (2026-09-24); the
  endpoints-for-asset and find-connections calls return empty for the same
  asset and should not be used for this.
- **Who may use which is metadata security.** Zones and security tags on
  the `Connection` element decide which connections a given user, including
  the engine host's user, can see (§5).
- **Egeria holds no secret-read API.** pyegeria has `save_client_side_secret`
  and `delete_client_side_secret` and nothing that reads one back; secrets
  are read only by connectors. This is deliberate and shapes §4.3.

### 4.2 In Resource Explorer — an index, plus RE's own secrets

- **Today:** `databases.db_user` and `databases.db_password`, the latter
  encrypted at rest with Fernet (`credential_crypto.py`; key from
  `RE_DB_CREDENTIAL_KEY`, falling back to `TRELLIS_DB_CREDENTIAL_KEY`, and
  to a per-host derived key with a logged warning — set one for any real
  deployment, or encrypted values will not survive a move).
- **Designed:** a `database_credentials` index — `(slug, connection_guid,
  role, secrets_collection_name, granted_by, expires_at, last_probe_at,
  capability)` — that is a cache of Egeria's connections, never the source
  of truth; the single `db_user`/`db_password` pair becomes the default
  connection reference. RE keeps a secret value only for identities it
  created itself.

### 4.3 The `.omsecrets` projection — one name, one writer, two copies

Because Egeria cannot hand a secret back, RE's local runs and Egeria's
native runs resolve the same conceptual credential from two places. The
model unifies **the name and the writer**, not the storage:

- The collection name is a pure function fixed at registration
  (`omsecrets_store.secrets_collection_name`: `"{slug}::PostgreSQL Secret"`
  today; per-role under the two-identity model) and **persisted on the
  registry row, never re-derived** — a slug rename must not orphan a
  collection the engine host is still using.
- RE writes both places in one operation: its encrypted registry copy, and
  the YAML collection in RE's own secrets file, which is the projection for
  the engine host. `EGERIA_SECRETS_STORE_LOCAL_PATH` is the host-visible
  path (`/deployments/secrets/resource-explorer.omsecrets` inside the
  quickstart container, which is a single container so a server-side write
  is immediately visible; a multi-host deployment needs the container path
  mounted or written through the platform).
- **Refresh:** Egeria instantiates a fresh connector per survey run and the
  secrets connector re-reads its file on the first read of each instance,
  so an edit is live for the next survey; an already-running survey sees it
  after `refreshTimeInterval` (60 minutes in the shipped file). No restart.
- **Drift is detected by name:** a collection missing on either side is the
  reachability outcome `unresolvable_secret` (§6), not a resource failure.

Two related bugs found on the way and fixed in RE (#185's verification):
the template-bound `secretsCollectionName` placeholder was never
substituted, and Egeria's YAML secrets-store *provider* instantiates a
read-only connector so server-side saves silently succeeded doing nothing.
The second is an Egeria issue (ISSUE-112 in `PYEGERIA_ISSUES.md`).

---

## 5 · How a credential is chosen for a run

| Route | Who chooses | Rule |
|---|---|---|
| **RE-local survey** | RE, for the signed-in user | list the asset's connections (security connector filters by user); read each link's label; bind the step to the identity its capability resolves to; among several of that role, the least-privileged whose probe satisfies the step; the user may override from the list. **Purpose-aware (project owner, 2026-09-25):** the investigation's Purpose — modelled in Egeria as a valid values list (design §10) — is an input to the choice: an Explore or Select investigation binds the catalog identity by default and asks before using a data identity; Assess and Certify bind the data identity where one is labelled for that purpose; the connection's `ResourceConnection` label may name the purposes it is intended for, and a connection labelled for a purpose wins over an unlabelled one of the same role |
| **Egeria-native survey** | Egeria's security connector, for the engine host's user | RE cannot pass a connection — the survey call takes only the asset GUID. `ConnectionHandler.getConnectionForAsset` hands every connection on the asset to `securityVerifier.selectConnection`, which keeps those the user may read and returns one |

**Two defects in the default security connector**
(`OpenMetadataAccessSecurityConnector.selectConnection`, lines 2628–2674 in
the current source), both logged for upstream filing:

1. When exactly one connection is visible it returns the first of the
   *unfiltered* list, not the visible one — an asset with an admin
   connection first and a surveyor connection second, seen by a user allowed
   only the second, gets the admin one.
2. When several are visible it picks one **at random**.

**Operational rule until both are fixed:** the engine host's user must be
able to see **exactly one connection per asset, the catalog identity**.
Under the two-identity model that means the data connection is zoned away
from the engine host's user. Egeria then has surveyor-level access and
never user-level access by construction, which is the property the project
owner described on 2026-09-25 as the intended state.

---

## 6 · Reachability

Reachable means **network and a resolvable secret**, not "a connection
exists". Probe 9 (2026-09-21) showed an asset with a real connection whose
secret did not resolve failing on SCRAM authentication after the
"has a connection" check passed. The reachability record's outcome
vocabulary: `reachable`, `no_connection`, `unresolvable_secret`,
`network_unreachable`, `auth_rejected`, `unknown` — with "checked but could
not be determined" as `unknown`, never silently `reachable`. The probe is a
`CHECK_ASSET`-only engine action (`finalAnalysisStep=CHECK_ASSET`, passed
through `initiate_gov_action_type` because the survey convenience wrappers
drop request parameters). Built for filesystems (`reachability.py`); the
database variant follows the same mapping.

When reachability or capability is insufficient, RE raises a request for
action to the resource owner naming the exact grant, engine-specific:

| Engine | Ask, in order |
|---|---|
| PostgreSQL | `SELECT` on the named tables (structure is already visible) |
| Oracle | `SELECT_CATALOG_ROLE` first (structure, no rows), then `SELECT` |
| SQL Server | `VIEW DEFINITION` first, then `SELECT` |
| MySQL | a surveyor account with `SELECT` on the databases of interest; nothing cheaper exists |

---

## 7 · What the user is told

- **Registering a resource Egeria does not know:** the form asks for the
  catalog identity only, in these words: *"A read-only account that can see
  the catalog and statistics. Table data access is not needed yet."* The
  probe runs immediately and the banner shows what was given.
- **The banner:** "Connected as `egeria_user` — sees 6 of 8 schemas; can
  read 3 of 26 tables; no write access." Persistent, not a toast; on engines
  without a floor, "3 tables visible; total not established."
- **Every database answer** carries its completeness state (§3.4) and, once
  built, the basis and freshness of any estimate (design §5.1a).
- **The gate**, when a step needs more than the bound identity has:
  *"Needs the data identity (SELECT on 23 of 26 tables) — supply one, pick a
  broader connection, or run the catalog-tier steps only."* Three choices,
  never a silent partial run. Retry appears only when a retry could change
  the answer.
- **The Survey & Analyses pane** shows engine and identity as secondary
  details on each row and in its popover (`REPLY-SURVEY-ANALYSES-PANE-
  USER-FACING-MODEL.md`), never as a banner the user must get past.

---

## 8 · Resource Explorer's own identities

Unchanged by this model and documented in `admin-guide.md`: users sign in
with an Egeria user id and password, exchanged once for a bearer token and
never stored; RE publishes into a draft governance zone and promotes on
curate-accept; the service account in `.env` is what RE uses toward Egeria.
The one interaction with this document: the *engine host's* user, which is
an Egeria configuration, is the user whose connection visibility §5's
operational rule constrains.

---

## 9 · Open items and upstream

| Item | Where |
|---|---|
| Egeria: `selectConnection` returns the wrong element when one is visible; random choice among several | file upstream with the line references in §5 |
| Egeria: YAML secrets-store provider instantiates a read-only connector, so `saveClientSideSecret` reports success doing nothing | ISSUE-112 |
| pyegeria: `ConnectionMaker.link_*` silently no-op with `body=None`; `_async_initiate_survey` drops request parameters; `NewElementRequestBody` drops `initialStatus` | ISSUE-111, ISSUE-113, and the parameters item in `PYEGERIA_ISSUES.md` |
| The §4.2 model change: two labelled connections per asset, per-role collections, `database_credentials`, retirement of the clear pair | awaiting the project owner's go-ahead; Phase 1 |
| Per-engine declaration beyond Postgres (`structural_floor`, checks, containment) | with the DuckDB slice (D4) |
| Snowflake, BigQuery, Unity, Cassandra, MongoDB, Kafka rows of §2.1 | not yet audited |
