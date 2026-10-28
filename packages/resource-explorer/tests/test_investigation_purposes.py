"""Investigation purposes: Egeria's valid-value list, seeded by RE, read back through a labelled cache.

Owner rulings 2026-10-13 (memory: project_re_purposes_valid_values_2026_10_13):
1. Egeria holds the set of record; RE may cache it, labelled and stale-aware.
2. Find and Integrate are NEW purposes (10 in all).
3. Suggestive everywhere: an unknown purpose warns, naming it, and is never rejected.
5. Free text is kept apart from the picked value, as entered, and is queryable with counts.

End to end through the bootstrap monitor's own pass (`bootstrap._purposes_pass`) with RE's real client
factory and a real temp-SQLite registry. Only the pyegeria clients are faked: `create_autospec(..., spec_set=True)`
of the REAL classes, so every call binds pyegeria's real signature, and the fake refuses what Egeria 6.2 refuses
(typeName `ValidValueSet`, OMAG-COMMON-400-018; a member property ValidValueMemberProperties does not have).
"""
from __future__ import annotations

import json
import logging
from unittest.mock import create_autospec

import pytest
from pyegeria.omvs.metadata_expert import MetadataExpert as _RealMX
from pyegeria.omvs.reference_data import ReferenceDataManager as _RealRef

from resource_explorer import bootstrap
from resource_explorer import investigation_purposes as ip
from resource_explorer.registry import ProjectRegistry

NONE_FOUND = "No elements found"
VVD = "ValidValueDefinition"
TEN = ["Assess", "Certify", "Deploy", "Explore", "Find", "Integrate", "Learn", "Maintain", "Select", "Share"]

#: The properties Egeria 6.2 accepts on the bodies RE sends (ValidValueDefinitionProperties and
#: ValidValueMemberProperties, as read live).
VVD_PROPS = {"class", "qualifiedName", "displayName", "description", "dataType", "scope", "preferredValue",
             "category", "isCaseSensitive", "isDeprecated", "usage", "additionalProperties"}
MEMBER_PROPS = {"class", "isDefaultValue", "ordinal"}


class EgeriaRefused(Exception):
    """What pyegeria raises for an Egeria 400: the message carries Egeria's id."""


