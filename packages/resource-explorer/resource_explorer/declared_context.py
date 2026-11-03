"""A declared analysis's section of a compiled chat context, built from its declaration and nothing else
(DESIGN-DECLARED-EXTENSIBILITY-RENDERING-CHAT-CONFIDENCE.md §3, slice 2).

`context_compile.compile_context` routes a question to analyses and packs what they hold. For an analysis whose
catalogue entry carries a declaration (`trellis_analytics.declaration.Declaration`), the section is built here:

- **Step 2, extents by declaration.** The measures as `name: value unit (as of ...)`, then each declared extent: its
  declared fields only, at most its declared rows, in the declared `sort` order (or the result's own order where
  the declaration states it in words), with the truncation sentence filled in when rows were left out. Nothing is invented: a field the result
  does not carry is said as `(none)`, an extent whose rows are not in the result is said to be absent from it.
- **Step 3, absences in words.** An analysis that has not run or could not run enters the context as its declared
  absence sentence (in the instructions section, `absence_lines`), and one that ran and found nothing carries its
  `measured_nothing` sentence under its measures, so the model cannot read "never measured" as "nothing there".
- **Step 4, omissions said.** A declared section the budget or the section cap left out is named in the context
  with its declared token cost (`omission_line`), so an answer never pretends to have seen what was dropped.
- **Confidence (§4).** Each section says its form and basis on its own line, where the numbers are; the compile's
  manifest names the weakest form any packed section rests on, the "weakest link" an answer reports.

What the model saw is returned beside the text (`seen`): per extent, how many rows of how many, the as-of, and the
identity of each row shown, so the stored compile names exactly what the answer rested on (§3 step 5).
"""
from __future__ import annotations

import logging

from trellis_analytics.declaration import CONFIDENCE_STRENGTH
from trellis_artifact_tree.model import Rung

from resource_explorer import declared_words
from resource_explorer.publish_table import (
    _ABSENCE_OF,
    _MISSING,
    confidence_words,
    filled_unit,
    resolve,
    run_fields,
)
from resource_explorer.surveyors.annotation_readers import STATE_WORDS as _STATE_WORDS

log = logging.getLogger(__name__)

#: The fact states that carry a value (`facts.Fact.is_known`).
KNOWN_STATES = ("measured", "nothing_found", "partial", "measured_within_credential_scope")

#: Rows an extent shows at the SUMMARY rung, whatever it declares for FULL.
SUMMARY_ROWS = 3

#: About how many tokens of rows one table result may quote in one answer (Architect ruling, 2026-11-02, "about
#: 2,000"). The extent's own token_cost is the cost of quoting every row; it is never spent.
TABLE_QUOTE_TOKENS = 2000

#: The fewest rows a table result quotes, whatever a row costs.
TABLE_QUOTE_MIN_ROWS = 5

#: Said after the quoted rows when the table holds more.
TABLE_REST_WORDS = "the table lists the rest"

#: §4's forms from strongest to weakest, for "the weakest link" (the declaration's own ordering).
FORM_STRENGTH = CONFIDENCE_STRENGTH

#: The absence of an analysis declared before it has a runner (`Declaration.built` false): "planned, not measured"
#: (project owner, 2026-10-28), ahead of its declared not-measured sentence.
NOT_BUILT = "not_built"
NOT_BUILT_WORDS = "Planned, not built yet: not measured."

#: A cell's characters at most: a value that is a whole document must not take the section.
CELL_CHARS = 80


def declarations(resource_type: str) -> dict:
    """{analysis id: Declaration} for every catalogue entry of this type that declares one."""
    from resource_explorer.surveyors.analysis_catalog_reader import _load

    return {e.id: e.declaration for e in _load().get(resource_type, []) if e.declaration is not None}


def absence_key(state: str) -> str:
    """The declared absence that says a fact layer state (the same map the publish table uses)."""
    return _ABSENCE_OF.get(state, "not_measured")


