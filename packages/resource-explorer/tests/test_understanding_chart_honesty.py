"""Understanding is reachable for any resource type, but every one of
REPO_CHARTS' 8 kinds resolves via `/api/stats/{slug}/charts/{kind}`, which
404s through the repo-only `ProjectRegistry.exists` for a database/
filesystem slug -- every tile rendered a uniform "unavailable" with no way to
tell a permanent non-equivalence from a real wiring gap.

Source-level test (no JS runner in this suite), same technique
test_fact_answer_rendering.py/test_re_api_entity_type_threading.py use.
"""
from __future__ import annotations

from pathlib import Path

UNDERSTANDING = (
    Path(__file__).resolve().parents[1]
    / "resource_explorer" / "web" / "static" / "next" / "stages" / "understanding.js"
)


def _source() -> str:
    return UNDERSTANDING.read_text()


class TestNonRepoChartsAreHandledHonestly:
    def test_a_non_repo_entity_type_short_circuits_before_probing(self):
        src = _source()
        assert "entityType !== 'repo'" in src

    def test_the_repo_kinds_list_for_a_database_is_gone(self):
        """2026-10-01 (REPLY-DESIGNER-CURATE-AND-UNDERSTANDING-ALL-KINDS.md §4):
        a database sees its own charts, not the repo kinds with "does not apply"
        tiles. The behaviour is pinned by frontend-build/test-harness/
        understanding-databases.test.mjs; this only pins the removal."""
        src = _source()
        assert "does not apply to this resource type" not in src
        assert "CHART_NO_EQUIVALENT_REASON" not in src.replace(
            "CHART_NO_EQUIVALENT_REASON are gone", "")
        assert "nonRepoChartIndexHtml" not in src

    def test_repo_path_is_unchanged(self):
        """The repo branch must still probe every chart kind exactly as
        before -- this fix must not touch working behaviour for repos."""
        src = _source()
        assert "wanted.map(async ([kind, label])" in src
        assert ": REPO_CHARTS;" in src  # with no kinds named, every chart kind is probed
        assert "await getChart(slug, kind)" in src
