# Stale evidence rail -- implemented (branch `re/stale-rail-clear`, 2026-10-01)

Fixes `docs/Backlog.md` "Stale evidence rail outside the Context tab". Owner screenshot on 8810: viewing
`egeria_git`, the rail read "for laz_local_adventureworks".

Environment check (before any test):
`uv run python -c "import resource_explorer; print(resource_explorer.__file__)"` ->
`/Users/dwolfson/localGit/egeria-v6/trellis-re-stale-rail/packages/resource-explorer/resource_explorer/__init__.py`
(inside the worktree).

## Root cause

`#rail-evidence` is one slot with five writers: `railFrame` (`next/app.js` ~2720; used by `showEvidence`,
`openMembers`, curate's Ports/Members), `openMembers`' two direct `out.innerHTML` writes (app.js ~5700-5730),
and `renderEnrichmentEvidence` / `renderEnrichmentEvidenceLoading` (`stages/enrichment.js` 316, 737).
Only `renderContext` (`stages/context.js` 240-251, `_railFactsSlug`) cleared it, and only on a slug change
*while rendering Context*. Nothing cleared it on a stage click (app.js 974), sub-tab click (`bindSubTabs`),
sidebar resource click (`renderSidebar`) or URL restore -- only the rail's own "close". `railClaim()` tickets
protected writers from each other, never from the selection moving.

The ASK header ("scoped to <slug>") derives from `state.selectedSlug` via `renderRailScope()`, but that was
called from only four call sites; the evidence frame's "for <slug>" derives from the writer's own `slug`
argument and was never compared with the selection. So it could name any resource.

## Fix (one place)

`next/app.js`: `railKeyOf()` = (resourceType, selectedSlug, stage, subTab, workListSlug);
`syncRailToSelection()` -- when the key changed it takes a rail ticket (in-flight writers for the old key
stand down), empties the slot, drops its tag, and re-renders the ASK scope line. Called at the top of
`loadPane()` (every stage/sub-tab/resource/URL/delete path goes through it), again after
`reconcileSubTabForStage()` and the retired-`dashboard` alias. Same key => no-op, so a rail deliberately open
on this resource's evidence survives a same-pane re-render (e.g. a Context save).
`railTag(out, forWhat)` stamps `data-rail-for` / `data-rail-key` on every write (railFrame, openMembers'
direct writes, both enrichment writers).
`stages/context.js` `renderContext`: remembers `railKeyOf()` at entry and returns after its awaits if the key
moved (the old guard checked only the slug, so a Context fetch landing after a *stage* change on the same
resource wrote the rail on Discovery); shows the loading frame when the rail is empty on re-entry, so the slot
is never blank.
`renderSidebar` gained `export` (no logic change; same precedent as the README's four earlier exports) so the
harness can click the real sidebar.

## Evidence

Tests: `frontend-build/test-harness/rail-clears-on-change.test.mjs` (18). Real app.js, real writers
(`openMembers`; the Questions pane's real "evidence" link -> `showEvidence`; the real Context render), real
clicks on sidebar / nav / sub-tab buttons.
- On main (+ only the `renderSidebar` export): 12 fail, 6 pass. Fails: members/evidence x {resource, stage,
  sub-tab} change (6), Context leave-Enrichment, late members response, late Context render after a stage
  change, question evidence after B loads, ASK/evidence header, tag.
  Passing on main by design: Context slug change (unchanged behaviour), Context leave-and-return, staying on A
  (x2), two known-negatives.
- With the fix: 18/18. Full harness: 210/210 (was 192). `node --check` on .mjs copies of the 3 edited JS
  files: ok. `.venv/bin/python -m pytest tests/test_next_*.py` (PGVECTOR_PORT=1, temp SQLite
  REGISTRY_DATABASE_URL): 660 passed.
- Known-negatives: rail never opened stays empty across a resource change; the assertion helper throws on text
  naming A.

## Other shared screen-state slots (same bug shape) -- NOT fixed here

- `state.runsInFlight` / `pendingProposals` / `autoRanNotes` (app.js 231-253; read 6823, 7401, 7481): keyed by
  question TEXT, not slug, and not cleared on resource change (only `state.answers.clear()` at app.js ~7199).
  A run in flight for A's "Q1" shows B's identical "Q1" row as running. AFFECTED (read from code, not
  reproduced). Not a one-line fix.
- `loadAnswer` (app.js ~7764-7785): late answer guarded by slug only; replaces row `qrow-<i>` by INDEX
  (`replaceRow`, `rowKey` 7303). A response from one stage's Questions pane landing after a same-resource
  stage change can write into the new pane's row i. AFFECTED (read from code, not reproduced).
- `state.analysisRunFailures` is keyed `${slug}|${analysisId}`: not affected.
- `state.overview` (7082): guarded by slug on both set and late landing: not affected.
- Doc-sources poll (`stages/enrichment.js` 548-566): slug-guarded, stopped on re-render: not affected.
- RFA drawer `_scopeSlug` (`next/rfa.js` 63, 249): stays on the resource it was opened for, but its label says
  "this resource only (<slug>)": honest, not affected.
- Chat transcript (`state.chat`): turns are labelled with their own resource by design: not affected.
- `state.promoted`: only a pointer; the promoted pane is replaced by loadPane: not affected.
- `state.enrichment` / `contextAnswers` / `_humanQuestions` (context.js 201, 296): re-fetched per Context
  render, written only after the slug+key guard: not affected.
- `stages/investigation.js` `_detailSlug` (45): an investigation slug, not a resource: n/a.

## Not verified

- No real browser render (no screenshot of 8810); jsdom only. CSS-hidden / closed-drawer states are not
  exercised beyond `ensureRailShowing`.
- Curate's Ports/Members rail writers (`stages/curate.js` 443, 773) share `railFrame` and are covered by the
  same sync, but have no dedicated test.
- The retired `dashboard` alias sync line has no test.
- Live server behaviour (real latencies) not exercised; fetches are stubbed.
