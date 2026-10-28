"""A survey publish zones what it PRODUCED, never what it REFERENCES (2026-10-13 review).

`docs/investigation-classification-and-zoning-design.md` §3.6. For a member of a private investigation:

* the repository's Asset and the GitHub SourceControlLibrary are REFERENCED: never private, readable by everyone;
* the SurveyReport is PRODUCED: born in `[private, owner]` (no window), refused to anyone else;
* the annotations go beneath the report only once Egeria SHOWS the report private; otherwise the publish
  raises, records why, and writes no finding.

End to end through `EgeriaPublisher.publish()` with a real temp-SQLite registry (a real private investigation,
the real outbox). Only the pyegeria boundary is faked: each user gets `create_autospec(cls, spec_set=True)` doubles
of the real classes, backed by one store that models Egeria's zone check (OpenMetadataAccessSecurityConnector.
validateZoneAccess): an element in the secured private zone is visible only to a user named as one of its zones; an
anchored element (an annotation) is judged by its anchor; a create is judged by the zones it is born with.
"""
from __future__ import annotations

import itertools
import uuid
from contextlib import contextmanager
from unittest.mock import create_autospec

import pytest

from resource_explorer.registry import Project, ProjectRegistry

OWNER, OTHER = "alice", "bob"


class Denied(RuntimeError):
    """Egeria's 403 for a zone the user is not admitted to."""


# ── the fake Egeria: one store, one set of autospec clients per user ──────────────────────────────────────────

