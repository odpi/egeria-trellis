"""Phase 2 A-F1: EA's votes in the trellis-feedback store, triaged through the library's router.

Every test runs on SQLite and, when EA's Postgres is reachable, on a throwaway schema of its own
(`ea_test_schema`, the Q1 pattern), dropped afterwards. Nothing touches EA's real feedback tables.

No MLflow and no query_metrics Postgres: both are fakes here (`_quiet`).
"""
from __future__ import annotations

import threading

import pytest
from sqlalchemy import update

from advisor import auth
from advisor import feedback_collector as fcmod
from advisor import feedback_store

ADMIN, OTHER = "garygeeke", "peterprofile"
BODY = {"query": "what is a glossary?", "query_type": "explanation", "vote": 1,
        "perspective": "Data Steward", "routing_agent": "doc_agent",
        "response_text": "A glossary is ...", "intent_override": "explain"}


class _FakeDB:
    def __init__(self):
        self.calls = []

    def execute_update(self, sql, params):
        self.calls.append(params)
        return 1


@pytest.fixture(params=["sqlite", "postgres"])
def backend(request, monkeypatch):
    feedback_store.reset_stores()
    if request.param == "postgres":
        schema = request.getfixturevalue("ea_test_schema")      # skips when Postgres is not reachable
        monkeypatch.delenv(feedback_store.URL_ENV, raising=False)
        monkeypatch.setenv(feedback_store.URL_ENV, feedback_store.feedback_database_url())
        monkeypatch.setenv(feedback_store.SCHEMA_ENV, schema)
        # Registered after the schema fixture, so it runs first: the pool is closed before the DROP.
        request.addfinalizer(feedback_store.reset_stores)
    yield request.param
    feedback_store.reset_stores()


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr("advisor.db_consolidated.get_db_manager", lambda: db)
    monkeypatch.setattr(fcmod, "submit_mlflow_feedback", lambda *a, **k: None)
    monkeypatch.setattr(fcmod, "_feedback_collector", None)
    for var in ("TRELLIS_ADMIN_USERS", "TRELLIS_ADMIN_TOKEN", "ADVISOR_ADMIN_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ADVISOR_ADMIN_USERS", ADMIN)
    auth.reset_policy_cache()
    yield db
    auth.reset_policy_cache()


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from advisor.web.app import app
    monkeypatch.setattr(auth, "_auth_enabled", lambda: True)
    return TestClient(app, raise_server_exceptions=False)


def _as(user):
    return {"Authorization": f"Bearer {auth.create_access_token(user_id=user, egeria_token='tok-' + user)}"}


def _store():
    return feedback_store.get_store()


def _vote(fc, query, *, key=None, user="u1", rating="negative"):
    return fc.record_vote(query=query, query_type="t", collections_searched=[], response_length=0,
                          rating=rating, user_id=user, idempotency_key=key)


# ── the vote is an answer item ──────────────────────────────────────────────────────────────────

def test_a_vote_is_an_answer_item_with_its_fields(backend, client):
    r = client.post("/api/feedback", json={**BODY, "session_id": "s1", "exchange_id": "x1"}, headers=_as(OTHER))
    assert r.status_code == 200, r.text
    (item,) = _store().list(kind="answer")
    assert item["id"] == r.json()["item"]["id"]
    assert (item["app"], item["kind"], item["vote"]) == ("egeria-advisor", "answer", 1)
    assert (item["user_id"], item["session_id"]) == (OTHER, "s1")
    assert item["query_text"] == BODY["query"] and item["response_text"] == BODY["response_text"]
    # N2: intent_label is the ROUTED intent; the UI selector has its own clearly named field
    assert (item["intent_label"], item["perspective"], item["routing_agent"]) == ("explanation", "Data Steward", "doc_agent")
    assert item["extra"]["intent_selected"] == "explain" and "query_type" not in item["extra"]
    assert item["extra"]["exchange_id"] == "x1"
    assert item["triage"]["status"] == "new"


