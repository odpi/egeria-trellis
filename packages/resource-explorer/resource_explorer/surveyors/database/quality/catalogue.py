"""The vendored quality-check catalogue: loading it, its selection rules as Python, and rendering its SQL.

The catalogue is DataKitchen TestGen's test-type YAML, copied unchanged into
`configdata/quality/testgen/` (PROVENANCE.md). Two parts of each file are used, in two different ways:

- **`selection_criteria`** is Postgres SQL over TestGen's own `profile_results`. RE's registry can be SQLite,
  so these fragments are ported to Python predicates (`PREDICATES`). Each predicate carries the SHA-256 of the
  fragment it was ported from; `tests/test_quality_catalogue.py` fails when the vendored text no longer
  matches, so adopting an upstream change forces a re-port instead of a silent divergence. The predicates use
  SQL's three-valued logic (`_and`, `_not`, `_cmp`): a rule that SQL would evaluate to NULL returns `None`
  here, which the propose step records as `not_established`, never as "no".
- **The Postgres `cat_test_conditions` entry** (`measure`, `test_operator`, `test_condition`) runs verbatim
  against the surveyed database, through `render_measure`, which validates every parameter by type before it
  is substituted. Nothing read from a person or from Egeria is ever executed as SQL.
"""
from __future__ import annotations

import csv
import hashlib
import io
import math
import operator
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from resource_explorer.surveyors.database.quality.functional_type import (
    INPUTS as INFERENCE_INPUTS,
)
from resource_explorer.surveyors.database.quality.functional_type import (
    as_timestamp,
    datediff_day,
    recency_excluded,
)

CATALOGUE_DIR = Path(__file__).resolve().parents[3] / "configdata" / "quality" / "testgen"
TEST_TYPES_DIR = CATALOGUE_DIR / "test_types"
POSTGRES_DIR = CATALOGUE_DIR / "postgresql"
GENERATION_TEMPLATES_DIR = CATALOGUE_DIR / "gen_query_tests"

#: The upstream commit the catalogue was copied from (PROVENANCE.md). Part of every proposal's catalogue_ref.
UPSTREAM_COMMIT = "3606859c"

#: Slice 1's seven test types.
SLICE_1_TEST_TYPES = (
    "Missing_Pct", "Required", "Required_No_Placeholder", "Unique", "Future_Date", "Min_Date", "Row_Ct_Pct",
)
#: Quality slice Q6 (DESIGN-QUALITY-Q6-RECENCY.md): Recency, whose rule reads the date branch of the
#: functional-type inference (`functional_type.py`), and Future_Date_1Y, which rides with it.
Q6_TEST_TYPES = ("Recency", "Future_Date_1Y")
#: Q4 port order step 2, B1a (DESIGN-QUALITY-B1A-BASELINE-MATCH.md): the Table Comparison types whose baseline is a
#: measure the value profile already takes. Proposed only for a table a person opted in (`table_comparison.py`).
B1A_TEST_TYPES = ("Match_Row_Ct", "Match_Distinct_Ct", "Match_Null_Ct", "Match_Zero_Length_Ct", "Match_Min_Date",
                  "Match_Max_Date")
#: Q4 port order step 3, profile 1b then B1b (DESIGN-QUALITY-PROFILE-1B.md): the Table Comparison types whose baseline
#: is a measure profile 1b adds, and Daily_Record_Ct (TestGen's Standard set, proposed without an opt-in). Match_Epoch_Sum
#: is not ported: upstream takes `epoch_sum` only for a column in its hashed set (step 8).
B1B_TEST_TYPES = ("Match_Min_Val", "Match_Max_Val", "Match_Sum", "Match_Stdev", "Match_Zero_Ct", "Match_Fractional_Sum",
                  "Match_Min_Length", "Match_Max_Length", "Match_Total_Length", "Match_True_Ct",
                  "Match_Distinct_Date_Ct", "Daily_Record_Ct")
#: Q4 port order step 4 (DESIGN-QUALITY-STEPS-4-5-DUPE-ROWS-AND-DRIFT.md): Dupe_Rows, a QUERY type with its own
#: statement and its own per-table time budget (`dupe_rows.py`).
STEP_4_TEST_TYPES = ("Dupe_Rows",)
#: Q4 port order step 5: the schema-drift checks, METADATA types that read what RE already stored from its surveys and
#: run no query against the surveyed database (`schema_drift.py`). Schema_Conformance is vendored but has no declared
#: schema in RE to compare with, so it never proposes and never concludes (see the note, section 5).
STEP_5_TEST_TYPES = ("Match_Column_Ct", "Match_Column_Type", "Schema_Conformance", "Schema_Drift")
#: Q4 port order step 6 (DESIGN-QUALITY-STEP-6-FUNCTIONAL-TYPE-INFERENCE.md): the types whose selection rules read the
#: functional-type inference (`functional_infer.py`) or the measures it needs. All CAT. Several can propose only once
#: profile slice 2 (text shape) lands: their rules read `not_established` until then, naming what is missing.
STEP_6_TEST_TYPES = ("Alpha_Trunc", "Avg_Shift", "Dec_Trunc", "Distinct_Date_Ct", "Distinct_Value_Ct", "Incr_Avg_Shift",
                     "LOV_Match", "Min_Val", "Monthly_Rec_Ct", "Outlier_Pct_Above", "Outlier_Pct_Below", "Pattern_Match",
                     "Unique_Pct", "Valid_Month", "Valid_US_Zip", "Valid_US_Zip3", "Variability_Decrease",
                     "Variability_Increase", "Weekly_Rec_Ct")
#: Q4 port order step 7 (DESIGN-QUALITY-STEP-7-SAMPLED-TEXT-SHAPE.md): the types that read a text column's sampled
#: text-shape measures (`text_shape.py`), families D and B1c. All CAT.
STEP_7_TEST_TYPES = ("Email_Format", "Street_Addr_Pattern", "US_State", "Valid_Characters", "Match_Lead_Space_Ct",
                     "Match_Upper_Case_Ct", "Match_Non_Printing_Ct")
#: Q4 port order step 8 (DESIGN-QUALITY-STEP-8-HASHES-AND-FREQUENT-VALUES.md): the Table Comparison types that read a column's value hashes
#: (`value_hashes.py`), which are taken only when the profile is asked for them. All CAT. Match_Frequent_Values,
#: Match_Frequent_Patterns and Match_Distinct_Hash are QUERY types and are not ported here (the note says why).
STEP_8_TEST_TYPES = ("Match_Hash_Text", "Match_Hash_Num", "Match_Hash_Date", "Match_Keyed_Hash_Text",
                     "Match_Keyed_Hash_Num", "Match_Keyed_Hash_Date", "Match_Epoch_Sum")
#: Port step 8b: the three Table Comparison QUERY types that compare a column's baseline text (`query_types.py`).
STEP_8B_TEST_TYPES = ("Match_Frequent_Values", "Match_Frequent_Patterns", "Match_Distinct_Hash")
#: Every test type RE has ported, in the order proposals are considered.
PORTED_TEST_TYPES = (SLICE_1_TEST_TYPES + Q6_TEST_TYPES + B1A_TEST_TYPES + B1B_TEST_TYPES + STEP_4_TEST_TYPES
                     + STEP_5_TEST_TYPES + STEP_6_TEST_TYPES + STEP_7_TEST_TYPES + STEP_8_TEST_TYPES
                     + STEP_8B_TEST_TYPES)

#: TestGen's `run_type` values: CAT is an aggregate in the per-table SELECT, QUERY is a statement of its own, METADATA
#: reads TestGen's own metadata tables (RE: its stored surveys).
RUN_CAT, RUN_QUERY, RUN_METADATA = "CAT", "QUERY", "METADATA"

#: TestGen's generation set for the baseline-match types. TestGen generates them only for a table group a person
#: opted into it; RE proposes them only for a table a person opted in (Q4 §7, question 2).
TABLE_COMPARISON = "Table Comparison"

#: The test type RE proposes on its own initiative: TestGen has no selection rule for it (manual-only there).
RE_PROPOSED = frozenset({"Row_Ct_Pct"})

#: TestGen's default_severity -> the ODCS/Egeria severity word a rule carries.
SEVERITY = {"Fail": "error", "Warning": "warning", "Log": "info"}

#: TestGen's data quality dimension -> the ODCS dimension word Egeria's `qualityDimension` carries
#: (`DataContractQualityDimension`: accuracy, completeness, conformity, consistency, coverage, timeliness,
#: uniqueness). Validity is ACCURACY, by the owner's ruling of 2026-10-20 ("conformity and validity are not
#: equivalent - accuracy is closer"; DESIGN-VALUE-PROFILE-AND-QUALITY-CHECKS.md §11 item 9), carried with
#: `originalDimension: Validity` until Egeria adds a `validity` value; Recency is Timeliness; the rest map by name.
#: INVENTORY-TESTGEN-TEST-TYPES-MAPPED.md (2026-10-21) says "Conformity" for Validity; it did not carry the ruling.
EGERIA_DIMENSION = {"Validity": "accuracy", "Recency": "timeliness"}
ODCS_DIMENSIONS = frozenset({"accuracy", "completeness", "conformity", "consistency", "coverage", "timeliness",
                             "uniqueness"})


def egeria_dimension(testgen_dimension: str) -> str:
    """The ODCS dimension word for a TestGen `dq_dimension`; "" when it is not one Egeria names."""
    word = EGERIA_DIMENSION.get(testgen_dimension, (testgen_dimension or "").strip().lower())
    return word if word in ODCS_DIMENSIONS else ""


def original_dimension(testgen_dimension: str) -> str:
    """TestGen's own dimension name when Egeria's word is a different one (Validity -> accuracy), for
    `additionalProperties.originalDimension`; "" when the word is the name itself or there is none."""
    word = egeria_dimension(testgen_dimension)
    return testgen_dimension if word and word != (testgen_dimension or "").strip().lower() else ""


#: TestGen's FAIL-WHEN operator -> the ODCS MUST-BE operator (its inverse), as Publish will write it (design §7).
MUST_BE = {
    ">": "mustBeLessOrEqualTo",
    ">=": "mustBeLessThan",
    "<": "mustBeGreaterOrEqualTo",
    "<=": "mustBeGreaterThan",
    "=": "mustNotBe",
    "<>": "mustBe",
    "NOT BETWEEN": "mustBeBetween",
}


@dataclass(frozen=True)
class Between:
    """The limit of a `NOT BETWEEN low AND high` condition (Match_Sum, Match_Stdev): the check fails outside it."""

    low: Any
    high: Any

    def __str__(self) -> str:
        return f"{self.low} AND {self.high}"


_FAIL_WHEN: dict[str, Callable[[Any, Any], bool]] = {
    ">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "=": operator.eq, "<>": operator.ne,
    "NOT BETWEEN": lambda measure, limit: not (limit.low <= measure <= limit.high),
}


@dataclass(frozen=True)
class TestType:
    """One vendored test type, as slice 1 reads it."""

    __test__ = False  # not a pytest test class, despite the name

    test_type: str
    id: str
    name_short: str
    description: str
    usage_notes: str
    dimension: str
    default_severity: str
    measure_uom: str
    test_scope: str
    selection_criteria: str | None
    default_parm_columns: tuple[str, ...]
    default_parm_values: str | None
    measure: str
    test_operator: str
    test_condition: str
    generation_sets: tuple[str, ...] = ()
    generation_template: str | None = None
    #: Upstream's flag: a NULL measure is a failure rather than a pass. Read by `run_quality_checks`.
    null_measure_fails: bool = False
    #: How upstream runs it: CAT (the default), QUERY (Dupe_Rows) or METADATA (the drift checks). Only a CAT type has
    #: a `cat_test_conditions` entry, so `measure`, `test_operator` and `test_condition` are empty for the others.
    run_type: str = RUN_CAT
    #: Upstream's `default_parm_prompts`: the words for each default parameter, in `default_parm_columns` order.
    default_parm_prompts: str = ""

    @property
    def severity(self) -> str:
        return SEVERITY.get(self.default_severity, "error")

    @property
    def origin(self) -> str:
        """`re` for a check RE proposes on its own initiative, `vendored` for one the catalogue's rule selects."""
        return "re" if self.test_type in RE_PROPOSED else "vendored"

    @property
    def is_table_check(self) -> bool:
        """A check on a table. Upstream's Schema_Drift is `tablegroup` scope, but its result rows are per table, and RE
        keeps one check per table (the note, section 5)."""
        return self.test_scope in ("table", "tablegroup")

    @property
    def manual_only(self) -> bool:
        """No selection rule upstream and not one RE proposes itself: a person defines it (Schema_Conformance)."""
        return self.selection_criteria is None and self.test_type not in RE_PROPOSED

    @property
    def is_table_comparison(self) -> bool:
        """A baseline-match type: proposed only for a table a person opted in."""
        return TABLE_COMPARISON in self.generation_sets

    @property
    def measure_is_text(self) -> bool:
        """The measure is text compared with a quoted baseline (Match_Min_Date, Match_Max_Date), not a number."""
        return self.test_condition.strip() == "{BASELINE_VALUE_LITERAL}"

    @property
    def measure_is_exact(self) -> bool:
        """The measure is an exact NUMERIC compared with an exact decimal (Match_Sum, Match_Fractional_Sum), never
        through a float."""
        return self.test_type in EXACT_TEST_TYPES


