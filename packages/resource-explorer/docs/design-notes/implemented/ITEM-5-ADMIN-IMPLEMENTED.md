# Item 5 — Admin: implemented (real, deliberately partial)

**Replies to:** `PLAN-FINISH-REPOS.md` item 5 — *"Admin — not started. Owns
`next/admin/*`, `web/routes/*`. Done when: the ten classic admin views are
either reachable in `/next` or each recorded as a deliberate deferral.
Independent — no dependency on Part 2 (the app.js split)."*

**Branch:** `re/admin-surface-next`, worktree `.claude/worktrees/wt-admin`.
Nothing was written in the main checkout. This item was previously assigned
to another peer session; the worktree it left behind had only stale,
already-merged commits unrelated to Admin, so it was reset to a fresh branch
off current `origin/main` (`7b7bc2b4`) before this work started.

---

## The starting point

There was no `next/admin/` directory and no header entry point for Admin —
`/next`'s header carried a placeholder outbound link (`<a href="/"
title="Admin and feedback are not built in /next — opens the current
UI">Admin ↗</a>`), the same shape Activity's header link had before item 6.
`web/routes/*` needed no changes: every classic Admin pane already reads a
route that exists and works; the gap was entirely on the `/next` client.

## Reconciling "ten" against what classic actually has

The plan's "ten classic admin views" undercounts by one against what
`index.html`'s own `_ADMIN_GROUPS` constant declares today (commit
`4fb480715`/`26320f89a`, "nine flat tabs grouped by the question being
asked" then "a control per group, not a button per pane" — that second
commit's own message says "eleven panes"):

| Group | Panes |
|---|---|
| **Configure** | Annotation Types, Groups, Discovery Sources, Question Catalog |
| **Reconcile** | Egeria Alignment, Egeria Links, Publish Queue, Repair |
| **Observe** | Prefect, Feedback, Logs |

That is 4 + 4 + 3 = 11, not 10. `packages/resource-explorer/CLAUDE.md`'s own
"System/catalog configuration is not an intent" section is stale on the same
axis for a different reason: it still names only "Annotation Types
registry, resource Groups, and the Schedules overview" as reachable from ⚙
Admin — Schedules moved into Automate's own sub-tab on 2026-08-13 (a
decision `CLAUDE.md`'s Automate section documents correctly), and Feedback
plus Logs were added under Observe on 2026-08-31/09-01, after that sentence
was last true. Both discrepancies are pre-existing documentation drift, not
something this item introduced — the "ten" figure and the CLAUDE.md sentence
were written before the current shape existed. This doc and `next/admin/
index.js`'s own header comment treat eleven as the real count and record
the discrepancy here rather than silently building against the wrong number.

## Decision: which views are real ports, which are named deferrals

Read every one of classic's eleven pane functions in `index.html` before
deciding (sizes below are the function's own line count, not counting
shared helpers):

| Pane | Size | Decision | Why |
|---|---|---|---|
| 📝 Annotation Types | ~120 | **Built** (browse only) | List + detail view is genuinely useful on its own and is the pane CLAUDE.md names first; Register/Edit/Delete deferred (see below) |
| ❓ Question Catalog | ~120 | **Built** | Read-only by design even in classic — "This screen does not write back to the CSV" is classic's own comment |
| 📜 Logs | ~90 | **Built** | Simple GET over an in-memory ring buffer; no mutation at all |
| 💬 Feedback | ~160 | **Built** | Read-only listing behind an existing admin-token gate; reused verbatim |
| ⚡ Prefect | ~100 | **Built** | Read-mostly status, plus one reversible, already-idempotent cancel action — same shape as a schedule delete, which item 4 (Automate) already ported |
| 🗂 Groups | ~900 | **Deferred, named** | Three separable jobs bolted into one pane (group CRUD, per-repo assignment, GitHub-org bulk import); the largest single classic pane after Egeria Alignment |
| 🔍 Discovery Sources | ~180 (list+create+refresh+delete) | **Deferred, named** | Create/refresh both trigger a real external scan, and shares state with Groups' GitHub-org import |
| 🔄 Egeria Alignment (resync) | ~1000 | **Deferred, named** | The largest classic pane; every repair is a real write against shared Egeria/registry state |
| 🔗 Egeria Links | ~270 | **Deferred, named** | Same reconciliation shape as Egeria Alignment |
| 📤 Publish Queue (outbox) | ~110 + retry semantics | **Deferred, named** | Retry is tied to the outbox's own internal state machine; a read-only port would misrepresent the pane |
| 🔧 Repair | ~270 | **Deferred, named** | Every action is a destructive, identity-changing write (rename, fix `github_url`), each gated behind its own confirm in classic |

