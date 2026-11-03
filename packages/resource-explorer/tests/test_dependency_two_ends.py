"""DESIGN-DEPENDENCY-ROW-TWO-ENDS.md: a dependency has two ends and a kind.

"You show a source but not a target, so they are not a wire." Every row names the dependent, the relation,
the typed target, the kind, the evidence and a state; a confirmed row publishes ONE ResourceMeasureAnnotation
carrying all of it; and the diagram's wires are read from the same rows, so the two cannot disagree.

Synthetic fixtures in tmp_path; temp SQLite; no Egeria (the publisher is a recording fake).
"""
from __future__ import annotations

import json
import os
import subprocess

import pytest

from resource_explorer import dependency_table as dt
from resource_explorer import node_admission
from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors.sub_surveyors.arch_recovery_detect import ArchDetectSurveyor

SURVEYED = "2026-10-07T00:00:00"


def _write(root, rel, content=""):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _git(root):
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=T", "-c", "commit.gpgsign=false",
                    "commit", "-q", "-m", "x"], cwd=root, check=True)


@pytest.fixture
def registry(tmp_path_factory):
    return ProjectRegistry(db_path=str(tmp_path_factory.mktemp("db") / "t.db"))


def _survey(registry, slug, root):
    project = Project(slug=slug, display_name=slug, github_url=f"https://github.com/o/{slug}")
    registry.add(project)
    ArchDetectSurveyor(project, registry, local_path=root, surveyed_at=SURVEYED).run()
    return project


WORKSPACES = """services:
  egeria-platform:
    image: ${EGERIA_REPO:-odpi}/egeria-platform:${EGERIA_VERSION:-latest}
    depends_on: [kafka, postgres]
    ports: ["9443:9443"]
  kafka:
    image: apache/kafka:3.7.0
  postgres:
    image: postgres:16
  jupyter:
    image: quay.io/jupyter/base-notebook:latest
    environment:
      - EGERIA_URL=https://egeria-platform:9443
      - CACHE_URL=redis://kafka:6379
"""


def _workspaces(tmp_path):
    root = str(tmp_path / "workspaces")
    _write(root, "compose-configs/egeria-quickstart.yaml", WORKSPACES)
    _write(root, "README.md", "# workspaces\n")
    _git(root)
    return root


MIXED = """services:
  api:
    build: ./api
    depends_on: [kafka]
  worker:
    build: ./worker
    depends_on: [api, kafka]
  kafka:
    image: apache/kafka:3.7.0
"""


def _mixed(tmp_path, prod=False):
    root = str(tmp_path / "mixed")
    _write(root, "docker/compose.yaml", MIXED)
    _write(root, "docker/api/Dockerfile", "FROM python:3.12\n")
    _write(root, "docker/api/start.sh", "#!/bin/sh\n")
    _write(root, "docker/worker/Dockerfile", "FROM python:3.12\n")
    _write(root, "docker/worker/start.sh", "#!/bin/sh\n")
    if prod:       # the same api -> kafka link, declared again in a second artifact
        _write(root, "docker/compose.prod.yaml", "services:\n  api:\n    build: ./api\n    depends_on: [kafka]\n"
                                                  "  kafka:\n    image: apache/kafka:3.7.0\n")
    _git(root)
    return root


def _rows(registry, slug):
    return dt.build_table(registry, slug)["rows"]


def _row(registry, slug, dependent, relation, target):
    hits = [r for r in _rows(registry, slug)
            if r["dependent"] == dependent and r["relation"] == relation and r["target_name"] == target]
    assert len(hits) == 1, [(r["dependent"], r["relation"], r["target_name"]) for r in _rows(registry, slug)]
    return hits[0]


# ── the row has two ends, a relation, a typed target, a kind, evidence and a state ──────────