class Store:
    def __init__(self, private_zone: str):
        self.private = private_zone
        self.elements: dict[str, dict] = {}
        self.events: list[tuple] = []
        self._ids = itertools.count(1)
        self.fail_zone_stamp = False           # add_zone_membership answers with an error
        self.drop_initial_zones = False        # the create ignores initialClassifications
        self.zones_after_stamp = None          # a stamp that lands but leaves THESE zones (e.g. an extra one)
        self.unreadable: set[str] = set()      # reads of these GUIDs fail as an outage would
        self.hidden: set[str] = set()          # annotations Egeria holds but does not answer under their report
        self.read_back_error: Exception | None = None   # the report's annotations cannot be read

    # the zone check
    def _zones(self, guid):
        el = self.elements[guid]
        return self.elements[el["anchor"]]["zones"] if el.get("anchor") else el["zones"]

    def admits(self, user, zones) -> bool:
        return self.private not in zones or user in zones

    def visible(self, user, guid) -> bool:
        return guid in self.elements and self.admits(user, self._zones(guid))

    def _new(self, user, kind, qn, zones=(), anchor=None, props=None):
        if any(e["qn"] == qn for e in self.elements.values()):
            raise RuntimeError(f"409 OMAG-COMMON-409-001 The qualifiedName {qn} is not available for use")
        if not self.admits(user, list(zones)):
            raise Denied(f"403 OPEN-METADATA-SECURITY-403 {user} may not create {qn} in {list(zones)}")
        guid = str(uuid.UUID(int=next(self._ids)))       # GUID-shaped: RE's look-ups refuse anything else
        self.elements[guid] = {"kind": kind, "qn": qn, "zones": list(zones), "anchor": anchor,
                               "props": props or {}}
        self.events.append(("create", kind, guid, user, list(zones)))
        return guid

    def raw(self, guid) -> dict:
        """The element as MetadataExpert returns it, Egeria's array encoding for zones."""
        from tests.zone_fakes import element

        return {"elementHeader": element(guid, zones=self._zones(guid)) | {"guid": guid}}

    # the clients
    def clients_for(self, user):
        from pyegeria import (
            AssetMaker,
            AutomatedCuration,
            ClassificationExplorer,
            CollectionManager,
            ExternalReferences,
        )
        from pyegeria.omvs.data_discovery import DataDiscovery
        from pyegeria.omvs.metadata_expert import MetadataExpert

        fake = lambda cls: create_autospec(cls, instance=True, spec_set=True)
        am, cx, me, dd, ac = (fake(AssetMaker), fake(ClassificationExplorer), fake(MetadataExpert),
                              fake(DataDiscovery), fake(AutomatedCuration))
        store = self

        def _find(search_string="*", starts_with=True, **kw):
            hits = [{"elementHeader": {"guid": g}, "properties": {"qualifiedName": e["qn"]}}
                    for g, e in store.elements.items()
                    if e["qn"].startswith(search_string) and store.visible(user, g)]
            return hits or "No elements found"

        def _create(body, kind):
            ic = (body.get("initialClassifications") or {}).get("ZoneMembership") or {}
            zones = [] if store.drop_initial_zones else list(ic.get("zoneMembership") or [])
            p = body["properties"]
            return store._new(user, p.get("typeName", kind), p["qualifiedName"], zones, props=p)

        am.find_assets.side_effect = _find
        am.find_software_capabilities.side_effect = _find
        am.create_asset.side_effect = lambda asset_type=None, body=None: _create(body, "Asset")
        am.create_software_capability.side_effect = lambda body=None: _create(body, "SoftwareCapability")
        am.add_capability_asset_use.return_value = None

        def _classify(guid, body, timeout=30):
            if not store.visible(user, guid):
                raise Denied(f"403 {user} may not classify {guid}")
            name = body["properties"]["class"]
            if name == "ZoneMembershipProperties":
                if store.fail_zone_stamp:
                    raise RuntimeError("500 the ZoneMembership classify failed")
                store.elements[guid]["zones"] = (list(store.zones_after_stamp) if store.zones_after_stamp is not None
                                                 else list(body["properties"]["zoneMembership"]))
            store.events.append(("classify", name, guid, user))

        cx.add_ownership_to_element.side_effect = _classify
        cx.add_zone_membership.side_effect = _classify

        def _read(guid=None, *a, **k):
            if guid in store.unreadable:
                raise ConnectionError("platform unreachable")
            if not store.visible(user, guid):
                raise Denied(f"403 OPEN-METADATA-SECURITY-403-020 {user} may not read {guid}")
            return store.raw(guid)

        me.get_metadata_element_by_guid.side_effect = _read

        def _annotate(body):
            anchor = body.get("anchorGUID")
            if not store.visible(user, anchor):
                raise Denied(f"403 {user} may not create beneath {anchor}")
            return store._new(user, "Annotation", body["properties"]["qualifiedName"], anchor=anchor)

        dd.create_annotation.side_effect = _annotate

        def _new_annotations(survey_report_guid, body=None, start_from=0, page_size=0, **kw):
            """The report's annotations by relationship, paged; the live answer shapes (a list of
            {"elementHeader": {"guid"}, "properties"}, or the string "No elements found")."""
            if store.read_back_error is not None:
                raise store.read_back_error
            hits = [{"elementHeader": {"guid": g, "type": {"typeName": "Annotation"}},
                     "properties": {"qualifiedName": e["qn"]}}
                    for g, e in store.elements.items()
                    if e["kind"] == "Annotation" and e.get("anchor") == survey_report_guid
                    and g not in store.hidden and store.visible(user, g)]
            page = hits[start_from:start_from + page_size] if page_size else hits[start_from:]
            return page or "No elements found"

        dd.get_new_annotations.side_effect = _new_annotations
        ac.get_guid_for_name.side_effect = lambda qn, *a, **k: next(
            (g for g, e in store.elements.items() if e["qn"] == qn and store.visible(user, g)),
            "No elements found")
        by_class = {AssetMaker: am, ClassificationExplorer: cx, MetadataExpert: me, DataDiscovery: dd,
                    AutomatedCuration: ac, ExternalReferences: fake(ExternalReferences),
                    CollectionManager: fake(CollectionManager)}

        class _Clients:
            user_id = user

            def of(self, cls):
                return by_class[cls]

        return _Clients()


# ── fixtures ────────────────────────────────────────────────────────────────────────────────────────────────

