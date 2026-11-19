"""TR-62a: a dispatched step says it is alive, and what it is doing, at least every 30 s.

On hedwig (2026-11-18) repo_file_inventory sat about 21 minutes idle on the worker and RE's timeout line could say
only "(state Running)". Nobody could tell where it hung. Now, while a step runs in a flow run:

  - a daemon thread writes one Prefect log line for the flow run every HEARTBEAT_SECONDS, "RE heartbeat · <phase> ·
    <n> s into the step", through a SYNC client of its own (never the flow's async client or loop). The flow run's
    own logs are the store, so nothing new is deployed. Each beat also reads the run's state and sets `cancelled` at
    CANCELLING/CANCELLED (TR-62b's checks read it). A failed write or read is logged once and never kills the step;
    a failed read leaves the flag unset;
  - the step names its phase where it matters (`phase("downloading")`, "inventorying", "waiting for GitHub rate
    limit"); elsewhere the phase is "running" and the elapsed time says the rest;
  - RE's waiter, on a timeout, reads the newest heartbeat for that run id and adds ONE clause to its existing line:
    "· last heard 23:24:05Z (downloading)", or "· no heartbeat received" when there was none (never a blank, never
    "0 s ago"), or "· heartbeat unreadable (...)" when it could not ask. TR-71: no new panel or row.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Callable, Iterator

log = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 30.0
HEARTBEAT_PREFIX = "RE heartbeat"
LOGGER_NAME = "resource_explorer.step_heartbeat"
DEFAULT_PHASE = "running"


def _sync_client():
    from prefect.client.orchestration import get_client

    return get_client(sync_client=True)


class Heartbeat:
    """One step's heartbeat thread and the state it shares with the step."""

    def __init__(self, flow_run_id: str, *, interval: float = HEARTBEAT_SECONDS,
                 client_factory: Callable | None = None, clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], datetime] | None = None) -> None:
        self.flow_run_id = str(flow_run_id)
        self.interval = float(interval)
        self.phase_word = DEFAULT_PHASE
        self.cancelled = threading.Event()
        self.beats = 0
        self._client_factory = client_factory or _sync_client
        self._clock = clock
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._t0 = clock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._warned: set[str] = set()

    def message(self) -> str:
        return f"{HEARTBEAT_PREFIX} · {self.phase_word} · {int(self._clock() - self._t0)} s into the step"

    def _warn_once(self, what: str, exc: BaseException) -> None:
        if what not in self._warned:
            self._warned.add(what)
            log.warning("step heartbeat for flow run %s: could not %s (%s: %s); the step goes on",
                        self.flow_run_id, what, type(exc).__name__, exc)

    def beat(self) -> None:
        """Write one heartbeat line and read the run's state. Never raises."""
        from uuid import UUID

        from resource_explorer.surveyors.run_cancellation import CANCEL_STATES, state_type_name

        try:
            run_id = UUID(self.flow_run_id)
        except ValueError as exc:
            self._warn_once("parse the flow run id", exc)
            return
        try:
            with self._client_factory() as client:
                try:
                    from prefect.client.schemas.actions import LogCreate

                    client.create_logs([LogCreate(name=LOGGER_NAME, level=logging.INFO, message=self.message(),
                                                  timestamp=self._now(), flow_run_id=run_id)])
                    self.beats += 1
                except Exception as exc:
                    self._warn_once("write the heartbeat", exc)
                try:
                    run = client.read_flow_run(run_id)
                    if state_type_name(getattr(run, "state", None)) in CANCEL_STATES:
                        self.cancelled.set()
                except Exception as exc:
                    self._warn_once("read the run's state", exc)
        except Exception as exc:
            self._warn_once("open a Prefect client", exc)

    def _loop(self) -> None:
        while True:
            self.beat()
            if self._stop.wait(self.interval):
                return

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name=f"re-heartbeat-{self.flow_run_id[:8]}",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval + 10.0)

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


_CURRENT: ContextVar[Heartbeat | None] = ContextVar("re_step_heartbeat", default=None)


@contextmanager
def running(flow_run_id: str, *, interval: float = HEARTBEAT_SECONDS,
            client_factory: Callable | None = None) -> Iterator[Heartbeat | None]:
    """Beat for flow run `flow_run_id` while the block runs; stopped and joined on the way out, whatever happens.
    No flow run id (CLI, tests with no Prefect context): no thread, yields None."""
    if not flow_run_id:
        yield None
        return
    hb = Heartbeat(flow_run_id, interval=interval, client_factory=client_factory)
    hb.start()
    token = _CURRENT.set(hb)
    try:
        yield hb
    finally:
        _CURRENT.reset(token)
        hb.stop()


@contextmanager
def phase(word: str) -> Iterator[None]:
    """Name what the step is doing for the heartbeat; restored on exit. A no-op outside a heartbeat."""
    hb = _CURRENT.get()
    if hb is None:
        yield
        return
    previous, hb.phase_word = hb.phase_word, word
    try:
        yield
    finally:
        hb.phase_word = previous


def current() -> Heartbeat | None:
    return _CURRENT.get()


def _parse(message: str) -> str:
    """The phase in a heartbeat line ("" when it is not one)."""
    parts = [p.strip() for p in (message or "").split(" · ")]
    return parts[1] if len(parts) >= 2 and parts[0] == HEARTBEAT_PREFIX else ""


async def last_heard_words(client, flow_run_id) -> str:
    """The one clause RE's timeout line carries about the step's heartbeat. Never raises, never blank."""
    # Newest first, by run id only (no server-side text search: the server may be older than this client). A chatty
    # step can bury its last beat, so up to _PAGES pages are read before saying none was found in them.
    page, scanned = 200, 0
    try:
        from uuid import UUID

        from prefect.client.schemas.filters import LogFilter, LogFilterFlowRunId
        from prefect.client.schemas.sorting import LogSort

        log_filter = LogFilter(flow_run_id=LogFilterFlowRunId(any_=[UUID(str(flow_run_id))]))
        for n in range(_PAGES):
            logs = await client.read_logs(log_filter=log_filter, limit=page, offset=n * page,
                                          sort=LogSort.TIMESTAMP_DESC)
            for entry in logs or []:
                word = _parse(getattr(entry, "message", ""))
                if word:
                    ts = getattr(entry, "timestamp", None)
                    when = ts.astimezone(timezone.utc).strftime("%H:%M:%SZ") if isinstance(ts, datetime) else "?"
                    return f" · last heard {when} ({word})"
            scanned += len(logs or [])
            if len(logs or []) < page:
                break
    except Exception as exc:
        return f" · heartbeat unreadable ({type(exc).__name__})"
    if scanned >= _PAGES * page:
        return f" · no heartbeat in the run's last {scanned} log lines"
    return " · no heartbeat received"


_PAGES = 5
