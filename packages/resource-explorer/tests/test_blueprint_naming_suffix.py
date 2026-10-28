"""Blueprint and component names say what the element represents (the 2026-10-08 naming ruling, 'what it represents').

displayName only, never qualifiedName; five closed words derived from the evidence class; content-pack
elements are never renamed. Recording fakes only.
"""
from __future__ import annotations

import pytest

from resource_explorer import blueprint_shape as bs
from resource_explorer.blueprint_shape import (
    CODE_MODULE, CONTAINER_DEFINITION, IMAGE, RUNTIME, Node, blueprint_suffix_name, display_name,
    represents_kind,
)
from tests.test_blueprint_container_shape import (  # noqa: F401  (fixtures and fakes reused)
    BP_GUID, CLUSTER, FakeEgeria, G, PLATFORM, ROOT_NAME, SERVERS, registry, run,
)
from resource_explorer.surveyors.arch_recovery import admission as adm
from resource_explorer.surveyors.arch_recovery.blueprint_materializer import BlueprintMaterializer as REAL

# Brief I: a blueprint or port is written by a queued publish: RE's daemon on the requester's behalf.
pytestmark = pytest.mark.usefixtures("as_daemon")


class TestTheFiveWords:
    def test_a_package_manifest_is_a_code_module(self):
        for rel in ("pom.xml", "build.gradle", "svc/package.json", "pyproject.toml"):
            assert represents_kind({"admission_evidence": f"from {rel}"}) == CODE_MODULE

    def test_a_dockerfile_or_a_compose_service_is_a_container_definition_built_shipped_or_referenced(self):
        assert represents_kind({"admission_evidence": "from docker/Dockerfile"}) == CONTAINER_DEFINITION
        assert represents_kind({"admission": adm.BUILT, "admission_evidence": "from deploy/docker-compose.yml · build ."}) == CONTAINER_DEFINITION
        assert represents_kind({"admission": adm.SHIPPED, "admission_evidence": "image odpi/x · published by a"}) == CONTAINER_DEFINITION
        assert represents_kind({"admission": adm.REFERENCED, "image": "redis", "admission_evidence": "image redis · from c.yaml"}) == CONTAINER_DEFINITION

    def test_a_built_artefact_by_name_is_an_image(self):
        assert represents_kind({"image": "odpi/egeria-platform"}, artefact=True) == IMAGE
        assert represents_kind({}, artefact=True) is None            # no image name, no claim

    def test_a_registered_or_live_instance_is_a_runtime_never_a_file(self):
        assert represents_kind({"admission_evidence": "from Dockerfile"}, registered=True) == RUNTIME

    def test_a_content_pack_element_has_no_word(self):
        assert represents_kind({"admission_evidence": "from Dockerfile"}, content_pack=True) is None

    def test_no_evidence_is_no_word_never_a_guess(self):
        assert represents_kind({}) is None
        assert represents_kind({"admission_evidence": "from some/notes.txt"}) is None


class TestDisplayName:
    def test_component_form(self):
        assert display_name("egeria-main", CONTAINER_DEFINITION) == "egeria-main (container definition)"
        assert display_name("open-metadata-implementation", CODE_MODULE) == "open-metadata-implementation (code module)"
        assert display_name("odpi/egeria-platform", IMAGE) == "odpi/egeria-platform (image)"

    def test_no_kind_no_suffix_and_the_list_is_closed(self):
        assert display_name("View Server", None) == "View Server"
        with pytest.raises(ValueError):
            display_name("x", "deployment unit")

    def test_idempotent(self):
        once = display_name("x", CODE_MODULE)
        assert display_name(once, CODE_MODULE) == once

    def test_a_sub_resource_name_is_never_suffixed(self):
        assert display_name("src/a.py · egeria", CODE_MODULE) == "src/a.py · egeria"


