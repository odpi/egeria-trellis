# Catalogue lever findings: what Egeria's PostgreSQL/JDBC cataloguers can be told to do

Researcher note, 2026-10-04. Read-only investigation. It answers the "Left open,
for the architect" list in `REPLY-DESIGNER-CURATE-CATALOGUE-SCOPE-DATABASES.md`
(on `origin/re/design-curate-catalogue-scope`) plus two build questions.

## Source and its relation to what runs

- Egeria checkout `/Users/dwolfson/localGit/egeria-v6/egeria`, HEAD `df82f4fe`,
  commit date **2026-09-15T23:47:15+01:00**. The running platform is a locally
  built 6.2-SNAPSHOT image built **2026-10-03 13:46 CDT**, so the commit is
  **before** the build by 18 days. The working tree is clean for the connector,
  integration-framework, generic-handler and OMF directories (only an untracked
  `.junie/` exists). The newest commit touching the JDBC connector is
  `61bb6da06b` (2026-08-30); the newest touching the integration framework,
  generic handlers and OMF is `f154a7a8ba` (2026-09-10). That makes it *likely*
  the image was built from this source, but **which checkout the image was built
  from is not established**; treat "read in source" as "true of df82f4fe".
- Paths below are relative to
  `egeria/open-metadata-implementation/` (written `$E/`) unless they start
  `egeria/`. `JDBC` = `$E/adapters/open-connectors/integration-connectors/jdbc-integration-connector/src/main/java/org/odpi/openmetadata/adapters/connectors/integration/jdbc/`.
  `OIF` = `$E/frameworks/open-integration-framework/src/main/java/org/odpi/openmetadata/frameworks/integration/`.
- Base of this worktree: `origin/main` at `45f4671b`. The designer's reply was
  read from `origin/re/design-curate-catalogue-scope`.

## 1. Depth

**Answer.** There is no depth option. All four depths can be produced from the
include/exclude lists, but "off" is only expressible as an *include list holding
a name that matches nothing*, because matching is plain case-sensitive equality
with no wildcard, and an empty list means "no filter".

**Evidence.**
- `JDBC transfer/customization/TransferCustomizations.java:170-183`
  `shouldTransfer`: if the include list is non-empty the answer is
  `inclusions.contains(name)` and the exclude list is **ignored**; else if the
  exclude list is non-empty, `!exclusions.contains(name)`; else true. `List.contains`
  is exact, case-sensitive string equality.
- `:214-237` `processCustomization`: a `String` value becomes **one** list entry
  (it is **not** split on commas); a `List` has its `String` items kept; anything
  else yields an empty list (no filter).
- `JDBC README.md:85` says "lists with database object names ... no wildcards supported".
  `JDBC controls/JDBCConfigurationProperty.java` describes the lists as
  "comma-separated" with type `array<string>`, but the code does not split
  (see section 5).
- The eight list keys: `includeSchemaNames`, `excludeSchemaNames`,
  `includeTableNames`, `excludeTableNames`, `includeViewNames`, `excludeViewNames`,
  `includeColumnNames`, `excludeColumnNames` (`TransferCustomizations.java:26-28`).
- Where each is applied, `JDBC RelationalDatabaseCataloguer.java`: schema filter
  at `:135`, `:170`, `:939`; table filter at `:337`, `:355`, `:948`; column filter
  at `:672`.
- **The view lists are never read.** `shouldTransferView` (`TransferCustomizations.java:82`)
  has no caller anywhere under `$E/`; views are filtered by `shouldTransferTable`
  (`RelationalDatabaseCataloguer.java:355`). `includeViewNames` / `excludeViewNames`
  are configured-but-never-consumed.

**Which depths the lists can produce** (all by reading the code paths; none run):

