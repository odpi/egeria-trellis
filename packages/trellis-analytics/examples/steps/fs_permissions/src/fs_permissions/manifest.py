"""The step's manifest: the wrapper's declaration. Its version is the package's."""
from trellis_analytics.declaration import (AbsenceSentences, AnnotationDeclaration, ConfidenceDeclaration,
                                           Declaration, ExtentDeclaration, MeasureDeclaration)
from trellis_analytics.manifest import StepManifest

from fs_permissions import __version__

MANIFEST = StepManifest(
    name="fs_permissions",
    display_name="File Permissions",
    function="fs_permissions.step.run",
    description=("Owners, groups and modes as counts (world-readable, world-writable, group-writable, setuid), and "
                 "world-readable files whose names look like secrets as a request for action, from the owner, group "
                 "and mode Resource Explorer's newest walk kept. Opens no file and publishes no name. With no stat "
                 "fields, or no file name classified, it is shown as \"not measured\", never as none found."),
    returns=("a ResourceProfileAnnotation of the file system's permissions and a RequestForActionAnnotation for "
             "world-readable secret-named files, each with its envelope"),
    source="ods",
    version=__version__,
    resource_types=["filesystem"],
    annotation_type="ResourceProfileAnnotation",
    credential_tier="none",
    inputs=["ods"],
    measures=["fs_permissions", "world_readable_sensitive"],
    measure_annotation_types={"world_readable_sensitive": "RequestForActionAnnotation"},
    stage="analysis",
    perspectives=["Security", "Privacy", "Governance"],
    # Q-0108: "Who owns or can read what, and are there world-readable sensitive files?" (F9)
    questions=["Q-0108"],
    purposes=["Assess", "Certify"],
    family="Access",
    tags=["file system", "permissions", "security"],
    request_type="re-fs-permissions",
    nothing_to_derive_guard="survey-completed",
    declaration=Declaration(
        result_kind="measure",
        measures=[
            MeasureDeclaration(name="fs_permissions", unit="world-readable files", as_of="asOf",
                               value_field="World Readable Files",
                               annotation=AnnotationDeclaration(type="ResourceProfileAnnotation")),
            MeasureDeclaration(name="world_readable_sensitive",
                               unit="world-readable files with a secret-looking name", as_of="asOf",
                               value_field="World Readable Secret Named Files",
                               annotation=AnnotationDeclaration(type="RequestForActionAnnotation"))],
        absence=AbsenceSentences(
            not_measured=("Not measured: no walk has kept per-file rows, the walk recorded no owner, group or mode "
                          "(no stat fields), or it classified no file name, so who can read what cannot be told."),
            not_permitted=("Not permitted: the walk could not stat these files, so no owner or mode is held for "
                           "them."),
            not_applicable="Not applicable: file permissions are measured for file systems only.",
            measured_nothing="Measured: no world-readable file has a secret-looking name."),
        extents=[ExtentDeclaration(
            name="permission_counts",
            fields=["scope", "Distinct Owners", "Distinct Groups", "World Readable Files", "World Writable Files",
                    "World Readable Secret Named Files", "Files Whose Names Were Not Classified", "asOf"],
            rows=2, order="the permissions profile, then the request for action",
            truncation="{shown} of {total} results shown", token_cost=300,
            citation="{analysis} · {scope} · as of {as_of}")],
        # Mode bits read from stat and counted exactly; "secret-looking" is the walk's name rule, said in the basis.
        confidence=ConfidenceDeclaration(form="measured"),
        scope="resource",
    ),
)
