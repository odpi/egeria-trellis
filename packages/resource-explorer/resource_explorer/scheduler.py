"""Background scheduler — executes due analyses in a daemon thread.

Schedules are configured via POST /api/schedules/{entity_type}/{slug} and stored
in the resource_schedules SQLite table. This module starts a single daemon thread
that wakes every _CHECK_INTERVAL_SECONDS, finds analyses whose next_run has passed,
runs them, and advances their next_run timestamp.

Every run — success or failure — writes a real ActivityEntry (via
activity_logger.log_survey) and records the outcome on the schedule row itself
(resource_schedules.last_run_status/last_run_activity_id). Previously this only
logged to Python's own logger, invisible from the UI — a real gap against
CLAUDE.md rule 16 ("Activity log entries must be written for ALL operations"),
not a deliberate omission. This is what backs the Admin Schedules overview's
"watch for successful completions, look for errors that need follow-up" use.

Supported entity_type values: 'repo', 'database'

Dispatch by analysis_id, not just entity_type: a scheduled analysis_id might be
(a) a local analysis_catalog entry — runs RE's own scan, as before; (b) a
local analysis_catalog entry flagged egeria_registration.native_process_ref
(currently just database's 'egeria_db_survey') — initiates Egeria's own
native re-survey of an already-cataloged resource via
EgeriaDatabaseSurveyor.trigger_survey_by_guid(), passing this schedule's
next_run as Egeria's own start_time so its engine host (not RE's poll loop)
owns the precise timing; (c) a local analysis_catalog entry that's neither of
the above (currently repo's 'egeria_publish' and database's equivalent) —
writes NEW things into Egeria (registers + publishes), deliberately excluded
from scheduling, which needs explicit operator intent, not a cadence; or (d)
not in the local catalog at all — a Survey Definition qualified_name (from
any intent's D7a Survey panel, docs/unified-survey-execution-model-plan.md —
its per-candidate ⏱ Schedule action writes exactly this shape of schedule
row), dispatched through the same run_survey_definition() executor the
panel's own manual "Run →" button uses. Previously (b)/(c)/(d) were never
distinguished from (a) — every scheduled database analysis silently ran the
generic local scan regardless of which one was actually selected, a real gap
found while wiring this up, not by design.

Repo dispatch, separately: previously _run_repo_survey always ran the full
SurveyOrchestrator (all 10 sub-surveyors) regardless of which analysis_id was
scheduled — repo's own version of the (a)-only gap above, fixed the same way:
repo_survey_definition_adapter.REPO_ANALYSIS_SOURCE_STEPS resolves a scheduled
analysis_id to the specific SurveyOrchestrator step key(s) to run via
SurveyOrchestrator.run(steps=...) — public there, not scheduler-private,
since web/routes/projects.py's per-card "Run just this one analysis" is a
second real consumer of the same mapping.
Repo has no (b) equivalent — no repo analysis_catalog entry is flagged for
native re-survey dispatch, and analysis_catalog_reader.py's live-Egeria
merge only ever applies to 'database'. Repo DOES have (d)
(_run_repo_survey_definition(), mirroring database's _run_survey_definition()
minus db_user/db_pwd — repo Survey Definition steps run entirely locally via
repo_survey_definition_adapter's runner(project, registry, **_) signature,
nothing to authenticate) — closes a real gap: the D7a Survey panel could
display and let a user set a schedule against a Survey Definition candidate,
but the scheduler had no dispatch path for it, so it would silently never
fire. So a repo analysis_id today is: a local catalog entry (dispatch by
step map), 'egeria_publish' (excluded, matching (c)), or a Survey Definition
qualified_name (dispatch via (d)) — no "unrecognized → error" bucket left
for that last case.

One more repo-only case: action:"ingest" (currently just 'rag_ingestion' —
"Refresh & Re-ingest," pgvector re-embedding via IncrementalIndexer). Unlike
action:"publish" this IS schedulable — it's a local re-index, not a new
write into Egeria's catalog of record — so it gets its own dispatch branch
in _run_repo_survey rather than going through REPO_ANALYSIS_SOURCE_STEPS (it
isn't a SurveyOrchestrator step at all).
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)

from resource_explorer.registry import TARGET_ANALYSIS, TARGET_SURVEY

_CHECK_INTERVAL_SECONDS = 900  # 15 minutes
_started = False
_lock = threading.Lock()
_stop_event: threading.Event | None = None
_thread: threading.Thread | None = None


def start_scheduler() -> None:
    """Start the background scheduler daemon thread (idempotent).

    Owned by the worker role since 2026-09-04 (docs/process-model.md):
    resource_explorer/worker.py calls this only after winning the
    `scheduler` advisory lock, so a second RE process against the same
    registry stands by rather than double-firing every due schedule.
    """
    global _started, _stop_event, _thread
    with _lock:
        if _started and _thread is not None and _thread.is_alive():
            return
        _started = True
        _stop_event = threading.Event()
        _thread = threading.Thread(
            target=_scheduler_loop,
            args=(_stop_event,),
            daemon=True,
            name="resource-explorer-scheduler",
        )
        t = _thread
    t.start()
    log.info("Background scheduler started (check interval: %ds)", _CHECK_INTERVAL_SECONDS)


def stop_scheduler(timeout: float = 5.0) -> None:
    """Ask the loop to stop and wait, bounded, for it to notice.

    This did not exist before 2026-09-04 — bootstrap.py and
    egeria_resync.py each had a stop_scheduler() and this one did not,
    so app.py's lifespan shut two of its three loops down and left the
    third running until the process died. Needed now for real: the
    worker role must release its advisory lock on the way out, and a
    loop still running past that release is exactly the double-fire the
    lock exists to prevent.
    """
    global _started, _stop_event, _thread
    with _lock:
        stop, thread = _stop_event, _thread
        _stop_event = None
        _thread = None
        _started = False
    if stop is not None:
        stop.set()
    if thread is not None and thread.is_alive():
        thread.join(timeout=timeout)


def _scheduler_loop(stop: threading.Event | None = None) -> None:
    """Tick every _CHECK_INTERVAL_SECONDS, sleeping FIRST — startup is
    not a reason to fire every due schedule at once.

    `stop` is optional so the loop stays directly callable (tests drive
    one iteration by patching time.sleep); the worker always passes one.
    """
    while stop is None or not stop.is_set():
        if stop is None:
            time.sleep(_CHECK_INTERVAL_SECONDS)
        elif stop.wait(_CHECK_INTERVAL_SECONDS):
            break
        try:
            _run_due()
        except Exception:
            log.exception("Scheduler iteration failed")
        try:
            _reconcile_rfa_actions()
        except Exception:
            log.exception("RFA reconciliation iteration failed")
        try:
            _drain_egeria_outbox()
        except Exception:
            log.exception("Egeria outbox drain iteration failed")


def _reconcile_rfa_actions() -> None:
    """docs/rfa-egeria-todo-followup.md's "Sync mechanics" — reuses this
    same background loop rather than starting a second one, runs every
    iteration regardless of whether any analysis is due (RFA/ToDo sync is
    independent of scheduled-analysis dispatch)."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.rfa_egeria_sync import reconcile_rfa_actions

    registry = ProjectRegistry()
    reconcile_rfa_actions(registry)


