"""Database/filesystem Analyses cards never showed a last-run/published badge.

Live-reproduced (classic UI, `coco_pharma @ local-docker`): a database survey
completed (`POST /api/databases/{slug}/survey` -> 200 OK, confirmed via the
network tab) and every per-analysis card still showed no run/result indicator
at all. Root cause: `_loadAnalysisCatalogPanel()` in index.html only ever
fetched `/api/projects/{slug}/analyses/last-activity` for
`resourceType === 'repo'` -- `lastActivity` was hard-coded to `{}` for
database and filesystem, no matter what had run.

This file exercises the fix at the two layers that made the repo card work:

* `ProjectRegistry.get_analysis_last_run()`, generalized to attribute
  database/filesystem survey steps via `DATABASE_ANALYSIS_RE_STEP_MAP` /
  `FILESYSTEM_ANALYSIS_STEP_MAP` (a step_key -> [analysis_id, ...] fan-out,
  unlike repo's step_key -> analysis_id partition -- one coarse database step
  like "db_derived" is the real source of six separate analysis_catalog
  entries).
* `workflows.analysis.build_analysis_last_activity()`, the (now shared)
  function behind `GET /api/{projects,databases,filesystems}/{slug}/
  analyses/last-activity`.
"""
from __future__ import annotations

import json

import pytest

from resource_explorer.activity_logger import log_survey
from resource_explorer.registry import Project


@pytest.fixture
def slug(request):
    import re as _re
    return "dbfs_" + _re.sub(r"[^a-z0-9]+", "_", request.node.name.lower())[:44]


@pytest.fixture
def reg(pg_registry, slug):
    # get_analysis_last_run / build_analysis_last_activity read activity_log
    # and the analysis catalog directly -- no DatabaseEntity/FileSystemEntity
    # row is needed for these tests; a `Project` row is added only so the
    # slug exists for anything that happens to look it up.
    pg_registry.add(Project(slug=slug, display_name=slug,
                            github_url=f"https://github.com/x/{slug}"))
    return pg_registry


def _log_db_survey(reg, slug, ts, steps, process="PostgresFullSurvey"):
    """One entity_type='database' operation='survey' row, real-shaped: each
    step's qualifiedName suffix is the CamelCase authoring convention
    survey-definitions.md shows, deliberately NOT identical to the
    re_analysis_step key -- so these rows only attribute correctly because
    `re_analysis_step` is recorded directly now (survey_definition_executor's
    `_step_key(step)`), not by parsing the qualifiedName suffix."""
    log_survey(reg, "database", slug, slug, "", "discovery", "ok", "surveyed", json.dumps({
        "source": "survey-definition", "entity_type": "database", "slug": slug,
        "surveyed_at": ts,
        "steps": [{"step": f"GovActionProcessStep::{process}::{qn}",
                   "re_analysis_step": key, "status": st}
                  for qn, key, st in steps],
    }))


