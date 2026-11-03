"""Database Understanding charts, built on the structured detail rows.

Until 2026-10-01 the four database chart routes in `web/routes/stats.py` read
the latest run's `survey_data` blob and turned "not measured" into zero
(`table.get("row_count", 0)`, `schema_count` defaulting to 0, a missing column
type bucketed as "unknown"). `get_database_diff` stopped reading that blob on
2026-09-20 for the same reason; this module follows that route's approach
(`database_tables` / `database_columns` / `database_schemas` rows plus the
`database_survey_coverage` record of what each run attempted) and returns, per
chart: a figure, the run it came from, and a state.

State vocabulary (`state`):
  measured      the run measured this and nothing about it is caveated.
  partial       measured, with a stated limit: some row counts / types were
                not established, or the credential could only see part of the
                database (`reasons` says which, `scope` carries the fraction).
  not_measured  the run did not measure this (never surveyed, not
                materialized, step did not run, not permitted). `reasons`
                carries the specific coverage state. Never rendered as zero.

NULL stays NULL: a table with no established row count is counted and named
(`not_established_*`), never ranked as 0 rows.
"""
from __future__ import annotations

from typing import Any

STATE_MEASURED = "measured"
STATE_PARTIAL = "partial"
STATE_NOT_MEASURED = "not_measured"

#: Relation kinds a row count does not apply to. A view has no row count to
#: "establish", so reporting it as "statistics not gathered" would be false.
_NO_ROW_COUNT_TYPES = frozenset({"VIEW"})

NO_TYPE_LABEL = "type not recorded"


# ── run + section helpers ────────────────────────────────────────────────────


def valid_runs(registry, slug: str, limit: int | None = None) -> list[dict]:
    """Valid (not soft-invalid) survey runs, newest first, WITHOUT the blob.

    `get_database_surveys` fetches every historical `survey_data` blob (61MB
    for a heavily surveyed database); the chart routes never need it now.
    """
    sql = (
        "SELECT id, surveyed_at, source, surveyed_as FROM database_surveys "
        "WHERE database_slug = ? AND invalid_at IS NULL "
        "ORDER BY surveyed_at DESC"
    )
    params: list[Any] = [registry._normalize_slug(slug)]
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    with registry._conn() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def run_ref(run: dict | None) -> dict | None:
    """What a response says about the run it read."""
    if not run:
        return None
    return {
        "run_id": run.get("id"),
        "surveyed_at": run.get("surveyed_at"),
        "source": run.get("source") or "local",
        "surveyed_as": run.get("surveyed_as") or "",
    }


def section_state(
    registry, slug: str, run: dict, section: str, n_rows: int
) -> tuple[str, str]:
    """(state, note) for one section of one run.

    The logic `get_database_diff._tables_for` carried inline, extracted so the
    diff and the charts cannot disagree about what a run did and did not
    measure. Returns registry.STATE_MEASURED only when the run has structured
    rows; `not_materialized` when it has neither rows nor a coverage record
    (a run that predates the structured tables and was never back-filled).
    """
    from resource_explorer.registry import (
        STATE_EMPTY, STATE_MEASURED, STATES_WITHOUT_A_MEASUREMENT,
    )
    if n_rows:
        return STATE_MEASURED, ""
    coverage = registry.get_section_coverage(
        "database", slug, run.get("surveyed_at") or "", run.get("source") or None
    ).get(section)
    if coverage is None:
        return "not_materialized", (
            "This run has no structured rows yet — run "
            "scripts/backfill_structured_tables.py to convert its stored "
            "survey_data."
        )
    state = coverage.get("state") or STATE_EMPTY
    if state in STATES_WITHOUT_A_MEASUREMENT:
        return state, coverage.get("detail") or ""
    return STATE_EMPTY, coverage.get("detail") or ""


def _is_measured(section_state_value: str) -> bool:
    from resource_explorer.registry import STATE_EMPTY, STATE_MEASURED
    return section_state_value in (STATE_MEASURED, STATE_EMPTY)


