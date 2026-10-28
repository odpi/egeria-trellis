"""DESIGN-BLUEPRINT-NODE-ADMISSION.md: a node enters a blueprint by the evidence class of its boundary.

* a workspaces-style repository (compose files that run OTHER projects' images) admits 0 components, lists
  every service as a runtime dependency row, says the zero-component sentence, and is offered the
  Environment Deployment Blueprint;
* an egeria_git-style repository (Gradle modules, a built platform image, a published image, Kafka and
  Postgres alongside) admits its own components and no Kafka or Postgres node;
* the class is on each node; a person's reclassification with a reason is recorded and moves the node.

Synthetic fixtures in tmp_path; temp SQLite; nothing live.
"""
from __future__ import annotations

import os
import subprocess

import pytest

from resource_explorer import blueprint_kinds, dependency_table, node_admission
from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors.arch_recovery import admission as adm
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


WORKSPACES_COMPOSE = """services:
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
"""


def _workspaces_repo(tmp_path):
    root = str(tmp_path / "workspaces")
    _write(root, "compose-configs/egeria-quickstart.yaml", WORKSPACES_COMPOSE)
    _write(root, "README.md", "# workspaces\n")
    _git(root)
    return root


EGERIA_COMPOSE = """services:
  platform:
    image: odpi/egeria-platform:latest
    build:
      context: ./platform
    depends_on: [kafka, postgres]
  ui:
    image: odpi/egeria-ui:latest
  worker:
    build: ./worker
  kafka:
    image: apache/kafka:3.7.0
  postgres:
    image: postgres:16
"""


def _egeria_repo(tmp_path):
    root = str(tmp_path / "egeria")
    _write(root, "settings.gradle", "rootProject.name = 'egeria'\ninclude 'open-metadata-implementation:core'\n"
                                    "include 'open-metadata-implementation:admin'\n")
    _write(root, "open-metadata-implementation/core/build.gradle", "plugins { id 'java' }\n")
    _write(root, "open-metadata-implementation/admin/build.gradle", "plugins { id 'java' }\n")
    _write(root, "docker/compose.yaml", EGERIA_COMPOSE)
    _write(root, "docker/platform/Dockerfile", "FROM eclipse-temurin:21\nCMD [\"java\", \"-jar\", \"platform.jar\"]\n")
    _write(root, "docker/platform/start.sh", "#!/bin/sh\n")
    _write(root, "docker/worker/Dockerfile", "FROM alpine\n")
    # the UI image is published by CI, not built from a compose context
    _write(root, ".github/workflows/publish.yml",
           "jobs:\n  publish:\n    steps:\n      - uses: docker/build-push-action@v5\n"
           "        with:\n          tags: odpi/egeria-ui:latest\n")
    _git(root)
    return root


@pytest.fixture
def registry(tmp_path_factory):
    return ProjectRegistry(db_path=str(tmp_path_factory.mktemp("db") / "t.db"))


def _survey(registry, slug, root, name=None):
    project = Project(slug=slug, display_name=name or slug, github_url=f"https://github.com/o/{slug}")
    registry.add(project)
    ArchDetectSurveyor(project, registry, local_path=root, surveyed_at=SURVEYED).run()
    return project


# ── the pure rule ───────────────────────────────────────────────────────

def test_images_are_compared_by_name_not_tag_registry_or_variable():
    assert adm.normalise_image("${R:-odpi}/egeria-platform:${V:-latest}") == "odpi/egeria-platform"
    assert adm.normalise_image("ghcr.io/odpi/egeria-platform@sha256:abc") == "odpi/egeria-platform"
    assert adm.same_image("ghcr.io/odpi/egeria-platform:1", "odpi/egeria-platform")
    assert not adm.same_image("egeria-platform", "odpi/egeria-platform:1")      # namespaces must agree
    assert not adm.same_image("postgres", "odpi/egeria-platform")


def test_a_build_context_is_built_an_unpublished_image_is_referenced_a_published_one_shipped():
    pub = {"odpi/egeria-ui": "ci.yml:3"}
    assert adm.classify_compose_service({"image": "x/y", "build": "./y"}, "c.yaml", pub)[0] == adm.BUILT
    assert adm.classify_compose_service({"image": "odpi/egeria-ui:1", "build": ""}, "c.yaml", pub)[0] == adm.SHIPPED
    cls, why = adm.classify_compose_service({"image": "postgres:16", "build": ""}, "c.yaml", pub)
    assert (cls, why) == (adm.REFERENCED, "image postgres · from c.yaml")


# ── a workspaces-style repository ───────────────────────────────────────

