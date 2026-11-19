"""Prefect Flow and Task definitions for Resource Explorer distributed surveying."""
from __future__ import annotations

import logging
import os
import json
from typing import Any
from prefect import flow, task
from resource_explorer.registry import ProjectRegistry
from resource_explorer.surveyors import step_cost_observer, step_dispatch_policy
from resource_explorer.surveyors.survey_definition_executor import get_adapter

log = logging.getLogger(__name__)


def _daemon_entry(fn):
    """Brief I: a flow run in a Prefect worker process has no caller; it is declared
    `Daemon(PREFECT_FLOW)` there. In RE's own process the carried identity is kept."""
    from resource_explorer.egeria_clients import DaemonReason, daemon_entry

    return daemon_entry(DaemonReason.PREFECT_FLOW)(fn)


def _one_acquisition_per_run(fn):
    """F-AB-2: a whole-definition run acquires its source tree once, by the first step that reads it, and holds it
    until the flow returns; each planned step otherwise builds its own orchestrator and fetches it again. The pool
    reaches the step tasks through the context Prefect copies into them (surveyors/run_resource_pool.py)."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        from resource_explorer.surveyors.run_resource_pool import run_resource_scope

        with run_resource_scope():
            return fn(*args, **kwargs)

    return wrapper


@task(name="Run Surveyor Step")
def run_surveyor_step_task(
    entity_type: str,
    slug: str,
    step_name: str,
    runner_kwargs: dict[str, Any],
    credential_ref: str = "",
) -> dict[str, Any]:
    """Execute a single surveyor analysis step inside a Prefect task.

    Database credentials are never a parameter of this task or of any flow
    that calls it (Prefect persists parameters). They are resolved HERE, where
    the step runs: the stored ones from the registry row, or -- for a one-run
    override running in this same process -- the one `credential_ref` points
    at (resource_explorer/credential_handoff.py). They live in local variables
    only, are kept out of log lines, and are scrubbed from the returned output
    (which a flow may persist).
    """
    from resource_explorer.credential_handoff import resolve_credentials
    from resource_explorer.secret_redaction import redacting_logs, scrub
    from resource_explorer.surveyors.survey_definition_executor import _redactable

    registry = ProjectRegistry()
    adapter = get_adapter(entity_type)
    if not adapter:
        raise ValueError(f"No resource type adapter registered for '{entity_type}'")

    entity = adapter.get_entity(registry, slug)
    if not entity:
        raise ValueError(f"Entity '{slug}' of type '{entity_type}' not found in registry")

    user, pwd = resolve_credentials(entity, credential_ref)
    secret = _redactable(pwd, "this run" if credential_ref else "")
    with redacting_logs(secret):
        try:
            out = _run_step_with_credentials(
                entity_type, adapter, entity, registry, step_name, runner_kwargs, user, pwd)
        except Exception as exc:
            if not secret:
                raise
            # A driver error can echo the password it was handed, and Prefect
            # persists a failed run's message and traceback. Re-raise it
            # scrubbed, with no chained original (`from None`).
            raise RuntimeError(f"{type(exc).__name__}: {scrub(str(exc), secret)}") from None
    return scrub(out, secret) if secret else out


def _run_step_with_credentials(entity_type, adapter, entity, registry, step_name, runner_kwargs, user, pwd):
    # Dynamic routing for pre-integrated workflow quality analyzers
    if step_name == "soda_data_quality":
        if entity_type != "database":
            raise ValueError("soda_data_quality is only supported for entity_type='database'")
        return run_soda_scan_task(
            db_type=entity.db_type,
            host=entity.host,
            port=entity.port,
            database_name=entity.database_name,
            db_user=user,
            db_password=pwd,
            sodacl_yaml=runner_kwargs.get("sodacl_yaml", ""),
        )
    elif step_name == "great_expectations_validation":
        if entity_type != "database":
            raise ValueError("great_expectations_validation is only supported for entity_type='database'")
        return run_gx_validation_task(
            db_type=entity.db_type,
            host=entity.host,
            port=entity.port,
            database_name=entity.database_name,
            db_user=user,
            db_password=pwd,
            table_name=runner_kwargs.get("table_name", ""),
            expectation_suite=runner_kwargs.get("expectation_suite", {}),
        )

    runner = adapter.re_analysis_steps.get(step_name)
    if not runner:
        raise ValueError(f"Analysis step '{step_name}' not supported for type '{entity_type}'")

    # Run the surveyor step callback. Inject the resolved credential the way
    # the in-process executor always has: only when the entity carries one
    # (repo / filesystem entities have none), and never over a value the
    # caller put in `runner_kwargs` itself.
    call_kwargs = dict(runner_kwargs)
    if user or pwd:
        call_kwargs.setdefault("db_user", user)
        call_kwargs.setdefault("db_pwd", pwd)
    result = runner(entity, registry, **call_kwargs)
    return result or {}


@task(name="Soda Core Quality Scan")
def run_soda_scan_task(
    db_type: str,
    host: str,
    port: int,
    database_name: str,
    db_user: str,
    db_password: str,
    sodacl_yaml: str,
) -> dict[str, Any]:
    """Run Soda Core data quality checks on the target PostgreSQL database."""
    from soda.scan import Scan

    # Construct the configuration dictionary for the database
    config_yaml = f"""
