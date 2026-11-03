"""One bounded shared thread pool per process, for bridging sync calls
(pyegeria's, BeeAI's) into and out of async code.

Why this module exists
----------------------
Before this, six separate `ThreadPoolExecutor`s were constructed ad hoc
across the package (``agents/base.py``, ``agents/conversation_agent.py``
x2, ``surveyors/survey_definition_reader.py`` x2, ``surveyors/
prefect_adapter.py``, plus ``tui/app.py``), five of them per-call and one
of them (``prefect_adapter.py``) with **no ``max_workers`` at all**. Each
was individually small; together they had no cap, no shared accounting,
and no single place to bound a call. ``docs/process-model.md`` §1.3 has
the full inventory and the incident that motivated the change:
``resolve_question_guid()`` hanging 15+ seconds inside pyegeria's
``get_guid_for_name`` -> ``nest_asyncio`` -> ``run_until_complete`` frame
on 2026-09-03/04. (pyegeria applied nest_asyncio then; since 6.2.16, egeria-python
#513, it does not, and its sync methods run on a per-thread loop through its own
``run_sync``. The bound here is kept: it never depended on how pyegeria waits.)

What replaces them: **one** pool per process, sized from config
(``EXPLORER_SYNC_POOL_SIZE``, default 8), with the per-call timeout that
contained that incident kept as defense in depth rather than dropped
because the pool is now shared.

The re-entrancy rule
--------------------
A shared bounded pool has a failure mode the throwaway pools did not: a
task that itself calls :func:`run_sync` can wait on a slot that only a
task behind it in the queue could free — a self-deadlock that appears
only under load. ``_resolve_question_guids`` was exactly this shape (a
pool of workers each opening its own one-worker pool).

Two things prevent it here:

1. Batch callers submit **leaf** work with :func:`submit_all`, never work
   that re-enters the pool.
2. :func:`run_sync` detects that it is *already running on a pool thread*
   (``_IN_POOL``) and runs the callable inline on that thread instead of
   submitting. Inline execution loses the timeout (there is no second
   thread to abandon), which is the honest trade: an unbounded wait in
   one already-owned slot beats a deadlock that stops every slot.

Abandoned workers, and why process exit needs help
--------------------------------------------------
On timeout the future is cancelled if it has not started; if it has, the
worker thread is *abandoned* — Python threads cannot be force-killed, and
joining one blocked in Egeria's client is precisely the hang being
avoided. Unlike the old throwaway pools (``shutdown(wait=False)`` and the
whole pool went with it), an abandoned worker here holds a slot in the
shared pool for as long as it stays stuck. :func:`stuck_worker_count`
reports how many, so the condition is observable rather than inferred
from things getting slow.

Abandonment is not free at interpreter shutdown, and this is the part
that bites. ``concurrent.futures.thread`` registers ``_python_exit`` with
``threading._register_atexit``, and that handler **joins every pool
worker**, whether or not the executor was shut down with ``wait=False``;
``threading._shutdown`` then waits on the same threads' shutdown locks
because pool workers are not daemon threads. So one worker stuck in
pyegeria's ``get_guid_for_name`` frame holds the whole process open —
forever, past every timeout, past a clean SIGTERM.

Observed live 2026-09-04 while verifying the worker role: the worker
logged "worker role stopped cleanly", released its advisory locks, and
then did not exit. The SIGUSR1 dump showed the main thread parked in
``_python_exit -> join`` behind two workers in the exact
``get_guid_for_name -> nest_asyncio -> select()`` frame from the
2026-09-03/04 incident. This is not new — the old per-call pools were
non-daemon threads in ``_threads_queues`` too, so
``shutdown(wait=False)`` never actually detached them either. It is
plausibly part of why that incident needed a ``kill -9`` after SIGTERM
was ignored for 8+ seconds.

Two things make abandonment real here:

* the pool's workers are **daemon threads**, so CPython's
  ``_thread_shutdown()`` does not wait for them. There is no supported
  way to daemonise a thread after it starts, and none to un-register it
  from that wait — but ``threading.Thread`` inherits ``daemon`` from the
  thread that *creates* it, and ``ThreadPoolExecutor`` creates its
  workers on whichever thread happens to submit. So :func:`get_pool`
  spawns all of them up front from one daemon thread
  (``_spawn_daemon_workers``) rather than letting them be created
  lazily by a request thread.
* :func:`detach_workers` removes them from
  ``concurrent.futures.thread._threads_queues``, which is what
  ``_python_exit`` iterates to join. That name is private and the call
  is guarded: if a future CPython renames it, exit degrades to the old
  join-and-hang rather than raising.
"""
from __future__ import annotations