class TestWorkspacesStyle:
    def test_admits_no_component_and_says_why(self, registry, tmp_path):
        _survey(registry, "workspaces", _workspaces_repo(tmp_path))
        assert registry.query_finding_scopes("workspaces", "architecture_recovery", check_name="component") == []
        s = node_admission.summary(registry, "workspaces")
        assert s["admitted"] == 0 and s["referenced"] == 4
        assert s["sentence"] == ("this repository deploys other software and builds none of its own: "
                                 "see Dependencies · runtime")
        assert s["left_out"][0] == "4 services referenced only · listed as runtime dependencies"

    def test_every_service_is_a_runtime_dependency_row_with_its_compose_source(self, registry, tmp_path):
        _survey(registry, "workspaces", _workspaces_repo(tmp_path))
        t = dependency_table.build_table(registry, "workspaces")
        refs = {r["name"]: r for r in t["rows"] if r["style"] == "referenced only"}
        assert set(refs) == {"egeria-platform", "kafka", "postgres", "jupyter"}
        assert refs["kafka"]["target"] == "apache/kafka"
        assert refs["egeria-platform"]["target"] == "odpi/egeria-platform"
        assert refs["kafka"]["state"] == "proposed"
        assert "compose-configs/egeria-quickstart.yaml" in refs["kafka"]["state_words"]
        assert t["deploys_only"].startswith("this repository deploys other software")
        assert t["counts"]["runtime"] >= 4

    def test_the_environment_deployment_blueprint_is_offered_and_only_then(self, registry, tmp_path):
        _survey(registry, "workspaces", _workspaces_repo(tmp_path))
        env = node_admission.environment(registry, "workspaces")
        assert len(env["nodes"]) == 4 and env["linked"] == 0
        assert {n["words"] for n in env["nodes"]} == {"builder not known"}
        kinds = blueprint_kinds.blueprint_kind_rows(
            label="Workspaces", blueprints=[], artifact_count=1, build_files=[], logical_unconfirmed=None,
            environment_services=4, environment_linked=0)
        row = next(k for k in kinds if k["kind"] == "environment")
        assert row["name"] == "Workspaces Environment Deployment Blueprint"
        assert row["state"] == "proposed" and "4 services run other projects' images" in row["source"]
        none = blueprint_kinds.blueprint_kind_rows(
            label="X", blueprints=[], artifact_count=0, build_files=[], logical_unconfirmed=None)
        assert [k["kind"] for k in none] == ["deployment", "build", "logical"]

    def test_the_environment_links_a_service_to_the_repository_that_builds_its_image(self, registry, tmp_path):
        _survey(registry, "egeria_git", _egeria_repo(tmp_path))
        _survey(registry, "workspaces", _workspaces_repo(tmp_path))
        env = node_admission.environment(registry, "workspaces")
        by = {n["name"]: n for n in env["nodes"]}
        assert by["egeria-platform"]["built_by"] == "egeria_git"
        assert by["egeria-platform"]["words"] == "built by egeria_git"
        assert by["kafka"]["built_by"] == "" and env["linked"] == 1


# ── an egeria_git-style repository ──────────────────────────────────────

class TestEgeriaStyle:
    def test_no_kafka_or_postgres_node_and_its_own_components_stay(self, registry, tmp_path):
        _survey(registry, "egeria_git", _egeria_repo(tmp_path))
        from resource_explorer.surveyors.repo_survey_definition_adapter import _architecture_recovery_results
        comps = {c["name"]: c for c in _architecture_recovery_results(registry, "egeria_git", max_depth=None)["components"]}
        names = " ".join(comps)
        assert "kafka" not in names and "postgres" not in names
        assert comps["platform"]["admission"] == "built_here"
        assert comps["ui"]["admission"] == "shipped_here"
        assert comps["ui"]["admission_evidence"].startswith("image odpi/egeria-ui · published by")
        assert any("open-metadata-implementation" in n for n in comps)        # Gradle modules, built here
        s = node_admission.summary(registry, "egeria_git")
        assert s["referenced"] == 2 and s["sentence"] == ""
        assert s["counts"]["shipped_here"] == 1 and s["counts"]["built_here"] >= 4

    def test_the_class_is_on_each_leaf(self, registry, tmp_path):
        from resource_explorer.component_tree import leaves
        _survey(registry, "egeria_git", _egeria_repo(tmp_path))
        classes = {l["name"]: (l["admission"], l["admission_evidence"]) for b in
                   ("docker", "docker::worker", "docker::ui", "open-metadata-implementation") for l in leaves(registry, "egeria_git", b)}
        assert classes["platform"][0] == "built_here" and classes["platform"][1] == "from docker/platform/Dockerfile"
        assert classes["worker"] == ("built_here", "from docker/compose.yaml · build ./worker")
        assert classes["ui"][0] == "shipped_here"

    def test_the_manifest_lists_what_was_found_and_not_admitted(self, registry, tmp_path):
        _survey(registry, "egeria_git", _egeria_repo(tmp_path))
        left = node_admission.summary(registry, "egeria_git")["left_out"]
        assert "2 services referenced only · listed as runtime dependencies" in left

    def test_the_environment_blueprint_is_offered_to_egeria_git_with_its_two_referenced_services(self, registry, tmp_path):
        _survey(registry, "egeria_git", _egeria_repo(tmp_path))
        assert len(node_admission.environment(registry, "egeria_git")["nodes"]) == 2


# ── a person reclassifies ───────────────────────────────────────────────