| Depth | Expressible | How |
|---|---|---|
| database only | yes | `includeSchemaNames` and `includeTableNames` each set to one name that cannot exist (a sentinel). The adopted database element is untouched. |
| schemas | yes (the designer assumed no) | real `includeSchemaNames`, `includeTableNames` = sentinel. Schemas are created (`:162-182`); tables are filtered out at `:337`. An empty schema-type element is still created per schema (`:326`, `:397-409`). |
| tables | yes | real schema/table lists, `includeColumnNames` = sentinel (columns are filtered at `:672`; any existing columns are then deleted as stale, `:683`) |
| tables and columns | yes | the default; no column list |

"Columns off" via `excludeColumnNames` is **not** expressible (no wildcard). It is
expressible via `includeColumnNames` = a sentinel. An empty include list does
**not** mean "none"; it means "everything".

**Matching is by bare name, not path.** The table filter sees only
`jdbcTable.getTableName()` (`:337`), so `orders` is chosen or left out in *every*
schema at once; a column name is chosen or left out in *every* table. This
confirms the designer's "same name in two schemas can't be told apart".

**Two leaks in schema scoping (read in source, behaviour not run).**
1. `catalogDatabaseContents` first calls
   `catalogTablesAndViews(databaseElement, databaseQualifiedName, databaseGUID, null)`
   (`:125`). With `jdbcSchemaName == null` the call is
   `getTables(catalog, null, null, TABLE_TYPES)` (`:335`), and the connector's own
   README (`JDBC README.md:92-102`) says null "returns tables from all schemas".
   So tables of *every* schema are also catalogued **directly under the database**
   (qualified name `<db>::<table>`), filtered only by the table-name lists, not by
   the schema lists. Excluding a schema does not stop its tables arriving at database level.
   A same-named table in two schemas would collide on `<db>::<table>` and be
   updated rather than created twice (`findExistingSchemaAttribute`, `:1058-1099`).
   *Inferred*: pgjdbc types pg_catalog/information_schema tables as SYSTEM TABLE /
   SYSTEM VIEW so the `TABLE`/`VIEW` type filter probably drops them; not checked.
2. `getSchemas(catalogName, null)` (`:128`) returns every schema, including
   `pg_catalog`, `information_schema` and `pg_toast*`. Nothing excludes system
   schemas by default (no default exclude list in
   `JDBC controls/JDBCConfigurationProperty.java`; contrast the server cataloguer,
   which defaults `excludeDatabaseList` to `postgres`, section 2). RE must send an
   explicit schema **include** list (preferred) or exclude the system schemas.
3. `getTables`, `getColumns` and `getSchemas` pass names as JDBC *patterns*
   (`transfer/JdbcMetadata.java:101-135`; `RelationalDatabaseCataloguer.java:670`
   passes the table name as `tableNamePattern`), so `_` and `%` in a real name act
   as wildcards in the JDBC call. *Inferred* risk for names such as `order_items`.

**Status.** Read in source (all). Leaks 1 and 3 are inferred behaviour from source
plus the README; not run.

**For the build.** The designer's disabled "schemas alone can't be asked for"
line is wrong: schemas-only is expressible. Compile depth to sentinel include
lists, always send an explicit schema include list, and put a sentinel in
`includeTableNames` for the two shallow depths. Do not promise that leaving a
schema out keeps its tables out until the database-level pass (leak 1) is tested
on a scratch database; if it holds, an excluded schema's tables can still appear
at database level whenever tables are included. Do not expose a "views" choice:
it is a no-op.

## 2. Adoption

**Answer.** Yes. When the catalog target is the RE-created RelationalDatabase, the
JDBC cataloguer uses that element as-is and builds schemas, tables and columns
under it; it never looks the database up by name. The *server* cataloguer, if the
PostgreSQL **server** asset were attached, creates one RelationalDatabase **per
database on the server by name from a template**, skipping one only if the
resolved qualified name already exists.

**Evidence.**
- `JDBC JDBCIntegrationCatalogTargetProcessor.java:70` requires the target to be
  of type RelationalDatabase, else it throws wrong-type (`:100`). `:83-86` pass the
  target element itself into `refreshDatabase`; `:220-223` `catalogDatabase` returns
  the given element immediately when non-null. The create-by-name path
  (`:225-251`) is only for a target that is not a database element.
  `RelationalDatabaseCataloguer.java:122-123` takes the GUID and qualified name
  **from the adopted element**, so schema names are
  `<adopted qualifiedName>::<schema>` (`:175`), tables `<schema QN>::<table>` (`:342`)
  and columns `<table QN>::<column>` (`:677`).
