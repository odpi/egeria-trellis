"""`narrative_docs` (Files build step 7): the narrative files the walk found, read in full and chunked by section into
Resource Explorer's search index, so the long form of what a README, a codebook or a data dictionary says can be
quoted with its source (Files brief F11's long-form half, §2a; §3 item 4).

`folder_narratives` (Files build step 1, `walk_extensions.folder_narratives`) already finds each folder's narrative files
and reads the first 64 KB of each for a title, a first paragraph, a licence, a stated owner, source and dates. This step
takes the SAME files, the ones that walk recorded in `survey_data["folder_narratives"]`, and nothing else:

* each is opened by `walk_extensions.read_plain_file` (no link followed, a regular file by `fstat` on the open
  descriptor), whole and only up to `walk_extensions.NARRATIVE_MAX_FILE_BYTES`; a file that grew past it since the walk
  is not read (`too_large`), never cut and passed off as the file;
* a secret-looking name is never opened, checked again here (`secret_name`), though the walk never makes one a candidate;
* split into sections on its own headings (Markdown `#` and underlined titles, reStructuredText underlined titles), each
  section cut into overlapping word windows (`DocParser`'s sizes), and stored with the file's path, its folder, the
  section's heading, the file's modified time and the walk run as its evidence, and `status: proposal`,
  `stated_by: <path under the root> § <section>` (never the bare file name: three README.md files must not cite
  alike): nothing a file says becomes a fact here (the two-channel rule; a person confirms on Curate);
* the collection is one per file system, `fs_<slug>_narratives`, replaced whole on each run, so a file removed since the
  last run leaves nothing behind.

The price is shown before the press (`price`): the files and bytes the run would read, from the walk's own rows; "not
priced" until a walk has found narrative files. Absence is never zero (`view`): "not run yet", "no walk has looked for
narrative files", "the walk found no narrative file" are each their own words.

One run per file system at a time (a claim, `ProjectRegistry.take_claim`). Nothing is written into the path, and nothing
is published to Egeria: the index is Resource Explorer's own. The run's counts are a `step_runs` row (`STEP_KEY`).
"""
from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

log = logging.getLogger(__name__)

STEP_KEY = "narrative_docs"
#: The collection type: one collection per file system, `fs_<slug>_narratives`.
COLLECTION_TYPE = "narratives"
CLAIM_STALE_SECONDS = 900
ALREADY_RUNNING_WORDS = "narrative files of {slug} are already being read"
#: Per-file outcomes on the run row.
READ = "read"
TOO_LARGE = "too_large"
NOT_A_PLAIN_FILE = "not_a_plain_file"
SECRET_NAME = "secret_name"
EMPTY = "empty"

_ATX = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_UNDERLINE = re.compile(r"^\s*([=\-~^\"'`*+#])\1{2,}\s*$")


class NothingToRead(RuntimeError):
    """No walk has recorded narrative files for this file system, so there is nothing to read."""


class AlreadyRunning(RuntimeError):
    """Another run for this file system holds the claim."""


class NotReachable(RuntimeError):
    """The mount point cannot be reached from this machine."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def collection_for(store, slug: str) -> str:
    return store.collection_name(f"fs_{slug}", COLLECTION_TYPE)


def newest_narratives(registry, slug: str) -> tuple[str, list[dict]] | None:
    """(walk run id, the narrative entries that walk recorded), from the newest stored walk that looked for them; None when
    no walk has. A walk that looked and found none is `(run, [])`."""
    rows = [r for r in registry.list_filesystem_surveys(slug)
            if isinstance((r.get("survey_data") or {}).get("folder_narratives"), list)]
    if not rows:
        return None
    newest = max(rows, key=lambda r: ((r["survey_data"].get("walk_extensions") or {}).get("run_id")
                                      or r["survey_data"].get("surveyed_at") or r.get("surveyed_at") or ""))
    sd = newest["survey_data"]
    run = (sd.get("walk_extensions") or {}).get("run_id") or sd.get("surveyed_at") or newest.get("surveyed_at") or ""
    return run, list(sd["folder_narratives"])


def _sizes(registry, slug: str, run: str) -> dict[str, int]:
    try:
        return {r["file_path"]: int(r.get("size_bytes") or 0) for r in registry.list_filesystem_walk_files(slug, run)}
    except Exception:  # noqa: BLE001 -- the price then says bytes are not known, never 0
        log.warning("%s: walk rows for %s could not be read", slug, run, exc_info=True)
        return {}


def _files(entries: list[dict]) -> list[dict]:
    """One per file (the walk keeps one entry per narrative file), in path order."""
    seen: dict[str, dict] = {}
    for e in entries:
        if e.get("file") and e["file"] not in seen:
            seen[e["file"]] = e
    return [seen[k] for k in sorted(seen)]


def _bytes(n: int) -> str:
    from resource_explorer.surveyors.filesystem.walk_derived import _fmt_bytes as fmt

    return fmt(n)


def price(registry, slug: str) -> dict:
    """The price before the press: {priced, files, bytes, walk, words}. From the walk's own rows; never a guessed number."""
    found = newest_narratives(registry, slug)
    if found is None:
        return {"priced": False, "files": None, "bytes": None, "walk": "",
                "words": "not priced · no walk has looked for narrative files yet"}
    run, entries = found
    files = _files(entries)
    if not files:
        return {"priced": True, "files": 0, "bytes": 0, "walk": run,
                "words": f"nothing to read · the walk of {run[:10]} found no narrative file"}
    sizes = _sizes(registry, slug, run)
    known = [sizes[f["file"]] for f in files if f["file"] in sizes]
    total = sum(known) if len(known) == len(files) else None
    size_words = _bytes(total) if total is not None else "size not known for every file"
    return {"priced": True, "files": len(files), "bytes": total, "walk": run,
            "words": (f"reads {len(files):,} narrative file{'s' if len(files) != 1 else ''} in full ({size_words}), "
                      f"each under {_bytes(_max_bytes())} · found by the walk of {run[:10]} · opens no other file")}


