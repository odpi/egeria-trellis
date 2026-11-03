"""RE as an engine host, step 1 (E3): the polling claim sweep.

Design: docs/design-notes/DESIGN-RE-ENGINE-HOST-BY-EQUIVALENCE.md §3 and §5.2. These are tests 1, 2, 8 and 10,
plus the claim part of test 4. The sweep talks to Egeria only through pyegeria; the clients here are
`create_autospec(<real class>, spec_set=True)`, installed at the one constructor seam
(`egeria_clients._build`), so every call binds the real signature and nothing between the sweep and the
client is faked. The registry is a real temp SQLite registry.
"""
from __future__ import annotations

import logging
from unittest.mock import create_autospec

import pytest

from resource_explorer.registry import DatabaseEntity, ProjectRegistry

ENGINE_GUID = "0a1b2c3d-0000-4000-8000-00000000e001"
OTHER_ENGINE_GUID = "0a1b2c3d-0000-4000-8000-00000000e002"   # PostgreSQLSurvey's, say
DB_GUID = "0a1b2c3d-0000-4000-8000-0000000000d1"
PRIVATE_DB_GUID = "0a1b2c3d-0000-4000-8000-0000000000d2"
REQUESTER = "peterprofile"


def _action(guid: str, *, engine: str = ENGINE_GUID, status: str = "APPROVED",
            request_type: str = "re-db-derived", claimed_by: str | None = None,
            requester: str = REQUESTER, target: str = DB_GUID) -> dict:
    """One element of `GET /engine-actions/active`, shaped as Egeria's `EngineActionElement` serialises."""
    return {
        "class": "EngineActionElement",
        "elementHeader": {"guid": guid},
        "governanceEngineGUID": engine,
        "governanceEngineName": "ResourceExplorerSurvey" if engine == ENGINE_GUID else "PostgreSQLSurvey",
        "actionStatus": status,
        "requestType": request_type,
        "processingEngineUserId": claimed_by,
        "requesterUserId": requester,
        "requestedStartTime": 1_760_400_000_000,
        "actionTargetElements": [{"actionTargetName": "newAsset", "actionTargetGUID": target}],
    }


@pytest.fixture
def reg(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "engine_host.db"))
    r.register_database(DatabaseEntity(slug="coco_pharma", display_name="coco_pharma", db_type="postgresql",
                                       host="localhost", port=5442, database_name="coco_pharma",
                                       egeria_asset_guid=DB_GUID))
    r.register_database(DatabaseEntity(slug="alice_db", display_name="alice_db", db_type="postgresql",
                                       host="localhost", port=5442, database_name="alice_db",
                                       egeria_asset_guid=PRIVATE_DB_GUID))
    return r


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("RE_ENGINE_HOST_ENABLED", "true")


