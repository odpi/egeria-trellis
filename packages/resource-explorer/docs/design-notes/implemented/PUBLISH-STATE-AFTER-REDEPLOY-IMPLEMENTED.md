# Implemented: publish state after a redeploy

**Replies to:** `SPEC-PUBLISH-STATE-AFTER-REDEPLOY.md`,
`PUBLISH-STATE-AFTER-REDEPLOY-CORRECTIONS.md`, `REPLY-PUBLISH-STATE-GO-AHEAD.md`
**Shipped in:** `8758f248` ("RE: publish state resolves against Egeria and
flags, never deletes, what vanished"), Wed Sep 16 21:02:12 2026 — **before**
`INBOX.md` itself was written (2026-09-17) and before this reply document
existed. This is the exact gap `INBOX.md`'s own "Why this kept happening"
section describes: no `*-IMPLEMENTED.md` was written at ship time, so the
board kept listing "Stale publish" as ready-to-start three days after it
shipped.

---

## What was asked for, and what shipped

The go-ahead's steps 2–3 (step 1, the `investigations`/
`entity_egeria_project_context` origin-blind-clear fix, shipped separately in
`#111` — out of scope for this doc):

- **A resolve-against-Egeria check, reusing the existing tri-state resolver.**
  `egeria_resync.py`'s `_scan_vanished_publishes` (around line 341) is the new
  scan: for each project's newest `project_egeria_surveys.egeria_report_guid`,
  it calls `ClassificationExplorer.get_element_by_guid(guid,
  graph_query_depth=0)` — a minimal-depth call, reusing `_resolves()`'s
  existing `True`/`False`/`None` tri-state check (including the
  `isinstance(result, str)` branch both corrections/go-ahead docs called out
  as the detail worth getting right) rather than a new resolver, exactly as
  §2 of the go-ahead asked.
- **Flag, never delete.** `_do_flag_vanished_publishes` (around line 993)
  writes the result to `egeria_linkage_status` with
  `entity_type='repo_publish'` — the same flag-don't-delete table and pattern
  already in production for stale asset links (`stale_guid`,
  `mark_egeria_linkage_stale`) — and self-heals the flag once a project's
  latest publish resolves again (`clear_egeria_linkage_status` when a later
  pass resolves clean). No schema change; no new table.
- **The orphan-claims job gated, per the go-ahead's §2 refinement, not
  dropped.** The go-ahead's sharper read was that `_scan_orphan_publish_claims`
  /`_do_clear_orphan_publish_claims` conflates two conditions — "locally
  unmoored" (never coherent, safe to clear) vs. "published, then vanished"
  (the only evidence a publish happened, must be flagged not cleared). The
  shipped commit's message states these were already disjoint conditions
  (`clear_orphan_publish_claims` only ever deletes rows with no local record
  of the report at all) — this scan adds the check for the one condition
  `clear_orphan_publish_claims` could never see, rather than needing to gate
  one job behind the other. `_do_flag_vanished_publishes` is itself in
  `SAFE_SCHEDULED_STEPS`: it only ever writes a flag, so a false positive
  costs one wrong badge until the next pass, never a silently-unmade
  decision.
- **The 4th state wired into every surface that shows the first three** — per
  §3 of the original spec and the go-ahead's closing note that this is "the
  honesty half of `RULING-CLASSIC-AND-NEXT.md` §2, not a parity nicety":
  `next/app.js`'s resource header, plus `index.html`'s Analyses cards, Survey
  Definition candidate cards, and Survey Results dashboard badges. Two of
  those reuse their existing Publish button as a "Publish again" action
  (`publish_stale`) rather than adding a new one; the dashboard badge is
  read-only and states the warning only.

## What did not need building

Both corrections' other findings were about *not* building new mechanism: no
new "connect" abstraction (`EgeriaResync._connect` was already the right
home), and no new tri-state resolver (`_resolves()` already existed). The
shipped code confirms both — `_scan_vanished_publishes` calls the existing
`_resolves()`/`_connect()` machinery rather than adding parallel plumbing.

## Test coverage

`tests/test_egeria_resync.py` gained coverage for the new scan/repair pair
(86 lines added in the same commit) — the resolve-tri-state cases, the
flag-write/self-heal cycle, and that `clear_orphan_publish_claims` still only
ever touches the locally-unmoored condition.

## Board hygiene

`INBOX.md`'s "Start here" table listed this as ready-to-start through
2026-09-20, three commits behind the code, matching the failure pattern the
board's own "Why this kept happening" section names. This doc's existence is
the fix for that instance; `INBOX.md` is updated in the same change to move
the "Stale publish" row into "Shipped" and add its ledger entry.
