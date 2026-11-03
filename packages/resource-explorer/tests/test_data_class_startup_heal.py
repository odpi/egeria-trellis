"""The data-class seed at start-up: canary read, heal only what is missing, record the outcome.

End to end through the monitor's own pass (`bootstrap._data_class_pass`, which `bootstrap._loop`
calls after the Dr.Egeria batch heal): RE's real client factory, real registry (temp SQLite), real
seed file. Only the Egeria client boundary is faked, and every fake method binds pyegeria's REAL
signature. The fake Egeria holds elements by (type, qualifiedName) and relationships by
(type, end1, end2). An untyped by-name lookup RESOLVES a plain ValidValueDefinition, as the live
classification-explorer route does; the Reference Data view's FIND refuses an untyped call.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import uuid

import pytest
from pyegeria.omvs.data_designer import DataDesigner as _RealDD
from pyegeria.omvs.metadata_expert import MetadataExpert as _RealMX
from pyegeria.omvs.reference_data import ReferenceDataManager as _RealRef

from resource_explorer import bootstrap
from resource_explorer.registry import ProjectRegistry
from resource_explorer.surveyors.database import bootstrap_data_classes as bdc

NONE_FOUND = "No elements found"
VVD = "ValidValueDefinition"


def _sig(cls, name):
    return inspect.signature(getattr(cls, name))


class FakeEgeria:
    """What the platform holds: {qualifiedName: (type, guid)}, plus every write in order."""

    def __init__(self):
        self.elements: dict[str, tuple[str, str]] = {}
        self.writes: list[tuple] = []
        self.fail_lookups = False
        self.refuse_creates = ""
        self.refuse_links = 0                 # refuse the next N member links
        self.fail_after_creates = 0           # raise a bare error after N creates
        self.relationships: set[tuple[str, str, str]] = set()
        self.member_bodies: list[dict] = []

    def create(self, type_name, qn):
        self.writes.append(("create", type_name, qn))
        creates = len([w for w in self.writes if w[0] == "create"])
        if self.fail_after_creates and creates > self.fail_after_creates:
            raise KeyError("unexpected")
        return self.add(type_name, qn)

    def link(self, rel, end1, end2):
        self.writes.append(("link", rel, end1, end2))
        self.relationships.add((rel, end1, end2))

    def add(self, type_name, qn):
        guid = str(uuid.UUID(int=len(self.elements) + 1))     # GUID-shaped: the unique-name read accepts nothing else
        self.elements[qn] = (type_name, guid)
        return guid


class _Client:
    egeria: FakeEgeria
    cls: type

    def __init__(self, *a, **k):
        pass

    def create_egeria_bearer_token(self, *_a, **_k):
        return "tok"

    def set_bearer_token(self, *_a, **_k):
        pass

    def close_session(self):
        pass

    def get_guid_for_name(self, *args, **kwargs):
        b = _sig(self.cls, "get_guid_for_name").bind(self, *args, **kwargs)
        b.apply_defaults()
        a = b.arguments
        if self.egeria.fail_lookups:
            raise RuntimeError("Egeria is not answering")
        found = self.egeria.elements.get(a["name"])
        if not found:
            return NONE_FOUND
        type_name, guid = found
        if a["type_name"] and a["type_name"] != type_name:
            return NONE_FOUND
        return guid


class FakeDesigner(_Client):
    cls = _RealDD

    def create_data_class(self, *args, **kwargs):
        body = _sig(_RealDD, "create_data_class").bind(self, *args, **kwargs).arguments["body"]
        qn = body["properties"]["qualifiedName"]
        if self.egeria.refuse_creates and self.egeria.refuse_creates in qn:
            raise RuntimeError("OMAG-REPOSITORY-HANDLER-400-001 The DataClass create was refused")
        return self.egeria.create("DataClass", qn)

    def link_data_class_definition(self, *args, **kwargs):
        a = _sig(_RealDD, "link_data_class_definition").bind(self, *args, **kwargs).arguments
        self.egeria.link("DataValueDefinition", a["data_definition_guid"], a["data_class_guid"])


class FakeRef(_Client):
    cls = _RealRef

    def create_valid_value_definition(self, *args, **kwargs):
        body = _sig(_RealRef, "create_valid_value_definition").bind(self, *args, **kwargs).arguments["body"]
        qn = body["properties"]["qualifiedName"]
        return self.egeria.create(VVD, qn)

    def link_valid_value_definition(self, *args, **kwargs):
        a = _sig(_RealRef, "link_valid_value_definition").bind(self, *args, **kwargs).arguments
        assert a["body"]["class"] == "NewRelationshipRequestBody"
        self.egeria.member_bodies.append(a["body"])
        if self.egeria.refuse_links:
            self.egeria.refuse_links -= 1
            raise RuntimeError("OMAG-COMMON-503-001 The link could not be made")
        self.egeria.link("ValidValueMember", a["vv_set_guid"], a["vv_member_guid"])

    def find_valid_value_definitions(self, *args, **kwargs):
        b = _sig(_RealRef, "find_valid_value_definitions").bind(self, *args, **kwargs)
        b.apply_defaults()
        a = b.arguments
        if a["kwargs"].get("metadata_element_type") != VVD:
            raise AssertionError("untyped find: the view would hide plain ValidValueDefinitions")
        if self.egeria.fail_lookups:
            raise RuntimeError("Egeria is not answering")
        hits = [{"elementHeader": {"guid": g}, "properties": {"qualifiedName": qn}}
                for qn, (t, g) in self.egeria.elements.items()
                if t == VVD and qn.startswith(a["search_string"])]
        return hits or NONE_FOUND


class FakeExpert(_Client):
    cls = _RealMX

    def get_metadata_element_by_unique_name(self, *args, **kwargs):
        """The unique-name read (production's exact-qualifiedName lookup): the element with its type, or
        pyegeria's miss sentence; a raise when Egeria is not answering."""
        b = _sig(_RealMX, "get_metadata_element_by_unique_name").bind(self, *args, **kwargs)
        b.apply_defaults()
        a = b.arguments
        if self.egeria.fail_lookups:
            raise RuntimeError("Egeria is not answering")
        found = self.egeria.elements.get(a["name"])
        if not found:
            return NONE_FOUND
        type_name, guid = found
        return {"elementGUID": guid, "type": {"typeName": type_name, "superTypeNames": ["Referenceable"]}}

    def get_related_metadata_elements(self, *args, **kwargs):
        a = _sig(_RealMX, "get_related_metadata_elements").bind(self, *args, **kwargs).arguments
        if self.egeria.fail_lookups:
            raise RuntimeError("Egeria is not answering")
        guid, rel = a["guid"], a["relationship_type"]
        others = [e2 if e1 == guid else e1 for (r, e1, e2) in self.egeria.relationships
                  if r == rel and guid in (e1, e2)]
        if not others:
            return NONE_FOUND
        return {"elementList": [{"relationshipGUID": f"rel-{o}", "element": {"elementGUID": o}}
                                for o in others]}


@pytest.fixture
def egeria(monkeypatch):
    eg = FakeEgeria()
    monkeypatch.setattr("resource_explorer.egeria_clients._daemon_credential", lambda: ("re-daemon", "pw"))
    monkeypatch.setenv("EGERIA_PLATFORM_URL", "https://egeria-blocked.invalid:9443")
    monkeypatch.setenv("EGERIA_VIEW_SERVER", "v")
    monkeypatch.delenv(bdc.EXTRA_SEED_ENV, raising=False)
    monkeypatch.setattr("pyegeria.omvs.data_designer.DataDesigner", type("DD", (FakeDesigner,), {"egeria": eg}))
    monkeypatch.setattr("pyegeria.omvs.reference_data.ReferenceDataManager", type("RD", (FakeRef,), {"egeria": eg}))
    monkeypatch.setattr("pyegeria.omvs.metadata_expert.MetadataExpert", type("MX", (FakeExpert,), {"egeria": eg}))
    monkeypatch.setattr(bdc, "_last_key", None)
    monkeypatch.setattr(bdc, "_last_outcome", None)
    return eg


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "heal.db"))


