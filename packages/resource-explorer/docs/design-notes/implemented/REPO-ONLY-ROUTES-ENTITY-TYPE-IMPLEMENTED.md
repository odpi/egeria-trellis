# IMPLEMENTED

## Environment confirmation
`uv sync --all-packages --extra dev` run in worktree; resource_explorer resolves to:
`/Users/dwolfson/localGit/egeria-v6/trellis-re-repo-only-routes/packages/resource-explorer/resource_explorer/__init__.py`
(inside worktree trellis-re-repo-only-routes)

## What changed
Routes in `resource_explorer/web/routes/projects.py` now take `entity_type` (query, default `repo` so the pre-sweep client keeps working):
- `GET /{slug}/analyses/{id}/trend`: resolves the slug as its own kind (404 names the kind), reads that kind's results map via `get_adapter(kind)`. Database/filesystem maps have no trend readers, so 400: "Trend history isn't built for databases yet; today it covers repository analyses."
- `GET /{slug}/members/{id}`, `.../children`, `POST .../promote`: 400 for database/filesystem with design's sentence ("Members aren't built for databases yet; today they list repository findings (advisories, dependencies, symbols, components)."), checked BEFORE 401/422/404. 400 not 404: the resource exists; the capability does not. Wording is "not built yet", not "repository-only" (design ruling: a database's tables are the member level).
- Unknown kind: 400. Notify-me/analyses-index were already kind-aware (not among the four).
- Frontend: `openMembers` (app.js) renders a 400 as the server sentence (`data-members-not-built`), not "could not be read". Trend already rendered 400 text; promote already shows `err.message`.

## Tests
- `tests/test_repo_only_routes_entity_type.py`: route x kind table (4 routes x db/fs = 8 honest-400 cases naming the kind; repo still resolves; 404s name the kind; anonymous database promote gets the sentence, not "sign in"; invalid body still 400; exact members sentence).
- `frontend-build/test-harness/repo-only-routes-honest-400.test.mjs`: routing-level (openMembers -> rail DOM); 500 still reads as a fault.
- Revert proof: with projects.py reverted to HEAD, 12 of the new Python tests fail; restored, all pass. With app.js reverted, the harness test fails (1 fail), restored passes. Full harness: 120 pass (Node 20.11; Node 18 lacks `module.register`).
