"""RFA -> Egeria ToDo sync (docs/rfa-egeria-todo-followup.md).

The RFA drawer's defer/reassign/complete/reopen actions write a local
`rfa_actions` row first (authoritative for the API response, never blocked
on Egeria's reachability) and then attempt to create/update a real Egeria
`ToDo` behind it — same non-blocking try/except/log shape as
`egeria_publisher.py`'s activity-log write, since a failed Egeria call must
never fail the user's local action.

Unlike the activity log (a pure audit trail that can afford "log and
forget"), RFA/ToDo state is live workflow truth both systems must agree on
— `scheduler.py`'s background loop runs `reconcile_rfa_actions()` every
iteration to retry failed writes and pull in changes made by other Egeria
clients, closing the gap a one-shot sync attempt alone would leave.

Real, standing limitation (docs/rfa-egeria-todo-followup.md open question
#1, not solved here): RE has no per-user identity yet, so `reassign_action`
(which needs a real actor GUID) is never called — `assignee` stays a local
free-text field only until that's solved. Also not attempted: linking the
ToDo to the RFA's own Egeria Annotation element via `add_action_target` —
today's local RFAs are flattened from `activity_log` rows and don't
reliably carry a real Egeria annotation GUID to link against.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

def _get_clients():
    """Construct (MyProfile, MetadataExpert) clients, bearer-tokened —
    MyProfile subclasses AssetMaker (pyegeria/omvs/my_profile.py), so it
    already has create_my_todo/update_asset/get_my_to_dos AND assign_action/
    reassign_action/add_action_target in one client; MetadataExpert is only
    needed for the fully generic metadata-element read
    (get_metadata_element_by_guid, used elsewhere for verification/
    reconciliation) — kept as a second client rather than folded into
    MyProfile since it's a genuinely different, non-Asset-specific surface."""
    from pyegeria import MetadataExpert, MyProfile

    from resource_explorer.egeria_clients import current_principal, egeria_client

    # Who (Brief I): only scheduler.py's loop drives this, inside acting_as(Daemon(SCHEDULER)).
    # An rfa_actions row records no requester, so Daemon(..., requested_by) has nobody to name
    # (adding one is DDL). A route that ever calls it runs as the signed-in Caller.
    clients = egeria_client(current_principal(), purpose="rfa sync")
    return clients.of(MyProfile), clients.of(MetadataExpert)


def sync_rfa_action(registry, rfa_row: dict) -> None:
    """Attempt to create (first time) or update (subsequent) the Egeria
    ToDo behind one rfa_actions row. Never raises — every failure is caught,
    logged, and recorded via registry.mark_rfa_sync_error so the write-
    direction reconciliation pass can retry it later.

    Real bug found and fixed during live verification (2026-08-16): the
    update path originally sent a `{"class": "ToDoProperties",
    "activityStatus": "WAITING", ...}` body straight to
    `MetadataExpert.update_metadata_element_properties()` (the fully
    generic metadata-element endpoint) — that shape was silently accepted
    (no HTTP error) and changed nothing; its real wire contract needs each
    property individually typed (`EnumTypePropertyValue`/
    `PrimitiveTypePropertyValue`) plus epoch-millis dates, none of which
    that first attempt supplied.

    Fixed by switching to `MyProfile.update_asset()` instead (per direct
    guidance — a `ToDo` is an `Asset` subtype, so the Asset-specific update
    endpoint applies) — confirmed live: it accepts the same plain,
    `typeName`-tagged properties shape `create_my_todo()` already uses
    (human-readable date strings included, no epoch-millis conversion
    needed), and does a real partial update — fields not included in the
    call (description, qualifiedName, dueTime when omitted) are left
    untouched, not wiped. Simpler and more correct than the generic
    endpoint's typed-property-value contract.
    """
    rfa_id = rfa_row.get("id", "")
    try:
        my_profile, _ = _get_clients()
        todo_guid = rfa_row.get("egeria_todo_guid") or ""

        if not todo_guid:
            todo_guid = my_profile.create_my_todo(
                todo_name=f"RFA: {rfa_id}",
                activity_status=rfa_row.get("activity_status") or "REQUESTED",
                description=rfa_row.get("resolution_note") or "",
                priority=rfa_row.get("priority") or 0,
            )
        else:
            properties = {
                "class": "ToDoProperties",
                "typeName": "ToDo",
                "activityStatus": rfa_row.get("activity_status") or "REQUESTED",
                "priority": rfa_row.get("priority") or 0,
            }
            # dueTime/startTime only included when actually set — omitting
            # them (not sending an empty value) avoids clearing a real due
            # date on the Egeria side that this sync didn't intend to touch.
            if rfa_row.get("due_time"):
                properties["dueTime"] = rfa_row["due_time"]
            if rfa_row.get("start_time"):
                properties["startTime"] = rfa_row["start_time"]

            my_profile.update_asset(todo_guid, {
                "class": "UpdateElementRequestBody",
                "properties": properties,
            })

        registry.mark_rfa_synced(rfa_id, todo_guid)
    except Exception as exc:
        log.warning("Could not sync RFA %s to Egeria: %s", rfa_id, exc)
        try:
            registry.mark_rfa_sync_error(rfa_id, str(exc))
        except Exception:
            log.exception("Could not even record RFA sync error for %s", rfa_id)


