# Understanding for databases: the /next UI

Slice: REPLY-DESIGNER-CURATE-AND-UNDERSTANDING-ALL-KINDS.md §3 (Understanding on a
database), §4 (chart-kind list), §5 (Export). UI half; the routes are
UNDERSTANDING-DB-CHARTS-IMPLEMENTED.md. Branch `re/understanding-ui-databases`, built on
`origin/re/understanding-db-charts` (92893b15), because that branch was not yet in
`origin/main` when this was built.

`resource_explorer` resolved to the worktree before any test:
`/Users/dwolfson/localGit/egeria-v6/trellis-re-understanding-ui/packages/resource-explorer/resource_explorer/__init__.py`.
Node 20.11.0. pytest ran with `PGVECTOR_PORT=1` and `REGISTRY_DATABASE_URL` at a temp SQLite
file; the shared Postgres was never contacted, nothing was written to Egeria.

## What each chart shows, and from which route

All in `static/next/stages/understanding.js` (`renderDatabaseUnderstanding`), drawing into
`#understanding-host`. A card is: title, a state line, the figure (or the not-measured
sentence), extras, and a provenance line with its own "Save image".

| Section | Chart | Route | Notes drawn from the response |
|---|---|---|---|
| Now | Tables and columns per schema | `schema_distribution` | the server's two-panel figure (tables, columns); `schema_scope` names schemas not fully readable |
| Now | Largest tables, by rows | `table_sizes?measure=rows` | `not_established_count`/`_tables` as "? N tables' row counts not established — statistics not gathered on the server · by size instead ›"; the count opens the names; `truncated_by_limit`, `views_excluded` stated |
| Now | Column types | `column_types` | `type_not_recorded` as "? type not recorded · N" |
| Over time | (sentence) Since the last run | existing `GET /api/databases/{slug}/diff` | counts open the added/removed names; fewer than two runs: "Only one run so far — nothing to compare"; `table_diff_state != measured`: the route's own `table_diff_note` and no counts |
| Over time | Structure over runs | `survey_history` | `points[]`, one point per run with its full timestamp; null is a gap (`connectgaps: false`) |
| Over time | Rows per table over runs | `table_growth` | the owner's wish (growth in rows per table); established counts only, gaps where not established |
| Over time | Activity per table | none | "not wired up yet", present in its own slot (see deviations) |

"Now" header: run label and source from the first response that has a `run`, plus
"◐ credential scope: <fraction> readable" when a response's `scope.partial` is true. "Over
time" header counts `points`. A per-kind list (chips) above the sections carries each chart's
state word from its response.

State words come from the response: `state` (measured / partial / not measured) and `reasons`
(translated by a lookup; an unknown code prints as itself). `not_measured` draws no figure
at all: it says "○ not measured — <notes[0] or the reasons>", or for `never_surveyed`
"○ not run: needs a database survey" with a Run survey control (clicks the real Discovery nav
button). A failed route says "could not be read: <error>", never a blank or a zero. The
Plotly figure is the server's, themed with app.js's `chartLayout`.

File share: one sentence, "No charts for file shares yet. Their survey isn't charted anywhere
today.", and no fetch. A repo is unchanged. `CHART_NO_EQUIVALENT_REASON`,
`CHART_GAP_ROUTE` and `nonRepoChartIndexHtml` are deleted.

## Save image

`static/next/chart-export.js` is one stage-independent module (Scouting can import it later;
it is not wired in). `provenanceFromResponse(resource, title, response)` reads
`response.run.surveyed_at` and `response.run.source`; `composeFooter` builds
`<resource> · <title> · run <surveyed_at> · source <source>` (a missing run is written
"unknown", never omitted); `saveChartImage` redraws the figure off screen with the footer as
an annotation under the plot, calls Plotly's own `toImage` on that, downloads the PNG, and
purges it. The visible chart is not redrawn. The harness test builds the expected footer from the
response fixture (not from the page) and compares it to the annotation Plotly received, and a
known-negative changes the response's source and run and shows the footer follows.

