"""Tests for ComponentMaterializer — the accept-verdict -> real Egeria
SolutionComponent step (docs/architecture-recovery-report-then-curate.md).

Mirrors test_egeria_publisher_sub_resources.py's mocking shape: construct
the class with a fake platform_url, replace the pyegeria client attributes
directly with MagicMocks, and short-circuit _connect() so no real network
call is ever attempted. No live Egeria in any test here.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from resource_explorer.surveyors.arch_recovery.materializer import (
    ComponentMaterializer, MaterializationError,
)

# Brief I: these tests fake the Egeria clients; the code under test runs as a signed-in caller, as on a route.
pytestmark = pytest.mark.usefixtures("signed_in_caller")


@pytest.fixture(autouse=True)
def _no_live_classification(monkeypatch):
    """Ownership/ZoneMembership go to a mock, not to a real view server.

    `materialize()` stamps both on the element it creates (2026-09-04, plan
    §4), and `stamp_published` builds its own client rather than reusing one of
    the mocks below — deliberately, because the whole point is that the client
    carries *this caller's* credential. Without this fixture the module's "no
    live Egeria in any test here" promise quietly stopped holding: the calls
    went out, failed against the real platform, and were swallowed as
    best-effort warnings.
    """
    monkeypatch.setattr(
        "resource_explorer.egeria_identity.classification_client",
        lambda identity=None: MagicMock(),
    )


def _materializer(registry=None):
    m = ComponentMaterializer(platform_url="https://fake", registry=registry)
    m._solution_architect = MagicMock()
    m._automated_curation = MagicMock()
    m._connect = MagicMock()  # short-circuit: attributes above stand in for a real connection
    m._automated_curation.get_guid_for_name.return_value = []  # nothing pre-exists by default
    m._solution_architect.create_solution_component.return_value = "11111111-1111-1111-1111-111111111111"
    return m


class TestQualifiedName:
    def test_shape_matches_the_rest_of_the_codebase(self):
        qn = ComponentMaterializer.qualified_name_for("repo", "myproj", "src/svc")
        assert qn == "SolutionComponent::repo::myproj::src/svc"


class TestFreshMaterialization:
    def test_creates_with_expected_properties(self):
        m = _materializer(registry=MagicMock(get_materialized_component=MagicMock(return_value=None)))
        result = m.materialize("repo", "myproj", "src/svc", name="svc", component_type="Software Service",
                                perspective="deployment", confidence=85)
        governance = result.pop("governance")
        assert result == {
            "status": "materialized",
            "guid": "11111111-1111-1111-1111-111111111111",
            "qualified_name": "SolutionComponent::repo::myproj::src/svc",
        }
        # Born in the draft zone, like every other element RE creates. The
        # accept path promotes it immediately after — and it must, because
        # Egeria's security connector 403s a zone change whose before and
        # after are equal, which is what writing the publish zones here
        # produced (live, 2026-09-04).
        # No default draft zone since 2026-10-08 (owner: DRAFT is a status, not a zone): NOTHING is
        # sent. The configured case is `test_a_configured_draft_zone_is_stamped` below.
        assert governance["zones"] == []
        assert governance["ownership"] is True and governance["zone_membership"] is False
        m._connect.assert_called_once()
        body = m._solution_architect.create_solution_component.call_args[0][0]
        assert body["class"] == "NewElementRequestBody"
        assert body["isOwnAnchor"] is True
        props = body["properties"]
        assert props["class"] == "SolutionComponentProperties"
        assert props["qualifiedName"] == "SolutionComponent::repo::myproj::src/svc"
        assert props["displayName"] == "svc"
        assert props["solutionComponentType"] == "Software Service"
        assert props["additionalProperties"]["perspective"] == "deployment"
        assert props["additionalProperties"]["originalConfidence"] == "85"
        assert props["additionalProperties"]["recoveredBy"] == "architecture_recovery"

    def test_content_status_draft(self):
        """architecture-recovery.md §10 Phase 2's "All at ContentStatus =
        Draft" (Backlog.md item 6, 2026-09-03) — the same fix
        BlueprintMaterializer already has, via the real mechanism
        (contentStatus on ReferenceableProperties, egeria-python's ISSUE-84
        finding), not the fictional NewSolutionElementRequestBody shape."""
        m = _materializer(registry=MagicMock(get_materialized_component=MagicMock(return_value=None)))
        m.materialize("repo", "myproj", "src/svc", name="svc")
        props = m._solution_architect.create_solution_component.call_args[0][0]["properties"]
        assert props["contentStatus"] == "DRAFT"

    def test_records_into_the_registry(self):
        registry = MagicMock(get_materialized_component=MagicMock(return_value=None))
        m = _materializer(registry=registry)
        m.materialize("repo", "myproj", "src/svc", name="svc")
        registry.record_materialized_component.assert_called_once_with(
            "repo", "myproj", "src/svc",
            "SolutionComponent::repo::myproj::src/svc",
            "11111111-1111-1111-1111-111111111111",
        )

    def test_no_solution_component_type_when_unknown(self):
        """A component the detector could not type must not invent one on
        the Egeria element — 'solutionComponentType' is simply absent
        rather than sent as an empty string."""
        m = _materializer(registry=MagicMock(get_materialized_component=MagicMock(return_value=None)))
        m.materialize("repo", "myproj", "src/svc", name="svc", component_type="")
        props = m._solution_architect.create_solution_component.call_args[0][0]["properties"]
        assert "solutionComponentType" not in props

    def test_bad_guid_from_egeria_raises(self):
        m = _materializer(registry=MagicMock(get_materialized_component=MagicMock(return_value=None)))
        m._solution_architect.create_solution_component.return_value = "not-a-guid"
        with pytest.raises(MaterializationError, match="no usable GUID"):
            m.materialize("repo", "myproj", "src/svc", name="svc")

    def test_create_call_failure_wraps_as_materialization_error(self):
        m = _materializer(registry=MagicMock(get_materialized_component=MagicMock(return_value=None)))
        m._solution_architect.create_solution_component.side_effect = RuntimeError("500 from server")
        with pytest.raises(MaterializationError, match="rejected the new SolutionComponent"):
            m.materialize("repo", "myproj", "src/svc", name="svc")


class TestIdempotency:
    def test_local_cache_hit_skips_connect_entirely(self):
        """A repeat accept (re-running the survey, or a retried request)
        must not cost a search call, let alone a create — the local table
        is checked BEFORE _connect()."""
        registry = MagicMock(get_materialized_component=MagicMock(return_value={
            "guid": "22222222-2222-2222-2222-222222222222",
            "qualified_name": "SolutionComponent::repo::myproj::src/svc",
        }))
        m = _materializer(registry=registry)
        result = m.materialize("repo", "myproj", "src/svc", name="svc")
        assert result == {
            "status": "already_materialized",
            "guid": "22222222-2222-2222-2222-222222222222",
            "qualified_name": "SolutionComponent::repo::myproj::src/svc",
        }
        m._connect.assert_not_called()
        m._solution_architect.create_solution_component.assert_not_called()

    def test_live_search_hit_records_locally_and_does_not_create(self):
        """No local record yet (a fresh registry, or a GUID from before this
        table existed), but Egeria already has the element — e.g. created
        by a previous run whose local record was lost. Search-before-create
        finds it; create is never called."""
        registry = MagicMock(get_materialized_component=MagicMock(return_value=None))
        m = _materializer(registry=registry)
        m._automated_curation.get_guid_for_name.return_value = [
            "33333333-3333-3333-3333-333333333333"
        ]
        result = m.materialize("repo", "myproj", "src/svc", name="svc")
        assert result["status"] == "already_materialized"
        assert result["guid"] == "33333333-3333-3333-3333-333333333333"
        m._solution_architect.create_solution_component.assert_not_called()
        registry.record_materialized_component.assert_called_once_with(
            "repo", "myproj", "src/svc",
            "SolutionComponent::repo::myproj::src/svc",
            "33333333-3333-3333-3333-333333333333",
        )


class TestConnectionFailure:
    def test_no_platform_url_raises_before_any_pyegeria_call(self):
        # The constructor always falls back to a non-empty default
        # (os.getenv(..., _DEFAULT_PLATFORM_URL)), so an empty platform_url
        # can only be observed by setting it directly post-construction —
        # this mirrors what _connect()'s own guard actually checks.
        m = ComponentMaterializer(registry=MagicMock(
            get_materialized_component=MagicMock(return_value=None)))
        m.platform_url = ""
        with pytest.raises(MaterializationError, match="EGERIA_PLATFORM_URL"):
            m.materialize("repo", "myproj", "src/svc", name="svc")
