# Curate for repositories: explicit selection of folders and files (brief 2a) — implemented

Branch `re/curate-explicit-selection`, built on main after brief 1. Brief: `BRIEF-CURATE-EXPLICIT-SELECTION-REPOS.md`
(sections 1 to 3; nesting depth is brief 2b and is untouched). Not run against a live Egeria, a live page or the
shared registry: every Egeria call is faked in tests and every registry is a temp SQLite file.

## The DDL (needs the owner's separate go and a peer round BEFORE a restart applies it)

The registry creates its tables when a `ProjectRegistry` is constructed (`_init_schema`), so merging and restarting
a process against the shared registry would run this. It is one block, `RESOURCE_SCOPE_EVENTS_DDL` in
`resource_explorer/registry.py`, called from one two-line loop in `_init_schema`. Additive and idempotent: `CREATE ...
IF NOT EXISTS` only, no `ALTER` of any existing table, no data migration, no foreign keys.

```sql
CREATE TABLE IF NOT EXISTS resource_scope_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_type   TEXT NOT NULL,
    resource_slug   TEXT NOT NULL,
    locator         TEXT NOT NULL DEFAULT '',
    kind            TEXT NOT NULL DEFAULT 'file',
    choice          TEXT NOT NULL DEFAULT '',
    action          TEXT NOT NULL DEFAULT 'set',
    source          TEXT NOT NULL DEFAULT 'person',
    proposal_rule   TEXT NOT NULL DEFAULT '',
    reason          TEXT NOT NULL DEFAULT '',
    author          TEXT NOT NULL,
    changed_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_resource_scope_events_locator
    ON resource_scope_events(resource_type, resource_slug, locator, id);
```

On Postgres the translator turns `INTEGER PRIMARY KEY AUTOINCREMENT` into `SERIAL PRIMARY KEY` (pinned by
`test_the_ddl_survives_the_postgres_translator`: no `?`, no `:name`, nothing rewritten). Append-only: the registry has
three methods for it (`append_resource_scope_event`, `list_resource_scope_events`, `current_resource_scope`) and no
update or delete; a test fails if an `UPDATE` or `DELETE FROM` of the table ever appears. Nothing in the pg fixtures'
scratch schemas was run by hand; they create it through the same `_init_schema`.

## Where the brief and the code disagreed (the code won; the brief's intent was kept)

* **The "repository not yet in Egeria" blocker.** The Curate commit's first step publishes the repository asset and its
  survey report itself, so a Curate blocker saying "publish the repository first" would deadlock a first press. It is a
  blocker where the press really needs an existing asset: the Analysis pane's press (the catalog route answers 409
  without one), with a button that goes to the Curate pane. Curate keeps its other blockers (nothing selected, no
  survey, no project context, sign in).
* **"Publish N items".** N is files + chosen folders + containers, as the brief says, so the gate's "Publish 2 items"
  holds with the default-confirmed lines. A press with only confirmed lines is still allowed (it publishes the
  repository and its report), and its button reads "Publish →"; "nothing selected" means no line and no item.
  The button word moved from Catalog to Publish, so `wording-save-catalog-publish.test.mjs` no longer pins "Catalog →"
  for a repository (a database keeps its own).
* **A latent break in brief 1's merged step.** `execute_curation` closes its event loop and sets none behind it
  (`set_event_loop(None)`) after the report publish, and `EgeriaPublisher` drives its async calls through
  `asyncio.get_event_loop()`, which raises in a thread with no loop. Every sub-resource create after the report would
  fail with "There is no current event loop" in a thread that has none (the new press test hit it). `publish_chosen`
  gives the call a loop when the thread has none and puts the thread back as it was. Not seen live.
* **`sub_resources` is written at publish, not at choice.** The publisher publishes only rows already in `sub_resources`,
  so the shared publish function catalogs them locally at the moment of the press. A choice never touches the table.

## Section 1: the record

