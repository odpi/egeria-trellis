"""7h: RE in Egeria's actor graph, through pyegeria's ActorManager.

(a) The startup bootstrap ensures `ITProfile::ResourceExplorer`, `UserIdentity::<daemon userId>`
    and their ProfileIdentity link — adopting what is there by qualifiedName, creating only on a
    plain "absent", refusing duplicates and conflicting links, never raising.
(b) Ownership for a person names their profile (Egeria's own shape) when their UserIdentity is
    linked to one, else the userId form; a failed lookup falls back and is reported.

Every test drives RE's real path and fakes ONLY the pyegeria client classes (patched on the
`pyegeria` module, where RE imports them). The fake ActorManager is a small in-memory Egeria: what
a create writes, the next read returns. Nothing here contacts Egeria.
"""
from __future__ import annotations

import functools
import inspect
import logging

import pyegeria
import pytest

#: The REAL pyegeria classes, captured at import (before any fixture patches the module). Round 4:
#: every fake method is bound against the real method's signature first, so a call real pyegeria
#: would reject (a missing required `name`, an unknown keyword) fails here too.
_REAL = {name: getattr(pyegeria, name) for name in ("ActorManager", "ClassificationExplorer", "MetadataExpert")}


def _signature_faithful(fake_cls, real_cls):
    """Wrap each method `fake_cls` defines (and __init__) so its arguments are first bound
    against the same method on `real_cls`. A method the real class lacks is an AttributeError."""
    for attr in [a for a, v in vars(fake_cls).items() if inspect.isfunction(v) and not a.startswith("_")] + ["__init__"]:
        fake = getattr(fake_cls, attr)
        real_sig = inspect.signature(getattr(real_cls, attr))

        def checked(self, *args, __fake=fake, __sig=real_sig, __attr=attr, **kwargs):
            try:
                __sig.bind(self, *args, **kwargs)
            except TypeError as exc:
                raise TypeError(f"{real_cls.__name__}.{__attr}(): {exc}") from None
            return __fake(self, *args, **kwargs)

        setattr(fake_cls, attr, functools.wraps(fake)(checked))
    return fake_cls

NPA, NPA_PW = "resourceexplorernpa", "fake-npa-password-for-tests"
HAND_PROFILE = "3fe25189-f315-4507-90bd-f00f096503a8"
HAND_IDENTITY = "4152cba3-dce2-4367-a074-54d155a8b444"
NONE = "No elements found"


#: Egeria's type hierarchy, as far as these tests reach (OpenMetadataType: Person, Team and
#: ITProfile are ActorProfiles; ActorProfile, UserIdentity and GlossaryTerm are Referenceables).
SUPERTYPE = {"Person": "ActorProfile", "Team": "ActorProfile", "ITProfile": "ActorProfile",
             "ActorProfile": "Referenceable", "UserIdentity": "Referenceable",
             "GlossaryTerm": "Referenceable"}


def _is_a(type_name, want):
    while type_name:
        if type_name == want:
            return True
        type_name = SUPERTYPE.get(type_name)
    return False


