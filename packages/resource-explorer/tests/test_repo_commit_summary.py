"""Brief section 2 (owner, 2026-10-07): "yes, we need the summary for repos."

The commit table's state column comes from PROOF ROWS only, and its counts are the rows' counts, never
the request's: a sub-resource is counted read back only when a read of its GUID showed it. A press
with nothing selected is blocked with the sentence and writes nothing. Fake clients; nothing live.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from resource_explorer import repo_publish as rp
from resource_explorer.curate_plan import Curations, NOTHING_SELECTED_SENTENCE
from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.workflows.curate_commit import STEPS, execute_curation


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="p", display_name="P repo", github_url="https://github.com/x/p", description=""))
    r.set_project_context("repo", "p", status="linked", egeria_project_guid="g", egeria_project_qualified_name="Project::p")
    return r


def test_each_sub_resource_gets_a_proof_row_by_guid_after_a_read(registry):
    wanted = {"docs": "folder", "docs/a.md": "file", "src": "folder"}
    guids = {"docs": "g-docs", "docs/a.md": "g-a", "src": "g-src"}
    reads = []

    def reader(guid):
        reads.append(guid)
        return None if guid == "g-src" else {"guid": guid}

    counts = rp.record_sub_resource_proofs(registry, "p", "c1", "dan", wanted, ["docs/a.md", "src"], guids, reader)
    assert counts == {"read_back": 2, "sent": 1, "failed": 0}
    assert sorted(reads) == ["g-a", "g-docs", "g-src"]                 # every GUID was read before any word
    rows = {p["table_name"]: p for p in registry.list_catalogue_commit_proofs("p") if p["node_kind"] == "sub_resource"}
    assert rows["docs/a.md"]["proof"] == "sub_resource_read_back" and rows["docs/a.md"]["element_guid"] == "g-a"
    assert rows["src"]["proof"] == "report_sent"                         # created, not shown yet: sent, never read back
    assert rows["docs"]["detail"]["role"] == "container" and rows["docs/a.md"]["detail"]["role"] == "chosen"


def test_no_guid_and_a_failed_read_are_failures_with_egeria_s_sentence(registry):
    def reader(guid):
        raise RuntimeError("OMAG-404-001 element not found. Details follow.")
    counts = rp.record_sub_resource_proofs(registry, "p", "c1", "dan", {"a": "folder", "b": "file"}, ["a", "b"],
                                           {"b": "g-b"}, reader)
    assert counts == {"read_back": 0, "sent": 0, "failed": 2}
    rows = {p["table_name"]: p for p in registry.list_catalogue_commit_proofs("p") if p["node_kind"] == "sub_resource"}
    assert "no GUID" in rows["a"]["detail"]["error"]
    assert rows["b"]["detail"]["error"].startswith("OMAG-404-001")      # the full sentence is stored


def test_the_table_counts_are_the_proof_rows_counts_not_the_requests(registry):
    cur = Curations(registry).create("repo", "p", author="dan", selection={"sub_resources": ["a", "b", "c"]},
                                     manifest={}, steps=list(STEPS))
    rp.record_sub_resource_proofs(registry, "p", cur["id"], "dan", {"a": "folder", "b": "file", "c": "file"},
                                  ["a", "b", "c"], {"a": "g1", "b": "g2"}, lambda g: {"guid": g})
    s = rp.commit_proof_summary(registry, "p", registry_get(registry, cur["id"]))
    assert s["sub_resources"]["read_back"] == 2 and s["sub_resources"]["failed"] == 1     # the request said 3
    assert s["sub_resources"]["first_failure"].startswith("Egeria returned no GUID")


def registry_get(registry, cid):
    return Curations(registry).get(cid)


def test_the_report_state_is_read_from_the_newest_report_proof_after_the_press(registry):
    cur = Curations(registry).create("repo", "p", author="dan", selection={}, manifest={}, steps=list(STEPS))
    assert rp.commit_proof_summary(registry, "p", cur)["report"] == {"state": "none"}
    registry.append_catalogue_commit_proof("p", proof="report_sent", node_kind="repo_report", element_guid="r1",
                                           detail={"surveyed_at": "2026-10-07T01:00:00"})
    assert rp.commit_proof_summary(registry, "p", cur)["report"]["state"] == "sent"
    registry.append_catalogue_commit_proof("p", proof="report_published", node_kind="repo_report", element_guid="r1",
                                           detail={"surveyed_at": "2026-10-07T01:00:00", "annotation_count": 7})
    rep = rp.commit_proof_summary(registry, "p", cur)["report"]
    assert rep["state"] == "published" and rep["annotation_count"] == 7 and rep["surveyed_at"] == "2026-10-07T01:00:00"
    registry.append_catalogue_commit_proof("p", proof="read_failed", node_kind="repo_report",
                                           detail={"error": "OMAG-409-001 name taken. More follows."})
    rep = rp.commit_proof_summary(registry, "p", cur)["report"]
    assert rep["state"] == "not published" and rep["first"] == "OMAG-409-001 name taken."


def test_the_commit_writes_sub_resource_proofs_and_says_how_many_were_read_back(registry):
    from resource_explorer.surveyors import survey_snapshot
    from resource_explorer.surveyors.survey_report import ClassificationAnnotation
    survey_snapshot.record_step(registry, "p", "repo_language", "2026-10-07T01:00:00",
                                [ClassificationAnnotation(summary="s", analysis_step="repo_language", check_name="c")])
    cur = Curations(registry).create("repo", "p", author="dan", selection={"sub_resources": ["docs"]},
                                     manifest={}, steps=list(STEPS))
    with patch("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher") as Pub:
        pub = Pub.return_value
        pub.publish.side_effect = lambda r, **k: registry.set_egeria_asset_guid("p", "asset-1") or "rep-1"
        pub.report_reused = False
        pub.get_survey_reports_by_guid.return_value = [{"guid": "rep-1", "qualified_name": "x", "annotation_count": 1}]
        pub.publish_sub_resources.return_value = {"docs": "g-docs"}
        pub._asset_maker.get_asset_by_guid.return_value = {"guid": "g-docs"}
        rec = execute_curation(registry, cur["id"])
    step = next(s for s in rec["steps"] if s["name"] == "sub_resources")
    assert step["state"] == "done" and "1 read back" in step["detail"]
    proofs = [p for p in registry.list_catalogue_commit_proofs("p") if p["node_kind"] == "sub_resource"]
    assert [(p["table_name"], p["proof"]) for p in proofs] == [("docs", "sub_resource_read_back")]


def test_nothing_selected_is_blocked_with_the_sentence_and_writes_nothing(registry, monkeypatch):
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setattr("resource_explorer.auth.get_current_user", lambda request: {"user_id": "dan"})
    from resource_explorer.surveyors import survey_snapshot
    from resource_explorer.surveyors.survey_report import ClassificationAnnotation
    survey_snapshot.record_step(registry, "p", "repo_language", "2026-10-07T01:00:00",
                                [ClassificationAnnotation(summary="s", analysis_step="repo_language", check_name="c")])
    monkeypatch.setattr("resource_explorer.curate_plan.build_plan", lambda reg, slug: {
        "in_population": True, "disposition": "using", "what_it_is": [], "writes": {"contained": {"data_files": 0}}})
    from resource_explorer.web.app import app
    r = TestClient(app).post("/api/projects/p/curate/commit", json={"confirm": [], "sub_resources": []})
    assert r.status_code == 409 and r.json()["detail"] == NOTHING_SELECTED_SENTENCE
    assert registry.list_runs() == [] and Curations(registry).for_resource("repo", "p") == []
    assert registry.list_catalogue_commit_proofs("p") == []


def test_the_plan_carries_the_survey_the_commit_would_publish(registry):
    from resource_explorer.curate_plan import build_plan
    plan = build_plan(registry, "p")
    assert plan["survey"] == {"exists": False, "surveyed_before_kept": False,
                              "sentence": "no survey to publish yet · run the first survey"}


def test_the_plan_of_a_repo_surveyed_before_kept_surveys_says_so(registry):
    from resource_explorer.curate_plan import build_plan
    registry.update_project_surveyed_at("p")          # the header's "surveyed <ago>" reads this column
    plan = build_plan(registry, "p")
    assert plan["survey"] == {"exists": False, "surveyed_before_kept": True,
                              "sentence": "surveyed before 10-07 · nothing kept to publish · re-survey to publish"}


def test_the_summary_carries_the_one_confirmation_verdict_from_the_reports_own_run(registry):
    """One decider for "same result": a report found by GUID is not confirmed while its annotations are still
    sending, nor when some are dead; only the settled, none-dead run is confirmed (repo_publish.confirmation)."""
    cur = Curations(registry).create("repo", "p", author="dan", selection={}, manifest={}, steps=list(STEPS))
    assert "confirm" not in rp.commit_proof_summary(registry, "p", cur)
    run = "Annotation::p::2026-10-11T00:00:00"
    registry.append_catalogue_commit_proof("p", proof="report_published", node_kind="repo_report", element_guid="r1",
                                           detail={"annotation_count": 2, "annotations_in_egeria": 2,
                                                   "publish_run_id": run})
    ids = [registry.enqueue_outbox_element("repo", "p", "annotation", f"{run}::{i}", {}, run_id=run) for i in range(2)]
    c = rp.commit_proof_summary(registry, "p", cur)["confirm"]
    assert c["state"] == rp.UNCONFIRMED and c["word"].endswith("not yet confirmed · 2 sending")
    registry.mark_outbox_done(ids[0], "g0")
    with registry._conn() as conn:
        conn.execute("UPDATE egeria_outbox SET status='dead' WHERE id=?", (ids[1],))
    c = rp.commit_proof_summary(registry, "p", cur)["confirm"]
    assert c["state"] == rp.DIFFERS and c["word"].endswith("Egeria shows 1")
    registry.mark_outbox_done(ids[1], "g1")
    assert rp.commit_proof_summary(registry, "p", cur)["confirm"]["state"] == rp.CONFIRMED
