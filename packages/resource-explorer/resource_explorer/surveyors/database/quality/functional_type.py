"""The date branch of TestGen's functional-type inference, ported as data: what Recency's selection rule reads.

Recency's vendored `selection_criteria` excludes `functional_data_type IN ('Future Date', 'Schedule Date')`. That
label comes from upstream's `profiling/functional_datatype.sql` (TestGen 5.134.0, commit 3606859c): 629 lines of
ordered UPDATE statements over its profile. The statements that can label a DATE column are vendored verbatim, in
upstream order, in `configdata/quality/testgen/profiling/functional_datatype_date.sql`; this module is their port,
pinned to that file's SHA-256 (`VENDORED_SQL_SHA256`; `tests/test_quality_q6_recency.py` fails when it changes).

In upstream order, for a column of general_type 'D':

1. TBD: `record_ct = 0`, or `value_ct < 5`, or every value zero-length -> 'TBD (Not enough data)'.
2. Constant: one distinct value filling more than 75 % of the records -> 'Constant'.
3. By name, the table group's masks (TestGen's defaults `%_sk`, `%_id`, ILIKE) -> 'ID-SK', 'ID'.
4. The date CASE, first match wins: `before_20yr_date_ct / value_ct >= 75 %` 'Historical Date', then
   `future_date_ct / value_ct >= 95 %` 'Future Date', then `>= 50 %` 'Schedule Date'. Its later branches
   (the Transactional Date family, 'Date (TBD)', 'Date Stamp', 'DateTime Stamp') read `before_5yr_date_ct` and the
   date-days/weeks/months measures, which RE does not take yet; they are one outcome here, `LATER_DATE_BRANCH`.
5. Without the IS NULL guard, so they can relabel a Future or Schedule Date: the 'Process ' prefix (by name), the
   Period Month and Period Week overrides (by measures), 'Period Month' / 'Period Week' / 'Period' (by name), and
   'Process User' / 'System User' (by name).

Every date column ends with SOME label upstream (its last statement sets what is still NULL to 'UNKNOWN'), so
Recency's exclusion is never NULL there. Here it is NULL only when an input it needs was not measured: a profile
from before `before_20yr_date_ct` existed, or a Period override that reads a measure RE does not take
(`recency_excluded`). Absence is never read as "not a Future Date".
"""
from __future__ import annotations

import calendar
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROFILING_DIR = Path(__file__).resolve().parents[3] / "configdata" / "quality" / "testgen" / "profiling"
FUNCTIONAL_DATATYPE_SQL = PROFILING_DIR / "functional_datatype_date.sql"

#: SHA-256 of the vendored subset. A changed file fails the pin test until this port is re-read against it.
VENDORED_SQL_SHA256 = "6f9758e18842b6169029925c4c81a9bf7c27f0758e3e5606a091813232718483"

#: TestGen's table-group defaults (`settings.DEFAULT_PROFILING_ID_COLUMN_MASK` / `_SK_COLUMN_MASK`). RE has no
#: table groups, so it uses the defaults; they are ILIKE patterns, `_` included as a one-character wildcard.
PROFILE_ID_COLUMN_MASK = "%_id"
PROFILE_SK_COLUMN_MASK = "%_sk"

TBD = "TBD (Not enough data)"
CONSTANT = "Constant"
HISTORICAL_DATE = "Historical Date"
FUTURE_DATE = "Future Date"
SCHEDULE_DATE = "Schedule Date"
#: Any outcome of the date CASE after 'Schedule Date'. RE's own word, not one of TestGen's labels: those branches
#: are not ported (they need measures RE does not take), and none of them is a label Recency excludes.
LATER_DATE_BRANCH = "Date (later branch, not inferred)"

#: The labels Recency's selection rule excludes.
RECENCY_EXCLUDED = frozenset({FUTURE_DATE, SCHEDULE_DATE})

#: The inputs the inference reads from a profile row.
INPUTS = ("general_type", "column_name", "record_ct", "value_ct", "distinct_value_ct", "filled_value_ct",
          "zero_length_ct", "future_date_ct", "before_20yr_date_ct", "min_date", "max_date")

# Postgres `~` is a case-sensitive regex search. Upstream text, verbatim.
_PROCESS_DATE = (re.compile(r"^(last_?|system_?|)(add|create|insert|inrt|update|updt|mod|modif|modf|del|delete|refresh)"
                            r"(.{0,3}d?_?(time|tm|date|day|dt|stamp|timestamp|datestamp))$"),
                 re.compile(r"^(last_?|)(change|chg|update|updt|mod|modify|modf|modified|refresh|refreshed)$"))
_PROCESS_USER = re.compile(r"^(last_?|)(create|update|modif|delete|refresh)(.*?(by|id|name|nm|user|usr))$")
_SYSTEM_USER = re.compile(r"(user|usr)_?(name|nm)?$")


def _ilike(value: str, pattern: str) -> bool:
    """SQL ILIKE: `%` any run, `_` exactly one character, the whole value, ignoring case."""
    rx = "".join(".*" if ch == "%" else "." if ch == "_" else re.escape(ch) for ch in pattern)
    return re.fullmatch(rx, value, flags=re.IGNORECASE | re.DOTALL) is not None