def _drain_egeria_outbox() -> None:
    """docs/outbox-publishing-design.md §5 — reuses this same background loop
    rather than starting a second one, exactly as RFA reconciliation above
    does, and for the same reason: this loop already runs everywhere publishes
    originate, so a retry layer driven from it sits under BOTH survey-launch
    paths automatically (design §6, note on step 4).

    Runs every iteration regardless of whether any analysis is due — a pending
    Egeria write is independent of scheduled-analysis dispatch, and is in fact
    most likely to exist precisely when nothing new is being scheduled."""
    from resource_explorer.egeria_outbox import drain_outbox
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    summary = drain_outbox(registry)
    if summary.get("claimed"):
        log.info("Egeria outbox drain: %s", summary)

    # Retention. Only completed rows, and only ones past the window — dead rows
    # still need a human and pending rows are live work. Driven from here rather
    # than left to the API route, because retention that has to be invoked by
    # hand is not retention: one proposal publish is ~2,100 rows at the largest
    # run observed, and nothing would ever call it in time.
    try:
        removed = registry.purge_outbox_completed()
        if removed:
            log.info("Egeria outbox: purged %d completed row(s) past retention", removed)
    except Exception:
        # Housekeeping must never break the drain that precedes it.
        log.exception("Egeria outbox: retention purge failed")