class TestDatabaseStepMapFansOutCorrectly:
    """DATABASE_ANALYSIS_RE_STEP_MAP: one coarse step legitimately credits
    several analysis_catalog entries at once -- not a partition like repo's."""

    def test_db_derived_credits_all_analyses(self):
        """Was "all six" until Phase 1 slice 14's follow-up (2026-09-22) added
        schema_diff and grant_change to db_derived, then "all eight" until
        slice 17 (docs/design-notes/SLICE-17-RUNNABILITY-FROM-CATALOG-
        IMPLEMENTED.md) fixed this map being derived from
        `db_derived.DB_DERIVED_ANALYSES` instead of hand-listed a second
        time — `subject_signals`/`coverage_signals`/`preliminary_fit`
        (design §16.3's Scouting/Discovery rows, added to
        DB_DERIVED_ANALYSES 2026-09-24) had never been added here, which is
        exactly why their Run buttons reported "no mapped survey step(s))"
        live against `coco_pharma` (REVIEW-SURVEY-PANE-285.md §5(a)). Now
        that this map is computed from DB_DERIVED_ANALYSES directly, this
        assertion is really pinning that the two constants agree, not an
        independent list."""
        from resource_explorer.surveyors.database.db_derived import DB_DERIVED_ANALYSES
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_RE_STEP_MAP)

        owners_of_db_derived = [
            analysis_id for analysis_id, keys in DATABASE_ANALYSIS_RE_STEP_MAP.items()
            if "db_derived" in keys
        ]
        assert set(owners_of_db_derived) == set(DB_DERIVED_ANALYSES)
        assert set(owners_of_db_derived) == {
            "db_classification", "db_relationship_graph", "grain_determination",
            "db_fingerprint", "schema_conventions", "db_change_rates",
            "schema_diff", "grant_change",
            "subject_signals", "coverage_signals", "preliminary_fit",
        }

    def test_sql_analysis_has_no_analysis_catalog_entry(self):
        """A known, documented gap -- not silently mapped to something wrong."""
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_RE_STEP_MAP)

        assert "sql_analysis" not in DATABASE_ANALYSIS_RE_STEP_MAP.values()
        for keys in DATABASE_ANALYSIS_RE_STEP_MAP.values():
            assert "sql_analysis" not in keys


class TestDatabaseNeverRun:
    def test_no_surveys_at_all_is_never_run(self, reg, slug):
        assert reg.get_analysis_last_run("database", slug) == {}

    def test_the_endpoint_reports_never_run_for_every_card(self, reg, slug):
        from resource_explorer.workflows.analysis import build_analysis_last_activity

        got = build_analysis_last_activity(reg, "database", slug)
        assert got["schema_conventions"]["last_run_basis"] == "never_run"
        assert got["nested_column_profile"]["last_run_basis"] == "never_run"
        assert got["data_class_match"]["last_run_basis"] == "never_run"


class TestDatabaseRunIsAttributed:
    def test_a_db_derived_run_credits_its_six_analyses(self, reg, slug):
        _log_db_survey(reg, slug, "2026-09-20T10:00:00", [
            ("SchemaAndStats", "db_derived", "ok"),
        ])
        got = reg.get_analysis_last_run("database", slug)
        for analysis_id in ("db_classification", "db_relationship_graph",
                            "grain_determination", "db_fingerprint",
                            "schema_conventions", "db_change_rates"):
            assert got[analysis_id]["last_run_at"] == "2026-09-20T10:00:00"
            assert got[analysis_id]["last_run_status"] == "ok"
            assert got[analysis_id]["last_run_via"] == "survey"
            # Each of these analyses owns exactly one step key today, so a
            # run of that key is never partial.
            assert got[analysis_id]["last_run_partial"] is False

    def test_nested_column_profile_is_attributed_independently(self, reg, slug):
        _log_db_survey(reg, slug, "2026-09-20T11:00:00", [
            ("NestedColumns", "postgres_nested_columns", "ok"),
        ])
        got = reg.get_analysis_last_run("database", slug)
        assert got["nested_column_profile"]["last_run_at"] == "2026-09-20T11:00:00"
        assert "db_classification" not in got

    def test_an_error_status_survives_attribution(self, reg, slug):
        _log_db_survey(reg, slug, "2026-09-20T12:00:00", [
            ("ColumnProfile", "postgres_column_profile", "error"),
        ])
        got = reg.get_analysis_last_run("database", slug)
        assert got["data_class_match"]["last_run_status"] == "error"
        assert got["reference_data_match"]["last_run_status"] == "error"

    def test_the_endpoint_now_shows_measured_after_a_real_run(self, reg, slug):
        """The exact live symptom: a survey ran, and a per-analysis card must
        stop saying 'Never run'."""
        from resource_explorer.workflows.analysis import build_analysis_last_activity

        _log_db_survey(reg, slug, "2026-09-20T13:00:00", [
            ("SchemaAndStats", "postgres_schema_and_stats", "ok"),
        ])
        got = build_analysis_last_activity(reg, "database", slug)
        assert got["schema_inventory"]["last_run_basis"] == "measured"
        assert got["schema_inventory"]["last_run_at"] == "2026-09-20T13:00:00"
        assert got["row_count_snapshot"]["last_run_basis"] == "measured"


