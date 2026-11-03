import sqlite3
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def entry_point_dist(tmp_path, monkeypatch):
    """A dist-info built from the package's own pyproject entry-points, so the declared entry point is what's tested."""
    eps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["entry-points"]["pyegeria.analytics"]
    d = tmp_path / "trellis_analytics_fixture-0.1.0.dist-info"
    d.mkdir()
    (d / "METADATA").write_text("Metadata-Version: 2.1\nName: trellis-analytics-fixture\nVersion: 0.1.0\n")
    (d / "entry_points.txt").write_text(
        "[pyegeria.analytics]\n" + "".join(f"{k} = {v}\n" for k, v in eps.items()))
    monkeypatch.syspath_prepend(str(tmp_path))
    return tmp_path


@pytest.fixture
def make_registry(tmp_path):
    """A real ProjectRegistry over a scratch SQLite file (never the shared registry: the constructor migrates).
    Returns (registry, url). Resource Explorer must be installed: a skip here once hid 17 tests."""
    from resource_explorer.registry import DatabaseEntity, ProjectRegistry

    def build(slug="demo", name="ods"):
        db = str(tmp_path / f"{name}.sqlite")
        reg = ProjectRegistry(db_path=db)
        assert reg.database_url == f"sqlite:///{db}" and str(tmp_path) in reg.database_url
        reg.register_database(DatabaseEntity(
            slug=slug, display_name=slug, db_type="postgresql", host="localhost", port=5442,
            database_name=slug, egeria_asset_guid="abcdef12-0000-0000-0000-000000000000"))
        return reg, reg.database_url
    return build


def write_local_survey(reg, slug, schemas, at="2026-10-01T00:00:00Z", tables=("t",)):
    """The local survey rows (database_tables), one table per name in `tables` for each schema."""
    reg.write_detail_rows("database_tables", slug, at, rows=[
        {"schema_name": s, "table_name": t, "table_type": "BASE TABLE", "row_count": 10, "column_count": 1}
        for s in schemas for t in tables])


def write_native_survey(reg, slug, schemas, tables_per=2, at="2026-10-04T06:00:00", guid="rep-1", extra=()):
    """A stored, complete Egeria native survey (step_runs + native_survey_annotations) naming `schemas`."""
    import json
    from resource_explorer.catalog_and_survey import DATABASE_SURVEY_QN
    proc = DATABASE_SURVEY_QN
    def ann(n, kind, props):
        return {"guid": f"a-{n}", "annotation_type": kind, "summary": f"{kind} {n}", "analysis_step": "x",
                "explanation": "", "confidence": 100, "detail": {"json_properties": json.dumps(props)}}
    anns, n = [ann(0, "Capture Database Measurements", {})], 0
    for sname in schemas:
        anns.append(ann(n := n + 1, "Capture Database Schema Measurements",
                        {"schemaName": sname, "qualifiedSchemaName": f"{slug}.{sname}", "tableCount": str(tables_per)}))
        for j in range(tables_per):
            anns.append(ann(n := n + 1, "Capture Database Table Measurements",
                            {"tableName": f"t{j}", "qualifiedTableName": f"{slug}.{sname}.t{j}", "tableSize": "8192",
                             "tableType": "BASE TABLE"}))
    anns += list(extra)
    reg.record_native_survey_submission("database", slug, proc, at, engine_action_guid="ea-1")
    reg.record_native_survey_report("ea-1", entity_type="database", slug=slug, process_qualified_name=proc,
                                    report_guid=guid, report_at=at, read_at=at, annotations=anns)


def add_curation_rows(url, slug, proofs=(), events=(), outbox=()):
    """Curation rows in the real tables. proofs: (node_kind, schema, proof, read_at, detail_json);
    events: (schema, choice, source); outbox: statuses."""
    c = sqlite3.connect(url.removeprefix("sqlite:///"))
    for p in proofs:
        c.execute("INSERT INTO catalogue_commit_proofs (database_slug,node_kind,schema_name,table_name,proof,element_guid,read_at,detail_json)"
                  " VALUES (?,?,?,?,?,?,?,?)", (slug, p[0], p[1], "", p[2], "g", p[3], p[4]))
    for e in events:
        c.execute("INSERT INTO catalogue_scope_events (database_slug,node_kind,schema_name,table_name,choice,action,source,proposal_rule,author,changed_at)"
                  " VALUES (?,?,?,?,?,?,?,?,?,?)", (slug, "schema", e[0], "", e[1], "set", e[2], "", "tester", "2026-10-02T00:00:00Z"))
    for st in outbox:
        c.execute("INSERT INTO egeria_outbox (entity_type, entity_slug, element_kind, qualified_name, payload_json, status, created_at)"
                  " VALUES ('database',?,'x','q','{}',?,'2026-10-02T00:00:00Z')", (slug, st))
    c.commit()
    c.close()


@pytest.fixture
def make_ods(make_registry):
    """A scratch registry holding the given local survey and curation rows; returns its url."""
    def build(slug="demo", schemas=(), proofs=(), events=(), outbox=()):
        reg, url = make_registry(slug)
        if schemas:
            write_local_survey(reg, slug, schemas)
        add_curation_rows(url, slug, proofs, events, outbox)
        return url
    return build