def _coalesce_repo_surveys(due: list[dict], registry) -> dict[tuple[str, str], list[str]]:
    """Run every same-repo due survey in ONE orchestrator call, and return each
    one's errors keyed by (entity_slug, analysis_id).

    Why this exists: shared resources are deduplicated *within* a
    SurveyOrchestrator.run() call — `resolve_resources` guarantees one zipball
    download no matter how many selected steps ask for one — but the scheduler
    ran each due analysis in its own call. Four of the sixteen repo analyses
    declare fetch_cost="download" (code_symbol_extraction, data_file_profiling,
    rag_ingestion, website_ingestion), so a repo with two of them scheduled
    daily downloaded the same zipball twice a day, every day, to no purpose.
    Cadences make this the common case rather than a rare one: schedules are
    picked from a small set of intervals, so they land on the same tick.

    Only analyses dispatched through REPO_ANALYSIS_SOURCE_STEPS are batched. The
    ingest/profile/publish and Survey-Definition paths each do something other
    than run orchestrator steps and keep their own dispatch.

    Groups of one are left alone deliberately — a single analysis gains nothing
    from being routed through here, and leaving it on the existing path keeps
    the common case running through code that has not changed.
    """
    from resource_explorer.surveyors.repo_survey_definition_adapter import (
        REPO_ANALYSIS_SOURCE_STEPS, STEP_REGISTRY,
    )
    from resource_explorer.surveyors.survey_orchestrator import SurveyOrchestrator

    by_slug: dict[str, list[dict]] = {}
    for sched in due:
        if sched["entity_type"] != "repo":
            continue
        analysis_id = sched["analysis_id"]
        # Same exclusions the per-analysis path applies, checked here so a
        # publish or an ingest never gets swept into a batch.
        if analysis_id == "rag_ingestion":
            continue
        entry = _catalog_entry("repo", analysis_id)
        # Not "ingest": website_ingestion carries that action for the UI but is
        # an ordinary orchestrator step and belongs in the batch. rag_ingestion,
        # the other ingest, is excluded by name above — the same distinction
        # that made action the wrong dispatch key in _run_repo_survey.
        if entry is None or entry.get("action") in ("publish", "profile"):
            continue
        # SOURCE steps throughout this function: the question here is what to
        # execute and which analyses to credit the outcome to, not who owns a
        # step. An analysis that derives from another's steps
        # (architecture_diagram) is genuinely refreshed by running them, so it
        # batches and reports like any other; skipping it on owned-steps would
        # make a schedule on it silently do nothing.
        if not REPO_ANALYSIS_SOURCE_STEPS.get(analysis_id):
            continue
        by_slug.setdefault(sched["entity_slug"], []).append(sched)

    results: dict[tuple[str, str], list[str]] = {}
    for slug, scheds in by_slug.items():
        if len(scheds) < 2:
            continue
        project = registry.get(slug)
        if not project:
            continue

        analysis_ids = [s["analysis_id"] for s in scheds]
        wanted = {k for aid in analysis_ids for k in REPO_ANALYSIS_SOURCE_STEPS[aid]}
        # STEP_REGISTRY order, not the order the schedules happened to be read
        # in. run(steps=[...]) executes in the caller's given order, and the
        # registry order encodes real prerequisites — repo_file_inventory must
        # precede everything that reads the inventory, or the batch reports
        # against the previous extraction while looking like a fresh one.
        ordered = [k for k in STEP_REGISTRY if k in wanted]

        log.info("Scheduler: coalescing %d due analysis(es) for %s into one run (%d step(s))",
                 len(scheds), slug, len(ordered))
        try:
            result = SurveyOrchestrator(registry).run(slug, steps=ordered)
        except Exception as exc:
            # One failure for the whole batch is correct here: nothing ran.
            for aid in analysis_ids:
                results[(slug, aid)] = [str(exc)]
            continue

        # Attribute per analysis, so one broken step fails only the analyses
        # that actually contain it rather than every analysis in the batch.
        for aid in analysis_ids:
            results[(slug, aid)] = [
                msg for k in REPO_ANALYSIS_SOURCE_STEPS[aid]
                if (msg := result.step_errors.get(k))
            ]
    return results


