"""First index, asked first (REPLY-DESIGNER-GROUP-SCHEDULING-AND-FIRST-INDEX-2026-10-19 §4 + the 2026-10-20 consent reply).

Never touches the shared registry (every registry here is a tmp SQLite file), GitHub, pgvector or Ollama: the collaborators
are autospecs of the real classes with `spec_set=True`, so a call the real class could not take fails the test.

What is pinned: the figures say where they came from and say "unknown until the first index" when the indexed repositories
cannot support an estimate (never a made-up number); a yes is a queued run row with the requester recorded and nothing else
starts; the state words come from run rows plus the persisted collections list; one end-to-end test runs the queue write path
(route -> run row -> claim -> handler -> ingestion -> index state) with fakes that bind the real signatures.
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, create_autospec, patch

import pytest

from resource_explorer import first_index as fi
from resource_explorer.github.analyzer import IngestionPlan, RepoAnalyzer
from resource_explorer.github.client import GitHubClient
from resource_explorer.index_state import index_state
from resource_explorer.ingestion.pipeline import IngestionPipeline
from resource_explorer.query_cache import QueryCache
from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.vector_store_pg import MultiCollectionStore

ME = "dwolfson"


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "fi.db"))


def add(registry, slug, collections=(), files=()):
    registry.add(Project(slug=slug, display_name=slug.title(), github_url=f"https://github.com/o/{slug}",
                         collections=list(collections)))
    if files:
        registry.upsert_file_inventory(slug, list(files))


@pytest.fixture
def client(registry, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    from resource_explorer.web.app import app
    return TestClient(app)


def as_user(u=ME):
    from resource_explorer.auth import create_access_token
    return {"Authorization": "Bearer " + create_access_token(user_id=u, egeria_token="t")}


# ── the figures ──────────────────────────────────────────────────────────────

def test_measured_figures_are_sourced_and_only_text_the_index_reads(registry):
    add(registry, "alpha", files=[("a.py", 2_000_000), ("b.py", 1_000_000), ("logo.png", 9_000_000)])
    j = fi.measured(registry, "alpha")
    assert j["measured"] and j["files"] == 2 and j["text_bytes"] == 3_000_000     # the png is not text
    assert j["source"].startswith("from the survey of ")


def test_a_repository_with_no_inventory_is_not_measured_not_zero(registry):
    add(registry, "bare")
    j = fi.measured(registry, "bare")
    assert j["measured"] is False and j["files"] is None and j["text_bytes"] is None
    assert "no file inventory" in j["source"]


def test_the_ask_route_says_unknown_when_no_repository_gives_an_estimate_basis(client, registry):
    add(registry, "alpha", files=[("a.py", 5_000_000)])
    r = client.get("/api/first-index/ask", params={"slug": "alpha"}, headers=as_user())
    assert r.status_code == 200
    repo = r.json()["repositories"][0]
    assert repo["files"] == 1 and repo["seconds"] is None and repo["chunks"] is None
    assert repo["time_words"] == fi.UNKNOWN_UNTIL_FIRST_INDEX == "unknown until the first index"
    assert repo["size_words"] == "unknown until the first index"
    assert repo["fetch"].startswith("clones the repository")


def test_an_estimate_needs_enough_indexed_repositories_and_names_them(registry):
    for i in range(fi.MIN_BASIS_REPOS):
        slug = f"done{i}"
        add(registry, slug, collections=[f"{slug}_python_code"], files=[("x.py", 1_048_576)])
        registry.upsert_metric(slug, "rag_ingestion", {"total_chunks": 400, "collections": 1})
    add(registry, "next", files=[("y.py", 2 * 1_048_576)])
    b = fi.basis(registry)
    assert b["indexed_repos"] == fi.MIN_BASIS_REPOS and b["chunks_per_mb"] == pytest.approx(400)
    e = fi.estimate(2 * 1_048_576, b)
    assert e["chunks"] == 800 and "3 repositories indexed here" in e["chunks_source"]
    # no finished first index has a duration yet: time stays unknown rather than being invented
    assert e["seconds"] is None


def test_too_few_indexed_repositories_give_no_number(registry):
    add(registry, "one", collections=["one_python_code"], files=[("x.py", 1_048_576)])
    registry.upsert_metric("one", "rag_ingestion", {"total_chunks": 400, "collections": 1})
    assert fi.estimate(1_048_576, fi.basis(registry))["chunks"] is None


def test_batch_totals_count_only_what_was_measured_and_say_how_many():
    t = fi.totals([{"files": 10, "text_bytes": 5, "seconds": None, "chunks": None},
                   {"files": None, "text_bytes": None, "seconds": None, "chunks": None}])
    assert t["files"] == {"value": 10, "of": 1, "out_of": 2}
    assert t["seconds"]["value"] is None


# ── queueing a yes ───────────────────────────────────────────────────────────

def test_queueing_without_a_signed_in_person_is_refused_and_queues_nothing(client, registry):
    add(registry, "alpha")
    r = client.post("/api/first-index/queue", json={"slugs": ["alpha"]})
    assert r.status_code == 401
    assert registry.list_runs(kind="first_index") == []


def test_a_yes_is_one_run_row_with_the_requester_and_the_position(client, registry):
    for s in ("alpha", "beta"):
        add(registry, s)
    r = client.post("/api/first-index/queue", headers=as_user(),
                    json={"slugs": ["alpha"], "batch_id": "b1", "batch_total": 2, "path": "each"})
    r2 = client.post("/api/first-index/queue", headers=as_user(),
                     json={"slugs": ["beta"], "batch_id": "b1", "batch_total": 2, "path": "each"})
    assert r.json()["queued"][0]["position"] == 1 and r2.json()["queued"][0]["position"] == 2
    rows = registry.list_runs(kind="first_index")
    assert {x["requested_by"] for x in rows} == {ME} and {x["state"] for x in rows} == {"queued"}
    t = json.loads(rows[0]["target"])
    assert t["batch_id"] == "b1" and t["batch_total"] == 2 and t["path"] == "each"
    # the batch is ONE activity row, rewritten as the yeses come in
    batch = registry.get_activity("b1")
    assert "2 of 2 repositories queued by dwolfson" in batch["summary"] and batch["status"] == "ok"


def test_an_indexed_repository_and_a_second_yes_are_refused_with_a_sentence(client, registry):
    add(registry, "done", collections=["done_python_code"])
    add(registry, "alpha")
    first = client.post("/api/first-index/queue", headers=as_user(), json={"slugs": ["alpha", "done"]}).json()
    assert [q["slug"] for q in first["queued"]] == ["alpha"]
    assert "already has an index of its own" in first["refused"][0]["reason"]
    again = client.post("/api/first-index/queue", headers=as_user(), json={"slugs": ["alpha"]}).json()
    assert "already waiting" in again["refused"][0]["reason"]
    assert len(registry.list_runs(kind="first_index")) == 1


def test_a_yes_can_be_withdrawn_until_it_is_claimed(client, registry):
    add(registry, "alpha")
    run_id = client.post("/api/first-index/queue", headers=as_user(), json={"slugs": ["alpha"]}).json()["queued"][0]["run_id"]
    assert client.post(f"/api/first-index/withdraw/{run_id}", headers=as_user()).status_code == 200
    assert registry.get_run(run_id)["state"] == "cancelled"
    assert client.get("/api/first-index/progress").json()["lines"] == []          # a withdrawn yes is not a line
    run2 = client.post("/api/first-index/queue", headers=as_user(), json={"slugs": ["alpha"]}).json()["queued"][0]["run_id"]
    registry.claim_next_run("w:1", {"pid": os.getpid()})
    assert client.post(f"/api/first-index/withdraw/{run2}", headers=as_user()).status_code == 409


# ── the state words come from rows ───────────────────────────────────────────

def test_a_succeeded_run_without_an_index_of_its_own_is_failed_not_indexed(registry):
    add(registry, "alpha")
    rid = registry.enqueue_run("first_index", {"slug": "alpha"}, requested_by=ME)
    registry.claim_next_run("w:1", {"pid": 1}); registry.mark_run_running(rid)
    registry.finish_run(rid, "succeeded")
    line = fi.progress(registry)["lines"][0]
    assert line["state"] == "failed" and "no index of its own" in line["reason"]


def test_progress_counts_done_of_the_batch(registry):
    for s in ("a", "b", "c"):
        add(registry, s)
    ids = [registry.enqueue_run("first_index", {"slug": s, "batch_id": "b1", "batch_total": 3, "position": i + 1},
                                requested_by=ME) for i, s in enumerate("abc")]
    registry.claim_next_run("w:1", {"pid": 1}); registry.mark_run_running(ids[0])
    registry.update_indexed_at("a", ["a_python_code"])
    registry.finish_run(ids[0], "succeeded")
    p = fi.progress(registry)
    assert [(x["slug"], x["state"]) for x in p["lines"]] == [("a", "indexed"), ("b", "waiting"), ("c", "waiting")]
    assert (p["done"], p["of"], p["active"]) == (1, 3, True)


# ── the queue write path, end to end ─────────────────────────────────────────

@pytest.fixture
def fakes(registry):
    """Collaborators bound to the real signatures; the pipeline fake writes what the real one writes."""
    analyzer = create_autospec(RepoAnalyzer, instance=True, spec_set=True)
    from resource_explorer.configdata.collection_config import COLLECTION_TYPES
    analyzer.analyze.return_value = IngestionPlan(
        "https://github.com/o/alpha", "alpha", "", "", [COLLECTION_TYPES["python_code"]], {"python_code": 3})
    pipeline = create_autospec(IngestionPipeline, instance=True, spec_set=True)

    def run(slug, github_url, collection_types, subproject_path=None, extra_docs_paths=None, local_path=None):
        registry.update_indexed_at(slug, [f"{slug}_{c.name}" for c in collection_types])
    pipeline.run.side_effect = run
    client = create_autospec(GitHubClient, instance=True, spec_set=True)
    client.get_repo.return_value = MagicMock(name="repo")
    client.get_latest_commit_sha.return_value = "abc123"
    store = create_autospec(MultiCollectionStore, instance=True, spec_set=True)
    store.count.return_value = 4200
    cache = create_autospec(QueryCache, instance=True, spec_set=True)
    with patch("resource_explorer.github.analyzer.RepoAnalyzer", return_value=analyzer), \
         patch("resource_explorer.ingestion.pipeline.IngestionPipeline", return_value=pipeline), \
         patch("resource_explorer.github.client.GitHubClient", return_value=client), \
         patch("resource_explorer.vector_store_pg.MultiCollectionStore", return_value=store), \
         patch("resource_explorer.query_cache.QueryCache", return_value=cache):
        yield {"analyzer": analyzer, "pipeline": pipeline, "client": client, "store": store, "cache": cache}


def _claim_and_run(registry):
    from resource_explorer.run_queue import execute_run
    row = registry.claim_next_run("w:1", {"pid": os.getpid()})
    return row, execute_run(row, registry)


def test_e2e_a_yes_through_the_queue_makes_the_first_index(client, registry, fakes, monkeypatch):
    add(registry, "alpha", collections=["web_docs_site"], files=[("a.py", 3_000_000)])
    r = client.post("/api/first-index/queue", headers=as_user(), json={"slugs": ["alpha"]})
    assert r.status_code == 200
    run_id = r.json()["queued"][0]["run_id"]
    row = registry.get_run(run_id)
    assert (row["kind"], row["state"], row["requested_by"]) == ("first_index", "queued", ME)
    # nothing has been indexed by saying yes: the ingestion is the queue's, and has not run
    fakes["pipeline"].run.assert_not_called()
    assert index_state(registry.get("alpha")) == "not_yet_indexed"
    assert client.get("/api/first-index/progress").json()["lines"][0]["state"] == "waiting"

    row, outcome = _claim_and_run(registry)
    assert outcome.state == "succeeded", outcome.error
    fakes["analyzer"].analyze.assert_called_once_with("https://github.com/o/alpha", subpath=None)
    fakes["pipeline"].run.assert_called_once()
    p = registry.get("alpha")
    assert index_state(p) == "indexed" and "web_docs_site" in p.collections       # the shared site collection stays listed
    assert p.last_commit_sha == "abc123"
    assert registry.query_metrics("alpha", "rag_ingestion")["total_chunks"] == 4200
    fakes["cache"].invalidate_project.assert_called_once_with("alpha")
    assert registry.get_activity(row["result_ref"])["status"] == "ok"

    line = client.get("/api/first-index/progress").json()["lines"][0]
    assert (line["state"], line["chunks"]) == ("indexed", 4200) and line["seconds"] is not None
    # once indexed, a refresh updates it: a second yes is refused
    again = client.post("/api/first-index/queue", headers=as_user(), json={"slugs": ["alpha"]}).json()
    assert "already has an index of its own" in again["refused"][0]["reason"]


def test_e2e_a_failed_ingestion_is_failed_with_its_first_sentence_and_no_index(client, registry, fakes):
    add(registry, "alpha")
    client.post("/api/first-index/queue", headers=as_user(), json={"slugs": ["alpha"]})
    fakes["pipeline"].run.side_effect = RuntimeError("GitHub rate limit exceeded. Wait and retry.")
    row, outcome = _claim_and_run(registry)
    assert outcome.state == "failed"
    line = client.get("/api/first-index/progress").json()["lines"][0]
    assert line["state"] == "failed" and line["reason"].startswith("RuntimeError: GitHub rate limit exceeded")
    assert index_state(registry.get("alpha")) == "not_yet_indexed"
    assert registry.get_activity(row["result_ref"])["status"] == "error"


def test_a_first_index_row_with_no_requester_fails_rather_than_runs(registry, fakes):
    add(registry, "alpha")
    registry.enqueue_run("first_index", {"slug": "alpha"}, requested_by="")
    row, outcome = _claim_and_run(registry)
    assert outcome.state == "failed" and "no requester recorded" in outcome.error
    fakes["pipeline"].run.assert_not_called()
