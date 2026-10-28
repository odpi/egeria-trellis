# Curate: what gets catalogued, slice A2.1 (implemented)

2026-10-04, branch `re/curate-scope-a21`, built on `origin/main` f0ed685f (PR #471, slice A2). A polish slice on the
first real look at A2 (`localhost_docker_coco_pharma`, 8810 at f0ed685f). Still **no Egeria write of any kind**, no new
registry table, nothing here queries Postgres or Egeria: stored rows only.

Environment proof: `uv run python -c "import resource_explorer; print(resource_explorer.__file__)"` printed
`/Users/dwolfson/localGit/egeria-v6/trellis-re-scope-a21/packages/resource-explorer/resource_explorer/__init__.py`.

## What the owner saw, and what changed

| Finding | Change |
|---|---|
| Schema Inventory still showed 8 schemas; the credential line said "sees 6 of 8 schemas ... every count on this page is scoped" | `GET /api/databases/{slug}/schema-inventory-tree` now answers from `catalogue_scope.resolve_node_set` (the same call Curate makes; no second merge) plus a `sources` block (`sources_view()`, also used by the Curate view, asserted equal in a test). Each schema and table row carries its source and as-of ("from Egeria survey 10-04 · rows from RE local survey 10-03"). A header line names the survey read and, when the two disagree, both: "29 schemas · Egeria survey 10-04 · RE's own survey saw 8 schemas, 61 tables, 10-03". The credential line now reads "RE's own survey 10-03: connected as surveyor, sees 6 of 8 schemas, SELECT on 3 of 61 relations; counts from that survey are scoped to this credential" (the survey date is the new `credential_capability_at` on the database summary; the scoped clause only when the credential cannot see everything) |
| "0 B" on every table | the size rule below |
| Column lines read as belonging to the next table | columns are grouped in one wrapper directly under their table row, indented under the table's NAME (spacers as wide as the tick and choice cells), with a left rule and a "└" marker, in muted ink at the smaller provenance size |
| Too wide | see widths below |

## The size rule (`catalogue_scope.measured_size_rule`)

The native survey records `tableSize: 0` and `columnCount: 0` for tables it never measured, even beside non-zero
counters: a "not measured" written as a zero. Applied once, to the merged node set in `resolve_node_set`, in order:

1. A column count of 0 is never a measurement (a table has columns). It becomes the number of columns listed, or
   "not measured" when none are listed.
2. A size of 0 with a column count of 0 is not a measurement.
3. A size of 0 where rows were counted above zero (a row count above 0, or inserted/updated counters above 0) is not a
   measurement.
4. A size of 0 stays "0 B" **only** when rows were counted as 0 by a measured (not estimated) source and the column
   count is not 0: a plausible empty table. A size of 0 with no row evidence either way is "not established" and shows
   "not measured".
5. A schema total of 0 is "not measured" unless one of its tables is a measured empty one. A non-measurement also loses
   its `facts_from.size`, so no source is claimed for it.

Both sides are tested (`tests/test_catalogue_scope.py`). The rule also applies to a local-survey size of 0, which was
previously shown as "0 B" whatever the rows were. In the Schema Inventory pane a missing size reads "not measured" and a
missing column count "columns not measured".

## Widths

- The tree's floor was `min-w-max` (the width of the longest unwrapped sentence, so any long state line widened every
  row and forced a scroll at every width). It is now a fixed `min-w-[64rem]`; the container still scrolls sideways as the
  fallback below that.
- Columns: tick 2ch, choice 24ch, Schema / table flexible (floor 16ch, wraps), Rows 9ch, Size 8ch, Activity 18ch,
  Classes 10ch, In Egeria 20ch. Fixed total 91ch plus the 16ch name floor, against about 144ch at 1300px (9px per ch is
  a deliberately generous figure) and eight 8px gaps.
- Headers: "Rows", "Size", "Activity", "Classes", "In Egeria", each with its full wording in the title.
- The per-row source line moved from the state cell to under the name.
- Activity: the cell says "can't tell" / "dormant · 0 writes in 336 days" / "active · 1,204 writes", with the whole
  sentence in the cell's title; the reason is said once above the tree, grouped: "Activity: can't tell on N rows: counter
  reset date not recorded".

## Files

`resource_explorer/catalogue_scope.py` (size rule, `sources_view`), `web/routes/databases.py` (route reads the node
set; summary carries `credential_capability_at`), `web/static/next/stages/scope-sources.js` (new, no imports: header
sentence, per-node source line, credential line), `web/static/next/app.js` (pane header, row sources, credential line),
`web/static/next/stages/curate-scope.js` (layout), `web/static/next/tailwind-next.css` (regenerated on Node 20.11),
`tests/test_catalogue_scope.py`, `tests/test_schema_inventory_tree_route.py` (the empty-tree answer gained `sources`),
`frontend-build/test-harness/curate-catalogue-scope.test.mjs`, `frontend-build/test-harness/schema-inventory-sources.test.mjs`.

## What is left

- Layout is **not measured**: jsdom does no layout. The 1300px fit is reasoned from the column widths above, and the
  owner's eyes are the check.
- Other places that show schema/table counts from RE's own local survey without naming it (not changed): the Questions
  board's `schema_inventory` headline ("8 schema(s), 7 with tables, 6 visible to this credential") and its evidence
  facts (`survey_definition_adapter._schema_inventory_*`, `db_derived`), the database summary's `schema_count` /
  `table_count` / `column_count` (`web/routes/databases.py`, taken from the latest stored survey row), and the classic
  `index.html` database pages. Each reads stored local rows; naming the survey there needs the results reader to carry a
  source and as-of.
- Native column annotations are still not used for column rows (columns come from the local survey when it has them).
- Rules, the filter bar, the Schema Inventory choice control (A3) and slice B (the commit) are unchanged.