def test_a_retried_vote_with_the_same_key_is_one_item(backend, client, _quiet):
    body = {**BODY, "session_id": "s1", "exchange_id": "x1"}
    first = client.post("/api/feedback", json=body, headers=_as(OTHER)).json()
    again = client.post("/api/feedback", json=body, headers=_as(OTHER)).json()
    assert first["created"] is True and again["created"] is False
    assert again["item"]["id"] == first["item"]["id"]
    assert again["secondary"]["query_metrics"] == "not_run"         # the secondary sinks ran once
    assert len(_quiet.calls) == 1
    assert _store().count(kind="answer") == 1
    # The same exchange voted differently is a different vote; no ids at all is a plain submit.
    client.post("/api/feedback", json={**body, "vote": -1}, headers=_as(OTHER))
    client.post("/api/feedback", json=BODY, headers=_as(OTHER))
    client.post("/api/feedback", json=BODY, headers=_as(OTHER))
    assert _store().count(kind="answer") == 4


def test_one_users_key_never_finds_another_users_vote(backend, client):
    body = {**BODY, "session_id": "s1", "exchange_id": "x1"}
    a = client.post("/api/feedback", json=body, headers=_as(OTHER)).json()
    b = client.post("/api/feedback", json=body, headers=_as(ADMIN)).json()
    assert a["item"]["id"] != b["item"]["id"] and b["created"] is True


def test_an_unknown_vote_is_400_not_a_stored_item(backend, client):
    r = client.post("/api/feedback", json={**BODY, "vote": 5}, headers=_as(OTHER))
    assert r.status_code == 400
    assert _store().count(kind="answer") == 0


# ── concurrency ─────────────────────────────────────────────────────────────────────────────────

def test_concurrent_triage_and_submit(backend):
    fc = fcmod.get_feedback_collector()
    target = _vote(fc, "the item being triaged")["item_id"]
    statuses = ["triaged", "known_issue", "actioned", "deferred"] * 2
    errors = []
    start = threading.Barrier(len(statuses) + 8 + 4)

    def triage(i, status):
        try:
            start.wait()
            _store().triage(target, status, note=f"n{i}", actor=ADMIN)
        except Exception as exc:  # noqa: BLE001 - collected and asserted on below
            errors.append(exc)

    def submit(i, key=None):
        try:
            start.wait()
            _vote(fc, f"q{i}" if key is None else "retried", key=key)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = ([threading.Thread(target=triage, args=(i, s)) for i, s in enumerate(statuses)]
               + [threading.Thread(target=submit, args=(i,)) for i in range(8)]
               + [threading.Thread(target=submit, args=(99, "same-key")) for _ in range(4)])
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors, errors
    store = _store()
    assert store.count(kind="answer") == 1 + 8 + 1                  # four sends of one key: one item
    history = store.history(target)
    assert len(history) == len(statuses)
    seqs = [e.seq for e in history]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert store.state(target).status == history[-1].status      # the latest event wins
    others = [r for r in store.list(kind="answer") if r["id"] != target]
    assert all(r["triage"]["events"] == 0 for r in others)       # no event landed on another item


# ── triage through the library router ──────────────────────────────────────────────────────────