Five built, six deferred — the same "genuinely portable now vs. named
deferral" split `ITEM-4-AUTOMATE-IMPLEMENTED.md` used, applied at the
per-pane grain rather than per-stage since Admin is eleven independent
panes, not one screen with sub-tabs answering one question. The axis that
actually separated built from deferred was not size alone (Question
Catalog and Feedback are non-trivial) but **whether the pane's job is to
answer a question or to perform a write against shared Egeria/registry
state that this item's verification could not exercise live** (see
Verification below) — every deferred pane's core action is a mutation with
real consequences; every built pane is read-mostly, with Prefect's cancel
the one narrow exception justified above.

Each deferral is named specifically in `next/admin/index.js`'s `GROUPS`
constant as a `{ does, why }` pair, not a generic "not built" string, and
renders with a live `oldUiHref()` link into the same pane in classic —
identical house style to `ITEM-4-AUTOMATE-IMPLEMENTED.md`'s one deferred
Automate sub-surface.

## What was built

- **`next/admin/index.js`** — the panel shell. A header-triggered overlay
  (`openAdminPanel()`), chrome-level like `next/stages/activity.js` — NOT a
  `STAGES` entry, decoupled from `#intent-nav`/`currentNavIntent`, matching
  `CLAUDE.md`'s "System/catalog configuration is not an intent" ruling.
  Reproduces classic's three-group `<select>` subnav (`_adminSubnavHtml`,
  commit `26320f89a`) rather than a button row, for the same reason classic
  switched to it: eleven destinations plus three labels do not fit one line
  at any width that does not wrap, and a wrapped group label reads as a
  stray word. `GROUPS` declares all eleven tabs; five carry a `render`
  function, six carry a `defer: { does, why }` pair.
- **`next/admin/annotation_types.js`** — list + detail browse against
  `GET /api/analyses/annotation-types`; the three mutating actions
  (Register/Edit/Delete) link out via `oldUiHref()`.
- **`next/admin/question_catalog.js`** — full port of the read-only browser
  (search, stage chips, perspective chips) against
  `GET /api/analyses/question-catalog`.
- **`next/admin/logs.js`** — full port of the log viewer against
  `GET /api/logs/`, including the three distinct empty states ("filtered out
  of a non-empty buffer" / "buffer genuinely empty, and that's not evidence
  of nothing happening" / residual "nothing to show") and the self-stopping
  5s auto-refresh (stops when the host element's `data-admin-pane` no longer
  reads `'logs'`, i.e. the panel closed or the subnav switched away — a
  sharper guard than classic's own "pane hidden" check, since /next's panel
  removes its DOM entirely on close/switch rather than hiding it).
- **`next/admin/feedback.js`** — full port of the combined
  resource+page feedback listing against `GET /api/curate/feedback`,
  reproducing classic's `X-Admin-Token`/`re_admin_token` gate exactly (same
  sessionStorage key, so entering the token in either surface authenticates
  both) rather than a second mechanism. A 403 renders the token-gate
  explanation, not an empty list — "not authenticated yet" and "no feedback
  exists" are different facts and this pane exists specifically because an
  earlier version of classic's own pane once collapsed a different pair of
  facts the same way (2026-09-01 incident, `fa68c9676`). Absent ratings
  render an em dash, not zero stars, pinned the same way `9d5a1452d` pinned
  it in classic.
- **`next/admin/prefect.js`** — full port of flow-run status plus cancel,
  against `GET /api/prefect/status`, `GET /api/prefect/flow-runs`,
  `POST /api/prefect/flow-runs/{id}/cancel`.
