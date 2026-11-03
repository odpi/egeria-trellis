"""A work-list member's confidence reaches Egeria as CollectionMembership `confidence` (2026-10-13).

The bug: RE sent `expectedConfidence`, which is not a field of Egeria 6.2's `CollectionMembershipProperties`
(`membershipRationale`, `membershipType`, `expression`, `confidence` (int, 0-100, default 0),
`membershipStatus`, `userDefinedStatus`, `steward`, `stewardTypeName`, `stewardPropertyName`, `source`,
`notes`). The class is `@JsonIgnoreProperties(ignoreUnknown=true)`, so Egeria dropped the name, answered
success, and the relationship read `confidence: 0`. The outbox row said done.

No Egeria here. `EgeriaCollections` is the only fake: the CollectionManager write and the MetadataExpert
member-side read, every call bound to the installed pyegeria signature first. It keeps a relationship
property the way Egeria does (known names only, `confidence` 0 when not sent) and answers the read in the
shape measured read-only on 6.2 (2026-10-13): `elementList[].element.elementGUID` is the collection (at end 1,
`elementAtEnd1` True), `type.typeName` the relationship, `relationshipProperties.propertyValueMap.<name>.
primitiveValue` the value. It pages by the BODY's `startFrom`/`pageSize` and answers `starting_at_end` as
pyegeria 6.2.2 does (the member is at end 2: 1 finds nothing). 6.2.1 ignores both keywords. Zone access reads are faked at their own client boundary (`zone_fakes`),
with no zone in use.
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

from resource_explorer.registry import Project, ProjectRegistry

sys.path.insert(0, str(Path(__file__).parent))

from tests.zone_fakes import permit_publish_routes, real_lookup, signed_in  # noqa: E402

pytestmark = pytest.mark.usefixtures("mock_egeria_client_connections")

USER = "garygeeke"

#: Egeria 6.2 `CollectionMembershipProperties` (+ RelationshipBeanProperties' extendedProperties and
#: RelationshipProperties' effectiveFrom/effectiveTo). Anything else in a body is dropped by Egeria.
EGERIA_MEMBERSHIP_FIELDS = frozenset({
    "membershipRationale", "membershipType", "expression", "confidence", "membershipStatus",
    "userDefinedStatus", "steward", "stewardTypeName", "stewardPropertyName", "source", "notes",
    "extendedProperties", "effectiveFrom", "effectiveTo",
})


def _sig(name: str) -> inspect.Signature:
    from pyegeria.omvs.collection_manager import CollectionManager
    from pyegeria.omvs.metadata_expert import MetadataExpert

    cls = MetadataExpert if name == "get_related_metadata_elements" else CollectionManager
    return inspect.signature(getattr(cls, name))


def _value(v):
    kind = "int" if isinstance(v, int) else "string"
    return {"class": "PrimitiveTypePropertyValue", "typeName": kind, "primitiveValue": v}


class EgeriaCollections:
    """The CollectionManager boundary, as Egeria 6.2 behaves for these three calls."""

    def __init__(self, *, keep=EGERIA_MEMBERSHIP_FIELDS, fail_read: bool = False, hide: bool = False,
                 relationship_type: str = "CollectionMembership"):
        self.keep = keep
        self.fail_read = fail_read
        self.hide = hide                                  # the read does not show the membership
        self.relationship_type = relationship_type        # what the read says the relationship is
        self.reads = 0
        self.read_calls: list[dict] = []                  # each read's body and keywords
        self.extra_collections: list[str] = []            # other collections the member is in, listed first
        self.at_end1 = True
        self.sent: list[dict | None] = []                 # every add_to_collection body, as sent
        self.relationships: dict[tuple[str, str], dict] = {}

    def create_collection(self, *a, **kw):
        _sig("create_collection").bind(self, *a, **kw)
        return "ws-guid"

    def add_to_collection(self, *a, **kw):
        bound = _sig("add_to_collection").bind(self, *a, **kw)
        coll, member = bound.arguments["collection_guid"], bound.arguments["element_guid"]
        body = bound.arguments.get("body")
        self.sent.append(body)
        props = dict((body or {}).get("properties") or {})
        kept = {k: v for k, v in props.items() if k in self.keep}
        # Uni-link: a repeat replaces. The int default comes back on every read.
        self.relationships[(coll, member)] = {"class": "CollectionMembershipProperties",
                                             "typeName": "CollectionMembership", "confidence": 0, **kept}

    def get_related_metadata_elements(self, *a, **kw):
        bound = _sig("get_related_metadata_elements").bind(self, *a, **kw)
        self.reads += 1
        body = bound.arguments["body"]
        self.read_calls.append({"body": dict(body), "kwargs": dict(bound.arguments.get("kwargs") or {}),
                                **{k: bound.arguments[k] for k in ("starting_at_end", "start_from", "page_size")
                                   if k in bound.arguments}})
        if self.fail_read:
            raise ConnectionError("platform went away")
        member = bound.arguments["guid"]
        assert bound.arguments["relationship_type"] == "CollectionMembership"
        if bound.arguments.get("starting_at_end", 0) == 1:
            return "No elements found"                    # the member is never at end 1
        rels = [(c, {"confidence": 0}) for c in self.extra_collections]
        rels += [(c, p) for (c, m), p in self.relationships.items() if m == member and not self.hide]
        start, size = body.get("startFrom", 0), body.get("pageSize", 1000)
        page = rels[start:start + size]
        if not page:
            return "No elements found"
        items = [{"relationshipGUID": f"rel-{c}-{member}", "type": {"typeName": self.relationship_type},
                  "element": {"elementGUID": c}, "elementAtEnd1": self.at_end1,
                  "relationshipProperties": {
                      "propertyValueMap": {k: _value(v) for k, v in p.items() if k not in ("class", "typeName")},
                      "propertyNames": sorted(k for k in p if k not in ("class", "typeName"))}}
                 for c, p in page]
        return {"startingElement": {"elementGUID": member}, "elementList": items, "mermaidGraph": ""}


class _Clients:
    def __init__(self, cm):
        self.cm = cm

    def require(self, name):
        assert name in ("collection_manager", "metadata_expert")
        return self.cm


def test_the_fake_rejects_what_real_pyegeria_rejects():
    with pytest.raises(TypeError):
        EgeriaCollections().add_to_collection("c")                       # element_guid is required
    with pytest.raises(TypeError):
        EgeriaCollections().add_to_collection("c", "m", body={}, nonsense=1)
    with pytest.raises(TypeError):
        EgeriaCollections().get_related_metadata_elements("m", "CollectionMembership")   # body is required


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "memconf.db"))
    assert str(tmp_path) in r.database_url                                # never the shared registry
    for slug in ("p", "q"):
        r.add(Project(slug=slug, display_name=slug.upper(), github_url=f"https://github.com/x/{slug}",
                      description=""))
        r.set_egeria_asset_guid(slug, f"g-{slug}")
    return r


def _queue(registry, **extra) -> int:
    payload = {"collection_guid": "wg", "member_guid": "g-q", "member_entity_type": "repo",
               "member_entity_slug": "q", "by": USER, **extra}
    return registry.enqueue_outbox_element("work_list", "w", "collection_membership",
                                           "CollectionMembership::wg::g-q", payload)


def _drain(registry, cm):
    from resource_explorer.egeria_outbox import OutboxClients, drain_outbox

    return drain_outbox(registry, OutboxClients(collection_manager=cm, metadata_expert=cm), lambda qn: "",
                        limit=10)


def _row(registry, slug="w"):
    return registry.list_outbox_elements(entity_slug=slug, limit=10)[0]


# ── the body ──────────────────────────────────────────────────────────────────────────────────────────────

class TestTheBody:
    def test_confidence_is_sent_as_egerias_confidence_never_expected_confidence(self, registry, monkeypatch):
        permit_publish_routes(monkeypatch, USER)
        _queue(registry, membership_rationale="system of record", expected_confidence=80)
        cm = EgeriaCollections()
        _drain(registry, cm)
        props = cm.sent[0]["properties"]
        assert props["class"] == "CollectionMembershipProperties"
        assert props["confidence"] == 80 and "expectedConfidence" not in props
        assert set(props) - {"class"} <= EGERIA_MEMBERSHIP_FIELDS
        assert cm.relationships[("wg", "g-q")]["confidence"] == 80
        assert _row(registry)["status"] == "done"

    def test_no_confidence_recorded_sends_none(self, registry, monkeypatch):
        """Egeria's default is 0, so "not recorded" is NOT sent as 0: the key is simply absent."""
        permit_publish_routes(monkeypatch, USER)
        _queue(registry, membership_rationale="why")
        cm = EgeriaCollections()
        _drain(registry, cm)
        assert "confidence" not in cm.sent[0]["properties"]
        assert cm.sent[0]["properties"]["membershipRationale"] == "why"

    def test_neither_sends_no_body_and_reads_nothing_back(self, registry, monkeypatch):
        permit_publish_routes(monkeypatch, USER)
        _queue(registry)
        cm = EgeriaCollections()
        _drain(registry, cm)
        assert cm.sent == [None] and cm.reads == 0
        assert (_row(registry)["status"], _row(registry)["last_error"]) == ("done", "")

    @pytest.mark.parametrize("raw, sent", [(80, 80), ("80", 80), (79.6, 80), (0, 0), (100, 100)])
    def test_a_number_is_coerced_to_an_int(self, registry, monkeypatch, raw, sent):
        permit_publish_routes(monkeypatch, USER)
        _queue(registry, expected_confidence=raw)
        cm = EgeriaCollections()
        _drain(registry, cm)
        assert cm.sent[0]["properties"]["confidence"] == sent
        assert type(cm.sent[0]["properties"]["confidence"]) is int

    @pytest.mark.parametrize("raw", ["high", True, 101, -1, float("nan")])
    def test_a_non_number_or_out_of_range_is_refused_and_nothing_is_sent(self, registry, monkeypatch, raw):
        permit_publish_routes(monkeypatch, USER)
        _queue(registry, expected_confidence=raw)
        cm = EgeriaCollections()
        _drain(registry, cm)
        assert cm.sent == []
        row = _row(registry)
        assert row["status"] == "dead" and row["last_error"].startswith("not sent: confidence")

    def test_a_payload_carrying_confidence_by_its_egeria_name_is_read_too(self, registry, monkeypatch):
        permit_publish_routes(monkeypatch, USER)
        _queue(registry, confidence=65)
        cm = EgeriaCollections()
        _drain(registry, cm)
        assert cm.sent[0]["properties"]["confidence"] == 65


