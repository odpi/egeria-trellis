# Morning note for the owner: the Curate catalog commit gate on coco_pharma (2026-10-06)

Written by the coordinator overnight. Everything below is **read from evidence notes, merged code notes and live reads of
2026-10-05/06**; nothing here has been run on the fixed build against a real database yet. Where a word is quoted, its source
is named. The gate on coco_pharma is the first live use of the fixed commit path; that is why it is yours to walk.

## The build you are testing

| | |
|---|---|
| Serving | **8813** (the gate build), not 8810 |
| main HEAD | `46aaf5dbf54ceff5feca5846630769c8111009e1` (merge of PR #501) |
| Worktree | `/Users/dwolfson/localGit/egeria-v6/trellis/.claude/worktrees/wt-relgraph`, detached at that HEAD, clean (0 status entries) |
| Process | pid 82489, started Tue Oct 6 07:08:01 2026, cwd = that worktree; `GET /` returns 200 (coordinator re-checked) |
| Environment | the main checkout's `.env` loaded (key names only on record: EGERIA_*, PREFECT_*, TRELLIS_*, FEEDBACK_ADMIN_TOKEN, GITHUB_TOKEN, PORT) |
| Migrations since 8810's code | none in the last four merges (the one new table, `catalogue_commit_proofs`, exists since 2026-10-05) |
| 8810 | you restarted it after last night's platform refresh; it runs the code from BEFORE #501 (the old commit path) and is NOT the gate build; do not walk the gate there |

What changed since the second rehearsal (merged in order): #498 templates sync (`725cf1ff`), #499 parent-link evidence
(`da6643a1`), #500 the live Egeria tiers are opt-in (`1b433d2e`), **#501 the rehearsal-2 fixes (`46aaf5db`)**.

## What was wrong in rehearsal 2, and what #501 changed

Rehearsal 2 (a throwaway database, `evidence/REHEARSAL-2-CURATE-COMMIT-DATABASES-2026-10-05.md`) failed gate items 1 and 2:

- every leave-out read ARCHIVE; soft delete was unreachable;
- Resource Explorer could not read its own catalog targets, so every press attached again (9 targets for 3 schemas);
- a schema with tables read back still read "failed";
- Egeria rejected the parent link of a new schema (the 500 on the first press).

#501 fixes these: the structural-relationship rule and an anchor-aware walk (so a schema with only the template's own
connection graph plans a soft delete and one with a term assignment on a table plans an archive); the live target-list parser
and a guard that never initiates the attach twice (reads the list first; an `attach_requested` proof row; the drain uses the
same guard); state from the newest proof row; the parent link taken from the cataloguer's own source (database at end 2,
confirmed by the live read `evidence/READBACK-CATALOGUER-PARENT-LINK-2026-10-06.md`); the survey step reads done only when the
engine action is COMPLETED; the server read by exact name (`server not found` / `server ambiguous · N matches`); US spelling
("cataloged") on the rows it touches; the survey-report sentence.

## The scope you will commit (coco_pharma, as signed)

Read from the shared registry on the evening of 2026-10-05, nothing changed since: `coco_sus` catalogue, `coco_ods`
catalogue, `eu_sales` left out, the other 26 of the 29 schemas undecided; depth `tables_and_columns`. So expect the manifest to
say **2 schema targets to attach · survey limited to 2 schemas** and to count `eu_sales` as left out and the 26 as undecided
(the manifest may group them as 27 not cataloged: record what it prints). Undecided means Egeria stays as it is.

## The six gate items and the words to look for

Compare what the page says with these. They are the strings the code and the evidence notes use; **wording not yet seen live on
the fixed build is marked (new)**. If a word differs, that is a finding, not necessarily a failure.

1. **Manifest and press.** Open Curate, expand the scope. Expect the three mechanisms, the target count, the survey's schema
   list in the form `Egeria's survey is limited to your chosen schemas: coco_sus, coco_ods`, the line
   `Egeria catalogs whole schemas · table choices are kept for when it can` (US spelling is **(new)**; rehearsal 2 printed
   "catalogues"), and (new) `RE's survey report is published whole; it describes all <m> schemas; elements are created for the
   <n> you chose.` Header seen in rehearsal 2: `Database element <guid> in Egeria · published <time> · zones: none · everyone visible · ...`.
   Press Catalog (the button is named Catalogue until the wording slice lands). Each schema should climb queued, sent, attached,
   then `cataloged · read back <when>` (new: before, rows stuck at "failed"). Step words seen live: `publish_elements`
   `server <guid> · database <guid> · descriptions and versions supplied`; survey `submitted · <time> · ...` then `done · report <guid> · <n> annotations · read back <time>`;
   refresh `refreshed · connector time moved <t1> → <t2> (the JDBC cataloguer connector's own time, not a schema's)`; zones
   `zones left to Egeria · RE writes no ZoneMembership (EXPLORER_PUBLISH_ZONES is not configured)`. Tables and columns appear
   under each schema, **none directly under the database** for the chosen schemas.
   **Watch for:** more than one catalog target per schema on the JDBC cataloguer (the guard should prevent it; this is the
   first live proof). A press creates real elements on coco_pharma's database in Egeria.
