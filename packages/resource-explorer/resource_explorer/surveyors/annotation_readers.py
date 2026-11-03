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

from collections.abc import Callable

#: The four envelope states and their words (the same words as trellis_analytics' ResultState). Kept here so
#: Resource Explorer reads stored rows without the library installed.
STATE_WORDS = {
    "measured": "measured",
    "nothing_found": "nothing found",
    "not_established": "not established",
    "not_applicable": "not applicable",
}
NO_ENVELOPE = "no envelope"


def _us(text):
    """Stored step prose in US spelling (a run before 2026-10-30 wrote "analysed"); see `declared_words.us_spelling`."""
    from resource_explorer.declared_words import us_spelling

    return us_spelling(text)


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
            "value": _us(a.get("summary") or ""),
            "opens": None,
            "note": "",
            "state": state,
            "state_words": words,
            "reason": reason,
            "properties": detail.get("resourceProperties") or {},
            # Which of the step's declared measures this row is ("" its own, primary one), where the row says. The
            # host stores each further measure under its own key (`measure_step_key`), so a reader of one key sees
            # one measure; `declared_context.measure_records` reads each measure from its key.
            "measure": detail.get("measure") or (detail.get("additionalProperties") or {}).get("measure") or "",
            "annotation_type": a.get("annotation_type") or "",
            "run": run,
        })
    return run, rows


