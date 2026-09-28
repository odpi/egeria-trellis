"""
Generic dispatch loop for executing a Survey Definition's RE-side steps.

Fetches a Survey Definition from Egeria (via SurveyDefinitionReader), walks its
ordered steps, and for each step tagged executes_at="resource-explorer" dispatches
to the matching local surveyor via a per-resource-type ResourceTypeAdapter. Steps
tagged for another engine (e.g. "egeria", "airflow") are skipped — that's someone
else's responsibility to run.

This module has no resource-type-specific logic itself; each resource type
(database, repo, filesystem) registers its own small adapter — see
resource_explorer/surveyors/database/survey_definition_adapter.py,
resource_explorer/surveyors/repo_survey_definition_adapter.py, and
resource_explorer/surveyors/filesystem/survey_definition_adapter.py.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from resource_explorer.surveyors import result_status, step_cost_observer
from resource_explorer.surveyors.survey_definition_reader import SurveyDefinitionReader

log = logging.getLogger(__name__)

from resource_explorer.activity_logger import log_survey


class SurveyDefinitionExecutorError(RuntimeError):
    """Raised for executor-level errors (unknown entity type, ambiguous/missing
    Survey Definition, technology-type mismatch) — distinct from per-step failures,
    which are accumulated in the result's "errors" list instead of raised."""


@dataclass
class ResourceTypeAdapter:
    entity_type: str
    technology_type: str
    re_analysis_steps: dict  # re_analysis_step -> Callable(entity, registry, **kwargs) -> dict
    get_entity: Callable  # (registry, slug) -> entity | None
    publish: Callable  # (entity, step_outputs: list[dict], surveyed_at: str, registry, defer_drain: bool = False) -> str (report_guid)
    # re_analysis_step -> {"description": str, "annotation_types": list[str]} — lets
    # UI/API callers explain what a step actually does/produces before running it,
    # without needing a live Egeria round-trip (RE already knows this about its own
    # steps). Optional: steps that exist in re_analysis_steps but have no entry
    # here just won't get a description/annotation-type hint shown.
    re_analysis_step_info: dict = field(default_factory=dict)
    # executes_at value (e.g. "egeria") -> Callable(entity, registry, step, **kwargs)
    # -> dict. For steps NOT tagged "resource-explorer" that this resource type can
    # still actively do something about — e.g. triggering Egeria's own native
    # survey engine for an "egeria"-tagged step, rather than only ever skipping it.
    # Optional: engines with no handler here are still just skipped/logged, same
    # as before this existed.
    other_engine_handlers: dict = field(default_factory=dict)
    # Egeria's own real Technology Type display name (e.g. "PostgreSQL Relational
    # Database") — deliberately separate from `technology_type` above, which is
    # only the free-text string RE's own authored Survey Definitions happen to be
    # tagged with via additionalProperties.supported_technology_type. The two are
    # NOT guaranteed to match (confirmed live 2026-07-08: our own authored
    # "SimplePostgresSurvey" uses "PostgreSQL Database", which isn't a real
    # registered Technology Type at all). This field is used to query Egeria's
    # real Technology Type catalog (EgeriaTechTypeCatalog) for what a native
    # survey step actually produces. Optional: leave blank if unknown.
    egeria_technology_type_name: str = ""
    # Optional: (entity, registry, re_analysis_step_keys: list[str], **kwargs) ->
    # {"annotations": [...], "errors": [...]}. When set, the dispatch loop below
    # groups consecutive "resource-explorer" steps into ONE call here instead of
    # calling re_analysis_steps[key] once per step — real fix, not a
    # micro-optimization: per-step dispatch means any step declaring a shared
    # D6 resource (e.g. repo's zipball_root) re-acquires it independently every
    # time, since trellis_microflow.resolve_resources only dedupes *within* a
    # single SurveyOrchestrator.run() call. A Survey Definition whose steps all
    # need the same zipball (e.g. a "Coarse Profile" survey) would otherwise
    # download it once per step. None (default, every adapter before this
    # existed) keeps the exact prior one-call-per-step behavior.
    run_batch: Callable | None = None

    # ── what is KNOWN about a resource of this type (2026-09-20) ────────────
    #
    # docs/multi-resource-questions-design.md §1.1 item 5 / §13 Phase 0 item 3.
    # `facts.py` used to import `REPO_ANALYSIS_RESULTS_MAP` and consult a
    # `RESOURCE_STATE_SOURCES` table keyed by REPO question text, both
    # unconditionally — so `FactLayer` answered every question about every
    # resource type out of the repository's maps. These four fields are the
    # per-type instances of that pattern (design §2, "carries over as a
    # pattern, needs a per-type instance"), and `FactLayer` now dispatches
    # through them.
    #
    # Each is a PROVIDER — a zero-argument callable returning the map — not
    # the map itself. A resource type's maps are built at the bottom of a
    # module that imports its whole surveyor stack, and this dataclass is
    # constructed in that same module; a provider lets a type whose maps live
    # elsewhere (repo's state sources live in facts.py, where their resolver
    # functions are) declare them without an import cycle, and costs nothing
    # for a type whose maps are local.
    #
    # None means NOT DECLARED, which is not the same as empty: a resource type
    # with no results map cannot have facts read from it at all, and FactLayer
    # says so in those words rather than reporting every analysis as
    # never-run. Same distinction the question catalog draws between "not
    # authored" and "filtered to nothing".

    #: () -> {analysis_id: (results_reader, trend_reader)}
    analysis_results_map: Callable | None = None
    #: () -> {analysis_id: [step_key, ...]} — the SOURCE steps that would
    #: establish an analysis, which for a derives-from analysis is its
    #: source's steps rather than its own (see FactLayer.fact's `can_run`).
    analysis_source_steps: Callable | None = None
    #: () -> {analysis_id: AnalysisKind} — carries `results.live_read` and
    #: `results.headline_reader`, which FactLayer reads off the kind rather
    #: than off the derived results map (the derived map holds a tuple, and
    #: `getattr(tuple, "live_read")` silently returned False for everything
    #: the first time that was tried).
    analysis_kinds: Callable | None = None
    #: () -> {question_text: (resolver, subject)} — questions answerable from
    #: the resource's own recorded state rather than from an analysis result.
    #: Keyed by question text, so this map is per resource type by
    #: construction: a database's questions are different strings.
    state_sources: Callable | None = None
    #: () -> {analysis_id: headline_reader}. A separate, directly-keyed
    #: provider rather than reading `analysis_kinds()[id].results.
    #: headline_reader` (2026-09-23, context_compile.py's own
    #: results-reader/headline fallback): that fallback previously imported
    #: `REPO_ANALYSIS_HEADLINE_MAP` — a module-level dict, mutated in place by
    #: `monkeypatch.setitem` in tests — directly rather than through
    #: `ANALYSIS_KINDS`. Deriving the same value from `analysis_kinds()` at
    #: call time would read past that monkeypatch, since the derived headline
    #: map is a separate object from the kinds table it was built from. This
    #: provider keeps the same object identity `REPO_ANALYSIS_HEADLINE_MAP`
    #: already had. None (undeclared) means no headline reader is offered for
    #: this resource type — the database/filesystem case today, same as their
    #: own `DATABASE_ANALYSIS_HEADLINE_MAP`/`FILESYSTEM_ANALYSIS_HEADLINE_MAP`
    #: constants being empty dicts.
    analysis_headline_map: Callable | None = None
    #: () -> {analysis_id: container_level_headline_reader} — Slice 21a.
    #: Consulted by `FactLayer._headline_for` only when the asking
    #: question's level is a sub-resource one (container/member/field),
    #: and only for the analysis_ids that register one here; every other
    #: analysis, and every level-agnostic caller of `analysis_headline_map`
    #: itself, is unaffected. None (undeclared, the default) means no
    #: resource type currently offers a level-aware reading for anything —
    #: the falls-back-to-resource behaviour is `_headline_for`'s own, not
    #: this field's absence being treated as an error.
    analysis_container_headline_map: Callable | None = None
    #: () -> {analysis_id: container_level_results_reader} — Slice 21a point
    #: 4. The "numbers behind this" evidence table's container-level
    #: counterpart to `analysis_container_headline_map`: consulted by
    #: `workflows/stage_page.py::build_measurements` only when the request's
    #: `level` query param is a sub-resource one, and only for the
    #: analysis_ids that register one here. Its reader returns a list of
    #: `{name, value, opens, note}` rows already shaped for the frontend
    #: table (one row per container — e.g. per schema — rather than one row
    #: per resource-level scalar), not a raw results dict like
    #: `analysis_results_map`'s readers return. None (undeclared, the
    #: default) means no resource type offers a level-aware measurements
    #: table for anything yet — `build_measurements` falls back to the
    #: existing resource-level reader, unchanged.
    analysis_container_results_map: Callable | None = None
    #: () -> {step_key: StepInfo} — what each of this type's steps COSTS,
    #: what stored data it REQUIRES, and what tables it PRODUCES (design
    #: §17.1/§17.2). A provider for the same import-cycle reason as the four
    #: above.
    #:
    #: None means NOT DECLARED, and the prerequisite resolver treats it as
    #: such: a resource type with no step registry has no preconditions it
    #: can check and no costs it can compare, so every step dispatches
    #: exactly as it did before §17.1 existed. That is deliberately different
    #: from an empty registry, which would be a claim that this type's steps
    #: have no prerequisites.
    step_registry: Callable | None = None


