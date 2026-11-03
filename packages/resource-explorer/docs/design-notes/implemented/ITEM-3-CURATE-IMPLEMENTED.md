# Item 3 — Curate: selection and blueprints — implemented

**Replies to:** `PLAN-FINISH-REPOS.md` item 3 — *"Curate — component
selection, blueprints. Owns `next/stages/curate.js`; `component_tree.py`,
`repo_survey_definition_adapter.py`. Done when: a curator can select
components and rule on blueprints, and the accept act catalogues what it
said it would."* Builds directly on
`SPEC-CURATE-SELECTION-AND-BLUEPRINTS.md`, the designer's build-ready spec
this replies to point by point.

**Branch:** `re/curate-selection-blueprints`, worktree
`.claude/worktrees/wt-curate`. Nothing was written in the main checkout.

---

## What the spec found already true, verified again here

The spec's §0 already found that `blueprint_materializer.py` needs no
invented Egeria type — `SolutionBlueprint`, qualified
`SolutionBlueprint::{entity_type}::{entity_slug}::{perspective}::{cluster_name}`,
was already pinned. Before writing any UI against it, this item re-verified
every backend claim the spec makes, per the coordinator's instruction, and
found the backend had moved further than the spec's own reading of it:

- `web/routes/curate.py` already has **both** `list_component_verdicts` and
  `list_blueprint_verdicts`/`add_blueprint_verdict` — the "sibling reader"
  the spec's §2 asks for already existed for the *verdict* half. What was
  missing was a reader that also carries the full candidate-blueprint
  payload (members, children, cohesion signal) the frontend needs to draw a
  list, not just a verdict lookup.
- `resource_explorer/surveyors/repo_survey_definition_adapter.py`'s
  `_candidate_blueprints_results()` already resolves exactly that — one row
  per (perspective, cluster_name), each carrying its own verdict,
  materialization, and its members'/children's verdict+materialization —
  and was already covered by `tests/test_candidate_blueprints_reader.py`.
  It just had no HTTP route.
- **The membership wiring the spec's §4 calls "does not exist" partly
  does.** `blueprint_materializer.py`'s own module docstring says Phase B
  (member/wire enqueueing) is "not here" — true of that file — but
  `resource_explorer/workflows/curate.py`'s `materialize_blueprint_if_accepted`
  (Phase B itself) already calls `resolve_member_guids`/
  `resolve_child_blueprint_guids` and, for whichever members/children
  already have their own materialized Egeria element,
  `egeria_outbox.enqueue_blueprint_members()` — which the outbox's
  `_create_collection_membership` drain handler already knows how to
  process. So accepting a blueprint today **does** queue real
  `CollectionMembership` writes for its already-accepted-and-catalogued
  members, asynchronously.

This is a real discrepancy between the spec and the code it was read
against, not a nitpick, and it changes what "honest" means for §4. See
"The membership-honesty judgement call" below for how this was resolved —
narrower than "not built", narrower than "linked".

## 1 · Selection on the branch tree (§1)

`curateRowHtml`/`recordVerdicts` were already plural-capable, per the spec.
Built the missing wiring in `next/stages/curate.js`:

