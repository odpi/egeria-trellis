# Archive-cascade re-proof, phase 2: leave out alpha (2026-10-23) — PASS

Throwaway `laz_local_re_cascade_reproof_1021` (database c79be6db-c959-48a2-b96f-c71249f47e1e, schemas alpha 4ffa5fec, beta 2f9a40a0).
Tree built on odpi/egeria-platform@sha256:621ca8e0 (2026-10-21/22); archive run on odpi/egeria-platform@sha256:a8bcff5c (6.3-SNAPSHOT, odpi/egeria c6dbf952 #9378; local a5f941a1; started 2026-10-22T15:59:10Z). Owner chose to proceed across the build change ("Proceed on it"); v3 and v4 show the tree unchanged across the upgrade.

## Words and clearances
- Owner: "We have a long session now - so you can proceed"; "Proceed on it (Recommended)"; "You press it from my window".
- Peers: egeria-workspaces-fs-41 CLEAR (platform idle, duplicate-QN scan off); Architect CLEAR with evidence asks; trellis-51 informed.
- The override restart was run by the owner himself (the coordinator's attempt to write an override start script was refused by the auto-mode classifier).

## Before
- v4 read (`REPROOF-1021-PHASE2-PRE-v4.json`, ~01:30Z, read-only): 55 elements, all ACTIVE, no `_archivedOn_` name; none added, gone or renamed since v3; edges 107 = v3 + the CapabilityAssetUse heal edge (DatabaseManager f024e7cd -> database).
- Expected archive set = alpha + every element anchored to alpha = 14: DeployedDatabaseSchema 1, RelationalDBSchemaType 1, RelationalTable 2, RelationalColumn 6, VirtualConnection 1, Connection 1, Endpoint 2.
- Code checks: override format and 60-minute expiry `catalogue_gateway.py:74-140`; destructive outbox kinds (incl. `catalogue_schema_leave_out`) are terminal on first failure, never retried, `egeria_outbox.py:243-262`.

## Run
- 8813 start-up line: `archive-cascade block: OFF for laz_local_re_cascade_reproof_1021 until 2026-10-23T02:42:36Z` (pid 60549, main 24da909b).
- Choice: PUT /node alpha leave_out (owner's pane, peterprofile). Preview: can_commit, "Publish · 1 schema · archives 1", alpha form=archive, hangs_off "2 ForeignKey · 2 published tables", checked 14; beta attach.
- Press: POST /commit once, 2026-10-23T01:45:33Z, author peterprofile, curation d5dbd07c472b4e73bc9e9990c5b5e85e, run e003d217 succeeded 01:50:20Z.
- Egeria writes (8813 log): remove_catalog_target a7dd8781 ok (01:45:54Z); 14 ARCHIVE calls, leaf first, all `outcome=ok`, 01:46:03-01:46:06Z, flags deleteMethod=ARCHIVE forLineage forDuplicateProcessing cascade_delete=False. The archived GUID set EQUALS the expected set (14/14, none extra, none missing).
- Curation steps: leave_outs "1 of 1 archived in Egeria, each with its proof row"; survey_report reused 1b09c959; survey submitted for beta only (engine action 3f04855d, APPROVED); read_back "1 published · 0 attached, waiting" (old wording; chips branch replaces it).
- written by resourceexplorernpa (synchronous path, identity follow-up pending)
- createdBy/updatedBy on the archive: `resourceexplorernpa` (the synchronous path still writes as the service account; identity follow-up re/identity-sync-paths in progress). Identity does not change the anchor walk.

## After (`REPROOF-1021-PHASE2-POST-v5.json`, read-only, ~01:51Z)
- All 14 expected elements: live read refused ("classified as a memento", classifications `[Anchors, Memento]`); lineage read shows each renamed `<qualifiedName>_archivedOn_Fri Oct 23 01:46:0x GMT 2026`.
- The other 41 v4 elements: ACTIVE, qualifiedName unchanged. None archived.
- beta: CatalogTarget relationships 1 (still attached). Database c79be6db: ACTIVE, readable.
- 8813 restarted with the block ON at 01:51:52Z (pid 64083; no override in its environment; 0 errors; 4 leader loops).

## Verdict
PASS against the architect's criteria: exactly the 14 alpha-anchored elements Memento; every other v4 element ACTIVE with its qualifiedName unchanged; beta still a catalog target; database read OK. On a8bcff5c, RE's leaf-first ARCHIVE of a non-anchor schema does NOT cascade to the anchor (the ISSUE-117 behaviour of 2026-10-06 is not reproduced).

## Side finding
`leave_outs` spent ~4 min (01:46:06-01:50:19Z) in `_prove_gone` -> `archived_siblings` -> `find_archived_siblings` -> `find_metadata_elements_with_string(startsWith)`: an untyped string search on the grown repository, not in the fast-lookup site table. Correct but slow; a candidate for the lookup follow-ups.

## Scope of the verdict
Proven on odpi/egeria c6dbf952 / image a8bcff5c only. RE does not compare the platform's build with this one: its
start-up line prints the platform's origin string beside it, and the env re-arm `RE_ISSUE_117_BLOCK=on` is the only
control for an older platform.

## Next (not done)
- The lift PR with this evidence under docs/design-notes/evidence/ (architect + owner).
- Investigation-delete live proof, separately, after the lift.