#: annotation type -> reader. One entry per type; a step of a listed type is readable with no new code.
READERS: dict[str, Callable] = {
    "ResourceMeasureAnnotation": read_resource_measure_rows,
    # A file system's library steps (Files build step 6) publish Egeria's folder-survey types. Their stored rows carry
    # the same envelope, scope, summary and resourceProperties, so they are read the same way.
    "ResourceProfileAnnotation": read_resource_measure_rows,
    "RequestForActionAnnotation": read_resource_measure_rows,
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


def _step_sentence(row: dict) -> str:
    """The step's own sentence for one stored result, the way the Survey tab's "Derived from the walk" reads it: the state
    words, then what the step said, and its reason when it could not measure (never a count of results)."""
    said = _us(row.get("summary") or row.get("value") or "")
    out = f"{row.get('state_words') or NO_ENVELOPE} · {said}".rstrip(" ·") if said else str(row.get("state_words") or NO_ENVELOPE)
    reason = _us(row.get("reason"))
    return f"{out} · {reason}" if reason and row.get("state") in ("not_established", "not_applicable") else out


def _declared_words(dec, name: str, rec: dict) -> dict:
    """A measure record with its sentence derived from the declaration where the measure declares a basis or a cap: "456 of
    1,236 columns", "at least 500 rows", from the stored fields, never from the step's own prose (brief 2026-10-30). A
    record that is not a measured value keeps the step's sentence (an absence is its own words, never a number), as does a
    measure declaring neither. `capped` says the sentence is a lower bound."""
    from resource_explorer import declared_words

    m = dec.measure(name)
    out = {**rec, "capped": False}
    if m is None or not (m.basis or m.capped_field) or rec.get("state") != "measured":
        return out
    allow = declared_words.allows_of(dec)
    words = declared_words.measure_words(m, rec.get("value") or {}, {}, allow_of=allow)
    # Derived only where it adds what was declared: the whole is in the result, or the value is a lower bound. A basis whose
    # denominator the result does not hold leaves the step's own sentence, which says more than a bare count would.
    if words and (words["denominator"] is not None or words["capped"]):
        out.update(summary=declared_words.measure_sentence(m, rec.get("value") or {}, {}, allow_of=allow)[0],
                   capped=words["capped"])
    return out


def _declared_summary(registry, entity_type: str, slug: str, step: str) -> dict | None:
    """The step's resource-scoped measure record (its one-per-database summary), when it declares one and it ran.

    A step declaring several takes the FIRST in the manifest's declaration order that ran and has a summary
    (`measure_records` walks `dec.measures` in order), so the headline is stable across reads and the author picks it
    by ordering the declaration. The others are not dropped: `declared_measure_rows` gives each its own drill-down row."""
    from resource_explorer.surveyors.analysis_catalog_reader import _load
    from resource_explorer import declared_context

    for entry in _load().get(entity_type, []):
        provided = getattr(entry, "provided_by", None) or {}
        if (provided.get("step") or entry.id) != step or not getattr(entry, "declaration", None):
            continue
        dec = entry.declaration
        for name, rec in declared_context.measure_records(registry, entity_type, slug, step, dec, entry.id).items():
            if rec.get("summary"):
                return {**_declared_words(dec, name, rec), "state_words": STATE_WORDS.get(rec.get("state") or "", NO_ENVELOPE)}
    return None


def declared_measure_rows(registry, entity_type: str, slug: str, step: str) -> list[dict]:
    """The step's further resource-scoped measures as drill-down rows, each with its own stated state (F19).

    The host stores the step's own measure under the step key and each further declared measure under its own key, so
    the results reader sees only the first. A measure that could not be measured (fs_activity's access bands on a walk
    that kept no access times) is its own row: its state word and its reason, the sentence the step gave, never a
    number and never the whole step's state. Empty when the step declares nothing more."""
    from resource_explorer.surveyors.analysis_catalog_reader import _load
    from resource_explorer import declared_context

    for entry in _load().get(entity_type, []):
        provided = getattr(entry, "provided_by", None) or {}
        if (provided.get("step") or entry.id) != step or not getattr(entry, "declaration", None):
            continue
        names = [m.name for m in entry.declaration.measures]
        primary = next((n for n in (step, entry.id) if n in names), names[0] if names else "")
        out = []
        for name, rec in declared_context.measure_records(registry, entity_type, slug, step, entry.declaration, entry.id).items():
            if name == primary:
                continue          # the results reader already returned the step's own measure
            rec = _declared_words(entry.declaration, name, rec)
            state = rec.get("state") or ""
            words = STATE_WORDS.get(state, NO_ENVELOPE)
            out.append({"name": name, "value": _us(rec.get("summary") or rec.get("reason") or words), "opens": None, "note": "",
                        "state": state, "state_words": words, "reason": rec.get("reason") or "",
                        "properties": rec.get("value") or {}, "measure": name, "annotation_type": "", "run": rec.get("run"),
                        "capped": rec["capped"]})
        return out
    return []


def _categories_legend(entity_type: str, step: str, summary: dict | None) -> str:
    """The legend a step line shows for the categories its declaration declares ("each item is in one category", "categories
    overlap · not stacked or added up"), or ""."""
    from resource_explorer import declared_words
    from resource_explorer.surveyors.analysis_catalog_reader import _load

    for entry in _load().get(entity_type, []):
        provided = getattr(entry, "provided_by", None) or {}
        if (provided.get("step") or entry.id) == step and getattr(entry, "declaration", None):
            return declared_words.categories_legend(entry.declaration, (summary or {}).get("value") or {})
    return ""


def _kind_sentence(entity_type: str, step: str, rows: list[dict]) -> str:
    """The headline a finding, proposal, list or table declares for itself ("4 findings · high: ...", "12 proposed · 3
    accepted", "37 tables, largest first", "20 rows × 5 columns"), or "" where the step declares none or its rows do not
    carry the declared key field (then the step's own sentence stands: an unreadable kind is not "0")."""
    from resource_explorer import declared_context, declared_words
    from resource_explorer.surveyors.analysis_catalog_reader import _load

    for entry in _load().get(entity_type, []):
        provided = getattr(entry, "provided_by", None) or {}
        if (provided.get("step") or entry.id) != step or not getattr(entry, "declaration", None):
            continue
        value = declared_context.library_rows({"measurement_rows": rows})
        return declared_words.kind_headline(entry.declaration, value)
    return ""


def headline_reader(entity_type: str, analysis_step: str, annotation_type: str) -> Callable:
    """The one-line answer for a library step: the step's own sentence, the one the Survey tab shows for the same rows
    (F21). A step with one result says it; one with a declared resource-scoped summary says that; only a step with
    several plain results and no summary says how many, by state."""
    reader = results_reader(entity_type, analysis_step, annotation_type)

    def headline(registry, slug: str) -> dict | None:
        value = reader(registry, slug)
        if not value:
            return None
        rows = value["measurement_rows"]
        summary = _declared_summary(registry, entity_type, slug, analysis_step)
        legend = _categories_legend(entity_type, analysis_step, summary)
        if summary:
            return {"label": _step_sentence(summary), "status": "info", "capped": bool(summary.get("capped")),
                    "categories": legend}
        kind = _kind_sentence(entity_type, analysis_step, rows)
        if kind:
            return {"label": kind, "status": "info", "categories": legend}
        if len(rows) == 1:
            return {"label": _step_sentence(rows[0]), "status": "info", "categories": legend}
        counts = value["state_counts"]
        order = [*STATE_WORDS.values(), NO_ENVELOPE]
        parts = [f"{counts[w]} {w}" for w in order if counts.get(w)]
        n = len(rows)
        return {"label": f"{n} results: " + " · ".join(parts), "status": "info", "categories": legend}
    return headline