def test_a_compose_image_is_a_runs_row_with_both_ends(registry, tmp_path):
    _survey(registry, "ws", _workspaces(tmp_path))
    r = _row(registry, "ws", "egeria-platform", "runs", "odpi/egeria-platform")
    assert (r["dependent_type"], r["target_type"], r["kind"]) == ("service", "image", "runtime")
    assert r["evidence_path"] == "compose-configs/egeria-quickstart.yaml"
    assert r["state"] == "proposed"
    assert r["resolution"] == "builder not known"        # said, never guessed


def test_a_depends_on_is_a_connects_to_row_with_the_evidence_line(registry, tmp_path):
    _survey(registry, "ws", _workspaces(tmp_path))
    r = _row(registry, "ws", "egeria-platform", "connects_to", "kafka")
    assert (r["target_type"], r["kind"], r["state"]) == ("service", "runtime", "proposed")
    assert r["evidence_path"] == "compose-configs/egeria-quickstart.yaml" and r["evidence_line"]
    assert r["evidence"] == f"compose-configs/egeria-quickstart.yaml:{r['evidence_line']}"


def test_a_connection_string_to_a_data_store_is_a_data_row_and_does_not_claim_read_or_write(registry, tmp_path):
    _survey(registry, "ws", _workspaces(tmp_path))
    r = _row(registry, "ws", "jupyter", "connects_to", "kafka")
    assert r["kind"] == "data" and r["protocol"] == "redis"
    http = [x for x in _rows(registry, "ws") if x["dependent"] == "jupyter" and x["target_name"] == "egeria-platform"][0]
    assert http["kind"] == "runtime"                      # https is not a data store
    assert not [x for x in _rows(registry, "ws") if x["relation"] in ("reads", "writes")]


def test_the_table_counts_every_kind_and_the_heading_names_the_kind(registry, tmp_path):
    _survey(registry, "ws", _workspaces(tmp_path))
    t = dt.build_table(registry, "ws")
    assert t["heading"] == "Dependencies · by kind" and t["kinds"] == ["build-time", "runtime", "data"]
    assert t["counts"]["data"] == 1 and t["counts"]["runtime"] >= 4
    assert "lineage" not in json.dumps(t).lower()


def test_a_target_image_another_repository_builds_resolves_to_that_resource_and_states_deployed_by(registry, tmp_path):
    egeria = str(tmp_path / "egeria")
    _write(egeria, "docker/compose.yaml",
           "services:\n  platform:\n    image: odpi/egeria-platform:latest\n    build: ./platform\n")
    _write(egeria, "docker/platform/Dockerfile", "FROM eclipse-temurin:21\n")
    _write(egeria, "docker/platform/start.sh", "#!/bin/sh\n")
    _git(egeria)
    _survey(registry, "egeria_git", egeria)
    registry.set_egeria_asset_guid("egeria_git", "guid-egeria-git")
    _survey(registry, "ws", _workspaces(tmp_path))
    r = _row(registry, "ws", "egeria-platform", "runs", "egeria_git")
    assert (r["target_type"], r["target_slug"], r["target_guid"]) == ("resource", "egeria_git", "guid-egeria-git")
    assert r["target_image"] == "odpi/egeria-platform"
    assert r["cross_fact"] == "egeria_git deployed_by ws · stated, not written to Egeria"
    assert r["drawn"] == "cross-blueprint"
    kafka = _row(registry, "ws", "kafka", "runs", "apache/kafka")
    assert kafka["target_type"] == "image" and kafka["cross_fact"] == ""


