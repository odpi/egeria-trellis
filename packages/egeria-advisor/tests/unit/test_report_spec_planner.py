import pytest
from advisor.agents.report_spec_agent import calculate_required_depth, ReportSpecAgent
from advisor.report_spec_parser import parse_report_spec_markdown
from pyegeria.view._output_format_models import FormatSet

def test_calculate_required_depth():
    assert calculate_required_depth("") == 0
    assert calculate_required_depth("guid") == 0
    assert calculate_required_depth("properties.displayName") == 0
    assert calculate_required_depth("elementHeader.status") == 0
    assert calculate_required_depth("member_of[].properties.displayName") == 1
    assert calculate_required_depth("categories[].terms[].guid") == 2
    assert calculate_required_depth("classifications[].classificationName") == 1
    assert calculate_required_depth("relatedElement.properties.displayName") == 0

def test_report_spec_agent_auto_tuning(monkeypatch):
    # Create a mock report spec markdown with multiple columns at different depths
    rsd_content = """
# Custom Optimize Report

## Create Report Spec
### Target Type
Glossary
### Heading
Optimize Test
### Action Function
EgeriaTech.find_glossaries
### Spec Params
search_string=*

## Create Column
### Name
Name
### Key
properties.displayName

## Create Column
### Name
Collection Name
### Key
member_of[].properties.displayName
"""
    # Instantiate the agent
    agent = ReportSpecAgent()
    
    # Mock doc manager loading
    class MockDocManager:
        def load(self, doc_id):
            return rsd_content
            
    monkeypatch.setattr("advisor.agents.report_spec_agent.get_report_spec_doc_manager", lambda: MockDocManager())
    
    # Mock connection reading
    class MockPipeline:
        def _read_pyegeria_connection(self, egeria_credentials=None):
            return {
                "view_server": "dev_server",
                "platform_url": "http://localhost:9443",
                "user_id": "test_user",
                "user_pwd": "password"
            }
    monkeypatch.setattr("advisor.agents.report_spec_agent.get_report_pipeline", lambda: MockPipeline())
    
    # Mock exec_report_spec to capture parameters passed to it
    captured_params = {}
    def mock_exec_report_spec(doc_id, output_format, params, **kwargs):
        nonlocal captured_params
        captured_params = params
        return {"kind": "empty"}
        
    monkeypatch.setattr("pyegeria.exec_report_spec", mock_exec_report_spec)
    
    # Run execution
    res = agent.execute("test_optimize_report", output_format="TABLE")
    
    assert res["status"] == "Completed"
    # Verify auto-tuned parameters
    # The columns: "properties.displayName" (depth 0), "member_of[].properties.displayName" (depth 1)
    # The max depth is 1
    assert captured_params.get("graph_query_depth") == 1
    # Verify it doesn't set skip_relationships because max depth > 0
    assert "skip_relationships" not in captured_params

def test_report_spec_agent_auto_tuning_depth_zero(monkeypatch):
    # Only depth 0 columns
    rsd_content = """
# Custom Depth Zero Report

## Create Report Spec
### Target Type
Glossary
### Heading
Optimize Test Zero
### Action Function
EgeriaTech.find_glossaries
### Spec Params
search_string=*

## Create Column
### Name
Name
### Key
properties.displayName
"""
    agent = ReportSpecAgent()
    
    class MockDocManager:
        def load(self, doc_id):
            return rsd_content
            
    monkeypatch.setattr("advisor.agents.report_spec_agent.get_report_spec_doc_manager", lambda: MockDocManager())
    
    class MockPipeline:
        def _read_pyegeria_connection(self, egeria_credentials=None):
            return {
                "view_server": "dev_server",
                "platform_url": "http://localhost:9443",
                "user_id": "test_user",
                "user_pwd": "password"
            }
    monkeypatch.setattr("advisor.agents.report_spec_agent.get_report_pipeline", lambda: MockPipeline())
    
    captured_params = {}
    def mock_exec_report_spec(doc_id, output_format, params, **kwargs):
        nonlocal captured_params
        captured_params = params
        return {"kind": "empty"}
        
    monkeypatch.setattr("pyegeria.exec_report_spec", mock_exec_report_spec)
    
    # Run execution
    res = agent.execute("test_optimize_report_zero", output_format="TABLE")
    
    assert captured_params.get("graph_query_depth") == 0
    # Auto-tuned skip_relationships to True
    assert captured_params.get("skip_relationships") is True


