"""The Resource Locator finds a file system by its narrative passages (owner, 2026-10-29: rank by best passage, search
only pressed file systems). A fake index per collection; the registry is a temp SQLite file; nothing reaches :5442.

Pinned: ranking by best passage, the citation and proposal label, only pressed file systems searched with the unread
counted in words, nothing pressed, no hit, an unreachable index said as a failure, and the route.
"""
from __future__ import annotations

import pytest
from trellis_vectorstore.base import SearchResult

from resource_explorer import narrative_context as nc
from resource_explorer.registry import FileSystemEntity, ProjectRegistry
from resource_explorer.surveyors.filesystem import narrative_docs as nd


class Index:
    """collection -> [SearchResult]; a collection in `down` raises, one in `missing` does not exist."""

    def __init__(self, by_collection=None, down=(), missing=()):
        self.by, self.down, self.missing, self.searched = by_collection or {}, set(down), set(missing), []

    def collection_name(self, slug, ctype):
        return f"{slug}_{ctype}"

    def collection_exists(self, c):
        return c not in self.missing

    def search(self, query, collections, top_k=None, min_score=None):
        self.searched.append(collections[0])
        if collections[0] in self.down:
            raise ConnectionError("down")
        self.floors = getattr(self, "floors", []) + [min_score]
        return [h for h in self.by.get(collections[0], []) if min_score is None or h.score >= min_score][:top_k]


def _hit(score, path="README.md", section="Intro", stated=None, run="w1", text="about"):
    return SearchResult(id="x", score=score, text=text, metadata={
        "file_path": path, "section": section, "walk_run": run, "stated_by": stated or f"{path} § {section}"})


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "loc.db"))
    for slug in ("alpha", "beta", "gamma", "delta"):
        r.register_filesystem(FileSystemEntity(slug=slug, display_name=slug.title(), local_mount_point=str(tmp_path)))
    return r


def _press(registry, *slugs):
    for s in slugs:
        registry.record_step_run(s, nd.STEP_KEY, "2026-10-29T00:00:00+00:00", entity_type="filesystem",
                                 source="resource-explorer", executor="local", metrics={"walk_run": "w1"})


def test_ranks_file_systems_by_their_best_passage_with_citation_and_label(registry):
    _press(registry, "alpha", "beta")
    idx = Index({"fs_alpha_narratives": [_hit(0.5, "a/README.md", "Scope")],
                 "fs_beta_narratives": [_hit(0.9, "b/CODEBOOK.txt", "Regions")]})
    out = nc.locate(registry, "regions", store_factory=lambda: idx)
    assert out["state"] == "found" and [m["slug"] for m in out["matches"]] == ["beta", "alpha"]
    top = out["matches"][0]
    assert top["citation"] == "stated by b/CODEBOOK.txt § Regions" and top["status"] == "proposal"
    assert "not a measured fact" in top["label"] and top["passage_id"] == "w1|b/CODEBOOK.txt|Regions"


def test_only_pressed_file_systems_are_searched_and_the_unread_are_counted(registry):
    _press(registry, "alpha")
    idx = Index({"fs_alpha_narratives": [_hit(0.7)]})
    out = nc.locate(registry, "q", store_factory=lambda: idx)
    assert idx.searched == ["fs_alpha_narratives"]
    assert out["not_read"] == ["beta", "delta", "gamma"]
    assert "3 file systems not read yet, not searched" in out["words"]


def test_nothing_pressed_says_so_and_searches_nothing(registry):
    idx = Index()
    out = nc.locate(registry, "q", store_factory=lambda: idx)
    assert out["state"] == "none_pressed" and not idx.searched
    assert "4 file systems not read yet, not searched" in out["words"]


def test_no_hit_does_not_claim_there_are_no_narratives(registry):
    _press(registry, "alpha")
    out = nc.locate(registry, "q", store_factory=lambda: Index({}))
    assert out["state"] == "no_match" and out["matches"] == []
    assert "no passage matched" in out["words"] and "may still say it in other words" in out["words"]


def test_an_unreachable_index_is_a_failure_not_an_absence(registry):
    _press(registry, "alpha", "beta")
    idx = Index({"fs_beta_narratives": [_hit(0.6)]}, down={"fs_alpha_narratives"})
    out = nc.locate(registry, "q", store_factory=lambda: idx)
    assert out["state"] == "found" and out["unavailable"] == ["alpha"]
    assert "1 could not be searched (index unreachable)" in out["words"]

    def boom():
        raise ConnectionError("down")
    out = nc.locate(registry, "q", store_factory=boom)
    assert out["state"] == "unavailable" and "could not be reached" in out["words"] and not out["matches"]


def test_an_old_press_row_is_cited_as_held_with_its_file_path(registry):
    _press(registry, "alpha")
    old = SearchResult(id="y", score=0.8, text="t", metadata={"stated_by": "README.md", "file_path": "d/README.md",
                                                              "section": "S", "walk_run": "w0"})
    out = nc.locate(registry, "q", store_factory=lambda: Index({"fs_alpha_narratives": [old]}))
    assert out["matches"][0]["citation"] == "stated by README.md (d/README.md)"


@pytest.mark.usefixtures("signed_in_caller")
def test_the_route(registry, monkeypatch):
    from fastapi.testclient import TestClient

    from resource_explorer.auth import create_access_token
    from resource_explorer.web.app import app
    from tests.zone_fakes import open_access

    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None, **k: setattr(self, "__dict__", registry.__dict__) or None)
    open_access(monkeypatch, caller_if_none="loc-tester")
    headers = {"Authorization": "Bearer " + create_access_token(user_id="loc-tester", egeria_token="tok")}
    client = TestClient(app)
    assert client.get("/api/filesystems/by-passages?q=%20", headers=headers).status_code == 422
    r = client.get("/api/filesystems/by-passages?q=regions", headers=headers)
    assert r.status_code == 200 and r.json()["state"] == "none_pressed"


def test_a_run_row_that_cannot_be_read_is_unknown_not_not_read_yet(registry, monkeypatch):
    _press(registry, "alpha")
    real = registry.query_step_runs

    def flaky(**k):
        if k.get("slug") == "beta":
            raise RuntimeError("registry hiccup")
        return real(**k)
    monkeypatch.setattr(registry, "query_step_runs", flaky)
    out = nc.locate(registry, "q", store_factory=lambda: Index({"fs_alpha_narratives": [_hit(0.7)]}))
    assert out["unknown"] == ["beta"] and "beta" not in out["not_read"]
    assert "whether its narratives were pressed could not be read" in out["words"]
    assert "1 file system whether" in out["unknown_words"]


def test_a_hit_below_the_calibrated_threshold_is_not_a_match(registry):
    _press(registry, "alpha")
    idx = Index({"fs_alpha_narratives": [_hit(0.01)]})
    out = nc.locate(registry, "q", store_factory=lambda: idx)
    assert idx.floors == [nc.min_score()] and nc.min_score() > 0.01
    assert out["state"] == "no_match" and out["matches"] == []
