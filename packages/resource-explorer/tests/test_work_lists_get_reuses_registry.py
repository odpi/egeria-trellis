"""WorkLists.get() attaches dispositions through the registry the instance
already holds. It used to build a bare ProjectRegistry() per get, which re-ran
the registry's whole init on every call (a real cost on SQLite) and read the
default store rather than the one the WorkLists was given."""
from __future__ import annotations

import json

import pytest

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.work_lists import WorkLists


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "wl.db"))
    r.add(Project(slug="repo1", display_name="repo1", github_url="https://github.com/x/repo1"))
    r.add(Project(slug="repo2", display_name="repo2", github_url="https://github.com/x/repo2"))
    r.set_disposition("https://github.com/x/repo1", "adopt", reason="r", decided_by="dan")
    return r


def _created(registry):
    wls = WorkLists(registry)
    slug = wls.create("A list", ["repo1", "repo2"])["slug"]
    return wls, slug


def test_get_constructs_no_registry(registry, monkeypatch):
    wls, slug = _created(registry)
    constructed = []
    real_init = ProjectRegistry.__init__

    def counting_init(self, *a, **k):
        constructed.append(1)
        return real_init(self, *a, **k)

    monkeypatch.setattr(ProjectRegistry, "__init__", counting_init)
    for _ in range(3):
        assert wls.get(slug) is not None
    assert constructed == []


def test_get_output_is_byte_identical_and_carries_dispositions(registry):
    wls, slug = _created(registry)
    first = json.dumps(wls.get(slug), sort_keys=True, default=str)
    second = json.dumps(wls.get(slug), sort_keys=True, default=str)
    assert first == second
    by_slug = {m["entity_slug"]: m for m in json.loads(first)["members"]}
    assert by_slug["repo1"]["disposition"] == "adopt"
    assert by_slug["repo1"]["github_url"] == "https://github.com/x/repo1"
    assert by_slug["repo2"]["disposition"] == ""
    assert by_slug["repo2"]["github_url"] == "https://github.com/x/repo2"
