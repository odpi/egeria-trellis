#!/usr/bin/env python
"""One-time repair for `doc_source_publish` outbox rows stuck `done` with a
claim that never held — round 6 (2026-09-29),
`DOC-SOURCES-DECLARE-AND-PROBE-IMPLEMENTED.md`'s "Silent
no-op self-heal loop" section.

Why this exists, and why it is a SEPARATE script rather than only the
self-heal fix: design's explicit call (round 6 design session, 2026-09-29)
was that the repair for EXISTING stuck data should run ONCE, as a signed,
logged pass at merge time — not fire on every request/render. The self-heal
fix itself (`web/routes/doc_sources.py::_compute_egeria_state`, `registry.
reopen_outbox_row`) *would* also catch every currently-stuck row the next
time a person GETs that resource's documentation sources — self-heal now
reopens (rather than skips) a `done` row whose element is still
`not_catalogued` — but design did not want the fix for a known, already-
identified backlog of bad rows to depend on someone happening to load the
right page. This script finds and repairs that backlog explicitly, with a
printed/logged record of exactly which rows it touched, independent of
whether or when a render would have found them.

**The failure this repairs, exactly:** a `doc_source_publish` row reached
`status='done'` carrying `egeria_guid=X` in `egeria_outbox`, while the
`doc_sources` row it belongs to still has `egeria_external_ref_guid=X` (or
empty) and NO `egeria_link_relationship_guid` — i.e. the row claims success
but was never actually linked to its resource's Egeria asset. Before round
5 (2026-09-29, same design doc) shipped, `_create_doc_source_publish` wrote
this "done" without verifying the link ever completed or that the ref guid
still resolved; round 5 closed that going forward, but every row that
reached `done` under the OLD rule is left exactly as stuck as the live
incident found it (`doc_sources` row `4711d538`, ref guid `b9925119…`,
deleted by an unrelated unpublish, outbox row 68961 `done` since
2026-09-29T17:42:46Z).

Read-only by default. Pass --apply to reopen. Reopening does NOT itself
contact Egeria — it resets the row to `pending` (via `registry.
reopen_outbox_row`, same primitive the self-heal fix uses) so the ordinary
drain (the scheduler's 15-minute loop, or the next `_attempt_outbox_row_
immediately` fired by an add/remove on the same resource) picks it up and
runs round 5's verify-before-trust logic against it for real. Pass --drain
together with --apply to also attempt a real drain of each reopened row
immediately, right here, rather than waiting for the scheduler — this is
the one piece of the repair that DOES contact Egeria, and is what actually
resolves the currently-stuck `laz_local_adventureworks` `egeria.ai` row to
`catalogued` with a fresh ref+link in one pass, without needing a page
render at all.

    uv run python scripts/repair_stuck_doc_source_publish_rows.py
    uv run python scripts/repair_stuck_doc_source_publish_rows.py --apply
    uv run python scripts/repair_stuck_doc_source_publish_rows.py --apply --drain
"""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                     help="reopen the stuck rows (default: report only)")
    ap.add_argument("--drain", action="store_true",
                     help="with --apply, also attempt a real drain of each reopened row "
                          "right now (contacts Egeria)")
    args = ap.parse_args()

    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    stuck = find_stuck_rows(registry)
    if not stuck:
        print("No stuck doc_source_publish rows found — nothing to repair.")
        return 0

    print(f"{len(stuck)} stuck doc_source_publish outbox row(s) found "
          f"(status='done', but the doc_sources row they belong to still lacks a link guid):\n")
    for r in stuck:
        print(f"    outbox row {r['outbox_id']:>6}  {r['entity_type']}/{r['entity_slug']}  "
              f"source {r['source_id']}  egeria_guid={r['egeria_guid']!r}  "
              f"doc_sources.ref={r['ref_guid']!r} link={r['link_guid']!r}")

    if not args.apply:
        print(f"\nDry run — re-run with --apply to reopen {len(stuck)} row(s).")
        print("Reopening resets each row to 'pending' (same row id — never a duplicate) so the "
              "normal drain (scheduler, or the next add/remove on that resource) runs round 5's "
              "verify-before-trust logic against it for real. Add --drain to also attempt a real "
              "drain of each row right now instead of waiting for the scheduler.")
        return 0

    reopened = 0
    for r in stuck:
        reason = (
            "one-time repair (round 6, 2026-09-29): row was 'done' with "
            f"egeria_guid={r['egeria_guid']!r} but the doc_sources row it belongs to has no "
            "link guid — its 'done' claim predates round 5's verify-before-trust write-back "
            "rule and was never actually linked to its asset"
        )
        registry.reopen_outbox_row(r["outbox_id"], reason)
        reopened += 1
        print(f"    reopened outbox row {r['outbox_id']}")
    print(f"\nReopened {reopened} row(s). Each is 'pending' again with attempts/egeria_guid "
          "cleared and reopened_at/reopen_reason recorded on the row.")

    if not args.drain:
        print("Left for the normal drain (scheduler's 15-minute loop, or the next add/remove "
              "on the same resource) to pick up. Re-run with --drain to attempt them right now.")
        return 0

    print("\nDraining each reopened row now...")
    from resource_explorer.egeria_outbox import drain_outbox_row

    done = failed = 0
    for r in stuck:
        try:
            summary = drain_outbox_row(registry, r["outbox_id"])
        except Exception as exc:  # pragma: no cover — drain_outbox_row already never raises
            print(f"    row {r['outbox_id']}: drain itself raised ({type(exc).__name__}: {exc})")
            failed += 1
            continue
        if summary.get("done"):
            done += 1
            print(f"    row {r['outbox_id']}: done")
        else:
            failed += 1
            print(f"    row {r['outbox_id']}: not done this pass "
                  f"(failed={summary.get('failed', 0)}, dead={summary.get('dead', 0)}) — "
                  "left for the normal retry/backoff")
    print(f"\nDrained: {done} done, {failed} left for retry, out of {len(stuck)} reopened.")
    return 0


