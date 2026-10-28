# Groups admin — implemented

**Reply to:** `SPEC-ADMIN-THE-FOUR-GAPS.md` §2 ("Groups — CRUD exists, and so
do suggestions nobody sees").

## What shipped

All four of classic's capabilities, ported against the existing backend —
no new routes, no new backend logic:

1. **Create/delete a group** — `createAdminGroup`/`deleteAdminGroup`
   against `POST /api/projects/groups` / `DELETE .../groups/{slug}`.
2. **Assign a resource to a group** — `submitAssignGroup`'s equivalent
   against `POST /api/projects/{slug}/group`, for all three resource types
   (repo, database, filesystem).
3. **Suggestions** — `GET /api/projects/groups/suggestions` (`GroupSuggestion`),
   which nothing in `/next` surfaced before this. Applying one creates the
   group and assigns every suggested repo, same as classic's
   `applyGroupSuggestion`.

## Where

- `resource_explorer/web/static/next/admin/groups.js` (new) — `renderGroups`,
  wired into `resource_explorer/web/static/next/admin/index.js`'s
  `admin-groups` tab (`defer` → `render: renderGroups`).
- `resource_explorer/web/static/re-api.js` — added `groupSuggestions`,
  `createGroup`, `deleteGroup` (`listGroups`/`assignGroup` already existed
  but were unused anywhere in `/next`).
- `resource_explorer/web/static/next/app.js` — added
  `refreshGroupsAndSidebar()` (exported), called after every group mutation.
  `state.groups`/`state.projects` are otherwise populated once, in
  `start()`; without this the sidebar's own grouping would show stale data
  until a full page reload.
- `tests/test_next_admin_pane.py` — moved `admin-groups` from
  `DEFERRED_TAB_IDS` to `BUILT_TAB_IDS` and added `TestGroupsPane`
  (source-text assertions, this repo's established pattern for `/next` JS
  with no browser harness — see the file's own docstring).

## One deliberate departure from classic

Classic opens the assign-to-group modal from a 🗂 button on each resource's
own row (sidebar item, Curate tab). `/next` has **no per-resource
"assign to group" affordance anywhere** — I checked (`grep`-ed `app.js` for
any existing group-assignment UI; only the unused `assignGroup` API function
existed). Building that entry point would mean editing sidebar/resource-card
rendering, which is explicitly out of scope for this item (owned by the
concurrent sidebar-collapse work — see below). Assignment is offered instead
as its own resource-picker + group-picker control inside this Admin pane,
against the exact same `POST /{slug}/group` route classic's modal uses. Bulk
assignment via multi-select stays out of scope per the spec ("wants the
sidebar's selection, belongs with the collapse round").

## Blast radius — the fix, not just a port

Checked classic's `deleteAdminGroup` directly: **it has no `window.confirm`
at all**, unlike its sibling filesystem/database "remove from registry"
actions a few hundred lines away, which do. That is exactly the gap §0/§2
call out — deleting a group reads as destructive and isn't (members return
to Ungrouped, nothing is deleted) — so the port adds the confirmation
classic is missing rather than copying its absence:

> `Delete group "X"? N resource(s) return to Ungrouped — nothing is
> deleted.`

worded after classic's own better line elsewhere ("Remove filesystem X…
This does not delete your files on disk").

## The split-feature connection — already closed

Checked `SPEC-PARITY-INVENTORY-AND-GROUPS.md` §3 (group *display*, sidebar
collapse) before starting, per this item's brief. **It is already built and
shipped** — `SIDEBAR-GROUP-COLLAPSE-IMPLEMENTED.md`, verified live, full
suite green at the time. So this item's scope is purely the authoring/CRUD/
suggestions half; no display work was silently picked up or left undone.

## Verification

- `node --input-type=module --check` on every touched/new `.js` file.
- Full suite: `uv run pytest tests/ -q` — **5134 passed, 103 skipped, 0
  failed** (run from this worktree).
- Tailwind: this pane's markup introduced classes not yet in the compiled
  `tailwind-next.css` (`bg-paper-surface`, some new arbitrary-value
  utilities) — rebuilt it via `cd frontend-build && npm install && npm run
  build:css:next` and confirmed the new classes are present in the output.
- **Found and did not fix in this pane's own scope:** three *existing*
  `/next` admin modules (`feedback.js`, `prefect.js`, `logs.js`) use
  `hover:bg-paper-raised`, which is not a defined color anywhere in
  `tailwind-next.config.js` (only `paper`/`paper-surface` exist) and does
  not appear in the compiled CSS even after a fresh rebuild — the hover
  highlight on those rows silently does nothing. Flagged as a separate
  follow-up rather than fixed here, since it's pre-existing and outside
  this pane.
- **Not verified against a live signed-in session.** The shared dev server
  at `localhost:8810` serves from the main checkout
  (`/Users/dwolfson/localGit/egeria-v6/trellis`), not this worktree — per
  `CLAUDE.md`'s own warning, pushing to a branch doesn't move what that
  server runs, so a browser click-through there would have shown the old
  deferred-tab code, not this pane. Confirmed this directly (diffed
  `admin/index.js` between the two checkouts) rather than assuming it.
  Verification here is static: full test suite, syntax checks on every
  touched file, and a manual trace of the create → suggest → assign →
  delete flow against the actual route contracts in `projects.py`. A live
  click-through is left to whoever merges this into the checkout the
  server actually serves.
