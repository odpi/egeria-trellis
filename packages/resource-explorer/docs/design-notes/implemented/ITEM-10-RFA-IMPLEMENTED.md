# Item 10 — RFAs: implemented

**Replies to:** `PLAN-FINISH-REPOS.md` item 10 — *"RFAs — not started. Owns
`next/rfa.js` (a NEW file — it does not exist yet, unlike the stage stubs);
`rfa_egeria_sync.py`. Done when: an RFA can be raised and tracked in
`/next`; today `app.js` (near where the header renders) honestly links out
to classic with the RFA count. Needs: independent — no blocking
dependency."*

**Branch:** `re/rfa-tracking`, worktree `.claude/worktrees/wt-rfa`. Nothing
was written in the main checkout.

---

## What was already there

RFA data was live and real before this branch touched anything: `log_rfa()`
is called from `context.py` (blank-critical-field auto-RFA on save) and
`scheduler.py` (Automate's own notifications, delivered as RFAs reusing
this same mechanism). The backend lifecycle was fully built —
`GET /api/activity/rfas` (`web/routes/activity.py`) flattens every
`RequestForAction` annotation in the activity log into one row per request,
overlaid with any locally-recorded response action; `PATCH
/api/activity/rfas/{id}` records defer/reassign/complete/reopen against
`registry.py`'s `rfa_actions` table and then attempts a best-effort Egeria
`ToDo` sync (`rfa_egeria_sync.py`), never blocking on it. `/next` had none
of the client side: `app.js`'s header rendered a dashed-underline link —
*"The RFA drawer is not built in /next — opens the current UI"* — straight
to `/`, and `re-api.js` only had `listRfas()` (used solely to compute the
header's total count).

## What was built

**`resource_explorer/web/static/next/rfa.js`** — a new top-level shared
module, deliberately alongside `worklist.js` rather than under `stages/`:
RFA is chrome-level infrastructure (a persistent panel reachable regardless
of which stage is active), not a per-resource stage, exactly as this
package's own `CLAUDE.md` already says of the classic `#rfa-drawer`. Being
top-level and imported *by* `app.js` (the same relationship `worklist.js`
has), it carries its own tiny `esc()` rather than importing `state`/`esc`/
`$` back from `app.js` — that would be circular.

The drawer:
- builds a fixed slide-in panel on first open, lazily (no DOM cost until
  someone clicks the header button);
- fetches `GET /api/activity/rfas` fresh every time it opens, so a
  defer/reassign/complete made in a previous visit or another tab is never
  shown stale;
- **scopes to the resource currently in view by default** (a "this resource
  only" checkbox, pre-checked when a resource is selected, off and disabled
  when none is), with a second checkbox to widen to every RFA — the plan's
  "filtered to the current resource, and/or a header-level all RFAs view"
  done test, both halves;
- shows what each RFA is about: entity name/slug, the analysis or
  annotation type, the summary and explanation text, and
  `action_requested`/`action_target_name` when the annotation carries them —
  the same fields the classic drawer already reads off this row shape;
- wires the three local response actions the backend already supports —
  **Defer** (prompts for a defer-until value), **Reassign** (prompts for an
  assignee), **Complete** (prompts for an optional resolution note) — plus
  **Reopen** on anything not already open, which is the same `PATCH`
  endpoint with `status: 'open'`, not a separate code path;
- keeps dismissed RFAs **visible, not hidden**, with their suppression
  reason shown inline — matching `docs/rfa-dismissals.md`'s "suppress with
  visibility" rule the classic drawer already follows; this drawer does not
  add its own dismiss action (out of scope — see below), it only has to not
  contradict a dismissal made elsewhere;
- updates the acted-on row in place from the `PATCH` response rather than
  re-fetching the whole list, so the scope/show-completed toggle state
  survives a click.

**Header wiring in `app.js`.** The old `<a href="/" title="The RFA drawer is
not built in /next…">` is replaced with a real `<button id="rfa-drawer-
toggle">`, wired (in `renderIntentNav()`, alongside the existing `chat-
toggle` wiring) to `toggleRfaDrawer(state.selectedSlug || '')` — passing
whichever resource is currently in view, or `''` for "no resource selected,
scope to all". The header's count badge (`#rfa-count`, `state.counts.rfas`)
is unchanged — it already existed and this item's done test does not ask
for a smarter count, only a working drawer.

**`re-api.js`** gained one function, `updateRfaAction`, `PATCH`-ing
`/api/activity/rfas/{id}` with the same four fields
`RfaActionUpdateRequest` (`web/routes/activity.py`) already expects:
`status`, `assignee`, `defer_until`, `resolution_note`. `listRfas()` already
existed and needed no change.

## What is explicitly out of scope

Per the package `CLAUDE.md`'s own framing of RFA as "a stepping stone
toward real Egeria ToDo actions, not that integration itself" — this drawer
does not add anything beyond what the classic UI and the backend already
support:
- **No dismiss action** (not-applicable / won't-do, `docs/rfa-
  dismissals.md`) — the drawer reads and shows dismissal state, it does not
  let someone create one. A real gap if dismissal turns out to matter in
  `/next` day to day, but the plan's done test names only "raised and
  tracked", and defer/reassign/complete is the tracked-and-acted-on half of
  that.
- **No free-text notes field** (`PATCH /api/activity/rfas/{id}/notes`) —
  same reasoning; `resolution_note` (asked for inline on Complete) is the
  one piece of free text this drawer captures.
- **No new way to *raise* an RFA from `/next`.** `/next` can already raise
  one — the "raise RFA" record action (`app.js`, `data-record-act="rfa"` /
  `data-promote="rfa"`, via `actOnRecord`) — and that path was left alone.
  "An RFA can be raised and tracked" reads as one continuous capability, and
  the raising half was already real; this item's gap was entirely on the
  tracking half.

## `rfa_egeria_sync.py` — what it does, and why it needed no change

Read in full before deciding scope, since the plan names this file as owned
by this item. It is real, working backend logic, already wired into both
`PATCH` routes (`update_rfa_action`/`update_rfa_note` call `sync_rfa_action`/
`sync_rfa_note` after the local write, non-blocking, logged-and-swallowed on
failure) and into `scheduler.py`'s background loop
(`reconcile_rfa_actions()`, one pass per iteration):

- **Write direction:** every `rfa_actions` row with no `egeria_todo_guid`
  yet gets a real Egeria `ToDo` created (`MyProfile.create_my_todo`); rows
  that already have one get updated (`MyProfile.update_asset` — the file's
  own docstring records a real, previously-fixed bug where the generic
  metadata-element update endpoint silently accepted and discarded a typed-
  property body; the Asset-specific endpoint is what actually works).
  Failed attempts are recorded (`mark_rfa_sync_error`) rather than raised,
  and retried on the next reconciliation pass.
- **Notes:** a resolution note or free-text note becomes an Egeria
  `ActivityEntry` under a per-RFA note log, once a `ToDo` already exists to
  link it to — append-only, one entry per distinct note text, sidestepping
  a known-broken `update_note` server behaviour entirely rather than
  working around it.
- **Read direction:** `reconcile_rfa_actions()` also pulls the caller's own
  open `ToDo`s (`get_my_to_dos`) and overwrites local state for any already-
  linked row whose remote status/dates/priority differ — so a change made
  by another Egeria client (not through this drawer at all) is picked up.
- **Named, real, standing limitation** (not something this item needed to
  fix): RE has no per-user identity yet, so `reassign_action` — which needs
  a real actor GUID — is never called; the drawer's Reassign action only
  ever writes a local free-text `assignee` field, which is exactly what the
  UI text above says it does ("Assign to (name or email)"), not a claim of
  a real Egeria reassignment.

This file needed **no changes**. The drawer built here calls
`PATCH /api/activity/rfas/{id}` exactly the way the classic drawer already
does, and that route is what invokes `sync_rfa_action`/`sync_rfa_note` — the
new `/next` UI is simply a second caller of an already-correct contract. Any
mismatch there would have shown up as the sync firing with fields the UI
never intended to send, and it does not: `updateRfaAction`'s four fields
(`status`, `assignee`, `defer_until`, `resolution_note`) are exactly
`RfaActionUpdateRequest`'s four fields, checked directly in
`tests/test_next_rfa_drawer.py`.

## Test coverage

`tests/test_next_rfa_drawer.py` (new, 21 cases), following the no-browser
grep/slice pattern of `test_next_curate_pane.py` and
`test_next_rail_states.py` — except `rfa.js` is a **top-level** module like
`worklist.js`, not one of `stages/*.js`, so its `_app()`-equivalent
concatenates `app.js` with `rfa.js` directly rather than with the
`stages/` directory. Covers:
- the honest placeholder link and its apology text are gone, and a real
  toggle with the count badge intact replaces it;
- the toggle passes `state.selectedSlug` (the real field name — there is no
  `state.slug`), so a per-resource scope is actually possible;
- `rfa.js` does not import back from `app.js` (the circularity this
  module's top-level placement exists to avoid) and imports only
  `listRfas`/`updateRfaAction`;
- every `data-rfa-act` status the drawer offers is a subset of the
  backend's own `RFA_STATUSES`, cross-checked by parsing both sources
  directly rather than hardcoding the expected set in the test twice;
  defer/reassign/complete/reopen are all present;
- Reopen only appears once the row is no longer already open; a completed
  row loses the three action buttons;
- a click calls `updateRfaAction` with the real field names, updates the row
  in place (not a full reload, which would drop the toggle state), and a
  failed `PATCH` renders "not recorded: …" rather than failing silently;
- Reassign/Defer prompt before acting and a cancelled prompt records
  nothing;
- scope filtering is by `entity_slug`, show-completed defaults to
  excluding `completed`, and opening with no slug does not leave the
  drawer silently scoped to nothing;
- each row surfaces entity, analysis, summary, explanation and the
  action-requested fields; dismissed RFAs stay visible with their reason
  rather than disappearing;
- the `re-api.js` helper's PATCH payload field names are cross-checked
  directly against `RfaActionUpdateRequest`'s fields in
  `web/routes/activity.py`.

**Full suite:** `uv run pytest tests/ -q -k "not Postgres"` —
**4869 passed, 103 skipped, 18 deselected, 0 failed** (9m39s). No
regressions from either change.

## What was verified, and how

**Static/structural, thoroughly** — the test suite above, plus a syntax
check of `rfa.js`, `re-api.js` and `app.js` (`node --check`, via a temporary
`.mjs` copy since these files are untranspiled ES modules served directly).

**Server boot and asset serving, live.** Started a throwaway dev server on
port 8815 (not 8810/8811/8812/8813/8814, per this task's instruction to
avoid ports other concurrent sessions might hold) from this worktree:
`/next`, `/static/next/rfa.js`, `/static/next/app.js` and
`/static/re-api.js` all served `200`. Opened `/next` in a browser and
confirmed **zero console errors** and every module (`rfa.js` included) load
`200 OK` over the network — the import wiring is real, not just
syntactically valid.

## What I could NOT verify

**The rendered drawer itself, signed in.** This checkout requires an Egeria
sign-in to reach the authenticated `start()` path that calls
`renderIntentNav()` (where the new header button lives) — `TRELLIS_
ANONYMOUS_READ` is not enabled here, and clicking "Continue without signing
in" did not bypass it. Typing a password into the login form, even a demo
one, is outside what this session is allowed to do. So the actual open/
close of the drawer, the row rendering against real RFA data, and the
defer/reassign/complete round trip against a live backend were traced
through the code and pinned by the structural test suite above, **not
watched in a browser with a signed-in session**. The fragile points to
check first once this merges and a signed-in session is available: (a) that
the scope checkbox actually narrows the list against real multi-resource
RFA data, (b) that a Defer/Reassign/Complete click's `PATCH` response shape
matches what `Object.assign(row, …)` expects (traced against the route's
return value, `{"status": "success", "id": rfa_id, "rfa_status":
body.status}` — note this does NOT echo back `assignee`/`defer_until`/
`resolution_note`, which is why the row is updated from the values already
in hand locally rather than from the response body), and (c) that the
best-effort Egeria `ToDo` sync behind the PATCH does not surface a visible
delay the drawer's "saving…" state doesn't account for.
