"""The rest of TestGen's functional-type inference (Q4 port order step 6), as data over RE's stored value profile.

Upstream's `profiling/functional_datatype.sql` (TestGen 5.134.0, commit 3606859c; 629 lines) is ~38 ordered UPDATEs
that give every profiled column a `functional_data_type`, and `functional_tabletype_stage.sql` gives every table a
`functional_table_type`. Q6 (functional_type.py) ported the statements that can label a DATE column, for Recency.
This module ports the whole file in upstream order. The statements are vendored verbatim in
`configdata/quality/testgen/profiling/functional_datatype.sql` and `functional_tabletype_stage.sql`; the port is
pinned to their SHA-256 (`VENDORED_SQL_SHA256`) and every rule below names the upstream lines it came from.

Find-absence-as-answer is the design, not a detail. Upstream computes over measures RE does not take yet (profile
slice 2, port step 7: the text-shape measures `numeric_ct`, `includes_digit_ct`, `min_text`, `max_text`,
`avg_embedded_spaces`, `frequent_patterns`, `std_pattern_match`; and the percentiles, `date_weeks_present`,
`date_months_present`), where a NULL there reads as "no" in a SQL CASE. Here a column's label is tracked as the SET OF
VALUES IT COULD STILL END UP WITH (`Inference.possible`; `None` in the set is NULL, still unlabelled), so:

- every statement whose condition is true, false or unknown is applied in that sense: a statement that cannot fire
  changes nothing; one that fires sets its label; one that MIGHT fire adds its possible results to the set;
- a column whose set is one label has that label (`Inference.label`); a column whose set is more than one has none,
  and says why ("type not inferred · needs min_text, which the profile does not take yet"), never a default type;
- a selection rule asks "can this column be label X" in three values (`label_is`, `label_in`): true, false (X is not
  in the set: the rule really says no, so a numeric column is not flooded with `not_established`), or unknown.

A measure RE does not take is NULL by construction on a column of another general_type upstream too (a text measure
on a number), and there it reads as upstream's NULL. On a text column it is unknown.

Statements not ported, with the reason, are listed in DESIGN-QUALITY-STEP-6-FUNCTIONAL-TYPE-INFERENCE.md.
"""
from __future__ import annotations

import itertools
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from resource_explorer.surveyors.database.quality.catalogue import (
    _and,
    _not,
    _cmp,
    _profiled_by_1b,
    _sql_or,
    label_in,
)
from resource_explorer.surveyors.database.quality.functional_type import (
    PROFILE_ID_COLUMN_MASK,
    PROFILE_SK_COLUMN_MASK,
    TBD,
    _dow,
    _ilike,
    _month_aligned,
    as_timestamp,
)
from resource_explorer.surveyors.database.quality.general_type import _information_schema_type

PROFILING_DIR = Path(__file__).resolve().parents[3] / "configdata" / "quality" / "testgen" / "profiling"
FUNCTIONAL_DATATYPE_SQL = PROFILING_DIR / "functional_datatype.sql"
FUNCTIONAL_TABLETYPE_SQL = PROFILING_DIR / "functional_tabletype_stage.sql"
#: SHA-256 of the two vendored upstream files. A changed file fails the pin test until this port is re-read.
VENDORED_SQL_SHA256 = {
    "functional_datatype.sql": "a63102029bb353b5bec08e22e41d50ce417fefd6848eac647e6045b0ab8b92a0",
    "functional_tabletype_stage.sql": "6c417c9fb158bf13ff20059f2437039e15bc534759b8157cf54de9195254477a",
}

#: Measures upstream takes that RE does not (yet). Each is read by some statement; on a TEXT column it is unknown, on
#: any other column it is NULL by construction upstream. The reason a type was not inferred names the ones it needed.
NOT_TAKEN = {
    "frequent_values": "its frequency analysis is not in the row (profiled before step 8b, or its read did not finish)",
    "percentile_25/50/75": "the percentile statement did not run or ran out of its budget", "date_weeks_present": "not planned",
    "date_months_present": "not planned",
}
#: Port step 7b: a text column's sampled text-shape measures are read by the text branches below. A row from before step 7
#: (or a column whose read did not finish) has no `sample_value_ct`; its text conditions are unknown, and the reason says so.
TEXT_SHAPE_UNREAD = "its sampled text shape was not measured (profiled before step 7, or this column's read did not finish)"
TAKEN_NOT_READ = frozenset()

KEEP = object()          # a statement's CASE left the value as it was


# ── the result ────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class Inference:
    """What the inference concluded for one column.

    `possible` is every value `functional_data_type` could end with (None = NULL, no statement labelled it). `label`
    is the one label when the set has exactly one element, else None. `needed` names the measures RE does not take
    that kept it from concluding; `why` is the sentence for a reader."""

    possible: frozenset
    needed: tuple[str, ...] = ()
    reasons: tuple[str, ...] = field(default=())

    @property
    def label(self) -> str | None:
        if len(self.possible) == 1:
            (only,) = self.possible
            return only
        return None

    @property
    def why(self) -> str:
        if self.label is not None:
            return ""
        if self.reasons:
            return "type not inferred · " + "; ".join(self.reasons)
        return "type not inferred"


# ── three-valued helpers ──────────────────────────────────────────────────────────────────────────────────────

def _pct(count, total) -> float | None:
    """`count / NULLIF(total::FLOAT, 0) * 100`."""
    return None if count is None or not total else count / float(total) * 100


def _num(value):
    """A stored decimal text (fractional_sum, sum_value) as a Decimal, or the value if it already is a number."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _similar(value: str, pattern: str) -> bool:
    """SQL `SIMILAR TO`: the whole value; `%` any run, `_` any one character, `|` alternation, `()` grouping."""
    rx = pattern.replace("%", ".*").replace("_", ".")
    return re.fullmatch(rx, value, flags=re.DOTALL) is not None


def _between(value, low, high) -> bool | None:
    return None if value is None else low <= value <= high


def case_set(arms, default) -> set:
    """The values a CASE can give: arms are (condition, result) in order; a true arm ends it (its result is the only one
    left), an unknown arm adds its result and goes on, a false arm is skipped. `default` is the ELSE."""
    out: set = set()
    for cond, result in arms:
        if cond is True:
            out.add(result)
            return out
        if cond is None:
            out.add(result)
    out.add(default)
    return out


def apply(current: set, where, results, *, null_guard: bool) -> set:
    """One UPDATE. `where` is the WHERE's value (without the IS NULL guard when `null_guard`); `results` the values its
    SET can give (`KEEP` = unchanged; a callable maps the old value). A false WHERE changes nothing; an unknown one
    keeps every old value as well."""
    if where is False:
        return current
    out: set = set()
    for old in current:
        if null_guard and old is not None:
            out.add(old)
            continue
        for result in results:
            out.add(old if result is KEEP else result(old) if callable(result) else result)
    if where is None:
        out |= current
    return out


# ── the column's view of the profile ──────────────────────────────────────────────────────────────────────────

def _first_pattern(stored) -> str | None:
    """`fn_frequent_value(frequent_patterns, 1)`: the most frequent pattern of a stored `frequent_patterns` (JSON text or an
    already parsed dict), or None."""
    import json

    if isinstance(stored, str):
        try:
            stored = json.loads(stored)
        except ValueError:
            return None
    values = (stored or {}).get("values") if isinstance(stored, dict) else None
    return values[0].get("value") if values else None


class _Col:
    """A profile row read for the inference. `text` is True for a general_type 'A' column: the measures upstream takes
    only for text are unknown there and NULL-by-construction anywhere else."""

    def __init__(self, p: dict):
        self.p = p
        self.gt = p.get("general_type") or ""
        self.text = self.gt == "A"
        self.name = p.get("column_name") or ""
        self.missed: set[str] = set()
        # The sampled text-shape measures (port step 7b): "na" on a column that is not text (NULL by construction, so a
        # condition on them is false), "read" on a text column whose shape was read or that holds no value, "unread" on a
        # text column that has values but no shape (its conditions on them are unknown).
        self.top1 = _first_pattern(p.get("frequent_patterns"))
        if not self.text:
            self.ts = "na"
        elif p.get("value_ct") == 0 or p.get("sample_value_ct") is not None:
            self.ts = "read"
        else:
            self.ts = "unread"

    def tcond(self, fn, *keys: str):
        """A condition over text-shape measures, `fn(*values)`, as a positive WHERE reads it: an unread text column is
        unknown (and `missed` says why); a measure that is SQL NULL (not text, a column with no value, an empty string
        that NULLIF removed) makes the condition not true; otherwise it is `fn`'s answer."""
        if self.ts == "unread":
            self.missed.add("sample_value_ct")
            return None
        values = [self.top1 if k == "_top1" else self.p.get(k) for k in keys]
        if any(v is None for v in values):
            return False
        return bool(fn(*values))

    def get(self, key):
        return self.p.get(key)

    def na(self, cond):
        """A condition that read a text-only measure, as the CASE sees it: unknown on a text column, FALSE on any other
        column when it is None only because of that NULL (a row from before profile 1b stays unknown)."""
        if cond is not None or self.text:
            return cond
        return False if _profiled_by_1b(self.p) is not None else None


