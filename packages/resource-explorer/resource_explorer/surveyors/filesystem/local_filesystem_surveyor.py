from __future__ import annotations

import logging
import os
import socket
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from resource_explorer.registry import FileSystemEntity, ProjectRegistry
from resource_explorer.surveyors.file_classifier.file_classifier_surveyor import classify_file_paths
from resource_explorer.surveyors.filesystem import walk_extensions as we
from resource_explorer.surveyors.filesystem.folder_survey_annotations import classify_for_folder_survey
from resource_explorer.surveyors.sub_surveyors.data_profiler import (
    _DATA_EXTENSIONS,
    _PANDAS_READABLE,
    _SCHEMA_ONLY,
    DataProfilerSurveyor,
    _fmt_size,
)

log = logging.getLogger(__name__)


def build_rfa_annotations(survey_data: dict) -> list[dict]:
    """Build activity-log RequestForAction annotation dicts from a survey_data
    dict's inaccessible/unclassified/profiling-failure fields.

    Distinct from EgeriaFileSystemSurveyor.publish_step_annotations' RFAs:
    those only get created when a survey is explicitly published to Egeria.
    This is RE's own local activity log (`registry.write_activity`/`GET
    /api/activity/rfas`, the 📝 RFAs tab) — it works for a purely local
    survey with no Egeria involvement at all, so problems found during a
    quick local walk are still visible somewhere without requiring a publish
    step first."""
    annotations: list[dict] = []

    inaccessible_count = survey_data.get("inaccessible_file_count", 0)
    if inaccessible_count:
        annotations.append({
            "analysis_name": "FileSystemWalk",
            "annotation_type": "RequestForAction",
            "count": inaccessible_count,
            "status": "local",
            "summary": f"{inaccessible_count} file(s)/director(y/ies) could not be accessed during the scan",
        })

    profiling_errors = survey_data.get("profiling_errors") or []
    if profiling_errors:
        annotations.append({
            "analysis_name": "DataProfiling",
            "annotation_type": "RequestForAction",
            "count": len(profiling_errors),
            "status": "local",
            "summary": f"{len(profiling_errors)} data file(s) could not be schema-profiled",
        })

    unclassified_count = (survey_data.get("classification") or {}).get("unclassified_count", 0)
    if unclassified_count:
        annotations.append({
            "analysis_name": "FileSystemClassification",
            "annotation_type": "RequestForAction",
            "count": unclassified_count,
            "status": "local",
            "summary": f"{unclassified_count} file(s) could not be classified against known file reference data",
        })

    return annotations

# Skip walking these common noise directories
IGNORE_DIRS = {
    ".git",
    ".github",
    ".venv",
    "venv",  # bare "venv" (not just ".venv") — the previous omission meant every
             # stray Python venv under a broad root got fully recursed into,
             # which is what made an ordinary scan take minutes and flood the
             # console with pandas/openpyxl noise from tzdata/pytz zoneinfo files.
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".idea",
    ".gemini",
    "dist",
    "build",
}

#: The walk's declared limits (Files step 2, caution 3). Shown on the report, and a walk that reaches one says it was
#: capped: a capped walk never reads as complete. `max_entries` counts files AND directories examined.
DEFAULT_MAX_DEPTH = 20
DEFAULT_MAX_ENTRIES = 100_000