data_source postgres:
  type: postgres
  host: {host}
  port: {port}
  username: {db_user}
  password: {db_password}
  database: {database_name}
  schema: public
"""

    scan = Scan()
    scan.set_data_source_name("postgres")
    scan.add_configuration_yaml_str(config_yaml)
    scan.add_sodacl_yaml_str(sodacl_yaml)

    # Execute the scan
    exit_code = scan.execute()
    results = scan.get_scan_results()

    return {
        "exit_code": exit_code,
        "results": results,
        "has_failures": scan.has_check_failures(),
        "has_warnings": scan.has_check_warnings(),
    }


@task(name="Great Expectations Validation")
def run_gx_validation_task(
    db_type: str,
    host: str,
    port: int,
    database_name: str,
    db_user: str,
    db_password: str,
    table_name: str,
    expectation_suite: dict[str, Any],
) -> dict[str, Any]:
    """Run Great Expectations validation checkpoints on a specific database table."""
    import pandas as pd
    import sqlalchemy
    import great_expectations as gx

    # Build connection string
    conn_str = f"postgresql://{db_user}:{db_password}@{host}:{port}/{database_name}"
    engine = sqlalchemy.create_engine(conn_str)

    # Read a sample batch of data
    df = pd.read_sql(f"SELECT * FROM {table_name} LIMIT 10000", engine)

    # Get GX context
    context = gx.get_context(mode="ephemeral")

    # Define an expectation suite
    suite_name = f"{table_name}_suite"
    suite = context.data_assistant.create_expectation_suite(suite_name) if hasattr(context, "data_assistant") else context.add_expectation_suite(suite_name)

    # In Great Expectations, we can convert expectation suite dict representation
    # and validate the Pandas DataFrame
    from great_expectations.dataset import PandasDataset
    dataset = PandasDataset(df, expectation_suite=expectation_suite)
    results = dataset.validate()

    return {
        "success": results.success,
        "statistics": results.statistics,
        "results": results.to_json_dict(),
    }


def output_json_and_hash(output: Any) -> tuple[str, str]:
    """The canonical encoded text of a step's output and its sha256 (`norm_hash`): what flow_run_results keeps and
    what the flow's summary carries as a cross-check. Through the ONE codec both sides share (TR-21 R4,
    surveyors/step_output_codec.py): annotations encoded losslessly, any value with no lossless encoding raises
    StepOutputEncodeError, which fails the flow run."""
    from resource_explorer.surveyors.step_output_codec import encode_step_output

    return encode_step_output(output)


def _run_step_as_flow_run(flow_run_id: str, entity_type: str, slug: str, step_name: str,
                          runner_kwargs: dict[str, Any], *, credential_ref: str = "",
                          keep_result: bool,
                          placement: str = step_dispatch_policy.WORKER) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one step as the work of flow run `flow_run_id`: (output, summary). TR-21 (Architect ruling 2026-11-15):

      - exactly ONE work row in step_runs for the flow run, for every entity type: every row observed while the
        step runs carries executor=prefect and the flow run id (step_cost_observer.in_flow_run). A runner that records
        its own row (the repo orchestrator) has written it; for one that records none (database, filesystem), the
        row is written here, from the same measurement;
      - with `keep_result`, the output is kept in flow_run_results BEFORE the flow returns, so COMPLETED implies the
        row. The output is already scrubbed of any one-run secret by run_surveyor_step_task.

    The summary (step_run_id, norm_hash, counts) is the flow's state message: a cross-check, never the data.

    TR-62b: every write below, and every write the runner makes on the step path, first checks that this flow run
    has not been cancelled (surveyors/run_cancellation.py); a cancelled run raises RunCancelled and writes nothing."""
    from resource_explorer.surveyors import run_cancellation

    # TR-62a: the step's heartbeat runs for exactly as long as the step (stopped and joined on the way out).
    with _heartbeat_for(flow_run_id) as hb, \
            run_cancellation.watching(flow_run_id, _cancel_check_for(flow_run_id, hb)):
        return _run_step_guarded(flow_run_id, entity_type, slug, step_name, runner_kwargs,
                                 credential_ref=credential_ref, keep_result=keep_result, placement=placement)