_ADAPTERS: dict = {}


def register_adapter(adapter: ResourceTypeAdapter) -> None:
    """Register a ResourceTypeAdapter for its entity_type. Called once at import
    time by each resource type's survey_definition_adapter module."""
    _ADAPTERS[adapter.entity_type] = adapter


def get_adapter(entity_type: str) -> ResourceTypeAdapter:
    # Import adapters lazily so this module doesn't force-import every resource
    # type's Egeria-coupled surveyor code just to construct an executor.
    if entity_type not in _ADAPTERS:
        if entity_type == "database":
            import resource_explorer.surveyors.database.survey_definition_adapter  # noqa: F401
        elif entity_type == "repo":
            import resource_explorer.surveyors.repo_survey_definition_adapter  # noqa: F401
        elif entity_type == "filesystem":
            import resource_explorer.surveyors.filesystem.survey_definition_adapter  # noqa: F401
    adapter = _ADAPTERS.get(entity_type)
    if adapter is None:
        raise SurveyDefinitionExecutorError(
            f"No Survey Definition adapter registered for entity_type={entity_type!r}. "
            f"Known types: {sorted(_ADAPTERS)}"
        )
    return adapter


class SurveyDefinitionExecutor:
    def __init__(self, registry, reader: SurveyDefinitionReader | None = None) -> None:
        self.registry = registry
        self.reader = reader or SurveyDefinitionReader()

    def run(
        self,
        entity_type: str,
        slug: str,
        technology_type: str | None = None,
        survey_definition_ref: str | None = None,
        refresh_definition: bool = False,
        publish: str | None = None,
        engine_override: str | None = None,
        demanded_by: str = "",
        capability_consented: bool = False,
        **runner_kwargs: Any,
    ) -> dict:
        """
        engine_override: per-RUN choice of which engine runs this definition's
        `resource-explorer`-tagged steps, taking precedence over
        `config.prefect.enabled`/`route_local_steps` for the duration of THIS
        call only — a local parameter, never a global mutation, so it cannot
        leak into a concurrent request.

          - None (default): unchanged, config-driven behaviour (see
            `_use_prefect` below).
          - "resource-explorer": force every `resource-explorer`-tagged step
            to run locally for this run, and skip whole-definition Prefect
            orchestration (`_run_via_prefect`) entirely.
          - "prefect": force every `resource-explorer`-tagged step to run via
            Prefect for this run (`run_prefect_step` already degrades to
            running the step locally if no Prefect server is actually
            reachable — see its own docstring — so this is safe to select
            even when Prefect isn't configured; it just won't do anything
            useful).

        This is genuinely a choice between two runners for the SAME function
        and the SAME result — it is deliberately narrower than `executes_at`
        itself: a step already declared `executes_at="egeria"` (coordinated by
        Egeria's own engine host, not RE) is never forced through either value
        of this override, and a step that exists ONLY as a Prefect flow (see
        PREFECT_ONLY_STEPS below — it has no local implementation to force it
        onto) is likewise left alone by "resource-explorer". See
        `docs/design-notes/ENGINE-CHOICE-IMPLEMENTED.md` for the full
        reasoning.

        Raises ValueError for any other value, so a typo or a stale UI/API
        param fails loudly rather than silently falling back to the default.
        """
        if engine_override not in (None, "resource-explorer", "prefect"):
            raise ValueError(
                f"engine_override must be one of None, 'resource-explorer', 'prefect' — "
                f"got {engine_override!r}"
            )

        adapter = get_adapter(entity_type)
        tech_type = technology_type or adapter.technology_type

        entity = adapter.get_entity(self.registry, slug)
        if entity is None:
            raise SurveyDefinitionExecutorError(
                f"{entity_type} '{slug}' not found in registry"
            )

        # Fall back to the entity's own stored credentials when the caller
        # supplied neither — the same fallback databases.py's plain (non-Survey-
        # Definition) survey route already applies (`req.db_user or database.
        # db_user`). Without this, every database Survey Definition run through
        # this path fails on its first step ("Database credentials are
        # required to connect") even though the database has stored
        # credentials and its ordinary survey button works fine — found live
        # 2026-09-26 running the first-ever database Survey Definition
        # (Slice 12) end to end. `getattr(entity, ..., "")` is a no-op for
        # repo/filesystem entities, which carry no db_user/db_password.
        if not runner_kwargs.get("db_user") and not runner_kwargs.get("db_pwd"):
            stored_user = getattr(entity, "db_user", "") or ""
            stored_pwd = getattr(entity, "db_password", "") or ""
            if stored_user or stored_pwd:
                runner_kwargs = {**runner_kwargs, "db_user": stored_user, "db_pwd": stored_pwd}

        process_guid, process_qn = self._resolve_process_guid(
            entity_type, slug, tech_type, survey_definition_ref, refresh_definition
        )

        survey_def = self.reader.fetch(process_guid)
        if (
            survey_def.supported_technology_type
            and survey_def.supported_technology_type != tech_type
        ):
            raise SurveyDefinitionExecutorError(
                f"Survey Definition '{survey_def.qualified_name}' declares "
                f"supported_technology_type={survey_def.supported_technology_type!r}, "
                f"which does not match the requested {tech_type!r}"
            )

        if not survey_def.steps:
            raise SurveyDefinitionExecutorError(
                f"Survey Definition '{survey_def.qualified_name}' (guid={process_guid}) "
                "was fetched successfully but resolved to zero steps. Either it genuinely "
                "has no steps linked via Link First/Next Process Step, or the graph's "
                "first-step relationship wasn't recognized by SurveyDefinitionReader — "
                "this is the one part of the reader not yet validated against a live "
                "server (see docs/survey-definitions.md, Current Limitations)."
            )

        return self._execute(
            entity_type=entity_type, slug=slug, entity=entity, survey_def=survey_def,
            process_guid=process_guid, process_qn=process_qn,
            publish=publish, engine_override=engine_override, runner_kwargs=runner_kwargs,
            # `demanded_by` used to be dropped here (a latent attribution bug
            # named but deliberately left alone by the capability-axis change
            # below) — fixed by re/survey-executor-demanded-by-run, forwarded
            # like every other kwarg `_execute` already accepts.
            demanded_by=demanded_by,
            capability_consented=capability_consented,
        )

    def _execute(
        self,
        *,
        entity_type: str,
        slug: str,
        entity,
        survey_def,
        process_guid: str,
        process_qn: str,
        publish: str | None,
        engine_override: str | None,
        runner_kwargs: dict,
        demanded_by: str = "",
        capability_consented: bool = False,
    ) -> dict:
        """Run an already-resolved Survey Definition's steps.

        This is the shared body behind both `run()` (a real, Egeria-hosted
        process fetched via `_resolve_process_guid`/`SurveyDefinitionReader.
        fetch`) and `run_synthetic_step()` (a one-step, in-process-only
        `SurveyDefinition` that never touches Egeria to be constructed).

        Extracted 2026-09-20 for the `egeria-adaptive` fold-in
        (EXECUTION-MODES-HYBRID-CLARIFICATION.md): its web/CLI call sites
        need the same steps_report/publish-gating/activity-logging machinery
        `run()` already has, for a single ad hoc step, without first having
        to author (or look up) a real Egeria Survey Definition process just
        to run one step.
        """
        adapter = get_adapter(entity_type)
        surveyed_at = datetime.utcnow().isoformat()

        steps_report: list = []
        step_outputs: list = []
        errors: list = []
        #: §17.1 — chains this run wants the user to confirm, and prerequisite
        #: steps it ran on its own initiative. Both are returned so the caller
        #: (the classic UI's survey response) can offer "run it?" without a
        #: second request, and so an auto-run is visible as a result rather
        #: than as three unexplained extra minutes.
        proposals: list = []
        auto_ran: set = set()

        def _stamp_definition_provenance(output) -> None:
            """Give every keyless annotation this call's `output` carries an
            item_key naming the Survey Definition being executed, so a step
            authored into more than one definition (e.g. `repo_secret_scan`,
            included by both repo-survey-definition-compliance.md and
            repo-survey-definition-full.md) cannot collide on publish —
            see survey_report.assert_unique_qualified_names, which raises
            exactly this collision rather than letting it silently drop an
            annotation.

            Stamped here, at the one place every RE-executed step's
            annotations pass through on their way into `step_outputs`
            (single-step, batched, and Prefect-routed alike), rather than in
            each sub-surveyor — so a step that has never anticipated running
            under more than one definition is covered automatically, and the
            fix does not have to be repeated for the next shared step.

            A step's own item_key (language, ecosystem, file_path,
            secret_pattern's path:line...) is left untouched: this only
            fills the gap for a check that is "one summary per run" and
            never set one, which is exactly the shape that collides.
            """
            if not isinstance(output, dict):
                return
            for ann in output.get("annotations", []) or []:
                # Defensive: a handful of dispatch-loop tests stand in
                # "annotations" with plain dicts/strings rather than real
                # Annotation instances — nothing to stamp on those, and
                # nothing this fix should need to care about.
                if not hasattr(ann, "item_key"):
                    continue
                if not ann.item_key:
                    ann.item_key = survey_def.qualified_name

        # Hand the WHOLE definition to Prefect when it is available, instead of
        # sequencing it here. RE should not be a workflow engine: Egeria
        # coordinates and RE executes leaves, or RE coordinates and delegates
        # the workflow to Prefect. The loop below is the third thing — RE as
        # sequencer, Prefect as task runner — which is the inversion of that.
        #
        # Gated on prefect.enabled, so a deployment without Prefect keeps the
        # local loop. That fallback is not temporary: an offline or
        # Prefect-less run has to keep working, which is the same argument that
        # made Automate local-first.
        # Whatever the local loop still has to do. Prefect having run the
        # definition leaves it empty; everything AFTER the loop — publishing,
        # the activity entry, the assembled result — is shared, so the two
        # paths differ only in who sequenced the steps.
        pending_steps = survey_def.steps
        if (
            _prefect_orchestration_enabled(engine_override)
            and _all_steps_prefect_runnable(survey_def)
            and not self._any_step_needs_prerequisites(adapter, entity, survey_def, surveyed_at)
        ):
            planned = self._run_via_prefect(entity_type, entity, survey_def, runner_kwargs)
            if planned is not None:
                steps_report, step_outputs, errors = planned
                for _output in step_outputs:
                    _stamp_definition_provenance(_output)
                pending_steps = []

        # Order + guard information for the local loop below. Only built when
        # the local loop is actually going to run something — Prefect having
        # taken the whole definition (pending_steps == []) already evaluated
        # guards itself, via prefect/flows.py::run_planned_step_task, and
        # walked `survey_execution_plan`'s own topological order.
        #
        # docs/survey-guard-evaluation-design.md §2.5/§5: before this, the
        # local loop walked `survey_def.steps` — the reader's flat,
        # topologically-ordered-but-unbranched list — with no reference to
        # `.links`/guards at all. Every step ran, always, regardless of what
        # any upstream produced. This is the fallback docs/survey-model-and-
        # engine-host-design.md §4.6 says must keep working indefinitely for
        # an offline/Prefect-less run, so it has to be correct on its own,
        # not merely "correct because every live definition today is
        # unbranched" (true, but an accident of what's authored, not a
        # property of this code).
        plan_by_key: dict = {}
        produced_guard: dict = {}
        if pending_steps:
            from resource_explorer.surveyors.survey_execution_plan import (
                CyclicPlanError,
                build_plan,
            )

            try:
                plan = build_plan(survey_def)
            except CyclicPlanError as exc:
                # Same treatment as _run_via_prefect's identical guard: never
                # run part of a survey whose steps cannot be ordered at all
                # and call it complete.
                raise SurveyDefinitionExecutorError(str(exc)) from exc

            plan_by_key = {ps.qualified_name: ps for ps in plan.steps}
            # Reorder pending_steps to the plan's topological order (a no-op
            # for every definition that runs today — 4.5's own verification
            # note: "every live definition plans to exactly its current
            # order"). A step the plan didn't resolve (defensive only; every
            # step in survey_def.steps came from the same survey_def the plan
            # was built from) is appended rather than dropped, in its
            # original position, so nothing silently vanishes from the run.
            by_qn = {s.qualified_name: s for s in pending_steps}
            ordered = [by_qn[ps.qualified_name] for ps in plan.steps if ps.qualified_name in by_qn]
            seen_qns = {s.qualified_name for s in ordered}
            ordered.extend(s for s in pending_steps if s.qualified_name not in seen_qns)
            pending_steps = ordered

        def _step_key(step) -> str:
            """The same key `survey_execution_plan.build_plan` uses (its own
            `key_of`, not exported): `re_analysis_step`, falling back to
            `qualified_name`. Kept in sync deliberately — `guarded_by` dicts
            are keyed by this, so a diverging key function here would make
            every guard silently never match."""
            return step.re_analysis_step or step.qualified_name or ""

        def _guard_check(step) -> tuple[bool, str]:
            """(may_run, reason). True with no reason when the step is
            unconditional (no PlannedStep found, or an empty `guarded_by`) —
            the overwhelmingly common case today, since authored guards are
            held at "Any" (step_outcome.py's 2026-08-21 decision) until a
            real branching definition exists.

            Egeria fires a step if ANY ONE of its guarded incoming links is
            satisfied (verified against EngineActionHandler.java — see
            docs/survey-guard-evaluation-design.md §1). This instead requires
            EVERY guarded incoming edge to match, the same simplification
            prefect/flows.py::run_planned_step_task already makes — kept
            consistent between the two paths rather than fixed in one and not
            the other; §2.3 of the design doc explains why it makes no
            observable difference to any definition that exists today.
            `mandatoryGuard`'s join semantics are not evaluated at all: the
            plan has nowhere to carry them (§2.2) — a gap, not a choice.
            """
            planned = plan_by_key.get(step.qualified_name)
            guarded_by = getattr(planned, "guarded_by", None) if planned else None
            if not guarded_by:
                return True, ""
            for upstream_key, required in guarded_by.items():
                produced = produced_guard.get(upstream_key)
                if produced != required:
                    return False, (
                        f"guard {required!r} required from {upstream_key!r} but it "
                        + (f"produced {produced!r}" if upstream_key in produced_guard
                           else "was never recorded — no step output in this run "
                                "carried a 'guard' key for it")
                    )
            return True, ""

        from resource_explorer.config import get_config
        from resource_explorer.surveyors.prefect_adapter import PrefectFlowRunCancelled, run_prefect_step

        # Steps that exist only as Prefect flows (resource_explorer/prefect/flows.py)
        # and have no STEP_REGISTRY entry, so there is nothing local to run them
        # with. Named here rather than inline so the list is findable from both
        # sides; they should carry executes_at="prefect" once authored.
        PREFECT_ONLY_STEPS = ("soda_data_quality", "great_expectations_validation")

        def _use_prefect(step) -> bool:
            """Which engine runs this step.

            executes_at is what the definition asked for, and it is honoured.
            `prefect.enabled` says Prefect is reachable — it does not re-route a
            step that named a different engine; `prefect.route_local_steps` is
            the explicit opt-in for that, because taking RE's own steps to
            Prefect is a real deployment choice but not one to make silently.

            `engine_override` (this run's local parameter — see `run`'s own
            docstring) takes precedence over both config values, but ONLY for
            a step tagged `executes_at="resource-explorer"` — the one case
            where "run this the other way" is a genuine, safe choice between
            two runners of the same function. A step already forced to
            Prefect (`executes_at="prefect"`, or a PREFECT_ONLY_STEPS entry
            with no local implementation at all) is left alone by
            engine_override="resource-explorer": there is nothing local to
            force it onto. `executes_at="egeria"` steps never reach this
            function's True branches at all — they fall through to `return
            False` below regardless of engine_override, same as today.
            """
            if step.executes_at == "prefect" or step.re_analysis_step in PREFECT_ONLY_STEPS:
                return True
            if step.executes_at == "resource-explorer":
                if engine_override == "prefect":
                    return True
                if engine_override == "resource-explorer":
                    return False
                try:
                    cfg = get_config().prefect
                    return bool(cfg.enabled and getattr(cfg, "route_local_steps", False))
                except Exception:
                    return False
            return False

        i = 0
        n = len(pending_steps)
        while i < n:
            step = pending_steps[i]

            # Egeria's guard, evaluated — see docs/survey-guard-evaluation-design.md
            # §1/§5. A step whose guard is not satisfied by what its upstream(s)
            # produced did not happen, on this branch, in this run: emitted as
            # SKIPPED_BY_DESIGN via result_status's own vocabulary, not as
            # silence and not as an error (a branch not taken is not a failure).
            may_run, guard_reason = _guard_check(step)
            if not may_run:
                log.info("Skipping %s — %s", step.qualified_name, guard_reason)
                steps_report.append({
                    "step": step.qualified_name,
                    "re_analysis_step": _step_key(step),
                    "status": result_status.SKIPPED_BY_DESIGN,
                    "detail": guard_reason,
                })
                i += 1
                continue

            use_prefect = _use_prefect(step)

            # D1 (docs/survey-tab-unification-plan.md) — batch a run of consecutive
            # plain "resource-explorer" steps into one adapter.run_batch()
            # call instead of dispatching each individually, when the
            # adapter supports it (repo does; database/filesystem don't yet
            # — run_batch=None there keeps their exact prior per-step path).
            # Only steps this adapter actually recognizes are eligible; an
            # unknown step_key breaks the group so it still gets the normal
            # "unknown_step" report below, not silently absorbed.
            if (
                not use_prefect
                and step.executes_at == "resource-explorer"
                and adapter.run_batch is not None
                and step.re_analysis_step in adapter.re_analysis_steps
            ):
                group = [step]
                j = i + 1
                while j < n:
                    nxt = pending_steps[j]
                    if (
                        _use_prefect(nxt)
                        or nxt.executes_at != "resource-explorer"
                        or nxt.re_analysis_step not in adapter.re_analysis_steps
                        or not _guard_check(nxt)[0]
                    ):
                        break
                    group.append(nxt)
                    j += 1

                if len(group) > 1:
                    step_keys = [s.re_analysis_step for s in group]
                    try:
                        output = adapter.run_batch(entity, self.registry, step_keys, **runner_kwargs)
                        _stamp_definition_provenance(output)
                        step_outputs.append(output)
                        batch_errors = output.get("errors") or []
                        # Batched steps share one status — real, minor cost
                        # of the fix: a partial failure within the group
                        # can't be pinpointed to the exact step_key without
                        # instantiating each surveyor just to read its
                        # step_name, which the batch call deliberately
                        # avoids. The combined error text is still fully
                        # visible in `errors` below and in each step's own
                        # "detail" here — not silently swallowed, just not
                        # individually attributed.
                        status = "error" if batch_errors else "ok"
                        # One output for the whole group (`run_batch`'s point —
                        # see its docstring), so one guard for the whole group:
                        # the same "shared status" limitation the comment above
                        # already names, not a new one.
                        batch_guard = output.get("guard") if isinstance(output, dict) else None
                        for s in group:
                            entry = {"step": s.qualified_name, "re_analysis_step": _step_key(s), "status": status}
                            if batch_errors:
                                entry["detail"] = "; ".join(batch_errors)
                            steps_report.append(entry)
                            produced_guard[_step_key(s)] = batch_guard
                        errors.extend(batch_errors)
                    except Exception as exc:
                        msg = f"RE batch steps {step_keys} failed: {exc}"
                        log.exception(msg)
                        errors.append(msg)
                        for s in group:
                            steps_report.append({"step": s.qualified_name, "re_analysis_step": _step_key(s), "status": "error", "detail": str(exc)})
                    i = j
                    continue
                # group of exactly 1 — fall through to the identical
                # single-step path below, unchanged from before batching.

            if use_prefect:
                try:
                    # Measured like any other step (§17.2's `executor` axis is
                    # "local / prefect / egeria / remote … so native and
                    # remote runs sit on the same board"). Only the PER-STEP
                    # Prefect route is covered: a whole definition handed to
                    # `_run_via_prefect` runs inside the flow and writes no
                    # vector — a known gap, of a piece with the one
                    # `_any_step_needs_prerequisites` documents, and the same
                    # fix closes both.
                    _pf_info = self._step_info(adapter, step.re_analysis_step)
                    with step_cost_observer.observe(
                        step.re_analysis_step,
                        getattr(_pf_info, "fetch_cost", ""),
                        getattr(_pf_info, "compute_cost", ""),
                        executor="prefect", source="prefect",
                    ) as _pf_observed:
                        output = run_prefect_step(entity_type, entity.slug, step.re_analysis_step, runner_kwargs)
                    self._record_cost(_pf_observed, entity_type, slug, output, surveyed_at)
                    _stamp_definition_provenance(output)
                    step_outputs.append(output)
                    steps_report.append({"step": step.qualified_name, "re_analysis_step": _step_key(step), "status": "ok", "engine": "prefect"})
                    produced_guard[_step_key(step)] = output.get("guard") if isinstance(output, dict) else None
                except PrefectFlowRunCancelled as exc:
                    # Distinct from a generic failure — this is the user
                    # actually stopping the survey (via the Admin "⚡ Prefect"
                    # panel's Cancel button), not something breaking. Surfaced
                    # as its own status so the activity log reads "cancelled",
                    # not "error", for what was a deliberate action.
                    msg = f"Prefect step '{step.re_analysis_step}' was cancelled: {exc}"
                    log.info(msg)
                    errors.append(msg)
                    steps_report.append({"step": step.qualified_name, "re_analysis_step": _step_key(step), "status": "cancelled", "engine": "prefect"})
                except Exception as exc:
                    msg = f"Prefect step '{step.re_analysis_step}' failed: {exc}"
                    log.exception(msg)
                    errors.append(msg)
                    steps_report.append({"step": step.qualified_name, "re_analysis_step": _step_key(step), "status": "error", "engine": "prefect"})
            elif step.executes_at == "resource-explorer":
                runner = adapter.re_analysis_steps.get(step.re_analysis_step)
                if runner is None:
                    msg = (
                        f"Unknown re_analysis_step '{step.re_analysis_step}' in step "
                        f"{step.qualified_name} — {entity_type} adapter only knows: "
                        f"{sorted(adapter.re_analysis_steps)}"
                    )
                    log.error(msg)
                    errors.append(msg)
                    steps_report.append({"step": step.qualified_name, "re_analysis_step": _step_key(step), "status": "unknown_step"})
                    i += 1
                    continue
                # §17.1 — prerequisites, on the shared path.
                #
                # `step_preconditions.evaluate` had exactly ONE call site
                # before today (`SurveyOrchestrator.run`), so a step run
                # through a Survey Definition was never gated at all: the
                # database steps, whose chain §17.4 names as the reason this
                # slice exists, had no precondition mechanism reaching them.
                # This is deliberately the SAME resolver the orchestrator
                # calls, not a parallel one — the design's intent is one
                # mechanism, and two would drift the way the two guard
                # evaluations in this file already had to be kept in step.
                resolution = self._resolve_prerequisites(
                    adapter, entity_type, entity, step.re_analysis_step,
                    surveyed_at, runner_kwargs, steps_report, step_outputs,
                    errors, auto_ran, capability_consented,
                )
                if resolution is not None and not resolution.may_run:
                    entry = {
                        "step": step.qualified_name,
                        "re_analysis_step": _step_key(step),
                        "status": result_status.SKIPPED_BY_DESIGN,
                        "detail": resolution.reason,
                    }
                    if resolution.proposal is not None:
                        entry["proposal"] = resolution.proposal.as_dict()
                        proposals.append(resolution.proposal.as_dict())
                    log.info("Skipping %s — %s", step.qualified_name, resolution.reason)
                    steps_report.append(entry)
                    i += 1
                    continue
                info = self._step_info(adapter, step.re_analysis_step)
                try:
                    with step_cost_observer.observe(
                        step.re_analysis_step,
                        getattr(info, "fetch_cost", ""),
                        getattr(info, "compute_cost", ""),
                        executor="local", source="local",
                        # Carries §17.1's attribution when this whole run IS
                        # an accepted prerequisite proposal — otherwise the
                        # accepted chain's cost lands on the board with no
                        # trace of the question that caused it, which is the
                        # one thing `demanded_by` exists to prevent.
                        demanded_by=demanded_by,
                    ) as observed:
                        output = runner(entity, self.registry, **runner_kwargs)
                    self._record_cost(observed, entity_type, slug, output, surveyed_at)
                    _stamp_definition_provenance(output)
                    step_outputs.append(output)
                    ok_entry = {"step": step.qualified_name,
                                "re_analysis_step": _step_key(step), "status": "ok"}
                    # "Run partially AND SAY SO" (REPLY-DATABASE-CREDENTIAL-
                    # CAPABILITY-VISIBILITY.md §7.1). An advisory proposal
                    # does not stop the step — see `Proposal.advisory` — but
                    # the run must not then come back indistinguishable from
                    # one made with a credential that could see everything.
                    # Without this the step reports plain "ok" and the whole
                    # gate is invisible on every unattended path.
                    if (resolution is not None and resolution.proposal is not None
                            and resolution.proposal.advisory):
                        ok_entry["capability"] = resolution.proposal.as_dict()
                    steps_report.append(ok_entry)
                    produced_guard[_step_key(step)] = output.get("guard") if isinstance(output, dict) else None
                except Exception as exc:
                    msg = f"RE step '{step.re_analysis_step}' failed: {exc}"
                    log.exception(msg)
                    errors.append(msg)
                    steps_report.append({"step": step.qualified_name, "re_analysis_step": _step_key(step), "status": "error"})
            elif step.executes_at in adapter.other_engine_handlers:
                handler = adapter.other_engine_handlers[step.executes_at]
                try:
                    outcome = handler(entity, self.registry, step, **runner_kwargs)
                    # Handlers that only trigger-and-forget (no waiting, e.g. an
                    # engine with no synchronous result to read back yet) return
                    # a dict with no "status" key, which keeps the historic
                    # "triggered" wording rather than implying completion.
                    # Handlers that wait for a real terminal result (e.g.
                    # database/filesystem's _trigger_egeria_native_survey, which
                    # polls to completion and reads back real annotations via
                    # egeria_async_survey_result.py) set status="ok" themselves —
                    # reported here as-is instead of overwritten, so a genuinely
                    # completed step is never misreported as merely "triggered".
                    if isinstance(outcome, dict):
                        _stamp_definition_provenance(outcome)
                        step_outputs.append(outcome)
                    status = outcome.get("status", "triggered") if isinstance(outcome, dict) else "triggered"
                    # `detail` feeds a json.dumps() call below (the activity-log
                    # summary), so it must stay JSON-safe — outcome's own
                    # "annotations" key (when present) carries real Annotation
                    # dataclass instances, not plain dicts, so it is summarized
                    # as a count here rather than embedded whole. The instances
                    # themselves still flow to publish() via step_outputs above.
                    detail = None
                    if isinstance(outcome, dict):
                        detail = {k: v for k, v in outcome.items() if k != "annotations"}
                        if "annotations" in outcome:
                            detail["annotation_count"] = len(outcome["annotations"] or [])
                    # `source` — which engine's numbers these actually are
                    # ("egeria" / "egeria-custom" / "custom" / "error", the
                    # egeria-adaptive handler's vocabulary — see
                    # database/survey_definition_adapter.py's
                    # `_run_egeria_adaptive`) — is promoted to a top-level
                    # field on the step's own steps_report entry, not left
                    # nested only inside `detail`. This is what makes a run's
                    # provenance visible in the run report itself rather than
                    # only in the handler's own return value: before this, a
                    # step's engine was directly observable but WHICH of
                    # several strategies an adaptive handler actually took
                    # was not, without reading raw `detail`.
                    entry = {"step": step.qualified_name, "re_analysis_step": _step_key(step), "status": status}
                    if isinstance(outcome, dict) and "source" in outcome:
                        entry["source"] = outcome["source"]
                    if detail is not None:
                        entry["detail"] = detail
                    steps_report.append(entry)
                    if isinstance(outcome, dict):
                        produced_guard[_step_key(step)] = outcome.get("guard")
                except Exception as exc:
                    msg = f"Failed to trigger {step.executes_at} for step '{step.qualified_name}': {exc}"
                    log.exception(msg)
                    errors.append(msg)
                    steps_report.append({"step": step.qualified_name, "re_analysis_step": _step_key(step), "status": "error"})
            elif step.executes_at == "egeria":
                # Real, live-reported gap (2026-08-24 — "closing the stub"):
                # this used to log.info and move on with no entry in `errors`
                # at all, so a mixed Survey whose Egeria-native step has no
                # registered other_engine_handlers reported "Survey
                # Definition run complete" — a silent lie, not a warning.
                # RE's own client-side step-walk is the only thing driving
                # execution today (per docs/unified-survey-execution-model-
                # plan.md's D1/D8 — Egeria's MAS doesn't independently create
                # Engine Actions for any step type yet), so a step that lands
                # here genuinely never runs anywhere, not "runs elsewhere
                # without RE watching." That's a real failure of this run's
                # completeness, not a benign no-op — now counted in `errors`
                # accordingly, distinct wording from an actual step
                # exception so it reads as "never executed," not "broke."
                msg = (
                    f"Step {step.qualified_name} was not executed: executes_at=egeria but no "
                    f"other_engine_handlers['egeria'] is registered for entity_type={entity_type!r} "
                    "(see database/survey_definition_adapter.py's _trigger_egeria_native_survey "
                    "for the pattern to add one)"
                )
                log.warning(msg)
                errors.append(msg)
                steps_report.append({"step": step.qualified_name, "re_analysis_step": _step_key(step), "status": "not_executed_no_egeria_handler"})
            else:
                msg = (
                    f"Skipping step {step.qualified_name}: unrecognized executes_at="
                    f"{step.executes_at!r} (recognized values are 'resource-explorer', "
                    "'prefect', 'egeria', and any key an adapter registers in its own "
                    "other_engine_handlers — e.g. 'egeria-adaptive' for database/"
                    "filesystem, see EXECUTION-MODES-HYBRID-CLARIFICATION.md)"
                )
                log.warning(msg)
                errors.append(msg)
                steps_report.append({"step": step.qualified_name, "re_analysis_step": _step_key(step), "status": "unrecognized_engine"})

            i += 1

        # Gated on the resource actually having an assigned Egeria Project — this
        # used to publish unconditionally for any repo a Survey Definition ran
        # against, decided-in-Egeria or not (confirmed live 2026-08-27, alongside
        # egeria.py's manual-publish route, which HAS always gated this). Findings
        # are stored locally either way; only the automatic Egeria write is
        # skipped for an unassigned resource. Manual publish is still available
        # to catalog an unassigned resource explicitly, or to re-publish.
        report_guid = ""
        published: bool | str = False
        if step_outputs and self.registry.has_assigned_egeria_project(entity_type, slug):
            # Same per-run choice run_analysis() honours (project owner,
            # 2026-09-13) — "wait"/"background" from the caller override
            # RunsConfig.publish_inline for this run only; not asked (None,
            # every pre-existing caller) falls back to the config default,
            # same three states (True/False/"queued"), no unconditional True.
            from resource_explorer.config import get_config

            if publish == "background":
                defer_drain = True
            elif publish == "wait":
                defer_drain = False
            else:
                defer_drain = not get_config().runs.publish_inline
            # Defensive signature check, same pattern this module's own
            # get_analysis_results route already uses for an optional
            # reader parameter: an adapter.publish that predates this choice
            # (a test double standing in a plain 4-arg function, or a
            # not-yet-updated resource type) does not accept defer_drain —
            # calling it with an unexpected kwarg would raise, for a run that
            # never asked to change behaviour.
            import inspect

            publish_kwargs = {}
            if "defer_drain" in inspect.signature(adapter.publish).parameters:
                publish_kwargs["defer_drain"] = defer_drain
            try:
                report_guid = adapter.publish(
                    entity, step_outputs, surveyed_at, self.registry, **publish_kwargs,
                )
                published = "queued" if (defer_drain and publish_kwargs) else True
            except Exception as exc:
                msg = f"Failed to publish results to Egeria: {exc}"
                log.exception(msg)
                errors.append(msg)

        # Record that this Survey Definition ran.
        #
        # It never did. The executor has carried zero log_survey calls since it
        # was written, so a Survey Definition could run any number of times and
        # its card still read "Never run" — get_survey_definition_last_activity
        # looks for an activity row of operation 'survey' keyed on
        # `survey_definition_ref`, and nothing wrote one. The card was reporting
        # the activity log accurately; the log was empty.
        #
        # The ref is what makes the row attributable to THIS definition rather
        # than to the repo generally.
        ran = sum(1 for r in steps_report if r.get("status") in ("ok", "triggered"))
        try:
            log_survey(
                self.registry, entity_type, slug,
                getattr(entity, "display_name", "") or slug,
                getattr(entity, "github_url", "") or "",
                intent="discovery",
                status="error" if errors else "ok",
                summary=(f"{survey_def.display_name}: {ran} of {len(steps_report)} step(s) ran"
                         + (f", {len(errors)} error(s)" if errors else "")),
                detail=json.dumps({
                    "survey_definition_ref": process_qn,
                    "process_guid": process_guid,
                    "steps": steps_report,
                    "errors": errors,
                    "published": published,
                    "egeria_report_guid": report_guid,
                }),
            )
            run_recorded = True
        except Exception as exc:
            # A survey that ran must not report failure because its bookkeeping
            # failed — but the caller has to be able to tell. Returned as a
            # field, not just logged: a silent miss here is precisely what
            # produced "Never run", and a second silent miss would look
            # identical to the first.
            run_recorded = False
            log.warning("could not record the survey run for %s: %s", process_qn, exc)

        return {
            "source": "survey-definition",
            "entity_type": entity_type,
            "slug": slug,
            "process_guid": process_guid,
            "process_qualified_name": process_qn,
            "surveyed_at": surveyed_at,
            "steps": steps_report,
            "errors": errors,
            # §17.1 — chains awaiting consent, and prerequisites this run took
            # on itself. Returned rather than only logged: a proposal nobody
            # can see is a skip with extra words, and "run it?" has to be
            # offerable from the response the UI already has.
            "proposals": proposals,
            "auto_ran_steps": sorted(auto_ran),
            "egeria_report_guid": report_guid,
            "published": published,
            # False when the run happened but could not be written to the
            # activity log — the card will say "Never run" and be wrong.
            "run_recorded": run_recorded,
        }

    # ── §17.1: prerequisites, shared across every resource type ──────────
    def _any_step_needs_prerequisites(self, adapter, entity, survey_def,
                                      surveyed_at: str) -> bool:
        """Whether §17.1 has anything to say about this definition. Runs nothing.

        **This is a GAP being contained, and it is worth saying so plainly.**
        Design §17.1 states that "on the Prefect path this is task
        dependencies and Prefect renders the chain itself". Checked against
        the code (2026-09-23) that is **not true today**:
        `survey_execution_plan.build_plan` builds Prefect's task graph from
        the definition's authored `Link Next Process Step` edges and their
        guards — the graph an author drew. It has never read
        `requires_context`, and `PRODUCES` did not exist until this change, so
        a Prefect-orchestrated definition would dispatch a step whose stored
        input is absent, exactly as the local loop did before
        `step_preconditions` was written. The design's sentence describes
        where the mechanism BELONGS, not where it is.

        Rather than build a second resolver inside the Prefect flow — the
        thing §17.1 is explicit about not wanting — this keeps one resolver
        and routes a definition that needs it down the local loop, which
        handles all three outcomes (auto-run, proposal, skip) correctly and
        still calls `run_prefect_step` per step where `executes_at` asks for
        it. A definition with nothing to resolve, which is every definition
        that exists today, goes to Prefect unchanged.

        The real fix is for the plan builder to fold `PRODUCES` edges into the
        task graph, so Prefect renders the prerequisite chain the way the
        design describes. That is a change to `survey_execution_plan` and to
        the flow, and it is a slice of its own.
        """
        provider = getattr(adapter, "step_registry", None)
        if provider is None:
            return False
        try:
            registry_map = provider() or {}
        except Exception:  # pragma: no cover - provider guard
            return False
        from resource_explorer.surveyors import prerequisite_resolver

        for step in survey_def.steps:
            key = getattr(step, "re_analysis_step", "") or ""
            if not registry_map.get(key):
                continue
            try:
                if prerequisite_resolver.resolve(
                    self.registry, entity, key, registry_map,
                    surveyed_at=surveyed_at,
                ).status != prerequisite_resolver.SATISFIED:
                    return True
            except prerequisite_resolver.PrerequisiteCycleError:
                # A declaration bug. Take the local loop, where it will be
                # raised at dispatch with the step it belongs to, rather than
                # silently handing the definition to Prefect as if nothing
                # were wrong.
                return True
        return False

    @staticmethod
    def _step_info(adapter, step_key: str):
        """This step's declared costs/preconditions/PRODUCES, or None when its
        resource type has not declared a step registry. None is honest: a type
        with no declarations has no preconditions to check and no tier to
        compare against, and every step of it dispatches exactly as it did
        before §17.1 existed."""
        provider = getattr(adapter, "step_registry", None)
        if provider is None:
            return None
        try:
            return (provider() or {}).get(step_key)
        except Exception as exc:  # pragma: no cover - provider guard
            log.debug("could not read the step registry: %s", exc)
            return None

    def _resolve_prerequisites(
        self, adapter, entity_type: str, entity, step_key: str,
        surveyed_at: str, runner_kwargs: dict, steps_report: list,
        step_outputs: list, errors: list, auto_ran: set,
        capability_consented: bool = False,
    ):
        """Resolve, auto-run what the budget covers, and return the (re-checked)
        Resolution. None when this resource type declares no step registry.

        The re-check after an auto-run is not defensive noise: a producer that
        ran and legitimately found nothing leaves the precondition still unmet
        (CLAUDE.md's Gradle/BOM case, where `repo_manifest_parse` recovers 216
        coordinates and zero versions), and the demanding step must then be
        skipped with a reason rather than dispatched because a producer
        "succeeded".
        """
        provider = getattr(adapter, "step_registry", None)
        if provider is None:
            return None
        try:
            registry_map = provider() or {}
        except Exception as exc:  # pragma: no cover - provider guard
            log.debug("could not read the step registry: %s", exc)
            return None

        from resource_explorer.surveyors import prerequisite_resolver

        def _resolve():
            return prerequisite_resolver.resolve(
                self.registry, entity, step_key, registry_map,
                surveyed_at=surveyed_at, already_ran=auto_ran,
                capability_consented=capability_consented,
            )

        resolution = _resolve()
        for producer in resolution.auto_run:
            self._auto_run_producer(
                adapter, entity_type, entity, producer, step_key, resolution,
                surveyed_at, runner_kwargs, steps_report, step_outputs, errors)
            auto_ran.add(producer)
        if resolution.auto_run:
            resolution = _resolve()
        return resolution

    def _auto_run_producer(
        self, adapter, entity_type: str, entity, producer: str,
        demanding_step: str, resolution, surveyed_at: str, runner_kwargs: dict,
        steps_report: list, step_outputs: list, errors: list,
    ) -> None:
        """Run one prerequisite and record that it happened (§17.1 condition 3).

        Three records, because an auto-run is a result and not an omission:
        its own entry in `steps_report` carrying `demanded_by`, an activity-log
        entry (CLAUDE.md rule 16), and a `step_runs` row whose `demanded_by`
        attributes the cost to the step that asked for it as well as to the
        step that paid it.
        """
        runner = adapter.re_analysis_steps.get(producer)
        precondition = (resolution.proposal.preconditions[0]
                        if resolution.proposal and resolution.proposal.preconditions
                        else (resolution.dead_ends[0] if resolution.dead_ends else ""))
        why = (f"ran {producer} because {demanding_step} required "
               f"{precondition or 'its stored output'}")
        if runner is None:
            msg = (f"prerequisite {producer!r} for {demanding_step!r} is declared but "
                   f"the {entity_type} adapter has no runner for it")
            log.error(msg)
            errors.append(msg)
            return
        info = self._step_info(adapter, producer)
        output = None
        try:
            with step_cost_observer.observe(
                producer, getattr(info, "fetch_cost", ""),
                getattr(info, "compute_cost", ""),
                executor="local", source="local", demanded_by=demanding_step,
            ) as observed:
                output = runner(entity, self.registry, **runner_kwargs)
            self._record_cost(observed, entity_type, entity.slug, output, surveyed_at)
        except Exception as exc:
            # Reported, not raised: the demanding step's precondition is then
            # simply still unmet, which the re-check sees, and it is skipped
            # with a reason — the outcome it would have had anyway.
            msg = f"prerequisite step '{producer}' failed: {exc}"
            log.exception(msg)
            errors.append(msg)
            steps_report.append({
                "step": producer, "re_analysis_step": producer, "status": "error",
                "detail": msg, "demanded_by": demanding_step, "auto_run": True,
            })
            return
        if isinstance(output, dict):
            step_outputs.append(output)
        steps_report.append({
            "step": producer, "re_analysis_step": producer, "status": "ok",
            "auto_run": True, "demanded_by": demanding_step, "detail": why,
        })
        log.info("%s", why)
        try:
            log_survey(
                self.registry, entity_type, entity.slug,
                getattr(entity, "display_name", "") or entity.slug,
                getattr(entity, "github_url", "") or "",
                intent="analysis", status="ok", summary=why,
                detail=json.dumps({"prerequisite_auto_run": producer,
                                   "demanded_by": demanding_step,
                                   "precondition": precondition}),
            )
        except Exception as exc:
            log.warning("could not log the prerequisite auto-run: %s", exc)

    def _record_cost(self, observed, entity_type: str, slug: str, output,
                     surveyed_at: str) -> None:
        """Attach what the step produced, then persist the vector.

        Yield is attached BEFORE the disagreement is judged, for the reason
        `step_cost_observer` states at length: a duration taken while the
        thing being measured was not happening is not evidence about a
        declaration.
        """
        if not observed:
            return
        obs = observed[0]
        annotations = (output or {}).get("annotations") if isinstance(output, dict) else None
        obs.annotations, obs.outcomes = step_cost_observer.describe_work(annotations)
        obs.disagreement = step_cost_observer._disagreement(obs)
        step_cost_observer.record(self.registry, slug, obs, surveyed_at,
                                  entity_type=entity_type)

    def run_synthetic_step(
        self,
        entity_type: str,
        slug: str,
        re_analysis_step: str,
        executes_at: str,
        display_name: str | None = None,
        publish: str | None = None,
        engine_override: str | None = None,
        demanded_by: str = "",
        capability_consented: bool = False,
        **runner_kwargs: Any,
    ) -> dict:
        """Run ONE step through the same dispatch loop `run()` uses, without
        first fetching (or authoring) a real Egeria-hosted Survey Definition
        process — a one-step, in-process-only `SurveyDefinition`/`SurveyStep`
        pair, never written to or read from Egeria.

        Built for the `egeria-adaptive` fold-in
        (docs/design-notes/EXECUTION-MODES-HYBRID-CLARIFICATION.md): the
        web/CLI "survey this database/filesystem" routes used to call
        `HybridDatabaseSurveyor`/`run_hybrid_filesystem_survey` directly,
        bypassing `executes_at` routing entirely. They now build a synthetic
        single-step definition tagged `executes_at="egeria-adaptive"` and run
        it through here, so the strategy-selector logic lives in
        `other_engine_handlers["egeria-adaptive"]` like any other engine, and
        its `source` provenance is visible in `steps_report` the same way a
        real Survey Definition's steps are — one mechanism, not two.

        `executes_at` is deliberately a parameter rather than hardcoded to
        "egeria-adaptive": this is a general "run a single ad hoc step
        through the executor" primitive, and a future caller wanting
        "resource-explorer" or "egeria" for one step needs no separate
        method.
        """
        from resource_explorer.surveyors.survey_definition_reader import (
            SurveyDefinition,
            SurveyStep,
        )

        adapter = get_adapter(entity_type)
        entity = adapter.get_entity(self.registry, slug)
        if entity is None:
            raise SurveyDefinitionExecutorError(
                f"{entity_type} '{slug}' not found in registry"
            )

        qualified_name = f"synthetic::{entity_type}::{slug}::{re_analysis_step}"
        step = SurveyStep(
            guid="",
            display_name=display_name or re_analysis_step,
            qualified_name=qualified_name,
            executes_at=executes_at,
            re_analysis_step=re_analysis_step,
        )
        survey_def = SurveyDefinition(
            process_guid="",
            display_name=display_name or re_analysis_step,
            qualified_name=qualified_name,
            supported_technology_type=None,
            steps=[step],
        )
        return self._execute(
            entity_type=entity_type,
            slug=slug,
            entity=entity,
            survey_def=survey_def,
            process_guid="",
            process_qn=survey_def.qualified_name,
            publish=publish,
            # Passed through rather than hardcoded to None (2026-09-23): an
            # accepted prerequisite proposal (§17.1) names
            # "resource-explorer", so the run the user consented to is the
            # run they were quoted a cost for, and is measured.
            engine_override=engine_override,
            runner_kwargs=runner_kwargs,
            demanded_by=demanded_by,
            # §7.1's first choice, carried from the user's "run it anyway".
            # Without it the re-resolve inside `_execute` would raise the very
            # shortfall the user just accepted and skip the step.
            capability_consented=capability_consented,
        )

    def _run_via_prefect(self, entity_type, entity, survey_def, runner_kwargs):
        """(steps_report, step_outputs, errors) from one Prefect flow, or None.

        Returns None rather than raising when the plan cannot be built or
        Prefect cannot be reached, so an orchestration problem degrades to the
        local loop instead of failing a survey the loop could have run. The
        reason is logged — a silent fallback would make "Prefect ran this" and
        "Prefect was never reachable" look identical in the report.

        Only called when `_all_steps_prefect_runnable(survey_def)` is true —
        every step here runs through `run_surveyor_step_task`, the plain
        local-analysis-step runner, with no per-step engine check of its own.
        A step tagged `executes_at="egeria"` (or anything else that isn't
        "resource-explorer"/"prefect") has no business here; see that
        function's docstring for the live incident this guard fixes.

        Passes the adapter's step registry into `build_plan` so PRODUCES
        edges get folded into the Prefect task graph (§17.1's Prefect-side
        gap — see `survey_execution_plan._add_produces_edges`). Callers
        already route a definition with anything for the runtime resolver to
        actually DO (`_any_step_needs_prerequisites`) to the local loop
        instead of here, so what reaches this function is: definitions with
        no unmet precondition today, plus whatever this folding now corrects
        structurally at build time.
        """
        from resource_explorer.surveyors.survey_execution_plan import (
            CyclicPlanError,
            MissingPrerequisiteError,
            PrerequisiteTierError,
            build_plan,
            serialise,
        )

        adapter = get_adapter(entity_type)
        provider = getattr(adapter, "step_registry", None)
        step_registry = None
        if provider is not None:
            try:
                step_registry = provider() or {}
            except Exception as exc:  # pragma: no cover - provider guard
                log.debug("could not read the step registry for %s: %s",
                          entity_type, exc)
                # Explicit, not redundant: a registry that failed to load is
                # the same as "not declared" for this call's purposes — no
                # PRODUCES-folding, no tier check, `build_plan` behaves as it
                # did before this change — and that fallback must be a
                # decision this line makes, not merely a log line implying
                # one.
                step_registry = None

        try:
            plan = build_plan(survey_def, step_registry=step_registry)
        except CyclicPlanError as exc:
            # Not a fallback case: the local loop would run a cyclic definition
            # in list order and report success for a survey that cannot be
            # ordered at all.
            raise SurveyDefinitionExecutorError(str(exc)) from exc
        except (MissingPrerequisiteError, PrerequisiteTierError) as exc:
            # Also not a fallback case, and deliberately so (see
            # `PrerequisiteTierError`'s docstring): silently falling back to
            # the local loop here would hide a build-time-detectable problem
            # behind "Prefect just wasn't reachable", which is exactly the
            # ambiguity this function's own docstring says a silent fallback
            # must not create.
            raise SurveyDefinitionExecutorError(str(exc)) from exc

        if not plan.steps:
            return None
        try:
            from resource_explorer.prefect.flows import re_survey_definition_flow

            report = re_survey_definition_flow(
                entity_type=entity_type, slug=entity.slug,
                plan=serialise(plan), runner_kwargs=runner_kwargs or {},
            )
        except Exception as exc:
            log.warning(
                "Prefect orchestration unavailable for %s (%s) — falling back to "
                "in-process sequencing", survey_def.qualified_name, exc)
            return None

        return (
            [{k: v for k, v in entry.items() if k != "output"} for entry in report],
            [entry["output"] for entry in report
             if entry.get("status") == "ok" and entry.get("output")],
            [f"{e['step_key']}: {e.get('detail', 'failed')}"
             for e in report if e.get("status") == "error"],
        )

    def _resolve_process_guid(
        self,
        entity_type: str,
        slug: str,
        technology_type: str,
        survey_definition_ref: str | None,
        refresh_definition: bool,
    ) -> tuple:
        if survey_definition_ref:
            guid = self.reader.find_process_guid_by_name(survey_definition_ref)
            if not guid:
                raise SurveyDefinitionExecutorError(
                    f"No Survey Definition found matching '{survey_definition_ref}'"
                )
            self.registry.set_survey_definition_guid(
                entity_type, slug, technology_type, guid, survey_definition_ref
            )
            return guid, survey_definition_ref

        if not refresh_definition:
            cached = self.registry.get_survey_definition_guid(entity_type, slug, technology_type)
            if cached:
                return cached["process_guid"], cached["process_qualified_name"]

        candidates = self.reader.find_candidate_process_guids(technology_type)
        if not candidates:
            raise SurveyDefinitionExecutorError(
                f"No Survey Definition found in Egeria for technology type "
                f"'{technology_type}'. Author one first (see "
                "docs/egeria-collaboration-and-survey-model.md section 6.3)."
            )
        if len(candidates) > 1:
            names = ", ".join(c["qualified_name"] for c in candidates)
            raise SurveyDefinitionExecutorError(
                f"Multiple Survey Definitions found for technology type "
                f"'{technology_type}': {names}. Disambiguate with --survey-definition."
            )

        chosen = candidates[0]
        self.registry.set_survey_definition_guid(
            entity_type, slug, technology_type, chosen["guid"], chosen["qualified_name"]
        )
        return chosen["guid"], chosen["qualified_name"]


