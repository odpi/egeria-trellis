# Discovery's corpus-level import/search/export — implemented

**Replies to:** a survey of the `/next` UI finding Discovery the thinnest of
the eight canonical stages — `stages/discovery.js` was 25 lines wiring the
shared Questions engine to 10 catalogued `intent: discovery` questions and
one resource-scoped write (`POST /api/discovery/disposition`), with ZERO
fetch calls for classic's (`index.html`) corpus-level Discovery actions:
GitHub org/repo search, the `/from-list` bulk loader, and the
`inventory.csv` export.

**Branch:** `re/next-discovery-import-search`, worktree
`.claude/worktrees/wt-next-discovery`. Nothing was written in the main
checkout.

---

## Where this landed, and why it is not in `stages/discovery.js`

Classic's three affordances map to four `discovery.py` endpoints
(`/search`, `/from-list`, `/inventory.csv`, and `_expand_org` as an
internal helper `/from-list` calls — never its own route). Before writing
any UI, this item re-read `web/routes/discovery.py` end to end and
`index.html`'s `searchScoutRepos()` / `_loadRepoListFromFile()` /
`_downloadInventory()` / `_saveCurrentSearchAsSource()`.

**Org import is not a third, separate flow.** `RepoSearchRequest.org` is a
plain qualifier field alongside keyword/language/license/topic — the old
org-only discovery flow (`GitHubClient.list_org_repos()`) was retired and
folded into general search. The only place an account gets expanded into
its member repos is server-side, inside `/from-list`, when a pasted/loaded
row's address is a bare GitHub account URL (`github_url_context.py`'s
`github_org_from_url`) rather than a repo URL. So this item built two UI
surfaces (search, from-list), not three, matching what the backend
actually has.

**Placement:** `app.js` and `SPEC-ACTIONABLE-AND-HONEST.md` point 2 already
carry a recorded project-owner ruling (2026-09-15) that finding/importing
repos is corpus-level work — the same action on Scouting as on Curate —
and that it does not belong inside any one stage's sub-tab strip. That
ruling is why `Find repos` already lived beside the sidebar's
`Repos · DBs · FS` switcher as a `find-repos` action, previously a stub
that only linked out to classic. Building the real thing *inside*
`stages/discovery.js` would have repeated exactly the mistake that ruling
corrected — Discovery has no more claim to "find repos" than Scouting or
Curate do. So this item built a new standalone module,
`next/discovery-import.js` (chrome-level, like `worklist.js`/`rfa.js`, not
a stage module), and pointed the existing `find-repos` action at it for
`state.resourceType === 'repo'`. Databases/filesystems keep the old
stub — their own discover/register flows are a separate, not-yet-ported
affordance and out of this item's scope.

`stages/discovery.js` itself is unchanged in shape: still `export {};`,
still reached only by the generic Questions-checklist engine for its 10
catalogued questions and the disposition sub-tab. Its header comment was
updated to point at the new module instead of claiming the four endpoints
are simply deferred.

## What was built

**`resource_explorer/web/static/re-api.js`** — two new wrappers beside the
two that already existed (`searchDiscoveryRepos`, `importDiscoveredRepos`):

- `discoverFromList(text)` → `POST /api/discovery/from-list`.
- `fetchInventoryCsv()` → `GET /api/discovery/inventory.csv`, returning
  `{ text, filename }`. Deliberately checks `res.ok` and parses a JSON
  `detail` before returning anything — classic's own `_downloadInventory`
  comment (`index.html:16082`) records that a plain `<a download>` used to
  silently save a 404's `{"detail":"Not Found"}` body as a `.csv`-shaped
  file that looked like a working export. The Blob/anchor/click sequence
  itself stays in the caller (`discovery-import.js`), per this file's own
  "nothing here touches the DOM" rule — `fetchInventoryCsv()` only does the
  response-shape work a DOM-free module can still own.

**`resource_explorer/web/static/next/worklist.js`** — `openDialog(title,
sub, { wide = false })`: an optional third argument widening the shared
dialog shell from `640px` to `960px` for content that is a table, not a
paragraph. Every existing two-argument call site is unaffected.

**`resource_explorer/web/static/next/discovery-import.js`** (new) — the
dialog `find-repos` now opens for repos:

- **Search** — a form with exactly `RepoSearchRequest`'s fields (keyword,
  min stars, language, license, pushed-after, org, topic, include
  archived/forks), the same "at least one filter" guard classic enforces,
  and GitHub topic lowercasing/hyphenation ported verbatim.
- **From a list** — paste into a textarea or upload a `.csv`/`.txt` file;
  both routes through `discoverFromList`. The load summary reports exactly
  what classic's `_renderListLoadSummary` does — rows read vs. listed,
  already-registered count, expanded-org detail (with truncation noted),
  skipped rows, and unreachable URLs — not just a bare result count.
- **One shared review table** for both modes: checkbox selection,
  "Select all new (N)" (skips already-registered and hidden-disposition
  rows, same predicate as classic), a text filter over the visible batch, a
  group-assignment dropdown, per-row disposition (all six of
  tracking/investigating/recommended/using/abandoned/ignored, prompting for
  a reason on the two hidden ones), and "Import Selected" — which queues
  the same background `POST /api/discovery/import` classic's button does.
  Nothing is registered by search or from-list alone; the status line says
  so explicitly, matching classic's own emphasis.
