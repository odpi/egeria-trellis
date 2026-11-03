"""`resource-explorer backfill-board-summaries` — the CLI entry point that
pre-populates `board_summary` rows for a resource (or every resource) before
any real user pays the first-visit recompute cost live
(BOARD-SUMMARY-READ-COST-IMPLEMENTED.md's "backfill CLI"
section).

This suite mirrors `test_board_summary_read_cost.py`'s `_wire_one_analysis`
pattern — a MagicMock results_reader stands in for the real, expensive
per-analysis reader so tests assert call-count/behavior rather than
wall-clock timing. What this suite adds is the CLI-specific contract: a
resource with no summaries gets backfilled, a resource with fresh summaries
is skipped, a per-board failure doesn't abort the whole run, and `--all`
iterates every registered database and filesystem.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from resource_explorer.cli.main import app
from resource_explorer.registry import DatabaseEntity, FileSystemEntity, ProjectRegistry
from resource_explorer.workflows import analysis as analysis_mod


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def registry(tmp_path, monkeypatch):
    r = ProjectRegistry(db_path=str(tmp_path / "cli-backfill.db"))
    r.register_database(DatabaseEntity(
        slug="mydb", display_name="My DB", db_type="postgresql",
        host="localhost", port=5432, database_name="mydb",
    ))
    # The command imports `ProjectRegistry` from `resource_explorer.registry`
    # inline, at call time -- patch the class there, not on the cli module
    # (which never binds the name at import time).
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry", lambda *a, **kw: r)
    return r


def _wire_boards(monkeypatch, entity_type="database", board_ids=("db_classification", "db_fingerprint")):
    """Two fake analysis_ids/boards (database/filesystem are 1:1), each with
    its own MagicMock results_reader so a test can assert on which boards
    were actually recomputed."""
    readers = {}
    results_map = {}
    headline_map = {}
    catalog_entries = []
    for board_id in board_ids:
        mock_reader = MagicMock(return_value={"row_count": 5})
        readers[board_id] = mock_reader
        results_map[board_id] = (mock_reader, None)
        headline_map[board_id] = MagicMock(return_value={"label": "5 rows."})
        catalog_entries.append({
            "id": board_id, "name": board_id.replace("_", " ").title(),
            "description": "test analysis", "intent": "discovery", "annotation_types": [],
        })

    monkeypatch.setattr(analysis_mod, "_results_map_for", lambda et: (results_map, headline_map))
    monkeypatch.setattr(
        "resource_explorer.surveyors.analysis_catalog_reader.get_analyses",
        lambda et, **kw: catalog_entries,
    )
    return readers


class TestBackfillsAResourceWithNoSummaries:
    def test_backfills_every_board_and_reports_progress(self, runner, registry, monkeypatch):
        readers = _wire_boards(monkeypatch)
        result = runner.invoke(app, ["backfill-board-summaries", "mydb"])
        assert result.exit_code == 0, result.output
        assert readers["db_classification"].call_count == 1
        assert readers["db_fingerprint"].call_count == 1
        assert "db_classification" in result.output
        assert "db_fingerprint" in result.output
        assert "backfilled" in result.output.lower()
        # Each board really did leave a fast-read row behind.
        assert registry.get_board_summary("database", "mydb", "db_classification") is not None
        assert registry.get_board_summary("database", "mydb", "db_fingerprint") is not None

    def test_unknown_slug_exits_nonzero(self, runner, registry, monkeypatch):
        _wire_boards(monkeypatch)
        result = runner.invoke(app, ["backfill-board-summaries", "no-such-resource"])
        assert result.exit_code == 1
        assert "not a registered database or filesystem" in result.output


class TestSkipsAResourceWithFreshSummaries:
    def test_a_fresh_board_is_skipped_not_recomputed(self, runner, registry, monkeypatch):
        readers = _wire_boards(monkeypatch)
        registry.write_board_summary(
            "database", "mydb", "db_classification",
            {"id": "db_classification", "has_results": True, "stages": ["discovery"], "analyses": []},
        )
        result = runner.invoke(app, ["backfill-board-summaries", "mydb"])
        assert result.exit_code == 0, result.output
        assert readers["db_classification"].call_count == 0  # already fresh -- never touched
        assert readers["db_fingerprint"].call_count == 1  # still needed backfilling
        assert "already fresh" in result.output


class TestContinuesPastAPerBoardFailure:
    def test_one_failing_board_does_not_abort_the_rest(self, runner, registry, monkeypatch):
        """`build_survey_results` itself is fail-soft per-analysis (a reader
        that raises degrades to a None result, per `_read_analyses`'s own
        docstring) -- so the failure this test forces is a level up: the
        board-level call raising outright (e.g. a catalog/registry lookup
        blowing up for one board_id specifically). The backfill command must
        still move on to the next board rather than aborting the whole run."""
        _wire_boards(monkeypatch)
        real_build_survey_results = analysis_mod.build_survey_results

        def _flaky(reg, entity_type, slug, stage="", include_empty=False, board_id=""):
            if board_id == "db_classification":
                raise RuntimeError("boom")
            return real_build_survey_results(
                reg, entity_type, slug, stage=stage, include_empty=include_empty, board_id=board_id,
            )

        monkeypatch.setattr(analysis_mod, "build_survey_results", _flaky)

        result = runner.invoke(app, ["backfill-board-summaries", "mydb"])
        # The run reports failure overall (a real error occurred)...
        assert result.exit_code == 1
        assert "boom" in result.output
        # ...but the OTHER board still got backfilled rather than the whole
        # run aborting on the first failure.
        assert registry.get_board_summary("database", "mydb", "db_fingerprint") is not None
        # The failing board never left a (wrong) row behind.
        assert registry.get_board_summary("database", "mydb", "db_classification") is None


class TestAllIteratesEveryRegistryResource:
    def test_all_covers_databases_and_filesystems(self, runner, registry, monkeypatch):
        registry.register_filesystem(FileSystemEntity(
            slug="myfs", display_name="My FS", local_mount_point="/tmp/myfs",
        ))
        readers = _wire_boards(monkeypatch)
        result = runner.invoke(app, ["backfill-board-summaries", "--all"])
        assert result.exit_code == 0, result.output
        assert "mydb" in result.output
        assert "myfs" in result.output
        # Each board's reader is shared across the fake analysis catalog for
        # both entity_types in this test double, so call_count reflects both
        # resources' boards having been visited.
        assert readers["db_classification"].call_count == 2
        assert readers["db_fingerprint"].call_count == 2
        assert registry.get_board_summary("database", "mydb", "db_classification") is not None
        assert registry.get_board_summary("filesystem", "myfs", "db_classification") is not None

    def test_all_with_no_registered_resources_says_so(self, runner, monkeypatch, tmp_path):
        empty = ProjectRegistry(db_path=str(tmp_path / "empty.db"))
        monkeypatch.setattr("resource_explorer.registry.ProjectRegistry", lambda *a, **kw: empty)
        result = runner.invoke(app, ["backfill-board-summaries", "--all"])
        assert result.exit_code == 0, result.output
        assert "No database or filesystem resources registered" in result.output


class TestArgumentValidation:
    def test_neither_slug_nor_all_exits_nonzero(self, runner, registry):
        result = runner.invoke(app, ["backfill-board-summaries"])
        assert result.exit_code == 1
        assert "Pass a resource slug" in result.output

    def test_both_slug_and_all_exits_nonzero(self, runner, registry):
        result = runner.invoke(app, ["backfill-board-summaries", "mydb", "--all"])
        assert result.exit_code == 1
        assert "not both" in result.output
