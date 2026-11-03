"""The step's manifest: the wrapper's declaration. Its version is the package's."""
from trellis_analytics.declaration import (AbsenceSentences, AnnotationDeclaration, ConfidenceDeclaration,
                                           Declaration, ExtentDeclaration, MeasureDeclaration)
from trellis_analytics.manifest import StepManifest

from fs_layout_signals import __version__

MANIFEST = StepManifest(
    name="fs_layout_signals",
    display_name="File System Layout Signals",
    function="fs_layout_signals.step.run",
    description=("Whether the file system holds a recognisable structure (a project, a dataset layout, an archive, "
                 "backups) from the names on Resource Explorer's newest walk: project manifests and .git directories, "
                 "dated and partition folders, dictionary and schema files, archive and backup names. Opens no file "
                 "and publishes no name. With no walk it is shown as \"not measured\", never as nothing recognised."),
    returns="a ResourceProfileAnnotation of the structures recognised, as counts, with its envelope",
    source="ods",
    version=__version__,
    resource_types=["filesystem"],
    annotation_type="ResourceProfileAnnotation",
    credential_tier="none",
    inputs=["ods"],
    measures=["fs_layout_signals"],
    stage="discovery",
    perspectives=["Data Expert", "Steward", "Architecture"],
    # Q-0104: "Does this file system contain a recognisable structure: a project, a dataset layout, an archive,
    # backups?" (F10)
    questions=["Q-0104"],
    purposes=["Select", "Explore"],
    family="Structure",
    tags=["file system", "layout", "discovery"],
    request_type="re-fs-layout-signals",
    nothing_to_derive_guard="survey-completed",
    declaration=Declaration(
        result_kind="measure",
        measures=[
            MeasureDeclaration(name="fs_layout_signals", unit="recognised structures", as_of="asOf",
                               value_field="Recognised Structures",
                               annotation=AnnotationDeclaration(type="ResourceProfileAnnotation"))],
        absence=AbsenceSentences(
            not_measured="Not measured: no walk has kept per-file rows, so no name could be matched.",
            not_permitted="Not permitted: the walk could not list these folders, so their names are not held.",
            not_applicable="Not applicable: layout signals are measured for file systems only.",
            measured_nothing=("Measured: no project manifest or .git directory, no dated or partition folders or "
                              "dictionary file, no archive or backup file on the walk.")),
        extents=[ExtentDeclaration(
            name="layout_counts",
            fields=["scope", "Recognised As", "Project Manifests", "Git Directories Not Walked", "Dated Folders",
                    "Partition Folders", "Data Dictionary Or Schema Files", "Archive Files", "Backup Files",
                    "Folders With A README", "asOf"],
            rows=1, order="the one layout profile",
            truncation="{shown} of {total} results shown", token_cost=260,
            citation="{analysis} · {scope} · as of {as_of}")],
        # Names matched against fixed patterns and counted exactly; the patterns are said in the result's rules.
        confidence=ConfidenceDeclaration(form="measured"),
        scope="resource",
    ),
)
