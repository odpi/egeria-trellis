"""The database Value profile screen: what the latest value-profile batch measured, per table and per column, in words.

Design: docs/design-notes/DESIGN-VALUE-PROFILE-SCREEN-2026-10-30.md. Everything here reads `_value_profile_results` (the one
source; it is the latest batch only), plus the frequent values of that same batch and the latest proposals. Nothing is
written.

Four facts look alike as an empty cell, and each has its own words here (design §4):

1. the column was not profiled (state other than measured): the row is one sentence, no cells;
2. the measure is not one this type has (a length on a number): a muted "—" titled "not a measure of a <type> column",
   decided from the general type, never from the NULL alone;
3. the measure applies but was not taken: short absence words ("not asked for", "not read", "not taken") with the
   reason as title;
4. measured and zero: the number 0, plain.

The words are made here rather than in the page so the screen, the container reader (one row per table) and the tests
share one vocabulary. `cells` values are `{"text", "title", "muted"}`; the page escapes and draws them.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode

#: TestGen's general type letter, as words.
TYPE_WORDS = {"A": "text", "N": "number", "D": "date", "B": "boolean", "T": "time", "X": "other"}
STATE_WORDS = {"measured": "measured", "not_measured": "not measured", "not_permitted": "not permitted",
               "not_applicable": "not applicable"}
#: Header order, and the order not-measured tables are listed in after the measured ones.
STATE_ORDER = ("measured", "not_measured", "not_permitted", "not_applicable")
TOP_VALUES = 3
#: "a <type> column" with its article, for titles ("not a measure of a number column").
TYPE_PHRASE = {"A": "a text column", "N": "a number column", "D": "a date column", "B": "a boolean column",
               "T": "a time column", "X": "a column of another type"}
#: What each general type holds, in words: the Type cell's title (never TestGen's letter).
TYPE_MEANING = {"A": "text: character values", "N": "number: integer and decimal values",
                "D": "date: dates and timestamps", "B": "boolean: true or false", "T": "time: times of day",
                "X": "other: identifiers, binary, arrays and other types the profile does not measure by kind"}
#: Why a column was not read, by the opening of its reason: what the table is (the not-a-table reasons).
KIND_BY_REASON = (("a view,", "view"), ("a materialized view,", "materialized view"),
                  ("a foreign table,", "foreign table"))


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def type_word(general_type: str | None) -> str:
    return TYPE_WORDS.get(general_type or "X", "other")


def state_word(state: str | None) -> str:
    return STATE_WORDS.get(state or "", str(state or "state not recorded").replace("_", " "))


def when_words(iso: str | None) -> str:
    """House date format, "2026-10-30 11:12", from a stored ISO timestamp."""
    return str(iso or "").replace("T", " ")[:16]


def _cell(text: str, title: str = "", muted: bool = False) -> dict:
    return {"text": text, "title": title, "muted": muted}


def _not_a_measure(gt: str | None) -> dict:
    return _cell("—", f"not a measure of {TYPE_PHRASE.get(gt or 'X', TYPE_PHRASE['X'])}", muted=True)


def _number(v: Any) -> str:
    """A measured number in words: grouped, at most two decimals, no trailing zeros."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f == int(f) and abs(f) < 1e15:
        return f"{int(f):,}"
    return f"{f:,.2f}".rstrip("0").rstrip(".")


def _day(text: Any) -> str:
    """A stored date measure as a date; the time only when it is not midnight."""
    s = str(text or "").replace("T", " ")
    return s[:10] if s[11:19] in ("", "00:00:00") else s[:16]


# ── the column cells ──────────────────────────────────────────────────────────────────────────────────────────────


def filled_cell(c: dict) -> dict:
    rec, val, nulls = c.get("record_ct"), c.get("value_ct"), c.get("null_value_ct")
    if rec is None or val is None:
        return _cell("not counted", "the row or value count was not taken for this column", muted=True)
    if rec == 0:
        return _cell("0 of 0", "an empty table: there are no values to fill")
    pct = val / rec * 100
    share = ("under 1%" if 0 < pct < 1 else "over 99%" if 99 < pct < 100 else f"{round(pct)}%")
    text = f"{_number(val)} of {_number(rec)} · {share}"
    if nulls is not None and nulls != rec - val:
        return _cell(f"{text} · {plural(nulls, 'null')}",
                     "the filled and null counts do not add up to the row count, so both are shown")
    return _cell(text)


