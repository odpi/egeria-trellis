"""The container shape for a blueprint (DESIGN-BLUEPRINT-BENCHMARK-EGERIA-WORKSPACES.md 0, 6, 6a).

The project owner, 2026-10-08: either the hosted servers are SUB-COMPONENTS of the platform and only the
platform is a blueprint member, or the platform is left out. The default is the container shape.

Recording fakes only: a fake Egeria that logs every call, a temp SQLite registry. No live Egeria, no live
registry. The fake "content pack" already holds three of the six compositions.
"""
from __future__ import annotations

import pytest

from resource_explorer import blueprint_shape as bs
from resource_explorer.blueprint_shape import CONTAINER, CONTENTS, Node, distinct_name, plan_shape
from resource_explorer.surveyors.arch_recovery import admission as adm

# Brief I: a blueprint or port is written by a queued publish: RE's daemon on the requester's behalf.
pytestmark = pytest.mark.usefixtures("as_daemon")

G = "{:08d}-0000-0000-0000-000000000000".format
BP_GUID = G(1)
PLATFORM = G(10)
SERVERS = {"view-server": G(11), "engine-host": G(12), "integration-daemon": G(13),
           "nanny-daemon": G(14), "simple-metadata-store": G(15), "active-metadata-store": G(16)}
CONTENT_PACK_HELD = ("view-server", "engine-host", "integration-daemon")
ROOT_NAME = "OMAG Server Platform"
CLUSTER = "OMAG-Server-Platform"


def _nodes(root_admission=adm.BUILT, root_structural=False, with_root=True):
    out = []
    if with_root:
        out.append(Node(slug="omag-server-platform", name=ROOT_NAME, admission=root_admission,
                        structural=root_structural))
    out += [Node(slug=s, name=s.replace("-", " ").title()) for s in SERVERS]
    return out


# ── the test "is the root a real component", both ways ───────────────────────────────────────────────


class TestIsTheRootARealComponent:
    def test_built_here_root_is_real(self):
        real, why = bs.root_is_real_component(_nodes()[0])
        assert real and "built here" in why

    def test_shipped_here_root_is_real(self):
        assert bs.root_is_real_component(_nodes(root_admission=adm.SHIPPED)[0])[0]

    def test_content_pack_element_is_real_even_without_an_evidence_class(self):
        root = Node(slug="r", name="r", admission=adm.REFERENCED, content_pack=True)
        assert bs.root_is_real_component(root)[0]

    def test_a_path_only_root_is_not_real(self):
        real, why = bs.root_is_real_component(_nodes(root_structural=True)[0])
        assert not real and "only as a path" in why

    def test_no_root_member_is_a_grouping(self):
        real, why = bs.root_is_real_component(None)
        assert not real and "grouping" in why

    def test_referenced_only_root_is_not_real(self):
        assert not bs.root_is_real_component(_nodes(root_admission=adm.REFERENCED)[0])[0]

    def test_the_root_is_the_member_the_cluster_is_named_after(self):
        root = bs.find_root(CLUSTER, _nodes())
        assert root is not None and root.name == ROOT_NAME
        assert bs.find_root("comps/animation/deployment", _nodes(with_root=False)) is None


# ── the plan: shapes, flip, name, manifest words ─────────────────────────────────────────────────────


