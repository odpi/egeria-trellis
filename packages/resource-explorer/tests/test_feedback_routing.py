"""Closed-loop step 2a: a saved diagnosis is routed to its one consumer, a gap (BRIEF-CLOSED-LOOP-STEP2-ROUTING.md).

The owner's rules under test: route on Save; one route per (item, cause); a re-diagnosis leaves the earlier route;
a failed route never fails the diagnosis; the admin picks one charged analysis; the asker's id never reaches a gap.
And the architect's A1 (gap counts sum to the total) and A2 (a deleted item is said to be deleted).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resource_explorer.feedback_routing import _zone_restriction as REAL_ZONE_RESTRICTION
from resource_explorer.web.app import app


def manifest(**kw) -> dict:
    base = {"coverage": {"kind": "analysis", "question": "Which dependencies does it use?", "note": ""},
            "packed": [], "gaps": [], "dropped": [], "deferred": [], "declared": {}}
    base.update(kw)
    return base


ABSENT = manifest(gaps=[{"key": "cve_scan", "state": "never_run", "reason": "has not run"}],
                  packed=[{"key": "deps", "rung": "FULL"}])


@pytest.fixture(autouse=True)
def no_zone_restriction(monkeypatch):
    """The Egeria zone half of private_context (2b) reads Egeria; no test here may. A shared, unzoned resource unless a
    test says otherwise."""
    from resource_explorer import feedback_routing
    monkeypatch.setattr(feedback_routing, "_zone_restriction", lambda entity_type, slug, registry: "")


@pytest.fixture
def feedback_store(tmp_path):
    from resource_explorer.feedback_store import FeedbackStore
    return FeedbackStore(db_path=str(tmp_path / "route_feedback.db"))


@pytest.fixture
def client(monkeypatch, feedback_store, tmp_path):
    monkeypatch.setenv("REGISTRY_DATABASE_URL", f"sqlite:///{tmp_path}/registry.db")
    import resource_explorer.config as _config_module
    _config_module._config = None
    monkeypatch.setattr(
        "resource_explorer.feedback_store.FeedbackStore.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", feedback_store.__dict__) or None,
    )
    return TestClient(app)


@pytest.fixture
def admin_headers(monkeypatch):
    from resource_explorer import config as cfg_mod
    monkeypatch.setattr(cfg_mod.get_config().feedback, "admin_token", "test-token", raising=False)
    return {"X-Admin-Token": "test-token"}


@pytest.fixture
def reg(client):
    from resource_explorer.registry import Project, ProjectRegistry
    r = ProjectRegistry()
    r.add(Project(slug="r1", display_name="r1", github_url="https://github.com/x/r1"))
    return r


def _compile(reg, compile_id: str, m: dict) -> None:
    reg.record_compile(compile_id, "r1", "what does it use?", {**m, "text": "## deps\nrequests"}, [])


def _answer(store, *, slug="r1", compile_id="c1", question="what does it use?", user="peterprofile", **kw):
    item_id, _ = store.add_answer(vote=-1, question=question, compile_id=compile_id, slug=slug,
                                  entity_type="repo" if slug else "", user_id=user, **kw)
    return item_id


def _save(client, headers, item_id, cause, **kw):
    return client.patch(f"/api/feedback/{item_id}", headers=headers,
                        json={"triage_status": "triaged", "cause": cause, **kw})


class TestRouteOnSave:
    def test_missing_evidence_raises_a_gap_on_the_first_charged_analysis(self, client, reg, feedback_store,
                                                                         admin_headers):
        _compile(reg, "c1", ABSENT)
        item_id = _answer(feedback_store)
        out = _save(client, admin_headers, item_id, "missing_evidence")
        assert out.status_code == 200, out.text
        route = out.json()["route"]
        assert route["state"] == "routed" and route["consumer"] == "gap"
        gap = reg.get_gap(int(route["target"]))
        assert (gap["analysis_id"], gap["gap_kind"]) == ("cve_scan", "missing_evidence")
        assert "“what does it use?”" in gap["sentence"]          # Q8: the question as asked
        assert gap["evidence"]["feedback_items"] == [item_id] and gap["evidence"]["role"] == "absent"

    def test_the_admin_picks_another_charged_analysis(self, client, reg, feedback_store, admin_headers):
        _compile(reg, "c1", ABSENT)
        route = _save(client, admin_headers, _answer(feedback_store), "analysis_defect",
                      analysis_id="deps").json()["route"]
        gap = reg.get_gap(int(route["target"]))
        assert (gap["analysis_id"], gap["gap_kind"]) == ("deps", "analysis_defect")

    def test_an_analysis_outside_the_charge_set_is_refused_before_anything_is_written(
            self, client, reg, feedback_store, admin_headers):
        _compile(reg, "c1", ABSENT)
        item_id = _answer(feedback_store)
        bad = _save(client, admin_headers, item_id, "missing_evidence", analysis_id="stars")
        assert bad.status_code == 400 and "not in this answer's charge set" in bad.json()["detail"]
        assert feedback_store.get(item_id)["triage"]["cause"] == ""          # no diagnosis written
        assert reg.list_feedback_routes([item_id]) == {}

    def test_the_asker_id_never_reaches_the_gap(self, client, reg, feedback_store, admin_headers):
        _compile(reg, "c1", ABSENT)
        route = _save(client, admin_headers, _answer(feedback_store, user="peterprofile"),
                      "missing_evidence").json()["route"]
        assert "peterprofile" not in repr(reg.get_gap(int(route["target"])))

    def test_a_catalog_gap_is_a_gap_on_no_analysis(self, client, reg, feedback_store, admin_headers):
        _compile(reg, "c1", manifest(coverage={"kind": "none", "question": "", "note": ""}))
        route = _save(client, admin_headers, _answer(feedback_store), "catalog_gap").json()["route"]
        gap = reg.get_gap(int(route["target"]))
        assert (gap["analysis_id"], gap["gap_kind"]) == ("", "catalog_gap")


class TestWhatRoutesNothing:
    @pytest.mark.parametrize("cause", ["wrong_evidence", "over_compressed", "reasoning", "not_an_issue"])
    def test_causes_without_a_step_2_consumer_route_nothing(self, client, reg, feedback_store, admin_headers,
                                                             cause):
        _compile(reg, "c1", ABSENT)
        item_id = _answer(feedback_store)
        status = "not_an_issue" if cause == "not_an_issue" else "triaged"
        out = client.patch(f"/api/feedback/{item_id}", headers=admin_headers,
                           json={"triage_status": status, "cause": cause})
        assert out.status_code == 200, out.text
        assert out.json()["route"]["state"] == "skipped"
        assert reg.list_feedback_routes([item_id]) == {} and reg.list_gaps("r1") == []

    def test_a_status_change_alone_routes_nothing(self, client, reg, feedback_store, admin_headers):
        _compile(reg, "c1", ABSENT)
        item_id = _answer(feedback_store)
        out = client.patch(f"/api/feedback/{item_id}", headers=admin_headers, json={"triage_status": "triaged"})
        assert "route" not in out.json() and reg.list_gaps("r1") == []

    def test_the_secondary_cause_never_routes(self, client, reg, feedback_store, admin_headers):
        _compile(reg, "c1", ABSENT)
        item_id = _answer(feedback_store)
        _save(client, admin_headers, item_id, "reasoning", secondary_cause="missing_evidence")
        assert reg.list_gaps("r1") == []


class TestOnceAndAgain:
    def test_saving_the_same_cause_twice_routes_once(self, client, reg, feedback_store, admin_headers):
        _compile(reg, "c1", ABSENT)
        item_id = _answer(feedback_store)
        first = _save(client, admin_headers, item_id, "missing_evidence").json()["route"]
        client.patch(f"/api/feedback/{item_id}", headers=admin_headers,
                     json={"triage_status": "triaged", "cause": "reasoning"})
        again = _save(client, admin_headers, item_id, "missing_evidence").json()["route"]
        assert again["state"] == "already" and again["target"] == first["target"]
        assert len(reg.list_gaps("r1")) == 1

    def test_a_re_diagnosis_routes_the_new_cause_and_leaves_the_old_route(self, client, reg, feedback_store,
                                                                         admin_headers):
        _compile(reg, "c1", ABSENT)
        item_id = _answer(feedback_store)
        first = _save(client, admin_headers, item_id, "missing_evidence").json()["route"]
        second = _save(client, admin_headers, item_id, "analysis_defect").json()["route"]
        assert second["state"] == "routed" and second["target"] != first["target"]
        assert reg.get_gap(int(first["target"]))["resolved_at"] is None
        routes = client.get(f"/api/feedback/{item_id}", headers=admin_headers).json()["routes"]
        assert [r["cause"] for r in routes] == ["missing_evidence", "analysis_defect"]

    def test_routing_back_to_a_resolved_route_says_so(self, client, reg, feedback_store, admin_headers):
        _compile(reg, "c1", ABSENT)
        item_id = _answer(feedback_store)
        first = _save(client, admin_headers, item_id, "missing_evidence").json()["route"]
        with reg._conn() as c:
            c.execute("UPDATE analysis_gaps SET resolved_at = '2026-11-10T00:00:00' WHERE id = ?",
                      (int(first["target"]),))
        _save(client, admin_headers, item_id, "reasoning")
        again = _save(client, admin_headers, item_id, "missing_evidence").json()["route"]
        assert again["state"] == "already" and again["words"].endswith(", resolved.")

    def test_two_answers_on_one_question_make_one_gap_listing_both(self, client, reg, feedback_store,
                                                                    admin_headers):
        _compile(reg, "c1", ABSENT)
        a, b = _answer(feedback_store), _answer(feedback_store, user="erinoverview")
        ra = _save(client, admin_headers, a, "missing_evidence").json()["route"]
        rb = _save(client, admin_headers, b, "missing_evidence").json()["route"]
        assert ra["target"] == rb["target"]
        assert reg.get_gap(int(ra["target"]))["evidence"]["feedback_items"] == [a, b]

    def test_the_claim_is_taken_once(self, reg):
        assert reg.claim_feedback_route("i1", "missing_evidence", "admin") is True
        assert reg.claim_feedback_route("i1", "missing_evidence", "admin") is False


class TestAFailedRouteNeverFailsTheDiagnosis:
    def test_no_resource_is_recorded_as_not_routed(self, client, reg, feedback_store, admin_headers):
        item_id = _answer(feedback_store, slug="", compile_id="")
        out = _save(client, admin_headers, item_id, "missing_evidence")
        assert out.status_code == 200 and out.json()["triage"]["cause"] == "missing_evidence"
        route = out.json()["route"]
        assert route["state"] == "not_routed" and "no resource in scope" in route["words"]

    def test_no_compile_says_nothing_to_charge(self, client, reg, feedback_store, admin_headers):
        route = _save(client, admin_headers, _answer(feedback_store, compile_id=""),
                      "missing_evidence").json()["route"]
        assert route["state"] == "not_routed" and "nothing to charge" in route["words"]

    def test_a_resource_that_cannot_hold_gaps_is_not_routed_with_why(self, client, reg, feedback_store,
                                                                     admin_headers):
        # Gaps are keyed to the projects table: a slug it does not hold (today, any non-repository) cannot hold one.
        reg.record_compile("c9", "db1", "q?", {**ABSENT, "text": ""}, [])
        route = _save(client, admin_headers, _answer(feedback_store, slug="db1", compile_id="c9"),
                      "missing_evidence").json()["route"]
        assert route["state"] == "not_routed" and "no gap could be raised" in route["words"]

    def test_a_passing_failure_is_released_so_the_next_save_retries(self, client, reg, feedback_store,
                                                                    admin_headers, monkeypatch):
        _compile(reg, "c1", ABSENT)
        item_id = _answer(feedback_store)
        from resource_explorer import feedback_routing

        def broken(*a, **kw):
            raise RuntimeError("registry unavailable")
        with monkeypatch.context() as m:           # scoped: undo() would also undo the fixtures' patches
            m.setattr(feedback_routing, "_raise_gap", broken)
            out = _save(client, admin_headers, item_id, "missing_evidence")
        assert out.status_code == 200 and out.json()["route"]["state"] == "failed"
        assert reg.list_feedback_routes([item_id]) == {}
        _save(client, admin_headers, item_id, "reasoning")
        assert _save(client, admin_headers, item_id, "missing_evidence").json()["route"]["state"] == "routed"


class TestTheChecklistDisagreement:
    def test_both_gaps_stay_and_link_both_ways(self, client, reg, feedback_store, admin_headers):
        from resource_explorer.gaps import record_disagreement
        _compile(reg, "c1", ABSENT)
        disputed = record_disagreement(reg, "r1", "what does it use?", "cve_scan")
        [d_row] = [g for g in reg.list_gaps("r1") if g["gap_kind"] == "disagreement"]
        route = _save(client, admin_headers, _answer(feedback_store), "missing_evidence").json()["route"]
        diagnosed = reg.get_gap(int(route["target"]))
        assert diagnosed["evidence"]["disagreement_gap"] == d_row["id"]
        assert reg.get_gap(d_row["id"])["evidence"]["diagnosed_as"] == [diagnosed["id"]]
        assert reg.get_gap(d_row["id"])["last_seen_at"] == d_row["last_seen_at"]   # linking is not a sighting
        assert disputed["gap_kind"] == "disagreement"


class TestGapsSummary:
    def test_buckets_sum_to_the_total_and_disputed_counts_disagreements_only(self, client, reg, feedback_store,
                                                                             admin_headers):
        from resource_explorer.gaps import GAP_KINDS, gaps_summary, record_disagreement
        _compile(reg, "c1", ABSENT)
        record_disagreement(reg, "r1", "what does it use?", "cve_scan")
        _save(client, admin_headers, _answer(feedback_store), "missing_evidence")
        _save(client, admin_headers, _answer(feedback_store, question="other?"), "catalog_gap")
        counts = gaps_summary(reg, "r1")["counts"]
        assert sum(counts[k] for k in GAP_KINDS) == counts["total"] == 3
        assert counts["disputed_by_a_person"] == 1                         # the disagreement, not the diagnoses

    def test_a_deleted_item_is_said_to_be_deleted(self, client, reg, feedback_store, admin_headers):
        from resource_explorer.gaps import gaps_summary
        _compile(reg, "c1", ABSENT)
        item_id = _answer(feedback_store)
        _save(client, admin_headers, item_id, "missing_evidence")
        feedback_store.store.delete_item(item_id)
        reg.delete_feedback_routes(item_id)
        [gap] = gaps_summary(reg, "r1")["gaps"]
        assert gap["evidence"]["feedback_items_deleted"] == [item_id]


def test_re_offers_no_way_to_delete_an_answer_item_yet():
    """A2: deleting an item must also delete its routes (`registry.delete_feedback_routes`). RE has no delete path
    for library items today. If one is added to FeedbackStore, this fails so the route cleanup goes with it."""
    from resource_explorer.feedback_store import FeedbackStore
    deleters = [n for n in dir(FeedbackStore) if "delete" in n and "event" not in n and not n.startswith("_")]
    assert deleters == [], f"{deleters}: make it call registry.delete_feedback_routes, then update this test"


class TestTheAdminListingCarriesRoutes:
    def test_each_answer_row_lists_its_routes(self, client, reg, feedback_store, admin_headers):
        _compile(reg, "c1", ABSENT)
        item_id = _answer(feedback_store)
        _save(client, admin_headers, item_id, "missing_evidence")
        rows = client.get("/api/curate/feedback?source=answer", headers=admin_headers).json()["feedback"]
        [row] = [r for r in rows if r["id"] == item_id]
        assert row["routes"][0]["consumer"] == "gap" and row["routes"][0]["words"].startswith("Routed: gap #")


def test_the_claim_is_taken_once_on_postgres(pg_registry):
    """The route's lock is `INSERT ... ON CONFLICT (item_id, cause) DO NOTHING` and its row count: checked on the
    real dialect, where the shared registry runs."""
    assert pg_registry.claim_feedback_route("pg-i1", "missing_evidence", "admin") is True
    assert pg_registry.claim_feedback_route("pg-i1", "missing_evidence", "admin") is False
    pg_registry.finish_feedback_route("pg-i1", "missing_evidence", "none", reason="no resource in scope")
    assert pg_registry.list_feedback_routes(["pg-i1"])["pg-i1"][0]["reason"] == "no resource in scope"
    assert pg_registry.delete_feedback_routes("pg-i1") == 1


class TestRaisingAnRfaForADiagnosedGap:
    """projects.py's gap→RFA route knew only the two measured kinds; a diagnosed gap reads as what it is."""

    def _rfa_detail(self, reg, rfa_id):
        with reg._conn() as c:
            rows = c.execute("SELECT detail, annotations_json FROM activity_log WHERE id = ?", (rfa_id,)).fetchall()
        return " ".join(str(r["detail"]) + str(r["annotations_json"]) for r in rows)

    def test_a_routed_catalog_gap_already_has_its_rfa(self, client, reg, feedback_store, admin_headers):
        # Since 2b, routing raises a catalog gap's RFA and stamps it on the gap; the manual route refuses a second.
        _compile(reg, "c1", manifest(coverage={"kind": "none", "question": "", "note": ""}))
        gap_id = int(_save(client, admin_headers, _answer(feedback_store), "catalog_gap").json()["route"]["target"])
        assert client.post(f"/api/projects/r1/gaps/{gap_id}/rfa").status_code == 409

    def test_a_catalog_gap_names_the_question_catalog(self, client, reg, feedback_store, admin_headers):
        from resource_explorer.gaps import CATALOG_GAP, SOURCE_PERSON
        gap_id = reg.upsert_gap("r1", "", CATALOG_GAP, "question:Q-9", "nothing answers it",
                                evidence={"source": SOURCE_PERSON})
        out = client.post(f"/api/projects/r1/gaps/{gap_id}/rfa")
        assert out.status_code == 200, out.text
        detail = self._rfa_detail(reg, out.json()["rfa"])
        assert "a question nothing in the catalog answers, in the question catalog" in detail
        assert "'' analysis" not in detail

    def test_a_missing_evidence_gap_names_its_analysis(self, client, reg, feedback_store, admin_headers):
        _compile(reg, "c1", ABSENT)
        gap_id = int(_save(client, admin_headers, _answer(feedback_store), "missing_evidence").json()["route"]["target"])
        detail = self._rfa_detail(reg, client.post(f"/api/projects/r1/gaps/{gap_id}/rfa").json()["rfa"])
        assert "missing this analysis's result, in the 'cve_scan' analysis" in detail


