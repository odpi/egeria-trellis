"""The Postgres run queue — enqueue from anywhere, execute in the worker.

`docs/runtime-architecture-plan.md` §2, step 2b. Until this existed, a `web`
request that started a survey spawned a `threading.Thread` and returned. Three
consequences, all of them observed rather than theorised:

* The run lived in whichever uvicorn process happened to serve the request, so
  `--workers N` spread long-running work across processes by luck of routing.
* A `--no-embed-worker` web process — supposedly "HTTP only" — was still where
  every survey actually executed, which is the plan's own rule for the `web`
  role being violated by the `web` role.
* The only record of ownership was a pid buried in an `activity_log` row's
  `detail`, judged *after the fact* by `run_reconciler.py`. A claim taken
  before the work starts is the same question asked in time to be useful.

**What did not change: the frontend contract.** A route still creates its
`activity_log` row up front, still returns `{"status": "started",
"activity_id": ...}`, and the browser still polls `GET /api/activity/{id}` and
reads the run's result out of that entry's `detail`. The queue row carries that
activity id in `result_ref`; the worker writes the terminal activity status
through the very same `execute_and_record_*` functions the route's background
thread used to call. From the UI's point of view the only difference is which
process did the work.

**Claiming.** `registry.claim_next_run()` selects and marks in one transaction,
with `FOR UPDATE SKIP LOCKED` on Postgres so a second claimer skips a locked
row rather than blocking on it. See that method for the per-user fairness rule
and why the empty `requested_by` bucket is exempt from it — since 2026-09-04
that bucket holds only the worker's own service-account work, so the exemption
is narrow and the fairness rule finally applies to real people.

**Attribution.** A claimed row is executed inside `_run_as_requester`, which
publishes `requested_by` as the caller so anything the run writes to Egeria is
owned by the person who asked for it. The row deliberately carries no Egeria
token — see that function for why, and for what the resulting gap is.

**Heartbeating.** A run's heartbeat is written every
`HEARTBEAT_INTERVAL_SECONDS` by a small companion thread, not by the executing
thread itself — the executing thread is, by construction, inside a survey that
may not return for sixteen minutes, so it cannot also be the thing that proves
it is alive. The companion thread's own liveness is the process's liveness,
which is exactly what reconciliation tests.

**Reconciliation** stays in `run_reconciler.py` and stays pid-based. A missed
heartbeat makes a row a *candidate*; only a provably-dead owning process makes
it a failure. A worker paused by a slow Egeria call is late, not gone.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

log = logging.getLogger(__name__)

#: How often a running row's heartbeat is refreshed.
HEARTBEAT_INTERVAL_SECONDS = 30

#: How long a claimed row may go without a heartbeat before reconciliation will
#: even look at it. Three intervals, so one slow tick and one missed tick are
#: both survivable without anyone being declared dead.
STALE_AFTER_INTERVALS = 3

#: How long the claim loop waits when the queue is empty. Short enough that a
#: click feels immediate; long enough that an idle dev box is not issuing a
#: query a second.
POLL_INTERVAL_SECONDS = 2.0


def worker_identity() -> str:
    """The `claimed_by` value: `hostname:pid`.

    Human-readable on purpose — it is what an operator reads in `runs list` and
    in the worker's own log line. It is NOT what reconciliation tests: pids are
    reused, so liveness is judged from the `runner` column's
    `run_reconciler.process_identity()` (pid plus process start time) instead.
    """
    return f"{socket.gethostname()}:{os.getpid()}"


def queue_consumption_enabled() -> bool:
    """Whether this process may CLAIM rows. Enqueueing is never affected.

    An operational flag first — a worker started to hold the background loops
    while a queue drain is deliberately paused (mid-migration, or while an
    Egeria platform is down and every claim would burn a retry) is a real thing
    to want, and there was no way to ask for it.

    It also stops the test suite from draining the shared registry's live queue.
    That is not hypothetical: `tests/test_process_roles.py` calls `run_worker()`
    for real, several sessions share one Postgres, and a claim loop started
    inside a unit test would happily pick up another session's queued survey and
    run it. `tests/conftest.py` sets this off for the whole session; the queue's
    own tests turn it back on for exactly the run they are asserting about.
    """
    raw = os.environ.get("EXPLORER_RUN_QUEUE_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def consumes_kind_here(kind: str) -> bool:
    """True when THIS process is running a QueueRunner that accepts `kind` (loop started, kind included and not held).

    The press route holds a presser's session only when this is true: a held session in a process that never claims
    its row could only be waited out. Otherwise the run is pressed as before (no hold) and fails fast with the
    sign-in-expired words in whichever process claims it."""
    runner = _runner
    if runner._thread is None or kind in HELD_KINDS:
        return False
    return runner.kinds is None or kind in runner.kinds


def requested_by() -> str:
    """Who a run is attributed to: the signed-in caller, or `""`.

    This was the plan's own hook and it has now been taken (2026-09-04, plan
    §4): it reads RE's one identity ContextVar, set by the web
    `_identity_middleware`, by the A2A middleware, and by the CLI's
    `cli.session.activate()`. So a run enqueued from a browser, from an agent
    or from a terminal all carry the same user id with no per-call-site
    threading — which is exactly why this was a function and not a literal.

    `""` now means one thing only: **the worker's own service-account work**,
    plus a genuinely signed-out invocation. `registry.claim_next_run()` still
    exempts that bucket from the per-user fairness rule, and that exemption is
    now narrow rather than universal — see that method.
    """
    from resource_explorer.a2a_auth import caller

    identity = caller()
    if identity is None or identity.auth_source == "anonymous":
        return ""
    return identity.user_id or ""


@dataclass(frozen=True)
class RunOutcome:
    state: str  # "succeeded" | "failed"
    error: str = ""
    #: "applying as <user> from <source> · written as <x>": the identity the run held when it wrote (intent,
    #: `presser_hold.applying_as_line`), then who Egeria says wrote, from a read-back (proof,
    #: `catalogue_commit.commit_written_as_line`). The activity entry ends with it. '' = not said.
    applied_as: str = ""

    def __post_init__(self) -> None:
        # One place for EVERY handler: an error text can carry a connection string or token from
        # somebody else's exception, and this is what finish_run stores and the UI shows.
        from resource_explorer.secret_redaction import scrub_text

        object.__setattr__(self, "error", scrub_text(self.error))


# ── the handlers, one per kind ───────────────────────────────────────────────
#
# Each takes the row's decoded `target` dict plus the `result_ref` (the
# activity_log id the UI polls) and returns a RunOutcome. They are thin on
# purpose: every one of them delegates to a workflows/ function that the route
# and the CLI also call, so "what a run does" has exactly one definition.


def _handle_analysis_run(target: dict, result_ref: str) -> RunOutcome:
    from resource_explorer.workflows.analysis import execute_and_record_analysis

    result = execute_and_record_analysis(
        target["slug"], target["analysis_id"], result_ref,
        publish=target.get("publish"),
        # Default "repo" for a row enqueued before `entity_type` was added to
        # `WorkLists.enqueue_batch`'s target dict — unchanged behaviour for
        # any row already queued.
        entity_type=target.get("entity_type", "repo"),
    )
    return RunOutcome(
        state="succeeded" if result.status == "ok" else "failed",
        error=result.error or "",
    )


def _handle_database_analysis_run(target: dict, result_ref: str) -> RunOutcome:
    """The database equivalent of `_handle_analysis_run` above — added
    because `run_single_database_analysis` (web/routes/databases.py) used to
    run its survey step(s) synchronously inside the request and return a
    response with no `activity_id` at all, so `/next`'s `pollActivity()`
    (which every "Run" action on a database's Questions checklist goes
    through) hit `GET /api/activity/undefined` and reported "Activity entry
    not found" for a run that had, in fact, already completed. See
    `workflows.analysis.execute_and_record_database_analysis`'s docstring for
    why this is a distinct handler/dataclass from the repo path rather than a
    reuse of `_handle_analysis_run` — database analysis runs do not
    auto-publish to Egeria the way repo's do."""
    from resource_explorer.workflows.analysis import execute_and_record_database_analysis

    result = execute_and_record_database_analysis(
        target["slug"], target["analysis_id"], result_ref,
    )
    return RunOutcome(
        state="succeeded" if result.status == "ok" else "failed",
        error=result.error or "",
    )


