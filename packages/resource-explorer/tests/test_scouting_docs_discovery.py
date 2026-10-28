"""Scouting finds a repo's homepage AND its documentation site, cheaply.

Fixture facts are DataKitchen/dataops-testgen (public): GitHub's homepage field
is the vendor site, pyproject says `Documentation = ".../testgen/what-is-testgen/"`,
`Homepage = "https://example.com"` is a leftover placeholder, and the README links
docs.datakitchen.io six times plus a shields.io badge.

Only the GitHub and HTTP boundaries are faked, as autospecs (spec_set) of the real
classes, so a renamed or mis-called method fails here instead of passing against a
loose mock. No real network.
"""
from __future__ import annotations

from unittest.mock import create_autospec

import pytest

from resource_explorer.github.client import GitHubClient
from resource_explorer.ingestion import docs_discovery as dd
from resource_explorer.ingestion.docs_probe import HttpProbe, ProbeResult
from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors.sub_surveyors.homepage import HomepageSurveyor

TESTGEN_URL = "https://github.com/DataKitchen/dataops-testgen"
DOCS = "https://docs.datakitchen.io/testgen/what-is-testgen/"

PYPROJECT = '''[project]
name = "dataops-testgen"

[project.urls]
"Homepage" = "https://example.com"
"Documentation" = "https://docs.datakitchen.io/testgen/what-is-testgen/"
"Release Notes" = "https://docs.datakitchen.io/testgen/release-notes/"
'''

README = """# DataOps TestGen
[![PyPI](https://img.shields.io/pypi/v/dataops-testgen.svg)](https://pypi.org/project/dataops-testgen/)
[Overview](https://docs.datakitchen.io/testgen/what-is-testgen/)
[Install](https://docs.datakitchen.io/testgen/install/)
[Config](https://docs.datakitchen.io/testgen/config/)
[Tests](https://docs.datakitchen.io/testgen/tests/)
[Rules](https://docs.datakitchen.io/testgen/rules/)
[More](https://docs.datakitchen.io/testgen/what-is-testgen/)
[Vendor](https://datakitchen.io)
"""


def _project(url=TESTGEN_URL):
    return Project(slug="testgen", display_name="TestGen", github_url=url, description="")


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(_project())
    return r


def _gh(files: dict, root_names=None, homepage=""):
    """An autospec GitHubClient serving `files`; zipball/clone calls fail loudly."""
    c = create_autospec(GitHubClient, instance=True, spec_set=True)
    repo = object()
    c.get_repo.return_value = repo
    c.list_root_file_names.return_value = list(root_names if root_names is not None else files)
    c.get_file_content.side_effect = lambda r, path: files.get(path)
    for forbidden in ("download_zipball", "fetch_zipball", "zipball_root",
                      "clone_git_root", "git_clone_root"):
        getattr(c, forbidden).side_effect = AssertionError(f"{forbidden} called at scouting time")
    return c


def _probe(state="reachable", status=200, final=DOCS, last_modified="Tue, 01 Oct 2026 10:00:00 GMT",
           reason=""):
    p = create_autospec(HttpProbe, instance=True, spec_set=True)
    p.probe.return_value = ProbeResult(state, status, final, last_modified, reason)
    return p


def _stats(registry, homepage):
    registry.upsert_project_stats("testgen", {"homepage": homepage}) if hasattr(
        registry, "upsert_project_stats") else None


def _run(registry, client, probe, gh_homepage="https://datakitchen.io"):
    registry.get_latest_project_stats = lambda slug: {"homepage": gh_homepage}
    s = HomepageSurveyor(registry.get("testgen"), registry, client=client, probe=probe,
                         surveyed_at="2026-10-14T10:00:00")
    return s.run()


def test_testgen_docs_from_pyproject_and_homepage_labelled_vendor(registry):
    client = _gh({"pyproject.toml": PYPROJECT, "README.md": README})
    probe = _probe()
    anns = _run(registry, client, probe)
    home, docs = anns[0], anns[1]
    assert docs.additional_properties["docs_url"] == DOCS
    assert docs.additional_properties["derivation_source"] == "pyproject_urls"
    assert "Documentation" in docs.additional_properties["source_detail"]
    assert home.additional_properties["homepage_url"] == "https://datakitchen.io"
    assert home.additional_properties["site_scope"] == "org_or_vendor"
    assert docs.additional_properties["site_scope"] == "project"
    # example.com is seen, rejected, and kept on the record
    summary = dd.read_summary(registry, "testgen")
    rejected = [c for c in summary["docs_candidates"] if c["url"] == "https://example.com"]
    assert rejected and rejected[0]["rejected"] == "placeholder"
    # both answers reached the registry
    p = registry.get("testgen")
    assert p.homepage_url == "https://datakitchen.io" and p.docs_url == DOCS
    probe.probe.assert_called_once_with(DOCS)