class Fakes:
    """The pyegeria boundary: one autospec per client class, plus who each client was built for."""

    def __init__(self, monkeypatch, actions: list[dict]):
        from pyegeria import GovernanceEngine, MetadataExpert

        from resource_explorer import egeria_clients

        # The engine-action calls are on the Governance Engine view service (odpi/egeria#9371).
        self.engine = create_autospec(GovernanceEngine, spec_set=True, instance=True)
        self.expert = create_autospec(MetadataExpert, spec_set=True, instance=True)
        self.actions = actions
        # The listing returns exactly `actions`, whatever they are: a listing that returns more than Egeria's approved
        # filter would, so these tests pin RE's own screen (engine, status, claimant), not the server's filter.
        self.engine.get_approved_engine_actions.side_effect = self._page
        self.engine.get_active_claimed_engine_actions.side_effect = self._claimed
        self.expert.get_metadata_guid_by_unique_name.return_value = ENGINE_GUID
        # Engine host step 4: each candidate's neighbourhood is read before the claim. These actions were started
        # alone: Egeria's exact "nothing there" (no ControlFlow, so no previous step to wait for).
        self.expert.get_all_related_elements.return_value = "No elements found"
        self.built: list[tuple[str, str]] = []           # (class name, Egeria user the client was built for)
        self.claims: list[tuple[str, object]] = []       # (engine action guid, principal at the call)
        self.engine.claim_engine_action.side_effect = self._claim
        by_cls = {GovernanceEngine: self.engine, MetadataExpert: self.expert}

        def build(cls, view_server, platform_url, user_id, password):
            self.built.append((cls.__name__, user_id))
            return by_cls[cls]

        monkeypatch.setattr(egeria_clients, "_build", build)

    def _page(self, governance_engine_guid, start_from=0, page_size=0):
        if page_size:
            chunk = self.actions[start_from:start_from + page_size]
        else:
            chunk = self.actions[start_from:]
        return chunk or "No elements found"

    def _claimed(self, governance_engine_guid, start_from=0, page_size=0):
        """`…/engine-actions/active-claimed`: the engine's active actions the caller's user has claimed."""
        from resource_explorer.egeria_clients import _daemon_credential

        mine = [a for a in self.actions if a["governanceEngineGUID"] == governance_engine_guid
                and a["processingEngineUserId"] == _daemon_credential()[0]
                and a["actionStatus"] in ("WAITING", "ACTIVATING", "IN_PROGRESS")]
        return mine[start_from:] or "No elements found"

    def _claim(self, engine_action_guid):
        """Egeria's claim gate (C5): APPROVED and unclaimed, else 403-003; a claim sets ACTIVATING."""
        from resource_explorer.egeria_clients import _daemon_credential, current_principal

        action = next((a for a in self.actions if a["elementHeader"]["guid"] == engine_action_guid), None)
        if action is not None and (action["actionStatus"] != "APPROVED" or action["processingEngineUserId"]):
            raise _refused_claim()
        self.claims.append((engine_action_guid, current_principal()))
        if action is not None:
            action["actionStatus"] = "ACTIVATING"
            action["processingEngineUserId"] = _daemon_credential()[0]

    def egeria_calls(self) -> list[str]:
        return [c[0] for m in (self.engine, self.expert) for c in m.mock_calls if c[0]]


def _host(reg):
    from resource_explorer.engine_host import EngineHost

    return EngineHost(registry=reg)


def _engine_rows(reg) -> list[dict]:
    import json

    rows = reg.list_runs(kind="engine_action")
    for r in rows:
        r["target"] = json.loads(r["target"])
    return rows


# ── test 2: only RE's approved work is claimed ─────────────────────────────────

def test_only_res_approved_work_of_a_known_type_is_claimed_once(reg, enabled, monkeypatch, caplog):
    fakes = Fakes(monkeypatch, [
        _action("a-postgres", engine=OTHER_ENGINE_GUID, request_type="survey-postgres-database"),
        # Another engine's action that happens to share RE's request type: still not RE's.
        _action("a-other-same-type", engine=OTHER_ENGINE_GUID),
        _action("a-requested", status="REQUESTED"),
        _action("a-unknown", request_type="re-no-such-step"),
        _action("a-claimed", claimed_by="someoneelse"),
        _action("a-good"),
    ])
    host = _host(reg)
    try:
        with caplog.at_level(logging.INFO, logger="resource_explorer.engine_host"):
            result = host.sweep_once()
            host.sweep_once()          # a second sweep: nothing new is claimed, the unknown type is not re-logged
    finally:
        host.close()

    claimed = [g for g, _ in fakes.claims]
    assert claimed == ["a-good"], f"claimed {claimed}; only RE's APPROVED, unclaimed, known-type action may be"
    # Not even ATTEMPTED: RE filters before asking (as native does), it does not lean on Egeria's claim gate.
    attempted = [c.args[0] for c in fakes.engine.claim_engine_action.call_args_list]
    assert attempted == ["a-good"], f"claim attempted on {attempted}"
    assert result.claimed == ["a-good"]
    assert "a-unknown" in result.skipped.get("unknown_request_type", [])
    unknown_lines = [r for r in caplog.records if "a-unknown" in r.getMessage()]
    assert len(unknown_lines) == 1, "an unknown request type is recorded once, not on every sweep"

    rows = _engine_rows(reg)
    assert len(rows) == 1
    row = rows[0]
    assert row["state"] == "queued"
    assert row["requested_by"] == REQUESTER
    assert row["target"]["engine_action_guid"] == "a-good"
    assert row["target"]["request_type"] == "re-db-derived"
    assert row["target"]["step_key"] == "db_derived"
    assert row["target"]["entity_type"] == "database"
    assert row["target"]["slug"] == "coco_pharma"
    assert row["target"]["governance_engine_guid"] == ENGINE_GUID
    assert row["target"]["requester_user_id"] == REQUESTER


