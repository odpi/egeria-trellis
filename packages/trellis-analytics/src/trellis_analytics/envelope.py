"""The result envelope: what a result says about itself, beside its value.

Four states, no fifth. `nothing_found` (looked, there was nothing) and
`not_established` (could not look, or could not tell) are never the same word.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ResultState(str, Enum):
    MEASURED = "measured"
    NOTHING_FOUND = "nothing_found"
    NOT_ESTABLISHED = "not_established"
    NOT_APPLICABLE = "not_applicable"

    @property
    def words(self) -> str:
        return self.value.replace("_", " ")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_PROP_KEYS = ("resultState", "resultStateReason", "producingRun", "measuredAt", "producerVersion", "scope")


class Envelope(BaseModel):
    result_state: ResultState
    result_state_reason: str = ""
    producing_run: str = ""
    measured_at: str = Field(default_factory=_now)
    producer_version: str = ""
    scope: str = ""

    def model_post_init(self, _ctx: Any) -> None:
        # A state that is not `measured` without a reason is the silent kind of absence.
        if self.result_state in (ResultState.NOT_ESTABLISHED, ResultState.NOT_APPLICABLE) \
                and not self.result_state_reason:
            raise ValueError(f"{self.result_state.value} needs a reason: say why it could not be established")

    def to_additional_properties(self) -> dict[str, str]:
        """The interim carrier (owner ruling 2026-10-07): ResourceMeasureAnnotation.additionalProperties."""
        return {
            "resultState": self.result_state.value,
            "resultStateReason": self.result_state_reason,
            "producingRun": self.producing_run,
            "measuredAt": self.measured_at,
            "producerVersion": self.producer_version,
            "scope": self.scope,
        }

    @classmethod
    def from_additional_properties(cls, props: dict[str, Any]) -> "Envelope":
        missing = [k for k in ("resultState", "measuredAt") if not props.get(k)]
        if missing:
            raise ValueError(f"no envelope: additionalProperties lacks {missing}")
        return cls(
            result_state=ResultState(props["resultState"]),
            result_state_reason=props.get("resultStateReason", ""),
            producing_run=props.get("producingRun", ""),
            measured_at=props["measuredAt"],
            producer_version=props.get("producerVersion", ""),
            scope=props.get("scope", ""),
        )