def find_stuck_rows(registry) -> list[dict]:
    """Every `doc_source_publish` outbox row that is `done` while the
    `doc_sources` row it targets still lacks a link guid — the exact "proof
    no longer holds" shape `web/routes/doc_sources.py::_compute_egeria_state`
    now reopens on sight. A row whose `doc_sources` row was deleted since
    (declared, published, then removed) is skipped — nothing left to repair
    for a source that no longer exists locally.

    Kept as a small, direct SQL query (not a full self-heal simulation) so
    this script's own logic is easy to audit independently of the route
    code it is repairing data for.
    """
    with registry._conn() as conn:
        rows = conn.execute(
            "SELECT o.id AS outbox_id, o.entity_type AS entity_type, o.entity_slug AS entity_slug, "
            "       o.payload_json AS payload_json, o.egeria_guid AS egeria_guid "
            "FROM egeria_outbox o "
            "WHERE o.element_kind = 'doc_source_publish' AND o.status = 'done'"
        ).fetchall()
    out = []
    for r in rows:
        row = dict(r)
        import json
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except ValueError:
            continue
        source_id = payload.get("source_id", "")
        source = registry.get_doc_source(row["entity_type"], row["entity_slug"], source_id)
        if source is None:
            continue  # removed locally since — nothing left to repair
        if source.get("egeria_link_relationship_guid"):
            continue  # genuinely linked — round 5's own proof holds, leave it alone
        out.append({
            "outbox_id": row["outbox_id"],
            "entity_type": row["entity_type"],
            "entity_slug": row["entity_slug"],
            "source_id": source_id,
            "egeria_guid": row["egeria_guid"],
            "ref_guid": source.get("egeria_external_ref_guid", ""),
            "link_guid": source.get("egeria_link_relationship_guid", ""),
        })
    return out


if __name__ == "__main__":
    sys.exit(main())
