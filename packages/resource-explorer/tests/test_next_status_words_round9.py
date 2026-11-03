"""Status words, round 9: a library step's run row names its declared stage, its executor and its duration."""
import json

import pytest

from resource_explorer.activity_logger import log_analysis_run
from resource_explorer.surveyors import analysis_catalog_reader as reader


@pytest.fixture
def fs_activity_ran(tmp_path, monkeypatch):
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors import survey_step_host
    from tests.library_step_install import _entries
    from tests.test_files_derived_steps_host import _register, _tree, _walk

    for entry in reversed(_entries(tmp_path, ("fs_activity",))):
        monkeypatch.syspath_prepend(entry)
    survey_step_host.refresh("filesystem")
    registry = ProjectRegistry(db_path=str(tmp_path / "fs.db"))
    _walk(registry, _register(registry, _tree(tmp_path)))
    yield registry
    monkeypatch.undo()
    survey_step_host.refresh("filesystem")


def test_the_run_row_uses_the_stage_the_catalog_declares(fs_activity_ran):
    registry = fs_activity_ran
    declared = next(a["intent"] for a in reader.get_analyses("filesystem", include_egeria_live=False)
                    if a["id"] == "fs_activity")
    assert declared != "assessment"
    aid = log_analysis_run(registry, "filesystem", "tree", "tree", "running", "x", "fs_activity")
    assert registry.get_activity(aid)["intent"] == declared


def test_the_finished_row_carries_executor_and_duration(fs_activity_ran):
    from resource_explorer.workflows.analysis import execute_and_record_filesystem_analysis

    registry = fs_activity_ran
    aid = log_analysis_run(registry, "filesystem", "tree", "tree", "running", "x", "fs_activity")
    execute_and_record_filesystem_analysis("tree", "fs_activity", aid, registry=registry)
    detail = json.loads(registry.get_activity(aid)["detail"])
    assert detail["executor"] in ("local", "prefect")
    assert isinstance(detail["duration_s"], float)


class _Registry:
    def __init__(self, row):
        self.row = row

    def get_compile(self, _id):
        return self.row


def _entry(manifest):
    from resource_explorer.journal import derive_turn_entry

    return derive_turn_entry(_Registry({"manifest": manifest}), question="q", answer="a", compile_id="c" * 32,
                             investigation="inv", resource_slug="r")


def test_the_journal_carries_the_compiles_declared_as_ofs_and_says_when_they_differ():
    manifest = {"packed": [{"key": "fs_activity"}, {"key": "fs_permissions"}],
                "declared": {"fs_activity": {"as_of": "2026-10-28T10:00"}, "fs_permissions": {"as_of": "2026-10-29T09:00"}}}
    out = _entry(manifest)
    assert out["turn"]["as_ofs"] == {"fs_activity": "2026-10-28T10:00", "fs_permissions": "2026-10-29T09:00"}
    assert "As of: differs by section: fs_activity 2026-10-28T10:00; fs_permissions 2026-10-29T09:00" in out["body"]
    assert "not recorded" not in out["body"].split("- As of:")[1].split("\n")[0]


def test_one_agreed_as_of_is_said_once_and_none_is_still_not_recorded():
    same = _entry({"packed": [{"key": "a"}], "declared": {"a": {"as_of": "2026-10-28"}}})
    assert "- As of: 2026-10-28\n" in same["body"]
    assert "- As of: not recorded\n" in _entry({"packed": [{"key": "a"}]})["body"]


def test_the_journal_keeps_the_row_ids_each_section_was_built_from():
    out = _entry({"packed": [{"key": "fs_activity"}],
                  "rows_seen": {"fs_activity": {"step_annotations": ["step_annotations:filesystem/t/fs_activity/r1/"]}}})
    assert out["turn"]["rows_seen"] == {"fs_activity": ["step_annotations:filesystem/t/fs_activity/r1/"]}
    assert "- Rows: fs_activity: step_annotations:filesystem/t/fs_activity/r1/" in out["body"]


def test_a_measure_record_names_its_stored_row(fs_activity_ran):
    from resource_explorer import declared_context as dc
    from resource_explorer.workflows.analysis import run_filesystem_analysis

    registry = fs_activity_ran
    assert run_filesystem_analysis("tree", "fs_activity", registry=registry).status == "ok"
    dec = dc.declarations("filesystem")["fs_activity"]
    records = dc.measure_records(registry, "filesystem", "tree", "fs_activity", dec, "fs_activity")
    assert records and all(r["row_id"].startswith("step_annotations:filesystem/tree/fs_activity") for r in records.values())


class _CveRegistry:
    def __init__(self, metrics):
        self._metrics = metrics

    def query_findings(self, slug, kind):
        return []

    def query_metrics(self, slug, kind):
        return self._metrics


def _cve(checked, recorded=65, advisories=0):
    from resource_explorer.surveyors.repo_survey_definition_adapter import _cve_scan_results

    return _cve_scan_results(_CveRegistry({"checked": checked, "advisories": advisories,
                                            "detail": {"scanned": True, "recorded": recorded}}), "docling")


def test_a_cve_scan_that_queried_nothing_is_not_established_not_answered():
    from resource_explorer.facts import FactLayer, NOT_ESTABLISHED

    out = _cve(0)
    assert out["_status"]["state"] == "not_established"
    state = FactLayer._state_for(out, {})
    assert state == NOT_ESTABLISHED


def test_a_cve_scan_that_queried_something_keeps_its_state():
    assert "_status" not in _cve(40)
    assert "_status" not in _cve(0, advisories=3)
