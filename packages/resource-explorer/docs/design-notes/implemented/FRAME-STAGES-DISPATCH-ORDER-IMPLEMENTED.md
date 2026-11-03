# Frame-class stages route before subTab branches — IMPLEMENTED

Branch `re/frame-stages-dispatch-order`, off origin/main 01965c5e (includes E1/E2/E3 and the native-survey work).

## Environment confirmation (done before any test run)

`uv sync --all-packages --extra dev` in the worktree, then:

    resource_explorer.__file__ =
    /Users/dwolfson/localGit/egeria-v6/trellis-re-frame-dispatch/packages/resource-explorer/resource_explorer/__init__.py

It resolves INTO this worktree, not the shared checkout.

## The bug

`loadPane()` (app.js) is an if-chain. The stage-click handler leaves `state.subTab` alone by design. The
subTab branches (schema_inventory, context, survey, by_analysis, disposition) returned before the
frame-class checks (`understanding`, `automate`, `investigation`) were reached, so Enrichment (default subTab
`context`) -> click Investigation/Understanding/Automate rendered Context again.

## The fix

Only the check order changed: the three frame-stage blocks moved, verbatim, to just below the work-list
branches and above `schema_inventory`. `state.subTab` is not touched anywhere.

Work-list precedence: left first, unchanged. Work lists take a `stage` and replace the pane for any stage;
a stage click does not clear `workListSlug`, so a frame-stage click while a work list is open still shows the
work list. That is pre-existing behaviour and not part of this bug; not changed here (flagged, not decided).

`renderIntentNav` gained an `export` keyword (visibility only, same pattern as the harness's other exports) so
the test can click the real nav buttons.

## Test: frontend-build/test-harness/frame-stage-dispatch-order.test.mjs (9 cases)

Clicks real `#intent-nav button[data-stage]` buttons: Enrichment -> each of Investigation / Understanding /
Automate; stale subTab (schema_inventory, survey, by_analysis, disposition) -> Investigation; and the reverse,
Investigation -> Enrichment lands on Context.

### Red (fix reverted, `renderIntentNav` export kept)

    # tests 9 / # pass 2 / # fail 7
    ok     1 Enrichment lands on Context by default (sanity)
    not ok 2-4 Enrichment (Context) -> investigation / understanding / automate
    not ok 5-8 stale subTab schema_inventory / survey / by_analysis / disposition -> Investigation
    ok     9 REVERSE Investigation -> Enrichment lands on Context

Cases 1 and 9 pass on the old code by design: they guard the sanity precondition and the reverse direction,
which the old order handled correctly.

### Green (fix restored)

    # tests 9 / # pass 9 / # fail 0

### Full runs on the fix

- Node 20.11.0, `npm run test:harness`: tests 127, pass 127, fail 0.
- Python, `pytest tests -k "next or tailwind or render_modes"`: 701 passed, 0 failed.

## Backlog (NOT built here)

`loadPane` is an if-chain where order decides what renders, and every new tab adds a branch ahead of the
stages. This is the fourth time this week a stage-routing special-case has caused a bug (E1's doc-sources
mount, the native-survey Enrichment routing fix, and now this). Follow-up, NOT for this fix: replace the
if-chain with a dispatch table keyed by (stage, subTab), with one test that walks every stage x subTab
combination and asserts a pane renders, so a new branch can't silently shadow an old one.