class FakeEgeria:
    """The actor graph, and every call that reached the pyegeria boundary."""

    def __init__(self):
        self.elements: dict[str, dict] = {}          # guid -> {type, props}
        self.links: list[tuple[str, str]] = []       # (identity guid, profile guid)
        self.calls: list[tuple] = []                 # (class, method, built-for user, token)
        self.built: list[dict] = []
        self.fail: dict[str, BaseException] = {}
        self.ownership: list[tuple[str, dict]] = []
        self.searches: list[dict] = []               # what each by-name search sent
        self.override: dict[str, object] = {}        # method -> canned answer
        self.delay = 0.0
        self.closed: list[str] = []                  # class of each client RE closed
        self._n = 0

    def add(self, type_name, guid=None, **props):
        self._n += 1
        guid = guid or f"guid-{self._n:04d}"
        self.elements[guid] = {"type": type_name, "props": dict(props)}
        return guid

    def _shape(self, guid, depth=True):
        e = self.elements[guid]
        out = {"elementHeader": {"guid": guid, "type": {"typeName": e["type"]}},
               "properties": dict(e["props"])}
        if depth:
            if e["type"] == "UserIdentity":
                prof = [p for (i, p) in self.links if i == guid]
                if prof:
                    out["userProfile"] = {"relatedElement": self._shape(prof[0], depth=False)}
            else:
                ids = [i for (i, p) in self.links if p == guid]
                if ids:
                    out["userIdentities"] = [{"relatedElement": self._shape(i, depth=False)} for i in ids]
        return out

    def by_name(self, name, type_name):
        """Like Egeria's getRootElementsByName: `metadataElementTypeName` and its subtypes only."""
        hits = [g for g, e in self.elements.items()
                if _is_a(e["type"], type_name)
                and name in (e["props"].get("qualifiedName"), e["props"].get("userId"))]
        return [self._shape(g) for g in hits] or NONE

    def classes(self):
        eg = self

        class _Base:
            name = ""

            def __init__(self, *args, **kwargs):
                self._rec = {"cls": self.name, "user": args[2] if len(args) > 2 else "", "token": None}
                eg.built.append(self._rec)

            def set_bearer_token(self, token):
                self._rec["token"] = token

            def create_egeria_bearer_token(self, *a, **k):
                self._rec["token"] = f"minted-for-{self._rec['user']}"
                return self._rec["token"]

            def close_session(self):
                eg.closed.append(self.name)

            def _call(self, method):
                eg.calls.append((self.name, method, self._rec["user"], self._rec["token"]))
                if method in eg.fail:
                    raise eg.fail[method]

        class ActorManager(_Base):
            name = "ActorManager"

            def _search(self, method, name, kw, default_type):
                self._call(method)
                body = kw.get("body") or {}
                eg.searches.append({"method": method, "kwargs": sorted(kw), "body": body, "name": name})
                if eg.delay:
                    import time as _t
                    _t.sleep(eg.delay)
                if method in eg.override:
                    return eg.override[method]
                # Egeria's handler uses the body's metadataElementTypeName when set, else its own type.
                return eg.by_name(name if name is not None else body.get("filter"),
                                  body.get("metadataElementTypeName") or default_type)

            def get_actor_profiles_by_name(self, name=None, **kw):
                return self._search("get_actor_profiles_by_name", name, kw, "ActorProfile")

            def get_user_identities_by_name(self, name=None, **kw):
                return self._search("get_user_identities_by_name", name, kw, "UserIdentity")

            def get_user_identity_by_guid(self, guid, **kw):
                self._call("get_user_identity_by_guid")
                return eg._shape(guid)

            def get_actor_profile_by_guid(self, guid, **kw):
                self._call("get_actor_profile_by_guid")
                return eg._shape(guid)

            def create_actor_profile(self, body):
                self._call("create_actor_profile")
                p = body["properties"]
                return eg.add(p["typeName"], **{k: v for k, v in p.items() if k not in ("class", "typeName")})

            def create_user_identity(self, body):
                self._call("create_user_identity")
                p = body["properties"]
                return eg.add("UserIdentity", **{k: v for k, v in p.items() if k not in ("class", "typeName")})

            def link_identity_to_profile(self, identity_guid, profile_guid, body=None):
                self._call("link_identity_to_profile")
                eg.links.append((identity_guid, profile_guid))

        class ClassificationExplorer(_Base):
            name = "ClassificationExplorer"

            def add_ownership_to_element(self, guid, body):
                self._call("add_ownership_to_element")
                eg.ownership.append((guid, body["properties"]))

            def add_zone_membership(self, guid, body):
                self._call("add_zone_membership")

        class MetadataExpert(_Base):
            name = "MetadataExpert"

            def get_metadata_element_by_guid(self, guid, **kw):
                self._call("get_metadata_element_by_guid")
                return {"elementGUID": guid, "classifications": [],
                        "elementProperties": {"propertyValueMap": {}}}

            def update_metadata_element_properties(self, guid, body):
                self._call("update_metadata_element_properties")

        return (_signature_faithful(ActorManager, _REAL["ActorManager"]),
                _signature_faithful(ClassificationExplorer, _REAL["ClassificationExplorer"]),
                _signature_faithful(MetadataExpert, _REAL["MetadataExpert"]))

    def writes(self):
        return [c[1] for c in self.calls if c[1].startswith(("create_", "link_"))]


@pytest.fixture
def egeria(monkeypatch):
    import pyegeria

    from resource_explorer import config, egeria_actors, egeria_clients

    cfg = config.get_config().model_copy(deep=True)
    cfg.egeria.user_id, cfg.egeria.user_password = NPA, NPA_PW
    monkeypatch.setattr(config, "get_config", lambda: cfg)
    monkeypatch.setattr(egeria_clients, "_source_logged", True)
    eg = FakeEgeria()
    am, ce, me = eg.classes()
    monkeypatch.setattr(pyegeria, "ActorManager", am)
    monkeypatch.setattr(pyegeria, "ClassificationExplorer", ce)
    monkeypatch.setattr(pyegeria, "MetadataExpert", me)
    monkeypatch.setattr(egeria_actors, "_identity_state", None)
    egeria_actors.clear_actor_cache()
    yield eg
    egeria_actors.clear_actor_cache()


def _hand_made(eg, user=NPA):
    """What the owner created by hand on 2026-10-09."""
    eg.add("ITProfile", HAND_PROFILE, qualifiedName="ITProfile::ResourceExplorer", displayName="Resource Explorer")
    eg.add("UserIdentity", HAND_IDENTITY, qualifiedName=f"UserIdentity::{user}", userId=user)
    eg.links.append((HAND_IDENTITY, HAND_PROFILE))


# ── (a) the startup bootstrap ─────────────────────────────────────────────