- The process that attaches: `egeria/open-metadata-resources/open-metadata-archives/core-content-pack/.../RequestTypeDefinition.java:1520-1532`
  (`catalog-postgres-database`: governance service `CATALOG_TARGET_ASSET`, action
  target = the JDBC cataloguer's GUID `70dcd0b7-9f06-48ad-ad44-ae4d7a7762aa` from
  `IntegrationConnectorDefinition.java:180`). The service
  `$E/adapters/open-connectors/governance-action-connectors/.../stewardship/CatalogTargetAssetGovernanceActionConnector.java:95-121`
  takes the `newAsset` action target, names the target after the element's
  `displayName` (`:97-110`) and creates the CatalogTarget relationship from the
  connector to **that element** with the request parameters as
  `configurationProperties` (`:112-121`, `combineProperties` `:153-173`).
- Two more request types use the same JDBC cataloguer: `catalog-postgres-server`
  (`RequestTypeDefinition.java:1463`) uses the **server** cataloguer GUID
  `36f69fd0-54ba-4f59-8a44-11ccf2687a34`; `catalog-postgres-schema` (`:1976`)
  points at the JDBC cataloguer but the JDBC processor rejects any target that is
  not a RelationalDatabase (`:70`, `:100`), so a schema target would not work
  (*inferred*, not run).
- Server stage: `egeria/open-metadata-implementation/adapters/open-connectors/data-manager-connectors/postgres-server-connectors/src/main/java/org/odpi/openmetadata/adapters/connectors/postgres/catalog/PostgresServerCatalogTargetProcessor.java`:
  `catalogDatabases` reads `pg_database` (`:302`), keeps non-template, connectable
  databases (`:313-314`) that pass `elementShouldBeCatalogued(name, exclude, include)`
  (`:318`); `catalogDatabase` resolves the qualified name from the **database
  template** (`:400-403`), looks it up (`:405`), logs "skipping" if found (`:407-411`)
  and otherwise creates a RelationalDatabase from the template (`:413-428`) and
  attaches it as a catalog target of the JDBC cataloguer (`:434`, `addCatalogTarget`
  `:495-520`). Default `excludeDatabaseList` is `["postgres"]` (`:89-91`).
- The template's qualified name is
  `PostgreSQL Relational Database::{{serverName}}::{{databaseName}}`
  (`egeria/open-metadata-resources/open-metadata-archives/core-content-pack/.../DataAssetTemplateDefinition.java:55-59`).
  `{{serverName}}` is filled from a configuration property named `serverName`
  (`$E/frameworks/open-integration-framework/.../CatalogTargetProcessorBase.java:732-757`
  `getSuppliedPlaceholderProperties`, copied into the template call at `:388-395`).
  The shape RE uses, `...::<host>:<port>::<db>`, matches only if the server target's
  `serverName` equals `<host>:<port>` exactly. *Inferred* from that template; the
  RE publish route's actual `serverName` value was not read.
- **Inheritance of lists.** `addCatalogTarget` copies the *server* target's whole
  configuration into each database target (`:505`,
  `targetConfigurationProperties.putAll(configurationProperties)`), so any JDBC
  include/exclude lists on a server target silently become every database's lists.

**Status.** Adoption by construction: read in source. Duplicate by name when a
server is attached: read in source (conditional on `serverName` equality).
`serverName` value RE sends: not established.

**What RE must NOT attach.**
1. **Never attach the PostgreSQL Server asset to the server cataloguer
   (`catalog-postgres-server`, `36f69fd0-...`).** It would create a RelationalDatabase for
   *every other database on the server* (everything except `postgres` unless an
   `includeDatabaseList` is given) and attach each to the JDBC cataloguer. RE's
   decision to catalogue one database becomes "catalogue the server".