def credential_scope(registry, slug: str) -> dict:
    """The credential-scope partial mark, if the data carries one.

    Reuses the fact layer's own reading (`_credential_scope_status`), which
    searches stored surveys newest-first for the latest `credential_capability`
    probe. That probe is therefore the latest one ON RECORD, not necessarily
    the one taken in the run a chart reads (`from_run` is not recorded on it),
    and the response says so rather than implying a match.
    """
    try:
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            _credential_scope_status,
        )
        status = _credential_scope_status(registry, slug)
    except Exception as exc:  # a chart must not die on its caveat; say so
        return {"checked": False, "partial": False, "error": f"{type(exc).__name__}: {exc}"}
    if not status:
        return {"checked": True, "partial": False}
    return {
        "checked": True,
        "partial": True,
        "fraction": status.get("fraction", ""),
        "schema_fraction": status.get("schema_fraction", ""),
        "connected_as": status.get("connected_as", ""),
        "by_container": {
            n: {"state": s.get("state"), "table_select": s.get("table_select"),
                "table_total": s.get("table_total")}
            for n, s in (status.get("by_container") or {}).items()
        },
        "probe_note": "from the latest survey on record that carried a "
                      "credential probe, which may not be this run",
    }


def _envelope(registry, slug: str, run: dict | None) -> dict:
    return {
        "database": slug,
        "run": run_ref(run),
        "state": STATE_NOT_MEASURED,
        "reasons": [],
        "notes": [],
        "scope": None,
        "figure": None,
    }


def _finish(registry, slug: str, env: dict, measured: bool, caveats: list[str]) -> dict:
    """Settle `state` from what was measured and what was caveated."""
    if not measured:
        env["state"] = STATE_NOT_MEASURED
        return env
    env["scope"] = credential_scope(registry, slug)
    if env["scope"].get("partial"):
        caveats = caveats + ["credential_scope"]
    env["reasons"] = list(dict.fromkeys(env["reasons"] + caveats))
    env["state"] = STATE_PARTIAL if env["reasons"] else STATE_MEASURED
    return env


def _not_run(env: dict, reason: str, note: str = "") -> dict:
    env["state"] = STATE_NOT_MEASURED
    env["reasons"] = [reason]
    if note:
        env["notes"].append(note)
    return env


def _table_rows(registry, slug: str, run: dict) -> list[dict]:
    return registry.query_detail_rows(
        "database_tables", slug, run["surveyed_at"], run.get("source") or None
    )


def _qualified(r: dict) -> str:
    return f"{r.get('schema_name') or ''}.{r.get('table_name') or ''}"


def _group_counts(registry, table: str, slug: str, run: dict, col: str) -> dict[str, int]:
    with registry._conn() as conn:
        rows = conn.execute(
            f"SELECT {col} AS k, COUNT(*) AS n FROM {table} "
            "WHERE database_slug = ? AND surveyed_at = ? AND source = ? "
            f"GROUP BY {col}",
            (registry._normalize_slug(slug), run["surveyed_at"], run.get("source") or "local"),
        ).fetchall()
    out: dict[str, int] = {}
    for r in rows:
        r = dict(r)
        k = r["k"] if r["k"] is not None else ""   # NULL and '' are the same "missing"
        out[k] = out.get(k, 0) + r["n"]
    return out


# ── schema_distribution ──────────────────────────────────────────────────────


