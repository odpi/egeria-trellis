"""Caller identity, read side (owner's decision 2026-10-14, option 2 of DESIGN-MCP-SERVER-AND-A2A-GAPS.md).

Everything synchronous that serves a signed-in request runs as the caller; queued writes stay on the service
account with the requester recorded. Inventory: docs/design-notes/evidence/IDENTITY-INVENTORY-2026-10-14.md.

Only the pyegeria boundary is faked: `egeria_clients._build` returns `create_autospec(cls, instance=True,
spec_set=True)` of the REAL pyegeria class RE asked for, so a call RE makes that pyegeria does not have fails
here too. Each assertion is on what reached that boundary: which bearer token was set, and whether a token was
minted from the service account's password.
"""
from __future__ import annotations

import asyncio
import json
import threading
from unittest.mock import create_autospec

import pytest

DAEMON_USER, DAEMON_PW = "re-daemon", "pw-daemon-not-a-real-one"
ERIN, ERIN_TOKEN = "erin", "tok-erin-signed-in"
PETER, PETER_TOKEN = "peter", "tok-peter-signed-in"


class Boundary:
    """Every pyegeria client the factory built: its class, the Egeria user it was built for, and the autospec."""

    def __init__(self):
        self.built: list[dict] = []
        self.configure: dict[str, callable] = {}
        self.lock = threading.Lock()

    def build(self, cls, view_server, platform_url, user_id, password):
        mock = create_autospec(cls, instance=True, spec_set=True)
        mock.create_egeria_bearer_token.return_value = f"minted-for-{user_id}"
        hook = self.configure.get(cls.__name__)
        if hook is not None:
            hook(mock)
        with self.lock:
            self.built.append({"cls": cls.__name__, "user": user_id, "mock": mock,
                               "thread": threading.current_thread().name})
        return mock

    def of(self, name):
        return [b for b in self.built if b["cls"] == name]


@pytest.fixture
def boundary(monkeypatch):
    from resource_explorer import config, egeria_clients

    cfg = config.get_config().model_copy(deep=True)
    cfg.egeria.user_id, cfg.egeria.user_password = DAEMON_USER, DAEMON_PW
    cfg.egeria.platform_url = "https://localhost:9443"
    monkeypatch.setattr(config, "get_config", lambda: cfg)
    b = Boundary()
    monkeypatch.setattr(egeria_clients, "_build", b.build)
    return b


def _signed_in(user, token):
    """Publish `user` as the web login's `_identity_middleware` does (same ContextVar, same CallerIdentity)."""
    from resource_explorer.a2a_auth import CallerIdentity, current_caller

    return current_caller.set(CallerIdentity(user_id=user, egeria_token=token, auth_source="app-jwt"))


def _sent_only_the_callers_token(entry, token):
    mock = entry["mock"]
    mock.set_bearer_token.assert_called_once_with(token)
    mock.create_egeria_bearer_token.assert_not_called()      # never the service account's password exchange
    assert entry["user"] != DAEMON_USER


# ── the web login publishes the same CallerIdentity the factory reads ─────────────────────────────────

def test_the_web_login_session_reaches_the_factory_as_the_caller(boundary):
    """A session JWT minted by RE's login, read back by `_identity_middleware`'s own function, is what
    `Caller()` acts as: the Egeria token inside the JWT is the one set on the client."""
    from pyegeria.omvs.metadata_expert import MetadataExpert

    from resource_explorer.a2a_auth import current_caller
    from resource_explorer.auth import create_access_token, decode_token, identity_from_claims
    from resource_explorer.egeria_clients import Caller, client_scope, egeria_client

    jwt = create_access_token(user_id=PETER, egeria_token=PETER_TOKEN)
    reset = current_caller.set(identity_from_claims(decode_token(jwt)))
    try:
        with client_scope():
            egeria_client(Caller(), purpose="test read").of(MetadataExpert)
    finally:
        current_caller.reset(reset)
    (entry,) = boundary.of("MetadataExpert")
    assert entry["user"] == PETER
    _sent_only_the_callers_token(entry, PETER_TOKEN)


# ── converted: the Technology Type catalog (a module-level instance serving every request) ────────────

def _fresh_route_catalog():
    import resource_explorer.web.routes.survey_definitions as route

    route._tech_type_catalog = None
    return route._get_tech_type_catalog()