## Minimum edits outside understanding.js

- `static/re-api.js`: `getDbChart(slug, kind, params)` and `getDatabaseDiff(slug)`.
- `static/next/app.js`: two `export` keywords (`loadScript`, `chartLayout`). No logic changed.
- `static/next/tailwind-next.css`: regenerated (`npm run build:css:next`) for the new
  utility classes. It is one minified line, so any sibling branch that also regenerates it
  will conflict textually: regenerate again at merge rather than hand-merging.
- `tests/test_understanding_chart_honesty.py`: one pin asserted the deleted "does not apply"
  text; it now asserts the removal and points at the harness test.

## Tests

`frontend-build/test-harness/understanding-databases.test.mjs`, 20 tests, real `app.js`, real
router (clicks the real Understanding nav button), fetch and Plotly stubbed:
six charts in two sections from the routes; section headers; not_measured (state, sentence, no
figure drawn into the card, no zero in its text, by-size offered); never_surveyed; partial
table_sizes names the count and opens the names; by-size refetch; type not recorded; two runs
on one day are two points and the null is null with `connectgaps: false`; provenance on every
chart; credential scope; the since-the-last-run sentence (measured and not_comparable); a failed
route; Save image footer and its known-negative; chart-export unit checks; file share sentence
and no fetch; repo charts still draw and no database route is called for a repo; a frame stage
routes before a lingering sub-tab; a missing host throws (known-negative).

Red/green: with `app.js`, `understanding.js`, `re-api.js` reverted to the base, 18 of 20 fail (the
two that pass are the repo-regression test, which is meant to pass on both, and the
chart-export unit test, because `chart-export.js` is a new file that stays). With the change,
20 of 20. Mutations: hardcoding the footer's source, and letting a `not_measured` response
draw, each fail the tests that name them (4 failures).
Full harness: 212 of 212. `pytest tests/test_next_*.py tests/test_understanding*.py
tests/test_curate*.py`: 721 passed.

## Deviations from the designer's reply

- **Activity per table** is a slot saying "not wired up yet" with no route behind it
  (`db_change_rates` has no route). The words "not wired up" are therefore a fact about the
  codebase, not a response field: there is no response to read. The brief asked for this
  wording and the reply (§4) names it.
- **Rows per table over runs** is a seventh element the designer did not draw, added from the
  owner's wish (2026-10-01) because `table_growth` already serves it.
- **"14 columns added"** is written "14 columns net added": the diff route returns a net
  `deltas.columns`, not added columns. No new data was invented.
- **Charts are Plotly figures**, not the wireframe's CSS bars: needed for `toImage`.
- **"Run survey ›"** opens the Discovery stage (via its nav button); it does not start a survey.
- The wireframe puts a "save image" link on each chart's provenance line: done. "by size
  instead" is a link that refetches with `measure=size` and relabels the card; there is no
  automatic fallback.

## Deferred

- Growth rate (delta per day) and a per-run size series (`size_bytes` per run): `table_growth`
  carries row counts only.
- Schema changes beyond the latest pair: the sentence uses the existing diff route (latest two
  runs, table names). Added/removed columns and schemas by name, and arbitrary run pairs, need
  route work.
- Activity per table (`db_change_rates` route).
- File-share charts (owner wants databases finished first).
- Wiring Scouting's chart to `chart-export.js`.

## Not verified

- No real browser render. Everything ran in jsdom with Plotly stubbed, so layout (the
  auto-fit grid, 150px left margin for table names, the footer's position inside a real PNG,
  light and dark theming, `toImage` output) is unseen.
- Nothing ran against a real survey's output or a live server; fixtures are hand-built
  responses in the shape the route note documents.
- `Run survey ›` was tested only as present, not as navigating.
- The exported PNG's footer was proven as text handed to Plotly, not as pixels.