class TestBlueprintName:
    def test_one_kind_takes_the_plural(self):
        assert blueprint_suffix_name("Egeria Deployment Blueprint", [CONTAINER_DEFINITION] * 3) \
            == "Egeria Deployment Blueprint (container definitions)"
        assert blueprint_suffix_name("Egeria Build Blueprint", [CODE_MODULE, CODE_MODULE]) \
            == "Egeria Build Blueprint (code modules)"
        assert blueprint_suffix_name("Egeria Deployment Blueprint", [RUNTIME]) == "Egeria Deployment Blueprint (runtime)"

    def test_a_mixed_blueprint_takes_no_suffix(self):
        assert blueprint_suffix_name("Egeria Deployment Blueprint", [CODE_MODULE, CONTAINER_DEFINITION]) \
            == "Egeria Deployment Blueprint"

    def test_a_member_whose_kind_is_unknown_means_no_suffix_not_a_wrong_one(self):
        assert blueprint_suffix_name("B", [CODE_MODULE, None]) == "B"
        assert blueprint_suffix_name("B", []) == "B"

    def test_a_content_pack_member_is_neutral(self):
        assert blueprint_suffix_name("B", [CONTAINER_DEFINITION, "content_pack"]) == "B (container definitions)"

    def test_the_blueprint_still_never_shares_a_members_name(self):
        name = "Egeria Deployment Blueprint (container definitions)"
        assert bs.distinct_name(name, [name]) != name


# ── the write ────────────────────────────────────────────────────────────────────────────────────────


def _seed_with_evidence(reg, *, evidence="from docker-compose.yml"):
    for slug in ["omag-server-platform", *SERVERS]:
        name = ROOT_NAME if slug == "omag-server-platform" else slug
        scope = f"src/{slug}"
        reg.upsert_finding("egeria_git", "architecture_recovery", [{
            "check_name": "component", "label": "x",
            "detail": {"name": name, "slug": slug, "admission": adm.BUILT, "admission_evidence": evidence}}],
            surveyed_at="2026-10-08T00:00:00", scope_locator=scope)
        reg.record_materialized_component("repo", "egeria_git", scope,
                                          f"SolutionComponent::repo::egeria_git::{scope}",
                                          PLATFORM if slug == "omag-server-platform" else SERVERS[slug])
    reg.upsert_finding("egeria_git", "architecture_blueprints", [{
        "check_name": "candidate_blueprint", "label": CLUSTER,
        "detail": {"name": CLUSTER, "perspective": "deployment", "children": [], "parent": "",
                   "members": sorted(["omag-server-platform", *SERVERS]), "oversized": False, "composed_into": ""}}],
        surveyed_at="2026-10-08T00:00:00", scope_locator="")


class TestTheBlueprintWrite:
    def test_egeria_git_reads_with_the_container_definitions_suffix_and_the_qualified_name_is_clean(self, registry, run):
        accept, _ = run
        _seed_with_evidence(registry)
        fake = FakeEgeria()
        accept(fake)
        props = fake.of("create_solution_blueprint")[0][1]["properties"]
        assert props["displayName"] == "Egeria Deployment Blueprint (container definitions)"
        assert props["qualifiedName"] == "SolutionBlueprint::repo::egeria_git::deployment"
        assert "OMAG" not in props["qualifiedName"]
        assert props["identifier"] == "EGERIA-GIT-DEPLOYMENT"
        assert "(" not in props["qualifiedName"]

    def test_the_qualified_name_is_identical_with_and_without_the_suffix(self, registry, run, tmp_path):
        from resource_explorer.registry import Project, ProjectRegistry
        accept, _ = run
        _seed_with_evidence(registry)
        with_suffix = FakeEgeria()
        accept(with_suffix)
        # same cluster, no derivable evidence: no suffix
        other = ProjectRegistry(db_path=str(tmp_path / "o.db"))
        other.add(Project(slug="egeria_git", display_name="egeria_git", github_url="https://github.com/o/e"))
        _seed_with_evidence(other, evidence="from some/notes.txt")
        import resource_explorer.workflows.curate as cur
        from resource_explorer.surveyors.arch_recovery import blueprint_materializer as bm
        real = REAL
        without = FakeEgeria()

        def factory(registry=None):
            m = real(platform_url="https://fake", registry=registry)
            m._solution_architect = without
            m._automated_curation = without
            m._connect = lambda: None
            return m
        bm.BlueprintMaterializer = factory
        try:
            cur.materialize_blueprint_if_accepted(other, "repo", "egeria_git", "deployment", CLUSTER, "accepted")
        finally:
            bm.BlueprintMaterializer = REAL
        a = with_suffix.of("create_solution_blueprint")[0][1]["properties"]
        b = without.of("create_solution_blueprint")[0][1]["properties"]
        assert a["displayName"].endswith("(container definitions)") and "(" not in b["displayName"]
        assert a["qualifiedName"] == b["qualifiedName"]

    def test_a_mixed_blueprint_gets_no_suffix(self, registry, run):
        accept, _ = run
        _seed_with_evidence(registry)
        registry.upsert_finding("egeria_git", "architecture_recovery", [{
            "check_name": "component", "label": "x",
            "detail": {"name": "view-server", "slug": "view-server", "admission": adm.BUILT,
                       "admission_evidence": "from pom.xml"}}],
            surveyed_at="2026-10-08T00:05:00", scope_locator="src/view-server")
        fake = FakeEgeria()
        accept(fake)
        assert fake.of("create_solution_blueprint")[0][1]["properties"]["displayName"] == "Egeria Deployment Blueprint"


