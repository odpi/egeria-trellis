"""D1-b (design §13): the database-level press links the pack's schemas to RE's database element (DataSetContent).

DESIGN-ADOPT-BY-ENDPOINT-D1-2026-10-30.md §10, §13; docs/design-notes/D1B-DATABASE-LINK-PRESS-UNDO.md. The list is the
newest `endpoint_resolved` row's `plan.database_row`; each schema is read first and an existing link skipped; one
Egeria call per missing link; one `reanchor_applied` proof row; the plan is read again after. Over the D1-a fake
gateway (the live shape: the schema at end 1, the database at end 2), whose methods bind the real gateway's
signatures (asserted below). Temp SQLite; no Egeria.
"""
from __future__ import annotations

import inspect
import re
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_catalogue_commit import (  # noqa: F401  (fixtures and helpers re-used by name)
    _curation_access_reads,
    _zones,
    as_user,
    client,
    registry,
    world,
)
from test_d1a_endpoint_resolver import PACK, RE_SERVER, FakeGateway, _coco_entity

from resource_explorer import catalogue_commit as cc
from resource_explorer import endpoint_identity as ei
from resource_explorer.catalogue_gateway import GatewayError, PyegeriaCatalogueGateway
from resource_explorer.registry import ProjectRegistry

ME = "peterprofile"
SLUG = "localhost_docker_coco_pharma"
PACK_22 = ["aus_inventory", "austin_haz_mat", "ca_payroll", "coco_expenses", "coco_haz_mat", "coco_hrim",
           "coco_inventory", "coco_ledgers", "coco_products", "cocopages", "ed_mfg_control", "eddepot01", "global_crm",
           "kcdepot01", "manufacturing_planning", "mfctrl9482", "nl_payroll", "procurement01", "sec_admin",
           "uk_payroll", "winch_mfg_control", "winchdepot01"]


class PressGateway(FakeGateway):
    """The resolver's reads and the one write, with the real gateway's signature. A link appears on the next read
    unless Egeria refuses the schema (its words) or drops it (`silent`: accepted, never on the read-back)."""

    def __init__(self):
        super().__init__()
        self.refuse: dict[str, str] = {}
        self.silent: set[str] = set()

    def link_data_set_content(self, data_set_guid: str, content_guid: str) -> None:
        self.calls.append(("link_data_set_content", data_set_guid, content_guid))
        if data_set_guid in self.refuse:
            raise GatewayError(self.refuse[data_set_guid])
        if data_set_guid not in self.silent:
            self.member(content_guid, data_set_guid, guid=f"new-{len(self.writes())}")

    def writes(self):
        return [c for c in self.calls if c[0] == "link_data_set_content"]


def coco_live() -> PressGateway:
    """coco_pharma as read 2026-10-30/31: c990f349 with RE's 4 schemas, the pack's 22 with no database element."""
    gw = PressGateway()
    gw.database("c990f349", RE_SERVER)
    for name in ("coco_ods", "coco_sus", "target_sales", "us_sales"):
        gw.schema(f"re-{name}", RE_SERVER, name)
        gw.member("c990f349", f"re-{name}")
    for name in PACK_22:
        gw.schema(f"pack-{name}", PACK, name, host="localhost")
    return gw


