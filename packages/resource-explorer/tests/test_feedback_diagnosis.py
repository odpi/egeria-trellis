"""Closed-loop step 1 in RE: a cause on a rated answer, the evidence behind it, and the counts by cause.

docs/feedback-closed-loop-design.md §2.3 (suggestions, each with its rule), §3.1 (the stored compile, never re-compiled;
the charge set) and §8 Q2/Q6 (admins diagnose; the by-cause view is part of the step).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resource_explorer.feedback_diagnosis import charge_set, not_judged, suggestions
from resource_explorer.web.app import app


def manifest(**kw) -> dict:
    base = {"coverage": {"kind": "analysis", "question": "Which dependencies does it use?", "note": ""},
            "packed": [], "gaps": [], "dropped": [], "deferred": [], "declared": {}}
    base.update(kw)
    return base


class TestSuggestions:
    def test_nothing_fires_on_a_full_well_covered_compile(self):
        assert suggestions(manifest(packed=[{"key": "deps", "rung": "FULL"}])) == []

    def test_no_catalog_question_is_a_catalog_gap(self):
        [s] = suggestions(manifest(coverage={"kind": "none", "question": "", "note": ""}))
        assert s["cause"] == "catalog_gap" and "no catalog question" in s["rule"]

    def test_an_absent_gap_is_missing_evidence_and_a_real_zero_is_not(self):
        out = suggestions(manifest(gaps=[
            {"key": "cve_scan", "state": "never_run", "reason": "has not run"},
            {"key": "licenses", "state": "nothing_found", "reason": "ran and found nothing"},
            {"key": "ci", "reason": "no candidate"}]))
        [s] = out
        assert s["cause"] == "missing_evidence" and s["sections"] == ["cve_scan", "ci"]
        assert "cve_scan (has not run)" in s["rule"] and "ci (not judged" in s["rule"]

    def test_old_evidence_is_stale_measured_from_the_answer_not_from_today(self):
        m = manifest(declared={"deps": {"as_of": "2026-06-01T00:00:00+00:00"},
                               "ci": {"as_of": "2026-10-30T00:00:00+00:00"}})
        [s] = suggestions(m, answered_at="2026-11-10T12:00:00+00:00")
        assert s["cause"] == "stale" and s["sections"] == ["deps"] and "deps is as of 2026-06-01" in s["rule"]
        assert suggestions(m, answered_at="2026-07-01T00:00:00+00:00") == []
        assert suggestions(m) == []                       # no answer time: no age to judge

    def test_identifiers_dropped_and_catalogued_deferred_are_over_compressed(self):
        m = manifest(packed=[{"key": "deps", "rung": "IDENTIFIERS"}, {"key": "ci", "rung": "FULL"}],
                     dropped=[{"key": "api", "reason": "over budget"}],
                     deferred=[{"key": "licenses", "reason": "below the section cap of 6"},
                               {"key": "stars", "reason": "below the section cap of 6"}])
        [s] = suggestions(m, catalog_analyses=["licenses"])
        assert s["cause"] == "over_compressed" and s["sections"] == ["deps", "api", "licenses"]

    def test_several_signals_come_in_chain_order_so_the_first_is_the_earliest_stage(self):
        m = manifest(packed=[{"key": "deps", "rung": "IDENTIFIERS"}],
                     gaps=[{"key": "cve_scan", "state": "never_run", "reason": "has not run"}])
        assert [s["cause"] for s in suggestions(m)] == ["missing_evidence", "over_compressed"]


class TestNotJudged:
    """Absence is an answer only when it is stated: a cause the manifest could not judge is said to be unjudged, not
    left to read as "the evidence was fresh" (read-back P2-2)."""

    def test_no_declared_as_of_means_stale_was_not_judged(self):
        assert not_judged(manifest(packed=[{"key": "deps", "rung": "FULL"}]), answered_at="2026-11-10T00:00:00+00:00") \
            == [{"cause": "stale", "why": "no packed section declares an as-of date"}]

    def test_an_unreadable_as_of_is_named(self):
        [n] = not_judged(manifest(declared={"deps": {"as_of": "2026-06-01"}, "ci": {"as_of": "last week"}}),
                         answered_at="2026-11-10T00:00:00+00:00")
        assert n["cause"] == "stale" and "ci, deps cannot be read" in n["why"]

    def test_no_answer_time_means_no_age(self):
        [n] = not_judged(manifest(declared={"deps": {"as_of": "2026-06-01T00:00:00+00:00"}}))
        assert "answer's time is not known" in n["why"]

    def test_readable_dates_are_judged(self):
        assert not_judged(manifest(declared={"deps": {"as_of": "2026-11-01T00:00:00+00:00"}}),
                          answered_at="2026-11-10T00:00:00+00:00") == []

    def test_some_sections_undated_says_n_of_m(self):
        # Architect P3 after #1019: a dated section does not vouch for the others.
        [n] = not_judged(manifest(packed=[{"key": "deps", "rung": "FULL"}, {"key": "ci", "rung": "FULL"},
                                          {"key": "docs", "rung": "FULL"}],
                                  declared={"deps": {"as_of": "2026-11-01T00:00:00+00:00"}}),
                         answered_at="2026-11-10T00:00:00+00:00")
        assert n == {"cause": "stale", "partial": True,
                     "why": "2 of 3 sections have no as-of date (ci, docs), so their ages were not judged"}

    def test_the_instructions_section_is_not_counted(self):
        # Every real compile packs `instructions` (role "instructions"); it is not evidence and has no as-of.
        [n] = not_judged(manifest(packed=[{"key": "instructions", "role": "instructions", "rung": "FULL"},
                                          {"key": "deps", "role": "evidence", "rung": "FULL"},
                                          {"key": "ci", "role": "evidence", "rung": "FULL"}],
                                  declared={"deps": {"as_of": "2026-11-01T00:00:00+00:00"}}),
                         answered_at="2026-11-10T00:00:00+00:00")
        assert n["why"] == "1 of 2 sections have no as-of date (ci), so its age was not judged"

    def test_all_evidence_dated_beside_instructions_is_judged(self):
        assert not_judged(manifest(packed=[{"key": "instructions", "role": "instructions", "rung": "FULL"},
                                           {"key": "deps", "role": "evidence", "rung": "FULL"}],
                                   declared={"deps": {"as_of": "2026-11-01T00:00:00+00:00"}}),
                          answered_at="2026-11-10T00:00:00+00:00") == []

    def test_one_undated_section_reads_in_the_singular(self):
        [n] = not_judged(manifest(packed=[{"key": "deps", "rung": "FULL"}, {"key": "ci", "rung": "FULL"}],
                                  declared={"deps": {"as_of": "2026-11-01T00:00:00+00:00"}}),
                         answered_at="2026-11-10T00:00:00+00:00")
        assert n["why"] == "1 of 2 sections have no as-of date (ci), so its age was not judged"

    def test_unreadable_and_undated_are_both_said(self):
        [n] = not_judged(manifest(packed=[{"key": "deps", "rung": "FULL"}, {"key": "ci", "rung": "FULL"},
                                          {"key": "docs", "rung": "FULL"}],
                                  declared={"deps": {"as_of": "2026-11-01T00:00:00+00:00"}, "ci": {"as_of": "last week"}}),
                         answered_at="2026-11-10T00:00:00+00:00")
        assert "ci cannot be read" in n["why"] and "1 of 3 sections have no as-of date (docs)" in n["why"]
        assert n["partial"] is True

    def test_all_dates_unreadable_is_not_partial(self):
        [n] = not_judged(manifest(declared={"ci": {"as_of": "last week"}}), answered_at="2026-11-10T00:00:00+00:00")
        assert "partial" not in n

    def test_an_unjudged_gap_quotes_the_stored_reason(self):
        [s] = suggestions(manifest(gaps=[{"key": "ci", "reason": "no candidate — resolver produced nothing yet"}]))
        assert "ci (not judged: no candidate — resolver produced nothing yet)" in s["rule"]
        [c] = charge_set(manifest(gaps=[{"key": "ci", "reason": "no candidate"}]))
        assert c["words"] == "had no result (not judged)"


class TestChargeSet:
    def test_roles_are_ordered_most_likely_at_fault_first_and_each_analysis_once(self):
        m = manifest(packed=[{"key": "ci", "rung": "FULL"}, {"key": "deps", "rung": "IDENTIFIERS"},
                             {"key": "api", "rung": "SUMMARY"}],
                     gaps=[{"key": "cve_scan", "state": "never_run"}, {"key": "lic", "state": "nothing_found"}],
                     dropped=[{"key": "docs"}],
                     deferred=[{"key": "tests"}, {"key": "stars"}])
        got = [(c["analysis_id"], c["role"]) for c in charge_set(m, ["tests"])]
        assert got == [("cve_scan", "absent"), ("docs", "dropped"), ("tests", "deferred"),
                       ("deps", "IDENTIFIERS"), ("api", "SUMMARY"), ("ci", "FULL")]

    def test_a_catalog_gap_is_charged_to_no_analysis(self):
        assert charge_set(manifest(coverage={"kind": "none"}, packed=[{"key": "deps", "rung": "FULL"}])) == []


# ── routes ──────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def feedback_store(tmp_path):
    from resource_explorer.feedback_store import FeedbackStore
    return FeedbackStore(db_path=str(tmp_path / "diag_feedback.db"))


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


def _compile(compile_id: str, m: dict, text: str = "## deps\nrequests") -> None:
    from resource_explorer.registry import ProjectRegistry
    ProjectRegistry().record_compile(compile_id, "r1", "what does it use?", {**m, "text": text}, [])


class TestRoutes:
    def test_the_panel_reads_the_stored_compile_and_suggests_with_the_rule(self, client, feedback_store,
                                                                             admin_headers):
        _compile("c1", manifest(gaps=[{"key": "cve_scan", "state": "never_run", "reason": "has not run"}],
                                packed=[{"key": "deps", "rung": "FULL"}]))
        item_id, _ = feedback_store.add_answer(vote=-1, question="what does it use?", compile_id="c1")
        d = client.get(f"/api/feedback/{item_id}/diagnosis", headers=admin_headers).json()
        assert d["compile"]["text"] == "## deps\nrequests"           # what the model was given, as stored
        assert "text" not in d["compile"]["manifest"]
        assert d["suggestions"][0]["cause"] == "missing_evidence"
        assert d["not_judged"] == [{"cause": "stale", "why": "no packed section declares an as-of date"}]
        assert [c["analysis_id"] for c in d["charge_set"]] == ["cve_scan", "deps"]

    def test_an_answer_with_no_compile_says_so(self, client, feedback_store, admin_headers):
        item_id, _ = feedback_store.add_answer(vote=-1, question="hello?")
        d = client.get(f"/api/feedback/{item_id}/diagnosis", headers=admin_headers).json()
        assert d["compile"] is None and "RAG or agent turn" in d["no_compile"] and d["suggestions"] == []
        gone_id, _ = feedback_store.add_answer(vote=-1, question="hello?", compile_id="nope")
        assert "no longer stored" in client.get(f"/api/feedback/{gone_id}/diagnosis",
                                                headers=admin_headers).json()["no_compile"]

    def test_the_panel_is_admin_only_and_for_answers_only(self, client, feedback_store, admin_headers):
        from resource_explorer.feedback_store import FeedbackEntry
        item_id, _ = feedback_store.add_answer(vote=-1, question="q?")
        assert client.get(f"/api/feedback/{item_id}/diagnosis").status_code == 403
        page = feedback_store.add(FeedbackEntry(page="/x", category="bug", message="m"))
        assert client.get(f"/api/feedback/{page.id}/diagnosis", headers=admin_headers).status_code == 400

    def test_patch_records_a_cause_and_refuses_a_contradiction(self, client, feedback_store, admin_headers):
        item_id, _ = feedback_store.add_answer(vote=-1, question="q?")
        ok = client.patch(f"/api/feedback/{item_id}", headers=admin_headers,
                          json={"triage_status": "triaged", "cause": "wrong_evidence", "secondary_cause": "reasoning"})
        assert ok.status_code == 200, ok.text
        assert (ok.json()["triage"]["cause"], ok.json()["triage"]["secondary_cause"]) == ("wrong_evidence", "reasoning")
        bad = client.patch(f"/api/feedback/{item_id}", headers=admin_headers, json={"triage_status": "not_an_issue"})
        assert bad.status_code == 400 and "re-diagnose" in bad.json()["detail"]

    def test_a_page_report_takes_no_cause(self, client, feedback_store, admin_headers):
        from resource_explorer.feedback_store import FeedbackEntry
        page = feedback_store.add(FeedbackEntry(page="/x", category="bug", message="m"))
        r = client.patch(f"/api/feedback/{page.id}", headers=admin_headers,
                         json={"triage_status": "triaged", "cause": "reasoning"})
        assert r.status_code == 400 and "product report" in r.json()["detail"]

    def test_the_listing_filters_by_cause_and_serves_the_vocabulary(self, client, feedback_store, admin_headers):
        from resource_explorer.feedback_store import FeedbackEntry
        a, _ = feedback_store.add_answer(vote=-1, question="one?")
        b, _ = feedback_store.add_answer(vote=-1, question="two?")
        feedback_store.add(FeedbackEntry(page="/x", category="bug", message="m"))
        feedback_store.update_triage_status(a, "triaged", cause="stale")
        data = client.get("/api/curate/feedback?cause=stale", headers=admin_headers).json()
        assert [r["id"] for r in data["feedback"]] == [a] and data["filtered"]
        undiagnosed = client.get("/api/curate/feedback?cause=undiagnosed", headers=admin_headers).json()["feedback"]
        assert [r["id"] for r in undiagnosed] == [b]
        assert data["causes"][0] == "catalog_gap" and data["cause_chain"][-1] == "reasoning"
        assert client.get("/api/curate/feedback?cause=flaky", headers=admin_headers).status_code == 400

    def test_counts_by_cause_and_by_charged_analysis(self, client, feedback_store, admin_headers):
        _compile("c1", manifest(gaps=[{"key": "cve_scan", "state": "never_run"}],
                                packed=[{"key": "deps", "rung": "FULL"}]))
        _compile("c2", manifest(packed=[{"key": "deps", "rung": "IDENTIFIERS"}]))
        a, _ = feedback_store.add_answer(vote=-1, question="a?", compile_id="c1")
        b, _ = feedback_store.add_answer(vote=-1, question="b?", compile_id="c2")
        c, _ = feedback_store.add_answer(vote=-1, question="c?")
        feedback_store.add_answer(vote=1, question="d?", compile_id="c1")
        feedback_store.update_triage_status(a, "triaged", cause="missing_evidence")
        feedback_store.update_triage_status(b, "triaged", cause="over_compressed")
        feedback_store.update_triage_status(c, "triaged", cause="reasoning")
        data = client.get("/api/feedback/causes", headers=admin_headers).json()
        assert data["vote"] == -1
        assert "all time" in data["populations"]["counts"] and "newest" in data["populations"]["by_analysis"]
        assert (data["counts"]["missing_evidence"], data["counts"]["over_compressed"],
                data["counts"]["reasoning"], data["counts"]["undiagnosed"]) == (1, 1, 1, 0)
        by = {e["analysis_id"]: e for e in data["by_analysis"]}
        assert by["deps"]["total"] == 2 and by["deps"]["causes"] == {"missing_evidence": 1, "over_compressed": 1}
        assert by["cve_scan"]["roles"] == {"absent": 1}
        assert data["uncharged"] == 1                                 # c: no compile
        assert client.get("/api/feedback/causes?vote=all", headers=admin_headers).json()["counts"]["undiagnosed"] == 1
        assert client.get("/api/feedback/causes?vote=5", headers=admin_headers).status_code == 400
        assert client.get("/api/feedback/causes?vote=0", headers=admin_headers).json()["counts"]["undiagnosed"] == 0
        assert client.get("/api/feedback/causes").status_code == 403

    def test_the_slow_routes_are_plain_defs_so_they_run_off_the_event_loop(self):
        # Read-back P2-1: they read the registry and the catalog synchronously; as async defs they would block
        # every request on the loop, chat streams included, while they ran.
        import inspect

        from resource_explorer.web.routes import feedback as routes
        assert not inspect.iscoroutinefunction(routes.cause_counts_route)
        assert not inspect.iscoroutinefunction(routes.feedback_diagnosis)
