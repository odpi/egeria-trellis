"""The sampled text-shape measures of a text column (port step 7, Q4 §5 row 7: profile slice 2, families D and B1c).

TestGen reads these through a `TABLESAMPLE BERNOULLI (p) REPEATABLE (64)` block, and only these: counts, nulls, distinct
counts and bounds stay whole-table (DESIGN-QUALITY-AT-SCALE-AND-TESTGEN.md 2.3). The vendored blocks are
`configdata/quality/testgen/postgresql/text_shape.sql`; each is pinned here by SHA-256 and checked against the upstream
clone by `scripts/sync_quality_catalog.py`. The four whole-table text measures (`zero_value_ct`, `distinct_std_value_ct`,
`min_text`, `max_text`) are in the batched whole-table scan (`profile_measures.sql`, flag `is_A_text`), not here.

What is ported, and what is not (DESIGN-QUALITY-STEP-7-SAMPLED-TEXT-SHAPE.md):

- The sampling rule is TestGen's `calculate_sampling_params`: a table of `SAMPLE_MIN_COUNT` rows or fewer is read whole
  (no `sample_ratio`); above it the sample is `SAMPLE_PERCENT` % of the rows, never below `SAMPLE_MIN_COUNT` nor above
  `SAMPLE_MAX_COUNT` rows, and `sample_ratio` is the table's rows over the sample's size. The seed is `REPEATABLE (64)`.
- A count taken on a sample is lifted onto the whole-table scale as upstream's `calibrate_sampled_counts` does, so a
  calibration against TestGen on the same physical table compares like with like. The un-lifted size of what was read is
  kept (`sample_value_ct`, `sample_pattern_value_ct`), and a column read on a sample carries its `sample_ratio`: every
  such measure is an ESTIMATE, and a 0 in it means "none in the sample", never "none in the table".
- `zero_length_ct` and `filled_value_ct` are in upstream's sampled block too. RE keeps reading them over the whole table
  (slice 1, published, unchanged), so the two sampled copies in this statement are discarded.

A column whose statement could not run keeps NULL in every one of these measures, with the reason in the run summary
(`text_shape_not_measured`): NULL is "not measured", never a count of 0.
"""
from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from typing import Any

from resource_explorer.surveyors.database.quality.catalogue import (
    POSTGRES_DIR,
    expand_templated_functions,
    quote_identifier,
    sha256_text,
)
from resource_explorer.surveyors.database.quality.value_profile import (
    ProfileBounds,
    begin_read,
    classify_error,
    error_text,
)

TEXT_SHAPE_SQL = POSTGRES_DIR / "text_shape.sql"
#: SHA-256 of each vendored block (its lines after the `-- upstream lines` header, with a final newline).
TEXT_SHAPE_SHA256 = {
    "sampling_rows": "54709db340b3623ec82aa7aae3acbb8df753208b539d225d67cc0b034fea344c",
    "no_sampling_rows": "b84183f1841dd9264dda2ccb96a25246ecbbacb2e46016049614621cb26e4682",
    "sampleable_measures": "e33ab647cd6d06b967f05a1ead114ff819ba5dcbfc742df97680a88a26b32014",
    "pattern_measures": "ebe633c2b4f1504c414209a8c5768cfd1505b3504949d7e9f68d9972428b9dea",
}

#: TestGen's table-group defaults (`profile_sample_percent`, `profile_sample_min_count`) and the ceiling in
#: `calculate_sampling_params`; `ProfilingSQL.max_pattern_length`.
SAMPLE_PERCENT = 30.0
SAMPLE_MIN_COUNT = 100_000
SAMPLE_MAX_COUNT = 999_000
MAX_PATTERN_LENGTH = 25
PATTERN_SLOTS = 5

#: The measures a text column's sampled statement stores, under the vendored names.
COUNT_MEASURES = ("lead_space_ct", "quoted_value_ct", "includes_digit_ct", "upper_case_ct", "lower_case_ct",
                  "non_alpha_ct", "non_printing_ct", "numeric_ct", "date_ct", "embedded_space_ct")
#: Taken on the sample and stored as measured (upstream's SAMPLEABLE, not SAMPLEABLE_COUNT).
UNLIFTED_MEASURES = ("avg_embedded_spaces", "std_pattern_match", "distinct_pattern_ct")
SIZE_MEASURES = ("sample_value_ct", "sample_pattern_value_ct")
TEXT_SHAPE_MEASURES = (COUNT_MEASURES + UNLIFTED_MEASURES + SIZE_MEASURES + ("frequent_patterns",))
#: The whole-table text measures taken in the batched scan.
WHOLE_TEXT_MEASURES = ("distinct_std_value_ct", "min_text", "max_text")
CASE_CLASS_MEASURES = ("upper_case_ct", "lower_case_ct", "non_alpha_ct")


# ── the sampling rule ──────────────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TableSampling:
    sample_count: int
    sample_ratio: float
    sample_percent: float