class TestDatabaseUnattributedSurveyIsNotEstablishedNotNeverRun:
    def test_a_survey_with_no_step_detail_is_not_established(self, reg, slug):
        _log_db_survey(reg, slug, "2026-09-20T09:00:00", [])
        got = reg.get_analysis_last_run("database", slug)
        assert got["__unattributed_surveys__"]["count"] == 1

    def test_qualifiedname_fallback_still_works_for_a_pre_field_row(self, reg, slug):
        """A historical row from before `re_analysis_step` was recorded
        directly must still attribute via the qualifiedName-suffix parse
        repo always used -- this is the fallback path, not the primary one."""
        log_survey(reg, "database", slug, slug, "", "discovery", "ok", "surveyed", json.dumps({
            "source": "survey-definition", "entity_type": "database", "slug": slug,
            "surveyed_at": "2026-09-19T08:00:00",
            "steps": [{"step": "GovActionProcessStep::PostgresFullSurvey::postgres_nested_columns",
                       "status": "ok"}],
        }))
        got = reg.get_analysis_last_run("database", slug)
        assert got["nested_column_profile"]["last_run_at"] == "2026-09-19T08:00:00"


class TestFilesystemAttribution:
    def _log_fs_survey(self, reg, slug, ts, status="ok"):
        log_survey(reg, "filesystem", slug, slug, "", "discovery", status, "surveyed", json.dumps({
            "source": "survey-definition", "entity_type": "filesystem", "slug": slug,
            "surveyed_at": ts,
            "steps": [{"step": "GovActionProcessStep::FsSurvey::Inventory",
                       "re_analysis_step": "filesystem_inventory", "status": status}],
        }))

    def test_never_run(self, reg, slug):
        assert reg.get_analysis_last_run("filesystem", slug) == {}

    def test_a_run_is_attributed(self, reg, slug):
        self._log_fs_survey(reg, slug, "2026-09-20T14:00:00")
        got = reg.get_analysis_last_run("filesystem", slug)
        assert got["filesystem_inventory"]["last_run_at"] == "2026-09-20T14:00:00"
        assert got["filesystem_inventory"]["last_run_partial"] is False

    def test_the_endpoint_reflects_it(self, reg, slug):
        from resource_explorer.workflows.analysis import build_analysis_last_activity

        self._log_fs_survey(reg, slug, "2026-09-20T15:00:00")
        got = build_analysis_last_activity(reg, "filesystem", slug)
        assert got["filesystem_inventory"]["last_run_basis"] == "measured"


class TestRepoBehaviourIsUnchanged:
    """The generalization must not change a single repo outcome -- these are
    the same shapes test_run_publish_honesty.py already pins, run again here
    against the generalized code path as a direct regression check."""

    def test_repo_step_attribution_still_singular(self, reg, slug):
        log_survey(reg, "repo", slug, slug, "", "scouting", "ok", "surveyed", json.dumps({
            "source": "survey-definition", "entity_type": "repo", "slug": slug,
            "surveyed_at": "2026-09-20T16:00:00",
            "steps": [{"step": "GovActionProcessStep::RepoCoarseProfile::repo_language",
                       "status": "ok"}],
        }))
        got = reg.get_analysis_last_run("repo", slug)
        assert got["language_file_classification"]["last_run_at"] == "2026-09-20T16:00:00"

    def test_an_unknown_entity_type_maps_to_nothing_rather_than_crashing(self, reg, slug):
        assert reg.get_analysis_last_run("not_a_real_entity_type", slug) == {}
