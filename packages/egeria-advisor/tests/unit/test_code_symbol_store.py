"""CodeSymbolStore round trip.

Isolation (Phase 1 Q1-1, 2026-11-10): runs against conftest's ea_test_db, EA's db manager scoped to a
throwaway schema (`ea_test_<pid>_<hex>`, dropped afterwards). Until then it wrote EA's real
code_symbols / code_relationships in the shared Postgres on every EA unit run.
"""
import pytest
from advisor.code_symbol_store import get_symbol_store
from advisor.data_prep.code_parser import CodeElement
from advisor.db_consolidated import get_db_manager

def test_code_symbol_store(ea_test_db, ea_test_schema):
    store = get_symbol_store()
    assert store.db_manager is ea_test_db             # the throwaway schema, never public
    
    # 1. Clear test collection
    test_col = "test_collection_dummy"
    store.clear_collection(test_col)
    
    # Verify it is empty
    summary = store.collection_summary(test_col)
    assert test_col not in summary
    
    # 2. Upsert symbols
    el1 = CodeElement(
        type="class",
        name="TestClass",
        file_path="test_file.py",
        line_number=10,
        end_line_number=50,
        docstring="A test class",
        signature="class TestClass(BaseTest)",
        body="...",
        bases=["BaseTest"]
    )
    
    el2 = CodeElement(
        type="method",
        name="test_method",
        file_path="test_file.py",
        line_number=20,
        end_line_number=30,
        docstring="A test method",
        signature="def test_method(self)",
        body="...",
        parent_class="TestClass"
    )
    
    n = store.upsert_symbols(test_col, [el1, el2])
    assert n == 2
    
    # Verify count by kind
    assert store.count_by_kind("class", test_col) == 1
    assert store.count_by_kind("method", test_col) == 1
    
    # Verify list classes
    classes = store.list_classes(test_col)
    assert len(classes) == 1
    assert classes[0]["name"] == "TestClass"
    
    # Verify methods for class
    methods = store.methods_for_class("TestClass", test_col)
    assert len(methods) == 1
    assert methods[0]["name"] == "test_method"
    
    # Verify relationships are created in database
    db = get_db_manager()
    rows = db.execute_query(
        "SELECT * FROM code_relationships WHERE collection = %s ORDER BY relationship_type",
        (test_col,)
    )
    assert len(rows) == 2
    # One contains_method, one inherits_from
    rel_types = [r["relationship_type"] for r in rows]
    assert "contains_method" in rel_types
    assert "inherits_from" in rel_types
    
    # Clean up
    store.clear_collection(test_col)
    assert test_col not in store.collection_summary(test_col)
