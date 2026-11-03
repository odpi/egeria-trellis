"""Brief A section 3 and 7: the composition read on Egeria 6.2, and the child-blueprint existence check.

6.2 returns a component with no children with NO children key at all. `_child_guids` rightly reads that as
"the read did not say", so `link_sub_components` wrote nothing and all six compositions on egeria_git's
OMAG-Server-Platform stayed 'unconfirmed'. The container's SolutionComposition relationships are now read
explicitly; only a typed answer ("No elements found", or a list of related elements) says "none".
"""
from __future__ import annotations

import pytest

from resource_explorer.surveyors.arch_recovery.blueprint_materializer import BlueprintMaterializer

PLATFORM = "11111111-1111-1111-1111-111111111111"
KIDS = [f"22222222-2222-2222-2222-22222222222{i}" for i in range(3)]


class Fake62:
    """6.2: the element read has no children key; the relationship read answers."""

    def __init__(self, related="none", element_has_key=False):
        self.related = related                     # "none" | "list" | "raises" | "text" | "noguid" | "full"
        self.linked: list[str] = []
        self.element_has_key = element_has_key
        self.calls: list[tuple] = []

    def get_solution_component_by_guid(self, guid, **kw):
        self.calls.append(("element", guid))
        out = {"elementHeader": {"guid": guid}, "properties": {"qualifiedName": "x"}}
        if self.element_has_key:
            out["subComponents"] = [{"relatedElement": {"elementHeader": {"guid": g}}} for g in self.linked]
        return out

    def link_subcomponent(self, container, child, body):
        self.calls.append(("link", container, child))
        self.linked.append(child)

    def get_related_metadata_elements(self, guid, relationship_type, body, **kw):
        self.calls.append(("related", guid, relationship_type, body.get("pageSize"), body.get("startFrom"), sorted(kw)))
        self.reverse = getattr(self, "reverse", [])
        if self.related == "raises":
            raise RuntimeError("503 from the view server")
        if self.related == "text":
            return "something else entirely"
        if self.related == "noguid":
            return {"elementList": [{"element": {}, "elementAtEnd1": False}]}
        if self.related == "full":
            return {"elementList": [{"element": {"elementGUID": f"g{i}"}, "elementAtEnd1": False} for i in range(body["pageSize"])]}
        if not self.linked:
            return "No elements found"
        return {"startingElement": {}, "elementList": [{"element": {"elementGUID": g}, "elementAtEnd1": False} for g in self.linked]
                + [{"element": {"elementGUID": g}, "elementAtEnd1": True} for g in self.reverse]}


def _mat(fake):
    m = BlueprintMaterializer(platform_url="https://fake")
    m._solution_architect = fake
    m._automated_curation = fake
    m._metadata_expert = fake
    m._connect = lambda: None
    return m


CHILDREN = [(g, f"qn-{i}") for i, g in enumerate(KIDS)]


class TestCompositionReadOnSixPointTwo:
    def test_a_component_with_no_children_and_no_key_is_known_to_have_none_and_the_links_are_written(self):
        fake = Fake62()
        rows = _mat(fake).link_sub_components(PLATFORM, "qn-platform", CHILDREN)
        assert [r["status"] for r in rows] == ["linked"] * 3
        assert all(r["read_back"] for r in rows)
        assert fake.linked == KIDS
        # pyegeria sends only the body, so the page is asked for IN the body (and no page kwargs are relied on)
        assert ("related", PLATFORM, "SolutionComposition", 1000, 0, []) in fake.calls

    def test_a_reverse_pair_is_not_read_as_a_child(self):
        fake = Fake62()
        fake.linked = []
        fake.reverse = [KIDS[0]]                 # the container is nested UNDER kid 0, not its parent
        fake.get_related_metadata_elements = lambda guid, rel, body, **kw: {"elementList": [
            {"element": {"elementGUID": KIDS[0]}, "elementAtEnd1": True}]}
        rows = _mat(fake).link_sub_components(PLATFORM, "qn-platform", CHILDREN)
        assert KIDS[0] in fake.linked            # written as a child, not skipped as "already present"
        assert rows[0]["status"] == "unconfirmed"  # and the reverse pair is not read back as proof of it

    def test_an_entry_that_does_not_say_its_end_is_could_not_tell(self):
        fake = Fake62()
        fake.get_related_metadata_elements = lambda guid, rel, body, **kw: {"elementList": [{"element": {"elementGUID": KIDS[0]}}]}
        rows = _mat(fake).link_sub_components(PLATFORM, "qn-platform", CHILDREN)
        assert [r["status"] for r in rows] == ["unconfirmed"] * 3 and fake.linked == []

    def test_pairs_the_relationship_read_shows_are_not_rewritten(self):
        fake = Fake62()
        fake.linked = [KIDS[0]]
        rows = _mat(fake).link_sub_components(PLATFORM, "qn-platform", CHILDREN)
        assert [r["status"] for r in rows] == ["already_present", "linked", "linked"]
        assert fake.linked == KIDS and [c for c in fake.calls if c[0] == "link"] == [
            ("link", PLATFORM, KIDS[1]), ("link", PLATFORM, KIDS[2])]

    @pytest.mark.parametrize("related", ["raises", "text", "noguid", "full"])
    def test_an_unreadable_or_untyped_relationship_answer_is_could_not_tell_never_none(self, related):
        fake = Fake62(related=related)
        rows = _mat(fake).link_sub_components(PLATFORM, "qn-platform", CHILDREN)
        assert [r["status"] for r in rows] == ["unconfirmed"] * 3
        assert not any(r["read_back"] for r in rows)
        assert fake.linked == []                     # nothing written on a guess: the relationship is multi-link

    def test_no_relationship_client_keeps_the_old_answer(self):
        fake = Fake62()
        m = _mat(fake)
        m._metadata_expert = None
        rows = m.link_sub_components(PLATFORM, "qn-platform", CHILDREN)
        assert [r["status"] for r in rows] == ["unconfirmed"] * 3 and fake.linked == []

    def test_an_element_read_that_does_carry_the_key_is_trusted_without_a_second_read(self):
        fake = Fake62(element_has_key=True)
        guids, known = _mat(fake).sub_component_guids(PLATFORM)
        assert (guids, known) == (set(), True)
        assert not [c for c in fake.calls if c[0] == "related"]

    def test_an_unreadable_container_still_raises(self):
        class Gone(Fake62):
            def get_solution_component_by_guid(self, guid, **kw):
                return "No elements found"
        rows = _mat(Gone()).link_sub_components(PLATFORM, "qn-platform", CHILDREN)
        assert [r["status"] for r in rows] == ["unread"] * 3


