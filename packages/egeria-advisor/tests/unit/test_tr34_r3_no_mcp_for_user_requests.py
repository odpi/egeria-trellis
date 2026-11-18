"""TR-34 R3: a user's request never reaches an MCP child.

After TR-34 the pyegeria and Dr.Egeria MCP children sign in as EA's own service account. They can't take a
user's token per call yet (TR-43). EA is multi-user, so a tool call made for a user would run as the service
account: the TR-33 shape. So:
- for any user request (signed in or anonymous; the web middleware sets request_context, and F1 carries it into
  the query threads), MCPAgent refuses every tool call before its cache;
- the conversation agent makes no MCP call and says "That tool can't run as you yet, so I didn't use it.";
- explicit service work (`with service_mcp():`) and background work (no request context) may still use them.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from advisor import mcp_agent as ma
from advisor.request_context import using_user


class _NoCalls:
    """Fails the test on ANY use: tool listing, a call, a retry, a cache read."""

    _initialized = True

    def __getattr__(self, name):
        pytest.fail(f"the MCP agent was used for a user request: .{name}")


def _agent():
    from advisor.agents.conversation_agent import ConversationAgent
    a = ConversationAgent.__new__(ConversationAgent)
    a.llm = SimpleNamespace(generate=lambda prompt, **kw: "an answer from the codebase")
    a.rag = SimpleNamespace(retrieve=lambda **kw: [])
    a.analytics = SimpleNamespace(answer_quantitative_query=lambda q: "")
    a.rag_top_k, a.max_history, a.conversation_history = 10, 10, []
    a.enable_mlflow, a.enable_mcp, a.mcp_agent = False, True, _NoCalls()
    return a


@pytest.fixture
def no_other_routes(monkeypatch):
    """The agent's earlier routes (the PyEgeria and CLI agents) don't take these questions."""
    monkeypatch.setattr("advisor.agents.pyegeria_agent.reset_pyegeria_agent", lambda: None)
    monkeypatch.setattr("advisor.agents.conversation_agent.get_pyegeria_agent",
                        lambda: SimpleNamespace(is_pyegeria_query=lambda q: False))
    monkeypatch.setattr("advisor.cli_integration.get_cli_router",
                        lambda: SimpleNamespace(should_use_cli_agent=lambda q: False))


@pytest.mark.parametrize("who", ["erinoverview", None])        # signed in, and an anonymous request
@pytest.mark.parametrize("question", ["run report on glossary terms", "execute command list glossaries"])
def test_a_users_chat_that_would_pick_an_mcp_tool_makes_no_call_and_says_why(no_other_routes, who, question):
    agent = _agent()
    with using_user(who):
        out = agent._process_query_internal(question, use_rag=False, start_time=0.0)
    assert out["content"].startswith(ma.MCP_NOT_AS_YOU)
    assert out["notes"] == [ma.MCP_NOT_AS_YOU]


def test_a_question_no_tool_fits_says_nothing_about_tools(no_other_routes):
    agent = _agent()
    with using_user("erinoverview"):
        out = agent._process_query_internal("what is a glossary?", use_rag=False, start_time=0.0)
    assert ma.MCP_NOT_AS_YOU not in out["content"] and "notes" not in out


def test_the_agents_own_tool_path_returns_nothing_for_a_user_request():
    """A direct call, or a retry, of the agent's tool step can't reach the children either."""
    from advisor.agents.conversation_agent import QueryType
    agent = _agent()
    with using_user("erinoverview"):
        assert agent._invoke_mcp_tools("run report on x", QueryType.REPORT) is None
        assert agent._invoke_mcp_tools("execute command x", QueryType.COMMAND) is None


# ── every MCPAgent entry point refuses for a user request ────────────────────

def _bare_mcp_agent():
    a = ma.MCPAgent.__new__(ma.MCPAgent)
    a._initialized = True
    a.tools = {"list_reports": object()}
    a.cache = SimpleNamespace(get=lambda *k, **kw: pytest.fail("the cache was read for a user request"))
    a.config = SimpleNamespace(max_concurrent_tools=2)
    return a


@pytest.mark.parametrize("who", ["erinoverview", None])
def test_execute_tool_refuses_before_the_cache_for_a_user_request(who):
    agent = _bare_mcp_agent()
    with using_user(who):
        with pytest.raises(ma.MCPRunsAsServiceAccount, match="can't run as you yet"):
            asyncio.run(agent.execute_tool("list_reports", {}))


def test_execute_tools_parallel_refuses_for_a_user_request():
    agent = _bare_mcp_agent()
    with using_user("erinoverview"):
        with pytest.raises(ma.MCPRunsAsServiceAccount):
            asyncio.run(agent.execute_tools_parallel([{"name": "list_reports", "arguments": {}}]))


def test_background_work_is_not_a_user_request():
    assert ma.user_request_in_flight() is False             # no request context: background or startup


def test_explicit_service_work_may_use_them_inside_a_request():
    with using_user("erinoverview"):
        assert ma.user_request_in_flight() is True
        with ma.service_mcp():
            assert ma.user_request_in_flight() is False
        assert ma.user_request_in_flight() is True


def test_the_report_pipelines_mcp_calls_are_refused_for_a_user_request(monkeypatch):
    """report_pipeline reaches the children through _call_tool (spec search, listings): refused the same way."""
    from advisor.report_pipeline import get_report_pipeline
    pipeline = get_report_pipeline()
    monkeypatch.setattr(pipeline, "_ensure_agent", lambda: None)
    monkeypatch.setattr(pipeline, "_agent", _bare_mcp_agent(), raising=False)
    with using_user("erinoverview"):
        with pytest.raises(ma.MCPRunsAsServiceAccount):
            pipeline._call_tool("list_reports", {})