def sha256_text(text: str | None) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _postgres_condition(raw: dict) -> dict:
    for cond in raw.get("cat_test_conditions") or []:
        if cond.get("sql_flavor") == "postgresql":
            return cond
    if (raw.get("run_type") or RUN_CAT) != RUN_CAT:
        return {"measure": "", "test_operator": "", "test_condition": ""}
    raise ValueError(f"{raw.get('test_type')}: no postgresql cat_test_conditions entry")


def postgres_template(raw: dict) -> str:
    """The Postgres `test_templates` entry of a QUERY or METADATA type: the statement upstream runs for it."""
    for entry in raw.get("test_templates") or []:
        if entry.get("sql_flavor") == "postgresql":
            return entry["template"]
    raise ValueError(f"{raw.get('test_type')}: no postgresql test_templates entry")


def parse_test_type(raw: dict) -> TestType:
    cond = _postgres_condition(raw)
    return TestType(
        test_type=raw["test_type"],
        id=str(raw["id"]),
        name_short=raw.get("test_name_short") or raw["test_type"],
        description=raw.get("test_description") or "",
        usage_notes=raw.get("usage_notes") or "",
        dimension=raw.get("dq_dimension") or "",
        default_severity=raw.get("default_severity") or "Fail",
        measure_uom=raw.get("measure_uom") or "",
        test_scope=raw.get("test_scope") or "column",
        selection_criteria=raw.get("selection_criteria"),
        default_parm_columns=tuple(c.strip() for c in (raw.get("default_parm_columns") or "").split(",") if c.strip()),
        default_parm_values=raw.get("default_parm_values"),
        measure=cond["measure"],
        test_operator=cond["test_operator"],
        test_condition=cond["test_condition"],
        generation_sets=tuple(raw.get("generation_sets") or ()),
        generation_template=raw.get("generation_template"),
        null_measure_fails=bool(raw.get("null_measure_fails")),
        run_type=raw.get("run_type") or RUN_CAT,
        default_parm_prompts=raw.get("default_parm_prompts") or "",
    )


def read_raw(path: Path) -> dict:
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("test_types") or {}


@lru_cache(maxsize=1)
def load_catalogue() -> dict[str, TestType]:
    """{test_type: TestType} for every ported test type, read from the vendored YAML."""
    return {name: parse_test_type(read_raw(TEST_TYPES_DIR / f"{name}.yaml")) for name in PORTED_TEST_TYPES}


# ── upstream's templated functions (`<%NAME;arg;arg%>`) ────────────────────────────────────────────────────────

TEMPLATED_FUNCTIONS_YAML = POSTGRES_DIR / "templated_functions.yaml"
_FUNCTION_CALL = re.compile(r"<%(\w+)(?:;(.+?))?%>")
_FUNCTION_ARG = re.compile(r"\{\$(\d+)\}")


@lru_cache(maxsize=1)
def templated_functions() -> dict[str, str]:
    """{name: template} from the vendored subset, comments dropped and whitespace squashed as upstream does."""
    raw = yaml.safe_load(TEMPLATED_FUNCTIONS_YAML.read_text(encoding="utf-8")) or {}
    return {name: re.sub(r"\s\s*", " ", re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)) for name, text in raw.items()}


def expand_templated_functions(text: str) -> str:
    """Upstream's `read_file.replace_templated_functions` for the Postgres flavour: each `<%NAME;a;b%>` becomes the
    template with `{$1}`, `{$2}` set to the arguments. Applied to VENDORED text only, before any placeholder is
    filled, so no identifier or parameter value is ever split on `;` or matched as `%>`. An unknown function or a
    missing argument raises ParameterError: the vendored SQL then calls something this port has not vendored."""
    functions = templated_functions()

    def call(match: re.Match) -> str:
        name, args = match.group(1), (match.group(2) or "").split(";")
        if name not in functions:
            raise ParameterError(f"the vendored SQL calls <%{name}%>, which templated_functions.yaml does not hold")

        def arg(m: re.Match) -> str:
            index = int(m.group(1)) - 1
            if index >= len(args):
                raise ParameterError(f"<%{name}%> is missing argument {index + 1}")
            return args[index]
        return _FUNCTION_ARG.sub(arg, functions[name])
    return _FUNCTION_CALL.sub(call, text)


def selection_source(test_type: TestType) -> str | None:
    """The vendored text a selection rule was ported from: the `selection_criteria` fragment, or, when that is
    upstream's `TEMPLATE` marker, the vendored generation template it names (Match_Row_Ct). None when there is no
    upstream rule (Row_Ct_Pct)."""
    if test_type.selection_criteria == "TEMPLATE":
        if not test_type.generation_template:
            raise ValueError(f"{test_type.test_type}: selection is TEMPLATE but no generation_template is named")
        return (GENERATION_TEMPLATES_DIR / test_type.generation_template).read_text(encoding="utf-8")
    return test_type.selection_criteria


def catalogue_ref(test_type: TestType) -> str:
    """Where a proposal's rule came from: the upstream commit and the SHA-256 of the selection fragment (or of the
    generation template, for a TEMPLATE type; or of the Postgres measure, for a check RE proposes itself)."""
    if test_type.selection_criteria == "TEMPLATE":
        return f"vendored-catalog@{UPSTREAM_COMMIT}:{test_type.test_type}:generation_template:sha256:" \
               f"{sha256_text(selection_source(test_type))}"
    if test_type.selection_criteria is not None:
        return f"vendored-catalog@{UPSTREAM_COMMIT}:{test_type.test_type}:selection_criteria:sha256:" \
               f"{sha256_text(test_type.selection_criteria)}"
    return f"vendored-catalog@{UPSTREAM_COMMIT}:{test_type.test_type}:measure:sha256:{sha256_text(test_type.measure)}"


# ── selection rules: SQL three-valued logic, then the seven ports ────────────────────────────────────────────

def _and(*values: bool | None) -> bool | None:
    """SQL AND: FALSE if any operand is FALSE, else NULL if any is NULL, else TRUE."""
    if any(v is False for v in values):
        return False
    if any(v is None for v in values):
        return None
    return True


def _not(value: bool | None) -> bool | None:
    return None if value is None else not value


def _coalesce(*values):
    return next((v for v in values if v is not None), None)


def _cmp(left, op: str, right) -> bool | None:
    """SQL comparison: NULL when either side is NULL."""
    if left is None or right is None:
        return None
    return _FAIL_WHEN[op](left, right)


@dataclass(frozen=True)
class Predicate:
    """A ported selection rule: the fragment's pinned hash, the profile inputs it reads, and the port."""

    fragment_sha256: str | None
    inputs: tuple[str, ...]
    rule: Callable[[dict], bool | None]

    def evaluate(self, profile: dict) -> bool | None:
        return self.rule(profile)

    def null_inputs(self, profile: dict) -> list[str]:
        """The inputs this rule reads that are NULL in `profile`; `sample_ratio` is read by IS NULL, so its NULL
        is a value, never a missing input."""
        return [name for name in self.inputs if name != "sample_ratio" and profile.get(name) is None]


def _p(row: dict, name: str):
    return row.get(name)


def _shift(ts: datetime | None, *, days: int) -> datetime | None:
    return None if ts is None else ts + timedelta(days=days)


def _midnight(ts: datetime | None) -> datetime | None:
    """`'...'::DATE`, compared with a timestamp: its midnight."""
    return None if ts is None else datetime(ts.year, ts.month, ts.day)  # noqa: DTZ001 - naive UTC, as upstream's timestamp columns


def _datediff_day(first, second) -> int | None:
    a, b = as_timestamp(first), as_timestamp(second)
    return None if a is None or b is None else datediff_day(a, b)


def _greatest(*values):
    """Postgres GREATEST: NULL arguments are ignored; NULL only when every argument is NULL."""
    known = [v for v in values if v is not None]
    return max(known) if known else None


def _abs(value):
    return None if value is None else abs(value)


def _mul(*values):
    return None if any(v is None for v in values) else math.prod(values)


def _sub(a, b):
    return None if a is None or b is None else a - b


#: The measure profile 1b always takes for a column of each general_type that has a value: NULL there, with
#: value_ct > 0, means the row was profiled before profile 1b existed (its migration left the column NULL).
PROFILE_1B_WITNESS = {"N": "min_value", "A": "min_length", "B": "boolean_true_ct", "D": "date_days_present"}


def _profiled_by_1b(row: dict) -> bool | None:
    """None when profile 1b's measures cannot be read from this row: the column was not profiled (`state`), or the row
    predates profile 1b (its general_type's witness measure is NULL though the column has values). Else True. A B1b
    rule ANDs this in, so either reads `not_established`, never a silent no: several of their fragments read a 1b
    measure with IS NOT NULL, where upstream's NULL means "not applicable" and an unprofiled column has no row."""
    if (row.get("state") or "measured") != "measured":
        return None
    witness = PROFILE_1B_WITNESS.get(row.get("general_type") or "")
    if witness is None:
        return True
    if row.get("value_ct") is None:
        return None
    if row["value_ct"] == 0:
        return True
    return None if row.get(witness) is None else True


def _is_not_null_1b(row: dict, measure: str) -> bool | None:
    """`<1b measure> IS NOT NULL`, unknown (None) when the row cannot carry 1b measures (`_profiled_by_1b`): FALSE
    there would win any SQL AND and turn "not measured" into a silent no."""
    return None if _profiled_by_1b(row) is None else row.get(measure) is not None


def _sql_or(*values: bool | None) -> bool | None:
    """SQL OR: TRUE if any operand is TRUE, else NULL if any is NULL, else FALSE."""
    if any(v is True for v in values):
        return True
    if any(v is None for v in values):
        return None
    return False


#: Daily_Record_Ct's anchor date, `'1800-01-05'::DATE` (its midnight).
_DAILY_ANCHOR = datetime(1800, 1, 5)  # noqa: DTZ001 - naive, as upstream's timestamp columns


def _date_span_days(min_date, max_date) -> int | None:
    """`DATEDIFF('day', '1800-01-05'::DATE, max_date) - DATEDIFF('day', '1800-01-05'::DATE, min_date) + 1`: the
    calendar days from the first date to the last, both counted."""
    first, last = _datediff_day(_DAILY_ANCHOR, max_date), _datediff_day(_DAILY_ANCHOR, min_date)
    return None if first is None or last is None else first - last + 1


def _profiled(row: dict) -> bool | None:
    """True for a profile row that was measured, None (unknown) otherwise. The templates of steps 4 and 5 select every
    table of a profile run upstream; a table RE could not profile (budget, privilege, error) has no such row, and the
    owner's rule is that an incomplete table gets no proposals (2026-10-21), so their rules read "unknown" there and
    propose reports `not_established` with the profile's own reason."""
    return True if (row.get("state") or "measured") == "measured" else None


def _between_ct(row: dict, low: int, high: int) -> bool | None:
    """`distinct_value_ct BETWEEN low AND high`."""
    value = row.get("distinct_value_ct")
    return None if value is None else low <= value <= high


def _outlier(p: dict) -> bool | None:
    dv, vc, hi, lo = p.get("distinct_value_ct"), p.get("value_ct"), p.get("max_value"), p.get("min_value")
    span = None if hi is None or lo is None or dv is None else dv == hi - lo + 1
    ratio = None if dv is None or not vc else dv / vc
    avg, sd = p.get("avg_value"), p.get("stdev_value")
    cv = None if sd is None or not avg else sd / avg   # a zero mean is a division error upstream: unknown here
    return _and(_fdt_is(p, "Measurement"), _cmp(dv, ">", 30), _not(span), _cmp(ratio, ">", 0.1), _cmp(cv, ">", 0.01),
                _not_geo(p))


def _variability(p: dict) -> bool | None:
    dv, vc, hi, lo = p.get("distinct_value_ct"), p.get("value_ct"), p.get("max_value"), p.get("min_value")
    consecutive = _and(None if hi is None or lo is None or dv is None else dv == hi - lo + 1, _cmp(dv, ">", 2))
    return _and(_cmp(p.get("general_type"), "=", "N"), _fdt_measure(p), _not_geo(p), _cmp(vc, "<>", dv),
                _cmp(dv, ">", 10), _cmp(p.get("stdev_value"), ">", 0), _is_not_null_1b(p, "avg_value"),
                _not(consecutive))


def label_in(possible, test) -> bool | None:
    """Whether a column's functional_data_type satisfies `test` (a function of a label): True when every label it could
    still end with does, False when none does, else None. `possible` None = the type is not known at all. NULL (no
    label) satisfies nothing. The inference that produces the set is `functional_infer`."""
    if possible is None:
        return None
    hits = [bool(v is not None and test(v)) for v in possible]
    if all(hits):
        return True
    return False if not any(hits) else None


