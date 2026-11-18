"""A resource-scoped "what is / purpose / how to" question searches the resource's docs before the model runs.

Traced on 8813 2026-11-10 (query_log rows 1425, 1426): "What is the purpose of Amundsen" and "what is the purpose of
Egeria?" made no vector_search call, answered from profile metrics ("not explicitly stated"), and recorded no
chunk_refs, so a vote on them could not move a chunk score (#1015). ConversationAgent._doc_evidence puts the passages
in front of the model, records them as the turn's chunks, and says which of three absences it hit when there are none.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from trellis_vectorstore import SearchResult

from resource_explorer.agents import conversation_agent as ca
from resource_explorer.agents.conversation_agent import ConversationAgent
from resource_explorer.query_processor import QueryIntent, QueryProcessor

DOCS = ["egeria_git_markdown_docs", "egeria_git_web_docs"]


class _FakeRegistry:
    def __init__(self, collections):
        self._collections = collections

    def get(self, slug):
        return None if self._collections is None else SimpleNamespace(slug=slug, collections=self._collections)

    @staticmethod
    def _normalize_slug(slug):
        return slug.replace("-", "_").lower()


class _FakeStore:
    """MultiCollectionStore stand-in: records each search; returns `hits`, or raises `error`."""

    def __init__(self, hits=(), error=None):
        self.hits, self.error, self.calls = list(hits), error, []

    def search(self, query, collections, top_k=None, min_score=None):
        self.calls.append((query, list(collections), top_k))
        if self.error:
            raise self.error
        return list(self.hits)


def _hit(i, text="Egeria is an open metadata and governance platform.", score=0.62, col=DOCS[0]):
    return SearchResult(id=f"c{i}", score=score, text=text, collection=col)


def _ask(question, collections, store, *, slug="egeria_git", resource_type="repo"):
    """Run one turn with the model stubbed out; return (prompt the model saw, turn_log)."""
    seen = {}

    def _model(self, prompt):
        seen["prompt"] = prompt
        return "answer"

    turn_log: dict = {}
    agent = ConversationAgent(compiled_evidence=False, resource_type=resource_type)
    with patch.object(ca, "_registry", return_value=_FakeRegistry(collections)), \
         patch("resource_explorer.vector_store_pg.MultiCollectionStore", return_value=store), \
         patch("resource_explorer.collection_router.CollectionRouter.select", return_value=[]), \
         patch.object(ConversationAgent, "_run_persistent", _model):
        answer = agent.handle(question, resource_slug=slug, turn_log=turn_log)
    assert answer == "answer"
    return seen["prompt"], turn_log


class TestDocEvidenceInThePrompt:
    def test_two_hits_are_in_the_prompt_and_recorded_as_the_turns_chunks(self):
        store = _FakeStore([_hit(1), _hit(2, text="Getting started with Egeria.", col=DOCS[1], score=0.5)])
        prompt, turn_log = _ask("What is the purpose of Egeria?", DOCS + ["egeria_git_java_code"], store)

        assert "Documentation for egeria_git (2 passages): use these for what it is, what it is for" in prompt
        assert "[egeria_git_markdown_docs | score=0.62]" in prompt
        assert "Getting started with Egeria." in prompt
        assert turn_log["refs"] == [f"{DOCS[0]}:c1", f"{DOCS[1]}:c2"]
        # doc collections only, from the registry: never the code collection the slug also has
        assert store.calls[0][1] == DOCS
        assert prompt.index("Documentation for") < prompt.index("Question:")

    def test_turn_chunk_refs_reads_them_back(self):
        from resource_explorer.agents.tools import set_current_question, turn_chunk_refs

        set_current_question("what is it")
        with patch.object(ca, "_registry", return_value=_FakeRegistry(DOCS)), \
             patch("resource_explorer.vector_store_pg.MultiCollectionStore",
                   return_value=_FakeStore([_hit(1), _hit(2)])):
            ConversationAgent._doc_evidence("what is it", "egeria_git")
        assert turn_chunk_refs() == [f"{DOCS[0]}:c1", f"{DOCS[0]}:c2"]

    def test_release_notes_only_when_the_question_is_about_changes(self):
        cols = DOCS + ["egeria_git_release_notes"]
        store = _FakeStore([_hit(1)])
        _ask("What is Egeria?", cols, store)
        _ask("What is new in the latest release of Egeria?", cols, store)
        assert "egeria_git_release_notes" not in store.calls[0][1]
        assert "egeria_git_release_notes" in store.calls[1][1]

    def test_a_health_score_question_is_not_told_to_prefer_the_passages(self):
        """"what is the health score of X" classifies as conceptual ("what is" wins over health), so it gets doc
        passages; the header must send a measurement question to the evidence, not to README prose (read-back P2)."""
        assert QueryProcessor().classify("what is the health score of egeria_git") == QueryIntent.CONCEPTUAL
        prompt, _ = _ask("what is the health score of egeria_git", DOCS, _FakeStore([_hit(1)]))
        header = next(line for line in prompt.splitlines() if line.startswith("Documentation for"))
        assert "for measurements, use the evidence above" in header
        assert "first" not in header and "answer the question from these" not in header

    def test_results_under_the_threshold_are_dropped(self):
        from resource_explorer.config import get_config

        floor = get_config().rag.min_score
        store = _FakeStore([_hit(1), _hit(2, score=max(floor - 0.05, 0.0))])
        _, turn_log = _ask("what is the purpose of egeria", DOCS, store)
        assert turn_log["refs"] == [f"{DOCS[0]}:c1"]


class TestAbsenceIsSaid:
    def test_no_doc_collection_says_none_ingested_and_does_not_search(self):
        store = _FakeStore([_hit(1)])
        prompt, turn_log = _ask("What is the purpose of Amundsen", [], store, slug="amundsen")
        assert "Documentation for amundsen: none ingested (no doc collection)" in prompt
        assert "say so rather than inferring it from metrics" in prompt
        assert store.calls == []
        assert turn_log.get("refs", []) == []

    def test_unregistered_slug_is_none_ingested_too(self):
        store = _FakeStore([_hit(1)])
        prompt, _ = _ask("What is the purpose of Amundsen", None, store, slug="amundsen")
        assert "none ingested" in prompt and store.calls == []

    def test_collections_but_no_hit_says_nothing_matched(self):
        prompt, turn_log = _ask("What is the purpose of Egeria?", DOCS, _FakeStore([]))
        assert "Documentation for egeria_git: searched 2 doc collection(s)" in prompt
        assert "nothing matched the question above the relevance threshold" in prompt
        assert "none ingested" not in prompt
        assert turn_log.get("refs", []) == []

    def test_a_store_error_says_could_not_be_read_and_the_turn_still_answers(self):
        prompt, _ = _ask("What is the purpose of Egeria?", DOCS, _FakeStore(error=ConnectionError("pg down")))
        assert "Documentation for egeria_git: could not be read (ConnectionError)" in prompt
        assert "nothing matched" not in prompt and "none ingested" not in prompt

    def test_a_registry_error_says_could_not_be_read(self):
        class _Broken:
            def get(self, slug):
                raise RuntimeError("registry down")

        with patch.object(ca, "_registry", return_value=_Broken()):
            lines = ConversationAgent._doc_evidence("what is it", "egeria_git")
        assert lines == ["Documentation for egeria_git: could not be read (RuntimeError)."]


class TestWhenItRuns:
    @pytest.mark.parametrize("question", [
        "how many commits last month",            # statistical
        "is it abandoned",                        # health
        "how is the yaml parser implemented",     # code search
    ])
    def test_not_for_statistical_health_or_code_questions(self, question):
        intent = QueryProcessor().classify(question)
        if intent not in (QueryIntent.STATISTICAL, QueryIntent.HEALTH, QueryIntent.CODE_SEARCH):
            pytest.skip(f"{question!r} classifies as {intent}, not the excluded intent this case is about")
        store = _FakeStore([_hit(1)])
        prompt, _ = _ask(question, DOCS, store)
        assert store.calls == [] and "Documentation for" not in prompt

    @pytest.mark.parametrize("intent", [QueryIntent.CODE_SEARCH, QueryIntent.STATISTICAL, QueryIntent.HEALTH,
                                        QueryIntent.EXAMPLES, QueryIntent.DEPENDENCY])
    def test_excluded_intents_even_when_the_wording_matches(self, intent):
        assert not ca._wants_doc_evidence("what is the purpose of egeria", intent)

    @pytest.mark.parametrize("question", [
        "What is the purpose of Amundsen", "what does egeria do", "give me an overview",
        "how do I install it", "how to configure the server", "explain the architecture", "describe this repo",
    ])
    def test_about_the_resource_questions_qualify(self, question):
        assert ca._wants_doc_evidence(question, QueryIntent.GENERAL)

    def test_conceptual_qualifies_whatever_the_wording(self):
        assert ca._wants_doc_evidence("architecture", QueryIntent.CONCEPTUAL)

    def test_not_without_a_slug(self):
        store = _FakeStore([_hit(1)])
        seen = {}
        agent = ConversationAgent(compiled_evidence=False)
        with patch.object(agent, "_infer_project_slug", return_value=None), \
             patch("resource_explorer.vector_store_pg.MultiCollectionStore", return_value=store), \
             patch.object(ConversationAgent, "_run_persistent", lambda self, p: seen.setdefault("p", p) and "a"):
            agent.handle("what is the purpose of this?", resource_slug=None)
        assert store.calls == [] and "Documentation for" not in seen["p"]

    def test_not_for_a_database(self):
        store = _FakeStore([_hit(1)])
        prompt, _ = _ask("what is the purpose of this database?", DOCS, store, resource_type="database")
        assert store.calls == [] and "Documentation for" not in prompt


class TestCaps:
    def test_each_passage_is_capped(self):
        long = "x" * (ca.DOC_EVIDENCE_PASSAGE_CHARS * 3)
        lines = self._lines([_hit(1, text=long)])
        block = lines[1]
        assert len(block) <= ca.DOC_EVIDENCE_PASSAGE_CHARS + 60   # the "[collection | score]" label line
        assert block.endswith("...")

    def test_the_block_is_capped_and_only_shown_passages_are_recorded(self):
        from resource_explorer.agents.tools import set_current_question, turn_chunk_refs

        long = "y" * ca.DOC_EVIDENCE_PASSAGE_CHARS
        hits = [_hit(i, text=long) for i in range(ca.DOC_EVIDENCE_TOP_K)]
        set_current_question("what is it")
        lines = self._lines(hits)
        assert len(lines[1]) <= ca.DOC_EVIDENCE_BLOCK_CHARS + 2 * ca.DOC_EVIDENCE_TOP_K
        shown = int(lines[0].split("(")[1].split(" ")[0])
        assert 0 < shown < len(hits)
        assert len(turn_chunk_refs()) == shown

    def test_the_caps_fit_inside_the_compile_budget_order_of_magnitude(self):
        assert ca.DOC_EVIDENCE_PASSAGE_CHARS < ca.DOC_EVIDENCE_BLOCK_CHARS

    @staticmethod
    def _lines(hits):
        with patch.object(ca, "_registry", return_value=_FakeRegistry(DOCS)), \
             patch("resource_explorer.vector_store_pg.MultiCollectionStore", return_value=_FakeStore(hits)):
            return ConversationAgent._doc_evidence("what is it", "egeria_git")


class TestTheVoteHasChunksToScore:
    """Through the chat route: the model makes no tool call, yet the turn's query_log row carries the doc chunks."""

    def test_query_log_row_carries_the_doc_chunks(self, monkeypatch, tmp_path):
        from fastapi.testclient import TestClient

        monkeypatch.setenv("METRICS_DATABASE_URL", f"sqlite:///{tmp_path / 'metrics.db'}")
        monkeypatch.chdir(tmp_path)
        import resource_explorer.config as config_module
        config_module._config = None
        from resource_explorer.observability.metrics_collector import shared_collector
        from resource_explorer.web.app import app

        store = _FakeStore([_hit(1), _hit(2)])
        with patch.object(ca, "_registry", return_value=_FakeRegistry(DOCS)), \
             patch("resource_explorer.vector_store_pg.MultiCollectionStore", return_value=store), \
             patch("resource_explorer.collection_router.CollectionRouter.select", return_value=[]), \
             patch.object(ConversationAgent, "_run_persistent", lambda self, p: "Egeria is a platform."):
            resp = TestClient(app).post(
                "/api/query/", json={"query": "What is the purpose of Egeria?", "project_slug": "egeria_git"}
            )
        assert resp.status_code == 200, resp.text
        with shared_collector()._conn() as conn:
            row = conn.execute("SELECT query_hash, chunk_refs FROM query_log ORDER BY id DESC LIMIT 1").fetchone()
        assert row["query_hash"] == resp.json()["query_hash"]
        assert json.loads(row["chunk_refs"]) == [f"{DOCS[0]}:c1", f"{DOCS[0]}:c2"]
