# Find databases dialog: two defects fixed (2026-10-02)

Found by the owner on the 8813 gate and confirmed from the server log. Branch
`re/find-dialog-fixes`, off origin/main 2d44c2f9. Resolved checkout for every run:
`/Users/dwolfson/localGit/egeria-v6/trellis-re-find-fixes/packages/resource-explorer/resource_explorer/__init__.py`.

## A. Stale "Sign in to load the saved sources."

Root cause: `loadServers()` in `next/db-server-discovery.js` set `view.status` and
`view.statusIsError` in its `catch`, but its success path only set
`view.loadFailed = false`. A 401 at 08:36:39 then a 200 at 08:36:54 left the
banner above a correctly loaded list.

Fix: the catch records the exact text it set (`view.loadFailureMsg`); a successful
load clears `view.status` only when it still equals that text. "Registered server
X." (set before `loadServers` is called from `submitRegister` and `removeServerRow`)
and a Run summary are untouched.

Other places a failed call sets `view.status`, checked: `removeServerRow` failure,
`discoverInline` and `saveSource` validation, `saveSource` failure. Each is
overwritten by the next success of the same action, and Run / Discover / tab change
already set `view.status = ''`. None has the "later success of a different call
leaves it up" shape of the load, so none was changed. Run and Discover failures go
to `view.cand.error`, which `emptyCandidates()` resets on the next attempt.

## B. Open investigation page not refreshed

Root cause: no cache is involved. `next/stages/investigation.js` caches only
vocabularies; `renderDetail(slug)` fetches members on every call, but it is called
only on entering the pane and from the pane's own Add / Remove / lifecycle
handlers. The dialog's confirm (`db-server-discovery.js`, the loop that calls
`addInvestigationMember`) and the sidebar's `bulkScope()` (app.js) wrote members
and updated their own surface (work list / sidebar working set) but never told the
open page. Separately `renderDetail` had no guard for a late response: a slow
members fetch for investigation A repainted `#content` after the person had opened B.

Fix, one mechanism: `refreshOpenInvestigation(slug)` exported from
`stages/investigation.js`. It re-renders only when `state.stage === 'investigation'`
and the open detail is that slug; otherwise it does nothing. Callers, each once
after its writes: the dialog's confirm (when at least one member was added) and
`bulkScope()` (covers both "＋ scope" and "− scope"). `renderDetail` takes a
sequence number and drops its result if a newer render, a different slug, or a
different stage has superseded it (checked after the data fetch and after the
vocabulary fetch).

Other writers: the pane's own Add form and Remove button already re-render with
`renderDetail(inv.slug)` and were not changed (test pins that they still work).
Disposition changes, relink, bind and promote are not member-set changes and were
not touched.

## Evidence

`frontend-build/test-harness/find-dialog-fixes.test.mjs` (9 tests, real app.js,
real router, real investigation pane, only fetch stubbed). On current main: 4 fail
(the A reload test, the B dialog confirm, the B sidebar scope add/remove, the B
late response), 5 pass (known-negatives: still-failing load keeps its message; an
unrelated status survives a load; confirm into a non-open investigation neither
repaints nor fetches it; confirm while another stage is showing does not overwrite
that stage; the pane's own Add / Remove). With the change: 9 of 9.
Full harness 270 / 270 on Node 20.11.0. pytest over tests/test_next_*.py,
tests/test_tailwind*.py, tests/test_static_js_syntax.py,
tests/test_find_databases_dialog.py: 732 passed. `node --check` clean on copies of
the three edited files. No Tailwind classes were added.

## Not verified

No real browser; the pane flash ("Loading…" is painted while the refresh fetches)
is as for the pane's own Add. The sidebar "− scope" path is exercised in jsdom
only for the pane refresh, not against the real server.
