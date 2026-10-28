# Parity slice G3 — database report and Understanding: implemented

Branch `re/parity-g3-db-report-understanding`. Source: `BRIEF-PARITY-G1-G3-TO-ALPHA.md`, section G3.
Everything here is read only: no write to Egeria, no registry write, nothing archived or deleted.
The one thing remembered is the "show invalid" toggle, in the browser's localStorage (try/catch).

| PI | State | What and where |
|---|---|---|
| PI-037 | DONE | "Survey history" on Understanding (`stages/db-report.js` `surveyHistoryHtml`, drawn by `understanding.js` `drawSurveyHistory`): date, schemas, tables, columns, source; invalid rows lowered (`opacity-60`) with "invalid · reason" and "marked <when>"; "show invalid (N)" toggle, key `re-next.showInvalidSurveys`. One fetch of `/surveys?include_invalid=true&slim=true`; `slim` is new (omits the survey blobs, ~61MB on a database with 57 surveys). |
| PI-030 | DONE | Header "surveyed N ago · local survey / Egeria / hybrid" (`app.js` `resourceHeaderHtml`, `surveySourceWord`). Read from the summary row's `last_survey_source`, which the server takes from the latest valid survey row, the row the history table lists first. A row with no source shows no word. |
| PI-031 | DONE | `[data-schema-counts]` line above the schema inventory (`threeNumberLine`): "N schemas · N tables · N columns · from <source> <when>". Columns are summed from the tables' own counts and the line says "in the tables measured (N tables' columns not measured)" when some table never had its columns measured. |
| PI-032 | DONE | Column rows gain Default and References columns with a header row (`tableHtml`). The server's tree column now carries `default` (`survey_definition_adapter.schema_inventory_tree`); an empty default reads "none recorded" because the table cannot tell no default from one not captured. |
| PI-036 | DONE | Ranked tables under the Tables chart: rows, size, last analyzed, pending changes, and a "Since last run" column from the diff the banner reads ("new", "in both runs", "no diff yet", "not comparable"). `table_sizes` now returns `ranked` (name, rows, size, last_analyzed, pending_changes, activity_state) from the same run's activity rows; a table with none is "not collected", never a zero. |
| PI-040 | DONE | "Views" section on Understanding: complexity, portability, joins/CTEs, "Depends on", SQL behind a disclosure, flowchart drawn on first open through `/api/diagrams/mermaid` (a second open does not ask again; a failure says why in its slot). New read-only route `GET /api/databases/{slug}/views` (states measured / not_measured / never_surveyed). The word "lineage" appears nowhere on screen or in the diagram source; edges say "reads from" and "depends on". |
| PI-041 | DONE (read only) | "Rules Egeria applies" inside the Curate term (glossary) section (`curate-bands.js` `rulesBodyHtml`). Only rules the route marks as read from Egeria are called Egeria's; the route's built-in fallback rules are shown apart as "not read from Egeria"; with none from Egeria, or a failed route, the block says "no reader yet". No control, no writer. |
| PI-049 | DONE | Repository Understanding opens with the same banner element as a database (`sinceLastRunBannerHtml`), reading `/api/egeria/{slug}/diff` (`repoSinceLastRunHtml`: file total and the file types that moved; the route keeps the ten largest and the sentence says so). |
| PI-042 | waits for G1's Egeria reports component | Not built here; no second annotation view. |
| PI-046 | waits for G1's Egeria reports component | Not built here. |
| PI-045 | retired with Classic | Prefect is the default; the engine shows after the run. Recorded only. |

## Questions and deviations for the architect

1. PI-036 "pending changes from the diff the banner uses": that diff (`/api/databases/{slug}/diff`)
   carries added and removed tables and count deltas, not per-table pending changes. Pending
   changes (PostgreSQL's modifications since the last analyze) live in `database_table_activity`,
   so the column reads there, and the diff feeds a separate "Since last run" column. If the owner
   meant only the diff, drop the "Pending changes" column.
2. PI-041: the data-class route (`/api/egeria/rules/dataclasses`) reads Egeria server side when
   configured and otherwise answers a hard-coded six-rule list marked "Local Fallback". The block
   refuses to call those Egeria's. Whether RE should keep a built-in list at all is the owner's call.
3. PI-037 order: the table sits as its own section under "Over time", not inside the trend card,
   because the grid card is too narrow for five columns.
4. Test suite note: `node_modules` was symlinked from the shared checkout for the harness and removed afterwards.