class TestAPrivateContextKeepsTheQuestionInAdmin:
    """The architect's P1 on 6c050de2: GET /api/projects/{slug}/gaps is not filtered per viewer, so a diagnosis of an
    answer about a resource in a private investigation names the item and the cause only, never the question."""

    def _private(self, monkeypatch, owner="erinoverview"):
        from resource_explorer.registry import ProjectRegistry
        monkeypatch.setattr(ProjectRegistry, "private_owner_for_entity", lambda self, et, slug: owner)

    def test_the_gap_carries_no_question_text_anywhere(self, client, reg, feedback_store, admin_headers,
                                                       monkeypatch):
        self._private(monkeypatch)
        _compile(reg, "c1", ABSENT)
        item_id = _answer(feedback_store, question="what does Erin's secret project use?")
        gap = reg.get_gap(int(_save(client, admin_headers, item_id, "missing_evidence").json()["route"]["target"]))
        assert "secret project" not in repr(gap) and "question" not in gap["evidence"]
        assert gap["check_name"].startswith("question:sha1:") and item_id in gap["sentence"]
        assert gap["evidence"]["private_context"] == "the resource is in a private investigation"

    def test_a_catalog_id_keys_the_private_gap_without_text(self, client, reg, feedback_store, admin_headers,
                                                            monkeypatch):
        self._private(monkeypatch)
        _compile(reg, "c1", ABSENT)
        item_id = _answer(feedback_store, extra={"question_id": "Q-0042"})
        gap = reg.get_gap(int(_save(client, admin_headers, item_id, "missing_evidence").json()["route"]["target"]))
        assert gap["check_name"] == "question:Q-0042"

    def test_unreadable_privacy_fails_closed(self, client, reg, feedback_store, admin_headers, monkeypatch):
        from resource_explorer.registry import ProjectRegistry

        def unreadable(self, et, slug):
            raise RuntimeError("registry down")
        monkeypatch.setattr(ProjectRegistry, "private_owner_for_entity", unreadable)
        _compile(reg, "c1", ABSENT)
        gap = reg.get_gap(int(_save(client, admin_headers, _answer(feedback_store), "missing_evidence")
                              .json()["route"]["target"]))
        assert "what does it use?" not in repr(gap) and "could not be read" in gap["evidence"]["private_context"]

    def test_a_shared_resource_keeps_the_question(self, client, reg, feedback_store, admin_headers, monkeypatch):
        self._private(monkeypatch, owner="")
        _compile(reg, "c1", ABSENT)
        gap = reg.get_gap(int(_save(client, admin_headers, _answer(feedback_store), "missing_evidence")
                              .json()["route"]["target"]))
        assert gap["evidence"]["question"] == "what does it use?" and "private_context" not in gap["evidence"]

    def test_the_opaque_key_is_stable_and_textless(self):
        from resource_explorer.feedback_routing import opaque_question_key
        assert opaque_question_key("what?") == opaque_question_key(" what? ")
        assert "what" not in opaque_question_key("what?")
        assert opaque_question_key("what?", "Q-1") == "question:Q-1"


