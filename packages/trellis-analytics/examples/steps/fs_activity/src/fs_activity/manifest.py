"""The step's manifest: the wrapper's declaration. Its version is the package's."""
from trellis_analytics.declaration import (AbsenceSentences, AnnotationDeclaration, ConfidenceDeclaration,
                                           Declaration, ExtentDeclaration, MeasureDeclaration,
                                           CategoriesDeclaration)
from trellis_analytics.manifest import StepManifest

from fs_activity import __version__

MANIFEST = StepManifest(
    name="fs_activity",
    display_name="File Activity",
    function="fs_activity.step.run",
    description=("How long since each file was modified, and since it was read where the mount records access "
                 "times: hot, warm and cold files and bytes, aged at the newest walk's own time, from the per-file "
                 "rows Resource Explorer kept. Opens no file. Where access times were not recorded on the walk or "
                 "the mount does not update them, reads are shown as \"not measured\" (can't tell), never as cold."),
    returns="two ResourceProfileAnnotations for the file system (by modification, by access), each with its envelope",
    source="ods",
    version=__version__,
    resource_types=["filesystem"],
    annotation_type="ResourceProfileAnnotation",
    credential_tier="none",
    inputs=["ods"],
    measures=["fs_activity", "fs_access_activity"],
    # Assumed (owner to confirm): a week is hot, over a year is cold.
    parameters={"hot_days": 7, "cold_days": 365},
    stage="analysis",
    perspectives=["Steward", "Financial", "Data Owner"],
    # Q-0107: "Which files has nobody touched for a long time, and which are hot?" (F8)
    questions=["Q-0107"],
    purposes=["Maintain", "Assess"],
    family="Freshness",
    tags=["file system", "activity", "age"],
    request_type="re-fs-activity",
    nothing_to_derive_guard="survey-completed",
    declaration=Declaration(
        result_kind="distribution",
        measures=[
            MeasureDeclaration(name="fs_activity", unit="files not modified for more than {Cold Days} days (cold)",
                               as_of="asOf", value_field="Cold Files",
                               annotation=AnnotationDeclaration(type="ResourceProfileAnnotation")),
            MeasureDeclaration(name="fs_access_activity", unit="files not read for more than {Cold Days} days (cold)",
                               as_of="asOf", value_field="Cold Files",
                               annotation=AnnotationDeclaration(type="ResourceProfileAnnotation"))],
        absence=AbsenceSentences(
            not_measured=("Not measured: no walk has kept per-file rows, or (for reads) access times were not "
                          "recorded on the walk or the mount does not update them, so which files are read cannot "
                          "be told."),
            not_permitted=("Not permitted: the walk could not read these files' times, so no age is held for them."),
            not_applicable="Not applicable: file activity is measured for file systems only.",
            measured_nothing="Measured: the walk found no files."),
        extents=[ExtentDeclaration(
            name="activity_bands",
            fields=["scope", "Hot Files", "Warm Files", "Cold Files", "Cold Bytes", "Hot Days", "Cold Days",
                    "Access Possibly Resource Explorer's Own Read", "asOf"],
            rows=2, order="by modification, then by access",
            truncation="{shown} of {total} results shown", token_cost=300,
            citation="{analysis} · {scope} · as of {as_of}")],
        # Times read from the walk's stat and aged exactly; a mount that does not record reads is said, not guessed.
        confidence=ConfidenceDeclaration(form="measured"),
        # A file is hot, warm or cold, never two of them.
        categories=CategoriesDeclaration(exclusive=True),
        scope="resource",
    ),
)