2. Never attach one database element to the JDBC cataloguer **twice**. The
   CatalogTarget relationship is `multiLink=true`
   (`egeria/open-metadata-resources/open-metadata-archives/open-metadata-types/.../OpenMetadataTypesArchive4_0.java:358`),
   so a second attach makes a second relationship rather than updating the first.
   Read the connector's existing targets first and update.
3. Do not attach with no configuration at all: the JDBC processor
   dereferences the combined configuration map at `:148` and
   `RequestedCatalogTargetsManager.java:83-97` returns null for an empty map
   (*inferred* NullPointerException, caught and logged at `:182-190`, nothing
   catalogued). RE always sends lists, so this only matters for an empty commit.
4. Do not attach `catalog-postgres-schema` targets (see above).

**For the build.** The commit is: attach the RE-published RelationalDatabase to the
JDBC cataloguer GUID, once, with lists. Keep RE's server asset unattached. The
read-back in the designer's tree needs only one database element per database; the
"which is which" header is only needed if someone attaches the server by hand.
Whether the RE-created database element's Connection is accepted by the JDBC
resource connector (the cast at `:81`) depends on RE's template publish using the
Egeria `JDBCResourceConnectorProvider` connector type (what
`DataAssetTemplateDefinition.java:62` specifies); not checked against RE's publish.

## 3. Owner and zone

**Answer.** The cataloguer sets **no owner and no zone** on anything it creates.
Tables and columns inherit the database element's zone membership, copied at
creation, through the Anchors classification; schemas (assets) carry no zone of
their own. Nothing in the connector config, the daemon config or the catalog
target's configurationProperties sets owner or zones, so RE must classify after
read-back.

**Evidence.**
- `grep -i 'zone|owner' JDBC` returns nothing under the JDBC connector source.
  Creation calls are `createAsset(newElementOptions, null, schemaProperties, null)`
  (`RelationalDatabaseCataloguer.java:216`), `createSchemaAttribute(...)` for
  tables (`:465`), views (`:546`) and columns (`:754`). The only initial
  classification ever passed is `CalculatedValue` on views and their columns
  (`:543-546`, `:744-754`). No `ZoneMembership`, `Ownership` or `Anchors` properties
  are set by the connector.
- Anchors: tables, columns, schemas and schema types are created with
  `setAnchorGUID(<database GUID>)`, `setIsOwnAnchor(false)` (`:208-216`, `:397-409`,
  `:453-465`, `:732-754`). Server side,
  `$E/common-services/generic-handlers/src/main/java/org/odpi/openmetadata/commonservices/generichandlers/OpenMetadataAPIAnchorHandler.java:337-385`
  (`setUpAnchorsClassificationFromAnchor`) builds the new element's Anchors
  classification with `getZoneMembershipFromClassification(anchorEntity)` (`:368`)
  or the anchor's existing `anchors.zoneMembership` (`:378`). So if the database
  already has `ZoneMembership` at the moment the cataloguer creates a table, the
  table's Anchors record carries those zones. `grep defaultZones` in the generic
  handlers finds nothing: there is no server-side default zone at creation (the OMF
  code comments say defaults come from "the user directory", i.e. the security
  connector, which was not read).
- Provenance: elements are created under the catalog target's metadata source
  (`$E/frameworks/open-integration-framework/.../context/IntegrationContext.java:174-185,526-553`:
  `metadataCollectionQualifiedName` on the target, else the connector's; resolved
  to an external-source GUID through `setUpMetadataSource`). Updates and deletes
  by a caller with no external source are rejected on such elements
  (`$E/common-services/repository-handler/.../RepositoryErrorHandler.java:259-319`
  `validateProvenance`, error `LOCAL_CANNOT_CHANGE_EXTERNAL`; applied in
  `RepositoryHandler.updateEntityProperties :1120,:1591`, `reclassifyEntity :2098`,
  `declassifyEntity :2242`, `removeEntity :2367`). `classifyEntity` (adding a *new*
  classification), `RepositoryHandler.java:1832`, shows no `validateProvenance`
  call in its first 75 lines.

