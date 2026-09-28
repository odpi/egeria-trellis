""""By analysis" / scouting-questions were repo-only (2026-09-22).

Two more real, separate backend gaps found continuing the "DB and FS now in
scope for /next" audit (docs/Backlog.md):

* `GET /{slug}/survey-results` ("By analysis" in /next) read
  `REPO_ANALYSIS_RESULTS_MAP` directly, with no database/filesystem
  equivalent -- fixed by `workflows.analysis.build_survey_results` and new
  `DATABASE_ANALYSIS_RESULTS_MAP` / `FILESYSTEM_ANALYSIS_RESULTS_MAP`.
* `GET /{slug}/scouting-questions`'s `has_data` scoring
  (`workflows.scouting.question_has_data`) read the same repo-only map --
  fixed by parametrizing it over `entity_type`, and the route itself
  (repo-only lookup) by `workflows.scouting.build_question_checklist`.

This file exercises the new results readers directly (no live DB
connection -- every one of them reads already-stored rows), then the two
generalized functions built on top of them.
"""
from __future__ import annotations

import json

import pytest

from resource_explorer.registry import DatabaseEntity, FileSystemEntity, ProjectStatus


@pytest.fixture
def slug(request):
    import re as _re
    return "dbq_" + _re.sub(r"[^a-z0-9]+", "_", request.node.name.lower())[:44]


@pytest.fixture
def db_reg(pg_registry, slug):
    pg_registry.register_database(DatabaseEntity(
        slug=slug, display_name=slug, db_type="postgresql",
        host="localhost", port=5432, database_name=slug,
        status=ProjectStatus.ACTIVE,
    ))
    return pg_registry


@pytest.fixture
def fs_reg(pg_registry, slug):
    pg_registry.register_filesystem(FileSystemEntity(
        slug=slug, display_name=slug, local_mount_point=f"/data/{slug}",
        status=ProjectStatus.ACTIVE,
    ))
    return pg_registry


class TestDatabaseResultsMapCoverage:
    """The map's own documented contract: 14 real readers, 3 known-absent
    (data_class_match/reference_data_match/nested_column_profile -- no local
    store exists for them yet, see the map's docstring), egeria_db_survey
    excluded (a trigger, not a reader, same as repo's own Egeria-triggered
    analyses)."""

    def test_covers_the_fourteen_documented_analyses(self):
        # Now fifteen: credential_capability (design REPLY-DATABASE-
        # CREDENTIAL-CAPABILITY-VISIBILITY.md §3/§4, replying to
        # ASK-...-#251) has its own results reader from the start, unlike
        # the three genuinely-still-unbacked ids in expected_absent below.
        #
        # Now eighteen (2026-09-24): `subject_signals`, `coverage_signals` and
        # `preliminary_fit` — multi-resource-questions-design.md §16.3's
        # Scouting and Discovery rows, all three backed by the same zero-fetch
        # `db_derived` step and so all three with a reader from the start.
        #
        # `DATABASE_ANALYSIS_RE_STEP_MAP` (renamed from `DATABASE_ANALYSIS_
        # STEP_MAP` — slice 17, docs/design-notes/SLICE-17-RUNNABILITY-FROM-
        # CATALOG-IMPLEMENTED.md) maps an analysis id to a `re_analysis_step`
        # key, `db_derived` for all eleven zero-fetch ids including these
        # three — it is now DERIVED from `db_derived.DB_DERIVED_ANALYSES`
        # rather than hand-listed a second time, which is exactly the fix:
        # before it, this map had all eight of the OLDER db_derived ids but
        # not these three, and every Run button for them reported "no mapped
        # survey step(s)" although a real results reader (this test) always
        # existed for them. The set difference asserted below is unaffected
        # by the fix either way, since all eleven db_derived ids are present
        # in BOTH maps now.
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_RE_STEP_MAP, DATABASE_ANALYSIS_RESULTS_MAP,
        )

        expected_absent = {
            "data_class_match", "reference_data_match", "nested_column_profile",
            "egeria_db_survey",
        }
        assert set(DATABASE_ANALYSIS_RE_STEP_MAP) - set(DATABASE_ANALYSIS_RESULTS_MAP) == expected_absent
        assert len(DATABASE_ANALYSIS_RESULTS_MAP) == 18

    def test_every_entry_is_a_reader_pair(self):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_RESULTS_MAP,
        )
        for analysis_id, (results_reader, trend_reader) in DATABASE_ANALYSIS_RESULTS_MAP.items():
            assert callable(results_reader), analysis_id
            assert trend_reader is None, analysis_id  # none built yet -- honest, not guessed