def test_the_engine_guid_is_resolved_by_qualified_name(reg, enabled, monkeypatch):
    fakes = Fakes(monkeypatch, [])
    host = _host(reg)
    try:
        host.sweep_once()
    finally:
        host.close()
    fakes.expert.get_metadata_guid_by_unique_name.assert_called_with("ResourceExplorerSurvey")


def test_with_no_engine_declared_in_egeria_nothing_is_claimed(reg, enabled, monkeypatch):
    fakes = Fakes(monkeypatch, [_action("a-good")])
    fakes.expert.get_metadata_guid_by_unique_name.return_value = "No elements found"
    host = _host(reg)
    try:
        result = host.sweep_once()
    finally:
        host.close()
    assert fakes.claims == []
    assert result.claimed == []
    fakes.engine.get_approved_engine_actions.assert_not_called()
    assert _engine_rows(reg) == []


# ── test 1: a refused claim is ordinary ────────────────────────────────────────

def _refused_claim():
    from pyegeria.core._exceptions import PyegeriaUnauthorizedException

    return PyegeriaUnauthorizedException(None, context={"caller method": "claim_engine_action"}, additional_info={
        "relatedHTTPCode": 403,
        "exceptionErrorMessageId": "OMAG-GENERIC-HANDLERS-403-003",
        "exceptionErrorMessage": "OMAG-GENERIC-HANDLERS-403-003 The engine action is already claimed",
    })


def test_a_refused_claim_writes_nothing_logs_no_error_and_the_next_sweep_continues(reg, enabled, monkeypatch,
                                                                                    caplog):
    fakes = Fakes(monkeypatch, [_action("a-raced")])
    original = fakes.engine.claim_engine_action.side_effect

    def first_refused(engine_action_guid):
        if engine_action_guid == "a-raced":
            raise _refused_claim()
        return original(engine_action_guid)

    fakes.engine.claim_engine_action.side_effect = first_refused
    host = _host(reg)
    try:
        with caplog.at_level(logging.DEBUG, logger="resource_explorer.engine_host"):
            first = host.sweep_once()
        assert first.refused == ["a-raced"]
        assert first.claimed == []
        assert _engine_rows(reg) == []
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR], \
            "another claimant winning is an ordinary outcome, never an error"

        fakes.actions[:] = [_action("a-next")]
        second = host.sweep_once()
    finally:
        host.close()
    assert second.claimed == ["a-next"]
    assert [r["target"]["engine_action_guid"] for r in _engine_rows(reg)] == ["a-next"]


# ── test 4 (the claim part): the same identity end to end ─────────────────────

def test_the_claim_is_made_as_the_engine_host_daemon_for_the_requester(reg, enabled, monkeypatch):
    from resource_explorer.egeria_clients import _daemon_credential

    fakes = Fakes(monkeypatch, [_action("a-good", requester="erinoverview")])
    host = _host(reg)
    try:
        host.sweep_once()
    finally:
        host.close()

    assert len(fakes.claims) == 1
    guid, principal = fakes.claims[0]
    assert guid == "a-good"
    assert principal.kind == "daemon"
    assert principal.reason == "engine_host"
    assert principal.requested_by == "erinoverview"
    daemon_user = _daemon_credential()[0]
    assert principal.client_user == daemon_user
    # Every client was built for RE's service account; the requester is recorded, never the credential.
    assert fakes.built and all(user == daemon_user for _, user in fakes.built), fakes.built
    assert [r["requested_by"] for r in _engine_rows(reg)] == ["erinoverview"]


# ── test 8: a private target is left unclaimed ────────────────────────────────

def test_an_action_on_a_privately_owned_resource_is_not_claimed(reg, enabled, monkeypatch):
    from resource_explorer.a2a_auth import CallerIdentity, current_caller

    reset = current_caller.set(CallerIdentity(user_id="alice", egeria_token=None, auth_source="app-jwt"))
    try:
        inv = reg.create_investigation("Alice Private", project_classification="PersonalProject")
        ws = reg.get_or_create_working_set(inv["slug"])
        reg.add_working_set_member(ws["slug"], "database", "alice_db")
    finally:
        current_caller.reset(reset)
    assert reg.private_owner_for_entity("database", "alice_db") == "alice"

    fakes = Fakes(monkeypatch, [_action("a-private", target=PRIVATE_DB_GUID), _action("a-public")])
    host = _host(reg)
    try:
        result = host.sweep_once()
    finally:
        host.close()
    assert [g for g, _ in fakes.claims] == ["a-public"]
    assert "a-private" in result.skipped.get("private_target", [])
    assert [r["target"]["engine_action_guid"] for r in _engine_rows(reg)] == ["a-public"]