def test_bootstrap_adopts_the_hand_made_profile_identity_and_link_and_writes_nothing(egeria):
    from resource_explorer.egeria_actors import ensure_re_identity_in_egeria, identity_words, re_identity_status

    _hand_made(egeria)
    state = ensure_re_identity_in_egeria()
    assert state["status"] == "present" and state["created"] == []
    assert (state["it_profile_guid"], state["user_identity_guid"]) == (HAND_PROFILE, HAND_IDENTITY)
    assert egeria.writes() == []
    assert len(egeria.elements) == 2 and egeria.links == [(HAND_IDENTITY, HAND_PROFILE)]
    # As the daemon: the ActorManager was built for RE's own account and minted its token.
    assert {(b["cls"], b["user"], b["token"]) for b in egeria.built} == {
        ("ActorManager", NPA, f"minted-for-{NPA}")}
    assert identity_words(re_identity_status()) == "present"


def test_bootstrap_on_an_empty_egeria_creates_all_three_and_the_next_run_finds_them(egeria):
    from resource_explorer.egeria_actors import ensure_re_identity_in_egeria, identity_words

    first = ensure_re_identity_in_egeria()
    assert first["status"] == "created"
    assert first["created"] == ["ITProfile", "UserIdentity", "ProfileIdentity"]
    assert identity_words(first) == "created (ITProfile, UserIdentity, ProfileIdentity)"
    types = sorted((e["type"], e["props"]["qualifiedName"]) for e in egeria.elements.values())
    assert types == [("ITProfile", "ITProfile::ResourceExplorer"), ("UserIdentity", f"UserIdentity::{NPA}")]
    ident = next(g for g, e in egeria.elements.items() if e["type"] == "UserIdentity")
    assert egeria.elements[ident]["props"]["userId"] == NPA
    # The next read shows the state: a second start adopts, writes nothing, duplicates nothing.
    n_writes = len(egeria.writes())
    second = ensure_re_identity_in_egeria()
    assert second["status"] == "present" and second["created"] == []
    assert len(egeria.writes()) == n_writes and len(egeria.elements) == 2 and len(egeria.links) == 1
    assert (second["it_profile_guid"], second["user_identity_guid"]) == (
        first["it_profile_guid"], first["user_identity_guid"])


def test_bootstrap_links_an_unlinked_identity_to_the_profile(egeria):
    from resource_explorer.egeria_actors import ensure_re_identity_in_egeria

    _hand_made(egeria)
    egeria.links.clear()
    state = ensure_re_identity_in_egeria()
    assert state["status"] == "created" and state["created"] == ["ProfileIdentity"]
    assert egeria.links == [(HAND_IDENTITY, HAND_PROFILE)]


@pytest.mark.parametrize("setup, words", [
    ("two_profiles", "2 profile elements named 'ITProfile::ResourceExplorer'"),
    ("two_identities", f"2 UserIdentity elements named 'UserIdentity::{NPA}'"),
    ("same_userid_other_qn", f"a UserIdentity for userId '{NPA}' already exists as 'npa-identity'"),
    ("linked_elsewhere", "already linked to another profile (Person::someone)"),
    ("profile_wrong_type", "is a Person, not an ITProfile"),
    # Round 3: held by a type outside ActorProfile; a typed search would hide it and RE create a twin.
    ("profile_held_by_a_term", "is a GlossaryTerm, not an ITProfile"),
    ("identity_wrong_type", f"'UserIdentity::{NPA}' is a Person, not a UserIdentity"),
])
def test_bootstrap_refuses_what_it_should_not_resolve_and_creates_nothing(egeria, setup, words):
    from resource_explorer.egeria_actors import ensure_re_identity_in_egeria

    if setup == "two_profiles":
        _hand_made(egeria)
        egeria.add("ITProfile", qualifiedName="ITProfile::ResourceExplorer")
    elif setup == "two_identities":
        _hand_made(egeria)
        egeria.add("UserIdentity", qualifiedName=f"UserIdentity::{NPA}", userId=NPA)
    elif setup == "same_userid_other_qn":
        egeria.add("ITProfile", HAND_PROFILE, qualifiedName="ITProfile::ResourceExplorer")
        egeria.add("UserIdentity", qualifiedName="npa-identity", userId=NPA)
    elif setup == "linked_elsewhere":
        _hand_made(egeria)
        egeria.links.clear()
        other = egeria.add("Person", qualifiedName="Person::someone")
        egeria.links.append((HAND_IDENTITY, other))
    elif setup == "profile_wrong_type":
        egeria.add("Person", qualifiedName="ITProfile::ResourceExplorer")
    elif setup == "profile_held_by_a_term":
        egeria.add("GlossaryTerm", qualifiedName="ITProfile::ResourceExplorer")
    elif setup == "identity_wrong_type":
        egeria.add("ITProfile", HAND_PROFILE, qualifiedName="ITProfile::ResourceExplorer")
        egeria.add("Person", qualifiedName=f"UserIdentity::{NPA}")
    before = (dict(egeria.elements), list(egeria.links))
    state = ensure_re_identity_in_egeria()
    assert state["status"] == "could not check"
    assert state["reason"].startswith("refused: ") and words in state["reason"], state["reason"]
    assert egeria.writes() == []
    assert (egeria.elements, egeria.links) == before


