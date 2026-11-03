"""File types and file counts as Egeria's own folder-survey annotations.

Ruling 2026-10-07 (DESIGN-FILE-TYPES-AS-ANNOTATIONS.md): a file type is a MEASUREMENT, published as
profile annotations in the survey report under Egeria's names, verbatim, never as a DataSet per type.

    Capture File Counts      ResourceMeasureAnnotation   files, directories, distinct extensions, types
    Profile File Extensions  ResourceProfileAnnotation   count per extension
    Profile File Types       ResourceProfileAnnotation   count per file type (resolve_technology_type)
    Profile Asset Types      ResourceProfileAnnotation   what cataloging the SELECTED items would create
    Profile File Names to External Log  ResourceProfileLogAnnotation  only when a person asks, a CSV (never default)

Every count here comes from `project_file_inventory`, the table the inventory step refreshes, so the
profile equals the inventory by construction. The envelope (resultState, measuredAt, producingRun,
scope) rides in `additionalProperties`, which Egeria's own types do not carry.
"""
from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath

from resource_explorer.surveyors.survey_report import (
    Annotation, ResourceMeasureAnnotation, ResourceProfileAnnotation, ResourceProfileLogAnnotation,
)

CAPTURE_FILE_COUNTS = "Capture File Counts"
PROFILE_FILE_EXTENSIONS = "Profile File Extensions"
PROFILE_FILE_TYPES = "Profile File Types"
PROFILE_ASSET_TYPES = "Profile Asset Types"
#: Egeria's own name for this one is "Profile File Names to External Log" (SurveyFolderAnnotationType).
PROFILE_FILE_NAMES = "Profile File Names to External Log"

#: Egeria's summary and explanation from SurveyFolderAnnotationType, with one named departure: "catalogued" reads "published" (project owner, 2026-10-12: Publish is the one verb for writing to Egeria).
_TEXT = {
    CAPTURE_FILE_COUNTS: ("Count up the number of files and directories under the surveyed directory that have specific characteristics.",
                          "Count the number of directories and files under the starting directory."),
    PROFILE_FILE_EXTENSIONS: ("Iterate through files under a directory (folder) and count the occurrences of each file extension.",
                              "The file extension often provides a hint as to the type of file."),
    PROFILE_FILE_TYPES: ("Iterate through files under a directory (folder) and count the occurrences of each file type.",
                         "The file type is a category of file that describes its use.  The file types are defined as reference data in Egeria."),
    PROFILE_ASSET_TYPES: ("Iterate through files under a directory (folder) and count each potential asset type if they were to be published to open metadata.",
                          "The asset type is an open metadata type.  This annotation identifies the numbers of each type of asset that will be created if the files were published to the open metadata ecosystem."),
    PROFILE_FILE_NAMES: ("Iterate through files under a directory (folder) and count the occurrences of each file name.",
                         "Some file names indicate a file of special type/use.  This profile information is likely to be large so it is logged to a CSV File."),
}

#: FileDirectoryMetric display names (the keys of Egeria's resourceProperties), verbatim, for the
#: metrics RE can compute from its inventory. The rest (readable/writable/executable, symbolic links,
#: hidden, deployed implementation types, unclassified, inaccessible, last file times) are not
#: measured by RE and are OMITTED, never written as zero.
M_FILES = "Number of files"
M_SIZE = "Total file size"
M_DIRS = "Number of subdirectories (folders)"
M_NAMES = "Number of unique filenames"
M_EXTS = "Number of unique file extensions"
M_TYPES = "Number of file types"
M_ASSET_TYPES = "Number of asset types"

#: The names the default publish carries. Profile File Names is deliberately absent (size).
DEFAULT_NAMES = (CAPTURE_FILE_COUNTS, PROFILE_FILE_EXTENSIONS, PROFILE_FILE_TYPES, PROFILE_ASSET_TYPES)

#: RE's own attribution key, written as `producingStep` in additionalProperties (extend, never change):
#: which RE step made the annotation. analysisStep itself is Egeria's, verbatim, for these types.
STEP = "FileInventory"
#: AnalysisStep.PROFILING_ASSOCIATED_RESOURCES.getName(); SurveyFolderAnnotationType.getAnalysisStep()
#: returns it for every folder-survey annotation type.
EGERIA_ANALYSIS_STEP = "Profiling Associated Resources"


def java_double_string(x: float) -> str:
    """Java's Double.toString for a non-negative whole-number size: plain below 1e7 ("49.0"),
    scientific from 1e7 ("1.2345678E7"), which is what Egeria stores for "Total file size"."""
    if x < 1e7:
        return repr(float(x))
    digits = repr(float(x)).split(".")[0] if x < 1e16 else f"{x:.0f}"
    exp = len(digits) - 1
    mant = digits[0] + "." + (digits[1:].rstrip("0") or "0")
    return f"{mant}E{exp}"


def _extension(path: str) -> str:
    name = PurePosixPath(path).name
    return name.rsplit(".", 1)[-1].lower() if "." in name.lstrip(".") else ""