def _max_bytes() -> int:
    from resource_explorer.surveyors.filesystem.walk_extensions import NARRATIVE_MAX_FILE_BYTES

    return NARRATIVE_MAX_FILE_BYTES


def split_sections(text: str, file_name: str = "") -> list[tuple[str, str]]:
    """[(heading, body)] in order, split on the text's own headings: Markdown `#` headings, and a title line underlined
    with a repeated punctuation character (Markdown setext, reStructuredText). Text before the first heading is the
    opening, with heading ''. A file with no heading is one section. Pure."""
    lines = text.splitlines()
    sections: list[tuple[str, list[str]]] = [("", [])]
    i = 0
    while i < len(lines):
        line = lines[i]
        atx = _ATX.match(line)
        if atx:
            sections.append((atx.group(2).strip(), []))
            i += 1
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if line.strip() and _UNDERLINE.match(nxt) and len(nxt.strip()) >= min(3, len(line.strip())):
            # an rst overline (a punctuation line ABOVE the title too) belongs to the title, not the previous section
            prev = sections[-1][1]
            if prev and _UNDERLINE.match(prev[-1]):
                prev.pop()
            sections.append((line.strip(), []))
            i += 2
            continue
        sections[-1][1].append(line)
        i += 1
    out = [(h, "\n".join(body).strip()) for h, body in sections]
    return [(h, b) for h, b in out if b or h]


PASSAGE_SEP = " § "


def stated_by_of(rel_path: str, section: str = "") -> str:
    """How a passage cites its source: the path relative to the file system's root, then ` § ` and the section
    heading (no heading, no suffix). The bare file name is only a display short form (`PurePosixPath(...).name`)."""
    return f"{rel_path}{PASSAGE_SEP}{section}" if section else rel_path


def passage_id(walk_run: str, rel_path: str, section: str = "") -> str:
    """The id of one cited passage: the walk run that found the file, its path under the root, its section."""
    return f"{walk_run}|{rel_path}|{section}"


def chunks_for(text: str, *, rel_path: str, modified_at: str, slug: str, run: str,
               parser=None) -> list[tuple[str, dict]]:
    """[(text, metadata)] for one file: each section cut into word windows, each window's text led by its heading."""
    from resource_explorer.ingestion.doc_parser import DocParser

    parser = parser or DocParser()
    name = PurePosixPath(rel_path).name
    folder = str(PurePosixPath(rel_path).parent)
    out: list[tuple[str, dict]] = []
    for s_index, (heading, body) in enumerate(split_sections(text, name)):
        windows = parser._fixed_window(body) if body else []
        for c_index, window in enumerate(windows):
            out.append((f"{heading}\n{window}" if heading else window, {
                "file_path": rel_path, "folder": "" if folder == "." else folder, "section": heading,
                "section_index": s_index, "chunk_index": c_index, "modified_at": modified_at or "",
                "stated_by": stated_by_of(rel_path, heading), "status": "proposal", "source": "narrative_file", "type": "narrative",
                "resource_type": "filesystem", "project_slug": slug, "walk_run": run,
            }))
    return out


def default_store():
    from resource_explorer.vector_store_pg import MultiCollectionStore

    return MultiCollectionStore()


