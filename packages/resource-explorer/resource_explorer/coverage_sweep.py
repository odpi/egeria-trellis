"""Which resources have, lack, or have never been checked for one analysis.

The one-to-many version of the coverage matrix: "which repos have no license classification?" is a
question about every resource at once. It reads the same FactLayer states the single-resource
answers do, so the four outcomes stay distinct -- measured with a finding, measured with nothing
found, never run, and not established -- and a resource that was never checked is never reported as
a resource that passed.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from resource_explorer.registry import ProjectRegistry

log = logging.getLogger(__name__)

#: Plain words people use, mapped to the analysis id they mean. Anything else must be an exact id.
ALIASES = {
    "license": "license_classification", "licence": "license_classification",
    "security": "security_scan", "cve": "cve_scan", "vulnerabilities": "cve_scan",
    "secrets": "secret_scan", "docs": "documentation_coverage", "documentation": "documentation_coverage",
    "dependencies": "dependency_analysis", "ci": "ci_quality", "owner": "database_owner",
    "staleness": "database_staleness", "classification": "repo_classification",
    "schema": "schema_inventory",
}

#: Seconds before a sweep stops reading and reports how far it got. Each resource is a fact read.
DEFAULT_TIME_BUDGET = 30.0
_LIST_LIMIT = 25

_STATE_ORDER = ("measured", "partial", "nothing_found", "never_run", "not_established")
_STATE_LABEL = {
    "measured": "has a result",
    "partial": "partial result",
    "nothing_found": "ran, found nothing",
    "never_run": "not yet run",
    "not_established": "cannot be established",
}


def _resources(registry: "ProjectRegistry") -> list[tuple[str, str]]:
    """(slug, resource_type) for everything registered."""
    out = [(p.slug, "repo") for p in registry.list_all()]
    out += [(d.slug, "database") for d in registry.list_databases()]
    out += [(f.slug, "filesystem") for f in registry.list_filesystems()]
    return out


def known_analyses(registry: "ProjectRegistry") -> dict[str, list[str]]:
    """Analysis ids that can be read, per resource type."""
    from resource_explorer.facts import FactLayer
    from resource_explorer.resource_types import SURVEYED_RESOURCE_TYPES

    found: dict[str, list[str]] = {}
    for rtype in SURVEYED_RESOURCE_TYPES:
        results_map = FactLayer(registry, resource_type=rtype)._map("analysis_results_map")
        found[rtype] = sorted(results_map or {})
    return found


def coverage_sweep(
    registry: "ProjectRegistry",
    analysis: str,
    resource_type: str = "",
    only_gaps: bool = False,
    time_budget: float = DEFAULT_TIME_BUDGET,
) -> str:
    from resource_explorer.facts import FactLayer

    analysis = (analysis or "").strip().lower()
    analysis_id = ALIASES.get(analysis, analysis)
    by_type = known_analyses(registry)
    resource_type = (resource_type or "").strip().lower()
    if resource_type and resource_type not in by_type:
        return f"Unknown resource type '{resource_type}'; use one of: {', '.join(by_type)}."

    types = [resource_type] if resource_type else [t for t, ids in by_type.items() if analysis_id in ids]
    if not types:
        listing = "; ".join(f"{t}: {', '.join(ids)}" for t, ids in by_type.items() if ids)
        return (
            f"'{analysis}' is not an analysis I can sweep"
            + (f" for {resource_type} resources" if resource_type else "")
            + f". Known analyses -- {listing}."
        )
    if resource_type and analysis_id not in by_type[resource_type]:
        return f"'{analysis_id}' does not apply to {resource_type} resources."

    layers = {t: FactLayer(registry, resource_type=t) for t in types}
    targets = [(s, t) for s, t in _resources(registry) if t in types]
    if not targets:
        return f"No {'/'.join(types)} resources are registered, so there is nothing to sweep."

    deadline = time.monotonic() + time_budget
    groups: dict[str, list[str]] = {}
    checked = 0
    for slug, rtype in targets:
        if time.monotonic() > deadline:
            break
        try:
            state = layers[rtype].fact(slug, analysis_id).state
        except Exception as exc:
            log.warning("coverage sweep could not read %s/%s: %s", slug, analysis_id, exc)
            state = "not_established"
        groups.setdefault(state, []).append(slug)
        checked += 1

    lines = [f"Coverage of `{analysis_id}` across {checked} of {len(targets)} {'/'.join(types)} resource(s):"]
    for state in [s for s in _STATE_ORDER if s in groups] + sorted(set(groups) - set(_STATE_ORDER)):
        slugs = sorted(groups[state])
        is_gap = state in ("never_run", "not_established")
        if only_gaps and not is_gap:
            lines.append(f"- {_STATE_LABEL.get(state, state)}: {len(slugs)}")
            continue
        shown = ", ".join(slugs[:_LIST_LIMIT])
        more = f" (+{len(slugs) - _LIST_LIMIT} more)" if len(slugs) > _LIST_LIMIT else ""
        lines.append(f"- {_STATE_LABEL.get(state, state)} ({len(slugs)}): {shown}{more}")
    if checked < len(targets):
        lines.append(
            f"Stopped after {time_budget:.0f}s: {len(targets) - checked} resource(s) were not checked "
            "and are neither covered nor uncovered."
        )
    if "never_run" in groups or "not_established" in groups:
        lines.append(
            "`not yet run` and `cannot be established` mean nothing is known either way; they are not passes."
        )
    return "\n".join(lines)
