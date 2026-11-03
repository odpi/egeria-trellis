"""The kind of a surveyed relation, in the words PostgreSQL gives (`information_schema.tables.table_type` / `pg_class.relkind`).

Stored as `database_table_activity.table_kind` (additive, nullable). NULL is never "a base table": a row written
before the column existed, or whose type the survey did not read, is NULL, and the typed read says so in words
(`trellis_analytics_re.ods`).
"""
from __future__ import annotations

BASE_TABLE, VIEW, MATERIALIZED_VIEW = "base table", "view", "materialized view"
FOREIGN_TABLE, PARTITIONED_TABLE = "foreign table", "partitioned table"
TABLE_KINDS = (BASE_TABLE, VIEW, MATERIALIZED_VIEW, FOREIGN_TABLE, PARTITIONED_TABLE)

_BY_TYPE = {
    "BASE TABLE": BASE_TABLE, "VIEW": VIEW, "MATERIALIZED VIEW": MATERIALIZED_VIEW,
    "FOREIGN": FOREIGN_TABLE, "FOREIGN TABLE": FOREIGN_TABLE, "PARTITIONED TABLE": PARTITIONED_TABLE,
}
_BY_RELKIND = {"r": BASE_TABLE, "v": VIEW, "m": MATERIALIZED_VIEW, "f": FOREIGN_TABLE, "p": PARTITIONED_TABLE}


def table_kind_of(table_type: str | None = None, relkind: str | None = None) -> str | None:
    """The kind word for a survey's table type (or relkind), or None when it is empty or not one of the five."""
    if relkind in _BY_RELKIND:
        return _BY_RELKIND[relkind]
    return _BY_TYPE.get((table_type or "").strip().upper())
