"""A human-question answer, author-stamped — PATCH /api/context/{type}/{slug}/answer.

ENRICHMENT-E0-ROW-ANATOMY (docs/design-notes/exchange/REPLY-DESIGNER-ENRICHMENT-
STAGE-IA.md §0.3/§6 item 1): before this route existed, a human answer to one
of the catalog's Human-Supplied questions was saved by the client doing its
own read-modify-write against the whole context document, and the record it
built carried only a timestamp — no author at all, breaking the reply's own
stated rule that "a human-supplied answer carries who and when." This mirrors
`test_enrichment_fields.py`'s tests for `save_field` (the same rule already
enforced for enrichment judgements/observations): the author is stamped by
the server from the signed-in identity, never taken from the client, and an
anonymous write is refused rather than recorded as nobody's.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import Project, ProjectRegistry


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="p", display_name="P", github_url="https://github.com/x/p", description=""))
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    from resource_explorer.web.app import app
    return TestClient(app)


def signed_in_as(monkeypatch, user_id):
    monkeypatch.setattr("resource_explorer.web.routes.context.get_current_user", lambda request: {"user_id": user_id})


QUESTION = "Do we already support these dependencies?"


class TestAnswerIsAuthorStamped:
    def test_answered_by_and_answered_at_are_stamped_by_the_server(self, client, monkeypatch):
        signed_in_as(monkeypatch, "peterprofile")
        r = client.patch("/api/context/repo/p/answer", json={"question": QUESTION, "answer": "Yes, both."})
        assert r.status_code == 200, r.text
        a = r.json()["answer"]
        assert a["answered_by"] == "peterprofile"
        assert a["answered_at"].startswith("2026") or a["answered_at"]
        assert a["question"] == QUESTION
        assert a["answer"] == "Yes, both."

    def test_the_client_cannot_assert_an_answerer(self, client, monkeypatch):
        """The write model has no `answered_by` field at all — a forged
        identity in the body is simply not read."""
        signed_in_as(monkeypatch, "peterprofile")
        r = client.patch("/api/context/repo/p/answer", json={
            "question": QUESTION, "answer": "Yes.", "answered_by": "forged",
        })
        assert r.status_code == 200
        assert r.json()["answer"]["answered_by"] == "peterprofile"

    def test_anonymous_is_refused_not_recorded_as_nobody(self, client, monkeypatch):
        monkeypatch.setattr("resource_explorer.web.routes.context.get_current_user", lambda request: None)
        r = client.patch("/api/context/repo/p/answer", json={"question": QUESTION, "answer": "Yes."})
        assert r.status_code == 401

    def test_blank_question_is_refused(self, client, monkeypatch):
        signed_in_as(monkeypatch, "a")
        r = client.patch("/api/context/repo/p/answer", json={"question": "  ", "answer": "Yes."})
        assert r.status_code == 422


class TestPerAnswerSave:
    def test_two_answers_saved_separately_both_survive(self, client, monkeypatch, registry):
        signed_in_as(monkeypatch, "a")
        client.patch("/api/context/repo/p/answer", json={"question": QUESTION, "answer": "Yes."})
        signed_in_as(monkeypatch, "b")
        client.patch("/api/context/repo/p/answer", json={"question": "What does it cost to run?", "answer": "$200/mo."})
        ctx = registry.get_context("repo", "p")
        answers = ctx["question_answers"]
        assert len(answers) == 2
        by_question = {v["question"]: v for v in answers.values()}
        assert by_question[QUESTION]["answered_by"] == "a"
        assert by_question["What does it cost to run?"]["answered_by"] == "b"

    def test_answering_one_question_does_not_clobber_enrichment_fields(self, client, monkeypatch):
        """The two stores stay separate stores (design's ruling, §0.3) — an
        answer save must not touch `enrichment`, the same way a `save_field`
        write must not touch `question_answers`."""
        signed_in_as(monkeypatch, "a")
        client.patch("/api/context/repo/p/field", json={"key": "sensitivity", "value": "internal", "kind": "judgement"})
        client.patch("/api/context/repo/p/answer", json={"question": QUESTION, "answer": "Yes."})
        ctx = client.get("/api/context/repo/p").json()
        assert ctx["enrichment"]["sensitivity"]["value"] == "internal"
        assert len(ctx["question_answers"]) == 1


class TestReviseAfterReload:
    def test_a_saved_answer_is_readable_back_through_get_context(self, client, monkeypatch):
        signed_in_as(monkeypatch, "peterprofile")
        client.patch("/api/context/repo/p/answer", json={"question": QUESTION, "answer": "Yes, both."})
        r = client.get("/api/context/repo/p")
        assert r.status_code == 200
        answers = r.json()["question_answers"]
        assert len(answers) == 1
        answer = next(iter(answers.values()))
        assert answer["answer"] == "Yes, both."
        assert answer["answered_by"] == "peterprofile"

    def test_re_answering_the_same_question_overwrites_its_own_key_not_the_whole_document(self, client, monkeypatch):
        signed_in_as(monkeypatch, "a")
        client.patch("/api/context/repo/p/answer", json={"question": QUESTION, "answer": "First answer."})
        signed_in_as(monkeypatch, "b")
        client.patch("/api/context/repo/p/answer", json={"question": QUESTION, "answer": "Revised answer."})
        ctx = client.get("/api/context/repo/p").json()
        answers = ctx["question_answers"]
        assert len(answers) == 1
        answer = next(iter(answers.values()))
        assert answer["answer"] == "Revised answer."
        assert answer["answered_by"] == "b"