class _Reg:
    def __init__(self, cache):
        self.cache = cache

    def get_materialized_blueprint(self, et, slug, persp, name):
        return {"guid": self.cache[name]} if name in self.cache else None


class FakeBlueprints:
    def __init__(self, answers):
        self.answers = answers                       # guid -> "present" | "gone" | "raises"
        self.reads: list[str] = []

    def get_solution_blueprint_by_guid(self, guid, **kw):
        self.reads.append(guid)
        a = self.answers[guid]
        if a == "raises":
            raise RuntimeError("503")
        if a == "gone":
            return "No elements found"
        return {"elementHeader": {"guid": guid}, "properties": {"qualifiedName": "q"}}


class TestChildBlueprintExistenceCheck:
    def test_a_child_is_verified_by_guid_and_gone_is_reported_not_linked(self):
        fake = FakeBlueprints({"g-ok": "present", "g-gone": "gone"})
        m = _mat(fake)
        resolved, unmet = m.resolve_child_blueprint_guids(
            _Reg({"ok": "g-ok", "gone": "g-gone"}), "repo", "p", "deployment", ["ok", "gone", "never"], verify=True)
        assert resolved == {"ok": "g-ok"}
        assert unmet == ["gone", "never"]
        assert m.child_blueprints_gone == {"gone": "g-gone"} and m.child_blueprints_unreadable == {}
        assert fake.reads == ["g-ok", "g-gone"]      # a child with no cached GUID is not read

    def test_could_not_read_stays_distinct_from_gone_and_is_not_linked_either(self):
        fake = FakeBlueprints({"g-1": "raises"})
        m = _mat(fake)
        resolved, unmet = m.resolve_child_blueprint_guids(_Reg({"c": "g-1"}), "repo", "p", "deployment", ["c"], verify=True)
        assert resolved == {} and unmet == ["c"]
        assert m.child_blueprints_gone == {} and "RuntimeError" in m.child_blueprints_unreadable["c"] or \
            "BlueprintMaterializationError" in m.child_blueprints_unreadable["c"]

    def test_without_verify_the_cache_is_trusted_as_before(self):
        fake = FakeBlueprints({})
        resolved, unmet = _mat(fake).resolve_child_blueprint_guids(_Reg({"c": "g-1"}), "repo", "p", "deployment", ["c"])
        assert resolved == {"c": "g-1"} and unmet == [] and fake.reads == []

    def test_the_write_asks_for_the_check_and_reports_a_gone_child(self, tmp_path, monkeypatch):
        from resource_explorer.registry import Project, ProjectRegistry
        from resource_explorer.surveyors.arch_recovery import blueprint_materializer as bm
        from resource_explorer.workflows import curate

        reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
        reg.add(Project(slug="p", display_name="P", github_url="https://github.com/x/p"))
        for name, kids in (("root", ["kid"]), ("kid", [])):
            reg.upsert_finding("p", "architecture_blueprints", [{
                "check_name": "candidate_blueprint", "label": name,
                "detail": {"name": name, "perspective": "physical", "members": [], "children": kids,
                           "parent": "" if name == "root" else "root", "oversized": False, "composed_into": ""}}],
                surveyed_at="2026-10-09T00:00:00")
        reg.record_materialized_blueprint("repo", "p", "physical", "kid", "SolutionBlueprint::x::kid", "g-gone")
        fake = FakeBlueprints({"g-gone": "gone"})
        seen = {}

        class M(bm.BlueprintMaterializer):
            def __init__(self, registry=None):
                super().__init__(platform_url="https://fake", registry=registry)
                self._solution_architect = fake
                self._automated_curation = fake
                self._connect = lambda: None

            def materialize_blueprint_element(self, *a, **k):
                return {"status": "materialized", "guid": "bp-root", "qualified_name": "q"}

            def blueprint_member_guids(self, guid):
                return None

            def resolve_child_blueprint_guids(self, *a, **k):
                seen.update(k)
                return super().resolve_child_blueprint_guids(*a, **k)

        monkeypatch.setattr(bm, "BlueprintMaterializer", M)
        monkeypatch.setattr("resource_explorer.egeria_outbox.enqueue_blueprint_members", lambda *a, **k: [])
        out = curate.materialize_blueprint_if_accepted(reg, "repo", "p", "physical", "root", "accepted")
        assert seen == {"verify": True}
        assert out["children_gone"] == {"kid": "g-gone"} and out["unmaterialized_children"] == ["kid"]
        assert out["status"] == "partial"
        assert "attached_guids" in out and "g-gone" not in out["attached_guids"]   # a gone child was not handed over