def _run_due() -> None:
    from resource_explorer.activity_logger import log_survey
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    due = registry.get_due_schedules()
    if not due:
        return
    log.info("Scheduler: %d analysis(es) due", len(due))
    # Batched first, so several due analyses for one repo share a single
    # zipball download instead of one each. Everything below is unchanged —
    # each analysis still gets its own activity entry, its own schedule
    # bookkeeping and its own subscription check, because those are per-schedule
    # contracts regardless of how the work was executed.
    try:
        batched = _coalesce_repo_surveys(due, registry)
    except Exception:
        log.exception("Scheduler: coalescing failed; falling back to per-analysis runs")
        batched = {}

    for sched in due:
        entity_type = sched["entity_type"]
        entity_slug = sched["entity_slug"]
        analysis_id = sched["analysis_id"]
        next_run = sched.get("next_run", "")
        analysis_name = _analysis_display_name(entity_type, analysis_id)
        try:
            batch_key = (entity_slug, analysis_id)
            if batch_key in batched:
                project = registry.get(entity_slug)
                entity_name = project.display_name if project else entity_slug
                entity_location = project.github_url if project else ""
                errors = batched[batch_key]
            else:
                entity_name, entity_location, errors = _execute(
                    entity_type, entity_slug, analysis_id, registry, next_run,
                    target_kind=sched.get("target_kind") or TARGET_ANALYSIS)
            status = "error" if errors else "ok"
            detail = "; ".join(errors) if errors else ""
        except Exception as exc:
            log.exception("Scheduler: failed %s/%s/%s", entity_type, entity_slug, analysis_id)
            entity_name, entity_location, status, detail = entity_slug, "", "error", str(exc)

        summary = f"Scheduled run of '{analysis_name}' " + ("completed with errors" if status == "error" else "completed successfully")
        try:
            activity_id = log_survey(
                registry=registry,
                entity_type=entity_type,
                entity_slug=entity_slug,
                entity_name=entity_name,
                entity_location=entity_location,
                intent="scouting",
                status=status,
                summary=summary,
                detail=detail,
            )
        except Exception:
            log.exception("Scheduler: failed to write activity log entry for %s/%s/%s", entity_type, entity_slug, analysis_id)
            activity_id = ""

        try:
            registry.update_schedule_after_run(entity_type, entity_slug, analysis_id, status=status, activity_id=activity_id)
        except Exception:
            log.exception("Scheduler: failed to update schedule bookkeeping for %s/%s/%s", entity_type, entity_slug, analysis_id)

        if status == "ok":
            try:
                _check_subscriptions(entity_type, entity_slug, analysis_id, entity_name, registry)
            except Exception:
                log.exception("Scheduler: subscription check failed for %s/%s/%s", entity_type, entity_slug, analysis_id)

        log.info("Scheduler: %s %s/%s/%s", status, entity_type, entity_slug, analysis_id)


