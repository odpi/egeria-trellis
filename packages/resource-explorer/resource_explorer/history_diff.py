"""What changed between two surveys of the same resource.

Answers "what changed in this schema since the last survey?" and "has this repo's dependency set
drifted?" from history the registry already keeps: every `database_surveys` row carries its full
schema, and `project_dependencies` appends a complete batch per ingest. Pure comparison, no new
collection -- and when there is nothing to compare, it says which of "never surveyed", "surveyed
once" or "surveyed but not measured" is true, because those are different answers.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from resource_explorer.registry import ProjectRegistry

log = logging.getLogger(__name__)

#: Items listed per category before the rest are summarised as a count.
_LIST_LIMIT = 25


def _columns(table: dict) -> dict[str, dict]:
    return {c["name"].lower(): c for c in table.get("columns", []) if c.get("name")}


def _tables(info: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for sch in info.get("schemas", []):
        for tbl in sch.get("tables", []):
            if sch.get("name") and tbl.get("name"):
                out[f"{sch['name']}.{tbl['name']}".lower()] = tbl
    return out


def schema_diff(old_info: dict, new_info: dict) -> dict:
    """Structured difference between two `schema_info` dicts (old -> new).

    Keys: tables_added, tables_removed (qualified names), and `changed`: {table: {columns_added,
    columns_removed, column_changes: [(column, description)]}} for tables present in both.
    """
    old_t, new_t = _tables(old_info), _tables(new_info)
    changed: dict[str, dict] = {}
    for name in sorted(set(old_t) & set(new_t)):
        oc, nc = _columns(old_t[name]), _columns(new_t[name])
        added = sorted(nc[c]["name"] for c in set(nc) - set(oc))
        removed = sorted(oc[c]["name"] for c in set(oc) - set(nc))
        edits: list[tuple[str, str]] = []
        for c in sorted(set(oc) & set(nc)):
            o, n = oc[c], nc[c]
            parts = []
            if (o.get("type") or "").lower() != (n.get("type") or "").lower():
                parts.append(f"type {o.get('type')} -> {n.get('type')}")
            if bool(o.get("nullable")) != bool(n.get("nullable")):
                parts.append(f"nullable {bool(o.get('nullable'))} -> {bool(n.get('nullable'))}")
            if bool(o.get("is_primary_key")) != bool(n.get("is_primary_key")):
                parts.append(f"primary key {bool(o.get('is_primary_key'))} -> {bool(n.get('is_primary_key'))}")
            if parts:
                edits.append((n["name"], "; ".join(parts)))
        if added or removed or edits:
            changed[name] = {"columns_added": added, "columns_removed": removed, "column_changes": edits}
    return {
        "tables_added": sorted(set(new_t) - set(old_t)),
        "tables_removed": sorted(set(old_t) - set(new_t)),
        "changed": changed,
    }


def _capped(items: list[str]) -> str:
    shown = ", ".join(items[:_LIST_LIMIT])
    return shown + (f" (+{len(items) - _LIST_LIMIT} more)" if len(items) > _LIST_LIMIT else "")


def render_schema_diff(diff: dict) -> list[str]:
    lines: list[str] = []
    if diff["tables_added"]:
        lines.append(f"- Tables added ({len(diff['tables_added'])}): {_capped(diff['tables_added'])}")
    if diff["tables_removed"]:
        lines.append(f"- Tables removed ({len(diff['tables_removed'])}): {_capped(diff['tables_removed'])}")
    for name, ch in list(diff["changed"].items())[:_LIST_LIMIT]:
        lines.append(f"- Table `{name}` changed:")
        if ch["columns_added"]:
            lines.append(f"  - columns added: {_capped(ch['columns_added'])}")
        if ch["columns_removed"]:
            lines.append(f"  - columns removed: {_capped(ch['columns_removed'])}")
        for col, desc in ch["column_changes"][:_LIST_LIMIT]:
            lines.append(f"  - `{col}`: {desc}")
    if len(diff["changed"]) > _LIST_LIMIT:
        lines.append(f"- ... and {len(diff['changed']) - _LIST_LIMIT} more changed tables")
    return lines


def database_changes(registry: "ProjectRegistry", database_slug: str) -> str:
    """Schema changes between the two most recent measured surveys of a database."""
    from resource_explorer.registry import _survey_schema_info, is_measured_survey

    slug = registry._normalize_slug(database_slug)
    if not registry.get_database(slug, allow_unreadable=True):
        return f"Database '{database_slug}' is not registered."

    surveys = registry.get_database_surveys(slug)  # newest first, invalid rows excluded
    if not surveys:
        return f"Database '{slug}' has never been surveyed, so there is no schema to compare."
    measured = [s for s in surveys if is_measured_survey(s)]
    if not measured:
        return (
            f"Database '{slug}' has {len(surveys)} survey record(s), but none recorded a schema "
            "(e.g. publish-only records), so nothing was measured to compare."
        )
    if len(measured) == 1:
        return (
            f"Database '{slug}' has been surveyed once with a recorded schema "
            f"({measured[0]['surveyed_at']}). A second survey is needed before changes can be shown."
        )

    new, old = measured[0], measured[1]
    diff = schema_diff(_survey_schema_info(old), _survey_schema_info(new))
    header = f"Schema changes for `{slug}`: {old['surveyed_at']} -> {new['surveyed_at']}"
    body = render_schema_diff(diff)
    if not body:
        return f"{header}\nNo differences in tables or columns between these two surveys."
    return "\n".join([header, *body])


def _dependency_batches(registry: "ProjectRegistry", slug: str) -> list[tuple[str, dict]]:
    """The two newest ingest batches as (indexed_at, {(ecosystem, name): version}), newest first."""
    with registry._conn() as conn:
        stamps = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT indexed_at FROM project_dependencies WHERE project_slug = ? "
                "ORDER BY indexed_at DESC LIMIT 2",
                (slug,),
            ).fetchall()
        ]
        batches = []
        for ts in stamps:
            rows = conn.execute(
                "SELECT ecosystem, dep_name, dep_version FROM project_dependencies "
                "WHERE project_slug = ? AND indexed_at = ?",
                (slug, ts),
            ).fetchall()
            batches.append((ts, {(r["ecosystem"], r["dep_name"]): r["dep_version"] or "" for r in rows}))
    return batches


def dependency_changes(registry: "ProjectRegistry", resource_slug: str) -> str:
    """Dependency drift between the two most recent ingests of a repository."""
    slug = registry._normalize_slug(resource_slug)
    if not registry.get(slug):
        return f"Repository '{resource_slug}' is not registered."

    batches = _dependency_batches(registry, slug)
    if not batches:
        return (
            f"No dependencies are recorded for '{slug}', so there is nothing to compare "
            "(that is not the same as having none)."
        )
    if len(batches) == 1:
        return (
            f"Dependencies for '{slug}' were recorded once ({batches[0][0]}); "
            "a second ingest is needed before drift can be shown."
        )

    (new_ts, new), (old_ts, old) = batches[0], batches[1]
    added = sorted(f"{n} {new[(e, n)]}".strip() for e, n in set(new) - set(old))
    removed = sorted(f"{n} {old[(e, n)]}".strip() for e, n in set(old) - set(new))
    bumped = sorted(
        f"{n}: {old[(e, n)] or '?'} -> {new[(e, n)] or '?'}"
        for e, n in set(new) & set(old)
        if old[(e, n)] != new[(e, n)]
    )
    header = f"Dependency changes for `{slug}`: {old_ts} -> {new_ts} ({len(old)} -> {len(new)} dependencies)"
    lines = []
    if added:
        lines.append(f"- Added ({len(added)}): {_capped(added)}")
    if removed:
        lines.append(f"- Removed ({len(removed)}): {_capped(removed)}")
    if bumped:
        lines.append(f"- Version changes ({len(bumped)}): {_capped(bumped)}")
    if not lines:
        return f"{header}\nNo differences between these two ingests."
    return "\n".join([header, *lines])
