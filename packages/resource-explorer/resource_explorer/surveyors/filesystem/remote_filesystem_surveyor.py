"""Resource Explorer's walk of a remote file system through a reach mechanism (outcome 3, S3 slice 2026-11-02).

The same walk as `LocalFileSystemSurveyor`, with the mechanism's `list/stat/read` in place of `os.scandir/stat/open`,
and the same `survey_data` shape, so the folder-survey card, the derived steps and the publish read it unchanged.

What an object store does not carry is said, never zeroed: owner, group and POSIX mode are "not reported by this
source" on every kept row; readable/writable/executable/link counts and access and creation times are left out of
the result (None), which the publish omits. A single-part object's etag is its hash (the source computed it; no byte
is read); a multipart etag is not a hash of the content and is kept as "not hashed · multipart etag".

Cost: one list call per 1,000 keys per prefix, counted (`walk_limits.list_calls`), with the local walk's depth and
entry caps. The only bytes read are the narrative files' capped reads.
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import datetime

from resource_explorer.reach_mechanisms import NOT_REPORTED, ReachError, ReachMechanism
from resource_explorer.surveyors.file_classifier.file_classifier_surveyor import classify_file_paths
from resource_explorer.surveyors.filesystem import walk_extensions as we
from resource_explorer.surveyors.filesystem.folder_survey_annotations import classify_for_folder_survey
from resource_explorer.surveyors.filesystem.local_filesystem_surveyor import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_ENTRIES,
    IGNORE_DIRS,
)
from resource_explorer.surveyors.sub_surveyors.data_profiler import _DATA_EXTENSIONS, _fmt_size

log = logging.getLogger(__name__)

#: The fields an object store does not report, said on the result.
NOT_REPORTED_FIELDS = ("owner", "group", "mode", "readable", "writable", "executable", "symlink", "access time",
                       "creation time")


def remote_row(entry, *, modified_at: str) -> dict:
    """One kept per-file row for a remote object. No byte is read: the hash is the source's own single-part etag."""
    rel = entry.key
    folder, _, name = rel.rpartition("/")
    if we.looks_secret(name):
        digest, state = "", we.SECRET_NAME
    elif not entry.etag or entry.multipart:
        digest, state = "", we.MULTIPART_ETAG if entry.multipart else we.UNREADABLE
    else:
        digest, state = f"etag:{entry.etag}", we.HASHED
    return {
        "file_path": rel,
        "folder_path": folder,
        "folder_depth": rel.count("/"),
        "size_bytes": entry.size,
        "modified_at": modified_at,
        "accessed_at": None,
        "owner_name": NOT_REPORTED,
        "group_name": NOT_REPORTED,
        "mode_octal": NOT_REPORTED,
        "world_readable": None,
        "content_hash": digest,
        "hash_state": state,
        "is_symlink": False,
    }