def _heartbeat_for(flow_run_id: str):
    """TR-62a: a heartbeat for this flow run (surveyors/step_heartbeat.py). One seam so tests can substitute one."""
    from resource_explorer.surveyors import step_heartbeat

    return step_heartbeat.running(flow_run_id)


def _cancel_check_for(flow_run_id: str, hb=None):
    """TR-62b: the check every write on this step's path makes first: is this flow run CANCELLING or CANCELLED?
    The heartbeat's flag answers at once when a beat has already seen the cancel; otherwise the state is read
    through a sync client of its own. A failed read is logged once and answers no. No flow run id (no Prefect
    context), no check. One seam so tests can substitute a fake run."""
    from resource_explorer.surveyors import run_cancellation

    if not flow_run_id:
        return lambda: False
    warned: list = []
    flag = getattr(hb, "cancelled", None)
    return lambda: (flag is not None and flag.is_set()) or run_cancellation.prefect_run_is_cancelled(
        flow_run_id, _warned=warned)


def _run_step_guarded(flow_run_id: str, entity_type: str, slug: str, step_name: str,
                      runner_kwargs: dict[str, Any], *, credential_ref: str, keep_result: bool,
                      placement: str) -> tuple[dict[str, Any], dict[str, Any]]:
    from resource_explorer.survey_time import now_utc_iso
    from resource_explorer.surveyors.run_cancellation import raise_if_cancelled

    registry = ProjectRegistry()
    info = _step_info_for(entity_type, step_name)
    kwargs = {"credential_ref": credential_ref} if credential_ref else {}
    # R1 (TR-11 F-AB-3): a worker's step flow records placement=worker; RE's own in-process step flow, in-process.
    with step_cost_observer.in_flow_run(flow_run_id) as recorded, step_cost_observer.placement(placement):
        with step_cost_observer.observe(
            step_name, getattr(info, "fetch_cost", ""), getattr(info, "compute_cost", ""),
            entity_type=entity_type, slug=slug, job_family="survey",
        ) as observed:
            output = run_surveyor_step_task(
                entity_type=entity_type, slug=slug, step_name=step_name, runner_kwargs=runner_kwargs, **kwargs)

    def own_rows() -> list[dict]:
        return [r for r in registry.step_run_ids_for_flow_run(flow_run_id)
                if r.get("step_key") == step_name and not r.get("demanded_by")]

    # TR-62b: a run cancelled while its step ran writes nothing from here on (fbfab6f3 wrote its result row 68 s
    # after the cancel).
    raise_if_cancelled("recording the step's work row")
    runner_wrote_it = (step_name, "") in recorded
    if not runner_wrote_it and observed:
        # The runner recorded no work row of its own (database, filesystem): write the one, from the flow's own
        # measurement, still inside the flow run so it carries the id.
        obs = observed[0]
        annotations = (output or {}).get("annotations") if isinstance(output, dict) else None
        obs.annotations, obs.outcomes = step_cost_observer.describe_work(annotations)
        obs.disagreement = step_cost_observer._disagreement(obs)
        commit = output.get("survey_commit_sha") if isinstance(output, dict) else None
        surveyed_at = (runner_kwargs or {}).get("surveyed_at") or now_utc_iso()
        with step_cost_observer.in_flow_run(flow_run_id):
            if flow_run_id:
                obs.executor = obs.source = "prefect"
                obs.executor_ref = obs.flow_run_id = flow_run_id
            step_cost_observer.record(registry, slug, obs, surveyed_at, entity_type=entity_type,
                                      survey_commit_sha=commit or None)
    rows = own_rows() if flow_run_id else []
    # Encoded (and so checked) BEFORE anything is kept or returned: a value with no lossless encoding fails the flow
    # run here, loudly, with its type and key path (TR-21 R4). Never str().
    text, norm_hash = output_json_and_hash(output)
    if len(rows) > 1:
        log.error("flow run %s wrote %d work rows for %s (expected 1): %s", flow_run_id, len(rows), step_name,
                  [r.get("id") for r in rows])
    step_run_id = rows[0]["id"] if rows else None
    if keep_result:
        if not flow_run_id:
            raise RuntimeError("RE Survey Flow ran without a flow run id: its result could not be kept")
        raise_if_cancelled("writing flow_run_results")
        registry.write_flow_run_result(flow_run_id, entity_type=entity_type, slug=slug, step_key=step_name,
                                       step_run_id=step_run_id, output_json=text, norm_hash=norm_hash)
    annotations = (output or {}).get("annotations") if isinstance(output, dict) else None
    summary = {"flow_run_id": flow_run_id, "step_run_id": step_run_id, "norm_hash": norm_hash,
               "annotations": len(annotations) if isinstance(annotations, list) else 0}
    raise_if_cancelled("returning the step's outcome")
    return output or {}, summary


