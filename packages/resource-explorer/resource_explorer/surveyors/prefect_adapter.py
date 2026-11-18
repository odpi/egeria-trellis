"""Prefect Client Adapter — dispatches flow runs to the Prefect API or runs them locally."""
from __future__ import annotations

import os

# Belt-and-braces guard against the ephemeral-server leak found 2026-09-04
# (see PrefectConfig.enabled's docstring in config.py): 13 orphaned
# `prefect.server.api.server:create_app` subprocess servers, days old,
# reparented to launchd. Root cause is Prefect's own client, not RE's
# fallback logic — `prefect.client.get_client()` starts an ephemeral
# in-process subprocess server whenever PREFECT_SERVER_EPHEMERAL_ENABLED is
# true and no PREFECT_API_URL is reachable, and nothing shuts that
# subprocess down. Prefect's shipped default profile (profiles.toml,
# `active = "ephemeral"`) sets PREFECT_SERVER_EPHEMERAL_ENABLED=true unless
# an operator's own ~/.prefect/profiles.toml overrides it — so this must be
# forced in-process, not just left to PrefectConfig.enabled defaulting to
# False.
#
# Verified against the installed Prefect version (3.8.1):
# prefect/settings/models/server/ephemeral.py — field `enabled`, env alias
# `PREFECT_SERVER_EPHEMERAL_ENABLED` (also accepts the older
# `PREFECT_SERVER_ALLOW_EPHEMERAL_MODE` spelling) — defaults to False in the
# settings model itself, but the shipped "ephemeral" profile overrides that
# default via profiles.toml, and env vars take priority over profile
# settings in Prefect's settings-source order (see
# prefect/settings/base.py's settings_customise_sources — env sources are
# checked before ProfileSettingsTomlLoader). setdefault() so an operator who
# deliberately wants ephemeral-server behavior can still override it via
# their own environment.
#
# Must run before `import prefect` (below) — Prefect reads its settings at
# import/first-use time.
os.environ.setdefault("PREFECT_SERVER_EPHEMERAL_ENABLED", "false")

import asyncio
import json
import re
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from prefect.client.orchestration import PrefectClient
from resource_explorer.config import DEFAULT_PREFECT_STEP_TIMEOUT_SECONDS, get_config
from resource_explorer.credential_handoff import assert_no_credentials
from resource_explorer.prefect.flows import run_surveyor_step_task

log = logging.getLogger(__name__)


def re_prefect_client() -> PrefectClient:
    """The one place `resource_explorer` constructs a Prefect client.

    Every module that needs to talk to Prefect's REST API must call this
    instead of `prefect.client.get_client()` / `prefect.client.orchestration.
    get_client()` directly — ratcheted by
    `tests/test_no_ambient_prefect_client.py`.

    Why this exists (found live, 2026-09-28): `get_client()`, on the
    installed Prefect version (3.8.1), takes no `api=` argument at all —
    it resolves the server address itself, from `PREFECT_API_URL.value()`,
    which reads `prefect.context`'s own settings machinery. That is exactly
    the ambient/frozen-at-import mechanism `tests/conftest.py`'s
    `ephemeral_prefect` fixture already warns about for a DIFFERENT reason
    (an env var set after Prefect's first import cannot reach it) — but the
    practical failure mode observed here needed no import-ordering edge
    case to trigger: a live web-server process had `PREFECT_API_URL`
    correctly set in its OS environment, and `get_client()` still raised
    `ValueError: No Prefect API URL provided`, because nothing in this
    process had ever pointed Prefect's OWN settings resolution at it — only
    RE's own `config.prefect.api_url` (`resource_explorer/config.py`) had
    it, and `get_client()` never reads that.

    `run_prefect_step`'s broad `except Exception` (deliberately broad — see
    its own docstring) caught that `ValueError` exactly like a genuinely
    unreachable server and fell back to running the step locally — while
    `step_runs.executor` was already written as `'prefect'`, entered before
    dispatch even attempted (see `survey_definition_executor.py`). Every
    `step_runs` row labelled `executor='prefect'` from a process that never
    had this fixed was therefore not trustworthy evidence that anything ran
    through Prefect at all.

    `PrefectClient` itself (unlike the `get_client()` helper wrapped around
    it) takes `api` as a required positional argument and does nothing with
    ambient settings for it — so this reads RE's own configured URL and
    hands it straight to the constructor, with no dependency on Prefect's
    settings context having seen it at any point.
    """
    config = get_config()
    return PrefectClient(config.prefect.api_url)


async def _check_prefect_reachable_async() -> tuple[bool, str]:
    """(reachable, detail) — never raises. `detail` is the resolved API URL
    on success, or the exception text on failure.

    `PrefectClient.api_healthcheck()` does not raise when the server is down: it
    RETURNS the exception (None on success). Until 2026-11-09 this awaited it and
    ignored the result, so with no server at all it reported reachable, logged
    "Prefect API reachable" at every startup, and let the dispatch gates
    (`run_prefect_step`'s in-process path, the whole-definition gate in
    `survey_definition_executor`) attempt Prefect against nothing."""
    config = get_config()
    try:
        async with re_prefect_client() as client:
            failure = await client.api_healthcheck()
    except Exception as exc:
        return False, str(exc)
    if failure is not None:
        return False, healthcheck_failure_words(failure)
    return True, config.prefect.api_url


def healthcheck_failure_words(failure: BaseException) -> str:
    """The text for an `api_healthcheck()` failure. httpx's ConnectError text can
    be empty, so fall back to the exception's class name rather than say nothing."""
    return str(failure) or type(failure).__name__


