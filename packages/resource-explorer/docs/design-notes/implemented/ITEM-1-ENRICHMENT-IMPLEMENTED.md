# Item 1 — Enrichment: verified and hardened

**Replies to:** `PLAN-FINISH-REPOS.md` Part 3, item 1 — *"Enrichment —
partially built. Owns `next/stages/enrichment.js`; Python for the judgement
fields. Done when: a curator can record and revisit an enrichment judgement
on a repo, and a judgement whose evidence moved says so. Needs: Part 2 (the
`app.js` split — already done, merged to main)."*

**Branch:** `re/enrichment`, worktree `.claude/worktrees/wt-enrichment`.
Nothing was written in the main checkout.

---

## What was already there

More than the plan's "partially built" suggested. `next/stages/enrichment.js`
(moved out of `app.js` by the Part 2 split, already on main) had real,
working logic for both halves of the done test:

- **The judgement/observation model** (`JUDGEMENTS`, `OBSERVATIONS`), each
  field saving alone through `PATCH /api/context/{type}/{slug}/field`
  (`context.py:save_field`), server-stamping `author`/`set_at` from the
  signed-in identity and refusing anonymous writes with 401.
- **The perishability mechanism** — `evidenceSnapshot()` captures every
  enrichment fact's `last_run_at` at save time into the judgement's
  `evidence` map; `movedSince(field)` compares that snapshot against
  `state.enrichmentFacts` (fetched fresh on every render via
  `getBulkFacts`) through `whenMs`, which normalizes the three timestamp
  spellings the registry writes (`Z`, `+00:00`, naive).
- **Revisit on load** — `app.js`'s `loadPane()` already fetched
  `getContext('repo', slug)` and assigned `ctx.enrichment` to
  `state.enrichment` *before* calling `renderEnrichment(slug)` when
  `state.stage === 'enrichment'`, so a reload does show prior judgements,
  not a blank form.
- The rail (`renderEnrichmentEvidence`), the licence confirm-not-apply
  affordance (`proposedFrom`, gated on the `license_risk_tier` finding), and
  the interim-owner action were all real and already covered by
  `tests/test_next_enrichment_fidelity.py`.

So this item's Python persistence and JS perishability mechanics were not
speculative — they were built and largely correct. What was missing was
**verification that they actually compose end to end**, and one real defect
that verification surfaced.

## What I found and fixed

**A real data-loss bug in `save_context` (`web/routes/context.py`).**
`POST /api/context/{entity_type}/{slug}` (`save_context`) built its stored
document from `ContextData.model_dump()`. `ContextData.enrichment` defaults
to `{}` when a request body omits it — and the classic `/` page's Context
form (`saveContextForm` in `index.html`) *always* omits it: it only ever
sends its own fixed fields (`environment`, `org_owner`, `sensitivity`, …).
The next time a curator saved that classic form, the whole-document POST
would silently overwrite `enrichment` with `{}`, erasing every judgement
recorded through `/next`'s per-field `PATCH` — a "revisit" that shows a
blank form is exactly the failure item 1's done test rules out.
`question_answers` (the seven Human-Supplied catalog questions) had the same
exposure for the same reason.

Fixed by reading the raw request body and only replacing `enrichment` /
`question_answers` when the caller's JSON actually names the key; otherwise
the value already on record is kept. A caller that means to clear one still
can, by sending `{"enrichment": {}}` explicitly — now present in the body,
so still honoured. `saveQuestionAnswer` (`re-api.js`) was already
unaffected in practice, because it does its own read-modify-write and
spreads the full prior context (including `enrichment`) back into its POST
body — but the route itself had no such protection for any other caller,
present or future.

This is the one functional change. Nothing else needed fixing: the
judgement/observation split, the perishability comparison, and the
load-before-render ordering were already correct.

## What I verified, and how

