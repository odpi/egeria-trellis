# Reply: database credentials and access — the model is Egeria's, the probe is RE's

**Replying to:** `ASK-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md` (#251).
**From:** the design session, 2026-09-24, at the project owner's request.
**Read against:** `main` at `00f3b615`; Egeria Java source at
`/Users/dwolfson/localGit/egeria-v6/egeria` (matches the running platform
version, per `PROBES-2026-09-21.md`).
**Status:** an opinion for the project owner to turn into a ruling. Piece 1
of the ask (the visibility probe and banner) does not wait on any of it.

---

## 0 · What the source says, that the ask could not see

Four facts, each with a location, that decide most of the three questions:

1. **An asset can carry any number of Connections.** The `ResourceConnection`
   relationship is *at most one* asset per connection and *any number* of
   connections per asset (`OpenMetadataTypesArchive.java` ~`:279-302`, ends
   `connectedResources` / `resourceConnections`). "Several credential sets
   for one database" is therefore several `Connection` elements on the same
   asset, each wrapping its own embedded `SecretsStoreConnection` with its
   own `secretsCollectionName`. Nothing needs inventing on the Egeria side.
2. **The role of a credential set has a native slot.** `ResourceConnectionProperties
   extends LabeledRelationshipProperties` (`:32`): the *link* between the
   asset and each connection carries `label` and `description`. "surveyor",
   "reader", "admin" belong there — on the relationship, not on the
   connection and not in a new RE table.
3. **Egeria picks the connection by metadata security, not by capability.**
   `ConnectionHandler.getConnectionForAsset` (`generichandlers/ConnectionHandler.java:95-155`)
   fetches every connection on the asset, most recent first, then delegates
   to `securityVerifier.selectConnection(userId, asset, connections, …)`.
   The default security connector
   (`OpenMetadataAccessSecurityConnector.java:2628-2674`) keeps the
   connections the *requesting user may read* — zones and security tags on
   the `Connection` element — and returns one. **There is no capability or
   scope vocabulary on a Connection anywhere in the type model.** Which
   credential a native survey runs with is a function of who the engine
   host's user is and which Connection elements that user can see.
4. **Two defects in that selection.** Same method: when exactly one
   connection is visible it returns `connectionEntities.get(0)` — the first
   of the *unfiltered* list — instead of `visibleConnections.get(0)`
   (`:2666-2669`). An asset with an admin connection first and a surveyor
   connection second, seen by a user allowed only the second, gets the
   admin one. And when several are visible it picks one **at random**
   (`:2670-2674`, the comment says so). Neither affects RE-local surveys,
   where RE chooses; both affect every native survey.

Also confirmed from the incident itself: Postgres system catalogs
(`pg_namespace`, `pg_class`, `pg_attribute`) are readable by any role
regardless of `USAGE`/`SELECT` grants; only `information_schema` and
`pg_stats` are privilege-filtered. So a credential can always see *that* a
table exists even when it cannot read it. **The blind spot has a known
denominator**, which is what makes piece 1 exact rather than a warning.

---

## 1 · Q1 — where the model lives: Egeria, using what exists; RE keeps an index

**Recommendation.** One `Connection` per credential set on the database
asset; each with its own secrets collection in RE's own client-side store
(#185, one collection per *(resource, role)* rather than per resource);
the role on the `ResourceConnection` link's `label`; and each Connection
zoned or security-tagged so the security connector decides who may use it.
That is the whole model, and it is Egeria's.

RE's registry gains **`database_credentials` as a cache and index, not a
source of truth**: `(slug, connection_guid, role, secrets_collection_name,
last_probe_at, capability jsonb)`. Its job is to let the launcher list
choices and show the last probe without a round trip. The `DatabaseEntity`
keeps a *default* connection reference, which is what today's single
`db_user`/`db_password` pair becomes.

**Retire `databases.db_password`.** It is a clear-text `TEXT` column
(`registry.py:2185`) holding a value the secrets store already holds since
#185. The migration is: write the existing value into the resource's
collection if none exists, then drop the column. `db_user` can stay as a
display field.

**Do not build an RE-owned multi-credential store.** It would duplicate
three things Egeria already does — multiple connections, secrets
collections, access control on who sees which — and the native survey path
would ignore it anyway (fact 3).

---

## 2 · Q2 — enumeration and choice: different answers for the two routes

| Route | Who chooses | How |
|---|---|---|
| **RE-local survey** (rule B local, `executes_at: resource-explorer`) | RE, for the signed-in user | list the asset's connections through pyegeria (the security connector filters by user); read each link's `label`; pick the **least-privileged connection whose probed capability satisfies the step's requirement** (§3); let the user override from that list |
| **Egeria-native survey** (`executes_at: egeria`) | the security connector, for the engine host's user | RE cannot pass a connection; the action target is the asset. **Operational rule:** the survey engine's user must be able to see exactly one connection per asset — the surveyor one — via zones or security tags, because the default picks at random among several and, with one visible, may return the wrong one (fact 4). Until the upstream fixes land, this rule is what makes native surveys deterministic |

"Elevate the credential" stays out of scope for RE, as the project owner
said. RE's part is the **RFA to the database owner**, raised by the probe:
"connected as `egeria_user`: SELECT on 3 of 26 tables; grant SELECT on
`coco_ods.*` or register a broader connection on this asset." The RFA
carries the exact object list, which the probe has.

**pyegeria enumeration — verified 2026-09-24 by the coordinating session,
against `coco_pharma` (asset `5246aa50…`, the post-redeploy re-catalogue):**
`ConnectionMaker.find_assets(search_string=…, output_format='JSON')` returns
the asset with a `connections` array; each entry carries the relationship
type (`ResourceConnection`, `superTypeNames: ["LabeledRelationship"]`) and
its `relationshipProperties`, so the label *is* readable through this call.
For `coco_pharma` today: exactly one connection, the unlabelled template
one (`…::coco_pharma::Connection`), `relationshipProperties: null` — the
slot exists and nothing has set it yet, as expected. **Gotcha for whoever
builds this:** `ConnectionMaker.get_endpoints_for_asset` and
`find_connections` both returned empty for the same asset; use
`find_assets` and read the `connections` array. Also confirmed:
`AutomatedCuration.initiate_postgres_server_survey(postgres_server_guid)`
takes no connection argument, so the native route really is decided by the
security connector alone.

---

## 3 · Q3 — declare the requirement per step; derive the capability by probing, never by declaring

**Per step, not per analysis.** Steps open connections; analyses are
presentation. `requires_capability` goes on `StepInfo` next to
`fetch_cost`/`compute_cost`; the analysis catalog inherits the strongest
requirement among its steps for display, the same way it already presents
cost.

**Coarse vocabulary, four values:**

| Value | Means | Postgres check |
|---|---|---|
| `catalog` | system-catalog reads only | always true for a connected role |
| `read` | SELECT on the target tables | `has_table_privilege(role, t, 'SELECT')` per table; `has_schema_privilege(role, s, 'USAGE')` per schema |
| `stats` | the **monitor-gated** views only: `pg_stat_replication` standby rows, `pg_stat_statements`, other sessions' query text in `pg_stat_activity`, WAL/archiver detail — *not* the per-table activity counters (`pg_stat_all_tables`/`_indexes`), which are unprivileged and belong to `catalog` (live-verified as `egeria_user` on 2026-09-24, correcting this row's first wording); and *not* `pg_stats`, whose column statistics are gated by `SELECT` on the column and so belong to `read` | `pg_has_role(role, 'pg_monitor', 'member')` or `pg_read_all_stats`; verify each view empirically, per `DATABASE-STEP-CAPABILITY-AUDIT.md` |
| `write` | never for a survey; only for a future repair step | `has_table_privilege(…, 'INSERT')` — probed, never exercised |

Finer vocabularies (per-schema, per-table) are **results of the probe**,
not values of the requirement: a step requires `read`; the probe says
`read` holds on 3 of 26 tables; the gate reports the fraction.

**The probe is a Scouting-tier step, `credential_capability`**, rule C,
`fetch_cost: api`, `compute_cost: low`, one connection, catalog reads only,
no trial writes. Output, stored on `database_credentials.capability` and
published as a `ResourceMeasureAnnotation` on the survey report:

```
connected_as: egeria_user
schemas: visible 6 of 8   (pg_namespace vs has_schema_privilege USAGE)
tables:  select 3 of 26   (pg_class vs has_table_privilege SELECT), by schema
stats:   false            (not pg_monitor)
write:   false
probed_at: …
```

**The gate mirrors cost-tier gating.** A step whose requirement the
connected credential does not fully meet is never run silently. The
launcher labels it — "needs `read`; this credential has `read` on 3 of 26
tables" — with three choices: run partially and say so; pick another
visible connection; raise the RFA. This is the same shape as the
prerequisite proposal in design §17.1 and should share its rendering.

---

## 4 · The third completeness state

Every envelope for a database fact gains one more state alongside
*measured* and *not established*: **measured within credential scope**,
with the fraction attached. "How big is this database" on `coco_pharma`
renders "3 tables, 32 columns — as `egeria_user`, which can read 3 of 26
tables in 6 of 8 schemas" and never again "3 tables". The credential
identity is recorded on every survey row (`source` already distinguishes
who ran it; add `surveyed_as`) and on the report, which is also what
Egeria's own Postgres survey annotations say in their explanation text
("missing schemas indicate the survey userId lacks permission").

This is the `find-absence-as-answer` shape the ask named, with the
denominator supplied by the system catalog.

---

## 5 · What to file upstream

| Target | Item | Evidence |
|---|---|---|
| Egeria server | `selectConnection` returns `connectionEntities.get(0)` when exactly one connection is visible; should be `visibleConnections.get(0)` | `OpenMetadataAccessSecurityConnector.java:2666-2669` |
| Egeria server | random selection among several visible connections; propose deterministic order (most recent, or a `ResourceConnection.label` match against a request parameter such as `connectionRole`) so a survey can ask for the surveyor connection | `:2670-2674` |
| pyegeria | ~~confirm a read of an asset's connections with the link label~~ — **verified**: `find_assets(...)['connections']` (§2). Remaining item: `get_endpoints_for_asset` / `find_connections` return empty for an asset that has a connection; log as a gotcha or bug in `PYEGERIA_ISSUES.md` | §2 |

Neither server item blocks piece 1. Both should be fixed before the
operational rule in §2 is relied on in a multi-connection deployment.

---

## 6 · Sequence

1. **Now, no ruling needed:** `credential_capability` probe; banner and the
   third envelope state; `surveyed_as` on survey rows; the RFA. (Piece 1
   of the ask.)
2. **On the ruling for §1:** one Connection per credential set with a
   labelled link and a per-role secrets collection; `database_credentials`
   index; retire `db_password`; migrate `coco_ods`/`coco_pharma`, which
   need delete-and-recreate anyway (`PROBES-2026-09-21.md`).
3. **Then:** `requires_capability` on `StepInfo` for the database steps
   that exist — as declared on `main` after #274: `credential_capability`
   → `catalog`; `postgres_schema_and_stats` → `read` (it enumerates via
   the privilege-filtered `information_schema` and reads `pg_stats`);
   `postgres_operations` → `stats` (for its `db_resilience` member; the
   bundle declares its strongest requirement); column profile, nested
   columns, `sql_analysis`, data-class and reference matching → `read`; the
   launcher gate; connection choice for local runs.
4. **Alongside:** file the two Egeria issues; verify the pyegeria read.

---

## 7 · Follow-ups from `ASK-CREDENTIAL-GATING-AND-OMSECRETS-REFRESH.md` (#258), 2026-09-24

### 7.1 Credential-tier gating: the catalog-tier signal is real, and it is §16's Discovery gate

The project owner's idea — survey with the minimal-privilege credential
first, disqualify early, prompt for broader credentials only for what
survives — is the cost-tier gate of design §17.1 applied to a second axis,
and the "worth pursuing" signal at catalog tier exists because design §16.2
chose its Scouting/Discovery signals to need no row reads. With `catalog`
capability alone (`pg_namespace`, `pg_class`, `pg_attribute`,
`pg_constraint`, `pg_description`, `pg_partitioned_table`,
`pg_stat_all_tables` — all readable by any role regardless of grants) RE
can answer:

| Answerable at `catalog` | Not answerable without `read` |
|---|---|
| size: `reltuples`, `relpages`, `n_live_tup` per table | `pg_stats` bounds and frequent values (privilege-filtered) |
| structure and the FK graph; entity grain from PK composition | column profiles; data-class and reference-set matching |
| subject signals from names and comments; documentation coverage | actual date ranges on unpartitioned tables |
| coverage from partition bounds | gaps, cadence, spatial extent |
| activity: `n_tup_ins/upd/del`, last vacuum and analyze | quality dimensions beyond completeness-by-structure |
| the capability probe itself (§3) | — |

So `preliminary_fit` (design §16.3, Discovery, zero fetch) runs at catalog
tier and yields a pursue/disqualify verdict on subject, grain, size and —
where partitioned — coverage. Where it cannot decide, its envelope says
"needs `read` on N of M tables to answer", and **that state is the prompt
for broader credentials.** It is also the natural first UI for the pending
Connection model: the launcher's three choices from §3 (run partially and
say so; pick another visible connection; raise the RFA), with "pick
another" listing the asset's labelled connections. Build it as one axis
beside cost tier in the same gate, not as a separate flow: a step declares
`fetch_cost`, `compute_cost` and `requires_capability`, and the launcher
shows one combined reason.

