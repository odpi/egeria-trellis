# Item 4 — Automate: implemented (real, deliberately partial)

**Replies to:** `PLAN-FINISH-REPOS.md` item 4 — *"Automate — not started.
Owns `next/stages/automate.js`. Done when: the stage does the automation
job, or honestly says it defers to classic. Needs: Part 2 (the app.js
split — done, merged to main)."*

**Branch:** `re/automate`, worktree `.claude/worktrees/wt-automate`.
Nothing was written in the main checkout.

---

## The starting point

`next/stages/automate.js` was a genuine empty stub (`export {};`), and
`automate` had no `built: true` flag in `app.js`'s `STAGES` array, so it
rendered as an honest "not in /next" placeholder — the stub's own comment
said exactly what building it would take: a renderer, the `built: true`
flag, and one import line.

## What "the automation job" is

`packages/resource-explorer/CLAUDE.md`'s Automate section and
`docs/discovery-automate-project-context-plan.md` Part 4: subscribe to an
analysis, get notified via RFA when it changes on a future scheduled run.
It is **local-first by explicit decision (2026-08-13)** — `notification_subscriptions`
plus `scheduler.py`'s `_check_subscriptions()`, not a real Egeria
NotificationType yet.

Classic's Automate view (`static/index.html`) has three sub-tabs, not two:
`🔔 Subscriptions`, `⏱ Schedules`, and `📋 Surveys` (put a whole Survey
Definition on a recurring cadence). Subscriptions are created from a
"🔔 Notify me" button on an Assessment/Analysis card, not from within the
Automate view itself — Automate only ever *shows* subscriptions, never
creates them.

The routes (`web/routes/automate.py`, `web/routes/schedules.py`) are
straightforward CRUD-plus-status over `ProjectRegistry`:

- `GET/POST /api/automate/subscriptions`, `POST .../{id}/activate|deactivate`
  — `SubscriptionData` carries a server-computed `has_schedule` flag
  (`_scheduled_pairs`): whether an *enabled, recurring* schedule exists for
  the same `(entity_type, entity_slug, analysis_id)`. Detection only ever
  runs off a scheduled completion, so an active subscription with no
  schedule can never fire, and the route computes this so the client never
  has to cross-reference two lists to know it.
- `GET /api/schedules/` — every schedule across every resource, each row
  carrying `last_run_status`/`last_run`/`next_run`; `DELETE
  /api/schedules/{entity_type}/{slug}/{analysis_id}` to remove one.

## Decision: real port, deliberately partial

Built, not deferred — but not the whole classic view either. Split by
whether the missing piece is a genuine `/next` gap or an artifact of what
else in `/next` doesn't exist yet:

**Built (`next/stages/automate.js`):**

- **🔔 Subscriptions** — list (global, or filtered to the selected
  resource via a "Just `<slug>`" checkbox, same UX as classic), with the
  no-schedule warning shown on the row (not just at creation time, since
  the condition can become true later if someone removes the schedule),
  and Activate/Deactivate against the real routes.
- **⏱ Schedules** — the global overview: resource, analysis, cadence,
  last-run status/time, next-run time, and a real Remove action against
  `DELETE /api/schedules/...` (with a confirm, since it's destructive and
  the route takes no confirmation flag of its own).

**Deferred, and named specifically:**

