"""Runs all sub-surveyors for a project and assembles a SurveyResult."""
from __future__ import annotations

import logging
from collections import defaultdict
from contextlib import ExitStack
from datetime import datetime

from trellis_microflow import resolve_resources

from resource_explorer.activity_logger import log_survey
from resource_explorer.registry import ProjectRegistry
from resource_explorer.observability import openlineage
from resource_explorer.surveyors import credential_capability, prerequisite_resolver, step_cost_observer
from resource_explorer.surveyors import result_status, step_preconditions
from resource_explorer.surveyors.survey_report import summarise_annotations, ClassificationAnnotation, SurveyResult

log = logging.getLogger(__name__)

# Step-cost-tiers plan (docs/step-cost-tiers-plan.md, D2/D5) — ordinal
# position, not numeric value, is what run()'s max_fetch_cost/
# max_compute_cost filter compares against. Keep in sync with the values
# StepInfo.fetch_cost/compute_cost actually use.
# Imported, not redeclared — see repo_survey_definition_adapter's own comment
# on why one copy of the ordinal scale matters. Module-level import would be
# circular (the adapter imports this module's SurveyOrchestrator), so these are
# bound lazily at first use.
def _cost_orders() -> tuple[list[str], list[str]]:
    from resource_explorer.surveyors.repo_survey_definition_adapter import (
        COMPUTE_COST_ORDER, FETCH_COST_ORDER,
    )
    return FETCH_COST_ORDER, COMPUTE_COST_ORDER


