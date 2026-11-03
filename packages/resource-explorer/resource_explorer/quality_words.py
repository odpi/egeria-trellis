"""The words a person reads on a proposed quality check: a test type's short name, its parameters, and why a check could
not be established.

Three rules, from the new-user read of 2026-10-30:
 - a test type is named by TestGen's own `test_name_short` and `default_parm_prompts` (the vendored catalogue is the one
   source; nothing here is a hand-written list of test types);
 - a parameter with no value is absent, never "null": `None` is "not measured", not a setting;
 - a reason is reworded only when it matches a sentence this code knows; any other reason is returned as it was stored,
   never replaced by a guess.
"""
from __future__ import annotations

import json
import re

from resource_explorer.surveyors.database.quality.catalogue import catalogue_ref

#: Parameters that are not a setting of the check (the profile the proposal was made from has its own column).
_NOT_A_SETTING = frozenset({"profile_surveyed_at"})
#: Parameters the catalogue's `default_parm_columns` does not list but every proposal carries.
_EXTRA_LABELS = {"severity": "severity"}
#: A comma list this long is a list of values (the placeholder list), not a single value that contains a comma.
_LIST_AT_LEAST = 10

#: The profile inputs a selection rule reads, in words. Any other input falls back to its name with spaces.
_INPUT_WORDS = {
    "frequent_patterns": "the common text patterns",
    "frequent_values": "the most frequent values",
    "date_months_present": "the months that hold dates",
    "date_weeks_present": "the weeks that hold dates",
    "percentile_25/50/75": "the 25th, 50th and 75th percentiles",
    "min_date": "the earliest date",
    "max_date": "the latest date",
}


def name_of_test_type(entry) -> str:
    """TestGen's short name for a test type ("Alpha Truncation"); "" when the catalogue has no such type."""
    return (entry.name_short or "") if entry is not None else ""


def _prompts(entry) -> dict[str, str]:
    columns, prompts = entry.default_parm_columns, [p.strip() for p in entry.default_parm_prompts.split(",")]
    labels = dict(zip(columns, (p.lower() for p in prompts), strict=True)) if len(columns) == len(prompts) else {}
    return {**_EXTRA_LABELS, **labels}


def _words(key: str) -> str:
    return key.replace("_", " ")


#: Why a column is left out of a duplicate-rows comparison (general_type X, dupe_rows.NO_EQUALITY), in words.
_NOT_COMPARABLE = "its type has no equality (identifiers, binary, arrays and the like), so it cannot be compared"


def _names(value) -> list[str]:
    """Column names from a list of names or of `{"column": ...}` records."""
    return [str(v.get("column") or v.get("name") or "") if isinstance(v, dict) else str(v) for v in value or []]


def _compared(value: list, left_out: list[str]) -> dict:
    """Duplicate Rows' column list as one line: "24 columns compared (all but rowguid)", the names behind it."""
    names = _names(value)
    n = len(names)
    summary = f"{n} column{'' if n == 1 else 's'} compared"
    if left_out:
        summary += f" (all but {', '.join(left_out)})"
    title = "; ".join(f"{c} is left out: {_NOT_COMPARABLE}" for c in left_out)
    return {"key": "groupby_names", "label": "columns compared", "list": names, "summary": summary, "title": title}


def parameter_items(entry, params: dict) -> list[dict]:
    """The parameters a check carries that have a value, each `{key, label, text}` or, for a long list of values,
    `{key, label, list, summary}`. A parameter whose value is None is left out: it was not measured.

    No value is shown as JSON. Duplicate Rows' compared columns are a count with the names behind a disclosure, and the
    columns it leaves out are named in that same line ("24 columns compared (all but rowguid)"), not as records."""
    labels = _prompts(entry) if entry is not None else dict(_EXTRA_LABELS)
    standard = (entry.default_parm_values or "") if entry is not None else ""
    params = params or {}
    left_out = [n for n in _names(params.get("excluded_columns")) if n]
    out = []
    for key, value in params.items():
        if key in _NOT_A_SETTING or value is None or value == "":
            continue
        label = labels.get(key) or _words(key)
        if key == "groupby_names" and isinstance(value, list):
            out.append(_compared(value, left_out))
        elif key == "excluded_columns" and isinstance(value, list):
            if "groupby_names" not in params and left_out:     # said with the compared columns when they are there
                out.append({"key": key, "label": "left out", "text": ", ".join(left_out),
                            "title": "; ".join(f"{c} is left out: {_NOT_COMPARABLE}" for c in left_out)})
        elif isinstance(value, list):
            names = [n for n in _names(value) if n]
            out.append({"key": key, "label": label, "list": names, "summary": f"{label} · {len(names)} values"}
                       if len(names) >= _LIST_AT_LEAST else {"key": key, "label": label, "text": ", ".join(names)})
        elif isinstance(value, str) and value.count(",") >= _LIST_AT_LEAST:
            items = [i.strip() for i in value.split(",")]
            origin = "standard " if value in standard else ""
            out.append({"key": key, "label": label, "list": items, "summary": f"{origin}{label} · {len(items)} values"})
        else:
            text = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
            out.append({"key": key, "label": label, "text": text})
    return out