def test_a_new_daemon_userid_gets_its_own_identity_on_the_same_profile_and_the_old_link_is_reported(egeria):
    from resource_explorer import config
    from resource_explorer.egeria_actors import ensure_re_identity_in_egeria, identity_words

    _hand_made(egeria, user="erinoverview")             # the earlier daemon userId
    config.get_config().egeria.user_id = NPA
    state = ensure_re_identity_in_egeria()
    assert state["status"] == "created" and state["created"] == ["UserIdentity", "ProfileIdentity"]
    assert state["it_profile_guid"] == HAND_PROFILE
    assert (HAND_IDENTITY, HAND_PROFILE) in egeria.links, "the old link is never deleted"
    assert (state["user_identity_guid"], HAND_PROFILE) in egeria.links
    assert any("erinoverview" in n and "no delete path" in n for n in state["notes"]), state["notes"]
    assert "also linked to ITProfile::ResourceExplorer: erinoverview" in identity_words(state)


@pytest.mark.parametrize("failure", ["raises", "unreadable"])
def test_bootstrap_that_cannot_read_egeria_reports_it_and_never_creates(egeria, failure, monkeypatch):
    from resource_explorer.egeria_actors import ensure_re_identity_in_egeria, identity_words

    if failure == "raises":
        egeria.fail["get_actor_profiles_by_name"] = ConnectionError("platform down")
    else:   # pyegeria's dynamic_catch can hand back None: never read as "absent"
        am = __import__("pyegeria").ActorManager
        monkeypatch.setattr(am, "get_actor_profiles_by_name", lambda self, *a, **k: None)
    state = ensure_re_identity_in_egeria()
    assert state["status"] == "could not check"
    assert identity_words(state).startswith("could not check (ITProfile search: ")
    assert egeria.writes() == []


def test_bootstrap_with_no_daemon_userid_says_so(egeria):
    from resource_explorer import config
    from resource_explorer.egeria_actors import ensure_re_identity_in_egeria

    config.get_config().egeria.user_id = ""
    state = ensure_re_identity_in_egeria()
    assert state == {**state, "status": "could not check", "reason": "no daemon userId is configured"}
    assert egeria.calls == []


def test_the_worker_bootstrap_logs_one_line_and_never_raises(egeria, caplog):
    from resource_explorer import worker

    _hand_made(egeria)
    with caplog.at_level(logging.INFO, logger="resource_explorer.worker"):
        worker._bootstrap_re_identity()
    lines = [r.getMessage() for r in caplog.records if r.name == "resource_explorer.worker"]
    assert lines == [f"RE identity in Egeria: present (ITProfile {HAND_PROFILE}, UserIdentity {HAND_IDENTITY})"]

    caplog.clear()
    egeria.fail["get_actor_profiles_by_name"] = TimeoutError("slow")
    with caplog.at_level(logging.INFO, logger="resource_explorer.worker"):
        state = worker._bootstrap_re_identity()
    recs = [r for r in caplog.records if r.name == "resource_explorer.worker"]
    assert [(r.levelno, r.getMessage()) for r in recs] == [
        (logging.WARNING, "RE identity in Egeria: could not check (ITProfile search: TimeoutError: slow)")]
    assert state["status"] == "could not check"


def test_the_bootstrap_reason_is_a_closed_daemon_reason():
    from resource_explorer.egeria_clients import DaemonReason

    assert DaemonReason("identity_bootstrap") is DaemonReason.IDENTITY_BOOTSTRAP
    assert DaemonReason("actor_lookup") is DaemonReason.ACTOR_LOOKUP


def test_whoami_shows_the_recorded_identity_words(egeria, monkeypatch):
    from fastapi.testclient import TestClient

    from resource_explorer.egeria_actors import ensure_re_identity_in_egeria
    from resource_explorer.web.routes import egeria as routes

    assert routes.whoami()["identity_in_egeria"]["words"] == "not checked in this process"
    _hand_made(egeria)
    ensure_re_identity_in_egeria()
    body = routes.whoami()
    assert body["identity_in_egeria"]["status"] == "present"
    assert body["identity_in_egeria"]["words"] == "present"
    assert body["owner_lookup"] is None


# ── (b) people: look up, never create ────────────────────────────────────

def _person(eg, user="dana", profile_type="Person", linked=True):
    ident = eg.add("UserIdentity", qualifiedName=f"UserIdentity::{user}", userId=user)
    if profile_type:
        prof = eg.add(profile_type, qualifiedName=f"{profile_type}::{user}", displayName=user.title())
        if linked:
            eg.links.append((ident, prof))