class RemoteFileSystemSurveyor:
    def __init__(self, filesystem, registry, mechanism: ReachMechanism, *, max_depth: int = DEFAULT_MAX_DEPTH,
                 max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self.filesystem = filesystem
        self.registry = registry
        self.mechanism = mechanism
        self.max_depth = max_depth
        self.max_entries = max_entries

    def reader(self, rel_path: str, max_bytes: int) -> bytes | None:
        """The narrative step's capped read, by relative path; None when the source refused it."""
        try:
            return self.mechanism.read(rel_path, max_bytes)
        except ReachError as exc:
            log.info("%s: %s not read: %s", self.filesystem.slug, rel_path, exc)
            return None

    def run(self, *, walk_extensions: bool = True) -> dict:
        started = time.monotonic()
        inaccessible: list[dict] = []
        files, walk = self._walk(inaccessible)
        inventory: list[dict] = []
        rows: list[dict] = []
        formats: dict[str, dict] = {}
        extensions: set[str] = set()
        names: set[str] = set()
        total_size = data_files = hidden = 0
        last_modified = ""
        for e in files:
            name = e.key.rpartition("/")[2]
            ext = name.rpartition(".")[2].lower() if "." in name else ""
            modified = e.modified_at
            last_modified = max(last_modified, modified)
            total_size += e.size
            hidden += 1 if name.startswith(".") else 0
            extensions.add(ext or "(none)")
            names.add(name)
            info = {"file_path": e.key, "file_name": name, "file_size_bytes": e.size, "file_size": _fmt_size(e.size),
                    "is_data_file": False, "format": "Unknown", "is_hidden": name.startswith("."),
                    "is_symlink": False, "is_executable": None, "is_writable": None, "modified_at": modified}
            if ext in _DATA_EXTENSIONS:
                data_files += 1
                fmt = _DATA_EXTENSIONS[ext]
                info.update(is_data_file=True, format=fmt)
                f = formats.setdefault(fmt, {"count": 0, "total_size_bytes": 0})
                f["count"] += 1
                f["total_size_bytes"] += e.size
            inventory.append(info)
            if walk_extensions:
                rows.append(remote_row(e, modified_at=modified))

        paths = [f["file_path"] for f in inventory]
        classification = classify_file_paths(paths)
        three_way = classify_for_folder_survey(paths)
        limits = {
            "max_depth": self.max_depth, "max_entries": self.max_entries, "capped": bool(walk["capped_reasons"]),
            "capped_reasons": walk["capped_reasons"], "deepest_level_reached": walk["deepest"],
            "not_walked_directory_names": sorted(IGNORE_DIRS), "not_walked_directories": walk["skipped_ignored"],
            "not_walked_names_found": sorted(walk["skipped_by_name"]),
            "not_walked_by_name": dict(sorted(walk["skipped_by_name"].items())),
            "broken_symlink_count": None, "symlinked_directories_not_followed": None,
            "entries_examined": walk["entries"],
            # The price line's measured half: round trips, one per 1,000 keys per prefix.
            "list_calls": walk["calls"], "protocol": getattr(self.filesystem, "protocol", ""),
        }
        limits["capped_words"] = we.capped_words(limits)
        limits["walk_seconds"] = round(time.monotonic() - started, 3)
        from resource_explorer.reach_mechanisms.s3 import re_host

        limits["machine"] = re_host()
        sd = {
            "surveyed_at": datetime.utcnow().isoformat(),
            "total_files": len(inventory), "total_data_files": data_files, "total_size_bytes": total_size,
            "total_size": _fmt_size(total_size),
            "formats": {k: {"count": v["count"], "total_size": _fmt_size(v["total_size_bytes"]),
                            "total_size_bytes": v["total_size_bytes"]} for k, v in formats.items()},
            "inventory": inventory,
            "hidden_count": hidden,
            # Not reported by an object store: None, which the publish omits (never a zero).
            "symlink_count": None, "executable_count": None, "writable_count": None, "readable_count": None,
            "unique_extension_count": len(extensions), "unique_filename_count": len(names),
            "last_access_time": "", "last_creation_time": "", "last_modification_time": last_modified,
            "inaccessible_file_count": len(inaccessible), "inaccessible_files": inaccessible[:100],
            "broken_symlink_count": None, "broken_symlinks": [],
            "total_directories": walk["directories"],
            "walk_limits": limits,
            "not_reported": {"fields": list(NOT_REPORTED_FIELDS), "words": NOT_REPORTED},
            "classification": {
                "type_counts": {label: len(p) for label, p in classification.type_groups.items()},
                "extension_counts": dict(classification.ext_counter),
                "unclassified_count": three_way["unclassified_count"],
                "unclassified_sample": three_way["unclassified_sample"],
                "source": classification.source,
                "file_type_counts": three_way["file_type_counts"],
                "asset_type_counts": three_way["asset_type_counts"],
                "deployed_type_counts": three_way["deployed_type_counts"],
            },
            "profiling_errors": [],
        }
        if walk_extensions:
            sd["walk_files"] = rows
            sd["walk_hash_limit_bytes"] = we.hash_limit_bytes()
            sd["walk_hash_algorithm"] = we.ETAG_ALGORITHM
        return sd

    def _walk(self, inaccessible: list[dict]) -> tuple[list, dict]:
        """Prefix by prefix, depth first, under the depth and entry caps. A prefix the source refuses is recorded as
        inaccessible and the walk goes on; a refusal of the ROOT is the whole answer (raised)."""
        files: list = []
        skipped_by_name: Counter = Counter()
        directories = entries = skipped_ignored = deepest = calls = 0
        capped: list[str] = []
        stack: list[tuple[str, int]] = [("", 0)]
        while stack:
            prefix, depth = stack.pop()
            deepest = max(deepest, depth)
            remaining = self.max_entries - entries
            if remaining <= 0:
                if "entry cap" not in capped:
                    capped.append("entry cap")
                break
            try:
                listing = self.mechanism.list(prefix, limit=remaining)
            except ReachError as exc:
                if not prefix:
                    raise
                inaccessible.append({"path": prefix.rstrip("/"), "error": str(exc)})
                continue
            calls += listing.calls
            children = sorted(listing.folders, reverse=True)
            for e in sorted(listing.entries, key=lambda x: x.key):
                if entries >= self.max_entries:
                    break
                entries += 1
                files.append(e)
            for folder in children:
                if entries >= self.max_entries:
                    break
                entries += 1
                name = folder.rstrip("/").rpartition("/")[2]
                if name in IGNORE_DIRS:
                    skipped_ignored += 1
                    skipped_by_name[name] += 1
                    continue
                directories += 1
                if depth + 1 > self.max_depth:
                    if "depth cap" not in capped:
                        capped.append("depth cap")
                    continue
                stack.append((folder, depth + 1))
            if listing.truncated or entries >= self.max_entries:
                if "entry cap" not in capped:
                    capped.append("entry cap")
                break
        return files, {"directories": directories, "entries": entries, "deepest": deepest, "calls": calls,
                       "skipped_ignored": skipped_ignored, "skipped_by_name": skipped_by_name, "capped_reasons": capped}
