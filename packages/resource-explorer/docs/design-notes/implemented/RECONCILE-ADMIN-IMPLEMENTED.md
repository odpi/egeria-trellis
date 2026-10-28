# Reconcile admin (Resync + Repair) — implemented

**Spec:** `SPEC-ADMIN-THE-FOUR-GAPS.md` §1 ("Reconcile is two panels in
classic, not one"). Read against `main` at `98c06bb9` (branched
`re/admin-reconcile` from there); this note records what shipped and where
it lives in `/next`.

## What shipped

Two separate panes, per the spec's correction — not one merged screen:

- **`resource_explorer/web/static/next/admin/resync.js`** — Egeria
  Alignment (global drift), a port of classic's `loadAdminResyncPanel`/
  `_applyResync` against the already-complete backend
  (`resource_explorer/egeria_resync.py`'s twelve scanners, nine repair
  actions, `SAFE_SCHEDULED_STEPS`).
- **`resource_explorer/web/static/next/admin/repair.js`** — per-repository
  correction, a port of classic's `loadAdminRepairPanel`/`selectRepairRepo`/
  `renderRepairRepoDetail` against the already-complete backend
  (`resource_explorer/repair.py` + `web/routes/repair.py`, mounted at
  `/api/admin/repair`).

`admin/index.js`'s `admin-resync` ("🔄 Egeria Alignment") and `admin-repair`
("🔧 Repair") entries moved from `defer` to `render`, matching the existing
`/next` Admin `GROUPS`/tabs pattern — no id or label changed.

## Resync: the three shapes, not one generic row

The whole design constraint was not flattening `Finding.repair_step`/
`needs_decision` into "a row with a button." `resync.js` renders three
distinct shapes:

