# Scratch test of Egeria's JDBC cataloguer, 2026-10-04 (overnight, unattended)

Runner: Claude Sonnet 5.5 (subagent). **STOPPED AT STEP 5 (after the second forced refresh); steps 6 and 7 were NOT run and nothing was torn down.** The stop rule fired on the first surprise that mattered: a JSON array in the catalog target's `configurationProperties` was **not honoured** by the cataloguer (question d), so the include list the rest of the plan depends on did not work. State is left exactly as it is; cleanup is at the end.

No credential value appears in this note. Times are CDT local with the UTC equivalent where the platform printed UTC (CDT = UTC-5).

## Environment proved

- Worktree `/Users/dwolfson/localGit/egeria-v6/trellis-re-scratch-test`, branch `re/scratch-cataloguer-test-evidence` from `origin/main` f0ed685f. `resource_explorer.__file__` is inside that worktree; `pyegeria` has no `__version__` attribute here (the lever note says 6.1.15).
- Egeria source read-only at `/Users/dwolfson/localGit/egeria-v6/egeria` (not built, not run). The running platform is the quickstart container `quickstart-egeria-main`; integration daemon `qs-integration-daemon`; view server `qs-view-server`.
- Scripts: scratchpad `.../scratchpad/sct/` (not committed). Clients built as `egeria_resync._connect` does, from `get_config().egeria`.

## Step 0: baseline (21:29 CDT, 02:29 UTC)

- JDBC cataloguer connector, from `ServerOps.get_integration_daemon_status()`: name `JDBCDatabaseCataloguer`, `connectorStatus` WAITING, `lastRefreshTime` 2026-10-05T02:18:59.935Z, `minMinutesBetweenRefresh` 60. All connectors but one (`OpenAPICataloguer`, REFRESHING, `minMinutesBetweenRefresh` 10080, unrelated) were WAITING.
- **Observation, not mine:** every connector's `lastRefreshTime` was 02:18 to 02:19 UTC, about ten minutes before my first read. The daemon (or its connectors) was restarted or reconfigured at about 21:18 CDT by someone else. I did not touch it.
- JDBC cataloguer catalog targets (`get_catalog_targets(JDBC, body={"class":"ResultsRequestBody","graphQueryDepth":0})`): "No elements found".
- Name search `scratch_cat_test` (`get_guid_for_name`, `get_assets_by_name`): "No elements found".

## Step 1: throwaway database, role, secrets file

- Postgres (as `postgres`, `docker exec -i egeria-shared-postgres psql -U postgres -p 5442 -v ON_ERROR_STOP=1`): `CREATE ROLE scratch_cat_role LOGIN PASSWORD '<random, in memory>'`, `CREATE DATABASE scratch_cat_test OWNER scratch_cat_role`. My first script then failed on a wrong relative path to the DDL file (no data harm, role and database already existed), so the role password was reset with `ALTER ROLE scratch_cat_role PASSWORD ...` (my own role) and the script continued. The password was never printed.
- DDL in `scratch_cat_test` (quoted identifiers, 3 rows each):
  - `"a_b"."t_ab"(id int, name text)`; `"aXb"."t_axb"(id int, label text)`
  - `"s_x"."x_y"(only_in_x_y int, a text)`; `"s_x"."xZy"(only_in_xzy int, b text)`
  - `"pct"."p%t"(id int, "c%d" text)`
  - `"plain"."orders"(order_id int, amount numeric)`; `"plain"."customers"(customer_id int, cname text)`
  - `GRANT USAGE ON SCHEMA ... TO scratch_cat_role; GRANT SELECT ON ALL TABLES IN SCHEMA ... TO scratch_cat_role;`
- Read-back: the seven tables above exist; `s_x.x_y` has columns `only_in_x_y, a`, `s_x.xZy` has `only_in_xzy, b`, `pct.p%t` has `id, c%d`.
- Secrets file: ONE new file `scratch-cat-test.omsecrets` in `.../quickstart-platform-data/secrets/`, written with `omsecrets_store.write_credential("scratch_cat_test::PostgreSQL Secret", "scratch_cat_role", <password>, path=...)` (shape: `secretsCollections.<name>.{displayName, refreshTimeInterval: 60, secrets.{userId, clearPassword}}`). Read-back by `ls`: the host directory and `docker exec quickstart-egeria-main ls /deployments/secrets` both list the file next to the four existing ones (no other file touched, none read).

## Step 2: template publish (question f)

