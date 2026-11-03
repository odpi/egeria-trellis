# Publish state after a redeploy — two corrections before building

**Replies to:** `SPEC-PUBLISH-STATE-AFTER-REDEPLOY.md`
**Read against:** `main` at `1b370cbe` (after #106/#107)
**Status:** not built yet — offering a revised plan, holding for a go-ahead.

---

## 1 · §5's own survey target is wrong

The spec sends the mixed-origin check at `members.py`. It holds no Egeria GUIDs
at all — grep for `guid` there returns zero hits, and `egeria` returns exactly
one docstring example string. It is a read-only enumerator over
`project_analysis_findings`; it persists nothing. Nothing in §5 applies to it.

**The real mixture case §5 was looking for is two other columns, and it is
already live and already wrong:**

- `investigations.egeria_project_guid` (`registry.py:2218`)
- `entity_egeria_project_context.egeria_project_guid` (`registry.py:2174`)

Both can hold a GUID this app **created** (`EgeriaInvestigationPublisher.
promote()`, `surveyors/egeria_investigation_publisher.py:313`) or a GUID
**bound to an already-existing** Egeria Project (`bind_egeria_project`,
`web/routes/investigations.py:272`). One column, both origins, no marker
distinguishing them — the adjacent `egeria_binding`/`egeria_project_status`
columns record policy (`local`/`egeria`, `linked`/`deferred`/`declined`), not
who created the element.

And `egeria_resync.py`'s existing scanners (`_scan_investigation_guids`
`:312`, `_scan_contexts` `:348`) already clear both columns
(`_do_clear_stale_investigations` `:932-960`) with no origin distinction —
via `ProjectManager.get_project_by_guid`, with no check for whether the GUID
was ever created here. **This is the exact failure §5 warned against, and
it's already running, unrelated to this spec.** Worth its own gaps-list entry
rather than folding into this build; noting it here since it's what turned up
while doing the survey the spec asked for.

## 2 · The mechanism this spec designs from scratch already exists twice

**The tri-state check:** `EgeriaResync._resolves` (`egeria_resync.py:171`) is
already the `True`/`False`/`None` existence check §3 specifies, including the
`isinstance(result, str)` branch the spec calls out as the detail worth
getting right. No new resolver is needed — this one should be reused, called
against the newest `project_egeria_surveys.egeria_report_guid` per project.

**The "flag, don't delete" state:** `egeria_linkage_status.stale_guid`
(`registry.py:1719`, written by `mark_egeria_linkage_stale` `:4935`, read by
`get_egeria_linkage`/`list_stale_egeria_linkages` `:4960`/`:4968`) is already
exactly §4's rule in production, for a different GUID (`projects.
egeria_asset_guid`). It already reaches the UI as `egeria_link_stale`
(`web/routes/projects.py:327-328` → `next/app.js:2664-2667`, rendered as
"published, but the Egeria link is stale"). The new 4th state is this same
shape, one table over — reuse the pattern rather than add new schema for it.

## 3 · A real conflict: an existing job already deletes what this spec flags

`_scan_orphan_publish_claims` (`egeria_resync.py:257`) already scans
`project_published_annotation_types`/`project_published_analyses` for claims
whose `egeria_report_guid` isn't in `project_egeria_surveys` — but only
checks **local** consistency, never Egeria. `_do_clear_orphan_publish_claims`
(`egeria_resync.py:891`) then **deletes** those rows, and
`"clear_orphan_publish_claims"` sits in `SAFE_SCHEDULED_STEPS`, run
unattended by `scan_and_clear` every 600 seconds (`egeria_resync.py:1169,
1188`).

That directly contradicts §4: *"Flag, do not delete. The row is the only
evidence that a publish happened."* Building the new check without touching
this means a row this feature flags as suspect can be deleted by the existing
loop within ten minutes of the flag being written — the flag would exist for
long enough to never be seen. This has to be reconciled as part of the same
change, not after.

## 4 · No single "connect" point exists

There's no place in the code that represents "connecting to Egeria" once.
Clients are built per-caller (`egeria_identity.classification_client`,
`egeria_identity.py:343-363`, deliberately uncached). `worker.py`'s
`ClassificationExplorer` import (`:350`) the spec cites is import-only, for
deadlock avoidance — nothing there constructs or uses one. The nearest fit is
`EgeriaResync._connect` (`egeria_resync.py:151`), already the "once per pass,
background, leader-elected" home every other existence check in this
codebase uses.

## 5 · Proposal — offering to build this

1. Take `clear_orphan_publish_claims` out of `SAFE_SCHEDULED_STEPS` (or gate
   it behind the new resolve-against-Egeria check, so it only ever deletes a
   claim this pass has itself confirmed gone) — reconciling §3 before
   anything downstream of it can be trusted.
2. Add a resolve step to `EgeriaResync`'s existing pass: call `_resolves()`
   against each project's newest `project_egeria_surveys.egeria_report_guid`;
   on a failed resolve, mark it via the same mechanism `mark_egeria_linkage_
   stale` already uses (reused pattern, minimal new schema — one column or
   one row-state, not a new table).
3. Wire the 4th state into the UI: restructure `next/app.js:2658-2667`
   (the stale-link branch already there is the template) plus the three
   `index.html` spots that build the same three-way badge
   (`:2934-2938`, `:8336-8344`, `:8584-8589`), each gaining "⚠ these elements
   are no longer in the store · publish again ›" per §4's exact wording.
4. File the `investigations`/`entity_egeria_project_context` blind-clear bug
   (§1 above) as its own item — same shape, different table, and it's a live
   bug independent of whether 1-3 above get built.

Say the word and I'll start on 1-3 (4 can go on the gaps list as a separate
item, or I can take it in the same pass — your call).
