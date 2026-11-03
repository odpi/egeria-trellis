"""Generates Dr.Egeria markdown that publishes RE's local analysis steps as
real Egeria `GovernanceActionProcessStep`/`GovernanceActionProcess` elements,
and chains them into a runnable Survey Definition.

Closes design-doc question A12 (`docs/egeria-collaboration-and-survey-model.md`
§6.1) and the tracked `docs/Backlog.md` item: "publishing RE's own
sub-surveyors as catalogable GovernanceActionType elements — nothing does
this today." See `docs/analysis-step-egeria-registration-plan.md` for the
full plan (D1-D3).

Deliberately generates markdown for the existing, proven "Action Author"
Dr.Egeria command family (`Create Governance Action Process Step`,
`Create Governance Action Process`, `Link First/Next Process Step`) — no new
Egeria types or Dr.Egeria commands needed (confirmed: §6.1's own finding,
and this is the exact recipe already used live for the original "Repo Coarse
Scout" process). Producing markdown to be executed through the same
mcp__egeria tooling already proven this session, rather than reimplementing
pyegeria's GovernanceOfficer.create_governance_definition() body-construction
natively in RE — that generic body shape has real footguns (see
egeria-python CLAUDE.md's "Creating a type with no dedicated OMVS wrapper"
note) not worth re-deriving here for what is fundamentally a one-time/
per-addition publishing action, not a hot path.

Resource-type-agnostic by design: everything here operates on a plain list
of PublishableStep descriptors, not on repo_survey_definition_adapter.py's
STEP_REGISTRY/StepInfo shape specifically — a database or filesystem adapter
(neither of which has repo's StepInfo-dataclass registry today) can build
its own PublishableStep list and reuse every function in this module
unchanged. See scripts/generate_repo_survey_definition.py for the one
resource-type-specific piece (extracting steps from repo's STEP_REGISTRY).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PublishableStep:
    """One analysis step to publish as a GovernanceActionProcessStep.

    step_key doubles as the `re_analysis_step` Additional Properties value —
    RE's own convention (documented in egeria-collaboration-and-survey-model.md
    §6.2) for "which local RE sub-surveyor a step maps to" is that the two
    are the same identifier, so a Survey Definition reader never needs a
    separate lookup table to go from Egeria element back to RE dispatch key.
    """
    step_key: str
    description: str
    technology_type: str
    # "resource-explorer" | "prefect" | "egeria" — see survey_definition_executor.py's
    # _use_prefect() and PrefectConfig.route_local_steps' docstring in config.py.
    # Defaults to plain local execution; set "prefect" only for steps genuinely
    # worth the per-step Prefect overhead in exchange for real flow-run
    # observability/cancellation — long-running or thrash-prone ones, not a
    # blanket switch. See docs/re-ea-consolidation-audit.md and the Prefect
    # entry in docs/Backlog.md for which steps and why.
    executes_at: str = "resource-explorer"
    #: The step's authored options, as the request parameters its GovernanceActionExecutor carries (engine host
    #: case 3, decision 2). (name, value) pairs; none are authored today, so the executor carries none.
    request_parameters: tuple = ()

    @property
    def runs_on_re_engine(self) -> bool:
        """Engine host case 3: a step RE runs is dispatched by Egeria to RE's engine (its executor). A step routed to
        Prefect or Egeria's own engines gets no executor here (design §4.1: `executes_at` and the executor agree)."""
        return self.executes_at == "resource-explorer"


def _step_qualified_name(survey_group: str, step_key: str) -> str:
    return f"GovActionProcessStep::{survey_group}::{step_key}"


def _process_qualified_name(survey_group: str) -> str:
    return f"GovActionProcess::{survey_group}"


def _step_display_name(survey_display_name: str, step_key: str) -> str:
    readable = step_key.replace("_", " ").title()
    return f"{survey_display_name} — {readable}"


