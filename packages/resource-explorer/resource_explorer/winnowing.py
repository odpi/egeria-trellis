"""Winnowing a work list: every headline a column, every column a filter (DESIGN-WORK-LISTS-WINNOWING-AND-MIXED-KINDS §2).

The bench evaluates a filter over the headlines it already holds (`next/winnow.js`); this module is the Python half:
what each column can be filtered BY (`column_types`, from the analysis catalog's declarations), and the check a
stored filter must pass before a shortlist keeps it (`check_filter`).

Three buckets, never two. A filter splits the rows into kept, dropped and could-not-check; a member whose answer
is absent for a criterion (never measured, not read, not established, unreadable) goes into the third bucket with
the sentence saying why. The stored filter carries all three, so a shortlist can say what it was cut from, on
what, as of when, and who could not be checked, rather than only who survived.

What a column can be filtered by:

* ``state``: the answer's state word (answered, nothing found, partial). Every column with an analysis has it.
* ``headline``: the answer's own sentence, by "contains". Every column with an analysis has it; it is the only
  text filter for a repository, whose analyses declare no measures yet.
* ``measure:<analysis_id>:<value_field>``: a declared measure, by threshold, or by age when the value is a date.
  Read with the declaration's own path rules (`publish_table.resolve`: dotted path, trailing ``[]`` = a count).
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

#: The state words a ``state`` criterion may name: the cell states that are answers. The rest of the cell states
#: (not run, not read yet, no reader, needs a person, could not read) are absences and land in could-not-check.
STATE_WORDS = ("answered", "nothing found", "partial")

#: Operators, by what the criterion reads.
STATE_OPS = ("is", "is_not")
HEADLINE_OPS = ("contains", "lacks")
NUMBER_OPS = (">=", "<=", ">", "<", "=")
DATE_OPS = ("older_than_days", "newer_than_days")
MEASURE_OPS = NUMBER_OPS + DATE_OPS

#: The keys of a stored criterion, and of a stored filter. Anything else is refused by name, so a stored filter
#: cannot carry a key no reader understands.
CRITERION_KEYS = {"question_id", "question", "analysis_ids", "analysis_ids_by_kind", "field", "op", "value",
                  "lens_dimension", "entity_type"}
FILTER_KEYS = {"criteria", "applied_at", "oldest_answer", "newest_answer", "parent_slug", "parent_count", "kept",
               "dropped", "could_not_check", "entity_type"}


def column_types(entity_type: str, analysis_ids: list[str]) -> dict:
    """What each analysis offers as a filter: its result kind, its declared measures and its absence sentences.

    An analysis with no declaration (every repository analysis today) offers state and headline only, and says so
    (`declared: False`); an id the catalog does not hold for this kind is listed in `unknown`, never dropped."""
    from resource_explorer.surveyors.analysis_catalog_reader import _load

    entries = {e.id: e for e in _load().get(entity_type, [])}
    out: dict[str, Any] = {"entity_type": entity_type, "analyses": {}, "unknown": []}
    for aid in dict.fromkeys(a for a in analysis_ids if a):
        entry = entries.get(aid)
        if entry is None:
            out["unknown"].append(aid)
            continue
        dec = entry.declaration
        if dec is None:
            out["analyses"][aid] = {"name": entry.name, "declared": False, "result_kind": "", "measures": [],
                                    "absence": {},
                                    "words": entry.declaration_refused or "no declaration: state and headline only"}
            continue
        out["analyses"][aid] = {
            "name": entry.name, "declared": True, "result_kind": dec.result_kind,
            "measures": [{"name": m.name, "unit": m.unit, "value_field": m.value_field, "as_of": m.as_of}
                         for m in dec.measures],
            "absence": dec.absence.model_dump(),
            "words": "",
        }
    return out


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _check_criterion(raw: Any, n: int) -> dict:
    where = f"criterion {n + 1}"
    if not isinstance(raw, dict):
        raise ValueError(f"{where} is a {type(raw).__name__}, not a mapping")
    stray = sorted(set(raw) - CRITERION_KEYS)
    if stray:
        raise ValueError(f"{where} has keys no reader understands: {', '.join(stray)}")
    field = str(raw.get("field") or "")
    op = str(raw.get("op") or "")
    value = raw.get("value")
    ids = raw.get("analysis_ids") or []
    if not isinstance(ids, list) or not all(isinstance(a, str) and a for a in ids):
        raise ValueError(f"{where}: analysis_ids must be a list of analysis ids")
    if not str(raw.get("question") or "").strip():
        raise ValueError(f"{where} names no question")
    # A shared column of a list of more than one kind (§5) is answered per kind by that kind's own analyses.
    by_kind = raw.get("analysis_ids_by_kind")
    if by_kind is not None and (not isinstance(by_kind, dict) or not all(
            isinstance(k, str) and isinstance(v, list) and all(isinstance(a, str) and a for a in v)
            for k, v in by_kind.items())):
        raise ValueError(f"{where}: analysis_ids_by_kind must map each kind to its analysis ids")
    if field == "state":
        if op not in STATE_OPS:
            raise ValueError(f"{where}: a state is filtered by {' or '.join(STATE_OPS)}, not {op!r}")
        if value not in STATE_WORDS:
            raise ValueError(f"{where}: {value!r} is not an answer's state ({', '.join(STATE_WORDS)})")
    elif field == "headline":
        if op not in HEADLINE_OPS:
            raise ValueError(f"{where}: a headline is filtered by {' or '.join(HEADLINE_OPS)}, not {op!r}")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{where}: a headline filter needs the words to look for")
    elif field.startswith("measure:"):
        parts = field.split(":", 2)
        if len(parts) != 3 or not parts[1] or not parts[2]:
            raise ValueError(f"{where}: a measure field is measure:<analysis id>:<value field>, not {field!r}")
        if parts[1] not in ids:
            raise ValueError(f"{where}: the measure's analysis {parts[1]!r} does not answer this question")
        if op not in MEASURE_OPS:
            raise ValueError(f"{where}: a measure is filtered by {', '.join(MEASURE_OPS)}, not {op!r}")
        if not _is_number(value) or (op in DATE_OPS and value < 0):
            raise ValueError(f"{where}: {op} needs a number{' of days, 0 or more' if op in DATE_OPS else ''}")
    else:
        raise ValueError(f"{where}: {field or '(none)'!r} is not something a column is filtered by "
                         "(state, headline, or measure:<analysis id>:<value field>)")
    out = {k: raw.get(k) for k in CRITERION_KEYS if k in raw and raw.get(k) is not None}
    out.update(field=field, op=op, analysis_ids=list(ids), lens_dimension=str(raw.get("lens_dimension") or ""))
    return out


def _check_iso(v: Any, name: str, *, required: bool) -> str:
    s = str(v or "").strip()
    if not s:
        if required:
            raise ValueError(f"the filter has no {name}")
        return ""
    try:
        datetime.fromisoformat(s)
    except ValueError:
        raise ValueError(f"the filter's {name} {s!r} is not an ISO date") from None
    return s


def check_filter(raw: Any) -> dict:
    """A filter a shortlist may store, checked; raises ValueError with the words otherwise.

    Shape: ``criteria`` (one or more, all must hold), ``applied_at`` (when the cut was made over the headlines
    then held), ``oldest_answer`` (the oldest as-of among the answers that decided it; "" when none did),
    ``parent_slug`` and ``parent_count`` (what it was cut from), and the three buckets: ``kept`` and ``dropped``
    (slugs) and ``could_not_check`` (``{entity_slug, words}``, each with its sentence, never a bare name).
    ``entity_type``, when given, says the cut was made over that kind's members only (a list of more than one
    kind, filtered with one kind's chip pressed): the other kinds were not asked, so they are in no bucket."""
    if not isinstance(raw, dict):
        raise ValueError("the filter is not a mapping")
    stray = sorted(set(raw) - FILTER_KEYS)
    if stray:
        raise ValueError(f"the filter has keys no reader understands: {', '.join(stray)}")
    criteria = raw.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        raise ValueError("the filter has no criteria: a shortlist cut on nothing is a copy, not a cut")
    checked = [_check_criterion(c, i) for i, c in enumerate(criteria)]

    def _slugs(name: str) -> list[str]:
        got = raw.get(name) or []
        if not isinstance(got, list) or not all(isinstance(s, str) and s for s in got):
            raise ValueError(f"the filter's {name} must be a list of member slugs")
        return list(dict.fromkeys(got))

    kept, dropped = _slugs("kept"), _slugs("dropped")
    unchecked = raw.get("could_not_check") or []
    if not isinstance(unchecked, list):
        raise ValueError("the filter's could_not_check must be a list")
    cnc: list[dict] = []
    for u in unchecked:
        if not isinstance(u, dict) or not u.get("entity_slug") or not str(u.get("words") or "").strip():
            raise ValueError("each could-not-check member needs its slug and the sentence saying why")
        cnc.append({"entity_slug": str(u["entity_slug"]), "words": str(u["words"]),
                    **({"entity_type": str(u["entity_type"])} if u.get("entity_type") else {})})
    buckets = [set(kept), set(dropped), {u["entity_slug"] for u in cnc}]
    overlap = (buckets[0] & buckets[1]) | (buckets[0] & buckets[2]) | (buckets[1] & buckets[2])
    if overlap:
        raise ValueError(f"a member is in two buckets: {', '.join(sorted(overlap))}")
    parent_count = raw.get("parent_count")
    if not isinstance(parent_count, int) or isinstance(parent_count, bool) or parent_count < 0:
        raise ValueError("the filter's parent_count must be a whole number")
    if len(kept) + len(dropped) + len(cnc) != parent_count:
        raise ValueError(f"the buckets hold {len(kept) + len(dropped) + len(cnc)} member(s) and the list it was "
                         f"cut from held {parent_count}: every member is kept, dropped or could not be checked")
    return {
        "criteria": checked,
        "applied_at": _check_iso(raw.get("applied_at"), "applied_at", required=True),
        "oldest_answer": _check_iso(raw.get("oldest_answer"), "oldest_answer", required=False),
        # The newest as-of among the answers read: what an automatic re-apply compares against `applied_at`.
        "newest_answer": _check_iso(raw.get("newest_answer"), "newest_answer", required=False),
        "parent_slug": str(raw.get("parent_slug") or ""),
        "parent_count": parent_count,
        "kept": kept, "dropped": dropped, "could_not_check": cnc,
        **({"entity_type": str(raw["entity_type"])} if raw.get("entity_type") else {}),
    }