def sync_rfa_note(registry, rfa_row: dict) -> None:
    """Push this RFA's current notes text to Egeria as an ActivityEntry,
    linked to the RFA's own ToDo — per direct decision (2026-08-16), only
    attempted once the ToDo already exists (egeria_todo_guid set); a note
    added before any status action has nothing to link against yet, and
    picks up automatically on the next reconciliation pass once one exists.
    Never raises — same non-blocking try/except/log shape as
    sync_rfa_action.

    One ActivityEntry per distinct note text (never edited in place) —
    matches docs/rfa-egeria-todo-followup.md's "Related, broader idea"
    section: status-change-style logging is naturally append-only, and
    sidesteps update_note's known-broken server-side behavior (egeria-python
    PYEGERIA_ISSUES.md ISSUE-30) entirely rather than working around it.
    notes_synced_value is the dedup key — an unchanged note is never
    re-pushed as a duplicate entry.
    """
    rfa_id = rfa_row.get("id", "")
    todo_guid = rfa_row.get("egeria_todo_guid") or ""
    notes = rfa_row.get("notes") or ""
    if not todo_guid or not notes or notes == (rfa_row.get("notes_synced_value") or ""):
        return
    try:
        my_profile, _ = _get_clients()
        notelog_guid = rfa_row.get("egeria_notelog_guid") or ""
        if not notelog_guid:
            notelog_guid = my_profile.create_note_log(
                element_guid=todo_guid,
                display_name=f"RFA notes: {rfa_id}",
                description="Notes recorded against this RFA from Resource Explorer.",
            )
            registry.mark_rfa_notelog(rfa_id, notelog_guid)

        my_profile.create_note(
            notelog_guid,
            associated_element=todo_guid,
            body={
                "class": "NoteProperties",
                "typeName": "ActivityEntry",
                "qualifiedName": my_profile.make_feedback_qn("ActivityEntry", todo_guid),
                "description": notes,
            },
        )
        registry.mark_rfa_note_synced(rfa_id, notes)
    except Exception as exc:
        log.warning("Could not sync RFA note %s to Egeria: %s", rfa_id, exc)
        try:
            registry.mark_rfa_note_sync_error(rfa_id, str(exc))
        except Exception:
            log.exception("Could not even record RFA note sync error for %s", rfa_id)


# ToDos in one of these states are still "open" from RE's point of view —
# used to bound the read-direction reconciliation pull below. Deliberately
# excludes ABANDONED/CANCELLED/INVALID/OTHER (Egeria's own ACTIVITY_STATUS
# vocabulary, pyegeria/core/_globals.py) — a ToDo another client moved into
# one of those isn't something this pass currently reconciles back in,
# since RE's local vocabulary has no equivalent to distinguish them from a
# plain "completed".
_OPEN_ACTIVITY_STATUSES = ["REQUESTED", "APPROVED", "WAITING", "ACTIVATING", "IN_PROGRESS", "PAUSED", "FOR_INFO"]


