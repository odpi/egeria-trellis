import pytest
from unittest.mock import MagicMock, patch

from resource_explorer.registry import FileSystemEntity
from resource_explorer.surveyors.survey_definition_executor import get_adapter

import resource_explorer.surveyors.filesystem.survey_definition_adapter as fs_adapter


class TestFilesystemChainedStepsRunnable:
    def test_file_profiles_in_re_analysis_steps(self):
        adapter = get_adapter("filesystem")
        assert "file_profiles" in adapter.re_analysis_steps
        assert "file_profiles" in adapter.re_analysis_step_info

    def test_narrative_docs_in_re_analysis_steps(self):
        adapter = get_adapter("filesystem")
        assert "narrative_docs" in adapter.re_analysis_steps
        assert "narrative_docs" in adapter.re_analysis_step_info

    def test_file_data_class_match_in_re_analysis_steps(self):
        adapter = get_adapter("filesystem")
        assert "file_data_class_match" in adapter.re_analysis_steps
        assert "file_data_class_match" in adapter.re_analysis_step_info

    def test_file_profiles_has_non_empty_description(self):
        adapter = get_adapter("filesystem")
        assert adapter.re_analysis_step_info["file_profiles"]["description"]

    def test_narrative_docs_has_non_empty_description(self):
        adapter = get_adapter("filesystem")
        assert adapter.re_analysis_step_info["narrative_docs"]["description"]

    def test_file_data_class_match_has_non_empty_description(self):
        adapter = get_adapter("filesystem")
        assert adapter.re_analysis_step_info["file_data_class_match"]["description"]

    def test_file_profiles_runner_calls_press_and_returns_summary(self):
        entity = FileSystemEntity(
            slug="fs", display_name="FS",
            local_mount_point="/tmp/nowhere", canonical_mount_point="file://nowhere"
        )
        registry = MagicMock()
        summary_dict = {"files_scanned": 5, "size_bytes": 1024}

        with patch(
            "resource_explorer.surveyors.filesystem.file_profiles.run_file_profiles"
        ) as mock_run:
            mock_run.return_value = summary_dict

            result = fs_adapter._run_file_profiles(entity, registry)

            mock_run.assert_called_once()
            args, kwargs = mock_run.call_args
            assert args == (entity, registry)
            assert callable(kwargs["port_factory"])
            assert result == {"step": "file_profiles", **summary_dict}

    def test_narrative_docs_runner_calls_press_and_returns_summary(self):
        entity = FileSystemEntity(
            slug="fs", display_name="FS",
            local_mount_point="/tmp/nowhere", canonical_mount_point="file://nowhere"
        )
        registry = MagicMock()
        summary_dict = {"docs_found": 3, "pages_processed": 12}
        expected_result = {"step": "narrative_docs", **summary_dict}

        with patch(
            "resource_explorer.surveyors.filesystem.narrative_docs.run_narrative_docs"
        ) as mock_run:
            mock_run.return_value = summary_dict

            result = fs_adapter._run_narrative_docs(entity, registry)

            mock_run.assert_called_once_with(entity, registry)
            assert result == expected_result

    def test_file_data_class_match_runner_calls_press_and_returns_summary(self):
        entity = FileSystemEntity(
            slug="fs", display_name="FS",
            local_mount_point="/tmp/nowhere", canonical_mount_point="file://nowhere"
        )
        registry = MagicMock()
        summary_dict = {"columns": ["class", "confidence"], "established": True}
        expected_result = {"step": "file_data_class_match", **summary_dict}

        with patch(
            "resource_explorer.surveyors.filesystem.file_class_match.run_file_class_match"
        ) as mock_run:
            mock_run.return_value = summary_dict

            result = fs_adapter._run_file_data_class_match(entity, registry)

            mock_run.assert_called_once_with(entity, registry)
            assert result == expected_result

    def test_file_profiles_runner_propagates_nothing_to_profile(self):
        entity = FileSystemEntity(
            slug="fs", display_name="FS",
            local_mount_point="/tmp/nowhere", canonical_mount_point="file://nowhere"
        )
        registry = MagicMock()

        from resource_explorer.surveyors.filesystem.file_profiles import NothingToProfile

        with patch(
            "resource_explorer.surveyors.filesystem.file_profiles.run_file_profiles"
        ) as mock_run:
            mock_run.side_effect = NothingToProfile("no walk")

            with pytest.raises(NothingToProfile, match="no walk"):
                fs_adapter._run_file_profiles(entity, registry)

    def test_file_profiles_runner_propagates_already_running(self):
        entity = FileSystemEntity(
            slug="fs", display_name="FS",
            local_mount_point="/tmp/nowhere", canonical_mount_point="file://nowhere"
        )
        registry = MagicMock()

        from resource_explorer.surveyors.filesystem.file_profiles import AlreadyRunning

        with patch(
            "resource_explorer.surveyors.filesystem.file_profiles.run_file_profiles"
        ) as mock_run:
            mock_run.side_effect = AlreadyRunning("already started")

            with pytest.raises(AlreadyRunning, match="already started"):
                fs_adapter._run_file_profiles(entity, registry)

    def test_file_profiles_runner_propagates_claim_lost(self):
        entity = FileSystemEntity(
            slug="fs", display_name="FS",
            local_mount_point="/tmp/nowhere", canonical_mount_point="file://nowhere"
        )
        registry = MagicMock()

        from resource_explorer.surveyors.filesystem.file_profiles import ClaimLost

        with patch(
            "resource_explorer.surveyors.filesystem.file_profiles.run_file_profiles"
        ) as mock_run:
            mock_run.side_effect = ClaimLost("lost claim")

            with pytest.raises(ClaimLost, match="lost claim"):
                fs_adapter._run_file_profiles(entity, registry)

    def test_narrative_docs_runner_propagates_nothing_to_read(self):
        entity = FileSystemEntity(
            slug="fs", display_name="FS",
            local_mount_point="/tmp/nowhere", canonical_mount_point="file://nowhere"
        )
        registry = MagicMock()

        from resource_explorer.surveyors.filesystem.narrative_docs import NothingToRead

        with patch(
            "resource_explorer.surveyors.filesystem.narrative_docs.run_narrative_docs"
        ) as mock_run:
            mock_run.side_effect = NothingToRead("no docs")

            with pytest.raises(NothingToRead, match="no docs"):
                fs_adapter._run_narrative_docs(entity, registry)

    def test_narrative_docs_runner_propagates_already_running(self):
        entity = FileSystemEntity(
            slug="fs", display_name="FS",
            local_mount_point="/tmp/nowhere", canonical_mount_point="file://nowhere"
        )
        registry = MagicMock()

        from resource_explorer.surveyors.filesystem.narrative_docs import AlreadyRunning

        with patch(
            "resource_explorer.surveyors.filesystem.narrative_docs.run_narrative_docs"
        ) as mock_run:
            mock_run.side_effect = AlreadyRunning("already running")

            with pytest.raises(AlreadyRunning, match="already running"):
                fs_adapter._run_narrative_docs(entity, registry)

    def test_narrative_docs_runner_propagates_not_reachable(self):
        entity = FileSystemEntity(
            slug="fs", display_name="FS",
            local_mount_point="/tmp/nowhere", canonical_mount_point="file://nowhere"
        )
        registry = MagicMock()

        from resource_explorer.surveyors.filesystem.narrative_docs import NotReachable

        with patch(
            "resource_explorer.surveyors.filesystem.narrative_docs.run_narrative_docs"
        ) as mock_run:
            mock_run.side_effect = NotReachable("unreachable")

            with pytest.raises(NotReachable, match="unreachable"):
                fs_adapter._run_narrative_docs(entity, registry)

    def test_narrative_docs_runner_propagates_not_stored(self):
        entity = FileSystemEntity(
            slug="fs", display_name="FS",
            local_mount_point="/tmp/nowhere", canonical_mount_point="file://nowhere"
        )
        registry = MagicMock()

        from resource_explorer.surveyors.filesystem.narrative_docs import NotStored

        with patch(
            "resource_explorer.surveyors.filesystem.narrative_docs.run_narrative_docs"
        ) as mock_run:
            mock_run.side_effect = NotStored("not stored")

            with pytest.raises(NotStored, match="not stored"):
                fs_adapter._run_narrative_docs(entity, registry)

    def test_file_data_class_match_runner_propagates_nothing_to_match(self):
        entity = FileSystemEntity(
            slug="fs", display_name="FS",
            local_mount_point="/tmp/nowhere", canonical_mount_point="file://nowhere"
        )
        registry = MagicMock()

        from resource_explorer.surveyors.filesystem.file_class_match import NothingToMatch

        with patch(
            "resource_explorer.surveyors.filesystem.file_class_match.run_file_class_match"
        ) as mock_run:
            mock_run.side_effect = NothingToMatch("no match")

            with pytest.raises(NothingToMatch, match="no match"):
                fs_adapter._run_file_data_class_match(entity, registry)

    def test_file_data_class_match_runner_propagates_already_running(self):
        entity = FileSystemEntity(
            slug="fs", display_name="FS",
            local_mount_point="/tmp/nowhere", canonical_mount_point="file://nowhere"
        )
        registry = MagicMock()

        from resource_explorer.surveyors.filesystem.file_class_match import AlreadyRunning

        with patch(
            "resource_explorer.surveyors.filesystem.file_class_match.run_file_class_match"
        ) as mock_run:
            mock_run.side_effect = AlreadyRunning("already running")

            with pytest.raises(AlreadyRunning, match="already running"):
                fs_adapter._run_file_data_class_match(entity, registry)

    def test_file_data_class_match_runner_propagates_not_reachable(self):
        entity = FileSystemEntity(
            slug="fs", display_name="FS",
            local_mount_point="/tmp/nowhere", canonical_mount_point="file://nowhere"
        )
        registry = MagicMock()

        from resource_explorer.surveyors.filesystem.file_class_match import NotReachable

        with patch(
            "resource_explorer.surveyors.filesystem.file_class_match.run_file_class_match"
        ) as mock_run:
            mock_run.side_effect = NotReachable("unreachable")

            with pytest.raises(NotReachable, match="unreachable"):
                fs_adapter._run_file_data_class_match(entity, registry)


