# Admin surface: separate site, separate page, or stay inline?

**Status:** implemented. Options survey from 2026-09-03; the admin surface it proposed has been built.

The project owner's observation: "EA has a separate /admin site — which we might need for RE
as well as admin gets more sophisticated." This is a survey and a proposal
only. No production code changes.

## 1. What EA's `/admin` actually is

- Route: `GET /admin` → `FileResponse(_STATIC / "admin.html")`
  (`packages/egeria-advisor/advisor/web/admin.py:267-269`), registered via
  `app.include_router(_admin_router)` in
  `packages/egeria-advisor/advisor/web/app.py:59-60`.
- It is a **separate static HTML file** (`admin.html`, 1010 lines,
  `packages/egeria-advisor/advisor/web/static/admin.html`), not a separate
  build/app. Same FastAPI process, same `StaticFiles` mount
  (`app.py:57`), just a second top-level page next to `index.html`
  (also 1010 lines vs `index.html`'s own size in EA — comparable weight,
  worth noting the two pages are roughly the same order of magnitude there,
  unlike RE where `index.html` dwarfs everything).
- Its own dedicated API namespace, `/api/admin/*`: status
  (`admin.py:272`), collection reindex (`:330`), repo pull (`:349`), job
  polling (`:362`, `:369`), maintenance actions — refresh_perspectives,
  refresh_specs, clear_cache, invalidate_index, refresh_templates,
  check_draft_doc_ids, repair_draft_doc_ids (`:377`), plan-stats (`:428`).
  These are backend/operator actions (git pulls, reindexing, cache
  invalidation, background jobs with polling) that have no equivalent in
  the analyst-facing chat UI at all — a genuinely different surface, not a
  duplicate.
- **Not linked from EA's own nav.** `grep` across
  `packages/egeria-advisor/advisor/web/static/*.html` for `/admin` finds
  nothing pointing at it from `index.html` — it's a bookmark-only URL. The
  only link is the other direction: `admin.html` has a "← Chat" button back
  to `/` (`admin.html:38`).
- **Auth: none.** `GET /admin` and every `/api/admin/*` route in
  `admin.py` has no `Depends(...)`, no header check, nothing — it is
  reachable by anyone who can reach the server, same as the rest of EA.
  So EA's precedent is not "a properly-secured admin site"; it is "an
  unlinked, unauthenticated static page that happens to live at a
  memorable path." Worth being honest about that when citing it as
  precedent.
- What it does well that an inline panel could not, given EA's shape: it
  runs and polls long background jobs (git pull, reindex) via a job table
  (`Job` class, `admin.py:44-109`) and shows live vector-collection health
  across every RAG collection — content orthogonal to EA's chat/plan
  screens, so nothing in `index.html` needs to know this exists.

## 2. RE's current admin inventory

RE has **no `/admin` route at all**. There is no page you navigate to;
"Admin" is a header-level intent inside the single-page app, with 11 panes
across three dropdown groups, defined at
`packages/resource-explorer/resource_explorer/web/static/index.html:12555-12571`:

| Group | Panes |
|---|---|
| Configure | 📝 Annotation Types · 🗂 Groups · 🔍 Discovery Sources · ❓ Question Catalog |
| Reconcile | 🔄 Egeria Alignment · 🔗 Egeria Links · 📤 Publish Queue · 🔧 Repair |
| Observe | ⚡ Prefect · 💬 Feedback · 📜 Logs |

All 11 are rendered by JS functions inside `index.html` into `<div>`
placeholders declared at lines 597-639 (`annotation-types-view`,
`admin-groups-view`, `admin-discovery-sources-view`,
`admin-egeria-links-view`, `admin-prefect-view`,
`admin-question-catalog-view`, `admin-resync-view`, `admin-repair-view`,
`admin-outbox-view`, `admin-feedback-view`, `admin-logs-view`) and switched
via `showMainView()`. None of these panes has its own URL; they are all
`fetch()` calls against JSON APIs from the one giant `<script>` block.