def _handle_filesystem_analysis_run(target: dict, result_ref: str) -> RunOutcome:
    """One file system Analytic Library step (Files build step 6): the file-system twin of
    `_handle_database_analysis_run`. Stored, never published (a file system's publish stays its own press)."""
    from resource_explorer.workflows.analysis import execute_and_record_filesystem_analysis

    result = execute_and_record_filesystem_analysis(target["slug"], target["analysis_id"], result_ref)
    return RunOutcome(state="succeeded" if result.status == "ok" else "failed", error=result.error or "")


def _handle_stage_batch(target: dict, result_ref: str) -> RunOutcome:
    from resource_explorer.workflows.analysis import (
        execute_and_record_stage_batch,
        resolve_stage_step_keys,
    )

    step_keys = target.get("step_keys")
    if not step_keys:
        # Re-derived rather than required: a batch enqueued from the CLI names
        # a stage, not a step list, and the catalog is the authority on what
        # the stage contains at the moment it runs.
        step_keys, _ = resolve_stage_step_keys(target["stage"])
    result = execute_and_record_stage_batch(
        target["slug"], target["stage"], step_keys, result_ref,
    )
    return RunOutcome(
        state="succeeded" if result.status == "ok" else "failed",
        error="; ".join(result.errors) if result.status == "error" else "",
    )


