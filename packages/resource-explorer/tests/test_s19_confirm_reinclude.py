"""S19, owner ruling 2026-10-14: re-including an archived element is a confirmed create, never a silent one.

Egeria archives an element by renaming it `<qn>_archivedOn_<date>` and keeping it as a Memento (captured
2026-10-14, `tests/fixtures/egeria_6_2/*memento*`). The original name is then free: the old S19 lookup by that name
found nothing and never fired, and a re-include would have created a new element without a word. Now RE looks for
the `_archivedOn_` sibling before every create that would re-include it (schema attach, database publish), does not
create while one exists, says "archived in Egeria · <date> · re-including creates a new element; the archived
record stays", and creates only on a confirmation that names the archived GUID, re-checked by the server. The new
create's proof row records the archived sibling's GUID and date.

Every registry is a tmp SQLite file (the fixtures below come from `test_catalogue_commit`); Egeria is the stateful
`FakeEgeria`, which now renames on archive exactly as Egeria does, and the real gateway is driven through a
`create_autospec(MetadataExpert, spec_set=True)` client answering the captured 6.2 shapes.
"""
# ruff: noqa: F811  (the fixtures are imported from test_catalogue_commit and requested by name)
from __future__ import annotations

import copy
import json
import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import create_autospec

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fake_egeria_catalogue import FakeEgeria
from test_catalogue_commit import (  # noqa: F401  (fixtures and helpers, no tests)
    ME, SOURCE, _curation_access_reads, _zones, choose, client, derived, fake, press, registry, step, view, world, as_user)

from resource_explorer import catalogue_commit as cc
from resource_explorer import catalogue_gateway as gw

pytestmark = pytest.mark.usefixtures("signed_in_caller")

SQN = "PostgreSQL Relational Database Schema::host.docker.internal:5442::shop.archive"
DBQN = "PostgreSQL Relational Database::host.docker.internal:5442::shop"
FIXTURES = Path(__file__).parent / "fixtures" / "egeria_6_2"


def captured(name: str):
    return copy.deepcopy(json.loads((FIXTURES / f"{name}.json").read_text())["answer"])


def _archived(world, fake) -> str:
    """Publish `archive`, then leave it out with a term on it: Egeria archives (and renames) it. Its GUID."""
    fake.zone_lockout = False          # not about zones: the configured zone is one this identity may write in
    choose(world, "archive", "catalogue")
    press(world, fake)
    fake.add_term_assignment("orders", SQN)
    guid = fake.by_qn(SQN)["guid"]
    choose(world, "archive", "leave_out")
    press(world, fake)
    assert fake.by_qn(SQN) is None and fake.elements[guid]["archived"]
    choose(world, "archive", "catalogue")
    return guid


def _creates(fake) -> list[str]:
    return [c[1] for c in fake.ops("create_schema_element")]


def _reincluded(world) -> list[dict]:
    return [p for p in world["registry"].list_catalogue_commit_proofs("db") if p["proof"] == cc.P_REINCLUDED]


# ── 1. an archived sibling exists: no create, needs_confirmation with the date ─────────────────────────────────────

def test_an_archived_sibling_means_no_create_and_needs_confirmation_with_its_date(world, fake):
    archived = _archived(world, fake)
    n = len(_creates(fake))
    p = cc.build_preview(world["registry"], "db", view(world), fake)
    assert p["needs_confirmation"] == [{
        "node": "schema", "name": "archive", "archived_guid": archived,
        "archived_qualified_name": f"{SQN}_archivedOn_Fri Oct 09 01:28:08 GMT 2026",
        "archived_on": "Fri Oct 09 01:28:08 GMT 2026", "archived_at": "2026-10-09T01:28:08+00:00", "date": "2026-10-09",
        "words": "archived · 2026-10-09",
        "text": "archive: archived in Egeria · 2026-10-09 · re-including creates a new element; the archived record stays"}]
    assert "archive" not in p["attach"]
    # the attach itself (as the outbox runs it) refuses with the same state and creates nothing
    chk = cc.reinclude_check(fake, SQN)
    assert chk["state"] == cc.NEEDS_CONFIRMATION and chk["sibling"].guid == archived
    with pytest.raises(cc.SchemaRefused, match="archived in Egeria · 2026-10-09"):
        cc.apply_attach(world["registry"], fake, {"slug": "db", "schema": "archive", "database_guid": fake.db_guid})
    assert len(_creates(fake)) == n and _reincluded(world) == []
    # and the sibling is looked up BEFORE any create is attempted
    assert fake.ops("archived_siblings")[-1] == ("archived_siblings", SQN)