class TestDatabaseResultsReadersAreHonestAboutAbsence:
    """Never-surveyed database: every reader returns an empty dict, not a
    fabricated zero-valued shape -- the same "absence renders as absence"
    contract every other results_reader in this codebase holds to."""

    def test_schema_inventory_empty_when_never_surveyed(self, db_reg, slug):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_RESULTS_MAP,
        )
        reader, _ = DATABASE_ANALYSIS_RESULTS_MAP["schema_inventory"]
        assert reader(db_reg, slug) == {}

    def test_row_count_snapshot_empty_when_never_surveyed(self, db_reg, slug):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_RESULTS_MAP,
        )
        reader, _ = DATABASE_ANALYSIS_RESULTS_MAP["row_count_snapshot"]
        assert reader(db_reg, slug) == {}

    def test_privilege_audit_empty_with_no_survey_row(self, db_reg, slug):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_RESULTS_MAP,
        )
        reader, _ = DATABASE_ANALYSIS_RESULTS_MAP["privilege_audit"]
        assert reader(db_reg, slug) == {}

    def test_db_derived_backed_readers_never_raise_on_empty_inputs(self, db_reg, slug):
        """The eight db_derived-backed analyses recompute live over whatever
        is stored -- with nothing stored, this must degrade gracefully
        (an empty/absence-shaped dict), never raise and break the whole
        dashboard the way a bare KeyError would."""
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_RESULTS_MAP,
        )
        for analysis_id in (
            "db_classification", "db_relationship_graph", "grain_determination",
            "db_fingerprint", "schema_conventions", "db_change_rates",
            "schema_diff", "grant_change",
        ):
            reader, _ = DATABASE_ANALYSIS_RESULTS_MAP[analysis_id]
            result = reader(db_reg, slug)
            assert isinstance(result, dict), analysis_id


class TestDatabaseResultsReadersReadStoredRows:
    """Once rows exist, the thin-wrapper readers surface them -- proving
    these are real reads, not always-empty stubs."""

    def test_schema_inventory_reads_stored_tables_and_columns(self, db_reg, slug):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_RESULTS_MAP,
        )
        ts = "2026-09-22T10:00:00"
        db_reg.write_detail_rows("database_tables", slug, ts, rows=[
            {"schema_name": "public", "table_name": "orders", "table_type": "table",
             "row_count": 42},
        ])
        db_reg.write_detail_rows("database_columns", slug, ts, rows=[
            {"schema_name": "public", "table_name": "orders", "column_name": "id",
             "ordinal_position": 1, "data_type": "integer"},
        ])
        reader, _ = DATABASE_ANALYSIS_RESULTS_MAP["schema_inventory"]
        result = reader(db_reg, slug)
        assert result["table_count"] == 1
        assert result["column_count"] == 1
        assert result["tables"][0]["table_name"] == "orders"
        assert result["tables"][0]["row_count"] == 42

    def test_row_count_snapshot_reads_stored_row_counts(self, db_reg, slug):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_RESULTS_MAP,
        )
        ts = "2026-09-22T10:00:00"
        db_reg.write_detail_rows("database_tables", slug, ts, rows=[
            {"schema_name": "public", "table_name": "orders", "row_count": 42},
            {"schema_name": "public", "table_name": "empty_stub", "row_count": None},
        ])
        reader, _ = DATABASE_ANALYSIS_RESULTS_MAP["row_count_snapshot"]
        result = reader(db_reg, slug)
        assert result["table_count"] == 2
        assert result["measured_count"] == 1  # the NULL row_count is not "measured as 0"

    def test_operations_sections_read_the_latest_survey_blob(self, db_reg, slug):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_RESULTS_MAP,
        )
        db_reg.record_database_survey(
            slug=slug, schema_count=1, table_count=1, column_count=1,
            survey_data={"operations": {
                "privilege_audit": {"roles": [{"rolname": "app"}], "table_grants": []},
                "activity_signals": {"table_activity": []},
            }},
        )
        priv_reader, _ = DATABASE_ANALYSIS_RESULTS_MAP["privilege_audit"]
        assert priv_reader(db_reg, slug)["roles"] == [{"rolname": "app"}]

        activity_reader, _ = DATABASE_ANALYSIS_RESULTS_MAP["db_activity_signals"]
        assert activity_reader(db_reg, slug) == {"table_activity": []}

        # A section this survey never populated (resilience wasn't in the
        # blob at all) is absence, not a KeyError.
        resilience_reader, _ = DATABASE_ANALYSIS_RESULTS_MAP["db_resilience"]
        assert resilience_reader(db_reg, slug) == {}


