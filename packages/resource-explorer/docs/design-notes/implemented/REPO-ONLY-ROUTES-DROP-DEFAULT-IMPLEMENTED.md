# REPO-ONLY-ROUTES-DROP-DEFAULT — implemented

## Environment confirmation
`uv sync --all-packages --extra dev` run in worktree `trellis-re-drop-default` (branch
`re/repo-only-routes-drop-default`, off origin/main 99f047cf); resource_explorer resolves to:
`/Users/dwolfson/localGit/egeria-v6/trellis-re-drop-default/packages/resource-explorer/resource_explorer/__init__.py`
Signed test commit verified `G` and dropped before any code change.

## What changed
`resource_explorer/web/routes/projects.py`: `entity_type` is now a REQUIRED query parameter
(`Query(...)`, shared `_REQUIRED_KIND`) on `/analyses/{id}/trend`, `/members/{id}`,
`/members/{id}/children`, `POST /members/{id}/promote` and `/analyses-index` (analyses-index did
carry `= "repo"`, so it was changed). Omitting it is FastAPI's native 422 with
`loc: ["query", "entity_type"]`, "Field required" — the same body shape as every other validation
422 in this app. No client change: every helper in `static/re-api.js` already sends the kind via
`requireKind(...)` (#382); verified by reading each of the five URLs.

Not changed (flagged): `/{slug}/analyses/{id}/measurements` and `answer_question()` in
`routes/analyses.py` still default `entity_type` to "repo"; same bug class, out of this slice's list.

Part 2: `IMPLEMENTED.md` (package root) moved with `git mv` to
`REPO-ONLY-ROUTES-ENTITY-TYPE-IMPLEMENTED.md`.

## Tests
`tests/test_repo_only_routes_entity_type.py`: route x kind tables — missing kind -> 422 naming
`entity_type` (5 routes x 3 kinds); with kind, repo still works; database/filesystem still honest 400
on the four repo-built routes; analyses-index resolves for database/filesystem.
`tests/test_analysis_results_routes.py`, `tests/test_promotion.py`: pre-existing calls now pass `?entity_type=repo`.
Subset run (every test file touching /api/projects/): 628 passed, 1 skipped. Frontend untouched, harness not rerun (no JS changed).
Revert proof: with projects.py at HEAD, 15 new tests fail (34 pass); restored: 49 pass, 1 skip.

## Walk-ready: #383's unwalked gate item
"Trend read on a database work list is either real or one honest sentence, never 'not found.'"
Still intact: trend with `entity_type=database` (and filesystem) still returns 400 "Trend history
isn't built for databases yet; today it covers repository analyses." (asserted by
`test_non_repo_kind_gets_honest_400_naming_the_kind[trend-*]`, and 404-for-unknown-slug names the
kind). Only the missing-parameter case changed, to 422.

## Fix commit: classic UI trend calls (found in PR review)
`resource_explorer/web/static/index.html` called `/api/projects/{slug}/analyses/{id}/trend` WITHOUT
`entity_type` in three places; with the default dropped each would 422 and the classic trend chart
would silently go empty. Now:
- ~3399 `loadAnalysisResults` and ~14011 `_toggleInlineResults`: `?entity_type=` + `encodeURIComponent('repo')`.
  Repo is correct, not a guess: both sit beside the `/results` call (repo-only route; databases/filesystems
  use `/sub-resources/.../results`), every caller is gated on `entityType === 'repo'` / `_REPO_RESULTS_RENDER_MODE`
  (the Results button, `_openAnalysisResults`, chat `_inlineResultsToggleHtml`), and the code comment says "repo-only".
- ~8883 `_loadDashboardTrendCharts`: now takes the `entityType` that `renderSurveyResultsPanel` already has in
  scope and sends `encodeURIComponent(entityType)` (new third parameter; one caller).
Other four routes (members/children/promote/analyses-index under `/api/projects/`): classic UI never calls them.
(`/api/investigations/.../members` and `/promote` are the investigations router, untouched.)
New test `tests/test_classic_ui_sends_entity_type.py` (source-text over index.html; scans every `/api/projects/...`
URL literal matching the five routes, requires `entity_type=`; plus a guard that >=3 trend calls are found).
Revert proof: removing it from the dashboard call fails; removing it from both repo calls fails; restored: passes.

### Follow-up scouting (NOT fixed here): `measurements` and `answer_question` in index.html
- `/analyses/{id}/measurements`: NO classic-UI callers (only prose mentions of "measurements").
- `answer_question` (`GET /api/analyses/facts/{slug}/answer`): TWO callers, both WITHOUT entity_type:
  `_answerQuestionInChat` (~11715) and `_chatReplaceAnswer` (~11740). Dropping that route's default would 422 the
  classic chat; those two need the kind (slug's resource kind from chat context) added in the same dispatch.