Replicated `_create_postgres_element_from_template` with `AutomatedCuration.get_template_guid_for_technology_type("PostgreSQL Relational Database")` = `3d398b3f-7ae6-4713-952a-409f3dea8520`, then `create_elem_from_template({"class":"TemplateRequestBody","templateGUID":...,"isOwnAnchor":True,"deepCopy":True,"placeholderPropertyValues":{databaseName: scratch_cat_test, serverName: host.docker.internal:5442, hostIdentifier: host.docker.internal, portNumber: 5442, databaseUserId: scratch_cat_role, description: ..., databasePassword: <redacted>, secretsCollectionName: "scratch_cat_test::PostgreSQL Secret", secretsStorePathName: /deployments/secrets/scratch-cat-test.omsecrets}})`. No server asset was created or needed. Nothing was registered in RE's registry.

Created at 21:30:11 CDT, database GUID **`ed49aacd-8dca-44cc-a224-c20034353f70`**, read back with `AssetMaker.get_asset_by_guid`:

- type `RelationalDatabase`, qualifiedName `PostgreSQL Relational Database::host.docker.internal:5442::scratch_cat_test`, displayName `scratch_cat_test`, `deployedImplementationType` "PostgreSQL Relational Database".
- Attached subgraph (all created by the template): `VirtualConnection` `...::scratch_cat_test::Connection` (38484457-86f3-432e-bb0b-cf06184ec34e, configurationProperties `{databaseName: scratch_cat_test}`); its connector type `Egeria::ResourceConnector::RelationalDatabase::JDBC` (provider `org.odpi.openmetadata.adapters.connectors.resource.jdbc.JDBCResourceConnectorProvider`, existing element 64463b01-...); endpoint `...::Endpoint` with `networkAddress` `jdbc:postgresql://host.docker.internal:5442/scratch_cat_test`; nested `Connection` `...::SecretsStoreConnection` (configurationProperties `{secretsCollectionName: "scratch_cat_test::PostgreSQL Secret"}`) with connector type `Egeria::SecretsStoreConnector::YAMLFile` and endpoint `...::SecretStoreEndpoint` with `networkAddress` `/deployments/secrets/scratch-cat-test.omsecrets`.
- The cleartext password does not appear anywhere in the read-back (checked by string search).
- **Defect, also in RE's own publish:** `description` read back as the unsubstituted `~{databaseDescription}~` and `versionIdentifier` as `~{versionIdentifier}~`. RE (and I, copying it) send the placeholder `description`; the template's name is `databaseDescription`. Cosmetic for the cataloguer, visible on screen.

## Step 3: adoption setup (question b)

Rule read in `RelationalDatabaseCataloguer.java` (df82f4fe): schema qualifiedName = `<database qualifiedName>::<schema name>` (`catalogSchemas`, `:175`); table = `<parent qualifiedName>::<table name>` where the parent is the schema's qualifiedName for the schema pass and the database's for the database-level pass (`catalogTablesAndViews`, `:342`); column = `<table qualifiedName>::<column name>`; the schema's schema type gets `<schema qualifiedName>_schemaType`. The cataloguer creates schemas as `DeployedDatabaseSchema` anchored to the database with a `DataSetContent` link (database at end 1), the schema type via `Schema` relationship, tables as `RelationalTable` via `AttributeForSchema` on that schema type, anchored to the database.

Pre-made at 21:31 CDT with the same strings (no external source, as RE publishes):

| element | GUID | qualifiedName |
|---|---|---|
| DeployedDatabaseSchema `plain` | 4a5b5807-9bae-4e8d-8391-71f91073de24 | `<DB QN>::plain` |
| RelationalDBSchemaType | 6a0559b5-1d37-467d-b435-a5de9e93c353 | `<DB QN>::plain_schemaType` |
| RelationalTable `orders` | f9134b32-e1c6-4063-902b-b98edd2303dc | `<DB QN>::plain::orders` |

(`<DB QN>` = `PostgreSQL Relational Database::host.docker.internal:5442::scratch_cat_test`.) My first call used a wrong relationship type name (`AssetSchemaType`, then `SchemaRelationship`); the server refused both before creating anything; the type is `Schema`. The schema asset from the first attempt existed already and was reused.

## Step 4: attach (question d)

21:32:51 CDT: `AssetMaker.add_catalog_target(JDBC, ed49aacd-..., body)` with `NewRelationshipRequestBody`, `properties` = `{class: CatalogTargetProperties, catalogTargetName: "scratch_cat_test", deleteMethod: "LOOK_FOR_LINEAGE", configurationProperties: {includeSchemaNames: ["a_b","aXb","s_x","pct","plain"]}}`. pyegeria's validator keeps `deleteMethod` and the array (checked offline first). Zero targets existed immediately before. Relationship GUID **`38722408-8dd1-4bc0-b7ab-dfe57e080ca0`**.