def _rows(registry):
    return [r for r in registry.list_activity(entity_type="egeria", entity_slug="data-classes")]


def _start(registry):
    """What a worker start runs: the monitor's pass (after its Dr.Egeria batch heal)."""
    return bootstrap._data_class_pass(registry=registry)


def _complete(egeria, drop_last_keyword=False, drop_member_link=None):
    """What a complete earlier run leaves: every element and both kinds of link, no canary."""
    for dc in bdc.STANDARD_DATA_CLASSES:
        dg = egeria.add("DataClass", bdc.data_class_qn(dc["name"]))
        sg = egeria.add(VVD, bdc.keyword_set_qn(dc["name"]))
        egeria.relationships.add(("DataValueDefinition", sg, dg))
        kws = dc["dataPatterns"][:-1] if drop_last_keyword else dc["dataPatterns"]
        for kw in kws:
            kg = egeria.add(VVD, bdc.keyword_qn(dc["name"], kw))
            if bdc.keyword_qn(dc["name"], kw) != drop_member_link:
                egeria.relationships.add(("ValidValueMember", sg, kg))


N_KEYWORDS = sum(len(dc["dataPatterns"]) for dc in bdc.STANDARD_DATA_CLASSES)


def test_first_start_creates_everything_second_start_creates_nothing(egeria, registry):
    first = _start(registry)
    creates = [w for w in egeria.writes if w[0] == "create"]
    assert first.status == bdc.CREATED and first.sets == 6
    assert first.line == (f"data classes: created, 6 sets ({6 + 6 + N_KEYWORDS} elements created, "
                          f"{N_KEYWORDS + 6} links made)")
    assert len(creates) == 6 + 6 + N_KEYWORDS + 1                     # + the canary, created last
    assert creates[-1][2] == bdc.canary_qualified_name(bdc.STANDARD_DATA_CLASSES)
    assert len([w for w in egeria.writes if w[0] == "link" and w[1] == "ValidValueMember"]) == N_KEYWORDS
    assert len([w for w in egeria.writes if w[0] == "link" and w[1] == "DataValueDefinition"]) == 6
    rows = _rows(registry)
    assert len(rows) == 1 and rows[0]["summary"] == first.line and rows[0]["status"] == "ok"
    assert all(i.get("guid") for i in rows[0]["items"] if i["kind"] != "link")

    bdc._last_key = None                                                 # a new process
    before = list(egeria.writes)
    second = _start(registry)
    assert egeria.writes == before, "a second start wrote to Egeria"
    assert second.status == bdc.PRESENT and second.line == "data classes: present, 6 sets"
    assert [r["summary"] for r in _rows(registry)][0] == "data classes: present, 6 sets"