def _cancelled_state(exc):
    """The state a flow ends in when its run was cancelled under it (TR-62b): Cancelled, never Completed."""
    from prefect.states import Cancelled

    return Cancelled(message=f"cancelled while running; nothing was written after the cancel ({exc.where})")


@flow(name="RE Survey Flow", persist_result=False)
@_daemon_entry
def re_survey_flow(
    entity_type: str,
    slug: str,
    step_name: str,
    runner_kwargs: dict[str, Any],
):
    """One dispatched step, run on a worker. TR-21: nothing is persisted to a result file (a container worker's
    files are not RE's: found on hedwig 2026-11-15). The output goes to the registry (flow_run_results, by this flow
    run's id) and the final state's message carries only the summary RE cross-checks."""
    from prefect.runtime import flow_run as _flow_run_ctx
    from prefect.states import Completed

    from resource_explorer.surveyors.run_cancellation import RunCancelled

    try:
        _, summary = _run_step_as_flow_run(str(_flow_run_ctx.id or ""), entity_type, slug, step_name, runner_kwargs,
                                           keep_result=True)
    except RunCancelled as exc:
        # TR-62b: never COMPLETED over a cancel. The flow ends Cancelled and has kept nothing.
        return _cancelled_state(exc)
    return Completed(message=json.dumps(summary, sort_keys=True), data=summary)


@flow(name="RE Survey Step (in process)", persist_result=False)
@_daemon_entry
def re_survey_step_in_process_flow(
    entity_type: str,
    slug: str,
    step_name: str,
    runner_kwargs: dict[str, Any],
    credential_ref: str = "",
) -> dict[str, Any]:
    """One step as a flow that runs in RE's OWN process.

    Used for a step that must connect with a credential typed for one run:
    that credential lives only in this process's memory, so the flow can only
    run here, and is handed the opaque `credential_ref`, never the password.
    Prefect still records the run (state, logs, flow-run id). Returns the
    step's output and this flow run's id.
    """
    from prefect.runtime import flow_run as _flow_run_ctx

    flow_run_id = str(_flow_run_ctx.id or "")
    # In RE's own process: the output comes straight back, so nothing is kept in flow_run_results; the one work row
    # carries this flow run's id like a worker's (TR-21).
    from resource_explorer.surveyors.run_cancellation import RunCancelled

    try:
        result, _summary = _run_step_as_flow_run(flow_run_id, entity_type, slug, step_name, runner_kwargs,
                                                 credential_ref=credential_ref, keep_result=False,
                                                 placement=step_dispatch_policy.IN_PROCESS)
    except RunCancelled as exc:
        # In RE's own process a BaseException must not escape the flow: it ends Cancelled, and Prefect raises
        # CancelledRun to the caller (prefect_adapter._run_step_in_process_flow maps it to "cancelled").
        return _cancelled_state(exc)
    return {"result": result, "flow_run_id": flow_run_id}


# ── whole-survey orchestration ──────────────────────────────────────────────
#
# `re_survey_flow` above runs ONE step. It is a task wrapped in a flow, which
# is why RE ended up sequencing surveys itself: with only a per-step entry
# point, the only place a step order could live was RE's own `while` loop in
# survey_definition_executor.
#
# This flow takes the whole plan. Prefect resolves the order from the task
# dependencies rather than RE walking a list, which is the point — RE should
# delegate workflow execution, not perform it.


