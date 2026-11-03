"""Tests for EgeriaDatabaseSurveyor's own secrets-store wiring.

PROBES-2026-09-21.md found that a freshly-cataloged
database's native PostgreSQL survey failed with a SCRAM/no-password error
because the "secretsCollectionName"/"secretsStorePathName" placeholders on
its templated Connection were never bound -- left as Egeria's own literal,
unsubstituted template text. These tests cover the fix: RE now finds-or-
creates its own SecretsStore Connection and writes each database's
credentials into it as a named collection before cataloging.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch
from tests.qn_lookup_bridge import expert_from_legacy

from resource_explorer.surveyors.database.egeria_database_surveyor import (
    EgeriaDatabaseSurveyor,
    _OWN_SECRETS_STORE_QUALIFIED_NAME,
    _YAML_SECRETS_FILE_PROVIDER_CLASS,
    _build_secrets_collection_body,
    _secrets_collection_name,
)

# Brief I: these tests fake the Egeria clients; the code under test runs as a signed-in caller, as on a route.
import pytest  # noqa: E402
pytestmark = pytest.mark.usefixtures("signed_in_caller")


class TestSecretsCollectionNaming:
    def test_collection_name_is_keyed_by_slug(self):
        # Two different databases must never collide on the same collection --
        # a shared file holds every database's credentials side by side.
        assert _secrets_collection_name("coco_pharma") != _secrets_collection_name("coco_ods")

    def test_collection_name_is_deterministic(self):
        # _save_database_secret must find/write the SAME collection on every
        # catalog run for one database, not create a new one each time.
        assert _secrets_collection_name("coco_pharma") == _secrets_collection_name("coco_pharma")


class TestSecretsCollectionBody:
    def test_body_uses_egeria_key_names(self):
        # "userId"/"clearPassword" are the YAML secrets store connector's own
        # convention (confirmed live 2026-07-09) -- not RE's choice, so a
        # rename here would silently break every native survey again.
        body = _build_secrets_collection_body("coco_pharma::PostgreSQL Secret", "erin", "hunter2")
        secrets = body["secretsCollection"]["secrets"]
        assert secrets == {"userId": "erin", "clearPassword": "hunter2"}

    def test_body_class_is_secrets_collection_request_body(self):
        body = _build_secrets_collection_body("x", "u", "p")
        assert body["class"] == "SecretsCollectionRequestBody"
        assert body["secretsCollection"]["collectionName"] == "x"


class TestEnsureOwnSecretsStoreGuid:
    def _surveyor(self):
        surveyor = EgeriaDatabaseSurveyor(
            platform_url="https://localhost:9443", view_server="qs-view-server",
            user_id="erinoverview", user_password="secret",
        )
        surveyor._automated_curation = MagicMock()
        surveyor._metadata_expert = expert_from_legacy(surveyor._automated_curation)
        return surveyor

    def test_explicit_config_guid_wins_and_short_circuits_lookup(self):
        surveyor = self._surveyor()
        with patch("resource_explorer.config.get_config") as mock_get_config:
            mock_get_config.return_value.egeria.secrets_store_guid = "existing-guid-123"
            guid = surveyor._ensure_own_secrets_store_guid()
        assert guid == "existing-guid-123"
        surveyor._metadata_expert.get_metadata_element_by_unique_name.assert_not_called()

    def test_finds_existing_element_by_qualified_name_before_creating(self):
        surveyor = self._surveyor()
        surveyor._automated_curation.get_guid_for_name.return_value = (
            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        )
        with patch("resource_explorer.config.get_config") as mock_get_config:
            mock_get_config.return_value.egeria.secrets_store_guid = ""
            with patch("pyegeria.ConnectionMaker") as mock_maker_cls:
                guid = surveyor._ensure_own_secrets_store_guid()
        assert guid == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        mock_maker_cls.assert_not_called()
        # Looks up the ASSET's qualifiedName, not the Connection's -- the
        # guid this method returns must be an Asset's (see its docstring:
        # save_client_side_secret resolves a connector from an Asset).
        surveyor._metadata_expert.get_metadata_element_by_unique_name.assert_called_once_with(
            f"{_OWN_SECRETS_STORE_QUALIFIED_NAME}::Asset", property_name="qualifiedName"
        )

    def test_creates_connection_endpoint_connector_type_asset_graph_when_absent(self):
        surveyor = self._surveyor()
        # A real miss is pyegeria's "No elements found" answer. A RAISED look-up is "could not check", which
        # creates nothing (tests/test_lookup_failure_never_creates.py); this test used to fake the miss as one.
        surveyor._automated_curation.get_guid_for_name.return_value = "No elements found"
        mock_maker = MagicMock()
        mock_maker.create_connector_type.return_value = "connector-type-guid"
        mock_maker.create_endpoint.return_value = "endpoint-guid"
        mock_maker.create_connection.return_value = "connection-guid"
        mock_maker.create_asset.return_value = "asset-guid"
        with patch("resource_explorer.config.get_config") as mock_get_config:
            mock_get_config.return_value.egeria.secrets_store_guid = ""
            mock_get_config.return_value.egeria.secrets_store_path_name = (
                "/deployments/secrets/resource-explorer.omsecrets"
            )
            with patch("pyegeria.ConnectionMaker", return_value=mock_maker):
                guid = surveyor._ensure_own_secrets_store_guid()

        # The returned guid is the ASSET's, not the Connection's -- confirmed
        # live 2026-09-21 that save_client_side_secret needs an Asset guid.
        assert guid == "asset-guid"
        connector_body = mock_maker.create_connector_type.call_args[0][0]
        assert (
            connector_body["properties"]["connectorProviderClassName"]
            == _YAML_SECRETS_FILE_PROVIDER_CLASS
        )
        endpoint_body = mock_maker.create_endpoint.call_args[0][0]
        assert (
            endpoint_body["properties"]["networkAddress"]
            == "/deployments/secrets/resource-explorer.omsecrets"
        )
        # The connection itself needs a non-null secretsCollectionName
        # configuration property or the OCF secrets-store connector
        # framework's own start() refuses to initialize at all -- confirmed
        # live 2026-09-21 ("OCF-CONNECTOR-400-009 ... secretsCollectionName
        # was not supplied"), even though YAMLSecretsFileConnector's own
        # start() immediately nulls it back out and every real save call
        # supplies its own collection name as an argument.
        connection_body = mock_maker.create_connection.call_args[0][0]
        assert connection_body["properties"]["configurationProperties"] == {
            "secretsCollectionName": "resource-explorer-admin"
        }
        # Every relationship link must pass an explicit body -- confirmed
        # live that ConnectionMaker's link_* calls silently create no
        # relationship at all when body defaults to None.
        relationship_body = {"class": "NewRelationshipRequestBody"}
        mock_maker.link_connection_connector_type.assert_called_once_with(
            "connection-guid", "connector-type-guid", body=relationship_body
        )
        mock_maker.link_connection_endpoint.assert_called_once_with(
            "connection-guid", "endpoint-guid", body=relationship_body
        )
        mock_maker.link_asset_to_connection.assert_called_once_with(
            "asset-guid", "connection-guid", body=relationship_body
        )

    def test_caches_guid_across_calls(self):
        surveyor = self._surveyor()
        surveyor._secrets_store_guid = "cached-guid"
        with patch("pyegeria.ConnectionMaker") as mock_maker_cls:
            guid = surveyor._ensure_own_secrets_store_guid()
        assert guid == "cached-guid"
        mock_maker_cls.assert_not_called()
        surveyor._metadata_expert.get_metadata_element_by_unique_name.assert_not_called()

    def test_reuses_a_partially_existing_graph_instead_of_409ing(self):
        """Live-verified 2026-09-21, after an Egeria wipe-and-redeploy: this
        method's own ConnectorType was found to already exist while the
        Endpoint/Connection/Asset did not. Gating creation on the Asset alone
        made create_connector_type blow up with a 409 duplicate-qualifiedName
        instead of finding and reusing what was already there."""
        surveyor = self._surveyor()
        mock_maker = MagicMock()
        mock_maker.create_endpoint.return_value = "fresh-endpoint-guid"
        mock_maker.create_connection.return_value = "fresh-connection-guid"
        mock_maker.create_asset.return_value = "fresh-asset-guid"

        def fake_find(qn: str) -> str:
            if qn == f"{_OWN_SECRETS_STORE_QUALIFIED_NAME}::ConnectorType":
                return "preexisting-connector-type-guid"
            return ""

        with patch("resource_explorer.config.get_config") as mock_get_config:
            mock_get_config.return_value.egeria.secrets_store_guid = ""
            mock_get_config.return_value.egeria.secrets_store_path_name = (
                "/deployments/secrets/resource-explorer.omsecrets"
            )
            with patch("pyegeria.ConnectionMaker", return_value=mock_maker):
                with patch.object(surveyor, "_find_element_guid", side_effect=fake_find):
                    guid = surveyor._ensure_own_secrets_store_guid()

        assert guid == "fresh-asset-guid"
        # The pre-existing ConnectorType must be reused, not recreated.
        mock_maker.create_connector_type.assert_not_called()
        # Everything else, genuinely absent, must still be created.
        mock_maker.create_endpoint.assert_called_once()
        mock_maker.create_connection.assert_called_once()
        mock_maker.create_asset.assert_called_once()
        # And the found ConnectorType must actually get linked to the fresh
        # Connection -- reusing a sub-element is only safe if it still ends
        # up wired into the graph.
        relationship_body = {"class": "NewRelationshipRequestBody"}
        mock_maker.link_connection_connector_type.assert_called_once_with(
            "fresh-connection-guid", "preexisting-connector-type-guid", body=relationship_body
        )

    def test_relinks_a_found_connection_even_when_not_freshly_created(self):
        """Live-verified 2026-09-21: a found-but-reused Connection can be a
        partial leftover that was never linked to its ConnectorType/Endpoint
        at all (OCF-CONNECTION-400-003 'Null connectorType property passed in
        connection'). Gating the link_* calls on 'just created' reproduces
        the exact 'reuse never repairs' defect this whole method exists to
        avoid -- the link calls must run whether or not this call created the
        Connection."""
        surveyor = self._surveyor()
        mock_maker = MagicMock()
        mock_maker.create_asset.return_value = "fresh-asset-guid"

        def fake_find(qn: str) -> str:
            return {
                f"{_OWN_SECRETS_STORE_QUALIFIED_NAME}::ConnectorType": "existing-connector-type-guid",
                f"{_OWN_SECRETS_STORE_QUALIFIED_NAME}::Endpoint": "existing-endpoint-guid",
                _OWN_SECRETS_STORE_QUALIFIED_NAME: "existing-connection-guid",
            }.get(qn, "")

        with patch("resource_explorer.config.get_config") as mock_get_config:
            mock_get_config.return_value.egeria.secrets_store_guid = ""
            with patch("pyegeria.ConnectionMaker", return_value=mock_maker):
                with patch.object(surveyor, "_find_element_guid", side_effect=fake_find):
                    surveyor._ensure_own_secrets_store_guid()

        mock_maker.create_connector_type.assert_not_called()
        mock_maker.create_endpoint.assert_not_called()
        mock_maker.create_connection.assert_not_called()
        relationship_body = {"class": "NewRelationshipRequestBody"}
        mock_maker.link_connection_connector_type.assert_called_once_with(
            "existing-connection-guid", "existing-connector-type-guid", body=relationship_body
        )
        mock_maker.link_connection_endpoint.assert_called_once_with(
            "existing-connection-guid", "existing-endpoint-guid", body=relationship_body
        )


class TestSaveDatabaseSecret:
    def _surveyor(self):
        surveyor = EgeriaDatabaseSurveyor(
            platform_url="https://localhost:9443", view_server="qs-view-server",
            user_id="erinoverview", user_password="secret",
        )
        surveyor._automated_curation = MagicMock()
        surveyor._metadata_expert = expert_from_legacy(surveyor._automated_curation)
        return surveyor

    def test_returns_collection_name_and_path_on_success(self):
        surveyor = self._surveyor()
        surveyor._secrets_store_guid = "store-guid"
        with patch("resource_explorer.config.get_config") as mock_get_config:
            mock_get_config.return_value.egeria.secrets_store_path_name = "/deployments/secrets/resource-explorer.omsecrets"
            collection_name, path = surveyor._save_database_secret("coco_pharma", "u", "p")
        assert collection_name == _secrets_collection_name("coco_pharma")
        assert path == "/deployments/secrets/resource-explorer.omsecrets"
        surveyor._automated_curation.save_client_side_secret.assert_called_once()
        call_args = surveyor._automated_curation.save_client_side_secret.call_args[0]
        assert call_args[0] == "store-guid"
        assert call_args[1]["secretsCollection"]["secrets"]["userId"] == "u"

    def test_failure_is_non_fatal_and_returns_empty_strings(self):
        # A secrets-store failure must not block cataloging -- the database
        # can still be created and locally scanned, just without a working
        # native survey (the same degraded-but-not-broken outcome as before
        # this fix existed).
        surveyor = self._surveyor()
        surveyor._automated_curation.save_client_side_secret.side_effect = Exception("boom")
        surveyor._secrets_store_guid = "store-guid"
        collection_name, path = surveyor._save_database_secret("coco_pharma", "u", "p")
        assert (collection_name, path) == ("", "")
