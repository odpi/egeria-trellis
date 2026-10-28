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
        stats_reset, state}]}]`.

        A snapshot is one survey's activity rows, keyed by (surveyed_at, source). Only a run that read something is
        one: at least one row carries a counter or a live-row count (a run whose every row is not_collected measured
        nothing, as `db_derived` rules). A survey RE marked invalid (`database_surveys.invalid_at`) is not a
        snapshot. One parameterised statement."""
        s = normalize_slug(slug)
        n = max(1, min(int(limit), self.MAX_SNAPSHOTS))
        rows = self.query(
            "SELECT a.surveyed_at AS surveyed_at, a.source AS source, a.schema_name AS schema_name, "
            "a.table_name AS table_name, a.rows_inserted AS rows_inserted, a.rows_updated AS rows_updated, "
            "a.rows_deleted AS rows_deleted, a.live_tuples AS live_tuples, a.dead_tuples AS dead_tuples, "
            "a.last_vacuum AS last_vacuum, a.last_autovacuum AS last_autovacuum, a.last_analyze AS last_analyze, "
            "a.last_autoanalyze AS last_autoanalyze, a.pending_changes AS pending_changes, "
            "a.stats_reset AS stats_reset, a.state AS state "
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
        return _group_snapshots(rows)

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

    def registry_view(self):
        """A read-only stand-in for ProjectRegistry (needs the optional `re` extra; see registry_view.py)."""
        from trellis_analytics.readers.registry_view import RegistryReadView
        return RegistryReadView(self.url)
