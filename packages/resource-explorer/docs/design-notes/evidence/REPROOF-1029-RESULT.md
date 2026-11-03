# Leave-out re-proof, 2026-10-29 — PASS on odpi/egeria abe31272 (upstream image 3f396977; local build 896d2b94)

Owner's word: "lets do it now", then "go ahead without waiting for the builders". RE 8813 on 9d40eab4. The block stayed ON (dwolfson/2026-10-28T14:39:08Z) and was opened for ONE slug by RE_ISSUE_117_BLOCK_OFF=dwolfson/2026-10-29T13:03:54Z/laz_local_re_reproof_postreset_1025, expiring 14:03:54Z.
Start line: "archive-cascade block: ON (dwolfson/2026-10-28T14:39:08Z) · OFF for laz_local_re_reproof_postreset_1025 until 2026-10-29T14:03:54Z".

1. Baseline (REPROOF-1029-pre-baseline.json, resourceexplorernpa): 67/67 ACTIVE; qualifiedNames identical to the 10-27 baseline.
2. PUT alpha=leave_out. Preview: "Publish · 1 schema · archives 1", attach beta, plan 14 (6 RelationalColumn · 2 RelationalTable · 1 VirtualConnection · 2 Endpoint · 1 Connection · 1 RelationalDBSchemaType · 1 DeployedDatabaseSchema); hangs_off "2 AssociatedAnnotation · 2 ForeignKey · 2 published tables". The plan GUIDs equal the 10-27 plan (REPROOF-1029-plan14.txt).
3. Commit d376fd0c710d44eaac818e1da2d74f8e, pressed ONCE from the signed-in pane (peterprofile) at 13:06:53Z, DONE 13:08:07Z. leave_outs: "1 of 1 archived in Egeria, each with its proof row". read_back: "published by RE 2 ✓ · attached 1 schema ✓ · cataloged by Egeria 1 schema ✓".
4. Proof rows (REPROOF-1029-proof-rows-d376fd0c.json):
   - element_archived 28 = 14 read_back:pending + 14 confirmed. Both sets equal the plan, every pending id is below every confirmed id, and planned is 14 on all 28.
   - 15 delete_call, all in the plan.
   - 1 target_detached ("target gone from the cataloguer's list on read-back").
   - archived: {elements_archived 14, tables 2, columns 6, hangs_off AA 2 / FK 2}.
5. Independent read (REPROOF-1029-after-leaveout.json): all 14 plan elements archived (_archivedOn_ under forLineage); 53/53 others ACTIVE, 0 archived. A plain read of an archived element gives SERVER_ERROR_500, as on 10-26 and 10-27.
6. Restore (read-first, restore_metadata_element):
   - Schema 87b8fb6b alone: ACTIVE under its original name, with all 13 descendants still archived.
   - Then the rest: 10 restored, 3 already live (fe413588, f646f352, 25b64af4). The trio depends on restore order, as on 10-27.
   - Final (REPROOF-1029-after-restore.json): 67/67 ACTIVE, names equal to the baseline.
7. Re-attach: alpha=catalogue, preview "Publish · 2 schemas" (no re-include). Commit f838003c pressed ONCE, DONE 13:11:05Z: "1 attached · 1 already attached", read_back "published by RE 2 ✓ · attached 2 schemas ✓ · cataloged by Egeria 2 schemas ✓".
Side effects: surveys submitted, engine actions d82a10ec [beta] and 43151002 [alpha, beta].
After the re-proof the override was removed from the server's start script; it expires by itself at 14:03:54Z in any case.
