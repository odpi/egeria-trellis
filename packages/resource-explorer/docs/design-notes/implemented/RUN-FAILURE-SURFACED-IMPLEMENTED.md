# Run failure surfaced (parity inventory D-43, per-analysis run)

Implemented 2026-10-01 on `re/run-failure-surfaced`.

Environment proof (before any test):
`resource_explorer/__init__.py` resolved to
`/Users/dwolfson/localGit/egeria-v6/trellis-re-run-failure-surfaced/packages/resource-explorer/resource_explorer/__init__.py`.

## Defect

`pollActivity` (`web/static/re-api.js`) resolves with the finished activity row
whatever status it ended on. Both per-analysis callers in `web/static/next/app.js`
awaited it and threw the return value away:

- the Survey & analyses row's run button (`data-analysis-run` handler): the
  section was redrawn and showed only a glyph from `last_run_status`;
- Questions `rerun()`: fell through to `loadAnswer`, showing the previous answer.

Only a failure to start produced text.

## Backend record

`execute_and_record_analysis` / `execute_and_record_database_analysis`
(`workflows/analysis.py`) write the terminal row: `status='error'`, `summary`
(result summary or error), `detail` JSON string carrying `error` (the reason).
`get_activity` returns `detail` as the raw string. So the reason is persisted;
no backend gap for these two writers. A run reconciled as orphaned gets status
`interrupted` with a summary saying completion is not known; it is deliberately
not treated as a failure here.

## Fix

- `activityFailure(entry)` / `failureText(f)` in `re-api.js`: failure derives
  from the row's own `status` (`error`/`failed`); reason is `detail.error`, else
  `summary`, else the text states "no reason recorded" (nothing invented).
- Row button: stores the text in `state.analysisRunFailures` and
  `analysisIndexRowHtml` renders it in a `data-analysis-run-failed` span that
  survives the redraw; cleared only when that analysis is run again (no timer).
- `rerun()`: a failed finished row sets the question's `__error` ("The run
  failed: <reason>") instead of re-reading the old answer.

## Evidence

`tests/test_next_run_failure_surfaced.py`: 9 failed on the unfixed source, pass
after. Known negative: success is `null` from the helper and both callers act
only when non-null. Helper also exercised under Node 20.11: error+detail ->
reason 'boom'; error with nothing -> "Failed — no reason recorded"; ok -> null.
`node --check` (Node 20.11.0, default was 18.16.1; files copied to .mjs because
they are ES modules) passed on both. 722 tests pass across `test_next_*.py`,
`test_static_js_syntax.py`, `test_re_api_entity_type_threading.py`.

## Not verified

No live browser check with a signed-in session; a real failing analysis was not
run. The `interrupted` status is left as is. The Survey Definition launch path
(`launchSurvey`) is a separate D-43 half and is untouched.
