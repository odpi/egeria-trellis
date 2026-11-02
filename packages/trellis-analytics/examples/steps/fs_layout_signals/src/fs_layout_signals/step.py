"""The code: `run(inputs) -> results`. It owns the measurement and its reasons, and nothing else.

From the newest walk run whose per-file rows RE kept: the NAMES of files and folders only, matched against fixed
patterns. No file is opened. One result for the whole file system, as COUNTS: no path, file or folder name is in it,
because a result can be published and those stay in Resource Explorer.

Five structures, each recognised from its own signals (`RULES` below, said in the result's basis):

- **a project**: a project manifest (pyproject.toml, package.json, pom.xml, go.mod ...), or a `.git` directory the
  walk did not enter (counted by name on the walk's own record);
- **a dataset layout**: two or more dated folders (`2024`, `2024-10`, `20241028`) or partition folders (`year=2024`),
  or a data dictionary, codebook, schema or layout file;
- **an archive**: archive files (.zip, .tar, .gz, .7z ...);
- **backups**: backup-named files (.bak, .old, .orig, `name~`, database dumps) or files under a backup-named folder;
- **a Delta Lake table**: a folder holding a `_delta_log/` folder with at least one JSON commit file. Its Parquet parts
  are counted as found: the transaction log is not read, so parts a later commit retired are counted too.

Never a silent zero:
- no walk: ``not_established`` (a walk that kept no rows is not a run the reader can see, so it reads the same);
- a walk with files and no signal: ``nothing_found``, naming every signal looked for; a capped walk says the counts
  are of the walked part only;
- a walk that did not record the directories it skipped: the git count is "not recorded", never 0.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

BASIS = ("the names on the walk's per-file rows matched against fixed patterns (project manifests, dated and "
         "partition folders, dictionary and schema names, archive and backup suffixes and folders, Delta Lake "
         "_delta_log folders) and the walk's own "
         "count of the .git directories it did not enter; no file is opened")

PROJECT_MANIFESTS = frozenset({
    "pyproject.toml", "setup.py", "setup.cfg", "package.json", "pom.xml", "build.gradle", "build.gradle.kts",
    "settings.gradle", "cargo.toml", "go.mod", "gemfile", "composer.json", "cmakelists.txt", "build.sbt", "mix.exs",
    "pubspec.yaml", "project.clj", "deno.json"})
PROJECT_SUFFIXES = (".csproj", ".fsproj", ".sln")
#: A folder name that is a date: a year, a year and month, or a full date, with or without separators.
DATED = re.compile(r"^(19|20)\d{2}([-_.]?(0[1-9]|1[0-2])([-_.]?(0[1-9]|[12]\d|3[01]))?)?$")
#: A Hive-style partition folder: `key=value`.
PARTITION = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=[^=]+$")
DICTIONARY_WORDS = ("dictionary", "codebook", "schema", "layout")
DICTIONARY_SUFFIXES = (".avsc", ".xsd")
#: Code files whose names happen to hold a dictionary word ("schema.py") are code, not a dataset's dictionary.
CODE_SUFFIXES = (".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".rb", ".cs", ".kt", ".scala", ".php",
                 ".c", ".h", ".cpp", ".mjs", ".cjs")
ARCHIVE_SUFFIXES = (".zip", ".tar", ".tgz", ".tbz2", ".gz", ".bz2", ".xz", ".7z", ".rar", ".zst")
BACKUP_SUFFIXES = (".bak", ".backup", ".bk", ".old", ".orig", ".dump")
BACKUP_FOLDERS = frozenset({"backup", "backups", "bak", "bkp", "snapshot", "snapshots"})
#: The folder a Delta Lake table keeps its transaction log in; a table is the folder that holds it.
DELTA_LOG = "_delta_log"
#: What makes each structure recognised, said in the result.
RULES = ("project: a project manifest or a .git directory; dataset layout: two or more dated or partition folders, "
         "or a dictionary, codebook, schema or layout file; archive: an archive file; backups: a backup-named file or "
         "a file under a backup-named folder; Delta Lake table: a _delta_log folder holding a JSON commit file "
         "(its Parquet parts counted as found, the log not read)")


def _absent(scope: str, reason: str, words: str) -> dict:
    return {"scope": scope, "state": "not_established", "reason": reason, "summary": f"not measured · {words}"}


def _bytes(n: int) -> str:
    size = float(n)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size):,} bytes" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n:,} bytes"


def _plural(n: int, one: str, many: str = "") -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def _is_dictionary(name: str) -> bool:
    low = name.lower()
    if low.endswith(CODE_SUFFIXES):
        return False
    return low.endswith(DICTIONARY_SUFFIXES) or any(w in low for w in DICTIONARY_WORDS)


def _is_backup_name(name: str) -> bool:
    low = name.lower()
    return low.endswith(BACKUP_SUFFIXES) or low.endswith("~") or low.endswith(".sql.gz") or "backup" in low


def signals(rows: list[dict]) -> dict:
    """The counts, from the rows' names. Pure."""
    manifests = dictionaries = archives = archive_bytes = backups = backup_bytes = 0
    manifest_folders: set[str] = set()
    readme_folders: set[str] = set()
    dated: set[str] = set()
    partitions: set[str] = set()
    backup_folders: set[str] = set()
    delta_tables: set[str] = set()
    parquet_by_folder: dict[str, int] = {}
    for r in rows:
        path = PurePosixPath(str(r.get("file_path") or ""))
        name = path.name
        low = name.lower()
        folder = str(path.parent)
        size = int(r.get("size_bytes") or 0)
        parts = path.parent.parts
        for i, part in enumerate(parts):
            prefix = "/".join(parts[: i + 1])
            if DATED.match(part):
                dated.add(prefix)
            elif PARTITION.match(part):
                partitions.add(prefix)
            if part.lower() in BACKUP_FOLDERS:
                backup_folders.add(prefix)
        if low in PROJECT_MANIFESTS or low.endswith(PROJECT_SUFFIXES):
            manifests += 1
            manifest_folders.add(folder)
        if low.startswith("readme"):
            readme_folders.add(folder)
        if _is_dictionary(name):
            dictionaries += 1
        if low.endswith(ARCHIVE_SUFFIXES) and not low.endswith(".sql.gz"):
            archives += 1
            archive_bytes += size
        if _is_backup_name(name) or any(p.lower() in BACKUP_FOLDERS for p in parts):
            backups += 1
            backup_bytes += size
        if parts and parts[-1] == DELTA_LOG and low.endswith(".json"):
            delta_tables.add(str(path.parent.parent))
        if low.endswith(".parquet"):
            parquet_by_folder[folder] = parquet_by_folder.get(folder, 0) + 1
    # A part is a table's when it sits in the table's folder or under it (partition folders).
    delta_parts = sum(n for f, n in parquet_by_folder.items()
                      if any(f == t or t == "." or f.startswith(t + "/") for t in delta_tables))
    return {"Project Manifests": manifests, "Folders With A Project Manifest": len(manifest_folders),
            "Folders With A README": len(readme_folders), "Dated Folders": len(dated),
            "Partition Folders": len(partitions), "Data Dictionary Or Schema Files": dictionaries,
            "Archive Files": archives, "Archive Bytes": archive_bytes, "Backup Files": backups,
            "Backup Bytes": backup_bytes, "Backup Folders": len(backup_folders),
            "Delta Lake Tables": len(delta_tables), "Delta Lake Parquet Parts": delta_parts}