def render_step_block(survey_group: str, survey_display_name: str, step: PublishableStep) -> str:
    """One "Create Governance Action Process Step" command block. Additional
    Properties follow the documented executes_at/supported_technology_type/
    re_analysis_step convention (§6.2) — executes_at defaults to
    "resource-explorer" (every PublishableStep this module handles is, by
    definition, an RE-local sub-surveyor) but is per-step overridable to
    "prefect" for steps a caller has deliberately opted into distributed
    execution for; see PublishableStep.executes_at."""
    qn = _step_qualified_name(survey_group, step.step_key)
    return (
        "## Create Governance Action Process Step\n"
        f"### Display Name\n{_step_display_name(survey_display_name, step.step_key)}\n\n"
        f"### Qualified Name\n{qn}\n\n"
        f"### Description\n{step.description}\n\n"
        + _produced_guards_section(step)
        # Engine host case 3 (H1): `NextGovernanceActionProcessStep` is MULTI_LINK and Dr.Egeria's Link Next only
        # adds, so a republish can leave two identical links into this step. With Egeria coordinating, each one
        # would start this step again (EngineActionHandler.initiateNextEngineActions, one engine action per matching
        # link). `ignoreMultipleTriggers` makes Egeria start it once per process run, whatever the duplicates.
        + "### Ignore Multiple Triggers\ntrue\n\n"
        + "### Additional Properties\n"
        "| Parameter Name | Parameter Value |\n"
        "|---|---|\n"
        f"| executes_at | {step.executes_at} |\n"
        f"| supported_technology_type | {step.technology_type} |\n"
        f"| re_analysis_step | {step.step_key} |\n"
    )


def _produced_guards_section(step: PublishableStep) -> str:
    """`### Produced Guards` for a step RE's engine runs: what it can complete with when Egeria coordinates the
    definition (`step_outcome.produced_guards`, the same mapping the engine host completes with). None otherwise."""
    if not step.runs_on_re_engine:
        return ""
    from resource_explorer.step_outcome import produced_guards

    return f"### Produced Guards\n{', '.join(produced_guards(step.step_key))}\n\n"


def render_executor_block(survey_group: str, step: PublishableStep) -> str:
    """The step's "Link Action to Action Executor" (engine host case 3, decision 2): Egeria dispatches this process
    step to Resource Explorer's engine under the request type of its step key, with the step's request parameters.
    Part of the published definition, never a manual link. NOT safe to repeat in Egeria (Dr.Egeria's Link commands
    are not idempotent); the reconciler reports a second executor as drift."""
    from resource_explorer.engine_host import ENGINE_QUALIFIED_NAME, request_type_for_step

    qn = _step_qualified_name(survey_group, step.step_key)
    params = ""
    if step.request_parameters:
        rows = "".join(f"| {k} | {v} |\n" for k, v in step.request_parameters)
        params = f"### Request Parameters\n| Parameter Name | Parameter Value |\n|---|---|\n{rows}\n"
    return (
        "## Link Action to Action Executor\n"
        f"### Governance Action Type\n{qn}\n\n"
        f"### Governance Engine\n{ENGINE_QUALIFIED_NAME}\n\n"
        f"### Request Type\n{request_type_for_step(step.step_key)}\n\n"
        f"{params}"
        f"### Description\nEgeria dispatches {qn} to Resource Explorer's engine, which claims it and runs RE step "
        f"{step.step_key}.\n"
    )


def render_process_block(
    survey_group: str, survey_display_name: str, technology_type: str, description: str,
    survey_kind: str | None = None,
) -> str:
    """The "Create Governance Action Process" command block — the survey
    container all steps get linked into.

    survey_kind (Additional Properties, generic-dictionary convention — same
    mechanism as executes_at/supported_technology_type, no schema change)
    distinguishes which UI surface a Survey Definition is meant for, e.g.
    "scouting" | "discovery" | "automate_full" — see
    docs/discovery-automate-project-context-plan.md Part 1. Without this,
    every Survey Definition matching a Technology Type shows up in every
    surface's candidate list regardless of which phase it was actually
    authored for (the bug this field exists to close). Optional/omitted for
    backward compatibility with Survey Definitions authored before this
    convention existed."""
    lines = [
        "## Create Governance Action Process\n"
        f"### Display Name\n{survey_display_name}\n\n"
        f"### Qualified Name\n{_process_qualified_name(survey_group)}\n\n"
        f"### Description\n{description}\n\n"
        "### Additional Properties\n"
        "| Parameter Name | Parameter Value |\n"
        "|---|---|\n"
        f"| supported_technology_type | {technology_type} |\n"
    ]
    if survey_kind:
        lines.append(f"| survey_kind | {survey_kind} |\n")
    return "".join(lines)


