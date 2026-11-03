"""The first index of a repository, asked first (REPLY-DESIGNER-GROUP-SCHEDULING-AND-FIRST-INDEX-2026-10-19 §4 and
REPLY-DESIGNER-FIRST-INDEX-CONSENT-CHOICE-2026-10-20).

**Nothing is indexed without a yes that names it.** A refresh cannot make a first index (`IncrementalIndexer.refresh`
returns `NOT_YET_INDEXED`); this module is the one place that does, and only for a repository a signed-in person said yes to.
The work is the ingestion RE already runs for a new project (`RepoAnalyzer` plan, then `IngestionPipeline.run`, then the
baseline commit SHA the wizard stores), queued through the run queue as kind `first_index` with the requester recorded.
There is no second indexing path.

**What is stored, and what is not.** No new table or column. The batch is the `runs` rows (their `target` carries the batch
id, the position among the yeses and the batch size) plus ONE activity row per batch. A repository's state on screen comes
from its run row and from the persisted collections list (`index_state`), never from the branch the code took.
"Skipped" and "not asked" are the dialog's own state: they have no run row, so nothing outside the dialog claims them.

**Figures.** Measured (files, MB of text, repository size) come from the latest stored file inventory, and say which survey
and when. Estimated (time, index size) come only from the repositories already indexed here, and only when the stored
records hold enough to estimate from; otherwise the figure is `None` and the words are `UNKNOWN_UNTIL_FIRST_INDEX`.
A made-up number is never returned.
"""
from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePath

from resource_explorer.index_state import has_own_index, index_state, own_collections, shared_collections

log = logging.getLogger(__name__)

RUN_KIND = "first_index"

#: The words on screen for a repository's first-index state; each is derived from a run row (see `state_of`).
WAITING, INDEXING, INDEXED, FAILED = "waiting", "indexing", "indexed", "failed"

#: Owner's ruling (2026-10-19): when the indexed repositories give too little data, the estimate says this.
UNKNOWN_UNTIL_FIRST_INDEX = "unknown until the first index"

#: How many indexed repositories (each with BOTH a text measure and a stored result) an estimate needs before it is
#: shown. Fewer than this and the ratio is a guess, so it is not a number.
MIN_BASIS_REPOS = 3

#: A run that is on the queue or being worked: a second yes for the same repository is refused while one is.
ACTIVE_STATES = ("queued", "claimed", "running")

NO_REQUESTER_REFUSAL = "A first index needs a signed-in person's yes; none is recorded for this request."