def distinct_cell(c: dict) -> dict:
    d, val = c.get("distinct_value_ct"), c.get("value_ct")
    if d is None and c.get("general_type") not in ("A", "N", "D", "B"):
        return _not_a_measure(c.get("general_type"))
    if d is None:
        return _cell("not counted", "the distinct count was not taken for this column", muted=True)
    if d == 1:
        return _cell("1 · one value")
    if val and d == val:
        return _cell(f"{_number(d)} · all distinct")
    return _cell(_number(d))


def range_cell(c: dict) -> dict:
    gt = c.get("general_type")
    if gt not in ("D", "N", "A", "B"):
        return _not_a_measure(gt)
    if not c.get("value_ct"):
        # Measured, and nothing to range over: an empty table or a column of nulls (case 4, not an absence).
        return _cell("no values", "every row is null, or the table is empty", muted=True)
    not_taken = _cell("not taken", f"the {type_word(gt)} range was not taken on this run", muted=True)
    if gt == "D":
        lo, hi = c.get("min_date"), c.get("max_date")
        return not_taken if lo is None or hi is None else _cell(f"{_day(lo)} → {_day(hi)}")
    if gt == "N":
        lo, hi = c.get("min_value"), c.get("max_value")
        return not_taken if lo is None or hi is None else _cell(f"{_number(lo)} → {_number(hi)}")
    if gt == "A":
        lo, hi, avg = c.get("min_length"), c.get("max_length"), c.get("avg_length")
        if lo is None or hi is None:
            return not_taken
        tail = f" (avg {_number(round(float(avg), 1))})" if avg is not None else ""
        return _cell(f"lengths {_number(lo)} → {_number(hi)}{tail}")
    true_ct = c.get("boolean_true_ct")
    if true_ct is None:
        return not_taken
    return _cell(f"true {_number(true_ct)} · false {_number(c['value_ct'] - true_ct)}")


def _value_words(v: Any) -> str:
    return "(empty)" if v == "" else str(v)


def common_cell(c: dict, values: list[dict]) -> dict:
    """The most common values: the top three as "value ×count", the rest behind "+N more" (`values` carries them all).
    Only short text columns are read (frequent_values.eligible), so every other case says which absence it is."""
    from resource_explorer.surveyors.database.quality import frequent_values

    gt = c.get("general_type")
    if gt != "A":
        return _cell("—", "most common values are read for text columns only", muted=True)
    st = c.get("frequency_state")
    if st == frequent_values.NOT_MEASURED:
        return _cell("not read", "asked for, but could not be read inside its time budget", muted=True)
    if st is None:
        if frequent_values.eligible({**c, "state": "measured"}):
            return _cell("not asked for", "most common values were not collected on this run", muted=True)
        return _cell("not collected", (f"read only for text columns with 2 to {frequent_values.MAX_DISTINCT} distinct "
                                       f"values of at most {frequent_values.MAX_LENGTH} characters"), muted=True)
    if not values:
        return _cell("only blank values", "the read found no value other than blanks", muted=True)
    shown = [v for v in values if v.get("value") is not None]
    other = next((v for v in values if v.get("value") is None), None)
    more = max(len(shown) - TOP_VALUES, 0) + int((other or {}).get("other_distinct_ct") or 0)
    text = " · ".join(f"{_value_words(v['value'])} ×{_number(v['value_ct'])}" for v in shown[:TOP_VALUES])
    cell = _cell(text + (f" · +{more:,} more" if more else ""))
    cell["top"], cell["more"] = text, more
    cell["values"] = [{"value": _value_words(v["value"]), "count": _number(v["value_ct"])} for v in shown]
    if other:
        cell["values"].append({"value": f"{plural(int(other.get('other_distinct_ct') or 0), 'other value')}",
                               "count": _number(other.get("value_ct") or 0)})
    return cell


