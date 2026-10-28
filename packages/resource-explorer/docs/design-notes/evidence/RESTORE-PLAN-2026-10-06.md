# Restore plan for the coco_pharma archive cascade, 2026-10-06 (PLAN ONLY, nothing run)

Status: prepared read-only at the project owner's request. No write to Egeria has been made. Every step
below needs the owner's word, a peer round, and the answers from the Egeria lead where marked.

## Before-state (read-only, ~18:30Z)

A name search for `_archivedOn_` with forLineage true found 148 renamed elements, all Memento; plus the
`us_sales` schema 87271b37 (Memento, NOT renamed, found by GUID). 149 in all. Full list with GUIDs:
`RESTORE-BEFORE-STATE-2026-10-06.json` in this folder.

| Group | Count | Note |
|---|---|---|
| database `coco_pharma` 17f0a963 | 1 | renamed |
| its connection graph (VirtualConnection, Endpoint, SecretsStoreConnection, SecretStoreEndpoint) | 4 | renamed |
| schemas coco_sus 28bbde37, coco_ods a358abb3 | 2 | renamed; their schema types and connection graphs are still LIVE |
| schema us_sales 87271b37 | 1 | Memento, not renamed |
| us_sales schema type, table, 11 columns | 13 | renamed several times (one suffix per archive pass) |
| us_sales schema-level connection graph (VirtualConnection, Connection, 2 Endpoints) | 4 | renamed several times |
| SurveyReports of the old database | 4 | renamed |
| ResourceMeasureAnnotations (survey results) | 120 | renamed |

Repeated `_archivedOn_` suffixes show RE's per-element archive calls each re-triggered the whole-tree
walk. Restoring means stripping ALL suffixes from each name.

New elements that now hold the original names: database def55997, its connection graph (d98b8ad5 and
siblings), schemas 4079a1d5 (coco_sus) and 1f31b602 (coco_ods).

## Constraint: names are unique

Putting an original name back fails while another element holds it. So the new elements must be renamed
aside first. A property edit of `qualifiedName` is an in-place update, not an archive or delete, so it is
not expected to cascade. (Not proven; trial first.)

## Approaches

**A. Old wins (closest to undo).** 1) Rename the new elements aside, in place, with a `_rollforward` suffix.
2) For each of the 149 old elements: declassify `Memento` (pyegeria `declassify_metadata_element(guid,
"Memento", body)` with `forLineage: true`, per the owner), then rename back by stripping every
`_archivedOn_...` suffix. Order to be decided by the trial (children first, or anchor first).
3) Re-attach a catalog target for us_sales through RE (its target was detached by RE earlier). coco_sus
and coco_ods still hold CatalogTarget relationships. 4) Retire the new tree by archiving its OWN anchor,
def55997, which walks only the new database's tree (the intended use of archive). 5) Read everything back
through RE.

**B. New wins.** Leave the old tree archived. Free the schema-level names by renaming the old, live
`coco_sus`/`coco_ods` schema types and connection graphs aside, then get a ResourceConnection onto the new
schemas. Unclear how without re-running the template create; weaker.

**Recommendation:** A, after a throwaway trial. B only if Mandy says the restore is unsafe.

## Throwaway trial (before touching coco_pharma)

1. Build a throwaway database with two schemas in Egeria by the same template route RE uses, backed by a
   scratch Postgres database (the existing scratch_cat_test databases may be gone).
2. Archive ONE schema, to reproduce the cascade on purpose. This is itself useful evidence for Mandy.
3. Try approach A on it, step by step, reading back after each: rename-aside edit, declassify with
   forLineage, rename back. Record whether any step cascades or fails (the 500 on reading the database is
   the case to watch).
4. Only if the trial restores a working, readable, cataloguable tree, plan the real run.

## Questions that decide the order (for the Egeria lead)

1. Is declassifying Memento plus renaming back safe, or does Egeria need an internal restore?
2. Does declassify cascade the way archive does (does it walk the anchor tree)?
3. Does the archived database's 500 on read go away once it is declassified?
4. Is there a restore for soft-deleted items yet (the owner says it is on the list)? Not needed for this
   incident, all elements here are archived.

## Not decided

