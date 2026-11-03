"""Statistics endpoints — project metrics and chart data."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from resource_explorer.registry import ProjectRegistry

router = APIRouter()

_VALID_METRICS = frozenset({
    "stars", "forks", "watchers", "open_issues",
    "contributors_count", "commits_30d", "commits_90d",
    "releases_count",
})


@router.get("/{slug}")
async def get_stats(slug: str) -> dict:
    registry = ProjectRegistry()
    if not registry.exists(slug):
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")

    row = _latest_stats(registry, slug)
    if not row:
        raise HTTPException(status_code=404, detail=f"No stats for '{slug}' — run refresh first")

    # Parse language_breakdown from stored string
    lang_raw = row.get("language_breakdown") or "{}"
    try:
        try:
            lang = json.loads(lang_raw)
        except json.JSONDecodeError:
            import ast
            lang = ast.literal_eval(lang_raw)
    except Exception:
        lang = {}

    return {
        "slug": slug,
        "fetched_at": row.get("fetched_at"),
        "stats": {
            "stars": row.get("stars"),
            "forks": row.get("forks"),
            "watchers": row.get("watchers"),
            "open_issues": row.get("open_issues"),
            "contributors_count": row.get("contributors_count"),
            "commits_30d": row.get("commits_30d"),
            "commits_90d": row.get("commits_90d"),
            "releases_count": row.get("releases_count"),
            "latest_release": row.get("latest_release"),
            "latest_release_at": row.get("latest_release_at"),
            "primary_language": row.get("primary_language"),
            "language_breakdown": lang,
            "file_count": row.get("ingestion_file_count") or row.get("file_count"),
            # Renamed on the wire (design doc D1): this counts every newline in
            # every text-suffixed file, so it is text lines and not code —
            # 1,118,195 vs 156,902 real Python code lines on egeria-python.
            # Real code volume is the `code_volume` metric, decomposed per
            # language. The old key is kept alongside for one release so a
            # reader is not broken silently, and is marked in its own name.
            "text_lines_all_files": row.get("ingestion_lines_of_code") or row.get("lines_of_code"),
            "lines_of_code_deprecated_counts_all_text": (
                row.get("ingestion_lines_of_code") or row.get("lines_of_code")),
            "file_count_exact": row.get("ingestion_file_count") is not None,
        },
    }


@router.get("/{slug}/history")
async def get_stats_history(
    slug: str,
    metric: str = "stars",
    limit: int = 30,
) -> dict:
    if metric not in _VALID_METRICS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid metric '{metric}'. Valid: {sorted(_VALID_METRICS)}",
        )

    registry = ProjectRegistry()
    if not registry.exists(slug):
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")

    rows = _history(registry, slug, metric, limit)
    return {
        "slug": slug,
        "metric": metric,
        "data": rows,
    }


@router.get("/{slug}/charts/stars")
async def stars_chart(slug: str) -> dict:
    """Return Plotly figure JSON for the star-growth chart."""
    from resource_explorer.dashboard.graphs import stars_over_time_plotly
    fig = stars_over_time_plotly(slug)
    return json.loads(fig.to_json())


@router.get("/{slug}/charts/commits")
async def commits_chart(slug: str) -> dict:
    """Return Plotly figure JSON for weekly commit activity (last 13 weeks)."""
    from resource_explorer.dashboard.graphs import weekly_commits_plotly
    fig = weekly_commits_plotly(slug)
    return json.loads(fig.to_json())


@router.get("/{slug}/charts/languages")
async def languages_chart(slug: str) -> dict:
    """Return Plotly figure JSON for the language-breakdown pie chart."""
    from resource_explorer.dashboard.graphs import language_breakdown_plotly
    from fastapi import HTTPException
    fig = language_breakdown_plotly(slug)
    fig_dict = json.loads(fig.to_json())
    # Return 404 when the pie has no slices so the UI shows "No data" instead of a blank chart
    if not fig_dict.get("data") or not fig_dict["data"][0].get("labels"):
        raise HTTPException(
            status_code=404,
            detail=f"No language data for '{slug}' — run 'project-explorer refresh {slug}' first",
        )
    return fig_dict


@router.get("/{slug}/charts/top_committers")
async def top_committers_chart(slug: str) -> dict:
    """Return Plotly figure JSON for the top-committers horizontal bar chart."""
    from resource_explorer.dashboard.graphs import top_committers_plotly
    from fastapi import HTTPException
    fig = top_committers_plotly(slug)
    if fig is None:
        raise HTTPException(
            status_code=404,
            detail=f"No commit data for '{slug}' — run 'project-explorer refresh {slug}' first",
        )
    return json.loads(fig.to_json())


@router.get("/{slug}/charts/weekly_commits")
async def weekly_commits_chart(slug: str) -> dict:
    """Return Plotly figure JSON for the weekly commit-activity bar chart."""
    from resource_explorer.dashboard.graphs import weekly_commits_plotly
    fig = weekly_commits_plotly(slug)
    return json.loads(fig.to_json())


@router.get("/compare/charts/stats")
async def compare_stats_chart(slugs: str) -> dict:
    """Return Plotly grouped bar chart comparing stats across comma-separated project slugs.

    Example: GET /api/stats/compare/charts/stats?slugs=proj_a,proj_b
    """
    from resource_explorer.dashboard.graphs import compare_stats_plotly
    slug_list = [s.strip() for s in slugs.split(",") if s.strip()]
    if len(slug_list) < 2:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Provide at least two comma-separated slugs")
    fig = compare_stats_plotly(slug_list)
    return json.loads(fig.to_json())


@router.get("/{slug}/charts/file_types")
async def file_types_chart(slug: str) -> dict:
    """Return Plotly figure JSON for the file-count-by-extension bar chart."""
    from resource_explorer.dashboard.graphs import file_types_plotly
    fig = file_types_plotly(slug)
    return json.loads(fig.to_json())


@router.get("/{slug}/charts/survey_history")
async def repo_survey_history(slug: str) -> dict:
    """Return file-count-over-time series for the repo survey history chart."""
    registry = ProjectRegistry()
    history = registry.query_file_type_history(slug)
    if not history:
        return {"dates": [], "total_files": []}
    return {
        "dates": [r["surveyed_at"][:16].replace("T", " ") for r in history],
        "total_files": [int(r["total_files"] or 0) for r in history],
    }


@router.get("/{slug}/charts/health")
async def health_chart(slug: str) -> dict:
    """Return Plotly figure JSON for the project-health radar chart."""
    from resource_explorer.dashboard.graphs import health_radar_plotly
    fig = health_radar_plotly(slug)
    return json.loads(fig.to_json())


# ── database charts ───────────────────────────────────────────────────────────

def _database_or_404(registry: ProjectRegistry, slug: str) -> None:
    if not registry.get_database(slug, allow_unreadable=True):
        raise HTTPException(status_code=404, detail=f"Database '{slug}' not found")


# The four database chart routes read the structured `database_tables` /
# `database_columns` / `database_schemas` rows (as `/api/databases/{slug}/diff`
# does), not the `survey_data` blob, and return: a Plotly `figure`, the `run` it
# read, and a `state` (measured / partial / not_measured). The pre-2026-10-01
# array fields are kept alongside, additively, for Classic. Logic lives in
# `resource_explorer.db_chart_data`. A never-surveyed database is a 200 with
# state `not_measured`, not a 404 and not zeros.

@router.get("/databases/{slug}/schema_distribution")
async def database_schema_distribution(slug: str) -> dict:
    """Tables and columns per schema, with the run and state it came from."""
    from resource_explorer import db_chart_data
    registry = ProjectRegistry()
    _database_or_404(registry, slug)
    return db_chart_data.schema_distribution(registry, slug)


@router.get("/databases/{slug}/table_sizes")
async def database_table_sizes(slug: str, limit: int = 20, measure: str = "rows") -> dict:
    """Largest tables. `measure=rows` (default) never switches to size on its
    own; tables with no established count are listed, counted, not ranked."""
    from resource_explorer import db_chart_data
    if measure not in ("rows", "size"):
        raise HTTPException(status_code=400, detail="measure must be 'rows' or 'size'")
    registry = ProjectRegistry()
    _database_or_404(registry, slug)
    return db_chart_data.table_sizes(registry, slug, limit=limit, measure=measure)


@router.get("/databases/{slug}/column_types")
async def database_column_types(slug: str) -> dict:
    """Columns per declared type; a missing type is `type_not_recorded`."""
    from resource_explorer import db_chart_data
    registry = ProjectRegistry()
    _database_or_404(registry, slug)
    return db_chart_data.column_types(registry, slug)


@router.get("/databases/{slug}/survey_history")
async def database_survey_history(slug: str, limit: int = 30) -> dict:
    """One point per run (full timestamp); unmeasured counts are null."""
    from resource_explorer import db_chart_data
    registry = ProjectRegistry()
    _database_or_404(registry, slug)
    return db_chart_data.survey_history(registry, slug, limit=limit)


@router.get("/databases/{slug}/table_growth")
async def database_table_growth(slug: str, top: int = 10, limit: int = 30) -> dict:
    """Established row count per run for the largest tables (growth over time)."""
    from resource_explorer import db_chart_data
    registry = ProjectRegistry()
    _database_or_404(registry, slug)
    return db_chart_data.table_growth(registry, slug, top=top, limit=limit)


@router.get("/databases/{slug}/statistics_currency")
async def database_statistics_currency(slug: str, limit: int = 12) -> dict:
    """How current the planner statistics are: database line, schema rollups,
    per-table state, and the counts across earlier surveys. Stored rows only."""
    from resource_explorer import db_chart_data
    registry = ProjectRegistry()
    _database_or_404(registry, slug)
    return db_chart_data.statistics_currency(registry, slug, limit=limit)


# ── helpers ───────────────────────────────────────────────────────────────────

def _latest_stats(registry, slug: str) -> dict:
    try:
        with registry._conn() as conn:
            row = conn.execute(
                "SELECT * FROM project_stats WHERE project_slug = ? ORDER BY fetched_at DESC LIMIT 1",
                (slug,),
            ).fetchone()
        return dict(row) if row else {}
    except Exception:
        return {}


def _history(registry, slug: str, metric: str, limit: int) -> list[dict]:
    try:
        with registry._conn() as conn:
            rows = conn.execute(
                f"SELECT fetched_at, {metric} FROM project_stats "  # noqa: S608 — metric validated above
                "WHERE project_slug = ? ORDER BY fetched_at ASC LIMIT ?",
                (slug, min(limit, 365)),
            ).fetchall()
        return [{"date": r["fetched_at"][:10], "value": r[metric]} for r in rows]
    except Exception:
        return []