def _handle_scouting_scan(target: dict, result_ref: str) -> RunOutcome:
    from resource_explorer.workflows.scouting import execute_and_record_scouting_scan

    result = execute_and_record_scouting_scan(target["slug"], result_ref)
    return RunOutcome(
        state="succeeded" if result.status == "ok" else "failed",
        error=result.error or "",
    )


def _handle_survey_definition_run(target: dict, result_ref: str) -> RunOutcome:
    from resource_explorer.workflows.survey_definition import (
        SurveyDefinitionRunParams,
        execute_and_record_definition,
    )

    from contextlib import nullcontext

    from resource_explorer import presser_hold

    params = SurveyDefinitionRunParams.from_dict(target.get("params") or {})
    run_id = (current_run.get() or {}).get("id") or ""
    # A pressed run's publish runs as the person who pressed it (their held session, or the "sign-in expired" words);
    # any other run keeps what it had.
    pressed = (presser_hold.pressed_run(run_id, params.started_by_person)
               if params.publish_as_presser and params.started_by_person else nullcontext())
    with pressed:
        result = execute_and_record_definition(
            target["entity_type"], target["slug"], params, result_ref,
        )
    return RunOutcome(
        state="succeeded" if result.status == "ok" else "failed",
        error="; ".join(result.errors) if result.errors else "",
    )


def _handle_discovery_expand(target: dict, result_ref: str) -> RunOutcome:
    from resource_explorer.workflows.discovery import expand_org

    expansion = expand_org(target["org"])
    if expansion.error:
        return RunOutcome(state="failed", error=expansion.error)
    return RunOutcome(state="succeeded")


