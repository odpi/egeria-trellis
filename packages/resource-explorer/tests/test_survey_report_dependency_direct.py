"""Walk 2026-10-12: egeria_git's report read "java · 70 · DIRECT 0". No parser ever writes dep_type "direct", so the
count was 0 for every ecosystem. Direct-ness is derived only where a parser records the transitive marker (go)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import Project, ProjectRegistry


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.add(Project(slug="myproj", display_name="My Project", github_url="https://github.com/test/myproj"))
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    from resource_explorer.web.app import app
    return TestClient(app)


def _dep(eco, name, dep_type):
    return {"project_slug": "myproj", "dep_name": name, "dep_version": "1", "dep_type": dep_type,
            "ecosystem": eco, "source_file": "f"}


def test_ecosystem_without_a_direct_marker_is_not_determined(client, registry):
    registry.upsert_dependencies("myproj", [_dep("java", "a:b", "runtime"), _dep("java", "c:d", "test")])
    deps = client.get("/api/egeria/myproj/survey-report").json()["dependencies"]
    assert deps == [{"ecosystem": "java", "count": 2, "direct": None}]


def test_go_direct_is_count_minus_indirect(client, registry):
    registry.upsert_dependencies("myproj", [_dep("go", "a", "runtime"), _dep("go", "b", "indirect"),
                                            _dep("go", "c", "runtime")])
    deps = client.get("/api/egeria/myproj/survey-report").json()["dependencies"]
    assert deps == [{"ecosystem": "go", "count": 3, "direct": 2}]
