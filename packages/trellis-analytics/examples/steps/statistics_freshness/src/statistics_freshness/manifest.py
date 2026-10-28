"""The step's manifest: the wrapper's declaration. Its version is the package's."""
from trellis_analytics.manifest import StepManifest

from statistics_freshness import __version__

MANIFEST = StepManifest(
    name="statistics_freshness",
    display_name="Table Statistics Freshness",
    function="statistics_freshness.step.run",
    description=("How old each table's planner statistics are: the age of the last ANALYZE and VACUUM, tables "
                 "never analysed, and tables changed past autovacuum's analyse trigger, as of the newest table "
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
)