@pytest.fixture
def reg(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.register_database(_coco_entity())
    r.register_database(_coco_entity(slug="throwaway", db="throwaway"))
    return r


def _read(reg, gw, slug=SLUG):
    """"Read Egeria again": the endpoint_resolved row the press takes its list from."""
    return ei.record_resolution(reg, gw, reg.get_database(slug), by=ME)


def _press(reg, gw, slug=SLUG, now=None):
    return ei.apply_reanchor(reg, gw, reg.get_database(slug), by=ME, now=now)


def _applied(reg, slug=SLUG):
    return [p for p in reg.list_catalogue_commit_proofs(slug) if p["proof"] == ei.APPLIED_KIND]


def _db_row(reg, slug=SLUG):
    d = cc.derive_commit_state(reg, slug, {"schemas": []})
    return d["endpoint"]["plan"]["database_row"], d["endpoint"]


def test_the_fake_binds_the_real_gateway_signatures():
    for name in ("find_endpoints", "relationships", "link_data_set_content"):
        real = list(inspect.signature(getattr(PyegeriaCatalogueGateway, name)).parameters)
        fake = list(inspect.signature(getattr(PressGateway, name)).parameters)
        assert fake == real, name


# (1) all added
def test_all_22_are_added_with_the_database_at_end_2_and_the_plan_reads_26_and_0(reg):
    gw = coco_live()
    _read(reg, gw)
    out = _press(reg, gw)
    assert out["counts"] == {"added": 22, "already_linked": 0, "failed": 0}
    assert gw.writes() == [("link_data_set_content", f"pack-{s}", "c990f349") for s in PACK_22]   # one call each
    assert all(o["relationship_guid"].startswith("new-") for o in out["outcomes"])
    assert (out["members_after"], out["to_add_after"]) == (26, 0)
    assert re.fullmatch(r"26 members · 22 added · read back \d\d-\d\d \d\d:\d\d", out["words"]), out["words"]
    rows = _applied(reg)
    assert len(rows) == 1 and rows[0]["element_guid"] == "c990f349" and rows[0]["recorded_by"] == ME
    assert [o["outcome"] for o in rows[0]["detail"]["outcomes"]] == ["added"] * 22
    assert ei.UNDO_CALL in rows[0]["detail"]["undo"]
    db, endpoint = _db_row(reg)                                     # re-derived from the read after the press
    assert (db["members_now"], db["to_add"]) == (26, [])
    assert "after the press" not in db["words"]
    assert endpoint["press"]["counts"]["added"] == 22 and endpoint["press"]["words"] == out["words"]


# (2) idempotence
def test_a_second_press_finds_22_already_linked_and_adds_nothing(reg):
    gw = coco_live()
    _read(reg, gw)
    _press(reg, gw)
    n = len(gw.writes())
    # The plan read after the first press has nothing to add; a press on the OLD plan's list is what a stale tab or a
    # retry would send, so press with the first plan's row put back as the newest: every schema is read first.
    first = next(p for p in reg.list_catalogue_commit_proofs(SLUG) if p["proof"] == ei.PROOF_KIND)
    reg.append_catalogue_commit_proof(SLUG, proof=ei.PROOF_KIND, node_kind="database", detail=first["detail"])
    again = _press(reg, gw)
    assert len(gw.writes()) == n
    assert again["counts"] == {"added": 0, "already_linked": 22, "failed": 0}
    assert again["words"].startswith("26 members · 0 added · 22 already linked")


def test_a_press_on_a_plan_with_nothing_to_add_writes_nothing(reg):
    gw = coco_live()
    _read(reg, gw)
    _press(reg, gw)
    n = len(gw.writes())
    out = _press(reg, gw)                 # the newest plan: to_add empty
    assert len(gw.writes()) == n and out["counts"] == {"added": 0, "already_linked": 0, "failed": 0}


# (3) one failure mid-list
def test_one_failure_mid_list_still_adds_the_others_and_the_plan_shows_25_and_1(reg):
    gw = coco_live()
    gw.refuse["pack-sec_admin"] = "user peterprofile is not authorized to update pack-sec_admin"
    _read(reg, gw)
    out = _press(reg, gw)
    assert out["counts"] == {"added": 21, "already_linked": 0, "failed": 1}
    failed = [o for o in out["outcomes"] if o["outcome"] == "failed"]
    assert [(o["schema"], "not authorized" in o["error"]) for o in failed] == [("sec_admin", True)]
    sent = [w[1] for w in gw.writes()]
    assert len(sent) == 22 and sent.index("pack-sec_admin") == 18     # mid-list: the 3 after it were still sent
    assert out["words"].startswith("25 members · 21 added · 1 not linked: sec_admin (user peterprofile is not")
    db, endpoint = _db_row(reg)
    assert db["members_now"] == 25 and [x["schema"] for x in db["to_add"]] == ["sec_admin"]
    assert endpoint["press"]["failed"][0]["schema"] == "sec_admin"


def test_a_link_sent_but_not_read_back_is_failed_never_added(reg):
    gw = coco_live()
    gw.silent.add("pack-cocopages")
    _read(reg, gw)
    out = _press(reg, gw)
    o = next(o for o in out["outcomes"] if o["schema"] == "cocopages")
    assert o["outcome"] == "failed" and "not on the read-back" in o["error"]


# (4) refusals: nothing is written
def test_refused_without_a_plan_row(reg):
    gw = coco_live()
    with pytest.raises(ei.ReanchorRefused) as exc:
        _press(reg, gw)
    assert ei.READ_AGAIN in exc.value.message and gw.writes() == [] and _applied(reg) == []


def test_refused_when_the_plan_row_is_stale(reg):
    gw = coco_live()
    _read(reg, gw)
    later = datetime.now(UTC) + timedelta(seconds=cc.READ_BACK_NOT_CHECKED_AFTER_S + 5)
    with pytest.raises(ei.ReanchorRefused) as exc:
        _press(reg, gw, now=later)
    assert ei.READ_AGAIN in exc.value.message and gw.writes() == []


def test_refused_when_the_last_endpoint_read_failed(reg):
    gw = coco_live()
    gw.fail = "Egeria answered 503"
    _read(reg, gw)
    with pytest.raises(ei.ReanchorRefused) as exc:
        _press(reg, gw)
    assert "failed" in exc.value.message and ei.READ_AGAIN in exc.value.message and gw.writes() == []


def test_refused_with_no_database_row(reg):
    gw = PressGateway()
    gw.database("t-db", RE_SERVER, db="throwaway")
    gw.schema("t-s", RE_SERVER, "sales", db="throwaway")
    gw.member("t-db", "t-s")
    _read(reg, gw, "throwaway")
    with pytest.raises(ei.ReanchorRefused) as exc:
        _press(reg, gw, "throwaway")
    assert "no database-level row" in exc.value.message and gw.writes() == []


def test_refused_when_the_ends_were_not_read(reg):
    gw = coco_live()
    for rels in gw.rels.values():
        for r in rels:
            r.other_at_end1 = None
    _read(reg, gw)
    with pytest.raises(ei.ReanchorRefused) as exc:
        _press(reg, gw)
    assert "nothing proposed" in exc.value.message and gw.writes() == []


def test_one_press_per_database_at_a_time(reg, monkeypatch):
    gw = coco_live()
    _read(reg, gw)
    entered, release = threading.Event(), threading.Event()
    real = ei._apply

    def slow(*a, **k):
        entered.set()
        release.wait(5)
        return real(*a, **k)

    monkeypatch.setattr(ei, "_apply", slow)
    t = threading.Thread(target=_press, args=(reg, gw))
    t.start()
    assert entered.wait(5)
    with pytest.raises(ei.ReanchorRefused) as exc:
        _press(reg, gw)
    assert "already running" in exc.value.message
    release.set()
    t.join(5)
    assert len(gw.writes()) == 22


def test_the_proof_kind_is_the_one_contents_reads():
    assert cc.P_REANCHOR == ei.APPLIED_KIND == "reanchor_applied"


# ── the route ────────────────────────────────────────────────────────────────

@pytest.fixture
def fake():
    """The commit tests' `client` builds its gateway from this: the shop database at host.docker.internal:5442,
    RE's family with one schema and the pack with two."""
    gw = PressGateway()
    server = "host.docker.internal:5442"
    gw.database("shop-db", server, db="shop")
    gw.schema("shop-sales", server, "sales", db="shop")
    gw.member("shop-db", "shop-sales")
    for s in ("hr", "ops"):
        gw.schema(f"pack-{s}", PACK, s, host="localhost", db="shop")
    return gw


def test_the_route_refuses_a_signed_out_press_and_writes_nothing(client, fake):
    r = client.post("/api/catalogue-scope/db/reanchor")
    assert r.status_code == 401 and fake.writes() == []


def test_the_route_refuses_before_any_read_of_the_plan(client, fake):
    r = client.post("/api/catalogue-scope/db/reanchor", headers=as_user(ME))
    assert r.status_code == 409 and ei.READ_AGAIN in r.json()["detail"] and fake.writes() == []


def test_the_route_presses_after_a_read_and_the_scope_reads_the_result(client, fake, registry):
    ei.record_resolution(registry, fake, registry.get_database("db"), by=ME)     # what "Read Egeria again" appends
    r = client.post("/api/catalogue-scope/db/reanchor", headers=as_user(ME))
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["counts"] == {"added": 2, "already_linked": 0, "failed": 0} and j["members_after"] == 3
    assert [w[1:] for w in fake.writes()] == [("pack-hr", "shop-db"), ("pack-ops", "shop-db")]
    ep = client.get("/api/catalogue-scope/db").json()["commit"]["endpoint"]
    assert (ep["plan"]["database_row"]["members_now"], ep["plan"]["database_row"]["to_add"]) == (3, [])
    assert ep["press"]["words"].startswith("3 members · 2 added")


# W25: one time form on the page, "MM-DD HH:MM", in the words the press stores and in the line an older row draws
def test_the_stored_press_words_use_the_pages_one_time_form(reg):
    gw = coco_live()
    _read(reg, gw)
    out = _press(reg, gw)
    stored = _applied(reg)[0]["detail"]["words"]
    assert stored == out["words"]
    stamp = stored.rsplit(" · read back ", 1)[1]
    assert re.fullmatch(r"\d\d-\d\d \d\d:\d\d", stamp) and "2026-" not in stored


def test_a_row_pressed_before_w25_reads_in_the_pages_form():
    row = {"read_at": "2026-10-31T20:38:31.123456", "recorded_by": "dan", "detail": {
        "words": "26 members · 22 added · read back 2026-10-31 20:38", "counts": {"added": 22}, "outcomes": []}}
    assert cc._press_summary(row)["words"] == "26 members · 22 added · read back 10-31 20:38"
    row["detail"]["words"] = "26 members · 22 added · read back 2026-10-31T20:38:31.5"
    assert cc._press_summary(row)["words"] == "26 members · 22 added · read back 10-31 20:38"
