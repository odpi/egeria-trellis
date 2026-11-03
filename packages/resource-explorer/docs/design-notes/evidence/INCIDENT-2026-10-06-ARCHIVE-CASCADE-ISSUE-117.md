# Incident 2026-10-06: leaving out one schema archived the whole database (ISSUE-117)

Status: OPEN. No repair attempted. Restore not yet proven. For the Egeria lead.

## What happened

Commit `aa798e88` on the 8813 gate build (46aaf5db), 16:03:43Z, `coco_pharma`. The project owner
left out one schema, `us_sales`. RE sent one archive per element (`catalogue_gateway.delete_element`:
delete endpoint, `deleteMethod` ARCHIVE, `forLineage` and `forDuplicateProcessing` true,
`cascade_delete` false), columns first, then tables, connection graph, schema type, schema.

Egeria archived far more than `us_sales`:

| Element | GUID | Result |
|---|---|---|
| database `coco_pharma` | 17f0a963 | qualifiedName `…coco_pharma_archivedOn_Tue Oct 06 16:04:25 GMT 2026`; a read of it now returns 500 (proof row `read_failed`, 16:05:01) |
| schema `coco_sus` | 28bbde37 | `_archivedOn_… 16:04:23`, Memento |
| schema `coco_ods` | a358abb3 | `_archivedOn_… 16:04:23`, Memento |
| schema `us_sales` (intended) | 87271b37 | Memento |
| a `us_sales` table | 9863e995 | Memento |

The commit's leave-out step reported "0 of 1 removed … createddate was archived but the read-back
does not show it". Its survey, refresh, zone and read-back steps stayed pending (engine action
4f65388a, left alone). Nothing was repaired afterwards.

## Cause (read from Egeria source, origin/main; nothing run)

`OpenMetadataAPIGenericHandler.archiveBeanInRepository` (public entry):

1. It reads the element's anchor. For a schema or table that is the DATABASE, not the element.
2. It then calls `invalidParameterHandler.validateAnchorGUID(entityGUID, …, anchorEntity, entityGUID, …)`.
   The comment above it says "archiving is only supported on the anchor entity". But the call passes
   the element's own GUID as the expected anchor, so the test `!anchorGUID.equals(elementGUID)` is
   always false and the method never throws. The guard cannot fire. (`InvalidParameterHandler.validateAnchorGUID`)
3. The private overload gets `anchorEntity` = the real anchor (the database) and calls
   `collectArchiveTargets`, which sets `anchorToMatch = anchorEntity` and walks relationships from
   the element, collecting EVERY reachable element whose Anchors classification names that anchor.
   So archiving one schema collects everything anchored to its database: the database itself, every
   sibling schema, their tables and columns.
4. Pass 2 renames each (`_archivedOn_<date>`) and classifies it as Memento.

That matches the incident: one request on `us_sales`, and the database plus both sibling schemas
archived within two seconds (16:04:23 to 16:04:25). Why rehearsal 2 did not show this is NOT established: the likely reason is that its throwaway
database had no sibling schemas, but that is an inference, not checked.

## Source and build

Source read: `/Users/dwolfson/localGit/egeria-v6/egeria`, `origin/main` (`git describe`: V5.1-1452-gaf400039c2).
Both files are under `open-metadata-implementation/common-services/`:

- `generic-handlers/src/main/java/org/odpi/openmetadata/commonservices/generichandlers/OpenMetadataAPIGenericHandler.java`
  - line 3103, in the public `archiveBeanInRepository`: `invalidParameterHandler.validateAnchorGUID(entityGUID, entityGUIDParameterName, anchorEntity, entityGUID, entityTypeName, methodName);`
    (the entity is passed as its own expected anchor, so the guard cannot throw)
  - line 2973, `private void collectArchiveTargets(...)`: `anchorToMatch = anchorEntity == null ? entity : anchorEntity`, then recurses into every related element whose Anchors classification names `anchorToMatch`.
- `ffdc-services/src/main/java/org/odpi/openmetadata/commonservices/ffdc/InvalidParameterHandler.java`, `validateAnchorGUID`: throws only when `anchorGUID != anchorEntity.getGUID()` AND `anchorGUID != elementGUID`.

Running platform: container `quickstart-egeria-main`, image `egeria-quickstart-platform:local`,
built 2026-10-05T14:19Z, version label 6.2-SNAPSHOT, no revision label. The commit the image was built
from is NOT known, so it is NOT established that the source above is what runs. The Egeria lead can
confirm whether her pending ISSUE-117 fix covers this path. The fixes listed as in source on oak2026
(90, 112, 117, 124, 125) are not in any build.

## Findings for the Egeria lead

1. The anchor guard is vacuous (above). Either archive of a non-anchor must be refused, or
   `collectArchiveTargets` must be scoped to the element's own subtree (the code comment already says so).
2. A database element that is archived then returns 500 on read (17f0a963).
3. Re-inclusion is blocked while elements are Mementos: RE cannot re-include until Egeria restores them.

## Not done, on purpose

- No restore, un-archive or rename of any element, by any session, until the Egeria lead answers or
  the project owner says so.
- A read-only look for a Memento restore path in Egeria/pyegeria is still to be written up.

## RE response

- Every archive RE could issue on a database's tree (schema, table, column, schema type) is blocked
  in code on the Curate UX branch, and soft delete stays frozen too until a throwaway proves the
  delete path does not walk the same way. Message: "Egeria archives the whole database tree when any
  part of it is archived (ISSUE-117, archiveBeanInRepository) · choice kept, nothing sent". The owner's choices are still recorded.
- 8813 is not rebuilt until it carries that block. All merges are frozen.

## Block lifted 2026-10-23 (leave-out path)

The re-proof on a throwaway database (`REPROOF-1021-PHASE2-RESULT.md`, with its baselines
`REPROOF-1021-PHASE1-BASELINE-v1.md`/`-v1.json`/`-v2.json`/`-v3-post-upgrade.json`, `REPROOF-1021-PHASE2-PRE-v4.json`
and `REPROOF-1021-PHASE2-POST-v5.json`, all in this folder) passed on odpi/egeria c6dbf952 / image a8bcff5c: leaving
out one cataloguer-built schema archived exactly its 14 anchored elements and nothing else. Per the architect's
ruling of 2026-10-23 the block is OFF for the leave-out ARCHIVE path only; `RE_ISSUE_117_BLOCK=on` re-arms it with no
code change. Soft delete and the investigation-delete ARCHIVE keep the block until their own throwaway proof.

Fix round 1 (architect rulings, 2026-10-23) narrowed the lift:
- A schema whose state is "restored" passes no lifted scope: "archive blocked · this schema was restored after the
  2026-10-06 incident; archiving restored trees is not proven".
- The start-up line prints "platform reports <origin string> · lift proven on odpi/egeria c6dbf952 / image a8bcff5c"
  (or "platform: unreachable at start", block state unchanged). RE does not compare builds; the env re-arm
  `RE_ISSUE_117_BLOCK=on` is the only control for an older platform.
- The preview's GUID set rides in the leave-out payload; the apply walks again before anything is sent and refuses
  with "the plan changed · preview again" on any difference, or when the payload has no set.
- RE never archives or deletes an RE SurveyReport: a re-used annotation is anchored to the report that first created
  it, so archiving that report would take what later reports still link. `_send_destructive` refuses it whatever
  scope or override is set, and a leave-out whose walk holds one is refused whole.
