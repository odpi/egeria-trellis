"""Cross-asset static dependency matcher.

Deterministically links code repositories to registered databases by cross-referencing
extracted code symbols, ORM models, SQL migration patterns, and dependencies against
database schemas and table inventories stored in ProjectRegistry.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from resource_explorer.registry import _survey_schema_info

if TYPE_CHECKING:
    from resource_explorer.registry import ProjectRegistry

log = logging.getLogger(__name__)


#: Table names (singular) common enough that a class of the same name proves little. Compared against the
#: singular of the table name, so `users` and `user` are both caught. Extended after a run on a real registry
#: (73 repos, 20 databases) where these drove most of the false HIGH links.
_GENERIC_TABLE_NAMES = frozenset({
    "user", "role", "group", "session", "event", "task", "item", "log", "file", "type", "status",
    "state", "config", "setting", "data", "name", "order", "account", "message", "token", "job",
    "tag", "owner", "source", "person", "category", "product", "address", "entity", "customer",
    "configuration", "property", "attribute", "version", "metadata", "permission", "label",
})
#: A table name present in this many surveyed databases identifies none of them.
_COMMON_TABLE_DB_COUNT = 3
#: Distinctive (non-generic, non-common) table matches needed before a link may be HIGH.
_HIGH_MIN_DISTINCTIVE = 2
_MIN_NAME_LEN = 4
_TABLE_WEIGHT = 0.15
_GENERIC_TABLE_WEIGHT = 0.05

_DB_DRIVER_DEPS = {
    "postgresql": {"psycopg2", "psycopg2-binary", "psycopg", "psycopg-binary", "asyncpg", "org.postgresql:postgresql", "r2dbc-postgresql"},
    "mysql": {"mysql-connector-python", "mysqlclient", "pymysql", "mysql:mysql-connector-java"},
    "sqlite": {"sqlite3", "org.xerial:sqlite-jdbc"},
    "oracle": {"cx-oracle", "oracledb", "com.oracle.database.jdbc:ojdbc8"},
}
_ORM_DEPS = {
    "sqlalchemy", "sqlmodel", "django", "tortoise-orm", "peewee", "hibernate",
    "org.springframework.boot:spring-boot-starter-data-jpa",
    "flyway", "liquibase", "alembic",
}


def _squash(name: str) -> str:
    """Lowercase with separators removed, so table `order_items` meets class `OrderItem`."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("sses", "xes", "ches", "shes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _table_variants(table: str) -> set[str]:
    """Spellings a class for `table` plausibly has: the table itself and its singular, separators dropped."""
    squashed = _squash(table)
    return {v for v in (squashed, _singular(squashed)) if len(v) >= _MIN_NAME_LEN}