class TestReclassify:
    def test_a_reason_is_required_and_the_scope_must_be_a_node(self, registry, tmp_path):
        _survey(registry, "workspaces", _workspaces_repo(tmp_path))
        scope = node_admission.referenced_rows(registry, "workspaces")[0]["scope"]
        with pytest.raises(ValueError, match="reason"):
            node_admission.reclassify(registry, "workspaces", scope, "built_here", "  ", "dan")
        with pytest.raises(ValueError, match="not a node"):
            node_admission.reclassify(registry, "workspaces", "nope", "built_here", "r", "dan")
        with pytest.raises(ValueError, match="built_here or referenced_only"):
            node_admission.reclassify(registry, "workspaces", scope, "shipped_here", "r", "dan")

    def test_referenced_to_built_moves_it_out_of_the_table_and_into_the_tree_with_the_reason(self, registry, tmp_path):
        from resource_explorer.component_tree import leaves
        _survey(registry, "workspaces", _workspaces_repo(tmp_path))
        row = next(r for r in node_admission.referenced_rows(registry, "workspaces") if r["name"] == "jupyter")
        entry = node_admission.reclassify(registry, "workspaces", row["scope"], "built_here",
                                          "we maintain this notebook image", "dan")
        assert entry["by"] == "dan" and entry["reason"] == "we maintain this notebook image"
        names = {r["name"] for r in dependency_table.build_table(registry, "workspaces")["rows"]
                 if r["style"] == "referenced only"}
        assert names == {"egeria-platform", "kafka", "postgres"}
        leaf = [l for l in leaves(registry, "workspaces", row["scope"]) if l["path"] == row["scope"]][0]
        assert leaf["admission"] == "built_here"
        assert "reclassified by dan · we maintain this notebook image" == leaf["admission_evidence"]
        assert leaf["reclassified"]["reason"] == "we maintain this notebook image"
        assert node_admission.summary(registry, "workspaces")["admitted"] == 1

    def test_built_to_referenced_moves_a_component_into_the_table(self, registry, tmp_path):
        from resource_explorer.component_tree import leaves
        _survey(registry, "egeria_git", _egeria_repo(tmp_path))
        scope = next(l["path"] for l in leaves(registry, "egeria_git", "docker") if l["name"] == "platform")
        node_admission.reclassify(registry, "egeria_git", scope, "referenced_only", "vendored build we do not own", "dan")
        assert all(l["name"] != "platform" for l in leaves(registry, "egeria_git", "docker"))
        rows = {r["name"]: r for r in dependency_table.build_table(registry, "egeria_git")["rows"]
                if r["style"] == "referenced only"}
        assert "platform" in rows and "reclassified by dan: vendored build we do not own" in rows["platform"]["state_words"]

    def test_the_next_survey_honours_the_trail(self, registry, tmp_path):
        root = _workspaces_repo(tmp_path)
        project = _survey(registry, "workspaces", root)
        row = next(r for r in node_admission.referenced_rows(registry, "workspaces") if r["name"] == "jupyter")
        node_admission.reclassify(registry, "workspaces", row["scope"], "built_here", "ours", "dan")
        ArchDetectSurveyor(project, registry, local_path=root, surveyed_at="2026-10-08T00:00:00").run()
        scopes = registry.query_finding_scopes("workspaces", "architecture_recovery", check_name="component")
        assert any("jupyter" in s for s in scopes)
        assert node_admission.summary(registry, "workspaces")["referenced"] == 3

    def test_a_confirmed_referenced_row_publishes_as_an_annotation_only(self, registry, tmp_path):
        _survey(registry, "workspaces", _workspaces_repo(tmp_path))
        key = next(r["key"] for r in dependency_table.build_table(registry, "workspaces")["rows"]
                   if r["style"] == "referenced only" and r["name"] == "kafka")
        dependency_table.record_confirmations(registry, "workspaces", [key], "confirmed", "dan")
        ann = dependency_table.runtime_annotations(registry, "workspaces")
        assert [a.json_properties["target"] for a in ann] == ["apache/kafka"]


# ── the routes ──────────────────────────────────────────────────────────

class TestRoutes:
    @pytest.fixture
    def client(self, registry, monkeypatch):
        from fastapi.testclient import TestClient
        monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                            lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
        monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
        monkeypatch.setattr("resource_explorer.auth.get_current_user", lambda request: {"user_id": "dan"})
        monkeypatch.setattr("resource_explorer.web.routes.curate.get_current_user", lambda request: {"user_id": "dan"})
        monkeypatch.setattr("resource_explorer.web.routes.curate._authorize_curation", lambda *a, **k: None)
        from resource_explorer.web.app import app
        return TestClient(app)

    def test_the_selector_offers_the_environment_kind_only_when_services_are_referenced(self, client, registry, tmp_path):
        _survey(registry, "workspaces", _workspaces_repo(tmp_path))
        out = client.get("/api/projects/workspaces/components/blueprints").json()
        assert [k["kind"] for k in out["kinds"]] == ["deployment", "build", "logical", "environment"]
        assert out["admission"]["sentence"].startswith("this repository deploys other software")
        env = client.get("/api/projects/workspaces/environment-blueprint").json()
        assert len(env["nodes"]) == 4

    def test_a_repository_with_nothing_referenced_is_not_offered_it(self, client, registry):
        registry.add(Project(slug="p", display_name="P", github_url="https://github.com/o/p"))
        out = client.get("/api/projects/p/components/blueprints").json()
        assert "environment" not in [k["kind"] for k in out["kinds"]]

    def test_reclassify_records_the_person_and_the_reason_and_refuses_no_reason(self, client, registry, tmp_path):
        _survey(registry, "workspaces", _workspaces_repo(tmp_path))
        scope = next(r["scope"] for r in node_admission.referenced_rows(registry, "workspaces") if r["name"] == "jupyter")
        bad = client.post("/api/projects/workspaces/components/reclassify",
                          json={"scope_locator": scope, "to": "built_here", "reason": ""})
        assert bad.status_code == 400
        ok = client.post("/api/projects/workspaces/components/reclassify",
                         json={"scope_locator": scope, "to": "built_here", "reason": "ours"})
        assert ok.status_code == 200 and ok.json()["reclassified"]["by"] == "dan"
        assert node_admission.read_reclassifications(registry, "workspaces")[scope][-1]["reason"] == "ours"


# ── review fixes: storage, corruption, caps, malformed entries, retries, keys ─────────