def test_the_route_catalog_reads_as_each_caller_and_never_serves_one_callers_answer_to_another(boundary):
    """`web/routes/survey_definitions.py` holds ONE catalog for the process. Erin reads first; Peter's read must
    reach Egeria with Peter's token and return Peter's answer, not Erin's cached one."""
    answers = {ERIN_TOKEN: {"displayName": "seen by erin"}, PETER_TOKEN: {"displayName": "seen by peter"}}

    def per_token(mock):
        def detail(name, output_format="JSON", **kw):
            token = mock.set_bearer_token.call_args.args[0]
            return answers[token]
        mock.get_tech_type_detail.side_effect = detail

    boundary.configure["AutomatedCuration"] = per_token
    from resource_explorer.egeria_clients import client_scope

    catalog = _fresh_route_catalog()
    try:
        reset = _signed_in(ERIN, ERIN_TOKEN)
        try:
            with client_scope():
                assert catalog.get_tech_type_detail("PostgreSQL Relational Database") == answers[ERIN_TOKEN]
        finally:
            from resource_explorer.a2a_auth import current_caller
            current_caller.reset(reset)

        reset = _signed_in(PETER, PETER_TOKEN)
        try:
            with client_scope():
                got = catalog.get_tech_type_detail("PostgreSQL Relational Database")
        finally:
            current_caller.reset(reset)
    finally:
        _fresh_route_catalog()

    assert got == answers[PETER_TOKEN], "Peter was served Erin's cached answer"
    peters = [b for b in boundary.of("AutomatedCuration") if b["user"] == PETER]
    assert len(peters) == 1
    _sent_only_the_callers_token(peters[0], PETER_TOKEN)
    peters[0]["mock"].get_tech_type_detail.assert_called_once()


def test_the_route_catalog_still_caches_within_one_caller(boundary):
    """Keyed per reader, not dropped: the same person asking twice is one Egeria read."""
    boundary.configure["AutomatedCuration"] = lambda m: setattr(m.get_tech_type_detail, "return_value", {"x": 1})
    from resource_explorer.a2a_auth import current_caller
    from resource_explorer.egeria_clients import client_scope

    catalog = _fresh_route_catalog()
    reset = _signed_in(ERIN, ERIN_TOKEN)
    try:
        for _ in range(2):
            with client_scope():
                catalog.get_tech_type_detail("PostgreSQL Relational Database")
    finally:
        current_caller.reset(reset)
        _fresh_route_catalog()
    calls = sum(b["mock"].get_tech_type_detail.call_count for b in boundary.of("AutomatedCuration"))
    assert calls == 1


# ── converted: the A2A / chat agents' hop onto the shared pool keeps the caller ─────────────────────

class _ReadsEgeriaAgent:
    """A stand-in for the BeeAI agent: its run reads Egeria as `Caller()`, from wherever it runs."""

    def __init__(self, seen: dict):
        self.seen = seen

    async def run(self, prompt):
        from pyegeria.omvs.metadata_expert import MetadataExpert

        from resource_explorer.a2a_auth import caller
        from resource_explorer.egeria_clients import Caller, egeria_client

        who = caller()
        self.seen["caller"] = who.user_id if who else None
        self.seen["thread"] = threading.current_thread().name
        egeria_client(Caller(), purpose="agent read", shared=False).of(MetadataExpert)
        return "answered"


def _run_in_event_loop(fn):
    """Call `fn` the way an A2A handler does: synchronously, from inside a running event loop."""
    async def handler():
        return fn()
    return asyncio.run(handler())


def test_an_a2a_agent_run_hops_to_the_pool_and_still_reads_egeria_as_the_caller(boundary, monkeypatch):
    from resource_explorer.a2a_auth import current_caller
    from resource_explorer.agents.stats_agent import StatsAgent

    seen: dict = {}
    agent = StatsAgent()
    monkeypatch.setattr(agent, "_build_agent", lambda: _ReadsEgeriaAgent(seen))
    reset = _signed_in(PETER, PETER_TOKEN)
    try:
        assert _run_in_event_loop(lambda: agent._run_agent("q")) == "answered"
    finally:
        current_caller.reset(reset)
    assert seen["thread"].startswith("re-sync"), "the run must have hopped onto the shared pool"
    assert seen["caller"] == PETER
    (entry,) = boundary.of("MetadataExpert")
    assert entry["thread"].startswith("re-sync")
    _sent_only_the_callers_token(entry, PETER_TOKEN)


