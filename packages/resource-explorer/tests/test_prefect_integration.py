"""Tests for Prefect workflow orchestration, PostgreSQL migration setup, and quality scanning."""
from __future__ import annotations

import os
import pytest
from unittest.mock import MagicMock, patch
from prefect.testing.utilities import prefect_test_harness

from resource_explorer.registry import ProjectRegistry, Project, DatabaseEntity, FileSystemEntity
from resource_explorer.prefect.flows import re_survey_flow, run_soda_scan_task, run_gx_validation_task
from resource_explorer.surveyors.prefect_adapter import run_prefect_step


@pytest.fixture
def db(tmp_path):
    """Local SQLite registry fixture for testing."""
    db_path = str(tmp_path / "test_prefect.db")
    return ProjectRegistry(db_path=db_path)


pytestmark = pytest.mark.usefixtures("ephemeral_prefect")

class TestRegistryGovernanceState:
    def test_default_governance_state(self, db):
        # Register a project, database, and filesystem
        proj = Project(
            slug="test-proj",
            display_name="Test Proj",
            github_url="https://github.com/test/test-proj",
        )
        db.add(proj)

        db_entity = DatabaseEntity(
            slug="test-db",
            display_name="Test DB",
            db_type="postgresql",
            host="localhost",
            port=5432,
            database_name="test_db",
        )
        db.register_database(db_entity)

        fs_entity = FileSystemEntity(
            slug="test-fs",
            display_name="Test FS",
            local_mount_point="/tmp/test-fs",
        )
        db.register_filesystem(fs_entity)

        # Check default governance_state
        assert db.get("test-proj").governance_state == "certified"
        assert db.get_database("test-db").governance_state == "certified"
        assert db.get_filesystem("test-fs").governance_state == "certified"

    def test_update_governance_state(self, db):
        # Register a database candidate
        db_entity = DatabaseEntity(
            slug="test-candidate",
            display_name="Candidate DB",
            db_type="postgresql",
            host="localhost",
            port=5432,
            database_name="candidate_db",
            governance_state="unclassified_candidate",
        )
        db.register_database(db_entity)
        assert db.get_database("test-candidate").governance_state == "unclassified_candidate"

        # Update governance_state
        db.update_governance_state("database", "test-candidate", "certified")
        assert db.get_database("test-candidate").governance_state == "certified"


class TestPrefectOrchestration:
    def test_local_flow_execution_fallback(self):
        """Verify that when Prefect is disabled/api is down, flows run locally in-process."""
        # Mock adapter registry and get_adapter
        mock_adapter = MagicMock()
        mock_entity = MagicMock()
        mock_entity.slug = "my-entity-slug"
        mock_adapter.get_entity.return_value = mock_entity
        mock_adapter.re_analysis_steps = {
            "dummy_step": lambda entity, registry, **kwargs: {"status": "dummy_ok"}
        }

        with patch("resource_explorer.surveyors.prefect_adapter.get_config") as mock_config, \
             patch("resource_explorer.prefect.flows.get_adapter", return_value=mock_adapter), \
             patch("resource_explorer.surveyors.prefect_adapter.re_prefect_client",
                   side_effect=Exception("API Down")):

            mock_config.return_value.prefect.enabled = True
            
            result = run_prefect_step(
                entity_type="database",
                slug="my-entity-slug",
                step_name="dummy_step",
                runner_kwargs={},
            )
            assert result == {"status": "dummy_ok"}

    @pytest.mark.asyncio
    async def test_soda_scan_task_mocked(self):
        """Verify Soda Core quality check executes and reports failures/warnings correctly."""
        with patch("soda.scan.Scan") as MockScan:
            mock_scan_instance = MockScan.return_value
            mock_scan_instance.execute.return_value = 0
            mock_scan_instance.get_scan_results.return_value = {"checks": []}
            mock_scan_instance.has_check_failures.return_value = False
            mock_scan_instance.has_check_warnings.return_value = False

            result = run_soda_scan_task.fn(
                db_type="postgresql",
                host="localhost",
                port=5432,
                database_name="test_db",
                db_user="user",
                db_password="password",
                sodacl_yaml="checks for my_table:\n  - row_count > 0",
            )

            assert result["exit_code"] == 0
            assert result["has_failures"] is False
            assert result["has_warnings"] is False
            MockScan.assert_called_once()
