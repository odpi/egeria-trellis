"""TR-49: with every vector collection EMPTY (a fresh install), EA says plainly that its knowledge base hasn't
been built, naming the commands that build it, instead of the LLM's "I don't have enough information", which
read like a model failure.

Offline: a stand-in retriever and store; the LLM is a stand-in that fails the test if it's asked.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from advisor import knowledge_base as kb
from advisor.retrieval_outcome import RetrievalOutcome

NAMES = ["pyegeria", "pyegeria_cli", "egeria_java", "egeria_docs", "egeria_concepts", "egeria_types",
         "egeria_general", "egeria_workspaces", "trellis"]


class _Store:
    def __init__(self, counts=None, fail=None):
        self.counts, self.fail = counts or {}, fail

    def get_collection_stats(self, name):
        if self.fail == name:
            raise ConnectionError("store unreachable")
        return {"name": name, "num_entities": self.counts.get(name, 0)}


class _LLM:
    def __init__(self):
        self.called = False

    def generate(self, **kw):
        self.called = True
        return "I don't have enough information in the Egeria codebase to answer that question accurately."


def _system(store, outcome):
    from advisor.rag_system import RAGSystem
    s = RAGSystem.__new__(RAGSystem)
    s.retriever = SimpleNamespace(retrieve_and_build_context=lambda **kw: ("", []), _last_outcome=outcome,
                                  _last_collections_searched=list(NAMES), vector_store=store)
    s.llm_client = _LLM()
    s.rag_config = SimpleNamespace(generation=SimpleNamespace(temperature=0.1, max_tokens=100))
    return s


def _ask(system):
    analysis = {"search_strategy": {"top_k": 5, "min_score": 0.3, "format_style": "detailed"},
                "enhanced_query": "how do I create a glossary?", "query_type": "explanation"}
    return system._run_rag_fallback("how do I create a glossary?", analysis, include_context=True, perspective=None)


@pytest.fixture
def nine(monkeypatch):
    monkeypatch.setattr(kb, "enabled_collection_names", lambda: list(NAMES))


@pytest.mark.parametrize("outcome", [RetrievalOutcome.never_ingested(NAMES), RetrievalOutcome.collection_empty(NAMES)])
def test_all_collections_empty_says_the_knowledge_base_isnt_built(nine, outcome):
    system = _system(_Store(), outcome)
    out = _ask(system)
    assert system.llm_client.called is False
    assert out["response"].startswith("My knowledge base hasn't been built yet: all 9 of its collections are empty")
    assert "scripts/clone_repos.py --phase all" in out["response"]
    assert "scripts/ingest_collections.py --phase all" in out["response"]
    assert "enough information" not in out["response"]
    assert out["context_diagnosis"]["reason"] == "knowledge_base_not_built"


def test_a_built_knowledge_base_with_no_match_still_asks_the_model(nine):
    system = _system(_Store({"pyegeria": 1200}), RetrievalOutcome.collection_empty(["egeria_types"]))
    out = _ask(system)
    assert system.llm_client.called is True and "hasn't been built" not in out["response"]


def test_a_store_that_cant_be_counted_claims_nothing(nine):
    system = _system(_Store(fail="egeria_java"), RetrievalOutcome.never_ingested(NAMES))
    _ask(system)
    assert system.llm_client.called is True


def test_below_threshold_is_not_an_unbuilt_knowledge_base(nine):
    system = _system(_Store(), RetrievalOutcome.below_threshold(0.3, NAMES))
    _ask(system)
    assert system.llm_client.called is True


def test_not_built_only_on_a_confirmed_zero_everywhere():
    assert kb.not_built(_Store(), NAMES).startswith("My knowledge base hasn't been built yet: all 9")
    assert kb.not_built(_Store({"trellis": 3}), NAMES) is None
    assert kb.not_built(_Store(fail="trellis"), NAMES) is None
    assert kb.not_built(None, NAMES) is None and kb.not_built(_Store(), []) is None