class TestThePlan:
    def test_real_root_defaults_to_container_one_member_six_compositions(self):
        plan = plan_shape(CLUSTER, _nodes())
        assert plan.shape == CONTAINER and plan.default_shape == CONTAINER
        assert [n.slug for n in plan.members] == ["omag-server-platform"]
        assert len(plan.compositions) == 6
        assert all(a.slug == "omag-server-platform" for a, _ in plan.compositions)
        assert "omag-server-platform" not in [b.slug for _, b in plan.compositions]

    def test_a_grouping_root_is_the_contents_shape_children_as_members_no_root_element(self):
        plan = plan_shape("comps/animation/deployment", _nodes(with_root=False))
        assert plan.shape == CONTENTS
        assert len(plan.members) == 6 and plan.compositions == []
        assert plan.root is None

    def test_a_path_only_root_member_is_left_out_of_the_contents(self):
        plan = plan_shape(CLUSTER, _nodes(root_structural=True))
        assert plan.shape == CONTENTS
        assert "omag-server-platform" not in [n.slug for n in plan.members]
        assert len(plan.members) == 6

    def test_never_the_container_and_its_contents_together(self):
        for requested in ("", CONTAINER, CONTENTS):
            plan = plan_shape(CLUSTER, _nodes(), requested=requested)
            member_slugs = {n.slug for n in plan.members}
            if "omag-server-platform" in member_slugs:
                assert member_slugs == {"omag-server-platform"}

    def test_the_manifest_names_the_shape_and_why(self):
        plan = plan_shape(CLUSTER, _nodes())
        assert plan.words == f"{ROOT_NAME} as container · 6 sub-components"
        assert "real component" in plan.why and "built here" in plan.why
        grouping = plan_shape("comps/x", _nodes(with_root=False))
        assert grouping.words == "root is a grouping · 6 members"
        assert "grouping" in grouping.why

    def test_flipping_before_the_write_changes_the_plan(self):
        auto = plan_shape(CLUSTER, _nodes())
        flipped = plan_shape(CLUSTER, _nodes(), requested=CONTENTS)
        assert auto.shape == CONTAINER and flipped.shape == CONTENTS
        assert flipped.flipped and not auto.flipped
        assert len(flipped.members) == 6 and flipped.compositions == []
        assert "flipped" in flipped.words and "flipped" in flipped.why
        assert flipped.flip_to == CONTAINER

    def test_a_flip_to_container_is_refused_with_no_root(self):
        plan = plan_shape("comps/x", _nodes(with_root=False), requested=CONTAINER)
        assert plan.shape == CONTENTS and "nothing to be the container" in plan.flip_refused

    def test_a_flip_to_container_is_refused_for_a_path_only_root(self):
        plan = plan_shape(CLUSTER, _nodes(root_structural=True), requested=CONTAINER)
        assert plan.shape == CONTENTS and "only a path" in plan.flip_refused

    def test_the_blueprint_name_never_equals_a_member_name(self):
        assert distinct_name("Egeria Deployment Blueprint", ["Egeria Deployment Blueprint", "x"]) \
            != "Egeria Deployment Blueprint"
        assert distinct_name("Egeria Deployment Blueprint", [ROOT_NAME]) == "Egeria Deployment Blueprint"
        # a case/punctuation-only difference is still the same name
        assert distinct_name("OMAG Server Platform", ["omag-server-platform"]) != "OMAG Server Platform"


# ── the write, against a recording fake Egeria ───────────────────────────────────────────────────────


class FakeEgeria:
    """Records every call. Stands in for SolutionArchitect and AutomatedCuration."""

    def __init__(self, existing_qns: dict[str, str] | None = None, content_pack_held=()):
        self.calls: list[tuple] = []
        self.blueprints: dict[str, dict] = {}
        self.qn_to_guid = dict(existing_qns or {})
        self.subs: dict[str, list[str]] = {PLATFORM: [SERVERS[s] for s in content_pack_held]}
        self.content_pack: dict[str, dict] = {}
        self.fail_link_for: set[str] = set()
        self.silent_link_for: set[str] = set()
        self.read_raises: Exception | None = None       # the container read raises
        self.read_shape_unknown = False                  # the container read lacks the children keys
        self.link_raises: Exception | None = None        # the link call raises something unexpected
        self.bp_members: list[str] | None = []           # None = the blueprint read has no member key

    # AutomatedCuration
    def get_guid_for_name(self, qn, **kw):
        self.calls.append(("get_guid_for_name", qn))
        g = self.qn_to_guid.get(qn)
        return [g] if g else []

    # SolutionArchitect
    def create_solution_blueprint(self, body):
        self.calls.append(("create_solution_blueprint", body))
        guid = BP_GUID
        self.blueprints[guid] = body["properties"]
        self.qn_to_guid[body["properties"]["qualifiedName"]] = guid
        return guid

    def get_solution_blueprint_by_guid(self, guid, **kw):
        self.calls.append(("get_solution_blueprint_by_guid", guid))
        if guid not in self.blueprints:
            return "No elements found"
        qn = next((q for q, g in self.qn_to_guid.items() if g == guid), "")
        out = {"elementHeader": {"guid": guid, "type": {"typeName": "SolutionBlueprint"}},
               "properties": {"qualifiedName": qn, "displayName": self.blueprints[guid].get("displayName", "")}}
        if self.bp_members is not None:
            out["collectionMembers"] = [{"relatedElement": {"elementHeader": {"guid": g}}} for g in self.bp_members]
        return out

    def get_solution_component_by_guid(self, guid, **kw):
        self.calls.append(("get_solution_component_by_guid", guid))
        if self.read_raises:
            raise self.read_raises
        qn = next((q for q, g in self.qn_to_guid.items() if g == guid), "")
        out = {"elementHeader": {"guid": guid, "type": {"typeName": "SolutionComponent"}},
               "properties": {"qualifiedName": qn}}
        if not self.read_shape_unknown:
            out["nestedSolutionComponents"] = [
                {"relatedElement": {"elementHeader": {"guid": g}}} for g in self.subs.get(guid, [])]
        return out

    def link_subcomponent(self, container, child, body):
        self.calls.append(("link_subcomponent", container, child, body))
        if self.link_raises:
            raise self.link_raises
        if child in self.fail_link_for:
            raise RuntimeError("Egeria refused")
        if child not in self.silent_link_for:
            self.subs.setdefault(container, []).append(child)

    def get_solution_components_by_name(self, name, **kw):
        self.calls.append(("get_solution_components_by_name", name))
        return self.content_pack.get(name, "No elements found")

    def of(self, name):
        return [c for c in self.calls if c[0] == name]