def _possible(row: dict):
    """The labels a profile row's type could be: `fdt_possible` when propose supplied it (the stored label, or the set
    the stored measures narrow it to), else the stored `functional_data_type` alone, else unknown."""
    if "fdt_possible" in row:
        return row["fdt_possible"]
    stored = row.get("functional_data_type")
    return frozenset({stored}) if stored else None


def _fdt_is(row: dict, label: str) -> bool | None:
    return label_in(_possible(row), lambda v: v == label)


def _fdt_in(row: dict, labels: tuple[str, ...]) -> bool | None:
    return label_in(_possible(row), lambda v: v in labels)


def _fdt_ilike_prefix(row: dict, prefix: str) -> bool | None:
    """`functional_data_type ILIKE '<prefix>%'`."""
    return label_in(_possible(row), lambda v: v.lower().startswith(prefix.lower()))


def _fdt_measure(row: dict) -> bool | None:
    """`functional_data_type ILIKE 'Measure%' AND functional_data_type <> 'Measurement Spike'`."""
    return _and(_fdt_ilike_prefix(row, "Measure"), label_in(_possible(row), lambda v: v != "Measurement Spike"))


def _table_like(row: dict, needle: str) -> bool | None:
    """`functional_table_type LIKE '%<needle>%'`; unknown when the table type was not inferred."""
    value = row.get("functional_table_type")
    return None if value is None else needle.lower() in value.lower()


def _not_geo(row: dict) -> bool | None:
    name = (row.get("column_name") or "").lower()
    return "latitude" not in name and "longitude" not in name


def _text_shape_read(row: dict) -> bool | None:
    """None when this row's sampled text-shape measures cannot be read: the column was not profiled (`state`), or it is a
    text column with values whose `sample_value_ct` (the witness: the text-shape statement always returns it) is NULL,
    because the row predates step 7 or that column's read did not finish. Else True. A rule that reads a text-shape
    measure ANDs this in, so it reads `not_established`, never a silent no. A column that is not text, or holds no value,
    has no text shape upstream either: True, and the measure it reads is NULL there, which upstream reads as no."""
    if (row.get("state") or "measured") != "measured":
        return None
    if row.get("general_type") != "A" or row.get("value_ct") == 0:
        return True
    if row.get("value_ct") is None:
        return None
    return None if row.get("sample_value_ct") is None else True


def _std_pattern(row: dict, name: str) -> bool | None:
    """`std_pattern_match = name`: NULL there is upstream's "no standard pattern", so FALSE once the text shape was read
    for the row, and "not measured" before it."""
    read = _text_shape_read(row)
    if read is None:
        return None
    return row.get("std_pattern_match") == name


def _street_address(row: dict) -> bool | None:
    avg = row.get("avg_length")
    return _and(
        _std_pattern(row, "STREET_ADDR"),
        _cmp(avg, "<>", None if avg is None else round(avg)),
        None if row.get("avg_embedded_spaces") is None else 2 <= row["avg_embedded_spaces"] <= 6,
        _cmp(avg, "<", 35),
    )


def _hash_rule(row: dict, general_type: str, measure: str) -> bool | None:
    """`general_type = <t> AND <measure> IS NOT NULL` for a value-hash measure (port step 8). Hashes are off by default, so a
    NULL is not a "no" until the profile says it asked: `hash_state` NULL is "not taken", `not_measured` is "could not be
    read", both `not_established`. After a successful read a NULL hash means nothing was hashable (a "no"), and for a keyed
    hash that the table has no key, or the key is this column."""
    if row.get("general_type") != general_type:
        return False if (row.get("state") or "measured") == "measured" else None
    if (row.get("state") or "measured") != "measured":
        return None
    if row.get("value_ct") == 0:
        return False
    state = row.get("hash_state")
    if state not in ("taken", "keyed_not_measured"):
        return None
    if measure == "keyed_hash" and state == "keyed_not_measured" and row.get("key_column_name") not in (None, row.get("column_name")):
        return None
    return row.get(measure) is not None


def _text_measure_present(row: dict, measure: str, *, frequency: bool) -> bool | None:
    """`<measure> IS NOT NULL` for a text-shape or frequency measure (port step 8b), three-valued. NULL upstream means the
    column has no such measure; here that is a "no" only once the column was read: an unread one (profiled before the
    analysis existed, or its read did not finish) is `not_established`. A column that is not text, or that is not
    eligible for the frequency analysis (2 to 70 distinct values of at most 70 characters), has none: a "no"."""
    if (row.get("state") or "measured") != "measured":
        return None
    if row.get("general_type") != "A" or row.get("value_ct") == 0:
        return False
    if frequency:
        from resource_explorer.surveyors.database.quality import frequent_values

        if not frequent_values.eligible(row):
            return False if row.get("distinct_value_ct") is not None and row.get("max_length") is not None else None
        state = row.get("frequency_state")
        if state != frequent_values.READ:
            return None
    elif _text_shape_read(row) is None:
        return None
    value = row.get(measure)
    return bool(value) if not isinstance(value, dict) else bool(value.get("values"))


def _frequent_values_rule(row: dict) -> bool | None:
    """general_type = 'A' AND frequent_values IS NOT NULL AND LENGTH(<the baseline text>) <= 1000 AND sample_ratio IS NULL."""
    present = _text_measure_present(row, "frequent_values", frequency=True)
    from resource_explorer.surveyors.database.quality import frequent_values

    text = frequent_values.baseline_text(row.get("frequent_values"))
    return _and(_cmp(row.get("general_type"), "=", "A"), present, None if present is None else (
        len(text) <= 1000 if text is not None else False), row.get("sample_ratio") is None)


def _frequent_patterns_rule(row: dict) -> bool | None:
    return _and(_cmp(row.get("general_type"), "=", "A"),
                _text_measure_present(row, "frequent_patterns", frequency=False), row.get("sample_ratio") is None)


def _distinct_hash_rule(row: dict) -> bool | None:
    return _and(_cmp(row.get("general_type"), "=", "A"),
                _text_measure_present(row, "distinct_value_hash", frequency=True), row.get("sample_ratio") is None)


def _text_baseline(row: dict) -> bool | None:
    """`general_type = 'A' AND value_ct > 0 AND sample_ratio IS NULL`, and the text shape was read: a sampled estimate is
    never an accepted baseline (upstream's rule), and an unread one is not_established."""
    return _and(_cmp(_p(row, "general_type"), "=", "A"), _cmp(_p(row, "value_ct"), ">", 0),
                _p(row, "sample_ratio") is None, _text_shape_read(row))


def _fractional_positive(row: dict) -> bool | None:
    """`fractional_sum > 0`: NULL there means "not a decimal column" (so FALSE) once profile 1b has taken the row, and
    "not measured" before it."""
    if _profiled_by_1b(row) is None:
        return None
    text = row.get("fractional_sum")
    if text is None:
        return False
    try:
        return Decimal(str(text)) > 0
    except InvalidOperation:
        return None


def _unrelated(value) -> None:
    """A measure RE does not take yet (text shape, frequent values and patterns, week and month presence, percentiles):
    its term is unknown on every column, and named in the not_established reason by its input name."""
    return None  # noqa: RET501 - the value is the measure's name for the reader; the answer is always unknown


