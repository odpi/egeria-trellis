"""Resource Explorer's own file-system survey report carries the derived steps' results and a declared-measures hook.

The project owner's ruling (2026-10-28): a file system's chosen measures publish through RE's own file-system survey
report, not the root-folder publish; the derived steps' annotations ride in that report. Here the five derived steps
(Files build steps 6 and 7) are installed the way pip would, run over a real walk on a temp SQLite registry, and the
report is published through the real publisher with only the Egeria boundary faked (AssetMaker autospec, the
annotation creates, the report read). Nothing contacts Egeria.

Pinned: the derived results ride AFTER the report's own annotations (whose indices do not move), as the type each
measure declares, counts only; a "not measured" result, one derived from another walk, and a request for action with
nothing to request are not sent and each says why; every sent annotation carries its qualifiedName and the GUID the
report read found ("" with why when the read could not be made); the declared-measures hook appends after the derived
results and reads back the same way, and by default appends nothing.
"""
from __future__ import annotations

import getpass
import os
from pathlib import Path
from unittest.mock import create_autospec

import pytest

from resource_explorer.registry import FileSystemEntity, ProjectRegistry

FS_STEPS = ("fs_activity", "fs_change_rates", "fs_duplicates", "fs_layout_signals", "fs_permissions")


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "report.db"))


@pytest.fixture
def fs_installed(tmp_path, monkeypatch):
    from tests.library_step_install import _entries

    from resource_explorer.surveyors import survey_step_host

    for entry in reversed(_entries(tmp_path, FS_STEPS)):
        monkeypatch.syspath_prepend(entry)
    survey_step_host.refresh("filesystem")
    yield
    monkeypatch.undo()
    survey_step_host.refresh("filesystem")


def _tree(tmp_path) -> Path:
    root = tmp_path / "tree"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir()
    (root / "a" / "x.txt").write_text("same")
    (root / "b" / "y.txt").write_text("same")
    (root / "a" / "pyproject.toml").write_text("[project]\n")
    (root / ".env").write_text("K=V")
    os.chmod(root / ".env", 0o644)
    return root


def _register(registry, root, slug="tree"):
    registry.register_filesystem(FileSystemEntity(slug=slug, display_name=slug, local_mount_point=str(root),
                                                  egeria_asset_guid="fs-guid-1"))
    return registry.get_filesystem(slug)


def _walk(registry, fs) -> dict:
    from resource_explorer.surveyors.filesystem import walk_extensions as we
    from resource_explorer.surveyors.filesystem.local_filesystem_surveyor import LocalFileSystemSurveyor

    sd = LocalFileSystemSurveyor(fs, registry).run(profile_contents=False, walk_extensions=True)
    we.attach_extensions(fs, registry, sd, led_by="re")
    sd.pop("walk_files", None)
    registry.add_filesystem_survey(fs.slug, sd["surveyed_at"], sd)
    return sd


def _derive(registry):
    from resource_explorer.workflows.analysis import run_filesystem_analysis

    for a in FS_STEPS:
        assert run_filesystem_analysis("tree", a, registry=registry).status == "ok", a


def _fake(monkeypatch, *, readable=True, missing=()):
    """The real publisher with only Egeria faked: AssetMaker (autospec), the annotation creates and the report read.
    `missing`: indices the report read does not find; `readable=False`: the report cannot be read at all."""
    from pyegeria import AssetMaker

    from resource_explorer.surveyors.filesystem import egeria_filesystem_surveyor as mod
    from resource_explorer.surveyors.survey_report import annotation_qualified_name

    maker = create_autospec(AssetMaker, instance=True, spec_set=True)
    maker.create_asset.return_value = "report-guid-7"
    maker.get_asset_by_guid.return_value = {"elementHeader": {"guid": "report-guid-7", "type": {"typeName": "SurveyReport"}},
                                            "properties": {"qualifiedName": "q"}}
    seen: dict = {}

    class Fake(mod.EgeriaFileSystemSurveyor):
        def __init__(self, **kw):
            super().__init__()
            self.connect = lambda: None
            self._asset_maker = maker

        def _create_annotations(self, anns, report_guid, slug, at):
            seen.update(anns=list(anns), slug=slug, at=at)
            return [f"g{i}" for i in range(len(anns))]

        def _report_annotations(self, report_guid):
            if not readable:
                return None
            prefix = f"Annotation::FileSystem::{seen['slug']}::{seen['at']}"
            return [{"qualified_name": annotation_qualified_name(prefix, i, a), "guid": f"g{i}"}
                    for i, a in enumerate(seen["anns"]) if i not in missing]

    return Fake(), seen


