"""A memo that lives for exactly one scope -- one `FactLayer.fact()` call -- and no longer.

`FactLayer.fact()` reads an analysis twice: once for its results (`_read_results`) and once for its
headline (`_headline_for`), and for `architecture_recovery` and `architecture_diagram` the headline
reader calls the very same results function again with the same arguments. On `egeria_git` each of
those is a multi-second rebuild, so every fact paid for it twice.

This is not a cache across requests. The scope is opened by `fact()` and closed when it returns, so
nothing outlives the call that produced it; between two `fact()` calls no entry exists to go stale.
Within one call nothing writes, and what is stored is a DEEP COPY on the way in and on every hit
(the first caller's value is handed to code that mutates it -- `attach_status` and the like -- before
the headline reader asks for its own), so neither side can see the other's changes.

Outside a scope `memoize` is a pass-through: a function decorated with it behaves exactly as if it
were not, which is what every caller other than `FactLayer.fact()` gets.
"""
from __future__ import annotations

import copy
import functools
from contextlib import contextmanager
from contextvars import ContextVar

_memo: ContextVar[dict | None] = ContextVar("read_memo", default=None)

#: How many times a memoized call was answered from the scope, for tests and diagnostics.
stats = {"hits": 0, "misses": 0}


@contextmanager
def scope():
    """Open a memo scope. Nested scopes share the outermost one's memo."""
    if _memo.get() is not None:
        yield
        return
    token = _memo.set({})
    try:
        yield
    finally:
        _memo.reset(token)


def memoize(fn):
    """Memoize `fn(registry, slug, ...)` for the duration of the current `scope()`."""

    @functools.wraps(fn)
    def wrapper(registry, slug, *args, **kwargs):
        memo = _memo.get()
        if memo is None:
            return fn(registry, slug, *args, **kwargs)
        key = (fn.__qualname__, id(registry), slug, args, tuple(sorted(kwargs.items())))
        if key in memo:
            stats["hits"] += 1
            return copy.deepcopy(memo[key])
        stats["misses"] += 1
        result = fn(registry, slug, *args, **kwargs)
        memo[key] = copy.deepcopy(result)
        return result

    return wrapper