def sampling_for(record_ct: int | None, *, percent: float | None = None, min_count: int | None = None,
                 max_count: int | None = None) -> TableSampling | None:
    """TestGen's `calculate_sampling_params`: None when the table is read whole (a table of `min_count` rows or fewer,
    an unknown count, or a percent outside 0-100). The settings default to the module's, read at call time."""
    percent = SAMPLE_PERCENT if percent is None else percent
    min_count = SAMPLE_MIN_COUNT if min_count is None else min_count
    max_count = SAMPLE_MAX_COUNT if max_count is None else max_count
    if record_ct is None or not (0 < percent < 100) or record_ct <= min_count:
        return None
    calc = round(percent * record_ct / 100)
    count = min(max(calc, min_count), max_count)
    return TableSampling(sample_count=count, sample_ratio=record_ct / count,
                         sample_percent=round(100 * count / record_ct, 4))


# ── the statement ──────────────────────────────────────────────────────────────────────────────────────────────

def vendored_blocks() -> dict[str, str]:
    """{block name: text} from `text_shape.sql`: the lines after each `-- upstream lines A-B (name)` header."""
    blocks: dict[str, str] = {}
    name = None
    buf: list[str] = []
    for line in TEXT_SHAPE_SQL.read_text(encoding="utf-8").splitlines():
        header = re.match(r"-- upstream lines \d+-\d+ \((\w+)\)", line)
        if header:
            if name:
                blocks[name] = "\n".join(buf).strip("\n") + "\n"
            name, buf = header.group(1), []
        elif name is not None:
            buf.append(line)
    if name:
        blocks[name] = "\n".join(buf).strip("\n") + "\n"
    return blocks


def pins_match() -> list[str]:
    """The names of vendored blocks whose text no longer matches the pin (empty when all do)."""
    blocks = vendored_blocks()
    return [n for n, digest in TEXT_SHAPE_SHA256.items() if sha256_text(blocks.get(n)) != digest]


def build_statement(schema: str, table: str, column: str, sampling: TableSampling | None) -> str:
    """One text column's sampled read, assembled from the vendored blocks. The identifiers are the inventory's, quoted;
    `{SAMPLE_PERCENT_CALC}` is a number RE computed."""
    blocks = vendored_blocks()
    rows = blocks["sampling_rows" if sampling else "no_sampling_rows"].lstrip()
    if not rows.startswith(", "):
        raise ValueError("text_shape.sql: the sampled-rows block no longer starts with a comma")
    text = ("WITH " + rows[2:] + blocks["sampleable_measures"] + blocks["pattern_measures"]
            + "SELECT * FROM sampleable_measures CROSS JOIN pattern_measures")
    text = expand_templated_functions(text)
    text = (text.replace('"{DATA_SCHEMA}"."{DATA_TABLE}"', f"{quote_identifier(schema)}.{quote_identifier(table)}")
            .replace('"{COL_NAME}"', quote_identifier(column))
            .replace("{MAX_PATTERN_LENGTH}", str(MAX_PATTERN_LENGTH)))
    if sampling:
        text = text.replace("{SAMPLE_PERCENT_CALC}", repr(float(sampling.sample_percent)))
    if re.search(r"\{(COL_NAME|DATA_SCHEMA|DATA_TABLE|SAMPLE_PERCENT_CALC|MAX_PATTERN_LENGTH)\}", text):
        raise ValueError("text_shape.sql: a placeholder was left unfilled")
    return text


# ── lifting a sample's counts onto the table's scale (upstream's calibrate_sampled_counts) ─────────────────────

def _as_decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number if number.is_finite() else None


