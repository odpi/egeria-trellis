"""A step's prerequisites run themselves, within the budget the user chose.

Design §17.1. **Decision (project owner, 2026-09-23):** prerequisite surveys or
steps are executed automatically as needed, telling the user.

What happened before: `step_preconditions` checked whether the stored data a
step reads exists, and a step whose input was missing was **skipped with a
named reason**. Honest, and the wrong outcome for someone who asked the
question — they got "skipped: no parsed dependencies" when the answer was one
cheap step away.

This module turns that check into a plan. Three things make it safe, and they
are the design:

**1. The budget is the consent.** A prerequisite whose declared costs sit
inside the tier the caller is already operating at runs unasked; one that
crosses into a more expensive tier, needs a download or clone, needs
credentials the executor cannot resolve (rule B), or is answered by a human
becomes a **proposal** the user confirms. A user who picked Scouting never
triggers an Analysis-tier fetch by accident, and a user who picked Analysis is
not nagged about a 200 ms catalog read.

**How "the tier the caller is operating at" is read**, since the design names
the principle and not the mechanism. Two sources, in order:

  a. an explicit ceiling the run already carries — `SurveyOrchestrator.run`'s
     `max_fetch_cost`/`max_compute_cost`, the existing "compose a survey by
     budget" surface. When a caller has said what it will pay, that IS the
     budget and there is nothing to infer.
  b. otherwise, **the demanding step's own declared `fetch_cost`/
     `compute_cost`**. The user asked for that step; its tier is the tier they
     chose. This is the reading that makes §17.1's two examples come out
     right: `postgres_column_profile` (api_heavy/medium) absorbs
     `postgres_schema_and_stats` (api/low) silently, and `db_derived`
     (none/low — the zero-fetch step) cannot silently open a connection to
     fill its own input.

Deliberately NOT read from the analysis catalog's intent/stage. A step can sit
in several survey definitions at different stages; its declared cost is a
property of the step, which is what a ceiling has always been compared against
(`SurveyOrchestrator.run`'s own D5 filter uses exactly these two fields).

**2. A producer map, because preconditions name data, not steps.** See
`step_produces`. The walk is precondition → producing step → *its* own
preconditions, recursively, with:

  * a **cycle guard** — `PrerequisiteCycleError`, raised, never a silent
    truncation. A step that transitively requires itself is a declaration bug
    and the only useful response is to say so;
  * **stop rule 1** — a producer that already ran *on this snapshot* and found
    nothing is not re-run. Its `nothing_found` is the answer. Snapshot-scoped,
    not a freshness window: the question is "has this run already established
    this", and a TTL would answer a different one (and would re-run a step
    every time the clock moved, which is the loop this rule exists to break);
  * **stop rule 2** — if anything anywhere in the chain needs consent, the
    WHOLE chain collapses into one proposal naming every step and the summed
    cost. Never a partial auto-run followed by a prompt: that would spend the
    user's time and then ask permission for the rest, which is the worst
    ordering available.

**A second axis, in the same gate (2026-09-24).** `REPLY-DATABASE-CREDENTIAL-
CAPABILITY-VISIBILITY.md` §7.1, which the project owner approved building:
"Build it as one axis beside cost tier in the same gate, not as a separate
flow: a step declares `fetch_cost`, `compute_cost` and `requires_capability`,
and the launcher shows one combined reason."

So `requires_capability` is checked HERE, by the same `resolve()`, producing
the same `Proposal` with an extra `ConsentReason(kind="capability")` — not a
second resolver and not a second prompt. When both axes fire, the two reasons
land in ONE proposal and `Proposal.sentence()` joins them into one sentence;
a reader is never handed two gates to reconcile.

Two things about the capability axis are genuinely different from cost, and
both are deliberate:

  * it applies to the **demanding step itself**, not only to producers. The
    budget reading above says a step the user asked for by name is inside its
    own budget by definition — true of cost, false of capability. Asking for
    a step does not widen a grant;
  * the measurement comes from a **probe that already ran**
    (`credential_capability.stored_probe`), never from a fresh connection.
    "Never probed" is a third answer and does not block — see that module.

§7.1 names three choices at the gate: run partially and say so; pick another
visible connection; raise the RFA. The first is `Proposal.run_partially`, the
third is `credential_capability.capability_rfa`. **The second is deliberately
not built here** — it needs the multi-connection model of §1/§2, which is
separately gated on the project owner's ruling (`docs/Backlog.md`) and has no
registry table, no enumeration call and no UI yet. Offering a choice that
cannot be taken would be worse than offering two that can.

**3. An auto-run is a result, not an omission.** The caller writes the
annotation, the activity-log entry and the `step_runs` attribution — see
`auto_run_annotation()` and `Resolution.demanded_by`. This module decides; it
does not execute, so it can be tested without a survey and serialised for a
remote executor (rule E: `Resolution.as_plan()`).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping

from resource_explorer.surveyors import step_preconditions

log = logging.getLogger(__name__)

#: Ordinal cost scales. Imported lazily from the repo adapter at first use for
#: the same reason `survey_orchestrator._cost_orders` does it — that module
#: imports this family and a top-level import would close the cycle.
def _cost_orders() -> tuple[list[str], list[str]]:
    from resource_explorer.surveyors.repo_survey_definition_adapter import (
        COMPUTE_COST_ORDER, FETCH_COST_ORDER,
    )
    return FETCH_COST_ORDER, COMPUTE_COST_ORDER


#: Rough wall-clock estimates per (fetch, compute) tier, in seconds, used ONLY
#: when `step_runs` holds no measurement for a step yet. Coarse on purpose:
#: a proposal has to put a number in front of a person before the first run of
#: a step exists to measure, and a wrong order of magnitude is worse than an
#: admitted estimate — which is why `Proposal.estimated_is_measured` says
#: which kind of number this is rather than letting the two render alike.
_TIER_SECONDS = {
    ("none", "low"): 1.0, ("none", "medium"): 10.0, ("none", "high"): 60.0,
    ("api", "low"): 3.0, ("api", "medium"): 15.0, ("api", "high"): 90.0,
    ("api_heavy", "low"): 20.0, ("api_heavy", "medium"): 40.0, ("api_heavy", "high"): 120.0,
    ("download", "low"): 30.0, ("download", "medium"): 60.0, ("download", "high"): 180.0,
}

SATISFIED = "satisfied"
AUTO_RUN = "auto_run"
PROPOSAL = "proposal"
UNSATISFIABLE = "unsatisfiable"


class PrerequisiteCycleError(RuntimeError):
    """A step transitively requires its own output.

    Raised, not logged-and-truncated. A cycle here is always a bug in how
    `produces`/`requires_context` are declared — there is no resource state
    that can produce one — and a resolver that quietly stopped walking would
    turn a declaration bug into an intermittently-skipped step nobody could
    account for.
    """


@dataclass(frozen=True)
class ConsentReason:
    """Why one step in a chain cannot run unasked."""
    step_key: str
    #: `tier` / `download` / `credentials` / `human` / `capability`
    #:
    #: `capability` is the second axis (REPLY-DATABASE-CREDENTIAL-CAPABILITY-
    #: VISIBILITY.md §7.1), and it is a `ConsentReason` like the other four
    #: rather than a parallel mechanism ON PURPOSE — §7.1's words are "one
    #: axis beside cost tier in the same gate, not a separate flow… the
    #: launcher shows one combined reason". A step short on both cost tier
    #: and capability therefore produces two reasons inside ONE `Proposal`,
    #: which renders as one sentence with two clauses, not two prompts the
    #: reader has to reconcile.
    #:
    #: It differs from the other four in one way worth knowing: those are all
    #: properties of a PRODUCER the resolver would auto-run, so they only ever
    #: appear for steps in `Proposal.steps`. A capability shortfall can also
    #: be a property of the DEMANDING step itself, which no amount of "the
    #: user asked for it" fixes — asking for a step does not widen a grant.
    kind: str
    detail: str


@dataclass
class Proposal:
    """One combined ask covering an entire chain (stop rule 2)."""
    #: Every step that would run, in the order it would run.
    steps: list[str] = field(default_factory=list)
    #: Why consent is needed, per step that needs it.
    reasons: list[ConsentReason] = field(default_factory=list)
    #: The chain's cost, on the same two ordinal axes a step declares.
    fetch_cost: str = "none"
    compute_cost: str = "low"
    estimated_seconds: float = 0.0
    #: True when every step in `steps` had a real prior measurement in
    #: `step_runs`. A mixed or absent chain says so rather than presenting a
    #: tier default as an observation.
    estimated_is_measured: bool = False
    #: What the demanding step is, and which precondition(s) started this.
    demanding_step: str = ""
    preconditions: list[str] = field(default_factory=list)
    #: The demanding step, repeated here ONLY when a capability reason
    #: applies to it — §7.1's first choice, "run partially and say so".
    #:
    #: It is a separate field rather than an entry in `steps` because the two
    #: mean different things to the accept path: `steps` are PRODUCERS to run
    #: BEFORE the demanding step, and the client posts them to
    #: `/api/prerequisites/run` verbatim. A capability-only proposal has no
    #: producers at all — nothing needs running first; the question is whether
    #: to run the demanding step itself knowing its answer will be bounded by
    #: the credential. Folding it into `steps` would make the existing UI say
    #: "answering this needs `postgres_column_profile` first" about the step
    #: the reader just asked for.
    #:
    #: Empty when the proposal is purely about cost tier, which leaves every
    #: pre-existing proposal byte-identical.
    run_partially: str = ""
    #: The measured shortfall behind `run_partially`, as
    #: `credential_capability.CapabilityAssessment.as_dict()`. Carried so the
    #: launcher can render the fraction ("SELECT on 3 of 26") and offer the
    #: RFA without re-deriving either.
    capability: dict | None = None

    @property
    def advisory(self) -> bool:
        """Every reason here is a capability one, so nothing is being SPENT.

        **The judgement call that makes this axis safe to add to an existing
        gate, and the one place the two axes deliberately behave
        differently.**

        A cost-tier proposal asks permission to spend something the user has
        not agreed to spend — a download, a clone, an hour of CPU. Declining
        costs them nothing; proceeding unasked is the harm, so the safe
        default is to stop.

        A capability proposal spends nothing. It says the answer will be
        bounded by what the credential can reach. Here the safe default is
        the opposite: declining costs the user their answer entirely, and
        stopping would mean a database whose credential lacks `pg_monitor`
        gets NO survey at all — strictly worse than today's under-report,
        and worse still on every path with nobody there to ask (the
        scheduler, the CLI, a Survey Definition run), which would silently
        produce nothing where they used to produce something.

        So a capability-only proposal is ADVISORY: `Resolution.may_run` stays
        true, the step runs, and the run says so — `MEASURED_WITHIN_
        CREDENTIAL_SCOPE` on the envelope (#253) and the proposal itself
        attached to the step's report entry. That is §7.1's own first choice,
        "run partially and say so", taken as the default rather than offered
        only to someone watching.

        The launcher still asks, because it asks FIRST: `/next` calls
        `/api/prerequisites/plan` before dispatching and stops on
        `status == "proposal"` whatever `may_run` says. So an interactive
        user is shown the combined reason and chooses; an unattended run
        proceeds and is labelled. Neither is silent.

        False as soon as ANY non-capability reason joins — a proposal
        carrying both axes blocks on the cost half, as it did before.
        """
        kinds = {r.kind for r in self.reasons}
        return bool(kinds) and kinds == {"capability"}

    @property
    def combines_cost_and_capability(self) -> bool:
        """Both axes fired for this one proposal.

        Not used to change behaviour — both kinds already live in `reasons`
        and render together. It exists so a test can pin §7.1's "one combined
        reason" as a property of the object rather than by string-matching the
        sentence, and so a reader can see that the combination is a designed
        state and not an accident of two features overlapping.
        """
        kinds = {r.kind for r in self.reasons}
        return "capability" in kinds and bool(kinds - {"capability"})

    def sentence(self) -> str:
        """The §17.1 prompt, as one line: what is needed and what it costs.

        ONE sentence however many axes fired. A cost-tier shortfall and a
        capability shortfall are two clauses of the same `why`, joined like
        any other two reasons — §7.1's "the launcher shows one combined
        reason", made literal here rather than left to the renderer, so the
        API, the CLI and the UI cannot drift into three different phrasings
        of the same gate.

        Ends with a full stop, not the decision. `REPLY-COPY-REVIEW-
        CREDENTIAL-AND-FIT-LANGUAGE.md` §1 defect 3: the part a reader has to
        act on was the last seven words of a 60-80 word sentence. This stays
        the single source of the full explanation — that reasoning doesn't
        change — and `question()` alongside it carries the short, actionable
        ask a button or a CLI prompt puts in front of someone. One source
        still; nothing can drift between the two.
        """
        why = "; ".join(r.detail for r in self.reasons) or "it crosses the tier you chose"
        if not self.steps:
            # Capability-only: nothing runs first, so there is no chain to
            # name and no estimate to quote. Saying "answering this needs
            # first — estimated 0s" would be three claims that are all false.
            return f"`{self.demanding_step}` can run, but not completely — {why}."
        steps = ", ".join(f"`{s}`" for s in self.steps)
        return (
            f"answering this needs {steps} first — estimated "
            f"{self.estimated_seconds:.0f}s"
            f"{'' if self.estimated_is_measured else ' (not yet measured — estimated from declared tiers)'}"
            f", {why}."
        )

    def question(self) -> str:
        """The short, actionable question `sentence()` no longer ends with.

        `sentence()` stays the single source of the full explanation; this is
        the other half of REPLY-COPY-REVIEW-CREDENTIAL-AND-FIT-LANGUAGE.md
        §1 defect 3 — the UI puts this on the confirm button, the CLI prints
        it after `sentence()`.
        """
        if self.run_partially:
            return "Run it within this credential's scope?"
        return "Run it?"

    def as_dict(self) -> dict:
        return {
            "steps": list(self.steps),
            "reasons": [{"step": r.step_key, "kind": r.kind, "detail": r.detail}
                        for r in self.reasons],
            "fetch_cost": self.fetch_cost,
            "compute_cost": self.compute_cost,
            "estimated_seconds": round(self.estimated_seconds, 1),
            "estimated_is_measured": self.estimated_is_measured,
            "demanding_step": self.demanding_step,
            "preconditions": list(self.preconditions),
            "run_partially": self.run_partially,
            "capability": dict(self.capability) if self.capability else None,
            "advisory": self.advisory,
            "sentence": self.sentence(),
            "question": self.question(),
        }


@dataclass
class Resolution:
    """What to do about one step's preconditions."""
    step_key: str
    status: str = SATISFIED
    #: Producers to run first, in dependency order. Only ever populated for
    #: `AUTO_RUN` — stop rule 2 means a chain is never half-run.
    auto_run: list[str] = field(default_factory=list)
    proposal: Proposal | None = None
    #: Human-readable, for the skip annotation when the step cannot proceed.
    reason: str = ""
    #: Preconditions that are unmet and cannot be satisfied by running
    #: anything — no declared producer, or a producer that already looked and
    #: found nothing on this snapshot.
    dead_ends: list[str] = field(default_factory=list)

    @property
    def may_run(self) -> bool:
        """Whether the demanding step itself should be dispatched now.

        True for SATISFIED and for AUTO_RUN (after the producers have run).
        False for UNSATISFIABLE, and for a PROPOSAL that asks to spend
        something — the step is skipped with a reason, exactly as it was
        before §17.1.

        **One exception, added with the capability axis (2026-09-24): an
        ADVISORY proposal still runs.** A proposal whose every reason is a
        capability shortfall is not asking to spend anything; it is saying
        the answer will be bounded. Blocking on it would leave a narrow
        credential with no survey at all, which is worse than the bounded
        answer it is warning about. See `Proposal.advisory` for the full
        reasoning and for why the interactive launcher still asks.
        """
        if self.status == PROPOSAL and self.proposal is not None:
            return self.proposal.advisory
        return self.status in (SATISFIED, AUTO_RUN)

    def as_plan(self) -> dict:
        """Rule E: the resolver's output as a serialised plan.

        Rule E's remote executors do not exist yet (`prefect_adapter.py` is the
        seam, Airflow is "later"), so this builds nothing for them — it only
        makes sure the output IS serialisable, which is the constraint rule E
        places on the step contract *now*. No live consumer today; the classic
        UI reads `proposal.as_dict()` directly.
        """
        return {
            "step_key": self.step_key,
            "status": self.status,
            "auto_run": list(self.auto_run),
            "proposal": self.proposal.as_dict() if self.proposal else None,
            "reason": self.reason,
            "dead_ends": list(self.dead_ends),
        }


@dataclass(frozen=True)
class Budget:
    """The tier the caller is already operating at. See the module docstring
    for how it is read."""
    fetch_cost: str = "none"
    compute_cost: str = "low"
    #: Which of the two readings produced it, carried so a proposal can say
    #: "the ceiling you set" rather than "the step you asked for" and be right.
    source: str = "step"

    @classmethod
    def for_step(cls, info, max_fetch_cost: str | None = None,
                 max_compute_cost: str | None = None) -> "Budget":
        if max_fetch_cost is not None or max_compute_cost is not None:
            return cls(
                max_fetch_cost or getattr(info, "fetch_cost", "none") or "none",
                max_compute_cost or getattr(info, "compute_cost", "low") or "low",
                source="ceiling",
            )
        return cls(
            getattr(info, "fetch_cost", "none") or "none",
            getattr(info, "compute_cost", "low") or "low",
            source="step",
        )


def _exceeds(budget: Budget, info) -> str:
    """"" when `info` fits inside `budget`, else which axis it crosses, in
    plain language.

    No field names and no Python `repr` quotes — this lands verbatim in a
    sentence a person reads, not a log line (REPLY-COPY-REVIEW-CREDENTIAL-
    AND-FIT-LANGUAGE.md §1 defect 4).
    """
    fetch_order, compute_order = _cost_orders()
    fetch = getattr(info, "fetch_cost", "none") or "none"
    compute = getattr(info, "compute_cost", "low") or "low"
    where = "the ceiling this run set" if budget.source == "ceiling" else "the step you asked for"
    try:
        if fetch_order.index(fetch) > fetch_order.index(budget.fetch_cost):
            return f"needs a more expensive fetch than {where} ({fetch}; you're at {budget.fetch_cost})"
        if compute_order.index(compute) > compute_order.index(budget.compute_cost):
            return f"needs more compute than {where} ({compute}; you're at {budget.compute_cost})"
    except ValueError:
        # An unrecognised tier string cannot be ordered. Ask rather than
        # assume cheap — the failure direction that spends nothing.
        return f"declares a cost tier this run doesn't recognise ({fetch}/{compute})"
    return ""


def _consent_reasons(step_key: str, info, budget: Budget,
                     resolved_resources: set[str] | None) -> list[ConsentReason]:
    """Every reason this one producer cannot run unasked. §17.1's four."""
    out: list[ConsentReason] = []
    crossed = _exceeds(budget, info)
    if crossed:
        out.append(ConsentReason(step_key, "tier", f"`{step_key}` {crossed}"))
    # A download/clone is called out separately from the tier even though
    # `fetch_cost="download"` usually crosses it too: the design names it as
    # its own condition, and the sentence a user reads should say "one
    # download", not only "a more expensive tier".
    needs = dict(getattr(info, "requires_resources", None) or {})
    unresolved = {r for r in needs if not (resolved_resources or set()) >= {r}}
    if unresolved:
        out.append(ConsentReason(
            step_key, "download",
            f"`{step_key}` needs {', '.join(sorted(unresolved))} — a download or clone "
            "this run has not already acquired"))
    if getattr(info, "needs_credentials", False):
        # Provenance ("rule B", the module docstring's naming for this
        # condition) belongs to the maintainer reading this code, not to the
        # sentence a user reads — REPLY-COPY-REVIEW-CREDENTIAL-AND-FIT-
        # LANGUAGE.md §1 defect 1 / §0's "provenance belongs in the evidence,
        # not in the sentence".
        out.append(ConsentReason(
            step_key, "credentials",
            f"`{step_key}` needs credentials this executor cannot resolve"))
    if getattr(info, "answering_kind", "") == "human":
        out.append(ConsentReason(
            step_key, "human",
            f"`{step_key}` is answered by a person, not by a run"))
    return out


def _declares_capability(step_registry: Mapping[str, Any], keys) -> bool:
    """Whether any of `keys` declares a `requires_capability` at all.

    The guard that keeps this axis free for every resource type that has no
    credential model — repositories and filesystems declare none, so the probe
    is never read and not one registry call is made on their path.
    """
    for key in keys:
        info = step_registry.get(key)
        if info is not None and (getattr(info, "requires_capability", "") or ""):
            return True
    return False


def _capability_reason(step_key: str, info, probe, consented: bool = False,
                       demanding: bool = False) -> tuple:
    """`(ConsentReason | None, CapabilityAssessment)` for one step.

    The whole of the capability axis's per-step logic, kept beside
    `_consent_reasons` (the cost axis's) because the two are one gate and a
    reader looking for "why can this step not just run" should find both in
    the same place.

    `consented` still returns the ASSESSMENT — only the reason is dropped. The
    shortfall is a fact about the credential that a consented run does not
    change, and the run's own envelope still has to say so
    (`result_status.MEASURED_WITHIN_CREDENTIAL_SCOPE`). Consent is permission
    to proceed, never permission to stop mentioning it.

    `demanding` is true only when `step_key` is `Proposal.demanding_step` AND
    the resulting proposal has no producers to run first (`Proposal.steps`
    empty) — the capability-only template in `Proposal.sentence()` already
    opens with that same step name, so repeating it in the detail said the
    subject twice (REPLY-COPY-REVIEW-CREDENTIAL-AND-FIT-LANGUAGE.md §1 defect
    2). When producers ARE involved, the combined sentence never otherwise
    names the demanding step, so the prefix stays — only the capability-only
    case is redundant.
    """
    from resource_explorer.surveyors import credential_capability

    requirement = getattr(info, "requires_capability", "") or ""
    assessment = credential_capability.assess(requirement, probe)
    if consented or not assessment.blocks:
        return None, assessment
    detail = assessment.detail if demanding else f"`{step_key}` {assessment.detail}"
    return ConsentReason(step_key, "capability", detail), assessment


def capability_reason_detail(step_key: str, info, probe) -> str:
    """The launcher's own reason for `step_key`'s capability shortfall against the stored `probe` (the
    `ConsentReason.detail` the launcher puts in its proposal, "`<step>` needs `<tier>`: connected as ..."), or ""
    when there is none (the step declares no capability, nobody has probed, or the credential has it).

    The reason only, never `Proposal.sentence()`: that sentence offers to "run, but not completely", and RE's engine
    host (case 3, decision 4), which has nobody to ask, does not run such a step at all; it completes it
    `survey-invalid` with this reason instead of the advisory partial run an unattended RE run takes."""
    reason, _assessment = _capability_reason(step_key, info, probe)
    return reason.detail if reason is not None else ""


def _estimate(registry, slug: str, step_keys: list[str],
              registries: Mapping[str, Any]) -> tuple[float, bool]:
    """(seconds, every_step_was_measured) for a chain."""
    total = 0.0
    all_measured = True
    for key in step_keys:
        measured = None
        try:
            measured = registry.median_step_wall_ms(key)
        except Exception as exc:  # pragma: no cover - registry guard
            # Not silent: an unreadable history is the same as no history for
            # the purpose this serves, and `estimated_is_measured` is what
            # says so to the person reading the number.
            all_measured = False
            log.debug("no step_runs history for %s: %s", key, exc)
        if measured:
            total += measured / 1000.0
            continue
        all_measured = False
        info = registries.get(key)
        total += _TIER_SECONDS.get(
            (getattr(info, "fetch_cost", "none") or "none",
             getattr(info, "compute_cost", "low") or "low"), 10.0)
    return total, all_measured and bool(step_keys)


def _found_nothing_on_this_snapshot(registry, slug: str, step_key: str,
                                    surveyed_at: str | None) -> bool:
    """Stop rule 1. Did this producer already run, in THIS snapshot, and find
    nothing?

    Read from `step_runs` (§17.2), which is the one place a step's yield is
    recorded per run. `surveyed_at` is the snapshot key — a survey stamps one
    timestamp across every step it dispatches (`SurveyOrchestrator.run`'s
    `surveyed_at`, `SurveyDefinitionExecutor._execute`'s own), so "this
    snapshot" is a real, already-existing scope and not a window invented
    here.

    False whenever the question cannot be answered — no snapshot key, no
    table, no row. Re-running a cheap producer one extra time costs a
    measurable amount; NOT running a step because an unreadable table looked
    like a `nothing_found` costs an answer, and this codebase's standing rule
    on that direction is `step_preconditions._needs_rows`'s: on our own
    failure, run the step.
    """
    if not surveyed_at:
        return False
    try:
        rows = registry.query_step_runs(slug=slug, step_key=step_key,
                                        surveyed_at=surveyed_at)
    except Exception as exc:
        log.debug("cannot read step_runs for %s/%s: %s", slug, step_key, exc)
        return False
    for row in rows or ():
        metrics = row.get("metrics") or {}
        if metrics.get("annotations") == 0:
            return True
        if "no_signal" in (metrics.get("outcomes") or ()):
            return True
    return False


def resolve(
    registry,
    entity,
    step_key: str,
    step_registry: Mapping[str, Any] | None,
    *,
    surveyed_at: str | None = None,
    max_fetch_cost: str | None = None,
    max_compute_cost: str | None = None,
    resolved_resources: set[str] | None = None,
    already_ran: set[str] | None = None,
    capability_probe: dict | None = None,
    capability_consented: bool = False,
) -> Resolution:
    """What to do about `step_key`'s preconditions. Runs nothing.

    `step_registry` None (a resource type that has not declared one) resolves
    to SATISFIED with no checks — see `ResourceTypeAdapter.step_registry`.

    `resolved_resources` names the D6 shared resources this run has ALREADY
    acquired, so a producer needing a zipball the run already downloaded is
    not proposed as "needs a download". Empty/None is the conservative
    reading and the one every caller outside `SurveyOrchestrator.run` uses.

    `already_ran` names producers this run has run itself (the resolver's
    caller records them), so a second demanding step does not re-run a
    producer the first one just triggered.

    `capability_probe` is the stored `credential_capability` result for this
    resource (`credential_capability.stored_probe`). Passed in rather than
    read here so this function stays pure and testable without a registry —
    the same reason it takes `resolved_resources` instead of inspecting a
    run. `None` means "not supplied", and the resolver fetches it itself, but
    ONLY when some step actually declares a `requires_capability`: a repo or
    filesystem resolve makes no registry call for an axis none of its steps
    use.

    `capability_consented` is §7.1's first choice already taken: the user was
    shown the shortfall and chose to run partially anyway. It suppresses the
    capability reasons ONLY — the cost axis is untouched, because the two
    consents are about different things and one is not evidence of the other.
    It is the exact analogue of `/api/prerequisites/run` naming the steps the
    user was shown: without it, an accepted "run it anyway" would re-resolve,
    re-raise the same shortfall and skip the very step the user just approved,
    which is the one outcome the accept path exists to prevent.
    """
    if not step_registry:
        return Resolution(step_key, SATISFIED)
    info = step_registry.get(step_key)
    if info is None:
        return Resolution(step_key, SATISFIED)

    if (not capability_consented and capability_probe is None
            and _declares_capability(step_registry, step_registry.keys())):
        from resource_explorer.surveyors import credential_capability

        capability_probe = credential_capability.stored_probe(registry, entity)

    budget = Budget.for_step(info, max_fetch_cost, max_compute_cost)
    auto_run: list[str] = []
    consent: list[ConsentReason] = []
    dead_ends: list[str] = []
    reasons: list[str] = []
    started_by: list[str] = []
    ran = set(already_ran or ())

    def walk(key: str, path: tuple[str, ...]) -> None:
        """Depth-first over one step's unmet preconditions, appending the
        producers to `auto_run` in dependency order (deepest first)."""
        node = step_registry.get(key)
        ctx = dict(getattr(node, "requires_context", None) or {})
        if not ctx:
            return
        for name, why in step_preconditions.unmet(registry, entity, ctx):
            if key == step_key:
                started_by.append(name)
            entry = step_preconditions.PRECONDITIONS.get(name)
            producer = entry.produced_by() if entry else ""
            if not producer:
                dead_ends.append(name)
                reasons.append(why)
                continue
            if producer in path or producer == key:
                raise PrerequisiteCycleError(
                    "prerequisite cycle: "
                    + " -> ".join((*path, key, producer))
                    + f" (via precondition {name!r}). One of these steps declares "
                      "`produces` for a table it also gates on — fix the "
                      "declaration; there is no resource state that can cause this."
                )
            if producer in auto_run:
                # Already queued by another branch of this same walk; it will
                # run once, before everything that asked for it.
                continue
            if producer in ran:
                # It ran in THIS run and the precondition is STILL unmet. That
                # is a real answer — the producer looked and found nothing (or
                # found rows that do not satisfy this particular condition,
                # which is the Gradle/BOM case: 216 coordinates, 0 versions).
                # A dead end, never a satisfied precondition: treating it as
                # satisfied would dispatch a step with no input on the grounds
                # that something ran, which is worse than the skip this
                # replaced.
                dead_ends.append(name)
                reasons.append(
                    f"{why} — `{producer}` already ran in this survey and did not "
                    "produce what this step needs")
                continue
            if _found_nothing_on_this_snapshot(registry, entity.slug, producer,
                                               surveyed_at):
                # Its `nothing_found` IS the answer. Re-running it is the loop.
                dead_ends.append(name)
                reasons.append(
                    f"{why} — `{producer}` already ran on this snapshot and found "
                    "nothing, so running it again would not change the answer")
                continue
            producer_info = step_registry.get(producer)
            if producer_info is None:
                dead_ends.append(name)
                reasons.append(
                    f"{why} — `{producer}` produces it but is not a step of this "
                    "resource type")
                continue
            # Depth first: a producer's OWN prerequisites come before it.
            walk(producer, (*path, key))
            consent.extend(_consent_reasons(producer, producer_info, budget,
                                            resolved_resources))
            # The capability axis applies to a producer too: auto-running a
            # step whose credential cannot see what it reads would fill the
            # demanding step's input with a bounded answer and then present
            # the result as if the input were whole.
            producer_reason, _ = _capability_reason(
                producer, producer_info, capability_probe,
                consented=capability_consented)
            if producer_reason is not None:
                consent.append(producer_reason)
            auto_run.append(producer)

    walk(step_key, ())

    # The demanding step's OWN capability — the part with no cost-tier
    # equivalent. A step's cost tier IS the budget when the user asked for it
    # by name (module docstring, reading b), so a demanding step never
    # consents to its own cost. Capability does not work that way: asking for
    # a step does not widen a grant, so the shortfall survives the request and
    # has to be said out loud.
    own_reason, own_assessment = _capability_reason(
        step_key, info, capability_probe, consented=capability_consented,
        # No producers to run first (`auto_run` empty) means `sentence()`
        # will use the capability-only template, which already opens with
        # `step_key` — see `_capability_reason`'s own docstring.
        demanding=not auto_run)
    if own_reason is not None:
        consent.append(own_reason)

    if not auto_run and not dead_ends and not consent:
        return Resolution(step_key, SATISFIED)

    if consent:
        # Stop rule 2 — the WHOLE chain becomes one proposal. Not the crossing
        # steps only: running the cheap half first and then asking would spend
        # time on work the user may decline.
        seconds, measured = _estimate(registry, entity.slug, auto_run, step_registry)
        fetch_order, compute_order = _cost_orders()
        proposal = Proposal(
            steps=list(auto_run),
            reasons=list(consent),
            fetch_cost=max((getattr(step_registry[s], "fetch_cost", "none") or "none"
                            for s in auto_run), key=fetch_order.index, default="none"),
            compute_cost=max((getattr(step_registry[s], "compute_cost", "low") or "low"
                              for s in auto_run), key=compute_order.index, default="low"),
            estimated_seconds=seconds,
            estimated_is_measured=measured,
            demanding_step=step_key,
            preconditions=list(dict.fromkeys(started_by)),
            # §7.1's first choice. Set only when the DEMANDING step is the one
            # short on capability — a producer's shortfall is answered by
            # running the chain, which `steps` already names.
            run_partially=step_key if own_reason is not None else "",
            capability=own_assessment.as_dict() if own_reason is not None else None,
        )
        return Resolution(step_key, PROPOSAL, proposal=proposal,
                          reason=proposal.sentence(), dead_ends=dead_ends)

    if not auto_run:
        return Resolution(
            step_key, UNSATISFIABLE,
            reason="; ".join(reasons) or "a precondition is unmet and nothing produces it",
            dead_ends=dead_ends)

    return Resolution(
        step_key, AUTO_RUN, auto_run=auto_run, dead_ends=dead_ends,
        reason="; ".join(reasons))


def auto_run_annotation(producer: str, demanding_step: str, precondition: str) -> dict:
    """§17.1 condition 3's annotation payload — an auto-run is a *result*.

    Deliberately the same shape a skip carries (`step_preconditions.
    skip_status`): both are things that happened to the run which the step
    itself did not report, and a reader should find them the same way.
    """
    return {
        "state": "auto_run_prerequisite",
        "cause": precondition,
        "hint": (f"ran `{producer}` because `{demanding_step}` required "
                 f"{precondition}"),
        "producer": producer,
        "demanded_by": demanding_step,
        "known_positive": True,
    }
