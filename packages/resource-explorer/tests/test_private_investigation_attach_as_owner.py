"""A private investigation's working set is attached AS ITS OWNER, never as the service account (2026-10-13).

Found live during the owner's 6.2 privacy proof: peterprofile promoted the PersonalProject investigation
`privacy-proof-2026-10-13`. The Project (834dbe00) was created and zoned `[resource-explorer-private,
peterprofile]` as peterprofile. The next step, the ResourceList from the Project to its working-set Folio, went
through the outbox, whose drain-time access check read the Project as RE's service account
(`zone_access._metadata_expert`: `Daemon(ACCESS_CHECK)`). Egeria refuses that account a private element by design,
so the row went dead at once: "not permitted · could not check access in Egeria (the element 834dbe00:
PyegeriaUnauthorizedException ... user ``)". Every private investigation's working-set attach failed that way.

The rule now: writes and access checks that touch a private investigation's Egeria elements run as the signed-in
owner. A queued row of a private investigation is never attempted as the service account: it waits, pending, with
the words "waiting for the owner, <user>, to retry: ...", and the owner's next Relink applies it inline as them.

The only fake is the pyegeria client boundary: `egeria_clients._build` (the one place a pyegeria client is
constructed) hands back `create_autospec(<real pyegeria class>, instance=True, spec_set=True)` per identity, so every
call is bound to the real signature and records WHO sent it. The fake enforces zoning as Egeria does: an element
zoned `[private zone, owner]` (or anchored to one) raises a real-shaped `PyegeriaUnauthorizedException` (403,
OPEN-METADATA-SECURITY-403-020) for any other user and is readable and writable by the owner. Registry: real, temp
sqlite. Routes: the real FastAPI app.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import create_autospec

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_zone_access_investigations_worklists import (  # noqa: E402,F401  (fixtures are shared)
    OWNER, registry, web,
)

PLATFORM = "https://localhost:9443"            # an allowed fixture platform (conftest `allow_fixture_platforms`)
PRIVATE = "resource-explorer-private"
MEMBER_ASSET = "g-p"                            # the registry fixture's repo `p`, published, unzoned


def _refused(user: str, guid: str):
    """A pyegeria 403 exactly as `_async_make_request` raises it for Egeria's security refusal."""
    from pyegeria.core._exceptions import PyegeriaUnauthorizedException

    msg = (f"OPEN-METADATA-SECURITY-403-020 User {user} is not authorized to access element {guid} "
           "because of its zone membership")
    body = {"class": "OpenMetadataElementResponse", "relatedHTTPCode": 403,
            "exceptionErrorMessageId": "OPEN-METADATA-SECURITY-403-020", "exceptionErrorMessage": msg}
    resp = httpx.Response(200, json=body, request=httpx.Request("POST", f"{PLATFORM}/servers/v/api"))
    return PyegeriaUnauthorizedException(resp, {"caller_method": "_async_get_guid_request"}, additional_info=body)


