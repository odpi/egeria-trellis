"""The stage page's backend facts (STAGE-PAGE-ROUND.md, 2026-09-14):

* `build_measurements` — point 10, "the fact opens under the answer".
* `fetch_step_counts` — point 2, the definition rows' "N steps · M fetch".
* `build_analyses_index` — points 1-3, "AnalysesIndex"'s rows.

All three are pure functions in resource_explorer/workflows/stage_page.py,
tested here directly against a throwaway Postgres schema (pg_registry) —
no FastAPI app needed, same convention as test_depth_offer.py /
test_run_freshness.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from resource_explorer.members import _READERS
from resource_explorer.registry import DatabaseEntity, Project
from resource_explorer.workflows.stage_page import (
    build_analyses_index,
    build_measurements,
    fetch_step_counts,
    runnable_and_reason,
)


@pytest.fixture
def slug(request):
    import hashlib
    import re as _re
    # request.node.name alone collides across classes with a like-named test
    # (the shared, session-scoped pg_test_schema means every test in this
    # file's `projects` table is the same table) — nodeid disambiguates by
    # class, a short hash keeps it under Postgres' identifier length.
    h = hashlib.sha1(request.node.nodeid.encode()).hexdigest()[:8]
    base = _re.sub(r"[^a-z0-9]+", "_", request.node.name.lower())[:30]
    return f"sp_{base}_{h}"


@pytest.fixture
def reg(pg_registry, slug):
    pg_registry.add(Project(slug=slug, display_name=slug,
                            github_url=f"https://github.com/x/{slug}"))
    return pg_registry


def _log_run(reg, slug, analysis_id, *, minutes_ago=0, status="ok"):
    """One activity_log analysis_run row, back-dated — same helper as
    tests/test_run_freshness.py's and tests/test_depth_offer.py's."""
    from resource_explorer.activity_logger import log_analysis_run
    entry_id = log_analysis_run(reg, "repo", slug, slug, status,
                                f"ran {analysis_id}", analysis_id, published=None)
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    with reg._conn() as c:
        c.execute("UPDATE activity_log SET ts = %s WHERE id = %s", (ts, entry_id))
    return entry_id


def _write_metric_row(reg, slug, kind, metrics, *, surveyed_at=None):
    """registry.upsert_metric requires every value to be a float — the
    schema's `metric_value REAL NOT NULL` means a genuinely null column
    cannot be written this way. Null-column handling is exercised in
    `test_null_column_renders_null_and_zero_stays_zero` via a monkeypatched
    `query_metrics` instead (build_measurements never coerces whatever that
    call returns)."""
    surveyed_at = surveyed_at or datetime.now(timezone.utc).isoformat()
    reg.upsert_metric(slug, kind, metrics, surveyed_at=surveyed_at)


class TestBuildMeasurements:
    def test_null_column_renders_null_and_zero_stays_zero(self, reg, slug, monkeypatch):
        # The schema's `metric_value REAL NOT NULL` means a real null column
        # cannot be written through upsert_metric/a raw INSERT — so this
        # exercises build_measurements' own pass-through by monkeypatching
        # what query_metrics returns, the way a null WOULD arrive if the
        # column ever did allow one. `zero stays zero` is covered for real
        # (through an actual write) in test_opens_present_exactly_where_a_
        # reader_exists below.
        _log_run(reg, slug, "api_structure", minutes_ago=2)
        real_query_metrics = reg.query_metrics

        def _fake_query_metrics(slug_, kind, scope_locator=""):
            if kind == "api_structure":
                return {"symbol_count": 0, "relationship_count": None,
                        "surveyed_at": "2026-09-14T00:00:00+00:00"}
            return real_query_metrics(slug_, kind, scope_locator)

        monkeypatch.setattr(reg, "query_metrics", _fake_query_metrics)
        m = build_measurements(reg, slug, "api_structure")
        assert m["not_applicable"] is False
        by_name = {row["name"]: row for row in m["measurements"]}
        assert by_name["symbol_count"]["value"] == 0
        assert by_name["relationship_count"]["value"] is None

    def test_opens_present_exactly_where_a_reader_exists(self, reg, slug):
        """Assert against members._READERS directly so this test cannot
        drift from the reader table it's supposed to track."""
        _log_run(reg, slug, "api_structure", minutes_ago=1)
        _write_metric_row(reg, slug, "api_structure",
                          {"symbol_count": 42, "relationship_count": 7})
        m = build_measurements(reg, slug, "api_structure")
        by_name = {row["name"]: row for row in m["measurements"]}

        assert ("api_structure", "symbol_count") in _READERS
        assert by_name["symbol_count"]["opens"] == {
            "analysis_id": "api_structure", "metric": "symbol_count",
        }
        # relationship_count has no reader keyed to it, exact or fallback —
        # _symbol_members (the (api_structure, symbol_count) reader) lists
        # symbols, not relationships.
        assert ("api_structure", "relationship_count") not in _READERS
        assert by_name["relationship_count"]["opens"] is None

    def test_cve_scan_opens_only_the_metric_the_fallback_reader_lists(self, reg, slug):
        _log_run(reg, slug, "cve_scan", minutes_ago=1)
        _write_metric_row(reg, slug, "cve_scan", {
            "advisories": 3, "packages_affected": 2, "checked": 10, "unqueryable": 0,
        })
        m = build_measurements(reg, slug, "cve_scan")
        by_name = {row["name"]: row for row in m["measurements"]}
        assert ("cve_scan", None) in _READERS
        assert by_name["advisories"]["opens"] == {"analysis_id": "cve_scan", "metric": "advisories"}
        assert by_name["packages_affected"]["opens"] is None
        assert by_name["checked"]["opens"] is None

    def test_findings_only_analysis_is_not_applicable(self, reg, slug):
        # `license_classification` (a findings_list kind with no metrics
        # kind mapping) never writes to project_analysis_metrics.
        m = build_measurements(reg, slug, "license_classification")
        assert m["not_applicable"] is True
        assert m["measurements"] == []
        assert "records findings, not measurements" in m["reason"]
        assert "license_classification" in m["reason"]

    def test_metrics_kind_exists_but_nothing_recorded_yet(self, reg, slug):
        m = build_measurements(reg, slug, "api_structure")
        assert m["not_applicable"] is False
        assert m["measurements"] == []
        assert "has not recorded measurements" in m["reason"]

    def test_unknown_analysis_id_raises_lookup_error(self, reg, slug):
        with pytest.raises(LookupError):
            build_measurements(reg, slug, "not_a_real_analysis")

    def test_unknown_slug_raises_lookup_error(self, reg):
        with pytest.raises(LookupError):
            build_measurements(reg, "no-such-slug-at-all", "api_structure")

    def test_footer_names_the_analysis_and_the_run_age(self, reg, slug):
        _log_run(reg, slug, "api_structure", minutes_ago=2)
        _write_metric_row(reg, slug, "api_structure", {"symbol_count": 1})
        m = build_measurements(reg, slug, "api_structure")
        assert m["footer"].startswith("Read from api_structure, run ")
        assert "ago" in m["footer"]

    def test_footer_says_run_date_not_recorded_when_never_run(self, reg, slug):
        m = build_measurements(reg, slug, "api_structure")
        assert "run date not recorded" in m["footer"]


