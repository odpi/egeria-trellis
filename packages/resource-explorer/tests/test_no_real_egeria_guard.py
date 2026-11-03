"""Brief I round 2: the autouse `no_real_egeria` guard. A real pyegeria client built by the
factory in a test cannot send anything; a fake passes through; a live-marked test is left alone."""
from __future__ import annotations

import pytest

from tests.conftest import RealEgeriaBlocked


def test_a_real_client_is_refused_before_it_is_built(signed_in_caller, monkeypatch):
    import httpx
    from pyegeria import AutomatedCuration

    from resource_explorer.egeria_clients import Caller, Daemon, DaemonReason, egeria_client

    sent = []
    real_send = httpx.AsyncClient.send

    async def spy(self, request, *a, **k):
        sent.append(str(request.url))
        return await real_send(self, request, *a, **k)
    monkeypatch.setattr(httpx.AsyncClient, "send", spy)

    with pytest.raises(RealEgeriaBlocked):
        egeria_client(Daemon(DaemonReason.SCHEDULER), purpose="t").of(AutomatedCuration)
    with pytest.raises(RealEgeriaBlocked):
        egeria_client(Caller(), purpose="t").of(AutomatedCuration)
    assert sent == [], "nothing reached the network"


def test_a_fake_client_passes_untouched(signed_in_caller):
    from resource_explorer.egeria_clients import Caller, egeria_client

    class Fake:
        def __init__(self, *a):
            self.token = None

        def set_bearer_token(self, t):
            self.token = t

        async def _async_make_request(self, *a, **k):
            return "fake answer"

    client = egeria_client(Caller(), purpose="t").of(Fake)
    assert client.token == "tok-test-caller"
    import asyncio
    assert asyncio.run(client._async_make_request()) == "fake answer"


def test_a_subclass_of_a_real_client_defined_outside_pyegeria_is_refused_too(signed_in_caller):
    """By class, not by `__module__` text (round-3 review): a test-side subclass of a real client
    still inherits its HTTP layer."""
    from pyegeria import AssetMaker

    from resource_explorer.egeria_clients import Caller, egeria_client

    class Sneaky(AssetMaker):
        pass

    with pytest.raises(RealEgeriaBlocked):
        egeria_client(Caller(), purpose="t").of(Sneaky)