**One exception, and it's a genuine anomaly, not a clean second pattern:**
`packages/resource-explorer/resource_explorer/web/static/admin-feedback.html`
(181 lines) is served separately at `GET /admin/feedback`
(`resource_explorer/web/app.py:145-149`), gated by a password-style
"Admin token" field that calls `GET/PATCH /api/feedback*`
(`admin-feedback.html:93,108,153`), which the backend actually enforces —
`packages/resource-explorer/resource_explorer/web/routes/feedback.py:44-46`
calls `is_admin_request()` before every read/write.

But the inline `admin-feedback-view` pane in `index.html`
(`loadAdminFeedbackPanel()`, line 12734) is a **second, independent
implementation of the same feature**: it fetches `/api/curate/feedback`
(a different endpoint — `routes/curate.py`, not `routes/feedback.py`) and
that endpoint has **no** `is_admin_request()` check anywhere — confirmed by
`grep -rl is_admin_request packages/resource-explorer/resource_explorer/web/routes/*.py`
returning only `feedback.py`. So today, in the one place RE already tried
"separate page," the result is two panels, two endpoints, two data
sources (`resource_feedback` vs `feedback` stores — see the comment at
`index.html:628-635`), and two different auth postures for what a user
would reasonably assume is the same feature. That is a concrete,
already-existing cost of separation, not a hypothetical one: a second
surface is a second place for the same concept to drift.

`admin_auth.py`
(`packages/resource-explorer/resource_explorer/web/admin_auth.py:1-13`)
is explicit that it is not a general auth system — "deliberately NOT a
general auth system... exists only to gate the feedback-triage admin
endpoints" — and fails closed if unconfigured. It is the *only* admin auth
gate in RE: Egeria Alignment's repair endpoints, Repair, Publish Queue,
Prefect, Groups, Discovery Sources, Question Catalog and Logs all have no
auth check at all (confirmed: `is_admin_request`/`admin_auth` appears in
exactly one route file). So RE's admin surface today has an even weaker
and more inconsistent auth posture than EA's (which is uniformly none) —
RE has a real gate on exactly one-eleventh of its admin surface.

## 3. Contention pressure — measured

- `packages/resource-explorer/resource_explorer/web/static/index.html` is
  **15,774 lines**, `wc -l`.
- It is the single most-touched file in the package by a wide margin.
  `git log --since=2026-08-06 --name-only` across
  `packages/resource-explorer` for the period this file has existed:

  | File | Commits touching it |
  |---|---|
  | `resource_explorer/web/static/index.html` | **166** |
  | `docs/Backlog.md` | 116 |
  | `scripts/arch-spike/README.md` | 80 |
  | `resource_explorer/surveyors/repo_survey_definition_adapter.py` | 76 |
  | `resource_explorer/registry.py` | 67 |

  (`git log --oneline -- <path> | wc -l` on `index.html` alone, over its
  full history, returns 181.)
- Churn is not spread thin — it clusters. Commits-per-day on `index.html`:
  23 on 2026-08-30, 19 on 2026-08-24, 17 on both 2026-08-25 and 2026-08-26,
  15 on 2026-08-31 and again 15 on its first day, 2026-08-06. That's the
  pattern behind "three agents queued behind it" — this file is
  frequently the busiest single write target in the whole package on any
  given day.
- Today's (2026-09-01) two commits on it —
  `fa68c96 Admin Feedback pane: show both feedback stores, badged by
  origin` and `8b3dbf9 Promotion: show that it is working, and that it
  worked` — are logically unrelated (an Admin/Observe fix vs a
  Curate/Promotion UI fix) and both had to serialize through the same
  15.7k-line file.
- By contrast, EA's admin page has essentially never needed to change:
  `git log --oneline -- packages/egeria-advisor/advisor/web/static/admin.html`
  returns 1 commit in this checkout's history. Zero evidence yet that EA's
  split has paid for itself in reduced churn (small sample — EA's history
  in this checkout is short, 28 commits total for the whole package) — but
  it also hasn't needed maintenance, which cuts against "sophistication
  demands a separate site" as the reason EA has one. EA's actual reason
  looks more like "these are backend jobs with no other home," not
  "the main page got too big."

## 4. Does RE have a build step? (bearing on what "separate site" would mean)