def test_a_name_shared_by_two_nodes_is_not_established_with_the_reason_and_cannot_be_confirmed(registry):
    registry.add(Project(slug="p", display_name="P", github_url="https://github.com/o/p"))
    registry.upsert_finding("p", node_admission.ADMISSION_KIND, [{
        "check_name": "admission", "label": "referenced", "summary": "2", "confidence": 100,
        "detail": {"referenced": [
            {"scope": "a::db", "name": "db", "slug": "a::db", "image": "postgres", "evidence": "image postgres · from a.yaml", "unit": "a"},
            {"scope": "b::db", "name": "db", "slug": "b::db", "image": "mysql", "evidence": "image mysql · from b.yaml", "unit": "b"},
            {"scope": "a::web", "name": "web", "slug": "a::web", "image": "nginx", "evidence": "image nginx · from a.yaml", "unit": "a"}],
            "left_out": [], "published_images": []}}], surveyed_at=SURVEYED, scope_locator="", supersedes_previous=True)
    registry.upsert_finding("p", "architecture_interfaces", [{"check_name": "wire", "label": "wire", "detail": {
        "kind": "wire", "source": "web", "target": "db", "integrationStyle": "compose depends_on", "protocol": "",
        "evidence": {"path": "a.yaml", "line": 3}}}], surveyed_at=SURVEYED)
    r = _row(registry, "p", "web", "connects_to", "db")
    assert r["state"] == "not established" and "shared by 2 nodes" in r["state_reason"]
    assert r["state_words"].startswith("not established · ")
    assert r["drawn"] == "not drawn"
    with pytest.raises(ValueError):
        dt.record_confirmations(registry, "p", [r["key"]], "confirmed", "dan")


