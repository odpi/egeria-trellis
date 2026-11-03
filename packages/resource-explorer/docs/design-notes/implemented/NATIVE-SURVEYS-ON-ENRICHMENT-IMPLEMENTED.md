# Egeria's own surveys on the Enrichment Survey & analyses tab -- implemented

## Environment confirmation (recorded before any test run)

Worktree `/Users/dwolfson/localGit/egeria-v6/trellis-re-native-on-enrichment`, after
`uv sync --all-packages --extra dev`:

    resource_explorer.__file__ =
    /Users/dwolfson/localGit/egeria-v6/trellis-re-native-on-enrichment/packages/resource-explorer/resource_explorer/__init__.py

It resolves into this worktree.

## The bug

Survey & analyses is rendered by two functions in `web/static/next/app.js`:
`loadSurveyPane()` (every stage but Enrichment) and `loadEnrichmentAnalysesMapPane()`
(Enrichment only, routed in `loadPane`). The native-survey slice changed only the
first, so on Enrichment no `/api/native-surveys/...` request fired and the section
was invisible. Third feature hidden by a stage special case.

## The fix (design's ruling: the section renders on that tab on every stage, beneath the unlock map on Enrichment)

- New shared `fetchNativeSurveyRows(slug)` (database/filesystem only; null on
  other kinds or a failed read). `loadSurveyPane()` now calls it instead of its
  inline fetch; behaviour there is unchanged, including the fall-back to the
  informational block.
- `loadEnrichmentAnalysesMapPane()` calls it and renders
  `nativeSurveysSectionHtml` into a new `#enrichment-native-surveys` host placed
  after the map, then `bindNativeSurveys(host, slug, rows)` -- the same binding
  loadSurveyPane uses. A failed read leaves the section absent; it never blanks
  the map.
- Not done: the informational `egeria_native_processes` fall-back is not shown on
  Enrichment (that data comes from the candidates read Enrichment does not make).

## Regression test

`frontend-build/test-harness/native-surveys-every-stage-routing.test.mjs`: for
discovery, assessment, analysis and enrichment, sets the stage, CLICKS the
Survey & analyses strip button (`bindSubTabs` -> `loadPane` -> router), and asserts
the `/api/native-surveys` request fired and `#native-surveys` renders the row; plus
one test that on Enrichment the map precedes the native section.

Red/green: with the Enrichment call removed (rows forced to null), tests 4 and 5
(enrichment) FAIL and discovery/assessment/analysis pass; restored, all 5 pass.

Full harness: 78/78 (Node 20.11). `pytest -k "next or tailwind or native"`: 786 passed.

## Follow-up: a failed read is drawn, not dropped (design ruling)

"Simply absent" on failure was absence drawn as nothing. `fetchNativeSurveyRows`
now returns `{rows}` / `{rows: null}` (kind has none) / `{failed: true}`. On
failure BOTH panes render the section heading plus
"? couldn't read Egeria's surveys · re-check" (`nativeSurveysUnreadableHtml`,
re-check re-runs the pane). On Enrichment the map is untouched above it; on the
generic pane the candidates list AND the informational `egeria_native_processes`
block stay (previously the generic pane silently fell back to the informational
block only -- same silent-absence defect, fixed). Four more routing cases (one per
stage) force the read to 500 and assert the heading, the line, no invented rows,
and the map/candidates intact.

### Red/green proof for the failure cases

RED -- `fetchNativeSurveyRows`'s catch reverted to silent absence
(`catch { return { rows: null }; }`, no `failed: true`), node 20:

    ok 1..5 (the success cases)
    not ok 6 - routing: a FAILED native-survey read on discovery is drawn, and leaves the rest of the pane intact
    not ok 7 - ... on assessment ...
    not ok 8 - ... on analysis ...
    not ok 9 - ... on enrichment ...
    # pass 5
    # fail 4
    error: 'the unreadable section must be present'   (assert.ok on #native-surveys-unreadable)

GREEN -- failure handling restored (`failed: true`):

    ok 1..9
    # pass 9
    # fail 0

`git diff` was empty after the restore, so the committed code is the green one.