@pytest.fixture
def registry(tmp_path):
    from resource_explorer.registry import Project, ProjectRegistry

    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    reg.add(Project(slug="egeria_git", display_name="egeria_git", github_url="https://github.com/o/e"))
    return reg


def _seed(reg, *, root_admission=adm.BUILT, with_root=True, materialize_children=True,
          materialize_root=True, cluster_name=CLUSTER, root_structural=False):
    nodes = _nodes(root_admission=root_admission, with_root=with_root, root_structural=root_structural)
    members = []
    for i, n in enumerate(nodes):
        scope = f"src/{n.slug}"
        detail = {"name": n.name, "slug": n.slug, "admission": n.admission, "type": "Server"}
        if n.structural:
            detail["structural"] = True
        reg.upsert_finding("egeria_git", "architecture_recovery",
                           [{"check_name": "component", "label": "Server", "detail": detail}],
                           surveyed_at="2026-10-08T00:00:00", scope_locator=scope)
        members.append(n.slug)
        is_root = n.slug == "omag-server-platform"
        if (is_root and materialize_root) or (not is_root and materialize_children):
            guid = PLATFORM if is_root else SERVERS[n.slug]
            reg.record_materialized_component("repo", "egeria_git", scope,
                                              f"SolutionComponent::repo::egeria_git::{scope}", guid)
    reg.upsert_finding("egeria_git", "architecture_blueprints", [{
        "check_name": "candidate_blueprint", "label": cluster_name,
        "detail": {"name": cluster_name, "perspective": "deployment", "members": sorted(members),
                   "children": [], "parent": "", "oversized": False, "composed_into": ""}}],
        surveyed_at="2026-10-08T00:00:00", scope_locator="")


@pytest.fixture
def run(registry, monkeypatch):
    """accept(fake, shape="") -> (result, enqueued) with BlueprintMaterializer wired to the fake."""
    from resource_explorer.surveyors.arch_recovery import blueprint_materializer as bm
    from resource_explorer.workflows import curate

    enqueued: list[list[str]] = []
    monkeypatch.setattr("resource_explorer.egeria_outbox.enqueue_blueprint_members",
                        lambda reg, et, slug, bp, guids, **k: enqueued.append(list(guids)) or [1] * len(guids))
    # The workflow must never touch zones: this is the configured-only rule (brief 1).
    def _no_zone(*a, **k):
        raise AssertionError("the shape write touched a zone")
    monkeypatch.setattr("resource_explorer.egeria_identity.set_zone_membership", _no_zone, raising=False)
    monkeypatch.setattr("resource_explorer.egeria_identity.stamp_published", _no_zone, raising=False)
    real = bm.BlueprintMaterializer

    def accept(fake, shape="", cluster=CLUSTER):
        def factory(registry=None):
            m = real(platform_url="https://fake", registry=registry)
            m._solution_architect = fake
            m._automated_curation = fake
            m._connect = lambda: None
            return m
        monkeypatch.setattr(bm, "BlueprintMaterializer", factory)
        kw = {"shape": shape} if shape else {}
        return curate.materialize_blueprint_if_accepted(
            registry, "repo", "egeria_git", "deployment", cluster, "accepted", **kw)

    return accept, enqueued


