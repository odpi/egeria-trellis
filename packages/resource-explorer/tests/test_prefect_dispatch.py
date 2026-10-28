"""Prefect dispatch: which engine runs a step, and whether the API path works.

Two faults, neither of which produced an error.

**The API path had never executed.** run_prefect_step began with
`asyncio.get_running_loop()`, while a redundant `import asyncio` further down the
same function turned `asyncio` into a closure cell (the lambda captures it), so
that first line did a LOAD_DEREF on a cell nothing had stored and raised
UnboundLocalError. The broad `except` read it as "API unreachable", logged a
warning, and ran the flow locally. Every Prefect step has always run in-process
and _run_prefect_step_api was dead code — while the log said only that dispatch
had "failed", which is exactly what a genuinely unreachable server looks like.

**A global flag overrode a per-step declaration.** `prefect.enabled` re-routed
every step that had explicitly declared executes_at="resource-explorer".
executes_at is documented as naming the execution engine, and as open-ended so
engines can be chosen per step; a flag that overrules it removes the only way to
say "run this one here" and makes executes_at="prefect" redundant. Routing RE's
own steps through Prefect is a legitimate deployment choice, so it keeps working
— it just has to be asked for by name.

**A third fault, found once the first two were fixed and the API path could
finally run for real (2026-08-26, verified live against a local `prefect
server` + worker — deploy required switching `.deploy()` to `.from_source()`,
since a bare `work_pool_name` assumes remote/image-based storage rather than a
local `process` pool).** `_run_prefect_step_api` fetched the completed flow
run's result via `client.resolve_value(state.data)` — that method doesn't
exist on `PrefectClient` in Prefect 3.x, so every completed API-dispatched run
raised `AttributeError`, was caught by the same broad `except`, and fell
through to a **second, duplicate local execution** — the step's real work had
already happened once via the worker, then happened again via the fallback,
silently. Fixed to `state.result(raise_on_failure=True)`, the real 3.x API —
which then raised `MissingResult` until `re_survey_flow` also declared
`persist_result=True` (3.x doesn't persist flow results by default, and a
`State` fetched back via `read_flow_run` from a different process than the one
that ran the flow has no in-memory result to fall back to)."""
from __future__ import annotations

import types
from unittest.mock import patch

import pytest

import resource_explorer.surveyors.prefect_adapter as adapter


def _cfg(enabled=True):
    return types.SimpleNamespace(prefect=types.SimpleNamespace(
        enabled=enabled, api_url="http://localhost:4200/api", work_pool="p"))


