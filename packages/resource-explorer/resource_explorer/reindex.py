"""Rebuild a repository's chat index from scratch, alongside the live one, then swap (BRIEF-REINDEX-FROM-SCRATCH.md, R1b).

The rules, all decided by the project owner or required by the architect's read-backs (2026-11-10):

* **Asked first, admins only, one at a time.** A run-queue row of kind `reindex` with the requester recorded. Refused
  while the repository has a first index or rebuild queued or running, while any other rebuild runs, or while an
  earlier rebuild's previous index is still kept (its names would collide).
* **Build aside.** Each collection type the analyzer proposes TODAY (Q1, re-plan) is ingested into
  `{slug}_{type}__rebuild`, with chunk ids minted under the live name (`vector_store_pg.insert(id_collection=)`), from
  one download at a recorded commit. Chat keeps reading the live index.
* **Check.** Old and new counts per type; a type that had chunks and is still proposed but now has none is refused;
  each built table must own its vector index. One exception: a web_docs whose every HTML file is site scaffolding
  (site_templates.py) is a planned drop, listed as dropped with a note.
* **The first rebuild ever is held** for an admin to accept (Q7), and timed; later ones go straight on.
* **Survey writes after the check, before the swap** (Q6, the architect's Q6 points): the pipeline's other writers, from
  a download of the same commit. Not undone by an undo. Each is listed in the result.
* **The swap, recoverably** (C3 as accepted): projects.collections is PRUNED of dropped types before the swap, the
  tables and their indexes are swapped in one transaction (`swap_collections`), then the list is EXTENDED with added
  types. A reader never sees a list entry for a missing table. The query cache is invalidated after each list change.
  The phase is written on the run row before and after each step, so `recover` can finish forward or roll back.
* **Votes** are snapshotted before the swap and carried after it (`chunk_votes`); every move is kept for undo.
* **The previous index is kept 7 days** (Q4) under `{name}__previous`; undo restores it (and its votes) in the mirror
  order; a later job drops it (R1c).
* **No project-status writes.** A failed rebuild is recorded on its run row, never as the project's ERROR, since its live
  index still serves.
"""
from __future__ import annotations

import json
import logging
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from resource_explorer.index_state import has_own_index, own_collections, shared_collections

log = logging.getLogger(__name__)

RUN_KIND = "reindex"
ASIDE, PREVIOUS = "__rebuild", "__previous"
KEEP_PREVIOUS_DAYS = 7
ACTIVE_STATES = ("queued", "claimed", "running")

#: The steps, in order. A run's target carries `phase` and `outcomes` (each step's words). An -ing phase is an INTENT,
#: recorded before its effect; the past-tense one after it (the architect's B1). Undo's phases mirror the swap's.
PHASES = ("queued", "building", "built", "awaiting_accept", "surveyed", "pruning", "list_pruned", "swapping",
          "swapped", "extending", "list_extended", "done", "rolled_back", "failed", "cancelled",
          "undo_pruning", "undo_swapping", "undo_swapped", "undo_extending")
#: Phases at which nothing live has changed: a dead run is rolled back by dropping its aside tables.
BEFORE_LIVE = ("queued", "building", "built", "awaiting_accept", "surveyed")

#: The pipeline's other writers a rebuild re-runs (Q6), in the order a first index runs them.
SURVEY_WRITERS = ("_record_line_census", "_store_file_inventory", "_parse_dependencies", "_profile_data_files",
                  "_read_dataset_descriptors", "_parse_ci_workflows", "_parse_supply_chain", "_parse_repo_conventions")

UNDO_NOT_REVERTED = "survey results updated (not reverted by undo)"