def test_get_report_draft_schema_endpoint(monkeypatch):
    """The route requires a signed-in user and forwards their Egeria credentials
    (token-shaped since 2026-09-04) to the discovery helper."""
    from advisor.web.reports import get_report_draft_schema

    captured = {}

    async def mock_discover(draft_id, egeria_credentials=None):
        captured["draft_id"] = draft_id
        captured["creds"] = egeria_credentials
        return {"state": "ok", "attributes": [{"attribute_path": "guid", "data_type": "string"}], "message": None}

    fake_creds = {"user_id": "test_user", "password": "", "token": "egeria-token"}
    monkeypatch.setattr("advisor.web.reports.discover_draft_schema_with_status", mock_discover)
    monkeypatch.setattr("advisor.auth.require_egeria_user", lambda request: {"user_id": "test_user"})
    monkeypatch.setattr("advisor.auth.get_egeria_credentials", lambda request: fake_creds)

    import asyncio
    res = asyncio.run(get_report_draft_schema(object(), "test_draft"))
    assert res == [{"attribute_path": "guid", "data_type": "string"}]
    assert captured == {"draft_id": "test_draft", "creds": fake_creds}


def test_discover_draft_schema_internal(monkeypatch):
    from advisor.web.reports import discover_draft_schema_internal

    class MockDraftManager:
        def load(self, draft_id):
            return {
                "draft_id": draft_id,
                "title": "Test Title",
                "columns": [],
                "answers": {"Heading": "Test Title"}
            }
    # Import the elicitor BEFORE patching report_draft: it binds get_report_draft_manager by name at import, so a
    # first import while the patch is on would keep the mock for every later test on this worker.
    import advisor.agents.report_spec_elicitor  # noqa: F401
    monkeypatch.setattr("advisor.report_draft.get_report_draft_manager", lambda *a, **k: MockDraftManager())
    
    class MockElicitor:
        def _generate_report_spec_md(self, draft):
            return "# Test Spec\n"
    monkeypatch.setattr("advisor.agents.report_spec_elicitor.get_report_spec_elicitor", lambda: MockElicitor())
    
    monkeypatch.setattr("advisor.report_spec_parser.parse_report_spec_markdown", lambda md: None)
    monkeypatch.setattr("advisor.report_spec_parser.register_report_spec", lambda name, spec: None)
    
    class MockPipeline:
        def _read_pyegeria_connection(self, egeria_credentials=None):
            return {
                "view_server": "dev_server",
                "platform_url": "http://localhost:9443",
                "user_id": "test_user",
                "user_pwd": "password"
            }
    monkeypatch.setattr("advisor.report_pipeline.get_report_pipeline", lambda: MockPipeline())
    
    class MockEgeriaTech:
        def __init__(self, **kwargs):
            self.user_id, self.user_pwd = kwargs.get("user_id"), kwargs.get("user_pwd")   # as pyegeria's client
        def create_egeria_bearer_token(self):
            pass
        def get_report_spec_schema(self, **kwargs):
            return [{"attribute_path": "guid", "data_type": "string"}]
            
    monkeypatch.setattr("pyegeria.egeria_tech_client.EgeriaTech", MockEgeriaTech)
    
    import asyncio
    res = asyncio.run(discover_draft_schema_internal("test_draft"))
    assert res == [{"attribute_path": "guid", "data_type": "string"}]