`resource_scope_events` (above) and `ProjectRegistry.append_resource_scope_event / list_resource_scope_events /
current_resource_scope`. The newest row per locator is the current choice; a clear is a row with `action='clear'` and
`choice=''`, so who cleared it and when is kept. Generic across `resource_type` so file systems reuse it. `kind` is
`folder | file` (the table column would hold `file_type` but the registry and the route refuse it: file types are a
measurement published as Egeria's profile annotations, not a choice, per #540); `source` is `person | proposal`.

`resource_explorer/resource_scope.py` turns the record, the sub-resource survey and `sub_resources` into one view:
`rows` (one per candidate, per container folder, and per locator with a choice that left the survey), `manifest`
(files, folders, containers, items, chosen, container_locators, not_selected, proposals_not_accepted, left_out,
published_earlier) and `proposals`. `GET /api/projects/{slug}/scope-events` returns it; `POST` appends a batch (one
event per row), refuses an unknown locator or a wrong kind with 400 and writes nothing of that batch, takes the author
from the session (401 signed out), and returns the re-read view. Neither route reaches Egeria.

## Section 2: the rows

`stages/resource-scope.js` is the one component for both panes; the selector itself is `selectorHtml`, exported from
`curate-scope.js` (the database scope tree now calls it; same markup). Curate's "what's in it" and the Analysis pane's
sub-resource panel both draw it, under the column header "Publish to Egeria?". The checkbox, "select all worthy / deselect
all", the "also publish to Egeria" box and the contained-set checkbox under the manifest are gone.

* Nothing is pre-ticked. A worthy row with no choice reads "proposed · worthy · <reason>" in muted ink; "accept the N
  proposals" writes N `include` events with `source='proposal'` and `proposal_rule='worthy'`, each then a filled segment
  tagged "accepted proposal".
* A press dims the selector and says "saving…" in the same cell; the server's re-read fills the segment and prints
  "saved · you · just now" (title carries the sentence); a second press while any key is out sends nothing; a failure
  rolls the cell back with "✕ not saved · <cause>".
* A folder's selector is about the folder only: included, its cell says "folder only · N inside not selected" with
  "include its M worthy children" (explicit events on the still-undecided worthy children, none on children a person
  left out); left out, "left out · children keep their own choice".
* A container folder (an included file's folder that is not itself included, with the synthetic root for a root file)
  reads "○ needed as a container · not an asset of its own", is counted separately, and its selector is not filled.
* A published row shows "published · <when>" in its own Egeria cell; leaving it out reads "left out for future publishes
  · published earlier · kept in Egeria". No code path deletes or archives (a test inspects every call the fake clients saw).
* "include all visible" / "clear all visible" act on the filtered rows only, one event per row. Curate hides the not-worthy
  rows behind a "show the rest" box; the Analysis panel lists them all.

## Section 3: the manifest and the publish

The commit table (`stages/repo-manifest.js`) takes `scope` (the view) instead of a list: rows for the files you chose
(`N DataFile`), folders you chose (`M FileFolder`), folders needed as containers (`K FileFolder`, "the files above"),
Left out ("P not selected · Q proposals not accepted · R left out") and Published earlier (S). After a press each of the
three item rows gets its own state from the proof rows (`proof_summary.sub_resources.by_row`). The button is "Publish N
items →".

The route reads the record, not the request: `CurateSelection` no longer has `sub_resources` (an old client's list is
ignored), the commit freezes the record's chosen list into the curation's selection, and the catalog route
(`POST /sub-resources/catalog`) publishes the record too. `publish_to_egeria=false` is retired. Both go through
`resource_scope.publish_chosen`: local cataloging, the ancestor rule (`ancestor_folder_paths`, unchanged), the publish,
and one proof row per element by GUID after a read of that GUID. A second press finds each element by qualifiedName
and creates nothing (tested with the real publisher and a faked Egeria that remembers what it made).

## Not run

No live page, no live Egeria, no shared registry or Postgres. UNVERIFIED LIVE: that `get_asset_by_guid` reads back a
FileFolder/DataFile as brief 1 assumed (the same reader brief 1 used), and the root-folder `CapabilityAssetUse` gap the
publisher already documents. The gate on egeria_git (8813) is the owner's, by use.
