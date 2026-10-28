# Database engine catalog/capability survey — PostgreSQL, MySQL/MariaDB, DuckDB, Oracle, SQL Server, SQLite

**For:** the credential-capability / `requires_capability` design track, as a second
data point beyond Postgres before any cross-engine generalization is attempted.
**Not:** a redo of Postgres's audit, a design decision, or a `StepInfo`/`requires_
capability` proposal. This is input a future design pass can cite.
**Method:** each engine's own official reference documentation, cited per claim.
Where a claim could not be pinned to an exact doc sentence, it is marked
**"unverified, based on general knowledge"** rather than presented with the
confidence of a cited claim. Do not treat unverified lines as equivalent to
`DATABASE-STEP-CAPABILITY-AUDIT.md`'s (`#254`) Postgres findings, which were
traced to actual SQL in this codebase and, in places, live-verified against a
real database.

**Vocabulary** (unchanged from `REPLY-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md`
§3, reproduced for reference):

| Value | Means |
|---|---|
| `catalog` | system-catalog reads, visible to any connected role regardless of grants |
| `read` | needs `SELECT` on target tables |
| `stats` | needs elevated role/membership or ownership for full monitoring-view visibility |
| `write` | never exercised by a survey, only probed |

**Per-engine structure.** Following a mid-task refinement from the architecture
session (routed through the coordinating session while this doc was being
written), each engine below is organized in this fixed order so it slots
directly into `ASK-SCHEMA-AS-SUB-RESOURCE.md` §5 item 1's per-engine
declaration (that file is the actual doc on disk — `REPLY-SCHEMA-AS-SUB-
RESOURCE.md` does not exist yet; this survey cites the ask, not a reply):

1. Containment levels, with four flags (`namespace` / `owner` / `security_boundary`
   / `physical_unit`), the default container, and which system containers to
   exclude from a survey.
2. Catalog-only sources for size, structure, keys, comments, and activity —
   the engine's analog of `pg_class`/`pg_constraint`/`pg_stat_all_tables`.
3. Whether column statistics exist without `SELECT` on the table — this
   decides where a Scouting-tier coverage estimate is free versus not.
4. Privilege-check primitives for `catalog`/`read`/`stats`/`write`, explicitly
   naming where a tier is **meaningless** for that engine rather than unknown.
5. Egeria technology types per containment level, and whether a native Egeria
   survey action service exists for it today.

**Scope note on item 5 and the cloud/NoSQL engines.** The architecture session's
refinement also asked about Snowflake, BigQuery, Unity Catalog, Cassandra,
MongoDB, and Kafka, and flagged that catalog/stats reads are *billed* on the
cloud warehouses (a real cost-vector concern against design §17's
`fetch_cost` axis, since what is free on Postgres has a metered dollar cost
there). Those six engines are **not** part of this pass — researching them
to the same standard as the six below is a separate, similarly-sized task.
This survey answers the Egeria-native-survey-service question for the six
engines actually in scope (§5 of each section below) and confirms via the
Egeria source tree (`/Users/dwolfson/localGit/egeria-v6/egeria/open-
metadata-implementation/adapters/open-connectors/data-manager-connectors/`)
that connector directories exist today only for `postgres-server-connectors`,
`oracle-server-connectors`, `mssql-server-connectors`, `duckdb-connectors`,
`db2luw-server-connectors`, and `unity-catalog-connectors` — **no** `mysql-*`
or `sqlite-*` directory exists in that tree, confirmed by direct listing
(2026-09-24). Kafka's survey service lives elsewhere in the tree (event-broker
connectors, not data-manager-connectors) and was not re-checked here since
Kafka is out of this pass's scope; the coordinator's message already treats it
as confirmed-existing. The billing/cost-vector point for Snowflake/BigQuery is
recorded here as a flag for whoever does that follow-up, not resolved.

---

## 1 · PostgreSQL — reference, not redone

**Already audited, live-verified, and not repeated here.** See
`DATABASE-STEP-CAPABILITY-AUDIT.md` (`#254`) for the full trace: every
`DATABASE_STEP_REGISTRY` entry, every method in `connection.py`, mapped to
`catalog`/`read`/`stats`/`write` against actual SQL, with one live
cross-check against `coco_pharma` (`pg_namespace`/`pg_class` unfiltered vs.
`information_schema`/`has_table_privilege` grant-filtered — the same
credential, two different answers).

For the five-point structure the other engines below follow:

1. **Containment levels:** cluster (`physical_unit` only — no security
   boundary spans it) → database (`namespace`, `physical_unit`, the unit
   `CONNECT` gates) → schema (`namespace`, `security_boundary` via `USAGE`)
   → table (`owner`, `security_boundary` via `SELECT`/`INSERT`/etc.).
   Default container: `public` schema. System containers to exclude:
   `pg_catalog`, `information_schema`, and (Postgres 15+) any schema owned
   by the bootstrap superuser that isn't `public`.