def schema_distribution(registry, slug: str) -> dict:
    runs = valid_runs(registry, slug, limit=1)
    run = runs[0] if runs else None
    env = _envelope(registry, slug, run)
    env.update({"schemas": [], "table_counts": [], "column_counts": [],
                "schema_scope": {}})
    if not run:
        return _not_run(env, "never_surveyed", "No survey has run for this database.")

    tables = _table_rows(registry, slug, run)
    t_state, t_note = section_state(registry, slug, run, "tables", len(tables))
    if not _is_measured(t_state):
        return _not_run(env, t_state, t_note)

    per_schema_tables: dict[str, int] = {}
    for t in tables:
        s = t.get("schema_name") or ""
        per_schema_tables[s] = per_schema_tables.get(s, 0) + 1
    # schemas that exist but hold no tables still belong on the chart
    schema_rows = registry.query_detail_rows(
        "database_schemas", slug, run["surveyed_at"], run.get("source") or None)
    for s in schema_rows:
        per_schema_tables.setdefault(s.get("schema_name") or "", 0)

    col_counts = _group_counts(registry, "database_columns", slug, run, "schema_name")
    c_state, c_note = section_state(
        registry, slug, run, "columns", sum(col_counts.values()))
    columns_measured = _is_measured(c_state)

    names = sorted(per_schema_tables)
    env["schemas"] = names
    env["table_counts"] = [per_schema_tables[n] for n in names]
    # None, not 0, when the run did not measure columns
    env["column_counts"] = (
        [col_counts.get(n, 0) for n in names] if columns_measured else [None] * len(names)
    )
    caveats = []
    if not columns_measured:
        caveats.append(f"columns_{c_state}")
        env["notes"].append(c_note or f"Columns were not measured in this run ({c_state}).")
    env = _finish(registry, slug, env, True, caveats)
    env["schema_scope"] = {
        n: (env["scope"].get("by_container") or {}).get(n, {}).get("state", "readable")
        for n in names
    } if env["scope"] and env["scope"].get("partial") else {}
    env["figure"] = {
        "data": [
            {"type": "bar", "orientation": "h", "name": "Tables",
             "y": names, "x": env["table_counts"], "xaxis": "x", "yaxis": "y"},
            {"type": "bar", "orientation": "h", "name": "Columns",
             "y": names, "x": env["column_counts"], "xaxis": "x2", "yaxis": "y"},
        ],
        "layout": {
            "title": {"text": "Tables and columns per schema"},
            "xaxis": {"domain": [0, 0.45], "title": {"text": "Tables"}},
            "xaxis2": {"domain": [0.55, 1], "title": {"text": "Columns"}},
            "yaxis": {"autorange": "reversed"},
            "showlegend": False,
        },
    }
    return env


# ── table_sizes ──────────────────────────────────────────────────────────────


def _ranked_with_activity(registry, slug: str, run: dict, top: list[dict]) -> list[dict]:
    """The ranked tables of `table_sizes` as rows a table can show: rows, size,
    and what the same run's activity rows say about analysis and pending changes.

    A table with no activity row is `activity_state: not_collected` with `None`
    for both, never a date it does not have and never a zero.
    """
    activity: dict[str, dict] = {}
    try:
        for a in registry.query_detail_rows(
                "database_table_activity", slug, run["surveyed_at"], run.get("source") or None):
            activity[_qualified(a)] = a
    except Exception:  # an unreadable activity table is "not collected", not a failure of the chart
        activity = {}
    from resource_explorer.statistics_currency import table_currency
    out = []
    for t in top:
        a = activity.get(_qualified(t))
        out.append({
            "statistics": table_currency(a, run["surveyed_at"], table=t),
            "name": _qualified(t),
            "row_count": t.get("row_count"),
            "size_bytes": t.get("size_bytes"),
            "last_analyzed": ((a.get("last_analyze") or a.get("last_autoanalyze") or None)
                              if a else None),
            "pending_changes": a.get("pending_changes") if a else None,
            "activity_state": "measured" if a else "not_collected",
        })
    return out


