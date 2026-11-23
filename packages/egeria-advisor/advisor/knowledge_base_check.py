"""TR-60a (Architect's TR-60 ruling, point 2): EA says so, loudly, when the collections it answers from are empty.

From 2026-11-17 22:53Z to 11-18 23:02Z EA answered 18 questions from EMPTY tables (the egeria_advisor schema's copies,
reached through the role's search_path) and nothing flagged it: "0 results" read the same as "nothing relevant"
(absence-as-answer). At startup EA now counts every enabled collection, logs a WARNING naming each empty one, and
/api/status carries the result as `knowledge_base`.

States: not_checked (startup has not counted yet) | ok | partly_empty | empty (every enabled collection holds 0 rows)
| unread (no collection could be counted; the reasons are listed).
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Iterable, Optional

from loguru import logger

_lock = threading.Lock()
_last: Dict[str, Any] = {"state": "not_checked", "message": "EA has not counted its collections yet.",
                         "empty": [], "counts": {}, "unread": {}}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def check_enabled_collections(store: Any = None, names: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Count each enabled collection through `store` (EA's PgVectorStore by default), record and return the result,
    and log a WARNING unless every one holds rows."""
    if names is None:
        from advisor.collection_config import get_enabled_collections
        names = [c.name for c in get_enabled_collections()]
    names = list(names)
    if store is None:
        from advisor.vector_store_pg import PgVectorStore
        store = PgVectorStore()
    counts: Dict[str, int] = {}
    unread: Dict[str, str] = {}
    for name in names:
        try:
            counts[name] = int(store.get_collection_stats(name).get("num_entities") or 0)
        except Exception as exc:  # noqa: BLE001 -- a collection that cannot be counted is reported, not hidden
            unread[name] = f"{type(exc).__name__}: {str(exc)[:160]}"
    empty = [n for n in names if counts.get(n) == 0]
    where = "schema public (EA pins search_path=public on every connection)"
    if not names:
        state, message = "empty", "No collection is enabled, so EA has nothing to answer from."
    elif not counts:
        state = "unread"
        message = (f"EA could not count any of its {_plural(len(names), 'enabled collection')}: "
                   + "; ".join(f"{n}: {why}" for n, why in unread.items()))
    elif len(empty) == len(names):
        state = "empty"
        message = (f"Every enabled collection is EMPTY in {where}: {', '.join(empty)}. EA will answer with no "
                   "sources until they are indexed.")
    elif empty or unread:
        state = "partly_empty"
        bits = []
        if empty:
            bits.append(f"{_plural(len(empty), 'enabled collection')} EMPTY in {where}: {', '.join(empty)}")
        if unread:
            bits.append(f"{_plural(len(unread), 'collection')} could not be counted: "
                        + "; ".join(f"{n}: {why}" for n, why in unread.items()))
        message = "; ".join(bits) + "."
    else:
        state = "ok"
        message = f"{_plural(len(names), 'enabled collection')} hold rows ({sum(counts.values())} in all)."
    result = {"state": state, "message": message, "empty": empty, "counts": counts, "unread": unread}
    with _lock:
        _last.clear()
        _last.update(result)
    if state == "ok":
        logger.info(f"EA knowledge base: {message}")
    else:
        logger.warning(f"EA knowledge base: {message}")
    return dict(result)


def knowledge_base_status() -> Dict[str, Any]:
    """The last count, for /api/status. `not_checked` until startup has counted."""
    with _lock:
        return dict(_last)


def check_in_background(check: Callable[[], Any] = check_enabled_collections) -> threading.Thread:
    """Startup's call: counting never blocks startup, and a failure is logged, never raised."""
    def _run():
        try:
            check()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"EA knowledge base: could not count the enabled collections: {exc}")
    t = threading.Thread(target=_run, name="ea-knowledge-base-check", daemon=True)
    t.start()
    return t


def _reset_for_tests() -> None:
    with _lock:
        _last.clear()
        _last.update({"state": "not_checked", "message": "EA has not counted its collections yet.",
                      "empty": [], "counts": {}, "unread": {}})