def run_narrative_docs(fs_entity, registry, *, store_factory: Callable[[], object] | None = None) -> dict:
    """The press: read the newest walk's narrative files in full, chunk them by section and replace this file system's
    collection with them. Raises NothingToRead (no walk has looked, or it found none), NotReachable, AlreadyRunning."""
    from resource_explorer.surveyors.filesystem.file_profiles import machine
    from resource_explorer.surveyors.filesystem.walk_extensions import looks_secret, read_plain_file

    slug = fs_entity.slug
    found = newest_narratives(registry, slug)
    if found is None:
        raise NothingToRead("No walk has looked for narrative files yet. Run the survey first.")
    run, entries = found
    files = _files(entries)
    if not files:
        raise NothingToRead(f"The walk of {run[:10]} found no narrative file, so there is nothing to read.")
    root = Path(fs_entity.local_mount_point)
    if not root.is_dir():
        raise NotReachable(f"The mount point {fs_entity.local_mount_point} cannot be reached from here · nothing read.")
    claim_key = f"{STEP_KEY}:{registry._normalize_slug(slug)}"
    holder = f"{machine()}:{os.getpid()}:{_now()}"
    if not registry.take_claim(claim_key, holder, stale_after_seconds=CLAIM_STALE_SECONDS):
        raise AlreadyRunning(ALREADY_RUNNING_WORDS.format(slug=slug))
    try:
        limit = _max_bytes()
        outcomes: dict[str, int] = {READ: 0, TOO_LARGE: 0, NOT_A_PLAIN_FILE: 0, SECRET_NAME: 0, EMPTY: 0}
        per_file: list[dict] = []
        chunks: list[tuple[str, dict]] = []
        sections = 0
        for e in files:
            rel = e["file"]
            name = PurePosixPath(rel).name
            if looks_secret(name):
                outcome, n_chunks = SECRET_NAME, 0
            else:
                raw = read_plain_file(root / rel, limit, whole=True)
                if raw is None:
                    # grown past the limit since the walk, or not a plain file now (a link or FIFO swapped in): not read
                    try:
                        big = (root / rel).is_file() and not (root / rel).is_symlink() and (root / rel).stat().st_size > limit
                    except OSError:
                        big = False
                    outcome, n_chunks = (TOO_LARGE if big else NOT_A_PLAIN_FILE), 0
                else:
                    text = raw.decode("utf-8", errors="replace")
                    mine = chunks_for(text, rel_path=rel, modified_at=e.get("modified_at") or "", slug=slug, run=run)
                    sections += len({m["section_index"] for _, m in mine})
                    chunks.extend(mine)
                    outcome, n_chunks = (READ if mine else EMPTY), len(mine)
            outcomes[outcome] += 1
            per_file.append({"file_path": rel, "outcome": outcome, "chunks": n_chunks})
        store = (store_factory or default_store)()
        collection = collection_for(store, slug)
        if store.collection_exists(collection):
            store.drop_collection(collection)
        stored = store.insert(collection, [t for t, _ in chunks], [m for _, m in chunks]) if chunks else 0
        summary = {"walk_run": run, "ran_at": _now(), "collection": collection, "files": len(files),
                   "outcomes": outcomes, "sections": sections, "chunks": len(chunks), "stored": int(stored or 0),
                   "files_detail": per_file[:200], "files_not_shown": max(0, len(per_file) - 200),
                   "max_file_bytes": limit, "machine": machine()}
        registry.record_step_run(slug, STEP_KEY, summary["ran_at"], entity_type="filesystem",
                                 source="resource-explorer", executor="local", metrics=summary)
        return summary
    finally:
        registry.release_claim(claim_key, holder)


def _plural(n: int, one: str, many: str = "") -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def run_words(m: dict) -> str:
    """The stored run in words. Every number from the run row."""
    o = m.get("outcomes") or {}
    parts = [f"{_plural(o.get(READ, 0), 'file')} read in full · {_plural(m.get('sections', 0), 'section')} · "
             f"{_plural(m.get('stored', 0), 'passage')} in Resource Explorer's search index"]
    for key, words in ((TOO_LARGE, "grown past the size limit since the walk · not read"),
                       (NOT_A_PLAIN_FILE, "no longer a plain file · not read"),
                       (SECRET_NAME, "with a secret-looking name · not opened"), (EMPTY, "with no text")):
        if o.get(key):
            parts.append(f"{_plural(o[key], 'file')} {words}")
    return " · ".join(parts)


def view(registry, slug: str) -> dict:
    """{price, run, words}: the price before the press and the newest stored run (None when never run)."""
    rows = registry.query_step_runs(slug=slug, step_key=STEP_KEY, entity_type="filesystem", limit=1)
    m = (rows[0].get("metrics") or {}) if rows else {}
    run = None
    if m:
        newest = newest_narratives(registry, slug)
        run = {**m, "words": run_words(m),
               "stale": bool(newest and newest[0] != m.get("walk_run")),
               "stated": "each passage is stated by its file · a proposal, never a fact, until a person confirms it"}
    return {"price": price(registry, slug), "run": run,
            "words": "" if run else "not read yet",
            "kept": "kept in Resource Explorer's search index · not published to Egeria"}