**Status.** No owner/zone set by the cataloguer: read in source. Zones inherited
into the Anchors record at creation: read in source. Provenance blocking
*changes* to cataloguer elements by RE: read in source for the repository handler,
**not** traced through the OMF classify route RE would use. Whether a later zone
change on the database propagates to existing anchored members: not established
(`refreshAnchorsClassification` exists, `OpenMetadataAPIAnchorHandler.java:1801`; not traced).

**For the build.**
- To have catalogued tables and columns land in the publish zones, put the
  `ZoneMembership` classification on the **database element before** the first
  cataloguer cycle. Doing it after leaves existing members' Anchors with the old
  zones until proven otherwise.
- Owner: RE must write `Ownership` itself on the elements it wants owned, after
  read-back (one write per element; 121 tables for the designer's example).
  Adding a new classification looks permitted; *changing* an existing one on a
  cataloguer-created element is probably refused for a non-matching external
  source. Probe with one element on the scratch database before building the
  per-element write.
- The designer's "element . owner not set" state is the honest default.

## 4. Last cycle

**Answer.** No per-catalog-target time exists. The CatalogTarget relationship
carries no timestamp or status; only the connector report has a time
(`lastRefreshTime`), per connector, not per target.

**Evidence.**
- Relationship properties: `$E/frameworks/open-metadata-framework/src/main/java/org/odpi/openmetadata/frameworks/openmetadata/properties/assets/processes/connectors/CatalogTargetProperties.java:28-35`
  (`catalogTargetName`, `metadataCollectionQualifiedName`, `metadataSourceQualifiedName`,
  `connectionName`, `configurationProperties`, `templates`, `permittedSynchronization`,
  `deleteMethod`) and the type definition
  `OpenMetadataTypesArchive4_0.java:346-354` list no time or status. The read-side
  `CatalogTarget` (`$E/frameworks/open-governance-framework/.../properties/CatalogTarget.java:34-36`)
  adds only `relationshipGUID`, versions and the element.
- Refresh loop: `OIF connectors/RequestedCatalogTargetsManager.java:204-262` refreshes
  every target in turn, catches exceptions per target (`:285-295`), and records
  nothing against the target. It only writes audit messages
  `REFRESHING_CATALOG_TARGET` / `REFRESHED_CATALOG_TARGETS`; the JDBC processor adds
  `STARTING_METADATA_TRANSFER` / `EXITING_ON_COMPLETE` per database
  (`JDBCIntegrationCatalogTargetProcessor.java:122-124,174`). Those are audit-log
  entries, not an element RE can read through the view server.
- Connector-level: `IntegrationConnectorReport.java:34` `lastRefreshTime`;
  `IntegrationConnectorHandler.java:167,782-784`. Daemon status route:
  `GET /servers/{daemon}/open-metadata/integration-daemon/status`
  (`$E/governance-server-services/integration-daemon-services/integration-daemon-services-spring/.../IntegrationDaemonResource.java:163`);
  pyegeria `ServerOps.get_integration_daemon_status` (`omvs/server_operations.py:363`).
  Not called. The same resource has `POST .../integration-connectors/refresh`
  (`:310`); not called (a write).
- A connector activity report element is written after a refresh **only if elements
  changed** (`ConnectorActivityReportWriter.java:127-140`) and carries created /
  updated / deleted element GUID sets, connector-wide. The content pack registers
  the JDBC cataloguer with report generation on
  (`ContentPackBaseArchiveWriter.java:1826-1833`, the `true` argument). It is
  connector-level and absent when nothing changed, so it is no "last cycle" for a
  target that produced no change.
- Refresh interval: 60 **minutes** for the JDBC cataloguer by content-pack default
  (`IntegrationConnectorDefinition.java:190`; unit from `GovernanceArchiveHelper.java:392`).
  The running quickstart's value was not read.

**Live read-only check (done once).** Through RE's configured client
(`AssetMaker`, as in `egeria_resync.py:_connect`), `AssetMaker.get_catalog_targets(<JDBC
cataloguer GUID>)` as shipped **fails**: it sets `metadataElementTypeName =
"CatalogTarget"`, which is a relationship type, and the server answers
`OMAG-COMMON-400-019 ... CatalogTarget ... is not a sub-type of OpenMetadataRoot`,
surfaced as `SERVER_ERROR_500`. Called with an explicit body that omits the type,
`get_catalog_targets(GUID, body={"class": "ResultsRequestBody", "graphQueryDepth": 0})`,
it works and returned "No elements found" for **both** the JDBC cataloguer
(`70dcd0b7-...`) and the server cataloguer (`36f69fd0-...`): zero catalog targets,
consistent with the ask's 2026-10-03 finding. The per-target field shape of a
**non-empty** result therefore could not be observed live and I created none.
It is a POST-bodied retrieval, not a write. No credential or element content was printed.

**Status.** "No per-target time on the relationship": read in source.
Per-target response shape: not established (zero targets).

**For the build.** The "last cycle 09:05" in the designer's waiting row can only be
the **connector's** `lastRefreshTime` (daemon status), labelled as the connector's,
not the target's. The honest per-target proof is the read-back itself (the
CatalogTarget relationship exists and the elements do or do not). When the connector's time is missing, keep the
designer's "last cycle not reported". Fix or avoid pyegeria's
`get_catalog_targets` default body before building on it (supply the body above).

