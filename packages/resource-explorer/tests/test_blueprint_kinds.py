"""Brief section 4 (owner, 2026-10-07): the kind in the blueprint's name, and the selector.

"Egeria Deployment Blueprint" for egeria_git (the kind in displayName and in the `<perspective>` slot
of the SolutionBlueprint qualifiedName); "<repository> Deployment Blueprint" generically. Fake
Egeria clients that record calls; nothing live.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from resource_explorer import blueprint_kinds as bk
from resource_explorer.surveyors.arch_recovery.blueprint_materializer import BlueprintMaterializer

# Brief I: a blueprint or port is written by a queued publish: RE's daemon on the requester's behalf.
pytestmark = pytest.mark.usefixtures("as_daemon")

GUID = "11111111-1111-1111-1111-111111111111"
LEGACY_GUID = "22222222-2222-2222-2222-222222222222"


def _m(find=None):
    m = BlueprintMaterializer(platform_url="https://fake", registry=MagicMock(
        get_materialized_blueprint=MagicMock(return_value=None)))
    m._solution_architect = MagicMock()
    m._automated_curation = MagicMock()
    m._connect = MagicMock()
    m._automated_curation.get_guid_for_name.side_effect = (
        (lambda qn, **kw: find(qn)) if find else (lambda qn, **kw: []))
    m._solution_architect.get_solution_blueprint_by_guid.side_effect = lambda guid: {
        "elementHeader": {"type": {"typeName": "SolutionBlueprint"}},
        "properties": {"qualifiedName": "SolutionBlueprint::repo::egeria_git::deployment::core",
                       "displayName": "Egeria Deployment Blueprint"}}
    m._solution_architect.create_solution_blueprint.return_value = GUID
    return m


def test_the_repository_reads_as_egeria_for_egeria_git_and_generically_otherwise():
    assert bk.repo_label("egeria_git", "egeria_git") == "Egeria"
    assert bk.repo_label("kafka", "kafka") == "Kafka"
    assert bk.repo_label("my_big_repo", "My Big Repo") == "My Big Repo"
    assert bk.blueprint_kind_name("Egeria", "deployment") == "Egeria Deployment Blueprint"
    assert bk.blueprint_kind_name("Egeria", "dev") == "Egeria Build Blueprint"
    assert bk.blueprint_kind_name("Egeria", "logical") == "Egeria Logical Blueprint"
    assert bk.qualified_name_slot("deployment") == "Deployment Blueprint"


def test_the_sole_top_blueprint_takes_the_plain_name_and_others_keep_their_cluster():
    assert bk.blueprint_display_name("Egeria", "deployment", "core", sole_root=True) == "Egeria Deployment Blueprint"
    assert bk.blueprint_display_name("Egeria", "deployment", "core", sole_root=False) == "Egeria Deployment Blueprint · core"


def test_a_new_blueprint_is_named_by_kind_and_repository_never_by_its_root_cluster():
    m = _m()
    out = m.materialize_blueprint_element(
        "repo", "egeria_git", "deployment", "OMAG-Server-Platform", display_name="Egeria Deployment Blueprint")
    qn = "SolutionBlueprint::repo::egeria_git::deployment"
    assert out["qualified_name"] == qn
    props = m._solution_architect.create_solution_blueprint.call_args[0][0]["properties"]
    assert props["qualifiedName"] == qn and props["displayName"] == "Egeria Deployment Blueprint"
    assert props["identifier"] == "EGERIA-GIT-DEPLOYMENT"
    assert "OMAG" not in props["qualifiedName"]


def test_a_blueprint_written_before_the_rename_is_adopted_never_duplicated():
    legacy = "SolutionBlueprint::repo::egeria_git::deployment::core"
    m = _m(find=lambda qn: [LEGACY_GUID] if qn == legacy else [])
    out = m.materialize_blueprint_element(
        "repo", "egeria_git", "deployment", "core", display_name="Egeria Deployment Blueprint")
    assert out == {"status": "already_materialized", "guid": LEGACY_GUID, "qualified_name": legacy}
    m._solution_architect.create_solution_blueprint.assert_not_called()


# ── the selector ───────────────────────────────────────────────────────────

def _bp(perspective, members, verdict=None, parent=""):
    return {"perspective": perspective, "cluster_name": "c", "members": members, "parent": parent,
            "verdict": {"verdict": verdict} if verdict else None, "member_status": [
                {"verdict": None} for _ in members]}


def test_the_selector_draws_deployment_and_lists_the_other_two_honestly():
    rows = bk.blueprint_kind_rows(label="Egeria", blueprints=[_bp("deployment", ["a", "b", "c"])],
                                  artifact_count=4, build_files=["build.gradle"], logical_unconfirmed=None)
    dep, build, logical = rows
    assert dep["name"] == "Egeria Deployment Blueprint" and dep["state"] == "proposed" and dep["drawn"]
    assert dep["source"] == "recovered from 4 artifacts · 3 units"
    assert build["name"] == "Egeria Build Blueprint" and build["state"] == "not yet drawn" and not build["drawn"]
    assert build["source"] == "from build.gradle"
    assert logical["name"] == "Egeria Logical Blueprint" and logical["state"] == "not yet drawn"
    assert logical["source"] == "needs your confirmation of its components"      # k unknown: not given as 0


def test_the_logical_row_counts_components_awaiting_confirmation_when_it_can():
    rows = bk.blueprint_kind_rows(label="Egeria", blueprints=[], artifact_count=0, build_files=[],
                                  logical_unconfirmed=7)
    assert rows[2]["source"] == "needs your confirmation of 7 components"
    assert rows[0]["state"] == "not yet drawn" and "no deployment reading" in rows[0]["source"]
    assert rows[1]["source"] == "no build file found"


def test_an_accepted_deployment_blueprint_reads_accepted():
    rows = bk.blueprint_kind_rows(label="Egeria", blueprints=[_bp("deployment", ["a"], "accepted")],
                                  artifact_count=1, build_files=[], logical_unconfirmed=None)
    assert rows[0]["state"] == "accepted" and rows[0]["accepted"] == 1


def test_accepting_writes_the_kind_into_the_display_name_and_passes_no_identifier_for_the_first(tmp_path, monkeypatch):
    from resource_explorer.registry import Project, ProjectRegistry
    from resource_explorer.workflows import curate

    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    reg.add(Project(slug="egeria_git", display_name="egeria_git", github_url="https://github.com/o/e"))
    reg.upsert_finding("egeria_git", "architecture_blueprints", [{
        "check_name": "candidate_blueprint", "label": "core",
        "detail": {"name": "core", "perspective": "deployment", "members": [], "children": [], "parent": "",
                   "oversized": False}}], surveyed_at="2026-10-01T00:00:00")
    seen = {}

    class Fake:
        def __init__(self, registry=None):
            pass

        def materialize_blueprint_element(self, et, slug, perspective, cluster, *, display_name,
                                          oversized=False, verify_cached=False, identifier="", live_clusters=None):
            seen.update(display_name=display_name, identifier=identifier)
            return {"status": "materialized", "guid": GUID, "qualified_name": "x"}

        def blueprint_member_guids(self, guid):
            return None

        def resolve_member_guids(self, *a, **k):
            return {}, []

        def resolve_child_blueprint_guids(self, *a, **k):
            return {}, []

    monkeypatch.setattr("resource_explorer.surveyors.arch_recovery.blueprint_materializer.BlueprintMaterializer", Fake)
    monkeypatch.setattr("resource_explorer.egeria_outbox.enqueue_blueprint_members", lambda *a, **k: [])
    out = curate.materialize_blueprint_if_accepted(reg, "repo", "egeria_git", "deployment", "core", "accepted")
    assert out["status"] == "materialized"
    assert seen == {"display_name": "Egeria Deployment Blueprint", "identifier": ""}