def test_a_steady_state_pass_records_nothing_new(egeria, registry):
    _start(registry)
    _start(registry)
    _start(registry)
    assert len(_rows(registry)) == 2, "the first 'present' is recorded once, then not every pass"


def test_a_failed_check_creates_nothing(egeria, registry):
    egeria.fail_lookups = True
    out = _start(registry)
    assert egeria.writes == []
    assert out.status == bdc.COULD_NOT_CHECK
    assert out.line.startswith("data classes: could not check · could not check 'ValidValueDefinition::ResourceExplorer")
    assert "Egeria is not answering" in out.line
    assert _rows(registry)[0]["status"] == "error"


def test_elements_already_present_are_adopted_and_only_the_missing_keyword_is_created(egeria, registry):
    """The state a manual run on main's code leaves: everything there, no canary. Plus one keyword
    added to the seed since: only it (and the canary) is created, linked into the existing set."""
    _complete(egeria, drop_last_keyword=True)
    out = _start(registry)
    created = [w[2] for w in egeria.writes if w[0] == "create"]
    last_kws = [bdc.keyword_qn(dc["name"], dc["dataPatterns"][-1]) for dc in bdc.STANDARD_DATA_CLASSES]
    assert sorted(created[:-1]) == sorted(last_kws)
    assert created[-1] == bdc.canary_qualified_name(bdc.STANDARD_DATA_CLASSES)
    assert not [w for w in egeria.writes if w[0] == "link" and w[1] == "DataValueDefinition"], "existing pairs are not re-linked"
    assert len([w for w in egeria.writes if w[0] == "link" and w[1] == "ValidValueMember"]) == 6, "only the new keywords"
    assert out.status == bdc.CREATED and out.sets == 6


def test_only_the_canary_missing_is_present_not_created(egeria, registry):
    _complete(egeria)
    out = _start(registry)
    assert [w[2] for w in egeria.writes] == [bdc.canary_qualified_name(bdc.STANDARD_DATA_CLASSES)]
    assert out.status == bdc.PRESENT and out.line == "data classes: present, 6 sets"