class TestReroute:
    """The architect's P2: a Save keyed on (item, cause) cannot move a default charge, so re-routing is its own,
    explicit action."""

    def test_reroute_moves_the_item_to_another_charged_analysis(self, client, reg, feedback_store, admin_headers):
        _compile(reg, "c1", ABSENT)
        item_id = _answer(feedback_store)
        first = _save(client, admin_headers, item_id, "missing_evidence").json()["route"]
        out = client.post(f"/api/feedback/{item_id}/routes/missing_evidence/reroute", headers=admin_headers,
                          json={"analysis_id": "deps"})
        assert out.status_code == 200, out.text
        moved = out.json()
        assert moved["state"] == "routed" and moved["rerouted_from"] == first["target"]
        assert reg.get_gap(int(moved["target"]))["analysis_id"] == "deps"
        old = reg.get_gap(int(first["target"]))
        assert old["resolved_at"] is None and old["evidence"]["feedback_items"] == []
        assert old["evidence"]["rerouted_items"] == [item_id]

    def test_reroute_refuses_what_it_cannot_do(self, client, reg, feedback_store, admin_headers):
        _compile(reg, "c1", ABSENT)
        item_id = _answer(feedback_store)
        url = f"/api/feedback/{item_id}/routes/missing_evidence/reroute"
        assert client.post(url, headers=admin_headers, json={"analysis_id": "deps"}).status_code == 400  # no route yet
        _save(client, admin_headers, item_id, "missing_evidence")
        bad = client.post(url, headers=admin_headers, json={"analysis_id": "stars"})
        assert bad.status_code == 400 and "charge set" in bad.json()["detail"]
        assert client.post(f"/api/feedback/{item_id}/routes/reasoning/reroute", headers=admin_headers,
                           json={}).status_code == 400
        assert client.post(url, json={"analysis_id": "deps"}).status_code == 403