# Each rule below is the vendored fragment, term for term. The fragment is quoted above its port.
PREDICATES: dict[str, Predicate] = {
    # record_ct <> value_ct
    "Missing_Pct": Predicate(
        "788cb3933546b28f0d8b9d8bcfc9a15aa0a86b8cae26289429ed41814383193b",
        ("record_ct", "value_ct"),
        lambda p: _cmp(_p(p, "record_ct"), "<>", _p(p, "value_ct")),
    ),
    # record_ct = value_ct AND record_ct > 10 AND NOT COALESCE(general_type = 'A' AND zero_length_ct = 0
    #   AND filled_value_ct = 0 AND sample_ratio IS NULL, FALSE)
    "Required": Predicate(
        "8ec541f833fe241959272d67e56abf21531ba64ed06b8396be9fb9dde0f43894",
        ("record_ct", "value_ct", "general_type", "zero_length_ct", "filled_value_ct", "sample_ratio"),
        lambda p: _and(
            _cmp(_p(p, "record_ct"), "=", _p(p, "value_ct")),
            _cmp(_p(p, "record_ct"), ">", 10),
            _not(_coalesce(_and(
                _cmp(_p(p, "general_type"), "=", "A"),
                _cmp(_p(p, "zero_length_ct"), "=", 0),
                _cmp(_p(p, "filled_value_ct"), "=", 0),
                _p(p, "sample_ratio") is None,
            ), False)),
        ),
    ),
    # record_ct = value_ct AND record_ct > 10 AND general_type = 'A' AND zero_length_ct = 0
    #   AND filled_value_ct = 0 AND sample_ratio IS NULL
    "Required_No_Placeholder": Predicate(
        "de6007a4f24be5371ba244212e6201bac5ec713b5d66208f02c4ad0487107bba",
        ("record_ct", "value_ct", "general_type", "zero_length_ct", "filled_value_ct", "sample_ratio"),
        lambda p: _and(
            _cmp(_p(p, "record_ct"), "=", _p(p, "value_ct")),
            _cmp(_p(p, "record_ct"), ">", 10),
            _cmp(_p(p, "general_type"), "=", "A"),
            _cmp(_p(p, "zero_length_ct"), "=", 0),
            _cmp(_p(p, "filled_value_ct"), "=", 0),
            _p(p, "sample_ratio") is None,
        ),
    ),
    # record_ct > 500 and record_ct = distinct_value_ct and value_ct > 0
    "Unique": Predicate(
        "d1ffee508acd1cbb2349028da390e0acebf5c2023e74f5f782f3ad01a9f36e72",
        ("record_ct", "distinct_value_ct", "value_ct"),
        lambda p: _and(
            _cmp(_p(p, "record_ct"), ">", 500),
            _cmp(_p(p, "record_ct"), "=", _p(p, "distinct_value_ct")),
            _cmp(_p(p, "value_ct"), ">", 0),
        ),
    ),
    # general_type='D'AND future_date_ct = 0
    "Future_Date": Predicate(
        "bbc5d8abe13875e63d7e0179eb54ea7c1b3563c867da38b9b35c6990b7300bba",
        ("general_type", "future_date_ct"),
        lambda p: _and(_cmp(_p(p, "general_type"), "=", "D"), _cmp(_p(p, "future_date_ct"), "=", 0)),
    ),
    # general_type='D'and min_date IS NOT NULL AND distinct_value_ct > 1
    "Min_Date": Predicate(
        "81422ad9c3f37e2ff7697c5772ca740da32ab43649ae5f085578f1e1661bddc4",
        ("general_type", "min_date", "distinct_value_ct"),
        lambda p: _and(
            _cmp(_p(p, "general_type"), "=", "D"),
            _p(p, "min_date") is not None,
            _cmp(_p(p, "distinct_value_ct"), ">", 1),
        ),
    ),
    # No fragment: TestGen leaves Row_Ct_Pct manual-only. RE's own rule, for every table with record_ct > 0.
    "Row_Ct_Pct": Predicate(
        None,
        ("record_ct",),
        lambda p: _cmp(_p(p, "record_ct"), ">", 0),
    ),
    # general_type= 'D' AND max_date <= run_date + INTERVAL '1 day' AND NOT column_name IN ( 'filedate' ,
    #   'file_date' ) AND NOT functional_data_type IN ('Future Date', 'Schedule Date') AND DATEDIFF( 'DAY' ,
    #   max_date, run_date) <= 62
    # run_date is the profile's own instant (its batch `surveyed_at`); functional_data_type is the date branch of
    # the inference, ported in functional_type.py.
    "Recency": Predicate(
        "575ccbd3c20b457f0de7c76a98a1ac8d5c5900f544bc455f0017ed4826067c44",
        tuple(dict.fromkeys(("general_type", "max_date", "surveyed_at") + INFERENCE_INPUTS)),
        lambda p: _and(
            _cmp(_p(p, "general_type"), "=", "D"),
            _cmp(as_timestamp(_p(p, "max_date")), "<=", _shift(as_timestamp(_p(p, "surveyed_at")), days=1)),
            _not(_p(p, "column_name") in ("filedate", "file_date")),
            _not(recency_excluded(p)),
            _cmp(_datediff_day(_p(p, "max_date"), _p(p, "surveyed_at")), "<=", 62),
        ),
    ),
    # general_type='D'AND future_date_ct > 0 AND max_date <='{AS_OF_DATE}'::DATE + INTERVAL'365 DAYS'
    # {AS_OF_DATE} is the instant the checks are generated: the propose run (`as_of_date`), as TestGen's
    # generation run with no profiling delay.
    "Future_Date_1Y": Predicate(
        "90e86999535a8ca5b5acfb738507d1f6745e4888eb3851526c891716efa01027",
        ("general_type", "future_date_ct", "max_date", "as_of_date"),
        lambda p: _and(
            _cmp(_p(p, "general_type"), "=", "D"),
            _cmp(_p(p, "future_date_ct"), ">", 0),
            _cmp(as_timestamp(_p(p, "max_date")), "<=", _shift(_midnight(as_timestamp(_p(p, "as_of_date"))), days=365)),
        ),
    ),
    # ── B1a, the Table Comparison set: each runs only for a table a person opted in (propose.py) ──
    # gen_Match_Row_Ct.sql: every table of the profile run, with no condition (threshold_value = MAX(record_ct)).
    # A table RE could not profile has no record_ct; its default then reads NULL and it is not_established.
    "Match_Row_Ct": Predicate(
        "388b034e415c0229391f2e2405019fe5fc33df6c6113cdc75830be395f88c421",
        (),
        lambda p: True,
    ),
    # record_ct > 0 AND general_type <> 'X'
    "Match_Distinct_Ct": Predicate(
        "58e585157796b4807f7c5e18560ab021f575a3acabbbc0ed6c55cd7ea14245a5",
        ("record_ct", "general_type"),
        lambda p: _and(_cmp(_p(p, "record_ct"), ">", 0), _cmp(_p(p, "general_type"), "<>", "X")),
    ),
    # record_ct > 0 AND NOT (general_type = 'X' AND query_error IS NOT NULL)
    # query_error is TestGen's per-column profiling error. RE keeps no measured row with an error: a column whose read
    # failed is a row whose state is not `measured`, so that is the port of `query_error IS NOT NULL`.
    "Match_Null_Ct": Predicate(
        "a47c1abddc42557952036c5548933ff1bca2f940604b3a0633e60ecffa3442da",
        ("record_ct", "general_type"),
        lambda p: _and(
            _cmp(_p(p, "record_ct"), ">", 0),
            _not(_and(_cmp(_p(p, "general_type"), "=", "X"), (_p(p, "state") or "measured") != "measured")),
        ),
    ),
    # general_type = 'A' AND value_ct > 0\nAND sample_ratio IS NULL
    "Match_Zero_Length_Ct": Predicate(
        "359e71c7c48e6d6e625aefc828c381cbdae63f00a62fa74b6097fd0728f47e78",
        ("general_type", "value_ct", "sample_ratio"),
        lambda p: _and(
            _cmp(_p(p, "general_type"), "=", "A"),
            _cmp(_p(p, "value_ct"), ">", 0),
            _p(p, "sample_ratio") is None,
        ),
    ),
    # general_type = 'D' AND value_ct > 0
    "Match_Min_Date": Predicate(
        "d37f535f28c39a6fec1b6602b138cd7b327ca3c1e08d1db2246ea7043c2d19a7",
        ("general_type", "value_ct"),
        lambda p: _and(_cmp(_p(p, "general_type"), "=", "D"), _cmp(_p(p, "value_ct"), ">", 0)),
    ),
    # general_type = 'D' AND value_ct > 0
    "Match_Max_Date": Predicate(
        "d37f535f28c39a6fec1b6602b138cd7b327ca3c1e08d1db2246ea7043c2d19a7",
        ("general_type", "value_ct"),
        lambda p: _and(_cmp(_p(p, "general_type"), "=", "D"), _cmp(_p(p, "value_ct"), ">", 0)),
    ),
    # ── B1b, on profile 1b's measures. Each ANDs in `_profiled_by_1b` (an RE addition, named in its inputs) ──
    # general_type = 'N' AND value_ct > 0
    "Match_Min_Val": Predicate(
        "b5e6d3de37808d45817466a91cddf37dc88632bab718dff3d716ebb801b9ac09",
        ("general_type", "value_ct", "min_value"),
        lambda p: _and(_cmp(_p(p, "general_type"), "=", "N"), _cmp(_p(p, "value_ct"), ">", 0), _profiled_by_1b(p)),
    ),
    # general_type = 'N' AND value_ct > 0
    "Match_Max_Val": Predicate(
        "b5e6d3de37808d45817466a91cddf37dc88632bab718dff3d716ebb801b9ac09",
        ("general_type", "value_ct", "min_value"),
        lambda p: _and(_cmp(_p(p, "general_type"), "=", "N"), _cmp(_p(p, "value_ct"), ">", 0), _profiled_by_1b(p)),
    ),
    # general_type = 'N' AND value_ct > 0 AND GREATEST(ABS(min_value), ABS(max_value)) * value_ct < 1e30
    # AND (sum_value IS NOT NULL OR ABS(avg_value) >= 1e-5 * GREATEST(ABS(min_value), ABS(max_value)))
    "Match_Sum": Predicate(
        "18c91beb2f1dedf1164c971104dcac5ec6001004182898373d36333f0b9953bd",
        ("general_type", "value_ct", "min_value", "max_value", "sum_value", "avg_value"),
        lambda p: _and(
            _cmp(_p(p, "general_type"), "=", "N"),
            _cmp(_p(p, "value_ct"), ">", 0),
            _cmp(_mul(_greatest(_abs(_p(p, "min_value")), _abs(_p(p, "max_value"))), _p(p, "value_ct")), "<", 1e30),
            _sql_or(_is_not_null_1b(p, "sum_value"),
                    _cmp(_abs(_p(p, "avg_value")), ">=",
                         _mul(1e-5, _greatest(_abs(_p(p, "min_value")), _abs(_p(p, "max_value")))))),
            _profiled_by_1b(p),
        ),
    ),
    # general_type = 'N' AND value_ct > 1 AND distinct_value_ct > 1 AND stdev_value IS NOT NULL
    "Match_Stdev": Predicate(
        "37c334e78f5ebb4ebc3069b433808a25ca9a4685edfece61adc863f868907572",
        ("general_type", "value_ct", "distinct_value_ct", "stdev_value", "min_value"),
        lambda p: _and(
            _cmp(_p(p, "general_type"), "=", "N"),
            _cmp(_p(p, "value_ct"), ">", 1),
            _cmp(_p(p, "distinct_value_ct"), ">", 1),
            _is_not_null_1b(p, "stdev_value"),
        ),
    ),
    # general_type = 'N' AND value_ct > 0
    "Match_Zero_Ct": Predicate(
        "b5e6d3de37808d45817466a91cddf37dc88632bab718dff3d716ebb801b9ac09",
        ("general_type", "value_ct", "min_value"),
        lambda p: _and(_cmp(_p(p, "general_type"), "=", "N"), _cmp(_p(p, "value_ct"), ">", 0), _profiled_by_1b(p)),
    ),
    # general_type = 'N' AND fractional_sum IS NOT NULL
    "Match_Fractional_Sum": Predicate(
        "3a01ea588bc1e733473008b3b9557fed37fb8f6652756cb2cef83a765d7cb81c",
        ("general_type", "fractional_sum", "min_value"),
        lambda p: _and(_cmp(_p(p, "general_type"), "=", "N"), _is_not_null_1b(p, "fractional_sum")),
    ),
    # general_type = 'A' AND value_ct > 0
    "Match_Min_Length": Predicate(
        "d0557b9d9bd6b14ed583462b330402c1559fa790d99f26941badd66ddba9a283",
        ("general_type", "value_ct", "min_length"),
        lambda p: _and(_cmp(_p(p, "general_type"), "=", "A"), _cmp(_p(p, "value_ct"), ">", 0), _profiled_by_1b(p)),
    ),
    # general_type = 'A' AND value_ct > 0
    "Match_Max_Length": Predicate(
        "d0557b9d9bd6b14ed583462b330402c1559fa790d99f26941badd66ddba9a283",
        ("general_type", "value_ct", "min_length"),
        lambda p: _and(_cmp(_p(p, "general_type"), "=", "A"), _cmp(_p(p, "value_ct"), ">", 0), _profiled_by_1b(p)),
    ),
    # general_type = 'A' AND value_ct > 0\nAND sample_ratio IS NULL
    "Match_Total_Length": Predicate(
        "359e71c7c48e6d6e625aefc828c381cbdae63f00a62fa74b6097fd0728f47e78",
        ("general_type", "value_ct", "sample_ratio", "min_length"),
        lambda p: _and(
            _cmp(_p(p, "general_type"), "=", "A"),
            _cmp(_p(p, "value_ct"), ">", 0),
            _p(p, "sample_ratio") is None,
            _profiled_by_1b(p),
        ),
    ),
    # general_type = 'B' AND value_ct > 0
    "Match_True_Ct": Predicate(
        "b9c4460a9ceea97b193b0c5e58f3468d43e8dbdd1c06d8cf144a01e311484c13",
        ("general_type", "value_ct", "boolean_true_ct"),
        lambda p: _and(_cmp(_p(p, "general_type"), "=", "B"), _cmp(_p(p, "value_ct"), ">", 0), _profiled_by_1b(p)),
    ),
    # general_type = 'D' AND date_days_present IS NOT NULL
    # COUNT(DISTINCT ...) is never NULL once taken, so upstream's IS NOT NULL is "was profiled"; RE's NULL here is a row
    # from before profile 1b, or a column not profiled, read as not_established rather than "no".
    "Match_Distinct_Date_Ct": Predicate(
        "84883274b83c4a9d84624f2c445158921eaf0335d159fb7b4740d36159ec674e",
        ("general_type", "date_days_present"),
        lambda p: _and(_cmp(_p(p, "general_type"), "=", "D"), _is_not_null_1b(p, "date_days_present")),
    ),
    # general_type= 'D' AND date_days_present > 21 AND date_days_present - (DATEDIFF('day', '1800-01-05'::DATE,
    #   max_date) - DATEDIFF('day', '1800-01-05'::DATE, min_date) + 1) = 0 AND future_date_ct::FLOAT /
    #   NULLIF(value_ct, 0) <= 0.75
    # Standard set: proposed without an opt-in, as TestGen generates it for every table group.
    "Daily_Record_Ct": Predicate(
        "74ba0096f8ab06169623e9b5b18e2089faa3ddc929e3fe92c521a5e0a74eba49",
        ("general_type", "date_days_present", "min_date", "max_date", "future_date_ct", "value_ct"),
        lambda p: _and(
            _cmp(_p(p, "general_type"), "=", "D"),
            _cmp(_p(p, "date_days_present"), ">", 21),
            _cmp(_sub(_p(p, "date_days_present"),
                      _date_span_days(_p(p, "min_date"), _p(p, "max_date"))), "=", 0),
            _cmp(None if _p(p, "future_date_ct") is None or not _p(p, "value_ct")
                 else _p(p, "future_date_ct") / float(_p(p, "value_ct")), "<=", 0.75),
        ),
    ),
    # ── Q4 port order steps 4 and 5 ──
    # gen_Dupe_Rows.sql: every profiled table, no condition (groupby_names = all its columns, STRING_AGG by position).
    # The columns themselves, less the general_type X ones, are `dupe_rows.default_params`.
    "Dupe_Rows": Predicate(
        "bb8ea6de17f82094d69f4457f274862df32e1eb78915ccf63cf950694d63bd95",
        (),
        lambda p: _profiled(p),
    ),
    # gen_Match_Column_Ct.sql: every profiled table, threshold_value = its column count from the column catalog.
    "Match_Column_Ct": Predicate(
        "583550e28097785940d76f45bbcf9f13f2a996fa775a09d7efcbcb620454f9c1",
        (),
        lambda p: _profiled(p),
    ),
    # general_type IS NOT NULL AND NOT (general_type = 'X' AND query_error IS NOT NULL)
    # query_error is TestGen's per-column profiling error; RE's port of it is a row whose state is not `measured`
    # (as Match_Null_Ct reads it).
    "Match_Column_Type": Predicate(
        "592885c4a5b9904f29657c1fe5eb71d47301ce0d42d8fc5e533b258b69ae45b0",
        ("general_type",),
        lambda p: _and(
            _profiled(p),
            _p(p, "general_type") is not None and _p(p, "general_type") != "",
            _not(_and(_cmp(_p(p, "general_type"), "=", "X"), (_p(p, "state") or "measured") != "measured")),
        ),
    ),
    # gen_Schema_Drift.sql: one test for the schema, no condition. RE keeps one check per table (the note, section 5).
    "Schema_Drift": Predicate(
        "a7cf8b51bb6345e4f0b2a16fcc8e494ba515b2fb9506d997ae70d96168e543bb",
        (),
        lambda p: _profiled(p),
    ),
    # No selection rule and no generation template upstream: a person declares the schema (an ODCS contract), and RE
    # holds no declared schema. Never proposed (`TestType.manual_only`).
    "Schema_Conformance": Predicate(None, (), lambda p: False),
    # ── step 6: the functional-type family (functional_infer.py). `functional_data_type` is read as the set of labels
    # the stored measures leave possible: a numeric column is not flooded with not_established for a text rule. ──
    # general_type ='A' AND max_length > 0 AND ( (min_length = avg_length AND max_length = avg_length) OR (numeric_ct <>
    #   value_ct ) ) AND functional_table_type NOT LIKE '%window%' AND ( (distinct_value_ct NOT BETWEEN 2 AND 10 AND
    #   functional_data_type NOT IN ( 'Constant', 'Boolean') ) AND NOT ( fn_frequent_size(frequent_patterns) = 1 ))
    "Alpha_Trunc": Predicate(
        "e6c0ffd7a754d2804d83eaa259fd4314e0f9c5e899d07d54c0e962ed8f94e075",
        ("general_type", "max_length", "min_length", "avg_length", "numeric_ct", "value_ct", "functional_table_type",
         "distinct_value_ct", "functional_data_type", "frequent_patterns"),
        lambda p: _and(
            _cmp(_p(p, "general_type"), "=", "A"), _cmp(_p(p, "max_length"), ">", 0),
            _sql_or(_and(_cmp(_p(p, "min_length"), "=", _p(p, "avg_length")),
                         _cmp(_p(p, "max_length"), "=", _p(p, "avg_length"))), _unrelated(_p(p, "numeric_ct"))),
            _not(_table_like(p, "window")),
            _and(_not(_between_ct(p, 2, 10)), label_in(_possible(p), lambda v: v not in ("Constant", "Boolean"))),
            _not(_unrelated(_p(p, "frequent_patterns"))))),
    # general_type='N' AND distinct_value_ct > 10 AND functional_data_type ilike 'Measure%' AND functional_data_type <>
    #   'Measurement Spike' AND column_name NOT ilike '%latitude%' AND column_name NOT ilike '%longitude%'
    "Avg_Shift": Predicate(
        "bde6e83d885b546f9c9bd0575ab448bbbd4472283b180135066cace7df4bdbf2",
        ("general_type", "distinct_value_ct", "functional_data_type"),
        lambda p: _and(_cmp(_p(p, "general_type"), "=", "N"), _cmp(_p(p, "distinct_value_ct"), ">", 10),
                       _fdt_measure(p), _not_geo(p))),
    # fractional_sum > 0 AND functional_table_type LIKE'%cumulative%'
    "Dec_Trunc": Predicate(
        "7eaac30c6b8bc8c33beb3cdd788bd5b782292ce2865ec48621c3250c40daafd8",
        ("fractional_sum", "functional_table_type"),
        lambda p: _and(_fractional_positive(p), _table_like(p, "cumulative"))),
    # functional_data_type ILIKE 'Transactional Date%' AND date_days_present > 1 AND functional_table_type ILIKE
    #   '%cumulative%'
    "Distinct_Date_Ct": Predicate(
        "bd30cbed60ba1c07557b2729b235290d5d6588934320131a33a0598afda4dde2",
        ("functional_data_type", "date_days_present", "functional_table_type"),
        lambda p: _and(_fdt_ilike_prefix(p, "Transactional Date"), _cmp(_p(p, "date_days_present"), ">", 1),
                       _table_like(p, "cumulative"))),
    # distinct_value_ct between 2 and 10 AND value_ct > 50 AND functional_data_type IN ('Code', 'Category', 'Attribute',
    #   'Description') AND frequent_values IS NULL
    "Distinct_Value_Ct": Predicate(
        "f13cceae802041f9c65afcf4627c8a8de8a2bb72f254d15b092e9963f94e8e94",
        ("distinct_value_ct", "value_ct", "functional_data_type", "frequent_values"),
        lambda p: _and(_between_ct(p, 2, 10), _cmp(_p(p, "value_ct"), ">", 50),
                       _fdt_in(p, ("Code", "Category", "Attribute", "Description")), _unrelated(_p(p, "frequent_values")))),
    # (same selection as Avg_Shift)
    "Incr_Avg_Shift": Predicate(
        "bde6e83d885b546f9c9bd0575ab448bbbd4472283b180135066cace7df4bdbf2",
        ("general_type", "distinct_value_ct", "functional_data_type"),
        lambda p: _and(_cmp(_p(p, "general_type"), "=", "N"), _cmp(_p(p, "distinct_value_ct"), ">", 10),
                       _fdt_measure(p), _not_geo(p))),
    # functional_data_type IN ('Boolean', 'Code', 'Category') AND frequent_values IS NOT NULL AND distinct_value_ct
    #   BETWEEN 2 and 10 AND value_ct > 5
    "LOV_Match": Predicate(
        "105b97ed128cf19417641a902412241358633940384f0d723ada46d1ab84fdd1",
        ("functional_data_type", "frequent_values", "distinct_value_ct", "value_ct"),
        lambda p: _and(_fdt_in(p, ("Boolean", "Code", "Category")), _unrelated(_p(p, "frequent_values")),
                       _between_ct(p, 2, 10), _cmp(_p(p, "value_ct"), ">", 5))),
    # general_type='N' AND functional_data_type ILIKE 'Measure%' AND min_value IS NOT NULL AND (distinct_value_ct >= 2
    #   OR (distinct_value_ct=2 and min_value<>0 and max_value<>1))
    "Min_Val": Predicate(
        "133e1e51624645220be07d7712ba2f7470a3d306b760542ffd9ae6485f33bba1",
        ("general_type", "functional_data_type", "min_value", "distinct_value_ct", "max_value"),
        lambda p: _and(_cmp(_p(p, "general_type"), "=", "N"), _fdt_ilike_prefix(p, "Measure"),
                       _is_not_null_1b(p, "min_value"),
                       _sql_or(_cmp(_p(p, "distinct_value_ct"), ">=", 2),
                               _and(_cmp(_p(p, "distinct_value_ct"), "=", 2), _cmp(_p(p, "min_value"), "<>", 0),
                                    _cmp(_p(p, "max_value"), "<>", 1))))),
    # functional_data_type ILIKE 'Transactional Date%' AND date_days_present > 1 AND functional_table_type ILIKE
    #   '%cumulative%' AND date_months_present > 2 AND date_months_present - (datediff( 'MON' , min_date, max_date) +
    #   1) = 0 AND future_date_ct::FLOAT / NULLIF(value_ct, 0) <= 0.75
    "Monthly_Rec_Ct": Predicate(
        "b0e2f84a3f3f3639f479e6d5c046602559dd06ff30c62e42ee44b8eab6cbb65e",
        ("functional_data_type", "date_days_present", "functional_table_type", "date_months_present", "min_date",
         "max_date", "future_date_ct", "value_ct"),
        lambda p: _and(_fdt_ilike_prefix(p, "Transactional Date"), _cmp(_p(p, "date_days_present"), ">", 1),
                       _table_like(p, "cumulative"), _unrelated(_p(p, "date_months_present")),
                       _cmp(None if not _p(p, "value_ct") or _p(p, "future_date_ct") is None
                            else _p(p, "future_date_ct") / _p(p, "value_ct"), "<=", 0.75))),
    # functional_data_type = 'Measurement' AND distinct_value_ct > 30 AND NOT distinct_value_ct = max_value - min_value
    #   + 1 AND distinct_value_ct::FLOAT/value_ct::FLOAT > 0.1 AND stdev_value::FLOAT/avg_value::FLOAT > 0.01 AND
    #   column_name NOT ILIKE '%latitude%' AND column_name NOT ilike '%longitude%'
    "Outlier_Pct_Above": Predicate(
        "382b204165f890443850d6b2740636a204de7ff06f99f630a5e68e98856c5e56",
        ("functional_data_type", "distinct_value_ct", "max_value", "min_value", "value_ct", "stdev_value", "avg_value"),
        lambda p: _outlier(p)),
    "Outlier_Pct_Below": Predicate(
        "382b204165f890443850d6b2740636a204de7ff06f99f630a5e68e98856c5e56",
        ("functional_data_type", "distinct_value_ct", "max_value", "min_value", "value_ct", "stdev_value", "avg_value"),
        lambda p: _outlier(p)),
    # (functional_data_type IN ('Attribute', 'DateTime Stamp', 'Phone') OR functional_data_type ILIKE 'ID%' OR
    #   functional_data_type ILIKE 'Period%') AND fn_frequent_size(frequent_patterns) = 1 AND distinct_value_ct > 10
    "Pattern_Match": Predicate(
        "e5663d5846832b0a0c1aefdc3ead0675f84c56f0a55e47ef970003bbe1952540",
        ("functional_data_type", "frequent_patterns", "distinct_value_ct"),
        lambda p: _and(label_in(_possible(p), lambda v: v in ("Attribute", "DateTime Stamp", "Phone")
                                or v.lower().startswith(("id", "period"))),
                       _unrelated(_p(p, "frequent_patterns")), _cmp(_p(p, "distinct_value_ct"), ">", 10))),
    # distinct_value_ct > 10 AND functional_data_type NOT ILIKE 'Measurement%'
    "Unique_Pct": Predicate(
        "0e6748217c9a4e6e22873810a2c1322f36b8b0c12e1fe27f1e4c20c809729922",
        ("distinct_value_ct", "functional_data_type"),
        lambda p: _and(_cmp(_p(p, "distinct_value_ct"), ">", 10),
                       label_in(_possible(p), lambda v: not v.lower().startswith("measurement")))),
    # functional_data_type = 'Period Month'
    "Valid_Month": Predicate(
        "bacabcc021b881d3eb371c1c2d42c6777d3e99c568bf608fe7d2342e9562fcd8", ("functional_data_type",),
        lambda p: _fdt_is(p, "Period Month")),
    # functional_data_type = 'Zip'
    "Valid_US_Zip": Predicate(
        "733e0d0d1b9e6557817dd685681706f447c0836377e716b09b7ad55b97e2afe4", ("functional_data_type",),
        lambda p: _fdt_is(p, "Zip")),
    # functional_data_type = 'Zip3'
    "Valid_US_Zip3": Predicate(
        "c4c5d8d57f6326af5382725134c4c84a6beb5eba5ce3c33ed7a33ca1c3d0017c", ("functional_data_type",),
        lambda p: _fdt_is(p, "Zip3")),
    # general_type = 'N' AND functional_data_type ilike 'Measure%' AND functional_data_type <> 'Measurement Spike' AND
    #   column_name NOT ilike '%latitude%' AND column_name NOT ilike '%longitude%' AND value_ct <> distinct_value_ct AND
    #   distinct_value_ct > 10 AND stdev_value > 0 AND avg_value IS NOT NULL AND NOT (distinct_value_ct = max_value -
    #   min_value + 1 AND distinct_value_ct > 2)
    "Variability_Decrease": Predicate(
        "ee60f52c07da58ce3d36ba7b432e783ac0c6ecb32388595a50f59aa1cfacbd79",
        ("general_type", "functional_data_type", "value_ct", "distinct_value_ct", "stdev_value", "avg_value",
         "max_value", "min_value"),
        lambda p: _variability(p)),
    "Variability_Increase": Predicate(
        "ee60f52c07da58ce3d36ba7b432e783ac0c6ecb32388595a50f59aa1cfacbd79",
        ("general_type", "functional_data_type", "value_ct", "distinct_value_ct", "stdev_value", "avg_value",
         "max_value", "min_value"),
        lambda p: _variability(p)),
    # functional_data_type ILIKE 'Transactional Date%' AND date_days_present > 1 AND functional_table_type ILIKE
    #   '%cumulative%' AND date_weeks_present > 3 AND date_weeks_present - (DATEDIFF('week', '1800-01-05'::DATE,
    #   max_date) - DATEDIFF('week', '1800-01-05'::DATE, min_date) + 1) = 0 AND future_date_ct::FLOAT / NULLIF(value_ct,
    #   0) <= 0.75
    "Weekly_Rec_Ct": Predicate(
        "61c72522c09e9c4d29b59f14180b78d42a5ccb3c99b7dab1741ac017562a9693",
        ("functional_data_type", "date_days_present", "functional_table_type", "date_weeks_present", "min_date",
         "max_date", "future_date_ct", "value_ct"),
        lambda p: _and(_fdt_ilike_prefix(p, "Transactional Date"), _cmp(_p(p, "date_days_present"), ">", 1),
                       _table_like(p, "cumulative"), _unrelated(_p(p, "date_weeks_present")),
                       _cmp(None if not _p(p, "value_ct") or _p(p, "future_date_ct") is None
                            else _p(p, "future_date_ct") / _p(p, "value_ct"), "<=", 0.75))),
    # std_pattern_match='EMAIL'
    "Email_Format": Predicate(
        "ef044052518d6eed7c8ad94a2eeb3a465bc21e05d1944eb56413849b9c522664", ("sample_value_ct", "std_pattern_match"),
        lambda p: _std_pattern(p, "EMAIL")),
    # (std_pattern_match='STREET_ADDR') AND (avg_length <> round(avg_length)) AND (avg_embedded_spaces BETWEEN 2 AND 6)
    #   AND (avg_length < 35)
    "Street_Addr_Pattern": Predicate(
        "aaf41b66925f84286e603d426370cc88eb89dc4f829c8cd86eea85ea2cd19a27",
        ("sample_value_ct", "std_pattern_match", "avg_length", "avg_embedded_spaces"), _street_address),
    # general_type= 'A' AND column_name ILIKE '%state%' AND distinct_value_ct < 70 AND max_length = 2
    "US_State": Predicate(
        "c1deae601c98bd5392d1e8756d32de9f43aaa39745860c7fe5792b61498e01f2",
        ("general_type", "column_name", "distinct_value_ct", "max_length"),
        lambda p: _and(_cmp(_p(p, "general_type"), "=", "A"), "state" in (_p(p, "column_name") or "").lower(),
                       _cmp(_p(p, "distinct_value_ct"), "<", 70), _cmp(_p(p, "max_length"), "=", 2),
                       _profiled_by_1b(p))),
    # general_type = 'A'
    "Valid_Characters": Predicate(
        "607214ad6b295cebd42ca3c0cb4e34093527ace61a9552253c1ec77991f05450", ("general_type",),
        # upstream's rule reads no measure; a column that was not profiled is not proposed on its type alone
        lambda p: _and(_cmp(_p(p, "general_type"), "=", "A"),
                       None if (_p(p, "state") or "measured") != "measured" else True)),
    # general_type = 'A' AND value_ct > 0\nAND sample_ratio IS NULL
    "Match_Lead_Space_Ct": Predicate(
        "359e71c7c48e6d6e625aefc828c381cbdae63f00a62fa74b6097fd0728f47e78",
        ("general_type", "value_ct", "sample_ratio", "sample_value_ct"), _text_baseline),
    "Match_Upper_Case_Ct": Predicate(
        "359e71c7c48e6d6e625aefc828c381cbdae63f00a62fa74b6097fd0728f47e78",
        ("general_type", "value_ct", "sample_ratio", "sample_value_ct"), _text_baseline),
    "Match_Non_Printing_Ct": Predicate(
        "359e71c7c48e6d6e625aefc828c381cbdae63f00a62fa74b6097fd0728f47e78",
        ("general_type", "value_ct", "sample_ratio", "sample_value_ct"), _text_baseline),
    # general_type = 'A' | 'N' | 'D' AND multiset_hash IS NOT NULL
    "Match_Hash_Text": Predicate("ae4a317c644ee093fa382d0658ca658008d4e20836cdc6a93249117b124f62c8",
                                 ("general_type", "hash_state"), lambda p: _hash_rule(p, "A", "multiset_hash")),
    "Match_Hash_Num": Predicate("4161e2ea636bebde49c8a0e6a522ae3b5c2f1da6745c231f6f18e4bdaa0a9d0f",
                                ("general_type", "hash_state"), lambda p: _hash_rule(p, "N", "multiset_hash")),
    "Match_Hash_Date": Predicate("95d8ca0468621fb9d5e4ca78722ce5ce76c4e571c1fe1330a638b3a3c90600b1",
                                 ("general_type", "hash_state"), lambda p: _hash_rule(p, "D", "multiset_hash")),
    # general_type = 'A' | 'N' | 'D' AND keyed_hash IS NOT NULL
    "Match_Keyed_Hash_Text": Predicate("ea485c759bf74b1395c167413d69e9cf2ea582b2f54483af2b8a761f0e01e925",
                                       ("general_type", "hash_state"), lambda p: _hash_rule(p, "A", "keyed_hash")),
    "Match_Keyed_Hash_Num": Predicate("c0d80d37fe32a62790b8542b50a36a5916989f0c3adb9f4d326b48c1c78b7d5c",
                                      ("general_type", "hash_state"), lambda p: _hash_rule(p, "N", "keyed_hash")),
    "Match_Keyed_Hash_Date": Predicate("ffb309d799216e3ef26f2e4841f148407d71236df998df7e3cfa239c6eb1ab35",
                                       ("general_type", "hash_state"), lambda p: _hash_rule(p, "D", "keyed_hash")),
    # general_type = 'A' AND frequent_values IS NOT NULL AND LENGTH(<baseline text>) <= 1000 AND sample_ratio IS NULL
    "Match_Frequent_Values": Predicate("20e556e1a55314f9ae3e31312f60063b8f39aa067803f59029576ba708a3062b",
                                       ("general_type", "frequency_state", "sample_ratio"), _frequent_values_rule),
    # general_type = 'A' AND frequent_patterns IS NOT NULL AND sample_ratio IS NULL
    "Match_Frequent_Patterns": Predicate("330cc4d10e72c6af8358963f4915ef64a95af0b9c05894c2e749751fa96f9abc",
                                         ("general_type", "sample_value_ct", "sample_ratio"), _frequent_patterns_rule),
    # general_type = 'A' AND distinct_value_hash IS NOT NULL AND sample_ratio IS NULL
    "Match_Distinct_Hash": Predicate("ca914aba2841b2930cf62e7cad7cb1e000568171e847a8b9306961df7b269c21",
                                     ("general_type", "frequency_state", "sample_ratio"), _distinct_hash_rule),
    # general_type = 'D' AND epoch_sum IS NOT NULL
    "Match_Epoch_Sum": Predicate("4bb0f8c3059d06b0201a1fcd4d6a6871b2ed9c2e8b58bf0a0978a226056d3519",
                                 ("general_type", "hash_state"), lambda p: _hash_rule(p, "D", "epoch_sum")),
}