def recognised(counts: dict, git: int | None) -> list[str]:
    """The structures the counts recognise, each with the words that say why, in a fixed order."""
    out = []
    if counts["Project Manifests"] or git:
        why = [_plural(counts["Project Manifests"], "project manifest")] if counts["Project Manifests"] else []
        if git:
            why.append(_plural(git, ".git directory", ".git directories") + " not walked")
        out.append(f"a project ({', '.join(why)})")
    layout = counts["Dated Folders"] + counts["Partition Folders"]
    if layout >= 2 or counts["Data Dictionary Or Schema Files"]:
        why = []
        if counts["Dated Folders"]:
            why.append(_plural(counts["Dated Folders"], "dated folder"))
        if counts["Partition Folders"]:
            why.append(_plural(counts["Partition Folders"], "partition folder"))
        if counts["Data Dictionary Or Schema Files"]:
            why.append(_plural(counts["Data Dictionary Or Schema Files"], "dictionary or schema file"))
        out.append(f"a dataset layout ({', '.join(why)})")
    if counts["Archive Files"]:
        out.append(f"an archive ({_plural(counts['Archive Files'], 'archive file')}, {_bytes(counts['Archive Bytes'])})")
    if counts["Backup Files"]:
        out.append(f"backups ({_plural(counts['Backup Files'], 'file')}, {_bytes(counts['Backup Bytes'])})")
    if counts.get("Delta Lake Tables"):
        out.append(f"a Delta Lake table ({_plural(counts['Delta Lake Tables'], 'table')}, "
                   f"{_plural(counts['Delta Lake Parquet Parts'], 'Parquet part')} as found · the Delta log was not "
                   "read, so retired parts may be included)")
    return out


def run(inputs) -> list[dict]:
    slug = inputs.slug
    runs = inputs.ods.filesystem_walk_runs(slug, limit=1)
    if not runs:
        return [_absent(slug, "no walk has kept per-file rows for this file system yet", "no walk yet")]
    walk = runs[0]
    rows = inputs.ods.filesystem_walk_files(slug, walk["run_id"])
    if not rows:
        return [{"scope": slug, "state": "nothing_found",
                 "value": {"asOf": walk["run_id"], "Files Walked": 0, "basis": BASIS},
                 "summary": "the walk found no files"}]
    not_walked = inputs.ods.filesystem_walk_not_walked(slug, walk["run_id"])
    git = None if not_walked is None else int(not_walked.get(".git", 0))
    counts = signals(rows)
    kinds = recognised(counts, git)
    capped = walk.get("capped")
    value = {"asOf": walk["run_id"], "Files Walked": len(rows), **counts,
             "Recognised Structures": len(kinds),
             "Recognised As": "; ".join(k.split(" (")[0] for k in kinds),
             "Walk Capped": {1: "yes", 0: "no"}.get(capped, "not recorded"),
             "rules": RULES, "basis": BASIS}
    if git is None:
        value["Git Directories Not Walked"] = "not recorded on this walk"
    else:
        value["Git Directories Not Walked"] = git
    caveat = "; the walk was capped, so this is the walked part only" if capped == 1 else ""
    if git is None:
        caveat += "; .git directories not recorded on this walk"
    if not kinds:
        return [{"scope": slug, "state": "nothing_found", "value": value,
                 "summary": ("no recognisable structure: no project manifest or .git directory, no dated or partition "
                             "folders or dictionary file, no archive or backup file, no Delta Lake _delta_log folder"
                             + caveat)}]
    return [{"scope": slug, "state": "measured", "value": value,
             "summary": "recognised: " + "; ".join(kinds) + caveat}]