def _directories(paths: list[str]) -> set[str]:
    out: set[str] = set()
    for p in paths:
        parent = PurePosixPath(p).parent
        while str(parent) not in (".", ""):
            out.add(str(parent))
            parent = parent.parent
    return out


def envelope(*, measured_at: str, producing_run: str, partial_reason: str = "") -> dict:
    env = {"producingStep": STEP, "resultState": "MEASURED", "measuredAt": measured_at, "producingRun": producing_run,
           "scope": "PARTIAL" if partial_reason else "WHOLE"}
    if partial_reason:
        env["scopeReason"] = partial_reason
    return env


def selected_asset_types(registry, slug: str) -> Counter:
    """What cataloging the selected items WOULD create, by Egeria asset type: the rows of the
    `sub_resources` table are the selection, and publish creates one FileFolder or DataFile per row,
    so this equals the Curate manifest's file and folder counts by construction."""
    out: Counter = Counter()
    for r in registry.list_sub_resources("repo", slug):
        out["FileFolder" if r.get("kind") == "folder" else "DataFile"] += 1
    return out


def build_file_type_annotations(registry, slug: str, *, surveyed_at: str,
                                partial_reason: str = "") -> list[Annotation]:
    """The four default annotations for one repository, from its stored inventory.

    `Profile Asset Types` is emitted only when something is selected: an empty selection is "nothing
    chosen", not a measured zero, so it is left out rather than published as an empty profile."""
    from resource_explorer.surveyors.sub_resource_templates import resolve_technology_type

    rows = registry.get_file_inventory_with_sizes(slug, include_vendored=True)
    paths = [r["file_path"] for r in rows]
    if not paths:
        # An empty inventory is "nothing was measured" (the step records it as unverified), never
        # a MEASURED zero: publish no file-type annotation at all rather than a profile of nothing.
        return []
    ext_counts = Counter(_extension(p) or "(none)" for p in paths)
    type_counts = Counter(resolve_technology_type(p) for p in paths)
    env = envelope(measured_at=surveyed_at, producing_run=f"{slug}::{surveyed_at}", partial_reason=partial_reason)
    vendored = sum(1 for r in rows if r.get("vendored"))

    n_dirs = len(_directories(paths))
    names = {PurePosixPath(p).name for p in paths}
    chosen = selected_asset_types(registry, slug)
    measures = {M_FILES: len(paths), M_SIZE: java_double_string(float(sum(r["file_size_bytes"] for r in rows))),
                M_DIRS: n_dirs, M_NAMES: len(names), M_EXTS: len(ext_counts), M_TYPES: len(type_counts)}
    if chosen:
        measures[M_ASSET_TYPES] = len(chosen)        # only when something is selected: not a measured zero

    def _text(name):
        return {"summary": _TEXT[name][0], "explanation": _TEXT[name][1]}

    out: list[Annotation] = [
        ResourceMeasureAnnotation(
            check_name="file_counts", analysis_step=EGERIA_ANALYSIS_STEP, annotation_type_name=CAPTURE_FILE_COUNTS,
            **_text(CAPTURE_FILE_COUNTS), resource_properties=measures,
            additional_properties={**env, "vendoredFileCount": vendored}),
        ResourceProfileAnnotation(
            check_name="file_extensions", analysis_step=EGERIA_ANALYSIS_STEP, annotation_type_name=PROFILE_FILE_EXTENSIONS,
            **_text(PROFILE_FILE_EXTENSIONS), value_count=dict(ext_counts), additional_properties=dict(env)),
        ResourceProfileAnnotation(
            check_name="file_types", analysis_step=EGERIA_ANALYSIS_STEP, annotation_type_name=PROFILE_FILE_TYPES,
            **_text(PROFILE_FILE_TYPES), value_count=dict(type_counts), additional_properties=dict(env)),
    ]
    if chosen:
        out.append(ResourceProfileAnnotation(
            check_name="asset_types", analysis_step=EGERIA_ANALYSIS_STEP, annotation_type_name=PROFILE_ASSET_TYPES,
            **_text(PROFILE_ASSET_TYPES), value_count=dict(chosen),
            additional_properties={**env, "basis": "the selection on Curate"}))
    return out


def file_names_log_annotation(registry, slug: str, *, surveyed_at: str, csv_path: str) -> ResourceProfileLogAnnotation:
    """"Profile File Names", ON REQUEST only: the per-name counts are written to `csv_path` (one
    `name,count` line each) and the annotation points at that file. Never part of the default publish."""
    import csv
    names = Counter(PurePosixPath(r["file_path"]).name
                    for r in registry.get_file_inventory_with_sizes(slug, include_vendored=True))
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["fileName", "count"])
        for name, n in sorted(names.items(), key=lambda kv: (-kv[1], kv[0])):
            w.writerow([name, n])
    return ResourceProfileLogAnnotation(
        check_name="file_names", analysis_step=EGERIA_ANALYSIS_STEP, annotation_type_name=PROFILE_FILE_NAMES,
        summary=_TEXT[PROFILE_FILE_NAMES][0], explanation=_TEXT[PROFILE_FILE_NAMES][1], log_file=csv_path,
        additional_properties=envelope(measured_at=surveyed_at, producing_run=f"{slug}::{surveyed_at}"))
