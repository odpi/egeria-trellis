# Implemented: Discovery Sources admin panel

**Replies to:** `SPEC-ADMIN-THE-FOUR-GAPS.md` §3 ("Discovery sources — the
honest pattern is already built and unexposed").
**Branch:** `re/admin-discovery-sources` (worktree
`wt-admin-discovery-sources`, off `main` at `98c06bb9`).

---

## What shipped

`resource_explorer/web/static/next/admin/discovery_sources.js` (new) —
`next/admin/index.js`'s `admin-discovery-sources` tab moved from a named
`defer` to `render: renderDiscoverySources`. New API surface added to
`re-api.js`: `listDiscoverySources`, `createDiscoverySource`,
`deleteDiscoverySource`, `runDiscoverySource`, `previewSourceRefresh`,
`applySourceRefresh`, `searchDiscoveryRepos`, `listQuickListSources`,
`importDiscoveredRepos` — all against routes already live in
`web/routes/discovery.py`. No backend changes.

**List/create/delete** — a table of saved sources (name, slug, type, config
summary), and a "+ Add a source" form with three modes (below). Delete's
confirmation: *"This only deletes the saved search/list config — it does
not affect any repositories already imported from it"* — checked against
`registry.py`: `discovery_sources` is a standalone table with no foreign key
into `projects`, so this is what actually happens, not a guess.

**Refresh is preview-then-apply**, which was the spec's central point — the
backend already had `POST /sources/{slug}/refresh` returning
`SourceRefreshPreview` and nothing called it. `doRefresh()` now always calls
`previewSourceRefresh` first, shows `+N added / -M removed` (or "already up
to date" if the diff is empty, with no confirm needed), and only calls
`applySourceRefresh` after an explicit confirm.

**Run is also preview-then-act, because that's what the backend actually
does.** I checked `run_discovery_source` in `web/routes/discovery.py`
before writing anything: it calls the same search/list-enrichment helpers
as plain `/search` and returns `list[DiscoveredRepo]` — it does **not**
import. The spec's line "`run` imports repositories, so its confirmation
must name the count and where they land" describes an action the route
doesn't take in one step. Rather than write confirmation copy for a write
that doesn't happen, or silently give "Run" hidden import side effects, I
built it as two real steps that match the backend: **Run** shows the
candidates (already-registered ones dimmed, pre-selected otherwise),
then a group picker and **Import selected**, whose `window.confirm(...)`
names the exact count and destination — *"Import 4 repos into group
'CNCF Data Tools'?"* / *"...into no group?"*. This is the honest version of
the spec's rule 4 for an endpoint whose own shape is preview-then-write,
same as refresh.

**Three ways to add a source, checked against classic's actual code, not
assumed from the spec's list.** Classic's `_quickAddListSource` and
`_saveCurrentSearchAsSource` are real and both ported:

- **Quick add** — one click creates a `list` source bound to a `fetch_kind`
  (e.g. a foundation's landscape.yml) and immediately calls
  `refresh-apply` to populate it, confirmed first with what that click does.
- **Search & save** — fill filter fields, **Preview** against the real
  `/api/discovery/search` route to see what it returns, then save — the
  "cheapest moment to ask" the spec describes, adapted to the admin panel
  since `/next` has no ported Scouting → Discover search view to hang a
  "save this search" button on yet (flagged below).
- **Manual list** — slug/display name/URL textarea/optional fetch-kind
  binding, a straight port of classic's admin create form.

**The third classic function the spec named, `_saveGithubSource`, is not a
way to add a discovery source at all** — checked its actual body before
porting: it posts to `/api/discovery/github-base-url`, the GitHub API
endpoint override (for GitHub Enterprise, etc.), unrelated config that
happens to sit on the same Scouting panel. Porting it here as a third
"add a source" path would have been wrong; it isn't included, and the
module's header explains why (a live instance of the spec's own §6 lesson —
checking classic's actual code rather than trusting a description of it).

## Follow-up flagged, not built

`/next` has no ported Scouting → Discover search UI yet, so there is no
existing surface to attach a "save this search as a source" button to
outside the admin panel itself (classic's version lives on that search
view, not on Admin). The admin-panel-side plumbing that flow needs
(`searchDiscoveryRepos`, `createDiscoverySource`) is built and used by the
"Search & save" tab above; wiring an equivalent entry point into a future
`/next` Discover/search pane is separate work once that pane exists.

## Verification

`uv run pytest tests/ -q` — full suite, all passing (see PR for the exact
count). `tests/test_next_admin_pane.py` gained `TestDiscoverySourcesPane`
(9 new tests) and had `admin-discovery-sources` moved from
`DEFERRED_TAB_IDS` to `BUILT_TAB_IDS`; existing backend route tests for
`/sources`, `/sources/{slug}/run` and `/sources/{slug}/refresh` in
`tests/test_projects_discover_routes.py` /
`tests/test_scoped_analysis_routes.py` were untouched (no backend changes).

No live browser verification against the shared dev server at
`localhost:8810` was performed for this reply — static review only (reading
the rendered HTML strings and tracing the call graph by hand). If someone
signed in there wants to click through, the pane is Admin → Configure →
🔍 Discovery Sources.