class FirstIndexRefused(Exception):
    """A yes that cannot be queued, with the sentence to show. `status` is the HTTP status the route uses."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _day(ts: str) -> str:
    """`2026-10-17T09:00:00` -> `10-17`."""
    return (ts or "")[5:10]


def _text_extensions() -> frozenset[str]:
    from resource_explorer.configdata.collection_config import COLLECTION_TYPES

    return frozenset(e.lower() for ct in COLLECTION_TYPES.values() for e in ct.file_extensions)


# ── measured figures ─────────────────────────────────────────────────────────


def measured(registry, slug: str) -> dict:
    """What the latest stored file inventory says about the repository, or `measured: False`.

    `files` and `text_bytes` count the repository's own files (not vendored, not generated) whose extension an index
    collection reads. A repository with no inventory is NOT measured, which is not the same as "0 files": every field is
    None and `source` says so.
    """
    summary = registry.file_inventory_summary(slug)
    if not summary.get("total"):
        return {"measured": False, "files": None, "text_bytes": None, "source": "no file inventory yet",
                "surveyed_at": "", "repo_kb": _repo_kb(registry, slug)}
    exts = _text_extensions()
    rows = registry.get_file_inventory_with_sizes(slug)
    text = [r for r in rows if PurePath(r["file_path"]).suffix.lower() in exts]
    surveyed_at = summary.get("indexed_at") or ""
    return {"measured": True, "files": len(text), "text_bytes": sum(int(r["file_size_bytes"] or 0) for r in text),
            "source": f"from the survey of {_day(surveyed_at)}" if surveyed_at else "from the stored file inventory",
            "surveyed_at": surveyed_at, "repo_kb": _repo_kb(registry, slug)}


def _repo_kb(registry, slug: str) -> int | None:
    stats = registry.get_latest_project_stats(slug) or {}
    kb = stats.get("repo_size_kb")
    return int(kb) if kb else None


# ── estimates, from the repositories already indexed here ────────────────────


def basis(registry, projects=None) -> dict:
    """The stored records an estimate may stand on, and what they hold.

    Index size: for each indexed repository with a measured text size AND a stored `rag_ingestion` result, chunks and MB of
    text. Time: each finished `first_index` run's duration against the MB of text it recorded when it was queued. The
    registry holds no timing for repositories indexed before this existed, so the time basis starts empty and fills as
    first indexes finish; until `MIN_BASIS_REPOS` of them have, time is unknown.
    """
    projects = registry.list_all() if projects is None else projects
    chunk_pairs: list[tuple[float, float]] = []
    for p in projects:
        if not has_own_index(p.slug, p.collections):
            continue
        m = registry.query_metrics(p.slug, "rag_ingestion") or {}
        chunks = m.get("total_chunks")
        text = measured(registry, p.slug)
        if chunks is None or not text["measured"] or not text["text_bytes"]:
            continue
        chunk_pairs.append((float(chunks), text["text_bytes"] / 1_048_576))
    timed: list[float] = []
    for r in registry.list_runs(kind=RUN_KIND, state="succeeded", limit=500):
        t = _target(r)
        secs = _duration_seconds(r)
        mb = (t.get("text_bytes") or 0) / 1_048_576
        if secs is not None and secs > 0 and mb > 0:
            timed.append(secs / mb)
    return {
        "indexed_repos": len(chunk_pairs),
        "chunks_per_mb": (sum(c for c, _ in chunk_pairs) / sum(m for _, m in chunk_pairs)
                          if len(chunk_pairs) >= MIN_BASIS_REPOS else None),
        "timed_runs": len(timed),
        "seconds_per_mb": statistics.median(timed) if len(timed) >= MIN_BASIS_REPOS else None,
    }


def estimate(text_bytes: int | None, b: dict) -> dict:
    """Time and index size for `text_bytes` of text, or `None` for whichever the basis cannot support."""
    if not text_bytes:
        return {"seconds": None, "chunks": None, "seconds_source": "", "chunks_source": ""}
    mb = text_bytes / 1_048_576
    secs = round(mb * b["seconds_per_mb"]) if b.get("seconds_per_mb") else None
    chunks = round(mb * b["chunks_per_mb"]) if b.get("chunks_per_mb") else None
    return {
        "seconds": secs,
        "chunks": chunks,
        "seconds_source": f"estimated from {b['timed_runs']} first indexes done here" if secs is not None else "",
        "chunks_source": f"estimated from the {b['indexed_repos']} repositories indexed here" if chunks is not None else "",
    }


def ask_figures(registry, slugs: list[str]) -> dict:
    """The figures the ask shows: one entry per repository, and totals that count only what was measured/estimated."""
    b = basis(registry)
    repos, missing = [], []
    for slug in slugs:
        p = registry.get(slug)
        if p is None:
            missing.append(slug)
            continue
        m = measured(registry, slug)
        e = estimate(m["text_bytes"], b)
        repos.append({
            "slug": slug, "name": p.display_name or slug, "index_state": index_state(p),
            **m, **e, "time_words": UNKNOWN_UNTIL_FIRST_INDEX if e["seconds"] is None else "",
            "size_words": UNKNOWN_UNTIL_FIRST_INDEX if e["chunks"] is None else "",
            "fetch": "clones the repository" + (f" (≈ {round(m['repo_kb'] / 1024)} MB)" if m["repo_kb"] else ""),
        })
    return {"repositories": repos, "missing": missing, "basis": b, "totals": totals(repos)}


def totals(repos: list[dict]) -> dict:
    """Totals over the entries that HAVE the figure, with how many that is, so "1,240 files" never silently omits ten."""
    def total(key):
        have = [r[key] for r in repos if r.get(key) is not None]
        return {"value": sum(have) if have else None, "of": len(have), "out_of": len(repos)}

    return {"count": len(repos), "files": total("files"), "text_bytes": total("text_bytes"),
            "seconds": total("seconds"), "chunks": total("chunks")}


# ── queueing a yes ───────────────────────────────────────────────────────────


def _target(run: dict) -> dict:
    try:
        t = json.loads(run.get("target") or "{}")
    except (TypeError, ValueError):
        return {}
    return t if isinstance(t, dict) else {}


def _duration_seconds(run: dict) -> float | None:
    try:
        a, b = datetime.fromisoformat(run["started_at"]), datetime.fromisoformat(run["finished_at"])
    except (KeyError, TypeError, ValueError):
        return None
    return (b - a).total_seconds()


def active_run_for(registry, slug: str) -> dict | None:
    for state in ACTIVE_STATES:
        for r in registry.list_runs(kind=RUN_KIND, state=state, limit=500):
            if _target(r).get("slug") == slug:
                return r
    return None


def queue(registry, slug: str, *, requested_by: str, batch_id: str = "", batch_total: int = 0,
          path: str = "single") -> dict:
    """Queue one yes. Returns `{run_id, activity_id, position, batch_id}`; raises `FirstIndexRefused` otherwise.

    `position` is the ordinal of this yes within its batch (the order of the yeses, which is the order the queue runs
    them). The requester is recorded on the run row; a yes with nobody recorded is refused here, not left to fail later.
    """
    from resource_explorer.activity_logger import log_first_index

    if not (requested_by or "").strip():
        raise FirstIndexRefused(NO_REQUESTER_REFUSAL, 401)
    p = registry.get(slug)
    if p is None:
        raise FirstIndexRefused(f"Project '{slug}' not found.", 404)
    if has_own_index(p.slug, p.collections):
        raise FirstIndexRefused(f"{p.display_name or slug} already has an index of its own; a refresh updates it.")
    if active_run_for(registry, slug) is not None:
        raise FirstIndexRefused(f"{p.display_name or slug} is already waiting to be indexed, or is being indexed.")
    m = measured(registry, slug)
    position = 1
    if batch_id:
        position = 1 + sum(1 for r in registry.list_runs(kind=RUN_KIND, limit=500) if _target(r).get("batch_id") == batch_id)
        _record_batch(registry, batch_id, batch_total, path, requested_by, position)
    activity_id = log_first_index(registry, slug, p.display_name or slug, "running",
                                  f"First index of {p.display_name or slug} for chat: queued.")
    target = {"slug": slug, "batch_id": batch_id, "batch_total": int(batch_total or 0), "position": position,
              "path": path, "files": m["files"], "text_bytes": m["text_bytes"], "asked_by": requested_by}
    run_id = registry.enqueue_run(RUN_KIND, target, requested_by=requested_by, result_ref=activity_id)
    return {"run_id": run_id, "activity_id": activity_id, "position": position, "batch_id": batch_id}


def _record_batch(registry, batch_id: str, total: int, path: str, who: str, queued: int) -> None:
    """The batch's ONE activity row, written on its first yes and rewritten as the yeses come in (same id)."""
    from resource_explorer.activity_logger import log_first_index_batch

    log_first_index_batch(registry, batch_id, who, total, queued, path)


