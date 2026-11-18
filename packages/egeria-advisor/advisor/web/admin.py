"""
Admin API routes for Egeria Advisor.

Endpoints:
  GET  /admin                           → admin.html
  GET  /api/admin/status                → collection + repo + system status
  POST /api/admin/collections/{n}/reindex  → start incremental or force re-index
                                          (force=true also needs confirm=<n>: it drops the collection)
  POST /api/admin/repos/{repo}/pull     → start git pull
  GET  /api/admin/jobs                  → list recent jobs (last 20)
  GET  /api/admin/jobs/{job_id}         → single job status + output
  POST /api/admin/maintenance/{action}  → refresh_perspectives | sync_specs (alias refresh_specs) |
                                          clear_cache | invalidate_index |
                                          refresh_templates | check_draft_doc_ids |
                                          repair_draft_doc_ids

Every route here is admin-only: `advisor.web.app` gates the whole `/api/admin/` prefix with
`trellis_auth.AdminPathMiddleware` (Phase 0 S1, 2026-11-10), so a route added here later is gated by default.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from loguru import logger

from advisor.config import resolve_advisor_data_root
from advisor.db_consolidated import get_db_manager

router = APIRouter()

_STATIC = Path(__file__).parent / "static"
_REPO_ROOT = Path(__file__).parent.parent.parent
# Writable-state root, same one advisor_cache_dir/feedback already use
# (resolve_advisor_data_root, ADVISOR_DATA_PATH) -- NOT _REPO_ROOT/"data", the
# package-relative path this used to be. That path sits outside the
# container's only persistent volume (trellis-ea-data:/app/data), so every
# repo clone_repos.py/ingest_collections.py made was silently wiped on the
# next redeploy: a "pull" from the admin UI would always start from a fresh
# clone rather than an incremental update. See clone_repos.py/
# ingest_collections.py for the matching fix on the write side.
_DATA_DIR = resolve_advisor_data_root()
_REPOS_DIR = _DATA_DIR / "repos"

# ---------------------------------------------------------------------------
# Job tracking
# ---------------------------------------------------------------------------

class Job:
    def __init__(self, job_id: str, job_type: str, description: str):
        self.id = job_id
        self.type = job_type
        self.description = description
        self.status = "running"
        self.lines: List[str] = []
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.completed_at: Optional[str] = None
        self.error: Optional[str] = None

    def log(self, line: str) -> None:
        self.lines.append(line)

    def finish(self, error: Optional[str] = None) -> None:
        self.status = "failed" if error else "done"
        self.error = error
        self.completed_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "type": self.type, "description": self.description,
            "status": self.status, "output": self.lines[-200:],
            "started_at": self.started_at, "completed_at": self.completed_at,
            "error": self.error,
        }


_jobs: Dict[str, Job] = {}
_jobs_lock = threading.Lock()


def _new_job(job_type: str, description: str) -> Job:
    job = Job(str(uuid.uuid4())[:8], job_type, description)
    with _jobs_lock:
        _jobs[job.id] = job
        # Keep only the last 20 jobs
        if len(_jobs) > 20:
            oldest = sorted(_jobs.keys(),
                            key=lambda k: _jobs[k].started_at)[:len(_jobs) - 20]
            for k in oldest:
                del _jobs[k]
    return job


def _run_job(job: Job, cmd: List[str], cwd: Optional[Path] = None,
             collection_name: Optional[str] = None) -> None:
    """Run a subprocess, stream output to the job, mark done/failed."""
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, cwd=str(cwd or _REPO_ROOT),
        )
        for line in proc.stdout:
            job.log(line.rstrip())
        proc.wait()
        if proc.returncode != 0:
            job.finish(error=f"Process exited with code {proc.returncode}")
        else:
            job.finish()
            # Record successful reindex in consolidated DB
            if collection_name and job.type == "reindex":
                record_ingest_time(collection_name, source="admin")
    except Exception as exc:
        job.log(f"ERROR: {exc}")
        job.finish(error=str(exc))


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------

_TABLE_NAME_MAP = {"pyegeria_drE": "pyegeria_dre"}


def _table(name: str) -> str:
    return _TABLE_NAME_MAP.get(name, name.lower())


def _vector_counts() -> Dict[str, int]:
    """Return {collection_name: vector_count} for all known collections."""
    counts: Dict[str, int] = {}
    try:
        from advisor.collection_config import ALL_COLLECTIONS
        db = get_db_manager()
        conn = db.get_connection()
        try:
            with conn.cursor() as cur:
                for name in ALL_COLLECTIONS:
                    table = _table(name)
                    try:
                        cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                        counts[name] = cur.fetchone()[0]
                    except Exception:
                        conn.rollback()
                        counts[name] = -1
        finally:
            db.put_connection(conn)
    except Exception as exc:
        logger.debug(f"admin: vector count failed — {exc}")
    return counts


def record_ingest_time(
    collection_name: str,
    files: int = 0,
    chunks: int = 0,
    source: str = "admin",
) -> None:
    """Write an ingestion completion record to consolidated DB."""
    try:
        db = get_db_manager()
        sql = """
            INSERT INTO ingest_log (collection_name, last_ingested, files_processed, chunks_created, source)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT(collection_name) DO UPDATE SET
                last_ingested=EXCLUDED.last_ingested,
                files_processed=EXCLUDED.files_processed,
                chunks_created=EXCLUDED.chunks_created,
                source=EXCLUDED.source
        """
        db.execute_update(sql, (collection_name, time.time(), files, chunks, source))
    except Exception as exc:
        logger.debug(f"admin: record_ingest_time failed — {exc}")


def _last_indexed() -> Dict[str, Optional[float]]:
    """
    Return {collection_name: unix_timestamp} using consolidated PostgreSQL.
    """
    result: Dict[str, Optional[float]] = {}
    db = get_db_manager()

    # Source 1: ingest_log
    try:
        rows = db.execute_query("SELECT collection_name, last_ingested FROM ingest_log")
        for r in rows:
            result[r["collection_name"]] = r["last_ingested"]
    except Exception as exc:
        logger.debug(f"admin: ingest_log read failed — {exc}")

    # Source 2: indexed_files (consolidated change tracker)
    try:
        rows = db.execute_query(
            "SELECT collection_name, MAX(last_indexed) AS max_idx FROM indexed_files "
            "GROUP BY collection_name"
        )
        for r in rows:
            name = r["collection_name"]
            ts = r["max_idx"]
            if ts and ts > (result.get(name) or 0):
                result[name] = ts
    except Exception as exc:
        logger.debug(f"admin: indexed_files read failed — {exc}")

    # Source 3: pg_stat_user_tables — use last_autoanalyze as a proxy for
    # when rows were last bulk-inserted (PostgreSQL auto-analyzes after inserts)
    try:
        rows = db.execute_query("""
            SELECT relname,
                   GREATEST(last_analyze, last_autoanalyze) AS last_activity
            FROM pg_stat_user_tables
            WHERE schemaname = 'public'
              AND GREATEST(last_analyze, last_autoanalyze) IS NOT NULL
        """)
        import calendar
        for r in rows:
            relname = r["relname"]
            last_activity = r["last_activity"]
            # Reverse-map table name to collection name
            col_name = next(
                (k for k, v in _TABLE_NAME_MAP.items() if v == relname), relname
            )
            ts = calendar.timegm(last_activity.timetuple()) if last_activity else None
            if ts and ts > (result.get(col_name) or 0):
                result[col_name] = ts
    except Exception as exc:
        logger.debug(f"admin: pg_stat_user_tables read failed — {exc}")

    return result


def _repo_name_from_url(url: str) -> str:
    return url.rstrip("/").split("/")[-1].removesuffix(".git")


def _repo_status(repo_path: Path) -> Dict[str, Any]:
    if not (repo_path / ".git").exists():
        return {"exists": False, "last_commit": None, "last_commit_msg": None, "last_pull": None}
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "log", "-1", "--format=%H|%ai|%s"],
            capture_output=True, text=True, timeout=5,
        )
        parts = result.stdout.strip().split("|", 2)
        sha, date, msg = (parts + ["", "", ""])[:3]
        return {"exists": True, "last_commit": sha[:8], "last_commit_date": date, "last_commit_msg": msg[:80]}
    except Exception:
        return {"exists": True, "last_commit": None, "last_commit_msg": None, "last_pull": None}


def _system_health() -> Dict[str, Any]:
    health: Dict[str, Any] = {"pgvector": False, "ollama": False}
    try:
        db = get_db_manager()
        conn = db.get_connection()
        db.put_connection(conn)
        health["pgvector"] = True
    except Exception:
        pass
    try:
        import urllib.request
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
        health["ollama"] = True
    except Exception:
        pass
    return health


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/admin")
async def admin_page() -> FileResponse:
    return FileResponse(_STATIC / "admin.html")


def _dre_templates_status() -> Dict[str, Any]:
    """Where Dr.Egeria templates are read from (B2): the installed pyegeria,
    or a fallback copy that may not match the dispatcher."""
    try:
        from advisor.dre_templates import templates_status
        return templates_status()
    except Exception as exc:
        return {"source": "error", "root": None, "matches_executor": False, "error": str(exc)}


@router.get("/api/admin/status")
async def admin_status() -> Dict[str, Any]:
    from advisor.collection_config import ALL_COLLECTIONS

    counts = _vector_counts()
    indexed = _last_indexed()

    # Deduplicate repos
    repos_seen: Dict[str, Dict] = {}
    collections_out = []
    for name, meta in ALL_COLLECTIONS.items():
        repo_url = meta.source_repo
        repo_name = _repo_name_from_url(repo_url)
        if repo_name not in repos_seen:
            repo_path = _REPOS_DIR / repo_name
            repos_seen[repo_name] = {
                "name": repo_name,
                "url": repo_url,
                "local_path": str(repo_path),
                **_repo_status(repo_path),
            }

        count = counts.get(name, -1)
        last_ts = indexed.get(name)
        now = time.time()
        age_days = (now - last_ts) / 86400 if last_ts else None

        if not meta.enabled:
            badge = "disabled"
        elif count < 0:
            badge = "error"
        elif count == 0:
            badge = "empty"
        elif age_days is not None and age_days > 7:
            badge = "stale"
        else:
            badge = "healthy"

        collections_out.append({
            "name": name,
            "description": meta.description,
            "enabled": meta.enabled,
            "repo": repo_name,
            "vector_count": count,
            "last_indexed_ts": last_ts,
            "age_days": round(age_days, 1) if age_days is not None else None,
            "badge": badge,
            "source_paths": meta.source_paths,
        })

    return {
        "collections": sorted(collections_out, key=lambda c: (not c["enabled"], c["name"])),
        "repos": list(repos_seen.values()),
        "system": _system_health(),
        "dr_egeria_templates": _dre_templates_status(),
        "jobs_running": sum(1 for j in _jobs.values() if j.status == "running"),
    }


@router.post("/api/admin/collections/{name}/reindex")
async def reindex_collection(name: str, force: bool = False, confirm: str = "") -> Dict[str, Any]:
    """Start a re-index job. A FORCE re-index runs `ingest_collections.py --force`, which drops the collection's
    table (`DROP TABLE ... CASCADE`), so it also needs `confirm=<the collection's name>`; without it, or with any
    other name, nothing starts (400). An incremental re-index needs only the admin gate."""
    from advisor.collection_config import ALL_COLLECTIONS
    if name not in ALL_COLLECTIONS:
        raise HTTPException(status_code=404, detail=f"Collection '{name}' not found")

    if force and (confirm or "").strip() != name:
        raise HTTPException(
            status_code=400,
            detail=f"A force re-index drops '{name}' and rebuilds it: confirm it by passing confirm={name}",
        )

    if force:
        desc = f"Force re-index: {name}"
        cmd = [sys.executable, str(_REPO_ROOT / "scripts" / "ingest_collections.py"),
               "--collection", name, "--force"]
    else:
        desc = f"Incremental re-index: {name}"
        cmd = [sys.executable, "-m", "advisor.incremental_indexer", "--collection", name]

    job = _new_job("reindex", desc)
    threading.Thread(target=_run_job, args=(job, cmd), kwargs={"collection_name": name}, daemon=True).start()
    return {"job_id": job.id, "description": desc}


@router.post("/api/admin/repos/{repo}/pull")
async def pull_repo(repo: str) -> Dict[str, Any]:
    repo_path = _REPOS_DIR / repo
    if not (repo_path / ".git").exists():
        raise HTTPException(status_code=404, detail=f"Repository '{repo}' not found at {repo_path}")

    desc = f"git pull: {repo}"
    job = _new_job("git_pull", desc)
    cmd = ["git", "-C", str(repo_path), "pull", "--ff-only"]
    threading.Thread(target=_run_job, args=(job, cmd), daemon=True).start()
    return {"job_id": job.id, "description": desc}


@router.get("/api/admin/jobs")
async def list_jobs() -> Dict[str, Any]:
    with _jobs_lock:
        jobs = sorted(_jobs.values(), key=lambda j: j.started_at, reverse=True)
    return {"jobs": [j.to_dict() for j in jobs[:20]]}


@router.get("/api/admin/jobs/{job_id}")
async def get_job(job_id: str) -> Dict[str, Any]:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


@router.post("/api/admin/maintenance/{action}")
async def maintenance_action(action: str) -> Dict[str, Any]:
    """Run one maintenance action. {"status": "ok", "message"} when it was done (or, for sync_specs,
    started); otherwise a non-2xx with the reason in `detail`: 409 when a spec sync is already running,
    500 when the action failed, 400 for an unknown action. A failure is never a 200 (A-F1)."""
    if action == "refresh_perspectives":
        from advisor.perspective_manager import invalidate
        invalidate()
        return {"status": "ok", "message": "Perspective cache invalidated — will reload on next request"}

    if action in ("sync_specs", "refresh_specs"):
        # "Sync report specs from Egeria": fill the EGERIA tier from EA's consumer folder
        # (advisor/egeria_spec_sync.py). refresh_specs is the old name, kept for callers.
        # Started in the background (G2); poll GET /api/admin/specs/sync for the outcome.
        from advisor.egeria_spec_sync import SyncBusy, start_sync
        try:
            st = start_sync()
        except SyncBusy as exc:
            # The same answer as POST /api/admin/specs/sync: a conflict, not a 200 saying "error".
            raise HTTPException(status_code=409, detail=str(exc))
        return {"status": "ok", "message": st["message"] + " The outcome shows in the report catalog's "
                                                    "'held in Egeria' line once it finishes.",
                "sync": st, "status_url": "/api/admin/specs/sync"}

    if action == "clear_cache":
        try:
            from advisor.query_cache import get_query_cache
            get_query_cache().clear()
        except Exception as exc:  # noqa: BLE001 -- reported to the admin with its reason
            raise HTTPException(status_code=500, detail=f"Query cache not cleared: {type(exc).__name__}: {exc}")
        return {"status": "ok", "message": "Query cache cleared"}

    if action == "invalidate_index":
        from advisor.report_pipeline import _question_index
        from advisor.egeria_type_registry import invalidate as invalidate_type_registry
        _question_index.invalidate()
        invalidate_type_registry()
        return {"status": "ok", "message": "QuestionSpecIndex and type registry invalidated — will rebuild on next use"}

    if action == "refresh_templates":
        from advisor.command_keyword_index import get_command_keyword_index
        get_command_keyword_index().invalidate()
        return {"status": "ok", "message": "Dr.Egeria command/template index invalidated — will rescan on next lookup"}

    if action in ("check_draft_doc_ids", "repair_draft_doc_ids"):
        from advisor.governance_draft import get_draft_manager
        report = get_draft_manager().check_doc_ids(repair=(action == "repair_draft_doc_ids"))
        stale = [r for r in report if r["status"] in ("unresolved", "repaired")]
        repaired = [r for r in stale if r["status"] == "repaired"]
        unresolved = [r for r in stale if r["status"] == "unresolved"]
        if not stale:
            message = f"All {len(report)} draft(s) checked — no stale doc_ids found"
        elif action == "check_draft_doc_ids":
            message = f"{len(stale)} of {len(report)} draft(s) have a stale doc_id — run Repair to fix"
        else:
            message = f"Repaired {len(repaired)} draft(s)" + (f"; {len(unresolved)} need manual review" if unresolved else "")
        return {"status": "ok", "message": message, "report": report}

    raise HTTPException(status_code=400, detail=f"Unknown action '{action}'")


@router.get("/api/admin/plan-stats")
async def plan_stats() -> Dict[str, Any]:
    """Return LGCI plan usage analytics from the plan_events table in Postgres."""
    empty = {
        "total_created": 0, "total_executed": 0, "execution_rate": 0.0,
        "by_outcome": {}, "by_perspective": {}, "by_family": {},
        "recent": [], "available": True,
    }

    try:
        db = get_db_manager()

        # ── Counts ───────────────────────────────────────────────────────
        rows = db.execute_query(
            "SELECT event_type, COUNT(*) AS n FROM plan_events GROUP BY event_type"
        )
        counts = {r["event_type"]: int(r["n"]) for r in rows}
        created = counts.get("created", 0)
        executed = counts.get("executed", 0)

        # ── Outcome breakdown ─────────────────────────────────────────────
        outcome_rows = db.execute_query("""
            SELECT COALESCE(outcome_status, 'Unknown') AS status, COUNT(*) AS n
            FROM plan_events WHERE event_type = 'executed'
            GROUP BY status ORDER BY n DESC
        """)
        by_outcome = {r["status"]: int(r["n"]) for r in outcome_rows}

        # ── By perspective ────────────────────────────────────────────────
        persp_rows = db.execute_query("""
            SELECT COALESCE(perspective, 'none') AS perspective, COUNT(*) AS n
            FROM plan_events WHERE event_type = 'created'
            GROUP BY perspective ORDER BY n DESC
        """)
        by_perspective = {r["perspective"]: int(r["n"]) for r in persp_rows}

        # ── Command family frequency ──────────────────────────────────────
        family_counts: Dict[str, int] = {}
        fam_rows = db.execute_query("""
            SELECT command_families FROM plan_events 
            WHERE event_type = 'created' AND command_families IS NOT NULL
        """)
        for row in fam_rows:
            for fam in (row["command_families"] or "").split(","):
                fam = fam.strip()
                if fam:
                    family_counts[fam] = family_counts.get(fam, 0) + 1
        by_family = dict(sorted(family_counts.items(), key=lambda x: -x[1])[:10])

        # ── Recent events (last 15) ───────────────────────────────────────
        recent_rows = db.execute_query("""
            SELECT doc_id, event_type, title, outcome_status, perspective, timestamp
            FROM plan_events ORDER BY timestamp DESC LIMIT 15
        """)
        recent = [dict(r) for r in recent_rows]

        execution_rate = round(executed / created, 2) if created else 0.0

        return {
            "total_created": created,
            "total_executed": executed,
            "execution_rate": execution_rate,
            "by_outcome": by_outcome,
            "by_perspective": by_perspective,
            "by_family": by_family,
            "recent": recent,
            "available": True,
        }

    except Exception as exc:
        logger.warning(f"plan_stats: {exc}")
        return {**empty, "error": str(exc)}