def _cell(v) -> str:
    if v is None or v is _MISSING:
        return "(none)"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        v = round(v, 4)
    if isinstance(v, (list, tuple)):
        return f"{len(v)} item(s)" if v else "(empty list)"
    if isinstance(v, dict):
        return f"{len(v)} field(s)" if v else "(empty mapping)"
    text = " ".join(str(v).split())
    if not text:
        return "(empty)"
    return text if len(text) <= CELL_CHARS else text[: CELL_CHARS - 1].rstrip() + "…"


def _field(row, path: str):
    """A declared field of one row: a dotted path reads a nested key; a key that is the whole path is read first."""
    if not isinstance(row, dict):
        return _MISSING
    if path in row:
        return row[path]
    got = row
    for part in path.split("."):
        if not isinstance(got, dict) or part not in got:
            return _MISSING
        got = got[part]
    return got


def library_rows(value: dict) -> dict:
    """A library step's stored result as the step declared it.

    Every library step is read by the one shared reader (`annotation_readers.results_reader`), which wraps the
    step's own record as `{"measurement_rows": [{"name": <scope>, "properties": {...}}]}`. A step's author declares
    the record they wrote ("scope", "Writes Per Day"), not RE's wrapping of it, so the rows are unwrapped here once,
    for every step, rather than each declaration learning RE's reader."""
    rows = value.get("measurement_rows") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        return value
    flat = [{"scope": r.get("name"), **(r.get("properties") or {})} for r in rows if isinstance(r, dict)]
    return {**{k: v for k, v in value.items() if k != "measurement_rows"}, "measurement_rows": flat}


def library_steps(resource_type: str) -> dict:
    """{analysis id: library step key} for every catalogue entry of this type a library step provides."""
    from resource_explorer.surveyors.analysis_catalog_reader import _load

    return {e.id: (e.provided_by or {}).get("step") or e.id for e in _load().get(resource_type, []) if e.provided_by}


def measure_records(registry, entity_type: str, slug: str, step: str, dec, analysis_id: str) -> dict:
    """{measure name: its record} for a resource-scoped library step, read from where the host stores each measure.

    The host (`survey_step_host`) stores a step's own measure under the step key and each further declared measure
    under `<step>:<measure>` (`measure_step_key`), so the step's fact holds its own measure only; each measure is read
    here from its own key, as `walk_derived.derived_view` reads it. Each record is {"value": {"scope", **the
    annotation's resourceProperties}, "state", "reason", "run"}; a measure with nothing stored has no record. Empty
    when there is no step. A measure is read here only when its own scope is the resource (`Declaration.scope_of`): a
    member- or field-scoped measure's rows are members, not measures, but a resource-scoped summary beside them is
    read from its own key all the same."""
    if not step:
        return {}
    from resource_explorer.surveyors.annotation_readers import _envelope_state
    from resource_explorer.surveyors.survey_step_host import measure_step_key

    names = [m.name for m in dec.measures]
    # The step's own measure: the one named after the step (or the analysis), else the first declared.
    primary = next((n for n in (step, analysis_id) if n in names), names[0] if names else "")
    out: dict = {}
    for name in names:
        if (dec.scope_of(dec.measure(name)) or "resource") != "resource":
            continue
        key = step if name == primary else measure_step_key(step, name)
        run, rows = registry.query_step_annotations(entity_type, slug, key)
        if not rows:
            continue
        a = rows[0]
        detail = a.get("detail") or {}
        state, _words, reason = _envelope_state(detail)
        out[name] = {"value": {"scope": a.get("scope") or "", **(detail.get("resourceProperties") or {})},
                     "state": state, "reason": reason, "run": run, "measured_at": a.get("measured_at") or "",
                     "summary": a.get("summary") or "",
                     # Which stored row this is: the key a stored answer names it by (no integer id is selected).
                     "row_id": f"step_annotations:{entity_type}/{slug}/{key}/{run}/{a.get('scope') or ''}"}
    return out