# ── 2. confirm: a create, with the sibling GUID on the proof row ───────────────────────────────────────────────────

def test_a_confirmed_reinclude_creates_and_records_the_archived_sibling_on_the_proof_row(world, fake):
    """End to end (§5b rule 2): the press and the run with only Egeria faked; the next read shows the state."""
    archived = _archived(world, fake)
    confirm = [{"node": "schema", "name": "archive", "archived_guid": archived}]
    out = cc.start_commit(world["registry"], "db", ME, refresh_now=True, gateway=fake, confirm_reinclude=confirm)
    rec = cc.execute_commit(world["registry"], out["curation"]["id"], gateway=fake)
    assert out["curation"]["selection"]["reinclude"] == [
        {"node": "schema", "name": "archive", "archived_guid": archived, "archived_on": "Fri Oct 09 01:28:08 GMT 2026"}]
    assert _creates(fake)[-1] == "archive"
    new = fake.by_qn(SQN)["guid"]
    assert new != archived and fake.elements[archived]["archived"]           # the archived record stays
    rows = _reincluded(world)
    assert len(rows) == 1 and rows[0]["element_guid"] == new and rows[0]["schema_name"] == "archive"
    d = rows[0]["detail"]
    assert d["archived_sibling_guid"] == archived and d["archived_on"] == "Fri Oct 09 01:28:08 GMT 2026"
    assert d["archived_at"] == "2026-10-09T01:28:08+00:00" and d["confirmed_by"] == ME
    assert step(rec, "schema_targets")["state"] == "done"
    # the next read: published, and a fresh preview asks nothing (a live element holds the name now)
    assert derived(world)["schemas"]["archive"]["state"] == "catalogued"
    assert cc.build_preview(world["registry"], "db", view(world), fake)["needs_confirmation"] == []


# ── 3. confirm, but the sibling changed in the meantime: refuse ────────────────────────────────────────────────────

def test_a_confirmation_naming_a_copy_that_is_no_longer_the_newest_is_refused_at_the_press(world, fake, registry):
    archived = _archived(world, fake)
    # meanwhile: re-included elsewhere and archived again, so a NEWER archived copy is the sibling now
    again = fake.add_element(SQN, "DeployedDatabaseSchema", parent=fake.db_guid)
    fake.archive_clock += timedelta(days=2)
    fake.archive(again)
    n, rows = len(_creates(fake)), len(registry.list_catalogue_outbox_rows("db"))
    with pytest.raises(cc.CommitBlocked) as err:
        cc.start_commit(world["registry"], "db", ME, gateway=fake,
                        confirm_reinclude=[{"node": "schema", "name": "archive", "archived_guid": archived}])
    assert err.value.status == 409 and err.value.message == cc.SIBLING_CHANGED
    assert len(_creates(fake)) == n and len(registry.list_catalogue_outbox_rows("db")) == rows   # nothing queued


def test_the_run_re_checks_the_sibling_before_it_creates(world, fake):
    """The press saw copy A; by the time the outbox row runs, copy B is the newest: no create."""
    archived = _archived(world, fake)
    again = fake.add_element(SQN, "DeployedDatabaseSchema", parent=fake.db_guid)
    fake.archive_clock += timedelta(days=2)
    fake.archive(again)
    n = len(_creates(fake))
    with pytest.raises(cc.SchemaRefused, match="changed since you confirmed"):
        cc.apply_attach(world["registry"], fake, {"slug": "db", "schema": "archive", "database_guid": fake.db_guid,
                                                  "archived_sibling_guid": archived})
    assert len(_creates(fake)) == n and _reincluded(world) == []
    # naming the newest copy, it goes ahead
    cc.apply_attach(world["registry"], fake, {"slug": "db", "schema": "archive", "database_guid": fake.db_guid,
                                              "archived_sibling_guid": again, "by": ME})
    assert len(_creates(fake)) == n + 1 and _reincluded(world)[0]["detail"]["archived_sibling_guid"] == again
    assert _reincluded(world)[0]["detail"]["other_archived_guids"] == [archived]


