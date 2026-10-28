# Sidebar group collapse — implemented

**Spec:** `SPEC-PARITY-INVENTORY-AND-GROUPS.md` §3 ("The first entry: collapsible
resource groups"). Read against that doc's citations of classic's
`index.html`; this note records what actually shipped and where it lives in
`/next`'s own `app.js`.

**Trigger:** the project owner, live-testing `/next`: "the groups of resources
on the left are still not collapsible?"

## What was missing

`/next`'s `renderSidebar()` grouped repos by `group_slug` and rendered a
static header with a name and a count. No toggle, no collapse state
anywhere in `state`. The header's own count already promised something it
did not deliver — a count implies something to fold away.

## What shipped

All three of classic's behaviours, ported to `/next`'s own conventions
rather than copied verbatim:

1. **Persisted collapse.** `COLLAPSED_GROUPS_KEY = 're_next_collapsed_sidebar_groups'`
   — deliberately its own key, not classic's `re_collapsed_sidebar_groups`,
   so the two surfaces never fight over one localStorage entry with
   different shapes (the spec's own open question, now closed: a distinct
   key per surface). `collapsedGroups()` reads it; `toggleGroupCollapsed(slug)`
   writes it and re-renders.

   The group header itself is `<details>`/`<summary>` rather than a custom
   caret+button widget — `/next` already uses that pattern elsewhere for
   disclosure (facet groups in `openMembers`, survey-run history, the
   analyses index's "Other stages"), so the group header follows the same
   idiom instead of inventing a new one. The native marker (▸/▾ in most
   browsers) is the caret; no extra icon was added.

   Persistence is driven from a `click` listener on `<summary>` (with
   `preventDefault()`), not from the native `toggle` event. A `toggle`
   listener would also fire from the browser's own initial-state handling
   of the `open` attribute — which is set fresh on every `renderSidebar()`
   call — and could silently overwrite a reader's saved preference with
   whatever the force-expand-on-filter render happened to show. Driving it
   from the click instead means the write only ever happens on a genuine
   user gesture, matching classic's own button-based `_toggleGroupCollapsed`
   in spirit even though the markup differs.

2. **Group-level select respects collapse.** `toggleGroupSelected(groupSlug)`
   selects/deselects every member `visibleProjects()` reports for that
   group — the same list the header's own count is drawn from — regardless
   of whether the group is currently collapsed. Ported directly from
   classic's `_toggleGroupSelected`: a folded group's count already
   includes what's inside it, so selecting the group must select what it
   counted, not just what happens to be visible. The checkbox lives inside
   `<summary>`; its click handler calls `preventDefault()` +
   `stopPropagation()` so picking it doesn't also toggle the disclosure.

3. **Force-expand on filter — shipped together with collapse, not after.**
   `filterActive = !!(state.filter.trim() || state.scope)` mirrors classic's
   `_projectLifecycleFilter` default exactly: `state.scope` defaults to
   `'working-set'` (reset to `''` only once there's no current
   investigation), so "no filter" is the explicit `''`/All state, same as
   classic. Any active filtering forces every group open
   (`collapsed = !filterActive && collapsedSlugs.includes(g)`), so a repo
   matching the filter inside a collapsed group is never silently hidden.
   Clearing the filter returns the group to exactly its saved state — the
   saved list itself is never written to during a force-expand render.

## Where

- `resource_explorer/web/static/next/app.js` — `collapsedGroups()`,
  `toggleGroupCollapsed()`, `toggleGroupSelected()`, the group block inside
  `renderSidebar()`, and the corresponding wiring in `bindSidebar()`.
- `tests/test_next_sidebar_group_collapse.py` — source-text assertions,
  matching this repo's existing pattern for pure front-end JS behaviour
  (see `test_next_component_review.py`, `test_next_rail_states.py`); there
  is no jsdom/browser harness here.

## Verification

- `node --input-type=module --check` on the touched file.
- Full suite: `uv run pytest tests/ -v` — 4994 passed, 103 skipped (no
  failures), including the 8 new tests.
- No new Tailwind utility classes were introduced (checked the diff's
  `class="..."` values against the compiled `tailwind-next.css`, including
  its escaped bracket-class spellings) — `tailwind-next.css` did not need
  rebuilding.
- Live, in a throwaway dev server on port 8821 (`TRELLIS_ANONYMOUS_READ=true`,
  the documented dev-box override in `CLAUDE.md`/`trellis_auth/policy.py` —
  used here only because signing in with real Egeria credentials was not
  possible without entering a password, which is never done): collapsed and
  re-expanded a group by clicking its header; reloaded the page and
  confirmed the collapsed group stayed collapsed; typed a filter matching a
  repo inside the still-collapsed group and watched it force-expand to show
  the match; cleared the filter and confirmed the group returned to
  collapsed (the saved preference survived the force-expand); turned on
  Select mode and checked a collapsed group's checkbox, confirming it
  selected all of that group's members (13/13) without expanding it.
- **Limit:** this could not be verified against the actual signed-in
  Egeria session the project owner uses day to day — `trellis-auth` enforces
  "login required for every non-public path" on a normal run, and
  `TRELLIS_ANONYMOUS_READ` is a GET/HEAD-only dev override, not a
  substitute for a real session. The rendering and persistence logic itself
  does not depend on which identity is signed in, but this was not
  cross-checked against, e.g., role-based visibility differences.