def _totals(counted: list[dict]) -> dict:
    """Whole-database sums over the tables the same read already holds. A sum covers only the
    tables whose figure is established, and says how many those are, so a reader can tell a
    total from a lower bound; a figure no table established is None, never 0."""
    def add(key):
        vals = [t.get(key) for t in counted if t.get(key) is not None]
        return (sum(vals) if vals else None), len(vals)
    rows, rows_n = add("row_count")
    size, size_n = add("size_bytes")
    return {"tables": len(counted), "rows": rows, "rows_tables": rows_n,
            "size_bytes": size, "size_tables": size_n}


def table_sizes(registry, slug: str, limit: int = 20, measure: str = "rows") -> dict:
    if measure not in ("rows", "size"):
        raise ValueError("measure must be 'rows' or 'size'")
    runs = valid_runs(registry, slug, limit=1)
    run = runs[0] if runs else None
    env = _envelope(registry, slug, run)
    env.update({
        "measure": measure,
        "tables": [], "row_counts": [], "sizes_mb": [],
        "table_count_total": 0, "views_excluded": 0,
        "not_established_count": 0, "not_established_tables": [],
        "ranked_count": 0, "truncated_by_limit": 0, "ranked": [],
        "provenance": "", "totals": None,
    })
    if not run:
        return _not_run(env, "never_surveyed", "No survey has run for this database.")
    tables = _table_rows(registry, slug, run)
    t_state, t_note = section_state(registry, slug, run, "tables", len(tables))
    if not _is_measured(t_state):
        return _not_run(env, t_state, t_note)

    key = "row_count" if measure == "rows" else "size_bytes"
    counted = [t for t in tables if (t.get("table_type") or "") not in _NO_ROW_COUNT_TYPES]
    env["views_excluded"] = len(tables) - len(counted)
    env["table_count_total"] = len(counted)
    established = [t for t in counted if t.get(key) is not None]
    missing = [t for t in counted if t.get(key) is None]
    established.sort(key=lambda t: (t[key], _qualified(t)), reverse=True)
    top = established[: max(0, int(limit))]
    env["ranked_count"] = len(established)
    env["truncated_by_limit"] = len(established) - len(top)
    env["not_established_count"] = len(missing)
    env["not_established_tables"] = sorted(_qualified(t) for t in missing)
    env["totals"] = _totals(counted)

    def mb(v):
        return None if v is None else round(v / 1_048_576, 2)

    env["tables"] = [_qualified(t) for t in top]
    env["row_counts"] = [t.get("row_count") for t in top]
    env["sizes_mb"] = [mb(t.get("size_bytes")) for t in top]
    env["ranked"] = _ranked_with_activity(registry, slug, run, top)
    env["provenance"] = (
        "rows, as estimated by the server's statistics" if measure == "rows"
        else "relation size on disk, in MB"
    )

    caveats = []
    what = "row counts" if measure == "rows" else "sizes"
    if missing:
        caveats.append(f"{what.replace(' ', '_')}_not_established")
        env["notes"].append(
            f"{len(missing)} of {len(counted)} tables' {what} not established "
            "— statistics not gathered on the server. They are not ranked, "
            "not drawn as zero, and are named in not_established_tables."
        )
    measured = bool(established) or not counted
    if counted and not established:
        # Tables are known, the measure is not: not measured, not "all zero".
        env["reasons"] = [f"{what.replace(' ', '_')}_not_established"]
        env["notes"].append(
            "No table has an established " + what.rstrip("s") + " in this run."
            + (" The size measure may still be available (measure=size)."
               if measure == "rows" else ""))
        env["state"] = STATE_NOT_MEASURED
        return env
    env = _finish(registry, slug, env, measured, caveats)
    env["figure"] = {
        "data": [{"type": "bar", "orientation": "h",
                  "y": env["tables"],
                  "x": env["row_counts"] if measure == "rows" else env["sizes_mb"]}],
        "layout": {
            "title": {"text": f"Largest tables, by {'rows' if measure == 'rows' else 'size (MB)'}"},
            "xaxis": {"title": {"text": "Rows" if measure == "rows" else "Size (MB)"}},
            "yaxis": {"autorange": "reversed"},
        },
    }
    return env