class TestReclassifyStorage:
    def _scope(self, registry, tmp_path):
        _survey(registry, "workspaces", _workspaces_repo(tmp_path))
        return [r["scope"] for r in node_admission.referenced_rows(registry, "workspaces")]

    def test_two_concurrent_reclassifies_both_survive(self, registry, tmp_path):
        import threading
        scopes = self._scope(registry, tmp_path)[:2]
        barrier = threading.Barrier(2)
        errors = []

        def go(scope):
            try:
                barrier.wait()
                node_admission.reclassify(registry, "workspaces", scope, "built_here", f"reason {scope}", "dan")
            except Exception as exc:               # pragma: no cover - the failure being tested for
                errors.append(exc)

        ts = [threading.Thread(target=go, args=(s,)) for s in scopes]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert not errors
        trail = node_admission.read_reclassifications(registry, "workspaces")
        assert set(trail) == set(scopes) and all(len(v) == 1 for v in trail.values())

    def test_two_entries_for_one_scope_keep_both_in_order(self, registry, tmp_path):
        scope = self._scope(registry, tmp_path)[0]
        node_admission.reclassify(registry, "workspaces", scope, "built_here", "first", "dan")
        node_admission.reclassify(registry, "workspaces", scope, "referenced_only", "second", "dan")
        trail = node_admission.read_reclassifications(registry, "workspaces")[scope]
        assert [e["reason"] for e in trail] == ["first", "second"]

    def test_a_corrupt_stored_entry_is_never_overwritten_and_is_said(self, registry, tmp_path):
        scope = self._scope(registry, tmp_path)[0]
        registry.set_setting("repo_node_reclassifications::workspaces::00000000000000000001-bad", "{not json")
        node_admission.reclassify(registry, "workspaces", scope, "built_here", "ours", "dan")
        assert registry.get_setting("repo_node_reclassifications::workspaces::00000000000000000001-bad") == "{not json"
        assert node_admission.read_reclassifications(registry, "workspaces")[scope][-1]["reason"] == "ours"
        s = node_admission.summary(registry, "workspaces")
        assert s["unreadable_reclassifications"] == 1
        assert any("unreadable reclassification" in l for l in s["left_out"])

    def test_a_non_object_entry_is_unreadable_not_a_crash(self, registry, tmp_path):
        self._scope(registry, tmp_path)
        registry.set_setting("repo_node_reclassifications::workspaces::00000000000000000002-x", "[1, 2]")
        assert node_admission.summary(registry, "workspaces")["unreadable_reclassifications"] == 1
        assert dependency_table.build_table(registry, "workspaces")["counts"]["runtime"] >= 4

    def test_a_malformed_entry_missing_keys_does_not_break_the_readers(self, registry, tmp_path):
        from resource_explorer.component_tree import leaves
        scope = self._scope(registry, tmp_path)[0]
        registry.set_setting("repo_node_reclassifications::workspaces::00000000000000000003-y",
                             '{"scope": "%s", "to": "built_here"}' % scope)
        assert node_admission.summary(registry, "workspaces")["unreadable_reclassifications"] == 1
        assert len(node_admission.referenced_rows(registry, "workspaces")) == 4
        assert leaves(registry, "workspaces", scope) == []
        ArchDetectSurveyor(Project(slug="workspaces", display_name="w", github_url="https://github.com/o/workspaces"),
                           registry, local_path=_workspaces_repo(tmp_path / "again"), surveyed_at="2026-10-09T00:00:00").run()

    def test_reason_and_scope_are_capped_with_a_sentence(self, registry, tmp_path):
        scope = self._scope(registry, tmp_path)[0]
        with pytest.raises(ValueError, match="500 characters"):
            node_admission.reclassify(registry, "workspaces", scope, "built_here", "x" * 501, "dan")
        with pytest.raises(ValueError, match="1024 characters"):
            node_admission.reclassify(registry, "workspaces", "s" * 1025, "built_here", "r", "dan")


def test_a_retried_step_with_the_same_surveyed_at_leaves_one_admission_row(registry, tmp_path):
    root = _workspaces_repo(tmp_path)
    project = _survey(registry, "workspaces", root)
    ArchDetectSurveyor(project, registry, local_path=root, surveyed_at=SURVEYED).run()
    rows = [r for r in registry.query_findings("workspaces", "architecture_admission") if r["check_name"] == "admission"]
    assert len(rows) == 1


def test_same_named_services_in_same_named_directories_are_two_nodes_with_two_keys(registry, tmp_path):
    root = str(tmp_path / "twin")
    for side in ("a", "b"):
        _write(root, f"{side}/deploy/compose.yaml", "services:\n  kafka:\n    image: apache/kafka:3\n")
    _git(root)
    _survey(registry, "twin", root)
    rows = node_admission.referenced_rows(registry, "twin")
    assert len(rows) == 2 and len({r["scope"] for r in rows}) == 2
    keys = {r["key"] for r in dependency_table.build_table(registry, "twin")["rows"] if r["style"] == "referenced only"}
    assert len(keys) == 2