### 7.2 `.omsecrets` refresh: a file edit takes effect on the next survey run, always

Traced through the engine host rather than the connector alone:

- `ConnectorBroker.getConnector` (`:314`) calls
  `connectorProvider.getConnector(connection)` (`:460`) — a **new instance
  on every call**.
- `SurveyActionServiceHandler` starts a fresh survey service per engine
  action (`:148`), and `SurveyAssetStore.getConnectorForAsset` goes through
  `connectedAssetClient` on every call (`:146`), so the asset connector and
  its embedded `SecretsStoreConnector` are new per survey run.
- `SecretsStoreConnector` initialises `secretsTimeout = new Date()` at
  construction (`:39`) and `checkSecretsStillValid` refreshes whenever
  `!secretsTimeout.after(now)` (`:105`) — so the **first** secret read of
  every new instance re-reads the file; the 60-minute
  `refreshTimeInterval` (`:165-170`) only governs a long-lived instance.

Net: no shared server state, no restart; an edit is live for the next
survey, and for an already-running survey after the interval. The
factor-of-60 correction in #258 stands and has no operational effect.

### 7.3 Two credential sources: unify the identity and the writer, not the storage

pyegeria has `save_client_side_secret` and `delete_client_side_secret` and
**no read** (`automated_curation.py:2119-2269`); Egeria deliberately
exposes no "get secret" API — secrets are read only by connectors. So RE's
local execution path cannot resolve a credential through Egeria, and the
`.omsecrets` file cannot be the single store for both paths.

