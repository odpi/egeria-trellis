"""The RFA drawer's rows: every RequestForAction annotation in the activity log, flattened.

Lives here, not in the web route, so the chat tools can read the same rows the drawer shows -- with the
response actions (status, assignee, ...) overlaid and dismissals marked -- without importing the web layer.
"""
from __future__ import annotations

from resource_explorer.registry import ProjectRegistry

#: Curate's tag add/remove bookkeeping rows carry no RFAs and must not push real ones out of the newest-N window.
CURATE_BOOKKEEPING_OPERATIONS = ("curate_tag_added", "curate_tag_removed")


def flatten_rfas(
    registry: ProjectRegistry,
    entity_type: str | None = None,
    entity_slug: str | None = None,
    limit: int = 500,
) -> list[dict]:
    """Return all RequestForAction annotations across all activity log entries.

    Each RFA gets a stable `id` (f"{entry_id}::{annotation_index}", minted
    positionally the same way every time this flattens the same entry's
    annotations) and has any locally-recorded response action
    (see PATCH /rfas/{rfa_id}) overlaid onto it: `rfa_status` (defaults to
    "open" if nothing's been recorded yet), `assignee`, `defer_until`,
    `resolution_note`, `action_updated_at`. `status` (Egeria/local
    provenance, e.g. "local") is unrelated and untouched — don't confuse
    the two."""
    # Brief F round 3: Curate's tag add/remove bookkeeping rows carry no RFAs and must not push real ones out of
    # the newest-N window.
    entries = registry.list_activity(entity_type=entity_type, entity_slug=entity_slug, limit=limit,
                                     exclude_operations=CURATE_BOOKKEEPING_OPERATIONS)
    overrides = registry.list_rfa_action_overrides()
    # Dismissed RFAs are returned like every other one, carrying `dismissed`
    # and the dismissal record — NEVER filtered out here. The project owner asked for
    # "suppress with visibility": the drawer collapses them behind an
    # explicit "N suppressed" toggle, which it can only do if it is told
    # they exist. A server-side filter would make a suppressed finding
    # indistinguishable from one that never occurred, which is precisely the
    # absence-as-answer shape this codebase keeps getting bitten by.
    dismissals = registry.active_rfa_dismissals()
    rfas = []
    for entry in entries:
        for idx, ann in enumerate(entry.get("annotations") or []):
            if "RequestForAction" in (ann.get("annotation_type") or ""):
                # One row per REQUEST, not one per group.
                #
                # survey_report.summarise_annotations groups by
                # (step, annotation_type) — right for a compact activity
                # preview, wrong here. Three distinct requests from
                # SecurityHygieneCheck (no SECURITY.md, no CI config, no
                # license) share both keys, so this used to emit ONE row
                # carrying count=3 and the first one's text: "Security
                # HygieneCheck 3 / No SECURITY.md found", with the other two
                # unreachable. That is the original report, and the reason
                # the group now carries `items`.
                #
                # The sub-index is appended to rfa_id so each request keeps a
                # stable identity for PATCH and for dismissal. A group with
                # no items (an entry written before this change) still
                # yields its single row, so old entries do not vanish.
                members = ann.get("items") or [None]
                for sub, member in enumerate(members):
                    _emit_rfa(rfas, entry, ann, idx, sub, member,
                              len(members), overrides, dismissals, registry)
    return rfas


def _emit_rfa(rfas, entry, ann, idx, sub, member, n_members,
              overrides, dismissals, registry) -> None:
    """One RFA row. `member` is this request's own text when the group
    carried per-item detail, None when it did not."""
    rfa_id = f"{entry['id']}::{idx}" if n_members == 1 else f"{entry['id']}::{idx}::{sub}"
    override = overrides.get(rfa_id)
    # Content key, not rfa_id: a dismissal has to keep matching
    # across future survey runs, which mint new entry ids (see
    # the rfa_dismissals DDL in registry.py).
    dismissal_key = registry.rfa_dismissal_key(*registry.rfa_dismissal_subject(entry, ann, member))
    dismissal = dismissals.get(dismissal_key)
    rfas.append({
        "id": rfa_id,
        "entry_id": entry["id"],
        "annotation_index": idx,
        "ts": entry["ts"],
        "entity_type": entry.get("entity_type", ""),
        "entity_slug": entry.get("entity_slug", ""),
        "entity_name": entry.get("entity_name", ""),
        "annotation_type": ann.get("annotation_type", ""),
        "analysis_name": ann.get("analysis_name", ""),
        "count": 1 if n_members > 1 else ann.get("count", 1),
        "summary": (member or ann).get("summary", "") or ann.get("summary", ""),
        # Carried from the annotation the summary came from
        # (survey_orchestrator.py's by_step grouping) — the
        # drawer showed only `summary` before this, and with ten
        # "SecurityHygieneCheck" RFAs sharing near-identical
        # summaries, that read as ten identical, unexplained
        # items. `explanation` says why it matters;
        # `action_requested`/`action_target_name` say what to do
        # and to what — RequestForActionAnnotation always carries
        # them (security_hygiene.py, file_size.py, etc.), they
        # just were never forwarded past the activity log.
        "explanation": (member or ann).get("explanation", ""),
        "action_requested": (member or ann).get("action_requested", ""),
        "action_target_name": (member or ann).get("action_target_name", ""),
        "status": ann.get("status", "local"),
        "rfa_status": override["rfa_status"] if override else "open",
        "assignee": override["assignee"] if override else "",
        "defer_until": override["defer_until"] if override else "",
        "resolution_note": override["resolution_note"] if override else "",
        "notes": (override.get("notes") if override else "") or "",
        "action_updated_at": override["updated_at"] if override else "",
        # Egeria ToDo sync state (docs/rfa-egeria-todo-followup.md)
        # — "" guid means never synced yet (or no response action
        # taken at all); sync_error non-empty means the last
        # attempt failed and scheduler.py's reconciliation pass
        # will retry it.
        "egeria_todo_guid": override["egeria_todo_guid"] if override else "",
        "synced_at": override["synced_at"] if override else "",
        "sync_error": override["sync_error"] if override else "",
        # Suppression state (docs/rfa-dismissals.md). `dismissal`
        # carries the whole record — reason, note, who, when —
        # so the drawer can say what was decided and by whom
        # rather than just hiding the row.
        "dismissal_key": dismissal_key,
        "dismissed": dismissal is not None,
        "dismissal": dismissal,
    })
    return rfas