def _differing_part(path: str, paths: list[str]) -> str:
    """Which source a joined row came from: the first part of its path the other sources do not share
    ("columns_added" of columns_added/columns_dropped; "naming_convention" of checks.naming_convention.items)."""
    parts = path.split(".")
    others = [p.split(".") for p in paths if p != path]
    for i, part in enumerate(parts):
        if any(len(o) <= i or o[i] != part for o in others):
            return part
    return parts[-1]


def extent_rows(value: dict, extent) -> list | None:
    """The rows of one extent in a result, in the result's order, or None when the result does not carry them.

    `source` names where they are (`ExtentDeclaration.source`), each path one of: a list, each item a row; a mapping
    of mappings, one row per entry with its key in the extent's first field (`by_schema` keyed by schema); any other
    mapping, one row; "." the result itself as one row. Without `source`, the extent's name is the path. Several
    paths are joined in order; `tag` then carries which one a row came from, and an item that is a bare value (a
    qualified name in `columns_added`) goes in the first field that is not the tag.
    """
    paths = list(getattr(extent, "source", None) or []) or [extent.name]
    if not getattr(extent, "source", None) and _field(value, extent.name) is _MISSING and \
            isinstance(value.get("measurement_rows"), list):
        # A library step's extent is its own records (`library_rows`): a step declares no RE path.
        paths = ["measurement_rows"]
    tag = getattr(extent, "tag", "") or ""
    fields = list(extent.fields)
    bare_field = next((f for f in fields if f != tag), "value")
    rows, found = [], False
    for path in paths:
        got = value if path == "." else _field(value, path)
        if got is _MISSING or got is None:
            continue
        found = True
        if isinstance(got, (list, tuple)):
            items = list(got)
        elif path != "." and isinstance(got, dict) and got and all(isinstance(v, dict) for v in got.values()):
            items = [{fields[0]: k, **v} for k, v in got.items()]
        else:
            items = [got]
        for item in items:
            if not isinstance(item, dict):
                item = {bare_field: item}
            if tag:
                item = {tag: _differing_part(path, paths), **item}
            rows.append(item)
    return rows if found else None


def _sort_value(v):
    """A sort key for one cell: numbers, and numbers written as text (a library step's properties are strings), by
    value; anything else by its words. Booleans count as 0/1."""
    if isinstance(v, bool):
        return (0, float(v))
    if isinstance(v, (int, float)):
        return (0, float(v))
    try:
        return (0, float(str(v).replace(",", "")))
    except ValueError:
        return (1, " ".join(str(v).split()).lower())


def sorted_rows(rows: list, extent) -> list:
    """The rows in the extent's declared `sort` order (stable; first key first), or as given with no sort. A row
    missing a key's field goes last whatever the direction: an absent value is not the smallest or the largest."""
    keys = extent.sort_keys() if hasattr(extent, "sort_keys") else []
    out = list(rows)
    for field, descending in reversed(keys):
        present = [r for r in out if _field(r, field) not in (_MISSING, None)]
        absent = [r for r in out if _field(r, field) in (_MISSING, None)]
        present.sort(key=lambda r, f=field: _sort_value(_field(r, f)), reverse=descending)
        out = present + absent
    return out


def _row_line(row, fields: list[str], joined: bool = False) -> str:
    """One row, its declared fields in declared order. A field the row lacks is said as `(none)`, except in rows
    joined from several sources, where a field belongs to another source's rows and is left out rather than
    reported missing (an extension has no foreign-server name)."""
    if not isinstance(row, dict):
        return f"- {_cell(row)}"
    cells = [(f, _field(row, f)) for f in fields]
    if joined:
        cells = [(f, v) for f, v in cells if v is not _MISSING]
    return "- " + " · ".join(f"{f}: {_cell(v)}" for f, v in cells)


#: How many leading declared fields name a row for "open what this answer saw" ("public · orders").
IDENTITY_FIELDS = 2