def _signed_in(user):
    from resource_explorer.egeria_identity import EgeriaIdentity, use_identity

    return use_identity(EgeriaIdentity(user_id=user, token=f"token-{user.strip()}"))


def _stamp(user, zones=()):
    from resource_explorer.egeria_clients import Daemon, DaemonReason
    from resource_explorer.egeria_identity import stamp_published

    identity = Daemon(DaemonReason.RUN_QUEUE, requested_by=user)
    return stamp_published("elem-1", user, identity=identity, zones=list(zones))


def test_ownership_names_the_persons_profile_in_egerias_own_shape(egeria):
    _person(egeria)
    result = _stamp("dana")
    assert egeria.ownership == [("elem-1", {
        "class": "OwnershipProperties", "owner": "Person::dana",
        "ownerTypeName": "Person", "ownerPropertyName": "qualifiedName"})]
    assert (result["ownership"], result["ownership_form"], result["ownership_note"]) == (True, "profile", "")
    # Looked up read-only, as the daemon; nothing created for the person; no userIds set.
    am = {(b["user"], b["token"]) for b in egeria.built if b["cls"] == "ActorManager"}
    assert am == {(NPA, f"minted-for-{NPA}")}
    assert egeria.writes() == []
    assert "userIds" not in egeria.ownership[0][1]


@pytest.mark.parametrize("profile_type, linked", [(None, False), ("Person", False)])
def test_no_profile_keeps_the_userid_form(egeria, profile_type, linked):
    _person(egeria, profile_type=profile_type, linked=linked)
    result = _stamp("dana")
    assert egeria.ownership[0][1] == {"class": "OwnershipProperties", "owner": "dana",
                                      "ownerTypeName": "UserIdentity", "ownerPropertyName": "userId"}
    assert (result["ownership_form"], result["ownership_note"]) == ("userId", "")
    assert egeria.writes() == []


def test_nobody_in_egeria_keeps_the_userid_form_and_creates_no_identity(egeria):
    result = _stamp("nobody")
    assert egeria.ownership[0][1]["owner"] == "nobody"
    assert result["ownership_form"] == "userId"
    assert egeria.writes() == [] and egeria.elements == {}


def test_a_failed_lookup_falls_back_reports_and_never_blocks_the_write(egeria, caplog):
    from resource_explorer.egeria_actors import actor_lookup_status
    from resource_explorer.web.routes import egeria as routes

    _person(egeria)
    egeria.fail["get_user_identities_by_name"] = ConnectionError("refused")
    with caplog.at_level(logging.WARNING, logger="resource_explorer.egeria_actors"):
        result = _stamp("dana")
    assert result["ownership"] is True
    assert egeria.ownership[0][1]["owner"] == "dana"
    assert result["ownership_form"] == "userId"
    assert result["ownership_note"] == "profile lookup failed: UserIdentity search: ConnectionError: refused"
    assert actor_lookup_status("dana")["user_id"] == "dana"
    with _signed_in("dana"):
        assert routes.whoami()["owner_lookup"]["reason"] == "UserIdentity search: ConnectionError: refused"
    assert any("Ownership uses the userId form" in r.getMessage() for r in caplog.records)
    # Round 2: a failure is remembered briefly (one wait per press, not one per element).
    egeria.fail.clear()
    again = _stamp("dana")
    assert again["ownership_form"] == "userId" and again["ownership_note"] == result["ownership_note"]


def test_an_ambiguous_userid_falls_back_rather_than_picking_one(egeria):
    _person(egeria)
    egeria.add("UserIdentity", qualifiedName="other::dana", userId="dana")
    result = _stamp("dana")
    assert result["ownership_form"] == "userId"
    assert "2 UserIdentities carry userId 'dana'" in result["ownership_note"]


def test_lookups_are_cached_per_process(egeria):
    _person(egeria)
    _stamp("dana")
    _stamp("dana")
    searches = [c for c in egeria.calls if c[1] == "get_user_identities_by_name"]
    assert len(searches) == 1
    assert [o[1]["owner"] for o in egeria.ownership] == ["Person::dana", "Person::dana"]


def test_stamp_on_behalf_of_a_queued_requester_names_their_profile(egeria):
    """The blueprint/port path: Ownership for the requester of a daemon run."""
    from resource_explorer.egeria_clients import Daemon, DaemonReason
    from resource_explorer.egeria_identity import on_behalf_of, stamp_on_behalf

    _person(egeria)
    identity = Daemon(DaemonReason.RUN_QUEUE, requested_by="dana")
    assert stamp_on_behalf("elem-2", on_behalf_of(identity), identity=identity) == ""
    assert egeria.ownership == [("elem-2", {
        "class": "OwnershipProperties", "owner": "Person::dana",
        "ownerTypeName": "Person", "ownerPropertyName": "qualifiedName"})]