@contextmanager
def _signed_in(user):
    from resource_explorer.a2a_auth import current_caller
    from tests.zone_fakes import signed_in

    reset = signed_in(user)
    try:
        yield
    finally:
        current_caller.reset(reset)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A private investigation of OWNER's holding repo `r`, an enforced private zone, the fake Egeria."""
    from resource_explorer import egeria_identity as ident
    from resource_explorer.egeria_clients import _client_user

    monkeypatch.delenv("EXPLORER_DRAFT_ZONE", raising=False)
    monkeypatch.setattr(ident, "_private_zone_state", {"status": "exists", "zone": ident.private_zone(),
                                                       "control_present": True, "enforced": True})
    monkeypatch.setattr(ident, "person_ownership_body", lambda uid: (ident.ownership_body(uid), None))
    store = Store(ident.private_zone())
    monkeypatch.setattr("resource_explorer.egeria_clients.egeria_client",
                        lambda identity, *, purpose, **kw: store.clients_for(_client_user(identity)))
    reg = ProjectRegistry(db_path=str(tmp_path / "reg.db"))
    reg.add(Project(slug="r", display_name="R", github_url="https://github.com/o/r", description=""))
    with _signed_in(OWNER):
        inv = reg.create_investigation("Alice private", project_classification="PersonalProject")
        reg.add_working_set_member(reg.get_or_create_working_set(inv["slug"])["slug"], "repo", "r")
    assert reg.private_owner_for_entity("repo", "r") == OWNER
    return store, reg


def _result():
    from resource_explorer.surveyors.survey_report import Annotation, AnnotationType, SurveyResult

    result = SurveyResult(resource_slug="r", project_display_name="R", github_url="https://github.com/o/r")
    for i in range(2):
        result.add(Annotation(annotation_type=AnnotationType.CLASSIFICATION, summary=f"finding {i}",
                              analysis_step="repo_language"))
    return result


def _publisher(reg):
    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

    return EgeriaPublisher(platform_url="https://localhost:9443", registry=reg)


def _of(store, kind):
    return [g for g, e in store.elements.items() if e["kind"] == kind]


def _reads(store, user, guid) -> bool:
    from pyegeria.omvs.metadata_expert import MetadataExpert

    try:
        store.clients_for(user).of(MetadataExpert).get_metadata_element_by_guid(guid)
        return True
    except Denied:
        return False


# ── the e2e ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_private_members_publish_zones_the_report_and_never_the_asset(world):
    store, reg = world
    from resource_explorer.egeria_identity import private_zone

    with _signed_in(OWNER):
        report_guid = _publisher(reg).publish(_result(), defer_drain=False)

    [asset] = [g for g in _of(store, "Asset")]
    asset_props = store.elements[asset]["props"]
    assert private_zone() not in str(asset_props) and OWNER not in asset_props.get("zoneMembership", []), (
        f"the shared repo asset was created with a private zone: {asset_props}")
    assert store.elements[asset]["zones"] == []
    assert _reads(store, OTHER, asset), "another user cannot read the shared repository asset"

    assert store.elements[report_guid]["zones"] == [private_zone(), OWNER]
    created = next(e for e in store.events if e[:3] == ("create", "SurveyReport", report_guid))
    # Born private: the store recorded the zones the report was CREATED with, before any classify ran on it.
    assert created[4] == [private_zone(), OWNER], f"the report was created with zones {created[4]}"
    assert _reads(store, OWNER, report_guid)
    assert not _reads(store, OTHER, report_guid), "another user read a private investigation's report"

    annotations = _of(store, "Annotation")
    assert len(annotations) == 2
    assert all(not _reads(store, OTHER, g) for g in annotations), "a finding was readable by a non-owner"
    last_report_zone_event = max(i for i, e in enumerate(store.events)
                                 if e[2] == report_guid and e[0] in ("create", "classify"))
    first_annotation = min(i for i, e in enumerate(store.events) if e[1] == "Annotation")
    assert last_report_zone_event < first_annotation, "findings were written before the report was zoned"


def test_the_report_create_body_carries_the_private_zones(world):
    """The shape on the wire: `initialClassifications.ZoneMembership`, never a `zoneMembership` property."""
    _store, reg = world
    from resource_explorer.egeria_identity import private_zone

    with _signed_in(OWNER):
        pub = _publisher(reg)
        pub.publish(_result(), defer_drain=False)
    bodies = [c.kwargs["body"] for c in pub._asset_maker.create_asset.call_args_list]
    report_body = next(b for b in bodies if b["properties"]["typeName"] == "SurveyReport")
    asset_body = next(b for b in bodies if b["properties"]["typeName"] == "Asset")
    assert report_body["initialClassifications"] == {
        "ZoneMembership": {"class": "ZoneMembershipProperties", "zoneMembership": [private_zone(), OWNER]}}
    assert "initialClassifications" not in asset_body
    assert "zoneMembership" not in asset_body["properties"]