async def alog_prefect_reachability_at_startup() -> None:
    """Async twin of `log_prefect_reachability_at_startup`, for a caller
    that already owns a running event loop (the web role's FastAPI
    `lifespan`) and cannot call `asyncio.run` itself. Same behaviour,
    same log lines — see that function's docstring for why this exists at
    all."""
    config = get_config()
    if not config.prefect.enabled:
        log.info("Prefect dispatch disabled (PREFECT_ENABLED=false) — startup check skipped")
        return
    try:
        ok, detail = await _check_prefect_reachable_async()
    except Exception as exc:  # pragma: no cover - defensive, see docstring
        log.warning("Prefect startup reachability check itself failed: %s", exc)
        return
    if ok:
        log.info("Prefect API reachable at %s", detail)
    else:
        log.warning(
            "Prefect API NOT reachable at %s (%s) — steps declaring "
            "executes_at: prefect will silently fall back to local "
            "execution until this is fixed; step_runs.executor will "
            "correctly say 'local' with a dispatch_failed reason (see design note "
            "PREFECT-DISPATCH-HONESTY-IMPLEMENTED.md), "
            "but nothing will actually run through Prefect",
            config.prefect.api_url, detail,
        )


def check_prefect_reachable_sync() -> tuple[bool, str]:
    """Synchronous (reachable, detail) wrapper around
    `_check_prefect_reachable_async`, for the per-run whole-definition
    default gate (2026-09-28, PREFECT-DEFAULT-WHOLE-DEFINITION-IMPLEMENTED.md):
    `SurveyDefinitionExecutor._execute` calls this once, before deciding
    whether to attempt `_run_via_prefect`, so a default (`engine_override is
    None`) run that has no Prefect server to reach skips straight to the
    local loop with an honest reason recorded — never a silent whole-
    definition fallback like the one `run_prefect_step`'s own per-step
    fallback already avoids (see PREFECT-DISPATCH-HONESTY-IMPLEMENTED.md).

    Same nested-event-loop handling as `run_prefect_step`: hands the
    coroutine to the shared bounded pool (`concurrency.run_sync`) when
    already inside a running loop (a FastAPI request thread), otherwise
    calls `asyncio.run` directly. Never raises — an exception here answers
    "not reachable", same as `_check_prefect_reachable_async` itself.
    """
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            from resource_explorer.concurrency import run_sync

            return run_sync(lambda: asyncio.run(_check_prefect_reachable_async()))
        return asyncio.run(_check_prefect_reachable_async())
    except Exception as exc:  # pragma: no cover - defensive, see docstring
        return False, str(exc)


def log_prefect_reachability_at_startup() -> None:
    """A startup check for the web/worker roles: log, on the first page of
    the process's own log, whether the Prefect server RE is actually
    configured to use is reachable — not merely whether `prefect.enabled`
    is true.

    Exists because the 2026-09-28 silent-dispatch bug (see
    `re_prefect_client`'s own docstring and
    `PREFECT-DISPATCH-HONESTY-IMPLEMENTED.md`) was
    invisible on the log's first page: nothing failed loudly at process
    start, `step_runs.executor` still said `'prefect'`, and the only trace
    was a per-step WARNING inside `run_prefect_step`'s fallback, discovered
    by tracing a traceback back through two modules. A wrong or unreachable
    `PREFECT_API_URL` should be visible the moment the process starts,
    the same way the draft-zone/private-zone bootstrap
    (`worker.py::_ensure_draft_zone`) already logs its own outcome —
    not something that requires reasoning backward from a `step_runs`
    row months later.

    Skips the check entirely when `prefect.enabled` is False: an
    intentionally Prefect-less deployment (CLAUDE.md's own documented
    fallback) should not log a warning about a server nobody asked it to
    reach.

    Never raises — a Prefect outage at startup must not block the web or
    worker role from serving; `run_prefect_step`'s own per-step fallback
    already handles that case, this only makes it visible sooner.

    Synchronous wrapper (`asyncio.run`) around `alog_prefect_reachability_
    at_startup` for a caller with no event loop of its own (`worker.py`'s
    `run_worker`, called from a plain thread) — `web/app.py`'s async
    `lifespan` calls the async version directly instead, since it already
    owns a running loop and `asyncio.run` cannot nest inside one.
    """
    asyncio.run(alog_prefect_reachability_at_startup())

# `import nest_asyncio` + `nest_asyncio.apply()` used to sit here, described as
# "enable nested event loops if running inside another async loop (e.g.
# Uvicorn/FastAPI)". Removed 2026-09-04 with the shared-pool refactor, on two
# grounds:
#
# 1. It is not what makes this module work. Every sync/async bridge in RE now
#    hands the coroutine to a thread with its own fresh loop
#    (resource_explorer/concurrency.py), which needs no loop re-entrancy at all.
# 2. It was redundant regardless. pyegeria then declared nest-asyncio as its own
#    dependency and applied it at package import, so the global patch was in
#    effect for any process that touched pyegeria whether or not this module
#    was imported. Since pyegeria 6.2.16 (egeria-python #513) pyegeria neither
#    declares nor applies it, and nothing in RE's process is patched.
#
# The cross-loop hazard behind the 2026-09-03/04 incident lives in pyegeria's
# async-client construction and is NOT claimed fixed by this (see
# docs/process-model.md §1.3); this removes RE's own redundant global patch,
# nothing more.


class PrefectRunOutcome(RuntimeError):
    """A step's flow run EXISTS and this is its outcome; nothing may run the step locally after it (TR-20, Architect
    ruling 2026-11-15, RULING-PREFECT-NO-LOCAL-FALLBACK-AND-RESULT-TRANSPORT-2026-11-15 on laz).

    A local fallback is allowed only BEFORE create_flow_run has returned an id. From then on the step's outcome is the
    flow run's, whatever goes wrong while waiting for it or reading it: re-running it locally is a second execution
    (found on hedwig 2026-11-15: a COMPLETED container run whose result could not be read was run again in RE's
    process, two step_runs rows and two downloads). `status` is one of:

      - "failed": the run reached FAILED or CRASHED (`state` says which, `detail` its message);
      - "cancelled": the run was cancelled (PrefectFlowRunCancelled);
      - "result_unreadable": the run COMPLETED but its result could not be read;
      - "mismatch": the run COMPLETED but its summary disagrees with its flow_run_results row (TR-21);
      - "unknown": the wait timed out (PrefectFlowRunTimeout) or the run could not be read: a reconciler or operator
        reads the flow run later.

    A retry is an operator's or Prefect's, never a silent local run."""

    status = "unknown"

    def __init__(self, message: str, flow_run_id: str = "", *, status: str | None = None, state: str = "") -> None:
        super().__init__(message)
        self.flow_run_id = flow_run_id
        if status:
            self.status = status
        self.state = state


