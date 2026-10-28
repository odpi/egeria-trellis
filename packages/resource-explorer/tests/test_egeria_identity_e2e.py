"""Brief I, per converted area: which identity reaches the pyegeria boundary.

Every test drives RE's real path and fakes ONLY the pyegeria client classes (patched where RE
imports them from). The fake records, per client built, the Egeria user it was built for, the
bearer token set on it, and whether it minted its own (a password exchange: the daemon). The
assertion is on that record — "the person's token reached Egeria" or "the daemon minted" — never
on an RE-internal flag.

Nothing here contacts Egeria.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time

import pytest


@pytest.fixture(autouse=True)
def _zone_access_open(monkeypatch):
    """Brief Z2: this write path asks `curation_access` (the one decision). These tests are about something else,
    so no zone is in use: the access reads are faked open at the client boundary (`tests/zone_fakes.open_access`)."""
    from tests.zone_fakes import open_access

    return open_access(monkeypatch, caller_if_none="zone2-tester")

PERSON, PERSON_TOKEN = "dana", "tok-dana-signed-in"
DAEMON_USER, DAEMON_PW = "re-daemon", "pw-daemon-not-a-real-one"


class Boundary:
    """What reached pyegeria: one dict per client constructed."""

    def __init__(self):
        self.clients: list[dict] = []
        self.calls: list[tuple] = []

    def users_and_tokens(self):
        return {(c["cls"], c["user"], c["token"], c["minted"]) for c in self.clients}

    def fake(self, name, **methods):
        boundary = self

        class Fake:
            def __init__(self, *args, **kwargs):
                self._rec = {"cls": name, "server": args[0] if args else "", "user": args[2] if len(args) > 2 else "",
                             "token": None, "minted": False}
                boundary.clients.append(self._rec)

            def set_bearer_token(self, token):
                self._rec["token"] = token

            def create_egeria_bearer_token(self, *a, **k):
                self._rec["minted"] = True
                self._rec["token"] = f"minted-for-{self._rec['user']}"
                return self._rec["token"]

            def __getattr__(self, attr):
                if attr in methods:
                    fn = methods[attr]

                    def call(*a, **k):
                        boundary.calls.append((name, attr, self._rec["user"], self._rec["token"]))
                        return fn(*a, **k)
                    return call
                raise AttributeError(attr)

        Fake.__name__ = name
        return Fake


@pytest.fixture
def boundary(monkeypatch):
    b = Boundary()
    # The daemon's configured credential, on a COPY of the config (never the shared one).
    from resource_explorer import config

    cfg = config.get_config().model_copy(deep=True)
    cfg.egeria.user_id, cfg.egeria.user_password = DAEMON_USER, DAEMON_PW
    monkeypatch.setattr(config, "get_config", lambda: cfg)
    return b


@pytest.fixture
def reg(tmp_path, monkeypatch):
    from resource_explorer.registry import ProjectRegistry, Project

    r = ProjectRegistry(db_path=str(tmp_path / "e2e.db"))
    r.add(Project(slug="p", display_name="P", github_url="https://github.com/x/p"))
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None, **k: setattr(self, "__dict__", r.__dict__) or None)
    return r


def _token(user=PERSON, egeria_token=PERSON_TOKEN):
    from resource_explorer.auth import create_access_token

    return {"Authorization": "Bearer " + create_access_token(user_id=user, egeria_token=egeria_token)}


@pytest.fixture
def client(reg):
    from fastapi.testclient import TestClient

    from resource_explorer.web.app import app

    return TestClient(app)


# ── bulk ops: Delete in Egeria (a route) → the signed-in Caller ───────────────

def test_bulk_delete_in_egeria_reaches_egeria_with_the_callers_token(boundary, reg, client, monkeypatch):
    reg.set_egeria_asset_guid("p", "guid-p")
    monkeypatch.setattr("pyegeria.AssetMaker", boundary.fake("AssetMaker", delete_asset=lambda g: None))
    r = client.post("/api/egeria/linkage/delete-in-egeria",
                    json={"targets": [{"entity_type": "repo", "slug": "p"}], "dry_run": False}, headers=_token())
    assert r.status_code == 200, r.text
    assert r.json()["succeeded"] == 1
    assert boundary.users_and_tokens() == {("AssetMaker", PERSON, PERSON_TOKEN, False)}
    assert boundary.calls == [("AssetMaker", "delete_asset", PERSON, PERSON_TOKEN)]


def test_bulk_delete_with_no_caller_is_401_and_builds_no_client(boundary, reg, client, monkeypatch):
    reg.set_egeria_asset_guid("p", "guid-p")
    monkeypatch.setattr("pyegeria.AssetMaker", boundary.fake("AssetMaker", delete_asset=lambda g: None))
    r = client.post("/api/egeria/linkage/delete-in-egeria",
                    json={"targets": [{"entity_type": "repo", "slug": "p"}], "dry_run": False})
    assert r.status_code == 401, r.text
    assert boundary.clients == [], "no caller must never fall back to the service account"


def test_bulk_delete_with_an_expired_sign_in_is_401_with_the_sentence(boundary, reg, client, monkeypatch):
    reg.set_egeria_asset_guid("p", "guid-p")
    monkeypatch.setattr("pyegeria.AssetMaker", boundary.fake("AssetMaker", delete_asset=lambda g: None))
    headers = _token()
    # The Egeria token's own exp has passed while the app session still stands (the factory reads
    # the Egeria token's exp; patched here, after the app JWT was minted).
    monkeypatch.setattr("resource_explorer.egeria_clients._token_expiry", lambda tok: int(time.time()) - 5)
    r = client.post("/api/egeria/linkage/delete-in-egeria",
                    json={"targets": [{"entity_type": "repo", "slug": "p"}], "dry_run": False}, headers=headers)
    assert r.status_code == 401
    assert r.json()["detail"] == "your Egeria sign-in expired; sign in again"
    assert boundary.clients == []


def test_a_refusal_is_reported_as_egerias_and_never_retried_as_anyone_else(boundary, reg, client, monkeypatch):
    reg.set_egeria_asset_guid("p", "guid-p")

    class Refused(Exception):
        related_http_code = 403

    def refuse(guid):
        raise Refused("OPEN-METADATA-SECURITY-403-007 User dana is not authorized to issue operation "
                      "Delete on Asset guid-p. More text here.")
    monkeypatch.setattr("pyegeria.AssetMaker", boundary.fake("AssetMaker", delete_asset=refuse))
    r = client.post("/api/egeria/linkage/delete-in-egeria",
                    json={"targets": [{"entity_type": "repo", "slug": "p"}], "dry_run": False}, headers=_token())
    detail = r.json()["details"][0]
    assert detail["result"] == "failed"
    assert detail["message"].startswith("refused by Egeria: OPEN-METADATA-SECURITY-403-007 User dana")
    assert len(boundary.clients) == 1 and boundary.clients[0]["user"] == PERSON
    assert not any(c["minted"] for c in boundary.clients), "never retried as the service account"


# ── linkage recheck (a route) → the signed-in Caller ─────────────────────────

def test_linkage_recheck_route_reads_as_the_caller(boundary, reg, client, monkeypatch):
    reg.set_egeria_asset_guid("p", "guid-p")
    monkeypatch.setattr("pyegeria.omvs.metadata_expert.MetadataExpert", boundary.fake(
        "MetadataExpert", get_metadata_element_by_guid=lambda g: {"elementHeader": {"guid": g}}))
    r = client.post("/api/egeria/linkage/recheck", headers=_token())
    assert r.status_code == 200, r.text
    assert boundary.users_and_tokens() == {("MetadataExpert", PERSON, PERSON_TOKEN, False)}


# ── read routes: documentation-source read-back, project search → Caller ─────

def test_doc_source_read_back_route_reads_as_the_caller(boundary, reg, client, monkeypatch):
    reg.set_egeria_asset_guid("p", "guid-p")
    monkeypatch.setattr("resource_explorer.web.routes.doc_sources._publish_status",
                        lambda *a, **k: {"is_published": True, "note": ""})
    monkeypatch.setattr("pyegeria.omvs.metadata_expert.MetadataExpert", boundary.fake(
        "MetadataExpert", get_related_metadata_elements=lambda *a, **k: "No elements found"))
    r = client.get("/api/doc-sources/repo/p", headers=_token())
    assert r.status_code == 200, r.text
    assert boundary.users_and_tokens() == {("MetadataExpert", PERSON, PERSON_TOKEN, False)}


def test_project_search_route_reads_as_the_caller(boundary, reg, client, monkeypatch):
    from resource_explorer.config import get_config

    monkeypatch.setenv("EGERIA_PLATFORM_URL", get_config().egeria.platform_url)   # the allowed one
    monkeypatch.setattr("pyegeria.ProjectManager", boundary.fake("ProjectManager", find_projects=lambda *a, **k: []))
    r = client.get("/api/project-context/search/candidates?q=x", headers=_token())
    assert r.status_code == 200, r.text
    assert boundary.users_and_tokens() == {("ProjectManager", PERSON, PERSON_TOKEN, False)}


# ── egeria_resync: the scheduled pass → Daemon(RESYNC) ────────────────────────

def test_the_resync_loop_runs_as_the_daemon(boundary, reg, monkeypatch):
    from resource_explorer import egeria_resync

    for cls in ("AssetMaker", "ProjectManager", "CollectionManager", "ClassificationExplorer"):
        monkeypatch.setattr(f"pyegeria.{cls}", boundary.fake(cls))
    monkeypatch.setattr("resource_explorer.omsecrets_reproject.heal_missing", lambda: None)
    stop = threading.Event()
    seen = {}

    def one_pass(registry=None):
        stop.set()                                   # exactly one pass, whatever happens below
        ok, why = egeria_resync.EgeriaResync(registry=reg)._connect()
        seen["connected"] = (ok, why)
        try:
            from resource_explorer.egeria_clients import current_principal

            seen["principal"] = current_principal()
        except Exception as exc:  # noqa: BLE001 - recorded, asserted below
            seen["principal"] = exc
        return {"reachable": True, "applied": {}}

    monkeypatch.setattr(egeria_resync, "scan_and_clear", one_pass)
    egeria_resync._loop(0, stop)
    assert seen["connected"][0], seen["connected"]
    assert seen["principal"].kind == "daemon" and seen["principal"].reason == "egeria_resync"
    users = {(c["cls"], c["user"]) for c in boundary.clients}
    assert users == {(c, DAEMON_USER) for c in
                     ("AssetMaker", "ProjectManager", "CollectionManager", "ClassificationExplorer")}
    assert sum(c["minted"] for c in boundary.clients) == 1, "one token, shared across sub-clients"
    assert {c["token"] for c in boundary.clients} == {f"minted-for-{DAEMON_USER}"}


# ── reachability → Daemon(REACHABILITY), even with a person signed in ─────────

def test_reachability_runs_as_the_daemon_even_from_a_signed_in_request(boundary, monkeypatch, signed_in_caller):
    from resource_explorer import reachability

    monkeypatch.setattr("pyegeria.AutomatedCuration", boundary.fake("AutomatedCuration"))
    monkeypatch.setattr("pyegeria.MetadataExpert", boundary.fake("MetadataExpert"))
    reachability._get_clients()
    assert {(c["cls"], c["user"]) for c in boundary.clients} == {
        ("AutomatedCuration", DAEMON_USER), ("MetadataExpert", DAEMON_USER)}
    assert not any(c["token"] == "tok-test-caller" for c in boundary.clients)


# ── the scheduler tick (RFA sync) → Daemon(SCHEDULER) ─────────────────────────

def test_the_scheduler_tick_runs_rfa_sync_as_the_daemon(boundary, monkeypatch):
    from resource_explorer import rfa_egeria_sync, scheduler

    monkeypatch.setattr("pyegeria.MyProfile", boundary.fake("MyProfile"))
    monkeypatch.setattr("pyegeria.MetadataExpert", boundary.fake("MetadataExpert"))
    for name in ("_run_due", "_drain_egeria_outbox", "_sweep_native_surveys"):
        monkeypatch.setattr(scheduler, name, lambda: None)
    monkeypatch.setattr(scheduler, "_reconcile_rfa_actions", lambda: rfa_egeria_sync._get_clients())
    scheduler._tick()
    assert {(c["cls"], c["user"]) for c in boundary.clients} == {
        ("MyProfile", DAEMON_USER), ("MetadataExpert", DAEMON_USER)}


# ── the outbox: inline drains run as the person; only the background loop is the daemon ─

def _drain_fakes(boundary, monkeypatch):
    for cls in ("AssetMaker", "ExternalReferences"):
        monkeypatch.setattr(f"pyegeria.{cls}", boundary.fake(cls))
    # The outbox's pre-create look-up: a real miss (pyegeria's own answer). A missing method used to be read
    # as a miss too; a look-up that could not be made now creates nothing (re/lookup-failure-never-creates).
    monkeypatch.setattr("pyegeria.AutomatedCuration", boundary.fake(
        "AutomatedCuration", get_guid_for_name=lambda *a, **k: "No elements found"))
    monkeypatch.setattr("pyegeria.CollectionManager", boundary.fake(
        "CollectionManager", add_to_collection=lambda *a, **k: None))
    monkeypatch.setattr("pyegeria.omvs.data_discovery.DataDiscovery", boundary.fake("DataDiscovery"))
    monkeypatch.setattr("pyegeria.omvs.metadata_expert.MetadataExpert", boundary.fake("MetadataExpert"))


def test_an_inline_drain_in_a_persons_request_runs_as_that_person(boundary, reg, monkeypatch, signed_in_caller):
    from resource_explorer.egeria_outbox import drain_outbox

    _drain_fakes(boundary, monkeypatch)
    reg.enqueue_outbox_element("repo", "p", "collection_membership", "CollectionMembership::c::m",
                               {"collection_guid": "c", "member_guid": "m"})
    assert drain_outbox(reg)["done"] == 1
    adds = [c for c in boundary.calls if c[1] == "add_to_collection"]
    assert {(u, t) for _, _, u, t in adds} == {("test-caller", "tok-test-caller")}
    assert not any(c["minted"] for c in boundary.clients), "never the daemon for a person's act"


def test_the_background_drain_loop_runs_as_the_daemon_even_beside_a_person(boundary, reg, monkeypatch, signed_in_caller):
    from resource_explorer import scheduler

    _drain_fakes(boundary, monkeypatch)
    reg.enqueue_outbox_element("repo", "p", "collection_membership", "CollectionMembership::c::m",
                               {"collection_guid": "c", "member_guid": "m"})
    scheduler._drain_egeria_outbox()
    adds = [c for c in boundary.calls if c[1] == "add_to_collection"]
    assert adds and {(u, t) for _, _, u, t in adds} == {(DAEMON_USER, f"minted-for-{DAEMON_USER}")}
    assert not any(c["token"] == "tok-test-caller" for c in boundary.clients)


def test_a_drain_with_no_identity_claims_nothing_and_sends_nothing(boundary, reg, monkeypatch):
    from resource_explorer.egeria_outbox import drain_outbox

    _drain_fakes(boundary, monkeypatch)
    rid = reg.enqueue_outbox_element("repo", "p", "collection_membership", "CollectionMembership::c::m",
                                     {"collection_guid": "c", "member_guid": "m"})
    out = drain_outbox(reg)
    assert out["claimed"] == 0 and "sign in" in out["identity_error"]
    assert boundary.clients == []
    assert reg.get_outbox_element(rid)["status"] == "pending"


# ── the catalog gateway: Curate route → Caller; background drain → daemon; stored creds never ─

def _db_entity(**kw):
    from resource_explorer.registry import DatabaseEntity

    return DatabaseEntity(slug="db", display_name="Shop", db_type="postgresql", host="localhost",
                          port=5442, database_name="shop", **kw)


def test_the_gateway_on_a_curate_route_acts_as_the_caller_and_ignores_stored_credentials(boundary, monkeypatch, signed_in_caller):
    from resource_explorer.catalogue_commit import make_gateway

    monkeypatch.setattr("pyegeria.AssetMaker", boundary.fake("AssetMaker"))
    gw = make_gateway(_db_entity(egeria_user="stored-user", egeria_password="stored-pw"))
    gw._client("AssetMaker")
    assert boundary.users_and_tokens() == {("AssetMaker", "test-caller", "tok-test-caller", False)}


def test_the_background_drain_gateway_uses_the_daemon_even_when_the_entity_stores_egeria_creds(boundary, monkeypatch):
    """Owner's ruling (2026-10-09): the outbox drain is the daemon; an entity's stored Egeria
    user/password are never used."""
    from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as
    from resource_explorer.egeria_outbox import _catalogue_gateway

    monkeypatch.setattr("pyegeria.AssetMaker", boundary.fake("AssetMaker"))

    class Clients:
        catalogue_gateway = None

        def __init__(self, entity):
            self._entity = entity

        def require(self, name):
            entity = self._entity
            return type("R", (), {"get_database": lambda self, slug, allow_unreadable=True: entity})()

    with acting_as(Daemon(DaemonReason.OUTBOX)):                 # the background loop
        _catalogue_gateway(Clients(_db_entity(egeria_user="stored-user", egeria_password="stored-pw")),
                           {"slug": "db"})._client("AssetMaker")
    assert [(c["user"], c["minted"]) for c in boundary.clients] == [(DAEMON_USER, True)]


def test_a_background_doc_source_drain_uses_the_daemon_not_the_stored_creds(boundary, monkeypatch):
    from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as
    from resource_explorer.egeria_outbox import _doc_source_clients

    monkeypatch.setattr("pyegeria.ExternalReferences", boundary.fake("ExternalReferences"))
    with acting_as(Daemon(DaemonReason.OUTBOX)):
        from pyegeria import ExternalReferences

        _doc_source_clients(_db_entity(egeria_user="stored-user", egeria_password="stored-pw")).of(ExternalReferences)
    assert [(c["user"], c["minted"]) for c in boundary.clients] == [(DAEMON_USER, True)]


def test_an_inline_drain_gateway_runs_as_the_person_not_the_stored_credential(boundary, monkeypatch, signed_in_caller):
    from resource_explorer.egeria_outbox import _catalogue_gateway

    monkeypatch.setattr("pyegeria.AssetMaker", boundary.fake("AssetMaker"))
    entity = _db_entity(egeria_user="stored-user", egeria_password="stored-pw")
    clients = type("C", (), {"catalogue_gateway": None, "require": lambda self, n: type(
        "R", (), {"get_database": lambda self, slug, allow_unreadable=True: entity})()})()
    _catalogue_gateway(clients, {"slug": "db"})._client("AssetMaker")
    assert boundary.users_and_tokens() == {("AssetMaker", "test-caller", "tok-test-caller", False)}


# ── item 1: no credential goes to a platform RE is not configured for ────────

def test_an_entity_naming_another_platform_is_refused_and_nothing_is_sent(boundary, monkeypatch, signed_in_caller):
    from resource_explorer.catalogue_commit import make_gateway
    from resource_explorer.egeria_clients import PlatformNotAllowed

    monkeypatch.setattr("pyegeria.AssetMaker", boundary.fake("AssetMaker"))
    gw = make_gateway(_db_entity(egeria_url="https://evil.invalid"))
    with pytest.raises(PlatformNotAllowed, match="not configured for: https://evil.invalid:443"):
        gw._client("AssetMaker")
    assert boundary.clients == [], "no client built, no token sent"


def test_a_publish_route_naming_another_platform_is_403_and_sends_nothing(boundary, reg, client, monkeypatch):
    from resource_explorer.registry import FileSystemEntity

    reg.register_filesystem(FileSystemEntity(slug="fs", display_name="FS", local_mount_point="/tmp",
                                             egeria_url="https://evil.invalid"))
    monkeypatch.setattr(reg, "get_latest_filesystem_survey", lambda slug: {"survey_data": {}}, raising=False)
    for cls in ("AutomatedCuration", "AssetMaker"):
        monkeypatch.setattr(f"pyegeria.{cls}", boundary.fake(cls))
    monkeypatch.setattr("pyegeria.omvs.data_discovery.DataDiscovery", boundary.fake("DataDiscovery"))
    r = client.post("/api/filesystems/fs/publish", json={}, headers=_token())
    assert r.status_code == 403, r.text
    assert "not configured for: https://evil.invalid:443" in r.json()["detail"]
    assert boundary.clients == []


# ── the run queue: a survey → Daemon(RUN_QUEUE, requested_by), Ownership = requester ─

def test_a_queued_survey_runs_as_the_daemon_and_stamps_ownership_as_the_requester(boundary, reg, monkeypatch):
    from resource_explorer import run_queue as rq

    stamped = []
    monkeypatch.setattr("pyegeria.ClassificationExplorer", boundary.fake(
        "ClassificationExplorer",
        add_ownership_to_element=lambda guid, body: stamped.append(body["properties"]["owner"])))

    def handler(target, result_ref):
        from resource_explorer.egeria_identity import caller_credentials, set_ownership

        who = caller_credentials()
        assert set_ownership("guid-x", who.user_id) is True
        return rq.RunOutcome(state="succeeded")

    monkeypatch.setitem(rq.HANDLERS, "analysis_run", handler)
    reg.enqueue_run("analysis_run", {"slug": "p"}, requested_by="erin")
    row = reg.claim_next_run("host:1", {"pid": os.getpid()})
    assert rq.execute_run(row, reg).state == "succeeded"
    assert stamped == ["erin"], "Ownership names the person who asked"
    assert [(c["user"], c["minted"]) for c in boundary.clients] == [(DAEMON_USER, True)], \
        "and the daemon authenticates"


# ── the run queue: a queued Publish → Daemon(RUN_QUEUE, requested_by) ─────
# Owner's Egeria-consistent ruling (2026-10-09): a queued write is committed by RE's daemon, and
# the person is the recorded requester and the Ownership owner.

def test_a_queued_publish_is_committed_by_the_daemon_with_ownership_the_requester(boundary, reg, monkeypatch):
    from resource_explorer import run_queue as rq

    stamped = []
    monkeypatch.setattr("pyegeria.ClassificationExplorer", boundary.fake(
        "ClassificationExplorer",
        add_ownership_to_element=lambda guid, body: stamped.append(body["properties"]["owner"])))

    def handler(target, result_ref):
        from resource_explorer.egeria_identity import caller_credentials, set_ownership

        who = caller_credentials()
        assert who.kind == "daemon" and who.requested_by == "dana"
        assert set_ownership("guid-bp", who.user_id) is True
        return rq.RunOutcome(state="succeeded")

    monkeypatch.setitem(rq.HANDLERS, "publish_architecture", handler)
    run_id = reg.enqueue_run("publish_architecture", {"slug": "p"}, requested_by="dana")
    row = reg.claim_next_run("host:1", {"pid": os.getpid()})
    assert rq.execute_run(row, reg).state == "succeeded"
    assert stamped == ["dana"]
    assert [(c["user"], c["minted"]) for c in boundary.clients] == [(DAEMON_USER, True)]
    assert reg.get_run(run_id)["requested_by"] == "dana"


@pytest.mark.parametrize("kind", ["publish_architecture", "curate_commit", "catalogue_commit"])
def test_a_persons_queued_action_with_no_requester_fails_loudly_and_writes_nothing(boundary, reg, monkeypatch, kind):
    from resource_explorer import run_queue as rq

    ran = []
    monkeypatch.setitem(rq.HANDLERS, kind, lambda t, r: ran.append(1) or rq.RunOutcome("succeeded"))
    run_id = reg.enqueue_run(kind, {"slug": "p"})
    row = reg.claim_next_run("host:1", {"pid": os.getpid()})
    out = rq.execute_run(row, reg)
    assert out.state == "failed" and out.error == rq.NO_REQUESTER_SENTENCE
    assert ran == [] and boundary.clients == []
    assert reg.get_run(run_id)["error"] == rq.NO_REQUESTER_SENTENCE


# ── round 3: a daemon write on a person's behalf names them in Egeria ─────────
# One rule, one helper (`egeria_identity.on_behalf_of`): `additionalProperties.requestedBy` is the
# requester, and Ownership is the requester (round 4: no declared-owner branch on these writes).
# Driven through the real materializers / gateway, faking only the pyegeria classes; asserted on
# what reached them.

NEW_GUID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def stamps(boundary, monkeypatch):
    seen = {"bodies": [], "owners": {}, "requested_by": {}}

    def create(body):
        seen["bodies"].append(body)
        return NEW_GUID

    def own(guid, body):
        seen["owners"][guid] = body["properties"]["owner"]

    def update(guid, body):
        m = body["properties"]["propertyValueMap"]["additionalProperties"]["mapValues"]["propertyValueMap"]
        seen["requested_by"][guid] = m["requestedBy"]["primitiveValue"]

    sa = boundary.fake("SolutionArchitect", create_solution_component=create, create_solution_blueprint=create,
                       link_solution_component_port=lambda *a, **k: None,
                       get_solution_blueprint_by_guid=lambda *a, **k: "No elements found")
    for path in ("pyegeria.SolutionArchitect", "pyegeria.omvs.solution_architect.SolutionArchitect"):
        monkeypatch.setattr(path, sa)
    monkeypatch.setattr("pyegeria.AutomatedCuration", boundary.fake(
        "AutomatedCuration", get_guid_for_name=lambda *a, **k: [],
        create_elem_from_template=lambda body: NEW_GUID))
    me = boundary.fake("MetadataExpert", create_metadata_element=create,
                       get_metadata_guid_by_unique_name=lambda *a, **k: "No elements found",
                       # S19: the archived-sibling find, answered as live 6.2 answers a miss (no archived copy)
                       find_metadata_elements_with_string=lambda *a, **k: "No elements found",
                       update_metadata_element_properties=update,
                       get_metadata_element_by_guid=lambda guid, *a, **k: {
                           "elementHeader": {"guid": guid}, "elementProperties": {"propertyValueMap": {}}})
    for path in ("pyegeria.MetadataExpert", "pyegeria.omvs.metadata_expert.MetadataExpert"):
        monkeypatch.setattr(path, me)
    monkeypatch.setattr("pyegeria.ClassificationExplorer", boundary.fake(
        "ClassificationExplorer", add_ownership_to_element=own,
        add_zone_membership=lambda *a, **k: None))
    return seen


def _queued_for(person):
    from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

    return acting_as(Daemon(DaemonReason.RUN_QUEUE, requested_by=person))


def _platform():
    from resource_explorer.config import get_config

    return get_config().egeria.platform_url


@pytest.mark.parametrize("declared", ["", "olivia"])
def test_a_queued_component_names_the_requester_and_the_owner(stamps, reg, declared):
    from resource_explorer.surveyors.arch_recovery.materializer import ComponentMaterializer

    if declared:
        reg.save_context("repo", "p", {"enrichment": {"owner": {"value": declared}}})
    with _queued_for("dana"):
        out = ComponentMaterializer(platform_url=_platform(), registry=reg).materialize(
            "repo", "p", "src/a", name="a", component_type="Software Service")
    assert out["guid"] == NEW_GUID
    assert stamps["bodies"][0]["properties"]["additionalProperties"]["requestedBy"] == "dana"
    assert stamps["owners"][NEW_GUID] == "dana"     # the requester, even with a declared owner (round 4)


@pytest.mark.parametrize("declared", ["", "olivia"])
def test_a_queued_blueprint_names_the_requester_and_the_owner(stamps, reg, declared):
    from resource_explorer.surveyors.arch_recovery.blueprint_materializer import BlueprintMaterializer

    if declared:
        reg.save_context("repo", "p", {"enrichment": {"owner": {"value": declared}}})
    with _queued_for("dana"):
        out = BlueprintMaterializer(platform_url=_platform(), registry=reg).materialize_blueprint_element(
            "repo", "p", "deployment", "root", display_name="P Deployment Blueprint")
    assert out["guid"] == NEW_GUID
    props = stamps["bodies"][0]["properties"]["additionalProperties"]
    assert props["requestedBy"] == "dana" and props["re_slug"] == "p"      # beside the existing provenance
    assert stamps["owners"][NEW_GUID] == "dana"     # the requester, even with a declared owner (round 4)


@pytest.mark.parametrize("declared", ["", "olivia"])
def test_a_queued_port_names_the_requester_and_the_owner(stamps, reg, declared):
    from resource_explorer.surveyors.arch_recovery.port_materializer import PortMaterializer

    if declared:
        reg.save_context("repo", "p", {"enrichment": {"owner": {"value": declared}}})
    with _queued_for("dana"):
        out = PortMaterializer(platform_url=_platform(), registry=reg).materialize_port_element(
            "repo", "p", "src/a", "http", component_guid="22222222-2222-2222-2222-222222222222")
    assert out["guid"] == NEW_GUID
    assert stamps["requested_by"][NEW_GUID] == "dana"
    assert stamps["owners"][NEW_GUID] == "dana"     # the requester, even with a declared owner (round 4)


@pytest.mark.parametrize("declared", ["", "olivia"])
def test_a_queued_catalog_schema_element_names_the_requester_and_the_owner(stamps, reg, declared, monkeypatch):
    from resource_explorer.catalogue_commit import apply_attach, make_gateway
    from resource_explorer.registry import DatabaseEntity

    db = DatabaseEntity(slug="db", display_name="Shop", db_type="postgresql", host="localhost",
                        port=5442, database_name="shop")
    reg.register_database(db)
    if declared:
        reg.save_context("database", "db", {"enrichment": {"owner": {"value": declared}}})
    gw = make_gateway(db)
    monkeypatch.setattr(gw, "read_element", lambda qn, **k: None)

    def stop(*a, **k):
        raise RuntimeError("stop after the create and its stamps")   # the attach half is not this test's
    monkeypatch.setattr(gw, "list_catalog_targets", stop)
    with _queued_for("dana"):
        with pytest.raises(RuntimeError, match="stop after"):
            apply_attach(reg, gw, {"slug": "db", "schema": "sales",
                                   "database_guid": "33333333-3333-3333-3333-333333333333", "by": "dana"})
    assert stamps["requested_by"][NEW_GUID] == "dana"
    assert stamps["owners"][NEW_GUID] == "dana"     # the requester, even with a declared owner (round 4)


# ── owner's ruling 2026-10-09: per-resource Egeria credentials are ignored on the way in ─────────

SECRET = "pw-typed-into-an-old-form"


def _stored(reg, kind, slug):
    """The raw stored columns, read the way the registry stores them (no RE reader involved)."""
    import sqlite3

    table = {"filesystem": "file_systems", "database": "databases", "server": "db_servers"}[kind]
    with sqlite3.connect(reg.database_url.removeprefix("sqlite:///")) as conn:
        return conn.execute(f"SELECT egeria_user, egeria_password FROM {table} WHERE slug=?",  # noqa: S608
                            (slug,)).fetchone()


@pytest.mark.parametrize("kind,path,body", [
    ("filesystem", "/api/filesystems/", {"slug": "fs1", "display_name": "FS", "local_mount_point": "/tmp"}),
    ("database", "/api/databases/register", {"slug": "db1", "display_name": "DB", "db_type": "postgresql",
                                              "host": "localhost", "port": 5432, "database_name": "shop"}),
    ("server", "/api/db-servers/register", {"slug": "srv1", "display_name": "S", "host": "localhost"}),
])
def test_registration_ignores_posted_egeria_credentials_and_says_so_without_the_values(
        reg, client, caplog, kind, path, body):
    import logging

    caplog.set_level(logging.WARNING)
    r = client.post(path, json={**body, "egeria_user": "someone", "egeria_password": SECRET}, headers=_token())
    assert r.status_code == 200, r.text
    assert tuple(_stored(reg, kind, body["slug"])) == ("", ""), "nothing stored"
    assert any("egeria_user/egeria_password ignored" in rec.getMessage() for rec in caplog.records)
    assert SECRET not in caplog.text and "someone" not in caplog.text
    assert "egeria_user" not in r.json() and SECRET not in r.text


def test_publishing_local_doc_sources_runs_as_the_caller(boundary, reg, monkeypatch, signed_in_caller):
    """Round-1 regression found in round 3: `publish_local_doc_sources` still passed the old
    credential keywords. It now builds one client for whoever the publish runs as."""
    from resource_explorer.web.routes.doc_sources import publish_local_doc_sources

    monkeypatch.setattr("pyegeria.ExternalReferences", boundary.fake(
        "ExternalReferences", create_external_reference=lambda **k: "44444444-4444-4444-4444-444444444444",
        link_external_reference=lambda *a, **k: "55555555-5555-5555-5555-555555555555"))
    monkeypatch.setattr("pyegeria.AutomatedCuration", boundary.fake(
        "AutomatedCuration", get_guid_for_name=lambda *a, **k: []))
    reg.add_doc_source("repo", "p", "https://docs.example/a", label="a", source_type="other")
    out = publish_local_doc_sources("repo", "p", "66666666-6666-6666-6666-666666666666", registry=reg)
    assert out and out[0]["ok"], out
    assert ("ExternalReferences", "create_external_reference", "test-caller", "tok-test-caller") in boundary.calls
