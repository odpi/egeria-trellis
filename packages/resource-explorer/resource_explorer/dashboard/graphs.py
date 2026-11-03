"""Graph builders — Plotext for terminal output, Plotly for web/export.

Every query here goes through ProjectRegistry._conn(), NOT sqlite3.connect().
Until 2026-08-23 all six did the latter, against `registry.db_path` — a bare
file path that is only meaningful when the registry is configured for SQLite.
The registry has defaulted to Postgres for some time, so db_path pointed at
`data/registry.db`: a file that held two stale rows from 2026-08-09 and, once
that leftover was removed, nothing at all. sqlite3.connect() creates a missing
file rather than failing, so every chart read an empty database and rendered an
empty chart — and each function's `except Exception: return []` meant no error
reached anyone either.

The visible symptom was "star growth over time shows no data points when there
are clearly stars". The stars were always there: project_stats in Postgres held
14 snapshots for sqlglot, 8 for egeria_git. The chart was reading somewhere
else entirely.
"""
from __future__ import annotations

import json

from resource_explorer.registry import ProjectRegistry


# ── shared data helpers ───────────────────────────────────────────────────────

def _load_history(resource_slug: str, limit: int = 12) -> list[dict]:
    """Return up to `limit` project_stats rows ordered oldest → newest."""
    registry = ProjectRegistry()
    try:
        with registry._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM project_stats
                   WHERE project_slug = ?
                   ORDER BY fetched_at ASC
                   LIMIT ?""",
                (resource_slug, limit),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _latest_row(resource_slug: str) -> dict:
    registry = ProjectRegistry()
    try:
        with registry._conn() as conn:
            row = conn.execute(
                "SELECT * FROM project_stats WHERE project_slug = ? ORDER BY fetched_at DESC LIMIT 1",
                (resource_slug,),
            ).fetchone()
        return dict(row) if row else {}
    except Exception:
        return {}


# ── terminal charts (Plotext) ─────────────────────────────────────────────────

def commits_over_time_terminal(resource_slug: str) -> None:
    """Print a commit-frequency bar chart to the terminal using Plotext."""
    import plotext as plt

    rows = _load_history(resource_slug)
    if not rows:
        print(f"No stats data for '{resource_slug}'. Run 'project-explorer refresh' first.")
        return

    dates = [r["fetched_at"][:10] for r in rows]
    counts = [r.get("commits_30d") or 0 for r in rows]

    plt.clf()
    plt.title(f"Commits (30-day window) — {resource_slug}")
    plt.xlabel("Snapshot date")
    plt.ylabel("Commits")
    plt.bar(dates, counts)
    plt.show()


def stars_over_time_terminal(resource_slug: str) -> None:
    """Print a star-growth line chart to the terminal using Plotext."""
    import plotext as plt

    rows = _load_history(resource_slug)
    if not rows:
        print(f"No stats data for '{resource_slug}'. Run 'project-explorer refresh' first.")
        return

    dates = [r["fetched_at"][:10] for r in rows]
    stars = [r.get("stars") or 0 for r in rows]

    plt.clf()
    plt.title(f"Star growth — {resource_slug}")
    plt.xlabel("Snapshot date")
    plt.ylabel("Stars")
    plt.plot(dates, stars, marker="hd")
    plt.show()


# ── web charts (Plotly) ───────────────────────────────────────────────────────

def stars_over_time_plotly(resource_slug: str) -> "plotly.graph_objects.Figure":
    """Return a Plotly figure for star growth over time."""
    import plotly.graph_objects as go

    # `or 0` would turn a snapshot that never recorded a star count into a
    # measured zero — sqlglot's first row is stars=None, which plotted as a
    # drop to 0 followed by a jump to 9,491, i.e. a growth story that never
    # happened. A missing reading is dropped from the series instead.
    rows = [r for r in _load_history(resource_slug) if r.get("stars") is not None]
    dates = [r["fetched_at"][:10] for r in rows]
    stars = [r["stars"] for r in rows]

    fig = go.Figure(go.Scatter(x=dates, y=stars, mode="lines+markers", name="Stars"))
    fig.update_layout(
        title=f"Stars over time — {resource_slug}",
        xaxis_title="Date",
        yaxis_title="Stars",
        yaxis_rangemode="tozero",
    )
    return fig


def commits_over_time_plotly(resource_slug: str) -> "plotly.graph_objects.Figure":
    """Return a Plotly figure for commit frequency over time (snapshot history)."""
    import plotly.graph_objects as go

    rows = _load_history(resource_slug)
    dates = [r["fetched_at"][:10] for r in rows]
    counts = [r.get("commits_30d") or 0 for r in rows]

    fig = go.Figure(go.Bar(x=dates, y=counts, name="Commits (30d)"))
    fig.update_layout(
        title=f"Commit activity — {resource_slug}",
        xaxis_title="Snapshot date",
        yaxis_title="Commits (30-day window)",
        yaxis_rangemode="tozero",
    )
    return fig


def weekly_commits_plotly(resource_slug: str) -> "plotly.graph_objects.Figure":
    """Return a Plotly bar chart of weekly commit counts from project_commits table."""
    import plotly.graph_objects as go
    from collections import defaultdict
    from datetime import datetime, timedelta, timezone

    registry = ProjectRegistry()
    try:
        with registry._conn() as conn:
            rows = conn.execute(
                "SELECT committed_at FROM project_commits "
                "WHERE project_slug = ? ORDER BY committed_at DESC",
                (resource_slug,),
            ).fetchall()
    except Exception:
        rows = []

    now = datetime.utcnow()  # naive UTC — matches stored committed_at format
    week_counts: defaultdict = defaultdict(int)
    # r["committed_at"], not tuple unpacking: these rows used to come from a
    # bare sqlite3 connection with no row_factory (plain tuples); through
    # registry._conn() they are mapping rows, and `for (ts,) in rows` would
    # bind ts to the *column name*. The enclosing except Exception would then
    # have turned that into a silently empty chart.
    for r in rows:
        ts = r["committed_at"]
        try:
            dt = datetime.fromisoformat(ts[:19])  # strip tz suffix
            weeks_ago = (now - dt).days // 7
            if 0 <= weeks_ago < 13:
                week_counts[weeks_ago] += 1
        except Exception:
            pass

    # Build x-axis oldest→newest so chart reads left-to-right
    week_offsets = list(range(12, -1, -1))
    dates = [(now - timedelta(weeks=w)).strftime("%Y-%m-%d") for w in week_offsets]
    counts = [week_counts.get(w, 0) for w in week_offsets]

    fig = go.Figure(go.Bar(x=dates, y=counts, name="Commits", marker_color="#06b6d4"))
    fig.update_layout(
        title=f"Weekly commit activity — {resource_slug}",
        xaxis_title="Week starting",
        yaxis_title="Commits",
        yaxis_rangemode="tozero",
    )
    return fig


def language_breakdown_plotly(resource_slug: str) -> "plotly.graph_objects.Figure":
    """Return a Plotly pie chart of language breakdown."""
    import plotly.graph_objects as go

    row = _latest_row(resource_slug)
    labels: list[str] = []
    values: list[int] = []

    if row.get("language_breakdown"):
        try:
            raw = row["language_breakdown"]
            # StatsFetcher stores as "Python: 426,777 bytes; Shell: 19,452 bytes; ..."
            # Try that format first, then fall back to JSON / ast for legacy data.
            if ": " in raw and " bytes" in raw:
                breakdown: dict = {}
                for part in raw.split(";"):
                    part = part.strip()
                    if not part:
                        continue
                    lang, _, rest = part.partition(": ")
                    num_str = rest.replace(" bytes", "").replace(",", "").strip()
                    try:
                        breakdown[lang.strip()] = int(num_str)
                    except ValueError:
                        pass
            else:
                try:
                    breakdown = json.loads(raw)
                except json.JSONDecodeError:
                    import ast
                    breakdown = ast.literal_eval(raw)
            labels = list(breakdown.keys())
            values = list(breakdown.values())
        except Exception:
            pass

    total = sum(values)
    shares = [(100.0 * v / total) if total else 0.0 for v in values]
    # walk3: a tiny slice's label ("0.00249%") sat outside the pie, on the title. The pie prints a number only on a
    # slice big enough to hold it, inside and horizontal; the legend carries every slice with its percentage.
    fig = go.Figure(go.Pie(
        labels=[f"{name} {_pie_percent(p)}" for name, p in zip(labels, shares)],
        values=values,
        text=[_pie_percent(p) if p >= PIE_LABEL_MIN_PERCENT else "" for p in shares],
        textinfo="text",
        textposition="inside",
        insidetextorientation="horizontal",
    ))
    fig.update_layout(title=f"Language breakdown — {resource_slug}")
    return fig


PIE_LABEL_MIN_PERCENT = 3.0


def _pie_percent(p: float) -> str:
    """A share as text with at most 3 significant figures; under 0.01% it reads '<0.01%'."""
    if p <= 0:
        return "0%"
    if p < 0.01:
        return "<0.01%"
    return f"{p:.3g}%"


def top_committers_plotly(resource_slug: str, limit: int = 10) -> "plotly.graph_objects.Figure | None":
    """Return a Plotly horizontal bar chart of top committers, or None if no data."""
    import plotly.graph_objects as go
    from collections import Counter

    registry = ProjectRegistry()
    try:
        with registry._conn() as conn:
            rows = conn.execute(
                "SELECT author_name, author_email FROM project_commits WHERE project_slug = ?",
                (resource_slug,),
            ).fetchall()
    except Exception:
        rows = []

    if not rows:
        return None

    counter: Counter = Counter()
    # Same mapping-row change as weekly_commits_plotly above — `for name, email
    # in rows` bound both to column NAMES, so the chart plotted a single bar
    # labelled "author_name".
    for r in rows:
        name, email = r["author_name"], r["author_email"]
        label = name or email or "unknown"
        counter[label] += 1

    top = counter.most_common(limit)
    # Reverse so highest bar is at the top of a horizontal chart
    names = [t[0] for t in reversed(top)]
    counts = [t[1] for t in reversed(top)]

    fig = go.Figure(go.Bar(
        x=counts, y=names, orientation="h",
        marker_color="#10b981", text=counts, textposition="outside",
    ))
    fig.update_layout(
        title=f"Top committers — {resource_slug} (last 90 days)",
        xaxis_title="Commits",
        yaxis_title="",
        height=max(220, len(top) * 32 + 80),
        margin={"l": 160, "r": 40, "t": 40, "b": 40},
    )
    return fig


def compare_stats_plotly(project_slugs: list[str]) -> "plotly.graph_objects.Figure":
    """Return a grouped bar chart comparing key stats across multiple projects."""
    import plotly.graph_objects as go

    metrics = ["stars", "forks", "contributors_count", "commits_30d", "open_issues"]
    labels = ["Stars", "Forks", "Contributors", "Commits (30d)", "Open Issues"]
    colors = ["#06b6d4", "#10b981", "#f59e0b", "#8b5cf6", "#ef4444"]

    project_data: dict[str, dict] = {}
    registry = ProjectRegistry()
    try:
        with registry._conn() as conn:
            for slug in project_slugs:
                row = conn.execute(
                    "SELECT * FROM project_stats WHERE project_slug = ? ORDER BY fetched_at DESC LIMIT 1",
                    (slug,),
                ).fetchone()
                project_data[slug] = dict(row) if row else {}
    except Exception:
        pass

    fig = go.Figure()
    for i, (metric, label, color) in enumerate(zip(metrics, labels, colors)):
        values = [project_data.get(slug, {}).get(metric) or 0 for slug in project_slugs]
        fig.add_trace(go.Bar(
            name=label,
            x=project_slugs,
            y=values,
            marker_color=color,
        ))

    fig.update_layout(
        barmode="group",
        title=f"Project comparison — {' vs '.join(project_slugs)}",
        xaxis_title="Project",
        yaxis_title="Count",
    )
    return fig


def file_types_plotly(resource_slug: str) -> "plotly.graph_objects.Figure":
    """Return a Plotly horizontal bar chart of file counts by type.

    Prefers surveyor data (Egeria-enriched type labels) stored in
    project_file_type_counts.  Falls back to counting raw extensions
    from project_code_symbols when no survey has been run.
    """
    import plotly.graph_objects as go
    from collections import Counter

    registry = ProjectRegistry()
    subtitle = ""

    # ── prefer persisted surveyor data ───────────────────────────────────────
    surveyor_rows = registry.query_file_type_counts(resource_slug)
    hover_texts: list[str] = []
    if surveyor_rows:
        labels = [r["type_label"] for r in surveyor_rows]
        counts = [r["file_count"] for r in surveyor_rows]
        source = surveyor_rows[0].get("source", "extension")
        ts = (surveyor_rows[0].get("surveyed_at") or "")[:16].replace("T", " ")
        source_label = "Egeria-classified" if source == "egeria" else "by extension"
        subtitle = f"Surveyed {ts} UTC · {source_label}"
        # Build hover text: for "Other" include extension breakdown
        for r in surveyor_rows:
            if r["type_label"] == "Other" and r.get("details_json"):
                try:
                    breakdown = json.loads(r["details_json"])
                    lines = [f"{ext}: {n}" for ext, n in list(breakdown.items())[:10]]
                    hover_texts.append("Unrecognized types:<br>" + "<br>".join(lines))
                except Exception:
                    hover_texts.append(str(r["file_count"]))
            else:
                hover_texts.append(str(r["file_count"]))
        # Cap at 25, already sorted desc by registry
        labels, counts, hover_texts = labels[:25], counts[:25], hover_texts[:25]
        labels, counts, hover_texts = (
            list(reversed(labels)), list(reversed(counts)), list(reversed(hover_texts))
        )
    else:
        # ── fallback: raw extension count from code symbols ───────────────────
        subtitle = "by extension (run 'project-explorer survey' for richer labels)"
        try:
            with registry._conn() as conn:
                rows = conn.execute(
                    "SELECT DISTINCT file_path FROM project_code_symbols WHERE project_slug = ?",
                    (resource_slug,),
                ).fetchall()
        except Exception:
            rows = []

        counter: Counter = Counter()
        for row in rows:
            path = row["file_path"].replace("\\", "/")
            name = path.rsplit("/", 1)[-1]
            if name.startswith(".") and name.count(".") == 1:
                ext = name
            elif "." in name:
                ext = "." + name.rsplit(".", 1)[-1].lower()
            else:
                ext = "(no extension)"
            counter[ext] += 1

        if not counter:
            fig = go.Figure()
            fig.update_layout(title=f"No file data — {resource_slug}")
            return fig

        top = counter.most_common(25)
        labels = [t[0] for t in reversed(top)]
        counts = [t[1] for t in reversed(top)]
        hover_texts = [str(c) for c in counts]

    fig = go.Figure(go.Bar(
        x=counts,
        y=labels,
        orientation="h",
        marker_color="#8b5cf6",
        text=counts,
        textposition="outside",
        hovertext=hover_texts,
        hovertemplate="%{y}: %{hovertext}<extra></extra>",
    ))
    fig.update_layout(
        title=f"Files by type — {resource_slug}<br><sup>{subtitle}</sup>",
        xaxis_title="File count",
        margin={"l": 10, "r": 40, "t": 50, "b": 10},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": "#94a3b8"},
    )
    return fig


def health_radar_plotly(resource_slug: str) -> "plotly.graph_objects.Figure":
    """Return a Plotly radar chart of project health dimensions."""
    import plotly.graph_objects as go

    row = _latest_row(resource_slug)
    if not row:
        fig = go.Figure()
        fig.update_layout(title=f"No data — {resource_slug}")
        return fig

    commits_30d = row.get("commits_30d") or 0
    contributors = row.get("contributors_count") or 0
    stars = row.get("stars") or 0
    releases = row.get("releases_count") or 0
    open_issues = row.get("open_issues") or 0

    # Normalize to 0–10 scale (rough heuristics for OSS projects)
    activity = min(commits_30d / 3, 10)
    community = min(contributors / 2, 10)
    popularity = min(stars / 1000, 10)
    release_health = min(releases / 2, 10)
    issue_health = max(10 - open_issues / 20, 0)

    dimensions = ["Activity", "Community", "Popularity", "Releases", "Issue Health"]
    scores = [activity, community, popularity, release_health, issue_health]

    fig = go.Figure(go.Scatterpolar(
        r=scores + [scores[0]],
        theta=dimensions + [dimensions[0]],
        fill="toself",
        name=resource_slug,
    ))
    fig.update_layout(
        polar={"radialaxis": {"visible": True, "range": [0, 10]}},
        title=f"Project health — {resource_slug}",
    )
    return fig
