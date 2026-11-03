# Curate: the catalogue commit for databases, by schema-kind targets (implemented)

2026-10-05, branch `re/curate-catalogue-commit-databases`, built on `origin/main` 27a11316 and merged with
`origin/main` d71be2cf (PR #483, the scope header and collapse). Brief:
`BRIEF-CURATE-CATALOGUE-COMMIT-DATABASES.md`. **Built against a fake Egeria only: nothing here has run against a live
Egeria**, by instruction. Every live question is listed under "Not verified without live Egeria".

Verified import path: `uv run python -c "import resource_explorer; print(resource_explorer.__file__)"` printed
`/Users/dwolfson/localGit/egeria-v6/trellis-re-slice-b/packages/resource-explorer/resource_explorer/__init__.py`,
inside the builder's own worktree. Tests ran with `PGVECTOR_PORT=1` and temp SQLite registries (asserted in the fixtures).

**Decision (project owner, 2026-10-05):** "Go with the schema-kind door." One SCHEMA-kind catalog target per chosen
schema; revisit as Egeria changes.

## What was built

| Piece | Where |
|---|---|
| The commit: preview and manifest, collision check, leave-out forms, re-inclusion rules, proof-row state derivation, read-back, the run's steps | `resource_explorer/catalogue_commit.py` (new) |
| The one door to Egeria (a `CatalogueGateway` protocol and the pyegeria implementation, with the evidence cited per call) | `resource_explorer/catalogue_gateway.py` (new) |
| `catalogue_commit_proofs`, an append-only table, plus `append_/list_catalogue_commit_proofs` and `list_catalogue_outbox_rows` | `registry.py` |
| Outbox kinds `catalogue_schema_attach` and `catalogue_schema_leave_out` (self-resolving, registry and gateway on `OutboxClients`) | `egeria_outbox.py` |
| Run kind `catalogue_commit` and its handler | `registry.py` (`RUN_KINDS`), `run_queue.py` |
| Routes: `GET /{slug}` now carries `commit`; `GET /commit-preview`, `POST /commit`, `GET /commits[/id]`, `POST /read-back` | `web/routes/catalogue_scope.py` |
| The manifest, per-node "In Egeria" states, the Catalogue button, the steps, the state-derived header marker | `static/next/stages/curate-scope.js`, `glyphs.js`, `re-api.js` |
| Classic's database Publish button and modal removed | `static/index.html` |
| Database template gets `databaseDescription` and `versionIdentifier`; `publish_local_survey(survey_after_catalog=False)` | `egeria_database_surveyor.py` |
| Depth is now a tree view (`commit_honours`), its lever wording removed | `catalogue_scope.py` |

## The steps, in the manifest's order

(Order as changed by the rehearsal fixes below; the zone step moved from second to next-to-last.)

`publish_elements` (server and database, no survey), `owner` (added after the database exists; a refused change reads
"owner set by Egeria's source · can't change from RE"), `schema_targets` (outbox rows, drained at once), `leave_outs`,
`survey_report` (RE's own report and annotations, no native survey), `survey` (Egeria's, with `includeSchemaNames`; reads
"submitted · <time>" until a read-back shows the report with annotations), `refresh` (optional;
`refresh_integration_connectors("JDBCDatabaseCataloguer", daemon, 120)`; RE never restarts a connector, asserted by a test;
reads "refreshed" only when the connector's own time moved), `zone_membership` (written ONLY when `EXPLORER_PUBLISH_ZONES` or
`egeria.default_catalog_zones` is configured, and then LAST), `read_back`. A step that fails is a failed step; what depends on
it is skipped with the reason.

## Decisions

* **Proof rows.** A schema is `catalogued` only when a read-back found elements under its qualified name;
  `attached, waiting` when the target was read back with none; `queued`/`failed` are the newest outbox row's own
  status and `last_error` (Egeria's word, class prefix stripped); `removed` and `archived` are written only after the
  absence or the Memento is read back; `left out` is the scope record and carries no glyph. A test deletes the rows and
  the words go with them. A schema with zero tables stays "attached, waiting" (it can never be read back as catalogued).
* **Header marker.** `#483`'s constant is now `derive_commit_state(...)["header"]`: the old sentence only while no row
  exists. The JS holds no copy of the words; `#483`'s pinned tests were updated to say so.
* **Per-schema, not per-commit, blocking** for a failed relationships read and for an archived schema chosen again
  ("couldn't check what hangs off it"; the S19 sentence): that schema is left exactly where it is and the commit proceeds for
  the rest. Name collisions disable the whole commit (the brief), and the only way out is leaving the wildcard-bearing schema out.
* **Hang-off is a negative list** (`STRUCTURAL_RELATIONSHIPS`): any relationship type not known as the cataloguer's own
  structure counts, so an unknown type errs towards ARCHIVE (keeps it), never a soft delete. The form is re-derived at press
  time and the safer one wins.
* **Undecided schemas already in Egeria stay targets** and in the survey list (the scope's rule: only an explicit leave out removes).
* **Survey list**: comma-bearing names are left out of it and named; an empty list starts no survey (an empty parameter
  would survey everything).
* **Depth.** A schema-kind target always creates tables and columns and Egeria has no depth option (S2), so depth is
  a tree view; the earlier "include list" wording would now be a false claim and is gone.
* **Version** is "not recorded" (RE keeps no PostgreSQL version): honest beats a made-up "1.0".
* **Classic**: the button and modal are gone, and the survey-definition retry now goes through the commit (see the architect rulings
  below). `POST /api/databases/{slug}/publish` stays for Classic API callers only; it starts an unscoped survey (documented on the route).
* A queued attach checks the scope again when it runs: if the schema was left out in the meantime it does nothing.

## Migration (for the coordinator's peer check)

One new table, **`catalogue_commit_proofs`** (+ index `idx_catalogue_commit_proofs_node`), `CREATE TABLE IF NOT EXISTS`,
no foreign keys, no colons in comments (the Postgres translator). No column added to any existing table; `runs.kind` has
no constraint, so the new kind needs nothing. Covered by `test_migration_is_additive_and_an_old_shape_database_opens`
(drops the table, reopens, existing rows untouched). `remove_database` deletes its proof rows. New env var
`EGERIA_INTEGRATION_DAEMON` (default `qs-integration-daemon`).

## Evidence of the red runs

* Unchanged `origin/main` (d71be2cf) with the new tests copied in: `test_catalogue_commit.py` cannot import
  (`resource_explorer.catalogue_gateway` missing); `test_classic_database_publish_retired.py` 3 failed;
  `test_catalogue_scope.py` 2 failed (depth); the two harness files 17 failed.
* Sixteen mutations on a copy, each breaking one guard on purpose, all went red: header made a constant; state taken from the
  branch not the rows; collisions ignored; leave-out always soft delete; targets not read before attaching; comma names joined into
  the survey; zones check skipped; archived re-inclusion allowed; orphan schema type not adopted; catalogue kinds not self-resolving;
  parent-first delete order; template placeholders dropped; archive flags dropped in the gateway; targets read with pyegeria's
  default body; and two in the JS (header constant, state-cell constant).
* Full suite (10 shards, `PGVECTOR_PORT=1`): green except `test_architecture_doc_lens ... test_a_site_only_project_reports_every_site_it_found`,
  which fails identically on `origin/main` (a connection-refused message where the test expects a sentence; pre-existing).
  `test_prefect_survey_flow` failed once under 10-way load and passes alone. Two tests of mine-adjacent code were updated, not loosened:
  the dynamic-class tripwire (one interpolation removed from the JS instead of raising the count) and the Classic publish test (the
  code it pinned was retired). Render harness: 455 of 455.

## Owner's gate, on 8810 after merge, coco_pharma, the 29-schema scope

1. Open Curate, expand the scope: the manifest lists the three mechanisms, the target count and the survey's schema list.
   Press Catalogue (leave "refresh now" ticked): the steps land, each schema reads queued, then attached/waiting, then
   catalogued with a read-back time; tables and columns appear under each schema and nothing under the database
   (`PostgreSQL Relational Database::<server>::<db>::` has no children).
2. Leave out a schema with nothing hanging off it: the preview says soft delete; after the press "removed · was catalogued";
   choose it again and press: it is re-created with new GUIDs.
3. Leave out one with a term assignment: the preview names it and says archive; then "archived"; choosing it again is refused with the S19 sentence.
4. Two colliding names (`a_b` and `aXb`, `x_y` and `xZy`) are flagged before the press; coco_pharma may have none, then the fixtures prove it.
5. After an Egeria restart that keeps the store, the scope and states survive; after a reset, pressing again recreates from the scope record
   (tested with a fake reset).
6. Classic shows no database Publish; the header reads from state (change the state, the line changes).

## Not verified without live Egeria (read these first on the first live run)

1. `create_schema_element` sends `anchorGUID`, `parentGUID`, `parentRelationshipTypeName` DataSetContent: the scratch runs created
   the schema with no parent. The brief says "under the database element" so zones copy; if rejected, drop those fields and set the zones another way.
2. The shape of a non-empty `get_catalog_targets` answer (relationship GUID and element keys), of `get_all_related_elements`
   (relationship type names), and of the daemon status (`connectorName`, `lastRefreshTime`): parsed tolerantly, not observed.
3. `elements_under` uses a starts-with name search then filters client-side; whether the server treats the string as a literal or a regex was not established.
4. Adoption of a pre-existing `<dbQN>::<schema>_schemaType` links it with the `Schema` relationship (schema at end 1; it was `AssetSchemaType` until 2026-10-06, a name that is in no Egeria source); whether the cataloguer's schema-kind scheme even
   produces that name was not observed.
5. The structural relationship list, and that a term assignment on a column is seen by the relationships read of the table and column elements.
6. The Ownership read-before-write shape; the zone read-first (`current_zones`) against a real security connector.
7. Cost: the preview makes one relationships read per element of every schema being left out that was ever catalogued.
8. The lingering-target sentence is the brief's wording; the read-back proves the detach, and S17 (a removed target keeps being refreshed) is the recorded behaviour, not something each read re-observes.
9. The server-template placeholder for its description (S15 was only isolated for the database template). The rehearsal answered it:
   the server takes `description` (it received the database's) and leaves `versionIdentifier` unresolved; both fixed (D7), not re-run live.

Still open after the rehearsal fixes (nothing below has been seen live): items 1, 4, 5, 7 and 8 above; the engine action's
`actionStatus`/`completionMessage` property names and the newest-report-since pick used by `survey_outcome`; the live shape of a
non-empty `get_catalog_targets` answer; whether a configured `EXPLORER_PUBLISH_ZONES` zone leaves the service identity, the survey
engine and the cataloguer able to write later (see D1); and that the zone read-back parses the live ZoneMembership classification.

## Architect rulings (2026-10-05) and what they changed

**Decision (architect, 2026-10-05):** (1) The split is confirmed: a failed relationships read or a refused re-inclusion blocks only
that schema, and the manifest names it in the form "1 schema not committed: s_x · couldn't check what hangs off it" (or "... ·
can't be re-included until Egeria restores archived elements") while the rest proceeds; a name collision blocks the whole commit.
Built as the `not_committed` manifest line and `manifest.not_committed`.
**Decision (architect, 2026-10-05):** (2) RE itself never calls the unscoped publish route. The survey-definition "catalogue, then
retry" button now POSTs the commit (`/api/catalogue-scope/{slug}/commit`), which compiles the stored scope; with no scope declared the
commit stops with "no scope declared · nothing catalogued", before any gateway is built or Egeria is called, and queues nothing. The
commit is queued work, so the survey definition is not auto-retried: the toast says to run it again when the commit has finished.
**Decision (architect, 2026-10-05):** (3) Refresh-now default stays: UI on, API off.
**Decision (architect, 2026-10-05):** (4) The database version stays "not recorded".

**Decision (project owner, 2026-10-14), superseding the S19 wording above:** Egeria archives an element by renaming it
`<qn>_archivedOn_<date>` (a Memento), so the original name is free and a re-include creates a NEW element. Before any
create that would re-include one (schema attach, database publish) RE looks for the `_archivedOn_` sibling
(`catalogue_gateway.archived_siblings`, one routine `catalogue_commit.reinclude_check` for preview, press and run). If
one exists it does not create: the row reads "archived in Egeria · <date> · re-including creates a new element; the
archived record stays" with a Re-include control. Only a press with `confirm_reinclude` naming the newest archived
GUID creates, re-checked at the press (409 if it changed) and again in the run; the create's `reincluded` proof row
records `archived_sibling_guid` and the date. A lookup that cannot be made creates nothing ("could not check whether
an archived copy exists"). "Can't be re-included until Egeria restores" is retired.

**Backlog:** `POST /api/databases/{slug}/publish` stays only for Classic API callers and its docstring says it is unscoped; a follow-up
makes it scope-aware or removes it. Also unscoped and not touched here: `HybridDatabaseSurveyor` (the `egeria-adaptive` survey path)
still calls `publish_local_survey`/`catalog_and_survey`, which catalogue and start an unscoped survey; that is a separate follow-up.

## Rehearsal fixes (follow-up to PR #485, from the live rehearsal of 2026-10-05)

Source: `evidence/REHEARSAL-CURATE-COMMIT-DATABASES-2026-10-05.md`. D5 (RE's own survey report names left-out schemas) is waiting on
the project owner and D8 (no remove for the secrets projection) is a separate follow-up; neither is in this change.

| # | Defect | Status | What changed |
|---|---|---|---|
| D2 | Gateway parsed invented shapes: every read came back empty | fixed, tests only | `catalogue_gateway.parse_element_answer`/`parse_elements_answer`/`parse_related_answer` read the LIVE shapes (`elementGUID`, `type.typeName`, `elementProperties.propertiesAsStrings`, the related-elements dict with `elementList`). An answer they do not recognise RAISES `GatewayError`, so a shape change can never again read as "nothing there" (and so never as "nothing hangs off it"). `tests/live_catalogue_payloads.py` holds the recorded shapes; `FakeEgeria` builds its payloads with the same builders and its reads go through the real parsers, so the whole commit suite runs on live shapes. A test asserts the fake's payloads equal the fixtures. |
| D1 | The zone write locked the identity, the survey engine and the cataloguer out of the database element | fixed in code, NOT verified live | The commit writes no zone unless `EXPLORER_PUBLISH_ZONES` (or `egeria.default_catalog_zones`) is configured; the hard-coded `egeria-runtime` fallback is no longer written. Unconfigured, the step reads "zones left to Egeria" and the database row shows "zones: <list> · set by Egeria", read back (`zones_read` proof). Configured, the zone is the LAST write (after the targets, the survey submission, the refresh and the owner), is read back, is skipped if any earlier step failed, and a refusal reads in Egeria's words and is never retried. Egeria offers no dry check of "can I still write after this zone change", so it is last, not first. The manifest, steps and proof rows say so. |
| D3 | `survey` read "done" on initiation; `refresh` read "done" with no read | fixed | `survey` reads "submitted · <time>" (state `submitted`) until a read-back (the commit's own, or "Read Egeria again") finds the survey report WITH annotations, then "done"; a failed engine action reads Egeria's word; a report with 0 annotations stays "submitted". `refresh` reads "refreshed · connector time moved" only when the connector status's `lastRefreshTime` moved across the call, else "refresh requested" (state `requested`). New proofs: `survey_result`, `zones_read`. No table changes. |
| D4 | Raw multi-part Egeria message on failed rows; "attachd" | fixed | Failed schema rows and failed steps show the first sentence (`egeria_first_sentence`) and fold the rest under "details" (collapsed). The typo came from `f"{what}d"`; the outbox step now uses real past tenses. |
| D6 | First declaration recorded as "redeclare" | fixed | The first explicit declaration is kind "declare"; only a later one is "redeclare"; the implicit baseline a first choice makes stays "first". One existing assertion changed with it (`['first','redeclare']` became `['first','declare']`). |
| D7 | Server element kept `~{versionIdentifier}~` and took the database's description | fixed | The server template now gets `versionIdentifier` and its own description (`PostgreSQL server at <host>:<port>`). |

**A consequence worth knowing before the gate:** with a zone configured, a SECOND commit writes anchored elements under a database
element that now carries RE's zone. If that zone is not one the service identity may write in (the rehearsal's evidence), that second
commit fails the way the first did. The fake models this lockout (`zone_lockout`, on by default) and the ordering test relies on it;
**Decision (architect, 2026-10-05):** the first live use of a configured `EXPLORER_PUBLISH_ZONES` goes on a throwaway database with TWO
commits, the second exercising anchored writes under the zone. The zone setting is **unverified for a second commit**; this is stated
on the `default_catalog_zones` field in `config.py`, in `docs/admin-guide.md` and in `docs/Architecture.md`.

**Decision (architect, 2026-10-05):** `ActionTarget` is structural (added to `STRUCTURAL_RELATIONSHIPS`): an engine action targeting a
schema is Egeria's own machinery and never makes a leave-out an archive. The companion exception (an engine action still running on a schema blocks its leave-out) was first skipped for want of a recorded status field and is built in the next section.

## Step 2 from the read-back (successor branch re/curate-commit-step2-from-readback)

Source: the read-back evidence note `READBACK-ZONES-AND-SCHEMA-PROCESS-2026-10-05.md` (branch `re/readback-zones-schema-process-evidence`). Every shape
below is a recorded live shape (`tests/live_catalogue_payloads.py`), and the fake Egeria emits them.

1. **Step 2 stays RE's template creation, then Egeria's own attach.** RE creates the `DeployedDatabaseSchema` from the schema template with the
   deterministic qualifiedName `PostgreSQL Relational Database Schema::<host:port>::<db>.<schema>`, parented under RE's database element (as before).
   It then PREFERS Egeria's GovernanceActionType `PostgreSQLGovernance::catalog-postgres-schema` (a type, not the process) with the schema as action
   target `newAsset` and the template's request parameters (`schemaPlaceholders`: databaseName, serverName, hostIdentifier, portNumber, schemaName,
   schemaDescription, secretsCollectionName, secretsStorePathName, versionIdentifier; the type copies them into the target). The target is read back
   (a few polls, Egeria attaches on its own side). It FALLS BACK to `add_catalog_target` only when the action type errors or its engine action ends
   FAILED/INVALID/CANCELLED/IGNORED. An action still running, or completed with no target, is an ERROR to retry, never a reason to attach a second time.
   The `target_attached` proof row records `mechanism` (`action_type`, `add_catalog_target` or `already_attached`), the engine action GUID and any
   fallback reason.
2. **Zones.** The commit writes no zone. The read-back found NO ZoneMembership on any element created by the template, the process, the cataloguer or the
   survey, so nothing on this build assigns a default zone, and all five identities (erinoverview, postgresqlsurveyengine, dbcatnpa,
   postgresqlgovernanceengine, olcatnpa) wrote without refusal. The database row reads "zones: none · everyone visible" when the read-back shows no
   ZoneMembership, "zones: <list> · set by Egeria" when it shows one, and "zones: not read back" when the read failed (`read_zones` raises on an
   unreadable answer; `[]` means "none"). `EXPLORER_PUBLISH_ZONES` stays an optional override (last write, **unverified for a second commit**).
3. **A running engine action blocks that schema's leave-out.** The relationships read now carries each `ActionTarget`'s own `activityStatus`
   (`relationshipProperties`), `completionTime` and the action's `completionMessage`. REQUESTED, APPROVED, IN_PROGRESS and any value never seen block the
   leave-out for THAT schema with "<schema>: in use by a running survey · wait or cancel · <status>" (the rest of the commit proceeds; re-checked at press
   time, nothing is deleted); COMPLETED and FAILED do not. `ActionTarget` stays structural for the archive-versus-soft-delete choice. The engine action's status
   attribute is `activityStatus` (not `actionStatus`; `survey_outcome` was corrected).
4. **Non-deterministic identifiers** (process instance, engine actions, surveys, reports) are stored on proof rows at submission (`engine_action`,
   `target_guid`) and never reconstructed; schema, table, column and connection names are deterministic and read by name. A test pins that every name the
   commit builds contains no GUID, epoch or timestamp.

**S21 for the Egeria list:** the schema CreateAsCatalogTarget process takes only text placeholders and cannot adopt an existing database element or anchor
the schema under it; either it should accept the database element (action target) and anchor the schema there, or the `catalog-postgres-schema` action
type should be documented as the attach-only step it is.

**Still unverified live:** the action type's initiation with an EXISTING element created by RE's template (the read-back ran the process, not the type on
its own); the `ACTIVATING` status (not sampled; it would block as an unknown value); template-first-then-process adoption (only process-first was run); the
polling window (4 reads, 2 s apart) against a real attach; the zone read-back parse of a present ZoneMembership (none was ever seen on this build).

## Rehearsal 2 fixes (branch re/curate-commit-fixes-from-rehearsal-2)

Source: the combined rehearsal's evidence note `REHEARSAL-2-CURATE-COMMIT-DATABASES-2026-10-05.md` (branch `re/combined-rehearsal-evidence`). Payloads are built from its
recorded live shapes (`tests/live_catalogue_payloads.py`); code and tests only, nothing was run against Egeria.

| # | Defect | Status | What changed |
|---|---|---|---|
| D-A | every leave-out archived | fixed | `STRUCTURAL_RELATIONSHIPS` gains the machinery RE's own template creates for the schema and anchors to it: `ConnectToEndpoint`, `ConnectionConnectorType`, `EmbeddedConnection`, `ResourceConnection`, plus `Schema`, which is the cataloguer's own schema-to-schema-type link (`RelationalDatabaseCataloguer.java:444-448`, `SCHEMA_RELATIONSHIP`, parent at end 1) and so exists on every schema the cataloguer fills. `ActionTarget` stays structural. **`DataFlow` is NOT structural** (architect: lineage someone asserted hangs off, forces archive). `SemanticAssignment` still archives. **Live caveat for the owner:** the rehearsal saw `DataFlow` added by Egeria's OpenLineage cataloguer to every schema, so on this build a CATALOGED schema may still plan an archive until the architect rules on engine-made `DataFlow`; a template-made schema nothing has touched plans a soft delete (tested). |
| D-B | targets never seen; duplicates | fixed | `parse_catalog_targets_answer` reads the live item (`elementHeader.guid` the target element, `relatedBy.relationshipHeader.guid`, `relatedBy.relationshipProperties.catalogTargetName`); any other non-empty shape RAISES. `apply_attach` reads the list FIRST and matches by element guid OR target name (`<db>.<schema>` or RE's fallback name): a match means attached, no initiation, no `add_catalog_target`. An attach request is recorded as a proof row (`attach_requested`, with the engine action guid) at initiation, so a retry while the read-back lags checks THAT action and waits for its target instead of initiating again (a COMPLETED request older than 10 minutes with no target counts as removed). `drain_outbox` goes through the same `apply_attach`. The fake fails the test if it sees a second initiation for a schema. |
| D-C | failed outranked cataloged | fixed | `_schema_state` takes the NEWEST fact by time between the outbox row (`created_at`) and the schema's newest proof row of any state kind; a failure older than a proof does not outrank it; header counts and rows come from the same derivation. Ladder: queued, sent (`attach_requested`), attached, cataloged; US spelling on these rows ("cataloged · N tables · read back <when>"). |
| D-D | invalid parent link | fixed, **proof pending live** | The parent link is the JDBC cataloguer's own: `RelationalDatabaseCataloguer.getOrCreateSchema`, egeria `open-metadata-implementation/adapters/open-connectors/integration-connectors/jdbc-integration-connector/src/main/java/org/odpi/openmetadata/adapters/connectors/integration/jdbc/RelationalDatabaseCataloguer.java` lines 255-259 (read at origin/main): anchor = parent = the database, `DATA_SET_CONTENT_RELATIONSHIP` (DataSetContent), `setParentAtEnd1(false)`, the DATABASE at END 2. Rehearsal 2 sent it at end 1 and Egeria rejected it (OMRS-REPOSITORY-400-047). `create_schema_element` now sends end 2; a test fails if DataSetContent is sent with the database at end 1. A 500 on a create is answered by reading the qualifiedName: an element that exists is adopted (recorded as `create_error_adopted`), and a real failure's first sentence names its stage ("creating the schema element for <s> from the template failed"). **Proof pending live:** one throwaway run, database-kind target on a scratch database so the cataloguer makes a schema itself; read the relationship between that database and that schema; compare with RE's (first live item after the platform is back, before the coco_pharma gate). |
| D-E | server printed as the database | fixed, with a deviation | A stored `egeria_asset_guid` (the DATABASE's guid in every current flow) is no longer taken as the server, and a name search that lands on the database is not the server either. The architect asked for the server to be read from a relationship of the database element at commit time; none of the relationships the rehearsals recorded on the database (`ReportSubject`, `ActionTarget`, `SourcedFrom` (the template), `ResourceConnection`, `DataFlow`, `AssociatedAnnotation`) links it to its server, so it is still resolved by name, excluding the database. **A server element is shared by host:port**: a commit attaches to whatever server already carries that name, including one an earlier run left behind (rehearsal 2's first commit attached to rehearsal 1's leftover). |
| D-F | survey "done" while running | fixed | "done" only when the engine action is COMPLETED AND annotations were read back. Running (REQUESTED, APPROVED, WAITING, ACTIVATING, IN_PROGRESS) reads "running in Egeria · <status> · N annotations so far"; a missing status keeps polling; an unknown value reads "status '<X>' · not one Resource Explorer knows" and keeps polling; FAILED, CANCELLED and INVALID are failures (IGNORED is no longer treated as one). |
| D-G | wording | fixed | an archive says "archived" (a mix says "N removed, M archived"); a missing report guid reads "report not found"; the block reads "in use by a running survey · wait or cancel" only for a survey (request type `survey-*` or none stated), otherwise "in use by <request type> · wait or cancel"; ACTIVATING and a missing activity status BLOCK (the status or "no status stated" is shown); COMPLETED, FAILED, CANCELLED and INVALID do not. |
| D-H | cataloged with 0 tables | fixed | a schema reads cataloged only when tables or columns were read back; the template's own 4-element connection graph does not count. |
| D-I | "will retry" | fixed in words | a failed row reads "waiting for a worker". The registry holds no worker heartbeat row that could prove a worker is running, so it never promises a retry. |
| D5 | survey report whole | wording done | manifest line: "RE's survey report is published whole; it describes all <m> schemas; elements are created for the <n> you chose." |

**Still unverified live:** the D-D parent link at end 2 (above); that the poll window and the 600 s give-up are right for a real attach; `DataFlow` by Egeria's own engines (above); the `Schema` link on every cataloged schema (read from the source, seen once in the rehearsal as one per schema); the `requestType` property of an engine action in the related-elements element (assumed from the read-back's property list).

### Rulings after rehearsal 2 (architect, 2026-10-06)

**DataFlow, split by the OTHER END, not by who made it.** A DataFlow is machinery (it goes with the element on a soft delete) only when its far end is
Egeria's own governance-action job component: a `DeployedSoftwareComponent` whose qualifiedName starts `DeployedSoftwareComponent::GovernanceActions::`.
Evidence: the OpenLineage cataloguer names the component it makes for every OpenLineage job `DeployedSoftwareComponent::<namespace>::<name>`
(egeria `OpenLineageCataloguerIntegrationConnector.java:4665`; `openlineage-integration-connectors/docs/open-lineage-cataloguing.md:78`, jobs become
`DeployedSoftwareComponent`s and inputs/outputs are linked by `DataFlow`, same doc line 94), and the READBACK note records the database's DataFlow
going to `DeployedSoftwareComponent::GovernanceActions::PostgreSQLSurvey::survey-postgres-database`, which is that job component for Egeria's own survey.
Anything else hangs off and forces an archive, whoever asserted it: another asset, a person's process, another schema, a `GovernanceActionProcess`,
or an end that could not be read. **Not proven, so treated as hanging off:** the READBACK note's DataFlow from the process-created schema goes to the
GovernanceActionProcess `eea77a85` (an `olcatnpa` write), and rehearsal 2's template-made schemas' DataFlow (the note says "1 DataFlow", far end not
recorded). If either far end turns out to be a `GovernanceActions` job component it is machinery; until a live read shows it, a cataloged schema may still
plan an archive. The preview says which: "archive · lineage to <name> would be lost" or "delete · nothing depends on it". Tests: a connector-only DataFlow
plans delete; a DataFlow to another asset, a person's process, a governance process, another schema, or an unreadable end plans archive.

**D-E, by-name accepted with conditions.** The server is read by the EXACT qualifiedName `PostgreSQL Server::<host:port>` (the SoftwareServer RE's publish
created in rehearsal 1: `PostgreSQL Server::host.docker.internal:5442`, rehearsal 1 evidence note, "State left behind"); the lookup is a starts-with search
filtered to exact equality, never a fuzzy name match. No match reads "server not found: ..."; several read "server ambiguous · N matches ...; Resource Explorer
will not guess"; either fails the publish step with that sentence first and nothing else runs. **Why by name:** a database has no link to its server in
anything recorded because coco_pharma's database element was made by RE's own publish, not by the cataloguer, so the absence proves nothing about the
cataloguer's shape. The `scratch_cat_test6` read (cataloguer-made database to its server) decides it: if it finds a relationship, RE's publish gets a follow-up
to create the same one, and D-E then reads by relationship and falls back to the name only when no link exists; if none, by-name stays as designed here.

**Fixture note:** the catalog target list fixtures include a 3-target list, built from the single live item repeated with distinct guids (the duplicate case
rehearsal 2 hit); the test6 run will replace it with a live one.

**Backlog:** the missing worker heartbeat row is what would make "will retry" honest: today a failed row says "waiting for a worker" because nothing in the
registry proves a worker is running.

### Live read of the cataloguer's own links (2026-10-06, `READBACK-CATALOGUER-PARENT-LINK-2026-10-06.md`, branch re/readback-cataloguer-parent-link-evidence)

It AGREES with the source read behind D-D: the cataloguer-made `DeployedDatabaseSchema` is linked to the database by `DataSetContent`
`227526de-bc2c-43db-9231-ab9920b3e8f7` with the SCHEMA at END 1 and the DATABASE at END 2, and is anchored to the database (`Anchors.anchorGUID` = the
database). So RE's `parentAtEnd1: false` is the confirmed direction. The schema's only other relationship is `Schema` (type id
815b004d-73c6-4728-9dd9-536f4fe803cd; end 1 the schema, end 2 its `RelationalDBSchemaType` `<schemaQN>_schemaType`); tables link to the schema TYPE by
`AttributeForSchema` and are anchored to the database; there is no relationship between the server and the database, schema or tables, so D-E stays by name.

- **Anchor:** RE's template create passes `isOwnAnchor: false` and `anchorGUID` = the database, so the schema is anchored to the database like the
  cataloguer's (rehearsal 2 read the same on RE's schemas: `Anchors anchorGUID` = the database). Same direction, same anchor.
- **What differs, noted:** RE's schema is made from the schema template, so it also carries the template's connection graph (`ResourceConnection` to a
  VirtualConnection and its endpoints), which the cataloguer's own schema does not, and its qualifiedName is
  `PostgreSQL Relational Database Schema::<host:port>::<db>.<schema>` where the cataloguer's is `<DBQN>::<schema>`.
- **`AssetSchemaType` was wrong, and the call is gone:** `link_schema_type` (orphan schema-type adoption) sent `AssetSchemaType`, a name that is in no Egeria
  Java source and not a live type (type `Schema`, id 815b004d-73c6-4728-9dd9-536f4fe803cd, schema at end 1). Per the architect RE creates the schema element ONLY:
  `find_schema_type`, `link_schema_type` and the adoption in `apply_attach` are removed as dead code (the cataloguer makes `<schemaQN>_schemaType` and the `Schema`
  link itself when it catalogs the target; rehearsal 2 shows tables under RE's schema), and a test fails if the string `AssetSchemaType` appears anywhere in
  `resource_explorer`. **Does the template create a schema type? No:** the schema the template makes carries `CatalogTarget`, `ResourceConnection`, `SourcedFrom`,
  `ActionTarget` and `DataFlow` and no `Schema` relationship (READBACK note, process-created schema; rehearsal 2 read the same on RE's), and its 4 template
  elements are the connection graph, not a schema type. (An orphan `<dbQN>::<schema>_schemaType` is a database-kind-pass leftover and cannot clash with
  RE's `<schemaQN>_schemaType`, whose name has RE's schema qualifiedName.)
- **RE uses schema-kind targets only.** By source reading (not run live), a database-kind pass would create a SECOND schema element for the same schema, with
  the cataloguer's qualifiedName, beside RE's. A test pins that every target and every attach RE makes is a `DeployedDatabaseSchema`, never the database or the server.

### Anchors change the leave-out scan and the delete order (architect, 2026-10-06; live read of the cataloguer's links)

Tables, columns and the schema type are anchored to the DATABASE, not the schema, and the schema's own relationships are exactly two. A table is reached only by
schema `--Schema-->` schema type `--AttributeForSchema-->` table `--NestedSchemaAttribute-->` column. So:
- **"Hangs off" is found by WALKING that graph** (`_walk_schema`: schema, schema type, tables, columns, then each element's relationships, plus whatever the
  qualifiedName prefix finds, e.g. the template's connection graph), never by listing the schema's direct relationships. A term assignment two hops away plans an
  archive (tested with a fake graph where the schema's own list has no `SemanticAssignment`).
- **A CATALOGED schema (tables found) always archives**, because deleting the schema element never cascades its content; its row reads "N cataloged tables hang off
  it". A schema attached but not yet cataloged, with only the template's machinery, plans a soft delete.
- **Delete order, each element by its own GUID with its own read-back:** columns, tables, the template's connection graph, the schema type, then the schema
  (`_prove_gone` reads each back: gone for a soft delete, a Memento for an archive; the fake no longer cascades an archive and refuses a parent that still holds
  visible content). Interaction with the DataFlow rule: a machinery DataFlow (far end a `GovernanceActions` job component) is a relationship of the schema
  element and goes with it; any other lineage forces the archive, and the elements around it are archived leaf first like the rest.

### qualifiedName: NOT changed (architect asked; decision left open)

The ruling would give RE's schema the cataloguer's form `<DBQN>::<schema>` so a database-kind pass adopts it instead of making a second element. NOT applied,
because it cannot be proven safe from the source and the notes, and it conflicts with the earlier ruling that Egeria's own process adopts RE's schema by the
TEMPLATE's name. Facts: the template create takes the schema's qualifiedName from the template's placeholders (`PostgreSQL Relational Database Schema::<serverName>::
<databaseName>.<schemaName>`, rehearsal 1 and 2). The request body also has `replacementProperties` (`TemplateRequestBody.java`, overlay properties) that the
automated-curation endpoint passes straight through (`AutomatedCurationRESTServices.java:412-416` into `MetadataElementHandler.java:2374-2440`'s builder, then
`createBeanFromTemplate`), so an override is possible in principle. Not shown: the JSON class `replacementProperties` needs for a `DeployedDatabaseSchema`
(pyegeria never sent one), whether the deep-copied connection graph (`<schemaQN>::Connection`, `::Endpoint`, `::SecretsStoreConnection`, `::SecretStoreEndpoint`)
is named from the template's pattern or from the overlaid qualifiedName, and whether `allowRetrieve` (return the element that already has this name) behaves.
Against it: Egeria's own `CreateAsCatalogTarget` process creates the schema from the template's placeholders, so with the cataloguer's name a later process run
would NOT find RE's schema and would create a second one (the opposite of the adoption the earlier ruling relied on, and shown live: template-first then process
adopted by qualifiedName). The schema-kind target RE uses does not depend on the schema's name (`<schemaQN>::<table>` is built from whatever name the element
has), so the schema-kind door works either way. **Question for the architect:** which adoption matters more, a later Egeria process run (template name) or a
later database-kind pass (cataloguer name)? RE only uses schema-kind targets, and a database-kind pass would, by source reading and not live, create a SECOND
schema element for the same schema.

### D-E stays by name (live read, evidence note 899bb292)

Neither the cataloguer nor the template links a database to its server: the live read of a cataloguer-made database, schema and tables found NO relationship
between the server element (or its DatabaseManager capability) and any of them. So the exact-qualifiedName lookup (`PostgreSQL Server::<host:port>`) stays and
no relationship-based follow-up is planned.
