"""TR-98 absence-as-answer defects in analytics.

When no cached metrics file exists, answers should state counts were not measured (not 0),
and cache-only answers should report retrieval_time: None instead of 0.0.

Offline: a stand-in database, symbol reader and model.
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


CLASS_ROW = {"name": "GlossaryManager", "project_slug": "egeria_python_git",
             "file_path": "pyegeria/glossary_manager.py", "start_line": 40, "end_line": 900,
             "signature": "class GlossaryManager(Client2)", "docstring": "Manage glossaries.",
             "parent_class": ""}


# ── code_intel_agent ────────────────────────────────────────────────────────

def test_a_class_answer_reports_the_definition_it_read(monkeypatch, llm):
    agent, db = _code_intel(monkeypatch,
                            [("SELECT name FROM", [{"name": "GlossaryManager"}]),
                             ("kind = 'class' AND name ILIKE", [CLASS_ROW]),
                             ("WITH RECURSIVE ancestors", [{"class_name": "Client2", "depth": 1,
                                                            "project_slug": "egeria_python_git"}])])
    out = agent.handle("what is GlossaryManager")
    assert "pyegeria/glossary_manager.py" in llm.prompts[0]
    paths = [s["file_path"] for s in out["sources"]]
    assert "pyegeria/glossary_manager.py" in paths
    assert out["num_sources"] == len(out["sources"]) >= 2
    assert {s["collection"] for s in out["sources"]} == {"symbol_store"}
    assert out["retrieval_time"] > 0.0
    assert out["retrieval_method"] == "symbol_store"


def test_a_name_that_isnt_there_counts_the_lookup_but_no_sources(monkeypatch, llm):
    agent, _ = _code_intel(monkeypatch, [])
    out = agent.handle("what is FluxCapacitor")
    assert out["num_sources"] == 0 and out["sources"] == []
    assert out["retrieval_time"] > 0.0
    assert out["retrieval_method"] == "symbol_store"


def test_a_clarifying_answer_read_nothing_and_says_so(monkeypatch, llm):
    agent, db = _code_intel(monkeypatch, [])
    out = agent.handle("list classes")
    assert db.queries == []
    assert out["num_sources"] == 0 and out["retrieval_time"] == 0.0
    assert out["retrieval_method"] == "none"


def test_codebase_stats_report_the_table_they_counted(monkeypatch, llm):
    agent, _ = _code_intel(monkeypatch,
                           [("GROUP BY kind", [{"kind": "class", "count": 12, "total_loc": 3400}])])
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


# ── TR-98 absence-as-answer tests ───────────────────────────────────────────

def test_missing_metrics_file_answer_does_not_say_0_classes(monkeypatch, tmp_path):
    """When no metrics file exists, answer should say counts were not measured."""
    from advisor import analytics as an
    from advisor import re_code_symbol_reader as rr
    
    # No metrics file, no symbol store
    monkeypatch.setattr(rr, "get_re_code_symbol_reader", lambda: (_ for _ in ()).throw(ConnectionError("down")))
    manager = an.AnalyticsManager(cache_dir=tmp_path)
    
    out = _rag()._quantitative_answer("how many classes are there", None, manager)
    
    # Should NOT say "0 classes" - should indicate counts were not measured
    assert "not measured" in out["response"].lower() or "no data" in out["response"].lower()
    assert "0 classes" not in out["response"]
    # Should have no sources since nothing was read
    assert out["num_sources"] == 0


def test_real_zero_answer_still_says_0_classes(monkeypatch, tmp_path):
    """When metrics file exists and says 0, answer should still say "0 classes"."""
    from advisor import analytics as an
    from advisor import re_code_symbol_reader as rr
    
    # Create metrics file with 0 classes
    (tmp_path / "enhanced_metrics.json").write_text(json.dumps({
        "files_analyzed": 10, "total_classes": 0, "total_functions": 0, "total_methods": 0
    }))
    monkeypatch.setattr(rr, "get_re_code_symbol_reader", lambda: (_ for _ in ()).throw(ConnectionError("down")))
    manager = an.AnalyticsManager(cache_dir=tmp_path)
    
    out = _rag()._quantitative_answer("how many classes are there", None, manager)
    
    # Should say 0 classes since file exists and says 0
    assert "0 classes" in out["response"]
    # Should have one source (the metrics file)
    assert out["num_sources"] == 1


def test_cache_only_answer_reports_retrieval_time_none(monkeypatch, tmp_path):
    """When answer comes from cache (metrics file), retrieval_time should be None."""
    from advisor import analytics as an
    from advisor import re_code_symbol_reader as rr
    
    (tmp_path / "enhanced_metrics.json").write_text(json.dumps({"files_analyzed": 10, "total_classes": 7}))
    monkeypatch.setattr(rr, "get_re_code_symbol_reader", lambda: (_ for _ in ()).throw(ConnectionError("down")))
    manager = an.AnalyticsManager(cache_dir=tmp_path)
    
    out = _rag()._quantitative_answer("how many classes are there", None, manager)
    
    # Should have retrieval_time: None (not measured), not 0.0
    assert out["retrieval_time"] is None
    assert out["retrieval_method"] == "analytics_cache"


def test_with_symbol_store_retrieval_time_is_measured(monkeypatch, tmp_path):
    """When answer comes from symbol store, retrieval_time should be measured."""
    from advisor import analytics as an
    from advisor import re_code_symbol_reader as rr
    
    db = _DB([("WHERE kind = 'class'", [{"name": "GlossaryManager", "collection": "pyegeria",
                                         "file_path": "pyegeria/glossary_manager.py", "start_line": 40, "loc": 860,
                                         "docstring": "Manage glossaries."}])])
    reader = rr.ReCodeSymbolReader.__new__(rr.ReCodeSymbolReader)
    reader.db_manager = db
    monkeypatch.setattr(rr, "get_re_code_symbol_reader", lambda: reader)
    
    manager = an.AnalyticsManager(cache_dir=tmp_path)
    
    out = _rag()._quantitative_answer("how many classes are there", "pyegeria", manager)
    
    # Should have measured retrieval_time > 0
    assert out["retrieval_time"] is not None
    assert out["retrieval_time"] > 0.0
    assert out["retrieval_method"] == "symbol_store"


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
    assert out["retrieval_time"] > 0.0


def test_a_command_discovery_answer_when_no_commands_found_counts_the_lookup(monkeypatch):
    """When command keyword index returns nothing, it still counts its lookup time."""
    monkeypatch.setattr("advisor.command_keyword_index.get_command_keyword_index", lambda: _Index([]))
    out = _rag()._handle_command_discovery("what dr.egeria commands are there for nonexistent?")
    assert out["num_sources"] == 0
    assert out["retrieval_time"] > 0.0
    assert out["retrieval_method"] == "command_keyword_index"


# ── tools.py with_retrieval() tests ─────────────────────────────────────────

def test_with_retrieval_handles_none_retrieval_time():
    """with_retrieval should handle None retrieval_time without crashing."""
    from advisor.agents.tools import with_retrieval
    
    # When log is None, nothing is claimed: the result comes back as it was (no time is invented, not even None)
    result = {"response": "test"}
    out = with_retrieval(result, None)
    assert out == {"response": "test"}
    
    # When log exists but seconds is 0.0, should use that
    log = {"sources": [], "seconds": 0.0, "searches": 0, "methods": []}
    result = {"response": "test", "sources": [], "num_sources": 0, "retrieval_time": 0.0}
    out = with_retrieval(result, log)
    assert out["retrieval_time"] == 0.0
    
    # When log exists with measured time, should use that
    log = {"sources": [], "seconds": 0.123, "searches": 1, "methods": ["symbol_store"]}
    result = {"response": "test", "sources": [], "num_sources": 0, "retrieval_time": 0.0}
    out = with_retrieval(result, log)
    assert out["retrieval_time"] == 0.123


def test_with_retrieval_preserves_existing_sources():
    """with_retrieval should not overwrite existing sources."""
    from advisor.agents.tools import with_retrieval
    
    log = {"sources": [], "seconds": 0.1, "searches": 1, "methods": ["symbol_store"]}
    result = {"response": "test", "sources": [{"name": "existing"}], "num_sources": 1}
    out = with_retrieval(result, log)
    assert len(out["sources"]) == 1
    assert out["num_sources"] == 1


def test_with_retrieval_computes_avg_score():
    """with_retrieval should compute average relevance score from sources."""
    from advisor.agents.tools import with_retrieval
    
    from advisor.agents.tools import lookup_source
    sources = [{**lookup_source("docs", "a.md", "a", "text a"), "score": 0.8},
               {**lookup_source("docs", "b.md", "b", "text b"), "score": 0.6}]
    log = {"sources": sources, "seconds": 0.1, "searches": 1, "methods": ["vector_search"]}
    result = {"response": "test", "sources": [], "num_sources": 0, "retrieval_time": 0.0}
    out = with_retrieval(result, log)
    assert out["avg_relevance_score"] == 0.7


def test_with_retrieval_handles_empty_sources():
    """with_retrieval should handle empty sources list."""
    from advisor.agents.tools import with_retrieval
    
    log = {"sources": [], "seconds": 0.1, "searches": 1, "methods": ["vector_search"]}
    result = {"response": "test", "sources": [], "num_sources": 0, "retrieval_time": 0.0}
    out = with_retrieval(result, log)
    assert out["avg_relevance_score"] == 0.0


# ── Hand-correction round (Claude) on the loop's result ─────────────────────────────────────────────────────────────────


@pytest.fixture
def _no_db(monkeypatch):
    from advisor.analytics import AnalyticsManager
    monkeypatch.setattr(AnalyticsManager, "_symbol_store", lambda self: None)


def test_a_missing_metrics_file_names_the_files_it_looked_for(tmp_path, _no_db):
    from advisor.analytics import AnalyticsManager
    out = AnalyticsManager(cache_dir=tmp_path).answer_quantitative_query("how many classes are there")
    assert "not measured" in out.lower() and "0 classes" not in out
    assert "enhanced_metrics.json" in out and "pipeline_summary.json" in out


def test_a_metrics_file_that_holds_no_statistics_is_not_a_zero(tmp_path, _no_db):
    """A file that parses but carries no statistics (`{}`) measured nothing; it must not answer "0 classes"."""
    from advisor.analytics import AnalyticsManager
    (tmp_path / "enhanced_metrics.json").write_text("{}")
    out = AnalyticsManager(cache_dir=tmp_path).answer_quantitative_query("how many classes are there")
    assert "not measured" in out.lower() and "0 classes" not in out and "enhanced_metrics.json" in out


def test_with_retrieval_changes_nothing_it_was_not_asked_to():
    """Unrelated results keep their old shape: no log means nothing is claimed, and a result without a time gets none."""
    from advisor.agents.tools import with_retrieval
    assert with_retrieval({"response": "x"}, None) == {"response": "x"}
    already = {"response": "x", "num_sources": 2, "retrieval_time": 0.5}
    assert with_retrieval(dict(already), {"sources": [], "seconds": 0.0, "searches": 0, "methods": []}) == already


def test_a_measured_zero_second_read_is_not_mistaken_for_unmeasured():
    """"Not measured" is tracked, not inferred from 0 seconds: a read that really took 0.0 s is a measured 0.0."""
    from advisor.agents.tools import record_lookup, recording_retrieval, with_retrieval
    with recording_retrieval() as log:
        record_lookup("symbol_store", 0.0, [])
    assert with_retrieval({"response": "x"}, log)["retrieval_time"] == 0.0


# ── Second read-back: a time that was not measured must not reach MLflow or the metrics table as a number ───────────────


class _FakeTracker:
    def __init__(self):
        self.logged = []

    def log_metrics(self, metrics):
        self.logged.append(dict(metrics))

    def add_relevance(self, score):
        pass


def _bare_rag(tracker=None, collector=None):
    import contextlib

    from advisor.rag_system import RAGSystem
    rag = RAGSystem.__new__(RAGSystem)

    class _Mlflow:
        @contextlib.contextmanager
        def track_operation(self, **kwargs):
            yield tracker

    rag.mlflow_tracker = _Mlflow() if tracker is not None else None
    rag.metrics_collector = collector
    return rag


def test_mlflow_is_not_sent_a_retrieval_time_that_was_not_measured():
    """MLflow's log_metrics raises on a None value, which dropped the WHOLE row (response_length included) for every
    cache-only answer. None-valued keys are dropped; everything measured is kept."""
    tracker = _FakeTracker()
    result = {"query": "how many classes", "response": "There are **3 classes**", "num_sources": 1,
              "retrieval_time": None, "generation_time": 0.0, "avg_relevance_score": 0.0, "context_length": 5, "sources": []}
    _bare_rag(tracker=tracker)._track_mlflow(result, include_context=False)
    (logged,) = tracker.logged
    assert "retrieval_time" not in logged
    assert logged["response_length"] == len(result["response"]) and logged["num_sources"] == 1
    assert all(v is not None for v in logged.values())


def test_mlflow_still_gets_a_measured_retrieval_time():
    tracker = _FakeTracker()
    result = {"query": "q", "response": "r", "num_sources": 1, "retrieval_time": 0.25, "generation_time": 1.0,
              "avg_relevance_score": 0.5, "context_length": 1, "sources": []}
    _bare_rag(tracker=tracker)._track_mlflow(result, include_context=False)
    assert tracker.logged[0]["retrieval_time"] == 0.25


def _recorded_metric(retrieval_time):
    class _Collector:
        def __init__(self):
            self.metrics = []

        def record_query(self, metric):
            self.metrics.append(metric)

    collector = _Collector()
    result = {"query": "q", "response": "r", "num_sources": 0, "retrieval_time": retrieval_time, "generation_time": 2.0,
              "sources": [], "query_type": "GENERAL"}
    _bare_rag(collector=collector)._record_local_metrics(result)
    return collector.metrics[0]


def test_an_unmeasured_search_time_is_stored_as_null_not_zero_milliseconds():
    metric = _recorded_metric(None)
    assert metric.search_time_ms is None          # the column is nullable: "not measured", not "0 ms"
    assert metric.latency_ms == pytest.approx(2000.0)   # NOT NULL: the generation time alone, no invented search time


def test_a_measured_search_time_is_stored_in_milliseconds():
    metric = _recorded_metric(0.25)
    assert metric.search_time_ms == pytest.approx(250.0) and metric.latency_ms == pytest.approx(2250.0)