class FakeEgeria:
    """What the platform holds, and every write in order."""

    def __init__(self):
        self.elements: dict[str, dict] = {}            # qualifiedName -> {"guid", "type", "props"}
        self.links: dict[tuple[str, str], dict] = {}   # (set guid, member guid) -> member properties
        self.writes: list[tuple] = []
        self.fail_lookups = False
        self.lookup_answer = None                      # a non-miss, non-GUID answer to return instead
        self.lookup_raises = None                      # an exception every look-up raises instead
        self.fail_after_creates = 0                    # refuse every create after the first N

    def guid_of(self, qn):
        return self.elements[qn]["guid"]

    def _check_vvd_body(self, body):
        if body.get("class") != "NewElementRequestBody":
            raise EgeriaRefused(f"OMAG-COMMON-400-001 body class {body.get('class')!r}")
        props = body.get("properties") or {}
        for where in (body, props):
            if where.get("typeName") == "ValidValueSet":
                raise EgeriaRefused("OMAG-COMMON-400-018 The type name ValidValueSet is not known")
        if props.get("class") != "ValidValueDefinitionProperties":
            raise EgeriaRefused(f"OMAG-COMMON-400-001 properties class {props.get('class')!r}")
        unknown = set(props) - VVD_PROPS
        if unknown:
            raise EgeriaRefused(f"OMAG-COMMON-400-001 unknown properties {sorted(unknown)}")
        if not props.get("qualifiedName"):
            raise EgeriaRefused("OMAG-COMMON-400-001 qualifiedName is null")
        return props

    def create(self, body):
        props = self._check_vvd_body(body)
        if isinstance(props.get("additionalProperties"), dict):
            assert all(isinstance(v, str) for v in props["additionalProperties"].values()), "a string map"
        qn = props["qualifiedName"]
        creates = len([w for w in self.writes if w[0] == "create"])
        if self.fail_after_creates and creates >= self.fail_after_creates:
            raise EgeriaRefused("OMAG-REPOSITORY-HANDLER-500-001 the repository is not answering")
        if qn in self.elements:
            raise AssertionError(f"created twice: {qn}")
        guid = f"0000{len(self.elements) + 1:04d}-0000-4000-8000-000000000000"
        self.elements[qn] = {"guid": guid, "type": VVD, "props": props}
        self.writes.append(("create", qn))
        return guid

    def link(self, set_guid, member_guid, body):
        if body is None or body.get("class") != "NewRelationshipRequestBody":
            raise EgeriaRefused("OMAG-COMMON-400-001 relationship body")
        props = body.get("properties") or {}
        if props.get("class") != "ValidValueMemberProperties" or set(props) - MEMBER_PROPS:
            raise EgeriaRefused(f"OMAG-COMMON-400-001 member properties {sorted(props)}")
        if not isinstance(props.get("ordinal", 0), int):
            raise EgeriaRefused("OMAG-COMMON-400-001 ordinal must be an int")
        guids = {e["guid"] for e in self.elements.values()}
        assert set_guid in guids and member_guid in guids, "a link to an element Egeria does not hold"
        self.links[(set_guid, member_guid)] = props
        self.writes.append(("link", set_guid, member_guid))

    def lookup(self, name, property_name, type_name):
        if self.lookup_raises is not None:
            raise self.lookup_raises
        if self.fail_lookups:
            raise RuntimeError("Egeria is not answering")
        if self.lookup_answer is not None:
            return self.lookup_answer
        assert type_name == VVD, "an untyped look-up would miss a plain ValidValueDefinition"
        assert property_name == ["qualifiedName"]
        found = self.elements.get(name)
        return found["guid"] if found else NONE_FOUND

    def find(self, search_string, starts_with=True, metadata_element_type=None, **k):
        """`find_valid_value_definitions`, typed, by qualifiedName prefix: Egeria's element shape."""
        if self.fail_lookups:
            raise RuntimeError("Egeria is not answering")
        assert metadata_element_type == VVD and starts_with
        hits = [{"elementHeader": {"guid": e["guid"]}, "properties": dict(e["props"])}
                for qn, e in self.elements.items() if qn.startswith(search_string)]
        return hits or NONE_FOUND

    def unlink(self, set_qn, member_qn):
        del self.links[(self.guid_of(set_qn), self.guid_of(member_qn))]

    def remove(self, qn):
        g = self.elements.pop(qn)["guid"]
        self.links = {k: v for k, v in self.links.items() if g not in k}

    def related(self, guid, relationship_type, body, *a, **k):
        if self.fail_lookups:
            raise RuntimeError("Egeria is not answering")
        assert relationship_type == "ValidValueMember"
        by_guid = {e["guid"]: e for e in self.elements.values()}
        items = []
        for (s, m), props in self.links.items():
            if s == guid:
                el = by_guid[m]
                items.append({"elementAtEnd1": False,
                              "relationshipProperties": {"propertiesAsStrings": {
                                  k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in props.items()
                                  if k != "class"}},
                              "element": {"elementGUID": m, "elementProperties": {"propertiesAsStrings": {
                                  k: str(v) for k, v in el["props"].items() if k != "class"}}}})
        return {"elementList": items} if items else NONE_FOUND


def _fake_classes(eg: FakeEgeria):
    def ref_factory(*_a, **_k):
        rm = create_autospec(_RealRef, spec_set=True, instance=True)
        rm.create_egeria_bearer_token.return_value = "tok"
        rm.get_guid_for_name.side_effect = lambda name, property_name=None, type_name=None: eg.lookup(
            name, property_name, type_name)
        rm.create_valid_value_definition.side_effect = lambda body: eg.create(body)
        rm.link_valid_value_definition.side_effect = lambda vv_set_guid, vv_member_guid, body=None: eg.link(
            vv_set_guid, vv_member_guid, body)
        rm.find_valid_value_definitions.side_effect = (
            lambda search_string, starts_with=True, output_format="JSON", metadata_element_type=None, **k:
            eg.find(search_string, starts_with, metadata_element_type))
        return rm

    def mx_factory(*_a, **_k):
        mx = create_autospec(_RealMX, spec_set=True, instance=True)
        mx.create_egeria_bearer_token.return_value = "tok"
        mx.get_related_metadata_elements.side_effect = eg.related
        return mx

    return ref_factory, mx_factory