def test_a_chat_agent_turn_hops_to_the_pool_and_still_reads_egeria_as_the_caller(boundary, monkeypatch):
    from resource_explorer.a2a_auth import current_caller
    from resource_explorer.agents.conversation_agent import ConversationAgent

    seen: dict = {}
    agent = ConversationAgent()
    monkeypatch.setattr(agent, "_get_agent", lambda: _ReadsEgeriaAgent(seen))
    reset = _signed_in(ERIN, ERIN_TOKEN)
    try:
        assert _run_in_event_loop(lambda: agent._run_persistent("q")) == "answered"
    finally:
        current_caller.reset(reset)
    assert seen["thread"].startswith("re-sync") and seen["caller"] == ERIN
    (entry,) = boundary.of("MetadataExpert")
    _sent_only_the_callers_token(entry, ERIN_TOKEN)


def test_the_hop_does_not_leak_the_caller_into_the_pool_thread_afterwards(boundary, monkeypatch):
    """The copied context is per call: the pool thread that served Peter holds no caller for the next job."""
    from resource_explorer.a2a_auth import caller, current_caller
    from resource_explorer.agents.stats_agent import StatsAgent
    from resource_explorer.concurrency import run_sync

    seen: dict = {}
    agent = StatsAgent()
    monkeypatch.setattr(agent, "_build_agent", lambda: _ReadsEgeriaAgent(seen))
    reset = _signed_in(PETER, PETER_TOKEN)
    try:
        _run_in_event_loop(lambda: agent._run_agent("q"))
    finally:
        current_caller.reset(reset)
    leftovers = {run_sync(lambda: caller()) for _ in range(16)}
    assert leftovers == {None}


# ── unchanged: a queued write carries the requester and drains on the service account ──────────────

@pytest.fixture
def reg(tmp_path):
    from resource_explorer.registry import ProjectRegistry, Project

    r = ProjectRegistry(db_path=str(tmp_path / "reads.db"))
    r.add(Project(slug="p", display_name="P", github_url="https://github.com/x/p"))
    return r


def test_a_queued_write_keeps_its_requester_and_drains_on_the_service_account(boundary, reg, monkeypatch):
    """Option 2's other half, untouched by this change: Peter's queued collection membership is applied by the
    background drain (no one signed in) as `Daemon(OUTBOX, requested_by=peter)`: the client is the daemon's,
    its token minted from the daemon's credential, never Peter's; the row still names Peter."""
    from tests.zone_fakes import open_access

    open_access(monkeypatch, caller_if_none=PETER)
    from resource_explorer import egeria_clients, egeria_outbox

    acting: list = []

    def add(mock):
        def record(*a, **k):
            acting.append(egeria_clients._acting.get())
        mock.add_to_collection.side_effect = record

    boundary.configure["CollectionManager"] = add
    boundary.configure["AutomatedCuration"] = lambda m: setattr(
        m.get_guid_for_name, "return_value", "No elements found")
    rid = reg.enqueue_outbox_element("repo", "p", "collection_membership", "CollectionMembership::c::m",
                                     {"collection_guid": "c", "member_guid": "m", "requested_by": PETER})
    from resource_explorer.a2a_auth import caller

    assert caller() is None, "the background drain has no signed-in person"
    summary = egeria_outbox.drain_outbox(reg, identity=egeria_clients.Daemon(egeria_clients.DaemonReason.OUTBOX))
    assert summary["done"] == 1, summary

    assert len(acting) == 1
    who = acting[0]
    assert who.kind == "daemon" and who.reason == "outbox_drain"
    assert who.requested_by == PETER and who.client_user == DAEMON_USER
    managers = boundary.of("CollectionManager")
    assert managers and {m["user"] for m in managers} == {DAEMON_USER}
    for m in boundary.built:
        tokens = {c.args[0] for c in m["mock"].set_bearer_token.call_args_list}
        assert PETER_TOKEN not in tokens
    assert any(m["mock"].create_egeria_bearer_token.called for m in boundary.built), "the daemon minted"
    row = reg.get_outbox_element(rid)
    assert row["status"] == "done"
    assert json.loads(row["payload_json"])["requested_by"] == PETER


# ── round 2: the shared pool carries the caller (HIGH, pre-existing leak) ───────────────────────────

