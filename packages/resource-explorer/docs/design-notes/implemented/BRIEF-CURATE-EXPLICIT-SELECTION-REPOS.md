# BRIEF 2a — Curate for repositories: explicit selection of folders and files (2026-10-07)

Design session brief for the coordinator and one builder, from
`DESIGN-CURATE-NESTING-SELECTION-AND-DEPENDENCY-KINDS.md` §2, at the owner's
go ("the explicit selection"). **Starts after brief 1
(`BRIEF-CURATE-REPO-PUBLISH-DEPENDENCIES-BLUEPRINTS.md`) lands**, on a branch
from the main that carries it. Nesting depth (§1 of the design note) is
brief 2b, later, and nothing here pre-empts it: this brief works on the
candidate set the sub-resource survey already produces at its current
depth. Every rule of the parity brief's preamble applies (verbs, cue
vocabulary, proof rows by GUID after a read, Egeria's full sentence stored,
the ISSUE-117 block on, US spelling, worktree and red runs, registry guard).
Gate by the owner on egeria_git on 8813, by use.

## The rule

**Selecting a folder never implies publishing what is nested in it.
Publishing is of named items, previewed, plus the ancestors needed to hold
them. Worthiness is a proposal, never a decision.**

What the code does today (main c5304bb5): the Curate pane's "what's in it"
takes the contained set whole through one checkbox under the manifest
(`stages/curate.js`, `data-curate-subs`, "select all / select none"); the
sub-resource panel (`stages/analysis.js`, `subResRowHtml`) pre-ticks rows
the survey labelled *worthy*; `catalogSubResources` posts the ticked
locators and the route auto-includes ancestor folders because Egeria's
`NestedFile` needs a `FileFolder`; the `sub_resources` table records what
was cataloged and its `egeria_guid`. There is no record of a choice
separate from a publish.

## 1. The selection record (registry, additive DDL, peer round)

A choice is RE's record, saved before and separately from any publish, the
same declare-then-commit pattern as the database catalog scope. The
database table (`catalogue_scope_events`) is keyed by `database_slug`,
`schema_name` and `table_name` and cannot hold a path, so repositories get
their own append-only table:

`resource_scope_events(id, resource_type, resource_slug, locator, kind
('folder'|'file'|'file_type'), choice ('include'|'leave_out'|''), action
('set'|'clear'), source ('person'|'proposal'), proposal_rule, reason,
author, changed_at)`, index on `(resource_type, resource_slug, locator,
id)`. The newest row per locator is the current choice. No update, no
delete, ever. The `sub_resources` table stays as the record of what was
**published** (locator, `cataloged_at`, `egeria_guid`); it is never written
by a choice.

Registry methods: `append_resource_scope_event`, `current_resource_scope
(resource_type, slug)`, both generic across kinds so file systems reuse them
later. A test proves the Postgres translation of the DDL (no `?` or `:name`,
AUTOINCREMENT → SERIAL) as the group_changes migration did.

## 2. The rows

In both places that list candidates (the Curate pane's "what's in it" and
the Analysis pane's sub-resource panel, which must show the same state from
the same record):

- The checkbox goes. Each row gets the two-part selector under the column
  header **"Publish to Egeria?"**: `Include | Leave out`, × to clear, the
  selected segment filled in ink, the other outlined (the designer's panel
  A; the same component the database scope tree now uses, reused, not
  copied).
- A press dims the selector, shows "saving…" in the same cell, writes the
  event, re-reads, fills the segment and prints "saved · you · just now"
  with the sentence one gesture away. A second press while pending does
  nothing (no second POST). A failed write rolls the cell back with "✕ not
  saved · <cause>".
- **Worthiness is a proposal.** A row the survey labelled worthy reads
  "proposed · worthy · <reason>" in muted ink, segment unfilled, until a
  person includes it; "accept the N proposals" is one explicit control
  above the table that writes N `include` events with `source='proposal'`
  and `proposal_rule` set, each visible as a filled segment afterwards.
  Nothing is pre-ticked.
- **A folder's selector is about the folder as an asset only.** When a
  folder is included, its cell adds "folder only · N inside not selected"
  and a one-gesture action "include its M worthy children", which writes
  explicit events on those children; a folder that is left out leaves its
  children's own choices untouched and the row says "left out · children
  keep their own choice".