# ── step 2b: the RFA consumers ──────────────────────────────────────────────────────────────────────────────────

def _rfa(reg, rfa_id):
    entry = reg.get_activity(rfa_id)
    assert entry is not None, rfa_id
    return entry


class TestRfaRouting:
    def test_unclear_question_raises_an_unassigned_rfa_with_the_question_and_the_note(
            self, client, reg, feedback_store, admin_headers):
        _compile(reg, "c1", ABSENT)
        item_id = _answer(feedback_store, question="what does 'pkgx' mean?")
        out = client.patch(f"/api/feedback/{item_id}", headers=admin_headers,
                           json={"triage_status": "triaged", "cause": "unclear_question", "note": "pkgx is an alias"})
        route = out.json()["route"]
        assert route["state"] == "routed" and route["consumer"] == "rfa"
        entry = _rfa(reg, route["target"])
        assert (entry["entity_type"], entry["entity_slug"], entry["operation"]) == ("repo", "r1", "rfa")
        assert "“what does 'pkgx' mean?”" in entry["summary"] and "Curator note: pkgx is an alias" in entry["detail"]
        assert {"kind": "feedback", "id": item_id} in entry["items"]
        assert reg.list_gaps("r1") == []                                    # an RFA only
        assert "peterprofile" not in repr(entry)
        rows = client.get("/api/activity/rfas").json()
        [row] = [r for r in rows if r["entry_id"] == route["target"]]
        assert row["assignee"] == "" and row["rfa_status"] == "open"         # unassigned (Q5)

    def test_a_catalog_gap_raises_a_gap_and_an_rfa_stamped_on_it(self, client, reg, feedback_store, admin_headers):
        _compile(reg, "c1", manifest(coverage={"kind": "none", "question": "", "note": ""}))
        route = _save(client, admin_headers, _answer(feedback_store), "catalog_gap").json()["route"]
        gap = reg.get_gap(int(route["target"]))
        assert gap["gap_kind"] == "catalog_gap" and gap["rfa_activity_id"]
        assert ", RFA " in route["words"]
        assert {"kind": "gap", "gap_id": gap["id"]} in _rfa(reg, gap["rfa_activity_id"])["items"]

    def test_with_no_resource_the_rfa_goes_to_the_question_catalog(self, client, reg, feedback_store, admin_headers):
        item_id = _answer(feedback_store, slug="", compile_id="")
        route = _save(client, admin_headers, item_id, "catalog_gap").json()["route"]
        assert route["consumer"] == "rfa"
        entry = _rfa(reg, route["target"])
        assert (entry["entity_type"], entry["entity_slug"], entry["entity_name"]) == ("catalog", "questions",
                                                                                     "Question catalog")
        rows = client.get("/api/activity/rfas").json()
        assert any(r["entry_id"] == route["target"] and r["entity_name"] == "Question catalog" for r in rows)

    def test_a_second_answer_on_the_question_joins_the_rfa(self, client, reg, feedback_store, admin_headers):
        _compile(reg, "c1", ABSENT)
        a, b = _answer(feedback_store), _answer(feedback_store, user="erinoverview")
        ra = _save(client, admin_headers, a, "unclear_question").json()["route"]
        rb = _save(client, admin_headers, b, "unclear_question").json()["route"]
        assert ra["target"] == rb["target"]
        items = _rfa(reg, ra["target"])["items"]
        assert {"kind": "feedback", "id": a} in items and {"kind": "feedback", "id": b} in items

    def test_the_rfa_claim_is_taken_once(self, reg):
        assert reg.claim_feedback_rfa("question:Q-1", "unclear_question", "repo", "r1") is True
        assert reg.claim_feedback_rfa("question:Q-1", "unclear_question", "repo", "r1") is False

    def test_a_failed_rfa_is_released_so_the_next_save_raises_it(self, client, reg, feedback_store, admin_headers,
                                                                 monkeypatch):
        _compile(reg, "c1", ABSENT)
        item_id = _answer(feedback_store)

        def broken(*a, **kw):
            raise RuntimeError("activity log unavailable")
        with monkeypatch.context() as m:
            m.setattr("resource_explorer.activity_logger.log_rfa", broken)
            out = _save(client, admin_headers, item_id, "unclear_question")
        assert out.json()["route"]["state"] == "failed"
        assert reg.list_feedback_routes([item_id]) == {}
        _save(client, admin_headers, item_id, "reasoning")
        assert _save(client, admin_headers, item_id, "unclear_question").json()["route"]["state"] == "routed"

    def test_an_rfa_cause_takes_no_analysis(self, client, reg, feedback_store, admin_headers):
        _compile(reg, "c1", ABSENT)
        bad = _save(client, admin_headers, _answer(feedback_store), "unclear_question", analysis_id="deps")
        assert bad.status_code == 400