class ReindexRefused(Exception):
    """A rebuild, accept or undo that cannot be done, with the sentence to show. `status` is the HTTP status."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def aside(name: str) -> str:
    return name + ASIDE


def previous(name: str) -> str:
    return name + PREVIOUS


def _now() -> datetime:
    return datetime.now(UTC)


def _target(row: dict) -> dict:
    t = row.get("target")
    if isinstance(t, dict):
        return t
    try:
        return json.loads(t or "{}")
    except (TypeError, ValueError):
        return {}


# ── who is doing what ─────────────────────────────────────────────────────────────────────────────────────────────

def active_run_for(registry, slug: str) -> dict | None:
    """The rebuild of `slug` that is queued or running, if any. A held run (awaiting accept) is not active: it has
    finished building and changes nothing until it is accepted."""
    for state in ACTIVE_STATES:
        for r in registry.list_runs(state=state, kind=RUN_KIND, limit=200):
            if _target(r).get("slug") == slug:
                return r
    return None


def any_active(registry) -> dict | None:
    for state in ACTIVE_STATES:
        rows = registry.list_runs(state=state, kind=RUN_KIND, limit=1)
        if rows:
            return rows[0]
    return None


def is_rebuilding(registry, slug: str) -> bool:
    """True while `slug` has a rebuild queued or running, or one held for accept. Refresh, first index and the
    scheduler's survey steps for `slug` are refused while it is (the architect's Q6 point 4)."""
    return active_run_for(registry, slug) is not None or held_run_for(registry, slug) is not None


def held_run_for(registry, slug: str) -> dict | None:
    for r in registry.list_runs(kind=RUN_KIND, limit=200):
        t = _target(r)
        if t.get("slug") == slug and t.get("phase") == "awaiting_accept" and not t.get("accepted"):
            return r
    return None


def latest_done(registry, slug: str) -> dict | None:
    """The last finished rebuild of `slug` whose previous index is still kept (undo window open, not undone)."""
    for r in registry.list_runs(kind=RUN_KIND, limit=200):
        t = _target(r)
        if t.get("slug") == slug and t.get("phase") == "done" and not t.get("undone") and not t.get("discarded"):
            return r
    return None


def ever_completed(registry) -> bool:
    return any(_target(r).get("phase") == "done" for r in registry.list_runs(kind=RUN_KIND, limit=500))


# ── queue ────────────────────────────────────────────────────────────────────────────────────────────────────────

def _checked(registry, slug: str, store) -> tuple[Any, list[str], int | None]:
    """The checks a rebuild must pass, shared by the ask and the queue. Returns (project, its own collections, the
    measured bytes they take, or None when unmeasured); raises ReindexRefused with the sentence otherwise."""
    p = registry.get(slug)
    if p is None:
        raise ReindexRefused(f"Project '{slug}' not found.", 404)
    name = p.display_name or slug
    if not has_own_index(p.slug, p.collections):
        raise ReindexRefused(f"{name} has no index of its own to rebuild; give it a first index instead.")
    if active_run_for(registry, slug) or held_run_for(registry, slug):
        raise ReindexRefused(f"{name} already has a rebuild queued, running, or waiting to be accepted.")
    other = any_active(registry)
    if other is not None:
        raise ReindexRefused(f"Another rebuild ({_target(other).get('slug', '?')}) is running; one at a time.")
    from resource_explorer.first_index import active_run_for as first_index_active
    if first_index_active(registry, slug) is not None:
        raise ReindexRefused(f"{name} is waiting for, or being given, a first index.")
    kept = latest_done(registry, slug)
    if kept is not None:
        until = _target(kept).get("previous_until", "")
        raise ReindexRefused(f"{name}'s previous index from its last rebuild is kept until {until[:10]} for undo; "
                             "undo it or discard it before rebuilding again.")
    mine = own_collections(slug, p.collections)
    problems = store.check_names(*[n for c in mine for n in (aside(c), previous(c))])
    if problems:
        raise ReindexRefused("; ".join(problems), 400)
    # Measured, not estimated: Postgres cannot report its filesystem's free space (the architect's read-back), so the
    # ask states the space a rebuild needs again until its previous index is dropped.
    try:
        needs = sum(store.table_bytes(c) for c in mine)
    except Exception:  # noqa: BLE001 -- an unmeasured size is said to be unknown, not zero
        needs = None
    return p, mine, needs


def ask(registry, slug: str, *, store=None) -> dict:
    """What the ask shows before an admin says yes (BRIEF-REINDEX-FROM-SCRATCH.md §6): the collections and their
    chunk counts, the measured space, whether this is the held first rebuild, and what an undo does not revert. When a
    rebuild would be refused, `refused` carries the sentence and nothing is offered."""
    store = store or _store()
    try:
        p, mine, needs = _checked(registry, slug, store)
    except ReindexRefused as exc:
        return {"slug": slug, "refused": str(exc)}
    counts = []
    for c in mine:
        try:
            counts.append({"collection": c, "chunks": store.count(c) if store.collection_exists(c) else 0})
        except Exception:  # noqa: BLE001 -- a count that cannot be read is said to be unknown
            counts.append({"collection": c, "chunks": None})
    return {"slug": slug, "name": p.display_name or slug, "collections": counts, "needs_bytes": needs,
            "needs_words": needs_words(needs), "first": not ever_completed(registry),
            "keep_days": KEEP_PREVIOUS_DAYS, "survey_words": UNDO_NOT_REVERTED, "refused": ""}


def queue(registry, slug: str, *, requested_by: str, store=None) -> dict:
    """Queue one admin's yes to rebuild `slug`. Returns {run_id, held}; raises ReindexRefused otherwise."""
    if not (requested_by or "").strip():
        raise ReindexRefused("A rebuild needs a signed-in admin's yes; none is recorded for this request.", 401)
    store = store or _store()
    p, _, needs = _checked(registry, slug, store)
    name = p.display_name or slug
    held = not ever_completed(registry)
    from resource_explorer.activity_logger import log_reindex

    activity_id = log_reindex(registry, slug, name, "running", f"Rebuild of {name}'s chat index: queued by "
                              f"{requested_by}" + (" (the first rebuild: held for accept before it swaps)." if held
                                                   else "."))
    target = {"slug": slug, "phase": "queued", "held": held, "asked_by": requested_by, "outcomes": [],
              "needs_bytes": needs}
    run_id = registry.enqueue_run(RUN_KIND, target, requested_by=requested_by, result_ref=activity_id)
    return {"run_id": run_id, "held": held, "needs_bytes": needs, "needs_words": needs_words(needs)}


def needs_words(needs: int | None) -> str:
    if needs is None:
        return "the space it needs could not be measured"
    return (f"needs about {needs / 1_048_576:,.0f} MB more in the database (the current index's size, measured) "
            f"until its previous index is dropped, {KEEP_PREVIOUS_DAYS} days after the swap")


# ── the run ──────────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class Deps:
    """The collaborators, so a test can hand in fakes bound to the real signatures."""
    registry: Any
    store: Any = None
    analyzer: Any = None
    pipeline: Any = None
    client: Any = None
    query_cache: Any = None

    def __post_init__(self):
        if self.store is None:
            self.store = _store()
        if self.analyzer is None:
            from resource_explorer.github.analyzer import RepoAnalyzer
            self.analyzer = RepoAnalyzer()
        if self.pipeline is None:
            from resource_explorer.ingestion.pipeline import IngestionPipeline
            self.pipeline = IngestionPipeline()
        if self.client is None:
            from resource_explorer.github.client import GitHubClient
            self.client = GitHubClient()
        if self.query_cache is None:
            from resource_explorer.query_cache import QueryCache
            self.query_cache = QueryCache()


def _store():
    from resource_explorer.vector_store_pg import MultiCollectionStore
    return MultiCollectionStore()


class _Run:
    """One rebuild's state, written to its run row as it moves."""

    def __init__(self, run_id: str, deps: Deps):
        self.id, self.d = run_id, deps
        self.t = deps.registry.merge_run_target(run_id, {})

    def step(self, phase: str, words: str = "", **data) -> None:
        patch = {"phase": phase, **data}
        if words:
            patch["outcomes"] = list(self.t.get("outcomes") or []) + [{"phase": phase, "words": words,
                                                                        "at": _now().isoformat()}]
        self.t = self.d.registry.merge_run_target(self.id, patch)