def test_a_build_time_row_names_the_repository_or_the_owning_component_as_its_dependent(registry, tmp_path):
    _survey(registry, "mixed", _mixed(tmp_path))
    with registry._conn() as conn:
        for name, src in (("fastapi", "pyproject.toml"), ("httpx", "docker/api/pyproject.toml")):
            conn.execute(
                "INSERT INTO project_dependencies (project_slug, dep_name, dep_version, dep_type, ecosystem, "
                "source_file, indexed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("mixed", name, "1.0", "runtime", "python", src, "2026-10-01T00:00:00"))
    fast = _row(registry, "mixed", "mixed", "requires", "fastapi")
    assert (fast["dependent_type"], fast["target_type"], fast["kind"], fast["state"]) == ("repository", "package", "build-time", "measured")
    assert fast["target_version"] == "1.0" and fast["evidence"] == "pyproject.toml"
    httpx = [r for r in _rows(registry, "mixed") if r["target_name"] == "httpx"][0]
    assert httpx["dependent_type"] == "component" and httpx["dependent"] == "api" and httpx["dependent_locator"] == "docker/api"


# ── when a row becomes a wire, and the diagram reads the same rows ─────────────────────────────

def test_two_admitted_ends_are_a_wire_an_admitted_and_a_referenced_end_is_a_port_on_the_edge(registry, tmp_path):
    _survey(registry, "mixed", _mixed(tmp_path))
    wire = _row(registry, "mixed", "worker", "connects_to", "api")
    port = _row(registry, "mixed", "api", "connects_to", "kafka")
    assert (wire["drawn"], wire["dependent_type"], wire["target_type"]) == ("wire", "component", "component")
    assert (port["drawn"], port["target_type"]) == ("edge port", "service")
    assert port["drawn_words"] == "a port on the blueprint's edge"


def test_the_diagrams_wire_count_equals_the_tables(registry, tmp_path):
    from resource_explorer.surveyors.repo_survey_definition_adapter import _architecture_diagram_results
    _survey(registry, "mixed", _mixed(tmp_path))
    drawn = [r for r in _rows(registry, "mixed") if r["drawn"] in dt.DIAGRAM_EDGES]
    diagram = _architecture_diagram_results(registry, "mixed")["mermaid"]
    edges = [l for l in diagram.splitlines() if "-->" in l or "<-->" in l]
    assert len(drawn) == len(edges) == 3
    # the referenced-only target is a port on the edge, not an "outside this analysis" box
    assert "outside this analysis" not in diagram and "port on the edge" in diagram


def test_two_referenced_ends_are_an_environment_wire_and_the_environment_route_carries_them(registry, tmp_path):
    _survey(registry, "ws", _workspaces(tmp_path))
    r = _row(registry, "ws", "egeria-platform", "connects_to", "kafka")
    assert r["drawn"] == "environment wire"
    wires = dt.environment_wires(registry, "ws")
    assert {(w["dependent"], w["target_name"]) for w in wires} >= {("egeria-platform", "kafka"), ("egeria-platform", "postgres")}
    assert all("relation" in w and "evidence" in w for w in wires)


# ── a confirmed row publishes one annotation naming the target ─────────────────────────────────

def test_a_confirmed_row_is_one_resource_measure_annotation_with_the_envelope(registry, tmp_path):
    _survey(registry, "mixed", _mixed(tmp_path))
    r = _row(registry, "mixed", "api", "connects_to", "kafka")
    assert dt.runtime_annotations(registry, "mixed") == []
    dt.record_confirmations(registry, "mixed", [r["key"]], "confirmed", "dan")
    (a,) = dt.runtime_annotations(registry, "mixed")
    assert type(a).__name__ == "ResourceMeasureAnnotation" and a.check_name == "runtime_dependency"
    p = a.resource_properties
    assert (p["relation"], p["target_type"], p["target_name"], p["kind"], p["confirmed_by"]) == (
        "connects_to", "service", "kafka", "runtime", "dan")
    assert p["dependent"] == "api" and p["evidence"].startswith("docker/compose.yaml:") and p["confirmed_at"]
    assert a.json_properties["target"] == "kafka" and "lineage" not in json.dumps(vars(a), default=str).lower()


def test_a_resolved_target_publishes_its_guid_and_a_second_publish_reuses_the_qualified_name(registry, tmp_path):
    from resource_explorer.surveyors.survey_report import annotation_qualified_name
    egeria = str(tmp_path / "egeria")
    _write(egeria, "c.yaml", "services:\n  platform:\n    image: odpi/egeria-platform\n    build: ./platform\n")
    _write(egeria, "platform/Dockerfile", "FROM x\n")
    _write(egeria, "platform/s.sh", "x\n")
    _git(egeria)
    _survey(registry, "egeria_git", egeria)
    registry.set_egeria_asset_guid("egeria_git", "guid-1")
    _survey(registry, "ws", _workspaces(tmp_path))
    r = _row(registry, "ws", "egeria-platform", "runs", "egeria_git")
    dt.record_confirmations(registry, "ws", [r["key"]], "confirmed", "dan")
    (a,) = dt.runtime_annotations(registry, "ws")
    assert a.resource_properties["target_guid"] == "guid-1" and a.resource_properties["target_name"] == "egeria_git"
    registry.set_egeria_asset_guid("egeria_git", "guid-2")        # the GUID changes; the identity must not
    (b,) = dt.runtime_annotations(registry, "ws")
    assert annotation_qualified_name("run7", 0, a) == annotation_qualified_name("run7", 0, b)
    assert "guid-1" not in annotation_qualified_name("run7", 0, a)


def test_a_proposed_withdrawn_or_not_established_row_publishes_nothing(registry, tmp_path):
    _survey(registry, "mixed", _mixed(tmp_path))
    key = _row(registry, "mixed", "api", "connects_to", "kafka")["key"]
    dt.record_confirmations(registry, "mixed", [key], "confirmed", "dan")
    dt.record_confirmations(registry, "mixed", [key], "withdrawn", "dan")
    assert dt.runtime_annotations(registry, "mixed") == []


# ── confirmations: one key per entry, written once ─────────────────────────────────────────────

def test_each_confirmation_is_its_own_setting_written_once_and_the_old_single_key_still_reads(registry, tmp_path):
    _survey(registry, "mixed", _mixed(tmp_path))
    rows = {r["target_name"]: r for r in _rows(registry, "mixed") if r["dependent"] == "api"}
    key = rows["kafka"]["key"]
    dt.record_confirmations(registry, "mixed", [key], "confirmed", "dan")
    dt.record_confirmations(registry, "mixed", [key], "withdrawn", "mandy")
    stored = registry.list_settings_with_prefix("repo_dependency_confirmations::")
    assert len(stored) == 2 and all(k.count("::") == 2 for k, _ in stored)
    assert registry.get_setting("repo_dependency_confirmations::mixed") is None
    assert [e["verdict"] for e in dt.read_confirmations(registry, "mixed")[key]] == ["confirmed", "withdrawn"]
    # a confirmation written under the old single key (before this change) is still honoured
    registry.set_setting("repo_dependency_confirmations::old", json.dumps(
        {"worker->api@deploy/compose.yaml": [{"verdict": "confirmed", "by": "dan", "at": "2026-10-07T00:00:00+00:00"}]}))
    assert dt.read_confirmations(registry, "old")["worker->api@deploy/compose.yaml"][0]["by"] == "dan"


# ── the build-time annotation gains the same fields ────────────────────────────────────────────

def test_the_dependency_survey_annotation_carries_relation_target_type_kind_and_evidence(registry):
    from resource_explorer.surveyors.sub_surveyors.dependency import DependencySurveyor
    project = Project(slug="p", display_name="P", github_url="https://github.com/o/p")
    registry.add(project)
    with registry._conn() as conn:
        conn.execute(
            "INSERT INTO project_dependencies (project_slug, dep_name, dep_version, dep_type, ecosystem, "
            "source_file, indexed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("p", "fastapi", "0.110", "runtime", "python", "svc/pyproject.toml", "2026-10-01T00:00:00"))
    out = DependencySurveyor(project, registry).run()
    cls = next(a for a in out if a.check_name == "dependencies_by_ecosystem")
    d = cls.json_properties["dependencies"][0]
    assert d["relation"] == "requires" and d["target_type"] == "package" and d["kind"] == "build-time"
    assert d["evidence"] == "svc/pyproject.toml" and d["dependent"] == "svc" and d["name"] == "fastapi"


def test_the_environment_route_carries_the_nodes_and_their_wires(registry, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    from resource_explorer.web.app import app
    _survey(registry, "ws", _workspaces(tmp_path))
    out = TestClient(app).get("/api/projects/ws/environment-blueprint").json()
    assert len(out["nodes"]) == 4
    assert {(w["dependent"], w["target_name"]) for w in out["wires"]} >= {("egeria-platform", "kafka")}


def test_the_qualified_name_carries_the_dependent_locator_and_survives_a_guid_change(registry, tmp_path):
    from resource_explorer.surveyors.survey_report import annotation_qualified_name
    _survey(registry, "mixed", _mixed(tmp_path))
    r = _row(registry, "mixed", "api", "connects_to", "kafka")
    assert r["dependent_locator"]
    dt.record_confirmations(registry, "mixed", [r["key"]], "confirmed", "dan")
    (a,) = dt.runtime_annotations(registry, "mixed")
    qn = annotation_qualified_name("run1", 0, a)
    assert r["dependent_locator"] in qn, qn
    # accepting the component later gives it a GUID; the name must not change (a re-homing is a move)
    registry.record_materialized_component("repo", "mixed", r["dependent_locator"], "qn", "comp-guid")
    registry.record_component_verdict("repo", "mixed", r["dependent_locator"], "accepted", decided_by="dan")
    assert _row(registry, "mixed", "api", "connects_to", "kafka")["dependent_guid"] == "comp-guid"
    (b,) = dt.runtime_annotations(registry, "mixed")
    assert annotation_qualified_name("run1", 0, b) == qn


def test_the_header_says_what_the_table_does_not_read(registry, tmp_path):
    _survey(registry, "mixed", _mixed(tmp_path))
    t = dt.build_table(registry, "mixed")
    assert t["not_derived"] == ("module-to-module requires not yet derived · "
                                "reads/writes, endpoints and host:port not derived")


# ── review fixes ───────────────────────────────────────────────────────────────────────────────

def _mixed_two_artifacts(tmp_path):
    return _mixed(tmp_path, prod=True)


def test_one_link_in_two_artifacts_is_one_row_with_all_its_evidence(registry, tmp_path):
    _survey(registry, "mixed", _mixed_two_artifacts(tmp_path))
    hits = [r for r in _rows(registry, "mixed")
            if (r["dependent"], r["relation"], r["target_name"]) == ("api", "connects_to", "kafka")]
    assert len(hits) == 1
    r = hits[0]
    assert sorted(e["path"] for e in r["evidences"]) == ["docker/compose.prod.yaml", "docker/compose.yaml"]
    assert all(e["line"] for e in r["evidences"]) and "docker/compose.prod.yaml" in r["evidence"] and len(r["keys"]) == 2


def test_confirming_the_collapsed_row_once_publishes_one_annotation_without_the_refusal(registry, tmp_path):
    from resource_explorer.surveyors.survey_report import assert_unique_qualified_names
    _survey(registry, "mixed", _mixed_two_artifacts(tmp_path))
    r = next(x for x in _rows(registry, "mixed") if (x["dependent"], x["target_name"]) == ("api", "kafka"))
    # either member key is accepted; one confirmation covers the link
    dt.record_confirmations(registry, "mixed", [r["keys"][0], r["keys"][1]], "confirmed", "dan")
    anns = dt.runtime_annotations(registry, "mixed")
    assert len(anns) == 1
    assert_unique_qualified_names("run1", anns)
    ev = json.loads(anns[0].resource_properties["evidence_list"]) if isinstance(
        anns[0].resource_properties["evidence_list"], str) else anns[0].resource_properties["evidence_list"]
    assert sorted(e["path"] for e in ev) == ["docker/compose.prod.yaml", "docker/compose.yaml"]


def test_a_link_confirmed_under_the_old_per_evidence_key_still_reads_confirmed(registry, tmp_path):
    _survey(registry, "mixed", _mixed_two_artifacts(tmp_path))
    r = next(x for x in _rows(registry, "mixed") if (x["dependent"], x["target_name"]) == ("api", "kafka"))
    old = "api->kafka@docker/compose.prod.yaml"
    assert old in r["keys"]
    registry.set_setting("repo_dependency_confirmations::mixed", json.dumps(
        {old: [{"verdict": "confirmed", "by": "dan", "at": "2026-10-07T00:00:00+00:00"}]}))
    r = next(x for x in _rows(registry, "mixed") if (x["dependent"], x["target_name"]) == ("api", "kafka"))
    assert r["state"] == "confirmed" and r["by"] == "dan"
    assert len(dt.runtime_annotations(registry, "mixed")) == 1
    # a later withdrawal under the other key wins by order, not by which key it was recorded under
    other = next(k for k in r["keys"] if k != old)
    dt.record_confirmations(registry, "mixed", [other], "withdrawn", "mandy")
    r = next(x for x in _rows(registry, "mixed") if (x["dependent"], x["target_name"]) == ("api", "kafka"))
    assert r["state"] == "withdrawn" and r["by"] == "mandy"


def test_the_whole_request_builds_the_nodes_and_reads_the_recovery_and_the_wires_once(registry, tmp_path, monkeypatch):
    from resource_explorer.surveyors import repo_survey_definition_adapter as adapter
    _survey(registry, "mixed", _mixed_two_artifacts(tmp_path))
    calls = {"recovery": 0, "interfaces": 0}
    real = adapter._architecture_recovery_results
    monkeypatch.setattr(adapter, "_architecture_recovery_results",
                        lambda *a, **k: (calls.__setitem__("recovery", calls["recovery"] + 1), real(*a, **k))[1])
    real_q = registry.query_findings
    monkeypatch.setattr(registry, "query_findings", lambda slug, kind, *a, **k: (
        calls.__setitem__("interfaces", calls["interfaces"] + (kind == "architecture_interfaces")), real_q(slug, kind, *a, **k))[1])
    dt.build_table(registry, "mixed")
    # one rebuild of the recovery results (which itself reads the interfaces once) plus this module's ONE read
    assert calls == {"recovery": 1, "interfaces": 2}, calls
    calls.update(recovery=0, interfaces=0)
    key = next(x for x in _rows(registry, "mixed") if x["target_name"] == "kafka" and x["dependent"] == "api")["key"]
    calls.update(recovery=0, interfaces=0)
    ctx = dt.Context(registry, "mixed")
    dt.record_confirmations(registry, "mixed", [key], "confirmed", "dan", ctx=ctx)
    dt.runtime_annotations(registry, "mixed", ctx=ctx)
    dt.environment_wires(registry, "mixed", ctx=ctx)
    dt.build_table(registry, "mixed", ctx=ctx)
    # one rebuild of the recovery results (which itself reads the interfaces once) plus this module's ONE read
    assert calls == {"recovery": 1, "interfaces": 2}, calls


def test_the_confirm_limits_say_a_sentence_and_a_reason_is_stored(registry, tmp_path):
    _survey(registry, "mixed", _mixed(tmp_path))
    key = next(x for x in _rows(registry, "mixed") if x["target_name"] == "kafka" and x["dependent"] == "api")["key"]
    with pytest.raises(ValueError, match="at most 200"):
        dt.record_confirmations(registry, "mixed", [f"k{i}" for i in range(201)], "confirmed", "dan")
    with pytest.raises(ValueError, match="512"):
        dt.record_confirmations(registry, "mixed", ["x" * 513], "confirmed", "dan")
    with pytest.raises(ValueError, match="500"):
        dt.record_confirmations(registry, "mixed", [key], "confirmed", "dan", reason="r" * 501)
    assert dt.record_confirmations(registry, "mixed", [key, key, key], "confirmed", "dan", reason="seen in prod") == 1
    assert len(registry.list_settings_with_prefix("repo_dependency_confirmations::mixed::")) == 1
    r = next(x for x in _rows(registry, "mixed") if x["key"] == key)
    assert r["reason"] == "seen in prod"
    (a,) = dt.runtime_annotations(registry, "mixed")
    assert a.resource_properties["confirm_reason"] == "seen in prod"


def test_a_withdrawn_row_says_it_affects_future_surveys_only(registry, tmp_path):
    _survey(registry, "mixed", _mixed(tmp_path))
    key = next(x for x in _rows(registry, "mixed") if x["target_name"] == "kafka" and x["dependent"] == "api")["key"]
    dt.record_confirmations(registry, "mixed", [key], "withdrawn", "dan")
    r = next(x for x in _rows(registry, "mixed") if x["key"] == key)
    assert "future surveys only" in r["state_words"]


def test_the_confirm_route_checks_401_then_404_then_403_then_400(registry, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from resource_explorer.workflows.curate import CurationDenied
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    user = {"user_id": "dan"}
    monkeypatch.setattr("resource_explorer.auth.get_current_user", lambda request: user)
    from resource_explorer.web.app import app
    client = TestClient(app)
    _survey(registry, "mixed", _mixed(tmp_path))
    key = next(x for x in _rows(registry, "mixed") if x["target_name"] == "kafka" and x["dependent"] == "api")["key"]

    def deny(*a, **k):
        raise CurationDenied("not a curator of this resource")
    monkeypatch.setattr("resource_explorer.web.routes.curate._require_curation_rights", deny)
    url = "/api/projects/mixed/dependencies/confirm"
    assert client.post("/api/projects/nope/dependencies/confirm", json={"keys": [key]}).status_code == 404
    denied = client.post(url, json={"keys": ["bogus"]})
    assert denied.status_code == 403 and "curator" in denied.json()["detail"]      # 403 before the 400 on a bad key
    assert registry.list_settings_with_prefix("repo_dependency_confirmations::mixed::") == []
    monkeypatch.setattr("resource_explorer.web.routes.curate._require_curation_rights", lambda *a, **k: None)
    assert client.post(url, json={"keys": ["bogus"]}).status_code == 400
    assert client.post(url, json={"keys": [key], "reason": "r" * 501}).status_code == 400
    assert client.post(url, json={"keys": [key], "reason": "ok"}).status_code == 200
    user = None
    monkeypatch.setattr("resource_explorer.auth.get_current_user", lambda request: None)
    assert client.post(url, json={"keys": [key]}).status_code == 401