class ZonedEgeria:
    """What Egeria holds, behind autospec'd pyegeria clients built per identity. `build` replaces
    `egeria_clients._build`; `requests` records (user, method, guid) for every element-addressed call."""

    def __init__(self):
        self.elements: dict[str, dict] = {MEMBER_ASSET: {"zones": [], "anchor": "", "kinds": []}}
        self.attached: dict[str, list[str]] = {}            # project guid -> collection guids (ResourceList)
        self.members: dict[str, list[str]] = {}             # collection guid -> member guids
        self.requests: list[tuple[str, str, str]] = []
        self.fail_attach: list[Exception] = []              # raised (once each) by attach_collection
        self.drop_attach = False                            # accept attach_collection and keep nothing
        self.fail_read_back: list[Exception] = []           # raised (once each) by get_attached_collections
        self.fail_project_reads = 0                         # element reads of a Project that time out (count)
        self._n = 0

    # ── zoning, as Egeria's access connector answers it ─────────────────────
    def _zones(self, guid: str) -> list[str]:
        el = self.elements.get(guid) or {}
        anchor = el.get("anchor") or ""
        return list((self.elements.get(anchor) or {}).get("zones") or []) if anchor else list(el.get("zones") or [])

    def _gate(self, user: str, method: str, *guids: str) -> None:
        for guid in guids:
            self.requests.append((user, method, guid))
            zones = self._zones(guid)
            if PRIVATE in zones and user not in zones:
                raise _refused(user, guid)

    def _new(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n:04d}-0000-4000-8000-000000000000"

    # ── the factory boundary ─────────────────────────────────────────────────
    def build(self, cls, view_server, platform_url, user_id, password):
        inst = create_autospec(cls, instance=True, spec_set=True)
        name, user = cls.__name__, user_id
        if name == "ProjectManager":
            inst.create_project.side_effect = lambda *a, body=None, **k: self._create_project(user, body)
            inst.get_project_by_guid.side_effect = lambda guid, *a, **k: self._project(user, guid)
            inst.update_project.side_effect = lambda guid, *a, **k: self._gate(user, "update_project", guid)
        elif name == "ClassificationExplorer":
            inst.add_zone_membership.side_effect = lambda guid, body, *a, **k: self._zone(user, guid, body)
        elif name == "MetadataExpert":
            inst.get_metadata_element_by_guid.side_effect = lambda guid=None, *a, **k: self._element(user, guid)
            inst.classify_metadata_element.side_effect = (
                lambda guid, cname, body=None: self._gate(user, "classify_metadata_element", guid))
        elif name == "CollectionManager":
            inst.create_collection.side_effect = lambda *a, body=None, **k: self._create_collection(user, body)
            inst.attach_collection.side_effect = lambda p, c, body=None: self._attach(user, p, c)
            inst.get_attached_collections.side_effect = lambda p, *a, **k: self._attached(user, p)
            inst.add_to_collection.side_effect = lambda c, m, body=None: self._add(user, c, m)
        elif name == "SecurityOfficer":
            inst.get_user_account.side_effect = lambda platform_name, user_id, platform_guid=None: {
                "userAccountStatus": "AVAILABLE", "userAccountType": "EMPLOYEE",
                "securityGroups": [], "securityRoles": []}
            inst.get_security_access_control.side_effect = lambda *a, **k: None
        return inst

    def _create_project(self, user, body):
        guid = self._new("p")
        kinds = list(((body or {}).get("initialClassifications") or {}).keys())
        self.elements[guid] = {"zones": [], "anchor": "", "kinds": kinds}
        self.requests.append((user, "create_project", guid))
        return guid

    def _project(self, user, guid):
        self._gate(user, "get_project_by_guid", guid)
        return {"class": "OpenMetadataRootElement",
                "elementHeader": {"guid": guid, "projectKinds": [
                    {"classificationName": k} for k in self.elements[guid]["kinds"]]},
                "properties": {}}

    def _zone(self, user, guid, body):
        self._gate(user, "add_zone_membership", guid)
        self.elements[guid]["zones"] = list(body["properties"]["zoneMembership"])

    def _element(self, user, guid):
        self._gate(user, "get_metadata_element_by_guid", guid)
        if self.fail_project_reads and str(guid).startswith("p"):
            self.fail_project_reads -= 1
            raise TimeoutError("the read timed out")
        from tests.zone_fakes import element
        el = self.elements.get(guid) or {"zones": [], "anchor": ""}
        return element(guid, zones=el["zones"], anchor=el["anchor"])

    def _create_collection(self, user, body):
        anchor = (body or {}).get("anchorGUID") or ""
        if anchor:
            self._gate(user, "create_collection(anchor)", anchor)
        guid = self._new("c")
        self.elements[guid] = {"zones": [], "anchor": anchor, "kinds": []}
        self.members[guid] = []
        self.requests.append((user, "create_collection", guid))
        return guid

    def _attach(self, user, parent, coll):
        self._gate(user, "attach_collection", parent, coll)
        if self.fail_attach:
            raise self.fail_attach.pop(0)
        if self.drop_attach:
            return
        held = self.attached.setdefault(parent, [])
        if coll not in held:
            held.append(coll)

    def _attached(self, user, parent):
        self._gate(user, "get_attached_collections", parent)
        if self.fail_read_back:
            raise self.fail_read_back.pop(0)
        held = self.attached.get(parent) or []
        return [{"elementHeader": {"guid": c}} for c in held] if held else "No elements found"

    def _add(self, user, coll, member):
        self._gate(user, "add_to_collection", coll, member)
        if member not in self.members.setdefault(coll, []):
            self.members[coll].append(member)

    def sent_by(self, user: str, guid: str) -> list[str]:
        return [m for u, m, g in self.requests if u == user and g == guid]