def test_the_route_refuses_an_unsigned_caller_and_oversized_input_with_a_sentence(registry, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    _survey(registry, "workspaces", _workspaces_repo(tmp_path))
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    monkeypatch.setattr("resource_explorer.auth.get_current_user", lambda request: {"user_id": "dan"})
    monkeypatch.setattr("resource_explorer.web.routes.curate.get_current_user", lambda request: {"user_id": "dan"})
    monkeypatch.setattr("resource_explorer.web.routes.curate._authorize_curation", lambda *a, **k: None)
    from resource_explorer.web.app import app
    r = TestClient(app).post("/api/projects/workspaces/components/reclassify",
                             json={"scope_locator": "x", "to": "built_here", "reason": "y" * 600})
    assert r.status_code == 400 and "500 characters" in r.json()["detail"]


# ── stable slugs for same-named services; a reclassification belongs to its directory ─────────

def _twin_repo(tmp_path, dirs, name="twin"):
    root = str(tmp_path / name)
    for d in dirs:
        _write(root, f"{d}/compose.yaml", "services:\n  kafka:\n    image: apache/kafka:3\n")
    _git(root)
    return root


def _scopes(registry, slug):
    return sorted(r["scope"] for r in node_admission.referenced_rows(registry, slug))


class TestStableSlugs:
    def test_every_same_named_service_is_qualified_independent_of_order(self, registry, tmp_path):
        _survey(registry, "t1", _twin_repo(tmp_path, ["a/deploy", "b/deploy"], "t1"))
        assert _scopes(registry, "t1") == ["a-deploy::kafka", "b-deploy::kafka"]

    def test_a_unique_name_keeps_the_plain_slug(self, registry, tmp_path):
        _survey(registry, "t2", _twin_repo(tmp_path, ["b/deploy"], "t2"))
        assert _scopes(registry, "t2") == ["deploy::kafka"]

    def test_adding_an_earlier_sorting_sibling_never_re_points_a_reclassification(self, registry, tmp_path):
        root = _twin_repo(tmp_path, ["b/deploy"], "t3")
        project = _survey(registry, "t3", root)
        node_admission.reclassify(registry, "t3", "deploy::kafka", "built_here", "ours", "dan")
        _write(root, "a/deploy/compose.yaml", "services:\n  kafka:\n    image: apache/kafka:3\n")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        ArchDetectSurveyor(project, registry, local_path=root, surveyed_at="2026-10-09T00:00:00").run()
        # neither service is treated as built here by the old entry
        assert _scopes(registry, "t3") == ["a-deploy::kafka", "b-deploy::kafka"]
        assert all(r["reclassified"] is None for r in node_admission.referenced_rows(registry, "t3"))
        left = node_admission.summary(registry, "t3")["left_out"]
        assert any("deploy::kafka" in l and "not applied" in l for l in left)

    def test_deleting_one_of_two_never_re_points_a_reclassification(self, registry, tmp_path):
        root = _twin_repo(tmp_path, ["a/deploy", "b/deploy"], "t4")
        project = _survey(registry, "t4", root)
        node_admission.reclassify(registry, "t4", "b-deploy::kafka", "built_here", "ours", "dan")
        subprocess.run(["git", "rm", "-rq", "a"], cwd=root, check=True)
        ArchDetectSurveyor(project, registry, local_path=root, surveyed_at="2026-10-09T00:00:00").run()
        rows = node_admission.referenced_rows(registry, "t4")
        assert [r["scope"] for r in rows] == ["deploy::kafka"] and rows[0]["reclassified"] is None
        assert any("b-deploy::kafka" in l and "not applied" in l
                   for l in node_admission.summary(registry, "t4")["left_out"])

    def test_a_scoped_run_does_not_change_slugs(self, registry, tmp_path):
        root = _twin_repo(tmp_path, ["a/deploy", "b/deploy"], "t5")
        project = _survey(registry, "t5", root)
        ArchDetectSurveyor(project, registry, scope_locator="a/deploy", local_path=root,
                           surveyed_at="2026-10-09T00:00:00").run()
        scopes = registry.query_finding_scopes("t5", "architecture_recovery", check_name="component")
        assert "deploy::kafka" not in scopes
        assert _scopes(registry, "t5") == ["a-deploy::kafka", "b-deploy::kafka"]

    def test_a_stored_unit_that_no_longer_matches_is_reported_not_applied(self, registry, tmp_path):
        _survey(registry, "t6", _twin_repo(tmp_path, ["b/deploy"], "t6"))
        registry.add_setting_once(
            "repo_node_reclassifications::t6::00000000000000000009-zz",
            '{"scope": "deploy::kafka", "to": "built_here", "reason": "r", "by": "dan", '
            '"at": "2026-10-09T00:00:00+00:00", "unit": "x/elsewhere"}')
        assert all(r["reclassified"] is None for r in node_admission.referenced_rows(registry, "t6"))
        assert any("belongs to x/elsewhere" in l and "not applied" in l
                   for l in node_admission.summary(registry, "t6")["left_out"])

    def test_reclassify_stores_the_unit(self, registry, tmp_path):
        _survey(registry, "t7", _twin_repo(tmp_path, ["b/deploy"], "t7"))
        node_admission.reclassify(registry, "t7", "deploy::kafka", "built_here", "ours", "dan")
        (key, raw), = registry.list_settings_with_prefix("repo_node_reclassifications::t7::")
        assert '"unit": "b/deploy"' in raw


def test_unreadable_entries_are_one_warning_per_read(registry, tmp_path, caplog):
    _survey(registry, "t8", _twin_repo(tmp_path, ["b/deploy"], "t8"))
    for i in range(3):
        registry.set_setting(f"repo_node_reclassifications::t8::0000000000000000000{i}-bad", "nope")
    caplog.clear()
    with caplog.at_level("WARNING"):
        node_admission.read_reclassifications(registry, "t8")
    assert len([r for r in caplog.records if "unreadable" in r.getMessage()]) == 1


def test_a_slug_with_colons_cannot_read_another_slugs_entries(registry, tmp_path):
    _survey(registry, "t9", _twin_repo(tmp_path, ["b/deploy"], "t9"))
    node_admission.reclassify(registry, "t9", "deploy::kafka", "built_here", "ours", "dan")
    assert node_admission.read_reclassifications(registry, "t9::deploy") == {}
    assert node_admission.read_reclassifications(registry, "T9") == {}


# ── Dockerfile twins survive qualification; empty units never apply silently; injective slugs ─────

def _components_named(root, name):
    from resource_explorer.surveyors.arch_recovery import detectors, exclusion
    first = exclusion.scan(root).first_party
    comps, _e, _n, _o = detectors.build_components(root, first, all_files=first)
    return [c for c in comps if c.name == name]


def _twin_with_dockerfile(tmp_path, name, other_unit=None):
    root = str(tmp_path / name)
    _write(root, "a/p/compose.yaml", "services:\n  web:\n    image: odpi/web:1\n    build: ./web\n")
    _write(root, "a/p/web/Dockerfile", "FROM python:3\nCMD [\"python\", \"app.py\"]\n")
    _write(root, "a/p/web/app.py", "print(1)\n")
    if other_unit:
        _write(root, f"{other_unit}/compose.yaml", "services:\n  web:\n    image: apache/web:1\n")
    _git(root)
    return root


@pytest.mark.parametrize("other", [None, "b/p"])
def test_a_dockerfile_and_its_compose_service_stay_one_node_even_when_the_name_is_shared(tmp_path, other):
    root = _twin_with_dockerfile(tmp_path, f"d{other is None}", other)
    in_a = [c for c in _components_named(root, "web") if c.identity.deployment_context in ("a/p", "a/p/web")
            or "a/p" in "".join(c.files)]
    assert len(in_a) == 1
    assert in_a[0].admission == "built_here" and in_a[0].image == "odpi/web"
    if other:
        assert len(_components_named(root, "web")) == 2        # b/p's own service is still its own node


def test_qualified_slugs_are_injective_for_directories_that_differ_only_by_separator(tmp_path):
    root = str(tmp_path / "inj")
    for d in ("a/b-c/deploy", "a-b/c/deploy"):
        _write(root, f"{d}/compose.yaml", "services:\n  kafka:\n    image: apache/kafka:3\n")
    _git(root)
    slugs = [c.slug for c in _components_named(root, "kafka")]
    assert len(slugs) == 2 and len(set(slugs)) == 2
    assert all(s.startswith(("a-b-c-deploy::kafka", "a-b-c-deploy.")) or "::kafka" in s for s in slugs)


def test_non_colliding_qualified_slugs_are_unchanged(tmp_path):
    root = _twin_repo(tmp_path, ["a/deploy", "b/deploy"], "plainq")
    assert sorted(c.slug for c in _components_named(root, "kafka")) == ["a-deploy::kafka", "b-deploy::kafka"]


class TestEmptyUnitEntries:
    def _put(self, registry, slug, scope, unit):
        import json as _j
        registry.add_setting_once(f"repo_node_reclassifications::{slug}::00000000000000000007-u",
                                  _j.dumps({"scope": scope, "to": "built_here", "reason": "r", "by": "dan",
                                            "at": "2026-10-09T00:00:00+00:00", **({"unit": unit} if unit is not None else {})}))

    def test_a_unitless_entry_applies_to_a_plain_slug_with_no_collision(self, registry, tmp_path):
        _survey(registry, "e1", _twin_repo(tmp_path, ["b/deploy"], "e1"))
        self._put(registry, "e1", "deploy::kafka", None)
        assert node_admission.referenced_rows(registry, "e1") == []        # moved to built here

    def test_a_unitless_entry_does_not_apply_to_a_qualified_slug(self, registry, tmp_path):
        _survey(registry, "e2", _twin_repo(tmp_path, ["a/deploy", "b/deploy"], "e2"))
        self._put(registry, "e2", "a-deploy::kafka", None)
        assert len(node_admission.referenced_rows(registry, "e2")) == 2
        assert any("no directory recorded" in l and "not applied" in l
                   for l in node_admission.summary(registry, "e2")["left_out"])

    def test_an_entry_with_a_directory_does_not_apply_to_a_node_with_none(self):
        entry, note = node_admission.effective({"x": [{"to": "built_here", "reason": "r", "by": "d", "at": "t",
                                                       "unit": "a/b"}]}, "x", "")
        assert entry is None and "not applied" in note


# ── twin guard: a compose service merges into a Dockerfile/manifest node only if it BUILDS it ────────

def _dockerfile_repo(tmp_path, name, service_yaml):
    root = str(tmp_path / name)
    _write(root, "X/compose.yaml", "services:\n" + service_yaml)
    _write(root, "X/web/Dockerfile", "FROM python:3\nCMD [\"python\", \"app.py\"]\n")
    _write(root, "X/web/app.py", "print(1)\n")
    _git(root)
    return root


class TestTwinGuard:
    def test_a_foreign_image_service_next_to_a_dockerfile_stays_its_own_referenced_node(self, tmp_path):
        root = _dockerfile_repo(tmp_path, "g1", "  web:\n    image: nginx:1\n")
        web = _components_named(root, "web")
        assert sorted(c.admission for c in web) == ["built_here", "referenced_only"]
        assert len({c.slug for c in web}) == 2                      # never two nodes under one slug
        dockerfile = next(c for c in web if c.admission == "built_here")
        assert dockerfile.image == ""                               # nginx is NOT the Dockerfile's image

    def test_a_service_that_builds_the_directory_merges_and_is_built_here(self, tmp_path):
        root = _dockerfile_repo(tmp_path, "g2", "  web:\n    image: odpi/web:1\n    build: ./web\n")
        web = _components_named(root, "web")
        assert len(web) == 1 and web[0].admission == "built_here" and web[0].image == "odpi/web"

    def test_a_service_building_some_other_directory_does_not_merge(self, tmp_path):
        root = _dockerfile_repo(tmp_path, "g3", "  web:\n    build: ./elsewhere\n")
        assert len(_components_named(root, "web")) == 2

    def test_a_manifest_directory_and_a_same_named_image_service_do_not_merge(self, tmp_path):
        root = str(tmp_path / "g4")
        _write(root, "kafka/pyproject.toml",
               '[project]\nname = "kafka"\nversion = "1"\n[project.scripts]\nkafka = "kafka:main"\n')
        _write(root, "kafka/compose.yaml", "services:\n  kafka:\n    image: confluentinc/kafka:7\n")
        _git(root)
        kafka = _components_named(root, "kafka")
        # two nodes. (The service is "shipped here" only because the manifest's package name equals the
        # image's last segment: that is the published-image rule, not the twin merge.)
        assert len(kafka) == 2 and len({c.slug for c in kafka}) == 2
        assert next(c for c in kafka if c.image == "").admission == "built_here"
        assert next(c for c in kafka if c.image).image == "confluentinc/kafka"


def test_qualified_slugs_that_collide_across_plain_slug_groups_all_get_the_hash(tmp_path):
    root = str(tmp_path / "xg")
    # Two plain-slug groups ("b-c-x::svc" and "x::svc"), each shared by two directories. Their qualified
    # texts collide ACROSS the groups: a/b-c-x and a-b/c/x both read "a-b-c-x::svc".
    for d in ("a/b-c-x", "q/b-c-x", "a-b/c/x", "q/x"):
        _write(root, f"{d}/compose.yaml", "services:\n  svc:\n    image: i/svc:1\n")
    _git(root)
    slugs = [c.slug for c in _components_named(root, "svc")]
    assert len(slugs) == 4 and len(set(slugs)) == 4
    assert "q-b-c-x::svc" in slugs and "q-x::svc" in slugs          # non-colliding ones are unchanged


def test_a_unitless_slug_entry_on_a_node_with_no_directory_does_not_apply():
    entry, note = node_admission.effective({"a-b::x": [{"to": "built_here", "reason": "r", "by": "d", "at": "t"}]},
                                           "a-b::x", "", "x")
    assert entry is None and "not applied" in note


# ── build: dockerfile:, scoped == full, uniqueness, fixtures, and the published-image rule ─────────

def _mono(tmp_path, name, build_yaml):
    root = str(tmp_path / name)
    _write(root, "services/compose.yaml", "services:\n  api:\n" + build_yaml)
    _write(root, "services/api/Dockerfile", "FROM python:3\nCMD [\"python\", \"app.py\"]\n")
    _write(root, "services/api/app.py", "print(1)\n")
    _git(root)
    return root


class TestDockerfileKey:
    def test_a_dockerfile_key_decides_which_directory_a_service_builds(self, tmp_path):
        # context is the repository root, the Dockerfile is in the twin's directory: it builds that directory
        root = _mono(tmp_path, "df1", "    image: odpi/api:1\n    build:\n      context: ..\n"
                                     "      dockerfile: services/api/Dockerfile\n")
        api = _components_named(root, "api")
        assert len(api) == 1 and api[0].admission == "built_here" and api[0].image == "odpi/api"

    def test_the_plain_shapes_still_work(self, tmp_path):
        for n, yml in (("a", "    build: ./api\n"), ("b", "    build:\n      context: ./api\n"),
                       ("c", "    build:\n      context: ./api\n      dockerfile: Dockerfile\n")):
            root = _mono(tmp_path, f"df3{n}", yml)
            assert len(_components_named(root, "api")) == 1, n


def test_a_remote_build_context_is_no_build(tmp_path):
    root = str(tmp_path / "rm")
    _write(root, "c.yaml", "services:\n  api:\n    build: https://github.com/o/r.git\n")
    assert adm.compose_service_facts(root, "c.yaml")["api"]["build"] == ""


class TestScopedEqualsFull:
    def _scoped_and_full(self, root):
        from resource_explorer.surveyors.arch_recovery import detectors, exclusion
        full = exclusion.scan(root).first_party
        scoped = [f for f in full if f.startswith("b/")]
        f_comps = detectors.build_components(root, full, all_files=full)[0]
        s_comps = detectors.build_components(root, scoped, all_files=full)[0]
        return ({c.slug: (c.admission, c.image) for c in f_comps},
                {c.slug: (c.admission, c.image) for c in s_comps})

    def test_a_scoped_run_that_excludes_the_twins_directory_still_decides_from_the_census(self, tmp_path):
        from resource_explorer.surveyors.arch_recovery import detectors, exclusion
        root = str(tmp_path / "sc")
        _write(root, "X/compose.yaml", "services:\n  web:\n    image: nginx:1\n")
        _write(root, "X/web/Dockerfile", "FROM python:3\nCMD [\"python\", \"app.py\"]\n")
        _write(root, "X/web/app.py", "print(1)\n")
        _git(root)
        full = exclusion.scan(root).first_party
        scoped = [f for f in full if not f.startswith("X/web/")]            # the twin is outside the scope
        f_c = {c.slug: c.admission for c in detectors.build_components(root, full, all_files=full)[0]}
        s_c = {c.slug: c.admission for c in detectors.build_components(root, scoped, all_files=full)[0]}
        assert s_c == {"X::web.svc": "referenced_only"} and f_c["X::web.svc"] == "referenced_only"

    def test_a_scoped_run_keeps_ci_publish_evidence_from_outside_its_scope(self, tmp_path):
        root = str(tmp_path / "sc2")
        _write(root, ".github/workflows/p.yml", "steps:\n  - with:\n      tags: odpi/web:latest\n")
        _write(root, "b/compose.yaml", "services:\n  web:\n    image: odpi/web:1\n")
        _git(root)
        full, scoped = self._scoped_and_full(root)
        assert full["b::web"][0] == "shipped_here" and scoped["b::web"][0] == "shipped_here"


def test_a_service_literally_named_like_a_derived_slug_cannot_collide(tmp_path):
    root = str(tmp_path / "lit")
    _write(root, "X/compose.yaml", "services:\n  web:\n    image: nginx:1\n  web.svc:\n    image: redis:7\n")
    _write(root, "X/web/Dockerfile", "FROM python:3\nCMD [\"python\", \"app.py\"]\n")
    _write(root, "X/web/app.py", "print(1)\n")
    _git(root)
    from resource_explorer.surveyors.arch_recovery import detectors, exclusion
    first = exclusion.scan(root).first_party
    slugs = [c.slug for c in detectors.build_components(root, first, all_files=first)[0]]
    assert len(slugs) == len(set(slugs))
    assert "X::web.svc" in slugs                       # the literally named service keeps its natural slug


def test_a_fixture_compose_service_does_not_qualify_a_real_one(tmp_path):
    root = str(tmp_path / "fx")
    _write(root, "deploy/compose.yaml", "services:\n  kafka:\n    image: apache/kafka:3\n")
    _write(root, "tests/deploy/compose.yaml", "services:\n  kafka:\n    image: apache/kafka:3\n")
    _git(root)
    assert [c.slug for c in _components_named(root, "kafka")] == ["deploy::kafka"]


class TestPublishedImageRule:
    def _repo(self, tmp_path, name, with_ci):
        root = str(tmp_path / name)
        _write(root, "pyproject.toml", '[project]\nname = "redis"\nversion = "1"\n[project.scripts]\nredis = "redis:main"\n')
        _write(root, "compose.yaml", "services:\n  cache:\n    image: bitnami/redis:7\n")
        if with_ci:
            _write(root, ".github/workflows/p.yml", "steps:\n  - run: docker push bitnami/redis:7\n")
        _git(root)
        return root

    def test_a_package_named_like_a_third_party_image_does_not_make_it_shipped(self, tmp_path):
        cache = _components_named(self._repo(tmp_path, "ph1", False), "cache")
        assert [c.admission for c in cache] == ["referenced_only"]

    def test_the_same_with_a_workflow_that_pushes_that_image_is_shipped(self, tmp_path):
        cache = _components_named(self._repo(tmp_path, "ph2", True), "cache")
        assert [c.admission for c in cache] == ["shipped_here"]

    def test_a_bare_ci_push_counts_only_with_a_matching_package_name(self, tmp_path):
        root = str(tmp_path / "ph3")
        _write(root, "pyproject.toml", '[project]\nname = "app"\nversion = "1"\n[project.scripts]\napp = "app:main"\n')
        _write(root, "compose.yaml", "services:\n  a:\n    image: app:1\n  b:\n    image: other:1\n")
        _write(root, ".github/workflows/p.yml", "steps:\n  - run: docker push app:1\n  - run: docker push other:1\n")
        _git(root)
        by = {c.name: c.admission for c in _components_named(root, "a") + _components_named(root, "b")}
        assert by == {"a": "shipped_here", "b": "referenced_only"}


# ── last round: published once from the census, manifest twins, separators ─────────

def _stored_published(registry, slug):
    return node_admission._stored(registry, slug).get("published_images")


def test_a_scoped_run_after_a_full_run_leaves_the_published_images_identical(registry, tmp_path):
    root = _egeria_repo(tmp_path)
    project = _survey(registry, "egeria_git", root)
    before = _stored_published(registry, "egeria_git")
    assert "odpi/egeria-platform" in before and "odpi/egeria-ui" in before
    ArchDetectSurveyor(project, registry, scope_locator="docker", local_path=root,
                       surveyed_at="2026-10-09T00:00:00").run()
    assert _stored_published(registry, "egeria_git") == before


def test_the_published_list_comes_from_the_census_not_the_scoped_files(tmp_path):
    from resource_explorer.surveyors.arch_recovery import detectors, exclusion
    root = _egeria_repo(tmp_path)
    full = exclusion.scan(root).first_party
    scoped = [f for f in full if f.startswith("docker/")]
    a, b = {}, {}
    detectors.build_components(root, full, all_files=full, census_info=a)
    detectors.build_components(root, scoped, all_files=full, census_info=b)
    assert a["published"] == b["published"] and "odpi/egeria-ui" in a["published"]


class TestManifestTwins:
    def _repo(self, tmp_path, name, build_yaml):
        root = str(tmp_path / name)
        _write(root, "services/compose.yaml", "services:\n  api:\n" + build_yaml)
        _write(root, "services/api/pyproject.toml",
               '[project]\nname = "api"\nversion = "1"\n[project.scripts]\napi = "api:main"\n')
        _write(root, "services/docker/api.Dockerfile", "FROM alpine\n")
        _write(root, "services/api/web.Dockerfile", "FROM alpine\n")
        _git(root)
        return root

    def test_the_context_matching_the_manifest_directory_merges_even_with_a_dockerfile_elsewhere(self, tmp_path):
        root = self._repo(tmp_path, "m1", "    build:\n      context: ./api\n      dockerfile: ../docker/api.Dockerfile\n")
        assert len(_components_named(root, "api")) == 1

    def test_the_dockerfile_in_the_manifest_directory_merges_with_another_context(self, tmp_path):
        root = self._repo(tmp_path, "m2", "    build:\n      context: .\n      dockerfile: api/web.Dockerfile\n")
        assert len(_components_named(root, "api")) == 1

    def test_neither_the_context_nor_the_dockerfile_in_the_manifest_directory_does_not_merge(self, tmp_path):
        root = self._repo(tmp_path, "m3", "    build:\n      context: ./docker\n      dockerfile: api.Dockerfile\n")
        assert len(_components_named(root, "api")) == 2


def test_windows_separators_in_context_and_dockerfile_are_normalised(tmp_path):
    root = _mono(tmp_path, "win", "    build:\n      context: ..\n      dockerfile: 'services\\api\\Dockerfile'\n")
    assert len(_components_named(root, "api")) == 1
    _write(root, "c.yaml", "services:\n  w:\n    build:\n      context: '.\\api'\n")
    assert adm.compose_service_facts(root, "c.yaml")["w"]["build"] == "./api"
