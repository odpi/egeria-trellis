"""Carry chat votes on chunks across a re-insert of their files (BRIEF-REINDEX-FROM-SCRATCH.md §5, R0).

A vote is stored against `chunk_ref = "<collection>:<id>"` (`chunk_feedback`, closed-loop step 0). When a file's chunks
are deleted and inserted again (a refresh's per-file replace, a collection rebuilt in full), a chunk can come back under
a different id: its text moved within the file, or it was written before R0's per-file ids. Without this, its vote would
point at nothing and silently stop affecting retrieval.

How:
1. **Before** the delete, `snapshot` reads the voted chunks of the collection (optionally only those in the files about
   to be replaced): each ref with its text and file.
2. The re-insert runs inside `vector_store_pg.record_inserts()`, which records every inserted (id, text, file).
3. **After**, `carry` matches each snapshot entry to an inserted chunk with **identical text**, narrowed to the **same
   file** when several match (the architect's read-back of 463c555a):
   - the same id came back: nothing to do;
   - one match: the vote moves to the new ref (`MetricsCollector.move_chunk_votes`);
   - several identical candidates in the same file: **ambiguous**, left where it is;
   - no identical text: **orphaned**, left where it is.
   An ambiguous or orphaned vote is never guessed onto another chunk and never deleted: it stays in `chunk_feedback`,
   affects no retrieval (no chunk has its ref), and is reported.

The result records every move (`moved`), so R1's undo can move them back. A carry that fails is logged and reported; it
never fails the refresh or ingestion around it.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Voted:
    ref: str
    collection: str
    chunk_id: str
    text: str
    file: str


@dataclass
class Snapshot:
    """What `snapshot` read: the voted chunks to carry, and the voted refs that already point at nothing (their row is
    gone, or their id cannot be one) before this re-insert, which the report names rather than leaving out (the
    architect's read-back of 7af6ea51: absence is an answer). True when either is non-empty."""
    collection: str
    voted: list[Voted] = field(default_factory=list)
    already_orphaned: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.voted or self.already_orphaned)


@dataclass
class CarryReport:
    collection: str
    #: old ref -> new ref, for every vote moved.
    moved: dict[str, str] = field(default_factory=dict)
    #: refs whose chunk came back under the same id.
    kept: list[str] = field(default_factory=list)
    #: old ref -> how many identical candidates (in the same file) there were.
    ambiguous: dict[str, int] = field(default_factory=dict)
    #: refs whose text is no longer in what was inserted.
    orphaned: list[str] = field(default_factory=list)
    #: refs that already pointed at nothing before this re-insert (`Snapshot.already_orphaned`).
    already_orphaned: list[str] = field(default_factory=list)
    error: str = ""

    def words(self) -> str:
        if self.error:
            return f"votes not carried ({self.error})"
        parts = [f"{len(self.moved)} moved", f"{len(self.kept)} kept"]
        if self.ambiguous:
            parts.append(f"{len(self.ambiguous)} ambiguous")
        if self.orphaned:
            parts.append(f"{len(self.orphaned)} orphaned")
        if self.already_orphaned:
            parts.append(f"{len(self.already_orphaned)} already orphaned before this re-insert")
        return "votes: " + ", ".join(parts)


def _metrics():
    from resource_explorer.vector_store_pg import _metrics_collector
    return _metrics_collector()


def snapshot(store: Any, collection: str, files: Iterable[str] | None = None, *, metrics: Any = None,
             rows_from: str | None = None) -> Snapshot:
    """The voted chunks of `collection`, read before their rows are replaced. `files`: only chunks from these files
    (a per-file refresh); None for the whole collection (a full rebuild). A voted ref whose row is already gone, or
    whose id cannot be one, is listed as already orphaned. Never raises: a snapshot that cannot be read is empty, and
    logged, so the replace still runs."""
    snap = Snapshot(collection=collection)
    try:
        metrics = metrics or _metrics()
        refs = metrics.chunk_vote_refs(collection)
        if not refs:
            return snap
        ids = {r.split(":", 1)[1]: r for r in refs if ":" in r}
        wanted = set(files) if files is not None else None
        found: set[str] = set()
        # rows_from: read the voted rows from another table than the one their refs name (a rebuild's recovery reads
        # them from `{collection}__previous`, where the swap moved them).
        for row in store.rows_by_ids(rows_from or collection, list(ids)):
            found.add(row["id"])
            meta = row.get("metadata") or {}
            file = str(meta.get("file_path") or meta.get("source_url") or "")
            if wanted is not None and file not in wanted:
                continue
            snap.voted.append(Voted(ref=ids[row["id"]], collection=collection, chunk_id=row["id"],
                                    text=row.get("text") or "", file=file))
        snap.already_orphaned = sorted(ref for chunk_id, ref in ids.items() if chunk_id not in found)
        return snap
    except Exception as exc:  # noqa: BLE001 -- the replace must run; the carry reports what it could not do
        logger.warning("chunk votes for %s could not be read before re-insert: %s", collection, exc)
        return snap


def carry(snap: Snapshot, inserted: list[tuple[str, str, str, str]], *, metrics: Any = None) -> CarryReport:
    """Match each voted chunk to what was inserted, and move its votes. `inserted` is `record_inserts()`'s list of
    (collection, id, text, file)."""
    collection = snap.collection
    voted = snap.voted
    report = CarryReport(collection=collection, already_orphaned=list(snap.already_orphaned))
    if not voted:
        return report
    try:
        metrics = metrics or _metrics()
        mine = [r for r in inserted if r[0] == collection]
        by_text: dict[str, list[tuple[str, str]]] = {}
        new_ids = set()
        for _, chunk_id, text, file in mine:
            by_text.setdefault(text, []).append((chunk_id, file))
            new_ids.add(chunk_id)
        for v in voted:
            if v.chunk_id in new_ids:
                report.kept.append(v.ref)
                continue
            candidates = by_text.get(v.text, [])
            same_file = [c for c in candidates if c[1] == v.file]
            pool = same_file or candidates
            if not pool:
                report.orphaned.append(v.ref)
            elif len(pool) > 1:
                report.ambiguous[v.ref] = len(pool)
            else:
                new_ref = f"{collection}:{pool[0][0]}"
                if metrics.move_chunk_votes(v.ref, new_ref):
                    report.moved[v.ref] = new_ref
    except Exception as exc:  # noqa: BLE001 -- the ingestion has happened; say what the carry could not do
        logger.warning("chunk votes for %s could not be carried: %s", collection, exc)
        report.error = f"{type(exc).__name__}: {exc}"[:200]
    if report.moved or report.ambiguous or report.orphaned or report.already_orphaned or report.error:
        logger.info("chunk votes for %s after re-insert: %s", collection, report.words())
    return report
