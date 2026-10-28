"""Find and rank resources from what the registry already holds.

"Which repos use sqlalchemy?" and "which databases have the most undocumented tables?" are filters and
sorts over stored surveys. The point of doing them here is the denominator: a repo with no recorded
dependencies, or a table the survey could only see through the catalog, is *unknown*, not a
non-user or an undocumented table, and the answer says how many were left out and why.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from resource_explorer.registry import ProjectRegistry

log = logging.getLogger(__name__)

_LIST_LIMIT = 25
DATABASE_METRICS = ("tables", "columns", "undocumented_tables")


def _canonical(name: str) -> str:
    """Package-name spelling folded the way pip folds it: `Foo_Bar`, `foo.bar` and `foo-bar` are one name."""
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def repos_using_dependency(
    registry: "ProjectRegistry", name: str, ecosystem: str = "", contains: bool = False
) -> str:
    """Repositories whose latest recorded dependencies include `name`.

    An exact package name by default (spelling-folded), because `sql` is not `sqlalchemy`; `contains`
    widens it to a substring for exploratory questions.
    """
    needle = (name or "").strip().lower()
    if not needle:
        return "Give a dependency name to look for."
    like = "%" + needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

    # Exact matching folds `-`, `_` and `.`, which SQL LIKE cannot, so it filters in Python; only the
    # substring search narrows in SQL.
    narrow = "AND LOWER(d.dep_name) LIKE ? ESCAPE '\\' " if contains else ""
    eco = "AND LOWER(d.ecosystem) = ? " if ecosystem else ""
    params = ([like] if contains else []) + ([ecosystem.strip().lower()] if ecosystem else [])
    with registry._conn() as conn:
        rows = conn.execute(
            "SELECT d.project_slug, d.dep_name, d.dep_version, d.ecosystem "
            "FROM project_dependencies d "
            "JOIN (SELECT project_slug, MAX(indexed_at) AS latest FROM project_dependencies "
            "      GROUP BY project_slug) l "
            "  ON l.project_slug = d.project_slug AND l.latest = d.indexed_at "
            f"WHERE 1=1 {narrow}{eco}"
            "ORDER BY d.project_slug, d.dep_name",
            tuple(params),
        ).fetchall()
        with_deps = {
            r[0] for r in conn.execute("SELECT DISTINCT project_slug FROM project_dependencies").fetchall()
        }

    repos = [p.slug for p in registry.list_all()]
    unknown = sorted(s for s in repos if s not in with_deps)
    wanted = _canonical(needle)
    by_repo: dict[str, list[str]] = {}
    for r in rows:
        if not contains and _canonical(r["dep_name"]) != wanted:
            continue
        spec = f"{r['dep_name']} {r['dep_version'] or ''}".strip()
        specs = by_repo.setdefault(r["project_slug"], [])
        if spec not in specs:  # the same pin often appears in several manifests of one repo
            specs.append(spec)

    scope = (f" ({ecosystem} only)" if ecosystem else "") + (" (name contains)" if contains else "")
    lines = [f"Repositories whose latest dependencies match '{name}'{scope}: {len(by_repo)} of {len(repos)} registered."]
    for slug in sorted(by_repo)[:_LIST_LIMIT]:
        lines.append(f"- `{slug}`: {', '.join(by_repo[slug][:5])}")
    if len(by_repo) > _LIST_LIMIT:
        lines.append(f"- ... and {len(by_repo) - _LIST_LIMIT} more")
    if unknown:
        shown = ", ".join(unknown[:_LIST_LIMIT])
        lines.append(
            f"Unknown ({len(unknown)}): no dependencies are recorded for {shown}"
            + (" ..." if len(unknown) > _LIST_LIMIT else "")
            + " -- they may or may not use it."
        )
    return "\n".join(lines)


def _database_figures(info: dict) -> dict:
    """Counts from one `schema_info`, separating what was measured from what the survey could not see."""
    tables = columns = documented = undocumented = unmeasured = 0
    for sch in info.get("schemas", []):
        readable = sch.get("access") != "not_readable"
        for tbl in sch.get("tables", []):
            tables += 1
            columns += len(tbl.get("columns", []))
            # A table seen only through the catalog never had its comment read: unknown, not undocumented.
            if not readable or tbl.get("source") == "catalog_fallback":
                unmeasured += 1
            elif (tbl.get("description") or "").strip():
                documented += 1
            else:
                undocumented += 1
    return {
        "tables": tables, "columns": columns, "documented": documented,
        "undocumented_tables": undocumented, "unmeasured": unmeasured,
    }


def rank_databases(registry: "ProjectRegistry", metric: str = "tables", limit: int = 10) -> str:
    """Databases ordered by a figure from their latest measured survey."""
    from resource_explorer.registry import _survey_schema_info, is_measured_survey

    metric = (metric or "tables").strip().lower()
    if metric not in DATABASE_METRICS:
        return f"Unknown metric '{metric}'; use one of: {', '.join(DATABASE_METRICS)}."
    limit = max(1, min(int(limit or 10), _LIST_LIMIT))

    databases = registry.list_databases()
    if not databases:
        return "No databases are registered."
    ranked: list[tuple[str, dict]] = []
    unsurveyed: list[str] = []
    for db in databases:
        survey = registry.get_latest_database_survey(db.slug)
        if not is_measured_survey(survey):
            unsurveyed.append(db.slug)
            continue
        ranked.append((db.slug, _database_figures(_survey_schema_info(survey))))

    ranked.sort(key=lambda kv: (-kv[1][metric], kv[0]))
    lines = [f"Databases ranked by {metric.replace('_', ' ')} ({len(ranked)} of {len(databases)} have a measured survey):"]
    for place, (slug, f) in enumerate(ranked[:limit], start=1):
        extra = ""
        if metric == "undocumented_tables":
            extra = f" of {f['documented'] + f['undocumented_tables']} readable"
            if f["unmeasured"]:
                extra += (
                    f"; {f['unmeasured']} table(s) whose description could not be read "
                    "(catalog-only or in an unreadable schema), so not counted either way"
                )
        lines.append(f"{place}. `{slug}`: {f[metric]}{extra}")
    if len(ranked) > limit:
        lines.append(f"... and {len(ranked) - limit} more")
    if unsurveyed:
        lines.append(
            f"Not ranked ({len(unsurveyed)}): no measured survey for {', '.join(sorted(unsurveyed)[:_LIST_LIMIT])}."
        )
    return "\n".join(lines)
