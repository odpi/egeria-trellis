# Scratch cataloguer test, part 2: string forms and SCHEMA-kind targets (2026-10-05)

Follows `SCRATCH-CATALOGUER-TEST-2026-10-04.md` (which stopped at step 5 because a JSON array in a catalog target's
`configurationProperties` was not honoured). Same throwaway database `scratch_cat_test` on Postgres 5442 and the same live
scratch target. **Build: the platform image of 2026-10-03 (`egeria-quickstart-platform:local` built 18:46Z,
`FROM docker.io/odpi/egeria-platform:latest@sha256:5d2cf5a8…`, 6.2-SNAPSHOT, connector jar mtime 2026-10-03 18:37:33Z).
It does NOT contain Egeria's fixes of 2026-10-05.** Source reading below is upstream main `d395c18f` (the JDBC connector
code is unchanged since 2026-09-22 `fcb804149b`, which the running jar contains). No credentials appear here.
Each step: one `update_catalog_target` or one create+attach, one forced refresh (about 15 s,
`ServerOps.refresh_integration_connectors("JDBCDatabaseCataloguer","qs-integration-daemon",120)`), one read-back.

## The rule, from the source (`TransferCustomizations.processCustomization`)
A `String` value becomes ONE name (no splitting on commas, no JSON parsing); only a `List<String>` gives several. The
CatalogTarget relationship stores `configurationProperties` as strings, so a list cannot be expressed through it
(observed 10-04: the array stored as a flattened Java `toString`). So through the DATABASE-kind target an include list can
name exactly ONE schema, and a multi-name string matches nothing.

## Variants on the database-kind target (`includeSchemaNames`)
| Variant | Value sent | Stored as | Schemas processed |
|---|---|---|---|
| `comma` | `a_b,aXb,s_x,pct,plain` | the same string | none |
| `jsonstr` | `["a_b","aXb","s_x","pct","plain"]` | the same string | none |
| `single` | `plain` | `plain` | `plain`: the schema element was created, but the pass then FAILED (below) |
| `single_ab` | `a_b` | `a_b` | `a_b`, clean |

* `comma` and `jsonstr`: after the refresh the only elements under the database are the seven tables created DIRECTLY
  under it by the database-level pass (`<DB QN>::<table>`), as on 10-04. No schema element.
* `single` (`plain`): the daemon logged `JDBC-INTEGRATION-CONNECTOR-0006` from `resolveSchemaTypeGUID`:
  `OMAG-COMMON-409-001 ... not able to create an instance of type RelationalDBSchemaType because ... qualifiedName ...
  <DB QN>::plain_schemaType is not available for use`. The schema type left behind by the 10-04 stale delete (orphan
  `6a0559b5…`) still holds the unique name, so **re-including a schema that was once excluded fails**; `plain` got a schema
  element and no tables or columns under it.
* `single_ab` (`a_b`): the schema pass ran cleanly: `<DB QN>::a_b` plus tables and columns, **and `aXb`'s table `t_axb`
  with its columns were created under `a_b`** (7 elements under `a_b`, 0 under `aXb`). This is the schema-name LIKE hazard
  confirmed: `getTables(catalog, "a_b", ...)` takes the name as a pattern, `_` matches one character, so `aXb` matched.
* The database-level leak persists in every variant: the seven tables of every schema stay catalogued directly under the
  database whatever the schema filter says.

## SCHEMA-kind catalog targets (the lever added upstream 2026-09-22)
Template lookup: the technology type is `PostgreSQL Relational Database Schema` (template GUID `82a5417c-d882-4271-8444-4c6a996a8bfc`;
`PostgreSQL Database Schema` and `PostgreSQL Schema` return nothing, which broke the first attempt before any write). A
`DeployedDatabaseSchema` was created from that template with placeholders (`databaseName`, `serverName`, `hostIdentifier`,
`portNumber`, `schemaName`, `schemaDescription`, `secretsCollectionName`, `secretsStorePathName`), with one connection, and
attached to the JDBC cataloguer with NO configuration properties and `deleteMethod` LOOK_FOR_LINEAGE.
* **Schema `s_x`** (element `e823c910-2c27-44f3-b181-a77a02630f4e`, target relationship `9a832b42-818b-4ad1-9cb2-40f17c351adc`):
  one refresh created 9 elements, all under `PostgreSQL Relational Database Schema::host.docker.internal:5442::scratch_cat_test.s_x`
  (the schema, tables `x_y` and `xZy`, and their columns). Nothing from any other schema; nothing GONE; no exception.
  Column-level wildcard confirmed: `x_y` received `xZy`'s column `only_in_xzy` (`x_y` has a, b, only_in_x_y, only_in_xzy).
* **Schema `a_b`** (element `ee3e1504-7f6c-4bce-b365-69d44ebcabfe`, relationship `4466bbab-7dbe-432a-a957-612dc35ee2e5`): 7 new
  elements: `a_b`, `t_ab` and columns, **and `aXb`'s `t_axb` with its columns under `a_b`**. So a SCHEMA-kind target scopes to
  one schema (no other schema's tables appear) but does NOT escape the schema-name wildcard: a schema whose name contains
  `_` or `%` also pulls in same-shaped neighbours.
* The schema element made from the template has the qualifiedName `PostgreSQL Relational Database Schema::<server>::<db>.<schema>`,
  a different scheme from the database-level cataloguer's `<DB QN>::<schema>`; the two coexisted for `a_b` (different elements).
* No pre-made elements were adopted or duplicated in this part (adoption stays as recorded on 10-04).

## What this means for slice B
1. Database-kind target + include list: only ONE schema can be named; the database-level pass still catalogues every
   schema's tables under the database. It cannot honour a scope.
2. SCHEMA-kind targets (one per chosen schema, no lists) honour a per-schema scope cleanly, with the two wildcard caveats
   (schema names and table names containing `_` or `%`), which RE can detect from the inventory before attaching.
3. A schema that was excluded and is re-included hits the 409 on its orphaned schema type (found on `plain`).
4. Not tested: how a SCHEMA-kind target is REMOVED (detach: archive/soft-delete behaviour), the database-level pass when
   no database-kind target exists at all (RE would attach only schema targets), and everything on the rebuilt platform.

## State left in place (teardown is the owner's separate word)
Postgres 5442: database `scratch_cat_test`, role `scratch_cat_role`. Secrets file `scratch-cat-test.omsecrets`. Egeria: database
element `ed49aacd-8dca-44cc-a224-c20034353f70`; DATABASE-kind target `38722408-8dd1-4bc0-b7ab-dfe57e080ca0` (now
`includeSchemaNames` = `a_b`); two NEW SCHEMA-kind targets `9a832b42-818b-4ad1-9cb2-40f17c351adc` (s_x) and
`4466bbab-7dbe-432a-a957-612dc35ee2e5` (a_b); two NEW schema elements `e823c910-2c27-44f3-b181-a77a02630f4e` and
`ee3e1504-7f6c-4bce-b365-69d44ebcabfe` with their tables and columns (16 elements); the elements from 10-04 (about 90, plus
orphans `6a0559b5-1d37-467d-b435-a5de9e93c353` and `f9134b32-e1c6-4063-902b-b98edd2303dc`, the DataFile and SecretsCollection).
Teardown order: detach the three targets by relationship GUID, force one refresh and read what the cataloguer does, remove
the new schema elements and their children, then follow the cleanup list in the 10-04 note, drop the database and role,
remove the secrets file.