def run(run_id: str, deps: Deps) -> tuple[str, str]:
    """Execute a `reindex` run: build, check, and either hold (the first ever) or go on to survey and swap. Returns
    (state, error) for the run queue. Never writes the project's status."""
    r = _Run(run_id, deps)
    slug = r.t["slug"]
    # queue() checks, then enqueues: two admins at once could both pass. The claim is the second check.
    others = [x for x in deps.registry.list_runs(state="running", kind=RUN_KIND, limit=50) if x["id"] != run_id]
    if others:
        msg = f"another rebuild ({_target(others[0]).get('slug', '?')}) is running; one at a time"
        r.step("failed", f"refused when claimed: {msg}")
        return "failed", msg
    if r.t.get("continue_of"):
        return _continue_accepted(r)
    try:
        _build(r)
    except Exception as exc:  # the run row says what failed; the live index is untouched
        log.exception("rebuild of %s failed while building", slug)
        _drop_aside(r)
        why = _why(exc)
        r.step("failed", f"the build failed, and the live index is unchanged: {why}",
               built_seconds=_seconds_since(r.t.get("build_started_at")))
        return "failed", why[:500]
    if r.t.get("refused"):
        _drop_aside(r)
        r.step("failed", f"the check refused the swap: {r.t['refused']}")
        return "failed", r.t["refused"]
    if r.t.get("held"):
        r.step("awaiting_accept", "held for an admin to accept: the first rebuild is checked by a person before it "
                                  "swaps")
        return "succeeded", ""
    return _finish(r)


def _seconds_since(iso: str | None) -> float | None:
    """Wall-clock seconds since `iso`, for a build that failed before it recorded its own time."""
    if not iso:
        return None
    try:
        return round((_now() - datetime.fromisoformat(iso)).total_seconds(), 1)
    except ValueError:
        return None


def _why(exc: BaseException) -> str:
    """The words for a failure. Postgres running out of disk (SQLSTATE 53100) is named as such: the aside tables are
    what filled it, and they are dropped."""
    if getattr(exc, "pgcode", "") == "53100" or "disk full" in str(exc).lower() or "No space left" in str(exc):
        return ("the database ran out of disk while building; the aside tables were dropped and the live index is "
                f"unchanged ({type(exc).__name__})")
    return str(exc)


def _build(r: _Run) -> None:
    d, slug = r.d, r.t["slug"]
    p = d.registry.get(slug)
    started = time.monotonic()
    r.step("building", "building aside from a fresh download; chat keeps the live index",
           build_started_at=_now().isoformat())
    plan = d.analyzer.analyze(p.github_url, subpath=p.subproject_path or None)
    live = own_collections(slug, p.collections)
    proposed = {f"{slug}_{ct.name}": ct for ct in plan.proposed_collections}
    problems = d.store.check_names(*[n for c in set(live) | set(proposed) for n in (aside(c), previous(c), c)])
    if problems:
        raise ValueError("; ".join(problems))
    repo = d.client.get_repo(p.github_url)
    sha = d.client.get_latest_commit_sha(repo)
    counts: dict[str, dict] = {}
    extra_docs = list(p.extra_docs_paths or [])
    doc_types = getattr(d.pipeline, "_DOC_CTYPES", frozenset())
    with tempfile.TemporaryDirectory() as tmp:
        extra: list[tuple[str, Path]] = []
        if p.subproject_path and extra_docs:
            # As a first index and a refresh do: the whole repository, so the extra docs paths outside the subproject
            # are ingested into the doc collections too.
            full = d.client.download_zipball(repo, Path(tmp), None, ref=sha)
            root = full / p.subproject_path
            extra = [(path, full / path) for path in extra_docs]
        else:
            root = d.client.download_zipball(repo, Path(tmp), p.subproject_path or None, ref=sha)
        for name, ct in proposed.items():
            if d.store.collection_exists(aside(name)):
                d.store.drop_collection(aside(name))
            kw = {"extra_paths": extra} if extra and ct.name in doc_types else {}
            if ct.name == "pdfs":
                d.pipeline.pdf_failures = []
            if ct.name == "web_docs":
                d.pipeline.last_web_docs_scaffolding = None
            new = d.pipeline._ingest_collection(repo, slug, aside(name), ct,
                                                root if ct.name != "release_notes" else None, id_collection=name,
                                                **kw)
            old = d.store.count(name) if name in live and d.store.collection_exists(name) else 0
            counts[name] = {"old": old, "new": int(new or 0)}
            if ct.name == "pdfs":
                counts[name].update(_pdf_files(d, name, live))
            if ct.name == "web_docs" and not new:
                counts[name].update(_web_docs_scaffolding(d))
    for name in live:
        if name not in proposed:
            counts[name] = {"old": d.store.count(name) if d.store.collection_exists(name) else 0, "new": None}
    # web_docs whose every HTML file is site-generator scaffolding (template folders, redirect stubs) is a PLANNED
    # drop, not a regression: the old chunks were the scaffolding (egeria_docs, owner 2026-11-11, option A).
    scaffolding = sorted(n for n, c in counts.items() if c.get("scaffolding_only"))
    for n in scaffolding:
        if d.store.collection_exists(aside(n)):
            d.store.drop_collection(aside(n))
    refused = [f"{n} had {c['old']} chunk(s) and now has none" for n, c in counts.items()
               if c["new"] == 0 and c["old"] > 0 and not c.get("scaffolding_only")]
    # A PDF that HAS live chunks and now yields no text would replace them with none (laz 2026-11-10: Docling
    # returned empty documents without raising): that is a regression, and the rebuild refuses rather than swap a
    # smaller index in quietly. A PDF that had no live chunks either (image-only, corrupt) loses nothing, so it is a
    # note, not a refusal: otherwise one such file would block every rebuild of its repository (Architect read-back
    # of #1055, 2026-11-11). So is a file whose chunks were all dropped as copies of another file's.
    notes: list[str] = []
    for n, c in counts.items():
        if c.get("failed_files"):
            refused.append(f"{n}: {len(c['failed_files'])} PDF(s) with live chunks now yield no text "
                           f"({_shown(c['failed_files'])})")
        if c.get("no_text_before"):
            notes.append(f"{n}: {len(c['no_text_before'])} PDF(s) gave no text, as before "
                         f"({_shown(c['no_text_before'])})")
        if c.get("duplicates"):
            notes.append(f"{n}: {len(c['duplicates'])} PDF(s) left with no chunks, all duplicates "
                         f"({_shown(c['duplicates'])})")
        if c.get("scaffolding_only"):
            notes.append(f"{n}: every HTML file ({c['scaffolding_only']}) is site scaffolding "
                         f"({_shown(c.get('scaffolding_files') or [])}); collection dropped")
    unindexed = [n for n, c in counts.items() if c["new"] and not d.store.has_vector_index(aside(n))]
    if unindexed:
        refused.append("these rebuilt tables have no vector index: " + ", ".join(unindexed))
    added = sorted(n for n, c in counts.items() if c["new"] and n not in live)
    dropped = sorted(n for n in live if n not in proposed or n in scaffolding)
    kept = sorted(n for n, c in counts.items() if c["new"] and n in live)
    words = "; ".join(f"{n}: {c['old']} → {c['new'] if c['new'] is not None and n not in scaffolding else 'dropped'}"
                      for n, c in sorted(counts.items()))
    if notes:
        words += "; note: " + "; ".join(notes)
    r.step("built", f"built: {words}", sha=sha, counts=counts, added=added, dropped=dropped, kept=kept,
           built_seconds=round(time.monotonic() - started, 1), refused="; ".join(refused), notes=notes)