Read-back (`get_catalog_targets` with the working body): one target, `catalogTargetName` scratch_cat_test, `deleteMethod` LOOK_FOR_LINEAGE (persisted), and **`includeSchemaNames` comes back as a STRING**, the Java `toString()` of an `ArrayTypePropertyValue` (starts `ArrayTypePropertyValue{arrayCount=5, arrayValues=ElementProperties{...`, ends `typeName='array<string>'}`), not a list. The repository did store an array (it knows `array<string>` with five members) but the view-server read path flattens it to text.

## Step 5: refresh (questions a, b, c-partial, d, e)

Forced refresh: `ServerOps(DAEMON, ...).refresh_integration_connectors("JDBCDatabaseCataloguer", "qs-integration-daemon", 120)`. It is synchronous (the call returns when the refresh is done, 16 s here). `RuntimeManager.refresh_integration_connector` was not used. `lastRefreshTime`: 02:18:59.935Z (baseline) -> **02:33:36.061Z** (forced 1, 21:33 CDT) -> **02:35:18.468Z** (forced 2, 21:35 CDT).

**Daemon log for forced refresh 1** (secrets-checked: no credential in any line; 93 lines mention `scratch_cat`): refreshing target, "preparing to extract metadata", `JDBC-INTEGRATION-CONNECTOR-0012` "Transfer complete for table ..." for exactly these tables and **no line for any schema**: `t_axb`, `t_ab`, `p%t`, `customers`, `orders`, `xZy`, `x_y`, then "successfully extracted". No exception or error lines. Forced refresh 2 produced the same 91 lines.

**What exists under the database after refresh 1 (read back by `find_assets` / `SchemaMaker.find_schema_attributes`, search `scratch_cat_test`; 86 elements; identical GUID set after refresh 2, so idempotent):**

