"""Brief L §2 (backlog 7f.7): an existing Egeria database or server is adopted by its FULL
qualifiedName, never by a bare name.

Host A's `sales` is already in Egeria. Registering host B's `sales` must create host B's own
server and database elements, not adopt host A's. The fake below answers `get_guid_for_name` the
way Egeria does: an exact match on ANY of the properties asked for (pyegeria's default searches
qualifiedName, displayName, resourceName and identifier), so a bare-name search for `sales` finds
host A's database through its displayName. Nothing here contacts Egeria.
"""
from __future__ import annotations

from unittest.mock import MagicMock
from tests.qn_lookup_bridge import expert_from_legacy

import pytest

from resource_explorer.registry import DatabaseEntity
from resource_explorer.surveyors.database.egeria_database_surveyor import (
    EgeriaDatabaseSurveyor, EgeriaDatabaseSurveyorError,
)

A_SERVER = "11111111-1111-1111-1111-111111111111"
A_DB = "22222222-2222-2222-2222-222222222222"
B_SERVER = "33333333-3333-3333-3333-333333333333"
B_DB = "44444444-4444-4444-4444-444444444444"
DEFAULT_PROPS = ["qualifiedName", "displayName", "resourceName", "identifier"]


class FakeEgeria:
    """Elements as Egeria holds them, and an exact-match `get_guid_for_name`."""

    def __init__(self):
        self.elements = [
            {"guid": A_SERVER, "qualifiedName": "PostgreSQL Server::host-a:5432",
             "displayName": "host-a:5432", "resourceName": "host-a:5432"},
            {"guid": A_DB, "qualifiedName": "PostgreSQL Relational Database::host-a:5432::sales",
             "displayName": "sales", "resourceName": "sales"},
        ]
        self.lookups: list[tuple[str, tuple]] = []

    def get_guid_for_name(self, name, property_name=DEFAULT_PROPS, type_name=None):
        self.lookups.append((name, tuple(property_name)))
        hits = [e["guid"] for e in self.elements if any(e.get(p) == name for p in property_name)]
        if len(hits) > 1:
            raise RuntimeError("Multiple elements found for supplied name!")
        return hits[0] if hits else "No elements found"


@pytest.fixture
def egeria():
    return FakeEgeria()


@pytest.fixture
def surveyor(egeria):
    s = EgeriaDatabaseSurveyor(platform_url="https://localhost:9443", view_server="qs-view-server",
                               user_id="u", user_password="p")
    curation = MagicMock()
    curation.get_guid_for_name.side_effect = egeria.get_guid_for_name
    curation.get_template_guid_for_technology_type.return_value = "template-guid"
    curation.create_elem_from_template.side_effect = [B_SERVER, B_DB]
    s._automated_curation = curation
    s._asset_maker = MagicMock()
    s._discovery = MagicMock()
    from unittest.mock import create_autospec
    from pyegeria import MetadataExpert
    s._metadata_expert = create_autospec(MetadataExpert, spec_set=True, instance=True)
    expert_from_legacy(s._automated_curation, s._metadata_expert)
    s._metadata_expert.find_metadata_elements_with_string.return_value = "No elements found"   # S19: no archived copy
    s.connect = lambda: None
    s._save_database_secret = lambda *a: ("", "")
    return s


def _host_b_sales():
    return DatabaseEntity(slug="host_b_sales", display_name="sales", db_type="postgresql",
                          host="host-b", port=5432, database_name="sales")


def test_registering_host_b_sales_does_not_adopt_host_a_sales(surveyor, egeria):
    result = surveyor._catalog_and_survey(_host_b_sales(), db_user="u", db_pwd="p",
                                          survey_after_catalog=False)

    assert result["database_guid"] == B_DB, "host B's sales is its own element, never host A's"
    assert result["server_guid"] == B_SERVER
    assert surveyor._automated_curation.create_elem_from_template.call_count == 2
    # Every adoption lookup was by the full qualifiedName, on the qualifiedName property only.
    adoption = [(n, p) for n, p in egeria.lookups if not n.endswith("::Connection")]
    assert ("PostgreSQL Relational Database::host-b:5432::sales", ("qualifiedName",)) in adoption
    assert ("PostgreSQL Server::host-b:5432", ("qualifiedName",)) in adoption
    assert all(p == ("qualifiedName",) for _n, p in adoption)
    assert not any(n == "sales" for n, _p in egeria.lookups), "never a bare-name lookup"


def test_host_a_sales_is_still_adopted_on_host_a(surveyor, egeria):
    host_a = DatabaseEntity(slug="host_a_sales", display_name="sales", db_type="postgresql",
                            host="host-a", port=5432, database_name="sales")
    result = surveyor._catalog_and_survey(host_a, db_user="u", db_pwd="p", survey_after_catalog=False)

    assert (result["server_guid"], result["database_guid"]) == (A_SERVER, A_DB)
    surveyor._automated_curation.create_elem_from_template.assert_not_called()


def test_publishing_step_results_for_host_b_does_not_attach_to_host_a_sales(surveyor):
    with pytest.raises(EgeriaDatabaseSurveyorError, match="not yet published to Egeria"):
        surveyor.publish_step_annotations(_host_b_sales(), schema_info={}, statistics=None,
                                          surveyed_at="2026-10-09T00:00:00")


def test_a_failed_qualified_name_lookup_raises_and_creates_nothing(surveyor):
    """A lookup that failed is not "absent": treating it so would create a duplicate server."""
    surveyor._automated_curation.get_guid_for_name.side_effect = RuntimeError("view server down")
    with pytest.raises(EgeriaDatabaseSurveyorError, match="cannot tell whether it exists"):
        surveyor._catalog_and_survey(_host_b_sales(), db_user="u", db_pwd="p", survey_after_catalog=False)
    surveyor._automated_curation.create_elem_from_template.assert_not_called()


# ── round 2: a host change ────────────────────────────────────────────────

def _moved_host_a_sales():
    """Host A's sales, registered earlier (RE stored the DATABASE guid), now with egeria_host changed."""
    return DatabaseEntity(slug="host_a_sales", display_name="sales", db_type="postgresql",
                          host="host-a", egeria_host="host-a.internal", port=5432,
                          database_name="sales", egeria_asset_guid=A_DB)


def test_a_stored_database_guid_is_never_reused_as_the_server_when_the_database_lookup_misses(surveyor, egeria):
    result = surveyor._catalog_and_survey(_moved_host_a_sales(), db_user="u", db_pwd="p",
                                          survey_after_catalog=False)
    assert result["server_guid"] != A_DB, "the database's guid is never the server"
    assert result["server_guid"] == B_SERVER          # no server under host-a.internal: created
    assert ("PostgreSQL Server::host-a.internal:5432", ("qualifiedName",)) in egeria.lookups


def test_a_host_change_says_which_host_egeria_holds_and_how_to_fix_it(surveyor):
    surveyor._asset_maker.get_asset_by_guid.return_value = {
        "elementHeader": {"guid": A_DB, "type": {"typeName": "Database"}},
        "properties": {"qualifiedName": "PostgreSQL Relational Database::host-a:5432::sales"}}
    with pytest.raises(EgeriaDatabaseSurveyorError) as err:
        surveyor.publish_step_annotations(_moved_host_a_sales(), schema_info={}, statistics=None,
                                          surveyed_at="2026-10-09T00:00:00")
    assert str(err.value) == (
        f"The database element in Egeria ({A_DB}) is named for host host-a:5432; this registration "
        "says host host-a.internal:5432. Re-publish the database, or correct the host (Egeria host) "
        "on the registration.")
