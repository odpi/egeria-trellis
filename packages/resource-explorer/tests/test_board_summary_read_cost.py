"""board_summary read-cost fix (2026-09-28,
BOARD-SUMMARY-READ-COST-IMPLEMENTED.md).

The problem: `build_survey_results(board_id=...)`, called once per board by
the By-analysis pane's progressive-render fetch (PR #346), recomputes its
answer from scratch on EVERY call by invoking that analysis_id's
`results_reader`/`headline_reader` — measured 10-26s per board on
adventureworks discovery boards. Seven boards fetch in parallel on every
pane load, so nothing settled within 60+s on a real gate check.

The fix: persist each board's computed dashboard dict as a `board_summary`
row at run completion (`_refresh_board_summaries_after_run`, called from
`execute_and_record_database_analysis`/`execute_and_record_analysis`), and
have `build_survey_results(..., board_id=...)` read that row instead of
recomputing, falling back to the old expensive path only when the summary
is missing or older than the analysis's most recent recorded run.

This suite is the SOURCE OF TRUTH for the fix (not wall-clock timing, which
is flaky in CI) — it asserts the expensive reader is NOT re-invoked on a
fresh-summary read, IS invoked exactly once to seed the summary, and IS
invoked again when the summary is missing/stale. Wall-clock verification
against real data (laz_local_adventureworks) is documented separately, in
the IMPLEMENTED doc, where it was possible to run.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.workflows import analysis as analysis_mod


# ── registry-level round trip ────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "test.db"))


class TestRegistryBoardSummaryRoundTrip:
    def test_missing_summary_is_none(self, db):
        assert db.get_board_summary("database", "mydb", "db_classification") is None

    def test_write_then_read_round_trips_the_dict_and_source_run_at(self, db):
        summary = {"id": "db_classification", "has_results": True, "analyses": []}
        db.write_board_summary("database", "mydb", "db_classification", summary, source_run_at="2026-09-28T10:00:00")
        got = db.get_board_summary("database", "mydb", "db_classification")
        assert got["summary"] == summary
        assert got["source_run_at"] == "2026-09-28T10:00:00"
        assert got["computed_at"]  # stamped, non-empty

    def test_write_upserts_rather_than_duplicating(self, db):
        db.write_board_summary("database", "mydb", "db_classification", {"has_results": False}, source_run_at="t1")
        db.write_board_summary("database", "mydb", "db_classification", {"has_results": True}, source_run_at="t2")
        got = db.get_board_summary("database", "mydb", "db_classification")
        assert got["summary"] == {"has_results": True}
        assert got["source_run_at"] == "t2"

    def test_summaries_for_different_boards_do_not_collide(self, db):
        db.write_board_summary("database", "mydb", "db_classification", {"id": "db_classification"})
        db.write_board_summary("database", "mydb", "db_fingerprint", {"id": "db_fingerprint"})
        assert db.get_board_summary("database", "mydb", "db_classification")["summary"]["id"] == "db_classification"
        assert db.get_board_summary("database", "mydb", "db_fingerprint")["summary"]["id"] == "db_fingerprint"


# ── build_survey_results fast path ───────────────────────────────────────

@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.register_database(DatabaseEntity(
        slug="mydb", display_name="My DB", db_type="postgresql",
        host="localhost", port=5432, database_name="mydb",
    ))
    return r


def _wire_one_analysis(monkeypatch, analysis_id="db_classification", intent="discovery", reader_result=None):
    """Point `_results_map_for`/`get_analyses` at ONE fake analysis_id, with
    a MagicMock results_reader this suite can assert on call-count for --
    the real per-analysis readers are exactly the expensive thing this fix
    exists to stop calling repeatedly, so a spy stands in for one."""
    reader_result = reader_result if reader_result is not None else {"row_count": 5}
    mock_reader = MagicMock(return_value=reader_result)
    mock_headline = MagicMock(return_value={"label": "5 rows classified."})
    results_map = {analysis_id: (mock_reader, None)}
    headline_map = {analysis_id: mock_headline}
    monkeypatch.setattr(analysis_mod, "_results_map_for", lambda entity_type: (results_map, headline_map))

    catalog_entry = {
        "id": analysis_id, "name": analysis_id.replace("_", " ").title(),
        "description": "test analysis", "intent": intent, "annotation_types": [],
    }
    monkeypatch.setattr(
        "resource_explorer.surveyors.analysis_catalog_reader.get_analyses",
        lambda entity_type, **kw: [catalog_entry],
    )
    return mock_reader, mock_headline


class TestFastPathAvoidsRecompute:
    def test_first_call_recomputes_and_persists(self, registry, monkeypatch):
        mock_reader, _ = _wire_one_analysis(monkeypatch)
        result = analysis_mod.build_survey_results(
            registry, "database", "mydb", include_empty=True, board_id="db_classification",
        )
        assert mock_reader.call_count == 1
        assert result["dashboards"][0]["id"] == "db_classification"
        assert result["dashboards"][0]["has_results"] is True
        # And it left a row behind for the next read to find.
        persisted = registry.get_board_summary("database", "mydb", "db_classification")
        assert persisted is not None
        assert persisted["summary"]["id"] == "db_classification"

    def test_second_call_reads_the_persisted_summary_not_the_reader_again(self, registry, monkeypatch):
        mock_reader, mock_headline = _wire_one_analysis(monkeypatch)
        analysis_mod.build_survey_results(
            registry, "database", "mydb", include_empty=True, board_id="db_classification",
        )
        assert mock_reader.call_count == 1

        result = analysis_mod.build_survey_results(
            registry, "database", "mydb", include_empty=True, board_id="db_classification",
        )
        # THE gate: the expensive reader must not be re-invoked on a fresh read.
        assert mock_reader.call_count == 1
        assert mock_headline.call_count == 1
        assert result["dashboards"][0]["id"] == "db_classification"
        assert result["dashboards"][0]["has_results"] is True

    def test_missing_summary_falls_back_to_recompute(self, registry, monkeypatch):
        mock_reader, _ = _wire_one_analysis(monkeypatch)
        # No write_board_summary call at all -- registry.get_board_summary is None.
        assert registry.get_board_summary("database", "mydb", "db_classification") is None
        result = analysis_mod.build_survey_results(
            registry, "database", "mydb", include_empty=True, board_id="db_classification",
        )
        assert mock_reader.call_count == 1
        assert result["dashboards"][0]["has_results"] is True

    def test_stale_summary_falls_back_to_recompute(self, registry, monkeypatch):
        mock_reader, _ = _wire_one_analysis(monkeypatch)
        # A summary exists, but a NEWER run than the one it reflects is on
        # record -- get_analysis_last_run must show something newer than
        # source_run_at for this to trigger. Stubbed directly rather than
        # routed through real activity-log attribution (DATABASE_ANALYSIS_
        # RE_STEP_MAP's actual step-key naming is a different concern this
        # test has no need to depend on).
        registry.write_board_summary(
            "database", "mydb", "db_classification", {"id": "db_classification", "has_results": True,
                                                        "stages": ["discovery"], "analyses": []},
            source_run_at="2020-01-01T00:00:00",
        )
        monkeypatch.setattr(
            ProjectRegistry, "get_analysis_last_run",
            lambda self, entity_type, slug, limit=500: {
                "db_classification": {"last_run_at": "2026-09-28T12:00:00"},
            },
        )
        result = analysis_mod.build_survey_results(
            registry, "database", "mydb", include_empty=True, board_id="db_classification",
        )
        assert mock_reader.call_count == 1  # recomputed because the persisted row was stale
        assert result["dashboards"][0]["has_results"] is True

    def test_fresh_summary_with_no_recorded_run_is_still_used(self, registry, monkeypatch):
        """A summary written with no `source_run_at` (a lazy recompute-and-
        persist that had no run timestamp handy) is treated as always fresh
        rather than always stale -- see write_board_summary's docstring."""
        mock_reader, _ = _wire_one_analysis(monkeypatch)
        registry.write_board_summary(
            "database", "mydb", "db_classification",
            {"id": "db_classification", "has_results": True, "stages": ["discovery"], "analyses": []},
        )
        result = analysis_mod.build_survey_results(
            registry, "database", "mydb", include_empty=True, board_id="db_classification",
        )
        assert mock_reader.call_count == 0
        assert result["dashboards"][0]["has_results"] is True

    def test_no_board_id_never_uses_the_fast_path(self, registry, monkeypatch):
        """The board_id-scoped short-circuit must not engage for the
        whole-stage sweep (no board_id) -- that call's contract (union of
        every matching board) is unchanged by this fix."""
        mock_reader, _ = _wire_one_analysis(monkeypatch)
        registry.write_board_summary(
            "database", "mydb", "db_classification",
            {"id": "db_classification", "has_results": True, "stages": ["discovery"], "analyses": []},
        )
        analysis_mod.build_survey_results(registry, "database", "mydb", include_empty=True)
        assert mock_reader.call_count == 1