def flags_cell(c: dict, run_year: int | None) -> dict:
    """Only the non-zero counts, as short words. Silence when all are 0: on an empty table the zeros are not flags."""
    gt = c.get("general_type")
    words = []
    if gt == "D":
        if c.get("future_date_ct"):
            words.append(plural(c["future_date_ct"], "future date"))
        if c.get("before_20yr_date_ct"):
            words.append(f"{_number(c['before_20yr_date_ct'])} before {run_year - 20}" if run_year
                         else plural(c["before_20yr_date_ct"], "date over 20 years old", "dates over 20 years old"))
    if gt == "A" and c.get("zero_length_ct"):
        words.append(plural(c["zero_length_ct"], "empty string"))
    return _cell(" · ".join(words))


def checks_cell(counts: dict[str, int]) -> dict:
    order = ("proposed", "accepted", "rejected", "suppressed", "not_established")
    words = [f"{counts[s]:,} {s.replace('_', ' ')}" for s in order if counts.get(s)]
    words += [f"{n:,} {s.replace('_', ' ')}" for s, n in sorted(counts.items()) if s not in order and n]
    if not words:
        return _cell("—", "no checks proposed for this column", muted=True)
    return _cell(" · ".join(words), "the proposals are on Curate › Structure")


# ── the screen's reads ─────────────────────────────────────────────────────────────────────────────────────────────


def _profile(registry, slug: str) -> dict:
    from resource_explorer.surveyors.database.survey_definition_adapter import _value_profile_results

    return _value_profile_results(registry, slug)


def _tables(columns: list[dict]) -> list[dict]:
    """The batch's columns grouped per table, in schema order, each with its state counts, type counts and record_ct."""
    out: dict[tuple[str, str], dict] = {}
    for c in columns:
        t = out.setdefault((c["schema_name"], c["table_name"]), {
            "schema_name": c["schema_name"], "table_name": c["table_name"], "column_count": 0,
            "state_counts": {}, "type_counts": {}, "record_ct": None, "reasons": {}})
        t["column_count"] += 1
        st = c.get("state") or ""
        t["state_counts"][st] = t["state_counts"].get(st, 0) + 1
        if st == "measured":
            tw = type_word(c.get("general_type"))
            t["type_counts"][tw] = t["type_counts"].get(tw, 0) + 1
            if t["record_ct"] is None and c.get("record_ct") is not None:
                t["record_ct"] = c["record_ct"]
        elif c.get("reason"):
            t["reasons"][c["reason"]] = t["reasons"].get(c["reason"], 0) + 1
    return sorted(out.values(), key=lambda t: (t["schema_name"], t["table_name"]))


def _table_state(t: dict) -> str:
    if t["state_counts"].get("measured"):
        return "measured"
    return max(t["state_counts"], key=lambda s: t["state_counts"][s]) if t["state_counts"] else ""


def _table_reason(t: dict) -> str:
    return max(t["reasons"], key=lambda r: t["reasons"][r]) if t["reasons"] else ""


def table_kind(t: dict) -> str:
    """"table", or what else it is (view, materialized view, foreign table) when that is why it was not read."""
    reason = _table_reason(t)
    return next((kind for lead, kind in KIND_BY_REASON if reason.startswith(lead)), "table")


def rows_words(rec: int | None) -> str:
    return "rows not counted" if rec is None else ("0 rows · empty table" if rec == 0 else plural(rec, "row"))


def table_line(t: dict, surveyed_at: str) -> str:
    """The card's line after the table's name."""
    name = f"{t['schema_name']}.{t['table_name']}"
    state = _table_state(t)
    if state != "measured":
        reason = _table_reason(t)
        return f"{name} · {state_word(state)}" + (f" · {reason}" if reason else "")
    measured, total = t["state_counts"]["measured"], t["column_count"]
    rows = rows_words(t["record_ct"])
    cols = plural(measured, "column") + " measured" if measured == total else f"{measured:,} of {plural(total, 'column')} measured"
    return f"{name} · {rows} · {cols} · profiled {when_words(surveyed_at)[11:]}"


_PERMISSION = re.compile(r"permission denied for (schema|table|relation|view|sequence) \"?([\w.$-]+)\"?", re.I)
#: A database's own error text ("LINE 429: FROM ... ^", "ERROR:", a caret line): not words for a headline.
_RAW_ERROR = re.compile(r"\bLINE \d+:|\bERROR:|\^|\n")