def _web_docs_scaffolding(d: Deps) -> dict:
    """For a web_docs that came back empty: `scaffolding_only` (the number of HTML files) when every one of them was
    site scaffolding, so the empty result is the plan and not a regression; nothing otherwise."""
    sc = getattr(d.pipeline, "last_web_docs_scaffolding", None) or {}
    skipped = sc.get("skipped") or {}
    files = [f"{path} ({reason})" for reason, paths in sorted(skipped.items()) for path in paths]
    if sc.get("candidates") and len(files) == sc["candidates"]:
        return {"scaffolding_only": sc["candidates"], "scaffolding_files": files}
    return {}


def _shown(items: list[str], n: int = 3) -> str:
    return "; ".join(items[:n]) + (f" and {len(items) - n} more" if len(items) > n else "")


def _live_chunks_of(file: str, live_files: dict[str, int]) -> int:
    """A file's chunks in the live index. Rows written before PDFs stored their repository-relative path carry the
    absolute path of that run's download folder, which ends with the relative one."""
    return sum(n for k, n in live_files.items() if k == file or k.endswith("/" + file))


def _pdf_files(d: Deps, name: str, live: list[str]) -> dict:
    """Per file, for a rebuilt PDF collection: which no-text files lose live chunks (`failed_files`, refused), which
    never had any (`no_text_before`, a note), and which were left with none because every chunk was a copy of another
    file's (`duplicates`, a note)."""
    live_files = (d.store.count_by_metadata(name, "file_path")
                  if name in live and d.store.collection_exists(name) else {})
    out: dict[str, list[str]] = {}
    for f, why in getattr(d.pipeline, "pdf_failures", None) or []:
        had = _live_chunks_of(f, live_files)
        if had:
            out.setdefault("failed_files", []).append(f"{f}: {why} (had {had} live chunk(s))")
        else:
            out.setdefault("no_text_before", []).append(f"{f}: {why}")
    kept = getattr(d.pipeline, "last_kept_per_file", None) or {}
    for f, by in sorted((getattr(d.pipeline, "last_duplicate_of", None) or {}).items()):
        if not kept.get(f):
            copies = ", ".join(f"{n} of {other}" for other, n in sorted(by.items()))
            out.setdefault("duplicates", []).append(f"{f}: dropped as duplicates ({copies})")
    return out


def accept(registry, run_id: str, *, requested_by: str) -> dict:
    """An admin accepts a held first rebuild: a continuation run does the survey writes and the swap."""
    if not (requested_by or "").strip():
        raise ReindexRefused("Accepting needs a signed-in admin.", 401)
    holder = f"accept:{requested_by}:{uuid.uuid4().hex[:8]}"
    if not registry.take_claim(_decision_key(run_id), holder):
        raise ReindexRefused("That rebuild is being accepted or cancelled right now.")
    try:
        return _accept_claimed(registry, run_id, requested_by)
    finally:
        registry.release_claim(_decision_key(run_id), holder)


def _decision_key(run_id: str) -> str:
    """One claim per held run for its accept-or-cancel (the architect's C-1 on 6a57117e): both are read-then-write, so
    without it they could interleave, and an accepted continuation could run its survey writes for a cancelled run."""
    return f"reindex-decision:{run_id}"


def _accept_claimed(registry, run_id: str, requested_by: str) -> dict:
    row = registry.get_run(run_id)
    if row is None or row.get("kind") != RUN_KIND:
        raise ReindexRefused(f"No rebuild {run_id!r}.", 404)
    t = _target(row)
    if t.get("phase") == "cancelled":
        raise ReindexRefused("That rebuild was already cancelled.")
    if t.get("phase") != "awaiting_accept" or t.get("accepted"):
        raise ReindexRefused("That rebuild is not waiting to be accepted.")
    registry.merge_run_target(run_id, {"accepted": requested_by, "accepted_at": _now().isoformat()})
    cont = {**{k: t[k] for k in ("slug", "sha", "counts", "added", "dropped", "kept") if k in t},
            "phase": "built", "held": False, "continue_of": run_id, "asked_by": requested_by, "outcomes": []}
    from resource_explorer.activity_logger import log_reindex

    slug = t.get("slug", "")
    activity_id = log_reindex(registry, slug, slug, "running",
                              f"Rebuild of {slug}'s chat index: accepted by {requested_by}; survey writes and swap "
                              "queued.")
    new_id = registry.enqueue_run(RUN_KIND, cont, requested_by=requested_by, result_ref=activity_id)
    return {"run_id": new_id}


def _original_cancelled(r: _Run) -> bool:
    """For a continuation: whether the held run it continues was cancelled. The decision claim makes this impossible
    between two callers of accept/cancel_held; the checks that read it are the second line (the architect's C-1)."""
    if not r.t.get("continue_of"):
        return False
    return _target(r.d.registry.get_run(r.t["continue_of"]) or {}).get("phase") == "cancelled"


def _continue_accepted(r: _Run) -> tuple[str, str]:
    if _original_cancelled(r):
        msg = "the held rebuild was cancelled before this accept ran; nothing was swapped"
        r.step("failed", msg)
        return "failed", msg
    return _finish(r)