## 5. Attach, detach, update through pyegeria, and the body

**Answer.** The client RE already uses, `AssetMaker`, has `add_catalog_target`,
`update_catalog_target`, `get_catalog_target(s)`, `remove_catalog_target` and
`detach_catalog_target`. The include/exclude lists travel in
`properties.configurationProperties` of the relationship body.

**Evidence** (`/Users/dwolfson/localGit/egeria-v6/trellis/.venv/lib/python3.13/site-packages/pyegeria/omvs/asset_maker.py`).
- `add_catalog_target(integration_connector_guid, metadata_element_guid, body)`
  `:1150`; async `:1089`. `POST {asset-maker}/integration-connectors/{connector}/catalog-targets/{element}`
  (`:1128`). Body is a `NewRelationshipRequestBody` with `properties` of class
  `CatalogTargetProperties` (`:1100-1126` sample): `catalogTargetName`,
  `metadataSourceQualifiedName`, `templates`, `configurationProperties`.
- `update_catalog_target(relationship_guid, body)` `:1271`; async `:1215`.
  `POST .../catalog-targets/{relationship}/update` (`:1262`); body
  `UpdateRelationshipRequestBody` with `mergeUpdate`.
- `remove_catalog_target(relationship_guid, body)` `:1584`, `POST .../catalog-targets/{relationship}/remove`
  (`:1580`); `detach_catalog_target(connector, element, body)` `:1865`,
  `POST .../integration-connectors/{connector}/catalog-targets/{element}/detach` (`:1862`).
- `get_catalog_targets(connector, ... body)` `:1492` (fails as shipped, section 4);
  `get_catalog_target(relationship_guid)` `:1381`.
- The pydantic `CatalogTargetProperties` in the same file (`:46-51`) lists only
  `catalogTargetName`, `metadataSourceQualifiedName`, `templates`,
  `configurationProperties`; the Java relationship also takes `deleteMethod`,
  `permittedSynchronization`, `connectionName`, `metadataCollectionQualifiedName`
  (Java file above). Whether a dict body carrying the extra keys survives pyegeria's
  validation was not tested.
- A second, older client method exists in `automated_curation.py:4150` (flat
  body, default `delete_method="ARCHIVE"`, `permitted_sync="BOTH_DIRECTIONS"`);
  prefer `AssetMaker`.

**The list value trap.** The same key means different things to the two stages:
- JDBC cataloguer (`TransferCustomizations.java:214-237`): a JSON **array**
  `["a","b"]` gives two names; a **string** `"a,b"` gives ONE name `"a,b"`
  (never matches anything real).
