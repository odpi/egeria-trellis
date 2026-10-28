"""No database password ever becomes something Prefect stores.

Prefect keeps a flow run's parameters in its own database and shows them in
its UI. These tests put a FAKE Prefect (no server, no worker, no network) in
every place a password could cross -- the REST dispatch to a worker, the
in-process one-step flow, the whole-definition flow -- record everything it is
handed, serialise it and grep for the fake passwords. They also prove the step
still connects with working credentials: the fake "worker" runs the real task
body, which resolves the credential in memory.

Stored credentials: never sent; resolved from the registry where the step runs.
Override credentials (parity G2): never sent; kept in RE's memory behind an
opaque reference, only for a flow that runs in this process.
"""
from __future__ import annotations

import json
import logging
import types
from unittest.mock import MagicMock, patch

import pytest

import resource_explorer.surveyors.prefect_adapter as pa
from resource_explorer import credential_handoff as ch
from resource_explorer.prefect import flows
from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.surveyors.survey_definition_executor import (
    ResourceTypeAdapter, SurveyDefinitionExecutor, register_adapter)
from resource_explorer.surveyors.survey_definition_reader import SurveyDefinition, SurveyStep

OVERRIDE_PW = "test-password-not-real"
STORED_PW = "stored-password-not-real"
STORED_USER = "stored_user"
OVERRIDE_USER = "one_off_user"


@pytest.fixture(autouse=True)
def _restore_adapters(monkeypatch):
    """The fake adapter is registered as "database" (the soda step only runs
    for that type); put the real registry of adapters back afterwards."""
    from resource_explorer.surveyors import survey_definition_executor as sde
    monkeypatch.setattr(sde, "_ADAPTERS", dict(sde._ADAPTERS))


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    assert not getattr(r, "is_postgres", False)
    r.register_database(DatabaseEntity(
        slug="mydb", display_name="My DB", db_type="postgresql", host="localhost", port=5432,
        database_name="mydb", db_user=STORED_USER, db_password=STORED_PW))
    return r


class World:
    """One fake adapter, one definition, and a recorder for what Prefect saw."""

    def __init__(self, registry, steps=("local_step", "soda_data_quality"), runner_raises=False):
        self.registry = registry
        self.received: list[dict] = []        # what the step's runner connected with, in memory
        self.prefect_saw: list = []           # EVERYTHING handed to the fake Prefect
        self.soda_saw: list[dict] = []

        entity = types.SimpleNamespace(slug="mydb", display_name="mydb", db_user=STORED_USER,
                                       db_password=STORED_PW, db_type="postgresql", host="h",
                                       port=1, database_name="mydb")

        def runner(ent, reg, **kw):
            self.received.append({"user": kw.get("db_user"), "pwd": kw.get("db_pwd")})
            if runner_raises:
                raise RuntimeError(f"connect failed for password {kw['db_pwd']}")
            return {"ok": True, "echo": f"connected as {kw.get('db_user')}"}

        self.adapter = ResourceTypeAdapter(
            entity_type="database", technology_type="PF Cred",
            re_analysis_steps={"local_step": runner, "second_step": runner},
            get_entity=lambda r, slug: entity, publish=MagicMock(return_value=""))
        register_adapter(self.adapter)
        step_defs = {
            "local_step": SurveyStep(guid="a", display_name="Local", qualified_name="Step::Local",
                                     executes_at="prefect", re_analysis_step="local_step"),
            "second_step": SurveyStep(guid="c", display_name="Second", qualified_name="Step::Second",
                                      executes_at="prefect", re_analysis_step="second_step"),
            "soda_data_quality": SurveyStep(guid="b", display_name="Soda", qualified_name="Step::Soda",
                                            executes_at="prefect", re_analysis_step="soda_data_quality"),
        }
        sd = SurveyDefinition(
            process_guid="pp", display_name="PF", qualified_name="GovActionProcess::PF",
            supported_technology_type="PF Cred", steps=[step_defs[s] for s in steps])
        reader = MagicMock()
        reader.fetch.return_value = sd
        reader.find_candidate_process_guids.return_value = [
            {"guid": "pp", "qualified_name": "GovActionProcess::PF", "display_name": "PF"}]
        self.executor = SurveyDefinitionExecutor(registry, reader=reader)

    # the soda task is replaced: the real one needs the soda package and a database
    def fake_soda(self, **kw):
        self.soda_saw.append({"user": kw["db_user"], "pwd": kw["db_password"]})
        return {"exit_code": 0, "results": {}}

    def everything_prefect_saw(self) -> str:
        return json.dumps(self.prefect_saw, default=str)


