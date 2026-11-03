"""PI-072: component search in the Curate tree reads every component at once.

`/components/leaves?branch=` named one branch; the empty branch (the root, as `/components/tree?prefix=`
already treats it) returned nothing, so a search had no whole-repository list to look in. The root is
now every component, from the SAME function that returns one branch's, so a hit in a search and the
row in its branch cannot disagree."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resource_explorer.component_tree import leaves
from resource_explorer.registry import Project, ProjectRegistry

COMPS = [
    {"path": "pyegeria", "name": "pyegeria", "type": "Software Library", "confidence": 80,
     "proposals": [{"perspective": "physical", "run_label": "detect", "confidence": 80}]},
    {"path": "pyegeria/commands", "name": "commands", "type": "Console Command", "confidence": 70,
     "proposals": [{"perspective": "logical", "run_label": "coupling", "confidence": 70}]},
    {"path": "server", "name": "hive-server", "type": "Long Running Daemon", "confidence": 90},
    {"path": "", "name": "root", "type": "", "confidence": 0},
    {"path": "build", "name": "build", "type": "", "confidence": 0, "structural": True},
]


@pytest.fixture
def registry(tmp_path, monkeypatch):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="p", display_name="P repo", github_url="https://github.com/x/p", description=""))
    monkeypatch.setattr("resource_explorer.component_tree._components", lambda reg, slug: [dict(c) for c in COMPS])
    return r


def test_the_root_branch_is_every_component_and_a_branch_is_unchanged(registry):
    every = leaves(registry, "p", "")
    assert [l["path"] for l in every] == ["pyegeria", "pyegeria/commands", "server"]   # no grouping node, no pathless row
    assert [l["path"] for l in leaves(registry, "p", "pyegeria")] == ["pyegeria", "pyegeria/commands"]
    # the rows are the branch rows, byte for byte
    by = {l["path"]: l for l in every}
    assert by["pyegeria/commands"] == next(l for l in leaves(registry, "p", "pyegeria") if l["path"] == "pyegeria/commands")


def test_the_route_serves_the_root(registry, monkeypatch):
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    from resource_explorer.web.app import app
    out = TestClient(app).get("/api/projects/p/components/leaves", params={"branch": ""}).json()
    assert [l["path"] for l in out["leaves"]] == ["pyegeria", "pyegeria/commands", "server"]


# ── review round 2 ─────────────────────────────────────────────────────────

def test_the_finder_read_is_slim_capped_and_says_the_servers_total(registry):
    from resource_explorer.component_tree import finder_rows
    out = finder_rows(registry, "p", limit=2)
    assert out["total"] == 3 and out["shown"] == 2 and out["truncated"] is True
    assert [r["path"] for r in out["leaves"]] == ["pyegeria", "pyegeria/commands"]
    assert set(out["leaves"][0]) == {"path", "name", "type", "readings"}      # no verdicts, ports or proposals
    assert out["leaves"][1]["readings"] == ["logical"] and out["leaves"][0]["readings"] == ["physical"]
    full = finder_rows(registry, "p")
    assert full["truncated"] is False and full["total"] == 3 and full["leaves"][2]["readings"] == [""]


def test_the_route_serves_finder_mode_and_never_groups_the_whole_set(registry, monkeypatch):
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    from resource_explorer.web.app import app
    c = TestClient(app)
    slim = c.get("/api/projects/p/components/leaves", params={"branch": "", "groups": "false", "limit": 1}).json()
    assert slim["total"] == 3 and slim["truncated"] is True and len(slim["leaves"]) == 1 and "groups" not in slim
    plain = c.get("/api/projects/p/components/leaves", params={"branch": ""}).json()
    assert plain["groups"] == [] and len(plain["ungrouped"]) == 3


def test_an_alias_held_by_another_resource_is_not_moved_without_move(registry, monkeypatch):
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    monkeypatch.setattr("resource_explorer.auth.get_current_user", lambda request: {"user_id": "dan"})
    registry.add(Project(slug="q", display_name="Q repo", github_url="https://github.com/x/q", description=""))
    from resource_explorer.web.app import app
    c = TestClient(app)
    assert c.post("/api/aliases/", json={"alias": "Foo Bar", "project_slug": "p"}).status_code == 200
    r = c.post("/api/aliases/", json={"alias": "foo bar", "project_slug": "q"})
    assert r.status_code == 409 and "already used for p" in r.json()["detail"]
    assert registry.resolve_alias("Foo Bar") == "p"
    assert c.post("/api/aliases/", json={"alias": "foo bar", "project_slug": "p"}).status_code == 200   # same resource: fine
    assert c.post("/api/aliases/", json={"alias": "foo bar", "project_slug": "q", "move": True}).status_code == 200
    assert registry.resolve_alias("Foo Bar") == "q"
    assert [a["confirmed_by"] for a in registry.list_aliases("q")] == ["dan"]
