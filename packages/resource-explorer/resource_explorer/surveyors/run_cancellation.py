"""TR-62b: a step whose flow run was cancelled stops, and writes nothing more.

On hedwig (2026-11-18, run fbfab6f3) RE's deadline cancelled a dispatched step. The process went on running, and
68 s later it wrote its flow_run_results row and set COMPLETED over CANCELLED. Two defences, both here:

  - the cancel itself is now a request for CANCELLING, which the worker acts on by stopping the process
    (prefect_adapter.request_cancel);
  - a second line that does not depend on the worker. A flow run installs a check (`watching`), and every write site
    on the step path calls `raise_if_cancelled(where)` immediately before it writes. Outside a flow run nothing is
    installed and the call is a no-op: an in-process or CLI run has nothing to be cancelled by.

`RunCancelled` derives from BaseException, like asyncio.CancelledError, on purpose. A runner's broad
`except Exception` (FileInventorySurveyor.run, the orchestrator's per-step try) would otherwise turn the cancel into
one more failed annotation and carry on to the next write. The flow catches it by name and ends Cancelled.

The check is check-then-write; a cancel landing between the two is the accepted race (brief TR-62b, point 2).
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator

log = logging.getLogger(__name__)

#: Prefect state types that mean "this run has been told to stop".
CANCEL_STATES = frozenset({"CANCELLING", "CANCELLED"})


class RunCancelled(BaseException):
    """The flow run this step belongs to was cancelled; nothing more is written."""

    def __init__(self, flow_run_id: str, where: str) -> None:
        self.flow_run_id = flow_run_id
        self.where = where
        super().__init__(f"flow run {flow_run_id or '(unknown)'} was cancelled; stopped before {where}")


_CHECK: ContextVar[Callable[[], bool] | None] = ContextVar("re_run_cancel_check", default=None)
_RUN: ContextVar[str] = ContextVar("re_run_cancel_flow_run", default="")


@contextmanager
def watching(flow_run_id: str, is_cancelled: Callable[[], bool]) -> Iterator[None]:
    """Inside this scope, `raise_if_cancelled` asks `is_cancelled()` and raises when it says so."""
    token = _CHECK.set(is_cancelled)
    rtoken = _RUN.set(str(flow_run_id or ""))
    try:
        yield
    finally:
        _RUN.reset(rtoken)
        _CHECK.reset(token)


def raise_if_cancelled(where: str) -> None:
    """Raise RunCancelled if the current flow run has been cancelled. Call it immediately before a write."""
    check = _CHECK.get()
    if check is not None and check():
        raise RunCancelled(_RUN.get(), where)


def state_type_name(state) -> str:
    """A Prefect state's type as an upper-case name ("" for none)."""
    kind = getattr(state, "type", None)
    return str(getattr(kind, "value", kind) or "").upper()


def prefect_run_is_cancelled(flow_run_id: str, *, client_factory=None, _warned: list | None = None) -> bool:
    """True when flow run `flow_run_id` is CANCELLING or CANCELLED, read through a SYNC Prefect client of its own.

    A failed read answers False (the step goes on, as it did before this check existed) and is logged once per
    `_warned` list, never per call: an unreachable server must not stop a run nobody cancelled."""
    if not flow_run_id:
        return False
    try:
        if client_factory is None:
            from resource_explorer.surveyors.prefect_adapter import re_prefect_sync_client as client_factory
        from uuid import UUID

        with client_factory() as client:
            run = client.read_flow_run(UUID(str(flow_run_id)))
        return state_type_name(getattr(run, "state", None)) in CANCEL_STATES
    except Exception as exc:  # never kills the step
        if _warned is None or not _warned:
            log.warning("could not read the state of flow run %s (cancel check skipped): %s: %s",
                        flow_run_id, type(exc).__name__, exc)
            if _warned is not None:
                _warned.append(True)
        return False