Whether to restore at all or to roll forward. The project owner decides, with the Egeria lead's answers.

## Added after the project owner's message (2026-10-06)

- **Possible regression.** The project owner says some of this is a regression in Egeria's behaviour that the
  Egeria lead will look at tomorrow. Consistent with the source: a comment above the anchor check says
  archiving is supported only on the anchor entity, yet the check cannot fire. Whether it ever worked, and
  since which build, is for her to say. Rehearsal 2's clean archive is a data point (cause not established).
  If it is a regression, the restore plan may be superseded by a fixed build, so nothing here should run
  before her answer.
- **Nested anchors for cataloguing (each layer an anchor).** Under consideration; pros and cons as seen from
  RE's side, not decided:
  - Pro: an archive or delete of a schema, table or column would walk only its own subtree, which removes
    the cross-schema cascade that caused this incident.
  - Pro: a schema could be retired on its own without hiding the database.
  - Con: RE's live-proven shapes (all elements anchored to the DATABASE) and its leave-out order, proofs and
    read-back all assume that; each would need rework and re-proof.
  - Con: existing elements are anchored to the database already; changing anchors probably means
    re-creating them, which is the same name-collision problem as this restore.
  - Con: anchors also decide visibility and delete cascade for everything under them, so a table anchored
    on its own schema changes what a database-level delete reaches. Behaviour of the template create and
    the JDBC cataloguer with nested anchors is not known; they would need a throwaway trial.

## Review additions (architect, 2026-10-06; items marked CHECK were not verified by me)

**Approach A needs these steps added:**
1. Detach the NEW schemas' catalog targets (by relationship GUID, read first) before retiring the new tree.
   Otherwise the connector keeps two targets per name, one on an archived element, and the restored
   coco_sus/coco_ods targets compete with them. The daemon already shows this state for scratch_cat_test6.
2. Move RE's registry with Egeria. The database record's Egeria element pointer (CHECK the exact field
   name, the architect calls it egeria_asset_guid) and the roll-forward commit's proof rows point at
   def55997. After a restore, write a proof row "restored · 17f0a963 · from def55997 · <when> · by <who>" and
   set the pointer back; otherwise every status on the page derives from rows naming the wrong element. If
   B is chosen, write "rolled forward · def55997 · previous 17f0a963 archived".
3. Rename-aside must cover every new element that took an old element's name: the two schemas, the
   database and its four connection elements, and any new SurveyReport or annotation sharing a deterministic
   name with an old one. 56 of 76 annotations suggests about twenty collided; read before the trial.
4. The old coco_sus schema type and schema-level connection are LIVE with original names and relationships
   intact (read 17:5xZ); the old coco_ods schema type is live too (its schema-level connection was NOT read:
   CHECK). After declassify and rename-back they should rejoin without work. This is why A restores a
   cataloguable tree and B does not.
5. Order: anchor first (the database), then children, because a read of a child can return 500 while its
   anchor is Memento (the symptom seen). The trial confirms.

**Trial additions.** After the deliberate cascade, read the database by GUID and expect the 500; declassify
the database alone and read again before touching any child. That answers question 3 with one call. The
trial database needs two schemas and a table with a term assignment, so the cascade and the surviving
relationships both reproduce.

**Why A is consistent with roll-forward.** The archive was not an intended state change but a defective
write; Egeria keeps the versions either way; the correction is recorded as a forward event (the
"restored" proof row). If the Egeria lead's fix lands first, B becomes a re-run on the fixed build rather than
a plan.

**Nested anchors, what the first cons missed.**
- The anchor is Egeria's choice (template and cataloguer), not RE's: RE can only follow. The real question
  for the Egeria lead is whether her cataloguer and template will change.
- A transition leaves mixed trees (old elements anchored to the database, new ones to the schema), so RE's
  leave-out preview must read each element's Anchors classification and not assume the shape. That is right
  regardless.
- Visibility and zones are evaluated through the anchor, so nesting changes which element's zone governs a table.
- Pro: schema-kind targets already make the schema the unit of cataloguing; nested anchors would make it
  the unit of archive too.
- The anchor check that cannot fire is a bug whatever the anchoring. Nested anchors shrink the blast radius
  without fixing it. Keep the fix and the redesign apart.

