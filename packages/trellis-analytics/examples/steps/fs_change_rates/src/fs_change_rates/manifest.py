"""The step's manifest: the wrapper's declaration. Its version is the package's."""
from trellis_analytics.declaration import (AbsenceSentences, AnnotationDeclaration, ConfidenceDeclaration,
                                           Declaration, ExtentDeclaration, MeasureDeclaration)
from trellis_analytics.manifest import StepManifest

from fs_change_rates import __version__

MANIFEST = StepManifest(
    name="fs_change_rates",
    display_name="File System Change",
    function="fs_change_rates.step.run",
    description=("Files added, removed and modified, and the growth in bytes, between the latest two walks Resource "
                 "Explorer kept for a file system, over their interval. Reads the walks' per-file rows; opens no "
                 "file. With one walk only it is shown as \"not measured\", never as no change; where a walk was "
                 "capped, files added, removed and growth are \"not measured\" and only the files both walks saw "
                 "are compared."),
    returns="one ResourceMeasureAnnotation for the file system, with the interval, both as-ofs and its envelope",
    source="ods",
    version=__version__,
    resource_types=["filesystem"],
    annotation_type="ResourceMeasureAnnotation",
    credential_tier="none",
    inputs=["ods"],
    stage="discovery",
    perspectives=["Steward", "Data Expert"],
    # Q-0102: "What has changed since the last walk: new, modified, removed files, and growth?" (Files brief F5)
    questions=["Q-0102"],
    purposes=["Maintain"],
    family="Freshness",
    tags=["file system", "change", "walk"],
    request_type="re-fs-change-rates",
    # One walk is an absence honestly recorded, a completed survey (the same ruling as change_rates, 2026-10-15).
    nothing_to_derive_guard="survey-completed",
    declaration=Declaration(
        result_kind="measure",
        measures=[MeasureDeclaration(
            name="fs_change_rates", unit="files added, removed or modified between two walks", as_of="toAsOf",
            value_field="Files Changed", annotation=AnnotationDeclaration(type="ResourceMeasureAnnotation"))],
        absence=AbsenceSentences(
            not_measured=("Not measured: Resource Explorer has kept per-file rows from one walk of this file system, "
                          "or none; a change needs two walks."),
            not_permitted=("Not permitted: the walk could not read this file system's folders, so no per-file rows "
                           "were kept to compare."),
            not_applicable="Not applicable: a change between walks is measured for file systems only.",
            measured_nothing="Measured: no file was added, removed or modified between the two walks."),
        extents=[ExtentDeclaration(
            name="change_since_last_walk",
            fields=["scope", "Files Added", "Files Removed", "Files Modified", "Bytes Growth", "Interval Days",
                    "fromAsOf", "toAsOf"],
            rows=1, order="one row for the file system",
            truncation="{shown} of {total} file systems shown", token_cost=200,
            citation="{analysis} · {scope} · as of {as_of}")],
        # Exact comparison of two stored walks: no estimate, no score. A capped walk is said, not estimated.
        confidence=ConfidenceDeclaration(form="measured"),
        scope="resource",
    ),
)