def _step_info_for(entity_type: str, step_key: str):
    """This step's declared cost tiers, from the SAME step registry the local
    loop reads (`SurveyDefinitionExecutor._step_info`) — looked up fresh here
    rather than threaded through Prefect's task graph, since `StepInfo` isn't
    JSON-serialisable and the plan already carries only plain dicts
    (`serialise()`). None on any failure — `step_cost_observer.observe`
    already treats a blank declared tier as nothing to disagree with."""
    try:
        from resource_explorer.surveyors.survey_definition_executor import get_adapter

        adapter = get_adapter(entity_type)
        provider = getattr(adapter, "step_registry", None)
        return (provider() or {}).get(step_key) if provider else None
    except Exception as exc:  # pragma: no cover - registry-provider guard
        log.debug("could not read step registry for %s/%s: %s", entity_type, step_key, exc)
        return None


def _record_step_cost(entity_type: str, slug: str, obs, output, surveyed_at: str) -> None:
    """§E, 2026-09-27: give a Prefect-orchestrated step the same `step_runs`
    row the local dispatch loop has always written for it
    (`SurveyDefinitionExecutor._record_cost`). Before this,
    `step_cost_observer.record` was only ever called from that module's own
    local dispatch loop, so a WHOLE definition run via `_run_via_prefect`
    produced no `step_runs` rows at all, silently — the brief's own gate
    ("the Analysis-tier `step_runs` rows must appear") would have failed on
    a SUCCESSFUL Prefect run exactly as it failed on the bare error §E
    otherwise fixes. Best-effort and never fatal: the step's real output has
    already been returned by the time this runs, so losing the cost row is
    strictly better than losing the step's actual result over an
    observability side-channel.
    """
    if not surveyed_at:
        return
    try:
        from resource_explorer.registry import ProjectRegistry

        annotations = (output or {}).get("annotations") if isinstance(output, dict) else None
        obs.annotations, obs.outcomes = step_cost_observer.describe_work(annotations)
        obs.disagreement = step_cost_observer._disagreement(obs)
        # The commit the runner's run read, when its step completed (repo runners put it on the output).
        commit = output.get("survey_commit_sha") if isinstance(output, dict) else None
        step_cost_observer.record(ProjectRegistry(), slug, obs, surveyed_at,
                                  entity_type=entity_type, survey_commit_sha=commit or None)
    except Exception as exc:  # pragma: no cover - best-effort observability only
        log.debug("could not record step cost for %s/%s: %s", slug, obs.step_key, exc)


def _current_flow_run_id() -> str | None:
    try:
        from prefect.runtime import flow_run as _flow_run_ctx

        return _flow_run_ctx.id
    except Exception:  # pragma: no cover - defensive, never fatal
        return None


