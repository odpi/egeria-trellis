"""The step's manifest: the wrapper's declaration. Its version is the package's."""
from trellis_analytics.declaration import (AbsenceSentences, AnnotationDeclaration, ConfidenceDeclaration,
                                           Declaration, ExtentDeclaration, MeasureDeclaration)
from trellis_analytics.manifest import StepManifest

from fs_duplicates import __version__

MANIFEST = StepManifest(
    name="fs_duplicates",
    display_name="Duplicate Files",
    function="fs_duplicates.step.run",
    description=("Files with the same content (size and SHA-256) within a file system, and files whose content "
                 "another registered file system also holds, from the hashes Resource Explorer's newest walk kept. "
                 "Opens no file. Where the walk hashed nothing (hashing off, every file over the limit) it is shown "
                 "as \"not measured\", never as no duplicates; files the walk did not hash are counted as not "
                 "compared."),
    returns=("two ResourceMeasureAnnotations for the file system (duplicates here; content also held elsewhere), "
             "each with the walk's as-of and its envelope"),
    source="ods",
    version=__version__,
    resource_types=["filesystem"],
    annotation_type="ResourceMeasureAnnotation",
    credential_tier="none",
    inputs=["ods"],
    measures=["fs_duplicates", "fs_duplicates_elsewhere"],
    stage="analysis",
    perspectives=["Steward", "Data Expert", "Financial"],
    # Q-0106: "Which files are duplicates of each other, or of files in another registered file system?" (F6)
    questions=["Q-0106"],
    purposes=["Assess", "Maintain"],
    family="Redundancy",
    tags=["file system", "duplicates", "hash"],
    request_type="re-fs-duplicates",
    nothing_to_derive_guard="survey-completed",
    declaration=Declaration(
        result_kind="measure",
        measures=[
            MeasureDeclaration(name="fs_duplicates", unit="copies beyond the first of identical files", as_of="asOf",
                               value_field="Redundant Files",
                               annotation=AnnotationDeclaration(type="ResourceMeasureAnnotation")),
            MeasureDeclaration(name="fs_duplicates_elsewhere",
                               unit="files whose content another registered file system also holds", as_of="asOf",
                               value_field="Files Also Elsewhere",
                               annotation=AnnotationDeclaration(type="ResourceMeasureAnnotation"))],
        absence=AbsenceSentences(
            not_measured=("Not measured: the newest walk hashed no file (hashing off, or every file over the limit "
                          "or secret-named), or no other registered file system has hashes to compare with."),
            not_permitted=("Not permitted: the walk could not read these files, so none was hashed and none can be "
                           "compared."),
            not_applicable="Not applicable: duplicate content is measured for file systems only.",
            measured_nothing="Measured: no two hashed files have the same content."),
        extents=[ExtentDeclaration(
            name="duplicate_content",
            fields=["scope", "Duplicate Sets", "Redundant Files", "Redundant Bytes", "Files Not Hashed",
                    "Files Also Elsewhere", "Matches By File System", "asOf"],
            rows=2, order="duplicates here, then content also held elsewhere",
            truncation="{shown} of {total} results shown", token_cost=300,
            citation="{analysis} · {scope} · as of {as_of}")],
        # A SHA-256 match on equal sizes is taken as the same content: exact, with no score.
        confidence=ConfidenceDeclaration(form="measured"),
        scope="resource",
    ),
)