def _handle_curate_commit(target: dict, result_ref: str) -> RunOutcome:
    """Catalogue →. Every step writes its outcome to the curation record;
    the run fails only if a step did (the record says which)."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.workflows.curate_commit import execute_curation

    rec = execute_curation(ProjectRegistry(), target["curation_id"])
    failed = [s["name"] for s in rec.get("steps", []) if s.get("state") == "failed"]
    return RunOutcome(state="failed" if failed else "succeeded",
                      error=("failed: " + ", ".join(failed)) if failed else "")


def _handle_catalogue_commit(target: dict, result_ref: str) -> RunOutcome:
    """Catalogue → for a database. Every step writes its outcome to the curation
    record; the run fails only if a step did (the record says which)."""
    from contextlib import nullcontext

    from resource_explorer import presser_hold
    from datetime import datetime, timezone

    from resource_explorer.catalogue_commit import (
        commit_written_as_line,
        execute_commit,
        refuse_presser_gone,
        run_with_loop,
    )
    from resource_explorer.egeria_clients import EXPIRED_SENTENCE, current_principal
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    run_id = (current_run.get() or {}).get("id") or ""
    presser = str(target.get("presser") or "").strip() if target.get("publish_as_presser") else ""
    # A pressed commit writes AS the person who pressed it (their session, held in memory by the press: the #836
    # mechanism). It crosses from the request thread to this worker thread in the hold, keyed by the run id, never in
    # a ContextVar. A row with no person (none is enqueued today) keeps the queue's daemon, as before.
    pressed = presser_hold.pressed_run(run_id, presser) if presser else nullcontext()
    applied = ""
    with pressed:
        try:
            with presser_hold.publishing_as_presser():
                who = current_principal()
                applied = presser_hold.applying_as_line(who)
                log.info("catalog commit %s (%s): %s", target.get("curation_id"), target.get("slug"), applied)
                started = datetime.now(timezone.utc)
                # pyegeria's sync wrappers need an event loop on the executing thread
                rec = run_with_loop(execute_commit, registry, target["curation_id"])
                # The proof beside that intent: who Egeria says wrote the elements THIS commit wrote (its first
                # attached schema, else a leave-out's archived copy), read by GUID as the run acts, and only when
                # changed since `started` ("written as: could not check" otherwise, never an earlier writer).
                try:
                    written = run_with_loop(commit_written_as_line, registry, target["curation_id"], started)
                except Exception:  # noqa: BLE001 - the commit is done; an unreadable proof is said, never a crash
                    written = presser_hold.WRITTEN_AS_UNCHECKED
                applied = f"{applied} · {written}" if applied else written
        except presser_hold.PresserSignInExpired:
            # Nothing written, as anyone: never retried as the service account.
            applied = f"not applied: {EXPIRED_SENTENCE}"
            rec = refuse_presser_gone(registry, target["curation_id"])
    failed = [s["name"] for s in rec.get("steps", []) if s.get("state") == "failed"]
    return RunOutcome(state="failed" if failed else "succeeded",
                      error=("failed: " + ", ".join(failed)) if failed else "", applied_as=applied)


def _handle_materialize_components(target: dict, result_ref: str) -> RunOutcome:
    """Accepted components become Egeria SolutionComponents, one at a time,
    after a branch verdict, then each is promoted out of RE's draft zone (the configured-only zone
    rule). Run with an event loop on this thread, like its sibling `_handle_curate_commit`: pyegeria's
    sync wrappers need one and a worker thread has none. The run fails if a materialization did OR a
    promotion did; the summary says which, separately."""
    from resource_explorer.catalogue_commit import run_with_loop

    return run_with_loop(_materialize_components, target)


def _materialize_components(target: dict) -> RunOutcome:
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.workflows.curate import (
        NODE_PROMOTION_COMPONENT,
        materialize_component_if_accepted,
        promote_to_publish_zones,
        record_promotion,
    )

    from resource_explorer.secret_redaction import scrub_text

    registry = ProjectRegistry()
    slug = target["slug"]
    failed, promo_failed, done = [], [], 0
    for path in target.get("paths") or []:
        try:
            res = materialize_component_if_accepted(registry, "repo", slug, path, "accepted")
            if res and res.get("status") == "error":
                failed.append(scrub_text(f"{path}: {res.get('error')}"))
                continue
            guid = (res or {}).get("guid", "")
            if guid:
                promotion = promote_to_publish_zones(guid)
                record_promotion(registry, slug, path, NODE_PROMOTION_COMPONENT, promotion)
                if promotion.get("status") == "error":
                    promo_failed.append(scrub_text(f"{path}: {promotion.get('error') or promotion.get('words')}"))
                    continue
            done += 1                      # counted only once the whole accept (element and zone) landed
        except Exception as exc:
            failed.append(scrub_text(f"{path}: {type(exc).__name__}: {exc}"))
    if not failed and not promo_failed:
        return RunOutcome(state="succeeded")
    parts = [f"{done} materialised"]
    if failed:
        parts.append("materialization failed: " + "; ".join(failed))
    if promo_failed:
        parts.append("promotion failed: " + "; ".join(promo_failed))
    return RunOutcome(state="failed", error=" · ".join(parts)[:1500])


def _handle_publish_architecture(target: dict, result_ref: str) -> RunOutcome:
    """Publish for a repository's architecture: the accepted components, then the accepted blueprints, are
    written to Egeria (architecture_publish.run_publish). Run with an event loop on this thread, like its
    sibling `_handle_materialize_components`. The run fails when any item failed or was only partly written;
    the per-item results are the proof rows the run wrote."""
    from resource_explorer.catalogue_commit import run_with_loop

    return run_with_loop(_publish_architecture, target, result_ref)


def _publish_architecture(target: dict, result_ref: str) -> RunOutcome:
    from resource_explorer.architecture_publish import FAILED, PARTIAL, run_publish
    from resource_explorer.registry import ProjectRegistry

    results = run_publish(ProjectRegistry(), target["slug"], target, result_ref)
    bad = [r for r in results if r["status"] in (FAILED, PARTIAL)]
    if not bad:
        return RunOutcome(state="succeeded")
    return RunOutcome(state="failed", error=" · ".join(
        f"{r['name']}: {r['status']} · {r['words']}" for r in bad)[:1500])


def _handle_first_index(target: dict, result_ref: str) -> RunOutcome:
    """A repository's first index for chat, the ingestion a new project gets (first_index.run_first_index). The
    handler is the run's one writer of the activity entry's terminal words; a failure is the first sentence."""
    from resource_explorer.first_index import run_first_index

    result = run_first_index(target["slug"])
    return RunOutcome(state="succeeded" if result.status == "ok" else "failed", error=result.error or "")


def _handle_engine_action(target: dict, result_ref: str) -> RunOutcome:
    """An Egeria engine action RE claimed as an engine host (engine_host.py, E4). Runs as
    `Daemon(ENGINE_HOST, requested_by)` (see `_run_as`), never as `_run_as_requester`'s RUN_QUEUE."""
    from resource_explorer.engine_host import run_engine_action

    run = current_run.get() or {}
    state, error = run_engine_action(run.get("registry"), run.get("id", ""), target)
    return RunOutcome(state=state, error=error)


HANDLERS: dict[str, Callable[[dict, str], RunOutcome]] = {
    "curate_commit": _handle_curate_commit,
    "catalogue_commit": _handle_catalogue_commit,
    "materialize_components": _handle_materialize_components,
    "publish_architecture": _handle_publish_architecture,
    "analysis_run": _handle_analysis_run,
    "database_analysis_run": _handle_database_analysis_run,
    "filesystem_analysis_run": _handle_filesystem_analysis_run,
    "stage_batch": _handle_stage_batch,
    "scouting_scan": _handle_scouting_scan,
    "survey_definition_run": _handle_survey_definition_run,
    "discovery_expand": _handle_discovery_expand,
    "engine_action": _handle_engine_action,
    "first_index": _handle_first_index,
}


