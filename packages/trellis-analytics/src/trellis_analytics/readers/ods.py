"""ODS reader: RE's registry, read side only.

Opens the store itself read-only (SQLite `mode=ro`; Postgres `default_transaction_read_only`), and does
NOT construct RE's ProjectRegistry, whose constructor runs migrations: a library that reads must not
be able to change the schema. Every query is a SELECT; the guard below refuses anything else.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator


class OdsUnreadable(Exception):
    """The registry could not be read; the cause is the message."""


def normalize_slug(slug: str) -> str:
    return slug.replace("-", "_").lower()   # the same rule as ProjectRegistry._normalize_slug


def _json_dict(value) -> dict:
    """A stored JSON object (TEXT), or {} when it is absent or not an object."""
    if isinstance(value, dict):
        return value
    try:
        out = json.loads(value) if value else {}
    except (ValueError, TypeError):
        return {}
    return out if isinstance(out, dict) else {}


def _group_snapshots(rows: list[dict]) -> list[dict]:
    """Rows ordered by (surveyed_at DESC, source, ...) -> `[{"surveyed_at", "source", "tables": [...]}]`."""
    out: list[dict] = []
    for r in rows:
        at, source = r.pop("surveyed_at"), r.pop("source")
        if not out or (out[-1]["surveyed_at"], out[-1]["source"]) != (at, source):
            out.append({"surveyed_at": at, "source": source, "tables": []})
        out[-1]["tables"].append(r)
    return out


#: The day `database_table_activity.table_kind` began to be written. A row older than this reads the absence words below.
TABLE_KIND_SINCE = "2026-11-02"


def _kind_absence(surveyed_at, column_exists: bool) -> str:
    """What a NULL `table_kind` says: never blank, never assumed to be a base table."""
    if not column_exists:
        return "kind not recorded (this registry has no table_kind column yet)"
    if str(surveyed_at or "")[:10] < TABLE_KIND_SINCE:
        return f"kind not recorded (snapshot before {TABLE_KIND_SINCE})"
    return "kind not recorded for this table"


class OdsReader:
    def __init__(self, url: str):
        self.url = url
        self._is_pg = url.startswith("postgresql")

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        try:
            if self._is_pg:
                import psycopg2
                import psycopg2.extras
                conn = psycopg2.connect(self.url)
                conn.set_session(readonly=True, autocommit=True)
                cur_factory = psycopg2.extras.RealDictCursor
            else:
                path = self.url.removeprefix("sqlite:///")
                conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                conn.row_factory = sqlite3.Row
                cur_factory = None
        except Exception as exc:  # noqa: BLE001
            raise OdsUnreadable(f"could not open the registry: {exc}") from exc
        try:
            yield conn, cur_factory
        finally:
            conn.close()

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        """Run one SELECT with `?` placeholders. Raises OdsUnreadable when the store cannot answer."""
        if not sql.lstrip().lower().startswith("select"):
            raise ValueError("the ODS reader runs SELECT statements only")
        try:
            with self._conn() as (conn, factory):
                if self._is_pg:
                    cur = conn.cursor(cursor_factory=factory)
                    cur.execute(sql.replace("?", "%s"), params)
                else:
                    cur = conn.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
        except OdsUnreadable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise OdsUnreadable(f"the registry did not answer: {exc}") from exc

    # ── the curation record, as RE writes it ──

    def commit_proofs(self, slug: str) -> list[dict]:
        rows = self.query(
            "SELECT id, node_kind, schema_name, table_name, proof, element_guid, read_at, detail_json "
            "FROM catalogue_commit_proofs WHERE database_slug = ? ORDER BY id", (normalize_slug(slug),))
        for r in rows:
            try:
                r["detail"] = json.loads(r.pop("detail_json") or "{}")
            except (ValueError, TypeError):
                r["detail"] = {}
        return rows

    def catalogue_scope_events(self, slug: str) -> list[dict]:
        return self.query(
            "SELECT id, node_kind, schema_name, table_name, choice, action, source, proposal_rule, changed_at "
            "FROM catalogue_scope_events WHERE database_slug = ? ORDER BY id", (normalize_slug(slug),))

    def resource_scope_events(self, resource_type: str, slug: str) -> list[dict]:
        return self.query(
            "SELECT id, locator, kind, choice, action, source, proposal_rule, changed_at "
            "FROM resource_scope_events WHERE resource_type = ? AND resource_slug = ? ORDER BY id",
            (resource_type, normalize_slug(slug)))

    def outbox_status_counts(self, entity_type: str, slug: str) -> dict[str, list[int]]:
        """status -> outbox row ids, for one resource."""
        out: dict[str, list[int]] = {}
        for r in self.query("SELECT id, status FROM egeria_outbox WHERE entity_type = ? AND entity_slug = ? ORDER BY id",
                            (entity_type, normalize_slug(slug))):
            out.setdefault(r["status"], []).append(r["id"])
        return out

    # ── row_count_snapshot results, as RE stores them ──

    def row_count_snapshots(self, slug: str, limit: int = 2) -> list[dict]:
        """The latest `limit` row_count_snapshot results for a database, newest first:
        `[{"surveyed_at", "source", "tables": [{schema_name, table_name, table_type, row_count, state}]}]`.

        A snapshot is one survey's `database_tables` rows (the rows RE's `row_count_snapshot` analysis reads),
        keyed by (surveyed_at, source). A survey RE marked invalid (`database_surveys.invalid_at`, the false-zero
        rule) is not a snapshot. A survey that stored no table rows measured no row counts, so it is not one either.
        """
        s = normalize_slug(slug)
        runs = self.query(
            "SELECT t.surveyed_at AS surveyed_at, t.source AS source FROM database_tables t "
            "WHERE t.database_slug = ? AND NOT EXISTS (SELECT 1 FROM database_surveys d "
            "  WHERE d.database_slug = t.database_slug AND d.surveyed_at = t.surveyed_at "
            "  AND d.source = t.source AND d.invalid_at IS NOT NULL) "
            "GROUP BY t.surveyed_at, t.source ORDER BY t.surveyed_at DESC, t.source LIMIT ?", (s, int(limit)))
        out = []
        for run in runs:
            tables = self.query(
                "SELECT schema_name, table_name, table_type, row_count, state FROM database_tables "
                "WHERE database_slug = ? AND surveyed_at = ? AND source = ? ORDER BY schema_name, table_name",
                (s, run["surveyed_at"], run["source"]))
            out.append({"surveyed_at": run["surveyed_at"], "source": run["source"], "tables": tables})
        return out

    # ── database_table_activity snapshots, as RE stores them ──

    #: The most snapshots one read returns: a step compares the latest few, never a history.
    MAX_SNAPSHOTS = 10

    def table_activity_snapshots(self, slug: str, limit: int = 2) -> list[dict]:
        """The latest `limit` (1..10) `database_table_activity` snapshots for a database, newest first:
        `[{"surveyed_at", "source", "tables": [{schema_name, table_name, rows_inserted, rows_updated, rows_deleted,
        live_tuples, dead_tuples, last_vacuum, last_autovacuum, last_analyze, last_autoanalyze, pending_changes,
        stats_reset, table_kind, state}]}]`.

        `table_kind` is one of base table / view / materialized view / foreign table / partitioned table, as the
        survey stored it. A row that stored none (written before the column, or the type was not read) reads
        "kind not recorded (...)": never blank and never assumed to be a base table.

        A snapshot is one survey's activity rows, keyed by (surveyed_at, source). Only a run that read something is
        one: at least one row carries a counter or a live-row count (a run whose every row is not_collected measured
        nothing, as `db_derived` rules). A survey RE marked invalid (`database_surveys.invalid_at`) is not a
        snapshot. One parameterised statement."""
        s = normalize_slug(slug)
        n = max(1, min(int(limit), self.MAX_SNAPSHOTS))
        has_kind = self._has_column("database_table_activity", "table_kind")
        rows = self.query(
            "SELECT a.surveyed_at AS surveyed_at, a.source AS source, a.schema_name AS schema_name, "
            "a.table_name AS table_name, a.rows_inserted AS rows_inserted, a.rows_updated AS rows_updated, "
            "a.rows_deleted AS rows_deleted, a.live_tuples AS live_tuples, a.dead_tuples AS dead_tuples, "
            "a.last_vacuum AS last_vacuum, a.last_autovacuum AS last_autovacuum, a.last_analyze AS last_analyze, "
            "a.last_autoanalyze AS last_autoanalyze, a.pending_changes AS pending_changes, "
            "a.stats_reset AS stats_reset, " + ("a.table_kind" if has_kind else "NULL") + " AS table_kind, "
            "a.state AS state "
            "FROM database_table_activity a JOIN ("
            "  SELECT x.surveyed_at AS surveyed_at, x.source AS source FROM database_table_activity x "
            "  WHERE x.database_slug = ? AND (x.rows_inserted IS NOT NULL OR x.rows_updated IS NOT NULL "
            "    OR x.rows_deleted IS NOT NULL OR x.seq_scan IS NOT NULL OR x.idx_scan IS NOT NULL "
            "    OR x.live_tuples IS NOT NULL) "
            "  AND NOT EXISTS (SELECT 1 FROM database_surveys d WHERE d.database_slug = x.database_slug "
            "    AND d.surveyed_at = x.surveyed_at AND d.source = x.source AND d.invalid_at IS NOT NULL) "
            "  GROUP BY x.surveyed_at, x.source ORDER BY x.surveyed_at DESC, x.source LIMIT ?"
            ") s ON a.surveyed_at = s.surveyed_at AND a.source = s.source "
            "WHERE a.database_slug = ? ORDER BY a.surveyed_at DESC, a.source, a.schema_name, a.table_name",
            (s, n, s))
        snapshots = _group_snapshots(rows)
        for snap in snapshots:
            for t in snap["tables"]:
                t["table_kind"] = t["table_kind"] or _kind_absence(snap["surveyed_at"], has_kind)
        return snapshots

    def _has_column(self, table: str, column: str) -> bool:
        """Does the registry have this column? A registry that predates an additive column is read, not migrated.
        Asked by selecting it (no rows): the SQLite reader's authorizer allows nothing but reading, and a failed
        read on either backend leaves nothing behind."""
        try:
            self.query(f"SELECT {column} FROM {table} WHERE 1 = 0")
        except Exception:  # noqa: BLE001 - the host's reader raises the driver's own error, this one OdsUnreadable
            return False
        return True

    # ── keys captured per table, as RE stores them ──

    def table_keys(self, slug: str) -> dict | None:
        """The keys the latest column survey captured, per table, or None when no survey stored columns:
        `{"surveyed_at", "source", "tables": [{schema_name, table_name, table_type, keys_captured,
        foreign_keys: [{column, to_schema, to_table, to_column}]}]}`.

        `keys_captured` is the per-table rule `db_derived` uses (`keys_captured_for_table`): at least one of the
        table's columns has `is_primary_key` stated (0 or 1); a native survey leaves it NULL, and then "no keys" is
        not established. The snapshot is the newest (surveyed_at, source) of `database_columns` not marked invalid;
        `table_type` comes from the same run's `database_tables` row ("" when that run stored none). One
        parameterised statement."""
        s = normalize_slug(slug)
        rows = self.query(
            "SELECT c.surveyed_at AS surveyed_at, c.source AS source, c.schema_name AS schema_name, "
            "c.table_name AS table_name, c.column_name AS column_name, c.is_primary_key AS is_primary_key, "
            "c.foreign_key_json AS foreign_key_json, t.table_type AS table_type "
            "FROM database_columns c JOIN ("
            "  SELECT x.surveyed_at AS surveyed_at, x.source AS source FROM database_columns x "
            "  WHERE x.database_slug = ? "
            "  AND NOT EXISTS (SELECT 1 FROM database_surveys d WHERE d.database_slug = x.database_slug "
            "    AND d.surveyed_at = x.surveyed_at AND d.source = x.source AND d.invalid_at IS NOT NULL) "
            "  GROUP BY x.surveyed_at, x.source ORDER BY x.surveyed_at DESC, x.source LIMIT 1"
            ") s ON c.surveyed_at = s.surveyed_at AND c.source = s.source "
            "LEFT JOIN database_tables t ON t.database_slug = c.database_slug AND t.surveyed_at = c.surveyed_at "
            "  AND t.source = c.source AND t.schema_name = c.schema_name AND t.table_name = c.table_name "
            "WHERE c.database_slug = ? ORDER BY c.schema_name, c.table_name, c.column_name",
            (s, s))
        if not rows:
            return None
        tables: dict[tuple[str, str], dict] = {}
        for r in rows:
            key = (r["schema_name"], r["table_name"])
            t = tables.setdefault(key, {"schema_name": key[0], "table_name": key[1],
                                        "table_type": r.get("table_type") or "", "keys_captured": False,
                                        "foreign_keys": []})
            if r.get("is_primary_key") is not None:
                t["keys_captured"] = True
            fk = _json_dict(r.get("foreign_key_json"))
            if fk.get("foreign_table"):
                t["foreign_keys"].append({"column": r["column_name"],
                                          "to_schema": fk.get("foreign_schema") or key[0],
                                          "to_table": fk["foreign_table"],
                                          "to_column": fk.get("foreign_column") or ""})
        return {"surveyed_at": rows[0]["surveyed_at"], "source": rows[0]["source"], "tables": list(tables.values())}

    # ── the schema inventory, as RE stores it (schema_comparison, CMP1) ──

    def schema_inventory(self, slug: str) -> dict:
        """The tables and columns the latest column survey stored for a database:
        `{"registered", "surveyed_at", "source", "tables": [{schema_name, table_name, table_type,
        columns: [{column_name, ordinal_position, data_type, column_size, is_nullable, is_primary_key}]}]}`.

        `registered` is whether RE knows the database at all; `surveyed_at` is None (and `tables` empty) when no
        survey stored its columns, so "not surveyed" and "surveyed, no tables" are never the same answer. The snapshot
        is the newest (surveyed_at, source) of `database_columns` not marked invalid, as `table_keys` reads it; a table
        that run stored in `database_tables` with no column row is listed with no columns. `is_nullable` and
        `is_primary_key` are None where the survey left them unstated."""
        s = normalize_slug(slug)
        registered = bool(self.query("SELECT 1 AS one FROM databases WHERE slug = ?", (s,)))
        runs = self.query(
            "SELECT x.surveyed_at AS surveyed_at, x.source AS source FROM database_columns x "
            "WHERE x.database_slug = ? "
            "AND NOT EXISTS (SELECT 1 FROM database_surveys d WHERE d.database_slug = x.database_slug "
            "  AND d.surveyed_at = x.surveyed_at AND d.source = x.source AND d.invalid_at IS NOT NULL) "
            "GROUP BY x.surveyed_at, x.source ORDER BY x.surveyed_at DESC, x.source LIMIT 1", (s,))
        if not runs:
            return {"registered": registered, "surveyed_at": None, "source": None, "tables": []}
        at, source = runs[0]["surveyed_at"], runs[0]["source"]
        tables: dict[tuple[str, str], dict] = {}
        for t in self.query(
                "SELECT schema_name, table_name, table_type FROM database_tables "
                "WHERE database_slug = ? AND surveyed_at = ? AND source = ? ORDER BY schema_name, table_name",
                (s, at, source)):
            tables[(t["schema_name"], t["table_name"])] = {**t, "table_type": t.get("table_type") or "",
                                                            "columns": []}
        for c in self.query(
                "SELECT schema_name, table_name, column_name, ordinal_position, data_type, column_size, is_nullable, "
                "is_primary_key FROM database_columns WHERE database_slug = ? AND surveyed_at = ? AND source = ? "
                "ORDER BY schema_name, table_name, ordinal_position, column_name", (s, at, source)):
            key = (c.pop("schema_name"), c.pop("table_name"))
            t = tables.setdefault(key, {"schema_name": key[0], "table_name": key[1], "table_type": "", "columns": []})
            t["columns"].append(c)
        return {"registered": registered, "surveyed_at": at, "source": source,
                "tables": [tables[k] for k in sorted(tables)]}

    # ── a file system's walk rows (Files build step 1), as RE stores them ──

    #: RE keeps the newest five walk runs per file system (`walk_extensions.WALK_RUNS_KEPT`); a step reads at most that.
    MAX_WALK_RUNS = 5

    #: How a stored walk says it stopped at a limit. RE stores `survey_data` as `json.dumps` text, so a capped walk's
    #: limits read `"capped": true`. A LIKE on that token is the one test both SQLite and Postgres answer the same way
    #: without parsing a blob that can hold an inventory of 100,000 files (a test pins the token to the writer).
    CAPPED_TOKEN = '%"capped": true%'

    def filesystem_walk_runs(self, slug: str, limit: int = 2) -> list[dict]:
        """The latest `limit` (1..5) walk runs that kept per-file rows for a file system, newest first:
        `[{"run_id", "led_by", "files", "bytes", "hashed", "with_mode", "with_access_time", "capped"}]`.

        `run_id` is the walk's own time. `capped` is 1 when the walk stopped at a limit, 0 when it walked to the end,
        and None when no stored walk row matches the run (cannot tell). `with_mode` counts rows whose stat gave a mode;
        `with_access_time` rows that carry an access time (a walk before that column existed carries none). Two
        parameterised statements."""
        s = normalize_slug(slug)
        n = max(1, min(int(limit), self.MAX_WALK_RUNS))
        runs = self.query(
            "SELECT run_id, MAX(led_by) AS led_by, COUNT(*) AS files, COALESCE(SUM(size_bytes), 0) AS bytes, "
            "SUM(CASE WHEN hash_state = 'hashed' THEN 1 ELSE 0 END) AS hashed, "
            "SUM(CASE WHEN mode_octal <> '' THEN 1 ELSE 0 END) AS with_mode, "
            "SUM(CASE WHEN accessed_at IS NOT NULL AND accessed_at <> '' THEN 1 ELSE 0 END) AS with_access_time "
            "FROM filesystem_walk_files WHERE filesystem_slug = ? GROUP BY run_id ORDER BY run_id DESC LIMIT ?",
            (s, n))
        if not runs:
            return []
        marks = ", ".join("?" for _ in runs)
        capped: dict[str, int] = {}
        for r in self.query(
                "SELECT surveyed_at, MAX(CASE WHEN survey_data LIKE ? THEN 1 ELSE 0 END) AS capped "
                f"FROM filesystem_surveys WHERE filesystem_slug = ? AND surveyed_at IN ({marks}) GROUP BY surveyed_at",
                (self.CAPPED_TOKEN, s, *[r["run_id"] for r in runs])):
            capped[r["surveyed_at"]] = int(r["capped"])
        for r in runs:
            r["capped"] = capped.get(r["run_id"])
        return runs

    def filesystem_walk_files(self, slug: str, run_id: str) -> list[dict]:
        """One walk run's per-file rows, by path: `[{file_path, folder_path, folder_depth, size_bytes, modified_at,
        accessed_at, owner_name, group_name, mode_octal, world_readable, content_hash, hash_state}]`. Names, sizes,
        times, owner, mode and a hash only: no content. One parameterised statement."""
        return self.query(
            "SELECT file_path, folder_path, folder_depth, size_bytes, modified_at, accessed_at, owner_name, group_name, "
            "mode_octal, world_readable, content_hash, hash_state FROM filesystem_walk_files "
            "WHERE filesystem_slug = ? AND run_id = ? ORDER BY file_path", (normalize_slug(slug), str(run_id)))

    def filesystem_walk_not_walked(self, slug: str, run_id: str) -> dict | None:
        """The directories one walk run did not enter, by noise name (`{".git": 3, "node_modules": 12}`), from the stored
        walk row whose time is the run's. `{}` when the walk recorded the count and skipped nothing; None when no stored
        walk row for the run records it (a walk before the count was kept: cannot tell). Names of noise directories
        only, never a path. One parameterised statement."""
        for r in self.query(
                "SELECT survey_data FROM filesystem_surveys WHERE filesystem_slug = ? AND surveyed_at = ?",
                (normalize_slug(slug), str(run_id))):
            limits = _json_dict(_json_dict(r.get("survey_data")).get("walk_limits"))
            by_name = limits.get("not_walked_by_name")
            if isinstance(by_name, dict):
                return {str(k): int(v) for k, v in by_name.items() if isinstance(v, int)}
        return None

    def filesystem_hash_matches(self, slug: str, run_id: str) -> dict:
        """What the OTHER registered file systems hold of this run's content, by (hash, size), from each one's newest
        walk run: `{"peers": [{filesystem_slug, run_id, hashed}], "matches": [{filesystem_slug, content_hash,
        size_bytes, files}]}`. `peers` is every other file system with walk rows, so "no other file system has hashes"
        (nothing to compare) is told apart from "compared and found nothing". Empty files are never matched. Two
        parameterised statements."""
        s = normalize_slug(slug)
        newest = ("SELECT filesystem_slug, MAX(run_id) AS run_id FROM filesystem_walk_files "
                  "WHERE filesystem_slug <> ? GROUP BY filesystem_slug")
        peers = self.query(
            "SELECT o.filesystem_slug AS filesystem_slug, o.run_id AS run_id, "
            "SUM(CASE WHEN o.hash_state = 'hashed' THEN 1 ELSE 0 END) AS hashed "
            f"FROM filesystem_walk_files o JOIN ({newest}) n "
            "ON o.filesystem_slug = n.filesystem_slug AND o.run_id = n.run_id "
            "GROUP BY o.filesystem_slug, o.run_id ORDER BY o.filesystem_slug", (s,))
        matches = self.query(
            "SELECT o.filesystem_slug AS filesystem_slug, o.content_hash AS content_hash, o.size_bytes AS size_bytes, "
            "COUNT(*) AS files "
            f"FROM filesystem_walk_files o JOIN ({newest}) n "
            "ON o.filesystem_slug = n.filesystem_slug AND o.run_id = n.run_id "
            "WHERE o.hash_state = 'hashed' AND o.size_bytes > 0 AND EXISTS (SELECT 1 FROM filesystem_walk_files m "
            "  WHERE m.filesystem_slug = ? AND m.run_id = ? AND m.hash_state = 'hashed' "
            "  AND m.content_hash = o.content_hash AND m.size_bytes = o.size_bytes) "
            "GROUP BY o.filesystem_slug, o.content_hash, o.size_bytes "
            "ORDER BY o.filesystem_slug, o.content_hash", (s, s, str(run_id)))
        return {"peers": peers, "matches": matches}

    def registry_view(self):
        """A read-only stand-in for ProjectRegistry (needs the optional `re` extra; see registry_view.py)."""
        from trellis_analytics.readers.registry_view import RegistryReadView
        return RegistryReadView(self.url)