# ── the read-back ─────────────────────────────────────────────────────────────────────────────────────────

class TestTheReadBack:
    def test_a_property_egeria_did_not_keep_is_named_on_the_row(self, registry, monkeypatch):
        """The shape of the original bug, against a correct name: Egeria answers success and keeps nothing."""
        permit_publish_routes(monkeypatch, USER)
        _queue(registry, membership_rationale="why", expected_confidence=80)
        cm = EgeriaCollections(keep=frozenset({"membershipRationale"}))
        _drain(registry, cm)
        row = _row(registry)
        assert row["status"] == "dead"
        assert row["last_error"] == ("Egeria accepted the membership but did not keep: "
                                     "confidence (sent 80, read back 0)")

    def test_a_property_not_kept_is_dead_on_the_first_failure_and_never_resent(self, registry, monkeypatch):
        """Gate decision 2026-10-13: no retry with backoff. A second drain sends nothing more."""
        permit_publish_routes(monkeypatch, USER)
        _queue(registry, expected_confidence=80)
        cm = EgeriaCollections(keep=frozenset())
        summary = _drain(registry, cm)
        assert summary["dead"] == 1 and summary["failed"] == 0
        row = _row(registry)
        assert (row["status"], row["attempts"]) == ("dead", 1)
        _drain(registry, cm)
        assert len(cm.sent) == 1

    def test_every_property_kept_is_done_with_no_note_after_one_read(self, registry, monkeypatch):
        permit_publish_routes(monkeypatch, USER)
        _queue(registry, membership_rationale="why", expected_confidence=80)
        cm = EgeriaCollections()
        _drain(registry, cm)
        assert (_row(registry)["status"], _row(registry)["last_error"]) == ("done", "")
        assert cm.reads == 1

    def test_a_dropped_rationale_is_named(self, registry, monkeypatch):
        permit_publish_routes(monkeypatch, USER)
        _queue(registry, membership_rationale="why", expected_confidence=80)
        cm = EgeriaCollections(keep=frozenset({"confidence"}))
        _drain(registry, cm)
        row = _row(registry)
        assert row["status"] == "dead"
        assert row["last_error"] == ("Egeria accepted the membership but did not keep: "
                                     "membershipRationale (sent 'why', read back None)")

    def test_a_membership_missing_from_the_read_back_is_done_and_says_so(self, registry, monkeypatch):
        permit_publish_routes(monkeypatch, USER)
        _queue(registry, expected_confidence=80)
        cm = EgeriaCollections(hide=True)
        _drain(registry, cm)
        row = _row(registry)
        assert row["status"] == "done"
        assert row["last_error"] == "written; properties not confirmed: the membership is not in the read-back"

    def test_another_relationship_to_the_collection_is_not_taken_for_the_membership(self, registry, monkeypatch):
        permit_publish_routes(monkeypatch, USER)
        _queue(registry, expected_confidence=80)
        cm = EgeriaCollections(relationship_type="ResourceList")
        _drain(registry, cm)
        assert _row(registry)["last_error"] == ("written; properties not confirmed: "
                                                "the membership is not in the read-back")

    def test_the_read_pages_in_the_body_from_the_members_end(self, registry, monkeypatch):
        """pyegeria 6.2.1 ignores page_size/starting_at_end keywords; a body startFrom/pageSize works on both."""
        permit_publish_routes(monkeypatch, USER)
        monkeypatch.setattr("resource_explorer.egeria_outbox.MEMBERSHIP_READ_BACK_PAGE", 2)
        _queue(registry, expected_confidence=80)
        cm = EgeriaCollections()
        cm.extra_collections = ["other-1", "other-2", "other-3"]      # pages: [o1, o2] [o3, wg]
        _drain(registry, cm)
        assert (_row(registry)["status"], _row(registry)["last_error"]) == ("done", "")
        assert [c["body"] for c in cm.read_calls] == [
            {"class": "ResultsRequestBody", "startFrom": 0, "pageSize": 2},
            {"class": "ResultsRequestBody", "startFrom": 2, "pageSize": 2}]
        assert all(c["starting_at_end"] == 2 and "page_size" not in c and "start_from" not in c
                   for c in cm.read_calls)

    def test_a_short_page_does_not_end_the_read_only_an_empty_one_does(self, registry, monkeypatch):
        permit_publish_routes(monkeypatch, USER)
        monkeypatch.setattr("resource_explorer.egeria_outbox.MEMBERSHIP_READ_BACK_PAGE", 2)
        _queue(registry, expected_confidence=80)
        cm = EgeriaCollections(hide=True)
        cm.extra_collections = ["other-1", "other-2", "other-3"]      # pages: [o1, o2] [o3] []
        _drain(registry, cm)
        assert cm.reads == 3
        assert _row(registry)["last_error"] == ("written; properties not confirmed: "
                                                "the membership is not in the read-back")

    def test_an_item_with_the_collection_not_at_end_1_is_not_the_membership(self, registry, monkeypatch):
        permit_publish_routes(monkeypatch, USER)
        _queue(registry, expected_confidence=80)
        cm = EgeriaCollections()
        cm.at_end1 = False
        _drain(registry, cm)
        assert _row(registry)["last_error"] == ("written; properties not confirmed: "
                                                "the membership is not in the read-back")

    def test_a_read_that_never_ends_stops_and_says_so(self, registry, monkeypatch):
        permit_publish_routes(monkeypatch, USER)
        monkeypatch.setattr("resource_explorer.egeria_outbox.MEMBERSHIP_READ_BACK_PAGE", 1)
        monkeypatch.setattr("resource_explorer.egeria_outbox.MEMBERSHIP_READ_BACK_MAX_PAGES", 3)
        _queue(registry, expected_confidence=80)
        cm = EgeriaCollections(hide=True)
        cm.extra_collections = [f"other-{i}" for i in range(10)]
        _drain(registry, cm)
        assert cm.reads == 3
        assert _row(registry)["last_error"] == ("written; properties not confirmed: "
                                                "the read-back stopped after 3 pages")

    def test_a_failed_read_leaves_the_write_done_and_says_so(self, registry, monkeypatch):
        permit_publish_routes(monkeypatch, USER)
        _queue(registry, expected_confidence=80)
        cm = EgeriaCollections(fail_read=True)
        _drain(registry, cm)
        row = _row(registry)
        assert row["status"] == "done"
        assert row["last_error"] == ("written; properties not confirmed: read-back failed "
                                     "(ConnectionError: platform went away)")
        assert cm.relationships[("wg", "g-q")]["confidence"] == 80


