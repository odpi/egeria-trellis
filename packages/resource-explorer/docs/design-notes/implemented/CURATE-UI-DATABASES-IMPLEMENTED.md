# Curate UI, all kinds (databases first): implemented

Source: REPLY-DESIGNER-CURATE-AND-UNDERSTANDING-ALL-KINDS.md §1-2 and the slice table, `wireframes/CurateAndUnderstanding.dc.html` (page 16), owner rulings of 2026-10-01 (journal append-only; publishing tags/journal to Egeria is later and not built; nothing reads back from Egeria).

Branch `re/curate-ui-databases`, based on `origin/re/curate-authors` (cdc4b9a9), which was NOT yet an ancestor of `origin/main` when this was built. This branch therefore carries the curate-authors backend commit; retarget the PR onto main once re/curate-authors merges (if main has moved to include it by then, merge `origin/main` into this branch with a signed merge commit, do not rebase).

Verified import path: `/Users/dwolfson/localGit/egeria-v6/trellis-re-curate-ui/packages/resource-explorer/resource_explorer/__init__.py`

## What each band does

The Curate pane is now three bands on every kind, in `#curate-host` (still the sibling of `#question-rows` that mountCurateHost makes). New module `static/next/stages/curate-bands.js`; `stages/curate.js` `renderCurate` writes the frame and gives the repo's existing plan view the middle band as its `host`.