def test_a_refused_create_stops_and_records_egerias_sentence(egeria, registry):
    egeria.refuse_creates = "DataClass::PhoneNumber"
    out = _start(registry)
    assert out.status == bdc.FAILED
    assert "OMAG-REPOSITORY-HANDLER-400-001 The DataClass create was refused" in out.line
    assert bdc.canary_qualified_name(bdc.STANDARD_DATA_CLASSES) not in egeria.elements, "no canary on a failed pass"
    item = [i for i in _rows(registry)[0]["items"] if i.get("egeria_said")][0]
    assert item["qualified_name"] == "DataClass::PhoneNumber"


def test_a_changed_seed_heals_on_the_next_start(egeria, registry, tmp_path, monkeypatch):
    _start(registry)
    extra = tmp_path / "more.yaml"
    extra.write_text("data_classes:\n  - name: IpAddress\n    displayName: IP Address\n"
                     "    description: A network address.\n    dataPatterns: [ip, ip_address]\n")
    monkeypatch.setenv(bdc.EXTRA_SEED_ENV, str(extra))
    before = len(egeria.writes)
    out = _start(registry)
    created = [w[2] for w in egeria.writes[before:] if w[0] == "create"]
    assert created[:4] == ["DataClass::IpAddress", "ValidValuesSet::IpAddressKeywords",
                           "ValidValueDefinition::IpAddressKeyword::ip",
                           "ValidValueDefinition::IpAddressKeyword::ip_address"]
    assert out.sets == 7


def test_the_monitor_loop_runs_the_data_class_pass(monkeypatch, tmp_path):
    import threading

    calls = []
    monkeypatch.setattr(bootstrap, "check_and_heal", lambda *_a, **_k: calls.append("batches"))
    monkeypatch.setattr(bootstrap, "_data_class_pass", lambda **_k: calls.append("data classes"))
    monkeypatch.setattr(bootstrap, "check_prefect_pool_workers", lambda *_a, **_k: None)
    stop = threading.Event()
    monkeypatch.setattr(stop, "wait", lambda *_a: stop.set())
    bootstrap._loop(tmp_path, 1, stop)
    assert calls == ["batches", "data classes"]


def test_the_manual_script_takes_the_same_path(egeria, registry):
    assert bdc.bootstrap_data_classes(registry=registry) == 0
    assert bdc.bootstrap_data_classes(registry=registry) == 0
    rows = _rows(registry)
    assert [r["summary"] for r in rows][0] == "data classes: present, 6 sets"


def test_status_reports_the_last_outcome(egeria, registry):
    _start(registry)
    status = bootstrap.get_status(Path("/nonexistent"))["data_classes"]
    assert status["line"].startswith("data classes: created")


# ── the seed file ─────────────────────────────────────────────────────────────

SIX = [
    ("EmailAddress", "Email Address", "Electronic mail address identifier.",
     ["email", "email_address", "mail_addr", "emailaddr"]),
    ("PhoneNumber", "Phone Number", "Telephone contact number.",
     ["phone", "phone_number", "telephone", "mobile", "tel_num"]),
    ("SocialSecurityNumber", "Social Security Number", "Government issued social security identification number.",
     ["ssn", "socialsec", "social_security", "ssn_num"]),
    ("CreditCardNumber", "Credit Card Number", "Financial credit or debit card identifier.",
     ["creditcard", "credit_card", "cc_num", "card_number"]),
    ("Password", "Password", "Credential or secret authentication passkey.",
     ["password", "passkey", "passwd", "pwd"]),
    ("DateOfBirth", "Date of Birth", "Individual date or anniversary of birth.",
     ["dob", "dateofbirth", "birth_date", "birthdate"]),
]


def test_the_shipped_seed_is_the_six_exactly():
    assert [(d["name"], d["displayName"], d["description"], d["dataPatterns"])
            for d in bdc.STANDARD_DATA_CLASSES] == SIX