# ── 4. the lookup cannot be made: no create ────────────────────────────────────────────────────────────────────────

def test_a_lookup_that_cannot_be_made_creates_nothing_and_says_so(world, fake):
    choose(world, "sales", "catalogue")
    fake.fail["schema_elements"] = "503 the view server timed out"       # the preview's one paged find
    p = cc.build_preview(world["registry"], "db", view(world), fake)
    assert p["refused"] == [{"schema": "sales", "state": "cannot_check",
                             "text": "sales: could not check whether an archived copy exists"}]
    assert "sales" not in p["attach"] and not p["can_commit"]
    fake.fail["archived_siblings"] = "503 the view server timed out"     # the run's own re-check
    with pytest.raises(gw.GatewayError, match="could not check whether an archived copy exists"):
        cc.apply_attach(world["registry"], fake, {"slug": "db", "schema": "sales", "database_guid": "d"})
    assert _creates(fake) == []


def test_no_gateway_means_could_not_check_never_a_create(world):
    choose(world, "sales", "catalogue")
    p = cc.build_preview(world["registry"], "db", view(world), None)
    assert p["refused"][0]["text"] == "sales: could not check whether an archived copy exists" and p["attach"] == []


# ── 5. no sibling: a normal create ─────────────────────────────────────────────────────────────────────────────────

def test_no_archived_sibling_is_a_normal_create_with_no_reinclude_row(world, fake):
    choose(world, "sales", "catalogue")
    p = cc.build_preview(world["registry"], "db", view(world), fake)
    assert p["needs_confirmation"] == [] and p["attach"] == ["sales"] and p["can_commit"]
    press(world, fake)
    assert _creates(fake) == ["sales"] and _reincluded(world) == []
    names = [c[0] for c in fake.calls]
    assert "archived_siblings" in names and names.index("archived_siblings") < names.index("create_schema_element")


# ── the database element: the same guard before a publish under its name ─────────────────────────────────────────

def test_an_archived_database_blocks_the_press_until_confirmed_then_a_new_database_is_recorded(world, fake):
    choose(world, "sales", "catalogue")
    press(world, fake)
    old_db = fake.db_guid
    for g, e in list(fake.elements.items()):            # Egeria archived the database's whole tree (ISSUE-117)
        if e["qn"].startswith(DBQN.replace("PostgreSQL Relational Database::", "PostgreSQL Relational Database Schema::")) \
                or g == old_db:
            fake.archive(g)
    p = cc.build_preview(world["registry"], "db", view(world), fake)
    assert p["needs_confirmation"][0]["node"] == "database" and p["needs_confirmation"][0]["archived_guid"] == old_db
    assert not p["can_commit"] and any("the database is archived in Egeria" in b for b in p["blockers"])
    n = len(fake.ops("publish_database"))
    with pytest.raises(cc.CommitBlocked):
        cc.start_commit(world["registry"], "db", ME, gateway=fake)
    assert len(fake.ops("publish_database")) == n
    sales_archived = fake.archived_siblings(SQN.replace("archive", "sales"))[0].guid
    out = cc.start_commit(world["registry"], "db", ME, gateway=fake, confirm_reinclude=[
        {"node": "database", "name": "", "archived_guid": old_db},
        {"node": "schema", "name": "sales", "archived_guid": sales_archived}])
    rec = cc.execute_commit(world["registry"], out["curation"]["id"], gateway=fake)
    assert step(rec, "publish_elements")["state"] == "done" and fake.db_guid != old_db
    by_node = {p["node_kind"]: p for p in _reincluded(world)}
    assert by_node["database"]["element_guid"] == fake.db_guid
    assert by_node["database"]["detail"]["archived_sibling_guid"] == old_db
    assert by_node["schema"]["detail"]["archived_sibling_guid"] == sales_archived