def test_the_github_library_is_created_unzoned(world):
    store, reg = world
    with _signed_in(OWNER):
        pub = _publisher(reg)
        pub.publish(_result(), defer_drain=False)
    [scl_body] = [c.kwargs["body"] for c in pub._asset_maker.create_software_capability.call_args_list]
    assert "zoneMembership" not in scl_body["properties"] and "initialClassifications" not in scl_body
    [scl] = _of(store, "SourceControlLibrary")
    assert store.elements[scl]["zones"] == [] and _reads(store, OTHER, scl)


def _activity(reg, status):
    return reg.list_activity(entity_slug="r", status=status, limit=50)


def test_a_report_that_cannot_be_confirmed_private_publishes_no_finding(world):
    """The stamp fails AND the create did not carry the zones: the read shows a public report. Nothing beneath it,
    an error entry in words, and the publish raises (so `repo_publish` writes its failure proof)."""
    store, reg = world
    from resource_explorer.surveyors.egeria_publisher import PrivateReportNotConfirmed

    store.drop_initial_zones = True
    store.fail_zone_stamp = True
    with _signed_in(OWNER), pytest.raises(PrivateReportNotConfirmed) as info:
        _publisher(reg).publish(_result(), defer_drain=False)
    assert _of(store, "Annotation") == []
    assert reg.list_outbox_elements(entity_slug="r") == [], "findings were queued beneath an unconfirmed report"
    words = str(info.value)
    assert "could not be confirmed private" in words and "No findings were written" in words
    assert "Egeria holds zones (none)" in words and "stamp did not land" in words
    [entry] = _activity(reg, "error")
    assert entry["summary"].startswith("NOT published") and "no findings were written" in entry["summary"]


def test_a_stamp_that_failed_on_a_report_born_private_still_publishes(world):
    """The read decides, not the stamp's own answer: born private and read back private is confirmed."""
    store, reg = world
    store.fail_zone_stamp = True
    with _signed_in(OWNER):
        report_guid = _publisher(reg).publish(_result(), defer_drain=False)
    assert not _reads(store, OTHER, report_guid)
    assert len(_of(store, "Annotation")) == 2


def test_a_classification_client_that_cannot_be_built_refuses_a_private_publish(world, monkeypatch):
    """Was: warn, return `{}`, summary "" — a silent leak path. Now: raise before any finding."""
    store, reg = world
    from resource_explorer.surveyors.egeria_publisher import PrivateReportNotConfirmed

    def _boom(*a, **k):
        raise RuntimeError("view server down")

    monkeypatch.setattr("resource_explorer.egeria_identity.classification_client", _boom)
    with _signed_in(OWNER), pytest.raises(PrivateReportNotConfirmed, match="classification client could not"):
        _publisher(reg).publish(_result(), defer_drain=False)
    assert _of(store, "Annotation") == []


def test_a_classification_client_failure_is_never_a_silent_empty_outcome():
    """The non-private case: still reported, element by element, so the summary warns."""
    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher, governance_summary

    pub = EgeriaPublisher(platform_url="https://localhost:9443")
    import resource_explorer.egeria_identity as ident

    real = ident.classification_client
    ident.classification_client = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    try:
        with _signed_in("carol"):
            out = pub._stamp_governance("g1", "g2")
    finally:
        ident.classification_client = real
    assert set(out) == {"g1", "g2"} and not any(o["ownership"] for o in out.values())
    assert "client could not be built" in out["g1"]["reason"]
    assert "⚠ Ownership NOT set on 2 of 2" in governance_summary(pub.last_governance)


def test_a_private_publish_summary_never_says_zones_left_to_egeria():
    from resource_explorer.surveyors.egeria_publisher import governance_summary

    unzoned = {"g": {"ownership": True, "zone_membership": False, "zones": []}}
    assert "zones left to Egeria" in governance_summary(unzoned)
    assert "zones left to Egeria" not in governance_summary(unzoned, private=True)
    assert "NO zone" in governance_summary(unzoned, private=True)
    assert governance_summary({}, private=True) == " (⚠ private publish: no governance outcome recorded)"


