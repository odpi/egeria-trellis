"""The step's manifest: the wrapper's declaration. Its version is the package's."""
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
)