1. **Already scheduled** (`SCHEDULED_STEPS` = `clear_stale_assets`,
   `clear_orphan_publish_claims`, `flag_vanished_publishes` — mirrors
   `egeria_resync.py`'s `SAFE_SCHEDULED_STEPS`). The row shows a state dot
   (clean/drifted) and current count, with the copy explicit that this
   reports what a pass would find right now, not something waiting on the
   user — plus a **"Run now"** button (disabled when already clean) rather
   than a tick-and-apply affordance.
2. **Repairable, not scheduled** — a checkbox (unticked by default when
   `expensive`, per the existing `Finding.as_dict()` field) and a single
   **Apply selected** button. The confirmation before applying is built from
   a per-step `BLAST_RADIUS` map that names what each selected step touches,
   how many (from that finding's own `count`), and whether it reverses —
   never one sentence covering multiple different repairs.
3. **`repair_step: ""`** — no button, ever. When `needs_decision` is true the
   row carries a **"your call ›"** badge instead of a fix affordance, per
   `SPEC-ACTIONABLE-AND-HONEST.md`'s "a judgement is not a task" — this
   covers `unpublishable`, `local_investigations`, and `specification_gap`,
   the three findings the backend already marks `needs_decision=True`.

**`clear_stale_investigations` gets its own explicit warning**, not folded
into the generic per-step template: `egeria_resync.py`'s own comment above
`SAFE_SCHEDULED_STEPS` calls an unlabelled button for this step "the most
dangerous control in the product" (it can unbind a GUID a person
deliberately bound to a real Egeria Project, not only one this app created).
`BLAST_RADIUS.clear_stale_investigations` says so in capitals, names the
count, and says it is not reversible by this app. `clear_stale_contexts`
gets the same treatment for the same reason (same binding-risk class, per
the module's own comment).

Reachability and the private-zone card are ported unchanged in behaviour:
an unreachable Egeria is never rendered as "no drift" (`d.reachable` gates
the whole panel before any finding renders), and the four private-zone
states (enforced / settling / not enforced / — no card when the check
itself failed) render as differently as classic's.

## Repair: blast radius, including what it does NOT do

Per §0's rule and its sharpest instance — classic's *"This does not delete
your files on disk"* — every mutating action's confirmation or inline copy
in `repair.js` names what it does, checked against `resource_explorer/
repair.py`'s own docstrings rather than assumed, and names what it does
**not** do where the action's name reads more destructive than it is:

- **Rename** — touches only RE's registry rows and this repo's own pgvector
  collections; confirmation says it does not touch GitHub or Egeria.
- **Change github_url** — drops this repo's existing collections (count
  named, from the repo's own `collections` field) and marks it needing a
  re-index; says it does not touch GitHub itself and does not delete the
  repo's registry row.
- **Enable collection** — framed as additive; no confirm (matches classic —
  it ingests one more collection type, touching no existing one).
- **Drop / repoint membership** — the drop confirmation explicitly says this
  only removes the repo from *that investigation's* Folio, not from RE's
  registry or any other investigation it belongs to, and does not touch
  Egeria.

## API surface added (`re-api.js`)

No new backend routes — both panes' backends were already complete and
mounted (`/api/egeria/resync/scan`, `/api/egeria/resync/apply`, and all six
`/api/admin/repair/repos/...` routes), confirmed by reading
`web/routes/egeria.py`'s resync block and `web/routes/repair.py` in full,
and `web/app.py`'s `app.include_router(repair.router, prefix="/api/admin/repair")`.
Client-side, `re-api.js` gained: `getPrivateZone`, `getResyncScan`,
`applyResyncSteps`, `repairRename`, `repairGithubUrl`,
`repairEnableCollection`, `getRepairDrift`, `getRepairMemberships`,
`repairRepointMembership`, `repairDropMembership`. `listInvestigations` grew
an optional `{ includeClosed }` param (defaulting to the old no-arg
behaviour) since Repair needs closed investigations in its repoint target
list, same as classic's `include_closed=true` fetch; its one existing
caller (`next/app.js`) is unaffected by the default.

## One noted gap — not built, per the task's instruction not to add backend surface unasked

`egeria_resync.get_status()` (last scheduler run time/outcome, consecutive
failure count) has **no HTTP route** — nothing in `web/routes/` calls it.
The "Already scheduled" row's "current state" is drawn entirely from the
live scan (`Finding.count`), which is honest and sufficient for what the
spec asked (state, not history), but a `GET /api/egeria/resync/status`
route mirroring `bootstrap.py`'s `GET .../status` → `bootstrap_mod.
get_status()` pattern would let that row also show *when the scheduler last
ran and whether it succeeded* — closer to Prefect's own status pane.
Separately, `Finding.as_dict()` already has an `"expensive"` boolean
computed server-side so the frontend need not duplicate `EXPENSIVE_STEPS`;
an analogous `"scheduled"` boolean (against `SAFE_SCHEDULED_STEPS`) would
remove `resync.js`'s own hardcoded `SCHEDULED_STEPS` set, which will drift
silently if that backend constant ever changes. Neither is built here —
flagged for a decision, not assumed.

## Tests

`tests/test_next_admin_pane.py` (existing pattern for `/next` admin panes —
grep/slice function bodies out of the concatenated source, no browser):
moved `admin-resync`/`admin-repair` from `DEFERRED_TAB_IDS` to
`BUILT_TAB_IDS`, and added `TestResyncPane`/`TestRepairPane` covering: the
real routes are read; unreachable never renders as clean; scheduled rows
carry no checkbox; decision rows carry no button and are framed as "your
call"; `clear_stale_investigations`'s confirmation names the unbind;
`applySelected` confirms before writing; expensive steps default unticked;
Repair's destructive actions are confirmed and name what they do not touch;
the two panes' own header comments assert they are not folded together.

Backend routes/scanners/repairs were already covered by
`tests/test_egeria_resync*.py` (five files) and `tests/test_repair.py` —
confirmed present rather than assumed, per the task's instruction; no new
backend tests were added since no backend surface changed.

`uv run pytest tests/ -q` — full suite, 0 failed (see PR for the exact
count at the commit this shipped).

## Live verification

Not exercised against a live signed-in session in this pass — no browser
tool was available to this agent. `ITEM-5-ADMIN-IMPLEMENTED.md`
records the same limitation for the original Admin port; this is a static
review only (source read in full, cross-checked against the live routes'
Python source and the existing classic UI's exact behaviour). Flagging
rather than claiming otherwise.
