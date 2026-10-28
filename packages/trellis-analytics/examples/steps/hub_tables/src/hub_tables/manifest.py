"""The step's manifest: the wrapper's declaration. Its version is the package's."""
from trellis_analytics.manifest import StepManifest

from hub_tables import __version__

MANIFEST = StepManifest(
    name="hub_tables",
    display_name="Hub Tables",
    function="hub_tables.step.run",
    description=("Foreign-key degree in and out per table, from the keys Resource Explorer stored, and the tables "
                 "it proposes as hubs (referenced by at least hub_min_referencing other tables, a declared "
                 "parameter; a count from partly captured keys is a lower bound, reported as at least N). The "
                 "proposal is an annotation, never a classification on the table. Opens no connection to the "
                 "database. With no foreign keys stored it is shown as \"not measured\", never as a database "
                 "with no hubs."),
    returns=("one ResourceMeasureAnnotation per table (foreign keys in and out), and one ClassificationAnnotation "
             "proposing 'hub table' per hub, each with its envelope"),
    source="ods",
    version=__version__,
    resource_types=["database"],
    annotation_type="ResourceMeasureAnnotation",
    credential_tier="none",
    inputs=["ods"],
    # Two measures, each its own annotation: the degrees (the step's own), and the hub proposal, published as
    # Egeria's ClassificationAnnotation (owner, 2026-10-15: an annotation, never a classification on the element).
    measures=["hub_tables", "hub_table_proposal"],
    measure_annotation_types={"hub_table_proposal": "ClassificationAnnotation"},
    # The hub threshold, read by the code from here (inputs.parameters), so the declaration cannot drift from it.
    parameters={"hub_min_referencing": 3},
    stage="analysis",
    perspectives=["Data Expert", "Steward"],
    # Q-0072: "Which tables would a consumer start with?"; Q-0068: "Is there a data model here ...?"
    questions=["Q-0072", "Q-0068"],
    purposes=["Explore", "Integrate"],
    family="Structure",
    tags=["foreign keys", "hubs"],
    request_type="re-hub-tables",
    # Design ruling 2026-10-15: no keys captured is an absence honestly recorded ("not measured · no keys captured;
    # hubs cannot be derived"), a completed survey. survey-invalid is only for inputs present and wrong.
    nothing_to_derive_guard="survey-completed",
)
