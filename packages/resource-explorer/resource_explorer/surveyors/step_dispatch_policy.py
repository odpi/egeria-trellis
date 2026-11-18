"""Which engine runs one survey step: the ONE dispatch decision (TR-11 F-AB-3).

Background, the hedwig A/B of 2026-11-17 (TR-11): `repo_symbol_extraction`
ran its step in 4.5 s on the container worker against 7.7 s in-process, with
identical outputs, but end to end the dispatched call took ~15 s longer
(Prefect hand-off, a fresh process, reading the result back). Dispatching a
step to a worker therefore pays for itself only for steps heavy enough to
move off RE's process, so the owner chose per-step dispatch with the policy
in `.env` (option (a)), and the Architect's ruling
(RULING-TR11-FAB3-STEP-DISPATCH-POLICY-2026-11-17) fixed its shape:

* **One policy (C1).** `PREFECT_DISPATCH_STEPS` is the per-step generalisation
  of `PREFECT_ROUTE_LOCAL_STEPS`: a comma list of step keys and/or the tokens
  `heavy` and `all`. `PREFECT_ROUTE_LOCAL_STEPS=true` is a deprecated alias
  for `PREFECT_DISPATCH_STEPS=all`; when both are set and disagree,
  `PREFECT_DISPATCH_STEPS` wins and startup WARNs naming both.
* **One function (C2), `dispatch_decision`.** Both executors (the in-process
  loop's per-step `_use_prefect`, and the whole-definition flow's
  `run_planned_step_task`) and the startup summary
  (`bootstrap.prefect_pool_runs_locally_instead`) ask it, so the three can
  never disagree about where a step runs.
* **"heavy" (C3)** is `StepInfo.compute_cost in HEAVY_COMPUTE`, nothing else:
  no hard-coded step names, and fetch cost is deliberately NOT counted (a
  download-heavy step pays its fetch wherever it runs; moving it buys
  nothing). `engine_host` imports `HEAVY_COMPUTE` from here.
* **Validation (C4).** An unknown step key is a startup WARN naming it (it
  dispatches nothing); an unknown token is a startup error; "" (the default)
  is today's behaviour.

Telling a key from a token: every step key of RE's registries contains an
underscore (`repo_symbol_extraction`, `database_schema_inventory`, ...); a
token never does. So an entry without an underscore that is not `heavy` or
`all` is an unknown TOKEN (an error), and an entry with one that no registry
knows is an unknown KEY (a WARN).
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

log = logging.getLogger(__name__)

#: `StepInfo.compute_cost` values that make a step "heavy" -- the one place this is said (C3). Moved here from
#: `engine_host._HEAVY_COMPUTE`, which imports it.
HEAVY_COMPUTE = frozenset({"high"})

#: What dispatching one step to a worker costs end to end beyond the step itself, measured on hedwig 2026-11-17
#: (TR-11 A/B: 26.8 s dispatched vs 11.2 s in-process for a 4.5 s / 7.7 s step). Named in the startup line (C5).
DISPATCH_OVERHEAD_SECONDS = 15

TOKEN_ALL = "all"
TOKEN_HEAVY = "heavy"
TOKENS = frozenset({TOKEN_ALL, TOKEN_HEAVY})

#: Where a step runs, as `dispatch_decision` answers it.
WORKER = "worker"            # a Prefect flow run on the work pool (run_prefect_step's REST dispatch)
IN_PROCESS = "in-process"    # Resource Explorer's own process
#: Any other answer is the step's own `executes_at` engine (e.g. "egeria"): never a Prefect worker.

ENV_DISPATCH = "PREFECT_DISPATCH_STEPS"
ENV_ROUTE_LOCAL = "PREFECT_ROUTE_LOCAL_STEPS"


class DispatchPolicyError(ValueError):
    """`PREFECT_DISPATCH_STEPS` holds a token this policy does not know (C4: a startup error)."""


@dataclass(frozen=True)
class DispatchPolicy:
    """The resolved policy for Resource Explorer's own (`executes_at: resource-explorer`) steps."""

    keys: frozenset[str] = frozenset()
    heavy: bool = False
    all: bool = False
    #: Which setting it came from: ENV_DISPATCH, ENV_ROUTE_LOCAL (the deprecated alias), or "" (the default).
    source: str = ""
    #: Startup warnings: an unknown step key, the deprecated alias, both settings disagreeing.
    warnings: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.keys or self.heavy or self.all)

    def describe(self) -> str:
        if self.empty:
            return f'{ENV_DISPATCH}="" (default: Resource Explorer\'s own steps run in-process)'
        if self.all:
            words = f"{ENV_DISPATCH}=all"
        else:
            parts = sorted(self.keys) + ([TOKEN_HEAVY] if self.heavy else [])
            words = f"{ENV_DISPATCH}={','.join(parts)}"
        if self.source == ENV_ROUTE_LOCAL:
            words += f" (from the deprecated {ENV_ROUTE_LOCAL}=true)"
        return words


