"""Brief F (2026-10-11): one Publish per page; ratings and notes go to Egeria as the person who wrote them.

The owner, verbatim: "It goes under the user who is logged in and entering the ratings etc. It is also possible for
another user to login and evaluate the same resource and put in different feedback." / "Ratings and notes should be
public too." / one publish button per page, each section showing it will be published.

No Egeria anywhere. The fakes stand at the client boundary only: `egeria_outbox._default_clients` (what the drain
builds its clients from, per identity) returns a recording fake whose every method BINDS the real pyegeria
ClassificationExplorer signature (memory: feedback_fakes_must_bind_real_signatures), and the zone reads are faked at
`zone_access`'s client constructors. The report half of a repository's press is faked at the EgeriaPublisher (as the
G1 tests do); everything of RE's own between the route and the clients runs for real, and every test reads the NEXT
state back through the plan the band reads.
"""
from __future__ import annotations

import inspect
import json
import re
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pyegeria import ClassificationExplorer

from resource_explorer import egeria_outbox as ob
from resource_explorer import feedback_egeria as fe
from resource_explorer import repo_publish as rp
from resource_explorer import single_publish as sp
from resource_explorer.auth import create_access_token
from resource_explorer.journal import Journal
from resource_explorer.registry import Project, ProjectRegistry
from tests.zone_fakes import element as zoned_element
from tests.zone_fakes import install as install_zones

pytestmark = pytest.mark.usefixtures("mock_egeria_client_connections")

ASSET = "11111111-2222-3333-4444-555555555555"
DUP = "SERVER_ERROR_500 OMAG-COMMON-409-001 the qualifiedName {qn} is not available for use"


class Egeria:
    """What Egeria holds, shared by every client view (one view per identity a drain builds clients for)."""

    def __init__(self):
        self.ratings: dict[tuple[str, str], dict] = {}     # (element, createdBy) -> rating
        self.note_logs: dict[str, dict] = {}                 # guid -> {qn, element, createdBy}
        self.notes: dict[str, dict] = {}                     # guid -> {qn, log, createdBy, anchor, description}
        self.tags: dict[str, dict] = {}
        self.tag_links: set[tuple[str, str]] = set()
        self.calls: list[tuple] = []
        self.identities: list = []
        self.fail: dict[str, Exception] = {}
        self.miss_note_log_once = False                      # a concurrent create: the find misses, the create is refused
        self.no_created_by = False                           # the ratings read carries no versions.createdBy
        self._n = 0

    def new(self, prefix):
        self._n += 1
        return f"{prefix}-{self._n:04d}"


class View:
    """One pyegeria ClassificationExplorer as one person. Every method binds the REAL signature first."""

    def __init__(self, eg: Egeria, user: str):
        self.eg, self.user = eg, user

    def _bind(self, name, *a, **k):
        inspect.signature(getattr(ClassificationExplorer, name)).bind(self, *a, **k)   # TypeError on a wrong call
        self.eg.calls.append((name, self.user) + a)
        if name in self.eg.fail:
            raise self.eg.fail[name]

    # ratings
    def add_rating_to_element(self, *a, **k):
        self._bind("add_rating_to_element", *a, **k)
        element_guid, body = a[0], k["body"]
        assert body["class"] == "UpdateElementRequestBody" and body["properties"]["class"] == "RatingProperties"
        key = (element_guid, self.user)
        old = self.eg.ratings.get(key)
        self.eg.ratings[key] = {"guid": old["guid"] if old else self.eg.new("rating"),
                                "star": body["properties"]["starRating"], "review": body["properties"]["review"],
                                "public": k.get("is_public")}
        return {"class": "VoidResponse", "relatedHTTPCode": 200}

    @staticmethod
    def _type_filter(name, k):
        """The type filter pyegeria puts in the request body: the keyword, else the method's DEFAULT (pyegeria 6.2.1
        sends `metadataElementTypeName` = the default when filter_results_by_type). Strict: an element is returned
        only for its own type name (RE must not depend on the filter including subtypes)."""
        if "body" in k and isinstance(k["body"], dict) and k["body"].get("metadataElementTypeName"):
            return k["body"]["metadataElementTypeName"]
        if "metadata_element_type_name" in k:
            return k["metadata_element_type_name"]
        return inspect.signature(getattr(ClassificationExplorer, name)).parameters["metadata_element_type_name"].default

    def get_attached_ratings(self, *a, **k):
        self._bind("get_attached_ratings", *a, **k)
        if self._type_filter("get_attached_ratings", k) != "Rating":
            return "No elements found"
        if self.eg.no_created_by:
            return [{"elementHeader": {"guid": r["guid"]}, "properties": {}} for (el, _u), r in self.eg.ratings.items()
                    if el == a[0]] or "No elements found"
        return [{"elementHeader": {"guid": r["guid"], "versions": {"createdBy": u}},
                 "properties": {"starRating": r["star"], "review": r["review"]}}
                for (el, u), r in self.eg.ratings.items() if el == a[0]] or "No elements found"

    # note logs and notes
    def get_note_logs_by_name(self, *a, **k):
        self._bind("get_note_logs_by_name", *a, **k)
        assert a and a[0] == k["body"]["filter"], "the name travels with the body"
        if self._type_filter("get_note_logs_by_name", k) != "NoteLog":
            return "No elements found"
        if self.eg.miss_note_log_once:
            self.eg.miss_note_log_once = False
            return "No elements found"
        return [{"elementHeader": {"guid": g}, "properties": {"qualifiedName": n["qn"]}}
                for g, n in self.eg.note_logs.items() if n["qn"] == a[0]] or "No elements found"

    def create_note_log(self, *a, **k):
        self._bind("create_note_log", *a, **k)
        qn = k["body"]["properties"]["qualifiedName"]
        if any(n["qn"] == qn for n in self.eg.note_logs.values()):
            raise RuntimeError(DUP.format(qn=qn))
        g = self.eg.new("notelog")
        self.eg.note_logs[g] = {"qn": qn, "element": a[0], "createdBy": self.user}
        return g

    def get_notes_for_note_log(self, *a, **k):
        self._bind("get_notes_for_note_log", *a, **k)
        if self._type_filter("get_notes_for_note_log", k) != "Note":
            return "No elements found"
        return [{"elementHeader": {"guid": g}, "properties": {"qualifiedName": n["qn"]}}
                for g, n in self.eg.notes.items() if n["log"] == a[0]] or "No elements found"

    def create_note(self, *a, **k):
        self._bind("create_note", *a, **k)
        props = k["body"]["properties"]
        if any(n["qn"] == props["qualifiedName"] for n in self.eg.notes.values()):
            raise RuntimeError(DUP.format(qn=props["qualifiedName"]))
        g = self.eg.new("note")
        self.eg.notes[g] = {"qn": props["qualifiedName"], "log": a[0], "createdBy": self.user,
                            "anchor": k["body"]["anchorGUID"], "description": props["description"]}
        return g

    # tags (Brief T's creators)
    def find_tags(self, *a, **k):
        self._bind("find_tags", *a, **k)
        s = k["search_string"].lower()
        return [{"elementHeader": {"guid": g}, "properties": {"displayName": t["name"], "qualifiedName": t["qn"]}}
                for g, t in self.eg.tags.items() if s in (t["name"].lower(), t["qn"].lower())] or "No elements found"

    def create_informal_tag(self, *a, **k):
        self._bind("create_informal_tag", *a, **k)
        g = self.eg.new("tag")
        self.eg.tags[g] = {"name": k["display_name"], "qn": k["qualified_name"]}
        return g

    def add_tag_to_element(self, *a, **k):
        self._bind("add_tag_to_element", *a, **k)
        self.eg.tag_links.add((a[0], a[1]))

    # never
    def update_note(self, *a, **k):
        raise AssertionError("RE never updates a Note")

    def remove_note(self, *a, **k):
        raise AssertionError("RE never deletes a Note")

    def remove_note_log(self, *a, **k):
        raise AssertionError("RE never deletes a NoteLog")

    def remove_rating_from_element(self, *a, **k):
        raise AssertionError("RE never deletes a Rating")


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "f.db"))
    r.add(Project(slug="p", display_name="P repo", github_url="https://github.com/x/p", description=""))
    r.set_egeria_asset_guid("p", ASSET)
    return r


