"""B1 equivalence proof for value_profile, on real stored profiles, with no write anywhere shared.

Reads the latest value-profile batch (and that batch's frequent values, and the latest proposals) of each slug from the
shared registry on 5442 through a READ-ONLY psycopg2 session (never ProjectRegistry, whose constructor migrates), copies
them into a scratch SQLite registry, stores the typed table result exactly as the step does (`write_typed_rows`, timed),
and compares the native read with the typed read on every surface the brief names.

usage: b1_value_profile_equivalence.py <scratch dir> <slug>... [<pre-B1 analysis_catalog.yaml>]
(the pre-B1 catalogue, e.g. `git show cbab28be:packages/resource-explorer/resource_explorer/configdata/analysis_catalog.yaml`,
turns on the publish-table comparison). REGISTRY_DATABASE_URL must name a scratch SQLite file.
"""
import json
import sys
import time
from pathlib import Path

import psycopg2
import psycopg2.extras
import yaml

PG = "postgresql://egeria_advisor:advisor@localhost:5442/egeria_advisor?options=-csearch_path%3Dresource_explorer"
OLD_CATALOG = next((a for a in sys.argv[2:] if a.endswith(".yaml")), None)


def read_shared(slug):
    conn = psycopg2.connect(PG)
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT MAX(surveyed_at) AS at FROM database_value_profiles WHERE database_slug = %s", (slug,))
    at = cur.fetchone()["at"]
    cur.execute("SELECT * FROM database_value_profiles WHERE database_slug = %s AND surveyed_at = %s", (slug, at))
    profiles = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT * FROM database_value_profile_frequent_values WHERE database_slug = %s AND surveyed_at = %s",
                (slug, at))
    frequent = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT MAX(surveyed_at) AS at FROM database_quality_proposals WHERE database_slug = %s", (slug,))
    pat = cur.fetchone()["at"]
    cur.execute("SELECT * FROM database_quality_proposals WHERE database_slug = %s AND surveyed_at = %s", (slug, pat))
    proposals = [dict(r) for r in cur.fetchall()]
    conn.close()
    return at, profiles, frequent, pat, proposals


