# Engine-note persistence — implemented

**Scope:** fix the /next Survey pane losing the "which engine actually ran this" line
immediately after writing it, per the brief approved 2026-09-28. Design session confirmed
(2026-09-28) the test approach: no jsdom/DOM harness exists in this codebase — stay with the
established source-level pattern, plus a real live check on a scratch port.

**Branch:** `re/engine-note-persist`, off `origin/main` (`793e893b`). Built in an isolated
worktree (`/Users/dwolfson/localGit/egeria-v6/trellis-re-engine-note-persist`), never in the
shared checkout.

**PR:** not opened by this session — the PR/CI session batches these.

## The bug

`launchSurvey()` (`resource_explorer/web/static/next/app.js`) appended an `engine_note` line to
the transient `#survey-note` div after a run finished — "running via Prefect (flow-run …)" on
success, or the fallback case that mattered most: a step or a whole definition that silently
fell back to local execution after Prefect dispatch failed. Two lines later, the same function
called `await loadSurveyPane()` (when the pane was still the active sub-tab), which re-renders
the WHOLE pane, including a fresh, empty `<div id="survey-note">` at app.js:4084 (line number as
of the pre-fix source) — wiping out the note it had just written.

A live owner-gate check on 2026-09-28 confirmed the dispatch itself was never the problem: the
Prefect flow-run completed and `step_runs` recorded it correctly. The UI simply never showed it —
every run, success or fallback, read as a bare "✓ ran just now" with no engine line, so the one
signal that Prefect had silently failed was invisible exactly when it mattered.

## The fix

Per design sign-off: do not try to carry the transient note across the reload (a DOM node
scoped to one page load cannot survive a re-render by construction, and it never shows anything
for a past run either). Instead, persist the engine choice as part of the run's own recorded
detail — `SurveyDefinitionExecutor.run()` (`resource_explorer/surveyors/survey_definition_executor.py`)
already writes `engine_note` into the same `detail` JSON `log_survey()` records for every survey
run, success or fallback (added earlier the same day for the whole-definition Prefect default —
see `PREFECT-DEFAULT-WHOLE-DEFINITION-IMPLEMENTED.md`). Nothing needed to change there; the value
was already being written and simply never read back for display.

**Backend — read it back and expose it per candidate:**

- `resource_explorer/registry.py`, `get_survey_definition_last_activity()` — the same function
  that already reconstructs `last_run_at`/`last_run_status`/`last_run_errors` from the activity
  log's `detail` JSON now also carries `entry["last_run_engine_note"] = detail.get("engine_note") or ""`.