# ── column_types ─────────────────────────────────────────────────────────────


def column_types(registry, slug: str) -> dict:
    runs = valid_runs(registry, slug, limit=1)
    run = runs[0] if runs else None
    env = _envelope(registry, slug, run)
    env.update({"types": [], "counts": [], "type_not_recorded": 0,
                "column_count_total": 0})
    if not run:
        return _not_run(env, "never_surveyed", "No survey has run for this database.")
    by_type = _group_counts(registry, "database_columns", slug, run, "data_type")
    total = sum(by_type.values())
    c_state, c_note = section_state(registry, slug, run, "columns", total)
    if not _is_measured(c_state):
        return _not_run(env, c_state, c_note)

    missing = by_type.pop("", 0)
    real = sorted(by_type.items(), key=lambda kv: (-kv[1], kv[0]))
    env["types"] = [t for t, _ in real]
    env["counts"] = [n for _, n in real]
    env["type_not_recorded"] = missing   # its own field, never a bucket
    env["column_count_total"] = total
    caveats = []
    if missing:
        caveats.append("type_not_recorded")
        env["notes"].append(
            f"{missing} of {total} columns have no recorded type; they are "
            f"counted in type_not_recorded, not as a type.")
    env = _finish(registry, slug, env, True, caveats)
    env["figure"] = {
        "data": [{"type": "bar", "x": env["types"], "y": env["counts"]}],
        "layout": {"title": {"text": "Columns per declared type"},
                   "yaxis": {"title": {"text": "Columns"}}},
    }
    return env


# ── survey_history ───────────────────────────────────────────────────────────


def _per_run_counts(registry, table: str, slug: str) -> dict[tuple, int]:
    with registry._conn() as conn:
        rows = conn.execute(
            f"SELECT surveyed_at, source, COUNT(*) AS n FROM {table} "
            "WHERE database_slug = ? GROUP BY surveyed_at, source",
            (registry._normalize_slug(slug),),
        ).fetchall()
    return {(dict(r)["surveyed_at"], dict(r)["source"]): dict(r)["n"] for r in rows}


def _per_run_coverage(registry, slug: str) -> dict[tuple, dict[str, str]]:
    with registry._conn() as conn:
        rows = conn.execute(
            "SELECT surveyed_at, source, section, state FROM database_survey_coverage "
            "WHERE database_slug = ?",
            (registry._normalize_slug(slug),),
        ).fetchall()
    out: dict[tuple, dict[str, str]] = {}
    for r in rows:
        r = dict(r)
        out.setdefault((r["surveyed_at"], r["source"]), {})[r["section"]] = r["state"]
    return out


