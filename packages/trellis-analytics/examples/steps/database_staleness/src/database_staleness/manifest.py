"""The step's manifest: the wrapper's declaration. Its version is the package's."""
from trellis_analytics.declaration import (AbsenceSentences, ConfidenceDeclaration, Declaration, ExtentDeclaration,
                                           MeasureDeclaration,
                                           CategoriesDeclaration)
from trellis_analytics.manifest import StepManifest

from database_staleness import __version__

MANIFEST = StepManifest(
    name="database_staleness",
    display_name="Database Staleness",
    function="database_staleness.step.run",
    description=("Rows changed per table since the last survey: compares the latest two row-count snapshots "
                 "Resource Explorer stored for the database. Reads only what RE already stored; opens no "
                 "connection to the database. Shown as \"not measured\", never as zero, when fewer than two "
                 "row-count snapshots exist, or a table or its count is missing from one."),
    returns="one ResourceMeasureAnnotation per table, each with its envelope",
    source="ods",
    version=__version__,
    resource_types=["database"],
    annotation_type="ResourceMeasureAnnotation",
    credential_tier="none",
    # `source_report`: in an Egeria process, the survey report the step before this one wrote (engine host step 4).
    inputs=["ods", "source_report"],
    # Two measures, each its own annotation (design ruling, engine host step 4): staleness from the stored snapshots,
    # and, only when a survey report is handed in, its writes since PostgreSQL's statistics reset. 0.2.0 adds the second.
    measures=["database_staleness", "writes_since_statistics_reset"],
    stage="analysis",
    perspectives=["Steward", "Data Expert"],
    # Q-0041: "How much has changed since the last time this was surveyed — is it worth re-running now?"
    questions=["Q-0041"],
    purposes=["Maintain"],
    family="Freshness",
    tags=["staleness", "row counts"],
    # What Resource Explorer's engine host advertises to Egeria for this step, and dispatches on.
    request_type="re-database-staleness",
    # Design ruling 2026-10-15: an absence honestly recorded is a completed survey (the report says "not measured ·
    # one snapshot only"). survey-invalid is for a surveyReport that is present but wrong, decided by the engine host.
    nothing_to_derive_guard="survey-completed",
    declaration=Declaration(
        result_kind="distribution",
        measures=[MeasureDeclaration(name="database_staleness", unit="rows changed since the previous survey",
                                     as_of="latestSurvey", value_field="rowsChanged"),
                  MeasureDeclaration(name="writes_since_statistics_reset",
                                     unit="rows inserted, updated or deleted since the statistics reset",
                                     as_of="asOf", value_field="writesSinceStatisticsReset")],
        absence=AbsenceSentences(
            not_measured=("Not measured: fewer than two row-count snapshots are stored, or the table or its count is "
                          "missing from one; a change needs two — never shown as zero."),
            not_permitted=("Not permitted: the survey that stored the row counts could not count this table's rows, "
                           "so no change is held for it."),
            not_applicable="Not applicable: a view holds no rows of its own.",
            measured_nothing="Measured: no rows changed between the two latest surveys."),
        extents=[ExtentDeclaration(
            name="tables_by_change", fields=["scope", "rowsChanged", "rowsBefore", "rowsAfter", "previousSurvey",
                                             "latestSurvey"],
            rows=20, sort=["rowsChanged desc"],
            truncation="{shown} of {total} tables shown, by rows changed", token_cost=1000,
            citation="{analysis} · {scope} · as of {as_of}")],
        # Both counts are PostgreSQL's statistics-collector figures (pg_stat_user_tables), not a count(*).
        confidence=ConfidenceDeclaration(
            form="estimated",
            method="the change between two of PostgreSQL's row-count estimates, not a count of the rows"),
        categories=CategoriesDeclaration(exclusive=True),
        scope="member",
    ),
)