def cancel_held(registry, run_id: str, *, by: str, deps: Deps | None = None) -> dict:
    """Cancel a rebuild held for accept: its aside tables are dropped and the repository is no longer rebuilding, so
    its refreshes, first index and a new rebuild are allowed again. The live index was never changed, so there is
    nothing to undo. The next rebuild is still the held first one: only a rebuild that reaches `done` ends that."""
    if not (by or "").strip():
        raise ReindexRefused("Cancelling needs a signed-in admin.", 401)
    holder = f"cancel:{by}:{uuid.uuid4().hex[:8]}"
    if not registry.take_claim(_decision_key(run_id), holder):
        raise ReindexRefused("That rebuild is being accepted or cancelled right now.")
    try:
        return _cancel_claimed(registry, run_id, by, deps)
    finally:
        registry.release_claim(_decision_key(run_id), holder)


def _cancel_claimed(registry, run_id: str, by: str, deps: Deps | None) -> dict:
    row = registry.get_run(run_id)
    if row is None or row.get("kind") != RUN_KIND:
        raise ReindexRefused(f"No rebuild {run_id!r}.", 404)
    t = _target(row)
    if t.get("accepted"):
        raise ReindexRefused("That rebuild was already accepted, so there is nothing to cancel.")
    if t.get("phase") != "awaiting_accept":
        raise ReindexRefused("That rebuild is not waiting to be accepted, so there is nothing to cancel.")
    deps = deps or Deps(registry=registry)
    r = _Run(run_id, deps)
    # The phase first: from here accept refuses, and a continuation an accept already queued fails on its first step.
    r.step("cancelled", f"cancelled by {by} before it swapped: the live index was never changed",
           cancelled=by, cancelled_at=_now().isoformat())
    _drop_aside(r)
    names = set((r.t.get("counts") or {}).keys())
    left = sorted(aside(n) for n in names if deps.store.collection_exists(aside(n)))
    words = ("the built index was dropped" if not left
             else f"could not drop {', '.join(left)}; the next rebuild of this repository drops them first")
    r.t = registry.merge_run_target(run_id, {"left_aside": left})
    from resource_explorer.activity_logger import log_reindex

    slug = t.get("slug", "")
    p = registry.get(slug)
    log_reindex(registry, slug, (p.display_name if p else "") or slug, "ok" if not left else "error",
                f"Rebuild of {slug}'s chat index: cancelled by {by} before it swapped; {words}. The live index was "
                "never changed.")
    return {"run_id": run_id, "left_aside": left}


def _finish(r: _Run) -> tuple[str, str]:
    """Survey writes, then the recoverable swap. Each step is recorded before the next starts."""
    try:
        _survey(r)
    except Exception as exc:  # nothing live changed yet
        log.exception("rebuild of %s failed in its survey writes", r.t["slug"])
        _drop_aside(r)
        r.step("failed", f"the survey writes failed before the swap, so nothing was swapped: {exc}")
        return "failed", str(exc)[:500]
    if _original_cancelled(r):
        _drop_aside(r)
        msg = (f"the held rebuild was cancelled before its swap; nothing was swapped, and the survey writes it made "
               f"stay ({UNDO_NOT_REVERTED})")
        r.step("failed", msg)
        return "failed", msg
    try:
        _swap(r)
    except Exception as exc:  # recover_target decides by the tables
        log.exception("rebuild of %s failed during the swap", r.t["slug"])
        result = recover_target(r)
        return ("succeeded", "") if result == "finished forward" else ("failed", f"{_why(exc)} ({result})"[:500])
    return "succeeded", ""


def _survey(r: _Run) -> None:
    d, slug = r.d, r.t["slug"]
    p = d.registry.get(slug)
    repo = d.client.get_repo(p.github_url)
    done: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = d.client.download_zipball(repo, Path(tmp), p.subproject_path or None, ref=r.t.get("sha"))
        file_count, loc = d.pipeline._count_repo_stats(root)
        for writer in SURVEY_WRITERS:
            fn = getattr(d.pipeline, writer, None)
            if fn is None:
                continue
            try:
                fn(slug, root, repo=repo) if writer == "_store_file_inventory" else fn(slug, root)
                done.append({"writer": writer.lstrip("_"), "state": "done"})
            except Exception as exc:  # noqa: BLE001 -- listed, not fatal: the index is the rebuild's purpose
                done.append({"writer": writer.lstrip("_"), "state": "failed", "error": str(exc)[:200]})
    failed = [w["writer"] for w in done if w["state"] == "failed"]
    words = f"{UNDO_NOT_REVERTED}: " + ", ".join(w["writer"] for w in done if w["state"] == "done")
    if failed:
        words += f"; failed: {', '.join(failed)}"
    r.step("surveyed", words, survey=done, file_count=file_count, loc=loc)


def _set_list(r: _Run, collections: list[str]) -> None:
    """Set the project's collections list and invalidate its query cache. Idempotent: setting the same list twice is
    the same as once, so recovery can repeat a list step it cannot tell happened."""
    d, slug = r.d, r.t["slug"]
    d.registry.set_project_collections(slug, collections)
    d.query_cache.invalidate_project(slug)


def _final_list(r: _Run) -> list[str]:
    d, slug = r.d, r.t["slug"]
    p = d.registry.get(slug)
    shared = shared_collections(slug, p.collections if p else [])
    kept, added = r.t.get("kept") or [], r.t.get("added") or []
    return kept + added + [c for c in shared if c not in kept + added]


def _swap_committed(r: _Run) -> bool:
    """Whether the swap's transaction committed, read from the TABLES, never from the phase (the architect's B1): the
    phase is written after its effect can have happened. Committed: every live table that was rebuilt or dropped now
    exists as `__previous`, and no table that was built is still aside."""
    s = r.d.store
    kept, added, dropped = r.t.get("kept") or [], r.t.get("added") or [], r.t.get("dropped") or []
    renamed = [old for old, _ in (r.t.get("renames") or []) if not old.endswith(ASIDE)]
    return (all(s.collection_exists(previous(n)) for n in renamed or kept + dropped)
            and not any(s.collection_exists(aside(n)) for n in kept + added))


