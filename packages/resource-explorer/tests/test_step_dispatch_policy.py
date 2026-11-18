"""TR-11 F-AB-3: the per-step Prefect dispatch policy (RULING-TR11-FAB3-STEP-DISPATCH-POLICY-2026-11-17, C1-C6).

One policy (PREFECT_DISPATCH_STEPS, with PREFECT_ROUTE_LOCAL_STEPS=true its deprecated `all` alias), one function
(`dispatch_decision`) that both executors and the startup summary ask, "heavy" = compute_cost in HEAVY_COMPUTE, and
a fallback that is recorded as a fallback, never as a dispatch.
"""
import logging
import types
import uuid
from unittest.mock import patch

import pytest

from resource_explorer.config import PrefectConfig
from resource_explorer.surveyors import step_dispatch_policy as sdp

EMPTY = sdp.DispatchPolicy()


def _cfg(monkeypatch, **env):
    """A PrefectConfig read from these environment variables only (no .env file)."""
    for name in (sdp.ENV_DISPATCH, sdp.ENV_ROUTE_LOCAL):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return PrefectConfig(_env_file=None)


def _info(compute):
    return types.SimpleNamespace(compute_cost=compute)


# ── C2: the precedence rows of dispatch_decision ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("engine", ["egeria", "egeria-adaptive"])
@pytest.mark.parametrize("policy", [EMPTY, sdp.parse("all"), sdp.parse("heavy,repo_health")])
def test_row1_an_egeria_step_is_never_dispatched(engine, policy):
    where, why = sdp.dispatch_decision("repo_health", engine, _info("high"), policy)
    assert where == engine and where != sdp.WORKER and "never" in why


@pytest.mark.parametrize("policy", [EMPTY, sdp.parse("repo_other"), sdp.parse("all")])
def test_row2_an_executes_at_prefect_step_always_goes_to_the_worker(policy):
    assert sdp.dispatch_decision("repo_health", "prefect", _info("low"), policy) == (
        sdp.WORKER, "executes_at=prefect")


def test_row3_a_listed_key_goes_to_the_worker_and_an_unlisted_one_stays():
    policy = sdp.parse("repo_symbol_extraction")
    assert sdp.dispatch_decision("repo_symbol_extraction", "resource-explorer", _info("medium"), policy)[0] == \
        sdp.WORKER
    assert sdp.dispatch_decision("repo_health", "resource-explorer", _info("medium"), policy)[0] == sdp.IN_PROCESS


def test_row3_heavy_matches_compute_cost_high_only():
    policy = sdp.parse("heavy")
    assert sdp.dispatch_decision("repo_x", "resource-explorer", _info("high"), policy)[0] == sdp.WORKER
    for compute in ("medium", "low", "", None):
        assert sdp.dispatch_decision("repo_x", "resource-explorer", _info(compute), policy)[0] == sdp.IN_PROCESS
    assert sdp.dispatch_decision("repo_x", "resource-explorer", None, policy)[0] == sdp.IN_PROCESS


def test_row3_all_sends_every_own_step():
    assert sdp.dispatch_decision("repo_x", "resource-explorer", _info("low"), sdp.parse("all"))[0] == sdp.WORKER


def test_row3_the_default_is_todays_behaviour_in_process():
    where, why = sdp.dispatch_decision("repo_x", "resource-explorer", _info("high"), EMPTY)
    assert where == sdp.IN_PROCESS and '""' in why


def test_a_prefect_only_step_goes_to_the_worker_whatever_the_policy():
    assert sdp.dispatch_decision("soda_data_quality", "resource-explorer", None, EMPTY)[0] == sdp.WORKER


# ── C1: one policy, run both env ways ───────────────────────────────────────────────────────────────────────────

def test_route_local_steps_alone_is_the_deprecated_alias_for_all(monkeypatch):
    policy = sdp.resolve(_cfg(monkeypatch, PREFECT_ROUTE_LOCAL_STEPS="true"))
    assert policy.all and policy.source == sdp.ENV_ROUTE_LOCAL
    assert any("deprecated" in w and sdp.ENV_DISPATCH in w for w in policy.warnings)


def test_route_local_steps_false_alone_is_the_default(monkeypatch):
    policy = sdp.resolve(_cfg(monkeypatch, PREFECT_ROUTE_LOCAL_STEPS="false"))
    assert policy.empty and policy.warnings == ()


def test_dispatch_steps_alone(monkeypatch):
    policy = sdp.resolve(_cfg(monkeypatch, PREFECT_DISPATCH_STEPS="repo_symbol_extraction, heavy"))
    assert policy.keys == {"repo_symbol_extraction"} and policy.heavy and not policy.all
    assert policy.source == sdp.ENV_DISPATCH and policy.warnings == ()