def main():
    scratch = Path(sys.argv[1])
    slugs = [a for a in sys.argv[2:] if not a.endswith(".yaml")]
    from resource_explorer import publish_table as pt
    from resource_explorer import table_result
    from resource_explorer import value_profile_screen as screen
    from resource_explorer.declared_context import declarations
    from resource_explorer.facts import FactLayer
    from resource_explorer.registry import _DETAIL_TABLE_SPECS, DatabaseEntity, ProjectRegistry
    from resource_explorer.surveyors import analysis_catalog_reader as catalog
    from resource_explorer.surveyors.database import survey_definition_adapter as sda
    from resource_explorer.surveyors.database.quality import value_profile as vp
    from trellis_analytics.declaration import Declaration

    def keep(table, rows):
        cols = set(_DETAIL_TABLE_SPECS[table].value_columns)
        return [{k: v for k, v in r.items() if k in cols} for r in rows]

    report = {}
    for slug in slugs:
        at, profiles, frequent, pat, proposals = read_shared(slug)
        db = scratch / f"{slug}.db"
        db.unlink(missing_ok=True)
        reg = ProjectRegistry(db_path=str(db))
        reg.register_database(DatabaseEntity(slug=slug, display_name=slug, db_type="postgresql", host="localhost",
                                             port=5432, database_name=slug))
        reg.write_detail_rows("database_value_profiles", slug, at, rows=keep("database_value_profiles", profiles))
        reg.write_detail_rows("database_value_profile_frequent_values", slug, at,
                              rows=keep("database_value_profile_frequent_values", frequent))
        if proposals:
            reg.write_detail_rows("database_quality_proposals", slug, pat,
                                  rows=keep("database_quality_proposals", proposals))
        t0 = time.perf_counter()
        n_typed = vp.write_typed_rows(reg, slug, at)
        write_ms = (time.perf_counter() - t0) * 1000

        real = sda._value_profile_typed_rows
        t0 = time.perf_counter()
        typed = sda._value_profile_results(reg, slug)
        typed_read_ms = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        native = sda._value_profile_native_results(reg, slug)
        native_read_ms = (time.perf_counter() - t0) * 1000

        def both(fn):
            a = fn()
            sda._value_profile_typed_rows = lambda *_: []
            try:
                b = fn()
            finally:
                sda._value_profile_typed_rows = real
            return a, b

        ts, ns = both(lambda: {k: v for k, v in screen.build_summary(reg, slug).items() if k != "table_result"})
        tables = [(t["schema_name"], t["table_name"]) for t in ts["tables"]]
        tt, nt = both(lambda: [screen.build_table(reg, slug, s, t) for s, t in tables])
        th, nh = both(lambda: sda._value_profile_headline(reg, slug))

        dec = declarations("database")["value_profile"]
        shown = table_result.table_rows(reg, "database", slug, "value_profile", dec)
        native_rows = reg.query_detail_rows("database_value_profiles", slug)
        first = [(r["schema"], r["table"], r["column"]) for r in shown["rows"]]
        native_first = [(r["schema_name"], r["table_name"], r["column_name"]) for r in native_rows[:dec.table.max_rows]]
        raw = table_result.stored_rows(reg, "database", slug, "value_profile", dec)[1][:dec.table.max_rows]
        value_mismatch = sum(1 for r, n in zip(raw, native_rows)
                             for c in dec.table.column_names() if c not in dec.table.key_fields and r[c] != n.get(c))

        entry = next(e for e in catalog._load()["database"] if e.id == "value_profile")
        layer = FactLayer(reg, resource_type="database")
        new_rows, _ = both(lambda: pt.build_rows(reg, "database", slug, layer=layer, entries=[entry])["rows"])
        publish = {}
        if OLD_CATALOG:
            raw_old = {e["id"]: e for e in yaml.safe_load(Path(OLD_CATALOG).read_text())["database_analyses"]}
            old_entry = entry.__class__(**{**entry.__dict__,
                                           "declaration": Declaration.model_validate(raw_old["value_profile"]["declaration"])}) \
                if hasattr(entry, "__dict__") else entry
            sda._value_profile_typed_rows = lambda *_: []
            try:
                old_rows = pt.build_rows(reg, "database", slug, layer=FactLayer(reg, resource_type="database"),
                                         entries=[old_entry])["rows"]
            finally:
                sda._value_profile_typed_rows = real
            strip = lambda rows: [{k: v for k, v in r.items() if k != "rows_band"} for r in rows]  # noqa: E731
            publish = {"identical_to_pre_B1_excluding_band": strip(new_rows) == strip(old_rows),
                       "rows": len(new_rows), "band": sorted({r.get("rows_band", "") for r in new_rows}),
                       "values": {r["measure"]: r["value"] for r in new_rows}}

        report[slug] = {
            "batch": at, "native_rows": len(profiles), "typed_rows": n_typed,
            "column_count": [typed["column_count"], native["column_count"]],
            "measured_count": [typed["measured_count"], native["measured_count"]],
            "state_counts": [typed["state_counts"], native["state_counts"]],
            "not_permitted": [typed["state_counts"].get("not_permitted", 0),
                              native["state_counts"].get("not_permitted", 0)],
            "nulls_total": [sum(c.get("null_value_ct") or 0 for c in typed["columns"]),
                            sum(c.get("null_value_ct") or 0 for c in native["columns"])],
            "results_identical": typed == native,
            "summary_identical": ts == ns,
            "tables": len(tables), "table_cards_identical": tt == nt,
            "questions_row": [th["label"], nh["label"]], "questions_row_identical": th == nh,
            "shown": shown["shown"], "total": shown["total"], "truncation": shown["truncation"],
            "first_rows_identical_in_order": first == native_first,
            "first_rows_value_mismatches": value_mismatch,
            "publish": publish,
            "write_typed_rows_ms": round(write_ms, 1),
            "typed_read_ms": round(typed_read_ms, 1), "native_read_ms": round(native_read_ms, 1),
        }
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
