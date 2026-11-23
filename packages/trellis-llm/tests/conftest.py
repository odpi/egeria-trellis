"""Replay recorded HTTP through httpx.MockTransport: the official clients run for real, nothing touches the network."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def fixture_json(name: str) -> dict:
    return json.loads(fixture_bytes(name))


class Replay:
    """An httpx client whose every request is answered by `respond(request) -> httpx.Response`; requests are kept."""

    def __init__(self, respond):
        self.requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return respond(request)

        self.transport = httpx.MockTransport(handler)
        self.client = httpx.Client(transport=self.transport)

    @property
    def body(self) -> dict:
        return json.loads(self.requests[-1].content)


def json_reply(name_or_obj, status: int = 200):
    data = fixture_bytes(name_or_obj) if isinstance(name_or_obj, str) else json.dumps(name_or_obj).encode()
    return lambda request: httpx.Response(status, content=data, headers={"content-type": "application/json"})


def sse_reply(name: str, content_type: str = "text/event-stream"):
    return lambda request: httpx.Response(200, content=fixture_bytes(name), headers={"content-type": content_type})


@pytest.fixture(autouse=True)
def _fresh_ignored_log():
    """Each test sees the 'logged once per (provider, param)' set empty."""
    from trellis_llm import params
    params._LOGGED.clear()
    yield
    params._LOGGED.clear()