def withdraw(registry, run_id: str) -> bool:
    """Take a yes back before the repository has started. False once it has: nothing interrupts a started index."""
    run = registry.get_run(run_id)
    if run is None or run["kind"] != RUN_KIND:
        return False
    if not registry.cancel_queued_run(run_id):
        return False
    registry.close_activity_if_running(run.get("result_ref") or "", "ok",
                                       "First index withdrawn before it started; nothing was indexed.")
    return True


# ── the state of each yes, from run rows ─────────────────────────────────────


def state_of(run: dict, project) -> tuple[str, str]:
    """(state, one sentence) for a first-index run. Every word comes from the run row, plus the persisted collections.

    `indexed` needs BOTH a succeeded run and an index of its own on the project: a run that finished without leaving one is
    `failed`, with that said, rather than a success claimed from the branch the handler took.
    """
    st = run.get("state")
    if st in ("queued", "claimed"):
        return WAITING, ""
    if st == "running":
        return INDEXING, ""
    if st == "succeeded":
        if project is not None and has_own_index(project.slug, project.collections):
            return INDEXED, ""
        return FAILED, "The run finished but the repository has no index of its own."
    if st == "cancelled":
        return "withdrawn", ""
    return FAILED, _first_sentence(run.get("error") or "no reason recorded")


def _first_sentence(text: str, limit: int = 240) -> str:
    text = " ".join(str(text or "").split())
    for i, ch in enumerate(text):
        if ch == "." and (i + 1 == len(text) or text[i + 1] == " "):
            text = text[:i]
            break
    return text[:limit]