def test_an_existing_public_asset_keeps_its_zones_when_its_repo_goes_private(world, monkeypatch):
    """§3.6: someone adds an already-catalogued public repo to a PersonalProject and publishes. The asset, found
    by its qualifiedName, is not re-zoned: it keeps exactly what it had, readable by everyone."""
    from tests.zone_fakes import open_access

    open_access(monkeypatch)      # the adopt-by-name curation check (Brief Z2) is not what this test is about
    store, reg = world
    asset = store._new(OTHER, "Asset", "GitHubRepository::https://github.com/o/r", zones=["team-zone"])
    with _signed_in(OWNER):
        _publisher(reg).publish(_result(), defer_drain=False)
    assert store.elements[asset]["zones"] == ["team-zone"]
    assert not any(e[0] == "classify" and e[1] == "ZoneMembershipProperties" and e[2] == asset
                   for e in store.events)
    assert _reads(store, OTHER, asset)


# ── round 2 (review of the staged change, 2026-10-13) ───────────────────────────────────────────────────────────

def test_a_report_whose_zones_cannot_be_read_back_publishes_no_finding(world):
    """`ZoneReadError` (an outage on the read-back) is never taken for "private"."""
    store, reg = world
    from resource_explorer.surveyors.egeria_publisher import PrivateReportNotConfirmed

    real_new = store._new

    def _new(user, kind, qn, *a, **k):
        guid = real_new(user, kind, qn, *a, **k)
        if kind == "SurveyReport":
            store.unreadable.add(guid)
        return guid

    store._new = _new
    with _signed_in(OWNER), pytest.raises(PrivateReportNotConfirmed, match="could not be read back"):
        _publisher(reg).publish(_result(), defer_drain=False)
    assert _of(store, "Annotation") == []
    assert len(_activity(reg, "error")) == 1


def test_an_extra_zone_on_the_report_is_not_private_enough(world):
    """Exact set only: `[private, owner, x]` admits whoever x admits, so it is refused."""
    store, reg = world
    from resource_explorer.egeria_identity import private_zone
    from resource_explorer.surveyors.egeria_publisher import PrivateReportNotConfirmed

    store.zones_after_stamp = [private_zone(), OWNER, "everyone"]
    with _signed_in(OWNER), pytest.raises(PrivateReportNotConfirmed, match="everyone"):
        _publisher(reg).publish(_result(), defer_drain=False)
    assert _of(store, "Annotation") == []


def test_a_reused_public_report_never_gets_private_findings(world):
    """A 409 on the run's report name meets a report published BEFORE the repo joined the private investigation.
    It is not re-zoned and nothing is attached beneath it: anyone may already have read it."""
    store, reg = world
    from resource_explorer.surveyors.egeria_publisher import PrivateReportNotConfirmed

    result = _result()
    old = store._new(OTHER, "SurveyReport",
                     f"SurveyReport::GitHubRepo::r::{result.surveyed_at.isoformat()}", zones=[])
    with _signed_in(OWNER), pytest.raises(PrivateReportNotConfirmed, match="never attached beneath a report"):
        _publisher(reg).publish(result, defer_drain=False)
    assert store.elements[old]["zones"] == [], "the public report was re-zoned"
    assert not any(e[0] == "classify" and e[2] == old for e in store.events)
    assert _of(store, "Annotation") == []
    assert len(_activity(reg, "error")) == 1


def test_guard_linkage_passes_a_private_refusal_through_untouched():
    """Even when its reason quotes an unknown-GUID code, the refusal is not recorded as a stale link."""
    from resource_explorer.egeria_linkage import StaleEgeriaLinkageError, guard_linkage
    from resource_explorer.surveyors.egeria_publisher import PrivateReportNotConfirmed

    refusal = PrivateReportNotConfirmed("rep-1", OWNER, "read failed: OMRS-REPOSITORY-404-002 unknown entity")
    with pytest.raises(PrivateReportNotConfirmed) as info, guard_linkage(None, "repo", "r", "R", "cached-guid"):
        raise refusal
    assert info.value is refusal and not isinstance(info.value, StaleEgeriaLinkageError)


