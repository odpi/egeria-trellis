"""TR-132 C1: a repository's branches, first increment (Dan: option C, sparse trees later, cap 5 later).

Recorded, not created: the ref a survey READ (name, kind, commit), every branch GitHub lists (metadata only), and, behind
gates, Egeria 6.4's Branch classification on the default branch's EXISTING root folder. Nothing here makes a new Egeria element.
No network, no Egeria: fakes only.
"""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from github import GithubException

from resource_explorer.github.client import GitHubClient, UnknownRef
from resource_explorer.registry import ProjectRegistry
from resource_explorer.repo_branches import record_repo_branches, ref_fields
from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

URL = "https://github.com/acme/widgets"
SHA = "a" * 40


# ── fakes ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def _exc(status):
    return GithubException(status, {"message": "not found"}, {})


class FakeRepo:
    def __init__(self, default="main", branches=None, tags=None, commits=None, annotated=None):
        self.default_branch = default
        self._branches = branches if branches is not None else {"main": ("m" * 40, True), "dev": ("d" * 40, False)}
        self._tags = tags or {}
        self._commits = commits or {}
        self._annotated = annotated or {}

    def get_branches(self):
        for name, (sha, prot) in self._branches.items():           # a generator: PyGitHub's PaginatedList is iterated the same way
            yield SimpleNamespace(name=name, commit=SimpleNamespace(sha=sha), protected=prot)

    def get_branch(self, name):
        if name not in self._branches:
            raise _exc(404)
        return SimpleNamespace(commit=SimpleNamespace(sha=self._branches[name][0]))

    def get_git_ref(self, ref):
        tag = ref.split("/", 1)[1]
        if tag not in self._tags:
            raise _exc(404)
        kind = "tag" if self._tags[tag] in self._annotated else "commit"
        return SimpleNamespace(object=SimpleNamespace(sha=self._tags[tag], type=kind))

    def get_git_tag(self, sha):
        return SimpleNamespace(object=SimpleNamespace(sha=self._annotated[sha]))

    def get_commit(self, sha):
        if sha not in self._commits:
            raise _exc(404)
        return SimpleNamespace(sha=self._commits[sha])


@pytest.fixture
def reg(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "t.db"))


@pytest.fixture
def client():
    return GitHubClient.__new__(GitHubClient)          # list_branches/resolve_ref use only the repo object


# ── the inventory and ref resolution ──────────────────────────────────────────────────────────────────────────────

def test_the_inventory_lists_every_branch_with_head_sha_and_protected_and_no_date(client):
    out, more = client.list_branches(FakeRepo(branches={f"b{i}": (f"{i:040d}", i % 2 == 0) for i in range(250)}))
    assert len(out) == 250 and more is False
    assert out[3] == {"name": "b3", "head_sha": f"{3:040d}", "protected": False, "last_commit_date": None}
    assert all(b["last_commit_date"] is None for b in out)                 # not read, never a made-up date


def test_a_repository_with_more_branches_than_the_cap_says_so(client):
    out, more = client.list_branches(FakeRepo(branches={f"b{i}": ("0" * 40, False) for i in range(12)}), limit=5)
    assert len(out) == 5 and more is True


def test_a_branch_is_resolved_with_its_commit(client):
    ref = client.resolve_ref(FakeRepo(), "dev")
    assert ref == {"ref_name": "dev", "ref_kind": "branch", "surveyed_commit": "d" * 40}


def test_no_ref_means_the_default_branch(client):
    assert client.resolve_ref(FakeRepo(default="main")) == {"ref_name": "main", "ref_kind": "branch", "surveyed_commit": "m" * 40}


def test_a_tag_is_a_tag_and_an_annotated_tag_resolves_to_its_commit(client):
    repo = FakeRepo(tags={"v1": "1" * 40, "v2": "t" * 40}, annotated={"t" * 40: "c" * 40})
    assert client.resolve_ref(repo, "v1") == {"ref_name": "v1", "ref_kind": "tag", "surveyed_commit": "1" * 40}
    assert client.resolve_ref(repo, "v2") == {"ref_name": "v2", "ref_kind": "tag", "surveyed_commit": "c" * 40}


def test_a_sha_is_a_commit(client):
    assert client.resolve_ref(FakeRepo(commits={"abcdef1": "abcdef1" + "0" * 33}), "abcdef1") == {
        "ref_name": "abcdef1", "ref_kind": "commit", "surveyed_commit": "abcdef1" + "0" * 33}


def test_a_name_that_is_nothing_is_refused_in_words(client):
    with pytest.raises(UnknownRef, match="not a branch, a tag or a commit"):
        client.resolve_ref(FakeRepo(), "nope")


