"""pyegeria 6.2.16 (egeria-python #513): no nest_asyncio, sync methods through run_sync.

The publisher used to resolve a sub-resource's template with
`asyncio.get_event_loop().run_until_complete(ac._async_get_template_guid_for_technology_type(...))`.
Without nest_asyncio, a real `asyncio.run` clears the thread's loop on exit, so after ANY earlier
`asyncio.run` on the thread `get_event_loop()` raised and `publish_sub_resources` logged "Could not
catalog sub-resource" and created nothing. b2's reproduction, 2026-10-30.

The client here is a REAL `AutomatedCuration` (built without a connection); only the coroutine under
the sync method is faked, so pyegeria's own `run_sync` is what is exercised.
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock

from tests.qn_lookup_bridge import expert_from_legacy

from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

GITHUB_URL = "https://github.com/example/myproj"


def _row(locator: str, kind: str) -> dict:
    return {"locator": locator, "kind": kind, "status": "cataloged", "detail": None,
            "egeria_guid": None, "source_finding": "test", "cataloged_at": "2026-10-30T00:00:00Z"}


def _publisher():
    from pyegeria import AutomatedCuration

    registry = MagicMock()
    registry.list_sub_resources.return_value = [_row("docs", "folder")]
    pub = EgeriaPublisher(platform_url="https://fake", registry=registry)
    ac = AutomatedCuration.__new__(AutomatedCuration)
    asked = []

    async def _template(type_name, **_k):
        asked.append(type_name)
        return f"template-{type_name}"

    ac._async_get_template_guid_for_technology_type = _template
    ac.create_elem_from_template = MagicMock(return_value="guid-docs")
    ac.get_guid_for_name = MagicMock(return_value="No elements found")
    pub._automated_curation = ac
    pub._metadata_expert = expert_from_legacy(ac)
    pub._asset_maker = MagicMock()
    pub._discovery = MagicMock()
    pub._connect = MagicMock()
    return pub, ac, asked


def _publish(pub):
    return pub.publish_sub_resources("myproj", GITHUB_URL, "asset-guid", ["docs"])


def test_after_an_asyncio_run_on_the_thread_a_sub_resource_publish_still_finds_its_template():
    asyncio.run(asyncio.sleep(0))                  # leaves this thread with no current loop
    pub, ac, asked = _publisher()
    assert _publish(pub) == {"docs": "guid-docs"}
    assert asked and ac.create_elem_from_template.call_count == 1
    assert ac.create_elem_from_template.call_args.args[0]["templateGUID"].startswith("template-")


def test_a_worker_thread_with_no_loop_publishes_too():
    pub, ac, _ = _publisher()
    out = {}
    t = threading.Thread(target=lambda: out.update(guids=_publish(pub)))
    t.start(); t.join()
    assert out["guids"] == {"docs": "guid-docs"} and ac.create_elem_from_template.call_count == 1


def test_a_publish_called_from_inside_a_running_loop_publishes_too():
    pub, ac, _ = _publisher()

    async def inside():
        return _publish(pub)                       # sync code on a loop thread: run_sync hands it off

    assert asyncio.run(inside()) == {"docs": "guid-docs"}
    assert ac.create_elem_from_template.call_count == 1