class PrefectFlowRunFailed(PrefectRunOutcome):
    """The flow run reached FAILED or CRASHED."""

    status = "failed"


class PrefectResultUnreadable(PrefectRunOutcome):
    """The flow run COMPLETED, but its result could not be read here."""

    status = "result_unreadable"


class PrefectFlowRunCancelled(PrefectRunOutcome):
    """A flow run was cancelled through Prefect (e.g. the Admin "⚡ Prefect"
    panel's Cancel button — see web/routes/prefect_status.py). Deliberately
    NOT caught by run_prefect_step's fallback-to-local except block: falling
    back would re-run the step's work anyway, just locally and outside
    Prefect's supervision — silently defeating the entire reason someone
    clicked Cancel. Found live 2026-08-26 testing the cancel endpoint: a
    cancelled run's RuntimeError was swallowed like any other API failure,
    the fallback ran the step again, and it just... finished — cancel had
    no actual effect. Every other failure (unreachable server, a real bug)
    should still fall back; only a genuine user-initiated cancellation
    should not.

    Since TR-20 (2026-11-15) the rule is wider: no outcome of a run that exists falls back (PrefectRunOutcome)."""

    status = "cancelled"


class PrefectFlowRunTimeout(PrefectRunOutcome):
    """A dispatched flow run did not reach a terminal state before the
    configured deadline (`PrefectConfig.step_timeout_seconds`). Carries the
    flow run id so a person can find it in Prefect.

    Like `PrefectFlowRunCancelled`, deliberately NOT caught by
    `run_prefect_step`'s fall-back-to-local branch: the run may still be live
    (cancel is best-effort), and re-running the step's work locally would
    duplicate it. The caller records an honest error step instead.
    Found 2026-10-02: the poll loop had no deadline, so a pool with no online
    worker left the run Scheduled forever and hung the survey thread."""

    status = "unknown"


def _step_timeout_seconds() -> float:
    cfg = getattr(get_config(), "prefect", None)
    value = getattr(cfg, "step_timeout_seconds", None)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return DEFAULT_PREFECT_STEP_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_PREFECT_STEP_TIMEOUT_SECONDS


def _is_online(worker: Any) -> bool:
    status = getattr(worker, "status", None)
    return str(getattr(status, "value", status) or "").upper() == "ONLINE"


# ── version parity (DESIGN-PREFECT-ROLE-AND-RE-WORKER-2026-11-13 §1.6) ─────────────────────────────
#: The deployment every worker without a commit of its own serves (the host worker on laz).
DEFAULT_DEPLOYMENT = "RE Survey Flow/re-survey-step-deployment"
#: Workers already warned about (no advertised commit): once per worker per process.
_PARITY_UNCHECKED_WARNED: set[str] = set()
_WORKER_SHA = re.compile(r"^re-worker-([0-9a-f]{12})(?:-|$)")
#: Resource Explorer's own worker names: the container worker (`re-worker-<sha12>-<host>`) and the host worker
#: (`re-host-worker-<host>`). A generic `ProcessWorker <uuid>` cannot be told apart from another product's.
_RE_WORKER = re.compile(r"^re-(?:host-)?worker-")


def sha_deployment(sha12: str) -> str:
    """The deployment a container worker on commit `sha12` creates for itself at start (entrypoint role
    `prefect-worker`): its flows load from the image's own source and land on its own work queue."""
    return f"re-survey-step-{sha12}"


def sha_work_queue(sha12: str) -> str:
    return f"re-{sha12}"


#: The deployments a worker creates for its own commit, and nothing else: prune touches only these.
_COMMIT_DEPLOYMENT = re.compile(r"^re-survey-step-[0-9a-f]{12}$")
#: A deployment with a flow run in one of these states is in use: never pruned.
_ACTIVE_RUN_STATES = ("SCHEDULED", "PENDING", "RUNNING", "PAUSED", "CANCELLING")


async def prune_commit_deployments(keep: int, pool: str) -> dict[str, list[str]]:
    """Delete RE's per-commit deployments (`re-survey-step-<sha12>`, RE's flow, on `pool`) beyond the newest
    `keep` by creation time (Architect ruling 2026-11-13, READBACK-re-launchd-worker-8f5af544 §D).

    Never deleted: the default deployment, any other flow's or pool's, the newest `keep`, any with a flow run
    that is scheduled, pending, running, paused or cancelling, and any whose commit has an ONLINE worker
    (`re-worker-<sha12>-*`): an idle worker on an older commit keeps the deployment it serves (read-back of
    6888a58c). Returns {"kept", "deleted", "skipped_active", "skipped_online_worker"}, by name; the caller logs
    each deleted name. A standing Prefect write: workers run it after their own deploy."""
    from prefect.client.schemas.filters import (
        DeploymentFilter, DeploymentFilterId, FlowFilter, FlowFilterName, FlowRunFilter, FlowRunFilterState,
        FlowRunFilterStateType,
    )
    from prefect.client.schemas.objects import StateType

    if keep < 1:
        raise ValueError("keep must be at least 1: a worker's own deployment is never pruned")
    flow_name = DEFAULT_DEPLOYMENT.split("/", 1)[0]
    out: dict[str, list[str]] = {"kept": [], "deleted": [], "skipped_active": [], "skipped_online_worker": []}
    async with re_prefect_client() as client:
        served = {worker_sha(w) for w in await client.read_workers_for_work_pool(pool) if _is_online(w)} - {""}
        deployments = await client.read_deployments(flow_filter=FlowFilter(name=FlowFilterName(any_=[flow_name])))
        ours = [d for d in deployments
                if _COMMIT_DEPLOYMENT.match(str(getattr(d, "name", ""))) and getattr(d, "work_pool_name", None) == pool]
        ours.sort(key=lambda d: str(getattr(d, "created", "") or ""), reverse=True)
        out["kept"] = [d.name for d in ours[:keep]]
        active = FlowRunFilter(state=FlowRunFilterState(type=FlowRunFilterStateType(
            any_=[StateType[s] for s in _ACTIVE_RUN_STATES])))
        for d in ours[keep:]:
            if d.name.rsplit("-", 1)[-1] in served:
                out["skipped_online_worker"].append(d.name)
                continue
            runs = await client.read_flow_runs(
                deployment_filter=DeploymentFilter(id=DeploymentFilterId(any_=[d.id])),
                flow_run_filter=active, limit=1)
            if runs:
                out["skipped_active"].append(d.name)
                continue
            await client.delete_deployment(d.id)
            out["deleted"].append(d.name)
    return out