@pytest.fixture
def db_slug(request):
    """Same disambiguation trick as `slug` above, for a database row in the
    same shared `databases` table."""
    import hashlib
    import re as _re
    h = hashlib.sha1(request.node.nodeid.encode()).hexdigest()[:8]
    base = _re.sub(r"[^a-z0-9]+", "_", request.node.name.lower())[:24]
    return f"spdb_{base}_{h}"


@pytest.fixture
def db_reg(pg_registry, db_slug):
    pg_registry.register_database(DatabaseEntity(
        slug=db_slug, display_name=db_slug, db_type="postgresql",
        host="localhost", port=5442, database_name=db_slug,
    ))
    return pg_registry


class TestBuildMeasurementsForADatabase:
    """Bug report 2026-09-23: "the numbers behind this" 404'd for a database
    slug with "Project '<slug>' not found" — `build_measurements()` always
    did a repo-only `registry.get()` lookup and always checked the analysis
    id against repo's own `ANALYSIS_KINDS`, regardless of `entity_type`. The
    fourth instance of PR #226/#233/#236's "resource-type never threaded
    through" bug class. These pin the fix for the exact repro case: a
    database slug, `row_count_snapshot`/`schema_inventory`."""

    def test_unknown_database_slug_raises_lookup_error(self, pg_registry):
        with pytest.raises(LookupError):
            build_measurements(pg_registry, "no-such-database-at-all",
                              "row_count_snapshot", entity_type="database")

    def test_a_real_database_slug_is_found_not_404d_as_a_project(self, db_reg, db_slug):
        # Before the fix this raised LookupError("Project '...' not found")
        # even though the slug is a real, registered database.
        m = build_measurements(db_reg, db_slug, "row_count_snapshot", entity_type="database")
        assert m["analysis_id"] == "row_count_snapshot"

    def test_unknown_analysis_id_for_database_raises_lookup_error(self, db_reg, db_slug):
        with pytest.raises(LookupError):
            build_measurements(db_reg, db_slug, "not_a_real_analysis", entity_type="database")

    def test_repo_only_analysis_id_is_unknown_for_a_database(self, db_reg, db_slug):
        # api_structure is real, but it's a REPO analysis id -- a database's
        # own catalog (DATABASE_ANALYSIS_KINDS) must not recognise it.
        with pytest.raises(LookupError):
            build_measurements(db_reg, db_slug, "api_structure", entity_type="database")

    def test_row_count_snapshot_returns_real_scalar_measurements(self, db_reg, db_slug):
        # database_tables detail rows are what _row_count_snapshot_results
        # (the database's own results_reader) reads -- no
        # project_analysis_metrics/query_metrics involved at all, unlike repo.
        db_reg.write_detail_rows(
            "database_tables", db_slug, "2026-09-23T00:00:00+00:00",
            rows=[
                {"schema_name": "public", "table_name": "orders",
                 "table_type": "BASE TABLE", "row_count": 100, "size_bytes": 200000},
                {"schema_name": "public", "table_name": "customers",
                 "table_type": "BASE TABLE", "row_count": 50, "size_bytes": 152320},
            ],
        )
        m = build_measurements(db_reg, db_slug, "row_count_snapshot", entity_type="database")
        assert m["not_applicable"] is False
        by_name = {row["name"]: row for row in m["measurements"]}
        assert by_name["relation_count"]["value"] == 2
        assert by_name["measured_count"]["value"] == 2
        assert by_name["total_row_count"]["value"] == 150
        assert by_name["total_size_bytes"]["value"] == 352320
        # The per-table breakdown ("tables") is a nested list -- it belongs
        # to the members drill-down, not a scalar measurement row.
        assert "tables" not in by_name

    def test_schema_inventory_returns_real_scalar_measurements(self, db_reg, db_slug):
        db_reg.write_detail_rows(
            "database_tables", db_slug, "2026-09-23T00:00:00+00:00",
            rows=[{"schema_name": "public", "table_name": "orders",
                   "table_type": "BASE TABLE", "row_count": 100}],
        )
        db_reg.write_detail_rows(
            "database_columns", db_slug, "2026-09-23T00:00:00+00:00",
            rows=[
                {"schema_name": "public", "table_name": "orders", "column_name": "id"},
                {"schema_name": "public", "table_name": "orders", "column_name": "total"},
            ],
        )
        m = build_measurements(db_reg, db_slug, "schema_inventory", entity_type="database")
        assert m["not_applicable"] is False
        by_name = {row["name"]: row for row in m["measurements"]}
        assert by_name["relation_count"]["value"] == 1
        assert by_name["column_count"]["value"] == 2

    def test_no_data_yet_is_not_applicable_false_with_a_reason(self, db_reg, db_slug):
        # Nothing written to database_tables yet -- "not recorded", not a
        # findings-only analysis.
        m = build_measurements(db_reg, db_slug, "row_count_snapshot", entity_type="database")
        assert m["not_applicable"] is False
        assert m["measurements"] == []
        assert "has not recorded measurements" in m["reason"]


