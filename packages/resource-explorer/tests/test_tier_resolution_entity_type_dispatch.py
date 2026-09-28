"""`_step_key_owner()`/`tier_of_activity_row()` used to consult only
`REPO_ANALYSIS_STEP_MAP`, with no per-`entity_type` branch — unlike
`registry.py`'s own `_analysis_step_map(entity_type)`, which already dispatches
correctly. A database/filesystem `survey` row's steps could therefore never be
found in the (repo-only) ownership map and always resolved to
`unknown-analysis`, regardless of what actually ran.

Low visibility (only exercised by tests and an ad-hoc cost report today, per
`tier_resolution.py`'s own module docstring on why this matters at all) but a
real defect in the same "true statement about the mechanism read as a claim
about the world" family the rest of this module exists to guard against.
"""
from __future__ import annotations

import pytest

from resource_explorer.tier_resolution import clear_cache, tier_of_activity_row


@pytest.fixture(autouse=True)
def _fresh_caches():
    clear_cache()
    yield
    clear_cache()


def _survey(*step_keys, process="Survey"):
    return {"steps": [{"step": f"GovActionProcessStep::{process}::{k}", "status": "ok"}
                      for k in step_keys]}


class TestStepOwnershipDispatchesByEntityType:
    def test_a_database_survey_step_resolves_against_the_database_step_map(self):
        """`postgres_nested_columns` only means anything (and only maps to
        exactly one owner) under DATABASE_ANALYSIS_RE_STEP_MAP — resolving it
        against repo's step map (the pre-fix behavior) would never find it."""
        a = tier_of_activity_row("survey", _survey("postgres_nested_columns"),
                                 entity_type="database")
        assert a.resolved, (
            "a database survey step failed to resolve — _step_key_owner is "
            "still only consulting the repo-only step map")
        assert a.analyses == frozenset({"nested_column_profile"})

    def test_a_filesystem_survey_step_resolves_against_the_filesystem_step_map(self):
        a = tier_of_activity_row("survey", _survey("filesystem_inventory"),
                                 entity_type="filesystem")
        assert a.resolved
        assert a.analyses == frozenset({"filesystem_inventory"})

    def test_default_entity_type_stays_repo_for_backward_compatibility(self):
        """Existing callers that don't pass entity_type (and the pre-fix test
        suite) must keep resolving repo steps exactly as before."""
        a = tier_of_activity_row("survey", _survey("repo_arch_detect"))
        assert a.resolved
        assert a.analyses == frozenset({"architecture_recovery"})

    def test_a_repo_step_key_does_not_leak_into_a_database_lookup(self):
        """A repo-only step key must not resolve under entity_type=database —
        the maps are disjoint by resource type, not merged."""
        a = tier_of_activity_row("survey", _survey("repo_arch_detect"),
                                 entity_type="database")
        assert a.state == "unknown-analysis"
        assert not a.tiers