#: Kinds whose rows are enqueued before their handler exists, and so are held QUEUED rather than claimed
#: and failed with "no handler". Empty since E4 (DESIGN-RE-ENGINE-HOST-BY-EQUIVALENCE §5.1) registered the
#: `engine_action` handler; kept as the mechanism for the next kind enqueued ahead of its handler.
HELD_KINDS: frozenset[str] = frozenset()

#: Kinds a person may never cancel from RE (web route and CLI; refused in `registry.cancel_queued_run`), in
#: any state. An `engine_action` row stands for an Egeria engine action RE has already CLAIMED: cancelling
#: the row would leave that action claimed in Egeria with nothing to complete it (C11: Egeria has no lease).
#: Cancelling an engine action is Egeria's own call (C10), not in this slice. Held kinds are included.
UNCANCELLABLE_KINDS: frozenset[str] = HELD_KINDS | frozenset({"engine_action"})

#: Kinds run as an engine host, `Daemon(ENGINE_HOST, requested_by)`: owner, 2026-10-14, "when RE accepts an
#: action or process for execution, it is acting as a daemon - there is no relationship to logged in users."
ENGINE_HOST_KINDS = frozenset({"engine_action"})

#: The row being executed (`{"id", "registry"}`), for a handler that must write its own outcome on the row
#: before it tells Egeria (the engine-action handler). Set by `execute_run` around the handler only.
current_run: "ContextVar[dict | None]" = ContextVar("re_current_run", default=None)


# ── executing one claimed row ────────────────────────────────────────────────


class _Heartbeat:
    """Refresh one run's heartbeat until the work finishes.

    A daemon thread rather than a timer on the executing thread, because the
    executing thread is inside the survey and cannot come back to tick. It
    stops on its own when `registry.heartbeat_run` reports the row is no longer
    active — which is how it learns the row was reconciled or cancelled out
    from under it instead of writing heartbeats onto a terminal row for ever.
    """

    def __init__(self, registry, run_id: str, interval: float = HEARTBEAT_INTERVAL_SECONDS):
        self._registry = registry
        self._run_id = run_id
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name=f"re-run-heartbeat-{run_id[:8]}", daemon=True,
        )

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                if not self._registry.heartbeat_run(self._run_id):
                    log.info("run %s is no longer active; heartbeat stopping", self._run_id)
                    return
            except Exception as exc:  # a lost heartbeat must not kill the run
                log.warning("heartbeat for run %s failed: %s", self._run_id, exc)

    def __enter__(self) -> _Heartbeat:
        self._thread.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self._stop.set()


# ── who a run acts as (Brief I, owner's Egeria-consistent ruling 2026-10-09) ──

#: A person's own actions that are queued. Like Egeria's engine hosts, a queued write is
#: committed under RE's daemon identity; the person's accountability is the recorded
#: `requested_by` and the Ownership classification stamped with it. So these kinds MUST carry a
#: requester: queued with none, they fail loudly rather than write anonymously on someone's behalf.
#: (`materialize_components` is a run kind with a handler but nothing enqueues it today, so it is
#: not listed; whoever starts queueing it adds it here.)
PERSON_ACTION_KINDS = frozenset({"publish_architecture", "curate_commit", "catalogue_commit", "first_index"})

NO_REQUESTER_SENTENCE = ("this action was queued with no requester recorded; RE does not write to "
                         "Egeria on a person's behalf without saying whose")


@contextmanager
def _run_as_requester(row: dict):
    """Execute a claimed row as `Daemon(RUN_QUEUE, requested_by)`: the daemon authenticates and
    `Ownership` is stamped with `requested_by` (no token survives a queue; this is also how
    Egeria's own engine hosts commit queued work). `current_caller` names the requester
    (token-less), because the registry's user scoping reads it. A row with no requester is the
    worker's own work: `Daemon(RUN_QUEUE)` with nobody recorded."""
    from resource_explorer.a2a_auth import CallerIdentity, current_caller
    from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

    requester = (row.get("requested_by") or "").strip()
    reset = current_caller.set(
        CallerIdentity(user_id=requester, egeria_token=None, auth_source="queued-run", role="user")
        if requester else None)
    try:
        with acting_as(Daemon(DaemonReason.RUN_QUEUE, requested_by=requester or None)):
            yield
    finally:
        current_caller.reset(reset)


@contextmanager
def _run_as_engine_host(row: dict):
    """Execute an engine-action row as `Daemon(ENGINE_HOST, requested_by=<requesterUserId>)`. No caller is
    set: the engine host has no relationship to a signed-in person (owner, 2026-10-14). The requester is
    recorded and stamped as Ownership by the handler; it is never a credential (native does the same:
    `GovernanceEngineHandler.java:98-101`, `SurveyActionEngineHandler.java:302-331`)."""
    from resource_explorer.a2a_auth import current_caller
    from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

    requester = (row.get("requested_by") or "").strip()
    reset = current_caller.set(None)
    try:
        with acting_as(Daemon(DaemonReason.ENGINE_HOST, requested_by=requester or None)):
            yield
    finally:
        current_caller.reset(reset)


