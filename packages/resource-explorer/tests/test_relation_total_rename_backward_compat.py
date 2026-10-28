"""Backward compatibility for the `table_total`/`table_select` ->
`relation_total`/`relation_select` rename (REPLY-DESIGNER-ROUND2-DATABASE-
SCREENS.md §2.3, see also `TABLE-COUNT-RENAME-IMPLEMENTED.md`).

Every `credential_capability` probe already stored under a survey's
`survey_data` blob before this rename carries the OLD top-level key names.
Nothing rewrites those rows retroactively, so every reader this rename
touched must keep accepting the old key as a fallback for the new one --
architecture-session requirement, verbatim: "read `relation_total`, else
`table_total` -- otherwise every card on coco_pharma and adventureworks
goes blank until a re-survey."

This file feeds a PRE-RENAME-SHAPED fixture (old keys ONLY, no new ones,
same as a real stored row from before this change) through each of the
four readers that consume a `credential_capability` probe, and asserts each
still produces the same correct output it would from a post-rename fixture.
`_schema_inventory_results`/`_row_count_snapshot_results` are NOT covered
here: both build their own value dict fresh from `database_tables` every
call rather than reading a stored `table_count`/`relation_count` field
back, so there is no old-shaped blob for them to be backward compatible
with (see each function's own docstring/comment).
"""
from __future__ import annotations

import json

import pytest

from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.surveyors import credential_capability as capability_module
from resource_explorer.surveyors.database import schema_scope
from resource_explorer.surveyors.database.connection import (
    POSTGRES_CONTAINMENT as _POSTGRES_CONTAINMENT,
)
from resource_explorer.surveyors.database.database_surveyor import DatabaseSurveyor
from resource_explorer.surveyors.database.survey_definition_adapter import (
    _credential_capability_results,
    _credential_scope_status,
)

#: A `credential_capability` probe exactly as it was stored before this
#: rename -- old keys only, both at the whole-database level and per-schema
#: (the per-schema `by_schema[...]` keys were never renamed, so they are
#: identical pre- and post-rename; only the top-level pair changed).
_PRE_RENAME_CAP = {
    "connected_as": "egeria_user",
    "schema_total": 8,
    "schema_visible": 6,
    "table_total": 61,
    "table_select": 3,
    "by_schema": {
        "coco_ods": {"usage_granted": True, "table_total": 23, "table_select": 0},
        "eu_sales": {"usage_granted": True, "table_total": 1, "table_select": 1},
    },
    "stats_role": False,
    "write_probed": True,
    "write_capable": False,
}


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "test.db"))


@pytest.fixture
def db_entity(registry):
    entity = DatabaseEntity(
        slug="coco_pharma", display_name="Coco Pharma", db_type="postgresql",
        host="localhost", port=5432, database_name="coco_pharma",
    )
    registry.register_database(entity)
    return entity


class TestCredentialCapabilityAssess:
    """`credential_capability.assess()` reads `probe.get("relation_total",
    probe.get("table_total"))` -- a pre-rename probe must still produce a
    real READ assessment, not "no denominator" (total <= 0)."""

    def test_pre_rename_probe_still_assesses_read_capability(self):
        result = capability_module.assess(capability_module.READ, _PRE_RENAME_CAP)
        assert result.known is True
        assert result.satisfied is False
        assert result.have == 3
        assert result.of == 61
        assert "3 of 61 relation(s)" in result.detail


class TestSchemaScopeCredentialShortfall:
    """`schema_scope.credential_shortfall()` reads the same fallback --a
    pre-rename cap must still produce the "N of M relation(s)" phrase, not
    "0 of 0"."""

    def test_pre_rename_cap_still_produces_a_real_phrase(self):
        shortfall = schema_scope.credential_shortfall(_PRE_RENAME_CAP, _POSTGRES_CONTAINMENT)
        assert shortfall is not None
        assert shortfall["relation_total"] == 61
        assert shortfall["relation_select"] == 3
        assert "3 of 61 relation(s)" in shortfall["phrase"]


class TestCredentialScopeStatusReader:
    """`_credential_scope_status()` (survey_definition_adapter.py) reads a
    `credential_capability` blob back from the registry's stored
    `survey_data` -- the actual, real-world shape a pre-rename stored survey
    has. Without the fallback this reads `relation_total: 0`, decides there
    is no denominator, and returns `None` -- silently dropping the third
    fact-envelope state for every database surveyed before this rename."""

    def test_a_pre_rename_stored_survey_still_produces_the_third_state(self, registry, db_entity):
        registry.record_database_survey(
            slug=db_entity.slug, schema_count=8, table_count=61, column_count=0,
            survey_data={"credential_capability": _PRE_RENAME_CAP},
        )
        # Sanity: the stored blob really is old-shaped (no new keys at all).
        cap = _credential_capability_results(registry, db_entity.slug)
        assert "relation_total" not in cap
        assert cap["table_total"] == 61

        status = _credential_scope_status(registry, db_entity.slug)
        assert status is not None
        assert "3 of 61 relation(s)" in status["fraction"]
        assert "6 of 8" in status["fraction"]


class TestCredentialCapabilityAnnotationBuilder:
    """`DatabaseSurveyor._create_credential_capability_annotations()`
    (database_surveyor.py) is the RFA/annotation builder -- called either
    with a fresh probe result or, via `EgeriaDatabaseSurveyor.
    publish_step_annotations`, a Survey-Definition step's own stored output,
    which can be old-shaped. Must still name the real fraction and fire the
    thin-coverage RFA, not silently report 0 of 0."""

    def test_pre_rename_info_still_produces_a_real_summary_and_rfa(self, registry, db_entity):
        surveyor = DatabaseSurveyor(db_entity, {"user": "u", "password": "p"}, registry)
        annotations = surveyor._create_credential_capability_annotations(_PRE_RENAME_CAP)

        summary_annotation = annotations[0]
        assert "3 of 61 relation(s)" in summary_annotation.summary
        assert summary_annotation.resource_properties["relation_total"] == 61
        assert summary_annotation.resource_properties["relation_select"] == 3

        rfa = [a for a in annotations if hasattr(a, "action_requested")]
        assert rfa, "thin coverage (3 of 61) must still raise the RFA from an old-shaped probe"
        assert "relation(s)" in rfa[0].summary