# ── test 10: off means off ────────────────────────────────────────────────────

def test_with_the_flag_unset_the_sweep_makes_no_egeria_call(reg, monkeypatch):
    monkeypatch.delenv("RE_ENGINE_HOST_ENABLED", raising=False)
    fakes = Fakes(monkeypatch, [_action("a-good")])
    host = _host(reg)
    try:
        result = host.sweep_once()
    finally:
        host.close()
    assert fakes.built == [], "no Egeria client may even be built when the engine host is off"
    assert fakes.egeria_calls() == []
    assert result.claimed == []
    assert _engine_rows(reg) == []


def test_with_the_flag_unset_the_worker_starts_no_engine_host_loop(monkeypatch):
    from resource_explorer import worker

    monkeypatch.delenv("RE_ENGINE_HOST_ENABLED", raising=False)
    assert "engine-host" not in [s.name for s in worker.loop_specs()]
    monkeypatch.setenv("RE_ENGINE_HOST_ENABLED", "true")
    spec = {s.name: s for s in worker.loop_specs()}["engine-host"]
    assert spec.lock_name == "engine-host"
    assert spec.interval_seconds == 5


# ── E4 is built (step 2): a claimed row is now run by the queue, not held ──────
# Changed in step 2 on purpose: step 1 pinned "held queued until its handler exists"; the handler now exists
# (tests/test_engine_host_e4.py covers what it does), so the queue claims the row.

def test_a_claimed_row_is_taken_by_the_queue_now_its_handler_exists(reg, enabled, monkeypatch):
    from resource_explorer import run_queue

    Fakes(monkeypatch, [_action("a-good")])
    host = _host(reg)
    try:
        host.sweep_once()
    finally:
        host.close()
    ran = []
    monkeypatch.setitem(run_queue.HANDLERS, "engine_action",
                        lambda target, result_ref: ran.append(target["engine_action_guid"])
                        or run_queue.RunOutcome(state="succeeded"))
    assert run_queue.claim_and_execute_once(reg) is not None
    assert ran == ["a-good"]


# ── review fixes: adopt RE's own orphaned claims; fail closed on targets ──────

def _daemon_user() -> str:
    from resource_explorer.egeria_clients import _daemon_credential

    return _daemon_credential()[0]


def test_a_claim_that_errored_but_was_committed_is_adopted_once_by_the_restart_pass(reg, enabled, monkeypatch):
    """A timeout or 5xx after Egeria committed the claim leaves the action ACTIVATING under RE's user and no
    runs row. The sweep never adopts it (an approved-only listing cannot show it, design §2.2); the restart pass
    does: exactly one row, and no second claim attempt."""
    fakes = Fakes(monkeypatch, [_action("a-orphan")])
    commit_then_fail = fakes.engine.claim_engine_action.side_effect

    def timeout_after_commit(engine_action_guid):
        commit_then_fail(engine_action_guid)
        raise RuntimeError("read timeout")

    fakes.engine.claim_engine_action.side_effect = timeout_after_commit
    host = _host(reg)
    try:
        first = host.sweep_once()
        assert first.errors == ["a-orphan"] and _engine_rows(reg) == []
        swept = host.sweep_once()
        assert swept.adopted == [] and _engine_rows(reg) == [], "the sweep never adopts (design §2.2)"
        second = host.restart_pass()
        third = host.restart_pass()
    finally:
        host.close()
    assert second.adopted == ["a-orphan"]
    assert third.adopted == []
    assert [r["target"]["engine_action_guid"] for r in _engine_rows(reg)] == ["a-orphan"]
    assert fakes.engine.claim_engine_action.call_count == 1