def _input_names(text: str) -> str:
    return ", ".join(_INPUT_WORDS.get(name.strip(), _words(name.strip())) for name in text.split(","))


_READS = re.compile(r"(?:its selection rule )?reads (?P<names>[^;]+?), which the profile did not measure")
_NEEDS = re.compile(r"needs (?P<names>[^;(]+?), which the profile does not take")


def _reads(match: re.Match) -> str:
    names = match.group("names")
    if names == "an input":
        return "needs an input that this profile did not collect (the rule does not say which)"
    return f"needs {_input_names(names)}, which this profile did not collect"


def reason_in_words(reason: str) -> str:
    """A not-established reason in a person's words: the missing input named, not the rule engine's sentence. A reason
    this code does not recognise comes back unchanged."""
    if not reason:
        return ""
    out = _READS.sub(_reads, reason)
    out = _NEEDS.sub(lambda m: f"needs {_input_names(m.group('names'))}, which this profile does not collect", out)
    out = out.replace("so its selection rule could not be evaluated", "so this check could not be evaluated")
    out = out.replace("table type not inferred", "the table's type could not be worked out").replace(
        "type not inferred", "the column's type could not be worked out")
    # The type inference's own tails ("undetermined", "statements unresolved: Constant") say nothing more to a person
    # than "could not be worked out" already does; the stored reason keeps them (the row's title).
    return _TYPE_TAIL.sub("", out)


_TYPE_TAIL = re.compile(r" · (?:undetermined|statements unresolved: [^·;]+?)(?=$| · |;)")


#: `catalogue_ref`'s shape: `vendored-catalog@<commit>:<type>:<kind>:sha256:<hash>`.
_REF = re.compile(r"^vendored-catalog@(?P<commit>[0-9a-f]+):[^:]+:[a-z_]+:sha256:[0-9a-f]{64}$")


def attribution_of(entry, origin: str, ref: str) -> str:
    """The one sentence a DETAILED description carries about where a check comes from (owner, 2026-10-30: measures may
    be said to be inspired by, or to implement, measures TestGen defines; headlines, row words and rule names do not
    say it). Decided from what is on the proposal: its `origin` and `catalogue_ref`.

    - `vendored` and the proposal's statement pin equals the vendored catalogue's: "Implements ...";
    - `re` (a check RE proposes itself), or a vendored one whose pin no longer matches: "Inspired by ...", with what differs;
    - anything else (an imported TestGen-only check, a type with no TestGen id, an unreadable pin): "", never a guess."""
    if entry is None or not getattr(entry, "id", ""):
        return ""
    ident = f"TestGen's test type {entry.id}"
    if origin == "re":
        what = ("Resource Explorer proposes it for every table that has rows; TestGen has no selection rule for it"
                if entry.selection_criteria is None else "Resource Explorer applies its own selection rule")
        return f"Inspired by {ident}; {what}"
    if origin != "vendored":
        return ""
    found = _REF.match(ref or "")
    if not found:
        return ""
    if ref == catalogue_ref(entry):
        return f"Implements TestGen test type {entry.id} (catalog commit {found.group('commit')[:8]})"
    return (f"Inspired by {ident}; this proposal's statement pin (catalog commit {found.group('commit')[:8]}) no longer "
            "matches the vendored catalog, so it is not described as TestGen's implementation")