def _row_identity(row, fields: list[str]) -> str:
    """What names a row for "open what this answer saw": its leading declared fields' values."""
    if not isinstance(row, dict):
        return _cell(row)
    return " · ".join(_cell(_field(row, f)) for f in fields[:IDENTITY_FIELDS])


def _measure_lines(dec, value: dict, run: dict, records: dict | None = None) -> list[str]:
    """One line per declared measure. `records` ({measure: record}, `measure_records`) is where a resource-scoped
    library step keeps each measure's value; a measure with a record is read from it, and one whose record states
    no value says the record's state and reason rather than "not in the stored result"."""
    lines = []
    records = records or {}
    for m in dec.measures:
        record = records.get(m.name)
        source = record["value"] if record else value
        v = resolve(source, m.value_field, run)
        as_of = resolve(source, m.as_of, run)
        when = f" (as of {as_of})" if as_of is not _MISSING else " (as-of not recorded)"
        own = f" · {m.confidence.form}" if m.confidence is not None else ""
        if record and record["state"] == "nothing_found" and record.get("summary"):
            # The step declared this sentence for a nothing-found result: quote it, never the 0 its row stores.
            lines.append(f"- {m.name}: {record['summary']}{when}")
        elif v is _MISSING and record and record["state"] in _STATE_WORDS and record["state"] != "measured":
            reason = f": {record['reason']}" if record["reason"] else ""
            lines.append(f"- {m.name}: {_STATE_WORDS[record['state']]}{reason}{when}")
        elif v is _MISSING and dec.scope_of(m) in ("member", "field") and dec.extents:
            # A member-scoped measure has one value per member, not one for the resource: its values are the rows.
            lines.append(f"- {m.name}: one value per {dec.scope_of(m)}, in {filled_unit(m.unit, source)}, in the rows below")
        elif v is _MISSING:
            lines.append(f"- {m.name}: not in the stored result{when}")
        else:
            # A measure that declares a basis or a cap says it in the declared words: "456 of 1,236 columns", "at least
            # 500 rows · not derived: the count is a lower bound" (declared_words), never a bare number.
            said = (declared_words.measure_sentence(m, source, run, allow_of=declared_words.allows_of(dec))[0]
                    if (m.basis or m.capped_field) else "")
            lines.append(f"- {m.name}: {said or f'{_cell(v)} {filled_unit(m.unit, source)}'}{when}{own}")
    return lines


def _confidence_line(dec) -> str:
    words = confidence_words(dec.confidence)
    if not words.get("form"):
        return ""
    line = f"confidence: {words['words']}"
    if any(m.confidence is not None for m in dec.measures):
        line += " (the weakest of its measures)"
    if words.get("form") == "inferred":
        line += " · score calibrated" if words.get("calibrated") else " · score is a rule count, not a probability"
    return line


def _extent_block(extent, rows: list | None, n: int, as_of: str, scope: str, analysis_id: str,
                  cite: bool, total: int | None = None) -> tuple[list[str], dict]:
    """One extent at `n` rows: its lines, and what was shown (for the manifest). `total`, when given, is how many rows
    exist beyond the ones passed (a table result's stored rows, of which only the shown slice is read)."""
    if rows is None:
        return ([f"{extent.name}: not in the stored result"],
                {"total": None, "shown": 0, "as_of": as_of, "rows": [], "token_cost": extent.token_cost})
    rows = sorted_rows(rows, extent)
    total = len(rows) if total is None else max(total, len(rows))
    shown = rows[:n]
    lines = [f"{extent.name} ({extent.order_words()}):"]
    joined = len(getattr(extent, "source", None) or []) > 1
    lines += [_row_line(r, extent.fields, joined) for r in shown] or ["- (no rows)"]
    if total > len(shown):
        if getattr(extent, "token_cost_per_row", 0):
            # A table result: the extent's truncation is the screen's sentence (what the table SHOWS); an answer says
            # what it QUOTED of what is stored.
            lines.append(f"{len(shown):,} of {total:,} rows quoted; {TABLE_REST_WORDS}")
        else:
            lines.append(extent.truncation.replace("{shown}", str(len(shown))).replace("{total}", str(total)))
    if cite:
        lines.append("cite as: " + extent.citation.replace("{analysis}", analysis_id).replace("{scope}", scope)
                     .replace("{as_of}", as_of or "as-of not recorded"))
    return lines, {"total": total, "shown": len(shown), "as_of": as_of,
                   "rows": [_row_identity(r, extent.fields) for r in shown], "token_cost": extent.token_cost}