#: Inputs whose NULL is a real value rather than a missing measure: min_date is read by IS NOT NULL. Recency's
#: inference reads zero_length_ct and filled_value_ct only through upstream's own NULL handling (neither is
#: measured on a date column, upstream either), and min_date only for the Period overrides. A B1b rule reads its
#: own measure with IS NOT NULL (NULL is "not applicable" there: no exact sum on a float column, no stdev of one
#: value); a missing measure is named through the profile 1b witness instead.
_NULL_IS_A_VALUE = {"Min_Date": {"min_date"},
                    "Email_Format": {"std_pattern_match"},
                    "Street_Addr_Pattern": {"std_pattern_match"},
                    "Recency": {"zero_length_ct", "filled_value_ct", "min_date"},
                    "Match_Sum": {"sum_value"},
                    "Match_Stdev": {"stdev_value"},
                    "Match_Fractional_Sum": {"fractional_sum"}}


def not_established_inputs(test_type: str, profile: dict) -> list[str]:
    """The NULL inputs that made `test_type`'s rule NULL, for the not_established reason."""
    skip = _NULL_IS_A_VALUE.get(test_type, set())
    return [n for n in PREDICATES[test_type].null_inputs(profile) if n not in skip]


# ── default parameters ─────────────────────────────────────────────────────────────────────────────────────────

