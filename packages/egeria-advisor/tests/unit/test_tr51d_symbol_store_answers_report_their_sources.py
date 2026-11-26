"""TR-51 (d): answers EA builds from its symbol store and command index report what they read.

TR-51c fixed the DocAgent and ExamplesAgent, whose vector-search answers came back with hard-coded
"sources": [], "num_sources": 0, "retrieval_time": 0.0. The Architect ruled that the symbol-store answers are the
same bug: code_intel_agent (each answer branch reads project_code_symbols / project_code_relationships),
rag_system's structural and quantitative shortcuts (analytics.answer_quantitative_query, which returns text only),
and the Dr.Egeria command-discovery answer from command_keyword_index. Each read something, then reported zeros.

Now each read is recorded in the request's retrieval log (agents.tools.recording_retrieval) with its lookup
time. A read that returned rows is a source; a read that found nothing still counts its time (the store was
asked). retrieval_method says which stores were read, and is "none" only when nothing was read at all.

Offline: a stand-in database, symbol reader, command index and model.
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest


class _DB:
    """Answers each SQL by the first matching (fragment, rows) pair; records every query."""

    def __init__(self, answers):
        self.answers, self.queries = answers, []

    def connect(self):
        return None

    def execute_query(self, sql, params=()):
        time.sleep(0.001)
        self.queries.append(sql)
        for fragment, rows in self.answers:
            if fragment in sql:
                return list(rows)
        return []


class _LLM:
    def __init__(self):
        self.prompts = []

    def generate(self, prompt, **kw):
        self.prompts.append(prompt)
        return "GlossaryManager manages glossaries."


@pytest.fixture
def llm(monkeypatch):
    model = _LLM()
    monkeypatch.setattr("advisor.llm_client.get_ollama_client", lambda: model)
    return model


def _code_intel(monkeypatch, answers):
    from advisor.agents import code_intel_agent as cia
    db = _DB(answers)
    monkeypatch.setattr(cia, "get_db_manager", lambda: db)
    return cia.CodeIntelAgent(), db


CLASS_ROW = {"name": "GlossaryManager", "project_slug": "egeria_python_git", "file_path": "pyegeria/glossary_manager.py",
             "start_line": 40, "end_line": 900, "signature": "class GlossaryManager(Client2)",
             "docstring": "Manage glossaries.", "parent_class": ""}


# ── code_intel_agent ─────────────────────────────────────────────────────────

def test_a_class_answer_reports_the_definition_it_read(monkeypatch, llm):
    agent, db = _code_intel(monkeypatch, [("SELECT name FROM", [{"name": "GlossaryManager"}]),
                                          ("kind = 'class' AND name ILIKE", [CLASS_ROW]),
                                          ("WITH RECURSIVE ancestors", [{"class_name": "Client2", "depth": 1,
                                                                         "project_slug": "egeria_python_git"}])])
    out = agent.handle("what is GlossaryManager")
    assert "pyegeria/glossary_manager.py" in llm.prompts[0]                # the read reached the answer
    paths = [s["file_path"] for s in out["sources"]]
    assert "pyegeria/glossary_manager.py" in paths
    assert out["num_sources"] == len(out["sources"]) >= 2                   # the class row + the hierarchy read
    assert {s["collection"] for s in out["sources"]} == {"symbol_store"}
    assert out["retrieval_time"] > 0.0
    assert out["retrieval_method"] == "symbol_store"


def test_a_name_that_isnt_there_counts_the_lookup_but_no_sources(monkeypatch, llm):
    agent, _ = _code_intel(monkeypatch, [])
    out = agent.handle("what is FluxCapacitor")
    assert out["num_sources"] == 0 and out["sources"] == []
    assert out["retrieval_time"] > 0.0                                      # the store was asked
    assert out["retrieval_method"] == "symbol_store"


def test_a_clarifying_answer_read_nothing_and_says_so(monkeypatch, llm):
    agent, db = _code_intel(monkeypatch, [])
    out = agent.handle("list classes")
    assert db.queries == []
    assert out["num_sources"] == 0 and out["retrieval_time"] == 0.0
    assert out["retrieval_method"] == "none"


def test_codebase_stats_report_the_table_they_counted(monkeypatch, llm):
    agent, _ = _code_intel(monkeypatch, [("GROUP BY kind", [{"kind": "class", "count": 12, "total_loc": 3400}])])
    out = agent.handle("how many classes are there")
    assert out["num_sources"] == 1
    assert out["sources"][0]["collection"] == "symbol_store"
    assert "project_code_symbols" in out["sources"][0]["file_path"]
    assert out["retrieval_time"] > 0.0


# ── rag_system shortcuts over analytics.answer_quantitative_query ───────────

@pytest.fixture
def analytics(monkeypatch, tmp_path):
    """A real AnalyticsManager over an empty cache dir, reading a real ReCodeSymbolReader with a stand-in DB."""
    from advisor import analytics as an
    from advisor import re_code_symbol_reader as rr
    db = _DB([("WHERE kind = 'class'", [{"name": "GlossaryManager", "collection": "pyegeria",
                                         "file_path": "pyegeria/glossary_manager.py", "start_line": 40, "loc": 860,
                                         "docstring": "Manage glossaries."},
                                        {"name": "ProjectManager", "collection": "pyegeria",
                                         "file_path": "pyegeria/project_manager.py", "start_line": 30, "loc": 700,
                                         "docstring": ""}])])
    reader = rr.ReCodeSymbolReader.__new__(rr.ReCodeSymbolReader)
    reader.db_manager = db
    monkeypatch.setattr(rr, "get_re_code_symbol_reader", lambda: reader)
    manager = an.AnalyticsManager(cache_dir=tmp_path)
    monkeypatch.setattr(an, "get_analytics_manager", lambda: manager)
    return manager


def _rag():
    from advisor.rag_system import RAGSystem
    return RAGSystem.__new__(RAGSystem)


def test_a_structural_answer_reports_the_symbol_read(analytics):
    out = _rag()._handle_structural_query("list classes in pyegeria", path_filter="pyegeria")
    assert "GlossaryManager" in out["response"]
    assert out["num_sources"] == 1 == len(out["sources"])
    src = out["sources"][0]
    assert src["collection"] == "symbol_store" and "project_code_symbols" in src["file_path"]
    assert "2 rows" in src["text"]
    assert out["retrieval_time"] > 0.0
    assert out["retrieval_method"] == "symbol_store"


def test_a_quantitative_answer_reports_the_symbol_read(analytics):
    out = _rag()._quantitative_answer("list classes in pyegeria", "pyegeria", analytics)
    assert out["num_sources"] == 1 and out["retrieval_time"] > 0.0
    assert out["retrieval_method"] == "symbol_store"


def test_an_answer_from_the_metrics_file_names_that_file(monkeypatch, tmp_path):
    """No symbol store: the answer comes from the cached metrics file, and says so (not "symbol_store")."""
    from advisor import analytics as an
    from advisor import re_code_symbol_reader as rr
    (tmp_path / "enhanced_metrics.json").write_text(json.dumps({"files_analyzed": 10, "total_classes": 7}))
    monkeypatch.setattr(rr, "get_re_code_symbol_reader", lambda: (_ for _ in ()).throw(ConnectionError("down")))
    manager = an.AnalyticsManager(cache_dir=tmp_path)
    out = _rag()._quantitative_answer("how many classes are there", None, manager)
    assert "7 classes" in out["response"]
    assert out["num_sources"] == 1
    assert out["sources"][0]["collection"] == "analytics_cache"
    assert out["sources"][0]["file_path"].endswith("enhanced_metrics.json")
    assert out["retrieval_method"] == "analytics_cache"


def test_with_no_store_and_no_metrics_file_nothing_was_read(monkeypatch, tmp_path):
    from advisor import analytics as an
    from advisor import re_code_symbol_reader as rr
    monkeypatch.setattr(rr, "get_re_code_symbol_reader", lambda: (_ for _ in ()).throw(ConnectionError("down")))
    out = _rag()._quantitative_answer("how many classes are there", None, an.AnalyticsManager(cache_dir=tmp_path))
    assert out["num_sources"] == 0 and out["retrieval_time"] == 0.0
    assert out["retrieval_method"] == "none"


# ── command discovery over command_keyword_index ────────────────────────────

class _Index:
    def __init__(self, groups):
        self.groups = groups

    def search_by_keyword(self, kw):
        time.sleep(0.001)
        return self.groups

    def all_commands(self):
        time.sleep(0.001)
        return self.groups


def test_a_command_discovery_answer_reports_the_commands_it_found(monkeypatch):
    groups = {"Glossary": [{"name": "Create Glossary", "family": "Glossary", "aliases": [], "in_catalog": True},
                           {"name": "Create Term", "family": "Glossary", "aliases": [], "in_catalog": True}]}
    monkeypatch.setattr("advisor.command_keyword_index.get_command_keyword_index", lambda: _Index(groups))
    out = _rag()._handle_command_discovery("what dr.egeria commands are there for glossary?")
    assert [s["name"] for s in out["sources"]] == ["Create Glossary", "Create Term"]
    assert {s["collection"] for s in out["sources"]} == {"command_keyword_index"}
    assert out["num_sources"] == 2
    assert out["retrieval_time"] > 0.0
    assert out["retrieval_method"] == "command_keyword_index"


def test_a_command_discovery_miss_counts_the_lookup(monkeypatch):
    monkeypatch.setattr("advisor.command_keyword_index.get_command_keyword_index", lambda: _Index({}))
    out = _rag()._handle_command_discovery("what dr.egeria commands are there for flux capacitors?")
    assert "No Dr. Egeria commands found" in out["response"]
    assert out["num_sources"] == 0 and out["retrieval_time"] > 0.0
    assert out["retrieval_method"] == "command_keyword_index"


# ── the log itself ──────────────────────────────────────────────────────────

def test_retrieval_method_is_none_only_when_nothing_was_read():
    from advisor.agents.tools import record_lookup, recording_retrieval, with_retrieval
    with recording_retrieval() as log:
        pass
    assert with_retrieval({"response": "x"}, log)["retrieval_method"] == "none"
    with recording_retrieval() as log:
        record_lookup("symbol_store", 0.002)
    out = with_retrieval({"response": "x"}, log)
    assert out["retrieval_method"] == "symbol_store" and out["num_sources"] == 0 and out["retrieval_time"] > 0
    assert "retrieval_method" not in with_retrieval({"response": "x"}, None)    # no log: claim nothing


def test_record_lookup_outside_a_request_is_a_no_op():
    from advisor.agents.tools import record_lookup
    record_lookup("symbol_store", 0.1, [{"collection": "symbol_store", "file_path": "x", "text": ""}])


def test_lookup_sources_carry_no_made_up_score():
    """A lookup has no relevance score; consumers default a missing score, so none is invented."""
    from advisor.agents.tools import lookup_source
    src = lookup_source("symbol_store", "pyegeria/glossary_manager.py", "GlossaryManager", "class GlossaryManager")
    assert "score" not in src and src["collection"] == src["_collection"] == "symbol_store"