@task(name="Run Planned Step")
def run_planned_step_task(
    entity_type: str,
    slug: str,
    step_key: str,
    qualified_name: str,
    runner_kwargs: dict[str, Any],
    upstream: list[dict[str, Any]],
    guarded_by: dict[str, str],
    satisfied_by_stored: dict[str, Any] | None = None,
    surveyed_at: str = "",
    credential_ref: str = "",
    added_because: str = "",
    demanded_by: str = "",
    executes_at: str = "resource-explorer",
) -> dict[str, Any]:
    """One planned step, plus the guard decision for its incoming edges.

    Where the step runs (TR-11 F-AB-3) is `step_dispatch_policy.dispatch_decision`'s answer, the same one the
    in-process loop and the startup summary use: in this process (the default), or on a Prefect worker through
    `run_prefect_step` (`_run_on_worker`) for an executes_at=prefect step or one PREFECT_DISPATCH_STEPS selects.
    The report entry's `dispatch` says which and why; a dispatch that fell back says so, never "worker".

    `upstream` is the results of everything this step depends on. Prefect
    resolves those futures before this task starts, which is what makes the
    ordering Prefect's rather than RE's.

    A conditional edge is honoured here rather than in the planner: the
    planner is pure and cannot know what guard a step emitted. A step whose
    guards are not satisfied is reported "skipped" — never "ok", which would
    make a branch not taken indistinguishable from one that ran.

    `satisfied_by_stored` (§E, 2026-09-27) is `survey_execution_plan.
    PlannedStep.satisfied_by_stored` — the preconditions this step needed
    that the planner found already met by a fresh stored result rather than
    by a producer in this run. Carried through to the report entry unchanged
    so the provenance ("schema inventory from 20:59:29") reaches the same
    place a person or the UI pane reads the rest of the step's outcome.

    `surveyed_at`, when given, is this run's shared timestamp — see
    `_record_step_cost` for why an ACTUAL step run through here otherwise
    left no `step_runs` row at all.
    """
    by_key = {u.get("step_key"): u for u in upstream if isinstance(u, dict)}
    for upstream_key, required in (guarded_by or {}).items():
        produced = (by_key.get(upstream_key) or {}).get("guard")
        if produced != required:
            return {"step_key": step_key, "step": qualified_name, "status": "skipped",
                    "engine": "prefect", "guard": None,
                    "detail": (f"guard {required!r} required from {upstream_key} "
                               f"but it produced {produced!r}")}

    # An upstream that failed must stop this branch. Running on regardless
    # would produce a result derived from a step that did not happen.
    failed = [k for k, u in by_key.items() if u.get("status") == "error"]
    if failed:
        return {"step_key": step_key, "step": qualified_name, "status": "skipped",
                "engine": "prefect", "guard": None,
                "detail": f"upstream step(s) failed: {', '.join(sorted(failed))}"}

    info = _step_info_for(entity_type, step_key)
    # Brief D 7f.1: the steps of one database scan share the run's time, so
    # they merge into one snapshot (the in-process loop does this in
    # `SurveyDefinitionExecutor._execute`). Added here, inside the task, so the
    # flow's persisted parameters stay exactly what they were.
    step_kwargs = (
        {**runner_kwargs, "scan_surveyed_at": surveyed_at}
        if entity_type == "database" and surveyed_at else runner_kwargs
    )
    # F-AB-1: the repo runner (SurveyOrchestrator) records its own step_runs row. Hand it the run's time, and run
    # it inside this flow run's scope (as `_run_step_as_flow_run` does, TR-21) so that row carries the flow run id;
    # then the row below is written only for a runner that recorded none (or a planner-added step: the runner's row
    # does not carry its demanded_by). One row per step, all under one run.
    if entity_type == "repo" and surveyed_at:
        step_kwargs = {**step_kwargs, "surveyed_at": surveyed_at}
    where, why = _where_to_run(step_key, executes_at, info, credential_ref)
    if where == step_dispatch_policy.WORKER:
        return _run_on_worker(
            entity_type, slug, step_key, qualified_name, info, step_kwargs, surveyed_at, why,
            satisfied_by_stored=satisfied_by_stored, added_because=added_because, demanded_by=demanded_by)
    fr_id = _current_flow_run_id()
    try:
        with step_cost_observer.in_flow_run(fr_id or "") as recorded, \
                step_cost_observer.placement(step_dispatch_policy.IN_PROCESS), step_cost_observer.observe(
            step_key, getattr(info, "fetch_cost", ""), getattr(info, "compute_cost", ""),
            executor="prefect", source="prefect",
            demanded_by=demanded_by,
            entity_type=entity_type, slug=slug,
        ) as observed:
            output = run_surveyor_step_task.fn(
                entity_type=entity_type, slug=slug, step_name=step_key,
                runner_kwargs=step_kwargs,
                **({"credential_ref": credential_ref} if credential_ref else {}),
            )
    except Exception as exc:
        return {"step_key": step_key, "step": qualified_name, "status": "error",
                "engine": "prefect", "guard": None, "detail": str(exc)}

    if observed and not (fr_id and not demanded_by and (step_key, "") in recorded):
        # Dispatch honesty completeness (2026-09-28,
        # PREFECT-DISPATCH-HONESTY-IMPLEMENTED.md): this
        # function only ever reaches this line by genuinely executing as a
        # Prefect task inside a running flow (no REST-dispatch-then-fallback
        # branch like run_prefect_step has), so `executor="prefect"` above is
        # always trustworthy here — this just fills in the id for the same
        # "checkable against Prefect's own API" reason the per-step path's
        # flow_run_id carries one.
        if fr_id:
            observed[0].flow_run_id = str(fr_id)
        _record_step_cost(entity_type, slug, observed[0], output, surveyed_at)

    # Whole-definition Prefect default (2026-09-28,
    # PREFECT-DEFAULT-WHOLE-DEFINITION-IMPLEMENTED.md): carry the flow_run_id
    # into the step's own report entry too, not just onto the step_runs row —
    # `SurveyDefinitionExecutor._run_via_prefect` strips "output" but keeps
    # every other key, so this is how the whole-definition caller (and, via
    # steps_report, the /next pane's status line) learns which real Prefect
    # flow_run this run created, checkable against Prefect's own API rather
    # than merely asserted.
    result = {"step_key": step_key, "step": qualified_name, "status": "ok",
              "engine": "prefect", "guard": (output or {}).get("guard"),
              "flow_run_id": str(fr_id) if fr_id else "",
              "dispatch": {"where": step_dispatch_policy.IN_PROCESS, "why": why},
              "placement": step_dispatch_policy.IN_PROCESS,
              "output": output}
    return _mark_planned(result, satisfied_by_stored, added_because, demanded_by)