class TestFilesystemResultsMap:
    def test_covers_its_one_analysis(self):
        from resource_explorer.surveyors.filesystem.survey_definition_adapter import (
            FILESYSTEM_ANALYSIS_RESULTS_MAP,
        )
        assert set(FILESYSTEM_ANALYSIS_RESULTS_MAP) == {"filesystem_inventory"}

    def test_empty_when_never_surveyed(self, fs_reg, slug):
        from resource_explorer.surveyors.filesystem.survey_definition_adapter import (
            FILESYSTEM_ANALYSIS_RESULTS_MAP,
        )
        reader, _ = FILESYSTEM_ANALYSIS_RESULTS_MAP["filesystem_inventory"]
        assert reader(fs_reg, slug) == {}

    def test_reads_stored_entries(self, fs_reg, slug):
        from resource_explorer.surveyors.filesystem.survey_definition_adapter import (
            FILESYSTEM_ANALYSIS_RESULTS_MAP,
        )
        ts = "2026-09-22T10:00:00"
        fs_reg.write_detail_rows("filesystem_entries", slug, ts, rows=[
            {"entry_path": "/data/a.csv", "entry_type": "file", "size_bytes": 100,
             "is_hidden": False},
            {"entry_path": "/data/sub", "entry_type": "directory"},
        ])
        reader, _ = FILESYSTEM_ANALYSIS_RESULTS_MAP["filesystem_inventory"]
        result = reader(fs_reg, slug)
        assert result["entry_count"] == 2
        assert result["file_count"] == 1
        assert result["directory_count"] == 1
        assert result["total_size_bytes"] == 100


class TestBuildSurveyResultsSynthesizesPerAnalysisDashboards:
    """database/filesystem have no curated SURVEY_RESULT_DASHBOARDS groupings
    -- build_survey_results synthesizes one dashboard per analysis_id with a
    results reader instead, literally "by analysis"."""

    def test_database_with_no_data_returns_no_dashboards_by_default(self, db_reg, slug):
        from resource_explorer.workflows.analysis import build_survey_results
        got = build_survey_results(db_reg, "database", slug)
        assert got["dashboards"] == []

    def test_database_include_empty_lists_every_mapped_analysis(self, db_reg, slug):
        from resource_explorer.workflows.analysis import build_survey_results
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_RESULTS_MAP,
        )
        got = build_survey_results(db_reg, "database", slug, include_empty=True)
        ids = {d["id"] for d in got["dashboards"]}
        assert ids == set(DATABASE_ANALYSIS_RESULTS_MAP)
        for d in got["dashboards"]:
            assert d["analyses"][0]["analysis_id"] == d["id"]
            assert d["has_results"] is False

    def test_database_surfaces_a_dashboard_with_real_data(self, db_reg, slug):
        from resource_explorer.workflows.analysis import build_survey_results
        ts = "2026-09-22T10:00:00"
        db_reg.write_detail_rows("database_tables", slug, ts, rows=[
            {"schema_name": "public", "table_name": "orders", "row_count": 42},
        ])
        got = build_survey_results(db_reg, "database", slug)
        ids = {d["id"] for d in got["dashboards"]}
        assert "schema_inventory" in ids
        assert "row_count_snapshot" in ids

    def test_stage_filter_scopes_to_that_analysis_catalog_intent(self, db_reg, slug):
        from resource_explorer.workflows.analysis import build_survey_results
        ts = "2026-09-22T10:00:00"
        db_reg.write_detail_rows("database_tables", slug, ts, rows=[
            {"schema_name": "public", "table_name": "orders", "row_count": 42},
        ])
        got_wrong_stage = build_survey_results(db_reg, "database", slug, stage="curate")
        assert got_wrong_stage["dashboards"] == []

    def test_filesystem_surfaces_its_one_dashboard(self, fs_reg, slug):
        from resource_explorer.workflows.analysis import build_survey_results
        ts = "2026-09-22T10:00:00"
        fs_reg.write_detail_rows("filesystem_entries", slug, ts, rows=[
            {"entry_path": "/data/a.csv", "entry_type": "file", "size_bytes": 100},
        ])
        got = build_survey_results(fs_reg, "filesystem", slug)
        assert [d["id"] for d in got["dashboards"]] == ["filesystem_inventory"]


