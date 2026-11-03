"""Tests for the filesystem Survey Definition adapter (survey_definition_adapter.py)
and the richer annotation set publish_step_annotations now builds from an
enriched survey_data dict — see docs/filesystem-survey-analytics-plan.md.
"""
import os
import tempfile
from unittest.mock import MagicMock, patch

from resource_explorer.registry import FileSystemEntity
from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import EgeriaFileSystemSurveyor
from resource_explorer.surveyors.survey_definition_executor import get_adapter


def test_filesystem_adapter_registration():
    adapter = get_adapter("filesystem")
    assert adapter.entity_type == "filesystem"
    assert adapter.technology_type == "File System Directory"
    assert adapter.egeria_technology_type_name == "File System Directory"
    assert "filesystem_inventory" in adapter.re_analysis_steps
    info = adapter.re_analysis_step_info["filesystem_inventory"]
    assert "ClassificationAnnotation" in info["annotation_types"]
    assert "RequestForActionAnnotation" in info["annotation_types"]
    # 2026-08-24 — closing survey_definition_executor.py's Egeria-trigger
    # stub for this resource type (was the one resource type with no
    # other_engine_handlers at all; database already had one).
    assert "egeria" in adapter.other_engine_handlers


def test_trigger_egeria_native_survey_requires_cataloged_filesystem():
    """Mirrors database/survey_definition_adapter.py's identical guard — an
    uncataloged filesystem must raise, not silently no-op or catalog it as
    a side effect (trigger_survey_by_guid surveys an *existing* asset)."""
    import resource_explorer.surveyors.filesystem.survey_definition_adapter as fs_adapter

    fs_entity = FileSystemEntity(
        slug="uncataloged-fs", display_name="Uncataloged FS",
        local_mount_point="/tmp/nowhere", canonical_mount_point="file://nowhere",
    )
    try:
        fs_adapter._trigger_egeria_native_survey(fs_entity, MagicMock(), step=MagicMock())
        assert False, "expected RuntimeError for an uncataloged filesystem"
    except RuntimeError as exc:
        assert "no stored Egeria asset guid" in str(exc)