@pytest.fixture
def egeria(monkeypatch):
    eg = FakeEgeria()
    ref_factory, mx_factory = _fake_classes(eg)
    monkeypatch.setattr("resource_explorer.egeria_clients._daemon_credential", lambda: ("re-daemon", "pw"))
    monkeypatch.setenv("EGERIA_PLATFORM_URL", "https://egeria-blocked.invalid:9443")
    monkeypatch.setenv("EGERIA_VIEW_SERVER", "v")
    monkeypatch.setattr("pyegeria.omvs.reference_data.ReferenceDataManager", ref_factory)
    monkeypatch.setattr("pyegeria.omvs.metadata_expert.MetadataExpert", mx_factory)
    monkeypatch.setattr(ip, "_last_key", None)
    monkeypatch.setattr(ip, "_last_outcome", None)
    monkeypatch.setattr(ip, "_still_bootstrap_leader", lambda: True)
    ip.reset_memo()
    yield eg
    ip.reset_memo()


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "purposes.db"))


def _start(registry):
    """What a worker start runs: the monitor's pass, after the data-class seed."""
    return bootstrap._purposes_pass(registry=registry)


def _rows(registry):
    return registry.list_activity(entity_type="egeria", entity_slug="investigation-purposes")


def _live_clients():
    from pyegeria.omvs.metadata_expert import MetadataExpert
    from pyegeria.omvs.reference_data import ReferenceDataManager

    return ReferenceDataManager(), MetadataExpert()


# ── the seed ──────────────────────────────────────────────────────────────────

def test_the_seed_is_the_eight_plus_find_and_integrate_each_described():
    seed = ip.load_seed()
    assert sorted(v["name"] for v in seed["values"]) == TEN
    assert all(v["description"] for v in seed["values"])
    assert seed["default"] == "Explore"


def test_the_registry_tuple_is_no_longer_the_authority():
    assert not hasattr(ProjectRegistry, "VALID_PURPOSES")


# ── the heal: one write path, end to end ──────────────────────────────────────

def test_first_start_creates_the_set_values_links_and_canary(egeria, registry):
    out = _start(registry)
    seed = ip.load_seed()
    creates = [w[1] for w in egeria.writes if w[0] == "create"]
    assert out.status == ip.CREATED
    assert creates == ([ip.SET_QN] + [ip.value_qn(n["name"]) for n in seed["values"]]
                       + [ip.canary_qualified_name(seed)]), "the canary is created last"
    assert out.line == "investigation purposes: created, 10 values (12 elements created, 10 links made)"
    assert len(out.elements_created) == 12, "the canary counts, so the dry run and the write agree"
    set_guid = egeria.guid_of(ip.SET_QN)
    members = {m: p for (s, m), p in egeria.links.items() if s == set_guid}
    assert len(members) == 10
    explore = egeria.guid_of(ip.value_qn("Explore"))
    assert members[explore]["isDefaultValue"] is True
    assert sum(p["isDefaultValue"] for p in members.values()) == 1
    assert sorted(p["ordinal"] for p in members.values()) == list(range(10))
    find = egeria.elements[ip.value_qn("Find")]["props"]
    assert find["preferredValue"] == "Find" and "before any choice" in find["description"]
    # Proof: one activity row naming each element with its GUID.
    rows = _rows(registry)
    assert len(rows) == 1 and rows[0]["summary"] == out.line and rows[0]["status"] == "ok"
    assert {i["qualified_name"] for i in rows[0]["items"] if i.get("guid")} == set(creates)
    # The reader's cache now holds Egeria's list, read back from Egeria.
    cache = json.loads(registry.get_setting(ip.CACHE_KEY))
    assert [v["name"] for v in cache["values"]] == [v["name"] for v in seed["values"]]