- **Containers are shown, not hidden.** When an included file's folder is
  not itself included, that folder's row reads "needed as a container · not
  an asset of its own" with a hollow mark, and it is counted separately in
  the manifest. The ancestor rule in the route (`ancestor_folder_paths`)
  stays the mechanism; the UI stops hiding its effect.
- A row already published (in `sub_resources` with a GUID) reads, in the
  Egeria lane, ✓ "published · <when>" and its selector still works: leaving
  it out means "left out for future publishes · published earlier · kept in
  Egeria". **No un-publishing** in this brief (roll-forward; the owner's
  rule).
- "select all / select none" become "include all visible" / "clear all
  visible", acting on the filtered rows only and writing one event per row.

## 3. The manifest and the publish

The "what this commit does" table (brief 1's panel C for repositories)
gains the selection's rows and counts from the record, never from the DOM:

| | what | how many | from |
|---|---|---|---|
| Egeria gets | the files you chose | N `DataFile` | your selection |
| Egeria gets | the folders you chose | M `FileFolder` | your selection |
| Egeria gets | folders needed as containers | K `FileFolder` | the files above |
| Left out | nothing in Egeria changes | P not selected · Q proposals not accepted · R left out | — |
| Published earlier | kept in Egeria | S | previous publishes |

The button reads **"Publish N items"** (N = files + chosen folders +
containers) at the table's top right; blockers directly under it: nothing
selected; the repository not yet in Egeria ("publish the repository first
→", routing to brief 1's Publish band); no project context (brief 1's two
choices). The press publishes exactly the N items, one proof row per
element by GUID after a read-back, states "sent · waiting for Egeria",
"published · read back <when>", "not published · <Egeria's sentence>"; a
second press reuses by qualifiedName and never makes a second asset. The
old `publish_to_egeria=false` "sandbox" flag on the catalog route is
retired: a choice without a publish is now the selection record itself.

## 4. Files this brief touches, for sequencing against brief 1

- `web/static/next/stages/curate.js` — the "what's in it" column, the
  `data-curate-subs` block, the manifest rows and the go handler
  (**conflicts with brief 1**, which rewrites the manifest table and the
  Catalog/Publish button; 2a edits brief 1's table, so it must start from
  brief 1's merged tip).
- `web/static/next/stages/analysis.js` — `subResRowHtml`,
  `renderSubResourcePanel`, the catalog press (brief 1 does not touch it).
- `web/static/next/stages/curate-scope.js` — only to export the selector
  component for reuse (brief 1 does not touch it).
- `web/static/next/re-api.js` — two wrappers for the scope-event routes
  (brief 1 adds its own wrappers; append, no conflict beyond the merge).
- `web/routes/projects.py` — two routes (`GET/POST
  /{slug}/scope-events`), and the catalog route reads the record for the
  containers count (brief 1 does not touch this file).
- `registry.py` — the new table and two methods (brief 1 adds a proof kind
  there; small merge).
- `workflows/curate_commit.py` — the commit's sub-resource list comes from
  the record instead of the request body (**brief 1 edits this file** for
  the re-survey checkbox; sequence after it).
- Tests: `tests/test_resource_scope_events.py` (new), the harness files
  for the two panes, the DDL translation test.

## 5. Tests that fail first

- A worthy candidate is not ticked and reads "proposed"; accepting the
  proposals writes N events with `source='proposal'`.
- Including a folder writes one event for the folder and none for its
  children; "include its M worthy children" writes M.
- A file included under an unincluded folder yields a container row and the
  manifest counts it as a container, not a chosen folder.
- The manifest's counts equal the record's current choices; a changed
  choice changes the count without a publish.
- The publish sends exactly the N items and writes one proof row per GUID
  after a read-back; a second publish writes zero new elements.
- A published row left out is still published (no delete call is ever
  made; the ISSUE-117 block's test for this path).
- A second press while pending issues no second POST.

## Gate (egeria_git on 8813, by use)

1. Open Curate: no row is ticked; worthy rows read "proposed"; the
   manifest says 0 items.
2. Include one file in a folder you did not include: the folder row reads
   "needed as a container"; the manifest says "1 file · 0 folders · 1
   container"; press "Publish 2 items"; both rows reach "published · read
   back".
3. Include a folder: its cell says how many inside are not selected; the
   manifest counts 1 folder; "include its worthy children" fills the
   children's segments and the count rises.
4. Leave out a published file: the row reads "left out for future publishes
   · published earlier · kept in Egeria"; a read-only lookup shows the asset
   still ACTIVE.
5. Reload the page: every choice survives (it is the record, not the DOM).
