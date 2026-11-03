"""Database credentials reach Egeria's templates ONLY through the secrets store; plaintext is never sent.

Before, both template calls sent databaseUserId + databasePassword unconditionally, next to the secrets pair.
Egeria's PostgreSQL Server and Database templates declare neither (PostgresPlaceholderProperty
.getPostgresServerPlaceholderPropertyTypes / getPostgresDatabasePlaceholderPropertyTypes), so a plaintext pair is
discarded and gives the element no credentials. Owner decision 2026-10-21: "drop the fallback if it isn't
useful" -- a failed save sends nothing credential-bearing and logs the failure.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, create_autospec

import pytest

from resource_explorer.registry import DatabaseEntity
from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor

pytestmark = pytest.mark.usefixtures("signed_in_caller")

PASSWORD = "s3cr3t-not-real"
USER = "surveyor_user"

#: Placeholders each template declares, from Egeria's PostgresPlaceholderProperty.java (lines 160-196).
SECRETS = {"secretsStorePathName", "secretsCollectionName"}
SERVER_DECLARED = {"hostIdentifier", "portNumber", "serverName", "description", "versionIdentifier",
                   "resourceName"} | SECRETS
DATABASE_DECLARED = {"hostIdentifier", "portNumber", "serverName", "versionIdentifier", "databaseName",
                     "databaseDescription"} | SECRETS


def _run(save_result, found=""):
    from pyegeria import MetadataExpert
    ent = DatabaseEntity(slug="shop_db", display_name="Shop", db_type="postgresql", host="localhost",
                         egeria_host="host.docker.internal", port=5442, database_name="shop", description="d")
    s = EgeriaDatabaseSurveyor(platform_url="http://x")
    s.connect = lambda: None
    s._metadata_expert = create_autospec(MetadataExpert, spec_set=True, instance=True)
    s._metadata_expert.find_metadata_elements_with_string.return_value = "No elements found"
    s._find_by_qualified_name = (lambda qn: f"{found}-{qn}") if found else (lambda qn: "")
    s._warn_if_database_has_no_connection = lambda *a, **k: None
    save = create_autospec(EgeriaDatabaseSurveyor._save_database_secret, spec_set=True, return_value=save_result)
    create = create_autospec(EgeriaDatabaseSurveyor._create_postgres_element_from_template, spec_set=True,
                             side_effect=lambda self, tech, ph: f"guid-{tech}")
    s._save_database_secret = lambda *a, **k: save(s, *a, **k)
    s._create_postgres_element_from_template = lambda tech, ph: create(s, tech, ph)
    s._catalog_and_survey(ent, USER, PASSWORD, registry=None, survey_after_catalog=False)
    return {c.args[1]: c.args[2] for c in create.call_args_list}, save


def test_secret_saved_means_no_plaintext_on_either_template():
    calls, save = _run(("coll", "/store"))
    assert set(calls) == {"PostgreSQL Server", "PostgreSQL Relational Database"}
    for ph in calls.values():
        assert ph["secretsCollectionName"] == "coll" and ph["secretsStorePathName"] == "/store"
        assert "databasePassword" not in ph and "databaseUserId" not in ph
        assert PASSWORD not in str(ph) and USER not in str(ph)
    assert save.call_count == 1


def test_save_failed_sends_no_credentials_at_all_and_warns_without_the_password(caplog):
    with caplog.at_level(logging.WARNING):
        calls, _ = _run(("", ""))
    for ph in calls.values():
        assert "databasePassword" not in ph and "databaseUserId" not in ph
        assert PASSWORD not in str(ph) and USER not in str(ph)
        assert not (SECRETS & set(ph))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "shop_db" in text and "secrets-store save failed; element created without credentials" in text
    assert PASSWORD not in text


@pytest.mark.parametrize("save_result", [("coll", "/store"), ("", "")])
def test_no_declared_placeholder_is_left_unfilled_beyond_the_secrets_pair_when_it_failed(save_result):
    calls, _ = _run(save_result)
    saved = bool(save_result[0])
    for tech, declared in (("PostgreSQL Server", SERVER_DECLARED), ("PostgreSQL Relational Database", DATABASE_DECLARED)):
        ph = calls[tech]
        unfilled = declared - set(ph)
        # On a failed save the secrets pair has no truthful value to bind; every other declared key is filled.
        assert unfilled == (set() if saved else SECRETS), (tech, unfilled)
        assert all(ph[k] for k in declared - unfilled), tech
        assert "~{" not in str(ph)


def test_reuse_by_name_writes_no_secret_and_calls_no_template():
    calls, save = _run(("coll", "/store"), found="existing-guid")
    assert calls == {}
    save.assert_not_called()
