"""Database surveyor for custom database surveys."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

log = logging.getLogger(__name__)

from resource_explorer.registry import (
    DatabaseEntity,
    ProjectRegistry,
    SECTION_COLUMN_PROFILES,
    SECTION_TABLE_ACTIVITY,
    STATE_MEASURED,
    STATE_NOT_COLLECTED,
    STATE_NOT_SUPPORTED,
    STATS_SOURCE_DATABASE,
)
from resource_explorer.surveyors.survey_report import (
    AnnotationType,
    RequestForActionAnnotation,
    ResourceMeasureAnnotation,
    ResourcePhysicalStatusAnnotation,
    SchemaAnalysisAnnotation,
)

from .connection import EngineCapabilities, NO_CAPABILITIES, database_connection

# analysis_catalog.yaml database entry id -> DatabaseSurveyor.survey() steps,
# for the subset of database analyses that need an open connection to run at
# all (a DatabaseSurveyor.survey() call). Database per-card dispatch fix (D6
# prerequisite, repo-scope-narrowing-funnel plan) — closes the gap where every
# local database analysis card triggered the identical whole-DB survey()
# regardless of which was clicked. "schema" always runs (see
# DatabaseSurveyor._ALL_STEPS's comment), so schema_inventory/row_count_
# snapshot genuinely diverge in what extra work they do (views vs.
# statistics), not just in label.
#
# privilege_audit: had no dedicated check before Phase 1 slice 8
# (confirmed — "database-only, aspirational" per the target-shape audit),
# so it used to run the full survey (schema+statistics+views) as a
# fallback with no dedicated queries backing it at all. It now maps to the
# "operations" step (postgres_operations, design §5.7), which reads
# pg_roles/role_table_grants/pg_default_acl directly and raises an RFA on
# PUBLIC grants — see DatabaseSurveyor._survey_operations().
# db_activity_signals/db_resilience/db_external_dependencies are new
# catalog ids the same step now backs; each also needs "schema" for
# _survey_operations()'s activity-signals table count, which is why
# "schema" appears in their step lists too, not just because the shared
# invariant below forces it regardless.
#
# Deliberately does NOT carry the zero-fetch `db_derived`-backed ids
# (db_classification, subject_signals, …) — those never call
# DatabaseSurveyor.survey() at all (see db_derived.py's own module
# docstring: it opens no connection, to the database or to Egeria), so a
# "which DatabaseSurveyor steps does X need" map has nothing to say about
# them. Every caller of this map (below, plus web/routes/databases.py,
# workflows/analysis.py, scheduler.py) checks `analysis_id in
# db_derived.DB_DERIVED_ANALYSES` FIRST and only falls through to this map
# for what remains — that membership check, not this map, is what used to
# be missing for `subject_signals`/`coverage_signals`/`preliminary_fit`
# wherever a caller forgot it (see `survey_definition_adapter.py`'s
# `DATABASE_ANALYSIS_RE_STEP_MAP`, now derived from `DB_DERIVED_ANALYSES`
# rather than hand-listed a second time, for exactly that reason).
#
# Renamed from `DATABASE_ANALYSIS_STEP_MAP` (slice 17,
# docs/design-notes/SLICE-17-RUNNABILITY-FROM-CATALOG-IMPLEMENTED.md) — that
# name was shared, coincidentally, with a SEPARATE hand-maintained dict in
# `survey_definition_adapter.py` with a different shape (re_analysis_step
# keys, not DatabaseSurveyor.survey() step names) and a different set of
# consumers. The two were never the same data and updating one while
# forgetting the other is exactly the bug REVIEW-SURVEY-PANE-285.md §5(a)
# found — distinct names make that mistake harder to make by accident.
DATABASE_SURVEYOR_STEP_MAP: dict[str, list[str]] = {
    "schema_inventory": ["schema", "views"],
    "row_count_snapshot": ["schema", "statistics"],
    "privilege_audit": ["schema", "operations"],
    "db_activity_signals": ["schema", "operations"],
    "db_resilience": ["schema", "operations"],
    "db_external_dependencies": ["schema", "operations"],
    # Phase 1 slice 10 (postgres_column_profile, design §5.4/§5.7/§5.8). Both
    # need "statistics" as well as "schema": the cardinality gate for
    # reference_data_match reads slice 7's stored pg_stats n_distinct, and the
    # sample's provenance needs the table row counts "statistics" collects.
    # Sampling itself is the "column_profile" step.
    "data_class_match": ["schema", "statistics", "column_profile"],
    "reference_data_match": ["schema", "statistics", "column_profile"],
    # Phase 1 slice 11 (postgres_nested_columns, design §5.4/§5.7). "schema"
    # for the JSON/JSONB/XML column catalog to iterate, "statistics" for the
    # same per-table row counts the sample's provenance is stated against —
    # exactly slice 10's reasoning above, not a new invariant.
    "nested_column_profile": ["schema", "statistics", "nested_columns"],
    # credential_capability (design REPLY-DATABASE-CREDENTIAL-CAPABILITY-
    # VISIBILITY.md §3/§4, replying to ASK-...-#251): opt-in, same shape as
    # "operations" above — "schema" is not actually read by this step, but
    # every other database step needs it and the shared invariant in
    # DatabaseSurveyor.survey() adds it regardless, so listing it here keeps
    # this map an honest description of what actually runs.
    "credential_capability": ["schema", "credential_capability"],
}


def _parse_pg_array(value) -> list | None:
    """Parse a Postgres array's text representation (`{a,b,c}`) to a list.

    Returns None for a genuinely absent value (NULL, or the column is not
    array-typed for this stat) so "not reported" survives — an empty list
    is a different, real answer (pg_stats can validly report `{}`).
    """
    if value is None or value == "":
        return None
    if isinstance(value, (list, tuple)):
        return list(value)
    text = str(value).strip()
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]
    if not text:
        return []
    return [part.strip().strip('"') for part in text.split(",")]


#: `_resolve_n_distinct`'s reason vocabulary — why `distinct_count` came back
#: `None`, per round 3's design review (§3, "the corrected `distinct_count`,
#: and the blank the correction produced"). A row's `state` already says
#: *whether* a value is measured; these say *why one specific field* is
#: absent when the row around it is otherwise `STATE_MEASURED` — row-level
#: state cannot carry a per-field absence, which is exactly what produced the
#: unexplained blank the review flagged. Three of the four end in the same
#: card action ("run ANALYZE"); `REASON_STATS_RESET` deliberately does not,
#: because ANALYZE is not the problem there — see each constant's docstring.
REASON_NOT_COLLECTED = "not_collected"
"""`n_distinct` itself is null in `pg_stats` for this column — the stat was
never collected (e.g. a statistics target of 0). Card sentence: "not
collected for this column — run ANALYZE"."""

REASON_NEVER_ANALYZED = "never_analyzed"
"""The table has never been ANALYZEd: `reltuples < 0` (PG14+'s explicit
sentinel), or `reltuples == 0` with no evidence a reset happened instead
(PG13's ambiguous case, folded into the same reason so the card never
guesses "empty"). Card sentence: "the table has never been ANALYZEd — run
ANALYZE"."""

REASON_STATS_RESET = "stats_reset"
"""`reltuples == 0` and `ever_analyzed` reads false, but `stats_reset` shows
a reset happened — the table WAS analyzed before that reset, so "never
analyzed" would be the wrong read. Distinct count is not resolvable right
now, but the other figures on the row predate the reset and stand. Card
sentence: "distinct count not resolvable — table statistics were reset on
<date>; the other figures predate the reset and stand." **No ANALYZE
prompt** — ANALYZE is not the problem here."""


@dataclass(frozen=True)
class NDistinctResolution:
    """`_resolve_n_distinct`'s return shape: the resolved count (or `None`),
    plus — when it is `None` — which of the four cases produced that,
    from `REASON_*` above. Collapsing straight back to `float | None` is
    exactly the bug design review round 3 §3 found: three of the four
    `None` cases want "run ANALYZE" and one wants no action at all, and a
    bare `None` cannot tell a caller which.
    """

    distinct_count: float | None
    reason: str | None


def _distinct_count_reason_sentence(reason: str | None, stats_reset=None) -> str:
    """The card sentence for one `REASON_*` code (round 3 design review §3's
    table, verbatim). Shared between the annotation `explanation` built here
    and, eventually, whatever UI reads `distinct_count_reason` off the
    stored row — one place so the four sentences cannot drift apart.
    """
    if reason == REASON_NOT_COLLECTED:
        return "Not collected for this column — run ANALYZE."
    if reason == REASON_NEVER_ANALYZED:
        return "The table has never been ANALYZEd — run ANALYZE."
    if reason == REASON_STATS_RESET:
        when = stats_reset or "an unknown time"
        return (
            f"Distinct count not resolvable — table statistics were reset "
            f"on {when}; the other figures predate the reset and stand. "
            "No ANALYZE needed — ANALYZE is not the problem here."
        )
    return ""


