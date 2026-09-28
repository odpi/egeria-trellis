# Slice 22 — per-schema Schema Inventory view in /next — implemented

**Coordinator brief:** owner-approved next slice after #309, off `main`
(`832485e8`). Backlog note of 2026-09-26 and brief row 22: build it from
the structured tables and `_schema_inventory_container_rows` (Slice 21a
already computes), never from the `survey_data` blob the classic UI's
schema panel reads. First slice explicitly gated on usability rather than
sentence-correctness.
**PR:** #TBD (`re/slice22-schema-inventory-view`).

## What was built

**Backend** — `schema_inventory_tree(registry, slug)` (new,
`survey_definition_adapter.py`): the full Schemas → Tables → Columns tree
in one call, built entirely from `_schema_inventory_container_rows()`'s
own per-schema classification (data/empty/staging/no_access/
structure_only/system, already correctly ordered — data by rows desc →
empty → staging → structure-only/no-access → system folded last) plus the
structured `database_tables`/`database_columns` detail rows. Each schema
row is the SAME dict `_schema_inventory_container_rows` returns (so the
tree's schema ordering and classification can never disagree with the
headline's), now also carrying a `"reason"` field (`_schema_inventory_
container_rows` was extended to thread `schema_scope.container_scope_
states()`'s own `explanation` text through — previously only the bare
classification survived past that function). A non-system schema gains a
`"tables"` list; each table carries `row_count`/`row_count_state` (the
raw stored `state` — `catalog_estimate` is the estimate stamp) and
`size_bytes` (`None` renders "not measured", never a false zero); each
column carries `name`/`type`/`nullable` (`True`/`False`/`None` for
genuinely unknown — a catalog-only-fallback column has no source for
this)/`key_role` (`"PK"`/`"FK"`/`""`, from Slice 21b's catalog-floor keys)/
`comment` (empty string when none captured, rendered "comments not
captured" by the frontend rather than a blank cell indistinguishable from
"no comment written").

New route `GET /api/databases/{slug}/schema-inventory-tree`
(`databases.py`), following `get_database_survey_results`'s own shape
(local `ProjectRegistry()`, 404 on an unknown database, `asyncio.
to_thread` since this walks every stored row plus the credential probe).
New `getSchemaInventoryTree(slug)` in `re-api.js`.

**Frontend** — a new `/next` sub-tab, "Schema Inventory", database-only
(`SUB_TABS` gained a `resourceTypes: ['database']` filter — a repo/
filesystem has no schema tree, so the tab is filtered out entirely for
those types rather than shown greyed-out). `loadSchemaInventoryPane()`
fetches the whole tree in one call (no per-node lazy fetch — the classic
UI's own schema panel already proved a whole database's schema/table/
column data is small enough to fetch once and toggle with plain DOM
show/hide) and renders:

- `resourceHeaderHtml(slug)` above the tree (unchanged, already-existing
  function) — this is where the credential banner ("connected as X — sees
  N of M schema(s), SELECT on N of M table(s)") already lives, persistent
  across every sub-tab; Slice 22 does not duplicate it.
- A filter `<input>` that narrows the tree by name across all three
  levels via `filterSchemaTree()`: every schema/table/column carries a
  `data-tree-text` attribute (its own name, or — for a schema/table node —
  the concatenation of every descendant's name), and a match on that
  attribute shows the node AND force-opens every `<details>` ancestor on
  its path to the root, so a match inside a collapsed schema is never
  hidden. Clearing the filter reverts every node to `display: ''` with no
  saved-collapse-state bookkeeping (this tree has no cross-session
  preference to protect, unlike the sidebar's own persistent collapse
  state).
- Native `<details>`/`<summary>` per schema (collapsed by default — no
  `open` attribute), showing the schema name, its classification stamp
  ("N table(s) · M row(s) [(est.)]" for a data schema, "N table(s) — no
  access"/"structure only"/"empty" otherwise), and the container-state
  `reason` text on its own line beneath the summary (never a tooltip).
  Each schema's `<details>` nests one `<details>` per table (row/byte
  stamp, "not measured" for either when never measured), each of which
  nests a plain column table (name, type, nullable, PK/FK mark, comment or
  "comments not captured").
- The trailing folded system-schema row renders as a single info line
  ("N system schema(s) folded…"), never expanded to tables.

## How a person uses this screen

Three tasks, the owner's own gate criteria, and where each click lands:

1. **"Find a named table in `coco_ods` and read its columns and keys in
   two clicks or fewer."** Click 1: type "orders" (or any substring) into
   the filter box — `coco_ods`'s `<details>` force-opens (its aggregate
   `data-tree-text` contains every table/column name underneath it) and
   the matching table's own `<details>` is ALSO force-opened by the same
   ancestor-walk, since the table name itself matched. The column table is
   already visible with no further click needed — filtering directly to a
   table name opens straight to its columns in one action. (Filtering by a
   COLUMN name instead — e.g. "customer_id" — opens the same way, straight
   to the table that owns it.) If browsing instead of filtering: click 1
   opens the schema, click 2 opens the table — two clicks, matching the
   gate's own ceiling.
2. **"Tell which schemas the credential can't see without opening them."**
   Never requires opening anything: a `no_access`/`structure_only` schema's
   stamp says so directly in its collapsed `<summary>` line ("N table(s) —
   no access" / "structure only"), and the `reason` line beneath it (always
   rendered, not gated behind an expand) gives the exact grant gap
   ("No USAGE on demo: nothing in it is reachable by this credential...").
   Zero clicks — visible in the collapsed list.
3. **"See at a glance which schema holds the data."** The tree's own
   ordering answers this without any interaction: data schemas are always
   listed FIRST, sorted by row count descending — the top of the list IS
   "which schema holds the data," and empty/structure-only/no-access
   schemas visibly sort below it. Zero clicks.

## Not verified here: the live browser render

This session's browser tool could not reach an authenticated `/next`
session on the gate server — the sign-in form's own "Continue without
signing in" affordance does not persist a session (confirmed: a reload
returns to the sign-in form), and entering the documented dev password was
blocked by this session's own permission classifier earlier tonight (the
same block disclosed on Slice 21b/the fail-fast branch). Verification here
instead went through: (1) the backend `schema_inventory_tree()` function
and its route, directly against real `coco_pharma` data via
`ProjectRegistry()` — confirmed correct PK/FK/type/nullable/comment
extraction, correct schema ordering matching `_schema_inventory_container_
rows`, correct "not measured" vs real-zero distinction; (2) `node --check`
on the modified `app.js`/`re-api.js` for syntax; (3) a full read-through of
the new render functions against the exact JSON shape the backend actually
returns (not a guessed shape). The three-task usability walkthrough above
is reasoned from the code's actual behavior (the `filterSchemaTree`/
ancestor-open logic, the fixed schema ordering), not observed by clicking
through the running app. Someone with browser access to the gate server
should confirm the visual/interaction result before this is taken as
usability-verified rather than usability-designed.

## Tests

- `tests/test_schema_inventory_tree.py` (new, 7 tests): no rows → `None`;
  a table's columns carry PK/FK/comment correctly; a column with no
  captured nullability is `None` not a guessed `False`; `row_count_state`
  carries the estimate stamp; a never-measured table reports `None` not a
  false zero; schema order matches `_schema_inventory_container_rows`
  exactly and every schema carries a `reason`; a system schema is folded,
  never expanded to tables.
- `tests/test_schema_inventory_tree_route.py` (new, 3 tests): unknown
  database 404s; no stored rows yet returns an empty tree (200, not a
  404); a real schema/table/column round-trips through the route.
- `tests/test_schema_inventory_container_headline.py` (existing, 17
  tests): re-run clean — the new `"reason"` field on each row is additive,
  no existing assertion depended on the row's exact key set.
- `tests/test_next_db_server_discovery.py`: 5 new tests for two small,
  cheap fixes bundled into this branch (found live while gating): a
  `TestNetworkFailureGetsAClearMessage` (the shared `request()` helper in
  re-api.js now wraps a raw fetch-level failure — "Failed to fetch" shown
  three times in the Register Database Server dialog when the RE server
  itself was unreachable — into a clear "Resource Explorer at <origin> is
  not responding" message, once per call site rather than a confusing raw
  browser error); `TestEgeriaHostDefaultsForDocker` (the Egeria-visible
  host field now defaults to `host.docker.internal` when the DB host is
  `localhost`/`127.0.0.1`, never overwriting a value already typed).
- `tests/test_next_analysis_subresources.py`: existing `TestNoFifthTabIsAdded`
  updated — its actual guard (no "sub-resources" tab id, a specific,
  unrelated design ruling) is unaffected; its exact-count assertion now
  includes the new `schema_inventory` tab.
- Full suite: 6571 passed, 104 skipped, 0 failed.

## Report

Branch `re/slice22-schema-inventory-view`, tip: see PR. Two small,
unrelated usability fixes bundled in per the coordinator's own "cheap,
bundle it here" call: the network-failure message and the Egeria-host
Docker default (§ above).