@pytest.mark.parametrize("body, words", [
    ("nothing: here\n", "expected a top-level 'data_classes' list"),
    ("data_classes: []\n", "no data classes"),
    ("data_classes:\n  - name: Bad Name\n    displayName: x\n    description: y\n    dataPatterns: [a]\n",
     "entry 1 (Bad Name): 'name' must be letters and digits"),
    ("data_classes:\n  - name: Ok\n    displayName: x\n    description: y\n    dataPatterns: [Upper]\n",
     "entry 1 (Ok): keyword 'Upper'"),
    ("data_classes:\n  - name: Ok\n    displayName: x\n    description: y\n    dataPatterns: []\n",
     "entry 1 (Ok): 'dataPatterns' must be a non-empty list"),
    ("data_classes:\n  - name: Ok\n    displayName: ''\n    description: y\n    dataPatterns: [a]\n",
     "entry 1 (Ok): 'displayName' must be a non-empty string"),
    ("data_classes:\n  - name: Ok\n    displayName: x\n    description: y\n    dataPatterns: [a]\n    colour: red\n",
     "entry 1 (Ok): unknown field(s) ['colour']"),
    ("data_classes:\n  - name: Ok\n    displayName: x\n    description: y\n    dataPatterns: [a]\n"
     "  - name: Ok\n    displayName: x\n    description: y\n    dataPatterns: [b]\n",
     "entry 2 (Ok): data class 'Ok' appears twice"),
    ("data_classes: [unclosed\n", "unreadable"),
])
def test_a_bad_seed_entry_is_named(tmp_path, body, words):
    f = tmp_path / "seed.yaml"
    f.write_text(body)
    with pytest.raises(bdc.SeedError) as exc:
        bdc.load_data_class_seed(f, extra_path="")
    assert str(f) in str(exc.value) and words in str(exc.value)


def test_a_second_file_adds_entries_and_a_clash_is_an_error(tmp_path):
    more = tmp_path / "more.yaml"
    more.write_text("data_classes:\n  - name: IpAddress\n    displayName: IP\n    description: d\n    dataPatterns: [ip]\n")
    assert [d["name"] for d in bdc.load_data_class_seed(extra_path=str(more))][-1] == "IpAddress"
    clash = tmp_path / "clash.yaml"
    clash.write_text("data_classes:\n  - name: Password\n    displayName: P\n    description: d\n    dataPatterns: [pw]\n")
    with pytest.raises(bdc.SeedError, match=r"\['Password'\] are already in"):
        bdc.load_data_class_seed(extra_path=str(clash))


def test_a_bad_seed_at_start_is_recorded_not_raised(egeria, registry, tmp_path, monkeypatch):
    bad = tmp_path / "bad.yaml"
    bad.write_text("data_classes: [1]\n")
    monkeypatch.setenv(bdc.EXTRA_SEED_ENV, str(bad))
    out = _start(registry)
    assert out.status == bdc.FAILED and "entry 1: not a mapping" in out.line
    assert egeria.writes == []


# ── review fixes (MED-1, MED-3, LOW-1, LOW-5, LOW-6, member body) ─────────────

def test_a_refused_member_link_is_healed_next_pass_without_recreating_the_keyword(egeria, registry):
    egeria.refuse_links = 1
    first = _start(registry)
    assert first.status == bdc.FAILED and "OMAG-COMMON-503-001" in first.line
    kw_qn = bdc.keyword_qn("EmailAddress", "email")
    assert kw_qn in egeria.elements and not any(r[0] == "ValidValueMember" for r in egeria.relationships)
    before = list(egeria.writes)
    second = _start(registry)
    new = egeria.writes[len(before):]
    assert ("create", VVD, kw_qn) not in new, "the keyword was created again"
    set_guid, kw_guid = egeria.elements[bdc.keyword_set_qn("EmailAddress")][1], egeria.elements[kw_qn][1]
    assert ("link", "ValidValueMember", set_guid, kw_guid) in new
    assert second.status == bdc.CREATED
    # Every member link exactly once, every class linked to its set.
    members = [r for r in egeria.relationships if r[0] == "ValidValueMember"]
    assert len(members) == N_KEYWORDS
    assert len([r for r in egeria.relationships if r[0] == "DataValueDefinition"]) == 6


def test_a_missing_definition_link_is_made_when_both_ends_exist(egeria, registry):
    _complete(egeria)
    sg = egeria.elements[bdc.keyword_set_qn("Password")][1]
    dg = egeria.elements[bdc.data_class_qn("Password")][1]
    egeria.relationships.discard(("DataValueDefinition", sg, dg))
    out = _start(registry)
    links = [w for w in egeria.writes if w[0] == "link"]
    assert links == [("link", "DataValueDefinition", sg, dg)]
    assert out.status == bdc.CREATED and out.line.endswith("(1 link made)")