import atexit
import contextvars
import logging
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any, Callable, Iterable, Sequence, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

#: Fallback when config is unreadable (a bare unit test, a broken .env).
DEFAULT_SYNC_POOL_SIZE = 8

_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()
_pool_size: int = 0

#: Set on a thread owned by the shared pool. Read by run_sync to decide
#: submit-vs-inline; see "The re-entrancy rule" above.
_IN_POOL = threading.local()

_stuck = 0
_stuck_lock = threading.Lock()

#: Set when the pool size came from the fallback because config could not be
#: read. Without it, "8 because that is what config says" and "8 because we
#: could not tell" are the same observable state — the exact shape
#: tests/test_no_silent_success.py exists to stop.
_size_source = "unset"


def size_source() -> str:
    """Where the live pool size came from: ``"config"``, ``"env"``,
    ``"default"``, or ``"default-after-config-error"``. The last one is why
    this exists — a fallback that looks identical to a configured value is
    not a diagnosis."""
    return _size_source


def _configured_size() -> int:
    """Pool size from config, then env, then the default. Never raises —
    a process that cannot read its config still needs a working pool."""
    global _size_source
    try:
        from resource_explorer.config import get_config

        size = int(get_config().runtime.sync_pool_size)
        if size > 0:
            _size_source = "config"
            return size
    except Exception as exc:  # pragma: no cover - config is present in practice
        _size_source = "default-after-config-error"
        log.warning(
            "shared sync pool: could not read EXPLORER_SYNC_POOL_SIZE from "
            "config (%s: %s) — falling back", type(exc).__name__, exc,
        )
    try:
        size = int(os.environ.get("EXPLORER_SYNC_POOL_SIZE", "") or 0)
        if size > 0:
            if _size_source != "default-after-config-error":
                _size_source = "env"
            return size
    except ValueError:
        pass
    if _size_source != "default-after-config-error":
        _size_source = "default"
    return DEFAULT_SYNC_POOL_SIZE


