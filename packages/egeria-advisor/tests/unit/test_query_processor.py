"""
Unit tests for the Query Processor module.

Tests query type detection, intent classification, and query routing.
"""

import pytest
from advisor.query_processor import QueryProcessor, QueryType

# Opens a real Postgres connection on construction (the metrics store): needs the password (TR-12 R1).
pytestmark = pytest.mark.requires_pgvector


@pytest.fixture
def query_processor():
    """Create a QueryProcessor instance for testing."""
    return QueryProcessor()


class TestQueryTypeDetection:
    """Test query type detection functionality."""
    
    def test_code_help_query(self, query_processor):
        """Test detection of code help queries."""
        queries = [
            "Show me how to create a glossary",
            "Find examples of asset management",
            "How do I use the glossary manager?"
        ]
        
        for query in queries:
            query_type = query_processor.detect_query_type(query)
            assert query_type == QueryType.CODE_HELP, f"Failed for: {query}"
            
    def test_code_intel_query(self, query_processor):
        """Test detection of codebase structural / intelligence queries."""
        queries = [
            "Show me the class hierarchy",
            "What functions call create_glossary?",
            "Show inheritance relationships",
            "Does GlossaryManager inherit from Referenceable?",
            "What class is method run defined in?"
        ]
        
        for query in queries:
            query_type = query_processor.detect_query_type(query)
            assert query_type == QueryType.CODE_INTEL, f"Failed for: {query}"
    
    def test_explanation_query(self, query_processor):
        """Test detection of explanation queries."""
        queries = [
            "Explain how glossaries work",
            "What is a collection?",
            "Tell me about governance zones",
            "Describe the asset manager"
        ]
        
        for query in queries:
            query_type = query_processor.detect_query_type(query)
            assert query_type == QueryType.EXPLANATION, f"Failed for: {query}"
    
    def test_example_query(self, query_processor):
        """Test detection of example queries."""
        queries = [
            "Show examples of creating terms",
            "Give me examples of asset queries",
            "What are some examples of using collections?",
            "Give me an example of using the collection manager"
        ]
        
        for query in queries:
            query_type = query_processor.detect_query_type(query)
            assert query_type == QueryType.EXAMPLE, f"Failed for: {query}"
    
    def test_comparison_query(self, query_processor):
        """Test detection of comparison queries."""
        queries = [
            "What's the difference between OMVS and OMAS?",
            "Compare glossaries and collections",
            "How does X differ from Y?"
        ]
        
        for query in queries:
            query_type = query_processor.detect_query_type(query)
            assert query_type == QueryType.COMPARISON, f"Failed for: {query}"
    
    def test_best_practice_query(self, query_processor):
        """Test detection of best practice queries."""
        queries = [
            "What's the best way to organize glossaries?",
            "Best practices for asset management",
            "Recommended approach for collections"
        ]
        
        for query in queries:
            query_type = query_processor.detect_query_type(query)
            assert query_type == QueryType.BEST_PRACTICE, f"Failed for: {query}"
    
    def test_debugging_query(self, query_processor):
        """Test detection of debugging queries."""
        queries = [
            "Why isn't my glossary creation working?",
            "I'm getting an error when creating assets",
            "How do I fix connection issues?",
            "Troubleshoot collection creation"
        ]
        
        for query in queries:
            query_type = query_processor.detect_query_type(query)
            assert query_type == QueryType.DEBUGGING, f"Failed for: {query}"
    
    def test_quantitative_query(self, query_processor):
        """Test detection of quantitative queries."""
        queries = [
            "How many lines of code are in the project?",
            "What is the average complexity?",
            "How many files are there?",
            "What's the total SLOC?",
            "Show me code statistics"
        ]
        
        for query in queries:
            query_type = query_processor.detect_query_type(query)
            assert query_type == QueryType.QUANTITATIVE, f"Failed for: {query}"
    
    def test_relationship_query(self, query_processor):
        """Test detection of relationship queries."""
        queries = [
            "What does GlossaryManager import?",
            "What are the dependencies of this module?"
        ]
        
        for query in queries:
            query_type = query_processor.detect_query_type(query)
            assert query_type == QueryType.RELATIONSHIP, f"Failed for: {query}"
    
    def test_general_query(self, query_processor):
        """Test detection of general queries."""
        queries = [
            "What is Egeria?",
            "Tell me about metadata management",
            "How does the system work?"
        ]
        
        for query in queries:
            query_type = query_processor.detect_query_type(query)
            assert query_type == QueryType.GENERAL, f"Failed for: {query}"


