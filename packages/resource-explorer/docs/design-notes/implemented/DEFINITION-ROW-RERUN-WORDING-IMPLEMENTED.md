# Definition-row "re-run" wording — implemented

**Scope:** small, well-specified wording fix logged in `Backlog.md` 2026-09-27 (owner found it
checking 8810), dispatched 2026-09-29. Branch `re/definition-row-rerun-wording`, off
`origin/main` (`8c6f1845`). Built in an isolated worktree
(`/Users/dwolfson/localGit/egeria-v6/trellis-re-rerun-wording`), never in the shared checkout.

## The bug

On the Survey & analyses pane, the SURVEY DEFINITION row's own run button always read "Run",
even after a run had already happened for that definition on the current resource. The
analyses list directly below it already got this right — it shows "re-run" once an analysis
has a prior run. The definition row's own button just never applied the same rule: it emitted
the literal string `Run →` unconditionally, in `surveyRowHtml(c)`
(`resource_explorer/web/static/next/app.js`), even though the very same row already renders
`c.last_run_at` elsewhere (via `lastRunHtml(c)`, the "✓ ran 3 hours ago" text, and
`engineNoteHtml(c)`) — the data was already on the object and already in use on that row, just
not consulted for the button label.

## The reference rule (reused, not reinvented)

The analyses list's own row renderer, `analysisIndexRowHtml(row)`, already has the correct
rule:

```js
>${row.last_run_at ? 're-run' : 'run'} →</button>
```

`row.last_run_at` is populated per-analysis, scoped to the current definition and resource, by
the backend's existing "has this run before" lookup (the same `last_run_at`/`last_run_status`
reconstruction from the activity log that `registry.get_survey_definition_last_activity()`
already does for the definition row itself — see `ENGINE-NOTE-PERSISTENCE-IMPLEMENTED.md` for
that lookup's own history). No new query was needed: the definition row's `c` object already
carries `last_run_at` from that same lookup; it just wasn't read by the button.

## The fix

One-line change to `surveyRowHtml(c)`, applying the identical ternary the analyses list uses,
against the same field:

```diff
- >Run →</button>
+ >${c.last_run_at ? 're-run' : 'run'} →</button>
```

`surveyRowHtml` is the single render function for survey-definition rows on **both** the
Scouting and Discovery panes — both reach it through the same `loadSurveyPane()` →
`byTier.get(t).map(surveyRowHtml)` path in `app.js`; which pane is showing is decided only by
which `stage`/`phase` was fetched, not by a different render function per pane. So this one
change, in one function, covers both panes — there is no separate per-pane rendering path to
duplicate the fix into.

## Tests

Added `frontend-build/test-harness/definition-row-rerun-wording.test.mjs`, following the
existing real-DOM harness pattern (`dom-harness.mjs`, jsdom, the real unmodified `app.js` module
graph):

1. No prior run (`last_run_at: ''`) → button renders `run →`.
2. A prior run (`last_run_at` set, `last_run_status: 'ok'`) → button renders `re-run →`.
3. The wording survives a second render of the same row (a pane switch / reload), matching the
   pattern `engine-note-persistence.test.mjs` already established for this same row.

Since both panes share `surveyRowHtml`, one set of tests against that function covers both —
no separate Scouting/Discovery test was needed.

**Harness suite:** `cd frontend-build && npm run test:harness` (node v20.11.0 via nvm) — full
suite green, 20/20 passing (17 pre-existing + 3 new).

**Python suite:** `uv sync --all-packages --extra dev && uv run pytest tests/ -q -rf` from
`packages/resource-explorer/` (the repo-root `tests/` path doesn't exist — tests live per
package). See commit message / PR for the pass/fail summary from this run.

## Judgment calls

- Matched the reference implementation's exact casing (`re-run`/`run`, both lowercase) rather
  than keeping the definition row's previous capitalized `Run` — the ask was to reuse the
  analyses list's exact rule, and that includes its wording, not just its condition.
- No backend change was needed or made — `c.last_run_at` was already present and already
  correct on the definition-row candidate object (used by `lastRunHtml`/`engineNoteHtml`
  already); the bug was purely that the button's own template didn't read it.
