# W1-A: the investigation picker and the "add to <investigation>" acts — implemented

Source: `REPLY-DESIGNER-WORK-LISTS-VS-INVESTIGATIONS.md` §2 (picker) and §4 (select
bar, report and member-list acts). Built on origin/main 346770d4.

Environment proof: `resource_explorer.__file__` =
`/Users/dwolfson/localGit/egeria-v6/trellis-re-w1-picker/packages/resource-explorer/resource_explorer/__init__.py`
(inside the builder's own worktree).

## What changed

- `static/next/investigation-picker.js` (new): `openInvestigationPicker` (open
  investigations only, plus "start a new one…"), `scopeActWording`, `shortName`
  (24-character cut). Callbacks, not a Promise, because opening the creation dialog
  replaces the picker.
- `stages/investigation.js`: `openCreateDialog` is exported and takes `{ onCreated }`.
  The picker's "start a new one" reuses it; without `onCreated` it behaves as before.
- `static/next/app.js`:
  - Select bar: "＋ add to an investigation…" is enabled with none current and opens
    the picker; after adding, the chosen investigation becomes current and the note
    reads "Added N to <name>, now your current investigation." The note is now written
    AFTER `renderSidebar()` (it was written before it and wiped at once, on every bulk
    add; an old defect on the same path).
  - Report acts and member-list acts: "add to work list" is replaced by "add to <name>"
    / "add to an investigation…" / plain text "already in <name>’s scope". The used-by
    line reads "added to <name>’s scope". `renderRecords` and `wireSelection` are now
    exported (tests only).
- `static/re-api.js`: `actOnRecord` and `promoteMembers` carry `investigation`.
- `web/routes/projects.py`: see below.
- `tailwind-next.css` regenerated.

## Route decision: an argument, not a new endpoint

Both existing act routes (`POST .../records/{id}/act` for repos and for databases and
file systems, and `POST .../members/{analysis}/promote`) gain `action: "scope"` with an
`investigation` field. The server puts the resource in the investigation's Folio with the
act's provenance line as `membership_rationale`, using `keep_existing=True`; a resource
already in scope is reported (`already_in_scope: true`) and nothing is written, so no
`add_use` either. 404 for an unknown investigation, 409 for one that is not open, 422
when none is named. The record's `add_use` is called with act `scope`. `work_list` stays
accepted server-side for old callers and tests; the UI no longer offers it.

## Left for later

- W1-B: the two work-list pane actions ("Add these N to …", "Start an investigation from
  this list…").
- W1-C: vocabulary, sidebar, stage-click rule.
- The to-do the old act stood in for belongs in `WorkItemList`; not built.
- Nothing here was run in a browser or against live Egeria.