class TestBuildMeasurementsIsLevelAwareForADatabase:
    """Slice 21a point 4: "the numbers behind this" for a container-level
    question ("Which schemas carry the data...?") must show the per-schema
    breakdown, not the same flat table/column/row scalars "How big is this
    database" (`level="resource"`) shows — found live, owner's question,
    2026-09-26."""

    def test_default_level_is_unchanged_resource_scalars(self, db_reg, db_slug):
        db_reg.write_detail_rows(
            "database_tables", db_slug, "2026-09-23T00:00:00+00:00",
            rows=[{"schema_name": "public", "table_name": "orders",
                   "table_type": "BASE TABLE", "row_count": 100}],
        )
        m = build_measurements(db_reg, db_slug, "schema_inventory", entity_type="database")
        by_name = {row["name"]: row for row in m["measurements"]}
        assert by_name["relation_count"]["value"] == 1

    def test_container_level_returns_a_per_schema_row_not_resource_scalars(self, db_reg, db_slug):
        db_reg.write_detail_rows(
            "database_tables", db_slug, "2026-09-23T00:00:00+00:00",
            rows=[
                {"schema_name": "coco_ods", "table_name": "orders",
                 "table_type": "BASE TABLE", "row_count": 1000, "size_bytes": 500000},
                {"schema_name": "eu_sales", "table_name": "leads",
                 "table_type": "BASE TABLE", "row_count": 0},
                {"schema_name": "pg_catalog", "table_name": "pg_class",
                 "table_type": "BASE TABLE", "row_count": 5000},
            ],
        )
        m = build_measurements(db_reg, db_slug, "schema_inventory",
                                entity_type="database", level="container")
        assert m["not_applicable"] is False
        names = [row["name"] for row in m["measurements"]]
        assert "coco_ods" in names
        assert "eu_sales" in names
        assert "relation_count" not in names  # not the resource scalar shape
        by_name = {row["name"]: row for row in m["measurements"]}
        assert "1,000 row(s)" in by_name["coco_ods"]["value"]
        assert by_name["eu_sales"]["note"] == "empty"
        assert any("system schema" in row["name"] for row in m["measurements"])

    def test_container_level_falls_back_when_no_container_reader_registered(self, db_reg, db_slug):
        # row_count_snapshot has no container-level reader registered
        # (only schema_inventory does) -- must fall back to its ordinary
        # resource-level reading rather than erroring or returning nothing.
        db_reg.write_detail_rows(
            "database_tables", db_slug, "2026-09-23T00:00:00+00:00",
            rows=[{"schema_name": "public", "table_name": "orders",
                   "table_type": "BASE TABLE", "row_count": 100, "size_bytes": 1000}],
        )
        m = build_measurements(db_reg, db_slug, "row_count_snapshot",
                                entity_type="database", level="container")
        by_name = {row["name"]: row for row in m["measurements"]}
        assert by_name["relation_count"]["value"] == 1


