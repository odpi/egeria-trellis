# Combined rehearsal 2 of the Curate catalogue commit for databases, 2026-10-05 (unattended)

Runner: Claude Sonnet 5.5 (subagent). **COMPLETED: steps 0 to 9 ran; no safety stop fired.** Several RE defects were found; one of them (D-B,
the attach target is never seen) made RE create duplicate catalog targets, which I removed by relationship GUID (listed below). No credential value
appears here. Times are CDT (UTC-5) unless marked Z; Egeria's log is Z.

## Header
- RE: `origin/main` **e46f146f** (PR #492 merged), worktree `/Users/dwolfson/localGit/egeria-v6/trellis-re-rehearsal2`, branch `re/combined-rehearsal-evidence`.
  `resource_explorer.__file__` and pyegeria both resolved inside that worktree's `.venv`.
- Egeria: container `quickstart-egeria-main`, image `egeria-quickstart-platform:local` (id `d1c48ab6eaa0`, created 2026-10-05T14:19:48Z), started
  2026-10-05T14:39:08Z, **unchanged at the end of the run** (checked at 20:28 and after teardown). View server `qs-view-server`, daemon `qs-integration-daemon`.
- RE served on `127.0.0.1:8847` (free port) from `serve.py` (RE's own `resource-explorer web --no-embed-worker`), against a TEMP SQLite registry
  `.../sct5/registry5.db`; feedback and metrics temp SQLite; `PGVECTOR_PORT=1`. Proof printed before start: registry, feedback and metrics URL prefix checks
  all `True`; log line `registry: sqlite:///registry5.db`; after start `lsof -nP -a -p <pid> -i` showed only the `127.0.0.1:8847` listener, **0 connections to 5442**.
  The commit runs were claimed by my own `claim_and_execute_once(kinds=["catalogue_commit"])` loop (RE's `run_queue` path), as in rehearsal 1.
- Env: the gate's `.env` was loaded into my processes' environment with `dotenv_values` and overridden for registry/metrics/feedback URL, port and JWT secret
  (own random value). Never printed or opened.
- Sign-in: as rehearsal 1: a session for user `rehearsal-runner` minted with RE's own `auth.create_access_token` against my own JWT secret. No password.
  Egeria's identity for every RE write was `erinoverview`.
- Own secrets file `scratch-cat-test5.omsecrets` (created by Egeria's secrets store through RE's `write_credential` path) and own secrets-store asset graph
  `scratch_cat_test5:SecretsStoreConnector:YAML File Connection::{Asset,Connection,Endpoint,ConnectorType}` (asset `200033ae-73d6-4b91-8a8e-c97454b60dd9`),
  `EGERIA_SECRETS_STORE_*` pointed at them. Secrets directory FILE NAMES before and after: `coco-user-directory, egeria-servers, integration, resource-explorer,
  scratch-cat-test3 .omsecrets` (equal; diff empty).
- Shared helper scripts for this run are in the scratch dir `.../scratchpad/sct5/` (not committed).

## Step 0: baselines (19:41 to 19:49)
- `AssetMaker.get_catalog_targets(70dcd0b7-..., body={"class":"ResultsRequestBody","graphQueryDepth":0})` -> `No elements found`. Daemon: 52 connector entries in the
  walk, all `WAITING` except two `OpenAPICataloguer` `REFRESHING` (unrelated, as in earlier runs). JDBC `lastRefreshTime` 00:17:20Z. Name searches for
  `scratch_cat_test5`, `scratch-cat-test5`, `scratch_cat_role5`: 0 elements.
- OPEN-METADATA-SECURITY-0011 lines in the 10 minutes before 19:41 (log Z minute buckets): 40 at 00:36, 280 at 00:38, 280 at 00:40 (all `DigitalProductFamily` reads; noise,
  not acted on). None named my user on my elements at any time (grep of the window of each commit).
- Throwaway Postgres (as `postgres` via `docker exec egeria-shared-postgres psql -U postgres -p 5442 -v ON_ERROR_STOP=1`): `CREATE ROLE scratch_cat_role5 LOGIN PASSWORD '<random, memory only>'`,
  `CREATE DATABASE scratch_cat_test5 OWNER scratch_cat_role5`. DDL (quoted identifiers, no password): schemas `plain` (`orders(order_id int, amount numeric)`,
  `customers(customer_id int, cname text)`), `s_x` (`x_y(only_in_x_y int, a text)`, `"xZy"(only_in_xzy int, b text)`), `a_b` (`t_ab(id int, name text)`), `"aXb"` (`t_axb(id int, label text)`),
  `leave_clean` (`lc_t(id int, v text)`), `leave_term` (`lt_t(id int, v text, w text)`), `public.pub_t(id int, v text)`; 3 rows each; USAGE and SELECT to the role.
  `pg_tables` read-back: 9 user tables, 3 rows each. Later `nat_c.nc_t(id int, v text)` (3 rows) for the natural-cycle watch.
- Register: `POST /api/databases/register` (`slug scratch_cat_test5, db_type postgresql, host localhost, port 5442, database_name scratch_cat_test5, egeria_host host.docker.internal, db_user, db_password`) -> 200.
  RE's inventory `POST /api/databases/scratch_cat_test5/survey {"use_egeria": false, "force_custom": true}` -> 200, 7 schemas, 9 tables, 19 columns (after `nat_c`: 8, 10, 21).

## Step 1: collision check (19:42)
`POST /api/catalogue-scope/scratch_cat_test5/nodes {"nodes":[{"schema_name":..} x6],"choice":"catalogue"}`, then `GET .../commit-preview`:
- `can_commit false`, button `Catalogue · 6 schemas`, blocker `2 name collisions Egeria's listing can't tell apart: leave the schema out, or rename it in the database`;
  `collisions`: `⚠ Egeria's listing for x_y in s_x would also return xZy` (kind table) and `⚠ Egeria's listing for a_b would also return aXb` (kind schema).
  `POST .../commit` -> **409** `The commit is disabled: 2 name collisions Egeria's listing can't tell apart: leave the schema out, or rename it in the database`.
- Leave `aXb` out: still `Catalogue · 5 schemas`, same two collisions (known). Leave `a_b` out: one collision left (x_y/xZy), `Catalogue · 4 schemas`. Leave `s_x` out: `can_commit true`, `Catalogue · 3 schemas`, attach `[plain, leave_clean, leave_term]`.
- `POST .../redeclare {}` -> `declared`, `declared {by: rehearsal-runner, kind: "declare"}` (D6 fixed). Owner set via `PATCH /api/context/database/scratch_cat_test5/field {"key":"owner","value":"rehearsal-runner"}`.

## Step 2: COMMIT 1 (refresh ON) 19:49, and the follow-up COMMIT 1b 19:51
Manifest as returned (identical in preview and curation record):
1. `RE publishes the server and database assets and RE's own survey report, supplying the database description and version and the server's own description and version. RE writes no ZoneMembership: zones left to Egeria (EXPLORER_PUBLISH_ZONES is not configured). Owner from Context: rehearsal-runner.`
2. `Egeria's cataloguer creates tables and columns for 3 schema targets (3 to attach now). Each is a schema-kind target, never the database or the server. The elements arrive on the daemon's next refresh, not now.`
3. `Egeria's survey is limited to your chosen schemas: plain, leave_clean, leave_term`
4. `s_x: nothing to remove · never catalogued; aXb: ...; a_b: ...` and `Egeria catalogues whole schemas · table choices are kept for when it can`.

`POST .../commit {"refresh_now": true}` -> 200, curation `8088d976e12844d094a644cc8eed0f41`. Observed (API words next to Egeria):

| time | API | Egeria |
|---|---|---|
| 19:49:14 | log `YAML-SECRETS-STORE-CONNECTOR-0003 Adding client-side secret scratch_cat_test5::PostgreSQL Secret ...scratch-cat-test5.omsecrets` | secrets collection written to my file |
| 19:49:16 | `publish_elements done: server d87059ce · database 5ce419a5 · descriptions and versions supplied`; `owner done: Ownership set to rehearsal-runner` | database `5ce419a5-58a1-4f14-b121-e63b61221701` (RelationalDatabase) from the template, Ownership `rehearsal-runner`, **no ZoneMembership**; no `~{` anywhere |
| 19:49:17-26 | each schema `failed · SERVER_ERROR_500 => Egeria detected error: \`https://localhost:9443/servers/qs-view-server/api/open-metadata/automated-curation/catalog-templates/new-element\`.` · `step: attach · outbox #n · will retry` | log `OMAG-REPOSITORY-0003 ... OMRS-REPOSITORY-400-047 ... relationship that has one or more ends of the wrong or invalid type. Relationship type is DataSetContent; entity proxy 5ce419a5 for end 1 is of type RelationalDatabase rather than DataSet`. **The three DeployedDatabaseSchema elements WERE created anyway** (`a13fd170` plain, `a15661ba` leave_clean, `9e2b6212` leave_term), anchored to the database (`Anchors anchorGUID 5ce419a5`), with no parent relationship, no target |
| 19:49:27 | `survey_report done: report a9afeb89 · 23 annotations`; `survey submitted · 10-06 00:49 · ... engine action dcd0a969` | native survey `dcd0a969` COMPLETED, report `bf39dd15`, 8 annotations |
| 19:50:28 | `refresh done` (connector time moved) | JDBC `lastRefreshTime` 00:17:20 -> 00:50:28.515Z, no targets to refresh |
| 19:50:42 | `read_back done: 3 catalogued · 0 attached, waiting`; header `Database element 5ce419a5 in Egeria · published 10-06 00:49 · zones: none · everyone visible · 3 schemas chosen: 3 failed · cataloguer connector's last refresh 10-06 00:50 (the connector's, not a schema's)`; record `failed` | each schema read back "4 elements" (the template's own connection graph), 0 tables |

**COMMIT 1b** (press again, curation `1d901d7246684e05b3343b88a001ccb8`, refresh ON, 19:51:58): RE found the schema elements by qualifiedName and adopted them (template-first, then Egeria's own attach):
- `publish_elements done: server 5ce419a5 · database 5ce419a5 ...` (**the server GUID printed is the database's own**, see D-E); `owner done: Ownership already names rehearsal-runner`.
- Per schema: queued (outbox #4 to #6) -> `failed · Egeria's attach action 0924b412 is COMPLETED but the target for plain is not in the cataloguer's list yet (still waiting; not attaching a second time) || step: attach · outbox #4 · will retry` (19:52:12/18/26). **Egeria in fact had the target**: `CatalogTarget` relationship `1d4d9b95` for `plain` created 00:52:06.387Z by `postgresqlgovernanceengine`.
- Engine action `catalog-postgres-schema` (plain `0924b412`): requested 00:52:02.717Z, started 00:52:05.961Z, completed 00:52:06.604Z, `activityStatus COMPLETED`, `completionGuards {0=set-up-complete}`, completionMessage `GOVERNANCE-ACTION-CONNECTORS-0032 Integration connector Egeria:IntegrationGroup:Database::JDBCIntegrationConnector is now cataloging DeployedDatabaseSchema server scratch_cat_test5.plain`, `requestParameters` = the nine placeholders RE sent (`versionIdentifier=not recorded, hostIdentifier, databaseName, secretsStorePathName, schemaDescription, secretsCollectionName, serverName, schemaName, portNumber`), processing engine user `postgresqlgovernanceengine`.
- Resulting CatalogTarget: kind SCHEMA (element type `DeployedDatabaseSchema`), `catalogTargetName scratch_cat_test5.plain`, `configurationProperties` = the same nine request parameters plus nothing else. Shape: `{class OpenMetadataRootElement, elementHeader{guid..}, relatedBy{relationshipHeader{guid,..}, relationshipProperties{catalogTargetName, configurationProperties}}, properties}`.
- Survey `7f4c2d54`: APPROVED -> IN_PROGRESS (19:52:27) -> COMPLETED (19:52:38); step `submitted · 10-06 00:52 · Egeria's survey is limited to your chosen schemas: plain, leave_clean, leave_term · engine action 7f4c2d54` then `done · report 3b403eab · 8 annotations · read back 10-06 00:53 · ...`. requestParameters `{includeSchemaNames=plain,leave_clean,leave_term}`. The report RE picked equals the one in the engine action's own completionMessage for all 4 surveys I ran (right pick by time).
- `refresh done: refreshed · connector time moved 10-06 00:51 → 10-06 00:53 (the JDBC cataloguer connector's own time, not a schema's)`; lastRefreshTime 00:53:35.585Z (= what Egeria shows).
- Tables and columns appeared under each schema (plain 2 tables/4 columns, leave_clean 1/2, leave_term 1/3) and **none under the database**: `elements_read_back` proofs, 7, 8, 10 elements. No `~{` placeholder in any of 21 elements checked; **no ZoneMembership on any element** (21 read). Header text: `zones: none · everyone visible`.
- `read_back done: 3 catalogued · 0 attached, waiting` while every node still read `failed` (D-B, D-C).

## Step 3: refresh paths, natural cycle (COMMIT 2, 3 and the watch)
- Created `nat_c`/`nc_t` in Postgres, re-inventoried (`8 schemas, 10 tables`), `POST nodes {nat_c, catalogue}`, preview `Catalogue · 4 schemas`; COMMIT 2 (curation `56bef38a...`, `refresh_now false`, 20:00): steps `refresh | skipped | not asked: the cataloguer's own cycle will pick the targets up`. nat_c failed with the same 500 (element created, no attach), so COMMIT 3 (20:01, curation `ad9be82b...`) adopted it; nat_c target relationship `121128c9` created 01:01:46.646Z.
- Natural cycle: JDBC `REFRESHING` seen 20:25:06 (01:25Z), back to `WAITING` with `lastRefreshTime` **01:26:13.163Z** (20:26:14 CDT); previous time 00:53:35.585Z, so the cycle was 32.6 min. The `nc_t` table first existed in Egeria at the 20:26:14 poll (checked every 60 s).
- RE's words afterwards (after "Read Egeria again", `POST .../read-back` -> `{"catalogued": 4, "attached_waiting": 0, "read_failed": 0, ..., "survey": "settled"}`): header `... 4 schemas chosen: 4 failed · cataloguer connector's last refresh 10-06 01:26 (the connector's, not a schema's)`. So **the connector time on the row is the connector's own `lastRefreshTime` and the header says so**; but the per-schema rows stayed `failed · ... target ... not in the cataloguer's list yet` and tables `as its schema: failed`; the movement queued -> attached -> catalogued **could not be observed through RE's words** (D-B, D-C). Egeria itself: targets present, tables and columns created by `dbcatnpa`.

## Step 4: leave out nothing hangs off it (`leave_clean`) 20:34
- Preview words (6.4 s to build, 8 elements checked): `leave_clean: 4 ConnectToEndpoint · 2 ConnectionConnectorType · 1 DataFlow · 2 EmbeddedConnection · 2 ResourceConnection · 1 Schema hang off it · will be archived in Egeria, not deleted · can't be re-included until Egeria restores archived elements`; button `Catalogue · 3 schemas · archives 1`. **A schema nothing of the steward's hangs off was classed ARCHIVE** (D-A): the soft-delete form was never reachable.
- Press (curation `...`): `leave_outs done | 1 of 1 removed, each with its proof row`; node `archived in Egeria · 10-06 01:35 || Egeria's cataloguer still lists this schema until its connector restarts · nothing is recreated`. Egeria: schema `a15661ba` carries `Memento` (archiveUser erinoverview, archiveMethod `deleteMetadataElementInStore`), drops out of name searches (tree gone from search), the target was detached (`target_detached` proof; target list read back shows only the others), S17 was not looked for with a log grep during the run.
- Re-include (`choice catalogue`): preview `refused [{leave_clean: can't be re-included until Egeria restores archived elements}]` and manifest line `1 schema not committed: leave_clean · can't be re-included until Egeria restores archived elements`. **Re-creation with the same qualifiedName (new GUIDs) was NOT testable** because the form was archive.

## Step 5: leave out something hangs off it (`leave_term`) 20:38
- Throwaway glossary `a6fe011a-eefe-4282-a7fe-d1f71fd109d1` (`scratch_cat_test5_glossary`) and term `8333bd56-c037-410f-a969-a5cd354e7ea1` (`scratch_cat_test5_term`) via `GlossaryManager.create_glossary` and `create_glossary_term` (parentGUID glossary, `CollectionMembership`). Assignment on the TABLE `lt_t` (`0a87c57f-2d63-43e6-8905-df7e1f89f93c`): `ClassificationExplorer.setup_semantic_assignment(term_guid, table_guid, {"class":"NewRelationshipRequestBody","properties":{"class":"SemanticAssignmentProperties","status":"VALIDATED","confidence":100,...}})` -> relationship `be092f5c-43d3-4a36-a77a-7ab633de465e` (returned `None`; found by relationships read, type `SemanticAssignment`, ACTIVE).
- Preview: `leave_term: 4 ConnectToEndpoint · 2 ConnectionConnectorType · 1 DataFlow · 2 EmbeddedConnection · 2 ResourceConnection · 1 Schema · 1 term assignment hang off it · will be archived in Egeria, not deleted · can't be re-included until Egeria restores archived elements` (9 elements checked, 6.9 s). The term assignment IS seen (`SemanticAssignment` on the table, one level down) and named as `1 term assignment`.
- Press: `1 of 1 removed, each with its proof row`; node `archived in Egeria · 10-06 01:39 || Egeria's cataloguer still lists this schema until its connector restarts · nothing is recreated`. Egeria: the top-level schema `9e2b6212` has `Memento`; **the table `lt_t` stays ACTIVE with no Memento, and the SemanticAssignment `be092f5c` survived** (still readable on the table, ACTIVE); the target was detached. Re-inclusion: refused with `leave_term: can't be re-included until Egeria restores archived elements`. (No rename of the top-level schema was seen; this build did not show the ISSUE-117 behaviour.)

## Step 6: in-progress block
Could not hold a window open through a full press: an attach action runs about 1.7 s on the throwaway. I started `catalog-postgres-schema` on `plain` through RE's gateway `initiate_catalog_action` (engine action `27512ffe`) and read the schema's relationships through RE's gateway immediately: for the first ~1.5 s `ActionTarget` carries **no `activityStatus` at all**, and RE's own `running_actions` returned `[('no status stated', '27512ffe')]` (it blocks), then `COMPLETED` at 1.73 s. So the row would read `plain: in use by a running survey · wait or cancel · no status stated`; I did not see that string rendered through a press. Engine action statuses seen on a catalogue action: `APPROVED -> ACTIVATING -> COMPLETED` (**ACTIVATING seen**, 20:39:55; it would block as an unknown value; on a survey: APPROVED, IN_PROGRESS, COMPLETED). The wording says "survey" for any running action (minor).

## Step 7: no-scope retry
Registered `scratch_cat_test5_noscope` (same database, no credential, no scope). `GET .../commit-preview`: `can_commit false`, `nothing to commit: choose at least one schema to catalogue`. `POST .../commit {"refresh_now":true}` and `{"refresh_now":false}` -> **409** `{"detail": "no scope declared · nothing catalogued"}`. No new Egeria element appeared (watcher saw none). The Classic button `☁ Catalogue from its scope, then retry →` is the commit POST (index.html:9194).

## Words the owner will read (verbatim from the API)

| Step | API field | Verbatim | Proof row |
|---|---|---|---|
| collision | `commit-preview.collisions[].text` | `⚠ Egeria's listing for a_b would also return aXb` / `⚠ Egeria's listing for x_y in s_x would also return xZy` | none (scope only) |
| collision | `blockers[0]` | `2 name collisions Egeria's listing can't tell apart: leave the schema out, or rename it in the database` | none |
| manifest 1 | `manifest.lines[re_publishes]` | `RE publishes the server and database assets and RE's own survey report, supplying the database description and version and the server's own description and version. RE writes no ZoneMembership: zones left to Egeria (EXPLORER_PUBLISH_ZONES is not configured). Owner from Context: rehearsal-runner.` | none |
| manifest 2 | `cataloguer_creates` | `Egeria's cataloguer creates tables and columns for 3 schema targets (3 to attach now). Each is a schema-kind target, never the database or the server. The elements arrive on the daemon's next refresh, not now.` (says `4 ... (4 to attach now)` for commit 2 although 3 were already attached) | none |
| manifest 3 | `survey_measures` | `Egeria's survey is limited to your chosen schemas: plain, leave_clean, leave_term` | none |
| manifest 4 | `whole_schemas` | `Egeria catalogues whole schemas · table choices are kept for when it can` | none |
| publish | step `publish_elements` | `server d87059ce · database 5ce419a5 · descriptions and versions supplied` (commit 1b: `server 5ce419a5 · database 5ce419a5 ...`) | `database_published` 1, 11 |
| owner | step `owner` | `Ownership set to rehearsal-runner` / `Ownership already names rehearsal-runner` | `owner_result` 2, 12 |
| attach, failing create | node `words` | `failed · SERVER_ERROR_500 => Egeria detected error: \`https://localhost:9443/servers/qs-view-server/api/open-metadata/automated-curation/catalog-templates/new-element\`.` / `step: attach · outbox #1 · will retry` | outbox 1 to 3 |
| attach, target not seen | node `words` | `failed · Egeria's attach action 0924b412 is COMPLETED but the target for plain is not in the cataloguer's list yet (still waiting; not attaching a second time)` / `step: attach · outbox #4 · will retry` | outbox 4 to 6 |
| attach | step `schema_targets` | `0 of 3 attached, each with its proof row · plain: ...` | outbox rows |
| report | step `survey_report` | `report a9afeb89 · 23 annotations` / `report ? · 23 annotations` | `report_published` 3, 13, 34 |
| survey | step `survey` | `submitted · 10-06 00:52 · Egeria's survey is limited to your chosen schemas: plain, leave_clean, leave_term · engine action 7f4c2d54` then `done · report 3b403eab · 8 annotations · read back 10-06 00:53 · Egeria's survey is limited to ...` | `survey_started` 14, `survey_result` 20 |
| refresh | step `refresh` | `refreshed · connector time moved 10-06 00:51 → 10-06 00:53 (the JDBC cataloguer connector's own time, not a schema's)` / `not asked: the cataloguer's own cycle will pick the targets up` | `connector_read` 15 |
| zones | step `zone_membership` / header | `zones left to Egeria · RE writes no ZoneMembership (EXPLORER_PUBLISH_ZONES is not configured)` / `zones: none · everyone visible` | `zones_read` 19 |
| read back | step `read_back` | `3 catalogued · 0 attached, waiting` | `elements_read_back` 16 to 18 |
| header | `commit.header.text` | `Database element 5ce419a5 in Egeria · published 10-06 00:49 · zones: none · everyone visible · 3 schemas chosen: 3 failed · cataloguer connector's last refresh 10-06 00:50 (the connector's, not a schema's)` | database_published, connector_read |
| tables | table `words` | `as its schema: failed` | follows schema |
| leave out preview | `leave_out[].text` | `leave_clean: 4 ConnectToEndpoint · 2 ConnectionConnectorType · 1 DataFlow · 2 EmbeddedConnection · 2 ResourceConnection · 1 Schema hang off it · will be archived in Egeria, not deleted · can't be re-included until Egeria restores archived elements` | none (relationships read) |
| leave out preview | same, with assignment | `... · 1 Schema · 1 term assignment hang off it · will be archived ...` | none |
| leave out | step `leave_outs` | `1 of 1 removed, each with its proof row` | `archived`, `target_detached` |
| leave out | node | `archived in Egeria · 10-06 01:35` / `Egeria's cataloguer still lists this schema until its connector restarts · nothing is recreated` | `archived` 52 |
| re-include | `manifest.not_committed` | `1 schema not committed: leave_clean · can't be re-included until Egeria restores archived elements` | none |
| already archived | `leave_out[].text` | `leave_clean: nothing to remove · already archived` | `archived` |
| never catalogued | `leave_out[].text` | `s_x: nothing to remove · never catalogued` | none |
| no scope | 409 `detail` | `no scope declared · nothing catalogued` | none |

## Not-verified item -> Answer -> Evidence

| # | Item | Answer | Evidence |
|---|---|---|---|
| 1 | `create_schema_element` with `anchorGUID`/`parentGUID`/`DataSetContent` | **REJECTED** by Egeria: `DataSetContent` has a `DataSet` at end 1 and the database element is a `RelationalDatabase`. Egeria returns 500 but **still creates the schema element (anchored to the database, no parent relationship)**, so the element is created and the step reports failed. | log OMRS-REPOSITORY-400-047 19:49:18 and 19:49:19; schemas `a13fd170`, `a15661ba`, `9e2b6212` exist after the failure |
| 2 | Non-empty `get_catalog_targets`, `get_all_related_elements`, daemon status shape | Non-empty target: `{elementHeader, relatedBy{relationshipHeader, relationshipProperties{catalogTargetName, configurationProperties}}, properties}`; RE's `list_catalog_targets` parser returns empty names/GUIDs for it (D-B). Related-elements and daemon shapes are as parsed. | `PyegeriaCatalogueGateway.list_catalog_targets()` -> nine `CatalogTarget(relationship_guid='', element_guid='', name='')` |
| 3 | `elements_under` literal or regex | Not re-tested for wildcard behaviour (rehearsal 1: literal). Under a real schema it returns tables, columns and the template's connection graph (4 + tables/columns). | `elements_read_back` details |
| 4 | Adoption of a pre-existing `<schema>_schemaType` | Not exercised (no orphan schema type; the cataloguer's schema type `<qn>_schemaType` is attached to tables). The template-created element WAS adopted by the next commit (found by qualifiedName, no second create). | commit 1b |
| 5 | Structural list; term assignment seen on table/column | `ConnectToEndpoint`, `ConnectionConnectorType`, `EmbeddedConnection`, `ResourceConnection`, `DataFlow`, `Schema` are **not** in the structural list, so every catalogued schema reads as "something hangs off it" (D-A). `SemanticAssignment` on the table IS seen and named. | step 4 and 5 previews |
| 6 | Ownership read-before-write; zone read | Ownership: first press `set`, second `already` (read-before-write works; Egeria shows `ownerTypeName UserIdentity`). Zone read: `[]` read cleanly as none; present ZoneMembership parse **skipped** (no admin identity named by the owner). | proofs 2, 12, 19 |
| 7 | Preview cost | One relationships read per element: 8 to 11 elements checked per leave-out, preview 5.1 to 6.9 s for one schema (0.0 s when repeated: cached). | step 4, 5, 6 |
| 8 | Lingering-target sentence | The row reads it verbatim after both archives; the target IS detached on read-back (list no longer has them). S17 (the cataloguer keeps refreshing a removed target) was not checked: the connector's next cycle after the leave-outs did not occur before teardown. | node words, target list |
| 9 | Server template description (D7) | **Not verifiable: RE's publish resolved to rehearsal 1's server `d87059ce` (rule 6: I did not read it) and, on commit 1b, printed the database's GUID as the server.** | proof rows 1 and 11 |
| 10 | Action type initiation on an existing RE element | Works: `PostgreSQLGovernance::catalog-postgres-schema` with `newAsset` = the schema element created by the template, three of three; engine action COMPLETED in ~3.9 s; creates a CatalogTarget with kind SCHEMA and the nine parameters as configurationProperties. It **creates a NEW target relationship each time it is initiated** (no dedupe): 9 targets for 3 schemas after three attaches. | targets list, 20:00 to 20:02 |
| 11 | Poll window (4 x 2 s) vs real attach | Real attach: initiation 00:52:02.7Z, engine action done 00:52:06.6Z, target relationship created 00:52:06.4Z: about 4 s. The window is long enough; **RE still read "not in the list" because it cannot parse the live target shape.** | D-B |
| 12 | `ACTIVATING` status | **Seen** (APPROVED -> ACTIVATING -> COMPLETED) on a catalogue action; it blocks as an unknown value. | step 6 |
| 13 | Template-first-then-process adoption order | Covered: template creates the schema (even through the 500), the action type adopts it; same element, same qualifiedName `PostgreSQL Relational Database Schema::host.docker.internal:5442::scratch_cat_test5.<schema>`. | step 2 |
| 14 | Survey report pick by time | Right pick in 4 of 4 (RE's `survey_result` report GUID equals the report named in the engine action's `completionMessage`). The count and "done" can be wrong (D-F). | `chkrep.py` |
| 15 | `survey_report` GUID | RE's own report guid prints `report ?` on repeat publishes (the report is found by its timestamped name and the GUID is not returned). | commit 1b and 3 |
| 16 | ZoneMembership anywhere | **None** on 21 elements read after commit 1b, none after later commits. Header `zones: none · everyone visible`. | step 2 |
| 17 | Survey request parameter | `includeSchemaNames=plain,leave_clean,leave_term` accepted and honoured (8 annotations: database, 3 schemas, 4 tables); the report RE publishes itself still carries every schema (23 annotations; D5 of rehearsal 1 still open). | engine action properties |

## DEFECTS (symptom, where, test sketch, blocker for the owner's gate)

**D-A. Every leave-out reads as ARCHIVE; the soft-delete form is unreachable. Blocker: YES (gate items 2 and 3).** `STRUCTURAL_RELATIONSHIPS` lacks `ConnectToEndpoint`, `ConnectionConnectorType`, `EmbeddedConnection`, `ResourceConnection`, `DataFlow` (the OpenLineage cataloguer adds it to every schema) and `Schema`; the connection subgraph of every template-created schema counts as "hanging off it". Where: `catalogue_commit.py` `STRUCTURAL_RELATIONSHIPS`/`classify_hangs_off`, `read_hangs_off` (reads the relationships of the schema and all `elements_under(qn::)`, which includes the template's connection graph). Test sketch: build the fake from the recorded live shape of a catalogued schema with no steward link and assert `form == "soft delete"`; add a case with one `SemanticAssignment` and assert archive.

**D-B. RE cannot read a non-empty catalog target list, so it never sees its own targets. Blocker: YES.** Symptom: node rows say `failed · Egeria's attach action X is COMPLETED but the target for plain is not in the cataloguer's list yet` although the target exists; `target_attached` and `attached, waiting` proofs are never written; each retry initiates ANOTHER attach action and Egeria creates ANOTHER CatalogTarget relationship (9 targets for 3 schemas; I ran one `drain_outbox` pass by hand which added 6, and each commit press added 3 to 4). With the worker's 15 minute retry loop this would add targets forever. Where: `catalogue_gateway.PyegeriaCatalogueGateway.list_catalog_targets` reads `item["relationshipHeader"]`, `item["properties"]["catalogTargetName"]` and `catalogTargetElement/relatedElement/element`; live: `item["relatedBy"]["relationshipHeader"]["guid"]`, `item["relatedBy"]["relationshipProperties"]["catalogTargetName"]`, `item["elementHeader"]["guid"]`. Per D2's lesson an unrecognised non-empty answer must RAISE. Test sketch: record the live target payload (in `scratch/sct5/targets_c1.json`) as a fixture; assert the parser yields name, relationship GUID and element GUID; assert a second apply on an attached schema reports `already_attached` and initiates nothing.

**D-C. The newest outbox row's `failed` outranks a newer read-back proof; catalogued schemas read `failed`. Blocker: YES.** After the read-back found tables (`elements_read_back` rows 16 to 18, 4 catalogued), `_schema_state` still returns the outbox row's `failed`, and tables read `as its schema: failed`; the header says `3 failed` next to `read_back: 3 catalogued`. Where: `catalogue_commit._schema_state` (outbox status checked before proofs). Test sketch: outbox `failed` older than an `elements_read_back` proof -> state `catalogued`.

**D-D. `create_schema_element`'s parent relationship is invalid; the failure leaves orphan schema elements and the word hides the cause. Blocker: YES.** `DataSetContent` needs a `DataSet` at end 1. Egeria still creates the element (so the next commit adopts it), but every first commit reports `0 of n attached`, and the row's first sentence is only `SERVER_ERROR_500 => Egeria detected error: \`.../catalog-templates/new-element\`.` (D4's "first sentence" drops the useful part: the reason is in the platform log). Where: `catalogue_gateway.create_schema_element` (`parentRelationshipTypeName: "DataSetContent"`, `parentAtEnd1: True`). Fix options: no parent (anchor only, as the first rehearsal's `x_probe` and the read-back showed), or a valid database-to-schema relationship; test sketch: the fake rejects `DataSetContent` on a non-DataSet end; assert the commit's first press creates and attaches.

**D-E. The publish step prints the DATABASE guid as the server on a repeat commit. Blocker: no (words).** Commit 1 printed `server d87059ce` (rehearsal 1's leftover server: RE found-or-creates by name, so it attached to a server that belongs to the earlier unfinished run; I did not read or modify it); commits 1b to 5 printed `server 5ce419a5`. Where: `egeria_database_surveyor` server find-by-name (`host.docker.internal:5442` also matches the database's qualified name prefix?). Test sketch: publish twice, assert `database_published.server_guid` is a SoftwareServer.

**D-F. Survey step says `done` while the engine action is still IN_PROGRESS, with a partial annotation count. Blocker: no (wording).** Commits 2, 3, 4: `done · report df9fbc5b · 9 annotations`, `d545c2df · 7 annotations`, `b9991301 · 4 annotations` while the reports end with 10, 10 and 10 annotations (the `survey_result` proofs record `action_status: IN_PROGRESS`). Where: `catalogue_commit` survey read-back (`outcome annotated` is accepted when the action is not finished). Test sketch: engine action `IN_PROGRESS` with partial annotations must stay `submitted`.

**D-G. Step `leave_outs` says `removed` for an archive; `survey_report` can print `report ?`; the running-block wording says "survey" for any action; the manifest says `(4 to attach now)` for schemas already attached. Blocker: no.**

**D-H. `read_back` step says `3 catalogued` when only the template's own elements exist (4 elements, 0 tables). Blocker: no, same family as D-C** (a schema with zero tables was meant to stay "attached, waiting"; here the schema counted its connection subgraph as catalogued content). Where: `read_back`/`elements_under` count of any element.

**D-I. `will retry` is a promise only a worker keeps.** A `failed` row says `will retry` but nothing in the web process retries (the 15 minute loop is in the worker) and the retry, per D-B, adds targets. Blocker: yes when combined with D-B.

## The owner's six gate items (as far as a throwaway can judge)

1. Manifest lists the three mechanisms, target count and survey list; Catalogue; steps land; queued -> attached/waiting -> catalogued: **FAIL** (manifest and survey list PASS; the per-schema words go queued -> failed and never reach attached or catalogued: D-B, D-C, D-D). Tables and columns appear under each schema and nothing under the database: **PASS** (Egeria side).
2. Leave out a schema with nothing hanging off it: soft delete, then re-create with new GUIDs: **FAIL** (archive form every time: D-A; re-creation not testable).
3. Leave out one with a term assignment: names it and says archive; `archived`; choosing again refused with the S19 sentence: **PASS** for the wording, the archive, the refusal; (the assignment survived on the table).
4. Colliding names flagged before the press: **PASS** (a_b/aXb, x_y/xZy; the commit blocked with 409).
5. Egeria restart keeps scope and states: **not exercised** (no restart permitted).
6. Classic shows no database Publish; header reads from state: header reads from state: **PASS** (it changed with every proof: `not yet catalogued`, `3 queued`, `3 failed`, `zones: none · everyone visible`); Classic UI **not exercised** (no browser).

## What was NOT exercised live, and why
- Soft delete of a schema and re-creation with the same qualifiedName (D-A).
- queued -> attached/waiting -> catalogued words (D-B, D-C).
- A rendered `in use by a running survey · wait or cancel · <status>` row through a full press (the window is about 1.7 s).
- A present ZoneMembership read (skipped by instruction), the Egeria restart/reset behaviour (gate item 5), Classic UI.
- `EXPLORER_PUBLISH_ZONES` (not configured by design).
- The a_b/aXb listing collision against the cataloguer's own LIKE (the colliding schemas were left out).
- The S17 404-007 lines after the engine actions were deleted: the log shows `ENGINE-HOST-SERVICES-2000 executeEngineAction caught an exception ... engine action 7eeadf3e` repeatedly after teardown (the engine host keeps polling a deleted action). No connector or daemon was restarted.

## State changes outside my own throwaway
- RE's publish used rehearsal 1's server `d87059ce` (see D-E). I did not read or write it myself.
- I made one by-hand `drain_outbox` pass (RE's own function) which created 6 duplicate CatalogTarget relationships; all duplicates were removed by relationship GUID right away (list kept to the three real targets, then none).

## Teardown (20:44 to 20:50)
- RE server, runner and watchers stopped. Targets removed by relationship GUID, read back: none (`No elements found` / empty list).
- Deleted per element (`delete_metadata_element(..., SOFT_DELETE, forLineage true, forDuplicateProcessing true, cascade_delete false)`), leaf first by type: 167 elements from the inventory (annotations 79, survey reports 8, engine actions 30, columns, tables, schema types, glossary term and glossary, schemas including the two archived ones `a15661ba` and `9e2b6212`, connections and endpoints, the database `5ce419a5`, the secrets collection and file elements, my secrets-store asset graph). All deletions were accepted, including the archived schemas; the inventory is in the scratch dir (`inventory_pre_td.json`).
- Postgres: `DROP DATABASE scratch_cat_test5 WITH (FORCE)`, `DROP ROLE scratch_cat_role5` -> counts 0. `scratch-cat-test5.omsecrets` removed; directory file names equal the pre-run list.
- Not touched: `d87059ce`, `edb12250`, `scratch_cat_test3`, `scratch_cat_test4`, shared registry, `.env`, 8810/8811/8813, Prefect.

The platform is as it was, except for: the engine host's repeated `ENGINE-HOST-SERVICES-2000` errors for the deleted engine actions (Egeria's own retry), the shared leftovers of rehearsal 1 which RE's publish also pointed at (`d87059ce` server graph, `edb12250` database graph), and Egeria's audit/lineage record of the deleted elements.
