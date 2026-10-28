"""Brief section 3 (project owner, 2026-10-07): dependencies as ONE table with a kind column.

A fixture with both sources yields the two kinds with the right counts; a repository with manifests
and no deployment artifacts shows the runtime state sentence, never an empty section; runtime rows are
PROPOSED until a person confirms them; a confirmed runtime row publishes as an annotation only; and
no word anywhere says "lineage".
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from resource_explorer import dependency_table as dt
from resource_explorer.registry import Project, ProjectRegistry


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="p", display_name="P", github_url="https://github.com/o/p"))
    return r


def _deps(registry, rows):
    with registry._conn() as conn:
        for name, version, eco, src in rows:
            conn.execute(
                "INSERT INTO project_dependencies (project_slug, dep_name, dep_version, dep_type, ecosystem, "
                "source_file, indexed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("p", name, version, "runtime", eco, src, "2026-10-01T00:00:00"))


def _wires(registry, wires, ports=0):
    findings = [{"check_name": "wire", "label": "wire", "detail": {
        "kind": "wire", "source": s, "target": t, "integrationStyle": style, "protocol": proto,
        "evidence": {"path": path, "line": line}}} for s, t, style, proto, path, line in wires]
    findings += [{"check_name": "port", "label": "port", "detail": {
        "kind": "port", "component": "web", "port": str(8000 + i), "evidence": {"path": "docker-compose.yml", "line": i}}}
        for i in range(ports)]
    registry.upsert_finding("p", "architecture_interfaces", findings, surveyed_at="2026-10-01T00:00:00")


WIRES = [("web", "db", "compose depends_on", "", "deploy/docker-compose.yml", 12),
         ("web", "cache", "compose environment reference", "redis", "deploy/docker-compose.yml", 20)]


def test_both_sources_yield_the_two_kinds_with_the_right_counts(registry):
    _deps(registry, [("fastapi", "0.110", "python", "pyproject.toml"), ("uvicorn", "0.30", "python", "pyproject.toml"),
                     ("left-pad", "1.0", "javascript", "ui/package.json")])
    _wires(registry, WIRES)
    t = dt.build_table(registry, "p")
    assert t["heading"] == "Dependencies · by kind"
    # `web -> cache (redis)` is a connection string to a data store: the third kind, read from the same wires.
    assert t["counts"] == {"build-time": 3, "runtime": 1, "data": 1}
    assert t["summary"] == "3 build-time · 1 runtime · 1 data"
    assert t["runtime_state"] == ""
    assert {r["kind"] for r in t["rows"]} == {"build-time", "runtime", "data"}
    assert t["kinds"] == ["build-time", "runtime", "data"]    # a new kind is a new value, not a new section


def test_build_time_rows_are_measured_from_their_manifest(registry):
    _deps(registry, [("fastapi", "0.110", "python", "svc/pyproject.toml")])
    row = dt.build_table(registry, "p")["rows"][0]
    # two ends: the repository requires the package (a manifest in `svc/` owned by no admitted component)
    assert (row["dependent"], row["relation"], row["target_name"], row["target_version"]) == ("P", "requires", "fastapi", "0.110")
    assert (row["source"], row["evidence"], row["state"]) == ("svc/pyproject.toml", "svc/pyproject.toml", "measured")
    assert row["state_words"] == "measured · from pyproject.toml"


def test_runtime_rows_are_proposed_from_their_artifact_until_confirmed(registry):
    _wires(registry, WIRES)
    rows = {r["target"]: r for r in dt.build_table(registry, "p")["rows"]}
    assert rows["db"]["state_words"] == "proposed · from docker-compose.yml"
    assert rows["cache"]["state"] == "proposed" and rows["cache"]["source"] == "deploy/docker-compose.yml:20"
    assert rows["cache"]["protocol"] == "redis" and rows["cache"]["kind"] == "data"
    assert rows["db"]["name"] == "web"


def test_manifests_and_no_artifacts_say_why_never_an_empty_section(registry):
    _deps(registry, [("fastapi", "0.110", "python", "pyproject.toml")])
    t = dt.build_table(registry, "p")
    assert t["counts"] == {"build-time": 1, "runtime": 0, "data": 0}
    assert t["runtime_state"] == "runtime not surveyed"             # the architecture step never ran here
    registry.upsert_finding("p", "architecture_recovery", [{"check_name": "component", "label": "x", "detail": {}}],
                            surveyed_at="2026-10-01T00:00:00")
    assert dt.build_table(registry, "p")["runtime_state"] == "no deployment artifact found"   # it ran and found none


def test_artifacts_with_no_declared_dependency_still_say_so(registry):
    _wires(registry, [], ports=2)
    t = dt.build_table(registry, "p")
    assert t["counts"]["runtime"] == 0
    assert t["runtime_state"] == "deployment artifacts found · none declares a dependency between services"


def test_a_person_confirms_and_withdraws_and_the_trail_keeps_both(registry):
    _wires(registry, WIRES)
    key = next(r["key"] for r in dt.build_table(registry, "p")["rows"] if r["target"] == "db")
    assert dt.record_confirmations(registry, "p", [key], "confirmed", "dan") == 1
    row = next(r for r in dt.build_table(registry, "p")["rows"] if r["key"] == key)
    assert row["state"] == "confirmed" and row["state_words"] == "confirmed · by dan · from docker-compose.yml"
    dt.record_confirmations(registry, "p", [key], "withdrawn", "mandy")
    row = next(r for r in dt.build_table(registry, "p")["rows"] if r["key"] == key)
    assert row["state"] == "withdrawn" and row["by"] == "mandy"
    assert [e["verdict"] for e in dt.read_confirmations(registry, "p")[key]] == ["confirmed", "withdrawn"]


def test_only_runtime_rows_of_this_repository_can_be_confirmed(registry):
    _wires(registry, WIRES)
    with pytest.raises(ValueError):
        dt.record_confirmations(registry, "p", ["fastapi@pyproject.toml"], "confirmed", "dan")
    with pytest.raises(ValueError):
        dt.record_confirmations(registry, "p", [], "maybe", "dan")
    with pytest.raises(ValueError):
        dt.record_confirmations(registry, "p", [], "confirmed", "")


def test_only_a_confirmed_row_publishes_and_only_as_an_annotation(registry):
    _wires(registry, WIRES)
    assert dt.runtime_annotations(registry, "p") == []                # proposed publishes nothing
    key = next(r["key"] for r in dt.build_table(registry, "p")["rows"] if r["target"] == "db")
    dt.record_confirmations(registry, "p", [key], "confirmed", "dan")
    anns = dt.runtime_annotations(registry, "p")
    assert len(anns) == 1
    a = anns[0]
    assert type(a).__name__ == "ResourceMeasureAnnotation" and a.check_name == "runtime_dependency"
    assert a.resource_properties["target_name"] == "db" and a.resource_properties["relation"] == "connects_to"
    assert a.json_properties["confirmed_by"] == "dan"
    dt.record_confirmations(registry, "p", [key], "withdrawn", "dan")
    assert dt.runtime_annotations(registry, "p") == []


def test_the_publish_sends_the_confirmed_runtime_rows_beside_the_kept_survey(registry, monkeypatch):
    from resource_explorer import repo_publish as rp
    from resource_explorer.surveyors import survey_snapshot as ss
    from resource_explorer.surveyors.survey_report import ClassificationAnnotation
    _wires(registry, WIRES)
    key = next(r["key"] for r in dt.build_table(registry, "p")["rows"] if r["target"] == "db")
    dt.record_confirmations(registry, "p", [key], "confirmed", "dan")
    ss.record_step(registry, "p", "repo_language", "2026-10-07T01:00:00",
                   [ClassificationAnnotation(summary="s", analysis_step="repo_language", check_name="c")])
    sent = []

    class Pub:
        report_reused = False
        def __init__(self, registry=None, **k): pass
        def publish(self, result, **k):
            sent.append(result)
            registry.set_egeria_asset_guid("p", "a")
            return "r1"
        def get_survey_reports_by_guid(self, g):
            return [{"guid": "r1", "qualified_name": "x", "annotation_count": len(sent[-1].annotations)}]

    monkeypatch.setattr("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher", Pub)
    out = rp.publish_report(registry, "p", "dan", without_project=True)
    assert out["ok"]
    checks = sorted(a.check_name for a in sent[0].annotations)
    assert checks == ["c", "runtime_dependency"]
    proof = [p for p in registry.list_catalogue_commit_proofs("p") if p["proof"] == "report_published"][-1]
    assert proof["detail"]["annotation_count"] == 2


def test_no_word_in_the_table_says_lineage(registry):
    _deps(registry, [("fastapi", "0.110", "python", "pyproject.toml")])
    _wires(registry, WIRES)
    key = next(r["key"] for r in dt.build_table(registry, "p")["rows"] if r["target"] == "db")
    dt.record_confirmations(registry, "p", [key], "confirmed", "dan")
    text = json.dumps(dt.build_table(registry, "p")) + json.dumps([vars(a) for a in dt.runtime_annotations(registry, "p")], default=str)
    assert "lineage" not in text.lower()


# ── the routes ─────────────────────────────────────────────────────────────

def test_the_routes_read_the_table_and_record_a_confirmation_as_the_signed_in_person(registry, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setattr("resource_explorer.auth.get_current_user", lambda request: {"user_id": "dan"})
    from resource_explorer.web.app import app
    client = TestClient(app)
    _wires(registry, WIRES)
    t = client.get("/api/projects/p/dependencies").json()
    assert t["counts"]["runtime"] + t["counts"]["data"] == 2 and t["heading"] == "Dependencies · by kind"
    key = next(r["key"] for r in t["rows"] if r["target"] == "db")
    out = client.post("/api/projects/p/dependencies/confirm", json={"keys": [key], "verdict": "confirmed"})
    assert out.status_code == 200
    assert next(r for r in out.json()["rows"] if r["key"] == key)["state_words"].startswith("confirmed · by dan")
    assert client.post("/api/projects/p/dependencies/confirm", json={"keys": ["nope"]}).status_code == 400
    assert client.get("/api/projects/nope/dependencies").status_code == 404
    monkeypatch.setattr("resource_explorer.auth.get_current_user", lambda request: None)
    assert client.post("/api/projects/p/dependencies/confirm", json={"keys": [key]}).status_code == 401