def find_cross_asset_links(
    registry: ProjectRegistry,
    repo_slug: str | None = None,
    database_slug: str | None = None,
) -> list[dict]:
    """Find deterministic linkages between code repositories and cataloged databases.

    Cross-references:
      - ORM model/entity class names with database table names (e.g. Order -> orders/order)
      - Database driver & ORM dependencies (psycopg2, sqlalchemy, spring-boot-starter-data-jpa)
      - SQL / database references found in symbols and schemas
    """
    databases = registry.list_databases()
    if not databases:
        return []

    # Map database slug -> set of table names (lowercased) + db properties
    db_tables_map: dict[str, dict] = {}
    for db in databases:
        # Read every database, even when one was asked for: how common a table name is across the
        # catalog decides how much a match on it is worth.
        survey = registry.get_latest_database_survey(db.slug)
        tables = set()
        schemas = set()
        info = _survey_schema_info(survey) if survey else {}
        for sch in info.get("schemas", []):
            sname = sch.get("name", "")
            if sname:
                schemas.add(sname.lower())
            for tbl in sch.get("tables", []):
                tname = tbl.get("name", "")
                if tname:
                    tables.add(tname.lower())
        db_tables_map[db.slug] = {
            "entity": db,
            "schemas": schemas,
            "tables": tables,
            "db_type": db.db_type,
            "db_name": (db.database_name or "").lower(),
        }

    table_db_count: dict[str, int] = {}
    for info_ in db_tables_map.values():
        for t in info_["tables"]:
            table_db_count[t] = table_db_count.get(t, 0) + 1
    wanted_db = registry._normalize_slug(database_slug) if database_slug else None

    repos = registry.list_all()
    if repo_slug:
        norm_repo = registry._normalize_slug(repo_slug)
        repos = [r for r in repos if r.slug == norm_repo]

    links: list[dict] = []

    for repo in repos:
        # 1. Inspect repository dependencies
        deps = registry.query_dependencies(repo.slug)
        dep_names = {d.get("dep_name", "").lower().replace("_", "-") for d in deps} if deps else set()
        has_orm = bool(dep_names & _ORM_DEPS)

        # 2. Class names. An ORM model or entity is a class; matching every symbol, as an earlier
        # version did, let any function called `orders` vouch for a table, and the 500-row LIMIT with
        # no ORDER BY made the answer depend on which rows the database returned first.
        class_names: set[str] = set()
        symbols_error = ""
        try:
            with registry._conn() as conn:
                rows = conn.execute(
                    "SELECT DISTINCT name FROM project_code_symbols "
                    "WHERE project_slug = ? AND kind = 'class'",
                    (repo.slug,),
                ).fetchall()
            class_names = {_squash(r["name"]) for r in rows if r["name"]}
        except Exception as exc:
            # Say so on the links, so "no class matched" is not read as "checked, none matched".
            symbols_error = f"{type(exc).__name__}: {exc}"
            log.warning("could not read code symbols for %s: %s", repo.slug, exc)

        for db_s, db_info in db_tables_map.items():
            if wanted_db and db_s != wanted_db:
                continue
            matched_tables: list[str] = []
            matched_evidence: list[str] = []
            score = 0.0

            db_tables = db_info["tables"]
            db_type = db_info["db_type"].lower()
            db_name = db_info["db_name"]

            matched_drivers = dep_names & _DB_DRIVER_DEPS.get(db_type, set())
            if matched_drivers:
                score += 0.3
                matched_evidence.append(f"Matching DB drivers in dependencies: {', '.join(sorted(matched_drivers))}")

            for tbl in db_tables:
                if class_names & _table_variants(tbl):
                    matched_tables.append(tbl)

            # ORM use is evidence only alongside something tying this repo to this database;
            # on its own it says the repo uses *a* database, which is true of every ORM repo.
            if has_orm and (matched_drivers or matched_tables):
                score += 0.1
                matched_evidence.append("ORM / Migration framework detected in dependencies")

            distinctive: list[str] = []
            if matched_tables:
                distinctive = [
                    t for t in matched_tables
                    if _singular(_squash(t)) not in _GENERIC_TABLE_NAMES
                    and table_db_count.get(t, 0) < _COMMON_TABLE_DB_COUNT
                ]
                table_score = sum(
                    _TABLE_WEIGHT if t in distinctive else _GENERIC_TABLE_WEIGHT for t in matched_tables
                )
                table_score = min(0.6, table_score)
                # Class names alone, with no driver or ORM behind them, are coincidence-prone: cap.
                if not (matched_drivers or has_orm):
                    table_score = min(table_score, 0.3)
                score += table_score
                matched_evidence.append(
                    f"Model classes match {len(matched_tables)} database tables "
                    f"({len(distinctive)} distinctive): {', '.join(sorted(matched_tables)[:8])}"
                )

            if len(db_name) >= _MIN_NAME_LEN and db_name in repo.slug.lower():
                score += 0.2
                matched_evidence.append(f"Database name '{db_name}' referenced in repository name")

            if score >= 0.25:
                confidence = "high" if score >= 0.6 else "medium" if score >= 0.4 else "low"
                # Driver, ORM and name evidence says "this repo talks to a database like this one",
                # not "this one": without a matched table it cannot rise above low, and HIGH needs
                # more than one table that identifies the database.
                if not matched_tables:
                    confidence = "low"
                elif confidence == "high" and len(distinctive) < _HIGH_MIN_DISTINCTIVE:
                    confidence = "medium"
                links.append({
                    "repo_slug": repo.slug,
                    "repo_name": repo.display_name or repo.slug,
                    "database_slug": db_s,
                    "database_name": db_info["entity"].display_name or db_s,
                    "database_type": db_info["db_type"],
                    "confidence": confidence,
                    "score": round(min(score, 1.0), 2),
                    "matched_tables": sorted(matched_tables),
                    "distinctive_tables": sorted(distinctive),
                    "symbols_error": symbols_error,
                    "evidence": matched_evidence,
                })

    return sorted(links, key=lambda l: -l["score"])