class TestTheRfaKeepsAPrivateQuestionInAdmin:
    def test_a_private_investigation_keeps_the_question_and_the_note_out(self, client, reg, feedback_store,
                                                                         admin_headers, monkeypatch):
        from resource_explorer.registry import ProjectRegistry
        monkeypatch.setattr(ProjectRegistry, "private_owner_for_entity", lambda self, et, slug: "erinoverview")
        _compile(reg, "c1", ABSENT)
        item_id = _answer(feedback_store, question="what does Erin's secret project use?")
        route = client.patch(f"/api/feedback/{item_id}", headers=admin_headers,
                             json={"triage_status": "triaged", "cause": "unclear_question",
                                   "note": "the secret project's alias"}).json()["route"]
        entry = _rfa(reg, route["target"])
        assert "secret project" not in repr(entry) and item_id in entry["summary"]

    def test_a_restricted_zone_is_private(self, client, reg, feedback_store, admin_headers, monkeypatch):
        from resource_explorer import feedback_routing
        monkeypatch.setattr(feedback_routing, "_zone_restriction",
                            lambda et, slug, r: "the resource is in a restricted zone (security)")
        _compile(reg, "c1", ABSENT)
        route = _save(client, admin_headers, _answer(feedback_store, question="what is in the vault?"),
                      "unclear_question").json()["route"]
        entry = _rfa(reg, route["target"])
        assert "vault" not in repr(entry) and "restricted zone (security)" in entry["summary"]