class LocalFileSystemSurveyor:
    """Walks a local filesystem directory, scans files, and profiles data files.

    Internally split into two phases — a metadata-only pass (one `stat()` per
    file — cheap, matches what Egeria's own native FileDirectory:CreateAndSurvey
    process captures) and a content-reading pass (pandas-based schema
    profiling — expensive, and the actual source of the multi-minute scans and
    swallowed errors reported 2026-07-13). `run()` still does both in one call
    for backward compatibility with the pre-Survey-Definition `/survey` route
    and its UI (`renderFilesystemSurveyReport`), which expect one flat dict.
    See docs/filesystem-survey-analytics-plan.md for the full design record —
    note the phases are kept as one Survey Definition step, not two: Egeria's
    own native survey doesn't decompose this either, and once you're already
    asking the OS for one file's stat() info there's no benefit to walking the
    tree twice to get the rest of it.
    """

    def __init__(self, filesystem: FileSystemEntity, registry: ProjectRegistry, *,
                 max_depth: int = DEFAULT_MAX_DEPTH, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self.filesystem = filesystem
        self.registry = registry
        self.root_path = Path(filesystem.local_mount_point)
        self.max_depth = max_depth
        self.max_entries = max_entries

    def run(self, *, profile_contents: bool = True, walk_extensions: bool = False) -> dict:
        """Scan the filesystem path, profile data files, and return the survey data.

        `profile_contents=False` is the folder survey's walk (`filesystem_inventory`): names, sizes and times only, and
        no file is opened (Files step 2, caution 1). The default keeps Classic's modal, which still shows per-file
        profiles until `file_profiles` is its own step.

        `walk_extensions=True` also builds one per-file row (owner, group, mode, folder depth and a content hash under the
        size limit; `walk_extensions.build_row`) and returns the rows under `survey_data["walk_files"]`. The CALLER stores
        them and removes the key before the blob is saved; the blob never carries them. This is the one place the walk
        opens a file, and only through `walk_extensions.hash_file`'s guards (plain file, under the limit, no secret-looking
        name)."""
        survey_data = self._scan_structure(walk_extensions=walk_extensions)
        if profile_contents:
            self._profile_data_files(survey_data)
        return survey_data

    def _scan_structure(self, *, walk_extensions: bool = False) -> dict:
        """Phase 1: one walk, one `stat()` per file. Collects counts, OS-level
        file attributes (hidden/symlink/executable/writable, timestamps),
        Egeria-reference-data classification, and inaccessible-file tracking —
        comparable in content to Egeria's native "Capture File Counts" /
        "Profile Deployed Implementation Types" / "Profile Asset Types" /
        "Profile File Types" / "Profile File Extensions" / "Missing File
        Reference Data" / "Inaccessible files" annotations. No file content is
        read in this phase."""
        if not self.root_path.exists() or not self.root_path.is_dir():
            raise FileNotFoundError(
                f"Local mount point directory not found or is not a directory: {self.root_path}"
            )

        log.info(f"Starting local filesystem survey for {self.filesystem.slug} on {self.root_path}")

        inventory: list[dict] = []
        total_files = 0
        total_data_files = 0
        total_size_bytes = 0
        formats_summary: dict[str, dict[str, Any]] = {}
        hidden_count = symlink_count = executable_count = writable_count = readable_count = 0
        extensions: set[str] = set()
        filenames: set[str] = set()
        last_access_time = last_creation_time = last_modification_time = ""
        inaccessible_files: list[dict] = []
        walk_rows: list[dict] = []
        hash_limit = we.hash_limit_bytes() if walk_extensions else 0
        hash_budget = we.HashBudget() if walk_extensions else None

        # The walk's own wall time (stat and hashing), kept with its limits: F12's price line reads it as this machine's
        # measured rate. Not a price for a different machine.
        walk_started = time.monotonic()
        walk = self._walk(inaccessible_files)
        broken_links = walk["broken_symlinks"]
        for p in walk["files"]:
            total_files += 1
            rel_path = str(p.relative_to(self.root_path))
            try:
                stat = p.stat()
                size_bytes = stat.st_size
            except Exception as exc:
                inaccessible_files.append({"path": rel_path, "error": str(exc)})
                continue
            total_size_bytes += size_bytes

            is_hidden = p.name.startswith(".")
            try:
                is_symlink = p.is_symlink()
                link_unknown = False
            except OSError:
                # cannot tell: the counts stay as they were, but the per-file row treats it as a link (never opened)
                is_symlink, link_unknown = False, True
            is_executable = os.access(p, os.X_OK)
            is_writable = os.access(p, os.W_OK)
            if os.access(p, os.R_OK):
                readable_count += 1
            if is_hidden:
                hidden_count += 1
            if is_symlink:
                symlink_count += 1
            if is_executable:
                executable_count += 1
            if is_writable:
                writable_count += 1

            # st_ctime is inode-change time on Unix, not true creation time —
            # no portable birth-time API exists across platforms, so this is
            # the same best-effort approximation Egeria's own annotation
            # wording ("Last file creation time") leaves ambiguous too.
            access_iso = datetime.utcfromtimestamp(stat.st_atime).isoformat()
            modify_iso = datetime.utcfromtimestamp(stat.st_mtime).isoformat()
            create_iso = datetime.utcfromtimestamp(stat.st_ctime).isoformat()
            last_access_time = max(last_access_time, access_iso)
            last_modification_time = max(last_modification_time, modify_iso)
            last_creation_time = max(last_creation_time, create_iso)

            ext = p.suffix.lstrip(".").lower()
            extensions.add(ext or "(none)")
            filenames.add(p.name)

            if walk_extensions:
                walk_rows.append(we.build_row(rel_path, p, stat, is_symlink=is_symlink or link_unknown, limit=hash_limit,
                                              modified_at=modify_iso, budget=hash_budget, accessed_at=access_iso))

            file_info = {
                "file_path": rel_path,
                "file_name": p.name,
                "file_size_bytes": size_bytes,
                "file_size": _fmt_size(size_bytes),
                "is_data_file": False,
                "format": "Unknown",
                "is_hidden": is_hidden,
                "is_symlink": is_symlink,
                "is_executable": is_executable,
                "is_writable": is_writable,
                "modified_at": modify_iso,
            }

            if ext in _DATA_EXTENSIONS:
                total_data_files += 1
                fmt = _DATA_EXTENSIONS[ext]
                file_info["is_data_file"] = True
                file_info["format"] = fmt

                if fmt not in formats_summary:
                    formats_summary[fmt] = {"count": 0, "total_size_bytes": 0}
                formats_summary[fmt]["count"] += 1
                formats_summary[fmt]["total_size_bytes"] += size_bytes

            inventory.append(file_info)

        paths = [f["file_path"] for f in inventory]
        classification = classify_file_paths(paths)
        three_way = classify_for_folder_survey(paths)

        survey_data = {
            "surveyed_at": datetime.utcnow().isoformat(),
            "total_files": total_files,
            "total_data_files": total_data_files,
            "total_size_bytes": total_size_bytes,
            "total_size": _fmt_size(total_size_bytes),
            "formats": {
                fmt: {
                    "count": val["count"],
                    "total_size": _fmt_size(val["total_size_bytes"]),
                    "total_size_bytes": val["total_size_bytes"],
                }
                for fmt, val in formats_summary.items()
            },
            "inventory": inventory,
            # Egeria "Capture File Counts" parity:
            "hidden_count": hidden_count,
            "symlink_count": symlink_count,
            "executable_count": executable_count,
            "writable_count": writable_count,
            "unique_extension_count": len(extensions),
            "unique_filename_count": len(filenames),
            "last_access_time": last_access_time,
            "last_creation_time": last_creation_time,
            "last_modification_time": last_modification_time,
            "inaccessible_file_count": len(inaccessible_files),
            "inaccessible_files": inaccessible_files[:100],
            # Links whose target is gone: counted and listed (registry only), never silently dropped.
            "broken_symlink_count": len(broken_links),
            "broken_symlinks": broken_links[:100],
            "readable_count": readable_count,
            "total_directories": walk["directories"],
            # Declared and shown (Files step 2, caution 3): what the walk was allowed to do and what it did. A walk
            # that stopped at a limit says so; it never reads as the whole tree.
            "walk_limits": {
                "max_depth": self.max_depth,
                "max_entries": self.max_entries,
                "capped": walk["capped"],
                "capped_reasons": walk["capped_reasons"],
                "deepest_level_reached": walk["deepest"],
                "not_walked_directory_names": sorted(IGNORE_DIRS),
                "not_walked_directories": walk["skipped_ignored"],
                # Which of the noise names were actually met and skipped, so the report can say "12 directories not
                # walked: .git, node_modules, .venv" and never skip silently.
                "not_walked_names_found": sorted(walk["skipped_names"]),
                # Per noise name, how many directories were not entered ({".git": 3, "node_modules": 12}): counted and
                # named, never silent.
                "not_walked_by_name": dict(sorted(walk["skipped_by_name"].items())),
                "broken_symlink_count": len(broken_links),
                "symlinked_directories_not_followed": walk["skipped_symlinks"],
                "entries_examined": walk["entries"],
            },
            # Egeria "Profile Deployed Implementation Types/Asset Types/File
            # Types/File Extensions" + "Missing File Reference Data" parity:
            "classification": {
                "type_counts": {label: len(paths) for label, paths in classification.type_groups.items()},
                "extension_counts": dict(classification.ext_counter),
                "unclassified_count": three_way["unclassified_count"],
                "unclassified_sample": three_way["unclassified_sample"],
                "source": classification.source,
                # Egeria's three profiles (Profile File Types / Asset Types / Deployed Implementation Types) and the
                # unclassified rule of FolderSurveyService (any of the three missing), from the same reference data.
                "file_type_counts": three_way["file_type_counts"],
                "asset_type_counts": three_way["asset_type_counts"],
                "deployed_type_counts": three_way["deployed_type_counts"],
            },
            # Populated by _profile_data_files(); present here so callers that
            # only run the structure phase still get a consistent shape.
            "profiling_errors": [],
        }

        # "capped at 100,000 entries": said on every result, so a capped walk never reads as the whole tree.
        survey_data["walk_limits"]["capped_words"] = we.capped_words(survey_data["walk_limits"])
        survey_data["walk_limits"]["walk_seconds"] = round(time.monotonic() - walk_started, 3)
        survey_data["walk_limits"]["machine"] = socket.gethostname()
        if walk_extensions:
            survey_data["walk_files"] = walk_rows
            survey_data["walk_hash_limit_bytes"] = hash_limit
            survey_data["walk_hash_budget"] = hash_budget.as_dict()
        log.info(
            f"Filesystem structure scan complete for {self.filesystem.slug}: {total_files} files "
            f"({total_data_files} data files, {len(inaccessible_files)} inaccessible), "
            f"total size: {survey_data['total_size']}"
        )
        return survey_data

    def _profile_data_files(self, survey_data: dict) -> None:
        """Phase 2: read and parse each data file's contents to profile schema
        (rows/columns/dtypes/nulls). Mutates survey_data in place — adds
        row_count/col_count/columns/null_summary to each profiled inventory
        entry, and collects per-file failures into survey_data["profiling_errors"]
        instead of only logging them (the previous behavior: profiling
        failures were `log.warning`'d and otherwise silently dropped, which is
        why a run that hit dozens of malformed CSVs/legacy .xls files looked
        like a hang rather than a completed run with warnings)."""
        pd: Any = None
        try:
            import pandas as _pd
            pd = _pd
        except ImportError:
            log.warning("pandas not installed — file schema profiling will be skipped (size and extension only)")
            return

        profiling_errors: list[dict] = []
        for file_info in survey_data["inventory"]:
            if not file_info.get("is_data_file"):
                continue
            ext = Path(file_info["file_name"]).suffix.lstrip(".").lower()
            if ext not in _PANDAS_READABLE:
                continue
            size_bytes = file_info["file_size_bytes"]
            limit_bytes = 50 * 1024 * 1024  # 50MB
            if ext not in _SCHEMA_ONLY and size_bytes > limit_bytes:
                continue

            p = self.root_path / file_info["file_path"]
            try:
                profile = DataProfilerSurveyor._profile_file(p, ext, pd)
                if profile:
                    file_info.update({
                        "row_count": profile.get("row_count"),
                        "col_count": profile.get("col_count"),
                        "columns": profile.get("columns", []),
                        "null_summary": profile.get("null_summary", ""),
                    })
            except Exception as exc:
                log.debug(f"Could not profile schema for {file_info['file_path']}: {exc}")
                profiling_errors.append({"path": file_info["file_path"], "error": str(exc)})

        survey_data["profiling_errors"] = profiling_errors
        if profiling_errors:
            log.info(
                f"Filesystem data profiling for {self.filesystem.slug} finished with "
                f"{len(profiling_errors)} file(s) that could not be profiled — see "
                "survey_data['profiling_errors'] for the full list."
            )

    def _walk(self, inaccessible_files: list[dict]) -> dict:
        """Walk the root, returning the files and what the walk did and did not cover.

        Names only: it lists directories and stats entries; no file is opened. Noise directories (`IGNORE_DIRS`) and
        symlinked directories are not entered, and both are COUNTED so the report can say so. The walk stops at
        `max_depth` and at `max_entries` (files and directories examined) and records which limit stopped it; a walk
        that stopped is `capped`. Permission and OS errors are recorded as inaccessible, relative to the root.
        """
        files: list[Path] = []
        broken_symlinks: list[str] = []
        skipped_names: set[str] = set()
        skipped_by_name: Counter = Counter()
        directories = entries = skipped_ignored = skipped_symlinks = deepest = 0
        capped_reasons: list[str] = []
        stack: list[tuple[Path, int]] = [(self.root_path, 0)]
        stopped = False
        while stack and not stopped:
            current, depth = stack.pop()
            deepest = max(deepest, depth)
            try:
                children = sorted(os.scandir(current), key=lambda e: e.name)
            except Exception as exc:  # noqa: BLE001 -- recorded as inaccessible, the walk goes on
                self._record_inaccessible_dir(current, exc, inaccessible_files)
                continue
            for entry in children:
                if entries >= self.max_entries:
                    if "entry cap" not in capped_reasons:
                        capped_reasons.append("entry cap")
                    stopped = True
                    break
                try:
                    is_symlink = entry.is_symlink()
                    is_dir = entry.is_dir(follow_symlinks=False)
                    is_file = entry.is_file()
                except OSError as exc:
                    entries += 1
                    inaccessible_files.append({"path": self._rel(Path(entry.path)), "error": str(exc)})
                    continue
                entries += 1
                if is_dir:
                    if entry.name in IGNORE_DIRS:
                        skipped_ignored += 1
                        skipped_names.add(entry.name)
                        skipped_by_name[entry.name] += 1
                        continue
                    directories += 1
                    if depth + 1 > self.max_depth:
                        if "depth cap" not in capped_reasons:
                            capped_reasons.append("depth cap")
                        continue
                    stack.append((Path(entry.path), depth + 1))
                elif is_symlink and entry.is_dir():
                    # A link to a folder is counted and never followed: it can loop, or lead outside the root.
                    skipped_symlinks += 1
                elif is_file:
                    files.append(Path(entry.path))
                elif is_symlink:
                    # neither a file nor a folder behind it: a broken link
                    broken_symlinks.append(self._rel(Path(entry.path)))
        return {"files": files, "broken_symlinks": broken_symlinks, "skipped_names": skipped_names, "skipped_by_name": skipped_by_name, "directories": directories, "entries": entries, "deepest": deepest,
                "skipped_ignored": skipped_ignored, "skipped_symlinks": skipped_symlinks,
                "capped": bool(capped_reasons), "capped_reasons": capped_reasons}

    def _rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root_path))
        except ValueError:
            return str(path)

    def _record_inaccessible_dir(self, root: Path, exc: Exception, inaccessible_files: list[dict]) -> None:
        log.warning(f"Error walking directory {root}: {exc}")
        try:
            rel = str(root.relative_to(self.root_path))
        except ValueError:
            rel = str(root)
        inaccessible_files.append({"path": rel, "error": str(exc)})
