"""TR-46 (newcomer install, Rocky 2026-11-18): `resource-explorer add <url>` printed "Authentication error. Check
GITHUB_TOKEN" and then EXITED 0, so a script or a newcomer's `&&` carried on as if the repository had been added.
A failed add now says "Nothing was added." and exits non-zero.

No database and no GitHub: the registry and the analyzer are stand-ins (ProjectRegistry() runs DDL, never in a
unit test).
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from resource_explorer.cli import wizard as wz
from resource_explorer.cli.main import app


class _Registry:
    def exists(self, slug):
        return False

    def get_by_github_url(self, url):
        return None


def _analyzer(exc):
    class A:
        def analyze(self, url, subpath=None):
            raise exc
    return A


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(wz, "ProjectRegistry", _Registry)
    return monkeypatch


@pytest.mark.parametrize("exc, words", [
    (Exception("401 Bad credentials"), "Authentication error"),
    (Exception("403 Forbidden"), "Authentication error"),
    (Exception("404 Not Found"), "Repository not found"),
    (Exception("API rate limit exceeded"), "rate limit"),
    (Exception("connection reset"), "Failed to analyze repository"),
    (ValueError("not a GitHub URL"), "Error:"),
])
def test_a_failed_add_says_so_and_exits_non_zero(offline, exc, words):
    offline.setattr(wz, "RepoAnalyzer", _analyzer(exc))
    result = CliRunner().invoke(app, ["add", "https://github.com/odpi/egeria", "--yes"])
    assert result.exit_code == 1, result.output
    assert words in result.output and "Nothing was added." in result.output


def test_choosing_no_collections_adds_nothing_and_exits_non_zero(offline, monkeypatch):
    plan = type("Plan", (), {"proposed_collections": [], "display_name": "x", "file_counts": {},
                             "github_url": "https://github.com/odpi/egeria", "description": "", "homepage_url": ""})()

    class A:
        def analyze(self, url, subpath=None):
            return plan
    offline.setattr(wz, "RepoAnalyzer", A)
    monkeypatch.setattr(wz.OnboardingWizard, "_augment_plan_for_extra_paths", lambda self, p, e, l: p)
    result = CliRunner().invoke(app, ["add", "https://github.com/odpi/egeria", "--yes"])
    assert result.exit_code == 1 and "Nothing was added." in result.output


def test_a_successful_add_exits_zero(offline, monkeypatch):
    collection = type("CT", (), {"name": "code", "description": "code"})()
    plan = type("Plan", (), {"proposed_collections": [collection], "display_name": "Egeria", "file_counts": {},
                             "github_url": "https://github.com/odpi/egeria", "description": "", "homepage_url": ""})()

    class A:
        def analyze(self, url, subpath=None):
            return plan
    added = []
    offline.setattr(wz, "RepoAnalyzer", A)
    monkeypatch.setattr(wz.OnboardingWizard, "_augment_plan_for_extra_paths", lambda self, p, e, l: p)
    monkeypatch.setattr(_Registry, "add", lambda self, project: added.append(project.slug), raising=False)
    monkeypatch.setattr(wz.OnboardingWizard, "_trigger_ingestion", lambda self, *a, **k: None)
    result = CliRunner().invoke(app, ["add", "https://github.com/odpi/egeria", "--yes"])
    assert result.exit_code == 0, result.output
    assert added == ["egeria"] and "Nothing was added." not in result.output