class TestRunnableAndReasonThreadsEntityType:
    """Live-reproduced 2026-09-25: `runnable_and_reason()` always resolved
    against repo's own catalog (`resolve_analysis_plan` defaulting to
    "repo"), so every database analysis row in the Survey & Analyses pane
    showed a disabled "run" button with "has no mapped survey step(s)" --
    including `schema_inventory`, which had just run successfully seconds
    earlier via a direct API call to the (unaffected) database run route.
    These pin the fix: `entity_type` must reach `resolve_analysis_plan`."""

    def test_a_database_only_analysis_id_is_runnable_for_database(self):
        # db_activity_signals exists only in the database catalog -- it is
        # NOT a repo analysis id, so the pre-fix default ("repo") reported it
        # as having no mapped step(s) at all.
        runnable, reason = runnable_and_reason("db_activity_signals", "database")
        assert runnable is True
        assert reason == ""

    def test_the_same_id_is_not_runnable_for_repo(self):
        # Confirms the check is genuinely entity-type-scoped, not just
        # permissive now -- a database-only id must still be rejected when
        # asked about repo.
        runnable, reason = runnable_and_reason("db_activity_signals", "repo")
        assert runnable is False
        assert "no mapped survey step(s)" in reason

    def test_build_analyses_index_reports_a_database_row_as_runnable(self, db_reg, db_slug):
        idx = build_analyses_index(db_reg, db_slug, entity_type="database")
        row = next(r for r in idx["analyses"] if r["analysis_id"] == "db_activity_signals")
        assert row["runnable"] is True
        assert row["runnable_reason"] == ""


