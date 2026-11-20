"""Thin reader over Resource Explorer's resource_explorer.project_code_symbols
/ project_code_relationships tables, exposing the same public method names/
shapes as advisor/code_symbol_store.py's CodeSymbolStore (the methods
advisor/analytics.py actually calls) — so analytics.py's call sites don't
need to change, only what backs them (AST-ownership-transfer plan Phase 7).

A new class rather than extending CodeSymbolStore itself: CodeSymbolStore is
still EA's own writer for (soon-to-be-deprecated, see plan Phase 8) code_symbols
/code_relationships — overloading it with a "which schema" flag would make one
class serve two unrelated data sources mid-migration. Every returned row is
aliased `project_slug AS collection` so analytics.py's existing formatting
code (which reads `row['collection']`) needs no changes.
"""
from __future__ import annotations

import time
from typing import Any, Optional
from loguru import logger

from advisor.db_consolidated import get_db_manager
from advisor.re_code_scope import scope_clause

_SYMBOLS_TABLE = "resource_explorer.project_code_symbols"


class ReCodeSymbolReader:
    """Read-only — RE owns writes to these tables now (see ingestion pipeline
    in the resource-explorer package). Mirrors CodeSymbolStore's read API."""

    def __init__(self) -> None:
        self.db_manager = get_db_manager()
        self.db_manager.connect()

    def _read(self, sql: str, params: tuple, what: str) -> list:
        """One read, recorded with its time in the request's retrieval log (TR-51 d): a read that returned rows is
        one source (the table and what was asked of it); one that found nothing records its time only."""
        from advisor.agents.tools import lookup_source, record_lookup
        started = time.perf_counter()
        rows = self.db_manager.execute_query(sql, params)
        record_lookup("symbol_store", time.perf_counter() - started,
                      [lookup_source("symbol_store", _SYMBOLS_TABLE, what, f"{len(rows)} rows", kind="aggregate")]
                      if rows else ())
        return rows

    def collection_summary(self, collection: Optional[str] = None) -> dict[str, Any]:
        where, params = scope_clause(collection)
        sql = f"""
            SELECT project_slug, kind, COUNT(*) AS n,
            SUM(end_line - start_line + 1) AS loc
            FROM {_SYMBOLS_TABLE} WHERE {where}
            GROUP BY project_slug, kind
        """
        rows = self._read(sql, tuple(params), f"collection summary for {collection or 'all projects'}")

        _kind_key = {"class": "classes", "function": "functions", "method": "methods"}
        summary: dict[str, Any] = {}
        for r in rows:
            col = r["project_slug"]
            if col not in summary:
                summary[col] = {"classes": 0, "functions": 0, "methods": 0, "loc": 0}
            key = _kind_key.get(r["kind"], r["kind"] + "s")
            summary[col][key] = int(r["n"])
            summary[col]["loc"] = summary[col].get("loc", 0) + int(r["loc"] or 0)
        return summary

    def count_by_kind(self, kind: str, collection: Optional[str] = None, include_private: bool = True) -> int:
        where, params = scope_clause(collection)
        sql = f"SELECT COUNT(*) AS count FROM {_SYMBOLS_TABLE} WHERE kind = %s AND {where}"
        full_params: list = [kind] + params
        if not include_private:
            sql += " AND is_private = 0"
        rows = self._read(sql, tuple(full_params), f"count of {kind}s in {collection or 'all projects'}")
        return rows[0]["count"] if rows else 0

    def list_classes(self, collection: Optional[str] = None, include_private: bool = False) -> list[dict]:
        where, params = scope_clause(collection)
        priv = "" if include_private else " AND is_private = 0"
        sql = f"""
            SELECT name, project_slug AS collection, file_path, start_line,
            end_line - start_line + 1 AS loc, docstring
            FROM {_SYMBOLS_TABLE}
            WHERE kind = 'class' AND {where}{priv}
            ORDER BY name
        """
        return self._read(sql, tuple(params), f"classes in {collection or 'all projects'}")

    def methods_for_class(self, class_name: str, collection: Optional[str] = None, include_private: bool = False) -> list[dict]:
        where, params = scope_clause(collection)
        priv = "" if include_private else " AND is_private = 0"
        sql = f"""
            SELECT name, signature, return_type, is_async, complexity, docstring
            FROM {_SYMBOLS_TABLE}
            WHERE kind = 'method' AND parent_class = %s AND {where}{priv}
            ORDER BY name
        """
        return self._read(sql, tuple([class_name] + params), f"methods of {class_name}")

    def search_symbols(self, name_pattern: str, collection: Optional[str] = None, kind: Optional[str] = None, limit: int = 20) -> list[dict]:
        where, params = scope_clause(collection)
        kind_clause = " AND kind = %s" if kind else ""
        full_params: list = [f"%{name_pattern}%"] + params + ([kind] if kind else []) + [limit]
        sql = f"""
            SELECT project_slug AS collection, kind, name, qualified_name, signature,
            parent_class, start_line, docstring
            FROM {_SYMBOLS_TABLE}
            WHERE name LIKE %s AND {where}{kind_clause}
            ORDER BY project_slug, kind, name
            LIMIT %s
        """
        return self._read(sql, tuple(full_params), f"symbols matching {name_pattern}")

    def most_complex(self, collection: Optional[str] = None, limit: int = 10) -> list[dict]:
        where, params = scope_clause(collection)
        sql = f"""
            SELECT project_slug AS collection, kind, name, parent_class, complexity,
            start_line, end_line - start_line + 1 AS loc
            FROM {_SYMBOLS_TABLE}
            WHERE kind IN ('function','method') AND {where}
            ORDER BY complexity DESC LIMIT %s
        """
        return self._read(sql, tuple(params + [limit]), f"most complex in {collection or 'all projects'}")

    def largest_classes(self, collection: Optional[str] = None, limit: int = 10) -> list[dict]:
        where, params = scope_clause(collection, table_alias="s")
        sql = f"""
            SELECT s.project_slug AS collection, s.name,
            s.end_line - s.start_line + 1 AS loc,
            (SELECT COUNT(*) FROM {_SYMBOLS_TABLE} m
             WHERE m.parent_class = s.name
             AND m.project_slug = s.project_slug
             AND m.kind = 'method') AS method_count
            FROM {_SYMBOLS_TABLE} s
            WHERE s.kind = 'class' AND {where}
            ORDER BY method_count DESC LIMIT %s
        """
        return self._read(sql, tuple(params + [limit]), f"largest classes in {collection or 'all projects'}")


_reader: ReCodeSymbolReader | None = None


def get_re_code_symbol_reader() -> ReCodeSymbolReader:
    global _reader
    if _reader is None:
        try:
            _reader = ReCodeSymbolReader()
        except Exception as exc:
            logger.warning(f"ReCodeSymbolReader init failed: {exc}")
            raise
    return _reader