def test_the_catalogue_gateway_names_the_requesters_profile_but_not_a_declared_owner(egeria):
    from resource_explorer.catalogue_gateway import PyegeriaCatalogueGateway
    from resource_explorer.egeria_clients import Daemon, DaemonReason

    _person(egeria)
    gw = PyegeriaCatalogueGateway(identity=Daemon(DaemonReason.RUN_QUEUE, requested_by="dana"))
    assert gw.mark_on_behalf("db-1", "dana", "dana") == ""
    outcome, detail = gw.set_owner("db-2", "Data Team")          # a Context-declared owner: as before
    assert (outcome, detail) == ("set", "Ownership set to Data Team")
    assert egeria.ownership == [
        ("db-1", {"class": "OwnershipProperties", "owner": "Person::dana",
                  "ownerTypeName": "Person", "ownerPropertyName": "qualifiedName"}),
        ("db-2", {"class": "OwnershipProperties", "owner": "Data Team",
                  "ownerTypeName": "UserIdentity", "ownerPropertyName": "userId"}),
    ]


# ── round 2 ──────────────────────────────────────────────────────────────

def test_round2_200_stamps_with_a_failing_lookup_make_one_call(egeria):
    egeria.fail["get_user_identities_by_name"] = ConnectionError("refused")
    for i in range(200):
        _stamp("dana")
    searches = [c for c in egeria.calls if c[1] == "get_user_identities_by_name"]
    assert len(searches) == 1
    assert len(egeria.ownership) == 200 and {o[1]["owner"] for o in egeria.ownership} == {"dana"}


def test_round2_a_failure_is_asked_again_after_its_short_ttl(egeria, monkeypatch):
    from resource_explorer import egeria_actors

    egeria.fail["get_user_identities_by_name"] = ConnectionError("refused")
    _stamp("dana")
    _person(egeria)
    egeria.fail.clear()
    for user, (exp, shape) in list(egeria_actors._cache.items()):
        egeria_actors._cache[user] = (0.0, shape)         # the 60s negative entry expired
    assert _stamp("dana")["ownership_form"] == "profile"


def test_round2_one_person_costs_one_lookup_per_press(egeria):
    from resource_explorer import egeria_actors
    from resource_explorer.egeria_clients import client_scope

    _person(egeria)
    with client_scope():
        for _ in range(5):
            _stamp("dana")
            egeria_actors._cache.clear()                  # whatever the process cache says
    assert len([c for c in egeria.calls if c[1] == "get_user_identities_by_name"]) == 1


def test_round2_the_lookup_is_bounded_even_from_a_shared_pool_thread(egeria, monkeypatch):
    import time as _t

    from resource_explorer import egeria_actors
    from resource_explorer.concurrency import run_sync

    _person(egeria)
    egeria.delay = 2.0
    monkeypatch.setattr(egeria_actors, "ACTOR_LOOKUP_TIMEOUT_SECONDS", 0.2)
    t0 = _t.time()
    shape = run_sync(egeria_actors.ownership_for_person, "dana", timeout=30)
    assert _t.time() - t0 < 1.5
    assert shape.form == "userId" and shape.note == "profile lookup failed: no answer within 0.2s"


def test_round2_searches_send_the_type_filter_egeria_reads(egeria):
    from resource_explorer.egeria_actors import ensure_re_identity_in_egeria

    _hand_made(egeria)
    _person(egeria)
    ensure_re_identity_in_egeria()
    _stamp("dana")
    assert egeria.searches
    for sent in egeria.searches:
        assert "metadata_element_type" not in sent["kwargs"]
        # Round 3: a qualifiedName search asks every Referenceable (qualifiedName is unique across
        # them, so a twin of another type must be seen); a userId search asks UserIdentity.
        by_qn = str(sent["body"].get("filter", "")).startswith(("ITProfile::", "UserIdentity::"))
        want = "Referenceable" if by_qn else "UserIdentity"
        assert sent["body"].get("metadataElementTypeName") == want, sent


@pytest.mark.parametrize("answer", [[{"weird": 1}], [{"elementHeader": {"guid": "g1"}}]])
def test_round2_unparseable_elements_are_could_not_check_never_absent(egeria, answer):
    from resource_explorer.egeria_actors import ensure_re_identity_in_egeria

    egeria.override["get_actor_profiles_by_name"] = answer
    state = ensure_re_identity_in_egeria()
    assert state["status"] == "could not check"
    assert "RE cannot parse" in state["reason"], state["reason"]
    assert egeria.writes() == []

    egeria.override = {"get_user_identities_by_name": answer}
    result = _stamp("dana")
    assert result["ownership_form"] == "userId"
    assert "RE cannot parse" in result["ownership_note"]


