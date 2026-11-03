"""A file system's folder survey, as Egeria's own folder-survey annotation types (Files step 2).

When Resource Explorer's walk leads (only RE can read the path), it publishes the SAME annotation types Egeria's
`FolderSurveyService` writes, under the same names, so Understanding reads an Egeria-led report and an RE-led one the
same way. The names come from Egeria's source, not from memory:

* `SurveyFolderAnnotationType` (`frameworks/open-survey-framework/.../controls/`): the annotation names and their open
  metadata types, and each one's analysis step.
* `FileDirectoryMetric` (`.../measurements/`): the keys of "Capture File Counts" `resourceProperties` (the DISPLAY names;
  the camelCase names are the keys of its `jsonProperties`).
* `FolderSurveyService.FolderProfile.getFolderAnnotation()` / `getAnnotations()`: which annotation carries what.
* `AnalysisStep`: "Profiling Associated Resources" for the measurements and profiles, "Produce Exceptions" for the two
  requests for action.

"Profile File Names to External Log" is NEVER built here, and since fix round 1 NO path or file name reaches any annotation
property at all: the publish carries counts only (`folder_specs` without `include_paths`). The paths stay in Resource
Explorer's registry and the /next card shows them from there.

Three pure layers, so each is testable without Egeria:

    `folder_specs(survey_data)`      the walk's numbers -> neutral annotation specs (dicts)
    `to_annotations(specs)`          specs -> `survey_report.Annotation` objects, for the publish
    `read_folder_survey(specs)`      specs (RE's own, or Egeria's stored annotations) -> ONE view dict, the shape the
                                     survey card and Understanding read
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from resource_explorer.surveyors.file_type_profile import (
    CAPTURE_FILE_COUNTS,
    EGERIA_ANALYSIS_STEP,
    PROFILE_ASSET_TYPES,
    PROFILE_FILE_EXTENSIONS,
    PROFILE_FILE_NAMES,
    PROFILE_FILE_TYPES,
    _TEXT as _SHARED_TEXT,
    java_double_string,
)

# ── Egeria's annotation names (SurveyFolderAnnotationType) ──────────────────────────────────────────────────────────
PROFILE_DEP_IMPL_TYPES = "Profile Deployed Implementation Types"
MISSING_REF_DATA = "Missing File Reference Data"
INACCESSIBLE_FILES = "Inaccessible files"

#: AnalysisStep.PRODUCE_ACTIONS.getName(), used by the two requests for action.
PRODUCE_EXCEPTIONS_STEP = "Produce Exceptions"

#: Every annotation name this builder may emit. "Profile File Names to External Log" is deliberately absent.
EMITTED_NAMES = (CAPTURE_FILE_COUNTS, PROFILE_FILE_EXTENSIONS, PROFILE_FILE_TYPES, PROFILE_ASSET_TYPES,
                 PROFILE_DEP_IMPL_TYPES, MISSING_REF_DATA, INACCESSIBLE_FILES)

# ── FileDirectoryMetric display names (the keys of "Capture File Counts" resourceProperties) ─────────────────────
M_FILE_COUNT = "Number of files"
M_TOTAL_SIZE = "Total file size"
M_SUBDIRS = "Number of subdirectories (folders)"
M_READABLE = "Readable files/directories"
M_WRITABLE = "Writable files/directories"
M_EXECUTABLE = "Executable files/directories"
M_SYMLINKS = "Symbolic Link File Count"
M_HIDDEN = "Hidden File Count"
M_NAMES = "Number of unique filenames"
M_EXTENSIONS = "Number of unique file extensions"
M_FILE_TYPES = "Number of file types"
M_ASSET_TYPES = "Number of asset types"
M_DEPLOYED_TYPES = "Number of deployed implementation types"
M_UNCLASSIFIED = "Number of unclassified files"
M_INACCESSIBLE = "Number of inaccessible files"
M_LAST_CREATED = "Last file creation time"
M_LAST_MODIFIED = "Last file modification time"
M_LAST_ACCESSED = "Last file access time"

#: display name -> the camelCase `FileDirectoryMetric.getName()` that keys `jsonProperties`.
JSON_NAME = {
    M_FILE_COUNT: "fileCount", M_TOTAL_SIZE: "totalFileSize", M_SUBDIRS: "subDirectoryCount",
    M_READABLE: "readableFileCount", M_WRITABLE: "writeableFileCount", M_EXECUTABLE: "executableFileCount",
    M_SYMLINKS: "symLinkFileCount", M_HIDDEN: "hiddenFileCount", M_NAMES: "fileNameCount",
    M_EXTENSIONS: "fileExtensionCount", M_FILE_TYPES: "fileTypeCount", M_ASSET_TYPES: "assetTypeCount",
    M_DEPLOYED_TYPES: "deployedImplementationTypeCount", M_UNCLASSIFIED: "unclassifiedFileCount",
    M_INACCESSIBLE: "inaccessibleFileCount", M_LAST_CREATED: "lastFileCreationTime",
    M_LAST_MODIFIED: "lastFileModificationTime", M_LAST_ACCESSED: "lastFileAccessedTime",
}

#: Egeria's summary and explanation (SurveyFolderAnnotationType), "catalogued" read "published" (owner, 2026-10-12).
_TEXT = dict(_SHARED_TEXT)
_TEXT[PROFILE_DEP_IMPL_TYPES] = (
    "Iterate through files under a directory (folder) and count each potential deployed implementation type if they "
    "were to be published to open metadata.",
    "The deployed implementation type reflects the technology employed within this resource.  The deployed "
    "implementation type is defined as reference data in Egeria.")
_TEXT[MISSING_REF_DATA] = (
    "List of files that could not be classified using the file reference data.",
    "The survey service uses reference data to classify the purpose of files based on their file name.  This "
    "annotation captures all of the file names that could not be matched to the reference data. If any of these "
    "reveal files that are important to your organization then the reference data should be enhanced to include "
    "these files.")
_TEXT[INACCESSIBLE_FILES] = (
    "List of files that the survey service was unable to retrieve the basic attributes for.",
    "The survey service retrieves the basic attributes of a file as part of its profiling effort.  Files that "
    "returned an I/O error when their basic properties were read are listed here.")


def classify_for_folder_survey(paths: Iterable[str]) -> dict:
    """Egeria's file classification of plain paths, three ways, from the SAME reference data the folder survey uses
    (the Egeria-backed `FileTypeCache`, else its built-in table). Names and extensions only; no file is opened.

    A file with no file type, asset type or deployed implementation type is "unclassified", as in
    `FolderSurveyService` (it adds the file to `missingReferenceData` when ANY of the three is missing). A missing value
    is skipped in its profile, never counted under a made-up name."""
    from pathlib import Path

    from resource_explorer.surveyors.file_classifier.file_classifier import FileClassifier
    from resource_explorer.surveyors.file_classifier.type_cache import get_cache

    cache = get_cache()
    helper = FileClassifier(pyegeria_client=None)
    ext_counts: Counter = Counter()
    file_types: Counter = Counter()
    asset_types: Counter = Counter()
    deployed: Counter = Counter()
    unclassified: list[str] = []
    names: set[str] = set()
    total = 0
    for path in paths:
        total += 1
        name = Path(path).name
        names.add(name)
        ext = helper.get_file_extension(name)
        ext_counts[ext or "(none)"] += 1
        meta = cache.lookup(name, ext) or {}
        ft, at, di = meta.get("fileType"), meta.get("assetTypeName"), meta.get("deployedImplementationType")
        if ft:
            file_types[ft] += 1
        if at:
            asset_types[at] += 1
        if di:
            deployed[di] += 1
        if not (ft and at and di):
            unclassified.append(path)
    return {
        "file_count": total, "extension_counts": dict(ext_counts), "file_type_counts": dict(file_types),
        "asset_type_counts": dict(asset_types), "deployed_type_counts": dict(deployed),
        "unclassified_count": len(unclassified), "unclassified_sample": unclassified[:20],
        "unique_filename_count": len(names), "source": "egeria-or-builtin-reference-data",
    }


def _metrics(sd: dict) -> dict[str, str]:
    """`resourceProperties` of "Capture File Counts": every value a string, as Egeria writes them. A metric the walk did
    not measure is OMITTED, never written as zero."""
    cls = sd.get("classification") or {}
    out: dict[str, str] = {}

    def put(key: str, value: Any) -> None:
        if value is not None:
            out[key] = str(value)

    put(M_FILE_COUNT, sd.get("total_files"))
    if sd.get("total_size_bytes") is not None:
        out[M_TOTAL_SIZE] = java_double_string(float(sd["total_size_bytes"]))
    put(M_SUBDIRS, sd.get("total_directories"))
    put(M_READABLE, sd.get("readable_count"))
    put(M_WRITABLE, sd.get("writable_count"))
    put(M_EXECUTABLE, sd.get("executable_count"))
    put(M_SYMLINKS, sd.get("symlink_count"))
    put(M_HIDDEN, sd.get("hidden_count"))
    put(M_NAMES, sd.get("unique_filename_count"))
    put(M_EXTENSIONS, sd.get("unique_extension_count"))
    if "file_type_counts" in cls:
        put(M_FILE_TYPES, len(cls["file_type_counts"]))
        put(M_ASSET_TYPES, len(cls.get("asset_type_counts") or {}))
        put(M_DEPLOYED_TYPES, len(cls.get("deployed_type_counts") or {}))
        put(M_UNCLASSIFIED, cls.get("unclassified_count"))
    put(M_INACCESSIBLE, sd.get("inaccessible_file_count"))
    put(M_LAST_CREATED, sd.get("last_creation_time") or None)
    put(M_LAST_MODIFIED, sd.get("last_modification_time") or None)
    put(M_LAST_ACCESSED, sd.get("last_access_time") or None)
    return out


def folder_specs(sd: dict, *, directory_name: str = "", include_paths: bool = False) -> list[dict]:
    """The walk's `survey_data` as neutral annotation specs under Egeria's names. Pure.

    **Counts only, by default (fix round 1, M3, coordinator ruling pending the owner's word).** No file path and no file or
    folder name goes into any spec unless `include_paths` is True: that is for Resource Explorer's OWN reader (the card,
    which shows the paths from RE's registry), never for the publish. On the owner's tree the paths include names like
    `.env` and `.omsecrets`, and Egeria is a shared catalog. `directory_name` is likewise only used with `include_paths`.

    `survey_data` is what `LocalFileSystemSurveyor` returns (structure phase). A section the walk could not fill (it ran
    without classification) is left out, not published as an empty profile."""
    cls = sd.get("classification") or {}
    metrics = _metrics(sd)
    json_props = {JSON_NAME[k]: v for k, v in metrics.items() if k in JSON_NAME}
    if directory_name and include_paths:
        json_props["directoryName"] = directory_name

    def spec(name: str, kind: str, **extra) -> dict:
        summary, explanation = _TEXT[name]
        return {"annotation_type_name": name, "kind": kind, "analysis_step": EGERIA_ANALYSIS_STEP,
                "summary": summary, "explanation": explanation, **extra}

    specs = [spec(CAPTURE_FILE_COUNTS, "measure", resource_properties=metrics, json_properties=json_props)]
    for name, key in ((PROFILE_FILE_EXTENSIONS, "extension_counts"), (PROFILE_FILE_TYPES, "file_type_counts"),
                      (PROFILE_ASSET_TYPES, "asset_type_counts"), (PROFILE_DEP_IMPL_TYPES, "deployed_type_counts")):
        if key in cls:
            specs.append(spec(name, "profile", value_count={str(k): int(v) for k, v in cls[key].items()}))
    if cls.get("unclassified_count"):
        specs.append(spec(
            MISSING_REF_DATA, "rfa", analysis_step=PRODUCE_EXCEPTIONS_STEP,
            action_requested="Review the files that could not be classified and add them to Egeria's file reference data.",
            json_properties={"count": cls["unclassified_count"],
                             **({"sample_paths": list(cls.get("unclassified_sample") or [])[:20]} if include_paths else {})}))
    inaccessible = sd.get("inaccessible_files") or []
    if sd.get("inaccessible_file_count") or inaccessible:
        specs.append(spec(
            INACCESSIBLE_FILES, "rfa", analysis_step=PRODUCE_EXCEPTIONS_STEP,
            action_requested="Review permissions on the files and folders that could not be read.",
            json_properties={"count": sd.get("inaccessible_file_count", len(inaccessible)),
                             **({"inaccessible": [i.get("path", i) if isinstance(i, dict) else i
                                                  for i in inaccessible[:100]]} if include_paths else {})}))
    return specs


def to_annotations(specs: list[dict], *, additional: dict | None = None) -> list:
    """Neutral specs -> `survey_report` annotation objects for the publish. `additional` rides in each annotation's
    `additionalProperties` (RE's envelope: surveyor, walk limits, capped), which Egeria's own types do not carry."""
    from resource_explorer.surveyors.survey_report import (
        RequestForActionAnnotation, ResourceMeasureAnnotation, ResourceProfileAnnotation,
    )

    out = []
    for s in specs:
        cname = s["annotation_type_name"].lower().replace(" ", "_")     # a stable identity for the check, per annotation
        common = dict(analysis_step=s["analysis_step"], annotation_type_name=s["annotation_type_name"],
                      summary=s["summary"], explanation=s["explanation"], additional_properties=dict(additional or {}))
        if s["kind"] == "measure":
            out.append(ResourceMeasureAnnotation(check_name=cname, **common,
                                                 resource_properties=dict(s["resource_properties"]),
                                                 json_properties=dict(s.get("json_properties") or {})))
        elif s["kind"] == "profile":
            out.append(ResourceProfileAnnotation(check_name=cname, **common, value_count=dict(s["value_count"])))
        else:
            out.append(RequestForActionAnnotation(check_name=cname, **common,
                                                  action_requested=s.get("action_requested", ""),
                                                  json_properties=dict(s.get("json_properties") or {})))
    return out


# ── reading: one view for both leads ──────────────────────────────────────────────────────────────────────────────

def _pick(a: dict, key: str, default=None):
    """A field of a spec or of a stored Egeria annotation (whose wire fields sit under `detail`)."""
    if key in a and a[key] not in (None, "", {}, []):
        return a[key]
    d = a.get("detail") or {}
    return d.get(key, default) if isinstance(d, dict) else default


def _int(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


#: Egeria's Folder Survey counts the files it could not classify under a key it writes as the word "null" (W20).
UNCLASSIFIED_WORDS = "unclassified"
_UNNAMED_KEYS = ("null", "none", "")


def _counts(a: dict | None) -> list[dict]:
    if not a:
        return []
    vc = _pick(a, "value_count", {}) or {}
    merged: dict[str, int | None] = {}
    for k, v in vc.items():
        name = UNCLASSIFIED_WORDS if k is None or str(k).strip().lower() in _UNNAMED_KEYS else k
        n = _int(v)
        merged[name] = n if name not in merged else (None if n is None or merged[name] is None else merged[name] + n)
    return [{"name": k, "count": v} for k, v in sorted(merged.items(), key=lambda kv: (-(kv[1] or 0), kv[0]))]


def read_folder_survey(annotations: list[dict]) -> dict:
    """The folder-survey results out of annotations, whoever made them. Pure.

    Takes RE's own specs (`folder_specs`) or Egeria's stored annotations (`query_native_survey_annotations`, whose wire
    fields are under `detail`). Returns the numbers the survey card shows. A number the annotations do not carry is
    None ("not measured"), never 0. "Profile File Names to External Log" is ignored if present: RE does not read a
    names log."""
    by_name: dict[str, dict] = {}
    for a in annotations:
        name = a.get("annotation_type_name") or a.get("annotation_type") or ""
        if name and name != PROFILE_FILE_NAMES:
            by_name.setdefault(name, a)
    measure = by_name.get(CAPTURE_FILE_COUNTS)
    props = (_pick(measure, "resource_properties", {}) or {}) if measure else {}

    def m(key: str) -> int | None:
        return _int(props.get(key))

    inacc = by_name.get(INACCESSIBLE_FILES)
    inacc_list = None
    if inacc:
        jp = _pick(inacc, "json_properties", {}) or {}
        if isinstance(jp, dict) and isinstance(jp.get("inaccessible"), list):
            inacc_list = [str(x) for x in jp["inaccessible"]]
    inacc_count = m(M_INACCESSIBLE)
    return {
        "measured": measure is not None,
        "counts": {
            "files": m(M_FILE_COUNT), "total_size_bytes": (_int(props.get(M_TOTAL_SIZE)) if M_TOTAL_SIZE in props else None),
            "subdirectories": m(M_SUBDIRS), "readable": m(M_READABLE), "writable": m(M_WRITABLE),
            "executable": m(M_EXECUTABLE), "symlinks": m(M_SYMLINKS), "hidden": m(M_HIDDEN),
            "unique_filenames": m(M_NAMES), "unique_extensions": m(M_EXTENSIONS), "file_types": m(M_FILE_TYPES),
            "asset_types": m(M_ASSET_TYPES), "deployed_types": m(M_DEPLOYED_TYPES),
            "unclassified": m(M_UNCLASSIFIED), "inaccessible": inacc_count,
        },
        "times": {"last_created": props.get(M_LAST_CREATED) or None, "last_modified": props.get(M_LAST_MODIFIED) or None,
                  "last_accessed": props.get(M_LAST_ACCESSED) or None},
        "file_types": _counts(by_name.get(PROFILE_FILE_TYPES)),
        "extensions": _counts(by_name.get(PROFILE_FILE_EXTENSIONS)),
        "asset_types": _counts(by_name.get(PROFILE_ASSET_TYPES)),
        "deployed_types": _counts(by_name.get(PROFILE_DEP_IMPL_TYPES)),
        # The names of the inaccessible files: RE's walk lists them; Egeria writes them to a CSV asset (not in the
        # annotation), so an Egeria-led read has the COUNT and the fact of the request, not the list.
        "inaccessible": {"count": inacc_count, "listed": inacc_list, "requested": inacc is not None},
        "has_sections": {n: (n in by_name) for n in EMITTED_NAMES},
    }
