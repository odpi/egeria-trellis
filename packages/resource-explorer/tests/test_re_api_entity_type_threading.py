"""Source-level regression tests for re-api.js's entity-kind contract.

No JS test runner is wired into this suite, so these pin the contract at the
source level (same technique as test_fact_answer_rendering.py).

The contract (2026-09-30 entity-type sweep): the resource kind is NEVER
defaulted. A silent `entityType = 'repo'` once resolved database/filesystem
slugs as repo Projects ("Project 'X' not found") three separate times, and
saveEnrichmentField's default was a real wrong-bucket WRITE. So:

  * no helper parameter named entityType/resourceType carries a default
    (listSubscriptions' `entityType = ''` is a *filter*, not a kind -- it is
    the one explicit exception);
  * every kind-taking helper calls `requireKind('<helperName>', <param>)`
    (param `resourceType` for assignGroup and listQuestionCatalog) so an
    omitted kind throws instead of silently becoming 'repo';
  * the kind is still threaded into the request path / query / body;
  * getResourceFacts was dead code and is intentionally deleted.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

RE_API = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "re-api.js"

# Helpers whose kind parameter is `resourceType`, not `entityType`.
RESOURCE_TYPE_HELPERS = {"assignGroup", "listQuestionCatalog"}

# Verified against re-api.js at the time of the sweep.
# 32: G1 added egeriaReportsPath (the Egeria reports component's per-kind route base; it requires its kind).
EXPECTED_HELPER_COUNT = 36   # +registerWithEgeria, +checkNativeSurveyPointers (catalog-and-survey), +compileContext (parity P3), +cancelNativeAction (Egeria action cancel)

_DEF = r"^export (?:async )?(?:const|function\*?) {name}\b"


def _source() -> str:
    return RE_API.read_text()


def _helper_names(js: str) -> list[str]:
    return sorted(set(re.findall(r"requireKind\('([A-Za-z0-9_]+)'", js)))


def _def(js: str, name: str) -> str:
    """Source of `export const|function NAME` up to the next top-level export."""
    m = re.search(_DEF.format(name=re.escape(name)), js, re.M)
    assert m, f"{name} is not exported from re-api.js"
    nxt = re.search(r"^export ", js[m.end():], re.M)
    end = m.end() + nxt.start() if nxt else len(js)
    return js[m.start():end]


def _code(fn: str) -> str:
    """The definition with /* */ and // comments stripped (docs mention the old default)."""
    fn = re.sub(r"/\*.*?\*/", "", fn, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", fn)


def _doc_above(js: str, name: str) -> str:
    """The text directly above `export ... NAME` (its doc comment)."""
    start = re.search(_DEF.format(name=re.escape(name)), js, re.M).start()
    return js[max(0, start - 1500):start]


HELPERS = _helper_names(_source())


def test_helper_count_is_pinned():
    assert len(HELPERS) == EXPECTED_HELPER_COUNT, (
        f"{len(HELPERS)} helpers call requireKind, expected {EXPECTED_HELPER_COUNT}: {HELPERS}. "
        "If a helper was legitimately added/removed, update EXPECTED_HELPER_COUNT."
    )


def test_no_kind_parameter_has_a_default_anywhere():
    js = _source()
    offenders = re.findall(r"\b(?:entityType|resourceType)\s*=\s*['\"`][^,)}\n]*['\"`]", js)
    # listSubscriptions' `entityType = ''` is a filter ("any kind"), not a kind.
    offenders = [o for o in offenders if re.sub(r"\s", "", o) != "entityType=''"]
    assert not offenders, f"silent kind default reintroduced: {offenders}"


def test_get_resource_facts_stays_deleted():
    assert "getResourceFacts" not in _source(), (
        "getResourceFacts was dead code (zero callers) removed by the sweep"
    )


@pytest.mark.parametrize("name", HELPERS)
def test_helper_requires_its_kind_and_has_no_default(name):
    fn = _code(_def(_source(), name))
    param = "resourceType" if name in RESOURCE_TYPE_HELPERS else "entityType"
    assert re.search(rf"requireKind\('{name}', {param}\b", fn), (
        f"{name} must call requireKind('{name}', {param}...) before using the kind"
    )
    # The kind must be a bare parameter (destructured or positional), no default.
    assert not re.search(rf"\b{param}\s*=[^=>]", fn), f"{name} gives {param} a default"
    assert re.search(rf"\b{param}\b", fn.split("requireKind", 1)[0]), (
        f"{name} must declare {param} as a parameter"
    )


class TestKindIsThreadedIntoRequests:
    def test_save_enrichment_field_builds_path_from_kind(self):
        fn = _def(_source(), "saveEnrichmentField")
        assert "/api/context/repo/" not in fn
        assert "/api/context/${encodeURIComponent(requireKind('saveEnrichmentField', entityType))}/" in fn

    def test_get_analyses_index_sends_entity_type_query_param(self):
        fn = _def(_source(), "getAnalysesIndex")
        assert "entity_type=${encodeURIComponent(requireKind('getAnalysesIndex', entityType))}" in fn

    @pytest.mark.parametrize("name", ["getBulkFacts", "getBulkStates"])
    def test_bulk_helpers_send_entity_type(self, name):
        fn = _def(_source(), name)
        assert f"entity_type: requireKind('{name}', entityType)" in fn

    def test_run_analysis_dispatches_by_validated_kind(self):
        js = _source()
        fn = _def(js, "runAnalysis")
        assert "_runAnalysisPath(requireKind('runAnalysis', entityType)" in fn
        path_fn = js[js.index("function _runAnalysisPath("):]
        path_fn = path_fn[: path_fn.index("\n}\n")]
        assert "/api/databases/" in path_fn
        assert "entityType === 'database'" in path_fn

    @pytest.mark.parametrize("name", ["getJournal", "writeJournal"])
    def test_journal_path_uses_kind(self, name):
        fn = _def(_source(), name)
        assert f"/api/journal/${{encodeURIComponent(requireKind('{name}', entityType))}}/" in fn

    def test_assign_group_sends_resource_type(self):
        fn = _def(_source(), "assignGroup")
        assert "resource_type: requireKind('assignGroup', resourceType, 'resourceType')" in fn


class TestFoundNotFixedGapsAreDocumented:
    """getAnalysisTrend/getMembers must keep saying in-source that their
    backend route gap was found and not fixed."""

    def test_get_analysis_trend_names_the_gap(self):
        assert "FOUND, NOT FIXED" in _doc_above(_source(), "getAnalysisTrend")

    def test_get_members_names_the_gap(self):
        assert "found, not fixed" in _doc_above(_source(), "getMembers").lower()
