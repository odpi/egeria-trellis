"""publish_plan reads architecture_materialized_blueprints a constant number of times, however many
blueprints there are (it used to make two reads per blueprint, twice over: 1,064 queries on 266 blueprints)."""
from __future__ import annotations

import json

import pytest

from resource_explorer import architecture_publish as ap
from resource_explorer.registry import Project, ProjectRegistry

pytestmark = pytest.mark.usefixtures("mock_egeria_client_connections")

READERS = ("get_materialized_blueprint", "get_materialized_blueprints",
           "get_materialized_blueprint_by_identity", "get_materialized_blueprints_by_identity")


def _registry(tmp_path, n):
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    reg.add(Project(slug="p", display_name="P repo", github_url="https://github.com/x/p", description=""))
    rows = []
    for i in range(n):
        persp = "physical" if i % 2 else "logical"
        rows.append({"check_name": "candidate_blueprint", "label": f"c{i}",
                     "detail": {"name": f"c{i}", "perspective": persp, "signal": "compose", "carrier": "compose.yaml",
                                "composed_into": "", "size": 0, "members": [], "children": [], "parent": "",
                                "oversized": False, "target_size": 8, "run_scope": "", "not_a_claim": True}})
    reg.upsert_finding("p", "architecture_blueprints", rows, surveyed_at="2026-09-03T00:00:00")
    for i in range(n):
        persp = "physical" if i % 2 else "logical"
        if i % 3 == 0:
            reg.record_component_verdict("repo", "p", f"{persp}::c{i}", "accepted", "", "",
                                         verdict_target="blueprint", decided_by="x")
        if i == 0:   # one already in Egeria: it holds its kind's plain identity
            reg.record_materialized_blueprint("repo", "p", persp, f"c{i}", "SolutionBlueprint::repo::p::logical", "g0")
    return reg


def _count(reg, monkeypatch):
    calls = []
    for name in READERS:
        if not hasattr(type(reg), name):
            continue
        real = getattr(type(reg), name)
        monkeypatch.setattr(type(reg), name, lambda self, *a, _r=real, _n=name, **k: calls.append(_n) or _r(self, *a, **k))
    return calls


@pytest.mark.parametrize("n", [5, 30])
def test_publish_plan_reads_the_blueprint_cache_at_most_twice(tmp_path, monkeypatch, n):
    reg = _registry(tmp_path, n)
    calls = _count(reg, monkeypatch)
    plan = ap.publish_plan(reg, "p")
    assert len(calls) <= 2, calls
    assert len(plan["blueprints"]["to_write"]) + len(plan["blueprints"]["needs_identifier"]) > 0


def test_the_identity_the_accept_pane_reads_is_unchanged(tmp_path):
    from resource_explorer.surveyors.repo_survey_definition_adapter import _candidate_blueprints_results
    reg = _registry(tmp_path, 6)
    by = {(b["perspective"], b["cluster_name"]): b["identity"] for b in _candidate_blueprints_results(reg, "p")}
    assert by[("logical", "c0")]["needs_identifier"] is False           # in Egeria: its own
    assert by[("logical", "c2")]["needs_identifier"] is True            # c0 holds the plain identity
    assert by[("logical", "c2")]["sentence"]
    # JSON-stable, for the before/after comparison in the handoff note
    json.dumps(ap.publish_plan(reg, "p"), sort_keys=True)


def test_publish_plan_runs_inside_a_read_memo_scope(tmp_path):
    """FactLayer.fact() opens read_memo.scope(); the frozen snapshot (mappingproxy) is unhashable, so it must
    never reach the memo key."""
    from resource_explorer import read_memo
    reg = _registry(tmp_path, 5)
    expected = ap.publish_plan(reg, "p")
    with read_memo.scope():
        assert ap.publish_plan(reg, "p") == expected


def test_publish_plan_output_matches_origin_main_on_the_30_blueprint_fixture(tmp_path):
    """Expected JSON was produced by origin/main (before this branch) on the same fixture."""
    import pathlib
    reg = _registry(tmp_path, 30)
    want = (pathlib.Path(__file__).parent / "fixtures" / "publish_plan_30_blueprints.json").read_text().strip()
    assert json.dumps(ap.publish_plan(reg, "p"), sort_keys=True) == want


def test_a_failed_publish_plan_does_not_leave_its_recovery_result_shared(tmp_path, monkeypatch):
    from resource_explorer import component_tree as ct
    from resource_explorer.surveyors import repo_survey_definition_adapter as A
    reg = _registry(tmp_path, 3)

    def boom(*a, **k):
        raise RuntimeError("boom")
    with monkeypatch.context() as m:
        m.setattr("resource_explorer.node_admission.apply_to_tree_components", boom)
        with pytest.raises(RuntimeError):
            ap.publish_plan(reg, "p")
    assert ct._shared_recovery_results.get() is None
    built = []
    real = A._architecture_recovery_results
    monkeypatch.setattr(A, "_architecture_recovery_results", lambda *a, **k: built.append(1) or real(*a, **k))
    ct._components(reg, "p")
    assert built == [1]          # built afresh, not served from the aborted call's result


def _put(reg, rid, persp, cluster, qn, at):
    with reg._conn() as conn:
        conn.execute(
            """INSERT INTO architecture_materialized_blueprints
               (id, entity_type, entity_slug, perspective, cluster_name, qualified_name, guid, materialized_at)
               VALUES (?, 'repo', 'p', ?, ?, ?, ?, ?)""", (rid, persp, cluster, qn, f"g-{rid}", at))


@pytest.mark.parametrize("rows, ask", [
    ([("1", "logical", "a", "SolutionBlueprint::repo::p::logical", "2026-01-01"),
      ("2", "logical", "b", "SolutionBlueprint::repo::p::logical", "2026-03-01"),
      ("3", "logical", "c", "SolutionBlueprint::repo::p::logical", "2026-02-01")],
     ["SolutionBlueprint::repo::p::logical", "SolutionBlueprint::repo::p::physical"]),
    ([], ["SolutionBlueprint::repo::p::logical"]),
    ([("1", "physical", "old", "physical::old", "2026-01-01"),            # legacy: not the identity string
      ("2", "physical", "new", "SolutionBlueprint::repo::p::physical", "2026-02-01")],
     ["physical::old", "SolutionBlueprint::repo::p::physical", "nothing"]),
])
def test_bulk_by_identity_equals_the_per_row_method(tmp_path, rows, ask):
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    for r in rows:
        _put(reg, *r)
    bulk = reg.get_materialized_blueprints_by_identity("repo", "p")
    for qn in ask:
        assert bulk.get(qn) == reg.get_materialized_blueprint_by_identity("repo", "p", qn)


def test_own_row_is_the_first_row_the_per_row_method_returns(tmp_path):
    """Two rows under one (perspective, cluster): the old per-blueprint read took the first row."""
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    _put(reg, "1", "logical", "a", "qn1", "2026-01-01")
    _put(reg, "2", "logical", "a", "qn2", "2026-02-01")
    first = reg.get_materialized_blueprint("repo", "p", "logical", "a")
    by_key, _by_identity = reg.get_materialized_blueprint_lookups("repo", "p")
    assert by_key["logical::a"] == first