2. **Catalog-only sources:** `pg_namespace` (schemas), `pg_class` (tables,
   size via `pg_database_size()`/`pg_tables`), `pg_attribute` (columns),
   `pg_constraint`/`key_column_usage` equivalents via `regclass`,
   `pg_description` (comments) — all unfiltered, per `#254` §1.
3. **Column stats without `SELECT`:** **no** — `pg_stats` is defined
   `WHERE has_column_privilege(...)`, genuinely privilege-filtered
   (`#254` §1).
4. **Privilege-check primitives:** `has_schema_privilege`/`has_table_
   privilege`/`pg_has_role` — all catalog-tier privilege-*check* functions,
   not privilege-*filtered views* (`#254` §3). No tier is meaningless for
   Postgres; all four apply cleanly.
5. **Egeria technology types / native survey:** `PostgreSQLDatabase` /
   `RelationalDatabase` at the database level, `RelationalSchema` at schema,
   `RelationalTable`/`RelationalColumn` below that. A native Postgres
   survey action service exists (`postgres-server-connectors/.../survey`,
   confirmed present in the Egeria source tree) — this is the connector
   `HybridDatabaseSurveyor`'s `executes_at: egeria` path already drives.

---

## 2 · MySQL / MariaDB

### 2.1 Containment levels

