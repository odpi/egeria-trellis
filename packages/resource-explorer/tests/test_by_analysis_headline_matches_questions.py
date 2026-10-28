"""The By-analysis panel's per-board headline must equal what the Questions
tab computes for that analysis's lead question -- same function, same
inputs (BRIEF-BY-ANALYSIS-PANEL-USABILITY.md's own backend test ask).

Rather than exercising both read paths end-to-end (which would need a live
registry with survey data for every analysis id, in every entity type), this
pins the thing that MAKES them equal: `workflows.analysis._read_analyses`
(which the By-analysis payload's `a.headline` comes from) and `facts.
FactLayer._headline_for` (which the Questions tab's envelope-building goes
through) both resolve to the exact same `headline_reader` FUNCTION OBJECT
for a given analysis_id -- not two functions that happen to compute the same
thing today and can silently drift apart tomorrow. `_read_analyses` calls
`headline_map.get(analysis_id)` directly; `FactLayer._headline_for` reaches
the same reader via `analysis_kinds[analysis_id].results.headline_reader`.
Both maps are built from `analysis_kinds=lambda: X_ANALYSIS_KINDS` /
`analysis_headline_map=lambda: X_ANALYSIS_HEADLINE_MAP` in each adapter
module, and each `AnalysisKind.results.headline_reader` is constructed as
`X_ANALYSIS_HEADLINE_MAP.get(analysis_id)` (see e.g. `survey_definition_
adapter.py`'s `DATABASE_ANALYSIS_KINDS` construction) -- so an `is` check
below is a real, direct assertion that both callers run the identical code
with the identical (registry, slug) arguments, not a paraphrase of it.
"""
from __future__ import annotations

import pytest


@pytest.mark.parametrize("entity_type", ["database", "repo"])
def test_headline_map_and_analysis_kinds_share_the_same_reader_object(entity_type):
    # Only these two entity types register a `analysis_kinds=lambda: ...`
    # FactLayer provider (see `facts.py`'s per-entity-type construction) --
    # filesystem's Questions tab does not resolve headlines through
    # `FactLayer._headline_for`'s `analysis_kinds` path the same way, so
    # there is no equivalent "same object" claim to pin for it here.
    if entity_type == "database":
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_HEADLINE_MAP as headline_map,
        )
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_KINDS as kinds,
        )
    else:
        from resource_explorer.surveyors.repo_survey_definition_adapter import (
            ANALYSIS_KINDS as kinds,
        )
        from resource_explorer.surveyors.repo_survey_definition_adapter import (
            REPO_ANALYSIS_HEADLINE_MAP as headline_map,
        )

    checked = 0
    for analysis_id, reader in headline_map.items():
        kind = kinds.get(analysis_id)
        if kind is None:
            continue
        kind_reader = getattr(getattr(kind, "results", None), "headline_reader", None)
        assert kind_reader is reader, (
            f"{entity_type}/{analysis_id}: the By-analysis payload's headline reader "
            "and the Questions-tab envelope's headline reader must be the SAME "
            "function object, or the two panes' headlines can silently drift apart"
        )
        checked += 1
    assert checked, f"no headline_map entries had a matching analysis_kinds entry for {entity_type}"


class TestListSurveyResultBoardsIsCatalogOnly:
    """The contents board's cheap half: no results/headline reader called."""

    def test_database_boards_listing_never_touches_results_map_readers(self):
        import inspect

        from resource_explorer.workflows.analysis import list_survey_result_boards

        src = inspect.getsource(list_survey_result_boards)
        assert "_read_analyses" not in src
        assert "results_reader" not in src
        assert "headline_reader(" not in src

    def test_returns_the_same_board_ids_build_survey_results_would_synthesize(self):
        from resource_explorer.surveyors.analysis_catalog_reader import get_analyses
        from resource_explorer.workflows.analysis import (
            _results_map_for,
            list_survey_result_boards,
        )

        class _FakeRegistry:
            def get_database(self, slug, **kw):
                return object()

            def get_last_published_annotation_types(self, slug):
                return {}

            def get_egeria_linkage(self, kind, slug):
                return {}

        results_map, _headline_map = _results_map_for("database")
        catalog_by_id = {a["id"]: a for a in get_analyses("database", include_egeria_live=False)}
        expected_ids = set(results_map.keys())

        boards = list_survey_result_boards(_FakeRegistry(), "database", "some-slug", "")
        got_ids = {b["id"] for b in boards["boards"]}
        assert got_ids == expected_ids
        for b in boards["boards"]:
            entry = catalog_by_id.get(b["id"])
            if entry:
                assert b["title"] == (entry.get("name") or b["id"].replace("_", " ").title())


class TestBuildSurveyResultsBoardIdFilter:
    """`board_id` scopes the read to exactly one board -- the progressive
    fetch's per-board request."""

    def test_board_id_param_exists_on_build_survey_results(self):
        import inspect

        from resource_explorer.workflows.analysis import build_survey_results

        sig = inspect.signature(build_survey_results)
        assert "board_id" in sig.parameters
        assert sig.parameters["board_id"].default == ""