def test_a_claim_whose_row_could_not_be_written_is_adopted_by_the_restart_pass(reg, enabled, monkeypatch):
    fakes = Fakes(monkeypatch, [_action("a-norow")])
    real_enqueue = reg.enqueue_run
    calls = {"n": 0}

    def fail_first(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("registry unavailable")
        return real_enqueue(*args, **kwargs)

    monkeypatch.setattr(reg, "enqueue_run", fail_first)
    host = _host(reg)
    try:
        first = host.sweep_once()
        assert first.claimed == ["a-norow"] and _engine_rows(reg) == []
        assert host.sweep_once().adopted == [] and _engine_rows(reg) == []
        second = host.restart_pass()
        host.restart_pass()
    finally:
        host.close()
    assert second.adopted == ["a-norow"]
    rows = _engine_rows(reg)
    assert [r["target"]["engine_action_guid"] for r in rows] == ["a-norow"]
    assert rows[0]["requested_by"] == REQUESTER
    assert fakes.engine.claim_engine_action.call_count == 1


def test_an_action_another_engine_user_claimed_is_never_adopted(reg, enabled, monkeypatch):
    action = _action("a-theirs", status="ACTIVATING", claimed_by="postgresqlsurveyengine")
    fakes = Fakes(monkeypatch, [action])
    host = _host(reg)
    try:
        result = host.sweep_once()
        host.sweep_once()
    finally:
        host.close()
    assert result.adopted == [] and "a-theirs" in result.skipped.get("already_claimed", [])
    assert _engine_rows(reg) == []
    fakes.engine.claim_engine_action.assert_not_called()


def test_an_own_claim_that_already_has_a_row_is_not_adopted_again(reg, enabled, monkeypatch):
    reg.enqueue_run("engine_action", {"engine_action_guid": "a-mine"}, requested_by=REQUESTER)
    Fakes(monkeypatch, [_action("a-mine", status="ACTIVATING", claimed_by=_daemon_user())])
    host = _host(reg)
    try:
        result = host.sweep_once()
    finally:
        host.close()
    assert result.adopted == []
    assert len(_engine_rows(reg)) == 1


def test_an_action_with_an_unregistered_target_is_claimed_to_be_failed(reg, enabled, monkeypatch):
    """Changed in step 2 (owner ruling, 2026-10-14): an unregistered target is claimed and then completed with
    "not registered in Resource Explorer", as native claims and fails NO_TARGET_ASSET; step 1 left it unclaimed.
    The row carries the refusal and no slug, so the handler runs nothing for it."""
    action = _action("a-mixed")
    action["actionTargetElements"].append(
        {"actionTargetName": "other", "actionTargetGUID": "0a1b2c3d-0000-4000-8000-0000000000ff"})
    fakes = Fakes(monkeypatch, [action])
    host = _host(reg)
    try:
        result = host.sweep_once()
    finally:
        host.close()
    assert [g for g, _ in fakes.claims] == ["a-mixed"]
    assert result.claimed == ["a-mixed"]
    [row] = _engine_rows(reg)
    assert "not registered in Resource Explorer" in row["target"]["refusal"]


def test_a_failing_private_owner_check_claims_nothing(reg, enabled, monkeypatch):
    def boom(entity_type, slug):
        raise RuntimeError("registry read failed")

    monkeypatch.setattr(reg, "private_owner_for_entity", boom)
    fakes = Fakes(monkeypatch, [_action("a-good")])
    host = _host(reg)
    try:
        host.sweep_once()
    finally:
        host.close()
    fakes.engine.claim_engine_action.assert_not_called()
    assert _engine_rows(reg) == []


def test_close_closes_the_clients_http_sessions(reg, enabled, monkeypatch):
    fakes = Fakes(monkeypatch, [])
    host = _host(reg)
    host.sweep_once()
    host.close()
    fakes.engine.close_session.assert_called()
    fakes.expert.close_session.assert_called()


def test_stopping_the_loop_joins_its_thread(monkeypatch):
    import threading

    from resource_explorer import engine_host

    started = threading.Event()

    class Idle:
        def __init__(self, *a, **k):
            pass

        def sweep_once(self):
            started.set()

        def close(self):
            pass

    monkeypatch.setattr(engine_host, "EngineHost", Idle)
    loop = engine_host.EngineHostLoop(interval=0.05)
    loop.start()
    assert started.wait(5)
    thread = loop._thread
    loop.stop()
    assert not thread.is_alive()