@pytest.fixture
def world(web, registry, monkeypatch):
    """peterprofile signed in; the private zone enforced; pyegeria faked at its construction."""
    from resource_explorer import config as re_config
    from resource_explorer import egeria_clients
    from resource_explorer import egeria_identity as ident

    monkeypatch.setattr(re_config.get_config().egeria, "platform_url", PLATFORM)
    monkeypatch.setenv("EXPLORER_EGERIA_PLATFORM_NAME", "Quickstart platform")
    monkeypatch.delenv("EXPLORER_PRIVATE_ZONE", raising=False)
    monkeypatch.delenv("EXPLORER_DRAFT_ZONE", raising=False)
    monkeypatch.setattr(ident, "configured_publish_zones", lambda: [])
    monkeypatch.setattr(ident, "_private_zone_state", {"status": "exists", "enforced": True,
                                                       "zone": PRIVATE, "control_present": True})
    eg = ZonedEgeria()
    monkeypatch.setattr(egeria_clients, "_build", eg.build)
    client, who = web
    who["user_id"] = OWNER
    service = egeria_clients.Daemon(egeria_clients.DaemonReason.OUTBOX).client_user
    assert service and service != OWNER
    return {"client": client, "who": who, "registry": registry, "eg": eg, "service": service}


def _create(world, classification: str = "PersonalProject", name: str = "Privacy Proof") -> str:
    r = world["client"].post("/api/investigations/", json={"display_name": name,
                                                            "project_classification": classification})
    assert r.status_code == 200, r.text
    slug = r.json()["slug"]
    assert r.json()["created_by"] == world["who"]["user_id"]
    r = world["client"].post(f"/api/investigations/{slug}/members", json={"entity_type": "repo",
                                                                          "entity_slug": "p"})
    assert r.status_code == 200, r.text
    return slug


def _promote(world, slug: str) -> dict:
    r = world["client"].post(f"/api/investigations/{slug}/promote")
    assert r.status_code == 200, r.text
    return r.json()


def _rows(world, slug: str, kind: str) -> list[dict]:
    return [r for r in world["registry"].list_outbox_elements(entity_slug=slug, limit=100)
            if r["element_kind"] == kind]


# ── 1. the e2e: promote as the signed-in owner of a PersonalProject ──────────────────────────────────

def test_promote_attaches_the_working_set_as_the_owner(world):
    eg, reg = world["eg"], world["registry"]
    slug = _create(world)

    out = _promote(world, slug)

    project, folio = out["project_guid"], out["collection_guid"]
    assert project and folio, out
    # The Project is created zoned to its owner.
    assert out["private_zoned"] is True, out
    assert eg.elements[project]["zones"] == [PRIVATE, OWNER]
    # No outbox row went dead (or is left waiting); the ResourceList row is done.
    rows = reg.list_outbox_elements(entity_slug=slug, limit=100)
    assert [r for r in rows if r["status"] == "dead"] == [], [(r["element_kind"], r["last_error"]) for r in rows]
    assert {r["element_kind"]: r["status"] for r in rows} == {"resource_list": "done",
                                                               "collection_membership": "done"}
    # The ResourceList attach is made AS THE OWNER, and it landed.
    assert ("peterprofile", "attach_collection", project) in eg.requests
    assert eg.attached.get(project) == [folio]
    # ... and the response says attached, from a read-back as the owner.
    assert out["resource_list_linked"] is True, out
    assert (OWNER, "get_attached_collections", project) in eg.requests
    assert not any("attach_collection failed" in e for e in out["errors"]), out["errors"]
    assert out["ok"] is True, out["errors"]
    # The member is attached too, as the owner.
    assert out["members_linked"] == ["p"], out
    assert eg.members[folio] == [MEMBER_ASSET]
    # The service account never sent a request for the investigation's elements.
    assert eg.sent_by(world["service"], project) == []
    assert eg.sent_by(world["service"], folio) == []


# ── 2. the background drain never attempts a private row as the service account ──────────────────────