def test_the_run_refuses_an_unconfirmed_archived_database_even_past_the_press(world, fake):
    choose(world, "sales", "catalogue")
    press(world, fake)
    old_db = fake.db_guid
    out = cc.start_commit(world["registry"], "db", ME, gateway=fake)
    fake.archive(old_db)                                # archived between the press and the run
    n = len(fake.ops("publish_database"))
    rec = cc.execute_commit(world["registry"], out["curation"]["id"], gateway=fake)
    st = step(rec, "publish_elements")
    assert st["state"] == "failed" and "re-including creates a new element" in st["detail"]
    assert "nothing created" in st["detail"] and len(fake.ops("publish_database")) == n


# ── the real gateway's lookup: the pyegeria 6.2.3 call and the captured answers ───────────────────────────────────

def _gateway_with(answer):
    from pyegeria import MetadataExpert
    client = create_autospec(MetadataExpert, spec_set=True, instance=True)
    if isinstance(answer, BaseException):
        client.find_metadata_elements_with_string.side_effect = answer
    else:
        client.find_metadata_elements_with_string.return_value = answer
    g = gw.PyegeriaCatalogueGateway(view_server="qs-view-server", platform_url="https://localhost:9443")
    g._clients = {"MetadataExpert": client}
    return g, client


CAPTURED_ORIGINAL = "PostgreSQL Relational Database::host.docker.internal:5432::re_throwaway_62"


def test_the_gateway_finds_the_captured_6_2_memento_by_its_archived_name():
    page = captured("metadata_expert_find_with_string_for_lineage__memento")
    g, client = _gateway_with(None)
    client.find_metadata_elements_with_string.side_effect = [page, "No elements found"]
    sibs = g.archived_siblings(CAPTURED_ORIGINAL)
    # ordered by Egeria's own archive time (the Memento's archiveDate), the day read from the suffix
    assert [(s.guid, s.date, s.archived_at, s.archived) for s in sibs] == [
        ("ad2724e6-40e9-46a9-aaff-e3352c6b04bb", "2026-10-09", "2026-10-09T01:28:07.233000+00:00", True)]
    first, end = client.find_metadata_elements_with_string.call_args_list
    assert first.kwargs["search_string"] == CAPTURED_ORIGINAL + "_archivedOn_" and first.kwargs["starts_with"] is True
    assert first.kwargs["body"] == {"class": "SearchStringRequestBody", "searchString": CAPTURED_ORIGINAL + "_archivedOn_",
                                    "startsWith": True, "ignoreCase": False, "forLineage": True,
                                    "forDuplicateProcessing": True, "graphQueryDepth": 0, "startFrom": 0, "pageSize": 500}
    assert end.kwargs["body"]["startFrom"] == 500                          # read to "No elements found"
    # the same element is NOT a sibling of a name it merely starts with
    client.find_metadata_elements_with_string.side_effect = [page, "No elements found"]
    assert g.archived_siblings(CAPTURED_ORIGINAL[:-3]) == []


def test_the_gateway_reads_no_elements_found_as_no_sibling():
    g, _ = _gateway_with("No elements found")
    assert g.archived_siblings(CAPTURED_ORIGINAL) == []


@pytest.mark.parametrize("answer", [None, {"unexpected": 1}, TimeoutError("read timed out"),
                                    "captured_not_visible"])
def test_the_gateway_fails_closed_when_the_lookup_cannot_be_made(answer):
    if answer == "captured_not_visible":
        from pyegeria.core._exceptions import PyegeriaAPIException
        answer = RuntimeError(captured("metadata_expert_by_guid__memento_not_visible")["message"])
        assert not issubclass(RuntimeError, PyegeriaAPIException)
    g, _ = _gateway_with(answer)
    with pytest.raises(gw.SiblingLookupFailed, match="could not check whether an archived copy exists"):
        g.archived_siblings(CAPTURED_ORIGINAL)


# ── the fake: renames on archive, as Egeria does; the old shape is refused ────────────────────────────────────────