def _check_subscriptions(entity_type: str, entity_slug: str, analysis_id: str, entity_name: str, registry) -> None:
    """Automate (Part 4) — after a scheduled run completes cleanly, check
    every active subscription watching this exact (entity, analysis_id)
    for a change since the previous run, and deliver via RFA if so.
    Detection only ever runs off scheduled runs (not manual "Run" clicks)
    — a subscription with no active schedule for its analysis_id never
    fires, since there's nothing recurring to compare against; this is
    surfaced to the user in the Automate UI, not silently true."""
    from resource_explorer.activity_logger import log_rfa

    subs = registry.list_subscriptions(
        entity_type=entity_type, entity_slug=entity_slug, analysis_id=analysis_id, active_only=True,
    )
    if not subs:
        return

    now = _now_iso()
    # Database analyses persist to structured tables, not to
    # project_analysis_findings/metrics (those are repo-only — FK'd to
    # projects(slug)), so the generic findings/metrics-backed detector has
    # nothing to read for them and would silently report "no change" forever
    # (Phase 1 slice 14, design §9.1 — see db_change_comparator.py's module
    # docstring for how this was found). Dispatch database subscriptions to
    # the dedicated comparator instead.
    if entity_type == "database":
        from resource_explorer.surveyors.database.db_change_comparator import (
            detect_database_change,
        )
        result = detect_database_change(registry, entity_slug, analysis_id)
    else:
        from resource_explorer.notification_detector import detect_change
        result = detect_change(registry, entity_slug, analysis_id)
    for sub in subs:
        registry.record_subscription_checked(sub["id"], now)
        if not result.changed:
            continue
        label = sub["label"] or _analysis_display_name(entity_type, analysis_id)
        try:
            log_rfa(
                registry=registry,
                entity_type=entity_type,
                entity_slug=entity_slug,
                entity_name=entity_name,
                status="pending",
                summary=f"{label} changed",
                detail=result.summary,
            )
            registry.record_subscription_notified(sub["id"], now)
        except Exception:
            log.exception("Scheduler: failed to deliver notification for subscription %s", sub["id"])


def _now_iso() -> str:
    from datetime import datetime
    return datetime.utcnow().isoformat()


def _analysis_display_name(entity_type: str, analysis_id: str) -> str:
    """Best-effort human-readable name for the summary line — falls back to
    the raw id (e.g. a Discovery Survey Definition's qualified_name, which
    isn't in the local analysis_catalog) if no catalog entry matches."""
    try:
        from resource_explorer.surveyors.analysis_catalog_reader import get_analyses
        for a in get_analyses(entity_type, include_egeria_live=False):
            if a["id"] == analysis_id:
                return a["name"]
    except Exception:
        pass
    return analysis_id


def _catalog_entry(entity_type: str, analysis_id: str) -> dict | None:
    """The local analysis_catalog entry for this id, or None if analysis_id
    isn't in the catalog at all (e.g. a Discovery Survey Definition's
    qualified_name, which is never a catalog entry)."""
    try:
        from resource_explorer.surveyors.analysis_catalog_reader import get_analyses
        for a in get_analyses(entity_type, include_egeria_live=False):
            if a["id"] == analysis_id:
                return a
    except Exception:
        pass
    return None


def _execute(
    entity_type: str, entity_slug: str, analysis_id: str, registry,
    next_run: str = "", target_kind: str = TARGET_ANALYSIS,
) -> tuple[str, str, list[str]]:
    """Returns (entity_name, entity_location, errors) — errors is [] on a
    clean run. Only truly unexpected failures propagate to the caller.

    Dispatch — see module docstring for the (a)/(b)/(c)/(d) breakdown
    (database) and the repo-specific paragraph beneath it.
    """
    if target_kind == TARGET_SURVEY:
        return _run_scheduled_survey(entity_type, entity_slug, analysis_id, registry)
    if entity_type == "repo":
        return _run_repo_survey(entity_slug, analysis_id, registry)
    elif entity_type == "database":
        return _run_db_survey(entity_slug, analysis_id, registry, next_run)
    else:
        return (entity_slug, "", [f"Unknown entity_type '{entity_type}'"])


def _run_scheduled_survey(
    entity_type: str, entity_slug: str, survey_ref: str, registry
) -> tuple[str, str, list[str]]:
    """Run a scheduled Survey Definition.

    A schedule could only name an analysis until 2026-08-28, so a survey could
    be authored, listed and run by hand but never put on a cadence. The
    reference is the definition's qualified name; run_survey_definition records
    the run and auto-publishes on the same terms a manual run does, so a
    scheduled survey and a hand-started one differ only in what triggered them.
    """
    from resource_explorer.surveyors.survey_definition_executor import (
        run_survey_definition,
    )

    entity = (registry.get(entity_slug) if entity_type == "repo"
              else registry.get_database(entity_slug))
    name = getattr(entity, "display_name", "") or entity_slug
    location = getattr(entity, "github_url", "") or ""
    try:
        result = run_survey_definition(
            entity_type, entity_slug, survey_definition_ref=survey_ref)
    except Exception as exc:
        log.exception("scheduled survey %s failed for %s", survey_ref, entity_slug)
        return (name, location, [f"Survey '{survey_ref}' failed: {exc}"])
    errors = list(result.get("errors") or [])
    # A survey whose run could not be recorded still ran. Surfaced as an error
    # on the schedule rather than passed over, since the schedules view is
    # where someone would look to find out whether it did.
    if result.get("run_recorded") is False:
        errors.append(f"Survey '{survey_ref}' ran but its run could not be recorded")
    return (name, location, errors)


