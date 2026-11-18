"""TR-51 (c): an answer built from retrieval reports the sources it used (Architect: absence-as-answer).

Rocky's run (2026-11-18): after indexing, EA answered an explanation question correctly but reported
num_sources 0 / retrieval 0.00. The DocAgent (explanation, best_practice, comparison, debugging) and the
ExamplesAgent retrieve through agents.tools._search_egeria_content_raw, then returned a result with hard-coded
"sources": [], "num_sources": 0, "retrieval_time": 0.0: a measured-looking zero for something that did happen.

Also TR-49 through the DocAgent: on an empty knowledge base it said "I don't have enough information …", like the
RAG fallback did; it now says the knowledge base hasn't been built.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def _hit(collection, path, score, text):
    return SimpleNamespace(score=score, text=text,
                           metadata={"_collection": collection, "file_path": path, "name": path.rsplit("/", 1)[-1]})


HITS = [_hit("egeria_concepts", "site/docs/concepts/governance-zone.md", 0.82, "A governance zone groups assets."),
        _hit("egeria_general", "site/docs/guides/zones.md", 0.71, "Zones control visibility."),
        _hit("egeria_types", "site/docs/types/4/0424-Governance-Zones.md", 0.64, "GovernanceZone type.")]


class _Store:
    def __init__(self, hits):
        self.hits, self.calls = hits, 0

    def search_specific_collections(self, query, collection_names, top_k):
        self.calls += 1
        return SimpleNamespace(results=list(self.hits))


@pytest.fixture
def store(monkeypatch):
    s = _Store(HITS)
    monkeypatch.setattr("advisor.multi_collection_store.get_multi_collection_store", lambda: s)
    monkeypatch.setattr("advisor.llm_client.get_ollama_client",
                        lambda: SimpleNamespace(generate=lambda *a, **k: "A governance zone groups assets."))
    return s


def test_a_doc_agent_answer_reports_the_sources_it_used(store):
    from advisor.agents.doc_agent import get_doc_agent
    out = get_doc_agent().handle("What is a governance zone?", mode="explanation")
    assert out["response"] == "A governance zone groups assets."
    assert out["num_sources"] == 3 == len(out["sources"])
    assert [s["file_path"] for s in out["sources"]] == [h.metadata["file_path"] for h in HITS]
    assert out["sources"][0]["collection"] == "egeria_concepts" and out["sources"][0]["score"] == 0.82
    assert out["retrieval_time"] > 0.0
    assert out["avg_relevance_score"] == pytest.approx((0.82 + 0.71 + 0.64) / 3)


def test_an_examples_agent_answer_reports_the_sources_it_used(store, monkeypatch):
    from advisor.agents import examples_agent as ea
    monkeypatch.setattr("advisor.agents.tools._get_egeria_symbol_raw", lambda name: "")
    out = ea.get_examples_agent().handle("show me how to create a governance zone")
    assert store.calls >= 1
    assert out["num_sources"] == len(out["sources"]) > 0
    assert out["retrieval_time"] > 0.0


def test_no_retrieval_still_reports_zero(monkeypatch):
    """The counts are what happened: a handler that searched nothing reports none (and isn't given any)."""
    from advisor.agents.tools import recording_retrieval, with_retrieval
    with recording_retrieval() as log:
        pass
    out = with_retrieval({"response": "x", "sources": [], "num_sources": 0, "retrieval_time": 0.0}, log)
    assert out["num_sources"] == 0 and out["sources"] == [] and out["retrieval_time"] == 0.0


def test_the_doc_agent_on_an_empty_knowledge_base_says_it_isnt_built(monkeypatch):
    from advisor import knowledge_base as kb
    from advisor.agents.doc_agent import get_doc_agent
    s = _Store([])
    monkeypatch.setattr("advisor.multi_collection_store.get_multi_collection_store", lambda: s)
    monkeypatch.setattr(kb, "enabled_collection_names", lambda: ["egeria_concepts", "egeria_general", "pyegeria"])
    monkeypatch.setattr("advisor.vector_store.get_vector_store",
                        lambda: SimpleNamespace(get_collection_stats=lambda n: {"num_entities": 0}))
    monkeypatch.setattr("advisor.llm_client.get_ollama_client",
                        lambda: pytest.fail("the model was asked with nothing indexed"))
    out = get_doc_agent().handle("What is a governance zone?")
    assert out["response"].startswith("My knowledge base hasn't been built yet: all 3 of its collections are empty")
    assert out["num_sources"] == 0


def test_the_doc_agent_with_a_built_knowledge_base_and_no_match_keeps_its_words(monkeypatch):
    from advisor import knowledge_base as kb
    from advisor.agents.doc_agent import get_doc_agent
    monkeypatch.setattr("advisor.multi_collection_store.get_multi_collection_store", lambda: _Store([]))
    monkeypatch.setattr(kb, "enabled_collection_names", lambda: ["egeria_concepts"])
    monkeypatch.setattr("advisor.vector_store.get_vector_store",
                        lambda: SimpleNamespace(get_collection_stats=lambda n: {"num_entities": 120}))
    out = get_doc_agent().handle("What is a flux capacitor?")
    assert out["response"].startswith("I don't have enough information about that in my indexed documentation")
