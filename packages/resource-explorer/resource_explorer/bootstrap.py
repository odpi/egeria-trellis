"""Detect and repair wiped Dr.Egeria-authored definitions.

In a development environment the Egeria database is reset regularly, which
silently deletes everything RE's Survey tab depends on: the shared glossaries
and perspectives, the Question terms, and the Survey Definitions themselves.
Nothing in RE noticed — the Survey tab just quietly degraded (empty candidate
lists, or a ~20s full instance scan on every load in place of a ~0.2s scoped
lookup) with no error surfaced anywhere. This module closes that gap by
checking, at web startup and periodically thereafter, whether each batch of
Dr.Egeria command documents is still present in Egeria, and re-executing the
ones that are not.

Patterned on egeria-workspaces' PyegeriaWebHandler bootstrap (bootstrap_monitor_
handler.py / bootstrap_batches.py), adapted where RE's content differs — see
"Where this diverges" below.

## Model

`docs/dr-egeria/` is organised as *batches*: one directory per ordered
execution group, each containing a `_batch.json` manifest plus its command
documents. A top-level `_folder_order.json` gives the cross-batch order.
Only directories with a `_batch.json` are batches, so root-level files (the CSV
sources of truth, one-off probe/debug documents) are never executed.

Each batch declares a *canary*: one element whose presence in Egeria means that
batch ran to completion. Presence is the entire detection mechanism — there is
no staleness detection, deliberately. Re-running a batch is how it gets fixed,
and re-running an already-present batch is the one thing we must never do (see
IDEMPOTENCY), so "is it there?" is the only question worth asking.

## Ordering is load-bearing, not cosmetic

Batches run in `_folder_order.json` order because the content is genuinely
dependent: Question terms are created inside glossaries that foundations.md
defines, and each Survey Definition's "Link Element To Scope" commands bind it
to Question terms *by name*. Process the survey-definitions batch while the
questions batch is absent and every command still reports success, but no
ScopedBy link is created — leaving a Survey Definition that looks perfectly
healthy (its own canary is present) while the scoped lookup depending on those
links silently returns nothing. That is precisely the state RE was found in on
2026-08-19.

## IDEMPOTENCY — why heals are gated on missing-only

Dr.Egeria's `Create X` commands are name-keyed upserts and safe to re-run, but
its `Link First/Next Process Step` commands are NOT: re-running a document
against an already-linked process creates a *second* edge per step rather than
merging. SurveyDefinitionReader then sees two outgoing next-steps, correctly
refuses to guess which chain was meant, and the whole Survey Definition drops
out of service. Confirmed live twice (2026-08-13, and again 2026-08-19 when
re-running all four documents produced 22 duplicate edges at once).

So: a batch is healed ONLY when its canary is missing. A present batch is never
re-run "just in case". Batches that are inherently unsafe to re-run additionally
declare `"idempotent": false`, and may declare a `post_heal` script — for the
survey definitions that is the reconciler, which strips duplicate edges a heal
may itself have introduced.

## Failure behaviour

Fails *open* on connection errors — but reports that it did. If Egeria is
unreachable the canary check returns None ("could not determine"), not True.
No heal is attempted either way, because an unreachable server is not evidence
of a reset and healing on that basis would re-run everything against a server
that is merely briefly down. The distinction is in the *reporting*: an
undeterminable check must never be shown as a verified-healthy one.

That distinction was missing initially and cost a real incident (2026-08-19):
a redeploy wiped all three batches, the periodic check happened to run while
Egeria was still restarting, failed open, and the status endpoint then reported
present=True for every batch while every canary was in fact gone. The heal
logic was correct throughout — only the reporting lied, and it lied silently
because the fail-open branch logged at DEBUG. Hence: tri-state check,
`last_verified_at`/`last_check_error` on the status, and INFO-level logging
whenever a heal is skipped for want of a reachable server.

Startup is never blocked — the initial check runs inside the scheduler thread,
and any exception is logged and swallowed.

A canary that can never resolve would otherwise re-heal its batch on every
single pass, forever; `_consecutive_failures` caps repeated attempts per batch
so a mis-specified canary degrades to "logged and left alone" rather than an
infinite heal loop hammering Egeria. This is not hypothetical: two Question
terms in scouting-questions.md do not resolve after a successful run (both
contain apostrophes), which is exactly how such a canary gets chosen by mistake.

## Where this diverges from the Portal's version

- Canary matching accepts `qualified_name` OR `display_name`, and `type` is
  optional. The Portal keys on (type, displayName). RE needs both forms because
  its two canary populations behave oppositely: Perspective qualified names are
  author-controlled and stable, while Dr.Egeria *generates* glossary-term
  qualified names with a deployment-specific org prefix and version
  ("Coco Pharmaceuticals::Term::...::1.0") that must not be hard-coded.
- `_folder_order.json` may be either a bare JSON array (the Portal's shape) or
  an object with a "folders" key (RE's, so the file can carry comments). Both
  parse, so a Portal-style file works here unchanged.
- Adds `idempotent` and `post_heal`, which the Portal has no equivalent of.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs" / "dr-egeria"
FOLDER_ORDER_FILE = "_folder_order.json"
BATCH_MANIFEST_FILE = "_batch.json"

# How long between periodic checks. The startup check alone would miss the
# common dev case where only the Egeria container is reset while the RE web
# process keeps running, hence a loop rather than a one-shot.
CHECK_INTERVAL_SECONDS = 600

# Per-document timeout for `dr_egeria --process`. scouting-questions.md is ~52KB
# and creates 41 terms, so this is generous by design.
HEAL_TIMEOUT_SECONDS = 900

# After this many consecutive failed heals, stop retrying a batch until the
# process restarts or a manual run is requested. Guards the infinite-heal-loop
# failure mode described in the module docstring.
MAX_CONSECUTIVE_FAILURES = 3


@dataclass
class Batch:
    """One folder of Dr.Egeria command documents plus its manifest."""

    batch_id: str
    path: Path
    display_name: str
    canary: dict
    files: list[str]
    idempotent: bool = True
    post_heal: dict | None = None
    #: Report-only checks run after post_heal, e.g. the survey-definitions
    #: batch's scope-link reconciler (scripts/reconcile_survey_definition_scopes.py,
    #: report mode). Unlike post_heal, a check's non-zero exit does NOT fail
    #: the batch heal — a check surfaces drift for a person to see, it does
    #: not repair anything on this path, so "the check found drift" is not
    #: the same failure as "the heal script itself errored".
    post_heal_checks: list[dict] = field(default_factory=list)

    @property
    def has_canary(self) -> bool:
        """A batch with no usable canary is never auto-healed — there is no way
        to tell whether it needs it, and guessing means risking a re-run."""
        c = self.canary or {}
        return bool(c.get("qualified_name") or c.get("display_name"))


@dataclass
class BatchStatus:
    # Last *known* answer. None until a check has actually succeeded — which is
    # NOT the same as "the last check said present". When Egeria is unreachable
    # the check cannot answer at all, and conflating that with a verified
    # "present" is what made a wiped catalog read as healthy after a redeploy
    # (2026-08-19): the status endpoint said present=True for all three batches
    # while every canary was in fact gone. last_verified_at/last_check_error
    # exist so a stale answer can be shown as stale instead of as fact.
    present: bool | None = None
    last_checked_at: str = ""            # when a check was last attempted
    last_verified_at: str = ""           # when a check last actually reached Egeria
    last_check_error: str = ""           # non-empty => last attempt could not determine
    last_healed_at: str = ""
    last_heal_result: str = ""
    consecutive_failures: int = 0


@dataclass
class PrefectPoolStatus:
    """Worker liveness for the `resource-explorer-pool` work pool (2026-09-28,
    part of the whole-definition-Prefect-default change — see
    PREFECT-DEFAULT-WHOLE-DEFINITION-IMPLEMENTED.md).

    Same tri-state shape as `BatchStatus`: `reachable` says whether the last
    attempt could actually reach the Prefect API at all, independent of
    whether a worker was found — a real incident earlier tonight was
    `prefect_up.sh` starting a worker on the wrong pool, silent until a run
    degraded; this exists so that shows up here, before anyone hits it in a
    run.
    """

    reachable: bool | None = None
    worker_count: int = 0
    last_heartbeat: str = ""
    last_checked_at: str = ""
    last_check_error: str = ""


_status: dict[str, BatchStatus] = {}
_status_lock = threading.Lock()
_reinitializing = False
#: Held for the whole of a check_and_heal pass (see there).
_heal_lock = threading.Lock()
_stop_event: threading.Event | None = None
_thread: threading.Thread | None = None

_prefect_pool_status = PrefectPoolStatus()
_prefect_pool_status_lock = threading.Lock()


def check_prefect_pool_workers(pool: str | None = None) -> PrefectPoolStatus:
    """Query Prefect's own API for `pool`'s registered workers — the same
    `work_pools/{pool}/workers/filter` data `prefect_up.sh` and the
    dispatch-honesty live gate checked by hand — and update/return the
    module-level status.

    Fails open like the batch canary checks above: an unreachable Prefect
    API reports `reachable=False` with the error text, never a false
    "0 workers" that would look identical to a real empty pool. `pool`
    defaults to `config.prefect.work_pool`.
    """
    from resource_explorer.config import get_config

    pool = pool or get_config().prefect.work_pool
    status = PrefectPoolStatus(last_checked_at=_now())
    try:
        import asyncio

        from resource_explorer.surveyors.prefect_adapter import re_prefect_client

        async def _fetch():
            async with re_prefect_client() as client:
                return await client.read_workers_for_work_pool(pool)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            from resource_explorer.concurrency import run_sync

            workers = run_sync(lambda: asyncio.run(_fetch()))
        else:
            workers = asyncio.run(_fetch())

        status.reachable = True
        status.worker_count = len(workers)
        heartbeats = [
            str(w.last_heartbeat_time) for w in workers if getattr(w, "last_heartbeat_time", None)
        ]
        status.last_heartbeat = max(heartbeats) if heartbeats else ""
        if status.worker_count == 0:
            log.warning(
                "bootstrap: Prefect work pool %r has NO registered workers — a "
                "default whole-definition run will find the API reachable but "
                "have nothing to dispatch to; see the design note "
                "PREFECT-DEFAULT-WHOLE-DEFINITION-IMPLEMENTED.md", pool,
            )
        else:
            log.info(
                "bootstrap: Prefect work pool %r has %d worker(s), last "
                "heartbeat %s", pool, status.worker_count, status.last_heartbeat or "unknown",
            )
    except Exception as exc:
        status.reachable = False
        status.last_check_error = str(exc)
        log.warning("bootstrap: could not check Prefect pool %r workers: %s", pool, exc)

    with _prefect_pool_status_lock:
        global _prefect_pool_status
        _prefect_pool_status = status
    return status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── discovery ───────────────────────────────────────────────────────────────

def _read_folder_order(docs_dir: Path) -> list[str]:
    """Parse _folder_order.json, tolerating both the bare-array shape the
    Portal uses and RE's object form (which exists so the file can carry
    explanatory comments — JSON arrays cannot)."""
    f = docs_dir / FOLDER_ORDER_FILE
    if not f.is_file():
        return []
    try:
        raw = json.loads(f.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("bootstrap: unreadable %s (%s) — falling back to alphabetical", f, exc)
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, dict):
        return [str(x) for x in raw.get("folders", [])]
    log.warning("bootstrap: %s is neither array nor object — ignoring", f)
    return []


def discover_batches(docs_dir: Path = DOCS_DIR) -> list[Batch]:
    """Find every batch folder and return them in execution order.

    Ordering follows _folder_order.json; any batch on disk not named there runs
    afterwards, alphabetically (matching the Portal's rule — never skipped,
    never silently first). A name in the order file matching nothing on disk is
    ignored rather than raising, so deleting a folder doesn't break startup.
    """
    if not docs_dir.is_dir():
        return []

    found: dict[str, Batch] = {}
    for child in sorted(docs_dir.iterdir()):
        if not child.is_dir():
            continue
        manifest_path = child / BATCH_MANIFEST_FILE
        if not manifest_path.is_file():
            # Not a batch. Deliberate: dropping a folder into docs/dr-egeria/
            # must not silently make it execute against Egeria.
            continue
        try:
            raw = json.loads(manifest_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("bootstrap: skipping %s — unreadable manifest (%s)", child.name, exc)
            continue

        declared = [str(x) for x in raw.get("files", [])]
        on_disk = sorted(p.name for p in child.glob("*.md"))
        # Declared order first (dropping any that no longer exist), then
        # everything else alphabetically — same rule as folder ordering.
        files = [f for f in declared if (child / f).is_file()]
        files += [f for f in on_disk if f not in files]

        found[child.name] = Batch(
            batch_id=child.name,
            path=child,
            display_name=raw.get("display_name") or raw.get("displayName") or child.name,
            canary=raw.get("canary") or {},
            files=files,
            idempotent=bool(raw.get("idempotent", True)),
            post_heal=raw.get("post_heal"),
            post_heal_checks=list(raw.get("post_heal_checks") or []),
        )

    ordered_names = _read_folder_order(docs_dir)
    ordered = [found[n] for n in ordered_names if n in found]
    ordered += [b for name, b in sorted(found.items()) if name not in ordered_names]
    return ordered


# ── canary check ────────────────────────────────────────────────────────────

def canary_present(batch: Batch, client=None) -> bool | None:
    """Is this batch's canary element in Egeria?

    Tri-state, and the third state matters:
      True  — verified present
      False — verified missing (heal it)
      None  — COULD NOT DETERMINE (Egeria unreachable / lookup errored)

    None still means "do not heal" — an unreachable Egeria is not evidence of a
    reset, and healing on that basis would re-run every batch against a server
    that is merely restarting. But it must not be reported as True. Collapsing
    the two is what let a wiped catalog read as healthy after a redeploy on
    2026-08-19: the check ran mid-redeploy, failed open, and the status endpoint
    then showed present=True for all three batches while every canary was in
    fact gone. The distinction is invisible to the heal logic and essential to
    anyone reading the status.

    Unreachability is logged at INFO, not DEBUG, for the same reason: a decision
    to skip healing is an event worth seeing in a normal log.
    """
    if not batch.has_canary:
        # Nothing to check, and nothing to heal — a known answer, not an unknown.
        return True

    if client is None:
        try:
            from resource_explorer.surveyors.survey_definition_reader import SurveyDefinitionReader

            client = SurveyDefinitionReader()._connect_classification_explorer()
        except Exception as exc:
            log.info(
                "bootstrap: cannot reach Egeria to check %s (%s) — skipping heal, state unknown",
                batch.batch_id, exc,
            )
            return None

    from resource_explorer.surveyors.survey_definition_reader import _as_guid

    c = batch.canary
    if c.get("qualified_name"):
        prop, name = "qualifiedName", c["qualified_name"]
    else:
        prop, name = "displayName", c["display_name"]

    kwargs = {"property_name": [prop]}
    # type is optional on purpose: a wrong type_name silently returns nothing,
    # which reads as "missing" and triggers a heal. Matching on the globally
    # unique qualified_name alone is safer than guessing a type.
    if c.get("type"):
        kwargs["type_name"] = c["type"]

    try:
        return _as_guid(client.get_guid_for_name(name, **kwargs)) is not None
    except Exception as exc:
        log.info(
            "bootstrap: canary lookup failed for %s (%s) — skipping heal, state unknown",
            batch.batch_id, exc,
        )
        return None


# ── heal ────────────────────────────────────────────────────────────────────

def _resolve_dr_egeria_executable() -> str | None:
    """Find the `dr_egeria` executable to run.

    Prefer the one installed next to the running interpreter over whatever
    `dr_egeria` resolves to on PATH. They are not interchangeable: this
    package pins a pyegeria version, and the venv's own `dr_egeria` is built
    against it, while PATH can point anywhere. On the dev box PATH's
    `dr_egeria` is a pipx install on a different Python with a newer
    pyegeria that died at bearer-token creation with
    `AsyncLibraryNotFoundError` before running a single command (verified
    2026-09-12) — a bootstrap auto-heal picking that one up fails before it
    writes anything.
    """
    venv_candidate = Path(sys.executable).parent / "dr_egeria"
    if venv_candidate.is_file():
        return str(venv_candidate)
    return shutil.which("dr_egeria")


@dataclass
class DrEgeriaProcess:
    """What one `dr_egeria --process` run of one document returned. `ok` is the process's own answer (exit 0);
    it is NOT a per-command result: the CLI exits 0 even when commands fail or the client raises, so a caller
    that needs per-command results reads `stdout` (the summary table), as `dr_egeria_runner` does."""

    ok: bool
    detail: str
    stdout: str = ""
    stderr: str = ""


def run_dr_egeria_document(doc: Path, *, env: dict | None = None) -> DrEgeriaProcess:
    """THE Dr.Egeria processor: run one document through the `dr_egeria` CLI, `--process --summary-only`.

    Used by the bootstrap heal (`_run_dr_egeria`) and by the ad-hoc runner (`dr_egeria_runner`), so there is
    one way RE executes a Dr.Egeria document. Runs as a subprocess from the document's own directory, matching
    how these documents are run by hand and keeping relative paths resolving the same way.

    `env` replaces the child's environment when given (the ad-hoc runner passes RE's daemon credential and
    platform explicitly); None inherits this process's environment, which is what the bootstrap has always done.
    """
    executable = _resolve_dr_egeria_executable()
    if executable is None:
        venv_candidate = Path(sys.executable).parent / "dr_egeria"
        return DrEgeriaProcess(False, f"dr_egeria CLI not found next to {venv_candidate} or on PATH")

    log.info("bootstrap: running dr_egeria via %s", executable)
    kwargs = {"env": env} if env is not None else {}
    try:
        proc = subprocess.run(
            [executable, "--process", "--summary-only", doc.name],
            cwd=str(doc.parent),
            capture_output=True,
            text=True,
            timeout=HEAL_TIMEOUT_SECONDS,
            **kwargs,
        )
    except FileNotFoundError:
        return DrEgeriaProcess(False, "dr_egeria CLI not found on PATH")
    except subprocess.TimeoutExpired:
        return DrEgeriaProcess(False, f"timed out after {HEAL_TIMEOUT_SECONDS}s")
    except Exception as exc:  # pragma: no cover - defensive
        return DrEgeriaProcess(False, f"{type(exc).__name__}: {exc}")

    stdout, stderr = proc.stdout or "", proc.stderr or ""
    if proc.returncode != 0:
        tail = (stderr or stdout).strip().splitlines()
        return DrEgeriaProcess(False, f"exit {proc.returncode}: {tail[-1] if tail else 'no output'}",
                               stdout, stderr)
    return DrEgeriaProcess(True, "ok", stdout, stderr)


def _run_dr_egeria(doc: Path) -> tuple[bool, str]:
    """Execute one document for a heal (inherits this process's environment)."""
    result = run_dr_egeria_document(doc)
    return result.ok, result.detail


def _run_post_heal(batch: Batch) -> tuple[bool, str]:
    """Run a batch's post-heal repair script, if it declares one.

    Exists for the survey-definitions batch: a legitimate heal still duplicates
    step links, because Dr.Egeria's Link First/Next Process Step commands do not
    dedupe. Without this the batch would come back from a heal already broken.
    """
    spec = batch.post_heal or {}
    script = spec.get("script")
    if not script:
        return True, ""

    pkg_root = Path(__file__).resolve().parent.parent
    script_path = pkg_root / script
    if not script_path.is_file():
        return False, f"post_heal script not found: {script}"

    import sys

    try:
        proc = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(pkg_root),
            capture_output=True,
            text=True,
            timeout=HEAL_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        return False, f"post_heal {type(exc).__name__}: {exc}"

    if proc.returncode != 0:
        return False, f"post_heal exit {proc.returncode}"
    return True, "post_heal ok"


def _run_post_heal_checks(batch: Batch) -> list[str]:
    """Run every report-only check a batch declares, after post_heal.

    Unlike `_run_post_heal`, a check's non-zero exit does not fail the batch
    heal — these scripts (e.g. reconcile_survey_definition_scopes.py's
    default report mode) never write to Egeria on this path; they surface
    drift a heal cannot see (ScopedBy links vs. an authored document is not a
    "branching" symptom, so it doesn't block SurveyDefinitionReader the way
    duplicate step edges do) for a person to act on. Returns one detail
    string per declared check, always — a script that fails to even run
    (missing file, timeout, non-zero exit) is reported here exactly like one
    that ran and found drift, never silently dropped.
    """
    details: list[str] = []
    pkg_root = Path(__file__).resolve().parent.parent
    for spec in batch.post_heal_checks:
        script = spec.get("script")
        if not script:
            continue
        script_path = pkg_root / script
        if not script_path.is_file():
            details.append(f"{script}: NOT FOUND")
            continue

        import sys as _sys

        try:
            proc = subprocess.run(
                [_sys.executable, str(script_path)],
                cwd=str(pkg_root),
                capture_output=True,
                text=True,
                timeout=HEAL_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            details.append(f"{script}: {type(exc).__name__}: {exc}")
            continue

        if proc.returncode == 0:
            details.append(f"{script}: reconciled")
        else:
            tail = (proc.stdout or proc.stderr or "").strip().splitlines()
            details.append(
                f"{script}: drift found (exit {proc.returncode}) — {tail[-1] if tail else 'see log'}"
            )
    return details


def heal_batch(batch: Batch) -> tuple[bool, str]:
    """Re-execute every document in a batch, in order, then its post-heal step.

    Stops at the first failing document: within a batch, file order encodes
    dependency order, so continuing past a failure would run commands whose
    prerequisites are missing — the same class of silent-partial-success this
    whole module exists to prevent.
    """
    for filename in batch.files:
        ok, detail = _run_dr_egeria(batch.path / filename)
        if not ok:
            return False, f"{filename}: {detail}"

    ok, detail = _run_post_heal(batch)
    if not ok:
        return False, detail

    check_details = _run_post_heal_checks(batch)
    if check_details:
        detail = "; ".join([detail or "ok"] + check_details)
    return True, detail or "ok"


# ── orchestration ───────────────────────────────────────────────────────────

def check_and_heal(docs_dir: Path = DOCS_DIR, force: bool = False) -> dict:
    """Check every batch and heal the missing ones, in declared order.

    force=True re-runs every batch regardless of canary state. It exists for the
    admin "run everything" action and is deliberately NOT the default: for a
    batch with idempotent=false a forced re-run duplicates relationships and
    takes the Survey Definition out of service, so callers must opt in
    explicitly and surface a confirmation to the user.
    """
    global _reinitializing

    # One pass at a time in this process, whoever calls (the route, the automatic loop, the CLI). Non-blocking: a
    # pass that finds it held does nothing and says so, and must not touch `_reinitializing`, which belongs to the
    # winner (the loser used to clear it in its finally).
    if not _heal_lock.acquire(blocking=False):
        return {"batches": {}, "running": True}
    try:
        return _check_and_heal_locked(docs_dir, force)
    finally:
        _heal_lock.release()


def _check_and_heal_locked(docs_dir: Path, force: bool) -> dict:
    global _reinitializing

    batches = discover_batches(docs_dir)
    results: dict[str, dict] = {}
    _reinitializing = True
    try:
        for batch in batches:
            with _status_lock:
                st = _status.setdefault(batch.batch_id, BatchStatus())

            if st.consecutive_failures >= MAX_CONSECUTIVE_FAILURES and not force:
                results[batch.batch_id] = {"action": "skipped", "reason": "too many consecutive failures"}
                continue

            # Tri-state: True/False are verified answers, None means the check
            # could not reach Egeria. None MUST be handled explicitly — a bare
            # `if present:` would treat it as falsy and heal during an outage,
            # precisely the behaviour failing open exists to prevent.
            state = False if force else canary_present(batch)
            now = _now()
            with _status_lock:
                st.last_checked_at = now
                if state is None:
                    st.last_check_error = "could not reach Egeria"
                    # Leave st.present untouched: the previous verified answer
                    # is still the best information available, and overwriting
                    # it with a guess is what made a wiped catalog look healthy.
                else:
                    st.last_check_error = ""
                    st.last_verified_at = now
                    if not force:
                        st.present = state

            if state is None:
                results[batch.batch_id] = {"action": "unknown", "reason": "could not reach Egeria"}
                continue

            if state:
                results[batch.batch_id] = {"action": "ok", "reason": "canary present"}
                continue

            log.info("bootstrap: %s missing — healing (%d file(s))", batch.batch_id, len(batch.files))
            ok, detail = heal_batch(batch)
            # Re-check rather than assuming the heal worked: a canary that never
            # resolves is exactly how an infinite heal loop starts.
            recheck = canary_present(batch) if ok else False
            with _status_lock:
                st.last_healed_at = _now()
                st.last_heal_result = "ok" if ok else detail
                # The guard has to key off the *verified* outcome (recheck),
                # not whether dr_egeria's process exited zero (ok) — those are
                # different questions. A batch can heal "successfully" every
                # cycle (ok=True) while its canary never actually resolves
                # (recheck=False), which is precisely the infinite-heal-loop
                # case this counter exists to catch (module docstring). Keying
                # on `ok` instead reset the counter to 0 on every such cycle
                # and the guard never fired — confirmed live 2026-09-07 on a
                # deployment where foundations/survey-definitions/governance-
                # metrics re-healed every ~10 minutes for 6+ hours straight,
                # each cycle re-running all 10 (idempotent=false) Survey
                # Definition documents, relying entirely on post_heal dedup to
                # clean up the resulting duplicate step links every time.
                # recheck is None (heal ran, but Egeria was unreachable for
                # the verification call) is deliberately left unchanged —
                # matching this module's tri-state policy that "could not
                # determine" is not evidence of success or failure.
                if recheck is True:
                    st.consecutive_failures = 0
                elif recheck is False:
                    st.consecutive_failures += 1
                if recheck is None:
                    st.last_check_error = "could not reach Egeria"
                else:
                    st.last_check_error = ""
                    st.last_verified_at = _now()
                    st.present = recheck
            results[batch.batch_id] = {"action": "healed" if ok else "failed", "reason": detail}
    finally:
        _reinitializing = False

    return {"batches": results}


def get_status(docs_dir: Path = DOCS_DIR) -> dict:
    """Status for the admin banner / status endpoint.

    `present` is the last *verified* answer and may be stale; `reachable` says
    whether the most recent attempt could actually reach Egeria, and
    `last_verified_at` says when `present` was last known to be true of the
    real server. Consumers must show a stale answer as stale rather than as
    fact — reporting an unreachable check as healthy is the specific bug this
    shape exists to prevent.
    """
    out = {}
    any_unreachable = False
    for batch in discover_batches(docs_dir):
        with _status_lock:
            st = _status.get(batch.batch_id, BatchStatus())
        reachable = not st.last_check_error
        any_unreachable = any_unreachable or not reachable
        out[batch.batch_id] = {
            "display_name": batch.display_name,
            "present": st.present,
            "reachable": reachable,
            "last_checked_at": st.last_checked_at,
            "last_verified_at": st.last_verified_at,
            "last_check_error": st.last_check_error,
            "last_healed_at": st.last_healed_at,
            "last_heal_result": st.last_heal_result,
            "consecutive_failures": st.consecutive_failures,
            "idempotent": batch.idempotent,
            "file_count": len(batch.files),
        }
    with _prefect_pool_status_lock:
        pool_st = _prefect_pool_status

    from resource_explorer.investigation_purposes import status as _purposes_status
    from resource_explorer.surveyors.database.bootstrap_data_classes import status as _dc_status

    return {
        "reinitializing": _reinitializing,
        "egeria_reachable": not any_unreachable,
        "batches": out,
        # The data-class seed's last outcome ("data classes: present, 6 sets" / "created, …" /
        # "could not check · why").
        "data_classes": _dc_status(),
        # The investigation-purpose seed's last outcome (`investigation_purposes.py`).
        "investigation_purposes": _purposes_status(),
        # 2026-09-28 — resource-explorer-pool worker liveness, reported
        # alongside the Dr.Egeria batch status the admin banner already
        # reads, so a missing/offline Prefect worker is a monitored
        # condition rather than something discovered by a confusing run
        # failure or silent local fallback.
        "prefect_pool": {
            "reachable": pool_st.reachable,
            "worker_count": pool_st.worker_count,
            "last_heartbeat": pool_st.last_heartbeat,
            "last_checked_at": pool_st.last_checked_at,
            "last_check_error": pool_st.last_check_error,
        },
    }


# ── data classes ────────────────────────────────────────────────────────────

def _data_class_pass(registry=None):
    """RE's data-class seed (`surveyors/database/bootstrap_data_classes.py`): its own canary (one
    typed read), healed only when absent, outcome logged and recorded as an activity row when it
    changes. Never raises."""
    try:
        from resource_explorer.surveyors.database.bootstrap_data_classes import (
            check_and_heal_data_classes,
        )

        return check_and_heal_data_classes(registry=registry)
    except Exception:  # pragma: no cover - check_and_heal_data_classes never raises
        log.exception("bootstrap: data-class pass failed")
        return None


def _purposes_pass(registry=None):
    """The investigation-purposes list (`investigation_purposes.py`): Egeria holds it (owner ruling
    2026-10-13); this pass creates the set and its values when missing, under its own canary, and refreshes
    the reader's cache. Never raises."""
    try:
        from resource_explorer.investigation_purposes import check_and_heal_purposes

        return check_and_heal_purposes(registry=registry)
    except Exception:  # pragma: no cover - check_and_heal_purposes never raises
        log.exception("bootstrap: investigation-purposes pass failed")
        return None


# ── scheduler ───────────────────────────────────────────────────────────────

def _loop(docs_dir: Path, interval: int, stop: threading.Event) -> None:
    from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

    while not stop.is_set():
        try:
            with acting_as(Daemon(DaemonReason.BOOTSTRAP)):
                check_and_heal(docs_dir)
        except Exception:
            # Never let a bad pass kill the loop — the next tick should still
            # get a chance to notice and repair a reset.
            log.exception("bootstrap: check pass failed")
        # After the Dr.Egeria batches: the data-class seed. Leader-elected with this loop
        # (LOCK_BOOTSTRAP), so it runs at start and after every Egeria reset, once per deployment.
        _data_class_pass()
        _purposes_pass()
        try:
            check_prefect_pool_workers()
        except Exception:
            # Same tolerance as the batch-heal pass above — a worker-liveness
            # check that itself throws must not stop future ticks either.
            log.exception("bootstrap: Prefect pool worker check failed")
        stop.wait(interval)


def start_scheduler(docs_dir: Path = DOCS_DIR, interval: int = CHECK_INTERVAL_SECONDS) -> None:
    """Start the background check loop. Never blocks or fails startup.

    A daemon thread rather than an asyncio task, matching scheduler.py's
    existing precedent for request-independent background work in this codebase,
    and because the heal itself is blocking subprocess work.
    """
    global _stop_event, _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop_event = threading.Event()
    _thread = threading.Thread(
        target=_loop,
        args=(docs_dir, interval, _stop_event),
        daemon=True,
        name="resource-explorer-bootstrap",
    )
    _thread.start()
    log.info("bootstrap: monitor started (every %ss)", interval)


def stop_scheduler() -> None:
    global _stop_event, _thread
    if _stop_event is not None:
        _stop_event.set()
    _thread = None