def survey_history(registry, slug: str, limit: int = 30) -> dict:
    from resource_explorer.registry import STATE_EMPTY, STATE_MEASURED, STATES_WITHOUT_A_MEASUREMENT
    runs = valid_runs(registry, slug, limit=limit)
    env = _envelope(registry, slug, runs[0] if runs else None)
    env.update({"points": [], "dates": [], "schema_counts": [], "table_counts": [],
                "column_counts": [],
                "diff_route": f"/api/databases/{slug}/diff"})
    if not runs:
        return _not_run(env, "never_surveyed", "No survey has run for this database.")

    n_schemas = _per_run_counts(registry, "database_schemas", slug)
    n_tables = _per_run_counts(registry, "database_tables", slug)
    n_columns = _per_run_counts(registry, "database_columns", slug)
    coverage = _per_run_coverage(registry, slug)

    def one(run, counts, section):
        k = (run["surveyed_at"], run.get("source") or "local")
        n = counts.get(k, 0)
        if n:
            return n, STATE_MEASURED
        cov = (coverage.get(k) or {}).get(section)
        if cov is None:
            return None, "not_materialized"
        if cov in STATES_WITHOUT_A_MEASUREMENT:
            return None, cov
        return 0, STATE_EMPTY   # measured, and genuinely nothing

    points = []
    for run in reversed(runs):                   # oldest first
        s, ss = one(run, n_schemas, "schemas")
        t, ts = one(run, n_tables, "tables")
        c, cs = one(run, n_columns, "columns")
        points.append({
            "run": run_ref(run),
            "schema_count": s, "table_count": t, "column_count": c,
            "states": {"schemas": ss, "tables": ts, "columns": cs},
        })
    env["points"] = points
    # Legacy parallel arrays: one entry PER RUN with the full timestamp (two
    # runs on one day are two points); None, never 0, where not measured.
    env["dates"] = [p["run"]["surveyed_at"] for p in points]
    env["schema_counts"] = [p["schema_count"] for p in points]
    env["table_counts"] = [p["table_count"] for p in points]
    env["column_counts"] = [p["column_count"] for p in points]

    measured_runs = [p for p in points if p["table_count"] is not None]
    skipped = len(points) - len(measured_runs)
    caveats = []
    if skipped:
        caveats.append("runs_without_table_measurement")
        env["notes"].append(
            f"{skipped} of {len(points)} runs did not measure tables; they "
            "are present in `points` with null counts and a state, and are "
            "gaps in the line, not zeros."
        )
    env["notes"].append(
        "Schema/table/column changes between the two latest runs: see "
        f"{env['diff_route']} (names, not counts; latest pair only)."
    )
    env = _finish(registry, slug, env, bool(measured_runs), caveats)
    if not measured_runs:
        env["reasons"] = ["no_run_measured_tables"]
        return env

    def trace(name, key):
        return {"type": "scatter", "mode": "lines+markers", "name": name,
                "x": env["dates"], "y": [p[key] for p in points],
                "connectgaps": False}
    env["figure"] = {
        "data": [trace("Schemas", "schema_count"), trace("Tables", "table_count"),
                 trace("Columns", "column_count")],
        "layout": {"title": {"text": "Structure over runs"},
                   "xaxis": {"type": "date"}},
    }
    return env


# ── table_growth (owner wish 2026-10-01: rows per table over time) ──────────


def table_growth(registry, slug: str, top: int = 10, limit: int = 30) -> dict:
    """Established row count per run for the latest run's largest tables.

    One series per table; `None` at a run where that table's count was not
    established (or the table did not exist), a gap, never a zero.
    """
    runs = valid_runs(registry, slug, limit=limit)
    env = _envelope(registry, slug, runs[0] if runs else None)
    env.update({"runs": [], "series": []})
    if not runs:
        return _not_run(env, "never_surveyed", "No survey has run for this database.")
    latest = runs[0]
    latest_rows = _table_rows(registry, slug, latest)
    t_state, t_note = section_state(registry, slug, latest, "tables", len(latest_rows))
    if not _is_measured(t_state):
        return _not_run(env, t_state, t_note)
    ranked = sorted(
        (t for t in latest_rows
         if t.get("row_count") is not None
         and (t.get("table_type") or "") not in _NO_ROW_COUNT_TYPES),
        key=lambda t: (t["row_count"], _qualified(t)), reverse=True)[: max(0, int(top))]
    wanted = {_qualified(t) for t in ranked}
    ordered = list(reversed(runs))
    env["runs"] = [run_ref(r) for r in ordered]
    by_run: dict[tuple, dict[str, Any]] = {}
    with registry._conn() as conn:
        rows = conn.execute(
            "SELECT surveyed_at, source, schema_name, table_name, row_count "
            "FROM database_tables WHERE database_slug = ?",
            (registry._normalize_slug(slug),),
        ).fetchall()
    for r in rows:
        r = dict(r)
        q = f"{r['schema_name'] or ''}.{r['table_name'] or ''}"
        if q in wanted:
            by_run.setdefault((r["surveyed_at"], r["source"]), {})[q] = r["row_count"]
    env["series"] = [
        {"table": q,
         "row_counts": [(by_run.get((r["surveyed_at"], r.get("source") or "local")) or {}).get(q)
                        for r in ordered]}
        for q in sorted(wanted)
    ]
    established_runs = sum(1 for s in env["series"] for v in s["row_counts"] if v is not None)
    if not env["series"] or established_runs == 0:
        env["reasons"] = ["row_counts_not_established"]
        env["state"] = STATE_NOT_MEASURED
        return env
    gaps = sum(1 for s in env["series"] for v in s["row_counts"] if v is None)
    caveats = []
    if gaps:
        caveats.append("row_counts_missing_in_some_runs")
        env["notes"].append(f"{gaps} (table, run) points have no established row count; gaps, not zeros.")
    env = _finish(registry, slug, env, True, caveats)
    env["figure"] = {
        "data": [{"type": "scatter", "mode": "lines+markers", "name": s["table"],
                  "x": [r["surveyed_at"] for r in env["runs"]], "y": s["row_counts"],
                  "connectgaps": False} for s in env["series"]],
        "layout": {"title": {"text": "Rows per table over runs"}, "xaxis": {"type": "date"}},
    }
    return env