def test_round2_whoami_failure_clears_on_the_next_successful_lookup(egeria):
    from resource_explorer import egeria_actors
    from resource_explorer.web.routes import egeria as routes

    egeria.fail["get_user_identities_by_name"] = ConnectionError("refused")
    _stamp("dana")
    with _signed_in("dana"):
        assert routes.whoami()["owner_lookup"]["user_id"] == "dana"
    egeria.fail.clear()
    _person(egeria, user="dana")
    egeria_actors._cache.clear()                          # the 60s negative entry expired
    _stamp("dana")                                        # round 3: dana's OWN success clears it
    with _signed_in("dana"):
        assert routes.whoami()["owner_lookup"] is None


# ── round 3 ──────────────────────────────────────────────────────────────

def test_round3_another_users_success_does_not_clear_a_failure(egeria):
    from resource_explorer.egeria_actors import actor_lookup_status
    from resource_explorer.egeria_identity import EgeriaIdentity, use_identity
    from resource_explorer.web.routes import egeria as routes

    egeria.fail["get_user_identities_by_name"] = ConnectionError("refused")
    _stamp("dana")
    egeria.fail.clear()
    _person(egeria, user="erin")
    assert _stamp("erin")["ownership_form"] == "profile"
    with use_identity(EgeriaIdentity(user_id="dana", token="token-dana")):
        assert (routes.whoami()["owner_lookup"] or {}).get("user_id") == "dana", "erin's success hid dana's failure"
    with use_identity(EgeriaIdentity(user_id="erin", token="token-erin")):
        assert routes.whoami()["owner_lookup"] is None, "erin's popover shows erin's state"
    assert actor_lookup_status("dana")["reason"] == "UserIdentity search: ConnectionError: refused"
    assert actor_lookup_status("erin") is None


def test_round3_a_failure_is_not_memoized_for_the_whole_scope(egeria):
    """A long run_queue job holds one scope: after the failure TTL it asks again."""
    from resource_explorer import egeria_actors
    from resource_explorer.egeria_clients import client_scope

    with client_scope():
        egeria.fail["get_user_identities_by_name"] = ConnectionError("refused")
        assert _stamp("dana")["ownership_form"] == "userId"
        egeria.fail.clear()
        _person(egeria)
        for user, (exp, shape) in list(egeria_actors._cache.items()):
            egeria_actors._cache[user] = (0.0, shape)     # the 60s negative entry expired
        assert _stamp("dana")["ownership_form"] == "profile"


def test_round3_each_lookup_closes_the_client_it_built(egeria):
    from resource_explorer import egeria_actors

    _person(egeria)
    _person(egeria, user="erin")
    egeria.fail["get_user_identities_by_name"] = ConnectionError("refused")
    egeria_actors.ownership_for_person("dana")            # a failed lookup closes too
    egeria.fail.clear()
    egeria_actors.ownership_for_person("erin")
    built = [b for b in egeria.built if b["cls"] == "ActorManager"]
    assert len(built) == 2 and egeria.closed == ["ActorManager", "ActorManager"]


