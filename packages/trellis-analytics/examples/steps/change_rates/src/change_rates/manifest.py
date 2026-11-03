"""The step's manifest: the wrapper's declaration. Its version is the package's."""
from trellis_analytics.declaration import (AbsenceSentences, ConfidenceDeclaration, Declaration,
                                           ExtentDeclaration, MeasureDeclaration,
                                           CategoriesDeclaration)
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
    declaration=Declaration(
        result_kind="distribution",
        measures=[MeasureDeclaration(name="change_rates", unit="rows written per day", as_of="toAsOf",
                                     value_field="Writes Per Day")],
        absence=AbsenceSentences(
            not_measured=("Not measured: one table activity snapshot is stored, or the table is missing from one; "
                          "a rate needs two, and a counter reset between them gives no rate."),
            not_permitted=("Not permitted: the survey that stored the activity snapshots could not read this "
                           "table's counters, so no rate is held for it."),
            not_applicable="Not applicable: a view holds no rows of its own, so nothing is written to it.",
            measured_nothing="Measured: no rows were inserted, updated or deleted over the interval."),
        extents=[ExtentDeclaration(
            name="tables_by_write_rate", fields=["scope", "Writes Per Day", "Writes In Interval", "Interval Days",
                                                  "fromAsOf", "toAsOf"],
            rows=20, sort=["Writes Per Day desc"],
            truncation="{shown} of {total} tables shown, by rows written per day", token_cost=1000,
            citation="{analysis} · {scope} · as of {as_of}")],
        # PostgreSQL's cumulative counters, read exactly and differenced: no estimate, no score.
        confidence=ConfidenceDeclaration(form="measured"),
        # A table is in one band of write rate.
        categories=CategoriesDeclaration(exclusive=True),
        scope="member",
    ),
)
