"""The `worker` process role — owner of every always-on background loop.

Before 2026-09-04 all of this started inside the FastAPI lifespan
(``web/app.py``), which meant there was no way to run the loops without
a web server and no way to run a web server without them. With N uvicorn
workers they would have run N times. ``docs/runtime-architecture-plan.md``
§2 makes the loops a **role**; this module is that role, and
``web/app.py`` now starts nothing of its own.

What it owns (docs/process-model.md §1.1 for the current-state detail):

* the scheduler loop — which, per that document's Finding F1, is not
  only "run due analyses": **RFA reconciliation** happens inside the same
  15-minute tick, in the same thread. The Egeria **outbox drain** and the
  native-survey read-back sweep run on their own 60 s thread
  (``scheduler._readback_loop``, 2026-10-21), started and stopped by
  ``start_scheduler``/``stop_scheduler`` and so under the same ``scheduler``
  advisory lock: a long ``_run_due`` can no longer starve them.
* the bootstrap monitor — heals Dr.Egeria definitions wiped by an
  Egeria reset, every 10 minutes.
* the Egeria resync scanner — clears stale Egeria pointers left by a
  repository-store wipe, every 10 minutes.
* orphaned-run reconciliation — one-shot at startup.
* the survey-definition cache warm — one-shot, best-effort.

Leadership
----------
Each of the three loops is gated on its own ``pg_try_advisory_lock``
(``leader_election.py``). A process that does not win a loop's key logs
``standby`` and retries at that loop's own interval, so leadership
transfers on its own when the holder exits — no failover machinery, and
no cleanup: Postgres drops a session's advisory locks when the
connection goes.

The two one-shots are deliberately **not** gated:

* orphaned-run reconciliation judges ownership per row (pid liveness
  plus process start time, ``run_reconciler.py``), so it is already safe
  to run in every process and would be *less* correct if only a leader
  ran it.
* the cache warm repopulates a **process-local** dict
  (``survey_definition_reader``'s module-level caches). Gating it would
  warm exactly one process's cache and leave the others cold, which is
  the opposite of what it is for.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable

from resource_explorer.leader_election import (
    LOCK_BOOTSTRAP,
    LOCK_DRAFT_ZONE,
    LOCK_EGERIA_RESYNC,
    LOCK_ENGINE_HOST,
    LOCK_SCHEDULER,
    LeaderLock,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoopSpec:
    """One leader-elected background loop."""

    name: str
    lock_name: str
    interval_seconds: int
    start: Callable[[], None]
    stop: Callable[[], None]


def _scheduler_spec() -> LoopSpec:
    from resource_explorer import scheduler

    return LoopSpec(
        name="scheduler",
        lock_name=LOCK_SCHEDULER,
        interval_seconds=scheduler._CHECK_INTERVAL_SECONDS,
        start=scheduler.start_scheduler,
        stop=scheduler.stop_scheduler,
    )


def _bootstrap_spec() -> LoopSpec:
    from resource_explorer import bootstrap

    return LoopSpec(
        name="bootstrap-monitor",
        lock_name=LOCK_BOOTSTRAP,
        interval_seconds=bootstrap.CHECK_INTERVAL_SECONDS,
        start=bootstrap.start_scheduler,
        stop=bootstrap.stop_scheduler,
    )


def _resync_spec() -> LoopSpec:
    from resource_explorer import egeria_resync

    return LoopSpec(
        name="egeria-resync",
        lock_name=LOCK_EGERIA_RESYNC,
        interval_seconds=egeria_resync.CHECK_INTERVAL_SECONDS,
        start=egeria_resync.start_scheduler,
        stop=egeria_resync.stop_scheduler,
    )


def _engine_host_spec() -> LoopSpec:
    from resource_explorer import engine_host

    return LoopSpec(
        name="engine-host",
        lock_name=LOCK_ENGINE_HOST,
        interval_seconds=engine_host.SWEEP_INTERVAL_SECONDS,
        start=engine_host.start_engine_host,
        stop=engine_host.stop_engine_host,
    )


def loop_specs() -> list[LoopSpec]:
    """The leader-elected loops, imported lazily so that importing
    this module does not drag in Egeria clients or the registry.

    Three by default. The engine-host claim sweep is a fourth only when
    RE_ENGINE_HOST_ENABLED is on: off means the loop is never started, never
    elects, and makes no Egeria call (DESIGN-RE-ENGINE-HOST-BY-EQUIVALENCE §5.2
    test 10)."""
    specs = [_scheduler_spec(), _bootstrap_spec(), _resync_spec()]
    from resource_explorer.engine_host import engine_host_enabled

    if engine_host_enabled():
        specs.append(_engine_host_spec())
    return specs


# ── one-shots ───────────────────────────────────────────────────────────

def _reconcile_orphaned_runs() -> None:
    """Resolve activity rows left 'running' by a process that is provably
    gone. Ownership-based (pid + process start time), so it never touches
    a live peer's run — see run_reconciler.py's docstring."""
    try:
        from resource_explorer.run_reconciler import reconcile

        result = reconcile()
        if result["resolved"]:
            log.info(
                "reconciled %s orphaned run(s) at startup; %s left alone",
                result["resolved"], result["left_alone"],
            )
    except Exception as exc:
        # Startup must not fail because bookkeeping did.
        log.warning("orphaned-run reconciliation skipped: %s", exc)


#: How often the worker re-checks the run queue for rows whose claimer died.
#: Well inside the stale window (3 × 30s) so a dead claim is noticed within
#: about two minutes rather than at the next process restart, which is when the
#: activity-log reconciliation runs.
_QUEUE_RECONCILE_INTERVAL_SECONDS = 60


def _reconcile_stale_queue_runs(*, ignore_heartbeat: bool = False) -> None:
    """Fail `runs` rows whose claiming process is provably gone.

    Ungated for the same reason `_reconcile_orphaned_runs` is: the judgement is
    per row and pid-based, so it is already safe in every process, and electing
    a leader for it would make it slower without making it safer.

    Runs on its own short interval rather than once at startup, because a queue
    row that a dead worker holds blocks that user's fairness slot — the
    activity-log equivalent blocks nothing, which is why a startup-only sweep
    was enough there and is not enough here.

    `ignore_heartbeat` is passed at worker startup. The heartbeat cutoff exists
    so the periodic sweep does not test liveness for every active row every
    minute; it is not part of the judgement. A pid that is provably gone is
    gone whether or not it wrote a heartbeat ten seconds before it died — and
    at startup a full sweep costs one query, so waiting three intervals to
    notice a claim the previous process left behind would be a delay bought
    with nothing.
    """
    try:
        from resource_explorer.run_reconciler import reconcile_runs

        result = reconcile_runs(ignore_heartbeat=ignore_heartbeat)
        if result["resolved"]:
            log.info(
                "reconciled %s stale queue run(s); %s left alone",
                result["resolved"], result["left_alone"],
            )
    except Exception as exc:
        log.warning("queue-run reconciliation skipped: %s", exc)


#: Deliberately NOT a LoopSpec. `loop_specs()` is the list of *leader-elected*
#: loops — the advisory keys in docs/process-model.md §1.1 are pinned by
#: tests/test_process_roles.py, and putting an ungated loop in that list would
#: make "which loops need an election" and "which loops the worker runs" the
#: same question when they are not. Same reasoning as the two one-shots.
_queue_reconcile_stop = threading.Event()
_queue_reconcile_thread: threading.Thread | None = None


def _start_queue_reconcile_loop() -> None:
    global _queue_reconcile_thread

    def _loop() -> None:
        while not _queue_reconcile_stop.wait(_QUEUE_RECONCILE_INTERVAL_SECONDS):
            _reconcile_stale_queue_runs()

    _queue_reconcile_stop.clear()
    _queue_reconcile_thread = threading.Thread(
        target=_loop, name="re-queue-reconcile", daemon=True,
    )
    _queue_reconcile_thread.start()


def _stop_queue_reconcile_loop() -> None:
    global _queue_reconcile_thread

    _queue_reconcile_stop.set()
    _queue_reconcile_thread = None


def _warm_survey_definition_cache() -> None:
    """Pre-resolve Survey-Definition question GUIDs so the first UI click
    does not pay the Egeria round trip.

    In its own daemon thread, never joined: startup must not wait on
    Egeria, which may legitimately not be up yet — the platform often
    starts after this process. Failures are swallowed by
    warm_question_guid_cache, which is best-effort by construction; if
    the warm does not happen, the first request resolves exactly as it
    does today. This changes WHEN the lookup happens, never WHAT it
    concludes.
    """

    def _run() -> None:
        try:
            from resource_explorer.surveyors.survey_definition_reader import (
                SurveyDefinitionReader,
            )

            from resource_explorer.egeria_clients import Daemon, DaemonReason

            # Its own thread, so its identity is declared here (Brief I): the daemon.
            SurveyDefinitionReader(identity=Daemon(DaemonReason.STARTUP_WARM)).warm_question_guid_cache()
        except Exception as exc:  # never let an optimisation take a process down
            log.debug("survey-definition cache warm skipped: %s", exc)

    threading.Thread(target=_run, name="survey-cache-warm", daemon=True).start()


def _ensure_draft_zone() -> None:
    """Create RE's draft `GovernanceZone` once, if one is CONFIGURED and Egeria lacks it.
    (By default none is configured and nothing is created; the private-zone bootstrap still runs.)

    Leader-elected and one-shot, not a loop: the zone either exists or it does
    not, and asking Egeria about it every 60 seconds would be a round trip
    whose answer never changes. The lock is what makes "once" true across N
    worker processes starting together — pyegeria's generic element create has
    no upsert, so two simultaneous creates would produce two zones with the
    same qualified name.

    A process that loses the election does **not** retry: whoever won either
    created the zone or found it there, and a loser retrying later would just
    re-ask a settled question. Contrast the loops in `_supervise`, which retry
    forever precisely because leadership can change hands.

    Its own thread, never joined, for the same reason as the cache warm above:
    startup must not wait on Egeria, which is routinely not up yet. Failure is
    logged and tolerated — `ensure_draft_zone_exists` never raises, and a
    publish still names the zone in its `ZoneMembership` whether or not the
    zone element exists.
    """

    from resource_explorer.egeria_identity import draft_zone as _dz

    # Every process says which mode it is in (a zone NAME is not a secret).
    log.info("draft zone: %s", _dz() or "none (default)")

    def _run() -> None:
        lock = LeaderLock(LOCK_DRAFT_ZONE)
        if not lock.acquire():
            log.debug("draft-zone bootstrap: another process holds the lock — skipping")
            return
        try:
            from resource_explorer.egeria_identity import (
                ensure_draft_zone_exists, ensure_private_zone_exists,
            )

            # Only when a draft zone was CONFIGURED (EXPLORER_DRAFT_ZONE). By default RE stamps
            # no draft zone and creates no GovernanceZone for it (owner ruling 2026-10-08).
            from resource_explorer.egeria_identity import draft_zone

            if draft_zone():
                outcome = ensure_draft_zone_exists()
                log.info("draft-zone bootstrap: %s", outcome)

            # The private zone, under the same lock and in the same one-shot.
            # Different mechanism from the draft zone though — that one creates
            # a GovernanceZone metadata ELEMENT (documentation, which the
            # security connector never reads); this one creates the
            # SecurityAccessControl the connector actually enforces. See
            # `ensure_private_zone_exists`.
            #
            # Logged at WARNING when it did not work, because the consequence is
            # user-visible: private investigations refuse to publish until it
            # does, which is the deliberate safe direction but needs to be
            # findable in the log rather than inferred from a refusal later.
            private = ensure_private_zone_exists()
            if private.get("enforced"):
                log.info("private-zone bootstrap: %s", private)
            else:
                log.warning(
                    "private-zone bootstrap: %s — private investigations will NOT "
                    "publish to Egeria until this is resolved. %s",
                    private, private.get("remedy") or "")

            # Every OTHER zone RE stamps (a configured draft or publish zone): its control read, three facts
            # recorded per zone, and one line for all of them, the private zone's entry included. WARNING when
            # any zone would lock everyone out once Egeria e9fd817f4c ships, warns, or could not be checked.
            from resource_explorer.egeria_identity import check_zone_controls, zone_controls_line

            report = check_zone_controls()
            if all(e.get("verdict") == "ok" for e in report):
                log.info(zone_controls_line(report))
            else:
                log.warning(zone_controls_line(report)
                            + " — publishing into a zone that would lock out everyone, or could not be "
                              "checked, is refused until its control is fixed.")

            # Read-only check of the ConnectorType RE's secrets store uses; a mismatch is named, never repaired.
            from resource_explorer.connector_type_check import check_connector_type, connector_type_line

            ct = check_connector_type()
            if ct.get("status") in ("ok", "absent"):
                log.info(connector_type_line(ct))
            else:
                log.warning(connector_type_line(ct))
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("draft-zone bootstrap failed: %s", exc)
        try:
            _bootstrap_re_identity()
        finally:
            lock.release()

    threading.Thread(target=_run, name="draft-zone-bootstrap", daemon=True).start()


def _bootstrap_re_identity() -> dict:
    """RE's own place in Egeria's actor graph (7h): ITProfile, UserIdentity, ProfileIdentity.
    Adopt-or-create, as the daemon; never raises, never blocks start. One log line, the same
    words the connection popover shows ("RE identity in Egeria: present / created / could not
    check (reason)"); WARNING when it could not check."""
    from resource_explorer.egeria_actors import (
        COULD_NOT_CHECK_WORD, ensure_re_identity_in_egeria, identity_words,
    )

    try:
        state = ensure_re_identity_in_egeria()
    except Exception as exc:  # pragma: no cover - ensure_re_identity_in_egeria never raises
        state = {"status": COULD_NOT_CHECK_WORD, "reason": f"{type(exc).__name__}: {exc}"}
    line = "RE identity in Egeria: %s"
    if state.get("status") == COULD_NOT_CHECK_WORD:
        log.warning(line, identity_words(state))
    else:
        log.info(line + " (ITProfile %s, UserIdentity %s)", identity_words(state),
                 state.get("it_profile_guid"), state.get("user_identity_guid"))
    return state


# ── the leader-gated supervisor ─────────────────────────────────────────

def _supervise(spec: LoopSpec, stop_event: threading.Event, embedded: bool) -> None:
    """Win the loop's lock, start it, hold leadership until asked to stop.

    A process that loses the election does not give up: it retries at the
    loop's own interval, so when the current leader exits this one takes
    over on its next tick without anyone coordinating the handover.
    """
    lock = LeaderLock(spec.lock_name)
    started = False
    try:
        while not stop_event.is_set():
            if lock.acquire():
                log.info(
                    "worker loop started: name=%s interval=%ss leader=true "
                    "advisory_key=%s embedded=%s",
                    spec.name, spec.interval_seconds, lock.key, embedded,
                )
                spec.start()
                started = True
                stop_event.wait()
                break
            log.info(
                "worker loop standby: name=%s interval=%ss leader=false "
                "advisory_key=%s embedded=%s (another process holds this "
                "lock; retrying every %ss)",
                spec.name, spec.interval_seconds, lock.key, embedded,
                spec.interval_seconds,
            )
            if stop_event.wait(spec.interval_seconds):
                break
    finally:
        if started:
            try:
                spec.stop()
            except Exception:
                log.exception("worker loop %s did not stop cleanly", spec.name)
        lock.release()



def _preload_pyegeria() -> bool:
    """Import the pyegeria modules the loops use, on the calling thread, before
    any loop thread starts.

    The loops import pyegeria lazily inside their functions; when several start
    at once on a cold interpreter, two threads import the same partially
    initialised module tree and importlib raises ``_DeadlockError`` (seen on
    trevor, 2026-09-04: the draft-zone bootstrap died with "deadlock detected
    by _ModuleLock('pyegeria.omvs.classification_explorer')" while the resync
    loop was importing the same package). One import on one thread, first,
    removes the race. Returns False when pyegeria is not installed; the loops
    then fail on their own terms as before.
    """
    try:
        import pyegeria  # noqa: F401
        from pyegeria import ClassificationExplorer, EgeriaTech  # noqa: F401
        from pyegeria.omvs.metadata_expert import MetadataExpert  # noqa: F401
        return True
    except Exception as exc:  # pragma: no cover - environment dependent
        log.debug("pyegeria preload skipped: %s", exc)
        return False

def run_worker(
    embedded: bool = False,
    stop_event: threading.Event | None = None,
    shutdown_timeout: float = 15.0,
) -> None:
    """Run the worker role.

    Blocks until ``stop_event`` is set. Call it in the foreground
    (``resource-explorer worker``) or, with ``embedded=True``, from a
    daemon thread inside a web process (``--embed-worker``) — the two
    differ only in what they log and who owns the stop event, because
    leader election, not the caller, is what decides which process's
    loops actually fire.
    """
    _preload_pyegeria()
    stop_event = stop_event if stop_event is not None else threading.Event()

    from resource_explorer.config import check_public_url, log_llm_tier_config

    check_public_url()  # refuses to start on a malformed RE_PUBLIC_URL, quoting it
    log_llm_tier_config()  # the resolved tier/model/num_ctx/budgets; the lazy first-call line predates logging setup

    log.info(
        "worker role starting (embedded=%s): %s leader-elected loop(s) "
        "plus orphaned-run reconciliation, survey-definition cache warm and "
        "draft/private-zone bootstrap",
        embedded, len(loop_specs()),
    )

    from resource_explorer.surveyors.prefect_adapter import log_prefect_reachability_at_startup

    log_prefect_reachability_at_startup()

    _reconcile_orphaned_runs()
    _warm_survey_definition_cache()
    _ensure_draft_zone()

    # The run queue. NOT leader-elected and NOT a LoopSpec, deliberately: the
    # three loops above take an advisory lock because two processes firing the
    # same schedule is duplicated work, whereas every extra queue consumer is
    # extra throughput — `SKIP LOCKED` is what makes that safe, and electing a
    # single leader would make the queue exactly as fast as its leader.
    from resource_explorer.run_queue import start_queue_runner, stop_queue_runner

    _reconcile_stale_queue_runs(ignore_heartbeat=True)
    start_queue_runner()
    _start_queue_reconcile_loop()

    supervisors: list[threading.Thread] = []
    for spec in loop_specs():
        t = threading.Thread(
            target=_supervise,
            args=(spec, stop_event, embedded),
            name=f"re-worker-{spec.name}",
            daemon=True,
        )
        t.start()
        supervisors.append(t)

    try:
        stop_event.wait()
    finally:
        stop_event.set()
        stop_queue_runner()
        _stop_queue_reconcile_loop()
        # Bounded: a supervisor blocked on an Egeria call must not be
        # able to hold the process open past its shutdown budget. The
        # threads are daemons, so anything still running dies with the
        # process — the join is to give a clean stop its chance, not to
        # depend on it.
        deadline_each = max(0.1, shutdown_timeout / max(1, len(supervisors)))
        for t in supervisors:
            t.join(timeout=deadline_each)
        still = [t.name for t in supervisors if t.is_alive()]
        if still:
            log.warning(
                "worker shutdown bound (%ss) reached with %s still running; "
                "they are daemon threads and go with the process",
                shutdown_timeout, ", ".join(still),
            )
        else:
            log.info("worker role stopped cleanly")
        # Drop the shared pool LAST and explicitly, not via atexit: the
        # interpreter's own shutdown joins pool workers before any atexit
        # handler of ours runs, so a worker stuck in pyegeria holds the
        # process open past every bound above. Verified live 2026-09-04 —
        # see concurrency.py's "Abandoned workers" section.
        from resource_explorer.concurrency import shutdown as _pool_shutdown

        _pool_shutdown(wait=False)


def start_embedded_worker() -> tuple[threading.Thread, threading.Event]:
    """Run the worker role in a daemon thread inside this process.

    Used by the FastAPI lifespan under ``--embed-worker``. Returns the
    thread and its stop event so the lifespan can stop it on shutdown.
    """
    stop_event = threading.Event()
    thread = threading.Thread(
        target=run_worker,
        kwargs={"embedded": True, "stop_event": stop_event},
        name="re-embedded-worker",
        daemon=True,
    )
    thread.start()
    return thread, stop_event