def _run_in_callers_context(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """The pool-side half of :class:`_CallersContextPool`: runs inside a COPY of the submitter's context.

    The per-request Egeria client cache (`egeria_clients.client_scope`) is the one thing not carried
    across: a pyegeria client binds its HTTP pool to the event loop of the thread that first drives it
    (pyegeria ISSUE-96), so a pool thread builds its own, as it always did."""
    try:
        from resource_explorer.egeria_clients import _scope
    except Exception:  # pragma: no cover - import cycle safety only
        return fn(*args, **kwargs)
    _scope.set(None)            # only this copy of the context; the submitter's scope is untouched
    return fn(*args, **kwargs)


class _CallersContextPool(ThreadPoolExecutor):
    """The shared pool, with the SUBMITTER's ContextVars carried into the worker (caller identity, 2026-10-14).

    A bare pool thread starts with an empty context. RE's identity lives in ContextVars
    (`a2a_auth.current_caller`, `egeria_clients.acting_as`), and a registry read that finds no caller
    reads as the shared/service identity, which sees every private investigation (`_may_see_investigation`
    returns True for ''). So any scoped registry read made from a pool job read as "see everything" (found in
    review: `GET /api/analyses/facts` fans out on this pool; the investigation-naming reader it was suspected of
    is reached through `/facts/{slug}/answer`, a sync route Starlette already runs in a copied context, but the
    pool path leaked for any such read). Copying the context
    in `submit` covers every way in (`submit`, `submit_all`, `run_sync`, `run_sync_all`, and a raw
    `get_pool().submit`), so a call site cannot forget. Each submission gets its own copy, so nothing a job
    sets outlives it and a worker holds no caller between jobs."""

    def submit(self, fn, /, *args, **kwargs):  # noqa: D401 - ThreadPoolExecutor's signature
        ctx = contextvars.copy_context()
        return super().submit(ctx.run, _run_in_callers_context, fn, *args, **kwargs)


def _mark_in_pool() -> None:
    _IN_POOL.value = True


def get_pool() -> ThreadPoolExecutor:
    """The process's one sync-bridging pool, built on first use."""
    global _pool, _pool_size
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            _pool_size = _configured_size()
            pool = _CallersContextPool(
                max_workers=_pool_size,
                thread_name_prefix="re-sync",
                initializer=_mark_in_pool,
            )
            _spawn_daemon_workers(pool, _pool_size)
            _pool = pool
            log.debug("shared sync pool started (max_workers=%d)", _pool_size)
    return _pool


def _spawn_daemon_workers(pool: ThreadPoolExecutor, size: int) -> None:
    """Create every worker up front, from a daemon thread.

    Not an optimisation — a correctness requirement for shutdown. See
    the module docstring: a non-daemon worker stuck in pyegeria holds the
    interpreter open for ever, and daemon-ness is fixed at thread start
    and inherited from the creating thread. Creating the workers here,
    on a thread that is itself a daemon, is the only way to get daemon
    pool workers without reimplementing ``_adjust_thread_count``.

    The barrier is what forces ``size`` DISTINCT threads: the executor
    only spawns a new worker when no idle one is available, so tasks that
    return immediately would be served by one thread over and over.
    Best-effort — on timeout the pool still works, with however many
    workers were created plus lazily-created non-daemon ones.
    """
    barrier = threading.Barrier(size + 1)

    def _park() -> None:
        try:
            barrier.wait(timeout=10)
        except Exception:
            pass

    def _spawn() -> None:
        for _ in range(size):
            try:
                pool.submit(_park)
            except Exception:  # pragma: no cover - pool already shut down
                break
        _park()

    spawner = threading.Thread(
        target=_spawn, name="re-sync-spawn", daemon=True)
    spawner.start()
    spawner.join(timeout=10)


def pool_size() -> int:
    """Configured worker count (0 before the pool is first used)."""
    return _pool_size


def in_pool_thread() -> bool:
    """True when the calling thread is one of the shared pool's own."""
    return getattr(_IN_POOL, "value", False)


def stuck_worker_count() -> int:
    """Workers abandoned by a :func:`run_sync` timeout and never observed
    to finish — each one permanently holds a slot. Non-zero here is the
    signal that the pool is shrinking under it."""
    return _stuck


def _note_stuck(delta: int) -> None:
    global _stuck
    with _stuck_lock:
        _stuck += delta


def submit(fn: Callable[..., T], *args: Any, **kwargs: Any) -> "Future[T]":
    """Submit leaf work to the shared pool. Prefer :func:`run_sync`."""
    return get_pool().submit(fn, *args, **kwargs)


def submit_all(
    fn: Callable[..., T], items: Iterable[Any]
) -> Sequence["Future[T]"]:
    """Submit one call per item, in order — the ``pool.map`` replacement
    for batch callers. Results stay positionally aligned with ``items``.

    The submitted callable must be **leaf** work: it must not itself call
    back into :func:`run_sync`/:func:`submit`. See the module docstring.
    """
    pool = get_pool()
    return [pool.submit(fn, item) for item in items]


def run_sync(
    fn: Callable[..., T], *args: Any, timeout: float | None = None, **kwargs: Any
) -> T:
    """Run ``fn`` on the shared pool and return its result.

    ``timeout`` (seconds) bounds the *caller's* wait, not the work: on
    expiry a :class:`concurrent.futures.TimeoutError` is raised and the
    worker is abandoned rather than joined.

    Called from a pool thread, ``fn`` runs inline instead — see the
    module docstring's re-entrancy rule. ``timeout`` does not apply in
    that case, because there is no second thread to abandon.
    """
    if in_pool_thread():
        return fn(*args, **kwargs)

    future = submit(fn, *args, **kwargs)
    try:
        return future.result(timeout=timeout)
    except FutureTimeoutError:
        if not future.cancel():
            # Already running, and unkillable. Account for the slot it
            # is holding, and stop accounting if it ever does finish.
            _note_stuck(1)
            future.add_done_callback(lambda _f: _note_stuck(-1))
            log.warning(
                "shared sync pool: %s did not return within %ss — abandoning "
                "the worker (%d slot(s) of %d now held by stuck work)",
                getattr(fn, "__name__", repr(fn)), timeout,
                stuck_worker_count(), _pool_size,
            )
        raise


def run_sync_all(
    fns: Sequence[Callable[[], T]], *, timeout: float | None = None
) -> list[tuple[bool, Any]]:
    """Run several **leaf** callables on the shared pool at once and wait for all of them.

    Returns one ``(ok, value)`` per callable, in order: ``(True, result)`` or ``(False, exception)``; a
    callable still running when ``timeout`` (seconds, the caller's whole wait) expires gives
    ``(False, concurrent.futures.TimeoutError())`` and its worker is abandoned and accounted exactly as
    :func:`run_sync` does. Nothing raises: each caller decides what a failed slot means.

    Called from a pool thread, the callables run inline, one after another (the re-entrancy rule; no
    timeout, as for :func:`run_sync`). Each callable must be leaf work: it must not call back into the pool.
    """
    import time as _time

    if in_pool_thread():
        out: list[tuple[bool, Any]] = []
        for fn in fns:
            try:
                out.append((True, fn()))
            except Exception as exc:  # noqa: BLE001 - returned, not raised: the caller decides
                out.append((False, exc))
        return out

    pool = get_pool()
    futures = [pool.submit(fn) for fn in fns]
    deadline = None if timeout is None else _time.monotonic() + timeout
    out = []
    for future in futures:
        left = None if deadline is None else max(0.0, deadline - _time.monotonic())
        try:
            out.append((True, future.result(timeout=left)))
        except FutureTimeoutError as exc:
            if not future.cancel():
                _note_stuck(1)
                future.add_done_callback(lambda _f: _note_stuck(-1))
                log.warning("shared sync pool: a batched call did not return within %ss — abandoning the "
                            "worker (%d slot(s) of %d now held by stuck work)",
                            timeout, stuck_worker_count(), _pool_size)
            out.append((False, exc))
        except Exception as exc:  # noqa: BLE001 - the callable's own failure, returned
            out.append((False, exc))
    return out


def detach_workers(pool: ThreadPoolExecutor) -> int:
    """Take this pool's threads out of the interpreter's exit joins.

    Returns how many were detached. See the module docstring: without
    this, ``wait=False`` is a lie at shutdown, because both
    ``concurrent.futures.thread._python_exit`` and ``threading._shutdown``
    join pool workers regardless.

    Deliberately narrow: only threads belonging to THIS pool, and every
    step guarded, so a CPython internals change costs the fast exit and
    nothing else.
    """
    detached = 0
    failed = 0
    try:
        from concurrent.futures import thread as _cf_thread

        threads = list(getattr(pool, "_threads", ()) or ())
    except Exception:  # pragma: no cover - internals moved
        return 0
    for t in threads:
        try:
            # 1. Stop _python_exit from joining it.
            _cf_thread._threads_queues.pop(t, None)
            # threading's own wait for non-daemon threads is handled at
            # creation instead (_spawn_daemon_workers) — there is no
            # supported way to withdraw a thread from it afterwards.
            detached += 1
        except Exception:  # pragma: no cover - internals moved
            failed += 1
    if failed:
        # Not swallowed: a thread we could not detach is a thread the
        # interpreter will join at exit, which is the hang this whole
        # function exists to prevent. Say so rather than returning a
        # count that quietly means less than it looks like.
        log.warning(
            "shared sync pool: could not detach %d of %d worker thread(s) "
            "from the interpreter's shutdown joins — process exit may block "
            "on stuck work (CPython internals may have moved)",
            failed, failed + detached,
        )
    return detached


def shutdown(wait: bool = False) -> None:
    """Drop the pool. ``wait=False`` by design — the whole point of the
    timeout path above is that a stuck worker is never joined, and
    :func:`detach_workers` is what makes that true at process exit too."""
    global _pool, _pool_size
    with _pool_lock:
        pool, _pool = _pool, None
        _pool_size = 0
    if pool is not None:
        if not wait:
            detached = detach_workers(pool)
            if _stuck:
                log.warning(
                    "shared sync pool: exiting with %d stuck worker(s); %d "
                    "pool thread(s) detached from the interpreter's shutdown "
                    "joins so they cannot hold the process open",
                    _stuck, detached,
                )
        pool.shutdown(wait=wait)


atexit.register(shutdown)