class ParameterError(ValueError):
    """A parameter that cannot be used: the check is not_established, with this message as the reason."""


def _split_sql_list(text: str) -> list[str]:
    """Split `default_parm_values` on commas outside single-quoted strings ('' is an escaped quote)."""
    items, current, in_quote, i = [], [], False, 0
    while i < len(text):
        ch = text[i]
        if ch == "'":
            if in_quote and i + 1 < len(text) and text[i + 1] == "'":
                current.append("''")
                i += 2
                continue
            in_quote = not in_quote
        if ch == "," and not in_quote:
            items.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        i += 1
    items.append("".join(current).strip())
    return items


_CAST = re.compile(r"::[A-Za-z_]+(\(\d+\))?$")
_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def _default_value(expr: str, profile: dict):
    """One `default_parm_values` expression: a profile column, a quoted literal, or a number (casts dropped)."""
    expr = _CAST.sub("", expr.strip())
    if expr.startswith("'") and expr.endswith("'") and len(expr) >= 2:
        return expr[1:-1].replace("''", "'")
    if _NUMBER.match(expr):
        return int(expr) if "." not in expr else float(expr)
    if _IDENT.match(expr):
        if expr not in profile:
            raise ParameterError(f"default parameter refers to {expr!r}, which the value profile does not have")
        return profile.get(expr)
    raise ParameterError(f"default parameter expression {expr!r} is not one this port understands")


#: catalogue column name -> the params_json key it fills.
_PARAM_KEYS = {
    "baseline_ct": "baseline_ct", "baseline_value_ct": "baseline_value_ct", "baseline_unique_ct": "baseline_unique_ct",
    "baseline_value": "baseline_value", "threshold_value": "threshold_value",
    "baseline_sum": "baseline_sum", "baseline_sd": "baseline_sd", "upper_tolerance": "upper_tolerance",
    "baseline_avg": "baseline_avg", "groupby_names": "key_column_name",
}
#: Parameter keys a type carries only when its catalogue entry names them (so an earlier type's params_json keeps the
#: shape it was accepted with).
_OPTIONAL_PARAM_KEYS = ("baseline_unique_ct", "baseline_sum", "baseline_sd", "upper_tolerance", "baseline_avg",
                       "key_column_name")


def _recency_threshold(profile: dict):
    """Recency's default threshold, the vendored CASE over d = DATEDIFF('DAY', max_date, run_date):
    d <= 3: d + 3; d <= 7: d + 7; d <= 31: CEILING(d / 7.0) * 7; d > 31: CEILING(d / 30.0) * 30."""
    d = _datediff_day(profile.get("max_date"), profile.get("surveyed_at"))
    if d is None:
        return None
    if d <= 3:
        return d + 3
    if d <= 7:
        return d + 7
    if d <= 31:
        return math.ceil(d / 7.0) * 7
    return math.ceil(d / 30.0) * 30


#: `TO_CHAR(<ts>, 'YYYY-MM-DD HH24:MI:SS')`: the text form the Match_Min_Date/Match_Max_Date baseline and measure take.
TO_CHAR_SECONDS = "%Y-%m-%d %H:%M:%S"
_TO_CHAR_SECONDS = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def to_char_seconds(value) -> str | None:
    """Upstream's `TO_CHAR(min_date, 'YYYY-MM-DD HH24:MI:SS')` over a stored profile date. The profile stores the date
    as the target returned it (`value_profile._value`: ISO text, with an offset for a timestamptz, in the reading
    session's zone). TO_CHAR prints the wall-clock time and drops fractional seconds, so the offset is dropped, not
    converted: the run step's `CAST(MAX(col) AS TIMESTAMP)` reads the same wall clock through the same account's
    session. None for NULL."""
    if value is None:
        return None
    if isinstance(value, datetime):
        ts = value
    elif isinstance(value, date):
        ts = datetime(value.year, value.month, value.day)  # noqa: DTZ001 - a date has no zone
    else:
        try:
            ts = datetime.fromisoformat(str(value).strip())
        except ValueError:
            raise ParameterError(f"the profile's date {value!r} is not an ISO date") from None
    return ts.replace(tzinfo=None).strftime(TO_CHAR_SECONDS)


_EPOCH = datetime(1970, 1, 1)  # noqa: DTZ001 - the text measure carries no zone; neither does its number


def text_measure_number(text: str) -> float | None:
    """A TO_CHAR date measure as the number `database_quality_results.measure` (DOUBLE PRECISION) can hold: seconds
    from 1970-01-01 00:00:00 of the same wall-clock text, lossless to the second TO_CHAR prints. None when the text is
    not that shape (the comparison itself is on the text, so the result does not depend on this)."""
    try:
        return (datetime.strptime(text, TO_CHAR_SECONDS) - _EPOCH).total_seconds()  # noqa: DTZ007
    except (TypeError, ValueError):
        return None


def text_measure_from_number(number: float) -> str:
    """`text_measure_number`, inverted: the TO_CHAR text a stored date measure stands for."""
    return (_EPOCH + timedelta(seconds=number)).strftime(TO_CHAR_SECONDS)


def _date_baseline(measure: str) -> Callable[[dict], Any]:
    return lambda profile: to_char_seconds(profile.get(measure))


def _rtrim(text: str, chars: str) -> str:
    return text.rstrip(chars)


def _match_sum_defaults(profile: dict) -> tuple[Any, Any]:
    """Match_Sum's two defaults, term for term:

        COALESCE(RTRIM(RTRIM(sum_value::VARCHAR, '0'), '.'), (avg_value * value_ct)::VARCHAR)
        CASE WHEN sum_value IS NULL THEN '0.000000001' ELSE '0' END

    `sum_value` is the exact decimal text the profile stored; `(avg_value * value_ct)::VARCHAR` is the float's
    shortest text, which Python's `repr` prints the same way. Both stay text, as upstream's parameters are, so the
    exact sum never passes through a float. NULL when neither measure was taken."""
    sum_value, avg, value_ct = profile.get("sum_value"), profile.get("avg_value"), profile.get("value_ct")
    if sum_value is not None:
        trimmed = _rtrim(_rtrim(str(sum_value), "0"), ".") if "." in str(sum_value) else str(sum_value)
        baseline = trimmed
    else:
        baseline = None if avg is None or value_ct is None else repr(float(avg) * value_ct)
    return baseline, ("0.000000001" if sum_value is None else "0")


def _total_length_default(profile: dict):
    """`CAST(ROUND(COALESCE(avg_length, 0) * (value_ct - COALESCE(zero_length_ct, 0))) AS BIGINT)`: avg_length is the
    mean over non-empty values (upstream's NULLIF(LENGTH, 0)), so times the non-empty count it is the total. Postgres
    rounds a double half to even, as Python's round does. NULL when value_ct is."""
    value_ct = profile.get("value_ct")
    if value_ct is None:
        return None
    return round((profile.get("avg_length") or 0) * (value_ct - (profile.get("zero_length_ct") or 0)))


