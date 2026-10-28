# Read-back: how the JDBC cataloguer links the schema and table elements it creates, 2026-10-06 (unattended)

Runner: Claude Sonnet 5.5 (subagent). **COMPLETED: steps 0, 1, 2, 3 and the teardown all ran; no safety stop fired.** No credential value appears here (the throwaway role's password lived in memory only). Local times are CDT (UTC-5); Egeria's log is Z (run 21:19-21:36 CDT = 02:19-02:36Z on 2026-10-06).

## Header
- RE: worktree `/Users/dwolfson/localGit/egeria-v6/trellis-re-parentlink`, branch `re/readback-cataloguer-parent-link-evidence`, created from `origin/main` 51c9caec and fast-forwarded to **71c38804** (PR #497) before this note was written. `git fetch origin` succeeded. Only pyegeria, `omsecrets_store.write_credential`, the surveyor's `_ensure_own_secrets_store_guid` / `_create_postgres_element_from_template` helpers, `docker exec ... psql` and read-only reads were used; no RE server, registry (temp SQLite URLs, `PGVECTOR_PORT=1`), `.env` content (loaded into the python process only, never printed) or the shared trellis checkout.
- Egeria: container `quickstart-egeria-main`, image `egeria-quickstart-platform:local` (id `d1c48ab6eaa0`, **created 2026-10-05T14:19:48Z**), container `StartedAt` 2026-10-06T02:09:12Z, unchanged at the end of the run. Daemon `qs-integration-daemon`: JDBC cataloguer `lastRefreshTime` 02:11:22Z at baseline; only `OpenAPICataloguer` (x2) not `WAITING` (unrelated, as in the earlier runs).
- Egeria source read-only at `/Users/dwolfson/localGit/egeria-v6/egeria` `origin/main` **b86b8f94** (`git show` / `git grep` only).
- Identity: every call as `erinoverview`; the cataloguer wrote as `dbcatnpa`.
- Scripts (not committed): `/private/tmp/claude-501/-Users-dwolfson/2a2f2e43-62b8-42b6-9039-7d61b315c869/scratchpad/sct6/`.

## Source read (egeria `origin/main` b86b8f94)
Base path `open-metadata-implementation/adapters/open-connectors/integration-connectors/jdbc-integration-connector/src/main/java/org/odpi/openmetadata/adapters/connectors/integration/jdbc/`.

**The schema** (`RelationalDatabaseCataloguer.java`):
- `catalogDatabaseContents` (133-162): first `catalogTablesAndViews(databaseElement, databaseQN, databaseGUID, null)` (tables directly under the database, line 141), then `catalogSchemas` (144-145), then per schema `catalogTablesAndViews(databaseElement, schemaQN, schemaGUID, schemaDisplayName)` (159).
- `catalogSchemas` (207-227): qualifiedName = `databaseQualifiedName + "::" + schemaName` (220); only schemas passing `transferCustomizations.shouldTransferSchema` (215).
- `getOrCreateSchema` (233-272): looks up by that qualifiedName (`getAssetByUniqueName`, 241); if absent, `createAsset` with `NewElementOptions`: `setAnchorGUID(databaseGUID)`, `setIsOwnAnchor(false)`, `setParentGUID(databaseGUID)`, **`setParentAtEnd1(false)`**, **`setParentRelationshipTypeName(DATA_SET_CONTENT_RELATIONSHIP)`** (255-259). Type is `DeployedDatabaseSchema` (client built at 119); properties `displayName` = schema name, `qualifiedName`, additionalProperties `jdbc.catalog`, `jdbc.schema` (275-287).
- Handler: `omf-metadata-server/.../handlers/MetadataElementHandler.java` `createParentRelationships` 2292-2317: `parentAtEnd1` true links (parent, new element); **false links (new element at end 1, parent at end 2)**. The type check at 2151-2166 validates the parent against `endDef1` (parentAtEnd1) or `endDef2` (false) of the relationship def. `DataSetContent` is "The assets that provide data for a data set" (`OpenMetadataType.java:2217`); `SimpleCatalogArchiveHelper.addDataSetContent` (4494-4519) builds it with the **data set at end 1 and the data content at end 2**. `JDBCIntegrationCatalogTargetProcessor.java` 69-90 states `DeployedDatabaseSchema` descends from `DataSet`, `RelationalDatabase` from `DataStore`.
- So the source says: schema = END 1 of `DataSetContent`, database = END 2, schema anchored to the database.

**The schema type and the tables** (`RelationalDatabaseCataloguer.java`):
- `resolveSchemaTypeGUID` (424-463): reads the parent asset (database OR schema); if it has no schema type, creates a `RelationalDBSchemaType` with `qualifiedName = parentQualifiedName + "_schemaType"` (451), `displayName "Schema Type for <parent displayName>"` (452), `setAnchorGUID(anchorAsset = the database)`, `setIsOwnAnchor(false)`, `setParentGUID(parent asset)`, **`setParentAtEnd1(true)`**, **`SCHEMA_RELATIONSHIP`** (`OpenMetadataType.java:4952`, type name `Schema`) (444-448). Asset at end 1, schema type at end 2.
- `createTable` (492-521): table `RelationalTable`, `qualifiedName = parentQualifiedName + "::" + tableName` (387), anchor = the database (`anchorAsset`, 500; the Javadoc at 361 says tables always anchor to the database even under a schema), parent = the schema type, **`setParentAtEnd1(true)`**, **`ATTRIBUTE_FOR_SCHEMA_RELATIONSHIP`** (`AttributeForSchema`), `AttributeForSchemaProperties` min/max cardinality 1/1 (502-508). Schema type at end 1, table at end 2. Columns (`catalogColumns` 704-729) hang off the table the same way in `NestedSchemaAttribute` (table end 1).
- Nothing in these files links a schema or table to a software server, and `AssetSchemaType` appears nowhere as a type name in the Java sources of `origin/main` (`git grep`: no match).
- Not in scope but relevant: `catalogSchemaContents` (180-191) is the SCHEMA-kind path (the target IS a schema element): it creates no schema and hangs `<schemaQN>::<table>` tables off a schema type created with `parent = the schema asset`, anchored to **the schema asset** ("rather than to a database asset", Javadoc 174-175; note the `anchorAsset` argument there is the schema element).

## Step 0: baselines and setup (21:19-21:22)
- `AssetMaker.get_catalog_targets(70dcd0b7-9f06-48ad-ad44-ae4d7a7762aa, body={"class":"ResultsRequestBody","graphQueryDepth":0})` -> `No elements found`. Daemon: 52 connector entries, 50 `WAITING`, 2 `REFRESHING` (`OpenAPICataloguer`). Name searches `scratch_cat_test6`, `scratch-cat-test6`, `scratch_cat_role6` -> none. Secrets directory file NAMES before: `coco-user-directory.omsecrets`, `egeria-servers.omsecrets`, `integration.omsecrets`, `resource-explorer.omsecrets`. (Postgres already had `scratch_cat_test3` / `scratch_cat_role3` from the first rehearsal; not mine, not touched.)
- Postgres 5442: `CREATE ROLE scratch_cat_role6 LOGIN PASSWORD '<random, memory only>'`, `CREATE DATABASE scratch_cat_test6 OWNER scratch_cat_role6`. DDL (quoted identifiers, no password): schemas `plain` and `other`; `plain.orders(order_id int, amount numeric)`, `plain.customers(customer_id int, cname text)`, `other.o_t(id int, v text)`, `public.pub_t(id int, v text)`; 3 rows each; `GRANT USAGE ON SCHEMA "plain","other","public"` and `GRANT SELECT ON ALL TABLES IN SCHEMA` the same to the role. Read-back: 4 user tables, 3 rows each.
- Secrets file `scratch-cat-test6.omsecrets` via `omsecrets_store.write_credential("scratch_cat_test6::PostgreSQL Secret", "scratch_cat_role6", <pw>, path=...)` -> `True`. Throwaway secrets-store graph (the surveyor's `_ensure_own_secrets_store_guid` with the qualified-name constant overridden): Asset `4ac4a2f7-b143-4d26-aa1a-620c2ec958ad`, Connection `53d5ca96-59d0-457c-bc79-b0aa5c1fa5cb`, Endpoint `15621eeb-6b6a-46d2-b587-71d017a138c0`, ConnectorType `ec3c81f5-1652-48e0-b61f-6036eeede666`.
- **Database element from the template** (21:21:53), RE's `_create_postgres_element_from_template("PostgreSQL Relational Database", ...)`: `TemplateRequestBody` template `3d398b3f-7ae6-4713-952a-409f3dea8520`, `isOwnAnchor` true, `deepCopy` true, placeholders `databaseName scratch_cat_test6`, `serverName host.docker.internal:5442`, `hostIdentifier host.docker.internal`, `portNumber 5442`, `databaseUserId scratch_cat_role6`, `description`/`databaseDescription` `scratch_cat_test6 throwaway database`, `versionIdentifier not recorded`, `secretsCollectionName scratch_cat_test6::PostgreSQL Secret`, `secretsStorePathName /deployments/secrets/scratch-cat-test6.omsecrets` (no `databasePassword`). No zone written. -> **database `d7b8fe0a-7f5e-47d5-bc8f-dbfdbcdc9543`**.
- Read-back: type `RelationalDatabase`, ACTIVE, `qualifiedName` `PostgreSQL Relational Database::host.docker.internal:5442::scratch_cat_test6`, createdBy `erinoverview`, classifications only `Anchors {anchorTypeName RelationalDatabase, anchorDomainName Asset}` (no `anchorGUID`: it is its own anchor; no ZoneMembership). Related elements (depth 0): `SourcedFrom` -> template `3d398b3f` (`elementAtEnd1` false: the template is at end 2), `ResourceConnection` `e10e7677-dfab-41f6-8df2-cfd217ef81a2` -> VirtualConnection `30d377b2-eef8-4796-8c2b-ce55a43dfd65` (`<DB>::Connection`). **No relationship to any server element.** A scan of the raw and related JSON for `clearPassword` / `password":` found nothing.

## Step 1: attach and one refresh (21:22)
- `AssetMaker.add_catalog_target(70dcd0b7-..., d7b8fe0a-..., body={"class":"NewRelationshipRequestBody","properties":{"class":"CatalogTargetProperties","catalogTargetName":"scratch_cat_test6_database","deleteMethod":"LOOK_FOR_LINEAGE","configurationProperties":{"includeSchemaNames":"plain"}}})` -> stored target: relationship **`c6af0f4a-340b-428a-8d44-1eb2d66ce828`** (`CatalogTarget`, createdBy `erinoverview`, configurationProperties `{includeSchemaNames: plain}`), connector `70dcd0b7` at end 1 of it as the database sees it (`elementAtEnd1` true).
- One forced refresh (21:22:17: `lastRefreshTime` 02:11:22.302Z -> 02:22:17.088Z, completed in 2784 ms). Log (Z, no credential in it): `OIF-CONNECTOR-0008 ... refreshing action target scratch_cat_test6_database`, `JDBC-INTEGRATION-CONNECTOR-0001 ... preparing to extract metadata from database <DB>`, `-0012 Transfer complete` for each table, column and for `schema <DB>::plain`, `-0003 ... successfully extracted`.
- Created under `scratch_cat_test6` by `dbcatnpa` (counts by type): **1 `DeployedDatabaseSchema`, 2 `RelationalDBSchemaType`, 6 `RelationalTable`, 12 `RelationalColumn`** (plus the 4 elements of my template graph found by name: the database, its connections and endpoints; and my secrets-store Asset).

| Type | qualifiedName (`<DB>` = the database's) | GUID |
|---|---|---|
| DeployedDatabaseSchema | `<DB>::plain` | `27d4d104-69c3-4c5b-b3a8-c538b5ab5107` |
| RelationalDBSchemaType | `<DB>::plain_schemaType` | `d2d28a1f-d709-4e42-a89e-1fc20bf475ee` |
| RelationalDBSchemaType | `<DB>_schemaType` | `d52585f6-8005-478b-ac31-12d3cddc4384` |
| RelationalTable | `<DB>::plain::orders` | `a287270b-94fb-4a73-b4de-e75be56839cf` |
| RelationalTable | `<DB>::plain::customers` | `54a899bc-921e-4a6d-a524-0382d3f12afb` |
| RelationalTable | `<DB>::orders` | `9d9ea0e5-aa69-4ffb-8084-ca1179a1c1b9` |
| RelationalTable | `<DB>::customers` | `5b6e023f-3a5f-46d6-9626-bc33a25b1922` |
| RelationalTable | `<DB>::o_t` | `56ecff1d-6bf8-457f-9207-9add520a3e28` |
| RelationalTable | `<DB>::pub_t` | `73f4f9fc-1e97-4d5c-88bd-912d3c1e0b09` |

(12 columns: 2 each under `plain::orders`, `plain::customers`, `orders`, `customers`, `o_t`, `pub_t`; columns `6b0fb319`, `072b6dda`, `a695766b`, `1b5a2df1`, `c27496e7`, `eb33143c`, `3c9e9615`, `b80d81c8`, `71c7bfd0`, `6784bf7f`, `cc3863f3`, `1dec6b67`.)

## Step 2: the point (live read; `MetadataExpert.get_metadata_element_by_guid` and `get_all_related_elements`, graphQueryDepth 0, forLineage and forDuplicateProcessing true)
`elementAtEnd1` below is the field on each related-element item: **true = the OTHER element (not the one read) is at end 1**. Each relationship was read from both of its elements and the two answers agree.

### The schema element `27d4d104-69c3-4c5b-b3a8-c538b5ab5107`
- Type `DeployedDatabaseSchema`, ACTIVE, `qualifiedName` `PostgreSQL Relational Database::host.docker.internal:5442::scratch_cat_test6::plain`, `displayName` `plain`, no description, `additionalProperties` `{jdbc.catalog=scratch_cat_test6, jdbc.schema=plain}`, createdBy `dbcatnpa`.
- **Anchor: classification `Anchors` {anchorTypeName `RelationalDatabase`, anchorDomainName `Asset`, anchorGUID `d7b8fe0a-7f5e-47d5-bc8f-dbfdbcdc9543` (the database)}.** No other classification (no ZoneMembership, no Ownership).
- **ALL relationships touching it: 2** (counts: `DataSetContent` 1, `Schema` 1):
  1. `DataSetContent` `227526de-bc2c-43db-9231-ab9920b3e8f7` (createdBy `dbcatnpa`, no relationship properties): other element = the database `d7b8fe0a`, `elementAtEnd1` false from the schema's side and **true from the database's side**.
  2. `Schema` `33efc1ea-ec2f-44ff-87ec-8db69b9a821c` (createdBy `dbcatnpa`, no properties): other element = `RelationalDBSchemaType` `d2d28a1f` (`plain_schemaType`), `elementAtEnd1` false from the schema's side (schema type read from the schema), true from the schema type's side.
- No `CatalogTarget`, `SourcedFrom`, `ResourceConnection`, or any other relationship: the cataloguer-made schema has no connection of its own and no target.

### (a) schema vs database asset
**`DataSetContent`, GUID `227526de-bc2c-43db-9231-ab9920b3e8f7`: END 1 = the schema (`DeployedDatabaseSchema 27d4d104`), END 2 = the database (`RelationalDatabase d7b8fe0a`).** No properties. Direct relationship. Schema anchored to the database (above). Verified from both elements.

### (b) schema vs server asset
The server element exists: `SoftwareServer` `d87059ce-2d87-4e0a-b35f-0652dc00d841`, `qualifiedName` `PostgreSQL Server::host.docker.internal:5442` (found by the string search; read only, nothing written). Its depth-0 relationships: `SourcedFrom` -> template `542134e6...`, `SupportedSoftwareCapability` `81bf2d90-a58f-4421-ba11-78d3314f3030` -> `DatabaseManager` `17f8b5a8-9f86-4f14-ac13-feecda77181e` (`elementAtEnd1` false from the server's side: the capability is at end 2), `ResourceConnection` -> VirtualConnection `7599baad-...`, `ServerEndpoint` -> Endpoint `358c98d8-...`. **No relationship of any type between the server (or its DatabaseManager capability, depth 0) and the database, the schema, the schema types or any table.** The DatabaseManager's only relationship is the one to the server. A name search for `scratch_cat_test6` finds no server element. **There is no schema-to-server and no database-to-server relationship, made by the cataloguer or by the template create.** (The earlier note's template-created schema had none either.)

### (c) table vs schema (table `a287270b-94fb-4a73-b4de-e75be56839cf`, `plain.orders`)
- Type `RelationalTable`, `qualifiedName` `<DB>::plain::orders`, `displayName` `orders`, `additionalProperties` `{jdbc.table=orders, jdbc.tableType=TABLE, jdbc.catalog=scratch_cat_test6, jdbc.schema=plain}`, createdBy `dbcatnpa`; **anchor `Anchors` anchorGUID = the DATABASE `d7b8fe0a`** (not the schema), anchorTypeName `RelationalDatabase`, anchorDomainName `Asset`.
- **ALL relationships touching it: 3** (`AttributeForSchema` 1, `NestedSchemaAttribute` 2):
  1. `AttributeForSchema` `bcc29a82-1772-41f1-87e0-1f3be644829b`, properties `{minCardinality 1, maxCardinality 1, position 0}`: **END 1 = the schema type `RelationalDBSchemaType d2d28a1f` (`<DB>::plain_schemaType`), END 2 = the table.**
  2. and 3. `NestedSchemaAttribute` `769cb251-...` and `1449d697-...`, properties `{minCardinality 1, maxCardinality 1, position 0}`: END 1 = the table, END 2 = the column (`plain::orders::amount` `6b0fb319`, `plain::orders::order_id` `072b6dda`).
- **There is no direct table-to-schema relationship.** The path is table <- `AttributeForSchema` - schema type <- `Schema` - schema: schema type `d2d28a1f` has exactly 3 relationships: `AttributeForSchema` x2 (end 1 of both; tables `plain::orders` `bcc29a82`, `plain::customers` `22a1c741-4024-4609-aad6-e4231b71b48c` -> `54a899bc`) and `Schema` `33efc1ea` with the **schema at END 1 and the schema type at END 2**. The schema type is anchored to the database too (`anchorGUID d7b8fe0a`).

### (d) table vs database
**No direct relationship** from any table to the database. The link is the anchor (`Anchors.anchorGUID = d7b8fe0a`) and, for the directly-under-database tables, the path table <- `AttributeForSchema` (END 1 = schema type `d52585f6`, `<DB>_schemaType`, END 2 = table) and `Schema` `8a961f5e-f48c-4770-aa84-0b1ef6985c59` (**END 1 = the database, END 2 = the schema type**). Example: table `<DB>::orders` `9d9ea0e5`, `AttributeForSchema` `c73c7d75-6d53-4eaf-8597-123f84ffed69`; the database `d7b8fe0a` has exactly 5 relationships: `DataSetContent` 1 (to the schema), `Schema` 1 (to `d52585f6`), `CatalogTarget` 1, `SourcedFrom` 1, `ResourceConnection` 1. Schema type `d52585f6` has `AttributeForSchema` x4 (`pub_t` `64f4ebfa-...`, `orders` `c73c7d75`, `customers` `0db9aff2-...`, `o_t` `40f19611-...`, all END 1 = the schema type) and `Schema` x1.

### The structure, as read
```
database d7b8fe0a (RelationalDatabase, own anchor)
 |-- Schema 8a961f5e -----------------> RelationalDBSchemaType d52585f6 <DB>_schemaType (anchor = db)
 |                                         `-- AttributeForSchema -> tables pub_t, orders, customers, o_t (anchor = db)
 `-- DataSetContent 227526de  <--------- DeployedDatabaseSchema 27d4d104 <DB>::plain (anchor = db)   [schema END 1, db END 2]
                                          `-- Schema 33efc1ea --> RelationalDBSchemaType d2d28a1f <DB>::plain_schemaType (anchor = db)
                                                                   `-- AttributeForSchema -> tables plain::orders, plain::customers (anchor = db)
```
Arrow = end 1 to end 2.

## Step 3: includeSchemaNames and S12 (21:22)
- `includeSchemaNames=plain` restricted the SCHEMA elements: only `<DB>::plain` exists; **no `DeployedDatabaseSchema` for `other`** (and none for `public`).
- It did NOT restrict the directly-under-database tables (S12, again): `<DB>::o_t` (from schema `other`) and `<DB>::pub_t` exist directly under the database's own schema type `<DB>_schemaType`, alongside `<DB>::orders` and `<DB>::customers` (from schema `plain`), one per source table regardless of its schema (source: `catalogTablesAndViews(..., null)`, line 141, with a null schema name). The `plain`-scoped tables `<DB>::plain::orders` and `::customers` are catalogued a second time under the schema element. `other.o_t` does not appear under the schema path (`<DB>::other::o_t` absent).

## Comparison
**Source and live AGREE, on every point checked:**
| Point | Source | Live |
|---|---|---|
| schema type, name | `DeployedDatabaseSchema`, `<DBQN>::<schema>` (220, 119) | same |
| schema <-> database | `DataSetContent`, `parentAtEnd1(false)`: schema END 1, database END 2 (255-259; handler 2292-2317) | `227526de`, schema END 1, database END 2 |
| schema anchor | `anchorGUID = databaseGUID`, `isOwnAnchor false` | `Anchors.anchorGUID = d7b8fe0a` |
| schema type element | `<parent QN>_schemaType`, `Schema`, `parentAtEnd1(true)` (444-451) | `d2d28a1f`, `Schema 33efc1ea`, schema END 1 |
| table <-> schema type | `AttributeForSchema`, `parentAtEnd1(true)`, cardinality 1/1 (503-508) | `bcc29a82`, schema type END 1, table END 2, 1/1 |
| table anchor | the database (500) | `anchorGUID d7b8fe0a` |
| server link | none in the code | none live |

**Against RE's current way:** `catalogue_gateway.create_schema_element` (`resource_explorer/catalogue_gateway.py`, around line 575-590) sends `parentRelationshipTypeName "DataSetContent"` with **`parentAtEnd1: True`**, i.e. the database at END 1. The live cataloguer has the database at END 2. This matches the rehearsal 2 rejection (`REHEARSAL-2-CURATE-COMMIT-DATABASES-2026-10-05.md` lines 61 and 132: OMRS-REPOSITORY-400-047, end 1 of `DataSetContent` is `RelationalDatabase` rather than `DataSet`): the type check at `MetadataElementHandler.java` 2151-2166 compares the parent to `endDef1` when `parentAtEnd1` is true. The relationship type and the anchor RE sends already match the cataloguer; **the end flag is the only difference seen.**

## What RE must send to make its schema indistinguishable (facts only)
- Type `DeployedDatabaseSchema`; relationship to the database `DataSetContent` with the **schema at end 1 and the database at end 2** (`parentAtEnd1: false` when the schema is created with `parentGUID` = the database); anchor `anchorGUID` = the database GUID with `isOwnAnchor` false (the cataloguer's schema has `Anchors.anchorGUID` = the database, `anchorTypeName RelationalDatabase`).
- The cataloguer's schema `qualifiedName` is `<database qualifiedName>::<schema name>` (e.g. `PostgreSQL Relational Database::host.docker.internal:5442::scratch_cat_test6::plain`), `displayName` the schema name, `additionalProperties` `jdbc.catalog` and `jdbc.schema`. RE's template-created schema has a different qualifiedName (`PostgreSQL Relational Database Schema::<host:port>::<db>.<schema>`, per the earlier read-backs). The cataloguer finds an existing schema ONLY by `<DBQN>::<schema>` (line 241), so a schema RE creates under another qualifiedName is a second element, not an adopted one, and the cataloguer will then create its own at the cataloguer's name (this follows from source lines 220-262; not run live).
- The schema carries exactly two relationships in the cataloguer's graph: `DataSetContent` (schema end 1) and `Schema` (schema end 1) to a `RelationalDBSchemaType` named `<schemaQN>_schemaType`, anchored to the database. No server relationship, no connection, no target on the cataloguer's schema.
- The schema-to-schema-type relationship type name that Egeria and the cataloguer use is **`Schema`** (type id `815b004d-73c6-4728-9dd9-536f4fe803cd`). RE's `link_schema_type` (`catalogue_gateway.py`, `create_related_elements` with `typeName "AssetSchemaType"`) uses a different name; `AssetSchemaType` does not occur as a type name in the Java sources of `origin/main` and was not tried live here.
- Tables: `RelationalTable`, `qualifiedName` `<schemaQN>::<table>`, anchored to the database (not the schema), attached to the schema type through `AttributeForSchema` (schema type END 1, table END 2, min/max cardinality 1/1); there is no table-to-schema or table-to-database relationship.

## Not established
- Whether Egeria accepts `parentAtEnd1: false` through the **template** create (`create_elem_from_template`) path RE uses: the cataloguer uses `createAsset` (`NewElementOptions`), not a template. The template path's handler (`MetadataElementHandler.java` around 2541-2578) shares the `parentAtEnd1` logic but was not run live here, nor was an RE-style create.
- Whether a schema created by RE under a different qualifiedName is later adopted, duplicated or left alone by the cataloguer when the schema is also a SCHEMA-kind target (only the DATABASE-kind path was run).
- Whether `AssetSchemaType` is accepted by `create_related_elements` (not tried).
- The schema type's `displayName` (the source says `Schema Type for <displayName>`; not read live) and the exact `position` semantics (reported `0` on every `AttributeForSchema` and `NestedSchemaAttribute`).
- What happens when the schema `other` or `public` is ever included (not run); anything about views, foreign keys or primary keys (the throwaway has none).
- The depth-0 related read of the server and its DatabaseManager capability does not show relationships that are more than one hop away.

## Step 4: teardown (21:33-21:36)
- Target removed by relationship GUID: `AssetMaker.remove_catalog_target(c6af0f4a-340b-428a-8d44-1eb2d66ce828, {DeleteRelationshipRequestBody, forLineage true, forDuplicateProcessing true})` -> `get_catalog_targets` -> `No elements found`.
- 30 elements soft-deleted one by one, `forLineage` true, `cascade_delete=False`, leaf first, each read back gone (no failure, no refusal): 12 columns, 6 tables, 2 schema types, the schema `27d4d104`, VirtualConnection `30d377b2`, Connections `566496e2` (`<DB>::SecretsStoreConnection`) and `53d5ca96`, Endpoints `83e8f158`, `f827f20a`, `15621eeb`, the database `d7b8fe0a`, the secrets-store Asset `4ac4a2f7`, ConnectorType `ec3c81f5`. The shared ConnectorTypes (`Egeria::ResourceConnector::RelationalDatabase::JDBC`, `Egeria::SecretsStoreConnector::YAMLFile`) and the template were not touched. No DataFile or SecretsCollection for my secrets file ever appeared (no element named `scratch-cat-test6`).
- Final forced refresh (21:34:55, `lastRefreshTime` 02:22:17Z -> 02:34:55.605Z): the cataloguer still refreshed the removed target once from its in-memory list (the S17 behaviour) and logged `OIF-CONNECTOR-0015` x3 and `JDBC-INTEGRATION-CONNECTOR-0006` with `OMAG-REPOSITORY-HANDLER-404-007` / `OMRS-REPOSITORY-404-013` ("RelationalDatabase entity d7b8fe0a ... is soft-deleted"); **it created nothing**. After it, `get_catalog_targets` -> `No elements found`, and the only connectors not `WAITING` are the two `OpenAPICataloguer`. The Egeria container's `StartedAt` was unchanged throughout (02:09:12Z); nothing was restarted.
- `DROP DATABASE scratch_cat_test6 WITH (FORCE)` and `DROP ROLE scratch_cat_role6` succeeded (only `scratch_cat_test3` / `scratch_cat_role3`, the first rehearsal's, remain on Postgres). `rm` of only `scratch-cat-test6.omsecrets`; the secrets directory's file names after equal the before list (`diff` empty).
- Final name sweep (assets, schema types, schema attributes, and `find_metadata_elements_with_string`) for `scratch_cat_test6`, `scratch-cat-test6`, `scratch_cat_role6`: **0 elements**.

The platform is as it was, except for: Egeria keeps the soft-deleted (not purged) records of the 30 deleted elements and their relationships, the audit-log lines of this run (including the 404-007 lines from the final refresh), the `lastRefreshTime` of the JDBC cataloguer (now 02:34:55Z), and Postgres keeps nothing of mine.