@pytest.fixture
def egeria(monkeypatch):
    eg = Egeria()

    def default_clients(identity=None):
        identity = ob.drain_identity(identity)
        eg.identities.append(identity)
        return ob.OutboxClients(feedback=View(eg, identity.user_id), acting_as=identity.user_id), lambda qn: ""

    monkeypatch.setattr(ob, "_default_clients", default_clients)
    install_zones(monkeypatch)            # no zone in use
    return eg


def caller(user):
    from resource_explorer.a2a_auth import CallerIdentity, current_caller

    return current_caller.set(CallerIdentity(user_id=user, egeria_token="t", auth_source="app-jwt"))


def press(registry, user, slug="p"):
    """The page's Publish after the report, as `user` (what repo_publish.publish_report calls)."""
    from resource_explorer.a2a_auth import current_caller

    reset = caller(user)
    try:
        return sp.send_with_publish(registry, "repo", slug, registry.get_egeria_asset_guid(slug) or "", user)
    finally:
        current_caller.reset(reset)


def plan(registry, viewer):
    return fe.plan_as_dict(fe.feedback_plan(registry, "repo", "p", viewer=viewer))


def rate(registry, user, stars, message):
    return registry.add_resource_feedback("repo", "p", stars, "", message, author=user)["id"]


def note(registry, user, body):
    return Journal(registry).write("repo", "p", author=user, body=body)["id"]


def by_id(items, i):
    return next(x for x in items if x["id"] == i)


# ── the fake is honest ──────────────────────────────────────────────────────────────────────────────────────

def test_the_fake_rejects_a_call_the_real_signature_would_reject(egeria):
    v = View(egeria, "alice")
    with pytest.raises(TypeError):
        v.add_rating_to_element(ASSET, body={}, nonsense=1)
    with pytest.raises(TypeError):
        v.create_note(display_name="no log guid")


# ── one Publish: report, tags and the presser's feedback; another person's untouched and shown as theirs ──

