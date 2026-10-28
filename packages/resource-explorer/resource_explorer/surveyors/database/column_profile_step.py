"""The `postgres_column_profile` step (design §5.7, Phase 1 slice 10).

Design §5.7's row for this step:

| `postgres_column_profile` (new; sampling fallback + value-based matching) |
| sampled rows, bounded by rows and bytes per column | api_heavy / medium |
| ResourceMeasure per column, DataClass, ValidValues advice, Semantic, RFAs
  proposing classes/sets |

What is here: the sampling *execution* (the configuration surface is
`sampling.py`, the matching logic is `column_matching.py`, the Egeria reads
and DRAFT proposals are `egeria_reference_catalog.py`), plus the annotation
building for `data_class_match` and `reference_data_match`.

Deliberately a module of its own rather than four more methods on
`DatabaseSurveyor`: slices 7 and 8 own that file, this slice consumes their
output, and `survey()` reaches this step through a single delegating branch.

**Absence discipline, the shape that matters most here.** Every column gets a
row and an annotation, including the ones nothing could be said about. Four
distinct "nothing" states reach the output, and none of them renders as "no
match found":

- the engine declares no `value_sampling` capability → `STATE_NOT_SUPPORTED`;
- the configured strategy is `catalog_stats_only`, so no value was read →
  `MATCH_NOT_SAMPLED`;
- the byte or time budget ran out before this column → `MATCH_NOT_SAMPLED`,
  with the budget named as the reason;
- the platform held nothing to compare against → `MATCH_NO_CANDIDATES`.

A `data_class_match` that reports "no PII found" for a column it never read
would be the most expensive version of the defect `find-absence-as-answer`
describes, so the states above are separate all the way to the annotation's
`label`.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from resource_explorer.registry import (
    STATE_MEASURED,
    STATE_NOT_APPLICABLE,
    STATE_NOT_COLLECTED,
    STATE_NOT_SUPPORTED,
)
from resource_explorer.surveyors.database.column_matching import (
    MATCH_MATCHED,
    MATCH_NOT_APPLICABLE,
    MATCH_NOT_SAMPLED,
    MATCH_PARTIAL,
    MATCH_UNMATCHED_PATTERNED,
    NOT_ESTABLISHED_VERDICTS,
    ColumnMatch,
    data_class_match,
    is_low_cardinality,
    reference_data_match,
    resolve_n_distinct,
)
from resource_explorer.surveyors.database.egeria_reference_catalog import (
    CONTENT_STATUS_DRAFT,
    ReferenceCatalog,
)
from resource_explorer.surveyors.database.sampling import (
    CATALOG_STATS_ONLY,
    SampleProvenance,
    SamplingConfig,
    build_column_sample_sql,
    build_distinct_values_sql,
    bytes_budget_exceeded,
    estimated_value_bytes,
    not_sampled_provenance,
    resolve_sampling_config,
)
from resource_explorer.surveyors.survey_report import (
    DataClassAnnotation,
    RelationshipAnnotation,
    RequestForActionAnnotation,
    ResourceMeasureAnnotation,
)

log = logging.getLogger(__name__)

#: The `analysis_step` every annotation from this step carries.
ANALYSIS_STEP = "DatabasePostgresColumnProfile"

#: §5.8's `actionRequested` vocabulary for a proposal, from
#: `egeria-support-for-multi-resource.md` §3.
ACTION_PROPOSE_DATA_CLASS = "propose-data-class"
ACTION_PROPOSE_VALID_VALUE_SET = "propose-valid-value-set"
#: The partial-reference-data case, which is NOT a proposal: the set exists and
#: some of the column's values fall outside it. There is no element to create
#: in DRAFT — the decision is whether to extend the set or fix the data — so
#: §3's RFA convention remains the right carrier here even though DRAFT
#: creation now works. See `POSTGRES-COLUMN-PROFILE-IMPLEMENTED.md`.
ACTION_REVIEW_UNMATCHED_VALUES = "review-unmatched-reference-values"

#: How many unmatched values an RFA names before it summarises. A curator
#: needs to see the values; a column with 60 of them needs a list, not a wall.
MAX_UNMATCHED_VALUES_IN_RFA = 40

#: §F, 2026-09-28: relation `type` strings (as `get_schema_info()` reports
#: them — `information_schema.tables.table_type`, or the catalog fallback's
#: own `kind_to_type` mapping) that `TABLESAMPLE` cannot be applied to at
#: all. Postgres rejects it outright on a view or a foreign table; a
#: materialized view is a real, TABLESAMPLE-able relation and is
#: deliberately NOT in this set — it is sampled exactly like a base table.
NOT_SAMPLABLE_TABLE_TYPES = frozenset({"VIEW", "FOREIGN", "FOREIGN TABLE"})

#: The step's own status (§F item 3) — not a `result_status` state, since
#: this describes the STEP's overall run, not one column's finding.
STEP_STATUS_OK = "ok"
STEP_STATUS_PARTIAL = "partial"


def _iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _row_counts_by_table(stats_info: dict) -> dict[tuple[str, str], int]:
    """`(schema, table) -> n_live_tup`, from slice 7's own statistics read.

    A table ABSENT from this mapping has an unknown row count, not zero — the
    statistics collector may have no row for it yet. Callers pass `None` in
    that case, and `SampleProvenance.describe()` then says "of an unknown
    total number of rows" rather than implying a fraction it cannot support.
    """
    counts: dict[tuple[str, str], int] = {}
    for row in stats_info.get("row_stats") or []:
        key = (row.get("schemaname", ""), row.get("tablename", ""))
        value = row.get("row_count")
        if value is not None:
            counts[key] = int(value)
    return counts


def _profiles_by_column(column_profile_rows: Sequence[dict]) -> dict[tuple[str, str, str], dict]:
    """Slice 7's `database_column_profiles` rows, keyed by column.

    This is where `reference_data_match`'s cardinality gate gets its number
    from: slice 7 already stored `pg_stats.n_distinct` per column, and
    re-deriving it with a `COUNT(DISTINCT ...)` would both cost a full scan and
    disagree with the stored row.
    """
    return {
        (r.get("schema_name", ""), r.get("table_name", ""), r.get("column_name", "")): r
        for r in column_profile_rows or []
    }


class SamplingBudget:
    """The two bounds §5.8 makes global to the step rather than per column:
    `max_bytes` and `time_budget`.

    Stateful on purpose — it is consumed as columns are sampled. When it runs
    out, the remaining columns are recorded as NOT SAMPLED with the budget
    named, which is the distinction that makes a partial run readable: a
    30-column table with 12 sampled and 18 `not_established` is a legible
    result, while 12 findings and 18 silences is not.
    """

    def __init__(self, config: SamplingConfig) -> None:
        self.config = config
        self.bytes_read = 0
        self.started_at = time.monotonic()
        self.columns_sampled = 0
        self.exhausted_reason = ""

    @property
    def exhausted(self) -> bool:
        if self.exhausted_reason:
            return True
        if bytes_budget_exceeded(self.config, self.bytes_read):
            self.exhausted_reason = (
                f"the step's {self.config.max_bytes:,}-byte sampling budget was "
                f"spent after {self.columns_sampled} column(s)"
            )
            return True
        elapsed = time.monotonic() - self.started_at
        if elapsed >= self.config.time_budget_seconds:
            self.exhausted_reason = (
                f"the step's {self.config.time_budget_seconds}s time budget was "
                f"spent after {self.columns_sampled} column(s) "
                f"({elapsed:.1f}s elapsed)"
            )
            return True
        return False

    def charge(self, values: Sequence[Any]) -> None:
        self.bytes_read += estimated_value_bytes(list(values))
        self.columns_sampled += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "bytes_read": self.bytes_read,
            "columns_sampled": self.columns_sampled,
            "elapsed_seconds": round(time.monotonic() - self.started_at, 3),
            "exhausted": bool(self.exhausted_reason),
            "exhausted_reason": self.exhausted_reason,
        }


def sample_column_values(
    conn,
    schema_name: str,
    table_name: str,
    column_name: str,
    config: SamplingConfig,
    total_rows: int | None,
    budget: SamplingBudget,
    *,
    distinct: bool = False,
) -> tuple[list[Any] | None, SampleProvenance]:
    """Execute one column's sample, returning `(values, provenance)`.

    `values is None` means no sample was taken and the provenance says why —
    the single most important return shape in this module. It is never `[]` for
    that case: an empty list means the sample ran and the column had no
    non-NULL values, which is a measurement.

    `distinct=True` builds the DISTINCT-with-counts query
    (`reference_data_match`), and sets `SampleProvenance.truncated` when more
    than `max_values` distinct values came back — which is what stops a
    coverage fraction being computed from an incomplete list.
    """
    if not config.reads_values:
        return None, not_sampled_provenance(config)
    if budget.exhausted:
        return None, not_sampled_provenance(config, budget.exhausted_reason)

    builder = build_distinct_values_sql if distinct else build_column_sample_sql
    try:
        query = builder(schema_name, table_name, column_name, config, total_rows)
    except Exception as exc:
        return None, not_sampled_provenance(
            config, f"the sample query could not be built: {exc}"
        )
    if query is None:
        return None, not_sampled_provenance(config)

    try:
        # `execute_query_isolated` (§F, 2026-09-28), not `execute_query`: this
        # loop issues one speculative query per column, and Postgres aborts
        # the WHOLE transaction on the first one that fails — every column
        # sampled afterward would fail too, silently, on a connection nothing
        # ever released from that state. See `PostgreSQLConnection.
        # execute_query_isolated`'s own docstring for the live incident this
        # replaced (`laz_local_adventureworks`, ~40 columns cascade-failed
        # behind one TABLESAMPLE-on-a-view error).
        rows = conn.execute_query_isolated(query.sql)
    except Exception as exc:
        # A failed sample is NOT an empty sample. Reported as not-sampled with
        # the error, so a permission problem on one table does not read as
        # "that table's columns match nothing".
        log.warning(
            "Sampling %s.%s.%s failed: %s", schema_name, table_name, column_name, exc
        )
        return None, SampleProvenance(
            strategy=CATALOG_STATS_ONLY,
            reason_not_sampled=f"the sample query failed: {exc}",
            errored=True,
        )

    values = [r.get("value") for r in (rows or []) if isinstance(r, dict)]
    truncated = bool(distinct and len(values) > config.max_values)
    if truncated:
        values = values[: config.max_values]
    budget.charge(values)

    provenance = SampleProvenance(
        strategy=query.strategy,
        sample_rows=len(values),
        total_rows=total_rows,
        seed=config.seed,
        sampled_at=_iso_now(),
        tablesample_method=query.tablesample_method,
        tablesample_percent=query.tablesample_percent,
        truncated=truncated,
    )
    return values, provenance


# ── Annotation building ───────────────────────────────────────────────────────

def _measure_annotation(match: ColumnMatch, provenance: SampleProvenance) -> ResourceMeasureAnnotation:
    """§5.7's "ResourceMeasure per column" — what was looked at, per column.

    Confidence 0 for every unestablished verdict, matching slices 7 and 8's
    convention for a finding that is not established. A reader comparing two
    columns' annotations can tell a measured one from an unmeasured one by the
    confidence alone, without parsing prose.
    """
    established = match.verdict not in NOT_ESTABLISHED_VERDICTS
    return ResourceMeasureAnnotation(
        summary=f"Column sample for {match.column_path}: {provenance.describe()}",
        analysis_step=ANALYSIS_STEP,
        annotation_type_name="ColumnValueSample",
        confidence=100 if established else 0,
        check_name="column_value_sample",
        item_key=match.column_path,
        label="sampled" if provenance.reads_values else "not_sampled",
        resource_properties={
            "schema": match.schema_name,
            "table": match.table_name,
            "column": match.column_name,
            **{k: v for k, v in provenance.as_dict().items() if v is not None},
        },
        json_properties={"sample": provenance.as_dict()},
        explanation=(
            "Which sampling strategy actually ran, with the sample size, the "
            "total row count and the seed — design §5.8 requires every "
            "sampled answer to carry this, so a consumer can tell a sampled "
            "measurement from a pg_stats one from a full scan."
        ),
    )


def _data_class_annotation(match: ColumnMatch) -> DataClassAnnotation:
    """A `DataClassAnnotation` for one column's `data_class_match` verdict.

    Set for EVERY verdict, not only for a match. An unestablished verdict
    publishes at confidence 0 with its reason in `explanation`, because the
    alternative — publishing nothing — is how a column nobody could read ends
    up looking identical to a column with no sensitive data in it.

    A `MATCH_UNMATCHED_PATTERNED` verdict carries `content_status: DRAFT`: the
    annotation is itself the proposal's evidence and is genuinely unconfirmed
    content. Verified live 2026-09-21 on a real `DataClassAnnotation` (probe 4,
    corrected) — `contentStatus` round-trips as `DRAFT` while the element's own
    `ElementStatus` stays `ACTIVE`.
    """
    candidates: list[str] = []
    if match.verdict == MATCH_MATCHED and match.matched_guid:
        candidates = [match.matched_guid]
    return DataClassAnnotation(
        summary=match.describe(),
        analysis_step=ANALYSIS_STEP,
        annotation_type_name="DataClassMatch",
        confidence=match.confidence,
        check_name="data_class_match",
        item_key=match.column_path,
        label=match.verdict,
        candidate_data_class_names=candidates,
        content_status=(
            CONTENT_STATUS_DRAFT if match.verdict == MATCH_UNMATCHED_PATTERNED else ""
        ),
        explanation=_verdict_explanation(match),
        json_properties=match.as_dict(),
    )


def _reference_data_annotations(match: ColumnMatch) -> list:
    """`reference_data_match`'s annotations for one column.

    A full match produces §5.4's "`ValidValuesAssignment` advice". Egeria has
    no `ValidValuesAnnotation` type — `ValidValuesAssignment` is a
    *relationship* — so the advice is published as a
    `RelationshipAnnotation` (`RelationshipAdviceAnnotationProperties`) naming
    that relationship type and the set to bind to. That is exactly what a
    relationship-advice annotation is for, and it keeps the finding a
    suggestion a curator accepts rather than a binding RE made unilaterally.

    Every other verdict publishes a confidence-scaled
    `RelationshipAnnotation` too, so the column is not silently absent from
    the reference-data view.
    """
    annotations: list = [
        RelationshipAnnotation(
            summary=match.describe(),
            analysis_step=ANALYSIS_STEP,
            annotation_type_name="ReferenceDataMatch",
            confidence=match.confidence,
            check_name="reference_data_match",
            item_key=match.column_path,
            label=match.verdict,
            related_entity_name=match.matched_qualified_name,
            relationship_type_name=(
                "ValidValuesAssignment"
                if match.verdict in (MATCH_MATCHED, MATCH_PARTIAL)
                else ""
            ),
            content_status=(
                CONTENT_STATUS_DRAFT
                if match.verdict == MATCH_UNMATCHED_PATTERNED
                else ""
            ),
            explanation=_verdict_explanation(match),
            json_properties=match.as_dict(),
        )
    ]
    return annotations


def _verdict_explanation(match: ColumnMatch) -> str:
    if match.verdict in NOT_ESTABLISHED_VERDICTS:
        return (
            f"NOT ESTABLISHED: {match.not_established_reason}. This is not a "
            f"finding that no match exists — nothing was concluded either way."
        )
    if match.verdict == MATCH_UNMATCHED_PATTERNED:
        return (
            "No existing element matched, and the sampled values are "
            "regular enough to propose one. Published as contentStatus: "
            "DRAFT — unconfirmed content awaiting a curator, not a finding."
        )
    base = (
        "Matched by comparing the column's name, type and sampled values "
        "against every element of this kind the Egeria platform holds "
        "(design §5.4)."
    )
    if match.evidence == "name_and_type":
        return (
            base
            + " The matched class declares no value specification, so NO "
            "value conformance was tested — this match rests on the column's "
            "name and type alone, and its confidence is capped accordingly."
        )
    return base


def _rfa_for_match(match: ColumnMatch, *, draft_available: bool) -> RequestForActionAnnotation | None:
    """The RFA convention from support-doc §3, for the cases DRAFT creation
    does not cover.

    Two cases, and the distinction is the point of this function:

    1. **A partial reference-data match.** The set exists; some sampled values
       fall outside it. There is no new element to create in DRAFT — the
       decision is whether to extend the set or to fix the data — so §3's RFA
       remains the right carrier here even now that DRAFT works. The RFA names
       the unmatched values, which §5.4 asks for by name ("partial → RFA
       listing the unmatched values").
    2. **A proposal that could not be created as a DRAFT element**, because no
       Egeria client was available to this run. The RFA is then the fallback
       §3 described as the "until DRAFT works" plan, still needed whenever the
       platform cannot be written to — carrying the observed pattern or values
       so the proposal is not lost.

    Returns `None` for every other verdict. An RFA on a `MATCH_NOT_SAMPLED`
    column would be an action request about nothing.
    """
    if match.verdict == MATCH_PARTIAL:
        values = match.unmatched_values[:MAX_UNMATCHED_VALUES_IN_RFA]
        overflow = len(match.unmatched_values) - len(values)
        listed = ", ".join(repr(v) for v in values)
        if overflow > 0:
            listed += f", and {overflow} more"
        return RequestForActionAnnotation(
            summary=(
                f"{match.column_path} partially conforms to Valid Value Set "
                f"'{match.matched_display_name}': "
                f"{len(match.unmatched_values)} sampled value(s) fall outside it"
            ),
            analysis_step=ANALYSIS_STEP,
            annotation_type_name="ReferenceDataPartialMatch",
            confidence=match.confidence,
            check_name="reference_data_match",
            item_key=f"{match.column_path}::unmatched",
            label="gap",
            action_requested=ACTION_REVIEW_UNMATCHED_VALUES,
            action_target_name=match.column_path,
            explanation=(
                f"Values outside the set: {listed}. Either the set should be "
                f"extended to include them, or the data does not conform. "
                f"{match.describe()} No element was proposed in DRAFT for this "
                f"case: the set already exists, so the decision is about its "
                f"membership, not about creating a candidate."
            ),
            json_properties=match.as_dict(),
        )

    if match.verdict == MATCH_UNMATCHED_PATTERNED and not draft_available:
        is_reference = match.question == "reference_data_match"
        return RequestForActionAnnotation(
            summary=(
                f"Propose a new "
                f"{'Valid Value Set' if is_reference else 'Data Class'} for "
                f"{match.column_path}"
            ),
            analysis_step=ANALYSIS_STEP,
            annotation_type_name="ProposalFallback",
            confidence=match.confidence,
            check_name=match.question,
            item_key=f"{match.column_path}::proposal",
            label="gap",
            action_requested=(
                ACTION_PROPOSE_VALID_VALUE_SET if is_reference
                else ACTION_PROPOSE_DATA_CLASS
            ),
            action_target_name=match.column_path,
            explanation=(
                f"{match.describe()} No Egeria client was available to this "
                f"run, so the candidate element could not be created with "
                f"contentStatus: DRAFT — this RFA carries the proposal "
                f"instead. "
                + (
                    f"Observed values: {', '.join(repr(v) for v in match.proposed_values[:MAX_UNMATCHED_VALUES_IN_RFA])}."
                    if is_reference
                    else f"Observed pattern: {match.proposed_specification!r} "
                         f"({match.proposed_specification_prose})."
                )
            ),
            json_properties=match.as_dict(),
        )
    return None


# ── The step ──────────────────────────────────────────────────────────────────

def run_column_profile(
    conn,
    capabilities,
    schema_info: dict,
    stats_info: dict,
    column_profile_rows: Sequence[dict],
    *,
    resource_slug: str = "",
    reference_catalog: ReferenceCatalog | None = None,
    sampling_overrides: dict[str, Any] | None = None,
    run_overrides: dict[str, Any] | None = None,
    include_draft_elements: bool = False,
    max_columns: int | None = None,
) -> dict:
    """`postgres_column_profile`: sample, then match.

    Returns a dict with `column_profile_rows` (sampling provenance to merge
    into slice 7's rows), `annotations`, `matches`, `sampling` (the resolved
    config and the budget actually spent) and `reference_catalog` (what the
    platform held, and whether it could be read).

    `reference_catalog=None` is honest, not empty: matching then reports
    `MATCH_NO_CANDIDATES` for every column, because a run that never asked
    Egeria what Data Classes exist has established nothing about whether a
    column matches one.
    """
    config = resolve_sampling_config(
        purpose="matching",
        resource_slug=resource_slug,
        global_settings=sampling_overrides,
        run_settings=run_overrides,
    )
    budget = SamplingBudget(config)
    annotations: list = []
    matches: list[ColumnMatch] = []
    rows: list[dict] = []

    supports_sampling = bool(getattr(capabilities, "value_sampling", False))
    catalog = reference_catalog or ReferenceCatalog(
        available=False,
        unavailable_reason=(
            "this run did not read the Egeria platform's Data Classes or Valid "
            "Value Sets, so no comparison was possible"
        ),
    )

    if not supports_sampling:
        annotations.append(
            ResourceMeasureAnnotation(
                summary="Value sampling not supported by this engine",
                analysis_step=ANALYSIS_STEP,
                confidence=0,
                check_name="column_value_sample",
                item_key="capability",
                label="unverified",
                resource_properties={"capability": "value_sampling", "supported": False},
                explanation=(
                    "This connection's engine capability declaration does not "
                    "include value_sampling, so data_class_match and "
                    "reference_data_match are not established for any column — "
                    "not a measurement that no column matches anything."
                ),
            )
        )
    if not catalog.available:
        annotations.append(
            ResourceMeasureAnnotation(
                summary=(
                    "The Egeria platform's Data Classes and Valid Value Sets "
                    "could not be read"
                ),
                analysis_step=ANALYSIS_STEP,
                confidence=0,
                check_name="reference_catalog_read",
                item_key="platform",
                label="unverified",
                resource_properties=catalog.as_dict(),
                explanation=(
                    f"{catalog.unavailable_reason}. Every column's match verdict "
                    f"is therefore 'no_candidates' — an unread catalog cannot "
                    f"produce a negative finding."
                ),
            )
        )

    row_counts = _row_counts_by_table(stats_info)
    profiles = _profiles_by_column(column_profile_rows)
    effective_config = config if supports_sampling else SamplingConfig(
        strategy=CATALOG_STATS_ONLY, seed=config.seed,
    )

    # ── §F, 2026-09-28: the step's own status ──────────────────────────────
    #
    # Counted per COLUMN, from the PRIMARY (data_class_match) sample's
    # provenance — the same one `_profile_row` bases its `state` on — so a
    # column that also took a second `distinct=True` sample for
    # `reference_data_match` is not counted twice. `_note_sample_outcome`
    # still checks every provenance it is handed for `errored`, so a failure
    # on the SECOND sample still counts toward `errored_count`/`first_error`
    # even when the first one succeeded.
    sampled_count = 0
    skipped_not_applicable_count = 0
    errored_count = 0
    first_error = ""

    def _note_sample_outcome(prov: SampleProvenance, *, count_success: bool) -> None:
        nonlocal errored_count, first_error, sampled_count
        if prov.errored:
            errored_count += 1
            if not first_error:
                first_error = prov.reason_not_sampled
        elif count_success and prov.reads_values and prov.sample_rows is not None:
            sampled_count += 1

    columns_seen = 0
    for schema in schema_info.get("schemas", []) or []:
        schema_name = schema.get("name", "")
        for table in schema.get("tables", []) or []:
            table_name = table.get("name", "")
            # §F: a view or foreign table's columns are never offered to
            # TABLESAMPLE at all — Postgres rejects it outright, and (before
            # this fix) that rejection aborted the whole transaction and
            # silently failed every column sampled afterward. A materialized
            # view's `type` is NOT in `NOT_SAMPLABLE_TABLE_TYPES`; it is
            # sampled exactly like a base table.
            table_type = (table.get("type") or "").strip().upper()
            not_samplable_reason = (
                "view" if table_type == "VIEW"
                else "foreign table" if table_type in ("FOREIGN", "FOREIGN TABLE")
                else ""
            )
            total_rows = row_counts.get((schema_name, table_name))
            for column in table.get("columns", []) or []:
                column_name = column.get("name", "")
                if not column_name:
                    continue
                if max_columns is not None and columns_seen >= max_columns:
                    break
                columns_seen += 1
                column_type = (
                    column.get("data_type") or column.get("type") or column.get("base_type") or ""
                )
                profile = profiles.get((schema_name, table_name, column_name)) or {}

                # ── data_class_match ──
                if not_samplable_reason:
                    values, provenance = None, not_sampled_provenance(
                        effective_config,
                        f"this is a {not_samplable_reason} — TABLESAMPLE cannot "
                        "be applied to it",
                        not_applicable=True,
                    )
                    skipped_not_applicable_count += 1
                else:
                    values, provenance = sample_column_values(
                        conn, schema_name, table_name, column_name,
                        effective_config, total_rows, budget,
                    )
                    _note_sample_outcome(provenance, count_success=True)
                dc_match = data_class_match(
                    schema_name=schema_name, table_name=table_name,
                    column_name=column_name, column_type=column_type,
                    known_classes=catalog.data_classes if catalog.available else [],
                    sampled_values=values, provenance=provenance,
                    include_draft_classes=include_draft_elements,
                )
                if not catalog.available:
                    dc_match = _force_no_candidates(dc_match, catalog)
                matches.append(dc_match)
                annotations.append(_measure_annotation(dc_match, provenance))
                annotations.append(_data_class_annotation(dc_match))

                # ── reference_data_match ──
                distinct_count = resolve_n_distinct(
                    profile.get("distinct_count"), total_rows
                )
                low = is_low_cardinality(distinct_count, total_rows)
                if low is True and not not_samplable_reason:
                    distinct_values, distinct_provenance = sample_column_values(
                        conn, schema_name, table_name, column_name,
                        effective_config, total_rows, budget, distinct=True,
                    )
                    # `count_success=False`: this column was already counted
                    # sampled (or errored) above, from the primary provenance
                    # `_profile_row` bases its state on — this call only adds
                    # to `errored_count`/`first_error` if IT fails, never a
                    # second success tally for the same column.
                    _note_sample_outcome(distinct_provenance, count_success=False)
                else:
                    # Not low-cardinality (or unknown): no second sample is
                    # taken. The gate is what §5.4 specifies, and paying for a
                    # distinct read on a unique key is exactly the cost the
                    # gate exists to avoid.
                    distinct_values, distinct_provenance = None, provenance
                rd_match = reference_data_match(
                    schema_name=schema_name, table_name=table_name,
                    column_name=column_name, column_type=column_type,
                    known_sets=catalog.valid_value_sets if catalog.available else [],
                    distinct_values=distinct_values,
                    distinct_count=distinct_count,
                    total_rows=total_rows,
                    provenance=distinct_provenance,
                    include_draft_sets=include_draft_elements,
                )
                if not catalog.available and rd_match.verdict not in (
                    MATCH_NOT_SAMPLED, MATCH_NOT_APPLICABLE,
                ):
                    rd_match = _force_no_candidates(rd_match, catalog)
                matches.append(rd_match)
                annotations.extend(_reference_data_annotations(rd_match))

                for match in (dc_match, rd_match):
                    rfa = _rfa_for_match(match, draft_available=catalog.available)
                    if rfa is not None:
                        annotations.append(rfa)

                rows.append(
                    _profile_row(
                        schema_name, table_name, column_name, profile,
                        provenance, supports_sampling,
                    )
                )

    # §F item 3: the step's own status stops saying "ok" the moment any
    # column actually ERRORED (a real query failure, `SampleProvenance.
    # errored`) — never for the ordinary, honest not-sampled reasons
    # (`catalog_stats_only`, budget exhausted, a view/foreign table's
    # `skipped_not_applicable_count`), which are not failures and have always
    # produced a complete, correct run.
    status = STEP_STATUS_PARTIAL if errored_count else STEP_STATUS_OK

    return {
        "column_profile_rows": rows,
        "annotations": annotations,
        "matches": [m.as_dict() for m in matches],
        "match_objects": matches,
        "sampling": {
            "config": config.as_dict(),
            "effective_strategy": effective_config.strategy,
            "budget": budget.as_dict(),
            "value_sampling_supported": supports_sampling,
        },
        "reference_catalog": catalog.as_dict(),
        "status": status,
        "counts": {
            "sampled": sampled_count,
            "skipped_not_applicable": skipped_not_applicable_count,
            "errored": errored_count,
        },
        "first_error": first_error,
    }


def _force_no_candidates(match: ColumnMatch, catalog: ReferenceCatalog) -> ColumnMatch:
    """Re-label a verdict reached against an UNREAD catalogue.

    `column_matching` already returns `MATCH_NO_CANDIDATES` for an empty
    candidate list, but "empty because the platform holds none" and "empty
    because we could not ask" are different claims and only the caller knows
    which it is. This carries the reason through so the rendered sentence says
    the platform could not be read, rather than that it is empty.
    """
    from resource_explorer.surveyors.database.column_matching import (
        MATCH_NO_CANDIDATES,
    )

    if match.verdict == MATCH_NOT_SAMPLED:
        # Not sampled outranks not-compared: with no values there was nothing
        # to compare regardless of what the platform holds.
        return match
    match.verdict = MATCH_NO_CANDIDATES
    match.confidence = 0
    match.not_established_reason = catalog.unavailable_reason or (
        "the Egeria platform's reference elements could not be read"
    )
    match.matched_guid = ""
    match.matched_qualified_name = ""
    match.matched_display_name = ""
    match.sampled_conformance = None
    match.value_coverage = None
    match.proposed_specification = ""
    match.proposed_values = []
    match.detected_patterns = []
    return match


def _profile_row(
    schema_name: str,
    table_name: str,
    column_name: str,
    slice_seven_profile: dict,
    provenance: SampleProvenance,
    supports_sampling: bool,
) -> dict:
    """One `database_column_profiles` row carrying this step's sampling
    provenance.

    Written as its own row keyed `(slug, surveyed_at, source)` like every
    other detail row, and deliberately NOT a mutation of slice 7's row: slice
    7's numbers come from `pg_stats` (the database's own statistics, possibly
    months old) and this step's come from a sample RE took just now. Merging
    them into one row would make `stats_source` a lie for half the columns.
    The `sample_*` columns already exist on the table (stream 3 added them for
    exactly this slice); `sample_total_rows` is added by this slice.
    """
    if provenance.not_applicable:
        # §F: checked before `supports_sampling` — a view's column is not
        # applicable to TABLESAMPLE regardless of what the engine can do.
        state = STATE_NOT_APPLICABLE
    elif not supports_sampling:
        state = STATE_NOT_SUPPORTED
    elif provenance.reads_values and provenance.sample_rows is not None:
        state = STATE_MEASURED
    else:
        state = STATE_NOT_COLLECTED
    return {
        "schema_name": schema_name,
        "table_name": table_name,
        "column_name": column_name,
        # Carried through unchanged so a consumer reading this row sees both
        # halves — the catalogue's own statistics and this step's sample.
        "null_fraction": slice_seven_profile.get("null_fraction"),
        "distinct_count": slice_seven_profile.get("distinct_count"),
        "average_width": slice_seven_profile.get("average_width"),
        "correlation": slice_seven_profile.get("correlation"),
        "stats_source": slice_seven_profile.get("stats_source") or "",
        "stats_computed_at": slice_seven_profile.get("stats_computed_at"),
        "sample_strategy": provenance.strategy,
        "sample_rows": provenance.sample_rows,
        "sample_seed": provenance.seed,
        "sample_total_rows": provenance.total_rows,
        "state": state,
    }


def publish_proposals(
    matches: Sequence[ColumnMatch],
    *,
    designer=None,
    ref_manager=None,
    discovery=None,
    annotation_guids_by_item: dict[str, str] | None = None,
) -> list[dict]:
    """Create a DRAFT candidate element for every proposable match.

    Separate from `run_column_profile` because it WRITES to Egeria, and the
    step's own analysis must be runnable (and testable) without any write at
    all. A caller with no clients gets `[]` and the RFA fallback path has
    already produced the annotations that carry the proposals instead.
    """
    from resource_explorer.surveyors.database.egeria_reference_catalog import (
        matches_needing_proposals,
        publish_proposed_data_class,
        publish_proposed_valid_value_set,
    )

    guids = annotation_guids_by_item or {}
    results: list[dict] = []
    for match in matches_needing_proposals(matches):
        annotation_guid = guids.get(match.column_path, "")
        if match.question == "data_class_match":
            if designer is None:
                continue
            result = publish_proposed_data_class(
                designer, match, discovery=discovery, annotation_guid=annotation_guid,
            )
        else:
            if ref_manager is None:
                continue
            result = publish_proposed_valid_value_set(
                ref_manager, match, discovery=discovery, annotation_guid=annotation_guid,
            )
        results.append(result.as_dict())
    return results