def test_the_daemon_drain_leaves_a_private_row_pending_for_its_owner(world):
    from resource_explorer.egeria_clients import Daemon, DaemonReason
    from resource_explorer.egeria_outbox import drain_outbox

    eg, reg = world["eg"], world["registry"]
    slug = _create(world)
    eg.fail_attach.append(ConnectionError("Egeria went away for a moment"))     # the owner's inline try fails
    out = _promote(world, slug)
    project, folio = out["project_guid"], out["collection_guid"]
    assert out["resource_list_linked"] is False
    (row,) = _rows(world, slug, "resource_list")
    assert row["status"] in ("pending", "failed"), row
    assert json.loads(row["payload_json"])["by"] == OWNER
    before = list(eg.requests)

    # The scheduler's drain, as RE's service account, once the row's backoff is over.
    with reg._conn() as conn:
        conn.execute("UPDATE egeria_outbox SET next_attempt_at = '' WHERE id = ?", (row["id"],))
    summary = drain_outbox(reg, identity=Daemon(DaemonReason.OUTBOX))

    after = reg.get_outbox_element(row["id"])
    assert after["status"] == "pending", (after["status"], after["last_error"])
    assert after["status"] != "dead"
    assert after["last_error"].startswith(
        f"waiting for the owner, {OWNER}, to retry: the service account cannot see a private investigation"), \
        after["last_error"]
    assert after["attempts"] == row["attempts"]                 # no attempt burned
    assert summary.get("dead", 0) == 0
    # Nothing at all was sent for it, by anyone: no access read, no write.
    assert eg.requests[len(before):] == []
    assert eg.sent_by(world["service"], project) == [] and eg.sent_by(world["service"], folio) == []

    # The owner's next Relink applies it inline, as them, and the read-back says attached.
    r = world["client"].post(f"/api/investigations/{slug}/relink-members")
    assert r.status_code == 200, r.text
    assert r.json()["resource_list_linked"] is True, r.json()
    assert eg.attached.get(project) == [folio]
    assert reg.get_outbox_element(row["id"])["status"] == "done"
    assert (OWNER, "attach_collection", project) in eg.requests[len(before):]
    assert eg.sent_by(world["service"], project) == []


def test_a_row_dead_from_the_old_refusal_is_revived_by_the_owners_relink(world):
    """Today's live row 76009: dead with "not permitted · could not check access ... 401 ... user ``". The owner's
    Relink re-queues the same row (one row per key) and applies it as them."""
    eg, reg = world["eg"], world["registry"]
    slug = _create(world)
    eg.fail_attach.append(ConnectionError("blip"))
    out = _promote(world, slug)
    project, folio = out["project_guid"], out["collection_guid"]
    (row,) = _rows(world, slug, "resource_list")
    reg.mark_outbox_failed(row["id"], "not permitted · could not check access in Egeria (the element "
                           f"{project[:8]}: PyegeriaUnauthorizedException: ... user ``)", max_attempts=1)
    assert reg.get_outbox_element(row["id"])["status"] == "dead"

    r = world["client"].post(f"/api/investigations/{slug}/relink-members")

    assert r.status_code == 200, r.text
    assert r.json()["resource_list_linked"] is True, r.json()
    assert eg.attached.get(project) == [folio]
    assert [x["id"] for x in _rows(world, slug, "resource_list")] == [row["id"]]
    assert reg.get_outbox_element(row["id"])["status"] == "done"
    assert eg.sent_by(world["service"], project) == []


# ── 3. a shared investigation keeps its path ────────────────────────────────────────────────────────

def test_a_shared_investigation_keeps_its_current_path(world):
    """StudyProject: no zone, so the access check reads the elements as RE's service account (as before) and the
    attach is made by the caller through the outbox's inline drain."""
    eg, reg = world["eg"], world["registry"]
    world["who"]["user_id"] = "garygeeke"
    slug = _create(world, "StudyProject", "Shared Study")

    out = _promote(world, slug)

    project, folio = out["project_guid"], out["collection_guid"]
    assert out["private_zoned"] is False and eg.elements[project]["zones"] == []
    assert out["resource_list_linked"] is True and out["ok"] is True, out
    assert eg.attached.get(project) == [folio]
    assert ("garygeeke", "attach_collection", project) in eg.requests
    # The access check's element reads are the service account's, unchanged.
    assert "get_metadata_element_by_guid" in eg.sent_by(world["service"], project)
    # No owner read-back for a shared one: the path is unchanged.
    assert ("garygeeke", "get_attached_collections", project) not in eg.requests
    assert {r["element_kind"]: r["status"] for r in reg.list_outbox_elements(entity_slug=slug, limit=50)} == {
        "resource_list": "done", "collection_membership": "done"}


