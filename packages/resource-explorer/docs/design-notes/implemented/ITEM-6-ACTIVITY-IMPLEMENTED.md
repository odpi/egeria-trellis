# Item 6 — Activity: implemented

**Replies to:** `PLAN-FINISH-REPOS.md` Part 3, item 6 — *"Activity — not
started. Owns `next/stages/activity.js`. Done when: the activity log is
readable in `/next`, not only a data source. Needs: Part 2 (the `app.js`
split — done, merged to main)."*

**Branch:** `re/activity`, worktree `.claude/worktrees/wt-activity`.
Nothing was written in the main checkout.

---

## The structural question first

Activity was named alongside Enrichment/Understanding/Curate as an example
`next/stages/*.js` file, but it is not one of the nine canonical stage ids
in `app.js`'s `STAGES` array (investigation, scouting, discovery,
assessment, analysis, enrichment, understanding, curate, automate). The
stub file already said so, and `packages/resource-explorer/CLAUDE.md` is
explicit: the 📋 Activity log is a persistent surface reachable from the
header, decoupled from `#intent-nav`/`currentNavIntent`, the same pattern
as ⚙ Admin. So this item was never "add `built: true` to a STAGES entry"
the way Understanding turned out to be — it needed its own nav affordance.

Before this branch, `app.js`'s header rendered a literal outbound link:

```html
<a href="/" title="The activity log is not built in /next — opens the current UI">
  Activity <span id="activity-count">…</span> ↗
</a>
```

Clicking it left `/next` entirely. That is the thing item 6's done-test
("readable in `/next`, not only a data source") rules out.

## What was already there

`listActivity(limit)` and `pollActivity(entryId, opts)` were already
imported into `app.js` from `re-api.js` and used — but only inline, for
polling one in-flight run's progress (e.g. Curate's commit flow,
`renderCurate`'s `await pollActivity(out.activity_id, …)`), never as an
Activity pane of their own. `state.counts.activity` was already populated
at startup (`listActivity(ACTIVITY_LIMIT)` in `start()`) purely to paint the
header's count badge. None of this rendered a single activity *entry*
anywhere in `/next`.

## What "readable" means here, and what I read first

I read the classic UI's implementation (`index.html`'s `loadActivityLog` /
`renderActivityLog` / `logOp` / `updateOp`, ~200 lines) and the backend
(`web/routes/activity.py`'s `list_activity`, `registry.py`'s
`ActivityEntry` dataclass and `list_activity`/`get_activity` methods, the
`activity_log` table) before deciding scope. Two things shaped the design:

1. **Classic's `_activityLog` is a client-side merge**, not a mirror of the
   record: `loadActivityLog()` keeps any browser-local-only entries (created
   by `logOp()` for a run started in *this* tab and not yet reflected by the
   API) alongside whatever `GET /api/activity/` returns, and `clearActivityLog()`
   only empties that in-memory array — it cannot and does not touch
   `activity_log` itself. None of that survives a reload; the actual
   persistent record is entirely server-side. Reproducing the merge and the
   fake "Clear" in `/next` would have copied a piece of classic's own
   awkwardness (a "Clear" button that clears nothing durable) rather than
   fixing it.
2. **The real data shape** (`ActivityEntry`): `operation` (`scout | survey |
   catalog | publish | discover | rfa | refresh | analysis_run`), `intent`,
   `entity_type`/`entity_slug`/`entity_name`, `status` (`running | ok |
   error | pending`), `summary`, `detail` (free text, GUIDs embedded),
   `items` (cataloged Egeria objects: kind/name/qualified_name/guid), and
   `annotations` (survey-report annotation tallies). `list_activity` already
   supports `entity_type`/`intent`/`operation`/`status`/`since` filters
   server-side and orders `ts DESC`; `listActivity(limit)` in `re-api.js`
   only ever passed `limit`.

## What I built

`next/stages/activity.js` (new, ~260 lines) exports `openActivityPanel()`:
a header-triggered overlay (the same `fixed inset-0 z-50` / backdrop-click /
Escape-to-close shell `worklist.js`'s `openDialog` establishes for the cell
detail and refresh-plan dialogs, reimplemented here rather than imported
because `openDialog`'s body assumes worklist's own `wl-detail` id and
`closeCellDetail` keydown handler) showing:

- A **text filter** (matches `entity_slug`/`entity_name`/`summary`,
  case-insensitive) and a **status chip row** (all / ok / error / running),
  both client-side over one freshly-fetched page of up to 300 entries —
  intentionally not a reproduction of classic's five-field filter form
  (`entity_type`/`intent`/`operation`/`status`/`since` as separate selects).
  One text box plus a status chip answers "what failed" and "what happened
  to this repo" without rebuilding every control classic has.