def test_discover_draft_schema_cache(monkeypatch):
    from advisor.web.reports import discover_draft_schema_internal, _SCHEMA_CACHE
    import time
    
    # Reset cache
    _SCHEMA_CACHE.clear()
    
    # Mock draft manager with a stateful draft configuration
    draft_store = {
        "test_draft": {
            "draft_id": "test_draft",
            "title": "Test Title",
            "action_function": "GlossaryManager.find_glossaries",
            "target_type": "Glossary",
            "columns": [],
            "answers": {"Heading": "Test Title"}
        }
    }
    class MockDraftManager:
        def load(self, draft_id):
            return draft_store.get(draft_id)
    # Import the elicitor BEFORE patching report_draft: it binds get_report_draft_manager by name at import, so a
    # first import while the patch is on would keep the mock for every later test on this worker.
    import advisor.agents.report_spec_elicitor  # noqa: F401
    monkeypatch.setattr("advisor.report_draft.get_report_draft_manager", lambda *a, **k: MockDraftManager())
    
    class MockElicitor:
        def _generate_report_spec_md(self, draft): return "# Test Spec\n"
    monkeypatch.setattr("advisor.agents.report_spec_elicitor.get_report_spec_elicitor", lambda: MockElicitor())
    monkeypatch.setattr("advisor.report_spec_parser.parse_report_spec_markdown", lambda md: None)
    monkeypatch.setattr("advisor.report_spec_parser.register_report_spec", lambda name, spec: None)
    
    class MockPipeline:
        def _read_pyegeria_connection(self, egeria_credentials=None):
            return {
                "view_server": "dev_server",
                "platform_url": "http://localhost:9443",
                "user_id": "test_user",
                "user_pwd": "password"
            }
    monkeypatch.setattr("advisor.report_pipeline.get_report_pipeline", lambda: MockPipeline())
    
    call_count = 0
    class MockEgeriaTech:
        def __init__(self, **kwargs): self.user_id, self.user_pwd = kwargs.get("user_id"), kwargs.get("user_pwd")
        def create_egeria_bearer_token(self): pass
        def get_report_spec_schema(self, **kwargs):
            nonlocal call_count
            call_count += 1
            return [{"attribute_path": f"field_{call_count}", "data_type": "string"}]
            
    monkeypatch.setattr("pyegeria.egeria_tech_client.EgeriaTech", MockEgeriaTech)
    
    import asyncio
    
    # 1. First fetch: should trigger actual client call
    res1 = asyncio.run(discover_draft_schema_internal("test_draft"))
    assert res1 == [{"attribute_path": "field_1", "data_type": "string"}]
    assert call_count == 1
    
    # 2. Second fetch: should hit the cache (call_count remains 1)
    res2 = asyncio.run(discover_draft_schema_internal("test_draft"))
    assert res2 == [{"attribute_path": "field_1", "data_type": "string"}]
    assert call_count == 1
    
    # 3. Modify draft parameter: should invalidate the cache and trigger a new fetch
    draft_store["test_draft"]["answers"]["Heading"] = "New Heading"
    res3 = asyncio.run(discover_draft_schema_internal("test_draft"))
    assert res3 == [{"attribute_path": "field_2", "data_type": "string"}]
    assert call_count == 2


def test_report_spec_agent_format_mapping(monkeypatch):
    agent = ReportSpecAgent()
    
    class MockDocManager:
        def load(self, doc_id):
            return """
# Custom Report
## Create Report Spec
### Target Type
Glossary
### Heading
Format Match Test
### Action Function
EgeriaTech.find_glossaries
### Spec Params
search_string=*
"""
            
    monkeypatch.setattr("advisor.agents.report_spec_agent.get_report_spec_doc_manager", lambda: MockDocManager())
    
    class MockPipeline:
        def _read_pyegeria_connection(self, egeria_credentials=None):
            return {
                "view_server": "dev_server",
                "platform_url": "http://localhost:9443",
                "user_id": "test_user",
                "user_pwd": "password"
            }
        def _format_output(self, data, fmt, heading, spec):
            return f"formatted_{fmt}"
            
    monkeypatch.setattr("advisor.agents.report_spec_agent.get_report_pipeline", lambda: MockPipeline())
    
    captured_format = None
    def mock_exec_report_spec(doc_id, output_format, params, **kwargs):
        nonlocal captured_format
        captured_format = output_format
        return {"kind": "json", "data": []}
        
    monkeypatch.setattr("pyegeria.exec_report_spec", mock_exec_report_spec)
    
    # 1. Test TABLE mapping to JSON
    res = agent.execute("test_format_mapping", output_format="TABLE")
    assert captured_format == "JSON"
    assert "formatted_TABLE" in res.get("response", "")
    
    # 2. Test REPORT passing as-is
    res = agent.execute("test_format_mapping", output_format="REPORT")
    assert captured_format == "REPORT"