2. **Leave out a CATALOGED schema.** On a cataloged schema the tables hang off it two hops away
   (schema, its schema type, tables), so the build rules that a cataloged schema **always archives**. Expect the preview to say
   `<schema>: <n> ... hang off it · will be archived in Egeria, not deleted · ...` and to name the tables
   (and `archive · lineage to <name> would be lost` when something else points at it); after the press
   `archived in Egeria · <time>`. **That is the correct result, not a failure.** The soft-delete path is NOT reachable on a
   cataloged schema, by design. It is proven by the tests in #501 and will be proven live on a throwaway later: a schema left
   out while still "attached", before the refresh has made tables. For the record, the strings the code prints for that path
   are, in the preview, `<schema>: nothing hangs off it · will be removed (soft-deleted) from Egeria` (with
   ` with its <n> tables` and ` · delete · nothing depends on it`), and on the row `removed · <time>`
   (the architect expected the verb "deleted from Egeria"; the code prints "removed", so if that is the wrong word it is a
   wording follow-up, not part of this gate).
3. **Leave out one with a term assignment on a table.** Expect the preview to name it: `1 term assignment hang off it · will be archived in Egeria, not deleted · can't be re-included until Egeria restores archived elements`, then `archived in Egeria · <time>`, the
   assignment surviving on the table, and choosing it again refused with `1 schema not committed: <schema> · can't be re-included until Egeria restores archived elements`. A schema with lineage to another asset says (new) `archive · lineage to <name> would be lost`.
4. **Colliding names** (`a_b`/`aXb`, `x_y`/`xZy`): flagged before the press (`⚠ Egeria's listing for a_b would also return aXb`; blockers
   `2 name collisions Egeria's listing can't tell apart: leave the schema out, or rename it in the database`). coco_pharma may
   have none; the rehearsal proved the check.
5. **After an Egeria restart that keeps the store, scope and states survive; after a reset, pressing again recreates from the
   scope record.** Not exercised live; the tests use a fake reset. Not a gate step to do tonight.
6. **Classic shows no database Publish; the header reads from state** (change the state, the line changes). `no scope declared · nothing catalogued` appears if a
   database has no declared scope and you press the survey-definition retry (409, nothing written).

## Do not, and watch for

- Do **not** press Catalog on any database other than the one you are walking; undecided schemas are untouched by design.
- If a page row reads `in use by a running survey · wait or cancel` on a leave-out, a survey is still running on that schema; wait.
- Every press writes to the shared dev Egeria; the shared registry holds the scope, outbox and proof rows
  (`catalogue_commit_proofs`).
- Unverified live on the fixed build (rehearsal 3 would be the first): the schema create with the parent link at end 2;
  the leaf-first archive against a real Egeria; DataFlow far ends on Resource Explorer's own schemas; `requestType` on the
  ActionTarget's engine action; the poll window against a real attach.

## Still open for you (not part of the gate)

- First rehearsal's leftovers, yours to clear with an identity that may write in zone `egeria-runtime`: Egeria elements
  `edb12250-e7e2-4024-9c6d-a8fb384d7956` (database), `d87059ce-2d87-4e0a-b35f-0652dc00d841` (server graph), their connection
  graphs, two survey reports, engine action `6c5100b1` (IN_PROGRESS), the throwaway secrets-store graph; Postgres database
  `scratch_cat_test3` and role `scratch_cat_role3`. (`scratch-cat-test3.omsecrets` went with the 02:03Z secrets re-copy.)
- The `<DBQN>::<schema>` versus template qualifiedName mismatch and the S12 findings go to the Egeria leads as suggestions
  (architect writing). CI `timeout-minutes` 30 to 45 is still your decision.
- Steady refusals as `erinoverview` (about 90 a minute, 16:50 to 19:48 UTC on 2026-10-05) remain unidentified: not 8810, not the Portal's canary, not
  egeria-python-20's tests; the live tier was auto-on in every full suite run until #500 merged, which is the leading explanation (counted run pending).
