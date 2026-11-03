"""Brief Z2 round 3 (2026-10-11): the Opus round-2 findings, each pinned by a test that fails on d1384832.

(1) a name-collision 409 makes no Egeria read is pinned by test_catalogue_commit.py itself (unchanged, red on
d1384832). Here: (2) the database element publish_database RETURNS is judged when it is not the one step 0
judged; (3) a NEW anchored member is judged on its anchor (validateUserForAnchorMemberCreate); (4) "own anchor"
is decided against the requested GUID; (5) resync and guard_linkage keep a refusal a refusal; (6) the leave-out
preview walks members as the run does; (7) an old documentation-source row falls back to its run's requester.

No Egeria: access reads faked at the client boundary (`tests/zone_fakes.py`), the catalogue at the gateway.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fake_egeria_catalogue import FakeEgeria as FakeCatalogue  # noqa: E402

from resource_explorer import catalogue_commit as cc  # noqa: E402
from resource_explorer import catalogue_scope as cs  # noqa: E402
from resource_explorer.catalogue_gateway import PublishedDatabase  # noqa: E402
from resource_explorer.registry import DatabaseEntity, Project, ProjectRegistry  # noqa: E402
from tests.zone_fakes import element as el  # noqa: E402
from tests.zone_fakes import install  # noqa: E402

ME = "test-caller"
SECURED = "finance-zone"
SOURCE = {"sales": {"orders": ["id"]}, "archive": {"old": ["id"]}}


def _secure(fake, guid, control, zones=(SECURED,), anchor=""):
    fake.elements[guid] = el(guid, list(zones), anchor=anchor)
    fake.controls[SECURED] = {"associatedSecurityList": control}


@pytest.fixture
def access(monkeypatch):
    monkeypatch.delenv("EXPLORER_PUBLISH_ZONES", raising=False)
    monkeypatch.delenv("RE_ISSUE_117_BLOCK_OFF", raising=False)
    return install(monkeypatch)


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "r3.db"))
    assert str(tmp_path) in r.database_url                                    # never the shared registry
    r.add(Project(slug="p", display_name="P", github_url="https://github.com/x/p", description=""))
    r.set_egeria_asset_guid("p", "g-p")
    return r


def _tbl(name):
    return {"name": name, "table_type": "BASE TABLE", "row_count": 1, "row_count_state": "measured",
            "size_bytes": 1, "column_count": 1, "columns": [{"name": "id", "type": "integer", "nullable": False,
                                                             "key_role": "PK", "foreign_key": None, "comment": ""}]}


@pytest.fixture
def world(tmp_path, monkeypatch, access, signed_in_caller):
    r = ProjectRegistry(db_path=str(tmp_path / "commit.db"))
    r.register_database(DatabaseEntity(slug="db", display_name="Shop", db_type="postgresql", host="localhost",
                                       egeria_host="host.docker.internal", port=5442, database_name="shop"))
    tree = {"schemas": [{"schema": n, "table_count": 1, "row_total": 1, "bytes_total": None, "is_estimate": False,
                         "classification": "data", "reason": "", "tables": [_tbl(t) for t in tabs]}
                        for n, tabs in SOURCE.items()]}
    monkeypatch.setattr(cs, "_load_tree", lambda reg, slug: tree)
    monkeypatch.setattr(cs, "_tree_survey_at", lambda reg, slug: "2026-10-02T09:00:00")
    monkeypatch.setattr(cs, "_activity_counters", lambda reg, slug: {})
    monkeypatch.setattr(cs, "_row_sources", lambda reg, slug: {})
    return {"registry": r, "fake": FakeCatalogue(SOURCE), "access": access}


def _choose(w, schema, choice):
    cs.set_node_choice(w["registry"], "db", ME, schema=schema, table="", choice=choice)


def _press(w):
    out = cc.start_commit(w["registry"], "db", ME, gateway=w["fake"])
    return out, cc.execute_commit(w["registry"], out["curation"]["id"], gateway=w["fake"])


def _step(rec, name):
    return next(s for s in rec["steps"] if s["name"] == name)


# ── (2) the database element publish_database hands back is judged ─────────────────────────────────────

def test_a_database_element_other_than_the_one_judged_is_judged_before_anything_hangs_off_it(world, monkeypatch):
    _choose(world, "sales", "catalogue")
    fake = world["fake"]
    real = fake.publish_database

    def adopts_another(db_entity, *a, registry=None, **k):   # the publish hands back another element (a race)
        pub = real(db_entity, *a, **k)
        if registry is not None:                                # and records it, as the real surveyor does
            registry.set_database_egeria_guid(db_entity.slug, "g-elsewhere")
        return PublishedDatabase(pub.server_guid, "g-elsewhere", pub.server_name, pub.database_qualified_name)
    monkeypatch.setattr(fake, "publish_database", adopts_another)
    _secure(world["access"], "g-elsewhere", {"DEFAULT": ["dbas"]})
    out, rec = _press(world)
    first = _step(rec, "publish_elements")
    assert first["state"] == "failed" and first["detail"].startswith(f"not permitted · zone {SECURED}")
    after = [c[0] for c in fake.calls[[c[0] for c in fake.calls].index("publish_database"):]]
    for write in ("initiate_catalog_action", "add_catalog_target", "set_owner", "initiate_survey",
                  "publish_local_report", "mark_on_behalf", "create_schema_element"):
        assert write not in after, write
    assert world["registry"].get_database("db").egeria_asset_guid in ("", None)      # never recorded


# ── (3) a NEW anchored member is judged on its anchor ──────────────────────────────────────────────────

class TestNewMember:
    def test_create_is_judged_on_the_anchors_zones(self, registry, access, signed_in_caller):
        from resource_explorer.workflows.curate import curation_access

        _secure(access, "g-db", {"DEFAULT": [ME], "CREATE": ["dbas"]})
        d = curation_access(registry, "database", "x", "", element_guid="", anchor_guid="g-db",
                            operations=("CREATE",))
        assert d.reason == f"zone {SECURED} does not grant {ME} create in Egeria"

    def test_an_anchor_with_no_zones_is_open_whatever_re_configures(self, registry, monkeypatch, signed_in_caller):
        from resource_explorer.workflows.curate import curation_access

        fake = install(monkeypatch, zones=[SECURED])
        fake.controls[SECURED] = {"associatedSecurityList": {"DEFAULT": ["dbas"]}}
        assert curation_access(registry, "repo", "p", "", element_guid="", anchor_guid="g-plain",
                               operations=("CREATE",)).allowed

    def test_a_new_schema_under_a_database_that_refuses_create_is_skipped_and_the_rest_written(self, world):
        _choose(world, "sales", "catalogue")
        _press(world)                                                      # sales is in Egeria now
        db_guid = world["registry"].get_database("db").egeria_asset_guid
        _secure(world["access"], db_guid, {"DEFAULT": [ME], "CREATE": ["dbas"]})
        _choose(world, "archive", "catalogue")                              # a new schema: CREATE under the db
        out, rec = _press(world)
        assert out["preview"]["attach"] == ["sales"]
        assert [r["schema"] for r in out["preview"]["not_permitted"]] == ["archive"]
        assert out["preview"]["not_permitted"][0]["reason"] == f"zone {SECURED} does not grant {ME} create in Egeria"


# ── (4) "own anchor" is decided against the GUID that was asked for ────────────────────────────────────

class TestOwnAnchor:
    def test_an_answer_naming_itself_is_not_the_requested_elements_own_anchor(self):
        from resource_explorer.zone_access import element_facts

        answer = el("g-other", anchor="g-other")            # Egeria answered with ANOTHER element
        assert element_facts(answer, "g-asked").anchor == "g-other"

    def test_an_anchor_with_nothing_to_compare_is_unreadable(self):
        from resource_explorer.zone_access import AccessUnreadable, element_facts

        with pytest.raises(AccessUnreadable):
            element_facts(el("g-x", anchor="g-x"), "")

    def test_an_anchors_classification_with_no_anchor_guid_is_its_own_anchor(self):
        """The LIVE shape (egeria_git's Asset, 2026-10-12): Anchors carries anchorTypeName/anchorDomainName and no
        anchorGUID. Egeria reads a null anchorGUID as "its own anchor" (OpenMetadataAPIGenericHandler ~:1736), so its
        own zones decide; refusing it as unreadable blocked every Publish on such an asset."""
        from resource_explorer.zone_access import element_facts

        live = {"elementGUID": "g-asset", "type": {"typeName": "Asset"},
                "classifications": [{"classificationName": "Anchors", "classificationProperties": {"propertyValueMap": {
                    "anchorTypeName": {"primitiveValue": "Asset"},
                    "anchorDomainName": {"primitiveValue": "Asset"}}}}]}
        assert element_facts(live, "g-asset").anchor == ""
        assert element_facts(live, "").anchor == ""          # nothing to compare is needed when there is no anchor

    def test_a_present_but_unreadable_anchor_guid_still_denies(self):
        from resource_explorer.zone_access import AccessUnreadable, element_facts

        odd = {"elementGUID": "g-x", "classifications": [{"classificationName": "Anchors", "classificationProperties": {
            "propertyValueMap": {"anchorGUID": {"primitiveValue": 42}}}}]}
        with pytest.raises(AccessUnreadable):
            element_facts(odd, "g-x")

    @pytest.mark.parametrize("cp", [
        {"anchorGUID": {"primitiveValue": "g-other"}},            # flat key, not a string: names ANOTHER element
        {"anchorGUID": 42},
        {"instanceProperties": {"anchorGUID": {"primitiveValue": "g-other"}}},   # a shape RE does not read
    ])
    def test_a_present_or_unrecognised_anchor_never_reads_as_its_own(self, cp):
        from resource_explorer.zone_access import AccessUnreadable, element_facts

        el_ = {"elementGUID": "g-x", "classifications": [{"classificationName": "Anchors", "classificationProperties": cp}]}
        with pytest.raises(AccessUnreadable):
            element_facts(el_, "g-x")

    @pytest.mark.parametrize("cp", [{}, None, {"anchorGUID": None}, {"anchorTypeName": "Asset", "anchorDomainName": "Asset"}])
    def test_shapes_with_no_anchor_guid_read_as_its_own_anchor(self, cp):
        from resource_explorer.zone_access import element_facts

        el_ = {"elementGUID": "g-x", "classifications": [{"classificationName": "Anchors", "classificationProperties": cp}]}
        assert element_facts(el_, "g-x").anchor == ""

    def test_the_batched_reader_passes_the_requested_guid(self, monkeypatch):
        from resource_explorer import zone_access as za

        client = MagicMock()
        client.get_metadata_element_by_guid.return_value = el("g-elsewhere", anchor="g-elsewhere")
        monkeypatch.setattr(za, "_metadata_expert", lambda: client)
        assert za._read_elements_on_one_client(["g-asked"])["g-asked"].anchor == "g-elsewhere"


# ── (5) a refusal stays a refusal ──────────────────────────────────────────────────────────────────────

def test_resync_reports_an_adopted_asset_refusal_as_not_permitted(registry, monkeypatch):
    from resource_explorer import egeria_resync as er
    from resource_explorer.workflows.curate import WriteNotPermitted

    r = er.EgeriaResync(registry=registry)
    r._refusal = lambda et, slug, write="publish": ""
    monkeypatch.setattr(registry, "get_project_context", lambda et, s: {"status": "linked"})
    monkeypatch.setattr("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator.run",
                        lambda self, slug, steps=None: MagicMock(annotations=[]))

    class Refusing:
        def __init__(self, registry=None):
            pass

        def publish(self, result, zone_names=None, *, defer_drain=None):     # the real signature (checked below)
            raise WriteNotPermitted(f"not permitted · zone {SECURED} does not grant {ME} update in Egeria")

    import inspect

    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

    shape = lambda f: [(p.name, p.kind, p.default) for p in inspect.signature(f).parameters.values()]  # noqa: E731
    assert shape(Refusing.publish) == shape(EgeriaPublisher.publish)
    monkeypatch.setattr("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher", Refusing)
    logged = []
    monkeypatch.setattr(r, "_log_publish_failure", lambda slug, error: logged.append(slug))
    out = r._publish_many(["p"], None)
    assert out["not_permitted"] == [{"slug": "p", "words": f"not permitted · zone {SECURED} does not grant {ME} "
                                                            "update in Egeria"}]
    assert out["failed"] == 0 and out["errors"] == [] and logged == []


def test_guard_linkage_never_turns_a_refusal_into_a_stale_link(registry, monkeypatch):
    from resource_explorer import egeria_linkage as el_mod
    from resource_explorer.workflows.curate import WriteNotPermitted

    monkeypatch.setattr(el_mod, "is_unknown_guid_error", lambda exc: True)     # even if its words looked like one
    with pytest.raises(WriteNotPermitted):
        with el_mod.guard_linkage(registry, "repo", "p", "P", "g-p"):
            raise WriteNotPermitted("not permitted · element g-p not found in zone x")
    assert not registry.get_egeria_linkage("repo", "p")


# ── (6) the leave-out preview walks members as the run does ────────────────────────────────────────────

def test_the_leave_out_preview_checks_members_as_the_run_does(world, monkeypatch):
    _choose(world, "sales", "catalogue")
    _press(world)
    _choose(world, "sales", "leave_out")
    monkeypatch.setenv("RE_ISSUE_117_BLOCK_OFF", "test/2026-10-11")
    seen = []
    real = cc.parts_check_by_name

    def spy(*a, **k):
        seen.append((tuple(a[4]), a[5], k.get("members", False)))
        return real(*a, **k)
    monkeypatch.setattr(cc, "parts_check_by_name", spy)
    cc.build_preview(world["registry"], "db", cs.build_scope_view(world["registry"], "db"), world["fake"])
    assert (("sales",), cc.LEAVE_OUT_OPS, True) in seen


# ── (7) an old documentation-source row falls back to its run's requester ─────────────────────────────

def test_a_doc_source_row_without_a_requester_is_checked_as_its_runs(registry, access, monkeypatch):
    from resource_explorer import egeria_outbox as eo

    _secure(access, "g-p", {"DEFAULT": ["dbas"]})
    run_id = registry.enqueue_run("analysis_run", {"slug": "p"}, requested_by="dana")
    row_id = registry.enqueue_outbox_element(            # as rows were queued before round 2: no `by`
        "repo", "p", "doc_source_publish", "ExternalReference::https://docs.example/x",
        {"entity_type": "repo", "entity_slug": "p", "source_id": "s1"}, run_id=run_id)
    applied = []
    monkeypatch.setattr(eo, "apply_element", lambda *a, **k: applied.append(a) or "g")
    eo.drain_outbox(registry, MagicMock(), lambda qn: "", element_id=row_id, identity=MagicMock(kind="daemon"))
    row = next(r for r in registry.list_outbox_elements(limit=10) if r["id"] == row_id)
    assert applied == [] and row["last_error"] == f"not permitted · zone {SECURED} does not grant dana attach in Egeria"


# ── round 4: judged BEFORE the write when it can be; any check failure refuses; the pointer is put back ──

def test_a_database_found_before_the_publish_is_judged_before_publish_database(world, monkeypatch):
    _choose(world, "sales", "catalogue")
    fake = world["fake"]
    real_read = fake.read_element
    db_qn = "PostgreSQL Relational Database::host.docker.internal:5442::shop"
    reads = {"n": 0}

    def racing_read(qn, *a, **k):                    # the run's step 0 finds none; by step 1 another process made it
        if qn == db_qn and reads["n"] is not None:
            reads["n"] += 1
            if reads["n"] > 1:
                return type("E", (), {"guid": "g-raced", "archived": False})()
        return real_read(qn, *a, **k)
    monkeypatch.setattr(fake, "read_element", racing_read)
    _secure(world["access"], "g-raced", {"DEFAULT": ["dbas"]})
    reads["n"] = None                                 # the press reads normally
    out = cc.start_commit(world["registry"], "db", ME, gateway=fake)
    reads["n"] = 0                                    # the run: step 0's read, then step 1's
    rec = cc.execute_commit(world["registry"], out["curation"]["id"], gateway=fake)
    first = _step(rec, "publish_elements")
    assert first["state"] == "failed" and "found before the publish" in first["detail"]
    assert "publish_database" not in [c[0] for c in fake.calls]


def test_a_check_that_fails_on_the_returned_element_refuses_and_leaves_no_pointer(world, monkeypatch):
    from resource_explorer.workflows import curate as wc

    _choose(world, "sales", "catalogue")
    fake = world["fake"]
    real = fake.publish_database

    def adopts_another(db_entity, *a, registry=None, **k):
        pub = real(db_entity, *a, **k)
        if registry is not None:
            registry.set_database_egeria_guid(db_entity.slug, "g-elsewhere")
        return PublishedDatabase(pub.server_guid, "g-elsewhere", pub.server_name, pub.database_qualified_name)
    monkeypatch.setattr(fake, "publish_database", adopts_another)
    real_access = wc.resource_access

    def boom(*a, element_guid=None, **k):
        if element_guid == "g-elsewhere":
            raise RuntimeError("registry went away")
        return real_access(*a, element_guid=element_guid, **k)
    monkeypatch.setattr(wc, "resource_access", boom)
    out, rec = _press(world)
    first = _step(rec, "publish_elements")
    assert first["state"] == "failed" and "could not check access (RuntimeError: registry went away)" in first["detail"]
    assert _step(rec, "owner")["state"] == "skipped" and "set_owner" not in [c[0] for c in fake.calls]
    assert world["registry"].get_database("db").egeria_asset_guid in ("", None)