class TestContainerShapeIsWritten:
    def test_platform_plus_six_servers_one_member_six_compositions_three_already_held(self, registry, run):
        accept, enqueued = run
        _seed(registry)
        fake = FakeEgeria(content_pack_held=CONTENT_PACK_HELD)
        out = accept(fake)
        # ONE blueprint member: the platform. No server is a direct member.
        assert enqueued == [[PLATFORM]]
        # six compositions in the end, three were already held and NOT rewritten
        links = fake.of("link_subcomponent")
        assert len(links) == 3
        assert {c[2] for c in links} == {SERVERS[s] for s in SERVERS if s not in CONTENT_PACK_HELD}
        assert all(c[1] == PLATFORM for c in links)
        assert sorted(fake.subs[PLATFORM]) == sorted(SERVERS.values())
        statuses = {c["status"] for c in out["compositions"]}
        assert statuses == {"already_present", "linked"}
        assert sum(1 for c in out["compositions"] if c["status"] == "already_present") == 3
        assert out["shape"]["shape"] == CONTAINER
        assert out["status"] == "materialized"

    def test_composition_keys_are_the_pair_of_qualified_names(self, registry, run):
        accept, _ = run
        _seed(registry)
        out = accept(FakeEgeria())
        c = out["compositions"][0]
        assert c["key"].startswith("SolutionComposition::SolutionComponent::repo::egeria_git::src/omag-server-platform::")

    def test_the_blueprint_is_named_by_kind_and_repository_never_by_a_member(self, registry, run):
        accept, _ = run
        _seed(registry)
        fake = FakeEgeria()
        accept(fake)
        name = fake.of("create_solution_blueprint")[0][1]["properties"]["displayName"]
        assert name == "Egeria Deployment Blueprint"
        assert name != ROOT_NAME

    def test_a_member_with_the_blueprints_own_name_forces_a_distinct_name(self, registry, run):
        accept, _ = run
        _seed(registry)
        registry.upsert_finding("egeria_git", "architecture_recovery", [{
            "check_name": "component", "label": "x",
            "detail": {"name": "Egeria Deployment Blueprint", "slug": "clash", "admission": adm.BUILT}}],
            surveyed_at="2026-10-08T00:00:00", scope_locator="src/clash")
        registry.upsert_finding("egeria_git", "architecture_blueprints", [{
            "check_name": "candidate_blueprint", "label": CLUSTER,
            "detail": {"name": CLUSTER, "perspective": "deployment", "children": [], "parent": "",
                       "members": ["clash", "omag-server-platform"], "oversized": False}}],
            surveyed_at="2026-10-08T00:01:00", scope_locator="")
        fake = FakeEgeria()
        accept(fake)
        name = fake.of("create_solution_blueprint")[0][1]["properties"]["displayName"]
        assert name != "Egeria Deployment Blueprint" and name.startswith("Egeria Deployment Blueprint")

    def test_proof_rows_are_written_after_a_read_by_guid(self, registry, run):
        accept, _ = run
        _seed(registry)
        fake = FakeEgeria(content_pack_held=CONTENT_PACK_HELD)
        accept(fake)
        proofs = [p for p in registry.list_catalogue_commit_proofs("egeria_git") if p["proof"] == "composition"]
        assert len(proofs) == 6
        assert {p["element_guid"] for p in proofs} == {PLATFORM}
        assert {p["target_guid"] for p in proofs} == set(SERVERS.values())
        assert all(p["detail"]["read_back"] is True for p in proofs)   # each really followed a read
        # every write was followed by a read of the container's sub-components by GUID
        order = [c[0] for c in fake.calls if c[0] in ("link_subcomponent", "get_solution_component_by_guid")
                 and (c[0] != "get_solution_component_by_guid" or c[1] == PLATFORM)]
        assert order[0] == "get_solution_component_by_guid" and order[-1] == "get_solution_component_by_guid"
        shape_rows = [p for p in registry.list_catalogue_commit_proofs("egeria_git") if p["proof"] == "shape"]
        assert shape_rows and shape_rows[-1]["detail"]["shape"] == CONTAINER

    def test_a_composition_egeria_does_not_show_back_is_not_called_linked(self, registry, run):
        accept, _ = run
        _seed(registry)
        fake = FakeEgeria()
        fake.silent_link_for = {SERVERS["nanny-daemon"]}
        out = accept(fake)
        by_child = {c["child_guid"]: c["status"] for c in out["compositions"]}
        assert by_child[SERVERS["nanny-daemon"]] == "unconfirmed"
        assert out["status"] == "partial"
        proof = [p for p in registry.list_catalogue_commit_proofs("egeria_git")
                 if p["proof"] == "composition" and p["target_guid"] == SERVERS["nanny-daemon"]][0]
        assert proof["detail"]["status"] == "unconfirmed"

    def test_a_refused_composition_is_reported_and_the_rest_still_land(self, registry, run):
        accept, _ = run
        _seed(registry)
        fake = FakeEgeria()
        fake.fail_link_for = {SERVERS["engine-host"]}
        out = accept(fake)
        by_child = {c["child_guid"]: c["status"] for c in out["compositions"]}
        assert by_child[SERVERS["engine-host"]] == "error"
        assert by_child[SERVERS["view-server"]] == "linked"
        assert out["status"] == "partial"

    def test_the_composition_body_validates_against_pyegeria(self, registry, run):
        from pydantic import TypeAdapter
        from pyegeria.models import NewRelationshipRequestBody

        accept, _ = run
        _seed(registry)
        fake = FakeEgeria()
        accept(fake)
        body = fake.of("link_subcomponent")[0][3]
        TypeAdapter(NewRelationshipRequestBody).validate_python(body)

    def test_no_zone_is_written_the_zone_rule_is_configured_only(self, registry, run):
        accept, _ = run
        _seed(registry)
        fake = FakeEgeria()
        accept(fake)   # the fixture makes any zone call raise
        assert not [c for c in fake.calls if "zone" in c[0].lower()]
        for _, *rest in fake.of("create_solution_blueprint"):
            assert "ZoneMembership" not in str(rest)

    def test_the_container_without_its_own_element_writes_no_membership_and_no_fallback(self, registry, run):
        accept, enqueued = run
        _seed(registry, materialize_root=False)
        fake = FakeEgeria()
        out = accept(fake)
        # never fall back to children-as-members: that would be the other shape, silently
        assert enqueued == [[]]
        assert fake.of("link_subcomponent") == []
        assert out["status"] == "partial"
        assert "omag-server-platform" in out["unmaterialized_members"]