def test_trigger_egeria_native_survey_calls_pyegeria_with_confirmed_process_name():
    """The real, live-confirmed process qualifiedName (FileSurvey::survey-folder,
    double colon) must be passed explicitly — NOT pyegeria's own
    initiate_file_folder_survey() default ("FileSurveys:survey-folder",
    single colon, different prefix), which is the same class of bug already
    worked around for PostgreSQL. This test would have caught a regression to
    the (wrong) default.

    Since the async result-retrieval build, this handler also polls the
    triggered engine action to a terminal status and reads back the produced
    report's annotations — mocked here too; the report-attribution/
    annotation-conversion detail is covered by
    test_egeria_async_survey_result.py instead of duplicated."""
    from datetime import datetime, timedelta, timezone

    import resource_explorer.surveyors.filesystem.survey_definition_adapter as fs_adapter

    fs_entity = FileSystemEntity(
        slug="test-fs", display_name="Test FS",
        local_mount_point="/tmp/x", canonical_mount_point="file://x",
        egeria_asset_guid="fs-guid-1",
    )
    metadata_expert = MagicMock()
    metadata_expert.get_metadata_element_by_guid.return_value = {
        "elementProperties": {"propertyValueMap": {
            "activityStatus": {"symbolicName": "COMPLETED"},
        }}
    }
    with patch.object(EgeriaFileSystemSurveyor, "connect", lambda self: None):
        e = EgeriaFileSystemSurveyor()
        e._automated_curation = MagicMock()
        e._automated_curation.initiate_file_folder_survey.return_value = "engine-action-guid-1"
        e.get_survey_reports_by_guid = MagicMock(return_value=[
            {"guid": "report-guid-1", "qualified_name": "SurveyReport::x",
             "surveyed_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()},
        ])
        e.get_annotations_by_report_guid = MagicMock(return_value=[])
        with patch(
            "resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor.EgeriaFileSystemSurveyor",
            return_value=e,
        ), patch(
            "resource_explorer.surveyors.egeria_async_survey_result._get_clients",
            return_value=(MagicMock(), metadata_expert),
        ):
            result = fs_adapter._trigger_egeria_native_survey(fs_entity, MagicMock(), step=MagicMock())

    assert result["status"] == "ok"
    assert result["engine_action_guid"] == "engine-action-guid-1"
    assert result["report_guid"] == "report-guid-1"
    e._automated_curation.initiate_file_folder_survey.assert_called_once_with(
        file_folder_guid="fs-guid-1", survey_name="FileSurvey::survey-folder",
    )


def test_run_filesystem_inventory_and_publish_roundtrip():
    """The adapter's runner + publish functions, wired together the same way
    the executor calls them, against a real temp directory with a malformed
    CSV — exercises the full path from walk -> annotations -> publish call."""
    import resource_explorer.surveyors.filesystem.survey_definition_adapter as fs_adapter

    with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmpdir:
        with open(os.path.join(tmpdir, "good.csv"), "w") as f:
            f.write("a,b\n1,2\n3,4\n")
        with open(os.path.join(tmpdir, "bad.csv"), "w") as f:
            f.write("header\nx\nx\nx\nx\na,b,c,d\n")  # triggers a tokenizing error
        with open(os.path.join(tmpdir, "mystery.xyz123"), "w") as f:
            f.write("unclassifiable")

        fs_entity = FileSystemEntity(
            slug="test-fs",
            display_name="Test FS",
            local_mount_point=tmpdir,
            canonical_mount_point="file://shared-nfs/test-fs",
            egeria_asset_guid="fs-guid-1",
        )
        registry = MagicMock()
        # The probe row that proves Egeria could not reach the path (Files step 2): the walk leads and may publish.
        registry.get_latest_reachability.return_value = {
            "outcome": "re_only", "probed_at": "2026-10-20T10:00:00+00:00", "error_detail": "", "error_code": ""}
        registry.get_filesystem.return_value = fs_entity
        from resource_explorer import file_reach

        with patch.object(file_reach, "probe_filesystem",
                          lambda slug, reg, **kw: file_reach.fork_for(reg, fs_entity)):
            step_output = fs_adapter._run_filesystem_inventory(fs_entity, registry)
        survey_data = step_output["survey_data"]
        assert survey_data["total_files"] == 3
        # Files step 2, caution 1: the walk opens no file's contents, so nothing is schema-profiled here
        # (`file_profiles` is its own later step). The malformed CSV is a file with a name, a size and a time.
        assert survey_data["profiling_errors"] == []
        assert survey_data["classification"]["unclassified_count"] >= 1

        captured_annotations = []
        with patch.object(EgeriaFileSystemSurveyor, "connect", lambda self: None), \
             patch.object(EgeriaFileSystemSurveyor, "_create_annotations",
                           lambda self, annotations, guid, slug, ts: captured_annotations.extend(annotations)):
            e = EgeriaFileSystemSurveyor()
            e._asset_maker = MagicMock()
            e._asset_maker.create_asset.return_value = "report-guid-1"

            with patch(
                "resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor.EgeriaFileSystemSurveyor",
                return_value=e,
            ):
                report_guid = fs_adapter._publish(fs_entity, [step_output], survey_data["surveyed_at"], registry)

        assert report_guid == "report-guid-1"

        # Egeria's own folder-survey annotation names (SurveyFolderAnnotationType), verbatim.
        names = {a.annotation_type_name for a in captured_annotations}
        assert "Capture File Counts" in names
        assert "Profile File Extensions" in names
        assert "Profile File Types" in names
        assert "Missing File Reference Data" in names
        assert "Profile File Names to External Log" not in names
        assert not any("schema-profiled" in a.summary for a in captured_annotations)
        body = e._asset_maker.create_asset.call_args.kwargs["body"]
        assert "surveyed by Resource Explorer · Egeria could not reach the path" in body["properties"]["description"]


def test_publish_step_annotations_requires_cataloged_filesystem():
    """publish_step_annotations must not silently no-op or auto-catalog — a
    filesystem with no known Egeria GUID and nothing findable by canonical
    mount point should raise, not create a new asset as a side effect."""
    fs_entity = FileSystemEntity(
        slug="uncataloged-fs",
        display_name="Uncataloged FS",
        local_mount_point="/tmp/nowhere",
        canonical_mount_point="file://nowhere",
    )
    minimal_survey_data = {
        "total_files": 0, "total_data_files": 0, "total_size_bytes": 0, "total_size": "0 B",
        "formats": {}, "inventory": [], "classification": {}, "inaccessible_files": [], "profiling_errors": [],
    }

    with patch.object(EgeriaFileSystemSurveyor, "connect", lambda self: None), \
         patch.object(EgeriaFileSystemSurveyor, "_find_element_guid", lambda self, name, type_name=None, **kw: ""):
        e = EgeriaFileSystemSurveyor()
        try:
            e.publish_step_annotations(fs_entity, minimal_survey_data, MagicMock())
            assert False, "expected EgeriaFileSystemSurveyorError"
        except Exception as exc:
            assert "is not yet published to Egeria" in str(exc)