def as_timestamp(value) -> datetime | None:
    """A stored date bound (`min_date`, `max_date`, a `surveyed_at`) as a naive UTC timestamp, the way upstream's
    `timestamp without time zone` columns hold it: a date is its midnight, an aware value is converted to UTC."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def datediff_day(first: datetime, second: datetime) -> int:
    """TestGen's metadata `datediff('DAY', first, second)`: `DATE_PART('day', second - first)`, the whole days of
    the interval, truncated toward zero (not a difference of calendar dates)."""
    seconds = (second - first) / timedelta(seconds=1)
    return int(seconds / 86400)


def _ratio_pct(count, value_ct) -> float | None:
    """`count / NULLIF(value_ct::FLOAT, 0) * 100`: NULL when value_ct is 0, as upstream."""
    return None if not value_ct else count / float(value_ct) * 100


def date_case_label(p: dict) -> str | None:
    """The label upstream's statements 1-4 (TBD, Constant, the ID masks, the date CASE) give a date column, or
    None when an input they read was not measured. Pure. Only for general_type 'D' (None for any other)."""
    if p.get("general_type") != "D":
        return None
    record_ct, value_ct = p.get("record_ct"), p.get("value_ct")
    if record_ct is None or value_ct is None:
        return None
    # 1. CASE WHEN record_ct = 0 ... WHEN record_ct > 0 and ((value_ct < 5 OR zero_length_ct / nullif(value_ct, 0)
    #    ::FLOAT = 1)). zero_length_ct is not measured on a date column, upstream either: its term is NULL there.
    if record_ct == 0:
        return TBD
    zero_length = p.get("zero_length_ct")
    if value_ct < 5 or (zero_length is not None and value_ct and zero_length / value_ct == 1):
        return TBD
    # 2. distinct_value_ct = 1 AND (((value_ct - coalesce(filled_value_ct, 0) - coalesce(zero_length_ct, 0))
    #    ::float/record_ct::float) * 100.00) > 75
    distinct = p.get("distinct_value_ct")
    if distinct is None:
        return None
    filled = (value_ct - (p.get("filled_value_ct") or 0) - (zero_length or 0)) / record_ct * 100.0
    if distinct == 1 and filled > 75:
        return CONSTANT
    # 3. column_name ILIKE :PROFILE_SK_COLUMN_MASK, then :PROFILE_ID_COLUMN_MASK
    name = p.get("column_name") or ""
    if _ilike(name, PROFILE_SK_COLUMN_MASK):
        return "ID-SK"
    if _ilike(name, PROFILE_ID_COLUMN_MASK):
        return "ID"
    # 4. The date CASE. value_ct >= 5 here, so the ratios are defined.
    before_20yr, future = p.get("before_20yr_date_ct"), p.get("future_date_ct")
    if before_20yr is None or future is None:
        return None
    if _ratio_pct(before_20yr, value_ct) >= 75:
        return HISTORICAL_DATE
    if _ratio_pct(future, value_ct) >= 95:
        return FUTURE_DATE
    if _ratio_pct(future, value_ct) >= 50:
        return SCHEDULE_DATE
    return LATER_DATE_BRANCH


def _process_prefixed(name: str) -> bool:
    """`general_type IN ('A', 'D') AND (column_name ~ ... OR column_name ~ ...)`: 'Process ' || the label."""
    return any(rx.search(name) for rx in _PROCESS_DATE)


def _month_aligned(ts: datetime) -> tuple[bool, bool]:
    """(first of its month at midnight, last of its month at midnight): `x = DATE_TRUNC('month', x)::DATE` and
    `x = (DATE_TRUNC('month', x) + INTERVAL '1 month' - INTERVAL '1 day')::DATE`, a timestamp against a date."""
    midnight = ts == datetime(ts.year, ts.month, ts.day)  # noqa: DTZ001 - naive UTC, as upstream's timestamp columns
    last = calendar.monthrange(ts.year, ts.month)[1]
    return midnight and ts.day == 1, midnight and ts.day == last


def _dow(ts: datetime) -> int:
    """`EXTRACT(DOW FROM x)`: Sunday 0 to Saturday 6."""
    return (ts.weekday() + 1) % 7


def _period_override_may_fire(p: dict) -> bool | None:
    """Whether upstream's Period Month or Period Week override (statements with no IS NULL guard) could fire. Both
    also read date_days/weeks/months_present, which RE does not measure, so this answers only the part RE can:
    False when their date-bound conditions already fail, True when they hold (the rest is then unknown), None when
    a bound is missing."""
    lo, hi = as_timestamp(p.get("min_date")), as_timestamp(p.get("max_date"))
    if lo is None or hi is None:
        return None
    (lo_first, lo_last), (hi_first, hi_last) = _month_aligned(lo), _month_aligned(hi)
    period_month = (lo_first and hi_first) or (lo_last and hi_last)
    period_week = _dow(lo) in (0, 1, 5, 6) and _dow(lo) == _dow(hi)
    return period_month or period_week


def recency_excluded(p: dict) -> bool | None:
    """`functional_data_type IN ('Future Date', 'Schedule Date')` for a date column, as upstream's whole file would
    leave it: True, False, or None when that cannot be told from what RE measured. Pure."""
    label = date_case_label(p)
    if label is None:
        return None
    name = p.get("column_name") or ""
    if _process_prefixed(name):
        label = "Process " + label
    if label not in RECENCY_EXCLUDED:
        return False                 # every later statement only sets labels Recency does not exclude
    if name.lower() in ("month", "week", "period"):
        return False                 # 'Period Month' / 'Period Week' / 'Period', by name
    if _PROCESS_USER.search(name) or _SYSTEM_USER.search(name):
        return False                 # 'Process User' / 'System User', by name, whatever the type
    may_fire = _period_override_may_fire(p)
    if may_fire is None or may_fire:
        return None                  # the override reads date-days/weeks/months measures RE does not take
    return True
