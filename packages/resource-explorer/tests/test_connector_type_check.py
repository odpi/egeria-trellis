"""Start-up ConnectorType check: reads only, names a mismatch, never says ok when it could not look."""
from unittest.mock import create_autospec

import pytest

pyegeria = pytest.importorskip("pyegeria")

from resource_explorer.connector_type_check import check_connector_type, connector_type_line  # noqa: E402
from resource_explorer.surveyors.database.egeria_database_surveyor import (  # noqa: E402
    _OWN_SECRETS_STORE_QUALIFIED_NAME,
    _YAML_SECRETS_FILE_PROVIDER_CLASS,
)

QN = f"{_OWN_SECRETS_STORE_QUALIFIED_NAME}::ConnectorType"
WRITES = ("create_connector_type", "update_connector_type", "delete_connector_type",
          "create_connector_type_from_template", "link_connection_connector_type",
          "detach_connection_connector_type")


def _maker(answer=None, raises=None):
    from pyegeria import ConnectionMaker

    maker = create_autospec(ConnectionMaker, instance=True)
    for name in WRITES:
        getattr(maker, name).side_effect = AssertionError(f"{name} must never be called")
    if raises is not None:
        maker.get_connector_types_by_name.side_effect = raises
    else:
        maker.get_connector_types_by_name.return_value = answer
    return maker


def _element(provider):
    props = {"qualifiedName": QN}
    if provider is not None:
        props["connectorProviderClassName"] = provider
    return [{"elementHeader": {"guid": "g1"}, "properties": props}]


def test_matching_class_logs_ok():
    state = check_connector_type(_maker(_element(_YAML_SECRETS_FILE_PROVIDER_CLASS)))
    assert state["status"] == "ok"
    assert connector_type_line(state) == "connector type: ok"


def test_mismatch_is_named_and_nothing_is_written():
    stale = "org.odpi.openmetadata.adapters.connectors.secretsstore.yaml.YAMLSecretsStoreProvider"
    maker = _maker(_element(stale))
    state = check_connector_type(maker)
    line = connector_type_line(state)
    assert state["status"] == "mismatch"
    assert stale in line and _YAML_SECRETS_FILE_PROVIDER_CLASS in line and "not repaired" in line
    assert "connector type: ok" not in line
    for name in WRITES:
        getattr(maker, name).assert_not_called()


def test_lookup_that_cannot_be_made_is_could_not_check_not_ok():
    state = check_connector_type(_maker(raises=ConnectionError("platform unreachable")))
    line = connector_type_line(state)
    assert state["status"] == "could not check"
    assert line.startswith("connector type: could not check") and "unreachable" in line
    assert "ok" not in line.replace("could not check", "")


def test_unreadable_element_is_could_not_check():
    state = check_connector_type(_maker(_element(None)))
    assert state["status"] == "could not check"
