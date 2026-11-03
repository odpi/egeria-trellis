# Item 11 — Discovery, Assessment, Analysis: implemented (real, deliberately partial)

**Replies to:** `PLAN-FINISH-REPOS.md` item 11 — *"Discovery, Assessment,
Analysis — not started (added by the owner, 2026-09-17). Done when: each of
the three shows its real catalogued questions/analyses in /next instead of
the 'not built' placeholder, and Discovery's classic-only
org-import/repo-search/`/from-list`/CSV-export/disposition-write endpoints
are reachable there too (or each is explicitly, individually deferred)."*

**Branch:** `re/discovery-assessment-analysis-stages`. Worked from the
worktree the coordinator assigned by name (`wt-das`); a launch mistake
auto-created a second, isolated worktree for this session
(`agent-aa92f5d67c965af06`) whose git operations this session's sandbox
would not let it redirect elsewhere — see "How this reached the branch"
below for exactly what that means for where the commits actually came from.

---

## Verifying the plan's own assessment before relying on it

The plan entry cites a same-day investigation's two claims. Both checked out
against the actual code, not just the summary:

1. **"Assessment and Analysis have no bespoke UI or backend in classic at
   all."** Confirmed: `index.html`'s `showAssessmentPane()`/
   `_assessmentSubnavHtml()` and `showAnalysisPane()`/`_analysisSubnavHtml()`
   are the same generic catalog-card/dashboard shell Discovery also reuses,
   backed by the same generic `web/routes/analyses.py`, pointed at
   `intent: assessment` / `intent: analysis` catalog entries
   (`analysis_catalog.yaml`). Analysis's one extra is the Sub-Resources
   sub-tab (see below).
2. **"Discovery carries real, dedicated backend logic with no /next
   equivalent."** Confirmed: `web/routes/discovery.py` (774 lines) has org
   import (`_expand_org`), repo search (`/search`), a bulk `/from-list`
   loader, `/inventory.csv` export, and `set_repo_disposition`
   (`POST /disposition`) — none of the first four have anything in `/next`.
   The fifth, disposition, **already did** — see below, this was the one
   correction to the plan's framing worth making explicit.

## The one thing the plan's framing got slightly wrong