class TestContentsShapeIsWritten:
    def test_a_grouping_root_writes_children_as_members_and_no_composition(self, registry, run):
        accept, enqueued = run
        _seed(registry, with_root=False, cluster_name="comps/animation/deployment")
        fake = FakeEgeria()
        out = accept(fake, cluster="comps/animation/deployment")
        assert len(enqueued) == 1 and sorted(enqueued[0]) == sorted(SERVERS.values())
        assert fake.of("link_subcomponent") == [] and out["compositions"] == []
        assert out["shape"]["shape"] == CONTENTS
        assert out["shape"]["words"] == "root is a grouping · 6 members"

    def test_flipping_a_real_root_to_contents_leaves_the_platform_out(self, registry, run):
        accept, enqueued = run
        _seed(registry)
        fake = FakeEgeria()
        out = accept(fake, shape=CONTENTS)
        assert sorted(enqueued[0]) == sorted(SERVERS.values()) and PLATFORM not in enqueued[0]
        assert fake.of("link_subcomponent") == []
        assert out["shape"]["flipped"] is True


class TestAdoptionAndIdempotency:
    def test_existing_components_are_adopted_by_qualified_name_and_none_created_twice(self, registry, run):
        """The old blueprint's seven components exist in Egeria but this registry never recorded them
        (a lost cache, a second machine): the new blueprint adopts them by qualifiedName."""
        accept, enqueued = run
        _seed(registry, materialize_children=False, materialize_root=False)
        qns = {}
        for n in _nodes():
            guid = PLATFORM if n.slug == "omag-server-platform" else SERVERS[n.slug]
            qns[f"SolutionComponent::repo::egeria_git::src/{n.slug}"] = guid
        fake = FakeEgeria(existing_qns=qns)
        out = accept(fake)
        assert enqueued == [[PLATFORM]]
        assert len(fake.of("link_subcomponent")) == 6
        assert not any(c[0].startswith("create_solution_component") for c in fake.calls)
        # and they are recorded, so the next accept reads the registry
        assert registry.get_materialized_component("repo", "egeria_git", "src/view-server")["guid"] == SERVERS["view-server"]
        assert out["status"] == "materialized"

    def test_a_rerun_writes_nothing_new(self, registry, run):
        accept, enqueued = run
        _seed(registry)
        fake = FakeEgeria(content_pack_held=CONTENT_PACK_HELD)
        accept(fake)
        creates = len(fake.of("create_solution_blueprint"))
        links = len(fake.of("link_subcomponent"))
        out2 = accept(fake)
        assert len(fake.of("create_solution_blueprint")) == creates == 1
        assert len(fake.of("link_subcomponent")) == links == 3
        assert {c["status"] for c in out2["compositions"]} == {"already_present"}
        assert enqueued[0] == enqueued[1] == [PLATFORM]

    def test_a_deleted_old_blueprint_is_not_trusted_from_the_cache(self, registry, run):
        """The owner deletes the wrongly shaped blueprint in Egeria. RE's registry still names its GUID;
        the next accepted verdict must create the new one, not re-attach to a ghost."""
        accept, enqueued = run
        _seed(registry)
        registry.record_materialized_blueprint(
            "repo", "egeria_git", "deployment", CLUSTER,
            "SolutionBlueprint::repo::egeria_git::deployment::OMAG-Server-Platform", G(99))
        fake = FakeEgeria()           # G(99) is not among the fake's blueprints: it was deleted
        out = accept(fake)
        assert len(fake.of("create_solution_blueprint")) == 1
        assert out["guid"] == BP_GUID and out["status"] == "materialized"
        assert registry.get_materialized_blueprint("repo", "egeria_git", "deployment", CLUSTER)["guid"] == BP_GUID

    def test_a_live_cached_blueprint_is_kept(self, registry, run):
        accept, _ = run
        _seed(registry)
        fake = FakeEgeria()
        fake.blueprints[G(99)] = {"qualifiedName": "x"}
        registry.record_materialized_blueprint("repo", "egeria_git", "deployment", CLUSTER, "x", G(99))
        out = accept(fake)
        assert fake.of("create_solution_blueprint") == [] and out["guid"] == G(99)

    def test_an_unreadable_cache_check_does_not_wipe_the_cache(self, registry, run):
        accept, _ = run
        _seed(registry)
        registry.record_materialized_blueprint("repo", "egeria_git", "deployment", CLUSTER, "x", G(99))
        fake = FakeEgeria()

        def boom(guid, **kw):
            raise ConnectionError("platform unreachable")
        fake.get_solution_blueprint_by_guid = boom
        out = accept(fake)
        assert out["status"] == "error"
        assert registry.get_materialized_blueprint("repo", "egeria_git", "deployment", CLUSTER)["guid"] == G(99)
        assert fake.of("create_solution_blueprint") == []

    def test_a_content_pack_root_is_adopted_and_makes_a_grouping_real(self, registry, run):
        accept, enqueued = run
        # root exists only as a path-less referenced node with no materialised element of its own
        _seed(registry, root_admission=adm.REFERENCED, materialize_root=False)
        fake = FakeEgeria()
        cp_guid = G(77)
        fake.content_pack[ROOT_NAME] = [{
            "elementHeader": {"guid": cp_guid},
            "properties": {"qualifiedName": "Egeria:ValidMetadataValue:Software Service:deployedImplementationType-(OMAG Server Platform)::OMAG Server Platform",
                           "displayName": ROOT_NAME}}]
        out = accept(fake)
        assert out["shape"]["shape"] == CONTAINER and "content-pack" in out["shape"]["why"]
        assert enqueued == [[cp_guid]]
        assert all(c[1] == cp_guid for c in fake.of("link_subcomponent"))


