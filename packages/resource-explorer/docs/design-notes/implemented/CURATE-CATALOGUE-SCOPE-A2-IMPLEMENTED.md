# Curate: what gets catalogued, slice A2 (implemented)

2026-10-04, branch `re/curate-scope-a2`, built on `origin/main` 074869f0 (PR #467, slice A). Fixes to what slice A
showed on its first real use (`localhost_docker_coco_pharma`). Still **no Egeria write of any kind**, no new
registry table, and nothing here queries Postgres or Egeria: stored rows only.

Environment proof: `uv run python -c "import resource_explorer; print(resource_explorer.__file__)"` printed
`/Users/dwolfson/localGit/egeria-v6/trellis-re-scope-a2/packages/resource-explorer/resource_explorer/__init__.py`.

## What the first look found, and what changed

| Finding | Change |
|---|---|
| The tree was RE's own credential-scoped survey of 10-03 (8 schemas, 61 tables); the post-reset Egeria native survey (29 schemas, 266 tables) was never read | `resolve_node_set()` builds the tree from the fullest, newest complete measured set. Native survey rows (`registry.list_native_survey_runs` + `query_native_survey_annotations`) are read first-class |
| Header said "Egeria's latest survey: not measured yet" | slice A looked for `database_surveys.source = 'egeria'`; the header now reads the native rows (the old row is only a fallback when no native run is stored) |
| Two false "no writes since 09-30" leave-out proposals | the two-survey rule is replaced by the designer's counters-since-reset `dormant` rule; short windows read "can't tell" and propose nothing |
| No way to choose 29 schemas | row checkboxes, "select all schemas", bulk bar, "catalogue all N schemas" |
| Column title "Name"; STATE clipped at the right edge | "Schema / table"; the tree scrolls inside its own container |

## How the node set is chosen (`catalogue_scope.resolve_node_set`)

- **Native**: the newest native survey whose stored annotations are all present (the run's `report_annotation_count`
  equals the rows stored; a run that lost rows is skipped). Nodes come from the schema annotations and the table
  annotations ("Capture Database Schema/Table Measurements"); `json_properties` is a JSON string and is read
  defensively together with `resource_properties`. A table whose name is unreadable is counted (`sources.unreadable`,
  drawn in the tree header) and never silently dropped; a readable name with an unreadable fact lists with the
  fact "not established". System schemas stay folded.
- **Local**: the existing Schema Inventory tree, as before.
- **Choice**: the FULLER set (schemas + tables) is primary; a tie goes to the newer. A newer but thinner survey does
  not hide schemas (that is how an 8-schema credential would hide 21). The other source fills facts the primary
  lacks, node by node (rows from the local survey, size from the native one), and each filled fact records its own
  source. A node only the other source has is **added when that source is newer** (it appeared after the primary was
  taken, with its own source and as-of); an older survey's extra node is not listed (it may have been dropped).
- Every node carries `source {kind, as_of, text}` and `facts_from`. The row's muted line reads "from Egeria survey
  10-04" / "from RE local survey 10-03", plus "rows from RE local survey 10-03" for a filled fact.
- Header: one source reads "29 schemas · Egeria survey 10-04" or "8 schemas · RE local survey 10-03 · sees 6 of 8";
  when the two sources' counts differ it names both ("Egeria's latest survey covers 29 schemas, 266 tables · RE's own
  survey saw 8 schemas, 61 tables, 10-03"). The "sees 6 of 8" is derived from the tree's no-access rows.

## Baselines and "new since"

A baseline now also records `source` and `as_of` inside `baseline_json` (no schema change). A scope declared now
baselines all 29 schemas; a later 30th is "new since".

**How a pre-existing baseline from a thin source is treated** (the safe reading, tested): a node absent from the
baseline is "new since" only if (a) a measurement taken AFTER the declaration shows it, AND (b) no complete survey
taken at or before the declaration (the newest native run and the newest local snapshot of that time) already knew
it. So an 8-schema baseline recorded on 10-03 does not make the 21 schemas that Egeria's 10-02 survey already listed
"new"; a schema that first appears in a 10-09 survey is. Baseline rows already stored are never rewritten. An unknown
as-of date is not flagged. Consequence to know: the "first seen" test needs a stored older survey; with none, a node a
thin baseline missed that appears in a later survey is flagged.

## Activity, rows and size (designer round 2 wins over the first brief)

Built against `REPLY-DESIGNER-CURATE-SCOPE-ROUND-2.md` (read from `origin/re/design-curate-scope-round-2`, not merged).
Changes from my first A2 draft, each one a consequence of that reply:

1. The "suspended `no_writes`" flag is gone. The rule is **replaced** by `dormant`: evidence is the cumulative counters
   since `stats_reset` (never the difference of two surveys; `_change_rates`/`derive_change_rates` is no longer read here),
   window = reset -> the source survey's date. **active** = writes counted ("active · 1,204 writes since counters reset
   06-02"); **dormant** = 0 writes in a window of at least `DORMANCY_DAYS` (90) ("dormant · 0 writes in 336 days (counters
   reset 2025-11-02)"), the only activity word that proposes (leave out, reason "dormant, 0 writes in 336 days");
   **can't tell** = fewer days than the threshold ("can't tell · counters reset 10-03 · 2 days of evidence"), no reset date
   ("can't tell · reset date not recorded"), or no counters ("can't tell · counters not measured"). A can't-tell proposes
   nothing. A schema is active if any table is, dormant if all are, else can't tell.
2. Counters come from the newest stored `database_table_activity` rows and from native annotations (`numberOfRows*`, the
   database annotation's `lastStatisticsReset`); the one with the longest window wins. Stored rows only. The reset date is
   found where stored; where it is not, the row says so rather than inventing it.
3. The data-lens **proposal by name is withdrawn**. A lens term found in table names yields only the sentence "The lens
   names Sales: 14 table names contain it · make that a rule?" (`view.suggested_rules`, drawn muted, no control). No rules
   are built. (No lens is stored on this build, so it fires on no real database; tested through the `lens` argument.)
4. Columns, in the reply's order: choice, Schema / table, rows, size, activity (header carries the 90-day definition),
   data classes, State in Egeria. A schema row shows its table count in the name cell. Rows/size are right-aligned
   tabular figures with "≈" for an estimate, "not measured" (never a blank or a zero), "? not established" (no access),
   "◐ sources disagree" with both values and dates when two stored sources differ by more than 2x, and a schema roll-up
   that says "≥ N · K tables not measured". Source and as-of ride on hover and in the source line.
5. **Interpreted:** the reply's "State in Egeria" column needs a read of Egeria's own state, which no stored row holds on
   this build and A2 must not query Egeria. The column is drawn and says "not read yet" on every row; slice B fills it.
   The existing scope lines (new since, conflicts, notes, source) live in that cell below it.
6. **Not built (by instruction):** filter bar, rules, "what decided it", return-visit strip, the Schema Inventory choice
   control. The dormancy threshold is the 90-day default, shown but not yet settable per scope (a settable value needs
   storage the "no new tables" rule defers).
7. Stored rows can disagree about row counts only where both a local and an `egeria`-source `database_tables` snapshot
   hold a count. The native survey carries no row count of its own, so on coco_pharma today most rows read from the local
   survey alone.

## Bulk choice

`POST /api/catalogue-scope/{slug}/nodes` `{nodes: [{schema_name, table_name}], choice, all_schemas}`; `choice` empty
clears. One event per node with the session author (401 when signed out; the body has no author), one baseline, the
whole request validated before any write. A proposal under a node is recorded as a confirmation or override exactly as
for one row. A bulk choice on a schema never writes to a table with its own explicit choice: those are returned as
`differing_tables` and the tree keeps showing "differs from its schema". The status sentence ("29 schemas now set to
catalogue by dwolfson") is derived from the re-read scope.

## Files

`resource_explorer/catalogue_scope.py` (node set, merge, activity, new-since, `set_nodes_choice`),
`web/routes/catalogue_scope.py` (`/nodes`), `web/static/re-api.js` (`setCatalogueNodes`),
`web/static/next/stages/curate-scope.js` (header, source lines, ticks, bulk bar, columns, overflow),
`web/static/next/tailwind-next.css` (regenerated on Node 20.11),
`tests/test_catalogue_scope.py` (70 tests), `frontend-build/test-harness/curate-catalogue-scope.test.mjs` (37).

## What is left

- Layout is **not measured**: jsdom does no layout, so the harness asserts that the STATE cell is present and that the
  container sets `overflow-x`, not that STATE is fully visible at ~1300px. The owner's eyes are the check.
- Native column annotations are not used for the column rows; columns still come from the local survey when it has them.
- Rules, the filter bar, the return-visit strip and the Schema Inventory choice control (A3, later).
- Slice B (the commit) is unchanged and not started.
