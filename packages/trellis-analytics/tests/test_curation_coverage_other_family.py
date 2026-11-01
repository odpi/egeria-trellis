"""D1-e (design §14): coverage says what another family holds at this endpoint. A schema no person chose that the
newest endpoint_resolved proof shows under another family leaves "undecided" for one line per family, "in Egeria
under '<family>' · not RE's"; no row, or a resolution with an error, and the output is today's. A fake OdsReader
whose methods bind the real signatures; the node set is stubbed (RE is an optional dependency here)."""
from __future__ import annotations

import importlib
import inspect

import pytest

from trellis_analytics.envelope import ResultState
from trellis_analytics.readers.ods import OdsReader
from trellis_analytics.routines import curation_coverage as cc_mod_fn
cc = importlib.import_module("trellis_analytics.routines.curation_coverage")   # the package re-exports the function by this name

PACK = "Coco PostgreSQL Server 1"
OWN = "host.docker.internal:5442"
RE_SCHEMAS = ["aus_inventory", "eu_inventory", "us_inventory", "warehouse"]          # RE's 4, published
LEFTOVER = ["demo", "demo_auth", "public"]                                         # nobody's: undecided
LEFT_OUT = "lab"                                                                   # left out by a confirmed rule
PACK_SCHEMAS = [f"pack_{i:02d}" for i in range(22)]


class FakeOds:
    def __init__(self, proofs, events, outbox=None):
        self._proofs, self._events, self._outbox = proofs, events, outbox or {}

    def commit_proofs(self, slug: str) -> list[dict]:
        return [dict(p) for p in self._proofs]

    def catalogue_scope_events(self, slug: str) -> list[dict]:
        return [dict(e) for e in self._events]

    def outbox_status_counts(self, entity_type: str, slug: str) -> dict[str, list[int]]:
        return dict(self._outbox)

    def registry_view(self):
        return None


def test_the_fake_binds_the_real_signatures():
    for name in ("commit_proofs", "catalogue_scope_events", "outbox_status_counts", "registry_view"):
        assert inspect.signature(getattr(FakeOds, name)) == inspect.signature(getattr(OdsReader, name)), name


@pytest.fixture
def node_set(monkeypatch):
    names = []

    def fake(ods, slug):
        return {"schemas": [{"schema": n, "classification": ""} for n in names], "chosen": "egeria"}
    monkeypatch.setattr(cc, "_node_set", fake)
    return names


def proof(i, kind, schema="", node="schema", detail=None, at="2026-10-31T12:00:00"):
    return {"id": i, "node_kind": node, "schema_name": schema, "table_name": "", "proof": kind,
            "element_guid": "", "read_at": at, "detail": detail or {}}


def event(i, schema, choice, source):
    return {"id": i, "node_kind": "schema", "schema_name": schema, "table_name": "", "choice": choice,
            "action": "set", "source": source, "proposal_rule": "", "changed_at": "2026-10-31T11:00:00"}


def el(schema, family, own):
    return {"guid": f"g-{family}-{schema}", "qualified_name": f"x::{family}::coco_pharma.{schema}",
            "family": family, "own": own, "schema": schema}


def resolution(families: dict[str, list[str]], error=""):
    schemas = {n: [el(n, OWN, True)] for n in RE_SCHEMAS}
    for fam, names in families.items():
        for n in names:
            schemas.setdefault(n, []).append(el(n, fam, False))
    return {"error": error, "endpoint": f"{OWN}/coco_pharma", "schemas": schemas, "families": []}


def world(node_set, endpoint_detail=None, extra_events=()):
    node_set[:] = sorted(RE_SCHEMAS + LEFTOVER + [LEFT_OUT] + PACK_SCHEMAS)
    proofs = [proof(i + 1, "elements_read_back", n, detail={"tables": ["t"]}) for i, n in enumerate(RE_SCHEMAS)]
    if endpoint_detail is not None:
        proofs.append(proof(90, "endpoint_resolved", node="database", detail=endpoint_detail))
    events = [event(1, LEFT_OUT, "leave_out", "rule-7"), *extra_events]
    return cc_mod_fn(FakeOds(proofs, events), "database", "coco_pharma")


def lines(r):
    return {l.key: l for l in r.group("not_yet").lines}


def unchanged_parts(r):
    return (r.group("published").model_dump(), r.group("blocked").model_dump(), r.known_schemas,
            r.counted_against, r.as_of)


def test_the_pack_schemas_leave_undecided_for_one_line_naming_the_family(node_set):
    base = world(node_set)
    r = world(node_set, {"resolution": resolution({PACK: PACK_SCHEMAS}), "plan": None})
    assert r.envelope.result_state is ResultState.MEASURED
    ny = lines(r)
    assert ny["undecided"].names == LEFTOVER
    held = ny["in_other_family"]
    assert held.count == 22 and held.names == PACK_SCHEMAS
    assert held.label == f"in Egeria under '{PACK}' · not RE's"
    assert held.rows == {"catalogue_commit_proofs": [90]}, "the line points at the read that said so"
    assert r.group("not_yet").words == (f"not yet published · 26: 3 undecided · 22 in Egeria under '{PACK}' · "
                                        "not RE's · 1 left out by a confirmed proposal")
    # it sits between undecided and chosen
    assert [l.key for l in r.group("not_yet").lines][:3] == ["undecided", "in_other_family", "chosen"]
    assert unchanged_parts(r) == unchanged_parts(base)
    assert r.count("published", "schemas") == 4


def test_no_endpoint_row_is_todays_output(node_set):
    r = world(node_set)
    assert "in_other_family" not in lines(r)
    assert lines(r)["undecided"].count == 25
    assert r.group("not_yet").words == "not yet published · 26: 25 undecided · 1 left out by a confirmed proposal"


def test_an_endpoint_row_with_an_error_is_todays_output(node_set):
    today = world(node_set).to_dict()
    r = world(node_set, {"resolution": resolution({PACK: PACK_SCHEMAS}, error="Egeria could not be read"),
                         "plan": None})
    assert r.to_dict() == today


def test_a_persons_choice_outranks_where_the_element_lives(node_set):
    r = world(node_set, {"resolution": resolution({PACK: PACK_SCHEMAS}), "plan": None},
              extra_events=[event(2, "pack_05", "leave_out", "person"), event(3, "pack_06", "catalogue", "person")])
    ny = lines(r)
    assert ny["left_out"].names == ["pack_05"]
    assert ny["chosen"].names == ["pack_06"]
    assert ny["in_other_family"].count == 20 and "pack_05" not in ny["in_other_family"].names
    assert sum(l.count for l in ny.values()) == 26


def test_two_other_families_give_two_lines(node_set):
    other = "Coco PostgreSQL Server 2"
    r = world(node_set, {"resolution": resolution({PACK: PACK_SCHEMAS[:20], other: PACK_SCHEMAS[20:]}),
                         "plan": None})
    held = [l for l in r.group("not_yet").lines if l.key == "in_other_family"]
    assert [(l.label, l.count) for l in held] == [(f"in Egeria under '{PACK}' · not RE's", 20),
                                                    (f"in Egeria under '{other}' · not RE's", 2)]
    assert lines(r)["undecided"].count == 3
