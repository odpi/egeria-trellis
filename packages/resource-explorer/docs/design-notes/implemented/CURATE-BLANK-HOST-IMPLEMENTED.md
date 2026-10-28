# Curate blank-host fix — implemented

Branch `re/curate-blank-host`, from origin/main 78354bef.

## Root cause

- 18b3bb41 (E1, 2026-09-29) deleted `<div id="enrichment-form">` from the Questions pane markup in `static/next/app.js`.
- `renderCurate` (`stages/curate.js`, was line 209) did `const host = $('enrichment-form'); if (!host) return;`. The host was gone, so it returned silently for every resource kind and drew nothing.
- `loadPane` (`app.js` ~7047) calls `renderCurate(slug)` unawaited for `state.stage === 'curate'`, then sets `#question-rows` to `''` and the count to "review and commit · Curate". Result: a header and an empty body.
- The older tests (`test_next_curate_pane.py`, `test_next_analysis_subresources.py`) pinned source text, so they stayed green.

## Fix

- `stages/curate.js`: new exported `mountCurateHost()`; `renderCurate` now writes into `#curate-host`, created as a SIBLING after `#question-rows` (not a child), so `rows.innerHTML = ''` cannot wipe it. If `#question-rows` is missing it throws `Curate pane host missing: ...` instead of returning.
- `app.js` loadPane: `renderCurate(slug).catch(...)` writes "Curate could not be drawn: <reason>" into the pane, so a failure is visible text, never a blank body. Only that call line changed; `nonRepoCurateHtml` text and the `publish_stale` span are untouched.
- `stages/enrichment.js`: `renderEnrichment` now throws on a missing `#enrichment-form` instead of silently returning.
- `tests/test_next_curate_pane.py`: one source pin updated to the new call shape (`renderCurate(slug).catch(`); same intent (Curate renders before the empty-questions early return).

## Tests

- `frontend-build/test-harness/curate-pane-renders.test.mjs` (node:test + jsdom, real `app.js`, real router: clicks the real Curate nav button): db and filesystem show "Curate isn't available for ..." in `#curate-host`; repo draws the plan view; the host survives the rows blanking; known-negative: with `#question-rows` absent, `renderCurate` rejects with "Curate pane host missing".
- `tests/test_next_stage_host_ids.py`: every `$('id')`/`getElementById('id')` in `stages/*.js` must exist as `id="..."` markup or `.id =` somewhere under `next/`; known-negative for the checker; asserts the one allowlisted dead lookup stays dead.
- Node used: v20.11.0 (nvm); default node is 18.16.1.

## Evidence (red on origin/main source, green with the fix)

- Harness: with the three source files reverted to main, 5 of 5 fail (no `#curate-host`; known-negative "Missing expected rejection"). With the fix: 5/5 pass. Full `npm run test:harness`: 173/173 pass.
- Pin: on main, `test_every_stage_lookup_id_exists...` fails listing `curate.js enrichment-form`; passes with the fix.
- `pytest tests/test_next_*.py tests/test_curate*.py`: all pass (667 incl. new).
- `uv run python -c "import resource_explorer; print(resource_explorer.__file__)"` resolved to `.../trellis-re-curate-blank-host/packages/resource-explorer/resource_explorer/__init__.py`.

## Silent `if (!host) return` audit (all of static/next/stages/*.js)

| Site | Disposition |
|---|---|
| curate.js renderCurate | fixed: owns host, throws if frame missing, loadPane shows the failure |
| enrichment.js renderEnrichment (263) | converted to throw; it has no callers since E1 (dead) |
| enrichment.js renderEnrichmentForm (334) | left; reachable only from dead renderEnrichment. Allowlisted in the pin with a still-dead check |
| context.js renderContext (244), renderHumanQuestions (205) | left: callers are async and run after the user may have switched stage (subTab lingers), where a missing host is legitimate and a visible message would land in the wrong pane |
| curate.js renderComponentTree (548), renderBlueprintList (784) | left: same stale-async reason; their hosts are created by renderCurate's own draw |
| enrichment.js renderDocSources (613) / FromData (641), rail-evidence (315, 736) | left: polled/async, rail may be unmounted |
| activity.js 138, 227 | left: panel can be closed while polling |
| analysis.js 62, 302; native-surveys.js 147, 170; curate.js 91, 343, 633, 868 | left: caller-supplied or optional inner node |

Rule applied: a required-host stage entry point throws/shows text; an async continuation that can outlive its pane keeps the guard (showing a message there would paint over whatever pane replaced it).

## Missing-id table

| Module | Id | Defined in |
|---|---|---|
| curate.js | enrichment-form | MISSING (fixed: no longer looked up) |
| enrichment.js | enrichment-form | MISSING (dead code, allowlisted) |
| enrichment.js | rail-evidence | chat.js |
| enrichment.js | doc-sources-block | context.js |
| enrichment.js | doc-source-add/-status/-label/-type/-url | enrichment.js |
| context.js | context-form | app.js |
| context.js | context-human-questions | context.js |
| curate.js | blueprint-list, blueprint-status, component-diagram, component-tree, component-tree-status | curate.js |
| activity.js | activity-panel-body, -controls | activity.js |
| investigation.js | inv-edit-form, inv-egeria-form, inv-reclass-form | investigation.js |
| understanding.js | chart-body, chart-index | understanding.js |
| automate/investigation/understanding.js | content | index.html |

Only the two `enrichment-form` rows were missing; everything else exists.

## Not verified

- No real browser render: only jsdom with stubbed `fetch`. The repo plan view was exercised with a minimal empty plan, not real data.
- Existing sub-tab/loader pins were not read for similar stale assumptions beyond the pytest run listed above.
- Does not include re/false-capability-claims (413014ed); it touches different lines of app.js/curate.js and should merge cleanly either way.
