"""`db_derived` — the zero-fetch derivation step for databases.

Phase 1 slice 9. Implements `docs/multi-resource-questions-design.md` §5.3
(Discovery: *reason over what Scouting stored, zero new fetch*), the
`schema_conventions`, `db_change_rates` and proposed-`DataScope` rows of §5.4,
and §5.7's own `db_derived` line: *"reads stored rows only — cost none/low —
produces Classification (db kind), DataGrain, Fingerprint, conventions checks,
change rates, proposed DataScope."*

**This module opens no connection of any kind.** Not to the surveyed database,
not to Egeria. Every input is a row RE already stored: the structured tables
Phase 0 stream 3 created (`database_schemas`, `database_tables`,
`database_columns`, `database_column_profiles`, `database_table_activity`) and
populated further by slices 7 and 8. It is deliberately NOT a
`DatabaseSurveyor.survey()` step, because `survey()` opens
`database_connection(...)` unconditionally before dispatching any step — a
zero-fetch step living inside it would pay for a connection it never uses, and
would be unable to run at all for a database whose credentials are gone. The
entry point is `run_db_derived(registry, slug)`, which takes a registry and a
slug and nothing else. `tests/test_db_derived_step.py` pins that with a test
that fails if `database_connection` is so much as referenced.

## Absence discipline

Four states, the same vocabulary slices 7 and 8 use (`registry.STATE_*`), and
the distinction this whole module turns on:

- **A measured negative is a real finding.** "This database has no foreign
  keys at all — it is a bag of tables" is an answer, not an absence. So is "no
  table in the registry structurally resembles this one".
- **Not established is not a negative.** "No survey ever captured key
  information for these rows" is not "there are no keys". "Only one survey run
  exists" is not "nothing changed". "No other database has stored schema rows"
  is not "this is not a copy".

Every check below names which of the two it is producing, and the confidence
of an inferred finding is computed from how much signal was actually available
(see `_CLASSIFICATION_SIGNAL_WEIGHTS`) rather than asserted.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from resource_explorer.registry import (
    SOURCE_LOCAL,
    STATE_MEASURED,
    STATE_NOT_COLLECTED,
    STATE_NOT_MEASURED,
)
from resource_explorer.surveyors.database import schema_scope
from resource_explorer.surveyors.survey_report import (
    ClassificationAnnotation,
    DataGrainAnnotation,
    FingerprintAnnotation,
    ResourceMeasureAnnotation,
    SchemaAnalysisAnnotation,
)

log = logging.getLogger(__name__)

#: The `re_analysis_step` key, and the `analysis_step` stamped on every
#: annotation this module produces.
ANALYSIS_STEP = "db_derived"

#: The analysis-catalog ids this one step backs (design §5.7 folds all six
#: into `db_derived`). Consumed by the web per-card dispatch and by
#: `survey_definition_adapter`, so there is one list rather than three.
DB_DERIVED_ANALYSES: tuple[str, ...] = (
    "db_classification",
    "db_relationship_graph",
    "grain_determination",
    "db_fingerprint",
    "schema_conventions",
    "db_change_rates",
    # Added for Phase 1 slice 14's remaining §9.1 comparators (schema_diff's
    # column/constraint half, and grant_change) — both are zero-fetch diffs
    # over already-stored rows, same shape as db_change_rates, and each needs
    # its own analysis_id so a subscription to it can be scheduled and
    # checked independently (see db_derived.py's own §7/§8 sections and
    # db_change_comparator.py).
    "schema_diff",
    "grant_change",
    # Design §16.3's Scouting and Discovery rows (added 2026-09-24). All three
    # are zero-fetch derivations over the same stored rows every check above
    # reads, so they belong to this step rather than to a new one — and
    # `db_derived`'s `requires_capability` stays UNDECLARED (`""`) rather than
    # becoming `catalog`, for exactly the reason the audit gives in
    # `survey_definition_adapter.py`: the weakest tier still implies a live
    # connection, and these open none.
    "subject_signals",
    "coverage_signals",
    "preliminary_fit",
)

#: NOTE on the two annotation sites that carry this check's name: they spell
#: it as a LITERAL rather than referencing this constant, deliberately.
#: `tests/test_annotation_check_names.py`'s shared-check-name guard only reads
#: `ast.Constant` keyword values, so a `check_name=<CONSTANT>` site is
#: invisible to it — using the constant there would have dodged the guard
#: rather than satisfied it. `TestPublishMapping` pins the literal against
#: this constant so the two cannot drift.
#:
#: `DataScope` is proposed by the `db_change_rates`/profile-reading half rather
#: than being its own catalog entry: design §5.4 hangs it off `column_profile`
#: ("[date ranges] → proposed DataScope"), and slice 9's job per the brief is
#: the *derivation*, not a new question row. It runs whenever this step runs.
_PROPOSED_SCOPE_CHECK = "proposed_data_scope"

# ── classification ─────────────────────────────────────────────────────────
#
# The kinds design §5.3 names, verbatim: "transactional, analytical, reference
# data, staging, a copy".
KIND_TRANSACTIONAL = "transactional"
KIND_ANALYTICAL = "analytical"
KIND_REFERENCE = "reference_data"
KIND_STAGING = "staging"
KIND_COPY = "copy"

_ALL_KINDS = (KIND_TRANSACTIONAL, KIND_ANALYTICAL, KIND_REFERENCE,
              KIND_STAGING, KIND_COPY)

#: Signal families and their weights. A family with no usable data is left
#: OUT of the denominator rather than contributing zeros — that is the
#: difference between "we had less to go on" (lower confidence, honest) and
#: "every kind scored zero on this axis" (a silent, confident lie). The
#: brief's own example is the one this protects: no tuple-counter data because
#: ANALYZE never ran must read as insufficient signal, never as "staging".
_CLASSIFICATION_SIGNAL_WEIGHTS: dict[str, float] = {
    #: keys, FK density, table widths, row-count skew
    "structure": 3.0,
    #: table-name prefixes/suffixes
    "naming": 2.0,
    #: read/write mix from pg_stat_user_tables tuple counters
    "activity": 3.0,
    #: structural similarity to another database in the registry
    "fingerprint": 2.0,
}
_TOTAL_CLASSIFICATION_WEIGHT = sum(_CLASSIFICATION_SIGNAL_WEIGHTS.values())

#: An inference never reaches certainty. 95 is the ceiling for a derived
#: classification however clean the signal; a direct measurement (the
#: conventions counts, a PK-derived grain) is not bounded by this.
_MAX_INFERRED_CONFIDENCE = 95
#: Below this, the step reports "no confident classification" rather than
#: naming a kind — the margin between the top two candidates was too small to
#: distinguish them. Stated here rather than buried in a comparison.
_MIN_CLASSIFICATION_CONFIDENCE = 25

#: Table-name patterns per kind. Matched against the bare table name,
#: lower-cased. Deliberately conservative: a pattern that fires on ordinary
#: business names would swamp the naming family.
_NAME_PATTERNS: dict[str, tuple[str, ...]] = {
    KIND_ANALYTICAL: (
        r"^dim[_.]", r"^fact[_.]", r"[_.]fact$", r"[_.]dim$", r"^agg[_.]",
        r"^mart[_.]", r"[_.]mart$", r"^cube[_.]", r"[_.]summary$",
        r"^rollup[_.]", r"[_.]rollup$", r"^daily[_.]", r"^monthly[_.]",
    ),
    KIND_REFERENCE: (
        r"^ref[_.]", r"^lookup[_.]", r"^lu[_.]", r"[_.]lookup$",
        r"[_.]codes?$", r"[_.]types?$", r"^code[_.]", r"[_.]enum$",
        r"[_.]categories$", r"[_.]status$",
    ),
    KIND_STAGING: (
        r"^stg[_.]", r"^staging[_.]", r"^tmp[_.]", r"^temp[_.]", r"^raw[_.]",
        r"[_.]raw$", r"[_.]stg$", r"[_.]staging$", r"^landing[_.]",
        r"^ingest[_.]", r"[_.]tmp$", r"[_.]temp$", r"[_.]wrk$", r"^wrk[_.]",
    ),
    KIND_COPY: (
        r"[_.]copy$", r"[_.]bak$", r"[_.]backup$", r"[_.]old$", r"[_.]new$",
        r"[_.]v\d+$", r"[_.]20\d\d\d*$", r"[_.]archive$", r"[_.]orig$",
        r"[_.]final$",
    ),
}

#: A table this wide is evidence of a denormalised/analytical shape rather
#: than a normalised transactional one. Not a cliff: evidence ramps to 1.0 at
#: twice this.
_WIDE_TABLE_COLUMNS = 20
#: At or below this many rows a table looks like a code/reference list rather
#: than a transaction store.
_SMALL_TABLE_ROWS = 1000

# ── fingerprint thresholds ─────────────────────────────────────────────────
#
# Stated as named constants because the brief asks for a *measure*, not a
# verdict pulled out of the air, and because a reader disputing a "likely
# copy" needs to see the line it crossed.

#: Column-signature Jaccard at or above this reads as the same database.
_COPY_JACCARD = 0.95
#: Containment (|A∩B| / |A|) at or above this, with B strictly larger, reads
#: as "A is a subset of B" — the case Jaccard alone misses, because a small
#: true subset of a large database has low Jaccard by construction.
_SUBSET_CONTAINMENT = 0.90
#: Shares meaningful structure without being a copy or a clean subset.
_RELATED_JACCARD = 0.50
#: Below this, not reported at all — incidental overlap (`id`, `created_at`)
#: between unrelated schemas.
_REPORTABLE_JACCARD = 0.30

#: Types whose stored profile min/max can carry a temporal range (§5.4's
#: "what is the data's scope in time"). Matched as a substring of the stored
#: `base_type`/`data_type`, so `timestamp with time zone` and `timestamptz`
#: both land.
_DATE_TYPE_MARKERS = ("date", "timestamp", "timestamptz", "datetime")

#: `snake_case`, the convention every table in this codebase's own registry
#: follows. A name that needs quoting in SQL (upper case, spaces, dashes) is
#: the violation this reports; it is not a style opinion so much as a
#: portability and tooling hazard.
_SNAKE_CASE_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


# ═══════════════════════════════════════════════════════════════════════════
# Inputs
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class DerivedInputs:
    """One snapshot's stored rows — everything the checks are allowed to read.

    Built by `load_inputs`. A frozen dataclass rather than a dict so a check
    cannot quietly reach for a table nobody loaded.
    """
    slug: str
    surveyed_at: str | None
    source: str | None
    schemas: list[dict] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)
    columns: list[dict] = field(default_factory=list)
    profiles: list[dict] = field(default_factory=list)
    activity: list[dict] = field(default_factory=list)

    @property
    def has_schema_rows(self) -> bool:
        return bool(self.tables or self.columns)

    @property
    def keys_were_captured(self) -> bool:
        """Did the survey that wrote these column rows record key information?

        The distinction this module cares about most. `result_materializer`
        writes `is_primary_key = None` for a NATIVE Egeria survey read-back
        ("Native does not report keys; leave is_primary_key absent rather
        than…"), and 0/1 for a local survey whose blob carried them. So:

        - every column row has `is_primary_key IS NULL` → keys were never
          captured. "No primary keys" is NOT established.
        - any row carries 0 or 1 → keys were captured, and a table with none
          genuinely has none.

        Without this test, a native-only survey would report every table as
        lacking a primary key, with total confidence, having never looked.
        """
        return any(c.get("is_primary_key") is not None for c in self.columns)

    def keys_captured_for_table(self, key: tuple[str, str]) -> bool:
        """The per-table counterpart of `keys_were_captured` (Slice 21b).

        `keys_were_captured` answers "was ANY column anywhere in this
        database's stored rows key-captured?" — a database-wide `any()`.
        That is right for gating a whole-database analysis on "were keys
        captured at all," but wrong for crediting individual tables: a
        database that is mostly catalog-only-fallback (Slice 21b's
        `pg_constraint` read, `connection.py`) with one live-surveyed table
        would have `keys_were_captured is True` from that one table alone,
        and every OTHER table's genuine "keys not captured" would then be
        silently read as "genuinely has no primary key" by any caller that
        gated on the single global boolean instead of asking per table —
        found in review, Slice 21b, 2026-09-26: exactly the confident-
        wrong-answer shape this module exists to prevent, just one level
        finer than the case `keys_were_captured` itself already guards
        against (a fully native-only survey with NO column ever captured).

        `False` (not captured for this table) when the table has no column
        rows at all — nothing to have captured yet, same "absence is not
        established" stance as the whole-database property for that case.
        """
        return any(
            c.get("is_primary_key") is not None
            for c in self.columns
            if _table_key(c) == key
        )


def load_inputs(
    registry,
    slug: str,
    surveyed_at: str | None = None,
    source: str | None = None,
) -> DerivedInputs:
    """Read one snapshot's stored rows. No fetch, no connection."""
    def _rows(table: str) -> list[dict]:
        try:
            return registry.query_detail_rows(table, slug, surveyed_at, source)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("db_derived: could not read %s for %s: %s", table, slug, exc)
            return []

    return DerivedInputs(
        slug=slug,
        surveyed_at=surveyed_at,
        source=source,
        schemas=_rows("database_schemas"),
        tables=_rows("database_tables"),
        columns=_rows("database_columns"),
        profiles=_rows("database_column_profiles"),
        activity=_rows("database_table_activity"),
    )


def _table_key(row: dict) -> tuple[str, str]:
    return (row.get("schema_name") or "", row.get("table_name") or "")


def _columns_by_table(columns: list[dict]) -> dict[tuple[str, str], list[dict]]:
    out: dict[tuple[str, str], list[dict]] = {}
    for col in columns:
        out.setdefault(_table_key(col), []).append(col)
    return out


def _is_base_table(row: dict) -> bool:
    """Views and materialised views are excluded from the structural checks.

    A view has no grain of its own to propose, no primary key to be missing,
    and counting it as a "table with no PK" would produce a conventions
    finding nobody can act on. `table_type` comes straight from
    `information_schema.tables`, so the values are Postgres's own.
    """
    ttype = (row.get("table_type") or "").upper().replace(" ", "_")
    if not ttype:
        # Not recorded — treat as a base table rather than dropping it
        # silently. An unrecorded type is not evidence of a view.
        return True
    return ttype in {"BASE_TABLE", "TABLE", "LOCAL_TEMPORARY", "FOREIGN"}


def _foreign_key_edges(columns: list[dict]) -> list[dict]:
    """FK edges from the stored `foreign_key_json` column.

    Shape per `connection.py`'s `fk_lookup`: `{"foreign_schema",
    "foreign_table", "foreign_column"}`. `registry._decode_detail_row` has
    already turned the stored TEXT back into a dict.
    """
    edges: list[dict] = []
    for col in columns:
        fk = col.get("foreign_key_json")
        if not isinstance(fk, dict):
            continue
        target_table = fk.get("foreign_table") or ""
        if not target_table:
            continue
        edges.append({
            "from_schema": col.get("schema_name") or "",
            "from_table": col.get("table_name") or "",
            "from_column": col.get("column_name") or "",
            "to_schema": fk.get("foreign_schema") or col.get("schema_name") or "",
            "to_table": target_table,
            "to_column": fk.get("foreign_column") or "",
        })
    return edges


def _distinct_estimate(profile: dict, row_count: int | None) -> float | None:
    """Resolve `pg_stats.n_distinct`'s two-sign convention to a row count.

    Postgres stores a NEGATIVE `n_distinct` to mean "this fraction of the row
    count", with −1 meaning every value is distinct. Slice 7 stores the raw
    figure (`"distinct_count": stat.get("n_distinct")`), so a consumer that
    reads it as a plain count sees −1 distinct values in a unique column and
    concludes the opposite of the truth.

    Returns None when the value is absent, or when it is a negative fraction
    and no row count is available to resolve it against — an unresolvable
    estimate is not a measurement.
    """
    raw = profile.get("distinct_count")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value >= 0:
        return value
    if not row_count:
        return None
    return abs(value) * float(row_count)


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    # Compare naive to naive: the stored timestamps are a mix (utcnow()
    # isoformat has no offset; a native read-back may carry one), and
    # subtracting across that boundary raises.
    return parsed.replace(tzinfo=None)


def _ratio(numerator: float, denominator: float) -> float:
    return (numerator / denominator) if denominator else 0.0


def _ramp(value: float, full_at: float) -> float:
    """Linear 0→1 ramp, clamped. Used so a threshold is a slope, not a cliff."""
    if full_at <= 0:
        return 0.0
    return max(0.0, min(1.0, value / full_at))


# ═══════════════════════════════════════════════════════════════════════════
# 1. db_classification  (design §5.3)
# ═══════════════════════════════════════════════════════════════════════════

def _structure_evidence(inputs: DerivedInputs) -> dict[str, float] | None:
    """Keys, FK density, widths and row-count skew → evidence per kind.

    Returns None when there is nothing structural to read, so the family is
    excluded from the confidence denominator rather than scoring zeros.
    """
    tables = [t for t in inputs.tables if _is_base_table(t)]
    if not tables:
        return None

    by_table = _columns_by_table(inputs.columns)
    keys_captured = inputs.keys_were_captured

    table_count = len(tables)
    # Slice 21b: `with_pk`'s denominator must be the tables whose OWN keys
    # were captured, not every table — see `DerivedInputs.
    # keys_captured_for_table`'s own docstring for the mixed-access case
    # this fixes (a database that is mostly catalog-fallback with one
    # live-surveyed table used to have `keys_captured` (the global flag)
    # read as True, but `table_count` below still counted every
    # catalog-fallback table as a denominator entry that could never
    # contribute a `with_pk`, silently inflating `no_pk_share` — evidence
    # FOR "staging" (KIND_STAGING) built from tables that were simply never
    # checked, not from tables genuinely observed to lack a key).
    measured_tables = [t for t in tables if inputs.keys_captured_for_table(_table_key(t))]
    measured_count = len(measured_tables)
    with_pk = 0
    widths: list[int] = []
    row_counts: list[int] = []
    small_tables = 0

    for table in measured_tables:
        key = _table_key(table)
        cols = by_table.get(key, [])
        if any(c.get("is_primary_key") for c in cols):
            with_pk += 1
    for table in tables:
        key = _table_key(table)
        cols = by_table.get(key, [])
        width = table.get("column_count")
        if width is None:
            width = len(cols) or None
        if width:
            widths.append(int(width))
        rows = table.get("row_count")
        if rows is not None:
            row_counts.append(int(rows))
            if int(rows) <= _SMALL_TABLE_ROWS:
                small_tables += 1

    edges = _foreign_key_edges(inputs.columns) if keys_captured else []
    fk_density = _ratio(len(edges), measured_count) if measured_count else 0.0
    avg_width = (sum(widths) / len(widths)) if widths else 0.0
    wide = _ramp(avg_width - _WIDE_TABLE_COLUMNS, _WIDE_TABLE_COLUMNS)
    small_share = _ratio(small_tables, len(row_counts)) if row_counts else 0.0

    # Row-count skew: one or two enormous tables beside many small ones is
    # the analytical/warehouse shape. Share of all rows held by the largest
    # table, only meaningful with several tables and a real total.
    skew = 0.0
    total_rows = sum(row_counts)
    if len(row_counts) >= 3 and total_rows > 0:
        skew = _ratio(max(row_counts), total_rows)

    if measured_count:
        pk_coverage = _ratio(with_pk, measured_count)
        no_pk_share = 1.0 - pk_coverage
    else:
        # No table's keys were captured (the whole-database case
        # `keys_were_captured` guards, now checked per-table via
        # `measured_count`). Neither "has keys" nor "has no keys" is
        # evidence here, so both the PK and FK signals contribute nothing —
        # but the rest of the family (widths, row skew, small-table share) is
        # still real, so the family stays in.
        pk_coverage = 0.0
        no_pk_share = 0.0

    evidence = {
        KIND_TRANSACTIONAL: min(
            1.0, 0.5 * pk_coverage + 0.5 * _ramp(fk_density, 1.0),
        ),
        KIND_ANALYTICAL: min(1.0, 0.55 * wide + 0.45 * skew),
        KIND_REFERENCE: min(1.0, 0.7 * small_share + 0.3 * pk_coverage),
        KIND_STAGING: min(1.0, no_pk_share),
        # Structure alone cannot tell a copy from its original — that is
        # exactly what the fingerprint family is for.
        KIND_COPY: 0.0,
    }
    return evidence


def _naming_evidence(inputs: DerivedInputs) -> dict[str, float] | None:
    tables = [t for t in inputs.tables if _is_base_table(t)]
    if not tables:
        return None

    counts = {kind: 0 for kind in _NAME_PATTERNS}
    for table in tables:
        name = (table.get("table_name") or "").lower()
        for kind, patterns in _NAME_PATTERNS.items():
            if any(re.search(p, name) for p in patterns):
                counts[kind] += 1

    total = len(tables)
    # A naming convention does not need to be universal to be evidence — a
    # quarter of tables prefixed `stg_` is a strong signal — so the share is
    # ramped to full at a third of tables rather than at all of them.
    evidence = {
        kind: _ramp(_ratio(count, total), 0.33)
        for kind, count in counts.items()
    }
    # Ordinary business names support "transactional" by elimination: this is
    # the one kind with no distinctive naming of its own.
    evidence[KIND_TRANSACTIONAL] = max(0.0, 1.0 - max(evidence.values(), default=0.0))
    return evidence


def _activity_evidence(inputs: DerivedInputs) -> dict[str, float] | None:
    """Read/write mix from the stored tuple counters.

    Returns None when no row carries a counter — the case the brief singles
    out. An ANALYZE that never ran, or a table the statistics collector has
    no row for, leaves these NULL, and reading NULL as 0 would make a
    never-measured database look exactly like a completely idle one.
    """
    if not inputs.activity:
        return None

    ins = upd = dele = seq = idx = 0
    measured_rows = 0
    for row in inputs.activity:
        values = [row.get("rows_inserted"), row.get("rows_updated"),
                  row.get("rows_deleted"), row.get("seq_scan"), row.get("idx_scan")]
        if all(v is None for v in values):
            continue
        measured_rows += 1
        ins += int(row.get("rows_inserted") or 0)
        upd += int(row.get("rows_updated") or 0)
        dele += int(row.get("rows_deleted") or 0)
        seq += int(row.get("seq_scan") or 0)
        idx += int(row.get("idx_scan") or 0)

    if not measured_rows:
        return None

    writes = ins + upd + dele
    reads = seq + idx
    if writes == 0 and reads == 0:
        # Genuinely measured, and genuinely idle since the last stats reset.
        # That is a real state, but it does not distinguish the five kinds,
        # so the family has no evidence to offer and is excluded. Recorded
        # in the classification's own explanation via `signals_used`.
        return None

    mutate_share = _ratio(upd + dele, writes)
    insert_share = _ratio(ins, writes)
    seq_share = _ratio(seq, reads)
    read_only = 1.0 if writes == 0 and reads > 0 else 0.0
    churn = min(_ramp(_ratio(ins, writes), 0.5), _ramp(_ratio(dele, writes), 0.3))

    evidence = {
        # OLTP rewrites and deletes rows in place, and reads by index.
        KIND_TRANSACTIONAL: min(1.0, 0.6 * mutate_share + 0.4 * (1.0 - seq_share)),
        # Warehouses append and scan.
        KIND_ANALYTICAL: min(1.0, 0.5 * insert_share + 0.5 * seq_share),
        KIND_REFERENCE: read_only,
        # Load-then-truncate churn: lots of inserts AND lots of deletes.
        KIND_STAGING: churn,
        KIND_COPY: 0.0,
    }
    return evidence


def _fingerprint_evidence(fingerprint: dict) -> dict[str, float] | None:
    """Copy evidence from the fingerprint check's own best match.

    `best_similarity is None` has TWO different causes, found conflated
    live (`adventureworks`, 2026-09-27): `fingerprint_database()` measured
    against real comparable databases and genuinely found no match above
    the reportable floor (a real, negative finding — "no known database
    resembles this one," that function's own words) sets `best_similarity`
    to `None` in EXACTLY the same way as never having had a comparable
    database to check against at all. Only the second case is "missing
    data" (`None` here, sorted into `classify_database`'s own `missing`
    list, reported as "No data for: fingerprint"); the first is a real,
    zero-strength "copy" measurement — this family DID contribute, it
    contributed "not a copy of anything on hand."
    """
    if fingerprint.get("state") != STATE_MEASURED:
        return None
    if not fingerprint.get("comparable_databases"):
        return None
    evidence = {kind: 0.0 for kind in _ALL_KINDS}
    best = fingerprint.get("best_similarity")
    if best is not None:
        # Ramp from the reportable floor to the copy threshold, so a 0.3
        # overlap is nearly no evidence and a 0.95 match is full evidence.
        span = _COPY_JACCARD - _REPORTABLE_JACCARD
        evidence[KIND_COPY] = _ramp(float(best) - _REPORTABLE_JACCARD, span)
    return evidence


def classify_database(inputs: DerivedInputs, fingerprint: dict) -> dict:
    """Derive what kind of database this is, with a confidence that reflects
    how much signal was available (design §5.3, §5.1's `not_established`).
    """
    families = {
        "structure": _structure_evidence(inputs),
        "naming": _naming_evidence(inputs),
        "activity": _activity_evidence(inputs),
        "fingerprint": _fingerprint_evidence(fingerprint),
    }
    available = {name: ev for name, ev in families.items() if ev is not None}
    missing = sorted(name for name, ev in families.items() if ev is None)

    if not available:
        return {
            "state": STATE_NOT_MEASURED,
            "kind": None,
            "confidence": 0,
            "scores": {},
            "signals_used": [],
            "signals_missing": missing,
            "explanation": (
                "No stored rows carry a classification signal for this "
                "database: no table/column catalog, no tuple counters, and "
                "no comparable database in the registry. This is insufficient "
                "signal, not a finding that the database is of no particular "
                "kind — run a schema survey (and ANALYZE, for the activity "
                "signal) first."
            ),
        }

    available_weight = sum(_CLASSIFICATION_SIGNAL_WEIGHTS[name] for name in available)
    scores: dict[str, float] = {}
    for kind in _ALL_KINDS:
        total = sum(
            _CLASSIFICATION_SIGNAL_WEIGHTS[name] * ev.get(kind, 0.0)
            for name, ev in available.items()
        )
        scores[kind] = round(total / available_weight, 4)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_kind, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0

    coverage = available_weight / _TOTAL_CLASSIFICATION_WEIGHT
    margin = _ratio(best_score - second_score, best_score)
    # Three factors, all of them things a reader can dispute separately: how
    # much of the signal was available, how clearly the winner won, and how
    # strong the winning evidence was in absolute terms.
    confidence = round(
        _MAX_INFERRED_CONFIDENCE * coverage * (0.35 + 0.65 * margin)
        * min(1.0, best_score * 2)
    )
    confidence = max(0, min(_MAX_INFERRED_CONFIDENCE, confidence))

    undecided = best_score <= 0 or confidence < _MIN_CLASSIFICATION_CONFIDENCE
    explanation_parts = [
        (f"Derived from {len(available)} of {len(families)} signal families "
         f"({', '.join(sorted(available))})."),
    ]
    if missing:
        explanation_parts.append(
            f"No data for: {', '.join(missing)} — these contributed nothing "
            f"rather than counting as zero, which is why the confidence is "
            f"scaled to {int(coverage * 100)}% coverage."
        )
    if "activity" in missing:
        explanation_parts.append(
            "In particular there are no usable tuple counters, so the "
            "read/write mix is unknown. That is insufficient signal, NOT "
            "evidence of a staging or idle database."
        )
    if undecided:
        explanation_parts.append(
            f"The top two candidates ({ranked[0][0]} {best_score:.2f}, "
            f"{ranked[1][0] if len(ranked) > 1 else '-'} {second_score:.2f}) "
            f"are too close to call at this coverage."
        )

    return {
        "state": STATE_MEASURED,
        "kind": None if undecided else best_kind,
        "confidence": 0 if undecided else confidence,
        "scores": scores,
        "ranked": [k for k, _ in ranked],
        "signals_used": sorted(available),
        "signals_missing": missing,
        "coverage": round(coverage, 3),
        "explanation": " ".join(explanation_parts),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 2. db_relationship_graph  (design §5.3)
# ═══════════════════════════════════════════════════════════════════════════

def derive_relationship_graph(inputs: DerivedInputs) -> dict:
    """Is there a data model here, or a bag of tables?

    The absence case that matters: a survey that never captured key
    information (every `is_primary_key` NULL — the native read-back path)
    must NOT report "no foreign keys". A survey that DID capture keys and
    found none is reporting a real, positive finding.
    """
    tables = [t for t in inputs.tables if _is_base_table(t)]
    if not tables:
        return {
            "state": STATE_NOT_MEASURED,
            "reason": "no_schema_rows",
            "explanation": (
                "No stored table rows for this database, so there is no graph "
                "to derive. Not a finding that the tables do not relate."
            ),
        }

    if not inputs.keys_were_captured:
        return {
            "state": STATE_NOT_MEASURED,
            "reason": "keys_not_captured",
            "table_count": len(tables),
            "explanation": (
                f"{len(tables)} tables are stored, but no column row carries "
                "key information (every `is_primary_key` is NULL), which is "
                "how a native Egeria survey read-back stores columns — it "
                "does not report keys. So whether these tables relate is "
                "NOT established. This is emphatically not a finding that "
                "the database has no foreign keys; run a local schema "
                "survey, which captures constraints, to answer it."
            ),
        }

    # Slice 21b: a MIXED database — some tables key-captured, some not (a
    # partial credential, or a schema only reachable via the catalog-only
    # fallback) — used to fall straight through here once ANY column
    # anywhere had been key-captured, then treat every uncaptured table's
    # lack of adjacency as a verified "isolated" finding, identically to a
    # table genuinely checked and found to have no FK. Uncaptured tables
    # are excluded from the graph itself and reported separately
    # (`unmeasured_table_count`) — never silently counted as isolated.
    measured_tables = [t for t in tables if inputs.keys_captured_for_table(_table_key(t))]
    unmeasured_tables = [t for t in tables if t not in measured_tables]
    if not measured_tables:
        return {
            "state": STATE_NOT_MEASURED,
            "reason": "keys_not_captured",
            "table_count": len(tables),
            "explanation": (
                f"{len(tables)} tables are stored, but none of their own "
                "column rows carry key information — so whether these "
                "tables relate is NOT established."
            ),
        }

    edges = _foreign_key_edges(inputs.columns)
    names = {_table_key(t) for t in measured_tables}
    # Adjacency over table identity, ignoring direction for component
    # counting (a data model is connected whichever way you walk it).
    adjacency: dict[tuple[str, str], set[tuple[str, str]]] = {n: set() for n in names}
    in_degree: dict[tuple[str, str], int] = {n: 0 for n in names}
    dangling: list[dict] = []
    for edge in edges:
        src = (edge["from_schema"], edge["from_table"])
        dst = (edge["to_schema"], edge["to_table"])
        if src not in adjacency:
            continue
        if dst not in adjacency:
            # References a table this snapshot does not contain — a
            # cross-schema target the survey did not cover, not a broken FK.
            dangling.append(edge)
            continue
        adjacency[src].add(dst)
        adjacency[dst].add(src)
        in_degree[dst] += 1

    # Connected components by breadth-first walk.
    seen: set[tuple[str, str]] = set()
    components: list[list[tuple[str, str]]] = []
    for node in names:
        if node in seen:
            continue
        stack = [node]
        component: list[tuple[str, str]] = []
        seen.add(node)
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbour in adjacency[current]:
                if neighbour not in seen:
                    seen.add(neighbour)
                    stack.append(neighbour)
        components.append(component)

    isolated = sorted(
        f"{s}.{t}" for (s, t) in names if not adjacency[(s, t)]
    )
    connected_tables = len(names) - len(isolated)
    largest = max((len(c) for c in components), default=0)

    unmeasured_note = (
        f" ({len(unmeasured_tables)} other table(s) excluded — their own keys "
        "were not captured, so their relationships are not established.)"
        if unmeasured_tables else ""
    )

    if not edges:
        verdict = "bag_of_tables"
        explanation = (
            f"Measured: all {len(measured_tables)} key-captured tables were "
            "checked and not one declares a foreign key. This is a real "
            "finding — the measured part of the database is a bag of tables "
            f"with no enforced relational model — not a gap in what was "
            f"surveyed.{unmeasured_note}"
        )
    elif _ratio(connected_tables, len(names)) >= 0.7:
        verdict = "data_model"
        explanation = (
            f"{len(edges)} foreign keys connect {connected_tables} of "
            f"{len(names)} key-captured tables into {len(components)} "
            f"component(s), the largest holding {largest}. A real "
            f"relational model.{unmeasured_note}"
        )
    else:
        verdict = "partial_model"
        explanation = (
            f"{len(edges)} foreign keys connect only {connected_tables} of "
            f"{len(names)} key-captured tables; {len(isolated)} stand alone. "
            f"A partial model — some of the schema is related, much of it is "
            f"not.{unmeasured_note}"
        )

    hubs = sorted(
        ({"table": f"{s}.{t}", "referenced_by": d} for (s, t), d in in_degree.items() if d),
        key=lambda h: h["referenced_by"], reverse=True,
    )[:10]

    return {
        "state": STATE_MEASURED,
        "verdict": verdict,
        "table_count": len(measured_tables),
        "unmeasured_table_count": len(unmeasured_tables),
        "edge_count": len(edges),
        "component_count": len(components),
        "largest_component": largest,
        "connected_tables": connected_tables,
        "isolated_tables": isolated,
        "dangling_references": dangling,
        "most_referenced": hubs,
        "edges": edges,
        "explanation": explanation,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 3. grain_determination  (design §5.3, bold in the design doc)
# ═══════════════════════════════════════════════════════════════════════════

#: Confidence per basis. A primary key IS the grain — declared by the schema,
#: not inferred — so it is high but still short of a measurement, because the
#: STATEMENT (what one row means in business terms) remains an inference from
#: column names. A distinct-count match is weaker again: `n_distinct` is an
#: ANALYZE-time estimate, and uniqueness today is not a declared constraint.
_GRAIN_CONFIDENCE = {
    "primary_key": 90,
    "unique_column_exact": 65,
    "unique_column_estimated": 50,
}
#: distinct/row ratio at or above which a column is treated as a candidate
#: key. Not 1.0: `n_distinct` is an estimate from a sample, so an exactly-
#: unique column routinely reports slightly under.
_UNIQUE_RATIO_EXACT = 0.99
_UNIQUE_RATIO_LIKELY = 0.95

# ── time grain from naming (design §16.2/§16.3, added 2026-09-24) ──────────
#
# §16.3's `grain_determination` row: *"exists, extended with time grain from
# naming, partitions and PK date columns"*. The PK-date half already existed
# (`_grain_from_pk` sets `interval` when a date column sits IN the key, and
# §16.2's "entity grain from keys" row is `_grain_from_pk` itself) — what is
# added here is the two naming halves, and an explicit BASIS and CONFIDENCE for
# whichever signal the interval came from.
#
# The basis matters more than the interval. "monthly" derived from a PK date
# column is a near-declaration; "monthly" derived from a table called
# `sales_202503` is a guess about a naming habit. Before this, both rendered as
# the same bare `interval: "monthly"` string with nothing to tell them apart —
# and §16.2 grades them differently on purpose ("medium for partitions and file
# names, low for column names").

#: Interval names this module emits, weakest-period-last. Shared with
#: `preliminary_fit`'s compatibility test, which needs them ordered: a table
#: recorded per hour can serve a monthly requirement, not the reverse.
INTERVAL_RANK: dict[str, int] = {
    "hourly": 1,
    "per-date": 2,
    "daily": 2,
    "weekly": 3,
    "monthly": 4,
    "quarterly": 5,
    "annual": 6,
}

#: Basis for an interval, and its confidence. Ordered strongest-first; the
#: first basis that fires wins, and the others are still recorded (as
#: `interval_signals`) so a reader can see a naming signal that AGREES with the
#: key — or disagrees with it, which is a finding of its own.
GRAIN_INTERVAL_BASIS_PK = "primary_key_date"
GRAIN_INTERVAL_BASIS_TABLE_NAME = "table_name"
GRAIN_INTERVAL_BASIS_PARTITION_SUFFIX = "partition_suffix"
GRAIN_INTERVAL_BASIS_COLUMN_NAME = "column_name"
GRAIN_INTERVAL_BASIS_COLUMN_NAME_UNTYPED = "column_name_untyped"

_INTERVAL_CONFIDENCE = {
    # A date column inside the declared primary key: the schema itself says
    # the grain is per-period. Not 100 — which period still comes from the
    # column's NAME.
    GRAIN_INTERVAL_BASIS_PK: 85,
    # `daily_sales`, `orders_hourly`: a deliberate name, and the period is
    # spelled out rather than inferred from a value. §16.2's "medium".
    GRAIN_INTERVAL_BASIS_TABLE_NAME: 70,
    # `events_202503`: a partition-naming habit. Medium per §16.2, and below
    # an explicit period word because the period is inferred from the shape of
    # a number.
    GRAIN_INTERVAL_BASIS_PARTITION_SUFFIX: 55,
    # A date/timestamp-TYPED column whose name carries a period word. §16.2's
    # "low for column names".
    GRAIN_INTERVAL_BASIS_COLUMN_NAME: 40,
    # A column named like a date whose TYPE is not temporal (`day integer`,
    # `period text`). The weakest signal here and deliberately still reported:
    # it is exactly the shape a bare warehouse fact table uses, and dropping
    # it would leave such a table with no time grain at all.
    GRAIN_INTERVAL_BASIS_COLUMN_NAME_UNTYPED: 25,
}

#: Period words in a TABLE name, longest/most-specific first so `semi_annual`
#: cannot be matched as `annual`. Matched against the name's tokens, not as a
#: substring: `annualised_rate` is not an annual grain, and a substring test
#: would say it was.
_TABLE_PERIOD_TOKENS: tuple[tuple[str, str], ...] = (
    ("hourly", "hourly"), ("hour", "hourly"),
    ("daily", "daily"), ("day", "daily"),
    ("weekly", "weekly"), ("week", "weekly"),
    ("monthly", "monthly"), ("month", "monthly"),
    ("quarterly", "quarterly"), ("quarter", "quarterly"),
    ("annual", "annual"), ("yearly", "annual"), ("year", "annual"),
)

#: A table name ending in a period-shaped number — Postgres's own partitioning
#: convention (`events_2025`, `events_202503`, `events_2025_03_01`) and the
#: nearest database equivalent of §16.2's `year=/month=/day=` partition keys.
#: Those literal Hive-style keys are a FILESYSTEM layout and are deliberately
#: not looked for here; see `_interval_from_partition_suffix`.
_PARTITION_SUFFIX_RE = re.compile(
    r"(?:^|[_.])(?:p|part|y)?(20\d{2})(?:[_.-]?(\d{2}))?(?:[_.-]?(\d{2}))?$"
)

#: Column-name markers for a date-bearing column, as a whole token. §16.2:
#: *"columns `*_date`, `*_ts`, `day`, `hour`, `period`"*. `at` is deliberately
#: absent as a bare token (`at` alone is never a column name worth matching);
#: `*_at` is caught by the `_at` suffix test in `_name_suggests_date`.
_DATE_NAME_TOKENS = frozenset({
    "date", "dates", "dt", "ts", "timestamp", "datetime", "time", "day",
    "hour", "period", "week", "month", "quarter", "year", "asof",
})

#: Suffixes that make a column a date by naming convention even when no token
#: matches (`ordered_at`, `load_dttm`).
_DATE_NAME_SUFFIXES = ("_at", "_on", "_dttm", "_date", "_ts", "_time", "_day")


def _tokenise_name(name: str) -> list[str]:
    """`OrderLine_2025` → `['order', 'line', '2025']`.

    Splits on non-alphanumerics AND on camelCase boundaries, because a
    database that quotes its identifiers routinely carries both conventions in
    one schema.
    """
    if not name:
        return []
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(name))
    return [t for t in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if t]


def _name_suggests_date(column_name: str) -> bool:
    """Does this column's NAME claim to carry a date, whatever its type?

    Name only, deliberately: the caller already knows the type, and the point
    of this test is the case where the two disagree (`day integer`,
    `period text`) — a real warehouse convention that a type-only test reads
    as "this table has no date column at all".
    """
    name = (column_name or "").lower()
    if not name:
        return False
    if any(name.endswith(suffix) for suffix in _DATE_NAME_SUFFIXES):
        return True
    return bool(_DATE_NAME_TOKENS & set(_tokenise_name(name)))


def _interval_from_table_name(table_name: str) -> str:
    """`daily_sales` → `daily`; `orders_hourly` → `hourly`; else `""`."""
    tokens = set(_tokenise_name(table_name))
    for token, interval in _TABLE_PERIOD_TOKENS:
        if token in tokens:
            return interval
    return ""


def _interval_from_partition_suffix(table_name: str) -> str:
    """`events_202503` → `monthly`; `events_2025` → `annual`; else `""`.

    **A judgement call about scope, stated rather than left implicit.** §16.2
    names *"partition keys `year=/month=/day=`"* as the free partition signal.
    That exact form is a Hive/Parquet directory layout — it is a
    FILESYSTEM/dataset signal and belongs with Phase 2's
    `filesystem_structure`, not with a database's stored table rows, which
    never carry it. The database equivalent, and what this looks for, is
    Postgres's own child-partition naming habit: a date-shaped suffix on the
    table name.

    It is a NAMING signal, not the partition metadata itself. Real partition
    bounds (`pg_partitioned_table`, the child tables' `CHECK` constraints)
    would be exact — and nothing in RE collects them today, which
    `coverage_signals` reports as a collection gap rather than as an absence
    of partitioning.
    """
    match = _PARTITION_SUFFIX_RE.search((table_name or "").lower())
    if not match:
        return ""
    _year, month, day = match.groups()
    if day:
        return "daily"
    if month:
        return "monthly"
    return "annual"


def _time_grain(
    table_name: str,
    pk_interval: str,
    typed_date_columns: list[str],
    named_date_columns: list[str],
) -> dict:
    """The interval, its basis, its confidence, and every signal that fired.

    `pk_interval` is what `_grain_from_pk` already derived (empty when no date
    column sits in the key). The strongest basis present wins; the others are
    kept in `interval_signals` so a disagreement is visible instead of being
    silently outranked.
    """
    signals: dict[str, str] = {}
    if pk_interval:
        signals[GRAIN_INTERVAL_BASIS_PK] = pk_interval
    from_table = _interval_from_table_name(table_name)
    if from_table:
        signals[GRAIN_INTERVAL_BASIS_TABLE_NAME] = from_table
    from_partition = _interval_from_partition_suffix(table_name)
    if from_partition:
        signals[GRAIN_INTERVAL_BASIS_PARTITION_SUFFIX] = from_partition
    if typed_date_columns:
        from_typed = _interval_from_columns(typed_date_columns)
        if from_typed:
            signals[GRAIN_INTERVAL_BASIS_COLUMN_NAME] = from_typed
    untyped = [c for c in named_date_columns if c not in set(typed_date_columns)]
    if untyped:
        from_untyped = _interval_from_columns(untyped)
        if from_untyped:
            signals[GRAIN_INTERVAL_BASIS_COLUMN_NAME_UNTYPED] = from_untyped

    for basis in (GRAIN_INTERVAL_BASIS_PK, GRAIN_INTERVAL_BASIS_TABLE_NAME,
                  GRAIN_INTERVAL_BASIS_PARTITION_SUFFIX,
                  GRAIN_INTERVAL_BASIS_COLUMN_NAME,
                  GRAIN_INTERVAL_BASIS_COLUMN_NAME_UNTYPED):
        if basis in signals:
            interval = signals[basis]
            disagreeing = sorted(
                f"{other}={value}" for other, value in signals.items()
                if other != basis and value != interval
            )
            return {
                "interval": interval,
                "interval_basis": basis,
                "interval_confidence": _INTERVAL_CONFIDENCE[basis],
                "interval_signals": signals,
                "interval_explanation": (
                    f"Time grain {interval!r}, from {basis} "
                    f"(confidence {_INTERVAL_CONFIDENCE[basis]}: a naming or "
                    f"key signal, never a measured cadence — that is "
                    f"`coverage_profile`, an Analysis-tier read)."
                    + (f" Other signals disagree: {', '.join(disagreeing)}."
                       if disagreeing else "")
                ),
            }

    return {
        "interval": "",
        "interval_basis": "",
        "interval_confidence": 0,
        "interval_signals": {},
        # NOT "this table has no time grain": nothing in its name, its key or
        # its column names names a period, which is a statement about the
        # available signal.
        "interval_explanation": (
            "No time grain is derivable from names: no date column sits in the "
            "key, the table name carries no period word or date-shaped suffix, "
            "and no column is named like a date. That is an absence of naming "
            "signal, NOT a finding that the data is not periodic — a measured "
            "cadence needs `coverage_profile` (Analysis tier)."
        ),
    }


def determine_grain(inputs: DerivedInputs) -> dict:
    """One row per what, per table (design §5.3).

    Produces, per table, exactly one of:
      - a grain with a basis and a confidence (a proposal — see the module
        docstring on `contentStatus: DRAFT`);
      - `no_candidate_key`: measured, and nothing identifies a row. A real
        finding about the modelling, per design §5.6's "grain clarity".
      - `insufficient_signal`: no keys captured and no stored profile to fall
        back on. Not a finding at all.
    """
    tables = [t for t in inputs.tables if _is_base_table(t)]
    if not tables:
        return {"state": STATE_NOT_MEASURED, "reason": "no_schema_rows", "grains": []}

    cols_by_table = _columns_by_table(inputs.columns)
    profiles_by_table: dict[tuple[str, str], list[dict]] = {}
    for profile in inputs.profiles:
        profiles_by_table.setdefault(_table_key(profile), []).append(profile)

    keys_captured = inputs.keys_were_captured
    grains: list[dict] = []

    for table in tables:
        key = _table_key(table)
        schema_name, table_name = key
        cols = cols_by_table.get(key, [])
        row_count = table.get("row_count")
        profiles = profiles_by_table.get(key, [])

        date_columns = [
            c.get("column_name") for c in cols
            if any(m in ((c.get("base_type") or c.get("data_type") or "").lower())
                   for m in _DATE_TYPE_MARKERS)
        ]
        date_columns = [d for d in date_columns if d]

        # §16.2's "time grain from naming" — columns named like a date
        # whatever their type. A superset of `date_columns` for a well-typed
        # schema and the ONLY signal in a warehouse that stores `day` as an
        # integer, which is why it is collected separately rather than folded
        # into the typed list (a consumer reading `date_columns` is reading a
        # list of temporal-TYPED columns and must keep doing so).
        named_date_columns = [
            c.get("column_name") for c in cols
            if c.get("column_name") and _name_suggests_date(c.get("column_name"))
        ]

        pk_columns = [
            c.get("column_name") for c in cols if c.get("is_primary_key")
        ] if keys_captured else []
        pk_columns = [p for p in pk_columns if p]

        entry: dict = {
            "schema_name": schema_name,
            "table_name": table_name,
            "qualified_name": f"{schema_name}.{table_name}",
            "row_count": row_count,
            "date_columns": date_columns,
            "named_date_columns": named_date_columns,
        }

        if pk_columns:
            entry.update(_grain_from_pk(pk_columns, date_columns))
            _apply_time_grain(entry, table_name, date_columns, named_date_columns)
            grains.append(entry)
            continue

        # No primary key (or keys were never captured). Fall back to the
        # stored profile: a column whose distinct count matches the row count
        # is a candidate key.
        candidate = _grain_from_profiles(profiles, row_count)
        if candidate:
            entry.update(candidate)
            _apply_time_grain(entry, table_name, date_columns, named_date_columns)
            grains.append(entry)
            continue

        if not profiles:
            entry.update({
                "state": STATE_NOT_MEASURED,
                "basis": None,
                "grain_statement": "",
                "confidence": 0,
                "label": "unverified",
                "explanation": (
                    ("No key information was captured for this table, and no "
                     if not keys_captured else
                     "This table declares no primary key, and no ")
                    + "stored column profile exists to look for a candidate key "
                      "in. The grain is NOT established — run the statistics "
                      "step (and ANALYZE, so pg_stats is populated) rather than "
                      "reading this as a table without a grain."
                ),
            })
            _apply_time_grain(entry, table_name, date_columns, named_date_columns)
            grains.append(entry)
            continue

        # Profiles exist and nothing in them is unique: a real finding.
        entry.update({
            "state": STATE_MEASURED,
            "basis": None,
            "grain_statement": "",
            "confidence": 0,
            "label": "gap",
            "explanation": (
                f"Measured: this table declares no primary key and none of its "
                f"{len(profiles)} profiled columns has a distinct count near "
                f"its row count. No column or simple column pair identifies a "
                f"row, so the grain is genuinely undetermined in the data — a "
                f"modelling gap (design §5.6, 'grain clarity'), not missing "
                f"evidence."
            ),
        })
        _apply_time_grain(entry, table_name, date_columns, named_date_columns)
        grains.append(entry)

    # Slice 21b follow-up (2026-09-27, live gate): the "numbers behind this"
    # evidence panel's `measureHtml()` array branch (app.js) renders a list
    # row's "name"/"summary" fields when present, falling back to
    # `check_name`/`name`/`id` and `summary`/`detail`/`label` — none of
    # which a grain entry has, so 58 of them rendered as a bare, repeated
    # `label` value ("gap gap gap gap gap gap and 52 more") with no way to
    # tell WHICH table each one was about. `name` is the qualified table
    # name every entry already computes; `summary` is the entry's own
    # explanation of ITS grain (or lack of one) — the same sentence
    # `grain_statement`/`explanation` already carries, just under the key
    # name the generic renderer looks for.
    for g in grains:
        g["name"] = g.get("qualified_name") or ""
        g["summary"] = g.get("grain_statement") or g.get("explanation") or g.get("label") or ""

    determined = [g for g in grains if g.get("grain_statement")]
    timed = [g for g in grains if g.get("interval")]
    bases: dict[str, int] = {}
    for g in timed:
        basis = g.get("interval_basis") or ""
        bases[basis] = bases.get(basis, 0) + 1
    # Slice 21a follow-up (owner's gate, 2026-09-27): a whole-database
    # boolean read as a flat contradiction beside the per-schema container
    # headline's own "keys_not_captured" lines for individual schools — "keys
    # were captured: yes" next to "coco_ods: not established
    # (keys_not_captured)" looked like two disagreeing claims about the same
    # fact, when they were actually answering different questions (ANY
    # table vs THIS table). A count, not a boolean, states what was actually
    # true without needing the reader to reconcile scope themselves.
    keys_captured_count = sum(1 for t in tables if inputs.keys_captured_for_table(_table_key(t)))
    return {
        "state": STATE_MEASURED,
        "grains": grains,
        "table_count": len(tables),
        "determined_count": len(determined),
        "undetermined_count": len(grains) - len(determined),
        "keys_captured_count": keys_captured_count,
        # §16.3's time-grain extension, rolled up so `preliminary_fit` and the
        # results card can read it without walking every table. `timed_count`
        # is a count of tables with a NAMING-derived interval; a table missing
        # from it has no period word anywhere, which is not the same as data
        # that is not periodic.
        "timed_count": len(timed),
        "interval_bases": bases,
        "intervals": sorted({g["interval"] for g in timed}),
    }


def _apply_time_grain(
    entry: dict,
    table_name: str,
    date_columns: list[str],
    named_date_columns: list[str],
) -> None:
    """Attach §16.3's time-grain fields to one table's grain entry, in place.

    `entry["interval"]` may already be set by `_grain_from_pk` (a date column
    IN the key). That is the strongest basis and is preserved — this records
    WHICH basis it was and what else agreed, which is the part that did not
    exist before.
    """
    time_grain = _time_grain(
        table_name, entry.get("interval") or "", date_columns, named_date_columns,
    )
    entry.update(time_grain)


def _grain_from_pk(pk_columns: list[str], date_columns: list[str]) -> dict:
    statement = "one row per " + " + ".join(pk_columns)
    interval = ""
    # A PK that itself contains a date column is a per-period grain, which is
    # the distinction `interval` exists to carry.
    pk_dates = [c for c in pk_columns if c in date_columns]
    if pk_dates:
        interval = _interval_from_columns(pk_dates)
    return {
        "state": STATE_MEASURED,
        "basis": "primary_key",
        "grain_statement": statement,
        "grain_columns": pk_columns,
        "interval": interval,
        "confidence": _GRAIN_CONFIDENCE["primary_key"],
        "label": "pass",
        "explanation": (
            f"The declared primary key ({', '.join(pk_columns)}) is the grain: "
            f"{statement}. Basis: the schema's own constraint, read from "
            f"stored column rows."
        ),
    }


def _grain_from_profiles(profiles: list[dict], row_count: int | None) -> dict | None:
    if not profiles or not row_count:
        return None
    best: tuple[float, dict] | None = None
    for profile in profiles:
        distinct = _distinct_estimate(profile, row_count)
        if distinct is None:
            continue
        ratio = _ratio(distinct, float(row_count))
        if ratio < _UNIQUE_RATIO_LIKELY:
            continue
        if best is None or ratio > best[0]:
            best = (ratio, profile)
    if best is None:
        return None
    ratio, profile = best
    column = profile.get("column_name") or ""
    exact = ratio >= _UNIQUE_RATIO_EXACT
    basis = "unique_column_exact" if exact else "unique_column_estimated"
    return {
        "state": STATE_MEASURED,
        "basis": basis,
        "grain_statement": f"one row per {column}",
        "grain_columns": [column],
        "interval": "",
        "confidence": _GRAIN_CONFIDENCE[basis],
        "label": "pass",
        "explanation": (
            f"No primary key is declared, but {column!r} has an estimated "
            f"{ratio:.3f} distinct values per row (pg_stats n_distinct against "
            f"the stored row count), so it behaves as a key. This is an "
            f"ANALYZE-time estimate and an observation about today's data, not "
            f"a declared constraint — hence the reduced confidence."
        ),
    }


def _interval_from_columns(date_columns: list[str]) -> str:
    """Name the period a date column in the key implies, from its name only.

    Deliberately shallow: the stored rows carry the column's type and name,
    not its values' spacing, so "daily" here means "the key includes a date,
    so the grain is per-date" — not a measured cadence. Anything stronger
    would need the values, which is a fetch.
    """
    joined = " ".join(c.lower() for c in date_columns)
    for marker, interval in (
        ("month", "monthly"), ("week", "weekly"), ("year", "annual"),
        ("quarter", "quarterly"), ("hour", "hourly"),
        # Added 2026-09-24 with §16.3's time-grain extension: `day` is one of
        # the column names §16.2 names explicitly, and without it a column
        # literally called `day` fell through to the generic `per-date`. Last
        # in the list so a `day` inside a longer period word cannot pre-empt
        # it, and checked after `month`/`week` for the same reason.
        ("day", "daily"),
    ):
        if marker in joined:
            return interval
    return "per-date"


# ═══════════════════════════════════════════════════════════════════════════
# 4. db_fingerprint  (design §3, §5.3)
# ═══════════════════════════════════════════════════════════════════════════

def _signature(columns: list[dict], tables: list[dict]) -> tuple[set[str], set[str]]:
    """(table signature, column signature) — the two sets similarity uses."""
    table_sig = {
        f"{t.get('schema_name') or ''}.{t.get('table_name') or ''}"
        for t in tables if _is_base_table(t)
    }
    column_sig = {
        f"{c.get('schema_name') or ''}.{c.get('table_name') or ''}."
        f"{c.get('column_name') or ''}:"
        f"{(c.get('base_type') or c.get('data_type') or '').lower()}"
        for c in columns
    }
    return table_sig - {"."}, column_sig


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return _ratio(len(a & b), len(a | b))


def fingerprint_database(registry, inputs: DerivedInputs) -> dict:
    """Does this look like a copy or subset of a database we already know?

    Compares this database's stored schema signature against every OTHER
    database in RE's registry — still zero-fetch: the peers' signatures come
    from their own stored rows, not from connecting to them.

    The absence case: a registry with no other database carrying stored
    schema rows means there was nothing to compare against. That is NOT a
    finding that this database is unique.
    """
    table_sig, column_sig = _signature(inputs.columns, inputs.tables)
    if not column_sig and not table_sig:
        return {
            "state": STATE_NOT_MEASURED,
            "reason": "no_schema_rows",
            "explanation": (
                "No stored table or column rows, so no signature could be "
                "computed for this database."
            ),
        }

    digest = hashlib.sha256(
        "\n".join(sorted(column_sig)).encode("utf-8")
    ).hexdigest()

    try:
        peers = registry.list_databases()
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("db_derived: could not list databases for fingerprint: %s", exc)
        peers = []

    matches: list[dict] = []
    comparable = 0
    skipped: list[str] = []
    for peer in peers:
        peer_slug = getattr(peer, "slug", None) or (
            peer.get("slug") if isinstance(peer, dict) else None
        )
        if not peer_slug or peer_slug == inputs.slug:
            continue
        peer_inputs = load_inputs(registry, peer_slug)
        peer_tables, peer_columns = _signature(peer_inputs.columns, peer_inputs.tables)
        if not peer_columns and not peer_tables:
            skipped.append(peer_slug)
            continue
        comparable += 1

        col_jaccard = _jaccard(column_sig, peer_columns)
        tbl_jaccard = _jaccard(table_sig, peer_tables)
        containment = _ratio(len(column_sig & peer_columns), len(column_sig))
        reverse_containment = _ratio(len(column_sig & peer_columns), len(peer_columns))

        if col_jaccard >= _COPY_JACCARD:
            verdict = "likely_copy"
        elif containment >= _SUBSET_CONTAINMENT and len(peer_columns) > len(column_sig):
            verdict = "likely_subset_of"
        elif reverse_containment >= _SUBSET_CONTAINMENT and len(column_sig) > len(peer_columns):
            verdict = "likely_superset_of"
        elif col_jaccard >= _RELATED_JACCARD:
            verdict = "shares_structure"
        elif col_jaccard >= _REPORTABLE_JACCARD:
            verdict = "incidental_overlap"
        else:
            continue

        matches.append({
            "slug": peer_slug,
            "verdict": verdict,
            "column_jaccard": round(col_jaccard, 4),
            "table_jaccard": round(tbl_jaccard, 4),
            "containment": round(containment, 4),
            "reverse_containment": round(reverse_containment, 4),
            "shared_columns": len(column_sig & peer_columns),
            "peer_columns": len(peer_columns),
        })

    matches.sort(key=lambda m: m["column_jaccard"], reverse=True)
    best = matches[0]["column_jaccard"] if matches else None

    if comparable == 0:
        explanation = (
            "A signature was computed for this database "
            f"({len(table_sig)} tables, {len(column_sig)} columns, digest "
            f"{digest[:16]}), but no OTHER database in the registry has "
            "stored schema rows to compare it against"
            + (f" ({len(skipped)} peer(s) are registered but unsurveyed)"
               if skipped else "")
            + ". Whether this is a copy is NOT established — this is not a "
              "finding that it is unique."
        )
    elif not matches:
        explanation = (
            f"Measured: compared against {comparable} other database(s) with "
            f"stored schema, none shares even {_REPORTABLE_JACCARD:.0%} of "
            f"this database's column signature. A real finding — no known "
            f"database resembles this one."
        )
    else:
        top = matches[0]
        explanation = (
            f"Closest of {comparable} comparable database(s): {top['slug']} "
            f"({top['verdict']}, column-signature Jaccard "
            f"{top['column_jaccard']:.2f}, containment "
            f"{top['containment']:.2f}). Thresholds: copy ≥ {_COPY_JACCARD}, "
            f"subset containment ≥ {_SUBSET_CONTAINMENT}, related ≥ "
            f"{_RELATED_JACCARD}."
        )

    return {
        "state": STATE_MEASURED,
        "digest": digest,
        "table_count": len(table_sig),
        "column_count": len(column_sig),
        "comparable_databases": comparable,
        "unsurveyed_peers": skipped,
        "matches": matches,
        "best_similarity": best,
        "explanation": explanation,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 5. schema_conventions  (design §5.4, folded into db_derived per §5.7)
# ═══════════════════════════════════════════════════════════════════════════

def check_conventions(inputs: DerivedInputs) -> dict:
    """The STRUCTURAL conventions checks, derivable from stored rows alone.

    Deliberately excludes unused indexes. Design §5.4's `schema_conventions`
    row lists them, but slice 7 already raises an unused-index
    `RequestForActionAnnotation` from a live `pg_stat_user_indexes` read
    (`DB-SCHEMA-AND-STATS-EXTENSION-IMPLEMENTED.md` §3), and index usage is
    not derivable from stored rows at all — no structured table carries it.
    Duplicating it here would produce two RFAs for one problem.
    """
    tables = [t for t in inputs.tables if _is_base_table(t)]
    if not tables:
        return {
            "state": STATE_NOT_MEASURED,
            "reason": "no_schema_rows",
            "checks": {},
        }

    cols_by_table = _columns_by_table(inputs.columns)
    keys_captured = inputs.keys_were_captured
    checks: dict[str, dict] = {}

    # ── no primary key ────────────────────────────────────────────────────
    if keys_captured:
        without_pk = sorted(
            f"{s}.{t}" for (s, t) in (_table_key(x) for x in tables)
            if not any(c.get("is_primary_key") for c in cols_by_table.get((s, t), []))
        )
        checks["tables_without_primary_key"] = {
            "state": STATE_MEASURED,
            "count": len(without_pk),
            "total": len(tables),
            "items": without_pk,
            "label": "gap" if without_pk else "pass",
            "explanation": (
                f"{len(without_pk)} of {len(tables)} base tables declare no "
                f"primary key."
                if without_pk else
                f"All {len(tables)} base tables declare a primary key."
            ),
        }
        without_fk = sorted(
            f"{s}.{t}" for (s, t) in (_table_key(x) for x in tables)
            if not any(
                isinstance(c.get("foreign_key_json"), dict)
                for c in cols_by_table.get((s, t), [])
            )
        )
        checks["tables_without_foreign_key"] = {
            "state": STATE_MEASURED,
            "count": len(without_fk),
            "total": len(tables),
            "items": without_fk,
            # Not automatically a gap: a reference table or a genuinely
            # standalone log table has no business declaring an FK. Reported
            # as a count for the relationship-graph verdict to interpret.
            "label": "info",
            "explanation": (
                f"{len(without_fk)} of {len(tables)} base tables declare no "
                f"foreign key. Whether that is a fault depends on the kind of "
                f"database — see db_relationship_graph's verdict."
            ),
        }
    else:
        for name in ("tables_without_primary_key", "tables_without_foreign_key"):
            checks[name] = {
                "state": STATE_NOT_MEASURED,
                "count": None,
                "total": len(tables),
                "items": [],
                "label": "unverified",
                "explanation": (
                    "No column row carries key information (every "
                    "`is_primary_key` is NULL — the native-survey read-back "
                    "path does not report keys), so this check could not run. "
                    "NOT a finding that keys are missing."
                ),
            }

    # ── comments ──────────────────────────────────────────────────────────
    #
    # `description` is '' both for "no comment" and, in principle, for a
    # column whose comment was not read. The distinction is carried at the
    # table level: if NOT ONE table or column in the whole database has a
    # description, the likelier explanation is that comments were not
    # captured than that a real database documents nothing. Reported as a
    # separate state rather than as 100% undocumented.
    undocumented_tables = sorted(
        f"{s}.{t}" for (s, t), row in ((_table_key(x), x) for x in tables)
        if not (row.get("description") or "").strip()
    )
    documented_tables = len(tables) - len(undocumented_tables)
    all_columns = [c for c in inputs.columns]
    documented_columns = sum(
        1 for c in all_columns if (c.get("description") or "").strip()
    )
    nothing_documented = (
        documented_tables == 0 and documented_columns == 0 and bool(all_columns)
    )
    checks["tables_without_comment"] = {
        "state": STATE_MEASURED if not nothing_documented else STATE_NOT_MEASURED,
        "count": len(undocumented_tables) if not nothing_documented else None,
        "total": len(tables),
        "items": undocumented_tables if not nothing_documented else [],
        "label": (
            "unverified" if nothing_documented
            else ("gap" if undocumented_tables else "pass")
        ),
        "explanation": (
            "Not one table and not one column in this database carries a "
            "description. That is more likely to mean comments were never "
            "captured by the survey that wrote these rows than that a real "
            "database documents nothing at all, so this is reported as "
            "unverified rather than as 100% undocumented."
            if nothing_documented else
            f"{len(undocumented_tables)} of {len(tables)} base tables have no "
            f"comment; {documented_columns} of {len(all_columns)} columns do."
        ),
    }
    checks["column_comment_coverage"] = {
        "state": STATE_MEASURED if not nothing_documented else STATE_NOT_MEASURED,
        "count": documented_columns if not nothing_documented else None,
        "total": len(all_columns),
        "fraction": (
            round(_ratio(documented_columns, len(all_columns)), 4)
            if all_columns and not nothing_documented else None
        ),
        "label": "unverified" if nothing_documented else "info",
        "explanation": (
            "See tables_without_comment — nothing in this database is "
            "documented, which reads as not captured."
            if nothing_documented else
            f"{documented_columns} of {len(all_columns)} stored columns carry "
            f"a description."
        ),
    }

    # ── naming convention ─────────────────────────────────────────────────
    offenders = sorted(
        f"{s}.{t}" for (s, t) in (_table_key(x) for x in tables)
        if t and not _SNAKE_CASE_RE.match(t)
    )
    column_offenders = sorted({
        f"{c.get('schema_name')}.{c.get('table_name')}.{c.get('column_name')}"
        for c in all_columns
        if c.get("column_name") and not _SNAKE_CASE_RE.match(c["column_name"])
    })
    checks["naming_convention"] = {
        "state": STATE_MEASURED,
        "count": len(offenders) + len(column_offenders),
        "total": len(tables) + len(all_columns),
        "items": offenders + column_offenders,
        "label": "gap" if (offenders or column_offenders) else "pass",
        "explanation": (
            f"{len(offenders)} table name(s) and {len(column_offenders)} "
            f"column name(s) are not lower snake_case, so they must be "
            f"double-quoted in every statement that touches them."
            if (offenders or column_offenders) else
            f"All {len(tables)} table names and {len(all_columns)} column "
            f"names are lower snake_case."
        ),
    }

    return {
        "state": STATE_MEASURED,
        "checks": checks,
        "table_count": len(tables),
        "column_count": len(all_columns),
        #: Named so a reader can tell this was a decision, not an omission.
        "excluded_checks": {
            "unused_indexes": (
                "Owned by postgres_schema_and_stats (slice 7), which reads "
                "pg_stat_user_indexes live and raises its own RFA. Index "
                "usage is not carried by any structured table, so it is not "
                "derivable here, and duplicating the RFA would double-report "
                "one problem."
            ),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# 6. db_change_rates  (design §5.4, §5.7)
# ═══════════════════════════════════════════════════════════════════════════

def _snapshot_keys(registry, slug: str) -> list[tuple[str, str]]:
    """(surveyed_at, source) for every recorded survey run, newest first.

    Read from `database_surveys` rather than by scanning the detail tables,
    because that is the table that knows a run happened at all — and it needs
    no new registry method. Runs whose detail rows are absent are filtered by
    the caller, which asks for the rows and finds none.
    """
    try:
        surveys = registry.get_database_surveys(slug)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("db_derived: could not list surveys for %s: %s", slug, exc)
        return []
    keys: list[tuple[str, str]] = []
    for survey in surveys:
        at = survey.get("surveyed_at")
        if not at:
            continue
        keys.append((at, survey.get("source") or SOURCE_LOCAL))
    # Newest first, de-duplicated while preserving order.
    seen: set[tuple[str, str]] = set()
    ordered: list[tuple[str, str]] = []
    for key in sorted(keys, key=lambda k: k[0], reverse=True):
        if key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


def derive_change_rates(registry, inputs: DerivedInputs) -> dict:
    """Rows inserted/updated/deleted per table per period, and schema churn.

    Needs two snapshots. With one — the common case for a database surveyed
    once — this renders as `insufficient_history`: explicitly NOT zero change,
    and not an error either.

    No new structured table: the per-table series design §5.4 wants for
    Understanding-tier charts is already expressible as the existing
    `database_table_activity` rows across their several `surveyed_at` values,
    which is what this function walks. The series it returns is a convenience
    for the annotation payload, not a second copy of the data.
    """
    keys = _snapshot_keys(registry, inputs.slug)
    snapshots: list[tuple[str, str, list[dict], list[dict]]] = []
    for surveyed_at, source in keys:
        activity = registry.query_detail_rows(
            "database_table_activity", inputs.slug, surveyed_at, source
        )
        if not activity:
            continue
        tables = registry.query_detail_rows(
            "database_tables", inputs.slug, surveyed_at, source
        )
        snapshots.append((surveyed_at, source, activity, tables))
        if len(snapshots) == 2:
            break

    if len(snapshots) < 2:
        return {
            "state": STATE_NOT_MEASURED,
            "reason": "insufficient_history",
            "snapshots_available": len(snapshots),
            "explanation": (
                f"Change rates need two survey snapshots to difference; "
                f"{len(snapshots)} snapshot(s) of table activity exist for "
                f"this database. This is insufficient history — NOT a finding "
                f"that nothing is changing, and not a failure. Survey the "
                f"database again and this answers itself."
            ),
            "per_table": [],
        }

    (new_at, _new_src, new_activity, new_tables) = snapshots[0]
    (old_at, _old_src, old_activity, old_tables) = snapshots[1]

    start = _parse_ts(old_at)
    end = _parse_ts(new_at)
    interval_days: float | None = None
    if start and end:
        interval_days = max(0.0, (end - start).total_seconds() / 86400.0)

    old_by_table = {_table_key(r): r for r in old_activity}
    new_by_table = {_table_key(r): r for r in new_activity}
    old_tbl = {_table_key(r): r for r in old_tables}
    new_tbl = {_table_key(r): r for r in new_tables}

    per_table: list[dict] = []
    counters = ("rows_inserted", "rows_updated", "rows_deleted")
    for key, new_row in sorted(new_by_table.items()):
        schema_name, table_name = key
        old_row = old_by_table.get(key)
        entry: dict = {
            "schema_name": schema_name,
            "table_name": table_name,
            "qualified_name": f"{schema_name}.{table_name}",
        }
        if old_row is None:
            entry.update({
                "state": STATE_MEASURED,
                "change": "new_table",
                "explanation": (
                    "This table has activity in the newer snapshot and none "
                    "in the older one — it is new since the previous survey, "
                    "so there is no rate to difference yet."
                ),
            })
            per_table.append(entry)
            continue

        # A counter reset between snapshots makes the difference meaningless.
        # registry.py's own `stats_reset` comment is explicit about this: a
        # reset, failover or restore produces a negative delta that a chart
        # would faithfully draw as "−40,000 inserts".
        old_reset = old_row.get("stats_reset")
        new_reset = new_row.get("stats_reset")
        reset_moved = bool(old_reset and new_reset and old_reset != new_reset)
        went_backwards = any(
            (new_row.get(c) is not None and old_row.get(c) is not None
             and int(new_row[c]) < int(old_row[c]))
            for c in counters
        )
        if reset_moved or went_backwards:
            entry.update({
                "state": STATE_NOT_MEASURED,
                "change": "counters_reset",
                "stats_reset_old": old_reset,
                "stats_reset_new": new_reset,
                "explanation": (
                    "The cumulative tuple counters "
                    + ("reset between the two snapshots"
                       if reset_moved else "went backwards between the two "
                       "snapshots, which means they were reset")
                    + " — a reset, failover or restore. No rate is available "
                      "for this interval; the difference would be a negative "
                      "number rendered as a real one."
                ),
            })
            per_table.append(entry)
            continue

        deltas: dict[str, int | None] = {}
        unmeasured: list[str] = []
        for counter in counters:
            new_value, old_value = new_row.get(counter), old_row.get(counter)
            if new_value is None or old_value is None:
                deltas[counter] = None
                unmeasured.append(counter)
            else:
                deltas[counter] = int(new_value) - int(old_value)

        if len(unmeasured) == len(counters):
            entry.update({
                "state": STATE_NOT_MEASURED,
                "change": "counters_not_measured",
                "explanation": (
                    "Neither snapshot carries tuple counters for this table "
                    "(they are NULL — ANALYZE may never have run, or the "
                    "statistics step did not run). No rate, and not zero "
                    "change."
                ),
            })
            per_table.append(entry)
            continue

        total = sum(v for v in deltas.values() if v is not None)
        per_day = (
            {k: (round(v / interval_days, 3) if v is not None else None)
             for k, v in deltas.items()}
            if interval_days else None
        )
        old_size = (old_tbl.get(key) or {}).get("size_bytes")
        new_size = (new_tbl.get(key) or {}).get("size_bytes")
        size_delta = (
            int(new_size) - int(old_size)
            if new_size is not None and old_size is not None else None
        )
        old_rows = (old_tbl.get(key) or {}).get("row_count")
        new_rows = (new_tbl.get(key) or {}).get("row_count")
        row_delta = (
            int(new_rows) - int(old_rows)
            if new_rows is not None and old_rows is not None else None
        )

        entry.update({
            "state": STATE_MEASURED,
            "change": "idle" if total == 0 else "active",
            "deltas": deltas,
            "per_day": per_day,
            "unmeasured_counters": unmeasured,
            "size_bytes_delta": size_delta,
            "row_count_delta": row_delta,
            "explanation": (
                f"Measured over {interval_days:.2f} day(s): "
                if interval_days is not None else "Measured: "
            ) + (
                "no inserts, updates or deletes at all between the two "
                "snapshots — genuinely idle."
                if total == 0 else
                f"{deltas.get('rows_inserted')} inserted, "
                f"{deltas.get('rows_updated')} updated, "
                f"{deltas.get('rows_deleted')} deleted."
            ) + (
                f" Counters not carried by both snapshots: "
                f"{', '.join(unmeasured)}."
                if unmeasured else ""
            ),
        })
        per_table.append(entry)

    # Schema churn, from the table lists of the same two snapshots.
    added = sorted(f"{s}.{t}" for (s, t) in (new_tbl.keys() - old_tbl.keys()))
    removed = sorted(f"{s}.{t}" for (s, t) in (old_tbl.keys() - new_tbl.keys()))
    churn_state = STATE_MEASURED if (old_tables and new_tables) else STATE_NOT_MEASURED

    measured = [e for e in per_table if e["state"] == STATE_MEASURED
                and e["change"] in {"idle", "active"}]
    active = [e for e in measured if e["change"] == "active"]
    reset = [e for e in per_table if e["change"] == "counters_reset"]

    return {
        "state": STATE_MEASURED,
        "from_surveyed_at": old_at,
        "to_surveyed_at": new_at,
        "interval_days": (round(interval_days, 4) if interval_days is not None else None),
        "per_table": per_table,
        "tables_with_a_rate": len(measured),
        "tables_active": len(active),
        "tables_idle": len(measured) - len(active),
        "tables_without_a_rate": len(per_table) - len(measured),
        "tables_counters_reset": len(reset),
        "schema_churn": {
            "state": churn_state,
            "tables_added": added,
            "tables_removed": removed,
            "explanation": (
                f"{len(added)} table(s) added and {len(removed)} removed "
                f"between the two snapshots."
                if churn_state == STATE_MEASURED else
                "One of the two snapshots carries no table rows, so schema "
                "churn could not be differenced."
            ),
        },
        "explanation": (
            f"Differenced {new_at} against {old_at}"
            + (f" ({interval_days:.2f} days apart)" if interval_days is not None else "")
            + f": {len(active)} table(s) changed, {len(measured) - len(active)} "
              f"genuinely idle, {len(per_table) - len(measured)} without a "
              f"usable rate"
            + (f" ({len(reset)} of those because the counters were reset)"
               if reset else "")
            + "."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 7. schema_diff — column/constraint-level  (design §9.1)
# ═══════════════════════════════════════════════════════════════════════════
#
# The table-add/drop half of `schema_diff` already exists as
# `derive_change_rates`'s `schema_churn` (differencing `database_tables`
# across the same two snapshots `db_change_rates` itself compares). This is
# the other half design §9.1 lists as still open: within tables that exist in
# BOTH snapshots, did a column get added, dropped, or retyped? Deliberately
# scoped to tables present in both snapshots — a whole new/dropped table's
# columns are schema churn's story to tell, not this one's, so a table add
# does not get double-reported as "N columns added" here too.
#
# A distinct `analysis_id` from `db_change_rates` (not folded into it):
# design §9.1's Perspective presets subscribe to `schema_diff` and
# `db_change_rates` separately (Steward gets `schema_diff`, `class_change`,
# `reference_set_change` — not `db_change_rates`), so a Steward's
# subscription needs its own comparator to check, and its own schedule to
# check it after (`scheduler._check_subscriptions` dispatches by
# `analysis_id`, matched to whichever schedule just completed).

def derive_schema_diff(registry, slug: str) -> dict:
    """Column-level `schema_diff`: new/dropped/retyped columns, restricted to
    tables present in both of the two most recent snapshots that carry
    `database_columns` rows.

    Same two-snapshot, zero-fetch shape as `derive_change_rates` — reads
    already-stored rows via `registry.query_detail_rows`, opens no
    connection. `state=STATE_NOT_MEASURED, reason="insufficient_history"`
    with fewer than two such snapshots, mirroring that function's absence
    discipline exactly (one snapshot is "cannot compare yet", never "no
    columns changed").
    """
    keys = _snapshot_keys(registry, slug)
    snapshots: list[tuple[str, str, list[dict]]] = []
    for surveyed_at, source in keys:
        columns = registry.query_detail_rows("database_columns", slug, surveyed_at, source)
        if not columns:
            continue
        snapshots.append((surveyed_at, source, columns))
        if len(snapshots) == 2:
            break

    if len(snapshots) < 2:
        return {
            "state": STATE_NOT_MEASURED,
            "reason": "insufficient_history",
            "snapshots_available": len(snapshots),
            "explanation": (
                f"Column-level schema diff needs two survey snapshots to "
                f"difference; {len(snapshots)} snapshot(s) of column rows "
                f"exist for this database. This is insufficient history — "
                f"NOT a finding that the schema is unchanged, and not a "
                f"failure. Survey the database again and this answers "
                f"itself."
            ),
            "columns_added": [], "columns_dropped": [], "columns_retyped": [],
        }

    (new_at, _new_src, new_columns) = snapshots[0]
    (old_at, _old_src, old_columns) = snapshots[1]

    old_tables = {_table_key(c) for c in old_columns}
    new_tables = {_table_key(c) for c in new_columns}
    common_tables = old_tables & new_tables

    def _col_key(c: dict) -> tuple[str, str, str]:
        return (c.get("schema_name") or "", c.get("table_name") or "", c.get("column_name") or "")

    old_by_col = {_col_key(c): c for c in old_columns if _table_key(c) in common_tables}
    new_by_col = {_col_key(c): c for c in new_columns if _table_key(c) in common_tables}

    added = sorted(
        f"{s}.{t}.{c}" for (s, t, c) in (new_by_col.keys() - old_by_col.keys())
    )
    dropped = sorted(
        f"{s}.{t}.{c}" for (s, t, c) in (old_by_col.keys() - new_by_col.keys())
    )

    def _type_of(row: dict) -> str:
        return (row.get("base_type") or row.get("data_type") or "").strip()

    retyped: list[dict] = []
    for key in sorted(old_by_col.keys() & new_by_col.keys()):
        old_type = _type_of(old_by_col[key])
        new_type = _type_of(new_by_col[key])
        if old_type and new_type and old_type != new_type:
            schema_name, table_name, column_name = key
            retyped.append({
                "qualified_name": f"{schema_name}.{table_name}.{column_name}",
                "old_type": old_type,
                "new_type": new_type,
            })

    changed = bool(added or dropped or retyped)
    parts: list[str] = []
    if added:
        parts.append(f"{len(added)} column(s) added: {', '.join(added)}")
    if dropped:
        parts.append(f"{len(dropped)} column(s) dropped: {', '.join(dropped)}")
    if retyped:
        detail = ", ".join(f"{r['qualified_name']} ({r['old_type']} -> {r['new_type']})" for r in retyped)
        parts.append(f"{len(retyped)} column(s) retyped: {detail}")

    return {
        "state": STATE_MEASURED,
        "from_surveyed_at": old_at,
        "to_surveyed_at": new_at,
        "columns_added": added,
        "columns_dropped": dropped,
        "columns_retyped": retyped,
        "explanation": (
            "; ".join(parts) if changed else
            f"Differenced {new_at} against {old_at} over "
            f"{len(common_tables)} table(s) present in both snapshots: no "
            f"column was added, dropped or retyped — genuinely unchanged."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 8. grant_change  (design §9.1)
# ═══════════════════════════════════════════════════════════════════════════

def derive_grant_change(registry, slug: str) -> dict:
    """`grant_change`: a new grant appearing (especially to `PUBLIC`) or an
    existing grant being revoked, between the two most recent snapshots that
    carry `database_grants` rows.

    Same two-snapshot, zero-fetch shape as `derive_change_rates`/
    `derive_schema_diff`. A grant's identity is
    `(schema_name, object_name, grantee, privilege_type)` — the same tuple
    `database_grants`' own UNIQUE constraint uses, so "the same grant" here
    means exactly what the registry already treats as one row.
    """
    keys = _snapshot_keys(registry, slug)
    snapshots: list[tuple[str, str, list[dict]]] = []
    for surveyed_at, source in keys:
        grants = registry.query_detail_rows("database_grants", slug, surveyed_at, source)
        if not grants:
            continue
        snapshots.append((surveyed_at, source, grants))
        if len(snapshots) == 2:
            break

    if len(snapshots) < 2:
        return {
            "state": STATE_NOT_MEASURED,
            "reason": "insufficient_history",
            "snapshots_available": len(snapshots),
            "explanation": (
                f"Grant change needs two survey snapshots to difference; "
                f"{len(snapshots)} snapshot(s) of grant rows exist for this "
                f"database. This is insufficient history — NOT a finding "
                f"that grants are unchanged, and not a failure. Survey the "
                f"database again and this answers itself."
            ),
            "grants_added": [], "grants_revoked": [], "public_grants_added": [],
        }

    (new_at, _new_src, new_grants) = snapshots[0]
    (old_at, _old_src, old_grants) = snapshots[1]

    def _grant_key(g: dict) -> tuple[str, str, str, str]:
        return (
            g.get("schema_name") or "", g.get("object_name") or "",
            g.get("grantee") or "", g.get("privilege_type") or "",
        )

    old_by_grant = {_grant_key(g): g for g in old_grants}
    new_by_grant = {_grant_key(g): g for g in new_grants}

    added_keys = new_by_grant.keys() - old_by_grant.keys()
    revoked_keys = old_by_grant.keys() - new_by_grant.keys()

    def _describe(key: tuple[str, str, str, str]) -> str:
        schema_name, object_name, grantee, privilege = key
        qualified = f"{schema_name}.{object_name}" if schema_name else object_name
        return f"{privilege} on {qualified} to {grantee}"

    added = sorted(_describe(k) for k in added_keys)
    revoked = sorted(_describe(k) for k in revoked_keys)
    # PUBLIC grants get their own field so a subscriber never has to parse
    # `added` looking for the specific case design §9.1 calls out by name
    # ("new grant, especially to PUBLIC") — the done-test this comparator
    # exists for names PUBLIC specifically, so it must be unambiguous in the
    # result, not buried in a generic "grants changed" message.
    public_added = sorted(
        _describe(k) for k in added_keys if (k[2] or "").upper() == "PUBLIC"
    )

    changed = bool(added or revoked)
    parts: list[str] = []
    if public_added:
        parts.append(f"{len(public_added)} new grant(s) to PUBLIC: {', '.join(public_added)}")
    non_public_added = sorted(set(added) - set(public_added))
    if non_public_added:
        parts.append(f"{len(non_public_added)} other new grant(s): {', '.join(non_public_added)}")
    if revoked:
        parts.append(f"{len(revoked)} grant(s) revoked: {', '.join(revoked)}")

    return {
        "state": STATE_MEASURED,
        "from_surveyed_at": old_at,
        "to_surveyed_at": new_at,
        "grants_added": added,
        "grants_revoked": revoked,
        "public_grants_added": public_added,
        "explanation": (
            "; ".join(parts) if changed else
            f"Differenced {new_at} against {old_at}: no grant was added or "
            f"revoked — genuinely unchanged."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Proposed DataScope  (design §5.4; key names from support doc §7)
# ═══════════════════════════════════════════════════════════════════════════

def propose_data_scope(inputs: DerivedInputs) -> dict:
    """Earliest/latest values across date columns → a proposed `DataScope`.

    Key names are `DataScopeProperties`' own (`dataCoverageStartTime`,
    `dataCoverageEndTime`), per `egeria-support-for-multi-resource.md` §7's
    convention — *"a `ResourceMeasureAnnotation` whose `resourceProperties`
    use the same key names as `DataScopeProperties` plus `confidence` and
    `basis`"* — so the Curate prefill is a key-for-key copy. Published with
    `contentStatus: DRAFT` per §3's project-owner decision: measured is not
    declared.

    **Data availability is the limit here, and it is recorded rather than
    worked around.** `database_column_profiles.min_value`/`max_value` are
    populated ONLY by `result_materializer`'s native-survey path (from an
    Egeria column-values annotation's `range_from`/`range_to`). Slice 7's
    local `pg_stats` path does not write them. What slice 7 DOES write is
    `histogram_bounds_json`, whose first and last elements are Postgres's own
    estimated extremes — so a local-only survey can still yield a range, at
    lower confidence and labelled as the estimate it is.
    """
    date_columns: list[dict] = []
    for col in inputs.columns:
        base = (col.get("base_type") or col.get("data_type") or "").lower()
        if any(marker in base for marker in _DATE_TYPE_MARKERS):
            date_columns.append(col)

    if not date_columns:
        return {
            "state": STATE_MEASURED,
            "has_scope": False,
            "reason": "no_date_columns",
            "explanation": (
                f"Measured: none of this database's {len(inputs.columns)} "
                f"stored columns has a date or timestamp type, so it has no "
                f"temporal scope to propose. A real finding, not missing data."
            ),
        }

    profiles = {
        (_table_key(p), p.get("column_name")): p for p in inputs.profiles
    }
    ranges: list[dict] = []
    for col in date_columns:
        profile = profiles.get((_table_key(col), col.get("column_name")))
        if not profile:
            continue
        low = (profile.get("min_value") or "").strip() or None
        high = (profile.get("max_value") or "").strip() or None
        basis = "profile_min_max"
        if low is None or high is None:
            bounds = profile.get("histogram_bounds_json")
            if isinstance(bounds, list) and len(bounds) >= 2:
                low = low or str(bounds[0])
                high = high or str(bounds[-1])
                basis = "histogram_bounds"
        if low is None or high is None:
            continue
        ranges.append({
            "schema_name": col.get("schema_name"),
            "table_name": col.get("table_name"),
            "column_name": col.get("column_name"),
            "start": low,
            "end": high,
            "basis": basis,
        })

    if not ranges:
        return {
            "state": STATE_NOT_MEASURED,
            "has_scope": False,
            "reason": "no_profiled_date_columns",
            "date_column_count": len(date_columns),
            "explanation": (
                f"{len(date_columns)} date/timestamp column(s) exist, but none "
                f"has a stored profile carrying a value range. "
                f"`min_value`/`max_value` are written only by the native "
                f"Egeria survey read-back path, and no stored "
                f"`histogram_bounds` is available to estimate from either — "
                f"so the temporal scope is NOT established. Run ANALYZE and "
                f"the statistics step, or a native survey, rather than reading "
                f"this as 'no time range'."
            ),
        }

    starts = sorted(r["start"] for r in ranges)
    ends = sorted(r["end"] for r in ranges)
    estimated = [r for r in ranges if r["basis"] == "histogram_bounds"]
    # An exact min/max from a profile is a real observation; a histogram
    # bound is Postgres's estimate of the extreme from a sample. Confidence
    # reflects which of the two the proposal rests on, and how many columns
    # agreed.
    confidence = 80 if not estimated else 55
    if len(estimated) == len(ranges):
        confidence = 50

    return {
        "state": STATE_MEASURED,
        "has_scope": True,
        # DataScopeProperties' own key names — support doc §7.
        "dataCoverageStartTime": starts[0],
        "dataCoverageEndTime": ends[-1],
        "confidence": confidence,
        "basis": ", ".join(sorted({r["basis"] for r in ranges})),
        "column_ranges": ranges,
        "date_column_count": len(date_columns),
        "profiled_column_count": len(ranges),
        "explanation": (
            f"Proposed from {len(ranges)} of {len(date_columns)} "
            f"date/timestamp column(s): coverage {starts[0]} … {ends[-1]}. "
            + (f"{len(estimated)} of those range(s) come from pg_stats "
               f"histogram bounds, which are ANALYZE-time estimates of the "
               f"extremes rather than exact min/max values — hence the "
               f"reduced confidence. " if estimated else "")
            + "This is a MEASURED scope, published as a proposal "
              "(contentStatus DRAFT). A curator declaring the asset's real "
              "DataScope may legitimately widen it (\"x..current\") — measured "
              "is not declared."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 10. subject_signals  (design §16.2/§16.3, added 2026-09-24)
# ═══════════════════════════════════════════════════════════════════════════
#
# Sections 10–12 are defined AHEAD of §9's aggregation-grain helpers below,
# because §9 calls them: `apply_container_grain` runs `derive_subject_signals`
# and `derive_coverage_signals` over container-scoped inputs. Numbered by when
# they were added, ordered by what reads what.
#
# §16.3, Scouting row 1: *"What is this data about — which subjects, business
# terms or data classes does it appear to hold?"* — from *"table, column, file
# and folder names; `pg_description`; README and descriptor text"*, at *"low–
# medium; a name is a claim"* confidence.
#
# Two things this deliberately does NOT do:
#
# * **It does not match against Egeria's glossary.** §16.3's mechanism column
#   reads "Catalog + Egeria glossary", and the glossary half is a fetch — this
#   module opens nothing. So the output is *candidate* terms, with
#   `glossary_matched: False` and a named reason, rather than terms silently
#   presented as if they had been resolved against a vocabulary. A later
#   Egeria-reading step (§16.7's `governance_context_readback` is the natural
#   home) can resolve them; nothing here pretends to have.
# * **It does not score a database's subject.** There is no single subject and
#   no composite. The output is a ranked list of terms, each carrying the
#   basis it came from, which is §15's "no composite score" applied here.

#: Tokens that carry no subject. Every one of these is either structural
#: (`id`, `pk`), temporal plumbing (`created`, `updated`), a modelling-pattern
#: word already reported by `db_classification` (`dim`, `fact`, `stg`), or a
#: generic container word (`table`, `data`). Kept conservative in the other
#: direction on purpose: `log`, `audit`, `event` and `invoice` are NOT here,
#: because each genuinely names what the data is about.
_SUBJECT_STOPWORDS = frozenset({
    "id", "ids", "uid", "uuid", "guid", "key", "keys", "pk", "fk", "seq",
    "sequence", "num", "no", "code", "codes", "type", "types", "kind",
    "status", "state", "flag", "flags", "name", "names", "desc", "description",
    "value", "values", "val", "data", "table", "tables", "tbl", "col", "cols",
    "column", "columns", "row", "rows", "field", "fields", "record", "records",
    "created", "updated", "modified", "changed", "deleted", "inserted",
    "create", "update", "delete", "insert", "at", "on", "by", "of", "in",
    "for", "the", "and", "or", "to", "from", "with", "is", "has", "was",
    "ts", "timestamp", "datetime", "date", "dates", "dt", "dttm", "time",
    "year", "month", "day", "hour", "week", "quarter", "period", "asof",
    # Period words name the GRAIN, not the subject — `grain_determination`
    # reports them as an interval with a basis, and letting `daily_sales`
    # contribute "daily" as a candidate subject would put the same signal in
    # two places under two meanings.
    "daily", "hourly", "weekly", "monthly", "quarterly", "annual", "yearly",
    "count", "total", "sum", "avg", "min", "max", "pct", "ratio", "rate",
    "version", "rev", "revision", "active", "enabled", "valid", "current",
    "tmp", "temp", "stg", "staging", "raw", "bak", "backup", "old", "new",
    "test", "dummy", "sample", "temp1", "aux", "misc", "other", "default",
    "ref", "lookup", "lu", "dim", "fact", "agg", "mart", "cube", "rollup",
    "summary", "snapshot", "hist", "history", "archive", "meta", "sys",
    "system", "public", "main", "base", "core", "src", "tgt", "target",
    "source", "detail", "details", "header", "line", "lines", "item", "items",
    "text", "json", "jsonb", "xml", "blob", "bytea", "int", "bigint", "str",
})

#: A token shorter than this is dropped. Three-letter subject words exist
#: (`tax`, `fee`, `sku`) so the floor is 3, not 4.
_MIN_SUBJECT_TOKEN = 3

#: Confidence per basis, §16.2's "low–medium; a name is a claim" made
#: concrete. A comment is a human sentence written to explain the thing, so it
#: outranks a name; a term appearing in BOTH is the strongest signal available
#: without reading a value or resolving a glossary, and is still capped well
#: below certainty.
SUBJECT_BASIS_NAME = "name"
SUBJECT_BASIS_COMMENT = "comment"
SUBJECT_BASIS_BOTH = "comment_and_name"

_SUBJECT_CONFIDENCE = {
    SUBJECT_BASIS_NAME: 25,
    SUBJECT_BASIS_COMMENT: 45,
    SUBJECT_BASIS_BOTH: 60,
}

#: How many terms the payload carries. A database with 4,000 columns produces
#: a long tail of one-off tokens that is noise on a card and in an annotation;
#: the full count is still reported as `term_count` so the cut is visible.
_SUBJECT_TERM_LIMIT = 40

#: `subject_signals`' own reasons, named so a consumer can branch on them
#: rather than on prose.
SUBJECT_REASON_NO_ROWS = "no_schema_rows"
SUBJECT_REASON_NO_SUBJECT_NAMES = "no_subject_bearing_names"


def derive_subject_signals(inputs: DerivedInputs) -> dict:
    """Candidate subject terms from stored names and comments. Zero fetch.

    Three states, and they must not read alike:

    - **measured, with terms** — names and/or comments name things.
    - **measured, with none** (`no_subject_bearing_names`) — a real finding:
      every name in this database is structural (`t1.c1`, `id`, `value`), so
      the names say nothing about the subject. Someone reading this should
      reach for Enrichment (§16.3's own Enrichment row), not for ANALYZE.
    - **not measured** (`no_schema_rows`) — nothing was ever surveyed.

    `comments_captured` carries the fourth distinction, the one
    `check_conventions` already draws for its own comment checks: if NOT ONE
    table or column in the whole database carries a description, the likelier
    explanation is that comments were never captured than that a real database
    documents nothing — so the confidence of every term drops to name-only and
    the payload says why.
    """
    tables = [t for t in inputs.tables if _is_base_table(t)]
    if not tables and not inputs.columns:
        return {
            "state": STATE_NOT_MEASURED,
            "reason": SUBJECT_REASON_NO_ROWS,
            "terms": [],
            "term_count": 0,
            "explanation": (
                "No stored table or column rows for this database, so there is "
                "nothing to read a subject from. NOT a finding that the data "
                "has no subject — run the schema step."
            ),
        }

    # {term: {basis-source: occurrence count}} plus the containers it appears in.
    hits: dict[str, dict[str, int]] = {}
    containers: dict[str, set] = {}

    def _record(term: str, source: str, container: str) -> None:
        if len(term) < _MIN_SUBJECT_TOKEN or term.isdigit():
            return
        if term in _SUBJECT_STOPWORDS:
            return
        bucket = hits.setdefault(term, {})
        bucket[source] = bucket.get(source, 0) + 1
        containers.setdefault(term, set()).add(container)

    documented_tables = 0
    documented_columns = 0

    for table in tables:
        container = table.get("schema_name") or ""
        for token in _tokenise_name(table.get("table_name") or ""):
            _record(token, "table_name", container)
        comment = (table.get("description") or "").strip()
        if comment:
            documented_tables += 1
            for token in _tokenise_name(comment):
                _record(token, "table_comment", container)

    for col in inputs.columns:
        container = col.get("schema_name") or ""
        for token in _tokenise_name(col.get("column_name") or ""):
            _record(token, "column_name", container)
        comment = (col.get("description") or "").strip()
        if comment:
            documented_columns += 1
            for token in _tokenise_name(comment):
                _record(token, "column_comment", container)

    # The same test `check_conventions` uses, for the same reason.
    comments_captured = not (
        documented_tables == 0 and documented_columns == 0 and bool(inputs.columns)
    )

    terms: list[dict] = []
    for term, sources in hits.items():
        from_comment = bool(sources.get("table_comment") or sources.get("column_comment"))
        from_name = bool(sources.get("table_name") or sources.get("column_name"))
        if from_comment and from_name:
            basis = SUBJECT_BASIS_BOTH
        elif from_comment:
            basis = SUBJECT_BASIS_COMMENT
        else:
            basis = SUBJECT_BASIS_NAME
        terms.append({
            "term": term,
            "basis": basis,
            "confidence": _SUBJECT_CONFIDENCE[basis],
            "occurrences": sum(sources.values()),
            "sources": dict(sorted(sources.items())),
            "containers": sorted(containers.get(term, ())),
        })

    terms.sort(key=lambda t: (-t["confidence"], -t["occurrences"], t["term"]))
    shown = terms[:_SUBJECT_TERM_LIMIT]

    if not terms:
        return {
            "state": STATE_MEASURED,
            "reason": SUBJECT_REASON_NO_SUBJECT_NAMES,
            "terms": [],
            "term_count": 0,
            "comments_captured": comments_captured,
            "glossary_matched": False,
            "glossary_reason": "zero_fetch_step",
            "label": "gap",
            "explanation": (
                f"Measured: every token in this database's {len(tables)} "
                f"table name(s) and {len(inputs.columns)} column name(s) is "
                f"structural or temporal plumbing (`id`, `created_at`, "
                f"`value`), and no comment names a subject either. The names "
                f"genuinely do not say what the data is about — a real "
                f"finding that points at Enrichment, not at a missing survey "
                f"step."
            ),
        }

    top = ", ".join(t["term"] for t in shown[:8])
    return {
        "state": STATE_MEASURED,
        "terms": shown,
        "term_count": len(terms),
        "shown_count": len(shown),
        "comments_captured": comments_captured,
        # Never resolved against a vocabulary here — see the section comment.
        "glossary_matched": False,
        "glossary_reason": "zero_fetch_step",
        "label": "info",
        "confidence": shown[0]["confidence"],
        "explanation": (
            f"{len(terms)} candidate subject term(s) from stored names"
            + (" and comments" if comments_captured else "")
            + f"; strongest: {top}. "
            + ("These rest on names alone: not one table or column in this "
               "database carries a comment, which reads as comments never "
               "having been captured rather than as a database that documents "
               "nothing — so no term here reaches comment-level confidence. "
               if not comments_captured else "")
            + "Candidate terms only: a name is a claim (§16.2), and NOTHING "
              "here was matched against Egeria's glossary or a data-class "
              "registry — this step opens no connection, so an unmatched term "
              "means 'not looked up', never 'not a known term'."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 11. coverage_signals  (design §16.2/§16.3, added 2026-09-24)
# ═══════════════════════════════════════════════════════════════════════════
#
# §16.3, Scouting row 3: *"What period and places does it appear to cover, from
# catalog statistics, file footers and descriptors alone?"*
#
# §16.2's warning is the whole point of this analysis and the thing most
# likely to be undone by a later edit: `pg_stats.histogram_bounds` gives a date
# column's min and max **without reading rows** — *"high when stats are fresh;
# **absent means 'run ANALYZE', not 'no dates'**"*. Those two states are one
# line apart in the data and opposite in meaning, so they are separate,
# named reasons here, and `tests/test_fit_and_coverage_signals.py` fails if
# anything collapses them.
#
# Scope, stated: **databases only**, per this change's brief. Parquet/Feather
# footers (§16.2 row 5) and DCAT/Croissant descriptors (row 6) are filesystem
# and dataset work and are not attempted — reported as `not_applicable` for a
# database rather than silently missing.

#: `coverage_signals`' temporal reasons.
COVERAGE_REASON_NO_ROWS = "no_schema_rows"
#: MEASURED NEGATIVE. Not one stored column has a temporal type or a
#: date-shaped name: this database holds nothing dated, which is a real answer
#: and enough on its own to exclude it from a lens with a time window.
COVERAGE_REASON_NO_DATE_COLUMNS = "no_date_columns"
#: NOT MEASURED. Date columns exist and no stored statistics carry bounds for
#: any of them. §16.2's warning, as a reason code: the remedy is `ANALYZE`
#: plus the statistics step, and the answer is unknown, NOT "no dates".
COVERAGE_REASON_STATS_NOT_POPULATED = "stats_not_populated"
#: MEASURED. Bounds were found.
COVERAGE_REASON_MEASURED = "measured"

#: The remedy sentence the `stats_not_populated` state carries. One string so
#: the card, the annotation and `preliminary_fit`'s `could_not_check` verdict
#: all say the same thing.
COVERAGE_ANALYZE_REMEDY = (
    "Run ANALYZE on the target database, then re-run the statistics step "
    "(`postgres_schema_and_stats`), which stores pg_stats histogram bounds. "
    "Note that step needs the `read` capability for the column in question — "
    "`pg_stats` is filtered by column-level SELECT, not by `pg_monitor` — so "
    "if the credential cannot SELECT that column, this stays unknown however "
    "often ANALYZE runs."
)

#: Place-bearing column-name tokens (§16.2's "geography from names and
#: classes", name half only). Whole tokens, never substrings: `state` inside
#: `statement` is not a place, and `lat` inside `latest` is not a latitude.
_PLACE_NAME_TOKENS = frozenset({
    "country", "countries", "region", "regions", "state", "province",
    "prefecture", "city", "town", "district", "county", "territory",
    "continent", "postcode", "postal", "zip", "zipcode", "latitude", "lat",
    "longitude", "lon", "lng", "geom", "geography", "geo", "location",
    "locale", "locality", "address", "timezone", "tz", "market", "site",
})

#: Not every place token is equally strong. A column called `latitude` or
#: `postcode` is almost certainly geographic; `site`, `market`, `location` and
#: `state` are routinely something else entirely (`state` is very often a
#: status). Two tiers rather than one flat list, so the payload can say which.
_STRONG_PLACE_TOKENS = frozenset({
    "country", "countries", "region", "regions", "province", "prefecture",
    "city", "postcode", "postal", "zip", "zipcode", "latitude", "longitude",
    "geom", "geography", "continent", "county",
})


def derive_coverage_signals(inputs: DerivedInputs) -> dict:
    """Period and places from catalog statistics and names alone. Zero fetch.

    Three blocks, each with its own state, because they fail independently:
    `temporal` (the one §16.2 warns about), `spatial` (names only at this
    tier), and `partitions` (a collection gap, reported as one).
    """
    if not inputs.columns:
        return {
            "state": STATE_NOT_MEASURED,
            "reason": COVERAGE_REASON_NO_ROWS,
            "temporal": {
                "state": STATE_NOT_MEASURED,
                "reason": COVERAGE_REASON_NO_ROWS,
                "label": "unverified",
                # NOT the ANALYZE remedy: nothing has been surveyed at all, so
                # running ANALYZE on the target would change nothing here. A
                # remedy that names the wrong fix is worse than none.
                "remedy": (
                    "Run the schema step (`postgres_schema_and_stats`) — no "
                    "column row for this database has ever been stored."
                ),
                "explanation": (
                    "No stored column rows, so no date column could even be "
                    "looked for. Unknown, not 'no dates'."
                ),
            },
            "spatial": {
                "state": STATE_NOT_MEASURED,
                "reason": COVERAGE_REASON_NO_ROWS,
                "explanation": "No stored column rows to read place names from.",
            },
            "partitions": _partition_signal(),
            "explanation": (
                "No stored column rows for this database. Neither the period "
                "nor the places it covers is established — run the schema "
                "step. NOT a finding that it covers nothing."
            ),
        }

    return {
        "state": STATE_MEASURED,
        "temporal": _temporal_coverage(inputs),
        "spatial": _spatial_coverage(inputs),
        "partitions": _partition_signal(),
        "explanation": (
            "Coverage estimated from stored catalog statistics and column "
            "names only — no value was read. See `temporal`, `spatial` and "
            "`partitions`, each of which carries its own state: they are "
            "established independently and one being unknown says nothing "
            "about the others."
        ),
    }


def _temporal_coverage(inputs: DerivedInputs) -> dict:
    """The period, from `pg_stats` bounds on date columns. §16.2's row 4.

    Shares `propose_data_scope`'s reading of the stored profile (exact
    `min_value`/`max_value` first, `histogram_bounds_json` second) and differs
    from it in two ways that matter: it also counts columns that are dated by
    NAME but not by type, and it distinguishes "no date columns at all" from
    "date columns whose statistics were never populated" with named reason
    codes instead of two similar prose sentences.
    """
    typed: list[dict] = []
    named_only: list[dict] = []
    for col in inputs.columns:
        base = (col.get("base_type") or col.get("data_type") or "").lower()
        if any(marker in base for marker in _DATE_TYPE_MARKERS):
            typed.append(col)
        elif _name_suggests_date(col.get("column_name") or ""):
            named_only.append(col)

    if not typed and not named_only:
        return {
            "state": STATE_MEASURED,
            "reason": COVERAGE_REASON_NO_DATE_COLUMNS,
            "date_column_count": 0,
            "named_date_column_count": 0,
            "label": "gap",
            "explanation": (
                f"MEASURED NEGATIVE: none of this database's "
                f"{len(inputs.columns)} stored columns has a date or timestamp "
                f"type, and none is named like a date either. It holds nothing "
                f"dated, so it has no period to cover — a real finding, not a "
                f"missing statistic."
            ),
        }

    profiles = {
        (_table_key(p), p.get("column_name")): p for p in inputs.profiles
    }
    ranges: list[dict] = []
    for col in typed:
        profile = profiles.get((_table_key(col), col.get("column_name")))
        if not profile:
            continue
        low = (profile.get("min_value") or "").strip() or None
        high = (profile.get("max_value") or "").strip() or None
        basis = "profile_min_max"
        if low is None or high is None:
            bounds = profile.get("histogram_bounds_json")
            if isinstance(bounds, list) and len(bounds) >= 2:
                low = low or str(bounds[0])
                high = high or str(bounds[-1])
                basis = "histogram_bounds"
        if low is None or high is None:
            continue
        ranges.append({
            "schema_name": col.get("schema_name"),
            "table_name": col.get("table_name"),
            "column_name": col.get("column_name"),
            "start": low,
            "end": high,
            "basis": basis,
        })

    if not ranges:
        profiled = sum(
            1 for col in typed
            if (_table_key(col), col.get("column_name")) in profiles
        )
        return {
            # THE distinction §16.2 warns about. NOT_MEASURED, never a
            # negative finding: the period is unknown.
            "state": STATE_NOT_MEASURED,
            "reason": COVERAGE_REASON_STATS_NOT_POPULATED,
            "date_column_count": len(typed),
            "named_date_column_count": len(named_only),
            "profiled_column_count": profiled,
            "label": "unverified",
            "remedy": COVERAGE_ANALYZE_REMEDY,
            "explanation": (
                f"{len(typed)} date/timestamp column(s) exist"
                + (f" (plus {len(named_only)} column(s) dated by name only)"
                   if named_only else "")
                + f", and {'none of their' if not profiled else 'not one of the ' + str(profiled) + ' stored'} "
                + "statistics rows carries a value range. pg_stats histogram "
                  "bounds are populated by ANALYZE, so their ABSENCE means "
                  "ANALYZE has not run (or the credential cannot see "
                  "pg_stats) — it does NOT mean this database holds no dates. "
                  "The period covered is UNKNOWN. " + COVERAGE_ANALYZE_REMEDY
            ),
        }

    starts = sorted(r["start"] for r in ranges)
    ends = sorted(r["end"] for r in ranges)
    estimated = [r for r in ranges if r["basis"] == "histogram_bounds"]
    confidence = 80 if not estimated else 55
    if len(estimated) == len(ranges):
        confidence = 50
    return {
        "state": STATE_MEASURED,
        "reason": COVERAGE_REASON_MEASURED,
        # `DataScopeProperties`' own key names, per egeria-support-for-multi-
        # resource.md §7 — so a lens comparison and a Curate prefill both read
        # the same keys on both sides (§16.1).
        "dataCoverageStartTime": starts[0],
        "dataCoverageEndTime": ends[-1],
        "confidence": confidence,
        "basis": ", ".join(sorted({r["basis"] for r in ranges})),
        "column_ranges": ranges,
        "date_column_count": len(typed),
        "named_date_column_count": len(named_only),
        "profiled_column_count": len(ranges),
        "label": "pass",
        "explanation": (
            f"Coverage {starts[0]} … {ends[-1]}, from {len(ranges)} of "
            f"{len(typed)} date/timestamp column(s), with no row read. "
            + (f"{len(estimated)} range(s) come from pg_stats histogram "
               f"bounds, which are ANALYZE-time estimates of the extremes "
               f"rather than exact values — hence the reduced confidence. "
               if estimated else "")
            + (f"{len(typed) - len(ranges)} date column(s) have no stored "
               f"bounds and are NOT included; their own range is unknown "
               f"rather than empty. " if len(ranges) < len(typed) else "")
            + "An estimate from the catalog, not a measured cadence: gaps "
              "inside this range are invisible here and need "
              "`coverage_profile` (Analysis tier)."
        ),
    }


def _spatial_coverage(inputs: DerivedInputs) -> dict:
    """The places, from column NAMES only. §16.2's row 7, name half.

    Returns candidates, never an extent. The set of regions actually present
    needs the values, which is a data read (`coverage_profile` +
    `reference_data_match`, Analysis tier) — so `values_read` is `False` on
    every payload this returns, and the verdict word is `candidates_only`
    rather than anything that could be mistaken for a measured extent.
    """
    strong: list[dict] = []
    weak: list[dict] = []
    for col in inputs.columns:
        name = col.get("column_name") or ""
        tokens = set(_tokenise_name(name))
        matched = sorted(tokens & _PLACE_NAME_TOKENS)
        if not matched:
            continue
        entry = {
            "schema_name": col.get("schema_name"),
            "table_name": col.get("table_name"),
            "column_name": name,
            "data_type": col.get("base_type") or col.get("data_type") or "",
            "matched_tokens": matched,
        }
        if set(matched) & _STRONG_PLACE_TOKENS:
            strong.append(entry)
        else:
            weak.append(entry)

    if not strong and not weak:
        return {
            "state": STATE_MEASURED,
            "verdict": "no_place_columns",
            "place_columns": [],
            "ambiguous_place_columns": [],
            "values_read": False,
            "next_analysis": "coverage_profile",
            "label": "info",
            "explanation": (
                f"MEASURED NEGATIVE, at name level only: no column among "
                f"{len(inputs.columns)} is named like a place (country, "
                f"region, postcode, lat/lon…). A place could still be encoded "
                f"inside a text column under another name, so this is "
                f"evidence of no geographic COLUMN, not proof of no "
                f"geographic content — the values have not been read."
            ),
        }

    return {
        "state": STATE_MEASURED,
        "verdict": "candidates_only",
        "place_columns": strong,
        "ambiguous_place_columns": weak,
        "values_read": False,
        "next_analysis": "coverage_profile",
        "label": "info",
        "confidence": 35 if strong else 20,
        "explanation": (
            f"{len(strong)} column(s) named unambiguously like a place"
            + (f", plus {len(weak)} whose name is geographic in some schemas "
               f"and not in others (`state`, `site`, `market`)" if weak else "")
            + ". These are the NAMES of place-bearing columns — the places "
              "actually present are NOT established, because no value was "
              "read. The set of regions, or a bounding box, needs "
              "`coverage_profile` plus `reference_data_match` (Analysis "
              "tier)."
        ),
    }


def _partition_signal() -> dict:
    """Partition bounds: named by §16.2, not collectable from stored rows.

    §16.2 row 4 lists *"partition bounds from `pg_partitioned_table` / check
    constraints give exact ranges"* as a free Scouting signal, and it would be
    — but nothing in RE collects it: no structured table carries partition
    metadata (checked against `registry.py`'s `_DB_FS_DETAIL_TABLE_DDL` and
    `connection.py`'s collection SQL, 2026-09-24). So this block reports a
    COLLECTION GAP with the step that would close it, rather than returning
    nothing and letting a reader conclude the database is unpartitioned.

    `grain_determination`'s `partition_suffix` interval basis is a *naming*
    signal about the same subject and is not a substitute: a name suggests a
    period, a bound states one.
    """
    return {
        "state": STATE_NOT_COLLECTED,
        "reason": "partition_metadata_not_stored",
        "label": "unverified",
        "remedy": (
            "Extend `postgres_schema_and_stats` to store `pg_partitioned_table` "
            "/ `pg_class.relispartition` and each child partition's bound, then "
            "this block becomes an exact range at no extra tier — it is "
            "unfiltered catalog metadata (`catalog` capability)."
        ),
        "explanation": (
            "Partition bounds are an exact, free coverage signal (§16.2) and "
            "RE does not collect them: no structured table carries partition "
            "metadata today. So this is UNKNOWN — the absence here means "
            "nothing collects it, NOT that this database is unpartitioned."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 12. preliminary_fit  (design §16.3 Discovery, §16.5, added 2026-09-24)
# ═══════════════════════════════════════════════════════════════════════════
#
# §16.3, Discovery row 1: *"Could this be in scope for what I am looking for —
# worth the full pass?"* — *"zero-fetch; the three Scouting estimates against
# the investigation's `DataLens`; per-input confidence; renders 'no requirement
# declared' when none"*.
#
# **Scope decision for this change (2026-09-24), stated because it is a
# deliberate narrowing.** §16.5 points 3–5 describe a full `DataLens`: an
# Egeria governance definition, versioned, editable in the investigation's
# framing form, with Curate-tier "make this my lens" actions and
# cross-investigation matching. None of that is built here. §16.6's own cost
# table is the authority for splitting it that way — *"`preliminary_fit` works
# before it exists, as a comparison across resources"* — and §16.5 point 2
# says the same: *"the same question works with no lens at all as a comparison
# across resources"*.
#
# So what this takes is a plain mapping, and there are exactly two ways to
# supply one:
#
#   * **no lens** → the verdict is `no_requirement_declared`, and the payload
#     still carries `achievable`: what this resource *could* satisfy (§16.5
#     point 2's "here is what the working set could satisfy"). Never a vacuous
#     pass, and never a silent fail.
#   * **an ad-hoc lens** → a dict using `DataScopeProperties`' / §16.6's own
#     `scopeElements` key names (`subjectTerms`, `interval`,
#     `dataCoverageStartTime`, `dataCoverageEndTime`, `regions`), which is the
#     shape a real `DataLens` will serialise to. `lens_source` records where
#     it came from, and `lens_version` is carried through when present (§16.5
#     point 4 — a fit verdict is only meaningful against a lens version), so
#     wiring a stored, versioned lens in later changes the CALLER, not this
#     function.

FIT_FITS = "fits"
FIT_DOES_NOT_FIT = "does_not_fit"
#: An input this verdict depends on was itself not established. The state
#: §16.2's ANALYZE warning makes unavoidable: with `coverage_signals`
#: unknown, "does not fit" and "fits" are both unsupported claims.
FIT_COULD_NOT_CHECK = "could_not_check"
#: NONE of the three Scouting estimates is established — a never-surveyed
#: database. Distinct from `FIT_COULD_NOT_CHECK`: that one names specific
#: inputs the lens needed that weren't measured FOR THIS ANALYSIS; this one
#: is that nothing about the resource has been surveyed at all. Both are
#: statements about OUR measurement, not the resource, but they call for
#: different remedies — a lens tweak or an ANALYZE vs. running a survey at
#: all — so REPLY-COPY-REVIEW-CREDENTIAL-AND-FIT-LANGUAGE.md §4 gives them
#: separate verdicts rather than collapsing the never-surveyed case into
#: "could not check".
FIT_NOTHING_MEASURED = "nothing_measured"
#: No lens, or a lens that declares nothing this tier can compare.
FIT_NO_REQUIREMENT = "no_requirement_declared"
#: Per-input only: the lens declares nothing on this axis, but does declare
#: something on another. Distinct from `FIT_NO_REQUIREMENT`, which is the
#: whole-verdict version.
FIT_INPUT_NO_CRITERION = "no_criterion"

#: Lens keys, §16.6's `scopeElements` set. Named constants because the whole
#: point of §16.1 is that lens and scope use the IDENTICAL key names, and a
#: typo on one side would silently read as "no criterion declared".
LENS_SUBJECT_TERMS = "subjectTerms"
LENS_DATA_CLASSES = "dataClasses"
LENS_REGIONS = "regions"
LENS_INTERVAL = "interval"
LENS_GRAIN_STATEMENT = "grainStatement"
LENS_COVERAGE_START = "dataCoverageStartTime"
LENS_COVERAGE_END = "dataCoverageEndTime"

#: Criteria a real `DataLens` carries that this tier cannot answer, with the
#: analysis that can. Reported as `deferred_criteria` rather than scored — a
#: lens that asks for EMEA regions must not read as "fits" because the region
#: test was quietly skipped, nor as "does not fit" because it was impossible.
_DEFERRED_CRITERIA = {
    LENS_REGIONS: (
        "coverage_profile (Analysis tier)",
        "which regions are actually present needs the values read; "
        "`coverage_signals` only names the columns that could carry them",
    ),
    LENS_DATA_CLASSES: (
        "data_class_match (Analysis tier)",
        "data-class membership is matched against sampled values, and this "
        "tier reads no value; `subject_signals`' candidate terms are names, "
        "not class matches",
    ),
    LENS_GRAIN_STATEMENT: (
        "grain_determination (measured) / requirement_fit (Assessment tier)",
        "comparing two grain STATEMENTS in prose is not a set test; the "
        "interval is the part this tier can compare",
    ),
}


def normalise_lens(lens: dict | None) -> dict | None:
    """A lens mapping, or None. Tolerates an empty dict as "no lens".

    Deliberately permissive about unknown keys (a real `DataLens` carries
    bounding-box and validity fields this tier has no estimate for) and
    deliberately strict about nothing: an unrecognised key is reported in
    `unused_criteria`, never silently dropped.
    """
    if not isinstance(lens, dict) or not lens:
        return None
    cleaned = {k: v for k, v in lens.items() if v not in (None, "", [], {})}
    return cleaned or None


def _fit_subject(subject: dict, lens: dict) -> dict:
    sought = [str(t).strip().lower() for t in (lens.get(LENS_SUBJECT_TERMS) or [])
              if str(t).strip()]
    if not sought:
        return {"verdict": FIT_INPUT_NO_CRITERION, "confidence": 0,
                "explanation": "The lens declares no subject terms."}
    if (subject or {}).get("state") != STATE_MEASURED:
        return {
            "verdict": FIT_COULD_NOT_CHECK,
            "confidence": 0,
            "reason": (subject or {}).get("reason") or "subject_not_established",
            "explanation": (
                "`subject_signals` is not established for this database "
                f"({(subject or {}).get('reason') or 'unknown reason'}), so "
                "whether it holds the sought subject cannot be answered "
                "either way."
            ),
        }
    held = {t["term"]: t for t in (subject.get("terms") or [])}
    if not held:
        return {
            "verdict": FIT_COULD_NOT_CHECK,
            "confidence": 0,
            "reason": SUBJECT_REASON_NO_SUBJECT_NAMES,
            "explanation": (
                "This database's names carry no subject term at all (a real "
                "finding of `subject_signals`), so they cannot confirm OR "
                "rule out the sought subject — the names simply do not say. "
                "Enrichment, or a value-level pass, is what would answer it."
            ),
        }
    # PREFIX match both ways, not a free substring test: a lens term "sales"
    # should match a held "salesorder" and a held "order" should match a
    # sought "orders", but an arbitrary substring test would match the held
    # "art" against a sought "cart" — a false fit, and at this tier a false
    # fit costs the whole expensive pass the gate exists to withhold. The
    # lens's own terms are tokenised exactly as the held ones were, so
    # "sales_order" compares like with like.
    sought_tokens = {tok for term in sought for tok in _tokenise_name(term)}
    matched = sorted(
        term for term in held
        if term in sought_tokens
        or any(term.startswith(tok) or tok.startswith(term)
               for tok in sought_tokens)
    )
    if matched:
        confidence = max(held[m]["confidence"] for m in matched)
        return {
            "verdict": FIT_FITS,
            "confidence": confidence,
            "matched_terms": matched,
            "sought_terms": sought,
            "explanation": (
                f"Subject overlap on {', '.join(matched)} (confidence "
                f"{confidence} — these are candidate terms from names and "
                f"comments, never glossary matches)."
            ),
        }
    return {
        "verdict": FIT_DOES_NOT_FIT,
        # Low on purpose: §16.2 grades name-derived subject low–medium, so a
        # non-overlap is a weak disqualifier and must not read as a strong one.
        "confidence": 25,
        "sought_terms": sought,
        "explanation": (
            f"No candidate subject term overlaps the sought "
            f"{', '.join(sought)}. A weak disqualifier: the held terms come "
            f"from names and comments, so a database that holds the subject "
            f"under unfamiliar names looks like this too."
        ),
    }


def _fit_coverage(coverage: dict, lens: dict) -> dict:
    start = str(lens.get(LENS_COVERAGE_START) or "").strip()
    end = str(lens.get(LENS_COVERAGE_END) or "").strip()
    if not start and not end:
        return {"verdict": FIT_INPUT_NO_CRITERION, "confidence": 0,
                "explanation": "The lens declares no time window."}

    temporal = (coverage or {}).get("temporal") or {}
    reason = temporal.get("reason")
    if reason == COVERAGE_REASON_NO_DATE_COLUMNS:
        return {
            "verdict": FIT_DOES_NOT_FIT,
            # A measured negative about the schema, so this one IS strong.
            "confidence": 90,
            "reason": reason,
            "explanation": (
                "The lens asks for a time window and this database has no "
                "date or timestamp column at all (measured, not missing) — so "
                "it cannot cover any window."
            ),
        }
    if temporal.get("state") != STATE_MEASURED:
        return {
            "verdict": FIT_COULD_NOT_CHECK,
            "confidence": 0,
            "reason": reason or "temporal_not_established",
            "remedy": temporal.get("remedy") or COVERAGE_ANALYZE_REMEDY,
            "explanation": (
                "The period this database covers is NOT established "
                f"({reason or 'unknown reason'}), so it cannot be compared "
                "with the lens's window. This is not 'does not fit' and not "
                "'no requirement' — it is unknown. "
                + (temporal.get("remedy") or COVERAGE_ANALYZE_REMEDY)
            ),
        }

    held_start = str(temporal.get("dataCoverageStartTime") or "")
    held_end = str(temporal.get("dataCoverageEndTime") or "")
    # String comparison, which is correct for ISO-8601 and is what the stored
    # bounds are. A non-ISO bound would compare wrongly rather than raise, so
    # the basis is carried through for a reader to dispute.
    overlaps = not ((end and held_start and held_start > end)
                    or (start and held_end and held_end < start))
    confidence = temporal.get("confidence") or 50
    if overlaps:
        return {
            "verdict": FIT_FITS,
            "confidence": confidence,
            "held_window": [held_start, held_end],
            "sought_window": [start, end],
            "basis": temporal.get("basis"),
            "explanation": (
                f"Held coverage {held_start} … {held_end} overlaps the sought "
                f"{start or '(open)'} … {end or '(open)'}. Overlap only — "
                f"whether the window is COVERED without gaps needs "
                f"`coverage_profile` (Analysis tier)."
            ),
        }
    return {
        "verdict": FIT_DOES_NOT_FIT,
        "confidence": confidence,
        "held_window": [held_start, held_end],
        "sought_window": [start, end],
        "basis": temporal.get("basis"),
        "explanation": (
            f"Held coverage {held_start} … {held_end} does not overlap the "
            f"sought {start or '(open)'} … {end or '(open)'} at all."
        ),
    }


def _fit_grain(grain: dict, lens: dict) -> dict:
    sought = str(lens.get(LENS_INTERVAL) or "").strip().lower()
    if not sought:
        return {"verdict": FIT_INPUT_NO_CRITERION, "confidence": 0,
                "explanation": "The lens declares no time interval."}
    if sought not in INTERVAL_RANK:
        return {
            "verdict": FIT_COULD_NOT_CHECK,
            "confidence": 0,
            "reason": "unknown_sought_interval",
            "explanation": (
                f"The lens asks for interval {sought!r}, which is not one of "
                f"the intervals this tier derives ({', '.join(sorted(INTERVAL_RANK))}). "
                f"Not compared rather than guessed at."
            ),
        }
    if (grain or {}).get("state") != STATE_MEASURED:
        return {
            "verdict": FIT_COULD_NOT_CHECK,
            "confidence": 0,
            "reason": (grain or {}).get("reason") or "grain_not_established",
            "explanation": (
                "`grain_determination` is not established for this database, "
                "so no time grain can be compared with the lens's interval."
            ),
        }
    timed = [g for g in (grain.get("grains") or []) if g.get("interval")]
    if not timed:
        return {
            "verdict": FIT_COULD_NOT_CHECK,
            "confidence": 0,
            "reason": "no_interval_derived",
            "explanation": (
                f"No table in this database carries a time grain derivable "
                f"from naming or from a date column in its key, so there is "
                f"nothing to compare with the sought {sought!r}. An absence "
                f"of naming signal, NOT a finding that the data is not "
                f"periodic — a measured cadence is `coverage_profile` "
                f"(Analysis tier)."
            ),
        }
    sought_rank = INTERVAL_RANK[sought]
    compatible = [
        g for g in timed
        if INTERVAL_RANK.get(g["interval"], 99) <= sought_rank
    ]
    if compatible:
        best = max(compatible, key=lambda g: g.get("interval_confidence") or 0)
        return {
            "verdict": FIT_FITS,
            "confidence": best.get("interval_confidence") or 0,
            "sought_interval": sought,
            "compatible_tables": [g["qualified_name"] for g in compatible][:20],
            "compatible_count": len(compatible),
            "held_intervals": sorted({g["interval"] for g in timed}),
            "explanation": (
                f"{len(compatible)} of {len(timed)} table(s) with a derivable "
                f"time grain are recorded at {sought!r} or finer (strongest: "
                f"{best['qualified_name']} at {best['interval']!r}, basis "
                f"{best.get('interval_basis')}, confidence "
                f"{best.get('interval_confidence')}). Finer serves coarser: "
                f"per-day data can answer a monthly requirement."
            ),
        }
    return {
        "verdict": FIT_DOES_NOT_FIT,
        "confidence": max(g.get("interval_confidence") or 0 for g in timed),
        "sought_interval": sought,
        "held_intervals": sorted({g["interval"] for g in timed}),
        "explanation": (
            f"Every table with a derivable time grain is COARSER than the "
            f"sought {sought!r} (held: "
            f"{', '.join(sorted({g['interval'] for g in timed}))}), and a "
            f"coarser grain cannot be disaggregated into a finer one."
        ),
    }


def compute_preliminary_fit(
    subject: dict,
    coverage: dict,
    grain: dict,
    lens: dict | None = None,
) -> dict:
    """The Discovery gate: could this be in scope, from the free estimates?

    Zero fetch — a pure function of the three Scouting payloads and a lens
    mapping, which is what makes §16.5 point 2's cheap refinement possible:
    changing the lens recomputes fit over every already-surveyed resource at
    no fetch cost.

    Five verdicts, and the third and fourth are the ones this exists to get
    right — REPLY-COPY-REVIEW-CREDENTIAL-AND-FIT-LANGUAGE.md §4 groups them
    into three different KINDS of statement, not five flavours of one:

    - `fits` — every criterion the lens declares and this tier can check
      does. **About the resource.**
    - `does_not_fit` — at least one checkable criterion definitely fails.
      **About the resource.**
    - `could_not_check` — no criterion fails, and at least one NAMED input
      this lens needed could not be evaluated because it was not established.
      §16.2's ANALYZE case lands here, and it must NEVER render as either of
      the two above. **About our measurement for THIS analysis.**
    - `nothing_measured` — NONE of the three Scouting estimates is
      established at all (a never-surveyed database): not "this lens'
      inputs", but nothing about the resource. A different absence from
      `could_not_check`, and a different remedy (run a survey, not tweak the
      lens or run ANALYZE). **Also about our measurement, but of the whole
      resource rather than named inputs.**
    - `no_requirement_declared` — no lens, or a lens declaring nothing
      comparable at this tier. Carries `achievable` instead of a pass.
      **About the lens**, not the resource and not our measurement — see the
      branch below and `_fit_annotations`' docstring for why that distinction
      has to survive into the rendered sentence, not just the data.
    """
    normalised = normalise_lens(lens)
    achievable = _achievable(subject, coverage, grain)

    # Nothing has been surveyed at all: none of the three estimates exists, so
    # there is no gate to run. STATE_NOT_MEASURED rather than a verdict,
    # because `_db_derived_field_reader` normalises that to `{}` and the card
    # disappears — which is right. A never-surveyed database rendering
    # "Preliminary fit: no requirement declared" would be an answer about a
    # resource nobody has looked at, and "no requirement declared" is a
    # statement about the LENS, not about the resource. The verdict field is
    # `nothing_measured`, its own verdict rather than `could_not_check`
    # (REPLY-COPY-REVIEW-CREDENTIAL-AND-FIT-LANGUAGE.md §4) — a never-surveyed
    # resource and a lens whose named inputs weren't measured are different
    # absences with different remedies, and neither reads as a fit or a miss.
    nothing_established = not (
        (subject or {}).get("state") == STATE_MEASURED
        or (coverage or {}).get("state") == STATE_MEASURED
        or (grain or {}).get("state") == STATE_MEASURED
    )
    if nothing_established:
        return {
            "state": STATE_NOT_MEASURED,
            "verdict": FIT_NOTHING_MEASURED,
            "reason": "no_estimates_established",
            "label": "unverified",
            "confidence": 0,
            "lens_declared": bool(normalised),
            "lens_source": str((normalised or {}).get("lens_source") or ""),
            "lens_version": str((normalised or {}).get("lens_version") or ""),
            "inputs": {},
            "failed_inputs": [],
            "unchecked_inputs": [],
            "achievable": achievable,
            "deferred_criteria": [],
            "unused_criteria": [],
            "explanation": (
                "None of the three Scouting estimates this gate compares "
                "(subject, coverage, time grain) is established for this "
                "database, so there is nothing to compare against any "
                "requirement. NOT a fit, NOT a miss, and NOT 'no requirement "
                "declared' — that would be a statement about the lens, and "
                "this is a statement about the resource: it has not been "
                "surveyed. Run the schema step."
            ),
        }

    deferred = []
    unused = []
    if normalised:
        for key in sorted(normalised):
            if key in _DEFERRED_CRITERIA:
                analysis, why = _DEFERRED_CRITERIA[key]
                deferred.append({"criterion": key, "answered_by": analysis,
                                 "explanation": why})
            elif key not in {LENS_SUBJECT_TERMS, LENS_INTERVAL,
                             LENS_COVERAGE_START, LENS_COVERAGE_END,
                             "lens_version", "lens_source", "name",
                             "qualified_name", "description"}:
                unused.append(key)

    if not normalised:
        return {
            "state": STATE_MEASURED,
            "verdict": FIT_NO_REQUIREMENT,
            "label": "unverified",
            "confidence": 0,
            "lens_declared": False,
            "lens_source": "",
            "lens_version": "",
            "inputs": {},
            "failed_inputs": [],
            "unchecked_inputs": [],
            "achievable": achievable,
            "deferred_criteria": [],
            "unused_criteria": [],
            "explanation": (
                "NO REQUIREMENT DECLARED — no lens was supplied, so fit is "
                "not a question that has an answer here. This is NOT a pass "
                "and NOT a failure. `achievable` states what this resource "
                "could satisfy (§16.5 point 2), which is the form the same "
                "question takes as a comparison across resources: supply "
                "subject terms, an interval or a time window as an ad-hoc "
                "lens and this becomes a real verdict at no fetch cost."
            ),
        }

    inputs = {
        "subject": _fit_subject(subject, normalised),
        "coverage": _fit_coverage(coverage, normalised),
        "grain": _fit_grain(grain, normalised),
    }

    failed = [k for k, v in inputs.items() if v["verdict"] == FIT_DOES_NOT_FIT]
    unchecked = [k for k, v in inputs.items() if v["verdict"] == FIT_COULD_NOT_CHECK]
    passed = [k for k, v in inputs.items() if v["verdict"] == FIT_FITS]
    no_criterion = [k for k, v in inputs.items()
                    if v["verdict"] == FIT_INPUT_NO_CRITERION]

    if failed:
        verdict, label = FIT_DOES_NOT_FIT, "gap"
        contributing = failed
        summary = (
            f"DOES NOT FIT: {', '.join(failed)} definitely fail(s) against "
            f"this lens."
        )
    elif unchecked:
        # The whole reason this state exists. A gap in an input is not a
        # negative verdict and is not a vacuous pass.
        verdict, label = FIT_COULD_NOT_CHECK, "unverified"
        contributing = unchecked
        summary = (
            f"COULD NOT CHECK: nothing contradicts this lens, and "
            f"{', '.join(unchecked)} could not be evaluated because the "
            f"underlying estimate is not established. Treat as UNKNOWN, not "
            f"as a fit and not as a miss."
        )
    elif passed:
        verdict, label = FIT_FITS, "pass"
        contributing = passed
        summary = (
            f"FITS on {', '.join(passed)} — worth the full pass. Every "
            f"criterion this lens declares and this tier can check is "
            f"satisfied."
        )
    else:
        # A lens that declares only deferred criteria (regions, data classes,
        # a grain statement). Not a pass: nothing was compared.
        verdict, label = FIT_NO_REQUIREMENT, "unverified"
        contributing = []
        summary = (
            "NO REQUIREMENT DECLARED at this tier: the lens declares only "
            "criteria this zero-fetch tier cannot compare "
            f"({', '.join(d['criterion'] for d in deferred) or 'none'}), so "
            "no verdict was reached. Not a pass."
        )

    confidence = (
        min(inputs[k].get("confidence") or 0 for k in contributing)
        if contributing else 0
    )

    caveats = []
    # The remedy travels up to the verdict rather than staying buried in the
    # input, so the person reading the gate is told what would answer it —
    # "could not check" without "run ANALYZE" is an unactionable verdict.
    remedies = [
        inputs[k]["remedy"] for k in unchecked if inputs[k].get("remedy")
    ]
    if remedies:
        caveats.append(" ".join(dict.fromkeys(remedies)))
    if unchecked and verdict != FIT_COULD_NOT_CHECK:
        caveats.append(
            f"{', '.join(unchecked)} could not be checked, so this verdict "
            f"rests on the rest."
        )
    if no_criterion:
        caveats.append(
            f"The lens declares nothing for {', '.join(no_criterion)}."
        )
    if deferred:
        caveats.append(
            "Deferred to a later tier: "
            + "; ".join(f"{d['criterion']} → {d['answered_by']}" for d in deferred)
            + "."
        )
    if unused:
        caveats.append(
            f"The lens carries {', '.join(unused)}, which nothing at this "
            f"tier estimates — carried through unevaluated rather than "
            f"dropped."
        )

    return {
        "state": STATE_MEASURED,
        "verdict": verdict,
        "label": label,
        "confidence": confidence,
        "lens_declared": True,
        "lens_source": str(normalised.get("lens_source") or "ad_hoc"),
        # §16.5 point 4: a verdict is only meaningful against the lens version
        # it used. Empty for an ad-hoc lens, which is itself the honest answer.
        "lens_version": str(normalised.get("lens_version") or ""),
        "lens": normalised,
        "inputs": inputs,
        "failed_inputs": failed,
        "unchecked_inputs": unchecked,
        "passed_inputs": passed,
        "deferred_criteria": deferred,
        "unused_criteria": unused,
        "achievable": achievable,
        "explanation": " ".join([summary, *caveats]),
    }


def _achievable(subject: dict, coverage: dict, grain: dict) -> dict:
    """What this resource could satisfy, lens or no lens (§16.5 point 2).

    The descriptive half that *"comes first and stands alone"* (§16.5 point 1):
    it is computed identically whether a lens exists, so the no-lens rendering
    is the same data as the verdict's, not a stub.
    """
    temporal = (coverage or {}).get("temporal") or {}
    window = (
        [temporal.get("dataCoverageStartTime"), temporal.get("dataCoverageEndTime")]
        if temporal.get("state") == STATE_MEASURED else None
    )
    all_terms = [t["term"] for t in ((subject or {}).get("terms") or [])]
    intervals = (grain or {}).get("intervals") or []

    if all_terms:
        subject_clause = f"subject terms {', '.join(all_terms[:6])}"
    else:
        subject_clause = "no subject terms derivable"
    if window:
        coverage_clause = f"coverage {window[0]} … {window[1]}"
    else:
        coverage_clause = (
            f"coverage not established ({temporal.get('reason') or 'unknown'})"
        )
    if intervals:
        grain_clause = f"time grain(s) {', '.join(intervals)}"
    else:
        grain_clause = "no time grain derivable"

    return {
        "subject_terms": all_terms[:12],
        "subject_state": (subject or {}).get("state"),
        "coverage_window": window,
        "coverage_state": temporal.get("state"),
        "coverage_reason": temporal.get("reason"),
        "intervals": intervals,
        "grain_state": (grain or {}).get("state"),
        "explanation": (
            "What this resource could satisfy, from the free Scouting "
            f"estimates: {subject_clause}; {coverage_clause}; {grain_clause}."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 9. The aggregation grain  (REPLY-SCHEMA-AS-SUB-RESOURCE.md shape 1, §1/§5)
# ═══════════════════════════════════════════════════════════════════════════
#
# Every check above answers for the whole database. REPLY §1: that is the wrong
# grain — *"'Edge count 0, component count 3' for `coco_pharma` is correct for
# three single-table schemas and meaningless as a statement about the
# database"*. So each of the four structural checks gains a per-container
# breakdown and a rollup that is LABELLED as a rollup, computed by running the
# same pure check function over container-scoped inputs. Nothing about the
# checks themselves changes; only what they are handed.
#
# §5's correction is why none of this says "schema": the container level comes
# from the engine's `ContainmentLevel` declaration
# (`connection.containment_for_engine`), and an engine with no declaration gets
# whole-database output labelled as undeclared rather than Postgres's
# hierarchy applied to it.
#
# `grain_determination` is untouched, per §1's own table ("already per table;
# nothing to do") — confirmed by reading it: every entry it emits already
# carries `schema_name`, `table_name` and `qualified_name`. It gains only the
# declarative marker below, so the fact that it was checked is visible rather
# than inferred from silence.


def containment_for_database(registry, slug: str):
    """The containment declaration for this database's engine.

    Reads `DatabaseEntity.db_type` from the registry — no connection, which is
    the point: this module must keep working for a database whose credentials
    are gone or whose server is down.
    """
    from .connection import NO_CONTAINMENT, containment_for_engine

    try:
        entity = registry.get_database(slug)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("db_derived: could not read engine for %s: %s", slug, exc)
        return NO_CONTAINMENT
    return containment_for_engine(getattr(entity, "db_type", None) if entity else None)


def scope_inputs(inputs: DerivedInputs, container: str) -> DerivedInputs:
    """`inputs`, restricted to one container. REPLY §1's `WHERE` clause."""
    return DerivedInputs(
        slug=inputs.slug,
        surveyed_at=inputs.surveyed_at,
        source=inputs.source,
        schemas=schema_scope.rows_in_container(inputs.schemas, container),
        tables=schema_scope.rows_in_container(inputs.tables, container),
        columns=schema_scope.rows_in_container(inputs.columns, container),
        profiles=schema_scope.rows_in_container(inputs.profiles, container),
        activity=schema_scope.rows_in_container(inputs.activity, container),
    )


def _containers(inputs: DerivedInputs, containment) -> list[str]:
    return schema_scope.containers_in_rows(
        containment, inputs.schemas, inputs.tables, inputs.columns,
    )


def _excluded(inputs: DerivedInputs, containment) -> list[str]:
    return schema_scope.system_containers_in_rows(
        containment, inputs.schemas, inputs.tables, inputs.columns,
    )


# ── db_fingerprint, per container ───────────────────────────────────────────
#
# §1's table calls this the important one: *"the list of schema signatures —
# this is what will show that `coco_pharma.coco_ods` is a copy of the
# `coco_ods` database, the finding the whole-database version buries"*.

def _relative_signature(scoped: DerivedInputs) -> tuple[set[str], set[str]]:
    """A container's signature with the container's own name stripped out.

    `_signature` above qualifies every entry with `schema_name`, which is
    correct for comparing two whole databases and fatally wrong for comparing
    two containers: `coco_pharma.coco_ods` and the `coco_ods` database's
    `public` share every table and column and would score a Jaccard of 0.0,
    because every single entry differs in its prefix. Container-relative
    signatures are what make the buried finding surface.
    """
    table_sig = {
        t.get("table_name") or "" for t in scoped.tables if _is_base_table(t)
    }
    column_sig = {
        f"{c.get('table_name') or ''}.{c.get('column_name') or ''}:"
        f"{(c.get('base_type') or c.get('data_type') or '').lower()}"
        for c in scoped.columns
    }
    return table_sig - {""}, column_sig


def _signature_verdict(
    column_sig: set[str], peer_sig: set[str],
) -> tuple[str, dict] | None:
    """The same four thresholds `fingerprint_database` uses, applied to two
    container-relative signatures.

    Reusing `_COPY_JACCARD`/`_SUBSET_CONTAINMENT`/`_RELATED_JACCARD`/
    `_REPORTABLE_JACCARD` rather than picking new numbers is a judgement call
    the REPLY left open — it names the finding to surface but no threshold. One
    set of thresholds for "are these two collections of tables the same thing"
    means a reader disputing a container-level verdict reads the same published
    line as for a database-level one; a second, container-only set would be a
    number nobody could compare against anything.
    """
    col_jaccard = _jaccard(column_sig, peer_sig)
    containment_ratio = _ratio(len(column_sig & peer_sig), len(column_sig))
    reverse = _ratio(len(column_sig & peer_sig), len(peer_sig))
    if col_jaccard >= _COPY_JACCARD:
        verdict = "likely_copy"
    elif containment_ratio >= _SUBSET_CONTAINMENT and len(peer_sig) > len(column_sig):
        verdict = "likely_subset_of"
    elif reverse >= _SUBSET_CONTAINMENT and len(column_sig) > len(peer_sig):
        verdict = "likely_superset_of"
    elif col_jaccard >= _RELATED_JACCARD:
        verdict = "shares_structure"
    elif col_jaccard >= _REPORTABLE_JACCARD:
        verdict = "incidental_overlap"
    else:
        return None
    return verdict, {
        "column_jaccard": round(col_jaccard, 4),
        "containment": round(containment_ratio, 4),
        "reverse_containment": round(reverse, 4),
        "shared_columns": len(column_sig & peer_sig),
        "peer_columns": len(peer_sig),
    }


def fingerprint_by_container(registry, inputs: DerivedInputs, containment) -> dict:
    """Per-container signatures, plus every container-to-container match —
    inside this database and against every other database's containers.

    Both directions matter and they are reported separately: two containers of
    the SAME database with matching signatures is a copy inside one database
    (the case §1's rollup exists to stop averaging away), while a container
    matching another DATABASE's container is `coco_pharma.coco_ods` vs the
    `coco_ods` database.

    Zero-fetch, like everything else here: peers' signatures come from their
    own stored rows. It walks the peer list a second time (after
    `fingerprint_database`) rather than threading container state through that
    function, which is a real cost in registry reads and no fetch at all —
    worth it to leave the whole-database check exactly as it was.
    """
    containers = _containers(inputs, containment)
    signatures: dict[str, tuple[set[str], set[str]]] = {}
    per_container: dict[str, dict] = {}
    for name in containers:
        scoped = scope_inputs(inputs, name)
        tsig, csig = _relative_signature(scoped)
        signatures[name] = (tsig, csig)
        per_container[name] = {
            "state": STATE_MEASURED if csig or tsig else STATE_NOT_MEASURED,
            "container": name,
            "digest": hashlib.sha256(
                "\n".join(sorted(csig)).encode("utf-8")
            ).hexdigest(),
            "table_count": len(tsig),
            "column_count": len(csig),
            "matches": [],
        }

    # Inside this database.
    for i, name in enumerate(containers):
        for other in containers[i + 1:]:
            verdict = _signature_verdict(signatures[name][1], signatures[other][1])
            if not verdict:
                continue
            kind, metrics = verdict
            per_container[name]["matches"].append({
                "target_slug": inputs.slug, "target_container": other,
                "same_database": True, "verdict": kind, **metrics,
            })
            reverse_kind = {
                "likely_subset_of": "likely_superset_of",
                "likely_superset_of": "likely_subset_of",
            }.get(kind, kind)
            per_container[other]["matches"].append({
                "target_slug": inputs.slug, "target_container": name,
                "same_database": True, "verdict": reverse_kind,
                "column_jaccard": metrics["column_jaccard"],
                "containment": metrics["reverse_containment"],
                "reverse_containment": metrics["containment"],
                "shared_columns": metrics["shared_columns"],
                "peer_columns": len(signatures[name][1]),
            })

    # Against every other database's containers.
    comparable_peers = 0
    undeclared_peers: list[str] = []
    try:
        peers = registry.list_databases()
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("db_derived: could not list databases for container fingerprint: %s", exc)
        peers = []
    for peer in peers:
        peer_slug = getattr(peer, "slug", None) or (
            peer.get("slug") if isinstance(peer, dict) else None
        )
        if not peer_slug or peer_slug == inputs.slug:
            continue
        peer_containment = containment_for_database(registry, peer_slug)
        if peer_containment.aggregation_grain is None:
            # Its engine declares no containment level, so it has no containers
            # to compare against. Named, not silently skipped: "not comparable
            # at this grain" is not "no match".
            undeclared_peers.append(peer_slug)
            continue
        peer_inputs = load_inputs(registry, peer_slug)
        peer_containers = _containers(peer_inputs, peer_containment)
        if not peer_containers:
            continue
        comparable_peers += 1
        for peer_container in peer_containers:
            _, peer_csig = _relative_signature(
                scope_inputs(peer_inputs, peer_container)
            )
            if not peer_csig:
                continue
            for name in containers:
                verdict = _signature_verdict(signatures[name][1], peer_csig)
                if not verdict:
                    continue
                kind, metrics = verdict
                per_container[name]["matches"].append({
                    "target_slug": peer_slug, "target_container": peer_container,
                    "same_database": False, "verdict": kind, **metrics,
                })

    for entry in per_container.values():
        entry["matches"].sort(key=lambda m: m["column_jaccard"], reverse=True)
        entry["best_similarity"] = (
            entry["matches"][0]["column_jaccard"] if entry["matches"] else None
        )

    cross = [
        {"container": name, **match}
        for name, entry in per_container.items()
        for match in entry["matches"]
        if match["verdict"] in ("likely_copy", "likely_subset_of", "likely_superset_of")
    ]
    cross.sort(key=lambda m: m["column_jaccard"], reverse=True)

    grain_name = containment.aggregation_grain.name
    if cross:
        lead = cross[0]
        where = (
            f"{lead['target_container']} in this same database"
            if lead["same_database"]
            else f"{lead['target_container']} in {lead['target_slug']}"
        )
        explanation = (
            f"Per-{grain_name} signatures, listed rather than averaged into one "
            f"digest. {len(cross)} copy/subset relationship(s) surfaced that a "
            f"single whole-database signature buries — closest: "
            f"{lead['container']} is a {lead['verdict']} of {where} "
            f"(container-relative column Jaccard {lead['column_jaccard']:.2f}). "
            f"Thresholds: copy ≥ {_COPY_JACCARD}, subset containment ≥ "
            f"{_SUBSET_CONTAINMENT}, related ≥ {_RELATED_JACCARD}."
        )
    else:
        explanation = (
            f"Per-{grain_name} signatures, listed rather than averaged into one "
            f"digest. Compared every {grain_name} against every other "
            f"{grain_name} in this database and against {comparable_peers} "
            f"other database(s) with stored rows; none crossed the copy "
            f"(≥ {_COPY_JACCARD}) or subset (≥ {_SUBSET_CONTAINMENT}) line."
        )

    return {
        f"by_{grain_name}": per_container,
        "aggregation": schema_scope.rollup_envelope(
            containment,
            rollup_kind="list_of_signatures",
            containers=containers,
            excluded_system_containers=_excluded(inputs, containment),
            explanation=explanation,
            signatures=[
                {
                    "container": name,
                    "digest": per_container[name]["digest"],
                    "table_count": per_container[name]["table_count"],
                    "column_count": per_container[name]["column_count"],
                }
                for name in containers
            ],
            cross_container_matches=cross,
            comparable_peer_databases=comparable_peers,
            peers_without_declared_containment=undeclared_peers,
        ),
    }


# ── db_classification, per container ────────────────────────────────────────

def classify_by_container(
    inputs: DerivedInputs, containment, fingerprints: dict,
) -> dict:
    """Each container's kind, and the SET of kinds present as the rollup.

    §1: *"the set of kinds present, with counts"* — never one averaged verdict,
    because a database that is legitimately several kinds at once has no single
    right answer and picking one is a confident wrong answer.
    """
    grain_name = containment.aggregation_grain.name
    containers = _containers(inputs, containment)
    per_container: dict[str, dict] = {}
    for name in containers:
        scoped = scope_inputs(inputs, name)
        per_container[name] = classify_database(
            scoped, fingerprints.get(name) or {"state": STATE_NOT_MEASURED},
        )

    kinds: dict[str, int] = {}
    undecided: list[str] = []
    not_measured: list[str] = []
    for name, result in per_container.items():
        if result.get("state") != STATE_MEASURED:
            not_measured.append(name)
        elif result.get("kind"):
            kinds[result["kind"]] = kinds.get(result["kind"], 0) + 1
        else:
            undecided.append(name)

    if kinds:
        spread = ", ".join(
            f"{count} {kind}" for kind, count in sorted(kinds.items())
        )
        explanation = (
            f"A rollup, not a verdict: across {len(containers)} {grain_name}(s) "
            f"the kinds present are {spread}"
            + (f"; {len(undecided)} too close to call" if undecided else "")
            + (f"; {len(not_measured)} with insufficient signal" if not_measured else "")
            + ". No single kind describes the database when more than one is "
              "listed here — that is the finding, not a gap."
        )
    else:
        explanation = (
            f"A rollup, not a verdict: no {grain_name} in this database "
            f"reached a confident classification "
            f"({len(undecided)} too close to call, {len(not_measured)} with "
            f"insufficient signal). Not a finding that the database is of no "
            f"particular kind."
        )

    return {
        f"by_{grain_name}": per_container,
        "aggregation": schema_scope.rollup_envelope(
            containment,
            rollup_kind="set_of_kinds",
            containers=containers,
            excluded_system_containers=_excluded(inputs, containment),
            explanation=explanation,
            kinds=kinds,
            undecided_containers=sorted(undecided),
            not_measured_containers=sorted(not_measured),
        ),
    }


# ── db_relationship_graph, per container ────────────────────────────────────

def relationship_graph_by_container(inputs: DerivedInputs, containment) -> dict:
    """Components and FK density WITHIN each container, plus cross-container FK
    edges as a database-level fact in their own right.

    §1: *"per-schema summaries **plus cross-schema FK edges reported as a
    database-level fact in their own right**"* — explicitly not folded into
    either container's count. An edge from `sales.order` to `core.customer`
    belongs to neither schema's internal model; counting it in `sales` would
    overstate that schema's connectedness and counting it in both would
    double-count it.

    Handed the scoped rows unchanged, the check would count such an edge in
    the container it leaves from (it is a column of that container's table)
    and then report it as a `dangling_reference`, because the target table is
    not in scope. Both are wrong for a reader: the edge is not part of that
    container's internal model, and it is not dangling — it resolves in
    another container of the same database. So the crossing columns are
    withheld from the scoped run and reported separately, as
    `cross_container_references` per container and as the rollup's
    `cross_container_edges` for the database.
    """
    grain_name = containment.aggregation_grain.name
    containers = _containers(inputs, containment)

    per_container: dict[str, dict] = {}
    for name in containers:
        scoped = scope_inputs(inputs, name)
        internal_columns: list[dict] = []
        crossing: list[dict] = []
        for col in scoped.columns:
            fk = col.get("foreign_key_json")
            target_schema = (
                (fk.get("foreign_schema") or name) if isinstance(fk, dict) else name
            )
            if isinstance(fk, dict) and fk.get("foreign_table") and target_schema != name:
                crossing.append({
                    "from_schema": name,
                    "from_table": col.get("table_name") or "",
                    "from_column": col.get("column_name") or "",
                    "to_schema": target_schema,
                    "to_table": fk.get("foreign_table") or "",
                    "to_column": fk.get("foreign_column") or "",
                })
                stripped = dict(col)
                stripped["foreign_key_json"] = None
                internal_columns.append(stripped)
            else:
                internal_columns.append(col)
        result = derive_relationship_graph(
            DerivedInputs(
                slug=scoped.slug, surveyed_at=scoped.surveyed_at,
                source=scoped.source, schemas=scoped.schemas,
                tables=scoped.tables, columns=internal_columns,
                profiles=scoped.profiles, activity=scoped.activity,
            )
        )
        result["cross_container_references"] = crossing
        result["container"] = name
        if crossing:
            # Withholding the crossing columns makes the scoped run's own
            # "not one declares a foreign key" sentence false for a table that
            # declares one pointing elsewhere. Restated rather than left to
            # read as a measured negative it is not.
            base = result.get("explanation", "")
            if result.get("verdict") == "bag_of_tables":
                base = (
                    f"Measured: no foreign key relates {name}'s own "
                    f"{result.get('table_count')} table(s) to each other."
                )
            result["explanation"] = (
                f"{base} {len(crossing)} foreign key(s) leave {name} for "
                f"another {grain_name}; they are reported as a database-level "
                f"fact and are NOT counted in {name}'s own edge count."
            )
        per_container[name] = result

    cross_edges = [
        edge for edge in _foreign_key_edges(inputs.columns)
        if (edge["from_schema"] != edge["to_schema"]
            and not containment.is_system_container(edge["from_schema"])
            and not containment.is_system_container(edge["to_schema"]))
    ]
    pairs: dict[str, int] = {}
    for edge in cross_edges:
        key = f"{edge['from_schema']} → {edge['to_schema']}"
        pairs[key] = pairs.get(key, 0) + 1

    summaries = {
        name: {
            "verdict": result.get("verdict"),
            "state": result.get("state"),
            "table_count": result.get("table_count"),
            "edge_count": result.get("edge_count"),
            "component_count": result.get("component_count"),
            "largest_component": result.get("largest_component"),
            "connected_tables": result.get("connected_tables"),
            "cross_container_reference_count": len(
                result.get("cross_container_references") or []
            ),
        }
        for name, result in per_container.items()
    }

    verdicts = sorted({
        s["verdict"] for s in summaries.values() if s.get("verdict")
    })
    explanation = (
        f"A rollup of {len(containers)} {grain_name}-level graph(s), not one "
        f"database-wide graph"
        + (f" — verdicts present: {', '.join(verdicts)}." if verdicts else ".")
        + (
            f" {len(cross_edges)} foreign key(s) cross a {grain_name} boundary "
            f"({', '.join(f'{k} ×{v}' for k, v in sorted(pairs.items()))}); "
            f"they are a database-level fact and are counted in NO "
            f"{grain_name}'s own edge count."
            if cross_edges else
            f" No foreign key crosses a {grain_name} boundary — measured, and a "
            f"real finding about how independent these {grain_name}s are."
        )
    )

    return {
        f"by_{grain_name}": per_container,
        "aggregation": schema_scope.rollup_envelope(
            containment,
            rollup_kind="per_container_summaries_plus_cross_edges",
            containers=containers,
            excluded_system_containers=_excluded(inputs, containment),
            explanation=explanation,
            summaries=summaries,
            cross_container_edges=cross_edges,
            cross_container_edge_count=len(cross_edges),
            cross_container_pairs=pairs,
        ),
    }


# ── schema_conventions, per container ───────────────────────────────────────

def conventions_by_container(inputs: DerivedInputs, containment) -> dict:
    """Each container's checks and ratios; the rollup names the spread.

    §1: *"totals, with the per-schema spread"*. The totals are the existing
    whole-database `checks` block, left exactly as it was; what is added is the
    per-container breakdown and, per check, which containers carry the gaps —
    "4 of 23 tables in `coco_ods`, 0 of 1 in `eu_sales`" rather than "4 of 26".
    """
    grain_name = containment.aggregation_grain.name
    containers = _containers(inputs, containment)
    per_container = {
        name: check_conventions(scope_inputs(inputs, name)) for name in containers
    }

    spread: dict[str, list[dict]] = {}
    for name, result in per_container.items():
        for check, payload in (result.get("checks") or {}).items():
            spread.setdefault(check, []).append({
                "container": name,
                "state": payload.get("state"),
                "count": payload.get("count"),
                "total": payload.get("total"),
                "fraction": payload.get("fraction"),
                "label": payload.get("label"),
            })
    for entries in spread.values():
        entries.sort(key=lambda e: e["container"])

    gapped = sorted({
        f"{entry['container']} ({check} {entry['count']}/{entry['total']})"
        for check, entries in spread.items()
        for entry in entries
        if entry.get("label") == "gap" and entry.get("count")
    })
    explanation = (
        f"A rollup: the whole-database `checks` block above is the total, and "
        f"this names the spread across {len(containers)} {grain_name}(s)"
        + (f" — gaps in {', '.join(gapped)}." if gapped else
           f" — no {grain_name} carries a gap on any check.")
    )

    return {
        f"by_{grain_name}": per_container,
        "aggregation": schema_scope.rollup_envelope(
            containment,
            rollup_kind="totals_plus_spread",
            containers=containers,
            excluded_system_containers=_excluded(inputs, containment),
            explanation=explanation,
            spread=spread,
        ),
    }


def subject_signals_by_container(inputs: DerivedInputs, containment) -> dict:
    """Each container's candidate subject terms; the rollup names the union.

    #266's grain, applied to §16's subject row. A database with a `sales`
    schema and an `hr` schema has two subjects, and one flat term list reads as
    a single confused one — which is the same defect as one relationship graph
    over three unrelated schemas.
    """
    grain_name = containment.aggregation_grain.name
    containers = _containers(inputs, containment)
    per_container = {
        name: derive_subject_signals(scope_inputs(inputs, name))
        for name in containers
    }
    by_container_terms = {
        name: [t["term"] for t in (result.get("terms") or [])][:12]
        for name, result in per_container.items()
    }
    silent = sorted(
        name for name, result in per_container.items()
        if not (result.get("terms") or [])
    )
    explanation = (
        f"A rollup: the whole-database term list above is the union, and this "
        f"splits it across {len(containers)} {grain_name}(s)"
        + (f" — {', '.join(silent)} carries no subject-bearing name at all."
           if silent else ".")
    )
    return {
        f"by_{grain_name}": per_container,
        "aggregation": schema_scope.rollup_envelope(
            containment,
            rollup_kind="union_of_terms",
            containers=containers,
            excluded_system_containers=_excluded(inputs, containment),
            explanation=explanation,
            terms_by_container=by_container_terms,
        ),
    }


def coverage_signals_by_container(inputs: DerivedInputs, containment) -> dict:
    """Each container's period and places; the rollup names the widest span.

    The reason this matters more here than anywhere else: a whole-database
    window is the MIN start and MAX end across every schema, so one schema
    covering 2019 and another covering 2026 render as "2019 … 2026" — a span
    no schema actually covers. The per-container breakdown is what makes that
    visible, and `aggregation.rollup_kind` says `widest_span` so the
    whole-database figure cannot be mistaken for a per-schema one.
    """
    grain_name = containment.aggregation_grain.name
    containers = _containers(inputs, containment)
    per_container = {
        name: derive_coverage_signals(scope_inputs(inputs, name))
        for name in containers
    }
    windows = {}
    unknown = []
    for name, result in per_container.items():
        temporal = (result.get("temporal") or {})
        if temporal.get("state") == STATE_MEASURED and temporal.get("dataCoverageStartTime"):
            windows[name] = [temporal["dataCoverageStartTime"],
                             temporal["dataCoverageEndTime"]]
        else:
            unknown.append({"container": name, "reason": temporal.get("reason")})
    explanation = (
        f"A rollup over {len(containers)} {grain_name}(s): the whole-database "
        f"window above is the widest span across them, which no single "
        f"{grain_name} need cover. "
        + (f"{len(windows)} {grain_name}(s) have a window; " if windows else "")
        + (f"{len(unknown)} do not — "
           + ", ".join(f"{u['container']} ({u['reason']})" for u in unknown)
           + ". Each of those is unknown or a measured negative on its own "
             "terms; read the per-container reason rather than the total."
           if unknown else "every one of them has a window.")
    )
    return {
        f"by_{grain_name}": per_container,
        "aggregation": schema_scope.rollup_envelope(
            containment,
            rollup_kind="widest_span",
            containers=containers,
            excluded_system_containers=_excluded(inputs, containment),
            explanation=explanation,
            windows_by_container=windows,
            containers_without_window=unknown,
        ),
    }


def preliminary_fit_by_container(
    inputs: DerivedInputs, containment, lens: dict | None,
) -> dict:
    """Each container's own fit verdict; the rollup counts them by verdict.

    The gate's whole purpose is deciding whether the expensive pass is worth
    running, and the unit that pass runs on is a schema. "One of six schemas
    fits" is a materially different answer from "the database fits", and a
    whole-database verdict cannot express it.
    """
    grain_name = containment.aggregation_grain.name
    containers = _containers(inputs, containment)
    per_container = {}
    for name in containers:
        scoped = scope_inputs(inputs, name)
        per_container[name] = compute_preliminary_fit(
            derive_subject_signals(scoped),
            derive_coverage_signals(scoped),
            determine_grain(scoped),
            lens,
        )
    counts: dict[str, int] = {}
    for result in per_container.values():
        counts[result["verdict"]] = counts.get(result["verdict"], 0) + 1
    fitting = sorted(n for n, r in per_container.items() if r["verdict"] == FIT_FITS)
    explanation = (
        f"A rollup over {len(containers)} {grain_name}(s), counted by verdict "
        f"rather than averaged: "
        + ", ".join(f"{verdict}={count}" for verdict, count in sorted(counts.items()))
        + ". "
        + (f"In scope: {', '.join(fitting)}." if fitting else
           "No single " + grain_name + " fits on its own.")
    )
    return {
        f"by_{grain_name}": per_container,
        "aggregation": schema_scope.rollup_envelope(
            containment,
            rollup_kind="verdict_counts",
            containers=containers,
            excluded_system_containers=_excluded(inputs, containment),
            explanation=explanation,
            verdict_counts=counts,
            fitting_containers=fitting,
        ),
    }


def apply_container_grain(registry, inputs: DerivedInputs, derived: dict) -> dict:
    """Attach the per-container breakdown and rollup to each structural check.

    Mutates and returns `derived`. The existing whole-database fields on every
    payload are left untouched — this ADDS a grain rather than replacing one,
    so an existing consumer (the publish path, the results cards, the fact
    layer) keeps working while a new one can read `by_<level>` and
    `aggregation`.
    """
    containment = containment_for_database(registry, inputs.slug)
    grain = containment.aggregation_grain
    targets = (
        "db_classification", "db_relationship_graph",
        "db_fingerprint", "schema_conventions",
        # Added 2026-09-24 with design §16.3's Scouting/Discovery rows. Both
        # are aggregations over tables and columns, so #266's ruling applies
        # to them exactly as it applies to the four above: "a bag of subject
        # terms from six schemas" and "2019…2026 across every schema" are the
        # same kind of blurred answer "edge count 0, component count 3" was.
        # `preliminary_fit` is included because a verdict over a whole
        # database hides the case the gate exists for — one schema in scope
        # and five not.
        "subject_signals", "coverage_signals", "preliminary_fit",
    )

    if grain is None:
        envelope = schema_scope.undeclared_envelope(containment)
        for key in targets:
            derived[key]["aggregation"] = dict(envelope)
        derived["grain_determination"]["aggregation"] = dict(envelope)
        return derived

    fingerprint_block = fingerprint_by_container(registry, inputs, containment)
    derived["db_fingerprint"].update(fingerprint_block)
    per_container_fingerprints = fingerprint_block[f"by_{grain.name}"]

    derived["db_classification"].update(
        classify_by_container(inputs, containment, per_container_fingerprints)
    )
    derived["db_relationship_graph"].update(
        relationship_graph_by_container(inputs, containment)
    )
    derived["schema_conventions"].update(
        conventions_by_container(inputs, containment)
    )
    # §16's three new rows, at the same grain and by the same mechanism: run
    # the pure check function over container-scoped inputs. `preliminary_fit`
    # additionally needs the per-container grain, which `determine_grain`
    # already produces per table — scoping its inputs is what turns that into
    # a per-container verdict.
    derived["subject_signals"].update(
        subject_signals_by_container(inputs, containment)
    )
    derived["coverage_signals"].update(
        coverage_signals_by_container(inputs, containment)
    )
    derived["preliminary_fit"].update(
        preliminary_fit_by_container(
            inputs, containment, (derived["preliminary_fit"].get("lens") or None),
        )
    )
    # The screen relays `explanation` (the fact layer's "prose" rung in
    # `next/app.js`'s `readEnvelope`) and skips nested objects, so a breakdown
    # that lives only in `by_<level>` would be invisible to the reader who
    # raised this — the whole-database sentence they saw would still be the
    # whole story on screen. The rollup's own sentence is appended to it, so
    # the spread reaches the surface without any consumer change.
    for key in targets:
        payload = derived[key]
        rollup_sentence = (payload.get("aggregation") or {}).get("explanation")
        if rollup_sentence and payload.get("explanation"):
            payload["explanation"] = f"{payload['explanation']} {rollup_sentence}"
        elif rollup_sentence:
            payload["explanation"] = rollup_sentence
    # §1: "already per table; nothing to do". Confirmed by reading
    # `determine_grain` — every entry carries schema_name/table_name/
    # qualified_name already. The marker records that this was checked at the
    # declared grain and needed no breakdown, so a later reader does not have
    # to re-derive that conclusion from the absence of a `by_schema` key.
    derived["grain_determination"]["aggregation"] = {
        "is_rollup": False,
        "averaged": False,
        "grain": "table",
        "grain_level": "table",
        "engine": containment.engine,
        "container_level": grain.name,
        "explanation": (
            f"Already finer than the {grain.name} grain: every entry names its "
            f"{grain.name} and its table, so there is no rollup here to label "
            f"and nothing was averaged."
        ),
    }
    return derived


# ═══════════════════════════════════════════════════════════════════════════
# The step
# ═══════════════════════════════════════════════════════════════════════════

def run_db_derived(
    registry,
    slug: str,
    *,
    surveyed_at: str | None = None,
    source: str | None = None,
    lens: dict | None = None,
) -> dict:
    """Run every `db_derived` check over stored rows. Opens no connection.

    Returns a dict shaped like the other database steps' output: the per-check
    payloads under `derived`, plus the annotations they produced.

    `lens` is design §16.5's optional data requirement, as a plain mapping
    using §16.6's `scopeElements` key names — see `compute_preliminary_fit`.
    Omitted (the default, and what every caller in the tree passes today),
    `preliminary_fit` renders "no requirement declared" plus what this
    resource could satisfy. That is the designed degraded mode, not a stub:
    §16.6 puts `preliminary_fit` in Phase 1 explicitly *"before [the DataLens]
    exists, as a comparison across resources"*.
    """
    inputs = load_inputs(registry, slug, surveyed_at, source)

    fingerprint = fingerprint_database(registry, inputs)
    classification = classify_database(inputs, fingerprint)
    graph = derive_relationship_graph(inputs)
    grain = determine_grain(inputs)
    conventions = check_conventions(inputs)
    change_rates = derive_change_rates(registry, inputs)
    schema_diff = derive_schema_diff(registry, slug)
    grant_change = derive_grant_change(registry, slug)
    scope = propose_data_scope(inputs)
    subject = derive_subject_signals(inputs)
    coverage = derive_coverage_signals(inputs)
    fit = compute_preliminary_fit(subject, coverage, grain, lens)

    derived = {
        "db_classification": classification,
        "db_relationship_graph": graph,
        "grain_determination": grain,
        "db_fingerprint": fingerprint,
        "schema_conventions": conventions,
        "db_change_rates": change_rates,
        "schema_diff": schema_diff,
        "grant_change": grant_change,
        _PROPOSED_SCOPE_CHECK: scope,
        "subject_signals": subject,
        "coverage_signals": coverage,
        "preliminary_fit": fit,
    }

    # REPLY-SCHEMA-AS-SUB-RESOURCE.md shape 1: the four structural checks above
    # answered for the whole database; this adds the per-container breakdown
    # and the labelled rollup at the engine's declared grain. Runs after the
    # whole-database pass and never in place of it — `annotations` below is
    # still built from the same fields it always was.
    apply_container_grain(registry, inputs, derived)

    return {
        "database_slug": slug,
        "surveyed_at": datetime.utcnow().isoformat(),
        "read_snapshot": inputs.surveyed_at,
        "source": source,
        "derived": derived,
        "annotations": build_annotations(derived),
        "errors": [],
    }


def build_annotations(derived: dict) -> list:
    """Turn the per-check payloads into annotations.

    A pure function of `derived`, deliberately: it is the same split slices 7
    and 8 use (fetch/derive in one place, annotation-building in another), so
    the Survey-Definition publish path can build annotations from a stored
    payload without re-deriving anything.
    """
    annotations: list = []
    annotations.extend(_classification_annotations(derived["db_classification"]))
    annotations.extend(_graph_annotations(derived["db_relationship_graph"]))
    annotations.extend(_grain_annotations(derived["grain_determination"]))
    annotations.extend(_fingerprint_annotations(derived["db_fingerprint"]))
    annotations.extend(_conventions_annotations(derived["schema_conventions"]))
    annotations.extend(_change_rate_annotations(derived["db_change_rates"]))
    annotations.extend(_schema_diff_annotations(derived["schema_diff"]))
    annotations.extend(_grant_change_annotations(derived["grant_change"]))
    annotations.extend(_scope_annotations(derived[_PROPOSED_SCOPE_CHECK]))
    annotations.extend(_subject_annotations(derived["subject_signals"]))
    annotations.extend(_coverage_annotations(derived["coverage_signals"]))
    annotations.extend(_fit_annotations(derived["preliminary_fit"]))
    return annotations


def _classification_annotations(result: dict) -> list:
    if result["state"] != STATE_MEASURED or result.get("kind") is None:
        return [ResourceMeasureAnnotation(
            summary="Database kind not established",
            analysis_step=ANALYSIS_STEP,
            annotation_type_name="db_classification",
            check_name="db_classification",
            label="unverified",
            confidence=0,
            explanation=result["explanation"],
            resource_properties={
                "signals_used": ", ".join(result.get("signals_used") or []) or "none",
                "signals_missing": ", ".join(result.get("signals_missing") or []) or "none",
            },
            json_properties={"scores": result.get("scores") or {}},
        )]
    return [ClassificationAnnotation(
        summary=f"Database kind: {result['kind']}",
        analysis_step=ANALYSIS_STEP,
        annotation_type_name="db_classification",
        check_name="db_classification",
        label=result["kind"],
        confidence=result["confidence"],
        explanation=result["explanation"],
        candidate_classifications=list(result.get("ranked") or [result["kind"]]),
        json_properties={
            "scores": result.get("scores") or {},
            "coverage": result.get("coverage"),
            "signals_used": result.get("signals_used"),
            "signals_missing": result.get("signals_missing"),
        },
    )]


def _graph_annotations(result: dict) -> list:
    if result["state"] != STATE_MEASURED:
        return [ResourceMeasureAnnotation(
            summary="Relational model not established",
            analysis_step=ANALYSIS_STEP,
            annotation_type_name="db_relationship_graph",
            check_name="db_relationship_graph",
            label="unverified",
            confidence=0,
            explanation=result["explanation"],
            resource_properties={"reason": result.get("reason") or ""},
        )]
    return [SchemaAnalysisAnnotation(
        summary=(
            f"{result['edge_count']} foreign key(s) across "
            f"{result['table_count']} table(s): {result['verdict']}"
        ),
        analysis_step=ANALYSIS_STEP,
        annotation_type_name="db_relationship_graph",
        check_name="db_relationship_graph",
        label=result["verdict"],
        # A measured graph, not an inference — the FK constraints are declared.
        confidence=100,
        explanation=result["explanation"],
        schema_name=f"{result['table_count']} tables",
        schema_type="relational_model",
        json_properties={
            "edge_count": result["edge_count"],
            "component_count": result["component_count"],
            "largest_component": result["largest_component"],
            "isolated_tables": result["isolated_tables"],
            "most_referenced": result["most_referenced"],
            "dangling_references": result["dangling_references"],
        },
    )]


def _grain_annotations(result: dict) -> list:
    if result["state"] != STATE_MEASURED:
        return [ResourceMeasureAnnotation(
            summary="Table grain not established",
            analysis_step=ANALYSIS_STEP,
            annotation_type_name="grain_determination",
            check_name="grain_determination",
            label="unverified",
            confidence=0,
            explanation=(
                "No stored table rows for this database, so no grain could be "
                "derived for any table."
            ),
            resource_properties={"reason": result.get("reason") or ""},
        )]

    annotations: list = []
    for grain in result["grains"]:
        item_key = grain["qualified_name"]
        if grain.get("grain_statement"):
            annotations.append(DataGrainAnnotation(
                summary=f"{item_key}: {grain['grain_statement']}",
                analysis_step=ANALYSIS_STEP,
                annotation_type_name="grain_determination",
                check_name="grain_determination",
                item_key=item_key,
                label=grain["label"],
                confidence=grain["confidence"],
                explanation=grain["explanation"],
                expression=item_key,
                grain_statement=grain["grain_statement"],
                granularity_basis=grain["basis"],
                interval=grain.get("interval") or "",
                # A PROPOSAL: no DataGrain element exists for this yet, and a
                # curator must declare it (support doc §3's decision).
                content_status="DRAFT",
                json_properties={
                    "grain_columns": grain.get("grain_columns") or [],
                    "row_count": grain.get("row_count"),
                    "date_columns": grain.get("date_columns") or [],
                    # §16.3's time-grain extension: the interval alone was
                    # ambiguous between a key-declared period and a guess
                    # from a table's name.
                    "named_date_columns": grain.get("named_date_columns") or [],
                    "interval_basis": grain.get("interval_basis") or "",
                    "interval_confidence": grain.get("interval_confidence") or 0,
                    "interval_signals": grain.get("interval_signals") or {},
                    "interval_explanation": grain.get("interval_explanation") or "",
                },
            ))
        else:
            annotations.append(ResourceMeasureAnnotation(
                summary=(
                    f"{item_key}: grain "
                    + ("not determinable" if grain["state"] == STATE_MEASURED
                       else "not established")
                ),
                analysis_step=ANALYSIS_STEP,
                annotation_type_name="grain_determination",
                check_name="grain_determination",
                item_key=item_key,
                label=grain["label"],
                confidence=0,
                explanation=grain["explanation"],
                expression=item_key,
                resource_properties={
                    "state": grain["state"],
                    "row_count": grain.get("row_count"),
                },
            ))
    return annotations


def _fingerprint_annotations(result: dict) -> list:
    if result["state"] != STATE_MEASURED:
        return [ResourceMeasureAnnotation(
            summary="Schema fingerprint not computed",
            analysis_step=ANALYSIS_STEP,
            annotation_type_name="db_fingerprint",
            check_name="db_fingerprint",
            label="unverified",
            confidence=0,
            explanation=result["explanation"],
            resource_properties={"reason": result.get("reason") or ""},
        )]

    comparable = result["comparable_databases"]
    matches = result["matches"]
    top = matches[0] if matches else None
    if comparable == 0:
        label, confidence = "unverified", 0
    elif top and top["verdict"] in {"likely_copy", "likely_subset_of", "likely_superset_of"}:
        label, confidence = top["verdict"], 85
    elif top:
        label, confidence = top["verdict"], 60
    else:
        label, confidence = "no_match", 100

    return [FingerprintAnnotation(
        summary=(
            f"Schema fingerprint {result['digest'][:16]}"
            + (f" — closest match {top['slug']} ({top['verdict']})" if top
               else f" — no match among {comparable} comparable database(s)"
               if comparable else " — nothing to compare against")
        ),
        analysis_step=ANALYSIS_STEP,
        annotation_type_name="db_fingerprint",
        check_name="db_fingerprint",
        label=label,
        confidence=confidence,
        explanation=result["explanation"],
        fingerprint_properties={
            "digest": result["digest"],
            "algorithm": "sha256(sorted schema.table.column:type)",
            "tableCount": result["table_count"],
            "columnCount": result["column_count"],
            "comparableDatabases": comparable,
            "bestSimilarity": result.get("best_similarity"),
            "closestMatch": top["slug"] if top else "",
        },
        json_properties={
            "matches": matches,
            "unsurveyed_peers": result["unsurveyed_peers"],
            "thresholds": {
                "copy_jaccard": _COPY_JACCARD,
                "subset_containment": _SUBSET_CONTAINMENT,
                "related_jaccard": _RELATED_JACCARD,
                "reportable_jaccard": _REPORTABLE_JACCARD,
            },
        },
    )]


def _conventions_annotations(result: dict) -> list:
    if result["state"] != STATE_MEASURED:
        return [ResourceMeasureAnnotation(
            summary="Schema conventions not checked",
            analysis_step=ANALYSIS_STEP,
            annotation_type_name="schema_conventions",
            check_name="schema_conventions",
            label="unverified",
            confidence=0,
            explanation=(
                "No stored table rows for this database, so no structural "
                "convention could be checked."
            ),
            resource_properties={"reason": result.get("reason") or ""},
        )]

    annotations: list = []
    for name, check in result["checks"].items():
        measured = check["state"] == STATE_MEASURED
        annotations.append(ResourceMeasureAnnotation(
            summary=f"{name}: {check['label']}",
            analysis_step=ANALYSIS_STEP,
            annotation_type_name="schema_conventions",
            check_name=name,
            label=check["label"],
            # A count of stored rows is a measurement, not an inference.
            confidence=100 if measured else 0,
            explanation=check["explanation"],
            resource_properties={
                "state": check["state"],
                "count": check.get("count"),
                "total": check.get("total"),
                "fraction": check.get("fraction"),
            },
            json_properties={"items": check.get("items") or []},
        ))
    return annotations


def _change_rate_annotations(result: dict) -> list:
    if result["state"] != STATE_MEASURED:
        return [ResourceMeasureAnnotation(
            summary="Change rates: insufficient history",
            analysis_step=ANALYSIS_STEP,
            annotation_type_name="db_change_rates",
            check_name="db_change_rates",
            label="unverified",
            confidence=0,
            explanation=result["explanation"],
            resource_properties={
                "reason": result.get("reason") or "",
                "snapshots_available": result.get("snapshots_available"),
            },
        )]

    annotations: list = [ResourceMeasureAnnotation(
        summary=(
            f"Change rates over {result['interval_days']} day(s): "
            f"{result['tables_active']} active, {result['tables_idle']} idle, "
            f"{result['tables_without_a_rate']} without a rate"
        ),
        analysis_step=ANALYSIS_STEP,
        annotation_type_name="db_change_rates",
        check_name="db_change_rates",
        label="measured",
        confidence=100,
        explanation=result["explanation"],
        resource_properties={
            "from_surveyed_at": result["from_surveyed_at"],
            "to_surveyed_at": result["to_surveyed_at"],
            "interval_days": result["interval_days"],
            "tables_active": result["tables_active"],
            "tables_idle": result["tables_idle"],
            "tables_without_a_rate": result["tables_without_a_rate"],
            "tables_counters_reset": result["tables_counters_reset"],
        },
        json_properties={"per_table": result["per_table"]},
    )]

    churn = result["schema_churn"]
    if churn["tables_added"] or churn["tables_removed"]:
        annotations.append(SchemaAnalysisAnnotation(
            summary=(
                f"Schema churn: {len(churn['tables_added'])} table(s) added, "
                f"{len(churn['tables_removed'])} removed"
            ),
            analysis_step=ANALYSIS_STEP,
            annotation_type_name="db_change_rates",
            check_name="schema_churn",
            label="changed",
            confidence=100 if churn["state"] == STATE_MEASURED else 0,
            explanation=churn["explanation"],
            schema_name="schema churn",
            schema_type="change",
            json_properties={
                "tables_added": churn["tables_added"],
                "tables_removed": churn["tables_removed"],
            },
        ))
    return annotations


def _schema_diff_annotations(result: dict) -> list:
    if result["state"] != STATE_MEASURED:
        return [ResourceMeasureAnnotation(
            summary="Column-level schema diff: insufficient history",
            analysis_step=ANALYSIS_STEP,
            annotation_type_name="schema_diff",
            check_name="schema_diff",
            label="unverified",
            confidence=0,
            explanation=result["explanation"],
            resource_properties={
                "reason": result.get("reason") or "",
                "snapshots_available": result.get("snapshots_available"),
            },
        )]

    changed = bool(result["columns_added"] or result["columns_dropped"] or result["columns_retyped"])
    return [SchemaAnalysisAnnotation(
        summary=(
            f"{len(result['columns_added'])} column(s) added, "
            f"{len(result['columns_dropped'])} dropped, "
            f"{len(result['columns_retyped'])} retyped"
            if changed else "No column-level schema change"
        ),
        analysis_step=ANALYSIS_STEP,
        annotation_type_name="schema_diff",
        check_name="schema_diff",
        label="changed" if changed else "unchanged",
        confidence=100,
        explanation=result["explanation"],
        schema_name="column-level schema diff",
        schema_type="change",
        json_properties={
            "from_surveyed_at": result["from_surveyed_at"],
            "to_surveyed_at": result["to_surveyed_at"],
            "columns_added": result["columns_added"],
            "columns_dropped": result["columns_dropped"],
            "columns_retyped": result["columns_retyped"],
        },
    )]


def _grant_change_annotations(result: dict) -> list:
    if result["state"] != STATE_MEASURED:
        return [ResourceMeasureAnnotation(
            summary="Grant change: insufficient history",
            analysis_step=ANALYSIS_STEP,
            annotation_type_name="grant_change",
            check_name="grant_change",
            label="unverified",
            confidence=0,
            explanation=result["explanation"],
            resource_properties={
                "reason": result.get("reason") or "",
                "snapshots_available": result.get("snapshots_available"),
            },
        )]

    changed = bool(result["grants_added"] or result["grants_revoked"])
    return [ResourceMeasureAnnotation(
        summary=(
            f"{len(result['grants_added'])} grant(s) added "
            f"({len(result['public_grants_added'])} to PUBLIC), "
            f"{len(result['grants_revoked'])} revoked"
            if changed else "No grant change"
        ),
        analysis_step=ANALYSIS_STEP,
        annotation_type_name="grant_change",
        check_name="grant_change",
        label="changed" if changed else "unchanged",
        confidence=100,
        explanation=result["explanation"],
        resource_properties={
            "from_surveyed_at": result["from_surveyed_at"],
            "to_surveyed_at": result["to_surveyed_at"],
            "grants_added_count": len(result["grants_added"]),
            "public_grants_added_count": len(result["public_grants_added"]),
            "grants_revoked_count": len(result["grants_revoked"]),
        },
        json_properties={
            "grants_added": result["grants_added"],
            "public_grants_added": result["public_grants_added"],
            "grants_revoked": result["grants_revoked"],
        },
    )]


def _scope_annotations(result: dict) -> list:
    if not result.get("has_scope"):
        return [ResourceMeasureAnnotation(
            summary=(
                "No temporal scope in the data"
                if result["state"] == STATE_MEASURED
                else "Temporal scope not established"
            ),
            analysis_step=ANALYSIS_STEP,
            annotation_type_name=_PROPOSED_SCOPE_CHECK,
            check_name="proposed_data_scope",
            label="pass" if result["state"] == STATE_MEASURED else "unverified",
            confidence=100 if result["state"] == STATE_MEASURED else 0,
            explanation=result["explanation"],
            resource_properties={
                "state": result["state"],
                "reason": result.get("reason") or "",
                "date_column_count": result.get("date_column_count"),
            },
        )]

    return [ResourceMeasureAnnotation(
        summary=(
            f"Proposed DataScope: {result['dataCoverageStartTime']} … "
            f"{result['dataCoverageEndTime']}"
        ),
        analysis_step=ANALYSIS_STEP,
        annotation_type_name=_PROPOSED_SCOPE_CHECK,
        check_name="proposed_data_scope",
        label="proposed",
        confidence=result["confidence"],
        explanation=result["explanation"],
        # A proposal, not a declaration — support doc §3's decision.
        content_status="DRAFT",
        resource_properties={
            # DataScopeProperties' own key names, so a Curate prefill is a
            # key-for-key copy (support doc §7).
            "dataCoverageStartTime": result["dataCoverageStartTime"],
            "dataCoverageEndTime": result["dataCoverageEndTime"],
            "confidence": result["confidence"],
            "basis": result["basis"],
        },
        json_properties={"column_ranges": result["column_ranges"]},
    )]


def _subject_annotations(result: dict) -> list:
    """One annotation for `subject_signals` (design §16.3's Scouting row 1).

    Two mutually exclusive branches — the absence branch `return`s
    immediately — which is why `subject_signals` is listed in
    `tests/test_annotation_check_names.py`'s `KNOWN_EXCLUSIVE`, on the same
    grounds as the eight checks above it.
    """
    if result["state"] != STATE_MEASURED or not result.get("terms"):
        measured = result["state"] == STATE_MEASURED
        return [ResourceMeasureAnnotation(
            summary=(
                "Names carry no subject term" if measured
                else "Subject signals not established"
            ),
            analysis_step=ANALYSIS_STEP,
            annotation_type_name="subject_signals",
            check_name="subject_signals",
            # `gap` when measured (the names really say nothing — an
            # Enrichment prompt); `unverified` when nothing was surveyed.
            label="gap" if measured else "unverified",
            confidence=0,
            explanation=result["explanation"],
            resource_properties={
                "state": result["state"],
                "reason": result.get("reason") or "",
                "comments_captured": result.get("comments_captured"),
            },
        )]

    terms = result["terms"]
    return [ResourceMeasureAnnotation(
        summary=(
            "Candidate subjects: "
            + ", ".join(t["term"] for t in terms[:6])
            + (f" (+{result['term_count'] - min(6, len(terms))} more)"
               if result["term_count"] > 6 else "")
        ),
        analysis_step=ANALYSIS_STEP,
        annotation_type_name="subject_signals",
        check_name="subject_signals",
        label="info",
        confidence=result["confidence"],
        explanation=result["explanation"],
        # Candidate terms from names, never a declaration — the same
        # measured-is-not-declared rule `proposed_data_scope` follows.
        content_status="DRAFT",
        resource_properties={
            # §16.6's `scopeElements` key, so a lens comparison reads the same
            # key name on both sides (§16.1).
            "subjectTerms": ", ".join(t["term"] for t in terms),
            "term_count": result["term_count"],
            "comments_captured": result.get("comments_captured"),
            "glossary_matched": result.get("glossary_matched"),
        },
        json_properties={"terms": terms},
    )]


def _coverage_annotations(result: dict) -> list:
    """One annotation per block of `coverage_signals` (§16.3's Scouting row 3).

    Three annotations rather than one composite, because the three blocks fail
    independently and §15 rules out a composite score: a database whose period
    is unknown and whose place columns are merely named must not average into
    one "partially covered" line.

    `check_name` is distinct per block (`coverage_signals_temporal` and so on),
    so each is separately followable and no two collide on qualifiedName.
    """
    if result["state"] != STATE_MEASURED:
        return [ResourceMeasureAnnotation(
            summary="Coverage signals not established",
            analysis_step=ANALYSIS_STEP,
            annotation_type_name="coverage_signals",
            check_name="coverage_signals",
            label="unverified",
            confidence=0,
            explanation=result["explanation"],
            resource_properties={"reason": result.get("reason") or ""},
        )]

    out: list = []
    temporal = result["temporal"]
    if temporal["state"] == STATE_MEASURED and temporal.get("dataCoverageStartTime"):
        out.append(ResourceMeasureAnnotation(
            summary=(
                f"Covers {temporal['dataCoverageStartTime']} … "
                f"{temporal['dataCoverageEndTime']} (catalog estimate)"
            ),
            analysis_step=ANALYSIS_STEP,
            annotation_type_name="coverage_signals",
            check_name="coverage_signals_temporal",
            label=temporal["label"],
            confidence=temporal["confidence"],
            explanation=temporal["explanation"],
            content_status="DRAFT",
            resource_properties={
                "dataCoverageStartTime": temporal["dataCoverageStartTime"],
                "dataCoverageEndTime": temporal["dataCoverageEndTime"],
                "basis": temporal["basis"],
                "reason": temporal["reason"],
            },
            json_properties={"column_ranges": temporal["column_ranges"]},
        ))
    else:
        no_dates = temporal["reason"] == COVERAGE_REASON_NO_DATE_COLUMNS
        out.append(ResourceMeasureAnnotation(
            summary=(
                "Holds no dated data" if no_dates
                else "Period covered not established — run ANALYZE"
            ),
            analysis_step=ANALYSIS_STEP,
            annotation_type_name="coverage_signals",
            check_name="coverage_signals_temporal",
            label=temporal["label"],
            # A measured negative is a real finding and says so; an
            # unpopulated statistic is not a finding at all.
            confidence=90 if no_dates else 0,
            explanation=temporal["explanation"],
            resource_properties={
                "state": temporal["state"],
                "reason": temporal["reason"],
                "remedy": temporal.get("remedy") or "",
                "date_column_count": temporal.get("date_column_count"),
            },
        ))

    spatial = result["spatial"]
    out.append(ResourceMeasureAnnotation(
        summary=(
            f"{len(spatial.get('place_columns') or [])} place-named column(s); "
            f"values not read"
            if spatial.get("verdict") == "candidates_only"
            else "No place-named column"
        ),
        analysis_step=ANALYSIS_STEP,
        annotation_type_name="coverage_signals",
        check_name="coverage_signals_spatial",
        label=spatial.get("label") or "info",
        confidence=spatial.get("confidence") or 0,
        explanation=spatial["explanation"],
        resource_properties={
            "state": spatial["state"],
            "verdict": spatial.get("verdict") or "",
            "values_read": spatial.get("values_read"),
            "next_analysis": spatial.get("next_analysis") or "",
        },
        json_properties={
            "place_columns": spatial.get("place_columns") or [],
            "ambiguous_place_columns": spatial.get("ambiguous_place_columns") or [],
        },
    ))

    partitions = result["partitions"]
    out.append(ResourceMeasureAnnotation(
        summary="Partition bounds not collected by any step",
        analysis_step=ANALYSIS_STEP,
        annotation_type_name="coverage_signals",
        check_name="coverage_signals_partitions",
        label=partitions["label"],
        confidence=0,
        explanation=partitions["explanation"],
        resource_properties={
            "state": partitions["state"],
            "reason": partitions["reason"],
            "remedy": partitions["remedy"],
        },
    ))
    return out


def _fit_annotations(result: dict) -> list:
    """One annotation for `preliminary_fit` (§16.3's Discovery gate).

    Five verdicts, and they are not five flavours of one summary —
    REPLY-COPY-REVIEW-CREDENTIAL-AND-FIT-LANGUAGE.md §4. Putting
    "Preliminary fit:" in front of every one of them made all five read as a
    verdict on the RESOURCE, which is only true of two of them:

    - `fits` / `does_not_fit` — about the resource.
    - `could_not_check` / `nothing_measured` — about OUR measurement, and two
      different absences: `could_not_check` means named inputs this lens
      needed weren't measured for THIS analysis; `nothing_measured` means
      nothing about the resource has been surveyed at all. Collapsing them
      would be the exact `could_not_check`-reads-like-`does_not_fit` mistake
      §16.2's ANALYZE warning is about, one level up.
    - `no_requirement_declared` — about the LENS (`db_derived.py`'s own
      docstring on `compute_preliminary_fit`), which is why its sentence
      leads with "No preliminary fit" rather than "Preliminary fit:" — there
      is no fit verdict to prefix.

    A `could_not_check` rendered like a `does_not_fit` would be the exact
    collapse §16.2's ANALYZE warning is about, one level up — the reason
    every branch below is its own sentence rather than one shared template.
    """
    verdict = result["verdict"]
    failed = result.get("failed_inputs") or []
    unchecked = result.get("unchecked_inputs") or []

    if verdict == FIT_FITS:
        summary = "Preliminary fit: in scope — worth the full pass"
    elif verdict == FIT_DOES_NOT_FIT:
        # The first failed input, not silence — "out of scope" with no reason
        # is a dismissal nobody can check (§4).
        summary = (
            f"Preliminary fit: out of scope — {failed[0]}" if failed
            else "Preliminary fit: out of scope"
        )
    elif verdict == FIT_COULD_NOT_CHECK:
        n = len(unchecked)
        summary = (
            f"Preliminary fit not checked — {n} input{'' if n == 1 else 's'} "
            f"not measured: {', '.join(unchecked)}"
        ) if unchecked else "Preliminary fit not checked"
    elif verdict == FIT_NOTHING_MEASURED:
        summary = ("No preliminary fit yet — nothing about this resource has "
                   "been measured; run a survey ›")
    elif verdict == FIT_NO_REQUIREMENT:
        summary = ("No preliminary fit — no fit requirement is declared; "
                   "declare one ›")
    else:
        # A blank label with no answer is worse than naming the surprise —
        # find_absence_as_answer's rule applies to our own defensive
        # fallback, not only to the data we're summarising.
        summary = f"Preliminary fit: unrecognised verdict `{verdict}`"
    return [ResourceMeasureAnnotation(
        summary=summary,
        analysis_step=ANALYSIS_STEP,
        annotation_type_name="preliminary_fit",
        check_name="preliminary_fit",
        label=result["label"],
        confidence=result["confidence"],
        explanation=result["explanation"],
        resource_properties={
            "verdict": verdict,
            "lens_declared": result["lens_declared"],
            "lens_source": result["lens_source"],
            # §16.5 point 4: which lens version this verdict was computed
            # against. Empty for an ad-hoc lens, and empty is the honest
            # answer rather than a default version number.
            "lens_version": result["lens_version"],
            "unchecked_inputs": ", ".join(result.get("unchecked_inputs") or []),
            "failed_inputs": ", ".join(result.get("failed_inputs") or []),
        },
        json_properties={
            "inputs": result.get("inputs") or {},
            "achievable": result.get("achievable") or {},
            "deferred_criteria": result.get("deferred_criteria") or [],
            "unused_criteria": result.get("unused_criteria") or [],
        },
    )]
