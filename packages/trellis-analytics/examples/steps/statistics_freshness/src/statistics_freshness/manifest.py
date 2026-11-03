"""The step's manifest: the wrapper's declaration. Its version is the package's."""
from trellis_analytics.declaration import (AbsenceSentences, BasisDeclaration, ConfidenceDeclaration, Declaration,
                                           ExtentDeclaration, ListDeclaration, MeasureDeclaration)
from trellis_analytics.manifest import StepManifest

from statistics_freshness import __version__

MANIFEST = StepManifest(
    name="statistics_freshness",
    display_name="Table Statistics Freshness",
    function="statistics_freshness.step.run",
    description=("How old each table's planner statistics are: the age of the last ANALYZE and VACUUM, tables "
                 "never analyzed, and tables changed past autovacuum's analyze trigger, as of the newest table "
                 "activity snapshot Resource Explorer stored. Opens no connection to the database. A table whose statistics were not read, "
                 "or a database with none stored, is shown as \"not measured\", never as fresh."),
    returns="one ResourceMeasureAnnotation per table, and one summary for the database, each with its envelope",
    source="ods",
    version=__version__,
    resource_types=["database"],
    annotation_type="ResourceMeasureAnnotation",
    credential_tier="none",
    inputs=["ods"],
    # Two measures, each its own annotation: per table, and one summary for the database (the slice's
    # "DataProfileAnnotation-style summary", published as a ResourceMeasureAnnotation, Egeria's nearest type).
    measures=["statistics_freshness", "statistics_freshness_summary"],
    stage="analysis",
    perspectives=["Steward", "Data Expert"],
    # Q-0073: "Are this database's planner statistics current?"; Q-0062: "Is this database alive ...?"
    questions=["Q-0073", "Q-0062"],
    purposes=["Maintain"],
    family="Freshness",
    tags=["statistics", "analyze", "vacuum"],
    request_type="re-statistics-freshness",
    # Design ruling 2026-10-15: an absence honestly recorded is a completed survey ("not measured · no table
    # statistics stored"). survey-invalid is only for inputs that are present and wrong.
    nothing_to_derive_guard="survey-completed",
    declaration=Declaration(
        result_kind="list",
        measures=[MeasureDeclaration(name="statistics_freshness", unit="days since the last ANALYZE", as_of="asOf",
                                     value_field="Days Since Analyze"),
                  # One value for the whole database, in a step whose rows are per table: its own scope.
                  MeasureDeclaration(name="statistics_freshness_summary", unit="tables never analyzed or stale",
                                     as_of="asOf", value_field="Needs Attention", scope="resource",
                                     basis=BasisDeclaration(denominator_field="Tables", denominator_label="tables"))],
        absence=AbsenceSentences(
            not_measured=("Not measured: no table statistics are stored for this database, or this table's were "
                          "not read — never shown as fresh."),
            not_permitted=("Not permitted: the survey that stored the table activity could not read this table's "
                           "statistics from pg_stat_user_tables."),
            not_applicable="Not applicable: a view has no planner statistics of its own.",
            measured_nothing=("Measured: no table whose statistics were read is stale or has never been analyzed "
                              "(a table not read, or with changes since not read, is counted separately).")),
        extents=[ExtentDeclaration(
            name="tables_by_staleness", fields=["scope", "Statistics Status", "Days Since Analyze",
                                                "Number Of Rows Changed Since Analyze", "Analyze Threshold"],
            rows=20, order="stale first, then never analyzed, then oldest ANALYZE first",
            truncation="{shown} of {total} tables shown, stale first", token_cost=840,
            citation="{analysis} · {scope} · as of {as_of}"),
            ExtentDeclaration(
            name="summary", fields=["Tables", "Needs Attention", "Never Analyzed", "Stale", "Current", "Not Read", "Oldest Analyze Days"],
            rows=1, order="the database as a whole", truncation="{shown} of {total} summaries shown",
            token_cost=88, citation="{analysis} · {scope} · as of {as_of}")],
        confidence=ConfidenceDeclaration(form="measured"),
        listing=ListDeclaration(item_fields=["scope", "Statistics Status", "Days Since Analyze"],
                                order="stale first, then never analyzed, then oldest ANALYZE first", key_field="scope",
                                item_label="tables"),
        scope="member",
    ),
)