def _resolve_n_distinct(
    n_distinct, reltuples, ever_analyzed=None, stats_reset=None
) -> NDistinctResolution:
    """Resolve `pg_stats.n_distinct`'s sign convention into an actual
    estimated distinct-value count.

    Postgres overloads this one column: a non-negative value is an absolute
    estimated count of distinct values; a NEGATIVE value is `-(distinct
    values / row count)` — a ratio, used when the planner expects the
    distinct count to scale with table size (e.g. a near-unique column).
    Passing a negative ratio through as if it were a count renders as a
    negative cardinality, which is never a real answer (found live,
    2026-09-21, design review round 2 — the shipped `REAL distinct_count`
    would have rendered "distinct: -0.8" for exactly this case).

    The row count MUST be `pg_class.reltuples` — the estimate from the same
    `ANALYZE` run that produced `n_distinct` — not a live tuple count read
    separately (a second design-review finding, same day: multiplying an
    analyze-time ratio by a since-drifted live count is an internally
    inconsistent number, even with the sign fixed. "Correct number, wrong
    label" wears a third shape here: computed at all, from the wrong
    denominator).

    `reltuples == -1` is Postgres's own "never analyzed" placeholder, but
    **only from PG14 on**. On PG13 and earlier, `0` means BOTH "analyzed,
    genuinely empty" and "never analyzed" — a table with real rows that has
    never been ANALYZEd would otherwise resolve every column's distinct
    count to a confident, wrong 0 (a third design-review finding, same
    day). `ever_analyzed` disambiguates this version-independently: pass
    whether this table has a non-null `last_analyze`/`last_autoanalyze`
    (already read by this module's caller) rather than trusting the
    server-version-dependent sentinel alone. `ever_analyzed=None` (unknown)
    is treated the same as `False` — refuse to guess.

    `stats_reset` disambiguates the PG13 `reltuples == 0` / not-analyzed
    case one step further (round 3 design review, §3). `ever_analyzed`
    comes from `pg_stat_user_tables.last_analyze`/`last_autoanalyze`, which
    `pg_stat_reset()` (or an equivalent counters reset) clears — while the
    `pg_stats` row `n_distinct` came from, and `pg_class.reltuples`, are
    untouched by it. So a table that was genuinely analyzed, then had its
    statistics reset, reads exactly like "never analyzed": `ever_analyzed`
    is false and `reltuples` reads 0. Passing `stats_reset` (this module's
    existing `get_stats_reset()`/`stats_info["stats_reset"]` value, already
    read at :699/:416 for the change-comparator and RFA uses) lets this
    function tell the two apart: when it is present, a reset is known to
    have happened, so "never analyzed" is not the right conclusion — see
    `REASON_STATS_RESET`. There is no per-table reset timestamp to compare
    against (`stats_reset` is database-wide, from `pg_stat_database`), so
    presence is the whole test — the same convention this file already uses
    to present the value (`stats_reset or "reset time unknown"`, the
    unused-index RFA below) rather than diffing it against a threshold.
    """
    if n_distinct is None:
        return NDistinctResolution(None, REASON_NOT_COLLECTED)
    if n_distinct >= 0:
        return NDistinctResolution(n_distinct, None)
    if reltuples is None or reltuples < 0:
        return NDistinctResolution(None, REASON_NEVER_ANALYZED)
    if reltuples == 0 and not ever_analyzed:
        if stats_reset:
            return NDistinctResolution(None, REASON_STATS_RESET)
        return NDistinctResolution(None, REASON_NEVER_ANALYZED)
    return NDistinctResolution(abs(n_distinct) * reltuples, None)