def _setup(registry, tmp_path, derive=True):
    fs = _register(registry, _tree(tmp_path))
    sd = _walk(registry, fs)
    if derive:
        _derive(registry)
    return fs, sd


# ── the derived results ride after the report's own ──

def test_the_derived_results_for_this_walk_ride_after_the_reports_own_as_their_declared_types(
        fs_installed, registry, tmp_path, monkeypatch):
    fs, sd = _setup(registry, tmp_path)
    surveyor, seen = _fake(monkeypatch)
    proof = surveyor.publish_step_annotations(fs, sd, registry)
    derived = proof["derived"]
    assert derived["state"] == "sent"
    start = len(seen["anns"]) - len(derived["sent"])
    own, rode = seen["anns"][:start], seen["anns"][start:]
    assert own and all(not a.analysis_step.startswith("fs_") for a in own), "the report's own come first"
    assert [a.analysis_step for a in rode] == [r["step"] for r in derived["sent"]]
    types = {r["measure"]: type(a).__name__ for r, a in zip(derived["sent"], rode)}
    assert types["fs_duplicates"] == "ResourceMeasureAnnotation"
    assert types["fs_layout_signals"] == "ResourceProfileAnnotation"
    assert types["fs_permissions"] == "ResourceProfileAnnotation"
    # .env is world-readable and secret-named: the request for action rides
    assert types["world_readable_sensitive"] == "RequestForActionAnnotation"
    layout = rode[[r["measure"] for r in derived["sent"]].index("fs_layout_signals")]
    assert layout.value_count["Project Manifests"] == 1 and isinstance(layout.value_count["Files Walked"], int)
    assert layout.additional_properties["derivedFromWalk"] == sd["walk_extensions"]["run_id"]
    assert layout.additional_properties["surveyor"] == "Resource Explorer"


def test_not_measured_results_are_not_sent_and_say_why(fs_installed, registry, tmp_path, monkeypatch):
    fs, sd = _setup(registry, tmp_path)
    surveyor, _ = _fake(monkeypatch)
    proof = surveyor.publish_step_annotations(fs, sd, registry)
    not_sent = {r["measure"]: r["why"] for r in proof["derived"]["not_sent"]}
    # one walk only: change rates cannot be measured; no other file system: nothing to compare against
    assert not_sent["fs_change_rates"].startswith("not measured (not established)")
    assert not_sent["fs_duplicates_elsewhere"].startswith("not measured")
    assert "fs_change_rates" not in {r["measure"] for r in proof["derived"]["sent"]}


def test_results_derived_from_another_walk_are_not_sent(fs_installed, registry, tmp_path, monkeypatch):
    fs, _ = _setup(registry, tmp_path)
    newer = _walk(registry, fs)                       # walked again, not derived again
    surveyor, seen = _fake(monkeypatch)
    proof = surveyor.publish_step_annotations(fs, newer, registry)
    assert proof["derived"]["sent"] == [] and proof["derived"]["state"] == "none_to_send"
    assert all("not this one" in r["why"] or r["why"].startswith("not measured") for r in proof["derived"]["not_sent"])
    assert not any(a.analysis_step.startswith("fs_") for a in seen["anns"])


def test_no_derived_run_sends_none_and_says_none_stored(fs_installed, registry, tmp_path, monkeypatch):
    fs, sd = _setup(registry, tmp_path, derive=False)
    surveyor, _ = _fake(monkeypatch)
    proof = surveyor.publish_step_annotations(fs, sd, registry)
    assert proof["derived"] == {"state": "none_stored", "sent": [], "not_sent": []}


def test_the_derived_results_are_counts_only(fs_installed, registry, tmp_path, monkeypatch):
    fs, sd = _setup(registry, tmp_path)
    surveyor, seen = _fake(monkeypatch)
    surveyor.publish_step_annotations(fs, sd, registry)
    rode = [a for a in seen["anns"] if a.analysis_step.startswith("fs_")]
    dumped = " ".join(repr(a.__dict__) for a in rode)
    for name in ("x.txt", "y.txt", ".env", "pyproject.toml", str(tmp_path), getpass.getuser()):
        assert name not in dumped, name


# ── read back by name ──

def test_each_sent_result_carries_its_qualified_name_and_the_guid_the_report_read_found(
        fs_installed, registry, tmp_path, monkeypatch):
    fs, sd = _setup(registry, tmp_path)
    surveyor, seen = _fake(monkeypatch, missing=())
    proof = surveyor.publish_step_annotations(fs, sd, registry)
    at = seen["at"]
    dup = next(r for r in proof["derived"]["sent"] if r["measure"] == "fs_duplicates")
    assert dup["qualified_name"] == f"Annotation::FileSystem::tree::{at}::fs_duplicates::tree"
    assert dup["guid"].startswith("g") and dup["why"] == ""
    assert proof["read_back_guids"] and len(proof["read_back_guids"]) == len(seen["anns"])
    assert proof["report_guid"] == "report-guid-7"