def worker_sha(worker: Any) -> str:
    """The commit a worker advertises in its name (`re-worker-<sha12>-<host>`), or "" for one that
    advertises none (a host worker, which runs the checkout's own code)."""
    name = getattr(worker, "name", "")
    m = _WORKER_SHA.match(name) if isinstance(name, str) else None
    return m.group(1) if m else ""


@dataclass
class DispatchChoice:
    """Where a dispatched step would go on `pool`, or why it would not go (`refused`, which names the configured
    pool, the default deployment's pool and the online commits). One decision for dispatch and for the pool
    banner (`resolve_dispatch`), so the banner never says a worker is live for a step that would run locally."""

    pool: str
    deployment: Any = None
    refused: str = ""
    default_pool: str | None = None
    online_commits: list[str] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)
    #: True when `deployment` is the default one (a worker that advertises no commit): parity is not checked.
    via_default: bool = False
    #: Resource Explorer's own workers ONLINE on another pool than `pool`, as "<worker> on pool <name>": a worker
    #: started with a --pool other than the web app's PREFECT_WORK_POOL, which never gets a step. Only read when
    #: asked (`look_elsewhere`); None when not read, [] when read and none found.
    elsewhere: list[str] | None = None
    #: Why `elsewhere` could not be read, or "".
    elsewhere_error: str = ""

    @property
    def deployment_name(self) -> str:
        return str(getattr(self.deployment, "name", "") or "") if self.deployment is not None else ""


async def _read_deployment_or_none(client: Any, name: str) -> tuple[Any, str]:
    """(deployment, "") or (None, the reason it could not be read). Read only."""
    try:
        return await client.read_deployment_by_name(name), ""
    except Exception as e:  # noqa: BLE001 - every failure is a refusal reason, returned to the caller
        return None, f"{type(e).__name__}: {e}"


async def _re_workers_elsewhere(client: Any, pool: str) -> list[str]:
    """Resource Explorer's own workers (`_RE_WORKER` names) ONLINE on any work pool other than `pool`. Read only."""
    found: list[str] = []
    for wp in await client.read_work_pools():
        name = str(getattr(wp, "name", "") or "")
        if not name or name == pool:
            continue
        for w in await client.read_workers_for_work_pool(name):
            wname = str(getattr(w, "name", "") or "")
            if _is_online(w) and _RE_WORKER.match(wname):
                found.append(f"{wname} on pool {name}")
    return sorted(found)


async def resolve_dispatch(client: Any, pool: str, online: list[Any] | None = None, *,
                           look_elsewhere: bool = False) -> DispatchChoice:
    """The deployment a step's flow run goes to on the CONFIGURED pool `pool`, or the refusal. Read only.

    The configured pool (`config.prefect.work_pool`) is authoritative (Architect ruling 2026-11-14, §RE-RULING
    of RULING-RE-PREFECT-AB-ENGINE-2026-11-13): workers are looked up there and nowhere else, and a deployment
    on any other pool is refused by name, never dispatched to. Before this, the pool came from the default
    deployment, so on hedwig (default deployment left on default-agent-pool from 11-09) the live container
    worker on resource-explorer-pool was never seen and every step ran locally.

      - an ONLINE worker on this RE's commit: its deployment `re-survey-step-<sha12>`, which must be on `pool`;
      - else an ONLINE worker that advertises no commit: the default deployment, only when it is on `pool`;
      - else ONLINE workers that all run other code: refused, naming the commits (§1.6, no silent drift);
      - else no ONLINE worker on `pool` (PREFECT-POLL-TIMEOUT: a run would sit Scheduled forever).

    `online` lets a caller pass the workers it already judged live (bootstrap's heartbeat check); by default the
    pool's workers with status ONLINE are read here.

    `look_elsewhere` (bootstrap's check, not each dispatch) also lists Resource Explorer's own workers ONLINE on
    any other pool (`elsewhere`): a worker whose --pool is not the web app's PREFECT_WORK_POOL gets no step, and
    re-config-parity.sh ignores PREFECT_WORK_POOL by design, so this is where that mismatch is caught (Architect,
    2026-11-14). It never changes the decision; a failure to read is `elsewhere_error`, not a refusal.
    """
    from resource_explorer.build_info import re_sha_short

    choice = DispatchChoice(pool=pool)
    if look_elsewhere:
        try:
            choice.elsewhere = await _re_workers_elsewhere(client, pool)
        except Exception as e:  # noqa: BLE001 - said on the status (elsewhere_error), never a silent "none"
            choice.elsewhere_error = f"{type(e).__name__}: {e}"
    if online is None:
        online = [w for w in await client.read_workers_for_work_pool(pool) if _is_online(w)]
    own = re_sha_short()
    choice.online_commits = sorted({worker_sha(w) for w in online} - {""})
    choice.unchecked = sorted(str(getattr(w, "name", "") or "?") for w in online if not worker_sha(w))
    default, default_err = await _read_deployment_or_none(client, DEFAULT_DEPLOYMENT)
    if default is not None:
        choice.default_pool = getattr(default, "work_pool_name", None) or None

    def refuse(reason: str) -> DispatchChoice:
        where = (f"on pool {choice.default_pool}" if choice.default_pool
                 else ("on no pool" if default is not None else "missing"))
        commits = ", ".join(choice.online_commits + [f"none advertised by {n}" for n in choice.unchecked])
        choice.refused = (f"{reason} (Resource Explorer is configured for pool {pool}; the default deployment "
                          f"'{DEFAULT_DEPLOYMENT}' is {where}; online commits: {commits or 'none'})")
        return choice

    if own and own in choice.online_commits:
        name = f"RE Survey Flow/{sha_deployment(own)}"
        deployment, err = await _read_deployment_or_none(client, name)
        if deployment is None:
            return refuse(f"a Prefect worker on this Resource Explorer's code ({own}) is online but its "
                          f"deployment '{name}' is missing: {err}")
        on = getattr(deployment, "work_pool_name", None)
        if on != pool:
            return refuse(f"deployment '{name}' is on pool {on} but Resource Explorer is configured for pool {pool}")
        choice.deployment = deployment
        return choice
    if choice.unchecked:
        if default is None:
            return refuse(f"Prefect deployment '{DEFAULT_DEPLOYMENT}' not found. "
                          f"Did you run 'resource-explorer prefect deploy'? Error: {default_err}")
        if choice.default_pool != pool:
            return refuse(f"default deployment '{DEFAULT_DEPLOYMENT}' is on pool {choice.default_pool} but "
                          f"Resource Explorer is configured for pool {pool}")
        choice.deployment = default
        choice.via_default = True
        return choice
    if choice.online_commits:
        return refuse(f"no online Prefect worker runs this Resource Explorer's code ({own or 'commit unknown'}); "
                      f"online workers on pool {pool} run {', '.join(choice.online_commits)}")
    return refuse(f"no online Prefect worker for pool {pool}; run `make prefect-up`")