def test_neither_set_is_the_default(monkeypatch):
    policy = sdp.resolve(_cfg(monkeypatch))
    assert policy.empty and policy.source == "" and policy.warnings == ()


@pytest.mark.parametrize("dispatch, route", [("repo_symbol_extraction", "true"), ("all", "false"),
                                             ("heavy", "true")])
def test_both_set_and_disagreeing_dispatch_steps_wins_with_a_warning_naming_both(monkeypatch, dispatch, route):
    policy = sdp.resolve(_cfg(monkeypatch, PREFECT_DISPATCH_STEPS=dispatch, PREFECT_ROUTE_LOCAL_STEPS=route))
    assert policy.source == sdp.ENV_DISPATCH
    assert policy == sdp.DispatchPolicy(sdp.parse(dispatch).keys, sdp.parse(dispatch).heavy,
                                        sdp.parse(dispatch).all, sdp.ENV_DISPATCH, policy.warnings)
    (warning,) = [w for w in policy.warnings if "disagree" in w]
    assert sdp.ENV_DISPATCH in warning and sdp.ENV_ROUTE_LOCAL in warning


def test_both_set_and_agreeing_still_warns_the_alias_is_redundant(monkeypatch):
    policy = sdp.resolve(_cfg(monkeypatch, PREFECT_DISPATCH_STEPS="all", PREFECT_ROUTE_LOCAL_STEPS="true"))
    assert policy.all and not [w for w in policy.warnings if "disagree" in w]
    assert any("redundant" in w for w in policy.warnings)


def test_the_route_local_steps_default_is_still_off_and_dispatch_steps_empty():
    cfg = PrefectConfig(_env_file=None)
    assert cfg.route_local_steps is False and cfg.dispatch_steps == ""


# ── C3: "heavy" resolves over the real registries, by compute_cost, with no step names in the code ──────────────

def test_heavy_picks_exactly_the_compute_cost_high_steps():
    infos = sdp.registry_step_infos()
    expected = sorted(k for k, i in infos.items() if getattr(i, "compute_cost", "") == "high")
    assert expected, "no compute_cost=high step in the registries: the test would prove nothing"
    assert sdp.heavy_steps(infos) == expected
    assert sdp.dispatched_steps(sdp.parse("heavy"), infos) == expected


def test_heavy_does_not_pick_repo_symbol_extraction_which_is_medium():
    """The Architect's addendum: the A/B step is compute_cost medium, so the live re-run names it explicitly."""
    infos = sdp.registry_step_infos()
    assert getattr(infos["repo_symbol_extraction"], "compute_cost", "") == "medium"
    assert "repo_symbol_extraction" not in sdp.dispatched_steps(sdp.parse("heavy"), infos)
    assert "repo_symbol_extraction" in sdp.dispatched_steps(sdp.parse("repo_symbol_extraction"), infos)


def test_engine_host_reads_the_one_heavy_compute_constant():
    from resource_explorer import engine_host

    assert engine_host._HEAVY_COMPUTE is sdp.HEAVY_COMPUTE


# ── C4: an unknown key warns, an unknown token is a startup error ───────────────────────────────────────────────

def test_an_unknown_step_key_is_a_startup_warning_naming_it(monkeypatch, caplog):
    cfg = _cfg(monkeypatch, PREFECT_DISPATCH_STEPS="repo_symbol_extraction,repo_no_such_step")
    with caplog.at_level(logging.INFO, logger=sdp.__name__):
        policy = sdp.log_dispatch_policy_at_startup(cfg)
    assert "repo_no_such_step" in policy.keys
    warned = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("repo_no_such_step" in r.getMessage() for r in warned)
    assert not any("repo_symbol_extraction" in r.getMessage() for r in warned)


@pytest.mark.parametrize("raw", ["heavvy", "everything", "repo_health,GPU"])
def test_an_unknown_token_is_a_startup_error(monkeypatch, raw):
    cfg = _cfg(monkeypatch, PREFECT_DISPATCH_STEPS=raw)
    with pytest.raises(sdp.DispatchPolicyError, match="unknown token"):
        sdp.log_dispatch_policy_at_startup(cfg)


def test_the_startup_line_names_the_policy_the_heavy_steps_and_the_cost(monkeypatch, caplog):
    cfg = _cfg(monkeypatch, PREFECT_DISPATCH_STEPS="repo_symbol_extraction")
    with caplog.at_level(logging.INFO, logger=sdp.__name__):
        sdp.log_dispatch_policy_at_startup(cfg)
    (line,) = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert "PREFECT_DISPATCH_STEPS=repo_symbol_extraction" in line
    for key in sdp.heavy_steps():
        assert key in line
    assert f"~{sdp.DISPATCH_OVERHEAD_SECONDS} s" in line


# ── C2: the startup summary asks the same function ──────────────────────────────────────────────────────────────