class DatabaseSurveyor:
    """Custom surveyor for databases when Egeria can't access them directly."""

    def __init__(
        self,
        db_entity: DatabaseEntity,
        credentials: dict,
        registry: ProjectRegistry,
    ) -> None:
        """Initialize database surveyor.
        
        Args:
            db_entity: DatabaseEntity with connection details
            credentials: Dict with 'user' and 'password' keys
            registry: ProjectRegistry for storing results
        """
        self.db_entity = db_entity
        self.credentials = credentials
        self.registry = registry

    # Every step other than "schema" reads from schema_info to attach its
    # results (row counts onto tables, view dependencies onto tables/views),
    # so "schema" always runs regardless of what's requested — it's the
    # structural backbone every other step is enrichment on top of, not an
    # independently optional step. Database per-card dispatch fix (D6
    # prerequisite, repo-scope-narrowing-funnel plan) — see
    # DATABASE_SURVEYOR_STEP_MAP (this module, used by web/routes/databases.py)
    # for the analysis_id -> steps mapping this enables.
    #
    # "operations" (Phase 1 slice 8, postgres_operations) is deliberately
    # NOT in _ALL_STEPS: unlike statistics/views, its four constituent
    # analyses (privilege_audit, db_activity_signals, db_resilience,
    # db_external_dependencies) are read directly from pg_roles/pg_stat_*/
    # pg_settings/pg_extension — an "api / low" cost per design §5.7 that
    # should not silently ride along on every default full survey(). It
    # only runs when explicitly requested (DATABASE_SURVEYOR_STEP_MAP or the
    # postgres_operations adapter entry point), same opt-in shape "views"
    # already had before this slice.
    _ALL_STEPS = ("schema", "statistics", "views")

    def survey(
        self,
        steps: list[str] | None = None,
        sampling_overrides: dict | None = None,
        reference_catalog=None,
        read_egeria_catalog: bool = True,
    ) -> dict:
        """Run a database survey.

        steps : optional subset of {"schema", "statistics", "views",
            "operations", "column_profile", "nested_columns",
            "credential_capability"} — None (default)
            runs the original three (_ALL_STEPS), exactly as before
            "operations" existed; "operations" must be requested explicitly
            (see _ALL_STEPS's comment). "schema" always runs even if
            omitted, since every other step's results are meaningless (or,
            for "operations", just less complete — see
            _survey_operations()) without the table list schema produces.

        reference_catalog : an already-loaded `ReferenceCatalog` (see
            `egeria_reference_catalog.load_reference_catalog`) to hand to the
            "column_profile" step for `data_class_match`/`reference_data_match`.
            When `None` (the default) and "column_profile" is among the
            requested steps, this method loads one itself — see the note
            below. Pass an explicit catalog to avoid a second read when a
            caller (e.g. `survey_definition_adapter._run_postgres_column_profile`)
            has already loaded one for the same run.

        read_egeria_catalog : when the caller has not supplied a
            `reference_catalog` and "column_profile" is requested, whether to
            read the platform's Data Classes / Valid Value Sets at all.
            Mirrors `_run_postgres_column_profile`'s parameter of the same
            name (design §5.4/§5.8): both entry points into
            `postgres_column_profile` — the Survey Definition executor and
            this survey() method, which backs the classic UI's per-analysis
            "Run" button (`web/routes/databases.py`'s
            `run_single_database_analysis`) — must load the SAME
            `ReferenceCatalog` the same way, via
            `egeria_reference_catalog.load_reference_catalog`, or a column's
            data-class verdict depends on which door the run came through.
            Before this, `survey()` accepted a `reference_catalog` parameter
            that no caller ever passed, so every "Run →" click on
            `data_class_match`/`reference_data_match` silently ran with
            `reference_catalog=None` — column_profile_step then treats an
            absent catalog as "we did not read the platform's Data Classes",
            which is `MATCH_NO_CANDIDATES`/"not established" for every
            column, never a real match. Set to False only where a caller
            deliberately wants that no-read behaviour (e.g. an offline test).

        Returns:
            Dict with survey results including annotations and statistics
        """
        requested = set(steps) if steps is not None else set(self._ALL_STEPS)
        requested.add("schema")
        # "column_profile" (Phase 1 slice 10) and "nested_columns" (Phase 1
        # slice 11) each read two things "statistics" produces and nothing
        # else does: the per-table row counts their sample provenance is
        # stated against (design §5.8's "of 4.2M rows"), and, for
        # column_profile, slice 7's stored pg_stats `n_distinct` (the
        # reference_data_match cardinality gate). Requesting either without
        # the statistics would silently produce every column's total_rows as
        # "not established" — a run that looks like it worked and
        # establishes nothing. Same invariant, and same reasoning, as
        # "schema" above.
        if "column_profile" in requested or "nested_columns" in requested:
            requested.add("statistics")

        # Load the platform's Data Classes / Valid Value Sets here, not only
        # in survey_definition_adapter._run_postgres_column_profile — see
        # this method's docstring. Both entry points share ONE loading
        # function (`load_reference_catalog`) rather than each re-reading the
        # platform their own way; this is the difference between the two
        # doors and one lock versus two locks that can drift.
        catalog_load_error = ""
        if (
            "column_profile" in requested
            and reference_catalog is None
            and read_egeria_catalog
        ):
            try:
                from resource_explorer.surveyors.database.egeria_reference_catalog import (
                    build_reference_clients,
                    load_reference_catalog,
                )

                designer, ref_manager = build_reference_clients()
                reference_catalog = load_reference_catalog(designer, ref_manager)
            except Exception as exc:
                # Non-fatal, same as _run_postgres_column_profile's identical
                # try/except: a failed read must surface as an explicitly
                # unavailable ReferenceCatalog (carrying the reason), not as
                # an empty one silently treated as "the platform holds none".
                catalog_load_error = str(exc)
                log.warning(
                    "Could not read Egeria's Data Classes / Valid Value Sets "
                    "for %s (survey() column_profile path): %s",
                    self.db_entity.slug, exc,
                )

        results = {
            "database_slug": self.db_entity.slug,
            "surveyed_at": datetime.utcnow().isoformat(),
            "source": "custom",
            "annotations": [],
            "schema_info": {},
            "statistics": {},
            "errors": [],
            #: design §5.1's capability declaration, captured while the
            #: connection is open (this dict is the only thing that survives
            #: past the `with` block below). Defaults to "nothing declared"
            #: so a caller never sees this key missing.
            "engine_capabilities": NO_CAPABILITIES.as_dict(),
            #: Rows ready for registry.write_detail_rows(), built only when
            #: "statistics" runs — see _survey_extended_statistics().
            "column_profile_rows": [],
            "table_activity_rows": [],
            #: postgres_operations output (Phase 1 slice 8), built only when
            #: "operations" runs — see _survey_operations().
            "operations": {},
            #: postgres_column_profile output (Phase 1 slice 10), built only
            #: when "column_profile" runs — see column_profile_step.py.
            "column_profile": {},
            #: postgres_nested_columns output (Phase 1 slice 11), built only
            #: when "nested_columns" runs — see nested_columns_step.py.
            "nested_columns": {},
            #: credential_capability output (design REPLY-DATABASE-
            #: CREDENTIAL-CAPABILITY-VISIBILITY.md §3/§4), built only when
            #: "credential_capability" runs — see
            #: _survey_credential_capability().
            "credential_capability": {},
        }
        if catalog_load_error:
            results["errors"].append(
                f"Could not read Egeria's Data Classes / Valid Value Sets "
                f"(non-fatal): {catalog_load_error}"
            )

        try:
            with database_connection(self.db_entity, self.credentials) as conn:
                capabilities = getattr(conn, "capabilities", NO_CAPABILITIES)
                results["engine_capabilities"] = capabilities.as_dict()

                # Survey schema — always runs, see _ALL_STEPS's comment.
                schema_info = self._survey_schema(conn)
                results["schema_info"] = schema_info
                results["annotations"].extend(
                    self._create_schema_annotations(schema_info)
                )

                # Survey statistics — non-fatal; proceed even if stats fail
                if "statistics" in requested:
                    try:
                        stats_info = self._survey_statistics(conn)
                        results["statistics"] = stats_info
                        results["annotations"].extend(
                            self._create_statistics_annotations(stats_info)
                        )
                        # pg_stats / pg_stat_user_tables / index usage
                        # extension (design §5.1, §5.7 — Phase 1 slice 7).
                        # Kept separate from _create_statistics_annotations
                        # because it also needs to know the full column/table
                        # catalog (schema_info) to tell "never analyzed" from
                        # "genuinely has no stats" — the other method never
                        # needed the catalog before now.
                        profile_rows, activity_rows, extended_annotations = (
                            self._survey_extended_statistics(
                                schema_info, stats_info, capabilities
                            )
                        )
                        results["column_profile_rows"] = profile_rows
                        results["table_activity_rows"] = activity_rows
                        results["annotations"].extend(extended_annotations)
                    except Exception as stats_err:
                        results["errors"].append(f"Statistics query failed (non-fatal): {stats_err}")

                # SQL views static analysis using SQLGlot (non-fatal)
                if "views" in requested:
                    try:
                        views_info = self._survey_views(conn, schema_info)
                        results["views"] = views_info
                        results["annotations"].extend(
                            self._create_views_annotations(views_info)
                        )
                    except Exception as views_err:
                        results["errors"].append(f"SQL view static analysis failed (non-fatal): {views_err}")

                # postgres_operations: privilege_audit, db_activity_signals,
                # db_resilience, db_external_dependencies (design §5.5, §5.7
                # — Phase 1 slice 8). Non-fatal, same shape as statistics/
                # views above.
                if "operations" in requested:
                    try:
                        operations_info = self._survey_operations(
                            conn, capabilities, schema_info
                        )
                        results["operations"] = operations_info
                        results["annotations"].extend(
                            self._create_operations_annotations(operations_info)
                        )
                    except Exception as ops_err:
                        results["errors"].append(f"Operations query failed (non-fatal): {ops_err}")

                # credential_capability (design REPLY-DATABASE-CREDENTIAL-
                # CAPABILITY-VISIBILITY.md §3/§4, replying to ASK-...-#251):
                # read-only catalog/privilege introspection of what THIS
                # connection can see and do. Non-fatal, opt-in — same shape
                # as "operations" above.
                if "credential_capability" in requested:
                    try:
                        cred_info = self._survey_credential_capability(conn, capabilities)
                        results["credential_capability"] = cred_info or {}
                        results["annotations"].extend(
                            self._create_credential_capability_annotations(cred_info)
                        )
                    except Exception as cred_err:
                        results["errors"].append(
                            f"Credential-capability query failed (non-fatal): {cred_err}"
                        )

                # postgres_column_profile: value sampling, data_class_match,
                # reference_data_match (design §5.4, §5.7, §5.8 — Phase 1
                # slice 10). Non-fatal, same shape as the three above, and
                # opt-in for the same reason "operations" is: design §5.7
                # prices it at "api_heavy / medium", the only step in the
                # family that reads actual table data, so it must never ride
                # along on a default full survey().
                #
                # Delegated to column_profile_step rather than implemented
                # here: slices 7 and 8 own this file, and slice 10 consumes
                # their output (schema_info's column catalog, statistics'
                # row counts, and column_profile_rows' pg_stats n_distinct)
                # rather than extending their code.
                if "column_profile" in requested:
                    try:
                        from resource_explorer.surveyors.database.column_profile_step import (
                            run_column_profile,
                        )

                        profile_result = run_column_profile(
                            conn,
                            capabilities,
                            schema_info,
                            results.get("statistics") or {},
                            results.get("column_profile_rows") or [],
                            resource_slug=self.db_entity.slug,
                            reference_catalog=reference_catalog,
                            sampling_overrides=sampling_overrides,
                        )
                        results["column_profile"] = profile_result
                        results["annotations"].extend(profile_result["annotations"])
                        # This step's rows are ADDITIONAL rows, not edits to
                        # slice 7's — see _profile_row()'s docstring for why
                        # merging them would make `stats_source` wrong.
                        results["column_sample_rows"] = profile_result["column_profile_rows"]
                    except Exception as profile_err:
                        results["errors"].append(
                            f"Column profiling failed (non-fatal): {profile_err}"
                        )

                # postgres_nested_columns: JSONB/JSON/XML value sampling and
                # nested-schema inference (design §5.4, §5.7 — Phase 1 slice
                # 11). Same opt-in shape as "column_profile" above and gated
                # on it per the coordinator brief ("shares the inference
                # core") — it reuses that step's own sample_column_values/
                # SamplingBudget rather than a second sampling
                # implementation; see nested_columns_step.py.
                if "nested_columns" in requested:
                    try:
                        from resource_explorer.surveyors.database.nested_columns_step import (
                            run_nested_columns,
                        )

                        nested_result = run_nested_columns(
                            conn,
                            capabilities,
                            schema_info,
                            results.get("statistics") or {},
                            resource_slug=self.db_entity.slug,
                            sampling_overrides=sampling_overrides,
                        )
                        results["nested_columns"] = nested_result["nested_columns"]
                        results["annotations"].extend(nested_result["annotations"])
                    except Exception as nested_err:
                        results["errors"].append(
                            f"Nested column profiling failed (non-fatal): {nested_err}"
                        )

        except Exception as e:
            error_msg = str(e)
            results["errors"].append(error_msg)
            from resource_explorer.registry import ProjectStatus
            self.registry.update_database_status(
                self.db_entity.slug, ProjectStatus.ERROR, error_msg
            )
            # Re-raise so the caller can detect failure and return status="error"
            raise

        # Store results regardless of non-fatal errors (stats failures etc.)
        self._store_results(results)
        return results

    def _survey_schema(self, conn) -> dict:
        """Survey database schema structure."""
        return conn.get_schema_info()

    def _survey_statistics(self, conn) -> dict:
        """Survey database statistics."""
        return conn.get_statistics()

    def _survey_extended_statistics(
        self,
        schema_info: dict,
        stats_info: dict,
        capabilities: EngineCapabilities,
    ) -> tuple[list[dict], list[dict], list]:
        """`pg_stats` column profiling, `pg_stat_user_tables` tuple counters
        and index-usage detection (design §5.1, §5.7 — Phase 1 slice 7).

        Iterates the column/table catalog from `schema_info` rather than the
        raw query results from `stats_info`, so a column or table that the
        catalog knows about but the statistics query has no row for is
        recognised as *absent from statistics* — the "ANALYZE never ran"
        case — rather than simply not appearing in the output at all. This
        is the distinction design §5.1 calls out by name: "the `pg_stats`
        route also has a second absence mode, 'stats never collected', which
        must render as 'run ANALYZE' rather than as 'no values'".

        Returns `(column_profile_rows, table_activity_rows, annotations)`.
        The rows are shaped exactly like `database_column_profiles` /
        `database_table_activity` (see `result_materializer.py`'s identical
        native-path shape) so `_store_results` can hand them to
        `registry.write_detail_rows` unchanged.
        """
        profile_rows: list[dict] = []
        activity_rows: list[dict] = []
        annotations: list = []

        stats_reset = stats_info.get("stats_reset") or None

        column_stats_by_key = {
            (r.get("schemaname", ""), r.get("tablename", ""), r.get("attname", "")): r
            for r in (stats_info.get("column_stats") or [])
        }
        activity_by_key = {
            (r.get("schemaname", ""), r.get("tablename", "")): r
            for r in (stats_info.get("table_activity") or [])
        }

        if not capabilities.column_stats:
            annotations.append(
                ResourceMeasureAnnotation(
                    summary="Column profiling (pg_stats) not supported by this engine",
                    analysis_step="DatabaseSchemaAndStats",
                    confidence=0,
                    resource_properties={"capability": "column_stats", "supported": False},
                    explanation=(
                        "This connection's engine capability declaration does not "
                        "include column_stats — the finding is not established, "
                        "not a measurement of zero columns."
                    ),
                )
            )
        if not capabilities.tuple_counters:
            annotations.append(
                ResourceMeasureAnnotation(
                    summary="Table activity (pg_stat_user_tables) not supported by this engine",
                    analysis_step="DatabaseSchemaAndStats",
                    confidence=0,
                    resource_properties={"capability": "tuple_counters", "supported": False},
                    explanation=(
                        "This connection's engine capability declaration does not "
                        "include tuple_counters — the finding is not established, "
                        "not a measurement of zero activity."
                    ),
                )
            )

        for schema in schema_info.get("schemas", []):
            schema_name = schema.get("name", "")
            for table in schema.get("tables", []):
                table_name = table.get("name", "")

                # ── table activity (tuple counters, dead/live rows, scans) ──
                if capabilities.tuple_counters:
                    activity = activity_by_key.get((schema_name, table_name))
                    if activity is None:
                        activity_rows.append({
                            "schema_name": schema_name,
                            "table_name": table_name,
                            "state": STATE_NOT_COLLECTED,
                        })
                    else:
                        activity_rows.append({
                            "schema_name": schema_name,
                            "table_name": table_name,
                            "rows_inserted": activity.get("rows_inserted"),
                            "rows_updated": activity.get("rows_updated"),
                            "rows_deleted": activity.get("rows_deleted"),
                            "hot_updates": activity.get("hot_updates"),
                            "live_tuples": activity.get("live_tuples"),
                            "dead_tuples": activity.get("dead_tuples"),
                            "seq_scan": activity.get("seq_scan"),
                            "idx_scan": activity.get("idx_scan"),
                            "last_vacuum": activity.get("last_vacuum", ""),
                            "last_autovacuum": activity.get("last_autovacuum", ""),
                            "last_analyze": activity.get("last_analyze", ""),
                            "last_autoanalyze": activity.get("last_autoanalyze", ""),
                            "pending_changes": activity.get("pending_changes"),
                            "stats_reset": stats_reset,
                            "state": STATE_MEASURED,
                        })
                        annotations.append(
                            ResourceMeasureAnnotation(
                                summary=(
                                    f"Table {schema_name}.{table_name} activity: "
                                    f"{activity.get('live_tuples')} live / "
                                    f"{activity.get('dead_tuples')} dead tuples, "
                                    f"seq_scan={activity.get('seq_scan')} "
                                    f"idx_scan={activity.get('idx_scan')}"
                                ),
                                analysis_step="DatabaseSchemaAndStats",
                                confidence=100,
                                resource_properties={
                                    "schema": schema_name,
                                    "table": table_name,
                                    **{
                                        k: activity.get(k)
                                        for k in (
                                            "rows_inserted", "rows_updated", "rows_deleted",
                                            "hot_updates", "live_tuples", "dead_tuples",
                                            "seq_scan", "idx_scan",
                                        )
                                    },
                                },
                                explanation=(
                                    "Cumulative tuple counters and scan counts since the "
                                    "last statistics reset, read directly from "
                                    "pg_stat_user_tables."
                                ),
                            )
                        )
                else:
                    activity_rows.append({
                        "schema_name": schema_name,
                        "table_name": table_name,
                        "state": STATE_NOT_SUPPORTED,
                    })

                # ── column profiling (pg_stats) ──
                for column in table.get("columns", []):
                    column_name = column.get("name", "")
                    if not capabilities.column_stats:
                        profile_rows.append({
                            "schema_name": schema_name,
                            "table_name": table_name,
                            "column_name": column_name,
                            "state": STATE_NOT_SUPPORTED,
                        })
                        continue

                    stat = column_stats_by_key.get((schema_name, table_name, column_name))
                    if stat is None:
                        # In the catalog, absent from pg_stats: ANALYZE has
                        # never run for this table (or this column has a
                        # statistics target of 0) — "run ANALYZE", not
                        # "no values". See STATE_NOT_COLLECTED's docstring.
                        profile_rows.append({
                            "schema_name": schema_name,
                            "table_name": table_name,
                            "column_name": column_name,
                            "stats_source": STATS_SOURCE_DATABASE,
                            "stats_computed_at": None,
                            "state": STATE_NOT_COLLECTED,
                        })
                        continue

                    last_analyzed = None
                    activity = activity_by_key.get((schema_name, table_name))
                    if activity:
                        last_analyzed = activity.get("last_analyze") or activity.get("last_autoanalyze") or None

                    n_distinct_raw = stat.get("n_distinct")
                    n_distinct_resolution = _resolve_n_distinct(
                        n_distinct_raw,
                        stat.get("reltuples"),
                        ever_analyzed=bool(last_analyzed),
                        stats_reset=stats_reset,
                    )
                    distinct_count = n_distinct_resolution.distinct_count
                    distinct_count_reason = n_distinct_resolution.reason

                    profile_rows.append({
                        "schema_name": schema_name,
                        "table_name": table_name,
                        "column_name": column_name,
                        "null_fraction": stat.get("null_frac"),
                        "distinct_count": distinct_count,
                        "distinct_count_reason": distinct_count_reason,
                        "average_width": stat.get("avg_width"),
                        "correlation": stat.get("correlation"),
                        "most_common_values_json": _parse_pg_array(stat.get("most_common_vals")),
                        "most_common_freqs_json": _parse_pg_array(stat.get("most_common_freqs")),
                        "histogram_bounds_json": _parse_pg_array(stat.get("histogram_bounds")),
                        "stats_source": STATS_SOURCE_DATABASE,
                        "stats_computed_at": last_analyzed,
                        "state": STATE_MEASURED,
                    })
                    annotations.append(
                        ResourceMeasureAnnotation(
                            summary=(
                                f"Column {schema_name}.{table_name}.{column_name} profile: "
                                f"null_frac={stat.get('null_frac')}, "
                                f"distinct_count={distinct_count}"
                            ),
                            analysis_step="DatabaseSchemaAndStats",
                            confidence=100,
                            resource_properties={
                                "schema": schema_name,
                                "table": table_name,
                                "column": column_name,
                                "null_frac": stat.get("null_frac"),
                                "n_distinct": n_distinct_raw,
                                "distinct_count": distinct_count,
                                "distinct_count_reason": distinct_count_reason,
                                "avg_width": stat.get("avg_width"),
                                "correlation": stat.get("correlation"),
                            },
                            explanation=(
                                _distinct_count_reason_sentence(distinct_count_reason, stats_reset)
                                if distinct_count_reason
                                else (
                                    "Column profile read from pg_stats, populated by the "
                                    "database's own ANALYZE — no sampling performed by "
                                    "Resource Explorer."
                                )
                            ),
                        )
                    )

        # ── index usage (pg_stat_user_indexes / pg_index) ──
        if capabilities.index_stats:
            for idx in stats_info.get("index_stats") or []:
                schema_name = idx.get("schemaname", "")
                table_name = idx.get("tablename", "")
                index_name = idx.get("indexrelname", "")
                idx_scan = idx.get("idx_scan")
                is_primary = bool(idx.get("is_primary"))
                annotations.append(
                    ResourceMeasureAnnotation(
                        summary=f"Index {schema_name}.{index_name} on {table_name}: idx_scan={idx_scan}",
                        analysis_step="DatabaseSchemaAndStats",
                        confidence=100,
                        resource_properties={
                            "schema": schema_name,
                            "table": table_name,
                            "index": index_name,
                            "idx_scan": idx_scan,
                            "idx_tup_read": idx.get("idx_tup_read"),
                            "idx_tup_fetch": idx.get("idx_tup_fetch"),
                            "is_unique": bool(idx.get("is_unique")),
                            "is_primary": is_primary,
                            "index_size_bytes": idx.get("index_size_bytes"),
                        },
                        explanation=(
                            "Index scan count since the last statistics reset, read "
                            "directly from pg_stat_user_indexes/pg_index."
                        ),
                    )
                )
                if idx_scan == 0 and not is_primary:
                    annotations.append(
                        RequestForActionAnnotation(
                            summary=f"Unused index candidate: {schema_name}.{index_name} on {table_name}",
                            analysis_step="DatabaseSchemaAndStats",
                            confidence=70,
                            action_requested=(
                                "Review whether this index is still needed; it has "
                                "recorded zero scans since the last statistics reset."
                            ),
                            action_target_name=f"{schema_name}.{index_name}",
                            explanation=(
                                "idx_scan is 0 since the last pg_stat_database reset "
                                f"({stats_reset or 'reset time unknown'}). A young "
                                "index or one reset recently may simply not have been "
                                "exercised yet — this is a candidate, not a verdict."
                            ),
                        )
                    )
        else:
            annotations.append(
                ResourceMeasureAnnotation(
                    summary="Index usage (pg_stat_user_indexes) not supported by this engine",
                    analysis_step="DatabaseSchemaAndStats",
                    confidence=0,
                    resource_properties={"capability": "index_stats", "supported": False},
                    explanation=(
                        "This connection's engine capability declaration does not "
                        "include index_stats — the finding is not established, not "
                        "a measurement of zero indexes."
                    ),
                )
            )

        return profile_rows, activity_rows, annotations

    def _survey_operations(
        self,
        conn,
        capabilities: EngineCapabilities,
        schema_info: dict,
    ) -> dict:
        """Fetch the four analyses `postgres_operations` folds together
        (design §5.7): `privilege_audit`, `db_activity_signals`,
        `db_resilience`, `db_external_dependencies`. Each is independently
        capability-gated (design §5.1) — a section is `None` when this
        connection's capability declaration says the engine can't do it,
        never an empty dict indistinguishable from "measured, nothing
        found". `_create_operations_annotations()` is the pure function
        that turns this dict into annotations, and is also what
        `EgeriaDatabaseSurveyor.publish_step_annotations` calls for the
        Survey-Definition publish path — this method does only the fetch.
        """
        info: dict = {"capabilities": capabilities.as_dict()}

        info["privilege_audit"] = (
            conn.get_privilege_audit() if capabilities.privileges else None
        )

        if capabilities.tuple_counters:
            info["activity_signals"] = {
                "table_activity": conn.get_table_activity(),
                "stats_reset": conn.get_stats_reset(),
                "table_count": schema_info.get("total_tables", 0),
            }
        else:
            info["activity_signals"] = None

        if capabilities.resilience:
            info["resilience"] = {
                "replication": conn.get_replication_status(),
                "wal_archiving": conn.get_wal_archiving_status(),
                "backup_tool_signals": conn.get_backup_tool_signals(),
                "clustering": conn.get_clustering_info(),
            }
        else:
            info["resilience"] = None

        info["external_dependencies"] = (
            conn.get_external_dependencies() if capabilities.external_dependencies else None
        )

        return info

    def _create_operations_annotations(self, operations_info: dict) -> list:
        """Turn `_survey_operations()`'s fetched dict into annotations.

        A `None` section here means "this connection's capability
        declaration says the engine can't do this" (STATE_NOT_SUPPORTED in
        registry.py's vocabulary) — rendered as a ResourceMeasureAnnotation
        at confidence 0 rather than silently skipped, the same pattern
        `_survey_extended_statistics` uses for column_stats/tuple_counters/
        index_stats above. Kept as a pure function of already-fetched data
        (no `conn` argument) so `EgeriaDatabaseSurveyor.publish_step_
        annotations` can call it directly on a Survey-Definition step's
        stored output, without re-opening a connection.
        """
        annotations: list = []

        # ── privilege_audit (design §5.4, §5.7) ──
        privilege_audit = operations_info.get("privilege_audit")
        if privilege_audit is None:
            annotations.append(
                ResourceMeasureAnnotation(
                    summary="Privilege audit (pg_roles/role_table_grants) not supported by this engine",
                    analysis_step="DatabaseOperations",
                    confidence=0,
                    resource_properties={"capability": "privileges", "supported": False},
                    explanation=(
                        "This connection's engine capability declaration does not "
                        "include privileges — the finding is not established, not a "
                        "measurement of zero roles or grants."
                    ),
                )
            )
        else:
            roles = privilege_audit.get("roles") or []
            grants = privilege_audit.get("table_grants") or []
            default_acl = privilege_audit.get("default_acl") or []
            superusers = [r.get("rolname") for r in roles if r.get("rolsuper")]
            annotations.append(
                ResourceMeasureAnnotation(
                    summary=(
                        f"{len(roles)} role(s), {len(grants)} table grant(s), "
                        f"{len(superusers)} superuser role(s)"
                    ),
                    analysis_step="DatabaseOperations",
                    confidence=100,
                    resource_properties={
                        "role_count": len(roles),
                        "table_grant_count": len(grants),
                        "default_acl_count": len(default_acl),
                        "superuser_roles": superusers,
                    },
                    explanation=(
                        "Roles, table grants and default ACLs read directly from "
                        "pg_roles, information_schema.role_table_grants and "
                        "pg_default_acl."
                    ),
                )
            )

            # RFA on PUBLIC grants (design §5.7), grouped per table so one
            # table with several PUBLIC-granted privileges raises one
            # actionable item rather than one per privilege_type.
            public_grants: dict[tuple, list[str]] = {}
            for g in grants:
                if (g.get("grantee") or "").upper() == "PUBLIC":
                    key = (g.get("table_schema", ""), g.get("table_name", ""))
                    public_grants.setdefault(key, []).append(g.get("privilege_type", ""))
            for (schema_name, table_name), privileges in sorted(public_grants.items()):
                qualified = f"{schema_name}.{table_name}"
                priv_list = ", ".join(sorted(set(privileges)))
                annotations.append(
                    RequestForActionAnnotation(
                        summary=f"PUBLIC has {priv_list} on {qualified}",
                        analysis_step="DatabaseOperations",
                        confidence=100,
                        action_requested=(
                            f"Review whether the PUBLIC role should retain {priv_list} "
                            f"on {qualified}; revoke if not intentional."
                        ),
                        action_target_name=qualified,
                        explanation=(
                            "A grant to the PUBLIC pseudo-role means every current and "
                            "future database user has this privilege, not just an "
                            "explicitly-listed role — read directly from "
                            "information_schema.role_table_grants."
                        ),
                    )
                )

        # ── db_activity_signals (design §5.2, §5.7) ──
        # Reuses Phase 1 slice 7's get_table_activity()/get_stats_reset()
        # rather than querying pg_stat_user_tables a second way — this is
        # the database-wide roll-up of the SAME per-table figures
        # postgres_schema_and_stats records individually.
        activity = operations_info.get("activity_signals")
        if activity is None:
            annotations.append(
                ResourceMeasureAnnotation(
                    summary="Activity signals (pg_stat_user_tables) not supported by this engine",
                    analysis_step="DatabaseOperations",
                    confidence=0,
                    resource_properties={"capability": "tuple_counters", "supported": False},
                    explanation=(
                        "This connection's engine capability declaration does not "
                        "include tuple_counters — the finding is not established, "
                        "not a measurement of zero activity."
                    ),
                )
            )
        else:
            rows = activity.get("table_activity") or []
            table_count = activity.get("table_count", 0)
            if not rows and table_count:
                # Present in the catalog, absent from pg_stat_user_tables —
                # stats not yet accumulated for any table, not zero
                # activity. Same "run ANALYZE"-shaped distinction slice 7
                # draws per-column; here it applies database-wide.
                annotations.append(
                    ResourceMeasureAnnotation(
                        summary=(
                            f"No pg_stat_user_tables rows despite {table_count} "
                            "table(s) in the catalog — activity not yet accumulated"
                        ),
                        analysis_step="DatabaseOperations",
                        confidence=0,
                        resource_properties={"table_count": table_count, "rows_found": 0},
                        explanation=(
                            "The statistics collector has not produced a row for any "
                            "table yet (recent stats reset, or the collector has not "
                            "run) — this is absence of measurement, not a measurement "
                            "of zero activity."
                        ),
                    )
                )
            else:
                total_inserts = sum(r.get("rows_inserted") or 0 for r in rows)
                total_updates = sum(r.get("rows_updated") or 0 for r in rows)
                total_deletes = sum(r.get("rows_deleted") or 0 for r in rows)
                never_read = [
                    f"{r.get('schemaname')}.{r.get('tablename')}" for r in rows
                    if (r.get("seq_scan") or 0) == 0 and (r.get("idx_scan") or 0) == 0
                ]
                tables_with_writes = [
                    r for r in rows
                    if (r.get("rows_inserted") or 0) or (r.get("rows_updated") or 0)
                    or (r.get("rows_deleted") or 0)
                ]
                annotations.append(
                    ResourceMeasureAnnotation(
                        summary=(
                            f"Database-wide activity since stats reset: "
                            f"{total_inserts} inserts, {total_updates} updates, "
                            f"{total_deletes} deletes across {len(rows)} table(s); "
                            f"{len(never_read)} table(s) with no reads recorded"
                        ),
                        analysis_step="DatabaseOperations",
                        confidence=100,
                        resource_properties={
                            "tables_measured": len(rows),
                            "rows_inserted_total": total_inserts,
                            "rows_updated_total": total_updates,
                            "rows_deleted_total": total_deletes,
                            "tables_with_no_reads": never_read,
                            "tables_with_writes": len(tables_with_writes),
                            "stats_reset": activity.get("stats_reset") or "",
                        },
                        explanation=(
                            "Database-wide roll-up of pg_stat_user_tables tuple "
                            "counters and scan counts — the same per-table figures "
                            "postgres_schema_and_stats records individually (design "
                            "§5.1), summarised here to answer 'is anything reading or "
                            "writing at all', not per-table detail."
                        ),
                    )
                )

        # ── db_resilience (design §5.5 — a MIXED analysis) ──
        resilience = operations_info.get("resilience")
        if resilience is None:
            annotations.append(
                ResourceMeasureAnnotation(
                    summary=(
                        "Operational resilience (pg_stat_replication/pg_settings) "
                        "not supported by this engine"
                    ),
                    analysis_step="DatabaseOperations",
                    confidence=0,
                    resource_properties={"capability": "resilience", "supported": False},
                    explanation=(
                        "This connection's engine capability declaration does not "
                        "include resilience — the finding is not established, not a "
                        "measurement of 'standalone, unreplicated'."
                    ),
                )
            )
        else:
            replication = resilience.get("replication") or {}
            wal = resilience.get("wal_archiving") or {}
            backup = resilience.get("backup_tool_signals") or {}
            clustering = resilience.get("clustering") or {}

            is_in_recovery = replication.get("is_in_recovery")
            replicas = replication.get("replicas") or []

            if is_in_recovery is True:
                role = "standby"
                summary = "Standby — receiving from a primary (pg_is_in_recovery() = true)"
            elif is_in_recovery is False and replicas:
                role = "primary"
                lags = [
                    r.get("replay_lag_seconds") for r in replicas
                    if r.get("replay_lag_seconds") is not None
                ]
                max_lag = max(lags) if lags else None
                summary = (
                    f"Primary with {len(replicas)} replica(s)"
                    + (f", max replay lag {max_lag:.0f}s" if max_lag is not None else
                       ", replay lag unknown")
                )
            elif is_in_recovery is False:
                # A real, positive finding — not an error and not an
                # omission. See DB-OPERATIONS-STEP-IMPLEMENTED.md.
                role = "standalone"
                summary = "Standalone — primary with no replicas (pg_is_in_recovery() = false)"
            else:
                role = "unknown"
                summary = "Replication role could not be determined (pg_is_in_recovery() unavailable)"

            annotations.append(
                ResourcePhysicalStatusAnnotation(
                    summary=summary,
                    analysis_step="DatabaseOperations",
                    confidence=100 if is_in_recovery is not None else 0,
                    physical_properties={
                        "role": role,
                        "replica_count": len(replicas),
                        "replicas": replicas,
                        "archive_mode": wal.get("archive_mode") or "",
                        "archiver_failed_count": wal.get("failed_count"),
                        "archiver_last_archived_time": wal.get("last_archived_time") or "",
                        "backup_tool_extensions_detected": backup.get("detected_extensions", []),
                        "citus_detected": clustering.get("citus_detected", False),
                        "citus_version": clustering.get("citus_version"),
                        # Enrichment half — design §5.5 lists these as "no"
                        # in the Observable column: not machine-observable
                        # from inside the database at all. Recorded as
                        # explicitly pending rather than omitted, per the
                        # MIXED-analysis envelope rule ("the envelope must
                        # say which half answered").
                        "last_backup_date": None,
                        "restore_test_date": None,
                        "enrichment_pending": ["last_backup_date", "restore_test_date", "rpo_rto"],
                    },
                    explanation=(
                        "MIXED analysis (design §5.5): the catalog half — replication "
                        "role, replica lag, WAL archiving status, backup-tool presence "
                        "and Citus clustering — is measured directly from "
                        "pg_is_in_recovery(), pg_stat_replication, "
                        "pg_settings.archive_mode, pg_stat_archiver and pg_extension. "
                        "The Enrichment half (last successful backup date, whether a "
                        "restore was ever tested, RPO/RTO) is NOT machine-observable "
                        "from inside the database and stays genuinely pending — see "
                        "enrichment_pending — rather than being silently omitted or "
                        "reported as zero."
                    ),
                )
            )

        # ── db_external_dependencies (design §5.4, §5.7) ──
        deps = operations_info.get("external_dependencies")
        if deps is None:
            annotations.append(
                ResourceMeasureAnnotation(
                    summary=(
                        "External dependency inventory (pg_extension/"
                        "pg_foreign_server/pg_publication) not supported by this engine"
                    ),
                    analysis_step="DatabaseOperations",
                    confidence=0,
                    resource_properties={"capability": "external_dependencies", "supported": False},
                    explanation=(
                        "This connection's engine capability declaration does not "
                        "include external_dependencies — the finding is not "
                        "established, not a measurement of zero dependencies."
                    ),
                )
            )
        else:
            extensions = deps.get("extensions") or []
            foreign_servers = deps.get("foreign_servers") or []
            foreign_tables = deps.get("foreign_tables") or []
            publications = deps.get("publications") or []
            subscriptions = deps.get("subscriptions") or []
            annotations.append(
                SchemaAnalysisAnnotation(
                    summary=(
                        f"{len(extensions)} extension(s), {len(foreign_servers)} "
                        f"foreign server(s), {len(foreign_tables)} foreign table(s), "
                        f"{len(publications)} publication(s), {len(subscriptions)} "
                        f"subscription(s)"
                    ),
                    analysis_step="DatabaseOperations",
                    schema_name=self.db_entity.database_name,
                    schema_type="external_dependencies",
                    confidence=100,
                    json_properties={
                        "extensions": [e.get("extname") for e in extensions],
                        "foreign_servers": [s.get("srvname") for s in foreign_servers],
                        "foreign_tables": [
                            f"{t.get('schema_name')}.{t.get('table_name')}"
                            for t in foreign_tables
                        ],
                        "publications": [p.get("pubname") for p in publications],
                        "subscriptions": [s.get("subname") for s in subscriptions],
                    },
                    explanation=(
                        "What this database depends on outside itself — extensions, "
                        "foreign data wrappers/servers/tables and logical replication "
                        "publications/subscriptions — read directly from pg_extension, "
                        "pg_foreign_server, pg_foreign_table, pg_publication and "
                        "pg_subscription."
                    ),
                )
            )

        return annotations

    #: "Meaningfully thin" (design REPLY-DATABASE-CREDENTIAL-CAPABILITY-
    #: VISIBILITY.md §4's worked example: 3 of 26 tables, 6 of 8 schemas — both
    #: well under this line). A judgement call, not a derivation: below half
    #: the database's real tables selectable, OR at least one whole schema
    #: with no USAGE grant at all, is enough to name specific objects and ask
    #: the database owner for a broader grant or connection, per the design's
    #: RFA example ("grant SELECT on coco_ods.* or register a broader
    #: connection on this asset").
    CREDENTIAL_CAPABILITY_RFA_TABLE_FRACTION = 0.5

    def _survey_credential_capability(self, conn, capabilities: EngineCapabilities) -> dict | None:
        """Fetch what THIS connection can see and do (design §3/§4). `None`
        means "not supported by this engine's capability declaration" — the
        same not-established convention `_survey_operations` uses for each of
        its four sections, never an empty dict indistinguishable from
        "measured, and it turns out this credential can see everything".
        """
        if not capabilities.credential_introspection:
            return None
        return conn.get_credential_capability()

    def _create_credential_capability_annotations(self, info: dict | None) -> list:
        """Turn `_survey_credential_capability()`'s fetched dict into
        annotations: one `ResourceMeasureAnnotation` stating the visibility
        fraction, and — when coverage is thin — a `RequestForActionAnnotation`
        to the database owner naming the worst-covered schema. Kept as a pure
        function of already-fetched data (no `conn` argument), the same shape
        `_create_operations_annotations` uses, so
        `EgeriaDatabaseSurveyor.publish_step_annotations` can call it directly
        on a Survey-Definition step's stored output without re-opening a
        connection.
        """
        annotations: list = []

        if info is None:
            annotations.append(
                ResourceMeasureAnnotation(
                    summary="Credential-capability probe not supported by this engine",
                    analysis_step="DatabaseCredentialCapability",
                    confidence=0,
                    resource_properties={
                        "capability": "credential_introspection",
                        "supported": False,
                    },
                    explanation=(
                        "This connection's engine capability declaration does not "
                        "include credential_introspection — the finding is not "
                        "established, not a measurement of full visibility."
                    ),
                )
            )
            return annotations

        connected_as = info.get("connected_as") or "(unknown)"
        schema_total = info.get("schema_total", 0)
        schema_visible = info.get("schema_visible", 0)
        table_total = info.get("table_total", 0)
        table_select = info.get("table_select", 0)
        by_schema = info.get("by_schema") or {}
        stats_role = bool(info.get("stats_role"))
        write_capable = bool(info.get("write_capable"))

        annotations.append(
            ResourceMeasureAnnotation(
                summary=(
                    f"Connected as {connected_as}: visible {schema_visible} of "
                    f"{schema_total} schema(s), SELECT on {table_select} of "
                    f"{table_total} table(s)"
                ),
                analysis_step="DatabaseCredentialCapability",
                confidence=100,
                resource_properties={
                    "connected_as": connected_as,
                    "schema_total": schema_total,
                    "schema_visible": schema_visible,
                    "table_total": table_total,
                    "table_select": table_select,
                    "by_schema": by_schema,
                    "stats_role": stats_role,
                    "write_probed": True,
                    "write_capable": write_capable,
                },
                explanation=(
                    "pg_namespace/pg_class (unfiltered — catalog metadata visible "
                    "to any connected role) compared against "
                    "has_schema_privilege(current_user, schema, 'USAGE') and "
                    "has_table_privilege(current_user, table, 'SELECT'); "
                    "pg_has_role(current_user, 'pg_monitor', 'MEMBER') for the "
                    "statistics capability; has_table_privilege(..., 'INSERT') "
                    "probed for write — never exercised as an actual write."
                ),
            )
        )

        # RFA when coverage is thin (design §2/§4's worked example) — grouped
        # to name the single worst-covered schema, same "one actionable item,
        # not one per gap" shape _create_operations_annotations uses for
        # PUBLIC grants.
        thin = table_total > 0 and (
            (table_select / table_total) < self.CREDENTIAL_CAPABILITY_RFA_TABLE_FRACTION
            or schema_visible < schema_total
        )
        if thin:
            worst_schema = None
            worst_gap = -1
            for name, sc in sorted(by_schema.items()):
                total = sc.get("table_total", 0)
                selected = sc.get("table_select", 0)
                gap = total - selected
                if not sc.get("usage_granted") and total == 0:
                    # A schema with no USAGE and no visible tables at all is
                    # the most severe gap this probe can name, even though
                    # pg_class shows it has zero (rather than "unknown")
                    # tables from this connection's vantage point.
                    gap = max(gap, 1)
                if gap > worst_gap:
                    worst_gap, worst_schema = gap, name
            target = f"{worst_schema}.*" if worst_schema else "this database"
            # REPLY-SCHEMA-AS-SUB-RESOURCE.md §2: the shortfall message "lists
            # them by schema, which is what a database owner grants on anyway"
            # — "3 of 8 schemas readable; 3 of 26 tables" rather than a bare
            # table count. The per-schema reading is
            # `schema_scope.credential_shortfall`, shared with the analysis
            # readers so the two surfaces cannot drift; `worst_schema` above
            # still picks the single RFA target, since an RFA needs one
            # actionable subject.
            from .schema_scope import credential_shortfall
            from .connection import containment_for_engine

            # The level's name comes from the surveyed engine's own
            # declaration, not a literal: the same probe against Oracle is
            # reporting on owners, not schemas.
            entity = getattr(self, "db_entity", None)
            shortfall = credential_shortfall(
                info, containment_for_engine(getattr(entity, "db_type", None)),
            )
            fraction_phrase = (
                shortfall["phrase"] if shortfall
                else f"SELECT on {table_select} of {table_total} table(s)"
            )
            short_clause = (
                f" Short on: " + ", ".join(
                    f"{name} ({shortfall['by_container'][name]['state']})"
                    for name in shortfall["short_containers"]
                ) + "."
                if shortfall else ""
            )
            annotations.append(
                RequestForActionAnnotation(
                    summary=(
                        f"connected as {connected_as}: {fraction_phrase}"
                    ),
                    analysis_step="DatabaseCredentialCapability",
                    confidence=100,
                    action_requested=(
                        f"Grant SELECT on {target} or register a broader connection "
                        "on this asset."
                    ),
                    action_target_name=target,
                    explanation=(
                        "This credential's visible coverage is well below what this "
                        "database's own catalog (pg_namespace/pg_class, unfiltered) "
                        "shows exists — every fact this survey reports is scoped to "
                        "what this credential can reach, not to the whole database."
                        + short_clause
                    ),
                )
            )

        return annotations

    def _create_schema_annotations(self, schema_info: dict) -> list:
        """Create annotations from schema information."""
        annotations = []

        # Overall schema summary
        total_schemas = len(schema_info.get("schemas", []))
        total_tables = schema_info.get("total_tables", 0)
        total_columns = schema_info.get("total_columns", 0)

        annotations.append(
            SchemaAnalysisAnnotation(
                summary=f"Database contains {total_schemas} schemas, {total_tables} tables, {total_columns} columns",
                analysis_step="DatabaseSchemaSurvey",
                schema_name=self.db_entity.database_name,
                schema_type=self.db_entity.db_type,
                confidence=100,
                explanation=(
                    "Aggregate schema inventory for the whole database, computed by RE's "
                    "local scan of the database's own information_schema/catalog tables."
                ),
            )
        )

        # Per-schema annotations
        for schema in schema_info.get("schemas", []):
            schema_name = schema["name"]
            table_count = len(schema["tables"])

            annotations.append(
                SchemaAnalysisAnnotation(
                    summary=f"Schema '{schema_name}' contains {table_count} tables",
                    analysis_step="DatabaseSchemaSurvey",
                    schema_name=schema_name,
                    schema_type="schema",
                    confidence=100,
                    explanation="Table count for one schema within the database, from the local scan.",
                )
            )

            # Per-table annotations (using SchemaAnalysisAnnotation for tables too)
            for table in schema["tables"]:
                table_name = table["name"]
                column_count = len(table["columns"])

                annotations.append(
                    SchemaAnalysisAnnotation(
                        summary=f"Table '{schema_name}.{table_name}' has {column_count} columns",
                        analysis_step="DatabaseSchemaSurvey",
                        schema_name=f"{schema_name}.{table_name}",
                        schema_type="table",
                        confidence=100,
                        explanation="Column count for one table, read directly from the database's own schema catalog.",
                    )
                )

        return annotations

    def _create_statistics_annotations(self, stats_info: dict) -> list:
        """Create annotations from statistics information."""
        annotations = []

        # Database size annotation
        db_size = stats_info.get("database_size", {})
        if db_size:
            size_bytes = db_size.get("size_bytes", 0)
            size_pretty = db_size.get("size_pretty", "unknown")
            # A size the engine did not report is not a size of zero.
            #
            # This published "Database size: unknown" at confidence 100 with
            # size_bytes defaulting to 0 — so a consumer reading the NUMBER got
            # a zero, which reads as an empty database, while the explanation
            # asserted the value had been "queried directly from the database
            # engine's own size functions". Both halves claimed provenance the
            # run did not have. Found 2026-08-31 by a sweep for placeholder
            # values published at high confidence; it was the only instance in
            # the package, and the same shape as language.py's 95%-confident
            # "Primary language: Unknown".
            measured = bool(size_bytes) and size_pretty not in ("", "unknown")
            annotations.append(
                ResourceMeasureAnnotation(
                    summary=(f"Database size: {size_pretty}" if measured
                             else "Database size not reported by the engine"),
                    analysis_step="DatabaseStatistics",
                    confidence=100 if measured else 0,
                    resource_properties=(
                        {"size_bytes": size_bytes, "size_pretty": size_pretty} if measured
                        # No size_bytes key at all rather than a 0 that reads as
                        # a measurement — absence a reader can see is better
                        # than a number a reader cannot doubt.
                        else {"size_measured": False}),
                    explanation=(
                        "Point-in-time on-disk size of the whole database, queried directly "
                        "from the database engine's own size functions."
                        if measured else
                        "The engine returned no usable size for this database. Reported as "
                        "absent rather than as zero: a missing measurement and an empty "
                        "database are different facts."
                    ),
                )
            )

        # Table statistics
        table_stats = stats_info.get("table_stats", [])
        if table_stats:
            largest_tables = table_stats[:5]  # Top 5 largest
            for table in largest_tables:
                schema = table.get("schemaname", "")
                table_name = table.get("tablename", "")
                size = table.get("total_size", "")
                size_bytes = table.get("total_bytes", 0)
                annotations.append(
                    ResourceMeasureAnnotation(
                        summary=f"Table {schema}.{table_name} size: {size}",
                        analysis_step="DatabaseStatistics",
                        confidence=100,
                        resource_properties={
                            "schema": schema,
                            "table": table_name,
                            "size_bytes": size_bytes,
                            "size_pretty": size,
                        },
                        explanation=(
                            "Point-in-time on-disk size for one of the database's largest tables, "
                            "queried directly from the database engine's own statistics views."
                        ),
                    )
                )

        return annotations

    def _store_results(self, results: dict) -> None:
        """Store survey results in the registry.

        Collector-honesty rule (design ruling 2026-09-26, generalizing
        slice 17c/the enumeration-floor PR's own fix): this method must
        write only the fields THIS run actually collected. Found live on
        `coco_pharma`: a per-card run of `schema_inventory` (steps
        `["schema", "views"]` — no `"statistics"`) or `db_activity_signals`
        (`["schema", "operations"]` — also no `"statistics"`) used to
        silently overwrite EVERY table's `row_count`/`size_bytes` back to a
        bare `0`, clobbering whatever a PRIOR `row_count_snapshot` run had
        correctly measured — a run that fetched no statistics manufacturing
        a "measured zero" by the act of writing, the exact class of bug
        this effort exists to close, one level up (the survey writer, not
        a results reader). Reproduced and fixed: a table this run has no
        fresh statistics for keeps whatever was already stored (read back
        before overwriting), or `None` if nothing was ever stored — never a
        fabricated `0`.

        This is the honest STOPGAP, not the real fix: the underlying cause
        is one row per table getting overwritten by every survey run
        instead of survey rows keyed `(slug, surveyed_at, source)` per
        design rule D — the structured-tables rework (stream 3) is where
        that actually gets fixed. Preserving prior values here prevents the
        visible symptom (numbers flipping between runs) without touching
        that larger design.
        """
        schema_info = results["schema_info"]
        statistics  = results.get("statistics", {})

        # Read back whatever is already stored for this database BEFORE
        # this run's own write, so a table this run has no fresh
        # statistics for can keep its prior value instead of losing it.
        try:
            prior_rows = self.registry.query_detail_rows(
                "database_tables", self.db_entity.slug
            )
        except Exception:
            prior_rows = []
        prior_by_key = {
            (r.get("schema_name"), r.get("table_name")): r for r in prior_rows
        }

        # Enrich each table with row count + activity timestamps from
        # pg_stat_user_tables. CORRECTED 2026-09-24/25: unlike information_
        # schema, this view is NOT privilege-filtered — live-verified (see
        # `DATABASE-STEP-CAPABILITY-AUDIT.md`'s "Correction") to be visible
        # to any connected role regardless of grants or `pg_monitor`. So a
        # catalog-fallback table (connection.py's `_catalog_only_fallback()`)
        # will in practice usually still have a row here; the `elif` below
        # exists for the residual case where it genuinely doesn't (e.g. a
        # table Postgres has never collected stats for), not because this
        # view is gated the way `information_schema`/`pg_tables` are.
        row_lookup: dict[tuple, dict] = {
            (rs["schemaname"], rs["tablename"]): rs
            for rs in statistics.get("row_stats", [])
        }
        for schema in schema_info.get("schemas", []):
            for table in schema["tables"]:
                key = (schema["name"], table["name"])
                rs = row_lookup.get(key, {})
                if rs:
                    table["row_count"] = rs.get("row_count", 0)
                elif table.get("source") == "catalog_fallback":
                    # No real stats-collector row either — fall back to the
                    # ANALYZE-time estimate connection.py already attached,
                    # rather than defaulting to 0 (a fabricated "measured
                    # zero" for a table this credential cannot actually
                    # count). result_materializer.py reads
                    # `row_count_estimate` when `row_count` is left absent.
                    table["row_count"] = None
                else:
                    # This run's own steps did not include "statistics" at
                    # all -- nothing here was measured THIS run. Keep
                    # whatever was already stored rather than asserting a
                    # zero this run never looked at.
                    prior = prior_by_key.get(key)
                    table["row_count"] = prior.get("row_count") if prior else None
                table["last_analyzed"]  = rs.get("last_analyzed", "")
                table["last_vacuumed"]  = rs.get("last_vacuumed", "")
                table["pending_changes"] = rs.get("pending_changes", 0)

        # Also enrich size data from table_stats
        size_lookup: dict[tuple, dict] = {
            (ts["schemaname"], ts["tablename"]): ts
            for ts in statistics.get("table_stats", [])
        }
        for schema in schema_info.get("schemas", []):
            for table in schema["tables"]:
                key = (schema["name"], table["name"])
                ts = size_lookup.get(key, {})
                if ts:
                    table["size_bytes"] = ts.get("total_bytes", 0) or 0
                elif table.get("source") == "catalog_fallback":
                    # Same reasoning as row_count above: pg_tables (the
                    # source of table_stats) is schema-USAGE-filtered, and a
                    # catalog-fallback table has no size measurement to fall
                    # back to at all — leave it unmeasured rather than 0.
                    table["size_bytes"] = None
                else:
                    # Same stopgap as row_count above -- this run collected
                    # no size data at all, so keep the prior value.
                    prior = prior_by_key.get(key)
                    table["size_bytes"] = prior.get("size_bytes") if prior else None
                table["size_pretty"] = ts.get("total_size", "")

        # `operations`/`credential_capability` preserve-prior, same stopgap
        # as row_count/size_bytes above — found live 2026-09-26 running a
        # database Survey Definition: SurveyDefinitionExecutor dispatches
        # each step as its OWN separate DatabaseSurveyor.survey() call, so
        # a 3-step Scouting definition writes THREE survey rows a couple of
        # seconds apart. The last step, credential_capability, collects no
        # "operations" at all — its own `results.get("operations", {})` is
        # correctly `{}` for ITS run — but writing that `{}` unconditionally
        # made it the newest row's value, silently shadowing the real
        # operations data postgres_operations had written two rows earlier.
        # db_activity_signals/db_resilience (which read the operations
        # section off the single latest row) then read nothing and
        # degraded from "0 writes and 6 reads" to "ran and found nothing" —
        # a per-analysis-path answer clobbered by an unrelated step in the
        # same Survey Definition run, not a real measurement. Same fix as
        # row_count/size_bytes: this run's own emptiness is only trusted
        # when this run's OWN requested steps included that section; when
        # they were run but the fields at hand happens to be empty by
        # design (a genuinely stale-only db) it stays empty. This is only a
        # difference at the boundary — for statistics we lacked a
        # "did this run cover it" flag, so we fall back to a permissive
        # "keep the value if THIS run's own value is empty" rule.
        import json
        prior_operations: dict = {}
        prior_credential_capability: dict = {}
        try:
            prior_survey = self.registry.get_latest_database_survey(self.db_entity.slug)
            if prior_survey:
                prior_data = json.loads(prior_survey.get("survey_data") or "{}")
                prior_operations = prior_data.get("operations") or {}
                prior_credential_capability = prior_data.get("credential_capability") or {}
        except Exception:
            pass

        # `surveyed_at` is passed explicitly (rather than left to default)
        # so this call's own backfill_database_survey() write and the
        # extension's write just below land under the SAME
        # (slug, surveyed_at, source) key — see record_database_survey's
        # docstring for the bug this avoids.
        self.registry.record_database_survey(
            slug=self.db_entity.slug,
            schema_count=len(schema_info.get("schemas", [])),
            table_count=schema_info.get("total_tables", 0),
            column_count=schema_info.get("total_columns", 0),
            survey_data={
                "schema_info": schema_info,
                "statistics": statistics,
                "annotation_count": len(results["annotations"]),
                "views": results.get("views", []),
                #: postgres_operations (Phase 1 slice 8) — falls back to the
                #: prior stored value when THIS run's own results have none,
                #: same "don't manufacture an empty answer" rule row_count/
                #: size_bytes above use; empty dict only when neither this
                #: run nor any prior one ever collected it.
                "operations": results.get("operations") or prior_operations,
                #: credential_capability (design §3/§4) — same preserve-prior
                #: rule as "operations" above.
                "credential_capability": (
                    results.get("credential_capability") or prior_credential_capability
                ),
            },
            surveyed_at=results["surveyed_at"],
            # `surveyed_as`: the credential identity this run connected as
            # (design §4 — "the credential identity is recorded on every
            # survey row"). Falls back to the stored db_user when the
            # credential_capability step did not run this time, so a plain
            # schema/statistics-only survey still records who ran it.
            surveyed_as=(
                (results.get("credential_capability") or {}).get("connected_as")
                or self.credentials.get("user", "")
                or ""
            ),
        )

        # pg_stats / pg_stat_user_tables extension (design §5.1, §5.7 —
        # Phase 1 slice 7). Written through the same generic detail-row path
        # `result_materializer.py` uses for native surveys, `source="local"`
        # (the default), so a native and a local run of the same database on
        # the same day coexist rather than overwrite (design §3 rule D).
        # Only written when the "statistics" step actually ran — an omitted
        # step leaves these lists empty and no rows/coverage are written,
        # which is correct: "step did not run" is a different fact from
        # "ran and found nothing" and is already covered by `skipped_steps`
        # elsewhere in this codebase, not by an empty coverage row here.
        surveyed_at = results["surveyed_at"]
        column_profile_rows = results.get("column_profile_rows") or []
        table_activity_rows = results.get("table_activity_rows") or []
        if column_profile_rows:
            self.registry.write_detail_rows(
                "database_column_profiles",
                self.db_entity.slug,
                surveyed_at,
                rows=column_profile_rows,
                coverage_section=SECTION_COLUMN_PROFILES,
            )
        if table_activity_rows:
            self.registry.write_detail_rows(
                "database_table_activity",
                self.db_entity.slug,
                surveyed_at,
                rows=table_activity_rows,
                coverage_section=SECTION_TABLE_ACTIVITY,
            )

    def _survey_views(self, conn, schema_info: dict) -> list[dict]:
        """Fetch view definitions and perform static analysis using SQLGlot."""
        db_type = self.db_entity.db_type
        
        # Query view definitions (excluding catalog schemas)
        query = """
            SELECT table_schema, table_name, view_definition
            FROM information_schema.views
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
            ORDER BY table_schema, table_name
        """
        rows = conn.execute_query(query)
        if not rows:
            return []

        # Construct SQLGlot schema structure: { table_name: { col_name: type } }
        sqlglot_schema = {}
        for schema in schema_info.get("schemas", []):
            s_name = schema["name"]
            for table in schema["tables"]:
                t_name = table["name"]
                cols = {c["name"]: c["base_type"] or c["type"] for c in table["columns"]}
                # Support both qualified and unqualified lookups
                sqlglot_schema[t_name] = cols
                sqlglot_schema[f"{s_name}.{t_name}"] = cols

        from resource_explorer.surveyors.database.sql_analyzer import SqlAnalyzer

        analyzed_views = []
        for r in rows:
            schema_name = r["table_schema"]
            view_name = r["table_name"]
            view_def = r["view_definition"]
            if not view_def:
                continue

            qualified_view_name = f"{schema_name}.{view_name}"

            # 1. Parse dependencies
            dependencies = SqlAnalyzer.parse_dependencies(view_def, db_type)

            # 2. Complexity metrics
            complexity = SqlAnalyzer.compute_complexity_metrics(view_def, db_type)

            # 3. Column-level lineage
            # Find columns of this view in schema_info to trace them
            view_cols = []
            for schema in schema_info.get("schemas", []):
                if schema["name"] == schema_name:
                    for table in schema["tables"]:
                        if table["name"] == view_name:
                            view_cols = [c["name"] for c in table["columns"]]
                            break

            col_lineage = {}
            for col in view_cols:
                lineage_res = SqlAnalyzer.trace_column_lineage(
                    sql=view_def,
                    target_column=col,
                    schema=sqlglot_schema,
                    db_type=db_type
                )
                if lineage_res:
                    col_lineage[col] = lineage_res

            analyzed_views.append({
                "schema": schema_name,
                "name": view_name,
                "qualified_name": qualified_view_name,
                "definition": view_def,
                "dependencies": dependencies,
                "complexity": complexity,
                "lineage": col_lineage,
            })

        return analyzed_views

    def _create_views_annotations(self, views_info: list[dict]) -> list:
        """Create annotations for parsed view dependencies, complexity, and lineage."""
        import os
        from resource_explorer.surveyors.survey_report import (
            SchemaAnalysisAnnotation,
            RelationshipAnnotation,
            QualityScoreAnnotation,
            RequestForActionAnnotation,
            DataClassAnnotation,
        )
        annotations = []

        # Default fallback PII classification keywords
        pii_keywords = ["email", "phone", "ssn", "socialsec", "creditcard", "password", "dob", "dateofbirth"]

        # Dynamically load PII keywords from Egeria Valid Value Sets if platform connection is configured
        platform_url = os.getenv("EGERIA_PLATFORM_URL")
        if platform_url:
            try:
                from pyegeria.omvs.reference_data import ReferenceDataManager
                view_server = os.getenv("EGERIA_VIEW_SERVER", "view-server")
                user_id = os.getenv("EGERIA_USER", "steward")
                user_pwd = os.getenv("EGERIA_USER_PASSWORD", "steward")

                ref_manager = ReferenceDataManager(view_server, platform_url, user_id, user_pwd)
                ref_manager.create_egeria_bearer_token(user_id, user_pwd)

                egeria_keywords = []
                for dc_name in ["EmailAddress", "PhoneNumber", "SocialSecurityNumber", "CreditCardNumber", "Password", "DateOfBirth"]:
                    try:
                        # Find all ValidValueDefinitions starting with the data class's keyword prefix
                        results = ref_manager.find_valid_value_definitions(
                            search_string=f"ValidValueDefinition::{dc_name}Keyword::",
                            starts_with=True,
                            output_format="JSON"
                        )
                        if isinstance(results, list):
                            for item in results:
                                if isinstance(item, dict):
                                    props = item.get("properties", {})
                                    val = props.get("preferredValue") or props.get("displayName")
                                    if val:
                                        egeria_keywords.append(val.strip().lower())
                    except Exception:
                        pass

                if egeria_keywords:
                    pii_keywords = list(set(egeria_keywords))
            except Exception:
                pass

        def is_pii_column(col_name: str) -> bool:
            name_clean = col_name.lower().replace("_", "").replace("-", "")
            for kw in pii_keywords:
                kw_clean = kw.lower().replace("_", "").replace("-", "")
                if kw_clean in name_clean:
                    return True
            return False

        def extract_leafs(node: dict) -> list[dict]:
            if not node:
                return []
            downstream = node.get("downstream", [])
            if not downstream:
                if node.get("name") and node.get("source"):
                    return [{"source": node["source"], "name": node["name"]}]
                return []
            res = []
            for child in downstream:
                res.extend(extract_leafs(child))
            return res

        for view in views_info:
            view_name = view["name"]
            qualified_name = view["qualified_name"]
            complexity = view["complexity"]
            dependencies = view["dependencies"]

            # 1. Schema Analysis Annotation for the View structural definition
            annotations.append(
                SchemaAnalysisAnnotation(
                    summary=f"View '{qualified_name}' references dependencies: {', '.join(dependencies)}",
                    analysis_step="SQLViewAnalysis",
                    schema_name=qualified_name,
                    schema_type="view",
                    confidence=100,
                    explanation=(
                        f"Static AST dependency check parsed via SQLGlot. "
                        f"Identified {len(dependencies)} source table dependencies."
                    ),
                    json_properties={"dependencies": dependencies}
                )
            )

            # 2. Relationship Advice Annotations (Table to View dependencies)
            for dep in dependencies:
                annotations.append(
                    RelationshipAnnotation(
                        summary=f"View '{qualified_name}' reads from source table '{dep}'",
                        analysis_step="SQLViewAnalysis",
                        confidence=100,
                        related_entity_name=dep,
                        relationship_type_name="LineageMapping",
                        explanation="Design lineage link mapping table dependency extracted from view SQL.",
                    )
                )

            # 3. Quality Score Annotation for Complexity
            annotations.append(
                QualityScoreAnnotation(
                    summary=f"Query complexity metrics for view '{qualified_name}'",
                    analysis_step="SQLViewAnalysis",
                    confidence=100,
                    quality_scores={
                        "complexity_score": float(complexity["complexity_score"]),
                        "portability_rating": float(complexity["portability_rating"]),
                        "node_count": float(complexity["node_count"]),
                        "join_count": float(complexity["join_count"]),
                        "cte_count": float(complexity["cte_count"]),
                        "subquery_count": float(complexity["subquery_count"]),
                    },
                    explanation="Static query complexity rating calculated from AST node size, depth, and joins.",
                )
            )

            # 4. Request for Action Annotation (High Complexity Warning)
            if complexity["complexity_score"] > 40:
                annotations.append(
                    RequestForActionAnnotation(
                        summary=f"High complexity view '{qualified_name}' (Score: {complexity['complexity_score']})",
                        analysis_step="SQLViewAnalysis",
                        confidence=90,
                        action_requested="Refactor view to simplify JOIN logic or use materialized caching.",
                        action_target_name=qualified_name,
                        explanation="Automated warning: view query structure exceeds maintainability thresholds.",
                    )
                )

            # 5. Request for Action Annotation (Portability issues)
            if complexity["portability_rating"] < 80:
                annotations.append(
                    RequestForActionAnnotation(
                        summary=f"Portability warning for view '{qualified_name}'",
                        analysis_step="SQLViewAnalysis",
                        confidence=85,
                        action_requested="Review Snowflake non-portable syntax elements used in view.",
                        action_target_name=qualified_name,
                        explanation="Transpilation test failed to convert Postgres dialect constructs to Snowflake.",
                    )
                )

            # 6. Data Class Annotation for PII propagation
            pii_propagations = []
            for col_name, lin_tree in view.get("lineage", {}).items():
                is_pii = False
                reasons = []

                if is_pii_column(col_name):
                    is_pii = True
                    reasons.append(f"derived column name '{col_name}' matches PII keywords")

                leafs = extract_leafs(lin_tree)
                for leaf in leafs:
                    src_col = leaf["name"]
                    src_tbl = leaf["source"]
                    if is_pii_column(src_col):
                        is_pii = True
                        reasons.append(f"propagates PII from source column '{src_tbl}.{src_col}'")

                if is_pii:
                    pii_propagations.append({
                        "column": col_name,
                        "reasons": sorted(list(set(reasons)))
                    })

            if pii_propagations:
                col_summaries = [f"'{p['column']}' ({', '.join(p['reasons'])})" for p in pii_propagations]
                annotations.append(
                    DataClassAnnotation(
                        summary=f"PII propagation detected in view '{qualified_name}' for columns: {'; '.join(col_summaries)}",
                        analysis_step="SQLViewAnalysis",
                        confidence=95,
                        candidate_data_class_names=["PII", "SensitiveData"],
                        explanation=(
                            f"Static analysis of column-level lineage traced PII/sensitive attributes "
                            f"flowing from physical tables to the view's output attributes."
                        ),
                        json_properties={"pii_propagations": pii_propagations}
                    )
                )

        return annotations


