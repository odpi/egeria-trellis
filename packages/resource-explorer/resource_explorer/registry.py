"""Project Registry — SQLite-backed store for registered GitHub projects and databases."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, NamedTuple
from sqlalchemy import create_engine

log = logging.getLogger(__name__)


def current_user_id() -> str:
    """The signed-in caller's user id, or `''` for shared/service work.

    The registry's single answer to "whose row is this". It reads RE's one
    identity ContextVar (`a2a_auth.current_caller`), set by the web
    `_identity_middleware`, by the A2A middleware, and by the CLI's
    `cli.session.activate()`.

    **Scoping lives here, in the registry layer, and not in the routes** —
    the plan's isolation matrix says so in as many words, and the reason is
    that a route is exactly the place a new endpoint forgets to do it. A
    method that takes `user_id=None` and resolves it here cannot be called
    without scoping; a route that has to remember can.

    `''` is the shared/legacy bucket: every row written before user scoping
    existed, plus anything a service-account path writes. It is a real value
    with a real meaning, not a null-shaped "unknown".
    """
    try:
        from resource_explorer.a2a_auth import caller
    except Exception:  # pragma: no cover - import cycle safety only
        return ""
    identity = caller()
    if identity is None or identity.auth_source == "anonymous":
        return ""
    return identity.user_id or ""


#: The shared/legacy bucket every scoped read falls back to.
SHARED_USER_ID = ""


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    INDEXING = "indexing"
    PAUSED = "paused"
    ERROR = "error"


@dataclass
class ProjectGroup:
    """An umbrella grouping of related resources (repos, databases, filesystems)."""
    slug: str
    display_name: str
    description: str = ""
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class Project:
    slug: str
    display_name: str
    github_url: str
    description: str = ""
    homepage_url: str = ""
    docs_url: str = ""
    github_token_encrypted: str = ""  # encrypted; "" means use global token
    collections: list[str] = field(default_factory=list)
    status: ProjectStatus = ProjectStatus.ACTIVE
    last_indexed_at: str = ""
    last_stats_fetched_at: str = ""
    last_commit_sha: str = ""
    last_surveyed_at: str = ""  # mirrors DatabaseEntity/FileSystemEntity — "" = never surveyed
    last_profiled_at: str = ""  # last successful Coarse Profile refresh (IngestionPipeline.refresh_profile()) — "" = never profiled
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    error_message: str = ""
    subproject_path: str = ""   # relative subdir to index, e.g. "commands" — "" means full repo
    parent_slug: str = ""       # slug of the parent project when this is a sub-project
    extra_docs_paths: list[str] = field(default_factory=list)  # repo-relative paths outside subproject_path to ingest as docs/examples
    egeria_asset_guid: str = ""  # GUID of this project's own Asset in Egeria; "" = not yet published
    governance_state: str = "certified"
    group_slug: str = ""  # slug of the umbrella project group this repo belongs to; "" = ungrouped


@dataclass
class DatabaseServer:
    """Represents a PostgreSQL (or other) database server connection."""
    slug: str
    display_name: str
    db_type: str = "postgresql"
    host: str = "localhost"
    port: int = 5432
    description: str = ""
    db_user: str = ""
    db_password: str = ""
    egeria_host: str = ""   # hostname Egeria uses (e.g. host.docker.internal)
    egeria_url: str = ""
    egeria_server: str = ""
    egeria_user: str = ""
    egeria_password: str = ""
    status: ProjectStatus = ProjectStatus.ACTIVE
    registered_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    error_message: str = ""
    group_slug: str = ""  # slug of the umbrella project group this server belongs to


@dataclass
class DatabaseEntity:
    """Represents a database in the registry."""
    slug: str
    display_name: str
    db_type: str  # "postgresql", "mysql", "oracle", etc.
    host: str       # hostname as seen from the local machine
    port: int
    database_name: str
    description: str = ""
    connection_ref: str = ""  # Reference to secrets store or connection config
    status: ProjectStatus = ProjectStatus.ACTIVE
    registered_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    last_surveyed_at: str = ""
    egeria_asset_guid: str = ""  # GUID of the Database asset in Egeria
    error_message: str = ""
    # Stored connection credentials (plaintext — local dev tool)
    db_user: str = ""
    db_password: str = ""
    # Egeria-visible hostname for the DB server (e.g. host.docker.internal when DB is in Docker)
    egeria_host: str = ""
    # Stored Egeria connection details
    egeria_url: str = ""
    egeria_server: str = ""
    egeria_user: str = ""
    egeria_password: str = ""
    # Server this database belongs to (if registered via server discovery)
    server_slug: str = ""
    governance_state: str = "certified"
    group_slug: str = ""  # slug of the umbrella project group this database belongs to


@dataclass
class FileSystemEntity:
    """Represents a filesystem in the registry."""
    slug: str
    display_name: str
    local_mount_point: str
    canonical_mount_point: str = ""
    description: str = ""
    status: ProjectStatus = ProjectStatus.ACTIVE
    registered_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    last_surveyed_at: str = ""
    egeria_asset_guid: str = ""  # GUID of the FileSystem asset in Egeria
    error_message: str = ""
    # Stored Egeria connection details
    egeria_url: str = ""
    egeria_server: str = ""
    egeria_user: str = ""
    egeria_password: str = ""
    file_count: int = 0
    data_file_count: int = 0
    governance_state: str = "certified"
    group_slug: str = ""  # slug of the umbrella project group this filesystem belongs to


@dataclass
class ActivityEntry:
    id: str
    ts: str
    operation: str    # 'scout' | 'survey' | 'catalog' | 'publish' | 'discover' | 'rfa' | 'refresh' | 'analysis_run'
    intent: str       # 'scouting' | 'assessment' | 'discovery' | 'enrichment'
    entity_type: str  # 'repo' | 'database' | 'server' | 'filesystem' | 'file'
    entity_slug: str
    entity_name: str = ""
    entity_location: str = ""
    status: str = "ok"  # 'running' | 'ok' | 'error' | 'pending'
    summary: str = ""
    detail: str = ""
    items: list[dict] = field(default_factory=list)
    annotations: list[dict] = field(default_factory=list)


def _json_sanitize(obj):
    """Replace NaN/Infinity with None recursively.

    Survey data computed with pandas (e.g. null-rate stats) can contain NaN,
    which json.dumps accepts by default but Starlette's JSONResponse rejects
    (allow_nan=False) — so a value that round-tripped fine into SQLite would
    500 on the way back out over the API. Applied on read so already-stored
    rows self-heal without needing a re-survey.
    """
    if isinstance(obj, dict):
        return {k: _json_sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_sanitize(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


class RowWrapper:
    def __init__(self, description, row_tuple):
        self._keys = [col[0] for col in description] if description else []
        self._values = row_tuple
        self._dict = dict(zip(self._keys, self._values))

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._dict[key]

    def keys(self):
        return self._keys

    def values(self):
        return self._values

    def items(self):
        return self._dict.items()

    def __iter__(self):
        return iter(self._keys)

    def get(self, key, default=None):
        return self._dict.get(key, default)

    def __repr__(self):
        return repr(self._dict)


#: How long a claimed outbox row stays claimed before another drainer may take
#: it. A drainer that is killed mid-row — which is exactly what happened on
#: 2026-09-02, when a batch republish was pkill'd after stalling the Egeria
#: platform — would otherwise strand its rows in 'running' forever, and the
#: retry machinery would never see them again. Long enough that a slow-but-live
#: create is not stolen from underneath its owner: the slowest single element
#: observed is well under a minute, and a whole 200-row batch under ten.
CLAIM_LEASE_SECONDS = 1800


class ConnectionWrapper:
    def __init__(self, raw_conn, is_postgres: bool):
        self.raw_conn = raw_conn
        self.is_postgres = is_postgres

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.raw_conn.rollback()
        else:
            self.raw_conn.commit()

    def execute(self, sql: str, params: Any = None):
        cursor = self.cursor()
        cursor.execute(sql, params)
        return cursor

    def executemany(self, sql: str, param_list: list):
        cursor = self.cursor()
        cursor.executemany(sql, param_list)
        return cursor

    def commit(self):
        self.raw_conn.commit()

    def close(self):
        self.raw_conn.close()

    def cursor(self):
        raw_cursor = self.raw_conn.cursor()
        if self.is_postgres:
            return PostgresCursorWrapper(raw_cursor)
        else:
            return SQLiteCursorWrapper(raw_cursor)


class SQLiteCursorWrapper:
    def __init__(self, raw_cursor):
        self.raw_cursor = raw_cursor

    def execute(self, sql: str, params: Any = None):
        if params is None:
            self.raw_cursor.execute(sql)
        else:
            self.raw_cursor.execute(sql, params)
        return self

    def executemany(self, sql: str, param_list: list):
        self.raw_cursor.executemany(sql, param_list)
        return self

    def fetchone(self):
        row = self.raw_cursor.fetchone()
        if row is None:
            return None
        return RowWrapper(self.raw_cursor.description, row)

    def fetchall(self):
        rows = self.raw_cursor.fetchall()
        desc = self.raw_cursor.description
        return [RowWrapper(desc, r) for r in rows]

    @property
    def rowcount(self):
        return self.raw_cursor.rowcount


class PostgresCursorWrapper:
    def __init__(self, raw_cursor):
        self.raw_cursor = raw_cursor

    def _translate_sql(self, sql: str) -> str:
        sql_trans = sql.replace('?', '%s')
        sql_trans = re.sub(r'(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)', r'%(\1)s', sql_trans)
        sql_trans = re.sub(r'(?i)\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b', 'SERIAL PRIMARY KEY', sql_trans)
        # GROUP_CONCAT(expr[, sep]) -> STRING_AGG(expr, sep) — Postgres has no
        # GROUP_CONCAT. Assumes a single, non-nested-comma argument (true of
        # every current call site); SQLite's default separator is ',',
        # matching STRING_AGG's required (no-default) separator argument.
        def _group_concat_to_string_agg(m):
            expr, sep = m.group(1), m.group(2) or "','"
            return f"STRING_AGG({expr}, {sep})"

        sql_trans = re.sub(
            r'(?i)\bGROUP_CONCAT\(\s*([^,()]+?)\s*(?:,\s*([^()]+?)\s*)?\)',
            _group_concat_to_string_agg,
            sql_trans,
        )
        return sql_trans

    def execute(self, sql: str, params: Any = None):
        sql_trans = self._translate_sql(sql)
        if params is None:
            self.raw_cursor.execute(sql_trans)
        else:
            self.raw_cursor.execute(sql_trans, params)
        return self

    def executemany(self, sql: str, param_list: list):
        if not param_list:
            return self
        sql_trans = self._translate_sql(sql)
        self.raw_cursor.executemany(sql_trans, param_list)
        return self

    def fetchone(self):
        row = self.raw_cursor.fetchone()
        if row is None:
            return None
        return RowWrapper(self.raw_cursor.description, row)

    def fetchall(self):
        rows = self.raw_cursor.fetchall()
        desc = self.raw_cursor.description
        return [RowWrapper(desc, r) for r in rows]

    @property
    def rowcount(self):
        return self.raw_cursor.rowcount


#: What a schedule row's `analysis_id` refers to. Running always goes through a
#: survey type by preference (a single analysis gets a single-step survey type,
#: so there is one way to run things rather than two) — TARGET_ANALYSIS remains
#: for the rows that predate that and for resource types with no authored
#: Survey Definitions.
TARGET_ANALYSIS = "analysis"
TARGET_SURVEY = "survey"


#: A finding row carrying this `label` WITHDRAWS its `scope_locator`: it states
#: that as of that `surveyed_at`, whatever used to propose this scope no longer
#: does. See `docs/architecture-recovery-scope-tombstoning.md` in
#: resource-explorer for the full design.
#:
#: A reserved LABEL rather than a check_name, so the registry stays
#: kind-agnostic — any analysis kind withdraws a scope the same way, and
#: `query_finding_scopes` needs one generic rule rather than a vocabulary of
#: per-kind exceptions.
#:
#: **Withdrawal is not deletion.** The rows stay, `query_findings` still returns
#: them, and `query_findings_all_runs` is untouched — the provenance view must
#: keep answering "who proposed what, ever" after the current answer changes.
WITHDRAWN_LABEL = "withdrawn"


# ── Structured DB/FS detail tables: provenance and measurement state ────────

#: `source` values for the structured DB/FS detail tables. Rows are keyed by
#: (slug, surveyed_at, source) so a native Egeria survey and an RE-local
#: survey of the same resource on the same day coexist rather than overwrite
#: (multi-resource-questions-design.md §3 rule D).
SOURCE_LOCAL = "local"      #: RE's own surveyors computed this row.
SOURCE_EGERIA = "egeria"    #: materialised from a native Egeria survey report.

#: `state` values carried by every structured DB/FS detail row, and by
#: the coverage tables.
#:
#: These exist to keep "we could not measure this" separate from "we measured
#: it and there was nothing". Collapsing the two is the bug the
#: `find-absence-as-answer` skill is about, and both Egeria's Postgres
#: connector docs and this design doc (§5.1) call it out specifically: a
#: native Postgres survey that returns no schemas means the survey userId
#: lacked permission, NOT that the database has no schemas; an empty
#: `pg_stats` means ANALYZE has never run, NOT that the column holds no
#: values. A consumer that sees STATE_MEASURED with a zero count may say
#: "none"; for every other state it must say why instead.
STATE_MEASURED = "measured"
#: Measured successfully, and the answer is genuinely empty. Distinct from
#: every state below: this one, and only this one, licenses rendering "none".
STATE_EMPTY = "empty"
#: The survey user lacked permission to see this. Postgres reports it as
#: absence, which is why it must be recorded explicitly at capture time —
#: nothing downstream can recover the distinction later.
STATE_NOT_PERMITTED = "not_permitted"
#: The source of the measurement exists but has never been populated —
#: `pg_stats` before any ANALYZE, tuple counters after a stats reset.
#: Renders as "run ANALYZE", never as "no values".
STATE_NOT_COLLECTED = "not_collected"
#: The engine cannot provide this at all (design §5.1's capability
#: declaration): a DuckDB connection asked for `pg_stat_replication`.
STATE_NOT_SUPPORTED = "not_supported"
#: The step that would have measured this did not run in this survey — the
#: commonest case for a back-filled row, where the old blob simply never
#: carried the field.
STATE_NOT_MEASURED = "not_measured"
#: The row exists, and something WAS measured — but by the catalog-only
#: fallback (design: REPLY-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md §0,
#: ASK-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md #251), not by the normal
#: `information_schema` path. `PostgreSQLConnection._get_tables_for_schema()`
#: falls back to `pg_class`/`pg_attribute`/`pg_namespace` — catalog metadata
#: any connected role can read regardless of grants — for a table/column
#: `information_schema` came back thin on (no `SELECT` on the underlying
#: table). That gives a table/column NAME and a Postgres TYPE name for
#: certain, but never real PK/FK detail, `is_nullable`, `column_default` or
#: an exact comment (those lookups are themselves privilege-filtered), and
#: any row count attached to a catalog-only row is `pg_class.reltuples` — an
#: ANALYZE-time estimate, not a live count.
#:
#: Deliberately NOT in `STATES_WITHOUT_A_MEASUREMENT` below: a catalog-only
#: row is not absent, it is just less precise than one measured the normal
#: way. Collapsing "we have an approximate answer" into "we have nothing"
#: would be its own confident-wrong-answer shape, in the opposite direction
#: from the one this state exists to prevent.
STATE_CATALOG_ESTIMATE = "catalog_estimate"

#: Every state other than STATE_MEASURED/STATE_EMPTY/STATE_CATALOG_ESTIMATE
#: means the number beside it is absent rather than zero.
STATES_WITHOUT_A_MEASUREMENT = frozenset({
    STATE_NOT_PERMITTED,
    STATE_NOT_COLLECTED,
    STATE_NOT_SUPPORTED,
    STATE_NOT_MEASURED,
})

# ── whose statistics, and as of when ───────────────────────────────────────
#
# `stats_source` on `database_column_profiles` / `filesystem_data_files`.
# Added on designer review, 2026-09-20. `source` says who ran the *survey*;
# `stats_source` says who computed the *numbers*, which is a different
# question and often a different answer — a native Egeria survey and an RE
# local survey can both report a column's frequent values straight out of the
# database's own pg_stats, in which case neither of them computed anything.
#
# `stats_computed_at` is the companion: when those numbers were computed, as
# opposed to when the survey that reports them ran. NULL means unknown, and
# for STATS_SOURCE_DATABASE specifically it is the "ANALYZE has never run"
# signal — which is why it is a nullable timestamp rather than a flag.

#: The database computed these (Postgres `pg_stats`, populated by ANALYZE).
#: Covers the whole table, was not computed by us, and may be far older than
#: the survey reporting it. `stats_computed_at` should carry `last_analyze`;
#: NULL means ANALYZE has never run and the answer is "run ANALYZE", not
#: "this column has no values".
STATS_SOURCE_DATABASE = "database"
#: Resource Explorer computed these at survey time, by reading values —
#: bounded by the sampling settings in the same row (design §5.8).
STATS_SOURCE_RESOURCE_EXPLORER = "resource_explorer"

#: Sections named by the per-type coverage tables. One row per section per
#: (slug, surveyed_at, source), so "this survey never looked at grants" is a
#: stored fact rather than an inference from an empty `database_grants`.
SECTION_SCHEMAS = "schemas"
SECTION_TABLES = "tables"
SECTION_COLUMNS = "columns"
SECTION_COLUMN_PROFILES = "column_profiles"
SECTION_TABLE_ACTIVITY = "table_activity"
SECTION_GRANTS = "grants"
SECTION_SQL_OBJECTS = "sql_objects"
SECTION_SETTINGS = "settings"
SECTION_ENTRIES = "entries"
SECTION_DATA_FILES = "data_files"


#: DDL for the structured DB/FS detail tables (design §5.7, §6).
#:
#: Written in SQLite dialect like every other statement in this file;
#: `PostgresCursorWrapper._translate_sql` rewrites `INTEGER PRIMARY KEY
#: AUTOINCREMENT` to `SERIAL PRIMARY KEY` and `?` to `%s` on the way out.
#: JSON is TEXT, timestamps are ISO-8601 TEXT, booleans are INTEGER — all
#: three per this file's existing convention.
#:
#: Nullable numeric columns are deliberate: NULL means "not measured", and the
#: row's `state` says why. A 0 in these columns is a real measured zero.
#:
#: Reachability outcome vocabulary (Phase 1 slice #13), taken verbatim from
#: `docs/egeria-support-for-multi-resource.md` §5's "Gap and ask" section
#: rather than invented for this table -- confirmed unchanged during this
#: slice's build by a peer review of the design doc. `unknown` doubles as
#: the third state of the reachability check's absence discipline: it means
#: "checked, but the CHECK_ASSET call itself did not complete or could not
#: be evaluated" (timeout, exception, initiation failure) -- as opposed to
#: no row at all ("never checked") or any of the other five outcomes, which
#: all mean "checked, and here is what Egeria said." See reachability.py's
#: classify_check_asset_result().
REACHABILITY_OUTCOMES: frozenset[str] = frozenset({
    "reachable",
    "no_connection",
    "unresolvable_secret",
    "network_unreachable",
    "auth_rejected",
    "unknown",
})

_DB_FS_DETAIL_TABLE_DDL: tuple[str, ...] = (
    # ── databases ──────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS database_schemas (
        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
        database_slug         TEXT NOT NULL,
        surveyed_at           TEXT NOT NULL,
        source                TEXT NOT NULL DEFAULT 'local',
        schema_name           TEXT NOT NULL,
        qualified_schema_name TEXT DEFAULT '',
        description           TEXT DEFAULT '',
        table_count           INTEGER DEFAULT NULL,
        view_count            INTEGER DEFAULT NULL,
        mat_view_count        INTEGER DEFAULT NULL,
        column_count          INTEGER DEFAULT NULL,
        total_table_size_bytes INTEGER DEFAULT NULL,
        state                 TEXT NOT NULL DEFAULT 'measured',
        UNIQUE(database_slug, surveyed_at, source, schema_name),
        FOREIGN KEY (database_slug) REFERENCES databases(slug)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS database_tables (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        database_slug        TEXT NOT NULL,
        surveyed_at          TEXT NOT NULL,
        source               TEXT NOT NULL DEFAULT 'local',
        schema_name          TEXT NOT NULL,
        table_name           TEXT NOT NULL,
        qualified_table_name TEXT DEFAULT '',
        table_type           TEXT DEFAULT '',
        table_owner          TEXT DEFAULT '',
        description          TEXT DEFAULT '',
        column_count         INTEGER DEFAULT NULL,
        row_count            INTEGER DEFAULT NULL,
        size_bytes           INTEGER DEFAULT NULL,
        is_populated         INTEGER DEFAULT NULL,
        has_indexes          INTEGER DEFAULT NULL,
        has_rules            INTEGER DEFAULT NULL,
        has_triggers         INTEGER DEFAULT NULL,
        has_row_security     INTEGER DEFAULT NULL,
        query_definition     TEXT DEFAULT '',
        state                TEXT NOT NULL DEFAULT 'measured',
        UNIQUE(database_slug, surveyed_at, source, schema_name, table_name),
        FOREIGN KEY (database_slug) REFERENCES databases(slug)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS database_columns (
        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
        database_slug         TEXT NOT NULL,
        surveyed_at           TEXT NOT NULL,
        source                TEXT NOT NULL DEFAULT 'local',
        schema_name           TEXT NOT NULL,
        table_name            TEXT NOT NULL,
        column_name           TEXT NOT NULL,
        qualified_column_name TEXT DEFAULT '',
        ordinal_position      INTEGER DEFAULT NULL,
        data_type             TEXT DEFAULT '',
        base_type             TEXT DEFAULT '',
        column_size           INTEGER DEFAULT NULL,
        is_nullable           INTEGER DEFAULT NULL,
        column_default        TEXT DEFAULT '',
        description           TEXT DEFAULT '',
        is_primary_key        INTEGER DEFAULT 0,
        foreign_key_json      TEXT DEFAULT NULL,
        state                 TEXT NOT NULL DEFAULT 'measured',
        UNIQUE(database_slug, surveyed_at, source, schema_name, table_name, column_name),
        FOREIGN KEY (database_slug) REFERENCES databases(slug)
    )
    """,
    # Separate from database_columns because a profile is a different
    # measurement with a different cost and a different absence mode: the
    # column exists (catalog read) while its profile may be missing because
    # ANALYZE never ran (STATE_NOT_COLLECTED). Folding them into one table
    # would make "column present, stats absent" unrepresentable.
    """
    CREATE TABLE IF NOT EXISTS database_column_profiles (
        id                        INTEGER PRIMARY KEY AUTOINCREMENT,
        database_slug             TEXT NOT NULL,
        surveyed_at               TEXT NOT NULL,
        source                    TEXT NOT NULL DEFAULT 'local',
        schema_name               TEXT NOT NULL,
        table_name                TEXT NOT NULL,
        column_name               TEXT NOT NULL,
        null_fraction             REAL DEFAULT NULL,
        distinct_count            REAL DEFAULT NULL,
        -- Why distinct_count is NULL, when it is. Designer review round 3
        -- (§3, "the corrected distinct_count, and the blank the correction
        -- produced"): `_resolve_n_distinct` in database_surveyor.py returns
        -- None for four different reasons, and this row's own `state` stays
        -- STATE_MEASURED because every other pg_stats field came back fine
        -- — a row-level state cannot carry a per-field absence. Values are
        -- database_surveyor.py's REASON_NOT_COLLECTED / REASON_NEVER_ANALYZED
        -- / REASON_STATS_RESET, or NULL when distinct_count is itself
        -- resolved. NULL here is silent on purpose: it means either
        -- distinct_count is present, or this row predates this column.
        distinct_count_reason     TEXT DEFAULT NULL,
        average_width             INTEGER DEFAULT NULL,
        correlation               REAL DEFAULT NULL,
        most_common_values_json   TEXT DEFAULT NULL,
        most_common_freqs_json    TEXT DEFAULT NULL,
        histogram_bounds_json     TEXT DEFAULT NULL,
        min_value                 TEXT DEFAULT '',
        max_value                 TEXT DEFAULT '',
        -- Whose numbers these are, and as of when. Designer review,
        -- 2026-09-20: `sample_strategy` answers how much was looked at; it
        -- does not answer whose statistics these are or when they were
        -- computed. A Postgres profile can come from the database's own
        -- pg_stats — whole table, not ours, possibly months older than the
        -- survey reporting it. A file profile is computed by RE at survey
        -- time. Without these two a card shows a null fraction computed
        -- three months ago beside a row count from two minutes ago and says
        -- nothing about the difference.
        --
        -- This also makes the "no statistics collected, run ANALYZE" state
        -- exact rather than special-cased: it is simply
        -- stats_source = STATS_SOURCE_DATABASE with stats_computed_at NULL.
        stats_source              TEXT DEFAULT '',
        stats_computed_at         TEXT DEFAULT NULL,
        sample_strategy           TEXT DEFAULT '',
        sample_rows               INTEGER DEFAULT NULL,
        sample_seed               INTEGER DEFAULT NULL,
        -- The table's total row count as known when the sample was taken.
        -- Phase 1 slice 10 (design §5.8): the envelope sentence §5.8 makes a
        -- hard requirement is "from a random sample of 10,000 of 4.2M rows
        -- (seed 7, ...)", and `sample_rows` alone is the 10,000 — without the
        -- 4.2M, a sample size cannot be read as a proportion, which is the
        -- only thing that makes it interpretable. NULL means the total was
        -- not established (the statistics collector had no row for the
        -- table), which is distinct from 0.
        sample_total_rows         INTEGER DEFAULT NULL,
        state                     TEXT NOT NULL DEFAULT 'measured',
        UNIQUE(database_slug, surveyed_at, source, schema_name, table_name, column_name),
        FOREIGN KEY (database_slug) REFERENCES databases(slug)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS database_table_activity (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        database_slug     TEXT NOT NULL,
        surveyed_at       TEXT NOT NULL,
        source            TEXT NOT NULL DEFAULT 'local',
        schema_name       TEXT NOT NULL,
        table_name        TEXT NOT NULL,
        rows_inserted     INTEGER DEFAULT NULL,
        rows_updated      INTEGER DEFAULT NULL,
        rows_deleted      INTEGER DEFAULT NULL,
        hot_updates       INTEGER DEFAULT NULL,
        live_tuples       INTEGER DEFAULT NULL,
        dead_tuples       INTEGER DEFAULT NULL,
        seq_scan          INTEGER DEFAULT NULL,
        idx_scan          INTEGER DEFAULT NULL,
        last_vacuum       TEXT DEFAULT '',
        last_autovacuum   TEXT DEFAULT '',
        last_analyze      TEXT DEFAULT '',
        last_autoanalyze  TEXT DEFAULT '',
        pending_changes   INTEGER DEFAULT NULL,
        -- When Postgres last reset the counters above. Designer review,
        -- 2026-09-20: `pg_stat_user_tables` counters are cumulative since
        -- the last stats reset, and a change *rate* is the difference
        -- between two snapshots. A reset, failover or restore between
        -- snapshots makes that difference negative, and a small-multiples
        -- chart would faithfully draw "−40,000 inserts" — a wrong number
        -- rendered confidently.
        --
        -- Storing the reset timestamp per snapshot lets a comparator see
        -- that it moved and report the interval as "counters reset, no rate
        -- available" instead of as a rate. That check belongs to the change
        -- comparators (design §9.1); this column is the evidence they need,
        -- and without it the information is gone by the time they run.
        stats_reset       TEXT DEFAULT NULL,
        state             TEXT NOT NULL DEFAULT 'measured',
        UNIQUE(database_slug, surveyed_at, source, schema_name, table_name),
        FOREIGN KEY (database_slug) REFERENCES databases(slug)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS database_grants (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        database_slug  TEXT NOT NULL,
        surveyed_at    TEXT NOT NULL,
        source         TEXT NOT NULL DEFAULT 'local',
        schema_name    TEXT DEFAULT '',
        object_name    TEXT DEFAULT '',
        object_type    TEXT DEFAULT '',
        grantee        TEXT NOT NULL,
        grantor        TEXT DEFAULT '',
        privilege_type TEXT NOT NULL,
        is_grantable   INTEGER DEFAULT 0,
        state          TEXT NOT NULL DEFAULT 'measured',
        UNIQUE(database_slug, surveyed_at, source, schema_name, object_name,
               grantee, privilege_type),
        FOREIGN KEY (database_slug) REFERENCES databases(slug)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS database_sql_objects (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        database_slug       TEXT NOT NULL,
        surveyed_at         TEXT NOT NULL,
        source              TEXT NOT NULL DEFAULT 'local',
        schema_name         TEXT NOT NULL,
        object_name         TEXT NOT NULL,
        object_type         TEXT NOT NULL DEFAULT 'view',
        definition          TEXT DEFAULT '',
        depends_on_json     TEXT DEFAULT NULL,
        column_lineage_json TEXT DEFAULT NULL,
        complexity          INTEGER DEFAULT NULL,
        state               TEXT NOT NULL DEFAULT 'measured',
        UNIQUE(database_slug, surveyed_at, source, schema_name, object_name, object_type),
        FOREIGN KEY (database_slug) REFERENCES databases(slug)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS database_settings (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        database_slug TEXT NOT NULL,
        surveyed_at   TEXT NOT NULL,
        source        TEXT NOT NULL DEFAULT 'local',
        setting_name  TEXT NOT NULL,
        setting_value TEXT DEFAULT '',
        unit          TEXT DEFAULT '',
        category      TEXT DEFAULT '',
        setting_source TEXT DEFAULT '',
        state         TEXT NOT NULL DEFAULT 'measured',
        UNIQUE(database_slug, surveyed_at, source, setting_name),
        FOREIGN KEY (database_slug) REFERENCES databases(slug)
    )
    """,
    # ── file systems ───────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS filesystem_entries (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        filesystem_slug     TEXT NOT NULL,
        surveyed_at         TEXT NOT NULL,
        source              TEXT NOT NULL DEFAULT 'local',
        entry_path          TEXT NOT NULL,
        entry_name          TEXT DEFAULT '',
        entry_type          TEXT NOT NULL DEFAULT 'file',
        size_bytes          INTEGER DEFAULT NULL,
        file_extension      TEXT DEFAULT '',
        file_type           TEXT DEFAULT '',
        asset_type          TEXT DEFAULT '',
        deployed_impl_type  TEXT DEFAULT '',
        is_hidden           INTEGER DEFAULT NULL,
        is_symlink          INTEGER DEFAULT NULL,
        is_executable       INTEGER DEFAULT NULL,
        is_writable         INTEGER DEFAULT NULL,
        is_readable         INTEGER DEFAULT NULL,
        created_at          TEXT DEFAULT '',
        modified_at         TEXT DEFAULT '',
        accessed_at         TEXT DEFAULT '',
        record_count        INTEGER DEFAULT NULL,
        state               TEXT NOT NULL DEFAULT 'measured',
        UNIQUE(filesystem_slug, surveyed_at, source, entry_path),
        FOREIGN KEY (filesystem_slug) REFERENCES file_systems(slug)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS filesystem_data_files (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        filesystem_slug TEXT NOT NULL,
        surveyed_at     TEXT NOT NULL,
        source          TEXT NOT NULL DEFAULT 'local',
        file_path       TEXT NOT NULL,
        format          TEXT DEFAULT '',
        row_count       INTEGER DEFAULT NULL,
        column_count    INTEGER DEFAULT NULL,
        schema_json     TEXT DEFAULT NULL,
        null_summary    TEXT DEFAULT '',
        file_size_bytes INTEGER DEFAULT NULL,
        -- Same pair as database_column_profiles, for the same reason. A file
        -- profile is normally computed by RE at survey time
        -- (STATS_SOURCE_RESOURCE_EXPLORER), so stats_computed_at is usually
        -- the survey time — but not always: a profile carried over from an
        -- earlier run, or read from a sidecar manifest the publisher wrote,
        -- is a different age from the walk that found the file, and the card
        -- shows them side by side.
        stats_source      TEXT DEFAULT '',
        stats_computed_at TEXT DEFAULT NULL,
        state           TEXT NOT NULL DEFAULT 'measured',
        UNIQUE(filesystem_slug, surveyed_at, source, file_path),
        FOREIGN KEY (filesystem_slug) REFERENCES file_systems(slug)
    )
    """,
    # ── coverage ───────────────────────────────────────────────────────────
    # Not in the design doc's table list, added because rule "absence is a
    # result" is otherwise unimplementable: an empty `database_grants` for a
    # given (slug, surveyed_at, source) is ambiguous between "this survey did
    # not look at grants", "the survey user could not read pg_roles" and
    # "there genuinely are no grants". Per-row `state` cannot express any of
    # those, because in each case there is no row to carry it. One row per
    # section per survey run makes the distinction a stored fact.
    #
    # One table per resource type rather than one polymorphic table keyed
    # (resource_type, resource_slug). The polymorphic form was written first
    # and replaced: a slug column that cannot carry a foreign key is exactly
    # the shape `tests/test_no_orphaned_slugs.py` exists to catch, and it is
    # right to catch it here — coverage describes a survey *of* a resource,
    # so when the resource is deleted these rows are debris, not history
    # that outlives it. Two typed tables get a real FK and need no exemption.
    """
    CREATE TABLE IF NOT EXISTS database_survey_coverage (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        database_slug TEXT NOT NULL,
        surveyed_at   TEXT NOT NULL,
        source        TEXT NOT NULL DEFAULT 'local',
        section       TEXT NOT NULL,
        state         TEXT NOT NULL DEFAULT 'measured',
        row_count     INTEGER DEFAULT NULL,
        detail        TEXT DEFAULT '',
        UNIQUE(database_slug, surveyed_at, source, section),
        FOREIGN KEY (database_slug) REFERENCES databases(slug)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS filesystem_survey_coverage (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        filesystem_slug TEXT NOT NULL,
        surveyed_at     TEXT NOT NULL,
        source          TEXT NOT NULL DEFAULT 'local',
        section         TEXT NOT NULL,
        state           TEXT NOT NULL DEFAULT 'measured',
        row_count       INTEGER DEFAULT NULL,
        detail          TEXT DEFAULT '',
        UNIQUE(filesystem_slug, surveyed_at, source, section),
        FOREIGN KEY (filesystem_slug) REFERENCES file_systems(slug)
    )
    """,
    # ── reachability (Phase 1 slice #13) ────────────────────────────────────
    # Deferred 2026-09-21 (egeria-support-for-multi-resource.md §5/§10: "defer
    # the resource_reachability table until further tests -- do not build it
    # yet") pending live confirmation of probe 7's premise and of what a
    # CHECK_ASSET result actually looks like. Un-deferred by the project
    # owner 2026-09-22 -- see docs/design-notes/RESOURCE-REACHABILITY-
    # IMPLEMENTED.md and PROBES-2026-09-21.md's "Probes 7 and 8, run live"
    # section for the live evidence this table's shape is built from.
    #
    # Scoped to filesystem/folder resources only, per the same live evidence:
    # probe 7 confirmed the mechanism (initiate_gov_action_type against
    # FileSurvey::survey-folder with finalAnalysisStep=CHECK_ASSET) but ALSO
    # confirmed RE's own create_folder_element_from_template() DataFolder
    # template attaches no Connection at all -- every filesystem RE has ever
    # cataloged this way hits the "no_connection" outcome, not a genuine
    # reachability answer, until a real Connection is attached (see the
    # design notes doc). Database reachability was NOT probed here -- the
    # design doc's own §5 gap analysis is explicitly about the folder-survey
    # CHECK_ASSET mechanism, and the two are different governance action
    # types with different failure shapes (secrets-store resolution vs. a
    # plain filesystem Connection) -- extending this table/check to databases
    # is future work, not assumed to work the same way.
    #
    # `outcome` uses the exact vocabulary from egeria-support-for-multi-
    # resource.md §5 (confirmed by a peer review during this slice's build,
    # not invented fresh): reachable, no_connection, unresolvable_secret,
    # network_unreachable, auth_rejected, unknown. `unknown` is also the
    # three-state-discipline's third state -- "the CHECK_ASSET call itself
    # did not complete/could not be evaluated" (timeout, exception, initiation
    # failure) -- distinct from both "never checked" (no row at all) and any
    # of the other outcomes, which all mean "checked, and here is what Egeria
    # said". See reachability.py's classify_check_asset_result() docstring.
    """
    CREATE TABLE IF NOT EXISTS resource_reachability (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        resource_type      TEXT NOT NULL DEFAULT 'filesystem',
        filesystem_slug    TEXT NOT NULL,
        probed_at          TEXT NOT NULL,
        probed_from        TEXT NOT NULL DEFAULT '',
        outcome            TEXT NOT NULL,
        error_code         TEXT DEFAULT '',
        error_detail       TEXT DEFAULT '',
        latency_ms         INTEGER DEFAULT NULL,
        engine_action_guid TEXT DEFAULT '',
        FOREIGN KEY (filesystem_slug) REFERENCES file_systems(slug)
    )
    """,
)

#: Coverage table and slug column per resource type. Separate from
#: `_DETAIL_TABLE_SPECS` because a coverage row is keyed by section rather
#: than by a surveyed object, so it does not use the generic reader/writer.
_COVERAGE_TABLES: dict[str, tuple[str, str]] = {
    "database": ("database_survey_coverage", "database_slug"),
    "filesystem": ("filesystem_survey_coverage", "filesystem_slug"),
}

#: Lookup indexes. The UNIQUE constraints above already cover the
#: (slug, surveyed_at, source) prefix for point lookups; these serve the
#: "latest run for this slug" and cross-run trend queries.
_DB_FS_DETAIL_TABLE_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_db_schemas_slug ON database_schemas(database_slug, surveyed_at)",
    "CREATE INDEX IF NOT EXISTS idx_db_tables_slug ON database_tables(database_slug, surveyed_at)",
    "CREATE INDEX IF NOT EXISTS idx_db_columns_slug ON database_columns(database_slug, surveyed_at)",
    "CREATE INDEX IF NOT EXISTS idx_db_columns_table ON database_columns(database_slug, schema_name, table_name)",
    "CREATE INDEX IF NOT EXISTS idx_db_col_profiles_slug ON database_column_profiles(database_slug, surveyed_at)",
    "CREATE INDEX IF NOT EXISTS idx_db_table_activity_slug ON database_table_activity(database_slug, surveyed_at)",
    "CREATE INDEX IF NOT EXISTS idx_db_grants_slug ON database_grants(database_slug, surveyed_at)",
    "CREATE INDEX IF NOT EXISTS idx_db_grants_grantee ON database_grants(database_slug, grantee)",
    "CREATE INDEX IF NOT EXISTS idx_db_sql_objects_slug ON database_sql_objects(database_slug, surveyed_at)",
    "CREATE INDEX IF NOT EXISTS idx_db_settings_slug ON database_settings(database_slug, surveyed_at)",
    "CREATE INDEX IF NOT EXISTS idx_fs_entries_slug ON filesystem_entries(filesystem_slug, surveyed_at)",
    "CREATE INDEX IF NOT EXISTS idx_fs_data_files_slug ON filesystem_data_files(filesystem_slug, surveyed_at)",
    "CREATE INDEX IF NOT EXISTS idx_db_coverage_slug ON database_survey_coverage(database_slug, surveyed_at)",
    "CREATE INDEX IF NOT EXISTS idx_fs_coverage_slug ON filesystem_survey_coverage(filesystem_slug, surveyed_at)",
    "CREATE INDEX IF NOT EXISTS idx_resource_reachability_slug "
    "ON resource_reachability(filesystem_slug, probed_at)",
)

#: Columns added after the tables above first shipped. Empty at introduction;
#: this is the seam so a later column lands on existing checkouts the same way
#: `database_surveys.source` and the `file_systems` columns did, rather than
#: being silently absent on any registry created before it.
_DB_FS_DETAIL_TABLE_MIGRATIONS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("database_schemas", ()),
    ("database_tables", ()),
    ("database_columns", ()),
    # stats_source/stats_computed_at (designer review, 2026-09-20) were added
    # to this table's CREATE TABLE after it had already been created against
    # the shared registry Postgres during this stream's own development —
    # CREATE TABLE IF NOT EXISTS is a no-op against an existing table, so a
    # pre-existing database_column_profiles was silently missing both columns
    # until this migration entry existed. Found live, running the back-fill
    # against the real shared registry.
    ("database_column_profiles", (
        ("stats_source", "TEXT DEFAULT ''"),
        ("stats_computed_at", "TEXT DEFAULT NULL"),
        # Phase 1 slice 10 — same seam, same reason: the table already exists
        # on every checkout, so the CREATE TABLE addition above is a no-op
        # there and this entry is what actually lands the column.
        ("sample_total_rows", "INTEGER DEFAULT NULL"),
        # distinct_count_reason: same story, round 3 design review addition.
        ("distinct_count_reason", "TEXT DEFAULT NULL"),
    )),
    # stats_reset: same story, same designer-review addition, same gap.
    ("database_table_activity", (
        ("stats_reset", "TEXT DEFAULT NULL"),
    )),
    ("database_grants", ()),
    ("database_sql_objects", ()),
    ("database_settings", ()),
    ("filesystem_entries", ()),
    # Same gap as database_column_profiles above, confirmed live against the
    # same pre-existing shared-registry table.
    ("filesystem_data_files", (
        ("stats_source", "TEXT DEFAULT ''"),
        ("stats_computed_at", "TEXT DEFAULT NULL"),
    )),
    ("database_survey_coverage", ()),
    ("filesystem_survey_coverage", ()),
    ("resource_reachability", ()),
)


class _DetailTableSpec(NamedTuple):
    """Everything the generic detail-row reader/writer needs about one table.

    Derived from the DDL rather than restated, so a column added to a CREATE
    TABLE above is picked up here with no second edit. The alternative — a
    hand-maintained column list per table — is the shape that goes stale
    silently: the INSERT keeps working, the new column just never receives a
    value, and nothing fails.
    """
    table: str
    slug_column: str
    resource_type: str
    value_columns: tuple[str, ...]
    insert_columns: tuple[str, ...]
    json_columns: frozenset[str]
    order_by: str


#: Ordering for each detail table's rows: the order a person reads them in,
#: not insertion order. Anything not named here falls back to its slug column.
_DETAIL_TABLE_ORDER: dict[str, str] = {
    "database_schemas": "schema_name",
    "database_tables": "schema_name, table_name",
    "database_columns": "schema_name, table_name, ordinal_position, column_name",
    "database_column_profiles": "schema_name, table_name, column_name",
    "database_table_activity": "schema_name, table_name",
    "database_grants": "schema_name, object_name, grantee, privilege_type",
    "database_sql_objects": "schema_name, object_type, object_name",
    "database_settings": "setting_name",
    "filesystem_entries": "entry_path",
    "filesystem_data_files": "file_path",
}

_DETAIL_COLUMN_RE = re.compile(r"^\s*([a-z_][a-z0-9_]*)\s+[A-Z]", re.MULTILINE)
_DETAIL_TABLE_NAME_RE = re.compile(r"CREATE TABLE IF NOT EXISTS\s+([a-z_]+)")


def _parse_detail_table_spec(ddl: str) -> _DetailTableSpec | None:
    """Build a spec from one CREATE TABLE statement, or None if it is not one
    of the per-object detail tables (the coverage table is keyed differently
    and is handled by its own methods)."""
    name_match = _DETAIL_TABLE_NAME_RE.search(ddl)
    if not name_match:
        return None
    table = name_match.group(1)
    if table in (
        "database_survey_coverage",
        "filesystem_survey_coverage",
        # resource_reachability (Phase 1 slice #13) is keyed by
        # (filesystem_slug, probed_at, outcome) with its own dedicated
        # methods (record_reachability_check/get_latest_reachability/
        # list_reachability_history) -- it has no surveyed_at/source columns
        # and does not fit the generic (slug, surveyed_at, source, ...,
        # state) detail-row shape the reader/writer below assumes. Excluded
        # here the same way the two coverage tables are, for the same
        # reason: it would otherwise be auto-detected by its `filesystem_
        # slug` column and silently break write_detail_rows()'s generic
        # insert (no surveyed_at/source to insert into).
        "resource_reachability",
    ):
        return None

    # Column lines only: a constraint line starts with UNIQUE/FOREIGN/PRIMARY,
    # which the [a-z_] anchor already excludes.
    columns = [c for c in _DETAIL_COLUMN_RE.findall(ddl) if c != "id"]
    slug_candidates = [c for c in columns if c.endswith("_slug")]
    if not slug_candidates:
        return None
    slug_column = slug_candidates[0]
    value_columns = tuple(
        c for c in columns if c not in (slug_column, "surveyed_at", "source")
    )
    return _DetailTableSpec(
        table=table,
        slug_column=slug_column,
        resource_type="database" if slug_column == "database_slug" else "filesystem",
        value_columns=value_columns,
        insert_columns=(slug_column, "surveyed_at", "source") + value_columns,
        json_columns=frozenset(c for c in value_columns if c.endswith("_json")),
        order_by=_DETAIL_TABLE_ORDER.get(table, slug_column),
    )


_DETAIL_TABLE_SPECS: dict[str, _DetailTableSpec] = {
    spec.table: spec
    for spec in (_parse_detail_table_spec(ddl) for ddl in _DB_FS_DETAIL_TABLE_DDL)
    if spec is not None
}


def _detail_value(value):
    """Coerce one caller-supplied cell to something both backends store.

    dicts and lists become JSON text; bools become 0/1 (SQLite has no boolean
    and Postgres will not accept a Python bool into an INTEGER column); None
    stays None, which is the whole point — NULL means "not measured", and the
    row's `state` says why. Callers must not substitute 0 for it.
    """
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(_json_sanitize(list(value) if isinstance(value, tuple) else value))
    return value


def _decode_detail_row(row: dict) -> dict:
    """Decode `*_json` columns back to Python on the way out."""
    for key, value in list(row.items()):
        if key.endswith("_json") and isinstance(value, str) and value:
            try:
                row[key] = json.loads(value)
            except (ValueError, TypeError):
                # Leave the raw text rather than dropping it: a value that
                # will not parse is evidence of a bug worth seeing, and
                # silently turning it into None would read as "not measured".
                pass
    return row


class ProjectRegistry:
    def __init__(self, db_path: str = "data/registry.db", database_url: str | None = None) -> None:
        """database_url, when given, is used verbatim — bypassing the
        db_path sentinel/config-lookup logic below entirely. Added for the
        real-Postgres integration test tier (Phase 8): db_path's own
        sentinel special-casing has no way to point a ProjectRegistry at an
        explicit, isolated Postgres URL (e.g. a throwaway test schema)
        without either mutating global config or forcing SQLite.
        """
        self.db_path = db_path
        if database_url is not None:
            self.database_url = database_url
        elif db_path == "data/registry.db":
            from resource_explorer.config import get_config
            try:
                config = get_config()
                self.database_url = config.registry.database_url
            except Exception:
                import os
                self.database_url = os.environ.get("REGISTRY_DATABASE_URL", f"sqlite:///{db_path}")
        else:
            self.database_url = f"sqlite:///{db_path}"

        if self.database_url.startswith("sqlite:///"):
            path_str = self.database_url[len("sqlite:///"):]
            if path_str and path_str != ":memory:":
                Path(path_str).parent.mkdir(parents=True, exist_ok=True)

        # pool_pre_ping: validate a pooled connection before handing it out, and
        # transparently replace it if the server has gone away. Without it a
        # connection severed while idle in the pool is discovered only when a
        # caller tries to use it, as
        # `psycopg2.OperationalError: server closed the connection unexpectedly`
        # — which then fails whatever that caller was doing.
        #
        # Observed 2026-08-20: a background org-import batch died on exactly
        # this and failed to register docling-graph. Nothing on the Postgres side
        # closed it — the container had been up 17 hours without restarting,
        # idle_session_timeout is 0, and it was at 25 of 1000 connections. This
        # is known Egeria-side behaviour against the shared instance, being
        # fixed upstream; RE's job is not to fix it here but to survive it.
        #
        # That distinction is the point. An upstream bug RE cannot control turned
        # into a lost registration because the pool handed out a connection it
        # had never checked. pre_ping costs one round-trip per checkout against a
        # local socket and makes the drop a reconnect instead of a failure — so
        # the upstream fix, whenever it lands, stops being load-bearing for us.
        #
        # pool_recycle bounds how long a connection may live regardless, so a
        # half-open connection that still answers a ping cannot linger forever.
        # Both are no-ops for SQLite, which is why they are not conditional.
        self.engine = create_engine(
            self.database_url, pool_pre_ping=True, pool_recycle=1800,
        )
        self._init_schema()

    @contextmanager
    def _conn(self):
        is_postgres = self.database_url.startswith("postgresql")
        raw_conn = self.engine.raw_connection()
        if not is_postgres:
            raw_conn.row_factory = sqlite3.Row
            # SQLite ships with foreign-key enforcement OFF. Production is
            # Postgres, which enforces — so without this the test backend is a
            # strictly *weaker* mirror of production, and any write that
            # violates a FK passes every test and fails live.
            #
            # That is not hypothetical. Two comments in this file already
            # describe deletion-order bugs found exactly this way (see remove()
            # and remove_database()), and on 2026-08-23 a peer session found
            # three orphaned project_analysis_findings rows in the live
            # Postgres for an unregistered slug — inserts that SQLite had
            # happily accepted in tests, absorbed live by a caller's broad
            # except+log.warning so nothing surfaced anywhere.
            #
            # Measured before adding: with the pragma off an orphan INSERT into
            # project_analysis_metrics succeeds; with it on the same statement
            # raises IntegrityError, matching Postgres.
            raw_conn.execute("PRAGMA foreign_keys=ON")
        conn = ConnectionWrapper(raw_conn, is_postgres)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _get_table_columns(self, conn, table_name: str) -> set[str]:
        """Columns currently on table_name, read through the SAME open
        transaction as the caller's CREATE TABLE — not a fresh connection via
        inspect(self.engine). That distinction matters on Postgres: a table
        just CREATEd in this transaction is invisible to a separate
        connection/inspector until commit, which made every "add column if
        missing" migration below think a brand-new table was missing columns
        it had literally just been created with, and crash trying to
        ALTER TABLE ADD a column that already existed. SQLite's PRAGMA
        table_info happened to dodge this (same-connection visibility), which
        is why the bug was invisible until this ran against real Postgres.
        """
        try:
            if conn.is_postgres:
                rows = conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = ? AND table_schema = current_schema()",
                    (table_name,),
                ).fetchall()
                return {r["column_name"] for r in rows}
            else:
                rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
                return {r["name"] for r in rows}
        except Exception:
            return set()

    def _add_user_id_to_keyed_table(
        self, conn, table: str, *, columns: str,
        column_names: "tuple[str, ...]", pk: "tuple[str, ...]",
    ) -> None:
        """Add `user_id` to a table whose PRIMARY KEY must widen to include it.

        The registry's usual convention — `CREATE TABLE IF NOT EXISTS` with the
        new shape, then `ALTER TABLE ... ADD COLUMN` for existing databases —
        is not enough for these two tables, and the reason is easy to miss:
        `ADD COLUMN` leaves the OLD two-column primary key in place. The
        column would appear, the code would write it, and the second user to
        touch a resource would get a duplicate-key error (Postgres) or silently
        overwrite the first (an `ON CONFLICT` naming the old key). The schema
        would look migrated and the isolation would not exist.

        So the key is widened too, per backend:

        * **Postgres** — `DROP CONSTRAINT <table>_pkey` then `ADD PRIMARY KEY`.
          Named by convention, and `IF EXISTS` covers a database where it was
          created some other way.
        * **SQLite** — no `DROP CONSTRAINT`, so the table is rebuilt: create
          the new shape under a temp name, copy every row (existing rows get
          `user_id = ''`, the shared/legacy bucket), drop, rename. Safe here
          because both tables are small, one row per resource, and the whole
          thing runs inside the caller's transaction.

        A no-op when `user_id` is already present, which is every run after the
        first and every freshly-created database.
        """
        existing = self._get_table_columns(conn, table)
        if not existing or "user_id" in existing:
            return

        pk_sql = ", ".join(pk)
        carried = [c for c in column_names if c != "user_id" and c in existing]
        if conn.is_postgres:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT NOT NULL DEFAULT ''")
            conn.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_pkey")
            conn.execute(f"ALTER TABLE {table} ADD PRIMARY KEY ({pk_sql})")
        else:
            tmp = f"{table}__user_id_migration"
            conn.execute(f"DROP TABLE IF EXISTS {tmp}")
            conn.execute(f"CREATE TABLE {tmp} ({columns}, PRIMARY KEY ({pk_sql}))")
            cols = ", ".join(carried)
            conn.execute(f"INSERT INTO {tmp} ({cols}) SELECT {cols} FROM {table}")
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"ALTER TABLE {tmp} RENAME TO {table}")
        log.info("registry: added user_id to %s and widened its primary key", table)

    def _migrate_repo_dispositions_to_entity_key(self, conn) -> None:
        """Widen `repo_dispositions`' primary key from `github_url` alone to
        `(entity_type, entity_slug)` — the schema half of generalizing
        disposition to database/filesystem resources (Backlog.md,
        "Disposition is NOT fixed here", 2026-09-22). No-op once
        `entity_slug` already exists: every run after the first, and every
        freshly-created database (the CREATE TABLE just above already
        declares the new shape).

        Backfill (every pre-migration row is `entity_type='repo'`):
        `entity_slug` is the row's own `project_slug` when non-empty — the
        repo's real, already-resolved slug, which can legitimately differ
        from a github_url-derived guess (a manual slug override, or a
        collision-avoidance rename — confirmed live in the shared dev
        registry: `odpi/egeria`'s row already carries `project_slug=
        'egeria_git'`, not the `_url_to_slug`-derived `'egeria'`). For the
        rarer never-imported candidate (`project_slug` `''` — 1 of 20 rows
        in the shared dev registry as of 2026-09-22), fall back to the same
        `_url_to_slug` derivation `org_importer.py` already uses for a
        candidate's entity_slug elsewhere (`resource_working_set`/
        `activity_log`) — see `add()` for what reconciles this if that repo
        is later imported under a *different* slug.
        """
        existing = self._get_table_columns(conn, "repo_dispositions")
        if not existing or "entity_slug" in existing:
            return
        from resource_explorer.github.org_importer import _url_to_slug

        conn.execute("ALTER TABLE repo_dispositions ADD COLUMN entity_type TEXT NOT NULL DEFAULT 'repo'")
        conn.execute("ALTER TABLE repo_dispositions ADD COLUMN entity_slug TEXT NOT NULL DEFAULT ''")
        rows = conn.execute("SELECT github_url, project_slug FROM repo_dispositions").fetchall()
        for row in rows:
            slug = row["project_slug"] or _url_to_slug(row["github_url"])
            conn.execute(
                "UPDATE repo_dispositions SET entity_slug = ? WHERE github_url = ?",
                (slug, row["github_url"]),
            )
        if conn.is_postgres:
            conn.execute("ALTER TABLE repo_dispositions DROP CONSTRAINT IF EXISTS repo_dispositions_pkey")
            conn.execute("ALTER TABLE repo_dispositions ADD PRIMARY KEY (entity_type, entity_slug)")
        else:
            tmp = "repo_dispositions__entity_key_migration"
            conn.execute(f"DROP TABLE IF EXISTS {tmp}")
            conn.execute(f"""
                CREATE TABLE {tmp} (
                    entity_type  TEXT NOT NULL DEFAULT 'repo',
                    entity_slug  TEXT NOT NULL,
                    github_url   TEXT DEFAULT '',
                    disposition  TEXT NOT NULL DEFAULT 'undecided',
                    reason       TEXT DEFAULT '',
                    decided_by   TEXT DEFAULT '',
                    decided_at   TEXT NOT NULL,
                    project_slug TEXT DEFAULT '',
                    PRIMARY KEY (entity_type, entity_slug)
                )
            """)
            conn.execute(
                f"INSERT INTO {tmp} (entity_type, entity_slug, github_url, disposition, "
                "reason, decided_by, decided_at, project_slug) "
                "SELECT entity_type, entity_slug, github_url, disposition, reason, "
                "decided_by, decided_at, project_slug FROM repo_dispositions"
            )
            conn.execute("DROP TABLE repo_dispositions")
            conn.execute(f"ALTER TABLE {tmp} RENAME TO repo_dispositions")
        log.info("registry: widened repo_dispositions primary key to (entity_type, entity_slug)")

    def _migrate_repo_disposition_history_to_entity_key(self, conn) -> None:
        """Backfill `entity_type`/`entity_slug` on `repo_disposition_history`
        — the append-only companion to `repo_dispositions` above. No PK
        change needed here (it's keyed on its own `id`, never on
        `github_url`), so this is a plain backfill, not a rebuild.

        Prefers the slug `repo_dispositions` already resolved for a given
        `github_url` (keeps history consistent with the current-state row
        for the same repo); falls back to the same url-derived slug for a
        `github_url` that only ever appears in history (its current-state
        row was since deleted, or somehow never existed)."""
        existing = self._get_table_columns(conn, "repo_disposition_history")
        if not existing or "entity_slug" in existing:
            return
        from resource_explorer.github.org_importer import _url_to_slug

        conn.execute("ALTER TABLE repo_disposition_history ADD COLUMN entity_type TEXT NOT NULL DEFAULT 'repo'")
        conn.execute("ALTER TABLE repo_disposition_history ADD COLUMN entity_slug TEXT NOT NULL DEFAULT ''")
        current = {
            r["github_url"]: r["entity_slug"]
            for r in conn.execute("SELECT github_url, entity_slug FROM repo_dispositions").fetchall()
        }
        rows = conn.execute("SELECT id, github_url FROM repo_disposition_history").fetchall()
        for row in rows:
            slug = current.get(row["github_url"]) or _url_to_slug(row["github_url"])
            conn.execute(
                "UPDATE repo_disposition_history SET entity_slug = ? WHERE id = ?",
                (slug, row["id"]),
            )
        log.info("registry: backfilled repo_disposition_history.entity_type/entity_slug")

    def _search_path_schema(self) -> str:
        """Schema named by the connection URL's `options=-csearch_path=<name>`
        query parameter; `resource_explorer` when the URL does not pin one."""
        from urllib.parse import parse_qs, unquote, urlsplit
        opts = parse_qs(urlsplit(self.database_url).query).get("options", [])
        for opt in opts:
            for part in unquote(opt).split():
                if part.startswith("-csearch_path="):
                    name = part.split("=", 1)[1].split(",")[0].strip().strip('"')
                    if name and name.replace("_", "").isalnum():
                        return name
        return "resource_explorer"

    def _init_schema(self) -> None:
        with self._conn() as conn:
            if self.database_url.startswith("postgresql"):
                # A fresh database has no `resource_explorer` schema, and the
                # connection URL pins search_path to it, so every CREATE TABLE
                # below fails with "no schema has been selected to create in"
                # (found on trevor's first deployment, 2026-09-04). The vector
                # store already guards its schema the same way (trellis-vectorstore
                # pg.py); the registry now does too. The schema name is whatever
                # search_path resolves to for this connection.
                conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{self._search_path_schema()}"')
            conn.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    slug TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    github_url TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    homepage_url TEXT DEFAULT '',
                    docs_url TEXT DEFAULT '',
                    github_token_encrypted TEXT DEFAULT '',
                    collections TEXT DEFAULT '[]',
                    status TEXT DEFAULT 'active',
                    last_indexed_at TEXT DEFAULT '',
                    last_stats_fetched_at TEXT DEFAULT '',
                    last_commit_sha TEXT DEFAULT '',
                    last_surveyed_at TEXT DEFAULT '',
                    last_profiled_at TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    error_message TEXT DEFAULT '',
                    subproject_path TEXT DEFAULT '',
                    parent_slug TEXT DEFAULT '',
                    extra_docs_paths TEXT DEFAULT '[]',
                    egeria_asset_guid TEXT DEFAULT NULL,
                    governance_state TEXT DEFAULT 'certified',
                    group_slug TEXT DEFAULT ''
                )
            """)
            # Migrations: add new columns to existing databases
            existing = self._get_table_columns(conn, "projects")
            for col, defn in [
                ("last_commit_sha", "TEXT DEFAULT ''"),
                ("last_surveyed_at", "TEXT DEFAULT ''"),
                ("last_profiled_at", "TEXT DEFAULT ''"),
                ("subproject_path", "TEXT DEFAULT ''"),
                ("parent_slug", "TEXT DEFAULT ''"),
                ("extra_docs_paths", "TEXT DEFAULT '[]'"),
                ("egeria_asset_guid", "TEXT DEFAULT NULL"),
                ("governance_state", "TEXT DEFAULT 'certified'"),
                ("group_slug", "TEXT DEFAULT ''"),
            ]:
                if col not in existing:
                    conn.execute(f"ALTER TABLE projects ADD COLUMN {col} {defn}")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS project_groups (
                    slug TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS project_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_slug TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    stars INTEGER,
                    forks INTEGER,
                    watchers INTEGER,
                    open_issues INTEGER,
                    contributors_count INTEGER,
                    commits_30d INTEGER,
                    commits_90d INTEGER,
                    releases_count INTEGER,
                    latest_release TEXT,
                    latest_release_at TEXT,
                    avg_release_interval_days INTEGER,
                    lines_of_code INTEGER,
                    file_count INTEGER,
                    repo_size_kb INTEGER,
                    primary_language TEXT,
                    language_breakdown TEXT DEFAULT '{}',
                    license TEXT DEFAULT '',
                    topics TEXT DEFAULT '',
                    repo_created_at TEXT DEFAULT '',
                    last_pushed_at TEXT DEFAULT '',
                    FOREIGN KEY (project_slug) REFERENCES projects(slug)
                )
            """)
            # Migrations: add new columns to existing databases
            existing_stats = self._get_table_columns(conn, "project_stats")
            for col, defn in [
                ("avg_release_interval_days", "INTEGER DEFAULT 0"),
                ("repo_size_kb", "INTEGER DEFAULT 0"),
                ("license", "TEXT DEFAULT ''"),
                # SPDX identifier (e.g. "MIT", "GPL-3.0", "Apache-2.0") — from
                # the same repo.get_license() call `license` (the human-
                # readable name) already comes from, so this is genuinely
                # free (no new API call). Added for LicenseClassifierSurveyor
                # (Assessment expansion plan B1) — a stable, well-known id is
                # far more reliable to classify against than free-text name
                # matching (e.g. "GNU General Public License v3.0" vs "GPL").
                ("license_spdx_id", "TEXT DEFAULT ''"),
                ("topics", "TEXT DEFAULT ''"),
                ("repo_created_at", "TEXT DEFAULT ''"),
                ("last_pushed_at", "TEXT DEFAULT ''"),
                ("ingestion_file_count", "INTEGER DEFAULT NULL"),
                ("ingestion_lines_of_code", "INTEGER DEFAULT NULL"),
                ("commits_365d", "INTEGER DEFAULT NULL"),
                # Lifecycle/repo-config flags — all free (already on the same
                # `repo` object StatsFetcher already has, zero extra API
                # calls), previously fetched but never persisted. Not all
                # are surfaced in Scouting's UI today, but kept for future
                # calculated attributes (per 2026-08-10 decision) — e.g. an
                # "actually still maintained" score could weight archived/
                # disabled/is_fork without a schema change later.
                ("archived", "INTEGER DEFAULT 0"),
                ("disabled", "INTEGER DEFAULT 0"),
                ("is_fork", "INTEGER DEFAULT 0"),
                ("is_template", "INTEGER DEFAULT 0"),
                ("default_branch", "TEXT DEFAULT ''"),
                ("has_issues", "INTEGER DEFAULT 1"),
                ("has_wiki", "INTEGER DEFAULT 1"),
                ("has_discussions", "INTEGER DEFAULT 0"),
                ("has_projects", "INTEGER DEFAULT 1"),
                ("has_pages", "INTEGER DEFAULT 0"),
                ("network_count", "INTEGER DEFAULT 0"),
                ("subscribers_count", "INTEGER DEFAULT 0"),
                ("visibility", "TEXT DEFAULT 'public'"),
                ("is_private", "INTEGER DEFAULT 0"),
                ("homepage", "TEXT DEFAULT ''"),
                ("mirror_url", "TEXT DEFAULT ''"),
                ("parent_full_name", "TEXT DEFAULT ''"),  # fork source, e.g. "torvalds/linux" — "" if not a fork
                ("allow_merge_commit", "INTEGER DEFAULT 1"),
                ("allow_squash_merge", "INTEGER DEFAULT 1"),
                ("allow_rebase_merge", "INTEGER DEFAULT 1"),
                ("allow_auto_merge", "INTEGER DEFAULT 0"),
                ("allow_update_branch", "INTEGER DEFAULT 0"),
                ("delete_branch_on_merge", "INTEGER DEFAULT 0"),
                # security_and_analysis: 7 boolean-ish feature toggles
                # (secret scanning, push protection, Dependabot updates,
                # etc.) — GitHub's own reported *configuration* state, not
                # findings. Stored as one JSON blob (mirrors
                # language_breakdown's existing convention) rather than 7
                # more columns, since it's always read/written as a unit.
                ("security_and_analysis_json", "TEXT DEFAULT '{}'"),
                # Deployments/environments — each its own extra API call
                # (get_environments()/get_deployments()), unlike everything
                # above. environments_json is a JSON list of environment
                # names; the rest describe only the single most recent
                # deployment, not full history (that's what the *_json
                # trend tables are for elsewhere in this codebase, and
                # deployment history isn't needed for a scouting-tier read).
                ("environments_json", "TEXT DEFAULT '[]'"),
                ("deployments_count", "INTEGER DEFAULT 0"),
                ("latest_deployment_at", "TEXT DEFAULT ''"),
                ("latest_deployment_environment", "TEXT DEFAULT ''"),
                ("latest_deployment_ref", "TEXT DEFAULT ''"),
            ]:
                if col not in existing_stats:
                    conn.execute(f"ALTER TABLE project_stats ADD COLUMN {col} {defn}")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS project_commits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_slug TEXT NOT NULL,
                    sha TEXT NOT NULL,
                    message TEXT DEFAULT '',
                    author_name TEXT DEFAULT '',
                    author_email TEXT DEFAULT '',
                    committed_at TEXT NOT NULL,
                    UNIQUE(project_slug, sha),
                    FOREIGN KEY (project_slug) REFERENCES projects(slug)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS project_code_symbols (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_slug   TEXT NOT NULL,
                    file_path      TEXT NOT NULL,
                    language       TEXT NOT NULL,
                    kind           TEXT NOT NULL,
                    name           TEXT NOT NULL,
                    qualified_name TEXT NOT NULL,
                    signature      TEXT DEFAULT '',
                    docstring      TEXT DEFAULT '',
                    summary        TEXT DEFAULT '',
                    start_line     INTEGER DEFAULT 0,
                    end_line       INTEGER DEFAULT 0,
                    parent_class   TEXT DEFAULT '',
                    return_type    TEXT DEFAULT '',
                    is_private     INTEGER DEFAULT 0,
                    is_async       INTEGER DEFAULT 0,
                    complexity     INTEGER DEFAULT 0,
                    UNIQUE(project_slug, file_path, qualified_name),
                    FOREIGN KEY (project_slug) REFERENCES projects(slug)
                )
            """)
            # Migrations: add new columns to existing databases (AST-ownership-
            # transfer plan Phase 3 — these five didn't exist before)
            existing_symbol_cols = self._get_table_columns(conn, "project_code_symbols")
            for col, defn in [
                ("parent_class", "TEXT DEFAULT ''"),
                ("return_type", "TEXT DEFAULT ''"),
                ("is_private", "INTEGER DEFAULT 0"),
                ("is_async", "INTEGER DEFAULT 0"),
                ("complexity", "INTEGER DEFAULT 0"),
            ]:
                if col not in existing_symbol_cols:
                    conn.execute(f"ALTER TABLE project_code_symbols ADD COLUMN {col} {defn}")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_symbols_slug_kind "
                "ON project_code_symbols(project_slug, kind)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_symbols_name "
                "ON project_code_symbols(project_slug, name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_symbols_parent_class "
                "ON project_code_symbols(project_slug, parent_class)"
            )
            # project_code_markers — DESIGN-INTERFACE-SURFACE-IMPLEMENTED-
            # RUNG.md §3.1/Decisions §2. A separate table from
            # project_code_symbols, not columns on it: that table's key is
            # UNIQUE(project_slug, file_path, qualified_name), one row per
            # symbol, and a single handler can carry several registrations
            # (`@app.get(...)` and `@app.post(...)` on one function is
            # ordinary FastAPI) — columns would force a lossy flattening on
            # day one. A marker is a fact ABOUT a symbol, the same
            # relationship project_code_relationships already models with
            # its own table.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS project_code_markers (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_slug   TEXT NOT NULL,
                    file_path      TEXT NOT NULL,
                    start_line     INTEGER DEFAULT 0,
                    language       TEXT NOT NULL,
                    marker_kind    TEXT NOT NULL,
                    framework      TEXT DEFAULT '',
                    interface_kind TEXT NOT NULL,
                    detail         TEXT DEFAULT '',
                    qualified_name TEXT DEFAULT '',
                    FOREIGN KEY (project_slug) REFERENCES projects(slug)
                )
            """)
            # (project_slug, interface_kind) — how interface_surface reads;
            # (project_slug, qualified_name) — the join back to
            # project_code_symbols.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_markers_slug_interface_kind "
                "ON project_code_markers(project_slug, interface_kind)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_markers_slug_qualified_name "
                "ON project_code_markers(project_slug, qualified_name)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS project_code_relationships (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_slug      TEXT NOT NULL,
                    relationship_type TEXT NOT NULL DEFAULT 'inherits_from',
                    source_name       TEXT NOT NULL,
                    target_name       TEXT NOT NULL,
                    UNIQUE(project_slug, relationship_type, source_name, target_name),
                    FOREIGN KEY (project_slug) REFERENCES projects(slug)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_relationships_source "
                "ON project_code_relationships(project_slug, source_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_relationships_target "
                "ON project_code_relationships(project_slug, target_name)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS project_aliases (
                    alias        TEXT PRIMARY KEY,
                    project_slug TEXT NOT NULL,
                    confirmed_by TEXT DEFAULT 'user',
                    created_at   TEXT NOT NULL,
                    FOREIGN KEY (project_slug) REFERENCES projects(slug)
                )
            """)
            # Migrations: add additions / deletions to existing project_commits rows
            existing_commits = self._get_table_columns(conn, "project_commits")
            for col, defn in [
                ("additions", "INTEGER DEFAULT NULL"),
                ("deletions", "INTEGER DEFAULT NULL"),
            ]:
                if col not in existing_commits:
                    conn.execute(f"ALTER TABLE project_commits ADD COLUMN {col} {defn}")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS project_contributor_stats (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_slug  TEXT NOT NULL,
                    period_start  TEXT NOT NULL,
                    period_end    TEXT NOT NULL,
                    author_email  TEXT NOT NULL,
                    author_name   TEXT NOT NULL,
                    commits       INTEGER DEFAULT 0,
                    additions     INTEGER DEFAULT 0,
                    deletions     INTEGER DEFAULT 0,
                    tier          TEXT DEFAULT '',
                    UNIQUE(project_slug, period_start, period_end, author_email),
                    FOREIGN KEY (project_slug) REFERENCES projects(slug)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_contributor_stats_slug "
                "ON project_contributor_stats(project_slug, period_start)"
            )
            # `user_id` (2026-09-04, plan §4 — "RE's conversation memory, keyed
            # by session_id with no owner, gains user_id the same way"). The
            # plan's isolation matrix is explicit that history is read only by
            # its owner while RAG retrieval stays shared, because the corpus is
            # shared by design and a person's own questions are not.
            #
            # No primary-key change needed here: the key is a surrogate id, so
            # this is a plain ADD COLUMN.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    turn_idx INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    project_slug TEXT DEFAULT '',
                    user_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
            """)
            existing_ch = self._get_table_columns(conn, "conversation_history")
            if "user_id" not in existing_ch:
                conn.execute(
                    "ALTER TABLE conversation_history ADD COLUMN user_id TEXT NOT NULL DEFAULT ''"
                )
            # compile_id links a turn to the context_compiles row that shaped
            # the answer (2026-09-08, context-compilation-design.md §13). NULL
            # means "no compile behind this turn" — the RAG fallback path, an
            # EXAMPLES intent, or a turn written before the column existed.
            # Those three are the same absence for every consumer of the link
            # (there is no compile to join to), so one NULL is honest here where
            # a sentinel string would invent a distinction nothing can recover.
            if "compile_id" not in existing_ch:
                conn.execute(
                    "ALTER TABLE conversation_history ADD COLUMN compile_id TEXT DEFAULT NULL"
                )
            # Every compile the apps run, keyed by a content hash of its inputs
            # (spec, version, budget, target model, resolved candidates), so the
            # same inputs land on the same row and `hits` counts the repeats —
            # which doubles as the replayability measurement the design asks
            # for (§9): a compile_id that repeats is a compile that replayed.
            # manifest/derivation are stored verbatim as JSON; feedback and
            # conversation turns point here rather than carrying copies.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS context_compiles (
                    compile_id    TEXT PRIMARY KEY,
                    project_slug  TEXT NOT NULL,
                    question      TEXT NOT NULL DEFAULT '',
                    spec_id       TEXT NOT NULL DEFAULT '',
                    budget        INTEGER NOT NULL DEFAULT 0,
                    used          INTEGER NOT NULL DEFAULT 0,
                    manifest      TEXT NOT NULL,
                    derivation    TEXT NOT NULL,
                    user_id       TEXT NOT NULL DEFAULT '',
                    session_id    TEXT NOT NULL DEFAULT '',
                    hits          INTEGER NOT NULL DEFAULT 1,
                    created_at    TEXT NOT NULL,
                    last_seen_at  TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_context_compiles_slug "
                "ON context_compiles(project_slug, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conv_session "
                "ON conversation_history(session_id, turn_idx)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conv_user "
                "ON conversation_history(user_id, session_id)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS project_dependencies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_slug TEXT NOT NULL,
                    dep_name TEXT NOT NULL,
                    dep_version TEXT DEFAULT '',
                    dep_type TEXT DEFAULT 'runtime',
                    ecosystem TEXT DEFAULT '',
                    source_file TEXT DEFAULT '',
                    indexed_at TEXT NOT NULL,
                    FOREIGN KEY (project_slug) REFERENCES projects(slug)
                )
            """)
            # Migration: add dep_version_source if not present (existing databases).
            #
            # This is the provenance for `dep_version`, added 2026-09-01 alongside
            # DependencyParser's Gradle version resolution — see
            # ingestion/dependency_parser.py's `_dep()` docstring for the full
            # rationale. Vocabulary:
            #   "declared"               — the manifest wrote this version at the
            #                              dependency's own declaration site.
            #   "variable_interpolation" — substituted from a same-file variable
            #                              (Gradle `ext { xVersion = '...' }`).
            #   "version_catalog"        — substituted from `gradle/libs.versions.toml`.
            #   ""                       — either no version was recorded at all
            #                              (dep_version is also '') or, for rows
            #                              written before this column existed,
            #                              unknown provenance for a version that
            #                              IS present. Never treat '' + a non-empty
            #                              dep_version as "declared" — that is
            #                              exactly the substituted-reused-as-
            #                              declared confusion this column exists
            #                              to prevent. A backfill would need to
            #                              re-run the parser, not guess.
            existing_deps = self._get_table_columns(conn, "project_dependencies")
            if "dep_version_source" not in existing_deps:
                conn.execute(
                    "ALTER TABLE project_dependencies ADD COLUMN dep_version_source TEXT DEFAULT ''"
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_deps_slug "
                "ON project_dependencies(project_slug)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_deps_name "
                "ON project_dependencies(dep_name)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS project_file_type_counts (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_slug TEXT NOT NULL,
                    surveyed_at  TEXT NOT NULL,
                    type_label   TEXT NOT NULL,
                    file_count   INTEGER NOT NULL,
                    source       TEXT DEFAULT 'extension',
                    details_json TEXT DEFAULT NULL,
                    FOREIGN KEY (project_slug) REFERENCES projects(slug)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_file_type_slug "
                "ON project_file_type_counts(project_slug)"
            )
            # Migration: add details_json if not present (existing databases)
            existing_ftc = self._get_table_columns(conn, "project_file_type_counts")
            if "details_json" not in existing_ftc:
                conn.execute(
                    "ALTER TABLE project_file_type_counts ADD COLUMN details_json TEXT DEFAULT NULL"
                )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS project_file_inventory (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_slug     TEXT NOT NULL,
                    file_path        TEXT NOT NULL,
                    file_size_bytes  INTEGER DEFAULT 0,
                    indexed_at       TEXT NOT NULL,
                    UNIQUE(project_slug, file_path),
                    FOREIGN KEY (project_slug) REFERENCES projects(slug)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_file_inventory_slug "
                "ON project_file_inventory(project_slug)"
            )
            # file_mode: the git tree entry's mode bits ("100644" regular,
            # "100755" executable, "120000" symlink) — free data already in
            # the get_git_tree() response this table's writer already calls,
            # just never captured before (Assessment sub-resource cataloging
            # plan, D9 Tier 1). Default '' (unknown/never refreshed since
            # this column existed), not a fabricated mode.
            existing_fi = self._get_table_columns(conn, "project_file_inventory")
            if "file_mode" not in existing_fi:
                conn.execute(
                    "ALTER TABLE project_file_inventory ADD COLUMN file_mode TEXT DEFAULT ''"
                )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS project_egeria_surveys (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_slug       TEXT NOT NULL,
                    surveyed_at        TEXT NOT NULL,
                    egeria_report_guid TEXT NOT NULL,
                    published_at       TEXT NOT NULL,
                    annotation_count   INTEGER DEFAULT NULL,
                    UNIQUE(project_slug, surveyed_at),
                    FOREIGN KEY (project_slug) REFERENCES projects(slug)
                )
            """)
            # Migration: add annotation_count if not present (existing databases)
            existing_es = self._get_table_columns(conn, "project_egeria_surveys")
            if "annotation_count" not in existing_es:
                conn.execute(
                    "ALTER TABLE project_egeria_surveys ADD COLUMN annotation_count INTEGER DEFAULT NULL"
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_egeria_surveys_slug "
                "ON project_egeria_surveys(project_slug)"
            )
            # Per-annotation-type publish history (2026-08-24 — direct
            # feedback: the Survey Results dashboards had no publish/status
            # signal per card at all, and "we should already have all of
            # that information if it's been published to Egeria" is right —
            # EgeriaPublisher already knows exactly which annotation_types
            # went into each publish, in-memory, at the moment it happens.
            # This just persists that instead of discarding it. One row per
            # distinct annotation_type per publish (not one row per
            # individual annotation instance — a repo can emit dozens of
            # QualityScoreAnnotation rows in one survey, only "was this
            # *type* published, and when" matters for the dashboard badge).
            # analysis_catalog.yaml's own `annotation_types` field per
            # analysis_id is the join key back to a specific Results
            # dashboard — see get_last_published_annotation_types() and
            # GET /api/projects/{slug}/survey-results's use of it.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS project_published_annotation_types (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_slug     TEXT NOT NULL,
                    annotation_type  TEXT NOT NULL,
                    published_at     TEXT NOT NULL,
                    egeria_report_guid TEXT NOT NULL DEFAULT ''
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_published_annotation_types_slug "
                "ON project_published_annotation_types(project_slug, annotation_type)"
            )
            # Migration 2026-09-11: two AnnotationType values were shortened
            # forms of the Egeria type name ("SchemaAnalysis",
            # "RequestForAction") while the catalog declared the full names.
            # Rows written under the short names are renamed so the
            # declared-vs-received join reads them.
            #
            # Guarded by a read, and placed AFTER the index. The first version
            # ran the UPDATE unconditionally, before CREATE INDEX IF NOT
            # EXISTS, and deadlocked every process that opened the registry
            # concurrently: an UPDATE takes RowExclusiveLock even when it
            # matches no rows, CREATE INDEX wants ShareLock, and two openers
            # each held the first while waiting for the second. The answer
            # layer opens one registry per question, sixteen at a time, so
            # the Dashboard by question hit it on every load. Now a process
            # that finds nothing to rename takes no write lock at all, which
            # is every process after the first.
            for short, full in (
                ("SchemaAnalysis", "SchemaAnalysisAnnotation"),
                ("RequestForAction", "RequestForActionAnnotation"),
            ):
                stale = conn.execute(
                    "SELECT 1 FROM project_published_annotation_types WHERE annotation_type = ? LIMIT 1",
                    (short,),
                ).fetchone()
                if stale:
                    conn.execute(
                        "UPDATE project_published_annotation_types SET annotation_type = ? "
                        "WHERE annotation_type = ?",
                        (full, short),
                    )
            # Which ANALYSES a publish covered, recorded directly.
            #
            # project_published_annotation_types above answers the same
            # question by inference — join a publish's annotation types against
            # each analysis's declared ones — and that only worked while every
            # type had exactly one producer. It stopped being true the moment a
            # second analysis produced a QualityScoreAnnotation, after which NO
            # analysis could earn an attributable publish and every card showed
            # the hedged "Repo published". The inference was always going to
            # break that way; nothing prevented a second producer.
            #
            # The publishing side knows the answer outright — SurveyOrchestrator
            # records the step keys it ran on SurveyResult.steps_run — so this
            # stores it instead of re-deriving it afterwards.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS project_published_analyses (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_slug       TEXT NOT NULL,
                    analysis_id        TEXT NOT NULL,
                    published_at       TEXT NOT NULL,
                    egeria_report_guid TEXT NOT NULL DEFAULT ''
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_published_analyses_slug "
                "ON project_published_analyses(project_slug, analysis_id)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS project_data_profiles (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_slug     TEXT NOT NULL,
                    file_path        TEXT NOT NULL,
                    profiled_at      TEXT NOT NULL,
                    format           TEXT NOT NULL,
                    row_count        INTEGER DEFAULT NULL,
                    col_count        INTEGER DEFAULT NULL,
                    schema_json      TEXT DEFAULT NULL,
                    null_summary     TEXT DEFAULT '',
                    file_size_bytes  INTEGER DEFAULT 0,
                    UNIQUE(project_slug, file_path),
                    FOREIGN KEY (project_slug) REFERENCES projects(slug)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_data_profiles_slug "
                "ON project_data_profiles(project_slug)"
            )
            # Analysis results reduce to exactly two real shapes — "list of
            # typed findings" and "aggregate snapshot metric(s)" — so every
            # analysis kind registers into ONE of these two generic tables
            # via a `kind` discriminator instead of getting its own bespoke
            # table. Four per-kind tables (project_security_findings,
            # project_documentation_findings, project_data_profile_snapshots,
            # project_api_structure_snapshots) preceded them; their rows were
            # migrated forward on 2026-08-08 and the tables were dropped on
            # 2026-08-31 after a soak period.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS project_analysis_findings (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_slug TEXT NOT NULL,
                    kind         TEXT NOT NULL,
                    surveyed_at  TEXT NOT NULL,
                    check_name   TEXT NOT NULL,
                    label        TEXT NOT NULL,
                    summary      TEXT DEFAULT '',
                    confidence   INTEGER DEFAULT 100,
                    detail_json  TEXT DEFAULT NULL,
                    scope_locator TEXT DEFAULT '',
                    FOREIGN KEY (project_slug) REFERENCES projects(slug)
                )
            """)
            # Migration: add scope_locator to project_analysis_findings for
            # DBs created before the repo scope-narrowing funnel plan
            # (docs/repo-scope-narrowing-funnel.md), D5/D6 — '' = whole-
            # resource, matching every pre-existing row unchanged.
            # Migration (2026-09-11): the inventory records whether a file is
            # the repository's own or somebody else's library checked in.
            # DEFAULT 0 = own, matching every pre-existing row until its
            # project is re-indexed; readers exclude vendored rows by default.
            inv_cols = self._get_table_columns(conn, "project_file_inventory")
            if "vendored" not in inv_cols:
                conn.execute("ALTER TABLE project_file_inventory ADD COLUMN vendored INTEGER DEFAULT 0")
            existing_findings_cols = self._get_table_columns(conn, "project_analysis_findings")
            if "scope_locator" not in existing_findings_cols:
                conn.execute(
                    "ALTER TABLE project_analysis_findings ADD COLUMN scope_locator TEXT DEFAULT ''"
                )
            # Migration: add egeria_annotation_guid for
            # docs/annotation-linking-plan.md Phase 1 — the Egeria Annotation
            # GUID this finding row was published as, when known.
            #
            # DEFAULT NULL, not '': every pre-existing row (~68,000 measured
            # 2026-08-31) gets NULL, same as a brand-new row that has not been
            # published yet — both genuinely mean "no known GUID" (plan Q3)
            # and this migration does not claim to know more than that. NULL
            # is used rather than '' specifically so this column never writes
            # an empty string as if it were a real (if empty) capture attempt
            # — see mark_finding_guid's docstring for why a failed publish
            # must not look like a completed one here. This mirrors the
            # existing `projects.egeria_asset_guid TEXT DEFAULT NULL` column
            # above (line ~486), not the `TEXT DEFAULT ''` convention used for
            # scope_locator, deliberately: scope_locator's '' is itself a
            # meaningful value ("whole resource"); this column has no such
            # meaningful empty state.
            if "egeria_annotation_guid" not in existing_findings_cols:
                conn.execute(
                    "ALTER TABLE project_analysis_findings "
                    "ADD COLUMN egeria_annotation_guid TEXT DEFAULT NULL"
                )
            # Migration: add superseded_at — "a run that finds nothing
            # retires the previous run's findings" (docs/Backlog.md,
            # 2026-09-13 cve_scan defect). NULL = still current, matching
            # every pre-existing row (nothing was ever superseded before
            # this column existed). Set only by upsert_finding() when a
            # caller opts in with supersedes_previous=True; see that
            # method's docstring. query_findings() (the "current answer"
            # reader) excludes rows where this is set; query_findings_
            # history_raw() and query_findings_all_runs() do not — a
            # superseded row is never deleted, only stopped being served
            # as current.
            if "superseded_at" not in existing_findings_cols:
                conn.execute(
                    "ALTER TABLE project_analysis_findings "
                    "ADD COLUMN superseded_at TEXT DEFAULT NULL"
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_analysis_findings_slug_kind "
                "ON project_analysis_findings(project_slug, kind)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_analysis_findings_scope "
                "ON project_analysis_findings(project_slug, kind, scope_locator)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS project_analysis_metrics (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_slug TEXT NOT NULL,
                    kind         TEXT NOT NULL,
                    surveyed_at  TEXT NOT NULL,
                    metric_name  TEXT NOT NULL,
                    metric_value REAL NOT NULL,
                    detail_json  TEXT DEFAULT NULL,
                    scope_locator TEXT DEFAULT '',
                    FOREIGN KEY (project_slug) REFERENCES projects(slug)
                )
            """)
            existing_metrics_cols = self._get_table_columns(conn, "project_analysis_metrics")
            if "scope_locator" not in existing_metrics_cols:
                conn.execute(
                    "ALTER TABLE project_analysis_metrics ADD COLUMN scope_locator TEXT DEFAULT ''"
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_analysis_metrics_slug_kind "
                "ON project_analysis_metrics(project_slug, kind, metric_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_analysis_metrics_scope "
                "ON project_analysis_metrics(project_slug, kind, metric_name, scope_locator)"
            )
            # ── step_runs — one row per step execution (design §17.2) ──────
            #
            # What a step actually COST, as a vector, replacing the
            # `<step>_elapsed`/`<step>_connects` metrics scattered across
            # project_analysis_metrics rows. Those measured one axis, seconds,
            # which is the axis the funnel argument is not really about: a
            # step that waits on the network is cheap in CPU and slow in wall
            # time, and for repositories the scarce resource is the GitHub
            # rate budget rather than either.
            #
            # **No FOREIGN KEY on `slug`, deliberately.** Every other table
            # here keys to `projects(slug)`, and this one records runs for
            # repositories, databases AND filesystems — three different
            # parent tables. A FK to one of them would reject two thirds of
            # the rows; `entity_type` carries which one instead.
            #
            # **`metrics`/`declared` are TEXT holding JSON, not `jsonb`.**
            # §17.2 writes `jsonb`, and this registry runs on Postgres in
            # production and SQLite as a fallback (CLAUDE.md's tech-stack
            # table) with one DDL for both — `PostgresCursorWrapper.
            # _translate_sql` translates AUTOINCREMENT and GROUP_CONCAT and
            # has no jsonb story, and every other JSON column in this file is
            # TEXT (`detail_json`, `survey_data`, the `_json` suffix the
            # detail-table reader keys off). One backend-specific column type
            # here would mean two read paths — psycopg2 hands back a dict,
            # sqlite3 a string — for a table whose whole purpose is to be
            # queried uniformly. Recorded as a knowing deviation.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS step_runs (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug          TEXT NOT NULL,
                    entity_type   TEXT NOT NULL DEFAULT 'repo',
                    step_key      TEXT NOT NULL,
                    surveyed_at   TEXT NOT NULL,
                    source        TEXT NOT NULL DEFAULT 'local',
                    executor      TEXT NOT NULL DEFAULT 'local',
                    executor_ref  TEXT DEFAULT '',
                    demanded_by   TEXT DEFAULT '',
                    metrics       TEXT DEFAULT '{}',
                    declared      TEXT DEFAULT '{}',
                    disagreement  TEXT DEFAULT '',
                    surveyed_as   TEXT DEFAULT ''
                )
            """)
            # `entity_type` and `executor_ref` post-date the first shape this
            # table shipped in during development — the same seam
            # `_DB_FS_DETAIL_TABLE_MIGRATIONS` exists for, and for the same
            # reason: CREATE TABLE IF NOT EXISTS is a no-op against a table
            # that already exists in the shared registry.
            _step_run_cols = self._get_table_columns(conn, "step_runs")
            for _col, _ddl in (
                ("entity_type", "TEXT NOT NULL DEFAULT 'repo'"),
                ("executor_ref", "TEXT DEFAULT ''"),
                ("demanded_by", "TEXT DEFAULT ''"),
                # design REPLY-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md
                # §4 — which credential identity this run executed as;
                # `source`/`executor` already say WHO ran it.
                ("surveyed_as", "TEXT DEFAULT ''"),
            ):
                if _step_run_cols and _col not in _step_run_cols:
                    conn.execute(f"ALTER TABLE step_runs ADD COLUMN {_col} {_ddl}")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_step_runs_step "
                "ON step_runs(step_key, surveyed_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_step_runs_slug "
                "ON step_runs(slug, surveyed_at)"
            )
            # One-time repair for project_dependencies rows written before
            # this Phase B change: upsert_dependencies() used to compute
            # datetime.utcnow() inside its per-row list comprehension, so
            # every row in what was conceptually one ingest run got its own
            # microsecond-distinct indexed_at. That breaks
            # query_dependencies()'s new MAX(indexed_at) latest-batch filter
            # down to "1 row" for any repo with legacy data. Provably safe
            # to collapse: before this fix, upsert_dependencies() always did
            # DELETE-then-INSERT, so at any point in time every existing row
            # for one project_slug WAS genuinely one batch — not a guess.
            # Self-limiting: COUNT(DISTINCT indexed_at) == COUNT(*) is the
            # exact signature of "every row uniquely timestamped" (the bug);
            # once collapsed onto one shared timestamp that's no longer true
            # (unless the project only ever had 1 dependency — a harmless
            # no-op), so this never touches legitimate future multi-batch
            # history and is safe to run unconditionally on every startup.
            dep_groups = conn.execute(
                "SELECT project_slug, COUNT(*) as n, COUNT(DISTINCT indexed_at) as d, MAX(indexed_at) as max_ts "
                "FROM project_dependencies GROUP BY project_slug"
            ).fetchall()
            for g in dep_groups:
                if g["n"] > 1 and g["n"] == g["d"]:
                    conn.execute(
                        "UPDATE project_dependencies SET indexed_at = ? WHERE project_slug = ?",
                        (g["max_ts"], g["project_slug"]),
                    )
            # Database entities table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS databases (
                    slug TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    db_type TEXT NOT NULL,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    database_name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    connection_ref TEXT DEFAULT '',
                    status TEXT DEFAULT 'active',
                    registered_at TEXT NOT NULL,
                    last_surveyed_at TEXT DEFAULT '',
                    egeria_asset_guid TEXT DEFAULT '',
                    error_message TEXT DEFAULT '',
                    db_user TEXT DEFAULT '',
                    db_password TEXT DEFAULT '',
                    egeria_host TEXT DEFAULT '',
                    egeria_url TEXT DEFAULT '',
                    egeria_server TEXT DEFAULT '',
                    egeria_user TEXT DEFAULT '',
                    egeria_password TEXT DEFAULT '',
                    schema_count INTEGER DEFAULT 0,
                    table_count INTEGER DEFAULT 0,
                    column_count INTEGER DEFAULT 0,
                    governance_state TEXT DEFAULT 'certified',
                    group_slug TEXT DEFAULT ''
                )
            """)
            # Migration: add credential/egeria/survey-count columns to existing databases tables
            existing_db = self._get_table_columns(conn, "databases")
            for col, defval in [
                ("db_user", "''"),
                ("db_password", "''"),
                ("egeria_host", "''"),
                ("egeria_url", "''"),
                ("egeria_server", "''"),
                ("egeria_user", "''"),
                ("egeria_password", "''"),
                ("server_slug", "''"),
                ("schema_count", "0"),
                ("table_count", "0"),
                ("column_count", "0"),
                ("governance_state", "'certified'"),
                ("group_slug", "''"),
            ]:
                if col not in existing_db:
                    deftype = "INTEGER" if defval == "0" else "TEXT"
                    conn.execute(f"ALTER TABLE databases ADD COLUMN {col} {deftype} DEFAULT {defval}")
            # Database servers table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS db_servers (
                    slug TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    db_type TEXT NOT NULL DEFAULT 'postgresql',
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL DEFAULT 5432,
                    description TEXT DEFAULT '',
                    db_user TEXT DEFAULT '',
                    db_password TEXT DEFAULT '',
                    egeria_host TEXT DEFAULT '',
                    egeria_url TEXT DEFAULT '',
                    egeria_server TEXT DEFAULT '',
                    egeria_user TEXT DEFAULT '',
                    egeria_password TEXT DEFAULT '',
                    status TEXT DEFAULT 'active',
                    registered_at TEXT NOT NULL,
                    error_message TEXT DEFAULT '',
                    group_slug TEXT DEFAULT ''
                )
            """)
            # Migration: add group_slug to db_servers
            existing_srv = self._get_table_columns(conn, "db_servers")
            if "group_slug" not in existing_srv:
                conn.execute("ALTER TABLE db_servers ADD COLUMN group_slug TEXT DEFAULT ''")
            # Database survey results table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS database_surveys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    database_slug TEXT NOT NULL,
                    surveyed_at TEXT NOT NULL,
                    egeria_report_guid TEXT DEFAULT '',
                    schema_count INTEGER DEFAULT 0,
                    table_count INTEGER DEFAULT 0,
                    column_count INTEGER DEFAULT 0,
                    survey_data TEXT DEFAULT '{}',
                    source TEXT DEFAULT 'local',
                    surveyed_as TEXT DEFAULT '',
                    FOREIGN KEY (database_slug) REFERENCES databases(slug)
                )
            """)
            # Migration: add source column to existing database_surveys tables
            existing_ds = self._get_table_columns(conn, "database_surveys")
            if "source" not in existing_ds:
                conn.execute("ALTER TABLE database_surveys ADD COLUMN source TEXT DEFAULT 'local'")
            # Migration: add surveyed_as (design REPLY-DATABASE-CREDENTIAL-
            # CAPABILITY-VISIBILITY.md §4 — "the credential identity is
            # recorded on every survey row"; `source` already distinguishes
            # WHO ran it (egeria/resource-explorer/local), this is WHICH
            # CREDENTIAL it ran as).
            if "surveyed_as" not in existing_ds:
                conn.execute("ALTER TABLE database_surveys ADD COLUMN surveyed_as TEXT DEFAULT ''")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_database_surveys_slug "
                "ON database_surveys(database_slug)"
            )
            # File system entities table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS file_systems (
                    slug TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    local_mount_point TEXT NOT NULL,
                    canonical_mount_point TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    status TEXT DEFAULT 'active',
                    registered_at TEXT NOT NULL,
                    last_surveyed_at TEXT DEFAULT '',
                    egeria_asset_guid TEXT DEFAULT '',
                    error_message TEXT DEFAULT '',
                    egeria_url TEXT DEFAULT '',
                    egeria_server TEXT DEFAULT '',
                    egeria_user TEXT DEFAULT '',
                    egeria_password TEXT DEFAULT '',
                    file_count INTEGER DEFAULT 0,
                    data_file_count INTEGER DEFAULT 0,
                    governance_state TEXT DEFAULT 'certified',
                    group_slug TEXT DEFAULT ''
                )
            """)
            # Migration: add new columns to existing file_systems tables
            existing_fs = self._get_table_columns(conn, "file_systems")
            for col, defval in [
                ("egeria_url", "''"),
                ("egeria_server", "''"),
                ("egeria_user", "''"),
                ("egeria_password", "''"),
                ("file_count", "0"),
                ("data_file_count", "0"),
                ("governance_state", "'certified'"),
                ("group_slug", "''"),
            ]:
                if col not in existing_fs:
                    deftype = "INTEGER" if defval == "0" else "TEXT"
                    conn.execute(f"ALTER TABLE file_systems ADD COLUMN {col} {deftype} DEFAULT {defval}")

            # File system survey results table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS filesystem_surveys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filesystem_slug TEXT NOT NULL,
                    surveyed_at TEXT NOT NULL,
                    egeria_report_guid TEXT DEFAULT '',
                    file_count INTEGER DEFAULT 0,
                    data_file_count INTEGER DEFAULT 0,
                    total_size_bytes INTEGER DEFAULT 0,
                    survey_data TEXT DEFAULT '{}',
                    source TEXT DEFAULT 'local',
                    FOREIGN KEY (filesystem_slug) REFERENCES file_systems(slug)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_filesystem_surveys_slug "
                "ON filesystem_surveys(filesystem_slug)"
            )

            # ── Structured DB/FS detail tables ──────────────────────────────
            #
            # multi-resource-questions-design.md §5.7 (database) and §6 (file
            # system). Until these existed, every per-object question ("which
            # tables have no primary key?"), every diff and every change
            # detector had to re-parse the `survey_data` JSON blob — which
            # `web/routes/databases.py`'s get_database_diff did literally.
            # The blob stays as the raw record; these are the queryable form.
            #
            # Every row is keyed by (slug, surveyed_at, source) per the design
            # doc's rule D: RE keeps a local copy of every result whoever ran
            # the survey, so a native Egeria run and an RE-local run of the
            # same day must not overwrite each other. `source` is 'local' for
            # RE's own surveyors (matching database_surveys.source's existing
            # default) and 'egeria' for rows materialised back out of a native
            # survey report by surveyors/result_materializer.py.
            #
            # `state` on every row, and the per-type coverage tables
            # below, exist because absence is a result. The Java connector
            # docs are explicit that MISSING schemas/tables/columns in a
            # native Postgres survey mean the survey userId lacks permission,
            # not that there are none; an empty pg_stats means ANALYZE never
            # ran, not that the column has no values. Those must never render
            # as "measured, and there was nothing" — see STATE_* below.
            for _ddl in _DB_FS_DETAIL_TABLE_DDL:
                conn.execute(_ddl)
            for _idx in _DB_FS_DETAIL_TABLE_INDEXES:
                conn.execute(_idx)
            # Migrations: these tables were added together, so a checkout that
            # already has them from an earlier point in this branch's life
            # gets any later-added column here rather than silently missing
            # it. Same (col, defn) shape as the projects/file_systems blocks.
            for _table, _cols in _DB_FS_DETAIL_TABLE_MIGRATIONS:
                _existing = self._get_table_columns(conn, _table)
                for _col, _defn in _cols:
                    if _col not in _existing:
                        conn.execute(f"ALTER TABLE {_table} ADD COLUMN {_col} {_defn}")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS activity_log (
                    id               TEXT PRIMARY KEY,
                    ts               TEXT NOT NULL,
                    operation        TEXT NOT NULL,
                    intent           TEXT NOT NULL,
                    entity_type      TEXT NOT NULL,
                    entity_slug      TEXT NOT NULL,
                    entity_name      TEXT DEFAULT '',
                    entity_location  TEXT DEFAULT '',
                    status           TEXT NOT NULL DEFAULT 'ok',
                    summary          TEXT DEFAULT '',
                    detail           TEXT DEFAULT '',
                    items_json       TEXT DEFAULT '[]',
                    annotations_json TEXT DEFAULT '[]'
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_activity_log_ts "
                "ON activity_log(ts DESC)"
            )
            # publish_run_id: the outbox run_id (the annotation qualified_name
            # prefix — see egeria_publisher.py's _create_annotations) a
            # background/deferred run's activity row was tagged with when it
            # reported `published: "queued"`. Lets
            # complete_publish_run_if_done() find, by exact match, the one
            # activity row to flip to `published: True` once every outbox row
            # of that run (and its `::links` companion) has landed — without
            # this, that lookup would have to scan every row's `detail` JSON.
            # Empty for every row that never went through the deferred path.
            existing_activity = self._get_table_columns(conn, "activity_log")
            if "publish_run_id" not in existing_activity:
                conn.execute("ALTER TABLE activity_log ADD COLUMN publish_run_id TEXT DEFAULT ''")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_activity_log_publish_run_id "
                "ON activity_log(publish_run_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_activity_log_entity "
                "ON activity_log(entity_slug, entity_type)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS resource_context (
                    entity_type  TEXT NOT NULL,
                    entity_slug  TEXT NOT NULL,
                    ts           TEXT NOT NULL,
                    context_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (entity_type, entity_slug)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS resource_tags (
                    entity_type  TEXT NOT NULL,
                    entity_slug  TEXT NOT NULL,
                    tag          TEXT NOT NULL,
                    created_at   TEXT NOT NULL,
                    PRIMARY KEY (entity_type, entity_slug, tag)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_resource_tags_tag ON resource_tags(tag)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS resource_feedback (
                    id           TEXT PRIMARY KEY,
                    entity_type  TEXT NOT NULL,
                    entity_slug  TEXT NOT NULL,
                    rating       INTEGER DEFAULT NULL,
                    category     TEXT DEFAULT '',
                    message      TEXT NOT NULL DEFAULT '',
                    created_at   TEXT NOT NULL
                )
            """)
            # See conversation_history.compile_id above for what NULL means.
            if "compile_id" not in self._get_table_columns(conn, "resource_feedback"):
                conn.execute(
                    "ALTER TABLE resource_feedback ADD COLUMN compile_id TEXT DEFAULT NULL"
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_resource_feedback_entity "
                "ON resource_feedback(entity_type, entity_slug)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS resource_curator_notes (
                    id           TEXT PRIMARY KEY,
                    entity_type  TEXT NOT NULL,
                    entity_slug  TEXT NOT NULL,
                    note         TEXT NOT NULL,
                    created_at   TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_resource_curator_notes_entity "
                "ON resource_curator_notes(entity_type, entity_slug)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS architecture_component_verdicts (
                    id             TEXT PRIMARY KEY,
                    entity_type    TEXT NOT NULL,
                    entity_slug    TEXT NOT NULL,
                    scope_locator  TEXT NOT NULL,
                    verdict        TEXT NOT NULL,   -- 'accepted' | 'rejected' | 'retyped'
                    retyped_to     TEXT NOT NULL DEFAULT '',
                    note           TEXT NOT NULL DEFAULT '',
                    -- Who recorded this verdict (2026-09-04, plan §4). Read by
                    -- workflows/curate.owner_of as the local mirror of the
                    -- element's Egeria `Ownership`, so an authorization
                    -- decision can still be made when Egeria is unreachable
                    -- and for a proposal that has not been materialized yet.
                    -- '' is the shared/legacy bucket: every verdict recorded
                    -- before this column existed, curatable by any signed-in
                    -- user (see may_curate).
                    decided_by     TEXT NOT NULL DEFAULT '',
                    created_at     TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_architecture_component_verdicts_scope "
                "ON architecture_component_verdicts(entity_type, entity_slug, scope_locator)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS architecture_materialized_components (
                    id             TEXT PRIMARY KEY,
                    entity_type    TEXT NOT NULL,
                    entity_slug    TEXT NOT NULL,
                    scope_locator  TEXT NOT NULL,
                    qualified_name TEXT NOT NULL,
                    guid           TEXT NOT NULL,
                    materialized_at TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_architecture_materialized_components_scope "
                "ON architecture_materialized_components(entity_type, entity_slug, scope_locator)"
            )
            # Migration: verdict_target — distinguishes a single-component
            # verdict row ('component', the only shape this table held until
            # now) from a blueprint verdict row ('blueprint'), added for
            # docs/blueprint-materialization-plan.md's Decision 3: a
            # blueprint verdict reuses this same table rather than a parallel
            # one, since get_component_verdicts/list_component_verdict_history/
            # the append-only-history convention/_ENTITY_SLUG_TABLES rename
            # wiring are all already correct for this shape too. For a
            # blueprint row, scope_locator is repurposed to hold
            # f"{perspective}::{cluster_name}" — a candidate_blueprint finding
            # has no scope_locator of its own (clustering.py's findings all
            # share scope_locator="", distinguished by label), so this reuses
            # the column rather than adding a second identity to keep in sync.
            # Same ALTER TABLE ... ADD COLUMN pattern as the last_run_status/
            # target_kind migrations on resource_schedules above.
            existing_verdicts = self._get_table_columns(conn, "architecture_component_verdicts")
            if "verdict_target" not in existing_verdicts:
                conn.execute(
                    "ALTER TABLE architecture_component_verdicts ADD COLUMN "
                    "verdict_target TEXT NOT NULL DEFAULT 'component'"
                )
            if "decided_by" not in existing_verdicts:
                conn.execute(
                    "ALTER TABLE architecture_component_verdicts ADD COLUMN "
                    "decided_by TEXT NOT NULL DEFAULT ''"
                )
            # New table: architecture_materialized_blueprints — parallel in
            # shape to architecture_materialized_components (Decision 4), but
            # keyed by (entity_type, entity_slug, perspective, cluster_name)
            # rather than scope_locator, since a blueprint has no scope_locator
            # of its own (same reason the verdict row above repurposes that
            # column instead of reusing it directly here).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS architecture_materialized_blueprints (
                    id              TEXT PRIMARY KEY,
                    entity_type     TEXT NOT NULL,
                    entity_slug     TEXT NOT NULL,
                    perspective     TEXT NOT NULL,
                    cluster_name    TEXT NOT NULL,
                    qualified_name  TEXT NOT NULL,
                    guid            TEXT NOT NULL,
                    materialized_at TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_architecture_materialized_blueprints_scope "
                "ON architecture_materialized_blueprints(entity_type, entity_slug, perspective, cluster_name)"
            )
            # New table: architecture_materialized_ports — same parallel shape
            # again, this time for PortMaterializer's temporary SolutionPort
            # workaround (Backlog.md item 8 / egeria-python ISSUE-85:
            # SolutionArchitect has no create_solution_port, confirmed live
            # against the actual OpenAPI spec — no create endpoint anywhere).
            # Keyed by (entity_type, entity_slug, scope_locator, port_name)
            # rather than scope_locator alone, since one component can
            # legitimately have more than one port (e.g. an HTTP port and a
            # metrics port on the same deployed service).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS architecture_materialized_ports (
                    id              TEXT PRIMARY KEY,
                    entity_type     TEXT NOT NULL,
                    entity_slug     TEXT NOT NULL,
                    scope_locator   TEXT NOT NULL,
                    port_name       TEXT NOT NULL,
                    qualified_name  TEXT NOT NULL,
                    guid            TEXT NOT NULL,
                    materialized_at TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_architecture_materialized_ports_scope "
                "ON architecture_materialized_ports(entity_type, entity_slug, scope_locator, port_name)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS resource_schedules (
                    entity_type  TEXT NOT NULL,
                    entity_slug  TEXT NOT NULL,
                    analysis_id  TEXT NOT NULL,
                    schedule     TEXT NOT NULL DEFAULT 'manual',
                    enabled      INTEGER NOT NULL DEFAULT 1,
                    last_run     TEXT DEFAULT '',
                    next_run     TEXT DEFAULT '',
                    PRIMARY KEY (entity_type, entity_slug, analysis_id)
                )
            """)
            # Migration: last_run_status/last_run_activity_id — added so the
            # Admin Schedules overview can show success/error at a glance
            # without scanning activity_log. Populated by
            # update_schedule_after_run(), written by scheduler.py's
            # _run_due(), which now also writes a real ActivityEntry for
            # every scheduled run (previously only logged to Python's own
            # logger — a real gap, not by design; see scheduler.py).
            existing_sched = self._get_table_columns(conn, "resource_schedules")
            if "last_run_status" not in existing_sched:
                conn.execute("ALTER TABLE resource_schedules ADD COLUMN last_run_status TEXT DEFAULT ''")
            if "last_run_activity_id" not in existing_sched:
                conn.execute("ALTER TABLE resource_schedules ADD COLUMN last_run_activity_id TEXT DEFAULT ''")
            # Migration: target_kind — what `analysis_id` refers to.
            #
            # A schedule could only ever name an analysis, so scheduling a
            # SURVEY had nowhere to go, which is what blocked Automate's survey
            # section (docs/granularity-pass.md). `analysis_id` now holds either
            # an analysis id or a Survey Definition's qualified name, and this
            # column says which.
            #
            # The column is added rather than the key rebuilt on purpose: the
            # primary key (entity_type, entity_slug, analysis_id) stays correct
            # either way, because the two namespaces cannot collide — a survey
            # reference is always "GovActionProcess::…" and no analysis id
            # contains "::". Rebuilding a primary key to express something the
            # existing one already distinguishes would be migration risk bought
            # for nothing.
            if "target_kind" not in existing_sched:
                conn.execute(
                    "ALTER TABLE resource_schedules ADD COLUMN target_kind "
                    "TEXT NOT NULL DEFAULT 'analysis'")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS survey_definition_cache (
                    entity_type            TEXT NOT NULL,
                    entity_slug            TEXT NOT NULL,
                    technology_type        TEXT NOT NULL,
                    process_guid           TEXT NOT NULL,
                    process_qualified_name TEXT DEFAULT '',
                    cached_at              TEXT NOT NULL,
                    PRIMARY KEY (entity_type, entity_slug, technology_type)
                )
            """)
            # Egeria linkage health, per entity, across all three resource
            # types. RE caches Egeria GUIDs (egeria_asset_guid, and per-report /
            # per-file GUIDs beneath them) and trusted them unconditionally: if
            # Egeria's repository was reset independently of RE's registry, the
            # next survey or publish failed with an opaque SERVER_ERROR_500 that
            # reached the UI verbatim and named nothing actionable.
            #
            # Its own table rather than a column on each of projects/databases/
            # filesystems: the condition and its three resolutions are identical
            # for all three, and each egeria_*_surveyor.py already reimplements
            # its own GUID caching — one more per-type copy is the direction that
            # produced the drift in _build_annotation_props.
            #
            # A row exists only once a divergence has been detected. No row means
            # "nothing has gone wrong", which is the overwhelmingly common case
            # and should cost nothing to represent.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS egeria_linkage_status (
                    entity_type    TEXT NOT NULL,
                    entity_slug    TEXT NOT NULL,
                    status         TEXT NOT NULL DEFAULT 'stale',
                    stale_guid     TEXT DEFAULT '',
                    detected_at    TEXT NOT NULL DEFAULT '',
                    last_checked_at TEXT NOT NULL DEFAULT '',
                    detail         TEXT DEFAULT '',
                    PRIMARY KEY (entity_type, entity_slug)
                )
            """)
            # Migration: add last_checked_at to existing databases.
            # `detected_at` is the FIRST detection and must never move while a
            # row stays stale (mark_egeria_linkage_stale's ON CONFLICT below
            # preserves it) — found live 2026-09-26: `recheck_all_linkages`
            # re-confirming a still-stale link overwrote `detected_at` to
            # today, so the header read "link stale since 2026-09-26" for a
            # link that had actually been stale since 2026-09-22.
            # `last_checked_at` is the new column that DOES move on every
            # recheck, so "how long has this been broken" and "how fresh is
            # this reading" are two separate, both-honest facts.
            if "last_checked_at" not in self._get_table_columns(conn, "egeria_linkage_status"):
                conn.execute(
                    "ALTER TABLE egeria_linkage_status ADD COLUMN last_checked_at TEXT NOT NULL DEFAULT ''")
            # Pending Egeria writes, one row per ELEMENT (asset, report,
            # each annotation, each relationship) — docs/outbox-publishing-
            # design.md, D1, settled per-element 2026-08-31.
            #
            # Why per element and not per publish() call: the failure
            # granularity that already exists IS per element. annotation_props
            # .publish_annotations() logs a failed annotation and continues, so
            # a SurveyReport is reported published even when every annotation
            # under it failed to write. One row per call could only record
            # "that publish failed", never how far it got — and "no half-
            # published blueprint" is a claim about elements, not calls.
            #
            # qualified_name is the idempotency key and is NOT NULL: the apply
            # step looks it up before creating (the behaviour
            # _find_or_create_asset and publish_annotations already have) so a
            # replay after a crash between Egeria's write and our recording of
            # it adopts the existing element instead of duplicating it.
            # egeria_guid, once recorded, is the primary identity and makes
            # even that search unnecessary on later retries.
            #
            # depends_on_id is what makes ordering enforceable rather than
            # aspirational: a row is eligible only when its dependency is
            # 'done', so annotations cannot be attempted before their report.
            #
            # Volume, measured 2026-08-31: every publish ever performed comes
            # to 831 rows at this granularity. One future proposal publish on
            # egeria_git is ~2,100 rows at the largest run observed (the
            # 13,813 scoped findings that figure was first taken from are
            # ALL-history, over 14 runs — a correct count of the wrong
            # population) — which is why purge_outbox_completed() exists rather
            # than being left for later, and why scheduler.py calls it on the
            # same loop that drains: retention nobody invokes is not retention.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS egeria_outbox (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_type     TEXT NOT NULL,
                    entity_slug     TEXT NOT NULL,
                    run_id          TEXT NOT NULL DEFAULT '',
                    element_kind    TEXT NOT NULL,
                    qualified_name  TEXT NOT NULL,
                    payload_json    TEXT NOT NULL,
                    depends_on_id   INTEGER DEFAULT NULL,
                    status          TEXT NOT NULL DEFAULT 'pending',
                    attempts        INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL DEFAULT '',
                    last_error      TEXT DEFAULT '',
                    egeria_guid     TEXT DEFAULT '',
                    created_at      TEXT NOT NULL,
                    completed_at    TEXT DEFAULT ''
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_egeria_outbox_due "
                "ON egeria_outbox(status, next_attempt_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_egeria_outbox_run "
                "ON egeria_outbox(run_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_egeria_outbox_entity "
                "ON egeria_outbox(entity_type, entity_slug)"
            )
            # When a drainer took this row. Set with status='running' in the
            # same transaction as the select, so a second drainer cannot take
            # it. Nullable because every pre-existing row predates claiming.
            existing_outbox = self._get_table_columns(conn, "egeria_outbox")
            for col, defn in [("claimed_at", "TEXT DEFAULT ''")]:
                if col not in existing_outbox:
                    conn.execute(f"ALTER TABLE egeria_outbox ADD COLUMN {col} {defn}")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rfa_actions (
                    id                TEXT PRIMARY KEY,
                    entry_id          TEXT NOT NULL,
                    annotation_index  INTEGER NOT NULL,
                    rfa_status        TEXT NOT NULL DEFAULT 'open',
                    assignee          TEXT DEFAULT '',
                    defer_until       TEXT DEFAULT '',
                    resolution_note   TEXT DEFAULT '',
                    updated_at        TEXT NOT NULL DEFAULT ''
                )
            """)
            # Migration: docs/rfa-egeria-todo-followup.md's "one model, not
            # one location" decision — rfa_actions mirrors ToDoProperties
            # directly (activity_status/due_time/start_time/priority, using
            # Egeria's own ACTIVITY_STATUS vocabulary) alongside rfa_status/
            # defer_until (RE's own friendly verbs — still the API/frontend's
            # wire contract, unchanged; see activity.py's
            # _STATUS_TO_ACTIVITY_STATUS for the one translation seam).
            existing_rfa = self._get_table_columns(conn, "rfa_actions")
            if "activity_status" not in existing_rfa:
                conn.execute("ALTER TABLE rfa_actions ADD COLUMN activity_status TEXT NOT NULL DEFAULT 'REQUESTED'")
            if "due_time" not in existing_rfa:
                conn.execute("ALTER TABLE rfa_actions ADD COLUMN due_time TEXT DEFAULT ''")
            if "start_time" not in existing_rfa:
                conn.execute("ALTER TABLE rfa_actions ADD COLUMN start_time TEXT DEFAULT ''")
            if "priority" not in existing_rfa:
                conn.execute("ALTER TABLE rfa_actions ADD COLUMN priority INTEGER DEFAULT 0")
            if "egeria_todo_guid" not in existing_rfa:
                conn.execute("ALTER TABLE rfa_actions ADD COLUMN egeria_todo_guid TEXT DEFAULT ''")
            if "synced_at" not in existing_rfa:
                conn.execute("ALTER TABLE rfa_actions ADD COLUMN synced_at TEXT DEFAULT ''")
            if "sync_error" not in existing_rfa:
                conn.execute("ALTER TABLE rfa_actions ADD COLUMN sync_error TEXT DEFAULT ''")
            # Real bug found during a live smoke test (2026-08-16): the
            # drawer's "Record answer" button never persisted anything — it
            # only called a purely client-side, in-memory logOp() (capped at
            # 200 entries, lost on reload), never a backend call. `notes` is
            # a real, persisted free-text field, addable independent of any
            # status change (unlike resolution_note, which is specifically
            # the note recorded when completing an RFA).
            if "notes" not in existing_rfa:
                conn.execute("ALTER TABLE rfa_actions ADD COLUMN notes TEXT DEFAULT ''")
            # Notes -> Egeria ActivityEntry sync (2026-08-16, per direct
            # decision): a note is only pushed to Egeria once the RFA
            # already has a real ToDo (egeria_todo_guid) — a note with
            # nothing to link against isn't synced yet, and picks up on the
            # next reconciliation pass once a status action creates one.
            # egeria_notelog_guid caches the NoteLog created on the ToDo
            # (reused across edits, not recreated); notes_synced_value is
            # the last note text actually pushed, so an unchanged note
            # doesn't get re-synced as a duplicate ActivityEntry.
            if "egeria_notelog_guid" not in existing_rfa:
                conn.execute("ALTER TABLE rfa_actions ADD COLUMN egeria_notelog_guid TEXT DEFAULT ''")
            if "notes_synced_value" not in existing_rfa:
                conn.execute("ALTER TABLE rfa_actions ADD COLUMN notes_synced_value TEXT DEFAULT ''")
            if "notes_sync_error" not in existing_rfa:
                conn.execute("ALTER TABLE rfa_actions ADD COLUMN notes_sync_error TEXT DEFAULT ''")
            # RFA dismissals — "this finding is not actionable here", recorded
            # as a ROW, never as a deletion.
            #
            # Dan, direct feedback (2026-08-31): "it seems that another option
            # should be Ignore (or Not Applicable) because the user, generally
            # can't do something about no SECURITY.md file found on repos they
            # don't control". Followed by: "suppress with visibility - do both -
            # there may be a future admin setting that allows you to reset or
            # clear some of these decisions in the future (for example, you
            # become a maintainer)". Hence `cleared_at`/`cleared_by`: reversing
            # a dismissal is an UPDATE that leaves the original judgement
            # legible, not an undelete of something that was thrown away.
            #
            # Why this is NOT a new rfa_status on rfa_actions, which already
            # carries open/deferred/reassigned/completed:
            #
            #  1. rfa_actions is keyed by rfa_id = "{entry_id}::{index}" — an
            #     identity for ONE OCCURRENCE in ONE activity-log entry. Every
            #     survey run writes a new entry, so a dismissal recorded there
            #     would be forgotten the next time the same survey ran. The
            #     whole point of "not applicable" is that the finding WILL
            #     recur (nobody is going to add SECURITY.md to a repo they
            #     don't own) and must stay suppressed when it does. So the key
            #     has to be the finding's CONTENT, not its occurrence.
            #  2. rfa_actions rows sync to real Egeria ToDos
            #     (rfa_egeria_sync.sync_rfa_action). "We are not going to act
            #     on this" is a local judgement about our own view, not a
            #     governance task for anyone to carry out — putting it in that
            #     table would push a ToDo into Egeria for every dismissal.
            #
            # The content key is (entity_type, entity_slug, analysis_name,
            # summary_key), deliberately the SAME tuple the drawer already
            # groups same-finding occurrences by (index.html's `byFinding`
            # map, analysis_name + summary within an entity) — one dismissal
            # therefore suppresses exactly one rendered group, including the
            # occurrences a future run has not produced yet.
            #
            # Known limit, disclosed rather than designed around: a summary
            # carrying a changing measurement ("...: 209.7 MB across 699
            # files") produces a different key each run, so a dismissal
            # against it stops matching. That is correct behaviour for a
            # measurement — the number changed, so the judgement may need
            # revisiting — and wrong-feeling for a finding that is really the
            # same one. Real RFA summaries measured 2026-09-01 are stable
            # text ("No SECURITY.md found"), so this bites nothing today; if a
            # measurement-bearing RFA appears later, the fix is a normalised
            # key on that annotation, not a looser match here.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rfa_dismissals (
                    id            TEXT PRIMARY KEY,
                    entity_type   TEXT NOT NULL DEFAULT '',
                    entity_slug   TEXT NOT NULL,
                    analysis_name TEXT NOT NULL DEFAULT '',
                    summary_key   TEXT NOT NULL DEFAULT '',
                    reason        TEXT NOT NULL,
                    note          TEXT DEFAULT '',
                    created_by    TEXT DEFAULT '',
                    created_at    TEXT NOT NULL,
                    cleared_at    TEXT DEFAULT '',
                    cleared_by    TEXT DEFAULT ''
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rfa_dismissals_entity "
                "ON rfa_dismissals(entity_slug)"
            )
            # A finding about the ANALYSIS, not the repository — a
            # disagreement between two measures, or a check that could not be
            # established — belongs in a gaps list the project owns rather
            # than on the resource's own findings (SPEC-ACTIONABLE-AND-HONEST.md
            # §3, "the missing half is whose absence it is"). Upserted, keyed
            # on (project_slug, analysis_id, gap_kind, check_name) — a repeat
            # sighting of the same gap bumps last_seen_at rather than
            # duplicating (see collect_gaps/upsert_gap in gaps.py), matching
            # the finding rows it is derived from, which are themselves
            # replaced per run rather than accumulated forever.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analysis_gaps (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_slug     TEXT NOT NULL,
                    analysis_id      TEXT NOT NULL,
                    gap_kind         TEXT NOT NULL,
                    check_name       TEXT NOT NULL DEFAULT '',
                    sentence         TEXT NOT NULL DEFAULT '',
                    evidence_json    TEXT DEFAULT NULL,
                    first_seen_at    TEXT NOT NULL,
                    last_seen_at     TEXT NOT NULL,
                    resolved_at      TEXT DEFAULT NULL,
                    rfa_activity_id  TEXT DEFAULT NULL,
                    FOREIGN KEY (project_slug) REFERENCES projects(slug)
                )
            """)
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_analysis_gaps_identity "
                "ON analysis_gaps(project_slug, analysis_id, gap_kind, check_name)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS annotation_types (
                    annotation_type TEXT PRIMARY KEY,
                    display_name    TEXT NOT NULL,
                    description     TEXT DEFAULT '',
                    properties      TEXT DEFAULT '[]',
                    egeria_type     TEXT DEFAULT '',
                    python_class    TEXT DEFAULT ''
                )
            """)
            count = conn.execute("SELECT COUNT(*) FROM annotation_types").fetchone()[0]
            if count == 0:
                from resource_explorer.surveyors.survey_report import ANNOTATION_TYPES_REGISTRY
                for item in ANNOTATION_TYPES_REGISTRY:
                    conn.execute(
                        """INSERT INTO annotation_types (
                            annotation_type, display_name, description, properties, egeria_type, python_class
                        ) VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            item["type"],
                            item["display_name"],
                            item["description"],
                            json.dumps(item["properties"]),
                            item["egeria_type"],
                            item["python_class"],
                        ),
                    )
            # Generic runtime-settings key-value store — the first one in this
            # codebase (everything else is .env/Pydantic, loaded once at process
            # start). Reusable for any future runtime setting, not GitHub-specific
            # ("Discover repos to scout" plan, D1).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS app_settings (
                    key        TEXT PRIMARY KEY,
                    value      TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            # Per-call Egeria timing (owner's ruling, 2026-09-15): every
            # measured RunCost/depth-offer price in this codebase so far has
            # been per ANALYSIS RUN, never per individual Egeria API call --
            # the layer-2 catalogue-depth offer wants "1.5s median" for one
            # write, which nothing recorded. `kind` distinguishes write from
            # query, deliberately: the owner's own point is that insert/
            # update cost and query cost are different populations, and a
            # query's cost is itself sensitive to its own parameters (graph
            # depth, page size) -- `params_json` carries those, so a reader
            # can bucket by them rather than averaging a shallow lookup
            # together with a deep graph walk. `call_name` is the pyegeria
            # method name (`create_solution_component`, `find_assets`, …),
            # not a higher-level label -- the same method can move between
            # call sites without losing its own history.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS egeria_call_timings (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    call_name    TEXT NOT NULL,
                    kind         TEXT NOT NULL,
                    seconds      REAL NOT NULL,
                    params_json  TEXT NOT NULL DEFAULT '{}',
                    recorded_at  TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_egeria_call_timings_call "
                "ON egeria_call_timings(call_name, kind)"
            )
            # Resource triage disposition — undecided (default) / tracking /
            # investigating / recommended / using / abandoned / ignored.
            # `recommended` and `using` are both positive terminal states,
            # sitting alongside the negative terminal states
            # abandoned/ignored — `recommended` added once real use showed
            # the original four-state vocabulary had no "decided for it"
            # counterpart to "decided against it"; `using` added later for
            # the stronger signal that the org is already actively using
            # the resource or knows of its use elsewhere in the org.
            #
            # Keyed by (entity_type, entity_slug) — generalized 2026-09-22
            # (Backlog.md, "Disposition is NOT fixed here") from a
            # github_url-only PK, joining the same convention every other
            # entity-family table already uses (see `_ENTITY_SLUG_TABLES`
            # below). `github_url` stays as a plain column, kept for
            # entity_type='repo' rows only — it is still how a repo's
            # disposition gets set/read (see `set_disposition`), just no
            # longer the identity the table is keyed on; database/
            # filesystem rows leave it ''.
            #
            # entity_slug for a repo is its Project's real slug once
            # imported (which can legitimately differ from a github_url-
            # derived guess — a manual slug override, or a collision-
            # avoidance rename), or a github_url-derived slug for a
            # never-imported discovery-search candidate ("Discover repos to
            # scout" plan, D10) — same derivation `org_importer._url_to_slug`
            # already uses for a candidate's entity_slug elsewhere
            # (resource_working_set/activity_log). `add()` reconciles a
            # candidate's disposition row onto the real slug at import time,
            # for the rarer case where they differ (see `add()`'s docstring
            # / `_reconcile_disposition_on_import`).
            #
            # One row per (entity_type, entity_slug) — upsert, not
            # append-only, since there's only ever one *current* decision.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS repo_dispositions (
                    entity_type  TEXT NOT NULL DEFAULT 'repo',
                    entity_slug  TEXT NOT NULL,
                    github_url   TEXT DEFAULT '',
                    disposition  TEXT NOT NULL DEFAULT 'undecided',
                    reason       TEXT DEFAULT '',
                    decided_by   TEXT DEFAULT '',
                    decided_at   TEXT NOT NULL,
                    project_slug TEXT DEFAULT '',
                    PRIMARY KEY (entity_type, entity_slug)
                )
            """)
            self._migrate_repo_dispositions_to_entity_key(conn)
            # Append-only companion to repo_dispositions above — that table
            # is upsert-only (one row per entity, only the *current*
            # decision), so it can't answer "what was the history of
            # decisions on this resource." Every set_disposition() call
            # writes here too, in addition to upserting the current-state
            # row (Scouting workflow redesign plan, D3/D6 — the Disposition
            # sub-tab's timeline view reads this).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS repo_disposition_history (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_type  TEXT NOT NULL DEFAULT 'repo',
                    entity_slug  TEXT NOT NULL DEFAULT '',
                    github_url   TEXT DEFAULT '',
                    disposition  TEXT NOT NULL,
                    reason       TEXT DEFAULT '',
                    decided_by   TEXT DEFAULT '',
                    decided_at   TEXT NOT NULL
                )
            """)
            self._migrate_repo_disposition_history_to_entity_key(conn)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_disposition_history_url "
                "ON repo_disposition_history(github_url, decided_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_disposition_history_entity "
                "ON repo_disposition_history(entity_type, entity_slug, decided_at)"
            )
            # DepthOffer (designer, 2026-09-13): the outcome of the /next
            # pane's "run these never-run analyses" offer, recorded on the
            # LATEST repo_disposition_history row for the url — once per
            # verdict (record_depth_offer() enforces "once" by refusing a
            # second write once this is non-null). NULL until offered.
            if "depth_offer" not in self._get_table_columns(conn, "repo_disposition_history"):
                conn.execute(
                    "ALTER TABLE repo_disposition_history ADD COLUMN depth_offer TEXT DEFAULT NULL"
                )
            # Locally-tracked sub-resources — the "Catalog" stage of the
            # repo scope-narrowing funnel (docs/repo-scope-narrowing-funnel.md,
            # D2). Built generically across resource types from the start,
            # even though only 'repo' has a UI/route wired up in Phase 1.
            # `locator`'s meaning is resource-type-specific: a relative path
            # for repo/filesystem, a 'schema' or 'schema.table' reference for
            # database. '' = the resource root itself. This table only
            # records THAT something is a tracked target — "what's been
            # surveyed against it" lives on project_analysis_findings/
            # _metrics via their own scope_locator column, not here.
            # `detail_json` is a denormalized snapshot of the source
            # finding's detail at catalog time (owners/dates/etc) so
            # publish_sub_resources() doesn't need to re-join back to
            # findings that might have been superseded by a later survey.
            # Idempotent catalog (D4 — repeatable, never clobbers
            # egeria_guid on re-catalog of an already-tracked locator).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sub_resources (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    resource_type  TEXT NOT NULL,
                    resource_slug  TEXT NOT NULL,
                    locator        TEXT NOT NULL,
                    kind           TEXT NOT NULL,
                    cataloged_at   TEXT NOT NULL,
                    source_finding TEXT DEFAULT '',
                    detail_json    TEXT DEFAULT '',
                    egeria_guid    TEXT DEFAULT '',
                    UNIQUE(resource_type, resource_slug, locator)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sub_resources_resource "
                "ON sub_resources(resource_type, resource_slug)"
            )
            # Named, reusable discovery sources — a saved search (org/
            # language/license/... filters) or a manually-curated list of
            # github_urls (for foundations like Eclipse that spread projects
            # across hundreds of orgs, or a user's own enterprise repos) —
            # Scouting workflow redesign plan, D1.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS discovery_sources (
                    slug         TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    source_type  TEXT NOT NULL,
                    config_json  TEXT NOT NULL,
                    created_at   TEXT NOT NULL
                )
            """)
            # Personal "do I want this in front of me right now" view filter
            # — deliberately separate from repo_dispositions (a canonical
            # judgment about the resource). Its own table precisely so a
            # `user_id` column could be added later without touching
            # disposition's model (D4) — **and it has been** (2026-09-04, plan
            # §4): the row is now per person, and the primary key carries
            # `user_id` because two people hiding the same repo are two facts,
            # not one that overwrites the other.
            #
            # `''` is the shared/legacy bucket: every row written before this
            # change, and anything an unauthenticated path writes. Reads take
            # the caller's own row when there is one and fall back to `''`, so
            # nobody's existing view filters vanished when the column appeared.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS resource_working_set (
                    entity_type TEXT NOT NULL,
                    entity_slug TEXT NOT NULL,
                    user_id     TEXT NOT NULL DEFAULT '',
                    hidden      INTEGER NOT NULL DEFAULT 0,
                    updated_at  TEXT NOT NULL,
                    PRIMARY KEY (entity_type, entity_slug, user_id)
                )
            """)
            self._add_user_id_to_keyed_table(
                conn, "resource_working_set",
                columns="entity_type TEXT NOT NULL, entity_slug TEXT NOT NULL, "
                        "user_id TEXT NOT NULL DEFAULT '', hidden INTEGER NOT NULL DEFAULT 0, "
                        "updated_at TEXT NOT NULL",
                column_names=("entity_type", "entity_slug", "user_id", "hidden", "updated_at"),
                pk=("entity_type", "entity_slug", "user_id"),
            )
            # Egeria Project context — the "uber intent" from
            # docs/discovery-automate-project-context-plan.md Part 5. Always
            # say "Egeria Project" in code/UI — RE's own Project class above
            # (a registered repo/resource) is an unrelated concept that
            # happens to share the word. `status`: unset (default, nothing
            # decided yet) | personal (explicit "just exploring," local-only
            # — no Egeria PersonalProject element created for this alone) |
            # linked (associated with a real, already-existing Egeria
            # Project — egeria_project_guid/qualified_name populated) |
            # deferred (a free-text project name typed now, correlation to
            # a real Egeria Project GUID resolved later, e.g. at Curate) |
            # declined (explicit "no association," a deliberate choice, not
            # the same as never having answered). One row per resource —
            # upsert, not append-only, since there's only ever one *current*
            # context (mirrors resource_working_set's exact shape above).
            # `user_id` for the same reason and on the same terms as
            # resource_working_set above (2026-09-04, plan §4): the Egeria
            # Project a person is working under is *their* answer, and two
            # people surveying one repo under two different Projects is a
            # legitimate state rather than a conflict to resolve by last-write.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entity_egeria_project_context (
                    entity_type                   TEXT NOT NULL,
                    entity_slug                   TEXT NOT NULL,
                    user_id                        TEXT NOT NULL DEFAULT '',
                    status                         TEXT NOT NULL DEFAULT 'unset',
                    egeria_project_guid            TEXT DEFAULT '',
                    egeria_project_qualified_name  TEXT DEFAULT '',
                    free_text_name                 TEXT DEFAULT '',
                    decided_at                     TEXT DEFAULT '',
                    PRIMARY KEY (entity_type, entity_slug, user_id)
                )
            """)
            self._add_user_id_to_keyed_table(
                conn, "entity_egeria_project_context",
                columns="entity_type TEXT NOT NULL, entity_slug TEXT NOT NULL, "
                        "user_id TEXT NOT NULL DEFAULT '', "
                        "status TEXT NOT NULL DEFAULT 'unset', "
                        "egeria_project_guid TEXT DEFAULT '', "
                        "egeria_project_qualified_name TEXT DEFAULT '', "
                        "free_text_name TEXT DEFAULT '', decided_at TEXT DEFAULT ''",
                column_names=("entity_type", "entity_slug", "user_id", "status",
                              "egeria_project_guid", "egeria_project_qualified_name",
                              "free_text_name", "decided_at"),
                pk=("entity_type", "entity_slug", "user_id"),
            )
            # Investigations — the framing step ahead of Scouting
            # (docs/investigation-framing-design.md §1). An Investigation is
            # one body of work: the context every other analysis runs inside,
            # and the thing that answers "why does this set of work exist at
            # all", which neither intent (what kind of work) nor perspective
            # (whose concerns) can say.
            #
            # ONE local row in all three of §1's starting modes — bind to an
            # existing Egeria Project, create a new one, or purely local with
            # no Egeria write. `egeria_project_guid` is nullable and that is
            # the single most important structural decision in the design: it
            # makes promotion to Egeria a fill-in rather than a migration.
            #
            # `purposes` is `ProjectCharter.purposes` (§2) — deliberately NOT
            # a new RE vocabulary. Stored as a JSON array of the 8 values the
            # question corpus is already tagged with (Assess, Certify, Deploy,
            # Explore, Learn, Maintain, Select, Share) so it replays into a
            # real ProjectCharter unchanged.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS investigations (
                    slug                           TEXT PRIMARY KEY,
                    display_name                   TEXT NOT NULL,
                    description                    TEXT DEFAULT '',
                    purposes_json                  TEXT DEFAULT '[]',
                    egeria_project_guid            TEXT DEFAULT '',
                    egeria_project_qualified_name  TEXT DEFAULT '',
                    -- The Egeria binding is the investigation's, not a
                    -- separate session-wide setting. §1 makes binding to an
                    -- Egeria Project one of the three ways to START an
                    -- investigation, so a second parallel control would be two
                    -- answers to one question. Carries the full context shape
                    -- (unset/personal/declined/linked/deferred + free text) so
                    -- publishes read it unchanged.
                    -- §1's mode 2 carries a classification. Kept on the local
                    -- row even for a local-only investigation, so promoting one
                    -- later does not have to ask again for something the user
                    -- already decided.
                    project_classification         TEXT DEFAULT 'StudyProject',
                    -- The second axis (see EGERIA_BINDINGS): whether this
                    -- investigation has, or is meant to have, an Egeria
                    -- Project. `local` is the ad-hoc case.
                    egeria_binding                 TEXT DEFAULT 'egeria',
                    -- Experiment's defining attribute. Empty for every other
                    -- classification, and required for that one.
                    hypothesis                     TEXT DEFAULT '',
                    -- Whose investigation this is. `SHARED_USER_ID` ('') is a
                    -- real value meaning shared/legacy, not a null-shaped
                    -- unknown — same convention as resource_working_set.
                    -- Decides visibility for the PRIVATE_CLASSIFICATIONS.
                    created_by                     TEXT DEFAULT '',
                    egeria_project_status          TEXT DEFAULT 'unset',
                    egeria_free_text_name          TEXT DEFAULT '',
                    status                         TEXT NOT NULL DEFAULT 'open',
                    created_at                     TEXT NOT NULL,
                    updated_at                     TEXT DEFAULT ''
                )
            """)
            # Migration: the Egeria binding columns were added when the
            # session-wide project control was folded into the investigation
            # (§1 — binding is one of the three ways to START one, so a second
            # parallel control was two answers to one question). CREATE TABLE
            # IF NOT EXISTS does nothing for a table that already exists, so a
            # registry created before that change needs them added here.
            inv_cols = self._get_table_columns(conn, "investigations")
            for col, defn in [
                ("egeria_project_status", "TEXT DEFAULT 'unset'"),
                ("egeria_free_text_name", "TEXT DEFAULT ''"),
                ("project_classification", "TEXT DEFAULT 'StudyProject'"),
                # Backfills to 'egeria' rather than 'local', and that is a
                # judgement worth stating. Before this column existed, ad-hoc
                # was not a choice anyone could make: every investigation was
                # headed for Egeria eventually, whether or not it had got
                # there. Defaulting the existing rows to 'local' would invent a
                # deliberate decision nobody took and would hide them from
                # anything that keys on the binding. 'egeria' says what was
                # actually true of them.
                ("egeria_binding", "TEXT DEFAULT 'egeria'"),
                ("hypothesis", "TEXT DEFAULT ''"),
                # Backfills to '' — the shared bucket — and that is the least
                # bad of two bad options, so it is written down rather than
                # left to look obvious.
                #
                # These rows genuinely have no owner: nothing recorded one, so
                # nothing can recover it. A Personal investigation among them
                # is therefore visible to everyone, which is uncomfortable —
                # but it has ALWAYS been visible to everyone, so this changes
                # nothing about who can see it. The alternative, hiding
                # ownerless private rows from all callers, would make somebody's
                # own work vanish from them with no way to get it back, and
                # would be a NEW loss rather than a persisting one.
                #
                # `get_investigation` marks these rows `visibility_note` so the
                # gap is visible instead of merely tolerated, and a person can
                # claim one.
                ("created_by", "TEXT DEFAULT ''"),
            ]:
                if col not in inv_cols:
                    conn.execute(f"ALTER TABLE investigations ADD COLUMN {col} {defn}")

            # Membership follows Egeria's real two-hop shape rather than a flat
            # link, so promotion stays a replay:
            #
            #   Project --ResourceList--> WorkingSet (a Collection)
            #           --CollectionMembership--> Repo / DB / FS
            #
            # An earlier pass here (and design §6's own sketch) linked the
            # Project straight to each resource. The two-hop form is better and
            # is what Egeria actually offers: the working set becomes a nameable,
            # reusable thing in its own right, `ResourceList.resourceUse` describes
            # how the investigation uses it, and `CollectionMembership` carries a
            # per-resource rationale that a direct link has nowhere to put.
            #
            # All three tables mirror their Egeria counterparts and carry a
            # nullable GUID, so an investigation works with no Egeria at all and
            # promotion fills in rather than migrates.
            # A Collection per investigation-scoped grouping. TWO kinds, and the
            # distinction is the whole point:
            #
            #   folio        — everything in scope for the investigation. One per
            #                  investigation. Membership here is "this is part of
            #                  the body of work", nothing more.
            #   working_set  — resources carrying ONE disposition. A WorkingSet
            #                  has a single Disposition, so one per disposition
            #                  per investigation, created lazily.
            #
            # An earlier pass here made a single WorkingSet per investigation and
            # used it as the membership list. That was a category error: if a
            # WorkingSet carries one disposition it cannot hold the whole
            # investigation, and the grouping had no meaning of its own.
            #
            # Consequence worth stating: a resource's disposition WITHIN an
            # investigation is expressed by which working_set it belongs to, so
            # no separate per-investigation disposition table is needed. The
            # global repo_dispositions table stays as it is — that is a judgement
            # about the repo at large, not within a body of work.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS working_sets (
                    slug                              TEXT PRIMARY KEY,
                    display_name                      TEXT NOT NULL,
                    collection_kind                   TEXT NOT NULL DEFAULT 'folio',
                    disposition                       TEXT DEFAULT '',
                    egeria_collection_guid            TEXT DEFAULT '',
                    egeria_collection_qualified_name  TEXT DEFAULT '',
                    created_at                        TEXT NOT NULL
                )
            """)
            ws_cols = self._get_table_columns(conn, "working_sets")
            for col, defn in [
                ("collection_kind", "TEXT NOT NULL DEFAULT 'folio'"),
                ("disposition", "TEXT DEFAULT ''"),
            ]:
                if col not in ws_cols:
                    conn.execute(f"ALTER TABLE working_sets ADD COLUMN {col} {defn}")
            # The ResourceList relationship (0019) itself. `watch_resource`
            # mirrors its `watchResource` property — the flag Automate's
            # subscriptions should eventually ride on rather than a separate
            # local table (see the Backlog entry).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS investigation_resource_lists (
                    investigation_slug  TEXT NOT NULL,
                    working_set_slug    TEXT NOT NULL,
                    resource_use        TEXT DEFAULT '',
                    watch_resource      INTEGER NOT NULL DEFAULT 0,
                    added_at            TEXT NOT NULL,
                    PRIMARY KEY (investigation_slug, working_set_slug)
                )
            """)
            # CollectionMembership (0021) — carries rationale and confidence in
            # Egeria; `state` keeps §7's candidate/in-scope/excluded, which
            # Remediate needs because its membership is outcome-driven.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS working_set_members (
                    working_set_slug      TEXT NOT NULL,
                    entity_type           TEXT NOT NULL,
                    entity_slug           TEXT NOT NULL,
                    membership_rationale  TEXT DEFAULT '',
                    state                 TEXT NOT NULL DEFAULT 'in-scope',
                    added_at              TEXT NOT NULL,
                    PRIMARY KEY (working_set_slug, entity_type, entity_slug)
                )
            """)
            # Automate — Part 4 of docs/discovery-automate-project-context-plan.md,
            # the 8th canonical intent (sustained *machine* attention,
            # parallel to Curate's sustained *human* attention). Local-first
            # by design decision (2026-08-13): Egeria's own real Notification
            # Manager (NotificationType + Link Monitored Resource + Link
            # Notification Subscriber) is the eventual catalog of record for
            # this, but its "Create Notification Type" command wraps the same
            # generic create_governance_definition() body-construction call
            # CLAUDE.md rule 12 warns against reimplementing untested — see
            # docs/automate-notification-manager-pyegeria-spec.md for what a
            # safe pyegeria convenience API would need to look like. Until
            # that lands, a subscription lives here, fully locally — RE's own
            # scheduler.py does the detection (comparing this analysis_id's
            # latest two findings/metrics batches), RFA does the delivery.
            # egeria_notification_type_guid/qualified_name are NULL/empty
            # until an Egeria-side element exists for this subscription —
            # every field below works without one.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS notification_subscriptions (
                    id                                        INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_type                                TEXT NOT NULL,
                    entity_slug                                TEXT NOT NULL,
                    analysis_id                                TEXT NOT NULL,
                    label                                       TEXT DEFAULT '',
                    active                                      INTEGER NOT NULL DEFAULT 1,
                    created_at                                  TEXT NOT NULL,
                    last_checked_at                             TEXT DEFAULT '',
                    last_notified_at                            TEXT DEFAULT '',
                    notification_count                          INTEGER NOT NULL DEFAULT 0,
                    egeria_notification_type_guid               TEXT DEFAULT '',
                    egeria_notification_type_qualified_name     TEXT DEFAULT ''
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_notification_subscriptions_entity "
                "ON notification_subscriptions(entity_type, entity_slug, analysis_id)"
            )

            # ── the run queue (docs/runtime-architecture-plan.md §2, step 2b) ──
            #
            # One row per backgrounded unit of work. Before this, a `web`
            # request spawned a `threading.Thread` and returned; the run then
            # existed only as a live thread in whichever uvicorn process
            # happened to serve the request, and as an `activity_log` row
            # claiming "running" with the owner's pid buried in `detail`.
            # That is what `run_reconciler.py` judges *after the fact*. This
            # table is the same ownership question asked *before* the work
            # starts: a claim, not a forensic reconstruction.
            #
            # Deliberately a NEW table rather than more columns on
            # `activity_log`. `activity_log` is the user-facing narrative of
            # everything that has happened — catalog writes, RFAs, scout
            # entries — and only a minority of its rows are queueable work.
            # Hanging state/claimed_by/heartbeat_at off all of them would put
            # queue semantics on rows that can never be claimed, and would
            # make "what is queued" a filtered scan of the largest table here.
            # The two stay joined by `result_ref`, which holds the
            # activity_log id the UI already polls — so the frontend contract
            # (POST returns an activity_id, poll GET /api/activity/{id}) is
            # unchanged by the queue existing.
            #
            # `claimed_by` is the human-readable "hostname:pid" the plan asks
            # for; `runner` is the machine-readable
            # `run_reconciler.process_identity()` (pid + process start time)
            # that reconciliation actually tests for liveness. Two fields
            # because pid alone is not an identity — pids are reused, which is
            # exactly the trap `run_reconciler._is_alive` documents.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    id            TEXT PRIMARY KEY,
                    kind          TEXT NOT NULL,
                    target        TEXT NOT NULL DEFAULT '{}',
                    requested_by  TEXT NOT NULL DEFAULT '',
                    state         TEXT NOT NULL DEFAULT 'queued',
                    claimed_by    TEXT NOT NULL DEFAULT '',
                    runner        TEXT NOT NULL DEFAULT '',
                    enqueued_at   TEXT NOT NULL,
                    claimed_at    TEXT NOT NULL DEFAULT '',
                    heartbeat_at  TEXT NOT NULL DEFAULT '',
                    started_at    TEXT NOT NULL DEFAULT '',
                    finished_at   TEXT NOT NULL DEFAULT '',
                    error         TEXT NOT NULL DEFAULT '',
                    result_ref    TEXT NOT NULL DEFAULT ''
                )
            """)
            # The claim's own ORDER BY, so a queue with a long terminal
            # history does not scan it to find the oldest queued row.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_state_enqueued "
                "ON runs(state, enqueued_at)"
            )
            # Per-user fairness reads this on every claim attempt.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_requested_by_state "
                "ON runs(requested_by, state)"
            )
            # GET /api/runs/{id} joins back from the activity id the UI holds.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_result_ref ON runs(result_ref)"
            )

    # ── the run queue ─────────────────────────────────────────────────────────
    #
    # Row-level SQL only. The claim loop, the heartbeat and the dispatch to a
    # workflow function live in resource_explorer/run_queue.py — the same split
    # egeria_outbox.py keeps from claim_due_outbox_elements() above.

    #: Every state a `runs` row can be in. queued -> claimed -> running ->
    #: (succeeded | failed | cancelled). `claimed` and `running` are separate on
    #: purpose: a claim is taken in one transaction and the work starts after
    #: it, so a row stuck in `claimed` says "a worker took this and died before
    #: it began", which is a different fact from "it died mid-run".
    RUN_STATES = ("queued", "claimed", "running", "succeeded", "failed", "cancelled")

    #: The kinds the queue knows how to execute. Not a foreign key to anything —
    #: run_queue.py's dispatch table is the real registry of handlers, and a row
    #: whose kind has no handler fails loudly rather than sitting queued for ever.
    RUN_KINDS = (
        "analysis_run",
        "database_analysis_run",
        "survey_definition_run",
        "scouting_scan",
        "stage_batch",
        "discovery_expand",
        "curate_commit",
        "materialize_components",
    )

    #: States that occupy a user's one fairness slot.
    _RUN_ACTIVE_STATES = ("claimed", "running")

    def enqueue_run(
        self,
        kind: str,
        target: dict | None = None,
        *,
        requested_by: str = "",
        result_ref: str = "",
        run_id: str | None = None,
        now: str | None = None,
    ) -> str:
        """Add one unit of work to the queue and return its id.

        `requested_by` is the user id the run is attributed to. Since RE
        adopted trellis-auth (2026-09-04, plan §4) it is a real signed-in user
        id on every person-initiated path — `run_queue.requested_by()` reads
        it from the request/CLI caller. `""` now means the worker role's own
        service-account work (bootstrap heal, resync, outbox drain), which is
        the one bucket claim_next_run() exempts from per-user fairness.
        """
        if kind not in self.RUN_KINDS:
            raise ValueError(f"unknown run kind {kind!r} — expected one of {list(self.RUN_KINDS)}")
        run_id = run_id or str(uuid.uuid4())
        now = now or datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO runs (id, kind, target, requested_by, state,
                                     enqueued_at, result_ref)
                   VALUES (?, ?, ?, ?, 'queued', ?, ?)""",
                (run_id, kind, json.dumps(target or {}), requested_by, now, result_ref),
            )
        return run_id

    def claim_next_run(
        self,
        claimed_by: str,
        runner: dict | None = None,
        *,
        kinds: "list[str] | None" = None,
        now: str | None = None,
    ) -> dict | None:
        """Take the oldest queued row this worker is allowed to run, or None.

        **Concurrency.** Select-and-mark happen in one transaction, and on
        Postgres the SELECT carries `FOR UPDATE SKIP LOCKED` so a second
        claimer skips the row a first claimer has locked instead of blocking
        on it and then claiming it a moment later. Exactly the shape
        claim_due_outbox_elements() uses, and for the same reason: SQLite has
        neither clause but serialises writers itself, so the transaction alone
        is enough there.

        **Per-user fairness.** A user with a run already `claimed` or `running`
        does not get a second one; their next row waits.

        The rule is scoped to a NON-EMPTY `requested_by`, and **as of
        2026-09-04 that exemption finally means what it was written to mean.**
        Before trellis-auth landed, every row was attributed to `""`, so the
        exemption covered the entire queue and the fairness rule was inert —
        applying it to that bucket would have collapsed the queue to one run at
        a time for everybody, a visible regression against per-route threads.
        Now `run_queue.requested_by()` returns the signed-in user, so the rule
        applies to real people and `""` is left holding only what it should:
        **the worker's own service-account runs.** No change was needed here
        the moment real ids appeared, which is what the hook was for.
        """
        now = now or datetime.now(timezone.utc).isoformat()
        sql = (
            "SELECT r.id FROM runs r WHERE r.state = 'queued' "
            # NOT EXISTS rather than a join: Postgres refuses FOR UPDATE over
            # the nullable side of an outer join (the same trap
            # claim_due_outbox_elements documents), and this has to keep
            # working against the real backend, not only against SQLite.
            "  AND (r.requested_by = '' OR NOT EXISTS ("
            "        SELECT 1 FROM runs busy WHERE busy.requested_by = r.requested_by "
            f"        AND busy.state IN ({','.join('?' * len(self._RUN_ACTIVE_STATES))}))) "
        )
        params: list = list(self._RUN_ACTIVE_STATES)
        if kinds:
            sql += f"  AND r.kind IN ({','.join('?' * len(kinds))}) "
            params.extend(kinds)
        sql += "ORDER BY r.enqueued_at ASC, r.id ASC LIMIT 1"

        with self._conn() as conn:
            if conn.is_postgres:
                sql += " FOR UPDATE SKIP LOCKED"
            row = conn.execute(sql, tuple(params)).fetchone()
            if row is None:
                return None
            run_id = row["id"]
            conn.execute(
                """UPDATE runs SET state='claimed', claimed_by=?, runner=?,
                                   claimed_at=?, heartbeat_at=?
                   WHERE id = ? AND state = 'queued'""",
                (claimed_by, json.dumps(runner or {}), now, now, run_id),
            )
            claimed = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(claimed) if claimed else None

    def mark_run_running(self, run_id: str, *, now: str | None = None) -> None:
        now = now or datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE runs SET state='running', started_at=?, heartbeat_at=? WHERE id=?",
                (now, now, run_id),
            )

    def heartbeat_run(self, run_id: str, *, now: str | None = None) -> bool:
        """Refresh a claimed/running row's heartbeat. False when the row is no
        longer active — which is how a heartbeat thread learns its run was
        reconciled or cancelled out from under it and stops."""
        now = now or datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                f"UPDATE runs SET heartbeat_at=? WHERE id=? AND state IN "  # noqa: S608
                f"({','.join('?' * len(self._RUN_ACTIVE_STATES))})",
                (now, run_id, *self._RUN_ACTIVE_STATES),
            )
            return bool(getattr(cur, "rowcount", 0))

    def finish_run(
        self,
        run_id: str,
        state: str,
        *,
        error: str = "",
        result_ref: str | None = None,
        now: str | None = None,
    ) -> None:
        """Write a terminal state. `result_ref` is left alone when not given —
        the enqueueing route usually set it already."""
        if state not in ("succeeded", "failed", "cancelled"):
            raise ValueError(f"{state!r} is not a terminal run state")
        now = now or datetime.now(timezone.utc).isoformat()
        sql = "UPDATE runs SET state=?, finished_at=?, error=?"
        params: list = [state, now, error]
        if result_ref is not None:
            sql += ", result_ref=?"
            params.append(result_ref)
        sql += " WHERE id=?"
        params.append(run_id)
        with self._conn() as conn:
            conn.execute(sql, tuple(params))

    def get_run(self, run_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def list_runs(
        self,
        *,
        state: str | None = None,
        kind: str | None = None,
        requested_by: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        sql = "SELECT * FROM runs"
        clauses, params = [], []
        if state:
            clauses.append("state = ?")
            params.append(state)
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if requested_by is not None:
            clauses.append("requested_by = ?")
            params.append(requested_by)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY enqueued_at DESC, id DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]

    def cancel_queued_run(self, run_id: str, *, now: str | None = None) -> bool:
        """Cancel a row that has NOT been claimed yet. Returns False otherwise.

        A claimed or running row is deliberately not cancellable here: it owns a
        real Python thread inside some worker process, and Python threads cannot
        be interrupted. Pretending otherwise would write `cancelled` over work
        that is still running and still writing findings — the same
        answer-shaped-non-answer `run_reconciler.py` exists to remove. If that
        worker dies, reconciliation resolves the row as failed with the reason;
        if it lives, the run finishes normally.
        """
        now = now or datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE runs SET state='cancelled', finished_at=?, "
                "error='cancelled before it was claimed' "
                "WHERE id=? AND state='queued'",
                (now, run_id),
            )
            return bool(getattr(cur, "rowcount", 0))

    def set_activity_runner(self, entry_id: str, runner: dict) -> None:
        """Merge `_runner` into an activity entry's `detail`, leaving the rest
        of it alone.

        Called when a queue worker actually starts the work, not when a route
        enqueued it. The distinction is the whole point: before the queue, the
        process that handled the request was the process that ran the survey, so
        recording the enqueuer's pid was recording the owner. Now they are
        different processes, and stamping the web server would make restarting
        the web server look like every in-flight run had been interrupted.

        A merge rather than an overwrite because `detail` already carries the
        run's own join keys (`analysis_id`, `survey_definition_ref`) that other
        readers depend on.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT detail FROM activity_log WHERE id = ?", (entry_id,)
            ).fetchone()
            if row is None:
                return
            try:
                detail = json.loads(row["detail"] or "{}") or {}
            except (TypeError, ValueError):
                # An unparseable detail is someone else's format, not ours to
                # replace — recording ownership must never destroy content.
                return
            if not isinstance(detail, dict):
                return
            detail["_runner"] = runner
            conn.execute(
                "UPDATE activity_log SET detail = ? WHERE id = ?",
                (json.dumps(detail), entry_id),
            )

    def stale_active_runs(self, cutoff_iso: str) -> list[dict]:
        """Claimed/running rows whose heartbeat is older than `cutoff_iso`.

        Candidates for reconciliation, not conclusions: whether each one is
        actually dead is decided by pid liveness in run_reconciler.py, never by
        the heartbeat alone. A worker paused by a slow Egeria call is late, not
        gone.
        """
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM runs WHERE state IN "  # noqa: S608
                f"({','.join('?' * len(self._RUN_ACTIVE_STATES))}) "
                f"AND heartbeat_at <> '' AND heartbeat_at < ? "
                f"ORDER BY enqueued_at ASC",
                (*self._RUN_ACTIVE_STATES, cutoff_iso),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_survey_definition_guid(
        self, entity_type: str, entity_slug: str, technology_type: str
    ) -> dict | None:
        """Return the cached {process_guid, process_qualified_name, cached_at} for a
        resource + technology type, or None if nothing is cached yet."""
        entity_slug = self._normalize_slug(entity_slug)
        with self._conn() as conn:
            row = conn.execute(
                """SELECT process_guid, process_qualified_name, cached_at
                   FROM survey_definition_cache
                   WHERE entity_type = ? AND entity_slug = ? AND technology_type = ?""",
                (entity_type, entity_slug, technology_type),
            ).fetchone()
        if row is None:
            return None
        return {
            "process_guid": row["process_guid"],
            "process_qualified_name": row["process_qualified_name"],
            "cached_at": row["cached_at"],
        }

    def set_survey_definition_guid(
        self,
        entity_type: str,
        entity_slug: str,
        technology_type: str,
        process_guid: str,
        process_qualified_name: str = "",
    ) -> None:
        """Cache the resolved GovernanceActionProcess GUID for a resource + technology type."""
        entity_slug = self._normalize_slug(entity_slug)
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO survey_definition_cache
                       (entity_type, entity_slug, technology_type, process_guid,
                        process_qualified_name, cached_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(entity_type, entity_slug, technology_type) DO UPDATE SET
                       process_guid = excluded.process_guid,
                       process_qualified_name = excluded.process_qualified_name,
                       cached_at = excluded.cached_at""",
                (
                    entity_type,
                    entity_slug,
                    technology_type,
                    process_guid,
                    process_qualified_name,
                    datetime.utcnow().isoformat(),
                ),
            )

    # ── generic runtime settings ("Discover repos to scout" plan, D1) ──────────

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO app_settings (key, value, updated_at)
                       VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                       value = excluded.value,
                       updated_at = excluded.updated_at""",
                (key, value, datetime.utcnow().isoformat()),
            )

    # ── per-call Egeria timing ────────────────────────────────────────────

    def record_egeria_call_timing(
        self, call_name: str, kind: str, seconds: float, params: dict | None = None,
    ) -> None:
        """One row per Egeria API call actually made — `kind` is `"write"`
        or `"query"`, never conflated (owner's ruling, 2026-09-15: an
        insert/update and a lookup are different cost populations, and
        averaging them under one number hides both). `params` is whatever
        the caller considers cost-relevant for THIS call (a query's
        `graph_query_depth`, a write's target type) -- stored, not
        interpreted here; `read_egeria_call_timing_stats` is what buckets by
        it. Best-effort by the caller's own convention throughout this
        module: never let a timing-recording failure fail the write it is
        timing (see `_publish_homepage_reference`'s docstring for the same
        argument made about a different best-effort site)."""
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO egeria_call_timings (call_name, kind, seconds, params_json, recorded_at)
                       VALUES (?, ?, ?, ?, ?)""",
                (call_name, kind, seconds, json.dumps(params or {}), datetime.utcnow().isoformat()),
            )

    def read_egeria_call_timing_stats(
        self, call_name: str, kind: str, *, param_filter: dict | None = None, sample: int = 500,
    ) -> dict:
        """`{count, median, p90}` (seconds) for `call_name`/`kind`, most
        recent `sample` rows. `param_filter` narrows to rows whose
        `params_json` matches every given key exactly (e.g.
        `{"graph_query_depth": 3}`) -- a query's cost depends on its own
        parameters, so mixing a depth-1 lookup with a depth-5 graph walk
        into one median would misprice both. `median`/`p90` are `None` when
        `count` is 0 -- never a fabricated number standing in for "nothing
        recorded yet"."""
        import statistics

        with self._conn() as conn:
            rows = conn.execute(
                """SELECT seconds, params_json FROM egeria_call_timings
                   WHERE call_name = ? AND kind = ?
                   ORDER BY id DESC LIMIT ?""",
                (call_name, kind, sample),
            ).fetchall()
        secs: list[float] = []
        for r in rows:
            if param_filter:
                try:
                    params = json.loads(r["params_json"] or "{}")
                except ValueError:
                    params = {}
                if any(params.get(k) != v for k, v in param_filter.items()):
                    continue
            secs.append(r["seconds"])
        if not secs:
            return {"count": 0, "median": None, "p90": None}
        secs.sort()
        p90_idx = min(len(secs) - 1, int(len(secs) * 0.9))
        return {"count": len(secs), "median": statistics.median(secs), "p90": secs[p90_idx]}

    def save_context(self, entity_type: str, entity_slug: str, context: dict) -> None:
        from datetime import timezone
        ts = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO resource_context (entity_type, entity_slug, ts, context_json)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(entity_type, entity_slug) DO UPDATE SET
                     ts=excluded.ts, context_json=excluded.context_json""",
                (entity_type, entity_slug, ts, json.dumps(context)),
            )

    def get_context(self, entity_type: str, entity_slug: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT context_json, ts FROM resource_context WHERE entity_type=? AND entity_slug=?",
                (entity_type, entity_slug),
            ).fetchone()
        if not row:
            return None
        ctx = json.loads(row["context_json"] or "{}")
        ctx["_saved_at"] = row["ts"]
        return ctx

    # ── Curate — tags, resource-level feedback, curator notes ──────────────────
    # Discoverability/reuse-readiness for a resource, distinct from Enrichment's
    # resource_context (facts about the resource). See web/routes/curate.py.

    def add_resource_tag(self, entity_type: str, entity_slug: str, tag: str) -> None:
        from datetime import timezone
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO resource_tags (entity_type, entity_slug, tag, created_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(entity_type, entity_slug, tag) DO NOTHING""",
                (entity_type, entity_slug, tag, datetime.now(timezone.utc).isoformat()),
            )

    def remove_resource_tag(self, entity_type: str, entity_slug: str, tag: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM resource_tags WHERE entity_type=? AND entity_slug=? AND tag=?",
                (entity_type, entity_slug, tag),
            )

    def list_resource_tags(self, entity_type: str, entity_slug: str) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT tag FROM resource_tags WHERE entity_type=? AND entity_slug=? ORDER BY tag",
                (entity_type, entity_slug),
            ).fetchall()
        return [r["tag"] for r in rows]

    def list_all_tags(self) -> list[dict]:
        """Distinct tags across all resources with their usage count — backs
        autocomplete and browse-by-tag ("making the resource easier to find")."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT tag, COUNT(*) as count FROM resource_tags GROUP BY tag ORDER BY tag"
            ).fetchall()
        return [{"tag": r["tag"], "count": r["count"]} for r in rows]

    def list_resources_by_tag(self, tag: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT entity_type, entity_slug FROM resource_tags WHERE tag=? ORDER BY entity_type, entity_slug",
                (tag,),
            ).fetchall()
        return [dict(r) for r in rows]

    def add_resource_feedback(
        self, entity_type: str, entity_slug: str, rating: int | None, category: str, message: str,
        *, compile_id: str | None = None,
    ) -> dict:
        from datetime import timezone
        entry = {
            "id": str(uuid.uuid4()),
            "entity_type": entity_type,
            "entity_slug": entity_slug,
            "rating": rating,
            "category": category,
            "message": message,
            "compile_id": compile_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO resource_feedback
                   (id, entity_type, entity_slug, rating, category, message, compile_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                tuple(entry.values()),
            )
        return entry

    def list_resource_feedback(self, entity_type: str, entity_slug: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM resource_feedback WHERE entity_type=? AND entity_slug=?
                   ORDER BY created_at DESC""",
                (entity_type, entity_slug),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_all_resource_feedback(self, limit: int = 200,
                                   entity_type: str = "",
                                   category: str = "") -> list[dict]:
        """Feedback across every resource, newest first.

        The per-resource reader (`list_resource_feedback`) answers "what did
        people say about this one", which is the right question from a resource
        page and the wrong one from Admin: nobody visits sixty resource pages to
        find out whether anything has been said at all. Feedback that can only
        be read where it was written is feedback nobody reads.

        `entity_type` deliberately defaults to empty rather than "repo".
        Feedback is recorded against databases and filesystems too, and a
        default that quietly showed one kind would make the other two look like
        they had none.
        """
        clauses, params = [], []
        if entity_type:
            clauses.append("entity_type = ?")
            params.append(entity_type)
        if category:
            clauses.append("category = ?")
            params.append(category)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(int(limit))
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM resource_feedback{where} "
                "ORDER BY created_at DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_all_resource_feedback(self) -> dict:
        """Totals for the Admin header: how much feedback exists, and on how
        many distinct resources. Both, because "40 items" and "40 items across
        3 resources" say different things about whether anyone is using it."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total, "
                "COUNT(DISTINCT entity_type || ':' || entity_slug) AS resources "
                "FROM resource_feedback"
            ).fetchone()
        return {"total": row["total"] or 0, "resources": row["resources"] or 0}

    def add_curator_note(self, entity_type: str, entity_slug: str, note: str) -> dict:
        """Ongoing curator commentary (discoverability, quality, readiness) —
        deliberately a separate stream from resource_context's 'notes' field,
        which is a one-time context-gathering fact, not a running log."""
        from datetime import timezone
        entry = {
            "id": str(uuid.uuid4()),
            "entity_type": entity_type,
            "entity_slug": entity_slug,
            "note": note,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO resource_curator_notes (id, entity_type, entity_slug, note, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                tuple(entry.values()),
            )
        return entry

    def list_curator_notes(self, entity_type: str, entity_slug: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM resource_curator_notes WHERE entity_type=? AND entity_slug=?
                   ORDER BY created_at DESC""",
                (entity_type, entity_slug),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_curator_note(self, note_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM resource_curator_notes WHERE id=?", (note_id,))
        return cur.rowcount > 0

    #: Valid architecture_component_verdicts.verdict values — kept here
    #: (rather than only in the route's Pydantic model) so a caller reading
    #: this module has one place to find the vocabulary.
    COMPONENT_VERDICTS = {"accepted", "rejected", "retyped"}

    #: Valid verdict values for a verdict_target='blueprint' row. No
    #: "retyped" — a component has a free-text `type` field a retype
    #: corrects (see materializer.py's module docstring); a cluster has no
    #: equivalent free-text field to retype. The route validates against
    #: whichever set matches the verdict_target it's recording.
    BLUEPRINT_VERDICTS = {"accepted", "rejected"}

    def record_component_verdict(
        self, entity_type: str, entity_slug: str, scope_locator: str,
        verdict: str, retyped_to: str = "", note: str = "",
        verdict_target: str = "component",
        decided_by: str | None = None,
    ) -> dict:
        """A curator's accept/reject/retype call on one proposed architecture
        component (docs/Backlog.md "take architecture results into Curate").

        Append-only, same convention as every other findings-shaped table in
        this codebase (query_findings, architecture_decisions): a new verdict
        is a new row, not an UPDATE. This is deliberate, not an oversight —
        the backlog entry's own constraint is that "a curator's verdict is
        evidence of a different kind, not a rewrite of what the detectors
        said" (§4.2 "map, never merge"), and that applies to a curator's OWN
        prior verdicts too: if someone accepts a component and later rejects
        it, both calls are real history, not a correction of a mistake.
        get_component_verdicts() reads back only the latest per scope;
        list_component_verdict_history() returns the full trail.

        scope_locator is the same join key architecture_recovery findings use
        (persist.py's scope_locator_for) — the component's path prefix — so
        the results reader can attach a verdict to a component by the same
        key it already groups on, with no separate identity to keep in sync.

        `verdict_target` distinguishes a single-component verdict
        ('component', the default — every caller before blueprint
        materialization existed) from a blueprint verdict ('blueprint') —
        see blueprint-materialization-plan.md's Decision 3. For a blueprint
        row, `scope_locator` is repurposed by the caller to hold
        f"{perspective}::{cluster_name}" rather than a real scope_locator;
        this method does not itself interpret the string, it just stores it.
        """
        valid = self.BLUEPRINT_VERDICTS if verdict_target == "blueprint" else self.COMPONENT_VERDICTS
        if verdict not in valid:
            raise ValueError(f"verdict must be one of {valid}, got {verdict!r}")
        from datetime import timezone
        entry = {
            "id": str(uuid.uuid4()),
            "entity_type": entity_type,
            "entity_slug": entity_slug,
            "scope_locator": scope_locator,
            "verdict": verdict,
            "retyped_to": retyped_to,
            "note": note,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "verdict_target": verdict_target,
            # Stamped here rather than passed in, for the same reason every
            # other user_id in this module is: a call site that has to remember
            # is a call site that will not.
            "decided_by": current_user_id() if decided_by is None else decided_by,
        }
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO architecture_component_verdicts
                   (id, entity_type, entity_slug, scope_locator, verdict, retyped_to, note,
                    created_at, verdict_target, decided_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                tuple(entry.values()),
            )
        return entry

    def get_component_verdicts(self, entity_type: str, entity_slug: str) -> dict[str, dict]:
        """{scope_locator: latest verdict row} for every component this
        resource has ever received a verdict on — the shape
        _architecture_recovery_results merges onto each component by `path`.
        One query, not one per component, same reasoning as
        query_finding_scopes' withdrawal check.

        Returns rows for BOTH verdict_target values mixed together — a
        caller that wants only 'blueprint' (or only 'component') rows
        filters client-side on the returned `verdict_target` field. Not
        worth a second query parameter for what is currently one filter
        (see curate.py's list_blueprint_verdicts)."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM architecture_component_verdicts
                   WHERE entity_type=? AND entity_slug=?
                   ORDER BY created_at ASC""",
                (entity_type, entity_slug),
            ).fetchall()
        # ASC-ordered, so the last write per scope_locator wins on overwrite —
        # same "newest instant" rule query_findings uses.
        latest: dict[str, dict] = {}
        for r in rows:
            latest[r["scope_locator"]] = dict(r)
        return latest

    def list_component_verdict_history(
        self, entity_type: str, entity_slug: str, scope_locator: str,
    ) -> list[dict]:
        """Every verdict ever recorded for one component, newest first —
        the full trail behind get_component_verdicts()'s single latest row,
        same relationship query_findings_all_runs has to query_findings."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM architecture_component_verdicts
                   WHERE entity_type=? AND entity_slug=? AND scope_locator=?
                   ORDER BY created_at DESC""",
                (entity_type, entity_slug, scope_locator),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_materialized_component(
        self, entity_type: str, entity_slug: str, scope_locator: str,
    ) -> dict | None:
        """Whether a real Egeria SolutionComponent already exists for this
        scope, or None. The idempotency check ComponentMaterializer runs
        before ever calling Egeria — a re-materialize (e.g. a curator
        re-accepting after re-running the underlying survey) must find the
        existing element, not create a duplicate. One row per scope: unlike
        verdicts, there is nothing append-only about "does this exist in
        Egeria" — it is a fact with one current answer, kept current by
        `record_materialized_component`'s INSERT OR REPLACE."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM architecture_materialized_components
                   WHERE entity_type=? AND entity_slug=? AND scope_locator=?""",
                (entity_type, entity_slug, scope_locator),
            ).fetchone()
        return dict(row) if row else None

    def get_materialized_components(self, entity_type: str, entity_slug: str) -> dict[str, dict]:
        """{scope_locator: row} for every component this resource has ever
        materialized — the bulk read the results reader merges onto each
        component alongside its verdict, one query for the whole resource
        rather than a per-component round trip. Mirrors
        get_component_verdicts' shape exactly; the only difference is that
        this table has no history to collapse (see
        get_materialized_component's docstring)."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM architecture_materialized_components
                   WHERE entity_type=? AND entity_slug=?""",
                (entity_type, entity_slug),
            ).fetchall()
        return {r["scope_locator"]: dict(r) for r in rows}

    def record_materialized_component(
        self, entity_type: str, entity_slug: str, scope_locator: str,
        qualified_name: str, guid: str,
    ) -> dict:
        """Record that `scope_locator` now has a real Egeria SolutionComponent.
        Keyed by (entity_type, entity_slug, scope_locator) as a natural key
        via INSERT OR REPLACE, not appended — see get_materialized_component's
        docstring for why this table's shape differs from the verdicts
        table it sits beside."""
        from datetime import timezone
        entry = {
            "id": str(uuid.uuid4()),
            "entity_type": entity_type,
            "entity_slug": entity_slug,
            "scope_locator": scope_locator,
            "qualified_name": qualified_name,
            "guid": guid,
            "materialized_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._conn() as conn:
            conn.execute(
                """DELETE FROM architecture_materialized_components
                   WHERE entity_type=? AND entity_slug=? AND scope_locator=?""",
                (entity_type, entity_slug, scope_locator),
            )
            conn.execute(
                """INSERT INTO architecture_materialized_components
                   (id, entity_type, entity_slug, scope_locator, qualified_name, guid, materialized_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                tuple(entry.values()),
            )
        return entry

    def get_materialized_blueprint(
        self, entity_type: str, entity_slug: str, perspective: str, cluster_name: str,
    ) -> dict | None:
        """Whether a real Egeria SolutionBlueprint already exists for this
        (perspective, cluster_name), or None. Byte-for-byte the same shape
        as get_materialized_component — see Decision 4 in
        blueprint-materialization-plan.md for why this is a parallel table
        rather than a scope_locator-keyed row in the existing one (a
        blueprint has no scope_locator)."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM architecture_materialized_blueprints
                   WHERE entity_type=? AND entity_slug=? AND perspective=? AND cluster_name=?""",
                (entity_type, entity_slug, perspective, cluster_name),
            ).fetchone()
        return dict(row) if row else None

    def get_materialized_blueprints(self, entity_type: str, entity_slug: str) -> dict[str, dict]:
        """{f"{perspective}::{cluster_name}": row} for every blueprint this
        resource has ever materialized — mirrors get_materialized_components'
        shape exactly, keyed the same way a blueprint verdict's scope_locator
        is repurposed to read (Decision 3)."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM architecture_materialized_blueprints
                   WHERE entity_type=? AND entity_slug=?""",
                (entity_type, entity_slug),
            ).fetchall()
        return {f"{r['perspective']}::{r['cluster_name']}": dict(r) for r in rows}

    def record_materialized_blueprint(
        self, entity_type: str, entity_slug: str, perspective: str, cluster_name: str,
        qualified_name: str, guid: str,
    ) -> dict:
        """Record that (perspective, cluster_name) now has a real Egeria
        SolutionBlueprint. Keyed by (entity_type, entity_slug, perspective,
        cluster_name) as a natural key via delete-then-insert, not appended —
        same "nothing append-only about does this exist in Egeria" reasoning
        as record_materialized_component."""
        from datetime import timezone
        entry = {
            "id": str(uuid.uuid4()),
            "entity_type": entity_type,
            "entity_slug": entity_slug,
            "perspective": perspective,
            "cluster_name": cluster_name,
            "qualified_name": qualified_name,
            "guid": guid,
            "materialized_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._conn() as conn:
            conn.execute(
                """DELETE FROM architecture_materialized_blueprints
                   WHERE entity_type=? AND entity_slug=? AND perspective=? AND cluster_name=?""",
                (entity_type, entity_slug, perspective, cluster_name),
            )
            conn.execute(
                """INSERT INTO architecture_materialized_blueprints
                   (id, entity_type, entity_slug, perspective, cluster_name, qualified_name, guid, materialized_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                tuple(entry.values()),
            )
        return entry

    def get_materialized_port(
        self, entity_type: str, entity_slug: str, scope_locator: str, port_name: str,
    ) -> dict | None:
        """Whether a real Egeria SolutionPort already exists for this
        (scope_locator, port_name), or None. Byte-for-byte the same shape
        as get_materialized_component/get_materialized_blueprint —
        PortMaterializer's own module docstring covers why this is a
        temporary workaround (Backlog.md item 8 / ISSUE-85: no
        create_solution_port anywhere)."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM architecture_materialized_ports
                   WHERE entity_type=? AND entity_slug=? AND scope_locator=? AND port_name=?""",
                (entity_type, entity_slug, scope_locator, port_name),
            ).fetchone()
        return dict(row) if row else None

    def get_materialized_ports(self, entity_type: str, entity_slug: str) -> dict[str, dict]:
        """{f"{scope_locator}::{port_name}": row} for every port this resource
        has ever materialized — mirrors get_materialized_blueprints' shape,
        same "no scope_locator of its own" reasoning applied to
        disambiguating multiple ports on one component instead."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM architecture_materialized_ports
                   WHERE entity_type=? AND entity_slug=?""",
                (entity_type, entity_slug),
            ).fetchall()
        return {f"{r['scope_locator']}::{r['port_name']}": dict(r) for r in rows}

    def record_materialized_port(
        self, entity_type: str, entity_slug: str, scope_locator: str, port_name: str,
        qualified_name: str, guid: str,
    ) -> dict:
        """Record that (scope_locator, port_name) now has a real Egeria
        SolutionPort. Keyed as a natural key via delete-then-insert, not
        appended — same "nothing append-only about does this exist in
        Egeria" reasoning as record_materialized_component/_blueprint."""
        from datetime import timezone
        entry = {
            "id": str(uuid.uuid4()),
            "entity_type": entity_type,
            "entity_slug": entity_slug,
            "scope_locator": scope_locator,
            "port_name": port_name,
            "qualified_name": qualified_name,
            "guid": guid,
            "materialized_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._conn() as conn:
            conn.execute(
                """DELETE FROM architecture_materialized_ports
                   WHERE entity_type=? AND entity_slug=? AND scope_locator=? AND port_name=?""",
                (entity_type, entity_slug, scope_locator, port_name),
            )
            conn.execute(
                """INSERT INTO architecture_materialized_ports
                   (id, entity_type, entity_slug, scope_locator, port_name, qualified_name, guid, materialized_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                tuple(entry.values()),
            )
        return entry

    _SCHEDULE_DAYS: dict[str, int] = {"daily": 1, "weekly": 7, "monthly": 30}

    def _next_run_iso(self, schedule: str, enabled: bool) -> str:
        """Compute the ISO timestamp for the next scheduled run, or '' for manual/disabled."""
        from datetime import timezone, timedelta
        if not enabled or schedule not in self._SCHEDULE_DAYS:
            return ""
        return (datetime.now(timezone.utc) + timedelta(days=self._SCHEDULE_DAYS[schedule])).isoformat()

    def save_schedule(
        self,
        entity_type: str,
        entity_slug: str,
        analysis_id: str,
        schedule: str,
        enabled: bool = True,
        target_kind: str = TARGET_ANALYSIS,
    ) -> None:
        """Schedule an analysis or a Survey Definition.

        `analysis_id` is the target reference either way — an analysis id, or a
        survey's qualified name when target_kind is "survey". Named for its
        original meaning because renaming it would reach through the schedules
        API, its routes and the frontend for no behavioural gain; the docstring
        carries what the name no longer does.
        """
        if target_kind not in (TARGET_ANALYSIS, TARGET_SURVEY):
            raise ValueError(
                f"unknown target_kind {target_kind!r}; expected "
                f"{TARGET_ANALYSIS!r} or {TARGET_SURVEY!r}")
        next_run = self._next_run_iso(schedule, enabled)
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO resource_schedules
                   (entity_type, entity_slug, analysis_id, schedule, enabled,
                    next_run, target_kind)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(entity_type, entity_slug, analysis_id) DO UPDATE SET
                     schedule=excluded.schedule, enabled=excluded.enabled,
                     next_run=excluded.next_run,
                     target_kind=excluded.target_kind""",
                (entity_type, entity_slug, analysis_id, schedule, int(enabled),
                 next_run, target_kind),
            )

    def update_schedule_after_run(
        self,
        entity_type: str,
        entity_slug: str,
        analysis_id: str,
        status: str = "ok",
        activity_id: str = "",
    ) -> None:
        """Record last_run = now, advance next_run by the configured interval,
        and record the outcome (status/activity_id) of that run — see the
        resource_schedules migration comment for why."""
        from datetime import timezone
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT schedule FROM resource_schedules "
                "WHERE entity_type=? AND entity_slug=? AND analysis_id=?",
                (entity_type, entity_slug, analysis_id),
            ).fetchone()
            if not row:
                return
            next_run = self._next_run_iso(row["schedule"], enabled=True)
            conn.execute(
                "UPDATE resource_schedules SET last_run=?, next_run=?, "
                "last_run_status=?, last_run_activity_id=? "
                "WHERE entity_type=? AND entity_slug=? AND analysis_id=?",
                (now, next_run, status, activity_id, entity_type, entity_slug, analysis_id),
            )

    def get_schedules(self, entity_type: str, entity_slug: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM resource_schedules WHERE entity_type=? AND entity_slug=?",
                (entity_type, entity_slug),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_all_schedules(self) -> list[dict]:
        """Every scheduled analysis across every resource — backs the Admin
        Schedules overview. Errors first (whatever needs follow-up surfaces
        immediately), then soonest-due, so the dashboard reads as a triage
        list rather than an alphabetical dump."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM resource_schedules
                   ORDER BY CASE WHEN last_run_status = 'error' THEN 0 ELSE 1 END,
                            next_run ASC"""
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_schedule(self, entity_type: str, entity_slug: str, analysis_id: str) -> bool:
        """Remove a schedule entirely — returns False if none existed."""
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM resource_schedules WHERE entity_type=? AND entity_slug=? AND analysis_id=?",
                (entity_type, entity_slug, analysis_id),
            )
        return cur.rowcount > 0

    def pause_schedules(self, entity_type: str, entity_slug: str) -> list[dict]:
        """Disable every enabled schedule on one resource and return what was
        disabled (analysis_id, next_run) so `resume_schedules` can put it back
        exactly. For an experiment window: on 2026-09-11 the nightly
        RepoRefreshSurvey re-surveyed two of three experiment repos while a run
        was answering, so its rows described two states. Pausing and restoring
        keeps the schedule's own cadence — `save_schedule(enabled=True)` would
        recompute next_run from now and silently shift it by a period."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT analysis_id, next_run FROM resource_schedules "
                "WHERE entity_type=? AND entity_slug=? AND enabled=1",
                (entity_type, entity_slug),
            ).fetchall()
            saved = [dict(r) for r in rows]
            for r in saved:
                conn.execute(
                    "UPDATE resource_schedules SET enabled=0 "
                    "WHERE entity_type=? AND entity_slug=? AND analysis_id=?",
                    (entity_type, entity_slug, r["analysis_id"]),
                )
        return saved

    def resume_schedules(self, entity_type: str, entity_slug: str, saved: list[dict]) -> int:
        """Re-enable the schedules `pause_schedules` returned, with their
        original next_run. A next_run already in the past simply becomes due,
        which is the scheduler's normal case after downtime."""
        n = 0
        with self._conn() as conn:
            for r in saved:
                cur = conn.execute(
                    "UPDATE resource_schedules SET enabled=1, next_run=? "
                    "WHERE entity_type=? AND entity_slug=? AND analysis_id=?",
                    (r.get("next_run") or "", entity_type, entity_slug, r["analysis_id"]),
                )
                n += cur.rowcount
        return n

    def get_due_schedules(self) -> list[dict]:
        """Return all enabled schedules that are past their next_run time."""
        from datetime import timezone
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM resource_schedules WHERE enabled=1 AND next_run != '' AND next_run <= ?",
                (now,),
            ).fetchall()
        return [dict(r) for r in rows]

    def add(self, project: Project) -> None:
        data = {
            **asdict(project),
            "collections": json.dumps(project.collections),
            "extra_docs_paths": json.dumps(project.extra_docs_paths),
        }
        data["slug"] = self._normalize_slug(data["slug"])
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO projects (
                    slug, display_name, github_url, description, homepage_url,
                    docs_url, github_token_encrypted, collections, status,
                    last_indexed_at, last_stats_fetched_at, last_commit_sha,
                    last_surveyed_at, last_profiled_at, created_at, error_message, subproject_path,
                    parent_slug, extra_docs_paths, egeria_asset_guid,
                    governance_state, group_slug
                ) VALUES (
                    :slug, :display_name, :github_url, :description, :homepage_url,
                    :docs_url, :github_token_encrypted, :collections, :status,
                    :last_indexed_at, :last_stats_fetched_at, :last_commit_sha,
                    :last_surveyed_at, :last_profiled_at, :created_at, :error_message, :subproject_path,
                    :parent_slug, :extra_docs_paths, :egeria_asset_guid,
                    :governance_state, :group_slug
                )""",
                data,
            )
        if project.github_url:
            self._reconcile_disposition_on_import(project.github_url, data["slug"])

    def _reconcile_disposition_on_import(self, github_url: str, real_slug: str) -> None:
        """A repo's disposition can be set before it's ever imported (a
        discovery-search candidate), keyed provisionally by a github_url-
        derived slug (`resolve_repo_entity_slug`'s fallback). If it's later
        imported under a genuinely different slug — a manual override, or a
        collision-avoidance rename — that provisional row is now orphaned:
        nothing at the real slug, and a stale one still sitting under the
        guess. Re-key it here, at the one place every import path (org
        importer, CLI, manual "add project") funnels through.

        A no-op in the overwhelmingly common case (no row was ever set for
        this repo pre-import, or the guessed slug already matches). Merges
        rather than overwrites if a row somehow already exists at
        real_slug too (shouldn't happen — real_slug wasn't registered a
        moment ago — but favor the already-real row over the guess rather
        than raise, since this runs inside `add()`'s success path and a
        disposition mismatch is not a reason to fail the import)."""
        from resource_explorer.github.org_importer import _url_to_slug

        guessed_slug = _url_to_slug(github_url)
        if guessed_slug == real_slug:
            return
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT 1 FROM repo_dispositions WHERE entity_type = 'repo' AND entity_slug = ?",
                (guessed_slug,),
            ).fetchone()
            if not existing:
                return
            real_exists = conn.execute(
                "SELECT 1 FROM repo_dispositions WHERE entity_type = 'repo' AND entity_slug = ?",
                (real_slug,),
            ).fetchone()
            if real_exists:
                # The real slug already has its own disposition row (set
                # directly against it, somehow, before this import) — leave
                # it alone and drop the stale guess rather than clobber it.
                conn.execute(
                    "DELETE FROM repo_dispositions WHERE entity_type = 'repo' AND entity_slug = ?",
                    (guessed_slug,),
                )
            else:
                conn.execute(
                    "UPDATE repo_dispositions SET entity_slug = ?, project_slug = ? "
                    "WHERE entity_type = 'repo' AND entity_slug = ?",
                    (real_slug, real_slug, guessed_slug),
                )
            conn.execute(
                "UPDATE repo_disposition_history SET entity_slug = ? "
                "WHERE entity_type = 'repo' AND entity_slug = ?",
                (real_slug, guessed_slug),
            )
        log.info(
            "registry: reconciled disposition for %s from provisional slug '%s' to real slug '%s'",
            github_url, guessed_slug, real_slug,
        )

    @staticmethod
    def _normalize_slug(slug: str) -> str:
        return slug.replace("-", "_").lower()

    def get(self, slug: str) -> Project | None:
        normalized = self._normalize_slug(slug)
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM projects WHERE slug = ?", (normalized,)).fetchone()
        return self._row_to_project(row) if row else None

    def list_all(self, governance_state: str | None = None) -> list[Project]:
        with self._conn() as conn:
            query = "SELECT * FROM projects WHERE 1=1"
            params = []
            if governance_state:
                query += " AND governance_state = ?"
                params.append(governance_state)
            query += " ORDER BY display_name"
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_project(r) for r in rows]

    def update_status(self, slug: str, status: ProjectStatus, error: str = "") -> None:
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            conn.execute(
                "UPDATE projects SET status = ?, error_message = ? WHERE slug = ?",
                (status.value, error, slug),
            )

    def update_indexed_at(self, slug: str, collections: list[str]) -> None:
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            conn.execute(
                "UPDATE projects SET last_indexed_at = ?, collections = ? WHERE slug = ?",
                (datetime.utcnow().isoformat(), json.dumps(collections), slug),
            )

    def update_extra_docs_paths(self, slug: str, paths: list[str]) -> None:
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            conn.execute(
                "UPDATE projects SET extra_docs_paths = ? WHERE slug = ?",
                (json.dumps(paths), slug),
            )

    def update_commit_sha(self, slug: str, sha: str) -> None:
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            conn.execute(
                "UPDATE projects SET last_commit_sha = ? WHERE slug = ?",
                (sha, slug),
            )

    def update_project_surveyed_at(self, slug: str) -> None:
        """Update the last_surveyed_at timestamp for a repo — mirrors
        update_database_surveyed_at(). Any survey (coarse scan or deep),
        not just a full one, counts as "surveyed"."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            conn.execute(
                "UPDATE projects SET last_surveyed_at = ? WHERE slug = ?",
                (datetime.utcnow().isoformat(), slug),
            )

    def update_project_profiled_at(self, slug: str) -> None:
        """Update the last_profiled_at timestamp — set by a successful
        Coarse Profile refresh (IngestionPipeline.refresh_profile()), the
        one Scouting-tier action with no staleness signal at all before
        this existed. Mirrors update_project_surveyed_at()'s pattern."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            conn.execute(
                "UPDATE projects SET last_profiled_at = ? WHERE slug = ?",
                (datetime.utcnow().isoformat(), slug),
            )

    def update_project_homepage(self, slug: str, homepage_url: str) -> None:
        """Record the project's external website — set by HomepageSurveyor.

        Deliberately writes `projects.homepage_url` rather than the `homepage`
        column on project_stats: that column is StatsFetcher's, refreshed
        wholesale from the GitHub API on every stats run, so a value the
        surveyor *derived* (from pyproject/package.json/README when GitHub's own
        field is empty) would be silently discarded on the next fetch. Keeping
        the derived answer on the project row means the two sources coexist —
        GitHub's declared homepage stays authoritative in stats, and this holds
        the best answer we have however it was found.
        """
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            conn.execute(
                "UPDATE projects SET homepage_url = ? WHERE slug = ?",
                (homepage_url, slug),
            )

    def update_github_url(self, slug: str, github_url: str) -> None:
        """Change which repository this slug points at.

        SQL-only — does not touch pgvector or reset collections/
        last_indexed_at. Callers MUST go through repair.change_github_url()
        instead of calling this directly: the old content still sits in
        this repo's collections, embedded from the repo the URL *used* to
        point at, and nothing here makes that stale state unreachable. See
        repair.py's docstring for why that has to be a forced invalidation
        or a refusal, never silent.
        """
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            conn.execute(
                "UPDATE projects SET github_url = ? WHERE slug = ?",
                (github_url, slug),
            )

    def invalidate_indexing(self, slug: str) -> None:
        """Reset a repo to the same "not yet indexed" state a freshly-added
        repo starts in — collections=[], last_indexed_at="". Used by
        repair.change_github_url() after the old collections have been
        dropped, so the repo doesn't keep advertising content that came
        from a URL it no longer points at. Reuses the existing "never
        indexed" representation rather than inventing a new status value,
        so every UI path that already knows how to say "index this repo"
        handles the post-URL-change state for free.
        """
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            conn.execute(
                "UPDATE projects SET collections = '[]', last_indexed_at = '' WHERE slug = ?",
                (slug,),
            )

    def update_ingestion_stats(self, slug: str, file_count: int, lines_of_code: int) -> None:
        """Update the most recent project_stats row with counts from actual ingestion."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM project_stats WHERE project_slug = ? ORDER BY id DESC LIMIT 1",
                (slug,),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE project_stats SET ingestion_file_count = ?, ingestion_lines_of_code = ? WHERE id = ?",
                    (file_count, lines_of_code, row["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO project_stats (project_slug, fetched_at, ingestion_file_count, ingestion_lines_of_code) "
                    "VALUES (?, ?, ?, ?)",
                    (slug, datetime.utcnow().isoformat(), file_count, lines_of_code),
                )

    def upsert_code_symbols(self, resource_slug: str, symbols: list) -> None:
        """Insert or replace extracted code symbols, and derive+write
        inherits_from relationship rows from each class-kind symbol's
        `bases` list in the same call — mirroring Egeria Advisor's own
        CodeSymbolStore.upsert_symbols(), which does both in one pass (see
        AST-ownership-transfer plan Phase 3). symbols is a list of
        CodeSymbol dataclasses (resource_explorer/ingestion/code_symbol_extractor.py).
        """
        if not symbols:
            return
        slug = self._normalize_slug(resource_slug)
        rows = [
            (
                slug, s.file_path, s.language, s.kind, s.name, s.qualified_name,
                s.signature, s.docstring, "", s.start_line, s.end_line,
                s.parent_class, s.return_type, int(s.is_private), int(s.is_async), s.complexity,
            )
            for s in symbols
        ]
        relationships = [
            (slug, "inherits_from", s.qualified_name, base)
            for s in symbols if s.kind == "class"
            for base in (s.bases or [])
        ]
        with self._conn() as conn:
            conn.executemany(
                """INSERT INTO project_code_symbols
                   (project_slug, file_path, language, kind, name, qualified_name,
                    signature, docstring, summary, start_line, end_line,
                    parent_class, return_type, is_private, is_async, complexity)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(project_slug, file_path, qualified_name)
                   DO UPDATE SET
                     language=excluded.language, kind=excluded.kind, name=excluded.name,
                     signature=excluded.signature, docstring=excluded.docstring,
                     start_line=excluded.start_line, end_line=excluded.end_line,
                     parent_class=excluded.parent_class, return_type=excluded.return_type,
                     is_private=excluded.is_private, is_async=excluded.is_async,
                     complexity=excluded.complexity""",
                rows,
            )
            if relationships:
                conn.executemany(
                    """INSERT INTO project_code_relationships
                       (project_slug, relationship_type, source_name, target_name)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(project_slug, relationship_type, source_name, target_name)
                       DO NOTHING""",
                    relationships,
                )

    def clear_code_symbols(self, resource_slug: str, language: str | None = None) -> None:
        """Remove symbol (and, when clearing a whole project, relationship)
        entries. Relationships aren't language-tagged themselves, so a
        language-filtered clear leaves them alone — they get reconciled
        naturally on the next full upsert_code_symbols() call for that
        project via ON CONFLICT DO NOTHING (stale target names, e.g. from a
        renamed/removed base class, are a known accepted gap — see migration
        plan decision D3, matching Egeria Advisor's own equivalent behavior)."""
        slug = self._normalize_slug(resource_slug)
        with self._conn() as conn:
            if language:
                conn.execute(
                    "DELETE FROM project_code_symbols WHERE project_slug = ? AND language = ?",
                    (slug, language),
                )
            else:
                conn.execute(
                    "DELETE FROM project_code_symbols WHERE project_slug = ?", (slug,)
                )
                conn.execute(
                    "DELETE FROM project_code_relationships WHERE project_slug = ?", (slug,)
                )

    def upsert_code_markers(self, resource_slug: str, markers: list) -> None:
        """Insert extracted code markers (route/annotation registrations —
        DESIGN-INTERFACE-SURFACE-IMPLEMENTED-RUNG.md §3.1). markers is a list
        of CodeMarker dataclasses (resource_explorer/ingestion/
        code_symbol_extractor.py). No UNIQUE constraint / ON CONFLICT here,
        unlike project_code_symbols: one symbol legitimately carries several
        registrations (`@app.get(...)` and `@app.post(...)` on the same
        handler), so plain INSERT after clear_code_markers()'s per-language
        DELETE is the whole rewrite — no upsert to merge."""
        if not markers:
            return
        slug = self._normalize_slug(resource_slug)
        rows = [
            (
                slug, m.file_path, m.start_line, m.language, m.marker_kind,
                m.framework, m.interface_kind, m.detail, m.qualified_name,
            )
            for m in markers
        ]
        with self._conn() as conn:
            conn.executemany(
                """INSERT INTO project_code_markers
                   (project_slug, file_path, start_line, language, marker_kind,
                    framework, interface_kind, detail, qualified_name)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )

    def clear_code_markers(self, resource_slug: str, language: str | None = None) -> None:
        """Remove marker rows — mirrors clear_code_symbols exactly (delete-
        then-reinsert per language keeps a rerun from accumulating stale
        duplicate markers)."""
        slug = self._normalize_slug(resource_slug)
        with self._conn() as conn:
            if language:
                conn.execute(
                    "DELETE FROM project_code_markers WHERE project_slug = ? AND language = ?",
                    (slug, language),
                )
            else:
                conn.execute(
                    "DELETE FROM project_code_markers WHERE project_slug = ?", (slug,)
                )

    def get_code_markers(self, resource_slug: str, interface_kind: str | None = None) -> list[dict]:
        """Marker rows for a project, optionally filtered to one
        interface_kind — interface_surface's primary read path."""
        slug = self._normalize_slug(resource_slug)
        with self._conn() as conn:
            if interface_kind:
                rows = conn.execute(
                    "SELECT file_path, start_line, language, marker_kind, framework, "
                    "interface_kind, detail, qualified_name FROM project_code_markers "
                    "WHERE project_slug = ? AND interface_kind = ?",
                    (slug, interface_kind),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT file_path, start_line, language, marker_kind, framework, "
                    "interface_kind, detail, qualified_name FROM project_code_markers "
                    "WHERE project_slug = ?",
                    (slug,),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_code_symbol_file_paths(self, resource_slug: str) -> list[str]:
        """Distinct file paths with at least one indexed code symbol —
        D2(c) (docs/repo-survey-catalog-completion-plan.md): a confirmed
        duplicate, hand-rolled independently by both
        sub_surveyors/file_structure.py (top-level directory breakdown) and
        sub_surveyors/security_hygiene.py (security-file presence check)
        before this method existed."""
        slug = self._normalize_slug(resource_slug)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT file_path FROM project_code_symbols WHERE project_slug = ?",
                (slug,),
            ).fetchall()
        return [r["file_path"] for r in rows]

    def get_code_relationships(
        self, resource_slug: str, relationship_type: str = "inherits_from"
    ) -> list[dict]:
        """All relationship rows for a project, e.g. for CodeIntelAgent-style
        inheritance/hierarchy queries."""
        slug = self._normalize_slug(resource_slug)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT source_name, target_name FROM project_code_relationships "
                "WHERE project_slug = ? AND relationship_type = ?",
                (slug, relationship_type),
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_contributor_stats(self, resource_slug: str, rows: list[dict]) -> None:
        """Insert or replace aggregated per-contributor stats for a time period."""
        if not rows:
            return
        slug = self._normalize_slug(resource_slug)
        with self._conn() as conn:
            conn.executemany(
                """INSERT INTO project_contributor_stats
                   (project_slug, period_start, period_end, author_email, author_name,
                    commits, additions, deletions, tier)
                   VALUES (:project_slug, :period_start, :period_end, :author_email, :author_name,
                           :commits, :additions, :deletions, :tier)
                   ON CONFLICT(project_slug, period_start, period_end, author_email)
                   DO UPDATE SET
                     author_name=excluded.author_name,
                     commits=excluded.commits,
                     additions=excluded.additions,
                     deletions=excluded.deletions,
                     tier=excluded.tier""",
                [{**r, "project_slug": slug} for r in rows],
            )

    def remove(self, slug: str) -> None:
        normalized = self._normalize_slug(slug)
        with self._conn() as conn:
            # Children before parent — every table below has a real FK to
            # projects.slug, which SQLite silently ignores by default
            # (foreign_keys pragma is off) but Postgres enforces. This was
            # ALSO incomplete on SQLite itself: project_dependencies,
            # project_file_type_counts, project_file_inventory,
            # project_egeria_surveys, and project_data_profiles were never
            # cleaned up here at all, silently leaking orphaned rows on every
            # remove() — invisible on SQLite (no FK enforcement to surface
            # it), a hard FK-violation crash on Postgres. Found live during
            # Phase 4 Postgres cutover testing; fixed for both backends.
            conn.execute("DELETE FROM project_stats WHERE project_slug = ?", (normalized,))
            conn.execute("DELETE FROM project_commits WHERE project_slug = ?", (normalized,))
            conn.execute("DELETE FROM project_code_symbols WHERE project_slug = ?", (normalized,))
            conn.execute("DELETE FROM project_code_relationships WHERE project_slug = ?", (normalized,))
            conn.execute("DELETE FROM project_code_markers WHERE project_slug = ?", (normalized,))
            conn.execute("DELETE FROM project_aliases WHERE project_slug = ?", (normalized,))
            conn.execute("DELETE FROM project_contributor_stats WHERE project_slug = ?", (normalized,))
            conn.execute("DELETE FROM project_dependencies WHERE project_slug = ?", (normalized,))
            conn.execute("DELETE FROM project_file_type_counts WHERE project_slug = ?", (normalized,))
            conn.execute("DELETE FROM project_file_inventory WHERE project_slug = ?", (normalized,))
            conn.execute("DELETE FROM project_egeria_surveys WHERE project_slug = ?", (normalized,))
            conn.execute("DELETE FROM project_data_profiles WHERE project_slug = ?", (normalized,))
            # The generic analysis tables (analysis-kind extensibility redesign)
            # were added after the comment above was written and never added
            # here — the same omission recurring on the newer tables. Found
            # 2026-08-23 by the real-Postgres registry tier
            # (tests/test_integration_registry_pg.py) on its first run: every
            # project that has ever been surveyed has rows in at least one of
            # these, so remove() raised ForeignKeyViolation on Postgres for
            # effectively every real project — including via Admin's bulk
            # "delete locally". Invisible on SQLite even with the pragma on,
            # because these two DELETEs were simply missing rather than
            # mis-ordered. test_remove_deletes_from_every_table_with_a_fk
            # now enumerates the FKs so a future table cannot be forgotten.
            conn.execute("DELETE FROM project_analysis_findings WHERE project_slug = ?", (normalized,))
            conn.execute("DELETE FROM project_analysis_metrics WHERE project_slug = ?", (normalized,))
            # analysis_gaps (gaps.py, added alongside destinations.py) — same
            # omission this whole block documents, caught this time by
            # test_remove_deletes_from_every_table_with_a_fk on first run
            # rather than live, because that test now enumerates every FK to
            # projects.slug instead of relying on someone remembering to add
            # a line here.
            conn.execute("DELETE FROM analysis_gaps WHERE project_slug = ?", (normalized,))
            # The four superseded per-kind tables from Phase B. The analysis-kind
            # redesign (D3) deliberately kept them rather than dropping them, for
            # a soak period — so they still exist, still carry a FK, and still
            # hold data (measured 2026-08-23: 6 security-finding rows, 4
            # documentation-finding rows, 2 api-structure snapshots). Deprecated
            # is not gone: while the table exists, remove() has to clear it.
            conn.execute("DELETE FROM projects WHERE slug = ?", (normalized,))

    # Every table with a project_slug column, in the order the FK-declared
    # ones (see grep for `REFERENCES projects(slug)` — 17 of the 20 below)
    # need to be touched relative to the `projects` row itself: AFTER a new
    # `projects` row exists for new_slug, since a bare UPDATE of a child row
    # to project_slug=new_slug while only old_slug's parent row exists would
    # fail the FK check on Postgres. Doing the parent-row swap first (insert
    # new, repoint children, delete old) sidesteps needing DEFERRABLE
    # constraints or a temporary FK disable — neither of which this schema
    # declares. The three without a declared FK (project_published_
    # annotation_types, project_published_analyses, repo_dispositions) are
    # included anyway: rename must not silently orphan their rows just
    # because nothing enforces referential integrity on them.
    _PROJECT_SLUG_TABLES: tuple[str, ...] = (
        "project_stats", "project_commits", "project_code_symbols",
        "project_code_relationships", "project_code_markers", "project_aliases",
        "project_contributor_stats", "conversation_history", "context_compiles",
        "project_dependencies", "project_file_type_counts",
        "project_file_inventory", "project_egeria_surveys",
        "project_published_annotation_types", "project_published_analyses",
        "project_data_profiles", "project_analysis_findings",
        "project_analysis_metrics", "analysis_gaps",
    )

    # Every table keyed by (entity_type, entity_slug) that can carry
    # entity_type='repo' rows. Unlike the tables above these have no FK to
    # `projects` at all (entity_type also covers 'database'/'filesystem'/
    # other kinds), so they can be updated in any order relative to the
    # `projects` row swap.
    _ENTITY_SLUG_TABLES: tuple[str, ...] = (
        "activity_log", "resource_context", "resource_tags",
        "resource_feedback", "resource_curator_notes", "resource_schedules",
        "survey_definition_cache", "egeria_linkage_status",
        "resource_working_set", "entity_egeria_project_context",
        "working_set_members", "notification_subscriptions",
        "architecture_component_verdicts", "architecture_materialized_components",
        "architecture_materialized_blueprints", "architecture_materialized_ports",
        # Joined this list 2026-09-22, when disposition's PK generalized from
        # github_url alone to (entity_type, entity_slug) — see
        # `_migrate_repo_dispositions_to_entity_key`. `repo_dispositions`
        # ALSO keeps its own project_slug-column UPDATE just below (a
        # separate, informational field, not the key), since that predates
        # and is independent of this list.
        "repo_dispositions", "repo_disposition_history",
    )

    def rename_project_slug(self, old_slug: str, new_slug: str, *,
                            new_collections_json: str | None = None) -> dict:
        """Rename a repo's slug across every table that carries it — the
        SQL-only half of a repair-operation rename. Does NOT touch pgvector
        (see repair.py's rename_repo(), which renames the owned collection
        tables first and only calls this once those renames have all
        succeeded — so a failure here never leaves a collection pointing at
        a slug the registry no longer has).

        Refuses (ValueError, no writes) rather than silently merging when:
          - old_slug is not registered
          - new_slug is already registered (including old_slug == new_slug
            after normalization, which would otherwise be a same-row no-op
            that looks like success but changed nothing)

        `projects.slug` is the PK every child table's project_slug is a
        soft-FK to (17 of 20 with a declared constraint — see
        _PROJECT_SLUG_TABLES' comment), so the swap goes: insert a new
        `projects` row under new_slug (copying every column, with
        `collections` already rewritten by the caller to its
        renamed-collection-table names), repoint every child row from
        old_slug to new_slug, THEN delete the old `projects` row — never the
        reverse, or Postgres rejects the child UPDATEs with a dangling FK.
        """
        old = self._normalize_slug(old_slug)
        new = self._normalize_slug(new_slug)
        if not self.exists(old):
            raise ValueError(f"repo '{old_slug}' is not registered")
        if old == new:
            raise ValueError(f"new slug '{new}' is the same as the current slug")
        if self.exists(new):
            raise ValueError(f"a repo with slug '{new}' already exists")

        touched: dict[str, int] = {}
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM projects WHERE slug = ?", (old,)).fetchone()
            cols = [k for k in row.keys() if k != "slug"]
            values = [new] + [
                new_collections_json if (c == "collections" and new_collections_json is not None)
                else row[c]
                for c in cols
            ]
            placeholders = ", ".join(["?"] * len(values))
            conn.execute(
                f"INSERT INTO projects (slug, {', '.join(cols)}) VALUES ({placeholders})",
                values,
            )

            for table in self._PROJECT_SLUG_TABLES:
                cur = conn.execute(
                    f"UPDATE {table} SET project_slug = ? WHERE project_slug = ?", (new, old)
                )
                touched[table] = cur.rowcount
            for table in self._ENTITY_SLUG_TABLES:
                cur = conn.execute(
                    f"UPDATE {table} SET entity_slug = ? WHERE entity_type = 'repo' AND entity_slug = ?",
                    (new, old),
                )
                touched[table] = cur.rowcount
            cur = conn.execute(
                "UPDATE sub_resources SET resource_slug = ? WHERE resource_type = 'repo' AND resource_slug = ?",
                (new, old),
            )
            touched["sub_resources"] = cur.rowcount
            cur = conn.execute(
                "UPDATE repo_dispositions SET project_slug = ? WHERE project_slug = ?", (new, old)
            )
            touched["repo_dispositions"] = cur.rowcount
            # Sub-projects (subproject_path monorepo entries) point back at
            # their parent by slug — the parent row itself was already
            # re-inserted above, but children still say parent_slug=old.
            cur = conn.execute(
                "UPDATE projects SET parent_slug = ? WHERE parent_slug = ?", (new, old)
            )
            touched["projects.parent_slug"] = cur.rowcount

            conn.execute("DELETE FROM projects WHERE slug = ?", (old,))
        return touched

    # ── alias management ──────────────────────────────────────────────────────

    def add_alias(self, alias: str, slug: str, confirmed_by: str = "user") -> None:
        """Store an alias → slug mapping. Alias is normalized (lowercase, spaces→underscores)."""
        normalized = alias.lower().replace(" ", "_").replace("-", "_")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO project_aliases (alias, project_slug, confirmed_by, created_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT (alias) DO UPDATE SET "
                "project_slug = excluded.project_slug, confirmed_by = excluded.confirmed_by, "
                "created_at = excluded.created_at",
                (normalized, self._normalize_slug(slug), confirmed_by, datetime.utcnow().isoformat()),
            )

    def remove_alias(self, alias: str) -> bool:
        """Delete an alias. Returns True if a row was deleted."""
        normalized = alias.lower().replace(" ", "_").replace("-", "_")
        with self._conn() as conn:
            cursor = conn.execute("DELETE FROM project_aliases WHERE alias = ?", (normalized,))
            return cursor.rowcount > 0

    def resolve_alias(self, term: str) -> str | None:
        """Look up a term in the alias table; return the project slug or None."""
        normalized = term.lower().replace(" ", "_").replace("-", "_")
        with self._conn() as conn:
            row = conn.execute(
                "SELECT project_slug FROM project_aliases WHERE alias = ?", (normalized,)
            ).fetchone()
        return row["project_slug"] if row else None

    def list_aliases(self, slug: str | None = None) -> list[dict]:
        """Return all aliases, optionally filtered to a single project."""
        with self._conn() as conn:
            if slug:
                rows = conn.execute(
                    "SELECT alias, project_slug, confirmed_by, created_at FROM project_aliases "
                    "WHERE project_slug = ? ORDER BY alias",
                    (self._normalize_slug(slug),),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT alias, project_slug, confirmed_by, created_at FROM project_aliases "
                    "ORDER BY project_slug, alias"
                ).fetchall()
        return [dict(r) for r in rows]

    def fuzzy_candidate(self, query: str) -> tuple[str, str] | None:
        """
        Return (display_term, project_slug) if a phrase in the query fuzzy-matches a project.
        Uses difflib at 0.70 cutoff. Tries 1–4-word ngrams ordered longest-first.
        Returns None when no confident match exists.
        """
        import difflib
        q = query.lower()
        projects = self.list_all()
        candidates: dict[str, str] = {}  # normalized_name → slug
        for p in projects:
            candidates[p.slug.lower()] = p.slug
            if p.display_name:
                key = p.display_name.lower().replace("-", "_").replace(" ", "_")
                candidates[key] = p.slug
        if not candidates:
            return None
        words = q.split()
        for n in range(min(4, len(words)), 0, -1):
            for i in range(len(words) - n + 1):
                term = "_".join(words[i:i + n])
                matches = difflib.get_close_matches(term, list(candidates.keys()), n=1, cutoff=0.70)
                if matches:
                    display_term = " ".join(words[i:i + n])
                    return display_term, candidates[matches[0]]
        return None

    # ── file type counts (from surveyor) ─────────────────────────────────────

    def upsert_file_type_counts(
        self,
        slug: str,
        counts: dict[str, int],
        source: str = "extension",
        details: dict[str, dict] | None = None,
    ) -> None:
        """Append a new survey run's file-type counts. Historical runs are kept for trending.

        details: optional per-label breakdown dict (e.g. {"Other": {".toml": 5, ".yaml": 3}})
        """
        slug = self._normalize_slug(slug)
        surveyed_at = datetime.utcnow().isoformat()
        details = details or {}
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO project_file_type_counts "
                "(project_slug, surveyed_at, type_label, file_count, source, details_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (slug, surveyed_at, label, count, source,
                     json.dumps(details[label]) if label in details else None)
                    for label, count in counts.items()
                ],
            )

    def query_file_type_counts(self, slug: str) -> list[dict]:
        """Return the most recent survey run's file-type counts, ordered by count desc.
        Each row includes surveyed_at so callers can display when the survey ran."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            latest_ts = conn.execute(
                "SELECT MAX(surveyed_at) FROM project_file_type_counts WHERE project_slug = ?",
                (slug,),
            ).fetchone()[0]
            if not latest_ts:
                return []
            rows = conn.execute(
                "SELECT type_label, file_count, source, surveyed_at, details_json "
                "FROM project_file_type_counts "
                "WHERE project_slug = ? AND surveyed_at = ? ORDER BY file_count DESC",
                (slug, latest_ts),
            ).fetchall()
        return [dict(r) for r in rows]

    def query_file_type_history(self, slug: str) -> list[dict]:
        """Return one row per survey run with total file count and timestamp, for trending."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            # MAX(source), not a bare `source` — SQLite's lax GROUP BY (any
            # selected column not in GROUP BY silently picks *a* row's value)
            # let this slide; Postgres correctly rejects it. source is
            # expected constant per surveyed_at run, so MAX just reproduces
            # SQLite's old behavior explicitly, without changing the
            # docstring's "one row per survey run" grouping granularity by
            # also grouping on source.
            rows = conn.execute(
                "SELECT surveyed_at, SUM(file_count) as total_files, MAX(source) as source "
                "FROM project_file_type_counts WHERE project_slug = ? "
                "GROUP BY surveyed_at ORDER BY surveyed_at ASC",
                (slug,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── full file inventory ───────────────────────────────────────────────────

    def upsert_file_inventory(
        self,
        slug: str,
        paths_with_sizes: list[tuple[str, int]],
        modes_by_path: dict[str, str] | None = None,
    ) -> None:
        """Replace the file inventory for a project (called during add/refresh).

        modes_by_path is optional and additive — a caller with no git-tree
        mode data (e.g. a purely local filesystem walk) simply omits it and
        every row gets file_mode='' (Assessment sub-resource cataloging
        plan, D9 Tier 1)."""
        from resource_explorer.ingestion.vendored import provenance

        slug = self._normalize_slug(slug)
        indexed_at = datetime.utcnow().isoformat()
        modes_by_path = modes_by_path or {}
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM project_file_inventory WHERE project_slug = ?", (slug,)
            )
            conn.executemany(
                "INSERT INTO project_file_inventory "
                "(project_slug, file_path, file_size_bytes, indexed_at, file_mode, vendored) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (slug, path, size, indexed_at, modes_by_path.get(path, ""), provenance(path))
                    for path, size in paths_with_sizes
                ],
            )

    def file_inventory_summary(self, slug: str) -> dict:
        """{total, own, vendored, generated, short} — the honest number beside
        the misleading one, and WHICH kind of not-own: vendored is a
        provenance claim, generated is the repository's own build output.
        Both are 0 for a project indexed before 2026-09-11 until it is
        re-indexed; one indexed between then and 2026-09-12 reports both
        kinds as vendored. `indexed_at` says when."""
        from resource_explorer.ingestion.vendored import GENERATED, VENDORED
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total, "
                f"SUM(CASE WHEN COALESCE(vendored, 0) = {VENDORED} THEN 1 ELSE 0 END) AS vendored, "
                f"SUM(CASE WHEN COALESCE(vendored, 0) = {GENERATED} THEN 1 ELSE 0 END) AS generated, "
                "MAX(indexed_at) AS indexed_at "
                "FROM project_file_inventory WHERE project_slug = ?", (slug,)
            ).fetchone()
        row = dict(row) if not isinstance(row, dict) else row
        total = int(row.get("total") or 0)
        vendored = int(row.get("vendored") or 0)
        generated = int(row.get("generated") or 0)
        own = total - vendored - generated
        parts = [f"{total:,} files"]
        if vendored:
            parts.append(f"{vendored:,} vendored")
        if generated:
            parts.append(f"{generated:,} generated")
        return {"total": total, "own": own, "vendored": vendored, "generated": generated,
                "short": " · ".join(parts), "indexed_at": row.get("indexed_at") or ""}

    def get_file_inventory(self, slug: str) -> list[str]:
        """Return all file paths from the stored inventory for a project."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT file_path FROM project_file_inventory WHERE project_slug = ? AND COALESCE(vendored, 0) = 0",
                (slug,),
            ).fetchall()
        return [r["file_path"] for r in rows]

    def file_exists(self, slug: str, *candidate_paths: str) -> str | None:
        """Return the first of candidate_paths present in the file inventory
        (in the order given), or None if none exist. Assessment expansion
        plan B3 — an exact-filename lookup nothing in the registry offered
        before (get_file_inventory/get_file_inventory_with_sizes both
        return the whole per-repo list unfiltered); the table's existing
        UNIQUE(project_slug, file_path) constraint makes this an indexed,
        cheap point lookup rather than a full-list scan client-side."""
        if not candidate_paths:
            return None
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            placeholders = ",".join("?" * len(candidate_paths))
            rows = conn.execute(
                f"SELECT file_path FROM project_file_inventory "  # noqa: S608
                f"WHERE project_slug = ? AND COALESCE(vendored, 0) = 0 AND file_path IN ({placeholders})",
                (slug, *candidate_paths),
            ).fetchall()
        found = {r["file_path"] for r in rows}
        for path in candidate_paths:
            if path in found:
                return path
        return None

    def store_data_profiles(self, slug: str, profiles: list[dict]) -> None:
        """Upsert data-file profiles produced during ingestion.

        Each profile dict must have: file_path, format, row_count (int|None),
        col_count (int|None), schema_json (str|None), null_summary (str),
        file_size_bytes (int).
        """
        slug = self._normalize_slug(slug)
        profiled_at = datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.executemany(
                """INSERT INTO project_data_profiles
                   (project_slug, file_path, profiled_at, format,
                    row_count, col_count, schema_json, null_summary, file_size_bytes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(project_slug, file_path) DO UPDATE SET
                     profiled_at=excluded.profiled_at,
                     format=excluded.format,
                     row_count=excluded.row_count,
                     col_count=excluded.col_count,
                     schema_json=excluded.schema_json,
                     null_summary=excluded.null_summary,
                     file_size_bytes=excluded.file_size_bytes""",
                [
                    (
                        slug,
                        p["file_path"],
                        profiled_at,
                        p["format"],
                        p.get("row_count"),
                        p.get("col_count"),
                        p.get("schema_json"),
                        p.get("null_summary", ""),
                        p.get("file_size_bytes", 0),
                    )
                    for p in profiles
                ],
            )

    def get_data_profiles(self, slug: str) -> list[dict]:
        """Return all stored data-file profiles for a project."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT file_path, format, row_count, col_count,
                          schema_json, null_summary, file_size_bytes, profiled_at
                   FROM project_data_profiles WHERE project_slug = ?
                   ORDER BY file_size_bytes DESC""",
                (slug,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Generic per-analysis-kind findings/metrics storage ─────────────────
    # The two tables every NEW analysis kind should use — see the
    # analysis-kind extensibility redesign. `kind` is the discriminator
    # (e.g. "security_hygiene", "documentation", "data_profile",
    # "api_structure", and whatever's added next) — one physical table each,
    # not one per kind. Both still copy project_file_type_counts's append
    # pattern (no UNIQUE constraint, append-only, MAX(surveyed_at) = latest
    # batch) — only the "one table per kind" part changes, not the pattern
    # itself.

    def upsert_finding(
        self, slug: str, kind: str, findings: list[dict], surveyed_at: str | None = None,
        scope_locator: str = "", supersedes_previous: bool = False,
    ) -> None:
        """Append one survey run's findings for one analysis kind.
        findings: list of {check_name, label, summary="", confidence=100, detail: dict|None}.
        `check_name` is "what kind of check" (e.g. a security check name, a
        documentation finding_type); `label` is its value/classification
        (e.g. "pass"/"gap", or a quality label) — callers translate these
        back to whatever field names their own /results API response uses.
        scope_locator (repo scope-narrowing funnel plan, D5/D6): '' (default)
        = whole-resource, matching every pre-scope-aware caller unchanged;
        a non-empty value scopes this run's findings to one cataloged
        sub-resource, kept distinct from whole-resource findings under the
        same `kind` rather than mixed together.

        supersedes_previous (default False — opt-in per writer, docs/
        Backlog.md "a run that finds nothing retires the previous run's
        findings"): when True, this call is the caller's assertion that it
        IS the complete answer for (slug, kind, scope_locator) as of
        `surveyed_at`. Every earlier still-current row for that exact
        (slug, kind, scope_locator) is stamped `superseded_at = surveyed_at`
        BEFORE any new rows from this call are inserted — including when
        `findings` is empty, which is the whole point: the plain
        `if not findings: return` below writes nothing at all for a clean
        run, so query_findings() (which serves only the newest surveyed_at)
        keeps returning the previous run's rows forever. That is the exact
        defect found live 2026-09-13 in cve_scan: a 2026-09-01 run found a
        `click` advisory; the 2026-09-12 run found none, wrote zero rows,
        and the stale advisory kept answering as current for 12 days.
        `_architecture_doc_lens_results` (repo_survey_definition_adapter.py)
        hit the same shape earlier and fixed it READ-side with a `lens_run`
        marker row filtered on by the reader — this is the write-side
        version, generic across kinds instead of one reader's bespoke fix.

        Only correct for a caller whose SINGLE upsert_finding() call is the
        whole run's result set for that (slug, kind, scope_locator) — a kind
        written across several calls or several sub-surveyors must NOT set
        this, because superseding here would retire a sibling call's still-
        valid rows, not just this run's own previous ones.
        `architecture_recovery` is the standing example (see
        query_findings_all_runs' own docstring): two independent survey
        steps can each contribute evidence for the SAME scope_locator at
        different times, and are not required to run together.

        This never deletes a row. `query_findings()` — the "current answer"
        reader — excludes superseded rows; `query_findings_history_raw()`
        and `query_findings_all_runs()` are untouched by this column and
        keep returning every row ever written, superseded or not.

        Raises ValueError if `slug` isn't a registered project. Every known
        caller (SurveyOrchestrator.run(), run_survey_definition()) already
        validates this upfront the same way (registry.get(slug) is None ->
        reject), but this is the one choke point every surveyor's _persist()
        actually reaches — 2026-08-23 incident: an unregistered/stale slug
        got past whatever called this (bypassing those two guards, or racing
        a project deletion mid-run) and the plain INSERT below hit
        project_analysis_findings_project_slug_fkey, which the callers' own
        broad try/except-and-log-warning (e.g. SecurityHygieneSurveyor.
        _persist) silently absorbed as a generic warning instead of the
        actionable message this now gives them."""
        if not findings and not supersedes_previous:
            return
        slug = self._normalize_slug(slug)
        if self.get(slug) is None:
            raise ValueError(
                f"Cannot record '{kind}' analysis findings for project '{slug}' — "
                "no such project in the registry (it was never added, or was "
                "removed since this survey run started)."
            )
        surveyed_at = surveyed_at or datetime.utcnow().isoformat()
        # `surveyed_at` is the READ KEY, not a label: query_findings returns
        # only the rows at MAX(surveyed_at). A non-timestamp string therefore
        # shadows every real run FOREVER, because any word starting above '2'
        # sorts above every ISO timestamp this century.
        #
        # Not hypothetical. 3,269 rows written with the literal `'probe'` from
        # an ad-hoc session on 2026-08/09 made `architecture_doc_lens`
        # unreadable on five resources — query_findings returned the probe
        # batch and the 2026-09-01 run was invisible. Same shadowing failure
        # arch_lens.py's LENS_KIND comment documents, through a different
        # door: there a newer timestamp, here a word.
        if not re.match(r"^\d{4}-\d{2}-\d{2}", surveyed_at):
            raise ValueError(
                f"surveyed_at must be an ISO-8601 timestamp, got {surveyed_at!r}. "
                "It is the read key — query_findings returns only the rows at "
                "MAX(surveyed_at) — so a non-timestamp value permanently hides "
                f"every real run of '{kind}' for '{slug}'."
            )
        with self._conn() as conn:
            if supersedes_previous:
                # Stamp every still-current row for this EXACT (slug, kind,
                # scope_locator) as superseded, BEFORE inserting this run's
                # own rows below — including when `findings` is empty, which
                # is what lets a clean run retire a stale positive. Scoped
                # tightly (all three columns) so a scoped run's completeness
                # claim never reaches another scope's rows, and idempotent
                # (`superseded_at IS NULL` guard) so re-running this against
                # already-superseded rows is a no-op, not a timestamp churn.
                conn.execute(
                    "UPDATE project_analysis_findings SET superseded_at = :surveyed_at "
                    "WHERE project_slug = :project_slug AND kind = :kind "
                    "AND scope_locator = :scope_locator AND superseded_at IS NULL",
                    {
                        "surveyed_at": surveyed_at, "project_slug": slug,
                        "kind": kind, "scope_locator": scope_locator,
                    },
                )
            if not findings:
                return
            conn.executemany(
                "INSERT INTO project_analysis_findings "
                "(project_slug, kind, surveyed_at, check_name, label, summary, confidence, detail_json, scope_locator) "
                "VALUES (:project_slug, :kind, :surveyed_at, :check_name, :label, :summary, :confidence, :detail_json, :scope_locator)",
                [
                    {
                        "project_slug": slug, "kind": kind, "surveyed_at": surveyed_at,
                        "check_name": f["check_name"], "label": f["label"],
                        "summary": f.get("summary", ""), "confidence": f.get("confidence", 100),
                        "detail_json": json.dumps(f["detail"]) if f.get("detail") else None,
                        "scope_locator": scope_locator,
                    }
                    for f in findings
                ],
            )

    def mark_finding_guid(self, finding_id: int, guid: str) -> None:
        """Record the Egeria Annotation GUID one finding row was published as.

        docs/annotation-linking-plan.md Phase 1. `upsert_finding` stays
        insert-only; this is a separate, narrow write used only after a
        successful publish of the annotation that corresponds to this row.

        Deliberately a no-op on a falsy `guid` — this is the guard the plan's
        own Phase 1 verification names explicitly: a create that raised, or
        that returned a 200 with no `"guid"` key (see
        `annotation_props.publish_annotations`'s Q1 handling), must leave the
        row exactly as it was, not record an empty string that would then
        read as "we tried and got nothing" — indistinguishable from a real
        (if bafflingly empty) capture. The column's NULL default already
        means "no known GUID" for both a never-published row and a
        pre-migration one (see the migration's own comment); calling this
        with an empty guid would only add a third, pointless way to spell the
        same thing.

        What this method does NOT do: distinguish "never published" from
        "published, but the create failed" for a given row. That
        distinction is not stored on `project_analysis_findings` at all — for
        annotations published through the outbox, it lives on the
        corresponding `egeria_outbox` row's own `status`/`attempts`/
        `last_error` (a failed create there stays `pending`/`failed`/`dead`,
        never silently advances to `done`); the direct/no-registry publish
        path has no per-row failure record today (a failure there is logged
        and the loop continues — see `publish_annotations`), so for that path
        specifically "not yet published" and "published but failed" are
        genuinely indistinguishable after the fact. That gap is real,
        pre-existing to this method, and out of Phase 1's scope to close.
        """
        if not guid:
            return
        with self._conn() as conn:
            conn.execute(
                "UPDATE project_analysis_findings SET egeria_annotation_guid = ? WHERE id = ?",
                (guid, finding_id),
            )

    def upsert_gap(
        self, project_slug: str, analysis_id: str, gap_kind: str, check_name: str,
        sentence: str, evidence: dict | None = None, seen_at: str | None = None,
    ) -> int:
        """Record one sighting of a gap about the ANALYSIS, not the repository
        (SPEC-ACTIONABLE-AND-HONEST.md §3) — a disagreement between two
        measures, or a check that could not be established.

        Upsert keyed on (project_slug, analysis_id, gap_kind, check_name) —
        the identity index above enforces this at the database level too, so
        a caller cannot duplicate a gap even if it calls this twice for the
        same sighting. A repeat sighting bumps `last_seen_at` and refreshes
        `sentence`/`evidence_json` (the underlying measurement may have
        changed even though the gap itself has not); `first_seen_at` and any
        existing `resolved_at`/`rfa_activity_id` are left untouched, so a gap
        already worked stays that way until something explicitly clears it —
        this method never resolves a gap and never reopens one.

        Raises ValueError for an unregistered slug, same guard and reason as
        `upsert_finding` — this table carries the same FOREIGN KEY."""
        slug = self._normalize_slug(project_slug)
        if self.get(slug) is None:
            raise ValueError(
                f"Cannot record a gap for project '{slug}' — no such project "
                "in the registry."
            )
        seen_at = seen_at or datetime.utcnow().isoformat()
        evidence_json = json.dumps(evidence) if evidence else None
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT id FROM analysis_gaps WHERE project_slug = ? AND analysis_id = ? "
                "AND gap_kind = ? AND check_name = ?",
                (slug, analysis_id, gap_kind, check_name),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE analysis_gaps SET last_seen_at = ?, sentence = ?, "
                    "evidence_json = ? WHERE id = ?",
                    (seen_at, sentence, evidence_json, existing["id"]),
                )
                return existing["id"]
            conn.execute(
                "INSERT INTO analysis_gaps "
                "(project_slug, analysis_id, gap_kind, check_name, sentence, "
                "evidence_json, first_seen_at, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (slug, analysis_id, gap_kind, check_name, sentence, evidence_json,
                 seen_at, seen_at),
            )
            # Neither cursor wrapper exposes lastrowid across both dialects
            # (see PostgresCursorWrapper/SQLiteCursorWrapper above) — read the
            # id back by the same identity the unique index enforces, exactly
            # as the existing-row branch above already does.
            row = conn.execute(
                "SELECT id FROM analysis_gaps WHERE project_slug = ? AND analysis_id = ? "
                "AND gap_kind = ? AND check_name = ?",
                (slug, analysis_id, gap_kind, check_name),
            ).fetchone()
            return row["id"] if row else 0

    def list_gaps(self, project_slug: str) -> list[dict]:
        """Every unresolved-or-not gap for one project, newest-seen first.
        Includes resolved rows — a caller wanting only open ones filters on
        `resolved_at is None`, same convention as this file's other
        soft-delete/soft-resolve columns (`superseded_at`, `cleared_at`)."""
        slug = self._normalize_slug(project_slug)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, project_slug, analysis_id, gap_kind, check_name, sentence, "
                "evidence_json, first_seen_at, last_seen_at, resolved_at, rfa_activity_id "
                "FROM analysis_gaps WHERE project_slug = ? ORDER BY last_seen_at DESC",
                (slug,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["evidence"] = json.loads(d.pop("evidence_json") or "null") or {}
            except (TypeError, ValueError):
                d["evidence"] = {}
                d.pop("evidence_json", None)
            out.append(d)
        return out

    def get_gap(self, gap_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, project_slug, analysis_id, gap_kind, check_name, sentence, "
                "evidence_json, first_seen_at, last_seen_at, resolved_at, rfa_activity_id "
                "FROM analysis_gaps WHERE id = ?",
                (gap_id,),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["evidence"] = json.loads(d.pop("evidence_json") or "null") or {}
        except (TypeError, ValueError):
            d["evidence"] = {}
            d.pop("evidence_json", None)
        return d

    def mark_gap_rfa(self, gap_id: int, rfa_activity_id: str) -> None:
        """Stamp the RFA raised for one gap. Never overwrites an existing
        stamp — the route calling this checks for one first and refuses with
        409 (same convention as outbox.py's retry route) rather than raising
        a second RFA for a gap already worked."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE analysis_gaps SET rfa_activity_id = ? WHERE id = ?",
                (rfa_activity_id, gap_id),
            )

    def analysis_result_summary(self, slugs: list, kinds: list) -> dict:
        """Per (resource, analysis): is there stored output, and how old is it.

        TWO grouped queries for a whole matrix, instead of running each
        analysis's results reader. The readers are the expensive part —
        `architecture_recovery`'s reassembles and projects a candidate
        hierarchy, measured at 47s on a large repo — and a grid cell needs to
        know THAT there is output and WHEN it was measured, not what it says.

        Returns `{(slug, kind): {"rows": int, "measured_at": str}}`, from
        `project_analysis_findings` and `project_analysis_metrics` combined —
        an analysis may write to either or both.

        **This is a projection, not the full state.** It can tell
        "there is output" from "there is none"; it CANNOT tell `measured`
        from `partial`, because that distinction lives in the results dict's
        own `_status`, which only the reader produces. A caller must render
        the difference as "not read yet", never as a state it has not
        established — see FactLayer._state_for for the full determination.
        """
        if not slugs or not kinds:
            return {}
        out: dict = {}
        with self._conn() as conn:
            for table in ("project_analysis_findings", "project_analysis_metrics"):
                rows = conn.execute(
                    f"SELECT project_slug, kind, COUNT(*) AS n, MAX(surveyed_at) AS last_at "
                    f"FROM {table} "
                    f"WHERE project_slug IN ({','.join('?' * len(slugs))}) "
                    f"  AND kind IN ({','.join('?' * len(kinds))}) "
                    # Excluded BEFORE the MAX, not after. A non-timestamp
                    # label sorts above every real timestamp, so filtering the
                    # result would discard the row's true date along with it.
                    # `____-__-__%` is the ISO date shape in both SQLite and
                    # Postgres LIKE; `_` is one character in each.
                    # `____-__-__%` is the ISO date shape in both SQLite and
                    # Postgres LIKE. BOUND, not inlined: a literal `%` in the
                    # SQL text collides with psycopg's own parameter syntax.
                    f"  AND surveyed_at LIKE ? "
                    f"GROUP BY project_slug, kind",
                    (*slugs, *kinds, "____-__-__%"),
                ).fetchall()
                for r in rows:
                    key = (r["project_slug"], r["kind"])
                    prev = out.get(key) or {"rows": 0, "measured_at": ""}
                    prev["rows"] += r["n"] or 0
                    # The LATER of the two tables' timestamps: an analysis that
                    # wrote metrics after findings was measured at the later
                    # moment, and reporting the earlier one would age it.
                    if (r["last_at"] or "") > prev["measured_at"]:
                        prev["measured_at"] = r["last_at"] or ""
                    out[key] = prev
        return out

    def query_findings(self, slug: str, kind: str, scope_locator: str = "") -> list[dict]:
        """Latest run's findings for one analysis kind, scoped to
        scope_locator (default '' = whole-resource, matching every
        pre-scope-aware caller unchanged).

        Excludes superseded rows (`superseded_at IS NOT NULL` — see
        upsert_finding's `supersedes_previous` docstring). `latest_ts` itself
        is still computed over ALL rows, superseded or not: a writer that
        opts in stamps `superseded_at` on the old rows WITHOUT writing a new
        `surveyed_at`, on purpose, so a clean run that supersedes a stale
        positive leaves `latest_ts` pointing at the now-fully-superseded
        run and this correctly returns `[]` rather than reaching further
        back for an even older row that was never the point."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            latest_ts = conn.execute(
                "SELECT MAX(surveyed_at) FROM project_analysis_findings "
                "WHERE project_slug = ? AND kind = ? AND scope_locator = ?",
                (slug, kind, scope_locator),
            ).fetchone()[0]
            if not latest_ts:
                return []
            rows = conn.execute(
                "SELECT check_name, label, summary, confidence, detail_json, surveyed_at, scope_locator "
                "FROM project_analysis_findings "
                "WHERE project_slug = ? AND kind = ? AND scope_locator = ? AND surveyed_at = ? "
                "AND superseded_at IS NULL "
                "ORDER BY check_name",
                (slug, kind, scope_locator, latest_ts),
            ).fetchall()
        return [dict(r) for r in rows]

    def query_findings_history_raw(self, slug: str, kind: str) -> list[dict]:
        """All finding rows for one analysis kind, across every run, ordered
        by surveyed_at — deliberately NOT aggregated here. What "the trend"
        means differs per kind (a gap-count for security, a quality-rank for
        documentation) — that aggregation belongs in each kind's own
        trend_reader (repo_survey_definition_adapter.py), not as per-kind
        SQL branches in this otherwise-generic function.

        `scope_locator` is included (added 2026-08-30) so a scope-keyed kind can
        answer "which scopes did this step write, and when" in ONE query rather
        than one per scope — architecture recovery has ~1000 scopes on `egeria`,
        so the per-scope form is not usable at persist time. Additive: existing
        callers read named keys."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT check_name, label, summary, confidence, detail_json, surveyed_at, "
                "       scope_locator "
                "FROM project_analysis_findings WHERE project_slug = ? AND kind = ? "
                "ORDER BY surveyed_at ASC",
                (slug, kind),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── step_runs (design §17.2) ─────────────────────────────────────────
    def record_step_run(
        self, slug: str, step_key: str, surveyed_at: str, *,
        entity_type: str = "repo", source: str = "local", executor: str = "local",
        executor_ref: str = "", demanded_by: str = "",
        metrics: dict | None = None, declared: dict | None = None,
        disagreement: str = "", surveyed_as: str = "",
    ) -> None:
        """One row per step EXECUTION. Append-only: a step run twice in one
        snapshot (once as a prerequisite, once on its own request) is two
        facts, and collapsing them would lose the `demanded_by` attribution
        that §17.1 exists to record.

        `demanded_by` is empty for a directly-requested step and carries the
        requesting step's key for an auto-run prerequisite — the field that
        answers "why did Scouting take three minutes".
        """
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO step_runs (slug, entity_type, step_key, surveyed_at, "
                "source, executor, executor_ref, demanded_by, metrics, declared, "
                "disagreement, surveyed_as) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (slug, entity_type, step_key, surveyed_at, source, executor,
                 executor_ref or "", demanded_by or "",
                 json.dumps(metrics or {}), json.dumps(declared or {}),
                 disagreement or "", surveyed_as or ""),
            )

    def query_step_runs(
        self, slug: str | None = None, step_key: str | None = None,
        surveyed_at: str | None = None, entity_type: str | None = None,
        limit: int = 5000,
    ) -> list[dict]:
        """Rows with `metrics`/`declared` already decoded to dicts.

        Decoded here rather than at each call site: the column is TEXT-holding-
        JSON on both backends (see the CREATE TABLE's own note), and leaving
        the decode to callers is how a `metrics.get(...)` against a string
        silently returns nothing.
        """
        where, params = [], []
        for column, value in (("slug", slug), ("step_key", step_key),
                              ("surveyed_at", surveyed_at),
                              ("entity_type", entity_type)):
            if value is not None:
                where.append(f"{column} = ?")
                params.append(value)
        sql = "SELECT * FROM step_runs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY surveyed_at DESC, id DESC LIMIT {int(limit)}"
        with self._conn() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        out = []
        for row in rows:
            record = dict(row)
            for column in ("metrics", "declared"):
                raw = record.get(column)
                if isinstance(raw, str):
                    try:
                        record[column] = json.loads(raw or "{}")
                    except (ValueError, TypeError):
                        record[column] = {}
                elif raw is None:
                    record[column] = {}
            out.append(record)
        return out

    def median_step_wall_ms(self, step_key: str) -> float | None:
        """Median observed `wall_ms` for a step across every resource, or None
        when it has never been measured.

        None is a real answer the caller must render as one — a proposal built
        on a tier default says so (`Proposal.estimated_is_measured`) rather
        than presenting a guess as a measurement.

        Across ALL resources, for the same reason `step_cost_observer.
        _step_ever_measured` reads that way: "what does this step usually
        cost" is a fact about the step.
        """
        try:
            rows = self.query_step_runs(step_key=step_key, limit=500)
        except Exception:
            return None
        values = sorted(
            float(r["metrics"]["wall_ms"]) for r in rows
            if isinstance(r.get("metrics"), dict)
            and isinstance(r["metrics"].get("wall_ms"), (int, float))
        )
        if not values:
            return None
        mid = len(values) // 2
        return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2

    def upsert_metric(
        self, slug: str, kind: str, metrics: dict[str, float],
        detail: dict | None = None, surveyed_at: str | None = None,
        scope_locator: str = "",
    ) -> None:
        """Append one survey run's metric(s) for one analysis kind.
        metrics: {metric_name: value} — one row per metric, sharing one
        surveyed_at (same "one row per label per run" shape
        project_file_type_counts and project_dependencies already use).
        detail (optional) attaches to only the first metric row, to avoid
        duplicating a potentially large JSON blob across every metric from
        the same run. scope_locator: see upsert_finding's docstring —
        '' (default) = whole-resource, unchanged for every pre-scope-aware
        caller."""
        if not metrics:
            return
        slug = self._normalize_slug(slug)
        # Same guard, same reason as upsert_finding above — these two are the
        # matched pair every surveyor's persistence path reaches, and only one
        # of them had it. With SQLite's foreign_keys pragma now ON, the missing
        # guard surfaced immediately as a raw `IntegrityError: FOREIGN KEY
        # constraint failed` from two notification-detector tests, which is the
        # vague error the finding-side guard exists to replace.
        if self.get(slug) is None:
            raise ValueError(
                f"Cannot record '{kind}' analysis metrics for project '{slug}' — "
                "no such project in the registry (it was never added, or was "
                "removed since this survey run started)."
            )
        surveyed_at = surveyed_at or datetime.utcnow().isoformat()
        rows = [
            {
                "project_slug": slug, "kind": kind, "surveyed_at": surveyed_at,
                "metric_name": name, "metric_value": value,
                "detail_json": json.dumps(detail) if detail and i == 0 else None,
                "scope_locator": scope_locator,
            }
            for i, (name, value) in enumerate(metrics.items())
        ]
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO project_analysis_metrics "
                "(project_slug, kind, surveyed_at, metric_name, metric_value, detail_json, scope_locator) "
                "VALUES (:project_slug, :kind, :surveyed_at, :metric_name, :metric_value, :detail_json, :scope_locator)",
                rows,
            )

    def query_metrics(self, slug: str, kind: str, scope_locator: str = "") -> dict:
        """Latest run's metrics for one analysis kind, as
        {metric_name: value, ..., "surveyed_at": ..., "detail": {...}?},
        scoped to scope_locator (default '' = whole-resource)."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            latest_ts = conn.execute(
                "SELECT MAX(surveyed_at) FROM project_analysis_metrics "
                "WHERE project_slug = ? AND kind = ? AND scope_locator = ?",
                (slug, kind, scope_locator),
            ).fetchone()[0]
            if not latest_ts:
                return {}
            rows = conn.execute(
                "SELECT metric_name, metric_value, detail_json FROM project_analysis_metrics "
                "WHERE project_slug = ? AND kind = ? AND scope_locator = ? AND surveyed_at = ?",
                (slug, kind, scope_locator, latest_ts),
            ).fetchall()
        out: dict = {"surveyed_at": latest_ts}
        for r in rows:
            out[r["metric_name"]] = r["metric_value"]
            if r["detail_json"]:
                out["detail"] = json.loads(r["detail_json"])
        return out

    def query_metrics_history(self, slug: str, kind: str, metric_name: str, scope_locator: str = "") -> list[dict]:
        """One row per run for one specific named metric, for trending —
        e.g. query_metrics_history(slug, "api_structure", "symbol_count").
        Scoped to scope_locator (default '' = whole-resource)."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT surveyed_at, metric_value FROM project_analysis_metrics "
                "WHERE project_slug = ? AND kind = ? AND metric_name = ? AND scope_locator = ? "
                "ORDER BY surveyed_at ASC",
                (slug, kind, metric_name, scope_locator),
            ).fetchall()
        return [dict(r) for r in rows]

    def query_metrics_history_raw(
        self, slug: str, kind: str, metric_name: str, scope_locator: str = "",
    ) -> list[dict]:
        """Like query_metrics_history, but including detail_json — for a
        reader that needs the per-run detail attached to a metric row (e.g.
        an outcome stamped on a component-less run's summary row, which has
        no finding row of its own to carry it — see persist.py's run-summary
        comment) rather than only its trend value."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT surveyed_at, metric_value, detail_json FROM project_analysis_metrics "
                "WHERE project_slug = ? AND kind = ? AND metric_name = ? AND scope_locator = ? "
                "ORDER BY surveyed_at ASC",
                (slug, kind, metric_name, scope_locator),
            ).fetchall()
        return [dict(r) for r in rows]

    def query_finding_scopes(self, slug: str, kind: str, check_name: str = "",
                             include_withdrawn: bool = False) -> list[str]:
        """Distinct `scope_locator`s that currently have at least one
        finding row for (slug, kind) — optionally narrowed to one
        check_name. This is the "list every component" query for a
        many-scope_locator analysis kind like architecture_recovery
        (design doc §6.0: a component IS a scope_locator/path prefix),
        where query_findings' single-scope_locator contract has nothing to
        enumerate against. Ordered for stable UI rendering, not by recency.

        **Withdrawn scopes are excluded** unless `include_withdrawn=True`
        (step 2 of docs/architecture-recovery-scope-tombstoning.md). This
        query previously had NO notion of currency — no timestamp, no
        recency, no filter — so a scope, once written, was enumerable
        forever. Measured 2026-08-30: 165 of `egeria_git`'s 1035 component
        scopes were written by no current run, and 27 of its 35
        deployment-perspective components were dead, every one rendered at
        its original confidence.

        A withdrawal is a row carrying `label = WITHDRAWN_LABEL`, written on
        the vacated scope. The mechanism is a reserved LABEL rather than an
        architecture-specific check_name so this method stays kind-agnostic:
        any analysis kind may withdraw a scope the same way.

        **A scope counts as withdrawn only when the newest information about
        it says so and nothing at that instant says otherwise** — i.e. at its
        latest `surveyed_at` there is a withdrawal and no ordinary row. That
        gives revival for free: `repo_arch_detect` and `repo_arch_coupling`
        write the same kind, so a scope one step still proposes stays live
        even after the other withdraws it, with no special case.
        """
        slug = self._normalize_slug(slug)
        sql = "SELECT DISTINCT scope_locator FROM project_analysis_findings WHERE project_slug = ? AND kind = ?"
        params: list = [slug, kind]
        if check_name:
            sql += " AND check_name = ?"
            params.append(check_name)
        sql += " ORDER BY scope_locator"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            scopes = [r["scope_locator"] for r in rows]
            if include_withdrawn or not scopes:
                return scopes
            # One query for every scope's newest instant, then which of those
            # instants are withdrawal-only. Deliberately NOT narrowed by
            # check_name: a withdrawal is about the SCOPE, so it must be
            # visible to a caller enumerating any check_name within it.
            withdrawn = conn.execute(
                "SELECT f.scope_locator FROM project_analysis_findings f "
                "JOIN (SELECT scope_locator, MAX(surveyed_at) AS ts "
                "      FROM project_analysis_findings "
                "      WHERE project_slug = ? AND kind = ? GROUP BY scope_locator) latest "
                "  ON f.scope_locator = latest.scope_locator AND f.surveyed_at = latest.ts "
                "WHERE f.project_slug = ? AND f.kind = ? "
                "GROUP BY f.scope_locator "
                "HAVING SUM(CASE WHEN f.label = ? THEN 1 ELSE 0 END) > 0 "
                "   AND SUM(CASE WHEN f.label = ? THEN 0 ELSE 1 END) = 0",
                (slug, kind, slug, kind, WITHDRAWN_LABEL, WITHDRAWN_LABEL),
            ).fetchall()
        dead = {r["scope_locator"] for r in withdrawn}
        return [s for s in scopes if s not in dead]

    def query_findings_all_runs(self, slug: str, kind: str, scope_locator: str) -> list[dict]:
        """Every finding row ever written for one (kind, scope_locator) —
        deliberately NOT narrowed to the latest surveyed_at the way
        query_findings() is.

        architecture_recovery is the one analysis kind where this matters:
        two independent survey steps (repo_arch_detect, repo_arch_coupling)
        can each contribute evidence for the SAME component (scope_locator)
        at DIFFERENT times — they are not required to run in the same
        SurveyOrchestrator batch. query_findings()'s "latest surveyed_at
        wins" semantics would silently discard whichever step ran earlier,
        which is exactly the "which approach proposed this" information the
        results view exists to show (Phase 1 plan §4.4). Every other caller
        of the generic findings table writes one kind from one step, where
        "latest run replaces the previous one" is the right reading — this
        method exists because that assumption doesn't hold here.
        """
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT check_name, label, summary, confidence, detail_json, surveyed_at "
                "FROM project_analysis_findings "
                "WHERE project_slug = ? AND kind = ? AND scope_locator = ? "
                "ORDER BY surveyed_at ASC",
                (slug, kind, scope_locator),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_file_inventory_with_sizes(self, slug: str, *, include_vendored: bool = False) -> list[dict]:
        """Return file paths, sizes, and git mode bits from the inventory for a project.

        Each dict has keys: ``file_path`` (str), ``file_size_bytes`` (int),
        and ``file_mode`` (str, '' if never captured — see D9 Tier 1 of the
        Assessment sub-resource cataloging plan).
        """
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT file_path, file_size_bytes, file_mode, COALESCE(vendored, 0) AS vendored "
                "FROM project_file_inventory WHERE project_slug = ?"
                + ("" if include_vendored else " AND COALESCE(vendored, 0) = 0"),
                (slug,),
            ).fetchall()
        return [
            {
                "file_path": r["file_path"],
                "file_size_bytes": r["file_size_bytes"] or 0,
                "file_mode": r["file_mode"] or "",
                "vendored": bool(r["vendored"]),
            }
            for r in rows
        ]

    # ── Egeria integration ────────────────────────────────────────────────────

    def get_egeria_asset_guid(self, slug: str) -> str | None:
        """Return the cached GUID of this project's own Egeria Asset, or None.

        Named for the repository's Asset since 2026-09-14 -- it cached a
        SourceControlLibrary GUID before that was corrected (see
        egeria_publisher.py's module docstring); the column name and this
        method's name are unchanged, only what they point at."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT egeria_asset_guid FROM projects WHERE slug = ?", (slug,)
            ).fetchone()
        if row:
            return row["egeria_asset_guid"] or None
        return None

    def set_egeria_asset_guid(self, slug: str, guid: str) -> None:
        """Persist the GUID of this project's own Egeria Asset."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            conn.execute(
                "UPDATE projects SET egeria_asset_guid = ? WHERE slug = ?",
                (guid, slug),
            )

    # ── Egeria linkage divergence (see egeria_linkage_status's DDL comment) ──

    def mark_egeria_linkage_stale(
        self, entity_type: str, entity_slug: str, stale_guid: str = "", detail: str = "",
    ) -> None:
        """Record that this entity's cached Egeria GUIDs can no longer be trusted.

        Deliberately does not clear the GUIDs. A stale root GUID means the whole
        Egeria-side lineage RE cached beneath it is *unverifiable*, not
        automatically wrong — recreating in place risks duplicating Egeria
        elements and silently discarding catalog history that may still matter.
        The GUIDs are kept so a human can see what RE had, and so "republish"
        can report exactly what it is replacing.

        `detected_at` is the FIRST time this was found stale, and is preserved
        across repeat calls (e.g. `recheck_all_linkages` re-confirming an
        already-stale link) rather than overwritten — found live 2026-09-26:
        a recheck bumped `detected_at` to the recheck's own timestamp, so a
        link stale since 2026-09-22 read as "stale since <today>" on every
        re-confirmation. `last_checked_at` is the one that always advances —
        "how long has this been broken" and "how fresh is this reading" are
        two separate facts, both honest.
        """
        from datetime import timezone
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO egeria_linkage_status
                       (entity_type, entity_slug, status, stale_guid, detected_at,
                        last_checked_at, detail)
                   VALUES (?, ?, 'stale', ?, ?, ?, ?)
                   ON CONFLICT (entity_type, entity_slug) DO UPDATE SET
                       status='stale', stale_guid=excluded.stale_guid,
                       detected_at=CASE WHEN egeria_linkage_status.status='stale'
                                        THEN egeria_linkage_status.detected_at
                                        ELSE excluded.detected_at END,
                       last_checked_at=excluded.last_checked_at,
                       detail=excluded.detail""",
                (entity_type, entity_slug, stale_guid, now, now, detail),
            )

    def get_egeria_linkage(self, entity_type: str, entity_slug: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM egeria_linkage_status WHERE entity_type=? AND entity_slug=?",
                (entity_type, entity_slug),
            ).fetchone()
        return dict(row) if row else None

    def list_stale_egeria_linkages(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM egeria_linkage_status WHERE status='stale' "
                "ORDER BY detected_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def clear_egeria_linkage_status(self, entity_type: str, entity_slug: str) -> bool:
        """Drop the divergence record — the linkage is trusted again.

        Deletes rather than setting status='ok': absence already means healthy
        (see the DDL comment), so an 'ok' row would be a second representation
        of the same state and something else to keep consistent.
        """
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM egeria_linkage_status WHERE entity_type=? AND entity_slug=?",
                (entity_type, entity_slug),
            )
        return cur.rowcount > 0

    # ── Egeria outbox — pending element writes ─────────────────────────────
    # docs/outbox-publishing-design.md §5. The apply side lives in
    # egeria_outbox.py; this class owns only the rows.

    #: A row that has failed this many times is dead-lettered rather than
    #: retried forever — the gap the rfa_actions precedent has, named in §3.
    OUTBOX_MAX_ATTEMPTS = 8

    #: Terminal states. 'done' succeeded; 'dead' exhausted its attempts and
    #: needs a human. Neither is ever picked up again by the drain.
    OUTBOX_TERMINAL = ("done", "dead")

    def enqueue_outbox_element(
        self, entity_type: str, entity_slug: str, element_kind: str,
        qualified_name: str, payload: dict, *, run_id: str = "",
        depends_on_id: int | None = None,
    ) -> int:
        """Record one pending Egeria element write. Returns its row id.

        Callers enqueue inside the same local transaction that records the
        survey, then drain — so a crash between "we recorded the survey" and
        "we told Egeria" is impossible, which is the per-process hole in §2.

        qualified_name must be the SAME string the apply step will search for
        and create with. For annotations that means the caller's own run
        timestamp is already baked in (see annotation_props.publish_annotations)
        — a retry replays the stored payload and therefore produces a
        byte-identical qualifiedName, which is what makes the replay converge
        instead of duplicating.
        """
        if not qualified_name:
            raise ValueError("qualified_name is the idempotency key and cannot be empty")
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO egeria_outbox "
                "(entity_type, entity_slug, run_id, element_kind, qualified_name, "
                " payload_json, depends_on_id, status, attempts, next_attempt_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?)",
                (entity_type, entity_slug, run_id, element_kind, qualified_name,
                 json.dumps(payload), depends_on_id, now, now),
            )
            row_id = getattr(cur, "lastrowid", None)
            if row_id is None:
                row_id = conn.execute(
                    "SELECT id FROM egeria_outbox WHERE qualified_name=? "
                    "ORDER BY id DESC LIMIT 1", (qualified_name,),
                ).fetchone()["id"]
        return int(row_id)

    def claim_due_outbox_elements(
        self, limit: int = 200, now: str | None = None, run_id: str | None = None,
    ) -> list[dict]:
        """Rows ready to attempt, oldest first.

        A row is due when it is pending/failed, its backoff has elapsed, AND
        its dependency (if any) is 'done'. That last clause is the whole point
        of depends_on_id: annotations cannot be attempted before the report
        they hang off, so a partial drain leaves a coherent prefix rather than
        orphans.

        **This is a real claim.** Select and status transition happen in ONE
        transaction (`self._conn()` below), so two drainers cannot both take
        the same row — fixed 2026-09-19, see
        `docs/design-notes/OUTBOX-DRAIN-RACE-FIXED.md`. A claimed row moves to
        `status='running'` with `claimed_at` set to `now`; on Postgres the
        `SELECT` additionally carries `FOR UPDATE SKIP LOCKED`, so a second,
        concurrent transaction skips rows the first is mid-claim on rather
        than blocking or re-reading them once the first commits. SQLite has no
        `SKIP LOCKED`, but needs none: it serialises writers itself, so the
        single transaction already gives the same guarantee there.

        This docstring used to say the opposite ("despite the name, it does
        not claim") — accurate when written (2026-09-02), stale from the
        moment the fix above landed a few hours later, and left uncorrected
        until now. A `claim_` function that performs no claim, *documented* as
        not claiming, is exactly the kind of doc a later reader trusts over
        the code; the failure mode this repeats is the same shape as the
        original bug it described, just inverted.

        A row can still be stranded in `running` if its claimer dies before
        marking it done/failed/pending — `CLAIM_LEASE_SECONDS` bounds that:
        once `claimed_at` is older than the lease, the row is due again (see
        the `status = 'running' AND claimed_at <= ?` clause below). A caller
        that claims rows and then cannot even attempt them (no Egeria client
        reachable) should call `release_outbox_claim()` immediately rather
        than wait out the lease — see `drain_outbox`'s no-client branch.

        `run_id` scopes the claim to one publish's rows. A publisher that
        enqueues and then drains inline needs its OWN annotations written
        before it returns; an unscoped claim takes the oldest due rows in the
        table, which could be another resource's backlog entirely — leaving
        the caller believing it had published when it had in fact drained
        someone else's queue.
        """
        now = now or datetime.utcnow().isoformat()
        lease_cutoff = (
            datetime.fromisoformat(now) - timedelta(seconds=CLAIM_LEASE_SECONDS)
        ).isoformat()
        sql = (
            "SELECT o.id FROM egeria_outbox o "
            # A correlated EXISTS rather than a LEFT JOIN: Postgres rejects
            # "FOR UPDATE ... nullable side of an outer join" outright, and the
            # SQLite unit tests cannot see that — they were green while this
            # raised FeatureNotSupported against the real backend.
            "WHERE (o.status IN ('pending', 'failed') "
            "       OR (o.status = 'running' AND o.claimed_at <= ?)) "
            "  AND o.next_attempt_at <= ? "
            "  AND (o.depends_on_id IS NULL OR EXISTS ("
            "        SELECT 1 FROM egeria_outbox dep "
            "        WHERE dep.id = o.depends_on_id AND dep.status = 'done')) "
        )
        params: list = [lease_cutoff, now]
        if run_id is not None:
            sql += "  AND o.run_id = ? "
            params.append(run_id)
        sql += "ORDER BY o.id ASC LIMIT ?"
        params.append(limit)

        # Select and mark in ONE transaction, so two drainers cannot both take
        # the same row. On Postgres the FOR UPDATE SKIP LOCKED makes the losing
        # claimer skip the locked rows outright instead of blocking on them;
        # SQLite serialises writers itself, so the same transaction suffices.
        with self._conn() as conn:
            if conn.is_postgres:
                sql += " FOR UPDATE SKIP LOCKED"
            ids = [r["id"] for r in conn.execute(sql, tuple(params)).fetchall()]
            if not ids:
                return []
            marks = ",".join("?" * len(ids))
            conn.execute(
                f"UPDATE egeria_outbox SET status='running', claimed_at=? "  # noqa: S608
                f"WHERE id IN ({marks})",
                tuple([now, *ids]),
            )
            rows = conn.execute(
                f"SELECT * FROM egeria_outbox WHERE id IN ({marks}) ORDER BY id ASC",  # noqa: S608
                tuple(ids),
            ).fetchall()
        return [dict(r) for r in rows]

    def peek_due_outbox_elements(self, *, limit: int = 200, run_id: str | None = None,
                                 now: str | None = None) -> list[dict]:
        """Which rows a drain WOULD take, without taking them.

        `claim_due_outbox_elements` is a real claim now: calling it marks rows
        'running'. Anything that wants to look — a test asserting another run's
        rows were left alone, a status panel, a person asking what is queued —
        needs this instead, or the act of looking becomes the act of taking.
        """
        now = now or datetime.utcnow().isoformat()
        lease_cutoff = (
            datetime.fromisoformat(now) - timedelta(seconds=CLAIM_LEASE_SECONDS)
        ).isoformat()
        sql = (
            "SELECT o.* FROM egeria_outbox o "
            # A correlated EXISTS rather than a LEFT JOIN: Postgres rejects
            # "FOR UPDATE ... nullable side of an outer join" outright, and the
            # SQLite unit tests cannot see that — they were green while this
            # raised FeatureNotSupported against the real backend.
            "WHERE (o.status IN ('pending', 'failed') "
            "       OR (o.status = 'running' AND o.claimed_at <= ?)) "
            "  AND o.next_attempt_at <= ? "
            "  AND (o.depends_on_id IS NULL OR EXISTS ("
            "        SELECT 1 FROM egeria_outbox dep "
            "        WHERE dep.id = o.depends_on_id AND dep.status = 'done')) "
        )
        params: list = [lease_cutoff, now]
        if run_id is not None:
            sql += "  AND o.run_id = ? "
            params.append(run_id)
        sql += "ORDER BY o.id ASC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]

    def release_outbox_claim(self, row_ids: "list[int]") -> None:
        """Hand claimed rows back, unchanged, as if they had never been taken.

        Status returns to 'pending', attempts and next_attempt_at untouched.
        The caller claimed the rows and then found it could not even try —
        no Egeria client, an aborted batch — and that is not a failed attempt.
        Without this, an outage would strand rows in 'running' for the whole
        CLAIM_LEASE_SECONDS, which is a slower, quieter version of exactly the
        thing the no-burn rule in drain_outbox exists to prevent.
        """
        if not row_ids:
            return
        marks = ",".join("?" * len(row_ids))
        with self._conn() as conn:
            conn.execute(
                f"UPDATE egeria_outbox SET status='pending', claimed_at='' "  # noqa: S608
                f"WHERE id IN ({marks}) AND status='running'",
                tuple(row_ids),
            )

    def mark_outbox_done(self, row_id: int, egeria_guid: str = "") -> None:
        """Record the element as written, with the GUID it resolved to.

        Once egeria_guid is set, a later retry of this row needs no
        qualifiedName search at all — the GUID is Egeria's primary identity,
        and the search is only how a row that never recorded one discovers it
        nonetheless landed.
        """
        with self._conn() as conn:
            conn.execute(
                "UPDATE egeria_outbox SET status='done', claimed_at='', egeria_guid=?, "
                "completed_at=?, last_error='' WHERE id=?",
                (egeria_guid, datetime.utcnow().isoformat(), row_id),
            )

    def mark_outbox_deferred(self, row_id: int, note: str) -> None:
        """Hand one claimed row back to 'pending' without burning an attempt,
        recording WHY: its referent(s) — another annotation this row's
        `AnnotationExtension` link points at — have not been applied yet.

        Distinct from `mark_outbox_failed`: this is not a failed attempt (no
        `attempts` increment, no exponential backoff), because the row is not
        broken — it is early, and will very likely succeed on the very next
        drain once its referent lands (rows drain in id order, so a run's own
        annotations normally apply before its own links are even attempted).
        Distinct from `release_outbox_claim`: that one is for an outage with
        no Egeria client reachable at all and hands back every claimed row
        identically; this is a per-row, expected-to-resolve-itself wait, and
        keeps the reason on the row so Admin -> Publish Queue shows it rather
        than a bare 'pending' with no explanation.
        """
        with self._conn() as conn:
            conn.execute(
                "UPDATE egeria_outbox SET status='pending', claimed_at='', last_error=? "
                "WHERE id=?",
                (note[:2000], row_id),
            )

    def complete_publish_run_if_done(self, publish_run_id: str) -> bool:
        """When every `egeria_outbox` row belonging to `publish_run_id` — its
        annotations AND its evidence-link companion run (`f"{publish_run_id}
        ::links"`, see egeria_publisher.py's `_link_evidence_outbox`/
        `_create_annotations`) — has reached a terminal state (done or dead;
        neither is retried again), resolve the activity row that reported
        this run as `published: "queued"`:

        * **All done, none dead** — the honest success case. Flips to
          `published: True`, appends "Published at <ts>." to the summary,
          and — if the row carries a `pending_published_record` (see
          egeria_publisher.py's `publish()`) — makes the badge-table writes
          (`record_published_annotation_types`/`record_published_analyses`)
          NOW, at the point they are actually true, rather than at enqueue
          time (which flipped the ☁ Published badge before anything had
          reached Egeria — the bug this deferral exists to fix).
        * **Any dead, and nothing still pending** — NOT success. Flips to
          `published: False` (not a fifth state: `False` is what already
          means "something needs a human", and it is what renders the ☁
          Publish retry button — the right action here) and appends "N
          Egeria write(s) failed permanently — see the Publish Queue" to the
          summary. No badge-table writes: the run's outbox rows genuinely
          did not all land, so the badge tables would be recording annotation
          types this publish did not actually get to Egeria.
        * **Still pending** — untouched; `published` stays `"queued"`.

        Returns False (a no-op) when: no rows exist for this run_id (nothing
        was ever enqueued under it, or a typo); some row is still pending,
        failed-and-backing-off, or running (not done yet); no activity row
        carries this `publish_run_id`; or that activity row's `published` is
        no longer `"queued"` (already flipped by an earlier pass, or was
        never the deferred path to begin with) — otherwise True, for either
        of the two flips above. Idempotent either way: a later call finds
        `published` no longer `"queued"` and does nothing.
        """
        if not publish_run_id:
            return False
        links_run_id = f"{publish_run_id}::links"
        with self._conn() as conn:
            remaining = conn.execute(
                "SELECT COUNT(*) AS n FROM egeria_outbox "
                "WHERE run_id IN (?, ?) AND status IN ('pending', 'failed', 'running')",
                (publish_run_id, links_run_id),
            ).fetchone()["n"]
            if remaining:
                return False
            dead = conn.execute(
                "SELECT COUNT(*) AS n FROM egeria_outbox "
                "WHERE run_id IN (?, ?) AND status = 'dead'",
                (publish_run_id, links_run_id),
            ).fetchone()["n"]
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM egeria_outbox WHERE run_id IN (?, ?)",
                (publish_run_id, links_run_id),
            ).fetchone()["n"]
            if not total:
                return False
            row = conn.execute(
                "SELECT id, summary, detail FROM activity_log "
                "WHERE publish_run_id = ? ORDER BY ts DESC LIMIT 1",
                (publish_run_id,),
            ).fetchone()
            if row is None:
                return False
            try:
                detail = json.loads(row["detail"] or "{}")
            except (TypeError, ValueError):
                return False
            if detail.get("published") != "queued":
                return False

            pending_record = detail.pop("pending_published_record", None)
            base_summary = row["summary"] or ""
            if dead:
                # Not success: some element(s) exhausted their retries and
                # will never apply on their own. False is the existing
                # "needs a human" state — rendering the ☁ Publish retry
                # button is the correct behaviour here, not a new state.
                detail["published"] = False
                detail["published_failed_count"] = dead
                summary = f"{base_summary} {dead} Egeria write(s) failed permanently — see the Publish Queue."
            else:
                now = datetime.utcnow().isoformat()
                detail["published"] = True
                detail["published_at"] = now
                summary = f"{base_summary} Published at {now}."
                if pending_record:
                    # Best-effort, same contract as the inline call site in
                    # egeria_publisher.py's publish() — a bookkeeping failure
                    # must never undo a publish that genuinely landed. The
                    # error is recorded onto the SAME detail this call is
                    # about to write, not merely logged, so it stays visible
                    # to whoever reads this activity entry rather than only
                    # to whoever thinks to check the server log.
                    try:
                        self.record_published_annotation_types(
                            pending_record["slug"],
                            set(pending_record.get("annotation_types") or []),
                            pending_record.get("report_guid", ""),
                        )
                        self.record_published_analyses(
                            pending_record["slug"],
                            pending_record.get("analyses") or [],
                            pending_record.get("report_guid", ""),
                        )
                    except Exception as exc:
                        bookkeeping_error = str(exc)
                        detail["pending_published_record_error"] = bookkeeping_error
                        log.warning(
                            "complete_publish_run_if_done: badge bookkeeping failed for "
                            "run_id %s: %s", publish_run_id, bookkeeping_error,
                        )
            conn.execute(
                "UPDATE activity_log SET detail = ?, summary = ? WHERE id = ?",
                (json.dumps(detail), summary, row["id"]),
            )
        return True

    def get_outbox_guids(self, row_ids: list[int]) -> dict[int, str]:
        """Map row id -> the GUID it resolved to, for rows that reached
        'done'. A row absent from the returned dict is 'not done' — pending,
        failed, or dead-lettered — the caller must not treat a missing key as
        an empty-string GUID; they mean different things (see
        `annotation_props.publish_annotations`'s own "guid vs. no guid" note).

        annotation-linking-plan Phase 2's own reason to exist: after
        `enqueue_annotations` + `drain_outbox` create this run's annotations,
        `egeria_publisher.py::_create_annotations` needs each one's actual
        GUID, by row id, to build its second `AnnotationExtension`-linking
        pass — the same-run in-memory GUIDs the plan's Phase 2 section
        describes, sourced from the outbox rows rather than threaded back
        through `drain_outbox`'s own return value (a summary count, not a
        per-row result, and changing that shape would ripple into every other
        caller of `drain_outbox`).
        """
        if not row_ids:
            return {}
        placeholders = ",".join("?" for _ in row_ids)
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT id, egeria_guid FROM egeria_outbox "
                f"WHERE id IN ({placeholders}) AND status='done'",
                tuple(row_ids),
            ).fetchall()
        return {int(r["id"]): r["egeria_guid"] for r in rows if r["egeria_guid"]}

    def mark_outbox_failed(
        self, row_id: int, error: str, *, max_attempts: int | None = None,
        base_delay_seconds: int = 60, now: datetime | None = None,
    ) -> str:
        """Record a failed attempt and schedule the next one. Returns the new
        status — 'failed' (will retry) or 'dead' (exhausted).

        Exponential backoff, capped at a day: 1m, 2m, 4m ... The rfa_actions
        precedent retries every pass forever with no backoff and no ceiling,
        which is exactly how a permanently-broken write becomes invisible
        noise instead of a signal.
        """
        max_attempts = self.OUTBOX_MAX_ATTEMPTS if max_attempts is None else max_attempts
        now = now or datetime.utcnow()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT attempts FROM egeria_outbox WHERE id=?", (row_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"No outbox row {row_id}")
            attempts = int(row["attempts"]) + 1
            if attempts >= max_attempts:
                status, next_at = "dead", ""
            else:
                delay = min(base_delay_seconds * (2 ** (attempts - 1)), 86400)
                status = "failed"
                next_at = (now + timedelta(seconds=delay)).isoformat()
            conn.execute(
                "UPDATE egeria_outbox SET status=?, claimed_at='', attempts=?, next_attempt_at=?, "
                "last_error=? WHERE id=?",
                (status, attempts, next_at, error[:2000], row_id),
            )
        return status

    def list_dead_outbox_elements(self, limit: int = 100) -> list[dict]:
        """Rows that exhausted their attempts — a stuck publish, visible to a
        human instead of retrying silently forever."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM egeria_outbox WHERE status='dead' "
                "ORDER BY id DESC LIMIT ?", (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_outbox_elements(
        self, status: str | None = None, run_id: str | None = None,
        entity_slug: str | None = None, limit: int = 200,
    ) -> list[dict]:
        """The detailed publish record behind an activity-log summary.

        The activity entry says "3 elements dead-lettered"; this is the list of
        which three, their qualifiedNames, how many attempts each took and what
        Egeria actually said. Newest first, because a stuck publish is read
        from the most recent failure backwards.
        """
        sql = "SELECT * FROM egeria_outbox"
        filters: list[str] = []
        params: list = []
        if status:
            filters.append("status = ?")
            params.append(status)
        if run_id:
            filters.append("run_id = ?")
            params.append(run_id)
        if entity_slug:
            filters.append("entity_slug = ?")
            params.append(entity_slug)
        if filters:
            sql += " WHERE " + " AND ".join(filters)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def retry_outbox_element(self, row_id: int) -> bool:
        """Return a dead row to the queue, attempts reset. Returns whether it
        was actually revived.

        Dead-lettering is deliberately terminal — the drain will never pick the
        row up again on its own — so there has to be a way back for the common
        case where the cause was fixed outside RE (a permission granted, a
        server restarted). Restricted to 'dead': re-queueing a row that is
        merely backing off would discard the backoff, and re-queueing a 'done'
        row would re-apply a completed write.
        """
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE egeria_outbox SET status='pending', claimed_at='', attempts=0, "
                "next_attempt_at=?, last_error='' WHERE id=? AND status='dead'",
                (datetime.utcnow().isoformat(), row_id),
            )
        return (cur.rowcount or 0) > 0

    def outbox_counts(self) -> dict[str, int]:
        """Row count per status — what a health endpoint or the drain's own
        log line reports."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM egeria_outbox GROUP BY status"
            ).fetchall()
        return {r["status"]: int(r["n"]) for r in rows}

    def preview_outbox_cancel(
        self, run_id: str = "", entity_slug: str = "", element_kind: str = "",
        created_before: str = "",
    ) -> dict:
        """What cancel_outbox_batch would affect, WITHOUT changing anything.

        Exists because the first hand-written DELETE that cancelled a bad
        batch on 2026-09-01 matched 39,603 of 48,113 rows — two buggy survey
        runs were queued, not one. Printing the count first is the only
        reason 8,510 bogus annotations were not left to publish on the next
        restart. So the count is not an optional courtesy here; it is the
        step that makes the operation safe, and it gets its own method so it
        cannot be skipped by forgetting to run a query by hand.

        Returns matched/total_cancellable/surviving plus a per-run
        breakdown — because "matched" alone cannot tell you whether the
        selection is the whole of the problem or part of it.
        """
        where, args = self._outbox_cancel_where(
            run_id, entity_slug, element_kind, created_before)
        with self._conn() as conn:
            matched = conn.execute(
                f"SELECT COUNT(*) AS n FROM egeria_outbox WHERE {where}",  # noqa: S608
                args,
            ).fetchone()["n"]
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM egeria_outbox "
                "WHERE status IN ('pending', 'failed')"
            ).fetchone()["n"]
            by_run = conn.execute(
                f"SELECT run_id, COUNT(*) AS n FROM egeria_outbox WHERE {where} "  # noqa: S608
                "GROUP BY run_id ORDER BY n DESC",
                args,
            ).fetchall()
        return {
            "matched": matched,
            "total_cancellable": total,
            "surviving": total - matched,
            "by_run": {r["run_id"]: r["n"] for r in by_run},
        }

    def cancel_outbox_batch(
        self, run_id: str = "", entity_slug: str = "", element_kind: str = "",
        created_before: str = "", reason: str = "",
    ) -> int:
        """Cancel queued outbox work. Returns rows cancelled.

        Queued work that turns out to be wrong is the normal consequence of
        finding a bug in something that already ran, and until now there was
        no supported way to stop it: purge_outbox_completed() handles 'done'
        rows only, deliberately, so cancelling meant a hand-written DELETE
        against live data.

        Rows are marked **cancelled, not deleted** — the record of the
        mistake is worth more than the disk it occupies, and a deleted row
        cannot answer "why did 48,000 annotations never reach Egeria".
        claim_due_outbox_elements() selects status IN ('pending','failed'),
        so a cancelled row is inert without any change there.

        At least one filter is required. A call with no arguments would
        cancel every queued row in the system, which is never what anyone
        means; it raises instead.
        """
        if not any((run_id, entity_slug, element_kind, created_before)):
            raise ValueError(
                "cancel_outbox_batch needs at least one filter — an unfiltered "
                "call would cancel every queued row. Pass run_id, entity_slug, "
                "element_kind or created_before."
            )
        where, args = self._outbox_cancel_where(
            run_id, entity_slug, element_kind, created_before)
        note = f"cancelled: {reason}" if reason else "cancelled"
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                f"UPDATE egeria_outbox SET status = 'cancelled', "  # noqa: S608
                f"last_error = ?, completed_at = ? WHERE {where}",
                [note, now, *args],
            )
        return cur.rowcount or 0

    @staticmethod
    def _outbox_cancel_where(
        run_id: str, entity_slug: str, element_kind: str, created_before: str,
    ) -> tuple[str, list]:
        """Shared predicate, so preview and cancel can never disagree.

        Only 'pending' and 'failed' are cancellable: 'done' rows already
        reached Egeria and cancelling one would misrepresent what happened,
        and 'dead' rows are the ones a human is meant to look at.
        """
        where = ["status IN ('pending', 'failed')"]
        args: list = []
        if run_id:
            where.append("run_id = ?")
            args.append(run_id)
        if entity_slug:
            where.append("entity_slug = ?")
            args.append(entity_slug)
        if element_kind:
            where.append("element_kind = ?")
            args.append(element_kind)
        if created_before:
            where.append("created_at < ?")
            args.append(created_before)
        return " AND ".join(where), args

    def purge_outbox_completed(self, older_than_days: int = 14) -> int:
        """Drop 'done' rows past the retention window. Returns rows removed.

        Only 'done' — 'dead' rows are the ones a human still has to look at,
        and failed/pending rows are live work. Retention exists from day one
        because one proposal publish on egeria_git is ~2,100 rows at the
        largest run observed, not because today's 831-row total needs it.
        """
        cutoff = (datetime.utcnow() - timedelta(days=older_than_days)).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM egeria_outbox WHERE status='done' AND completed_at <> '' "
                "AND completed_at < ?", (cutoff,),
            )
        return cur.rowcount or 0

    def clear_egeria_registration(self, slug: str) -> dict:
        """Clear the cached Egeria GUID and all published survey records for a project.

        Returns {"asset_guid_cleared", "surveys_deleted", "published_types_deleted"}
        so callers can report what was removed. Safe to call when the project is
        not registered.

        project_published_annotation_types goes too. Those rows are local claims
        that Egeria holds annotations of a given type, and they are what the
        Analyses cards' "Published"/"Repo published" badge is derived from. If
        the asset they were published against no longer resolves, the claim is
        false, and leaving it would put a Published badge on a repo whose
        catalog entry is gone — the same "published to a catalog that no longer
        holds it" this sweep exists to end, one table further along.
        """
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT egeria_asset_guid FROM projects WHERE slug = ?", (slug,)
            ).fetchone()
            had_guid = bool(row and row["egeria_asset_guid"])
            conn.execute(
                "UPDATE projects SET egeria_asset_guid = NULL WHERE slug = ?", (slug,)
            )
            deleted = conn.execute(
                "DELETE FROM project_egeria_surveys WHERE project_slug = ?", (slug,)
            ).rowcount
            published = conn.execute(
                "DELETE FROM project_published_annotation_types WHERE project_slug = ?",
                (slug,),
            ).rowcount
        return {
            "asset_guid_cleared": had_guid, "surveys_deleted": deleted,
            "published_types_deleted": published,
        }

    def record_egeria_survey(
        self,
        slug: str,
        surveyed_at: str,
        report_guid: str,
        annotation_count: int | None = None,
    ) -> None:
        """Record a published Egeria SurveyReport GUID for a project survey run."""
        slug = self._normalize_slug(slug)
        published_at = datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO project_egeria_surveys
                   (project_slug, surveyed_at, egeria_report_guid, published_at, annotation_count)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(project_slug, surveyed_at)
                   DO UPDATE SET egeria_report_guid = excluded.egeria_report_guid,
                                 published_at = excluded.published_at,
                                 annotation_count = excluded.annotation_count""",
                (slug, surveyed_at, report_guid, published_at, annotation_count),
            )

    def get_egeria_surveys(self, slug: str) -> list[dict]:
        """Return all published Egeria survey records for a project, newest first."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT project_slug, surveyed_at, egeria_report_guid, published_at, annotation_count
                   FROM project_egeria_surveys
                   WHERE project_slug = ?
                   ORDER BY surveyed_at DESC""",
                (slug,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_latest_egeria_survey(self, slug: str) -> dict | None:
        """Return the most recent published Egeria survey record, or None."""
        surveys = self.get_egeria_surveys(slug)
        return surveys[0] if surveys else None

    def get_latest_egeria_surveys_all_projects(self) -> dict[str, dict]:
        """{project_slug: latest survey row}, one query for every project —
        what a resolve-against-Egeria pass needs to check without a
        per-project round trip. Mirrors `_scan_assets`' own bulk-query shape
        in egeria_resync.py. Only rows carrying a real `egeria_report_guid`
        are returned; a survey published with none is not a publish claim
        this check has anything to resolve."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT s.project_slug, s.surveyed_at, s.egeria_report_guid,
                          s.published_at, s.annotation_count
                   FROM project_egeria_surveys s
                   JOIN (SELECT project_slug, MAX(surveyed_at) AS ts
                         FROM project_egeria_surveys GROUP BY project_slug) latest
                     ON s.project_slug = latest.project_slug AND s.surveyed_at = latest.ts
                   WHERE coalesce(s.egeria_report_guid, '') <> ''"""
            ).fetchall()
        return {r["project_slug"]: dict(r) for r in rows}

    def record_published_annotation_types(
        self, slug: str, annotation_types: set[str], report_guid: str = "",
        published_at: str | None = None,
    ) -> None:
        """One row per distinct annotation_type actually published in this
        publish — called from EgeriaPublisher.publish() right after it
        creates the real Egeria Annotation elements, so the Survey Results
        dashboards can show a genuine per-card 'last published' badge
        (get_last_published_annotation_types()) without re-deriving it from
        a live Egeria read later. Best-effort by design: the caller wraps
        this in its own try/except — a bookkeeping failure here must never
        fail or roll back the real publish that already succeeded.

        published_at: defaults to now (the live-publish call site's use
        case). scripts/backfill_published_annotation_types.py passes the
        real historical published_at instead, when backfilling from an
        already-existing project_egeria_surveys row — the whole point of a
        backfill is to record what already happened, not to claim it just
        happened now."""
        if not annotation_types:
            return
        slug = self._normalize_slug(slug)
        if published_at is None:
            published_at = datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.executemany(
                """INSERT INTO project_published_annotation_types
                   (project_slug, annotation_type, published_at, egeria_report_guid)
                   VALUES (?, ?, ?, ?)""",
                [(slug, at, published_at, report_guid) for at in annotation_types],
            )

    def record_published_analyses(
        self, slug: str, analysis_ids, report_guid: str = "",
        published_at: str | None = None,
    ) -> None:
        """One row per analysis actually covered by this publish.

        Best-effort like its annotation-type sibling: the caller wraps it, and
        a bookkeeping failure must never roll back a publish that succeeded.
        """
        analysis_ids = sorted(set(analysis_ids or []))
        if not analysis_ids:
            return
        slug = self._normalize_slug(slug)
        if published_at is None:
            published_at = datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.executemany(
                """INSERT INTO project_published_analyses
                   (project_slug, analysis_id, published_at, egeria_report_guid)
                   VALUES (?, ?, ?, ?)""",
                [(slug, aid, published_at, report_guid) for aid in analysis_ids],
            )

    def get_last_published_analyses(self, slug: str) -> dict[str, str]:
        """{analysis_id: latest published_at} — a direct answer, no inference."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT analysis_id, MAX(published_at) AS published_at
                   FROM project_published_analyses
                   WHERE project_slug = ? GROUP BY analysis_id""",
                (slug,),
            ).fetchall()
        return {r["analysis_id"]: r["published_at"] for r in rows}

    def get_last_published_annotation_types(self, slug: str) -> dict[str, str]:
        """{annotation_type: latest published_at} for this project — the
        Survey Results route joins this against each dashboard's own
        analysis_ids' known annotation_types (analysis_catalog.yaml) to
        derive a real per-dashboard last-published timestamp."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT annotation_type, MAX(published_at) AS published_at
                   FROM project_published_annotation_types
                   WHERE project_slug = ?
                   GROUP BY annotation_type""",
                (slug,),
            ).fetchall()
        return {r["annotation_type"]: r["published_at"] for r in rows}

    def has_published_annotation_types_for_report(self, slug: str, report_guid: str) -> bool:
        """True if this exact SurveyReport GUID already has rows —
        scripts/backfill_published_annotation_types.py's idempotency check,
        so a rerun skips reports it already backfilled instead of inserting
        duplicate rows (which would just re-confirm the same timestamp,
        harmless but wasteful and noisy in query results)."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            row = conn.execute(
                """SELECT 1 FROM project_published_annotation_types
                   WHERE project_slug = ? AND egeria_report_guid = ? LIMIT 1""",
                (slug, report_guid),
            ).fetchone()
        return row is not None

    def get_published_annotation_types_for_report(self, slug: str, report_guid: str) -> set[str]:
        """The annotation types actually published under ONE SurveyReport.

        This is the run-keyed half of declared-vs-received. A run's activity
        row carries `egeria_report_guid`, and record_published_annotation_types()
        stamps every row with the report it went into, so what a run produced
        can be read back exactly — by report, not by timestamp. Measured
        2026-09-11: all 152 rows carry a report GUID, across 104 reports.

        Empty set for an unpublished run, which the caller must treat as
        "cannot be listed from here" rather than "nothing was produced": the
        two are different claims and only publishing makes the first one
        answerable."""
        slug = self._normalize_slug(slug)
        if not report_guid:
            return set()
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT DISTINCT annotation_type FROM project_published_annotation_types
                   WHERE project_slug = ? AND egeria_report_guid = ?""",
                (slug, report_guid),
            ).fetchall()
        return {r["annotation_type"] if isinstance(r, dict) else r[0] for r in rows}

    def count_projects_published_annotation_type(self, annotation_type: str) -> int:
        """How many distinct projects have a local record of publishing this
        annotation type at least once — the blast-radius number Admin's
        Annotation Types registry shows before a rename/delete
        (SPEC-ADMIN-THE-FOUR-GAPS.md §4/§0).

        **This is a lower bound, not the true annotation count.** RE keeps no
        durable local table of individual annotation instances by type — the
        actual AnnotationType value only ever lives inside `egeria_outbox`'s
        `payload_json` (purged once a write completes) or in Egeria itself.
        `project_published_annotation_types` is the cheapest thing that is
        both indexed and real: one row per (project, type) per publish. It
        answers "how many projects have published this type at least once",
        which undercounts if the same project holds several annotations of
        the type, or if a project's real annotations were never bookkept
        here (a publish that predates this table, or a best-effort insert
        that silently failed — record_published_annotation_types() wraps its
        own insert in the caller's try/except by design). Callers must
        present this as a lower bound / "at least N", never as an exact
        count — and a result of 0 must not be presented as "unused", since
        it means "no local publish record", not "no annotations exist"."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT COUNT(DISTINCT project_slug) AS n
                   FROM project_published_annotation_types
                   WHERE annotation_type = ?""",
                (annotation_type,),
            ).fetchone()
        return int(row["n"] if row else 0)

    def get_latest_project_stats(self, slug: str) -> dict | None:
        """Return the most recent project_stats row as a dict, or None."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM project_stats WHERE project_slug = ? ORDER BY id DESC LIMIT 1",
                (slug,),
            ).fetchone()
        return dict(row) if row else None

    # ── dependency graph ──────────────────────────────────────────────────────

    def upsert_dependencies(self, slug: str, deps: list[dict]) -> None:
        """Append a new ingest/refresh's dependency list. Historical runs are
        kept for trending (mirrors upsert_file_type_counts's pattern) —
        this used to DELETE the prior row set before inserting, destroying
        any ability to see dependency count over time. indexed_at is the
        shared run-timestamp within one call (same value for every row),
        matching query_file_type_counts's surveyed_at-batch convention."""
        if not deps:
            return
        slug = self._normalize_slug(slug)
        indexed_at = datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO project_dependencies "
                "(project_slug, dep_name, dep_version, dep_version_source, dep_type, "
                "ecosystem, source_file, indexed_at) "
                "VALUES (:project_slug, :dep_name, :dep_version, :dep_version_source, :dep_type, "
                ":ecosystem, :source_file, :indexed_at)",
                # .get(..., "") because not every caller (older tests, hand-built
                # dicts) sets dep_version_source — absence means "provenance not
                # stated", the same '' a row with no version at all gets, never
                # a guess at what it would have been.
                [{**d, "dep_version_source": d.get("dep_version_source", ""),
                  "project_slug": slug, "indexed_at": indexed_at} for d in deps],
            )

    def query_dependencies(
        self,
        slug: str,
        dep_type: str | None = None,
        ecosystem: str | None = None,
    ) -> list[dict]:
        """Return the most recent ingest/refresh's dependency rows only —
        upsert_dependencies() now appends rather than replacing, so without
        this MAX(indexed_at) filter every historical run's rows would show
        up together (matches query_file_type_counts's latest-batch pattern)."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            latest_ts = conn.execute(
                "SELECT MAX(indexed_at) FROM project_dependencies WHERE project_slug = ?",
                (slug,),
            ).fetchone()[0]
            if not latest_ts:
                return []
            filters = ["project_slug = ?", "indexed_at = ?"]
            params: list = [slug, latest_ts]
            if dep_type and dep_type != "all":
                filters.append("dep_type = ?")
                params.append(dep_type)
            if ecosystem:
                filters.append("ecosystem = ?")
                params.append(ecosystem)
            where = " AND ".join(filters)
            rows = conn.execute(
                f"SELECT dep_name, dep_version, dep_version_source, dep_type, ecosystem, source_file "  # noqa: S608
                f"FROM project_dependencies WHERE {where} ORDER BY ecosystem, dep_type, dep_name",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def query_dependencies_history(self, slug: str) -> list[dict]:
        """Return one row per ingest/refresh run with total dependency count
        and timestamp, for trending — mirrors query_file_type_history()."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT indexed_at AS surveyed_at, COUNT(*) as total_dependencies "
                "FROM project_dependencies WHERE project_slug = ? "
                "GROUP BY indexed_at ORDER BY indexed_at ASC",
                (slug,),
            ).fetchall()
        return [dict(r) for r in rows]

    def query_shared_dependencies(self, slugs: list[str], dep_name: str | None = None) -> list[dict]:
        """Find dependencies shared across the given list of projects."""
        norms = [self._normalize_slug(s) for s in slugs]
        with self._conn() as conn:
            placeholders = ",".join("?" * len(norms))
            # HAVING references COUNT(*) directly, not the project_count
            # alias — SQLite allows referencing a SELECT-list alias in
            # HAVING, Postgres does not (HAVING is evaluated before aliases
            # are available in standard SQL semantics).
            rows = conn.execute(
                f"SELECT dep_name, ecosystem, GROUP_CONCAT(project_slug) as projects, COUNT(*) as project_count "  # noqa: S608
                f"FROM project_dependencies WHERE project_slug IN ({placeholders}) "
                f"GROUP BY dep_name, ecosystem HAVING COUNT(*) >= 2 ORDER BY project_count DESC, dep_name",
                norms,
            ).fetchall()
        return [dict(r) for r in rows]

    # ── conversation history ──────────────────────────────────────────────────

    def append_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        resource_slug: str | None = None,
        *,
        user_id: str | None = None,
        compile_id: str | None = None,
    ) -> None:
        """Append a single turn (user or assistant) to the conversation log.

        Stamped with the caller (plan §4). The plan's isolation matrix is
        explicit that conversation history is read only by its owner while RAG
        retrieval stays shared: the corpus is shared by design, a person's own
        questions are not.
        """
        user_id = current_user_id() if user_id is None else user_id
        with self._conn() as conn:
            next_idx = (
                conn.execute(
                    "SELECT COALESCE(MAX(turn_idx), -1) + 1 FROM conversation_history WHERE session_id = ?",
                    (session_id,),
                ).fetchone()[0]
            )
            conn.execute(
                "INSERT INTO conversation_history (session_id, turn_idx, role, content, "
                "project_slug, user_id, created_at, compile_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, next_idx, role, content, resource_slug or "", user_id,
                 datetime.utcnow().isoformat(), compile_id),
            )

    # ── Context compiles ─────────────────────────────────────────────────

    def record_compile(
        self,
        compile_id: str,
        resource_slug: str,
        question: str,
        manifest: dict,
        derivation: list,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> dict:
        """Persist one compile, or count a repeat of it.

        Same inputs → same compile_id → the existing row's `hits` goes up and
        `last_seen_at` moves; a new compile_id inserts. The first writer's
        user/session/timestamp stay on the row: the row describes the compile,
        and a repeat by someone else is evidence of replay, not a new compile.
        Returns {"compile_id", "created": bool}.
        """
        import json as _json
        user_id = current_user_id() if user_id is None else user_id
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT compile_id FROM context_compiles WHERE compile_id = ?", (compile_id,)
            ).fetchone()
            if row is not None:
                conn.execute(
                    "UPDATE context_compiles SET hits = hits + 1, last_seen_at = ? "
                    "WHERE compile_id = ?",
                    (now, compile_id),
                )
                return {"compile_id": compile_id, "created": False}
            conn.execute(
                "INSERT INTO context_compiles (compile_id, project_slug, question, spec_id, "
                "budget, used, manifest, derivation, user_id, session_id, hits, created_at, "
                "last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (compile_id, resource_slug, question, str(manifest.get("spec_id", "")),
                 int(manifest.get("budget", 0) or 0), int(manifest.get("used", 0) or 0),
                 _json.dumps(manifest, sort_keys=True, default=str),
                 _json.dumps(derivation, sort_keys=True, default=str),
                 user_id, session_id or "", now, now),
            )
        return {"compile_id": compile_id, "created": True}

    def get_compile(self, compile_id: str) -> dict | None:
        """One persisted compile, manifest and derivation decoded, or None."""
        import json as _json
        with self._conn() as conn:
            row = conn.execute(
                "SELECT compile_id, project_slug, question, spec_id, budget, used, manifest, "
                "derivation, user_id, session_id, hits, created_at, last_seen_at "
                "FROM context_compiles WHERE compile_id = ?", (compile_id,)
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["manifest"] = _json.loads(d["manifest"])
        d["derivation"] = _json.loads(d["derivation"])
        return d

    def load_turns(
        self, session_id: str, limit: int = 40, *, user_id: str | None = None,
    ) -> list[dict]:
        """Up to `limit` most-recent turns for a session, in chronological order.

        Scoped to the caller plus the `''` bucket. `''` covers every turn
        written before this column existed *and* an unauthenticated CLI
        session; excluding it would make a person's own history disappear the
        first time they signed in, which reads as data loss rather than as
        privacy.

        Note the ordering is over `turn_idx`, so a session mixing owners still
        replays in order. It should not mix owners — `session_id` is minted per
        person — but the scoping filter must not be what depends on that.
        """
        user_id = current_user_id() if user_id is None else user_id
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT role, content, project_slug FROM conversation_history "
                "WHERE session_id = ? AND user_id IN (?, ?) ORDER BY turn_idx DESC LIMIT ?",
                (session_id, user_id, SHARED_USER_ID, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def list_sessions(self, *, user_id: str | None = None) -> list[dict]:
        """Summary of this caller's stored sessions (id, turn count, last activity)."""
        user_id = current_user_id() if user_id is None else user_id
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT session_id, COUNT(*) as turns, MAX(created_at) as last_at "
                "FROM conversation_history WHERE user_id IN (?, ?) "
                "GROUP BY session_id ORDER BY last_at DESC",
                (user_id, SHARED_USER_ID),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_by_github_url(self, url: str) -> "Project | None":
        """Look up a project by GitHub URL, normalizing away .git suffix and case."""
        normalized = url.lower().rstrip("/").removesuffix(".git")
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM projects").fetchall()
        for row in rows:
            stored = row["github_url"].lower().rstrip("/").removesuffix(".git")
            if stored == normalized:
                return self._row_to_project(row)
        return None

    @staticmethod
    def _normalize_github_url(url: str) -> str:
        """Same normalization get_by_github_url() already uses — shared here
        so a disposition set against '.../repo.git' is found when later
        queried as '.../repo' (and vice versa)."""
        return url.lower().rstrip("/").removesuffix(".git")

    def resolve_repo_entity_slug(self, github_url: str) -> str:
        """The entity_slug a repo's disposition is keyed on, for a given
        github_url — the project's real slug if it has been imported
        (which can legitimately differ from a url-derived guess: a manual
        slug override, or a collision-avoidance rename), else the same
        url-derived slug `org_importer.py`'s `_url_to_slug` already uses for
        a not-yet-imported candidate elsewhere (`resource_working_set`/
        `activity_log`). Exposed as its own method (not folded into
        `set_disposition`) so `add()` can call it too, to reconcile a
        candidate's disposition row onto the real slug at import time when
        the two differ.

        Normalizes the URL before deriving the fallback slug —
        `_url_to_slug` (unlike this class's own `_normalize_github_url`)
        does not strip a `.git` suffix, so '.../bar.git' and '.../bar'
        would otherwise resolve to different slugs ('bar_git' vs 'bar') for
        what is the same repo."""
        from resource_explorer.github.org_importer import _url_to_slug

        project = self.get_by_github_url(github_url)
        return project.slug if project else _url_to_slug(self._normalize_github_url(github_url))

    def set_disposition(
        self, github_url: str, disposition: str,
        reason: str = "", decided_by: str = "", resource_slug: str = "",
    ) -> None:
        """Upsert the current triage disposition for a repo — repo-specific
        convenience over `set_disposition_for_entity` (entity_type='repo',
        entity_slug=`resolve_repo_entity_slug(github_url)`). Applies whether
        or not the repo has been imported yet — project_slug is best-effort
        context, not required. Kept github_url-in, github_url-out rather
        than folded away once the underlying table generalized
        (Backlog.md, "Disposition is NOT fixed here", 2026-09-22): a repo's
        stable identity really is its github_url (import can rename its
        slug, `rename_project_slug` can rename it again later), so every
        one of this method's ~20 existing callers already has the right
        identifier in hand and gains nothing from computing a slug
        themselves that this method computes once, correctly, either way."""
        key = self._normalize_github_url(github_url)
        entity_slug = self.resolve_repo_entity_slug(github_url)
        self.set_disposition_for_entity(
            "repo", entity_slug, disposition,
            reason=reason, decided_by=decided_by,
            github_url=key, project_slug=resource_slug,
        )

    def get_disposition(self, github_url: str) -> dict | None:
        """The current disposition for a repo, or None if nobody has ever
        decided on it (callers should treat that the same as "undecided")."""
        return self.get_disposition_for_entity("repo", self.resolve_repo_entity_slug(github_url))

    def get_disposition_history(self, github_url: str) -> list[dict]:
        """Every disposition ever set for this repo, oldest first — backs
        the Disposition sub-tab's timeline view. `depth_offer` comes back
        parsed (a dict) or None — never the raw JSON string."""
        return self.get_disposition_history_for_entity("repo", self.resolve_repo_entity_slug(github_url))

    def set_disposition_for_entity(
        self, entity_type: str, entity_slug: str, disposition: str,
        reason: str = "", decided_by: str = "", github_url: str = "", project_slug: str = "",
    ) -> None:
        """Upsert the current triage disposition for any entity — one row
        per (entity_type, entity_slug), overwriting any prior decision
        (unlike the append-only findings/metrics tables, there's only ever
        one *current* disposition) — and append a row to
        repo_disposition_history so the Disposition sub-tab can show a real
        timeline, not just the latest value ("Discover repos to scout"
        plan, D10; Scouting workflow redesign plan, D3/D6). This is the
        generalized primitive (Backlog.md, "Disposition is NOT fixed
        here", 2026-09-22) — `database`/`filesystem` callers use this
        directly; repo callers go through `set_disposition`, which resolves
        `entity_slug` and always passes `entity_type='repo'`."""
        decided_at = datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO repo_dispositions
                       (entity_type, entity_slug, github_url, disposition, reason, decided_by, decided_at, project_slug)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(entity_type, entity_slug) DO UPDATE SET
                       github_url = excluded.github_url,
                       disposition = excluded.disposition,
                       reason = excluded.reason,
                       decided_by = excluded.decided_by,
                       decided_at = excluded.decided_at,
                       project_slug = excluded.project_slug""",
                (entity_type, entity_slug, github_url, disposition, reason, decided_by, decided_at, project_slug),
            )
            conn.execute(
                """INSERT INTO repo_disposition_history
                       (entity_type, entity_slug, github_url, disposition, reason, decided_by, decided_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (entity_type, entity_slug, github_url, disposition, reason, decided_by, decided_at),
            )

    def get_disposition_for_entity(self, entity_type: str, entity_slug: str) -> dict | None:
        """The current disposition for any entity, or None if nobody has
        ever decided on it (callers should treat that the same as
        "undecided")."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM repo_dispositions WHERE entity_type = ? AND entity_slug = ?",
                (entity_type, entity_slug),
            ).fetchone()
        return dict(row) if row else None

    def get_disposition_history_for_entity(self, entity_type: str, entity_slug: str) -> list[dict]:
        """Every disposition ever set for this entity, oldest first — backs
        the Disposition sub-tab's timeline view. `depth_offer` comes back
        parsed (a dict) or None — never the raw JSON string."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT disposition, reason, decided_by, decided_at, depth_offer "
                "FROM repo_disposition_history WHERE entity_type = ? AND entity_slug = ? "
                "ORDER BY decided_at ASC",
                (entity_type, entity_slug),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            raw = d.get("depth_offer")
            d["depth_offer"] = json.loads(raw) if raw else None
            result.append(d)
        return result

    def record_depth_offer(
        self, github_url: str, outcome: str, analysis_ids: list[str],
        run_ids: list[str], decided_by: str,
    ) -> dict:
        """Record the outcome of a DepthOffer pane on the LATEST
        repo_disposition_history row for this url (designer, 2026-09-13).

        Once per verdict: refuses (raises `ValueError`) a second write once
        the latest row already carries a `depth_offer` — the pane only
        offers when it reads null, but that is a UI courtesy, not the
        enforcement; this is. Raises `LookupError` when there is no
        disposition history row at all for this url (nothing to attach the
        offer to — a verdict has to exist first). Both are ValueError's
        cousins by convention (see `set_disposition`'s callers), kept as two
        distinct exception TYPES rather than two message strings so a route
        can map them to 404 vs 409 without parsing text.

        `decided_by` is the caller's job to resolve (the signed-in user via
        `run_queue.requested_by()`) — this method just stores whatever it is
        given, the same trust boundary `enqueue_run`'s own `requested_by`
        param already has."""
        from datetime import UTC, datetime

        from resource_explorer.workflows.depth_offer import DEPTH_OFFER_OUTCOMES

        if outcome not in DEPTH_OFFER_OUTCOMES:
            raise ValueError(
                f"outcome must be one of {sorted(DEPTH_OFFER_OUTCOMES)}, got {outcome!r}"
            )
        key = self._normalize_github_url(github_url)
        payload = {
            "offered_at": datetime.now(UTC).isoformat(),
            "outcome": outcome,
            "analysis_ids": list(analysis_ids or []),
            "run_ids": list(run_ids or []),
            "decided_by": decided_by,
        }
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, depth_offer FROM repo_disposition_history "
                "WHERE github_url = ? ORDER BY id DESC LIMIT 1",
                (key,),
            ).fetchone()
            if row is None:
                raise LookupError(f"no disposition history for {github_url!r}")
            if row["depth_offer"]:
                raise ValueError(f"depth offer already recorded for {github_url!r}")
            conn.execute(
                "UPDATE repo_disposition_history SET depth_offer = ? WHERE id = ?",
                (json.dumps(payload), row["id"]),
            )
        return payload

    # ── sub-resources — the local "Catalog" stage of the repo scope-
    # narrowing funnel (docs/repo-scope-narrowing-funnel.md, D2/D4) ────────────

    def catalog_sub_resource(
        self, resource_type: str, resource_slug: str, locator: str, kind: str,
        source_finding: str = "", detail: dict | None = None,
    ) -> None:
        """Track a sub-resource locally — idempotent: re-cataloging an
        already-tracked locator is a no-op and never clobbers a previously
        set egeria_guid (D4 — catalog is a repeatable action, not a
        one-time gate; a user coming back later with more information
        should be able to add to the selection without disturbing what's
        already there)."""
        resource_slug = self._normalize_slug(resource_slug)
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT id FROM sub_resources WHERE resource_type=? AND resource_slug=? AND locator=?",
                (resource_type, resource_slug, locator),
            ).fetchone()
            if existing:
                return
            conn.execute(
                """INSERT INTO sub_resources
                       (resource_type, resource_slug, locator, kind, cataloged_at, source_finding, detail_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    resource_type, resource_slug, locator, kind,
                    datetime.utcnow().isoformat(), source_finding,
                    json.dumps(detail) if detail else "",
                ),
            )

    def uncatalog_sub_resource(self, resource_type: str, resource_slug: str, locator: str) -> None:
        """Remove a sub-resource from local tracking — reversible, does not
        touch anything already published to Egeria (that asset stays put;
        only RE's own local record of "we're tracking this" goes away)."""
        resource_slug = self._normalize_slug(resource_slug)
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM sub_resources WHERE resource_type=? AND resource_slug=? AND locator=?",
                (resource_type, resource_slug, locator),
            )

    def list_sub_resources(self, resource_type: str, resource_slug: str) -> list[dict]:
        resource_slug = self._normalize_slug(resource_slug)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM sub_resources WHERE resource_type=? AND resource_slug=? ORDER BY locator",
                (resource_type, resource_slug),
            ).fetchall()
        return [dict(r) for r in rows]

    def set_sub_resource_egeria_guid(
        self, resource_type: str, resource_slug: str, locator: str, guid: str,
    ) -> None:
        resource_slug = self._normalize_slug(resource_slug)
        with self._conn() as conn:
            conn.execute(
                "UPDATE sub_resources SET egeria_guid=? WHERE resource_type=? AND resource_slug=? AND locator=?",
                (guid, resource_type, resource_slug, locator),
            )

    # ── discovery sources ("Scouting workflow redesign" plan, D1/D2) ───────────

    def create_discovery_source(
        self, slug: str, display_name: str, source_type: str, config: dict,
    ) -> None:
        """Create (or overwrite) a named, reusable discovery source —
        source_type is 'search' (config = the RepoSearchRequest filter
        fields) or 'list' (config = {"urls": [...]})."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO discovery_sources (slug, display_name, source_type, config_json, created_at)
                       VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(slug) DO UPDATE SET
                       display_name = excluded.display_name,
                       source_type = excluded.source_type,
                       config_json = excluded.config_json""",
                (slug, display_name, source_type, json.dumps(config), datetime.utcnow().isoformat()),
            )

    def get_discovery_source(self, slug: str) -> dict | None:
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM discovery_sources WHERE slug = ?", (slug,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["config"] = json.loads(d.pop("config_json"))
        return d

    def list_discovery_sources(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM discovery_sources ORDER BY display_name"
            ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["config"] = json.loads(d.pop("config_json"))
            out.append(d)
        return out

    def delete_discovery_source(self, slug: str) -> int:
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            cursor = conn.execute("DELETE FROM discovery_sources WHERE slug = ?", (slug,))
            return cursor.rowcount

    # ── personal working set ("Scouting workflow redesign" plan, D4) ───────────
    # Separate axis from repo_dispositions — "do I want this in front of me
    # right now" (a view preference) vs. "what's the canonical judgment on
    # this resource" (disposition). Global for now (no per-person auth
    # exists yet), architected as its own table so a user_id column can be
    # added later without touching disposition's model.

    def set_working_set_hidden(
        self, entity_type: str, entity_slug: str, hidden: bool,
        *, user_id: str | None = None,
    ) -> None:
        """Stamp this view preference with the caller (plan §4).

        `user_id=None` means "whoever is signed in", resolved here rather than
        by the caller — see `current_user_id`. A route cannot forget to scope
        a write it never spells out.
        """
        entity_slug = self._normalize_slug(entity_slug)
        user_id = current_user_id() if user_id is None else user_id
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO resource_working_set
                       (entity_type, entity_slug, user_id, hidden, updated_at)
                       VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(entity_type, entity_slug, user_id) DO UPDATE SET
                       hidden = excluded.hidden, updated_at = excluded.updated_at""",
                (entity_type, entity_slug, user_id, int(hidden),
                 datetime.utcnow().isoformat()),
            )

    def is_working_set_hidden(
        self, entity_type: str, entity_slug: str, *, user_id: str | None = None,
    ) -> bool:
        """This caller's view preference, falling back to the shared/legacy row.

        Two rows can exist for one resource — the caller's own and the `''`
        bucket written before scoping existed. The caller's own wins; the
        shared one is what keeps a pre-existing filter working for the person
        who set it, rather than silently un-hiding everything they had hidden
        the moment they signed in for the first time.
        """
        entity_slug = self._normalize_slug(entity_slug)
        user_id = current_user_id() if user_id is None else user_id
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT user_id, hidden FROM resource_working_set "
                "WHERE entity_type = ? AND entity_slug = ? AND user_id IN (?, ?)",
                (entity_type, entity_slug, user_id, SHARED_USER_ID),
            ).fetchall()
        if not rows:
            return False
        by_user = {r["user_id"]: r for r in rows}
        row = by_user.get(user_id) or by_user.get(SHARED_USER_ID)
        return bool(row["hidden"]) if row else False

    # ── Investigations (docs/investigation-framing-design.md §1) ──────────
    #
    # The framing step ahead of Scouting. Deliberately local-first with a
    # nullable egeria_project_guid so an investigation can exist before, or
    # without, any Egeria write — promotion is then a fill-in, not a migration.

    VALID_PURPOSES = (
        "Assess", "Certify", "Deploy", "Explore", "Learn", "Maintain", "Select", "Share",
    )

    #: §1 mode 2's classifications — Egeria's own, mirrored exactly, because
    #: they are sent as `initialClassifications` on the Project and a value
    #: Egeria does not know would be rejected at that boundary. All five
    #: `ProjectKind` subtypes from `OpenMetadataType.java` (model 0130), each
    #: with Egeria's own definition:
    #:
    #:   PersonalProject  an informal project an individual created to help
    #:                    them organize their own work
    #:   StudyProject     a focused analysis of a topic, person, object or
    #:                    situation
    #:   Task             a self-contained, short activity, typically for one
    #:                    or two people
    #:   Campaign         a long-term strategic initiative implemented through
    #:                    multiple related projects
    #:   Experiment       a project testing a hypothesis, recorded in the
    #:                    `hypothesis` attribute — see HYPOTHESIS_REQUIRED_FOR
    #:
    #: StudyProject is the default because it is **accurate** for most RE
    #: investigations, not because it is the least committal. (It read as the
    #: latter until 2026-09-07, which quietly encouraged reading it as "no
    #: commitment yet" — a meaning Egeria's StudyProject does not carry. The
    #: no-commitment case is `BINDING_LOCAL` below, which is a different axis.)
    PROJECT_CLASSIFICATIONS = (
        "PersonalProject", "Task", "StudyProject", "Campaign", "Experiment",
    )

    #: Classifications whose defining attribute must be supplied with them.
    #: `Experiment` exists to record a hypothesis; creating one with an empty
    #: hypothesis publishes a classification with its whole point missing —
    #: the catalog-description form of "we did not measure this" rendered as a
    #: measurement. See docs/investigation-classification-and-zoning-design.md §1.4.
    HYPOTHESIS_REQUIRED_FOR = ("Experiment",)

    #: Whether this investigation has, or is meant to have, an Egeria Project.
    #:
    #: This is a SECOND axis, deliberately not folded into
    #: `project_classification`. "Ad-hoc" — investigate something without
    #: committing to it — is not a kind of project Egeria has; it is the
    #: absence of a Project, which this schema already models as a nullable
    #: `egeria_project_guid`. Putting `adHoc` in the classification column
    #: would put a non-type in a column whose values are sent to Egeria as
    #: types, and it would be silently dropped or rejected there.
    #:
    #: The column records *intent*, which the GUID alone cannot: a promotion
    #: that has not run yet and one that will never run look identical from a
    #: null GUID, and only one of them is a deliberate choice.
    BINDING_LOCAL = "local"      # ad-hoc: no Egeria Project, by choice
    BINDING_EGERIA = "egeria"    # has one, or is meant to
    EGERIA_BINDINGS = (BINDING_LOCAL, BINDING_EGERIA)

    #: Classifications whose investigations are visible only to their creator.
    #: Owner's design, points 5 and 6: Personal and Experiment are the
    #: creator's; Task, Campaign and Study follow the normal rules.
    #:
    #: Visibility follows the CLASSIFICATION rather than a separate flag, which
    #: is what makes reclassification a visibility change (design §5 / Phase 6).
    PRIVATE_CLASSIFICATIONS = ("PersonalProject", "Experiment")

    def _may_see_investigation(self, row, user_id: str) -> bool:
        """Whether `user_id` may see this investigation.

        **This is RE-side visibility, not enforcement.** It decides what RE's
        own registry hands back; it is not a security boundary and must not be
        described as one. Two reasons, both worth knowing before relying on it:

        * The service/shared identity (`SHARED_USER_ID`) sees everything,
          because the worker legitimately acts on a user's behalf without
          carrying their token — see `egeria_identity`'s module docstring on
          why a queued run publishes as the service account. With
          `TRELLIS_ANONYMOUS_READ=true` (a dev-box override, per CLAUDE.md, not
          a supported mode) an anonymous caller is that identity too.
        * Nothing here touches Egeria. An artifact already published from a
          private investigation stays readable in Egeria regardless of what
          this returns. That is Phase 5's job, and until it lands "private"
          means "RE does not show it to others", no more.

        A row nobody owns (`created_by == SHARED_USER_ID`) is visible to
        everyone even when privately classified — see the migration comment.
        """
        classification = (row.get("project_classification") or "").strip()
        if classification not in self.PRIVATE_CLASSIFICATIONS:
            return True
        owner = (row.get("created_by") or SHARED_USER_ID)
        if owner == SHARED_USER_ID:
            return True          # ownerless: has always been visible to all
        if user_id == SHARED_USER_ID:
            return True          # service/shared identity
        return owner == user_id

    def create_investigation(self, display_name: str, *, description: str = "",
                             purposes: list[str] | None = None,
                             project_classification: str = "StudyProject",
                             egeria_binding: str = "egeria",
                             hypothesis: str = "",
                             egeria_project_guid: str = "",
                             egeria_project_qualified_name: str = "") -> dict:
        """Create one investigation.

        Every vocabulary here is validated rather than accepting free text.
        `purposes` because an unrecognised purpose would silently fail to rank
        anything later; `project_classification` because it is sent to Egeria
        as a type name; `egeria_binding` because it decides whether anything is
        sent at all.

        `hypothesis` is required for the classifications in
        `HYPOTHESIS_REQUIRED_FOR` and refused for the others — an `Experiment`
        without one asserts a classification whose entire point is missing,
        and a hypothesis on a `Task` would be silently discarded at publish
        time, which is worse than being told.
        """
        import json as _json
        import re as _re
        purposes = purposes or []
        bad = [p for p in purposes if p not in self.VALID_PURPOSES]
        if bad:
            raise ValueError(
                f"unknown purpose(s) {bad}; valid: {list(self.VALID_PURPOSES)}"
            )
        if project_classification not in self.PROJECT_CLASSIFICATIONS:
            raise ValueError(
                f"unknown classification {project_classification!r}; "
                f"valid: {list(self.PROJECT_CLASSIFICATIONS)}"
            )
        if egeria_binding not in self.EGERIA_BINDINGS:
            raise ValueError(
                f"unknown egeria_binding {egeria_binding!r}; "
                f"valid: {list(self.EGERIA_BINDINGS)}"
            )
        hypothesis = (hypothesis or "").strip()
        if project_classification in self.HYPOTHESIS_REQUIRED_FOR and not hypothesis:
            raise ValueError(
                f"{project_classification} requires a hypothesis — it is the "
                "attribute the classification exists to record"
            )
        if hypothesis and project_classification not in self.HYPOTHESIS_REQUIRED_FOR:
            raise ValueError(
                f"hypothesis is only meaningful for "
                f"{list(self.HYPOTHESIS_REQUIRED_FOR)}, not {project_classification!r}"
            )
        base = _re.sub(r"[^a-z0-9]+", "-", display_name.lower()).strip("-") or "investigation"
        slug, n = base, 1
        while self.get_investigation(slug):
            n += 1
            slug = f"{base}-{n}"
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO investigations
                   (slug, display_name, description, purposes_json,
                    project_classification, egeria_binding, hypothesis,
                    created_by, egeria_project_guid,
                    egeria_project_qualified_name, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)""",
                (slug, display_name, description, _json.dumps(purposes),
                 project_classification, egeria_binding, hypothesis,
                 # Resolved here, not taken from the caller — the same reason
                 # `current_user_id`'s docstring gives: a method that resolves
                 # it cannot be called without scoping; a route that has to
                 # remember can forget.
                 current_user_id(), egeria_project_guid,
                 egeria_project_qualified_name, now, now),
            )
        return self.get_investigation(slug)

    def get_investigation(self, slug: str, *,
                          user_id: str | None = None) -> dict | None:
        """One investigation, or `None` if it does not exist **or the caller
        may not see it**.

        The two collapse into one answer on purpose, and it is the opposite of
        this codebase's usual rule that absence must be distinguishable from
        emptiness. Here they must NOT be: a private investigation that answered
        403 rather than 404 would confirm its existence and leak its name to
        anyone who guessed a slug — and slugs are derived from display names,
        so they are guessable. "Not found" is the honest answer to a caller for
        whom it does not exist.

        **Scoping lives here rather than in the routes**, because every one of
        the ~15 investigation routes already funnels through this method, and a
        new route is exactly the place a per-route check gets forgotten. Routes
        then 404 with no change of their own.
        """
        import json as _json
        user_id = current_user_id() if user_id is None else user_id
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM investigations WHERE slug = ?", (slug,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        if not self._may_see_investigation(d, user_id):
            return None
        d["purposes"] = _json.loads(d.pop("purposes_json") or "[]")
        # Say what the visibility actually IS, rather than leaving the caller
        # to re-derive it from classification + created_by. `private` here
        # means "RE does not show this to other people" — not that Egeria is
        # withholding anything; see `_may_see_investigation`.
        owner = d.get("created_by") or SHARED_USER_ID
        is_private_kind = (d.get("project_classification") or "") in self.PRIVATE_CLASSIFICATIONS
        d["visibility"] = "private" if (is_private_kind and owner != SHARED_USER_ID) else "shared"
        d["is_mine"] = bool(owner) and owner == user_id
        if is_private_kind and owner == SHARED_USER_ID:
            # Not an error, and not silently fine either: the row predates
            # ownership being recorded, so it is privately CLASSIFIED and
            # visible to everyone. Surfaced so it can be claimed rather than
            # quietly contradicting its own classification.
            d["visibility_note"] = (
                "Classified as private, but created before RE recorded who owns "
                "an investigation — so it is still visible to everyone. Recreate "
                "it, or an owner can be set, to make it private."
            )
        # The shape the publish path already speaks, assembled here so callers
        # never rebuild it from the individual columns.
        d["egeria_context"] = {
            "status": d.get("egeria_project_status") or "unset",
            "egeria_project_guid": d.get("egeria_project_guid") or "",
            "egeria_project_qualified_name": d.get("egeria_project_qualified_name") or "",
            "free_text_name": d.get("egeria_free_text_name") or "",
        }
        d["member_count"] = len(self.list_investigation_members(slug))
        return d

    def list_investigations(self, *, include_closed: bool = False,
                            user_id: str | None = None) -> list[dict]:
        # Resolved once and passed down rather than left to each
        # get_investigation() call: the answer cannot change mid-list, and a
        # per-row lookup would invite a future caller to think it could.
        user_id = current_user_id() if user_id is None else user_id
        with self._conn() as conn:
            rows = conn.execute("SELECT slug FROM investigations ORDER BY created_at DESC").fetchall()
        # get_investigation() returns None for anything this caller may not
        # see, so the privacy filter is inherited rather than repeated here —
        # one predicate, not two that can drift.
        out = [self.get_investigation(r["slug"], user_id=user_id) for r in rows]
        # Deliberately tests `!= "closed"` rather than `== "open"`: a `suspended`
        # investigation is still reachable through the default (unfiltered) list.
        # `suspended` means paused-but-will-resume, and hiding it here would be
        # the exact bug this whole feature exists to fix — the user pauses
        # something and it vanishes as if it had been closed. Only `closed`
        # (truly finished) needs `include_closed=True` to surface. The kwarg
        # name stays `include_closed` rather than being widened to
        # `include_inactive` because closed is the only status this filter
        # ever hides.
        return [i for i in out if i and (include_closed or i.get("status") != "closed")]

    # Membership goes through a WorkingSet, mirroring
    # Project --ResourceList--> WorkingSet --CollectionMembership--> resource.
    # An investigation gets one working set lazily, on first use, so a brand-new
    # investigation genuinely has none rather than an empty shell.

    #: The dispositions a WorkingSet can carry. Same vocabulary the global
    #: repo_dispositions uses, minus `undecided` — "no decision yet" is the
    #: absence of a WorkingSet, not a WorkingSet of its own. Creating one would
    #: make "nobody has judged this" and "someone judged it as undecided"
    #: indistinguishable.
    WORKING_SET_DISPOSITIONS = (
        "tracking", "investigating", "recommended", "using", "abandoned", "ignored",
    )

    def get_or_create_folio(self, investigation_slug: str) -> dict | None:
        """The investigation's Folio — everything in scope, one per investigation."""
        return self._get_or_create_collection(investigation_slug, "folio", "")

    def get_or_create_disposition_set(self, investigation_slug: str,
                                      disposition: str) -> dict | None:
        """The WorkingSet for one disposition within this investigation.

        Created lazily: seven empty Collections per investigation would be noise,
        and an empty WorkingSet is indistinguishable from one nobody has used.
        """
        if disposition not in self.WORKING_SET_DISPOSITIONS:
            raise ValueError(
                f"unknown disposition {disposition!r}; valid: {list(self.WORKING_SET_DISPOSITIONS)}"
            )
        return self._get_or_create_collection(investigation_slug, "working_set", disposition)

    def _get_or_create_collection(self, investigation_slug: str, kind: str,
                                  disposition: str) -> dict | None:
        inv = self.get_investigation(investigation_slug)
        if not inv:
            return None
        with self._conn() as conn:
            row = conn.execute(
                """SELECT ws.slug FROM working_sets ws
                   JOIN investigation_resource_lists rl ON rl.working_set_slug = ws.slug
                   WHERE rl.investigation_slug = ? AND ws.collection_kind = ?
                     AND COALESCE(ws.disposition, '') = ?""",
                (investigation_slug, kind, disposition),
            ).fetchone()
            if row:
                ws_slug = row["slug"]
            else:
                ws_slug = (f"{investigation_slug}-folio" if kind == "folio"
                           else f"{investigation_slug}-ws-{disposition}")
                name = (f"{inv['display_name']}" if kind == "folio"
                        else f"{inv['display_name']} — {disposition}")
                now = datetime.utcnow().isoformat()
                # ON CONFLICT, not a bare INSERT: a working_sets row can outlive
                # its ResourceList link (an interrupted cleanup, a deleted
                # investigation), and the slug is derived rather than generated,
                # so the orphan is hit again the moment the same name recurs.
                # Adopting it is right — it is the same collection.
                conn.execute(
                    """INSERT INTO working_sets
                       (slug, display_name, collection_kind, disposition, created_at)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT (slug) DO UPDATE
                         SET display_name = EXCLUDED.display_name,
                             collection_kind = EXCLUDED.collection_kind,
                             disposition = EXCLUDED.disposition""",
                    (ws_slug, name, kind, disposition, now),
                )
                conn.execute(
                    """INSERT INTO investigation_resource_lists
                       (investigation_slug, working_set_slug, resource_use, added_at)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT (investigation_slug, working_set_slug) DO NOTHING""",
                    (investigation_slug, ws_slug,
                     "folio" if kind == "folio" else f"disposition:{disposition}", now),
                )
        return self.get_working_set(ws_slug)

    # Back-compat: everything that asked for "the investigation's working set"
    # meant "what is in scope", which is now the Folio.
    def get_or_create_working_set(self, investigation_slug: str,
                                  display_name: str = "") -> dict | None:
        return self.get_or_create_folio(investigation_slug)

    def get_working_set(self, ws_slug: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM working_sets WHERE slug = ?", (ws_slug,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["members"] = self.list_working_set_members(ws_slug)
        return d

    def investigation_working_set_slug(self, investigation_slug: str) -> str:
        """The investigation's FOLIO slug — what "its working set" used to mean."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT ws.slug FROM working_sets ws
                   JOIN investigation_resource_lists rl ON rl.working_set_slug = ws.slug
                   WHERE rl.investigation_slug = ? AND ws.collection_kind = 'folio'""",
                (investigation_slug,),
            ).fetchone()
        return row["slug"] if row else ""

    def add_working_set_member(self, ws_slug: str, entity_type: str, entity_slug: str,
                               *, membership_rationale: str = "",
                               state: str = "in-scope") -> list[dict]:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO working_set_members
                   (working_set_slug, entity_type, entity_slug, membership_rationale, state, added_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (working_set_slug, entity_type, entity_slug)
                   DO UPDATE SET membership_rationale = EXCLUDED.membership_rationale,
                                 state = EXCLUDED.state""",
                (ws_slug, entity_type, entity_slug, membership_rationale, state,
                 datetime.utcnow().isoformat()),
            )
        return self.list_working_set_members(ws_slug)

    def remove_working_set_member(self, ws_slug: str, entity_type: str,
                                  entity_slug: str) -> list[dict]:
        with self._conn() as conn:
            conn.execute(
                """DELETE FROM working_set_members
                   WHERE working_set_slug = ? AND entity_type = ? AND entity_slug = ?""",
                (ws_slug, entity_type, entity_slug),
            )
        return self.list_working_set_members(ws_slug)

    def list_working_set_members(self, ws_slug: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT entity_type, entity_slug, membership_rationale, state, added_at
                   FROM working_set_members WHERE working_set_slug = ?
                   ORDER BY entity_type, entity_slug""",
                (ws_slug,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_investigation_members(self, investigation_slug: str) -> list[dict]:
        """What is in scope for this investigation — the Folio's members.

        Deliberately the Folio and not a union across WorkingSets: a resource can
        be in scope without anyone having judged it yet, and that is the normal
        state right after scouting.
        """
        folio = self.investigation_working_set_slug(investigation_slug)
        return self.list_working_set_members(folio) if folio else []

    def find_entity_investigations(self, entity_type: str, entity_slug: str,
                                   *, user_id: str | None = None) -> list[dict]:
        """The reverse of list_investigation_members(): which investigations'
        Folios this entity is in scope for. Folio-only, matching
        list_investigation_members' own reasoning — WorkingSet (per-
        disposition) membership implies Folio membership so this still finds
        it, but a WorkingSet-only match with no Folio row would mean the
        two tables have drifted, not that the entity is legitimately
        "in scope but unjudged" from a different angle.

        Backs the admin repair surface's repoint/drop-membership operation
        (repair.py) — an entity-centric view where nothing existed before;
        investigations.py's own members endpoints are all investigation-
        centric ("what's in this investigation"), not "which investigations
        is this repo in".
        """
        user_id = current_user_id() if user_id is None else user_id
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT i.slug AS investigation_slug, i.display_name, i.status,
                          i.project_classification, i.created_by,
                          wsm.state, wsm.membership_rationale, wsm.added_at
                   FROM working_set_members wsm
                   JOIN working_sets ws ON ws.slug = wsm.working_set_slug
                                        AND ws.collection_kind = 'folio'
                   JOIN investigation_resource_lists rl ON rl.working_set_slug = ws.slug
                   JOIN investigations i ON i.slug = rl.investigation_slug
                   WHERE wsm.entity_type = ? AND wsm.entity_slug = ?
                   ORDER BY i.display_name""",
                (entity_type, entity_slug),
            ).fetchall()
        # Filtered, because this is entity-CENTRIC: it answers "which
        # investigations is this repo in", on a page anyone can open. Without
        # the filter, a shared repo's page would name every private
        # investigation that happens to include it — the classic leak through a
        # back reference rather than through the object itself.
        return [dict(r) for r in rows if self._may_see_investigation(dict(r), user_id)]

    def set_investigation_disposition(self, investigation_slug: str, entity_type: str,
                                      entity_slug: str, disposition: str,
                                      *, rationale: str = "") -> dict:
        """Give a resource a disposition WITHIN this investigation.

        The disposition IS the WorkingSet it belongs to, so this moves it: added
        to the target set and removed from every other, because a WorkingSet
        carries a single disposition and membership of two would assert two
        contradictory judgements at once.

        Passing "" or "undecided" clears the disposition, leaving the resource in
        the Folio — in scope, unjudged.
        """
        folio = self.get_or_create_folio(investigation_slug)
        if not folio:
            return {}
        # Anything with a disposition is in scope by definition.
        self.add_working_set_member(folio["slug"], entity_type, entity_slug,
                                    membership_rationale=rationale)
        with self._conn() as conn:
            existing = conn.execute(
                """SELECT ws.slug, ws.disposition FROM working_sets ws
                   JOIN investigation_resource_lists rl ON rl.working_set_slug = ws.slug
                   WHERE rl.investigation_slug = ? AND ws.collection_kind = 'working_set'""",
                (investigation_slug,),
            ).fetchall()
            for r in existing:
                conn.execute(
                    """DELETE FROM working_set_members
                       WHERE working_set_slug = ? AND entity_type = ? AND entity_slug = ?""",
                    (r["slug"], entity_type, entity_slug),
                )
        if disposition and disposition != "undecided":
            target = self.get_or_create_disposition_set(investigation_slug, disposition)
            self.add_working_set_member(target["slug"], entity_type, entity_slug,
                                        membership_rationale=rationale)
        return self.investigation_dispositions(investigation_slug)

    def investigation_dispositions(self, investigation_slug: str) -> dict:
        """{disposition: [members]} for this investigation — what the left-hand
        filter needs, derived from WorkingSet membership rather than stored twice."""
        out: dict[str, list[dict]] = {}
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT ws.slug, ws.disposition FROM working_sets ws
                   JOIN investigation_resource_lists rl ON rl.working_set_slug = ws.slug
                   WHERE rl.investigation_slug = ? AND ws.collection_kind = 'working_set'
                   ORDER BY ws.disposition""",
                (investigation_slug,),
            ).fetchall()
        for r in rows:
            members = self.list_working_set_members(r["slug"])
            if members:
                out[r["disposition"]] = members
        return out

    def disposition_of(self, investigation_slug: str, entity_type: str,
                       entity_slug: str) -> str:
        for disp, members in self.investigation_dispositions(investigation_slug).items():
            if any(m["entity_type"] == entity_type and m["entity_slug"] == entity_slug
                   for m in members):
                return disp
        return ""

    def update_investigation(self, slug: str, *, display_name: str | None = None,
                             description: str | None = None,
                             purposes: list[str] | None = None,
                             hypothesis: str | None = None) -> dict | None:
        """Rename, re-describe, or refine the hypothesis. The slug never
        changes — members, the Egeria binding and inherited context rows
        reference it.

        Changing the *classification* is deliberately NOT here: it can move an
        investigation between visibility regimes and is a flow with a report,
        not an UPDATE (design §5). Sharpening an Experiment's hypothesis is a
        different thing — it stays within one classification.
        """
        import json as _json
        inv = self.get_investigation(slug)
        if not inv:
            return None
        if purposes is not None:
            bad = [p for p in purposes if p not in self.VALID_PURPOSES]
            if bad:
                raise ValueError(f"unknown purpose(s) {bad}; valid: {list(self.VALID_PURPOSES)}")
        if hypothesis is not None:
            hypothesis = hypothesis.strip()
            cls = inv.get("project_classification")
            if cls not in self.HYPOTHESIS_REQUIRED_FOR:
                raise ValueError(
                    f"hypothesis is only meaningful for "
                    f"{list(self.HYPOTHESIS_REQUIRED_FOR)}, not {cls!r}"
                )
            if not hypothesis:
                raise ValueError(
                    f"{cls} requires a hypothesis — clearing it would leave the "
                    "classification asserting something with its point missing"
                )
        with self._conn() as conn:
            if display_name is not None:
                conn.execute("UPDATE investigations SET display_name = ? WHERE slug = ?",
                             (display_name, slug))
            if description is not None:
                conn.execute("UPDATE investigations SET description = ? WHERE slug = ?",
                             (description, slug))
            if purposes is not None:
                conn.execute("UPDATE investigations SET purposes_json = ? WHERE slug = ?",
                             (_json.dumps(purposes), slug))
            if hypothesis is not None:
                conn.execute("UPDATE investigations SET hypothesis = ? WHERE slug = ?",
                             (hypothesis, slug))
            conn.execute("UPDATE investigations SET updated_at = ? WHERE slug = ?",
                         (datetime.utcnow().isoformat(), slug))
        return self.get_investigation(slug)

    def set_investigation_classification(self, slug: str, classification: str, *,
                                         hypothesis: str = "") -> dict | None:
        """Change an investigation's classification. LOCAL ONLY.

        Deliberately not exposed through `update_investigation`, and deliberately
        not the whole operation. Because visibility follows the classification
        (`PRIVATE_CLASSIFICATIONS`), changing it can move the investigation
        between visibility regimes — which is several Egeria writes that fail
        independently. `surveyors/investigation_reclassifier` is the flow that
        owns that, and it calls this LAST, once Egeria already holds the new
        state.

        Calling this directly changes what RE shows without changing what Egeria
        serves. That is legitimate for a purely local investigation and a bug
        anywhere else, which is why the reclassifier is the intended caller.

        The hypothesis rules match `create_investigation`: required when moving
        TO a classification that needs one, cleared when moving away from it —
        a hypothesis left on a `Task` would be stored and then silently dropped
        at publish time.
        """
        inv = self.get_investigation(slug)
        if not inv:
            return None
        if classification not in self.PROJECT_CLASSIFICATIONS:
            raise ValueError(
                f"unknown classification {classification!r}; "
                f"valid: {list(self.PROJECT_CLASSIFICATIONS)}")
        hypothesis = (hypothesis or "").strip()
        if classification in self.HYPOTHESIS_REQUIRED_FOR:
            hypothesis = hypothesis or (inv.get("hypothesis") or "").strip()
            if not hypothesis:
                raise ValueError(
                    f"{classification} requires a hypothesis — it is the attribute "
                    "the classification exists to record")
        else:
            # Cleared rather than carried: it is meaningless on the new
            # classification and `_initial_classifications` would not send it,
            # so keeping it would be a stored value that silently never reaches
            # Egeria.
            hypothesis = ""
        with self._conn() as conn:
            conn.execute(
                "UPDATE investigations SET project_classification = ?, hypothesis = ?, "
                "updated_at = ? WHERE slug = ?",
                (classification, hypothesis, datetime.utcnow().isoformat(), slug),
            )
        return self.get_investigation(slug)

    def set_investigation_egeria_project(self, slug: str, ctx: dict) -> dict | None:
        """Bind (or unbind) an investigation to an Egeria Project.

        Takes the same context shape the publish path speaks, so nothing
        downstream has to learn a new one.

        **Binding a GUID also moves `egeria_binding` off `local`.** Asking for
        an Egeria Project IS the decision that this is no longer ad-hoc, and
        leaving the flag behind would produce a row that has a Project and
        claims not to want one — a contradiction nothing downstream could
        resolve. The reverse is not symmetric: unbinding does NOT set it back
        to `local`, because losing or clearing a binding is not the same as
        deciding to stay local, and only the second is a choice somebody made.
        """
        guid = ctx.get("egeria_project_guid", "")
        with self._conn() as conn:
            conn.execute(
                """UPDATE investigations
                   SET egeria_project_status = ?, egeria_project_guid = ?,
                       egeria_project_qualified_name = ?, egeria_free_text_name = ?,
                       updated_at = ?
                   WHERE slug = ?""",
                (ctx.get("status", "unset"), guid,
                 ctx.get("egeria_project_qualified_name", ""),
                 ctx.get("free_text_name", ""),
                 datetime.utcnow().isoformat(), slug),
            )
            if guid:
                conn.execute(
                    "UPDATE investigations SET egeria_binding = ? WHERE slug = ?",
                    (self.BINDING_EGERIA, slug),
                )
        return self.get_investigation(slug)

    def inherited_egeria_project_context(self, entity_type: str, entity_slug: str,
                                         *, user_id: str | None = None) -> dict | None:
        """The Egeria Project binding a resource inherits from its investigation.

        Scoped to the FOLIO — being in scope for a bound investigation is what
        supplies the binding. A disposition WorkingSet's members are all in the
        Folio too, so this covers them without double-counting.

        **Also scoped to the caller**, and this one is easy to miss: the
        returned shape carries `_inherited_from_name` (the investigation's
        display name) and the Project's qualifiedName, and it is read on a
        RESOURCE's page — which anyone can open. A repo sitting in someone's
        private investigation would otherwise announce that investigation by
        name to every other user. It also stops one user publishing a shared
        repo into another user's private Project.

        The worker is unaffected: it runs as the shared identity, which sees
        everything, so queued surveys still inherit and publish normally.
        """
        user_id = current_user_id() if user_id is None else user_id
        with self._conn() as conn:
            row = conn.execute(
                """SELECT i.slug, i.display_name, i.egeria_project_guid,
                          i.egeria_project_qualified_name,
                          i.project_classification, i.created_by
                   FROM working_set_members m
                   JOIN working_sets ws ON ws.slug = m.working_set_slug
                   JOIN investigation_resource_lists rl
                     ON rl.working_set_slug = m.working_set_slug
                   JOIN investigations i ON i.slug = rl.investigation_slug
                   WHERE m.entity_type = ? AND m.entity_slug = ?
                     AND m.state <> 'excluded'
                     AND ws.collection_kind = 'folio'
                     AND i.status <> 'closed'
                     AND i.egeria_project_status = 'linked'
                     AND i.egeria_project_guid <> ''
                   ORDER BY i.updated_at DESC""",
                (entity_type, entity_slug),
            ).fetchall()
        visible = [dict(r) for r in row
                   if self._may_see_investigation(dict(r), user_id)]
        if not visible:
            return None
        first = visible[0]
        return {
            "status": "linked",
            "egeria_project_guid": first["egeria_project_guid"],
            "egeria_project_qualified_name": first["egeria_project_qualified_name"] or "",
            "free_text_name": "",
            "_inherited_from": first["slug"],
            "_inherited_from_name": first["display_name"],
            # Counts only what this caller can SEE. An investigation they may
            # not see cannot make their view ambiguous, and saying "ambiguous"
            # while showing one candidate would be an unanswerable prompt.
            "_ambiguous": len(visible) > 1,
        }

    def private_owner_for_entity(self, entity_type: str, entity_slug: str) -> str:
        """The userId an entity's artifacts must be zoned to, or `""` for none.

        A resource's artifacts are private when the resource is in scope for a
        privately-classified investigation (`PRIVATE_CLASSIFICATIONS`) that has
        a recorded owner. The answer is that owner's userId.

        **Deliberately UNSCOPED — it must see investigations the caller cannot.**
        This is the exact inverse of `_may_see_investigation`, and getting it
        backwards is the whole bug:

        * Phase 3's filter hides a private investigation FROM other callers.
        * This decides whether to PROTECT an artifact, so it must see the
          private investigation whoever is asking.

        The worker publishes as the service account (`egeria_identity`'s module
        docstring). If this used the caller-scoped read, a queued survey would
        find no private investigation, conclude "not private", and publish the
        artifact into the public zones — a silent leak produced by the privacy
        machinery itself. So it reads the rows directly rather than going
        through `get_investigation`, and that is not an oversight.

        Closed investigations still count. Closing one means the work finished,
        not that its artifacts became public.

        Where a resource is in several private investigations, the oldest
        owner wins — an arbitrary but STABLE choice, so an artifact does not
        change zone because an unrelated investigation was created. Multiple
        owners cannot be expressed in one `Ownership`, and picking the newest
        would let anyone re-home someone else's artifacts by adding the repo to
        their own investigation.
        """
        # NOT normalised. `working_set_members` stores the slug verbatim, as
        # `find_entity_investigations` and `inherited_egeria_project_context`
        # both assume — and `_normalize_slug` turns hyphens into underscores, so
        # normalising here matched nothing for any repo with a hyphen in its
        # name, which is most of them. That failure was silent and it failed
        # OPEN: no owner found means "not private", so the artifacts would have
        # published into the public zones. Caught by a test, not by review.
        placeholders = ",".join("?" * len(self.PRIVATE_CLASSIFICATIONS))
        with self._conn() as conn:
            row = conn.execute(
                f"""SELECT i.created_by, i.slug
                    FROM working_set_members m
                    JOIN working_sets ws ON ws.slug = m.working_set_slug
                                         AND ws.collection_kind = 'folio'
                    JOIN investigation_resource_lists rl
                      ON rl.working_set_slug = m.working_set_slug
                    JOIN investigations i ON i.slug = rl.investigation_slug
                    WHERE m.entity_type = ? AND m.entity_slug = ?
                      AND m.state <> 'excluded'
                      AND i.project_classification IN ({placeholders})
                      AND i.created_by IS NOT NULL AND i.created_by <> ''
                    ORDER BY i.created_at ASC""",
                (entity_type, entity_slug, *self.PRIVATE_CLASSIFICATIONS),
            ).fetchone()
        return (row["created_by"] if row else "") or ""

    def set_working_set_egeria_collection(self, ws_slug: str, guid: str,
                                         qualified_name: str = "") -> dict | None:
        """Record the Egeria Collection this working set became.

        Without this the GUID returned by create_collection is discarded the
        moment it is used, so a second promotion cannot tell that a Collection
        already exists and would create another — orphaning the first.
        """
        with self._conn() as conn:
            conn.execute(
                """UPDATE working_sets
                   SET egeria_collection_guid = ?, egeria_collection_qualified_name = ?
                   WHERE slug = ?""",
                (guid, qualified_name, ws_slug),
            )
        return self.get_working_set(ws_slug)

    #: An investigation's lifecycle. `closed` used to be the only non-open
    #: value, and it meant three different things at once — finished, paused,
    #: and abandoned — while the only behaviour attached to it was the one that
    #: belongs to "finished". Naming them apart is what lets the UI say what a
    #: button will do.
    #:
    #:   open      — active work.
    #:   suspended — paused, will resume. Keeps its Egeria Project inheritance:
    #:               pausing the work does not unbind the resources from the
    #:               project they belong to.
    #:   closed    — finished. Stops inheriting, because the work is over.
    #:
    #: Nothing is ever deleted by any of them.
    INVESTIGATION_STATUSES = ("open", "suspended", "closed")

    def set_investigation_status(self, slug: str, status: str) -> dict | None:
        """Move an investigation through its lifecycle. Reversible, always."""
        if status not in self.INVESTIGATION_STATUSES:
            raise ValueError(
                f"unknown status {status!r}; valid: {list(self.INVESTIGATION_STATUSES)}"
            )
        if not self.get_investigation(slug):
            return None
        with self._conn() as conn:
            conn.execute(
                "UPDATE investigations SET status = ?, updated_at = ? WHERE slug = ?",
                (status, datetime.utcnow().isoformat(), slug),
            )
        return self.get_investigation(slug)

    def close_investigation(self, slug: str) -> dict | None:
        """Kept as the name the close route and its tests already use."""
        return self.set_investigation_status(slug, "closed")

    # Egeria Project context (Part 5) — see entity_egeria_project_context's
    # own docstring above for the status vocabulary. get_project_context()
    # returns None (not a default-shaped dict) when nothing has ever been
    # decided, so callers can distinguish "no row yet" from a row whose
    # status happens to be "unset" — both should be treated the same by the
    # publish gate (web/routes/egeria.py), but the distinction matters for
    # anything inspecting the row directly later.

    def set_project_context(
        self,
        entity_type: str,
        entity_slug: str,
        status: str,
        *,
        egeria_project_guid: str = "",
        egeria_project_qualified_name: str = "",
        free_text_name: str = "",
        user_id: str | None = None,
    ) -> dict:
        """Record the Egeria Project this caller is working under (plan §4).

        Scoped to the caller: two people can survey one repo under two
        different Egeria Projects, and before this column that was a
        last-write-wins collision nobody could see.
        """
        entity_slug = self._normalize_slug(entity_slug)
        user_id = current_user_id() if user_id is None else user_id
        decided_at = datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO entity_egeria_project_context
                       (entity_type, entity_slug, user_id, status, egeria_project_guid,
                        egeria_project_qualified_name, free_text_name, decided_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(entity_type, entity_slug, user_id) DO UPDATE SET
                       status = excluded.status,
                       egeria_project_guid = excluded.egeria_project_guid,
                       egeria_project_qualified_name = excluded.egeria_project_qualified_name,
                       free_text_name = excluded.free_text_name,
                       decided_at = excluded.decided_at""",
                (
                    entity_type, entity_slug, user_id, status, egeria_project_guid,
                    egeria_project_qualified_name, free_text_name, decided_at,
                ),
            )
        return self.get_project_context(entity_type, entity_slug, user_id=user_id)

    def get_project_context(
        self, entity_type: str, entity_slug: str, *, user_id: str | None = None,
    ) -> dict | None:
        """This caller's context row, falling back to the shared/legacy one.

        Still returns None (not a default-shaped dict) when nothing has ever
        been decided by anyone, so callers can keep distinguishing "no row yet"
        from a row whose status happens to be `unset` — see this table's own
        comment in `_init_schema`.
        """
        entity_slug = self._normalize_slug(entity_slug)
        user_id = current_user_id() if user_id is None else user_id
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM entity_egeria_project_context "
                "WHERE entity_type = ? AND entity_slug = ? AND user_id IN (?, ?)",
                (entity_type, entity_slug, user_id, SHARED_USER_ID),
            ).fetchall()
        if not rows:
            return None
        by_user = {r["user_id"]: r for r in rows}
        row = by_user.get(user_id) or by_user.get(SHARED_USER_ID)
        return dict(row) if row is not None else None

    def has_assigned_egeria_project(self, entity_type: str, entity_slug: str) -> bool:
        """The single, correct gate for "should this resource's survey results be
        automatically cataloged into Egeria" — used by both publish paths
        (survey_definition_executor.py's auto-publish and the Assessment/Analysis
        auto-publish added alongside it) rather than each re-deriving its own.

        Deliberately tighter than egeria.py's publish route's own inline check
        (`status != "unset"`), which also lets personal/deferred/declined pass —
        those are equally deliberate "not linked to Egeria" answers, and treating
        them as green-lighting an automatic Egeria write would be wrong. Only a
        real `linked` status with a `egeria_project_guid` counts here.

        Falls back to `inherited_egeria_project_context()` and WRITES it when
        found, same as the publish route already does inline — an inheritance
        that stayed read-only would make a resource's context depend on
        investigation membership at read time, so leaving the investigation's
        working set later would silently un-answer a question already settled.
        """
        entity_slug = self._normalize_slug(entity_slug)
        context = self.get_project_context(entity_type, entity_slug)
        if not context or context.get("status") == "unset":
            inherited = self.inherited_egeria_project_context(entity_type, entity_slug)
            if not inherited:
                return False
            self.set_project_context(
                entity_type, entity_slug,
                status="linked",
                egeria_project_guid=inherited["egeria_project_guid"],
                egeria_project_qualified_name=inherited["egeria_project_qualified_name"],
                free_text_name=f"inherited from investigation '{inherited['_inherited_from_name']}'",
            )
            context = self.get_project_context(entity_type, entity_slug)
        return bool(context) and context.get("status") == "linked" and bool(context.get("egeria_project_guid"))

    # Automate (Part 4) — notification_subscriptions. See that table's own
    # docstring above for the local-first rationale. No hard delete —
    # deactivate/activate, matching the disposition/working-set convention
    # of never permanently destroying state elsewhere in this codebase.

    def create_subscription(self, entity_type: str, entity_slug: str, analysis_id: str, label: str = "") -> dict:
        entity_slug = self._normalize_slug(entity_slug)
        created_at = datetime.utcnow().isoformat()
        with self._conn() as conn:
            row = conn.execute(
                """INSERT INTO notification_subscriptions
                       (entity_type, entity_slug, analysis_id, label, active, created_at)
                   VALUES (?, ?, ?, ?, 1, ?) RETURNING id""",
                (entity_type, entity_slug, analysis_id, label, created_at),
            ).fetchone()
            sub_id = row["id"]
        return self.get_subscription(sub_id)

    def get_subscription(self, subscription_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM notification_subscriptions WHERE id = ?", (subscription_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_subscriptions(
        self, entity_type: str | None = None, entity_slug: str | None = None,
        analysis_id: str | None = None, active_only: bool = False,
    ) -> list[dict]:
        clauses, params = [], []
        if entity_type is not None:
            clauses.append("entity_type = ?"); params.append(entity_type)
        if entity_slug is not None:
            clauses.append("entity_slug = ?"); params.append(self._normalize_slug(entity_slug))
        if analysis_id is not None:
            clauses.append("analysis_id = ?"); params.append(analysis_id)
        if active_only:
            clauses.append("active = 1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM notification_subscriptions {where} ORDER BY created_at DESC", params
            ).fetchall()
        return [dict(r) for r in rows]

    def set_subscription_active(self, subscription_id: int, active: bool) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE notification_subscriptions SET active = ? WHERE id = ?",
                (int(active), subscription_id),
            )

    def record_subscription_checked(self, subscription_id: int, checked_at: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE notification_subscriptions SET last_checked_at = ? WHERE id = ?",
                (checked_at, subscription_id),
            )

    def record_subscription_notified(self, subscription_id: int, notified_at: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE notification_subscriptions SET last_notified_at = ?, "
                "notification_count = notification_count + 1 WHERE id = ?",
                (notified_at, subscription_id),
            )

    def exists(self, slug: str) -> bool:
        return self.get(slug) is not None

    def _row_to_project(self, row: sqlite3.Row) -> Project:
        import dataclasses
        d = dict(row)
        d["collections"] = json.loads(d.get("collections") or "[]")
        d["extra_docs_paths"] = json.loads(d.get("extra_docs_paths") or "[]")
        d["status"] = ProjectStatus(d["status"])
        # Filter to only known Project fields to stay forward-compatible with schema changes
        known = {f.name for f in dataclasses.fields(Project)}
        return Project(**{k: v for k, v in d.items() if k in known})


    # ── database entity management ────────────────────────────────────────────

    def register_database(self, database: DatabaseEntity) -> None:
        """Register a database entity in the registry.

        `db_password` is encrypted at rest (`credential_crypto.
        encrypt_db_password`) before it ever reaches the `databases` table —
        see that module's docstring and design REPLY-DATABASE-CREDENTIAL-
        CAPABILITY-VISIBILITY.md §7. Callers (web/routes/databases.py,
        cli/main.py) always pass the plaintext password they received from
        the operator; encryption is the registry's job, not theirs.
        """
        from resource_explorer.credential_crypto import encrypt_db_password

        data = asdict(database)
        data["slug"] = self._normalize_slug(data["slug"])
        data["db_password"] = encrypt_db_password(data.get("db_password") or "")
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO databases (
                    slug, display_name, db_type, host, port, database_name,
                    description, connection_ref, status, registered_at,
                    last_surveyed_at, egeria_asset_guid, error_message,
                    db_user, db_password, egeria_host,
                    egeria_url, egeria_server, egeria_user, egeria_password,
                    server_slug, governance_state, group_slug
                ) VALUES (
                    :slug, :display_name, :db_type, :host, :port, :database_name,
                    :description, :connection_ref, :status, :registered_at,
                    :last_surveyed_at, :egeria_asset_guid, :error_message,
                    :db_user, :db_password, :egeria_host,
                    :egeria_url, :egeria_server, :egeria_user, :egeria_password,
                    :server_slug, :governance_state, :group_slug
                )""",
                data,
            )

    def get_database(self, slug: str) -> DatabaseEntity | None:
        """Retrieve a database entity by slug."""
        normalized = self._normalize_slug(slug)
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM databases WHERE slug = ?", (normalized,)).fetchone()
        return self._row_to_database(row) if row else None

    def list_databases(self, db_type: str | None = None, server_slug: str | None = None, governance_state: str | None = None) -> list[DatabaseEntity]:
        """List all registered databases, optionally filtered by type, server slug, or governance state."""
        with self._conn() as conn:
            query = "SELECT * FROM databases WHERE 1=1"
            params = []
            if server_slug:
                query += " AND server_slug = ?"
                params.append(self._normalize_slug(server_slug))
            if db_type:
                query += " AND db_type = ?"
                params.append(db_type)
            if governance_state:
                query += " AND governance_state = ?"
                params.append(governance_state)
            query += " ORDER BY display_name"
            
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_database(r) for r in rows]

    def update_database_status(self, slug: str, status: ProjectStatus, error: str = "") -> None:
        """Update the status of a database entity."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            conn.execute(
                "UPDATE databases SET status = ?, error_message = ? WHERE slug = ?",
                (status.value, error, slug),
            )

    def update_database_credentials(self, slug: str, db_user: str, db_password: str) -> None:
        """Update the stored connection credentials for a database entity.

        Repoints an already-registered database at a different DB role/password
        without disturbing its registration history (slug, egeria_asset_guid,
        survey history, etc.) — the only supported way to change credentials;
        there is deliberately no broader multi-connection/credential-store model
        here (see docs/Backlog.md's "Database credential-capability model —
        awaiting the project owner's ruling").

        `db_password` is encrypted at rest (`credential_crypto.
        encrypt_db_password`), same as `register_database` — this is the
        registry-side half of the write; projecting the same credential into
        the `.omsecrets` file (design REPLY-DATABASE-CREDENTIAL-CAPABILITY-
        VISIBILITY.md §7) is the caller's job (web/routes/databases.py,
        cli/main.py) via `omsecrets_store.write_credential`, since this
        method only knows about the registry, not the deployment's
        `.omsecrets` path.
        """
        from resource_explorer.credential_crypto import encrypt_db_password

        slug = self._normalize_slug(slug)
        encrypted = encrypt_db_password(db_password or "")
        with self._conn() as conn:
            conn.execute(
                "UPDATE databases SET db_user = ?, db_password = ? WHERE slug = ?",
                (db_user, encrypted, slug),
            )

    def check_credential_drift(self, slug: str) -> dict:
        """Compare RE's registry against the `.omsecrets` file for one
        database's credential collection — design REPLY-DATABASE-CREDENTIAL-
        CAPABILITY-VISIBILITY.md §7's closing line: "Drift between the two
        is detectable by comparing collection names present on each side."

        This is deliberately a narrow point check, not a reconciliation
        UI: it answers "does this one database's collection name show up on
        both sides", not "repair the mismatch" or "scan every database" (a
        caller wanting the latter loops this over `list_databases()`). It
        also does not (yet) compare the actual `userId`/`clearPassword`
        values on each side, only whether both sides know about the same
        named collection at all — value-level drift (same collection name,
        different password on each side, e.g. one side rotated without the
        other) is a real gap left for later, noted rather than silently
        assumed away.

        Returns a dict rather than a dataclass so CLI/web callers can
        `json.dumps` it directly:
            {
                "slug": ..., "collection_name": ...,
                "in_registry": bool,   # RE has a non-empty db_password for this slug
                "in_omsecrets": bool,  # the collection name is a key in the .omsecrets file
                "omsecrets_configured": bool,  # False when no local path is set at all —
                                                # distinguishes "checked, and it's missing"
                                                # from "couldn't check" (find-absence-as-answer)
                "in_sync": bool,       # in_registry == in_omsecrets, only meaningful
                                       # when omsecrets_configured is True
            }
        """
        from resource_explorer.omsecrets_store import (
            collection_names,
            local_path,
            secrets_collection_name,
        )

        slug = self._normalize_slug(slug)
        database = self.get_database(slug)
        in_registry = bool(database and database.db_password)
        collection = secrets_collection_name(slug)
        configured = bool(local_path())
        present = collection_names()
        in_omsecrets = collection in present
        return {
            "slug": slug,
            "collection_name": collection,
            "in_registry": in_registry,
            "in_omsecrets": in_omsecrets,
            "omsecrets_configured": configured,
            "in_sync": (in_registry == in_omsecrets) if configured else None,
        }

    def update_database_surveyed_at(self, slug: str) -> None:
        """Update the last_surveyed_at timestamp for a database."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            conn.execute(
                "UPDATE databases SET last_surveyed_at = ? WHERE slug = ?",
                (datetime.utcnow().isoformat(), slug),
            )

    def set_database_egeria_guid(self, slug: str, guid: str) -> None:
        """Set the Egeria asset GUID for a database."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            conn.execute(
                "UPDATE databases SET egeria_asset_guid = ? WHERE slug = ?",
                (guid, slug),
            )

    def remove_database(self, slug: str) -> None:
        """Remove a database entity and all its survey records."""
        normalized = self._normalize_slug(slug)
        with self._conn() as conn:
            # Child before parent — database_surveys has a real FK to
            # databases.slug; SQLite silently allows the reverse order
            # (foreign_keys pragma off by default), Postgres does not.
            conn.execute("DELETE FROM database_surveys WHERE database_slug = ?", (normalized,))
            # The structured detail tables are children too, and every one of
            # them has a real FK. Missing them here does not strand rows — it
            # makes the parent delete fail outright, on Postgres and on
            # SQLite alike (this connection sets foreign_keys=ON). Driven off
            # the spec map so a table added later is covered without a second
            # edit here.
            for _table, _spec in _DETAIL_TABLE_SPECS.items():
                if _spec.resource_type == "database":
                    conn.execute(
                        f"DELETE FROM {_table} WHERE {_spec.slug_column} = ?",
                        (normalized,),
                    )
            conn.execute(
                "DELETE FROM database_survey_coverage WHERE database_slug = ?",
                (normalized,),
            )
            conn.execute("DELETE FROM databases WHERE slug = ?", (normalized,))

    def record_database_survey(
        self,
        slug: str,
        schema_count: int,
        table_count: int,
        column_count: int,
        survey_data: dict,
        egeria_report_guid: str = "",
        source: str = "local",
        surveyed_at: str | None = None,
        surveyed_as: str = "",
    ) -> None:
        """Record a database survey result.

        `surveyed_at` defaults to "now" (unchanged behaviour) but a caller
        that already stamped its own results dict with a `surveyed_at` MUST
        pass it here — this method's internal `backfill_database_survey`
        call writes `database_table_activity` / `database_column_profiles`
        placeholder rows from the SAME blob, keyed `(slug, surveyed_at,
        source)`. A caller writing its own, richer detail rows for those same
        tables after calling this method needs them keyed under the exact
        same `surveyed_at`, or the two writes land under different keys and
        `query_detail_rows`'s "latest wins" lookup can pick the older,
        thinner one. Found live in Phase 1 slice 7's own tests: with two
        independently-generated timestamps, `postgres_schema_and_stats`'s
        real tuple counters were silently shadowed by this method's
        `last_vacuum`/`last_analyze`-only backfill row, because the backfill
        call's freshly-generated timestamp happened to sort later.
        """
        slug = self._normalize_slug(slug)
        surveyed_at = surveyed_at or datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO database_surveys
                   (database_slug, surveyed_at, egeria_report_guid, schema_count,
                    table_count, column_count, survey_data, source, surveyed_as)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (slug, surveyed_at, egeria_report_guid, schema_count,
                 table_count, column_count, json.dumps(survey_data), source,
                 surveyed_as or ""),
            )
            # Keep the databases row in sync so API responses reflect current counts
            conn.execute(
                """UPDATE databases
                   SET schema_count=?, table_count=?, column_count=?,
                       last_surveyed_at=?
                   WHERE slug=?""",
                (schema_count, table_count, column_count, surveyed_at, slug),
            )
        # Materialise the structured detail rows from the same blob, so a
        # survey run today is queryable without waiting for a back-fill
        # (design §5.7). Done here rather than in each surveyor because every
        # database survey path — local, hybrid and the Egeria-adaptive
        # handler — already funnels through this one method, so one seam
        # covers all three and no surveyor has to remember.
        #
        # Deliberately not fatal: a conversion bug must not cost the survey
        # result that was just recorded above. The blob is still the raw
        # record, so a failure here is recoverable by re-running the back-fill
        # script — but it is logged loudly rather than swallowed, because a
        # silently empty detail table is exactly the absence this design is
        # trying to stop rendering as "nothing found".
        try:
            from resource_explorer.surveyors.result_materializer import (
                backfill_database_survey,
            )
            backfill_database_survey(self, slug, surveyed_at, survey_data, source=source)
        except Exception as exc:
            log.warning(
                "Structured detail rows not written for database %s @ %s (%s): %s. "
                "The survey_data blob was stored; re-run "
                "scripts/backfill_structured_tables.py to recover the rows.",
                slug, surveyed_at, source, exc,
            )

    def get_database_surveys(self, slug: str) -> list[dict]:
        """Return all survey records for a database, newest first."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT database_slug, surveyed_at, egeria_report_guid,
                          schema_count, table_count, column_count, survey_data, source,
                          surveyed_as
                   FROM database_surveys
                   WHERE database_slug = ?
                   ORDER BY surveyed_at DESC""",
                (slug,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_latest_database_survey(self, slug: str) -> dict | None:
        """Return the most recent survey record for a database, or None."""
        surveys = self.get_database_surveys(slug)
        return surveys[0] if surveys else None

    # ── structured DB/FS detail rows (design §5.7, §6) ────────────────────────
    #
    # One generic writer and one generic reader, rather than ten near-identical
    # pairs. The tables differ only in their columns and their slug column, and
    # both of those are derived from the DDL above, so adding a column to a
    # CREATE TABLE is enough — there is no second list to keep in step. That
    # matters here because these ten tables were written at once and would
    # otherwise drift silently the first time one of them gained a column.

    def write_detail_rows(
        self,
        table: str,
        slug: str,
        surveyed_at: str,
        source: str = SOURCE_LOCAL,
        rows: list[dict] | None = None,
        *,
        state: str = STATE_MEASURED,
        coverage_section: str | None = None,
        coverage_state: str | None = None,
        coverage_detail: str = "",
    ) -> int:
        """Replace this (slug, surveyed_at, source)'s rows in `table`.

        Returns the number of rows written. Replace rather than append, so
        re-materialising the same survey report is idempotent — a native
        survey that is read back twice must not double its rows.

        `coverage_section`, when given, also records a coverage
        row. Pass it for every section a survey *attempted*, including the ones
        that came back empty: an empty section with no coverage row is
        indistinguishable from a section the survey never looked at, which is
        exactly the collapse `STATE_*` exists to prevent. When
        `coverage_state` is omitted it is inferred as STATE_MEASURED for a
        non-empty result and STATE_EMPTY for an empty one — an inference that
        is only correct because the caller has asserted, by passing
        `coverage_section` at all, that the attempt was made.
        """
        if table not in _DETAIL_TABLE_SPECS:
            raise ValueError(
                f"{table!r} is not a structured detail table; known: "
                f"{sorted(_DETAIL_TABLE_SPECS)}"
            )
        spec = _DETAIL_TABLE_SPECS[table]
        slug = self._normalize_slug(slug)
        rows = rows or []

        with self._conn() as conn:
            conn.execute(
                f"DELETE FROM {table} WHERE {spec.slug_column} = ? "
                f"AND surveyed_at = ? AND source = ?",
                (slug, surveyed_at, source),
            )
            if rows:
                payload = []
                for row in rows:
                    values = [slug, surveyed_at, source]
                    for col in spec.value_columns:
                        if col == "state":
                            values.append(row.get("state", state))
                        else:
                            values.append(_detail_value(row.get(col)))
                    payload.append(tuple(values))
                placeholders = ", ".join("?" for _ in spec.insert_columns)
                conn.executemany(
                    f"INSERT INTO {table} ({', '.join(spec.insert_columns)}) "
                    f"VALUES ({placeholders})",
                    payload,
                )

        if coverage_section is not None:
            if coverage_state is None:
                coverage_state = STATE_MEASURED if rows else STATE_EMPTY
            self.record_section_coverage(
                resource_type=spec.resource_type,
                slug=slug,
                surveyed_at=surveyed_at,
                source=source,
                section=coverage_section,
                state=coverage_state,
                row_count=len(rows),
                detail=coverage_detail,
            )
        return len(rows)

    def query_detail_rows(
        self,
        table: str,
        slug: str,
        surveyed_at: str | None = None,
        source: str | None = None,
    ) -> list[dict]:
        """Rows from `table` for a slug.

        With no `surveyed_at`, returns the most recent run's rows — and
        `source` narrows *which* run that is, so a caller asking for the
        latest native run does not get a later local one. With neither, the
        latest run of any source wins, which is the "what do we currently
        know" question the UI asks.
        """
        if table not in _DETAIL_TABLE_SPECS:
            raise ValueError(f"{table!r} is not a structured detail table")
        spec = _DETAIL_TABLE_SPECS[table]
        slug = self._normalize_slug(slug)

        where = [f"{spec.slug_column} = ?"]
        params: list = [slug]
        if source is not None:
            where.append("source = ?")
            params.append(source)
        if surveyed_at is None:
            with self._conn() as conn:
                row = conn.execute(
                    f"SELECT MAX(surveyed_at) AS latest FROM {table} "
                    f"WHERE {' AND '.join(where)}",
                    tuple(params),
                ).fetchone()
            surveyed_at = (dict(row).get("latest") if row else None) or None
            if surveyed_at is None:
                return []
        where.append("surveyed_at = ?")
        params.append(surveyed_at)

        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE {' AND '.join(where)} "
                f"ORDER BY {spec.order_by}",
                tuple(params),
            ).fetchall()
        return [_decode_detail_row(dict(r)) for r in rows]

    def record_section_coverage(
        self,
        resource_type: str,
        slug: str,
        surveyed_at: str,
        section: str,
        state: str,
        source: str = SOURCE_LOCAL,
        row_count: int | None = None,
        detail: str = "",
    ) -> None:
        """Record that a survey run did (or could not) measure one section.

        This is the row that lets a consumer tell "no grants exist" from "we
        never read pg_roles" from "the survey user could not". Without it the
        only evidence is an empty table, which says all three at once.
        """
        table, slug_column = self._coverage_table(resource_type)
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            conn.execute(
                f"""INSERT INTO {table}
                   ({slug_column}, surveyed_at, source, section,
                    state, row_count, detail)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT({slug_column}, surveyed_at, source, section)
                   DO UPDATE SET state=excluded.state,
                                 row_count=excluded.row_count,
                                 detail=excluded.detail""",
                (slug, surveyed_at, source, section, state, row_count, detail),
            )

    @staticmethod
    def _coverage_table(resource_type: str) -> tuple[str, str]:
        try:
            return _COVERAGE_TABLES[resource_type]
        except KeyError:
            raise ValueError(
                f"no coverage table for resource type {resource_type!r}; "
                f"known: {sorted(_COVERAGE_TABLES)}"
            ) from None

    def get_section_coverage(
        self,
        resource_type: str,
        slug: str,
        surveyed_at: str | None = None,
        source: str | None = None,
    ) -> dict[str, dict]:
        """Coverage rows for a run, keyed by section name.

        A section absent from this mapping was never recorded at all, which is
        weaker than STATE_NOT_MEASURED: it means nothing claimed to have
        tried. Callers should render that as unknown, not as none.
        """
        table, slug_column = self._coverage_table(resource_type)
        slug = self._normalize_slug(slug)
        where = [f"{slug_column} = ?"]
        params: list = [slug]
        if source is not None:
            where.append("source = ?")
            params.append(source)
        if surveyed_at is None:
            with self._conn() as conn:
                row = conn.execute(
                    f"SELECT MAX(surveyed_at) AS latest FROM {table} "
                    f"WHERE {' AND '.join(where)}",
                    tuple(params),
                ).fetchone()
            surveyed_at = (dict(row).get("latest") if row else None) or None
            if surveyed_at is None:
                return {}
        where.append("surveyed_at = ?")
        params.append(surveyed_at)
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE {' AND '.join(where)}",
                tuple(params),
            ).fetchall()
        return {dict(r)["section"]: dict(r) for r in rows}

    def get_detail_run_timestamps(
        self, table: str, slug: str, source: str | None = None
    ) -> list[str]:
        """Distinct `surveyed_at` values present in `table`, newest first.

        The seam the diff and trend consumers need: "which runs do I have
        structured rows for", which is not the same as which rows
        `database_surveys` has, since a run that predates these tables has a
        blob and no rows until the back-fill runs.
        """
        if table not in _DETAIL_TABLE_SPECS:
            raise ValueError(f"{table!r} is not a structured detail table")
        spec = _DETAIL_TABLE_SPECS[table]
        slug = self._normalize_slug(slug)
        where = [f"{spec.slug_column} = ?"]
        params: list = [slug]
        if source is not None:
            where.append("source = ?")
            params.append(source)
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT surveyed_at FROM {table} "
                f"WHERE {' AND '.join(where)} ORDER BY surveyed_at DESC",
                tuple(params),
            ).fetchall()
        return [dict(r)["surveyed_at"] for r in rows]

    def database_exists(self, slug: str) -> bool:
        """Check if a database entity exists."""
        return self.get_database(slug) is not None

    def _row_to_database(self, row: sqlite3.Row) -> DatabaseEntity:
        """Convert a database row to a DatabaseEntity dataclass.

        Decrypts `db_password` (`credential_crypto.decrypt_db_password`) so
        every caller of `get_database`/`list_databases`/etc. sees plaintext,
        exactly as before encryption was added — the ciphertext never
        escapes the registry layer.

        Lazy migration: a row written before encryption shipped stores
        `db_password` as clear text, which `decrypt_db_password` detects by
        format (no `enc:v1:` prefix) and returns unchanged. When that
        happens here, the row is re-encrypted and written back immediately,
        so the migration is "on next read" rather than a separate startup
        pass — chosen over a `_init_schema()` ALTER-TABLE-style migration
        (the `surveyed_as` pattern, #253) because encrypting a column's
        existing values needs the encryption key and the plaintext at the
        same time, and by `_init_schema()` time neither this call path's
        `db_password` values nor a guarantee the key is configured yet are
        available in the same way a simple `ADD COLUMN` is. A row that is
        never read again (e.g. an abandoned/never-surveyed database) stays
        clear-text until it is; this only matters for rows still in active
        use, which are exactly the ones a lazy migration reaches.
        """
        import dataclasses

        from resource_explorer.credential_crypto import (
            decrypt_db_password,
            is_encrypted,
        )

        d = dict(row)
        d["status"] = ProjectStatus(d["status"])
        raw_password = d.get("db_password") or ""
        if raw_password and not is_encrypted(raw_password):
            plaintext = raw_password
            try:
                slug = d.get("slug")
                if slug:
                    self.update_database_credentials(slug, d.get("db_user") or "", plaintext)
            except (sqlite3.Error, OSError) as exc:
                # Narrowed to the write-back failing (DB error, e.g. a
                # locked file or a Postgres connection hiccup) — not a bare
                # `except Exception`, so a bug in `encrypt_db_password`
                # itself (a real, unrelated defect) still propagates instead
                # of being swallowed here. The read must still succeed even
                # when the migration write-back fails: the row simply stays
                # clear-text and is retried on the next read.
                log.warning(
                    "registry: failed to lazily migrate db_password to "
                    "encrypted storage for database slug=%r: %s", d.get("slug"), exc
                )
            d["db_password"] = plaintext
        else:
            d["db_password"] = decrypt_db_password(raw_password)
        # Filter to only known DatabaseEntity fields
        known = {f.name for f in dataclasses.fields(DatabaseEntity)}
        return DatabaseEntity(**{k: v for k, v in d.items() if k in known})

    # ── database server management ────────────────────────────────────────────

    def register_server(self, server: DatabaseServer) -> None:
        """Register a database server in the registry."""
        data = asdict(server)
        data["slug"] = self._normalize_slug(data["slug"])
        data["status"] = data["status"] if isinstance(data["status"], str) else data["status"]
        with self._conn() as conn:
            conn.execute("""INSERT INTO db_servers (
                slug, display_name, db_type, host, port, description,
                db_user, db_password, egeria_host,
                egeria_url, egeria_server, egeria_user, egeria_password,
                status, registered_at, error_message, group_slug
            ) VALUES (
                :slug, :display_name, :db_type, :host, :port, :description,
                :db_user, :db_password, :egeria_host,
                :egeria_url, :egeria_server, :egeria_user, :egeria_password,
                :status, :registered_at, :error_message, :group_slug
            )""", data)

    def get_server(self, slug: str) -> DatabaseServer | None:
        """Retrieve a database server by slug."""
        normalized = self._normalize_slug(slug)
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM db_servers WHERE slug = ?", (normalized,)).fetchone()
        return self._row_to_server(row) if row else None

    def list_servers(self) -> list[DatabaseServer]:
        """List all registered database servers."""
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM db_servers ORDER BY display_name").fetchall()
        return [self._row_to_server(r) for r in rows]

    def remove_server(self, slug: str) -> None:
        """Remove a database server and all its linked databases."""
        normalized = self._normalize_slug(slug)
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM database_surveys WHERE database_slug IN "
                "(SELECT slug FROM databases WHERE server_slug = ?)",
                (normalized,),
            )
            conn.execute("DELETE FROM databases WHERE server_slug = ?", (normalized,))
            conn.execute("DELETE FROM db_servers WHERE slug = ?", (normalized,))

    def _row_to_server(self, row: sqlite3.Row) -> DatabaseServer:
        """Convert a db_servers row to a DatabaseServer dataclass."""
        import dataclasses
        d = dict(row)
        d["status"] = ProjectStatus(d.get("status", "active"))
        known = {f.name for f in dataclasses.fields(DatabaseServer)}
        return DatabaseServer(**{k: v for k, v in d.items() if k in known})

    # ── file system entity management ──────────────────────────────────────────

    def register_filesystem(self, fs: FileSystemEntity) -> None:
        """Register a filesystem entity in the registry."""
        data = asdict(fs)
        data["slug"] = self._normalize_slug(data["slug"])
        data["status"] = data["status"].value if not isinstance(data["status"], str) else data["status"]
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO file_systems (
                    slug, display_name, local_mount_point, canonical_mount_point,
                    description, status, registered_at, last_surveyed_at,
                    egeria_asset_guid, error_message, egeria_url, egeria_server,
                    egeria_user, egeria_password, governance_state, group_slug
                ) VALUES (
                    :slug, :display_name, :local_mount_point, :canonical_mount_point,
                    :description, :status, :registered_at, :last_surveyed_at,
                    :egeria_asset_guid, :error_message, :egeria_url, :egeria_server,
                    :egeria_user, :egeria_password, :governance_state, :group_slug
                )""",
                data,
            )

    def get_filesystem(self, slug: str) -> FileSystemEntity | None:
        """Retrieve a filesystem entity by slug."""
        normalized = self._normalize_slug(slug)
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM file_systems WHERE slug = ?", (normalized,)).fetchone()
        return self._row_to_filesystem(row) if row else None

    def list_filesystems(self, governance_state: str | None = None) -> list[FileSystemEntity]:
        """List all registered filesystems, optionally filtered by governance state."""
        with self._conn() as conn:
            query = "SELECT * FROM file_systems WHERE 1=1"
            params = []
            if governance_state:
                query += " AND governance_state = ?"
                params.append(governance_state)
            query += " ORDER BY display_name"
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_filesystem(r) for r in rows]

    def filesystem_exists(self, slug: str) -> bool:
        """Check if a filesystem entity exists."""
        return self.get_filesystem(slug) is not None

    def remove_filesystem(self, slug: str) -> None:
        """Remove a filesystem and all its linked surveys."""
        normalized = self._normalize_slug(slug)
        with self._conn() as conn:
            conn.execute("DELETE FROM filesystem_surveys WHERE filesystem_slug = ?", (normalized,))
            # Same reason as remove_database: these carry real FKs, so
            # skipping them fails the parent delete rather than stranding
            # rows.
            for _table, _spec in _DETAIL_TABLE_SPECS.items():
                if _spec.resource_type == "filesystem":
                    conn.execute(
                        f"DELETE FROM {_table} WHERE {_spec.slug_column} = ?",
                        (normalized,),
                    )
            # resource_reachability is excluded from _DETAIL_TABLE_SPECS (see
            # _parse_detail_table_spec's docstring) since it doesn't fit the
            # generic detail-row shape, so it needs its own explicit cleanup
            # here rather than riding the loop above -- same FK-before-parent
            # reasoning as every other child table in this method.
            conn.execute(
                "DELETE FROM resource_reachability WHERE filesystem_slug = ?",
                (normalized,),
            )
            conn.execute(
                "DELETE FROM filesystem_survey_coverage WHERE filesystem_slug = ?",
                (normalized,),
            )
            conn.execute("DELETE FROM file_systems WHERE slug = ?", (normalized,))

    def update_filesystem_status(self, slug: str, status: ProjectStatus, error_message: str = "") -> None:
        """Update status and last error message for a filesystem."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE file_systems SET status = ?, error_message = ? WHERE slug = ?",
                (status.value, error_message, self._normalize_slug(slug)),
            )

    def update_filesystem_surveyed_at(self, slug: str, surveyed_at: str) -> None:
        """Update last surveyed timestamp of a filesystem."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE file_systems SET last_surveyed_at = ? WHERE slug = ?",
                (surveyed_at, self._normalize_slug(slug)),
            )

    def set_filesystem_egeria_guid(self, slug: str, guid: str) -> None:
        """Set Egeria asset GUID for a filesystem."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE file_systems SET egeria_asset_guid = ? WHERE slug = ?",
                (guid, self._normalize_slug(slug)),
            )

    def record_reachability_check(
        self,
        filesystem_slug: str,
        *,
        probed_at: str,
        outcome: str,
        probed_from: str = "",
        error_code: str = "",
        error_detail: str = "",
        latency_ms: int | None = None,
        engine_action_guid: str = "",
        resource_type: str = "filesystem",
    ) -> None:
        """Record one reachability probe result (Phase 1 slice #13).

        Every call writes a NEW row -- this is a history table, not a
        single "current status" row, the same choice `database_settings`/
        `database_grants` etc. make and for the same reason: "reachable at
        T1, unreachable at T2" is itself a fact worth keeping, not just the
        latest value. `get_latest_reachability` is the "current status" read.

        `outcome` must be one of `REACHABILITY_OUTCOMES` (validated here,
        not just by convention) -- see reachability.py's
        classify_check_asset_result() for how a live CHECK_ASSET result maps
        onto this vocabulary, taken verbatim from egeria-support-for-multi-
        resource.md §5 rather than invented for this table.
        """
        if outcome not in REACHABILITY_OUTCOMES:
            raise ValueError(
                f"outcome={outcome!r} is not one of {sorted(REACHABILITY_OUTCOMES)}"
            )
        normalized = self._normalize_slug(filesystem_slug)
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO resource_reachability (
                    resource_type, filesystem_slug, probed_at, probed_from,
                    outcome, error_code, error_detail, latency_ms,
                    engine_action_guid
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    resource_type, normalized, probed_at, probed_from,
                    outcome, error_code, error_detail, latency_ms,
                    engine_action_guid,
                ),
            )

    def get_latest_reachability(self, filesystem_slug: str) -> dict | None:
        """Most recent reachability probe for this filesystem, or None if it
        has never been checked -- the "never checked" state of the
        three-state absence discipline (rule: absence of a row, not a
        sentinel value, means "never checked"). Distinct from a row that
        exists with `outcome='unknown'`, which means "checked, and the
        CHECK_ASSET call itself did not complete/could not be evaluated"."""
        normalized = self._normalize_slug(filesystem_slug)
        with self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM resource_reachability
                   WHERE filesystem_slug = ?
                   ORDER BY probed_at DESC, id DESC LIMIT 1""",
                (normalized,),
            ).fetchone()
        return dict(row) if row else None

    def list_reachability_history(self, filesystem_slug: str, limit: int = 20) -> list[dict]:
        """Reachability probes for this filesystem, most recent first."""
        normalized = self._normalize_slug(filesystem_slug)
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM resource_reachability
                   WHERE filesystem_slug = ?
                   ORDER BY probed_at DESC, id DESC LIMIT ?""",
                (normalized, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def add_filesystem_survey(
        self,
        fs_slug: str,
        surveyed_at: str,
        survey_data: dict,
        egeria_report_guid: str = "",
        source: str = "local",
    ) -> None:
        """Add a survey run to the database and update file counts on the filesystem row."""
        normalized = self._normalize_slug(fs_slug)
        file_count = survey_data.get("total_files", 0)
        data_file_count = survey_data.get("total_data_files", 0)
        total_size_bytes = survey_data.get("total_size_bytes", 0)

        with self._conn() as conn:
            conn.execute(
                """INSERT INTO filesystem_surveys (
                    filesystem_slug, surveyed_at, egeria_report_guid,
                    file_count, data_file_count, total_size_bytes,
                    survey_data, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    normalized,
                    surveyed_at,
                    egeria_report_guid,
                    file_count,
                    data_file_count,
                    total_size_bytes,
                    json.dumps(survey_data),
                    source,
                ),
            )
            # Keep the file_systems row in sync so API responses reflect current counts
            conn.execute(
                """UPDATE file_systems
                   SET file_count = ?, data_file_count = ?, last_surveyed_at = ?
                   WHERE slug = ?""",
                (file_count, data_file_count, surveyed_at, normalized),
            )
        # Structured detail rows from the same blob — see the equivalent note
        # in record_database_survey for why this lives here and why a failure
        # is logged rather than raised.
        try:
            from resource_explorer.surveyors.result_materializer import (
                backfill_filesystem_survey,
            )
            backfill_filesystem_survey(
                self, normalized, surveyed_at, survey_data, source=source
            )
        except Exception as exc:
            log.warning(
                "Structured detail rows not written for filesystem %s @ %s (%s): %s. "
                "The survey_data blob was stored; re-run "
                "scripts/backfill_structured_tables.py to recover the rows.",
                normalized, surveyed_at, source, exc,
            )

    def get_latest_filesystem_survey(self, fs_slug: str) -> dict | None:
        """Retrieve the latest filesystem survey run."""
        normalized = self._normalize_slug(fs_slug)
        with self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM filesystem_surveys
                   WHERE filesystem_slug = ?
                   ORDER BY surveyed_at DESC LIMIT 1""",
                (normalized,),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["survey_data"] = _json_sanitize(json.loads(d.pop("survey_data") or "{}"))
        return d

    def list_filesystem_surveys(self, fs_slug: str) -> list[dict]:
        """List all survey runs for a filesystem ordered by timestamp."""
        normalized = self._normalize_slug(fs_slug)
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, filesystem_slug, surveyed_at, egeria_report_guid,
                          file_count, data_file_count, total_size_bytes, source, survey_data
                   FROM filesystem_surveys
                   WHERE filesystem_slug = ?
                   ORDER BY surveyed_at DESC""",
                (normalized,),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["survey_data"] = _json_sanitize(json.loads(d.pop("survey_data") or "{}"))
            result.append(d)
        return result

    def _row_to_filesystem(self, row: sqlite3.Row) -> FileSystemEntity:
        """Convert a file_systems row to a FileSystemEntity dataclass."""
        import dataclasses
        d = dict(row)
        d["status"] = ProjectStatus(d["status"])
        known = {f.name for f in dataclasses.fields(FileSystemEntity)}
        return FileSystemEntity(**{k: v for k, v in d.items() if k in known})

    # ── activity log ──────────────────────────────────────────────────────────

    def write_activity(self, entry: ActivityEntry) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO activity_log
                   (id, ts, operation, intent, entity_type, entity_slug,
                    entity_name, entity_location, status, summary, detail,
                    items_json, annotations_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     status=excluded.status,
                     summary=excluded.summary,
                     detail=excluded.detail,
                     items_json=excluded.items_json,
                     annotations_json=excluded.annotations_json""",
                (
                    entry.id, entry.ts, entry.operation, entry.intent,
                    entry.entity_type, entry.entity_slug, entry.entity_name,
                    entry.entity_location, entry.status, entry.summary, entry.detail,
                    json.dumps(entry.items), json.dumps(entry.annotations),
                ),
            )

    def list_activity(
        self,
        entity_type: str | None = None,
        entity_slug: str | None = None,
        intent: str | None = None,
        operation: str | None = None,
        status: str | None = None,
        since: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        filters: list[str] = []
        params: list = []
        if entity_type:
            filters.append("entity_type = ?")
            params.append(entity_type)
        if entity_slug:
            filters.append("entity_slug = ?")
            params.append(entity_slug)
        if intent:
            filters.append("intent = ?")
            params.append(intent)
        if operation:
            filters.append("operation = ?")
            params.append(operation)
        if status:
            filters.append("status = ?")
            params.append(status)
        if since:
            filters.append("ts >= ?")
            params.append(since)
        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM activity_log {where} ORDER BY ts DESC LIMIT ?",  # noqa: S608
                params,
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["items"] = json.loads(d.pop("items_json") or "[]")
            d["annotations"] = json.loads(d.pop("annotations_json") or "[]")
            result.append(d)
        return result

    def get_activity(self, entry_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM activity_log WHERE id = ?", (entry_id,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["items"] = json.loads(d.pop("items_json") or "[]")
        d["annotations"] = json.loads(d.pop("annotations_json") or "[]")
        return d

    def get_survey_definition_last_activity(
        self, entity_type: str, entity_slug: str, limit: int = 500,
    ) -> dict[str, dict]:
        """Latest known run/publish info per Survey Definition qualified_name
        for this entity, keyed by the `survey_definition_ref` that
        survey_definitions.py's run route and egeria.py's publish route embed
        in their activity_log `detail` JSON (added 2026-08-24 — direct
        feedback: Discovery's Survey Definition cards had no last-run/last-
        published signal at all). Scans the most recent `limit` rows for this
        entity — bounded, not a full table scan — ordered ts DESC, so the
        first hit per ref per operation kind is already the most recent.

        Falls back to `process_qualified_name` when `survey_definition_ref`
        is absent (added 2026-08-24, found live: monocle2ai/monocle's real,
        recent Scouting Survey runs showed "Never" despite genuinely having
        run — their activity_log rows predate/bypass the ref-tagging above
        and only carry `process_qualified_name`, which SurveyDefinitionExecutor
        always sets natively and which is the same qualified_name value in
        practice). Rows with neither key (or from genuinely unrelated
        operations) are silently skipped — those candidates just show no
        last-run/last-published data, same as one that's genuinely never
        run.

        Publish attribution has the same "who triggered it" gap `run` did:
        egeria.py's publish route only tags `survey_definition_ref` when the
        request came from a Survey Definition candidate's own "☁ Publish"
        button (renderSurveyPanel) — the far more common whole-repo "Publish
        survey →" button (publishSurvey()) has no single candidate to
        attribute to, so its `catalog` row carries no ref at all (found
        live, 2026-08-24: a genuinely published repo showed every candidate
        as never-published even though `Project.is_published` was true).
        Second pass below: the most recent untagged `catalog` row is treated
        as "the whole repo was published as of this timestamp" and applied
        to every candidate whose own last run happened at or before it —
        their findings were necessarily included in that publish, even
        though it wasn't scoped to them specifically. Marked
        `last_published_scope: 'repo'` (vs `'candidate'` for a real per-ref
        match) so the UI can render the distinction honestly rather than
        implying a precision that isn't there."""
        result: dict[str, dict] = {}
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT operation, ts, status, detail FROM activity_log "
                "WHERE entity_type = ? AND entity_slug = ? AND operation IN ('survey', 'catalog') "
                "ORDER BY ts DESC LIMIT ?",
                (entity_type, entity_slug, limit),
            ).fetchall()
        repo_wide_publish_at = ""  # most recent catalog row with no ref at all
        for row in rows:
            try:
                detail = json.loads(row["detail"] or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(detail, dict):
                continue
            ref = detail.get("survey_definition_ref") or detail.get("process_qualified_name")
            if not ref:
                if row["operation"] == "catalog" and not repo_wide_publish_at:
                    repo_wide_publish_at = row["ts"]
                continue
            entry = result.setdefault(ref, {})
            if row["operation"] == "survey" and "last_run_at" not in entry:
                entry["last_run_at"] = row["ts"]
                entry["last_run_status"] = row["status"]
                # The run's own step report — carried so the UI's "ran but
                # something failed" indicator can open what actually failed
                # instead of only flagging that something did (found live
                # 2026-09-26: the ⚠ beside "ran Xm ago" on a Survey
                # Definition card did nothing on click). Empty list on a
                # clean run, never absent, so the frontend can tell
                # "no errors" from "not fetched yet".
                entry["last_run_errors"] = detail.get("errors") or []
                # SurveyDefinitionExecutor.run() (added 2026-08-27) publishes
                # BEFORE it logs the 'survey' row that records the run itself
                # — adapter.publish() runs first, its own untagged 'catalog'
                # row lands a few ms earlier, and this row's timestamp always
                # ends up >= repo_wide_publish_at below as a result. That
                # ordering permanently defeats the repo-wide fallback's
                # `last_run_at <= repo_wide_publish_at` check for every future
                # auto-published run — confirmed live 2026-08-28 running
                # RepoCoarseScout against egeria_docs: publish genuinely
                # succeeded (a real 'catalog' row exists) yet the candidate
                # still showed unpublished. The executor already puts its own
                # outcome in this very row's detail (`"published": bool`) —
                # trust that directly instead of leaning on the fuzzy
                # timestamp heuristic for runs that carry it.
                if detail.get("published") is True:
                    entry["last_published_at"] = row["ts"]
                    entry["last_published_scope"] = "candidate"
            elif row["operation"] == "catalog" and "last_published_at" not in entry:
                entry["last_published_at"] = row["ts"]
                entry["last_published_scope"] = "candidate"
        if repo_wide_publish_at:
            for entry in result.values():
                if "last_published_at" in entry:
                    continue
                # `entry.get("last_run_at", "")` used to fall through to "" for a
                # candidate that has an entry (via a ref-tagged catalog row) but no
                # matching survey row — and "" <= anything is always True in Python
                # string comparison, so a candidate that never ran still got
                # last_published_scope: 'repo' stamped on it. Confirmed live
                # 2026-08-27: this is what produced "Never Run" + "Published today"
                # on the same card. A candidate can only have "necessarily been
                # included" in a repo-wide publish if it actually ran.
                last_run_at = entry.get("last_run_at")
                if last_run_at and last_run_at <= repo_wide_publish_at:
                    entry["last_published_at"] = repo_wide_publish_at
                    entry["last_published_scope"] = "repo"
        return result

    def get_analysis_last_run(
        self, entity_type: str, entity_slug: str, limit: int = 500,
    ) -> dict[str, dict]:
        """{analysis_id: {last_run_at, last_run_status}} for this entity's
        local AnalysisKind cards — the Analyses-card equivalent of
        get_survey_definition_last_activity() above, added the same day for
        the same reason (direct feedback: Analyses cards had no last-run
        signal at all, unlike Survey Definition cards).

        Generalized beyond repo (docs/Backlog.md: "Database/filesystem
        Analyses cards never showed a last-run/published badge" — the UI only
        ever fetched this for entity_type='repo', so database/filesystem
        cards could not show a badge no matter how many surveys had run).
        `entity_type` selects the step map via `_analysis_step_map()`/
        `_analysis_derived_sources()` above; the SQL and the attribution
        logic below are unchanged and identical for every entity_type.

        Reads TWO kinds of activity row, because an analysis can be invoked two
        ways and reading only one made almost everything look never-run:

        * operation='analysis_run' — the per-analysis "Run →" button.
        * operation='survey' — a Survey Definition run, whose detail records
          every step it executed. These are the NORMAL path (scheduled runs,
          "Run" on a survey card) and never write analysis_run rows. The whole
          database held 17 analysis_run rows against 116 survey rows, so every
          analysis on every repo reported "Never run" while its results sat in
          the findings tables and its annotations sat published — the card said
          "Never run" and "Published today" side by side.

        Each step's `re_analysis_step` key (recorded directly in `detail.steps`
        by survey_definition_executor as of this same change — previously only
        the step's qualifiedName was recorded, and callers had to assume its
        last `::`-separated segment equalled the re_analysis_step key, which
        held for repo's own authoring convention but is NOT guaranteed by
        anything in the schema; falls back to that same qualifiedName-suffix
        parse for historical rows that predate the field) is looked up in
        `_analysis_step_map(entity_type)`'s inversion.

        For repo, REPO_ANALYSIS_STEP_MAP partitions step keys across
        analyses — each key belongs to exactly one — so attribution is exact.
        For database, a single coarse step (e.g. "db_derived") is the source
        of SEVERAL analysis_catalog entries at once (see
        DATABASE_ANALYSIS_RE_STEP_MAP's docstring) — that fan-out is real and
        intentional, not a guess, so one step run credits every analysis_id
        it names, not just one.

        An analysis owning several step keys counts as run when ANY of them
        ran: it did real work then, and calling that "never run" is the larger
        error. `last_run_partial` says whether the run covered all its steps.
        """
        step_owner = self._step_key_to_analysis_ids(entity_type)
        owned_counts = {a: len(k) for a, k in _analysis_step_map(entity_type).items()}
        result: dict[str, dict] = {}

        with self._conn() as conn:
            rows = conn.execute(
                "SELECT ts, status, detail, operation FROM activity_log "
                "WHERE entity_type = ? AND entity_slug = ? "
                "AND operation IN ('analysis_run', 'survey') "
                "ORDER BY ts DESC LIMIT ?",
                (entity_type, entity_slug, limit),
            ).fetchall()

        unattributable = 0
        for row in rows:
            try:
                detail = json.loads(row["detail"] or "{}")
            except (TypeError, ValueError):
                detail = {}
            if not isinstance(detail, dict):
                detail = {}
            if row["operation"] == "survey" and not (detail.get("steps") or []):
                # A real survey happened; it just predates step recording. That
                # is "we cannot say which analyses ran", not "none did".
                unattributable += 1
                continue

            if row["operation"] == "analysis_run":
                analysis_id = detail.get("analysis_id")
                # A DERIVED analysis runs steps it does not own, so this row is
                # also evidence that the owners of those steps just ran. Credited
                # here rather than at write time so the correction applies to
                # rows already in the log — measured 2026-09-09, an
                # architecture_diagram run had rewritten architecture_recovery's
                # data minutes earlier while its card still read ten days stale.
                # Newest-first, so this loses to any more recent row of the
                # source's own, exactly like the survey-step branch below.
                for source_id, keys in _analysis_derived_sources(
                        entity_type, analysis_id or "").items():
                    if source_id in result:
                        continue
                    result[source_id] = {
                        "last_run_at": row["ts"],
                        # The derived run's own status. It dispatched these steps,
                        # so if it failed we cannot claim the source succeeded.
                        "last_run_status": row["status"],
                        # Named distinctly from "analysis": nobody ran the source
                        # directly, and a reader deciding whether to trust this
                        # freshness should be able to see that.
                        "last_run_via": "derived",
                        "last_run_partial": len(keys) < owned_counts.get(source_id, 0),
                        "last_publish_failed": False,
                        "last_run_derived_from": analysis_id,
                    }
                if analysis_id and analysis_id not in result:
                    result[analysis_id] = {
                        "last_run_at": row["ts"], "last_run_status": row["status"],
                        "last_run_via": "analysis", "last_run_partial": False,
                        # True only when this run's auto-publish attempt
                        # (projects.py's run_single_analysis) actually failed —
                        # None (never attempted: unassigned, or no annotations)
                        # is NOT a failure and must not read as one. The
                        # frontend's ☁ Publish button uses this to decide
                        # whether it's a recovery action worth showing.
                        "last_publish_failed": detail.get("published") is False,
                    }
                continue

            ran: dict[str, list] = {}
            for step in (detail.get("steps") or []):
                # `re_analysis_step` is the real key, recorded directly since
                # this same change; a historical row that predates it falls
                # back to the qualifiedName-suffix parse repo always relied on.
                key = step.get("re_analysis_step") or str(step.get("step") or "").rsplit("::", 1)[-1]
                for owner in step_owner.get(key, []):
                    ran.setdefault(owner, []).append(step.get("status") or "")
            for analysis_id, statuses in ran.items():
                if analysis_id in result:
                    continue    # newest-first, so the first win stands
                result[analysis_id] = {
                    "last_run_at": detail.get("surveyed_at") or row["ts"],
                    # The steps' own status, not the survey's: a survey that
                    # errored elsewhere still ran this analysis successfully.
                    "last_run_status": "error" if any(s == "error" for s in statuses) else "ok",
                    "last_run_via": "survey",
                    "last_run_partial": len(statuses) < owned_counts.get(analysis_id, 0),
                }
        if unattributable:
            # Recorded against a reserved key rather than spread across every
            # analysis: the point is that we do not know which ones ran, and
            # marking them all "ran" would be the same lie in the other
            # direction. Callers read it to choose "not established" over
            # "never run" for analyses with no row of their own.
            result["__unattributed_surveys__"] = {
                "count": unattributable, "last_run_at": "", "last_run_status": "",
                "last_run_via": "", "last_run_partial": False,
            }
        return result

    def analysis_run_activity_seconds(
        self, analysis_ids: list[str], *, status: str = "ok", limit: int = 2000,
    ) -> dict[str, list[tuple[float | None, float | None]]]:
        """{analysis_id: [(steps_seconds, publish_seconds), ...]} from
        `analysis_run` activity_log rows whose `detail` carries the per-phase
        split — added when `execute_and_record_analysis` started writing
        `steps_seconds`/`publish_seconds` into each terminal row's `detail`
        (the designer's ruling, 2026-09-13: wall clock belongs where someone
        is deciding, and always split). A row from before that change carries
        neither key and is skipped here, the same way a row whose `detail`
        fails to parse is skipped elsewhere in this class — absence of the
        instrumentation is not a zero-second measurement.

        Scoped to `analysis_ids` because every caller (today, just
        `estimate_run_cost`) already knows exactly which ids it needs — an
        analysis and its derived sources — cheaper than scanning every
        analysis in the log. `status` defaults to `'ok'`, the terminal status
        `execute_and_record_analysis` writes on success (`runs`' vocabulary
        calls the same thing 'succeeded').

        `publish_seconds` can be `None` inside an otherwise-qualifying row
        (`publish_mode: "not-attempted"` — no assigned Egeria project, or no
        annotations) — callers must drop those `None`s before taking a
        publish median rather than let a not-attempted run count as an
        instant one.
        """
        if not analysis_ids:
            return {}
        wanted = set(analysis_ids)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT detail FROM activity_log WHERE operation = 'analysis_run' "
                "AND status = ? ORDER BY ts DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        out: dict[str, list[tuple[float | None, float | None]]] = {
            aid: [] for aid in analysis_ids
        }
        for row in rows:
            try:
                detail = json.loads(row["detail"] or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(detail, dict):
                continue
            aid = detail.get("analysis_id")
            if aid not in wanted:
                continue
            steps = detail.get("steps_seconds")
            publish = detail.get("publish_seconds")
            if steps is None and publish is None:
                continue  # pre-split row — carries neither, not a data point
            out[aid].append((steps, publish))
        return out

    @staticmethod
    def _step_key_to_analysis_ids(entity_type: str) -> dict[str, list[str]]:
        """Inverse of `_analysis_step_map(entity_type)`: step_key ->
        [analysis_id, ...]. A list, not a single id, because database's
        `DATABASE_ANALYSIS_RE_STEP_MAP` genuinely fans one step key out to
        several analysis_ids (e.g. "db_derived" -> six analyses) — unlike
        repo's REPO_ANALYSIS_STEP_MAP, which partitions the step-key space so
        this inversion happens to be 1:1 there. `setdefault(...).append(...)`
        preserves every owner rather than the dict-comprehension overwrite
        `_repo_analysis_step_map`'s own docstring warns can silently drop
        one when two analyses declare the same key by mistake."""
        out: dict[str, list[str]] = {}
        for analysis_id, keys in _analysis_step_map(entity_type).items():
            for key in keys:
                out.setdefault(key, []).append(analysis_id)
        return out

    def update_activity_status(
        self,
        entry_id: str,
        status: str,
        summary: str = "",
        detail: str = "",
        annotations: list[dict] | None = None,
        publish_run_id: str = "",
    ) -> None:
        """Finalise a 'running' activity entry.

        `publish_run_id` (added for background/deferred publish — run-in-
        background plan): the outbox run_id this entry's publish was enqueued
        under, when `detail`'s `published` is `"queued"`. Lets
        `complete_publish_run_if_done()` find this exact row later and flip it
        to `published: True`, without scanning every row's `detail` JSON.
        Empty (the default) leaves the column untouched, same CASE-guarded
        convention `summary`/`detail`/`annotations_json` already use — a
        caller with nothing to add must not erase what the entry already
        carried.

        `annotations` added 2026-09-02. Without it, every Analyses-card run
        wrote its entry with an empty annotations list while its summary said
        "N annotation(s)" — so GET /api/activity/rfas, which flattens
        entry["annotations"] and keeps the RequestForAction ones, could not
        see a single RFA produced by that path. Measured on `workshops`: the
        run produced 3 RequestForAction annotations ("No SECURITY.md found",
        "No CI configuration detected", "No license file detected") and the
        drawer count stayed at 1, where the 1 came from an older full survey.
        Two of the three had never been visible at all.

        Same defect tests/test_rfa_visibility.py documents for log_rfa, in a
        different writer — which is why that test did not catch it.

        An empty list is NOT written over an existing value: a caller with
        nothing to add must not erase what the entry already carried.
        """
        # default=str, not bare dumps: a single unserialisable field in one
        # annotation would otherwise raise here and lose the ENTIRE activity
        # update — status, summary and all — turning a successful run into a
        # crash report. Degrading one field to its repr is strictly better
        # than losing the record of what ran.
        anns_json = json.dumps(annotations, default=str) if annotations else ""
        with self._conn() as conn:
            conn.execute(
                "UPDATE activity_log SET status = ?, "
                "summary = CASE WHEN ? != '' THEN ? ELSE summary END, "
                "detail  = CASE WHEN ? != '' THEN ? ELSE detail  END, "
                "annotations_json = CASE WHEN ? != '' THEN ? ELSE annotations_json END, "
                "publish_run_id = CASE WHEN ? != '' THEN ? ELSE publish_run_id END "
                "WHERE id = ?",
                (status, summary, summary, detail, detail,
                 anns_json, anns_json, publish_run_id, publish_run_id, entry_id),
            )

    # reconcile_orphaned_running_activity() lived here briefly (2026-08-26) —
    # removed in favor of resource_explorer/run_reconciler.py's
    # ownership-based (pid + process-start-time) reconciliation, wired into
    # web/app.py's _lifespan(). This method's blanket "every running row is
    # orphaned on any startup" was unsafe: multiple RE processes routinely
    # share one database, so it would falsely resolve a peer's still-live
    # run out from under it.

    # ── RFA actions (local response tracking — see PATCH /api/activity/rfas/{rfa_id}
    # in web/routes/activity.py) ─────────────────────────────────────────────
    #
    # RFAs themselves aren't first-class rows — GET /rfas flattens
    # RequestForAction-tagged entries out of each activity_log row's
    # annotations_json at read time. This table is keyed on the same
    # synthetic id that flatten step mints positionally
    # (f"{entry_id}::{annotation_index}"), and overlays a real, updatable
    # status/assignee/defer/resolution onto each flattened RFA.
    #
    # Egeria ToDo sync (docs/rfa-egeria-todo-followup.md, decided
    # 2026-08-15/implemented 2026-08-16): activity_status/due_time/
    # start_time/priority mirror ToDoProperties directly (Egeria's own
    # ACTIVITY_STATUS vocabulary — REQUESTED/WAITING/IN_PROGRESS/COMPLETED/
    # etc.) alongside rfa_status/defer_until (RE's own friendly verbs —
    # open/deferred/reassigned/completed — still the API/frontend's wire
    # contract, unchanged this pass). One translation seam, in
    # web/routes/activity.py, maps friendly verb -> activity_status exactly
    # once; rfa_egeria_sync.py (the module that actually talks to Egeria)
    # reads activity_status/due_time straight through, no re-translation.
    # egeria_todo_guid/synced_at/sync_error track the real Egeria ToDo this
    # row is synced with, per the "local write is authoritative, Egeria
    # call attempted non-blocking, both-direction reconciliation" sync
    # design in that doc.

    def upsert_rfa_action(
        self,
        rfa_id: str,
        entry_id: str,
        annotation_index: int,
        status: str,
        activity_status: str,
        assignee: str = "",
        defer_until: str = "",
        start_time: str = "",
        priority: int = 0,
        resolution_note: str = "",
    ) -> None:
        """Local write — authoritative for the API response regardless of
        Egeria's reachability (sync mechanics point 1). Callers attempt the
        matching Egeria sync (rfa_egeria_sync.sync_rfa_action) separately,
        after this write succeeds.

        `status` (RE's friendly verb — open/deferred/reassigned/completed,
        the drawer's own wire contract, unchanged) and `activity_status`
        (Egeria's real ToDoProperties.activityStatus value the route maps
        it to — see activity.py's _STATUS_TO_ACTIVITY_STATUS) are stored
        separately, not derived from one another here — the translation
        happens exactly once, at the route layer, not duplicated into the
        registry. `defer_until` is stored verbatim under both its original
        column and `due_time` (same value/semantics, no translation
        needed — due_time is just the ToDoProperties-named read path
        rfa_egeria_sync.py uses)."""
        updated_at = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO rfa_actions
                   (id, entry_id, annotation_index, rfa_status, activity_status,
                    assignee, defer_until, due_time, start_time, priority,
                    resolution_note, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     rfa_status=excluded.rfa_status,
                     activity_status=excluded.activity_status,
                     assignee=excluded.assignee,
                     defer_until=excluded.defer_until,
                     due_time=excluded.due_time,
                     start_time=excluded.start_time,
                     priority=excluded.priority,
                     resolution_note=excluded.resolution_note,
                     updated_at=excluded.updated_at""",
                (
                    rfa_id, entry_id, annotation_index, status, activity_status,
                    assignee, defer_until, defer_until, start_time, priority,
                    resolution_note, updated_at,
                ),
            )

    def upsert_rfa_note(self, rfa_id: str, entry_id: str, annotation_index: int, notes: str) -> None:
        """Real, persisted free-text notes on an RFA — addable independent
        of any status change (unlike resolution_note, recorded only when
        completing). Real bug fixed 2026-08-16: the drawer's "Record answer"
        button previously called a purely client-side, in-memory logOp() —
        never a backend call, so nothing survived a page reload. Upserts a
        fresh row (status defaults to 'open'/'REQUESTED') when this RFA has
        no prior response-action row yet — a note can be the first
        interaction with an RFA, not just something added after Defer/
        Reassign/Complete."""
        updated_at = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO rfa_actions (id, entry_id, annotation_index, notes, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET notes=excluded.notes, updated_at=excluded.updated_at""",
                (rfa_id, entry_id, annotation_index, notes, updated_at),
            )

    def get_rfa_action(self, rfa_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM rfa_actions WHERE id = ?", (rfa_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_rfa_action_overrides(self) -> dict[str, dict]:
        """All rfa_actions rows, keyed by id — used to overlay live status
        onto the read-time-flattened RFA list without a per-row query."""
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM rfa_actions").fetchall()
        return {r["id"]: dict(r) for r in rows}

    # ── RFA dismissals ──────────────────────────────────────────────────
    # See the rfa_dismissals DDL above for why this is a content-keyed table
    # of its own rather than another rfa_status on rfa_actions.

    RFA_DISMISSAL_REASONS = ("not_applicable", "wont_do")

    @staticmethod
    def rfa_dismissal_key(
        entity_type: str, entity_slug: str, analysis_name: str, summary_key: str
    ) -> str:
        """The one place the content key is derived.

        Both the write path (dismiss_rfa) and the read path
        (active_rfa_dismissals) call this — a dismissal that hashed
        differently from the lookup that has to find it would suppress
        nothing, silently, with no error anywhere. One function, so the two
        cannot drift apart.

        Whitespace-normalised and case-folded so a summary that gains a
        trailing space between runs still matches; NOT otherwise fuzzy (see
        the known limit in the DDL comment)."""
        parts = [
            (entity_type or "").strip().lower(),
            (entity_slug or "").strip().lower(),
            (analysis_name or "").strip().lower(),
            " ".join((summary_key or "").split()).lower(),
        ]
        return hashlib.sha256("\u001f".join(parts).encode("utf-8")).hexdigest()[:32]

    def dismiss_rfa(
        self,
        entity_type: str,
        entity_slug: str,
        analysis_name: str,
        summary_key: str,
        reason: str,
        note: str = "",
        created_by: str = "",
    ) -> dict:
        """Record that a finding is not going to be acted on here.

        Re-dismissing a previously cleared dismissal reinstates it in place
        (same row, cleared_at/cleared_by blanked, created_at restamped)
        rather than accumulating a second row for the same finding — the
        table answers "is this suppressed right now", and one finding has one
        answer.

        Raises ValueError on an unknown reason: an unrecognised value would
        otherwise be stored and then never match any filter, which is the
        silent-no-op shape this whole table exists to avoid."""
        if reason not in self.RFA_DISMISSAL_REASONS:
            raise ValueError(
                f"Unknown dismissal reason {reason!r} — expected one of "
                f"{', '.join(self.RFA_DISMISSAL_REASONS)}"
            )
        if not (entity_slug or "").strip():
            raise ValueError("entity_slug is required — a dismissal with no subject matches everything")
        did = self.rfa_dismissal_key(entity_type, entity_slug, analysis_name, summary_key)
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO rfa_dismissals
                       (id, entity_type, entity_slug, analysis_name, summary_key,
                        reason, note, created_by, created_at, cleared_at, cleared_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', '')
                   ON CONFLICT(id) DO UPDATE SET
                       reason = excluded.reason,
                       note = excluded.note,
                       created_by = excluded.created_by,
                       created_at = excluded.created_at,
                       cleared_at = '',
                       cleared_by = ''""",
                (did, entity_type, entity_slug, analysis_name, summary_key,
                 reason, note, created_by, now),
            )
            row = conn.execute("SELECT * FROM rfa_dismissals WHERE id = ?", (did,)).fetchone()
        return dict(row)

    def clear_rfa_dismissal(self, dismissal_id: str, cleared_by: str = "") -> dict | None:
        """Reverse a dismissal, keeping the record.

        Returns the updated row, or None if no such dismissal exists —
        distinguishable by the caller from "cleared it", so a stale id
        reports as not-found instead of as success. Clearing an
        already-cleared dismissal is a no-op that still returns the row."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM rfa_dismissals WHERE id = ?", (dismissal_id,)
            ).fetchone()
            if row is None:
                return None
            if not (row["cleared_at"] or ""):
                conn.execute(
                    "UPDATE rfa_dismissals SET cleared_at = ?, cleared_by = ? WHERE id = ?",
                    (now, cleared_by, dismissal_id),
                )
                row = conn.execute(
                    "SELECT * FROM rfa_dismissals WHERE id = ?", (dismissal_id,)
                ).fetchone()
        return dict(row)

    def active_rfa_dismissals(self) -> dict[str, dict]:
        """Currently-suppressing dismissals, keyed by content key.

        Only rows with an empty cleared_at — a cleared dismissal stays in the
        table as history and must not go on suppressing anything."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM rfa_dismissals WHERE COALESCE(cleared_at, '') = ''"
            ).fetchall()
        return {r["id"]: dict(r) for r in rows}

    def list_rfa_dismissals(self, include_cleared: bool = False) -> list[dict]:
        """Every dismissal, newest first — the listing a future admin surface
        reads to review and reverse past decisions. Cleared ones are excluded
        by default so the common read is "what is suppressed now"."""
        sql = "SELECT * FROM rfa_dismissals"
        if not include_cleared:
            sql += " WHERE COALESCE(cleared_at, '') = ''"
        sql += " ORDER BY created_at DESC"
        with self._conn() as conn:
            rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]

    def mark_rfa_synced(self, rfa_id: str, egeria_todo_guid: str) -> None:
        """Record a successful Egeria ToDo sync — clears any prior
        sync_error (a retry that finally succeeds shouldn't keep showing
        stale failure state)."""
        synced_at = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE rfa_actions SET egeria_todo_guid = ?, synced_at = ?, sync_error = '' WHERE id = ?",
                (egeria_todo_guid, synced_at, rfa_id),
            )

    def mark_rfa_sync_error(self, rfa_id: str, error: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE rfa_actions SET sync_error = ? WHERE id = ?",
                (error, rfa_id),
            )

    def list_unsynced_rfa_actions(self) -> list[dict]:
        """Rows needing a write-direction sync retry (reconciliation pass,
        write direction) — never synced yet, or synced but a later local
        write failed to propagate."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM rfa_actions WHERE egeria_todo_guid = '' OR sync_error != ''"
            ).fetchall()
        return [dict(r) for r in rows]

    def list_synced_rfa_actions(self) -> list[dict]:
        """Rows with a real Egeria ToDo behind them — read-direction
        reconciliation pulls each one's current remote state and reconciles
        it against these local rows."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM rfa_actions WHERE egeria_todo_guid != '' AND sync_error = ''"
            ).fetchall()
        return [dict(r) for r in rows]

    def update_rfa_from_remote(
        self, rfa_id: str, activity_status: str, due_time: str = "", start_time: str = "", priority: int = 0,
    ) -> None:
        """Read-direction reconciliation write — a change made by another
        Egeria client (not through RE's own drawer) is pulled in here.
        Doesn't touch egeria_todo_guid/sync_error (those are write-direction
        bookkeeping); does bump updated_at/synced_at since this is a real,
        confirmed-current state refresh."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE rfa_actions SET activity_status = ?, due_time = ?, start_time = ?, "
                "priority = ?, updated_at = ?, synced_at = ? WHERE id = ?",
                (activity_status, due_time, start_time, priority, now, now, rfa_id),
            )

    def mark_rfa_notelog(self, rfa_id: str, notelog_guid: str) -> None:
        """Cache the Egeria NoteLog GUID created on this RFA's ToDo, so
        later note edits reuse it instead of creating a new NoteLog every
        time (rfa_egeria_sync.sync_rfa_note)."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE rfa_actions SET egeria_notelog_guid = ? WHERE id = ?",
                (notelog_guid, rfa_id),
            )

    def mark_rfa_note_synced(self, rfa_id: str, notes_value: str) -> None:
        """Record that `notes_value` was successfully pushed to Egeria as
        an ActivityEntry — the comparison point for "has this note text
        already been synced" (sync_rfa_note skips re-pushing an unchanged
        note as a duplicate ActivityEntry)."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE rfa_actions SET notes_synced_value = ?, notes_sync_error = '' WHERE id = ?",
                (notes_value, rfa_id),
            )

    def mark_rfa_note_sync_error(self, rfa_id: str, error: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE rfa_actions SET notes_sync_error = ? WHERE id = ?",
                (error, rfa_id),
            )

    def list_unsynced_rfa_notes(self) -> list[dict]:
        """Rows with a real ToDo, real note text, and that note text not
        yet (successfully) pushed to Egeria — write-direction retry target
        for sync_rfa_note, mirroring list_unsynced_rfa_actions' shape for
        the status/ToDo sync. A note added before any status action ever
        ran (no egeria_todo_guid yet) is correctly excluded here — it picks
        up automatically once a later Defer/Reassign/Complete creates the
        ToDo and the next reconciliation pass runs."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM rfa_actions WHERE egeria_todo_guid != '' AND notes != '' "
                "AND notes != notes_synced_value"
            ).fetchall()
        return [dict(r) for r in rows]

    def update_governance_state(self, entity_type: str, slug: str, state: str) -> None:
        """Update the governance state of a registered resource."""
        slug = self._normalize_slug(slug)
        table_map = {
            "repo": "projects",
            "database": "databases",
            "filesystem": "file_systems"
        }
        table = table_map.get(entity_type)
        if not table:
            raise ValueError(f"Unknown entity_type: {entity_type}")
        with self._conn() as conn:
            conn.execute(f"UPDATE {table} SET governance_state = ? WHERE slug = ?", (state, slug))

    def list_annotation_types(self) -> list[dict]:
        """List all annotation types in the registry."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT annotation_type, display_name, description, properties, egeria_type, python_class
                   FROM annotation_types
                   ORDER BY annotation_type"""
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["properties"] = json.loads(d["properties"] or "[]")
            except Exception:
                d["properties"] = []
            result.append(d)
        return result

    def get_annotation_type(self, annotation_type: str) -> dict | None:
        """Get a single annotation type by name."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT annotation_type, display_name, description, properties, egeria_type, python_class
                   FROM annotation_types
                   WHERE annotation_type = ?""",
                (annotation_type,),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["properties"] = json.loads(d["properties"] or "[]")
        except Exception:
            d["properties"] = []
        return d

    def register_annotation_type(
        self,
        annotation_type: str,
        display_name: str,
        description: str = "",
        properties: list[str] | str = "[]",
        egeria_type: str = "",
        python_class: str = "",
    ) -> None:
        """Register a new annotation type."""
        if isinstance(properties, list):
            properties_str = json.dumps(properties)
        else:
            properties_str = properties
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO annotation_types (
                    annotation_type, display_name, description, properties, egeria_type, python_class
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (annotation_type, display_name, description, properties_str, egeria_type, python_class),
            )

    def update_annotation_type(
        self,
        annotation_type: str,
        display_name: str,
        description: str = "",
        properties: list[str] | str = "[]",
        egeria_type: str = "",
        python_class: str = "",
    ) -> None:
        """Update an existing annotation type."""
        if isinstance(properties, list):
            properties_str = json.dumps(properties)
        else:
            properties_str = properties
        with self._conn() as conn:
            conn.execute(
                """UPDATE annotation_types
                   SET display_name = ?, description = ?, properties = ?, egeria_type = ?, python_class = ?
                   WHERE annotation_type = ?""",
                (display_name, description, properties_str, egeria_type, python_class, annotation_type),
            )

    def delete_annotation_type(self, annotation_type: str) -> None:
        """Delete an annotation type from the registry."""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM annotation_types WHERE annotation_type = ?",
                (annotation_type,),
            )

    # ── project group management ─────────────────────────────────────────────

    def create_group(self, slug: str, display_name: str, description: str = "") -> None:
        """Create (or rename) an umbrella project group."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO project_groups (slug, display_name, description, created_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT (slug) DO UPDATE SET "
                "display_name = excluded.display_name, description = excluded.description, "
                "created_at = excluded.created_at",
                (slug, display_name, description, datetime.utcnow().isoformat()),
            )

    def get_group(self, slug: str) -> ProjectGroup | None:
        """Retrieve a project group by slug."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM project_groups WHERE slug = ?", (slug,)).fetchone()
        return ProjectGroup(**dict(row)) if row else None

    def list_groups(self) -> list[ProjectGroup]:
        """List all project groups."""
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM project_groups ORDER BY display_name").fetchall()
        return [ProjectGroup(**dict(r)) for r in rows]

    def delete_group(self, slug: str) -> int:
        """Delete a group. Member resources are ungrouped, not removed."""
        slug = self._normalize_slug(slug)
        with self._conn() as conn:
            # Ungroup all resources in this group
            conn.execute("UPDATE projects SET group_slug = '' WHERE group_slug = ?", (slug,))
            conn.execute("UPDATE databases SET group_slug = '' WHERE group_slug = ?", (slug,))
            conn.execute("UPDATE file_systems SET group_slug = '' WHERE group_slug = ?", (slug,))
            cursor = conn.execute("DELETE FROM project_groups WHERE slug = ?", (slug,))
            return cursor.rowcount

    def set_project_group(self, resource_slug: str, group_slug: str) -> None:
        """Assign a repository to a group."""
        resource_slug = self._normalize_slug(resource_slug)
        group_slug = self._normalize_slug(group_slug) if group_slug else ""
        with self._conn() as conn:
            conn.execute("UPDATE projects SET group_slug = ? WHERE slug = ?", (group_slug, resource_slug))

    def set_database_group(self, database_slug: str, group_slug: str) -> None:
        """Assign a database to a group."""
        database_slug = self._normalize_slug(database_slug)
        group_slug = self._normalize_slug(group_slug) if group_slug else ""
        with self._conn() as conn:
            conn.execute("UPDATE databases SET group_slug = ? WHERE slug = ?", (group_slug, database_slug))

    def set_filesystem_group(self, filesystem_slug: str, group_slug: str) -> None:
        """Assign a filesystem to a group."""
        filesystem_slug = self._normalize_slug(filesystem_slug)
        group_slug = self._normalize_slug(group_slug) if group_slug else ""
        with self._conn() as conn:
            conn.execute("UPDATE file_systems SET group_slug = ? WHERE slug = ?", (group_slug, filesystem_slug))

    def list_projects_in_group(self, group_slug: str) -> list[Project]:
        """List all repositories assigned to a group."""
        group_slug = self._normalize_slug(group_slug)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM projects WHERE group_slug = ? ORDER BY display_name",
                (group_slug,),
            ).fetchall()
        return [self._row_to_project(r) for r in rows]

    def list_databases_in_group(self, group_slug: str) -> list[DatabaseEntity]:
        """List all databases assigned to a group."""
        group_slug = self._normalize_slug(group_slug)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM databases WHERE group_slug = ? ORDER BY display_name",
                (group_slug,),
            ).fetchall()
        return [self._row_to_database(r) for r in rows]

    def list_filesystems_in_group(self, group_slug: str) -> list[FileSystemEntity]:
        """List all filesystems assigned to a group."""
        group_slug = self._normalize_slug(group_slug)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM file_systems WHERE group_slug = ? ORDER BY display_name",
                (group_slug,),
            ).fetchall()
        return [self._row_to_filesystem(r) for r in rows]

    def get_group_aggregate_stats(self, group_slug: str) -> dict:
        """Sum the latest stats snapshot across every member of a group."""
        group_slug = self._normalize_slug(group_slug)
        repos = self.list_projects_in_group(group_slug)
        dbs = self.list_databases_in_group(group_slug)
        fss = self.list_filesystems_in_group(group_slug)

        totals = {
            "project_count": len(repos),
            "database_count": len(dbs),
            "filesystem_count": len(fss),
            "stars": 0, "forks": 0, "watchers": 0, "open_issues": 0,
            "commits_30d": 0, "commits_90d": 0,
            "contributors_count": 0,
            "languages": {},
            "db_schema_count": 0, "db_table_count": 0, "db_column_count": 0,
            "fs_file_count": 0, "fs_data_file_count": 0,
        }
        for r in repos:
            snap = self.get_latest_project_stats(r.slug)
            if snap:
                for key in ("stars", "forks", "watchers", "open_issues", "commits_30d", "commits_90d"):
                    totals[key] += snap.get(key) or 0
                totals["contributors_count"] = max(totals["contributors_count"], snap.get("contributors_count") or 0)
                lang = snap.get("primary_language")
                if lang:
                    totals["languages"][lang] = totals["languages"].get(lang, 0) + 1
        
        for db in dbs:
            totals["db_schema_count"] += getattr(db, "schema_count", 0) or 0
            totals["db_table_count"] += getattr(db, "table_count", 0) or 0
            totals["db_column_count"] += getattr(db, "column_count", 0) or 0

        for fs in fss:
            totals["fs_file_count"] += getattr(fs, "file_count", 0) or 0
            totals["fs_data_file_count"] += getattr(fs, "data_file_count", 0) or 0

        return totals


def _repo_analysis_step_map() -> dict:
    """REPO_ANALYSIS_STEP_MAP, fetched lazily.

    repo_survey_definition_adapter imports this module at import time, so a
    module-level import here would close the cycle.
    """
    try:
        from resource_explorer.surveyors.repo_survey_definition_adapter import (
            REPO_ANALYSIS_STEP_MAP,
        )
    except ImportError:  # pragma: no cover - defensive
        return {}
    return REPO_ANALYSIS_STEP_MAP


def _analysis_step_map(entity_type: str) -> dict[str, list[str]]:
    """analysis_id -> [re_analysis_step keys] for `entity_type`, fetched
    lazily (same import-cycle reason as `_repo_analysis_step_map` above).

    Added alongside the database/filesystem last-activity endpoints
    (docs/Backlog.md: "Database/filesystem Analyses cards never showed a
    last-run/published badge") to generalize `get_analysis_last_run` beyond
    repo. `entity_type` values outside this map (or an import failure)
    return `{}` — an entity_type with no step map simply attributes no
    survey rows, same as repo behaved before REPO_ANALYSIS_STEP_MAP existed.
    """
    if entity_type == "repo":
        return _repo_analysis_step_map()
    if entity_type == "database":
        try:
            from resource_explorer.surveyors.database.survey_definition_adapter import (
                DATABASE_ANALYSIS_RE_STEP_MAP,
            )
        except ImportError:  # pragma: no cover - defensive
            return {}
        return DATABASE_ANALYSIS_RE_STEP_MAP
    if entity_type == "filesystem":
        try:
            from resource_explorer.surveyors.filesystem.survey_definition_adapter import (
                FILESYSTEM_ANALYSIS_STEP_MAP,
            )
        except ImportError:  # pragma: no cover - defensive
            return {}
        return FILESYSTEM_ANALYSIS_STEP_MAP
    return {}


def _analysis_derived_sources(entity_type: str, analysis_id: str) -> dict[str, list[str]]:
    """{source_analysis_id: [step_keys]} that `analysis_id` derives from, for
    `entity_type` — repo's `architecture_diagram`-derives-from-
    `architecture_recovery` pattern (see repo_analysis_derived_sources).
    Database and filesystem have no AnalysisKind-style `derives_from`
    declaration today, so this is `{}` for every other entity_type — not a
    guess, a real absence: neither adapter declares one analysis as a view
    over another's steps the way repo's architecture_diagram does."""
    if entity_type != "repo":
        return {}
    from resource_explorer.surveyors.repo_survey_definition_adapter import (
        repo_analysis_derived_sources,
    )

    return repo_analysis_derived_sources(analysis_id)