Partially yes, and this changes what "separate" can cost. RE has a
build step, but only for **assets**, not for **application code**:
`packages/resource-explorer/frontend-build/` (`build-vendor.js`,
`tailwind.config.js`, `package.json`) compiles Tailwind CSS and vendors
`marked`/`plotly`/`svg-pan-zoom` into checked-in files
(`resource_explorer/web/static/tailwind.css` and
`.../static/vendor/*.min.js`), replacing what used to be CDN `<script>`
tags — see `frontend-build/README.md`. This is a recent addition (the
`@tailwindcss/typography` plugin note is dated 2026-08-31).

What this means for the proposal: there is still **no JS framework, no
bundler, no component system** — `index.html` is one hand-written
`<script>` block with plain `fetch()`/DOM calls, same as
`admin-feedback.html`. A second page today would cost exactly what
`admin-feedback.html` cost: another static HTML file with its own
`<script>` block, sharing the built `tailwind.css`/vendor JS, no new
build tooling required. "A separate site" for RE right now realistically
means **a second static page served by the same FastAPI app**, not a
second deployable application — unless RE later adopts a real frontend
framework, at which point the calculus changes again.

## 5. Options, honestly costed

**(a) Leave inline (status quo).**
- Cost: `index.html` keeps growing and keeps being the busiest merge
  target in the package (166 commits and counting, clustering to 15-23/day
  at peak). No navigation cost, no duplicated auth/data-source logic, no
  second place to keep in sync — the admin-feedback split already shows
  what duplication costs (§2).
- Trigger to move off this: contention that is actually blocking work —
  not "the file is big" in the abstract, but repeated same-day conflicts
  or agents unable to make independent progress because they all need to
  touch the same `<script>` block. Today's two same-day, unrelated commits
  on `index.html` (§3) is exactly the shape of evidence that would justify
  reconsidering, but two same-day, non-conflicting commits touching a
  15k-line file is not yet the same thing as agents blocked on each other
  — worth measuring actual merge conflicts, not just co-occurrence, before
  treating this as decisive.

**(b) Extract admin into separate static pages served by the same app**
(the `admin-feedback.html` pattern, already precedent).
- Cost: one new static file per pane moved (or one file per admin
  *group* — Configure/Reconcile/Observe — which is more coarse-grained
  and less likely to fragment further), each with its own `<script>`
  block; a route per page (`app.py` already has the pattern at
  `:145-149`); and, critically, **duplicated logic has already happened
  once** (§2) — this option only pays off if the moved pane's data-fetch
  and auth logic is factored so index.html's inline version is deleted,
  not left running alongside a new one. It also loses the SPA's session
  continuity — no shared client-side state with whatever resource/project
  is selected in the main app, so any admin pane that today reads "the
  currently selected repo" (Prefect's per-repo filter, Schedules'
  repo-scoped view) would need that context passed explicitly (query
  param) or would become global-only.
- Trigger: pick a *specific* pane that is (1) rarely touched together with
  the rest of `index.html`'s churn, (2) has a genuinely different
  audience/cadence (operator-only, not analyst-facing), and (3) can be
  fully migrated in one pass (delete the inline version, not fork it).
  Feedback is the only pane that already has a foot in this world, and it
  should be **fixed** (delete `loadAdminFeedbackPanel()`'s
  `/api/curate/feedback` path or reconcile it with the gated
  `/api/feedback` path) before it's used as a template for extracting
  anything else.