def _lift(count: Any, whole: Decimal, sampled: Decimal) -> int | None:
    number = _as_decimal(count)
    if number is None:
        return None
    return int((number * whole / sampled).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _lift_partition(counts: list[Decimal], whole: Decimal, sampled: Decimal) -> list[int]:
    """Scale counts that add up to `sampled` so they add up to `whole`: each gets the integer part of its share and the
    units still missing go one each to the largest fractional parts, earlier counts first on a tie."""
    numerators = [int(c) * int(whole) for c in counts]
    lifted = [n // int(sampled) for n in numerators]
    shortfall = int(whole) - sum(lifted)
    by_remainder = sorted(range(len(counts)), key=lambda i: numerators[i] % int(sampled), reverse=True)
    for i in by_remainder[:shortfall]:
        lifted[i] += 1
    return lifted


def frequent_patterns(row: MutableMapping[str, Any]) -> dict[str, Any] | None:
    """Upstream's `build_frequent_patterns`: the five numbered pattern slots as {"values": [{"value", "ct"}, ...]}, most
    frequent first. A slot with a pattern and no count (or the reverse) is discarded whole."""
    values = []
    for slot in range(PATTERN_SLOTS):
        pattern, count = row.get(f"pattern_{slot}"), row.get(f"pattern_ct_{slot}")
        if pattern is None or count is None:
            continue
        values.append({"value": str(pattern).replace("\x00", ""), "ct": int(count)})
    return {"values": values} if values else None


def lift_sampled_counts(row: MutableMapping[str, Any], whole_value_ct: Any) -> None:
    """Upstream's `calibrate_sampled_counts`, in place, on a row holding the statement's raw result plus `frequent_patterns`
    (a dict). `whole_value_ct` is the column's whole-table value count. The case counts are shared out by largest
    remainder so they and the mixed-case values add up to `whole_value_ct`; every other count is multiplied by
    whole / sampled and rounded half up. A sample that held no values while the table did gives NULL counts."""
    whole = _as_decimal(whole_value_ct)
    sampled = _as_decimal(row.get("sample_value_ct"))
    pattern_sampled = _as_decimal(row.get("sample_pattern_value_ct"))
    if whole is None or sampled is None:
        return
    with localcontext() as context:
        context.prec = 60
        patterns = row.get("frequent_patterns")
        if patterns and pattern_sampled and pattern_sampled != whole:
            for entry in patterns["values"]:
                entry["ct"] = _lift(entry["ct"], whole, pattern_sampled)
        if whole == sampled:
            return
        if not sampled:
            for column in COUNT_MEASURES:
                row[column] = None
            return
        case_classes = [_as_decimal(row.get(c)) for c in CASE_CLASS_MEASURES]
        counts = list(COUNT_MEASURES)
        if None not in case_classes:
            mixed = sampled - sum(case_classes)
            lifted = _lift_partition([*case_classes, mixed], whole, sampled)
            row.update(zip(CASE_CLASS_MEASURES, lifted[:-1], strict=True))
            counts = [c for c in counts if c not in CASE_CLASS_MEASURES]
        for column in counts:
            row[column] = _lift(row.get(column), whole, sampled)


def _clean(value: Any, measure: str) -> Any:
    if value is None:
        return None
    if measure in ("std_pattern_match",):
        return str(value)
    if measure == "avg_embedded_spaces":
        return float(value)
    return int(value)


def measure_column(conn, schema: str, table: str, column: str, record_ct: int | None, whole_value_ct: int | None,
                   bounds: ProfileBounds, remaining_seconds: float) -> tuple[dict[str, Any], TableSampling | None]:
    """One text column's sampled measures: ({measure: value}, the sampling used). Raises on a failed statement; the
    caller reports it. One read-only transaction, ended at once, its timeout what is left of the budget."""
    sampling = sampling_for(record_ct)
    sql = build_statement(schema, table, column, sampling)
    try:
        begin_read(conn, replace(bounds, statement_timeout_seconds=max(1, math.ceil(remaining_seconds))))
        result = conn.execute_query_isolated(sql, None)
    finally:
        conn.end_transaction()
    if not result:
        raise RuntimeError("the text-shape query returned no row")
    raw = result[0]
    row: dict[str, Any] = {m: _clean(raw.get(m), m) for m in COUNT_MEASURES + UNLIFTED_MEASURES + SIZE_MEASURES}
    for slot in range(PATTERN_SLOTS):
        row[f"pattern_{slot}"] = raw.get(f"pattern_{slot}")
        row[f"pattern_ct_{slot}"] = raw.get(f"pattern_ct_{slot}")
    row["frequent_patterns"] = frequent_patterns(row)
    if sampling:
        lift_sampled_counts(row, whole_value_ct)
    patterns = row["frequent_patterns"]
    out = {m: row[m] for m in COUNT_MEASURES + UNLIFTED_MEASURES + SIZE_MEASURES}
    out["frequent_patterns"] = None if patterns is None else json.dumps(patterns, separators=(",", ":"))
    return out, sampling


def measure_table(conn, schema: str, table: str, columns: list[tuple[str, int | None]], record_ct: int | None,
                  bounds: ProfileBounds, clock: Callable[[], float] = time.monotonic
                  ) -> tuple[dict[str, tuple[dict[str, Any], TableSampling | None]], list[tuple[str, str]]]:
    """({column: (measures, sampling)}, [(column, reason)]) for a table's text columns `[(name, whole value_ct)]`,
    each its own statement inside one budget (`ProfileBounds.text_shape_budget_seconds`). A column the budget or an
    error stopped is in the reasons and absent from the answer, its measures NULL."""
    found: dict[str, tuple[dict[str, Any], TableSampling | None]] = {}
    gaps: list[tuple[str, str]] = []
    started = clock()
    budget = bounds.text_shape_budget_seconds
    for index, (column, value_ct) in enumerate(columns):
        remaining = budget - (clock() - started)
        if remaining <= 0:
            reason = f"exceeded {budget / 60:g} min · text shape not measured"
            gaps.extend((c, reason) for c, _ in columns[index:])
            break
        try:
            found[column] = measure_column(conn, schema, table, column, record_ct, value_ct, bounds, remaining)
        except Exception as exc:  # noqa: BLE001 - any failure of this optional statement is reported, never raised
            if classify_error(exc) != "not_permitted" and clock() - started >= budget:
                reason = f"exceeded {budget / 60:g} min · text shape not measured"
                gaps.extend((c, reason) for c, _ in columns[index:])
                break
            gaps.append((column, f"text shape not measured · {error_text(exc)}"))
    return found, gaps