@pytest.mark.parametrize("who", ["daemon", "other person"])
def test_only_the_signed_in_owner_may_publish_a_private_member(world, who):
    """A daemon (a queued run, the scheduler) or another person: refused BEFORE any write, in waiting words."""
    store, reg = world
    from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as
    from resource_explorer.private_publish import PrivateAwaitsOwner

    if who == "daemon":
        ctx = acting_as(Daemon(DaemonReason.RUN_QUEUE, requested_by=OWNER))
    else:
        ctx = _signed_in(OTHER)
    with ctx, pytest.raises(PrivateAwaitsOwner) as info:
        _publisher(reg).publish(_result(), defer_drain=False)
    assert store.elements == {} and store.events == [], "something was written before the refusal"
    words = str(info.value)
    assert words.startswith(f"waits for {OWNER} to publish") and "nothing was written" in words


def test_a_wait_is_recorded_once_per_resource_and_run(world):
    _store, reg = world
    from resource_explorer.private_publish import WAITING_STATUS, PrivateAwaitsOwner, record_waiting

    wait = PrivateAwaitsOwner("repo", "r", OWNER, "resourceexplorernpa")
    assert record_waiting(reg, wait, run_key="run-1", source="analysis") is True
    assert record_waiting(reg, wait, run_key="run-1", source="analysis") is False
    assert record_waiting(reg, wait, run_key="run-2", source="analysis") is True
    rows = _activity(reg, WAITING_STATUS)
    assert len(rows) == 2 and all(r["summary"].startswith(f"waits for {OWNER}") for r in rows)


def test_the_analysis_auto_publish_records_waiting_not_failed(world, monkeypatch):
    """`workflows.analysis.run_analysis` (an unattended caller): no failure words, `published` None, one wait row
    however many times the same run is retried."""
    _store, reg = world
    from resource_explorer.private_publish import WAITING_STATUS
    from resource_explorer.workflows import analysis

    result = _result()
    monkeypatch.setattr("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator.run",
                        lambda self, slug, **kw: result)
    monkeypatch.setattr(type(reg), "has_assigned_egeria_project", lambda self, et, slug: True)
    monkeypatch.setattr("resource_explorer.workflows.curate.person_write_refusal", lambda *a, **k: "")
    from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

    with acting_as(Daemon(DaemonReason.RUN_QUEUE, requested_by=OWNER)):
        outs = [analysis.run_analysis("r", "repo_language", steps=["repo_language"], registry=reg)
                for _ in range(2)]
    for out in outs:
        assert out.published is None
        assert "failed" not in out.summary and f"waits for {OWNER} to publish" in out.summary
    assert len(_activity(reg, WAITING_STATUS)) == 1


def test_a_deferred_private_publish_drains_now_as_the_owner(world):
    """`publish="background"` would leave the findings to the service-account drain, which Egeria refuses beneath a
    private report. A private member's findings are drained inline, as the owner."""
    store, reg = world
    with _signed_in(OWNER):
        pub = _publisher(reg)
        pub.publish(_result(), defer_drain=True)
    assert pub.publish_deferred is False
    assert pub.publish_read_back is True, "the owner's inline drain read the run back"
    assert len(_of(store, "Annotation")) == 2


def test_the_background_drain_parks_a_private_members_findings_for_the_owner(world):
    """Belt and braces: annotation rows of a private member that reach the service-account drain anyway are left
    pending with the owner's words, never sent and never dead."""
    _store, reg = world
    from pyegeria.omvs.data_discovery import DataDiscovery

    from resource_explorer.egeria_clients import Daemon, DaemonReason
    from resource_explorer.egeria_outbox import OutboxClients, drain_outbox, enqueue_annotations

    result = _result()
    enqueue_annotations(reg, "repo", "r", result.annotations, "report-x", "Annotation::r::t", run_id="Annotation::r::t")
    discovery = create_autospec(DataDiscovery, instance=True, spec_set=True)
    summary = drain_outbox(reg, OutboxClients(discovery=discovery), lambda qn: "",
                           identity=Daemon(DaemonReason.OUTBOX))
    discovery.create_annotation.assert_not_called()
    rows = reg.list_outbox_elements(entity_slug="r")
    assert len(rows) == 2 and summary.get("dead", 0) == 0
    assert all(r["status"] == "pending" and f"the owner, {OWNER}" in (r.get("last_error") or "") for r in rows), rows