def _run_as(row: dict):
    """Who a claimed row runs as: the engine host for engine-host kinds, else `_run_as_requester`."""
    if row.get("kind") in ENGINE_HOST_KINDS:
        return _run_as_engine_host(row)
    return _run_as_requester(row)


def _first_sentence(text: str, limit: int = 240) -> str:
    text = " ".join(str(text or "").split())
    for i, ch in enumerate(text):
        if ch == "." and (i + 1 == len(text) or text[i + 1] == " "):
            text = text[:i]
            break
    return text[:limit]


def _activity_words(registry, kind: str, target: dict, outcome: "RunOutcome") -> tuple[str, str]:
    """(status, one sentence) for the activity entry of a run that just ended.

    Says what happened. A failed commit names the step that failed and Egeria's/our own first sentence
    about why, read from the curation record the handler already filled in.
    """
    slug = str(target.get("slug") or "") if isinstance(target, dict) else ""
    ok = outcome.state == "succeeded"
    status = "ok" if ok else "error"
    if kind in ("curate_commit", "catalogue_commit"):
        label = "Cataloging" if not ok else "Cataloged"
        failed = []
        steps = []
        cid = target.get("curation_id") if isinstance(target, dict) else None
        if cid:
            from resource_explorer.curate_plan import Curations
            rec = Curations(registry).get(cid) or {}
            steps = rec.get("steps") or []
            failed = [s for s in steps if s.get("state") == "failed"]
        if ok:
            done = sum(1 for s in steps if s.get("state") == "done")
            return status, f"{label} {slug or 'the resource'}: {done} step(s) done."
        if failed:
            first = failed[0]
            why = _first_sentence(first.get("detail") or outcome.error or "no reason recorded")
            more = f" (and {len(failed) - 1} more step(s) failed)" if len(failed) > 1 else ""
            return status, f"{label} {slug or 'the resource'} failed at step {first.get('name')}: {why}{more}."
        return status, f"{label} {slug or 'the resource'} failed: {_first_sentence(outcome.error) or 'no reason recorded'}."
    if kind == "first_index":
        name = slug or "the repository"
        if ok:
            return status, f"First index of {name} for chat: done."
        return status, f"First index of {name} for chat failed: {_first_sentence(outcome.error, 400) or 'no reason recorded'}."
    if kind == "materialize_components":
        n = len(target.get("paths") or []) if isinstance(target, dict) else 0
        if ok:
            return status, f"Materialised {n} accepted component(s) of {slug or 'the resource'}."
        return status, f"Materialising {n} accepted component(s) of {slug or 'the resource'} failed: {_first_sentence(outcome.error, 400) or 'no reason recorded'}."
    if kind == "publish_architecture":
        n = (len(target.get("paths") or []) + len(target.get("blueprints") or [])) if isinstance(target, dict) else 0
        refused = len(target.get("not_permitted") or []) if isinstance(target, dict) else 0
        if ok:
            return status, (f"Published {n} accepted item(s) of {slug or 'the resource'}"
                            + (f"; {refused} not permitted." if refused else "."))
        return status, f"Publishing {n} accepted item(s) of {slug or 'the resource'} failed: {_first_sentence(outcome.error, 400) or 'no reason recorded'}."
    if ok:
        return status, f"{kind.replace('_', ' ')} finished."
    return status, f"{kind.replace('_', ' ')} failed: {_first_sentence(outcome.error) or 'no reason recorded'}."


def _close_activity(registry, result_ref: str, kind: str, target: dict, outcome: "RunOutcome") -> None:
    """Every exit of a run writes the final status on its activity entry (the entry the route opened as
    'running' and handed over as `result_ref`). A no-op when the handler already closed it. Not wrapped:
    if the registry cannot be written the caller must see that, not a row quietly left 'running'."""
    if not result_ref:
        return
    status, summary = _activity_words(registry, kind, target, outcome)
    if outcome.applied_as:
        summary = f"{summary} · {outcome.applied_as}"
    registry.close_activity_if_running(result_ref, status, summary)


