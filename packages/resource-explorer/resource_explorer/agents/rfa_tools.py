"""The one chat tool that writes: filing a RequestForAction.

Kept out of ``agents.tools`` on purpose. That module is what the A2A surface exposes, and the A2A tools are
read-only (tests/test_zone_access_remaining_routes.py guards it). Only the in-app chat agents import this
module; an agent served over A2A is built with ``allow_writes = False`` and never gets the tool.
"""
from __future__ import annotations

from beeai_framework.tools import tool

#: `status` on an RFA is its delivery state (the drawer reads it as `status`, default "local"), not
#: its workflow state -- that is `rfa_status` ("open"/"deferred"/...), kept in rfa_actions and set by
#: a person. An agent may only file a local RFA; "pending" is for the publish pipeline.
_AGENT_RFA_STATUSES = frozenset({"local"})
_RFA_SUMMARY_MAX = 200
_RFA_DETAIL_MAX = 4000


def _normalize_rfa_summary(summary: str) -> str:
    return " ".join(summary.lower().split())


def _existing_open_rfa(registry, slug: str, summary: str) -> str | None:
    """Entry id of a not-yet-completed RFA on this resource with the same summary, if any.

    Without this a retry loop, or two turns that reach the same conclusion, file the same request
    again and the drawer fills with duplicates a person must close one by one.
    """
    wanted = _normalize_rfa_summary(summary)
    overrides = registry.list_rfa_action_overrides()
    for entry in registry.list_activity(entity_slug=slug, operation="rfa", limit=500):
        for idx, ann in enumerate(entry.get("annotations") or []):
            text = ann.get("summary") or entry.get("summary") or ""
            if _normalize_rfa_summary(text) != wanted:
                continue
            override = overrides.get(f"{entry['id']}::{idx}")
            if override and override.get("rfa_status") == "completed":
                continue
            return entry["id"]
    return None


def create_rfa_from_finding_raw(
    resource_slug: str,
    summary: str,
    detail: str = "",
    analysis_name: str = "",
    status: str = "local",
    confirm: bool = True,
) -> str:
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.activity_logger import log_rfa

    if not summary or not summary.strip():
        return "Error: summary must not be empty when filing a RequestForAction."
    summary = summary.strip()
    if len(summary) > _RFA_SUMMARY_MAX:
        return (
            f"Error: summary is {len(summary)} characters; keep it under {_RFA_SUMMARY_MAX} "
            "and put the rest in detail."
        )
    if detail and len(detail) > _RFA_DETAIL_MAX:
        return f"Error: detail is {len(detail)} characters; the limit is {_RFA_DETAIL_MAX}."
    if status not in _AGENT_RFA_STATUSES:
        return (
            f"Error: status '{status}' is not allowed; an agent files RFAs as "
            f"{', '.join(sorted(_AGENT_RFA_STATUSES))} only."
        )

    registry = ProjectRegistry()
    slug = registry._normalize_slug(resource_slug)

    entity_type = None
    entity_name = slug

    proj = registry.get(slug)
    if proj:
        entity_type = "repo"
        entity_name = proj.display_name or proj.slug
    else:
        db = registry.get_database(slug, allow_unreadable=True)
        if db:
            entity_type = "database"
            entity_name = db.display_name or db.slug
        else:
            fs = registry.get_filesystem(slug)
            if fs:
                entity_type = "filesystem"
                entity_name = fs.display_name or fs.slug
    if entity_type is None:
        # Previously this fell through as a "repo", filing an RFA against a slug that exists nowhere.
        return f"Error: '{resource_slug}' is not a registered repository, database or filesystem."

    existing = _existing_open_rfa(registry, slug, summary)
    if existing:
        return (
            f"Not filed: an open RequestForAction with this summary already exists for "
            f"{entity_type} '{slug}' (entry {existing}). Tell the user about it instead of filing another."
        )

    if not confirm:
        return (
            f"NOT FILED YET. Proposed RequestForAction for {entity_type} '{slug}': {summary}\n"
            f"{detail.strip()}\n"
            "Show this to the user and ask whether to file it. Only if they agree, call "
            "create_rfa_from_finding again with the same arguments and confirm=true."
        )

    try:
        entry_id = log_rfa(
            registry=registry,
            entity_type=entity_type,
            entity_slug=slug,
            entity_name=entity_name,
            status=status,
            summary=summary,
            detail=detail.strip() if detail else "",
            analysis_name=analysis_name.strip() if analysis_name else "Agent Finding",
        )
        return (
            f"Successfully created RequestForAction (RFA) #{entry_id} for {entity_type} '{slug}': "
            f"{summary}"
        )
    except Exception as e:
        return f"Error creating RequestForAction: {e}"


@tool(
    description=(
        "Propose or file a Request For Action (RFA) in the RFA drawer for a registered resource (repo, "
        "database, or filesystem) when an architectural gap, schema mismatch, missing documentation, or "
        "governance action item is discovered. summary is a concise title (max 200 chars); detail gives "
        "context/remediation steps; analysis_name is the category or origin. This writes a persistent record "
        "a person must handle, so it is two-step: with confirm=false (the default) nothing is written and "
        "you get a preview to show the user; call again with confirm=true only after the user agrees. "
        "An open RFA with the same summary on the same resource is never duplicated."
    )
)
def create_rfa_from_finding(
    resource_slug: str,
    summary: str,
    detail: str = "",
    analysis_name: str = "",
    confirm: bool = False,
) -> str:
    return create_rfa_from_finding_raw(
        resource_slug, summary, detail, analysis_name, confirm=confirm
    )
