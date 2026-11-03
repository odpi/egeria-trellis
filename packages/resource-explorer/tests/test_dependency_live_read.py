"""dependency_analysis reads project_dependencies, which ingestion fills: a repo with rows is measured without a run."""
from __future__ import annotations

import os

import pytest

from resource_explorer.facts import FactLayer
from resource_explorer.registry import Project, ProjectRegistry


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=os.path.join(tmp_path, "t.db"))
    r.add(Project(slug="marquez", display_name="Marquez", github_url="https://github.com/x/marquez"))
    r.add(Project(slug="bare", display_name="Bare", github_url="https://github.com/x/bare"))
    return r


def test_recorded_dependencies_make_the_fact_measured_with_no_run(registry):
    registry.upsert_dependencies("marquez", [
        {"dep_name": "io.openlineage:openlineage-java", "dep_version": "1.2.0", "dep_type": "runtime",
         "ecosystem": "maven", "source_file": "build.gradle"}])
    fact = FactLayer(registry, resource_type="repo").fact("marquez", "dependency_analysis")
    assert fact.state == "measured"
    assert "1 dependency" in (fact.headline or "")


def test_a_repo_with_no_recorded_dependencies_is_still_never_run(registry):
    fact = FactLayer(registry, resource_type="repo").fact("bare", "dependency_analysis")
    assert fact.state == "never_run"