def test_round3_concurrent_first_presses_share_one_lookup(egeria, monkeypatch):
    import threading

    from resource_explorer import egeria_actors

    _person(egeria)
    egeria.delay = 0.5
    shapes, gate = [], threading.Barrier(8)

    def press():
        gate.wait()
        shapes.append(egeria_actors.ownership_for_person("dana"))

    threads = [threading.Thread(target=press) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len([c for c in egeria.calls if c[1] == "get_user_identities_by_name"]) == 1
    assert len(shapes) == 8 and {s.form for s in shapes} == {"profile"}
    assert len([b for b in egeria.built if b["cls"] == "ActorManager"]) == 1


def test_round3_waiters_on_a_stuck_lookup_are_still_bounded(egeria, monkeypatch):
    import threading
    import time as _t

    from resource_explorer import egeria_actors

    _person(egeria)
    egeria.delay = 2.0
    monkeypatch.setattr(egeria_actors, "ACTOR_LOOKUP_TIMEOUT_SECONDS", 0.2)
    shapes, gate = [], threading.Barrier(4)

    def press():
        gate.wait()
        shapes.append(egeria_actors.ownership_for_person("dana"))

    t0 = _t.time()
    threads = [threading.Thread(target=press) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert _t.time() - t0 < 1.5
    assert len(shapes) == 4 and {s.note for s in shapes} == {"profile lookup failed: no answer within 0.2s"}
    assert len([c for c in egeria.calls if c[1] == "get_user_identities_by_name"]) == 1


# ── round 4 ──────────────────────────────────────────────────────────────

def test_round4_the_fake_rejects_what_real_pyegeria_rejects(egeria):
    """Guard on the fake itself: a call installed pyegeria rejects must be rejected here too. Up to 6.2.1
    get_actor_profiles_by_name REQUIRED `name`; from 6.2.2 (ISSUE-135) a body-only call is valid, and the
    fake follows the real signature either way."""
    am = pyegeria.ActorManager("view-server", "https://platform", NPA)
    real_name = inspect.signature(_REAL["ActorManager"].get_actor_profiles_by_name).parameters["name"]
    if real_name.default is inspect.Parameter.empty:
        with pytest.raises(TypeError, match="name"):
            am.get_actor_profiles_by_name(body={"filter": "x"}, output_format="JSON")
    else:
        am.get_actor_profiles_by_name(body={"filter": "x"}, output_format="JSON")
    with pytest.raises(TypeError):                       # a positional the real method has no room for
        am.get_actor_profiles_by_name("x", "ActorProfile", None, None, None, 3, 0, 0, "JSON", None, {}, "extra")
    with pytest.raises(TypeError):                       # one positional too many
        am.create_actor_profile({"class": "NewElementRequestBody"}, "extra")


def test_round4_every_by_name_search_passes_the_name_and_the_bootstrap_adopts(egeria):
    """Live probe 2026-10-10: the body-only ITProfile search raised TypeError against real
    pyegeria, so the bootstrap always read 'could not check'. Each search now passes the name
    positionally, the same value as the body's filter."""
    from resource_explorer.egeria_actors import ensure_re_identity_in_egeria

    _hand_made(egeria)
    _person(egeria)
    state = ensure_re_identity_in_egeria()
    assert state["status"] == "present", state.get("reason")
    assert _stamp("dana")["ownership_form"] == "profile"
    methods = {s["method"] for s in egeria.searches}
    assert methods == {"get_actor_profiles_by_name", "get_user_identities_by_name"}
    for sent in egeria.searches:
        assert sent["name"] and sent["name"] == sent["body"].get("filter"), sent


# ── round 5 ──────────────────────────────────────────────────────────────

def test_round5_a_stuck_flight_older_than_the_timeout_is_replaced(egeria, monkeypatch):
    """The pyegeria asyncio hang is real: a leader that never returns must not pin the user."""
    from resource_explorer import egeria_actors

    _person(egeria)
    egeria.delay = 3.0
    monkeypatch.setattr(egeria_actors, "ACTOR_LOOKUP_TIMEOUT_SECONDS", 0.2)
    first = egeria_actors.ownership_for_person("dana")
    assert first.note == "profile lookup failed: no answer within 0.2s"
    egeria.delay = 0.0
    egeria_actors._cache.clear()                          # the 60s negative entry expired
    second = egeria_actors.ownership_for_person("dana")   # the first leader is still asleep
    assert second.form == "profile", second.note


def test_round5_a_thread_that_cannot_start_resolves_its_flight(egeria, monkeypatch):
    import threading as _real
    import types

    from resource_explorer import egeria_actors

    class _NoStart(_real.Thread):
        def start(self):
            raise RuntimeError("can't start new thread")

    _person(egeria)
    monkeypatch.setattr(egeria_actors, "ACTOR_LOOKUP_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(egeria_actors, "threading",
                        types.SimpleNamespace(Thread=_NoStart, Event=_real.Event, Lock=_real.Lock))
    shape = egeria_actors.ownership_for_person("dana")
    assert shape.note == "profile lookup failed: RuntimeError: can't start new thread"
    assert "dana" not in egeria_actors._inflight, "an unresolved flight would pin dana"
    monkeypatch.setattr(egeria_actors, "threading", _real)
    egeria_actors._cache.clear()
    assert egeria_actors.ownership_for_person("dana").form == "profile"


def test_round5_a_full_page_is_possibly_truncated_never_complete(egeria):
    from resource_explorer.egeria_actors import ensure_re_identity_in_egeria

    page = [{"elementHeader": {"guid": f"g{i}", "type": {"typeName": "Person"}},
             "properties": {"qualifiedName": f"Person::p{i}"}} for i in range(100)]
    egeria.override["get_actor_profiles_by_name"] = page
    state = ensure_re_identity_in_egeria()
    assert state["status"] == "could not check" and "possibly truncated" in state["reason"], state
    assert egeria.writes() == []

    page = [{"elementHeader": {"guid": f"u{i}", "type": {"typeName": "UserIdentity"}},
             "properties": {"qualifiedName": f"UserIdentity::u{i}", "userId": f"u{i}"}} for i in range(100)]
    egeria.override = {"get_user_identities_by_name": page}
    result = _stamp("dana")
    assert result["ownership_form"] == "userId" and "possibly truncated" in result["ownership_note"]


def test_round5_whoami_with_no_caller_shows_no_ones_failure_and_strips_the_caller(egeria):
    from resource_explorer.egeria_actors import actor_lookup_status
    from resource_explorer.web.routes import egeria as routes

    egeria.fail["get_user_identities_by_name"] = ConnectionError("refused")
    _stamp("dana")
    assert routes.whoami()["owner_lookup"] is None, "no caller: never another user's failure"
    assert actor_lookup_status() is None and actor_lookup_status("  ") is None
    with _signed_in(" dana "):
        assert (routes.whoami()["owner_lookup"] or {}).get("user_id") == "dana"