## Throwaway trial RESULTS (2026-10-06, 18:42Z to 18:45Z; run by the coordinator, owner's "proceed", peers all clear)

Tree `rftrial_202610061842_*`: one database (its own anchor) with two schemas, a schema type, a table, a column,
all anchored to the database as RE's are, plus a SemanticAssignment from the table to an existing term. Every
call went through pyegeria MetadataExpert directly (never RE's gateway); the ISSUE-117 block was neither
bypassed nor cleared. Full ordered call log with requests and Egeria's responses:
`RFTRIAL-CALLLOG-2026-10-06.jsonl` in this folder (includes the dry runs, marked `DRY`).

1. **Cascade reproduced exactly.** One ARCHIVE request (`forLineage` and `forDuplicateProcessing` true,
   cascade false) on ONE schema archived the database, the other schema, the schema type, the table and the
   column, all within the same second. As on coco_pharma, the schema that was archived was NOT renamed; the rest
   were. So the incident is reproducible on a clean throwaway tree with two schemas.
2. **The 500 reproduced.** A plain read (no forLineage) of the archived database returned SERVER_ERROR_500.
3. **Declassify the database alone works.** Removing `Memento` from the database with `forLineage: true` removed
   the classification, and the plain read answered OK (500 gone). The children stayed Memento, so declassify did
   not cascade.
4. **Restore per element works.** Declassify (forLineage true) then `update_metadata_element_properties` with the
   suffix stripped, one element at a time, in the order schemas, schema type, table, column. After each step a
   read-back showed Memento gone and the original name back. The database's own name needs its own rename
   (declassify does not rename it); my first script run missed that and was fixed.
5. **Plain read-back of the whole restored tree:** all six elements visible with original names, Anchors intact,
   and the SemanticAssignment on the table survived.
6. **Archiving the trial's OWN anchor stayed inside the trial tree.** The eight coco_pharma GUIDs were read before
   and after and did not change. The root (the database) was not renamed, the rest were: the known
   "top-level archived element keeps its name" behaviour.

**What the trial did NOT show:** the name-collision step (renaming the new elements aside) was not tried, because
the throwaway had no competing new elements; the ~149-element scale; whether a restored tree is cataloguable,
because the throwaway's elements were hand-built and not made by the template or the cataloguer, and no Postgres
was attached; pyegeria's `lineage_visible` was not available in RE's installed pyegeria, so a no-op shim was used
and every body carried forLineage true instead; the hand-built type and relationship names were partly guessed
(the first build stopped at a wrong relationship name, then resumed).

**Left on the platform:** the trial tree is archived and renamed, at the bottom of this list. Hard removal is a
separate decision: database c9842fd3, schemas f3c86a1a and 6645f226, schema type 1c6ac3a4, table 3d3fe8ec,
column 117f75f6 (all names start `rftrial_202610061842`), plus one SemanticAssignment 9a03366a to an existing term.

## Dry-run plan for the real restore (2026-10-06 ~18:50Z; read-only; no write, no `--go`)

The full per-element plan is `RESTORE-DRYRUN-PLAN-2026-10-06.txt` in this folder. The script itself is not committed.
It refuses `--go` unless env `RESTORE_CLEARED=<who>/<UTC>` is set, and its guard allows only the 149 old GUIDs plus the
7 new elements it found; the guard test refused 8 of 8 wrong cases.

Counts, from reads: all 149 old elements are Memento and read with forLineage; 148 carry `_archivedOn_` suffixes
(1 suffix on 131 elements, 3 on 4, 12 on 13), only us_sales 87271b37 has none; no two stripped old names collide.
A plain read of the old database returns the 500. Writes per step: detach new targets 2; rename-aside 7 (the new
database, its 4 connection elements, the 2 new schemas); restore-old 297 (149 declassify + 148 renames); verify 0;
retire new tree 1; registry proof 2 registry writes; cataloguer 1 refresh trigger.

**Corrections to earlier statements:**
- No annotation name collisions exist. The new database has 56 annotations and 1 SurveyReport; none shares a name with
  an old element (the per-measurement GUID in an annotation's name differs; checked by exact-name search of all 149
  stripped names and by traversal). My earlier "about twenty collided" idea for 56 versus 76 is WRONG; 56 versus 76
  is still unexplained.
- `get_catalog_targets` lists only the 2 new-schema targets. The old schemas' CatalogTarget relationships (one each, to
  the JDBC connector 70dcd0b7) show only in a read of the element's relationships, probably hidden while Memento. So my
  "target list is empty" reads were incomplete in a second way. Verification must read the relationship.
