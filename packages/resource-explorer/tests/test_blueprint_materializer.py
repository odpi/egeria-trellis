"""Tests for BlueprintMaterializer — the accept-verdict -> real Egeria
SolutionBlueprint step (docs/blueprint-materialization-plan.md), Phase A.

Mirrors test_component_materializer.py's mocking shape: construct the class
with a fake platform_url, replace the pyegeria client attributes directly
with MagicMocks, and short-circuit _connect() so no real network call is
ever attempted. No live Egeria in any test here.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from resource_explorer.surveyors.arch_recovery.blueprint_materializer import (
    BlueprintMaterializationError, BlueprintMaterializer,
)

# Brief I: a blueprint or port is written by a queued publish: RE's daemon on the requester's behalf.
pytestmark = pytest.mark.usefixtures("as_daemon")


def _materializer(registry=None):
    m = BlueprintMaterializer(platform_url="https://fake", registry=registry)
    m._solution_architect = MagicMock()
    m._automated_curation = MagicMock()
    m._connect = MagicMock()  # short-circuit: attributes above stand in for a real connection
    m._automated_curation.get_guid_for_name.return_value = []  # nothing pre-exists by default
    m._solution_architect.create_solution_blueprint.return_value = "11111111-1111-1111-1111-111111111111"
    return m


class TestQualifiedName:
    def test_shape_matches_the_rest_of_the_codebase(self):
        qn = BlueprintMaterializer.qualified_name_for("repo", "myproj", "deployment", "svc-cluster")
        assert qn == "SolutionBlueprint::repo::myproj::deployment::svc-cluster"


class TestFreshMaterialization:
    def test_creates_with_expected_properties(self):
        m = _materializer(registry=MagicMock(get_materialized_blueprint=MagicMock(return_value=None)))
        result = m.materialize_blueprint_element(
            "repo", "myproj", "deployment", "svc-cluster", display_name="svc cluster",
        )
        assert result == {
            "status": "materialized",
            "guid": "11111111-1111-1111-1111-111111111111",
            "qualified_name": "SolutionBlueprint::repo::myproj::deployment",
        }
        m._connect.assert_called_once()
        body = m._solution_architect.create_solution_blueprint.call_args[0][0]
        props = body["properties"]
        assert props["class"] == "SolutionBlueprintProperties"
        assert props["qualifiedName"] == "SolutionBlueprint::repo::myproj::deployment"
        assert props["displayName"] == "svc cluster"
        assert props["additionalProperties"]["recoveredBy"] == "architecture_recovery"

    def test_content_status_draft(self):
        """architecture-recovery.md §10 Phase 2's "All at ContentStatus =
        Draft" — achieved via the real mechanism (contentStatus on
        ReferenceableProperties, egeria-python-65's ISSUE-84 finding), not
        the fictional NewSolutionElementRequestBody/initialStatus shape.
        Confirmed live 2026-09-03: contentStatus round-trips correctly on
        a real blueprint (elementHeader.status stays ACTIVE regardless —
        a different, unrelated axis)."""
        m = _materializer(registry=MagicMock(get_materialized_blueprint=MagicMock(return_value=None)))
        m.materialize_blueprint_element(
            "repo", "myproj", "deployment", "svc-cluster", display_name="svc cluster",
        )
        props = m._solution_architect.create_solution_blueprint.call_args[0][0]["properties"]
        assert props["contentStatus"] == "DRAFT"

    def test_body_shape_matches_what_pyegeria_actually_validates(self):
        """Corrected 2026-09-03 (Backlog.md item 6): the originally-planned
        Draft-status divergence from ComponentMaterializer — class:
        "NewSolutionElementRequestBody" + initialStatus: "DRAFT", per
        SolutionArchitect.create_solution_blueprint's own docstring — fails
        client-side pydantic validation in every installed pyegeria version
        checked (5.3.4.23 here, 6.1.9 in the canonical egeria-python
        checkout): NewSolutionElementRequestBody is documented but never
        defined as a real model, so the client validates every body against
        a bare TypeAdapter(NewElementRequestBody), whose `class` field is
        Literal["NewElementRequestBody"]. Confirmed live 2026-09-03
        accepting a real candidate blueprint against sqlglot — this is the
        regression guard. Same body shape ComponentMaterializer already
        uses (ACTIVE, not Draft, until pyegeria ships a real model)."""
        m = _materializer(registry=MagicMock(get_materialized_blueprint=MagicMock(return_value=None)))
        m.materialize_blueprint_element(
            "repo", "myproj", "deployment", "svc-cluster", display_name="svc cluster",
        )
        body = m._solution_architect.create_solution_blueprint.call_args[0][0]
        assert body["class"] == "NewElementRequestBody"
        assert "initialStatus" not in body
        assert body["isOwnAnchor"] is True

    def test_body_actually_validates_against_installed_pyegeria(self):
        """Not a mock-shape assertion — runs the exact body this method
        builds through pyegeria's own TypeAdapter(NewElementRequestBody),
        the real validator create_solution_blueprint calls internally.
        This is what would have caught the original bug before it reached
        a live accept."""
        from pydantic import TypeAdapter

        from pyegeria.models import NewElementRequestBody

        m = _materializer(registry=MagicMock(get_materialized_blueprint=MagicMock(return_value=None)))
        m.materialize_blueprint_element(
            "repo", "myproj", "deployment", "svc-cluster", display_name="svc cluster",
        )
        body = m._solution_architect.create_solution_blueprint.call_args[0][0]
        TypeAdapter(NewElementRequestBody).validate_python(body)  # raises on failure

    def test_oversized_flag_recorded_in_additional_properties(self):
        m = _materializer(registry=MagicMock(get_materialized_blueprint=MagicMock(return_value=None)))
        m.materialize_blueprint_element(
            "repo", "myproj", "deployment", "svc-cluster", display_name="svc cluster", oversized=True,
        )
        props = m._solution_architect.create_solution_blueprint.call_args[0][0]["properties"]
        assert props["additionalProperties"]["oversized"] == "true"

    def test_no_oversized_key_when_not_oversized(self):
        m = _materializer(registry=MagicMock(get_materialized_blueprint=MagicMock(return_value=None)))
        m.materialize_blueprint_element(
            "repo", "myproj", "deployment", "svc-cluster", display_name="svc cluster",
        )
        props = m._solution_architect.create_solution_blueprint.call_args[0][0]["properties"]
        assert "oversized" not in props["additionalProperties"]

    def test_records_into_the_registry(self):
        registry = MagicMock(get_materialized_blueprint=MagicMock(return_value=None))
        m = _materializer(registry=registry)
        m.materialize_blueprint_element(
            "repo", "myproj", "deployment", "svc-cluster", display_name="svc cluster",
        )
        registry.record_materialized_blueprint.assert_called_once_with(
            "repo", "myproj", "deployment", "svc-cluster",
            "SolutionBlueprint::repo::myproj::deployment",
            "11111111-1111-1111-1111-111111111111",
        )

    def test_bad_guid_from_egeria_raises(self):
        m = _materializer(registry=MagicMock(get_materialized_blueprint=MagicMock(return_value=None)))
        m._solution_architect.create_solution_blueprint.return_value = "not-a-guid"
        with pytest.raises(BlueprintMaterializationError, match="no usable GUID"):
            m.materialize_blueprint_element(
                "repo", "myproj", "deployment", "svc-cluster", display_name="svc cluster",
            )

    def test_create_call_failure_wraps_as_materialization_error(self):
        m = _materializer(registry=MagicMock(get_materialized_blueprint=MagicMock(return_value=None)))
        m._solution_architect.create_solution_blueprint.side_effect = RuntimeError("500 from server")
        with pytest.raises(BlueprintMaterializationError, match="rejected the new SolutionBlueprint"):
            m.materialize_blueprint_element(
                "repo", "myproj", "deployment", "svc-cluster", display_name="svc cluster",
            )


class TestIdempotency:
    def test_local_cache_hit_skips_connect_entirely(self):
        """A repeat accept (re-running the survey, or a retried request)
        must not cost a search call, let alone a create — the local table
        is checked BEFORE _connect()."""
        registry = MagicMock(get_materialized_blueprint=MagicMock(return_value={
            "guid": "22222222-2222-2222-2222-222222222222",
            "qualified_name": "SolutionBlueprint::repo::myproj::deployment::svc-cluster",
        }))
        m = _materializer(registry=registry)
        result = m.materialize_blueprint_element(
            "repo", "myproj", "deployment", "svc-cluster", display_name="svc cluster",
        )
        assert result == {
            "status": "already_materialized",
            "guid": "22222222-2222-2222-2222-222222222222",
            "qualified_name": "SolutionBlueprint::repo::myproj::deployment::svc-cluster",
        }
        m._connect.assert_not_called()
        m._solution_architect.create_solution_blueprint.assert_not_called()

    def test_live_search_hit_records_locally_and_does_not_create(self):
        """No local record yet, but Egeria already has the element (e.g.
        created by a previous run whose local record was lost).
        Search-before-create finds it; create is never called."""
        registry = MagicMock(get_materialized_blueprint=MagicMock(return_value=None))
        m = _materializer(registry=registry)
        m._automated_curation.get_guid_for_name.return_value = [
            "33333333-3333-3333-3333-333333333333"
        ]
        m._solution_architect.get_solution_blueprint_by_guid.return_value = {
            "elementHeader": {"type": {"typeName": "SolutionBlueprint"}},
            "properties": {"qualifiedName": "SolutionBlueprint::repo::myproj::deployment",
                           "displayName": "svc cluster"}}
        result = m.materialize_blueprint_element(
            "repo", "myproj", "deployment", "svc-cluster", display_name="svc cluster",
        )
        assert result["status"] == "already_materialized"
        assert result["guid"] == "33333333-3333-3333-3333-333333333333"
        m._solution_architect.create_solution_blueprint.assert_not_called()
        registry.record_materialized_blueprint.assert_called_once_with(
            "repo", "myproj", "deployment", "svc-cluster",
            "SolutionBlueprint::repo::myproj::deployment",
            "33333333-3333-3333-3333-333333333333",
        )

    def test_repeat_call_after_first_materialize_finds_cached(self):
        """Explicit round-trip idempotency check: materialize once (creates),
        wire the registry mock to reflect that as a real cache would, then
        materialize again — the second call must not create a second
        element."""
        store: dict = {}

        def _get(entity_type, entity_slug, perspective, cluster_name):
            return store.get((entity_type, entity_slug, perspective, cluster_name))

        def _record(entity_type, entity_slug, perspective, cluster_name, qualified_name, guid):
            store[(entity_type, entity_slug, perspective, cluster_name)] = {
                "qualified_name": qualified_name, "guid": guid,
            }

        registry = MagicMock()
        registry.get_materialized_blueprint.side_effect = _get
        registry.record_materialized_blueprint.side_effect = _record

        m = _materializer(registry=registry)
        first = m.materialize_blueprint_element(
            "repo", "myproj", "deployment", "svc-cluster", display_name="svc cluster",
        )
        assert first["status"] == "materialized"

        second = m.materialize_blueprint_element(
            "repo", "myproj", "deployment", "svc-cluster", display_name="svc cluster",
        )
        assert second["status"] == "already_materialized"
        assert second["guid"] == first["guid"]
        m._solution_architect.create_solution_blueprint.assert_called_once()


class TestConnectionFailure:
    def test_no_platform_url_raises_before_any_pyegeria_call(self):
        m = BlueprintMaterializer(registry=MagicMock(
            get_materialized_blueprint=MagicMock(return_value=None)))
        m.platform_url = ""
        with pytest.raises(BlueprintMaterializationError, match="EGERIA_PLATFORM_URL"):
            m.materialize_blueprint_element(
                "repo", "myproj", "deployment", "svc-cluster", display_name="svc cluster",
            )


class TestResolveMemberGuids:
    def test_all_members_materialized(self):
        registry = MagicMock()
        registry.get_materialized_component.side_effect = lambda et, es, scope: {
            "src/a": {"guid": "aaaaaaaa-1111-1111-1111-111111111111"},
            "src/b": {"guid": "bbbbbbbb-1111-1111-1111-111111111111"},
        }.get(scope)
        m = _materializer()
        slug_to_scope = {"svc/a": "src/a", "svc/b": "src/b"}
        resolved, unmet = m.resolve_member_guids(
            registry, "repo", "myproj", ["svc/a", "svc/b"], slug_to_scope,
        )
        assert resolved == {
            "svc/a": "aaaaaaaa-1111-1111-1111-111111111111",
            "svc/b": "bbbbbbbb-1111-1111-1111-111111111111",
        }
        assert unmet == []

    def test_unmaterialized_member_reported_not_raised(self):
        """Decision 2's enforcement point: a member with no materialized
        row is data (reported in `unmet`), never an exception."""
        registry = MagicMock()
        registry.get_materialized_component.side_effect = lambda et, es, scope: (
            {"guid": "aaaaaaaa-1111-1111-1111-111111111111"} if scope == "src/a" else None
        )
        m = _materializer()
        slug_to_scope = {"svc/a": "src/a", "svc/b": "src/b"}
        resolved, unmet = m.resolve_member_guids(
            registry, "repo", "myproj", ["svc/a", "svc/b"], slug_to_scope,
        )
        assert resolved == {"svc/a": "aaaaaaaa-1111-1111-1111-111111111111"}
        assert unmet == ["svc/b"]

    def test_member_slug_missing_from_slug_to_scope_is_unmet_not_raised(self):
        """The identity-mismatch trap the plan calls out explicitly:
        clustering keys members by slug, verdicts/materialization by
        scope_locator. A slug with no entry in slug_to_scope must be
        reported as unmet, not looked up directly (which would silently
        find nothing for every member) or raise."""
        registry = MagicMock()
        registry.get_materialized_component.return_value = {
            "guid": "aaaaaaaa-1111-1111-1111-111111111111"
        }
        m = _materializer()
        resolved, unmet = m.resolve_member_guids(
            registry, "repo", "myproj", ["svc/unknown"], {},
        )
        assert resolved == {}
        assert unmet == ["svc/unknown"]
        registry.get_materialized_component.assert_not_called()

    def test_materialized_row_with_no_guid_is_unmet(self):
        registry = MagicMock()
        registry.get_materialized_component.return_value = {"guid": ""}
        m = _materializer()
        resolved, unmet = m.resolve_member_guids(
            registry, "repo", "myproj", ["svc/a"], {"svc/a": "src/a"},
        )
        assert resolved == {}
        assert unmet == ["svc/a"]

    def test_empty_member_list_returns_empty_without_raising(self):
        registry = MagicMock()
        m = _materializer()
        resolved, unmet = m.resolve_member_guids(registry, "repo", "myproj", [], {})
        assert resolved == {}
        assert unmet == []


class TestResolveChildBlueprintGuids:
    def test_two_level_cluster_children_resolved_by_name(self):
        """Two-level cluster fixture shape, matching
        tests/test_arch_clustering.py's TestRollup fixtures: a parent
        cluster's children are looked up by cluster NAME within the same
        perspective, not by slug."""
        registry = MagicMock()
        registry.get_materialized_blueprint.side_effect = lambda et, es, persp, name: {
            "a": {"guid": "aaaaaaaa-2222-2222-2222-222222222222"},
            "b": {"guid": "bbbbbbbb-2222-2222-2222-222222222222"},
        }.get(name)
        m = _materializer()
        resolved, unmet = m.resolve_child_blueprint_guids(
            registry, "repo", "myproj", "deployment", ["a", "b"],
        )
        assert resolved == {
            "a": "aaaaaaaa-2222-2222-2222-222222222222",
            "b": "bbbbbbbb-2222-2222-2222-222222222222",
        }
        assert unmet == []

    def test_unaccepted_child_reported_not_raised(self):
        """Decision 1's enforcement point: an unaccepted/unmaterialized
        child blueprint is a member the parent write cannot yet attach,
        reported back rather than silently skipped."""
        registry = MagicMock()
        registry.get_materialized_blueprint.side_effect = lambda et, es, persp, name: (
            {"guid": "aaaaaaaa-2222-2222-2222-222222222222"} if name == "a" else None
        )
        m = _materializer()
        resolved, unmet = m.resolve_child_blueprint_guids(
            registry, "repo", "myproj", "deployment", ["a", "b"],
        )
        assert resolved == {"a": "aaaaaaaa-2222-2222-2222-222222222222"}
        assert unmet == ["b"]