1. **Findable** (all kinds): "local · not in Egeria" once. Group: current group name plus an inline select and "change" (calls `assignGroup`, then `refreshGroupsAndSidebar`, then re-reads the row's group; Admin keeps the full manager). Tags: chips (hover shows who added it, from `author_label`), remove (x) on each, add input with a datalist from `GET /api/curate/tags`.
2. **The kind's own work**:
   - database: "Glossary terms on tables and columns" and "Logical schema match", each present with its one sentence (wording from the designer's reply and wireframe); no table.
   - file share: the one sentence, "Nothing to review for file shares yet. Group, tags, ratings and notes above and below apply."
   - repository: the existing plan/component-tree/blueprint/commit view, untouched in logic, now inside the middle band.
3. **What people say** (all kinds, below the middle band, so below the Catalogue commit on a repo):
   - Ratings: headline is counts only ("4 · ★★★★★ 2 · ★★★ 1 · ★★ 1", plus "N comments without a rating" when feedback has no stars). No average anywhere. Each entry: stars, category, message, author label, date. Form: rating select, the unchanged category list, a required message.
   - Notes: the journal, the same component and data as Disposition (`renderJournalWrite`/`renderJournalEntries` now exported from app.js and mounted in the band), append-only, no edit/delete controls. Beneath it "Curator notes from Classic · N · unsigned" (or "· U unsigned" when some are signed), read-only, newest first; delete is offered only on unsigned notes, with an inline "Delete this unsigned note? delete / keep" step; a signed note has no control.

## Endpoints used (all existing; none new)

`GET /api/curate/tags`, `GET/POST /api/curate/tags[-detail]/{type}/{slug}`, `DELETE /api/curate/tags/{type}/{slug}/{tag}`, `GET/POST /api/curate/feedback/{type}/{slug}`, `GET /api/curate/notes/{type}/{slug}`, `DELETE /api/curate/notes/{id}`, `GET/POST /api/journal/{type}/{slug}`, `POST /api/projects/{slug}/group`, `GET /api/projects/groups`. Entity type is `apiEntityType(state.resourceType)` (`database`, `filesystem`, `repo`). New wrappers in `re-api.js`: `getCurateAllTags`, `getCurateTagsDetail`, `addCurateTag`, `removeCurateTag`, `getCurateFeedback`, `addCurateFeedback`, `getCurateNotes`, `deleteCurateNote`.

## Rules applied

- Status words from proof rows: after any write the list is re-read and the status sentence is derived from the re-read ("tag x is on the list" only if it is; otherwise "the write returned, but x is not in the re-read list"). Pinned by a known-negative where the stub server drops the write.
- Signed out: tag input/add/remove, rating selects, message, Submit, Classic note delete and the journal Write button are all disabled with a plain reason ("sign in to ... it needs an author"). A 401 that still arrives says to sign in.
- Author label never blank: server `author_label`, falling back to the same words client-side ("unsigned · from before authors were recorded"); the journal entry renderer got the same fallback.
- A band whose read fails says so in its own slot and the other bands still draw. Nothing reads from Egeria; nothing publishes to it.
- The false sentences ("reachable from the resource header", and the interim "offers none of them") are gone; the old "Curate isn't available for ..." body is gone.

## Deviations from the designer's reply, with reasons

- Journal Write is now disabled when signed out on Disposition too (it shares the component). Before, it was enabled and failed with "sign in to write" on press. This follows the brief's every-write-control rule.
- Group change is not gated on sign-in: `POST /api/projects/{slug}/group` has no auth check and records no author (the brief's 401 applies to the curate routes). Left enabled; flag if the owner wants it gated server-side.
- "Rate it" is a select plus a required message, not a bare star row: the feedback route requires a message (400 otherwise), so a stars-only entry cannot be saved.
- Ratings of entries with no star value are counted separately as "comments without a rating" rather than being hidden, so the headline total equals the rated entries only.
- "Curator notes from Classic" is always expanded (the wireframe shows a "show" toggle); kept simple, easy to collapse later.
- Test-pin edits: `tests/test_curate_honest_non_repo_degrade.py` and `tests/test_next_false_capability_claims.py` pinned the removed sentences; rewritten for the new truth (the known-negative that no other /next file calls the curate routes is kept, with curate-bands.js exempted because its comments name the routes; calls still go through re-api.js).

## Deferred

- Understanding UI (sibling slice), file-share charts, anything Egeria-side (InformalTag publish, read-back).
- Glossary-term and schema-match rows themselves (readers do not exist; the sections say what they wait for).
- Classic notes "show/hide" toggle.
- Retiring Classic's own Curate UI, which now gets 401/409 it does not message (noted in CURATE-AUTHORS-IMPLEMENTED.md).

## Tests

`frontend-build/test-harness/curate-bands.test.mjs` (18 tests, jsdom, real app.js and router, stateful stub server): three bands with real controls on a database; tag add/remove API shapes and chips from the re-read; group change shape; rating headline with counts and no average; rating POST shape and required message; journal POST shape; legacy vs signed Classic notes (label, delete only on unsigned, newest first, confirm step, DELETE shape); no edit control; signed-out disables every write control and sends no write; file-share sentence and no kind sections; repo plan view still renders with bands around it and `entity type repo`; a failing band shows a message. Known-negatives: server drops the write (no "added" status); legacy row with no `author_label`; and mutation checks (signed note given a delete, status taken from the click, an average added each make exactly one test fail). `curate-pane-renders.test.mjs` updated for the new db/filesystem body.

Evidence: with app.js, curate.js and re-api.js reverted to the base, the harness subset (curate-bands + curate-pane-renders) is 19 fail / 4 pass (the 4 are the repo/host-survives/known-negative/db-has-no-file-share-sentence cases that hold on base); with the change 23/23. Full `npm run test:harness` (Node 20.11.0): 210/210. `pytest tests/test_next_*.py tests/test_curate*.py` with `PGVECTOR_PORT=1` and a temp SQLite `REGISTRY_DATABASE_URL`: 702 pass, 9 fail, all 9 in `tests/test_curate_authors.py` and all 9 also fail on the unmodified base branch when run in this combined glob (they pass 19/19 when the file runs alone: a test-order interaction in the curate-authors branch, not in this slice). Excluding that file: 692 pass.

## Not verified

- No real browser render or real screenshot: jsdom with a stub fetch only. Tailwind classes were reused from neighbouring panes; I did not check that every one exists in the built CSS (`text-state-warn` and the rest are used elsewhere in the same files).
- No run against a live 8813, a real signed-in session (state.me comes from the stub), or Postgres.
- `refreshGroupsAndSidebar` after a group change was exercised against the stub only; the sidebar re-render in a full page was not seen.
- Autocomplete is a native `<datalist>`; its dropdown behaviour was not exercised.
- The repo path's bands were tested with an empty plan, not real data.