def test_a_listed_items_triage_target_is_the_item_shown(backend, client):
    """Extraction doc §6.2: the id a row is triaged by is the id of the item that row shows, also when
    the store holds a row whose `extra` cannot be read (EA's live bug was a skipped line shifting every
    position after it)."""
    fc = fcmod.get_feedback_collector()
    ids = [_vote(fc, f"question {n}")["item_id"] for n in range(4)]
    store = _store()
    with store.engine.begin() as conn:                       # one corrupt row in the middle
        conn.execute(update(store.items).where(store.items.c.id == ids[1]).values(extra="{not json"))
    listed = client.get("/api/feedback/v2/items?kind=answer", headers=_as(ADMIN)).json()["items"]
    assert {r["id"] for r in listed} == set(ids)
    for row in listed:
        r = client.post("/api/feedback/v2/triage", headers=_as(ADMIN),
                        json={"ref_id": row["id"], "status": "triaged", "note": row["query_text"]})
        assert r.status_code == 200, r.text
        assert r.json()["event"]["actor"] == ADMIN                # the signed-in admin, not a body field
    for row in listed:
        got = client.get(f"/api/feedback/v2/items/{row['id']}", headers=_as(ADMIN)).json()
        assert got["item"]["query_text"] == row["query_text"]
        assert [e["note"] for e in got["history"]] == [row["query_text"]]   # its own event, and only that


def test_triage_is_admin_only_and_voting_is_not(backend, client):
    vote = client.post("/api/feedback", json=BODY, headers=_as(OTHER))
    assert vote.status_code == 200
    item_id = vote.json()["item"]["id"]
    r = client.post("/api/feedback/v2/triage", headers=_as(OTHER), json={"ref_id": item_id, "status": "actioned"})
    assert r.status_code == 403
    assert client.get("/api/feedback/v2/items", headers=_as(OTHER)).status_code == 403
    assert _store().state(item_id).status == "new"


def test_the_router_answers_503_with_the_reason_when_the_store_is_down(client, tmp_path, monkeypatch):
    feedback_store.reset_stores()
    monkeypatch.setenv(feedback_store.URL_ENV, f"sqlite:///{tmp_path / 'missing' / 'f.db'}")
    r = client.get("/api/feedback/v2/items", headers=_as(ADMIN))
    assert r.status_code == 503 and "feedback store not opened" in r.json()["detail"]


# ── the analysis reads the library ──────────────────────────────────────────────────────────────

def test_analysis_counts_the_library_votes(backend, client):
    fc = fcmod.get_feedback_collector()
    _vote(fc, "a", rating="positive")
    _vote(fc, "b", rating="negative")
    fc.record_vote(query="c", query_type="t", collections_searched=[], response_length=0,
                   rating="negative", routing_agent="rag_fallback", perspective="Developer")
    body = client.get("/api/feedback/analysis", headers=_as(ADMIN)).json()
    assert body["state"] == "ok" and body["source"] == "trellis-feedback" and body["count"] == 3
    assert (body["stats"]["total"], body["stats"]["positive"], body["stats"]["negative"]) == (3, 1, 2)
    # "b" has no routing agent (read as "unknown"), "c" fell back to RAG: both are fallback negatives
    assert {g["query"] for g in body["gaps"]["rag_fallback_negatives"]} == {"b", "c"}


def test_analysis_with_no_votes_says_so(backend, client):
    body = client.get("/api/feedback/analysis", headers=_as(ADMIN)).json()
    assert body["state"] == "none" and body["message"] == "No votes have been recorded yet."


def test_analysis_that_cannot_read_says_why_and_shows_no_zeros(client, tmp_path, monkeypatch):
    feedback_store.reset_stores()
    monkeypatch.setenv(feedback_store.URL_ENV, f"sqlite:///{tmp_path / 'missing' / 'f.db'}")
    body = client.get("/api/feedback/analysis", headers=_as(ADMIN)).json()
    assert body["state"] == "not_read" and body["stats"] is None
    assert body["message"].startswith("Feedback not read: feedback store not opened")


def test_a_legacy_file_collector_reports_unreadable_lines(tmp_path):
    f = tmp_path / "old.jsonl"
    f.write_text('{"query": "q", "rating": "positive"}\n{broken\n\n{"query": "r", "rating": "negative"}\n')
    fc = fcmod.FeedbackCollector(f)
    out = fc.analysis()
    assert out["count"] == 2 and out["stats"]["total"] == 2
    assert out["unreadable_lines"] == [{"line": 2, "reason": out["unreadable_lines"][0]["reason"]}]
    assert out["unreadable_lines"][0]["reason"].startswith("not JSON")