- **Creating a subscription.** Classic creates one from a "🔔 Notify me"
  button on an Assessment/Analysis card — and neither Assessment nor
  Analysis is built in `/next` (`STAGES` marks both `built`-less; they
  render as "not in `/next`"). Building a standalone create-subscription
  form here, detached from the card action it's supposed to live on, would
  be exactly the half-built-feature outcome the stub's own comment warns
  against: a UI element with no natural home, duplicating a decision that
  belongs on a card `/next` doesn't have yet. The pane instead names this
  one gap specifically ("creating one rides on the 🔔 Notify me action ...
  and those stages are not built in /next yet") and links to classic via
  `oldUiHref()` — the same resource-preserving link-out every other
  deferred surface in `app.js` uses (the RFA drawer, `deferredPaneHtml`).
- **📋 Surveys sub-tab** (scheduling a whole Survey Definition bundle).
  Left out of this port: it is a *third* sub-tab bolted onto Automate
  because "seems like there should be one place" (a project-owner remark
  quoted in `index.html`'s own comments), not part of the two-tab model
  `CLAUDE.md`'s Automate section actually describes ("its own '⏱ Schedules'
  sub-tab ... alongside its '🔔 Subscriptions' sub-tab"). Bringing in a
  third, larger surface (choosing among 8 survey definitions, an existing-
  schedule diff, a 34-step bundle) was out of scope for this item; it can
  be its own follow-up once Discovery's survey-launch surface exists in
  `/next` to share machinery with.

This means the "done when" test is met honestly on its own terms: the
stage does real automation-job work (subscriptions are visible, their
liveness is visible, they can be turned on/off; schedules are visible and
prunable) for exactly the part of the job `/next` currently has the
supporting surfaces for, and says exactly what it defers and why, rather
than either building a orphaned create-form or blanket-deferring a stage
that mostly doesn't need to be.

## What was built

- `re-api.js`: `listSubscriptions`, `setSubscriptionActive`,
  `listAllSchedules`, `deleteSchedule` — thin wrappers over the existing
  routes, following the file's established `get`/`post`/raw-`request`
  conventions (no new helper functions needed).
- `app.js`: exported `oldUiHref()` (was module-private) so `automate.js`
  can build the same "open in current UI" link the RFA drawer and
  `deferredPaneHtml()` already use; added the `automate.js` import; added
  `built: true` to the `automate` STAGES entry with a comment recording the
  partial-port decision above; added a dispatch branch in `loadPane()`
  (`if (state.stage === 'automate') { await renderAutomate(); ... return; }`)
  that bypasses the generic Questions-checklist engine and the standard
  `SUB_TABS` row entirely — same shape as Understanding's chart pane, for
  the same reason: Automate's screen is not a question list, and showing
  the four Questions/Survey/By analysis/Disposition tabs here would offer
  tabs that mean nothing for this stage.
- `next/stages/automate.js` (new): `renderAutomate()` dispatches to
  `renderSubscriptions()` or `renderSchedules()` based on module-local
  `_tab` state (mirroring classic's `_automateSubTab` — this is view state
  for one pane, not something any other stage reads), each with its own
  two-tab subnav, loading state, error state, and empty state.

## Verification

**Tests** (`tests/test_next_automate_pane.py`, 15 tests, all passing):
wiring (STAGES flag, import, dispatch-before-the-generic-built-check),
that subscriptions/schedules hit the real routes, that the no-schedule
warning renders, that toggle/delete actions disable themselves and surface
failures on the control that triggered them, and — the honesty checks —
that the deferred piece names the specific missing card action (not a
generic "not built" string), links out via the shared `oldUiHref()`
helper, and that no create-subscription form has been added to this module
without updating this pinning test.

**Static/syntax:** `node --input-type=module --check` against
`automate.js`, `re-api.js`, and `app.js` — all parse cleanly as ES modules.

**Live, unauthenticated:** started a throwaway dev server
(`uv run resource-explorer web --port 8813`, a port not used by the
running app on 8810/8811/8812) and loaded `/next` in a browser. The app
requires Egeria sign-in for every non-public path (`docs/runtime-
architecture-plan.md` §4) and this session cannot type a password into a
form, so the Automate pane itself — which needs an authenticated fetch to
`/api/automate/subscriptions` and `/api/schedules/` — was **not exercised
live**. What *was* confirmed: `static/next/stages/automate.js` and
`static/re-api.js` both load with `200 OK` and no console errors on the
unauthenticated login screen, meaning the module is syntactically valid
and its imports resolve. The pane's actual data flow, empty/loading/error
states, and the two toggle/delete actions were verified only via the
grep-based test suite above, not against a live signed-in session. The
throwaway server was stopped after this check.

**Full suite:** `uv run pytest tests/ -q -k "not Postgres"` — see the
branch's push for the exact pass count; no regressions from this change
(only `tests/test_next_automate_pane.py` is new, plus the two edited files
`re-api.js`/`app.js` which no existing test asserted the absence of).

## What's still open

- The 📋 Surveys sub-tab, as noted above — a real follow-up, not silently
  dropped.
- Live, signed-in verification of the actual subscription/schedule data
  and the toggle/delete round-trips. Whoever has Egeria credentials for a
  dev instance should load `/next`, sign in, open Automate, and confirm a
  real subscription's no-schedule warning and toggle work before this is
  treated as browser-verified rather than statically-verified.