class TestQuestionHasDataIsGeneralized:
    """workflows.scouting.question_has_data now takes entity_type and
    dispatches to the matching *_ANALYSIS_RESULTS_MAP -- the has_data
    scoring behind /next's Questions checklist."""

    def test_database_true_once_something_is_measured(self, db_reg, slug):
        from resource_explorer.workflows.scouting import question_has_data
        ts = "2026-09-22T10:00:00"
        db_reg.write_detail_rows("database_tables", slug, ts, rows=[
            {"schema_name": "public", "table_name": "orders", "row_count": 42},
        ])
        assert question_has_data(db_reg, slug, ["schema_inventory"], entity_type="database") is True

    def test_database_false_when_nothing_measured(self, db_reg, slug):
        from resource_explorer.workflows.scouting import question_has_data
        assert question_has_data(db_reg, slug, ["schema_inventory"], entity_type="database") is False

    def test_database_none_when_no_analysis_ids(self, db_reg, slug):
        from resource_explorer.workflows.scouting import question_has_data
        assert question_has_data(db_reg, slug, [], entity_type="database") is None

    def test_database_false_not_raise_for_the_known_gap_analyses(self, db_reg, slug):
        """data_class_match/reference_data_match/nested_column_profile have
        no entry in DATABASE_ANALYSIS_RESULTS_MAP yet (see that map's own
        docstring) -- has_data must degrade to False, not KeyError."""
        from resource_explorer.workflows.scouting import question_has_data
        assert question_has_data(
            db_reg, slug, ["data_class_match"], entity_type="database",
        ) is False

    def test_repository_health_special_case_stays_repo_only(self, db_reg, slug):
        """The repository_health carve-out (checked via project_stats, not a
        results_reader) must not fire for a database slug that happens to
        share the id string with a repo analysis -- it isn't one."""
        from resource_explorer.workflows.scouting import question_has_data
        # No "repository_health" entry exists in DATABASE_ANALYSIS_RESULTS_MAP
        # and the special case is gated on entity_type == "repo", so this
        # must fall through to False, not raise or short-circuit True.
        assert question_has_data(
            db_reg, slug, ["repository_health"], entity_type="database",
        ) is False

    def test_repo_behavior_is_unchanged(self, pg_registry, slug):
        from resource_explorer.registry import Project
        from resource_explorer.workflows.scouting import question_has_data
        pg_registry.add(Project(slug=slug, display_name=slug,
                                github_url=f"https://github.com/x/{slug}"))
        # repository_health special case still applies with entity_type
        # defaulting to "repo".
        assert question_has_data(pg_registry, slug, ["repository_health"]) is False


class TestBuildQuestionChecklistIsGeneralized:
    def test_database_checklist_has_scored_has_data_for_analysis_kind_entries(self, db_reg, slug):
        from resource_explorer.workflows.scouting import build_question_checklist
        got = build_question_checklist(db_reg, "database", slug, phase="scouting")
        assert got["phase"] == "scouting"
        assert isinstance(got["questions"], list)
        # Every analysis/partial/mixed-kind entry got a real True/False, not
        # left at the parametrized default -- direct/registry/human/chart/gap
        # kinds legitimately stay None (nothing to check).
        for q in got["questions"]:
            if q["kind"] in ("analysis", "partial", "mixed"):
                assert q["has_data"] in (True, False)
            else:
                assert q["has_data"] is None

    def test_filesystem_checklist_does_not_raise(self, fs_reg, slug):
        from resource_explorer.workflows.scouting import build_question_checklist
        got = build_question_checklist(fs_reg, "filesystem", slug, phase="scouting")
        assert isinstance(got["questions"], list)

    def test_levels_are_carried_through_to_each_question(self, db_reg, slug):
        """Slice 21a point 4: `question_catalog_reader.get_questions()`
        already carries `levels` on every entry, but this function builds
        its own separate per-question dict and dropped it — found live
        gating Slice 21a, 2026-09-26: app.js's `primaryQuestionLevel(entry)`
        always saw `undefined` and silently fell back to "resource" for
        every question, including "Which schemas carry the data, and which
        are system, empty or staging?" (`levels: [container]` alone), so
        the container-level evidence table never actually reached the
        container reader through this route."""
        from resource_explorer.workflows.scouting import build_question_checklist
        got = build_question_checklist(db_reg, "database", slug, phase="scouting")
        by_question = {q["question"]: q for q in got["questions"]}
        container_only = by_question["Which schemas carry the data, and which are system, empty or staging?"]
        assert container_only["levels"] == ["container"]
        multi_level = by_question[
            "How big is this database — schemas, tables, views, columns, rows and bytes?"]
        assert "resource" in multi_level["levels"]
        # Every question has a `levels` list, never a missing/None key --
        # every entry authored before the Level column existed defaults to
        # ["resource"] (question_catalog_reader's own default).
        assert all(q["levels"] for q in got["questions"])