**Python persistence and revisit, through the real HTTP routes** (not a
same-process registry read) — `tests/test_enrichment_fields.py`,
`TestReviseAfterReload` (new):
- a judgement saved via `PATCH .../field` is readable back through
  `GET /api/context/repo/{slug}` with its value, author, and evidence intact;
- a second field saved by a different person does not blank the first on
  the next `GET`;
- the classic Context form's save shape does **not** erase enrichment
  (regression for the bug above);
- a caller that explicitly sends `{"enrichment": {}}` can still clear it.

All 11 pre-existing `test_enrichment_fields.py` cases plus the 4 new ones
pass.

**The evidence-moved mechanism, executed, not just read** —
`tests/test_next_enrichment_persistence.py` (new). `movedSince` and
`evidenceSnapshot` are private to `stages/enrichment.js` and were not
exported; their source is extracted from the module and run under Node
with a minimal `state`/`whenMs` stand-in (the same no-browser approach
`test_next_curate_pane.py` and `test_next_rail_states.py` use for
structural checks, extended here to execute the extracted function bodies
rather than only pattern-match them):
- nothing is flagged when `state.enrichmentFacts` matches the snapshot;
- an analysis that re-ran after the judgement's `evidence` snapshot **is**
  flagged, by analysis id;
- a fact that reran but was never part of *this* judgement's evidence does
  not leak into its flag (per-judgement, not global);
- a naive vs. zoned spelling of the same instant does not false-positive;
- `evidenceSnapshot()` only snapshots facts that actually have a
  `last_run_at` — a fact object with no run recorded is not synthesized
  into "evidence that existed."

Two structural pins on top: the Save handler for a judgement sends
`evidenceSnapshot()` (not `{}` or a stale value) as `evidence`, and
`renderEnrichment` populates `state.enrichmentFacts` from the real
`getBulkFacts` call *before* building the rows that call `movedSince` off
it — so the flag is checked against live facts, not whatever the previous
render happened to leave in `state`.

`tests/test_next_enrichment_fidelity.py` (existing, untouched) continues to
cover the timestamp-spelling equivalence itself and that every
Human-Supplied catalog question reaches the Enrichment stage.

**Full suite:** `uv run pytest tests/ -q -k "not Postgres"` —
**4837 passed, 103 skipped, 18 deselected, 0 failed** (18m31s). No
regressions from either change.

## What I could NOT verify

- **In a running browser against this branch's code.** The dev-server
  launcher available to this session (`~/.claude/launch.json`) is scoped to
  the **main checkout** on port 8810 — the shared, currently-running app
  this repo's `CLAUDE.md` says never to restart or redeploy from a worktree
  session. Starting a background server process directly (bypassing that
  launcher) was refused by this session's own permission classifier. Port
  8811 (`re-next` in that same launch config) had nothing listening to
  attach to. So the save → reload → see-prior-judgement round trip, and the
  "⚠ review — evidence moved" text actually appearing in the rendered rail,
  were traced through the code and pinned by the Node-executed tests above,
  **not watched in a browser**. The fragile points to check first once this
  merges and the main checkout fast-forwards: (a) that `getContext` really
  returns the previously-PATCHed `enrichment` map through the live registry
  (Postgres in that deployment, SQLite in the fixture-backed tests here —
  same `ProjectRegistry.get_context`/`save_context` code path, but not the
  same database engine), and (b) that the rail's "new since you judged"
  sentence (`renderEnrichmentEvidence`, a related but separate freshness
  signal from `movedSince`) reads correctly once real survey re-runs
  produce a later `last_run_at`.
- **Postgres-backed enrichment tests specifically.** `test_enrichment_fields.py`
  uses a throwaway SQLite `ProjectRegistry`, matching that file's existing
  convention; the task's own suite-run instruction excludes `-k "not
  Postgres"`. The route code (`save_field`/`save_context`/`get_context`) is
  identical regardless of backend, but the Postgres-specific test classes
  were not added or exercised here.