# ── 4. the bound write paths of a private investigation run as the owner ────────────────────────────

def test_sync_and_relink_of_a_private_investigation_check_as_the_owner(world):
    eg = world["eg"]
    slug = _create(world)
    out = _promote(world, slug)
    project = out["project_guid"]
    n = len(eg.requests)

    r = world["client"].patch(f"/api/investigations/{slug}", json={"display_name": "Privacy Proof 2"})
    assert r.status_code == 200, r.text
    r = world["client"].post(f"/api/investigations/{slug}/sync-egeria")
    assert r.status_code == 200, r.text            # was a 403: the check read the Project as the service account
    r = world["client"].post(f"/api/investigations/{slug}/relink-members")
    assert r.status_code == 200, r.text
    assert r.json()["members_linked"] == ["p"], r.json()
    assert eg.sent_by(world["service"], project) == []
    assert (OWNER, "update_project", project) in eg.requests[n:]


def test_a_daemon_relink_of_a_private_investigation_sends_nothing(world):
    """Resync's unattended relink (no person): the service account cannot see it, so nothing is sent and the
    owner's words are returned."""
    from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as
    from resource_explorer.surveyors.egeria_investigation_publisher import EgeriaInvestigationPublisher
    from resource_explorer.workflows.egeria_work_access import investigation_member_refusal

    eg, reg = world["eg"], world["registry"]
    slug = _create(world)
    out = _promote(world, slug)
    n = len(eg.requests)
    from resource_explorer.a2a_auth import current_caller

    reset = current_caller.set(None)
    try:
        with acting_as(Daemon(DaemonReason.RESYNC)):
            pub = EgeriaInvestigationPublisher(reg)
            res = pub.relink_members(slug, refusal=investigation_member_refusal(reg, slug, unattended=True))
            others = [pub.ensure_working_set(slug).errors, pub.sync_project_properties(slug).errors,
                      pub.sync_disposition_sets(slug)["errors"]]
    finally:
        current_caller.reset(reset)
    assert eg.requests[n:] == []
    words = f"waiting for the owner, {OWNER}, to retry: the service account cannot see a private investigation"
    assert res.errors == [words], res.errors
    # ensure_working_set refuses before anything (its recorded working set included) is looked at.
    assert others == [[words], [words], [words]], others
    assert eg.sent_by(world["service"], out["project_guid"]) == []


def test_attached_only_from_a_read_back_as_the_owner(world):
    """Egeria answers the attach and keeps nothing: the response does not say attached."""
    eg = world["eg"]
    slug = _create(world)
    eg.drop_attach = True

    out = _promote(world, slug)

    assert out["resource_list_linked"] is False, out
    assert any("does not show it attached to the Project (read back as peterprofile)" in e
               for e in out["errors"]), out["errors"]


# ── review round 1 (1841e4a8) ────────────────────────────────────────────────────────────────────────

WAITING = f"waiting for the owner, {OWNER}, to retry: the service account cannot see a private investigation"


def _bind_private(world, *, record_folio: str = ""):
    """A private investigation bound to an existing private Project (zoned to the owner), optionally with a Folio
    already recorded in the registry."""
    eg, reg = world["eg"], world["registry"]
    slug = _create(world)
    project = eg._new("p")
    eg.elements[project] = {"zones": [PRIVATE, OWNER], "anchor": "", "kinds": ["PersonalProject"]}
    if record_folio:
        eg.elements[record_folio] = {"zones": [], "anchor": project, "kinds": []}
        ws = reg.get_or_create_working_set(slug)
        reg.set_working_set_egeria_collection(ws["slug"], record_folio)
    return slug, project


def test_bind_attaches_a_new_working_set_through_the_owners_check_and_read_back(world):
    eg, reg = world["eg"], world["registry"]
    slug, project = _bind_private(world)

    r = world["client"].put(f"/api/investigations/{slug}/egeria-project",
                            json={"status": "linked", "egeria_project_guid": project})

    assert r.status_code == 200, r.text
    ws = r.json()["working_set"]
    assert ws["resource_list_linked"] is True, ws
    folio = ws["collection_guid"]
    assert eg.attached.get(project) == [folio]
    assert (OWNER, "get_attached_collections", project) in eg.requests
    (row,) = _rows(world, slug, "resource_list")
    assert row["status"] == "done", row
    assert eg.sent_by(world["service"], project) == []