class TestTheZoneRead:
    """`_zone_restriction` itself, with the Egeria reader faked: READ restricted, not restricted, and fail closed."""

    def _with(self, monkeypatch, *, zones=None, control=None, element_error=None):
        from resource_explorer import zone_access

        class Reader:
            def __init__(self, **kw):
                pass

            def element(self, guid):
                if element_error:
                    raise element_error
                return zone_access.ElementFacts(zones=zones or [], owners=None, maintainers=None)

            def control(self, zone):
                if isinstance(control, Exception):
                    raise control
                return (control or {}).get(zone)
        monkeypatch.setattr(zone_access, "EgeriaAccessReader", Reader)

    def _registry(self, guid="g-1"):
        class Repo:
            egeria_asset_guid = guid

        class R:
            def get(self, slug):
                return Repo()
            get_database = get_filesystem = get
        return R()

    def _call(self, registry):
        return REAL_ZONE_RESTRICTION("repo", "r1", registry)     # the real one, captured before the autouse stub

    def test_a_zone_restricting_read_is_restricted(self, monkeypatch):
        self._with(monkeypatch, zones=["security"],
                   control={"security": {"associatedSecurityList": {"READ": ["security-team"]}}})
        assert self._call(self._registry()) == "the resource is in a restricted zone (security)"

    def test_a_zone_securing_only_updates_is_restricted(self, monkeypatch):
        """Was `..._is_not_restricted`. Egeria b49c5ed0: a control with neither a READ nor a DEFAULT list "grants
        access to no-one" for READ (validateZoneAccess :1156-1161), so the zone is restricted; the zone has a
        control, so it is configuration and is named."""
        self._with(monkeypatch, zones=["data-lake"],
                   control={"data-lake": {"associatedSecurityList": {"UPDATE_PROPERTIES": ["stewards"]}}})
        assert self._call(self._registry()) == "the resource is in a restricted zone (data-lake)"

    def test_an_unpublished_resource_has_no_zones(self, monkeypatch):
        self._with(monkeypatch, zones=["security"])
        assert self._call(self._registry(guid="")) == ""

    def test_an_unreadable_element_fails_closed(self, monkeypatch):
        from resource_explorer.zone_access import AccessUnreadable
        self._with(monkeypatch, element_error=AccessUnreadable("no answer"))
        assert "could not be read" in self._call(self._registry())