def test_one_press_sends_the_report_tags_and_only_the_pressers_feedback_and_the_next_read_shows_it(registry, egeria,
                                                                                                    monkeypatch):
    from resource_explorer import auth
    from resource_explorer.surveyors import survey_snapshot
    from resource_explorer.surveyors.survey_report import ClassificationAnnotation
    from resource_explorer.web.routes import curate as curate_routes
    from resource_explorer.web.routes import egeria as egeria_routes

    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None, **kw: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setattr(curate_routes, "get_current_user", auth.get_current_user)
    monkeypatch.setattr(egeria_routes, "get_current_user", auth.get_current_user)
    from resource_explorer.web.app import app
    client = TestClient(app)

    def hdr(u):
        return {"Authorization": "Bearer " + create_access_token(user_id=u, egeria_token="t")}

    registry.set_egeria_asset_guid("p", "")                  # the press creates the asset
    registry.set_project_context("repo", "p", "personal")
    survey_snapshot.record_step(registry, "p", "repo_language", "2026-10-11T00:00:00", [
        ClassificationAnnotation(summary="a", analysis_step="repo_language", check_name="c", item_key="a")])
    assert client.post("/api/curate/tags/repo/p", json={"tag": "observability"}, headers=hdr("alice")).status_code == 200
    a_rating, a_note = rate(registry, "alice", 4, "pretty good"), note(registry, "alice", "Good for lineage.")
    b_rating, b_note = rate(registry, "bob", 2, "hard to run"), note(registry, "bob", "Needs Java 17.")

    # before the press, the Publish band's list (one source with the press) names alice's own items only
    st = client.get("/api/egeria/p/publish-state", headers=hdr("alice")).json()
    assert {(i["kind"], i["id"]) for i in st["goes_out"]["feedback"]} == {("rating", a_rating), ("note", a_note)}
    assert [i["name"] for i in st["goes_out"]["findable"]] == ["observability"]
    assert st["goes_out"]["waiting_for_others"] == 2

    with patch("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher") as pub:
        def _publish(result, *a, **k):
            registry.set_egeria_asset_guid("p", ASSET)
            return "rep-1"
        pub.return_value.publish.side_effect = _publish
        pub.return_value.report_reused = False
        pub.return_value.get_survey_reports_by_guid.return_value = [
            {"guid": "rep-1", "qualified_name": "SurveyReport::x", "annotation_count": 1}]
        r = client.post("/api/egeria/p/publish-report", json={}, headers=hdr("alice"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["row"]["confirm"]["state"] == rp.CONFIRMED
    assert body["goes_out"]["count"] == 0, "after a fully successful press nothing of alice's is left to send"

    # Egeria: the tag, alice's rating and note under alice; nothing of bob's
    assert any(link[0] == ASSET for link in egeria.tag_links)
    assert list(egeria.ratings) == [(ASSET, "alice")]
    got = egeria.ratings[(ASSET, "alice")]
    assert (got["star"], got["review"], got["public"]) == ("FOUR_STARS", "pretty good", True)
    assert [n["createdBy"] for n in egeria.notes.values()] == ["alice"]
    assert {i.kind for i in egeria.identities} == {"caller"} and {i.user_id for i in egeria.identities} == {"alice"}

    # the NEXT read, as each person
    tag = client.get("/api/curate/egeria-state/repo/p", headers=hdr("alice")).json()["items"][0]
    assert tag["state"] == "in_egeria" and tag["word"].startswith("published "), "bug B: the tag reads published"
    as_alice = client.get("/api/curate/feedback-state/repo/p", headers=hdr("alice")).json()
    assert by_id(as_alice["ratings"], a_rating)["word"].startswith("published ")
    assert by_id(as_alice["notes"], a_note)["state"] == fe.PUBLISHED
    assert by_id(as_alice["ratings"], b_rating)["word"] == "bob's · waiting for them"
    assert by_id(as_alice["notes"], b_note)["action"] == "", "never sent by alice"
    as_bob = client.get("/api/curate/feedback-state/repo/p", headers=hdr("bob")).json()
    assert by_id(as_bob["ratings"], b_rating)["word"] == "saved · will publish"
    assert by_id(as_bob["notes"], b_note)["action"] == "send"


# ── authorship ──────────────────────────────────────────────────────────────────────────────────────────────

def test_the_daemon_drain_never_sends_feedback_and_another_caller_never_sends_yours(registry, egeria):
    from resource_explorer.a2a_auth import current_caller
    from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

    rid = rate(registry, "bob", 5, "great")
    row = registry.enqueue_outbox_element("repo", "p", fe.RATING_KIND, fe.rating_key(ASSET, "bob"), {
        "entity_type": "repo", "entity_slug": "p", "element_guid": ASSET, "by": "bob", "feedback_id": rid,
        "stars": 5, "review": "great"}, run_id=fe.run_id_for("repo", "p", "bob"))
    with acting_as(Daemon(DaemonReason.OUTBOX)):
        ob.drain_outbox(registry)
        ob.drain_outbox(registry, run_id=fe.run_id_for("repo", "p", "bob"))
    assert registry.get_outbox_element(row)["status"] == "pending" and egeria.ratings == {}
    reset = caller("alice")
    try:
        summary = ob.drain_outbox(registry, run_id=fe.run_id_for("repo", "p", "bob"))
    finally:
        current_caller.reset(reset)
    assert summary["skipped"] == 1 and egeria.ratings == {}
    got = registry.get_outbox_element(row)
    assert got["status"] == "pending" and got["attempts"] == 0 and got["last_error"] == ob.NOT_THIS_PERSON
    press(registry, "bob")
    assert list(egeria.ratings) == [(ASSET, "bob")]
    assert all(i.kind == "caller" for i in egeria.identities), "no client was ever built for the daemon"
    assert {c[1] for c in egeria.calls if c[0] == "add_rating_to_element"} == {"bob"}


def test_a_press_by_someone_who_is_not_the_caller_sends_nothing(registry, egeria):
    rate(registry, "alice", 3, "ok")
    from resource_explorer.a2a_auth import current_caller

    reset = caller("mallory")
    try:
        out = fe.publish_my_feedback(registry, "repo", "p", ASSET, "alice")
    finally:
        current_caller.reset(reset)
    assert out["refused"] == fe.NOT_AUTHOR and out["queued"] == [] and egeria.calls == []


# ── ratings ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_newer_rating_replaces_the_same_persons_and_two_peoples_coexist(registry, egeria):
    first = rate(registry, "peterprofile", 4, "pretty good")
    press(registry, "peterprofile")
    second = rate(registry, "peterprofile", 2, "Still learning")
    rate(registry, "erinoverview", 5, "excellent")
    p = plan(registry, "peterprofile")
    # round 2: until the newer one lands, the older one is what Egeria holds, for everyone who looks
    assert by_id(p["ratings"], first)["state"] == fe.PUBLISHED
    assert by_id(plan(registry, "erinoverview")["ratings"], first)["word"].startswith("published ")
    assert by_id(p["ratings"], second)["word"] == "saved · will publish"
    press(registry, "peterprofile")
    assert by_id(plan(registry, "erinoverview")["ratings"], first)["state"] == fe.REPLACED
    press(registry, "erinoverview")
    assert egeria.ratings[(ASSET, "peterprofile")]["star"] == "TWO_STARS"
    assert egeria.ratings[(ASSET, "peterprofile")]["review"] == "Still learning"
    assert set(egeria.ratings) == {(ASSET, "peterprofile"), (ASSET, "erinoverview")}
    assert registry.list_resource_feedback("repo", "p")[-1]["id"] == first, "the earlier rating stays in RE"
    assert not [c for c in egeria.calls if c[0] == "add_rating_to_element" and c[1] == "peterprofile"][2:]


def test_a_comment_without_stars_stays_in_re(registry, egeria):
    fid = rate(registry, "alice", None, "just a comment")
    assert by_id(plan(registry, "alice")["ratings"], fid)["word"] == "kept in RE · no rating"
    press(registry, "alice")
    assert egeria.ratings == {}


# ── notes ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_each_journal_entry_becomes_one_note_and_a_re_publish_never_duplicates(registry, egeria):
    n1, n2 = note(registry, "alice", "One."), note(registry, "alice", "Two.")
    press(registry, "alice")
    assert len(egeria.note_logs) == 1 and len(egeria.notes) == 2
    (log_guid, nlog), = egeria.note_logs.items()
    assert nlog["qn"] == fe.note_log_qualified_name(ASSET) and nlog["element"] == ASSET
    assert {n["qn"] for n in egeria.notes.values()} == {fe.note_qualified_name(n1), fe.note_qualified_name(n2)}
    assert all(n["log"] == log_guid and n["anchor"] == ASSET for n in egeria.notes.values())
    press(registry, "alice")
    n3 = note(registry, "bob", "Three.")
    press(registry, "bob")
    assert len(egeria.note_logs) == 1 and len(egeria.notes) == 3
    assert by_id(plan(registry, "bob")["notes"], n3)["state"] == fe.PUBLISHED
    # a lost outbox record (re-sent) finds the Note by its qualifiedName rather than adding one
    with registry._conn() as conn:
        conn.execute("UPDATE egeria_outbox SET status='cancelled' WHERE element_kind=?", (fe.NOTE_KIND,))
    press(registry, "alice")
    assert len(egeria.notes) == 3 and len(egeria.note_logs) == 1


def test_a_note_log_created_by_someone_else_at_the_same_moment_is_adopted_not_duplicated(registry, egeria):
    egeria.note_logs["notelog-winner"] = {"qn": fe.note_log_qualified_name(ASSET), "element": ASSET,
                                          "createdBy": "bob"}
    egeria.miss_note_log_once = True                         # our find ran before bob's create landed
    note(registry, "alice", "Mine.")
    press(registry, "alice")
    assert len(egeria.note_logs) == 1
    (n,) = egeria.notes.values()
    assert n["log"] == "notelog-winner" and n["createdBy"] == "alice"


def test_no_module_code_updates_or_deletes_a_note_rating_or_note_log():
    src = inspect.getsource(fe)
    code = "\n".join(line for line in src.splitlines() if not line.strip().startswith(("#", '"""')))
    assert not re.search(r"\.(update_note|update_note_log|remove_note|remove_note_log|remove_rating_from_element|"
                         r"delete_\w+|remove_\w+)\(", code)


def test_feedback_kinds_are_person_only_and_not_destructive():
    assert ob.PERSON_ONLY_KINDS == fe.PERSON_ONLY_KINDS
    for kind in fe.KINDS:
        assert kind in ob._CREATORS and kind in ob._SELF_RESOLVING_KINDS
        assert not ob.is_destructive_outbox_kind(kind)


# ── failures: not sent, never re-sent on their own, re-sent by the person's next Publish ─────────────────

def test_a_failed_note_is_not_sent_never_retried_by_the_drain_and_sent_by_the_next_publish(registry, egeria):
    from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

    nid = note(registry, "alice", "Hello.")
    egeria.fail["create_note"] = RuntimeError("OMAG-REPOSITORY-HANDLER-500 the repository is busy")
    press(registry, "alice")
    item = by_id(plan(registry, "alice")["notes"], nid)
    assert item["state"] == fe.NOT_SENT and "repository is busy" in item["word"] and item["action"] == "send"
    (row,) = registry.list_outbox_rows_for_kinds("repo", "p", (fe.NOTE_KIND,))
    assert row["status"] == "dead" and row["attempts"] == 1
    with acting_as(Daemon(DaemonReason.OUTBOX)):
        ob.drain_outbox(registry)
    assert egeria.notes == {}
    egeria.fail.clear()
    press(registry, "alice")
    assert len(egeria.notes) == 1
    assert by_id(plan(registry, "alice")["notes"], nid)["state"] == fe.PUBLISHED


def test_egeria_unreachable_leaves_each_row_not_sent(registry, egeria, monkeypatch):
    def unreachable(identity=None):
        raise ConnectionError("platform unreachable")

    monkeypatch.setattr(ob, "_default_clients", unreachable)
    nid = note(registry, "alice", "Hello.")
    press(registry, "alice")
    item = by_id(plan(registry, "alice")["notes"], nid)
    assert item["state"] == fe.NOT_SENT and item["word"] == f"not sent · {fe.UNREACHED}"


def test_egerias_security_refusal_reads_not_permitted(registry, egeria):
    rid = rate(registry, "alice", 5, "great")
    egeria.fail["add_rating_to_element"] = RuntimeError(
        "OPEN-METADATA-SECURITY-403-007 User alice is not authorized to issue operation addFeedback.")
    press(registry, "alice")
    item = by_id(plan(registry, "alice")["ratings"], rid)
    assert item["state"] == fe.NOT_PERMITTED and item["word"].startswith("not permitted · ")


# ── access: Brief Z's curation_access with Egeria's feedback operations, refused per item ───────────────────

def test_a_zone_that_grants_create_but_not_add_feedback_refuses_the_rating_and_sends_the_note(registry, egeria,
                                                                                              monkeypatch):
    zones = install_zones(monkeypatch)
    zones.elements[ASSET] = zoned_element(ASSET, zones=["sales-zone"], owners=["someone-else"])
    zones.controls["sales-zone"] = {"associatedSecurityList": {"CREATE": ["allUsers"], "DEFAULT": ["salesTeam"]}}
    rid, nid = rate(registry, "alice", 5, "great"), note(registry, "alice", "Hello.")
    press(registry, "alice")
    p = plan(registry, "alice")
    assert by_id(p["ratings"], rid)["state"] == fe.NOT_PERMITTED
    assert "sales-zone" in by_id(p["ratings"], rid)["word"]
    assert by_id(p["notes"], nid)["state"] == fe.PUBLISHED
    assert egeria.ratings == {} and len(egeria.notes) == 1
    assert fe.RATING_OPERATIONS == ("CREATE", "ADD_FEEDBACK") and fe.NOTE_OPERATIONS == ("CREATE",)


# ── a resource not in Egeria: saved · will publish, nothing sent ───────────────────────────────────────────

def test_with_no_asset_the_entries_are_saved_and_nothing_is_sent(registry, egeria):
    registry.set_egeria_asset_guid("p", "")
    nid = note(registry, "alice", "Hello.")
    assert by_id(plan(registry, "alice")["notes"], nid)["word"] == "saved · will publish"
    assert press(registry, "alice")["skipped"]
    assert egeria.calls == []


# ── one source: the band's list is what the press acts on ───────────────────────────────────────────────────

def test_the_publish_list_is_exactly_what_the_press_sends(registry, egeria):
    rate(registry, "alice", 3, "ok")
    note(registry, "alice", "a")
    note(registry, "bob", "b")
    listed = sp.goes_out(registry, "repo", "p", "alice")
    out = press(registry, "alice")
    assert out["feedback"]["queued"] == len(listed["feedback"]) == 2
    assert sp.goes_out(registry, "repo", "p", "alice")["feedback"] == []


# ── addendum: "confirmed" is one function ──────────────────────────────────────────────────────────────────

def test_confirmation_says_confirmed_not_yet_confirmed_or_what_egeria_shows():
    base = {"word": "published", "read_at": "2026-10-11T16:36:00", "annotation_count": 117}
    assert rp.confirmation({**base, "annotations_in_egeria": 117})["word"] == "published 10-11 · confirmed"
    assert rp.confirmation({**base, "annotations_in_egeria": 117})["state"] == rp.CONFIRMED
    assert rp.confirmation({**base, "annotations_in_egeria": None})["state"] == rp.UNCONFIRMED
    assert rp.confirmation({**base, "word": "sent"})["word"] == "published 10-11 · not yet confirmed"
    d = rp.confirmation({**base, "annotations_in_egeria": 112})
    assert d["state"] == rp.DIFFERS and d["word"] == "published 10-11 · Egeria shows 112"
    assert rp.confirmation({"word": "none"})["state"] == ""


def test_the_report_row_carries_the_confirmation_from_the_proof(registry):
    registry.append_catalogue_commit_proof("p", proof=rp.P_REPORT, node_kind=rp.NODE_REPORT, element_guid="rep",
                                           target_guid=ASSET, recorded_by="alice",
                                           detail={"annotation_count": 5, "annotations_in_egeria": 5})
    assert rp.publish_state(registry, "p")["row"]["confirm"]["state"] == rp.CONFIRMED


# ── §9: what the plan says is written is what the commit writes ──────────────────────────────────────────────

@pytest.mark.parametrize("enrichment", [
    {},
    {"sensitivity": {"value": "confidential", "author": "a"}},
    {"sensitivity": {"value": "odd"}, "criticality": {"value": "critical", "author": "b"}},
    {"retention": {"value": "", "note": "only a note"}},
    {"retention": {"value": "Ongoing"}, "criticality": {"value": "low"}},
])
def test_the_commit_writes_exactly_the_classifications_the_plan_shows(enrichment):
    from resource_explorer.curate_plan import classification_writes, context_facts
    from resource_explorer.workflows.curate_commit import _classification_bodies

    shown = [c for c in classification_writes(enrichment, context_facts(enrichment)) if not c["skipped"]]
    written = _classification_bodies(enrichment)
    assert [w[0] for w in written] == [c["write"][0] for c in shown]
    assert len(written) == len(shown)
    assert json.dumps([w[2]["properties"]["class"] for w in written]) == \
        json.dumps([c["write"][2]["properties"]["class"] for c in shown])


# ── §10: documentation sources go with the repository's Publish, and say whether they did ──────────────────

def test_documentation_sources_carry_their_published_state_and_the_publish_list_names_the_unsent(registry):
    from resource_explorer.curate_plan import context_facts

    docs = [{"id": "d1", "label": "Runbook", "url": "https://x", "added_by": "a", "egeria_external_ref_guid": "ref-1"},
            {"id": "d2", "label": "Wiki", "url": "https://w", "added_by": "a", "egeria_external_ref_guid": ""}]
    (doc_fact,) = [f for f in context_facts({}, docs) if f["key"] == "documentation"]
    assert [d["published"] for d in doc_fact["sources"]] == [True, False]
    registry.list_doc_sources = lambda et, slug: docs
    assert [d["id"] for d in sp.unpublished_doc_sources(registry, "repo", "p")] == ["d2"]
    assert [d["label"] for d in sp.goes_out(registry, "repo", "p", "alice")["doc_sources"]] == ["Wiki"]


def test_the_repository_publish_sends_the_rest_after_the_report_as_the_presser():
    src = inspect.getsource(rp.publish_report)
    assert 'send_with_publish(registry, "repo", slug, asset, author, doc_sources=doc_sources)' in src


# ── round 2: the reads name their type; a missing createdBy is harmless ───────────────────────────────────

def test_r2_a_re_publish_after_a_lost_record_finds_the_note_by_type_note_never_a_second(registry, egeria):
    """find_note names the Note type. With pyegeria's default ('Action') in the body, a strict type filter finds
    nothing and the re-send would create a second Note."""
    note(registry, "alice", "Once.")
    press(registry, "alice")
    with registry._conn() as conn:
        conn.execute("UPDATE egeria_outbox SET status='cancelled' WHERE element_kind=?", (fe.NOTE_KIND,))
    press(registry, "alice")
    assert len(egeria.notes) == 1
    assert [c[0] for c in egeria.calls].count("create_note") == 1, "found by the lookup: no second create attempted"
    (row,) = [r for r in registry.list_outbox_rows_for_kinds("repo", "p", (fe.NOTE_KIND,)) if r["status"] != "cancelled"]
    assert row["status"] == "done", "the re-send adopts the existing Note (with 'Action' it went dead on a 409)"


def test_r2_a_ratings_read_without_created_by_records_no_guid_and_never_adds_twice(registry, egeria):
    egeria.no_created_by = True
    rid = rate(registry, "alice", 4, "good")
    press(registry, "alice")
    (row,) = registry.list_outbox_rows_for_kinds("repo", "p", (fe.RATING_KIND,))
    assert row["status"] == "done" and row["egeria_guid"] == ""
    assert [c[0] for c in egeria.calls].count("add_rating_to_element") == 1
    assert by_id(plan(registry, "alice")["ratings"], rid)["state"] == fe.PUBLISHED
    press(registry, "alice")
    assert [c[0] for c in egeria.calls].count("add_rating_to_element") == 1, "a re-press sends nothing more"



# ── round 2 ────────────────────────────────────────────────────────────────────────────────────────────────

def _client(registry, monkeypatch):
    from resource_explorer import auth
    from resource_explorer.web.routes import curate as curate_routes
    from resource_explorer.web.routes import egeria as egeria_routes

    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None, **kw: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setattr(curate_routes, "get_current_user", auth.get_current_user)
    monkeypatch.setattr(egeria_routes, "get_current_user", auth.get_current_user)
    from resource_explorer.web.app import app
    return TestClient(app)


def _hdr(u):
    return {"Authorization": "Bearer " + create_access_token(user_id=u, egeria_token="t")}


def test_r2_a_press_with_no_kept_survey_sends_no_report_but_sends_the_rest_when_in_egeria(registry, egeria,
                                                                                          monkeypatch):
    client = _client(registry, monkeypatch)
    registry.set_project_context("repo", "p", "personal")
    nid = note(registry, "alice", "Worth reading.")
    st = client.get("/api/egeria/p/publish-state", headers=_hdr("alice")).json()
    assert st["survey"]["exists"] is False and st["goes_out"]["count"] == 1
    r = client.post("/api/egeria/p/publish-report", json={}, headers=_hdr("alice"))
    assert r.status_code == 409 and r.json()["sent"]["feedback"]["queued"] == 1
    assert by_id(plan(registry, "alice")["notes"], nid)["state"] == fe.PUBLISHED
    assert client.get("/api/egeria/p/publish-state", headers=_hdr("alice")).json()["goes_out"]["count"] == 0


def test_r2_a_lower_count_while_the_reports_rows_are_still_sending_is_not_yet_confirmed():
    base = {"word": "published", "read_at": "2026-10-11T16:36:00", "annotation_count": 117,
            "annotations_in_egeria": 112}
    c = rp.confirmation(base, unsettled=5)
    assert c["state"] == rp.UNCONFIRMED and c["word"] == "published 10-11 · not yet confirmed · 5 sending"
    assert rp.confirmation(base, unsettled=0)["state"] == rp.DIFFERS


def test_r2_the_report_row_counts_its_own_runs_unsettled_rows(registry):
    run = "Annotation::p::2026-10-11T00:00:00"
    registry.append_catalogue_commit_proof("p", proof=rp.P_REPORT, node_kind=rp.NODE_REPORT, element_guid="rep",
                                           target_guid=ASSET, recorded_by="alice",
                                           detail={"annotation_count": 3, "annotations_in_egeria": 1,
                                                   "publish_run_id": run})
    a = registry.enqueue_outbox_element("repo", "p", "annotation", f"{run}::a", {}, run_id=run)
    registry.enqueue_outbox_element("repo", "p", "annotation_link", f"{run}::l", {}, run_id=f"{run}::links")
    assert rp.publish_state(registry, "p")["row"]["confirm"]["word"].endswith("not yet confirmed · 2 sending")
    registry.mark_outbox_done(a, "g")
    with registry._conn() as conn:
        conn.execute("UPDATE egeria_outbox SET status='dead' WHERE run_id=?", (f"{run}::links",))
    c = rp.publish_state(registry, "p")["row"]["confirm"]
    # round 5: the one queued annotation landed, and the survey had 3: the cap is said, both counts kept
    assert c["state"] == rp.CONFIRMED and c["published_count"] == 3 and c["queued_count"] == 1
    assert c["word"].endswith("· confirmed · 2 not sent (capped)")


def test_r3_rows_retried_and_done_after_the_read_back_confirm_not_egeria_shows_the_old_count(registry):
    """Round 3: the count read once at publish time (1) is stale once the run's rows land; settled, none dead,
    every annotation done is "confirmed"; only a dead row says what Egeria shows."""
    run = "Annotation::p::2026-10-11T01:00:00"
    registry.append_catalogue_commit_proof("p", proof=rp.P_REPORT, node_kind=rp.NODE_REPORT, element_guid="rep",
                                           target_guid=ASSET, recorded_by="alice",
                                           detail={"annotation_count": 3, "annotations_in_egeria": 1,
                                                   "publish_run_id": run})
    ids = [registry.enqueue_outbox_element("repo", "p", "annotation", f"{run}::{i}", {}, run_id=run)
           for i in range(3)]
    for i in ids:
        registry.mark_outbox_done(i, f"g{i}")
    c = rp.publish_state(registry, "p")["row"]["confirm"]
    assert c["state"] == rp.CONFIRMED and c["word"].endswith("· confirmed") and c["basis"] == "outbox"
    with registry._conn() as conn:
        conn.execute("UPDATE egeria_outbox SET status='dead' WHERE id=?", (ids[2],))
    c = rp.publish_state(registry, "p")["row"]["confirm"]
    assert c["state"] == rp.DIFFERS and c["word"].endswith("Egeria shows 2")


# ── round 3 ────────────────────────────────────────────────────────────────────────────────────────────────

def test_r3_a_second_gated_press_while_one_runs_is_already_running(registry, egeria, monkeypatch):
    """The gated press (no kept survey) takes the same per-resource lock: two presses never queue every item
    twice."""
    import threading

    registry.set_project_context("repo", "p", "personal")
    note(registry, "alice", "Once.")
    started, release = threading.Event(), threading.Event()
    real = sp.send_with_publish

    def slow(*a, **k):
        started.set()
        release.wait(5)
        return real(*a, **k)
    monkeypatch.setattr(sp, "send_with_publish", slow)
    from resource_explorer.a2a_auth import current_caller
    errors = []

    def first():
        reset = caller("alice")
        try:
            rp.publish_report(registry, "p", "alice")
        finally:
            current_caller.reset(reset)
    t = threading.Thread(target=first)
    t.start()
    assert started.wait(5)
    try:
        rp.publish_report(registry, "p", "alice")
    except rp.AlreadyRunning as exc:
        errors.append(exc)
    release.set()
    t.join(10)
    assert errors, "the second press is refused while the first runs"


def test_r3_the_repository_press_sends_doc_sources_once_when_the_publisher_already_did():
    src = inspect.getsource(rp.publish_report)
    assert 'rest(doc_sources=not out.get("doc_sources_sent"))' in src
    import resource_explorer.web.routes.databases as db
    import resource_explorer.web.routes.filesystems as fs
    for mod in (db, fs):
        assert "publish_local_doc_sources(" not in inspect.getsource(mod), mod.__name__


# ── round 4 ────────────────────────────────────────────────────────────────────────────────────────────────

def _proof_with_run(registry, run, published=10):
    registry.append_catalogue_commit_proof("p", proof=rp.P_REPORT, node_kind=rp.NODE_REPORT, element_guid="rep",
                                           target_guid=ASSET, recorded_by="alice",
                                           detail={"annotation_count": published, "annotations_in_egeria": 0,
                                                   "publish_run_id": run})


def test_r4_a_second_press_of_the_same_kept_survey_counts_each_annotation_once(registry):
    """The run id repeats when one kept survey is published twice: the newest row per qualifiedName stands."""
    run = "Annotation::p::2026-10-11T02:00:00"
    _proof_with_run(registry, run)
    first = [registry.enqueue_outbox_element("repo", "p", "annotation", f"{run}::{i}", {}, run_id=run)
             for i in range(10)]
    for i, rid in enumerate(first):
        if i < 8:
            registry.mark_outbox_done(rid, f"g{i}")
        else:
            registry.mark_outbox_failed(rid, "busy", max_attempts=1)      # dead in the first press
    for i in range(10):                                                    # the second press: all land
        registry.mark_outbox_done(registry.enqueue_outbox_element("repo", "p", "annotation", f"{run}::{i}", {},
                                                                  run_id=run), f"h{i}")
    c = rp.publish_state(registry, "p")["row"]["confirm"]
    assert c["state"] == rp.CONFIRMED and c["egeria_count"] == 10, c


def test_r4_confirmed_is_judged_against_what_was_queued_not_the_survey_size(registry):
    run = "Annotation::p::2026-10-11T03:00:00"
    _proof_with_run(registry, run, published=5)                            # the outbox capped the run at 3
    for i in range(3):
        registry.mark_outbox_done(registry.enqueue_outbox_element("repo", "p", "annotation", f"{run}::{i}", {},
                                                                  run_id=run), f"g{i}")
    c = rp.publish_state(registry, "p")["row"]["confirm"]
    assert c["state"] == rp.CONFIRMED and c["published_count"] == 5 and c["queued_count"] == 3
    assert c["word"].endswith("confirmed · 2 not sent (capped)"), "round 5: a capped run never reads plainly confirmed"


def test_r4_a_refused_feedback_row_is_born_cancelled_and_no_retry_revives_it(registry, egeria, monkeypatch):
    zones = install_zones(monkeypatch)
    zones.elements[ASSET] = zoned_element(ASSET, zones=["sales-zone"], owners=["someone-else"])
    zones.controls["sales-zone"] = {"associatedSecurityList": {"DEFAULT": ["salesTeam"]}}
    rid = rate(registry, "alice", 5, "great")
    press(registry, "alice")
    (row,) = registry.list_outbox_rows_for_kinds("repo", "p", (fe.RATING_KIND,))
    assert row["status"] == "cancelled"
    assert by_id(plan(registry, "alice")["ratings"], rid)["state"] == fe.NOT_PERMITTED
    assert registry.retry_outbox_element(row["id"]) is False, "an admin retry cannot revive it"


def test_r4_documentation_sources_are_not_sent_twice_when_the_report_failed_after_the_publisher_sent_them(
        registry, monkeypatch):
    from resource_explorer.surveyors import survey_snapshot
    from resource_explorer.surveyors.survey_report import ClassificationAnnotation

    registry.set_project_context("repo", "p", "personal")
    survey_snapshot.record_step(registry, "p", "repo_language", "2026-10-11T00:00:00", [
        ClassificationAnnotation(summary="a", analysis_step="repo_language", check_name="c", item_key="a")])
    monkeypatch.setattr(rp, "publish_snapshot", lambda *a, **k: {"ok": False, "stage": "publish",
                                                                 "error": "x", "doc_sources_sent": True})
    seen = []
    monkeypatch.setattr(sp, "send_with_publish", lambda *a, **k: seen.append(k.get("doc_sources")) or {})
    rp.publish_report(registry, "p", "alice")
    assert seen == [False]



# ── round 5 ────────────────────────────────────────────────────────────────────────────────────────────────

def test_r5_a_lone_cancelled_annotation_is_not_sent_and_done_anywhere_is_done(registry):
    run = "Annotation::p::2026-10-11T04:00:00"
    _proof_with_run(registry, run, published=3)
    a = registry.enqueue_outbox_element("repo", "p", "annotation", f"{run}::a", {}, run_id=run)
    registry.mark_outbox_done(a, "ga")
    registry.enqueue_outbox_element("repo", "p", "annotation", f"{run}::a", {}, run_id=run,
                                    retired=("dead", "busy"))                # a later re-send of a died: still done
    b = registry.enqueue_outbox_element("repo", "p", "annotation", f"{run}::b", {}, run_id=run)
    registry.mark_outbox_done(b, "gb")
    registry.enqueue_outbox_element("repo", "p", "annotation", f"{run}::c", {}, run_id=run,
                                    retired=("cancelled", "cancelled: by an admin"))   # never sent
    c = rp.publish_state(registry, "p")["row"]["confirm"]
    assert c["state"] == rp.DIFFERS and c["egeria_count"] == 2 and c["queued_count"] == 3
    assert c["word"].endswith("Egeria shows 2 · 1 not sent")


def test_r5_unreadable_access_at_the_press_is_not_sent_not_not_permitted(registry, egeria, monkeypatch):
    zones = install_zones(monkeypatch)
    zones.fail_element = ASSET
    rid = rate(registry, "alice", 5, "great")
    press(registry, "alice")
    item = by_id(plan(registry, "alice")["ratings"], rid)
    assert item["state"] == fe.NOT_SENT and item["word"].startswith("not sent · couldn't check access")
    assert item["action"] == "send" and egeria.ratings == {}
    zones.fail_element = ""
    press(registry, "alice")
    assert by_id(plan(registry, "alice")["ratings"], rid)["state"] == fe.PUBLISHED


# ── round 6 ────────────────────────────────────────────────────────────────────────────────────────────────

def test_r6_a_key_cancelled_twice_is_still_not_sent_never_capped(registry):
    run = "Annotation::p::2026-10-11T05:00:00"
    _proof_with_run(registry, run, published=2)
    registry.mark_outbox_done(registry.enqueue_outbox_element("repo", "p", "annotation", f"{run}::a", {}, run_id=run),
                              "ga")
    for _ in range(2):                                       # two presses, then an admin batch cancel
        registry.enqueue_outbox_element("repo", "p", "annotation", f"{run}::b", {}, run_id=run,
                                        retired=("cancelled", "cancelled: by an admin"))
    c = rp.publish_state(registry, "p")["row"]["confirm"]
    assert c["state"] == rp.DIFFERS and c["queued_count"] == 2 and c["capped"] == 0
    assert c["word"].endswith("Egeria shows 1 · 1 not sent")


def test_r6_unreadable_items_are_said_apart_from_refusals(registry, egeria, monkeypatch):
    zones = install_zones(monkeypatch)
    zones.fail_element = ASSET
    rate(registry, "alice", 5, "great")
    out = press(registry, "alice")["feedback"]
    assert out["not_permitted"] == [] and len(out["unread"]) == 1
    assert out["unread"][0]["reason"].startswith("not sent · couldn't check access")


def test_r7_a_capped_run_that_also_differs_says_both():
    c = rp.confirmation({"word": "published", "read_at": "2026-10-11T16:36:00", "annotation_count": 5},
                        run={"rows": 3, "done": 2, "dead": 1, "not_sent": 0, "unsettled": 0})
    assert c["state"] == rp.DIFFERS and c["word"] == "published 10-11 · Egeria shows 2 · 2 not sent (capped)"