# ── maintenance (B6's flag, folded into A-F1) ───────────────────────────────────────────────────

def test_a_busy_spec_sync_is_409_not_a_200_error(client, monkeypatch):
    from advisor import egeria_spec_sync as ess

    def busy():
        raise ess.SyncBusy("A sync is already running (started 10:00).")
    monkeypatch.setattr(ess, "start_sync", busy)
    r = client.post("/api/admin/maintenance/sync_specs", headers=_as(ADMIN))
    assert r.status_code == 409 and r.json()["detail"].startswith("A sync is already running")


def test_a_failed_cache_clear_is_500_with_the_reason(client, monkeypatch):
    class _Cache:
        def clear(self):
            raise OSError("disk full")
    monkeypatch.setattr("advisor.query_cache.get_query_cache", lambda: _Cache())
    r = client.post("/api/admin/maintenance/clear_cache", headers=_as(ADMIN))
    assert r.status_code == 500 and r.json()["detail"] == "Query cache not cleared: OSError: disk full"


# ── A-F1 N1: a changed vote supersedes the earlier one; N2: routed intent vs selector ────────────

def _click(client, vote, seq, user=OTHER, exchange="x1"):
    r = client.post("/api/feedback", json={**BODY, "vote": vote, "session_id": "s1", "exchange_id": exchange,
                                           "vote_seq": seq}, headers=_as(user))
    assert r.status_code == 200, r.text
    return r.json()


def test_a_changed_vote_is_stored_and_only_the_latest_counts(backend, client):
    up = _click(client, 1, 1)
    down = _click(client, -1, 2)
    assert up["created"] and down["created"] and up["item"]["id"] != down["item"]["id"]
    assert _store().count(kind="answer") == 2                       # both kept, with their own history
    body = client.get("/api/feedback/analysis", headers=_as(ADMIN)).json()
    assert body["count"] == 1 and body["superseded"] == 1
    assert (body["stats"]["positive"], body["stats"]["negative"]) == (0, 1)


def test_changing_back_counts_the_last_choice_not_the_first_item(backend, client):
    _click(client, 1, 1)
    _click(client, -1, 2)
    back = _click(client, 1, 3)              # a new click, so a new item: not a replay of click 1
    assert back["created"] is True
    body = client.get("/api/feedback/analysis", headers=_as(ADMIN)).json()
    assert (body["count"], body["superseded"]) == (1, 2)
    assert (body["stats"]["positive"], body["stats"]["negative"]) == (1, 0)


def test_a_resent_click_is_still_one_item(backend, client):
    first = _click(client, -1, 2)
    again = _click(client, -1, 2)
    assert again["created"] is False and again["item"]["id"] == first["item"]["id"]
    assert _store().count(kind="answer") == 1


def test_votes_on_different_answers_or_by_different_users_all_count(backend, client):
    _click(client, 1, 1, exchange="x1")
    _click(client, -1, 1, exchange="x2")
    _click(client, -1, 1, user=ADMIN, exchange="x1")
    fc = fcmod.get_feedback_collector()
    _vote(fc, "a CLI vote, no exchange id")
    _vote(fc, "another CLI vote, no exchange id")
    body = client.get("/api/feedback/analysis", headers=_as(ADMIN)).json()
    assert (body["count"], body["superseded"]) == (5, 0)


def test_routed_intent_reads_items_from_before_n2_too():
    old = {"intent_label": "auto", "extra": {"query_type": "explanation"}}
    new = {"intent_label": "explanation", "extra": {"intent_selected": "auto"}}
    assert feedback_store.routed_intent(old) == feedback_store.routed_intent(new) == "explanation"
    assert feedback_store.as_collector_entry({**old, "vote": 1})["query_type"] == "explanation"