def _extract_todo_fields(raw: dict) -> dict | None:
    """Defensively pull {guid, activity_status, due_time, start_time,
    priority} out of one element of get_my_to_dos()'s JSON output. The
    exact nested shape (properties vs. top-level, elementHeader.guid vs.
    guid) hasn't been live-confirmed against a real Egeria response as of
    this writing — this tries the plausible shapes and returns None (skip,
    don't crash the reconciliation pass) rather than guessing wrong."""
    try:
        header = raw.get("elementHeader") or {}
        guid = header.get("guid") or raw.get("guid") or ""
        if not guid:
            return None
        props = raw.get("properties") or raw
        return {
            "guid": guid,
            "activity_status": props.get("activityStatus", ""),
            "due_time": props.get("dueTime") or "",
            "start_time": props.get("startTime") or "",
            "priority": props.get("priority") or 0,
        }
    except Exception:
        return None


def reconcile_rfa_actions(registry) -> None:
    """One reconciliation pass, both directions (sync mechanics, point 3):

    - Write direction: retry every row that's never synced or whose last
      sync attempt failed.
    - Read direction: pull the caller's open ToDos and, for every already-
      linked local row whose remote state differs, overwrite the local
      fields with what Egeria has — catching changes made by another
      Egeria client, not just RE's own drawer.

    Called from scheduler.py's background loop, once per iteration —
    reuses that existing loop rather than starting a second one (sync
    mechanics, point 3's own framing). Never raises — every real failure
    (a single row's sync, or the bulk pull itself) is caught and logged so
    one bad row/one Egeria hiccup doesn't stop the rest of the pass or the
    scheduler iteration it's part of.
    """
    try:
        from resource_explorer.workflows.egeria_work_access import todo_writes, writes_refusal

        for row in registry.list_unsynced_rfa_actions():
            # Brief Z3: the run-time re-check before a retried ToDo write. An rfa_actions row records no
            # requester (adding one is DDL), so this is the no-requester rule unless a caller is set: the
            # write proceeds only while no zone is in use; refused, the row keeps the words as its sync error.
            why = writes_refusal(registry, todo_writes(row))
            if why:
                registry.mark_rfa_sync_error(row.get("id", ""), why)
                continue
            sync_rfa_action(registry, row)
    except Exception:
        log.exception("RFA reconciliation: write-direction retry pass failed")

    try:
        from resource_explorer.workflows.egeria_work_access import note_writes, writes_refusal

        for row in registry.list_unsynced_rfa_notes():
            # Brief Z3 round 2 (F3): the retried NoteLog/Note write, by the no-requester rule (nobody recorded).
            why = writes_refusal(registry, note_writes(row))
            if why:
                registry.mark_rfa_note_sync_error(row.get("id", ""), why)
                continue
            sync_rfa_note(registry, row)
    except Exception:
        log.exception("RFA reconciliation: note write-direction retry pass failed")

    try:
        synced = registry.list_synced_rfa_actions()
        if not synced:
            return
        my_profile, _ = _get_clients()
        remote_todos = my_profile.get_my_to_dos(activity_status_list=_OPEN_ACTIVITY_STATUSES)
        if not isinstance(remote_todos, list):
            log.warning("RFA reconciliation: get_my_to_dos returned %s, expected a list", type(remote_todos))
            return

        remote_by_guid: dict[str, dict] = {}
        for raw in remote_todos:
            fields = _extract_todo_fields(raw)
            if fields:
                remote_by_guid[fields["guid"]] = fields

        for local in synced:
            remote = remote_by_guid.get(local["egeria_todo_guid"])
            if remote is None:
                continue  # not in the "still open" set — a closed/cancelled/etc. ToDo; leave local as-is
            changed = (
                remote["activity_status"] != local.get("activity_status")
                or remote["due_time"] != (local.get("due_time") or "")
                or remote["start_time"] != (local.get("start_time") or "")
                or remote["priority"] != (local.get("priority") or 0)
            )
            if changed:
                try:
                    registry.update_rfa_from_remote(
                        local["id"], remote["activity_status"],
                        remote["due_time"], remote["start_time"], remote["priority"],
                    )
                except Exception:
                    log.exception("RFA reconciliation: could not apply remote update for %s", local["id"])
    except Exception:
        log.exception("RFA reconciliation: read-direction pull failed")
