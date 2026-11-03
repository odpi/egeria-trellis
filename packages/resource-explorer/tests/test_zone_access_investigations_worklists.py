"""Brief Z3 (2026-10-11): zone access on investigations, work lists and RFA ToDo writes.

Brief Z2 left these routes on sign-in only: investigation promote, bind, reclassify, relink and sync; work-list
publish and batch runs; and the RFA status PATCH that creates or updates an Egeria ToDo. Every one now asks the
same `workflows.curate.curation_access` (through `workflows.egeria_work_access`) with the operation Egeria checks
for its write.

No Egeria anywhere: the access reads are faked at the client-construction boundary (`tests/zone_fakes.py`), the
write clients are fakes bound to the real pyegeria signatures, and in the route table what a route does after its
check is replaced by a sentinel, so "allowed" means the check passed and the route went on to its write.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import Project, ProjectRegistry

sys.path.insert(0, str(Path(__file__).parent))

from tests.zone_fakes import FakeEgeria, install, real_lookup  # noqa: E402
from tests.zone_fakes import element as _element  # noqa: E402

pytestmark = pytest.mark.usefixtures("mock_egeria_client_connections")

USER = "garygeeke"
OWNER = "peterprofile"
SECURED = "sales-zone"


def _real(cls_path: str, name: str) -> inspect.Signature:
    module, _, cls = cls_path.rpartition(".")
    return inspect.signature(getattr(getattr(__import__(module, fromlist=[cls]), cls), name))


REAL = {
    "get_metadata_element_by_guid": _real("pyegeria.omvs.metadata_expert.MetadataExpert",
                                          "get_metadata_element_by_guid"),
    "get_security_access_control": _real("pyegeria.omvs.security_officer.SecurityOfficer",
                                         "get_security_access_control"),
    "get_user_account": _real("pyegeria.omvs.security_officer.SecurityOfficer", "get_user_account"),
    "create_collection": _real("pyegeria.omvs.collection_manager.CollectionManager", "create_collection"),
    "add_to_collection": _real("pyegeria.omvs.collection_manager.CollectionManager", "add_to_collection"),
    "attach_collection": _real("pyegeria.omvs.collection_manager.CollectionManager", "attach_collection"),
    "create_project": _real("pyegeria.omvs.project_manager.ProjectManager", "create_project"),
    "get_project_by_guid": _real("pyegeria.omvs.project_manager.ProjectManager", "get_project_by_guid"),
}


class FaithfulEgeria(FakeEgeria):
    """`zone_fakes.FakeEgeria`, with every call bound against the installed pyegeria method first."""

    def get_metadata_element_by_guid(self, *a, **kw):
        REAL["get_metadata_element_by_guid"].bind(self, *a, **kw)
        return super().get_metadata_element_by_guid(*a, **kw)

    def get_security_access_control(self, *a, **kw):
        REAL["get_security_access_control"].bind(self, *a, **kw)
        return super().get_security_access_control(*a, **kw)

    def get_user_account(self, *a, **kw):
        REAL["get_user_account"].bind(self, *a, **kw)
        return super().get_user_account(*a, **kw)


class FakeCollections:
    """The write side of a CollectionManager / ProjectManager, bound to the real signatures, recording."""

    def __init__(self):
        self.calls: list[tuple] = []

    def create_collection(self, *a, **kw):
        REAL["create_collection"].bind(self, *a, **kw)
        self.calls.append(("create_collection", kw.get("body", {}).get("properties", {}).get("qualifiedName")))
        return f"coll-{len(self.calls)}"

    def add_to_collection(self, *a, **kw):
        REAL["add_to_collection"].bind(self, *a, **kw)
        self.calls.append(("add_to_collection", a[0], a[1]))

    def attach_collection(self, *a, **kw):
        REAL["attach_collection"].bind(self, *a, **kw)
        self.calls.append(("attach_collection", a[0], a[1]))

    def create_project(self, *a, **kw):
        REAL["create_project"].bind(self, *a, **kw)
        self.calls.append(("create_project",))
        return "pg-new"

    def get_project_by_guid(self, *a, **kw):
        REAL["get_project_by_guid"].bind(self, *a, **kw)
        return None


def test_the_fakes_reject_what_real_pyegeria_rejects():
    """A guard on the fakes themselves (passes before and after Brief Z3 by design)."""
    with pytest.raises(TypeError):
        FaithfulEgeria().get_user_account(user_id="x")                 # platform_name is required
    with pytest.raises(TypeError):
        FakeCollections().add_to_collection("c")                         # element_guid is required


def _install(monkeypatch, zones=()) -> FaithfulEgeria:
    fake = FaithfulEgeria()
    install(monkeypatch, zones=zones)
    monkeypatch.setattr("resource_explorer.zone_access._metadata_expert", lambda: fake)
    monkeypatch.setattr("resource_explorer.zone_access._security_officer", lambda: fake)
    monkeypatch.setenv("EXPLORER_PRIVATE_ZONE", SECURED)
    return fake


class Reached(Exception):
    """The route passed its access check and went on to its write."""


def _reached(*a, **k):
    raise Reached()


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "z3.db"))
    assert str(tmp_path) in r.database_url                                    # never the shared registry
    for slug in ("p", "q"):
        r.add(Project(slug=slug, display_name=slug.upper(), github_url=f"https://github.com/x/{slug}",
                      description=""))
        r.set_egeria_asset_guid(slug, f"g-{slug}")
    return r


@pytest.fixture
def web(registry, monkeypatch):
    """`who["identity"]` False is a request with no caller identity (what `curation_access` calls anonymous)."""
    who = {"user_id": USER, "role": "user", "identity": True}
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    claims = lambda request: {"user_id": who["user_id"], "egeria_token": f"tok-{who['user_id']}",  # noqa: E731
                              "role": who["role"]}
    monkeypatch.setattr("resource_explorer.auth.get_current_user", claims)
    from resource_explorer import auth as _auth

    real_identity = _auth.identity_from_claims
    monkeypatch.setattr("resource_explorer.auth.identity_from_claims",
                        lambda c: real_identity(c) if who["identity"] else None)
    from resource_explorer.web.app import app

    return TestClient(app), who


@pytest.fixture
def decisions(monkeypatch):
    """Every decision `curation_access` makes, in order (proves the route asked THE function)."""
    from resource_explorer.workflows import curate as wc

    seen: list = []
    real = wc.curation_access

    def spy(*a, **k):
        d = real(*a, **k)
        seen.append(d)
        return d
    monkeypatch.setattr(wc, "curation_access", spy)
    return seen


# ── the route table ──────────────────────────────────────────────────────────────────────────────────────

#: A private investigation, not yet in Egeria: promote creates its Project in [SECURED (the private zone), OWNER].
PRIVATE_UNBOUND = {"slug": "i", "display_name": "I", "project_classification": "PersonalProject",
                   "created_by": OWNER, "egeria_binding": "egeria", "egeria_project_guid": ""}
#: A bound investigation whose Project is "pg".
BOUND = {"slug": "i", "display_name": "I", "project_classification": "PersonalProject", "created_by": OWNER,
         "egeria_binding": "egeria", "egeria_project_guid": "pg", "status": "open"}
WORK_LIST = {"slug": "w", "display_name": "W", "entity_type": "repo", "stored_entity_type": "repo",
             "kinds": {"repo": 1}, "egeria_guid": "wg", "members": [{"entity_slug": "p", "entity_type": "repo"}]}

INV = "resource_explorer.registry.ProjectRegistry.get_investigation"
PUB = "resource_explorer.surveyors.egeria_investigation_publisher.EgeriaInvestigationPublisher"

# (name, method, path, body, the zoned element ("" = the creation zones), {patch target: replacement})
ROUTES = [
    ("investigation promote", "post", "/api/investigations/i/promote", None, "",
     {INV: lambda self, slug: dict(PRIVATE_UNBOUND), f"{PUB}.promote": _reached}),
    ("investigation bind", "put", "/api/investigations/i/egeria-project",
     {"status": "linked", "egeria_project_guid": "pg"}, "pg",
     {INV: lambda self, slug: dict(PRIVATE_UNBOUND),
      "resource_explorer.registry.ProjectRegistry.investigation_working_set_slug": lambda self, slug: "",
      "resource_explorer.registry.ProjectRegistry.set_investigation_egeria_project": _reached}),
    ("investigation reclassify", "post", "/api/investigations/i/reclassify",
     {"project_classification": "StudyProject"}, "pg",
     {INV: lambda self, slug: dict(BOUND),
      "resource_explorer.surveyors.investigation_reclassifier.InvestigationReclassifier._move_zones": _reached}),
    ("investigation relink", "post", "/api/investigations/i/relink-members", None, "cg",
     {INV: lambda self, slug: dict(BOUND),
      "resource_explorer.registry.ProjectRegistry.investigation_working_set_slug": lambda self, slug: "ws-i",
      "resource_explorer.registry.ProjectRegistry.get_working_set":
      lambda self, ws: {"slug": ws, "egeria_collection_guid": "cg"},
      f"{PUB}.relink_members": _reached}),
    ("investigation sync", "post", "/api/investigations/i/sync-egeria", None, "pg",
     {INV: lambda self, slug: dict(BOUND), f"{PUB}.sync_project_properties": _reached}),
    ("work-list publish", "post", "/api/work-lists/w/publish", None, "wg",
     {"resource_explorer.work_lists.WorkLists.get": lambda self, slug: dict(WORK_LIST),
      "resource_explorer.egeria_outbox._default_clients": _reached}),
    ("work-list batch", "post", "/api/work-lists/runs/batch",
     {"analysis_id": "a1", "entity_slugs": ["p"], "entity_type": "repo"}, "g-p",
     {"resource_explorer.workflows.analysis.resolve_analysis_plan": lambda aid, et: None,
      "resource_explorer.registry.ProjectRegistry.has_assigned_egeria_project": lambda self, et, s: True,
      "resource_explorer.registry.ProjectRegistry.enqueue_run": _reached}),
    ("RFA status (ToDo update)", "patch", "/api/activity/rfas/e1::0", {"status": "deferred"}, "td",
     {"resource_explorer.registry.ProjectRegistry.get_activity": lambda self, eid: {"id": eid},
      "resource_explorer.registry.ProjectRegistry.get_rfa_action":
      lambda self, rid: {"id": rid, "egeria_todo_guid": "td"},
      "resource_explorer.registry.ProjectRegistry.upsert_rfa_action": _reached}),
]
IDS = [r[0] for r in ROUTES]


def _zone(fake, guid: str, grant: list[str]) -> None:
    """The element (or, with no guid, the creation zones' private zone) in SECURED, whose control grants `grant`."""
    if guid:
        fake.elements[guid] = _element(guid, [SECURED])
    fake.controls[SECURED] = {"associatedSecurityList": {"DEFAULT": grant}}


def _expect_reached(client, method, path, body) -> None:
    """The route went past its check to the write. The work-list publish route turns any failure into a 502 with
    the exception's words, so the sentinel arrives there as that answer instead of raising."""
    try:
        r = _call(client, method, path, body)
    except Reached:
        return
    assert r.status_code == 502 and r.json()["detail"].startswith("Reached"), r.text


def _call(client, method, path, body):
    kw = {"json": body} if body is not None else {}
    return getattr(client, method)(path, **kw)


def _patch(monkeypatch, patches):
    for target, fn in patches.items():
        monkeypatch.setattr(target, fn)


@pytest.mark.parametrize("name,method,path,body,guid,patches", ROUTES, ids=IDS)
class TestEveryRoute:
    def test_no_caller_identity_is_denied(self, web, monkeypatch, decisions, name, method, path, body, guid, patches):
        client, who = web
        _install(monkeypatch)
        _patch(monkeypatch, patches)
        who["identity"] = False
        r = _call(client, method, path, body)               # never Reached
        assert r.status_code == 403, r.text
        assert r.json()["detail"].startswith("sign in to curate")
        assert decisions and decisions[-1].basis == "anonymous"

    def test_a_zoned_element_with_a_grant_is_allowed(self, web, monkeypatch, decisions,
                                                     name, method, path, body, guid, patches):
        client, who = web
        fake = _install(monkeypatch)
        _zone(fake, guid, [USER])                             # the user's own id is on the list
        _patch(monkeypatch, patches)
        _expect_reached(client, method, path, body)
        assert all(d.allowed for d in decisions)              # (a work list's unzoned member answers "no zones")
        assert any(d.basis == "zone_granted" for d in decisions)
        assert ("control", SECURED) in fake.calls

    def test_a_zoned_element_without_a_grant_is_refused_with_its_word(self, web, monkeypatch, decisions,
                                                                      name, method, path, body, guid, patches):
        client, who = web
        fake = _install(monkeypatch)
        _zone(fake, guid, ["dbas"])
        _patch(monkeypatch, patches)
        r = _call(client, method, path, body)
        assert r.status_code == 403, r.text
        assert r.json()["detail"].startswith(f"zone {SECURED} does not grant {USER} "), r.json()
        assert r.json()["detail"].endswith(" in Egeria")

    def test_an_unreadable_check_denies_with_the_reason(self, web, monkeypatch, decisions,
                                                        name, method, path, body, guid, patches):
        client, who = web
        fake = _install(monkeypatch)
        _zone(fake, guid, [USER])
        if guid:
            fake.fail_element = guid                          # the element read fails
        else:
            fake.fail_control = SECURED                       # the control read fails
        _patch(monkeypatch, patches)
        if name.startswith("RFA"):
            # Round 2 (F2): an RFA change stays local-first when Egeria cannot be asked: recorded, not synced
            # (tests/test_zone_access_z3_round2.py::TestRfaUnreadableStaysLocal has the words).
            _expect_reached(client, method, path, body)
            assert decisions[-1].basis == "unreadable"
            return
        r = _call(client, method, path, body)
        assert r.status_code == 403, r.text
        assert r.json()["detail"].startswith("could not check access in Egeria ("), r.json()

    def test_a_portal_curator_is_allowed_whatever_the_zones(self, web, monkeypatch, decisions,
                                                             name, method, path, body, guid, patches):
        client, who = web
        fake = _install(monkeypatch)
        _zone(fake, guid, ["dbas"])
        who["role"] = "curator"
        _patch(monkeypatch, patches)
        _expect_reached(client, method, path, body)
        assert decisions[-1].basis == "portal_role"
        assert fake.calls == []                               # nothing read for an override


def test_operations_are_the_ones_egeria_checks_for_each_write(registry):
    """CREATE/CLASSIFY/ATTACH(+PUBLISH) for a new Project; ATTACH both ends of a membership; UPDATE_PROPERTIES for a
    rename and an existing ToDo, CREATE for a new one; PUBLISH/CLASSIFY/DECLASSIFY for a reclassify."""
    from resource_explorer.workflows import egeria_work_access as ewa

    assert ewa.promote_writes({"project_classification": "StudyProject"})[0].operations == (
        "CREATE", "CLASSIFY", "ATTACH")
    assert ewa.promote_writes({"project_classification": "PersonalProject", "created_by": OWNER})[0].operations == (
        "CREATE", "CLASSIFY", "ATTACH", "PUBLISH")
    assert ewa.sync_writes({"egeria_project_guid": "pg"})[0].operations == ("UPDATE_PROPERTIES",)
    assert ewa.sync_writes({}) == []
    assert ewa.todo_writes({"egeria_todo_guid": "td"})[0].operations == ("UPDATE_PROPERTIES",)
    assert ewa.todo_writes(None)[0].operations == ("CREATE",)
    assert ewa.reclassify_project_write("pg", "PersonalProject").operations == ("PUBLISH", "CLASSIFY", "DECLASSIFY")
    assert ewa.work_list_collection_write("").operations == ("CREATE", "ATTACH")
    assert ewa.work_list_collection_write("wg").operations == ("ATTACH",)
    assert ewa.bind_writes(registry, "i", "unset", "pg") == []        # clearing writes nothing to Egeria


# ── batches and member sets: a refused member is skipped with its words, the rest proceed ─────────────────

def _signed_in(monkeypatch, user=USER, role="user"):
    from resource_explorer.a2a_auth import CallerIdentity

    monkeypatch.setattr("resource_explorer.workflows.curate._caller_identity",
                        lambda: CallerIdentity(user_id=user, egeria_token="t", auth_source="app-jwt", role=role))


class TestBatch:
    def test_a_batch_with_one_refused_member_runs_the_rest_and_the_next_read_shows_them(self, web, registry,
                                                                                         monkeypatch):
        client, _ = web
        fake = _install(monkeypatch)
        _zone(fake, "g-p", ["dbas"])                          # p refused, q unzoned (open)
        monkeypatch.setattr("resource_explorer.workflows.analysis.resolve_analysis_plan", lambda aid, et: None)
        monkeypatch.setattr(ProjectRegistry, "has_assigned_egeria_project", lambda self, et, s: True)
        r = client.post("/api/work-lists/runs/batch",
                        json={"analysis_id": "a1", "entity_slugs": ["p", "q"], "entity_type": "repo"})
        assert r.status_code == 200, r.text
        out = r.json()
        assert [x["entity_slug"] for x in out["runs"]] == ["q"]
        assert out["not_permitted"] == [
            {"entity_slug": "p", "words": f"not permitted · zone {SECURED} does not grant {USER} update in Egeria"}]
        progress = client.get(f"/api/work-lists/runs/sets/{out['set_id']}").json()
        assert [x["entity_slug"] for x in progress["runs"]] == ["q"]
        assert [x["requested_by"] for x in registry.list_runs(kind="analysis_run", limit=5)] == [USER]

    def test_a_member_whose_run_writes_nothing_to_egeria_is_not_checked(self, web, monkeypatch):
        client, _ = web
        fake = _install(monkeypatch)
        _zone(fake, "g-p", ["dbas"])
        monkeypatch.setattr("resource_explorer.workflows.analysis.resolve_analysis_plan", lambda aid, et: None)
        monkeypatch.setattr(ProjectRegistry, "has_assigned_egeria_project", lambda self, et, s: False)
        r = client.post("/api/work-lists/runs/batch",
                        json={"analysis_id": "a1", "entity_slugs": ["p"], "entity_type": "repo"})
        assert r.status_code == 200 and r.json()["not_permitted"] == []
        assert fake.calls == []


class _Clients:
    def __init__(self, cm):
        self.cm = cm

    def require(self, name):
        assert name == "collection_manager"
        return self.cm


class TestWorkListPublish:
    def test_one_refused_member_is_not_queued_and_the_rest_are(self, web, registry, monkeypatch):
        """End to end through the route, faking only the Egeria clients: the next read (the outbox) holds the
        permitted member's membership, recorded for the person who pressed publish."""
        from resource_explorer.work_lists import WorkLists

        client, _ = web
        fake = _install(monkeypatch)
        _zone(fake, "g-p", ["dbas"])
        cm = FakeCollections()
        monkeypatch.setattr("resource_explorer.egeria_outbox._default_clients", lambda who=None: (_Clients(cm), real_lookup()))
        WorkLists(registry).create("W", ["p", "q"], entity_type="repo", created_by=USER)
        slug = WorkLists(registry).list_all()[0]["slug"]
        r = client.post(f"/api/work-lists/{slug}/publish")
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["queued_members"] == 1
        assert out["not_permitted"] == [
            {"entity_slug": "p", "words": f"not permitted · zone {SECURED} does not grant {USER} attach in Egeria"}]
        rows = registry.list_outbox_elements(entity_slug=slug, limit=10)
        assert [r_["qualified_name"].rsplit("::", 1)[-1] for r_ in rows] == ["g-q"]
        import json as _json

        assert _json.loads(rows[0]["payload_json"])["by"] == USER

    def test_an_existing_collection_with_every_member_refused_is_a_403_and_nothing_is_written(self, web, registry,
                                                                                               monkeypatch):
        client, _ = web
        fake = _install(monkeypatch)
        _zone(fake, "g-p", ["dbas"])
        monkeypatch.setattr("resource_explorer.work_lists.WorkLists.get", lambda self, slug: dict(WORK_LIST))
        monkeypatch.setattr("resource_explorer.egeria_outbox._default_clients", _reached)
        r = client.post("/api/work-lists/w/publish")
        assert r.status_code == 403
        assert r.json()["detail"] == f"zone {SECURED} does not grant {USER} attach in Egeria"
        assert registry.list_outbox_elements(entity_slug="w", limit=10) == []


class TestInvestigationMembers:
    def _bound(self, registry, monkeypatch):
        inv = registry.create_investigation("Inv", project_classification="StudyProject",
                                            egeria_project_guid="pg")
        slug = inv["slug"]
        ws = registry.get_or_create_working_set(slug)
        registry.set_working_set_egeria_collection(ws["slug"], "cg")
        for m in ("p", "q"):
            registry.add_working_set_member(ws["slug"], "repo", m)
        return slug

    def test_relink_links_the_permitted_member_and_names_the_refused_one(self, web, registry, monkeypatch):
        """End to end through the route, faking only the Egeria clients (the publisher's CollectionManager): the
        next read (link-status) shows q linked and p not."""
        client, _ = web
        fake = _install(monkeypatch)
        _zone(fake, "g-p", ["dbas"])
        slug = self._bound(registry, monkeypatch)
        cm = FakeCollections()
        monkeypatch.setattr(f"{PUB}._managers", lambda self: (cm, cm))
        r = client.post(f"/api/investigations/{slug}/relink-members")
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["members_linked"] == ["q"]
        assert [(m["entity_slug"], m["reason"]) for m in out["members_unlinkable"]] == [
            ("p", f"not permitted · zone {SECURED} does not grant {USER} attach in Egeria")]
        assert cm.calls == [("add_to_collection", "cg", "g-q")]
        status = {m["entity_slug"]: m["state"] for m in client.get(f"/api/investigations/{slug}/link-status").json()["members"]}
        assert status == {"p": "not_linked", "q": "linked"}

    def test_relink_with_every_member_refused_is_a_403(self, web, registry, monkeypatch):
        client, _ = web
        fake = _install(monkeypatch)
        _zone(fake, "g-p", ["dbas"])
        fake.elements["g-q"] = _element("g-q", [SECURED])
        slug = self._bound(registry, monkeypatch)
        cm = FakeCollections()
        monkeypatch.setattr(f"{PUB}._managers", lambda self: (cm, cm))
        r = client.post(f"/api/investigations/{slug}/relink-members")
        assert r.status_code == 403
        assert r.json()["detail"] == f"zone {SECURED} does not grant {USER} attach in Egeria"
        assert cm.calls == []

    def test_promote_creates_the_project_and_skips_the_refused_member(self, web, registry, monkeypatch):
        client, _ = web
        fake = _install(monkeypatch)
        _zone(fake, "g-p", ["dbas"])
        inv = registry.create_investigation("Inv", project_classification="StudyProject")
        ws = registry.get_or_create_working_set(inv["slug"])
        for m in ("p", "q"):
            registry.add_working_set_member(ws["slug"], "repo", m)
        cm = FakeCollections()
        monkeypatch.setattr(f"{PUB}._managers", lambda self: (cm, cm))
        r = client.post(f"/api/investigations/{inv['slug']}/promote")
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["project_guid"] == "pg-new" and out["members_linked"] == ["q"]
        assert [(m["entity_slug"], m["reason"]) for m in out["members_unlinkable"]] == [
            ("p", f"not permitted · zone {SECURED} does not grant {USER} attach in Egeria")]
        assert ("add_to_collection", out["collection_guid"], "g-q") in cm.calls
        assert not any(c[0] == "add_to_collection" and c[2] == "g-p" for c in cm.calls)
        assert registry.get_investigation(inv["slug"])["egeria_project_guid"] == "pg-new"


# ── run-time re-checks: before each queued or retried write ─────────────────────────────────────────────

class TestRunTime:
    def _queue(self, registry, by: str | None):
        payload = {"collection_guid": "wg", "member_guid": "g-q", "member_entity_type": "repo",
                   "member_entity_slug": "q", **({"by": by} if by else {})}
        return registry.enqueue_outbox_element("work_list", "w", "collection_membership",
                                               "CollectionMembership::wg::g-q", payload)

    def _drain(self, registry):
        from resource_explorer.egeria_outbox import OutboxClients, drain_outbox

        cm = FakeCollections()
        summary = drain_outbox(registry, OutboxClients(collection_manager=cm), lambda qn: "", limit=5)
        return summary, cm

    def test_a_queued_membership_is_rechecked_as_its_requester(self, registry, monkeypatch):
        """The press was permitted; the zone changed before the drain: refused, nothing sent, the row's words."""
        fake = _install(monkeypatch)
        self._queue(registry, USER)
        _zone(fake, "g-q", ["dbas"])
        summary, cm = self._drain(registry)
        assert cm.calls == [] and summary.get("not_permitted") == 1
        row = registry.list_outbox_elements(entity_slug="w", limit=5)[0]
        assert row["last_error"] == f"not permitted · zone {SECURED} does not grant {USER} attach in Egeria"

    def test_the_requester_with_a_grant_is_applied(self, registry, monkeypatch):
        fake = _install(monkeypatch)
        self._queue(registry, USER)
        _zone(fake, "g-q", [USER])
        summary, cm = self._drain(registry)
        assert cm.calls == [("add_to_collection", "wg", "g-q")]
        assert ("control", SECURED) in fake.calls and ("account", USER) in fake.calls   # judged as the requester

    def test_a_row_nobody_asked_for_is_applied_only_with_no_zone_in_use(self, registry, monkeypatch):
        from resource_explorer.workflows.curate import NO_REQUESTER_WORDS

        fake = _install(monkeypatch)
        self._queue(registry, None)
        summary, cm = self._drain(registry)
        assert cm.calls == [("add_to_collection", "wg", "g-q")]           # no zone: open
        fake.elements["g-q"] = _element("g-q", [SECURED])
        registry.enqueue_outbox_element("work_list", "w2", "collection_membership",
                                        "CollectionMembership::wg::g-q2", {"collection_guid": "wg",
                                                                           "member_guid": "g-q"})
        summary, cm = self._drain(registry)
        assert cm.calls == []
        row = registry.list_outbox_elements(entity_slug="w2", limit=5)[0]
        assert row["last_error"] == f"not permitted · {NO_REQUESTER_WORDS}, and zone {SECURED} is in use"

    def test_a_curator_press_is_honoured_by_the_drain_only_when_the_verified_caller_had_the_role(self, registry,
                                                                                               monkeypatch):
        from resource_explorer.egeria_outbox import requester_fields

        _install(monkeypatch)
        monkeypatch.setattr("resource_explorer.run_queue.requested_by", lambda: USER)
        _signed_in(monkeypatch, role="curator")
        assert requester_fields() == {"by": USER, "portal_role": True}
        _signed_in(monkeypatch, role="user")
        assert requester_fields() == {"by": USER}

    def test_an_rfa_retry_with_nobody_recorded_follows_the_no_requester_rule(self, registry, monkeypatch):
        from resource_explorer import rfa_egeria_sync

        fake = _install(monkeypatch)
        fake.elements["td"] = _element("td", [SECURED])
        rows = [{"id": "e1::0", "egeria_todo_guid": "td"}, {"id": "e2::0", "egeria_todo_guid": ""}]
        synced, errors = [], {}
        monkeypatch.setattr(registry, "list_unsynced_rfa_actions", lambda: rows)
        monkeypatch.setattr(registry, "list_unsynced_rfa_notes", lambda: [])
        monkeypatch.setattr(registry, "list_synced_rfa_actions", lambda: [])
        monkeypatch.setattr(registry, "mark_rfa_sync_error", lambda rid, why: errors.__setitem__(rid, why))
        monkeypatch.setattr(rfa_egeria_sync, "sync_rfa_action", lambda reg, row: synced.append(row["id"]))
        rfa_egeria_sync.reconcile_rfa_actions(registry)
        assert synced == ["e2::0"]                                          # a new ToDo: RE writes no zone
        assert errors["e1::0"].startswith("not permitted · no person asked for this run, and zone sales-zone")


# ── reclassify: every element it moves is checked before anything moves ─────────────────────────────────

class TestReclassify:
    def _run(self, registry, monkeypatch, frm: str, to: str):
        from resource_explorer.surveyors import investigation_reclassifier as ir
        from resource_explorer.workflows.egeria_work_access import access_words

        inv = {"slug": "i", "display_name": "I", "project_classification": frm, "created_by": USER,
               "egeria_project_guid": "pg"}
        monkeypatch.setattr(registry, "get_investigation", lambda slug: dict(inv))
        monkeypatch.setattr(ir.InvestigationReclassifier, "_report_guids", lambda self, slug: (["r1", "r2"], []))
        moved, applied = [], []
        monkeypatch.setattr(ir.InvestigationReclassifier, "_move_zones",
                            lambda self, g, target, owner="": moved.append(g) or (True, ""))
        monkeypatch.setattr(ir.InvestigationReclassifier, "_move_kind_classification",
                            lambda self, *a: (True, ""))
        monkeypatch.setattr(registry, "set_investigation_classification",
                            lambda slug, to, hypothesis="": applied.append(to))
        monkeypatch.setattr("resource_explorer.egeria_identity.private_zone_status", lambda: {"status": "ok"})
        monkeypatch.setattr("resource_explorer.egeria_identity.private_zone_is_enforced", lambda: True)
        res = ir.InvestigationReclassifier(registry).reclassify(
            "i", to, access=lambda ws: access_words(registry, ws, entity_type="investigation", slug="i"))
        return res, moved, applied

    def test_a_tightening_with_a_refused_report_moves_nothing(self, registry, monkeypatch):
        fake = _install(monkeypatch)
        _zone(fake, "r1", ["dbas"])
        _signed_in(monkeypatch)
        res, moved, applied = self._run(registry, monkeypatch, "StudyProject", "PersonalProject")
        assert moved == [] and applied == []
        assert res.reports_unmovable == [{"guid": "r1", "reason": f"not permitted · zone {SECURED} does not grant "
                                                                  f"{USER} publish (zone change) in Egeria"}]

    def test_a_loosening_moves_the_rest_and_names_the_refused_report(self, registry, monkeypatch):
        fake = _install(monkeypatch)
        _zone(fake, "r1", ["dbas"])
        _signed_in(monkeypatch)
        res, moved, applied = self._run(registry, monkeypatch, "PersonalProject", "StudyProject")
        assert moved == ["pg", "r2"]
        assert [u["guid"] for u in res.reports_unmovable] == ["r1"]

    def test_a_refused_project_changes_nothing(self, registry, monkeypatch):
        fake = _install(monkeypatch)
        _zone(fake, "pg", ["dbas"])
        _signed_in(monkeypatch)
        res, moved, applied = self._run(registry, monkeypatch, "PersonalProject", "StudyProject")
        assert moved == [] and applied == []
        assert res.not_permitted == (f"not permitted · zone {SECURED} does not grant {USER} publish (zone change) "
                                     "in Egeria")


# ── the CLI and resync ask the same function ────────────────────────────────────────────────────────────

class TestSameFunction:
    def test_the_cli_has_no_investigation_work_list_or_todo_write_of_its_own(self):
        """The Brief Z3 inventory: none of these writes has a CLI door (only `egeria resync`'s relink, below). A
        tripwire: it fails the day the CLI gains one, which must then ask `egeria_work_access` like the web."""
        import resource_explorer.cli.main as cli
        import resource_explorer.cli.runs_commands as rc

        src = inspect.getsource(cli) + inspect.getsource(rc)
        for word in ("EgeriaInvestigationPublisher", "InvestigationReclassifier", "WorkLists", "enqueue_batch",
                     "sync_rfa_action", "upsert_rfa_action", "create_my_todo"):
            assert word not in src, word

    def test_resync_relink_asks_the_same_member_check_with_both_ends(self, registry, monkeypatch):
        from resource_explorer.egeria_resync import EgeriaResync
        from resource_explorer.workflows import egeria_work_access as ewa

        asked = []
        real = ewa.investigation_member_refusal
        monkeypatch.setattr(ewa, "investigation_member_refusal",
                            lambda reg, slug, **kw: asked.append((slug, kw)) or real(reg, slug, **kw))
        monkeypatch.setattr(registry, "list_investigations", lambda *a, **k: [{"slug": "i", "egeria_project_guid": "pg"}])
        from resource_explorer.surveyors import egeria_investigation_publisher as eip

        monkeypatch.setattr(eip.EgeriaInvestigationPublisher, "relink_members",
                            lambda self, slug, refusal=None: eip.PromotionResult())
        EgeriaResync(registry=registry)._do_relink_investigation_members()
        assert asked == [("i", {"unattended": True})]