| Level | namespace | owner | security_boundary | physical_unit |
|---|---|---|---|---|
| Server/instance | no | no | yes (global privileges) | yes |
| Database (`schema` in MySQL's own vocabulary — MySQL treats "database" and "schema" as synonyms) | yes | no (privileges are per-account, not per-schema-owner) | yes (`GRANT ... ON db.*`) | yes (own datadir subtree under InnoDB file-per-table, or shared tablespace) |
| Table | yes (qualified `db.table`) | no native ownership concept (MySQL has no `OWNER` on a table the way Postgres does) | yes (`GRANT SELECT ON db.table`) | yes |

MySQL/MariaDB has **no schema level distinct from database** — unlike
Postgres's three-tier database→schema→table, MySQL is two-tier:
database→table, with "database" and "schema" used interchangeably in its
own SQL (`CREATE SCHEMA` is a synonym for `CREATE DATABASE`). Default
container: none — every table must be qualified by an explicit database.
System containers to exclude: `mysql`, `information_schema`,
`performance_schema`, `sys` (MySQL); the same four in MariaDB.

### 2.2 Catalog-only sources

None of MySQL's/MariaDB's table/column/schema listing views are
catalog-tier in the Postgres sense — see 2.4. There is no MySQL analog of
`pg_class` that is unfiltered by grants. The closest thing to "structure
without a grant check" is the table's own `SHOW CREATE TABLE`, which
**still requires some privilege on the table** per the same rule as
`information_schema` (dev.mysql.com, "INFORMATION_SCHEMA General
Information": *"The same privileges apply to selecting information from
INFORMATION_SCHEMA and viewing the same information through SHOW
statements... you must have some privilege on an object to see information
about it."*).

### 2.3 Column stats without `SELECT`

**No.** `information_schema.COLUMN_STATISTICS`/`STATISTICS` follows the
same object-privilege-required rule as every other `information_schema`
table (same citation as above) — there is no free, ungated route to a
column's cardinality/histogram data.

### 2.4 Privilege-check primitives, tiers

`information_schema.TABLES`/`COLUMNS`/`SCHEMATA` are **`read`**-tier, not
`catalog`-tier: MySQL's own docs state each user "can see only the rows...
that correspond to objects for which the user has the proper access
privileges" (dev.mysql.com, INFORMATION_SCHEMA Introduction). This is the
opposite of Postgres's `pg_class`/`pg_namespace` and the single biggest
structural difference from Postgres worth flagging: **MySQL/MariaDB have no
documented `catalog`-tier layer at all for ordinary schema/table/column
metadata** — everything that answers "what tables exist" is already
grant-filtered. (MariaDB's parity with this specific behavior was not found
independently stated in MariaDB's own KB during this pass — flagged
**unverified, based on general knowledge** that MariaDB inherits the same
mechanic, since MariaDB forked from MySQL's `information_schema`
implementation and no divergence was found.)

| Analysis-shaped question | Source | Tier | Why |
|---|---|---|---|
| List tables/columns/schemas | `information_schema.TABLES`/`COLUMNS`/`SCHEMATA` | `read` | grant-filtered per-object (dev.mysql.com) |
| Own effective grants | `SHOW GRANTS` (no arg) | no-elevation self-check | requires no privilege for your own account |
| Another account's grants | `SHOW GRANTS FOR 'user'@'host'` | `stats` | requires `SELECT` on `mysql.*` (dev.mysql.com) |
| Table I/O, statement digests | `performance_schema.*` | `stats` | requires an explicit, non-default `SELECT` grant per table (dev.mysql.com, "Performance Schema General Table Characteristics") |
| All sessions across users | `performance_schema.threads` / full `SHOW PROCESSLIST` | `stats` | needs `PROCESS` privilege for others' threads (dev.mysql.com, "Accessing the Process List") |
| Raw account/password rows | `mysql.user` (MySQL) / `mysql.global_priv` (MariaDB) | `stats`-and-then-some | `SELECT` on that specific system table, not part of any ordinary grant — **unverified against one explicit restrictive sentence**, but consistent with all privilege documentation |
| Replication status | `SHOW REPLICA STATUS`/`SHOW MASTER STATUS` | `stats` | needs `REPLICATION CLIENT` in MySQL (dev.mysql.com, "Replication Privilege Checks"); MariaDB moved this to a dedicated `REPLICA MONITOR`/`SLAVE MONITOR` privilege as of 10.5.9 (mariadb.com docs, MDEV-23899/24107) — a genuine MariaDB-only fork of the model |
| Write probe | none exercised | `write` | not directly probable via a MySQL equivalent of `has_table_privilege` found in this pass — MySQL has no single-call privilege-check function analogous to Postgres's; the closest is parsing `SHOW GRANTS` output, which is a materially different mechanism worth flagging for whoever builds this |

No tier is meaningless for MySQL/MariaDB — the model applies, it is just
shifted: there is effectively no free `catalog` tier for object metadata,
only `read` and above.

### 2.5 Egeria technology types / native survey

No `mysql-server-connectors` (or MariaDB equivalent) directory exists in
the Egeria source tree's `data-manager-connectors/` (confirmed by direct
listing, 2026-09-24, alongside `db2luw-server-connectors`, `duckdb-
connectors`, `mssql-server-connectors`, `oracle-server-connectors`,
`postgres-server-connectors`, `unity-catalog-connectors`). **No native
Egeria survey action service exists for MySQL or MariaDB today.** Any
MySQL support in Resource Explorer would need to be RE-local
(`executes_at: resource-explorer`), the same way `credential_capability`
already is — there is no `executes_at: egeria` path available to delegate
to, unlike Postgres/Oracle/SQL Server/DuckDB.

---

## 3 · DuckDB

### 3.1 Containment levels

| Level | namespace | owner | security_boundary | physical_unit |
|---|---|---|---|---|
| Process/session | no | n/a (single principal — DuckDB "executes SQL with the full privileges of the user running it, much like a shell or scripting interpreter," per duckdb.org's Securing DuckDB — Overview) | no | no |
| Database (own file, or `ATTACH`ed) | yes | no | no (only `ATTACH ... (READ_ONLY)`, a write-not-visibility gate) | yes — one file per database |
| Schema | yes | no | no | no |
| Table | yes | no | no | no |

Default container: `main` schema in the default (unnamed) attached
database. System containers to exclude: `system` and `temp` databases,
and (per `duckdb_databases()`) any database whose `internal` flag is true.

### 3.2 Catalog-only sources

`duckdb_tables()`, `duckdb_columns()`, `duckdb_schemas()`, `duckdb_
databases()` (all in `main` schema, per duckdb.org's DuckDB Table
Functions doc) return the whole catalog — size estimates, column-count,
type, nullability, PK membership, SQL definition, and (`duckdb_databases`)
read-only/attached-path status — to any session with no privilege check of
any kind. `pragma_table_info()`, `pragma_database_list()`, `pragma_
database_size()`, `pragma_storage_info()` are the same: unrestricted.
There is no comments/`obj_description()` equivalent found in this pass.

### 3.3 Column stats without `SELECT`

**Yes, if the file opens at all** — this is DuckDB's defining departure
from every client-server engine in this survey. Because there is no
privilege system separating "see the schema" from "read the data,"
anything a column-profile step would compute is reachable the moment a
connection succeeds. There is no free-vs-gated split to make.

### 3.4 Privilege-check primitives, tiers — the model is a declared degenerate case, not a gap

**This is not unresearched — it is the documented reality.** DuckDB's own
Securing DuckDB guide states there is no SQL-level `GRANT`/`REVOKE` and no
users/roles concept in standard embedded mode; the real boundaries are
process/file access (OS permissions on who can run the process or open the
`.duckdb` file), `enable_external_access`/`allowed_directories` (filesystem
sandboxing, not data privilege), and resource caps. `ATTACH ... (READ_
ONLY)` (per DuckDB's `ATTACH` statement doc) restricts *writes*, not
*visibility* — a read-only-attached database's full catalog remains
queryable exactly as if attached read-write.

**Declared result: `catalog`, `read`, and `stats` all collapse into one
value — "the file/process opened" — for standard embedded DuckDB.** This
is a genuine engine property, not a research gap: there is no separate
grant that would ever produce a different answer for `duckdb_tables()`
versus a `SELECT` versus `pragma_database_size()`. The only real second
axis is `write`, gated by `ATTACH (READ_ONLY)` or the file's own OS write
permission.

| Analysis-shaped question | Source | Tier | Why |
|---|---|---|---|
| List tables/schemas/databases | `duckdb_tables()`, `duckdb_schemas()`, `duckdb_databases()`, `information_schema.*` | **n/a — meaningless as a separate tier** | universally visible, no privilege model exists to gate it |
| Column types/profile | `duckdb_columns()`, `pragma_table_info()`, plain `SELECT` | **n/a — collapses into the same access as catalog** | no intermediate grant exists between "see the column" and "read it" |
| DB size / memory / WAL | `pragma_database_size()`, `duckdb_memory()` | **n/a — no elevated-role concept** | same universal visibility |
| Writes | `INSERT`/`UPDATE`/DDL | `write` (probed via open-mode, not a GRANT check) | the one real gate: `ATTACH (READ_ONLY)` or OS file permission |

MotherDuck's cloud sharing layer reportedly adds GRANT/REVOKE-on-shares,
which would reintroduce something closer to the four-tier model — **not
verified against MotherDuck's own docs in this pass**, and out of scope
for a survey about the embedded engine RE would actually connect to.

### 3.5 Egeria technology types / native survey

`duckdb-connectors/.../survey` exists in the Egeria source tree — a native
DuckDB survey action service is present (confirmed by directory listing,
2026-09-24). Given §3.4's finding, an Egeria-native DuckDB survey has no
credential-capability question to answer in the first place: whatever
connection the survey service opens already sees everything short of
writing.

---

## 4 · Oracle Database

### 4.1 Containment levels

| Level | namespace | owner | security_boundary | physical_unit |
|---|---|---|---|---|
| Database/instance | no | no | yes (system privileges, roles) | yes |
| Schema (= a user account, in Oracle's model — schema and user are the same object) | yes | yes (the schema *is* its owning user) | yes (object privileges) | no (tablespaces are the physical unit, cutting across schemas) |
| Tablespace | no | no | yes (quota) | yes |
| Table | yes (qualified `owner.table`) | yes (inherits schema owner) | yes (`GRANT SELECT`) | no |

Oracle's schema/user identity is a real structural point: there is no
schema without a corresponding database user, unlike Postgres where a
schema is just a namespace any role can be granted into. Default
container: none — every reference is schema-qualified, or resolves via the
connecting user's own schema. System containers to exclude: `SYS`, `SYSTEM`,
and (in a pluggable/multitenant setup) `PDB$SEED`; the standard
Oracle-maintained schema list (`ORDSYS`, `MDSYS`, `CTXSYS`, etc.) if present.

### 4.2 Catalog-only sources

**None found — this is the headline structural difference from Postgres.**
Oracle's dictionary has no tier analogous to `pg_class` that is visible to
any connected user regardless of grants. The three-way `USER_`/`ALL_`/`DBA_`
split (confirmed via Oracle's own "About Static Data Dictionary Views"
reference) *is* the whole model:

- `USER_TABLES`/`USER_TAB_COLUMNS`/etc.: "displays all the information
  from the schema of the current user. No special privileges are
  required" — but this is self-scoped, not catalog-wide.
- `ALL_TABLES`/`ALL_TAB_COLUMNS`/etc.: "displays all the information
  accessible to the current user... by way of grants of privileges or
  roles" — grant-filtered, Oracle's rough analog of Postgres's
  `information_schema` (i.e. `read`-tier), not of `pg_class`.
- `DBA_TABLES`/etc.: "all relevant information in the entire database,"
  gated by `SELECT ANY DICTIONARY`, `SELECT_CATALOG_ROLE`, or `SYSDBA`.

There is no universally-visible structural layer to fall back to the way
Postgres's `pg_class`/`pg_namespace` let a low-privilege credential still
see that a table exists. This is worth its own callout for whoever designs
Oracle's `credential_capability` probe: the Postgres probe's whole design
(catalog-tier reads establish the denominator even when read-tier access
is thin) has **no equivalent floor in Oracle** — a credential with neither
`ALL_` visibility on an object nor `DBA_` privilege cannot establish that
the object exists at all.

### 4.3 Column stats without `SELECT`

**No, and worse than Postgres.** Optimizer column statistics live in
`ALL_TAB_COL_STATISTICS`/`USER_TAB_COL_STATISTICS`/`DBA_TAB_COL_STATISTICS`,
which inherit exactly the three-way grant-filtered split in §4.2 — full
visibility needs `DBA_`-tier privilege, and even the `ALL_` version needs
the same object-level grant a real `SELECT` would need. There is no
Postgres-`pg_stats`-style single privileged view; the split is the same
one governing every other dictionary object.

### 4.4 Privilege-check primitives, tiers

| Analysis-shaped question | Source | Tier | Why |
|---|---|---|---|
| Tables I own | `USER_TABLES` | self-scoped, no elevation | "No special privileges are required" (Oracle, Static Data Dictionary Views) |
| Tables I can see (mine + granted) | `ALL_TABLES` | `read` | grant-filtered — closest Oracle analog to Postgres's `information_schema` |
| All tables in the database | `DBA_TABLES` | `stats` (mapped here since it needs elevation, though it's structural not monitoring data) | needs `SYSDBA`/`SELECT ANY DICTIONARY`/`SELECT_CATALOG_ROLE` |
| Grants on my objects | `USER_TAB_PRIVS` | self-scoped | owner/grantor/grantee = self |
| Grants I can see (mine + role/PUBLIC) | `ALL_TAB_PRIVS` | `read` | same grant-filtered semantics |
| All grants in the database | `DBA_TAB_PRIVS` | `stats`-tier elevation | "describes all object grants in the database," gated by the `DBA_` rule |
| Active sessions / running SQL | `V$SESSION`, `V$SQL` | `stats` | `V$` views need `SYSDBA`/`SELECT_CATALOG_ROLE`/`SELECT ANY DICTIONARY` (Oracle, About Dynamic Performance Views) |
| Replication/standby health | `V$DATAGUARD_STATS`, `V$ARCHIVE_DEST` | `stats` (extrapolated from the general `V$` rule) | **unverified against an explicit per-view doc statement** — inherits the `V$` privilege rule but not independently confirmed for this specific view |
| Write probe | object-level `INSERT` privilege via `ALL_TAB_PRIVS`/`USER_TAB_PRIVS` filtered to `PRIVILEGE = 'INSERT'` | `write` | checked via the same grant-filtered views as `read`, never exercised |

No tier is meaningless for Oracle. The finding worth carrying forward is
structural, not a gap: **Oracle collapses Postgres's `catalog` tier into
`read`** — there is no cheaper fallback than a grant-filtered view, at any
scope narrower than `DBA_`.

### 4.5 Egeria technology types / native survey

`RelationalDatabase`/`OracleDatabase` at the database/schema-as-user level
(Oracle's schema-is-a-user model maps a schema to a `RelationalSchema`
whose qualified name embeds the owning user), `RelationalTable`/
`RelationalColumn` below. `oracle-server-connectors/.../survey` exists in
the Egeria source tree — a native Oracle survey action service is present
(confirmed 2026-09-24).

---

## 5 · Microsoft SQL Server

### 5.1 Containment levels

| Level | namespace | owner | security_boundary | physical_unit |
|---|---|---|---|---|
| Server/instance | no | no | yes (server-level permissions, logins) | yes |
| Database | yes | yes (a database has an owner principal) | yes (database-level permissions, `db_owner` etc.) | yes (own set of files/filegroups) |
| Schema | yes | yes (`AUTHORIZATION` clause) | yes (schema-level `GRANT`) | no |
| Table | yes (qualified `schema.table`) | yes (inherits schema) | yes (object-level `GRANT`) | no |

SQL Server is the one engine in this survey with the full four-level
stack (server → database → schema → table) *and* an owner concept at
every level from database down — closest structurally to Postgres of the
non-Postgres engines, per the closing comparison below. Default container:
`dbo` schema. System containers to exclude: the `master`, `tempdb`,
`model`, and `msdb` system databases; within a user database, the `sys` and
`INFORMATION_SCHEMA` schemas.

### 5.2 Catalog-only sources

**A short, explicit, Microsoft-named whitelist — not a general rule.**
SQL Server's own "Metadata Visibility Configuration" doc states plainly:
*"The visibility of metadata is limited to securables that a user either
owns or on which the user has been granted some permission"* — and gives
the example that querying `sys.tables` for a table the caller cannot
access "returns an empty result set." This is the **opposite** of
Postgres's `pg_class`: SQL Server's default posture makes `sys.tables`/
`sys.columns`/`sys.objects` **`read`**-tier, not `catalog`-tier. The same
doc then carves out an explicit, named exception list that *is*
`catalog`-tier regardless of grants: `sys.schemas`, `sys.partitions`,
`sys.filegroups`, `sys.configurations`, `sys.messages`, `sys.allocation_
units`, `sys.data_spaces`, `sys.database_files`, among others.
`INFORMATION_SCHEMA.*` views follow the identical grant-filtered rule,
per Microsoft's cross-reference in the System Information Schema Views
doc — no daylight between the two families' filtering behavior, only in
which specific views happen to be publicly whitelisted.

### 5.3 Column stats without `SELECT`

**No — needs `VIEW DATABASE STATE`** (or, SQL Server 2022+, the narrower
`VIEW DATABASE PERFORMANCE STATE`) for `sys.dm_db_stats_properties` and the
related stats DMVs, per Microsoft's System Dynamic Management Views
documentation on DMV permission requirements. This is the example the
architecture session's own refinement message named directly, confirmed
here against Microsoft's docs rather than merely restated.

### 5.4 Privilege-check primitives, tiers

| Analysis-shaped question | Source | Tier | Why |
|---|---|---|---|
| List tables/columns | `sys.tables`, `sys.columns` | `read` | grant-filtered per the Metadata Visibility Configuration rule |
| List schemas | `sys.schemas` | `catalog` | explicitly named in the public-visible whitelist |
| Standard table/column listing | `INFORMATION_SCHEMA.TABLES`/`COLUMNS` | `read` | same visibility rule as `sys.*`, cross-referenced in Microsoft's own docs |
| "What can I do here?" self-check | `sys.fn_my_permissions` | `catalog` | needs only `public` membership — self-scoped, always answerable |
| All grants/principals in the database | `sys.database_permissions`, `sys.database_principals` | `stats` | own grants visible by default; seeing others' needs `VIEW DEFINITION` or equivalent — **unverified against a dedicated doc page for this exact pairing**, extrapolated from the general metadata-visibility rule |
| Active sessions, index usage | `sys.dm_exec_sessions`, `sys.dm_db_index_usage_stats` | `stats` | needs `VIEW SERVER STATE`/`VIEW DATABASE STATE` (or 2022+ `...PERFORMANCE STATE`/`...SECURITY STATE` split) |
| Always On / replication health | `sys.dm_hadr_database_replica_states` | `stats` | needs `VIEW SERVER STATE` — a clean, directly documented equivalent to `pg_stat_replication`'s gate |
| Write probe | `sys.fn_my_permissions` filtered to `INSERT`/`UPDATE` | `write` | self-check function, same mechanism as the `catalog`-tier read above, never exercised |

No tier is meaningless for SQL Server. The structural finding to carry
forward: **SQL Server's default is `read`-tier for almost everything
Postgres treats as `catalog`-tier**, with only a short, explicitly named
whitelist (plus the self-scoped `fn_my_permissions`) staying free of any
grant check.

### 5.5 Egeria technology types / native survey

`RelationalDatabase` at the database level (SQL Server maps naturally onto
Egeria's existing relational-database/schema/table/column type chain),
with the schema level using SQL Server's own `dbo`-or-named schema as the
`RelationalSchema` qualifier. `mssql-server-connectors/.../survey` exists
in the Egeria source tree — a native SQL Server survey action service is
present (confirmed 2026-09-24).

---

## 6 · SQLite

### 6.1 Containment levels

| Level | namespace | owner | security_boundary | physical_unit |
|---|---|---|---|---|
| File | yes (the file *is* the database) | no | no (no SQL-level principal at all) | yes — one file, one database |
| Table/index/view/trigger | yes | no | no | no (all rows live in one file's page store) |

**SQLite has no schema level and no multi-database-per-connection concept
beyond `ATTACH`ed files**, each of which becomes a name prefix
(`main`, `temp`, or an alias), not a security boundary. There is no
`owner`/`security_boundary` flag that is ever true anywhere in SQLite —
confirmed directly, not inferred: sqlite.org's Zero-Configuration page
states *"There is no need for an administrator to create a new database
instance or assign access permissions to users."* Default container:
`main` (the primary attached database). System containers to exclude: none
distinct from the file itself — SQLite has no separate system catalog
database; `sqlite_schema` lives inside the same file as user objects.

### 6.2 Catalog-only sources

`sqlite_schema` (formerly `sqlite_master`) holds one row per table, index,
view, and trigger — `type`, `name`, `tbl_name`, `rootpage`, and the
reconstructed `sql` (CREATE statement) — with **no privilege filtering of
any kind** (sqlite.org, The Schema Table). `PRAGMA table_info`, `PRAGMA
database_list`, `PRAGMA index_list` are the same: no gating beyond
whether the file could be opened. There is no comments mechanism (SQLite
has no `COMMENT ON` equivalent).

### 6.3 Column stats without `SELECT`

**Yes, if the file opens at all** — same shape as DuckDB. `dbstat` (a
read-only eponymous virtual table reporting per-object on-disk space
usage, sqlite.org's dbstat Virtual Table doc) and `sqlite_stat1`/
`sqlite_stat4` (populated by `ANALYZE`, ordinary tables queryable by plain
`SELECT`) carry no privilege gate beyond ordinary file-read access.

### 6.4 Privilege-check primitives, tiers — declared degenerate case

**Confirmed plainly, not left as a gap:** SQLite has no SQL-level `GRANT`/
`REVOKE`, no users, no roles. The only real access boundary is OS-level
file permissions and whether the connection was opened read-write or
read-only. `sqlite3_set_authorizer` is a real, documented mechanism — but
it is an **embedding-application** hook (used by e.g. browsers to sandbox
untrusted SQL from an extension), fired at `prepare()` time, one per
connection. It has nothing to do with an external tool connecting to a
plain `.db` file the way Resource Explorer would, and should not be
mistaken for a SQL-level privilege system a survey step needs to account
for.

**Declared result: `catalog`, `read`, and `stats` all collapse into "the
file opened," exactly as with DuckDB, for the same underlying reason (no
multi-principal model to elevate within).** The one real second axis is
`write`: `SQLITE_OPEN_READONLY` vs. `SQLITE_OPEN_READWRITE` at connection
time (with silent downgrade to read-only if the OS denies write access —
check via `sqlite3_db_readonly()`), plus `PRAGMA query_only` and the URI
`immutable=1` parameter as finer write-behavior controls. A practical
write-capability probe: check the connection's actual open mode via
`sqlite3_db_readonly()` rather than assuming from the requested flags,
since SQLite silently downgrades.

| Analysis-shaped question | Source | Tier | Why |
|---|---|---|---|
| Schema/structure | `sqlite_schema`, `PRAGMA table_info` | **n/a — meaningless as a separate tier** | universally visible once the file opens |
| Column values | plain `SELECT` | **n/a — collapses into the same access as schema** | no intermediate grant exists |
| Size / stats | `dbstat`, `sqlite_stat1`/`sqlite_stat4` | **n/a — no elevated-role concept** | same universal, file-open-gated visibility |
| Writes | `INSERT`/`UPDATE`/DDL | `write` | gated by connection open-mode/OS file permission, not a GRANT |

### 6.5 Egeria technology types / native survey

No `sqlite-*` connector directory exists in the Egeria source tree's
`data-manager-connectors/` (confirmed by the same directory listing as
§2.5). **No native Egeria survey action service exists for SQLite today.**
Like MySQL, any SQLite support in Resource Explorer would need to be
RE-local — there is no `executes_at: egeria` path to delegate to.

---

## 7 · Mapping the existing Postgres-built analyses

Against `db_classification`, `db_relationship_graph`, `grain_determination`,
`db_fingerprint`, `schema_conventions`, `credential_capability`, `privilege_
audit`, `db_activity_signals`, `db_resilience`, `db_external_dependencies`:

**Maps cleanly, same shape, different SQL:**
- `db_classification`, `db_relationship_graph`, `grain_determination`,
  `db_fingerprint`, `schema_conventions` — all read structural/constraint
  metadata (tables, columns, keys, comments). Oracle, SQL Server, MySQL/
  MariaDB all have some equivalent catalog surface to read this from
  (`ALL_*`, `sys.*`/`INFORMATION_SCHEMA`, `information_schema.*`
  respectively), just at different tiers than Postgres's mixed catalog/read
  split. DuckDB and SQLite make these *cheaper*, not harder — the whole
  fetch is `catalog`-equivalent (file-open-gated) with no grant check to
  navigate at all.
- `credential_capability` itself — maps to every engine with a real
  privilege model (MySQL, Oracle, SQL Server), each via that engine's own
  probe (§2.4/§4.4/§5.4 above), exactly as `REPLY-DATABASE-CREDENTIAL-
  CAPABILITY-VISIBILITY.md` anticipated with its "the credential probe is
  per engine" framing. For DuckDB/SQLite, the probe degenerates to a single
  boolean (file opened, file writable) — still a meaningful probe, just not
  a fractional one.
- `privilege_audit` — maps to Oracle (`DBA_TAB_PRIVS`/`ALL_TAB_PRIVS`) and
  SQL Server (`sys.database_permissions`) directly, though at a stricter
  tier than Postgres's `pg_class.relacl` (both need real elevation where
  Postgres's version is catalog-tier). Does not map to DuckDB/SQLite at
  all — there is nothing to audit.

**Does not map without real per-engine research, flagged explicitly rather
than assumed:**
- `db_activity_signals` and `db_resilience` — Postgres's versions read
  `pg_stat_user_tables`/`pg_stat_replication`/`pg_stat_archiver`. This
  survey found *plausible* per-engine equivalents (MySQL's `performance_
  schema` + `SHOW REPLICA STATUS`; Oracle's `V$` views + `V$DATAGUARD_
  STATS`; SQL Server's DMVs + `sys.dm_hadr_*`) but none of these were
  traced to the level of detail `#254` achieved for Postgres (exact
  columns, exact gating function, live verification). Building these for a
  second engine is real work, not a renaming exercise — the shape of "a
  monitoring-view family gated by an elevated role" repeats, but the exact
  views, columns, and privilege names differ enough that a direct port
  would produce wrong SQL, not just wrong permission checks.
- `db_external_dependencies` — Postgres's version reads `pg_extension`/
  `pg_foreign_server`/`pg_publication`/`pg_subscription`, none of which
  have an obvious analog researched in this pass for any other engine
  (Oracle has database links, `DBA_DB_LINKS`; SQL Server has linked
  servers, `sys.servers`; MySQL has no first-class equivalent found). This
  needs its own research pass per engine, not assumed from the concept
  name alone.
- `db_resilience`'s DuckDB/SQLite instance is a category error, not an
  unmapped gap: neither engine has replication, WAL archiving, or backup
  tooling in the sense Postgres's version means it. The analysis's whole
  premise (multi-node resilience posture) doesn't apply to an embedded,
  single-file database — this should be named "not applicable" for those
  two engines, not "not yet built."

---

## 8 · Where the four-value model itself doesn't fit

**DuckDB and SQLite, as anticipated in the task brief, are the real
candidates** — both confirmed here as declared degenerate cases (§3.4,
§6.4), not partial matches forced into the shape. For both, `catalog`/
`read`/`stats` collapse into one value (file opened, or not), and `write`
is the only axis that survives the collapse intact. A `requires_capability`
field that assumes all four values are always distinguishable would need
an explicit fifth state for these two engines — the same shape `#254` §4
already identified for `db_derived` ("no tier applies," not the weakest
tier) — rather than defaulting them to `catalog` and calling it done.

**Oracle is a subtler case, not degenerate but genuinely asymmetric**:
it has no `catalog`-tier floor at all (§4.2) — everything above self-scope
is grant-filtered or DBA-gated, so a credential that fails `read` cannot
fall back to *any* structural fact the way a Postgres credential can via
`pg_class`. This isn't the model not fitting; it's the model fitting with
one rung of the ladder simply absent.

**MySQL/MariaDB and SQL Server both fit the model but invert Postgres's
defaults** — MySQL/MariaDB has no free tier for object metadata at all
(§2.4); SQL Server has one, but it's a short enumerated whitelist rather
than a general "system catalogs are unfiltered" rule (§5.2). Anyone
building a `credential_capability` probe for either engine should not
assume "the catalog views" are safe to read for free the way Postgres's
`pg_namespace`/`pg_class` are — that assumption is specifically wrong for
both.

---

## 9 · Closing comparison

**Most resembles Postgres's catalog model: SQL Server.** Same four-level
containment stack (server/database/schema/table) with owner and security
boundary at every level from database down, a real DMV family cleanly
gated by an elevated permission (`VIEW SERVER/DATABASE STATE`) that maps
almost one-to-one onto Postgres's `pg_monitor`/`pg_stat_*` split, and even
a named, if narrower, `catalog`-tier whitelist. The one real inversion is
that SQL Server's *default* posture for ordinary object metadata is
`read`-tier where Postgres's is `catalog`-tier — a detail easy to port
wrong if assumed rather than checked.

**Least resembles Postgres's catalog model: SQLite** (with DuckDB close
behind, for the same structural reason — no privilege system at all,
against Postgres's four-tier grant-checked one). Oracle is the least
Postgres-like of the *client-server, multi-user* engines specifically
because it lacks any catalog-tier floor — a fundamentally different shape
from "the same four tiers, different names," which is closer to what SQL
Server and, with the inversion noted, MySQL/MariaDB actually are.

**Cheapest to generalize first, if a second engine were built:** SQL
Server, on the strength of §9's structural resemblance — the `catalog`/
`read`/`stats` mapping requires re-deriving which specific views are
`catalog`-tier (a short, named list rather than "everything") but the
*shape* of the probe (privilege-check self-function, grant-filtered
object views, DMV family behind an elevated permission) transfers with
the least conceptual rework. DuckDB is cheapest in a different sense —
there's no probe to build at all, since the model collapses to a boolean
— but "cheapest" there is really "not applicable," not "generalized."
MySQL/MariaDB is the next most tractable after SQL Server; Oracle's
missing catalog-tier floor makes it the most work among the four
real multi-user engines, since the `credential_capability` probe's whole
design premise (a catalog-tier denominator that survives even thin `read`
access) has no direct Oracle equivalent and would need its own design
decision, not just its own SQL.