def _carry_votes(r: _Run, snaps: dict) -> None:
    """Carry each collection's votes to its new rows, recording the moves on the run row per collection AS THEY
    HAPPEN (B2), so a forward recovery carries only what is not yet moved and undo can move each one back."""
    from resource_explorer import chunk_votes

    d = r.d
    for n, snap in snaps.items():
        if not snap:
            continue
        rows = [(n, row["id"], row.get("text") or "", str((row.get("metadata") or {}).get("file_path") or
                 (row.get("metadata") or {}).get("source_url") or ""))
                for row in d.store.all_rows(n)]
        report = chunk_votes.carry(snap, rows)
        moved = {**(r.t.get("vote_moves") or {}), **report.moved}
        words = list(r.t.get("vote_words") or []) + [f"{n}: {report.words()}"]
        r.t = d.registry.merge_run_target(r.id, {"vote_moves": moved, "vote_words": words,
                                                 "votes_done": list(r.t.get("votes_done") or []) + [n]})


def _snapshots_from_previous(r: _Run) -> dict:
    """For a forward recovery: the voted chunks of each rebuilt collection, read from its `__previous` table (where
    the old rows went at the swap), minus the collections already carried and the refs already moved."""
    from resource_explorer import chunk_votes

    d = r.d
    done = set(r.t.get("votes_done") or [])
    moved = set((r.t.get("vote_moves") or {}).keys())
    out = {}
    for n in r.t.get("kept") or []:
        if n in done or not d.store.collection_exists(previous(n)):
            continue
        snap = chunk_votes.snapshot(d.store, n, rows_from=previous(n))
        snap.voted = [v for v in snap.voted if v.ref not in moved]
        out[n] = snap
    return out


def _swap(r: _Run) -> None:
    """Prune, swap, carry, extend: each step's INTENT is recorded before its effect (B1), so recovery knows what may
    have happened, and decides whether the swap committed from the tables."""
    from resource_explorer import chunk_votes

    d, slug = r.d, r.t["slug"]
    p = d.registry.get(slug)
    shared = shared_collections(slug, p.collections)
    live = own_collections(slug, p.collections)
    dropped, added, kept = r.t.get("dropped") or [], r.t.get("added") or [], r.t.get("kept") or []
    snaps = {n: chunk_votes.snapshot(d.store, n) for n in kept if d.store.collection_exists(n)}
    list_before = live + [c for c in shared if c not in live]
    pruned = [c for c in live if c not in dropped] + [c for c in shared if c not in live]
    # 1. prune: the dropped types leave the list while their tables still exist, so no reader names a missing one.
    r.step("pruning", list_before=list_before, pruned=pruned)
    _set_list(r, pruned)
    r.step("list_pruned", f"dropped from the list: {', '.join(dropped) or 'none'}")
    # 2. swap, one transaction: live -> previous for every live table rebuilt or dropped; aside -> live for the built.
    renames = [(n, previous(n)) for n in kept + dropped if d.store.collection_exists(n)]
    renames += [(aside(n), n) for n in kept + added]
    r.step("swapping", renames=renames)
    d.store.swap_collections(renames)
    r.step("swapped", f"swapped {len(kept) + len(added)} collection(s); the previous index is kept")
    # 3. votes, now that the new rows are live; recorded per collection.
    _carry_votes(r, snaps)
    # 4. extend: the added types join the list now that their tables exist.
    final = _final_list(r)
    r.step("extending", final=final)
    _set_list(r, final)
    words = "; ".join(r.t.get("vote_words") or [])
    r.step("list_extended", f"added to the list: {', '.join(added) or 'none'}" + (f"; {words}" if words else ""))
    _complete(r, final)


def _complete(r: _Run, final: list[str]) -> None:
    d, slug = r.d, r.t["slug"]
    d.registry.update_indexed_at(slug, final)
    if r.t.get("sha"):
        d.registry.update_commit_sha(slug, r.t["sha"])
    if r.t.get("file_count") is not None:
        d.registry.update_ingestion_stats(slug, int(r.t.get("file_count") or 0), int(r.t.get("loc") or 0))
    mine = own_collections(slug, final)
    counts = {c: d.store.count(c) for c in mine}
    d.registry.upsert_metric(slug, "rag_ingestion", {"total_chunks": sum(counts.values()), "collections": len(mine)},
                             detail={"by_collection": counts, "rebuild": True})
    d.query_cache.invalidate_project(slug)
    until = (_now() + timedelta(days=KEEP_PREVIOUS_DAYS)).isoformat()
    r.step("done", f"rebuilt; undo available until {until[:10]}", previous_until=until)


def _drop_aside(r: _Run) -> None:
    d, slug = r.d, r.t["slug"]
    p = d.registry.get(slug)
    names = set(own_collections(slug, p.collections if p else [])) | set((r.t.get("counts") or {}).keys())
    for n in names:
        try:
            if d.store.collection_exists(aside(n)):
                d.store.drop_collection(aside(n))
        except Exception as exc:  # noqa: BLE001 -- best effort; the name stays visible as __rebuild
            log.warning("could not drop %s: %s", aside(n), exc)


# ── recovery ─────────────────────────────────────────────────────────────────────────────────────────────────────

#: Phases at or after which the swap's transaction may have committed; recovery reads the tables to know.
SWAP_MAY_HAVE_COMMITTED = ("swapping", "swapped", "extending", "list_extended")


def recover_target(r: _Run) -> str:
    """Finish forward or roll back, by what the TABLES say, never by the phase alone (B1). Never leaves "pruned but the
    swap failed" (a dropped type gone from chat while its old index serves) or "swapped but not extended" (an added
    type never queried), and a forward recovery ends `done` with undo available."""
    phase = r.t.get("phase")
    if phase in ("done", "rolled_back", "failed", "awaiting_accept", "cancelled"):
        return f"nothing to recover at {phase}"
    if _swap_committed(r) and (phase in SWAP_MAY_HAVE_COMMITTED or r.t.get("renames")):
        if phase not in ("extending", "list_extended") or not r.t.get("votes_done"):
            _carry_votes(r, _snapshots_from_previous(r))
        final = _final_list(r)
        if phase != "list_extended":
            r.step("extending", "finished forward after the swap had committed", final=final)
            _set_list(r, final)
            r.step("list_extended", "finished forward: the added types joined the list")
        _complete(r, final)
        return "finished forward"
    if r.t.get("list_before"):
        _set_list(r, r.t["list_before"])
    _drop_aside(r)
    r.step("rolled_back", f"rolled back at {phase}: the swap had not committed, so the live index is unchanged; "
                          "the list is as it was and the aside tables were dropped")
    return "rolled back"