def parse(raw: str, known_keys: Iterable[str] | None = None) -> DispatchPolicy:
    """`PREFECT_DISPATCH_STEPS`'s value as a policy. Raises DispatchPolicyError on an unknown token. With
    `known_keys`, a key none of them matches is a warning on the result (it dispatches nothing)."""
    known = None if known_keys is None else frozenset(known_keys)
    keys: set[str] = set()
    heavy = every = False
    warnings: list[str] = []
    for entry in (e.strip() for e in (raw or "").split(",")):
        if not entry:
            continue
        token = entry.lower()
        if token == TOKEN_ALL:
            every = True
        elif token == TOKEN_HEAVY:
            heavy = True
        elif "_" not in entry:
            raise DispatchPolicyError(
                f"{ENV_DISPATCH}={raw!r}: unknown token {entry!r}. It takes step keys (which all contain an "
                f"underscore, e.g. repo_symbol_extraction) and the tokens {TOKEN_HEAVY!r} and {TOKEN_ALL!r}")
        else:
            keys.add(entry)
            if known is not None and entry not in known:
                warnings.append(f"{ENV_DISPATCH} names {entry!r}, which is not a step key of any step registry; "
                                "it dispatches nothing")
    return DispatchPolicy(keys=frozenset(keys), heavy=heavy, all=every,
                          source=ENV_DISPATCH if (keys or heavy or every) else "", warnings=tuple(warnings))


def resolve(prefect_cfg, known_keys: Iterable[str] | None = None) -> DispatchPolicy:
    """The effective policy from a PrefectConfig: `PREFECT_DISPATCH_STEPS`, else the deprecated
    `PREFECT_ROUTE_LOCAL_STEPS=true` alias for `all`, else the default (nothing dispatched)."""
    raw = getattr(prefect_cfg, "dispatch_steps", "")
    raw = raw if isinstance(raw, str) else ""      # a stub config without the field reads as unset
    route = bool(getattr(prefect_cfg, "route_local_steps", False))
    # Explicitly set in the environment/.env (pydantic records it), or set true by whoever built the config.
    route_set = route or "route_local_steps" in (getattr(prefect_cfg, "model_fields_set", None) or set())
    policy = parse(raw, known_keys)
    warnings = list(policy.warnings)
    if raw.strip():
        if route_set and route != policy.all:
            warnings.append(
                f"{ENV_DISPATCH}={raw!r} and {ENV_ROUTE_LOCAL}={str(route).lower()} disagree; {ENV_DISPATCH} wins. "
                f"{ENV_ROUTE_LOCAL} is deprecated: remove it")
        elif route_set:
            warnings.append(f"{ENV_ROUTE_LOCAL} is deprecated and redundant beside {ENV_DISPATCH}: remove it")
        return DispatchPolicy(policy.keys, policy.heavy, policy.all, ENV_DISPATCH, tuple(warnings))
    if route:
        warnings.append(f"{ENV_ROUTE_LOCAL}=true is deprecated: it means {ENV_DISPATCH}=all; set that instead")
        return DispatchPolicy(all=True, source=ENV_ROUTE_LOCAL, warnings=tuple(warnings))
    return DispatchPolicy(warnings=tuple(warnings))


