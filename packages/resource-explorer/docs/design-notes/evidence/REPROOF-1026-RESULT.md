# Archive-cascade re-proof on the new platform build (2026-10-26) — PASS

Throwaway `laz_local_re_reproof_postreset_1025` (database 78fa910b, server d498dcda, schemas alpha 87b8fb6b and beta).
Platform: odpi/egeria-platform@sha256:4994af36 (6.3-SNAPSHOT), up 2026-10-26T21:24:52Z. The image carries no
revision label. It was pushed by odpi/egeria's "Merge v6" run 40369610567 on **8de9572c** (#9386): its "Build and
push" step ran 17:45:55-17:46:48Z and the image was created at 17:46:18Z. That run's later Maven Central step
failed; the image push succeeded. The lift was first proven on odpi/egeria c6dbf952 / image a8bcff5c
(`REPROOF-1021-PHASE2-RESULT.md`); this run repeats that proof on the new build.

## Words and clearances
- Owner: "yes" to the leave-out re-proof on the new platform; signed in to the pane as peterprofile; the commit
  was pressed from the owner's pane. Afterwards, "yes on lifting" (record this build as proven).
- Peers: egeria-python-85, egeria-workspaces-fs-50, trellis-2d CLEAR.
- RE: 8813 on trellis main 7a581788, pyegeria 6.2.10, block OFF for the leave-out scope (no slug override).

## Before (`REPROOF-1026-pre-baseline.json`, `REPROOF-1026-pre-extra4.json`, read-only)
- All 67 elements (63 baseline + 4 connection-graph) ACTIVE with their live names.

## Run
- PUT /node alpha `leave_out`. Preview: "Publish · 1 schema · archives 1", plan 14 (the same 14 GUIDs as the
  plan), hangs_off "2 AssociatedAnnotation · 2 ForeignKey · 2 published tables".
- POST /commit once, 2026-10-26T21:45:52Z, commit d5c43d52: DONE, leave_outs "1 of 1 archived in Egeria, each
  with its proof row".
- Proof rows (`REPROOF-1026-proof-rows-d5c43d52.json`): element_archived 28 = 14 pending + 14 confirmed, every
  pending row written before every confirmed row; 14 distinct, 14 planned; 15 delete_call; 1 target_detached.

## After (`REPROOF-1026-after-leaveout.json`, read-only, independent of RE)
- 14/14 plan elements archived: the plain read fails, and the forLineage read shows an `_archivedOn_` name.
- 53/53 other elements ACTIVE.

## Restore (`REPROOF-1026-restore-schema.json`, `-after-schema-restore-13.json`, `-restore-rest.json`, `-after-restore.json`)
- Read-first `restore_metadata_element` as resourceexplorernpa (restores only an element whose plain read fails
  and whose lineage name is `_archivedOn_`).
- Schema 87b8fb6b alone first: ACTIVE with its original name; all 13 descendants still archived. Restoring the
  anchor does not restore what hangs off it.
- Then the rest: 10 restored, 3 already live (Endpoint, SecretsStoreConnection, SecretStoreEndpoint, which come
  back with the Connection restore, as in RECHECK-865).
- Final read: 67/67 ACTIVE with their original qualifiedNames.
- Re-attach: alpha `catalogue`, commit 3621856a pressed once: DONE, "attached 2 schemas ✓ · cataloged by Egeria
  2 schemas ✓". Side effect: each commit submitted an Egeria survey (engine actions 99012f54, b54887f9).

## Result
The leave-out archive behaves on 8de9572c / 4994af36 as it did on c6dbf952 / a8bcff5c: exactly the anchored set is
archived, nothing else is touched, every archive has its pending and confirmed proof rows, and the restore
brings every element back. `catalogue_gateway.LIFT_EVIDENCE` and `PROVEN_BUILD` now name this build.