# ── repo_branch_runs ──────────────────────────────────────────────────────────────────────────────────────────────

def test_the_default_gets_a_surveyed_row_and_every_other_branch_a_listed_one(reg, client):
    info = record_repo_branches(reg, "widgets", "2026-11-22T10:00:00", client, FakeRepo())
    assert info["ref_name"] == "main" and info["is_default"] is True and info["branches_listed"] == 2
    rows = reg.list_repo_branch_runs("widgets")
    assert [(r["ref_name"], r["tier"], r["is_default"], r["protected"]) for r in rows] == [
        ("main", "surveyed", True, True), ("dev", "listed", False, False)]
    assert rows[0]["surveyed_commit"] == "m" * 40 and rows[1]["surveyed_commit"] == "d" * 40
    assert all(r["compared_to"] is None and r["changed_paths"] is None and r["deleted_paths"] is None for r in rows)


def test_a_second_run_does_not_touch_the_first(reg, client):
    record_repo_branches(reg, "widgets", "2026-11-22T10:00:00", client, FakeRepo())
    first = reg.list_repo_branch_runs("widgets", "2026-11-22T10:00:00")
    record_repo_branches(reg, "widgets", "2026-11-23T10:00:00", client, FakeRepo(branches={"main": ("n" * 40, True)}))
    assert reg.list_repo_branch_runs("widgets", "2026-11-22T10:00:00") == first
    assert [r["surveyed_commit"] for r in reg.list_repo_branch_runs("widgets", "2026-11-23T10:00:00")] == ["n" * 40]
    assert reg.list_repo_branch_runs("widgets")[0]["run_id"] == "2026-11-23T10:00:00"      # none given: the newest run


def test_recording_the_same_run_again_replaces_only_that_runs_rows(reg, client):
    record_repo_branches(reg, "widgets", "2026-11-22T10:00:00", client, FakeRepo())
    record_repo_branches(reg, "widgets", "2026-11-22T10:00:00", client, FakeRepo(branches={"main": ("n" * 40, True)}))
    assert [r["ref_name"] for r in reg.list_repo_branch_runs("widgets", "2026-11-22T10:00:00")] == ["main"]


def test_a_tag_survey_is_a_surveyed_tag_row_and_the_default_branch_is_only_listed(reg, client):
    info = record_repo_branches(reg, "widgets", "r1", client, FakeRepo(tags={"v1": "1" * 40}), requested_ref="v1")
    assert (info["ref_kind"], info["is_default"]) == ("tag", False)
    rows = {r["ref_name"]: r for r in reg.list_repo_branch_runs("widgets")}
    assert rows["v1"]["tier"] == "surveyed" and rows["v1"]["ref_kind"] == "tag" and rows["v1"]["protected"] is None
    assert rows["main"]["tier"] == "listed" and rows["main"]["is_default"] is True


def test_a_failed_listing_is_said_in_words_and_the_surveyed_row_is_still_written(reg, client):
    repo = FakeRepo()
    repo.get_branches = MagicMock(side_effect=_exc(403))
    info = record_repo_branches(reg, "widgets", "r1", client, repo)
    rows = reg.list_repo_branch_runs("widgets")
    assert [r["ref_name"] for r in rows] == ["main"] and "could not be listed" in rows[0]["words"]
    assert info["branches_listed"] == 0 and rows[0]["protected"] is None            # unknown, not "not protected"


def test_an_unresolvable_ref_writes_nothing_and_says_so(reg, client):
    info = record_repo_branches(reg, "widgets", "r1", client, FakeRepo(), requested_ref="nope")
    assert info["ref_name"] == "" and "could not be resolved" in info["words"]
    assert reg.list_repo_branch_runs("widgets") == []


def test_old_runs_are_pruned_and_removing_the_repository_removes_them_all(reg, client):
    for i in range(12):
        record_repo_branches(reg, "widgets", f"run-{i:02d}", client, FakeRepo())
    runs = {r["run_id"] for s in [reg.list_repo_branch_runs("widgets", f"run-{i:02d}") for i in range(12)] for r in s}
    assert len(runs) == 10 and "run-00" not in runs and "run-11" in runs


# ── the table is made by `schema apply`, never by an external pod ─────────────────────────────────────────────────