def test_the_rfa_claim_is_taken_once_on_postgres(pg_registry):
    """One RFA per (question, cause, entity): the `feedback_rfas` key and the insert's row count, on the real dialect."""
    key = ("question:sha1:abc", "unclear_question", "catalog", "questions")
    assert pg_registry.claim_feedback_rfa(*key) is True
    assert pg_registry.claim_feedback_rfa(*key) is False
    assert pg_registry.get_feedback_rfa(*key) == "pending"
    pg_registry.set_feedback_rfa(*key, "rfa-1")
    assert pg_registry.get_feedback_rfa(*key) == "rfa-1"
    pg_registry.release_feedback_rfa(*key)                  # only a pending claim is released
    assert pg_registry.get_feedback_rfa(*key) == "rfa-1"


def test_joins_at_the_same_moment_both_land_on_postgres(pg_registry):
    """The architect's read-back of 4b522bc5: a later answer joining an RFA is a locked read-modify-write on Postgres,
    so many joins at once each keep their pointer."""
    from concurrent.futures import ThreadPoolExecutor

    from resource_explorer.activity_logger import log_rfa

    rfa_id = log_rfa(pg_registry, "catalog", "questions", "Question catalog", "open", "an unclear question")
    pointers = [{"kind": "feedback", "id": f"item-{n}"} for n in range(12)]
    with ThreadPoolExecutor(max_workers=12) as pool:
        assert all(pool.map(lambda p: pg_registry.add_activity_item(rfa_id, p), pointers))
    items = pg_registry.get_activity(rfa_id)["items"]
    assert all(p in items for p in pointers), f"lost: {[p for p in pointers if p not in items]}"


# ── step 2c: Automate's schedule proposal ───────────────────────────────────────────────────────────────────────

STALE = manifest(packed=[{"key": "deps", "rung": "FULL"}, {"key": "licenses", "rung": "FULL"}],
                 declared={"deps": {"as_of": "2026-11-01T00:00:00+00:00"},
                           "licenses": {"as_of": "2026-01-01T00:00:00+00:00"}})


def _stale_answer(store):
    item_id, _ = store.add_answer(vote=-1, question="what licences?", compile_id="c1", slug="r1",
                                  entity_type="repo", user_id="peterprofile")
    # The vote's time is what the stale rule measures from (feedback_diagnosis).
    return item_id


class TestAutomateProposal:
    def test_stale_proposes_the_stale_section_s_analysis_with_an_offer(self, client, reg, feedback_store,
                                                                        admin_headers):
        _compile(reg, "c1", STALE)
        item_id = _stale_answer(feedback_store)
        route = _save(client, admin_headers, item_id, "stale").json()["route"]
        assert route["state"] == "routed" and route["consumer"] == "automate"
        entry = _rfa(reg, route["target"])
        assert entry["summary"].startswith("Schedule licenses weekly on r1")    # the stale one, not the first packed
        assert "what licences?" not in repr(entry) and "peterprofile" not in repr(entry)
        [row] = [r for r in client.get("/api/activity/rfas").json() if r["entry_id"] == route["target"]]
        assert row["offer"] == {"kind": "schedule", "entity_type": "repo", "entity_slug": "r1",
                                "analysis_id": "licenses", "cadence": "weekly"}

    def test_the_admin_may_pick_another_charged_analysis(self, client, reg, feedback_store, admin_headers):
        _compile(reg, "c1", STALE)
        route = _save(client, admin_headers, _stale_answer(feedback_store), "stale", analysis_id="deps").json()["route"]
        assert _rfa(reg, route["target"])["summary"].startswith("Schedule deps weekly")

    def test_an_analysis_already_scheduled_is_offered_the_next_faster_cadence(self, client, reg, feedback_store,
                                                                            admin_headers):
        _compile(reg, "c1", STALE)
        reg.save_schedule(entity_type="repo", entity_slug="r1", analysis_id="licenses", schedule="monthly",
                          enabled=True)
        route = _save(client, admin_headers, _stale_answer(feedback_store), "stale").json()["route"]
        entry = _rfa(reg, route["target"])
        assert entry["summary"].startswith("Schedule licenses weekly") and "already scheduled monthly" in entry["detail"]

    def test_already_daily_and_still_stale_is_a_defect_gap_not_an_rfa(self, client, reg, feedback_store,
                                                                       admin_headers):
        # The architect's read-back of 0969661a: at daily, a faster schedule is not the fix; the run or the result's
        # path to the compile is, which is the analysis owner's.
        _compile(reg, "c1", STALE)
        reg.save_schedule(entity_type="repo", entity_slug="r1", analysis_id="licenses", schedule="daily", enabled=True)
        route = _save(client, admin_headers, _stale_answer(feedback_store), "stale").json()["route"]
        assert route["consumer"] == "gap"
        gap = reg.get_gap(int(route["target"]))
        assert (gap["analysis_id"], gap["gap_kind"]) == ("licenses", "analysis_defect")
        assert not [r for r in client.get("/api/activity/rfas").json()
                    if r["analysis_name"] == "Automate: schedule proposal"]

    def test_a_later_stale_answer_escalates_past_a_completed_proposal(self, client, reg, feedback_store,
                                                                      admin_headers):
        # R2: the claim is keyed by cadence, so once weekly is in place the next proposal offers daily.
        _compile(reg, "c1", STALE)
        first = _save(client, admin_headers, _stale_answer(feedback_store), "stale").json()["route"]
        reg.save_schedule(entity_type="repo", entity_slug="r1", analysis_id="licenses", schedule="weekly", enabled=True)
        second = _save(client, admin_headers, _stale_answer(feedback_store), "stale").json()["route"]
        assert second["target"] != first["target"]
        assert _rfa(reg, second["target"])["summary"].startswith("Schedule licenses daily")

    def test_one_proposal_per_resource_and_analysis(self, client, reg, feedback_store, admin_headers):
        _compile(reg, "c1", STALE)
        a, b = _stale_answer(feedback_store), _stale_answer(feedback_store)
        ra = _save(client, admin_headers, a, "stale").json()["route"]
        rb = _save(client, admin_headers, b, "stale").json()["route"]
        assert ra["target"] == rb["target"]
        items = _rfa(reg, ra["target"])["items"]
        assert {"kind": "feedback", "id": a} in items and {"kind": "feedback", "id": b} in items

    def test_no_resource_is_not_routed(self, client, reg, feedback_store, admin_headers):
        route = _save(client, admin_headers, _answer(feedback_store, slug="", compile_id=""), "stale").json()["route"]
        assert route["state"] == "not_routed" and "nothing to schedule" in route["words"]

    def test_the_offer_saves_through_the_schedule_route(self, client, reg, feedback_store, admin_headers,
                                                        monkeypatch):
        """What the drawer's button posts is what the per-card control posts; the schedule then exists."""
        _compile(reg, "c1", STALE)
        route = _save(client, admin_headers, _stale_answer(feedback_store), "stale").json()["route"]
        [row] = [r for r in client.get("/api/activity/rfas").json() if r["entry_id"] == route["target"]]
        o = row["offer"]
        monkeypatch.setattr("resource_explorer.web.routes.curate.require_resource_write", lambda *a, **kw: None)
        out = client.post(f"/api/schedules/{o['entity_type']}/{o['entity_slug']}",
                          json={"analysis_id": o["analysis_id"], "schedule": o["cadence"], "enabled": True})
        assert out.status_code == 200, out.text
        assert any(s["analysis_id"] == "licenses" and s["schedule"] == "weekly" for s in reg.get_schedules("repo", "r1"))