def absence(dec, state: str) -> dict:
    """§3 step 3: the declared sentence for an analysis with nothing stored, keyed by the fact layer's state. One not
    built yet says so first, whatever its state: planned is not the same as never run."""
    if not dec.built:
        return {"state": state, "absence": NOT_BUILT,
                "sentence": f"{NOT_BUILT_WORDS} {dec.absence.sentence('not_measured')}"}
    key = absence_key(state)
    return {"state": state, "absence": key, "sentence": dec.absence.sentence(key)}


#: Absence sentences the instructions carry at most; the rest are named, so none is silently dropped.
MAX_ABSENCE_SENTENCES = 6


def absence_lines(absences: dict[str, dict]) -> tuple[str, str]:
    """The absences block for the instructions section, at FULL and at SUMMARY.

    Absences ride inside the instructions, as the coverage line does, rather than as sections of their own: a
    section is a stored result used, and the rail counts `packed` as exactly that. The barest rung carries none.
    """
    if not absences:
        return "", ""
    items = list(absences.items())
    shown, rest = items[:MAX_ABSENCE_SENTENCES], items[MAX_ABSENCE_SENTENCES:]
    full = ["Not in the stored results, in each analysis's own words:"]
    full += [f"- {aid}: {a['sentence']}" for aid, a in shown]
    if rest:
        full.append("- also nothing stored for: " + ", ".join(aid for aid, _ in rest))
    summary = "Nothing stored for: " + ", ".join(f"{aid} ({a['absence'].replace('_', ' ')})" for aid, a in items)
    return "\n".join(full) + "\n", summary + "\n"