def _mark_planned(result: dict[str, Any], satisfied_by_stored, added_because: str, demanded_by: str) -> dict:
    if satisfied_by_stored:
        result["satisfied_by_stored"] = dict(satisfied_by_stored)
    if added_because:
        # The planner added this step; say so in the report, the same way an
        # auto-run prerequisite is marked on the local loop.
        result["auto_run"] = True
        result["added_because"] = added_because
        result["detail"] = added_because
        result["demanded_by"] = demanded_by
    return result


def _where_to_run(step_key: str, executes_at: str, info, credential_ref: str) -> tuple[str, str]:
    """`dispatch_decision` for a step of a whole-definition flow, plus the one case only this path has: a
    one-run override credential lives in this process's memory (credential_handoff), so it cannot go to a
    worker; that step stays here, said so."""
    try:
        from resource_explorer.config import get_config

        policy = step_dispatch_policy.resolve(get_config().prefect)
    except Exception as exc:  # noqa: BLE001 - an unreadable config dispatches nothing, and says so
        return step_dispatch_policy.IN_PROCESS, f"could not read the dispatch policy ({type(exc).__name__})"
    where, why = step_dispatch_policy.dispatch_decision(step_key, executes_at, info, policy)
    if where == step_dispatch_policy.WORKER and credential_ref:
        return (step_dispatch_policy.IN_PROCESS,
                f"{why}, but this run's override credential cannot leave this process")
    if where not in (step_dispatch_policy.WORKER, step_dispatch_policy.IN_PROCESS):
        # executes_at names another engine: `_all_steps_prefect_runnable` keeps such a step out of a
        # whole-definition flow, so this is not reached in practice; run it as the flow always has.
        return step_dispatch_policy.IN_PROCESS, why
    return where, why