async def _choose_deployment(client: Any, pool: str) -> Any:
    """The deployment a step's flow run goes to on the configured `pool` (`resolve_dispatch`), or RuntimeError
    with the refusal (run_prefect_step then runs the step locally and records why). Read only."""
    choice = await resolve_dispatch(client, pool)
    if choice.refused:
        raise RuntimeError(choice.refused)
    for name in choice.unchecked if choice.via_default else []:
        # Allowed, never silent (Architect ruling 2026-11-13): said once per worker, and the pool banner line
        # names it for as long as it lasts (bootstrap.PrefectPoolStatus.parity_unchecked).
        if name not in _PARITY_UNCHECKED_WARNED:
            _PARITY_UNCHECKED_WARNED.add(name)
            from resource_explorer.build_info import re_sha_short

            logging.warning("commit parity not checked: worker %s advertises none; dispatching to the "
                            "default deployment (this Resource Explorer runs %s)", name,
                            re_sha_short() or "commit unknown")
    return choice.deployment


async def _cancel_flow_run_best_effort(client: Any, flow_run_id: Any) -> str:
    """Try to cancel `flow_run_id`; return "" on success or a short failure
    description. Never raises."""
    try:
        from prefect.states import Cancelled

        await client.set_flow_run_state(flow_run_id=flow_run_id, state=Cancelled(), force=True)
        return ""
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


