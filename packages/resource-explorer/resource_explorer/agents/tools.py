"""BeeAI tool definitions — shared across all explorer agents."""

from __future__ import annotations

import re
from contextvars import ContextVar

from beeai_framework.tools import tool

from resource_explorer.name_resolution import ambiguity_message, resolve_database


def _absence(resource_slug: str, analysis_id: str, subject: str) -> str:
    """What to say when a table came back empty.

    "No dependencies found" asserts a fact about the repo. That is only true if
    the analysis actually ran; otherwise the truth is that nobody looked, and
    the two are opposite answers reaching the caller as the same sentence. An
    LLM handed the first will state it as a finding — which is the whole reason
    the fact layer exists.

    So this asks rather than guesses, and names the step that would settle it
    instead of a CLI command (the old text suggested `project-explorer refresh`,
    a binary that has not existed since the package was renamed).
    """
    try:
        from resource_explorer.facts import FactLayer

        fact = FactLayer().fact(resource_slug, analysis_id)
    except Exception:  # pragma: no cover - a tool must still answer
        return (
            f"No {subject} recorded for '{resource_slug}' — and whether the "
            f"analysis that produces them has run could not be determined."
        )

    if fact.is_known:
        return (
            f"No {subject} for '{resource_slug}'. This is a measured result: "
            f"the analysis ran and found none."
        )
    runnable = f" Run: {', '.join(fact.can_run)}." if fact.can_run else ""
    if fact.state == "never_run":
        return (
            f"Nothing is known about {subject} for '{resource_slug}' — the "
            f"analysis that produces them has never run here, so this is NOT "
            f"a finding that there are none.{runnable}"
        )
    return (
        f"Nothing is known about {subject} for '{resource_slug}' — "
        f"{fact.note or 'the result cannot be established from what was recorded.'}"
        f"{runnable}"
    )


def vector_search_raw(query: str, collection_names: str) -> str:
    from resource_explorer.vector_store_pg import MultiCollectionStore

    collections = [c.strip() for c in collection_names.split(",") if c.strip()]
    if not collections:
        return "Error: no collection names provided."
    results = MultiCollectionStore().search(query, collections)
    if not results:
        return "No relevant content found in the specified collections."
    parts = [f"[{r.collection} | score={r.score:.2f}]\n{r.text}" for r in results]
    return "\n\n---\n\n".join(parts)


@tool(
    description=(
        "Search indexed project content (code, docs, API specs) for text relevant to the query. "
        "collection_names is a comma-separated list of fully-qualified collection names, "
        "e.g. 'unitycatalog_python_code,unitycatalog_markdown_docs'. "
        "Call multiple times with different queries or collections to gather more context."
    )
)
def vector_search(query: str, collection_names: str) -> str:
    return vector_search_raw(query, collection_names)


def query_project_stats_raw(resource_slug: str, days: int = 90) -> str:
    from datetime import datetime, timedelta
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    slug = registry._normalize_slug(resource_slug)
    try:
        with registry._conn() as conn:
            row = conn.execute(
                "SELECT * FROM project_stats WHERE project_slug = ? ORDER BY fetched_at DESC LIMIT 1",
                (slug,),
            ).fetchone()
            now = datetime.utcnow()
            cutoff = (now - timedelta(days=days)).isoformat()
            cutoff_30 = (now - timedelta(days=30)).isoformat()
            cutoff_90 = (now - timedelta(days=90)).isoformat()
            live_n = conn.execute(
                "SELECT COUNT(*) FROM project_commits WHERE project_slug = ? AND committed_at >= ?",
                (slug, cutoff),
            ).fetchone()[0]
            live_30 = conn.execute(
                "SELECT COUNT(*) FROM project_commits WHERE project_slug = ? AND committed_at >= ?",
                (slug, cutoff_30),
            ).fetchone()[0]
            live_90 = conn.execute(
                "SELECT COUNT(*) FROM project_commits WHERE project_slug = ? AND committed_at >= ?",
                (slug, cutoff_90),
            ).fetchone()[0]
            live_total = conn.execute(
                "SELECT COUNT(*) FROM project_commits WHERE project_slug = ?", (slug,)
            ).fetchone()[0]
    except Exception as e:
        return f"Error reading stats: {e}"
    if not row:
        return _absence(slug, "repository_health", "statistics")
    d = dict(row)

    # Prefer live counts from project_commits; fall back to snapshot if no commit data
    have_live = bool(live_30 or live_90)
    commits_30d = live_30 if have_live else (d.get("commits_30d") or 0)
    commits_90d = live_90 if have_live else (d.get("commits_90d") or 0)

    def v(key, suffix=""):
        val = d.get(key)
        return "N/A" if val is None or val == "" else f"{val}{suffix}"

    loc = d.get("lines_of_code")
    if loc:
        loc = int(loc)
        loc_str = (
            f"{loc / 1_000_000:.1f}M (est.)" if loc >= 1_000_000 else f"{loc / 1_000:.1f}K (est.)"
        )
    else:
        loc_str = "N/A"

    kb = d.get("repo_size_kb")
    size_str = (
        f"{int(kb) / 1024:.1f} MB" if kb and int(kb) >= 1024 else (f"{kb} KB" if kb else "N/A")
    )

    # Prefer survey total (all file types) over code-only SQLite count
    try:
        with registry._conn() as _cs_conn:
            survey_total = _cs_conn.execute(
                """SELECT SUM(file_count) FROM project_file_type_counts
                   WHERE project_slug = ?
                     AND surveyed_at = (
                         SELECT MAX(surveyed_at) FROM project_file_type_counts WHERE project_slug = ?
                     )""",
                (slug, slug),
            ).fetchone()[0]
            if not survey_total:
                # Fall back to code-symbols count (Python/JS/Java/Go only)
                survey_total = _cs_conn.execute(
                    "SELECT COUNT(DISTINCT file_path) FROM project_code_symbols WHERE project_slug = ?",
                    (slug,),
                ).fetchone()[0]
                file_count_note = "code files only — run survey for full count"
            else:
                file_count_note = "surveyed"
    except Exception:
        survey_total = None
        file_count_note = ""

    file_count_str = (
        f"{survey_total} ({file_count_note})"
        if survey_total
        else (
            v("ingestion_file_count")
            if d.get("ingestion_file_count") is not None
            else v("file_count")
        )
    )

    lines = [
        f"Project: {slug}  (snapshot taken {v('fetched_at')[:19]}; commit counts below are counted today)",
        f"Stars: {v('stars')}  Forks: {v('forks')}  Watchers: {v('watchers')}  Open issues: {v('open_issues')}",
        f"Contributors: {v('contributors_count')}",
    ]
    if have_live:
        lines.append(
            f"Commits, counted {now:%Y-%m-%d} from {live_total} stored commits: "
            f"30d: {commits_30d}  90d: {commits_90d}"
            + (f"  {days}d: {live_n}" if days not in (30, 90) else "")
        )
    else:
        lines.append(
            f"Commits, from the {v('fetched_at')[:10]} snapshot (no stored commits to count): "
            f"30d: {commits_30d}  90d: {commits_90d}"
            + (f"  {days}d: N/A (run refresh)" if days not in (30, 90) else "")
        )
    lines += [
        f"Releases: {v('releases_count')}  Latest: {v('latest_release')} ({v('latest_release_at')[:10]})",
        f"Avg release interval: {v('avg_release_interval_days')} days",
        f"Primary language: {v('primary_language')}  LOC: {loc_str}  Files: {file_count_str}  Size: {size_str}",
        f"Languages: {v('language_breakdown')}",
        f"License: {v('license')}  Topics: {v('topics')}",
        f"Created: {v('repo_created_at')[:10]}  Last pushed: {v('last_pushed_at')[:10]}",
    ]
    return "\n".join(lines)