def _run_on_worker(entity_type: str, slug: str, step_key: str, qualified_name: str, info,
                   step_kwargs: dict[str, Any], surveyed_at: str, why: str, *,
                   satisfied_by_stored=None, added_because: str = "", demanded_by: str = "") -> dict[str, Any]:
    """Send one step of a whole-definition flow to a Prefect worker (TR-11 F-AB-3).

    Rows (C5, TR-21): a step that reached the worker has its WORK row written by the worker's own step flow
    (executor=prefect, that flow run's id); what is measured here is only the wait, which goes to the report as
    `dispatch_wait_ms`, never into step_runs. A dispatch that fell back ran the step HERE: its row is
    executor=local with `dispatch_failed` naming why (the runner's own row via `dispatch_fallback`, else the one
    written below), and the report says "in-process (fallback: ...)". Outside `in_flow_run(<this flow's id>)` on
    purpose: inside it a local fallback row would be relabelled executor=prefect."""
    from resource_explorer.config import get_config
    from resource_explorer.surveyors.prefect_adapter import run_prefect_step

    dispatch: dict[str, Any] = {}
    try:
        enabled = bool(get_config().prefect.enabled)
    except Exception:  # noqa: BLE001
        enabled = False
    disabled_reason = "" if enabled else "Prefect is disabled (PREFECT_ENABLED=false)"
    try:
        with step_cost_observer.in_flow_run("") as recorded, step_cost_observer.observe(
            step_key, getattr(info, "fetch_cost", ""), getattr(info, "compute_cost", ""),
            executor="local", source="local", demanded_by=demanded_by,
            entity_type=entity_type, slug=slug,
        ) as observed:
            if enabled:
                output = run_prefect_step(entity_type, slug, step_key, step_kwargs, dispatch_info=dispatch)
            else:
                with step_cost_observer.dispatch_fallback(disabled_reason):
                    output = run_surveyor_step_task.fn(
                        entity_type=entity_type, slug=slug, step_name=step_key, runner_kwargs=step_kwargs)
    except Exception as exc:  # noqa: BLE001 - any failure is this step's error entry, as for an in-process step
        entry = {"step_key": step_key, "step": qualified_name, "status": "error",
                 "engine": dispatch.get("engine") or "prefect", "guard": None, "detail": str(exc),
                 "flow_run_id": str(dispatch.get("flow_run_id") or getattr(exc, "flow_run_id", "") or ""),
                 "dispatch": {"where": step_dispatch_policy.WORKER, "why": why}}
        if getattr(exc, "status", ""):
            entry["prefect_status"] = exc.status
        return entry

    wait_ms = round(observed[0].elapsed * 1000.0, 1) if observed else None
    if dispatch.get("engine") == "prefect":
        flow_run_id = str(dispatch.get("flow_run_id") or "")
        log.info("Prefect step %s dispatched to a worker (%s): flow run %s, dispatch_wait_ms=%s",
                 step_key, why, flow_run_id, wait_ms)
        result = {"step_key": step_key, "step": qualified_name, "status": "ok", "engine": "prefect",
                  "guard": (output or {}).get("guard"), "flow_run_id": flow_run_id,
                  "dispatch_wait_ms": wait_ms,
                  "dispatch": {"where": step_dispatch_policy.WORKER, "why": why},
                  "placement": step_dispatch_policy.WORKER,
                  "output": output}
        return _mark_planned(result, satisfied_by_stored, added_because, demanded_by)

    reason = dispatch.get("dispatch_failed") or disabled_reason or "Prefect dispatch did not happen"
    if observed and not (not demanded_by and (step_key, "") in recorded):
        observed[0].executor = observed[0].source = "local"
        observed[0].flow_run_id = observed[0].executor_ref = ""
        observed[0].dispatch_failed = reason
        observed[0].placement = step_cost_observer.fallback_placement(reason)
        _record_step_cost(entity_type, slug, observed[0], output, surveyed_at)
    log.warning("Prefect step %s was to run on a worker (%s) but ran in-process: %s", step_key, why, reason)
    result = {"step_key": step_key, "step": qualified_name, "status": "ok", "engine": "local",
              "guard": (output or {}).get("guard"), "flow_run_id": "",
              "dispatch": {"where": step_cost_observer.fallback_placement(reason), "why": why},
              "placement": step_cost_observer.fallback_placement(reason),
              "output": output}
    result = _mark_planned(result, satisfied_by_stored, added_because, demanded_by)
    if not added_because:
        result["detail"] = f"ran locally: Prefect dispatch failed — {reason}"
    return result


@flow(name="RE Survey Definition Flow")
@_daemon_entry
@_one_acquisition_per_run
def re_survey_definition_flow(
    entity_type: str,
    slug: str,
    plan: list[dict[str, Any]],
    runner_kwargs: dict[str, Any] | None = None,
    surveyed_at: str = "",
    credential_ref: str = "",
) -> list[dict[str, Any]]:
    """Run a whole Survey Definition, ordered by Prefect.

    `plan` is the serialised ExecutionPlan — a list of
    {step_key, qualified_name, depends_on, guarded_by} in topological order, so
    every dependency has already been submitted when its dependents are.

    `surveyed_at` (§E, 2026-09-27): this run's shared timestamp, the same one
    `SurveyDefinitionExecutor.run()`'s local loop stamps across its own
    steps — passed through to every planned step so `step_cost_observer.
    record` (`run_planned_step_task`) writes this run's `step_runs` rows
    under it, exactly as the local loop already does for its own steps.
    """
    runner_kwargs = runner_kwargs or {}
    futures: dict[str, Any] = {}
    for entry in plan:
        key = entry["step_key"]
        upstream = [futures[d] for d in entry.get("depends_on", []) if d in futures]
        futures[key] = run_planned_step_task.submit(
            entity_type=entity_type, slug=slug, step_key=key,
            qualified_name=entry.get("qualified_name", ""),
            runner_kwargs=runner_kwargs,
            upstream=upstream,
            guarded_by=entry.get("guarded_by", {}) or {},
            satisfied_by_stored=entry.get("satisfied_by_stored", {}) or {},
            surveyed_at=surveyed_at,
            credential_ref=credential_ref,
            added_because=entry.get("added_because", "") or "",
            demanded_by=entry.get("demanded_by", "") or "",
            executes_at=entry.get("executes_at", "") or "resource-explorer",
        )
    # Resolved in plan order so the report reads in the order authored, not the
    # order Prefect happened to finish them in.
    return [futures[e["step_key"]].result() for e in plan]
