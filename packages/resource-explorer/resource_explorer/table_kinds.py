"""The stored `table_type` vocabulary for tables, in one place.

PostgreSQL relkind `r` is "BASE TABLE"; relkind `p` (a partitioned parent) is
"PARTITIONED TABLE". A partitioned parent is still a table for listing,
publishing, scope and counting; only the statistics currency sets it apart
(autovacuum never analyzes a parent). Ask `is_table_type()` for "a table of
either kind" instead of comparing against "BASE TABLE".
"""
from __future__ import annotations

BASE_TABLE = "BASE TABLE"
PARTITIONED_TABLE = "PARTITIONED TABLE"
TABLE_TYPES = frozenset({BASE_TABLE, PARTITIONED_TABLE})


def is_table_type(table_type) -> bool:
    """True for a plain table or a partitioned parent (not a view, matview or foreign table)."""
    return str(table_type or "").upper().replace("_", " ") in TABLE_TYPES
