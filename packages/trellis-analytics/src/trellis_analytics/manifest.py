"""Manifests: subclasses of pyegeria's AnalyticFunctionSpec carrying what trellis adds or narrows.

Since pyegeria 6.2.3 (ISSUE-149) the base class carries `kind`, `source`, `version`, `envelope`,
`annotation_type`, `resource_types`, `family` and `tags` itself, all optional. This module no longer redeclares the
ones it would only repeat (`annotation_type`, `resource_types`, `family`, `tags`). It keeps only the overrides
trellis wants stricter than the base:

- `source` is a closed `Literal["ods", "egeria", "resource"]` and required. The base is an open string that warns on
  an unknown value and keeps it ("suggestive"); a trellis manifest is checked at construction instead.
- `version` is required. The base defaults it to "", which would stamp an unversioned result.
- `envelope` defaults to True. Every trellis routine returns an Envelope; the base default is False.
- `kind` is one word per subclass (`Literal["routine"]` / `Literal["step"]`), so a RoutineManifest cannot be
  built as a step. The base allows either on either.

and what the base does not have: `purposes` and `questions` (qualifiers checked at load) and a step's
`credential_tier`, `inputs`, `display_name`, `stage`, `perspectives`, `request_type` and
`nothing_to_derive_guard`.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pyegeria.view.analytic_registry import AnalyticFunctionSpec

Source = Literal["ods", "egeria", "resource"]


class _TrellisManifest(AnalyticFunctionSpec):
    source: Source
    version: str
    envelope: bool = True
    #: Which investigation purposes this serves (a qualifier). Checked at load against Egeria's purposes list
    #: (`discovery.discover`); suggestive, so an unknown value warns, naming it, and is kept.
    purposes: list[str] = Field(default_factory=list)
    #: The questions this answers, BY STABLE ID (`Q-0001`, the `id` of an entry in Resource Explorer's question
    #: catalog), never by wording: the wording is edited, the id is not. Checked at load against the catalog
    #: (`discovery.check_questions`); suggestive, so an unknown id warns, naming it, and is kept.
    questions: list[str] = Field(default_factory=list)


def question_warnings(manifest: _TrellisManifest, known: set[str]) -> list[str]:
    """One sentence per declared question id outside `known`, naming the manifest and the id. Never raises."""
    return [f"manifest '{manifest.name}': question '{q}' is not an id in the question catalog; kept as written"
            for q in manifest.questions if q and q.strip() not in known]


def purpose_warnings(manifest: _TrellisManifest, known: list[str]) -> list[str]:
    """One sentence per declared purpose outside `known`, naming the manifest and the value. Never raises."""
    folded = {" ".join(k.split()).casefold() for k in known}
    return [f"manifest '{manifest.name}': purpose '{p}' is not in the investigation-purposes list; kept as written"
            for p in manifest.purposes if p and " ".join(p.split()).casefold() not in folded]


class RoutineManifest(_TrellisManifest):
    kind: Literal["routine"] = "routine"


#: What a step may ask of the resource it measures (owner ruling 2026-10-07): nothing, or read-only. Anything
#: higher is refused, with a sentence naming the tier, until a publication-style profile says otherwise.
CREDENTIAL_TIERS = ("none", "read_only")

#: What the wrapper can hand a step's code. A step gets the inputs it declares and nothing else.
#:   resource   - the resource's type and slug (always handed; declaring it alone hands nothing more)
#:   ods        - a read-only reader of RE's registry (the ODS)
#:   connection - a connection to the resource opened at the declared tier, never a password
#:   source_report - the report of the step before this one in an Egeria process, as plain data
#:                   ({guid, at, annotations}), read by the host (RE's engine host: the `surveyReport` action target);
#:                   a host with none hands None, and RE's engine host refuses to run the step without one
STEP_INPUTS = ("resource", "ods", "connection", "source_report")


#: Egeria's standard survey guards (`SurveyActionGuard`): survey-completed, survey-invalid, survey-failed. A step
#: names one of them as what applies when it has nothing to derive (Mandy, Q-M5: use the standard guards).
SURVEY_GUARDS = ("survey-completed", "survey-invalid", "survey-failed")

#: A request type as an engine advertises it: lower-case words joined by single hyphens (`re-db-derived`).
_REQUEST_TYPE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def default_request_type(name: str) -> str:
    """`database_staleness` -> `re-database-staleness`: the shape of Resource Explorer's built-in request types."""
    return "re-" + name.replace("_", "-")


def tier_refusal(tier: str) -> str:
    """The sentence for a tier above read-only, or "" for an allowed one."""
    if tier in CREDENTIAL_TIERS:
        return ""
    return (f"credential tier '{tier}' refused: a user step may ask for read-only on the resource and nothing "
            "more (owner ruling 2026-10-07)")