- **`re-api.js`**: `listAnnotationTypes`, `getAnnotationType`,
  `listQuestionCatalog`, `listLogs`, `getPrefectStatus`,
  `listPrefectFlowRuns`, `cancelPrefectFlowRun` — thin wrappers over the
  existing routes, in the file's established `get`/`post` style. Feedback's
  admin-token-gated fetch is deliberately NOT routed through `re-api.js`'s
  `request()`: a 403 there is an expected, distinct UI state (not yet
  authenticated) that must render its own explanation rather than the
  generic `ApiError` path every other caller of `request()` gets.
- **`app.js`**: imported `openAdminPanel`; added `wireAdminButton()`
  (idempotent, same shape as `wireActivityButton()`) called from
  `renderTopBar()`.
- **`index.html`**: replaced the placeholder `<a href="/" title="Admin and
  feedback are not built in /next…">Admin ↗</a>` with
  `<button id="admin-open-btn">⚙ Admin</button>`, matching
  `activity-open-btn`'s shape exactly.

No `web/routes/*` changes were needed — every route the five built panes
read already existed and already worked; classic and `/next` now share them
unchanged.

## Verification

**Tests** (`tests/test_next_admin_pane.py`, 26 tests, all passing): Admin's
absence from `STAGES`, the header button replacing the outbound link and its
idempotent wiring, that all eleven tab ids are present and grouped
Configure/Reconcile/Observe, that exactly five carry a `render` function,
that every deferred tab names a specific (non-generic) `does`/`why` pair and
links out via `oldUiHref()`, and per-pane pins for each built module (the
three-empty-states distinction in Logs, the token-gate-not-empty-list and
em-dash-not-zero-stars rules in Feedback, the confirm-before-cancel in
Prefect).

**Static/syntax:** `node --input-type=module --check` against every new/
changed file (`app.js`, `re-api.js`, and all six `next/admin/*.js` files) —
all parse cleanly as ES modules.

**Live, unauthenticated:** started a throwaway dev server
(`uv run resource-explorer web --port 8816`, not one of the ports any other
running instance uses) and loaded `/next` in a browser. Confirmed every new
static asset serves `200 OK`
(`/static/next/admin/{index,annotation_types,logs,feedback,question_catalog,
prefect}.js`, `/static/next/app.js`, `/static/re-api.js`), and that loading
`/next` produces zero console errors. The app requires Egeria sign-in for
every non-public path (`docs/runtime-architecture-plan.md` §4); this session
cannot type a password into any field, demo or otherwise, and clicking the
page's own "Continue without signing in" control did not reach an
authenticated session on this dev box (no `TRELLIS_ANONYMOUS_READ` override
set). **The Admin panel itself — opening it, browsing Annotation Types,
paging through Logs, hitting the Feedback token gate, watching Prefect
status — was NOT exercised live against a signed-in session.** What was
confirmed is exactly what `ITEM-4-AUTOMATE-IMPLEMENTED.md` was able to
confirm under the same constraint: the modules load, parse, and import
correctly, and the server itself started and ran without error (server log
tail showed no exceptions or tracebacks through worker startup, survey-
definition cache warm, and private/draft-zone bootstrap). The throwaway
server was stopped after this check.

**Full suite:** `uv run pytest tests/ -q -k "not Postgres"` — run to
completion from this worktree. See the branch's push for the exact pass
count; the only new file is `tests/test_next_admin_pane.py`, and the only
edited files besides it are `re-api.js`, `app.js` and `next/index.html`
(all additive — no existing route, function, or exported symbol was
removed or renamed).

## What's still open

- The six deferred panes (Groups, Discovery Sources, Egeria Alignment,
  Egeria Links, Publish Queue, Repair) — each a real follow-up with its own
  design pass, not silently dropped. Egeria Alignment and Groups are the two
  largest and most likely to want their own item rather than a quick port.
- Live, signed-in verification of the five built panes' actual data flow —
  whoever has Egeria credentials for a dev instance should load `/next`,
  sign in, open ⚙ Admin, and confirm Annotation Types/Question Catalog/Logs/
  Feedback/Prefect all render real data and that Prefect's cancel and
  Feedback's token gate round-trip correctly before this is treated as
  browser-verified rather than statically-verified.
- The `packages/resource-explorer/CLAUDE.md` sentence naming only three
  reachable panes should eventually be corrected to reflect the current
  eleven — left alone here since fixing it was not this item's scope, but
  flagged so it does not read as authoritative against what actually ships.