def render_link_first_block(survey_group: str, first_step_key: str) -> str:
    return (
        "## Link First Process Step\n"
        f"### Governance Action Process\n{_process_qualified_name(survey_group)}\n\n"
        f"### Governance Action Process Step\n{_step_qualified_name(survey_group, first_step_key)}\n"
    )


def render_scope_link_block(target_element: str, scope_reference: str) -> str:
    """One "Link Element To Scope" command block, creating a ScopedBy
    relationship — same mechanism docs/dr-egeria/foundations.md already
    uses for Question -> Perspective/Funnel-Stage links (compact command
    spec: `commands_curation_compact.json`'s "Scoped By Link Base" bundle
    needs only Target Element + Scope Reference; no Scope Category here —
    that field only distinguishes the asked-at/answered-at duality
    Question->Stage links need, which doesn't apply to a Survey Definition
    answering a Question). Reused for docs/survey-question-context-plan.md's
    D1 (Survey Definition -> Question ScopedBy links) — target_element is
    the Survey Definition's display name, scope_reference is the Question's
    display name (both resolved by name, matching every other Link command
    in this file's sibling docs)."""
    return (
        "## Link Element To Scope\n"
        f"### Target Element\n{target_element}\n\n"
        f"### Scope Reference\n{scope_reference}\n"
    )


def render_link_next_block(survey_group: str, prev_step_key: str, next_step_key: str, guard: str = "Any") -> str:
    """Unconditional linear chaining (guard="Any") — matches the proven,
    live-verified convention in docs/egeria-database-survey-definition.md.
    Guard-based branching between steps (a real Egeria mechanism, confirmed
    in §6.4) is deliberately not used here — v1 keeps every Survey
    Definition this module generates a plain linear sequence, per the
    design doc's A9 decision ("keep it simple initially")."""
    return (
        "## Link Next Process Step\n"
        f"### Governance Action Process Step\n{_step_qualified_name(survey_group, prev_step_key)}\n\n"
        f"### Next Governance Action Process Step\n{_step_qualified_name(survey_group, next_step_key)}\n\n"
        f"### Guard\n{guard}\n"
    )


def generate_survey_definition_markdown(
    survey_group: str,
    survey_display_name: str,
    technology_type: str,
    description: str,
    steps: list[PublishableStep],
    survey_kind: str | None = None,
    answers_questions: list[str] | None = None,
) -> str:
    """Full Dr.Egeria markdown document: one 'Create Governance Action
    Process Step' per step (in the given order), one 'Create Governance
    Action Process', 'Link First Process Step', then 'Link Next Process
    Step' for every consecutive pair, then (D1,
    docs/survey-question-context-plan.md) one 'Link Element To Scope' per
    entry in answers_questions — the ScopedBy link from this Survey
    Definition to each Question (by display name) it answers, run after the
    process itself has been created since the link needs both elements to
    already exist as resolvable references. Command order in the file
    matches the block ordering `dr_egeria`'s dispatcher expects (steps
    created before they're referenced by the process/links) — the exact
    structure proven live in docs/egeria-database-survey-definition.md,
    generalized.

    Raises ValueError on an empty steps list — a Survey Definition with no
    steps isn't a meaningful thing to author."""
    if not steps:
        raise ValueError("generate_survey_definition_markdown() needs at least one step")

    blocks = [render_step_block(survey_group, survey_display_name, s) for s in steps]
    blocks.append(render_process_block(survey_group, survey_display_name, technology_type, description, survey_kind))
    blocks.append(render_link_first_block(survey_group, steps[0].step_key))
    for prev, nxt in zip(steps, steps[1:]):
        blocks.append(render_link_next_block(survey_group, prev.step_key, nxt.step_key))
    # Engine host case 3, decision 2: every step RE runs carries its executor to RE's engine, so Egeria can run the
    # definition. Needs RE's engine and its request types declared first (engine-host/steps/).
    for step in steps:
        if step.runs_on_re_engine:
            blocks.append(render_executor_block(survey_group, step))
    for question in answers_questions or []:
        blocks.append(render_scope_link_block(survey_display_name, question))

    return "\n___\n\n".join(blocks) + "\n"