def _name_year(c: _Col, now: datetime) -> bool | None:
    n = c.name
    if not (re.search("year", n, re.IGNORECASE) or re.search("yr", n, re.IGNORECASE)):
        return False
    numeric = _and(_cmp(c.get("min_value"), ">=", 1900), _cmp(c.get("max_value"), "<=", now.year + 20),
                   _cmp(_num(c.get("fractional_sum")) or Decimal(0), "=", 0)
                   if c.get("fractional_sum") is not None else (True if _profiled_by_1b(c.p) else None))
    text = c.tcond(lambda lo, hi, al, es: lo >= "1900" and hi <= str(now.year + 20) and al == 4 and es == 0,
                   "min_text", "max_text", "avg_length", "avg_embedded_spaces")
    return _sql_or(numeric, text)


def run_instant(p: dict) -> datetime:
    return as_timestamp(p.get("surveyed_at")) or datetime.now(UTC).replace(tzinfo=None)


# ── the ordered statements ────────────────────────────────────────────────────────────────────────────────────

_PROCESS_DATE = (re.compile(r"^(last_?|system_?|)(add|create|insert|inrt|update|updt|mod|modif|modf|del|delete|refresh)"
                            r"(.{0,3}d?_?(time|tm|date|day|dt|stamp|timestamp|datestamp))$"),
                 re.compile(r"^(last_?|)(change|chg|update|updt|mod|modify|modf|modified|refresh|refreshed)$"))
_PROCESS_USER = re.compile(r"^(last_?|)(create|update|modif|delete|refresh)(.*?(by|id|name|nm|user|usr))$")
_SYSTEM_USER = re.compile(r"(user|usr)_?(name|nm)?$")
_PERSON_FULL = re.compile(r"(approver|full|contact|emp|employee|hcp|manager|mgr|owner|party|person|preferred|rep|"
                          r"reviewer|salesperson|spouse)(_| |)(name|nm)$")
_ENTITY = re.compile(r"(acct|account|affiliation|branch|business|co|comp|company|corp|corporate|cust|customer|"
                     r"distributor|employer|entity|firm|franchise|hco|org|organization|site|supplier|vendor|"
                     r"hospital|practice|clinic)(_| |)(name|nm)$")
_MEASURE_TEXT_NAME = (re.compile(r"_(average|avg|count|ct|sum|total|tot)$"),
                      re.compile(r"^(average|avg|count|ct|sum|total|tot)_"))


def _integer_type(c: _Col) -> bool | None:
    """`column_type ILIKE '%int%'`: the catalog type of an integer column (bigint, integer, smallint)."""
    t = c.get("catalog_type")
    if t is None:
        return None
    return "int" in _information_schema_type(t)[0].lower()


def _scale(c: _Col):
    """The numeric scale of the column: an int, `False` when the type has none (integers, floats: upstream's
    SPLIT_PART(column_type, ',', 2) is ''), or None when RE did not read it."""
    t = _information_schema_type(c.get("catalog_type") or "")[0].lower() if c.get("catalog_type") else None
    if t is None:
        return None
    if t != "numeric":
        return False
    scale = c.get("numeric_scale")
    return scale if scale is not None else None


def _scale_gt_zero(c: _Col) -> bool | None:
    """`RTRIM(SPLIT_PART(column_type, ',', 2), ')') > '0'`, a string comparison."""
    s = _scale(c)
    if s is None:
        return None
    return False if s is False else str(s) > "0"


def _scale_is_zero(c: _Col) -> bool | None:
    """`SPLIT_PART(column_type, ',', 2) > '' AND RTRIM(SPLIT_PART(...), ' )') = '0'`."""
    s = _scale(c)
    if s is None:
        return None
    return False if s is False else s == 0


def _fractional_sum_zero(c: _Col) -> bool | None:
    """`fractional_sum = 0`: NULL (a float or non-numeric column) is not equal, a row from before profile 1b is unknown."""
    if _profiled_by_1b(c.p) is None:
        return None
    f = _num(c.get("fractional_sum"))
    return None if f is None else f == 0


def _spike_ruled_out(c: _Col) -> bool:
    """Whether 'Measurement Spike' (a sparse column with extreme non-zero values) can be ruled out WITHOUT the
    percentiles, which RE does not take. Spike needs 'mostly zero' (the 25th, 50th and 75th percentiles all 0) or
    'mostly NULL' (< 5 % filled). All three percentiles 0 needs at least about half the values to be 0
    (zero_value_ct >= (value_ct - 1) / 2 - 1); so Spike is out when the values are mostly filled and zeros are fewer
    than that, or every value is on one side of 0. Otherwise it cannot be told."""
    value_ct, record_ct = c.get("value_ct"), c.get("record_ct")
    if value_ct is None or record_ct is None:
        return False
    mostly_null = value_ct > 0 and record_ct > 0 and value_ct / record_ct < 0.05
    if mostly_null:
        return False
    lo, hi, zeros = c.get("min_value"), c.get("max_value"), c.get("zero_value_ct")
    if lo is not None and hi is not None and (lo > 0 or hi < 0):
        return True
    return zeros is not None and value_ct > 0 and zeros < (value_ct - 1) / 2 - 1


# ── Measurement Spike: the evidence threshold is a declared choice ────────────────────────────────────────────────

#: How "extreme non-zero values" is expressed (owner, 2026-10-29: "sometimes the user may want to express in absolute
#: terms and other times as percent change - or other"). A kind is a function (p25, p50, p75, avg, limit) -> bool; the
#: configured kind and its limit are `SPIKE_EVIDENCE`. Upstream's own form is `upstream_ratio`, TestGen's default:
#: (p75 - p25) > 2 * |avg|  OR  |avg| > 5 * |p50|, which `limit` does not change.
SPIKE_KINDS: dict = {}


def spike_kind(name: str):
    """Register a way to express the spike evidence. Add a new kind by decorating a function (p25, p50, p75, avg,
    limit) -> bool with `@spike_kind("name")`, then set `SPIKE_EVIDENCE = {"kind": "name", "limit": ...}`; the
    inference, the tests' parametrization and the design note's table pick it up from `SPIKE_KINDS`."""
    def register(fn):
        SPIKE_KINDS[name] = fn
        return fn
    return register


