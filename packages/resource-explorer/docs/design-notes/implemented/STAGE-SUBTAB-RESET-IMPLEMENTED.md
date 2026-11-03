# Stage switch left Enrichment's Context pane on screen (implemented 2026-10-01)

## Root cause
`state.subTab` survives a stage click on purpose (a sub-tab chosen on one stage
persists where it exists on another). `loadPane()` in
`resource_explorer/web/static/next/app.js` tested `state.subTab === 'context'`
(`loadContextPane()`) with no stage gate, while `subTabsHtml()` hid Context
outside Enrichment via `SUB_TABS[].stages`. So Enrichment -> Context -> Discovery
rendered the Context pane under a tab bar with nothing highlighted. The same
shape existed for `schema_inventory` (`resourceTypes: ['db']`) after switching
to a non-database resource. The router and the tab bar kept two separate ideas
of validity.

## Fix
- `visibleSubTabs()` is the single list (resource type + stage filters),
  now used by `subTabsHtml()`.
- `reconcileSubTabForStage()`: a KNOWN sub-tab absent from that list reverts to
  the stage default ('context' on Enrichment, else 'questions'). Called in
  `loadPane()` right after the frame-stage branches (Understanding/Automate/
  Investigation still leave subTab alone, as documented) and before any subTab
  branch; `writeUrl()` runs when it changed, so the URL matches. All entry
  points (stage click, readUrl deep link, selection, goto-context/question)
  reach `loadPane()`, so one dispatch-time check covers them.
- Sub-tabs present on both stages (survey, by_analysis, disposition,
  schema_inventory on a database) still survive. Retired/unknown ids ('dashboard',
  strangers) are deliberately untouched: they keep their alias / "not a pane" handling.

## Evidence
`frontend-build/test-harness/stage-subtab-reset.test.mjs` (real app.js, real nav
and sub-tab clicks, Node 20.11): 19 tests, 19 pass with the fix; against
origin/main's app.js 11 fail (scouting, discovery, assessment, analysis, curate
Context lingers; frame -> discovery; deep link; schema_inventory on a repo;
the helper does not exist). Whole harness: 187 pass. Secondary pin:
`tests/test_next_stage_subtab_reset.py`.

## Not verified
A real browser render. `loadPane`/`readUrl` are not exported, so the deep link
is driven by setting the state readUrl() leaves and clicking the Discovery nav.
