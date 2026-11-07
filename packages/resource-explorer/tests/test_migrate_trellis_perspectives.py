"""scripts/migrate_trellis_perspectives.py: plan arithmetic and the never-write-twice rule.

Perspective ScopedBy links have no reconciler, so a duplicate is permanent. These pin that the plan and the
apply both skip a link the target already holds, and that the source is never retired while any of its
links is missing from the target. No Egeria: the clients are fakes.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "migrate_trellis_perspectives.py"
_spec = importlib.util.spec_from_file_location("migrate_trellis_perspectives", _PATH)
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)

DO, OWNER = mig.MIGRATIONS[0]["source_guid"], mig.MIGRATIONS[0]["target_guid"]
AD, ADMN = mig.MIGRATIONS[1]["source_guid"], mig.MIGRATIONS[1]["target_guid"]


def _el(guid):
    return {"elementHeader": {"guid": guid}, "properties": {"displayName": f"Q {guid}"}}


class FakeExplorer:
    def __init__(self, scopes, page=2, fail_link=()):
        self.scopes = {k: list(v) for k, v in scopes.items()}
        self.page, self.fail_link, self.writes, self.bodies = page, set(fail_link), [], []

    def get_scoped_elements(self, scope_guid, body=None, output_format="JSON"):
        start, size = body["startFrom"], min(body["pageSize"], self.page)   # short pages, never "the last"
        items = self.scopes.get(scope_guid, [])[start:start + size]
        return [_el(g) for g in items] or "No elements found"

    def add_scope_to_element(self, scoped_by_guid, element_guid, body=None):
        self.writes.append((scoped_by_guid, element_guid))
        self.bodies.append(body)
        if element_guid not in self.fail_link:
            self.scopes.setdefault(scoped_by_guid, []).append(element_guid)


class FakeActor:
    def __init__(self):
        self.deleted = []

    def delete_perspective(self, guid):
        self.deleted.append(guid)


@pytest.fixture
def resolve(monkeypatch):
    names = {m["source_qn"]: m["source_guid"] for m in mig.MIGRATIONS}
    names.update({m["target_qn"]: m["target_guid"] for m in mig.MIGRATIONS})
    import resource_explorer.egeria_lookup as lookup

    monkeypatch.setattr(lookup, "guid_by_qualified_name", lambda expert, qn: names.get(qn))
    return names


def test_plan_moves_only_what_the_target_lacks(resolve):
    ex = FakeExplorer({DO: ["a", "b", "c"], OWNER: ["b", "x"], AD: ["d"], ADMN: []})
    plan = mig.build_plan(None, ex)
    owner = plan[0]
    assert [e["guid"] for e in owner["to_move"]] == ["a", "c"]
    assert [e["guid"] for e in owner["already_on_target"]] == ["b"]
    assert (owner["source_links"], owner["target_links_before"], owner["target_links_after"]) == (3, 2, 4)
    assert ex.writes == []                       # a plan writes nothing


def test_apply_skips_held_links_and_retires_after_verifying(resolve):
    ex = FakeExplorer({DO: ["a", "b", "c"], OWNER: ["b"], AD: ["d"], ADMN: ["d"]})
    plan = mig.build_plan(None, ex)
    ex.scopes[OWNER].append("a")                 # someone linked "a" between plan and apply
    actor = FakeActor()
    mig.apply_plan(plan, ex, actor)
    assert ex.writes == [(OWNER, "c")]           # "a" and "b" never written twice; "d" was already there
    assert actor.deleted == [DO, AD]


def test_source_is_kept_when_a_link_did_not_land(resolve):
    ex = FakeExplorer({DO: ["a"], OWNER: [], AD: [], ADMN: []}, fail_link={"a"})
    plan = mig.build_plan(None, ex)
    actor = FakeActor()
    with pytest.raises(mig.Refused):
        mig.apply_plan(plan, ex, actor)
    assert actor.deleted == []


def test_an_already_retired_source_is_nothing_to_do(resolve):
    del resolve["Perspective::Data Owner"]
    ex = FakeExplorer({OWNER: ["a"], AD: [], ADMN: []})
    plan = mig.build_plan(None, ex)
    assert plan[0]["source_present"] is False and plan[0]["to_move"] == []
    actor = FakeActor()
    mig.apply_plan(plan, ex, actor)
    assert DO not in actor.deleted


def test_a_different_target_guid_refuses(resolve):
    resolve["Perspective::Owner"] = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(mig.Refused):
        mig.build_plan(None, FakeExplorer({}))


def test_every_link_sends_the_scoped_by_properties_body():
    """pyegeria's default body says properties class "ScopedBy", which Egeria rejects with HTTP 400 (live,
    2026-11-07). Every link must send Egeria's class name, ScopedByProperties."""
    m = mig.MIGRATIONS[0]
    explorer = FakeExplorer({m["source_guid"]: ["q1", "q2"], m["target_guid"]: []})
    plan = [{**m, "source_present": True, "to_move": [{"guid": "q1", "name": "Q1"}, {"guid": "q2", "name": "Q2"}],
             "already_on_target": []}]
    mig.apply_plan(plan, explorer, FakeActor())
    assert explorer.bodies and all(b == {"class": "NewRelationshipRequestBody",
                                         "properties": {"class": "ScopedByProperties"}} for b in explorer.bodies)