def test_the_heal_is_idempotent_a_second_start_creates_nothing(egeria, registry):
    _start(registry)
    ip._last_key = None                                     # a new process
    before = list(egeria.writes)
    out = _start(registry)
    assert egeria.writes == before, "a second start wrote to Egeria"
    assert out.status == ip.PRESENT and out.line == "investigation purposes: present, 10 values"


def test_a_value_removed_in_egeria_is_reported_never_recreated(egeria, registry):
    """Egeria holds the set of record: with the canary present, a value a person removed stays removed."""
    _start(registry)
    egeria.remove(ip.value_qn("Integrate"))
    ip._last_key = None
    n = len(egeria.writes)
    out = _start(registry)
    assert egeria.writes[n:] == [], "the heal recreated or relinked something a person removed"
    assert out.status == ip.PRESENT and out.missing == ["Integrate"]
    assert out.line == ("investigation purposes: present, 9 of 10 values · missing in Egeria, not recreated "
                        "(removed there?): Integrate")
    rows = _rows(registry)
    _start(registry)
    _start(registry)
    assert len(_rows(registry)) == len(rows), "the same state is recorded once"
    assert _rows(registry)[0]["summary"] == out.line


def test_a_seed_change_adds_only_the_new_value_and_a_new_canary(egeria, registry):
    _start(registry)
    egeria.unlink(ip.SET_QN, ip.value_qn("Learn"))      # a person took Learn out of the list
    egeria.remove(ip.value_qn("Share"))                 # and removed Share
    seed = ip.load_seed()
    seed["values"].append({"name": "Plan", "description": "Plan the work."})
    ip._last_key = None
    n = len(egeria.writes)
    out = ip.ensure_purposes(*_live_clients(), seed=seed, still_leader=lambda: True)
    new = egeria.writes[n:]
    assert [w[1] for w in new if w[0] == "create"] == [ip.value_qn("Plan"), ip.canary_qualified_name(seed)]
    assert [w for w in new if w[0] == "link"] == [("link", egeria.guid_of(ip.SET_QN),
                                                  egeria.guid_of(ip.value_qn("Plan")))]
    assert out.status == ip.CREATED
    assert egeria.elements[ip.canary_qualified_name(seed)]["props"]["additionalProperties"]["seedValues"] \
        .split(",")[-1] == "Plan"


def test_a_create_failing_mid_run_leaves_no_canary_and_the_next_start_finishes(egeria, registry):
    egeria.fail_after_creates = 5
    out = _start(registry)
    assert out.status == ip.FAILED and "OMAG-REPOSITORY-HANDLER-500-001" in out.reason
    assert not any(qn.startswith(ip.CANARY_PREFIX) for qn in egeria.elements)
    egeria.fail_after_creates = 0
    ip._last_key = None
    again = _start(registry)                            # the fake fails the test on any duplicate create
    assert again.status == ip.CREATED
    assert len(egeria.elements) == 12 and len(egeria.links) == 10


def test_a_typed_not_found_is_absent(egeria, registry):
    from pyegeria.core._exceptions import PyegeriaNotFoundException

    egeria.lookup_raises = PyegeriaNotFoundException.__new__(PyegeriaNotFoundException)
    rm, mx = _live_clients()
    plan = ip.ensure_purposes(rm, mx, dry_run=True)
    assert plan.status == ip.DRY_RUN and len(plan.would_create) == 22


def test_a_canary_only_write_is_counted_not_nothing_new(egeria, registry):
    _start(registry)
    egeria.remove(ip.canary_qualified_name(ip.load_seed()))
    ip._last_key = None
    out = _start(registry)
    assert out.status == ip.CREATED and out.line.endswith("(1 element created)")