- Old coco_sus still has 30 live tables under its schema type (so the cataloguer HAD built tables there before).

**Registry side:** the database record's pointer is `databases.egeria_asset_guid`, set by
`set_database_egeria_guid(slug, guid)` (the roll-forward commit set it, `catalogue_commit.py:1341`). A "restored" proof
row is free text in `catalogue_commit_proofs.proof` with the sentence in `detail_json['text']`; per-schema state words
derive only from the STATE_PROOFS set, so a new `P_RESTORED` constant (a code change, not a schema change) is needed
if the page should show it. Not done. The slug `coco_pharma` is assumed; the shared registry was not read.

**Not run:** every Egeria write, every `--go` path, the registry write, the cataloguer refresh.

## REAL RESTORE RESULTS (2026-10-06 20:50Z to 22:45Z; owner's go with clearance dan/20261006T2100Z; all four peers clear)

Ordered call log with requests and responses: `REALRESTORE-CALLLOG-2026-10-06.jsonl` (plus the earlier dry-run lines, marked DRY).

1. **Detach** the two new schemas' catalog targets (5289479c, e261ff9d): read-back 0 left.
2. **Rename aside** 7 new elements to `..._rolledforward_202610062050` (database def55997, its 4 connection elements,
   schemas 4079a1d5 and 1f31b602); each read back.
3. **Restore old:** the security classifier blocked the agent's own run of this step; the owner ran the same script in his
   own terminal. 149 declassify + 148 renames, call log shows 0 failures. Independent plain read of all 149: 149/149 visible,
   149/149 original names, 0 Memento, Anchors on 148/148 non-root, the SemanticAssignment on us_sales_forecast intact,
   one CatalogTarget relationship each on coco_sus and coco_ods, ownership on the database intact. Exactly ONE live
   element holds each original name (database 17f0a963; schemas 28bbde37, a358abb3, 87271b37).
4. **Registry:** slug is `localhost_docker_coco_pharma` (the script's default `coco_pharma` was wrong and would have written
   nothing useful; caught by reading the record). Proof row id 48 `restored` (database 17f0a963, from def55997) and
   `set_database_egeria_guid` back to 17f0a963; read back by a fresh read. The first --go would also have written to a
   throwaway SQLite had `run.sh` been used; a separate runner left REGISTRY_DATABASE_URL unset for this step.
5. **Retire the new tree:** ARCHIVE def55997 (its own anchor). Its four connection elements archived; the database kept its
   rolled-forward name (root not renamed, the known behaviour); 0 of the 149 old elements changed. The two new schemas
   (4079a1d5, 1f31b602) were NOT archived and stay live under `_rolledforward_` names.
6. **Cataloguer:** one forced JDBCDatabaseCataloguer refresh completed (22:44:20Z). The daemon log shows tables and columns
   being transferred for coco_ods ("Transfer complete for table ...coco_ods::sites"); 30 tables under the restored
   coco_sus after the refresh. No NullPointerException in the window read.

**Not done / still open:** us_sales is restored as an element but has no catalog target (RE detached it); re-attach through
RE's own commit. The two live `_rolledforward_` schemas and the archived new tree remain as leftovers, plus the trial tree.
The restore confirms the trial's method on the real tree. The Egeria lead's questions (regression, 500 cause, restore API)
remain open. 56 versus 76 annotations is moot: the old 120 annotations are restored.

## Why the retire did not take the two new schemas (read-only, ~22:50Z)