class TestTheComponentWrite:
    def _fake_cm(self, monkeypatch):
        calls = []

        class CM:
            qualified_name_for = staticmethod(
                __import__("resource_explorer.surveyors.arch_recovery.materializer",
                           fromlist=["ComponentMaterializer"]).ComponentMaterializer.qualified_name_for)

            def __init__(self, registry=None):
                pass

            def materialize(self, et, slug, scope, **kw):
                calls.append((scope, kw))
                return {"status": "materialized", "guid": G(5), "qualified_name": "q"}
        monkeypatch.setattr("resource_explorer.surveyors.arch_recovery.materializer.ComponentMaterializer", CM)
        return calls

    def _seed(self, registry, detail, scope="src/x"):
        registry.upsert_finding("egeria_git", "architecture_recovery",
                                [{"check_name": "component", "label": "x", "detail": detail}],
                                surveyed_at="2026-10-08T00:00:00", scope_locator=scope)

    def test_a_manifest_component_is_created_with_the_code_module_suffix(self, registry, monkeypatch):
        from resource_explorer.workflows.curate import materialize_component_if_accepted
        calls = self._fake_cm(monkeypatch)
        self._seed(registry, {"name": "open-metadata-implementation", "slug": "omi", "type": "Software Library",
                              "admission": adm.BUILT, "admission_evidence": "from build.gradle"})
        materialize_component_if_accepted(registry, "repo", "egeria_git", "src/x", "accepted")
        assert calls[0][1]["name"] == "open-metadata-implementation (code module)"

    def test_no_evidence_keeps_the_plain_name(self, registry, monkeypatch):
        from resource_explorer.workflows.curate import materialize_component_if_accepted
        calls = self._fake_cm(monkeypatch)
        self._seed(registry, {"name": "thing", "slug": "t", "type": "x"})
        materialize_component_if_accepted(registry, "repo", "egeria_git", "src/x", "accepted")
        assert calls[0][1]["name"] == "thing"

    def test_a_matched_content_pack_element_is_adopted_not_created_not_renamed(self, registry, monkeypatch):
        from resource_explorer.surveyors.arch_recovery import blueprint_materializer as bm
        from resource_explorer.workflows.curate import materialize_component_if_accepted
        calls = self._fake_cm(monkeypatch)
        self._seed(registry, {"name": "egeria-main", "slug": "em", "type": "Third Party Process",
                              "admission": adm.SHIPPED, "image": "odpi/egeria-platform",
                              "admission_evidence": "image odpi/egeria-platform · published by a"})
        fake = FakeEgeria()
        fake.content_pack[ROOT_NAME] = [{
            "elementHeader": {"guid": G(77)},
            "properties": {"qualifiedName": "Egeria:ValidMetadataValue:Software Service:x::OMAG Server Platform",
                           "displayName": ROOT_NAME}}]
        real = REAL

        def factory(registry=None):
            m = real(platform_url="https://fake", registry=registry)
            m._solution_architect = fake
            m._automated_curation = fake
            m._connect = lambda: None
            return m
        monkeypatch.setattr(bm, "BlueprintMaterializer", factory)
        out = materialize_component_if_accepted(registry, "repo", "egeria_git", "src/x", "accepted")
        assert out["status"] == "adopted_content_pack" and out["guid"] == G(77)
        assert out["words"] == f"{ROOT_NAME} · adopted from the content pack · matched by image odpi/egeria-platform"
        assert calls == []                                              # no second component
        assert not [c for c in fake.calls if c[0].startswith(("update", "create"))]   # no rename, no create
        assert registry.get_materialized_component("repo", "egeria_git", "src/x")["guid"] == G(77)
