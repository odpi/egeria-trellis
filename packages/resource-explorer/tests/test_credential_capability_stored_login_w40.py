"""W40: the probe blob carries the resource's stored login beside `connected_as`, words only."""
from __future__ import annotations

import json
from types import SimpleNamespace

from resource_explorer.surveyors.credential_capability import with_stored_login
from resource_explorer.surveyors.database.db_derived import _read_credential_capability


def test_stored_login_rides_beside_connected_as():
    out = with_stored_login({"connected_as": "egeria_admin", "schema_total": 2}, " surveyor ")
    assert out == {"connected_as": "egeria_admin", "schema_total": 2, "stored_login": "surveyor"}


def test_no_probe_stays_silent():
    assert with_stored_login(None, "surveyor") is None
    assert with_stored_login({}, "surveyor") == {}


def test_read_credential_capability_adds_the_stored_login_and_no_secret():
    class Reg:
        def get_database_surveys(self, slug):
            return [{"survey_data": json.dumps({"credential_capability": {"connected_as": "egeria_admin"}})}]

        def get_database(self, slug):
            return SimpleNamespace(db_user="surveyor", db_password="s3cret")

    cap = _read_credential_capability(Reg(), "x")
    assert cap["stored_login"] == "surveyor" and "s3cret" not in json.dumps(cap)
