"""The analysis-run and stage-batch workflows.

Moved verbatim out of `web/routes/projects.py` (its `_run_single_analysis_sync`
at :632 and `_run_stage_batch_background` at :837) as part of step 2b. The
route's own docstring already said the run function had been written
FastAPI-free "so it's trivially callable from a daemon thread" — this is that
observation taken to its conclusion: the same property makes it callable from
the CLI and from the run queue, and the only thing keeping it out of both was
which file it sat in.

**This is a move, not a redesign.** The ingest-vs-survey branch, the
`has_assigned_egeria_project` auto-publish gate, the three-state `published`
value, and the summary text are unchanged. The comments explaining *why* each
of those is the way it is moved with the code, because they are the reason it
must not be "simplified" on the way past.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

#: The analysis_id a stage-batch run is logged under. Not a real catalog entry —
#: a batch runs many analyses and belongs to none of them.
STAGE_BATCH_ANALYSIS_ID = "__stage_batch__"


@dataclass
class AnalysisRunResult:
    """What one analysis run concluded.

    `published` is FOUR-state, not a bool: None (never attempted — the
    resource has no assigned Egeria project, or the run produced no
    annotations), False (attempted and failed), True (attempted, drained
    inline, and landed in Egeria before this call returned), "queued"
    (`RunsConfig.publish_inline=False` — durably enqueued, not yet applied;
    the scheduler's periodic drain will apply it, within its own cycle).
    Callers render the ☁ Publish button on False and None and hide it on True
    and "queued" alike (both mean "nothing for the user to retry by hand"),
    so collapsing True/False would hide the recovery path for a failure, and
    collapsing True/"queued" would tell a user work is done when it is only
    scheduled.

    `steps_seconds`/`publish_seconds`/`publish_mode` are instrumentation, not
    behaviour: added so a run's cost can be split into "thought" (the survey
    steps) vs "waited" (the synchronous Egeria drain) — see
    `RunsConfig.publish_inline`'s docstring for the measurement that made the
    split worth having. `publish_mode` is "not-attempted" (no assigned
    project, or no annotations), "inline" (drained synchronously — the
    unchanged default), or "enqueued" (`publish_inline=False`).
    """

    status: str  # "ok" | "error"
    summary: str = ""
    error: str = ""
    published: bool | str | None = None
    annotations: list[dict] = field(default_factory=list)
    steps_seconds: float | None = None
    publish_seconds: float | None = None
    publish_mode: str = "not-attempted"
    #: The outbox run_id this publish's annotations were enqueued under, when
    #: `published == "queued"` — empty otherwise. Carried onto the activity
    #: row (see execute_and_record_analysis) so a later drain can find that
    #: exact row again and flip `published` to True once the run's own outbox
    #: rows (and their `::links` companion) are all terminal — see
    #: registry.complete_publish_run_if_done.
    publish_run_id: str = ""
    #: The badge-table writes (project_published_annotation_types/
    #: project_published_analyses) a deferred publish has NOT yet made —
    #: carried onto the activity row so registry.complete_publish_run_
    #: if_done() can make them once the run's outbox rows verifiably land,
    #: rather than EgeriaPublisher.publish() making them at enqueue time
    #: (which flipped the ☁ Published badge before anything reached Egeria).
    #: `None` for every non-deferred/no-op path.
    pending_published_record: dict | None = None

    def to_dict(self) -> dict:
        """The exact dict shape `_run_single_analysis_sync` used to return, so
        the route wrapper and its tests keep reading the same keys."""
        out: dict = {"status": self.status, "published": self.published}
        if self.summary:
            out["summary"] = self.summary
        if self.error:
            out["error"] = self.error
        if self.annotations:
            out["annotations"] = self.annotations
        return out


@dataclass
class StageBatchResult:
    status: str  # "ok" | "error"
    stage: str
    step_keys: list[str] = field(default_factory=list)
    summary: str = ""
    errors: list[str] = field(default_factory=list)
    annotations: list[dict] = field(default_factory=list)


def resolve_analysis_plan(
    analysis_id: str, entity_type: str = "repo",
) -> tuple[bool, list[str] | None]:
    """(is_ingest, steps) for one analysis id, of ONE entity type.

    `action: "ingest"` (today only `rag_ingestion`) is not a SurveyOrchestrator
    step at all — it re-embeds content into pgvector via IncrementalIndexer.
    Resolved before the step-map lookup, the same way scheduler.py special-cases
    `action: "publish"`. Callers use the pair both to validate up front (an
    unknown id has neither) and to run.

    `entity_type` used to be unaccepted here — this always resolved against
    the repo catalog and `REPO_ANALYSIS_SOURCE_STEPS`, imported directly,
    regardless of what the caller was actually running an analysis for. A
    database/filesystem work-list batch run (`run_queue.py::_handle_analysis_run`,
    a real, already-wired feature) resolved the wrong steps this way, then
    every row in the batch failed downstream on `registry.get(slug)` — same
    shape as `_results_map_for(entity_type)` a short distance below in this
    same file, which already dispatches correctly; this follows that
    template. `get_adapter(entity_type).analysis_source_steps` is None for a
    resource type that has not declared one (filesystem, today) — treated as
    "no known source steps" rather than an error, matching the None-means-
    not-declared convention `ResourceTypeAdapter` documents for these
    provider fields.
    """
    from resource_explorer.surveyors.analysis_catalog_reader import get_analyses
    from resource_explorer.surveyors.survey_definition_executor import get_adapter

    catalog_entry = next(
        (a for a in get_analyses(entity_type, include_egeria_live=False) if a["id"] == analysis_id),
        None,
    )
    is_ingest = bool(catalog_entry and catalog_entry.get("action") == "ingest")
    # SOURCE steps: this resolves what to RUN. An analysis that owns no steps
    # (architecture_diagram) still runs its source's — off the ownership map it
    # would resolve to [] and the caller would report it undispatchable.
    source_steps_provider = get_adapter(entity_type).analysis_source_steps
    source_steps = source_steps_provider() if source_steps_provider else {}
    steps = None if is_ingest else source_steps.get(analysis_id)
    return is_ingest, steps


def resolve_stage_step_keys(stage: str) -> tuple[list[str], int]:
    """(step_keys, analysis_count) for a "Run all <Stage>" batch.

    Derived live from every local AnalysisKind catalog entry tagged this intent,
    deliberately NOT from the per-stage Egeria Survey Definitions: measured
    2026-09-03, those cover 3/3, 5/7, 8/14 and 7/10 of each stage's catalog
    entries, so pinning one under a "Run all" label would silently under-run the
    stage. Deriving it means the set stays correct as entries are added.
    """
    from resource_explorer.surveyors.analysis_catalog_reader import get_analyses
    from resource_explorer.surveyors.repo_survey_definition_adapter import (
        REPO_ANALYSIS_SOURCE_STEPS,
    )

    entries = [
        a for a in get_analyses("repo", intent=stage, include_egeria_live=False)
        if a.get("action") not in ("ingest", "publish", "profile")
    ]
    step_keys: list[str] = []
    for a in entries:
        # SOURCE steps: "Run all <Stage>" must run what each entry needs, not
        # what it owns. Identical today only because architecture_diagram's
        # source is owned by architecture_recovery, which shares its intent —
        # a coincidence, not a guarantee.
        for sk in REPO_ANALYSIS_SOURCE_STEPS.get(a["id"], []):
            if sk not in step_keys:
                step_keys.append(sk)
    return step_keys, len(entries)


def run_analysis(
    slug: str,
    analysis_id: str,
    *,
    is_ingest: bool | None = None,
    steps: list[str] | None = None,
    registry=None,
    publish: str | None = None,
) -> AnalysisRunResult:
    """Run one analysis's mapped survey step(s) and, where the resource is
    assigned to an Egeria project, auto-publish what it produced.

    `publish` is the per-RUN choice (project owner, 2026-09-13): `"wait"`
    drains the Egeria publish inline before this returns (today's default
    behaviour); `"background"` enqueues it and returns in ~seconds, with
    `published` coming back `"queued"` rather than `True`. `None` (the
    default — and what every caller that predates this choice still passes)
    defers to `RunsConfig.publish_inline`, so nothing that does not ask for
    the choice sees any change in behaviour.

    Never raises for an analysis-level failure — that comes back as
    `status="error"` — only for something genuinely unexpected, which the caller
    catches and records. Same split `_run_survey_definition_background` makes.
    """
    from resource_explorer.registry import ProjectRegistry

    if is_ingest is None or (steps is None and not is_ingest):
        resolved_ingest, resolved_steps = resolve_analysis_plan(analysis_id)
        is_ingest = resolved_ingest if is_ingest is None else is_ingest
        steps = resolved_steps if steps is None else steps

    registry = registry or ProjectRegistry()
    project = registry.get(slug)
    if not project:
        # Can't happen when a route checked synchronously first — but this now
        # also runs from a queue worker minutes after the enqueue, so "the
        # world has not moved" is a weaker assumption than it was.
        return AnalysisRunResult(status="error", error=f"Project '{slug}' not found")

    if is_ingest:
        from resource_explorer.ingestion.incremental import IncrementalIndexer
        from resource_explorer.query_cache import QueryCache

        IncrementalIndexer().refresh(project)
        QueryCache().invalidate_project(slug)
        return AnalysisRunResult(
            status="ok", summary="Re-ingested into pgvector.", published=None,
        )

    from resource_explorer.surveyors.survey_orchestrator import SurveyOrchestrator

    # Timed separately from publish below so a run's cost can be split into
    # "thought" (the survey steps) vs "waited" (the synchronous Egeria drain,
    # when RunsConfig.publish_inline leaves it inline) — see that flag's
    # docstring for the 2026-09-13 measurement that made the split worth
    # having. Recorded even on the error return just below: the steps DID run.
    steps_start = time.perf_counter()
    result = SurveyOrchestrator(registry).run(slug, steps=steps)
    steps_seconds = time.perf_counter() - steps_start
    if result.errors:
        return AnalysisRunResult(
            status="error", error="; ".join(result.errors), steps_seconds=steps_seconds,
        )
    summary = f"{len(result.annotations)} annotation(s)."

    # Auto-publish, gated the same way survey_definition_executor.py's Survey
    # Definition path is (has_assigned_egeria_project): an Assessment/Analysis
    # "Run →" against an assigned resource reaches Egeria without a separate
    # manual Publish click. Scoped to exactly the steps that just ran (this
    # `result`, not a fresh full survey), same as the manual Publish button's
    # own `steps` scoping — so last-published attribution
    # (get_last_published_annotation_types) stays accurate to what actually ran.
    # Publish failure must not turn an otherwise-successful survey into a
    # reported error: the findings are real and stored either way.
    published = None
    publish_seconds = None
    publish_mode = "not-attempted"
    publish_run_id = ""
    pending_published_record = None
    if result.annotations and registry.has_assigned_egeria_project("repo", slug):
        from resource_explorer.config import get_config

        # The per-run choice, threaded through from the route: "wait"/
        # "background" override RunsConfig.publish_inline for this run only;
        # not asked (None) falls back to the config default, which is what
        # every pre-existing caller still does.
        if publish == "background":
            defer_drain = True
        elif publish == "wait":
            defer_drain = False
        else:
            defer_drain = not get_config().runs.publish_inline
        publish_mode = "enqueued" if defer_drain else "inline"
        publish_start = time.perf_counter()
        try:
            from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

            # Already off any FastAPI event loop here — this function runs in a
            # worker thread, never on the loop. No asyncio.to_thread: pyegeria's
            # synchronous methods drive their own event loop internally, which
            # is what made the OLD synchronous route (this call wrapped in
            # asyncio.to_thread from inside a running loop) raise "this event
            # loop is already running" and fail auto-publish on every
            # Egeria-bound project reached through this route, softly, into
            # `summary`, with the run still reporting ok.
            publisher = EgeriaPublisher(registry=registry)
            publisher.publish(result, defer_drain=defer_drain)
            # `is True`, not truthy: a test double patching EgeriaPublisher
            # wholesale (several do, across this file and
            # test_analysis_run_auto_publish.py) has no `publish_deferred`
            # attribute of its own, and a bare Mock auto-vivifies one that is
            # truthy by default — `if publisher.publish_deferred:` would have
            # silently turned every one of those tests' expected `published
            # is True` into "queued". The real attribute is only ever the
            # literal bool `True`/`False` (set in egeria_publisher.py's
            # __init__), so this is exact for it and safe for a loose mock.
            if publisher.publish_deferred is True:
                # Enqueued, not applied. "queued" is a THIRD state, not True
                # — the annotations are durable but have not landed in Egeria
                # yet, and every reader of `published` must be able to tell
                # the difference (see config.py's RunsConfig.publish_inline
                # docstring for the measurement, and this run-in-background
                # design for the per-run choice that can now also cause it).
                published = "queued"
                publish_run_id = getattr(publisher, "publish_run_id", "") or ""
                pending_published_record = getattr(publisher, "pending_published_record", None)
                summary = summary.rstrip(".") + (
                    f"; {len(result.annotations)} Egeria write(s) queued for "
                    "publish — next drain ≤ 15 min."
                )
            else:
                published = True
        except Exception as exc:
            published = False
            summary += f" (⚠ auto-publish to Egeria failed: {exc})"
            log.warning("Auto-publish failed for %s/%s: %s", slug, analysis_id, exc)
        finally:
            publish_seconds = time.perf_counter() - publish_start

    # Carried out of here so the caller can write them onto the activity entry.
    # Without this the RFA drawer never saw a single annotation from an
    # Analyses-card run — see registry.update_activity_status() for the
    # measurement.
    from resource_explorer.surveyors.survey_report import summarise_annotations

    # Belt and braces with summarise_annotations' own skip: a run that produced
    # and stored real findings must never be reported as failed because the
    # sentence describing it could not be built.
    try:
        ann_summary = summarise_annotations(result.annotations)
    except Exception as exc:
        log.warning("Could not summarise annotations for %s/%s: %s", slug, analysis_id, exc)
        ann_summary = []

    return AnalysisRunResult(
        status="ok", summary=summary, published=published, annotations=ann_summary,
        steps_seconds=steps_seconds, publish_seconds=publish_seconds,
        publish_mode=publish_mode, publish_run_id=publish_run_id,
        pending_published_record=pending_published_record,
    )


def run_stage_batch(
    slug: str, stage: str, step_keys: list[str], *, registry=None,
) -> StageBatchResult:
    """Run every step of one Funnel stage as a single orchestrator call.

    Uses the adapter's `_run_batch`, the same primitive
    survey_definition_executor.py uses to run a multi-step Survey Definition in
    one SurveyOrchestrator.run() — this just derives its step_keys from the
    catalog's intent tags instead of from an authored Survey Definition, so it
    cannot drift out of sync as analyses are added to a stage.
    """
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.repo_survey_definition_adapter import _run_batch
    from resource_explorer.surveyors.survey_report import summarise_annotations

    registry = registry or ProjectRegistry()
    project = registry.get(slug)
    result = _run_batch(project, registry, step_keys)
    ann_summary = summarise_annotations(result["annotations"])
    errors = result.get("errors") or []
    # An error only makes the whole batch an error when NOTHING was produced —
    # a 12-step batch where one step failed still wrote 11 steps' findings.
    status = "error" if errors and not result["annotations"] else "ok"
    n = len(step_keys)
    summary = f"Ran all {n} {stage} step(s)" + (f" — {len(errors)} error(s)" if errors else "")
    return StageBatchResult(
        status=status, stage=stage, step_keys=list(step_keys), summary=summary,
        errors=errors, annotations=ann_summary,
    )


# ── recording onto the activity entry ────────────────────────────────────────
#
# The route used to do this inline in its background thread. It lives here so
# the queue worker records a run in EXACTLY the same shape a route-spawned
# thread did — the frontend polls GET /api/activity/{id} and reads the run's
# result out of that entry's `detail`, and that contract must not depend on
# which process ran the work.


def execute_and_record_analysis(slug: str, analysis_id: str, activity_id: str,
                                *, registry=None, publish: str | None = None,
                                entity_type: str = "repo",
                                ) -> AnalysisRunResult:
    """Run one analysis and write its terminal status onto `activity_id`.

    `publish` ("wait" | "background" | None) is the per-run choice, carried
    here from the run queue's `target` dict (see run_queue.py's
    `_handle_analysis_run`) — passed straight through to `run_analysis`.

    `entity_type` defaults to "repo" for every caller that predates it —
    thread through the `target` dict's own `entity_type` (see
    `WorkLists.enqueue_batch`) for a database/filesystem batch run, so
    `resolve_analysis_plan` below resolves that resource type's steps rather
    than always the repo's.
    """
    from resource_explorer.registry import ProjectRegistry

    registry = registry or ProjectRegistry()
    is_ingest, steps = resolve_analysis_plan(analysis_id, entity_type)
    try:
        result = run_analysis(
            slug, analysis_id, is_ingest=is_ingest, steps=steps, registry=None,
            publish=publish,
        )
    except Exception as exc:  # pragma: no cover — genuinely unexpected
        log.exception("Analysis run crashed for %s/%s", slug, analysis_id)
        registry.update_activity_status(
            activity_id, "error", summary=f"'{analysis_id}' run crashed: {exc}",
            detail=json.dumps({"analysis_id": analysis_id, "published": None,
                               "error": str(exc)}),
        )
        return AnalysisRunResult(status="error", error=str(exc))

    summary = result.summary or result.error or ""
    detail = {
        "analysis_id": analysis_id,
        "published": result.published,
        "steps_seconds": result.steps_seconds,
        "publish_seconds": result.publish_seconds,
        "publish_mode": result.publish_mode,
    }
    if result.status == "error":
        detail["error"] = result.error or summary
    else:
        detail["message"] = summary
    # Carried so registry.complete_publish_run_if_done() can make the badge-
    # table writes (project_published_annotation_types/_analyses) at the
    # point the run's outbox rows actually land, instead of EgeriaPublisher.
    # publish() making them at enqueue time — see that method's own comment.
    # Only present when queued; a run that never deferred has nothing to
    # apply later.
    if result.published == "queued" and result.pending_published_record:
        detail["pending_published_record"] = result.pending_published_record
    # publish_run_id only when queued — see registry.complete_publish_run_
    # if_done, which reads it off this exact activity row to flip
    # `published` from "queued" to True once the run's outbox rows land.
    publish_run_id = result.publish_run_id if result.published == "queued" else ""
    registry.update_activity_status(
        activity_id, result.status, summary=summary, detail=json.dumps(detail),
        annotations=result.annotations or None, publish_run_id=publish_run_id,
    )
    return result


def execute_and_record_stage_batch(slug: str, stage: str, step_keys: list[str],
                                   activity_id: str, *, registry=None) -> StageBatchResult:
    """Run a stage batch and write its terminal status onto `activity_id`."""
    from resource_explorer.registry import ProjectRegistry

    registry = registry or ProjectRegistry()
    try:
        result = run_stage_batch(slug, stage, step_keys, registry=registry)
    except Exception as exc:  # pragma: no cover — genuinely unexpected
        log.exception("Stage-batch run crashed for %s/%s", slug, stage)
        registry.update_activity_status(
            activity_id, "error", summary=f"Run all {stage} crashed: {exc}",
            detail=json.dumps({"stage": stage, "step_keys": step_keys, "error": str(exc)}),
        )
        return StageBatchResult(status="error", stage=stage, step_keys=list(step_keys),
                                errors=[str(exc)])

    registry.update_activity_status(
        activity_id, result.status, summary=result.summary,
        detail=json.dumps({"stage": stage, "step_keys": step_keys, "errors": result.errors}),
        annotations=result.annotations,
    )
    return result


@dataclass
class DatabaseAnalysisRunResult:
    """What one database per-card analysis run concluded.

    Deliberately its own (smaller) dataclass rather than a reuse of
    `AnalysisRunResult` above: that one's `published`/`steps_seconds`/
    `publish_mode`/`publish_run_id` fields all describe repo's auto-publish-
    on-run behaviour (`run_analysis`'s `has_assigned_egeria_project("repo",
    slug)` gate), which a database analysis run does not have — publishing a
    database survey to Egeria stays its own explicit `POST /{slug}/publish`
    action (`web/routes/databases.py`), unchanged by this. Reusing the repo
    dataclass would either carry fields that are always `None`/`"not-
    attempted"` for every database row, or invite a future edit to wire up
    auto-publish for database runs by copying repo's gate verbatim — which
    would be wrong, since it is a genuinely different, deliberate design
    choice, not a gap.
    """

    status: str  # "ok" | "error"
    summary: str = ""
    error: str = ""
    annotations: list[dict] = field(default_factory=list)


def run_database_analysis(slug: str, analysis_id: str, *, registry=None) -> DatabaseAnalysisRunResult:
    """Run one database per-card analysis's mapped step(s) — the database
    equivalent of `run_analysis` above.

    Two local shapes, mirroring `web/routes/databases.py`'s
    `run_single_database_analysis` (which now only validates synchronously
    and enqueues; this is what actually runs, from the run queue worker):

    * `db_derived` (Phase 1 slice 9) — zero-fetch, reads stored rows only,
      needs no credentials. `run_db_derived` persists nothing itself (it
      never has — see its own module docstring); this function's only new
      behaviour versus the old inline route is recording the run onto the
      activity entry.
    * Everything in `DATABASE_SURVEYOR_STEP_MAP` — needs the database's
      stored credentials and actually opens a connection via
      `run_database_survey`.

    Never raises for an analysis-level failure — that comes back as
    `status="error"`, exactly like `run_analysis` — only for something
    genuinely unexpected, which the caller (`execute_and_record_database_
    analysis` below) catches and records.
    """
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.database.database_surveyor import (
        DATABASE_SURVEYOR_STEP_MAP,
        run_database_survey,
    )
    from resource_explorer.surveyors.database.db_derived import (
        DB_DERIVED_ANALYSES,
        run_db_derived,
    )

    registry = registry or ProjectRegistry()
    db = registry.get_database(slug)
    if not db:
        # Can't happen when the route checked synchronously first — but this
        # now also runs from a queue worker after the enqueue, so "the world
        # has not moved" is a weaker assumption than it was, same reasoning
        # as run_analysis's own re-check above.
        return DatabaseAnalysisRunResult(status="error", error=f"Database '{slug}' not found")

    from resource_explorer.surveyors.survey_report import summarise_annotations

    if analysis_id in DB_DERIVED_ANALYSES:
        try:
            derived_result = run_db_derived(registry, slug)
        except Exception as exc:
            return DatabaseAnalysisRunResult(status="error", error=str(exc))
        check = (derived_result.get("derived") or {}).get(analysis_id) or {}
        annotations = derived_result.get("annotations") or []
        try:
            ann_summary = summarise_annotations(annotations)
        except Exception as exc:  # a display-summary failure must not fail a real result
            log.warning("Could not summarise db_derived annotations for %s/%s: %s", slug, analysis_id, exc)
            ann_summary = []
        return DatabaseAnalysisRunResult(
            status="ok",
            summary=(
                f"{len(annotations)} annotation(s) derived from stored rows (no fetch). "
                f"{analysis_id}: {check.get('state', 'unknown')}."
            ),
            annotations=ann_summary,
        )

    if analysis_id not in DATABASE_SURVEYOR_STEP_MAP:
        return DatabaseAnalysisRunResult(
            status="error",
            error=f"Analysis '{analysis_id}' has no local survey step(s) mapped.",
        )

    if not db.db_user or not db.db_password:
        return DatabaseAnalysisRunResult(
            status="error",
            error="No stored database credentials — register the database with "
                  "db_user/db_password, or run a full survey with credentials, first.",
        )

    steps = DATABASE_SURVEYOR_STEP_MAP[analysis_id]
    try:
        result = run_database_survey(
            slug, credentials={"user": db.db_user, "password": db.db_password},
            registry=registry, steps=steps,
        )
    except Exception as exc:
        return DatabaseAnalysisRunResult(status="error", error=str(exc))

    non_fatal = result.get("errors") or []
    annotations = result.get("annotations") or []
    try:
        ann_summary = summarise_annotations(annotations)
    except Exception as exc:
        log.warning("Could not summarise database annotations for %s/%s: %s", slug, analysis_id, exc)
        ann_summary = []
    return DatabaseAnalysisRunResult(
        status="ok",
        summary=f"{len(annotations)} annotation(s)." + (
            f" ({len(non_fatal)} non-fatal error(s))" if non_fatal else ""
        ),
        annotations=ann_summary,
    )


def execute_and_record_database_analysis(slug: str, analysis_id: str, activity_id: str,
                                          *, registry=None) -> DatabaseAnalysisRunResult:
    """Run one database analysis and write its terminal status onto
    `activity_id` — the database equivalent of `execute_and_record_analysis`
    above, called from the run queue's `database_analysis_run` handler
    (run_queue.py::_handle_database_analysis_run)."""
    from resource_explorer.registry import ProjectRegistry

    registry = registry or ProjectRegistry()
    try:
        result = run_database_analysis(slug, analysis_id, registry=registry)
    except Exception as exc:  # pragma: no cover — genuinely unexpected
        log.exception("Database analysis run crashed for %s/%s", slug, analysis_id)
        registry.update_activity_status(
            activity_id, "error", summary=f"'{analysis_id}' run crashed: {exc}",
            detail=json.dumps({"analysis_id": analysis_id, "published": None, "error": str(exc)}),
        )
        return DatabaseAnalysisRunResult(status="error", error=str(exc))

    detail = {"analysis_id": analysis_id, "published": None}
    if result.status == "error":
        detail["error"] = result.error or result.summary
    else:
        detail["message"] = result.summary
    registry.update_activity_status(
        activity_id, result.status, summary=result.summary or result.error or "",
        detail=json.dumps(detail), annotations=result.annotations or None,
    )
    return result


def _humanise_age(seconds: float) -> str:
    """"3 minutes ago" / "2 hours ago" / "6 days ago" — coarse on purpose. The
    first version printed minutes at every scale and produced "1827 minutes
    ago", which is a number a reader has to do arithmetic on before it means
    anything."""
    if seconds < 60:
        return "less than a minute ago"
    if seconds < 3600:
        n, unit = int(seconds // 60), "minute"
    elif seconds < 86400:
        n, unit = int(seconds // 3600), "hour"
    else:
        n, unit = int(seconds // 86400), "day"
    return f"{n} {unit}{'s' if n != 1 else ''} ago"


@dataclass(frozen=True)
class Freshness:
    """Whether an analysis's data is recent enough that running it again would
    buy nothing.

    Deliberately a verdict PLUS its evidence, not a bare bool: a Run that
    declines to run has to be able to say why, and say it about the analysis
    that actually produced the data. `via` is the id whose run supplied the
    freshness — for a derived analysis that is its SOURCE, so
    `architecture_diagram` reports being fresh because `architecture_recovery`
    ran, rather than claiming a run of its own it never had.
    """

    fresh: bool
    #: "never-run" / "stale" / "fresh" — three states, because "we have no idea
    #: when this last ran" and "it ran, a while ago" are different reasons to
    #: proceed and only one of them is a measurement.
    state: str
    age_seconds: float | None
    last_run_at: str
    via: str

    def reason(self, analysis_id: str) -> str:
        """One sentence a caller can hand straight to a user."""
        if self.state == "never-run":
            return f"'{analysis_id}' has no recorded run, so it will run now."
        ago = _humanise_age(self.age_seconds or 0)
        source = "" if self.via == analysis_id else f" (via '{self.via}')"
        if self.fresh:
            return (f"'{analysis_id}' already has data from {ago}{source}; "
                    f"not running it again. Pass force=true to run anyway.")
        return f"'{analysis_id}' last produced data {ago}{source}."


@dataclass(frozen=True)
class RunCost:
    """What a re-run of an analysis would cost, and how we know.

    The designer's ruling on the freshness gate (FUNNEL-COST rulings,
    2026-09-13): "a gate that only says 'too fresh' reads as an obstacle; one
    that names the price reads as the system being careful with your money."
    So the skip carries the price — and, because a declared figure sitting
    where a measured one is expected is the failure the whole measurement
    spec exists to prevent, it carries the BASIS too. Three, not two:

    * ``measured`` — median wall time of this analysis's succeeded ``runs``
      rows (finished − started; never enqueued, which measures queue depth);
    * ``declared`` — no succeeded run to measure, so the catalog's
      ``run_time`` word (fast / minutes / async) stands in, labelled as such;
    * ``unknown`` — neither: not in the catalog and never run.

    Medians, never means: one sixteen-minute Egeria survey would otherwise
    carry the figure for every run of that analysis.

    **The split (designer's ruling, 2026-09-13):** "the wall clock belongs
    where someone is deciding — the cost preview and the freshness price —
    and ALWAYS SPLIT, never as one figure." Measured 2026-09-13: a run of
    `language_file_classification` took steps 0.06s, publish 92.3s (53
    writes, 1.5s median) — a single figure mixes 0.06s of analysis with ~80s
    of publishing, and publish scales with annotation COUNT, not with tier.
    `steps_seconds`/`publish_seconds` are the medians of that split across the
    same rows `seconds` above is drawn from (`split_runs` says how many); they
    are `None` until at least one activity row carries the instrumentation
    (added when `execute_and_record_analysis` started writing it), and the
    sentence falls back to the unsplit figure until then — a fabricated split
    would be worse than none.
    """

    seconds: float | None
    basis: str
    runs: int
    declared: str
    #: Whose runs supplied the figure. For a derived analysis that is its
    #: SOURCE — `architecture_diagram` costs what `architecture_recovery`
    #: costs, because that is what a re-run actually executes.
    via: str
    steps_seconds: float | None = None
    publish_seconds: float | None = None
    #: How many activity_log rows carried the split — the "median of N runs"
    #: in the split sentence, which can differ from `runs` (drawn from a
    #: different table, `runs`, and only some activity rows predate the
    #: instrumentation).
    split_runs: int = 0

    def sentence(self) -> str:
        if self.basis == "measured" and self.seconds is not None:
            n = f"median of {self.runs} run{'s' if self.runs != 1 else ''}"
            if self.steps_seconds is not None and self.publish_seconds is not None:
                # The two halves and the total must come from the SAME rows,
                # or the sentence carries numbers that disagree — the first
                # draft said "0.1s to run and 1m 32s to publish; about 2m 36s
                # in all", because "in all" was the `runs`-table median over
                # five runs (three of them before the platform redeploy) while
                # the split came from the one instrumented row. So the total
                # here is the sum of the split, and `seconds` (the whole-run
                # median, still what the button costs on average) stays in
                # the payload for callers that want it, not in this sentence.
                sn = f"median of {self.split_runs} run{'s' if self.split_runs != 1 else ''}"
                total = self.steps_seconds + self.publish_seconds
                # "about 1m 32s to publish — about 1m 32s in all" states one
                # figure twice when the run half rounds away, and a total that
                # restates its larger half verbatim reads as a bug in the
                # sentence (designer, 2026-09-13). Rule: the total names its
                # dominant half instead of repeating it, once that half is
                # more than nine tenths of the whole.
                if total > 0 and max(self.steps_seconds, self.publish_seconds) > 0.9 * total:
                    dominant = "publishing" if self.publish_seconds >= self.steps_seconds else "running"
                    tail = (f"{_humanise_duration(total)} in all, nearly all of it "
                            f"{dominant} ({sn}).")
                else:
                    tail = f"about {_humanise_duration(total)} in all ({sn})."
                return (
                    f"A re-run takes about {_humanise_split_seconds(self.steps_seconds)} "
                    f"to run and about {_humanise_split_seconds(self.publish_seconds)} "
                    f"to publish — {tail}"
                )
            return f"A re-run costs about {_humanise_duration(self.seconds)} ({n})."
        if self.basis == "declared" and self.declared:
            return (f"A re-run is declared '{self.declared}' in the catalog — "
                    f"not yet measured.")
        return "What a re-run would cost is not known — never measured, not declared."


def _humanise_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{max(1, round(seconds))}s"
    m, s = divmod(round(seconds), 60)
    return f"{m}m {s:02d}s"


def _humanise_split_seconds(seconds: float) -> str:
    """Like `_humanise_duration`, but a sub-second figure keeps one decimal
    instead of rounding up to "1s" — a 0.06s step rounded that way would read
    as sixteen times its real cost, exactly the distortion the split exists
    to remove."""
    if seconds < 1:
        return f"{seconds:.1f}s"
    return _humanise_duration(seconds)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return (ordered[mid] if len(ordered) % 2
            else (ordered[mid - 1] + ordered[mid]) / 2)


def estimate_run_cost(registry, analysis_id: str, *, resource_type: str = "repo",
                      sample: int = 500) -> RunCost:
    """How long a re-run of `analysis_id` takes, from the queue's own record.

    Reads succeeded ``runs`` rows for the analysis and — for a derived
    analysis — for its sources, since those are the steps a re-run executes.
    Falls back to the catalog's declared ``run_time`` and says so; never
    presents a declared word as a measurement.
    """
    from datetime import datetime

    from resource_explorer.surveyors.analysis_catalog_reader import get_analyses
    from resource_explorer.surveyors.repo_survey_definition_adapter import (
        repo_analysis_derived_sources,
    )

    candidates = [analysis_id, *repo_analysis_derived_sources(analysis_id)]
    durations: dict[str, list[float]] = {aid: [] for aid in candidates}
    # No broad except here: if the queue or the catalog cannot be read, the
    # skip fails loudly rather than reporting a price of "unknown" that looks
    # like a measurement of absence. Same registry the route already depends on.
    rows = registry.list_runs(kind="analysis_run", state="succeeded", limit=sample)
    for row in rows:
        try:
            target = json.loads(row.get("target") or "{}")
        except ValueError:
            continue
        aid = target.get("analysis_id")
        if aid not in durations:
            continue
        t0, t1 = row.get("started_at") or "", row.get("finished_at") or ""
        if not t0 or not t1:
            continue
        try:
            secs = (datetime.fromisoformat(t1) - datetime.fromisoformat(t0)).total_seconds()
        except ValueError:
            continue
        if secs >= 0:
            durations[aid].append(secs)

    # The analysis's own runs first; a derived analysis with no runs of its
    # own costs what its source costs.
    for aid in candidates:
        if durations[aid]:
            steps_seconds = publish_seconds = None
            split_runs = 0
            split_rows = registry.analysis_run_activity_seconds([aid]).get(aid, [])
            if split_rows:
                split_runs = len(split_rows)
                steps_vals = [s for s, _ in split_rows if s is not None]
                publish_vals = [p for _, p in split_rows if p is not None]
                if steps_vals:
                    steps_seconds = _median(steps_vals)
                if publish_vals:
                    publish_seconds = _median(publish_vals)
            return RunCost(_median(durations[aid]), "measured", len(durations[aid]),
                           _declared_run_time(analysis_id, resource_type, get_analyses), aid,
                           steps_seconds=steps_seconds, publish_seconds=publish_seconds,
                           split_runs=split_runs)

    declared = _declared_run_time(analysis_id, resource_type, get_analyses)
    return RunCost(None, "declared" if declared else "unknown", 0, declared, analysis_id)


def _declared_run_time(analysis_id: str, resource_type: str, get_analyses) -> str:
    for entry in get_analyses(resource_type, include_egeria_live=False):
        if entry.get("id") == analysis_id:
            return str(entry.get("run_time") or "")
    return ""


def assess_freshness(registry, entity_type: str, slug: str, analysis_id: str,
                     max_age_seconds: int | None = None) -> Freshness:
    """Is `analysis_id`'s data newer than the freshness threshold?

    Reads `get_analysis_last_run`, which since 2026-09-09 credits a derived
    analysis's run to the source whose steps it actually ran — this function
    depends on that fix and would otherwise report the recovery stale moments
    after a diagram run had rewritten its data.

    Considers the analysis's SOURCES as well as itself, in the other direction:
    `architecture_diagram` derives from `architecture_recovery`'s steps, so a
    recent run of the recovery makes the diagram's data current even though the
    diagram itself has never been run. Takes the most recent of the two.

    A run recorded as `error` does NOT count as freshness — its data is the old
    data, and refusing to re-run after a failure is the one behaviour nobody
    would want.
    """
    from datetime import UTC, datetime

    from resource_explorer.config import get_config
    from resource_explorer.surveyors.repo_survey_definition_adapter import (
        repo_analysis_derived_sources,
    )

    if max_age_seconds is None:
        max_age_seconds = get_config().runs.freshness_seconds

    runs = registry.get_analysis_last_run(entity_type, slug)
    candidates = [analysis_id, *repo_analysis_derived_sources(analysis_id)]

    best_ts, best_via = None, analysis_id
    for aid in candidates:
        row = runs.get(aid) or {}
        if row.get("last_run_status") == "error":
            continue
        raw = row.get("last_run_at") or ""
        if not raw:
            continue
        try:
            ts = datetime.fromisoformat(str(raw))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if best_ts is None or ts > best_ts:
            best_ts, best_via = ts, aid

    if best_ts is None:
        return Freshness(False, "never-run", None, "", analysis_id)

    age = (datetime.now(UTC) - best_ts).total_seconds()
    # A negative age means a clock skew or a future-dated row; treat it as stale
    # rather than as infinitely fresh, so a bad timestamp can never wedge an
    # analysis into never running again.
    fresh = 0 <= age < max_age_seconds
    return Freshness(fresh, "fresh" if fresh else "stale", age,
                     best_ts.isoformat(), best_via)


def build_analysis_last_activity(registry, entity_type: str, slug: str) -> dict[str, dict]:
    """{analysis_id: {last_run_at, last_run_status, last_published_at, ...}}
    for every local analysis_catalog entry of `entity_type` against `slug` —
    generalized out of `projects.py`'s `GET /{slug}/analyses/last-activity`
    (repo-only route, added first) so database and filesystem get the same
    per-analysis "Last run"/"Published" badge data their own Analyses cards
    were missing entirely (docs/Backlog.md: in classic, a survey visibly ran
    — 200 OK, confirmed via network tab — and every per-analysis card still
    showed no run/result indicator, because `_loadAnalysisCatalogPanel()`
    only ever fetched this for `resourceType === 'repo'`).

    `last_run_*` is real, attributed data for every entity_type now that
    `ProjectRegistry.get_analysis_last_run()` is generalized (see its
    docstring and `database/survey_definition_adapter.py`'s
    `DATABASE_SURVEYOR_STEP_MAP`).

    `last_published_at`/`last_published_scope` are NOT yet real data for
    database/filesystem: `record_published_annotation_types()`/
    `record_published_analyses()` — the tables this reads — are written only
    from `EgeriaPublisher` on the repo publish path (`surveyors/
    egeria_publisher.py`); `EgeriaDatabaseSurveyor.publish_step_annotations`
    and the filesystem equivalent never call them. So for database/
    filesystem this always returns empty publish fields today — an honest
    "not established", not a wrong "never published" — until that publish
    path is wired up too (logged as a follow-up in docs/Backlog.md rather
    than guessed at here; building the two-tier recorded/shared fallback the
    repo endpoint uses on top of data that plain doesn't exist yet would be
    exactly the "Never run"/"Published today" contradiction this endpoint
    exists to avoid, aimed at the wrong field).

    `publish_stale` is likewise always False for non-repo entity_types today:
    nothing writes a `f"{entity_type}_publish"` Egeria linkage row for
    database/filesystem (`get_egeria_linkage` has no such writer outside the
    repo path), so there is no staleness signal to report — never a lie,
    since a card only shows the flag alongside a real `last_published_at`,
    which is itself always empty here.
    """
    from resource_explorer.surveyors.analysis_catalog_reader import get_analyses

    last_run = registry.get_analysis_last_run(entity_type, slug)
    unattributed = last_run.pop("__unattributed_surveys__", {}).get("count", 0)
    published_by_type = registry.get_last_published_annotation_types(slug)
    published_by_analysis = registry.get_last_published_analyses(slug)
    publish_linkage = registry.get_egeria_linkage(f"{entity_type}_publish", slug) or {}
    publish_stale = publish_linkage.get("status") == "stale"

    result: dict[str, dict] = {}
    for a in get_analyses(entity_type, include_egeria_live=False):
        run = last_run.get(a["id"], {})
        recorded = published_by_analysis.get(a["id"])
        own_types = a.get("annotation_types") or []
        shared = [t for t in own_types if t in published_by_type]
        if recorded:
            pub_at, pub_scope = recorded, "analysis"
        elif shared:
            pub_at, pub_scope = max(published_by_type[t] for t in shared), entity_type
        else:
            pub_at, pub_scope = "", ""
        result[a["id"]] = {
            "last_run_at": run.get("last_run_at", ""),
            "last_run_status": run.get("last_run_status", ""),
            "last_run_basis": ("measured" if run.get("last_run_at")
                               else "not_established" if unattributed else "never_run"),
            "unattributed_surveys": unattributed,
            "last_run_via": run.get("last_run_via", ""),
            "last_run_derived_from": run.get("last_run_derived_from", ""),
            "last_run_partial": run.get("last_run_partial", False),
            "last_published_at": pub_at,
            "last_published_scope": pub_scope,
            "publish_stale": bool(pub_at) and publish_stale,
        }
    result["__auto_publishes__"] = {
        "auto_publishes": registry.has_assigned_egeria_project(entity_type, slug),
    }
    return result


def _results_map_for(entity_type: str):
    """(results_map, headline_map) for `entity_type` — the three per-type
    constants `build_survey_results` reads results/headlines from."""
    if entity_type == "database":
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_HEADLINE_MAP,
            DATABASE_ANALYSIS_RESULTS_MAP,
        )
        return DATABASE_ANALYSIS_RESULTS_MAP, DATABASE_ANALYSIS_HEADLINE_MAP
    if entity_type == "filesystem":
        from resource_explorer.surveyors.filesystem.survey_definition_adapter import (
            FILESYSTEM_ANALYSIS_HEADLINE_MAP,
            FILESYSTEM_ANALYSIS_RESULTS_MAP,
        )
        return FILESYSTEM_ANALYSIS_RESULTS_MAP, FILESYSTEM_ANALYSIS_HEADLINE_MAP
    from resource_explorer.surveyors.repo_survey_definition_adapter import (
        REPO_ANALYSIS_HEADLINE_MAP,
        REPO_ANALYSIS_RESULTS_MAP,
    )
    return REPO_ANALYSIS_RESULTS_MAP, REPO_ANALYSIS_HEADLINE_MAP


def build_survey_results(
    registry, entity_type: str, slug: str, stage: str = "", include_empty: bool = False,
) -> dict:
    """Tier 2 — the Survey Results ("By analysis") dashboards for any
    entity_type, generalized out of `projects.py`'s `_survey_results_sync`
    (repo-only route, added first; see docs/Backlog.md's "By analysis" was
    repo-only entry) the same way `build_analysis_last_activity` above
    generalized the analyses/last-activity route.

    For `entity_type == "repo"` this reproduces the original route exactly —
    same curated `SURVEY_RESULT_DASHBOARDS` groupings, same stage/perspective/
    publish-state derivation.

    Database and filesystem have no such curated groupings (`docs/survey-
    results-dashboard-plan.md`'s dashboard design was written for repo only,
    and building repo's kind of multi-analysis, themed dashboard for the
    other two entity_types is real design work, not a generalization of this
    function). So for those two entity_types, this SYNTHESIZES one dashboard
    per analysis_id that has an entry in the type's own *_ANALYSIS_RESULTS_MAP
    — literally "by analysis", which is what the pane is named and what the
    frontend gate (`paneNeedsRepoBackend` in app.js) has been describing this
    gap as. Every other field (has_results, last_published_at, publish_stale,
    last_surveyed_at) is computed the same way the repo branch computes it,
    just scoped to that one analysis_id's own annotation_types instead of a
    dashboard's union.
    """
    from resource_explorer.surveyors.analysis_catalog_reader import get_analyses

    results_map, headline_map = _results_map_for(entity_type)
    published_by_type = registry.get_last_published_annotation_types(slug)
    publish_stale = (registry.get_egeria_linkage(f"{entity_type}_publish", slug) or {}).get("status") == "stale"
    last_surveyed_at = ""
    getter = {
        "repo": registry.get, "database": registry.get_database, "filesystem": registry.get_filesystem,
    }.get(entity_type)
    entity = getter(slug) if getter else None
    if entity is not None:
        last_surveyed_at = getattr(entity, "last_surveyed_at", "") or ""

    dashboards: list[dict] = []

    if entity_type == "repo":
        from resource_explorer.surveyors.repo_survey_definition_adapter import (
            SURVEY_RESULT_DASHBOARDS,
            get_dashboard_annotation_types,
            get_dashboard_perspectives,
            get_dashboard_stages,
        )

        for dashboard in SURVEY_RESULT_DASHBOARDS.values():
            stages = get_dashboard_stages(dashboard.analysis_ids)
            if stage and stage not in stages:
                continue
            analyses = _read_analyses(registry, slug, dashboard.analysis_ids, results_map, headline_map)
            has_results = any(_results_have_data(a["results"]) for a in analyses)
            if not has_results and not include_empty:
                continue
            dashboard_types = get_dashboard_annotation_types(dashboard.analysis_ids)
            last_published_at = max(
                (published_by_type[t] for t in dashboard_types if t in published_by_type),
                default="",
            )
            dashboards.append({
                "id": dashboard.id,
                "title": dashboard.title,
                "description": dashboard.description,
                "render": dashboard.render,
                "custom_renderer": dashboard.custom_renderer,
                "perspectives": get_dashboard_perspectives(dashboard.analysis_ids),
                "stages": stages,
                "has_results": has_results,
                "analyses": analyses,
                "last_published_at": last_published_at,
                "publish_stale": bool(last_published_at) and publish_stale,
                "last_surveyed_at": last_surveyed_at,
            })
        return {"slug": slug, "stage": stage, "dashboards": dashboards}

    # database / filesystem: one synthesized dashboard per analysis_id that
    # this entity_type actually has a results reader for.
    catalog_by_id = {a["id"]: a for a in get_analyses(entity_type, include_egeria_live=False)}
    for analysis_id in results_map:
        entry = catalog_by_id.get(analysis_id)
        analyses = _read_analyses(registry, slug, [analysis_id], results_map, headline_map)
        this_stage = ((entry or {}).get("intent") or "").strip().lower()
        if stage and stage != this_stage:
            continue
        has_results = any(_results_have_data(a["results"]) for a in analyses)
        if not has_results and not include_empty:
            continue
        annotation_types = (entry or {}).get("annotation_types") or []
        last_published_at = max(
            (published_by_type[t] for t in annotation_types if t in published_by_type),
            default="",
        )
        dashboards.append({
            "id": analysis_id,
            "title": (entry or {}).get("name") or analysis_id.replace("_", " ").title(),
            "description": (entry or {}).get("description") or "",
            "render": "custom",
            "custom_renderer": "",
            "perspectives": [],
            "stages": [this_stage] if this_stage else [],
            "has_results": has_results,
            "analyses": analyses,
            "last_published_at": last_published_at,
            "publish_stale": bool(last_published_at) and publish_stale,
            "last_surveyed_at": last_surveyed_at,
        })
    return {"slug": slug, "stage": stage, "dashboards": dashboards}


def _read_analyses(registry, slug: str, analysis_ids: list[str], results_map: dict, headline_map: dict) -> list[dict]:
    """[{analysis_id, results, headline}] for a dashboard's analysis_ids —
    same fail-soft shape as the original repo-only loop: a reader that
    raises degrades to None rather than breaking the whole dashboard."""
    analyses = []
    for analysis_id in analysis_ids:
        entry = results_map.get(analysis_id)
        results = None
        if entry:
            results_reader, _ = entry
            try:
                results = results_reader(registry, slug)
            except Exception:
                results = None
        headline_reader = headline_map.get(analysis_id)
        headline = None
        if headline_reader:
            try:
                headline = headline_reader(registry, slug)
            except Exception:
                headline = None
        analyses.append({"analysis_id": analysis_id, "results": results, "headline": headline})
    return analyses


def _results_have_data(results) -> bool:
    """The exact same "truthy but not shaped-empty" test the original
    repo-only `_survey_results_sync` used (`workflows.scouting.
    results_have_data` — see that function's docstring for the full
    reasoning on why plain truthiness over-counts)."""
    from resource_explorer.workflows.scouting import results_have_data
    return results_have_data(results)