def dispatch_decision(step_key: str, executes_at: str, definition, policy: DispatchPolicy) -> tuple[str, str]:
    """(where, why) for one step. `definition` is the step's StepInfo (its `compute_cost`), or None.

    1. executes_at egeria / egeria-adaptive (or any other engine): never a Prefect worker;
    2. executes_at prefect (and a PREFECT_ONLY_STEPS entry, which has no in-process implementation): always;
    3. executes_at resource-explorer: the worker if the policy lists the key, or `heavy` matches its
       compute_cost, or `all`; otherwise in-process.

    This is the policy only. A caller's per-run `engine_override` and whether Prefect is enabled/reachable are
    the caller's to apply; where the step then ACTUALLY ran is what its step_runs row records (C5).
    """
    from resource_explorer.surveyors.survey_definition_executor import PREFECT_ONLY_STEPS

    engine = (executes_at or "resource-explorer").strip()
    if engine == "prefect":
        return WORKER, "executes_at=prefect"
    if engine != "resource-explorer":
        return engine, f"executes_at={engine}: run by that engine, never dispatched to a Prefect worker"
    if step_key in PREFECT_ONLY_STEPS:
        return WORKER, f"{step_key} has no in-process implementation (PREFECT_ONLY_STEPS)"
    if policy.all:
        return WORKER, policy.describe()
    if step_key in policy.keys:
        return WORKER, f"listed in {ENV_DISPATCH}"
    compute = (getattr(definition, "compute_cost", "") or "") if definition is not None else ""
    if policy.heavy and compute in HEAVY_COMPUTE:
        return WORKER, f"{ENV_DISPATCH} includes heavy, and its compute_cost is {compute}"
    if policy.empty:
        return IN_PROCESS, f'{ENV_DISPATCH} is "" (default)'
    return IN_PROCESS, f"not selected by {policy.describe()}"


def registry_step_infos() -> dict[str, object]:
    """{step_key: StepInfo} for the repository and database step registries -- what the policy is checked
    against and what "heavy" resolves over."""
    from resource_explorer.surveyors.database.survey_definition_adapter import (
        DATABASE_STEP_REGISTRY,
    )
    from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

    return {**dict(DATABASE_STEP_REGISTRY), **dict(STEP_REGISTRY)}


def current_policy(*, check_keys: bool = False) -> DispatchPolicy:
    """The policy the running configuration resolves to."""
    from resource_explorer.config import get_config

    return resolve(get_config().prefect, registry_step_infos() if check_keys else None)


def dispatched_steps(policy: DispatchPolicy, infos: dict[str, object] | None = None) -> list[str]:
    """The registry steps (as `executes_at: resource-explorer`) this policy sends to a worker, sorted."""
    infos = registry_step_infos() if infos is None else infos
    return sorted(k for k, info in infos.items()
                  if dispatch_decision(k, "resource-explorer", info, policy)[0] == WORKER)


def heavy_steps(infos: dict[str, object] | None = None) -> list[str]:
    """The registry steps `heavy` resolves to today: compute_cost in HEAVY_COMPUTE."""
    infos = registry_step_infos() if infos is None else infos
    return sorted(k for k, info in infos.items() if (getattr(info, "compute_cost", "") or "") in HEAVY_COMPUTE)


def log_dispatch_policy_at_startup(prefect_cfg=None) -> DispatchPolicy:
    """C4/C5: validate the policy and say what it does. Raises DispatchPolicyError on an unknown token, so a
    mistyped policy stops startup rather than silently dispatching nothing; WARNs for an unknown step key,
    the deprecated alias and a disagreement; then one INFO line naming the policy, the heavy steps it
    resolved, the steps it dispatches and their cost."""
    if prefect_cfg is None:
        from resource_explorer.config import get_config

        prefect_cfg = get_config().prefect
    infos = registry_step_infos()
    policy = resolve(prefect_cfg, infos)
    for warning in policy.warnings:
        log.warning("Prefect step dispatch: %s", warning)
    sent = dispatched_steps(policy, infos)
    log.info(
        "Prefect step dispatch: %s; heavy (compute_cost in %s) resolves to [%s]; Resource Explorer steps sent to "
        "a worker: [%s]. Each dispatched step costs ~%d s of Prefect hand-off on top of the step itself "
        "(TR-11 A/B, 2026-11-17); executes_at=prefect steps always go to the worker",
        policy.describe(), sorted(HEAVY_COMPUTE), ", ".join(heavy_steps(infos)),
        "all" if policy.all else ", ".join(sent), DISPATCH_OVERHEAD_SECONDS)
    return policy