What §1's model unifies is therefore the **name and the writer**: one
collection name per *(resource, role)*, persisted on the registry row
(`database_credentials.secrets_collection_name`, never re-derived — the
trap `PROBES-2026-09-21.md` filed), written to **both** places by RE in the
same operation. The registry side stops being a clear-text column and
becomes RE's own store — encrypted at rest, or the OS keychain on a
developer machine — and the `.omsecrets` collection is its projection for
the engine host. Drift is detectable by name: a collection missing on
either side surfaces as the reachability probe's `unresolvable_secret`
outcome (design §3 rule B), which is the strongest guarantee available
without a read API. Asking Egeria for a secret-read endpoint is the wrong
request; the right one, if any, is a *verify* call ("does collection X
resolve for asset Y") that returns a boolean, which is what `CHECK_ASSET`
already approximates.

---

## 8 · The structural floor is a Postgres property — declare it per engine

From the multi-engine catalog survey (branch
`re/database-engine-catalog-capability-survey`, 2026-09-24), which audited
MySQL/MariaDB, DuckDB, Oracle, SQL Server and SQLite against §3's
vocabulary. The finding that changes this reply: the "still see structure
with zero SELECT" fallback that #257 built on rests on `pg_class` and
`pg_namespace` being unprivileged. **That does not hold generally.**

| Engine | Structural floor without table `SELECT` | Value of `structural_floor` |
|---|---|---|
| PostgreSQL | yes — `pg_class`, `pg_namespace`, `pg_attribute` are readable by any role | `unprivileged` |
| DuckDB, SQLite | trivially — no per-user privileges; the file opens or it does not | `unprivileged` (degenerate: `catalog`/`read`/`stats` collapse to "opens", `write` to "not read-only") |
| Oracle | only with `SELECT_CATALOG_ROLE` or `SELECT ANY DICTIONARY`, which make `ALL_*`/`DBA_*` fully visible without row access | `role_grant` |
| SQL Server | only with `VIEW DEFINITION` at database scope | `role_grant` |
| MySQL / MariaDB | none — `information_schema` shows only objects the user holds *some* privilege on | `none` |

Three consequences, all now part of the design:

1. **The per-engine declaration (`REPLY-SCHEMA-AS-SUB-RESOURCE.md` §5)
   gains `structural_floor ∈ {unprivileged, role_grant, none}`**, with the
   grant named for `role_grant`. Default `unprivileged` for Postgres only.
2. **"Measured within credential scope" needs a denominator flag.** On
   `unprivileged` engines, and on `role_grant` engines where the grant is
   held, it reads "3 of 26 tables". On `none`, or `role_grant` without the
   grant, it reads **"3 tables visible; total not established"** — never a
   fraction, because M is unknown. That is a fourth completeness state and
   the envelope must render it distinctly from the other three.
3. **The RFA to the database owner is engine-specific.** Postgres: `SELECT`.
   Oracle: `SELECT_CATALOG_ROLE` first (structure, no rows), `SELECT`
   second. SQL Server: `VIEW DEFINITION`, then `SELECT`. MySQL: a surveyor
   account with `SELECT` on the databases of interest, because nothing
   cheaper exists. The `role_grant` path is the one to recommend to owners
   wherever it exists: it gives the probe an exact denominator without
   exposing a single row.

And a limit on §7.1: the catalog-tier "worth pursuing" gate works as
designed only on engines with a floor. On MySQL it degrades to "what this
credential can see", and the gate must say so rather than present a partial
inventory as the database.

Also confirmed from the Egeria source tree: native survey connectors exist
for Postgres, Oracle, SQL Server, DuckDB, DB2 and Unity Catalog; none for
MySQL/MariaDB or SQLite, so those two are rule-C engines end to end.

---

## 9 · Two identities, not one credential with more or fewer grants

**Project owner, 2026-09-25**, from an investigation summarised as *Database
Security & Access Architecture for Egeria Discovery and Survey* (multi-tier
access model; statistics as a privilege boundary; catalog estimates versus
pushdown profiling; how Atlan, Alation, Collibra, OpenMetadata and Unity
Catalog do it). This section adopts that model and revises §1 and §3
accordingly. Where the summary and this reply differ, the differences are
listed at the end so they can be settled by a probe rather than by
preference.

### 9.1 The model

| Identity | Holds | Scope | Owner and lifecycle | Asked for |
|---|---|---|---|---|
| **A — catalog** (`METADATA_READ`) | `catalog` + `stats`: topology, definitions, keys, ownership metadata, row and partition *estimates*, activity and session statistics | zero access to table records; on Oracle `SELECT_CATALOG_ROLE`, on SQL Server `VIEW ANY DEFINITION` + `VIEW SERVER STATE`, on Postgres `CONNECT` + `USAGE` + `pg_read_all_stats` | infrastructure; granted once, held long-term; the one Egeria's own surveys use | **at registration**, always — "surveying the unknown" (point 4) asks for this and nothing more |
| **B — data** (`DATA_READ`) | `read`: `SELECT` on target tables **including column statistics** (histograms, most-common values), which are derived from values and leak them | scoped to schemas or tables; default/future privileges so it survives migrations; timeouts, memory quotas and `TABLESAMPLE` as guardrails; a replica where one exists | the data owner; often time-limited and revocable independently of A | **at the gate**, when a `read`-tier step needs it and the user can see why |
| (C — export) | bulk extraction | a different risk class from B; its own connection and replica | — | only as dataset download from portals (design §7); out of scope for surveys |
| `write` | — | belongs to no survey identity, ever; inferred from grants, never exercised | — | — |

**Steps bind to an identity, not to a capability.** §3's four values remain
the vocabulary, but they resolve to two bindings: `catalog` and `stats` →
A; `read` → B. A survey that spans both binds two connections. On engines
with no privilege model (DuckDB, SQLite) A and B are the same "file opens"
boolean and the declaration says so.

**Estimated versus measured.** Catalog estimates (`reltuples`,
`n_distinct`, `null_frac`, histogram bounds where visible) are labelled
*estimated* in every envelope and annotation; pushdown aggregates
(`COUNT(*)`, `COUNT(DISTINCT)`, `MIN`/`MAX`, pattern checks) are *measured*.
That is the two-stage pipeline the summary recommends, and it maps onto
the design's existing envelope states with one new label.

### 9.2 What this changes in §1, §3 and §6

- §1's "one Connection per credential set" becomes **one Connection per
  identity, with two identity roles labelled on the `ResourceConnection`
  link: `catalog` and `data`.** RE's catalogue step creates A always and B
  only when supplied; each has its own secrets collection.
- §3's gate names the identity a step binds to as well as the capability:
  "needs the data identity (SELECT on 23 of 26 tables) — supply one, pick
  a broader connection, or run the catalog-tier steps only."
- §6's sequence: registration collects A only, with the form saying so in
  plain words ("a read-only account that can see the catalog and
  statistics; table data access is not needed yet"); the probe confirms
  what was given; B is requested later, at the gate.
- Native surveys and dual connections (summary §5 versus Egeria as it runs):
  with A and B both attached to an asset, the default security connector
  picks at random among what the engine host's user can see (§0 fact 4).
  **Until that is fixed upstream, B must be zoned away from the engine
  host's user**, so Egeria's survey always lands on A. Point 3 of the
  project owner's note — Egeria has surveyor-level, not user-level, access
  — is then true by construction, not by luck.

### 9.3 Where the summary and this reply differ — settle by probe

| Claim in the summary | This reply | How to settle |
|---|---|---|
| Postgres: membership in `pg_read_all_stats` "enables `pg_stats` without table read" (§1 and §2) | **Settled 2026-09-25, live on the dev platform: the summary is wrong.** Scratch role `NOINHERIT` with `pg_read_all_stats` + schema `USAGE` and no table `SELECT`, table freshly `ANALYZE`d (9 `pg_stats` rows as superuser): `pg_stats` returned **0 rows**; after granting `SELECT` on the table alone, **9 rows**. `pg_stats` is gated by its own `has_column_privilege` filter, not by `pg_read_all_stats`/`pg_monitor`. Column statistics belong to identity B on Postgres. (The role that exposes them everywhere is `pg_read_all_data`, which *is* table read.) Scratch role dropped after the probe | done — coordinating session, `coco_pharma`, `coco_ods.coco_locations` |
| MySQL: Tier 1 as "`SELECT` on `information_schema.*`" | not grantable as written; MySQL derives information-schema visibility from privileges on the underlying objects (the engine survey's *no floor* finding). Tier 1 needs `PROCESS` + `SHOW VIEW`, or in practice a reader account | already recorded in the engine survey; a one-line correction to the summary |
| Column statistics belong to Tier 2 (summary §2 implication) | agreed — and this **corrects the coverage design** (`multi-resource-questions-design.md` §16.2), which called `pg_stats` bounds "free at Scouting". They are free only on tables identity A can read, which by definition is none; the catalog-tier coverage estimate comes from partition bounds, names, file-name dates and Parquet footers, and everything else waits for B | fixed on branch `re/design-coverage-needs-data-identity` |

### 9.4 Additions the summary should carry

- **Identities have owners and lifecycles**: `granted_by`, `expires_at` on
  the registry index; an expired B is `unresolvable_secret`, not a resource
  failure.
- **Application name on every connection** ("resource-explorer scouting
  as egeria_user"), so the target's audit log shows who and why — the thing
  that makes owners willing to grant A.
- **Identity is provenance**: every survey row and published annotation
  records the identity that produced it; two runs under different
  identities are never diffed as if the database changed.
- **Server scope** (listing databases) is A with server scope, not a fourth
  identity.
- **RE stores no secret it did not create**: for identities Egeria already
  holds, RE keeps the collection name and role only.