_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
DEFAULT_LINK_LIMIT = 15


def query_cross_asset_links_raw(
    resource_slug: str = "", min_confidence: str = "high", limit: int = DEFAULT_LINK_LIMIT
) -> str:
    """Format cross-asset linkages between repos and databases as structured markdown.

    Only links at `min_confidence` or above, best first and at most `limit`, are listed; the counts of
    everything found are always shown. On a real registry (73 repos x 20 databases) the unfiltered list
    was 359 pairs and 144,000 characters -- more than a chat answer can carry or a person can act on.
    """
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    slug = registry._normalize_slug(resource_slug) if resource_slug else ""

    # Check if slug is repo or database
    is_repo = bool(registry.get(slug)) if slug else False
    is_db = bool(registry.get_database(slug, allow_unreadable=True)) if slug else False

    repo_arg = slug if is_repo else None
    db_arg = slug if is_db else None

    all_links = find_cross_asset_links(registry, repo_slug=repo_arg, database_slug=db_arg)
    floor = _CONFIDENCE_RANK.get((min_confidence or "high").lower())
    if floor is None:
        return f"Unknown min_confidence '{min_confidence}'; use one of: {', '.join(_CONFIDENCE_RANK)}."
    limit = max(1, int(limit or DEFAULT_LINK_LIMIT))
    counts = {c: sum(1 for l in all_links if l["confidence"] == c) for c in ("high", "medium", "low")}
    links = [l for l in all_links if _CONFIDENCE_RANK[l["confidence"]] >= floor][:limit]

    if all_links and not links:
        scope_str = f" for '{resource_slug}'" if resource_slug else ""
        return (
            f"No {min_confidence}-confidence links found{scope_str}; lower-confidence ones exist "
            f"({counts['medium']} medium, {counts['low']} low). Ask with min_confidence='medium' or 'low' to see them."
        )
    if not links:
        scope_str = f" for resource '{resource_slug}'" if resource_slug else ""
        return (
            f"No cross-asset dependencies or linkages found{scope_str}. "
            "Ensure both repositories and databases have been surveyed to discover table and ORM linkages."
        )

    lines: list[str] = [
        "### Discovered Cross-Asset Dependencies (Code Repositories ↔ Databases)\n",
        "| Repository | Database | DB Type | Confidence | Matched Tables | Evidence Summary |",
        "|---|---|---|---|---|---|",
    ]

    for l in links:
        tbl_str = ", ".join(l["matched_tables"][:4])
        if len(l["matched_tables"]) > 4:
            tbl_str += f" (+{len(l['matched_tables']) - 4} more)"
        if not tbl_str:
            tbl_str = "(Driver / Config match)"

        ev_str = "; ".join(l["evidence"][:2])
        lines.append(
            f"| `{l['repo_slug']}` | `{l['database_slug']}` | {l['database_type']} | "
            f"**{l['confidence'].upper()}** ({l['score']}) | {tbl_str} | {ev_str} |"
        )

    lines.append("\n#### Detailed Linkage Breakdown:")
    for l in links:
        lines.append(f"- **{l['repo_name']}** (`{l['repo_slug']}`) ↔ **{l['database_name']}** (`{l['database_slug']}`):")
        for ev in l["evidence"]:
            lines.append(f"  - {ev}")

    shown_note = (
        f"\nShowing {len(links)} of {len(all_links)} links found "
        f"({counts['high']} high, {counts['medium']} medium, {counts['low']} low); "
        f"minimum confidence '{min_confidence}', limit {limit}. "
        "A link means the repo's model classes and the database's tables share names, "
        "not that the repo owns the database."
    )
    return ("\n".join(lines) + shown_note).strip()
