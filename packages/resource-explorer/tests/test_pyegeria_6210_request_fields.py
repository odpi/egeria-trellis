"""pyegeria 6.2.10 (#483, ISSUE-163/164/165): request fields 6.2.9 silently dropped are now SENT.

The behaviour risk of the bump is a caller that passed a field 6.2.9 dropped and now gets it applied. The audit
(2026-10-23) found no RE call site passing any of them. This pins, against the REAL 6.2.10 functions:
1. `_async_find_request` now sends governanceZoneFilter / relationshipsPageSize / anchorScopeGUID, and no RE
   source passes a zone filter, a relationship page size or an anchor scope to any call (so none newly applies;
   adding one is a reviewed decision, since a zone filter can HIDE elements RE expects to see).
2. `initiate_engine_action` now sends startDate (= now when no start_time is given) and actionSourceGUIDs; RE's
   `initiate_and_wait` passes no start_time and an empty source list, so its actions still start at once.
3. `anchorScopeGUIDs` now serialises on the create bodies; RE sets it nowhere.
4. The new undo/restore methods on MetadataExpert keep the signature RE will bind to (restore_metadata_element
   is to be wired later; nothing calls it yet).
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, create_autospec, patch

from pyegeria import AutomatedCuration, MetadataExpert
from pyegeria.core._server_client import ServerClient
from pyegeria.models.models import NewElementRequestBody

import resource_explorer
from resource_explorer.surveyors.egeria_delegated_step import initiate_and_wait

RE_ROOT = Path(resource_explorer.__file__).parent

#: Keyword arguments and raw body keys that 6.2.9 dropped and 6.2.10 sends.
NOW_HONOURED_KWARGS = {"governance_zone_filter", "zone_filter", "limit_results_by_zone",
                       "relationship_page_size", "relationships_page_size",
                       "anchor_scope_guid", "anchor_scope_guids"}
NOW_HONOURED_KEYS = {"governanceZoneFilter", "zoneFilter", "limitResultsByZone", "relationshipPageSize",
                     "relationshipsPageSize", "anchorScopeGUID", "anchorScopeGuid", "anchorScopeGUIDs",
                     "anchorScopeGuids"}


def _response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    return resp


def _sent_body(fake_self) -> dict:
    args = fake_self._async_make_request.call_args.args
    body = args[2]
    return json.loads(body) if isinstance(body, str) else body


def test_no_re_source_passes_a_field_6210_now_honours():
    hits = []
    for py in RE_ROOT.rglob("*.py"):
        for node in ast.walk(ast.parse(py.read_text())):
            if isinstance(node, ast.keyword) and node.arg in NOW_HONOURED_KWARGS:
                hits.append(f"{py.relative_to(RE_ROOT)}:{node.lineno} kwarg {node.arg}")
            if isinstance(node, ast.Dict):
                for k in node.keys:
                    if isinstance(k, ast.Constant) and k.value in NOW_HONOURED_KEYS:
                        hits.append(f"{py.relative_to(RE_ROOT)}:{node.lineno} key {k.value}")
            if (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store)
                    and isinstance(node.slice, ast.Constant) and node.slice.value in NOW_HONOURED_KEYS):
                hits.append(f"{py.relative_to(RE_ROOT)}:{node.lineno} key {node.slice.value}")
    assert not hits, f"RE now passes a field pyegeria 6.2.10 applies (6.2.9 dropped it); review its values: {hits}"


def test_find_request_now_sends_zone_filter_page_size_and_anchor_scope():
    fake = create_autospec(ServerClient, instance=True)
    fake._validate_body = ServerClient._validate_body  # the real (static) validator, so the real model serialises
    fake._async_make_request.return_value = _response({"elements": []})
    asyncio.run(ServerClient._async_find_request(
        fake, "http://x/find", "Asset", MagicMock(), search_string="abc",
        governance_zone_filter=["z1"], relationship_page_size=7, anchor_scope_guid="scope-1"))
    body = _sent_body(fake)
    assert body["governanceZoneFilter"] == ["z1"]
    assert body["relationshipsPageSize"] == 7
    assert body["anchorScopeGUID"] == "scope-1"
    assert "zoneFilter" not in body and "anchorScopeGuid" not in body


def test_find_request_default_sends_no_zone_filter_or_anchor_scope():
    fake = create_autospec(ServerClient, instance=True)
    fake._validate_body = ServerClient._validate_body  # the real (static) validator, so the real model serialises
    fake._async_make_request.return_value = _response({"elements": []})
    asyncio.run(ServerClient._async_find_request(fake, "http://x/find", "Asset", MagicMock(), search_string="abc"))
    body = _sent_body(fake)
    assert "governanceZoneFilter" not in body and "anchorScopeGUID" not in body
    assert body.get("relationshipsPageSize", 0) == 0


def test_new_element_body_serialises_anchor_scope_guids():
    dumped = NewElementRequestBody.model_validate(
        {"class": "NewElementRequestBody", "anchorScopeGUIDs": ["s"]}).model_dump(by_alias=True, exclude_none=True)
    assert dumped["anchorScopeGUIDs"] == ["s"]


def test_real_initiate_engine_action_sends_start_date_now_when_unset():
    fake = create_autospec(AutomatedCuration, instance=True)
    fake.ref_curation_command_base = "http://x"
    fake._async_make_request.return_value = _response({"guid": "ea-1"})
    before = int(time.time() * 1000)
    guid = asyncio.run(AutomatedCuration._async_initiate_engine_action(
        fake, governance_engine_name="AssetSurvey", qualified_name="qn", domain_identifier=0,
        display_name="d", description="desc", request_source_guids=[], action_targets=[],
        received_guards=[], request_type="rt", request_parameters={}))
    after = int(time.time() * 1000)
    assert guid == "ea-1"
    body = _sent_body(fake)
    assert body["class"] == "InitiateEngineActionRequestBody"
    assert "startTime" not in body and "requestSourceGUIDs" not in body
    assert before - 1000 <= body["startDate"] <= after + 1000  # "now": the action still starts at once


def test_re_initiate_and_wait_passes_no_start_time_and_no_sources():
    curation = create_autospec(AutomatedCuration, instance=True, spec_set=True)
    curation.initiate_engine_action.return_value = "ea-1"
    expert = create_autospec(MetadataExpert, instance=True, spec_set=True)
    expert.get_metadata_element_by_guid.return_value = {
        "properties": {"actionStatus": "COMPLETED", "completionMessage": "ok"},
        "elementHeader": {"guid": "ea-1"}}
    with patch("resource_explorer.surveyors.egeria_delegated_step._get_clients", return_value=(curation, expert)), \
            patch("resource_explorer.surveyors.egeria_delegated_step._poll_action_status",
                  return_value=("COMPLETED", "ok")):
        initiate_and_wait(governance_engine_name="AssetSurvey", qualified_name="qn", display_name="d",
                          description="desc", request_type="rt")
    kwargs = curation.initiate_engine_action.call_args.kwargs
    assert "start_time" not in kwargs
    assert kwargs["request_source_guids"] == []


UNDO_RESTORE = {
    "undo_metadata_element_update": ("metadata_element_guid", "/metadata-elements/G/undo"),
    "restore_metadata_element": ("metadata_element_guid", "/metadata-elements/G/restore"),
    "undo_related_elements_update": ("relationship_guid", "/related-elements/G/undo"),
    "restore_related_elements": ("relationship_guid", "/related-elements/G/restore"),
}


def test_undo_restore_signatures():
    for name, (guid_param, _) in UNDO_RESTORE.items():
        for fn in (getattr(MetadataExpert, name), getattr(MetadataExpert, f"_async_{name}")):
            params = list(inspect.signature(fn).parameters.values())
            assert [p.name for p in params] == ["self", guid_param, "body"], name
            assert params[2].default is None, name
    expert = create_autospec(MetadataExpert, instance=True, spec_set=True)
    expert.restore_metadata_element("G")
    expert.restore_metadata_element("G", body={"class": "MetadataSourceRequestBody"})
    try:
        expert.restore_metadata_element("G", cascade=True)
    except TypeError:
        pass
    else:
        raise AssertionError("restore_metadata_element accepted an unknown keyword")


def test_real_undo_restore_post_to_their_urls():
    for name, (_, suffix) in UNDO_RESTORE.items():
        fake = create_autospec(MetadataExpert, instance=True)
        fake.command_root = "http://x"
        asyncio.run(getattr(MetadataExpert, f"_async_{name}")(fake, "G"))
        url = fake._async_metadata_source_body_request.call_args.args[0]
        assert url == f"http://x{suffix}", name