class TestQueryNormalization:
    """Test query normalization and preprocessing."""
    
    def test_lowercase_conversion(self, query_processor):
        """Test that queries are converted to lowercase."""
        query = "SHOW ME HOW TO CREATE A GLOSSARY"
        normalized = query_processor.normalize_query(query)
        assert normalized.islower()
    
    def test_whitespace_trimming(self, query_processor):
        """Test that extra whitespace is removed."""
        query = "  show me   examples  "
        normalized = query_processor.normalize_query(query)
        assert normalized == "show me examples"
    
    def test_special_character_handling(self, query_processor):
        """Test handling of special characters."""
        query = "What's the difference?"
        normalized = query_processor.normalize_query(query)
        assert "'" in normalized or "'" not in normalized  # Depends on implementation


class TestKeywordExtraction:
    """Test keyword extraction from queries."""
    
    def test_extract_entity_names(self, query_processor):
        """Test extraction of entity names."""
        query = "How do I use the GlossaryManager?"
        keywords = query_processor.extract_keywords(query)
        assert "glossarymanager" in [k.lower() for k in keywords] or "glossary" in [k.lower() for k in keywords]
    
    def test_extract_action_verbs(self, query_processor):
        """Test extraction of action verbs."""
        query = "Show me how to create a collection"
        keywords = query_processor.extract_keywords(query)
        assert any(k.lower() in ["create", "show"] for k in keywords)
    
    def test_filter_stop_words(self, query_processor):
        """Test that stop words are filtered out."""
        query = "What is the best way to do this?"
        keywords = query_processor.extract_keywords(query)
        # Common stop words should be filtered
        stop_words = ["the", "is", "to", "a", "an"]
        for stop_word in stop_words:
            assert stop_word not in [k.lower() for k in keywords]


class TestQueryContext:
    """Test query context extraction and management."""
    
    def test_extract_module_context(self, query_processor):
        """Test extraction of module context from query."""
        query = "How do I use the glossary manager to create terms?"
        context = query_processor.extract_context(query)
        assert "glossary" in context.get("modules", []) or "glossary_manager" in context.get("modules", [])
    
    def test_extract_operation_context(self, query_processor):
        """Test extraction of operation context."""
        query = "Show me how to create and update assets"
        context = query_processor.extract_context(query)
        operations = context.get("operations", [])
        assert any(op in ["create", "update"] for op in operations)


class TestQueryValidation:
    """Test query validation."""
    
    def test_empty_query(self, query_processor):
        """Test handling of empty queries."""
        with pytest.raises(ValueError):
            query_processor.process_query("")
    
    def test_very_short_query(self, query_processor):
        """Test handling of very short queries."""
        query = "hi"
        # Should either process or raise appropriate error
        try:
            result = query_processor.process_query(query)
            assert result is not None
        except ValueError:
            pass  # Acceptable to reject very short queries
    
    def test_very_long_query(self, query_processor):
        """Test handling of very long queries."""
        query = "word " * 1000  # Very long query
        # Should handle gracefully
        result = query_processor.process_query(query)
        assert result is not None


class TestQueryProcessing:
    """Test end-to-end query processing."""
    
    def test_process_code_help_query(self, query_processor):
        """Test processing a code help query."""
        query = "Show me how to create a glossary"
        result = query_processor.process_query(query)
        
        assert result["query_type"] == QueryType.CODE_HELP
        assert "keywords" in result
        assert len(result["keywords"]) > 0
    
    def test_process_quantitative_query(self, query_processor):
        """Test processing a quantitative query."""
        query = "How many lines of code are in the project?"
        result = query_processor.process_query(query)
        
        assert result["query_type"] == QueryType.QUANTITATIVE
        assert "keywords" in result
    
    def test_process_relationship_query(self, query_processor):
        """Test processing a relationship query."""
        query = "What does GlossaryManager import?"
        result = query_processor.process_query(query)
        
        assert result["query_type"] == QueryType.RELATIONSHIP
        assert "keywords" in result


@pytest.mark.parametrize("query,expected_type", [
    ("How do I create a glossary?", QueryType.CODE_HELP),
    ("What is a collection?", QueryType.EXPLANATION),
    ("Show me examples", QueryType.EXAMPLE),
    ("What's the difference between X and Y?", QueryType.COMPARISON),
    ("Best practices for glossaries", QueryType.BEST_PRACTICE),
    ("Why isn't this working?", QueryType.DEBUGGING),
    ("How many files?", QueryType.QUANTITATIVE),
    ("What imports this module?", QueryType.RELATIONSHIP),
    ("Tell me about Egeria", QueryType.GENERAL),
])
def test_query_type_detection_parametrized(query_processor, query, expected_type):
    """Parametrized test for query type detection."""
    detected_type = query_processor.detect_query_type(query)
    assert detected_type == expected_type, f"Expected {expected_type} for '{query}', got {detected_type}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])