"""EA's feedback, kept in the shared `trellis-feedback` library (Phase 2, A-F1).

Every chat vote is an `answer` item in the library's `trellis_feedback_items` table, with:
- the signed-in user (anonymous: ""), the question, the full answer text;
- the intent label, the perspective and the routing agent.

A vote's status is the latest of its append-only triage events, which only admins write, through
the library's router (mounted at `/api/feedback/v2` by `advisor.web.feedback`). Nothing in EA is
keyed by a line position any more: an item's id is its only handle.

This replaces two JSONL files under the data root:
- `feedback/user_feedback.jsonl`, the collector's record;
- `feedback/feedback_extended.jsonl`, the full record that triage edited by line index.

Neither is written any more. `scripts/import_feedback_jsonl.py` copies both in once
(`advisor.feedback_import`), and they stay on disk.

Where the store lives:
- `ADVISOR_FEEDBACK_DATABASE_URL`, when set, is the URL as given. Tests use SQLite.
- Otherwise EA's own Postgres, from the same `PGVECTOR_*` settings as the rest of EA.
- The tables are in the schema `ADVISOR_FEEDBACK_SCHEMA` (default `egeria_advisor`), one schema per
  app as the design asks (docs/trellis-feedback-extraction.md §4 item 4). SQLite ignores the schema.

The same pattern as RE's `resource_explorer/feedback_store.py` (adoption PRs #1013/#1014): one library
store per URL per process, opened on first use. Opening it can fail (Postgres down); that is
`StoreUnavailable`, carrying the database's first line, never a silent empty store.
"""
from __future__ import annotations

import os
import threading
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.parse import quote

APP = "egeria-advisor"
URL_ENV = "ADVISOR_FEEDBACK_DATABASE_URL"
SCHEMA_ENV = "ADVISOR_FEEDBACK_SCHEMA"
DEFAULT_SCHEMA = "egeria_advisor"
#: Page size when reading every answer item (the library caps one page at 1000).
PAGE = 1000


class StoreUnavailable(RuntimeError):
    """The feedback store could not be opened or read. The message says why, in the database's own
    first line (never the full error, which can quote a row's text)."""


def feedback_database_url() -> str:
    """The store's URL: `ADVISOR_FEEDBACK_DATABASE_URL`, else EA's own Postgres."""
    explicit = (os.environ.get(URL_ENV) or "").strip()
    if explicit:
        return explicit
    from advisor.config import require_pgvector_password, settings
    return (f"postgresql+psycopg2://{quote(settings.pgvector_user, safe='')}:"
            f"{quote(require_pgvector_password(), safe='')}@{settings.pgvector_host}:"
            f"{settings.pgvector_port}/{settings.pgvector_dbname}")


def feedback_schema() -> str:
    return (os.environ.get(SCHEMA_ENV) or "").strip() or DEFAULT_SCHEMA


_stores: Dict[Tuple[str, str], Any] = {}
_stores_lock = threading.Lock()


def _words(exc: BaseException) -> str:
    try:
        from trellis_feedback.store import db_error_words
        return db_error_words(exc)
    except Exception:  # noqa: BLE001 - only a formatter; fall back to the first line ourselves
        return (str(exc).splitlines() or [type(exc).__name__])[0]


def get_store(url: Optional[str] = None, schema: Optional[str] = None):
    """The library store for `url`/`schema` (defaults: the configured ones), opened once per process.

    Raises StoreUnavailable when the library is not installed or the database cannot be reached."""
    url = url or feedback_database_url()
    schema = schema or feedback_schema()
    key = (url, schema)
    with _stores_lock:
        store = _stores.get(key)
        if store is not None:
            return store
        try:
            from trellis_feedback import FeedbackStore
        except ImportError as exc:
            raise StoreUnavailable(f"trellis-feedback is not installed: {exc}") from exc
        try:
            store = FeedbackStore(url, APP, schema=schema)
        except Exception as exc:  # noqa: BLE001 - any failure to open is reported with its reason
            raise StoreUnavailable(f"feedback store not opened ({_redact(url)}): "
                                   f"{type(exc).__name__}: {_words(exc)}") from exc
        _stores[key] = store
        return store


def _redact(url: str) -> str:
    """The URL without its password, for messages."""
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        creds, host = rest.rsplit("@", 1)
        user = creds.split(":", 1)[0]
        return f"{scheme}://{user}:***@{host}"
    return url


def reset_stores() -> None:
    """Forget every opened store (tests, and a changed URL)."""
    with _stores_lock:
        for s in _stores.values():
            try:
                s.engine.dispose()
            except Exception:  # noqa: BLE001 - disposing is best effort; the store is dropped either way
                pass
        _stores.clear()