class TestTheApiPathIsReachable:

    def test_enabled_actually_calls_the_api(self):
        """The regression guard. Before the fix this never ran, and the failure
        was indistinguishable from an unreachable Prefect server."""
        called = {}

        async def fake_api(entity_type, slug, step_name, runner_kwargs):
            called["yes"] = step_name
            return {"via": "api"}, "flow-run-123"

        with patch.object(adapter, "get_config", lambda: _cfg(enabled=True)), \
             patch.object(adapter, "_run_prefect_step_api", fake_api), \
             patch.object(adapter.run_surveyor_step_task, "fn", return_value={"via": "local"}):
            dispatch_info: dict = {}
            out = adapter.run_prefect_step("repo", "s", "repo_health", {},
                                           dispatch_info=dispatch_info)

        assert called.get("yes") == "repo_health"
        assert out == {"via": "api"}
        # dispatch honesty (2026-09-28): a genuine Prefect dispatch records
        # its real flow-run id, so a step_runs row can be checked against
        # Prefect's own API rather than merely trusted.
        assert dispatch_info == {
            "engine": "prefect", "flow_run_id": "flow-run-123", "dispatch_failed": "",
        }

    def test_disabled_runs_locally_without_touching_the_api(self):
        with patch.object(adapter, "get_config", lambda: _cfg(enabled=False)), \
             patch.object(adapter, "_run_prefect_step_api",
                          side_effect=AssertionError("must not be called")), \
             patch.object(adapter.run_surveyor_step_task, "fn", return_value={"via": "local"}):
            assert adapter.run_prefect_step("repo", "s", "repo_health", {}) == {"via": "local"}

    def test_a_cancelled_run_does_not_fall_back_to_local(self):
        """Found live 2026-08-26 testing the cancel endpoint: a cancelled
        flow run's exception was caught by the same broad `except` as a
        connection failure, and the fallback quietly re-ran the step's work
        locally — cancel had no actual effect. A genuine user-initiated
        cancellation must propagate, not be treated as "API unreachable,
        try locally"."""
        async def cancelled(*a, **kw):
            raise adapter.PrefectFlowRunCancelled("flow run 'x' was cancelled")

        with patch.object(adapter, "get_config", lambda: _cfg(enabled=True)), \
             patch.object(adapter, "_run_prefect_step_api", cancelled), \
             patch.object(adapter.run_surveyor_step_task, "fn",
                          side_effect=AssertionError("must not fall back for a cancellation")):
            with pytest.raises(adapter.PrefectFlowRunCancelled):
                adapter.run_prefect_step("repo", "s", "repo_health", {})

    def test_a_failing_api_still_falls_back_to_local(self):
        """The fallback is correct behaviour for a real server problem — it is
        only the blindness that was wrong."""
        async def boom(*a, **kw):
            raise ConnectionError("prefect server down")

        with patch.object(adapter, "get_config", lambda: _cfg(enabled=True)), \
             patch.object(adapter, "_run_prefect_step_api", boom), \
             patch.object(adapter.run_surveyor_step_task, "fn", return_value={"via": "local"}):
            assert adapter.run_prefect_step("repo", "s", "repo_health", {}) == {"via": "local"}

    def test_the_fallback_names_the_exception_type(self, caplog):
        """A bug in this function must not read as a connection problem, which
        is precisely how the UnboundLocalError hid."""
        async def boom(*a, **kw):
            raise ValueError("not a network problem")

        with patch.object(adapter, "get_config", lambda: _cfg(enabled=True)), \
             patch.object(adapter, "_run_prefect_step_api", boom), \
             patch.object(adapter.run_surveyor_step_task, "fn", return_value={"via": "local"}):
            adapter.run_prefect_step("repo", "s", "repo_health", {})

        assert "ValueError" in caplog.text


class TestApiResultRetrieval:
    """The third fault (see module docstring): a completed API-dispatched flow
    run's result was fetched via `client.resolve_value`, which doesn't exist on
    Prefect 3.x's `PrefectClient` — every completed run raised AttributeError,
    was swallowed by the broad `except` in run_prefect_step, and silently
    re-ran the step's real work a second time, locally. These guard the fix
    directly against `_run_prefect_step_api`'s actual source, the same pattern
    `test_predicate_matches_production` below uses for the routing predicate —
    a live server round-trip is covered by manual verification (2026-08-26,
    see this file's module docstring), not re-created here as a mock, since a
    mock of `state.result()` can't tell a working call from a plausible-looking
    wrong one the way the real API already did to `resolve_value`."""

    def test_never_calls_the_nonexistent_resolve_value_again(self):
        import inspect

        src = inspect.getsource(adapter._run_prefect_step_api)
        assert "client.resolve_value" not in src, (
            "resolve_value doesn't exist on PrefectClient in Prefect 3.x — "
            "every completed flow run silently re-ran the step a second time "
            "via local fallback before this was caught. See the module "
            "docstring's third fault.")

    def test_fetches_the_result_via_state_result(self):
        import inspect

        src = inspect.getsource(adapter._run_prefect_step_api)
        assert "state.result(raise_on_failure=True)" in src, (
            "State.result()/aresult() is the real Prefect 3.x API for pulling "
            "a completed flow run's return value back out.")

    def test_the_flow_persists_its_result(self):
        """Without this, `state.result()` raises MissingResult for any run
        polled back via read_flow_run from a different process than the one
        that executed it — i.e. every real distributed dispatch, which is the
        entire point of the API path."""
        from resource_explorer.prefect.flows import re_survey_flow

        assert re_survey_flow.persist_result is True