def recover(registry, row: dict, deps: Deps | None = None) -> str:
    """Called for a `reindex` run whose process died (run_reconciler)."""
    deps = deps or Deps(registry=registry)
    return recover_target(_Run(row["id"], deps))


# ── undo ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def undo(registry, slug: str, *, requested_by: str, deps: Deps | None = None) -> dict:
    """Restore the previous index of `slug`'s last rebuild, within its 7 days, in the mirror order: types the rebuild
    ADDED leave the list first, the swap is reversed (each rebuilt table is set aside as `__rebuild`), then the types
    it DROPPED rejoin the list. Votes moved by the rebuild are moved back. Survey writes are not reverted. Recoverable
    like the rebuild (B3): intent before effect, and `recover_undo` reads the tables."""
    if not (requested_by or "").strip():
        raise ReindexRefused("Undo needs a signed-in admin.", 401)
    row = latest_done(registry, slug)
    if row is None:
        raise ReindexRefused(f"{slug} has no rebuild whose previous index is still kept.", 404)
    deps = deps or Deps(registry=registry)
    r = _Run(row["id"], deps)
    if r.t.get("discarding"):
        raise ReindexRefused(f"{slug}'s previous index is being discarded (partly dropped); undo is no longer "
                             "possible.")
    if r.t.get("previous_until", "") < _now().isoformat():
        raise ReindexRefused(f"The undo window for {slug} closed on {r.t.get('previous_until', '')[:10]}.")
    if is_rebuilding(registry, slug):
        raise ReindexRefused(f"{slug} has another rebuild in progress.")
    holder = f"undo:{requested_by}:{row['id']}"
    if not registry.take_claim(_claim_key(slug), holder):
        raise ReindexRefused(f"{slug}'s previous index is being discarded or undone right now.")
    try:
        _undo(r, requested_by)
        return {"run_id": row["id"], "votes_moved_back": len(r.t.get("votes_back") or [])}
    finally:
        registry.release_claim(_claim_key(slug), holder)


def _claim_key(slug: str) -> str:
    """One claim per repository for the steps that act on its previous index (undo, discard, the 7-day drop), so two
    of them can never run at once in two processes. A stale claim (900 s) marks a dead undo for `recover_undos`."""
    return f"reindex-previous:{slug}"


def _undo_committed(r: _Run) -> bool:
    """Whether the undo's swap committed, from the tables: no previous table is left, and each rebuilt table is aside."""
    s = r.d.store
    kept, added, dropped = r.t.get("kept") or [], r.t.get("added") or [], r.t.get("dropped") or []
    return (not any(s.collection_exists(previous(n)) for n in kept + dropped)
            and all(s.collection_exists(aside(n)) or not s.collection_exists(n) for n in added))


def _undo(r: _Run, requested_by: str, *, resume: bool = False) -> None:
    d, slug = r.d, r.t["slug"]
    kept, added, dropped = r.t.get("kept") or [], r.t.get("added") or [], r.t.get("dropped") or []
    p = d.registry.get(slug)
    shared = shared_collections(slug, p.collections if p else [])
    restored = kept + dropped + [c for c in shared if c not in kept + dropped]
    if not resume:
        r.step("undo_pruning", undo_by=requested_by,
               undo_list_before=_final_list(r), undo_pruned=kept + [c for c in shared if c not in kept])
        _set_list(r, r.t["undo_pruned"])
        renames = [(n, aside(n)) for n in kept + added] + [(previous(n), n) for n in kept + dropped]
        r.step("undo_swapping", undo_renames=renames)
        d.store.swap_collections(renames)
        r.step("undo_swapped", "the previous index is live again")
    metrics = _metrics()
    back = list(r.t.get("votes_back") or [])
    for old, new in (r.t.get("vote_moves") or {}).items():
        if old in back:
            continue
        metrics.move_chunk_votes(new, old)
        back.append(old)
        r.t = d.registry.merge_run_target(r.id, {"votes_back": back})
    r.step("undo_extending", undo_restored=restored)
    _set_list(r, restored)
    for n in kept + added:
        if d.store.collection_exists(aside(n)):
            d.store.drop_collection(aside(n))
    words = (f"undone by {requested_by}: the previous index is live again; {len(back)} vote(s) moved back; "
             f"{UNDO_NOT_REVERTED} stay as the rebuild left them")
    r.step("done", words, undone=requested_by, undone_at=_now().isoformat())
    from resource_explorer.activity_logger import log_reindex

    log_reindex(d.registry, slug, (p.display_name if p else "") or slug, "ok", f"Rebuild of {slug}'s chat index {words}.")


def recover_undo(r: _Run) -> str:
    """Finish or reverse an undo that died part-way, by what the tables say (B3)."""
    phase = r.t.get("phase", "")
    if not phase.startswith("undo_"):
        return f"nothing to recover at {phase}"
    if _undo_committed(r):
        _undo(r, r.t.get("undo_by") or "recovery", resume=True)
        return "undo finished forward"
    if r.t.get("undo_list_before"):
        _set_list(r, r.t["undo_list_before"])
    r.step("done", "an undo that died before its swap committed was reversed: the rebuilt index is still live and "
                   "undo is still available")
    return "undo reversed"


def recover_undos(registry, deps: Deps | None = None) -> list[str]:
    """For the reconciler: every rebuild left in an undo phase whose claim no live process holds is finished or
    reversed. An undo still running holds its claim, so it is left alone."""
    out = []
    for row in registry.list_runs(kind=RUN_KIND, limit=500):
        t = _target(row)
        if not str(t.get("phase", "")).startswith("undo_"):
            continue
        slug = t.get("slug", "")
        holder = f"recovery:{row['id']}"
        if not registry.take_claim(_claim_key(slug), holder):
            continue
        try:
            out.append(f"{slug}: {recover_undo(_Run(row['id'], deps or Deps(registry=registry)))}")
        finally:
            registry.release_claim(_claim_key(slug), holder)
    return out


# ── the previous index: discarded by an admin, or dropped after 7 days (R1c) ─────────────────────────────────────