def reason_class(reason: str) -> str:
    """The class of a not-measured reason, in words for a header (W52): "permission denied (schema demo)", "relation not
    found", "query error". The database's raw error text stays on the table's own line, behind the numbers; a reason in
    RE's own words is already a class and is returned as it is."""
    m = _PERMISSION.search(reason)
    if m:
        return f"permission denied ({m.group(1).lower()} {m.group(2)})"
    if re.search(r"permission denied", reason, re.I):
        return "permission denied"
    if re.search(r"\bdoes not exist\b|\brelation .* not found\b|\bUndefinedTable\b", reason, re.I):
        return "relation not found"
    if _RAW_ERROR.search(reason) or len(reason) > 120:
        return "query error"
    return reason


def _reason_phrase(reason: str, tables: int) -> str:
    """A not-measured reason as a short header phrase about tables."""
    for lead, one in KIND_BY_REASON:
        if reason.startswith(lead):
            return plural(tables, one)
    if reason.startswith("profile incomplete · exceeded "):
        minutes = reason[len("profile incomplete · exceeded "):].split(" min", 1)[0]
        return f"{plural(tables, 'table')} over the {minutes}-minute budget"
    return f"{plural(tables, 'table')}: {reason_class(reason)}"


def header_line(value: dict, tables: list[dict]) -> str:
    total, measured = value["column_count"], value["measured_count"]
    measured_tables = sum(1 for t in tables if _table_state(t) == "measured")
    parts = ["Value profile", f"{measured:,} of {plural(total, 'column')} measured", plural(measured_tables, "table"),
             f"profiled {when_words(value['surveyed_at'])}" + (f" as {value['ran_as']}" if value.get("ran_as") else "")]
    not_measured = total - measured
    if not_measured:
        by_reason: dict[str, int] = {}
        for t in tables:
            if _table_state(t) != "measured":
                r = _table_reason(t) or state_word(_table_state(t))
                by_reason[r] = by_reason.get(r, 0) + 1
        phrases = [_reason_phrase(r, n) for r, n in sorted(by_reason.items(), key=lambda kv: -kv[1])]
        tail = ", ".join(phrases[:3])
        if len(phrases) > 3:
            tail += f" and {len(phrases) - 3:,} more (see the tables)"
        parts.append(f"{plural(not_measured, 'column')} not measured" + (f": {tail}" if tail else ""))
    return " · ".join(parts)


def build_summary(registry, slug: str) -> dict:
    """`GET /api/databases/<slug>/value-profile`: the header and one line per table. `profiled: False` when no
    profile is stored (never run), so the page says so rather than drawing an empty screen."""
    value = _profile(registry, slug)
    if not value:
        return {"profiled": False, "sentence": ("This database has no value profile yet. Run the DatabaseQualitySurvey "
                                                "from Survey & analyses to profile its tables.")}
    tables = _tables(value["columns"])
    state_counts = {s: value["state_counts"][s] for s in STATE_ORDER if value["state_counts"].get(s)}
    state_counts.update({s: n for s, n in value["state_counts"].items() if s not in state_counts and n})
    type_counts: dict[str, int] = {}
    for t in tables:
        for tw, n in t["type_counts"].items():
            type_counts[tw] = type_counts.get(tw, 0) + n
    ordered = sorted(tables, key=lambda t: STATE_ORDER.index(_table_state(t)) if _table_state(t) in STATE_ORDER else 9)
    return {
        "profiled": True,
        "surveyed_at": value["surveyed_at"],
        "ran_as": value.get("ran_as", ""),
        "header": header_line(value, tables),
        "column_count": value["column_count"],
        "measured_count": value["measured_count"],
        "filters": {
            "states": [{"value": s, "label": state_word(s), "count": n} for s, n in state_counts.items()],
            "types": [{"value": tw, "label": tw, "count": type_counts[tw]}
                      for tw in TYPE_WORDS.values() if type_counts.get(tw)],
        },
        "tables": [
            {"schema_name": t["schema_name"], "table_name": t["table_name"], "state": _table_state(t),
             "state_words": state_word(_table_state(t)), "reason": _table_reason(t), "record_ct": t["record_ct"],
             "column_count": t["column_count"], "measured_count": t["state_counts"].get("measured", 0),
             "state_counts": t["state_counts"], "type_counts": t["type_counts"],
             "line": table_line(t, value["surveyed_at"]), "kind": table_kind(t),
             "rows_words": rows_words(t["record_ct"]),
             "openable": _table_state(t) == "measured"}
            for t in ordered
        ],
        "table_result": _table_result(registry, slug, value),
    }


