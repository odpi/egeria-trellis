"""Component review at the branch (designer's ports round, 2026-09-14).
Rows are branches; a branch verdict inherits and a component's own wins;
ports are a column, read from the artifacts, one owner each; the foot
sentence says what it looked in."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resource_explorer.component_tree import component_tree, group_leaves, leaves, resolve_verdict, topology_sentence
from resource_explorer.registry import Project, ProjectRegistry


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="p", display_name="P repo", github_url="https://github.com/x/p", description=""))
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    monkeypatch.setattr("resource_explorer.auth.get_current_user", lambda request: {"user_id": "peterprofile"})
    monkeypatch.setattr("resource_explorer.web.routes.curate._authorize_curation", lambda *a, **k: None)
    from resource_explorer.web.app import app
    return TestClient(app)


COMPS = [
    {"path": "pyegeria", "name": "pyegeria", "type": "Software Library", "confidence": 80},
    {"path": "pyegeria/commands", "name": "commands", "type": "Console Command", "confidence": 70},
    {"path": "pyegeria/commands/cat", "name": "cat", "type": "", "confidence": 40},
    {"path": "pyegeria/utils", "name": "utils", "type": "", "confidence": 30},
    {"path": "tests", "name": "tests", "type": "", "confidence": 20},
    {"path": "server", "name": "hive-server", "type": "Long Running Daemon", "confidence": 90},
    {"path": "", "name": "root", "type": "", "confidence": 0},          # not a branch
    {"path": "build", "name": "build", "type": "", "confidence": 0, "structural": True},   # a grouping node
]


@pytest.fixture
def seeded(registry, monkeypatch):
    monkeypatch.setattr("resource_explorer.component_tree._components", lambda reg, slug: [dict(c) for c in COMPS])
    registry.upsert_finding("p", "architecture_interfaces", [
        {"check_name": "port:8000", "label": "in", "summary": "", "detail": {"kind": "port", "component": "hive-server", "port": "8000", "direction": "in", "protocol": "http"}},
        {"check_name": "port:9092", "label": "in", "summary": "", "detail": {"kind": "port", "component": "nobody", "port": "9092", "direction": "in"}},
        {"check_name": "wire:x", "label": "declared-dependency", "summary": "", "detail": {"kind": "wire", "source": "a", "target": "b"}},
    ])
    return registry


class TestTheBranches:
    def test_rows_are_branches_with_what_a_curator_needs_before_opening(self, seeded):
        tr = component_tree(seeded, "p")
        by = {b["path"]: b for b in tr["branches"]}
        assert set(by) == {"pyegeria", "tests", "server", "build"}
        p = by["pyegeria"]
        assert p["components"] == 4 and p["children"] == 3 and p["low_confidence"] == 2
        assert p["types"] == {"Software Library": 1, "Console Command": 1}
        assert p["grouping_only"] is False and by["build"]["grouping_only"] is True
        assert tr["total_components"] == 6        # the root '' and the structural node are not components to review

    def test_ports_are_a_column_with_one_owner_each(self, seeded):
        tr = component_tree(seeded, "p")
        by = {b["path"]: b for b in tr["branches"]}
        assert by["server"]["ports"] == 1 and by["server"]["own_ports"][0]["name"] == "8000"
        assert tr["ports"] == 2 and tr["wires"] == 1 and tr["topology"] == ""
        assert tr["ports_owned"] == 1 and tr["ports_unowned"] == 1 and tr["components_with_ports"] == 1
        assert tr["topology_totals"] == "2 ports across 1 component · 1 wire · 1 not attributable to any shown component, counted apart."
        assert by["pyegeria"]["min_confidence"] == 30
        lv = {l["path"]: l for l in leaves(seeded, "p", "server")}
        assert lv["server"]["ports"][0]["protocol"] == "http"

    def test_no_declared_topology_says_what_it_looked_in(self, registry):
        s = topology_sentence(registry, "p", 0, 0)
        assert s.startswith("No declared topology — P repo declares no ports and no wires.")
        assert "Dockerfiles, compose files and API documents" in s


class TestVerdictsInherit:
    def test_a_branch_verdict_inherits_and_a_components_own_wins(self, seeded):
        seeded.record_component_verdict("repo", "p", "pyegeria", "accepted", decided_by="a")
        seeded.record_component_verdict("repo", "p", "pyegeria/utils", "rejected", decided_by="b")
        lv = {l["path"]: l["verdict"] for l in leaves(seeded, "p", "pyegeria")}
        assert lv["pyegeria"]["verdict"] == "accepted" and lv["pyegeria"]["inherited_from"] == ""
        assert lv["pyegeria/commands/cat"] == {**lv["pyegeria/commands/cat"], "verdict": "accepted", "inherited_from": "pyegeria"}
        assert lv["pyegeria/utils"]["verdict"] == "rejected" and lv["pyegeria/utils"]["inherited_from"] == ""
        tr = component_tree(seeded, "p")
        p = next(b for b in tr["branches"] if b["path"] == "pyegeria")
        assert (p["accepted"], p["rejected"], p["undecided"]) == (3, 1, 0)
        assert tr["accepted"] == 3 and tr["reviewed"] == 2      # reviewed counts own rows only

    def test_resolution_is_longest_prefix(self):
        v = {"a": {"verdict": "accepted"}, "a/b": {"verdict": "rejected"}}
        assert resolve_verdict("a/b/c", v)["inherited_from"] == "a/b"
        assert resolve_verdict("a/x", v)["inherited_from"] == "a"
        assert resolve_verdict("z", v) is None


class TestALeafWithComponentsBeneathIt:
    """Brief A follow-up 2: a leaf row that is itself a component with children carries the branch row's choice,
    so the leaf rows say how far a verdict on them reaches (the same counts a branch row carries)."""

    def test_a_leaf_says_how_many_components_sit_beneath_it(self, seeded):
        lv = {l["path"]: l for l in leaves(seeded, "p", "pyegeria")}
        assert lv["pyegeria/commands"]["children"] == 1          # cat
        assert lv["pyegeria/commands"]["reach_low"] == 1         # cat at 40%
        assert lv["pyegeria"]["children"] == 3
        assert lv["pyegeria/utils"]["children"] == 0
        tr = {b["path"]: b for b in component_tree(seeded, "p")["branches"]}
        assert lv["pyegeria"]["children"] == tr["pyegeria"]["children"], "the leaf and its branch row agree"
        assert lv["pyegeria"]["reach_low"] == tr["pyegeria"]["low_confidence"]

    def test_already_accepted_counts_follow_the_resolved_verdicts(self, seeded):
        from resource_explorer.component_tree import ONLY_THIS
        seeded.record_component_verdict("repo", "p", "pyegeria/commands", "accepted", ONLY_THIS, decided_by="a")
        lv = {l["path"]: l for l in leaves(seeded, "p", "pyegeria")}
        assert lv["pyegeria/commands"]["reach_accepted"] == 1, "its own; cat does not inherit an 'only' row"
        seeded.record_component_verdict("repo", "p", "pyegeria/commands", "accepted", "", decided_by="a")
        lv = {l["path"]: l for l in leaves(seeded, "p", "pyegeria")}
        assert lv["pyegeria/commands"]["reach_accepted"] == 2


class TestAgreementAndWithdrawal:
    """RULING-WHAT-A-VERDICT-IS-ABOUT.md §2a-§2c: a component is proposals
    kept together by path, not one proposal chosen over the other."""

    def test_agreement_rolls_up_to_the_branch_for_sorting(self, registry, monkeypatch):
        comps = [
            {"path": "pyegeria/commands", "name": "cli", "type": "Console Command",
             "confidence": 75, "agreement": True},
            {"path": "pyegeria/utils", "name": "utils", "type": "", "confidence": 90,
             "agreement": False},
        ]
        monkeypatch.setattr("resource_explorer.component_tree._components", lambda reg, slug: comps)
        tr = component_tree(registry, "p")
        by = {b["path"]: b for b in tr["branches"]}
        assert by["pyegeria"]["agreement_count"] == 1

    def test_withdrawal_flags_an_accepted_verdict_without_touching_it(self, registry, monkeypatch):
        comps = [
            {"path": "pyegeria/commands", "name": "cli", "type": "Console Command",
             "confidence": 75, "withdrawn_by": ["coupling"],
             "proposals": [{"run_label": "detect", "type": "Console Command", "confidence": 75}]},
        ]
        monkeypatch.setattr("resource_explorer.component_tree._components", lambda reg, slug: comps)
        registry.record_component_verdict("repo", "p", "pyegeria/commands", "accepted", decided_by="a")
        lv = {l["path"]: l for l in leaves(registry, "p", "pyegeria")}
        row = lv["pyegeria/commands"]
        assert row["verdict"]["verdict"] == "accepted"          # flag, do not invalidate
        assert row["withdrawn_by"] == ["coupling"]


class TestLeafGrouping:
    """2026-09-17: a branch's leaves regrouped by scope-hierarchy cluster —
    the same `scope_hierarchy.derive()` clustering.py's blueprint proposals
    already read, applied directly to the branch's own leaf paths. A group
    of one collapses nothing (`scope_hierarchy.MIN_GROUP`) and stays
    ungrouped, same as a component whose branch has no further structure."""

    def test_siblings_group_under_their_shared_ancestor(self, seeded):
        lv = leaves(seeded, "p", "pyegeria")
        groups, ungrouped = group_leaves(lv)
        assert len(groups) == 1
        g = groups[0]
        assert g["name"] == "pyegeria"
        assert {m["path"] for m in g["members"]} == {
            "pyegeria/commands", "pyegeria/commands/cat", "pyegeria/utils"}
        assert {m["path"] for m in ungrouped} == {"pyegeria"}      # a group of one -> ungrouped
        assert (g["accepted"], g["rejected"], g["undecided"]) == (0, 0, 3)

    def test_group_verdict_counts_reflect_recorded_verdicts(self, seeded):
        # "pyegeria/commands/cat" inherits its parent's verdict (`resolve_verdict`),
        # same as at the branch level -- so accepting "commands" accepts both it
        # and its child here.
        seeded.record_component_verdict("repo", "p", "pyegeria/commands", "accepted", decided_by="a")
        seeded.record_component_verdict("repo", "p", "pyegeria/utils", "rejected", decided_by="a")
        lv = leaves(seeded, "p", "pyegeria")
        groups, _ = group_leaves(lv)
        g = groups[0]
        assert (g["accepted"], g["rejected"], g["undecided"]) == (2, 1, 0)

    def test_a_branch_with_no_qualifying_group_returns_everything_ungrouped(self, seeded):
        lv = leaves(seeded, "p", "server")             # a single leaf, no siblings
        groups, ungrouped = group_leaves(lv)
        assert groups == []
        assert {m["path"] for m in ungrouped} == {"server"}

    def test_an_oversized_first_pass_group_is_subdivided_like_a_blueprint_cluster(self, registry, monkeypatch):
        # 12 siblings directly under "pkg/a" (over TARGET_CLUSTER_SIZE=10) plus
        # 3 more nested one level deeper under "pkg/a/sub" -- re-deriving within
        # "pkg/a"'s own members should split "pkg/a/sub"'s three out, the same
        # way clustering.py's `_subdivide()` finds a level a full-tree pass had
        # no reason to.
        comps = [{"path": f"pkg/a/f{i}", "name": f"f{i}", "type": "", "confidence": 90} for i in range(12)]
        comps += [{"path": f"pkg/a/sub/g{i}", "name": f"g{i}", "type": "", "confidence": 90} for i in range(3)]
        monkeypatch.setattr("resource_explorer.component_tree._components", lambda reg, slug: comps)
        lv = leaves(registry, "p", "pkg")
        groups, ungrouped = group_leaves(lv)
        names = {g["name"]: {m["path"] for m in g["members"]} for g in groups}
        assert "pkg/a/sub" in names and names["pkg/a/sub"] == {f"pkg/a/sub/g{i}" for i in range(3)}
        assert sum(len(v) for v in names.values()) + len(ungrouped) == 15


class TestTheRoute:
    def test_a_branch_verdict_is_one_row_and_nothing_is_queued(self, client, seeded):
        r = client.post("/api/projects/p/components/verdicts", json={"scope_locators": ["pyegeria/"], "verdict": "accepted"})
        assert r.status_code == 200, r.text
        out = r.json()
        assert len(out["verdicts"]) == 1 and out["verdicts"][0]["scope_locator"] == "pyegeria"
        assert out["queued"] == 0 and out["run_id"] is None and out["activity_id"] is None
        assert seeded.list_runs() == []                 # Accept is a decision; Publish writes
        tree = client.get("/api/projects/p/components/tree").json()
        p = next(b for b in tree["branches"] if b["path"] == "pyegeria")
        assert p["verdict"]["verdict"] == "accepted" and p["accepted"] == 4
        lv = client.get("/api/projects/p/components/leaves", params={"branch": "pyegeria"}).json()["leaves"]
        assert all(l["verdict"]["verdict"] == "accepted" for l in lv)

    def test_the_leaves_route_also_exposes_groups_and_ungrouped(self, client, seeded):
        out = client.get("/api/projects/p/components/leaves", params={"branch": "pyegeria"}).json()
        assert set(out) == {"branch", "leaves", "groups", "ungrouped"}
        assert len(out["leaves"]) == 4                              # unchanged flat shape
        assert len(out["groups"]) == 1 and out["groups"][0]["name"] == "pyegeria"
        assert {l["path"] for l in out["ungrouped"]} == {"pyegeria"}

    def test_reject_queues_nothing_and_anonymous_is_refused(self, client, seeded, monkeypatch):
        r = client.post("/api/projects/p/components/verdicts", json={"scope_locators": ["tests"], "verdict": "rejected"})
        assert r.status_code == 200 and r.json()["run_id"] is None
        monkeypatch.setattr("resource_explorer.auth.get_current_user", lambda request: None)
        assert client.post("/api/projects/p/components/verdicts", json={"scope_locators": ["tests"], "verdict": "accepted"}).status_code == 401