def _patches(world, *, reachable=True, mode="rest"):
    """Wire the fake Prefect. mode: 'rest' (worker via REST), 'inproc' (one-step
    flow in this process), plus the whole-definition flow in every mode."""
    ctx = []

    # --- REST: a recording client; its 'worker' runs the real task body -------
    class FakeState:
        def __init__(self, out): self._out = out
        def is_completed(self): return True
        def is_failed(self): return False
        def is_cancelled(self): return False
        async def result(self, raise_on_failure=True): return self._out

    class FakeClient:
        def __init__(self): self.out = {}
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def read_deployment_by_name(self, name):
            return types.SimpleNamespace(id="dep-1", work_pool_name="pool")
        async def read_workers_for_work_pool(self, pool):
            return [types.SimpleNamespace(status="ONLINE")]
        async def create_flow_run_from_deployment(self, deployment_id, parameters, tags):
            world.prefect_saw.append({"flow_run_parameters": parameters, "tags": tags})
            # the worker is ANOTHER process: it sees only what was sent
            ref = parameters.get("credential_ref", "")
            assert not ref, "a worker cannot read RE's memory, so no reference is sent to one"
            self.out = flows.run_surveyor_step_task.fn(**parameters)
            return types.SimpleNamespace(id="fr-rest")
        async def read_flow_run(self, fid):
            return types.SimpleNamespace(state=FakeState(self.out))

    ctx.append(patch.object(pa, "re_prefect_client", lambda: FakeClient()))

    # --- in-process one-step flow ---------------------------------------------
    def fake_inproc_flow(**params):
        world.prefect_saw.append({"in_process_flow_parameters": params})
        result = flows.run_surveyor_step_task.fn(**params)
        return {"result": result, "flow_run_id": "fr-inproc"}

    ctx.append(patch.object(pa, "_in_process_step_flow", lambda: fake_inproc_flow))
    ctx.append(patch.object(pa, "check_prefect_reachable_sync",
                            lambda: (reachable, "http://prefect.test/api" if reachable else "down")))

    # --- whole-definition flow: runs the real planned-step task body ----------
    def fake_definition_flow(entity_type, slug, plan, runner_kwargs=None, surveyed_at="",
                             credential_ref=""):
        if not reachable:
            raise ConnectionError("Prefect is down")
        world.prefect_saw.append({"definition_flow_parameters": {
            "entity_type": entity_type, "slug": slug, "plan": plan,
            "runner_kwargs": runner_kwargs, "surveyed_at": surveyed_at,
            "credential_ref": credential_ref}})
        out = []
        for e in plan:
            out.append(flows.run_planned_step_task.fn(
                entity_type=entity_type, slug=slug, step_key=e["step_key"],
                qualified_name=e.get("qualified_name", ""), runner_kwargs=runner_kwargs or {},
                upstream=[], guarded_by={}, surveyed_at="", credential_ref=credential_ref))
        return out

    ctx.append(patch.object(flows, "re_survey_definition_flow", fake_definition_flow))
    ctx.append(patch.object(flows, "ProjectRegistry", lambda *a, **k: world.registry))
    ctx.append(patch.object(flows, "run_soda_scan_task", world.fake_soda))
    ctx.append(patch.object(pa, "get_config", lambda: types.SimpleNamespace(prefect=types.SimpleNamespace(
        enabled=True, api_url="http://prefect.test/api", work_pool="pool",
        step_timeout_seconds=5, route_local_steps=False))))
    return ctx


class _Stack:
    def __init__(self, patches): self.patches = patches
    def __enter__(self):
        for p in self.patches: p.start()
    def __exit__(self, *a):
        for p in reversed(self.patches): p.stop()


def _run(world, **kw):
    kw.setdefault("engine_override", "prefect")
    return world.executor.run("database", "mydb", **kw)


def _assert_clean(world, res=None, caplog=None):
    seen = world.everything_prefect_saw()
    for pw in (OVERRIDE_PW, STORED_PW):
        assert pw not in seen, f"{pw!r} reached Prefect: {seen[:300]}"
        if res is not None:
            assert pw not in json.dumps(res, default=str)
        if caplog is not None:
            assert pw not in caplog.text


# ── A. stored credentials ───────────────────────────────────────────────────