async def _run_prefect_step_api(
    entity_type: str,
    slug: str,
    step_name: str,
    runner_kwargs: dict[str, Any],
    *,
    timeout_seconds: float | None = None,
    poll_interval: float = 1.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> tuple[dict[str, Any], str]:
    """Trigger a flow run via Prefect REST API and wait for it to complete.

    Returns (result, flow_run_id) -- the id is what makes a step_runs row
    saying executor='prefect' checkable against Prefect's own
    POST /api/flow_runs/filter rather than merely asserted (see
    PREFECT-DISPATCH-HONESTY-IMPLEMENTED.md's live gate).

    Two guards against the hang found 2026-10-02 (see
    PREFECT-POLL-TIMEOUT-IMPLEMENTED.md): (1) before creating a flow run,
    `_choose_deployment` requires >= 1 ONLINE worker on the CONFIGURED pool
    (`config.prefect.work_pool`, never the default deployment's pool) and a
    deployment on that same pool, else raises RuntimeError naming the
    configured pool, the default deployment's pool and the online commits
    (run_prefect_step's existing policy then falls back to local and records
    the cause); (2) the poll loop has a deadline
    (`timeout_seconds`, default `PrefectConfig.step_timeout_seconds`) and on
    expiry cancels the run best-effort and raises PrefectFlowRunTimeout.
    `poll_interval`, `clock` and `sleep` are injectable so tests never sleep.
    """
    config = get_config()
    if timeout_seconds is None:
        timeout_seconds = _step_timeout_seconds()
    # R1/R2 (Architect read-back of TR-20, 2026-11-15): whether a run CAN exist, not whether this client holds an
    # id, decides between a local fallback and that run's outcome. `dispatch` tracks it across the client context, so
    # an exception from closing the client (R2) is judged by it too.
    dispatch = {"may_exist": False, "flow_run_id": "", "key": uuid.uuid4().hex}
    outcome: PrefectRunOutcome | None = None
    result: tuple[dict[str, Any], str] | None = None
    try:
        async with re_prefect_client() as client:
            # 1. Which deployment, and fail fast: a pool with no ONLINE worker (or none on this RE's code)
            # would leave the run Scheduled forever, or run it on other code. Read-only; nothing is created yet.
            deployment = await _choose_deployment(client, config.prefect.work_pool)

            # 2. Trigger the flow run — tagged so the admin "⚡ Prefect" panel can
            # group/filter flow-runs by the resource they belong to. Not the RE
            # activity_id (run_prefect_step doesn't have one in scope — threading
            # it through SurveyDefinitionExecutor.run()'s whole call chain is a
            # bigger change than this pass makes; slug+step is enough for "what's
            # running/stuck for resource X").
            parameters = {
                "entity_type": entity_type,
                "slug": slug,
                "step_name": step_name,
                "runner_kwargs": runner_kwargs,
            }
            # Prefect persists these in its own database and shows them in its UI.
            # A credential here is a bug somewhere upstream: fail loud, never send.
            assert_no_credentials(parameters, where="Prefect flow-run parameters")
            try:
                flow_run = await _create_flow_run(
                    client, deployment, parameters,
                    [f"entity_type:{entity_type}", f"slug:{slug}", f"step:{step_name}"], dispatch)
                dispatch["flow_run_id"] = str(flow_run.id)
                # From here the run EXISTS: every way out is that run's outcome, never a local run (TR-20).
                result = await _await_flow_run(client, flow_run.id, dispatch["flow_run_id"],
                                               timeout_seconds=timeout_seconds, poll_interval=poll_interval,
                                               clock=clock, sleep=sleep)
            except PrefectRunOutcome as o:
                outcome = o
            except Exception as e:  # after the create was sent: an outcome, never a reason to run locally
                if not dispatch["may_exist"]:
                    raise
                outcome = PrefectRunOutcome(
                    f"Prefect flow run {dispatch['flow_run_id'] or '(id not known)'} could not be followed "
                    f"({type(e).__name__}: {e}; idempotency key {dispatch['key']}); its outcome is unknown and the "
                    "step was NOT run locally", dispatch["flow_run_id"], status="unknown")
                outcome.__cause__ = e
    except Exception as e:
        # R2: closing the client (or anything else outside the run's own handling) failed.
        if outcome is not None:
            logging.getLogger(__name__).warning("Prefect client close failed after flow run %s's outcome: %s: %s",
                                                outcome.flow_run_id or "?", type(e).__name__, e)
            raise outcome from None
        if result is not None:
            logging.getLogger(__name__).warning("Prefect client close failed after flow run %s completed and its "
                                                "result was read; the result is used: %s: %s",
                                                result[1], type(e).__name__, e)
            return result
        if dispatch["may_exist"]:
            raise PrefectRunOutcome(
                f"Prefect dispatch failed after the create was sent ({type(e).__name__}: {e}; idempotency key "
                f"{dispatch['key']}); a run may exist, so the step was NOT run locally",
                dispatch["flow_run_id"], status="unknown") from e
        raise  # no run can exist: run_prefect_step falls back locally
    if outcome is not None:
        raise outcome
    assert result is not None
    return result


async def _create_flow_run(client: Any, deployment: Any, parameters: dict[str, Any], tags: list[str],
                           dispatch: dict[str, Any]) -> Any:
    """Create the step's flow run under this dispatch's idempotency key (R1 and R3, Architect read-backs 2026-11-15).

    A create can fail AFTER the server made the run (a read timeout, a reset on the response): the client has no id,
    but a worker will pick the run up. No exception type proves the request was never sent. Prefect's own client
    retries inside send (prefect/client/base.py: ReadTimeout, ReadError, WriteError, RemoteProtocolError and
    ConnectTimeout, and ConnectError once it has connected, which `_choose_deployment` always has), so a
    ConnectError here can follow a send that reached the server. So EVERY create failure keeps
    `dispatch["may_exist"]` True: it is retried ONCE with the same key, which returns the run the first attempt made,
    if it did, and a second failure is "unknown", never a local run. The only local fallback is a failure before
    the create is attempted (choosing the deployment)."""
    def create():
        return client.create_flow_run_from_deployment(
            deployment_id=deployment.id, parameters=parameters, tags=tags, idempotency_key=dispatch["key"])

    dispatch["may_exist"] = True
    try:
        return await create()
    except Exception as first:  # noqa: BLE001 - any create failure: the run may exist, so retry with the key
        try:
            return await create()
        except Exception as second:
            raise PrefectRunOutcome(
                f"creating the Prefect flow run failed twice ({type(first).__name__}: {first}; then "
                f"{type(second).__name__}: {second}); a run may exist under idempotency key {dispatch['key']}, so "
                "the step was NOT run locally", "", status="unknown") from second


def _results_registry():
    """The registry a dispatched step's result is read from. One seam so tests can substitute one."""
    from resource_explorer.registry import ProjectRegistry

    return ProjectRegistry()


def _read_flow_run_output(flow_run_id: str, message: str) -> dict[str, Any]:
    """A COMPLETED flow run's output, from flow_run_results by its id (TR-21). Raises PrefectResultUnreadable when no
    row exists, and a "mismatch" PrefectRunOutcome when the run's own summary (its state message) disagrees with
    the row. Either way nothing is returned, so nothing is published and no guard is produced."""
    try:
        row = _results_registry().read_flow_run_result(flow_run_id)
    except Exception as e:  # the run completed: an unreadable result, never a re-run
        raise PrefectResultUnreadable(
            f"Prefect flow run {flow_run_id} COMPLETED but its result could not be read from the registry "
            f"({type(e).__name__}: {e}); the step was NOT run locally", flow_run_id, state="COMPLETED") from e
    if row is None:
        raise PrefectResultUnreadable(
            f"Prefect flow run {flow_run_id} COMPLETED but wrote no flow_run_results row; the step was NOT run "
            "locally", flow_run_id, state="COMPLETED")
    summary: dict[str, Any] = {}
    if message:
        try:
            parsed = json.loads(message)
            summary = parsed if isinstance(parsed, dict) else {}
        except ValueError:
            summary = {}
    disagree = [k for k in ("norm_hash", "step_run_id") if k in summary and summary[k] != row.get(k)]
    if disagree:
        raise PrefectRunOutcome(
            f"Prefect flow run {flow_run_id}: its summary disagrees with its flow_run_results row on "
            f"{', '.join(disagree)} (summary {[summary[k] for k in disagree]}, row "
            f"{[row.get(k) for k in disagree]}); nothing is used from it", flow_run_id, status="mismatch",
            state="COMPLETED")

    from resource_explorer.surveyors import step_output_codec

    try:
        return step_output_codec.decode_step_output(row.get("output") or {}) or {}
    except step_output_codec.StepOutputDecodeError as e:
        raise PrefectResultUnreadable(
            f"Prefect flow run {flow_run_id} COMPLETED but its stored output does not decode ({e}); nothing is used "
            "from it and the step was NOT run locally", flow_run_id, state="COMPLETED") from e


def _state_type(state: Any) -> str:
    """A state's type name (COMPLETED, FAILED, CRASHED, ...), upper case; "" when it has none."""
    kind = getattr(state, "type", None)
    return str(getattr(kind, "value", kind) or "").upper()


async def _await_flow_run(client: Any, run_id: Any, flow_run_id: str, *, timeout_seconds: float,
                          poll_interval: float, clock: Callable[[], float],
                          sleep: Callable[[float], Awaitable[Any]]) -> tuple[dict[str, Any], str]:
    """Poll an EXISTING flow run to its outcome: (result, id), or a PrefectRunOutcome (TR-20). Never runs anything."""
    # 3. Poll for the flow run to complete, bounded by a deadline
    deadline = clock() + timeout_seconds
    while True:
        run = await client.read_flow_run(run_id)
        state = run.state
        if state.is_completed():
            # TR-21 (Architect ruling 2026-11-15): the result is the registry row the flow wrote BEFORE it returned,
            # read by flow_run_id only. Not the state's own result: that read a result file from the worker's own disk,
            # which a container worker's is not (hedwig 2026-11-15: "outside of the base path"), and before that it
            # was the 2026-08-26 bug, an unreadable result silently re-run locally. Never the kept survey snapshot
            # either: that is "latest per slug and step", and another run could have written it.
            return _read_flow_run_output(flow_run_id, getattr(state, "message", "") or ""), flow_run_id
        elif state.is_failed() or _state_type(state) == "CRASHED":
            kind = "FAILED" if state.is_failed() else "CRASHED"
            raise PrefectFlowRunFailed(
                f"Prefect flow run '{flow_run_id}' {kind.lower()}: {state.message}", flow_run_id, state=kind)
        elif state.is_cancelled():
            raise PrefectFlowRunCancelled(f"Prefect flow run '{flow_run_id}' was cancelled.", flow_run_id,
                                          state="CANCELLED")

        if clock() >= deadline:
            cancel_failure = await _cancel_flow_run_best_effort(client, run_id)
            msg = (
                f"timed out after {timeout_seconds:g} s waiting for Prefect flow run "
                f"{flow_run_id} (state {getattr(state, 'name', None) or state.type})"
            )
            msg += (
                f"; cancel attempt FAILED ({cancel_failure}) - the run may still be live"
                if cancel_failure else "; the run was cancelled"
            )
            raise PrefectFlowRunTimeout(msg, flow_run_id=flow_run_id, state=str(getattr(state, "name", "") or ""))

        await sleep(poll_interval)


def _in_process_step_flow() -> Callable[..., dict]:
    """The flow an override-credential step runs as. One seam so tests can
    substitute a fake that records every parameter it is given."""
    from resource_explorer.prefect.flows import re_survey_step_in_process_flow

    return re_survey_step_in_process_flow


def _run_step_in_process_flow(
    entity_type: str, slug: str, step_name: str, runner_kwargs: dict[str, Any],
    credential_ref: str,
) -> tuple[dict[str, Any], str]:
    """Run one step as a Prefect flow INSIDE this process, so Prefect records
    the run (state, logs, flow-run id) while the one-run credential stays in
    RE's memory: the flow is handed `credential_ref`, an opaque reference to
    it, and nothing else. A worker could not do this -- it is another process.

    Raises RuntimeError when Prefect is unreachable, so `run_prefect_step`
    degrades to its local path exactly as it does for the REST dispatch. Any failure once the flow has started is
    a PrefectFlowRunFailed (TR-20): the step already ran here, and running it again locally would run it twice.
    """
    reachable, detail = check_prefect_reachable_sync()
    if not reachable:
        raise RuntimeError(f"Prefect API unreachable: {detail}")
    parameters = {
        "entity_type": entity_type, "slug": slug, "step_name": step_name,
        "runner_kwargs": runner_kwargs, "credential_ref": credential_ref,
    }
    assert_no_credentials(parameters, where="Prefect flow-run parameters")
    flow_fn = _in_process_step_flow()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    # From here the flow runs in THIS process, as a flow run Prefect records: a failure is that run's outcome,
    # never a reason to run the step again locally (TR-20). Only the reachability check above falls back.
    try:
        if loop and loop.is_running():
            from resource_explorer.concurrency import run_sync

            # The shared pool carries the caller/daemon identity into its thread itself
            # (concurrency._CallersContextPool) and leaves the request's Egeria client scope behind
            # (pyegeria ISSUE-96); an explicit ctx.run here would bring that scope back.
            out = run_sync(lambda: flow_fn(**parameters))
        else:
            out = flow_fn(**parameters)
    except PrefectRunOutcome:
        raise
    except Exception as e:  # the in-process flow ran: an outcome, never a second local run
        raise PrefectFlowRunFailed(
            f"the in-process Prefect flow for {step_name} failed ({type(e).__name__}: {e}); the step was NOT run "
            "locally again", "", state="FAILED") from e
    return (out.get("result") or {}), str(out.get("flow_run_id") or "")


def run_prefect_step(
    entity_type: str,
    slug: str,
    step_name: str,
    runner_kwargs: dict[str, Any],
    *,
    dispatch_info: dict[str, Any] | None = None,
    credential_ref: str = "",
) -> dict[str, Any]:
    """Dispatch step execution to Prefect, via its API when enabled, else in-process.

    Who calls it (corrected 2026-11-17, TR-11 F-AB-3): the in-process loop for a
    step its `_use_prefect` routes to Prefect, and the whole-definition flow's
    `run_planned_step_task` for a step `step_dispatch_policy.dispatch_decision`
    sends to a worker. A whole-definition run (`engine_override="prefect"`, or
    the default with Prefect reachable) does NOT come through here as a whole:
    it is one flow in RE's own process, and only its dispatched steps reach this
    function. When the dispatch falls back to running locally, the rows the step
    records carry the reason in `dispatch_failed` (`step_cost_observer.
    dispatch_fallback`), so a fallback can never read as a dispatched run.

    The API branch had never executed. It began with `asyncio.get_running_loop()`,
    while a redundant `import asyncio` further down the same function turned
    `asyncio` into a closure cell (the lambda below captures it) — so that first
    line did a LOAD_DEREF on a cell nothing had stored yet and raised
    UnboundLocalError. The broad `except` read that as "API unreachable", logged a
    warning and fell through to local execution. Every Prefect step has therefore
    always run in-process, and _run_prefect_step_api was dead code, while the log
    line said only that dispatch had "failed".

    The module already imports asyncio at the top; the inner import is gone.

    `dispatch_info`, when given, is filled in-place with the truth about what
    actually happened -- {"engine": "prefect"|"local", "flow_run_id": str,
    "dispatch_failed": str} -- so a caller can record step_runs.executor
    honestly instead of baking in 'prefect' the moment it entered this
    branch, which is what let a silently-failed dispatch (see
    re_prefect_client's own docstring for the exact bug: get_client()
    reading Prefect's ambient settings instead of RE's configured URL)
    masquerade as a real Prefect run for the entire life of this
    integration. Optional and additive -- every existing caller/test that
    ignores it sees the same return value as before.
    """
    # `runner_kwargs` crosses into Prefect: it must carry no credential. The
    # stored credential is resolved by the step's own task from the registry;
    # a one-run override travels only as `credential_ref` (see
    # credential_handoff.py). Outside the try below on purpose -- the broad
    # except there would turn this into a quiet local fallback.
    assert_no_credentials(runner_kwargs, where="Prefect")
    config = get_config()

    fallback_reason = ""

    def _fill(engine: str, flow_run_id: str = "", dispatch_failed: str = "") -> None:
        if dispatch_info is not None:
            dispatch_info["engine"] = engine
            dispatch_info["flow_run_id"] = flow_run_id
            dispatch_info["dispatch_failed"] = dispatch_failed

    # If Prefect is enabled, attempt to run via the API (distributed worker execution)
    if config.prefect.enabled:
        try:
            # Check if event loop is running
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if credential_ref:
                result, flow_run_id = _run_step_in_process_flow(
                    entity_type, slug, step_name, runner_kwargs, credential_ref)
                if dispatch_info is not None:
                    # The flow ran HERE and wrote its own work row, even when Prefect gave no id: the caller must
                    # not add a host row (Architect advisory on TR-21, 2026-11-15).
                    dispatch_info["in_process_flow"] = True
            elif loop and loop.is_running():
                # Already inside an event loop (a FastAPI request thread): asyncio.run
                # cannot nest, so hand the coroutine to a worker thread with its own
                # loop. This used to construct `ThreadPoolExecutor()` with NO
                # max_workers — the only one of the six bridge sites with no cap at
                # all. It now uses the one bounded shared pool per process
                # (resource_explorer/concurrency.py).
                from resource_explorer.concurrency import run_sync

                result, flow_run_id = run_sync(
                    lambda: asyncio.run(
                        _run_prefect_step_api(entity_type, slug, step_name, runner_kwargs))
                )
            else:
                result, flow_run_id = asyncio.run(
                    _run_prefect_step_api(entity_type, slug, step_name, runner_kwargs))
            _fill("prefect", flow_run_id=flow_run_id)
            return result
        except PrefectRunOutcome as outcome:
            # TR-20: the flow run exists, so this is ITS outcome (failed, cancelled, result unreadable, unknown).
            # Never a local run: that would execute the step a second time (the 2026-08-26 cancel bug, the
            # 2026-11-15 hedwig unreadable-result double run). Recorded as engine=prefect, then re-raised so the
            # caller writes an honest error step.
            _fill("prefect", flow_run_id=outcome.flow_run_id)
            if dispatch_info is not None:
                dispatch_info["status"] = outcome.status
                dispatch_info["state"] = outcome.state
                dispatch_info["detail"] = str(outcome)
            logging.getLogger(__name__).warning("Prefect flow run %s: %s (%s); not run locally",
                                                outcome.flow_run_id or "?",
                            outcome.status, outcome)
            raise
        except Exception as e:
            # Reached only BEFORE a flow run id exists (deployment not chosen, create_flow_run raised, server
            # unreachable): every later failure is a PrefectRunOutcome above. Falling back is right here —
            # the work still gets done locally. But catching everything is what hid
            # the UnboundLocalError above for the entire life of this integration,
            # so log the exception type and traceback rather than just str(e): a
            # bug here is indistinguishable from a connection problem otherwise.
            logging.warning(
                "Prefect API dispatch failed (%s), falling back to local flow execution: %s",
                type(e).__name__, e, exc_info=True,
            )
            fallback_reason = f"{type(e).__name__}: {e}"
            _fill("local", dispatch_failed=fallback_reason)
    else:
        _fill("local", dispatch_failed="")

    # Fallback/Local Run: call the step's underlying function directly, NOT
    # through `re_survey_flow` (a Prefect `@flow`). Found 2026-09-04: invoking
    # the flow-wrapped version here still enters Prefect's flow engine, which
    # itself calls `get_client()` to record flow-run state — with no
    # PREFECT_API_URL configured, that used to succeed only because the
    # ephemeral-server fallback silently started a subprocess server (the
    # exact leak this module now guards against at import time). With that
    # guard in place, calling the `@flow` here would raise instead of running
    # locally — defeating the entire point of this fallback branch, which is
    # reached whenever Prefect is disabled OR unreachable and must keep
    # working with zero Prefect server, ephemeral or real.
    #
    # `run_surveyor_step_task.fn` is Prefect's own way to reach the
    # undecorated function (same pattern `run_planned_step_task` already uses
    # to call it from inside `re_survey_definition_flow` — see
    # prefect/flows.py) — this runs the step as a plain Python call, no
    # Prefect engine involved at all.
    local_kwargs = {"credential_ref": credential_ref} if credential_ref else {}
    from resource_explorer.surveyors import step_cost_observer

    # TR-11 F-AB-3 (C5): a row the step's own runner records while falling back says so.
    with step_cost_observer.dispatch_fallback(fallback_reason):
        return run_surveyor_step_task.fn(
            entity_type=entity_type,
            slug=slug,
            step_name=step_name,
            runner_kwargs=runner_kwargs,
            **local_kwargs,
        )
