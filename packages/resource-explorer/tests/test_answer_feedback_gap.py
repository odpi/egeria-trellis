"""POST /api/feedback/answer — per-answer feedback, and the gap a
disagreement raises.

PLAN-FINISH-REPOS.md item 8's done test: "per-answer feedback exists, and a
disagreement lands in the gaps collection as destination `ours`." The gap
side is unit-tested in test_gaps.py; this file is about the route — what it
accepts, what it refuses, and that agreeing does NOT manufacture a gap (the
failure that would make the gaps count meaningless).
"""
from __future__ import annotations

import re as _re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from resource_explorer.config import FeedbackConfig
from resource_explorer.destinations import OURS
from resource_explorer.feedback_store import FeedbackStore
from resource_explorer.gaps import gap_is_about_question, gaps_summary
from resource_explorer.registry import Project


@pytest.fixture
def slug(request):
    return "ansfb_" + _re.sub(r"[^a-z0-9]+", "_", request.node.name.lower())[:40]


@pytest.fixture
def reg(pg_registry, slug):
    pg_registry.add(Project(slug=slug, display_name=slug,
                            github_url=f"https://github.com/x/{slug}"))
    return pg_registry


@pytest.fixture
def client(tmp_path, reg, monkeypatch):
    store = FeedbackStore(db_path=str(tmp_path / "answer_feedback.db"))
    monkeypatch.setattr(
        "resource_explorer.feedback_store.FeedbackStore.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", store.__dict__) or None,
    )
    monkeypatch.setattr(
        "resource_explorer.web.routes.feedback.get_config",
        lambda: SimpleNamespace(feedback=FeedbackConfig(admin_token="t")),
    )
    # The route builds its own ProjectRegistry(); point every instance at the
    # throwaway test schema the `reg` fixture is already using.
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, *a, **k: setattr(self, "__dict__", reg.__dict__) or None,
    )
    from resource_explorer.web.app import app
    return TestClient(app), store


def _a_question_with_an_analysis() -> tuple[str, str]:
    q, analysis_id, _qid = _a_question_with_its_id()
    return q, analysis_id


def _a_question_with_its_id() -> tuple[str, str, str]:
    """A real catalogued question that names at least one analysis, read from
    the catalog rather than hard-coded — a fixed string here would start
    failing the day someone rewords a row, which is not what this asserts."""
    from resource_explorer.surveyors.question_catalog_reader import get_questions

    for e in get_questions("repo"):
        ids = (e.get("answering") or {}).get("analysis_ids") or []
        if ids:
            return e["question"], ids[0], e["id"]
    pytest.skip("no catalogued question names an analysis")


class TestADisagreementBecomesAGap:
    def test_it_lands_in_the_gaps_collection_as_ours(self, client, reg, slug):
        api, _ = client
        question, analysis_id, qid = _a_question_with_its_id()

        res = api.post("/api/feedback/answer", json={
            "slug": slug, "question": question, "verdict": "disagree",
            "comment": "That contradicts the release history.",
        })
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["gap"]["destination"] == OURS
        assert body["gap"]["analysis_id"] == analysis_id

        # Found the way any reader finds it: by the question's stable id, or its text for a legacy row.
        rows = [r for r in gaps_summary(reg, slug)["gaps"] if gap_is_about_question(r, question, qid)]
        assert len(rows) == 1
        # keyed by the stable id (question-id slice), so a rewording cannot orphan it
        assert rows[0]["check_name"] == f"question:{qid}"
        assert rows[0]["destination"] == OURS
        assert rows[0]["source"] == "person"

    def test_agreeing_raises_no_gap(self, client, reg, slug):
        """The count has to mean "answers someone disputes". If agreement
        also produced a row, the number would only measure engagement."""
        api, _ = client
        question, _analysis = _a_question_with_an_analysis()

        body = api.post("/api/feedback/answer", json={
            "slug": slug, "question": question, "verdict": "agree",
        }).json()
        assert body["gap"] is None
        assert "no gap is owed" in body["gap_reason"]
        assert gaps_summary(reg, slug)["counts"]["disputed_by_a_person"] == 0

    def test_every_verdict_is_kept_even_when_it_raises_nothing(self, client, slug):
        """Agreement is signal with nowhere else to live — "nobody has
        questioned this" and "someone confirmed it" must not look alike."""
        api, store = client
        question, _analysis, qid = _a_question_with_its_id()

        api.post("/api/feedback/answer", json={
            "slug": slug, "question": question, "verdict": "partly",
            "comment": "Right about the licence, wrong about the date.",
        })
        recorded = store.list(limit=10)
        assert len(recorded) == 1
        # keyed by the question's stable id, not its wording
        assert recorded[0]["element_guid"] == f"answer:{slug}:{qid}"
        assert "[partly]" in recorded[0]["message"]


class TestWhatItRefuses:
    def test_an_unknown_verdict_is_rejected(self, client, slug):
        api, _ = client
        question, _a = _a_question_with_an_analysis()
        res = api.post("/api/feedback/answer", json={
            "slug": slug, "question": question, "verdict": "meh"})
        assert res.status_code == 400
        assert "verdict" in res.json()["detail"]

    def test_an_unknown_project_is_rejected(self, client):
        api, _ = client
        question, _a = _a_question_with_an_analysis()
        res = api.post("/api/feedback/answer", json={
            "slug": "no-such-project-here", "question": question, "verdict": "disagree"})
        assert res.status_code == 404

    def test_an_analysis_that_does_not_answer_the_question_is_rejected(self, client, slug):
        """A stale page must not be able to charge a dispute to an analysis
        that never answered that question — that would put work in the wrong
        queue and be invisible afterwards."""
        api, _ = client
        question, _a = _a_question_with_an_analysis()
        res = api.post("/api/feedback/answer", json={
            "slug": slug, "question": question, "verdict": "disagree",
            "analysis_id": "not_an_analysis_for_this_question"})
        assert res.status_code == 400
        assert "does not answer that question" in res.json()["detail"]

    def test_an_empty_question_is_rejected(self, client, slug):
        api, _ = client
        res = api.post("/api/feedback/answer", json={
            "slug": slug, "question": "   ", "verdict": "disagree"})
        assert res.status_code == 400