def table_quote_rows(extent) -> int:
    """How many rows of a table result an answer quotes: about `TABLE_QUOTE_TOKENS` worth at the extent's price per
    row (about `TOKENS_PER_CELL` a cell), in the declared order, never fewer than `TABLE_QUOTE_MIN_ROWS`, never more
    than the extent shows."""
    per_row = getattr(extent, "token_cost_per_row", 0)
    if not per_row:
        return extent.rows
    return min(extent.rows, max(TABLE_QUOTE_MIN_ROWS, TABLE_QUOTE_TOKENS // per_row))


def section_rungs(analysis_id: str, dec, fact: dict, records: dict | None = None, *,
                  table: dict | None = None) -> tuple[dict, dict]:
    """The rungs of one declared analysis's section, and what each rung shows.

    `fact` is `Fact.as_dict()` for a known fact (`KNOWN_STATES`): the measures, the confidence line and the extents
    (FULL at the declared rows, SUMMARY at `SUMMARY_ROWS`, IDENTIFIERS the measures alone). Returns (rungs, seen)
    where seen is {"state", "as_of", "confidence", "extents": {name: {"FULL": ..., "SUMMARY": ...}}}. `records` is
    `measure_records` for a resource-scoped library step: where each of its measures is stored. `table` is
    `table_result.table_rows` for a table result (B1): its extent's rows are those stored rows, in the declared order,
    and the FULL rung quotes about `TABLE_QUOTE_TOKENS` of them (`table_quote_rows`), then "N of M rows quoted; the
    table lists the rest".
    """
    state = fact.get("state", "")
    head = f"## {analysis_id}"
    conf = _confidence_line(dec)
    seen: dict = {"state": state, "confidence": (dec.confidence.form if dec.confidence else ""), "extents": {}}
    value = fact.get("value") or {}
    value = library_rows(value) if isinstance(value, dict) else {}
    run = run_fields(fact)
    as_of = str(fact.get("as_of") or run.get("read_snapshot") or run.get("surveyed_at") or "")
    scope = dec.scope or "resource"
    seen["as_of"] = as_of
    measures = _measure_lines(dec, value, run, records)
    lead = [head] + measures + ([dec.absence.sentence("measured_nothing")] if state == "nothing_found" else [])
    cats = declared_words.categories_line(dec, value, run)
    if cats:
        lead.append(cats)
    kind_line = declared_words.kind_headline(dec, value, run) if state in KNOWN_STATES else ""
    if kind_line:
        lead.append(f"headline: {kind_line}")
    if conf:
        lead.append(conf)

    full, summary = list(lead), list(lead)
    for extent in dec.extents:
        total = None
        if table is not None:
            rows = table["rows"] if table.get("stored") else None
            total = table.get("total")
        else:
            rows = extent_rows(value, extent)
        n_full = table_quote_rows(extent) if table is not None else extent.rows
        f_lines, f_seen = _extent_block(extent, rows, n_full, as_of, scope, analysis_id, cite=True, total=total)
        s_lines, s_seen = _extent_block(extent, rows, min(extent.rows, SUMMARY_ROWS), as_of, scope, analysis_id,
                                        cite=False, total=total)
        full += f_lines
        summary += s_lines
        seen["extents"][extent.name] = {"FULL": f_seen, "SUMMARY": s_seen}
    rungs = {Rung.FULL: "\n".join(full), Rung.IDENTIFIERS: "\n".join([head] + measures)}
    summary_text = "\n".join(summary)
    # Same rule as the generic rungs: a middle rung that saves nothing buys the packer no choice.
    if summary_text != rungs[Rung.FULL]:
        rungs[Rung.SUMMARY] = summary_text
    return rungs, seen


def list_extents(seen: dict) -> dict:
    """The manifest's `lists` shape ({field: {"total", "shown": {"FULL", "SUMMARY"}}}) for a declared section, so
    the rail reads declared extents as it reads every other section's lists."""
    out = {}
    for name, by_rung in (seen.get("extents") or {}).items():
        total = by_rung["FULL"]["total"]
        if total is None:
            continue
        out[name] = {"total": total, "shown": {"FULL": by_rung["FULL"]["shown"],
                                               "SUMMARY": by_rung["SUMMARY"]["shown"]}}
    return out


def weakest(forms: list[str]) -> str:
    """The weakest of the declared confidence forms a compile packed (§4: "reports the weakest link, not an
    average"); "" when none declares one."""
    ranked = [f for f in forms if f in FORM_STRENGTH]
    return max(ranked, key=FORM_STRENGTH.index) if ranked else ""


#: Omissions the context names one per line at most; the rest are counted in one line.
MAX_OMISSION_LINES = 6


def omission_block(omitted: list[tuple[str, object, str]]) -> str:
    """The context's "left out" block for (analysis id, declaration, reason) triples, or "" for none."""
    if not omitted:
        return ""
    lines = [omission_line(k, dec, why) for k, dec, why in omitted[:MAX_OMISSION_LINES]]
    rest = omitted[MAX_OMISSION_LINES:]
    if rest:
        cost = sum(e.token_cost for _, dec, _ in rest for e in dec.extents)
        lines.append(f"- and {len(rest)} more (about {cost:,} tokens): " + ", ".join(k for k, _, _ in rest))
    return "\n\nLeft out of this context (open them to see):\n" + "\n".join(lines)


def omission_line(analysis_id: str, dec, reason: str) -> str:
    """§3 step 4: a declared section left out of the budget, said in the context with its declared cost."""
    cost = sum(e.token_cost for e in dec.extents)
    names = ", ".join(e.name for e in dec.extents) or "measures"
    return f"- {analysis_id} ({names}, about {cost:,} tokens): {reason}"
