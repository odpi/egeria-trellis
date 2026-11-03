# DESIGN — Archive and restore in Resource Explorer (ISSUE-159 position, 2026-10-23)

For the owner's word. ISSUE-159 asked pyegeria for archive/restore wrappers;
the question was open because, after the 2026-10-06 archive cascade, RE had
no sanctioned restore path and restored coco_pharma by a hand script
(declassify Memento with forLineage on the anchor first, then children, then
strip the `_archivedOn_` suffixes). Two live tests on 2026-10-23 settle most
of it.

## 1. Facts (egeria-python-66, live on quickstart 9443, own-anchored throwaways)

- `archive_metadata_element`: same GUID, status ACTIVE, a `memento` slot in
  the element header (archiveMethod archiveMetadataElementInStore), the
  qualifiedName renamed `<qn>_archivedOn_<date>`. A plain get answers HTTP
  500 ("classified as a memento"); a forLineage read returns it.
- `restore_metadata_element` (POST metadata-expert/metadata-elements/{guid}/
  restore): same GUID, Memento removed, the ORIGINAL qualifiedName back,
  readable without forLineage. It restores soft-deleted elements too.
- `restore_related_elements` restores a removed relationship with its
  properties; `undo_metadata_element_update` and
  `undo_related_elements_update` each revert one update.
- NOT tested: anchored descendants. Whether restoring an anchor brings back
  what is anchored to it, and whether restoring a descendant needs a live
  anchor, are unknown.
- pyegeria methods: on egeria-python branch fix/role-appointment-class-names,
  unreleased. The Egeria 6 element header carries classifications in named
  slots (ISSUE-164 sample), which every read-back that looks for Memento must
  parse.
- The archive-cascade re-proof (phase 2, 2026-10-23) archived exactly the
  alpha schema's 14-element anchor set on odpi/egeria c6dbf952; the leave-out
  block is lifted for that path only.

## 2. Position

1. **No wrapper beyond the two calls.** RE's archive is `archive_element` per
   element from the plan function's list, leaf-first, each read back; RE's
   restore is `restore_metadata_element` per element from the leave-out's
   own proof rows, anchor-first, each read back without forLineage before the
   next. Egeria's own walk is never relied on for either direction until §3
   says it may be.
2. **Restore is the re-include path (S19).** Today re-include creates a new
   element and leaves the archived twin, which orphans every annotation,
   lineage link and proof row that pointed at the original. Restore keeps the
   GUID and reverts the rename, so the catalog reads as if the leave-out
   never happened. Until the methods ship, S19 stays and says so on the card:
   "re-included as a new element · the archived record <guid> remains", both
   GUIDs on the proof row, so the pairs can be migrated later.
3. **Collision guard.** If an element already holds the original
   qualifiedName (a re-include done the old way), restore is refused as
   ambiguous: "a new element already carries this name; keep the new one or
   restore the original". Never a silent second element. The read-first is
   the typed unique-name read.
4. **The 500 read.** A plain get of an archived element answers 500, not 404.
   Every RE read-back treats a 500 on a get as "possibly archived: retry with
   forLineage, then decide", never as unreadable-and-move-on; that was the
   10-06 read-back failure. Queued with the lookup follow-ups, with a
   fail-on-purpose test.
5. **Reports are never archived or deleted by RE** (reshape review finding
   9, enforced in the destructive-send guard): a re-used annotation is
   anchored to the report that first created it.
6. **Identity.** Archive and restore run as the presser; the service account
   only for an automated run with no person.

## 3. Proof before RE ships restore

A catalogr-built throwaway on the new platform after the planned reset,
the re-proof pattern: baseline by GUID; leave-out of one schema; restore
anchor-first from the proof rows; read-back equals the baseline (GUIDs,
qualifiedNames, anchors, status). Plus the two anchored-descendant
questions, answered whatever the outcome and written in the evidence:
restore the anchor only and read its descendants; restore one descendant
with its anchor still archived and read the result. If restore cascades from
the anchor, item 1's per-element loop becomes anchor-only with a read-back
of every descendant; the loop stays until then.

## 4. For the owner

- Yes or no to restore as the re-include path (item 2).
- Whether the proof runs on the first post-reset throwaway (my
  recommendation) or waits for a pyegeria release that carries the methods.
- Whether to ask Mandy the anchored-descendant question directly, which
  would save the second half of the proof.

## 5. Addendum 1 (2026-10-25): the post-reset proof answered the open questions

Owner's word 2026-10-23: "yes to restore as re-include, prove on the first
post-reset throwaway". The proof ran on `laz_local_re_reproof_postreset_1025`
(cataloger-built, two schemas, 67 elements with two column-linked
QualityAnnotations added for the purpose) on odpi/egeria 4486d1a7. Evidence:
REPROOF-POSTRESET-1025-step2-BASELINE.json and
REPROOF-POSTRESET-1025-step3-RESULT.md.

Archive half: the leave-out of `alpha` through the lifted path archived
exactly the 14 planned elements; the 53 others stayed ACTIVE; the two
AssociatedAnnotation links on the archived column survived, visible only
with forLineage. RE's own reporting of that press was a false failure ("not
sent · press Publish again", zero proof rows); ruled separately as a bug:
a proof row per accepted archive before any read-back, and after a
destructive write never "not sent" and never "press again".

Restore half, the three questions:

1. **Does restoring a descendant need its anchor live?** A descendant
   restored alone loses its Memento and regains its qualifiedName, but stays
   invisible on plain reads until its anchor is restored (the 400
   OMAG-REPOSITORY-HANDLER-400-010 names the anchor). So the order in §2 item
   1 is anchor-first by necessity, not preference, and the loop treats that
   400 as "anchor still archived", not as failure.
2. **Does restoring the anchor restore its descendants?** No. Restoring the
   schema brought back only the schema; its 12 descendants stayed archived.
   The per-element loop stays, exactly as §2 item 1 has it.
3. **Do annotation links come back?** Yes. After the full restore the two
   links are back on plain reads; the report and its annotations were ACTIVE
   throughout.

One more fact the design did not have: restore recreates no relationships.
RE's leave-out detaches the schema's CatalogTarget before archiving, and
after the restore it is gone. So **re-include = restore every element from
the leave-out's proof rows, anchor first, then re-attach what the leave-out
detached, read-first**, and the leave-out's proof rows must record every
relationship removed, not only the archives. The collision guard (§2 item 3)
is unchanged. Final state of the throwaway after the proof: 67 ACTIVE,
qualifiedNames unchanged, no Memento, CatalogTarget to be re-attached by the
next Publish.

The S19 builder brief follows from §2 as amended here; it waits for the
coordinator's slot and the owner's merge, since it is an Egeria write path.