def test_a_run_sync_or_submit_callee_sees_the_caller():
    from resource_explorer import concurrency
    from resource_explorer.a2a_auth import caller, current_caller

    reset = _signed_in(PETER, PETER_TOKEN)
    try:
        via_run_sync = concurrency.run_sync(lambda: caller().user_id, timeout=10)
        via_submit = concurrency.submit(lambda: caller().user_id).result(timeout=10)
        via_raw_pool = concurrency.get_pool().submit(lambda: caller().user_id).result(timeout=10)
        via_all = [v for ok, v in concurrency.run_sync_all([lambda: caller().user_id], timeout=10)]
    finally:
        current_caller.reset(reset)
    assert via_run_sync == via_submit == via_raw_pool == PETER and via_all == [PETER]
    # and nothing stays behind in a worker for the next, caller-less job
    assert {concurrency.run_sync(lambda: caller(), timeout=10) for _ in range(16)} == {None}


def test_a_pool_job_builds_its_own_egeria_clients_not_the_requests():
    """The request's client cache is NOT carried into the pool (pyegeria ISSUE-96: a client is bound to the
    loop of the thread that first drives it)."""
    from resource_explorer import concurrency, egeria_clients

    reset = _signed_in(PETER, PETER_TOKEN)
    try:
        with egeria_clients.client_scope():
            assert egeria_clients.current_scope() is not None
            in_pool = concurrency.run_sync(egeria_clients.current_scope, timeout=10)
    finally:
        from resource_explorer.a2a_auth import current_caller
        current_caller.reset(reset)
    assert in_pool is None


@pytest.fixture
def facts_world(tmp_path, monkeypatch):
    """Peter's PRIVATE investigation holds database x; the route reads through FactLayer on the shared pool."""
    from contextlib import contextmanager

    from resource_explorer.registry import DatabaseEntity, ProjectRegistry

    r = ProjectRegistry(db_path=str(tmp_path / "facts.db"))
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None, **k: setattr(self, "__dict__", r.__dict__) or None)
    r.register_database(DatabaseEntity(slug="x", display_name="X", db_type="postgresql", host="h",
                                       port=5432, database_name="x"))

    @contextmanager
    def as_user(user):
        from resource_explorer.a2a_auth import current_caller
        reset = _signed_in(user, "t")
        try:
            yield
        finally:
            current_caller.reset(reset)

    with as_user(PETER):
        inv = r.create_investigation("Peter Secret Plan", project_classification="PersonalProject")
        ws = r.get_or_create_working_set(inv["slug"])
        r.add_working_set_member(ws["slug"], "database", "x")
    return r


EXISTING_USE = "Is there any existing use within our organization?"


def _existing_use_on_the_pool(user):
    """A pool job (submitted the way `GET /api/analyses/facts` fans out, `concurrency.get_pool().submit`) runs the
    real database state reader that names investigations (`_db_r_existing_use` -> `find_entity_investigations` ->
    `_may_see_investigation`, which lets '' see everything). On the live routes that reader is reached through
    `/facts/{slug}/answer`, a sync route Starlette already runs in a copied context (pinned by the route test
    below); this test pins the POOL path, which leaked for any such read before the pool carried the caller."""
    from resource_explorer import concurrency
    from resource_explorer.a2a_auth import current_caller
    from resource_explorer.facts import FactLayer

    reset = _signed_in(user, f"tok-{user}")
    try:
        env = concurrency.get_pool().submit(
            lambda: FactLayer(resource_type="database").answer("x", {"question": EXISTING_USE}).as_dict()
        ).result(timeout=30)
    finally:
        current_caller.reset(reset)
    return json.dumps(env)


def test_erin_never_sees_peters_private_investigation_from_a_pool_job(facts_world):
    assert "Peter Secret Plan" in _existing_use_on_the_pool(PETER), "the fact must be computed for its owner"
    assert "Peter Secret Plan" not in _existing_use_on_the_pool(ERIN)


def test_erin_never_sees_peters_private_investigation_through_the_answer_route(facts_world):
    from fastapi.testclient import TestClient

    from resource_explorer.auth import create_access_token
    from resource_explorer.web.app import app

    def ask(user):
        h = {"Authorization": "Bearer " + create_access_token(user_id=user, egeria_token=f"tok-{user}")}
        r = TestClient(app).get("/api/analyses/facts/x/answer",
                                params={"entity_type": "database", "question": EXISTING_USE}, headers=h)
        assert r.status_code == 200, r.text
        return r.text

    assert "Peter Secret Plan" in ask(PETER)
    assert "Peter Secret Plan" not in ask(ERIN)