def test_the_manual_run_only_dry_runs(egeria, capsys):
    assert ip.main([]) == 2
    assert "dry-run only" in capsys.readouterr().err
    assert egeria.writes == []
    assert ip.main(["--dry-run"]) == 0
    assert egeria.writes == []


def test_a_failed_look_up_creates_nothing(egeria, registry):
    egeria.fail_lookups = True
    out = _start(registry)
    assert egeria.writes == []
    assert out.status == ip.COULD_NOT_CHECK
    assert "could not be made" in out.reason and "nothing was created" in out.reason
    assert _rows(registry)[0]["status"] == "error"


@pytest.mark.parametrize("answer", ["", "not a guid", [], {}, "No such server"])
def test_only_pyegeria_s_miss_sentence_reads_as_absent(egeria, registry, answer):
    """PR #637's rule: anything that is neither a GUID nor pyegeria's exact miss sentence is could-not-check."""
    egeria.lookup_answer = answer
    out = _start(registry)
    assert egeria.writes == [] and out.status == ip.COULD_NOT_CHECK


def test_a_process_that_lost_the_lock_writes_nothing(egeria, registry, monkeypatch):
    monkeypatch.setattr(ip, "_still_bootstrap_leader", lambda: False)
    out = _start(registry)
    assert egeria.writes == [] and out.status == ip.COULD_NOT_CHECK
    assert "no longer holds the bootstrap lock" in out.reason


def test_the_fake_refuses_a_valid_value_set_type_as_egeria_does(egeria):
    with pytest.raises(EgeriaRefused, match="OMAG-COMMON-400-018"):
        egeria.create({"class": "NewElementRequestBody", "typeName": "ValidValueSet",
                       "properties": {"class": "ValidValueDefinitionProperties", "qualifiedName": "x"}})
    with pytest.raises(EgeriaRefused):
        egeria.link("a", "b", {"class": "NewRelationshipRequestBody",
                               "properties": {"class": "ValidValueMemberProperties", "label": "x"}})


# ── the dry run: the same function, nothing sent ──────────────────────────────

def test_the_dry_run_lists_what_the_write_would_create_and_sends_nothing(egeria):
    from pyegeria.omvs.metadata_expert import MetadataExpert
    from pyegeria.omvs.reference_data import ReferenceDataManager

    rm, mx = ReferenceDataManager(), MetadataExpert()
    plan = ip.ensure_purposes(rm, mx, dry_run=True)
    assert egeria.writes == [] and rm.create_valid_value_definition.call_count == 0
    assert rm.link_valid_value_definition.call_count == 0
    seed = ip.load_seed()
    planned = [c["qualified_name"] for c in plan.would_create if c["kind"] != "link"]
    assert planned == [ip.SET_QN] + [ip.value_qn(v["name"]) for v in seed["values"]] + [
        ip.canary_qualified_name(seed)]
    assert len([c for c in plan.would_create if c["kind"] == "link"]) == 10
    assert plan.line == "investigation purposes: dry run · would create 12 elements and 10 links"
    # The real write then makes exactly the planned elements.
    done = ip.ensure_purposes(rm, mx, still_leader=lambda: True)
    assert [c["qualified_name"] for c in done.created if c.get("guid")] == planned


# ── the reader: Egeria, else the labelled cache, else the seed ────────────────

def test_the_reader_reads_egeria_and_falls_back_to_the_cache_with_labelled_words(egeria, registry):
    from pyegeria.omvs.metadata_expert import MetadataExpert
    from pyegeria.omvs.reference_data import ReferenceDataManager

    rm, mx = ReferenceDataManager(), MetadataExpert()
    ip.ensure_purposes(rm, mx, still_leader=lambda: True)
    live = ip.current_purposes(registry, lambda: ip.read_purposes_from_egeria(rm, mx), use_memo=False)
    assert live.source == "egeria" and live.names[:2] == ["Assess", "Certify"] and live.default == "Explore"

    egeria.fail_lookups = True
    cached = ip.current_purposes(registry, lambda: ip.read_purposes_from_egeria(rm, mx), use_memo=False)
    assert cached.source == "cache" and cached.names == live.names
    assert cached.words.startswith("cached copy · read from Egeria just now")
    assert "Egeria could not be read now" in cached.words and not cached.stale


