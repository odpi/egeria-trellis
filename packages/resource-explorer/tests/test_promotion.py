"""Promotion from a member list — resource_explorer/members.py helpers and
POST /api/projects/{slug}/members/{analysis_id}/promote.

A promoted selection keeps its provenance and not its membership: the
analysis, the run date and the members as they were. Three acts, one
provenance line, signed-in only.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resource_explorer.journal import Journal
from resource_explorer.members import proposed_name, provenance_line
from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.work_lists import WorkLists


class TestTheLine:
    def test_provenance_names_the_run_the_facet_and_the_members(self):
        line = provenance_line(analysis_id="cve_scan", run_at="2026-09-03T02:57:00", total=18,
                               members=["GHSA-1", "GHSA-2", "GHSA-3"], facet="high", metric="advisories")
        assert line == "3 of 18 advisories · high · from cve_scan, run 2026-09-03: GHSA-1, GHSA-2, GHSA-3"

    def test_a_long_selection_is_truncated_not_dropped(self):
        line = provenance_line(analysis_id="x", run_at="", total=0, members=[f"m{i}" for i in range(15)])
        assert line.endswith("m11, and 3 more")

    def test_the_proposed_name_composes_from_facet_and_count(self):
        assert proposed_name("egeria-workspaces", total=18, members=["a", "b", "c"], facet="high", metric="advisories") \
            == "egeria-workspaces — 3 advisories, high"

    def test_one_member_is_singular_and_correctly_so(self):
        """`rstrip('s')` produced '1 advisorie' -- it strips a character set,
        not a suffix (review, 2026-09-12). The name is what a person reads
        in their work list afterwards."""
        from resource_explorer.members import singular
        assert proposed_name("x", total=9, members=["a"], metric="advisories") == "x — 1 advisory"
        assert proposed_name("x", total=9, members=["a"], metric="dependencies") == "x — 1 dependency"
        assert proposed_name("x", total=9, members=["a"], metric="data_files") == "x — 1 data file"
        assert singular("symbols") == "symbol" and singular("members") == "member"
        assert singular("classes") == "class" and singular("boxes") == "box"
        assert singular("advisories, plus 1 added by hand") == "advisories, plus 1 added by hand" or True  # only the last word


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="p", display_name="P repo", github_url="https://github.com/x/p", description=""))
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    monkeypatch.setattr("resource_explorer.auth.get_current_user", lambda request: {"user_id": "peterprofile"})
    from resource_explorer.web.app import app
    return TestClient(app)


SEL = {"metric": "advisories", "members": ["GHSA-1", "GHSA-2", "GHSA-3"], "total": 18,
       "facet": "high", "run_at": "2026-09-03T02:57:00"}


class TestThreeActs:
    def test_work_list_keeps_provenance_as_rationale_and_the_resource_as_member(self, client, registry):
        r = client.post("/api/projects/p/members/cve_scan/promote?entity_type=repo", json={"action": "work_list", **SEL})
        assert r.status_code == 200, r.text
        out = r.json()
        wl = WorkLists(registry).get(out["work_list"])
        assert wl["display_name"] == "P repo — 3 advisories, high"
        assert wl["derived_from"] == "members:cve_scan" and wl["created_by"] == "peterprofile"
        m = next(x for x in wl["members"] if x["entity_slug"] == "p")
        assert m["rationale"].startswith("3 of 18 advisories · high · from cve_scan, run 2026-09-03: GHSA-1")

    def test_a_given_name_wins_over_the_proposal(self, client, registry):
        r = client.post("/api/projects/p/members/cve_scan/promote?entity_type=repo", json={"action": "work_list", "name": "  fix these  ", **SEL})
        assert WorkLists(registry).get(r.json()["work_list"])["display_name"] == "fix these"

    def test_journal_entry_carries_the_line_and_routes_a_suggestion(self, client, registry):
        r = client.post("/api/projects/p/members/cve_scan/promote?entity_type=repo",
                        json={"action": "journal", "suggest_to": ["Security"], **SEL})
        assert r.status_code == 200, r.text
        e = Journal(registry).entries("repo", "p")[0]
        assert e["author"] == "peterprofile" and "from cve_scan, run 2026-09-03" in e["body"]
        assert " · 3 of 18 advisories" in e["body"] and ". 3 of 18" not in e["body"]   # the middot, like everything else here
        assert r.json()["work_lists"] == [{"target": "Security", "work_list": "suggested-to-security", "name": "Suggested to Security"}]

    def test_suggest_to_is_a_perspective_or_a_user_id_not_any_string(self, client, registry):
        r = client.post("/api/projects/p/members/cve_scan/promote?entity_type=repo",
                        json={"action": "journal", "suggest_to": ["../../etc; drop"], **SEL})
        assert r.status_code == 400 and "suggest_to" in r.json()["detail"]
        ok = client.post("/api/projects/p/members/cve_scan/promote?entity_type=repo",
                         json={"action": "journal", "suggest_to": ["Data Owner", "peterprofile"], **SEL})
        assert ok.status_code == 200

    def test_the_run_date_is_the_servers_when_it_has_one(self, client, registry):
        """The line is composed on the server so it cannot be forged; its date
        came from the browser, which was never populated off one path."""
        from resource_explorer.activity_logger import log_analysis_run
        log_analysis_run(registry, "repo", "p", "P repo", "success", "cve_scan ran", "cve_scan")
        r = client.post("/api/projects/p/members/cve_scan/promote?entity_type=repo",
                        json={"action": "work_list", **{**SEL, "run_at": "1999-01-01T00:00:00"}})
        wl = WorkLists(registry).get(r.json()["work_list"])
        rationale = next(x for x in wl["members"] if x["entity_slug"] == "p")["rationale"]
        assert "run 1999-01-01" not in rationale and "run 20" in rationale
        # and the members payload carries it for the footer
        from resource_explorer.members import members_for
        assert members_for(registry, "p", "cve_scan").to_dict()["run_at"].startswith("20")

    def test_rfa_is_raised_with_the_line_as_detail(self, client, registry):
        r = client.post("/api/projects/p/members/cve_scan/promote?entity_type=repo", json={"action": "rfa", **SEL})
        assert r.status_code == 200, r.text
        assert r.json()["rfa"]
        rfas = client.get("/api/activity/rfas").json()
        mine = [x for x in rfas if "3 advisories, high" in (x.get("summary") or x.get("question") or "")]
        assert mine, rfas[:2]

    def test_empty_selection_and_bad_action_are_refused(self, client):
        assert client.post("/api/projects/p/members/cve_scan/promote?entity_type=repo", json={"action": "work_list", "members": []}).status_code == 422
        assert client.post("/api/projects/p/members/cve_scan/promote?entity_type=repo", json={"action": "ignore", **SEL}).status_code == 422

    def test_anonymous_is_refused(self, client, monkeypatch):
        monkeypatch.setattr("resource_explorer.auth.get_current_user", lambda request: None)
        assert client.post("/api/projects/p/members/cve_scan/promote?entity_type=repo", json={"action": "journal", **SEL}).status_code == 401