- A checkbox on every branch row (`branchRowHtml`, `data-branch-select`).
- **No select-mode toggle** — per the spec, the tree's rows are already a
  work queue (unlike the sidebar's navigation rows), so the checkboxes are
  simply present, all the time.
- `curateSelectionSet(slug)` keys the selection Set by slug, so switching
  resources does not carry a stale selection into a different repository's
  tree — the tree's own `componentSort`/`componentShowAll` preferences are
  intentionally global (pre-existing convention), but a *selection* of
  specific paths crossing resources would silently act on the wrong repo,
  which is a different risk and gets its own guard.
- `selectionBarHtml`: **select all shown** (a real checkbox, toggles every
  row currently displayed — 8 by default) and **select all N branches ›**
  (the full set at this tree level, stated before it acts — "select all 69
  branches ›" per the spec's own example). The footer reads "N of M shown
  selected", with an overflow clause ("· K selected in total") once
  select-all-matching has selected more than what's shown.
- **The confirmation states the total scope count before acting.**
  `recordVerdicts` now takes an `onDone` callback (clears the selection) and
  its dialog title reads `"{N} branches selected — {count} scopes total"`
  for a multi-branch call, vs. the original single-branch wording
  unchanged. The `count`/`low`/`exists` numbers are summed across every
  selected branch from the same `tree.branches` data the tree already
  fetched — no second round trip.
- Reject still records at once (no dialog) for any selection size, matching
  the existing single-branch behaviour — rejecting creates nothing in
  Egeria, so rule 4's "name it before you do it" has nothing irreversible to
  name.

## 2 · The blueprint list (§2) and its reading scope (§3)

**New route, no schema change:**
`GET /api/projects/{slug}/components/blueprints` (`web/routes/projects.py`)
wraps the existing `_candidate_blueprints_results()` and adds a
`perspectives` list — the exact "sibling reader, not a schema change" the
spec asked for. `re-api.js` exposes it as `getComponentBlueprints`, and
`postBlueprintVerdict` wraps the already-shipped
`POST /api/curate/blueprint-verdicts/repo/{slug}`.

`next/stages/curate.js`'s new `renderBlueprintList` renders it under the
component tree (`#blueprint-list`, inside the "made of" column):

- **One row per cluster in the current reading**: name, why it's cohesive
  (`signal`/`carrier`), oversized flag, and "N of M members accepted ›"
  opening the member list in the rail (`openBlueprintMembersInRail`).
- **Reading-scoped, and the screen says so** (§3): the head of the list
  states *"A verdict here is recorded against `{reading}::cluster name` and
  applies in this reading only — switching readings shows a different set,
  not the same set re-judged."* Clicking another reading's "the {p} reading
  has K ›" link **replaces** the list outright (`renderBlueprintList` always
  does a fresh `host.innerHTML =`, never a patch) — there is no code path
  that diffs the old reading's rows against the new one.
- **Accept/reject per row**, through the same shared-preview-dialog rule
  (rule 4) `recordVerdicts` uses: accepting names what it will do — "type is
  pinned, unlike a component's" — before it does it; rejecting records at
  once.
- **The twelve clusters the coverage sentence already named are now on
  screen** — the "0 of 12 clusters in the logical reading reviewed" line
  from `_architecture_verdict_coverage_sentence` now opens onto this list
  rather than nothing.

## 3 · The membership-honesty judgement call (§4)

The coordinator's brief treated §4 as unconditional: an accepted blueprint
must say its members are not yet linked, because
`blueprint_materializer.py` "does NOT wire component membership… out of
scope." Verifying against the source (item 3's own prerequisite, and this
project's own recurring lesson — "grep for the verb before designing the
mechanism") found that claim is true of `blueprint_materializer.py` alone
but not of the accept path as a whole: `workflows/curate.py`'s
`materialize_blueprint_if_accepted` **does** call
`egeria_outbox.enqueue_blueprint_members()` for every member/child that
already has its own materialized Egeria element, and the outbox **does**
know how to drain a `collection_membership` row into a real
`CollectionMembership` (`_create_collection_membership`, already wired to
the `collection_manager` client per the RE-server-stuck-incident fix
referenced in memory).

So "not yet linked, full stop" would itself be a false statement about a
write path that genuinely runs. But "linked" would overclaim in the other
direction: RE's own database records that a `CollectionMembership` row was
**enqueued**, never that the outbox actually drained it — there is no
per-row status a later page load can read back (only
`get_outbox_guids` for a `run_id`+row-ids the caller already holds
in-memory from the POST response, which a fresh GET of
`/components/blueprints` does not have). Adding that tracking would be a
schema change, which is exactly what the spec says this item should avoid.

**The call made here:** state the narrower, honest claim — *"its members
are not yet **confirmed** linked"* — and count, as "standing apart", every
member/child that has its own materialized Egeria element (the same fact
`resolve_member_guids` requires before it will even attempt to enqueue that
member). This is:

- **Never false in either direction.** It does not claim zero membership
  when a write may have already landed (the spec's own complaint about
  silence reading as "none"), and it does not claim confirmed linkage RE
  cannot actually verify.
- **The same count, either way.** Whether the outbox has drained a given
  row or not, the member/child was a *candidate* for linking the moment it
  was materialized — that population is exactly what "stand apart" should
  name, and it is stable across a page reload (unlike an in-flight queue
  position).
- **Named per §4's own worked example.** `membershipHonestyLine(bp)`: zero
  materialized members/children reads *"none of its proposed members are
  catalogued as their own Egeria elements yet, so there is nothing to
  link"* (absence stated, not silent); a nonzero count reads *"N accepted
  components stand apart ›"* (and *"M child blueprints"* alongside, if any),
  opening the rail filtered to exactly those (`openBlueprintMembersInRail`
  with `standApartOnly: true`).
- **A third, distinct state, also said honestly:** an accepted verdict with
  no `materialized` row at all — the write itself may have failed
  (`BlueprintMaterializationError`, reported but non-fatal per
  `workflows/curate.py`'s own docstring) — reads *"accepted, but not yet
  catalogued in Egeria — the write may not have completed; re-accepting
  will retry"*, rather than silently rendering the membership line against
  a blueprint that was never actually created.

This is a genuine judgement call the brief did not anticipate (it assumed
the backend state the spec described), made narrowly and documented here
rather than either blindly restating a now-inaccurate spec claim or
silently building something not asked for.

## 4 · The component-acceptance caveat (§4 of the brief) — unchanged

Accepting an individual **component** (not a blueprint) still reads *"the
exact Egeria type is not yet pinned"* in `recordVerdicts`'s confirmation
dialog — untouched. `tests/test_next_curate_selection_and_blueprints.py`
pins that the new blueprint-accept dialog does not introduce
`DeployedSoftwareComponent` or any other invented type name for that
separate, still-open question.

## 5 · The `#130` rule, applied to two more sites (§5)

Added `deferredAttrs(isBuilt, { title, extraStyle })` to `app.js` — the one
place a control's dashed "not built" underline comes from, gated on the
same flag that decides behaviour, never written inline a second time. No
new control this item added actually ended up in a deferred state (the
selection controls, the blueprint accept/reject, and the member-link
affordance are all real, working affordances against a verified backend),
so there was nothing new to route through it. Instead, the helper's first
use fixed the two **pre-existing** inline copies of exactly the pattern
`#130` fixed for Activity/Admin, which a `grep` for the literal style
string turned up while implementing this item:

- The stage nav's unbuilt-stage span (`renderIntentNav`).
- The sub-tab rail's deferred-tab button (`subTabsHtml`).

And, because this item is Curate's own last piece: **`STAGES`'s `curate`
entry is flipped to `built: true`**, the same pattern every other finished
stage in that array already followed (see the comments on Discovery/
Assessment/Analysis, Enrichment, Understanding). Before this, Curate's nav
tab rendered as the same dashed, unclickable span as a genuinely unbuilt
stage — real, shipped code (component-tree review, the catalogue-depth
offer, and now selection and blueprints) sitting behind chrome that told a
user not to bother clicking. `state.stage` was always reachable by URL
(`?stage=curate`) regardless, so this is a nav-affordance fix, not a
change to what was reachable.

## What was deliberately not built

- **Membership wiring itself** — linking components into a blueprint in
  Egeria. Named as future work in `blueprint_materializer.py` before this
  item, and still is: this item found that the *enqueueing* half already
  exists (see §3 above) but did not build the missing confirmation/status
  tracking that would let the UI say "linked" rather than "stand apart,
  not confirmed." That tracking is a genuine follow-up (a per-row read on
  `egeria_outbox`, or a persisted "last known linked" flag), not attempted
  here because it would be exactly the schema change the spec says this
  item should avoid.
- **The exact Egeria type for individual component acceptance.** Separate
  open question per `REPLY-CATALOGUE-IN-LAYERS.md`, not this item's to
  resolve — the existing caveat is left exactly as it was.
- **`repo_survey_definition_adapter.py:2951`'s primary-pick logic.** The
  brief and the spec both name this as a separate, already-recorded LOW
  backlog item; not touched here.
- **The classic Curate panel beyond the honesty clause** already ruled in
  `RULING-CLASSIC-AND-NEXT.md` §3 — out of scope per the spec's own "done
  when" list.
- **Truncation/pagination on the blueprint list.** A repository's candidate
  blueprints are typically a dozen or so (the coverage sentence this item
  makes actionable already said "12"), so every cluster in the current
  reading renders without the tree's own 8-then-"and N more" pattern. If a
  repository turns up with dramatically more clusters than components have
  branches, that pattern is a small, separate follow-up.

## What I could not test

**Live, signed in.** The main checkout (`:8810`) still serves `main` and
was never touched. A throwaway dev server on port 8819 (this worktree's own
checkout) confirmed:

- the server boots cleanly, connects to the shared Egeria platform
  (`Quickstart OMAG Server Platform`), and both draft- and private-zone
  bootstrap checks pass;
- every `/next` static asset — `app.js`, `re-api.js`, and every
  `stages/*.js` including the modified `curate.js` — serves `200 OK`;
- `node --input-type=module --check` passes on `app.js`, `re-api.js`, and
  `stages/curate.js`;
- zero console errors on load.

The app requires Egeria sign-in (2026-09-04 runtime plan). This box has no
`TRELLIS_ANONYMOUS_READ` override; "Continue without signing in" is present
but a no-op here (confirmed via `/api/auth/policy` returning "login
required for every non-public path" in the server's own startup log), and
no password was entered, demo or otherwise, per this session's absolute
rule. So the actual interaction — ticking branch checkboxes, the
select-all-matching count, the blueprint list rendering real cluster data,
accept materialising a real `SolutionBlueprint` GUID on screen — has **not
been seen rendered in a browser against real data**. Static/structural
verification above, plus the backend route/materializer tests against a
real (test) registry, are the honest substitute; live verification is the
first thing to do once this merges and a signed-in session is available.

## Tests

**Backend** — `tests/test_curate_blueprints_route.py` (new): the new
`/components/blueprints` route's 404/empty/shape/per-reading behaviour, and
that accepting through the already-shipped
`POST /api/curate/blueprint-verdicts/repo/{slug}` round-trips its
verdict/materialization/member-status back through this new reader with no
second write path. 6 tests.

**Frontend (static source)** — `tests/test_next_curate_selection_and_blueprints.py`
(new), following this codebase's established pattern for `/next` JS
without a browser (`test_next_component_review.py`,
`test_next_curate_pane.py`): 21 assertions across four groups —

- **Selection on the tree** — no select-mode toggle; every branch row
  carries a checkbox; select-all-shown and select-all-matching are
  distinct; the footer states shown/selected; the confirmation states the
  total scope count; bulk reject skips confirmation, bulk accept does not;
  selection clears after a bulk action; selection does not leak across
  resources.
- **The blueprint list** — a row shows why it's cohesive and a member
  count that opens; accept/reject both offered; the reading-scope sentence
  is present verbatim; switching readings replaces rather than diffs; the
  foot names the current and other readings; accepting names the pinned
  type and does not invent one for components.
- **Membership honesty** — a zero count is stated, not silent; a nonzero
  count is named and opens; the line only renders for an accepted,
  materialized blueprint; an accepted-but-unmaterialized blueprint says so.
- **Deferred styling** — `deferredAttrs` exists and is exported; the stage
  nav and sub-tab rail use it instead of an inline copy; Curate is flipped
  to `built: true`.

Existing suites unaffected: `tests/test_next_curate_pane.py` (5),
`tests/test_next_component_review.py` (8), and every backend suite the new
route's reused reader was already covered by
(`tests/test_candidate_blueprints_reader.py`,
`tests/test_component_tree.py`) all still pass unmodified.

Full suite: `uv run pytest tests/ -q` — **5013 passed, 103 skipped, 0
failed**, run twice against this branch (both runs agree).

## Addendum, 2026-09-17: the component tree grouped by scope-hierarchy cluster

The project owner tested Curate live on `odpi/egeria-trellis` itself after this item
shipped and found "what it's made of" hard to read: `packages/` alone holds 64 of
the repository's 69 recovered components, and opening that branch showed all 64
as one flat list (`curate.js`'s `leafRowHtml`, one row per component, sorted by
path with no further structure). The designer's read: the missing middle level
already exists — `scope_hierarchy.derive()` (`surveyors/arch_recovery/
scope_hierarchy.py`) computes exactly the grouping that would fix this, from the
same scope-locator paths the tree already keys components by, and it's already
on screen elsewhere as the "scope-hierarchy · collection" rows in the blueprints
panel below. So: group the flat leaf list by scope-hierarchy cluster instead of
building a new grouping scheme.

**Verified before building, not taken on faith.** `clustering.py`'s `propose()`
(~line 497) calls `parents, _structural = scope_hierarchy.derive(scopes)` as its
first pass, and `Cluster`'s own defaults (`clustering.py` lines 94–100) are
`signal: str = "scope-hierarchy"` / `carrier: str = "collection"` — so an
unpromoted, undivided cluster is literally rendered as "scope-hierarchy ·
collection" by `blueprintRowHtml` (confirmed live: `odpi/egeria-trellis`'s
blueprints panel shows `packages/resource-explorer/resource_explorer` etc. with
exactly that label). `repo_survey_definition_adapter.py`'s
`_candidate_blueprints_results()` (~line 3298) reads those persisted clusters for
the panel. The designer's premise held: this is the same function, applied to
the same kind of input (a flat set of scope locators), not merely an analogous
one.

**Where it's applied instead of joining the persisted rows.** The persisted
`candidate_blueprint` findings are scoped to one `perspective` at a time (a
blueprint is keyed `perspective::cluster_name`, see this file's own "Blueprints"
section above and `RULING-WHAT-A-VERDICT-IS-ABOUT.md` §0), while a branch's
leaves (`component_tree.leaves()`) mix every perspective with no reading
selected. Joining through the persisted rows would mean picking one perspective
for a multi-perspective leaf list, silently dropping components that only exist
in another reading. `component_tree.group_leaves()` (new) instead calls
`scope_hierarchy.derive()` directly over the branch's own leaf paths — same
algorithm, same input shape, no perspective to choose.

**What was built:**
- `component_tree.group_leaves(leaf_rows)` — `(groups, ungrouped)`. First pass
  groups by `scope_hierarchy.derive()`'s parent map, same as `clustering.py`'s
  own first pass. A first-pass group over `clustering.TARGET_CLUSTER_SIZE` (10)
  is re-derived within its own members (mirroring `clustering.py`'s
  `_subdivide()`), so a group that has a deeper level to find gets it, and one
  that doesn't stays one (undecided) oversized group rather than being
  truncated — same "no signal, no cluster" contract as the blueprint pipeline.
  `scope_hierarchy.MIN_GROUP = 2` is inherited unchanged: a would-be group of one
  collapses nothing, so its member comes back in `ungrouped` and renders as a
  plain row, same as before this change.
- `GET /api/projects/{slug}/components/leaves` now also returns `groups` and
  `ungrouped` alongside the unchanged flat `leaves` (the one other caller —
  `branch_verdicts`' materialization filter — reads `leaves()` directly, not the
  route, so it is untouched).
- `curate.js`: `leafGroupHtml(g)` renders a group as `<details>`/`<summary>`
  (this file's existing disclosure idiom, e.g. `app.js` ~line 4228), the summary
  showing the group's scope path and `N accepted · N rejected · N undecided` —
  the exact wording `branchRowHtml` already used one level up, kept consistent
  rather than inventing new phrasing. **Open by default when the group has any
  undecided members, closed once fully decided** (accepted+rejected with no
  undecided remaining) — the designer's exact rule. The branch-open handler now
  renders `out.groups.map(leafGroupHtml)` followed by `out.ungrouped.map(leafRowHtml)`
  instead of the old flat `out.leaves.map(leafRowHtml)`; a branch with no
  qualifying groups (small branches, unchanged from before) still falls back to
  a flat list because `groups` is simply empty. Every per-leaf capability
  (accept/reject buttons, proposal/agreement/withdrawal lines, ports) is
  untouched — grouping only wraps the same `leafRowHtml` output, it does not
  replace it. Branch-level selection (checkboxes, select-all-shown/matching,
  branch accept/reject) lives one level up in `branchRowHtml` and was not
  touched by this change.

**Live verification against `odpi/egeria-trellis`** (`TRELLIS_ANONYMOUS_READ=true`
dev server on port 8823, no password entered): opening the `packages/` branch
(64 leaves) produced **14 groups**, not the designer's estimated "~8 groups of
~10" — worth reporting honestly rather than rounding to the prediction. Sizes
ranged from 2 to 14: most named-package groups landed at 2–8 members
(`packages/egeria-advisor` 2, `.../advisor` 5, `.../resource-explorer` 7,
`.../resource_explorer` 8, `.../surveyors` 4, `.../arch_recovery` 2, `.../web` 2,
`.../web/static` 2, `.../docs` 6, `.../dr-egeria` 5, `.../scripts/arch-spike` 2),
while the top-level `packages` bucket itself — files with no named-package home,
e.g. `trellis-auth`, `trellis-context` — held 14 loose entries that resisted
further subdivision, because re-deriving `scope_hierarchy` over just those 14
finds no shared prefix among them (each is already at minimum ancestor depth).
This matches what the live blueprints panel already showed for the same
`packages` cluster in the logical reading — flagged `⚠ oversized (target 10)`
at 11 members (a different count: that panel is logical-perspective-only) and
likewise left unsplit — confirming this is a genuine property of the corpus's
own directory shape, not a bug in the new grouping: `TARGET_CLUSTER_SIZE` bounds
a group only when there is further declared structure to read out of it.
Fully-decided groups (`resource_explorer`, `surveyors`, `arch_recovery`, `web`,
`web/static` — all-accepted from earlier verdicts) rendered closed by default,
as `<details>` with no `open` attribute; every group with any undecided member
rendered open. `node --check` passed on `curate.js`. `tailwind-next.css` was
grepped for every class introduced (`border-rule`, `text-provenance`,
`font-mono`, `pl-s3`, `cursor-pointer`, `py-\[3px\]`, etc.) — all were already
compiled from other uses in this file, so **no rebuild was needed**.

**Tests** — `tests/test_component_tree.py`, new `TestLeafGrouping` class (4
tests): siblings group under their shared ancestor and a lone leaf stays
ungrouped; group verdict counts reflect recorded verdicts (including
inheritance — accepting a parent accepts its already-nested child too, same
rule as the branch level); a branch with no qualifying group returns everything
ungrouped; an oversized first-pass group is subdivided the same way a
blueprint cluster is. Plus one new route test confirming `/components/leaves`
exposes `groups`/`ungrouped` alongside the unchanged flat `leaves`. Full suite:
`uv run pytest tests/ -q` — **5017 passed, 103 skipped, 0 failed**, run to
completion synchronously against this branch.

---

## Addendum — page-level section nav + collapsible sections (2026-09-17)

**Not a new numbered plan item.** The project owner tested Curate live
after this item shipped and reported: "one very long page with no table of
contents at the top, the sections are not collapsible." Branch
`re/curate-section-nav`, worktree `.claude/worktrees/wt-curate-toc`.
Nothing was written in the main checkout.

**Scoping note carried over from the brief:** classic's Curate panel has a
narrower cross-reference "jump to this specific component/blueprint"
feature (`_curateJumpTo`/`_curateComponentAnchorId`, index.html
~line 4248-4273) — not a page-level table of contents. There was no
existing page-nav pattern to port; this is built fresh, and kept
deliberately small: anchor links + smooth scroll + `<details>`, nothing
more.

### What changed in `next/stages/curate.js`

- **`CURATE_SECTIONS`** — the six section ids/labels the page now
  organizes around: `curate-sec-what-it-is`, `curate-sec-what-holds`,
  `curate-sec-made-of`, `curate-sec-blueprints`, `curate-sec-relates`,
  `curate-sec-writes`. Blueprints — previously nested inside the "what
  it's made of" column's markup alongside the component tree — is now its
  own top-level section with its own id, summary and nav entry; the
  component tree keeps its own section untouched.
- **`curateSectionNavHtml()`** — a `<nav>` of six anchor links, one per
  `CURATE_SECTIONS` entry, rendered once at the top of the pane (below the
  resource-header/population-rule paragraph, above "what it is").
  `sticky top-0 z-10` with a `bg-paper` backing (the same opaque-surface
  utility app.js already uses for its own floating panels) so it stays
  visible while the six sections scroll underneath it.
- **`bindCurateSectionNav()`** — click handler for the nav's anchors:
  prevents the default same-page-hash jump, opens the target `<details>`
  if it was collapsed, then scrolls with
  `el.scrollIntoView({ behavior: 'smooth', block: 'center' })` — the exact
  convention classic's own `_curateJumpTo` (index.html:4266) and its other
  jump sites (index.html:4874) already use, reused rather than reinvented
  for consistency.
- **`curateSectionHtml(id, title, extraHeader, inner)`** — the shared
  collapsible wrapper: `<details id="${id}" open>` /
  `<summary>{title}{extraHeader}</summary>`, matching `/next`'s existing
  disclosure idiom (see app.js's own `<details>` usage for survey
  definitions' "other stages" and the analyses index's "other stages",
  e.g. around app.js:3171/:3376). `title` is the section's existing
  heading text (unchanged from before this addendum); `extraHeader` carries
  the small provenance/count spans each section already had next to its
  heading (e.g. "N of M confirmed" on "what it is"). **Default open on
  every section** — the reported problem was missing navigation/structure,
  not too much visible at once, so collapse had to stay a viewer option,
  never a default-hidden state this feature imposes.
- `renderCurate`'s `draw()` now assembles the six sections through
  `curateSectionHtml`, `bindCurateSectionNav(host)` runs once per redraw
  alongside the pane's other event wiring.

**What did not change:** `renderComponentTree()`'s internal row/branch/leaf
logic. Another agent is building scope-hierarchy component-list grouping
against that same function concurrently, on `re/curate-tree-scope-groups`.
This addendum only wraps the *outer* "what it's made of" container that
`renderCurate()` itself assembles (the `<div id="component-tree">` mount
point) in the new `<details>` shell — nothing inside what
`renderComponentTree()` returns was touched, and `test_next_curate_section_nav.py`'s
`TestComponentTreeInternalsUntouched` pins that boundary so a later merge
conflict is loud rather than silent.

### Tests

**Frontend (static source)** — `tests/test_next_curate_section_nav.py`
(new), following the same static-source-holds-the-design-rules pattern:

- All six section ids are declared and the nav links/anchors all six.
- Blueprints is its own section, separate from "what it's made of" (the
  component-tree div does not appear inside the blueprints section's call
  site and vice versa).
- Every section is wrapped in `<details ... open>`/`<summary>` — collapse
  exists and defaults open.
- The nav's click handler calls `scrollIntoView({ behavior: 'smooth',
  block: 'center' })` and opens a collapsed `<details>` before scrolling to
  it.
- `renderComponentTree()`'s own body contains none of the new
  wrapper/nav markup — the boundary the concurrent tree-grouping work
  depends on.

8 new assertions, all passing. Existing suites unaffected —
`tests/test_next_curate_pane.py` and
`tests/test_next_curate_selection_and_blueprints.py` still pass unmodified
against the restructured `draw()` output (they assert on function bodies
and literal strings that this addendum did not move outside their scanned
ranges).

Full suite: `uv run pytest tests/ -q` — **5021 passed, 103 skipped, 0
failed**, read synchronously to completion from this branch.

### tailwind-next.css

Checked for staleness per the standing backlog item: `sticky`, `top-0` and
`z-10` (used by the new nav) were **missing** from the compiled
`tailwind-next.css` before this change. Rebuilt via
`npm install && npm run build:css:next` from `frontend-build/` (had to
`npm install` first — `node_modules` wasn't present in this worktree) and
committed the regenerated file. `npm install` also touched
`package-lock.json` (older local npm/Node re-resolving some transitive
entries); that lockfile diff was reverted before committing since it is
unrelated to this change and not needed for the CSS rebuild to take effect.

### Live verification

Throwaway dev server on port 8822 (`TRELLIS_ANONYMOUS_READ=true`, ports
8810/8811/8817/8818/8819/8820/8821 already in use), Curate pane opened for
`amundsen-io/amundsen` via "Continue without signing in" (no password
entered at any point). Confirmed directly in the browser:

- The nav renders at the top of the pane with all six labels, below the
  resource header/population-rule text and above "what it is".
- Clicking "blueprints" in the nav scrolled the page so the blueprints
  section centered in view (classic's convention, reused).
- Clicking the "blueprints" `<summary>` collapsed the section to just its
  heading; clicking again re-expanded it with its content intact.
- The nav itself stayed pinned at the top of the pane while scrolled deep
  into later sections (the `sticky` positioning holds).

**Limits:** verified as an anonymous-read session, not a fully signed-in
one — the Catalogue action itself and the mermaid diagram render (a
pre-existing, unrelated 401 on `/api/diagrams/mermaid` for anonymous
sessions) were not exercised, since neither is part of this addendum.
`node --check` passed on the touched file (via a temporary `.mjs` copy,
since the file itself has no `.mjs` extension and Node's CommonJS loader
otherwise rejects the `import` syntax the ES module uses).
