"""Brief A follow-up 1: Classic's single-component Accept was the last Accept that wrote to Egeria.

The route is now a decision only (tested in test_architecture_publish.py, TestAcceptWritesNothing). Classic must say
so on the row and offer the same Publish the Next pane has: the list from the SAME plan the run uses, one press, the
results from the proof rows. Source-text checks, the established pattern for Classic's one large file."""
from __future__ import annotations

from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "resource_explorer/web/static/index.html").read_text()


def _fn(head: str) -> str:
    i = HTML.index(head)
    return HTML[i:HTML.index("\n}\n", i)]


def test_the_component_accept_no_longer_reads_or_reports_a_materialization():
    h = _fn("async function _archSubmitVerdict(")
    assert "materialization" not in h
    assert "created as a real SolutionComponent" not in h
    assert "not in Egeria yet" in h and "Publish" in h
    assert "_classicArchPublishLoad(slug)" in h, "the Publish list is re-read after a verdict"


def test_the_blueprint_accept_no_longer_reports_a_materialization():
    h = _fn("async function _archSubmitBlueprintVerdict(")
    assert "materialization" not in h and "SolutionBlueprint in Egeria" not in h
    assert "_classicArchPublishLoad(slug)" in h


def test_the_row_says_what_the_decision_means_for_egeria_from_the_cache_row():
    h = _fn("function _archVerdictBadgeHtml(")
    assert 'data-arch-egeria="no"' in h and "not in Egeria yet" in h
    assert 'data-arch-egeria="still"' in h and "still in Egeria" in h
    assert "_classicArchPublishJump()" in h, "the row points to Publish"


def test_classic_offers_the_same_publish_from_the_same_plan():
    assert 'id="classic-arch-publish"' in _fn("function renderCurateArchitecturePanel(")
    load = _fn("async function _classicArchPublishLoad(")
    assert "/architecture/publish-plan" in load
    press = _fn("async function _classicArchPublishPress(")
    assert "/architecture/publish`, { method: 'POST' }" in press
    assert "_pollActivityUntilDone(out.activity_id" in press
    assert "res.status === 409" in press and "res.status === 401" in press
    html = _fn("function _classicArchPublishHtml(")
    assert "plan.label" in html, "the control names its object and count from the plan"
    assert "data-classic-will-write" in html and "plan.last" in html
    assert "needs_identifier" in html, "a held-back blueprint is said, not hidden"


def test_the_missing_members_shortcut_accepts_rather_than_claiming_to_publish():
    h = _fn("async function _curatePublishMissingComponents(")
    assert "Published" not in h and "could not be published" not in h
    assert "materialization" not in h


def test_brief_z_classic_says_not_permitted_with_the_reason():
    """Brief Z (2026-10-10): a refused verdict or Publish item reads "not permitted · <reason>"; a refused Publish
    row has its own word rather than falling back to the raw status."""
    publish = _fn("function _classicArchPublishHtml(")
    assert "not_permitted: ['text-amber-400', '⊘ not permitted']" in publish
    assert "replace(/^not permitted · /, '')" in publish, "the reason is said once after the word"
    assert "'⊘ not permitted'" in _fn("async function _classicArchPublishPress(")
    for head in ("async function _archSubmitVerdict(", "async function _archSubmitBlueprintVerdict("):
        assert "resp.status === 403 ? `not permitted · ${detail}`" in _fn(head), head