@spike_kind("upstream_ratio")
def _upstream_ratio(p25, p50, p75, avg, limit=None) -> bool:
    return (p75 - p25) > 2 * abs(avg) or abs(avg) > 5 * abs(p50)


@spike_kind("absolute")
def _absolute(p25, p50, p75, avg, limit) -> bool:
    """The spread between the quartiles, or the mean's distance from the median, is more than `limit` in the column's
    own units."""
    return (p75 - p25) > limit or abs(avg - p50) > limit


@spike_kind("percent_change")
def _percent_change(p25, p50, p75, avg, limit) -> bool:
    """The mean differs from the median by more than `limit` percent of the median (a zero median with a non-zero
    mean is an unbounded change)."""
    if p50 == 0:
        return avg != 0
    return abs(avg - p50) / abs(p50) * 100 > limit


#: The configured form. TestGen's own by default, so a label agrees with TestGen's unless a person chooses otherwise.
SPIKE_EVIDENCE = {"kind": "upstream_ratio", "limit": None}


def _spike(c: _Col) -> bool | None:
    """Whether the column is a 'Measurement Spike' (upstream lines 488-497): `general_type = 'N'` AND (the 25th, 50th
    and 75th percentiles are all 0 OR fewer than 5 % of the records are filled) AND (the evidence, in the configured
    form). With the percentiles it is exact; without them (a profile from before they were taken, or a table that ran
    out of its percentile budget) the bound of `_spike_ruled_out` can still rule it out, else it is unknown."""
    if c.gt != "N":
        return False
    vc, rc, avg = c.get("value_ct"), c.get("record_ct"), c.get("avg_value")
    mostly_null = None if vc is None or rc is None else (vc > 0 and rc > 0 and vc / rc < 0.05)
    trio = (c.get("percentile_25"), c.get("percentile_50"), c.get("percentile_75"))
    have = None not in trio
    sparse = _sql_or((trio[0] == 0 and trio[1] == 0 and trio[2] == 0) if have else None, mostly_null)
    evidence = None
    if have and avg is not None:
        evidence = SPIKE_KINDS[SPIKE_EVIDENCE["kind"]](*trio, avg, SPIKE_EVIDENCE["limit"])
    result = _and(sparse, evidence)
    if result is None and _spike_ruled_out(c):
        return False
    if result is None:
        c.missed.add("percentile_25/50/75")
    return result


def _datediff_week(first: datetime, second: datetime) -> int:
    """`datediff('WEEK', first, second)`: (DATE_TRUNC('week', second)::DATE - DATE_TRUNC('week', first)::DATE) / 7."""
    def monday(ts: datetime):
        d = ts.date()
        return d.toordinal() - d.weekday()
    return (monday(second) - monday(first)) // 7


def _date_case_results(c: _Col, now: datetime) -> set:
    """Statement 72-116's CASE for a date column; the same thresholds as functional_type.date_case_label, plus the
    Transactional branches this step ports (the ones that read measures RE takes), and 'Date Stamp'/'DateTime Stamp'."""
    if c.gt != "D":
        # every date measure (future_date_ct, before_*_date_ct, min/max_date) is NULL by construction on a text column,
        # so every arm reads NULL and the CASE gives its ELSE: the label is left as it was.
        return {KEEP}
    value_ct = c.get("value_ct")
    before20, before5, future = c.get("before_20yr_date_ct"), c.get("before_5yr_date_ct"), c.get("future_date_ct")
    lo, hi = as_timestamp(c.get("min_date")), as_timestamp(c.get("max_date"))
    b20, fut, b5 = _pct(before20, value_ct), _pct(future, value_ct), _pct(before5, value_ct)
    transactional_gate = _and(_between(b5, 0, 20), _between(fut, 0, 10))
    days = c.get("date_days_present")
    arm1 = _and(_cmp(value_ct, ">", 100), _cmp(c.get("distinct_value_ct"), ">", 20),
                _cmp(days, ">=", None if lo is None or hi is None else 2 * (_datediff_week(lo, hi) + 1)))
    inner = case_set([(arm1, "Transactional Date"),
                      (None, "Transactional Date (Wk)"),    # reads date_weeks_present: not taken
                      (None, "Transactional Date (Mo)"),    # reads date_months_present: not taken
                      (None, "Transactional Date (Qtr)")],   # datediff('QUARTER') as vendored does not run on Postgres
                     "Date (TBD)")
    c.missed.update(("date_weeks_present", "date_months_present"))
    ctype = (c.get("catalog_type") or "").lower()
    # upstream's column_type for Postgres: 'date' for date, 'timestamp' for timestamp without time zone
    stamp = "Date Stamp" if ctype == "date" else "DateTime Stamp" if ctype.startswith("timestamp") else KEEP
    arms: list = [(_cmp(b20, ">=", 75), "Historical Date"), (_cmp(fut, ">=", 95), "Future Date"),
                  (_cmp(fut, ">=", 50), "Schedule Date")]
    out = case_set(arms, "__tail__")
    if "__tail__" not in out:
        return out
    out.discard("__tail__")
    if transactional_gate is True:
        out |= inner
    elif transactional_gate is None:
        out |= inner | {stamp}
    else:
        out.add(stamp)
    return out


