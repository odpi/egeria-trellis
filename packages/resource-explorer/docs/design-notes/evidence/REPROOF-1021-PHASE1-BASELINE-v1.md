# Archive-cascade re-proof, phase 1 baseline (v1, before the cataloguer's tables)

Captured 2026-10-22 ~11:40Z by the Wednesday Coordinator, read-only, from Egeria quickstart `egeria-quickstart-platform:local` (sha256:ed4a6609, built from odpi/egeria-platform:latest@sha256:621ca8e0, 6.3-SNAPSHOT, container started 2026-10-22T00:36:20Z). Full data: `REPROOF-1021-PHASE1-BASELINE-v1.json` (37 elements, 63 edges, anchors, createdBy).

## The press
- Curation 2a8727fc on `laz_local_re_cascade_reproof_1021`, POST /api/catalogue-scope/…/commit at 11:11:06Z from the owner's signed-in browser pane as **peterprofile**; RE's service account refuses login by design. Owner present: "I'm signed in - you can go". Peers cleared: architect, egeria-workspaces-fs-41, pyegeria session.
- Before the press: the two earlier presses (abb3dd2f, 515dd1b6) had failed at the server lookup timeout and written nothing; preview: can_commit, "Publish · 2 schemas", no blockers.
- Result (done 11:12:10Z): server 9aca0c49 **adopted** by the unique-name read (the fast lookup, #835, proven live); database created; 2 schema targets attached with proof rows; RE SurveyReport published; Egeria's survey submitted and completed in 2.6 s.

## Identity evidence (architect item 1)
Pressed by a person (peterprofile), yet every element RE wrote has `createdBy=resourceexplorernpa`: the synchronous catalogue-commit path still publishes as the service account. #836 moved only queued definition runs; the commit path is on its listed follow-ups. Egeria's own survey report and its 7 annotations: `postgresqlsurveyengine`.

## The tree (GUID prefixes; full GUIDs in the JSON)
| element | GUID | anchor |
|---|---|---|
| RelationalDatabase re_cascade_reproof_1021 | c79be6db-c959-48a2-b96f-c71249f47e1e | self |
| VirtualConnection (db) 27da987d · Endpoint 4d15c08e · secrets Connection 14f65d8d · secrets Endpoint 813ecc44 | | RelationalDatabase c79be6db |
| DeployedDatabaseSchema alpha 4ffa5fec-fb9d-47a8-93fa-ad5490b46713 | | RelationalDatabase c79be6db |
| alpha: VirtualConnection 909e62fc · Endpoint 515fc5ec · secrets Connection 94e096f8 · secrets Endpoint 5c59160b | | DeployedDatabaseSchema 4ffa5fec |
| DeployedDatabaseSchema beta 2f9a40a0-6f0b-4f37-9522-ea12407b5745 | | RelationalDatabase c79be6db |
| beta: VirtualConnection cc615faa · Endpoint 8a95b5ec · secrets Connection 306f208e · secrets Endpoint 807c8076 | | DeployedDatabaseSchema 2f9a40a0 |
| SurveyReport (RE) 1b09c959-08a9-42cb-bb67-0acdb676963f, 13 annotations by paged read | | self (RE's report anchors to itself) |
| SurveyReport (Egeria) 1c9917fa-6082-4b09-9b10-05737a0a8148, 7 ResourceMeasureAnnotations by paged read | | RelationalDatabase c79be6db (annotations too) |
| ConnectorType "Relational Database JDBC Connector" 64463b01 | shared, Egeria Project | not part of the tree |
| EngineAction survey-postgres-database 24cbda3f (completed); catalog-postgres-schema 8e58929e (alpha), 53fd20b9 (beta): "JDBCIntegrationConnector is now cataloging" | | EngineAction (self) |
Tables and columns: **none yet** (the integration connector builds them on its refresh); v2 of this baseline records them.

## Connection graph by anchor (architect item 2)
Every connection, endpoint and secrets-store connection RE created is anchored to its owning asset: the database's to the database, each schema's to that schema. The 10-06 cascade took the connection graph by anchor; this is the set phase 2 must leave intact when alpha is left out (expected: alpha's 4 connection-graph elements and alpha's schema go; beta's 4 and the database's 4 stay).

## Server
SoftwareServer 9aca0c49-3857-4464-8725-4461def488be (`PostgreSQL Server::host.docker.internal:5432`, createdBy erinoverview, from re_throwaway_62): **no edge to the new database** (its edges: ServerEndpoint→Endpoint 1, SupportedSoftwareCapability→DatabaseManager 1, ResourceConnection→VirtualConnection 1, ReportSubject→SurveyReport 8, ActionTarget→EngineAction 8, and 3,443 AssociatedAnnotation→ResourceMeasureAnnotation). RE "adopts" the server but links nothing from the database to it; whether a DeployedDatabaseSchema/Database ↔ DatabaseManager link is expected is a design question for the architect. The 3,443 annotations anchored to one server are the annotation-volume issue in numbers.

## Wording noted
read_back said "0 published · 2 attached, waiting" next to "publish_elements done": "published" there counts schema targets the cataloguer has built (catalogue_commit.py:2527), not what RE published. Candidate for the status-words list.

## v2 addendum (13:12Z): the cataloguer's tables and columns have arrived
Captured to `REPROOF-1021-PHASE1-BASELINE-v2.json` after Egeria's JDBC integration connector built the schema contents (about two hours after the press). Each DeployedDatabaseSchema now has a RelationalDBSchemaType (Schema edge) with 2 RelationalTables and 6 RelationalColumns (alpha: widgets, widget_parts; beta: gadgets, gadget_notes). Anchors and creators per element are in the JSON (summary printed in the coordinator's log). Phase 2 expectation now includes them: leaving out alpha must remove alpha's schema type, its 2 tables and 6 columns, and alpha's 4 connection-graph elements, while beta's and the database's stay.

## v3 addendum (16:10Z): platform upgraded under the tree; baseline unchanged
Egeria quickstart was upgraded at 15:59Z to odpi/egeria-platform:latest@sha256:a8bcff5c (6.3-SNAPSHOT, odpi/egeria c6dbf952, #9378; local egeria-quickstart-platform:local a5f941a1). Re-read into `REPROOF-1021-PHASE1-BASELINE-v3-post-upgrade.json`: the same 55 elements, same types, anchors, creators and versions as v2; 103 edges kept, 3 gained (listed in the coordinator's log; engine-action/report bookkeeping, no tree change). Rule (architect): **tree built on 621ca8e0, archive tested on a8bcff5c**; phase 2 runs on, and cites, a8bcff5c. If another upgrade lands before phase 2, re-take the reads again under the same rule.