The plan describes Discovery's disposition-write endpoint as needing
"porting work for parity". It doesn't. `/next`'s Disposition sub-tab
(`loadDispositionPane()`, `app.js`) calls `setDisposition()`
(`re-api.js`), which already posts to `POST /api/discovery/disposition` —
`discovery.py`'s own `set_repo_disposition` — and has since before this
item, because the Disposition sub-tab is resource-scoped chrome shared by
every stage, not stage-specific code. It was unreachable only because
Discovery was `built: false`, which gated the sub-tab buttons themselves
(`subTabsHtml()`'s "live only when the tab AND the current stage are
built" rule) — not because the write path was missing. Flipping `built:
true` was the entire fix for that piece.

## What "built" required, verified against the actual generic engine

`loadPane()`'s Questions-checklist engine (`app.js`) is parameterised by
`phase` and was already the mechanism Scouting/Enrichment/Curate reach
through with no per-stage branch. Before touching `STAGES`, this item
confirmed, by reading the code (not by assuming the plan's summary):

- `question_catalog.yaml` carries real, non-empty question sets tagged
  `stage: Discovery` (10), `stage: Assessment` (15) and `stage: Analysis`
  (11) — `question_catalog_reader.get_questions()`'s `phase` filter matches
  case-insensitively against that field, slash-combined stages included.
- `analysis_catalog.yaml` backs those with 10 `intent: discovery`, 15
  `intent: assessment` and 11 `intent: analysis` entries.
- `rowShell`/`rowInner`/`bodyLines`/`provenanceLine` (the row-rendering
  chain) have no stage conditionals at all — every state (`answered`,
  `unrun`, `human`, `gap`, `no-surveyor`, `automatic`, `unclassified`,
  `running`, `error`) is derived from the answer envelope and the running-
  runs map, not from `state.stage`.
- The sub-tab gate (`subTabsHtml()`) already reads `stageDef?.built`
  correctly (fixed by item 0, `UNBUILT-STAGES-IMPLEMENTED.md`) — so marking
  the three `built: true` was sufficient to make all four of their sub-tabs
  (Questions, Survey & analyses, By analysis, Disposition) live, not just
  Questions.

This is why the fix for Discovery and Assessment really is "two words
each" (`built: true`) with **zero** new rendering code: unlike Understanding
and Automate (which bypass the Questions engine for charts/subscriptions)
and unlike Curate (which renders even with zero catalog rows), Discovery
and Assessment have real catalog rows and no reason to leave the generic
path. `next/stages/discovery.js` and `next/stages/assessment.js` stay
`export {};` stubs — not because they were skipped, but because they
genuinely have nothing stage-specific to hold.

## Analysis: the one real difference

Analysis also needs nothing beyond `built: true` for its Questions
checklist — but classic's Analysis pane has one extra sub-tab, "Sub-
Resources" (`index.html`'s `loadAnalysisSubResourcesView` and about 380
lines of supporting code: candidate sub-resource selection, cataloguing,
and dispatching scoped analysis runs against them). That is a real,
separable, non-trivial feature — larger than a two-line honest-placeholder
fix and with its own selection/cataloguing UI state that does not fit
inside a Questions-checklist row.

**Decision (this item, 2026-09-17):** defer Sub-Resources by name rather
than port it or silently drop it. Porting it would mean either adding a
fifth sub-tab (which breaks the "`SUB_TABS` order is IDENTICAL across every
stage" rule `app.js` documents and relies on) or building a second,
differently-shaped mount for one stage only, either of which is more than
this item's scope of "port the stage, not rebuild every classic Discovery/
Analysis feature." `next/stages/analysis.js` exports one function,
`renderAnalysisNote(slug)`, called from `loadPane()` only when
`state.stage === 'analysis'`, alongside (not instead of) the generic
question rows — it writes one honest sentence naming Sub-Resources
specifically and links out to classic via `oldUiHref()`, into the
`enrichment-form` mount point `curate.js` already established as the
generic "per-stage extra content" slot.

## What was built

- **`app.js`**: `discovery`, `assessment` and `analysis` marked
  `built: true` in `STAGES`. One new call,
  `if (state.stage === 'analysis') renderAnalysisNote(slug);`, placed
  identically to Curate's own stage-specific call (before the
  empty-question early return, so it runs alongside real question rows
  rather than gating them). No new branch was added for Discovery or
  Assessment — the done-test for this item was specifically that none was
  needed.
- **`next/stages/analysis.js`**: `renderAnalysisNote()` — the one honest
  Sub-Resources deferral note described above.
- **`next/stages/discovery.js`, `next/stages/assessment.js`**: header
  comments updated to record why they stay empty (verified fact, not
  unexamined stub) — `export {};` unchanged.
- **Stale-comment fixes, caused by this change**: `automate.js`'s and
  `app.js`'s own comments about the "Notify me" gap (item 4) said
  "Assessment or Analysis... don't exist in /next yet." That became false
  the moment these three stages were marked built, even though the
  underlying gap (no card grid for a "Notify me" button to sit on) is
  unchanged. Both comments were corrected to say what is actually still
  missing — Automate's own create-subscription behavior was not touched.

## What was decided NOT to build, named individually

| Classic feature | Where | Decision | Why |
|---|---|---|---|
| GitHub org import (`_expand_org`) | `discovery.py` | **Deferred** (pre-existing) | Corpus-level, not resource-scoped; already covered by the sidebar's "Find repos" deferral (`app.js`'s `find-repos` action, `SPEC-ACTIONABLE-AND-HONEST.md` point 2) added before this item — this item did not need to duplicate it inside a Discovery-stage pane |
| Repo search (`POST /search`) | `discovery.py` | **Deferred** (pre-existing) | Same as above — same dialog, same link out |
| Bulk `/from-list` loader | `discovery.py` | **Deferred** (pre-existing) | Same as above |
| `/inventory.csv` export | `discovery.py` | **Deferred** (pre-existing) | Same as above — a corpus-wide export has no natural home inside a single-resource Questions checklist either |
| Disposition write (`POST /disposition`) | `discovery.py` | **Already built, before this item** | The Disposition sub-tab already called this route; only needed `built: true` to be reachable — see above |
| Sub-Resources (`loadAnalysisSubResourcesView`) | `index.html` | **Deferred, named** (this item) | Real, separable, ~380-line feature with its own selection/catalogue UI; would need either a fifth sub-tab (breaks the uniform-strip rule) or a bespoke mount; named and linked out via `renderAnalysisNote()` rather than silently dropped |

The first four were not re-deferred by this item because they were already
honestly marked "not built in /next" by the sidebar's pre-existing "Find
repos" affordance — this item's job was to confirm that coverage still
applies to Discovery specifically (it does: the dialog says "Repo discovery
— find and import candidate repos" and links to classic) rather than to
build a second, Discovery-specific deferral notice that would say the same
thing twice.

## Verification

**Tests** (`tests/test_next_discovery_assessment_analysis_stages.py`, 15
tests, all passing): all three stages marked `built: true` and Investigation
still correctly unmarked; `loadPane()` has no special-case branch for
Discovery or Assessment (pinning that the generic engine alone serves them);
Analysis's one note call is present, positioned correctly, and does not
bypass the generic rows; `discovery.js`/`assessment.js` stay empty stubs;
`/next`'s `setDisposition()` posts to the real `discovery.py` route and that
route exists; the Sub-Resources note names the feature specifically and
links out; the four corpus-level Discovery endpoints this item deliberately
did not re-defer still exist in `discovery.py` and the sidebar's "Find
repos" deferral still covers them; and the question/analysis catalogs still
carry real, non-zero row counts for all three stages (a regression guard —
if these ever drop to zero, `built: true` would be showing an empty
checklist, the exact honesty violation the unbuilt marker exists to
prevent).

**Static/syntax:** `node --input-type=module --check` against every
changed file (`app.js`, `next/stages/analysis.js`,
`next/stages/discovery.js`, `next/stages/assessment.js`,
`next/stages/automate.js`) — all parse cleanly as ES modules.

**Full suite:** `uv run pytest tests/ -q` run to completion from this
worktree. See this item's report for the exact pass count.

**Live, unauthenticated:** started a throwaway dev server
(`uv run --package resource-explorer resource-explorer web --port 8818`,
not port 8810/8811/8817, none of which any other running instance on this
machine uses) and loaded `/next` in the browser. Confirmed
`/static/next/stages/analysis.js` (and every other changed/existing static
asset) served `200 OK`, and that loading `/next` produced zero console
errors. The app requires Egeria sign-in for every non-public path
(`docs/runtime-architecture-plan.md` §4); this session cannot type a
password into any field, and clicking "Continue without signing in" did not
reach an authenticated session on this dev box (no `TRELLIS_ANONYMOUS_READ`
override set). **The Discovery/Assessment/Analysis panes themselves —
loading a real repo's checklist, running an analysis, setting a
disposition — were NOT exercised live against a signed-in session,** the
same constraint `ITEM-5-ADMIN-IMPLEMENTED.md` and
`ITEM-9-CHAT-IMPLEMENTED.md` recorded under. What was confirmed is that the
server starts and serves the new/changed modules without error, and that
the client boots to the sign-in screen with no console errors. The
throwaway server was stopped after this check.

## How this reached the branch

This session was launched with worktree isolation (`isolation: "worktree"`)
by mistake — it auto-created its own worktree/branch
(`agent-aa92f5d67c965af06` / `worktree-agent-aa92f5d67c965af06`), separate
from the `wt-das` worktree the coordinator set up on purpose for this item.
The coordinator corrected this mid-task and asked for all work to land on
`wt-das`'s branch (`re/discovery-assessment-analysis-stages`). Both
worktrees started from the identical commit (`bf9315a8`), so all edits in
this document were made directly against that shared starting point via
this session's own worktree, since its sandbox refuses any `git` invocation
that names or redirects to a path outside `agent-aa92f5d67c965af06` — `git
-C`, a prior `cd`, and even a plain `git status` after `cd`-ing there were
all refused with the same message. The commit was made in this worktree and
pushed with a refspec directly onto `origin/re/discovery-assessment-
analysis-stages` (`git push origin HEAD:re/discovery-assessment-analysis-
stages`) so the remote branch — which is what the coordinator's PR will
actually be opened from — carries the same file content `wt-das` would have
produced. No git operation of any kind was run against `wt-das`,
`/Users/dwolfson/localGit/egeria-v6/trellis`, or any other worktree from
this session.

## What's still open

- Sub-Resources (`loadAnalysisSubResourcesView`) — a real follow-up with its
  own design pass (does it need a fifth, Analysis-only sub-tab, or does the
  uniform-strip rule need revisiting first for a case like this one).
- The four corpus-level Discovery endpoints (org import, repo search,
  `/from-list`, CSV export) — still only reachable via the sidebar's
  generic "Find repos" link into classic, unchanged by this item.
- Live, signed-in verification of all three stages' actual data flow —
  whoever has Egeria credentials for a dev instance should load `/next`,
  sign in, select a repo, switch through Discovery/Assessment/Analysis, and
  confirm real question rows render, run, and answer correctly, and that a
  Discovery disposition write round-trips, before this is treated as
  browser-verified rather than statically-verified.
