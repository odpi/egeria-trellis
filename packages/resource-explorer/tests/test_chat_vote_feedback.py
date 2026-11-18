"""Chat votes and checklist verdicts are kept as `answer` feedback items Admin can read and triage.

docs/trellis-feedback-extraction.md §6 step 3, decisions Q4/Q5: every rating keeps its question, the full
answer and the signed-in user. Before this, a chat vote reached only query_log's hash row, so the pane said
"Marked helpful" and Admin → Feedback never showed it.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from resource_explorer.web.app import app


@pytest.fixture
def feedback_store(tmp_path):
    from resource_explorer.feedback_store import FeedbackStore
    return FeedbackStore(db_path=str(tmp_path / "votes_feedback.db"))


@pytest.fixture
def client(monkeypatch, feedback_store, tmp_path):
    # Its own registry file: this writes Curate feedback / projects, which must not reach another test even when
    # REGISTRY_DATABASE_URL was set to one file for the whole run.
    monkeypatch.setenv("REGISTRY_DATABASE_URL", f"sqlite:///{tmp_path}/registry.db")
    import resource_explorer.config as _config_module
    _config_module._config = None
    monkeypatch.setattr(
        "resource_explorer.feedback_store.FeedbackStore.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", feedback_store.__dict__) or None,
    )
    with patch("resource_explorer.observability.metrics_collector.MetricsCollector"):
        yield TestClient(app)


@pytest.fixture
def admin_headers(monkeypatch):
    from resource_explorer import config as cfg_mod
    monkeypatch.setattr(cfg_mod.get_config().feedback, "admin_token", "test-token", raising=False)
    return {"X-Admin-Token": "test-token"}


def _answers(store):
    return store.list(kind="answer")


class TestAChatVoteIsKept:
    def test_the_stored_exchange_supplies_the_text_not_the_client(self, client, feedback_store):
        from resource_explorer.web.routes.query import _persist_turn
        exchange_id = _persist_turn("sess-1", "what changed last week?", "Three commits to the parser.", "repo-a")
        resp = client.post("/api/query/feedback", json={
            "query_hash": "h1", "vote": -1, "session_id": "sess-1", "exchange_id": exchange_id,
            "question": "something else entirely", "answer": "made up", "intent": "temporal",
            "entity_type": "repo",
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["recorded"] is True and body["stored"] is True
        [row] = _answers(feedback_store)
        assert row["id"] == body["feedback_id"]
        assert (row["vote"], row["query_text"], row["response_text"]) == (
            -1, "what changed last week?", "Three commits to the parser.")
        assert (row["slug"], row["intent_label"], row["session_id"]) == ("repo-a", "temporal", "sess-1")
        assert row["extra"]["text_from"] == "stored exchange"
        assert row["extra"]["query_hash"] == "h1"

    def test_a_turn_that_was_not_stored_keeps_the_text_it_was_sent(self, client, feedback_store):
        resp = client.post("/api/query/feedback", json={
            "query_hash": "h2", "vote": 0, "question": "who owns this?", "answer": "Nobody is recorded."})
        assert resp.json()["stored"] is True
        [row] = _answers(feedback_store)
        assert (row["vote"], row["query_text"], row["response_text"]) == (0, "who owns this?", "Nobody is recorded.")
        assert row["extra"]["text_from"] == "client"

    def test_an_exchange_id_that_is_not_the_callers_falls_back_to_the_sent_text(self, client, feedback_store):
        resp = client.post("/api/query/feedback", json={
            "query_hash": "h3", "vote": 1, "session_id": "sess-x", "exchange_id": 999999,
            "question": "q", "answer": "a"})
        assert resp.json()["stored"] is True
        assert _answers(feedback_store)[0]["extra"]["text_from"] == "client"

    def test_a_vote_with_no_question_is_said_not_stored(self, client, feedback_store):
        """An older client sends only the hash: query_log still gets it, and the reply says it was not kept."""
        resp = client.post("/api/query/feedback", json={"query_hash": "h4", "vote": 1})
        body = resp.json()
        assert resp.status_code == 200
        assert body["recorded"] is True and body["stored"] is False and body["reason"]
        assert _answers(feedback_store) == []

    def test_a_retried_vote_is_one_item(self, client, feedback_store):
        payload = {"query_hash": "h5", "vote": 1, "question": "q", "answer": "a",
                   "idempotency_key": "vote:abc:1"}
        first = client.post("/api/query/feedback", json=payload).json()
        second = client.post("/api/query/feedback", json=payload).json()
        assert first["feedback_id"] == second["feedback_id"]
        assert (first["created"], second["created"]) == (True, False)
        assert len(_answers(feedback_store)) == 1

    def test_a_flipped_vote_on_one_turn_is_still_one_item_and_the_first_stands(self, client, feedback_store):
        """Read-back P2: thumbs up then thumbs down on one turn (a second click that beat the first reply).
        The key is per turn, so the server keeps one item and says which vote it kept."""
        base = {"query_hash": "h7", "question": "q", "answer": "a", "idempotency_key": "vote:turn-7"}
        first = client.post("/api/query/feedback", json={**base, "vote": 1}).json()
        second = client.post("/api/query/feedback", json={**base, "vote": -1}).json()
        assert first["feedback_id"] == second["feedback_id"]
        assert (first["vote"], second["vote"]) == (1, 1)
        assert [r["vote"] for r in _answers(feedback_store)] == [1]

    def test_a_vote_after_a_reload_on_a_stored_turn_is_the_same_item(self, client, feedback_store):
        """The pane derives a stored turn's key from session + exchange, so the restored turn's vote after a page
        reload (its page state gone) reaches the first item, not a second one."""
        from resource_explorer.web.routes.query import _persist_turn
        exchange_id = _persist_turn("sess-r", "q?", "a.", None)
        key = f"vote:sess-r:{exchange_id}"
        body = {"query_hash": "h8", "session_id": "sess-r", "exchange_id": exchange_id, "idempotency_key": key}
        first = client.post("/api/query/feedback", json={**body, "vote": -1}).json()
        again = client.post("/api/query/feedback", json={**body, "vote": 1}).json()
        assert first["feedback_id"] == again["feedback_id"] and again["vote"] == -1
        assert len(_answers(feedback_store)) == 1

    def test_the_hash_row_is_still_written(self, feedback_store, monkeypatch):
        monkeypatch.setattr(
            "resource_explorer.feedback_store.FeedbackStore.__init__",
            lambda self, db_path=None: setattr(self, "__dict__", feedback_store.__dict__) or None,
        )
        with patch("resource_explorer.observability.metrics_collector.MetricsCollector") as mc:
            TestClient(app).post("/api/query/feedback", json={"query_hash": "h6", "vote": 0, "question": "q"})
        mc.return_value.record_feedback.assert_called_once_with("h6", 0)


class TestAChecklistVerdict:
    def _post(self, client, **kw):
        from resource_explorer.registry import Project, ProjectRegistry
        reg = ProjectRegistry()
        if reg.get("repo-v") is None:
            reg.add(Project(slug="repo-v", display_name="repo-v", github_url="https://github.com/x/repo-v"))
        from resource_explorer.surveyors.question_catalog_reader import get_questions
        question = get_questions("repo")[0]["question"]
        body = {"slug": "repo-v", "question": question, "verdict": "partly", "comment": "half of it",
                "entity_type": "repo"}
        body.update(kw)
        return client.post("/api/feedback/answer", json=body), question

    def test_it_is_an_answer_item_with_its_vote_and_question(self, client, feedback_store):
        resp, question = self._post(client)
        assert resp.status_code == 200, resp.text
        [row] = _answers(feedback_store)
        assert (row["vote"], row["query_text"], row["message"], row["slug"]) == (0, question, "half of it", "repo-v")
        assert row["extra"]["verdict"] == "partly"
        assert feedback_store.list() == []        # no longer a page (product) row

    def test_a_chat_vote_already_stored_is_not_stored_twice(self, client, feedback_store):
        feedback_id, _ = feedback_store.add_answer(vote=-1, question="from chat", slug="repo-v")
        resp, _ = self._post(client, verdict="disagree", feedback_id=feedback_id)
        assert resp.status_code == 200, resp.text
        assert [r["id"] for r in _answers(feedback_store)] == [feedback_id]

    def test_another_users_item_does_not_stand_in_for_this_verdict(self, client, feedback_store):
        feedback_id, _ = feedback_store.add_answer(vote=-1, question="theirs", user_id="someone-else",
                                                   slug="repo-v")
        resp, _ = self._post(client, feedback_id=feedback_id)
        assert resp.status_code == 200
        assert len(_answers(feedback_store)) == 2

    def test_an_item_about_another_resource_does_not_stand_in(self, client, feedback_store):
        feedback_id, _ = feedback_store.add_answer(vote=-1, question="q", slug="other-repo")
        resp, _ = self._post(client, feedback_id=feedback_id)
        assert resp.status_code == 200
        assert len(_answers(feedback_store)) == 2

    def test_an_unknown_feedback_id_still_records_the_verdict(self, client, feedback_store):
        resp, _ = self._post(client, feedback_id="not-an-item")
        assert resp.status_code == 200
        assert len(_answers(feedback_store)) == 1


class TestAdminListsRatedAnswers:
    def test_answers_appear_badged_and_counted(self, client, feedback_store, admin_headers):
        from resource_explorer.feedback_store import FeedbackEntry
        feedback_store.add(FeedbackEntry(page="/scouting", message="page report"))
        feedback_store.add_answer(vote=1, question="q1", answer="a1", user_id="peterprofile",
                                  entity_type="database", slug="db-1")
        data = client.get("/api/curate/feedback", headers=admin_headers).json()
        by_source = {r["source"]: r for r in data["feedback"]}
        assert set(by_source) == {"page", "answer"}
        answer = by_source["answer"]
        assert (answer["query_text"], answer["response_text"], answer["user_id"]) == ("q1", "a1", "peterprofile")
        assert answer["triage_status"] == "new"
        assert data["counts"]["answer"] == {"total": 1}

    def test_source_and_resource_type_filter_answers(self, client, feedback_store, admin_headers):
        feedback_store.add_answer(vote=1, question="q1", entity_type="database", slug="db-1")
        feedback_store.add_answer(vote=-1, question="q2", entity_type="repo", slug="r-1")
        only = client.get("/api/curate/feedback?source=answer", headers=admin_headers).json()["feedback"]
        assert {r["query_text"] for r in only} == {"q1", "q2"}
        db = client.get("/api/curate/feedback?entity_type=database", headers=admin_headers).json()["feedback"]
        assert [r["query_text"] for r in db if r["source"] == "answer"] == ["q1"]
        cat = client.get("/api/curate/feedback?category=bug", headers=admin_headers).json()["feedback"]
        assert [r for r in cat if r["source"] == "answer"] == []

    def test_an_answer_is_triaged_through_the_same_route(self, client, feedback_store, admin_headers):
        feedback_id, _ = feedback_store.add_answer(vote=-1, question="q")
        resp = client.patch(f"/api/feedback/{feedback_id}", json={"triage_status": "known_issue", "note": "dup"},
                            headers=admin_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["triage_status"] == "known_issue"

    def test_the_listing_is_still_admin_only(self, client, feedback_store):
        feedback_store.add_answer(vote=1, question="private question")
        assert client.get("/api/curate/feedback").status_code == 403