def test_example_com_is_never_chosen(registry):
    client = _gh({"pyproject.toml": PYPROJECT})
    anns = _run(registry, client, _probe(), gh_homepage="")
    assert anns[0].additional_properties["homepage_url"] != "https://example.com"
    assert anns[1].additional_properties["docs_url"] == DOCS


@pytest.mark.parametrize("url", [
    "https://example.com", "https://www.example.org/x", "http://example.net",
    "http://localhost:8000", "http://127.0.0.1/docs", "https://foo.example.com"])
def test_placeholders_rejected(url):
    assert dd.reject_reason(url) == "placeholder"


def test_only_github_homepage(registry):
    client = _gh({})                      # empty repo root: nothing to read
    probe = _probe()
    anns = _run(registry, client, probe, gh_homepage="https://egeria-project.org")
    assert anns[0].additional_properties["homepage_url"] == "https://egeria-project.org"
    assert anns[0].additional_properties["derivation_source"] == "github_homepage"
    assert anns[1].summary == "No documentation site found"
    assert registry.get("testgen").docs_url == ""
    probe.probe.assert_not_called()       # nothing to check, nothing requested
    assert dd.read_summary(registry, "testgen")["cue"] == dd.CUE_NONE


def test_only_readme_docs_link(registry):
    readme = "Docs: [here](https://testgen.readthedocs.io/en/latest/) and [again](https://testgen.readthedocs.io/en/latest/)\n"
    anns = _run(registry, _gh({"README.md": readme}), _probe(final="https://testgen.readthedocs.io/en/latest/"),
                gh_homepage="")
    docs = anns[1]
    assert docs.additional_properties["docs_url"] == "https://testgen.readthedocs.io/en/latest/"
    assert docs.additional_properties["derivation_source"] == "readme_link"


def test_readme_skips_badges_and_own_forge(registry):
    readme = ("![b](https://img.shields.io/x.svg)\n[repo](https://github.com/DataKitchen/dataops-testgen)\n"
              "[ci](https://travis-ci.org/x/y)\n[docs](https://docs.datakitchen.io/testgen/)\n")
    anns = _run(registry, _gh({"README.md": readme}), _probe(), gh_homepage="")
    assert anns[1].additional_properties["docs_url"] == "https://docs.datakitchen.io/testgen/"


def test_none_is_no_docs_found_not_an_error(registry):
    anns = _run(registry, _gh({"README.md": "# nothing here\n"}), _probe(), gh_homepage="")
    assert anns[1].summary == "No documentation site found"
    s = dd.read_summary(registry, "testgen")
    assert s["docs_state"] == "none_found" and s["cue"] == "no docs found"


def test_unreachable_docs(registry):
    probe = _probe("unreachable", 404, DOCS, "", "HTTP 404")
    anns = _run(registry, _gh({"pyproject.toml": PYPROJECT}), probe)
    assert anns[1].additional_properties["reachability"] == "unreachable"
    assert anns[1].additional_properties["http_status"] == "404"
    s = dd.read_summary(registry, "testgen")
    assert s["docs_state"] == "unreachable" and s["cue"] == "docs unreachable"
    assert s["docs_url"] == DOCS                       # still linked, just flagged


def test_reachable_records_status_final_url_last_modified(registry):
    probe = _probe(final="https://docs.datakitchen.io/testgen/what-is-testgen/index.html")
    _run(registry, _gh({"pyproject.toml": PYPROJECT}), probe)
    s = dd.read_summary(registry, "testgen")
    assert (s["docs_state"], s["docs_status"], s["cue"]) == ("reachable", 200, "docs ✓")
    assert s["docs_final_url"].endswith("index.html")
    assert s["docs_last_modified"].startswith("Tue, 01 Oct 2026")