def test_an_old_cache_is_labelled_stale(registry):
    ip.write_cache(registry, [{"name": "Assess", "description": "", "is_default": False, "ordinal": 0}],
                   read_at="2026-10-01T00:00:00+00:00")
    pl = ip.current_purposes(registry, None, use_memo=False)
    assert pl.source == "cache" and pl.stale and "· stale" in pl.words and "days ago" in pl.words


def test_with_no_cache_the_seed_answers_and_says_so(registry):
    pl = ip.current_purposes(registry, lambda: (_ for _ in ()).throw(RuntimeError("down")), use_memo=False)
    assert pl.source == "seed" and sorted(pl.names) == TEN
    assert pl.words.startswith("RE's shipped list, not Egeria's") and "down" in pl.words


def test_egeria_holding_no_set_is_an_answer_not_a_read_failure(egeria, registry):
    rm, mx = _live_clients()
    ip.write_cache(registry, [{"name": "Assess", "description": "", "is_default": False, "ordinal": 0}])
    pl = ip.current_purposes(registry, lambda: ip.read_purposes_from_egeria(rm, mx), use_memo=False)
    assert pl.source == "egeria-empty" and pl.names == ["Assess"]
    assert pl.words.startswith("Egeria holds no purposes yet; showing the cached copy")
    assert "could not be read" not in pl.words
    registry.set_setting(ip.CACHE_KEY, "")
    pl = ip.current_purposes(registry, lambda: ip.read_purposes_from_egeria(rm, mx), use_memo=False)
    assert pl.source == "egeria-empty" and pl.words.startswith("Egeria holds no purposes yet; showing RE's shipped list")


def test_the_memo_s_words_age_with_the_read(egeria, registry, monkeypatch):
    rm, mx = _live_clients()
    ip.ensure_purposes(rm, mx, still_leader=lambda: True)
    ip.reset_memo()
    clock = [1_000_000.0]
    monkeypatch.setattr(ip.time, "time", lambda: clock[0])
    first = ip.current_purposes(registry, lambda: ip.read_purposes_from_egeria(rm, mx))
    assert first.words == "Egeria's list · read just now"
    clock[0] += 240
    later = ip.current_purposes(registry, lambda: ip.read_purposes_from_egeria(rm, mx))
    assert later.words == "Egeria's list · read 4 minutes ago"
    ip.reset_memo()


def test_a_hung_read_does_not_hold_up_the_next_request(registry):
    import threading
    import time as _time

    release = threading.Event()
    hung = ip.current_purposes(registry, lambda: release.wait(5) and [], timeout=0.2, use_memo=False)
    assert hung.source == "seed" and "did not answer" in hung.reason
    t0 = _time.monotonic()
    nxt = ip.current_purposes(registry, lambda: release.wait(5) and [], timeout=3, use_memo=False)
    assert _time.monotonic() - t0 < 1, "a later request waited behind the hung read"
    assert "has not answered yet" in nxt.reason
    release.set()


def test_known_names_never_open_a_registry_on_their_own(monkeypatch):
    """Constructing ProjectRegistry connects to the shared registry and migrates it; a manifest load must not."""
    attempts = []

    def refuse(*a, **k):
        attempts.append(1)
        raise AssertionError("constructed ProjectRegistry implicitly")

    monkeypatch.setattr(ProjectRegistry, "__init__", refuse)
    assert sorted(ip.known_purpose_names()) == TEN
    from trellis_analytics.discovery import check_purposes
    from trellis_analytics.manifest import StepManifest

    m = StepManifest(name="s", function="a.b", source="ods", version="1", purposes=["Find", "Wander"])
    assert len(check_purposes({"s": m})) == 1
    assert attempts == [], "a registry was constructed implicitly"