**(c) A genuine separate admin app** (own process, own deploy, own
build).
- Cost: real — a second thing to run, version, and keep talking to the
  same backend; meaningful only once there's enough operator-only surface
  (background jobs, cross-collection health, subprocess-driven repo pulls
  — EA's actual content) to justify it. RE's 11 panes are mostly
  CRUD-over-JSON-API screens against the same data model the analyst UI
  already renders (Groups, Discovery Sources, Annotation Types are
  registries the main app also reads), not a different application
  domain the way EA's job-runner/reindexer is.
- Trigger: RE grows genuine long-running background-job orchestration
  (the way EA's git-pull/reindex jobs work) that doesn't fit request/
  response, or admin needs its own deploy/release cadence independent of
  the analyst UI (e.g. a separate team owns it, or it needs different
  uptime/security requirements). Nothing in the current 11 panes needs
  this today — Egeria Alignment's repair operations are synchronous
  dry-run/confirm HTTP calls (§6), not background jobs with polling.

## 6. What must not be lost in any split

Egeria Alignment (`admin-resync`) is not a passive viewer; any relocation
of it must carry these properties intact, not re-implement them from
memory:

- **Scan vs. repair is a hard split, enforced by an explicit flag.** Every
  bulk action goes through `_postEgeriaBulk()`
  (`index.html:14461-14468`) which sends `dry_run: true` first; the
  "Confirm — run for real" button is the only path that repeats the call
  with `dry_run: false` (`index.html:14441-14444` comment: "Every click
  here does a dry_run:true call first... the backend requires an explicit
  target list rather than 'everything stale' precisely so a user acts on
  what they saw").
- **Undetermined is reported separately from clean, and never silently
  cleared.** `d.undetermined_count` renders its own amber line ("Lookups
  that failed. Reported, never counted as drift and never cleared" —
  `index.html:14912-14915`) rather than being folded into either "clean"
  or "drifted."
- **Expensive repairs are unticked by default and stay listed, not
  hidden**, so "N repairable" doesn't read as a stuck/failed run when it's
  actually declined work (`index.html:14899-14910`, comment: "That number
  was mistaken for a failed run twice in one session before this line was
  split").
- **Destructive actions carry an explicit, specific warning string per
  action** (`_EGERIA_BULK_WARNING`, `index.html:14456-14459`), not a
  generic confirm dialog — e.g. delete-in-Egeria explicitly says RE's own
  survey results are kept even though the catalog element (and reports
  published beneath it) are deleted.
- **Apply order is fixed regardless of selection** ("Runs in dependency
  order regardless of what you tick," `index.html:14930`).

Any of (b) or (c) must move these as behavior, not just as markup — a
separate page that re-does "scan then repair" with a plain confirm()
dialog, or that merges undetermined into either bucket, would be a
regression dressed as a refactor.

## 7. Recommendation

**Stay with (a), inline, for now** — with one immediate fix and one
concrete trigger for revisiting.

The case for splitting rests on `index.html`'s size and churn, and both
are real (15,774 lines; 166 commits; days peaking at 23). But the evidence
for *contention actually blocking work* is weaker than the evidence for
*this file changes a lot* — a file can be the busiest in the repo without
agents being blocked on each other, and I did not find evidence of actual
merge conflicts, only same-day co-occurrence. EA's `/admin` is not strong
precedent for "split when admin gets sophisticated": it's unauthenticated,
unlinked from nav, and exists because its content (background jobs, repo
pulls) has no home in the chat UI at all — not because its main page got
too big. RE's 11 admin panes are mostly registries and reconciliation
views over data the analyst UI already touches, which is a different
shape of "admin" than EA's.

The one place RE already tried separation — `admin-feedback.html` — is
not a clean success story to build on: it produced a second endpoint, a
second auth posture, and a second data source for the same concept,
still unreconciled. Extracting more panes onto that pattern today would
compound the drift risk before fixing the original instance.

**Immediate fix, independent of this decision:** reconcile
`loadAdminFeedbackPanel()`'s `/api/curate/feedback` (unauthenticated) with
`admin-feedback.html`'s `/api/feedback` (gated) — pick one endpoint and
one auth posture for "feedback admin," and either delete the standalone
page or make the inline pane defer to it.

**What would change this recommendation:** concrete evidence of blocked
work, not just co-occurrence — e.g. a real merge conflict on
`index.html` between two agents' independent admin changes, or a
specific admin pane whose data-fetch/auth needs diverge enough from the
analyst UI's session model that inlining it is actively awkward (Prefect
and Logs are the closest candidates — pure operator content with no
resource-selection context to share). If that happens, extract that one
pane using the `admin-feedback.html` pattern (option b), but only after
fixing its existing dual-implementation problem first, and only moving a
pane whose inline version is then deleted outright rather than left to
fork again.