@tool(
    description=(
        "Get GitHub statistics for a project: stars, forks, watchers, open issues, contributors, "
        "commits, releases, lines of code, file count, primary language, language breakdown, "
        "license, topics, repo size, creation date, last pushed date. "
        "days controls the commit-count window (default 90; use 30 for 30-day counts, 180 for 6-month, etc.)."
    )
)
def query_project_stats(resource_slug: str, days: int = 90) -> str:
    return query_project_stats_raw(resource_slug, days)


def query_top_committers_raw(resource_slug: str, limit: int = 10, days: int = 90) -> str:
    from collections import Counter
    from datetime import datetime, timedelta
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    slug = registry._normalize_slug(resource_slug)
    # The two contributor lists are the source (REVIEW-QUESTIONS-AND-ANALYTICS-2026-10-22.md, addendum 1): the same reader the question page
    # uses, so the chat and the page name the same people. The per-window git-author count below is only the
    # fallback for a repository whose lists were never fetched, and says so.
    try:
        from resource_explorer import contributors as contrib

        lists = contrib.both(registry, slug, top=limit)
    except Exception:  # noqa: BLE001 - the fallback still answers
        lists = None
    if lists and contrib.any_fetched(lists):
        return "\n".join([f"Top contributors to {slug}:", *contrib.text_block(lists, limit=limit)])
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    try:
        with registry._conn() as conn:
            rows = conn.execute(
                "SELECT author_name, author_email FROM project_commits "
                "WHERE project_slug = ? AND committed_at >= ?",
                (slug, cutoff),
            ).fetchall()
    except Exception as e:
        return f"Error reading commits: {e}"
    if not rows:
        return _absence(slug, "repository_health", f"commit data for the last {days} days")
    counter: Counter = Counter()
    for row in rows:
        name, email = row["author_name"], row["author_email"]
        label = name or email or "unknown"
        counter[label] += 1
    top = counter.most_common(limit)
    lines = [f"Top {min(limit, len(top))} committers to {slug} (last {days} days, {len(rows)} stored commits; "
             f"no contributor fetch yet, so these are from git authors, {days}-day window):"]
    for i, (name, count) in enumerate(top, 1):
        lines.append(f"  {i}. {name}: {count} commit{'s' if count != 1 else ''}")
    return "\n".join(lines)


@tool(
    description=(
        "Get the top contributors to a project: two lists, the last 12 months (commits by git author) and all "
        "time (GitHub's contributions by linked account), each with shares of its own total. When neither list "
        "has been fetched, falls back to stored commits over the last `days` days (default 90) and says so."
    )
)
def query_top_committers(resource_slug: str, limit: int = 10, days: int = 90) -> str:
    return query_top_committers_raw(resource_slug, limit, days)


def _query_code_symbols_raw(
    resource_slug: str,
    kind: str = "all",
    pattern: str = "",
    file_path: str = "",
    limit: int = 50,
) -> str:
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    slug = registry._normalize_slug(resource_slug)
    try:
        filters = ["project_slug = ?"]
        params: list = [slug]
        if kind and kind != "all":
            filters.append("kind = ?")
            params.append(kind)
        if pattern:
            filters.append("name LIKE ?")
            params.append(f"%{pattern}%")
        if file_path:
            filters.append("file_path LIKE ?")
            params.append(f"{file_path}%")
        where = " AND ".join(filters)
        with registry._conn() as conn:
            rows = conn.execute(
                f"SELECT kind, qualified_name, signature, docstring, file_path, start_line "  # noqa: S608
                f"FROM project_code_symbols WHERE {where} "
                f"ORDER BY file_path, start_line LIMIT ?",
                params + [limit],
            ).fetchall()
            total = conn.execute(
                f"SELECT COUNT(*) FROM project_code_symbols WHERE {where}",  # noqa: S608
                params,
            ).fetchone()[0]
    except Exception as exc:
        return f"Error reading symbol table: {exc}"
    if not rows:
        msg = f"No {kind if kind != 'all' else ''} symbols found in '{slug}'"
        return (
            msg + (f" matching '{pattern}'" if pattern else "") + ". Has this project been indexed?"
        )
    label = kind if kind != "all" else "symbol"
    header = f"{total} {label}(s) in {slug}"
    if pattern:
        header += f" matching '{pattern}'"
    if total > limit:
        header += f" — showing first {limit}"
    lines = [header + ":"]
    for r in rows:
        sig = f"  {r['signature']}" if r["signature"] else ""
        doc = f"  # {r['docstring']}" if r["docstring"] else ""
        lines.append(f"\n[{r['kind']}] {r['qualified_name']}{sig}")
        lines.append(f"  {r['file_path']}:{r['start_line']}{doc}")
    return "\n".join(lines)


@tool(
    description=(
        "List or count classes, functions, methods, and interfaces defined in a project's source code. "
        "kind filters by symbol type: 'class', 'function', 'method', 'interface', 'enum', or 'all' (default). "
        "pattern filters by name substring (case-insensitive). "
        "file_path restricts to a specific file or directory prefix. "
        "Returns qualified name, signature, and docstring for each match."
    )
)
def query_code_symbols(
    resource_slug: str,
    kind: str = "all",
    pattern: str = "",
    file_path: str = "",
    limit: int = 50,
) -> str:
    return _query_code_symbols_raw(
        resource_slug, kind=kind, pattern=pattern, file_path=file_path, limit=limit
    )


@tool(
    description=(
        "Get the full signature, docstring, and purpose summary for a named class or method. "
        "name can be a simple name ('parse') or qualified name ('CodeParser.parse'). "
        "Returns file location, signature, and a one-sentence summary of its purpose."
    )
)
def get_symbol_detail(resource_slug: str, name: str) -> str:
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    slug = registry._normalize_slug(resource_slug)
    try:
        with registry._conn() as conn:
            row = conn.execute(
                "SELECT * FROM project_code_symbols "
                "WHERE project_slug = ? AND (qualified_name = ? OR name = ?) "
                "ORDER BY CASE kind WHEN 'class' THEN 0 WHEN 'method' THEN 1 ELSE 2 END "
                "LIMIT 1",
                (slug, name, name),
            ).fetchone()
    except Exception as exc:
        return f"Error: {exc}"
    if not row:
        return (
            f"Symbol '{name}' not found in '{slug}'. "
            f"Use query_code_symbols to browse available symbols."
        )
    r = dict(row)
    summary = r["summary"] or r["docstring"] or _generate_summary(slug, r, registry)
    lines = [f"[{r['kind']}] {r['qualified_name']}"]
    lines.append(f"File: {r['file_path']}  lines {r['start_line']}–{r['end_line']}")
    if r["signature"]:
        lines.append(f"Signature: {r['signature']}")
    if summary:
        lines.append(f"Purpose: {summary}")
    return "\n".join(lines)


def _generate_summary(resource_slug: str, symbol: dict, registry) -> str:
    """Search for the symbol's source in pgvector and ask the LLM for a one-sentence summary."""
    try:
        from resource_explorer.vector_store_pg import MultiCollectionStore
        from resource_explorer.collection_router import CollectionRouter
        from resource_explorer.llm_client import get_llm

        query = symbol["qualified_name"]
        collections = CollectionRouter().select(query, resource_slug)
        code_collections = [c for c in collections if "code" in c]
        if not code_collections:
            return ""
        results = MultiCollectionStore().search(query, code_collections)
        if not results:
            return ""
        snippet = results[0].text[:600]
        prompt = f"Summarize what this {symbol['kind']} does in one sentence:\n\n{snippet}"
        summary = get_llm().complete(prompt).strip().split("\n")[0]
        # Persist so the next call is instant
        with registry._conn() as conn:
            conn.execute(
                "UPDATE project_code_symbols SET summary = ? "
                "WHERE project_slug = ? AND file_path = ? AND qualified_name = ?",
                (summary, resource_slug, symbol["file_path"], symbol["qualified_name"]),
            )
        return summary
    except Exception:
        return ""


