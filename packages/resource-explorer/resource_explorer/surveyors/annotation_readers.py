"""One generic reader per annotation type (R7; extensibility brief 2c, its first instance).

A step's stored annotations are read by their TYPE, not by the step that wrote them, so a new step of a known
type needs no reader of its own. The reader derives each row's state from the annotation's envelope
(`additionalProperties.resultState`, the interim carrier, owner ruling 2026-10-07) and never from its value: a
value of 0 is not "nothing found", and a missing value is not "not established". A row with no envelope says
"no envelope"; it is never given a state the step did not state.

Rows come back in the Analysis pane's measurement shape (`{name, value, opens, note}`, plus `state`,
`state_words` and `reason`), so `stage_page.build_measurements` hands them over unflattened.
"""
from __future__ import annotations

from typing import Callable

#: The four envelope states and their words (the same words as trellis_analytics' ResultState). Kept here so
#: Resource Explorer reads stored rows without the library installed.
STATE_WORDS = {
    "measured": "measured",
    "nothing_found": "nothing found",
    "not_established": "not established",
    "not_applicable": "not applicable",
}
NO_ENVELOPE = "no envelope"


def _envelope_state(detail: dict) -> tuple[str, str, str]:
    """(state, words, reason) from the stored envelope alone."""
    props = (detail or {}).get("additionalProperties") or {}
    state = props.get("resultState") or ""
    if state not in STATE_WORDS:
        return "", NO_ENVELOPE, ""
    return state, STATE_WORDS[state], props.get("resultStateReason") or ""


def read_resource_measure_rows(registry, entity_type: str, slug: str, analysis_step: str) -> tuple[str, list[dict]]:
    """ResourceMeasureAnnotation: one row per annotation of the step's latest run."""
    run, stored = registry.query_step_annotations(entity_type, slug, analysis_step)
    rows = []
    for a in stored:
        detail = a.get("detail") or {}
        state, words, reason = _envelope_state(detail)
        rows.append({
            "name": a.get("scope") or detail.get("scope") or "",
            # The step's own sentence for what it measured; empty when it did not measure (the reason is
            # shown beside the state words instead, once).
            "value": a.get("summary") or "",
            "opens": None,
            "note": "",
            "state": state,
            "state_words": words,
            "reason": reason,
            "properties": detail.get("resourceProperties") or {},
            "annotation_type": a.get("annotation_type") or "",
            "run": run,
        })
    return run, rows


#: annotation type -> reader. One entry per type; a step of a listed type is readable with no new code.
READERS: dict[str, Callable] = {
    "ResourceMeasureAnnotation": read_resource_measure_rows,
}


def measurement_rows(registry, entity_type: str, slug: str, analysis_step: str, annotation_type: str) -> list[dict]:
    reader = READERS.get(annotation_type)
    if reader is None:
        raise LookupError(f"no reader for annotation type '{annotation_type}' (readers: {', '.join(READERS)})")
    return reader(registry, entity_type, slug, analysis_step)[1]


def results_reader(entity_type: str, analysis_step: str, annotation_type: str) -> Callable:
    """A `(registry, slug) -> dict` results reader for the analysis maps: `measurement_rows` (already shaped,
    which build_measurements passes through), the run they came from, and a count per state."""
    def read(registry, slug: str) -> dict:
        run, rows = READERS[annotation_type](registry, entity_type, slug, analysis_step)
        if not rows:
            return {}
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["state_words"]] = counts.get(r["state_words"], 0) + 1
        return {"measurement_rows": rows, "run": run, "state_counts": counts}
    read.__name__ = f"{analysis_step}_results"
    return read


def headline_reader(entity_type: str, analysis_step: str, annotation_type: str) -> Callable:
    """The one-line answer, from the states alone: "4 tables: 1 measured · 1 nothing found · ..."."""
    reader = results_reader(entity_type, analysis_step, annotation_type)

    def headline(registry, slug: str) -> dict | None:
        value = reader(registry, slug)
        if not value:
            return None
        counts = value["state_counts"]
        order = [*STATE_WORDS.values(), NO_ENVELOPE]
        parts = [f"{counts[w]} {w}" for w in order if counts.get(w)]
        n = len(value["measurement_rows"])
        return {"label": f"{n} result{'s' if n != 1 else ''}: " + " · ".join(parts), "status": "info"}
    return headline