_OP_WORDS = {"is": "is", "is_not": "is not", "contains": "contains", "lacks": "does not contain",
             ">=": "at least", "<=": "at most", ">": "more than", "<": "less than", "=": "exactly",
             "older_than_days": "older than", "newer_than_days": "newer than"}


def criterion_words(c: dict) -> str:
    """One criterion in words: `Q-0011 · headline contains “orders”`. Same wording as `next/winnow.js`."""
    field = c.get("field") or ""
    what = "state" if field == "state" else "headline" if field == "headline" else field.split(":", 2)[-1]
    op = _OP_WORDS.get(c.get("op") or "", c.get("op") or "")
    value = c.get("value")
    shown = f"“{value}”" if field == "headline" else f"{value} days" if c.get("op") in DATE_OPS else f"{value}"
    return f"{c.get('question_id') or c.get('question') or '?'} · {what} {op} {shown}"


def filter_words(f: dict) -> str:
    """`kept 7 of 23 on <criteria> as of <date>` — what a shortlist says when it is reopened."""
    crit = " and ".join(criterion_words(c) for c in f.get("criteria") or [])
    unchecked = len(f.get("could_not_check") or [])
    tail = f" · {unchecked} could not be checked" if unchecked else ""
    of = f"{f.get('parent_count', 0)}" + (f" {f['entity_type']}" if f.get("entity_type") else "")
    return (f"kept {len(f.get('kept') or [])} of {of} on {crit} "
            f"as of {str(f.get('applied_at') or '')[:10]}{tail}")
