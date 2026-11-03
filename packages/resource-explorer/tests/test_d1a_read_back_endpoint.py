"""D1-a: the read-back records what Egeria holds at the database's endpoint as its own proof row, and a failed endpoint
read never fails a schema's read-back. Over the catalogue commit's FakeEgeria; temp SQLite; no Egeria."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_catalogue_commit import (  # noqa: F401  (fixtures are re-used by name)
    _curation_access_reads,
    _zones,
    choose,
    derived,
    fake,
    press,
    registry,
    world,
)

from resource_explorer import catalogue_commit as cc
from resource_explorer import endpoint_identity as ei
from resource_explorer.catalogue_gateway import EndpointRead, GatewayError

pytestmark = pytest.mark.usefixtures("signed_in_caller")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(cc, "_sleep", lambda s: None)


def _endpoint_rows(w):
    return [p for p in w["registry"].list_catalogue_commit_proofs("db") if p["proof"] == ei.PROOF_KIND]


def test_a_read_back_records_the_endpoint_and_a_unique_one_changes_nothing(world, fake):
    choose(world, "sales", "catalogue")
    press(world, fake)
    before = derived(world)
    fake.find_endpoints = lambda search: [EndpointRead("ep-x", "x::Endpoint", "jdbc:postgresql://otherbox:5442/shop")]
    out = cc.read_back(world["registry"], fake, "db", ["sales"])
    assert out["endpoint"] == {"error": "", "families": [], "pairs": None}
    rows = _endpoint_rows(world)
    assert len(rows) == 1 and rows[0]["node_kind"] == "database"
    after = derived(world)
    assert after["endpoint"]["plan"] is None
    shape = lambda d: {n: (st["state"], "endpoint" in st) for n, st in d["schemas"].items()}
    assert shape(after) == shape(before) and after["header"]["state"] == before["header"]["state"]
    assert not any("endpoint" in st for st in after["schemas"].values())


def test_a_failed_endpoint_read_is_its_own_row_and_the_schema_still_reads_back(world, fake):
    choose(world, "sales", "catalogue")
    press(world, fake)

    def boom(search):
        raise GatewayError("Egeria answered 503")

    fake.find_endpoints = boom
    out = cc.read_back(world["registry"], fake, "db", ["sales"])
    assert out["endpoint"]["error"] == "Egeria answered 503" and out["read_failed"] == 0
    assert derived(world)["schemas"]["sales"]["state"] in cc.IN_EGERIA_STATES
    assert derived(world)["endpoint"]["error"] == "Egeria answered 503"


def test_an_endpoint_row_alone_never_makes_the_database_read_committed(registry):
    view = {"schemas": [{"name": "sales", "effective": None, "tables": []}]}
    assert cc.derive_commit_state(registry, "db", view)["header"]["state"] == "not_committed"
    registry.append_catalogue_commit_proof("db", proof=ei.PROOF_KIND, node_kind="database",
                                           detail={"resolution": {"families": [], "schemas": {}}, "plan": None})
    assert cc.derive_commit_state(registry, "db", view)["header"]["state"] == "not_committed"