class TestEngineRouting:

    @staticmethod
    def _use_prefect_for(executes_at, step_key="repo_health", enabled=False, route_local=False):
        """Rebuild the executor's own predicate against a stub config."""
        from resource_explorer.surveyors import survey_definition_executor as ex

        step = types.SimpleNamespace(executes_at=executes_at, re_analysis_step=step_key)
        cfg = types.SimpleNamespace(prefect=types.SimpleNamespace(
            enabled=enabled, route_local_steps=route_local))
        with patch.object(ex, "get_config", lambda: cfg, create=True):
            # _use_prefect is defined inside run_survey_definition, so exercise the
            # same logic through the module's own config rather than duplicating it.
            return _predicate(step, cfg)

    def test_an_explicit_prefect_step_always_goes_to_prefect(self):
        assert _predicate(_step("prefect"), _c(False, False)) is True

    def test_a_resource_explorer_step_stays_local_when_prefect_is_merely_enabled(self):
        """The fix. Enabling Prefect must not overrule a step that named its
        engine — that is what executes_at is for."""
        assert _predicate(_step("resource-explorer"), _c(True, False)) is False

    def test_it_is_routed_only_when_explicitly_asked_for(self):
        assert _predicate(_step("resource-explorer"), _c(True, True)) is True

    def test_route_local_steps_alone_does_nothing_without_prefect_enabled(self):
        assert _predicate(_step("resource-explorer"), _c(False, True)) is False

    def test_an_egeria_step_is_never_routed_to_prefect(self):
        assert _predicate(_step("egeria"), _c(True, True)) is False

    def test_prefect_only_steps_go_to_prefect_regardless(self):
        """They have no STEP_REGISTRY entry, so there is nothing local to run."""
        assert _predicate(_step("resource-explorer", "soda_data_quality"), _c(False, False)) is True


# The predicate lives inside run_survey_definition; mirror it here rather than
# refactoring production code purely for testability, and assert in
# test_predicate_matches_production below that the two have not diverged.
def _step(executes_at, key="repo_health"):
    return types.SimpleNamespace(executes_at=executes_at, re_analysis_step=key)


def _c(enabled, route_local):
    return types.SimpleNamespace(enabled=enabled, route_local_steps=route_local)


def _predicate(step, cfg):
    prefect_only = ("soda_data_quality", "great_expectations_validation")
    if step.executes_at == "prefect" or step.re_analysis_step in prefect_only:
        return True
    if step.executes_at == "resource-explorer":
        return bool(cfg.enabled and getattr(cfg, "route_local_steps", False))
    return False


def test_predicate_matches_production():
    """Guard against this file's mirror drifting from the real one."""
    import inspect

    from resource_explorer.surveyors import survey_definition_executor as ex

    # _use_prefect is nested inside SurveyDefinitionExecutor._execute (the
    # dispatch loop extracted out of `run` on 2026-09-20 so
    # `run_synthetic_step` — the egeria-adaptive fold-in's one-step,
    # non-Egeria-hosted entry point — could share it), not `run` itself and
    # not the module-level run_survey_definition wrapper.
    src = inspect.getsource(ex.SurveyDefinitionExecutor._execute)
    assert "route_local_steps" in src, (
        "production no longer consults route_local_steps — the mirror in this test "
        "file is stale and its assertions no longer mean anything")
    assert 'cfg.enabled and getattr(cfg, "route_local_steps", False)' in src


def test_route_local_steps_defaults_to_off():
    from resource_explorer.config import PrefectConfig

    assert PrefectConfig().route_local_steps is False