def _round_half_up(text):
    """Postgres `ROUND(numeric, 0)`: half away from zero. None when the sum was not measured."""
    if text is None:
        return None
    return int(Decimal(str(text)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _stored_patterns(stored):
    if isinstance(stored, str):
        import json

        try:
            return json.loads(stored)
        except ValueError:
            return None
    return stored


def _frequent_baseline(frequent):
    from resource_explorer.surveyors.database.quality import frequent_values

    return frequent_values.baseline_text(frequent)


def _needs(test_type: str, measure: str):
    when = ("port step 8" if measure == "frequent_values"
            else "profiled since port step 7, but this default is not ported over it yet")
    raise ParameterError(f"{test_type}'s default parameter reads {measure}, which this port cannot evaluate yet ({when})")


#: Defaults that are an expression over the profile rather than a list of columns and literals: the SHA-256 of
#: the vendored `default_parm_values` each port was made from, the parameter (or parameters, in order) it fills, and
#: the port (returning a tuple when it fills several).
DEFAULT_PORTS: dict[str, tuple[str, str | tuple[str, ...], Callable[[dict], Any]]] = {
    # CASE WHEN DATEDIFF( 'DAY' , max_date, run_date) <= 3 THEN DATEDIFF('DAY', max_date, run_date) + 3 WHEN ...
    "Recency": ("4a8f8bd298ae8f1e20ff4b7251454697078c503ab760aed919b698a466a00622", "threshold_value",
                _recency_threshold),
    # TO_CHAR(min_date, 'YYYY-MM-DD HH24:MI:SS')
    "Match_Min_Date": ("a249544a0f690252db818b4c436431f6832f3b1be4d36910982e8ecbf5e01f40", "baseline_value",
                       _date_baseline("min_date")),
    # TO_CHAR(max_date, 'YYYY-MM-DD HH24:MI:SS')
    "Match_Max_Date": ("969773ce43fed7c2b153de336efddc38528ae80d47417d9ed31b10d73de93e97", "baseline_value",
                       _date_baseline("max_date")),
    # COALESCE(RTRIM(RTRIM(sum_value::VARCHAR, '0'), '.'), (avg_value * value_ct)::VARCHAR),CASE WHEN sum_value IS
    #   NULL THEN '0.000000001' ELSE '0' END
    "Match_Sum": ("baea8f54d3d7a291bc57510dcb93fd5548b3433a575ca830fb5f1373aa2c696b",
                  ("baseline_sum", "upper_tolerance"), _match_sum_defaults),
    # ROUND(fractional_sum, 0)
    "Dec_Trunc": ("95230af0160b048103c8ff0406968834f67c58873fe3d2578853479f52f280d4", "threshold_value",
                  lambda profile: _round_half_up(profile.get("fractional_sum"))),
    # value_ct,(avg_value * value_ct)::FLOAT,avg_value,stdev_value,2
    "Incr_Avg_Shift": ("c72f2e463bc297dbc5784f390931c10e908495e5d6d57a44f07774ff255929ba",
                       ("baseline_value_ct", "baseline_sum", "baseline_avg", "baseline_sd", "threshold_value"),
                       lambda profile: (profile.get("value_ct"),
                                        None if profile.get("avg_value") is None or profile.get("value_ct") is None
                                        else float(profile["avg_value"] * profile["value_ct"]),
                                        profile.get("avg_value"), profile.get("stdev_value"), 2)),
    # FLOOR(0.95 * max_length::FLOAT)
    "Alpha_Trunc": ("1b45b0f0f387039178ff6c7b4544691d723e04fd8ccb2a979162ee66f5dffdfd", "threshold_value",
                    lambda profile: None if profile.get("max_length") is None
                    else math.floor(0.95 * float(profile["max_length"]))),
    # the three below read frequent_values, frequent_patterns and min_text/max_text: not taken until profile slice 2
    "LOV_Match": ("2b9503536f33be15fbb794c4b570cd3b4b7b6e7883917f30639832f8a7c1c00a", ("baseline_value", "threshold_value"),
                  lambda profile: _needs("LOV_Match", "frequent_values")),
    "Pattern_Match": ("0d3ff1531d5030da37e0d62790fa5fe994ab170c95dcb44a5df5b88286af7d6e",
                      ("baseline_value", "threshold_value"), lambda profile: _needs("Pattern_Match", "frequent_patterns")),
    "Valid_Month": ("860b9139ef54da1331d851ac3a93e5ed0db594aeefeddc7910faec57cb62ab2f",
                    ("threshold_value", "baseline_value"), lambda profile: _needs("Valid_Month", "min_text")),
    # (SELECT STRING_AGG((entry ->> 'value') || ' (' || (entry ->> 'ct') || ')', ', ' ORDER BY rank) FROM ...frequent_values...)
    "Match_Frequent_Values": ("c28227a0555c78d0befe0649b8dca4aae7a8ca67e854bf549c41a585660f6777", "baseline_value",
                              lambda profile: _frequent_baseline(profile.get("frequent_values"))),
    # the same over frequent_patterns
    "Match_Frequent_Patterns": ("b01655e0980182f29779acefa36f3909ab8a4108517020f4b6bccef3bef14b13", "baseline_value",
                                lambda profile: _frequent_baseline(_stored_patterns(profile.get("frequent_patterns")))),
    # distinct_value_hash
    "Match_Distinct_Hash": ("c70dff059bc0da3dba9c30c3f43ac9254916af21daa5841dd37060c5fc5f5f9a", "baseline_value",
                            lambda profile: profile.get("distinct_value_hash")),
    # CAST(ROUND(COALESCE(avg_length, 0) * (value_ct - COALESCE(zero_length_ct, 0))) AS BIGINT)
    "Match_Total_Length": ("764c27fe862246b3e311ea6030ec1212802f99d752fd4f04b2575c469207bd6a", "baseline_sum",
                           _total_length_default),
}


def default_params(test_type: TestType, profile: dict, row_ct_pct_threshold: float) -> dict:
    """The parameters a proposal starts with: the catalogue's defaults evaluated against the profile, plus the
    severity. Raises ParameterError when a default reads a profile measure that is NULL."""
    params: dict[str, Any] = {"baseline_ct": None, "baseline_value_ct": None, "baseline_value": None,
                              "threshold_value": None, "severity": test_type.severity}
    for optional in _OPTIONAL_PARAM_KEYS:
        if optional in test_type.default_parm_columns:
            params[optional] = None
    if test_type.test_type == "Row_Ct_Pct":
        # TestGen has no defaults for it: RE's baseline is the measured count, the threshold a setting.
        params["baseline_ct"] = profile.get("record_ct")
        params["threshold_value"] = row_ct_pct_threshold
    elif test_type.test_type == "Match_Row_Ct":
        # gen_Match_Row_Ct.sql: `s.record_ct AS threshold_value`, the table's MAX(record_ct) in the profile run. RE's
        # record_ct is per table (every measured column's row carries the same count). Pinned with the predicate.
        if sha256_text(selection_source(test_type)) != PREDICATES["Match_Row_Ct"].fragment_sha256:
            raise ParameterError("Match_Row_Ct's vendored generation template changed: re-port it")
        params["threshold_value"] = profile.get("record_ct")
    elif test_type.test_type in DEFAULT_PORTS:
        sha, key, port = DEFAULT_PORTS[test_type.test_type]
        if sha256_text(test_type.default_parm_values) != sha:
            raise ParameterError(f"{test_type.test_type}'s vendored default_parm_values changed: re-port it")
        if isinstance(key, tuple):
            params.update(zip(key, port(profile)))
        else:
            params[key] = port(profile)
    else:
        values = _split_sql_list(test_type.default_parm_values or "")
        if len(values) != len(test_type.default_parm_columns):
            raise ParameterError(f"{test_type.test_type}: {len(test_type.default_parm_columns)} default parameter "
                                 f"column(s) but {len(values)} value(s)")
        for column, expr in zip(test_type.default_parm_columns, values):
            if column not in _PARAM_KEYS:
                raise ParameterError(f"{test_type.test_type}: default parameter column {column!r} is not supported")
            params[_PARAM_KEYS[column]] = _default_value(expr, profile)
    missing = [k for k in _required_params(test_type) if params.get(k) is None]
    if missing:
        raise ParameterError(f"default {', '.join(missing)} would come from a measure that is NULL in the profile")
    return params


# ── rendering the target-side SQL ──────────────────────────────────────────────────────────────────────────────

_PLACEHOLDER = re.compile(r"\{([A-Z_]+)\}")
#: placeholder -> the params key it is filled from (the identifiers and RUN_DATE are filled by RE itself).
_PLACEHOLDER_PARAM = {
    "BASELINE_CT": "baseline_ct", "BASELINE_VALUE_CT": "baseline_value_ct", "BASELINE_UNIQUE_CT": "baseline_unique_ct",
    "BASELINE_VALUE": "baseline_value", "BASELINE_VALUE_LIST": "baseline_value",
    "BASELINE_VALUE_LITERAL": "baseline_value",
    "THRESHOLD_VALUE": "threshold_value",
    "BASELINE_SUM": "baseline_sum", "BASELINE_SD": "baseline_sd", "UPPER_TOLERANCE": "upper_tolerance",
    "BASELINE_AVG": "baseline_avg",
}
_RE_FILLED = {"COLUMN_NAME", "COLUMN_NUM", "RUN_DATE"}
_HASH_TERMS = {"MULTISET_HASH_TERM", "EPOCH_SUM_TERM", "KEYED_HASH_TERM"}


def _hash_term(placeholder: str, test_type: TestType, column, key) -> str:
    from resource_explorer.surveyors.database.quality import value_hashes

    if column is None:
        raise ParameterError(f"{test_type.test_type} hashes the column, and the column's type was not given")
    if placeholder == "MULTISET_HASH_TERM":
        return value_hashes.multiset_hash_term(column)
    if placeholder == "EPOCH_SUM_TERM":
        return value_hashes.epoch_sum_term(column)
    if key is None:
        raise ParameterError(f"{test_type.test_type} needs the table's key column, and it was not given")
    return value_hashes.keyed_hash_term(key, column)


def _required_params(test_type: TestType) -> list[str]:
    names = _PLACEHOLDER.findall(test_type.measure) + _PLACEHOLDER.findall(test_type.test_condition)
    out: list[str] = []
    for n in names:
        key = _PLACEHOLDER_PARAM.get(n)
        if key and key not in out:
            out.append(key)
    return out


def quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def column_num(column: str, is_money: bool = False) -> str:
    """Upstream's `{COL_NUM}` / `{COLUMN_NUM}` for Postgres (PostgresqlFlavorService.numeric_value): the quoted column,
    read through ::NUMERIC when it is money, which compares only with money and has no modulo or cast to float."""
    return f"{quote_identifier(column)}::NUMERIC" if is_money else quote_identifier(column)


def is_money_type(catalog_type: str | None) -> bool:
    """Whether a stored catalog type is Postgres's money (the one type `{COLUMN_NUM}` casts)."""
    return (catalog_type or "").strip().lower() == "money"


def string_literal(value: str) -> str:
    """A Postgres escape-string literal: correct whatever standard_conforming_strings is set to."""
    return "E'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


def parse_value_list(value) -> list[str]:
    """TestGen's list parameter: CSV text (`""` is the empty string), or an already-split list of strings."""
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        if not all(isinstance(v, str) for v in value):
            raise ParameterError(f"placeholder list must hold strings, got {value!r}")
        return list(value)
    if not isinstance(value, str):
        raise ParameterError(f"placeholder list must be text, got {value!r}")
    return [item for row in csv.reader(io.StringIO(value), skipinitialspace=True) for item in row]


def as_number(name: str, value) -> float | int:
    """A parameter that must be a finite number (ints stay ints). Raises ParameterError quoting the value."""
    if isinstance(value, bool) or value is None or value == "":
        raise ParameterError(f"{name} must be a number, got {value!r}")
    if isinstance(value, (int, float)):
        number = value
    else:
        text = str(value).strip()
        if not _NUMBER.match(text) and not re.match(r"^-?\d+(\.\d+)?[eE][+-]?\d+$", text):
            raise ParameterError(f"{name} must be a number, got {value!r}")
        number = float(text) if any(c in text for c in ".eE") else int(text)
    if isinstance(number, float) and not math.isfinite(number):
        raise ParameterError(f"{name} must be a finite number, got {value!r}")
    return number


def as_iso_date(name: str, value) -> str:
    """A parameter that must be an ISO date or timestamp; returned normalised (so no quote can reach the SQL)."""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        raise ParameterError(f"{name} must be an ISO date, got {value!r}") from None
    text = str(value).strip()
    return parsed.date().isoformat() if len(text) == 10 else parsed.isoformat(sep=" ")


def _sql_number(number: float) -> str:
    return repr(number) if isinstance(number, float) else str(number)


def render_measure(test_type: TestType, column_name: str, params: dict, run_date: str, *,
                   is_money: bool = False, hash_column=None, key_column=None) -> str:
    """The test type's vendored Postgres measure with every placeholder filled from validated values.

    `params` is the check's parameters as accepted (or as Egeria holds them); each is validated by type here
    (numbers parse as numbers, the Min_Date baseline as an ISO date, the placeholder list item by item as string
    literals). Raises ParameterError, quoting the bad value, for anything that does not validate, and for any
    placeholder this port does not know: an unknown placeholder means the vendored SQL changed under us.
    `{COLUMN_NUM}` is the column read as a number (`column_num`; `is_money` for a money column). The hash terms
    (`{MULTISET_HASH_TERM}`, `{EPOCH_SUM_TERM}`, `{KEYED_HASH_TERM}`; port step 8) are built by `value_hashes` from
    `hash_column` (a `HashColumn`) and, for the keyed term, `key_column`; a type that needs one it was not given is a
    ParameterError, never a guess."""
    fills: dict[str, str] = {"COLUMN_NAME": quote_identifier(column_name), "RUN_DATE": run_date,
                             "COLUMN_NUM": column_num(column_name, is_money)}
    measure = expand_templated_functions(test_type.measure)
    for placeholder in set(_PLACEHOLDER.findall(measure)):
        if placeholder in _HASH_TERMS:
            fills[placeholder] = _hash_term(placeholder, test_type, hash_column, key_column)
            continue
        if placeholder in _RE_FILLED:
            continue
        key = _PLACEHOLDER_PARAM.get(placeholder)
        if key is None:
            raise ParameterError(f"the vendored measure uses {{{placeholder}}}, which this port does not fill")
        value = params.get(key)
        if placeholder == "BASELINE_VALUE_LIST":
            items = parse_value_list(value)
            fills[placeholder] = ", ".join(string_literal(v) for v in items) or "NULL"
        elif placeholder == "BASELINE_VALUE":
            if test_type.test_type == "Min_Date":
                fills[placeholder] = as_iso_date("baseline_value", value)
            elif test_type.test_type == "Min_Val":
                fills[placeholder] = _sql_number(as_number("baseline_value", value))
            else:
                raise ParameterError(f"{{BASELINE_VALUE}} is only validated for Min_Date and Min_Val, not "
                                     f"{test_type.test_type}")
        else:
            fills[placeholder] = _sql_number(as_number(key, value))
    return _PLACEHOLDER.sub(lambda m: fills[m.group(1)], measure)


def as_to_char_seconds(name: str, value) -> str:
    """A parameter that must be TO_CHAR's 'YYYY-MM-DD HH24:MI:SS' text (a Match_Min_Date/Match_Max_Date baseline) and
    a real date: returned as it is. It reaches the SQL only through `string_literal`, never formatted in."""
    if not isinstance(value, str) or not _TO_CHAR_SECONDS.match(value):
        raise ParameterError(f"{name} must be a date as 'YYYY-MM-DD HH:MM:SS', got {value!r}")
    try:
        datetime.strptime(value, TO_CHAR_SECONDS)  # noqa: DTZ007 - text compared as text, no zone involved
    except ValueError:
        raise ParameterError(f"{name} must be a date as 'YYYY-MM-DD HH:MM:SS', got {value!r}") from None
    return value


#: The conditions that are one placeholder, read from one parameter.
_CONDITIONS = {"{THRESHOLD_VALUE}": "threshold_value", "{BASELINE_CT}": "baseline_ct",
               "{BASELINE_UNIQUE_CT}": "baseline_unique_ct", "{BASELINE_VALUE_LITERAL}": "baseline_value",
               "{BASELINE_VALUE}": "baseline_value", "{BASELINE_SUM}": "baseline_sum"}

_DECIMAL = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
#: Postgres NUMERIC rounds half away from zero; Decimal's ROUND_HALF_UP is that rule.
_EIGHT_PLACES, _FIVE_PLACES = Decimal("1e-8"), Decimal("1e-5")


def as_decimal(name: str, value) -> Decimal:
    """A parameter that must be an exact finite number: an int, a Decimal, or numeric text (TestGen's parameters are
    text). A float is read through its shortest text, as Postgres reads a float literal. Raises ParameterError
    quoting the value. Never formatted into SQL: the condition is evaluated in Python."""
    if isinstance(value, bool) or value is None:
        raise ParameterError(f"{name} must be a number, got {value!r}")
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        number = Decimal(repr(value))
    elif isinstance(value, str) and _DECIMAL.match(value.strip()):
        number = Decimal(value.strip())
    else:
        raise ParameterError(f"{name} must be a number, got {value!r}")
    if not number.is_finite():
        raise ParameterError(f"{name} must be a finite number, got {value!r}")
    return number


def _numeric(value: Decimal, places: Decimal) -> Decimal:
    """`CAST(value AS NUMERIC(38, n))` / `ROUND(value, n)`: rounded to n places, half away from zero."""
    with localcontext() as ctx:
        ctx.prec = 80
        return value.quantize(places, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class SumTolerance:
    """Match_Sum's NOT BETWEEN limit before the run: its bounds read `COUNT({COLUMN_NAME})`, which the run measures
    beside the sum (`between`)."""

    baseline: Decimal
    tolerance: Decimal

    def between(self, count: int) -> Between:
        """`CAST(B AS NUMERIC(38,8)) -/+ CAST(ABS(B * T) + SIGN(ABS(T)) * COUNT(col) * 0.000000005 AS NUMERIC(38,8))`."""
        with localcontext() as ctx:
            ctx.prec = 80
            base = _numeric(self.baseline, _EIGHT_PLACES)
            sign = Decimal(1) if self.tolerance != 0 else Decimal(0)
            margin = _numeric(abs(self.baseline * self.tolerance) + sign * Decimal(count) * Decimal("0.000000005"),
                              _EIGHT_PLACES)
            return Between(base - margin, base + margin)

    def __str__(self) -> str:
        return f"{self.baseline} within tolerance {self.tolerance}"


def _whole(name: str, value) -> Decimal:
    """A value hash or epoch sum baseline: an exact whole number (its text is `NUMERIC(38,0)`), never a float."""
    number = as_decimal(name, value)
    if number != number.to_integral_value():
        raise ParameterError(f"{name} must be a whole number, got {value!r}")
    return number


def _match_sum_condition(params: dict) -> SumTolerance:
    return SumTolerance(as_decimal("baseline_sum", params.get("baseline_sum")),
                        as_decimal("upper_tolerance", params.get("upper_tolerance")))


def _match_stdev_condition(params: dict) -> Between:
    """`CAST(SD AS DOUBLE PRECISION) -/+ ABS(CAST(SD AS DOUBLE PRECISION) * {UPPER_TOLERANCE})`, in doubles."""
    sd = float(as_number("baseline_sd", params.get("baseline_sd")))
    margin = abs(sd * float(as_number("upper_tolerance", params.get("upper_tolerance"))))
    return Between(sd - margin, sd + margin)


#: Conditions that are an expression rather than one placeholder: the SHA-256 of the vendored Postgres
#: `test_condition` each port was made from, and the port (params -> limit). The test types in EXACT_TEST_TYPES
#: compare as Decimal, never as float.
CONDITION_PORTS: dict[str, tuple[str, Callable[[dict], Any]]] = {
    # CAST({BASELINE_VALUE} AS DOUBLE PRECISION)
    "Match_Min_Val": ("8a31c67d694efd17306b33f1e680fc5573fefdc4af6fba52bebe86bc69ffe380",
                      lambda params: float(as_number("baseline_value", params.get("baseline_value")))),
    "Match_Max_Val": ("8a31c67d694efd17306b33f1e680fc5573fefdc4af6fba52bebe86bc69ffe380",
                      lambda params: float(as_number("baseline_value", params.get("baseline_value")))),
    # CAST({BASELINE_SUM} AS NUMERIC(38,8)) - CAST(ABS({BASELINE_SUM} * {UPPER_TOLERANCE}) + SIGN(ABS({UPPER_TOLERANCE}))
    #   * COUNT({COLUMN_NAME}) * 0.000000005 AS NUMERIC(38,8)) AND <the same, +>
    "Match_Sum": ("0d6aa8bba2b6a3646a3f6498f845d88ede57123b5a6ea44c91ffc882331f1efc", _match_sum_condition),
    # CAST({BASELINE_SD} AS DOUBLE PRECISION) - ABS(CAST({BASELINE_SD} AS DOUBLE PRECISION) * {UPPER_TOLERANCE}) AND
    #   <the same, +>
    "Match_Stdev": ("83ee4adff0b3c7fff26b7b82b97a7df5729cdc33407733e6935f2748099ef7ac", _match_stdev_condition),
    # ROUND({BASELINE_SUM}, 5)
    "Match_Fractional_Sum": ("8404752672096b49e556cad30e0a0c0252e0e4220f3fe47c295d8038803507ec",
                             lambda params: _numeric(as_decimal("baseline_sum", params.get("baseline_sum")),
                                                     _FIVE_PLACES)),
    # CAST('{BASELINE_VALUE}' AS NUMERIC(38,0)) : a hash is a whole number, compared exactly
    **{name: ("504c839492645711c45a4cc6fd54401b7b51b132925cc86c7aba7abed4c8ec09",
              lambda params: _whole("baseline_value", params.get("baseline_value")))
       for name in STEP_8_TEST_TYPES if name != "Match_Epoch_Sum"},
    # CAST('{BASELINE_SUM}' AS NUMERIC(38,0))
    "Match_Epoch_Sum": ("2f6fca3a9b6a0c09398fbe6015cbbaacca8650eb631eb320f97bf9b55371f237",
                        lambda params: _whole("baseline_sum", params.get("baseline_sum"))),
}
#: Types whose measure is an exact NUMERIC: SUM(CAST(... AS NUMERIC(38,8))) and the fractional sum at NUMERIC(38,5).
EXACT_TEST_TYPES = frozenset({"Match_Sum", "Match_Fractional_Sum"}) | frozenset(STEP_8_TEST_TYPES)
#: Run-time aggregates a condition reads beyond the measure, as vendored text (Match_Sum's tolerance counts values).
CONDITION_AGGREGATES = {"Match_Sum": "COUNT({COLUMN_NAME})"}


#: The parameter each ported expression condition is built around: where a threshold edited in Egeria goes back in.
_CONDITION_PORT_KEYS = {"Match_Min_Val": "baseline_value", "Match_Max_Val": "baseline_value",
                        "Match_Sum": "baseline_sum", "Match_Stdev": "baseline_sd", "Match_Fractional_Sum": "baseline_sum",
                        **{n: "baseline_value" for n in STEP_8_TEST_TYPES if n != "Match_Epoch_Sum"},
                        "Match_Epoch_Sum": "baseline_sum"}


def threshold_key(test_type: TestType) -> str:
    """The accepted parameter the condition reads (`threshold_value`, `baseline_ct`, ...): where a threshold edited in
    Egeria goes back in. For a pinned expression condition (`CONDITION_PORTS`), the baseline it is built around (a
    range's tolerance stays the accepted one). Raises ParameterError when the vendored condition is neither one
    placeholder nor a pinned port."""
    if test_type.test_type in CONDITION_PORTS:
        return _CONDITION_PORT_KEYS[test_type.test_type]
    key = _CONDITIONS.get(test_type.test_condition.strip())
    if key is None:
        raise ParameterError(f"{test_type.test_type}: condition {test_type.test_condition!r} is not one placeholder "
                             f"this port reads")
    return key


def threshold(test_type: TestType, params: dict) -> Any:
    """The condition's value, validated: a number; for a text measure (`measure_is_text`) the baseline date text; for
    an exact measure a Decimal; for a NOT BETWEEN condition a `Between` (or Match_Sum's `SumTolerance`, whose bounds
    the run finishes). A condition that is neither one placeholder nor a pinned port means the vendored condition
    changed under us."""
    if test_type.test_type in CONDITION_PORTS:
        sha, port = CONDITION_PORTS[test_type.test_type]
        if sha256_text(test_type.test_condition) != sha:
            raise ParameterError(f"{test_type.test_type}'s vendored test_condition changed: re-port it")
        return port(params)
    key = threshold_key(test_type)
    if test_type.measure_is_text:
        return as_to_char_seconds(key, params.get(key))
    return as_number(key, params.get(key))


def published_threshold(test_type: TestType, params: dict) -> list[str]:
    """`thresholdValues` as a published DataQualityRule carries them: the condition's value as text. For a pinned
    expression condition it is the baseline the condition is built around (`threshold_key`), not the computed limit:
    Match_Sum's bounds depend on the run's count, and a value edited in Egeria goes back into that same parameter.
    The tolerance of a range rides in `additionalProperties.upperTolerance`. Validated by `threshold` first."""
    if test_type.run_type != RUN_CAT:
        raise ParameterError(f"{test_type.test_type} is run as {test_type.run_type}, not as a measure with a condition, "
                             f"so it is not published as a DataQualityRule yet")
    limit = threshold(test_type, params)
    if test_type.test_type in CONDITION_PORTS:
        return [str(params.get(threshold_key(test_type)))]
    return [str(limit)]


def fails(test_type: TestType, measure: Any, limit: Any) -> bool:
    """TestGen's fail-when comparison, `measure <op> condition`, evaluated by RE on a non-NULL measure."""
    try:
        return _FAIL_WHEN[test_type.test_operator](measure, limit)
    except KeyError:
        raise ParameterError(f"{test_type.test_type}: operator {test_type.test_operator!r} is not supported") from None


def check_key(schema: str, table: str, column: str, test_type: str) -> str:
    """`schema.table.column::Test_Type`, or `schema.table::Test_Type` for a table check (design §5)."""
    path = f"{schema}.{table}.{column}" if column else f"{schema}.{table}"
    return f"{path}::{test_type}"