# ── end to end: set a member's confidence, publish, drain ─────────────────────────────────────────────────

def test_work_list_publish_lands_the_members_confidence_on_the_relationship(registry, monkeypatch):
    from resource_explorer.work_lists import WorkLists

    permit_publish_routes(monkeypatch, USER)
    cm = EgeriaCollections()
    monkeypatch.setattr("resource_explorer.egeria_outbox._default_clients", lambda who=None: (_Clients(cm), real_lookup()))
    wls = WorkLists(registry)
    slug = wls.create("W", ["p", "q"], entity_type="repo", created_by=USER)["slug"]
    wls.set_member(slug, "q", rationale="the system of record", confidence=80)
    wls.set_member(slug, "p", rationale="a candidate")            # no confidence recorded

    token = signed_in(USER)
    try:
        out = wls.publish(slug)
    finally:
        from resource_explorer.a2a_auth import current_caller

        current_caller.reset(token)
    assert out["egeria_guid"] == "ws-guid" and out["queued_members"] == 2
    queued = {json.loads(r["payload_json"])["member_guid"]: json.loads(r["payload_json"])
              for r in registry.list_outbox_elements(entity_slug=slug, limit=10)}
    assert queued["g-q"]["expected_confidence"] == 80 and "expected_confidence" not in queued["g-p"]

    # Applied inside the publish (owner's ruling 2026-10-13): each add's row is done and read back before it returns,
    # so the background drain finds nothing left.
    assert {a["entity_slug"]: (a["state"], a["status"]) for a in out["attach"]} == {
        "p": ("attached", "done"), "q": ("attached", "done")}
    assert out["attach_error"] == ""
    assert _drain(registry, cm)["claimed"] == 0
    assert cm.relationships[("ws-guid", "g-q")] == {
        "class": "CollectionMembershipProperties", "typeName": "CollectionMembership",
        "confidence": 80, "membershipRationale": "the system of record"}
    assert cm.relationships[("ws-guid", "g-p")]["confidence"] == 0              # Egeria's default, not RE's
    sent_p = [b for b in cm.sent if b and b["properties"].get("membershipRationale") == "a candidate"]
    assert len(sent_p) == 1 and "confidence" not in sent_p[0]["properties"]   # RE sent none for p
    assert all("expectedConfidence" not in (b or {}).get("properties", {}) for b in cm.sent)