- **CSV export** — a "⬇ Download inventory" button in the from-list panel,
  using `fetchInventoryCsv()` plus the Blob/anchor download classic's fixed
  version uses, including the empty-file guard and reading the server's
  dated filename off `Content-Disposition` rather than reconstructing it.

**`tailwind-next.css`** rebuilt (`frontend-build`'s `npm run
build:css:next`) for the new dialog width and grid classes — closing the
gap `Backlog.md`'s "tailwind-next.css has no build-freshness check" entry
names, for this change at least.

## Tests

**Static-source (no browser)**, following this codebase's established
`/next`-without-a-browser pattern (`test_next_automate_pane.py`,
`test_next_curate_pane.py`'s `_app()` helper):

- `tests/test_next_discovery_import_search.py` (new, 21 assertions) —
  placement (the module lives outside `stages/`, `stages/discovery.js`
  stays empty of corpus-level UI), the two new `re-api.js` wrappers
  (including that `fetchInventoryCsv` checks `res.ok` before returning and
  never touches the DOM), `openDialog`'s new optional `wide` argument and
  that existing two-arg callers are unaffected, the search form's fields
  matching `RepoSearchRequest`, that org has no separate expansion
  affordance, from-list's paste/upload and load-summary detail, the
  two-step review-before-import shape, the CSV download's blob-download
  sequence, and per-row disposition with the hidden-disposition reason
  prompt.
- `tests/test_next_discovery_assessment_analysis_stages.py` — updated the
  `TestOrgImportRepoSearchFromListCsvExportStayDeferredAtTheSidebar` class
  (renamed `…LiveAtTheSidebarNotTheStage`): it used to assert the
  `find-repos` action merely links out for every resource type, which is no
  longer true for repos. Now asserts the dispatch is by resource type and
  that `stages/discovery.js` still carries no corpus-level UI of its own —
  the actual property that item's scoping call was protecting.

**Backend** — no new tests. `discovery.py`'s four endpoints already have
route-level test coverage from classic's own usage (`tests/` already
exercises `/search`, `/from-list`, `/inventory.csv`, `/import`,
`/disposition`); this item is a new UI client of the same routes, not a
change to their behaviour.

Full suite: `uv run pytest tests/ -q` from `packages/resource-explorer` —
see the run recorded at the end of this document.

## Live verification

Verified live, signed in, against a throwaway dev server (`uv run
resource-explorer web --port 8819`, launched from this worktree, `.env`
copied from the main checkout so it points at the same shared Postgres
registry and Egeria platform) — **not** against the main checkout's server
on `:8810`, which continues to serve `main` and was never touched or
restarted. Signed in as `erinoverview` / the shared dev password.

- **Search**: ran a live GitHub search for `keyword=egeria`. Got 90 real
  results (`odpi/egeria`, `odpi/egeria-python`, `odpi/egeria-workspaces`,
  etc.), correctly marked "already registered" against the real registry,
  with working disposition icons, group dropdown, and result filter.
- **From a list**: pasted `https://github.com/odpi/egeria` (already
  registered) and `https://github.com/dwolfson/egeria-python` (not yet
  registered). Got the real two-row response with the exact summary text
  described above ("2 row(s) read → 2 listed below, 1 already
  registered... Nothing is registered yet — select rows below and Import
  Selected to add them").
- **Import**: selected `dwolfson/egeria-python` and pressed Import
  Selected. Got "Import started for 1 repo(s)" and, after the module's
  4-second sidebar-refresh delay, the repo appeared for real in the
  sidebar's Ungrouped group — confirming the background
  `_run_import_batch` path (catalog-only registration, no Egeria publish
  or survey — verified by reading `github/org_importer.py` before running
  this, since this dev box shares the same live Postgres registry and
  Egeria platform as the main checkout) completed end to end. **Cleaned up
  immediately afterward** — removed the test import via the resource's own
  `remove` control — since this box's registry is the same shared instance
  the main app reads, not an isolated one.
- **CSV export**: clicked "⬇ Download inventory" from the from-list panel.
  Got "Downloaded re-inventory-2026-09-22.csv — 64 resource(s)" — a real,
  non-empty response with the server's own dated filename, no "Export
  failed" fallback path triggered.
- **Console**: zero errors across the whole session (checked after each
  step).

Not separately re-verified live: the per-row disposition buttons beyond
what was already exercised for `dwolfson/egeria-python` during from-list
(setting a disposition on an undecided candidate uses the same
pre-existing `setDisposition` call Discovery's disposition sub-tab already
had before this item, and was not re-tested here to avoid additional
shared-registry writes beyond the one import/cleanup pair above).

## What was deliberately not built

- **A third "org import" screen** — there is no such thing to port; see
  above.
- **Saving a search as a named discovery source**
  (`_saveCurrentSearchAsSource`, "Saved sources" quick-run chips,
  foundation prefilters, the GitHub base-URL override) — a real, separate
  slice of `discovery.py` (`/sources`, `/foundations`,
  `/quick-list-sources`, `/github-base-url`) with its own admin-facing
  surface in classic. Named here rather than silently dropped: it is not
  one of the four items the originating survey named as the core ask, and
  building it well (source CRUD, foundation chips, quick-add) is its own
  item's worth of work.
- **`working-set`/`disposition-history`** (`discovery.py:556-684, 712-775`)
  — read/organize surfaces over data this item's UI already writes
  (per-row disposition), not additional write paths. Left for a follow-up
  that wants to surface disposition history/working-set membership in
  `/next`, since neither blocks the four core actions this item covers.