def test_stored_credentials_per_step_rest_dispatch_sends_no_password_and_step_still_connects(registry, caplog):
    caplog.set_level(logging.DEBUG)
    w = World(registry, steps=("local_step", "soda_data_quality"))
    with _Stack(_patches(w)):
        # force the per-step route, as a definition that cannot go whole would
        with patch.object(SurveyDefinitionExecutor, "_any_step_needs_prerequisites", return_value=True):
            res = _run(w)
    assert [s["status"] for s in res["steps"]] == ["ok", "ok"]
    sent = [p["flow_run_parameters"] for p in w.prefect_saw]
    assert len(sent) == 2 and all("db_pwd" not in p["runner_kwargs"] and "db_user" not in p["runner_kwargs"]
                                  for p in sent)
    _assert_clean(w, res, caplog)
    # the worker resolved the stored credential from the registry, in memory
    assert w.received == [{"user": STORED_USER, "pwd": STORED_PW}]
    assert w.soda_saw == [{"user": STORED_USER, "pwd": STORED_PW}]


def test_stored_credentials_whole_definition_flow_sends_no_password_and_steps_still_connect(registry, caplog):
    caplog.set_level(logging.DEBUG)
    w = World(registry, steps=("local_step", "second_step"))
    with _Stack(_patches(w)):
        res = _run(w)
    assert all(s["status"] == "ok" for s in res["steps"]), res
    params = w.prefect_saw[0]["definition_flow_parameters"]
    assert params["runner_kwargs"] == {} and params["credential_ref"] == ""
    _assert_clean(w, res, caplog)
    assert w.received == [{"user": STORED_USER, "pwd": STORED_PW}] * 2


# ── B. override credentials ─────────────────────────────────────────────────

def test_override_whole_definition_runs_on_prefect_with_only_an_opaque_reference(registry, caplog):
    caplog.set_level(logging.DEBUG)
    w = World(registry, steps=("local_step", "second_step"))
    with _Stack(_patches(w)):
        res = _run(w, db_user=OVERRIDE_USER, db_pwd=OVERRIDE_PW, credential_scope="this run")
    assert all(s["status"] == "ok" for s in res["steps"]), res
    params = w.prefect_saw[0]["definition_flow_parameters"]
    assert params["credential_ref"].startswith("cred-") and params["runner_kwargs"] == {}
    _assert_clean(w, res, caplog)
    assert w.received == [{"user": OVERRIDE_USER, "pwd": OVERRIDE_PW}] * 2     # it really connected as the override
    assert all(s.get("ran_as") == {"user": OVERRIDE_USER, "scope": "this run"} for s in res["steps"])
    assert ch.active_references() == 0, "the reference is revoked when the run ends"


def test_override_per_step_runs_as_a_visible_in_process_flow_not_on_a_worker(registry, caplog):
    caplog.set_level(logging.DEBUG)
    w = World(registry, steps=("local_step", "soda_data_quality"))
    with _Stack(_patches(w)):
        with patch.object(SurveyDefinitionExecutor, "_any_step_needs_prerequisites", return_value=True):
            res = _run(w, db_user=OVERRIDE_USER, db_pwd=OVERRIDE_PW, credential_scope="this run")
    assert [s["status"] for s in res["steps"]] == ["ok", "ok"], res
    assert all("in_process_flow_parameters" in p for p in w.prefect_saw)          # none went to a worker
    assert all(p["in_process_flow_parameters"]["credential_ref"].startswith("cred-") for p in w.prefect_saw)
    _assert_clean(w, res, caplog)
    assert w.received == [{"user": OVERRIDE_USER, "pwd": OVERRIDE_PW}]
    assert w.soda_saw == [{"user": OVERRIDE_USER, "pwd": OVERRIDE_PW}], "soda/GE use the override, not the stored credential"
    assert all(s["engine"] == "prefect" and s["ran_as"]["user"] == OVERRIDE_USER for s in res["steps"])
    assert ch.active_references() == 0


def test_override_error_that_echoes_the_password_is_scrubbed_everywhere(registry, caplog):
    caplog.set_level(logging.DEBUG)
    w = World(registry, steps=("local_step",), runner_raises=True)
    with _Stack(_patches(w)):
        with patch.object(SurveyDefinitionExecutor, "_any_step_needs_prerequisites", return_value=True):
            res = _run(w, db_user=OVERRIDE_USER, db_pwd=OVERRIDE_PW, credential_scope="this run")
    assert res["errors"]
    _assert_clean(w, res, caplog)
    assert "***" in " ".join(res["errors"])


def test_stored_error_that_echoes_the_password_is_scrubbed_in_the_raised_exception():
    """What a failed Prefect task persists is the exception text."""
    entity = types.SimpleNamespace(slug="s", db_user=STORED_USER, db_password=STORED_PW)

    def boom(e, r, **kw):
        raise RuntimeError(f"auth failed for {kw['db_pwd']}")

    adapter = types.SimpleNamespace(get_entity=lambda r, s: entity, re_analysis_steps={"x": boom})
    with patch.object(flows, "get_adapter", lambda t: adapter), \
         patch.object(flows, "ProjectRegistry", lambda: object()):
        with pytest.raises(RuntimeError) as ei:
            flows.run_surveyor_step_task.fn("t", "s", "x", {})
    assert STORED_PW not in str(ei.value) and ei.value.__cause__ is None and ei.value.__suppress_context__