def test_a_failed_link_check_creates_and_links_nothing(egeria, registry, monkeypatch):
    _complete(egeria, drop_member_link=bdc.keyword_qn("Password", "pwd"))

    def boom(self, *a, **k):
        raise RuntimeError("related-elements read failed")
    monkeypatch.setattr(FakeExpert, "get_related_metadata_elements", boom)
    out = _start(registry)
    assert [w for w in egeria.writes if w[0] == "link"] == []
    assert out.status == bdc.COULD_NOT_CHECK and "related-elements read failed" in out.line


def test_the_member_link_body_carries_only_egerias_property_names(egeria, registry):
    _start(registry)
    assert egeria.member_bodies
    for body in egeria.member_bodies:
        props = dict(body["properties"])
        assert props.pop("class") == "ValidValueMemberProperties"
        assert set(props) <= set(bdc.MEMBER_PROPERTY_NAMES), props


def test_the_same_refusal_every_pass_is_one_row(egeria, registry):
    """Pass 1 creates EmailAddress's elements then is refused (one row, with those GUIDs). Every
    later pass creates nothing and meets the same refusal: one more row, then none."""
    egeria.refuse_creates = "DataClass::PhoneNumber"
    for _ in range(5):
        _start(registry)
    assert len(_rows(registry)) == 2


def test_canary_present_but_an_element_missing_heals_without_a_second_canary(egeria, registry):
    _start(registry)
    del egeria.elements[bdc.keyword_set_qn("DateOfBirth")]
    egeria.relationships = {r for r in egeria.relationships}
    before = len(egeria.writes)
    out = _start(registry)
    created = [w[2] for w in egeria.writes[before:] if w[0] == "create"]
    assert bdc.keyword_set_qn("DateOfBirth") in created
    assert bdc.canary_qualified_name(bdc.STANDARD_DATA_CLASSES) not in created
    assert out.status == bdc.CREATED


def test_an_unexpected_error_after_a_create_is_failed_not_could_not_check(egeria, registry):
    egeria.fail_after_creates = 2
    out = _start(registry)
    assert out.status == bdc.FAILED


def test_a_lost_leadership_writes_nothing(egeria, registry, monkeypatch):
    monkeypatch.setattr(bdc, "_still_bootstrap_leader", lambda: False)
    out = _start(registry)
    assert egeria.writes == []
    assert out.status == bdc.COULD_NOT_CHECK and "no longer holds the bootstrap lock" in out.line


def test_still_leader_is_none_where_no_election_ran_and_false_after_a_lost_session():
    from resource_explorer import leader_election as le

    assert le.still_leader("never-won-here") is None
    lock = le.LeaderLock("vvd-test-lock", database_url="postgresql://x/y")
    lock._held, lock._conn = True, type("C", (), {"cursor": lambda self: (_ for _ in ()).throw(OSError("gone"))})()
    le._held_locks["vvd-test-lock"] = lock
    try:
        assert le.still_leader("vvd-test-lock") is False
    finally:
        le._held_locks.pop("vvd-test-lock", None)
        lock._held, lock._conn = False, None


def test_the_keyword_find_rejects_an_unexpected_shape(egeria, registry, monkeypatch):
    _complete(egeria)
    monkeypatch.setattr(FakeRef, "find_valid_value_definitions", lambda self, *a, **k: {"weird": 1})
    del egeria.elements[bdc.keyword_qn("Password", "pwd")]   # force a heal of an existing set
    out = _start(registry)
    assert out.status == bdc.COULD_NOT_CHECK and "unreadable answer" in out.line
    assert [w for w in egeria.writes if w[0] == "create"] == []


def test_the_seed_is_capped(tmp_path):
    f = tmp_path / "big.yaml"
    f.write_text("data_classes:\n" + "".join(
        f"  - name: C{i}\n    displayName: c\n    description: d\n    dataPatterns: [k]\n"
        for i in range(bdc.MAX_DATA_CLASSES + 1)))
    with pytest.raises(bdc.SeedError, match="more than"):
        bdc.load_data_class_seed(f, extra_path="")
