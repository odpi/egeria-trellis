"""Stabilisation slice 1 (2026-10-07): a proof row proves exactly ONE element, by GUID, and is
written only AFTER a read of that GUID returns what the row claims.

The trace (2026-10-06, coco_pharma): a template create 500'd halfway, RE adopted the half-built
element and wrote `target_attached`; `elements_read_back` read the OLD schema's tree by name; a
409 publish still wrote `report_published`. The page said attached and cataloged for none of it."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_catalogue_commit import (  # noqa: E402,F401
    ME, SOURCE, _measured, choose, derived, entity, fake, press, registry, step, view, world)

from resource_explorer import catalogue_commit as cc  # noqa: E402
from resource_explorer import catalogue_gateway as gw  # noqa: E402
from resource_explorer.catalogue_gateway import GatewayError, Relationship  # noqa: E402

# Brief I: a catalog commit is a person's own action; it runs as a signed-in caller.
pytestmark = pytest.mark.usefixtures("signed_in_caller")

SALES_QN = "PostgreSQL Relational Database Schema::host.docker.internal:5442::shop.sales"
EGERIA_SENTENCE = ("OMAG-REPOSITORY-HANDLER-500-001 Egeria could not link the new schema to its parent database "
                   "because the parent relationship was rejected by the repository.")
LONG_500 = ("SERVER_ERROR_500 => Egeria detected error: `https://localhost:9443/servers/x/catalog-templates/new-element`. "
            + EGERIA_SENTENCE + " " + ("padding " * 60))          # well past the old 300-character cut


@pytest.fixture(autouse=True)
def _zone_access_open(monkeypatch):
    """Brief Z2: the commit asks `curation_access` for the database and each schema. These tests are about proof
    rows, so no zone is in use: the access reads are faked open at the client boundary."""
    from tests.zone_fakes import open_access

    return open_access(monkeypatch)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(cc, "_sleep", lambda s: None)


def rows(world, *kinds):
    return [p for p in world["registry"].list_catalogue_commit_proofs("db") if p["proof"] in kinds]


SUCCESS = (cc.P_TARGET, cc.P_ELEMENTS, cc.P_REPORT)


def _half_built(fake):
    fake.create_error_half_built = True
    fake.create_error_text = LONG_500
    # a read by NAME can still land on the OLD tree: its tables are under the same prefix
    fake.stale_under = [(f"{SALES_QN}::orders", "RelationalTable"), (f"{SALES_QN}::orders::id", "RelationalColumn")]


# ── E: the whole trace at once ───────────────────────────────────────────────────────────────────

def test_a_half_built_create_writes_no_success_row_and_the_page_does_not_read_attached(world, fake, monkeypatch):
    _measured(world)
    monkeypatch.setattr(fake, "publish_local_report", lambda *a, **k: {
        "annotation_count": 76, "report_guid": "", "report_error": "409 CONFLICT a report with that name exists"})
    _half_built(fake)
    choose(world, "sales", "catalogue")
    _, rec = press(world, fake)
    assert rows(world, *SUCCESS) == []
    assert rows(world, cc.P_ADOPTED) == []                       # D failed, so nothing was adopted
    st = step(rec, "schema_targets")
    assert st["state"] == "failed" and "schema created without its connection · " in st["detail"]
    s = derived(world)["schemas"]["sales"]
    assert s["state"] not in ("attached_waiting", "catalogued")
    assert "attached" not in s["words"] and "cataloged" not in s["words"]
    assert fake.targets == []


# ── A: target_attached only after a relationship read on THAT element ────────────────────────────

def test_target_attached_needs_the_element_s_own_catalog_target_relationship(world, fake, monkeypatch):
    real = fake.relationships

    def no_target(guid):
        return [r for r in real(guid) if r.type_name != "CatalogTarget"]
    monkeypatch.setattr(fake, "relationships", no_target)
    choose(world, "sales", "catalogue")
    _, rec = press(world, fake, refresh=False)
    assert rows(world, cc.P_TARGET) == []                          # the target LIST had it; the element does not
    assert step(rec, "schema_targets")["state"] == "failed"


def test_target_attached_carries_the_relationship_guid_read_from_the_element(world, fake):
    choose(world, "sales", "catalogue")
    press(world, fake, refresh=False)
    row = rows(world, cc.P_TARGET)[-1]
    g = fake.by_qn(SALES_QN)["guid"]
    assert row["element_guid"] == g and row["target_guid"] == fake.targets[0].relationship_guid


# ── A: elements_read_back by the created GUID's own Schema link, never by name ───────────────────

def test_elements_read_back_ignores_tables_the_name_prefix_finds_under_an_old_tree(world, fake):
    fake.stale_under = [(f"{SALES_QN}::ghost", "RelationalTable"), (f"{SALES_QN}::ghost::c", "RelationalColumn")]
    choose(world, "sales", "catalogue")
    press(world, fake, refresh=False)                               # attached, cataloguer has not run
    assert rows(world, cc.P_ELEMENTS) == []
    assert derived(world)["schemas"]["sales"]["state"] == "attached_waiting"


def test_elements_read_back_lists_the_tables_the_guid_s_own_schema_type_holds(world, fake):
    choose(world, "sales", "catalogue")
    press(world, fake)                                              # refresh runs the cataloguer
    row = rows(world, cc.P_ELEMENTS)[-1]
    assert row["element_guid"] == fake.by_qn(SALES_QN)["guid"]
    assert sorted(row["detail"]["tables"]) == sorted(SOURCE["sales"])
    assert row["detail"]["columns"] == sum(len(c) for c in SOURCE["sales"].values())


# ── A: report_published only with a guid and no error ────────────────────────────────────────────

def test_a_409_on_the_publish_writes_no_report_published_row_but_a_read_failed_one_with_the_full_text(world, fake, monkeypatch):
    _measured(world)
    long_409 = "409 CONFLICT " + ("a report with that name exists; " * 30)
    monkeypatch.setattr(fake, "publish_local_report", lambda *a, **k: {
        "annotation_count": 76, "report_guid": "", "report_error": long_409})
    choose(world, "sales", "catalogue")
    press(world, fake)
    assert rows(world, cc.P_REPORT) == []
    failed = [r for r in rows(world, cc.P_READ_FAILED) if r["detail"].get("what") == "RE's own survey report"]
    assert len(failed) == 1 and failed[0]["detail"]["error"] == long_409      # B: not cut


def test_a_report_with_no_guid_writes_no_report_published_row(world, fake, monkeypatch):
    _measured(world)
    monkeypatch.setattr(fake, "publish_local_report", lambda *a, **k: {"annotation_count": 76})
    choose(world, "sales", "catalogue")
    press(world, fake)
    assert rows(world, cc.P_REPORT) == []


# ── B: Egeria's full sentence ───────────────────────────────────────────────────────────────────

def test_the_gateway_keeps_egeria_s_whole_sentence():
    long = "x" * 1500
    assert gw._short(RuntimeError(long)) == long
    assert len(gw._short(RuntimeError("y" * 9000))) == gw.MAX_EGERIA_TEXT == 4000


# ── C and D: adoption after a create error ──────────────────────────────────────────────────────

def test_an_adoption_that_is_verified_is_its_own_row_with_the_full_sentence_and_the_guid(world, fake):
    fake.create_error_after_creating = True
    fake.create_error_text = LONG_500
    choose(world, "sales", "catalogue")
    _, rec = press(world, fake, refresh=False)
    ad = rows(world, cc.P_ADOPTED)
    assert len(ad) == 1 and ad[0]["element_guid"] == fake.by_qn(SALES_QN)["guid"]
    assert EGERIA_SENTENCE in ad[0]["detail"]["egeria_said"] and ad[0]["detail"]["egeria_said"].endswith("padding")
    s = derived(world)["schemas"]["sales"]
    assert s["words"].startswith("adopted after a create error · ") and EGERIA_SENTENCE in s["words"]
    assert "attached" not in s["words"].lower()
    assert step(rec, "schema_targets")["state"] == "done"


def test_adopted_proof_is_context_not_a_state_word():
    assert cc.P_ADOPTED == "create_error_adopted" and cc.P_ADOPTED not in cc.STATE_PROOFS


@pytest.mark.parametrize("missing", ["linked", "connected"])
def test_an_adopted_element_missing_its_link_or_its_connection_fails_the_row(world, fake, missing):
    fake.create_error_half_built = True
    fake.create_error_text = LONG_500
    orig = fake.create_schema_element

    def create(*a, **k):
        try:
            return orig(*a, **k)
        finally:
            g = fake.by_qn(SALES_QN)["guid"]
            fake.elements[g]["linked"] = missing != "linked"
            fake.elements[g]["connected"] = missing != "connected"
    fake.create_schema_element = create
    choose(world, "sales", "catalogue")
    _, rec = press(world, fake, refresh=False)
    st = step(rec, "schema_targets")
    assert st["state"] == "failed" and "schema created without its connection · " in st["detail"]
    assert rows(world, *SUCCESS, cc.P_ADOPTED) == [] and fake.targets == []


def test_an_adopted_element_linked_to_some_other_database_is_not_verified(world, fake, monkeypatch):
    fake.create_error_after_creating = True
    real = fake.relationships

    def elsewhere(guid):
        return [Relationship(r.type_name, other_guid="some-other-db") if r.type_name == "DataSetContent" else r
                for r in real(guid)]
    monkeypatch.setattr(fake, "relationships", elsewhere)
    choose(world, "sales", "catalogue")
    press(world, fake, refresh=False)
    assert rows(world, *SUCCESS, cc.P_ADOPTED) == []


# ── no regression: a clean create still writes the same success rows ─────────────────────────────

def test_a_clean_create_still_writes_target_elements_and_report_rows(world, fake):
    _measured(world)
    choose(world, "sales", "catalogue")
    press(world, fake)
    assert len(rows(world, cc.P_TARGET)) >= 1 and len(rows(world, cc.P_ELEMENTS)) >= 1
    assert len(rows(world, cc.P_REPORT)) == 1 and rows(world, cc.P_ADOPTED) == []
    s = derived(world)["schemas"]["sales"]
    assert s["state"] == "catalogued" and s["words"].startswith("published · ")