def test_not_checked_is_not_unreachable(registry, monkeypatch):
    """Network disabled: the real probe makes no request and says so."""
    monkeypatch.setenv("RE_OFFLINE", "1")
    def boom(*a, **k):
        raise AssertionError("a request was made while offline")
    monkeypatch.setattr("urllib.request.urlopen", boom)
    anns = _run(registry, _gh({"pyproject.toml": PYPROJECT}), HttpProbe())
    assert anns[1].additional_properties["reachability"] == "not_checked"
    s = dd.read_summary(registry, "testgen")
    assert s["docs_state"] == "not_checked" and s["cue"] == "docs not checked"
    assert s["docs_state"] != "unreachable"


def test_unreadable_repo_is_unknown_and_keeps_previous_answer(registry):
    registry.update_project_docs_url("testgen", DOCS)
    client = _gh({})
    client.get_repo.side_effect = RuntimeError("rate limited")
    anns = _run(registry, client, _probe(), gh_homepage="")
    assert anns[1].summary == "Documentation not looked for"
    assert registry.get("testgen").docs_url == DOCS       # not clobbered by a failed read
    assert dd.read_summary(registry, "testgen", DOCS)["cue"] == "docs not checked"


def test_never_run_draws_no_cue(registry):
    assert dd.read_summary(registry, "testgen")["cue"] == ""


def test_scouting_fetches_only_small_files_no_zipball_no_clone(registry):
    client = _gh({"pyproject.toml": PYPROJECT, "README.md": README, "big.bin": "x" * 10,
                  "LICENSE": "MIT"})
    _run(registry, client, _probe())
    fetched = [c.args[1] for c in client.get_file_content.call_args_list]
    assert sorted(fetched) == ["README.md", "pyproject.toml"]
    client.list_root_file_names.assert_called_once()
    for name in ("download_zipball", "fetch_zipball", "zipball_root", "clone_git_root", "git_clone_root"):
        getattr(client, name).assert_not_called()


def test_step_is_api_cost_and_needs_no_download():
    from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY
    info = STEP_REGISTRY["repo_homepage"]
    assert info.fetch_cost == "api"
    assert not info.requires_resources


def test_scouting_definition_chains_the_step():
    import csv
    from pathlib import Path
    rows = list(csv.DictReader(open(Path(__file__).resolve().parents[1] / "docs/dr-egeria/repo_survey_types.csv")))
    scout = sorted((r for r in rows if r["survey_group"] == "RepoCoarseScout"), key=lambda r: int(r["step_order"]))
    keys = [r["step_key"] for r in scout]
    assert "repo_homepage" in keys and keys.index("repo_git_statistics") < keys.index("repo_homepage")
    doc = (Path(__file__).resolve().parents[1] / "docs/dr-egeria/survey-definitions/repo-survey-definition-scouting.md").read_text()
    assert "re_analysis_step | repo_homepage" in doc


class TestRealProbeBoundary:
    """HttpProbe itself, with only urlopen faked."""

    def _resp(self, status, headers=None, url=DOCS):
        class R:
            def __init__(s):
                s.status, s.headers, s._u = status, headers or {}, url
            def geturl(s): return s._u
            def getcode(s): return s.status
            def __enter__(s): return s
            def __exit__(s, *a): return False
        return R()

    def test_head_ok(self, monkeypatch):
        monkeypatch.delenv("RE_OFFLINE", raising=False)
        seen = []
        def fake(req, timeout=None):
            seen.append((req.get_method(), timeout))
            return self._resp(200, {"Last-Modified": "Mon"}, "https://docs.x/final")
        monkeypatch.setattr("urllib.request.urlopen", fake)
        r = HttpProbe().probe(DOCS)
        assert (r.state, r.status, r.final_url, r.last_modified) == ("reachable", 200, "https://docs.x/final", "Mon")
        assert seen == [("HEAD", 8.0)]                 # one bounded request

    def test_head_refused_falls_back_to_get(self, monkeypatch):
        import urllib.error
        monkeypatch.delenv("RE_OFFLINE", raising=False)
        calls = []
        def fake(req, timeout=None):
            calls.append(req.get_method())
            if req.get_method() == "HEAD":
                raise urllib.error.HTTPError(DOCS, 405, "no", {}, None)
            return self._resp(200)
        monkeypatch.setattr("urllib.request.urlopen", fake)
        assert HttpProbe().probe(DOCS).state == "reachable"
        assert calls == ["HEAD", "GET"]

    def test_connection_error_is_unreachable(self, monkeypatch):
        monkeypatch.delenv("RE_OFFLINE", raising=False)
        def fake(req, timeout=None):
            raise OSError("name not resolved")
        monkeypatch.setattr("urllib.request.urlopen", fake)
        r = HttpProbe().probe(DOCS)
        assert r.state == "unreachable" and r.status is None