def test_the_pool_summary_names_the_steps_the_policy_dispatches(monkeypatch):
    from resource_explorer.bootstrap import prefect_pool_runs_locally_instead
    from resource_explorer.config import get_config

    monkeypatch.setattr(get_config().prefect, "route_local_steps", False)
    monkeypatch.setattr(get_config().prefect, "dispatch_steps", "repo_symbol_extraction")
    out = prefect_pool_runs_locally_instead()
    assert any("PREFECT_DISPATCH_STEPS selects: repo_symbol_extraction" in line for line in out)
    monkeypatch.setattr(get_config().prefect, "dispatch_steps", "")
    assert not any("PREFECT_DISPATCH_STEPS" in line for line in prefect_pool_runs_locally_instead())


# ── C5: the whole-definition flow dispatches per step, and records where each ACTUALLY ran ──────────────────────

def _unique_slug(prefix: str) -> str:
    """CI runs the registry on a Postgres schema shared by every test in a worker, so a fixed slug collides
    (projects_pkey) from the second test on; SQLite's per-test file hid that."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"   # "_", not "-": the registry stores a slug with "-" as "_"


def _three_step_flow(monkeypatch, dispatch_steps, run_api):
    """Run a 3-step repo definition through the real flow with surveyors faked; `run_api` stands in for the
    worker dispatch (`_run_prefect_step_api`)."""
    from resource_explorer.config import get_config
    from resource_explorer.prefect import flows
    from resource_explorer.registry import Project, ProjectRegistry
    from resource_explorer.surveyors import prefect_adapter
    from tests.test_survey_orchestrator import _patch_all_surveyors

    monkeypatch.setattr(get_config().prefect, "enabled", True)
    monkeypatch.setattr(get_config().prefect, "route_local_steps", False)
    monkeypatch.setattr(get_config().prefect, "dispatch_steps", dispatch_steps)
    monkeypatch.setattr(prefect_adapter, "_run_prefect_step_api", run_api)
    steps = ["repo_language", "repo_health", "repo_security"]
    slug = _unique_slug("fab3")
    registry = ProjectRegistry()
    registry.add(Project(slug=slug, display_name="F", github_url=f"https://github.com/t/{slug}"))
    plan = [{"step_key": k, "qualified_name": f"QN::{k}", "executes_at": "resource-explorer",
             "depends_on": [], "guarded_by": {}} for k in steps]
    _mocks, patchers = _patch_all_surveyors()
    try:
        with patch("resource_explorer.surveyors.survey_orchestrator.log_survey"):
            report = flows.re_survey_definition_flow(
                "repo", slug, plan, {}, surveyed_at="2026-11-17T12:00:00+00:00")
    finally:
        for p in patchers:
            p.stop()
    return {r["step_key"]: r for r in report}, registry.query_step_runs(slug=slug)


@pytest.mark.usefixtures("ephemeral_prefect")
def test_a_selected_step_goes_to_the_worker_and_the_rest_stay_in_process(monkeypatch):
    sent = []

    async def worker(entity_type, slug, step_name, runner_kwargs):
        sent.append(step_name)
        return {"annotations": []}, "fr-worker-1"

    report, rows = _three_step_flow(monkeypatch, "repo_language", worker)

    assert sent == ["repo_language"]
    assert report["repo_language"]["dispatch"]["where"] == sdp.WORKER
    assert report["repo_language"]["engine"] == "prefect"
    assert report["repo_language"]["flow_run_id"] == "fr-worker-1"
    for key in ("repo_health", "repo_security"):
        assert report[key]["dispatch"]["where"] == sdp.IN_PROCESS
    # The worker's own step flow writes the dispatched step's work row (TR-21); the host writes none for it.
    assert sorted(r["step_key"] for r in rows) == ["repo_health", "repo_security"]


@pytest.mark.usefixtures("ephemeral_prefect")
def test_the_default_policy_dispatches_nothing(monkeypatch):
    async def worker(*_a, **_k):  # pragma: no cover - must not be reached
        raise AssertionError("dispatched under the default policy")

    report, rows = _three_step_flow(monkeypatch, "", worker)
    assert {r["dispatch"]["where"] for r in report.values()} == {sdp.IN_PROCESS}
    assert len(rows) == 3


@pytest.mark.usefixtures("ephemeral_prefect")
def test_a_fallback_is_recorded_as_a_fallback_never_as_dispatched(monkeypatch):
    async def unreachable(*_a, **_k):
        raise RuntimeError("Prefect API unreachable: test")

    report, rows = _three_step_flow(monkeypatch, "repo_language", unreachable)

    entry = report["repo_language"]
    assert entry["status"] == "ok" and entry["engine"] == "local"
    assert entry["dispatch"]["where"].startswith(f"{sdp.IN_PROCESS} (fallback: ")
    assert "unreachable" in entry["dispatch"]["where"] and not entry["flow_run_id"]
    (row,) = [r for r in rows if r["step_key"] == "repo_language"]
    assert row["executor"] == "local" and not row["flow_run_id"]
    assert "unreachable" in (row["dispatch_failed"] or "")
    # The steps the policy kept in-process are unaffected.
    assert {r["executor"] for r in rows if r["step_key"] != "repo_language"} == {"prefect"}


# ── R1 (Architect read-back): an explicit placement in step_runs.metrics, one test per placement ────────────────

def _placements(rows, key):
    return [(r["metrics"] or {}).get("placement") for r in rows if r["step_key"] == key]


@pytest.mark.usefixtures("ephemeral_prefect")
def test_placement_in_process_is_recorded_for_a_step_the_policy_keeps_here(monkeypatch):
    async def worker(*_a, **_k):  # pragma: no cover - must not be reached
        raise AssertionError("dispatched under the default policy")

    report, rows = _three_step_flow(monkeypatch, "", worker)
    for key, entry in report.items():
        assert entry["placement"] == sdp.IN_PROCESS
        assert _placements(rows, key) == [sdp.IN_PROCESS]


@pytest.mark.usefixtures("ephemeral_prefect")
def test_placement_worker_is_recorded_by_the_workers_step_flow(monkeypatch):
    """The dispatched step's row is written by the worker's own step flow (`re_survey_flow` ->
    `_run_step_as_flow_run`), so that is where placement=worker is recorded; the host's report says it too."""
    from resource_explorer.prefect import flows
    from resource_explorer.registry import Project, ProjectRegistry
    from tests.test_survey_orchestrator import _patch_all_surveyors

    async def worker(*_a, **_k):
        return {"annotations": []}, "fr-worker-2"

    report, _rows = _three_step_flow(monkeypatch, "repo_language", worker)
    assert report["repo_language"]["placement"] == sdp.WORKER

    slug = _unique_slug("fab3w")
    registry = ProjectRegistry()
    registry.add(Project(slug=slug, display_name="W", github_url=f"https://github.com/t/{slug}"))
    _mocks, patchers = _patch_all_surveyors()
    try:
        with patch("resource_explorer.surveyors.survey_orchestrator.log_survey"):
            flows._run_step_as_flow_run(f"fr-{slug}", "repo", slug, "repo_language",
                                        {"surveyed_at": "2026-11-17T12:00:00+00:00"}, keep_result=False)
    finally:
        for p in patchers:
            p.stop()
    rows = registry.query_step_runs(slug=slug)
    assert _placements(rows, "repo_language") == [sdp.WORKER]
    assert {(r["executor"], r["flow_run_id"]) for r in rows} == {("prefect", f"fr-{slug}")}