class StepManifest(_TrellisManifest):
    """Level 2's declaration (brief 2c'): what the wrapper needs to run a user's code as a survey step."""
    kind: Literal["step"] = "step"
    annotation_type: str = "ResourceMeasureAnnotation"
    credential_tier: str = "none"
    inputs: list[str] = Field(default_factory=lambda: ["ods"])
    #: Shown as the analysis's name in Resource Explorer and as the Egeria element's display name.
    display_name: str = ""
    #: The stage this step's results belong to (RE's catalog calls it `intent`: scouting, analysis, ...).
    stage: str = "analysis"
    perspectives: list[str] = Field(default_factory=lambda: ["all"])
    #: The request type an engine host advertises for this step and dispatches on (Mandy, Q-M2: "RE dispatches on
    #: the request type to choose the action to invoke"). Empty means `default_request_type(name)`.
    request_type: str = ""
    #: Which of Egeria's survey guards (`SURVEY_GUARDS`) the engine host completes with when the step has nothing
    #: to derive: no result it returns is `measured` or `nothing_found`. With `survey-completed` the report is
    #: still published; with either other guard no report is written (an absence, not a finding).
    nothing_to_derive_guard: str = "survey-invalid"
    #: Every measure the step reports, each published as its own annotation and never folded into another (design
    #: ruling, engine host step 4: "never one function with two meanings"). The step's own name is its primary
    #: measure; a result names another declared measure with `"measure": <name>`. Empty means the step's own only.
    measures: list[str] = Field(default_factory=list)
    #: The Egeria annotation type of a declared measure, when it is not the step's `annotation_type` (for example a
    #: `ClassificationAnnotation` proposing a classification beside a step's ResourceMeasureAnnotations). Only for
    #: declared measures; a measure not named here is published as `annotation_type`.
    measure_annotation_types: dict[str, str] = Field(default_factory=dict)
    #: The step's tunable values (a threshold, a window), declared here so the published declaration says exactly
    #: what the code uses: the wrapper hands them to the code as `inputs.parameters`, and the code reads them from
    #: there rather than from a constant of its own.
    parameters: dict[str, int | float | str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _measure_types(self) -> "StepManifest":
        names = self.measure_names()
        unknown = [m for m in self.measure_annotation_types if m not in names]
        if unknown:
            raise ValueError(f"measure_annotation_types names {', '.join(unknown)}, which the step does not declare "
                             f"as measures ({', '.join(names)})")
        empty = [m for m, t in self.measure_annotation_types.items() if not str(t or "").strip()]
        if empty:
            raise ValueError(f"measure_annotation_types gives no annotation type for {', '.join(empty)}")
        return self

    def annotation_type_for(self, measure: str = "") -> str:
        """The Egeria annotation type a result of `measure` ("" or the step's own name: the primary) is published as."""
        return self.measure_annotation_types.get(measure or self.name, self.annotation_type)

    def annotation_types(self) -> list[str]:
        """Every annotation type the step can emit, the step's own first."""
        out = [self.annotation_type]
        for m in self.measure_names():
            t = self.annotation_type_for(m)
            if t not in out:
                out.append(t)
        return out

    @field_validator("nothing_to_derive_guard")
    @classmethod
    def _guard(cls, v: str) -> str:
        if v not in SURVEY_GUARDS:
            raise ValueError(f"nothing_to_derive_guard '{v}' is not one of Egeria's survey guards "
                             f"({', '.join(SURVEY_GUARDS)})")
        return v

    @model_validator(mode="after")
    def _measures(self) -> "StepManifest":
        if self.measures and self.name not in self.measures:
            raise ValueError(f"measures {self.measures} must include the step's own measure '{self.name}'")
        return self

    def measure_names(self) -> list[str]:
        """The declared measures, the step's own first."""
        return [self.name] + [m for m in self.measures if m != self.name]

    @model_validator(mode="after")
    def _request_type(self) -> "StepManifest":
        if not self.request_type:
            self.request_type = default_request_type(self.name)
        if not _REQUEST_TYPE.match(self.request_type):
            raise ValueError(f"request_type '{self.request_type}' must be lower-case words joined by hyphens "
                             "(for example re-database-staleness)")
        return self

    @field_validator("credential_tier")
    @classmethod
    def _tier(cls, v: str) -> str:
        if refusal := tier_refusal(v):
            raise ValueError(refusal)
        return v

    @field_validator("inputs")
    @classmethod
    def _inputs(cls, v: list[str]) -> list[str]:
        unknown = [i for i in v if i not in STEP_INPUTS]
        if unknown:
            raise ValueError(f"input {', '.join(repr(u) for u in unknown)} is not one the wrapper hands a step "
                             f"(known: {', '.join(STEP_INPUTS)})")
        return v

    @model_validator(mode="after")
    def _connection_needs_a_tier(self) -> "StepManifest":
        if "connection" in self.inputs and self.credential_tier == "none":
            raise ValueError("a step that declares the 'connection' input must declare credential_tier "
                             "'read_only'; with 'none' there is no connection to hand it")
        return self
