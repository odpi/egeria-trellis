"""The walk's extensions (Files build step 1): what Egeria's Folder Survey does not keep.

Egeria's Folder Survey counts the whole tree and classifies every file. It does not keep, per file, who owns it, its mode,
a content hash, or how deep and how heavy each folder is. This module is the part of Resource Explorer's own walk that
measures exactly that, and nothing Egeria lists as its own (design: BRIEF-FILES-QUESTIONS-AND-ANALYSES-TO-RETIRE-CLASSIC.md
section 1a):

* **per-file rows**, one per file per run (`build_row`), kept in the registry (`filesystem_walk_files`): path, folder, depth,
  size, modified time, owner, group, mode, a content hash under a size limit, and why a file has no hash;
* **folder measures** (`folder_measures`): bytes and files per depth, the ten heaviest folders, the deepest level;
* **summaries that may leave for Egeria** (`summaries`): counts only. No path, file name, owner name or hash goes into any
  summary a publish reads; those stay in the registry (same rule as the counts-only folder publish);
* **`folder_narratives`** (`folder_narratives`): the README-type files of each folder, read once each under a size cap, as
  PROPOSALS with the file and its modified time as evidence. Nothing here is a fact.

Three guards, each with a test:

* a file is hashed only if it is a plain file (not a link), under the limit, and its NAME does not look like a secret
  (`.env*`, `*.omsecrets`, keys, certificates, `*secret*`, `*credential*` ...). Those are counted with their reason;
* the narrative step opens only its candidate files, never a secret-named one, and reads at most `NARRATIVE_READ_BYTES` of each;
* nothing is written into the walked path.

The hash limit defaults to `HASH_MAX_BYTES_DEFAULT` and is configurable with the `RE_WALK_HASH_MAX_BYTES` environment
variable (`0` turns hashing off, said on the result as "hashing off").
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import stat as stat_mod
import time
from collections import Counter
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

#: A file larger than this is not hashed ("over the hash limit"). 10 MiB: SHA-256 reads that at disk speed, and the files
#: that matter for duplicates and change (documents, small data files, code) sit well under it.
HASH_MAX_BYTES_DEFAULT = 10 * 1024 * 1024
HASH_ENV = "RE_WALK_HASH_MAX_BYTES"
HASH_ALGORITHM = "SHA-256"

#: Runs of per-file rows kept per file system; the newest are kept, older runs are removed when a newer run is written.
WALK_RUNS_KEPT = 5

#: Hash states (the `hash_state` column). Only "hashed" has a hash.
HASHED = "hashed"
OVER_LIMIT = "over_limit"
SECRET_NAME = "secret_name"
LINK = "symlink"
UNREADABLE = "unreadable"
HASHING_OFF = "hashing_off"
NOT_A_FILE = "not_a_file"
OVER_BUDGET = "over_budget"
#: A remote object whose etag is a multipart upload's ("<hex>-<parts>"): not a hash of its content (S3 slice).
MULTIPART_ETAG = "multipart_etag"
#: The "hash" of a single-part S3 object is its etag, which the source computed: no byte is read for it.
ETAG_ALGORITHM = "S3 ETag (MD5 of a single-part object, computed by the source)"
#: Said for a field a remote source does not carry (reach_mechanisms.NOT_REPORTED; repeated so this module imports nothing).
NOT_REPORTED = "not reported by this source"

#: A total hashing budget per run, so a huge tree cannot hash for hours. Both are environment-overridable (0 = no limit on
#: that axis is NOT offered: a budget is always set). When either is spent, hashing stops for the rest of the run and the
#: result says so ("hashing stopped at ...: N files not hashed").
HASH_BUDGET_BYTES_DEFAULT = 2 * 1024 ** 3        # 2 GiB read in total
HASH_BUDGET_SECONDS_DEFAULT = 300                # 5 minutes of wall time
HASH_BUDGET_BYTES_ENV = "RE_WALK_HASH_BUDGET_BYTES"
HASH_BUDGET_SECONDS_ENV = "RE_WALK_HASH_BUDGET_SECONDS"

#: Narrative reading limits.
NARRATIVE_READ_BYTES = 64 * 1024            # of each candidate file
NARRATIVE_MAX_FILE_BYTES = 512 * 1024       # a candidate larger than this is listed, not read
NARRATIVE_MAX_PER_FOLDER = 4
NARRATIVE_MAX_READS = 1000                  # candidate files read per run; beyond it the step says it stopped
NARRATIVE_MAX_ENTRIES = 500                 # entries kept in the stored blob
NARRATIVE_TEXT_EXTENSIONS = {"", "md", "markdown", "rst", "txt", "text", "adoc"}

LARGEST_FOLDERS = 10


def hash_limit_bytes() -> int:
    """The size limit for hashing a file, from `RE_WALK_HASH_MAX_BYTES` or the default. 0 = hashing off."""
    raw = os.environ.get(HASH_ENV, "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            log.warning("%s=%r is not a whole number of bytes; using the default", HASH_ENV, raw)
    return HASH_MAX_BYTES_DEFAULT


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            log.warning("%s=%r is not a whole number; using the default", name, raw)
    return default


class HashBudget:
    """Bytes and wall time a run may spend hashing. Once either is spent it stays spent: every later file is `over_budget`."""

    def __init__(self, max_bytes: int | None = None, max_seconds: float | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.max_bytes = _env_int(HASH_BUDGET_BYTES_ENV, HASH_BUDGET_BYTES_DEFAULT) if max_bytes is None else max_bytes
        self.max_seconds = _env_int(HASH_BUDGET_SECONDS_ENV, HASH_BUDGET_SECONDS_DEFAULT) if max_seconds is None else max_seconds
        self._clock = clock
        self._start = clock()
        self.bytes_used = 0
        self.stopped_by = ""          # "bytes" | "time" | ""
        self.not_hashed = 0

    def allow(self, size: int) -> bool:
        """Whether a file of `size` bytes may be hashed now. Counts the file as not hashed when it may not."""
        if not self.stopped_by:
            if self._clock() - self._start >= self.max_seconds:
                self.stopped_by = "time"
            elif self.bytes_used + max(0, size) > self.max_bytes:
                self.stopped_by = "bytes"
        if self.stopped_by:
            self.not_hashed += 1
            return False
        return True

    def spent(self, n: int) -> None:
        self.bytes_used += n

    def out_of_time(self) -> bool:
        return self._clock() - self._start >= self.max_seconds

    def words(self) -> str:
        """"hashing stopped at 2.0 GiB: 14 files not hashed", or '' when the budget held."""
        if not self.stopped_by:
            return ""
        at = f"{_fmt_bytes(self.max_bytes)}" if self.stopped_by == "bytes" else f"{int(self.max_seconds)} seconds"
        return f"hashing stopped at {at}: {self.not_hashed:,} file{'s' if self.not_hashed != 1 else ''} not hashed"

    def as_dict(self) -> dict:
        return {"max_bytes": self.max_bytes, "max_seconds": self.max_seconds, "bytes_hashed": self.bytes_used,
                "stopped_by": self.stopped_by, "files_not_hashed": self.not_hashed, "words": self.words()}


def _fmt_bytes(n: int) -> str:
    v = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if v < 1024 or unit == "GiB":
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return f"{v:.1f} GiB"


# ── safe opening: a plain file or nothing ────────────────────────────────────────────────────────────────────────────

_O_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)


def read_plain_file(path: Path, max_bytes: int, *, chunk: int = 1024 * 1024, whole: bool = False, on_chunk: Callable[[bytes], bool] | None = None) -> bytes | None:
    """Open `path` without following a link or blocking (O_NOFOLLOW | O_NONBLOCK), require by `fstat` on the OPEN descriptor
    that it is a regular file, and read at most min(max_bytes, the stat'ed size) (`whole`: refuse a file larger than max_bytes). Returns the bytes read, or None when the
    path is not a plain file (a link, a FIFO, a device, a directory) or cannot be opened. Checking after opening closes the gap
    where a file is swapped for a link or FIFO between the walk's stat and the read. `on_chunk` sees each chunk and returns
    False to stop early (the result is then None)."""
    try:
        fd = os.open(path, _O_FLAGS)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat_mod.S_ISREG(st.st_mode):
            return None
        if whole and st.st_size > max_bytes:      # grew past the limit since the walk's stat: not a prefix passed off as the file
            return None
        remaining = min(max_bytes, st.st_size)
        out = bytearray()
        while remaining > 0:
            data = os.read(fd, min(chunk, remaining))
            if not data:
                break
            remaining -= len(data)
            if on_chunk is not None and on_chunk(data) is False:
                return None
            out += data
        return bytes(out)
    except OSError:
        return None
    finally:
        os.close(fd)


# ── secret-looking names: never hashed, never read as narratives ─────────────────────────────────────────────────────

_SECRET_SUFFIXES = (".omsecrets", ".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".kdbx", ".gpg", ".asc",
                    ".secret", ".secrets", ".crt", ".cer", ".ovpn")
_SECRET_PREFIXES = (".env", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".netrc", ".npmrc", ".pgpass", ".htpasswd")
_SECRET_WORDS = ("secret", "credential", "password", "passwd", "private_key", "privatekey", "api_key", "apikey", "token")


def looks_secret(name: str) -> bool:
    """Whether a file NAME looks like it holds a secret. Name-based on purpose: the walk must decide before opening."""
    n = name.lower()
    return n.startswith(_SECRET_PREFIXES) or n.endswith(_SECRET_SUFFIXES) or any(w in n for w in _SECRET_WORDS)


# ── owner / group / mode ─────────────────────────────────────────────────────────────────────────────────────────────

try:                                            # not on Windows: owner and group are then numeric or blank
    import grp
    import pwd
except ImportError:                             # pragma: no cover
    pwd = grp = None  # type: ignore[assignment]

_USERS: dict[int, str] = {}
_GROUPS: dict[int, str] = {}


def owner_name(uid: int) -> str:
    if uid not in _USERS:
        try:
            _USERS[uid] = pwd.getpwuid(uid).pw_name if pwd else str(uid)
        except (KeyError, OverflowError):
            _USERS[uid] = str(uid)
    return _USERS[uid]


def group_name(gid: int) -> str:
    if gid not in _GROUPS:
        try:
            _GROUPS[gid] = grp.getgrgid(gid).gr_name if grp else str(gid)
        except (KeyError, OverflowError):
            _GROUPS[gid] = str(gid)
    return _GROUPS[gid]


def mode_octal(st_mode: int) -> str:
    """The permission bits as four octal digits ("0644"); setuid/setgid/sticky ride in the first digit."""
    return format(stat_mod.S_IMODE(st_mode), "04o")


# ── hashing ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def hash_file(path: Path, size: int, *, is_symlink: bool, name: str, limit: int, budget: HashBudget | None = None) -> tuple[str, str]:
    """(hash, state). Opens the file only if it is a plain file under the limit with a name that does not look secret, and
    only while the run's hashing budget holds. A link is refused FIRST (even with hashing off), and the open itself refuses a
    link or non-regular file (`read_plain_file`), so a file swapped in after the walk's stat is not read either."""
    if is_symlink:
        return "", LINK
    if limit <= 0:
        return "", HASHING_OFF
    if looks_secret(name):
        return "", SECRET_NAME
    if size > limit:
        return "", OVER_LIMIT
    if budget is not None and not budget.allow(size):
        return "", OVER_BUDGET
    h = hashlib.sha256()
    stopped_for_time = []

    def feed(chunk: bytes) -> bool:
        h.update(chunk)
        if budget is not None:
            budget.spent(len(chunk))
            if budget.out_of_time():
                stopped_for_time.append(True)
                return False
        return True

    data = read_plain_file(path, limit, whole=True, on_chunk=feed)
    if stopped_for_time:
        budget.stopped_by = budget.stopped_by or "time"   # type: ignore[union-attr]
        budget.not_hashed += 1                            # type: ignore[union-attr]
        return "", OVER_BUDGET
    if data is None:
        return "", UNREADABLE
    return h.hexdigest(), HASHED


def build_row(rel_path: str, path: Path, st: os.stat_result, *, is_symlink: bool, limit: int, modified_at: str,
              budget: HashBudget | None = None, accessed_at: str | None = None) -> dict:
    """One per-file row from a file the walk already stat'ed. Opens the file only through `hash_file`'s guards.
    `accessed_at` is the access time from that same stat, taken BEFORE `hash_file` reads the file, so this walk's own
    read is never this walk's access time (the next walk can see it: fs_activity says so)."""
    folder, _, name = rel_path.rpartition("/")
    digest, state = hash_file(path, st.st_size, is_symlink=is_symlink, name=name, limit=limit, budget=budget)
    return {
        "file_path": rel_path,
        "folder_path": folder,
        "folder_depth": rel_path.count("/"),
        "size_bytes": st.st_size,
        "modified_at": modified_at,
        "accessed_at": accessed_at,
        "owner_name": owner_name(st.st_uid),
        "group_name": group_name(st.st_gid),
        "mode_octal": mode_octal(st.st_mode),
        "world_readable": 1 if st.st_mode & stat_mod.S_IROTH else 0,
        "content_hash": digest,
        "hash_state": state,
        "is_symlink": bool(is_symlink),      # carried on the row (not stored): a link is never opened for any purpose
    }


# ── folder measures and summaries (pure, over the rows) ──────────────────────────────────────────────────────────────

def _ancestors(folder: str):
    """The folder and each of its parents up to the root ('')."""
    while True:
        yield folder
        if not folder:
            return
        folder = folder.rpartition("/")[0]


def folder_measures(rows: list[dict]) -> dict:
    """Bytes, files and depth per folder, and the heaviest folders. A "folder" here is one holding at least one file at or
    below it (an empty folder has no rows; the walk counts those separately). Recursive bytes include sub-folders."""
    direct: dict[str, list[int]] = {}
    for r in rows:
        f = r["folder_path"]
        d = direct.setdefault(f, [0, 0])
        d[0] += 1
        d[1] += int(r["size_bytes"] or 0)
    total: dict[str, list[int]] = {}
    for f, (n, b) in direct.items():
        for a in _ancestors(f):
            t = total.setdefault(a, [0, 0])
            t[0] += n
            t[1] += b

    def depth(folder: str) -> int:
        return folder.count("/") + 1 if folder else 0

    histogram: dict[int, dict[str, int]] = {}
    for f, (n, b) in direct.items():
        h = histogram.setdefault(depth(f), {"folders": 0, "files": 0, "bytes": 0})
        h["files"] += n
        h["bytes"] += b
    for f in total:
        histogram.setdefault(depth(f), {"folders": 0, "files": 0, "bytes": 0})["folders"] += 1
    ranked = sorted(((f, v) for f, v in total.items() if f), key=lambda kv: (-kv[1][1], kv[0]))[:LARGEST_FOLDERS]
    all_bytes = total.get("", [0, 0])[1]
    top_bytes = sum(v[1] for _, v in ranked)
    return {
        "folders_with_files": len(total),
        "deepest_folder_depth": max((depth(f) for f in total), default=0),
        "depth_histogram": [{"depth": d, **histogram[d]} for d in sorted(histogram)],
        "largest_folders": [{"path": f, "depth": depth(f), "files": v[0], "bytes": v[1]} for f, v in ranked],
        "largest_folders_bytes": top_bytes,
        "total_bytes": all_bytes,
    }


def summaries(rows: list[dict], *, hash_limit: int, budget: dict | None = None,
              algorithm: str = HASH_ALGORITHM) -> dict:
    """Counts only: owners, modes, hashing. Nothing here names a file, a person or a hash, so a publish may read it.
    A source that reports no owner, group or mode (an object store) leaves `owners` and `modes` out (None, so the
    publish omits them) and says so under `not_reported`, never a count of zero."""
    states = Counter(r["hash_state"] for r in rows)
    reported = [r for r in rows if r.get("mode_octal") != NOT_REPORTED]
    owners = {r["owner_name"] for r in reported if r["owner_name"]}
    groups = {r["group_name"] for r in reported if r["group_name"]}
    no_stat = bool(rows) and not reported
    return {
        "files": len(rows),
        "owners": None if no_stat else {
            "distinct_owners": len(owners), "distinct_groups": len(groups),
            "world_readable_files": sum(1 for r in reported if r["world_readable"])},
        "modes": None if no_stat else dict(sorted(Counter(r["mode_octal"] for r in reported).items(),
                                                  key=lambda kv: (-kv[1], kv[0]))),
        **({"not_reported": {"fields": ["owner", "group", "mode"], "words": NOT_REPORTED}} if no_stat else {}),
        "hashing": {
            "algorithm": algorithm,
            "limit_bytes": hash_limit,
            "off": hash_limit <= 0,
            "hashed_files": states.get(HASHED, 0),
            "hashed_bytes": sum(int(r["size_bytes"] or 0) for r in rows if r["hash_state"] == HASHED),
            "over_limit_files": states.get(OVER_LIMIT, 0),
            "secret_named_files": states.get(SECRET_NAME, 0),
            "symlink_files": states.get(LINK, 0),
            "unreadable_files": states.get(UNREADABLE, 0),
            "over_budget_files": states.get(OVER_BUDGET, 0),
            **({"multipart_etag_files": states[MULTIPART_ETAG]} if states.get(MULTIPART_ETAG) else {}),
            "budget_stopped_by": (budget or {}).get("stopped_by", ""),
            "budget_words": (budget or {}).get("words", ""),
        },
    }


def capped_words(limits: dict | None) -> str:
    """"capped at 100,000 entries" / "capped at depth 20" / "" when the walk went to the end. Said on every result."""
    lim = limits or {}
    if not lim.get("capped"):
        return ""
    parts = []
    reasons = lim.get("capped_reasons") or []
    if "entry cap" in reasons:
        parts.append(f"{int(lim.get('max_entries') or 0):,} entries")
    if "depth cap" in reasons:
        parts.append(f"depth {int(lim.get('max_depth') or 0)}")
    return "capped at " + " and ".join(parts) if parts else "capped"


# ── folder_narratives ────────────────────────────────────────────────────────────────────────────────────────────────

_NARRATIVE_STEMS = {"readme", "read_me", "read-me", "index", "about", "notes", "manifest", "contents", "changelog",
                    "license", "licence", "copying", "citation", "authors", "provenance"}
_NARRATIVE_NAME_WORDS = ("dictionary", "codebook", "schema", "layout")
_LICENCE_STEMS = {"license", "licence", "copying"}

#: Recognised licence names, matched against text. Only a recognised name is ever kept; the rest is "unrecognised".
_LICENCES: list[tuple[str, re.Pattern]] = [
    ("MIT", re.compile(r"\bMIT License\b|Permission is hereby granted, free of charge", re.I)),
    ("Apache-2.0", re.compile(r"Apache License[\s,]+Version 2\.0|apache[- ]2\.0", re.I)),
    ("GPL-3.0", re.compile(r"GNU GENERAL PUBLIC LICENSE[\s\S]{0,80}Version 3|\bGPL[- ]?v?3", re.I)),
    ("GPL-2.0", re.compile(r"GNU GENERAL PUBLIC LICENSE[\s\S]{0,80}Version 2|\bGPL[- ]?v?2", re.I)),
    ("LGPL", re.compile(r"GNU LESSER GENERAL PUBLIC LICENSE|\bLGPL", re.I)),
    ("AGPL-3.0", re.compile(r"GNU AFFERO GENERAL PUBLIC LICENSE|\bAGPL", re.I)),
    ("MPL-2.0", re.compile(r"Mozilla Public License[\s,]+(?:Version |v)?2\.0|\bMPL[- ]?2", re.I)),
    ("BSD-3-Clause", re.compile(r"BSD[- ]3|Neither the name of", re.I)),
    ("BSD-2-Clause", re.compile(r"BSD[- ]2|Redistribution and use in source and binary forms", re.I)),
    ("CC-BY-4.0", re.compile(r"CC[- ]BY[- ]4\.0|Attribution 4\.0 International", re.I)),
    ("CC0-1.0", re.compile(r"CC0|Creative Commons Zero", re.I)),
    ("Unlicense", re.compile(r"\bThe Unlicense\b|This is free and unencumbered software", re.I)),
]
_LICENCE_LINE = re.compile(r"^\W*licen[cs]e[sd]?\W*(?:is|under|:|-)\s*(.+)$", re.I | re.M)
_OWNER_LINE = re.compile(r"^\W*(?:owner|owners|maintainer|maintainers|author|authors|contact|steward)\W*[:\-]\s*(.{2,120})$",
                         re.I | re.M)
_SOURCE_LINE = re.compile(r"^\W*(?:source|provenance|origin|collected from|downloaded from|data source)\W*[:\-]\s*(.{2,160})$",
                          re.I | re.M)
_DATE_LINE = re.compile(r"^.*\b(?:collected|collection|coverage|period|updated|as of|date)\b.*$", re.I | re.M)
_DATE_TOKEN = re.compile(r"\b(?:19|20)\d{2}(?:-\d{2}(?:-\d{2})?)?\b")


def licence_name(text: str) -> str:
    """A recognised licence name from licence text or a `License: X` line, else ''. Unrecognised text is never kept."""
    for name, pat in _LICENCES:
        if pat.search(text):
            return name
    m = _LICENCE_LINE.search(text)
    if m:
        line = m.group(1)
        for name, pat in _LICENCES:
            if pat.search(line) or name.lower() in line.lower():
                return name
    return ""


def _data_extensions() -> set[str]:
    from resource_explorer.surveyors.sub_surveyors.data_profiler import _DATA_EXTENSIONS

    return set(_DATA_EXTENSIONS)


def is_narrative_candidate(name: str, folder_name: str, sibling_stems: set[str]) -> bool:
    stem, _, ext = name.rpartition(".") if "." in name else (name, "", "")
    if "." not in name:
        stem, ext = name, ""
    s, e = stem.lower(), ext.lower()
    if e not in NARRATIVE_TEXT_EXTENSIONS:
        return False
    if looks_secret(name):
        return False
    if s in _NARRATIVE_STEMS:
        return True
    if any(w in s for w in _NARRATIVE_NAME_WORDS):
        return True
    return e in {"md", "markdown", "rst", "txt", "text"} and (s == folder_name.lower() or s in sibling_stems)


def _clean(text: str, limit: int) -> str:
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)         # markdown links and images keep their label
    text = re.sub(r"[`*_>#]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def extract_narrative(text: str, file_name: str, folder_files: list[str]) -> dict:
    """Title, first paragraph, licence, stated owner, source, dates and the folder's files the text names. All proposals."""
    lines = text.splitlines()
    title = ""
    body_start = 0
    for i, line in enumerate(lines):
        if line.strip():
            title = _clean(line, 120)
            body_start = i + 1
            if i + 1 < len(lines) and re.fullmatch(r"\s*[=\-~]{3,}\s*", lines[i + 1]):     # setext underline
                body_start = i + 2
            break
    paragraph: list[str] = []
    for line in lines[body_start:]:
        if line.strip():
            if line.lstrip().startswith("#") and not paragraph:
                continue
            paragraph.append(line.strip())
        elif paragraph:
            break
    description = _clean(" ".join(paragraph), 400)
    stem = file_name.rpartition(".")[0].lower() if "." in file_name else file_name.lower()
    lic = licence_name(text) if (stem in _LICENCE_STEMS or _LICENCE_LINE.search(text)) else ""
    owner = _OWNER_LINE.search(text)
    source = _SOURCE_LINE.search(text)
    dates: list[str] = []
    for m in _DATE_LINE.finditer(text):
        for d in _DATE_TOKEN.findall(m.group(0)):
            if d not in dates:
                dates.append(d)
    lower = text.lower()
    declared = [n for n in folder_files if len(n) >= 3 and n.lower() != file_name.lower() and n.lower() in lower][:50]
    return {
        "title": title, "description": description, "licence": lic,
        "stated_owner": _clean(owner.group(1), 120) if owner else "",
        "stated_source": _clean(source.group(1), 160) if source else "",
        "stated_dates": dates[:4],
        "declared_files": declared,
    }


def folder_narratives(root: Path, rows: list[dict], *, lookup: Callable[[str, str], dict | None] | None = None,
                      read_bytes: int = NARRATIVE_READ_BYTES,
                      reader: Callable[[str, int], bytes | None] | None = None) -> dict:
    """The narrative files of each folder, read once each (size-capped), as proposals.

    Reads ONLY candidate files: name-matched (README, INDEX, ABOUT, NOTES, MANIFEST, CONTENTS, CHANGELOG, LICENSE, COPYING,
    CITATION, a dictionary/codebook/schema/layout by name, or a text file named for its folder or a sibling data file),
    plain (not a link), text-typed, not secret-named, and under `NARRATIVE_MAX_FILE_BYTES`. `lookup` is the file
    classifier's reference-data lookup (name, extension) -> metadata; its `fileType` rides on the entry as the evidence of
    what Egeria's reference data calls the file. Returns {entries, summary}; every entry has status "proposal"."""
    by_folder: dict[str, list[dict]] = {}
    for r in rows:
        by_folder.setdefault(r["folder_path"], []).append(r)

    entries: list[dict] = []
    reads = skipped_large = 0
    stopped = False
    licences: Counter = Counter()
    for folder in sorted(by_folder):
        files = by_folder[folder]
        names = [r["file_path"].rpartition("/")[2] for r in files]
        # a text file named for a sibling DATA file (payroll.txt beside payroll.csv) describes it; a text file is never its own sibling
        sibling_stems = {n.rpartition(".")[0].lower() for n in names if "." in n and n.rpartition(".")[2].lower() in _data_extensions()}
        folder_name = folder.rpartition("/")[2] or root.name
        taken = 0
        for r in sorted(files, key=lambda x: x["file_path"]):
            name = r["file_path"].rpartition("/")[2]
            if not is_narrative_candidate(name, folder_name, sibling_stems):
                continue
            if r.get("is_symlink") or r["hash_state"] == LINK:
                continue
            if taken >= NARRATIVE_MAX_PER_FOLDER:
                break
            if int(r["size_bytes"] or 0) > NARRATIVE_MAX_FILE_BYTES:
                skipped_large += 1
                continue
            if reads >= NARRATIVE_MAX_READS:
                stopped = True
                break
            # `reader` (a remote mechanism's capped read, by the row's relative path) replaces the local open.
            raw = reader(r["file_path"], read_bytes) if reader else read_plain_file(root / r["file_path"], read_bytes)
            if raw is None:                  # a link, FIFO or device swapped in after the walk, or unreadable: not read
                continue
            text = raw.decode("utf-8", errors="replace")
            reads += 1
            taken += 1
            ext = name.rpartition(".")[2].lower() if "." in name else ""
            meta = (lookup(name, ext) if lookup else None) or {}
            found = extract_narrative(text, name, [n for n in names])
            if found["licence"]:
                licences[found["licence"]] += 1
            entries.append({
                "folder": folder, "folder_depth": r["folder_depth"], "file": r["file_path"],
                "file_type": meta.get("fileType") or "", "modified_at": r["modified_at"],
                "status": "proposal", "stated_by": name, **found,
            })
        if stopped:
            break
    folders_with = len({e["folder"] for e in entries})
    return {
        "entries": entries[:NARRATIVE_MAX_ENTRIES],
        "summary": {
            "folders": len(by_folder), "folders_with_narrative": folders_with, "files_read": reads,
            "entries_kept": min(len(entries), NARRATIVE_MAX_ENTRIES), "entries_found": len(entries),
            "too_large_to_read": skipped_large,
            "folders_with_licence": len({e["folder"] for e in entries if e["licence"]}),
            "licences": dict(licences),
            "folders_with_stated_owner": len({e["folder"] for e in entries if e["stated_owner"]}),
            "folders_with_stated_source": len({e["folder"] for e in entries if e["stated_source"]}),
            "folders_with_stated_dates": len({e["folder"] for e in entries if e["stated_dates"]}),
            "stopped_at_read_cap": stopped, "read_cap": NARRATIVE_MAX_READS,
        },
    }


def classifier_lookup() -> Callable[[str, str], dict | None] | None:
    """Egeria's file reference data lookup (name, extension), or None where it cannot be built."""
    try:
        from resource_explorer.surveyors.file_classifier.type_cache import get_cache

        return get_cache().lookup
    except Exception:  # noqa: BLE001 -- the narrative step runs without a classification label
        log.warning("file reference data not available for narrative classification", exc_info=True)
        return None


# ── the block stored in survey_data, and the publish specs ──────────────────────────────────────────────────────────

def extension_block(rows: list[dict], narratives: dict, *, run_id: str, hash_limit: int, led_by: str,
                    limits: dict | None, budget: dict | None = None, algorithm: str = HASH_ALGORITHM) -> dict:
    """The `walk_extensions` block kept in `survey_data`. Paths are in it (the registry is where paths live); the publish
    reads `extension_specs`, which reads only counts out of it."""
    return {
        "run_id": run_id,
        "led_by": led_by,
        "capped_words": capped_words(limits),
        "folders": folder_measures(rows),
        **summaries(rows, hash_limit=hash_limit, budget=budget, algorithm=algorithm),
        "narratives": narratives["summary"],
    }


# Annotation names for what Resource Explorer measures beyond Egeria's Folder Survey. Each is one of Egeria's own annotation
# TYPES (ResourceMeasureAnnotation / ResourceProfileAnnotation), named for what it holds; the surveyor is on the report.
CAPTURE_FOLDER_BULK = "Capture Folder Bulk and Depth"
PROFILE_FOLDER_DEPTHS = "Profile Files by Folder Depth"
CAPTURE_FILE_OWNERSHIP = "Capture File Ownership"
PROFILE_FILE_MODES = "Profile File Modes"
CAPTURE_CONTENT_HASHES = "Capture Content Hashes"
CAPTURE_FOLDER_NARRATIVES = "Capture Folder Narratives"
PROFILE_NARRATIVE_LICENCES = "Profile Licences Named in Folders"
PROFILE_DIRECTORIES_NOT_WALKED = "Profile Directories Not Walked"

EXTENSION_NAMES = (CAPTURE_FOLDER_BULK, PROFILE_FOLDER_DEPTHS, CAPTURE_FILE_OWNERSHIP, PROFILE_FILE_MODES,
                   CAPTURE_CONTENT_HASHES, CAPTURE_FOLDER_NARRATIVES, PROFILE_NARRATIVE_LICENCES,
                   PROFILE_DIRECTORIES_NOT_WALKED)

_TEXT = {
    CAPTURE_FOLDER_BULK: (
        "Depth and bulk of the folders under a directory, measured by Resource Explorer.",
        "Counts how many folders hold files, how deep the tree goes and how many bytes sit in the ten heaviest folders. "
        "Egeria's Folder Survey keeps whole-tree totals; it does not keep bytes and depth per folder. No folder name is published."),
    PROFILE_FOLDER_DEPTHS: (
        "Files by folder depth, measured by Resource Explorer.",
        "For each folder depth, the number of files held at that depth (the root is depth 0)."),
    CAPTURE_FILE_OWNERSHIP: (
        "Owners, groups and world-readable files, measured by Resource Explorer.",
        "Counts of distinct file owners and groups and of files readable by everyone. Egeria's Folder Survey keeps "
        "readable, writeable and executable counts, not who owns the files. No owner or group name is published."),
    PROFILE_FILE_MODES: (
        "Files by permission mode, measured by Resource Explorer.",
        "For each permission mode (octal), the number of files that carry it."),
    CAPTURE_CONTENT_HASHES: (
        "Content hashes taken, measured by Resource Explorer.",
        "A SHA-256 hash is taken of each plain file under the size limit whose name does not look like a secret, so that "
        "duplicates and changes can be found without reading the file again. This annotation counts the files hashed and "
        "the files not hashed, by reason. No hash is published."),
    CAPTURE_FOLDER_NARRATIVES: (
        "Folders that describe themselves, found by Resource Explorer.",
        "Narrative files (a read-me, an index, an about or notes file, a manifest, a licence and the like) were read once each, "
        "under a size limit. What they state is a proposal, not a fact. This annotation counts folders and statements; no text, file "
        "name or path is published."),
    PROFILE_NARRATIVE_LICENCES: (
        "Licences named in folders, found by Resource Explorer.",
        "For each recognised licence, the number of folders whose licence or read-me file names it."),
    PROFILE_DIRECTORIES_NOT_WALKED: (
        "Directories the walk did not enter, by name.",
        "Noise directories (such as .git, node_modules and virtual environments) are counted and named, never skipped silently."),
}


def extension_specs(sd: dict) -> list[dict]:
    """Neutral annotation specs (the shape `folder_survey_annotations.to_annotations` takes) for the walk's extensions.
    Pure. COUNTS ONLY: no path, file or folder name, owner or group name, hash or narrative text goes into a spec. A section
    the walk did not measure is left out, never written as zero."""
    ext = sd.get("walk_extensions")
    if not ext:
        return []
    from resource_explorer.surveyors.file_type_profile import EGERIA_ANALYSIS_STEP

    def spec(name: str, kind: str, **extra) -> dict:
        summary, explanation = _TEXT[name]
        return {"annotation_type_name": name, "kind": kind, "analysis_step": EGERIA_ANALYSIS_STEP,
                "summary": summary, "explanation": explanation, **extra}

    def strs(d: dict) -> dict[str, str]:
        return {k: str(v) for k, v in d.items() if v is not None}

    out: list[dict] = []
    fo = ext.get("folders")
    if fo is not None:
        props = strs({
            "Folders holding files": fo.get("folders_with_files"),
            "Deepest folder level": fo.get("deepest_folder_depth"),
            "Bytes in the ten heaviest folders": fo.get("largest_folders_bytes"),
            "Total bytes": fo.get("total_bytes"),
        })
        out.append(spec(CAPTURE_FOLDER_BULK, "measure", resource_properties=props, json_properties=dict(props)))
        out.append(spec(PROFILE_FOLDER_DEPTHS, "profile",
                        value_count={str(h["depth"]): int(h["files"]) for h in fo.get("depth_histogram", [])}))
    ow = ext.get("owners")
    if ow is not None:
        props = strs({"Distinct owners": ow.get("distinct_owners"), "Distinct groups": ow.get("distinct_groups"),
                      "World-readable files": ow.get("world_readable_files")})
        out.append(spec(CAPTURE_FILE_OWNERSHIP, "measure", resource_properties=props, json_properties=dict(props)))
    if ext.get("modes") is not None:
        out.append(spec(PROFILE_FILE_MODES, "profile", value_count={str(k): int(v) for k, v in ext["modes"].items()}))
    hs = ext.get("hashing")
    if hs is not None:
        props = strs({
            "Hash algorithm": hs.get("algorithm"), "Hash size limit (bytes)": hs.get("limit_bytes"),
            "Files hashed": hs.get("hashed_files"), "Bytes hashed": hs.get("hashed_bytes"),
            "Files over the hash size limit": hs.get("over_limit_files"),
            "Files not hashed (secret-looking name)": hs.get("secret_named_files"),
            "Files not hashed (linked)": hs.get("symlink_files"),
            "Files not hashed (unreadable)": hs.get("unreadable_files"),
            "Files not hashed (hashing budget spent)": hs.get("over_budget_files"),
            "Files not hashed (multipart etag)": hs.get("multipart_etag_files"),
            "Hashing stopped by": hs.get("budget_stopped_by") or None,
            "Hashing off": str(bool(hs.get("off"))).lower(),
        })
        out.append(spec(CAPTURE_CONTENT_HASHES, "measure", resource_properties=props, json_properties=dict(props)))
    nr = ext.get("narratives")
    if nr is not None:
        props = strs({
            "Folders examined": nr.get("folders"), "Folders with a narrative file": nr.get("folders_with_narrative"),
            "Narrative files read": nr.get("files_read"), "Narrative files too large to read": nr.get("too_large_to_read"),
            "Folders naming a licence": nr.get("folders_with_licence"),
            "Folders stating an owner or contact": nr.get("folders_with_stated_owner"),
            "Folders stating a source": nr.get("folders_with_stated_source"),
            "Folders stating dates": nr.get("folders_with_stated_dates"),
            "Stopped at read limit": str(bool(nr.get("stopped_at_read_cap"))).lower(),
        })
        out.append(spec(CAPTURE_FOLDER_NARRATIVES, "measure", resource_properties=props, json_properties=dict(props)))
        if nr.get("licences"):
            out.append(spec(PROFILE_NARRATIVE_LICENCES, "profile",
                            value_count={str(k): int(v) for k, v in nr["licences"].items()}))
    nw = (sd.get("walk_limits") or {}).get("not_walked_by_name")
    if nw:
        out.append(spec(PROFILE_DIRECTORIES_NOT_WALKED, "profile", value_count={str(k): int(v) for k, v in nw.items()}))
    return out


# ── the step: rows kept, narratives read, block stored ──────────────────────────────────────────────────────────────

LED_BY_RE = "re"
LED_BY_EGERIA = "egeria"


def attach_extensions(fs_entity, registry, survey_data: dict, *, led_by: str = LED_BY_RE,
                      egeria_report_guid: str = "", reader: Callable[[str, int], bytes | None] | None = None) -> dict:
    """Finish a walk that ran with `walk_extensions=True`: keep its per-file rows for this run, read the narrative files,
    and put the `walk_extensions` block and `folder_narratives` into `survey_data` (which the caller then stores).

    The rows are removed from `survey_data` (they live in `filesystem_walk_files`, never in the blob). A failed write of the
    rows is recorded in the block (`rows_kept`) and does not discard the walk; what the screen says about the rows is read
    back from the registry, not from this record."""
    rows = survey_data.pop("walk_files", None)
    limit = survey_data.pop("walk_hash_limit_bytes", hash_limit_bytes())
    budget = survey_data.pop("walk_hash_budget", None)
    if rows is None:
        return survey_data
    run_id = survey_data.get("surveyed_at") or ""
    root = Path(fs_entity.local_mount_point)
    narratives = folder_narratives(root, rows, lookup=classifier_lookup(), reader=reader)
    block = extension_block(rows, narratives, run_id=run_id, hash_limit=limit, led_by=led_by,
                            limits=survey_data.get("walk_limits"), budget=budget,
                            algorithm=survey_data.pop("walk_hash_algorithm", HASH_ALGORITHM))
    try:
        stored = registry.add_filesystem_walk_files(fs_entity.slug, run_id, rows, led_by=led_by,
                                                    egeria_report_guid=egeria_report_guid)
        block["rows_kept"] = {"rows": stored["rows"], "pruned_runs": stored["pruned_runs"], "error": ""}
    except Exception as exc:  # noqa: BLE001 -- the walk stands; the missing rows are said
        log.exception("%s: could not keep the walk's per-file rows", fs_entity.slug)
        block["rows_kept"] = {"rows": 0, "pruned_runs": [], "error": f"{type(exc).__name__}: {exc}"}
    survey_data["walk_extensions"] = block
    survey_data["folder_narratives"] = narratives["entries"]
    return survey_data


def run_alongside_egeria(fs_entity, registry, *, egeria_report_guid: str = "") -> dict:
    """Resource Explorer's extensions beside an Egeria-led folder survey: the same walk, but ONLY what Egeria's Folder
    Survey does not keep is stored and published (rows, folder bulk and depth, owner and mode, hashes, narratives). The stored
    blob carries no counts, classification or inventory (Egeria has those) and no `surveyed_by`, so the folder-survey card
    never mistakes it for the survey itself. Raises FileNotFoundError where Resource Explorer cannot read the mount."""
    from resource_explorer.surveyors.filesystem.local_filesystem_surveyor import LocalFileSystemSurveyor

    sd = LocalFileSystemSurveyor(fs_entity, registry).run(profile_contents=False, walk_extensions=True)
    attach_extensions(fs_entity, registry, sd, led_by=LED_BY_EGERIA, egeria_report_guid=egeria_report_guid)
    out = {
        "surveyed_at": sd["surveyed_at"], "total_files": sd["total_files"], "total_data_files": sd["total_data_files"],
        "total_size_bytes": sd["total_size_bytes"], "walk_limits": sd["walk_limits"],
        "walk_extensions": sd["walk_extensions"], "folder_narratives": sd["folder_narratives"],
        "report_sentence": "measured by Resource Explorer · beside Egeria's Folder Survey",
        "beside_egeria_report": egeria_report_guid,
    }
    registry.add_filesystem_survey(fs_entity.slug, out["surveyed_at"], out, egeria_report_guid="", source="walk-extensions")
    return out