def run_survey_definition(
    entity_type: str, slug: str, registry=None,
    engine_override: str | None = None, **kwargs: Any,
) -> dict:
    """Convenience function mirroring run_hybrid_survey's shape.

    `engine_override` is spelled out explicitly (rather than left to flow
    through **kwargs implicitly) so callers — the CLI, the web route — see it
    in this function's own signature. See SurveyDefinitionExecutor.run's
    docstring for what the three legal values do.
    """
    if registry is None:
        from resource_explorer.registry import ProjectRegistry
        registry = ProjectRegistry()
    executor = SurveyDefinitionExecutor(registry)
    return executor.run(entity_type, slug, engine_override=engine_override, **kwargs)


def _all_steps_prefect_runnable(survey_def) -> bool:
    """Whether every step in this definition can actually be run by
    `run_surveyor_step_task` — the plain local-analysis-step runner Prefect's
    flow calls for EVERY step in its plan, with no per-step engine check of
    its own (`prefect/flows.py`'s `run_planned_step_task` -> `run_surveyor_
    step_task.fn`).

    Found 2026-09-19, live, the first time PREFECT_ENABLED defaulted to true
    with a real reachable server on a mixed-engine definition:
    `_run_via_prefect` handed the WHOLE plan to Prefect regardless of what
    each step's `executes_at` said, so an `executes_at="egeria"` step never
    reached its own `other_engine_handlers["egeria"]` handler at all — it
    silently ran through the local-analysis-step path instead, which has no
    `re_analysis_step` for it and fails with "Entity ... not found" (or worse,
    for an entity type that DOES coincidentally have a same-named local step,
    would have run the wrong thing under the right-looking status). Repo
    Survey Definitions never hit this (repos have no Egeria-coordinated path
    today), which is why phase 2's live verification — one step,
    `repo_arch_coupling`, called directly via `run_prefect_step` rather than
    through this whole-definition path — never exercised it.

    The correct fix is per-step engine routing inside the Prefect flow
    itself, matching what the local loop below already does correctly. Until
    that's built, the safe answer is: don't send Prefect a definition it
    cannot execute correctly — skip whole-definition orchestration entirely
    for one that mixes engines, and let the local loop's existing, correct
    per-step routing (which does call `run_prefect_step` for individual
    `executes_at="prefect"` steps) handle it one step at a time instead.
    """
    return all(
        getattr(step, "executes_at", "resource-explorer") in ("resource-explorer", "prefect")
        for step in survey_def.steps
    )


def _prefect_orchestration_enabled(engine_override: str | None = None) -> bool:
    """Whether Prefect should sequence a whole Survey Definition.

    Reads the same `prefect.enabled` switch the per-step dispatch already
    honours, so there is one answer to "is Prefect available here" rather than
    two that can disagree — except when `engine_override` names a choice for
    THIS run, which takes precedence over the global config either way:
    "resource-explorer" always answers False (whole-definition orchestration
    is skipped, so the local loop's own per-step `_use_prefect` — which also
    honours the same override — is what actually runs each step); "prefect"
    always answers True (if no server is actually reachable,
    `_run_via_prefect`'s existing try/except already falls back to the local
    loop, same as it does today when `prefect.enabled` is True but Prefect is
    down).
    """
    if engine_override == "resource-explorer":
        return False
    if engine_override == "prefect":
        return True
    try:
        from resource_explorer.config import get_config

        return bool(get_config().prefect.enabled)
    except Exception as exc:  # pragma: no cover - config guard
        log.debug("could not read prefect config, assuming disabled: %s", exc)
        return False