- **No schema element at all** for `a_b`, `aXb`, `s_x`, `pct` or `plain`. So no include-listed schema matched: **the array was not honoured** (d). The cataloguer received something that matched no schema name (consistent with it getting the flattened string as one list entry; the lever note's source read says a `String` becomes one entry).
- Tables created at DATABASE level (qualifiedName `<DB QN>::<table>`, anchored to the database): `customers`, `orders`, `p%t`, `t_ab`, `t_axb`, `x_y`, `xZy`. This is the lever note's leak 1, confirmed: with the schema filter matching nothing, every schema's tables still arrive directly under the database via `getTables(catalog, null, ...)`. No `pg_catalog`/`information_schema` table appeared (system tables are dropped by the table-type filter, as inferred).
- **Hazard (a), the table-pattern half, confirmed on columns:** `<DB QN>::x_y` has columns `a`, `b`, `only_in_x_y`, `only_in_xzy`: it holds the columns of `xZy` too (`_` matched `Z`). `<DB QN>::xZy` has only its own (`b`, `only_in_xzy`), because `xZy` has no `_`. **`<DB QN>::p%t` has 67 columns**: its own `id`, `c%d` plus the columns of every pg_catalog table whose name matches `p%t` (`pg_cast`, `pg_constraint`, `pg_largeobject`, `pg_statistic_ext`, `pg_stats_ext`-style views and others: `castsource`, `conname`, `loid`, `stxname`, `tablename`, ...). So `%` in a real name is a wildcard and pulls in system-catalog columns, not just user ones. A name with `%` therefore does not "behave".
- Schema-name half of (a) (`a_b` vs `aXb` mis-parenting): **not tested**, because no schema was processed. `t_ab` and `t_axb` each have their own columns only, at database level.
- **Adoption (b):** the pre-made schema `plain` is **gone** (`get_asset_by_guid` and `find_assets` return not found, also with `forLineage`); its schema type `...::plain_schemaType` and its table `...::plain::orders` are **still ACTIVE, orphaned** (anchored to the database, no schema above them). This is the lever note's stale-delete plus anchor prediction: the schema was deleted as "stale" because it was not in the processed set (the filter matched nothing), and its dependents were left behind. This is NOT a clean adoption test: the cataloguer never reached the schema pass, so whether a *properly* included pre-made `plain` would be adopted is open. What is established: my pre-made `orders` (`<DB QN>::plain::orders`) was not touched or updated, and a second, different `orders` at `<DB QN>::orders` was created by the database-level pass. The qualifiedName rule I used is the cataloguer's (the database-level table names above follow it exactly).
- **Delete method (c):** the stale schema delete under `LOOK_FOR_LINEAGE` (no lineage on it) left the schema unreadable, consistent with a soft delete; not confirmable further. Orphaned dependents confirmed (above).
- Ownership of the work: none of this touched anything I did not create.

**Natural interval (e):** the configured value read from the daemon status is `minMinutesBetweenRefresh: 60` for `JDBCDatabaseCataloguer` (others: 60, one 840, one 10080). **Measured natural refreshes** of the JDBC cataloguer, from the daemon status: 02:18:59 UTC (daemon start), 02:52:43 UTC, 03:26:26 UTC. The two natural gaps are **33 min 44 s and 33 min 43 s**, so the real natural interval is about **34 minutes, not 60**; my forced refreshes (02:33:36, 02:35:18) did not move the schedule. (`minMinutesBetweenRefresh: 60` is therefore not the cycle; I did not find what is. Not established.) Both natural refreshes re-processed my target idempotently (same 86 GUIDs, then 87, see below).

**Side effect I did not create directly:** at the 02:52:43 cycle the `SecretsStoreCataloguer` connector (a different connector, watching `/deployments/secrets`) catalogued my new secrets file: log line `BASIC-FILES-INTEGRATION-CONNECTORS-0016 ... created the DataFile /deployments/secrets/scratch-cat-test.omsecrets (9a1833ac-ed9d-4fb2-85f7-8282c19dc6f2)`, and a `SecretsCollection` element `b655cf00-9d61-411a-9e84-8ea51786c0c7` with qualifiedName `SecretsCollection::9a1833ac-ed9d-4fb2-85f7-8282c19dc6f2::scratch_cat_test::PostgreSQL Secret` now exists (the 87th element in the later dumps). Whether that cataloguer removes them when the file is deleted is not established; they are in the cleanup list. Slice B note: any credential file RE drops in that directory is catalogued by Egeria as a DataFile and SecretsCollection without being asked.

## Question -> Answer -> Evidence

| Q | Answer | Evidence |
|---|---|---|
| a. LIKE-pattern hazard | **Partly confirmed.** Table-name patterns are live: `x_y` returned `xZy`'s columns, and `p%t` matched about 65 pg_catalog columns on top of its own two. Schema-name collision (`a_b`/`aXb`) not tested: no schema got processed. | Step 5 read-back: 86 elements, x_y columns a/b/only_in_x_y/only_in_xzy; p%t 67 columns |
| b. adoption | **Not established.** The pre-made schema was deleted as stale, its schema type and table orphaned; the cataloguer never reached the schema pass. A same-QN pre-made table was not touched; the database-level pass created a different `orders`. | Step 5 |
| c. leaving out | **Partly observed by accident:** a schema outside the processed set is deleted (soft, with LOOK_FOR_LINEAGE and no lineage) and its schema type and tables stay behind ACTIVE and orphaned. The planned step 6 was not run. | Step 5 `plain` vs `plain_schemaType`/`plain::orders` |
| d. arrays | **No.** The array is stored (the read path prints `array<string>`) but reads back as one flattened string and the cataloguer matched no schema by it. `deleteMethod` persists as written. | Step 4 read-back, Step 5 no schema lines |
| e. interval, forced refresh | Forced: works, synchronous, `ServerOps.refresh_integration_connectors(connector_name, server, timeout)`. Configured interval 60 min. Natural: about 34 min (33:44 and 33:43 between natural refreshes), not the configured 60. | Step 5 |
| f. connection from RE's template | **Yes.** The template gives a VirtualConnection with the JDBC resource connector type, a JDBC endpoint and a nested YAML secrets-store connection; the cataloguer opened it and read the database. | Step 2; daemon lines "JDBC resource connector for database scratch_cat_test" |

## What surprised me

1. The array did not survive to the connector (d). The brief and the lever note both expected arrays to be the working lever; they are not, through this client and this platform build.
2. Leak 1 is worse than the note feared: with a non-matching schema filter, ALL tables of ALL schemas still arrive at database level, anchored to the database, and the filter on schemas does not scope that pass at all.
3. `%` in a name pulls in system-catalog columns.
4. A schema that falls outside the filter is deleted while its table and schema type stay ACTIVE: removing a schema from scope leaves orphans, exactly the lever note's highest-risk unknown.
5. The daemon had been restarted by someone at about 21:18 CDT.
6. RE's template publish leaves `description` and `versionIdentifier` as `~{...}~` placeholders (name mismatch `description` vs `databaseDescription`).

## What slice B's commit can and cannot rely on

Can rely on: the template publish gives a database element the JDBC cataloguer can connect to and read; attaching once with `add_catalog_target` works; `deleteMethod` set on the target persists; a forced refresh is available, synchronous, and idempotent on unchanged data; the refresh is per connector and is fast for a small database (16 s).

Cannot rely on (as built): a JSON array in `configurationProperties` as the schema allow-list (the filter matched nothing); any scoping of what appears at database level; names containing `_` or `%` being treated as literals; a removed schema taking its tables with it; adoption of pre-made schema/table elements (untested at the schema pass).

## Not established

- Whether a plain STRING value `includeSchemaNames: "plain"` (one name) is honoured (the lever note's source read says yes); whether the same array fails when written through another route.
- Schema-name collision parenting (`a_b` vs `aXb`), schema-level table collisions, and adoption of a correctly included pre-made schema and table.
- What `LOOK_FOR_LINEAGE` does when a lineage relationship exists; what a deleted-then-re-included schema does; the "leave out then add back" cycle.
- Whether the orphaned `plain_schemaType` and `plain::orders` would be re-linked or duplicated if `plain` came back.
- The teardown behaviour (steps 6 and 7 were not run).
- Why the daemon restarted at 21:18 CDT.
- What drives the 34-minute natural cycle (the status says 60).

## State left behind (NOT torn down) and cleanup

NOT torn down: the run stopped at step 5. Elements and objects that exist now:

- Postgres (`localhost:5442`): database `scratch_cat_test`, role `scratch_cat_role`.
- Secrets directory: file `scratch-cat-test.omsecrets` (only this file is mine).
- Egeria: RelationalDatabase `ed49aacd-8dca-44cc-a224-c20034353f70` with its template-created VirtualConnection (38484457-86f3-432e-bb0b-cf06184ec34e), nested SecretsStoreConnection (a5ef08e5-c3d6-4b93-8fba-0120ec65c847) and two endpoints (4148baa7-12ca-4896-919e-a60e763c5eee, 7b2df6ab-e870-4023-aa97-fbacd721872b); the CatalogTarget relationship `38722408-8dd1-4bc0-b7ab-dfe57e080ca0` from the JDBC cataloguer (still attached: the JDBC cataloguer will refresh it hourly against my throwaway database only, and re-process it idempotently); about 85 cataloguer-created tables/columns anchored to the database (86 elements with the database, 87 with the secrets collection); the SecretsStoreCataloguer's DataFile `9a1833ac-ed9d-4fb2-85f7-8282c19dc6f2` and SecretsCollection `b655cf00-9d61-411a-9e84-8ea51786c0c7` for my secrets file; my orphans `6a0559b5-1d37-467d-b435-a5de9e93c353` (schema type) and `f9134b32-e1c6-4063-902b-b98edd2303dc` (table).

Cleanup, in order: (1) `AssetMaker.detach_catalog_target(<JDBC 70dcd0b7-9f06-48ad-ad44-ae4d7a7762aa>, <ed49aacd-...>, body)` (or `remove_catalog_target("38722408-...", body)`); (2) force one refresh with `ServerOps.refresh_integration_connectors("JDBCDatabaseCataloguer", "qs-integration-daemon", 120)` and read what the cataloguer does; (3) delete the orphan table and schema type with `SchemaMaker.delete_schema_attribute` / `delete_schema_type`, then `AssetMaker.delete_asset("ed49aacd-...", {"class":"DeleteElementRequestBody"})` (cascade delete of anchored members: check by searching `scratch_cat_test`); delete the template connections/endpoints if they survive (search `scratch_cat_test`); (4) `docker exec egeria-shared-postgres psql -U postgres -p 5442 -c "DROP DATABASE scratch_cat_test"` then `DROP ROLE scratch_cat_role`; (5) remove ONLY `/Users/dwolfson/localGit/egeria-v6/egeria-workspaces-fs/runtime-volumes/quickstart-platform-data/secrets/scratch-cat-test.omsecrets`; (6) after step 5, force a refresh of `SecretsStoreCataloguer` and see whether it removes its DataFile and SecretsCollection; delete them by GUID if not; (7) read back that nothing named `scratch_cat_test` or `scratch-cat-test` remains.

NOT torn down: state and cleanup commands above.

The platform is NOT torn down: state and cleanup commands above.
