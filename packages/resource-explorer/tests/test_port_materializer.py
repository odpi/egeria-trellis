"""Tests for PortMaterializer — the TEMPORARY workaround for Egeria's
missing create_solution_port (Backlog.md item 8 / egeria-python
ISSUE-85), confirmed live: no create endpoint anywhere in the actual
OpenAPI spec, not just a client-side pyegeria gap.

Mirrors test_blueprint_materializer.py's mocking shape: construct the class
with a fake platform_url, replace the pyegeria client attributes directly
with MagicMocks, and short-circuit _connect() so no real network call is
ever attempted. No live Egeria in any test here.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from resource_explorer.surveyors.arch_recovery.port_materializer import (
    PortMaterializationError, PortMaterializer, _direction_symbolic_name,
)

# Brief I: a blueprint or port is written by a queued publish: RE's daemon on the requester's behalf.
pytestmark = pytest.mark.usefixtures("as_daemon")


def _materializer(registry=None):
    m = PortMaterializer(platform_url="https://fake", registry=registry)
    m._metadata_expert = MagicMock()
    m._solution_architect = MagicMock()
    m._connect = MagicMock()  # short-circuit: attributes above stand in for a real connection
    m._metadata_expert.get_metadata_guid_by_unique_name.return_value = []  # nothing pre-exists by default
    m._metadata_expert.create_metadata_element.return_value = "11111111-1111-1111-1111-111111111111"
    return m


class TestQualifiedName:
    def test_shape_includes_scope_and_port_name(self):
        qn = PortMaterializer.qualified_name_for("repo", "myproj", "src/web", "http")
        assert qn == "SolutionPort::repo::myproj::src/web::http"


class TestDirectionMapping:
    """The real registered enum is SolutionPortDirection, confirmed live —
    a first attempt used the wrong, unrelated classic Port.portType enum
    and failed OMAG-COMMON-400-032. These pin the corrected mapping."""

    @pytest.mark.parametrize("direction,expected", [
        ("input", "INPUT"),
        ("Input", "INPUT"),
        ("output", "OUTPUT"),
        ("input-output", "INOUT"),
        ("output-input", "OUTIN"),
        ("", "UNKNOWN"),
        ("something-unrecognized", "OTHER"),
    ])
    def test_maps_re_vocabulary_to_the_real_enum(self, direction, expected):
        assert _direction_symbolic_name(direction) == expected


class TestFreshMaterialization:
    def test_creates_with_expected_body_shape(self):
        m = _materializer(registry=MagicMock(get_materialized_port=MagicMock(return_value=None)))
        result = m.materialize_port_element(
            "repo", "myproj", "src/web", "http",
            component_guid="comp-guid-1", direction="input",
        )
        assert result == {
            "status": "materialized",
            "guid": "11111111-1111-1111-1111-111111111111",
            "qualified_name": "SolutionPort::repo::myproj::src/web::http",
        }
        m._connect.assert_called_once()
        body = m._metadata_expert.create_metadata_element.call_args[0][0]
        assert body["class"] == "NewOpenMetadataElementRequestBody"
        assert body["typeName"] == "SolutionPort"
        assert body["isOwnAnchor"] is True
        props = body["properties"]
        assert props["class"] == "NewElementProperties"
        pvm = props["propertyValueMap"]
        assert pvm["qualifiedName"] == {
            "class": "PrimitiveTypePropertyValue", "typeName": "string",
            "primitiveTypeCategory": "OM_PRIMITIVE_TYPE_STRING",
            "primitiveValue": "SolutionPort::repo::myproj::src/web::http",
        }
        assert pvm["displayName"]["primitiveValue"] == "http"
        assert pvm["direction"] == {
            "class": "EnumTypePropertyValue", "typeName": "SolutionPortDirection",
            "symbolicName": "INPUT",
        }

    def test_attaches_to_the_owning_component(self):
        m = _materializer(registry=MagicMock(get_materialized_port=MagicMock(return_value=None)))
        m.materialize_port_element(
            "repo", "myproj", "src/web", "http", component_guid="comp-guid-1",
        )
        m._solution_architect.link_solution_component_port.assert_called_once_with(
            "comp-guid-1", "11111111-1111-1111-1111-111111111111", None,
        )

    def test_records_into_the_registry(self):
        registry = MagicMock(get_materialized_port=MagicMock(return_value=None))
        m = _materializer(registry=registry)
        m.materialize_port_element("repo", "myproj", "src/web", "http", component_guid="comp-guid-1")
        registry.record_materialized_port.assert_called_once_with(
            "repo", "myproj", "src/web", "http",
            "SolutionPort::repo::myproj::src/web::http",
            "11111111-1111-1111-1111-111111111111",
        )

    def test_no_usable_guid_raises(self):
        m = _materializer(registry=MagicMock(get_materialized_port=MagicMock(return_value=None)))
        m._metadata_expert.create_metadata_element.return_value = ""
        with pytest.raises(PortMaterializationError, match="no usable GUID"):
            m.materialize_port_element("repo", "myproj", "src/web", "http", component_guid="comp-guid-1")

    def test_attach_failure_raises_and_is_distinguishable(self):
        """A port that materialized but couldn't attach is not a useful
        proposal — this must surface as a real error, not a partial
        success (unlike blueprint member attachment, there's no fallback
        state for a single unattached port)."""
        m = _materializer(registry=MagicMock(get_materialized_port=MagicMock(return_value=None)))
        m._solution_architect.link_solution_component_port.side_effect = RuntimeError("boom")
        with pytest.raises(PortMaterializationError, match="could not be attached"):
            m.materialize_port_element("repo", "myproj", "src/web", "http", component_guid="comp-guid-1")


class TestIdempotency:
    def test_cached_guid_short_circuits_before_connect(self):
        registry = MagicMock(get_materialized_port=MagicMock(return_value={
            "guid": "cached-guid", "qualified_name": "SolutionPort::repo::myproj::src/web::http",
        }))
        m = _materializer(registry=registry)
        result = m.materialize_port_element("repo", "myproj", "src/web", "http", component_guid="comp-guid-1")
        assert result == {"status": "already_materialized", "guid": "cached-guid",
                          "qualified_name": "SolutionPort::repo::myproj::src/web::http"}
        m._connect.assert_not_called()
        m._metadata_expert.create_metadata_element.assert_not_called()

    def test_qualified_name_search_finds_existing_element_before_creating(self):
        m = _materializer(registry=MagicMock(get_materialized_port=MagicMock(return_value=None)))
        m._metadata_expert.get_metadata_guid_by_unique_name.return_value = "22222222-2222-2222-2222-222222222222"
        result = m.materialize_port_element("repo", "myproj", "src/web", "http", component_guid="comp-guid-1")
        assert result["status"] == "already_materialized"
        assert result["guid"] == "22222222-2222-2222-2222-222222222222"
        m._metadata_expert.create_metadata_element.assert_not_called()

    def test_search_found_element_still_gets_attached(self):
        """Idempotency covers the port's own creation, not the attach —
        re-accepting a port whose component changed (or was never attached
        the first time) must still attach it."""
        m = _materializer(registry=MagicMock(get_materialized_port=MagicMock(return_value=None)))
        m._metadata_expert.get_metadata_guid_by_unique_name.return_value = "22222222-2222-2222-2222-222222222222"
        m.materialize_port_element("repo", "myproj", "src/web", "http", component_guid="comp-guid-1")
        m._solution_architect.link_solution_component_port.assert_called_once_with(
            "comp-guid-1", "22222222-2222-2222-2222-222222222222", None,
        )


class TestBodyActuallyValidatesAgainstInstalledPyegeria:
    """Not a mock-shape assertion — runs the exact body this method builds
    through pyegeria's real NewOpenMetadataElementRequestBody model, the
    validator the real create call goes through internally. The kind of
    test that would have caught the wrong-enum mistake before it reached a
    live create."""

    def test_body_validates(self):
        from pydantic import TypeAdapter

        from pyegeria.models.models import NewOpenMetadataElementRequestBody

        m = _materializer(registry=MagicMock(get_materialized_port=MagicMock(return_value=None)))
        m.materialize_port_element("repo", "myproj", "src/web", "http", component_guid="comp-guid-1")
        body = m._metadata_expert.create_metadata_element.call_args[0][0]
        TypeAdapter(NewOpenMetadataElementRequestBody).validate_python(body)  # raises on failure