def test_the_fake_renames_on_archive_exactly_as_the_captured_6_2_answer_shows():
    fake = FakeEgeria(SOURCE)
    g = fake.add_element(SQN, "DeployedDatabaseSchema")
    new_qn = fake.archive(g)
    real = captured("metadata_expert_find_with_string_for_lineage__memento")[0]
    real_qn = real["elementProperties"]["propertiesAsStrings"]["qualifiedName"]
    assert new_qn.split("_archivedOn_")[1] == real_qn.split("_archivedOn_")[1]     # same Java Date form
    assert fake.read_element(SQN) is None and fake.read_element(SQN, for_lineage=True) is None
    assert fake.read_element(new_qn, for_lineage=True).archived
    assert [c["classificationName"] for c in fake.raw_element(g)["classifications"]] == \
        [c["classificationName"] for c in real["classifications"]]               # the captured Memento shape


def test_the_old_fake_shape_an_archived_element_keeping_its_name_is_refused():
    fake = FakeEgeria(SOURCE)
    g = fake.add_element(SQN, "DeployedDatabaseSchema")
    fake.elements[g]["archived"] = True                 # what the fake used to do; Egeria never answers this
    with pytest.raises(AssertionError, match="old fake shape"):
        fake.read_element(SQN, for_lineage=True)


# ── the route carries the confirmation ────────────────────────────────────────────────────────────────────────────

def test_the_commit_route_passes_the_confirmation_and_refuses_a_stale_one(client, world, fake, registry):
    archived = _archived(world, fake)
    r = client.post("/api/catalogue-scope/db/commit", headers=as_user(ME),
                    json={"confirm_reinclude": [{"node": "schema", "name": "archive", "archived_guid": "not-it"}]})
    assert r.status_code == 409 and r.json()["detail"] == cc.SIBLING_CHANGED
    r = client.post("/api/catalogue-scope/db/commit", headers=as_user(ME),
                    json={"confirm_reinclude": [{"node": "schema", "name": "archive", "archived_guid": archived}]})
    assert r.status_code == 200, r.text
    assert r.json()["curation"]["selection"]["reinclude"][0]["archived_guid"] == archived
    assert client.post("/api/catalogue-scope/db/commit", headers=as_user(ME),
                       json={"confirm_reinclude": [{"node": "schema", "name": "archive"}]}).status_code == 422


# ── review round 1 ────────────────────────────────────────────────────────────────────────────────────────────────

def _memento_named(qn: str, guid: str = "ad2724e6-40e9-46a9-aaff-e3352c6b04bb", archive_ms: int | None = None) -> dict:
    """The captured 6.2 Memento, renamed to `qn` (and, given, archived at `archive_ms`)."""
    el = captured("metadata_expert_find_with_string_for_lineage__memento")[0]
    el["elementGUID"] = guid
    el["elementProperties"]["propertiesAsStrings"]["qualifiedName"] = qn
    if archive_ms is not None:
        m = next(c for c in el["classifications"] if c["classificationName"] == "Memento")
        m["classificationProperties"]["propertiesAsStrings"]["archiveDate"] = str(archive_ms)
    return el


def _surveyor_over(answers):
    """The real EgeriaDatabaseSurveyor with its Egeria clients faked at the boundary: no database or server by
    name, and the archived-sibling find answering `answers` (pages, or an exception)."""
    from unittest.mock import MagicMock

    from pyegeria import MetadataExpert

    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor
    s = EgeriaDatabaseSurveyor(platform_url="https://localhost:9443", view_server="qs-view-server",
                               user_id="u", user_password="p")
    s._automated_curation = MagicMock()
    s._automated_curation.get_guid_for_name.return_value = "No elements found"
    s._automated_curation.create_elem_from_template.side_effect = ["new-server", "new-db"]
    s._asset_maker = MagicMock()
    s._discovery = MagicMock()
    s._metadata_expert = create_autospec(MetadataExpert, spec_set=True, instance=True)
    s._metadata_expert.find_metadata_elements_with_string.side_effect = answers
    s.connect = lambda: None
    s.saved_secrets = []
    s._save_database_secret = lambda *a: (s.saved_secrets.append(a) or ("", ""))
    s._warn_if_database_has_no_connection = lambda *a, **k: None
    return s