def discard_previous(registry, slug: str, *, by: str, reason: str = "", deps: Deps | None = None) -> dict:
    """Drop the previous index of `slug`'s last rebuild, ending its undo window early (an admin) or on time (the 7-day
    job). Drops each `{name}__previous` table (its indexes go with it) and the vote-remap log, which only an undo reads,
    and writes an activity row. Refused while the repository is rebuilding, or while another undo or discard holds it."""
    if not (by or "").strip():
        raise ReindexRefused("Discarding needs a signed-in admin.", 401)
    row = latest_done(registry, slug)
    if row is None:
        raise ReindexRefused(f"{slug} has no rebuild whose previous index is still kept.", 404)
    if is_rebuilding(registry, slug):
        raise ReindexRefused(f"{slug} has a rebuild in progress; its previous index is kept until it ends.")
    deps = deps or Deps(registry=registry)
    holder = f"discard:{by}:{row['id']}"
    if not registry.take_claim(_claim_key(slug), holder):
        raise ReindexRefused(f"{slug}'s previous index is being discarded or undone right now.")
    try:
        r = _Run(row["id"], deps)
        # The intent, before the first drop (the architect's read-back of 5d2b9865): an undo after a discard that died
        # part-way must refuse up front, not change chat's list and then fail on a missing table.
        if not r.t.get("discarding"):
            r.t = registry.merge_run_target(row["id"], {"discarding": by, "discarding_at": _now().isoformat()})
        dropped: list[str] = []
        for n in (r.t.get("kept") or []) + (r.t.get("dropped") or []):
            if deps.store.collection_exists(previous(n)):
                deps.store.drop_collection(previous(n))
                dropped.append(previous(n))
        why = reason or f"discarded by {by}"
        words = f"previous index dropped ({why}): {', '.join(dropped) or 'nothing was left to drop'}; undo is no longer " \
                "available"
        # A failure an earlier 7-day pass recorded is over once the drop succeeds: clear it, so progress does not show
        # a stale error beside "discarded".
        r.step("done", words, discarded=by, discarded_at=_now().isoformat(), vote_moves={}, drop_error="",
               drop_error_at="")
        from resource_explorer.activity_logger import log_reindex

        p = registry.get(slug)
        log_reindex(registry, slug, (p.display_name if p else "") or slug, "ok",
                    f"Rebuild of {slug}'s chat index: {words}.")
        return {"run_id": row["id"], "dropped": dropped}
    finally:
        registry.release_claim(_claim_key(slug), holder)


def drop_expired_previous(registry, *, now: datetime | None = None, deps: Deps | None = None) -> list[str]:
    """The 7-day job (Q4), run by the scheduler: drop every previous index whose undo window has closed. A repository
    that is rebuilding, or whose previous index another process holds, is left for the next pass. Returns the slugs
    dropped."""
    now_iso = (now or _now()).isoformat()
    done: list[str] = []
    unrecorded: list[str] = []
    for row in registry.list_runs(kind=RUN_KIND, limit=500):
        t = _target(row)
        if t.get("phase") != "done" or t.get("undone") or t.get("discarded"):
            continue
        # A discard that died part-way is finished whatever its window says; otherwise only an expired one is dropped.
        if not t.get("discarding") and (not t.get("previous_until") or t["previous_until"] >= now_iso):
            continue
        slug = t.get("slug", "")
        if latest_done(registry, slug) is None or latest_done(registry, slug)["id"] != row["id"]:
            continue
        try:
            reason = (f"finishing a discard by {t['discarding']} that stopped part-way" if t.get("discarding")
                      else f"the {KEEP_PREVIOUS_DAYS}-day undo window closed")
            discard_previous(registry, slug, by="the scheduled 7-day job", reason=reason, deps=deps)
            done.append(slug)
        except ReindexRefused as exc:
            log.info("previous index of %s kept for now: %s", slug, exc)
        except Exception as exc:
            # Recorded on the run row, not only logged (tests/test_no_silent_success.py): progress shows why the
            # previous index is still there, and the next pass tries again.
            log.exception("could not drop the previous index of %s; the next pass tries again", slug)
            try:
                registry.merge_run_target(row["id"], {"drop_error": f"{type(exc).__name__}: {exc}"[:300],
                                                      "drop_error_at": _now().isoformat()})
            except Exception as record_exc:  # noqa: BLE001 -- raised after the loop
                # Recording failed too: go on to the remaining repositories, then raise below so the scheduler's
                # tick logs it, rather than abort the loop here.
                unrecorded.append(f"{slug} ({type(exc).__name__}: {exc}; recording it: {record_exc})")
    if unrecorded:
        raise RuntimeError("could not drop, nor record why, the previous index of: " + "; ".join(unrecorded)
                           + (f" (dropped: {', '.join(done)})" if done else ""))
    return done


def _metrics():
    from resource_explorer.vector_store_pg import _metrics_collector
    return _metrics_collector()


# ── what the page reads ──────────────────────────────────────────────────────────────────────────────────────────

def progress(registry, slug: str) -> dict:
    """The latest rebuild of `slug`: its phase, each step's words, and when the undo window closes."""
    for r in registry.list_runs(kind=RUN_KIND, limit=200):
        t = _target(r)
        if t.get("slug") == slug:
            return {"run_id": r["id"], "state": r.get("state"), "phase": t.get("phase"), "held": t.get("held"),
                    "outcomes": t.get("outcomes") or [], "counts": t.get("counts") or {},
                    "survey": t.get("survey") or [], "previous_until": t.get("previous_until", ""),
                    "needs_words": needs_words(t.get("needs_bytes")) if "needs_bytes" in t else "",
                    "undo_available": (t.get("phase") == "done" and not t.get("undone") and not t.get("discarded")
                                       and not t.get("discarding")
                                       and t.get("previous_until", "") >= _now().isoformat()),
                    "previous_tables": [previous(n) for n in (t.get("kept") or []) + (t.get("dropped") or [])]
                    if t.get("phase") == "done" and not t.get("undone") and not t.get("discarded") else [],
                    "drop_error": t.get("drop_error", ""), "left_aside": t.get("left_aside") or [],
                    # The build's own timing (set on success and on failure), and the run row's heartbeat: a
                    # running row whose heartbeat is older than 3 intervals is what crash recovery looks at.
                    "built_seconds": t.get("built_seconds"), "build_started_at": t.get("build_started_at", ""),
                    "notes": t.get("notes") or [],
                    "heartbeat_at": r.get("heartbeat_at") or "", "started_at": r.get("started_at") or "",
                    "undone": bool(t.get("undone")), "error": r.get("error", "")}
    return {"run_id": None}