# ── PR/CI review of 02f0b8c6: duplicates, unknown shapes, uncaught errors, ambiguity, extras, truthful proofs ──


class TestAnUnknownReadWritesNothing:
    """A dict missing the children keys is "the read did not say", NOT "no children": SolutionComposition is
    multi-link, so writing every pair on a guess duplicates relationships."""

    def test_a_container_read_without_the_children_keys_writes_no_composition(self, registry, run):
        accept, enqueued = run
        _seed(registry)
        fake = FakeEgeria()
        fake.read_shape_unknown = True
        out = accept(fake)
        assert fake.of("link_subcomponent") == []
        assert {c["status"] for c in out["compositions"]} == {"unconfirmed"}
        assert all("did not say whether children exist" in c["note"] for c in out["compositions"])
        assert out["status"] == "partial"

    def test_an_empty_answer_with_the_key_present_is_trusted(self, registry, run):
        accept, _ = run
        _seed(registry)
        fake = FakeEgeria()                 # key present, empty list: really no children yet
        accept(fake)
        assert len(fake.of("link_subcomponent")) == 6

    def test_a_failed_read_before_writes_nothing_and_leaves_truthful_rows(self, registry, run):
        accept, enqueued = run
        _seed(registry)
        fake = FakeEgeria()
        fake.read_raises = ConnectionError("platform unreachable")
        out = accept(fake)
        assert fake.of("link_subcomponent") == []
        proofs = [p for p in registry.list_catalogue_commit_proofs("egeria_git") if p["proof"] == "composition"]
        assert len(proofs) == 6
        assert all(p["detail"]["read_back"] is False and p["detail"]["status"] == "unread" for p in proofs)
        assert out["status"] == "partial" and enqueued == [[PLATFORM]]     # the membership still goes on