def progress(registry) -> dict:
    """The First indexing list: one entry per repository's LATEST first-index run, plus the counts.

    `done` counts the repositories now `indexed`; `of` is the batch size when the latest runs share one batch, else the
    number of lines. Withdrawn runs are not listed (the yes was taken back).
    """
    latest: dict[str, dict] = {}
    for r in registry.list_runs(kind=RUN_KIND, limit=500):          # newest first
        slug = _target(r).get("slug")
        if slug and slug not in latest:
            latest[slug] = r
    lines = []
    for slug, r in latest.items():
        p = registry.get(slug)
        state, why = state_of(r, p)
        if state == "withdrawn":
            continue
        t = _target(r)
        lines.append({
            "slug": slug, "name": (p.display_name if p else slug) or slug, "run_id": r["id"], "state": state,
            "reason": why, "position": t.get("position") or 0, "batch_id": t.get("batch_id") or "",
            "batch_total": t.get("batch_total") or 0, "enqueued_at": r.get("enqueued_at") or "",
            "started_at": r.get("started_at") or "", "finished_at": r.get("finished_at") or "",
            "activity_id": r.get("result_ref") or "",
            "index_state": index_state(p) if p else "not_yet_indexed",
            "chunks": (registry.query_metrics(slug, "rag_ingestion") or {}).get("total_chunks") if state == INDEXED else None,
            "seconds": _duration_seconds(r) if state == INDEXED else None,
            "text_bytes": t.get("text_bytes") if state == INDEXED else None,
        })
    lines.sort(key=lambda x: (x["batch_id"], x["position"], x["enqueued_at"]))
    done = sum(1 for x in lines if x["state"] == INDEXED)
    batches = {x["batch_id"] for x in lines if x["batch_id"]}
    of = max((x["batch_total"] for x in lines), default=0) if len(batches) == 1 else len(lines)
    return {"lines": lines, "done": done, "of": max(of, len(lines)) if lines else 0,
            "active": any(x["state"] in (WAITING, INDEXING) for x in lines)}


# ── the run itself ───────────────────────────────────────────────────────────


@dataclass
class FirstIndexResult:
    status: str                      # "ok" | "error"
    summary: str = ""
    error: str = ""


def run_first_index(slug: str, *, registry=None, analyzer=None, pipeline=None, client=None, store=None,
                    query_cache=None) -> FirstIndexResult:
    """The first index of `slug`: the same plan, ingestion and baseline SHA a new project gets.

    The collaborators are parameters so a test can hand in fakes bound to the real signatures; each defaults to the real
    class. Nothing here is reached unless a run row (a person's yes) claimed it.
    """
    from resource_explorer.registry import ProjectRegistry

    registry = registry or ProjectRegistry()
    project = registry.get(slug)
    if project is None:
        return FirstIndexResult("error", error=f"Project '{slug}' not found")
    if has_own_index(project.slug, project.collections):
        return FirstIndexResult("error", error=f"{slug} already has an index of its own; a refresh updates it")
    if analyzer is None:
        from resource_explorer.github.analyzer import RepoAnalyzer
        analyzer = RepoAnalyzer()
    if pipeline is None:
        from resource_explorer.ingestion.pipeline import IngestionPipeline
        pipeline = IngestionPipeline()
    if client is None:
        from resource_explorer.github.client import GitHubClient
        client = GitHubClient()

    shared = shared_collections(slug, project.collections)
    plan = analyzer.analyze(project.github_url, subpath=project.subproject_path or None)
    pipeline.run(slug, plan.github_url, plan.proposed_collections,
                 subproject_path=project.subproject_path or None,
                 extra_docs_paths=list(project.extra_docs_paths or []) or None)
    after = registry.get(slug)
    mine = own_collections(slug, after.collections if after else [])
    if not mine:
        return FirstIndexResult("error", error=f"The ingestion finished but produced no collection of its own for {slug}.")
    if shared:                       # the pipeline replaces the list; the shared site collections stay listed
        registry.update_indexed_at(slug, mine + [c for c in shared if c not in mine])

    sha = client.get_latest_commit_sha(client.get_repo(project.github_url))
    registry.update_commit_sha(slug, sha)

    if store is None:
        from resource_explorer.vector_store_pg import MultiCollectionStore
        store = MultiCollectionStore()
    counts = {c: store.count(c) for c in mine}
    registry.upsert_metric(slug, "rag_ingestion", {"total_chunks": sum(counts.values()), "collections": len(mine)},
                           detail={"by_collection": counts, "first_index": True})
    if query_cache is None:
        from resource_explorer.query_cache import QueryCache
        query_cache = QueryCache()
    query_cache.invalidate_project(slug)
    return FirstIndexResult("ok", summary=f"{sum(counts.values())} chunk(s) across {len(mine)} collection(s).")