# ── statistics_currency (S4b: how current are the planner statistics) ───────


def _currency_for_run(registry, slug: str, run: dict) -> tuple[dict | None, int]:
    """(derived currency, activity-row count) for one run, from stored rows."""
    from resource_explorer.statistics_currency import derive_statistics_currency
    src = run.get("source") or None
    tables = _table_rows(registry, slug, run)
    activity = registry.query_detail_rows("database_table_activity", slug, run["surveyed_at"], src)
    schemas = [s.get("schema_name") or "" for s in
               registry.query_detail_rows("database_schemas", slug, run["surveyed_at"], src)]
    if not tables:
        return None, len(activity)
    return derive_statistics_currency(tables, activity, run["surveyed_at"], schemas), len(activity)


def statistics_currency(registry, slug: str, limit: int = 12) -> dict:
    """Statistics currency of the latest run, and its counts across earlier runs.

    Read-only, from `database_tables` / `database_table_activity` /
    `database_schemas` rows already stored. A run with no activity rows is a
    point with `measured: false`, never a count of zero.
    """
    runs = valid_runs(registry, slug, limit=limit)
    env = _envelope(registry, slug, runs[0] if runs else None)
    env.update({"currency": None, "history": []})
    if not runs:
        return _not_run(env, "never_surveyed", "No survey has run for this database.")
    cur, n_act = _currency_for_run(registry, slug, runs[0])
    if cur is None:
        tables = _table_rows(registry, slug, runs[0])
        t_state, t_note = section_state(registry, slug, runs[0], "tables", len(tables))
        return _not_run(env, t_state if not _is_measured(t_state) else "no_tables", t_note)
    if not n_act:
        out = _not_run(env, "no_table_activity",
                       "The latest survey stored no table-activity rows, so statistics "
                       "currency was not measured. This is not a finding that it is current.")
        # Why, where the stored rows show it: what this credential could read (the latest probe on
        # record, which may not be this run's; the scope says so).
        out["scope"] = credential_scope(registry, slug)
        return out
    env["currency"] = cur
    history = []
    for run in reversed(runs):                       # oldest first
        c, n = _currency_for_run(registry, slug, run)
        point = {"run": run_ref(run), "measured": bool(c and n)}
        if point["measured"]:
            d = c["database"]
            point.update({"tables": d["total"], "behind": d["behind"], "never": d["never"],
                          "stale": d["stale"], "reset_tables": d["reset_tables"],
                          "counters_reset": d["counters_reset"]})
        history.append(point)
    env["history"] = history
    env["notes"].append(
        f"Ages are measured at the survey time, not today. Stale means analyzed more than "
        f"{cur['stale_days']} days before the survey.")
    return _finish(registry, slug, env, True, [])
