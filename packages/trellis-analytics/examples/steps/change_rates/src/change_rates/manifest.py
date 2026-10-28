"""The step's manifest: the wrapper's declaration. Its version is the package's."""
from trellis_analytics.manifest import StepManifest

from change_rates import __version__

MANIFEST = StepManifest(
    name="change_rates",
    display_name="Table Change Rates",
    function="change_rates.step.run",
    description=("Rows inserted, updated and deleted per day per table between the latest two table activity "
                 "snapshots Resource Explorer stored, over their interval; a counter reset between them gives no "
                 "rate, never a negative one. Opens no connection to the database. With one activity snapshot, or a table "
                 "missing from one, it is shown as \"not measured\", never as a rate of zero."),
    returns="one ResourceMeasureAnnotation per table, each with the interval, both as-ofs and its envelope",
    source="ods",
    version=__version__,
    resource_types=["database"],
    annotation_type="ResourceMeasureAnnotation",
    credential_tier="none",
    inputs=["ods"],
    measures=["change_rates"],
    stage="analysis",
    perspectives=["Steward", "Data Expert"],
    # Q-0086: "How is this database changing — rows inserted, updated and deleted per table per period, ...?"
    questions=["Q-0086"],
    purposes=["Maintain", "Assess"],
    family="Freshness",
    tags=["change rates", "activity"],
    request_type="re-change-rates",
    # Design ruling 2026-10-15: one activity snapshot is an absence honestly recorded ("not measured · one activity
    # snapshot only; a rate needs two"), a completed survey. survey-invalid is only for inputs present and wrong.
    nothing_to_derive_guard="survey-completed",
)
