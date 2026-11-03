"""Tests for QueryProcessor intent classification."""
from __future__ import annotations

import pytest

from resource_explorer.query_processor import QueryIntent, QueryProcessor


@pytest.fixture
def qp():
    return QueryProcessor()


class TestStatisticalIntent:
    @pytest.mark.parametrize("query", [
        "how many commits last month",
        "how many contributors does this project have",
        "show me a chart of star growth",
        "commit history over the last year",
        "release cadence for this project",
        "lines of code in the repo",
        "growth over time",
    ])
    def test_statistical_queries(self, qp, query):
        assert qp.classify(query) == QueryIntent.STATISTICAL


class TestComparisonIntent:
    @pytest.mark.parametrize("query", [
        "compare project-a and project-b",
        "compare foo versus bar",
        "difference between foo and bar",
        "which project is better",
        "pros and cons of both",
    ])
    def test_comparison_queries(self, qp, query):
        assert qp.classify(query) == QueryIntent.COMPARISON


class TestHealthIntent:
    @pytest.mark.parametrize("query", [
        "is this project actively maintained",
        "how actively maintained is it",
        "community health of the project",
        "what is the bus factor",
        "contributor diversity",
        "is this project abandoned",
        "last activity",
    ])
    def test_health_queries(self, qp, query):
        assert qp.classify(query) == QueryIntent.HEALTH


class TestCodeSearchIntent:
    @pytest.mark.parametrize("query", [
        "how do I use the authentication module",
        "show me an example of creating a client",
        "how is the parser implemented",
        "source code for the main function",
    ])
    def test_code_queries(self, qp, query):
        assert qp.classify(query) == QueryIntent.CODE_SEARCH


class TestConceptualIntent:
    @pytest.mark.parametrize("query", [
        "what is the overall architecture",
        "how does the routing work",
        "explain the configuration system",
        "getting started guide",
        "how to install",
    ])
    def test_conceptual_queries(self, qp, query):
        assert qp.classify(query) == QueryIntent.CONCEPTUAL


class TestGeneralFallback:
    def test_unmatched_query_returns_general(self, qp):
        assert qp.classify("tell me about the project") == QueryIntent.GENERAL

    def test_empty_string_returns_general(self, qp):
        assert qp.classify("") == QueryIntent.GENERAL


class TestPriority:
    def test_statistical_beats_conceptual(self, qp):
        # "how many" matches statistical before conceptual
        assert qp.classify("how many files are there") == QueryIntent.STATISTICAL

    def test_health_beats_general(self, qp):
        assert qp.classify("is it abandoned") == QueryIntent.HEALTH


class TestRelationshipRoutesToIntegration:
    """"Relationship between X and Y" is an integration question (owner, 2026-10-25); before, it fell to conceptual
    ("what is ...") or to general."""

    @pytest.mark.parametrize("query", [
        "What is the relationship between docling and data-prep-kit?",
        "relationship between Egeria and OpenLineage",
        "how are kafka and flink related?",
        "what's the relation of marquez to openlineage",
    ])
    def test_relationship_questions_route_to_integration(self, qp, query):
        assert qp.classify(query) == QueryIntent.INTEGRATION

    def test_difference_between_stays_comparison(self, qp):
        assert qp.classify("what is the difference between docling and marker") == QueryIntent.COMPARISON
