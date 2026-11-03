"""pyegeria 6.2.12 + 6.2.13: what changed under RE, and what the 2026-10-27 audit found.

6.2.12 (#485, ISSUE-165; #487): methods whose Egeria endpoints were removed now delegate elsewhere (SurveyReport
assets for analysis reports, collection membership for subject areas, a best-effort data-classification mapping),
and get_platform_organization reads /api/public/app/info. RE calls none of them.

6.2.13 (#498, ISSUE-166): unset numbers and flags are no longer sent as 0/false, and create_egeria_bearer_token
RAISES on a failed login instead of returning "FAILED" (or, on a 401, the error text as if it were a token).

This pins:
1. No RE source or script calls a rerouted method by name (adding one is a reviewed decision).
2. The installed pyegeria is at least 6.2.13, so a downgrade cannot slip back in under the lock.
3. A refused login now raises from create_egeria_bearer_token and installs no "Bearer <401 text>" header. RE's
   EgeriaClients._mint/refresh call it unguarded, so a refused daemon login now propagates to the caller of
   `.of()` instead of leaving the clients with no token (6.2.12) or the 401 body as a token.
4. RE's annotation bodies reach the wire unchanged: pyegeria's new annotation models drop nothing RE sends.
"""
from __future__ import annotations

import ast
import asyncio
import json
from importlib.metadata import version
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pyegeria import DataDiscovery
from pyegeria.core._base_server_client import BaseServerClient
from pyegeria.core._exceptions import PyegeriaUnauthorizedException
from pyegeria.core._server_client import ServerClient

import resource_explorer
from resource_explorer.surveyors import survey_report as sr
from resource_explorer.surveyors.annotation_props import build_annotation_props

RE_ROOT = Path(resource_explorer.__file__).parent
#: Never reachable (RFC 2606), so no test here can pass only because a real Egeria answers. CI has none; a laptop
#: often does, which is how the first version passed every local gate and failed CI on its constructor probe.
PLATFORM = "https://egeria.invalid:9443"
SCRIPTS = RE_ROOT.parent / "scripts"

#: Rerouted by #485 (deprecated wrappers) or changed by #487. Calling any of these from RE needs a review first.
REROUTED = {
    "create_analysis_report", "update_analysis_report", "delete_analysis_report", "get_analysis_reports_by_name",
    "find_analysis_reports", "get_analysis_report_by_guid", "link_analysis_report_to_asset",
    "detach_analysis_report_from_asset", "add_element_to_subject_area", "remove_element_from_subject_area",
    "find_governance_action_processes", "get_gov_def_in_context", "create_data_classification",
    "update_data_classification", "link_data_classification_to_data_field",
    "detach_data_classification_from_data_field", "get_platform_organization",
}


@pytest.fixture(autouse=True)
def _no_platform_probe(monkeypatch):
    """pyegeria's client constructor GETs /api/about and raises when nothing answers; stub that probe only."""
    monkeypatch.setattr(BaseServerClient, "check_connection", lambda self: "")


def _called_names(root: Path):
    for py in root.rglob("*.py"):
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
                if name:
                    yield py, node.lineno, name.removeprefix("_async_")


def test_no_re_source_or_script_calls_a_rerouted_method():
    hits = [f"{py.relative_to(RE_ROOT.parent)}:{line} {name}"
            for root in (RE_ROOT, SCRIPTS) if root.exists()
            for py, line, name in _called_names(root) if name in REROUTED]
    assert hits == [], "review the pyegeria replacement before calling: " + ", ".join(hits)


def test_the_installed_pyegeria_is_at_least_6_2_13():
    parts = tuple(int(p) for p in version("pyegeria").split(".")[:3])
    assert parts >= (6, 2, 13)


def test_a_refused_token_request_raises_instead_of_returning_the_error_text():
    client = DataDiscovery("qs-view-server", PLATFORM, "u", "wrong")
    refused = MagicMock(status_code=401, text="Unauthorized: bad password")

    class _Post:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *args, **kwargs):
            return refused

    with patch("pyegeria.core._base_platform_client.AsyncClient", return_value=_Post()):
        with pytest.raises(PyegeriaUnauthorizedException):
            client.create_egeria_bearer_token("u", "wrong")
    assert "Bearer Unauthorized" not in client.headers.get("Authorization", "")


@pytest.mark.parametrize("ann", [
    sr.ResourceMeasureAnnotation(summary="s", analysis_step="a", resource_properties={"rows": 10},
                                 additional_properties={"resultState": "measured"}),
    sr.QualityCheckAnnotation(summary="s", analysis_step="a", quality_dimension="Completeness",
                              quality_description="d", quality_score=100, sample_size=50),
    sr.ClassificationAnnotation(summary="s", analysis_step="a", candidate_classifications=["Confidentiality"],
                                classification_name="Confidentiality", classification_properties={"level": 2}),
], ids=lambda a: type(a).__name__)
def test_an_re_annotation_body_reaches_the_wire_unchanged(ann):
    props = build_annotation_props(ann, "qn::wire")
    body = {"class": "NewElementRequestBody", "anchorGUID": "a", "isOwnAnchor": False, "parentGUID": "r",
            "parentRelationshipTypeName": "ReportedAnnotation", "parentAtEnd1": True, "properties": props}
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"class": "GUIDResponse", "relatedHTTPCode": 200, "guid": "g-1"}
    with patch.object(ServerClient, "_async_make_request", return_value=resp) as mk:
        DataDiscovery("qs-view-server", PLATFORM, "u", "p").create_annotation(body=body)
    sent = mk.call_args.args[2]
    sent = json.loads(sent) if isinstance(sent, str) else sent
    assert sent["properties"] == props