class TestOverviewRoute:
    def test_overview_carries_docs_cue(self, registry, monkeypatch):
        from fastapi.testclient import TestClient
        monkeypatch.setattr(
            "resource_explorer.registry.ProjectRegistry.__init__",
            lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
        from resource_explorer.web.app import app
        client = TestClient(app)
        assert client.get("/api/projects/testgen/scouting-overview").json()["docs_cue"] == ""
        _run(registry, _gh({"pyproject.toml": PYPROJECT}), _probe())
        data = client.get("/api/projects/testgen/scouting-overview").json()
        assert data["docs_url"] == DOCS and data["docs_cue"] == "docs ✓" and data["docs_state"] == "reachable"
        assert data["homepage_scope"] == "org_or_vendor"


class TestScoutingScanRunsTheStep:
    def test_old_egeria_definition_without_the_step_still_gets_it(self, registry, monkeypatch):
        from resource_explorer.workflows import scouting
        monkeypatch.setattr(
            "resource_explorer.surveyors.survey_definition_executor.run_survey_definition",
            lambda *a, **k: {"errors": [], "steps": [{"re_analysis_step": "repo_health"}]})
        ran = {}
        class Orch:
            def __init__(self, reg): pass
            def run(self, slug, steps=None, fast=False):
                ran["steps"] = steps
                class R: errors = []
                return R()
        monkeypatch.setattr("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator", Orch)
        res = scouting.run_scouting_scan("testgen", registry)
        assert res.status == "ok" and ran["steps"] == ["repo_homepage"]
        assert "does not list it yet" in res.message

    def test_definition_that_has_the_step_is_not_run_twice(self, registry, monkeypatch):
        from resource_explorer.workflows import scouting
        monkeypatch.setattr(
            "resource_explorer.surveyors.survey_definition_executor.run_survey_definition",
            lambda *a, **k: {"errors": [], "steps": [{"re_analysis_step": "repo_homepage"}]})
        monkeypatch.setattr("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator",
                            lambda reg: (_ for _ in ()).throw(AssertionError("ran twice")))
        assert scouting.run_scouting_scan("testgen", registry).message == "Coarse scan complete."


class TestIngestionTargetsDocs:
    """repo_website_ingestion prefers a reachable docs_url over homepage_url."""

    def _setup(self, registry, reach_state):
        p = registry.get("testgen")
        registry.update_project_homepage("testgen", "https://datakitchen.io")
        _run(registry, _gh({"pyproject.toml": PYPROJECT}),
             _probe(reach_state, 200 if reach_state == "reachable" else 404))
        return registry.get("testgen")

    def _target(self, registry, project, monkeypatch):
        from resource_explorer.surveyors.sub_surveyors import website_ingestion as wi
        seen = {}
        import resource_explorer.ingestion.site_profile as sp
        def fake_profile(url, fetch):
            seen["url"] = url
            raise RuntimeError("stop after targeting")
        monkeypatch.setattr(sp, "profile_site", fake_profile)
        try:
            anns = wi.WebsiteIngestionSurveyor(project, registry).run()
        except RuntimeError:
            anns = []
        return seen.get("url"), anns

    def test_reachable_docs_is_the_target(self, registry, monkeypatch):
        project = self._setup(registry, "reachable")
        url, _ = self._target(registry, project, monkeypatch)
        assert url == DOCS

    def test_unreachable_docs_falls_back_to_homepage_and_says_why(self, registry, monkeypatch):
        project = self._setup(registry, "unreachable")
        url, _ = self._target(registry, project, monkeypatch)
        assert url == "https://datakitchen.io"
        m = registry.query_metrics("testgen", "website_ingestion")
        # recorded on whichever terminal path ran
        d = (m or {}).get("detail") or {}
        assert d.get("target_field") in (None, "homepage_url")

    def test_record_names_the_field_and_reason(self, registry):
        from resource_explorer.surveyors.sub_surveyors.website_ingestion import WebsiteIngestionSurveyor
        project = self._setup(registry, "reachable")
        registry.upsert_file_inventory("testgen", [("site/mkdocs.yml", 10)])   # forces a no-network terminal path
        WebsiteIngestionSurveyor(project, registry).run()
        d = registry.query_metrics("testgen", "website_ingestion")["detail"]
        assert d["target_field"] == "docs_url" and "reachable" in d["target_why"]
        assert d["url"] == DOCS