def execute_run(row: dict, registry=None) -> RunOutcome:
    """Run one claimed row to a terminal state and record it; the presser's held session (`presser_hold`) is released
    on EVERY exit: an early return, a failed `mark_run_running`/`set_activity_runner`, a crash, or a normal end.

    Every failure path writes a terminal state; the one way a run ends without doing so is the process dying,
    which is what reconciliation exists for (and why the heartbeat is written at all).
    """
    from resource_explorer import presser_hold

    with presser_hold.released_on_exit(row["id"]):
        import json

        from resource_explorer.registry import ProjectRegistry

        registry = registry or ProjectRegistry()
        run_id = row["id"]
        kind = row["kind"]
        result_ref = row.get("result_ref") or ""
        try:
            target = json.loads(row.get("target") or "{}")
        except (TypeError, ValueError):
            target = {}

        handler = HANDLERS.get(kind)
        if handler is None:
            # Loud and terminal, not left queued: a kind with no handler is a
            # deployment mistake, and a row that sits queued for ever looks
            # identical to a queue nobody is draining.
            error = f"no handler registered for run kind {kind!r}"
            log.error("run %s: %s", run_id, error)
            registry.finish_run(run_id, "failed", error=error)
            outcome = RunOutcome(state="failed", error=error)
            _close_activity(registry, result_ref, kind, target, outcome)
            return outcome

        if kind in PERSON_ACTION_KINDS and not (row.get("requested_by") or "").strip():
            # A person's action with nobody recorded: never an anonymous daemon write (Brief I).
            log.error("run %s (%s): %s", run_id, kind, NO_REQUESTER_SENTENCE)
            registry.finish_run(run_id, "failed", error=NO_REQUESTER_SENTENCE)
            outcome = RunOutcome(state="failed", error=NO_REQUESTER_SENTENCE)
            _close_activity(registry, result_ref, kind, target, outcome)
            return outcome

        registry.mark_run_running(run_id)
        if result_ref:
            # Stamp THIS process onto the activity entry, now that it is genuinely
            # the owner. The enqueuing web process deliberately does not — see
            # registry.set_activity_runner. This is also what fixes the
            # process-model's Finding F5: a queued scouting scan now records
            # ownership the same way an analysis run does, so run_reconciler's
            # pid-liveness path applies to it instead of the six-hour age heuristic.
            #
            # Not wrapped in a best-effort try/except, deliberately, and for the
            # same reason `mark_run_running` above is not: if this write fails the
            # registry is unreachable, and the run's very next acts are to heartbeat
            # and to write findings to that same registry. Swallowing the failure
            # would buy a run that proceeds without an owner recorded — precisely
            # the state reconciliation cannot judge — instead of one that fails
            # immediately and visibly.
            from resource_explorer.run_reconciler import process_identity

            registry.set_activity_runner(result_ref, process_identity())
        log.info("run started: id=%s kind=%s claimed_by=%s target=%s",
                 run_id, kind, row.get("claimed_by"), target)
        try:
            from resource_explorer.observability import acquisition, llm_usage

            run_reset = current_run.set({"id": run_id, "registry": registry})
            try:
                with _Heartbeat(registry, run_id), \
                        _run_as(row), \
                        llm_usage.usage_scope() as usage, \
                        acquisition.acquisition_scope() as acquired:
                    outcome = handler(target, result_ref)
            finally:
                current_run.reset(run_reset)
        except Exception as exc:  # pragma: no cover — a handler is expected to catch its own
            # Scrubbed by shape before it is logged or stored: a handler's exception can carry a connection
            # string or token, and this catch-all writes the traceback, the run row and the outcome.
            import traceback
            from resource_explorer.secret_redaction import scrub_text

            log.error("run %s (%s) crashed\n%s", run_id, kind, scrub_text(traceback.format_exc()))
            crash = scrub_text(f"{type(exc).__name__}: {exc}")
            registry.finish_run(run_id, "failed", error=crash)
            outcome = RunOutcome(state="failed", error=crash)
            _close_activity(registry, result_ref, kind, target, outcome)
            return outcome

        # LLM cost for this run, recorded OUTSIDE the try above on purpose: the
        # handler has already succeeded by here, and a metrics sink must not be able
        # to turn that into a failed run. `usage` is still in scope — the `with`
        # closed, the name did not — and is read here in the worker rather than from
        # a callback, since the scope is a ContextVar only code running under it can
        # see. `log_run_usage` swallows its own errors too; this is belt and braces
        # because the cost of being wrong is a run reported failed after doing its
        # work.
        # Logged whenever EITHER has something to say. A run that made no LLM call
        # but fetched a 200MB zipball is the expensive case §1 cares most about, and
        # gating on `usage.calls` alone would have dropped it entirely.
        if usage.calls or acquired.lookups:
            snapshot = usage.as_dict()
            acquired_snapshot = acquired.as_dict()
            log.info("run %s (%s) cost: %s %s", run_id, kind, snapshot, acquired_snapshot)
            # No try/except here on purpose. log_run_usage cannot raise — its whole
            # body is guarded — and wrapping it would make execute_run itself a
            # broad-except/log-only site in a function that returns a RunOutcome,
            # which is exactly what tests/test_no_silent_success.py flags. The
            # protection belongs in the sink, not at every call site.
            from resource_explorer.observability.mlflow_tracking import log_run_usage

            log_run_usage(run_id, kind, snapshot, acquisition=acquired_snapshot,
                          slug=str(target.get("slug") or "") if isinstance(target, dict) else "",
                          analysis_id=str(target.get("analysis_id") or "") if isinstance(target, dict) else "")

        registry.finish_run(run_id, outcome.state, error=outcome.error)
        _close_activity(registry, result_ref, kind, target, outcome)
        log.info("run finished: id=%s kind=%s state=%s", run_id, kind, outcome.state)
        return outcome


