"""Sampling strategy as configuration, and the provenance every sampled
answer has to carry.

Implements `docs/multi-resource-questions-design.md` §5.8 — Phase 1 slice 10.

§5.8 is a *configuration surface*, not one knob: six strategies, three
bounds, a seed and a time budget, resolvable at four scopes (global → resource
type → resource → run). And two rules that follow from it, both of which this
module exists to make hard to get wrong:

1. **The envelope carries the strategy.** An answer reads "from a random
   sample of 10,000 of 4.2M rows (seed 7, 2026-09-21)" so a consumer can tell
   a sampled null-fraction from a `pg_stats` one from a full scan.
   `SampleProvenance.describe()` is the one place that sentence is built.
2. **Matching thresholds are stated against the sample** — "conforms to Data
   Class X with 0.98 of sampled values", never an unqualified "conforms". See
   `column_matching.py`, which refuses to emit a conformance claim without a
   `SampleProvenance` to qualify it.

No pyegeria and no psycopg2 import here: this module builds SQL *text* and
describes samples. `database_surveyor.py` executes it. That split is what
lets the SQL shape be tested without a live Postgres (there is none in the
build environment — see `POSTGRES-COLUMN-PROFILE-IMPLEMENTED.md`).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

# ── The six strategies (§5.8's `strategy` row) ────────────────────────────────
#
# String constants rather than an Enum because these values are configuration:
# they arrive from a settings dict / a registry row / a step parameter, and they
# are written back out into `database_column_profiles.sample_strategy` and into
# an annotation's properties. An Enum would mean converting at four boundaries.

#: Read nothing. Profile from the database's own `pg_stats` only (slice 7's
#: path). The default for *measures* — and the reason `column_matching.py`
#: needs a "not sampled at all" state distinct from "no match found": under
#: this strategy no value was ever read, so nothing can be said about
#: value-pattern conformance. That is `not_established`, not "no match".
CATALOG_STATS_ONLY = "catalog_stats_only"

#: `LIMIT n` with no ordering — fast and biased toward whatever the heap
#: returns first (in practice the oldest rows). Never a default; an explicit
#: choice for "I only need to see the shape of a value".
HEAD = "head"

#: `TABLESAMPLE SYSTEM`/`BERNOULLI` with `REPEATABLE (seed)`. The default for
#: *value matching*. §5.8 names `TABLESAMPLE` specifically, in preference to
#: `ORDER BY random() LIMIT n` — the latter sorts the whole table to return a
#: handful of rows, which is exactly the cost this tier must not pay.
RANDOM = "random"

#: Every n-th row, by `row_number()`. Deterministic and unbiased with respect
#: to insertion order, and it reads the whole table to get there — an explicit
#: choice, priced accordingly.
SYSTEMATIC = "systematic"

#: n rows per stratum of a named column (a partition key, a date). What a
#: date-range or reference-set question actually needs: a random sample of a
#: table whose rows are 99% one status value will not find the other four.
STRATIFIED = "stratified"

#: Every row. §5.8: "a deliberate choice, never a default."
FULL = "full"

SAMPLING_STRATEGIES: tuple[str, ...] = (
    CATALOG_STATS_ONLY,
    HEAD,
    RANDOM,
    SYSTEMATIC,
    STRATIFIED,
    FULL,
)

#: Strategies that read no table data at all. A caller that has one of these
#: must render value-derived findings as `not_established` — see
#: `column_matching.MATCH_NOT_SAMPLED`.
NON_SAMPLING_STRATEGIES: frozenset[str] = frozenset({CATALOG_STATS_ONLY})


class SamplingConfigError(ValueError):
    """An unusable sampling configuration — an unknown strategy, a
    non-positive bound, or `stratified` with no column to stratify by.

    Raised rather than silently corrected. A survey that quietly downgraded
    `stratified` to `random` because no strata column was supplied would
    record `random` in its provenance and be *correct* about what ran while
    being wrong about what was asked for, and nobody would ever look.
    """


# ── §5.8's defaults table ─────────────────────────────────────────────────────

#: §5.8: 10,000 rows, 64 MB, 1,000 values per column, 60s per step.
DEFAULT_MAX_ROWS = 10_000
DEFAULT_MAX_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_VALUES = 1_000
DEFAULT_TIME_BUDGET_SECONDS = 60


@dataclass(frozen=True)
class SamplingConfig:
    """One resolved sampling configuration (§5.8's settings table).

    Frozen: a config is resolved once per (step, resource) and then read by
    the SQL builder, the matcher and the provenance recorder. A mutable one
    could be changed between the sample being taken and the provenance being
    written, which is the single thing §5.8's first rule exists to prevent.
    Use `merged_with()` / `dataclasses.replace` to derive a narrower scope.
    """

    strategy: str = CATALOG_STATS_ONLY
    max_rows: int = DEFAULT_MAX_ROWS
    max_bytes: int = DEFAULT_MAX_BYTES
    max_values: int = DEFAULT_MAX_VALUES
    #: §5.8: "fixed per resource — reproducible samples, so two runs differ
    #: because the data did." `None` means "not pinned", which makes a sample
    #: unreproducible; `resolve_sampling_config` derives a stable seed from
    #: the resource slug rather than leaving it None, so this default is only
    #: reached by a caller constructing a config by hand.
    seed: int | None = None
    time_budget_seconds: int = DEFAULT_TIME_BUDGET_SECONDS
    #: Required by, and only meaningful for, `STRATIFIED`.
    strata_column: str = ""
    #: Which scope this config was resolved from — "global" / "resource_type" /
    #: "resource" / "run". Recorded so a surprising sample can be traced to the
    #: setting that produced it rather than guessed at.
    scope: str = "global"

    def __post_init__(self) -> None:
        if self.strategy not in SAMPLING_STRATEGIES:
            raise SamplingConfigError(
                f"Unknown sampling strategy {self.strategy!r}. "
                f"§5.8 defines exactly: {', '.join(SAMPLING_STRATEGIES)}."
            )
        for name in ("max_rows", "max_bytes", "max_values"):
            value = getattr(self, name)
            if not isinstance(value, int) or value <= 0:
                raise SamplingConfigError(
                    f"{name} must be a positive integer (got {value!r}) — a "
                    "zero or negative bound would read either nothing or "
                    "everything, and which of those it meant would depend on "
                    "the caller."
                )
        if not isinstance(self.time_budget_seconds, int) or self.time_budget_seconds <= 0:
            raise SamplingConfigError(
                f"time_budget_seconds must be a positive integer "
                f"(got {self.time_budget_seconds!r})."
            )
        if self.strategy == STRATIFIED and not self.strata_column:
            raise SamplingConfigError(
                "strategy 'stratified' needs a `strata_column` to stratify by "
                "(§5.8: 'by a column, e.g. partition key or date'). Refusing "
                "rather than falling back to 'random': the provenance would "
                "then record a strategy nobody asked for."
            )
        if self.seed is not None and not isinstance(self.seed, int):
            raise SamplingConfigError(f"seed must be an int or None (got {self.seed!r}).")

    @property
    def reads_values(self) -> bool:
        """Whether this strategy reads any table data.

        The gate for every value-derived finding. False under
        `catalog_stats_only`, which is what makes "no values were read" a
        first-class state rather than an empty result set.
        """
        return self.strategy not in NON_SAMPLING_STRATEGIES

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "max_rows": self.max_rows,
            "max_bytes": self.max_bytes,
            "max_values": self.max_values,
            "seed": self.seed,
            "time_budget_seconds": self.time_budget_seconds,
            "strata_column": self.strata_column,
            "scope": self.scope,
        }

    def merged_with(self, overrides: dict[str, Any] | None, scope: str) -> SamplingConfig:
        """This config with `overrides` applied, tagged as resolved at `scope`.

        Unknown keys are ignored rather than raising: a settings blob may
        legitimately carry keys for a different resource type. An unknown
        *strategy* still raises, via `__post_init__` — that is a typo in the
        one field whose value changes what runs.
        """
        if not overrides:
            return replace(self, scope=scope) if scope != self.scope else self
        known = {f for f in self.as_dict() if f != "scope"}
        # `time_budget` is §5.8's own name for the setting; accept it as an
        # alias so a settings file can use the design doc's spelling.
        cleaned = dict(overrides)
        if "time_budget" in cleaned and "time_budget_seconds" not in cleaned:
            cleaned["time_budget_seconds"] = cleaned.pop("time_budget")
        applied = {k: v for k, v in cleaned.items() if k in known}
        return replace(self, scope=scope, **applied)


#: §5.8's two defaults, named. "`catalog_stats_only` for measures; `random`
#: for value matching" — one step (`postgres_column_profile`) therefore
#: resolves TWO configs, not one, and that is deliberate: a per-column null
#: fraction should come free from `pg_stats`, while a data-class match cannot
#: come from `pg_stats` at all (design §5.1: "the only route for data-class
#: and reference-data matching, which need actual values").
DEFAULT_MEASURE_SAMPLING = SamplingConfig(strategy=CATALOG_STATS_ONLY)
DEFAULT_MATCHING_SAMPLING = SamplingConfig(strategy=RANDOM)


def stable_seed_for(resource_slug: str) -> int:
    """A seed fixed per resource (§5.8's `seed` default), derived from the slug.

    Deterministic across processes and Python versions — `hash()` is salted
    per interpreter run and would give a different sample every restart, which
    is the precise opposite of what this setting is for.
    """
    import hashlib

    digest = hashlib.sha256(resource_slug.encode("utf-8")).digest()
    # Postgres's REPEATABLE takes a double-precision seed; keep it small and
    # positive so it round-trips through JSON, a TEXT column and SQL alike.
    return int.from_bytes(digest[:4], "big") % 1_000_000


def resolve_sampling_config(
    *,
    purpose: str = "matching",
    resource_slug: str = "",
    global_settings: dict[str, Any] | None = None,
    resource_type_settings: dict[str, Any] | None = None,
    resource_settings: dict[str, Any] | None = None,
    run_settings: dict[str, Any] | None = None,
) -> SamplingConfig:
    """Resolve §5.8's four scopes, narrowest wins.

    `global → per resource type → per resource → per run`, exactly the order
    §5.8's scope row gives. `purpose` selects which of the two defaults the
    chain starts from: "measures" → `catalog_stats_only`, "matching" →
    `random`.

    The resolved `scope` field names the *narrowest scope that supplied any
    override*, so a production database's stricter setting is attributable
    (§5.8: "a production database gets a stricter default than a sample one").
    """
    if purpose not in ("measures", "matching"):
        raise SamplingConfigError(
            f"purpose must be 'measures' or 'matching' (got {purpose!r}) — "
            "§5.8 gives one default per use case, not one overall."
        )
    config = DEFAULT_MEASURE_SAMPLING if purpose == "measures" else DEFAULT_MATCHING_SAMPLING
    if config.seed is None and resource_slug:
        config = replace(config, seed=stable_seed_for(resource_slug))
    for scope, overrides in (
        ("global", global_settings),
        ("resource_type", resource_type_settings),
        ("resource", resource_settings),
        ("run", run_settings),
    ):
        if overrides:
            config = config.merged_with(overrides, scope)
    return config


# ── Provenance: §5.8's first rule ─────────────────────────────────────────────

#: `total_rows` is unknown. Distinct from 0 (an empty table, which IS a
#: measurement) — a sample "of 10,000 of 0 rows" is nonsense, and a sample of
#: 10,000 of an unknown total cannot be called representative.
TOTAL_ROWS_NOT_ESTABLISHED = "not_established"


@dataclass(frozen=True)
class SampleProvenance:
    """What was actually looked at — the envelope §5.8 requires.

    Recorded from the *execution*, never from the config: `strategy` here is
    the strategy that RAN. Those differ in one real case this module allows —
    `random` on a table whose total row count is unknown still runs, with an
    estimated fraction, and `describe()` says so rather than implying a
    proportion it cannot support.
    """

    strategy: str
    #: Rows/values actually returned. `None` when nothing was sampled.
    sample_rows: int | None = None
    #: The table's total row count, as known at sample time. `None` → unknown
    #: (see `TOTAL_ROWS_NOT_ESTABLISHED`).
    total_rows: int | None = None
    seed: int | None = None
    sampled_at: str = ""
    #: The `TABLESAMPLE` method that ran (`SYSTEM` / `BERNOULLI`), or "".
    tablesample_method: str = ""
    #: The percentage passed to `TABLESAMPLE`, or `None`.
    tablesample_percent: float | None = None
    #: True when the per-column distinct/value list hit `max_values` and was
    #: truncated. A truncated list cannot support a *coverage* claim — see
    #: `column_matching`, which downgrades to inconclusive rather than
    #: reporting a partial match against a list it knows is incomplete.
    truncated: bool = False
    #: Populated when the strategy did not read values at all.
    reason_not_sampled: str = ""
    #: True only when `reason_not_sampled` names an actual query FAILURE
    #: (§F, 2026-09-28) — a real exception the connection raised — as opposed
    #: to the other, benign reasons `reason_not_sampled` already carries
    #: (`catalog_stats_only`, the byte/time budget spent, a view/foreign
    #: table §F declares out of scope). `run_column_profile` uses this to
    #: count sampled/skipped/errored separately and report the step's own
    #: status as "partial" rather than "ok" when it is ever true, without
    #: string-matching `reason_not_sampled`'s free text to tell the cases
    #: apart.
    errored: bool = False
    #: True when this column's row was never attempted because it belongs to
    #: a relation kind `TABLESAMPLE` cannot apply to at all (a view or a
    #: foreign table) — distinct from `errored`: this is a declared-in-advance
    #: skip, not a failure, and `_profile_row` reports it as
    #: `STATE_NOT_APPLICABLE` rather than `STATE_NOT_COLLECTED`.
    not_applicable: bool = False

    @property
    def reads_values(self) -> bool:
        return self.strategy not in NON_SAMPLING_STRATEGIES

    def describe(self) -> str:
        """§5.8's sentence, built in exactly one place.

        "from a random sample of 10,000 of 4.2M rows (seed 7, 2026-09-21)".
        """
        if not self.reads_values:
            reason = self.reason_not_sampled or (
                "no table data was read (catalog statistics only)"
            )
            return f"no sample taken: {reason}"
        rows = "an unrecorded number of" if self.sample_rows is None else f"{self.sample_rows:,}"
        if self.total_rows is None:
            total = "an unknown total number of rows"
        else:
            total = f"{self.total_rows:,} rows"
        parts = [f"from a {self.strategy} sample of {rows} of {total}"]
        detail = []
        if self.tablesample_method:
            pct = (
                f" {self.tablesample_percent:g}%"
                if self.tablesample_percent is not None
                else ""
            )
            detail.append(f"TABLESAMPLE {self.tablesample_method}{pct}")
        if self.seed is not None:
            detail.append(f"seed {self.seed}")
        if self.sampled_at:
            detail.append(self.sampled_at)
        if self.truncated:
            detail.append("value list truncated at the per-column bound")
        if detail:
            parts.append(f" ({', '.join(detail)})")
        return "".join(parts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "sample_rows": self.sample_rows,
            "total_rows": self.total_rows if self.total_rows is not None else TOTAL_ROWS_NOT_ESTABLISHED,
            "seed": self.seed,
            "sampled_at": self.sampled_at,
            "tablesample_method": self.tablesample_method,
            "tablesample_percent": self.tablesample_percent,
            "truncated": self.truncated,
            "reason_not_sampled": self.reason_not_sampled,
            "errored": self.errored,
            "not_applicable": self.not_applicable,
            "envelope": self.describe(),
        }


def not_sampled_provenance(
    config: SamplingConfig, reason: str = "", *, errored: bool = False,
    not_applicable: bool = False,
) -> SampleProvenance:
    """Provenance for a column no value was read from.

    The honest record of `catalog_stats_only`, of an absent capability, and of
    a table the sampler could not reach — all three are "nothing was looked
    at", and all three must render differently from "we looked and found
    nothing".

    `errored`/`not_applicable` (§F, 2026-09-28): see `SampleProvenance`'s own
    docstrings for each field. Both default False, which is every existing
    call site's behaviour unchanged.
    """
    return SampleProvenance(
        strategy=config.strategy if not config.reads_values else CATALOG_STATS_ONLY,
        reason_not_sampled=reason
        or (
            "strategy is catalog_stats_only, so no table data was read"
            if not config.reads_values
            else "no sample was taken"
        ),
        errored=errored,
        not_applicable=not_applicable,
    )


# ── SQL construction ──────────────────────────────────────────────────────────

#: Below this many rows a `TABLESAMPLE SYSTEM` sample is worthless: SYSTEM
#: picks whole *pages*, and a table occupying a handful of pages either
#: returns almost everything or almost nothing. BERNOULLI (per-row) is the
#: right method there, and on a table this size reading every row to decide is
#: cheap anyway.
BERNOULLI_ROW_THRESHOLD = 50_000

#: Above this requested fraction, SYSTEM's page-level clumping stops buying
#: anything — it would read most of the pages regardless — so prefer
#: BERNOULLI's unbiased per-row selection.
BERNOULLI_PERCENT_THRESHOLD = 5.0

#: `TABLESAMPLE SYSTEM` returns approximately this fraction of *pages*, and a
#: page's rows are not the rows we want (NULLs are filtered, and a `LIMIT`
#: follows). Over-request so the LIMIT is what bounds the result rather than
#: the sample coming up short. Measured against nothing live — this is a
#: judgement, and it is recorded in the provenance as the percent that ran, so
#: a real deployment can see whether it was enough.
SYSTEM_OVERSAMPLE_FACTOR = 3.0

#: The fraction to request when the table's total row count is unknown, so
#: `random` can still run rather than silently becoming `head`.
UNKNOWN_TOTAL_PERCENT = 1.0

#: `TABLESAMPLE` percentages below this are rejected by some planners as
#: degenerate and return nothing useful.
MIN_TABLESAMPLE_PERCENT = 0.0001


def quote_ident(name: str) -> str:
    """Quote one SQL identifier.

    These names come from the database's own catalog, not from a user — but
    they reach a query as text, and a column legitimately named `order` or
    `Select` breaks an unquoted statement. Interpolating a catalog name is the
    only option: an identifier cannot be a bound parameter.
    """
    if not isinstance(name, str) or not name:
        raise SamplingConfigError(f"Not a usable SQL identifier: {name!r}")
    if "\x00" in name:
        raise SamplingConfigError("SQL identifiers cannot contain a NUL byte")
    return '"' + name.replace('"', '""') + '"'


def qualified_table(schema_name: str, table_name: str) -> str:
    return f"{quote_ident(schema_name)}.{quote_ident(table_name)}"


def choose_tablesample_method(
    total_rows: int | None, percent: float
) -> str:
    """SYSTEM or BERNOULLI, per §5.8's "`TABLESAMPLE SYSTEM`/`BERNOULLI`".

    §5.8 names both and does not choose between them, so this is the slice's
    judgement call, made once and explicitly:

    - **BERNOULLI** on a small table (`< BERNOULLI_ROW_THRESHOLD`) or a large
      requested fraction (`>= BERNOULLI_PERCENT_THRESHOLD`). It considers
      every row, so the sample is unbiased; on a small table, or when most
      pages would be read anyway, that costs little.
    - **SYSTEM** otherwise — the cheap path, and the reason §5.8 prefers
      `TABLESAMPLE` to `ORDER BY random()`: it reads a fraction of the pages
      instead of sorting the table.

    An unknown total row count chooses BERNOULLI: with no size to reason
    about, correctness of the sample beats a cost saving that cannot be
    justified.
    """
    if total_rows is None:
        return "BERNOULLI"
    if total_rows < BERNOULLI_ROW_THRESHOLD:
        return "BERNOULLI"
    if percent >= BERNOULLI_PERCENT_THRESHOLD:
        return "BERNOULLI"
    return "SYSTEM"


def tablesample_percent(config: SamplingConfig, total_rows: int | None) -> float:
    """The percentage to hand `TABLESAMPLE` so the sample can fill `max_rows`."""
    if total_rows is None:
        return UNKNOWN_TOTAL_PERCENT
    if total_rows <= 0:
        return 100.0
    raw = 100.0 * config.max_rows * SYSTEM_OVERSAMPLE_FACTOR / float(total_rows)
    return min(100.0, max(MIN_TABLESAMPLE_PERCENT, raw))


@dataclass(frozen=True)
class SampleQuery:
    """A built sample query and what it will mean when it runs."""

    sql: str
    #: The strategy this SQL implements — what the provenance will record.
    strategy: str
    tablesample_method: str = ""
    tablesample_percent: float | None = None
    params: tuple = field(default_factory=tuple)


def build_column_sample_sql(
    schema_name: str,
    table_name: str,
    column_name: str,
    config: SamplingConfig,
    total_rows: int | None = None,
) -> SampleQuery | None:
    """SQL that reads up to `max_values` values of one column.

    Returns `None` — never an empty query, never a query that reads
    everything — when the configured strategy reads no data. A caller that
    treats `None` as "no values found" has made exactly the mistake this
    slice's absence discipline is about; the `column_matching` module takes
    `None` and produces `MATCH_NOT_SAMPLED`.

    NULLs are excluded: a data-class or reference-data match is about the
    values that are present, and slice 7's `pg_stats.null_frac` already
    answers "how many are missing" without reading a row.
    """
    if not config.reads_values:
        return None

    col = quote_ident(column_name)
    table = qualified_table(schema_name, table_name)
    limit = min(config.max_values, config.max_rows)
    where = f"WHERE {col} IS NOT NULL"

    if config.strategy == HEAD:
        return SampleQuery(
            sql=f"SELECT {col} AS value FROM {table} {where} LIMIT {limit}",
            strategy=HEAD,
        )

    if config.strategy == RANDOM:
        percent = tablesample_percent(config, total_rows)
        method = choose_tablesample_method(total_rows, percent)
        repeatable = f" REPEATABLE ({config.seed})" if config.seed is not None else ""
        return SampleQuery(
            sql=(
                f"SELECT {col} AS value FROM {table} "
                f"TABLESAMPLE {method} ({percent:g}){repeatable} "
                f"{where} LIMIT {limit}"
            ),
            strategy=RANDOM,
            tablesample_method=method,
            tablesample_percent=percent,
        )

    if config.strategy == SYSTEMATIC:
        # Every n-th row. `row_number() OVER ()` over the heap order is the
        # only stable "n-th" available without a guaranteed ordering column,
        # and it reads the whole table — which is the price §5.8 attaches to
        # this strategy, not an oversight.
        step = max(1, (total_rows // config.max_rows) if total_rows else 1)
        return SampleQuery(
            sql=(
                f"SELECT value FROM (SELECT {col} AS value, "
                f"row_number() OVER () AS rn FROM {table} {where}) s "
                f"WHERE (rn - 1) % {step} = 0 LIMIT {limit}"
            ),
            strategy=SYSTEMATIC,
        )

    if config.strategy == STRATIFIED:
        strata = quote_ident(config.strata_column)
        # Rows per stratum rather than rows overall: the whole point is that a
        # rare stratum is represented at all. `max_rows` still caps the total.
        per_stratum = max(1, limit // 10)
        return SampleQuery(
            sql=(
                f"SELECT value FROM (SELECT {col} AS value, "
                f"row_number() OVER (PARTITION BY {strata} ORDER BY {strata}) AS rn "
                f"FROM {table} {where}) s "
                f"WHERE rn <= {per_stratum} LIMIT {limit}"
            ),
            strategy=STRATIFIED,
        )

    if config.strategy == FULL:
        # No LIMIT: `full` means every row, and §5.8 makes it a deliberate
        # choice for exactly that reason. `max_bytes` is still the caller's
        # bound (see `bytes_budget_exceeded`) — this is the one strategy where
        # the per-column value cap is not applied, because applying it would
        # make `full` a synonym for `head`.
        return SampleQuery(
            sql=f"SELECT {col} AS value FROM {table} {where}",
            strategy=FULL,
        )

    raise SamplingConfigError(f"No SQL builder for strategy {config.strategy!r}")


def build_distinct_values_sql(
    schema_name: str,
    table_name: str,
    column_name: str,
    config: SamplingConfig,
    total_rows: int | None = None,
) -> SampleQuery | None:
    """Distinct values with their counts, for `reference_data_match`.

    Wraps `build_column_sample_sql` so reference-data matching samples by
    exactly the same configured strategy as everything else — and so the
    `max_values` bound applies to the DISTINCT list rather than to the rows
    read, which is what "max_values per column" means in §5.8.

    One extra value is requested beyond `max_values`. That is the truncation
    detector: `max_values + 1` rows back means the distinct list is incomplete,
    the caller sets `SampleProvenance.truncated`, and no coverage claim may be
    made from it. Without the +1, a column with exactly `max_values` distinct
    values and one with a million would be indistinguishable.
    """
    inner = build_column_sample_sql(
        schema_name, table_name, column_name, config, total_rows
    )
    if inner is None:
        return None
    return SampleQuery(
        sql=(
            f"SELECT value, count(*) AS value_count FROM ({inner.sql}) sample "
            f"GROUP BY value ORDER BY value_count DESC, value "
            f"LIMIT {config.max_values + 1}"
        ),
        strategy=inner.strategy,
        tablesample_method=inner.tablesample_method,
        tablesample_percent=inner.tablesample_percent,
    )


def bytes_budget_exceeded(config: SamplingConfig, bytes_read: int) -> bool:
    """Whether `max_bytes` has been spent (§5.8's second bound).

    A separate function rather than an inline comparison because the *caller*
    must react by recording a truncated sample, not by dropping the column
    silently — a column skipped for budget is `not_established`, same as one
    never sampled.
    """
    return bytes_read >= config.max_bytes


def estimated_value_bytes(values: list[Any]) -> int:
    """A cheap byte estimate for a fetched value list.

    Deliberately an estimate: the exact on-wire cost is unknowable from
    Python objects, and the purpose is to stop reading, not to bill anyone.
    """
    total = 0
    for value in values:
        if value is None:
            continue
        if isinstance(value, (bytes, bytearray)):
            total += len(value)
        else:
            total += len(str(value).encode("utf-8", errors="replace"))
    return total
