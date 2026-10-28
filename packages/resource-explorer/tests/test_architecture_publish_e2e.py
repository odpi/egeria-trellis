"""Brief A round 4: the plan and the write must agree, so one press settles the plan.

Drives `run_publish` END TO END with nothing of RE's own mocked: the real component and blueprint workflows, the real
materializers, the real shape plan, proof rows and plan. Only the client boundary is faked: one fake Egeria that
answers like 6.2 (a component with no children carries no children key; the composition relationship read answers
"No elements found" or a list with `elementAtEnd1`), the outbox enqueue, and the zone client. Components A and B and
blueprint P are accepted and published in ONE press, in the three shapes; the NEXT `publish_plan` must be empty.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from resource_explorer import architecture_publish as ap
from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors.arch_recovery import admission as adm
from resource_explorer.surveyors.arch_recovery import blueprint_materializer as bm
from resource_explorer.surveyors.arch_recovery import materializer as cm

# Brief I: a queued Publish is committed by RE's daemon on the person's behalf (owner, 2026-10-09).
pytestmark = pytest.mark.usefixtures("as_daemon")

G = "{:08d}-0000-0000-0000-000000000000".format


class FakeEgeria:
    """Every Egeria client the two materializers use, in one recording fake."""

    def __init__(self):
        self.n = 100
        self.qn_to_guid: dict[str, str] = {}
        self.types: dict[str, str] = {}
        self.names: dict[str, str] = {}
        self.subs: dict[str, list[str]] = {}
        self.calls: list[tuple] = []

    def _new(self, qn, type_name):
        self.n += 1
        guid = G(self.n)
        self.qn_to_guid[qn] = guid
        self.types[guid] = type_name
        return guid

    # AutomatedCuration
    def get_guid_for_name(self, qn, **kw):
        g = self.qn_to_guid.get(qn)
        return [g] if g else []

    # SolutionArchitect
    def create_solution_component(self, body):
        self.calls.append(("create_solution_component", body["properties"]["qualifiedName"]))
        return self._new(body["properties"]["qualifiedName"], "SolutionComponent")

    def create_solution_blueprint(self, body):
        self.calls.append(("create_solution_blueprint", body["properties"]["qualifiedName"]))
        return self._new(body["properties"]["qualifiedName"], "SolutionBlueprint")

    def get_solution_blueprint_by_guid(self, guid, **kw):
        qn = next((q for q, g in self.qn_to_guid.items() if g == guid), "")
        return {"elementHeader": {"guid": guid, "type": {"typeName": "SolutionBlueprint"}},
                "properties": {"qualifiedName": qn}, "collectionMembers": []}

    def get_solution_component_by_guid(self, guid, **kw):      # 6.2: no children key at all
        return {"elementHeader": {"guid": guid, "type": {"typeName": "SolutionComponent"}}, "properties": {}}

    def get_solution_components_by_name(self, name, **kw):
        return "No elements found"

    def link_subcomponent(self, container, child, body):
        self.calls.append(("link_subcomponent", container, child))
        self.subs.setdefault(container, []).append(child)

    # MetadataExpert
    def get_related_metadata_elements(self, guid, relationship_type, body, **kw):
        kids = self.subs.get(guid, [])
        if not kids:
            return "No elements found"
        return {"elementList": [{"element": {"elementGUID": k}, "elementAtEnd1": False} for k in kids]}


@pytest.fixture(autouse=True)
def requester_with_no_zones(monkeypatch):
    """Brief Z round 2: the run re-checks each item as its requester before writing. Here no zone is in use
    (the fake Egeria answers every element with no ZoneMembership), so every item is permitted."""
    from tests.zone_fakes import install, signed_in
    from resource_explorer.a2a_auth import current_caller

    fake = install(monkeypatch)
    reset = signed_in("x", source="queued-run")
    yield fake
    current_caller.reset(reset)


@pytest.fixture
def world(tmp_path, monkeypatch):
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    reg.add(Project(slug="p", display_name="P repo", github_url="https://github.com/x/p"))
    fake = FakeEgeria()
    outbox: list[tuple] = []

    def comp_connect(self):
        self._solution_architect = fake
        self._automated_curation = fake
    monkeypatch.setattr(cm.ComponentMaterializer, "_connect", comp_connect)
    monkeypatch.setattr("resource_explorer.egeria_identity.classification_client", lambda identity=None: MagicMock())

    real = bm.BlueprintMaterializer

    def factory(registry=None):
        m = real(platform_url="https://fake", registry=registry)
        m._solution_architect = fake
        m._automated_curation = fake
        m._metadata_expert = fake
        m._connect = lambda: None
        return m
    monkeypatch.setattr(bm, "BlueprintMaterializer", factory)
    monkeypatch.setattr("resource_explorer.egeria_outbox.enqueue_blueprint_members",
                        lambda registry, et, slug, bp, guids, **k: outbox.append((bp, list(guids))) or [1] * len(guids))
    monkeypatch.setattr("resource_explorer.workflows.curate.promote_to_publish_zones",
                        lambda guid: {"status": "already_unzoned", "words": "left as is"})
    return reg, fake, outbox


def _seed(reg, cluster, a_admission=adm.BUILT):
    for slug, admission in (("a", a_admission), ("b", adm.BUILT)):
        reg.upsert_finding("p", "architecture_recovery", [{
            "check_name": "component", "label": "x",
            "detail": {"name": slug.upper(), "slug": slug, "type": "Software Service", "admission": admission}}],
            surveyed_at="2026-10-09T00:00:00", scope_locator=f"src/{slug}")
        reg.record_component_verdict("repo", "p", f"src/{slug}", "accepted", "", "", decided_by="x")
    reg.upsert_finding("p", "architecture_blueprints", [{
        "check_name": "candidate_blueprint", "label": cluster,
        "detail": {"name": cluster, "perspective": "deployment", "members": ["a", "b"], "children": [],
                   "parent": "", "oversized": False, "composed_into": ""}}], surveyed_at="2026-10-09T00:00:00")
    reg.record_component_verdict("repo", "p", f"deployment::{cluster}", "accepted", "", "",
                                 verdict_target="blueprint", decided_by="x")


def _press(reg):
    plan = ap.publish_plan(reg, "p")
    target = {"slug": "p", "paths": [c["path"] for c in plan["components"]["to_write"]],
              "blueprints": [b["key"] for b in plan["blueprints"]["to_write"]]}
    assert len(target["paths"]) == 2 and len(target["blueprints"]) == 1, "A, B and P are all waiting"
    return ap.run_publish(reg, "p", target, "run-1")


def _settled(reg):
    plan = ap.publish_plan(reg, "p")
    assert plan["components"]["to_write"] == []
    assert plan["blueprints"]["to_write"] == [], plan["blueprints"]["to_write"]
    assert plan["nothing"] is True


class TestOnePressSettlesThePlan:
    def test_a_container_shape(self, world):
        reg, fake, outbox = world
        _seed(reg, "A")                                   # the cluster is named after A, a real component: A is the container
        res = _press(reg)
        assert [(r["kind"], r["status"]) for r in res] == [("component", "done"), ("component", "done"), ("blueprint", "done")]
        a, b = (fake.qn_to_guid[f"SolutionComponent::repo::p::src/{s}"] for s in "ab")
        assert [c for c in fake.calls if c[0] == "link_subcomponent"] == [("link_subcomponent", a, b)]
        assert [g for _, g in outbox] == [[a]]            # only the container is a member
        _settled(reg)

    def test_b_contents_shape_with_no_root(self, world):
        reg, fake, outbox = world
        _seed(reg, "a grouping of services")              # no member is the root
        res = _press(reg)
        assert [r["status"] for r in res] == ["done", "done", "done"]
        a, b = (fake.qn_to_guid[f"SolutionComponent::repo::p::src/{s}"] for s in "ab")
        assert not [c for c in fake.calls if c[0] == "link_subcomponent"]
        assert sorted(outbox[0][1]) == sorted([a, b])     # both are direct members
        _settled(reg)

    def test_c_contents_shape_where_root_a_is_only_referenced_but_cached(self, world):
        reg, fake, outbox = world
        _seed(reg, "A", a_admission=adm.REFERENCED)       # named after A, but A is referenced only: contents, A left out
        res = _press(reg)
        assert [r["status"] for r in res] == ["done", "done", "done"]
        a, b = (fake.qn_to_guid[f"SolutionComponent::repo::p::src/{s}"] for s in "ab")
        assert reg.get_materialized_component("repo", "p", "src/a")["guid"] == a    # A is cached ...
        assert outbox[0][1] == [b]                                                    # ... but is not a member
        _settled(reg)                                     # and the plan does not ask for it for ever


def _seed_parent_and_child(reg):
    """Child blueprint C (members a, b) nested under parent blueprint P (member c, child C); all accepted."""
    for slug in ("a", "b", "c"):
        reg.upsert_finding("p", "architecture_recovery", [{
            "check_name": "component", "label": "x",
            "detail": {"name": slug.upper(), "slug": slug, "type": "Software Service", "admission": adm.BUILT}}],
            surveyed_at="2026-10-09T00:00:00", scope_locator=f"src/{slug}")
        reg.record_component_verdict("repo", "p", f"src/{slug}", "accepted", "", "", decided_by="x")
    reg.upsert_finding("p", "architecture_blueprints", [
        {"check_name": "candidate_blueprint", "label": "the services",
         "detail": {"name": "the services", "perspective": "deployment", "members": ["c"], "children": ["the parts"],
                    "parent": "", "oversized": False, "composed_into": ""}},
        {"check_name": "candidate_blueprint", "label": "the parts",
         "detail": {"name": "the parts", "perspective": "deployment", "members": ["a", "b"], "children": [],
                    "parent": "the services", "oversized": False, "composed_into": ""}},
    ], surveyed_at="2026-10-09T00:00:00")
    for cluster in ("the services", "the parts"):
        reg.record_component_verdict("repo", "p", f"deployment::{cluster}", "accepted", "", "",
                                     verdict_target="blueprint", decided_by="x")


class TestAParentAndItsChildBlueprintInOnePress:
    """One kind, one plain identity per repository (architect's ruling, 2026-10-08): a nested child blueprint of
    the same kind needs a person's identifier. Accept is a decision only, so both can be accepted before either is
    written; the parent (the top of the reading) takes the plain identity, never the child written first."""

    def _press_all(self, reg):
        plan = ap.publish_plan(reg, "p")
        target = {"slug": "p", "paths": [c["path"] for c in plan["components"]["to_write"]],
                  "blueprints": [b["key"] for b in plan["blueprints"]["to_write"]]}
        return plan, ap.run_publish(reg, "p", target, "run-1")

    def test_with_the_childs_identifier_both_are_written_the_child_linked_and_the_next_plan_is_empty(self, world):
        reg, fake, outbox = world
        _seed_parent_and_child(reg)
        reg.record_component_verdict("repo", "p", "deployment::the parts", "accepted",
                                     ap.encode_blueprint_choices("", "parts"), "",
                                     verdict_target="blueprint", decided_by="x")
        plan, res = self._press_all(reg)
        assert [b["key"] for b in plan["blueprints"]["to_write"]] == [
            "deployment::the parts", "deployment::the services"], "children before the parent that links them"
        assert [(r["kind"], r["status"]) for r in res] == [("component", "done")] * 3 + [("blueprint", "done")] * 2, res
        child = reg.get_materialized_blueprint("repo", "p", "deployment", "the parts")
        parent = reg.get_materialized_blueprint("repo", "p", "deployment", "the services")
        assert parent["qualified_name"] == "SolutionBlueprint::repo::p::deployment", "the parent holds the plain identity"
        assert child["qualified_name"] == "SolutionBlueprint::repo::p::deployment::parts"
        c = fake.qn_to_guid["SolutionComponent::repo::p::src/c"]
        handed = {bp: sorted(g) for bp, g in outbox}
        assert handed[parent["guid"]] == sorted([c, child["guid"]]), "the parent gets its member and its child"
        _settled(reg)

    def test_without_one_the_child_is_held_back_before_the_press_and_never_takes_the_parents_identity(self, world):
        reg, fake, outbox = world
        _seed_parent_and_child(reg)
        plan, res = self._press_all(reg)
        assert [b["key"] for b in plan["blueprints"]["to_write"]] == ["deployment::the services"]
        held = plan["blueprints"]["needs_identifier"]
        assert [h["key"] for h in held] == ["deployment::the parts"]
        assert "give this one an identifier" in held[0]["words"] and "the services" in held[0]["words"]
        parent = reg.get_materialized_blueprint("repo", "p", "deployment", "the services")
        assert parent["qualified_name"] == "SolutionBlueprint::repo::p::deployment"
        assert not (reg.get_materialized_blueprint("repo", "p", "deployment", "the parts") or {}).get("guid")
        assert [r["status"] for r in res if r["kind"] == "blueprint"] == ["partial"], res   # its child is not linked
        after = ap.publish_plan(reg, "p")
        assert after["blueprints"]["to_write"] == [] and after["nothing"] is True
        assert [h["key"] for h in after["blueprints"]["needs_identifier"]] == ["deployment::the parts"]


    def test_the_accept_pane_asks_the_child_not_the_parent_for_an_identifier(self, world):
        """Same answer the plan acts on: the identity view the accept pane draws from."""
        from resource_explorer.surveyors.repo_survey_definition_adapter import _candidate_blueprints_results
        reg, fake, outbox = world
        _seed_parent_and_child(reg)
        ident = {b["cluster_name"]: b["identity"] for b in _candidate_blueprints_results(reg, "p")}
        assert ident["the services"]["needs_identifier"] is False
        assert ident["the parts"]["needs_identifier"] is True
        assert "already exists" not in ident["the parts"]["sentence"], "nothing is in Egeria yet: never said"


class TestAFlippedShapeBetweenTwoPresses:
    """The person flips the shape between two presses: the plan converges on the new shape and no pair the old
    shape wrote keeps the blueprint in the plan for ever."""

    def _flip(self, reg, cluster, shape):
        reg.record_component_verdict("repo", "p", f"deployment::{cluster}", "accepted",
                                     ap.encode_blueprint_choices(shape, ""), "",
                                     verdict_target="blueprint", decided_by="x")

    def test_container_then_contents(self, world):
        reg, fake, outbox = world
        _seed(reg, "A")                                   # default: A is the container of B
        _press(reg)
        _settled(reg)
        self._flip(reg, "A", "contents")
        plan = ap.publish_plan(reg, "p")
        assert [b["key"] for b in plan["blueprints"]["to_write"]] == ["deployment::A"], (
            "the flip asks for the blueprint again: B is now a direct member")
        res = ap.run_publish(reg, "p", {"slug": "p", "paths": [], "blueprints": ["deployment::A"]}, "run-2")
        assert [r["status"] for r in res] == ["done"], res
        a, b = (fake.qn_to_guid[f"SolutionComponent::repo::p::src/{s}"] for s in "ab")
        assert b in outbox[-1][1]
        _settled(reg)                                     # the A->B pair from press 1 is not a stale pair now

    def test_contents_then_container(self, world):
        reg, fake, outbox = world
        _seed(reg, "A")
        self._flip(reg, "A", "contents")                  # first press as contents: A and B direct members
        _press(reg)
        _settled(reg)
        self._flip(reg, "A", "container")
        plan = ap.publish_plan(reg, "p")
        assert [b["key"] for b in plan["blueprints"]["to_write"]] == ["deployment::A"], (
            "the flip asks for the composition A->B")
        res = ap.run_publish(reg, "p", {"slug": "p", "paths": [], "blueprints": ["deployment::A"]}, "run-2")
        assert [r["status"] for r in res] == ["done"], res
        a, b = (fake.qn_to_guid[f"SolutionComponent::repo::p::src/{s}"] for s in "ab")
        assert ("link_subcomponent", a, b) in fake.calls
        _settled(reg)

    def test_flipping_back_and_forth_still_settles(self, world):
        reg, fake, outbox = world
        _seed(reg, "A")
        _press(reg)
        for n, shape in enumerate(("contents", "container", "contents"), start=2):
            self._flip(reg, "A", shape)
            assert [b["key"] for b in ap.publish_plan(reg, "p")["blueprints"]["to_write"]] == ["deployment::A"], (
                f"a flip to {shape} has something to hand over")
            res = ap.run_publish(reg, "p", {"slug": "p", "paths": [], "blueprints": ["deployment::A"]}, f"run-{n}")
            assert [r["status"] for r in res] == ["done"], (shape, res)
            _settled(reg)


class TestAClientThatSendsNoChoiceKeepsTheStoredOne:
    """Round 2, item 1: Classic (and the CLI) post a blueprint verdict with no shape and no identifier. The newest
    verdict wins, so storing "" there reverted a Next contents choice to container and the next press wrote
    container links over contents members. A choice not sent is carried forward; only an explicit value
    changes it."""

    def _post(self, monkeypatch, reg, **choice):
        import resource_explorer.web.routes.curate as routes
        monkeypatch.setattr(routes, "_registry", lambda: reg)
        monkeypatch.setattr(routes, "_authorize_curation", lambda *a, **k: None)
        return routes.add_blueprint_verdict("repo", "p", routes.BlueprintVerdictCreate(
            perspective="deployment", cluster_name="A", verdict="accepted", **choice))

    def test_a_next_contents_choice_survives_a_classic_re_accept(self, world, monkeypatch):
        reg, fake, outbox = world
        _seed(reg, "A")
        self._post(monkeypatch, reg, shape="contents")        # Next: the person flips to contents
        _press(reg)
        _settled(reg)
        before = ap.publish_plan(reg, "p")
        self._post(monkeypatch, reg)                          # Classic: re-accept, no shape, no identifier
        assert ap.blueprint_choices(reg.get_component_verdicts("repo", "p")["deployment::A"])["shape"] == "contents"
        after = ap.publish_plan(reg, "p")
        assert after == before and after["nothing"] is True, "the plan is unchanged"
        assert not [c for c in fake.calls if c[0] == "link_subcomponent"], "no container links over contents"

    def test_an_explicit_default_clears_the_choice(self, world, monkeypatch):
        reg, fake, outbox = world
        _seed(reg, "A")
        self._post(monkeypatch, reg, shape="contents", identifier="second")
        for clear in ("", "default"):
            self._post(monkeypatch, reg, shape="contents")
            self._post(monkeypatch, reg, shape=clear)
            got = ap.blueprint_choices(reg.get_component_verdicts("repo", "p")["deployment::A"])
            assert got == {"shape": "", "identifier": "second"}, (clear, got)
        self._post(monkeypatch, reg, identifier="")
        assert ap.blueprint_choices(reg.get_component_verdicts("repo", "p")["deployment::A"]) == {"shape": "", "identifier": ""}

    def test_the_accept_pane_shows_the_stored_shape_not_the_default(self, world, monkeypatch):
        from resource_explorer.surveyors.repo_survey_definition_adapter import _candidate_blueprints_results
        reg, fake, outbox = world
        _seed(reg, "A")
        assert _candidate_blueprints_results(reg, "p")[0]["shape_plan"]["shape"] == "container"
        self._post(monkeypatch, reg, shape="contents")
        assert _candidate_blueprints_results(reg, "p")[0]["shape_plan"]["shape"] == "contents"


class TestAnOlderAttachedRecordStillShowsAFlip:
    """Round 2, item 3: a record written before members and sub-components were kept apart has only `guids`.
    Counting them as both hid a flip; they are now split by the shape that same press wrote."""

    def _as_old_record(self, reg, key="deployment::A"):
        rows = [p for p in reg.list_catalogue_commit_proofs("p") if p["proof"] == ap.P_ATTACHED and p["table_name"] == key]
        reg.append_catalogue_commit_proof("p", proof=ap.P_ATTACHED, node_kind=ap.NODE_PUBLISH_ITEM, table_name=key,
                                          element_guid=rows[-1]["element_guid"],
                                          detail={"guids": rows[-1]["detail"]["guids"]})

    def test_a_container_press_by_older_code_then_a_flip_to_contents_attaches_the_sub_component(self, world, monkeypatch):
        reg, fake, outbox = world
        _seed(reg, "A")
        _press(reg)                                          # container: A member, B composed
        self._as_old_record(reg)
        _settled(reg)
        reg.record_component_verdict("repo", "p", "deployment::A", "accepted", ap.encode_blueprint_choices("contents", ""),
                                     "", verdict_target="blueprint", decided_by="x")
        plan = ap.publish_plan(reg, "p")
        assert [(b["key"], b["unattached"]) for b in plan["blueprints"]["to_write"]] == [("deployment::A", 1)], (
            "B was composed, not a member: the flip must attach it")
        assert plan["blueprints"]["check_egeria"] == []
        ap.run_publish(reg, "p", {"slug": "p", "paths": [], "blueprints": ["deployment::A"]}, "run-2")
        _settled(reg)

    def test_a_contents_press_by_older_code_counts_its_guids_as_members_only(self, world):
        reg, fake, outbox = world
        _seed(reg, "A")
        reg.record_component_verdict("repo", "p", "deployment::A", "accepted", ap.encode_blueprint_choices("contents", ""),
                                     "", verdict_target="blueprint", decided_by="x")
        _press(reg)
        self._as_old_record(reg)
        members, composed, unknown = ap._attached(reg.list_catalogue_commit_proofs("p"), "deployment::A")
        b = fake.qn_to_guid["SolutionComponent::repo::p::src/b"]
        assert (b in members, composed, unknown) == (True, set(), False)

    def test_with_no_shape_row_both_are_kept_and_the_plan_says_check_egeria(self, world):
        reg, fake, outbox = world
        _seed(reg, "A")
        _press(reg)
        key = "deployment::A"
        bp = reg.get_materialized_blueprint("repo", "p", "deployment", "A")["guid"]
        # Simulate a blueprint pressed before shape rows existed: only an old-format attached row survives.
        with reg._conn() as conn:
            conn.execute("DELETE FROM catalogue_commit_proofs WHERE table_name=? AND proof IN ('shape','composition',?)",
                         (key, ap.P_ATTACHED))
        a = fake.qn_to_guid["SolutionComponent::repo::p::src/a"]
        reg.append_catalogue_commit_proof("p", proof=ap.P_ATTACHED, node_kind=ap.NODE_PUBLISH_ITEM, table_name=key,
                                          element_guid=bp, detail={"guids": [a]})
        members, composed, unknown = ap._attached(reg.list_catalogue_commit_proofs("p"), key)
        assert (members, composed, unknown) == ({a}, {a}, True)
        check = ap.publish_plan(reg, "p")["blueprints"]["check_egeria"]
        assert [(c["key"], c["words"]) for c in check] == [(key, ap.EARLIER_SHAPE_UNKNOWN)]
