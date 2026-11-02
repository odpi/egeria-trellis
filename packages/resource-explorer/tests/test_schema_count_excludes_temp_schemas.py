"""F46: per-backend temporary schemas (`pg_temp_N`, `pg_toast_temp_N`) must not
reach the schema count. coco_pharma's first Scouting said "1644 schema(s)":
30 user + 3 system + 807 `pg_temp_*` + 807 `pg_toast_temp_*`.

No live Postgres: a fake `execute_query` evaluates the exclusion the queries
state against a five-schema fixture, so a query that stops excluding the
temporary schemas fails here with the wrong count.
"""
from __future__ import annotations

from resource_explorer.surveyors.database.connection import PostgreSQLConnection

FIXTURE = ["pg_temp_3", "pg_toast_temp_3", "pg_catalog", "public", "sales"]


def _kept(query: str, names: list[str]) -> list[str]:
    """Apply the query's own NOT IN / left() exclusions to `names`."""
    out = []
    for n in names:
        if n in ("pg_catalog", "information_schema", "pg_toast"):
            continue
        if "left(n.nspname, 8) <> 'pg_temp_'" in query or "left(schema_name, 8) <> 'pg_temp_'" in query:
            if n[:8] == "pg_temp_":
                continue
        if "'pg_toast_temp_'" in query and n[:14] == "pg_toast_temp_":
            continue
        out.append(n)
    return out


class _Conn(PostgreSQLConnection):
    def __init__(self):
        super().__init__(host="localhost", port=5432, database="x", user="u", password="p")
        self.queries: list[str] = []

    def execute_query(self, query, params=()):
        self.queries.append(query)
        if "FROM information_schema.schemata" in query:
            return [{"schema_name": n} for n in _kept(query, FIXTURE)]
        if "has_schema_privilege(current_user, n.nspname, 'USAGE')" in query:
            return [{"schema_name": n, "usage_granted": True} for n in _kept(query, FIXTURE)]
        if "obj_description(n.oid, 'pg_namespace')" in query:
            return [{"schema_name": n, "description": None} for n in _kept(query, FIXTURE)]
        return []


def test_enumerate_relations_counts_two_schemas():
    schemas, _ = _Conn()._enumerate_relations()
    assert sorted(s["schema_name"] for s in schemas) == ["public", "sales"]


def test_schemata_and_description_queries_drop_temp_schemas():
    conn = _Conn()
    info = conn.get_schema_info()
    assert [s["name"] for s in info["schemas"]] == ["public", "sales"]
    assert list(conn._get_schema_descriptions()) == ["public", "sales"]


def test_no_percent_in_filter_so_psycopg_placeholders_stay_safe():
    conn = _Conn()
    conn.get_schema_info()
    assert not any("%" in q for q in conn.queries if "pg_temp_" in q)