@tool(
    description=(
        "Get weekly commit activity trends for a project. "
        "weeks controls how many weeks of history to show (default 12). "
        "Shows commit counts per week with a simple bar chart, plus lines added/deleted when available."
    )
)
def query_commit_activity(resource_slug: str, weeks: int = 12) -> str:
    from collections import defaultdict
    from datetime import datetime, timedelta
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    slug = registry._normalize_slug(resource_slug)
    weeks = max(1, min(weeks, 52))
    try:
        with registry._conn() as conn:
            rows = conn.execute(
                "SELECT committed_at, additions, deletions FROM project_commits "
                "WHERE project_slug = ? ORDER BY committed_at DESC",
                (slug,),
            ).fetchall()
    except Exception as e:
        return f"Error reading commits: {e}"
    if not rows:
        return _absence(slug, "repository_health", "commit data")
    now = datetime.utcnow()
    week_commits: defaultdict = defaultdict(int)
    week_adds: defaultdict = defaultdict(int)
    week_dels: defaultdict = defaultdict(int)
    has_churn = False
    for row in rows:
        ts, adds, dels = row[0], row[1], row[2]
        try:
            dt = datetime.fromisoformat(ts[:19])
            w = (now - dt).days // 7
            if w < weeks:
                week_commits[w] += 1
                if adds is not None:
                    week_adds[w] += adds
                    week_dels[w] += dels or 0
                    has_churn = True
        except Exception:
            pass
    lines = [f"Weekly commit activity for {slug} (most recent first):"]
    for week in range(weeks):
        count = week_commits.get(week, 0)
        label = "this week" if week == 0 else f"{week}w ago"
        bar = "█" * min(count, 30)
        line = f"  {label:>10}: {bar} {count}"
        if has_churn:
            a, d = week_adds.get(week, 0), week_dels.get(week, 0)
            line += f"  (+{a:,} / -{d:,})"
        lines.append(line)
    if has_churn:
        total_adds = sum(week_adds.values())
        total_dels = sum(week_dels.values())
        lines.append(
            f"\nTotal over {weeks} weeks: +{total_adds:,} additions / -{total_dels:,} deletions"
        )
    return "\n".join(lines)