def run_database_survey(
    db_slug: str,
    credentials: dict,
    registry: ProjectRegistry | None = None,
    steps: list[str] | None = None,
    reference_catalog=None,
    read_egeria_catalog: bool = True,
) -> dict:
    """Convenience function to run a database survey.

    Args:
        db_slug: Database slug to survey
        credentials: Dict with 'user' and 'password' keys
        registry: Optional ProjectRegistry (creates new one if not provided)
        steps: optional subset of {"schema", "statistics", "views"} — see
            DatabaseSurveyor.survey()'s docstring. None (default) runs all
            three, unchanged from before this parameter existed.
        reference_catalog / read_egeria_catalog: passed straight through to
            `DatabaseSurveyor.survey()` — see its docstring. This is the
            funnel both `web/routes/databases.py`'s per-card "Run" endpoint
            and `scheduler.py`'s scheduled runs go through, so leaving
            `reference_catalog` as `None` here (the default) lets `survey()`
            load it itself for `column_profile`, rather than each caller
            needing to know to load one.

    Returns:
        Survey results dict

    Example:
        results = run_database_survey(
            "my-postgres",
            {"user": "admin", "password": "secret"}
        )
    """
    if registry is None:
        registry = ProjectRegistry()

    db_entity = registry.get_database(db_slug)
    if not db_entity:
        raise ValueError(f"Database '{db_slug}' not found in registry")

    surveyor = DatabaseSurveyor(db_entity, credentials, registry)
    return surveyor.survey(
        steps=steps,
        reference_catalog=reference_catalog,
        read_egeria_catalog=read_egeria_catalog,
    )

# Made with Bob
