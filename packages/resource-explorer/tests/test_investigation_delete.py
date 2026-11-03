"""Deleting an investigation (owner ruling 2026-10-21): who may, what each choice touches, what a refusal leaves.

A temp SQLite registry and a fake Egeria whose methods are `create_autospec(..., spec_set=True)` mocks of the real
pyegeria classes, so a call with a signature the real client does not have fails here. No live call is made.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import create_autospec

import pytest
from fastapi import HTTPException
from pyegeria import CollectionManager, MetadataExpert, ProjectManager

from resource_explorer.a2a_auth import CallerIdentity, current_caller
from resource_explorer.investigation_delete import (
    CHOICE_RE_AND_EGERIA,
    CHOICE_RE_ONLY,
    EgeriaHandles,
    InvestigationDeleter,
    delete_rights,
)
from resource_explorer.registry import ProjectRegistry

OWNER, OTHER, ADMIN = "owen", "olive", "ada"
SLUG = "customer-360"
PROJECT = "proj-guid"
LOG = "log-guid"
NOTE1, NOTE2 = "note-1", "note-2"
FOLIO = "folio-guid"
DISP = "disp-guid"
ASSET = "member-asset-guid"        # a member's own repo/database asset: must never be touched
RATING, COMMENT = "rating-guid", "comment-guid"     # anchored to the Project; made by other people
NO_ELEMENT = "No element found"
NO_ELEMENTS = "No elements found"


def _api_exc(cls_name="PyegeriaAPIException", code=500, msg_id=""):
    """A real-shaped Egeria failure: the class pyegeria raises, the HTTP code, and the message id Egeria's FFDC
    body carries (the shape observed live 2026-10-16). Live Egeria RAISES PyegeriaNotFoundException for a deleted
    GUID; it never returns the "No element found" string."""
    import pyegeria.core._exceptions as ex
    e = Exception.__new__(getattr(ex, cls_name))
    Exception.__init__(e, "Egeria error")
    e.related_http_code = code
    e.context, e.pyegeria_code, e.message, e.system_action, e.user_action = {}, "CLIENT-ERROR", "Egeria refused", "", ""
    e.response = None
    e.additional_info = {"exceptionErrorMessageId": msg_id} if msg_id else {}
    return e


def _not_found():
    return _api_exc("PyegeriaNotFoundException", 404)


NOT_VISIBLE = "OMAG-REPOSITORY-HANDLER-400-010"


def _identity(user: str, role: str = "user") -> CallerIdentity:
    return CallerIdentity(user_id=user, egeria_token="tok", auth_source="app-jwt", role=role)


@pytest.fixture
def registry(tmp_path):
    reg = ProjectRegistry(database_url=f"sqlite:///{tmp_path / 'r.db'}")
    reg._init_schema()
    return reg


@pytest.fixture
def as_caller(monkeypatch):
    """Sign in as `who`; the route reads `a2a_auth.caller()`, the registry `current_user_id()`."""
    tokens = []

    def _as(user: str, role: str = "user"):
        tokens.append(current_caller.set(_identity(user, role)))
    yield _as
    for t in reversed(tokens):
        current_caller.reset(t)


class World:
    """The Egeria elements that exist, and what was called, in order. Reads of a deleted GUID RAISE
    `PyegeriaNotFoundException`, as live Egeria does. An archived element is a Memento: it is readable only with
    `forLineage` (as live), carries its operation id, and has its qualifiedName renamed `<qn>_archivedOn_<date>`."""

    def __init__(self):
        self.exist = {PROJECT, LOG, NOTE1, NOTE2, FOLIO, DISP, ASSET}
        self.names = {LOG: ("NoteLog::RE::" + PROJECT), FOLIO: f"Folio::Investigation::{SLUG}",
                      DISP: f"WorkingSet::Investigation::{SLUG}::tracking",
                      PROJECT: f"Project::Investigation::{SLUG}"}
        self.members = {FOLIO: [ASSET], DISP: []}      # a Collection's members
        # guid -> type, ANCHORED to the Project: the Folio is (promote gives it anchorGUID), the disposition set is not
        self.anchored = {FOLIO: "Collection", LOG: "NoteLog", NOTE1: "Note", NOTE2: "Note"}
        self.anchored_meta: dict[str, dict] = {}                          # guid -> {name, author, created}
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[tuple[str, str, object]] = []                   # (method, guid, body) of every delete call
        self.refuse: dict[str, Exception] = {}
        self.stuck: set[str] = set()          # a call that is sent but the element stays live / the member stays listed
        self.extra_logs: list[dict] = []     # NoteLogs RE did not make
        self.archived: dict[str, str] = {}   # guid -> operation id
        self.hard_deleted: list[str] = []    # anything removed other than by an archive: must stay empty
        self.read_error: "Exception | None" = None   # every by-GUID read raises this
        self._ops = 0

    def add_anchored(self, guid, type_name, name="", author="", created=1759000000000):
        self.exist.add(guid)
        self.anchored[guid] = type_name
        self.anchored_meta[guid] = {"name": name, "author": author, "created": created}

    def _archive(self, guids):
        self._ops += 1
        for g in guids:
            if g in self.exist and g not in self.stuck:
                self.archived[g] = f"op-{self._ops}"
                self.exist.discard(g)

    def _hard_delete(self, guids):
        for g in guids:
            self.hard_deleted.append(g)
            self.exist.discard(g)

    def _delete(self, kind, guid, body, taken):
        self.calls.append((kind, guid))
        self.bodies.append((kind, guid, body))
        if guid in self.refuse:
            raise self.refuse[guid]
        if (body or {}).get("deleteMethod") == "ARCHIVE":
            self._archive([guid] + [g for g in taken if g in self.exist])
        else:
            self._hard_delete([guid] + taken)

    def element(self, guid, with_memento=False):
        versions = {"createdBy": self.anchored_meta.get(guid, {}).get("author", ""),
                    "createTime": self.anchored_meta.get(guid, {}).get("created", 1759000000000)}
        qn = self.names.get(guid, "")
        header = {"guid": guid, "versions": versions}
        if with_memento:
            versions["lastOperationId"] = self.archived[guid]
            versions["updateTime"] = 1760900000000                      # 2025-10-19
            header["classifications"] = [{"classificationName": "Memento"}]
            qn = f"{qn}_archivedOn_Sun Oct 19 19:33:20 GMT 2025" if qn else qn
        return {"elementHeader": header, "properties": {"qualifiedName": qn}}

    def pm(self):
        m = create_autospec(ProjectManager, instance=True, spec_set=True)

        def logs(element_guid, *a, **k):
            self.calls.append(("get_attached_note_logs", element_guid))
            out = ([_el(LOG, self.names[LOG], "Notes on x")] if LOG in self.exist else []) + self.extra_logs
            return out or NO_ELEMENTS

        def notes(note_log_guid, *a, **k):
            self.calls.append(("get_notes_for_note_log", note_log_guid))
            out = [_el(n, f"note::{n}", n) for n in (NOTE1, NOTE2) if n in self.exist]
            return out or NO_ELEMENTS

        def delete_project(project_guid, cascade=False, body=None):
            self._delete("delete_project", project_guid, body, list(self.anchored))

        m.get_attached_note_logs.side_effect = logs
        m.get_notes_for_note_log.side_effect = notes
        m.delete_project.side_effect = delete_project
        return m

    def cm(self):
        m = create_autospec(CollectionManager, instance=True, spec_set=True)

        def delete_collection(collection_guid, body=None, cascade=False):
            if (body or {}).get("deleteMethod") != "ARCHIVE" and self.members.get(collection_guid):
                self.calls.append(("delete_collection", collection_guid))
                raise RuntimeError("OMAG-GENERIC-HANDLERS-400-001 the Collection still has member elements")
            self._delete("delete_collection", collection_guid, body, [])

        def get(guid, *a, **k):
            self.calls.append(("get_collection_by_guid", guid))
            if guid not in self.exist:
                raise _not_found()
            return {"elementHeader": {"guid": guid}, "properties": {"qualifiedName": self.names.get(guid, "")}}

        def members(collection_guid=None, *a, **k):
            self.calls.append(("get_collection_members", collection_guid))
            out = [_el(g, "", g) for g in self.members.get(collection_guid, [])]
            return out or NO_ELEMENTS

        def unlink(collection_guid, element_guid, *a, **k):
            self.calls.append(("remove_from_collection", f"{collection_guid}:{element_guid}"))
            if f"{collection_guid}:{element_guid}" in self.refuse:
                raise self.refuse[f"{collection_guid}:{element_guid}"]
            if element_guid not in self.stuck:
                self.members[collection_guid] = [g for g in self.members.get(collection_guid, []) if g != element_guid]

        m.delete_collection.side_effect = delete_collection
        m.get_collection_by_guid.side_effect = get
        m.get_collection_members.side_effect = members
        m.remove_from_collection.side_effect = unlink
        return m

    def me(self):
        m = create_autospec(MetadataExpert, instance=True, spec_set=True)

        def get(guid=None, as_of_time=None, graph_query_depth=3, output_format="JSON", body=None, **k):
            self.calls.append(("get_metadata_element_by_guid", guid))
            if self.read_error is not None:
                raise self.read_error
            lineage = bool((body or {}).get("forLineage"))
            if guid in self.archived:
                if not lineage:
                    raise _not_found()          # a Memento is visible only with forLineage
                return self.element(guid, with_memento=True)
            if guid not in self.exist:
                raise _not_found()
            return self.element(guid)

        def graph(guid=None, *a, **k):
            self.calls.append(("get_anchored_element_graph", guid))
            els = []
            for g, t in self.anchored.items():
                if g in self.exist:
                    meta = self.anchored_meta.get(g, {})
                    el = self.element(g)
                    el["elementHeader"]["type"] = {"typeName": t}
                    el["properties"]["displayName"] = meta.get("name") or g
                    els.append(el)
            return {"elementHeader": {"guid": guid}, "anchoredElements": els}

        m.get_metadata_element_by_guid.side_effect = get
        m.get_anchored_element_graph.side_effect = graph
        return m

    def handles(self) -> EgeriaHandles:
        return EgeriaHandles(pm=self.pm(), cm=self.cm(), me=self.me())

    def write_calls(self):
        return [c for c in self.calls if c[0] in ("delete_collection", "delete_project", "remove_from_collection")]


def _el(guid, qn, name):
    return {"elementHeader": {"guid": guid}, "properties": {"qualifiedName": qn, "displayName": name}}


def _deleter(registry, world, **kw):
    return InvestigationDeleter(registry, handles_factory=world.handles, sleep=lambda s: None, **kw)


@pytest.fixture
def promoted(registry, as_caller):
    """A promoted investigation owned by OWNER, with members, a disposition, journal entry and a pending outbox row."""
    as_caller(OWNER)
    inv = registry.create_investigation("Customer 360", project_classification="StudyProject")
    assert inv["slug"] == SLUG
    registry.set_investigation_egeria_project(SLUG, {
        "status": "linked", "egeria_project_guid": PROJECT,
        "egeria_project_qualified_name": f"Project::Investigation::{SLUG}"})
    folio = registry.get_or_create_folio(SLUG)
    registry.set_working_set_egeria_collection(folio["slug"], FOLIO)
    registry.add_working_set_member(folio["slug"], "repo", "crm-repo", membership_rationale="holds customers")
    registry.set_investigation_disposition(SLUG, "repo", "crm-repo", "tracking")
    disp = registry.get_or_create_disposition_set(SLUG, "tracking")
    registry.set_working_set_egeria_collection(disp["slug"], DISP)
    return SLUG


def _count(registry, table, where="1=1", params=()):
    with registry._conn() as conn:
        return conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", params).fetchone()["n"]  # noqa: S608


def _seed(registry, table, slug, **extra):
    """One row in `table` for the investigation, filling any other NOT NULL column with a neutral value."""
    with registry._conn() as conn:
        cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
        vals = {}
        for c in cols:
            name, typ = c["name"], (c["type"] or "").upper()
            if c["pk"] and "INT" in typ and c["name"] == "id":
                continue
            if name == "entity_type":
                vals[name] = "investigation"
            elif name == "entity_slug":
                vals[name] = slug
            elif c["notnull"] and c["dflt_value"] is None:
                vals[name] = 0 if ("INT" in typ or "REAL" in typ) else f"{table}-{name}"
        vals.update(extra)
        conn.execute(f"INSERT INTO {table} ({', '.join(vals)}) VALUES ({', '.join('?' * len(vals))})",
                     tuple(vals.values()))


# ── who may delete ─────────────────────────────────────────────────────────────────────────────────────

def test_owner_admin_and_nobody_else(promoted, registry):
    inv = registry.get_investigation(SLUG, user_id="")
    assert delete_rights(inv, _identity(OWNER)) == (True, "")
    assert delete_rights(inv, _identity(ADMIN, "admin")) == (True, "")
    assert delete_rights(inv, _identity(ADMIN, "curator")) == (True, "")
    ok, words = delete_rights(inv, _identity(OTHER))
    assert not ok and "creat" in words and "admin" in words
    ok, words = delete_rights(inv, None)
    assert not ok and "Sign in" in words
    anon = CallerIdentity(user_id="anonymous", egeria_token=None, auth_source="anonymous", role="anonymous")
    assert not delete_rights(inv, anon)[0]


def test_ownerless_is_an_admins_alone(registry):
    inv = {"slug": "x", "created_by": ""}
    assert not delete_rights(inv, _identity(OWNER))[0]
    assert "no recorded owner" in delete_rights(inv, _identity(OWNER))[1]
    assert delete_rights(inv, _identity(ADMIN, "admin"))[0]


def _route(slug, egeria, *, who, role="user", monkeypatch=None, deleter=None):
    """Call the route as `who`. The real deleter is replaced by one wired to the fake Egeria."""
    from resource_explorer.web.routes import investigations as routes

    token = current_caller.set(_identity(who, role))
    try:
        return asyncio.run(routes.delete_investigation(slug, egeria=egeria))
    finally:
        current_caller.reset(token)


@pytest.fixture
def routes_on(registry, monkeypatch):
    from resource_explorer.web.routes import investigations as routes

    world = World()
    monkeypatch.setattr(routes, "_registry", lambda: registry)
    monkeypatch.setattr("resource_explorer.investigation_delete.caller_handles", world.handles)
    monkeypatch.setattr("resource_explorer.investigation_delete.time.sleep", lambda s: None)
    return world


def test_route_owner_can_delete(promoted, registry, routes_on):
    out = _route(SLUG, False, who=OWNER)
    assert out["ok"] and out["local_deleted"] and routes_on.calls == []
    assert registry.get_investigation(SLUG, user_id="") is None


def test_route_admin_can_delete(promoted, registry, routes_on):
    assert _route(SLUG, False, who=ADMIN, role="admin")["local_deleted"]
    assert registry.get_investigation(SLUG, user_id="") is None


def test_route_anyone_else_gets_403_with_words_and_nothing_changes(promoted, registry, routes_on):
    with pytest.raises(HTTPException) as e:
        _route(SLUG, True, who=OTHER)
    assert e.value.status_code == 403 and "admin" in e.value.detail
    assert registry.get_investigation(SLUG, user_id="") is not None and routes_on.calls == []


def test_route_404_for_missing_and_for_not_visible(registry, as_caller, routes_on):
    with pytest.raises(HTTPException) as e:
        _route("nope", False, who=OWNER)
    assert e.value.status_code == 404
    as_caller(OWNER)
    registry.create_investigation("Secret", project_classification="PersonalProject")
    with pytest.raises(HTTPException) as e:
        _route("secret", False, who=OTHER)            # private to OWNER: the same 404 get_investigation gives
    assert e.value.status_code == 404


def test_route_egeria_true_on_unpromoted_is_400(registry, as_caller, routes_on):
    as_caller(OWNER)
    registry.create_investigation("Local only", egeria_binding="local")
    with pytest.raises(HTTPException) as e:
        _route("local-only", True, who=OWNER)
    assert e.value.status_code == 400 and registry.get_investigation("local-only", user_id="") is not None


def test_route_409_while_a_publish_is_being_written(promoted, registry, routes_on):
    from datetime import datetime
    with registry._conn() as conn:
        _seed(registry, "egeria_outbox", SLUG, status="running", claimed_at=datetime.utcnow().isoformat(),
              element_kind="note", qualified_name="n")
    with pytest.raises(HTTPException) as e:
        _route(SLUG, False, who=OWNER)
    assert e.value.status_code == 409 and registry.get_investigation(SLUG, user_id="") is not None


def test_route_is_registered_as_delete():
    from resource_explorer.web.app import app

    assert "delete" in app.openapi()["paths"]["/api/investigations/{slug}"]


# ── the Egeria choices (fix round 2, 2026-10-21): delete in RE, ARCHIVE in Egeria ───────────────────────

@pytest.fixture(autouse=True)
def block_off(monkeypatch):
    """The archive-cascade block is ON unless an environment clearance lifts it. Archive tests run with it lifted; the
    tests about the block itself ask for `block_on`."""
    monkeypatch.setenv("RE_ISSUE_117_BLOCK_OFF", "test/clearance")


@pytest.fixture
def block_on(monkeypatch):
    monkeypatch.delenv("RE_ISSUE_117_BLOCK_OFF", raising=False)


def _run(registry, world, choice=CHOICE_RE_AND_EGERIA, confirmed="", **kw):
    return _deleter(registry, world, **kw).run(SLUG, choice, by=OWNER, confirmed=confirmed)


def _archive_bodies(world, kind):
    return [b for k, _g, b in world.bodies if k == kind]


def test_re_only_makes_zero_egeria_calls_and_says_what_it_left(promoted, registry):
    world = World()
    res = _deleter(registry, world).run(SLUG, CHOICE_RE_ONLY, by=OWNER)
    assert res.ok and res.local_deleted and not res.egeria_done
    assert world.calls == []
    assert PROJECT in world.exist and FOLIO in world.exist and NOTE1 in world.exist
    with registry._conn() as conn:
        row = conn.execute("SELECT * FROM activity_log WHERE operation = 'delete'").fetchone()
    detail = json.loads(row["detail"])
    assert detail["choice"] == CHOICE_RE_ONLY and detail["by"] == OWNER
    assert {d["guid"] for d in detail["left_behind_in_egeria"]} == {PROJECT, FOLIO, DISP}
    assert "Resource Explorer only" in row["summary"] and OWNER in row["summary"]


def test_the_archive_unlinks_first_then_archives_what_is_not_anchored_then_the_project(promoted, registry):
    world = World()
    res = _run(registry, world)
    assert res.ok and res.egeria_done and res.local_deleted
    assert world.write_calls() == [
        ("remove_from_collection", f"{FOLIO}:{ASSET}"),      # memberships first, one call at a time
        ("delete_collection", DISP),                         # the disposition set is not anchored: its own archive
        ("delete_project", PROJECT)]                         # the Folio, NoteLog and Notes ride with the Project
    # the member's own asset is only taken out of the Collection: never archived, deleted or cascaded
    assert ASSET in world.exist and ASSET not in world.archived and world.hard_deleted == []
    assert [u["guid"] for u in res.unlinked] == [ASSET]
    assert {a["guid"] for a in res.archived} == {PROJECT, DISP, FOLIO, LOG, NOTE1, NOTE2}
    assert set(world.archived) == {PROJECT, DISP, FOLIO, LOG, NOTE1, NOTE2}
    # Egeria's one operation took everything anchored; the disposition set was a separate operation
    assert len({world.archived[g] for g in (PROJECT, FOLIO, LOG, NOTE1, NOTE2)}) == 1
    assert world.archived[DISP] != world.archived[PROJECT]
    assert registry.get_investigation(SLUG, user_id="") is None
    with registry._conn() as conn:
        row = conn.execute("SELECT * FROM activity_log WHERE operation = 'delete'").fetchone()
    assert json.loads(row["detail"])["choice"] == CHOICE_RE_AND_EGERIA
    assert "archived in Egeria" in row["summary"] or "Egeria" in row["summary"]


def test_delete_project_and_delete_collection_are_never_called_without_deleteMethod_ARCHIVE(promoted, registry):
    world = World()
    _run(registry, world)
    assert world.bodies and all(b and b.get("deleteMethod") == "ARCHIVE" and b.get("class") == "DeleteElementRequestBody"
                                for _k, _g, b in world.bodies)
    assert {k for k, _g, _b in world.bodies} == {"delete_project", "delete_collection"}
    assert world.hard_deleted == []
    # the body survives pyegeria's own validation with the method intact (what actually goes on the wire)
    from pyegeria.models.models import DeleteElementRequestBody
    from resource_explorer.investigation_delete import archive_body
    sent = json.loads(DeleteElementRequestBody.model_validate(archive_body()).model_dump_json(exclude_none=True))
    assert sent["deleteMethod"] == "ARCHIVE"


def test_a_collection_anchored_to_the_project_gets_no_call_of_its_own(promoted, registry):
    world = World()
    res = _run(registry, world)
    assert ("delete_collection", FOLIO) not in world.calls and FOLIO in {a["guid"] for a in res.archived}
    assert [a for a in res.archived if a["guid"] == FOLIO][0]["with_project"] is True


def test_a_collection_that_is_not_anchored_is_archived_separately_whichever_one_it_is(promoted, registry):
    world = World()
    del world.anchored[FOLIO]                                       # now the Folio is only attached
    world.anchored[DISP] = "Collection"                             # and the disposition set is anchored
    res = _run(registry, world)
    assert res.ok
    assert ("delete_collection", FOLIO) in world.calls and ("delete_collection", DISP) not in world.calls
    assert world.write_calls().index(("remove_from_collection", f"{FOLIO}:{ASSET}")) < world.write_calls().index(
        ("delete_collection", FOLIO)) < world.write_calls().index(("delete_project", PROJECT))


def test_the_preflight_names_other_peoples_items_and_writes_nothing(promoted, registry):
    world = World()
    world.add_anchored(RATING, "Rating", name="5 stars", author="mandy", created=1759000000000)
    world.add_anchored(COMMENT, "Comment", name="looks right to me", author="olive", created=1759100000000)
    out = _deleter(registry, world).preview(SLUG)
    assert out["ok"] and out["project_ours"] and not out["blocked"]
    got = {(f["kind"], f["guid"], f["author"], f["name"]) for f in out["foreign"]}
    assert got == {("Rating", RATING, "mandy", "5 stars"), ("Comment", COMMENT, "olive", "looks right to me")}
    assert {f["created"] for f in out["foreign"]} == {"2025-09-27", "2025-09-28"}
    assert {o["guid"] for o in out["ours"]} == {FOLIO, LOG, NOTE1, NOTE2}        # RE's own are not listed as others'
    assert out["token"]
    assert world.write_calls() == [] and world.hard_deleted == [] and world.archived == {}
    assert registry.get_investigation(SLUG, user_id="") is not None


def test_names_then_allow_other_peoples_items_are_archived_with_the_project_and_recorded(promoted, registry):
    world = World()
    world.add_anchored(RATING, "Rating", name="5 stars", author="mandy")
    world.add_anchored(COMMENT, "Comment", name="looks right to me", author="olive")
    token = _deleter(registry, world).preview(SLUG)["token"]
    res = _run(registry, world, confirmed=token)
    assert res.ok and res.local_deleted
    assert {f["guid"] for f in res.foreign} == {RATING, COMMENT}
    assert RATING in world.archived and COMMENT in world.archived and world.hard_deleted == []     # restorable
    assert world.archived[RATING] == world.archived[PROJECT]
    with registry._conn() as conn:
        detail = json.loads(conn.execute("SELECT detail FROM activity_log WHERE operation = 'delete'").fetchone()["detail"])
    assert {f["guid"] for f in detail["egeria_foreign"]} == {RATING, COMMENT}


def test_a_list_that_changed_since_the_confirm_writes_nothing_and_keeps_the_investigation(promoted, registry):
    world = World()
    token = _deleter(registry, world).preview(SLUG)["token"]            # the person saw: nothing from others
    world.add_anchored(RATING, "Rating", name="5 stars", author="mandy")    # someone rated it since
    res = _run(registry, world, confirmed=token)
    assert not res.local_deleted and not res.ok
    assert world.write_calls() == [] and world.archived == {}
    assert "changed since you looked" in res.errors[0] and RATING in res.errors[0]
    assert registry.get_investigation(SLUG, user_id="") is not None


def test_proof_rows_store_the_guid_and_the_operation_id_and_outlive_the_investigation(promoted, registry):
    world = World()
    res = _run(registry, world)
    assert res.local_deleted and registry.get_investigation(SLUG, user_id="") is None
    rows = registry.list_investigation_archive_proofs(SLUG)
    by_guid = {r["element_guid"]: r for r in rows if r["proof"] == "archived"}
    assert set(by_guid) == {PROJECT, DISP, FOLIO, LOG, NOTE1, NOTE2}
    p = by_guid[PROJECT]
    assert p["detail"]["operation_id"] == world.archived[PROJECT] and p["schema_name"] == "Project"
    assert p["detail"]["words"] == "archived in Egeria · 2025-10-19"
    assert "_archivedOn_" in p["qualified_name"] and p["detail"]["with_project"] is False
    assert by_guid[NOTE1]["detail"]["operation_id"] == world.archived[PROJECT] and by_guid[NOTE1]["detail"]["with_project"]
    assert by_guid[DISP]["detail"]["operation_id"] == world.archived[DISP]
    assert [a for a in res.archived if a["guid"] == PROJECT][0]["words"] == "archived in Egeria · 2025-10-19"


def test_the_block_on_refuses_the_egeria_choice_before_anything_and_re_only_still_works(promoted, registry, block_on):
    from resource_explorer.catalogue_gateway import ISSUE_117_WORDS
    from resource_explorer.investigation_delete import ArchiveBlocked
    _outbox(registry, "pending")
    world = World()
    with pytest.raises(ArchiveBlocked) as e:
        _run(registry, world)
    assert e.value.words == ISSUE_117_WORDS
    assert world.calls == [] and _statuses(registry) == ["pending"]          # not even the outbox was touched
    assert registry.get_investigation(SLUG, user_id="") is not None
    assert _deleter(registry, world).preview(SLUG)["blocked"] is True
    assert _deleter(registry, world).preview(SLUG)["blocked_words"] == ISSUE_117_WORDS
    res = _run(registry, World(), CHOICE_RE_ONLY)
    assert res.ok and res.local_deleted


def test_route_409_with_the_blocks_sentence_for_the_egeria_choice_and_re_only_deletes(promoted, registry, routes_on,
                                                                                    block_on):
    from resource_explorer.catalogue_gateway import ISSUE_117_WORDS
    with pytest.raises(HTTPException) as e:
        _route(SLUG, True, who=OWNER)
    assert e.value.status_code == 409 and e.value.detail == ISSUE_117_WORDS and routes_on.calls == []
    assert registry.get_investigation(SLUG, user_id="") is not None
    assert _route(SLUG, False, who=OWNER)["local_deleted"]


def test_route_preview_answers_reads_only_and_is_guarded_like_the_delete(promoted, registry, routes_on):
    from resource_explorer.web.routes import investigations as routes
    routes_on.add_anchored(RATING, "Rating", name="5 stars", author="mandy")
    token = current_caller.set(_identity(OWNER))
    try:
        out = asyncio.run(routes.delete_investigation_preview(SLUG))
    finally:
        current_caller.reset(token)
    assert out["ok"] and [f["guid"] for f in out["foreign"]] == [RATING] and out["token"]
    assert routes_on.write_calls() == []
    token = current_caller.set(_identity(OTHER))
    try:
        with pytest.raises(HTTPException) as e:
            asyncio.run(routes.delete_investigation_preview(SLUG))
    finally:
        current_caller.reset(token)
    assert e.value.status_code == 403


def test_route_preview_of_an_unpromoted_investigation_is_400(registry, as_caller, routes_on):
    from resource_explorer.web.routes import investigations as routes
    as_caller(OWNER)
    registry.create_investigation("Local only", egeria_binding="local")
    with pytest.raises(HTTPException) as e:
        asyncio.run(routes.delete_investigation_preview("local-only"))
    assert e.value.status_code == 400


def test_an_unreadable_preflight_is_not_ok_and_offers_nothing(promoted, registry):
    world = World()
    world.read_error = TimeoutError("read timed out")
    out = _deleter(registry, world).preview(SLUG)
    assert out["ok"] is False and out["error"]


# ── refusals, stops and resuming ──────────────────────────────────────────────────────────────────────

def test_a_refused_archive_of_the_project_stops_and_keeps_the_investigation(promoted, registry):
    world = World()
    world.refuse[PROJECT] = PermissionError("OPEN-METADATA-SECURITY-403-020 you may not archive this. More words")
    res = _run(registry, world)
    assert not res.ok and not res.local_deleted and not res.egeria_done
    assert world.write_calls()[-1] == ("delete_project", PROJECT) and world.calls.count(("delete_project", PROJECT)) == 1
    assert [r["guid"] for r in res.remaining] == [PROJECT] and "403-020" in res.remaining[0]["why"]
    assert "403-020" in res.errors[0]
    assert registry.get_investigation(SLUG, user_id="")["member_count"] == 1       # nothing local deleted
    assert _count(registry, "working_sets") == 2
    with registry._conn() as conn:
        row = conn.execute("SELECT * FROM activity_log WHERE operation = 'delete'").fetchone()
    assert row["status"] == "error" and "kept" in row["summary"]
    refused = [r for r in registry.list_investigation_archive_proofs(SLUG) if r["proof"] == "archive_refused"]
    assert [r["element_guid"] for r in refused] == [PROJECT] and "403-020" in refused[0]["detail"]["egeria_sentence"]


def test_resume_after_a_refused_disposition_set_skips_the_unlinks_already_made(promoted, registry):
    world = World()
    world.refuse[DISP] = RuntimeError("boom")
    first = _run(registry, world)
    assert not first.local_deleted and world.write_calls() == [("remove_from_collection", f"{FOLIO}:{ASSET}"),
                                                              ("delete_collection", DISP)]
    world.refuse.clear()
    world.calls.clear()
    second = _run(registry, world)
    assert second.ok and second.local_deleted
    assert world.write_calls() == [("delete_collection", DISP), ("delete_project", PROJECT)]    # no second unlink


def test_resume_after_a_refused_project_skips_an_archived_collection_and_writes_its_proof_once(promoted, registry):
    world = World()
    world.refuse[PROJECT] = RuntimeError("boom")
    _run(registry, world)
    assert DISP in world.archived and PROJECT not in world.archived
    world.refuse.clear()
    world.calls.clear()
    res = _run(registry, world)
    assert res.ok and res.local_deleted
    assert world.write_calls() == [("delete_project", PROJECT)]                  # the archived Collection is not re-sent
    note = [a for a in res.archived if a["guid"] == DISP][0]
    assert note["note"] == "already archived in Egeria"
    rows = [r for r in registry.list_investigation_archive_proofs(SLUG) if r["proof"] == "archived" and r["element_guid"] == DISP]
    assert len(rows) == 1                                                       # one proof row per archived element


def test_a_second_run_after_a_full_archive_re_sends_nothing(promoted, registry, monkeypatch):
    """The local delete failed after Egeria finished; pressing Delete again must not archive anything twice."""
    world = World()

    def boom(self, slug, activity=None):
        raise RuntimeError("database is locked")

    real = ProjectRegistry.delete_investigation_local
    monkeypatch.setattr(ProjectRegistry, "delete_investigation_local", boom)
    first = _run(registry, world)
    assert first.egeria_done and not first.local_deleted
    monkeypatch.setattr(ProjectRegistry, "delete_investigation_local", real)
    world.calls.clear()
    second = _run(registry, world)
    assert second.ok and second.local_deleted and world.write_calls() == []
    assert len([r for r in registry.list_investigation_archive_proofs(SLUG) if r["proof"] == "archived"
                and r["element_guid"] == PROJECT]) == 1


def test_an_archive_that_cannot_be_confirmed_stops(promoted, registry):
    world = World()
    world.stuck.add(PROJECT)                         # the call is accepted and the Project stays live
    res = _run(registry, world)
    assert not res.local_deleted and res.remaining[-1]["guid"] == PROJECT
    assert "still returns it as live" in res.remaining[-1]["why"]


def test_an_exception_on_the_read_back_is_not_archived(promoted, registry):
    world = World()
    handles = world.handles()
    original = handles.me.get_metadata_element_by_guid.side_effect

    def flaky(*a, **k):
        if ("delete_project", PROJECT) in world.calls:
            raise TimeoutError("read timed out")
        return original(*a, **k)

    handles.me.get_metadata_element_by_guid.side_effect = flaky
    res = InvestigationDeleter(registry, handles_factory=lambda: handles, sleep=lambda s: None).run(
        SLUG, CHOICE_RE_AND_EGERIA, by=OWNER)
    assert not res.local_deleted and "could not be asked" in res.remaining[-1]["why"]


def test_an_element_the_project_archive_did_not_take_is_not_reported_archived(promoted, registry):
    world = World()
    world.stuck.add(NOTE1)                           # Egeria archives the Project but leaves this Note live
    res = _run(registry, world)
    assert not res.local_deleted and [r["guid"] for r in res.remaining] == [NOTE1]
    assert NOTE1 not in {a["guid"] for a in res.archived} and PROJECT in {a["guid"] for a in res.archived}


def test_an_unreadable_anchor_graph_stops_before_any_write(promoted, registry):
    world = World()
    handles = world.handles()
    handles.me.get_anchored_element_graph.side_effect = lambda *a, **k: "No element found"
    res = InvestigationDeleter(registry, handles_factory=lambda: handles, sleep=lambda s: None).run(
        SLUG, CHOICE_RE_AND_EGERIA, by=OWNER)
    assert not res.local_deleted and world.write_calls() == [] and res.errors


def test_a_refused_unlink_stops_before_any_archive(promoted, registry):
    world = World()
    world.refuse[f"{FOLIO}:{ASSET}"] = PermissionError("OPEN-METADATA-SECURITY-403-020 not yours")
    res = _run(registry, world)
    assert not res.local_deleted and ("delete_collection", DISP) not in world.calls
    assert ("delete_project", PROJECT) not in world.calls
    assert res.remaining[-1]["kind"] == "CollectionMembership" and "403-020" in res.remaining[-1]["why"]
    assert ASSET in world.exist


def test_members_that_will_not_unlink_stop_it(promoted, registry):
    world = World()
    world.stuck.add(ASSET)                           # the unlink is accepted but the member stays listed
    res = _run(registry, world)
    assert not res.local_deleted and ("delete_project", PROJECT) not in world.calls
    assert "still lists" in res.errors[0]


def test_a_collection_with_many_members_is_unlinked_one_call_each_never_cascaded(promoted, registry):
    world = World()
    world.members[FOLIO] = [ASSET, "asset-2"]
    world.exist.add("asset-2")
    res = _run(registry, world)
    assert res.ok
    order = world.write_calls()
    assert order[:2] == [("remove_from_collection", f"{FOLIO}:{ASSET}"), ("remove_from_collection", f"{FOLIO}:asset-2")]
    assert ASSET in world.exist and "asset-2" in world.exist and world.hard_deleted == []
    assert [u["guid"] for u in res.unlinked] == [ASSET, "asset-2"]


def test_a_project_that_was_bound_not_made_by_re_is_left_in_egeria_and_collections_are_archived_alone(promoted, registry):
    registry.set_investigation_egeria_project(SLUG, {
        "status": "linked", "egeria_project_guid": PROJECT, "egeria_project_qualified_name": "Project::Theirs::1"})
    world = World()
    res = _run(registry, world)
    assert res.local_deleted and ("delete_project", PROJECT) not in world.calls and PROJECT in world.exist
    assert res.left[-1]["guid"] == PROJECT and "did not create it" in res.left[-1]["why"]
    # the Project is not being archived, so RE's Collections are archived on their own
    assert ("delete_collection", FOLIO) in world.calls and ("delete_collection", DISP) in world.calls
    assert NOTE1 in world.exist and LOG in world.exist
    assert world.hard_deleted == []


def test_a_notelog_re_did_not_make_is_other_peoples_when_anchored_and_not_listed_when_only_attached(promoted, registry):
    world = World()
    world.extra_logs = [_el("their-log", "NoteLog::Theirs", "Their notes")]
    world.add_anchored("their-log", "NoteLog", name="Their notes", author="olive")
    out = _deleter(registry, world).preview(SLUG)
    assert [f["guid"] for f in out["foreign"]] == ["their-log"]
    assert "their-log" not in {x["guid"] for x in out["left"]}                   # it is named, not "left alone"
    world2 = World()
    world2.extra_logs = [_el("attached-log", "NoteLog::Theirs", "Attached only")]
    assert _deleter(registry, world2).preview(SLUG)["foreign"] == []


def test_re_s_notelog_that_the_project_does_not_anchor_is_left_and_said_so(promoted, registry):
    world = World()
    del world.anchored[LOG]
    res = _run(registry, world)
    assert res.local_deleted and LOG not in world.archived and LOG in world.exist
    assert LOG in {x["guid"] for x in res.left} and "not anchored" in [x for x in res.left if x["guid"] == LOG][0]["why"]


def test_a_collection_re_did_not_make_is_left_alone_when_it_is_not_anchored(promoted, registry):
    world = World()
    del world.anchored[FOLIO]
    world.names[FOLIO] = "Folio::Somebody::Else"
    res = _run(registry, world)
    assert ("delete_collection", FOLIO) not in world.calls and FOLIO in world.exist and FOLIO not in world.archived
    assert FOLIO in {x["guid"] for x in res.left} and res.local_deleted


def test_egeria_choice_on_an_unpromoted_investigation_is_refused(registry, as_caller):
    as_caller(OWNER)
    registry.create_investigation("Local only", egeria_binding="local")
    with pytest.raises(ValueError, match="never promoted"):
        _deleter(registry, World()).run("local-only", CHOICE_RE_AND_EGERIA, by=OWNER)


def test_signatures_match_the_real_clients():
    """The calls the deleter makes are the ones the fakes bind: spec_set autospecs reject anything else, and the
    real methods take the `body` that carries deleteMethod."""
    import inspect
    for cls, name in [(ProjectManager, "delete_project"), (CollectionManager, "delete_collection")]:
        assert "body" in inspect.signature(getattr(cls, name)).parameters, name
    for cls, name in [(ProjectManager, "get_attached_note_logs"), (ProjectManager, "get_notes_for_note_log"),
                      (MetadataExpert, "get_metadata_element_by_guid"), (MetadataExpert, "get_anchored_element_graph"),
                      (CollectionManager, "get_collection_by_guid"), (CollectionManager, "remove_from_collection"),
                      (CollectionManager, "get_collection_members")]:
        assert callable(getattr(cls, name)), name


def test_the_project_step_is_one_swappable_function():
    assert callable(InvestigationDeleter._retire_project)


# ── the local side ─────────────────────────────────────────────────────────────────────────────────────

# ── the local side ─────────────────────────────────────────────────────────────────────────────────────

def test_every_table_with_a_type_and_slug_pair_is_decided(registry):
    """A new table with an (entity_type|resource_type, slug) pair must be added to the delete or to the keep list."""
    pairs = (("entity_type", "entity_slug"), ("entity_type", "slug"), ("resource_type", "resource_slug"),
             ("resource_type", "slug"))
    found = set()
    with registry._conn() as conn:
        for t in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
            cols = {c["name"] for c in conn.execute(f"PRAGMA table_info({t['name']})").fetchall()}
            if any(a in cols and b in cols for a, b in pairs):
                found.add(t["name"])
    decided = set(registry.INVESTIGATION_KEYED_TABLES) | set(registry.INVESTIGATION_NOT_KEYED_TABLES)
    assert found - decided == set(), f"decide these in registry.INVESTIGATION_*: {sorted(found - decided)}"
    assert set(registry.INVESTIGATION_KEYED_TABLES) <= found


def test_every_local_table_keyed_by_the_slug_is_empty_and_the_activity_row_is_written(promoted, registry):
    from resource_explorer.journal import Journal
    from resource_explorer.work_lists import WorkLists

    Journal(registry).write("investigation", SLUG, author=OWNER, body="a note to self")
    for table in registry.INVESTIGATION_KEYED_TABLES:
        _seed(registry, table, SLUG)
        _seed(registry, table, "another-investigation")            # a neighbour that must survive
    _seed(registry, "egeria_outbox", SLUG, status="pending", element_kind="note", qualified_name="p")
    _seed(registry, "egeria_outbox", SLUG, status="failed", element_kind="note", qualified_name="f")
    _seed(registry, "egeria_outbox", SLUG, status="done", element_kind="note", qualified_name="d")
    _seed(registry, "egeria_outbox", SLUG, status="dead", element_kind="note", qualified_name="x")
    registry.create_investigation("Another")
    wl = WorkLists(registry).create("Saved", ["crm"], entity_type="database", rationale="", investigation=SLUG)
    assert _count(registry, "resource_journal", "entity_slug = ?", (SLUG,)) >= 1

    res = _deleter(registry, World()).run(SLUG, CHOICE_RE_ONLY, by=OWNER)

    assert res.local_deleted
    for table in registry.INVESTIGATION_KEYED_TABLES:
        assert _count(registry, table, "entity_type = 'investigation' AND entity_slug = ?", (SLUG,)) == 0, table
        assert _count(registry, table, "entity_slug = 'another-investigation'") == 1, table
    for table, where in [("investigations", "slug = ?"), ("investigation_resource_lists", "investigation_slug = ?"),
                         ("working_sets", "slug LIKE ?"), ("working_set_members", "working_set_slug LIKE ?")]:
        like = f"{SLUG}-%" if "LIKE" in where else SLUG
        assert _count(registry, table, where, (like,)) == 0, table
    assert _count(registry, "egeria_outbox", "entity_slug = ? AND status IN ('pending','failed')", (SLUG,)) == 0
    assert _count(registry, "egeria_outbox", "entity_slug = ? AND status = 'cancelled'", (SLUG,)) == 2
    assert _count(registry, "egeria_outbox", "entity_slug = ? AND status IN ('done','dead')", (SLUG,)) == 2
    assert _count(registry, "work_lists", "slug = ? AND investigation = ''", (wl["slug"],)) == 1
    assert registry.get_investigation("another", user_id="") is not None
    # the activity log is kept, plus the one delete row, which names the choice, who and what was left
    assert _count(registry, "activity_log", "operation = 'delete' AND entity_slug = ?", (SLUG,)) == 1
    assert res.activity_id


def test_the_local_delete_is_one_transaction(promoted, registry, monkeypatch):
    """A failure part-way leaves everything: the investigation, its sets and the journal."""
    from resource_explorer.journal import Journal

    Journal(registry).write("investigation", SLUG, author=OWNER, body="keep me if it fails")
    real = ProjectRegistry._insert_activity

    def boom(conn, entry):
        raise RuntimeError("activity write failed")

    monkeypatch.setattr(ProjectRegistry, "_insert_activity", staticmethod(boom))
    with pytest.raises(RuntimeError):
        registry.delete_investigation_local(SLUG, activity=object())
    monkeypatch.setattr(ProjectRegistry, "_insert_activity", staticmethod(real))
    assert registry.get_investigation(SLUG, user_id="") is not None
    assert _count(registry, "resource_journal", "entity_slug = ?", (SLUG,)) == 1
    assert _count(registry, "working_sets") == 2


# ── outbox, the persistent cue, name matching, the local failure, orphans ────────────────────────────

def _outbox(registry, status, **extra):
    _seed(registry, "egeria_outbox", SLUG, status=status, element_kind="note", qualified_name=f"q-{status}", **extra)


def _statuses(registry):
    with registry._conn() as conn:
        return sorted(r["status"] for r in conn.execute(
            "SELECT status FROM egeria_outbox WHERE entity_slug = ?", (SLUG,)).fetchall())


def test_m1_pending_and_failed_rows_are_cancelled_at_the_very_start_not_deleted(promoted, registry):
    _outbox(registry, "pending")
    _outbox(registry, "failed")
    _outbox(registry, "done")
    assert registry.begin_investigation_delete(SLUG) == 2
    assert _statuses(registry) == ["cancelled", "cancelled", "done"]


def test_m1_a_running_row_refuses_the_whole_delete_and_changes_nothing(promoted, registry):
    from datetime import datetime
    from resource_explorer.registry import InvestigationPublishRunning
    _outbox(registry, "pending")
    _outbox(registry, "running", claimed_at=datetime.utcnow().isoformat())
    world = World()
    with pytest.raises(InvestigationPublishRunning):
        _run(registry, world, CHOICE_RE_AND_EGERIA)
    assert world.calls == []                                       # no Egeria call at all
    assert _statuses(registry) == ["pending", "running"]           # the running row untouched, the pending one too
    assert registry.get_investigation(SLUG, user_id="") is not None


def test_m1_an_expired_claim_is_a_dead_claim_and_is_cancelled_not_a_blocker(promoted, registry):
    from datetime import datetime, timedelta
    old = (datetime.utcnow() - timedelta(hours=3)).isoformat()
    _outbox(registry, "running", claimed_at=old)
    res = _run(registry, World(), CHOICE_RE_ONLY)
    assert res.local_deleted and _statuses(registry) == ["cancelled"]


def test_m1_a_row_claimed_after_the_first_step_rolls_the_local_delete_back(promoted, registry):
    from datetime import datetime
    world = World()
    handles = world.handles()
    real_graph = handles.me.get_anchored_element_graph.side_effect

    def claim_then_graph(*a, **k):
        _outbox(registry, "running", claimed_at=datetime.utcnow().isoformat())
        return real_graph(*a, **k)

    handles.me.get_anchored_element_graph.side_effect = claim_then_graph
    res = InvestigationDeleter(registry, handles_factory=lambda: handles, sleep=lambda s: None).run(
        SLUG, CHOICE_RE_AND_EGERIA, by=OWNER)
    assert not res.local_deleted and not res.ok and res.errors          # words, not an exception
    assert registry.get_investigation(SLUG, user_id="") is not None
    assert "running" in _statuses(registry)                           # never deleted locally


def test_m2_the_cue_comes_from_the_latest_error_row_and_a_later_ok_row_clears_it(promoted, registry):
    world = World()
    world.refuse[PROJECT] = RuntimeError("boom")
    _run(registry, world)
    stop = registry.latest_investigation_delete_stop(SLUG)
    assert stop and DISP in [d["guid"] for d in stop["archived"]] and [r["guid"] for r in stop["remaining"]] == [PROJECT]
    assert [u["guid"] for u in stop["unlinked"]] == [ASSET]
    assert "boom" in stop["errors"][0]
    # the page's own read carries it, so a reload shows it
    from resource_explorer.web.routes import investigations as routes
    import resource_explorer.web.routes.investigations as mod
    mod_registry = mod._registry
    mod._registry = lambda: registry
    try:
        got = asyncio.run(routes.get_investigation(SLUG))
    finally:
        mod._registry = mod_registry
    assert got["delete_stop"]["remaining"][0]["guid"] == PROJECT
    # a later delete that finished (an ok row) clears it
    from datetime import datetime, timedelta, timezone
    from resource_explorer.registry import ActivityEntry
    registry.write_activity(ActivityEntry(
        id="later-ok", ts=(datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat(), operation="delete",
        intent="enrichment", entity_type="investigation", entity_slug=SLUG, entity_name="x", status="ok",
        summary="deleted", detail="{}", items=[]))
    assert registry.latest_investigation_delete_stop(SLUG) is None


def test_m2_no_stop_when_nothing_ever_stopped(promoted, registry):
    assert registry.latest_investigation_delete_stop(SLUG) is None


def test_l1_a_collection_whose_name_only_starts_like_ours_is_not_ours(promoted, registry):
    world = World()
    world.names[DISP] = f"WorkingSet::Investigation::{SLUG}::tracking-somebody-elses"      # startswith would match
    res = _run(registry, world)
    assert ("delete_collection", DISP) not in world.calls and DISP in world.exist
    assert DISP in {x["guid"] for x in res.left}


def test_l1_a_disposition_set_is_matched_by_its_own_disposition(promoted, registry):
    world = World()
    world.names[DISP] = f"WorkingSet::Investigation::{SLUG}::other-disposition"
    assert DISP in {x["guid"] for x in _run(registry, world).left} and ("delete_collection", DISP) not in world.calls


def test_l2_a_local_failure_after_a_full_archive_answers_in_words_and_records_what_egeria_holds(
        promoted, registry, monkeypatch):
    world = World()

    def boom(self, slug, activity=None):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(ProjectRegistry, "delete_investigation_local", boom)
    res = _run(registry, world)                                              # does not raise
    assert not res.ok and not res.local_deleted and res.egeria_done
    assert "still here" in res.errors[0] and "database is locked" in res.errors[0]
    assert set(world.archived) == {PROJECT, DISP, FOLIO, LOG, NOTE1, NOTE2}
    with registry._conn() as conn:
        row = conn.execute("SELECT * FROM activity_log WHERE operation = 'delete'").fetchone()
    assert row["status"] == "error"
    assert len(json.loads(row["detail"])["egeria_archived"]) == 6
    assert registry.latest_investigation_delete_stop(SLUG)["archived"]


def test_l2_the_route_answers_ok_false_not_a_500(promoted, registry, routes_on, monkeypatch):
    def boom(self, slug, activity=None):
        raise RuntimeError("disk full")

    monkeypatch.setattr(ProjectRegistry, "delete_investigation_local", boom)
    out = _route(SLUG, True, who=OWNER)
    assert out["ok"] is False and out["local_deleted"] is False and "disk full" in out["errors"][0]


def test_l3_orphaned_disposition_sets_are_swept_locally_and_in_egeria_but_a_neighbours_are_not(promoted, registry):
    orphan = f"{SLUG}-ws-parked"
    now = "2026-10-21T00:00:00"
    with registry._conn() as conn:
        conn.execute("INSERT INTO working_sets (slug, display_name, collection_kind, disposition, created_at, "
                      "egeria_collection_guid) VALUES (?, ?, 'working_set', 'parked', ?, 'orphan-guid')",
                     (orphan, "orphan", now))
    registry.create_investigation("Customer 360 ws x")                    # slug customer-360-ws-x: a neighbour
    neighbour_folio = registry.get_or_create_folio("customer-360-ws-x")
    world = World()
    world.exist.add("orphan-guid")
    world.names["orphan-guid"] = f"WorkingSet::Investigation::{SLUG}::parked"
    world.members["orphan-guid"] = []
    res = _run(registry, world)
    assert res.ok and ("delete_collection", "orphan-guid") in world.calls and "orphan-guid" in world.archived
    assert _count(registry, "working_sets", "slug = ?", (orphan,)) == 0
    assert _count(registry, "working_sets", "slug = ?", (neighbour_folio["slug"],)) == 1
    assert registry.get_investigation("customer-360-ws-x", user_id="") is not None


def test_a_private_investigation_is_the_owners_alone_an_admin_gets_404(registry, as_caller, routes_on):
    as_caller(OWNER)
    registry.create_investigation("Secret", project_classification="PersonalProject")
    with pytest.raises(HTTPException) as e:
        _route("secret", False, who=ADMIN, role="admin")
    assert e.value.status_code == 404
    assert registry.get_investigation("secret", user_id=OWNER) is not None       # untouched
    assert _route("secret", False, who=OWNER)["local_deleted"]                   # its owner can


def test_the_words_say_what_the_designer_asked_for():
    import pathlib
    js = (pathlib.Path(__file__).parent.parent / "resource_explorer/web/static/next/stages/investigation-delete.js"
          ).read_text()
    assert "Delete in Resource Explorer, archive in Egeria" in js
    assert "Archives the Project in Egeria (restorable); its notes, ratings and comments go with it." in js
    assert "A Project that Resource Explorer did not create is left in Egeria" in js
    assert "collide with the Project" in js


def test_the_leave_out_lift_of_2026_10_23_leaves_the_investigation_archive_blocked(promoted, registry, block_on,
                                                                                   monkeypatch):
    """The shipped rules, un-stubbed, at the default: the lift covers the leave-out ARCHIVE only, so the
    investigation's ARCHIVE (anchor = Project) is still refused before anything is read or changed."""
    from resource_explorer import catalogue_gateway as gw
    from resource_explorer.investigation_delete import ArchiveBlocked
    monkeypatch.setattr(gw, "issue_117_blocked", gw._shipped_issue_117_blocked)
    monkeypatch.delenv("RE_ISSUE_117_BLOCK", raising=False)
    assert gw.issue_117_state_line().startswith("archive-cascade block: OFF (lifted 2026-10-23")
    world = World()
    with pytest.raises(ArchiveBlocked) as e:
        _run(registry, world)
    assert e.value.words == gw.ISSUE_117_WORDS and world.calls == []
    assert _deleter(registry, world).preview(SLUG)["blocked"] is True