- Server cataloguer (`CatalogTargetProcessorBase.java:764-790`
  `getArrayConfigurationProperty`): takes `toString()` and splits on `,` with no
  trim, so `"a, b"` yields `" b"`, and a JSON array's `toString()` `"[a, b]"` yields
  `"[a"` and `" b]"`.
  The `catalog-postgres-database` process passes request parameters as a
  `Map<String,String>` (`CatalogTargetAssetGovernanceActionConnector.java:153-173`),
  so going through that process cannot deliver a real array to the JDBC
  cataloguer. A direct `add_catalog_target` with a JSON array value in
  `configurationProperties` is the way to send proper lists. Whether the
  repository keeps an array inside the `map<string,object>`
  `configurationProperties` was not verified (the property type is
  `MAP_STRING_OBJECT`, `OpenMetadataProperty.java:2057`); verify with one attach on
  the scratch database and read the relationship back.
- Updating the relationship changes its version; the daemon rebuilds the processor
  at its next retrieval (`RequestedCatalogTargetsManager.java:126-139,432-446`).

**Status.** Method names, URLs and body keys: read in installed pyegeria.
Array survival through the store: not established.

## 6. When the lists change: what happens to elements now excluded

**Answer.** On the **next refresh of that connector** (60 minutes by content-pack
default, or when a refresh is requested) the cataloguer deletes every existing
schema, table, view and column under the database that is not in the set it just
processed. "Stale" means "not in this refresh's processed set", whether the cause
was a changed filter or a dropped table, so exclusion deletes. It is not
immediate and does not need a full re-catalogue. The delete method decides whether
the element is archived or soft-deleted, and relationships on it go with it on a
soft delete.

**Evidence.**
- Stale sets: schemas `RelationalDatabaseCataloguer.java:166-181,249-284` (every
  DataSetContent child of the database not in the processed names is
  `deleteSchema`, `:295`, cascade `true`); tables and views `:333-371,599-644`
  (`deleteSchemaAttribute`, cascade `true`, `:637`); columns `:668-683,878-923`
  (cascade `false`, `:916`). A table-name filter and a schema filter feed the
  processed set, so a changed list changes what is "current".
- Timing: lists are read fresh each refresh from the combined target configuration
  (`JDBCIntegrationCatalogTargetProcessor.java:143`;
  `RequestedCatalogTargetsManager.java:108-145`); the processor refresh runs
  inside `refreshCatalogTargets` (`:204-262`).
- Delete method: the CatalogTarget's `deleteMethod` is passed into the connector
  context (`IntegrationContext.java:206`) and used for every delete
  (`ConnectorContextClientBase.java:565-575`). Server side
  (`$E/access-services/omf-metadata-management/omf-metadata-server/.../OpenMetadataStoreRESTServices.java:3265-3330`):
  `SOFT_DELETE` (also what a **null** method becomes, `:57,68-72`) soft-deletes;
  `ARCHIVE` adds a Memento classification and keeps the element; `LOOK_FOR_LINEAGE`
  archives if the element has any lineage relationship (`MetadataElementHandler.java:2811-2845`
  `hasLineageRelationships`) and soft-deletes otherwise; `PURGE` is not accepted
  for a first delete. The integration daemon's own default is `LOOK_FOR_LINEAGE`
  (`IntegrationConnectorHandler.java:142`) but the per-target value overrides it.
  pyegeria's older `add_catalog_target` defaults `delete_method="ARCHIVE"`
  (`automated_curation.py:4158`).
- Relationships: a soft delete removes **every** relationship of the element
  (`OpenMetadataAPIGenericHandler.java:3274-3375` `deleteBeanInRepository`, loop at
  the `removeRelationship` call in `deleteAnchoredBeanInRepository`), so glossary
  term assignments (SemanticAssignment) and lineage mappings are removed with it. An
  archive keeps the element and (not checked in detail) its relationships.
