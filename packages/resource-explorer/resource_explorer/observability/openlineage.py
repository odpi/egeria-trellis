"""OpenLineage run events for every RE step run (requirement R21).

`step_cost_observer.observe()` is the one place every step passes through --
the local loop, the orchestrator, an accepted prerequisite and the Prefect
task all use it -- so a START event is emitted when the step begins and a
COMPLETE or FAIL when it ends, built-in and external steps alike.

Egeria already listens: its Integration Daemon accepts OpenLineage events
and hands them to the connectors registered for them. See
docs/analytic-standards-comparison.md ("OpenLineage run events") for how to
turn it on and the exact URL.

**Default sends nothing.** `RE_OPENLINEAGE_TRANSPORT`:

* ``off`` (default) -- no event is built, written or sent;
* ``file`` -- one JSON object per line under ``RE_OPENLINEAGE_DIR``;
* ``http`` -- POST to ``RE_OPENLINEAGE_URL`` (optional bearer token in
  ``RE_OPENLINEAGE_TOKEN``), bounded by ``RE_OPENLINEAGE_TIMEOUT_S`` (2s).

**Emission never breaks or slows a step**, and a failed emission is never
silent: it is counted on the run's state, which `step_cost_observer` copies
onto the `step_runs` row's `metrics` (``openlineage``), and logged.

The events are built by hand from the 2-0-2 spec with schemaURLs, rather
than with the OpenLineage client: the client is not in the workspace lock,
and a RunEvent is a dozen fields. Nothing here ever reads a credential: the
input dataset is built from an allow-list of host/port/database/URL fields,
and any URL is stripped of userinfo and query before it is used.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import threading
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

PRODUCER = "https://github.com/odpi/egeria-trellis/tree/main/packages/resource-explorer"
ENGINE_NAME = "resource-explorer"
JOB_NAMESPACE = "resource-explorer"
_SPEC = "https://openlineage.io/spec"
RUN_EVENT_SCHEMA = f"{_SPEC}/2-0-2/OpenLineage.json#/$defs/RunEvent"
_FACET = f"{_SPEC}/facets/1-0-0"
NOMINAL_TIME_SCHEMA = f"{_FACET}/NominalTimeRunFacet.json#/$defs/NominalTimeRunFacet"
ERROR_MESSAGE_SCHEMA = f"{_FACET}/ErrorMessageRunFacet.json#/$defs/ErrorMessageRunFacet"
PROCESSING_ENGINE_SCHEMA = (
    f"{_SPEC}/facets/1-1-1/ProcessingEngineRunFacet.json#/$defs/ProcessingEngineRunFacet")

OFF, FILE, HTTP = "off", "file", "http"
_FILE_LOCK = threading.Lock()


def engine_version() -> str:
    try:
        from importlib.metadata import version

        return version("resource-explorer")
    except Exception:  # not installed as a distribution; say so, don't invent
        return "unknown"


def transport() -> str:
    if _suppressed.get():
        return OFF
    t = (os.environ.get("RE_OPENLINEAGE_TRANSPORT") or OFF).strip().lower()
    return t if t in (FILE, HTTP) else OFF


#: True inside `suppressed()`: work Egeria dispatched (an engine action RE runs as an engine host) emits no
#: R21 event. Egeria reports that work's lineage from the engine action's own status changes (the Egeria
#: leads, 2026-10-14, Q-M6: "RE's own R21 OpenLineage events stay off for Egeria-dispatched work").
_suppressed: contextvars.ContextVar[bool] = contextvars.ContextVar("re_openlineage_suppressed", default=False)


class suppressed:  # noqa: N801 - used as `with openlineage.suppressed():`
    """No R21 event is emitted for steps run inside this block (mode `off`, as when the transport is unset)."""

    def __enter__(self):
        self._token = _suppressed.set(True)
        return self

    def __exit__(self, *exc_info):
        _suppressed.reset(self._token)
        return False


def _timeout() -> float:
    try:
        return max(0.1, float(os.environ.get("RE_OPENLINEAGE_TIMEOUT_S", "2")))
    except ValueError:
        return 2.0


@dataclass
class RunState:
    """One step run's OpenLineage state. `summary()` is what lands on the
    `step_runs` row, so a missing event is visible rather than inferred."""
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    job_name: str = ""
    dataset: dict = field(default_factory=dict)
    started_at: str = ""
    failed_message: str = ""     # set by note_failure() or by a raised exception
    emitted: int = 0
    failed: int = 0
    last_error: str = ""
    mode: str = OFF

    def summary(self) -> dict:
        d = {"run_id": self.run_id, "transport": self.mode,
             "emitted": self.emitted, "failed": self.failed}
        if self.last_error:
            d["last_error"] = self.last_error
        return d


_current: contextvars.ContextVar[RunState | None] = contextvars.ContextVar(
    "re_openlineage_run", default=None)


def note_failure(message: str) -> None:
    """Mark the innermost running step as failed. For callers that catch the
    step's exception themselves (so `observe()` never sees it raise): without
    this a step that failed would be reported COMPLETE."""
    st = _current.get()
    if st is not None:
        st.failed_message = scrub(message) or "step failed"


def current() -> RunState | None:
    return _current.get()


_USERINFO = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^/\s@]*@")
_SECRETISH = re.compile(r"(?i)\b(password|passwd|pwd|token|secret|api[_-]?key)\b\s*[=:]\s*\S+")


def scrub(text: object, limit: int = 500) -> str:
    """Strip userinfo from URLs and key=value secrets from free text."""
    s = _SECRETISH.sub(lambda m: f"{m.group(1)}=***", _USERINFO.sub(r"\1", str(text or "")))
    return s[:limit]


def _clean_url(url: str) -> tuple[str, str]:
    """(scheme://host[:port], path) with userinfo, query and fragment dropped."""
    p = urlsplit(url or "")
    host = p.hostname or ""
    netloc = host + (f":{p.port}" if p.port else "")
    return f"{p.scheme}://{netloc}" if host else "", p.path.strip("/")


_DB_SCHEMES = {"postgresql": "postgres", "postgres": "postgres"}


def input_dataset(entity_type: str, slug: str, resource: object = None) -> dict:
    """The resource surveyed, as an OpenLineage dataset reference.

    Allow-list only: URL / host / port / database name / mount point. Never
    the user, password, token or connection_ref. Schema and table are not
    known at step level, so the dataset is the database (or repo) itself.
    """
    try:
        if entity_type == "repo":
            base, path = _clean_url(getattr(resource, "github_url", "") or "")
            if base and path:
                return {"namespace": base, "name": path}
        elif entity_type == "database":
            host = getattr(resource, "host", "") or ""
            port = getattr(resource, "port", "") or ""
            name = getattr(resource, "database_name", "") or ""
            if host and name:
                scheme = _DB_SCHEMES.get(
                    (getattr(resource, "db_type", "") or "postgresql").lower(),
                    (getattr(resource, "db_type", "") or "db").lower())
                return {"namespace": f"{scheme}://{host}" + (f":{port}" if port else ""),
                        "name": name}
        elif entity_type == "filesystem":
            mount = (getattr(resource, "canonical_mount_point", "")
                     or getattr(resource, "local_mount_point", "") or "")
            if mount:
                return {"namespace": "file", "name": mount}
    except ValueError as exc:  # unparseable URL/port: named by slug, loudly
        log.warning("openlineage: could not describe %s/%s: %s", entity_type, slug, scrub(exc))
    return {"namespace": JOB_NAMESPACE, "name": f"{entity_type or 'resource'}/{slug or 'unknown'}"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_event(event_type: str, st: RunState, *, event_time: str,
                ended_at: str = "", error: str = "") -> dict:
    nominal = {"_producer": PRODUCER, "_schemaURL": NOMINAL_TIME_SCHEMA,
               "nominalStartTime": st.started_at}
    if ended_at:
        nominal["nominalEndTime"] = ended_at
    facets = {
        "nominalTime": nominal,
        "processing_engine": {
            "_producer": PRODUCER, "_schemaURL": PROCESSING_ENGINE_SCHEMA,
            "version": engine_version(), "name": ENGINE_NAME,
            "openlineageAdapterVersion": "none"},
    }
    if error:
        facets["errorMessage"] = {
            "_producer": PRODUCER, "_schemaURL": ERROR_MESSAGE_SCHEMA,
            "message": error, "programmingLanguage": "python"}
    return {
        "eventType": event_type, "eventTime": event_time, "producer": PRODUCER,
        "schemaURL": RUN_EVENT_SCHEMA,
        "run": {"runId": st.run_id, "facets": facets},
        "job": {"namespace": JOB_NAMESPACE, "name": st.job_name},
        "inputs": [dict(st.dataset)] if st.dataset else [],
        "outputs": [],
    }


def _send(event: dict, mode: str) -> None:
    """Deliver one event. Raises on failure; the caller counts it."""
    line = json.dumps(event, separators=(",", ":"))
    if mode == FILE:
        d = os.environ.get("RE_OPENLINEAGE_DIR", "").strip()
        if not d:
            raise RuntimeError("RE_OPENLINEAGE_TRANSPORT=file but RE_OPENLINEAGE_DIR is not set")
        path = Path(d)
        path.mkdir(parents=True, exist_ok=True)
        with _FILE_LOCK, open(path / "openlineage-events.jsonl", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return
    url = os.environ.get("RE_OPENLINEAGE_URL", "").strip()
    if not url:
        raise RuntimeError("RE_OPENLINEAGE_TRANSPORT=http but RE_OPENLINEAGE_URL is not set")
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("RE_OPENLINEAGE_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=line.encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=_timeout()) as resp:  # noqa: S310
        if resp.status >= 300:
            raise RuntimeError(f"HTTP {resp.status}")


def emit(event_type: str, st: RunState, *, ended_at: str = "", error: str = "") -> bool:
    """Send one event; never raises. Failure is counted on `st` and logged."""
    if st.mode == OFF:
        return False
    try:
        _send(build_event(event_type, st, event_time=ended_at or _now(),
                          ended_at=ended_at, error=error), st.mode)
        st.emitted += 1
        return True
    except Exception as exc:
        st.failed += 1
        st.last_error = scrub(f"{type(exc).__name__}: {exc}", 200)
        log.warning("openlineage: %s event for %s (run %s) not delivered via %s: %s",
                    event_type, st.job_name, st.run_id, st.mode, st.last_error)
        return False


def begin(job_family: str, step_key: str, entity_type: str, slug: str,
          resource: object = None) -> tuple[RunState, contextvars.Token]:
    """Open a run: set it current and emit START. Always returns a state (mode
    ``off`` when disabled) so the run id is stored either way."""
    st = RunState(job_name=f"{job_family}.{step_key}", mode=transport(), started_at=_now())
    if st.mode != OFF:
        st.dataset = input_dataset(entity_type, slug, resource)
    token = _current.set(st)
    emit("START", st)
    return st, token


def finish(st: RunState, token: contextvars.Token, *, raised: BaseException | None) -> None:
    """Close a run: emit FAIL if the step raised (or noted a failure), else COMPLETE."""
    _current.reset(token)
    message = scrub(f"{type(raised).__name__}: {raised}") if raised is not None else st.failed_message
    ended = _now()
    if message:
        emit("FAIL", st, ended_at=ended, error=message)
    else:
        emit("COMPLETE", st, ended_at=ended)
