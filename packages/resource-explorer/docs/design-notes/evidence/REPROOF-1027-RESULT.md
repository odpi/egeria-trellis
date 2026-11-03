# Archive-cascade re-proof on the platform build of 2026-10-27 — PASS

Throwaway `laz_local_re_reproof_postreset_1025` (database 78fa910b, server d498dcda, schemas alpha 87b8fb6b and
beta), the same database as `REPROOF-1026-RESULT.md`.

**Platform:** odpi/egeria-platform@sha256:**c2ca054c** (6.3-SNAPSHOT), redeployed by the owner on 2026-10-27 with
the data kept, then carried through an OS-update reboot. The image has no revision label. It was pushed by
odpi/egeria's "Merge v6" run 40515787924 on **12b1aaae** (#9389, the boxed-properties fix for ISSUE-166): its
"Build and push" step ran 14:06:57–14:07:33Z, and the image was created at 14:07:09Z. That run's later Maven Central
step failed; the image push succeeded.

**Earlier proofs:** odpi/egeria 8de9572c / image 4994af36 (`REPROOF-1026-RESULT.md`), and first on c6dbf952 /
a8bcff5c (`REPROOF-1021-PHASE2-RESULT.md`).

## Words and clearances
- **Owner:** "signed in - run the re-proof". The commits were pressed from the owner's pane (peterprofile). After
  the run, "yes" to recording this build as proven.
- **Peers:** egeria-python-8b CLEAR, the only local session; it was cutting a pyegeria release and writes nothing to Egeria.
- **RE:** 8813 on trellis main f6bff48f, pyegeria 6.2.10, block OFF for the leave-out scope (no slug override).

## Before (`REPROOF-1027-pre-baseline.json`, read-only)
- All 67 elements read normally: ACTIVE, none with an `_archivedOn_` name, every qualifiedName unchanged since the
  2026-10-26 restore.

## Run
- **Choice:** PUT /node alpha `leave_out`.
- **Preview:** identical to 2026-10-26.
  - "Publish · 1 schema · archives 1".
  - hangs_off "2 AssociatedAnnotation · 2 ForeignKey · 2 published tables".
  - plan 14: 6 RelationalColumn, 2 RelationalTable, 1 VirtualConnection, 2 Endpoint, 1 Connection, 1
    RelationalDBSchemaType, 1 DeployedDatabaseSchema.
  - The 14 plan GUIDs are exactly the set archived on 2026-10-26.
- **Press:** POST /commit once, 2026-10-27T15:36:48Z, commit 41ff9434. DONE, leave_outs "1 of 1 archived in
  Egeria, each with its proof row".
- **Proof rows** (`REPROOF-1027-proof-rows-41ff9434.json`):
  - element_archived: 28 = 14 with read_back pending + 14 confirmed. Each set equals the plan, every pending row
    was written before every confirmed row, and planned = 14.
  - Also: 15 delete_call, 1 target_detached.

## After (`REPROOF-1027-after-leaveout.json`, read-only, independent of RE)
- 14/14 plan elements archived: the plain read fails (Egeria answers 500, as on 2026-10-26), and the forLineage
  read shows an `_archivedOn_` name.
- 53/53 other elements ACTIVE; none archived.

## Restore
Evidence: `REPROOF-1027-restore-schema.json`, `-after-schema-restore-13.json`, `-restore-rest.json`, `-after-restore.json`.
- **Method:** read-first `restore_metadata_element` as resourceexplorernpa. It restores only an element whose
  plain read fails and whose lineage name is `_archivedOn_`.
- **Schema 87b8fb6b alone first:** ACTIVE with its original name, and all 13 descendants still archived. Restoring
  the anchor does not restore what hangs off it.
- **Then the rest:** 10 restored and 3 already live (6920bac4 Connection, f646f352 SecretsStoreConnection,
  fe413588 Endpoint). The trio differs from 2026-10-26 only because of the restore order: the GUIDs went in sorted
  order today and in plan order then, and restoring one member of the connection graph brings its linked
  connections and endpoints back with it.
- **Final read:** 67/67 ACTIVE with their baseline qualifiedNames.
- **Re-attach:** alpha `catalogue`. Preview "Publish · 2 schemas", no re-include. Commit 39fc5896 pressed once:
  DONE, "published by RE 2 ✓ · attached 2 schemas ✓ · cataloged by Egeria 2 schemas ✓".
- **Side effect:** each commit submitted an Egeria survey (engine actions 79041873, 3217a5b8).

## Result
The leave-out archive behaves on 12b1aaae / c2ca054c as on 8de9572c / 4994af36 and c6dbf952 / a8bcff5c:
- exactly the anchored set is archived, and nothing else is touched;
- every archive has its pending and confirmed proof rows;
- the restore brings every element back.

`catalogue_gateway.LIFT_EVIDENCE` and `PROVEN_BUILD` now name this build.
