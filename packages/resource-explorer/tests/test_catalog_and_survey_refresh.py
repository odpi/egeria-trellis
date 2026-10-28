"""Tests for the "catalog_and_survey never refreshes an existing element's
connection" fix — docs/Backlog.md "TIER 1 — catalog_and_survey never refreshes
an existing element's credentials/connection" and
CATALOG-AND-SURVEY-REFRESH-FIX.md.

Root cause (confirmed live against qs-view-server, see the design note):
pyegeria's AutomatedCuration.create_postgres_server_element_from_template and
create_postgres_database_element_from_template build a TemplateRequestBody by
hand and never set "deepCopy": True, so Egeria's templated cataloguing never
instantiates the template's attached Connection subgraph — a fresh catalog run
can end up with an asset that has no Connection at all. These tests pin the
fix (EgeriaDatabaseSurveyor._create_postgres_element_from_template bypasses
the two broken wrappers and adds deepCopy=True) and the accompanying
visibility check for the case this fix does NOT repair: an element that
already exists by qualifiedName.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from resource_explorer.registry import DatabaseEntity
from resource_explorer.surveyors.database.egeria_database_surveyor import (
    EgeriaDatabaseSurveyor,
)


@pytest.fixture
def db_entity():
    return DatabaseEntity(
        slug="coco_ods",
        display_name="coco_ods",
        db_type="postgresql",
        host="localhost",
        port=5442,
        database_name="coco_ods",
    )


@pytest.fixture
def surveyor():
    s = EgeriaDatabaseSurveyor(
        platform_url="https://localhost:9443",
        view_server="qs-view-server",
        user_id="erinoverview",
        user_password="secret",
    )
    # Bypass connect() — inject mocks directly, as other tests in this suite do.
    s._automated_curation = MagicMock()
    # A real miss is pyegeria's "No elements found"; a bare MagicMock answer is not one (round 3: odd answers are
    # could-not-check, never "create").
    s._automated_curation.get_guid_for_name.return_value = "No elements found"
    s._asset_maker = MagicMock()
    s._discovery = MagicMock()
    # S19: no archived copy of the database, so a create is a plain create (a real 6.2 miss).
    from pyegeria import MetadataExpert
    from unittest.mock import create_autospec
    s._metadata_expert = create_autospec(MetadataExpert, spec_set=True, instance=True)
    s._metadata_expert.find_metadata_elements_with_string.return_value = "No elements found"
    return s


class TestCreatePostgresElementFromTemplate:
    """Pins the deepCopy fix itself: the request body sent to Egeria's
    template-instantiation endpoint must include "deepCopy": True, and the
    call must bypass the two known-broken pyegeria convenience wrappers
    entirely (they must never be called)."""

    def test_sets_deep_copy_true(self, surveyor):
        surveyor._automated_curation.get_template_guid_for_technology_type.return_value = "template-guid-1"
        surveyor._automated_curation.create_elem_from_template.return_value = "new-guid-1"

        result = surveyor._create_postgres_element_from_template(
            "PostgreSQL Relational Database",
            {"databaseName": "coco_ods", "serverName": "localhost:5442"},
        )

        assert result == "new-guid-1"
        surveyor._automated_curation.create_elem_from_template.assert_called_once()
        (body,), _ = surveyor._automated_curation.create_elem_from_template.call_args
        assert body["deepCopy"] is True
        assert body["templateGUID"] == "template-guid-1"
        assert body["placeholderPropertyValues"] == {
            "databaseName": "coco_ods", "serverName": "localhost:5442",
        }

    def test_never_calls_the_broken_wrappers(self, surveyor):
        """create_postgres_server_element_from_template/create_postgres_
        database_element_from_template never set deepCopy — calling this
        helper must not go anywhere near them."""
        surveyor._automated_curation.get_template_guid_for_technology_type.return_value = "t"
        surveyor._automated_curation.create_elem_from_template.return_value = "g"

        surveyor._create_postgres_element_from_template("PostgreSQL Server", {"serverName": "x"})

        surveyor._automated_curation.create_postgres_server_element_from_template.assert_not_called()
        surveyor._automated_curation.create_postgres_database_element_from_template.assert_not_called()


class TestCatalogAndSurveyFreshCatalog:
    """The case this fix actually repairs: nothing found by name yet."""

    def test_fresh_catalog_uses_deep_copy_path_for_server_and_database(self, surveyor, db_entity):
        surveyor._find_element_guid = MagicMock(return_value="")  # nothing exists yet
        surveyor._automated_curation.get_template_guid_for_technology_type.return_value = "template-guid"
        surveyor._automated_curation.create_elem_from_template.side_effect = [
            "server-guid-new", "db-guid-new",
        ]
        surveyor._initiate_survey = MagicMock(return_value="survey-guid")

        result = surveyor._catalog_and_survey(db_entity, db_user="postgres", db_pwd="egeria")

        assert result["server_guid"] == "server-guid-new"
        assert result["database_guid"] == "db-guid-new"
        assert surveyor._automated_curation.create_elem_from_template.call_count == 2
        for (body,), _ in surveyor._automated_curation.create_elem_from_template.call_args_list:
            assert body["deepCopy"] is True

    def test_fresh_catalog_passes_current_credentials_through(self, surveyor, db_entity):
        surveyor._find_element_guid = MagicMock(return_value="")
        surveyor._automated_curation.get_template_guid_for_technology_type.return_value = "template-guid"
        surveyor._automated_curation.create_elem_from_template.side_effect = [
            "server-guid-new", "db-guid-new",
        ]
        surveyor._initiate_survey = MagicMock(return_value="survey-guid")

        surveyor._catalog_and_survey(db_entity, db_user="postgres", db_pwd="corrected-pw")

        calls = surveyor._automated_curation.create_elem_from_template.call_args_list
        server_body = calls[0][0][0]
        db_body = calls[1][0][0]
        assert server_body["placeholderPropertyValues"]["databaseUserId"] == "postgres"
        assert server_body["placeholderPropertyValues"]["databasePassword"] == "corrected-pw"
        assert db_body["placeholderPropertyValues"]["databaseUserId"] == "postgres"
        assert db_body["placeholderPropertyValues"]["databasePassword"] == "corrected-pw"


def _by_qn(connection_guid):
    """Existing elements, found by their FULL qualifiedName (Brief L, 7f.7) — never a bare name."""
    found = {
        "PostgreSQL Server::localhost:5442": "existing-server-guid",
        "PostgreSQL Relational Database::localhost:5442::coco_ods": "existing-db-guid",
        "PostgreSQL Relational Database::localhost:5442::coco_ods::Connection": connection_guid,
    }
    return MagicMock(side_effect=lambda qn: found.get(qn, ""))


class TestCatalogAndSurveyExistingElement:
    """The case this fix does NOT repair: an element already exists by name.
    Confirmed live that Egeria's by-qualifiedName reuse path never re-runs
    deepCopy's child-copying, so calling create_elem_from_template again would
    not fix a missing connection — the code must not pretend otherwise, and
    must not call the (network) create path again once something is found."""

    def test_does_not_recreate_when_already_found_by_name(self, surveyor, db_entity):
        surveyor._find_by_qualified_name = _by_qn("")   # connection-presence check finds none
        surveyor._initiate_survey = MagicMock(return_value="survey-guid")

        result = surveyor._catalog_and_survey(db_entity, db_user="postgres", db_pwd="corrected-pw")

        assert result["server_guid"] == "existing-server-guid"
        assert result["database_guid"] == "existing-db-guid"
        surveyor._automated_curation.create_elem_from_template.assert_not_called()

    def test_warns_when_existing_element_has_no_connection(self, surveyor, db_entity, caplog):
        surveyor._find_by_qualified_name = _by_qn("")   # connection-presence check: none found
        surveyor._initiate_survey = MagicMock(return_value="survey-guid")

        with caplog.at_level("WARNING"):
            surveyor._catalog_and_survey(db_entity, db_user="postgres", db_pwd="corrected-pw")

        assert any("has no Connection attached" in r.message for r in caplog.records)
        assert any("delete-and-recatalog" in r.message for r in caplog.records)

    def test_no_warning_when_existing_element_has_a_connection(self, surveyor, db_entity, caplog):
        surveyor._find_by_qualified_name = _by_qn("connection-guid-present")   # connection found
        surveyor._initiate_survey = MagicMock(return_value="survey-guid")

        with caplog.at_level("WARNING"):
            surveyor._catalog_and_survey(db_entity, db_user="postgres", db_pwd="corrected-pw")

        assert not any("has no Connection attached" in r.message for r in caplog.records)