# ── degrade: Prefect unreachable ────────────────────────────────────────────

@pytest.mark.parametrize("override", [False, True])
def test_unreachable_prefect_still_degrades_to_local_and_leaks_nothing(registry, caplog, override):
    caplog.set_level(logging.DEBUG)
    w = World(registry, steps=("local_step", "second_step"))
    kw = dict(db_user=OVERRIDE_USER, db_pwd=OVERRIDE_PW, credential_scope="this run") if override else {}
    with _Stack(_patches(w, reachable=False)):
        # REST dispatch can't reach a server either
        with patch.object(pa, "re_prefect_client", side_effect=Exception("API Down")):
            res = _run(w, **kw)
    assert all(s["status"] == "ok" for s in res["steps"]), res
    want = ({"user": OVERRIDE_USER, "pwd": OVERRIDE_PW} if override
            else {"user": STORED_USER, "pwd": STORED_PW})
    assert w.received == [want] * 2, "the local loop connected with the right credential"
    _assert_clean(w, res, caplog)
    assert w.prefect_saw == []
    assert ch.active_references() == 0


def test_run_prefect_step_local_fallback_resolves_credentials_without_them_in_kwargs(registry, caplog):
    caplog.set_level(logging.DEBUG)
    w = World(registry, steps=("local_step",))
    with _Stack(_patches(w)):
        with patch.object(pa, "re_prefect_client", side_effect=Exception("API Down")):
            out = pa.run_prefect_step("database", "mydb", "local_step", {})
    assert out["ok"] and w.received == [{"user": STORED_USER, "pwd": STORED_PW}]
    _assert_clean(w, caplog=caplog)


# ── the guard ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("params", [
    {"runner_kwargs": {"db_pwd": "x"}},
    {"runner_kwargs": {"password": "x"}},
    {"a": [{"api_token": "x"}]},
    {"client_secret": "x"},
    {"runner_kwargs": {"db_user": "someone"}},
    {"note": f"postgresql://u:{OVERRIDE_PW}@h/db"},
])
def test_guard_raises_on_a_credential_looking_parameter(params):
    with pytest.raises(ch.CredentialLeakError) as ei:
        ch.assert_no_credentials(params)
    assert OVERRIDE_PW not in str(ei.value)


def test_guard_raises_on_a_known_password_in_a_value_and_allows_the_reference():
    with pytest.raises(ch.CredentialLeakError):
        ch.assert_no_credentials({"sodacl": f"password: {OVERRIDE_PW}"}, known_secrets=[OVERRIDE_PW])
    ch.assert_no_credentials({"credential_ref": "cred-abc", "slug": "s", "runner_kwargs": {"table_name": "t"}})


def test_run_prefect_step_refuses_a_credential_even_though_it_would_fall_back_locally():
    with pytest.raises(ch.CredentialLeakError):
        pa.run_prefect_step("t", "s", "x", {"db_pwd": OVERRIDE_PW})


def test_the_rest_dispatch_guard_raises_before_a_flow_run_is_created(registry):
    w = World(registry)
    with _Stack(_patches(w)):
        import asyncio
        with pytest.raises(ch.CredentialLeakError):
            asyncio.run(pa._run_prefect_step_api("t", "s", "x", {"db_pwd": OVERRIDE_PW}))
    assert w.prefect_saw == []


# ── the handoff store ───────────────────────────────────────────────────────

def test_handoff_reference_is_opaque_expires_and_is_revoked():
    t = [0.0]
    ref = ch.put("u", OVERRIDE_PW, ttl_seconds=10, clock=lambda: t[0])
    assert OVERRIDE_PW not in ref and ch.get(ref, clock=lambda: t[0]) == ("u", OVERRIDE_PW)
    t[0] = 11.0
    with pytest.raises(ch.CredentialHandoffExpired):
        ch.get(ref, clock=lambda: t[0])
    ref2 = ch.put("u", "p")
    ch.revoke(ref2)
    with pytest.raises(ch.CredentialHandoffExpired):
        ch.get(ref2)


def test_the_store_holds_the_password_encrypted():
    ref = ch.put("u", OVERRIDE_PW)
    try:
        assert OVERRIDE_PW.encode() not in ch._store[ref][1]
    finally:
        ch.revoke(ref)