class TestRunThroughTheExecutor:
    """R1: the chained steps run through SurveyDefinitionExecutor and call the same presses, instead of the executor
    answering "unknown step"."""

    PRESSES = {
        "file_profiles": "resource_explorer.surveyors.filesystem.file_profiles.run_file_profiles",
        "narrative_docs": "resource_explorer.surveyors.filesystem.narrative_docs.run_narrative_docs",
        "file_data_class_match": "resource_explorer.surveyors.filesystem.file_class_match.run_file_class_match",
    }

    def _run(self, step_key, press_side_effect=None):
        from resource_explorer.surveyors.survey_definition_executor import PUBLISH_NOT_CHOSEN, SurveyDefinitionExecutor
        from resource_explorer.surveyors.survey_definition_reader import SurveyDefinition, SurveyStep

        entity = FileSystemEntity(slug="fs", display_name="FS", local_mount_point="/tmp/nowhere",
                                  canonical_mount_point="file://nowhere")
        registry = MagicMock()
        registry.get_survey_definition_guid.return_value = None
        registry.get_filesystem.return_value = entity
        definition = SurveyDefinition(
            process_guid="proc-fs", display_name="FS chain", qualified_name="GovActionProcess::FsChain",
            supported_technology_type="File System Directory",
            steps=[SurveyStep(guid="s1", display_name=step_key, qualified_name=f"Step::{step_key}",
                              executes_at="resource-explorer", re_analysis_step=step_key)],
        )
        reader = MagicMock()
        reader.fetch.return_value = definition
        reader.find_candidate_process_guids.return_value = [
            {"guid": "proc-fs", "qualified_name": "GovActionProcess::FsChain", "display_name": "FS chain"}]
        with patch(self.PRESSES[step_key], side_effect=press_side_effect, return_value={"ok": True}) as press:
            result = SurveyDefinitionExecutor(registry, reader=reader).run(
                entity_type="filesystem", slug="fs", publish=PUBLISH_NOT_CHOSEN, engine_override="resource-explorer")
        return result, press

    def test_each_chained_step_runs_and_calls_its_press(self):
        for key in self.PRESSES:
            result, press = self._run(key)
            statuses = {s["step"]: s["status"] for s in result["steps"]}
            assert statuses[f"Step::{key}"] == "ok", (key, result["steps"], result["errors"])
            press.assert_called_once()

    def test_a_press_refusal_is_the_steps_error_not_an_unknown_step_or_an_empty_success(self):
        from resource_explorer.surveyors.filesystem.file_profiles import NothingToProfile

        result, _ = self._run("file_profiles", press_side_effect=NothingToProfile("no walk of this file system yet"))
        statuses = {s["step"]: s["status"] for s in result["steps"]}
        assert statuses["Step::file_profiles"] == "error"
        assert any("no walk of this file system yet" in e for e in result["errors"])