def _stage_a(c: _Col, now: datetime, by_pos: dict | None = None) -> tuple[set, set[str]]:
    """Every statement up to and including 'ID-Unique' (upstream 1-557), in order. Returns (the value set, the
    statements whose condition stayed unknown, by name)."""
    s: set = {None}
    unknown: set[str] = set()
    p, name, gt = c.p, c.name, c.gt
    vc, rc, dv = p.get("value_ct"), p.get("record_ct"), p.get("distinct_value_ct")

    def step(label: str, where, results, null_guard: bool = True):
        nonlocal s
        if where is None:
            unknown.add(label)
        s = apply(s, where, results, null_guard=null_guard)

    # 18-25: TBD
    zl = p.get("zero_length_ct")
    zero_all = _cmp(zl, "=", vc) if (gt == "A" and zl is not None and vc) else (None if gt == "A" else False)
    tbd = case_set([(_cmp(rc, "=", 0), TBD),
                    (_and(_cmp(rc, ">", 0), _sql_or(_cmp(vc, "<", 5), zero_all)), TBD)], KEEP)
    step("TBD", True, tbd, null_guard=False)
    # 28-36: Constant
    filled = None if vc is None or not rc else (vc - (p.get("filled_value_ct") or 0) - (zl or 0)) / rc * 100.0
    step("Constant", _and(_cmp(dv, "=", 1), _cmp(filled, ">", 75)), {"Constant"})
    # 39-49: ID masks
    step("ID-SK", _ilike(name, PROFILE_SK_COLUMN_MASK), {"ID-SK"})
    step("ID", _ilike(name, PROFILE_ID_COLUMN_MASK), {"ID"})
    # 72-116: the date CASE (a date column, or text that is all dates: unknown on text, date_ct is not taken)
    # For a text column that is all dates (value_ct = date_ct + zero_length_ct) the date measures are NULL, every CASE arm is
    # NULL and the ELSE leaves the label as it was: the statement changes nothing, whatever the text holds.
    step("Date", gt == "D", _date_case_results(c, now) if gt == "D" else {KEEP})
    # 119-134: character dates read frequent_patterns / min_text
    step("Character Date", c.tcond(lambda dp, mn, mx, t: dp == 1 and mn >= "1900" and mx <= "2200" and t.strip() == "NNNN-NN-NN",
                                   "distinct_pattern_ct", "min_text", "max_text", "_top1"), {"Date Stamp"})
    step("Character Timestamp", c.tcond(lambda dp, t: dp == 1 and t.strip() in ("NNNN-NN-NN NN:NN:NN", "NNNN-NN-NNANN:NN:NN+NN:NN"),
                                        "distinct_pattern_ct", "_top1"), {"DateTime Stamp"})
    # 136-142: Process <label>, general_type IN (A, D)
    process = _and(gt in ("A", "D"), any(rx.search(name) for rx in _PROCESS_DATE))
    step("Process", process, {lambda old: None if old is None else "Process " + old}, null_guard=False)
    # 146-226: the Periods, by name first
    year = _name_year(c, now)
    step("Period Year", year, {"Period Year"})
    qtr_name = bool(re.search("qtr|quarter", name, re.IGNORECASE))
    qtr_numeric = _and(_cmp(c.get("min_value"), "=", 1), _cmp(c.get("max_value"), "=", 4), _fractional_sum_zero(c))
    qtr_text = c.tcond(lambda mn, mx, al, t: mn >= "1900" and mx <= "2200" and 6 <= al <= 7
                       and re.search(r"^\s*NNNN[-_]AN\s*$", t) is not None, "min_text", "max_text", "avg_length", "_top1") \
       
    step("Period Quarter", _and(qtr_name, _sql_or(qtr_text, qtr_numeric)), {"Period Quarter"})
    mo = bool(re.search("mo", name, re.IGNORECASE))
    yearmon = c.tcond(lambda mn, mx, al, t: mn >= "1900" and mx <= "2200"
                      and ((6.8 <= al <= 7.2 and re.search(r"^\s*NNNN[-_]NN\s*$", t) is not None)
                           or (7.8 <= al <= 8.2 and re.search(r"^\s*NNNN[-_]AAA\s*$", t.upper()) is not None)),
                      "min_text", "max_text", "avg_length", "_top1")
    step("Period Year-Mon", _and(mo, yearmon), {"Period Year-Mon"})
    month_numeric = _and(_cmp(c.get("min_value"), "=", 1), _cmp(c.get("max_value"), "=", 12), _fractional_sum_zero(c))
    month_text = c.tcond(lambda ml, mn, mx: (ml == 2 and mn in ("01", "1") and mx == "12")
                         or (ml == 9 and mn.upper() == "APRIL" and mx.upper() == "SEPTEMBER")
                         or (ml == 3 and mn.upper() == "APR" and mx.upper() == "SEP"),
                         "max_length", "min_text", "max_text")
    step("Period Month", _and(mo, _sql_or(month_text, month_numeric)), {"Period Month"})
    monnn = re.compile(r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[\s-]?\d{1,2}$", re.IGNORECASE)
    step("Period Mon-NN", c.tcond(lambda mn, mx, al, t: monnn.search(mn) is not None and monnn.search(mx) is not None
                                  and 5.8 <= al <= 6.2 and re.search(r"AAA[\s-]NN", t.strip(), re.IGNORECASE) is not None,
                                  "min_text", "max_text", "avg_length", "_top1"), {"Period Mon-NN"})
    wk = bool(re.search("wk|week", name, re.IGNORECASE))
    max_52_53 = None if c.get("max_value") is None else c.get("max_value") in (52, 53)
    wk_numeric = _and(_cmp(c.get("min_value"), "=", 1), max_52_53, _fractional_sum_zero(c))
    wk_text = c.tcond(lambda mn, mx: mn in ("1", "01") and mx in ("52", "53"), "min_text", "max_text")
    step("Period Week", _and(wk, _between(dv, 10, 53), _sql_or(wk_text, wk_numeric)), {"Period Week"})
    dow_name = bool(re.search("day|dow", name, re.IGNORECASE))
    dow_numeric = _and(_cmp(c.get("min_value"), "=", 1), _cmp(c.get("max_value"), "=", 7), _fractional_sum_zero(c))
    dow_text = c.tcond(lambda ml, mn, mx: (mn == "1" and mx == "7") or (mn.upper() == "FRIDAY" and mx.upper() == "WEDNESDAY" and ml == 9)
                       or (mn.upper() == "FRI" and mx.upper() == "WED" and ml == 3), "max_length", "min_text", "max_text")
    step("Period DOW", _and(dow_name, _cmp(dv, "=", 7), _sql_or(dow_text, dow_numeric)), {"Period DOW"})
    # 230-250: Period overrides, no IS NULL guard; they also read date_days/weeks/months_present (weeks/months not taken)
    lo, hi = as_timestamp(c.get("min_date")), as_timestamp(c.get("max_date"))
    if gt == "D":
        if lo is None or hi is None:
            # a column with no values has no min_date: NULL upstream, so the override cannot fire; one that was not
            # measured is unknown
            override = False if c.get("value_ct") == 0 else None
        else:
            (lf, ll), (hf, hl) = _month_aligned(lo), _month_aligned(hi)
            month_bounds = (lf and hf) or (ll and hl)
            week_bounds = _dow(lo) in (0, 1, 5, 6) and _dow(lo) == _dow(hi)
            override = None if (month_bounds or week_bounds) else False
        c.missed.update(("date_weeks_present", "date_months_present"))
        step("Period override", override, {"Period Month", "Period Week"}, null_guard=False)
    # 252-262: month / week / period by name
    lower = name.lower()
    if lower in ("month", "week", "period"):
        def by_name(old, lower=lower):
            if lower == "month":
                return "Period Month"
            if lower == "week":
                return "Period Week"
            return old if (old is not None and old.lower().startswith("period")) else (None if old is None else "Period")
        step("Period by name", True, {by_name}, null_guard=False)
    # 276-298: Zip / Email / Phone / Address / State
    zip_name = bool(re.search("zip|postal", name, re.IGNORECASE))
    zip_exact = lower in ("zip_code", "zip")
    phone_name = bool(re.search("phone", name, re.IGNORECASE))
    phone_len = _between(c.get("max_length"), 7, 11)
    addr_name = bool(re.search("address$", name, re.IGNORECASE)) and not re.search("email", name, re.IGNORECASE)

    def std(label):
        # std_pattern_match = label: NULL (no standard pattern, or not text) is not true once the shape is read
        return c.tcond(lambda v: v == label, "std_pattern_match")

    zip_text_digits = c.tcond(lambda nc, vc_, zl_, ml: nc > 0 and vc_ == nc + zl_ and ml <= 9,
                              "numeric_ct", "value_ct", "zero_length_ct", "max_length") if zip_exact and gt == "A" else False
    zip_where = _sql_or(_and(std("ZIP_USA"), zip_name), zip_text_digits)
    arms = [(zip_where, "Zip"), (std("EMAIL"), "Email"),
            (_sql_or(_and(phone_name, phone_len), std("PHONE_USA")), "Phone"),
            (_sql_or(addr_name, std("STREET_ADDR")), "Address"), (std("STATE_USA"), "State")]
    step("Zip/Email/Phone/Address/State", True, case_set(arms, KEEP))
    # 301-317 and 520-536: City, from the neighbours' labels / text shape
    # City is set from the neighbours' labels (State at position + 1, Zip at + 2), which are decided in this very statement
    # for another column. A column whose name has no 'city'/'cty' cannot take the first form, and the second needs the
    # next column to be State, which only a column whose std_pattern_match is STATE_USA can be; both are false then.
    step("City (by position)", _city_by_position(c, by_pos or {}), {"City"}, null_guard=False)
    # 320-347: person names. avg_length is a profile 1b measure (also taken for a number or a date); avg_embedded_spaces is
    # text-only, so it is NULL, and the condition not true, on any other column.
    def spaces(fn):
        return c.tcond(lambda al, es: fn(al, es), "avg_length", "avg_embedded_spaces")

    step("Person Full Name", _and(_PERSON_FULL.search(name) is not None or name in ("name", "nm"),
                                  spaces(lambda al, es: al <= 20 and 0.9 <= es <= 2.0)), {"Person Full Name"})
    given = (_similar(lower, "%f(|i)rst(_| |)n(|a)m%%") or _similar(lower, "%(middle|mdl)(_| |)n(|a)m%%")
             or _similar(lower, "%nick(_| |)n(|a)m%%"))
    step("Person Given Name", _and(given, spaces(lambda al, es: al <= 8 and es < 0.2)), {"Person Given Name"},
         null_guard=False)
    last = (_similar(lower, "%l(|a)st(_| |)n(|a)m%") or _similar(lower, "%maiden(_| |)n(|a)m%")
            or _similar(lower, "%sur(_| |)n(|a)m%"))
    step("Person Last Name", _and(last, spaces(lambda al, es: 5 <= al <= 8 and es < 0.2)), {"Person Last Name"},
         null_guard=False)
    step("Entity Name", _and(gt == "A", _ENTITY.search(name) is not None), {"Entity Name"})
    # 356-366: Process User, System User, by name (no IS NULL guard, no type filter)
    step("Process User", _PROCESS_USER.search(name) is not None, {"Process User"}, null_guard=False)
    step("System User", _SYSTEM_USER.search(name) is not None, {"System User"}, null_guard=False)
    # 374-395: Boolean
    two = _cmp(dv, "=", 2)
    one = _cmp(dv, "=", 1)
    bool01 = _and(_cmp(c.get("min_value"), "=", 0), _cmp(c.get("max_value"), "=", 1),
                  None if c.get("catalog_type") is None else "numeric" not in (c.get("catalog_type") or "").lower())
    pairs2 = (("no", "yes"), ("n", "y"), ("false", "true"), ("0", "1"))
    text2 = c.tcond(lambda mn, mx: (mn.lower(), mx.lower()) in pairs2, "min_text", "max_text")
    text1 = c.tcond(lambda mn, mx: (mn.lower() in ("no", "yes") and mx.lower() in ("no", "yes"))
                    or (mn.lower() in ("n", "y") and mx.lower() in ("n", "y"))
                    or (mn.lower() in ("false", "true") and mx.lower() in ("f", "t"))
                    or (mn.lower() in ("0", "1") and mx.lower() in ("0", "1")), "min_text", "max_text")
    b_arms = [(_sql_or(gt == "B", _and(two, _sql_or(text2, bool01))), "Boolean"),
              (_sql_or(gt == "B", _and(one, _sql_or(text1, bool01))), "Boolean")]
    step("Boolean", True, case_set(b_arms, KEEP))
    # 412-448: Code / Category / ID / Attribute / Description / Measurement Text, character columns whose datatype_suggestion
    # is a VARCHAR (upstream runs datatype_suggestions.sql before this file, with the label still NULL: a first pass)
    if gt == "A":
        step("Code/Category/ID/Attribute/Description", _suggestion_is_varchar(c), _code_category_results(c))
    # 456-467: Flag
    if gt == "A":
        # the column's catalog type is not numeric for a text column, so `lower(column_type) NOT ILIKE '%numeric%'` is true and
        # the OR with datatype_suggestion needs nothing more
        flag = _and(_between(dv, 3, 5), c.tcond(lambda mn, ml, pats: mn > 1 and ml <= 7 and _has_alpha(pats),
                                                 "min_length", "max_length", "frequent_patterns"))
        step("Flag", flag, {"Flag"})
    # 472-517: the remaining NULLs
    step("Remaining", True, _tail(c), null_guard=True)
    # 520-536: City by text shape and a State neighbour; no IS NULL guard, so it overrides whatever the column was labelled
    step("City (by shape)", _city_by_shape(c, by_pos or {}), {"City"}, null_guard=False)
    # 538-565: ID-Secondary / ID-Group / ID-Unique
    id_pct = None if rc in (None, 0) or vc is None else round(100.0 * vc / rc)
    uniq_pct = None if vc in (None, 0) or dv is None else round(100.0 * dv / vc)
    arms2 = [(_and(_cmp(id_pct, ">", 70), _cmp(uniq_pct, ">=", 75)), "ID-Secondary"),
             (_and(_cmp(id_pct, ">", 70), _cmp(uniq_pct, "<", 75)), "ID-Group")]
    s = _guarded_on(s, "ID", case_set(arms2, KEEP))
    unique_where = _and(_cmp(rc, "=", dv), _cmp(rc, ">", 50))
    s = _relabel(s, {"ID", "ID-Secondary"}, unique_where, "ID-Unique")
    s = _relabel(s, {"ID-SK"}, unique_where, "ID-Unique-SK")
    return s, unknown


def _neighbour(c: _Col, by_pos: dict, offset: int):
    pos = c.get("position")
    if pos is None:
        return None
    return by_pos.get((c.p.get("schema_name"), c.p.get("table_name"), pos + offset))


def _may_be_state(n: dict | None) -> bool | None:
    """Whether a neighbouring column could carry the label 'State': only a column whose std_pattern_match is STATE_USA can.
    False is exact (the State arm cannot fire); True is only "possible" (earlier statements may have labelled it), so None."""
    if n is None:
        return False
    if n.get("general_type") == "A" and n.get("value_ct") and n.get("sample_value_ct") is None:
        return None                                   # its text shape was not read: unknown
    return None if n.get("std_pattern_match") == "STATE_USA" else False


def _may_be_zip(n: dict | None) -> bool | None:
    if n is None:
        return False
    name = (n.get("column_name") or "")
    named = bool(re.search("zip|postal", name, re.IGNORECASE))
    if n.get("general_type") == "A" and n.get("value_ct") and n.get("sample_value_ct") is None:
        return None if named or name.lower() in ("zip_code", "zip") else False
    return None if (n.get("std_pattern_match") == "ZIP_USA" and named) or name.lower() in ("zip_code", "zip") else False


def _city_by_position(c: _Col, by_pos: dict) -> bool | None:
    """Upstream 301-317: a column named like 'city' (`SIMILAR TO '%c(|i)ty%'`), with a State two columns on... one column on and a Zip two on,
    that is not itself State or Zip. Never TRUE here (the neighbours' labels are decided in the same statement); False exactly
    when it cannot fire."""
    if not _similar(c.name.lower(), "%c(|i)ty%"):
        return False
    return _and(_may_be_state(_neighbour(c, by_pos, 1)), _may_be_zip(_neighbour(c, by_pos, 2)))


def _city_by_shape(c: _Col, by_pos: dict) -> bool | None:
    """Upstream 520-536: no digits, numbers or dates in the values, average length 7-12, fewer than one embedded space on
    average, 15 to 40,000 distinct values, and a State in the next column."""
    shape = c.tcond(lambda dg, nc, dc, vc_, al, es, dv_: vc_ and dg / vc_ < 0.05 and nc / vc_ < 0.05 and dc / vc_ < 0.05
                    and 7 <= al <= 12 and es < 1 and 15 <= dv_ <= 40000,
                    "includes_digit_ct", "numeric_ct", "date_ct", "value_ct", "avg_length", "avg_embedded_spaces",
                    "distinct_value_ct")
    return _and(shape, _may_be_state(_neighbour(c, by_pos, 1)))


def _has_alpha(stored) -> bool:
    """`fn_frequent_like(frequent_patterns, '%A%')`: some stored pattern contains an upper-case A (a letter)."""
    import json

    if isinstance(stored, str):
        try:
            stored = json.loads(stored)
        except ValueError:
            return False
    values = (stored or {}).get("values") if isinstance(stored, dict) else None
    return any("A" in (v.get("value") or "") for v in values or ())


def _guarded_on(s: set, old_label: str, results: set) -> set:
    """`UPDATE ... SET label = CASE ... END WHERE functional_data_type = old_label`: only members equal to it change."""
    out = set()
    for v in s:
        if v == old_label:
            out |= {v if r is KEEP else r for r in results}
        else:
            out.add(v)
    return out


def _relabel(s: set, from_labels: set, where, to_label: str) -> set:
    """`UPDATE ... SET label = to WHERE label IN (from) AND <where>`."""
    if where is False:
        return s
    out = set()
    for v in s:
        if v in from_labels:
            out.add(to_label) if where is True else out.update({v, to_label})
        else:
            out.add(v)
    return out


def _nth_pattern(stored, n: int) -> str | None:
    """`fn_frequent_value(frequent_patterns, n)`: the n-th (1-based) most frequent stored pattern, or None."""
    import json

    if isinstance(stored, str):
        try:
            stored = json.loads(stored)
        except ValueError:
            return None
    values = (stored or {}).get("values") if isinstance(stored, dict) else None
    return (values[n - 1].get("value") if values and len(values) >= n else None)


def _frequent_like(c: _Col, needle: str) -> bool | None:
    """`fn_frequent_like(frequent_values, '%<needle>%')`: some stored frequent value contains it. A column eligible for the
    frequency analysis (2-70 distinct values of at most 70 characters) whose analysis is not in the row is unknown; any
    other column has no frequent values upstream either (false)."""
    state = _frequency_state(c)
    if state is None:
        return None
    entries = ((c.get("frequent_values") or {}).get("values") or ()) if state else ()
    return any(needle in str(e.get("value") or "") for e in entries)


def _frequent_not_null(c: _Col) -> bool | None:
    state = _frequency_state(c)
    return None if state is None else bool(state and (c.get("frequent_values") or {}).get("values"))


def _frequency_state(c: _Col):
    """True: the analysis was read; False: the column is not eligible for it (no frequent values, a real absence); None: it
    is eligible but not in the row (profiled before step 8b, or its read did not finish)."""
    from resource_explorer.surveyors.database.quality import frequent_values

    row = {**c.p, "state": c.p.get("state") or "measured"}
    if not frequent_values.eligible(row):
        return False if c.get("distinct_value_ct") is not None and c.get("max_length") is not None else None
    if c.get("frequency_state") == frequent_values.READ:
        return True
    c.missed.add("frequent_values")
    return None


def _suggestion_is_varchar(c: _Col) -> bool | None:
    """Whether `LOWER(datatype_suggestion) SIMILAR TO '(%varchar%)'` holds for a text column, from the first pass of
    `datatype_suggestions.sql` (the label is still NULL then, so its State / Boolean / Measurement Pct arms cannot fire).
    TestGen's `column_type` for a Postgres text column is `varchar(n)` (`text` and an unsized `character varying` are
    `varchar(65535)`) or `char(n)`; a table of 500 rows or fewer, or a name ending in "id", keeps it unchanged; above that the
    arms below suggest DECIMAL, INTEGER, BIGINT, TIMESTAMP(Z) or DATE for text that is all numbers or dates (they read the
    frequent values), else a VARCHAR. None when the answer depends on a frequent value the profile does not hold."""
    declared = (c.get("catalog_type") or "").lower().strip()
    if not declared:
        return None
    base_varchar = declared == "text" or "varchar" in declared or "varying" in declared
    gate = _and(_cmp(c.get("record_ct"), ">", 500), re.search("id$", c.name, re.IGNORECASE) is None)
    if gate is False:
        return base_varchar
    out: set[bool] = set() if gate is True else {base_varchar}
    vc, zl, ml = c.get("value_ct"), c.get("zero_length_ct"), c.get("max_length")
    numeric_all = _and(_cmp(c.get("numeric_ct"), ">", 0), None if None in (vc, c.get("numeric_ct"), zl) else
                       vc == c.get("numeric_ct") + zl) if c.ts != "unread" else None
    date_all = _and(_cmp(c.get("date_ct"), ">", 0), None if None in (vc, c.get("date_ct"), zl) else
                    vc == c.get("date_ct") + zl) if c.ts != "unread" else None
    if c.ts == "unread":
        c.missed.add("sample_value_ct")
    dot = _frequent_like(c, ".")
    fv_present = _frequent_not_null(c)
    arms = [
        (_and(re.search("zip", c.name, re.IGNORECASE) is not None, _cmp(ml, "<=", 10)), True),
        (_and(numeric_all, dot), False),
        (_and(numeric_all, _cmp(ml, "<=", 6), fv_present, _not(dot)), False),
        (_and(numeric_all, _cmp(ml, ">", 6), fv_present, _not(dot)), False),
        (_and(date_all, _frequent_like(c, "+")), False),
        (_and(date_all, _frequent_like(c, ":")), False),
        (date_all, False),
        (_cmp(ml, "<=", 5), True),
        (None if ml is None else True, True),
    ]
    decided = False
    for cond, is_varchar in arms:
        if cond is True:
            out.add(is_varchar)
            decided = True
            break
        if cond is None:
            out.add(is_varchar)
    if not decided:
        out.add(base_varchar)
    return True if out == {True} else False if out == {False} else None


def _code_category_results(c: _Col) -> set:
    """The values of the CASE at upstream 412-448 (a column whose label is still NULL): Measurement Text, then ID or Code by
    shape, then Attribute or Description by length and words, then Code or Category; `KEEP` where no arm fires."""
    name_ok = any(rx.search(c.name.lower()) for rx in _MEASURE_TEXT_NAME)
    arm1 = _and(name_ok, c.tcond(lambda nc, vc_: nc == vc_ and vc_ > 1, "numeric_ct", "value_ct"))
    vc, rc, dv = c.get("value_ct"), c.get("record_ct"), c.get("distinct_value_ct")
    populated = None if not rc or vc is None else vc / rc > 0.8
    unique = None if not vc or dv is None else dv / vc > 0.95
    short_unspaced = _and(c.tcond(lambda ml, es: ml <= 20 and es < 0.1, "max_length", "avg_embedded_spaces"), populated, unique)
    consistent = c.tcond(lambda es, ml, al, mn: es < 0.1 and (round(ml - al) <= 1 or round(al - mn) <= 1),
                         "avg_embedded_spaces", "max_length", "avg_length", "min_length")
    arm2 = _and(c.tcond(lambda dg: dg > 0, "includes_digit_ct"), _sql_or(short_unspaced, consistent))
    inner2 = case_set([(_cmp(dv, ">", 200), "ID"),
                       (_and(_cmp(dv, "<=", 200), c.tcond(lambda es: es < 1, "avg_embedded_spaces")), "Code")], KEEP)
    varies = c.tcond(lambda ml, al, mn: ml - round(al) > 1 and round(al) - mn > 1, "max_length", "avg_length", "min_length")
    attribute = c.tcond(lambda es, ml, mt: 0 <= es <= 3 and ml <= 30 and mt.count(" ") < 5,
                        "avg_embedded_spaces", "max_length", "max_text")
    inner3 = case_set([(varies, "__varies__")], KEEP)
    if "__varies__" in inner3:
        inner3.discard("__varies__")
        inner3 |= case_set([(attribute, "Attribute")], "Description")
    short = c.tcond(lambda es, ml: es < 1 and ml < 15, "avg_embedded_spaces", "max_length")
    code_by_pattern = _and(_pattern_like(c, "A"), _pattern_like(c, "N"))
    inner4 = case_set([(_sql_or(short, code_by_pattern), "Code")], "Category")
    out: set = set()
    for cond, results in ((arm1, {"Measurement Text"}), (arm2, inner2), (_cmp(dv, ">", 200), inner3),
                          (_between(dv, 2, 200), inner4)):
        if cond is True:
            return out | results
        if cond is None:
            out |= results
    return out | {KEEP}


def _pattern_like(c: _Col, letter: str) -> bool | None:
    """`fn_frequent_like(frequent_patterns, '%<letter>%')`: some stored pattern contains it (NULL patterns: false)."""
    if c.ts == "unread":
        c.missed.add("sample_value_ct")
        return None
    import json

    stored = c.get("frequent_patterns")
    if isinstance(stored, str):
        try:
            stored = json.loads(stored)
        except ValueError:
            return False
    values = (stored or {}).get("values") if isinstance(stored, dict) else None
    return any(letter in (v.get("value") or "") for v in values or ())


def _tail(c: _Col) -> set:
    """The last CASE (472-514), for a column nothing has labelled yet: Sequence, an ID/Attribute by name, a Spike, a
    discrete or continuous Measurement, else 'UNKNOWN'."""
    gt, vc, rc, dv = c.gt, c.get("value_ct"), c.get("record_ct"), c.get("distinct_value_ct")
    lo, hi = c.get("min_value"), c.get("max_value")
    one_b = _profiled_by_1b(c.p)
    fs = c.get("fractional_sum")
    seq_span = None if lo is None or hi is None or dv is None else hi - lo + 1 == dv
    if one_b is None:
        seq_span = None
    frac_ok = _sql_or(fs is None if one_b else None, None if _num(fs) is None else _num(fs) > 0)
    sequence = _and(seq_span, frac_ok) if gt == "N" or one_b is None else False
    is_n = gt == "N"
    id_by_name = _and(is_n, _similar(c.name.lower(), "%(no|num|number|nbr)"),
                      _sql_or(_integer_type(c), _and(_scale_gt_zero(c), _fractional_sum_zero(c))))
    id_pct = None if rc in (None, 0) or vc is None else round(100.0 * vc / rc)
    id_results = case_set([(_cmp(id_pct, ">", 70), "ID")], "Attribute-Numeric")
    spike = _spike(c)
    discrete = _and(is_n, _sql_or(_integer_type(c), _scale_is_zero(c), _and(_scale_gt_zero(c), _fractional_sum_zero(c))))
    avg, sd = c.get("avg_value"), c.get("stdev_value")
    # stdev_value / NULLIF(avg_value, 0): a zero mean makes the quotient NULL, so that arm is FALSE (a real NULL, not a
    # missing measure); a missing stdev or mean leaves it unknown
    ratio = None if sd is None or avg is None or avg == 0 else sd / avg
    cv_arm = False if avg == 0 else _cmp(ratio, ">=", 0.10)
    measure = _sql_or(_and(is_n, _cmp(dv, ">", 1), _cmp(lo, "<", 0)),
                      _and(is_n, _cmp(dv, ">", 1), _cmp(lo, ">=", 0), cv_arm))
    out: set = set()
    for cond, results in ((sequence, {"Sequence"}), (id_by_name, id_results), (spike, {"Measurement Spike"}),
                          (discrete, {"Measurement Discrete"}), (measure, {"Measurement"})):
        if cond is True:
            out |= results
            return out
        if cond is None:
            out |= results
    out.add("UNKNOWN")
    return out


def _measurement_pct_code(c: _Col) -> bool | None:
    """Upstream 583-602's second statement: a 'Code' column of at most three patterns, every value with a digit, between
    '0' and '99', whose top patterns are all of the form N, NN or NNN with an optional decimal and a percent sign."""
    rx = re.compile(r"^N{1,3}(\.N+)?%$")
    dp = c.get("distinct_pattern_ct")

    def pattern_ok(n: int):
        top = _nth_pattern(c.get("frequent_patterns"), n)
        return (top is not None and rx.search(top.strip()) is not None) or (dp is not None and dp < n)

    head = c.tcond(lambda dpc, vc_, dg, mn, mx, t: 1 <= dpc <= 3 and vc_ == dg and mn >= "0" and mx <= "99"
                   and rx.search(t.strip()) is not None, "distinct_pattern_ct", "value_ct", "includes_digit_ct",
                   "min_text", "max_text", "_top1")
    if head is not True:
        return head
    return pattern_ok(2) and pattern_ok(3)


def _stage_b(s: set, c: _Col, foreign_key: bool | None) -> set:
    """ID-FK (569-579) then Measurement Pct (583-602). `foreign_key`: another table has a column of this name that is a
    unique ID (True), might (None), or does not (False)."""
    if foreign_key is not False:
        out = set()
        for v in s:
            if v not in ("ID-Unique", "ID-Unique-SK") and foreign_key is True:
                out.add("ID-FK")
            else:
                out.add(v)
                if foreign_key is None and v not in ("ID-Unique", "ID-Unique-SK"):
                    out.add("ID-FK")
        s = out
    lo, hi, name = c.get("min_value"), c.get("max_value"), c.name
    pct_name = bool(re.search("pct|percent", name, re.IGNORECASE))
    pct_where = _and(c.gt == "N", _cmp(lo, ">=", -200), _cmp(hi, "<=", 200), pct_name)
    out = set()
    for v in s:
        if v in ("Measurement", "Measurement Discrete", "UNKNOWN") and pct_where is not False:
            out.add("Measurement Pct")
            if pct_where is None:
                out.add(v)
        else:
            out.add(v)
    s = out
    if "Code" in s and c.text:
        pct_text = _measurement_pct_code(c)
        if pct_text is True:
            s = {("Measurement Pct" if v == "Code" else v) for v in s}
        elif pct_text is None:
            s = s | {"Measurement Pct"}
    return s


def infer_batch(rows: list[dict], *, now: datetime | None = None) -> dict[tuple, Inference]:
    """The inference for every column of one value-profile batch, keyed (schema, table, column). `rows` are the profile
    rows, each with `catalog_type` (and for a number `numeric_scale`) from the stored inventory. A column that was not
    profiled has no inference (`possible` is every label)."""
    now = now or datetime.now(UTC).replace(tzinfo=None)
    cols = {(r["schema_name"], r["table_name"], r["column_name"]): _Col(r) for r in rows}
    by_pos = {(r["schema_name"], r["table_name"], r["position"]): r for r in rows if r.get("position") is not None}
    stage_a: dict[tuple, tuple[set, set]] = {}
    for key, c in cols.items():
        if (c.get("state") or "measured") != "measured":
            continue
        stage_a[key] = _stage_a(c, now, by_pos)
    unique_names: dict[str, set[tuple[str, str]]] = defaultdict(set)
    maybe_names: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for (schema, table, column), (s, _u) in stage_a.items():
        if s & {"ID-Unique", "ID-Unique-SK"}:
            (unique_names if s <= {"ID-Unique", "ID-Unique-SK"} else maybe_names)[column].add((schema, table))
    out: dict[tuple, Inference] = {}
    for key, c in cols.items():
        if key not in stage_a:
            continue
        s, unknown = stage_a[key]
        schema, table, column = key
        others_sure = unique_names.get(column, set()) - {(schema, table)}
        others_maybe = maybe_names.get(column, set()) - {(schema, table)}
        fk = True if others_sure else None if others_maybe else False
        final = _stage_b(s, c, fk)
        reasons = _reasons(final, c, unknown)
        out[key] = Inference(frozenset(final), tuple(sorted(c.missed)), tuple(reasons))
    return out


def _reasons(final: set, c: _Col, unknown: set[str]) -> list[str]:
    if len(final) == 1:
        return []
    needed = sorted(m for m in c.missed if m in NOT_TAKEN)
    if c.text and (needed or "sample_value_ct" in c.missed):
        said = []
        if "sample_value_ct" in c.missed:
            said.append(TEXT_SHAPE_UNREAD)
        if needed:
            said.append(f"needs {', '.join(needed)}, which the profile does not take "
                        f"({'; '.join(sorted({NOT_TAKEN[m] for m in needed}))})")
        return ["a text column; " + "; ".join(said)]
    if _profiled_by_1b(c.p) is None:
        return ["profiled before profile 1b: run the value profile again"]
    if needed:
        return [f"needs {', '.join(needed)}, which the profile does not take"]
    return [f"statements unresolved: {', '.join(sorted(unknown))}"] if unknown else ["undetermined"]


# ── what a selection rule asks ────────────────────────────────────────────────────────────────────────────────

def label_is(possible, label: str) -> bool | None:
    return label_in(possible, lambda v: v == label)


# ── the table type ────────────────────────────────────────────────────────────────────────────────────────────

#: TestGen calls a table 'cumulative' when its record count never fell between two profile runs. With a single run
#: nothing has been compared, and upstream's SUM over a NULL difference makes the first run 'cumulative'. **Decision
#: (project owner, 2026-10-29):** "keep TestGen's cumulative": a table with one measured profile is 'cumulative', as
#: upstream, so Dec_Trunc, Distinct_Date_Ct and the other cumulative checks are considered on a first profile. Set
#: False to read one point as "not inferred" (the earlier build). A table with NO measured profile has no period.
SINGLE_PROFILE_IS_CUMULATIVE = True


def table_period(record_cts: list[int | None]) -> tuple[str | None, str]:
    """('cumulative' | 'window' | None, why) from the table's record counts over its stored profile runs, oldest first.
    A run that did not measure the table (None) is skipped, not read as 0."""
    counts = [n for n in record_cts if n is not None]
    if len(counts) < 2:
        if SINGLE_PROFILE_IS_CUMULATIVE and counts:
            return "cumulative", ""
        return None, (f"the table has {len(counts)} measured profile run{'s' if len(counts) != 1 else ''}; "
                      f"cumulative or window needs at least 2")
    return ("window" if any(b < a for a, b in itertools.pairwise(counts)) else "cumulative"), ""


def table_kind(labels: list[Inference]) -> tuple[str | None, str]:
    """The table_type half of `functional_tabletype_stage.sql`, from the columns' labels. Only when every column's
    label is one label: the counts it reads cannot be taken from a set."""
    if not labels:
        return None, "no column of the table was profiled"
    if any(i.label is None for i in labels):
        n = sum(1 for i in labels if i.label is None)
        return None, f"{n} of {len(labels)} columns have no inferred type"
    names = [i.label for i in labels]

    def count(test) -> int:
        return sum(1 for v in names if test(v))
    measures = count(lambda v: v.lower().startswith("measure"))
    non_constant = count(lambda v: v != "Constant")
    ids_or_cat = count(lambda v: v.lower().startswith("id") or v == "Category")
    periods = count(lambda v: v.lower().startswith("period"))
    if ids_or_cat > 0 and ((periods > 0 and measures > 0) or (non_constant and measures / non_constant > 0.4)):
        return "summary", ""
    if measures > 0 and count(lambda v: "transactional date" in v.lower()) > 0:
        return "transaction", ""
    if (count(lambda v: v in ("Entity Name", "Person Last Name", "Person Given Name", "Person Full Name")) > 0
            and count(lambda v: v in ("Address", "City", "State")) > 1):
        return "entity", ""
    if (count(lambda v: v in ("ID-Unique", "ID-Unique-SK", "ID-Secondary")) > 1
            and count(lambda v: v in ("Attribute", "Description")) <= 1 and measures <= 1):
        return "bridge", ""
    return "domain", ""


def functional_table_type(period: str | None, kind: str | None) -> str | None:
    """`table_period || '-' || table_type` as upstream stores it. When only the period is known, just the period
    ('cumulative'): the selection rules read `LIKE '%cumulative%'` and `NOT LIKE '%window%'`, which a period alone
    answers. No period means no value: the type half is meaningless without it."""
    if period is None:
        return None
    return f"{period}-{kind}" if kind else period


# ── applying it to a profile batch ────────────────────────────────────────────────────────────────────────────

def _inference_rows(rows: list[dict], catalog_types: dict[tuple, str],
                    scales: dict[tuple, tuple[str, int | None]] | None,
                    positions: dict[tuple, int] | None = None) -> list[dict]:
    """Copies of the profile rows with the two inventory facts the inference reads: the column's catalog type, and for a
    number its numeric scale (the profile's own catalog read; None when it was not read)."""
    out = []
    for r in rows:
        key = (r["schema_name"], r["table_name"], r["column_name"])
        extra = {"catalog_type": catalog_types.get(key)}
        if positions is not None and key in positions:
            extra["position"] = positions[key]
        if scales is not None and key in scales:
            extra["numeric_scale"] = scales[key][1]
        out.append({**r, **extra})
    return out


def label_rows(rows: list[dict], *, catalog_types: dict[tuple, str],
               scales: dict[tuple, tuple[str, int | None]] | None,
               history: dict[tuple[str, str], list[int | None]], now: datetime | None = None,
               positions: dict[tuple, int] | None = None) -> dict[tuple, Inference]:
    """Fill `functional_data_type` and `functional_table_type` on a batch of freshly measured profile rows, in place.
    A column the inference could not settle stays NULL (and says why through `infer_batch` at read time); a table whose
    period cannot be told (fewer than two profile runs) stays NULL.

    `history` is each table's record_ct over the stored batches BEFORE this one; this batch's own count is added."""
    inferred = infer_batch(_inference_rows(rows, catalog_types, scales, positions), now=now)
    by_table: dict[tuple[str, str], list[Inference]] = defaultdict(list)
    counts: dict[tuple[str, str], int | None] = {}
    for r in rows:
        key = (r["schema_name"], r["table_name"], r["column_name"])
        inference = inferred.get(key)
        r["functional_data_type"] = inference.label if inference else None
        r["functional_table_type"] = None
        if inference is not None:
            by_table[(key[0], key[1])].append(inference)
            counts[(key[0], key[1])] = r.get("record_ct")
    for table, labels in by_table.items():
        period, _why = table_period([*history.get(table, []), counts.get(table)])
        kind, _kind_why = table_kind(labels)
        value = functional_table_type(period, kind)
        for r in rows:
            if (r["schema_name"], r["table_name"]) == table:
                r["functional_table_type"] = value
    return inferred


def effective(profile: dict, inference: Inference | None) -> tuple[frozenset | None, str]:
    """What a selection rule may assume about a STORED profile row's type: (the labels it could be, the reason when that
    is not one label). The stored label is the record of what that profile concluded and is used as it is. A row whose
    label is NULL is read from its stored measures again: if that still cannot settle one label, the rule sees the set it
    can narrow (so a type that is definitely not the one it asks for reads False); if it now could, the row is from
    before the inference was run on it, and its type is unknown until the profile is taken again."""
    stored = profile.get("functional_data_type")
    if stored is not None:
        return frozenset({stored}), ""
    if inference is None:
        return None, "type not inferred · the column was not profiled"
    if inference.label is not None:
        return None, "type not inferred · profiled before type inference existed: run the value profile again"
    return inference.possible, inference.why
