"""Level 2 of a step (brief 2c', the owner's three levels): `SurveyStep`, the wrapper that gives a user's plain
callable the behaviour of an Egeria Survey Step.

    level 1  the code      run(inputs) -> results         the user's; owns the measurement and its reason
    level 2  the wrapper   SurveyStep(manifest).run(host)  this module; owns everything below
    level 3  the survey    an Egeria Survey Definition     Dr.Egeria document naming the step by re_analysis_step

What the wrapper owns, so the code does not have to:

- **Declared inputs, enforced.** The code gets the inputs its manifest declares (`StepManifest.inputs`) and
  nothing else: an undeclared ODS reader is not handed over.
- **Never a password.** A connection, when declared, is opened by the host at the declared tier and handed as an
  object. Neither `HostContext` nor `StepInputs` has a field that could carry a credential, so a host cannot pass
  one through by accident.
- **Read-only on the resource and nothing more** (owner ruling 2026-10-07). A manifest declaring a higher tier
  is refused, with a sentence naming the tier, here as well as when the manifest is built.
- **The envelope on every result.** Each result the code returns names a state (one of the four) and a scope;
  the wrapper adds the producing run, the time, the producer version and the scope. A result with no state, an
  unknown state, or a `not_established`/`not_applicable` with no reason is refused, and nothing of that run is
  stored: a partial store would read as the whole answer.
- **Storage.** The host supplies the sink (`HostContext.store`); the wrapper decides what reaches it.

What a host may add (Resource Explorer's does): `inputs.ods` narrowed to typed read methods (no SQL text), and the code
run with no Egeria identity. A step is in-process, trusted code; none of this is a sandbox.

A result the code returns is a plain dict:

    {"scope": "public.orders", "state": "measured", "value": {"rowsChanged": 30}, "summary": "+30 rows",
     "reason": ""}
"""
from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, Field

from trellis_analytics.envelope import Envelope, ResultState, _now
from trellis_analytics.manifest import StepManifest, tier_refusal

log = logging.getLogger(__name__)


_STATE_SENTENCE = {ResultState.MEASURED: "measured", ResultState.NOTHING_FOUND: "nothing found",
                   ResultState.NOT_ESTABLISHED: "not measured", ResultState.NOT_APPLICABLE: "not applicable"}


def _state_sentence(state: ResultState, reason: str) -> str:
    """The short words a result carries when its step gave none: the state, then why. Never empty, so a state shown
    on screen always has words from the stored data."""
    words = _STATE_SENTENCE[state]
    return f"{words} · {reason}" if reason else words


class StepRefused(Exception):
    """The wrapper would not run the step, or would not keep what it returned; the sentence says why."""


@dataclass(frozen=True)
class HostContext:
    """What a host (RE's orchestrator, later Prefect or an engine host) gives the wrapper for one run.

    `connect(tier)` opens a connection to the resource at that tier and returns it; the host keeps the
    credential. `store(annotations)` keeps the run's annotations and returns how many it kept."""
    resource_type: str
    slug: str
    producing_run: str
    ods: Any = None
    connect: Callable[[str], Any] | None = None
    store: Callable[[list["StepAnnotation"]], int] | None = None
    #: The previous process step's report, as plain data, when the host has one (`source_report` input).
    source_report: Any = None


@dataclass(frozen=True)
class StepInputs:
    """What the code receives: the declared inputs, and nothing that could carry a credential."""
    resource_type: str
    slug: str
    ods: Any = None
    connection: Any = None
    source_report: Any = None
    #: The manifest's declared `parameters`, a copy: what the declaration publishes is what the code reads.
    parameters: dict = field(default_factory=dict)


class StepAnnotation(BaseModel):
    """One result, as an annotation of the manifest's type, carrying its envelope.

    Interim (owner ruling 2026-10-07): the envelope travels in `additionalProperties` of a
    ResourceMeasureAnnotation; the measured values in `resourceProperties`."""
    annotation_type: str
    analysis_step: str
    scope: str
    summary: str = ""
    explanation: str = ""
    resource_properties: dict[str, str] = Field(default_factory=dict)
    envelope: Envelope
    #: Which of the manifest's declared measures this result is; "" is the step's own (primary) measure.
    measure: str = ""
    #: For a measure published as a ClassificationAnnotation: the classifications it PROPOSES (Egeria's
    #: `candidateClassifications`). A proposal on the annotation, never a classification on the element.
    candidate_classifications: list[str] = Field(default_factory=list)

    def additional_properties(self) -> dict[str, str]:
        props = self.envelope.to_additional_properties()
        if self.measure:
            props["measure"] = self.measure
        return props

    def to_record(self) -> dict:
        """The shape a host stores, Egeria's property names where Egeria has them."""
        return {"scope": self.scope, "measure": self.measure, "annotation_type": self.annotation_type,
                "analysis_step": self.analysis_step,
                "summary": self.summary, "explanation": self.explanation,
                "resourceProperties": dict(self.resource_properties),
                "additionalProperties": self.additional_properties(),
                **({"candidateClassifications": list(self.candidate_classifications)}
                   if self.candidate_classifications else {})}


def _resolve(dotted: str) -> Callable:
    module, _, name = dotted.rpartition(".")
    return getattr(importlib.import_module(module), name)


