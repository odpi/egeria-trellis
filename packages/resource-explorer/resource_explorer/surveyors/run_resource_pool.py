"""Fetch-once sharing of a survey's source tree across the steps of ONE run (F-AB-2).

`SurveyOrchestrator.run()` already shares a zipball across every step of one
call (`trellis_microflow.resolve_resources`). A Prefect-ordered Survey Definition
(`prefect/flows.py::re_survey_definition_flow`) does not make one call: each
planned step is its own task, which builds its own orchestrator for one step,
so before this every step re-acquired the tree — two GitHub API calls (repo
lookup, default-branch SHA) and an extraction each, measured 2026-11-17 on a
3-step run.

A pool is opened for the length of a run (`run_resource_scope`) and holds each
acquired resource open until the run ends. The first step to ask acquires it,
inside its own measurement scopes, so the fetch is attributed to that step; a
later step gets the same root and `acquisition.record_reuse`, so its row says
`reused` rather than looking like a step that asked for nothing.

**Keyed on (resource, repository) for the run, not on the SHA.** The SHA is only
known after the first lookup, and within one run pinning it is the point: every
step of the run reads the same commit, so a push mid-run cannot split one
survey across two snapshots. Across runs the on-disk `SourceCache` below this
is what is keyed on (repo, SHA).

**Same-process only.** The pool lives in a ContextVar, which Prefect copies into
the task threads of a flow run (`ThreadPoolTaskRunner` uses `copy_context`); the
copied context holds the same pool object. Separate flow runs — single-step
dispatch to a worker, one flow run per step — share nothing here; for those the
`SourceCache` keeps the download warm, and each still pays its own SHA lookup.

Only the zipball is pooled. A shared git clone is a corruption risk under
concurrent readers (`github/source_cache.py`), and planned steps can run
concurrently.
"""
from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from contextvars import ContextVar
from typing import Any


class RunResourcePool:
    """Resources acquired once and held open for the rest of a run."""

    def __init__(self) -> None:
        self._stack = ExitStack()
        self._entries: dict[tuple, Any] = {}
        self._locks: dict[tuple, threading.Lock] = {}
        self._guard = threading.Lock()
        #: How many times each key was actually acquired — 1 is the contract.
        self.acquisitions: dict[tuple, int] = {}

    def get_or_acquire(self, key: tuple, acquire: Callable[[], AbstractContextManager]) -> tuple[Any, bool]:
        """(value, reused). The first caller for `key` enters `acquire()` into
        the pool; concurrent callers wait for it rather than fetching too. A
        failed acquisition is not cached, so a later step may try again."""
        from resource_explorer.observability import acquisition

        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            if key in self._entries:
                acquisition.record_reuse(key[0] if key else "")
                return self._entries[key], True
            value = self._stack.enter_context(acquire())
            self._entries[key] = value
            self.acquisitions[key] = self.acquisitions.get(key, 0) + 1
            return value, False

    def close(self) -> None:
        self._stack.close()


_current: ContextVar[RunResourcePool | None] = ContextVar("re_run_resource_pool", default=None)


def current() -> RunResourcePool | None:
    return _current.get()


@contextmanager
def run_resource_scope() -> Iterator[RunResourcePool]:
    """Share acquired resources across the steps run inside this block. Nested
    scopes join the outer run's pool rather than starting a second one."""
    outer = _current.get()
    if outer is not None:
        yield outer
        return
    pool = RunResourcePool()
    token = _current.set(pool)
    try:
        yield pool
    finally:
        _current.reset(token)
        pool.close()
