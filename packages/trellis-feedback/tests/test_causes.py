"""0.3: a diagnosis on the triage event -- the cause of a failed answer (docs/feedback-closed-loop-design.md §2, §3.1).

The cause is append-only like the status, is the latest DIAGNOSIS rather than the latest event's, and is checked
against the precedence rule so that two people reading one failure record the same thing.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from trellis_feedback import CAUSE_CHAIN, CAUSES, UNDIAGNOSED, FeedbackError, FeedbackItem, NotFound
from trellis_feedback.router import make_router


def answer(vote=-1, **kw) -> FeedbackItem:
    return FeedbackItem(app="ignored", kind="answer", vote=vote, query_text="what changed?", response_text="it did",
                        **kw)


def product() -> FeedbackItem:
    return FeedbackItem(app="ignored", kind="product", category="bug", message="the button is wrong")


def _events(store) -> int:
    with store.engine.connect() as conn:
        return conn.execute(text(f"SELECT COUNT(*) FROM {_table(store)}")).scalar_one()


def _table(store) -> str:
    return f'"{store.schema}"."trellis_feedback_events"' if store.schema else '"trellis_feedback_events"'


class TestVocabulary:
    def test_the_chain_is_in_precedence_order_and_the_two_outside_it_follow(self):
        assert CAUSE_CHAIN == ("catalog_gap", "missing_evidence", "stale", "analysis_defect",
                               "wrong_evidence", "over_compressed", "reasoning")
        assert CAUSES == (*CAUSE_CHAIN, "unclear_question", "not_an_issue")
        assert UNDIAGNOSED not in CAUSES


class TestDiagnosis:
    def test_a_diagnosis_is_the_items_cause(self, store):
        item, _ = store.submit(answer())
        event = store.triage(item.id, "triaged", cause="wrong_evidence", secondary_cause="reasoning", actor="a")
        assert (event.cause, event.secondary_cause) == ("wrong_evidence", "reasoning")
        state = store.state(item.id)
        assert (state.status, state.cause, state.secondary_cause) == ("triaged", "wrong_evidence", "reasoning")

    def test_a_later_status_only_change_keeps_the_cause(self, store):
        item, _ = store.submit(answer())
        store.triage(item.id, "triaged", cause="stale")
        store.triage(item.id, "actioned", note="rescanned")
        state = store.state(item.id)
        assert (state.status, state.cause, state.note) == ("actioned", "stale", "rescanned")
        [row] = store.list()
        assert row["triage"]["cause"] == "stale" and row["triage"]["status"] == "actioned"

    def test_a_rediagnosis_replaces_the_cause_and_the_history_keeps_both(self, store):
        item, _ = store.submit(answer())
        store.triage(item.id, "triaged", cause="reasoning")
        store.triage(item.id, "triaged", cause="over_compressed")
        assert store.state(item.id).cause == "over_compressed"
        assert [e.cause for e in store.history(item.id)] == ["reasoning", "over_compressed"]

    def test_an_untouched_or_status_only_item_has_no_cause(self, store):
        a, _ = store.submit(answer())
        b, _ = store.submit(answer())
        store.triage(b.id, "triaged")
        states = store.states([a.id, b.id])
        assert states[a.id].cause == "" and states[b.id].cause == ""

    def test_a_product_report_takes_a_status_but_no_cause(self, store):
        item, _ = store.submit(product())
        before = _events(store)
        with pytest.raises(FeedbackError, match="product report"):
            store.triage(item.id, "triaged", cause="reasoning")
        assert _events(store) == before
        store.triage(item.id, "triaged")          # a status alone is fine

    def test_an_external_row_takes_no_cause(self, store):
        with pytest.raises(FeedbackError, match="status only"):
            store.triage("7", "triaged", source="resource_feedback", cause="stale")

    def test_diagnosing_a_missing_item_is_not_found(self, store):
        with pytest.raises(NotFound):
            store.triage("nope", "triaged", cause="stale")

    @pytest.mark.parametrize("status, cause, secondary, words", [
        ("triaged", "flaky", "", "cause must be one of"),
        ("triaged", "", "stale", "needs a cause"),
        ("triaged", "reasoning", "stale", "must come after"),            # the earliest stage is the cause
        ("triaged", "stale", "stale", "must come after"),
        ("triaged", "stale", "unclear_question", "secondary_cause must be one of"),
        ("triaged", "unclear_question", "reasoning", "outside the cause chain"),
        ("triaged", "not_an_issue", "", "must be 'not_an_issue'"),
        ("not_an_issue", "wrong_evidence", "", "contradicts"),
    ])
    def test_a_diagnosis_that_contradicts_itself_is_refused_and_writes_nothing(self, store, status, cause,
                                                                                secondary, words):
        item, _ = store.submit(answer())
        with pytest.raises(FeedbackError, match=words):
            store.triage(item.id, status, cause=cause, secondary_cause=secondary)
        assert _events(store) == 0

    def test_a_status_only_change_must_agree_with_the_inherited_cause(self, store):
        # 0.3 read-back P2, case 1: a pipeline cause cannot stand under a not_an_issue status.
        item, _ = store.submit(answer())
        store.triage(item.id, "triaged", cause="analysis_defect")
        with pytest.raises(FeedbackError, match="re-diagnose"):
            store.triage(item.id, "not_an_issue")
        assert store.state(item.id).status == "triaged"
        store.triage(item.id, "not_an_issue", cause="not_an_issue")      # re-diagnosing with it is fine
        assert store.cause_counts()["analysis_defect"] == 0

    def test_an_item_closed_as_not_an_issue_cannot_be_reopened_without_a_rediagnosis(self, store):
        # 0.3 read-back P2, case 2.
        item, _ = store.submit(answer())
        store.triage(item.id, "not_an_issue", cause="not_an_issue")
        with pytest.raises(FeedbackError, match="re-diagnose"):
            store.triage(item.id, "actioned")
        store.triage(item.id, "actioned", cause="reasoning")
        assert (store.state(item.id).status, store.state(item.id).cause) == ("actioned", "reasoning")

    def test_a_diagnosis_needs_a_status_other_than_new(self, store):
        item, _ = store.submit(answer())
        with pytest.raises(FeedbackError, match="other than 'new'"):
            store.triage(item.id, "new", cause="stale")
        store.triage(item.id, "triaged", cause="stale")
        store.triage(item.id, "new", note="reopened")                      # reopening keeps the diagnosis
        assert store.state(item.id).cause == "stale"

    def test_a_helpful_vote_can_be_diagnosed(self, store):
        # Deliberate: a thumbs-up on a wrong answer is still a wrong answer.
        item, _ = store.submit(answer(vote=1))
        store.triage(item.id, "triaged", cause="analysis_defect")
        assert store.cause_counts(vote=1)["analysis_defect"] == 1

    def test_not_an_issue_closes_the_item(self, store):
        item, _ = store.submit(answer())
        store.triage(item.id, "not_an_issue", cause="not_an_issue")
        assert store.state(item.id).cause == "not_an_issue"

    def test_deleting_an_item_takes_its_diagnosis(self, store):
        item, _ = store.submit(answer())
        store.triage(item.id, "triaged", cause="stale")
        store.delete_item(item.id)
        assert store.cause_counts()["stale"] == 0


class TestFilterAndCounts:
    @pytest.fixture
    def seeded(self, store):
        ids = {}
        for name, vote, cause in [("a", -1, "stale"), ("b", -1, "stale"), ("c", 0, "reasoning"),
                                  ("d", -1, ""), ("e", 1, "")]:
            item, _ = store.submit(answer(vote=vote))
            ids[name] = item.id
            if cause:
                store.triage(item.id, "triaged", cause=cause)
        store.triage(ids["a"], "actioned")        # status-only: still `stale`
        ids["p"] = store.submit(product())[0].id
        return ids

    def test_list_filters_on_the_current_cause(self, store, seeded):
        assert {r["id"] for r in store.list(cause="stale")} == {seeded["a"], seeded["b"]}
        assert store.count_matching(cause="stale") == 2

    def test_undiagnosed_means_answer_items_with_no_cause_never_product_reports(self, store, seeded):
        assert {r["id"] for r in store.list(cause=UNDIAGNOSED)} == {seeded["d"], seeded["e"]}

    def test_cause_and_status_filters_combine(self, store, seeded):
        assert [r["id"] for r in store.list(cause="stale", status="actioned")] == [seeded["a"]]

    def test_an_unknown_cause_filter_is_refused(self, store):
        with pytest.raises(FeedbackError, match="cause must be one of"):
            store.list(cause="flaky")

    def test_counts_list_every_cause_with_zeros_and_the_undiagnosed(self, store, seeded):
        counts = store.cause_counts()
        assert set(counts) == {*CAUSES, UNDIAGNOSED}
        assert counts["stale"] == 2 and counts["reasoning"] == 1 and counts[UNDIAGNOSED] == 2
        assert counts["catalog_gap"] == 0
        assert sum(counts.values()) == 5           # answer items only

    def test_counts_narrow_to_one_vote(self, store, seeded):
        down = store.cause_counts(vote=-1)
        assert (down["stale"], down["reasoning"], down[UNDIAGNOSED]) == (2, 0, 1)


class TestUpgradeInPlace:
    def test_a_02_events_table_gains_the_columns_and_its_events_read_as_undiagnosed(self, make_store):
        old = make_store()
        item, _ = old.submit(answer())
        old.triage(item.id, "triaged", note="from 0.2")
        with old.engine.begin() as conn:            # make it look like a table 0.2 created
            conn.execute(text(f"ALTER TABLE {_table(old)} DROP COLUMN cause"))
            conn.execute(text(f"ALTER TABLE {_table(old)} DROP COLUMN secondary_cause"))

        new = make_store()
        state = new.state(item.id)
        assert (state.status, state.note, state.cause) == ("triaged", "from 0.2", "")
        new.triage(item.id, "triaged", cause="stale")
        assert new.state(item.id).cause == "stale"
        make_store()                                # opening it again changes nothing and raises nothing
        assert [e.cause for e in new.history(item.id)] == ["", "stale"]


@pytest.fixture
def client(store):
    app = FastAPI()
    app.include_router(make_router(store, is_admin=lambda r: r.headers.get("X-Admin") == "1",
                                   current_user=lambda r: "peterprofile"), prefix="/api/fb")
    return TestClient(app)


ADMIN = {"X-Admin": "1"}


class TestRoutes:
    def test_meta_serves_the_causes_in_order(self, client):
        meta = client.get("/api/fb/meta").json()
        assert meta["causes"] == list(CAUSES) and meta["cause_chain"] == list(CAUSE_CHAIN)
        assert meta["undiagnosed"] == UNDIAGNOSED

    def test_triage_records_a_cause_and_refuses_a_bad_one(self, client, store):
        item, _ = store.submit(answer())
        ok = client.post("/api/fb/triage", headers=ADMIN,
                         json={"ref_id": item.id, "status": "triaged", "cause": "missing_evidence"})
        assert ok.status_code == 200, ok.text
        assert ok.json()["triage"]["cause"] == "missing_evidence"
        bad = client.post("/api/fb/triage", headers=ADMIN,
                          json={"ref_id": item.id, "status": "triaged", "cause": "reasoning",
                                "secondary_cause": "stale"})
        assert bad.status_code == 400 and "must come after" in bad.json()["detail"]

    def test_list_filters_by_cause_and_counts_are_admin_only(self, client, store):
        item, _ = store.submit(answer())
        store.triage(item.id, "triaged", cause="stale")
        listed = client.get("/api/fb/items?cause=stale", headers=ADMIN).json()
        assert [r["id"] for r in listed["items"]] == [item.id] and listed["filtered"]
        assert client.get("/api/fb/items?cause=flaky", headers=ADMIN).status_code == 400
        assert client.get("/api/fb/causes").status_code == 403
        assert client.get("/api/fb/causes?vote=-1", headers=ADMIN).json()["counts"]["stale"] == 1