def _run_repo_survey(slug: str, analysis_id: str, registry) -> tuple[str, str, list[str]]:
    project = registry.get(slug)
    if not project:
        return (slug, "", [f"Repo '{slug}' not found — schedule may be stale"])

    entry = _catalog_entry("repo", analysis_id)
    if entry is None:
        # (d) Not in the local catalog at all — a Survey Definition
        # qualified_name (from the D7a Survey panel's Schedule action,
        # docs/unified-survey-execution-model-plan.md — previously a real,
        # named gap: a schedule set against a candidate silently never
        # fired). Repo now has this case too, matching database's
        # long-standing _run_survey_definition() dispatch (see below) — the
        # module docstring's old claim that "repo has no (b)/(d) equivalent"
        # is now only true of (b) (no native-re-survey concept for repos).
        return _run_repo_survey_definition(project, analysis_id, registry)

    if entry.get("action") == "publish":
        return (project.display_name, project.github_url, [
            f"'{entry['name']}' writes new data into Egeria (action: publish) — excluded from "
            "scheduled runs by design. Run it manually when you intend that write."
        ])

    if analysis_id == "rag_ingestion":
        # rag_ingestion re-embeds content into pgvector via IncrementalIndexer,
        # not a SurveyOrchestrator step — unlike "publish" this IS schedulable
        # (it's a local re-index, not a new write into Egeria's catalog of
        # record), so it gets its own dispatch branch rather than the
        # step-map lookup below.
        #
        # Keyed on analysis_id, NOT on action == "ingest", which is what this
        # branch tested until website_ingestion arrived and became the second
        # entry carrying that action. website_ingestion IS an ordinary
        # SurveyOrchestrator step and belongs in the step-map path below;
        # dispatching it here would have silently re-indexed the repository
        # instead — a scheduled run that succeeds while doing something other
        # than what was scheduled. The action vocabulary describes what an
        # analysis does for the UI; it was never a dispatch key, and only
        # worked as one while each value happened to have a single member.
        from resource_explorer.ingestion.incremental import IncrementalIndexer
        from resource_explorer.query_cache import QueryCache

        try:
            IncrementalIndexer().refresh(project)
            QueryCache().invalidate_project(slug)
        except Exception as exc:
            return (project.display_name, project.github_url, [str(exc)])
        return (project.display_name, project.github_url, [])

    if entry.get("action") == "profile":
        # repo_profile_refresh downloads the zipball once and refreshes
        # project_file_inventory/project_data_profiles (local-only, not a
        # SurveyOrchestrator step) — schedulable like "ingest", not excluded
        # like "publish". Auto-chains the language_file_classification survey
        # against the freshly refreshed inventory, matching the interactive
        # "Coarse Profile Survey" Survey Definition — a scheduled refresh should
        # produce the same displayed result an on-demand one does, not a
        # data-only update nothing ever reads. (There is no "Profile tab":
        # coarse profiling is a Survey Definition, "Coarse Profile Survey", run
        # from Scouting's Survey sub-tab like any other survey type. The route
        # remains as an API-level on-demand trigger with no UI caller.)
        from resource_explorer.ingestion.pipeline import IngestionPipeline
        from resource_explorer.surveyors.repo_survey_definition_adapter import REPO_ANALYSIS_STEP_MAP
        from resource_explorer.surveyors.survey_orchestrator import SurveyOrchestrator

        try:
            IngestionPipeline().refresh_profile(
                project.slug, project.github_url, project.collections or [],
                subproject_path=project.subproject_path or None,
                include_symbols=False,
            )
        except Exception as exc:
            return (project.display_name, project.github_url, [str(exc)])

        registry.update_project_profiled_at(slug)

        errors = []
        try:
            classify_result = SurveyOrchestrator(registry).run(
                slug, steps=REPO_ANALYSIS_STEP_MAP["language_file_classification"],
            )
            errors = list(classify_result.errors) if classify_result.errors else []
        except Exception as exc:
            errors = [f"classification failed: {exc}"]
        return (project.display_name, project.github_url, errors)

    from resource_explorer.surveyors.repo_survey_definition_adapter import (
        REPO_ANALYSIS_SOURCE_STEPS,
    )

    steps = REPO_ANALYSIS_SOURCE_STEPS.get(analysis_id)
    if not steps:
        return (project.display_name, project.github_url, [
            f"Analysis '{analysis_id}' has no mapped survey step(s) — internal configuration gap."
        ])

    from resource_explorer.surveyors.survey_orchestrator import SurveyOrchestrator

    orch = SurveyOrchestrator(registry)
    result = orch.run(slug, steps=steps)
    errors = list(result.errors) if result.errors else []
    return (project.display_name, project.github_url, errors)


