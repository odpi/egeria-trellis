"""Request parameters a step accepts, and the one place they are checked.

A Survey Definition's process step (Egeria's `GovernanceActionExecutor.requestParameters`) and an engine
action RE claims as an engine host both carry a string-to-string map of request parameters. Until 2026-10-20
neither reached a step: the RE-coordinated executor parsed them onto `SurveyStep.request_parameters` and the
engine host stored them on the run target, and nothing read either again. A step now declares what it accepts
(`StepInfo.parameters`), and `coerce_step_parameters` turns what arrived into keyword arguments for the step.

Three rules:

- **Only declared names are forwarded.** Anything else is reported as ignored, by name, never silently
  dropped and never passed through: Egeria's own parameters (for example `includeSchemaNames` for its native
  survey) travel in the same map.
- **A declared name with a value that does not parse is refused**, with the value and the reason. A step
  never runs with a parameter it was given but could not read, because the result would look like the run
  that was asked for when it was not.
- **Reserved names are never accepted from a request**, whatever a step declares: the credential, the run's
  own timestamp and the previous step's report are RE's to supply.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

#: Keyword arguments RE itself supplies to a step runner. A request parameter can never set one.
RESERVED_NAMES: frozenset[str] = frozenset({
    "db_user", "db_pwd", "scan_surveyed_at", "source_report", "credential_scope", "capability_consented",
})

JSON_OBJECT, BOOL, INT, STR = "json_object", "bool", "int", "str"
_TRUE, _FALSE = frozenset({"true", "yes", "1", "on"}), frozenset({"false", "no", "0", "off"})


@dataclass(frozen=True)
class StepParameter:
    """One parameter a step accepts: its type and, optionally, a check that raises on a bad value."""

    kind: str
    description: str = ""
    validate: Callable[[Any], None] | None = None


class StepParameterError(ValueError):
    """A request parameter a step declares arrived with a value it cannot use."""


@dataclass
class CheckedParameters:
    accepted: dict[str, Any] = field(default_factory=dict)
    ignored: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)  # one sentence per refused parameter

    def summary(self) -> dict:
        """For a run's step report: what was used and what was not, by name (values are not echoed)."""
        return {"used": sorted(self.accepted), "ignored": sorted(self.ignored), "invalid": list(self.invalid)}

    def raise_if_invalid(self, step_key: str) -> None:
        if self.invalid:
            raise StepParameterError(f"{step_key}: " + "; ".join(self.invalid))


def _coerce(kind: str, value: Any) -> Any:
    if kind == JSON_OBJECT:
        parsed = json.loads(value) if isinstance(value, str) else value
        if not isinstance(parsed, dict):
            raise ValueError("expected a JSON object")
        return parsed
    if kind == BOOL:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise ValueError("expected true or false")
    if kind == INT:
        if isinstance(value, bool):
            raise ValueError("expected a whole number")
        return int(str(value).strip())
    if kind == STR:
        return str(value)
    raise ValueError(f"unknown parameter kind {kind!r}")


def coerce_step_parameters(declared: Mapping[str, StepParameter] | None,
                           given: Mapping[str, Any] | None) -> CheckedParameters:
    """Split `given` into what `declared` accepts (coerced), what it does not declare, and what it refuses."""
    out = CheckedParameters()
    declared = declared or {}
    for name, value in (given or {}).items():
        spec = declared.get(name)
        if name in RESERVED_NAMES:
            out.invalid.append(f"request parameter {name!r} is supplied by Resource Explorer, never by a request")
            continue
        if spec is None:
            out.ignored.append(name)
            continue
        try:
            coerced = _coerce(spec.kind, value)
            if spec.validate is not None:
                spec.validate(coerced)
        except Exception as exc:  # noqa: BLE001 -- any parse or check failure is a refusal, in its own words
            out.invalid.append(f"request parameter {name!r} = {str(value)[:200]!r} is not valid: {exc}")
            continue
        out.accepted[name] = coerced
    return out