The architect asked whether the leftover schemas 4079a1d5 and 1f31b602 are anchored to def55997.
- Their Anchors classification names **def55997** (`anchorTypeName RelationalDatabase`, `anchorGUID def55997-...`), the same as the new connection d98b8ad5, which the archive DID take. So the anchoring is as RE's other elements: it is NOT the case that the adopt path anchored them elsewhere. def55997 itself is its own anchor (no anchorGUID).
- What differs is the relationships. The archive walk follows relationships from the element and collects those whose Anchors name the anchor. At 17:35Z (read earlier today) each new schema had only 3 relationships (DataFlow, ActionTarget, CatalogTarget) and NO `DataSetContent` link to the database, unlike the restored old schemas, which have DataSetContent to the database. A schema anchored to def55997 but not linked to it by any relationship cannot be reached by a relationship walk from def55997, which fits the schemas surviving the archive.
- So the walk is consistent with how it is written (it walks relationships), and the RE-side question is why the press created the new schemas anchored to def55997 but without the DataSetContent link to it (the architect's "created by the adopt-by-name path" candidate). Not diagnosed: the press's publish and schema-create calls were not read. Log as an RE defect candidate; the schemas also lack the ResourceConnection that stopped the cataloguer earlier (same family: the template create did not link what it should).

## The press's own proof rows settle part of the adoption question (read-only, registry rows 36-47)

PR/CI read the old 8813 log for the press (11:58:50 to 11:59:14 CDT): no schema-create, adopt or schema-name line at any level;
the only non-poll line is the SurveyReport 409 at 11:59:13. The proof rows say more than the log:
- **Rows 39 and 41** (`target_attached`, coco_sus 4079a1d5 and coco_ods 1f31b602) carry `create_error_adopted`: "creating the schema
  element for coco_sus from the template failed: SERVER_ERROR_500 ... open-metadata/automated-curation/catalog-templates/new-element".
  So the template create DID fail with a 500, RE adopted the element that existed anyway, and wrote `target_attached`. The text
  stored is truncated before Egeria's own sentence, so the cause of the 500 (a name collision on the old, still-live schema-level
  connection graph is the architect's candidate) is NOT shown. The page did not surface the adopted-after-error fact.
- **Rows 45 and 46** (`elements_read_back`) claim, for the NEW schemas, 23 tables / 171 columns / 198 elements (coco_ods) and a
  table list for coco_sus. A direct read of the same new schemas (17:35Z) found NO tables and 3 relationships. Likeliest
  explanation, NOT verified: the read-back resolves by qualifiedName and found the OLD schema type (live, original name),
  so it proved the old tree's tables for the new element.
- **Row 42** (`report_published`, 76 annotations, surveyed 2026-10-03) was written although the log shows the report publish failed
  with a 409 at 11:59:13. So a proof row said "published" for a publish that failed.
- Net: three proof rows read as success for a press whose schemas were half-built, the report not published, and the tables
  not present. This is the "status words derive from proof rows that prove it" rule failing at the proof-writing side.

## Curate backlog rules accepted by the architect (for the docs batch; not built)

1. A proof row proves exactly one element, by GUID, and is written only after a read of that GUID returns what the row claims:
   `target_attached` after reading the target relationship on that element; `elements_read_back` by the created schema's GUID
   and its own Schema link, never by name; `report_published` only with the report's GUID from a successful create or a verified reuse.
2. A proof row stores Egeria's full response sentence, never truncated. Any adoption after an error is its own row kind
   (`create_error_adopted`) and the page shows it in the row's first sentence: "adopted after a create error · <Egeria's sentence>",
   never "attached".
3. After a create error, reuse of an existing element requires its DataSetContent link to the database and its ResourceConnection;
   otherwise the row fails with "schema created without its connection · <Egeria's sentence>".
4. Test: a fake whose create fails and whose name-read returns the old tree must yield zero success rows.
5. Also logged: log every archive/delete call RE sends (GUID, type, flags, response) with a proof row per call; one resolver for
   the survey step and the publish step that reads the proof row's GUID first and the name second ("created · previous element
   archived" row when a rename forces a new element); an outbox drain must never retry an archive or delete row, only report it
   (the 16:15 retry wrote only "already archived when read"; all 299 suffix timestamps are 16:03 or 16:04, so the retry added none).

## Conclusion line

Three success rows on a half-built press (`target_attached` after an adopted create error, `elements_read_back` from the old tree,
`report_published` after a 409) are why the roll-forward looked better than it was. The rows were the status words' source, and the
rows were wrong.