def _run_repo_survey_definition(project, analysis_id: str, registry) -> tuple[str, str, list[str]]:
    """Repo's own (d) — mirrors _run_survey_definition() (database) exactly,
    minus db_user/db_pwd (repo Survey Definition steps run entirely locally
    via repo_survey_definition_adapter.re_analysis_steps' `runner(project,
    registry, **_)` signature — no credentials to thread through)."""
    from resource_explorer.surveyors.survey_definition_executor import (
        SurveyDefinitionExecutorError,
        run_survey_definition,
    )
    from resource_explorer.surveyors.survey_definition_reader import SurveyDefinitionReaderError

    try:
        result = run_survey_definition(
            "repo", project.slug, registry=registry, survey_definition_ref=analysis_id,
        )
    except (SurveyDefinitionExecutorError, SurveyDefinitionReaderError) as exc:
        return (project.display_name, project.github_url, [str(exc)])
    errors = list(result.get("errors") or [])
    return (project.display_name, project.github_url, errors)


def _run_db_survey(slug: str, analysis_id: str, registry, next_run: str = "") -> tuple[str, str, list[str]]:
    db = registry.get_database(slug)
    if not db:
        return (slug, "", [f"Database '{slug}' not found — schedule may be stale"])

    # db_derived (Phase 1 slice 9) is zero-fetch and needs no credentials —
    # checked before the step-map/credentials path below, mirroring
    # web/routes/databases.py's per-card "Run" route. Found while wiring
    # slice 14's change comparators onto the scheduler: without this check,
    # scheduling e.g. `db_change_rates` fell through to
    # `_run_local_db_survey`, which (a) requires stored db_user/db_password
    # even though db_derived needs none, and (b) would have run the full
    # DatabaseSurveyor (steps=None, since db_change_rates isn't in
    # DATABASE_SURVEYOR_STEP_MAP) instead of the zero-fetch step — the exact
    # database-unreachable case db_derived exists to still answer for could
    # never be scheduled at all. A real, in-scope bug: it directly blocked
    # this slice's own comparator from ever running on a cadence.
    from resource_explorer.surveyors.database.db_derived import (
        DB_DERIVED_ANALYSES,
        run_db_derived,
    )

    if analysis_id in DB_DERIVED_ANALYSES:
        try:
            run_db_derived(registry, slug)
        except Exception as exc:
            return (db.display_name, db.host, [str(exc)])
        return (db.display_name, db.host, [])

    entry = _catalog_entry("database", analysis_id)
    egeria_reg = (entry or {}).get("egeria_registration") or {}

    if entry is not None and not egeria_reg.get("native_process_ref"):
        # In the catalog, but not flagged for native re-survey dispatch —
        # currently means a 'publish'-action entry (writes NEW things into
        # Egeria, e.g. registering an asset) rather than a survey. Those need
        # explicit operator intent, not a cadence — see module docstring (c).
        if entry.get("action") == "publish":
            return (db.display_name, db.host, [
                f"'{entry['name']}' writes new data into Egeria (action: publish) — excluded from "
                "scheduled runs by design. Run it manually when you intend that write."
            ])
        return _run_local_db_survey(db, registry, analysis_id)

    if entry is not None and egeria_reg.get("native_process_ref"):
        # (b) Egeria-native re-survey of an already-cataloged database.
        return _run_egeria_native_db_survey(db, next_run)

    # (d) Not in the local catalog at all — a Discovery Survey Definition's
    # qualified_name. No start_time support here yet: run_survey_definition()
    # is a multi-step, multi-engine dispatch loop, not a single initiate call
    # — plumbing start_time through it is real, separate work, not done here.
    return _run_survey_definition(db, analysis_id, registry)