class LazyStore:
    """Stands in for the library store where one must exist before the database is reached.

    `trellis_feedback.router.make_router` takes a store when the routes are built, at import time. EA
    must start without Postgres, so the router gets this. It answers `app` and `external_sources`
    itself, and opens the real store on the first route call. When that fails, the route answers 503
    with the reason, not a bare 500.
    """

    app = APP
    external_sources: frozenset = frozenset()

    def __getattr__(self, name: str):
        try:
            store = get_store()
        except StoreUnavailable as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return getattr(store, name)


def vote_key(session_id: Optional[str], exchange_id: Optional[str], vote: int,
             vote_seq: Optional[int] = None) -> Optional[str]:
    """The idempotency key of one vote SEND: session + exchange + the row's click sequence number.

    Each choice a user makes on a row (👍, then 👎, then 👍 again) is its own submission with its own
    `vote_seq`, so each is stored; only a re-send of the same click (a network retry) replays. Which vote
    counts is decided when reading: the latest per answer (`latest_votes`, A-F1 N1).

    A client without `vote_seq` (before N1) keys on the vote itself, as A-F1 first did. None without
    both ids: an old client that sends neither gets a plain submit, not a key it cannot reproduce."""
    s, e = (session_id or "").strip(), (exchange_id or "").strip()
    if not s or not e:
        return None
    tail = f"n{int(vote_seq)}" if vote_seq is not None else str(int(vote))
    return f"{s}:{e}:{tail}"[:128]


def answer_key(row: Dict[str, Any]) -> Optional[Tuple[str, str, str]]:
    """The answer a vote is about: (user, session, exchange). None for a vote that carries no exchange
    id (the CLI, an imported line, an old client): such a vote stands on its own."""
    exchange = (row.get("extra") or {}).get("exchange_id") or ""
    session = row.get("session_id") or ""
    if not exchange or not session:
        return None
    return (row.get("user_id") or "", session, exchange)


def latest_votes(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(counted, superseded): one vote per answer, the LATEST (A-F1 N1).

    A user who changes 👍 to 👎 on one answer has made one judgement, the second. Every vote is kept
    in the store, and its triage history with it; the earlier ones are only left out of the counts.
    Latest is by `created_at`, then id, so the choice is the same on every read."""
    best: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for r in rows:
        k = answer_key(r)
        if k is None:
            continue
        cur = best.get(k)
        if cur is None or (r.get("created_at") or "", r.get("id") or "") > (cur.get("created_at") or "", cur.get("id") or ""):
            best[k] = r
    counted, superseded = [], []
    for r in rows:
        k = answer_key(r)
        (counted if k is None or best[k] is r else superseded).append(r)
    return counted, superseded


def iter_answer_items(store) -> Iterator[Dict[str, Any]]:
    """Every answer item of this app, newest first, each with its `triage` state."""
    offset = 0
    while True:
        rows = store.list(kind="answer", limit=PAGE, offset=offset)
        yield from rows
        if len(rows) < PAGE:      # SQL LIMIT/OFFSET: a short page is the last one
            return
        offset += len(rows)


RATING_BY_VOTE = {1: "positive", 0: "neutral", -1: "negative"}
VOTE_BY_RATING = {v: k for k, v in RATING_BY_VOTE.items()}


def routed_intent(row: Dict[str, Any]) -> str:
    """The intent EA routed the question to (A-F1 N2).

    - From N2 on, `intent_label` holds it and `extra.intent_selected` holds the UI selector ("auto",
      "explain", ...).
    - An item written before N2 had the selector in `intent_label` and the routed intent in
      `extra.query_type`. The presence of `intent_selected` tells the two apart."""
    extra = row.get("extra") or {}
    if "intent_selected" in extra:
        return row.get("intent_label") or "unknown"
    return extra.get("query_type") or "unknown"


def as_collector_entry(row: Dict[str, Any]) -> Dict[str, Any]:
    """A library answer row in the shape EA's analytics read (`FeedbackCollector` stats and gaps)."""
    extra = row.get("extra") or {}
    return {
        "timestamp": row.get("created_at"),
        "query": row.get("query_text") or "",
        "query_type": routed_intent(row),
        "collections_searched": list(extra.get("collections_searched") or []),
        "rating": RATING_BY_VOTE.get(row.get("vote"), "neutral"),
        "star_rating": extra.get("star_rating"),
        "category": extra.get("category"),
        "suggested_collection": extra.get("suggested_collection"),
        "perspective": row.get("perspective") or None,
        "routing_agent": row.get("routing_agent") or None,
        "user_id": row.get("user_id") or None,
    }