class TestAnOfferNeverSlowsASchedule:
    """R1: the drawer saves with only_if_faster; a schedule already at or faster than the offer is left alone."""

    def _post(self, client, monkeypatch, cadence):
        monkeypatch.setattr("resource_explorer.web.routes.curate.require_resource_write", lambda *a, **kw: None)
        return client.post("/api/schedules/repo/r1", json={"analysis_id": "licenses", "schedule": cadence,
                                                           "enabled": True, "only_if_faster": True}).json()

    def test_a_faster_schedule_is_left_as_it_is(self, client, reg, monkeypatch):
        reg.save_schedule(entity_type="repo", entity_slug="r1", analysis_id="licenses", schedule="daily", enabled=True)
        out = self._post(client, monkeypatch, "weekly")
        assert out["status"] == "unchanged" and "already scheduled daily" in out["reason"]
        assert [s["schedule"] for s in reg.get_schedules("repo", "r1")] == ["daily"]

    def test_the_same_cadence_is_left_as_it_is(self, client, reg, monkeypatch):
        reg.save_schedule(entity_type="repo", entity_slug="r1", analysis_id="licenses", schedule="weekly", enabled=True)
        assert self._post(client, monkeypatch, "weekly")["status"] == "unchanged"

    def test_a_slower_or_missing_schedule_is_sped_up(self, client, reg, monkeypatch):
        assert self._post(client, monkeypatch, "weekly")["status"] == "ok"
        assert self._post(client, monkeypatch, "daily")["status"] == "ok"
        assert [s["schedule"] for s in reg.get_schedules("repo", "r1")] == ["daily"]

    def test_a_disabled_schedule_counts_as_none(self, client, reg, monkeypatch):
        reg.save_schedule(entity_type="repo", entity_slug="r1", analysis_id="licenses", schedule="daily", enabled=False)
        assert self._post(client, monkeypatch, "weekly")["status"] == "ok"


def test_a_value_error_outside_the_defect_gap_is_a_passing_failure(client, reg, feedback_store, admin_headers,
                                                                    monkeypatch):
    """The architect's nit on 190d7aa1: only the defect gap's refusal reads as "already daily, no gap". A ValueError
    from writing the proposal is released and retried like any other passing failure."""
    _compile(reg, "c1", STALE)
    item_id = _stale_answer(feedback_store)

    def broken(*a, **kw):
        raise ValueError("activity log rejected the row")
    with monkeypatch.context() as m:
        m.setattr("resource_explorer.activity_logger.log_rfa", broken)
        route = _save(client, admin_headers, item_id, "stale").json()["route"]
    assert route["state"] == "failed" and "already runs daily" not in route["words"]
    assert reg.list_feedback_routes([item_id]) == {}


def test_a_daily_analysis_on_a_resource_without_gaps_is_not_routed(client, reg, feedback_store, admin_headers):
    reg.record_compile("c9", "db1", "q?", {**STALE, "text": ""}, [])
    reg.save_schedule(entity_type="database", entity_slug="db1", analysis_id="licenses", schedule="daily", enabled=True)
    item_id, _ = feedback_store.add_answer(vote=-1, question="q?", compile_id="c9", slug="db1", entity_type="database")
    route = _save(client, admin_headers, item_id, "stale").json()["route"]
    assert route["state"] == "not_routed" and "already runs daily" in route["words"]
