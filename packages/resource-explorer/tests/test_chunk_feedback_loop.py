"""A chat vote moves the retrieval score of the chunks behind the answer: end to end through the chat route.

docs/feedback-closed-loop-design.md §1 and §6 step 0. Two breaks, both silent, both fixed here:

* the write side: chat answers through ConversationAgent (since abd936f9, 2026-10-19), which never wrote the
  query_log row a vote scores, so record_feedback matched no row; and
* the read side: vector_store_pg read chunk_feedback from the old SQLite path while MetricsCollector wrote it to
  METRICS_DATABASE_URL, creating an empty data/metrics.db and returning no boosts.

Unit tests of record_feedback passed throughout, which is why this one goes through the routes: ask, vote, ask
again, and the score of the chunk the answer used has moved. Runs on SQLite and, when reachable, on Postgres.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from trellis_vectorstore import SearchResult

from resource_explorer.web.app import app

RAW = {"chunk-good": 0.80, "chunk-other": 0.70}


class _FakeStore:
    """The shared pgvector store, returning two fixed chunks of one collection at fixed raw scores."""

    def search(self, collection, query_text=None, top_k=5, **kw):
        return [SearchResult(id=i, score=s, text=f"text of {i}") for i, s in RAW.items()]


def _answer_with_a_search(self, prompt):
    """Stands in for the model: it calls the vector_search tool once, as the real agent does, then answers."""
    from resource_explorer.agents.tools import vector_search_raw

    vector_search_raw("how is it configured", "proj_docs")
    return "It is configured in settings.yaml."


@pytest.fixture(params=["sqlite", "postgres"])
def metrics_url(request, tmp_path):
    if request.param == "sqlite":
        return f"sqlite:///{tmp_path / 'metrics.db'}"
    schema = request.getfixturevalue("pg_test_schema")
    from resource_explorer.config import get_config
    cfg = get_config().pgvector
    url = (f"postgresql://{cfg.db_user}:{cfg.password}@{cfg.host}:{cfg.port}/{cfg.dbname}"
           f"?options=-csearch_path%3D{schema}")
    # pg_test_schema is session-scoped: start from no votes.
    from resource_explorer.observability.metrics_collector import MetricsCollector
    with MetricsCollector(database_url=url)._conn() as conn:
        conn.execute("DELETE FROM chunk_feedback")
        conn.execute("DELETE FROM query_log")
    return url


@pytest.fixture
def client(monkeypatch, metrics_url, tmp_path):
    monkeypatch.setenv("METRICS_DATABASE_URL", metrics_url)
    monkeypatch.chdir(tmp_path)          # so a stray data/metrics.db would land here, where the test looks
    import resource_explorer.config as config_module
    config_module._config = None
    from resource_explorer.agents.conversation_agent import ConversationAgent
    with patch("resource_explorer.vector_store_pg._get_shared_store", return_value=_FakeStore()), \
         patch.object(ConversationAgent, "_run_persistent", _answer_with_a_search):
        yield TestClient(app)


def _scores(question="how is it configured"):
    from resource_explorer.vector_store_pg import MultiCollectionStore
    return {r.id: r.score for r in MultiCollectionStore().search(question, ["proj_docs"])}


def test_ask_vote_ask_moves_the_chunk_score(client, tmp_path):
    question = "how is it configured?"
    first = client.post("/api/query/", json={"query": question})
    assert first.status_code == 200, first.text
    before = _scores()
    assert before == pytest.approx(RAW)                # no votes yet: raw scores

    # Five thumbs-down on the same answer: full confidence (chunk_boost), precision 0.
    for _ in range(5):
        client.post("/api/query/", json={"query": question})
        vote = client.post("/api/query/feedback", json={"query_hash": first.json()["query_hash"], "vote": -1})
        assert vote.status_code == 200, vote.text

    after = _scores()
    assert after["chunk-good"] == pytest.approx(RAW["chunk-good"] - 0.15)
    assert after["chunk-other"] == pytest.approx(RAW["chunk-other"] - 0.15)
    assert not os.path.exists(tmp_path / "data" / "metrics.db"), "the read created the old SQLite file again"


def test_the_chat_turn_is_logged_with_the_chunks_it_retrieved(client):
    from resource_explorer.observability.metrics_collector import shared_collector

    resp = client.post("/api/query/", json={"query": "where is the config?"})
    with shared_collector()._conn() as conn:
        row = conn.execute("SELECT query_hash, chunk_refs FROM query_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row["query_hash"] == resp.json()["query_hash"]
    assert sorted(__import__("json").loads(row["chunk_refs"])) == ["proj_docs:chunk-good", "proj_docs:chunk-other"]


def test_the_streamed_turn_is_logged_too(client):
    from resource_explorer.observability.metrics_collector import shared_collector

    with client.stream("POST", "/api/query/stream", json={"query": "streamed question"}) as resp:
        body = "".join(resp.iter_text())
    assert '"t": "done"' in body
    with shared_collector()._conn() as conn:
        rows = conn.execute("SELECT chunk_refs FROM query_log").fetchall()
    assert len(rows) == 1 and "proj_docs:chunk-good" in rows[0]["chunk_refs"]


def test_a_metrics_outage_keeps_retrieval_working_and_says_so(client, caplog, monkeypatch):
    import resource_explorer.vector_store_pg as vsp
    monkeypatch.setitem(vsp._boosts_warned, "done", False)

    def broken():
        raise RuntimeError("metrics database unreachable\nDETAIL: whatever")
    monkeypatch.setattr(vsp, "_metrics_collector", broken)
    with caplog.at_level("WARNING", logger="resource_explorer.vector_store_pg"):
        assert _scores() == pytest.approx(RAW)
    assert "chunk feedback boosts unavailable" in caplog.text
    assert "DETAIL" not in caplog.text


def test_two_concurrent_turns_on_one_session_agent_keep_their_own_chunks(client):
    """Read-back P3: the refs are the route's own record of its turn, not an attribute of the session agent, so two
    overlapping turns on one agent cannot swap them."""
    import threading

    from resource_explorer.agents.conversation_agent import ConversationAgent

    barrier = threading.Barrier(2)

    class _PerCollection(_FakeStore):
        def search(self, collection, query_text=None, top_k=5, **kw):
            return [SearchResult(id=f"{collection}-chunk", score=0.9, text="t")]

    def _overlapping(self, prompt):
        from resource_explorer.agents.tools import vector_search_raw
        coll = "coll_a" if "alpha" in prompt else "coll_b"
        barrier.wait(timeout=10)          # both turns are inside the agent at once
        vector_search_raw("q", coll)
        barrier.wait(timeout=10)
        return "ok"

    agent = ConversationAgent()
    logs = {"alpha": {}, "beta": {}}
    with patch("resource_explorer.vector_store_pg._get_shared_store", return_value=_PerCollection()), \
         patch.object(ConversationAgent, "_run_persistent", _overlapping):
        threads = [threading.Thread(target=agent.handle, args=(f"question {k}",), kwargs={"turn_log": logs[k]})
                   for k in logs]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
    assert logs["alpha"]["refs"] == ["coll_a:coll_a-chunk"]
    assert logs["beta"]["refs"] == ["coll_b:coll_b-chunk"]


def test_boosts_are_read_once_until_a_vote_lands(client, monkeypatch):
    """Read-back P3: chunk_boosts no longer reads the whole table on every search; a vote recorded through ANY
    collector in the process makes the next read fresh."""
    from resource_explorer.observability.metrics_collector import MetricsCollector, shared_collector

    collector = shared_collector()
    reads = []
    real = MetricsCollector._read_chunk_boosts
    monkeypatch.setattr(MetricsCollector, "_read_chunk_boosts", lambda self: reads.append(1) or real(self))
    collector._boost_cache = None
    collector.chunk_boosts()
    collector.chunk_boosts()
    assert len(reads) == 1
    MetricsCollector(database_url=collector.database_url).record_feedback("no-such-hash", 1)
    collector.chunk_boosts()
    assert len(reads) == 2