@pytest.mark.usefixtures("ephemeral_prefect")
def test_placement_fallback_is_recorded_with_its_reason(monkeypatch):
    async def unreachable(*_a, **_k):
        raise RuntimeError("Prefect API unreachable: test")

    report, rows = _three_step_flow(monkeypatch, "repo_language", unreachable)
    (where,) = _placements(rows, "repo_language")
    assert where.startswith(f"{sdp.IN_PROCESS} (fallback: ") and "unreachable" in where
    assert report["repo_language"]["placement"] == where


def test_prefect_status_shows_recent_placements():
    from resource_explorer.web.routes import prefect_status

    rows = [{"step_key": "repo_a", "slug": "s", "surveyed_at": "t2", "executor": "prefect",
             "flow_run_id": "fr", "metrics": {"placement": sdp.WORKER}},
            {"step_key": "repo_b", "slug": "s", "surveyed_at": "t1", "executor": "local",
             "flow_run_id": "", "metrics": {}}]
    registry = types.SimpleNamespace(query_step_runs=lambda limit: rows)
    assert prefect_status._recent_placements(registry) == [
        {"step_key": "repo_a", "slug": "s", "surveyed_at": "t2", "executor": "prefect", "flow_run_id": "fr",
         "placement": sdp.WORKER}]
    broken = types.SimpleNamespace(query_step_runs=lambda limit: 1 / 0)
    assert "error" in prefect_status._recent_placements(broken)


# ── (c): the key/token rule rests on "every registry key has '_', a token never does" ───────────────────────────

def test_every_registry_key_has_an_underscore_and_parses_as_a_key_never_a_token():
    infos = sdp.registry_step_infos()
    assert infos
    without = sorted(k for k in infos if "_" not in k)
    assert not without, (f"registry step keys without '_' {without}: step_dispatch_policy.parse would read them as "
                         "(unknown) tokens. Change the key/token rule before adding such a key")
    assert all("_" not in token for token in sdp.TOKENS)
    policy = sdp.parse(",".join(infos), known_keys=infos)
    assert policy.keys == frozenset(infos) and not policy.heavy and not policy.all and policy.warnings == ()