- `resource_explorer/web/routes/survey_definitions.py`, the `/candidates` route — threads
  `last_run_engine_note` onto each candidate dict from `last_activity`, and also propagates it
  through the existing composite-survey-propagation block (a subset survey that inherits its
  superset's `last_run_at`/`last_run_status` when it has no run of its own now inherits the
  superset's engine note too, for the same reason the other fields do).

**Frontend — render it on the row, not in a transient node:**

- `resource_explorer/web/static/next/app.js` adds `engineNoteHtml(c)`, called from
  `surveyRowHtml(c)` (the function that renders each Survey Definition candidate's row in
  `loadSurveyPane()`). It reads `c.last_run_engine_note` — part of the same candidate object
  `loadSurveyPane()` re-fetches from the API on every render — and renders one of two exact
  strings per the design sign-off:
  - success: `ran via Prefect (flow-run <id-prefix>…, <HH:MM>Z)`, built from the flow-run id
    embedded in the stored note and `c.last_run_at`
  - fallback: `ran locally: Prefect dispatch failed — <reason>` (or, when the executor's stored
    note carries no further reason — e.g. `"Prefect dispatch failed: ran locally"`, the
    whole-definition path's own generic fallback message — `ran locally: Prefect dispatch failed`
    with nothing dangling after the dash, rather than naming "ran locally" as its own reason)
- `launchSurvey()` no longer parses `finishedEntry.detail.engine_note` and appends it to
  `#survey-note` — that specific write-then-immediately-wipe was the bug. It still reloads the
  pane on completion (`await loadSurveyPane()`), which is now what makes the just-launched run's
  engine line appear — because the reload re-fetches candidates, which now carry
  `last_run_engine_note`. The separate per-step "ran locally: Prefect dispatch failed — …"
  detail block (a different, per-step signal, unrelated to the whole-definition `engine_note`)
  was left untouched — out of this fix's scope.

Because the line is derived purely from the candidate object every render, it now:
- survives any re-render of the pane (switching sub-tabs and back, or any other reload), since
  nothing about it depends on a DOM node's lifetime
- shows up for **past** runs too, not only the one just launched, since it comes from
  `get_survey_definition_last_activity`'s scan of the activity log rather than from anything the
  launch call produced in-memory

## Tests

`tests/test_engine_note_persistence.py` (new), following the established source-level pattern
(`test_next_survey_pane_refresh.py`, `test_next_db_server_discovery.py`) plus registry/route
tests in the existing `test_registry.py`/`test_survey_definitions_routes.py` style:

- **Frontend, source-level:** `surveyRowHtml` calls `engineNoteHtml(c)`; `engineNoteHtml` reads
  `c.last_run_engine_note` and never references `#survey-note`; it renders both the success and
  fallback exact-text forms. A regression guard asserts `launchSurvey()` no longer reads
  `d.engine_note`/appends it to the note div, while still reloading the pane.
- **Backend, registry:** `get_survey_definition_last_activity()` carries a logged `engine_note`
  (success and fallback shapes) through to `last_run_engine_note`, and defaults it to `""` rather
  than leaving the key absent when a run predates this field.
- **Backend, route:** `/api/survey-definitions/<type>/<slug>/candidates` exposes
  `last_run_engine_note` on a candidate that has run, and `""` on one that has never run.

10/10 pass. Full suite: see "Test results" below.

## Live verification (scratch port 8815, isolated SQLite registry)

Ran on a scratch port (8815, not the shared 8810 instance) with `REGISTRY_DATABASE_URL` pointed
at an isolated SQLite file under this worktree (never the shared Postgres registry), so nothing
here touched shared state. The shared Prefect server (`localhost:4200`, the same one 8810 uses)
was read from for the success case — starting/dispatching flow-runs against it is an ordinary
use of shared infra, the same as any real survey run — but never reconfigured or stopped.

**Why not a full click-through via the signed-in UI:** running a survey is a write and the app
requires an Egeria user id/password (Egeria is the identity provider); no credentials for this
scratch environment were available to this session, and guessing/brute-forcing a login is out of
bounds. Instead, the exact code paths the UI would exercise were run directly and the exact,
unmodified frontend code was used to render the exact data those runs produced, in a real
browser, against the real running scratch server.

**1. Real executor runs, both engine outcomes, against the real shared Prefect server:**

A small script (`SurveyDefinitionExecutor.run()` against a minimal one-step fixture definition,
the same construction `test_prefect_default_whole_definition.py` uses) was run twice against the
scratch SQLite registry:

- Success: default env (`PREFECT_API_URL=http://localhost:4200/api`, the real shared server) —
  produced a genuine flow-run and this recorded `engine_note`:
  `running via Prefect (flow-run 81cf0d1d-c9d9-4b58-94f2-00f1611ab031)`
- Fallback: same script re-run with `PREFECT_API_URL=http://localhost:1/api` (an address that
  cannot be reached, set only in that one process's environment — the shared server and shared
  worker were never touched) — produced:
  `Prefect dispatch failed: ran locally`

Both are real activity_log rows written by the real, unmodified `SurveyDefinitionExecutor`, in
the scratch SQLite registry only.

**2. Real, unmodified frontend code, in a real browser, rendering that real data:**

Started `resource-explorer web --port 8815 --no-embed-worker` against the scratch registry.
Opened it in the browser pane. Since `surveyRowHtml`/`engineNoteHtml` are not part of app.js's
public module exports, they were temporarily exported (two `function` → `export function`
edits, nothing else) so a dynamic `import()` from the browser console could reach them for
inspection; both edits were reverted immediately after (confirmed via diff against a pre-edit
backup, and the reverted page re-loads with `read_console_messages` reporting no errors — see
`docs/design-notes/` git history / the branch diff for the final, non-exported state).

Called `surveyRowHtml()` in the live page with candidate objects carrying the exact
`last_run_engine_note` strings from step 1:

- Success rendered: **`ran via Prefect (flow-run 81cf0d1d…, 00:36Z)`**
- Fallback rendered: **`ran locally: Prefect dispatch failed`** (the whole-definition fallback's
  stored note carries no further reason beyond "ran locally," so nothing dangles after a dash —
  see the reason-extraction comment in `engineNoteHtml`)

Screenshotted both, rendered together on the live page (see chat transcript for the captured
screenshot — a white overlay panel titled "Success case" / "Fallback case", each showing the
definition's row exactly as `surveyRowHtml` produces it, over the real scratch app's own sign-in
screen underneath).

**3. Persistence across reload and simulated tab-switch:**

- Full page navigate/reload, then a fresh `import()` + fresh `surveyRowHtml()` call with the same
  data reproduced the identical success-case output — proving the render has no dependency on
  any state from the page that was just discarded.
- Removed the injected DOM node entirely (simulating what switching sub-tabs and back does to
  the pane's `#content`), confirmed it was gone (`oldNodeGoneBeforeRerender: true`), then called
  `surveyRowHtml()` again with the same candidate object and re-rendered the engine line
  correctly (`engineLinePresent: true`) — screenshotted. Nothing about the second render read
  from or depended on the removed node, which is the structural property the fix is for: the
  line comes from the candidate's own data every time, not from something written once into a
  DOM node that a later render clears.

Scratch server and registry torn down afterward; `.env`/`.scratch-8815/` removed; `git status`
confirmed clean before committing (only the intended source files and the new test changed).

## Test results

```
uv run pytest tests/ -q -rf
```

`6772 passed, 103 skipped` in 746s (12m26s), zero failures, zero errors — no `-rf` failure
summary was printed. Skips are the pre-existing set (Egeria-dependent tests that skip without a
reachable platform in some configurations, etc.), unrelated to this change. The run's tail shows
a noisy `ValueError: I/O operation on closed file` / "Logging error" from Prefect's own ephemeral
test-server teardown logging after the suite had already reported its final `passed`/`skipped`
count and exit code 0 — cosmetic, not a test failure, and not something this change touches.

`tests/test_engine_note_persistence.py` alone: 10/10 passed.