# ── round 2: survey-definition caches are per reader ────────────────────────────────────────────────

def test_a_zoned_definition_seen_by_one_reader_is_never_served_from_cache_to_another(boundary):
    """The full scan lists every GovernanceActionProcess, a zoned user-authored one included when the reader may
    see it. Peter's scan finds his zoned definition; Erin's scan must reach Egeria as Erin, not hit his cache."""
    from resource_explorer.a2a_auth import current_caller
    from resource_explorer.egeria_clients import client_scope
    from resource_explorer.surveyors import survey_definition_reader as sdr

    def element(qn):
        return {"properties": {"qualifiedName": qn, "displayName": qn,
                               "additionalProperties": {"supported_technology_type": "PostgreSQL Server"}},
                "elementHeader": {"guid": "g-" + qn}}

    def per_token(mock):
        def find(**kw):
            token = mock.set_bearer_token.call_args.args[0]
            return [element("Shared")] + ([element("PeterPrivate")] if token == PETER_TOKEN else [])
        mock.find_governance_definitions.side_effect = find

    boundary.configure["GovernanceOfficer"] = per_token
    sdr.clear_caches()
    try:
        seen = {}
        for user, token in ((PETER, PETER_TOKEN), (ERIN, ERIN_TOKEN)):
            reset = _signed_in(user, token)
            try:
                with client_scope():
                    seen[user] = {c["qualified_name"] for c in
                                  sdr.SurveyDefinitionReader().find_candidate_process_guids("PostgreSQL Server")}
            finally:
                current_caller.reset(reset)
    finally:
        sdr.clear_caches()
    assert seen[PETER] == {"Shared", "PeterPrivate"}
    assert seen[ERIN] == {"Shared"}, "Erin was served Peter's cached scan"


# ── round 2: a refusal never names another person's private zone ───────────────────────────────────

def test_a_refusal_on_a_private_element_names_no_one(monkeypatch, caplog):
    """A private element is zoned [private zone, <owner's userId>]. When the owner's zone is secured, the
    refusal names the private zone at most, never the owner; the server log keeps the detail."""
    from resource_explorer.egeria_identity import private_zone
    from resource_explorer.workflows import curate as wc
    from resource_explorer.zone_access import zone_grants

    pz = private_zone()
    zones = [pz, PETER]
    control = {"associatedSecurityList": {"DEFAULT": ["instanceOwner"]}}
    account = {"userAccountStatus": "AVAILABLE", "userAccountType": "EMPLOYEE"}

    def verdict(user, guid, ops, reader):
        return zone_grants(user, zones, control_for=lambda z: control, account_for=lambda: account,
                           owners=[PETER], maintainers=[PETER], operations=ops), zones

    monkeypatch.setattr(wc, "_existing_element_verdict", verdict)
    reset = _signed_in(ERIN, ERIN_TOKEN)
    try:
        with caplog.at_level("INFO", logger="resource_explorer.workflows.curate"):
            d = wc.curation_access(None, "database", "x", "", element_guid="g-1", reader=object())
    finally:
        from resource_explorer.a2a_auth import current_caller
        current_caller.reset(reset)
    assert not d.allowed
    assert PETER not in d.reason, d.reason
    assert "private to its owner" in d.reason
    assert PETER in caplog.text, "the full detail stays in the server log"


# ── round 2: the per-reader catalog cache expires, is bounded, and refuses with nobody signed in ─────

def test_the_catalog_cache_expires_and_is_bounded(boundary, monkeypatch):
    from resource_explorer.surveyors import egeria_tech_type_catalog as cat

    c = cat._TTLCache(ttl=300, max_entries=3)
    clock = [1000.0]
    monkeypatch.setattr(cat.time, "monotonic", lambda: clock[0])
    for i in range(5):
        c[i] = i
    assert len(c) == 3 and 0 not in c and 4 in c
    clock[0] += 301
    assert 4 not in c


def test_the_catalog_with_nobody_signed_in_refuses_and_builds_nothing(boundary):
    from resource_explorer.egeria_clients import NoCallerIdentity

    catalog = _fresh_route_catalog()
    with pytest.raises(NoCallerIdentity):
        catalog.list_technology_types()
    assert boundary.built == [], "never the service account in place of a missing caller"