class TestStageFilterRunsBeforeExpensiveRead:
    """`_read_analyses` calls the expensive results_reader/headline_reader.
    For the database/filesystem synthesized-dashboard loop, the stage check
    must reject a non-matching analysis_id BEFORE that call, not after."""

    def test_reader_not_called_when_stage_does_not_match(self, registry, monkeypatch):
        mock_reader, mock_headline = _wire_one_analysis(monkeypatch, intent="discovery")
        result = analysis_mod.build_survey_results(
            registry, "database", "mydb", stage="assessment", include_empty=True,
        )
        assert result["dashboards"] == []
        mock_reader.assert_not_called()
        mock_headline.assert_not_called()

    def test_reader_is_called_when_stage_matches(self, registry, monkeypatch):
        mock_reader, _ = _wire_one_analysis(monkeypatch, intent="discovery")
        result = analysis_mod.build_survey_results(
            registry, "database", "mydb", stage="discovery", include_empty=True,
        )
        assert len(result["dashboards"]) == 1
        mock_reader.assert_called_once()


# ── the writer half: run completion persists a summary ──────────────────

class TestWriterPersistsAtRunCompletion:
    def test_execute_and_record_database_analysis_persists_a_summary(self, registry, monkeypatch):
        mock_reader, _ = _wire_one_analysis(monkeypatch, intent="discovery")
        from resource_explorer.workflows.analysis import DatabaseAnalysisRunResult

        monkeypatch.setattr(
            analysis_mod, "run_database_analysis",
            lambda slug, analysis_id, registry=None: DatabaseAnalysisRunResult(
                status="ok", summary="1 annotation(s).", annotations=[],
            ),
        )
        assert registry.get_board_summary("database", "mydb", "db_classification") is None
        analysis_mod.execute_and_record_database_analysis(
            "mydb", "db_classification", "activity-1", registry=registry,
        )
        persisted = registry.get_board_summary("database", "mydb", "db_classification")
        assert persisted is not None
        assert persisted["summary"]["id"] == "db_classification"
        # And the reader really was invoked (once, to build the summary) --
        # this is the "compute once, at run completion" half of the design.
        assert mock_reader.call_count == 1

    def test_a_failed_run_does_not_persist_a_summary(self, registry, monkeypatch):
        _wire_one_analysis(monkeypatch, intent="discovery")
        from resource_explorer.workflows.analysis import DatabaseAnalysisRunResult

        monkeypatch.setattr(
            analysis_mod, "run_database_analysis",
            lambda slug, analysis_id, registry=None: DatabaseAnalysisRunResult(
                status="error", error="boom",
            ),
        )
        analysis_mod.execute_and_record_database_analysis(
            "mydb", "db_classification", "activity-1", registry=registry,
        )
        assert registry.get_board_summary("database", "mydb", "db_classification") is None

    def test_refresh_helper_never_raises_on_a_broken_dashboard_lookup(self, registry, monkeypatch):
        """A summary refresh must never turn a successful run's activity-log
        write into a crash -- see _refresh_board_summaries_after_run's own
        docstring."""
        def _boom(*a, **kw):
            raise RuntimeError("simulated failure")
        monkeypatch.setattr(analysis_mod, "build_survey_results", _boom)
        analysis_mod._refresh_board_summaries_after_run(registry, "database", "mydb", "db_classification")
        # No exception propagated -- that's the whole assertion.