def test_a_registry_that_cannot_say_who_owns_it_publishes_nothing(world, monkeypatch):
    """Fail closed: "could not tell" is never "not private". Recorded once per state, not per attempt."""
    store, reg = world
    from resource_explorer.private_publish import PrivateOwnerUnknown

    def _down(self, et, slug):
        raise RuntimeError("registry unreachable")

    monkeypatch.setattr(type(reg), "private_owner_for_entity", _down)
    for _ in range(2):
        with _signed_in(OWNER), pytest.raises(PrivateOwnerUnknown, match="could not tell"):
            _publisher(reg).publish(_result(), defer_drain=False)
    assert store.elements == {}
    monkeypatch.undo()
    assert len(reg.list_activity(entity_slug="r", status="error", limit=10)) == 1


# ── databases and file systems: refused while private (interim) ─────────────────────────────────────────────────

@pytest.fixture
def private_db_and_fs(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "kinds.db"))
    with _signed_in(OWNER):
        inv = reg.create_investigation("Alice kinds", project_classification="PersonalProject")
        ws = reg.get_or_create_working_set(inv["slug"])["slug"]
        reg.add_working_set_member(ws, "database", "db1")
        reg.add_working_set_member(ws, "filesystem", "fs1")
    assert reg.private_owner_for_entity("database", "db1") == OWNER
    return reg


def _no_connect(self):
    raise AssertionError("connected to Egeria: something was about to be written")


@pytest.mark.parametrize("method", ["publish_step_annotations", "publish_local_survey"])
def test_a_private_database_publishes_nothing(private_db_and_fs, monkeypatch, method):
    from types import SimpleNamespace

    from resource_explorer.private_publish import PrivateKindNotSupported
    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor

    reg = private_db_and_fs
    monkeypatch.setattr(EgeriaDatabaseSurveyor, "connect", _no_connect)
    monkeypatch.setattr(EgeriaDatabaseSurveyor, "catalog_and_survey", _no_connect)
    s = EgeriaDatabaseSurveyor(platform_url="https://localhost:9443", view_server="qs-view-server")
    db = SimpleNamespace(slug="db1", display_name="DB1")
    for _ in range(3):                                    # three scheduler cycles
        with pytest.raises(PrivateKindNotSupported) as info:
            if method == "publish_step_annotations":
                s.publish_step_annotations(db, {}, None, "2026-10-13T00:00:00", registry=reg)
            else:
                s.publish_local_survey(db, {}, 0, 0, 0, "2026-10-13T00:00:00", registry=reg)
    assert str(info.value) == ("not published · this database ('db1') is in alice's private investigation, and "
                               "private publishing for databases is not built yet; nothing was written")
    assert len(reg.list_activity(entity_slug="db1", status="error", limit=10)) == 1, "one row per state"


@pytest.mark.parametrize("method", ["publish_step_annotations", "catalog_and_survey"])
def test_a_private_file_system_publishes_nothing(private_db_and_fs, monkeypatch, method):
    from types import SimpleNamespace

    from resource_explorer.private_publish import PrivateKindNotSupported
    from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import (
        EgeriaFileSystemSurveyor,
    )

    reg = private_db_and_fs
    monkeypatch.setattr(EgeriaFileSystemSurveyor, "connect", _no_connect)
    s = EgeriaFileSystemSurveyor(platform_url="https://localhost:9443", view_server="qs-view-server")
    fs = SimpleNamespace(slug="fs1", display_name="FS1", egeria_asset_guid="", local_mount_point="/x",
                         canonical_mount_point="/x", description="")
    for _ in range(3):
        with pytest.raises(PrivateKindNotSupported, match="private publishing for file systems is not built yet"):
            getattr(s, method)(fs, {}, registry=reg)
    assert len(reg.list_activity(entity_slug="fs1", status="error", limit=10)) == 1, "one row per state"


def test_a_public_database_is_not_refused(private_db_and_fs, monkeypatch):
    """The refusal is narrow: a database in no private investigation goes on to its ordinary publish."""
    from types import SimpleNamespace

    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor

    monkeypatch.setattr(EgeriaDatabaseSurveyor, "connect", _no_connect)
    s = EgeriaDatabaseSurveyor(platform_url="https://localhost:9443", view_server="qs-view-server")
    with pytest.raises(AssertionError, match="connected to Egeria"):
        s.publish_step_annotations(SimpleNamespace(slug="db2", display_name="DB2"), {}, None, "t",
                                   registry=private_db_and_fs)