class TestSlice17DbDerivedRunnabilityFromCatalog:
    """Slice 17 (SLICE-17-RUNNABILITY-FROM-CATALOG-IMPLEMENTED.md), replying to REVIEW-SURVEY-PANE-285.md §5(a).

    `subject_signals`, `coverage_signals` and `preliminary_fit` (design
    §16.3's Scouting/Discovery rows, added to `db_derived.DB_DERIVED_
    ANALYSES` 2026-09-24) had a real results reader and a real run-route
    dispatch (both keyed off `DB_DERIVED_ANALYSES` directly) from the day
    they were added — but `survey_definition_adapter.py`'s OWN, separately
    hand-maintained `DATABASE_ANALYSIS_STEP_MAP` (now `DATABASE_ANALYSIS_RE_
    STEP_MAP`, and now derived from `DB_DERIVED_ANALYSES` rather than
    hand-listed) was never updated to include them, so `resolve_analysis_
    plan`/`runnable_and_reason` — the ONLY thing standing between "has a
    reader and a route" and "the Run button actually renders enabled" —
    reported "no mapped survey step(s)" for exactly these three ids,
    live-reproduced against `coco_pharma`. These pin the fix by name, not
    just "some id works now", per the coordinator brief's own instruction
    for this slice."""

    @pytest.mark.parametrize(
        "analysis_id", ["subject_signals", "coverage_signals", "preliminary_fit"],
    )
    def test_the_three_previously_broken_ids_are_runnable(self, analysis_id):
        runnable, reason = runnable_and_reason(analysis_id, "database")
        assert runnable is True
        assert reason == ""

    @pytest.mark.parametrize(
        "analysis_id", ["subject_signals", "coverage_signals", "preliminary_fit"],
    )
    def test_build_analyses_index_reports_them_runnable(self, db_reg, db_slug, analysis_id):
        idx = build_analyses_index(db_reg, db_slug, entity_type="database")
        row = next(r for r in idx["analyses"] if r["analysis_id"] == analysis_id)
        assert row["runnable"] is True
        assert row["runnable_reason"] == ""

    def test_every_db_derived_id_agrees_between_the_two_step_maps(self):
        """Structural guard against this exact bug recurring: every id in
        `DB_DERIVED_ANALYSES` (the run route's and the results map's own
        source of truth) must also be `"db_derived"`-mapped in
        `DATABASE_ANALYSIS_RE_STEP_MAP` (the runnability precheck's source),
        by construction rather than by two lists happening to agree."""
        from resource_explorer.surveyors.database.db_derived import DB_DERIVED_ANALYSES
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_RE_STEP_MAP,
        )

        for analysis_id in DB_DERIVED_ANALYSES:
            assert DATABASE_ANALYSIS_RE_STEP_MAP.get(analysis_id) == ["db_derived"], analysis_id

    #: ENRICHMENT-E1-CONTEXT-TAB (2026-09-29 amendment): three catalog
    #: entries `intent: enrichment`, `source: local, action: survey`,
    #: registered so Enrichment's "Survey & analyses" map has real ids to
    #: point at, but declared PLACEHOLDER-ONLY in analysis_catalog.yaml's
    #: own comment — no runner exists for any of the three (see that
    #: comment: "Not built. Placeholder registration only"). This is a
    #: DIFFERENT case from the slice-17 bug this class guards: that bug was
    #: an id that WAS runnable (a real db_derived reader + run-route entry)
    #: reading as not-runnable because one hand-maintained map lagged
    #: another. These three genuinely have no implementation anywhere, so
    #: `runnable_and_reason` reporting "not runnable" is the TRUTH, not a
    #: recurrence of the bug — named here, the same way
    #: `DISCOVERY_FETCHES_ANYWAY` names its own exceptions, so a future
    #: genuinely-unmapped id still fails this guard rather than silently
    #: joining this set.
    ENRICHMENT_STUBS_WITH_NO_RUNNER_YET = {
        "doc_source_ingestion", "doc_evidence_check", "semantic_suggestions",
    }

    def test_every_local_survey_database_id_has_a_re_step_map_entry(self):
        """The catalog cross-check the brief asked for: every
        `analysis_catalog.yaml` database entry with `source: local, action:
        survey` (i.e. every id the Run route and the Questions tab could
        conceivably offer) resolves to a real step here — a database entry
        added to the catalog without a corresponding entry here is exactly
        how this bug happened the first time, so this is a standing guard,
        not a one-off regression pin."""
        from resource_explorer.surveyors.analysis_catalog_reader import get_analyses
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_RE_STEP_MAP,
        )

        local_survey_ids = {
            a["id"] for a in get_analyses("database", include_egeria_live=False)
            if a.get("source") == "local" and a.get("action") == "survey"
        }
        assert local_survey_ids
        mapped_ids = local_survey_ids - self.ENRICHMENT_STUBS_WITH_NO_RUNNER_YET
        assert mapped_ids <= set(DATABASE_ANALYSIS_RE_STEP_MAP)
        for analysis_id in mapped_ids:
            runnable, reason = runnable_and_reason(analysis_id, "database")
            assert runnable is True, f"{analysis_id}: {reason}"
        # The stubs' OWN honest state: genuinely not runnable, and the
        # standard route says so rather than crashing or claiming otherwise.
        for analysis_id in self.ENRICHMENT_STUBS_WITH_NO_RUNNER_YET:
            assert analysis_id in local_survey_ids, f"{analysis_id} is no longer in the catalog — update this set"
            runnable, reason = runnable_and_reason(analysis_id, "database")
            assert runnable is False
            assert reason