@tool(
    description=(
        "Get a detailed contribution profile for a specific author: commit count, lines added/deleted, "
        "activity tier (core/regular/occasional relative to the project team), and comparison to project median. "
        "author can be a name or email substring — fuzzy matched against stored commits. "
        "days controls the lookback window (default 90)."
    )
)
def query_contributor_profile(resource_slug: str, author: str, days: int = 90) -> str:
    from datetime import datetime, timedelta
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    slug = registry._normalize_slug(resource_slug)
    days = max(1, min(days, 365))
    try:
        now = datetime.utcnow()
        cutoff = (now - timedelta(days=days)).isoformat()
        with registry._conn() as conn:
            # Find matching contributors by name or email substring
            author_lower = author.lower()
            all_authors = conn.execute(
                "SELECT DISTINCT author_name, author_email FROM project_commits WHERE project_slug = ?",
                (slug,),
            ).fetchall()
            matches = [
                (r["author_name"], r["author_email"])
                for r in all_authors
                if author_lower in (r["author_name"] or "").lower()
                or author_lower in (r["author_email"] or "").lower()
            ]
            if not matches:
                return (
                    f"No contributor matching '{author}' found in '{slug}'. "
                    f"Use query_top_committers to see available names."
                )

            name, email = matches[0]
            row = conn.execute(
                """SELECT COUNT(*) AS commits,
                          COALESCE(SUM(additions), 0) AS additions,
                          COALESCE(SUM(deletions), 0) AS deletions
                   FROM project_commits
                   WHERE project_slug = ? AND committed_at >= ?
                   AND (author_name = ? OR author_email = ?)""",
                (slug, cutoff, name, email),
            ).fetchone()

            # Project-wide average commits per contributor in this window
            median_row = conn.execute(
                """SELECT AVG(c) AS avg_commits FROM (
                       SELECT COUNT(*) AS c FROM project_commits
                       WHERE project_slug = ? AND committed_at >= ?
                       GROUP BY author_email
                   )""",
                (slug, cutoff),
            ).fetchone()

            # Tier from contributor_stats, over the WIDEST window available.
            #
            # project_contributor_stats' "periods" are nested trailing windows —
            # 30d, 90d and 365d, all ending today — not a sequence. So the latest
            # period_start is the NARROWEST window, and ordering DESC silently
            # answered "tier over the last 30 days" to a question about the
            # contributor. Measured 2026-08-27 across the catalogue: 70 of 347
            # contributors got a different tier that way, every sampled case a
            # core maintainer downgraded to "regular" (Apache Polaris, sqlglot),
            # and a further 271 were absent from the 30-day slice altogether — so
            # the caller was told "unknown (run refresh to compute tiers)" about a
            # tier that had in fact been computed.
            #
            # Earliest period_start = widest window = the tier over the longest
            # history held. ASC, deliberately.
            tier_row = conn.execute(
                """SELECT tier FROM project_contributor_stats
                   WHERE project_slug = ? AND author_email = ?
                   ORDER BY period_start ASC LIMIT 1""",
                (slug, email or ""),
            ).fetchone()

        commits = row["commits"]
        additions = row["additions"]
        deletions = row["deletions"]
        avg_commits = round(median_row["avg_commits"] or 0, 1)
        rel_pct = round((commits - avg_commits) / max(avg_commits, 1) * 100)
        tier = tier_row["tier"] if tier_row else "unknown (run refresh to compute tiers)"

        lines = [f"Contributor: {name or email} in {slug} (last {days} days)"]
        lines.append(f"Commits: {commits}  (project avg: {avg_commits}; {rel_pct:+d}% relative)")
        if additions or deletions:
            net = additions - deletions
            lines.append(f"Lines added: {additions:,}  Lines deleted: {deletions:,}  Net: {net:+,}")
        else:
            lines.append("Lines added/deleted: not yet fetched (run refresh to populate)")
        lines.append(f"Activity tier: {tier}")
        if len(matches) > 1:
            also = ", ".join(n or e for n, e in matches[1:4])
            lines.append(f"Other matches: {also}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Error: {exc}"


def _build_example_context_raw(resource_slug: str, topic: str) -> str:
    from resource_explorer.vector_store_pg import MultiCollectionStore
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    slug = registry._normalize_slug(resource_slug)

    collection_types = ["examples", "python_code", "api_reference", "markdown_docs"]
    candidate_collections = [f"{slug}_{ctype}" for ctype in collection_types]

    store = MultiCollectionStore()
    existing = [c for c in candidate_collections if store.collection_exists(c)]
    if not existing:
        return (
            f"No indexed collections found for '{slug}' — this repo's content has "
            f"not been embedded, so nothing can be retrieved from it. That is not a "
            f"statement about what the repo contains. Run: resource-explorer add/refresh {slug}"
        )

    # Targeted searches: topic itself, import/setup patterns, usage examples
    pkg = slug.replace("_", " ")
    searches = [
        (topic, existing, 3),
        (f"from {slug} import", existing, 2),
        (f"import {slug} setup {pkg}", existing, 2),
        (
            "usage example how to",
            [c for c in existing if any(t in c for t in ("examples", "markdown"))],
            2,
        ),
    ]

    seen: set[str] = set()
    sections: list[str] = []
    for query_text, cols, top_k in searches:
        if not cols:
            continue
        for r in store.search(query_text, cols, top_k=top_k):
            if r.text not in seen and r.score >= 0.3:
                seen.add(r.text)
                sections.append(f"[{r.collection} | score={r.score:.2f}]\n{r.text}")

    if not sections:
        return (
            f"No relevant context found for '{topic}' in '{slug}'. "
            "The project may need re-indexing, or try a broader topic."
        )

    # Build hints from the retrieved text so the LLM doesn't have to infer
    # imports or constructor signatures — both are frequently in a different
    # chunk than the usage code and score too low to be retrieved on their own.
    import re as _re

    combined = "\n".join(sections)

    # Import hint: collect capitalised names used as constructors
    class_names = _re.findall(r"\b([A-Z][A-Za-z0-9]+)\s*\(", combined)
    known_classes = sorted(
        {n for n in class_names if n not in {"True", "False", "None"} and not n[0].isdigit()}
    )
    import_hint = ""
    if known_classes:
        import_hint = (
            f"IMPORT HINT: These classes come from the '{slug}' package.\n"
            f"  from {slug} import {', '.join(known_classes[:6])}\n\n"
        )

    # Constructor hint: extract the first concrete multi-arg instantiation for
    # each class so the LLM sees real parameter names, not self.xxx placeholders.
    constructor_hints: list[str] = []
    for cls in known_classes[:6]:
        # Match ClassName(\n    arg, kw=val, ...\n) — allow multi-line
        pattern = cls + r"\s*\(\s*\n?((?:[^\(\)]*\n?){1,8})\)"
        for m in _re.finditer(pattern, combined):
            args = m.group(1).strip()
            # Skip trivial no-arg or single self calls
            if args and "self." not in args and len(args) > 4:
                one_line = " ".join(args.split())
                constructor_hints.append(f"  {cls}({one_line})")
                break

    constructor_hint = ""
    if constructor_hints:
        constructor_hint = (
            "CONSTRUCTOR PATTERNS (use these exact signatures):\n"
            + "\n".join(constructor_hints)
            + "\n\n"
        )

    header = f"Context for: '{topic}' in {slug}\n{'=' * 60}\n\n" f"{import_hint}{constructor_hint}"
    return header + "\n\n---\n\n".join(sections)


@tool(
    description=(
        "Gather context for generating a Python code example about a specific topic. "
        "Searches the examples, python_code, api_reference, and markdown_docs collections "
        "and returns relevant snippets ready for code generation. "
        "topic should be a concrete task: e.g. 'connect to server', 'list metadata assets', "
        "'create a glossary term', 'authenticate with token'. "
        "Call multiple times with different topics to gather broader context."
    )
)
def build_example_context(resource_slug: str, topic: str) -> str:
    return _build_example_context_raw(resource_slug, topic)


@tool(
    description=(
        "Query the dependency graph for a project. Use for 'what does X depend on?', 'what packages does X use?'. "
        "Returns a markdown table of dependencies. "
        "dep_type can be 'runtime', 'dev', 'optional', 'indirect', 'test', or 'all' (default). "
        "Pass a comma-separated list of project_slugs to find shared dependencies across projects."
    )
)
def query_dependencies(resource_slug: str, dep_type: str = "all") -> str:
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    slugs = [s.strip() for s in resource_slug.split(",") if s.strip()]
    if not slugs:
        return "No project slug provided."

    if len(slugs) == 1:
        slug = slugs[0]
        deps = registry.query_dependencies(slug, dep_type=dep_type if dep_type != "all" else None)
        if not deps:
            return _absence(slug, "dependency_analysis", "dependencies")
        lines = [
            f"Dependencies for **{slug}** (filter: {dep_type}):",
            "",
            # "Version From" is provenance for the Version column — "declared"
            # means the manifest wrote that version at this dependency's own
            # declaration site; "variable_interpolation"/"version_catalog" mean
            # it was resolved from elsewhere (a Gradle `ext` variable or a
            # version catalog) and is not what the coordinate literally said.
            "| Package | Version | Version From | Type | Ecosystem | Source |",
            "|---------|---------|--------------|------|-----------|--------|",
        ]
        for d in deps:
            lines.append(
                f"| {d['dep_name']} | {d['dep_version'] or '—'} | "
                f"{d.get('dep_version_source') or '—'} | "
                f"{d['dep_type']} | {d['ecosystem']} | {d['source_file']} |"
            )
        return "\n".join(lines)

    shared = registry.query_shared_dependencies(slugs)
    if not shared:
        return f"No shared dependencies found across: {', '.join(slugs)}"
    lines = [
        f"Shared dependencies across {', '.join(slugs)}:",
        "",
        "| Package | Ecosystem | Projects |",
        "|---------|-----------|---------|",
    ]
    for d in shared:
        lines.append(f"| {d['dep_name']} | {d['ecosystem']} | {d['projects']} |")
    return "\n".join(lines)


def query_databases_raw(database_slug: str = "") -> str:
    import json
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    if database_slug:
        normalized, ambiguous = resolve_database(registry, database_slug)
        if ambiguous:
            return ambiguity_message(database_slug, ambiguous)
        db_entity = registry.get_database(normalized, allow_unreadable=True) if normalized else None
        if not db_entity:
            return f"Database '{database_slug}' not found in registry."

        lines = [
            f"Database: {db_entity.slug}",
            f"  Display Name: {db_entity.display_name}",
            f"  Type: {db_entity.db_type}",
            f"  Host: {db_entity.host}:{db_entity.port}",
            f"  Database Name: {db_entity.database_name}",
            f"  Status: {db_entity.status.value}",
            f"  Governance State: {db_entity.governance_state}",
            f"  Registered At: {db_entity.registered_at}",
            f"  Last Surveyed At: {db_entity.last_surveyed_at or 'Never'}",
        ]

        latest_survey = registry.get_latest_database_survey(normalized)
        if latest_survey:
            lines.append(f"  Latest survey record: {latest_survey.get('surveyed_at') or 'unknown'}")
            lines.append(f"  Schema Count: {latest_survey.get('schema_count', 0)}")
            lines.append(f"  Table Count: {latest_survey.get('table_count', 0)}")
            lines.append(f"  Column Count: {latest_survey.get('column_count', 0)}")

            survey_data = latest_survey.get("survey_data")
            if isinstance(survey_data, str):
                try:
                    survey_data = json.loads(survey_data)
                except Exception:
                    pass

            if isinstance(survey_data, dict) and "schema_info" in survey_data:
                schemas = survey_data["schema_info"].get("schemas", [])
                if schemas:
                    lines.append("\nSchemas and Tables:")
                    for schema in schemas:
                        schema_name = schema.get("name", "")
                        tables = schema.get("tables", [])
                        table_names = [t.get("name", "") for t in tables]
                        lines.append(
                            f"  - Schema '{schema_name}': {', '.join(table_names) if table_names else '(no tables)'}"
                        )
        else:
            lines.append("  No survey data available. Run a survey to see schemas/tables.")

        return "\n".join(lines)
    else:
        databases = registry.list_databases()
        if not databases:
            return "No databases registered in the metadata catalog."
        lines = ["Registered Databases:"]
        for db in databases:
            lines.append(
                f"- slug: {db.slug} | Name: {db.display_name} | Type: {db.db_type} | "
                f"Host: {db.host}:{db.port} | Name: {db.database_name} | "
                f"Status: {db.status.value} | Governance: {db.governance_state}"
            )
        return "\n".join(lines)


@tool(
    description=(
        "List registered databases, or show detailed info about a specific database. "
        "If database_slug is provided, returns details of that database, its schemas, tables, and column counts. "
        "If database_slug is empty, lists all registered databases with their slugs, types, hosts, ports, and governance states. "
        "Use this for 'when was <database> last surveyed?': the detail shows its last-surveyed date and the date of its "
        "latest survey record. Repository tools (stats, committers, commits) do not apply to databases."
    )
)
def query_databases(database_slug: str = "") -> str:
    return query_databases_raw(database_slug)


def query_database_schema_raw(
    database_slug: str, schema_name: str = "", table_name: str = ""
) -> str:
    import json
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    if table_name and "." in table_name and not schema_name:
        schema_name, table_name = table_name.split(".", 1)
    normalized, ambiguous = resolve_database(registry, database_slug)
    if ambiguous:
        return ambiguity_message(database_slug, ambiguous)
    db_entity = registry.get_database(normalized, allow_unreadable=True) if normalized else None
    if not db_entity:
        return f"Database '{database_slug}' not found."

    latest_survey = registry.get_latest_database_survey(normalized)
    if not latest_survey:
        return f"No survey data available for database '{database_slug}'."

    survey_data = latest_survey.get("survey_data")
    if isinstance(survey_data, str):
        try:
            survey_data = json.loads(survey_data)
        except Exception:
            return f"Failed to parse survey data for database '{database_slug}'."

    if not isinstance(survey_data, dict) or "schema_info" not in survey_data:
        return f"No schema information found in survey data for database '{database_slug}'."

    schema_info = survey_data["schema_info"]
    schemas = schema_info.get("schemas", [])

    if table_name:
        target_table = None
        found_schema_name = None
        for sch in schemas:
            sch_name = sch.get("name", "")
            if schema_name and sch_name.lower() != schema_name.lower():
                continue
            for tbl in sch.get("tables", []):
                t_name = tbl.get("name", "")
                if t_name.lower() == table_name.lower():
                    target_table = tbl
                    found_schema_name = sch_name
                    break
            if target_table:
                break

        if not target_table:
            filter_desc = f" in schema '{schema_name}'" if schema_name else ""
            return f"Table '{table_name}' not found{filter_desc} in database '{database_slug}'."

        columns = target_table.get("columns", [])
        lines = [
            f"Table: {found_schema_name}.{target_table.get('name')}",
            f"Type: {target_table.get('type')}",
            f"Description: {target_table.get('description') or 'None'}",
            f"Estimated Row Count: {target_table.get('row_count', 'unknown')}",
            f"Size: {target_table.get('size_pretty', 'unknown')}",
            "",
            "Columns:",
            "| Position | Name | Type | Nullable | Key | Foreign Key Target | Description |",
            "|---|---|---|---|---|---|---|",
        ]
        for col in columns:
            pk = "PK" if col.get("is_primary_key") else ""
            fk = ""
            fk_data = col.get("foreign_key")
            if fk_data:
                fk = f"{fk_data.get('foreign_schema')}.{fk_data.get('foreign_table')}({fk_data.get('foreign_column')})"
            lines.append(
                f"| {col.get('position')} | {col.get('name')} | {col.get('type')} | "
                f"{'Yes' if col.get('nullable') else 'No'} | {pk} | {fk} | {col.get('description') or ''} |"
            )
        return "\n".join(lines)

    elif schema_name:
        target_schema = None
        for sch in schemas:
            if sch.get("name", "").lower() == schema_name.lower():
                target_schema = sch
                break
        if not target_schema:
            return f"Schema '{schema_name}' not found in database '{database_slug}'."

        tables = target_schema.get("tables", [])
        lines = [
            f"Schema: {target_schema.get('name')}",
            f"Description: {target_schema.get('description') or 'None'}",
            "",
            "Tables:",
        ]
        for tbl in tables:
            lines.append(
                f"- {tbl.get('name')} | Type: {tbl.get('type')} | Columns: {len(tbl.get('columns', []))} | "
                f"Rows: {tbl.get('row_count', 'unknown')} | Size: {tbl.get('size_pretty', 'unknown')}"
            )
        return "\n".join(lines)

    else:
        lines = [
            f"Database Schema Summary for '{database_slug}':",
            f"Total Schemas: {len(schemas)}",
            f"Total Tables: {schema_info.get('total_tables', 0)}",
            f"Total Columns: {schema_info.get('total_columns', 0)}",
            "",
            "All Tables:",
            "| Schema | Table | Columns | Row Count | Size | Description |",
            "|---|---|---|---|---|---|",
        ]
        for sch in schemas:
            sch_name = sch.get("name", "")
            for tbl in sch.get("tables", []):
                lines.append(
                    f"| {sch_name} | {tbl.get('name')} | {len(tbl.get('columns', []))} | "
                    f"{tbl.get('row_count', 'unknown')} | {tbl.get('size_pretty', 'unknown')} | {tbl.get('description') or ''} |"
                )
        return "\n".join(lines)


@tool(
    description=(
        "Retrieve schema and structure details for a database, optionally filtering by schema_name and/or table_name. "
        "If table_name is provided, returns columns (with types, nullable, primary key, foreign key information) "
        "and row count/size statistics for that table. "
        "If only schema_name is provided, lists tables in that schema. "
        "If neither is provided, returns a list of all tables in the database with their row counts and sizes. "
        "To describe a table ('describe sales.salesorderheader'), pass table_name (a 'schema.table' name is "
        "accepted). database_slug may be the database's own name, e.g. 'adventureworks'."
    )
)
def query_database_schema(database_slug: str, schema_name: str = "", table_name: str = "") -> str:
    return query_database_schema_raw(database_slug, schema_name, table_name)


def query_filesystems_raw(filesystem_slug: str = "", file_pattern: str = "") -> str:
    import json
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    if filesystem_slug:
        normalized = registry._normalize_slug(filesystem_slug)
        fs_entity = registry.get_filesystem(normalized)
        if not fs_entity:
            return f"Filesystem '{filesystem_slug}' not found in registry."

        lines = [
            f"Filesystem: {fs_entity.slug}",
            f"  Display Name: {fs_entity.display_name}",
            f"  Local Mount Point: {fs_entity.local_mount_point}",
            f"  Canonical Mount Point: {fs_entity.canonical_mount_point or 'N/A'}",
            f"  Status: {fs_entity.status.value}",
            f"  Governance State: {fs_entity.governance_state}",
            f"  Registered At: {fs_entity.registered_at}",
            f"  Last Surveyed At: {fs_entity.last_surveyed_at or 'Never'}",
        ]

        latest_survey = registry.get_latest_filesystem_survey(normalized)
        if latest_survey:
            survey_data = latest_survey.get("survey_data")
            if isinstance(survey_data, str):
                try:
                    survey_data = json.loads(survey_data)
                except Exception:
                    pass

            if isinstance(survey_data, dict):
                lines.append(f"  Total Files: {survey_data.get('total_files', 0)}")
                lines.append(f"  Total Data Files: {survey_data.get('total_data_files', 0)}")
                lines.append(f"  Total Size: {survey_data.get('total_size', 'unknown')}")

                files = survey_data.get("files", [])
                if file_pattern:
                    pat = file_pattern.lower()
                    matching_files = [
                        f
                        for f in files
                        if pat in f.get("file_name", "").lower()
                        or pat in f.get("file_path", "").lower()
                    ]
                    lines.append(
                        f"\nFiles matching pattern '{file_pattern}' ({len(matching_files)} found):"
                    )
                    for f in matching_files[:30]:
                        lines.append(
                            f"  - {f.get('file_path')} | Size: {f.get('file_size')} | "
                            f"Format: {f.get('format')} | Modified: {f.get('modified_at', '')[:16]}"
                        )
                    if len(matching_files) > 30:
                        lines.append(f"  ... and {len(matching_files) - 30} more matching files.")
                else:
                    lines.append("\nSample Files:")
                    for f in files[:15]:
                        lines.append(
                            f"  - {f.get('file_path')} | Size: {f.get('file_size')} | "
                            f"Format: {f.get('format')} | Modified: {f.get('modified_at', '')[:16]}"
                        )
                    if len(files) > 15:
                        lines.append(
                            f"  ... and {len(files) - 15} more files. Search with file_pattern argument."
                        )
        else:
            lines.append("  No survey data available. Run a survey to catalog files.")

        return "\n".join(lines)
    else:
        filesystems = registry.list_filesystems()
        if not filesystems:
            return "No filesystems registered in the metadata catalog."
        lines = ["Registered Filesystems:"]
        for fs in filesystems:
            lines.append(
                f"- slug: {fs.slug} | Name: {fs.display_name} | Mount Point: {fs.local_mount_point} | "
                f"Status: {fs.status.value} | Governance: {fs.governance_state}"
            )
        return "\n".join(lines)


@tool(
    description=(
        "List registered filesystems, or search/view files within a specific filesystem. "
        "If filesystem_slug is provided, returns details of that filesystem and its latest survey (total files, size, etc.). "
        "If file_pattern is also provided, filters files in the latest survey matching that substring pattern (case-insensitive). "
        "If filesystem_slug is empty, lists all registered filesystems with their slugs, local mount points, and governance states."
    )
)
def query_filesystems(filesystem_slug: str = "", file_pattern: str = "") -> str:
    return query_filesystems_raw(filesystem_slug, file_pattern)


def compare_database_schemas_raw(
    database_slug_a: str,
    database_slug_b: str,
    schema_name_a: str = "",
    schema_name_b: str = "",
) -> str:
    import json
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    slug_a, ambiguous_a = resolve_database(registry, database_slug_a)
    slug_b, ambiguous_b = resolve_database(registry, database_slug_b)
    if ambiguous_a:
        return ambiguity_message(database_slug_a, ambiguous_a)
    if ambiguous_b:
        return ambiguity_message(database_slug_b, ambiguous_b)

    db_a = registry.get_database(slug_a, allow_unreadable=True) if slug_a else None
    if not db_a:
        return f"Database '{database_slug_a}' not found in registry."
    db_b = registry.get_database(slug_b, allow_unreadable=True) if slug_b else None
    if not db_b:
        return f"Database '{database_slug_b}' not found in registry."

    survey_a = registry.get_latest_database_survey(slug_a)
    survey_b = registry.get_latest_database_survey(slug_b)

    if not survey_a and not survey_b:
        return (
            f"No survey data available for either database '{database_slug_a}' or '{database_slug_b}'. "
            "Run surveys on both databases to enable schema comparison."
        )
    if not survey_a:
        return f"No survey data available for database '{database_slug_a}'. Run a survey to enable schema comparison."
    if not survey_b:
        return f"No survey data available for database '{database_slug_b}'. Run a survey to enable schema comparison."

    def _extract_schema_info(survey: dict, db_slug: str) -> tuple[dict | None, str | None]:
        survey_data = survey.get("survey_data")
        if isinstance(survey_data, str):
            try:
                survey_data = json.loads(survey_data)
            except Exception:
                return None, f"Failed to parse survey data for database '{db_slug}'."
        if not isinstance(survey_data, dict) or "schema_info" not in survey_data:
            return None, f"No schema information found in survey data for database '{db_slug}'."
        return survey_data["schema_info"], None

    info_a, err_a = _extract_schema_info(survey_a, database_slug_a)
    if err_a:
        return err_a
    info_b, err_b = _extract_schema_info(survey_b, database_slug_b)
    if err_b:
        return err_b

    assert info_a is not None and info_b is not None

    schemas_list_a = info_a.get("schemas", [])
    schemas_list_b = info_b.get("schemas", [])

    schemas_dict_a = {s.get("name", "").lower(): s for s in schemas_list_a if s.get("name")}
    schemas_dict_b = {s.get("name", "").lower(): s for s in schemas_list_b if s.get("name")}

    target_schema_a = schema_name_a.lower().strip() if schema_name_a else ""
    target_schema_b = (
        (schema_name_b or schema_name_a).lower().strip() if (schema_name_b or schema_name_a) else ""
    )

    if target_schema_a and target_schema_a not in schemas_dict_a:
        return f"Schema '{schema_name_a}' not found in database '{database_slug_a}'."
    if target_schema_b and target_schema_b not in schemas_dict_b:
        target_name = schema_name_b or schema_name_a
        return f"Schema '{target_name}' not found in database '{database_slug_b}'."

    lines = [
        f"Database Schema Comparison: {db_a.display_name or db_a.slug} vs {db_b.display_name or db_b.slug}",
        "",
        f"| Dimension | {db_a.display_name or db_a.slug} ({db_a.slug}) | {db_b.display_name or db_b.slug} ({db_b.slug}) |",
        "|---|---|---|",
        f"| Type | {db_a.db_type} | {db_b.db_type} |",
        f"| Host:Port | {db_a.host}:{db_a.port} | {db_b.host}:{db_b.port} |",
        f"| Database Name | {db_a.database_name} | {db_b.database_name} |",
        f"| Total Schemas | {len(schemas_list_a)} | {len(schemas_list_b)} |",
        f"| Total Tables | {info_a.get('total_tables', 0)} | {info_b.get('total_tables', 0)} |",
        f"| Total Columns | {info_a.get('total_columns', 0)} | {info_b.get('total_columns', 0)} |",
        "",
    ]

    if target_schema_a and target_schema_b:
        schema_pairs = [(target_schema_a, target_schema_b)]
    else:
        all_schema_keys = sorted(set(schemas_dict_a.keys()) | set(schemas_dict_b.keys()))
        schemas_only_a = [
            schemas_dict_a[k].get("name", k)
            for k in all_schema_keys
            if k in schemas_dict_a and k not in schemas_dict_b
        ]
        schemas_only_b = [
            schemas_dict_b[k].get("name", k)
            for k in all_schema_keys
            if k in schemas_dict_b and k not in schemas_dict_a
        ]

        if schemas_only_a or schemas_only_b:
            lines.append("Schema Differences:")
            if schemas_only_a:
                lines.append(f"- Schemas only in {db_a.slug}: {', '.join(schemas_only_a)}")
            if schemas_only_b:
                lines.append(f"- Schemas only in {db_b.slug}: {', '.join(schemas_only_b)}")
            lines.append("")

        common_keys = [k for k in all_schema_keys if k in schemas_dict_a and k in schemas_dict_b]
        schema_pairs = [(k, k) for k in common_keys]

    total_diff_tables = 0
    total_common_tables = 0
    total_identical_tables = 0

    for sch_k_a, sch_k_b in schema_pairs:
        sch_obj_a = schemas_dict_a[sch_k_a]
        sch_obj_b = schemas_dict_b[sch_k_b]
        sch_name_display_a = sch_obj_a.get("name", sch_k_a)
        sch_name_display_b = sch_obj_b.get("name", sch_k_b)

        header_label = (
            f"Schema '{sch_name_display_a}'"
            if sch_name_display_a == sch_name_display_b
            else f"Schema '{sch_name_display_a}' ({db_a.slug}) vs '{sch_name_display_b}' ({db_b.slug})"
        )
        lines.append(f"### {header_label}")

        tables_a = {
            t.get("name", "").lower(): t for t in sch_obj_a.get("tables", []) if t.get("name")
        }
        tables_b = {
            t.get("name", "").lower(): t for t in sch_obj_b.get("tables", []) if t.get("name")
        }

        all_table_names = sorted(set(tables_a.keys()) | set(tables_b.keys()))
        tbls_only_a = [
            tables_a[t].get("name", t)
            for t in all_table_names
            if t in tables_a and t not in tables_b
        ]
        tbls_only_b = [
            tables_b[t].get("name", t)
            for t in all_table_names
            if t in tables_b and t not in tables_a
        ]
        common_tbl_keys = [t for t in all_table_names if t in tables_a and t in tables_b]

        if tbls_only_a:
            lines.append(f"- Tables only in {db_a.slug}: {', '.join(tbls_only_a)}")
        if tbls_only_b:
            lines.append(f"- Tables only in {db_b.slug}: {', '.join(tbls_only_b)}")

        table_diff_details = []

        for t_k in common_tbl_keys:
            total_common_tables += 1
            t_a = tables_a[t_k]
            t_b = tables_b[t_k]
            t_name_display = t_a.get("name", t_k)

            cols_a = {c.get("name", "").lower(): c for c in t_a.get("columns", []) if c.get("name")}
            cols_b = {c.get("name", "").lower(): c for c in t_b.get("columns", []) if c.get("name")}

            all_col_keys = sorted(set(cols_a.keys()) | set(cols_b.keys()))
            cols_only_a = [
                cols_a[c].get("name", c) for c in all_col_keys if c in cols_a and c not in cols_b
            ]
            cols_only_b = [
                cols_b[c].get("name", c) for c in all_col_keys if c in cols_b and c not in cols_a
            ]
            common_cols = [c for c in all_col_keys if c in cols_a and c in cols_b]

            col_mismatches = []
            for c_k in common_cols:
                ca = cols_a[c_k]
                cb = cols_b[c_k]
                c_name_display = ca.get("name", c_k)
                diffs = []
                if ca.get("type", "").lower() != cb.get("type", "").lower():
                    diffs.append(f"Type: '{ca.get('type')}' vs '{cb.get('type')}'")
                if bool(ca.get("nullable")) != bool(cb.get("nullable")):
                    diffs.append(f"Nullable: {ca.get('nullable')} vs {cb.get('nullable')}")
                if bool(ca.get("is_primary_key")) != bool(cb.get("is_primary_key")):
                    diffs.append(f"PK: {ca.get('is_primary_key')} vs {cb.get('is_primary_key')}")

                if diffs:
                    col_mismatches.append(
                        (c_name_display, ca.get("type", ""), cb.get("type", ""), "; ".join(diffs))
                    )

            if cols_only_a or cols_only_b or col_mismatches:
                total_diff_tables += 1
                diff_sec = [f"#### Table '{t_name_display}' Differences:"]
                if cols_only_a:
                    diff_sec.append(f"- Columns only in {db_a.slug}: {', '.join(cols_only_a)}")
                if cols_only_b:
                    diff_sec.append(f"- Columns only in {db_b.slug}: {', '.join(cols_only_b)}")
                if col_mismatches:
                    diff_sec.append(
                        f"| Column | {db_a.slug} Type | {db_b.slug} Type | Differences |"
                    )
                    diff_sec.append("|---|---|---|---|")
                    for c_name, ta_type, tb_type, d_str in col_mismatches:
                        diff_sec.append(f"| {c_name} | {ta_type} | {tb_type} | {d_str} |")
                table_diff_details.append("\n".join(diff_sec))
            else:
                total_identical_tables += 1

        if table_diff_details:
            lines.append("")
            lines.extend(table_diff_details)
        elif not tbls_only_a and not tbls_only_b:
            lines.append(
                f"All {len(common_tbl_keys)} tables and column definitions in this schema are identical."
            )
        else:
            lines.append(
                f"Common tables ({len(common_tbl_keys)}) have identical column definitions."
            )
        lines.append("")

    return "\n".join(lines).strip()


@tool(
    description=(
        "Compare database schemas, tables, and column structures between two registered databases. "
        "Identifies common vs exclusive tables, column mismatches, type differences, primary keys, and nullability. "
        "Optionally filter to specific schema names (e.g. schema_name_a='public', schema_name_b='public')."
    )
)
def compare_database_schemas(
    database_slug_a: str,
    database_slug_b: str,
    schema_name_a: str = "",
    schema_name_b: str = "",
) -> str:
    return compare_database_schemas_raw(
        database_slug_a, database_slug_b, schema_name_a, schema_name_b
    )


def database_schema_changes_raw(database_slug: str) -> str:
    from resource_explorer.history_diff import database_changes
    from resource_explorer.registry import ProjectRegistry

    return database_changes(ProjectRegistry(), database_slug)


@tool(
    description=(
        "Show what changed in a registered database's schema between its two most recent surveys: "
        "tables added or removed, and per table the columns added or removed and any column whose type, "
        "nullability or primary-key status changed. Use for 'what changed in X since the last survey?'. "
        "Says so plainly when the database was never surveyed or only surveyed once."
    )
)
def database_schema_changes(database_slug: str) -> str:
    return database_schema_changes_raw(database_slug)


def dependency_changes_raw(resource_slug: str) -> str:
    from resource_explorer.history_diff import dependency_changes
    from resource_explorer.registry import ProjectRegistry

    return dependency_changes(ProjectRegistry(), resource_slug)


@tool(
    description=(
        "Show how a repository's dependencies drifted between its two most recent ingests: dependencies "
        "added, removed, and version changes. Use for 'has the dependency set of X changed?'. Says so "
        "plainly when dependencies were never recorded or recorded only once."
    )
)
def dependency_changes(resource_slug: str) -> str:
    return dependency_changes_raw(resource_slug)


def coverage_sweep_raw(analysis: str, resource_type: str = "", only_gaps: bool = False) -> str:
    from resource_explorer.coverage_sweep import coverage_sweep
    from resource_explorer.registry import ProjectRegistry

    return coverage_sweep(ProjectRegistry(), analysis, resource_type, only_gaps)


@tool(
    description=(
        "Sweep EVERY registered resource for one analysis and group them by what is known: has a result, "
        "ran and found nothing, not yet run, or cannot be established. Use for 'which repos have no license "
        "classification?', 'which databases have never been checked for X?'. The analysis word ('license', "
        "'security', 'cve') names an ANALYSIS, never a repository that happens to have that name. analysis is an analysis id "
        "(license_classification, security_scan, cve_scan, secret_scan, documentation_coverage, "
        "dependency_analysis, ci_quality, database_owner, schema_inventory, ...) or a plain word such as "
        "'license' or 'security'. resource_type is 'repo', 'database' or 'filesystem' (optional). "
        "only_gaps=true lists just the resources with nothing known. 'Not yet run' is not a pass."
    )
)
def coverage_sweep(analysis: str, resource_type: str = "", only_gaps: bool = False) -> str:
    return coverage_sweep_raw(analysis, resource_type, only_gaps)


def find_repos_by_dependency_raw(dependency: str, ecosystem: str = "", contains: bool = False) -> str:
    from resource_explorer.discovery import repos_using_dependency
    from resource_explorer.registry import ProjectRegistry

    return repos_using_dependency(ProjectRegistry(), dependency, ecosystem, contains)


@tool(
    description=(
        "Find which registered repositories use a dependency, by exact package name (case and -/_/. "
        "spelling ignored, e.g. 'sqlalchemy'), from each repo's latest recorded dependencies. Set "
        "contains=true to match any package whose name contains the text. ecosystem narrows it (e.g. 'pip'). "
        "Repositories with no recorded dependencies are listed as unknown, not as non-users."
    )
)
def find_repos_by_dependency(dependency: str, ecosystem: str = "", contains: bool = False) -> str:
    return find_repos_by_dependency_raw(dependency, ecosystem, contains)


def rank_databases_raw(metric: str = "tables", limit: int = 10) -> str:
    from resource_explorer.discovery import rank_databases
    from resource_explorer.registry import ProjectRegistry

    return rank_databases(ProjectRegistry(), metric, limit)


@tool(
    description=(
        "Rank registered databases by a figure from their latest measured survey. metric is 'tables', "
        "'columns' or 'undocumented_tables' (tables with no description; tables whose description the survey could not read "
        "are reported separately, not counted as undocumented). Databases with no measured survey are listed "
        "as not ranked. Use for 'which databases are largest?' or 'which have the most undocumented tables?'."
    )
)
def rank_databases(metric: str = "tables", limit: int = 10) -> str:
    return rank_databases_raw(metric, limit)


def list_rfas_raw(resource_slug: str = "", include_closed: bool = False) -> str:
    """Read-only view of the RFA drawer, with each RFA's workflow state and any dismissal."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.rfas import flatten_rfas

    registry = ProjectRegistry()
    slug = registry._normalize_slug(resource_slug) if resource_slug else None
    rfas = flatten_rfas(registry, entity_slug=slug)
    total = len(rfas)
    shown = [
        r for r in rfas
        if not r.get("dismissed") and (include_closed or r.get("rfa_status") != "completed")
    ]
    scope = f" for '{slug}'" if slug else ""
    # Completed RFAs the default view hides. Said in the result itself because a model that did not set
    # include_closed (both Qwens on q22, 2026-10-20) otherwise has no way to learn closed ones exist.
    hidden_closed = 0 if include_closed else sum(
        1 for r in rfas if not r.get("dismissed") and r.get("rfa_status") == "completed"
    )
    closed_hint = (
        f"{hidden_closed} completed (closed) RFA{'' if hidden_closed == 1 else 's'}{scope} "
        f"{'is' if hidden_closed == 1 else 'are'} hidden from this list: call list_rfas again with "
        f"include_closed=true to see {'it' if hidden_closed == 1 else 'them'}, and do that before "
        f"saying there is no closed or completed RFA."
    ) if hidden_closed else ""
    if not shown:
        return (
            f"No open RequestForAction entries{scope} ({total} in total, including closed or dismissed)."
            + (f" {closed_hint}" if closed_hint else "")
        )
    closed_note = "including completed ones" if include_closed else "not yet completed"
    by_status: dict[str, int] = {}
    for r in shown:
        by_status[r.get("rfa_status", "open")] = by_status.get(r.get("rfa_status", "open"), 0) + 1
    breakdown = ", ".join(f"{n} {status}" for status, n in sorted(by_status.items()))
    lines = [
        f"{len(shown)} RequestForAction entr{'y' if len(shown) == 1 else 'ies'}{scope} {closed_note} "
        f"({breakdown}; {total} in total). List every entry below, one line each, with its status word as shown:"
    ]
    for r in shown[:25]:
        who = f", assigned to {r['assignee']}" if r.get("assignee") else ""
        target = f"`{r['entity_slug']}`" if r.get("entity_slug") else "(no specific resource)"
        lines.append(
            f"- [{r.get('rfa_status', 'open')}{who}] {target} {r['summary']} "
            f"({r.get('analysis_name') or 'RFA'}, {r['ts'][:10]}, id {r['id']})"
        )
    if len(shown) > 25:
        lines.append(f"- ... and {len(shown) - 25} more")
    if closed_hint:
        lines.append(closed_hint)
    lines.append("To change an RFA's status, use the RFA drawer; the chat can only read them.")
    return "\n".join(lines)


@tool(
    description=(
        "List RequestForAction (RFA) entries from the RFA drawer, optionally for one resource: workflow "
        "status (open, deferred, reassigned), assignee, summary and date. Closed (completed) RFAs are hidden "
        "unless include_closed=true: set it whenever the question says closed, completed, resolved, all or "
        "history. Dismissed RFAs are always hidden. Read-only: changing an RFA is done in the drawer."
    )
)
def list_rfas(resource_slug: str = "", include_closed: bool = False) -> str:
    return list_rfas_raw(resource_slug, include_closed)


def query_cross_asset_links_raw(
    resource_slug: str = "", min_confidence: str = "high", limit: int = 15
) -> str:
    from resource_explorer.surveyors.cross_asset_matcher import query_cross_asset_links_report

    text, totals = query_cross_asset_links_report(
        resource_slug=resource_slug, min_confidence=min_confidence, limit=limit
    )
    facts = _turn_facts.get()
    if totals and facts is not None:
        facts.append(totals)
    return text


#: The question being answered, set by the conversation agent at the start of a turn. A tool argument the model
#: chose can be checked against it; a small model asked for "medium and low" links still passed "medium", and a
#: reworded description did not move it.
_current_question: ContextVar[str] = ContextVar("_current_question", default="")
_LOW_WORD = re.compile(r"\blow\b", re.IGNORECASE)


#: Facts a tool computed this turn that the answer should state outright. A list, not a value: BeeAI runs tools in
#: child tasks, whose own `set` would not reach the caller, but an append to this one list does.
_turn_facts: ContextVar[list | None] = ContextVar("_turn_facts", default=None)


def set_current_question(question: str) -> None:
    _current_question.set(question or "")
    _turn_facts.set([])


def turn_fact_lead() -> str:
    """The totals the links tool reported this turn (the last call's), or "". The 8B model read "first 15 of 20"
    as "15 high-confidence links" even with the cap labelled, so the sentence is stated by code ahead of its answer."""
    facts = _turn_facts.get()
    return facts[-1] if facts else ""


def link_confidence_floor(question: str, requested: str) -> str:
    """A question that says "low" asks for the low links, so the confidence floor is `low` whatever the model
    passed. A question that does not say it leaves the model's choice alone."""
    if _LOW_WORD.search(question or "") and (requested or "high").lower() in ("high", "medium"):
        return "low"
    return requested


@tool(
    description=(
        "Discover linkages between code repositories and cataloged databases. Use for 'which databases does X "
        "use?' (pass the repo as resource_slug) and 'which repos use database Y?'. A repo's model classes sharing "
        "names with a database's tables, plus matching database drivers. Pass resource_slug to filter to one "
        "repository or database, or leave empty for all. Lists only high-confidence links by default, best "
        "first (limit, default 15) and always reports how many medium and low ones exist; pass "
        "min_confidence='medium' (medium and high) or 'low' (every link) to see more: use 'low' when the "
        "question says medium and low, or all links. A link means shared names, not that the repo owns the "
        "database; generic table names (users, config, ...) and names found in many databases do not count "
        "toward high confidence."
    )
)
def query_cross_asset_links(
    resource_slug: str = "", min_confidence: str = "high", limit: int = 15
) -> str:
    min_confidence = link_confidence_floor(_current_question.get(), min_confidence)
    return query_cross_asset_links_raw(resource_slug=resource_slug, min_confidence=min_confidence, limit=limit)


def delegate_to_compare_agent_raw(query: str, resource_slug: str = "") -> str:
    from resource_explorer.agents.compare_agent import CompareAgent
    agent = CompareAgent()
    return agent.handle(query, resource_slug=resource_slug or None)


@tool(
    description=(
        "Delegate a comparative question comparing two or more resources (repos, databases, filesystems) "
        "to the specialized CompareAgent. Returns statistical diffs, schema comparisons, or Plotly charts."
    )
)
def delegate_to_compare_agent(query: str, resource_slug: str = "") -> str:
    return delegate_to_compare_agent_raw(query, resource_slug=resource_slug)


def delegate_to_integration_agent_raw(query: str, resource_slug: str = "") -> str:
    from resource_explorer.agents.integration_agent import IntegrationAgent
    agent = IntegrationAgent()
    return agent.handle(query, resource_slug=resource_slug or None)


@tool(
    description=(
        "Delegate an integration or ecosystem question ('how do X and Y work together?', 'can I use X with Y?') "
        "to the specialized IntegrationAgent. Returns technical connection points, shared contributors, and guidelines."
    )
)
def delegate_to_integration_agent(query: str, resource_slug: str = "") -> str:
    return delegate_to_integration_agent_raw(query, resource_slug=resource_slug)