def test_the_route_checks_confidence_with_the_outboxs_own_rule(registry, monkeypatch):
    """One source: the route's 422 comes from `membership_confidence`, the function the outbox sends with.
    (Pydantic's int had it backwards: it took `true` and refused 79.6.)"""
    from fastapi.testclient import TestClient

    from resource_explorer.work_lists import WorkLists

    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    monkeypatch.setattr("resource_explorer.auth.get_current_user",
                        lambda request: {"user_id": USER, "egeria_token": "t", "role": "user"})
    from resource_explorer.web.app import app

    slug = WorkLists(registry).create("W", ["q"], entity_type="repo", created_by=USER)["slug"]
    client = TestClient(app)
    for bad, words in ((150, "outside Egeria's 0-100"), (True, "is not a number"), ("high", "is not a number")):
        r = client.post(f"/api/work-lists/{slug}/members", json={"entity_slug": "q", "confidence": bad})
        assert r.status_code == 422 and words in r.json()["detail"], (bad, r.text)
    r = client.post(f"/api/work-lists/{slug}/members", json={"entity_slug": "q", "confidence": 79.6})
    assert r.status_code == 200, r.text
    assert [m["confidence"] for m in r.json()["members"]] == [80]


class TestSetMember:
    def test_a_rationale_only_update_keeps_the_confidence(self, registry):
        """journal.py's `set_member(slug, entity, rationale=body)` used to write confidence = NULL."""
        from resource_explorer.work_lists import WorkLists

        wls = WorkLists(registry)
        slug = wls.create("W", ["q"], entity_type="repo", created_by=USER)["slug"]
        wls.set_member(slug, "q", rationale="first", confidence=70)
        wls.set_member(slug, "q", rationale="a journal entry")
        assert [(m["rationale"], m["confidence"]) for m in wls.get(slug)["members"]] == [("a journal entry", 70)]

    def test_a_second_journal_suggestion_keeps_the_confidence(self, registry):
        from resource_explorer.journal import Journal
        from resource_explorer.work_lists import WorkLists

        j = Journal(registry)
        slug = j.write("repo", "q", author=USER, body="first", suggest_to=["dbas"])["work_lists"][0]["work_list"]
        WorkLists(registry).set_member(slug, "q", rationale="first", confidence=70)
        j.write("repo", "q", author=USER, body="written again", suggest_to=["dbas"])   # journal.py:125
        m = WorkLists(registry).get(slug)["members"][0]
        assert (m["rationale"], m["confidence"]) == ("written again", 70)

    def test_a_value_egeria_cannot_hold_is_not_stored(self, registry):
        from resource_explorer.work_lists import WorkLists

        wls = WorkLists(registry)
        slug = wls.create("W", ["q"], entity_type="repo", created_by=USER)["slug"]
        with pytest.raises(ValueError):
            wls.set_member(slug, "q", confidence=101)
        assert wls.get(slug)["members"][0]["confidence"] is None