class TestUnexpectedErrorsDoNotLeaveAHalfWrittenBlueprint:
    @pytest.mark.parametrize("exc", [KeyError("nestedSolutionComponents"), TypeError("bad shape"),
                                     RuntimeError("PyegeriaException: boom")])
    def test_an_unexpected_exception_still_enqueues_memberships_and_writes_proofs(self, registry, run, exc):
        accept, enqueued = run
        _seed(registry)
        fake = FakeEgeria()
        fake.link_raises = exc
        out = accept(fake)                                 # does not raise
        assert enqueued == [[PLATFORM]]
        assert out["status"] == "partial"
        assert {c["status"] for c in out["compositions"]} == {"error"}
        assert [p for p in registry.list_catalogue_commit_proofs("egeria_git") if p["proof"] == "shape"]
        assert [p for p in registry.list_catalogue_commit_proofs("egeria_git") if p["proof"] == "composition"]

    def test_an_exception_out_of_the_whole_link_step_is_caught_too(self, registry, run, monkeypatch):
        from resource_explorer.surveyors.arch_recovery import blueprint_materializer as bm
        accept, enqueued = run
        _seed(registry)
        monkeypatch.setattr(bm.BlueprintMaterializer, "link_sub_components",
                            lambda *a, **k: (_ for _ in ()).throw(KeyError("boom")), raising=False)
        out = accept(FakeEgeria())
        assert enqueued == [[PLATFORM]] and out["status"] == "partial"
        assert "KeyError" in out["composition_error"]
        proofs = [p for p in registry.list_catalogue_commit_proofs("egeria_git") if p["proof"] == "composition"]
        assert len(proofs) == 6 and all(p["detail"]["read_back"] is False for p in proofs)


class TestNeverRecreateOnAnUnreadableAnswer:
    def test_a_search_that_raises_creates_nothing(self, registry, run):
        accept, _ = run
        _seed(registry)
        fake = FakeEgeria()

        def boom(qn, **kw):
            raise TimeoutError("timed out")
        fake.get_guid_for_name = boom
        out = accept(fake)
        assert out["status"] == "error" and fake.of("create_solution_blueprint") == []

    def test_a_guid_containing_404_in_a_timeout_message_is_not_gone(self, registry, run):
        accept, _ = run
        _seed(registry)
        guid = "11404404-0000-0000-0000-000000000000"
        registry.record_materialized_blueprint("repo", "egeria_git", "deployment", CLUSTER, "x", guid)
        fake = FakeEgeria()

        def boom(g, **kw):
            raise TimeoutError(f"timeout reading {g} (504)")
        fake.get_solution_blueprint_by_guid = boom
        out = accept(fake)
        assert out["status"] == "error" and fake.of("create_solution_blueprint") == []
        assert registry.get_materialized_blueprint("repo", "egeria_git", "deployment", CLUSTER)["guid"] == guid

    def test_a_user_not_found_error_is_not_a_missing_blueprint(self, registry, run):
        accept, _ = run
        _seed(registry)
        registry.record_materialized_blueprint("repo", "egeria_git", "deployment", CLUSTER, "x", G(99))
        fake = FakeEgeria()

        def boom(g, **kw):
            raise RuntimeError("user erinoverview not found on server qs-view-server")
        fake.get_solution_blueprint_by_guid = boom
        out = accept(fake)
        assert out["status"] == "error" and fake.of("create_solution_blueprint") == []


