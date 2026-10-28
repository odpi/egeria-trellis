"""Brief I round 6: the catalog commit stamps an element only when it is SURE this commit created it
(nothing under this host's qualifiedName before, and the returned GUID reads back with exactly the
expected qualifiedName); the data-class rules read stops at the first timed-out lookup and closes
the clients it built. Fakes only; nothing reaches Egeria."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_catalogue_commit import choose, fake, registry, world  # noqa: E402,F401
from test_egeria_identity_round4 import _press_then_queue  # noqa: E402

from resource_explorer.catalogue_gateway import PublishedDatabase  # noqa: E402


@pytest.fixture(autouse=True)
def _zone_access_open(monkeypatch):
    """Brief Z2: this write path asks `curation_access` (the one decision). These tests are about something else,
    so no zone is in use: the access reads are faked open at the client boundary (`tests/zone_fakes.open_access`)."""
    from tests.zone_fakes import open_access

    return open_access(monkeypatch)


@pytest.fixture(autouse=True)
def _daemon(monkeypatch):
    monkeypatch.setattr("resource_explorer.egeria_clients._daemon_credential", lambda: ("re-daemon", "pw"))


def test_a_database_adopted_from_another_host_is_not_stamped_and_says_so(world, fake, monkeypatch):
    """The surveyor adopts by bare name, so it can hand back ANOTHER host's same-named database."""
    other_qn = "PostgreSQL Relational Database::other-host:5432::shop"
    other_guid = fake.add_element(other_qn, "RelationalDatabase")
    real = fake.publish_database

    def adopted_elsewhere(db_entity, *a, **k):
        pub = real(db_entity, *a, **k)
        fake.db_guid = other_guid
        return PublishedDatabase(pub.server_guid, other_guid, pub.server_name, pub.database_qualified_name)
    monkeypatch.setattr(fake, "publish_database", adopted_elsewhere)
    choose(world, "sales", "catalogue")
    _out, rec = _press_then_queue(world, fake, "dana")
    stamped = {c[1] for c in fake.calls if c[0] == "mark_on_behalf"}
    assert other_guid not in stamped
    step = next(s for s in rec["steps"] if s["name"] == "publish_elements")
    assert (f"partial · adopted an element for a different host ({other_qn}); requester not recorded"
            in step["detail"])


def test_a_server_whose_guid_reads_back_under_another_name_is_not_stamped(world, fake, monkeypatch):
    real = fake.publish_database

    def odd_server(db_entity, *a, **k):
        pub = real(db_entity, *a, **k)
        fake.elements[pub.server_guid]["qn"] = "PostgreSQL Server::somewhere-else:5432"
        return pub
    monkeypatch.setattr(fake, "publish_database", odd_server)
    choose(world, "sales", "catalogue")
    _out, rec = _press_then_queue(world, fake, "dana")
    stamped = {c[1] for c in fake.calls if c[0] == "mark_on_behalf"}
    assert fake.server_guid not in stamped and fake.db_guid in stamped
    step = next(s for s in rec["steps"] if s["name"] == "publish_elements")
    assert "adopted an element for a different host (PostgreSQL Server::somewhere-else:5432)" in step["detail"]


def test_elements_this_commit_created_on_this_host_are_still_stamped(world, fake):
    choose(world, "sales", "catalogue")
    _press_then_queue(world, fake, "dana")
    stamped = {c[1] for c in fake.calls if c[0] == "mark_on_behalf"}
    assert {fake.server_guid, fake.db_guid} <= stamped


def test_the_rules_read_stops_at_the_first_timeout_and_closes_its_clients(monkeypatch):
    import time as _t

    from fastapi.testclient import TestClient

    from resource_explorer.auth import create_access_token
    from resource_explorer.web import app as web_app
    from resource_explorer.web.routes import egeria as routes

    monkeypatch.setattr(routes, "_DATACLASS_GUID_TIMEOUT_SECONDS", 0.2)
    asked, closed = [], []

    class Designer:
        def __init__(self, *a):
            pass

        def set_bearer_token(self, t):
            pass

        def get_guid_for_name(self, qn):
            asked.append(qn)
            _t.sleep(1)                                   # the known hang
            return "guid"

        def close_session(self):
            closed.append(1)

    class Ref(Designer):
        def find_valid_value_definitions(self, **k):
            return []
    monkeypatch.setattr("pyegeria.omvs.data_designer.DataDesigner", Designer)
    monkeypatch.setattr("pyegeria.omvs.reference_data.ReferenceDataManager", Ref)
    hdr = {"Authorization": "Bearer " + create_access_token(user_id="dan", egeria_token="tok-dan")}
    r = TestClient(web_app.app).get("/api/egeria/rules/dataclasses", headers=hdr)
    _t.sleep(1.2)                                         # let the abandoned lookup finish and close
    assert r.status_code == 200 and len(r.json()) == 6
    assert {x["source"] for x in r.json()} == {"Local Fallback"}
    assert len(asked) == 1, "no second lookup after the first timed out"
    assert closed == [1], "the one shared=False client was closed after use"