def _run_local_db_survey(db, registry, analysis_id: str = "") -> tuple[str, str, list[str]]:
    # Same credential resolution as the manual survey route
    # (web/routes/databases.py) — stored db_user/db_password at registration,
    # nothing else. There is no separate env-var/config fallback inside
    # database_connection() (it does a raw credentials["user"]/["password"]
    # lookup) — a prior version of this comment claimed one existed and
    # didn't, which is exactly why this crashed with a bare KeyError instead
    # of a clear message.
    if not db.db_user or not db.db_password:
        return (db.display_name, db.host, [
            "No stored database credentials for this schedule — register the "
            "database with db_user/db_password, or run its survey manually "
            "with credentials, before scheduling it."
        ])

    from resource_explorer.surveyors.database.database_surveyor import (
        DATABASE_SURVEYOR_STEP_MAP,
        run_database_survey,
    )

    # Database per-card dispatch fix (D6 prerequisite) — only run the
    # step(s) this specific analysis_id needs, not every local check every
    # time. analysis_id="" (or an unmapped id) falls back to steps=None
    # (the full survey), matching this function's exact prior behavior for
    # any caller that doesn't have an analysis_id in hand.
    steps = DATABASE_SURVEYOR_STEP_MAP.get(analysis_id)

    errors: list[str] = []
    try:
        run_database_survey(
            db.slug, credentials={"user": db.db_user, "password": db.db_password},
            registry=registry, steps=steps,
        )
    except Exception as exc:
        errors.append(str(exc))
    return (db.display_name, db.host, errors)


def _run_egeria_native_db_survey(db, next_run: str) -> tuple[str, str, list[str]]:
    if not db.egeria_asset_guid:
        return (db.display_name, db.host, [
            "Database is not yet cataloged in Egeria (no stored egeria_asset_guid) — "
            "catalog/publish it first (Scouting's survey report or Curate), or run this "
            "survey manually, before scheduling the native re-survey."
        ])

    from datetime import datetime

    start_time = None
    if next_run:
        try:
            start_time = datetime.fromisoformat(next_run)
        except ValueError:
            pass  # fall back to firing immediately rather than failing the whole run over this

    from resource_explorer.surveyors.database.egeria_database_surveyor import (
        EgeriaDatabaseSurveyor,
        EgeriaDatabaseSurveyorError,
    )

    surveyor = EgeriaDatabaseSurveyor()
    try:
        surveyor.trigger_survey_by_guid(db.egeria_asset_guid, start_time=start_time)
    except EgeriaDatabaseSurveyorError as exc:
        return (db.display_name, db.host, [str(exc)])
    return (db.display_name, db.host, [])


def _run_survey_definition(db, analysis_id: str, registry) -> tuple[str, str, list[str]]:
    from resource_explorer.surveyors.survey_definition_executor import (
        SurveyDefinitionExecutorError,
        run_survey_definition,
    )
    from resource_explorer.surveyors.survey_definition_reader import SurveyDefinitionReaderError

    try:
        result = run_survey_definition(
            "database", db.slug, registry=registry,
            survey_definition_ref=analysis_id, db_user=db.db_user, db_pwd=db.db_password,
        )
    except (SurveyDefinitionExecutorError, SurveyDefinitionReaderError) as exc:
        return (db.display_name, db.host, [str(exc)])
    errors = list(result.get("errors") or [])
    return (db.display_name, db.host, errors)