def test_bind_does_not_say_linked_when_the_read_back_shows_nothing(world):
    eg = world["eg"]
    slug, project = _bind_private(world)
    eg.drop_attach = True

    r = world["client"].put(f"/api/investigations/{slug}/egeria-project",
                            json={"status": "linked", "egeria_project_guid": project})

    ws = r.json()["working_set"]
    assert ws["resource_list_linked"] is False, ws
    assert any("does not show it attached" in e for e in ws["errors"]), ws["errors"]


def test_ensure_working_set_with_a_recorded_folio_reads_back_before_saying_linked(world):
    """The Folio is recorded in RE but not attached in Egeria: the owner's ensure re-attaches it and says linked
    only after the read-back; it never answers 'linked' from the registry alone."""
    from resource_explorer.surveyors.egeria_investigation_publisher import EgeriaInvestigationPublisher
    from tests.zone_fakes import signed_in
    from resource_explorer.a2a_auth import current_caller

    eg, reg = world["eg"], world["registry"]
    folio = "c9999-0000-4000-8000-000000000000"
    slug, project = _bind_private(world, record_folio=folio)
    reg.set_investigation_egeria_project(slug, {"status": "linked", "egeria_project_guid": project})
    eg.drop_attach = True
    reset = current_caller.set(None)
    signed = signed_in(OWNER)
    try:
        res = EgeriaInvestigationPublisher(reg).ensure_working_set(slug)
        assert res.resource_list_linked is False, res
        assert any("does not show it attached" in e for e in res.errors), res.errors
        eg.drop_attach = False
        res = EgeriaInvestigationPublisher(reg).ensure_working_set(slug)
    finally:
        current_caller.reset(signed)
        current_caller.reset(reset)
    assert res.resource_list_linked is True, res.errors
    assert eg.attached.get(project) == [folio]
    assert (OWNER, "get_attached_collections", project) in eg.requests


def test_an_owner_check_that_times_out_defers_the_row_not_kills_it(world):
    eg = world["eg"]
    slug = _create(world)
    eg.fail_project_reads = 1

    out = _promote(world, slug)

    assert out["resource_list_linked"] is False
    (row,) = _rows(world, slug, "resource_list")
    assert row["status"] == "pending", (row["status"], row["last_error"])
    assert row["last_error"].startswith("waiting to re-check access"), row["last_error"]
    assert row["attempts"] == 0
    # The owner's Relink then applies it.
    r = world["client"].post(f"/api/investigations/{slug}/relink-members")
    assert r.status_code == 200, r.text
    assert r.json()["resource_list_linked"] is True, r.json()


def test_a_registry_error_reading_the_investigation_fails_closed_in_the_drain(world, monkeypatch):
    """Whether the row is private cannot be read: the drain must not take it for shared and check it as the
    service account; it waits, unsent."""
    from resource_explorer.egeria_clients import Daemon, DaemonReason
    from resource_explorer.egeria_outbox import drain_outbox

    eg, reg = world["eg"], world["registry"]
    slug = _create(world)
    eg.fail_attach.append(ConnectionError("blip"))
    out = _promote(world, slug)
    (row,) = _rows(world, slug, "resource_list")
    with reg._conn() as conn:
        conn.execute("UPDATE egeria_outbox SET next_attempt_at = '' WHERE id = ?", (row["id"],))
    real = type(reg).get_investigation

    def broken(self, slug_, *a, **k):
        if "user_id" in k:
            raise RuntimeError("registry unreachable")
        return real(self, slug_, *a, **k)
    monkeypatch.setattr(type(reg), "get_investigation", broken)
    n = len(eg.requests)

    drain_outbox(reg, identity=Daemon(DaemonReason.OUTBOX))

    after = reg.get_outbox_element(row["id"])
    assert after["status"] == "pending", (after["status"], after["last_error"])
    assert after["last_error"].startswith("waiting to re-check access"), after["last_error"]
    assert eg.requests[n:] == []
    assert eg.sent_by(world["service"], out["project_guid"]) == []