- **Dependents.** Tables, columns and schema types are anchored to the **database**,
  not to the schema or table (`RelationalDatabaseCataloguer.java:210,399,455,734`).
  The server deletes a related element only if its anchor is among the entities
  being deleted (`OpenMetadataAPIGenericHandler.java:2849-2905`
  `deleteIfAppropriatelyAnchoredEntity`), and the `cascadedDelete` type handlers
  (`validateCascadedDelete`, `:3685-3760`) name Database, folders, infrastructure
  and data-field types but not schema, table or column types. *Inferred*: deleting
  a schema or table may leave its schema type, tables or columns behind, with no
  connector code to remove them (stale processing only visits schemas still
  included, `:135`, `:939`). Re-including such a table would find the orphan
  columns by qualified name (`:700-713`) and update them without re-linking.
  **This was not run and is the highest-risk unknown for "left out removes it".**

**Status.** Timing, stale rule, delete-method mapping, relationship removal on
soft delete: read in source. Orphan dependents and the behaviour on an archived
element's re-inclusion (a Memento-classified element is probably invisible to the
name lookup at `:1064`, so a duplicate could be created): inferred, not run.
The running refresh interval: not established.

**For the build.** The designer's "will be removed from Egeria on its next
refresh, 2 term assignments and 1 lineage mapping hang off it" is right about
timing and about relationships, with one correction: a table with a lineage
mapping is **archived** under `LOOK_FOR_LINEAGE` or `ARCHIVE` and keeps its
relationships, so the preview should say "archived" for those and "deleted, term
assignments removed" for the rest. Set `deleteMethod` explicitly on the target;
leaving it null means soft delete. The removal count in the commit button should
be computed from the diff of the new scope against what is catalogued, not from the
refresh, because the refresh is up to an hour later. Do not trust "removed"
until the absence is read back and nothing is left behind (verify dependents on a
scratch database first).

## What the commit can and cannot express

Can:
- Choose which database elements are catalogued, by attaching exactly the
  RE-published RelationalDatabase to the JDBC cataloguer once, with its lists.
- Four depths, via sentinel include lists (section 1).
- A schema allow-list, by name. A table allow-list or deny-list, by bare name.
  A column allow-list or deny-list, by bare name. All case-sensitive, exact.
- A delete method per target (`ARCHIVE`, `SOFT_DELETE`, `LOOK_FOR_LINEAGE`).
- Zones on tables and columns, indirectly: set `ZoneMembership` on the database
  element **before** the first cycle.

Cannot:
- A wildcard or pattern anywhere; "no columns" through an exclude list.
- A table or column choice that differs by schema or by table (names are bare).
- A views choice (the view lists are never read; views follow the table lists).
- A guarantee that leaving a schema out keeps its tables out of the database-level
  pass (leak 1, section 1).
- Owner on anything the cataloguer creates; zones on schemas.
- A per-target "last cycle" time, or any per-target status.
- Immediate effect: changes land on the next connector refresh.
- A proper array through the `catalog-postgres-database` process (strings only);
  only a direct attach carries one.
- Limiting Egeria's own database survey (unchanged from the ask).

## Not established

- Which source checkout the 2026-10-03 image was built from (the working tree is
  clean and the commit predates the build, so likely `df82f4fe`).
- Whether the repository stores an array inside `configurationProperties` and returns
  it as a list; whether pyegeria's validation lets `deleteMethod` /
  `permittedSynchronization` through a dict body.
- Actual runtime behaviour of the database-level table pass, of pg system schemas
  at database level, of JDBC `_`/`%` pattern matching on real names, and of orphaned
  dependents or archived-element re-inclusion (all from source only).
- Whether RE's template-published database has the connector type and secrets the
  JDBC resource connector needs (cast at `JDBCIntegrationCatalogTargetProcessor.java:81`),
  and what `serverName` RE's publish route uses.
- Whether provenance blocks RE adding or changing Ownership on a cataloguer-created
  element through the OMF route; whether a later zone change propagates to members.
- The shape of a non-empty `get_catalog_targets` response (zero targets exist), the
  quickstart's actual refresh interval, and what the "user directory"/security
  connector adds at creation.
- Anything about the Egeria *survey* connectors beyond what the ask already records.