def _table_result(registry, slug: str, value: dict) -> dict | None:
    """B1: the declared table result's block (envelope.js `tableResultHtml`): the first `max_rows` columns, typed and
    worded, under the truncation sentence, with the summary measures behind it and the publish band. Its headline is
    left empty: this screen's header line is the headline. None for a profile stored before the conversion (no typed
    rows): the cards below still read it."""
    from resource_explorer import table_result
    from resource_explorer.declared_context import declarations

    dec = declarations("database").get("value_profile")
    if dec is None or not dec.is_table_result():
        return None
    block = table_result.payload(registry, "database", slug, "value_profile", dec, value)
    if not block["table"]["stored"]:
        return None
    return {**block, "headline": ""}


def build_table(registry, slug: str, schema: str, table: str) -> dict | None:
    """`GET /api/databases/<slug>/value-profile/<schema>/<table>`: that table's column rows. None when the table is
    not in the latest batch."""
    value = _profile(registry, slug)
    if not value:
        return None
    cols = [c for c in value["columns"] if c["schema_name"] == schema and c["table_name"] == table]
    if not cols:
        return None
    surveyed_at = value["surveyed_at"]
    t = _tables(cols)[0]
    freq: dict[str, list[dict]] = {}
    for r in registry.query_detail_rows("database_value_profile_frequent_values", slug, surveyed_at=surveyed_at):
        if r["schema_name"] == schema and r["table_name"] == table:
            freq.setdefault(r["column_name"], []).append(r)
    proposals: dict[str, dict[str, int]] = {}
    for r in registry.query_detail_rows("database_quality_proposals", slug):
        if r["schema_name"] == schema and r["table_name"] == table and r.get("column_name"):
            counts = proposals.setdefault(r["column_name"], {})
            counts[r.get("state", "")] = counts.get(r.get("state", ""), 0) + 1
    try:
        run_year = int(str(surveyed_at)[:4])
    except ValueError:
        run_year = None
    rows = []
    for c in cols:
        row = {"column_name": c["column_name"], "state": c.get("state", ""), "state_words": state_word(c.get("state")),
               "type": type_word(c.get("general_type"))}
        if c.get("state") != "measured":
            row["sentence"] = c.get("reason") or "no reason recorded"
            rows.append(row)
            continue
        values = sorted(freq.get(c["column_name"], []), key=lambda v: v.get("value_rank") or 0)
        row["cells"] = {
            "type": _cell(type_word(c.get("general_type")), TYPE_MEANING.get(c.get("general_type") or "X", "")),
            "filled": filled_cell(c),
            "distinct": distinct_cell(c),
            "range": range_cell(c),
            "common": common_cell(c, values),
            "flags": flags_cell(c, run_year),
            "checks": checks_cell(proposals.get(c["column_name"], {})),
        }
        rows.append(row)
    return {"schema_name": schema, "table_name": table, "surveyed_at": surveyed_at,
            "line": table_line(t, surveyed_at), "state": _table_state(t), "columns": rows}


def container_rows(registry, slug: str) -> list[dict] | None:
    """The container reader for value_profile (design §5): at a sub-resource level, one row per TABLE, measured
    first and not-measured last, each opening that table on the Value profile tab. None when no profile is stored."""
    summary = build_summary(registry, slug)
    if not summary["profiled"]:
        return None
    out = []
    for t in summary["tables"]:
        name = f"{t['schema_name']}.{t['table_name']}"
        if t["state"] == "measured":
            value = f"{t['rows_words']} · {t['measured_count']:,} of {plural(t['column_count'], 'column')} measured"
            note = ""
        else:
            value, note = t["state_words"], t["reason"]
        query = urlencode({"type": "db", "resource": slug, "tab": "value_profile", "table": name})
        out.append({"name": name, "value": value, "note": note, "opens": f"/next?{query}"})
    return out