def _db_entity():
    from resource_explorer.registry import DatabaseEntity
    return DatabaseEntity(slug="shop", display_name="Shop", db_type="postgresql", host="localhost",
                          egeria_host="host.docker.internal", port=5442, database_name="shop")


ARCHIVED_DB = _memento_named(f"{DBQN}_archivedOn_Fri Oct 09 01:28:08 GMT 2026")


def test_a_survey_of_an_archived_database_creates_nothing_and_says_it_needs_confirmation():
    from resource_explorer.surveyors.database.egeria_database_surveyor import ArchivedDatabaseNeedsConfirmation
    s = _surveyor_over([[ARCHIVED_DB], "No elements found"])
    with pytest.raises(ArchivedDatabaseNeedsConfirmation) as err:
        s.publish_local_survey(_db_entity(), schema_info={}, schema_count=0, table_count=0, column_count=0,
                               surveyed_at="2026-10-14T10:00:00")
    assert err.value.state == cc.NEEDS_CONFIRMATION and err.value.archived_guid == ARCHIVED_DB["elementGUID"]
    assert str(err.value) == ("the database is archived in Egeria · 2026-10-09 · re-including creates a new element; "
                              "the archived record stays · confirm Re-include in Curate › Contents · nothing created")
    s._automated_curation.create_elem_from_template.assert_not_called()
    assert s.saved_secrets == []                                            # nothing written before the check either


def test_the_hybrid_survey_outcome_says_needs_confirmation_and_the_local_result_stands(monkeypatch):
    from resource_explorer.surveyors.database.hybrid_database_surveyor import HybridDatabaseSurveyor
    s = _surveyor_over([[ARCHIVED_DB], "No elements found"])
    h = HybridDatabaseSurveyor.__new__(HybridDatabaseSurveyor)
    h.registry = None
    monkeypatch.setattr(h, "_get_egeria_surveyor", lambda: s, raising=False)
    monkeypatch.setattr(h, "_run_custom_survey", lambda e, c: {"database_slug": "shop", "schema_info": {}}, raising=False)
    out = h._run_egeria_survey(_db_entity(), None, {"user": "u", "password": "p"})
    assert out["egeria_status"] == "needs_confirmation" and out["needs_confirmation"]["node"] == "database"
    assert "re-including creates a new element" in out["message"] and out["schema_info"] == {}
    s._automated_curation.create_elem_from_template.assert_not_called()


def test_a_survey_whose_sibling_lookup_cannot_be_made_creates_nothing():
    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyorError
    s = _surveyor_over(TimeoutError("read timed out"))
    with pytest.raises(EgeriaDatabaseSurveyorError, match="could not check whether an archived copy exists"):
        s._catalog_and_survey(_db_entity(), "u", "p", survey_after_catalog=False)
    s._automated_curation.create_elem_from_template.assert_not_called()


def test_the_commit_s_confirmed_guid_lets_the_surveyor_create_and_a_stale_one_does_not():
    from resource_explorer.surveyors.database.egeria_database_surveyor import ArchivedDatabaseNeedsConfirmation
    s = _surveyor_over([[ARCHIVED_DB], "No elements found"])
    with pytest.raises(ArchivedDatabaseNeedsConfirmation) as err:
        s._catalog_and_survey(_db_entity(), "u", "p", survey_after_catalog=False, reinclude_guid="older-copy")
    assert err.value.state == "changed"
    s = _surveyor_over([[ARCHIVED_DB], "No elements found"])
    out = s._catalog_and_survey(_db_entity(), "u", "p", survey_after_catalog=False,
                                reinclude_guid=ARCHIVED_DB["elementGUID"])
    assert out["database_guid"] == "new-db"


@pytest.mark.parametrize("n", [1, 4])
def test_the_preview_reads_every_schema_in_one_find_however_many_are_chosen(world, fake, n):
    names = ["sales", "archive", "aXb", "cm,ma"][:n]          # no wildcard collision among them
    for name in names:
        choose(world, name, "catalogue")
    before = len(fake.calls)
    cc.build_preview(world["registry"], "db", view(world), fake)
    mine = [c for c in fake.calls[before:] if c[0] in ("schema_elements", "archived_siblings")]
    # one paged schema find for every schema, and the database's own sibling read: never one per schema
    assert [c[0] for c in mine].count("schema_elements") == 1
    assert len(mine) <= 2