def test_list_technology_types_reads_as_each_caller(boundary):
    from resource_explorer.a2a_auth import current_caller
    from resource_explorer.egeria_clients import client_scope

    def per_token(mock):
        mock.find_technology_types.side_effect = lambda **kw: [
            {"displayName": mock.set_bearer_token.call_args.args[0]}]

    boundary.configure["AutomatedCuration"] = per_token
    catalog = _fresh_route_catalog()
    got = {}
    try:
        for user, token in ((ERIN, ERIN_TOKEN), (PETER, PETER_TOKEN)):
            reset = _signed_in(user, token)
            try:
                with client_scope():
                    got[user] = catalog.list_technology_types()
            finally:
                current_caller.reset(reset)
    finally:
        _fresh_route_catalog()
    assert got == {ERIN: [{"displayName": ERIN_TOKEN}], PETER: [{"displayName": PETER_TOKEN}]}


# ── round 2: the doc-source immediate attempt is the person's own write, made with their token ──────

def test_the_doc_source_immediate_attempt_runs_as_the_caller(boundary, monkeypatch):
    """Option 2 ruling for this branch: the immediate attempt right after the press is the person's own act
    (an in-request write, PR 2's scope, already on the caller); a retry from the background drain is a queued
    write on the service account with `by` recorded (pinned above)."""
    from pyegeria.omvs.metadata_expert import MetadataExpert

    from resource_explorer.a2a_auth import current_caller
    from resource_explorer.egeria_clients import current_principal, egeria_client
    from resource_explorer.web.routes import doc_sources

    class SyncThread:
        def __init__(self, target, name="", daemon=True):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(doc_sources.threading, "Thread", SyncThread)
    monkeypatch.setattr(doc_sources, "_registry", lambda: None)
    monkeypatch.setattr(doc_sources, "drain_outbox_row", lambda reg, rid: egeria_client(
        current_principal(), purpose="doc sources").of(MetadataExpert))
    reset = _signed_in(PETER, PETER_TOKEN)
    try:
        doc_sources._attempt_outbox_row_immediately(7)
    finally:
        current_caller.reset(reset)
    (entry,) = boundary.of("MetadataExpert")
    _sent_only_the_callers_token(entry, PETER_TOKEN)


# ── round 3 ─────────────────────────────────────────────────────────────────────────────────────────

def test_a_prefect_in_process_step_from_a_request_runs_without_the_requests_client_scope(monkeypatch):
    """The pooled flow sees the caller, but not the request's Egeria client scope (pyegeria ISSUE-96)."""
    from resource_explorer import egeria_clients
    from resource_explorer.a2a_auth import caller, current_caller
    from resource_explorer.surveyors import prefect_adapter as pa

    seen = {}

    def flow(**kw):
        seen["scope"] = egeria_clients.current_scope()
        seen["caller"] = caller().user_id if caller() else None
        return {"result": {}, "flow_run_id": "f"}

    monkeypatch.setattr(pa, "check_prefect_reachable_sync", lambda: (True, ""))
    monkeypatch.setattr(pa, "_in_process_step_flow", lambda: flow)
    monkeypatch.setattr(pa, "assert_no_credentials", lambda *a, **k: None)
    fn = pa._run_step_in_process_flow

    async def handler():
        with egeria_clients.client_scope():
            return fn("database", "x", "step", {}, "")

    reset = _signed_in(PETER, PETER_TOKEN)
    try:
        asyncio.run(handler())
    finally:
        current_caller.reset(reset)
    assert seen == {"scope": None, "caller": PETER}


def test_a_question_guid_miss_is_kept_briefly_not_for_an_hour(monkeypatch):
    from resource_explorer.surveyors import survey_definition_reader as sdr

    sdr.clear_caches()
    clock = [1000.0]
    monkeypatch.setattr(sdr.time, "monotonic", lambda: clock[0])
    answers = iter([None, "g-q"])
    reader = sdr.SurveyDefinitionReader(identity=object())
    monkeypatch.setattr(reader, "_lookup_question_guid", lambda name: next(answers))
    try:
        assert reader.resolve_question_guid("Q") is None          # a reader who cannot see the term
        clock[0] += sdr._QUESTION_GUID_MISS_TTL_SECONDS + 1
        assert reader.resolve_question_guid("Q") == "g-q"           # asked again soon, not after an hour
        clock[0] += 600
        assert reader.resolve_question_guid("Q") == "g-q"           # a hit is still kept
    finally:
        sdr.clear_caches()
