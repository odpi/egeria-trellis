"""ConversationAgent's MCP calls must work from inside a running loop (pyegeria 6.2.16 no longer applies
nest_asyncio, so a bare asyncio.run there raises) and from plain sync code."""

import asyncio

from advisor.agents.conversation_agent import _run_coroutine


async def _answer():
    await asyncio.sleep(0)
    return 42


def test_from_sync_code():
    assert _run_coroutine(_answer) == 42


def test_from_inside_a_running_loop():
    async def caller():
        return _run_coroutine(_answer)

    assert asyncio.run(caller()) == 42