def test_the_one_find_is_paged_past_filtered_pages_to_the_end():
    page = [_memento_named(f"{SQN}_archivedOn_Fri Oct 09 01:28:08 GMT 2026")]
    g, client = _gateway_with(None)
    client.find_metadata_elements_with_string.side_effect = [page, "No elements returned", page[:0] or
                                                             [_memento_named(SQN.replace("archive", "sales"), "g-sales")],
                                                             "No elements found"]
    got = g.schema_elements("PostgreSQL Relational Database Schema::host.docker.internal:5442::shop.")
    assert sorted(e.qualified_name for e in got)[0].endswith("shop.archive_archivedOn_Fri Oct 09 01:28:08 GMT 2026")
    bodies = [c.kwargs["body"] for c in client.find_metadata_elements_with_string.call_args_list]
    assert [b["startFrom"] for b in bodies] == [0, 500, 1000, 1500]
    assert {b["metadataElementTypeName"] for b in bodies} == {"DeployedDatabaseSchema"} and all(b["forLineage"] for b in bodies)
    client.find_metadata_elements_with_string.side_effect = ["No elements returned"] * 12
    with pytest.raises(gw.SiblingLookupFailed, match="pages in a row came back empty"):
        g.schema_elements("x")


def test_a_schema_refused_at_run_time_is_not_in_the_survey_scope(world, fake):
    archived = _archived(world, fake)
    choose(world, "sales", "catalogue")
    out = cc.start_commit(world["registry"], "db", ME, gateway=fake,
                          confirm_reinclude=[{"node": "schema", "name": "archive", "archived_guid": archived}])
    again = fake.add_element(SQN, "DeployedDatabaseSchema", parent=fake.db_guid)   # a newer copy before the run
    fake.archive_clock += timedelta(days=2)
    fake.archive(again)
    n = len(fake.surveys)
    cc.execute_commit(world["registry"], out["curation"]["id"], gateway=fake)
    assert fake.surveys[n:][0][1] == {"includeSchemaNames": "sales"}
    held = [p for p in world["registry"].list_catalogue_commit_proofs("db") if p["proof"] == cc.P_REINCLUDE_HELD]
    assert [(p["schema_name"], p["detail"]["state"]) for p in held] == [("archive", "changed")]


@pytest.mark.parametrize("suffix,day", [("Fri Oct 09 01:28:08 GMT 2026", "2026-10-09"),
                                        ("Fri Oct 09 02:28:08 BST 2026", "2026-10-09"),
                                        ("Thu Oct 08 18:28:08 PDT 2026", "2026-10-08"),
                                        ("Fri Oct 09 03:28:08 CEST 2026", "2026-10-09"),
                                        ("Fri Oct 09 01:28:08 GMT+01:00 2026", "2026-10-09")])
def test_the_day_is_read_from_a_java_date_in_any_zone(suffix, day):
    assert gw.java_date_day(suffix) == day


def test_an_unparseable_suffix_is_still_a_sibling_and_the_order_is_egeria_s_archive_time():
    newer_by_suffix = _memento_named(f"{SQN}_archivedOn_Sat Dec 31 23:59:59 GMT 2099", "g-old", archive_ms=1_000_000_000_000)
    odd = _memento_named(f"{SQN}_archivedOn_2026.10.09 at 01:28", "g-new", archive_ms=1_791_509_287_233)
    sibs = gw.parse_archived_siblings([newer_by_suffix, odd], SQN)
    assert [s.guid for s in sibs] == ["g-new", "g-old"]                    # by archiveDate, not by the suffix
    assert sibs[0].date == "2026.10.09 at 01:28" and sibs[0].archived       # still a sibling, its text shown
    assert gw.decide_reinclude(None, sibs)["state"] == cc.NEEDS_CONFIRMATION