class SurveyStep:
    def __init__(self, manifest: StepManifest, code: Callable | None = None):
        if getattr(manifest, "kind", "") != "step":
            raise StepRefused(f"'{manifest.name}' is a {manifest.kind}, not a step")
        # Checked again here: a manifest built with model_construct skips its own validation.
        if refusal := tier_refusal(manifest.credential_tier):
            raise StepRefused(f"step '{manifest.name}': {refusal}")
        self.manifest = manifest
        # Questions and purposes are suggestive (owner ruling 2026-10-13): unknown ones warn, naming them.
        from trellis_analytics.discovery import check_purposes, check_questions
        check_questions({manifest.name: manifest})
        check_purposes({manifest.name: manifest})
        # The code is looked up at each run unless given, so the installed version is the one that runs.
        self._code = code

    @property
    def code(self) -> Callable:
        return self._code or _resolve(self.manifest.function)

    def inputs_for(self, host: HostContext) -> StepInputs:
        declared = set(self.manifest.inputs)
        connection = None
        if "connection" in declared:
            if host.connect is None:
                raise StepRefused(f"step '{self.manifest.name}' declares a connection and this host cannot open "
                                  f"one at '{self.manifest.credential_tier}'")
            connection = host.connect(self.manifest.credential_tier)
        return StepInputs(resource_type=host.resource_type, slug=host.slug,
                          ods=host.ods if "ods" in declared else None, connection=connection,
                          source_report=host.source_report if "source_report" in declared else None,
                          parameters=dict(getattr(self.manifest, "parameters", None) or {}))

    def _annotate(self, result: Any, host: HostContext, measured_at: str) -> StepAnnotation:
        name = self.manifest.name
        if not isinstance(result, dict):
            raise StepRefused(f"step '{name}' returned a {type(result).__name__}, not a result dict")
        scope = str(result.get("scope") or "")
        if not scope:
            raise StepRefused(f"step '{name}' returned a result with no scope: say what it is about")
        state = result.get("state")
        if not state:
            raise StepRefused(f"step '{name}' returned a result for '{scope}' with no state: every result says "
                              "measured, nothing_found, not_established or not_applicable")
        try:
            rstate = ResultState(state)
        except ValueError:
            raise StepRefused(f"step '{name}' returned state '{state}' for '{scope}', which is not one of the "
                              "four") from None
        reason = str(result.get("reason") or "")
        try:
            envelope = Envelope(result_state=rstate, result_state_reason=reason, producing_run=host.producing_run,
                                measured_at=measured_at, producer_version=self.manifest.version, scope=scope)
        except ValueError as exc:
            raise StepRefused(f"step '{name}', '{scope}': {exc}") from exc
        measure = str(result.get("measure") or "")
        if measure == name:
            measure = ""
        if measure and measure not in self.manifest.measure_names():
            raise StepRefused(f"step '{name}' returned a result for '{scope}' of measure '{measure}', which its "
                              f"manifest does not declare ({', '.join(self.manifest.measure_names())})")
        value = result.get("value") or {}
        return StepAnnotation(measure=measure,
            annotation_type=self.manifest.annotation_type, analysis_step=name, scope=scope,
            summary=str(result.get("summary") or "") or _state_sentence(rstate, reason), explanation=reason,
            resource_properties={str(k): str(v) for k, v in value.items() if v is not None},
            envelope=envelope)

    def _typed(self, annotation: StepAnnotation, result: dict) -> StepAnnotation:
        """The annotation with its measure's declared Egeria type (`StepManifest.measure_annotation_types`) and, for a
        ClassificationAnnotation, the classifications the result proposes (`candidate_classifications`, plain
        strings). A proposal given for any other type is refused: it would be dropped on the way to Egeria."""
        atype = self.manifest.annotation_type_for(annotation.measure)
        raw = result.get("candidate_classifications") or []
        if isinstance(raw, str) or not all(isinstance(c, str) and c.strip() for c in raw):
            raise StepRefused(f"step '{self.manifest.name}', '{annotation.scope}': candidate_classifications must be "
                              "a list of non-empty strings")
        if raw and atype != "ClassificationAnnotation":
            raise StepRefused(f"step '{self.manifest.name}', '{annotation.scope}': proposes classifications in a "
                              f"{atype}; only a ClassificationAnnotation carries them")
        if len(raw) > 1:
            raise StepRefused(f"step '{self.manifest.name}', '{annotation.scope}': proposes {len(raw)} "
                              "classifications; a ClassificationAnnotation names one (Egeria's classificationName)")
        if atype == annotation.annotation_type and not raw:
            return annotation
        return annotation.model_copy(update={"annotation_type": atype, "candidate_classifications": list(raw)})

    def run(self, host: HostContext) -> list[StepAnnotation]:
        """Run the code once for one resource; return (and store, when the host gives a store) its annotations.
        Raises StepRefused when the results cannot all be kept; nothing is stored then."""
        if host.resource_type not in self.manifest.resource_types:
            raise StepRefused(f"step '{self.manifest.name}' measures {', '.join(self.manifest.resource_types)}, "
                              f"not {host.resource_type}")
        results = self.code(self.inputs_for(host))
        measured_at = _now()
        annotations = [self._typed(self._annotate(r, host, measured_at), r) for r in (results or [])]
        if host.store is not None:
            host.store(annotations)
        return annotations