def test_relink_repairs_a_resource_list_detached_after_its_row_was_done(world):
    eg, reg = world["eg"], world["registry"]
    slug = _create(world)
    out = _promote(world, slug)
    project, folio = out["project_guid"], out["collection_guid"]
    (done,) = _rows(world, slug, "resource_list")
    assert done["status"] == "done"
    eg.attached[project] = []                        # someone detached it in Egeria

    r = world["client"].post(f"/api/investigations/{slug}/relink-members")

    assert r.status_code == 200, r.text
    assert r.json()["resource_list_linked"] is True, r.json()
    assert eg.attached.get(project) == [folio]
    rows = _rows(world, slug, "resource_list")
    assert [x["status"] for x in rows] == ["done", "done"] and rows[1]["id"] == done["id"], rows


def test_relink_takes_the_newest_dead_row_over_an_older_done_one(world):
    eg, reg = world["eg"], world["registry"]
    slug = _create(world)
    out = _promote(world, slug)
    project, folio = out["project_guid"], out["collection_guid"]
    eg.attached[project] = []
    newer = reg.enqueue_outbox_element("investigation", slug, "resource_list",
                                       f"ResourceList::{project}::{folio}",
                                       {"parent_guid": project, "collection_guid": folio, "by": OWNER})
    reg.mark_outbox_failed(newer, "not permitted · could not check access in Egeria (old refusal)", max_attempts=1)

    r = world["client"].post(f"/api/investigations/{slug}/relink-members")

    assert r.json()["resource_list_linked"] is True, r.json()
    assert reg.get_outbox_element(newer)["status"] == "done"
    assert len(_rows(world, slug, "resource_list")) == 2          # revived, not a third row


def test_a_non_owner_callers_drain_defers_a_private_row(world):
    from resource_explorer.a2a_auth import current_caller
    from resource_explorer.egeria_clients import Caller
    from resource_explorer.egeria_outbox import drain_outbox
    from tests.zone_fakes import signed_in

    eg, reg = world["eg"], world["registry"]
    slug = _create(world)
    eg.fail_attach.append(ConnectionError("blip"))
    out = _promote(world, slug)
    (row,) = _rows(world, slug, "resource_list")
    with reg._conn() as conn:
        conn.execute("UPDATE egeria_outbox SET next_attempt_at = '' WHERE id = ?", (row["id"],))
    n = len(eg.requests)
    reset = signed_in("garygeeke")
    try:
        drain_outbox(reg, identity=Caller(), element_id=row["id"])
    finally:
        current_caller.reset(reset)
    after = reg.get_outbox_element(row["id"])
    assert after["status"] == "pending" and after["last_error"] == WAITING, after
    assert eg.requests[n:] == []
    assert eg.sent_by("garygeeke", out["project_guid"]) == []


def test_a_drain_with_no_identity_and_handed_clients_defers_a_private_row(world):
    from unittest.mock import create_autospec

    from pyegeria import CollectionManager

    from resource_explorer.a2a_auth import current_caller
    from resource_explorer.egeria_outbox import OutboxClients, drain_outbox

    eg, reg = world["eg"], world["registry"]
    slug = _create(world)
    eg.fail_attach.append(ConnectionError("blip"))
    _promote(world, slug)
    (row,) = _rows(world, slug, "resource_list")
    with reg._conn() as conn:
        conn.execute("UPDATE egeria_outbox SET next_attempt_at = '' WHERE id = ?", (row["id"],))
    cm = create_autospec(CollectionManager, instance=True, spec_set=True)
    n = len(eg.requests)
    reset = current_caller.set(None)
    try:
        drain_outbox(reg, OutboxClients(collection_manager=cm), lambda qn: "", element_id=row["id"])
    finally:
        current_caller.reset(reset)
    after = reg.get_outbox_element(row["id"])
    assert after["status"] == "pending" and after["last_error"] == WAITING, after
    assert cm.mock_calls == [] and eg.requests[n:] == []


def test_the_words_when_the_read_back_raises(world):
    eg = world["eg"]
    slug = _create(world)
    eg.fail_read_back.append(ConnectionError("read-back went away"))

    out = _promote(world, slug)

    assert out["resource_list_linked"] is False
    assert any(e.startswith("the working set was sent, but it could not be read back as peterprofile "
                            "(ConnectionError: read-back went away), so it is not confirmed attached")
               for e in out["errors"]), out["errors"]