# ── the claim loop ───────────────────────────────────────────────────────────


def _engine_request_types_here(kinds: list[str] | None) -> "set[str] | None":
    """The engine-action request types THIS worker can run (built-in steps plus the Analytic Library steps
    installed here), so an `engine_action` row for a step whose package this worker lacks stays queued for one
    that has it. None when this claim cannot take an engine_action row at all. Unreadable is the empty set: no
    engine_action row is taken (fail closed), every other kind is."""
    if kinds is not None and "engine_action" not in kinds:
        return None
    try:
        from resource_explorer.engine_host import supported_request_types

        return set(supported_request_types())
    except Exception as exc:  # noqa: BLE001 - never stop the queue for it; take no engine action instead
        log.warning("run queue: could not read the engine host's request types (%s); no engine_action row is "
                    "taken by this worker until it can", exc)
        return set()


def claim_and_execute_once(registry=None, *, kinds: list[str] | None = None) -> dict | None:
    """Claim at most one row and run it. Returns the row, or None if the queue
    had nothing this worker was allowed to take.

    Exposed on its own because it is the whole of the queue's behaviour in one
    call — the loop below is just this in a `while`, and a test that wants to
    assert the embedded worker executes a queued row does not need a loop.
    """
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.run_reconciler import process_identity

    registry = registry or ProjectRegistry()
    identity = worker_identity()
    if kinds is None and HELD_KINDS:
        # "All kinds" means all kinds this worker can run: a held kind waits for its handler (see HELD_KINDS).
        kinds = [k for k in registry.RUN_KINDS if k not in HELD_KINDS]
    row = registry.claim_next_run(identity, process_identity(), kinds=kinds,
                                  engine_request_types=_engine_request_types_here(kinds))
    if row is None:
        return None
    log.info("run claimed: id=%s kind=%s claimed_by=%s", row["id"], row["kind"], identity)
    execute_run(row, registry)
    return row


class QueueRunner:
    """The worker role's queue loop, in the shape `worker.py` starts things.

    Not leader-elected, deliberately, and this is the one place in the worker
    role where that is true. The three background loops take an advisory lock
    because two processes firing the same schedule is duplicated work; the
    queue is the opposite — `SKIP LOCKED` means every extra consumer is extra
    throughput, and electing one leader would throw that away and make the
    queue as slow as its single leader.
    """

    def __init__(self, *, poll_interval: float = POLL_INTERVAL_SECONDS,
                 kinds: list[str] | None = None):
        self.poll_interval = poll_interval
        self.kinds = kinds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _loop(self) -> None:
        from resource_explorer.concurrency import run_sync

        while not self._stop.is_set():
            try:
                # Through the shared bounded pool (step 2a's concurrency.py),
                # not a thread of its own: a run is exactly the kind of
                # blocking sync-pyegeria work that pool exists to bound, and a
                # queue that spawned an unbounded thread per row would
                # reintroduce the problem step 2a removed. `run_sync` runs the
                # callable inline when it is already on a pool thread, so this
                # cannot deadlock against itself.
                claimed = run_sync(lambda: claim_and_execute_once(kinds=self.kinds))
            except Exception:
                import traceback
                from resource_explorer.secret_redaction import scrub_text

                log.error("run-queue tick failed; continuing\n%s", scrub_text(traceback.format_exc()))
                claimed = None
            if claimed is None:
                if self._stop.wait(self.poll_interval):
                    return
            # A row was executed — loop straight round rather than sleeping, so
            # a burst of queued work drains at the speed of the work.

    def start(self) -> None:
        if self._thread is not None:
            return
        if not queue_consumption_enabled():
            log.info("run-queue loop not started: EXPLORER_RUN_QUEUE_ENABLED is off")
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="re-worker-run-queue", daemon=True,
        )
        self._thread.start()
        log.info("run-queue loop started: poll=%ss identity=%s kinds=%s",
                 self.poll_interval, worker_identity(), self.kinds or "all")

    def stop(self) -> None:
        self._stop.set()
        self._thread = None
        log.info("run-queue loop stopped")


#: The worker role's single instance, so `worker.py` can start and stop it the
#: same way it starts and stops the leader-elected loops.
_runner = QueueRunner()


def start_queue_runner() -> None:
    _runner.start()


def stop_queue_runner() -> None:
    _runner.stop()