class TestFetchStepCounts:
    def test_mixed_step_list_splits_correctly_and_surfaces_unregistered(self):
        # repo_file_inventory and repo_manifest_parse both declare
        # requires_resources={"zipball_root": ...}; repo_language declares
        # none; "not_a_real_step" is in no STEP_REGISTRY at all.
        step_count, fetch_steps, unregistered = fetch_step_counts([
            "repo_file_inventory", "repo_manifest_parse", "repo_language", "not_a_real_step",
        ])
        assert step_count == 4
        assert fetch_steps == 2
        assert unregistered == ["not_a_real_step"]

    def test_empty_list(self):
        assert fetch_step_counts([]) == (0, 0, [])

    def test_every_step_fetches(self):
        step_count, fetch_steps, unregistered = fetch_step_counts(
            ["repo_file_inventory", "repo_manifest_parse"])
        assert step_count == fetch_steps == 2
        assert unregistered == []


class TestBuildAnalysesIndex:
    def test_question_backed_analysis_serves_question(self, reg, slug):
        idx = build_analyses_index(reg, slug)
        by_id = {r["analysis_id"]: r for r in idx["analyses"]}
        question_backed = [r for r in idx["analyses"] if r["questions"]]
        assert question_backed, "expected at least one analysis with a naming question"
        row = question_backed[0]
        assert row["serves"] == "question"

    def test_results_reader_with_no_question_is_chat_only(self, reg, slug):
        from resource_explorer.surveyors.analysis_catalog_reader import get_analyses
        from resource_explorer.surveyors.question_catalog_reader import get_questions
        from resource_explorer.surveyors.repo_survey_definition_adapter import ANALYSIS_KINDS

        questions = get_questions("repo")
        named = set()
        for q in questions:
            named.update(q["answering"]["analysis_ids"] or [])

        candidate = None
        for a in get_analyses("repo", include_egeria_live=False):
            if a["id"] in named or a.get("action") == "publish":
                continue
            kind = ANALYSIS_KINDS.get(a["id"])
            if kind and kind.results is not None:
                candidate = a["id"]
                break
        assert candidate, "expected a results-reader analysis with no naming question"

        idx = build_analyses_index(reg, slug)
        row = next(r for r in idx["analyses"] if r["analysis_id"] == candidate)
        assert row["serves"] == "chat-only"
        assert row["questions"] == []

    def test_neither_question_nor_reader_is_nothing_yet(self, reg, slug):
        from resource_explorer.surveyors.analysis_catalog_reader import get_analyses
        from resource_explorer.surveyors.question_catalog_reader import get_questions
        from resource_explorer.surveyors.repo_survey_definition_adapter import ANALYSIS_KINDS

        questions = get_questions("repo")
        named = set()
        for q in questions:
            named.update(q["answering"]["analysis_ids"] or [])

        candidate = None
        for a in get_analyses("repo", include_egeria_live=False):
            if a["id"] in named or a.get("action") == "publish":
                continue
            kind = ANALYSIS_KINDS.get(a["id"])
            if not kind or kind.results is None:
                candidate = a["id"]
                break

        idx = build_analyses_index(reg, slug)
        if candidate is None:
            pytest.skip("no analysis in the local catalog has neither a question nor a results reader")
        row = next(r for r in idx["analyses"] if r["analysis_id"] == candidate)
        assert row["serves"] == "nothing-yet"

    def test_short_description_is_the_first_sentence(self, reg, slug):
        idx = build_analyses_index(reg, slug)
        for row in idx["analyses"]:
            if ". " in row["description"]:
                assert row["short_description"] == row["description"].split(". ", 1)[0] + "."
                assert len(row["short_description"]) < len(row["description"])
                break
        else:
            pytest.skip("no catalog description spans more than one sentence")

    def test_egeria_publish_is_excluded_but_its_reason_matches_the_route(self):
        # egeria_publish (action: "publish") never appears as a row — see
        # the analyses-index population test below — but the underlying
        # gate is the same one the run route uses, and is id-only.
        runnable, reason = runnable_and_reason("egeria_publish")
        assert runnable is False
        assert reason == (
            "Analysis 'egeria_publish' has no mapped survey step(s) — "
            "either it's a publish action (not a survey) or an unknown id."
        )

    def test_publish_actions_are_excluded_from_the_index(self, reg, slug):
        idx = build_analyses_index(reg, slug)
        ids = {r["analysis_id"] for r in idx["analyses"]}
        assert "egeria_publish" not in ids

    def test_last_run_via_carries_the_source_for_a_derived_analysis(self, reg, slug):
        # architecture_diagram owns no steps of its own — it derives from
        # architecture_recovery's (AnalysisKind.derives_from). Only the
        # SOURCE ran directly; the derived row must still report a run,
        # attributed to the source, not "never run."
        _log_run(reg, slug, "architecture_recovery", minutes_ago=3)
        idx = build_analyses_index(reg, slug)
        row = next(r for r in idx["analyses"] if r["analysis_id"] == "architecture_diagram")
        assert row["last_run_at"]
        assert row["last_run_via"] == "architecture_recovery"

    def test_counts_never_run_and_no_question(self, reg, slug):
        idx = build_analyses_index(reg, slug)
        assert idx["counts"]["total"] == len(idx["analyses"])
        assert idx["counts"]["never_run"] == sum(
            1 for r in idx["analyses"] if not r["last_run_at"])
        assert idx["counts"]["no_question"] == sum(
            1 for r in idx["analyses"] if r["serves"] != "question")

    def test_unknown_slug_raises_lookup_error(self, reg):
        with pytest.raises(LookupError):
            build_analyses_index(reg, "no-such-slug-at-all")

    def test_catalog_field_is_the_entry_verbatim(self, reg, slug):
        from resource_explorer.surveyors.analysis_catalog_reader import get_analyses

        by_id = {a["id"]: a for a in get_analyses("repo", include_egeria_live=False)}
        idx = build_analyses_index(reg, slug)
        for row in idx["analyses"]:
            assert row["catalog"] == by_id[row["analysis_id"]]