- **One row per operation**: icon + label (`Survey`, `Catalog → Egeria`,
  `RFA`, …), entity icon + slug, intent, a status glyph/tone
  (✓ ok / ✗ error / ◔ running, reusing the same `text-state-ok` /
  `text-state-warn` / `text-accent-ink` tone vocabulary Curate's `tone` map
  and the checklist rows already use — not a fourth color scheme), the
  summary line, and a relative time (`ago()` from `format.js`) with the
  full UTC timestamp on hover.
- **Detail collapsed behind a toggle**, not always shown — matching
  classic's `▶`/expand behavior — carrying the annotation tally, a cataloged-
  items table (type/name/short-GUID with click-to-copy, same convenience
  classic's `hiGuid` gives), and the free-text `detail` field with GUIDs
  highlighted the same way.
- **Four distinct messages**, not one collapsed "nothing to show": reading
  in flight, a fetch failure ("could not be read: …"), zero entries ever
  ("No operations recorded yet … not because logging is broken"), and a
  filter matching nothing ("Nothing recorded matches this filter" plus the
  loaded total) — the same "confident wrong answer" trap this codebase's
  `find-absence-as-answer` pattern warns about elsewhere. A saturated fetch
  (300 rows back, more may exist) says "Showing the most recent 300
  entries" rather than implying that is the whole history.
- **RFA operations are shown, not filtered out.** The RFA drawer
  (`#rfa-drawer`) is `/next`'s own separate not-yet-built item, and its
  `RequestForAction` annotation rows belong there, not here — but rule 16
  requires every operation type be logged, including `rfa`, and hiding the
  `rfa`-operation row from this list would recreate exactly the
  "logged but invisible" gap that rule exists to close.

**app.js's footprint**: one import line, a 9-line `wireActivityButton()`
(idempotent, guarded the same way `wireSidebarDrawer`/`wireTextSize` are,
called from `renderTopBar()` — which already ran on every render and
already wrote `#activity-count`'s text), and no changes to `loadPane()`,
`STAGES`, or any stage-routing logic. Activity still is not a stage.

**index.html**: the header's `<a href="/" title="…not built in /next…">`
became `<button id="activity-open-btn" title="Recent activity — scouting,
survey, catalog, publish, RFA">`, keeping the same visual slot, the same
`#activity-count` span `renderTopBar()` already writes into, and dropping
the `↗` (which signaled "leaves the page" — no longer true) and the "not
built" wording (no longer honest).

## What I deliberately left out

- **Classic's advanced filter form** (five separate selects) — covered
  above.
- **The unread badge and `_activityLog` merge** — both are artifacts of
  classic rendering a client-only copy of a server-side log; `/next`'s
  panel reads the live endpoint fresh on every open instead, so there is
  nothing for an "unread since last viewed" concept to track that the
  fetch itself doesn't already show.
- **A "Clear" action** — classic's clears an in-memory copy only, never the
  real record; reproducing it here would either mislead in the same way or
  require a real "delete activity history" endpoint, which does not exist
  and is out of scope for a read pane.
- **A resource-scoped entry point** (e.g. a button on the resource header
  pre-filtering to that repo's own history) — considered, but adding a
  `data-act` handler into `resourceHeaderHtml`/`bindResourceHeader` would
  have grown `app.js`'s footprint well past "one element id, one listener,
  one import line" for a convenience the text filter already covers (type
  the slug). Left as a natural follow-up if it turns out to be wanted.
- **No STAGES entry, no `built: true` flag anywhere** — per the structural
  note above, this would misrepresent what Activity is.

## Test coverage

`tests/test_next_activity_pane.py` (new), following the established
grep/slice-the-concatenated-source pattern (`test_next_curate_pane.py`,
`test_next_understanding_pane.py`) — no browser, no DOM. 20 tests across
six classes:

- `TestActivityIsNotAStagesEntry` — `activity` is absent from the `STAGES`
  array; the module documents why.
- `TestTheHeaderLinkNowOpensInNext` — the old "not built in /next" wording
  is gone, the header control is a `<button>` (not an outbound `<a href="/">`),
  `#activity-count` still exists for `renderTopBar()`'s existing write,
  `app.js` imports and wires `openActivityPanel` to the button, and the
  wiring is idempotent (guarded the same way its sibling wire functions are
  — a second listener per render would double-open the panel on every
  header repaint).
- `TestThePanelReadsTheRealActivityLog` — it calls the same `listActivity`
  the classic tab uses, refetches (does not cache) on every open, and
  carries no client-side merge or fake `Clear`.
- `TestDistinctEmptyErrorAndNoMatchStates` — the four states above stay
  four different sentences, and a saturated page says so.
- `TestEntryRenderingCarriesWhatClassicShows` — operation/entity/status/time
  all render; detail stays collapsed behind a toggle; GUIDs are shortened
  with click-to-copy; RFA operations are shown, not hidden.
- `TestFilteringIsClientSideOverTheFetchedPage` — the status and text
  filters both apply; Escape and backdrop-click both close the panel.

All 20 pass in isolation. Full suite: see below.

## Live verification

Started a throwaway dev server from this worktree on port 8814 (not 8810,
8811, 8812, or 8813): `TRELLIS_ANONYMOUS_READ=true uv run resource-explorer
web --port 8814`, set for the session only, not written to any `.env` —
against the shared Postgres/Egeria instance. Booted cleanly; no startup
errors.

Opened `http://127.0.0.1:8814/next` in the browser, used "Continue without
signing in" (the documented anonymous-read dev-box override; no password
was entered by this session, per the hard rule against ever typing one).

Confirmed, with **zero console errors at any point**:

1. The page loads and renders `amundsen-io/amundsen`'s Questions pane with
   no JS errors. The header shows a real count (`Activity 200+`), proving
   `state.counts.activity` and the new button coexist correctly.
2. Clicking the header button opens the panel; it fetches and renders real
   entries from this instance's actual `activity_log` — survey/discovery
   rows for `egeria_trellis`, `egeria_git`, `docling`, etc., including a
   genuine **error** entry (`localhost_docker_coco_ods` / `index_health`
   "completed with errors") rendered in the error tone, distinct from the
   surrounding ok rows.
3. The detail toggle: confirmed via `element.click()` in the page (mouse-
   coordinate clicks through the browser tool were unreliable at this
   viewport's device-pixel scaling — a tool/coordinate-mapping issue, not
   an app bug, cross-checked by reading `hiddenClass` on the target element
   before and after) that clicking `▶ detail` removes the `hidden` class and
   reveals the entry's raw `detail` JSON with its GUID shortened.
4. The **text filter**: typing `docling` narrowed 300 loaded entries to the
   26 whose slug/summary matched, with the "N of 300 loaded entries match"
   line shown.
5. The **status filter**: selecting `error` (with `docling` still in the
   text box) correctly showed **zero** rows with the "nothing matches this
   filter" message — a real conjunction of both filters, not one ignoring
   the other. Clearing the text filter with `error` still selected then
   showed exactly the 8 real error entries in this instance's log.
6. **Escape** closes the panel cleanly, returning to the underlying
   Questions pane with no residue.

Nothing was written to the registry or to Egeria in this session — all
reads. The throwaway server was stopped and port 8814 confirmed free
afterward.

**One incidental finding, not part of this item:** while testing, the
shared browser context briefly surfaced tabs belonging to other concurrent
sessions (a `localhost:8810` tab and a `localhost:8815` tab neither opened
by this session). No action was taken against either — they were identified
by origin, left untouched, and this session's own tab was addressed
explicitly by `tabId` for the remainder of verification. Mentioned here
only so it isn't mistaken for something this branch touched.

## Full suite

`uv run pytest tests/ -q -k "not Postgres"` from
`packages/resource-explorer/` — see this session's final report for the
pass/fail count against this branch; run for regressions after the new
test file and the `app.js`/`index.html` edits.

## What I could not verify

- **A signed-in session's own activity** (as opposed to anonymous-read).
  The dev-box override was used to reach `/next` at all without a password;
  every write-gated flow (survey runs, publishes) that would generate a
  *fresh* entry while the panel is open was not exercised — the panel was
  verified against this instance's existing history, not against a live
  write landing mid-session. `pollActivity`'s existing inline callers
  already cover the "one entry updates while watched" case; this panel does
  not currently auto-refresh a still-open view, matching classic's own
  behavior (`loadActivityLog()` only re-renders while the tab is visibly
  open, on its own poll cadence — this panel simply doesn't add a poll loop
  of its own, since "refetch on next open" was judged sufficient for a
  read-log rather than a live monitor).
- **Very large/very old logs** — behavior beyond the 300-row fetch cap
  (paging further back) is not built; "Showing the most recent 300 entries"
  states the limit rather than working around it, consistent with this
  item's minimal-scope framing.
