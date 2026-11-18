"""Phase 0 S4: the feedback hotfix (POST /api/feedback).

The live bug (2026-11-10): the route was `async def` and called the synchronous collector, whose
MLflow call retried a refused port for ~4 minutes and blocked EA's whole event loop; the route
answered {"status": "ok"} whatever happened; the intent label went into user_comment; votes had no
user id; the Postgres query_metrics update matched on query text only.

No MLflow, no Postgres, no Egeria: MLflow is a fake that hangs or refuses, Postgres a recording fake,
and the data root a temp directory.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from advisor import feedback_collector as fcmod

_REAL_SUBMIT = fcmod.submit_mlflow_feedback      # captured before the autouse fixture stubs it

# Upper bounds for the ordering tests below. They only matter on the FAILURE path (how long a broken
# build waits before failing); a passing run never waits for them, however loaded the machine.
MLFLOW_HANG_S = 30
LOOP_HOLD_S = 15

BODY = {"query": "what is a glossary?", "query_type": "explanation", "vote": 1,
        "perspective": "Data Steward", "routing_agent": "doc_agent",
        "response_text": "A glossary is ...", "intent_override": "explain"}


class FakeDB:
    def __init__(self, affected=1, fail=None):
        self.calls = []
        self.affected = affected
        self.fail = fail

    def execute_update(self, sql, params):
        self.calls.append((" ".join(sql.split()), params))
        if self.fail:
            raise self.fail
        return self.affected


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ADVISOR_DATA_PATH", str(tmp_path))
    monkeypatch.setattr(fcmod, "_feedback_collector", None)
    monkeypatch.setattr(fcmod, "_mlflow_failure_logged", False)
    monkeypatch.setattr(fcmod, "_mlflow_disabled_logged", False)
    db = FakeDB()
    monkeypatch.setattr("advisor.db_consolidated.get_db_manager", lambda: db)
    # By default MLflow is a no-op here; tests that care replace it.
    monkeypatch.setattr(fcmod, "submit_mlflow_feedback", lambda *a, **k: None)
    yield db


def _records(tmp_path, name="user_feedback.jsonl"):
    """The legacy JSONL lines: A-F1 writes none any more, so every caller below expects []."""
    path = tmp_path / "feedback" / name
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _items():
    """The votes as the trellis-feedback store holds them (A-F1), newest first."""
    from advisor.feedback_store import get_store
    return get_store().list(kind="answer")


def _break_the_store(tmp_path, monkeypatch):
    """Point the store at a SQLite file in a folder that does not exist: opening it fails."""
    from advisor import feedback_store
    feedback_store.reset_stores()
    monkeypatch.setenv("ADVISOR_FEEDBACK_DATABASE_URL", f"sqlite:///{tmp_path / 'no-such-dir' / 'f.db'}")


@pytest.fixture
def http(monkeypatch):
    from fastapi.testclient import TestClient
    from advisor import auth
    from advisor.web.app import app
    monkeypatch.setattr(auth, "_auth_enabled", lambda: True)
    client = TestClient(app, raise_server_exceptions=False)

    def as_user(user):
        return {"Authorization": f"Bearer {auth.create_access_token(user_id=user, egeria_token='tok-' + user)}"}
    return client, as_user


# ── fields: user id recorded, intent not in user_comment ───────────────────────

def test_vote_records_user_id_and_keeps_intent_out_of_comment(http, tmp_path, _isolated):
    client, as_user = http
    r = client.post("/api/feedback", json=BODY, headers=as_user("erinoverview"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok" and body["created"] is True
    assert body["secondary"] == {"query_metrics": "ok"}

    (item,) = _items()
    assert item["id"] == body["item"]["id"]
    assert item["user_id"] == "erinoverview"
    assert item["intent_label"] == "explanation"           # the routed intent (A-F1 N2)
    assert item["extra"]["intent_selected"] == "explain"   # the UI selector: its own field, never a comment
    assert item["query_text"] == BODY["query"] and item["response_text"] == BODY["response_text"]
    assert (item["perspective"], item["routing_agent"]) == ("Data Steward", "doc_agent")
    assert item["message"] == ""
    extra = item["extra"]
    assert "user_comment" not in extra and "feedback_text" not in extra
    assert "sentiment" not in extra                        # nothing the user wrote, nothing analysed
    assert "query_type" not in extra                       # not repeated: it is intent_label
    assert _records(tmp_path) == [] and _records(tmp_path, "feedback_extended.jsonl") == []   # no JSONL


# ── query_metrics: by user AND query, skipped without a user ───────────────────

def test_query_metrics_update_matches_user_and_query(_isolated):
    fc = fcmod.get_feedback_collector()
    out = fc.record_vote(query="q1", query_type="t", collections_searched=[], response_length=0,
                         rating="negative", user_id="peterprofile", intent_override="act")
    assert out["query_metrics"] == "ok" and out["created"] is True
    sql, params = _isolated.calls[0]
    assert "WHERE user_id = %s AND query_text = %s" in sql
    assert params[-2:] == ("peterprofile", "q1")
    assert params[0] is False and params[1] == "negative"
    assert params[4] is None                               # feedback_text: never the intent label


def test_query_metrics_skipped_without_a_user(_isolated):
    fc = fcmod.get_feedback_collector()
    out = fc.record_vote(query="q1", query_type="t", collections_searched=[], response_length=0,
                         rating="positive", user_id=None)
    assert out["query_metrics"] == "skipped"
    assert _isolated.calls == []                           # never a text-only guess


def test_query_metrics_no_match_and_error_are_reported(_isolated):
    fc = fcmod.get_feedback_collector()
    _isolated.affected = 0
    assert fc.record_vote(query="q", query_type="t", collections_searched=[], response_length=0,
                          rating="positive", user_id="u")["query_metrics"] == "no_match"
    _isolated.fail = RuntimeError("pg down")
    assert fc.record_vote(query="q", query_type="t", collections_searched=[], response_length=0,
                          rating="positive", user_id="u")["query_metrics"] == "error"


# ── honest status ──────────────────────────────────────────────────────────────

def test_primary_write_failure_is_500_with_the_reason(http, tmp_path, monkeypatch, _isolated):
    client, as_user = http
    _break_the_store(tmp_path, monkeypatch)
    r = client.post("/api/feedback", json=BODY, headers=as_user("erinoverview"))
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert detail.startswith("could not record your feedback: feedback store not opened"), detail
    assert "unable to open database file" in detail
    assert _isolated.calls == []                           # no secondary for a vote that was not kept


def test_cli_wrapper_still_returns_a_bool(tmp_path, monkeypatch):
    fc = fcmod.get_feedback_collector()
    assert fc.record_feedback(query="q", query_type="t", collections_searched=[], response_length=0,
                              rating="positive") is True
    _break_the_store(tmp_path, monkeypatch)
    assert fc.record_feedback(query="q", query_type="t", collections_searched=[], response_length=0,
                              rating="positive") is False


# ── MLflow: never on the request path ──────────────────────────────────────────

def test_hanging_mlflow_does_not_delay_the_response(http, monkeypatch):
    """The real background submit, with an MLflow log that hangs until the test releases it: both
    responses come back while MLflow is STILL hanging. Ordering, not a wall-clock bound, so a loaded
    machine cannot fail it; if the request path waited on MLflow, the hang would end (its timeout)
    before the response, and the assertion on `finished` fails."""
    client, as_user = http
    monkeypatch.setattr(fcmod, "submit_mlflow_feedback", _REAL_SUBMIT)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    class Tracker:
        enabled = True
    monkeypatch.setattr("advisor.mlflow_tracking.get_mlflow_tracker", lambda: Tracker())

    def hang(self, entry, sentiment_result=None, raise_errors=False):
        entered.set()
        release.wait(MLFLOW_HANG_S)
        finished.set()
    monkeypatch.setattr(fcmod.FeedbackCollector, "log_feedback_to_mlflow", hang)
    try:
        r = client.post("/api/feedback", json=BODY, headers=as_user("erinoverview"))
        r2 = client.post("/api/feedback", json={**BODY, "vote": -1}, headers=as_user("erinoverview"))
        still_hanging = not finished.is_set()
        assert entered.wait(MLFLOW_HANG_S), "the background MLflow job never ran"
    finally:
        release.set()
        _drain_mlflow_worker()   # both queued jobs end HERE, while `hang` is still the patched log
    assert r.status_code == 200 and r2.status_code == 200
    assert still_hanging, "a response waited for MLflow to finish"


def _drain_mlflow_worker():
    """Wait until every MLflow job already queued on the one background worker has run. Without this,
    the second vote's job could run after this test's patches were undone, in the NEXT test, and be
    counted there (seen: test_refused_mlflow_is_logged_once got 4 calls instead of 3)."""
    executor = fcmod._mlflow_executor
    if executor is not None:
        executor.submit(lambda: None).result(MLFLOW_HANG_S)


def test_refused_mlflow_is_logged_once_and_never_raises(monkeypatch):
    """MLflow that refuses: the job swallows it, logs one warning, and applies the fail-fast env."""
    class Tracker:
        enabled = True
    monkeypatch.setattr("advisor.mlflow_tracking.get_mlflow_tracker", lambda: Tracker())
    monkeypatch.delenv("MLFLOW_HTTP_REQUEST_MAX_RETRIES", raising=False)
    monkeypatch.delenv("MLFLOW_HTTP_REQUEST_TIMEOUT", raising=False)
    calls = []

    def refuse(self, entry, sentiment_result=None, raise_errors=False):
        calls.append(raise_errors)
        raise ConnectionRefusedError("[Errno 61] Connection refused: localhost:5000")
    monkeypatch.setattr(fcmod.FeedbackCollector, "log_feedback_to_mlflow", refuse)
    warnings = []
    monkeypatch.setattr(fcmod.logger, "warning", lambda msg, *a, **k: warnings.append(msg))
    fc = fcmod.get_feedback_collector()
    entry = fcmod.FeedbackEntry(timestamp="t", query="q", query_type="t", collections_searched=[],
                                response_length=0, rating="positive")
    for _ in range(3):
        _REAL_SUBMIT(fc, entry).result(5)
    assert calls == [True, True, True]
    assert len([w for w in warnings if "MLflow" in w]) == 1
    import os
    assert os.environ["MLFLOW_HTTP_REQUEST_MAX_RETRIES"] == "0"
    assert int(os.environ["MLFLOW_HTTP_REQUEST_TIMEOUT"]) <= 2


def test_mlflow_skipped_when_the_probe_disabled_tracking(monkeypatch):
    class Tracker:
        enabled = False
    monkeypatch.setattr("advisor.mlflow_tracking.get_mlflow_tracker", lambda: Tracker())
    called = []
    monkeypatch.setattr(fcmod.FeedbackCollector, "log_feedback_to_mlflow",
                        lambda *a, **k: called.append(1))
    fc = fcmod.get_feedback_collector()
    entry = fcmod.FeedbackEntry(timestamp="t", query="q", query_type="t", collections_searched=[],
                                response_length=0, rating="positive")
    _REAL_SUBMIT(fc, entry).result(5)
    assert called == []


# ── the event loop stays free while a vote is being written ────────────────────

def test_event_loop_serves_other_requests_while_a_vote_is_written(monkeypatch):
    """While a vote is INSIDE the collector, another request on the same event loop completes, and it
    is that request finishing that lets the vote go on. Ordering by events, not sleeps or a clock, so
    a loaded machine cannot fail it. Were the vote run on the event loop (the old async route), the
    probe could not run until the vote's wait timed out: `released` would be False."""
    import httpx
    from advisor import auth
    from advisor.web.app import app
    monkeypatch.setattr(auth, "_auth_enabled", lambda: True)
    headers = {"Authorization": f"Bearer {auth.create_access_token(user_id='erinoverview', egeria_token='tok')}"}

    real_vote = fcmod.FeedbackCollector.record_vote
    vote_entered, probe_done = threading.Event(), threading.Event()
    released = []

    def held_vote(self, **kw):
        vote_entered.set()
        released.append(probe_done.wait(LOOP_HOLD_S))
        return real_vote(self, **kw)
    monkeypatch.setattr(fcmod.FeedbackCollector, "record_vote", held_vote)

    async def go():
        done = []
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://ea") as client:
            async def vote():
                r = await client.post("/api/feedback", json=BODY, headers=headers)
                done.append("vote")
                return r

            async def other():
                # Any cheap route a signed-in non-admin may call: the probe is "does the loop answer",
                # not this route. (Not /api/feedback/extended: S1 made that admin-only.)
                await asyncio.to_thread(vote_entered.wait, LOOP_HOLD_S)
                r = await client.get("/api/auth/me", headers=headers)
                done.append("other")
                probe_done.set()
                return r
            rv, ro = await asyncio.gather(vote(), other())
        return done, rv, ro

    done, rv, ro = asyncio.run(go())
    assert rv.status_code == 200 and ro.status_code == 200, (rv.text, ro.text)
    assert released == [True], "the vote held the event loop: the probe could not run while it was written"
    assert done == ["other", "vote"]


def test_anonymous_vote_records_no_user_and_skips_query_metrics(_isolated, tmp_path):
    """No signed-in user (e.g. anonymous mode): user_id None, and the query_metrics update is
    skipped rather than matched on text alone."""
    from advisor.web.feedback import _record_vote
    from advisor.web.shared import FeedbackRequest
    out = _record_vote(FeedbackRequest(**BODY), None)
    assert out["secondary"]["query_metrics"] == "skipped" and _isolated.calls == []
    assert _items()[0]["user_id"] == ""                    # the library's "anonymous"