class SurveyOrchestrator:
    """
    Runs all sub-surveyors in sequence and returns a SurveyResult.

    Parameters
    ----------
    registry        : open ProjectRegistry
    pyegeria_client : optional — passed to FileClassifierSurveyor for cache refresh
    force_refresh   : force FileTypeCache refresh even if not stale
    """

    def __init__(
        self,
        registry: ProjectRegistry,
        pyegeria_client=None,
        force_refresh: bool = False,
        data_path: str | None = None,
    ) -> None:
        self._registry = registry
        self._pyegeria_client = pyegeria_client
        self._force_refresh = force_refresh
        self._data_path = data_path  # local clone path for DataProfilerSurveyor Tier 2

    def _auto_run(self, producer: str, demanding_step: str, resolution,
                  project, result, surveyed_at: str, construct) -> str:
        """Run one prerequisite step on the run's own initiative (§17.1).

        **An auto-run is a recorded result, never a silent omission** — the
        same principle the skip already follows, and the reason this is not
        simply a call to `surveyor.run()`:

        * a real annotation lands in the report ("ran `X` because `Y`
          required `Z`"), so a reader who wonders where three minutes went
          finds the answer in the report they are already looking at;
        * an activity-log entry is written (CLAUDE.md rule 16);
        * the cost is attributed to BOTH steps — recorded on the producer's
          own `step_runs` row with `demanded_by` naming the demander, and
          included in the demander's own measurement because
          `step_cost_observer.observe`'s scopes nest.

        A producer that RAISES is reported and does not fail the survey: the
        demanding step's precondition is then simply still unmet, which the
        re-check immediately after this sees, and the step is skipped with a
        reason — the outcome it would have had anyway.
        """
        precondition = (resolution.proposal.preconditions[0]
                        if resolution.proposal and resolution.proposal.preconditions
                        else (resolution.dead_ends[0] if resolution.dead_ends else ""))
        why = (f"ran {producer} because {demanding_step} required "
               f"{precondition or 'its stored output'}")
        from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

        info = STEP_REGISTRY[producer]
        try:
            surveyor = construct(producer)
        except Exception as exc:
            msg = f"could not construct prerequisite {producer}: {exc}"
            log.warning(msg)
            result.add_error(msg)
            return msg
        annotations = []
        raised = False
        with step_cost_observer.observe(
            producer, info.fetch_cost, info.compute_cost,
            demanded_by=demanding_step,
            entity_type="repo", slug=project.slug, resource=project,
            job_family="survey",
        ) as observed:
            try:
                annotations = surveyor.run()
                for ann in annotations:
                    result.add(ann)
                result.step_annotations[producer] = list(annotations)
            except Exception as exc:
                raised = True
                msg = f"prerequisite {producer} raised unexpectedly: {exc}"
                log.exception(msg)
                openlineage.note_failure(msg)
                result.add_error(msg)
        if observed:
            observed[0].annotations, observed[0].outcomes = (
                step_cost_observer.describe_work(annotations))
            observed[0].disagreement = step_cost_observer._disagreement(observed[0])
            commit = result.survey_commit_sha if step_cost_observer.completed(annotations, raised) else None
            if commit:
                result.commit_by_step[producer] = commit
            step_cost_observer.record(
                self._registry, project.slug, observed[0], surveyed_at, survey_commit_sha=commit)
        result.add(ClassificationAnnotation(
            check_name="prerequisite_auto_run",
            item_key=producer,
            summary=why,
            analysis_step=producer,
            candidate_classifications=["auto_run_prerequisite"],
            confidence=100,
            json_properties=prerequisite_resolver.auto_run_annotation(
                producer, demanding_step, precondition),
        ))
        result.auto_ran_steps[producer] = why
        try:
            log_survey(
                self._registry, entity_type="repo", entity_slug=project.slug,
                entity_name=project.display_name,
                entity_location=project.github_url,
                intent="analysis", status="ok",
                summary=why,
                detail=f"prerequisite auto-run: {producer} demanded by {demanding_step}",
            )
        except Exception as exc:
            # Recorded on the result, not only logged. An auto-run that left
            # no activity entry is invisible in the one place a person goes to
            # ask "what did this run do" — which is the whole content of
            # §17.1's third condition, so failing it quietly would leave the
            # feature looking exactly like the silent omission it replaced.
            log.warning("could not log the prerequisite auto-run: %s", exc)
            result.add_error(
                f"{why} — but the activity-log entry for it could not be "
                f"written ({exc}), so this auto-run is missing from the log")
        return why

    def run(
        self, resource_slug: str, steps: list[str] | None = None,
        scope_locator: str = "", fast: bool = False,
        max_fetch_cost: str | None = None, max_compute_cost: str | None = None,
    ) -> SurveyResult:
        """Survey a single project and return the assembled SurveyResult.

        steps : optional list of step keys (same vocabulary as
            repo_survey_definition_adapter.py's re_analysis_steps — e.g.
            "repo_health", "repo_api_structure") to run only those
            sub-surveyors instead of the full set. None (default) runs
            every sub-surveyor, exactly as before this parameter existed.
        scope_locator : D5/D6 repo scope-narrowing funnel plan — "" (default,
            unchanged) surveys the whole repo. A non-empty locator narrows
            corpus-shaped steps (StepInfo.accepts_scope_locator=True) to one
            cataloged sub-resource; steps that don't accept it ignore this
            entirely (they always run whole-repo, matching their
            target_shape). Only meaningful alongside steps=[...] — a scoped
            full survey isn't a supported combination and callers should
            gate on D6 target-shape compatibility before calling this with
            both set.
        fast : Scouting-tier "skip anything N+1/expensive" flag — forwarded
            only to steps whose StepInfo.accepts_fast is True (repo_health
            today, see HealthSurveyor.__init__). False (default) is every
            existing caller's exact prior behavior, unchanged.
        max_fetch_cost, max_compute_cost : step-cost-tiers plan
            (docs/step-cost-tiers-plan.md, D5) — optional ceilings on
            StepInfo.fetch_cost/compute_cost ("none"/"api"/"api_heavy"/
            "download" and "low"/"medium"/"high" respectively, compared by
            ordinal position). A step whose cost exceeds either ceiling is
            excluded from this run, same as if it had been left out of
            `steps`. Both None (default) applies no filter — every
            existing caller's exact prior behavior, unchanged. This is
            deliberately the only new surface this plan adds — no new
            survey types, no scheduler changes; see the plan's "Out of
            scope".
        """
        project = self._registry.get(resource_slug)
        if project is None:
            raise ValueError(f"Project '{resource_slug}' not found in registry.")

        surveyed_at_dt = datetime.utcnow()
        # Shared run-timestamp, passed into the surveyors that persist
        # structured findings at survey time (Phase B, D1) — lets one
        # orchestrator run's writes across those tables be correlated,
        # instead of each independently stamping datetime.utcnow() seconds
        # apart. Dependency/DataProfiler persistence isn't threaded here —
        # those write at ingest/refresh time via IngestionPipeline, a
        # separate code path this orchestrator doesn't touch.
        surveyed_at = surveyed_at_dt.isoformat()

        result = SurveyResult(
            resource_slug=project.slug,
            project_display_name=project.display_name,
            github_url=project.github_url,
            surveyed_at=surveyed_at_dt,
        )

        # Derived from STEP_REGISTRY (analysis-kind extensibility redesign) —
        # a plain (project, registry) construction is the default; the one
        # surveyor needing genuine per-instance orchestrator context that
        # isn't a D6 resource (FileClassifier's Egeria-refresh client) is
        # the only explicit special case left, kept small and commented
        # rather than forcing a fully generic instance-attr-injection
        # mechanism for just this one. StepInfo.accepts_surveyed_at marks
        # the surveyors that persist structured findings/metrics and need
        # the shared per-run timestamp threaded in.
        from resource_explorer.surveyors.repo_survey_definition_adapter import (
            STEP_REGISTRY,
            _resource_providers_for,
        )

        step_keys_to_run = (
            set(STEP_REGISTRY.keys()) if steps is None else set(steps) & STEP_REGISTRY.keys()
        )

        # D5 cost-tier filter — applied after the steps=[...] selection
        # above, same set-narrowing shape. Ordinal comparison via
        # _FETCH_COST_ORDER/_COMPUTE_COST_ORDER's index, not the string
        # values themselves.
        _FETCH_COST_ORDER, _COMPUTE_COST_ORDER = _cost_orders()
        if max_fetch_cost is not None:
            ceiling = _FETCH_COST_ORDER.index(max_fetch_cost)
            step_keys_to_run = {
                k for k in step_keys_to_run
                if _FETCH_COST_ORDER.index(STEP_REGISTRY[k].fetch_cost) <= ceiling
            }
        if max_compute_cost is not None:
            ceiling = _COMPUTE_COST_ORDER.index(max_compute_cost)
            step_keys_to_run = {
                k for k in step_keys_to_run
                if _COMPUTE_COST_ORDER.index(STEP_REGISTRY[k].compute_cost) <= ceiling
            }

        # Recorded AFTER the cost-tier narrowing, so it is what actually ran
        # rather than what was asked for — a publish attributing an analysis
        # that a cost ceiling excluded would be the same false claim in a new
        # place. Precondition skips are subtracted after the dispatch loop below,
        # for the same reason: they are not known until each step is reached.
        result.steps_run = sorted(step_keys_to_run)

        # D6 (docs/unified-survey-execution-model-plan.md): resolve every
        # shared resource any selected step needs, once, before
        # constructing any surveyor — resolve_resources dedupes across
        # however many steps asked for the same name. self._data_path is a
        # real, still-live explicit override (CLI `resource-explorer
        # refresh --data-path`) — when a caller already supplied a local
        # clone path, skip the D6 zipball download for that purpose
        # entirely rather than fetching one nothing will use.
        needed_resources: set[str] = set()
        for step_key in step_keys_to_run:
            for resource_name, kwarg_name in STEP_REGISTRY[step_key].requires_resources.items():
                if kwarg_name == "local_path" and self._data_path is not None:
                    continue
                needed_resources.add(resource_name)

        # The commit of the code this run READ (artifacts slice 1): each provider writes the SHA of what it
        # served into `served`, once, when resolve_resources acquires it — the same once-per-run sharing
        # as the resource itself. Every step_runs row of the run carries it. A run that acquired no
        # zipball or clone (API-only steps, or a caller's --data-path) records NULL, never
        # projects.last_commit_sha.
        served: dict = {}
        with ExitStack() as stack:
            resources = (
                resolve_resources(
                    stack, _resource_providers_for(project, self._registry, served), needed_resources
                )
                if needed_resources
                else {}
            )
            result.survey_commit_sha = served.get("zipball_root") or served.get("git_clone_root") or None

            # Iterate STEP_REGISTRY's own insertion order, not
            # step_keys_to_run (a set — unordered) — a full run's step
            # order must stay deterministic and match the registry's
            # declared order, same as before this D6 change.
            def _construct(step_key: str):
                """One step's surveyor, with the kwargs its StepInfo declares.

                Extracted so the §17.1 resolver can construct a PRODUCER that
                was never in `step_keys_to_run` — an auto-run prerequisite is
                by definition a step the caller did not ask for. Raises
                KeyError for a resource the run did not acquire, which is why
                the resolver only ever auto-runs a producer whose resources
                are already in `resources` (anything else is a download, and
                a download is a proposal).
                """
                info = STEP_REGISTRY[step_key]
                if step_key == "repo_file_classification":
                    kwargs = {
                        "pyegeria_client": self._pyegeria_client,
                        "force_refresh": self._force_refresh,
                    }
                else:
                    kwargs = {}
                if info.accepts_surveyed_at:
                    kwargs["surveyed_at"] = surveyed_at
                if info.accepts_scope_locator:
                    kwargs["scope_locator"] = scope_locator
                if info.accepts_fast:
                    kwargs["fast"] = fast
                for resource_name, kwarg_name in info.requires_resources.items():
                    if kwarg_name == "local_path" and self._data_path is not None:
                        kwargs[kwarg_name] = self._data_path
                    else:
                        kwargs[kwarg_name] = resources[resource_name]
                return info.surveyor_cls(project, self._registry, **kwargs)

            all_surveyors = {}
            for step_key, info in STEP_REGISTRY.items():
                if step_key not in step_keys_to_run:
                    continue
                all_surveyors[step_key] = _construct(step_key)

            if steps is None:
                selected = list(all_surveyors.items())
            else:
                selected = [(key, all_surveyors[key]) for key in steps if key in all_surveyors]

            #: Prerequisites this run has already executed on its own
            #: initiative (§17.1). Shared across the loop so two demanding
            #: steps needing the same producer run it once — and, more to the
            #: point, so the second one does not re-run a producer that just
            #: found nothing.
            auto_ran: set[str] = set()

            #: The stored `credential_capability` probe, resolved AT MOST
            #: ONCE for this whole run rather than once per `resolve()` call.
            #: 2026-09-29 round 3 (PER-REQUEST-SERVER-LATENCY-ROUND-3-IMPLEMENTED.md) — `resolve()`
            #: already accepts
            #: `capability_probe` as a pass-in specifically so a caller that
            #: needs it more than once doesn't have to let `resolve()`
            #: re-fetch it each time (see that parameter's own docstring);
            #: this loop calls `resolve()` up to twice per step (the
            #: auto-run re-check) across every step in `selected`, and
            #: nothing was actually passing the probe in, so each call
            #: independently re-fetched via `credential_capability.
            #: stored_probe` -> `get_database_surveys` — measured (by the
            #: registry-cache-staleness fix, dc065497's own commit message)
            #: at up to 6x per run for one slug, 3.4s uncached on a
            #: 57-row/61MB survey history. The per-instance cache added
            #: alongside that fix now absorbs the repeat cost too, but that
            #: makes the cache a safety net for this pattern, not the fix —
            #: resolving once and threading it through is the actual fix,
            #: independent of whether a cache exists downstream. Lazy: only
            #: fetched if some step in `STEP_REGISTRY` actually declares
            #: `requires_capability` (`_declares_capability`, the same
            #: check `resolve()` itself would otherwise make on every call),
            #: so a repo/filesystem run — which declares no capability axis
            #: at all — still makes zero registry calls for this.
            capability_probe: dict | None = None
            capability_probe_resolved = False

            for step_key, surveyor in selected:
                # Preconditions on stored data, before dispatch. A step whose
                # input another step produces is absent cannot say anything, and
                # running it anyway costs a slot in every downstream picture —
                # `security_summary` counts such a step among its inputs as
                # "never ran" and withholds a verdict below its floor.
                #
                # The skip is EMITTED, not omitted. `result_status.skipped()`
                # requires a reason for the same purpose it does everywhere else:
                # a step that simply disappears from a report is indistinguishable
                # from one that ran and found nothing.
                #
                # §17.1 (2026-09-23): an unmet precondition is now a PLAN, not
                # only a skip. `step_preconditions.evaluate` is still what does
                # the checking — the resolver wraps it, and decides between
                # running the producer (inside the budget the caller chose),
                # proposing the whole chain (outside it), and skipping (nothing
                # produces the missing data, or the producer already looked and
                # found nothing on this snapshot).
                _info = STEP_REGISTRY.get(step_key)
                _ctx = getattr(_info, "requires_context", None)
                if _ctx:
                    if not capability_probe_resolved:
                        if prerequisite_resolver._declares_capability(
                            STEP_REGISTRY, STEP_REGISTRY.keys(),
                        ):
                            capability_probe = credential_capability.stored_probe(
                                self._registry, project,
                            )
                        capability_probe_resolved = True
                    resolution = prerequisite_resolver.resolve(
                        self._registry, project, step_key, STEP_REGISTRY,
                        surveyed_at=surveyed_at,
                        max_fetch_cost=max_fetch_cost,
                        max_compute_cost=max_compute_cost,
                        resolved_resources=set(resources),
                        already_ran=auto_ran,
                        capability_probe=capability_probe,
                    )
                    for producer in resolution.auto_run:
                        why_ran = self._auto_run(
                            producer, step_key, resolution, project, result,
                            surveyed_at, _construct)
                        auto_ran.add(producer)
                        log.info("Auto-ran %s for %s — %s", producer, project.slug, why_ran)
                    if resolution.auto_run:
                        # Re-check: the producers have run, so the preconditions
                        # may now be met. They may equally NOT be (a manifest
                        # parse that found no versions — CLAUDE.md's Gradle/BOM
                        # case), and that outcome must reach the skip below
                        # rather than being assumed away.
                        resolution = prerequisite_resolver.resolve(
                            self._registry, project, step_key, STEP_REGISTRY,
                            surveyed_at=surveyed_at,
                            max_fetch_cost=max_fetch_cost,
                            max_compute_cost=max_compute_cost,
                            resolved_resources=set(resources),
                            already_ran=auto_ran,
                            capability_probe=capability_probe,
                        )
                    if not resolution.may_run:
                        why = resolution.reason
                        log.info("Skipping %s for %s — %s",
                                 surveyor.step_name, project.slug, why)
                        status = step_preconditions.skip_status(
                            (resolution.proposal.preconditions[0]
                             if resolution.proposal and resolution.proposal.preconditions
                             else (resolution.dead_ends[0] if resolution.dead_ends else "")),
                            why)
                        if resolution.proposal is not None:
                            # The proposal travels WITH the skip, so the UI can
                            # offer "run it?" from the same annotation that says
                            # the step did not run. A proposal recorded
                            # somewhere else would be a second thing to find.
                            status["proposal"] = resolution.proposal.as_dict()
                        result.add(ClassificationAnnotation(
                            check_name="step_skipped",
                            item_key=surveyor.step_name,
                            summary=f"Skipped: {why}",
                            analysis_step=surveyor.step_name,
                            candidate_classifications=[result_status.SKIPPED_BY_DESIGN],
                            confidence=0,
                            json_properties=status,
                        ))
                        result.skipped_steps[step_key] = why
                        if resolution.proposal is not None:
                            result.proposals[step_key] = resolution.proposal.as_dict()
                        continue

                log.info("Running %s for %s …", surveyor.step_name, project.slug)
                # Measure what the step actually costs, against what its
                # StepInfo declares. Three mis-declarations surfaced in one week
                # — a zero-fetch step calling the GitHub API, a `medium` step
                # measuring `low`, and a timing taken for the wrong function —
                # and none was catchable by a test, because each was a
                # declaration disagreeing with behaviour rather than code
                # disagreeing with itself. See step_cost_observer: it reports
                # and never corrects, deliberately.
                info = STEP_REGISTRY.get(step_key)
                with step_cost_observer.observe(
                    step_key,
                    getattr(info, "fetch_cost", ""),
                    getattr(info, "compute_cost", ""),
                    entity_type="repo", slug=project.slug, resource=project,
                    job_family="survey",
                ) as _observed:
                    # Reset per step: a step that raises must not be judged on the previous step's output.
                    annotations = []
                    _raised = False
                    try:
                        annotations = surveyor.run()
                        for ann in annotations:
                            result.add(ann)
                        result.step_annotations[step_key] = list(annotations)
                        log.info("  → %d annotation(s)", len(annotations))
                    except Exception as exc:
                        _raised = True
                        msg = f"{surveyor.step_name} raised unexpectedly: {exc}"
                        log.exception(msg)
                        openlineage.note_failure(msg)
                        result.add_error(msg)
                        # Also keyed by step, so a caller running steps on behalf
                        # of several analyses at once can tell which of them
                        # failed.
                        result.step_errors[step_key] = msg
                # Recorded outside the try so a step that RAISED is still
                # measured: a step that fails after 90 seconds of network calls
                # is exactly the case worth having a number for.
                if _observed:
                    # Attach what the step produced before judging its speed —
                    # a duration with no idea whether the step was exercised is
                    # the absence-looks-like-zero shape inside the instrument.
                    _observed[0].annotations, _observed[0].outcomes = (
                        step_cost_observer.describe_work(locals().get("annotations")))

                    _observed[0].disagreement = step_cost_observer._disagreement(_observed[0])
                    # The commit only on a step that completed; a failed step read nothing to cite.
                    _commit = (result.survey_commit_sha
                               if step_cost_observer.completed(annotations, _raised) else None)
                    if _commit:
                        result.commit_by_step[step_key] = _commit
                    recorded = step_cost_observer.record(
                        self._registry, project.slug, _observed[0], surveyed_at,
                        survey_commit_sha=_commit)
                    if recorded == step_cost_observer.NOT_RECORDED:
                        # Branchable, not just logged: an unwritten observation
                        # is indistinguishable from a step that was never slow.
                        result.add_error(
                            f"step cost for {step_key} could not be recorded — "
                            "its cost observation is missing, not zero")

        # A skipped step did not run, so it must not stay in steps_run — which a
        # publish reads to say WHICH analyses it published. Leaving it there
        # would attribute an analysis to a step that produced nothing: the same
        # false claim the cost-ceiling comment above describes, arriving by a new
        # route. Subtracted here rather than up front because a precondition is
        # only evaluated when its step is reached.
        #
        # Caught by test_a_skip_actually_executes_end_to_end_through_the_orchestrator:
        # the annotation and skipped_steps were both already correct while
        # steps_run still said the step had run. Reading the dispatch site would
        # not have found it — only running the branch did.
        if result.skipped_steps:
            result.steps_run = sorted(
                set(result.steps_run) - set(result.skipped_steps))
        # …and an auto-run prerequisite DID run, so it must be added for the
        # mirror-image reason: a publish reads `steps_run` to say which
        # analyses it published, and omitting a step whose annotations are in
        # this very result would under-attribute them. `auto_ran_steps` keeps
        # "ran because something else needed it" distinguishable from "ran
        # because you asked".
        if result.auto_ran_steps:
            result.steps_run = sorted(
                set(result.steps_run) | set(result.auto_ran_steps))

        log.info(
            "Survey complete for %s: %d annotation(s), %d error(s)",
            project.slug,
            len(result.annotations),
            len(result.errors),
        )

        # Keep each completed step's annotations as the repository's latest survey, so a publish can
        # send the survey a person decided on without running one (brief section 1). A scoped run
        # (a sub-resource's own analysis) is not the repository's survey and is never kept as one.
        if not scope_locator and result.step_annotations:
            from resource_explorer.surveyors import survey_snapshot
            for step_key, kept in result.step_annotations.items():
                if step_key in result.step_errors:
                    continue          # a step that raised keeps its previous result, never a half one
                try:
                    survey_snapshot.record_step(self._registry, project.slug, step_key, surveyed_at, kept)
                except Exception as exc:
                    result.snapshot_error = (
                        f"the survey could not be kept for publishing ({step_key}): {exc}")
                    log.warning("could not keep survey step %s for %s: %s", step_key, project.slug, exc)
                    break

        # Any survey counts as "surveyed" — coarse scan or deep, full or
        # step-filtered — so this is unconditional, unlike the self-logging
        # below which only applies to a full (steps=None) run.
        try:
            self._registry.update_project_surveyed_at(project.slug)
        except Exception as exc:
            log.warning("Could not update last_surveyed_at for %s: %s", project.slug, exc)

        # Self-logging only applies to a full survey. A step-filtered (partial)
        # run is always driven by a caller that writes its own, more specific
        # activity-log entry (e.g. scheduler.py's per-analysis-id dispatch, or
        # repo_survey_definition_adapter.py's Survey Definition executor) —
        # logging here too would double-log every such run.
        if steps is None:
            # Group annotations by (analysis_step, annotation_type) for a
            # compact activity log entry — NOT by step alone.
            #
            # Real bug, fixed 2026-09-01: the group key used to be just
            # `step`, so every annotation a single sub-surveyor produced in
            # one run — e.g. SecurityHygieneSurveyor's PASSING
            # ClassificationAnnotation("Security policy file present")
            # alongside its FAILING RequestForActionAnnotation("No CI
            # configuration detected") — collapsed into one dict. `summary`
            # kept the FIRST annotation seen (first-wins), but
            # `annotation_type` kept overwriting to the LAST one seen
            # (last-wins) — two different "which one won" rules on the same
            # group, which is how a passing check's summary ended up wearing
            # a RequestForAction label. GET /api/activity/rfas
            # (web/routes/activity.py) substring-matches on "RequestForAction"
            # in `annotation_type`, so every such mislabeled group surfaced
            # in the RFA drawer as a request for action nobody could act on,
            # because there was nothing to act on — the check had passed.
            #
            # Keying by (step, annotation_type) keeps a passing
            # ClassificationAnnotation and a failing RequestForActionAnnotation
            # from the same step in separate groups, so neither's summary or
            # type can leak into the other's row. Confirmed against live data
            # before this fix: two stored `docs` activity entries each carried
            # a RequestForAction-typed annotation summarised "Security policy
            # file present" — the passing check's own words, wearing the
            # failing check's label.
            # One grouping rule, shared with the Analyses-card path —
            # see summarise_annotations() for why keying on
            # (step, annotation_type) matters and what it fixed.
            ann_summary = summarise_annotations(result.annotations)

            try:
                log_survey(
                    self._registry,
                    entity_type="repo",
                    entity_slug=project.slug,
                    entity_name=project.display_name,
                    entity_location=project.github_url,
                    intent="assessment",
                    status="error" if result.errors else "ok",
                    summary=f"{len(result.annotations)} annotation(s), {len(result.errors)} error(s)",
                    detail="; ".join(result.errors) if result.errors else "",
                    annotations=ann_summary,
                )
            except Exception as exc:
                log.warning("Could not write activity log entry: %s", exc)

        return result