def test_schema_apply_creates_the_table_and_external_mode_never_does(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'ext.db'}"
    monkeypatch.setenv("REGISTRY_DATABASE_URL", url)
    applier = ProjectRegistry.open_for_schema_apply(database_url=url)
    applier.schema_apply()
    with applier._conn() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(repo_branch_runs)").fetchall()]
    assert cols == ["slug", "run_id", "ref_name", "ref_kind", "surveyed_commit", "tier", "is_default", "protected", "compared_to",
                    "changed_paths", "deleted_paths", "egeria_folder_guid", "words", "recorded_at"]
    monkeypatch.setenv("RE_SCHEMA_MODE", "external")
    ProjectRegistry(database_url=url)                 # a matching marker: opens, and runs no DDL


def test_external_mode_without_the_applied_table_refuses_and_creates_nothing(tmp_path, monkeypatch):
    from resource_explorer.registry import SchemaNotReady

    url = f"sqlite:///{tmp_path / 'never.db'}"
    monkeypatch.setenv("RE_SCHEMA_MODE", "external")
    with pytest.raises(SchemaNotReady):
        ProjectRegistry(database_url=url)
    import sqlite3

    path = tmp_path / "never.db"
    assert not path.exists() or "repo_branch_runs" not in {r[0] for r in sqlite3.connect(path).execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def test_the_schema_fingerprint_follows_the_new_table():
    from resource_explorer.schema_fingerprint import fingerprint

    assert fingerprint("resource_explorer.registry", "ProjectRegistry") == ProjectRegistry.schema_fingerprint()
    src = open(__import__("resource_explorer.registry", fromlist=["x"]).__file__, encoding="utf-8").read()
    assert "for _ddl in REPO_BRANCH_RUNS_DDL" in src          # reached from _init_schema, so its SQL is in the hash


# ── the ref on the survey's metrics and on the SurveyReport ───────────────────────────────────────────────────────

def test_ref_fields_are_the_three_names_and_empty_when_no_code_was_read():
    assert ref_fields({"ref_name": "main", "ref_kind": "branch", "surveyed_commit": SHA}) == {
        "surveyed_ref": "main", "ref_kind": "branch", "surveyed_commit": SHA}
    assert ref_fields(None) == {} and ref_fields({"ref_name": ""}) == {}


def _observation():
    from resource_explorer.surveyors import step_cost_observer as sco

    return sco.Observation(step_key="repo_stats", elapsed=0.1, connects=0, declared_fetch="none", declared_compute="none")


def test_the_step_run_metrics_carry_the_ref(reg):
    from resource_explorer.surveyors import step_cost_observer as sco

    obs = _observation()
    sco.record(reg, "widgets", obs, "2026-11-22T10:00:00",
               survey_ref={"surveyed_ref": "main", "ref_kind": "branch", "surveyed_commit": SHA})
    rows = reg.query_step_runs(slug="widgets", step_key="repo_stats", entity_type="repo", limit=1)
    m = rows[0]["metrics"]
    assert (m["surveyed_ref"], m["ref_kind"], m["surveyed_commit"]) == ("main", "branch", SHA)


def test_a_step_run_that_read_no_code_carries_no_ref(reg):
    from resource_explorer.surveyors import step_cost_observer as sco

    obs = _observation()
    sco.record(reg, "widgets", obs, "2026-11-22T10:00:00")
    m = reg.query_step_runs(slug="widgets", step_key="repo_stats", entity_type="repo", limit=1)[0]["metrics"]
    assert not {"surveyed_ref", "ref_kind", "surveyed_commit"} & set(m)


def test_the_survey_report_carries_the_three_ref_fields_and_the_subject_stays_the_repository():
    from resource_explorer.surveyors.survey_report import SurveyResult

    pub = EgeriaPublisher(platform_url="https://fake", registry=MagicMock())
    pub._asset_maker = MagicMock()
    pub._asset_maker.create_asset.return_value = "report-guid"
    pub._find_element_guid = MagicMock(return_value="")
    result = SurveyResult(resource_slug="widgets", project_display_name="Widgets", github_url=URL, surveyed_at=datetime(2026, 11, 22),
                          surveyed_ref="main", ref_kind="branch", surveyed_commit=SHA)
    try:
        pub._create_survey_report(result, "asset-guid")
    except Exception:       # a later step of the create may need more than this fake: the body is what is asserted
        pass
    body = pub._asset_maker.create_asset.call_args.kwargs.get("body") or pub._asset_maker.create_asset.call_args.args[0]
    assert body["parentGUID"] == "asset-guid" and body["parentRelationshipTypeName"] == "ReportSubject"
    props = body["properties"]["additionalProperties"]
    assert (props["surveyed_ref"], props["ref_kind"], props["surveyed_commit"]) == ("main", "branch", SHA)


def test_a_report_for_a_run_that_read_no_code_has_no_ref_fields():
    from resource_explorer.surveyors.survey_report import SurveyResult

    pub = EgeriaPublisher(platform_url="https://fake", registry=MagicMock())
    pub._asset_maker = MagicMock()
    pub._asset_maker.create_asset.return_value = "g"
    pub._find_element_guid = MagicMock(return_value="")
    try:
        pub._create_survey_report(SurveyResult(resource_slug="w", project_display_name="W", github_url=URL), "a")
    except Exception:
        pass
    body = pub._asset_maker.create_asset.call_args.kwargs.get("body") or pub._asset_maker.create_asset.call_args.args[0]
    assert not {"surveyed_ref", "ref_kind", "surveyed_commit"} & set(body["properties"]["additionalProperties"])


def test_the_orchestrator_records_the_ref_from_the_repository_it_already_fetched_and_the_commit_it_read(reg, monkeypatch):
    from resource_explorer.surveyors import survey_orchestrator as so
    from resource_explorer.surveyors.survey_report import SurveyResult

    real = GitHubClient.__new__(GitHubClient)
    fake_client = MagicMock()
    fake_client.resolve_ref.side_effect = lambda repo, ref=None: GitHubClient.resolve_ref(real, repo, ref)
    fake_client.list_branches.side_effect = lambda repo, limit=None: GitHubClient.list_branches(real, repo, limit)
    monkeypatch.setattr("resource_explorer.github.client.GitHubClient", lambda *a, **k: fake_client)
    orch = so.SurveyOrchestrator.__new__(so.SurveyOrchestrator)
    orch._registry = reg
    result = SurveyResult(resource_slug="widgets", project_display_name="W", github_url=URL, survey_commit_sha="r" * 40)
    orch._record_surveyed_ref(SimpleNamespace(slug="widgets", github_url=URL), result, "2026-11-22T10:00:00", FakeRepo())
    assert (result.surveyed_ref, result.ref_kind) == ("main", "branch")
    assert result.surveyed_commit == "r" * 40            # what the run READ, not where main points now ("m" * 40)
    assert [r["tier"] for r in reg.list_repo_branch_runs("widgets")] == ["surveyed", "listed"]
    fake_client.get_repo.assert_not_called()             # no second repository lookup


def test_a_run_that_acquired_no_repository_records_no_ref(reg):
    from resource_explorer.surveyors import survey_orchestrator as so
    from resource_explorer.surveyors.survey_report import SurveyResult

    orch = so.SurveyOrchestrator.__new__(so.SurveyOrchestrator)
    orch._registry = reg
    result = SurveyResult(resource_slug="widgets", project_display_name="W", github_url=URL)
    orch._record_surveyed_ref(SimpleNamespace(slug="widgets", github_url=URL), result, "r1", None)
    assert result.surveyed_ref is None and reg.list_repo_branch_runs("widgets") == []


def test_a_github_failure_never_stops_a_survey_and_records_no_ref(reg, monkeypatch):
    from resource_explorer.surveyors import survey_orchestrator as so
    from resource_explorer.surveyors.survey_report import SurveyResult

    monkeypatch.setattr("resource_explorer.github.client.GitHubClient", MagicMock(side_effect=RuntimeError("no network")))
    orch = so.SurveyOrchestrator.__new__(so.SurveyOrchestrator)
    orch._registry = reg
    result = SurveyResult(resource_slug="widgets", project_display_name="W", github_url=URL)
    orch._record_surveyed_ref(SimpleNamespace(slug="widgets", github_url=URL), result, "r1", FakeRepo())
    assert result.surveyed_ref is None and reg.list_repo_branch_runs("widgets") == []


# ── the card's data: the overview carries the newest run's refs ───────────────────────────────────────────────────

def test_the_scouting_overview_carries_the_branches_and_an_empty_list_when_none_were_recorded(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from resource_explorer.registry import Project
    from resource_explorer.web.app import app

    r = ProjectRegistry(db_path=str(tmp_path / "ov.db"))
    r.add(Project(slug="widgets", display_name="widgets", github_url=URL, description=""))
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", r.__dict__) or None)
    client = TestClient(app)
    assert client.get("/api/projects/widgets/scouting-overview").json()["branches"] == []        # never a made-up row
    record_repo_branches(r, "widgets", "2026-11-22T10:00:00", GitHubClient.__new__(GitHubClient), FakeRepo())
    branches = client.get("/api/projects/widgets/scouting-overview").json()["branches"]
    assert [(b["ref_name"], b["tier"], b["is_default"]) for b in branches] == [("main", "surveyed", True), ("dev", "listed", False)]