def test_the_mining_read_excludes_another_user_s_private_investigation(registry):
    from contextlib import contextmanager

    from resource_explorer.a2a_auth import CallerIdentity, current_caller

    @contextmanager
    def as_user(uid):
        tok = current_caller.set(CallerIdentity(user_id=uid, egeria_token=None, auth_source="app-jwt"))
        try:
            yield
        finally:
            current_caller.reset(tok)

    with as_user("alice"):
        registry.create_investigation("Alice's own", project_classification="PersonalProject",
                                      purpose_text="my private reason")
    with as_user("bob"):
        assert registry.purpose_free_text()["investigation_purposes"] == []
    with as_user("alice"):
        assert registry.purpose_free_text()["investigation_purposes"][0]["text"] == "my private reason"


def test_a_cache_write_that_fails_is_said_on_the_list(egeria, registry, monkeypatch):
    """Not a silent success: Egeria's list is still shown, and the words say RE's copy did not move."""
    rm, mx = _live_clients()
    ip.ensure_purposes(rm, mx, still_leader=lambda: True)

    def broken(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(ip, "write_cache", broken)
    pl = ip.current_purposes(registry, lambda: ip.read_purposes_from_egeria(rm, mx), use_memo=False)
    assert pl.source == "egeria" and pl.cache_error == "disk full"
    assert pl.words == "Egeria's list · read just now · RE's cached copy not updated (disk full)"
    assert pl.as_dict()["cache_error"] == "disk full"


def test_a_cache_refresh_that_fails_after_the_heal_is_on_the_outcome(egeria, registry, monkeypatch):
    def broken(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(ip, "write_cache", broken)
    out = _start(registry)
    assert out.status == ip.CREATED and out.cache_error == "disk full"
    assert out.line.endswith(" · cached copy not refreshed: disk full")
    assert _rows(registry)[0]["summary"] == out.line
    assert ip.status()["cache_error"] == "disk full"


# ── suggestive everywhere: warn, never reject ─────────────────────────────────

def test_an_unknown_purpose_warns_and_is_kept(registry, caplog):
    with caplog.at_level(logging.WARNING):
        inv = registry.create_investigation("Odd one", purposes=["Explore", "Nonsense"])
    assert inv["purposes"] == ["Explore", "Nonsense"]
    assert inv["purpose_warnings"] == [
        "purpose 'Nonsense' is not in the investigation-purposes list; kept as written"]
    assert "Nonsense" in caplog.text
    upd = registry.update_investigation(inv["slug"], purposes=["Find", "Wander"])
    assert upd["purposes"] == ["Find", "Wander"] and "'Wander'" in upd["purpose_warnings"][0]


def test_find_and_integrate_are_purposes_now(registry):
    inv = registry.create_investigation("New ones", purposes=["Find", "Integrate"])
    assert inv["purpose_warnings"] == []


# ── free text: kept apart, as entered, and queryable ──────────────────────────

def test_free_text_is_stored_apart_and_queryable_with_counts(registry):
    a = registry.create_investigation("A", purposes=["Select"], purpose_text="  feed the CRM nightly ")
    registry.create_investigation("B", purpose_text="Feed the  CRM nightly")
    registry.create_investigation("C", purposes=["Wander"])
    assert a["purposes"] == ["Select"] and a["purpose_text"] == "feed the CRM nightly"
    # A legacy row (a bare JSON array) still reads.
    with registry._conn() as conn:
        conn.execute("UPDATE investigations SET purposes_json = ? WHERE slug = ?", ('["Assess"]', a["slug"]))
    assert registry.get_investigation(a["slug"])["purposes"] == ["Assess"]

    registry.save_context("database", "crm", {"enrichment": {"intended_use": {
        "value": "Find — reporting backfill", "picked": "Find", "free_text": "reporting backfill"}}})
    registry.save_context("database", "erp", {"enrichment": {"intended_use": {
        "value": "Reporting backfill", "picked": "", "free_text": "Reporting backfill"}}})
    registry.save_context("repo", "old", {"enrichment": {"intended_use": {"value": "Learn"}}})   # legacy, listed
    registry.save_context("repo", "older", {"purpose": "a demo for sales"})                     # Classic only

    mined = registry.purpose_free_text()
    inv = {(r["text"].casefold(), r["kind"]): r["count"] for r in mined["investigation_purposes"]}
    assert inv[("feed the crm nightly", "free text")] == 1        # A's text was overwritten by the legacy row
    assert inv[("wander", "not in the list")] == 1
    use = {(r["text"].casefold(), r["kind"]): r["count"] for r in mined["intended_use"]}
    assert use == {("reporting backfill", "free text"): 2, ("a demo for sales", "free text"): 1}
    assert sorted(mined["intended_use"][0]["where"]) == ["database/crm", "database/erp"]


# ── the purposes route ────────────────────────────────────────────────────────

def test_the_route_serves_the_list_with_where_it_came_from(registry, monkeypatch):
    from fastapi.testclient import TestClient

    from resource_explorer.web.app import app
    from resource_explorer.web.routes import investigations as routes

    monkeypatch.setattr(routes, "_registry", lambda: registry)
    ip.reset_memo()
    body = TestClient(app).get("/api/investigations/purposes").json()
    assert sorted(body["purposes"]) == TEN and body["source"] == "seed"   # no caller, no cache, in a test
    assert all(v["description"] for v in body["values"])
    assert body["words"].startswith("RE's shipped list")
    ip.reset_memo()


def test_the_free_text_route_returns_the_mining_read(registry, monkeypatch):
    from fastapi.testclient import TestClient

    from resource_explorer.web.app import app
    from resource_explorer.web.routes import investigations as routes

    monkeypatch.setattr(routes, "_registry", lambda: registry)
    registry.create_investigation("X", purpose_text="vendor due diligence")
    body = TestClient(app).get("/api/investigations/purposes/free-text").json()
    assert body["investigation_purposes"][0]["text"] == "vendor due diligence"


# ── Enrichment's Intended use: pick, or type, or both ─────────────────────────

@pytest.fixture
def context_client(registry, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    monkeypatch.setattr("resource_explorer.web.routes.context.get_current_user",
                        lambda request: {"user_id": "peterprofile"})
    from resource_explorer.web.app import app
    return TestClient(app)


def test_intended_use_keeps_the_picked_value_and_the_text_apart(context_client, registry):
    r = context_client.patch("/api/context/database/crm/field", json={
        "key": "intended_use", "kind": "judgement", "value": "ignored", "picked": "Integrate",
        "free_text": "  nightly CRM feed  "})
    assert r.status_code == 200, r.text
    f = r.json()["field"]
    assert (f["picked"], f["free_text"], f["value"]) == ("Integrate", "nightly CRM feed",
                                                        "Integrate — nightly CRM feed")
    assert r.json()["warnings"] == []
    ctx = registry.get_context("database", "crm")
    assert ctx["purpose"] == "Integrate — nightly CRM feed"            # Classic's flat key still mirrors
    assert registry.purpose_free_text()["intended_use"][0]["text"] == "nightly CRM feed"


def test_intended_use_from_an_older_client_is_split_by_the_list(context_client):
    listed = context_client.patch("/api/context/database/a/field", json={
        "key": "intended_use", "kind": "judgement", "value": "Find"}).json()["field"]
    free = context_client.patch("/api/context/database/b/field", json={
        "key": "intended_use", "kind": "judgement", "value": "a reporting backfill"}).json()["field"]
    assert (listed["picked"], listed["free_text"]) == ("Find", "")
    assert (free["picked"], free["free_text"]) == ("", "a reporting backfill")


def test_an_unknown_picked_intended_use_warns_and_is_kept(context_client):
    r = context_client.patch("/api/context/database/c/field", json={
        "key": "intended_use", "kind": "judgement", "picked": "Wander", "free_text": ""})
    assert r.status_code == 200
    assert r.json()["field"]["picked"] == "Wander"
    assert r.json()["warnings"] == [
        "intended use: purpose 'Wander' is not in the investigation-purposes list; kept as written"]