def test_an_annotation_the_read_did_not_find_has_no_guid_and_says_so(fs_installed, registry, tmp_path, monkeypatch):
    fs, sd = _setup(registry, tmp_path)
    surveyor, seen = _fake(monkeypatch, missing=(10_000,))
    # find the index of the first derived annotation once, then fake its absence
    proof = surveyor.publish_step_annotations(fs, sd, registry)
    first = len(seen["anns"]) - len(proof["derived"]["sent"])
    surveyor, seen = _fake(monkeypatch, missing=(first,))
    proof = surveyor.publish_step_annotations(fs, sd, registry)
    r = proof["derived"]["sent"][0]
    assert r["guid"] == "" and r["why"] == "not found on the report when it was read back"


def test_an_unreadable_report_leaves_every_guid_blank_with_why(fs_installed, registry, tmp_path, monkeypatch):
    fs, sd = _setup(registry, tmp_path)
    surveyor, _ = _fake(monkeypatch, readable=False)
    proof = surveyor.publish_step_annotations(fs, sd, registry)
    assert proof["read_back_guids"] is None
    assert proof["derived"]["sent"] and all(r["guid"] == "" and r["why"] == "the report could not be read back"
                                            for r in proof["derived"]["sent"])


# ── the declared-measures hook (for Curate's publish table) ──

def test_declared_measures_append_after_the_derived_results_and_read_back(fs_installed, registry, tmp_path, monkeypatch):
    from resource_explorer.surveyors.survey_report import ResourceMeasureAnnotation

    fs, sd = _setup(registry, tmp_path)
    surveyor, seen = _fake(monkeypatch)
    chosen = ResourceMeasureAnnotation(summary="files: 4", analysis_step="filesystem_inventory",
                                       resource_properties={"files": "4"},
                                       check_name="declared_measure.filesystem_inventory.files", item_key="tree")
    proof = surveyor.publish_step_annotations(fs, sd, registry, declared_measures={
        "state": "sent", "rows": [{"analysis_id": "filesystem_inventory", "measure": "files", "value": 4}],
        "annotations": [chosen]})
    assert seen["anns"][-1] is chosen, "after the derived results"
    sent = proof["declared_measures"]["sent"]
    assert proof["declared_measures"]["state"] == "sent" and len(sent) == 1
    assert sent[0]["analysis_id"] == "filesystem_inventory" and sent[0]["guid"]
    assert sent[0]["qualified_name"].endswith("::declared_measure.filesystem_inventory.files::tree")


def test_by_default_nothing_is_appended_for_declared_measures(fs_installed, registry, tmp_path, monkeypatch):
    fs, sd = _setup(registry, tmp_path)
    surveyor, _ = _fake(monkeypatch)
    proof = surveyor.publish_step_annotations(fs, sd, registry)
    assert proof["declared_measures"] == {"state": "not_asked", "sent": []}


# ── the card reads what the publish proved ──

def test_after_a_publish_the_derived_view_says_which_results_are_in_egeria(fs_installed, registry, tmp_path, monkeypatch):
    from resource_explorer.surveyors.filesystem.walk_derived import derived_view

    fs, sd = _setup(registry, tmp_path)
    before = {r["measure"]: r for s in derived_view(registry, "tree")["steps"] for r in s["results"]}
    assert all(r["published"] is None for r in before.values()), "never published: kept here only"
    surveyor, _ = _fake(monkeypatch, readable=True)
    surveyor.publish_step_annotations(fs, sd, registry)
    after = {r["measure"]: r for s in derived_view(registry, "tree")["steps"] for r in s["results"]}
    assert after["fs_duplicates"]["published"]["report_guid"] == "report-guid-7"
    assert after["fs_duplicates"]["published"]["guid"].startswith("g")
    assert after["fs_change_rates"]["published"] is None, "not measured: not sent, so not in Egeria"


def test_a_publish_whose_report_could_not_be_read_proves_nothing_published(fs_installed, registry, tmp_path, monkeypatch):
    from resource_explorer.surveyors.filesystem.walk_derived import derived_view

    fs, sd = _setup(registry, tmp_path)
    surveyor, _ = _fake(monkeypatch, readable=False)
    surveyor.publish_step_annotations(fs, sd, registry)
    assert all(r["published"] is None for s in derived_view(registry, "tree")["steps"] for r in s["results"])