class TestContentPackAmbiguity:
    def _el(self, guid, name, qn="Egeria:ValidMetadataValue:Software Service:x::" ):
        return {"elementHeader": {"guid": guid}, "properties": {"qualifiedName": qn + name, "displayName": name}}

    def test_a_contains_match_on_another_name_is_not_adopted(self, registry, run):
        accept, enqueued = run
        _seed(registry, root_admission=adm.REFERENCED, materialize_root=False)
        fake = FakeEgeria()
        fake.content_pack[ROOT_NAME] = [self._el(G(70), "OMAG Server Platform Extras")]
        out = accept(fake)
        # not adopted, so the referenced-only root is no real component: the contents shape, root left out
        assert out["shape"]["shape"] == CONTENTS and len(enqueued[0]) == 6 and G(70) not in enqueued[0]
        assert "adopted_from_content_pack" not in out

    def test_more_than_one_exact_match_refuses_and_says_so(self, registry, run):
        accept, enqueued = run
        _seed(registry, root_admission=adm.REFERENCED, materialize_root=False)
        fake = FakeEgeria()
        fake.content_pack[ROOT_NAME] = [self._el(G(71), ROOT_NAME), self._el(G(72), ROOT_NAME)]
        out = accept(fake)
        assert out["shape"]["shape"] == CONTENTS and G(71) not in enqueued[0] and G(72) not in enqueued[0]
        assert "2 content-pack elements" in out["content_pack_refused"][0]

    def test_adoption_by_qualified_name_verifies_the_type_and_reports_the_skipped(self, registry, run):
        accept, enqueued = run
        _seed(registry, materialize_children=False, materialize_root=False)
        qns = {f"SolutionComponent::repo::egeria_git::src/{n.slug}":
               (PLATFORM if n.slug == "omag-server-platform" else SERVERS[n.slug]) for n in _nodes()}
        fake = FakeEgeria(existing_qns=qns)
        orig = fake.get_solution_component_by_guid

        def wrong_type(guid, **kw):                     # the nanny-daemon name resolves to a different type
            out = orig(guid, **kw)
            if guid == SERVERS["nanny-daemon"]:
                out["elementHeader"]["type"]["typeName"] = "SolutionBlueprint"
            return out
        fake.get_solution_component_by_guid = wrong_type
        out = accept(fake)
        assert SERVERS["nanny-daemon"] not in [c[2] for c in fake.of("link_subcomponent")]
        assert "nanny-daemon" in out["adoption_skipped"]
        assert registry.get_materialized_component("repo", "egeria_git", "src/nanny-daemon") is None


class TestOldMembershipsAreReportedNeverRemoved:
    def test_children_that_are_also_direct_members_are_reported(self, registry, run):
        accept, enqueued = run
        _seed(registry)
        fake = FakeEgeria()
        # an earlier run on the old blueprint: root and three servers are direct members
        registry.record_materialized_blueprint("repo", "egeria_git", "deployment", CLUSTER, "x", BP_GUID)
        fake.blueprints[BP_GUID] = {}
        fake.bp_members = [PLATFORM, SERVERS["view-server"], SERVERS["engine-host"], SERVERS["nanny-daemon"]]
        out = accept(fake)
        assert sorted(out["extra_members"]) == sorted(SERVERS[s] for s in ("view-server", "engine-host", "nanny-daemon"))
        assert not [c for c in fake.calls if "detach" in c[0] or "delete" in c[0]]
        shape = [p for p in registry.list_catalogue_commit_proofs("egeria_git") if p["proof"] == "shape"][-1]
        assert sorted(shape["detail"]["extra_members"]) == sorted(out["extra_members"])

    def test_a_blueprint_read_without_members_reports_none_rather_than_guessing(self, registry, run):
        accept, _ = run
        _seed(registry)
        fake = FakeEgeria()
        fake.bp_members = None
        out = accept(fake)
        assert out["extra_members"] == [] and out["extra_members_read"] is False


class TestTruthfulProofs:
    def test_the_shape_proof_does_not_say_container_when_nothing_was_written(self, registry, run):
        accept, _ = run
        _seed(registry, materialize_root=False)
        accept(FakeEgeria())
        shape = [p for p in registry.list_catalogue_commit_proofs("egeria_git") if p["proof"] == "shape"][-1]
        assert shape["detail"]["shape"] == "container (not written: root has no GUID)"
        assert shape["detail"]["written"] is False

    def test_an_error_row_says_no_read_back(self, registry, run):
        accept, _ = run
        _seed(registry)
        fake = FakeEgeria()
        fake.fail_link_for = {SERVERS["engine-host"]}
        accept(fake)
        rows = {p["target_guid"]: p for p in registry.list_catalogue_commit_proofs("egeria_git")
                if p["proof"] == "composition"}
        assert rows[SERVERS["engine-host"]]["detail"]["read_back"] is False
        assert rows[SERVERS["view-server"]]["detail"]["read_back"] is True
