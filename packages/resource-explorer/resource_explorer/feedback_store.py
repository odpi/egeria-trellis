"""Feedback Store — RE's page-level product feedback, kept in the shared `trellis-feedback` library.

Since 2026-11-09 (docs/trellis-feedback-extraction.md §6 step 3) the rows live in the library's
`trellis_feedback_items` table and their status in its append-only `trellis_feedback_events` log,
the same store Egeria Advisor will use. This module is RE's facade over it: the routes and
curate.py's combined Admin listing keep calling `add`/`list`/`stats`/`update_triage_status`.

The tables are created on the URL's own search_path (the `resource_explorer` schema by default),
where the legacy `feedback` table already is, so a test's throwaway schema isolates both.

**Migration.** The first time a process opens a URL, any rows of the legacy `feedback` table that
the library does not hold yet are copied in with their own id and created_at
(`FeedbackStore.import_item`), and a legacy `triage_status` other than `new` becomes one triage
event, written in the same transaction as its item. Rows already copied are skipped, so running it
again, or from two processes at once, writes nothing twice. The legacy table is left in place and
nothing writes to it any more: a change made to it by hand after the copy is not carried over.
The copy runs at web startup (web/app.py), not inside the first request that needs the store.

See resource_explorer/web/admin_auth.py for the admin gating the triage endpoints require.
"""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError
from trellis_feedback import (
    PRODUCT_CATEGORIES,
    STATUSES,
    FeedbackError,
    FeedbackItem,
    NotFound,
)
from trellis_feedback import FeedbackStore as _LibraryStore
from trellis_feedback.store import db_error_words

logger = logging.getLogger(__name__)

APP = "resource-explorer"
#: RE's own tables whose rows Admin may triage by reference (Q2): Curate's per-resource feedback.
RESOURCE_SOURCE = "resource_feedback"
EXTERNAL_SOURCES = (RESOURCE_SOURCE,)
LEGACY_TABLE = "feedback"
MIGRATION_ACTOR = "migration"

#: Q1: the library's six statuses. RE's old three (new, triaged, actioned) are among them.
VALID_TRIAGE_STATUSES = set(STATUSES)
VALID_CATEGORIES = set(PRODUCT_CATEGORIES)

#: Fields a page submission carries, copied between FeedbackEntry and the library item.
_ENTRY_FIELDS = (
    "session_id", "page", "element_guid", "rating", "category", "message", "email",
    "wants_response", "consent_to_contact", "build_version", "user_agent", "viewport", "locale",
)


@dataclass
class FeedbackEntry:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    page: str = ""
    element_guid: str = ""
    rating: int | None = None
    category: str = ""  # bug | confusing | suggestion | praise
    message: str = ""
    email: str = ""
    wants_response: bool = False
    consent_to_contact: bool = False
    build_version: str = ""
    user_agent: str = ""
    viewport: str = ""
    locale: str = ""
    triage_status: str = "new"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


# One library store per URL per process: building one creates an engine and checks the schema,
# which a request should not pay for, and the legacy copy must run once, not per request.
# web/app.py opens it at startup, off the event loop. If that failed, the first request to need
# it builds it, and the copy, inline in an async route: rare, slow once, and logged at startup.
_stores: dict[str, _LibraryStore] = {}
_stores_lock = threading.Lock()


def _library_store(url: str) -> _LibraryStore:
    with _stores_lock:
        store = _stores.get(url)
        if store is None:
            from resource_explorer.registry import schema_mode_from_env
            store = _LibraryStore(url, APP, external_sources=EXTERNAL_SOURCES, ddl=schema_mode_from_env())
            migrate_legacy_feedback(store)
            _stores[url] = store
        return store


def _as_utc(created_at: str) -> str:
    """Legacy rows were stamped with naive `datetime.utcnow()`; say so, as the library's own do."""
    value = (created_at or "").strip()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.isoformat()


def migrate_legacy_feedback(store: _LibraryStore) -> dict[str, int]:
    """Copy the legacy `feedback` table's rows into the library. Safe to run any number of times.

    Returns counts: `legacy` rows seen, `imported` now, `skipped` (already there), `events`
    written, `refused` (a row the library would not take, logged and left behind).
    """
    counts = {"legacy": 0, "imported": 0, "skipped": 0, "events": 0, "refused": 0, "unknown_status": 0}
    if not inspect(store.engine).has_table(LEGACY_TABLE):
        return counts
    with store.engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(text(f"SELECT * FROM {LEGACY_TABLE}")).mappings()]
    for row in rows:
        counts["legacy"] += 1
        item = FeedbackItem(
            app=APP, kind="product", id=str(row.get("id") or ""),
            created_at=_as_utc(str(row.get("created_at") or "")),
            **{f: row.get(f) for f in _ENTRY_FIELDS},
        )
        item.wants_response = bool(item.wants_response)
        item.consent_to_contact = bool(item.consent_to_contact)
        status = (row.get("triage_status") or "new").strip()
        if status not in STATUSES:
            # Copied as `new` rather than left behind: the feedback itself is still worth having.
            counts["unknown_status"] += 1
            logger.warning("feedback migration: legacy row %r has unknown status %r; copied as new",
                           row.get("id"), status)
        try:
            # The status goes in with the item, in one transaction: a crash between the two cannot
            # leave a copied row reading `new` that every later run then skips.
            imported = store.import_item(
                item, status=status if status in STATUSES else None, actor=MIGRATION_ACTOR,
                note=(f"status carried over from RE's legacy {LEGACY_TABLE} table, "
                      "which kept no time for the change"))
        except (FeedbackError, SQLAlchemyError) as exc:
            # Per row, so one bad row (a Postgres DataError, say) cannot stop the store opening and
            # make every later request re-run the copy. Logged by its first line only: a database
            # error's full text quotes the row's values (email, message).
            counts["refused"] += 1
            words = str(exc) if isinstance(exc, FeedbackError) else db_error_words(exc)
            logger.warning("feedback migration: legacy row %r not copied: %s", row.get("id"), words)
            continue
        if not imported:
            counts["skipped"] += 1
            continue
        counts["imported"] += 1
        if status in STATUSES and status != "new":
            counts["events"] += 1
    if counts["imported"] or counts["refused"] or counts["unknown_status"]:
        logger.info("feedback migration from legacy %s table: %s", LEGACY_TABLE, counts)
    return counts


def _flatten(row: dict[str, Any]) -> dict[str, Any]:
    """A library list row, with the legacy `triage_status` key the Admin page still reads."""
    row["triage_status"] = (row.get("triage") or {}).get("status", "new")
    return row


class FeedbackStore:
    def __init__(self, db_path: str = "data/feedback.db") -> None:
        self.db_path = db_path
        if db_path == "data/feedback.db":
            try:
                from resource_explorer.config import get_config
                config = get_config()
                self.database_url = config.feedback.database_url
            except Exception:  # noqa: BLE001 -- no usable config: fall back to the env var, as before
                import os
                self.database_url = os.environ.get("FEEDBACK_DATABASE_URL", f"sqlite:///{db_path}")
        else:
            self.database_url = f"sqlite:///{db_path}"
        if not self.database_url:
            from resource_explorer.config import require_database_url

            require_database_url(self.database_url, "FEEDBACK_DATABASE_URL", "feedback store")   # TR-12: names it

        if self.database_url.startswith("sqlite:///"):
            path_str = self.database_url[len("sqlite:///"):]
            if path_str and path_str != ":memory:":
                Path(path_str).parent.mkdir(parents=True, exist_ok=True)

        self.store = _library_store(self.database_url)
        self.engine = self.store.engine

    def add(self, entry: FeedbackEntry) -> FeedbackEntry:
        item = FeedbackItem(app=APP, kind="product", **{f: getattr(entry, f) for f in _ENTRY_FIELDS})
        stored, _ = self.store.submit(item)
        entry.id, entry.created_at = stored.id, stored.created_at
        return entry

    def add_answer(
        self,
        *,
        vote: int,
        question: str,
        answer: str = "",
        user_id: str = "",
        session_id: str = "",
        compile_id: str = "",
        intent: str = "",
        entity_type: str = "",
        slug: str = "",
        message: str = "",
        page: str = "",
        element_guid: str = "",
        extra: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[str, bool]:
        """Store a rating of one answer (a chat vote, or a Questions-checklist verdict) as an `answer` item.

        Q4/Q5 (design doc §7): the question and the full answer text are kept, with the signed-in user, and
        only admins can read them. Returns `(item id, created)`; a retried call with the same
        `idempotency_key` from the same user returns the first item's id and `created=False`.
        """
        item = FeedbackItem(
            app=APP, kind="answer", vote=vote, query_text=(question or "").strip(),
            response_text=answer or "", user_id=user_id or "", session_id=session_id or "",
            compile_id=compile_id or "", intent_label=intent or "", entity_type=entity_type or "",
            slug=slug or "", message=(message or "").strip(), page=page or "",
            element_guid=element_guid or "", extra=dict(extra or {}),
        )
        stored, created = self.store.submit(item, idempotency_key=idempotency_key)
        return stored.id, created

    def list(
        self,
        triage_status: str | None = None,
        category: str | None = None,
        limit: int = 200,
        kind: str = "product",
        cause: str | None = None,
    ) -> list[dict[str, Any]]:
        """Items of one kind (`product`, the page widget; `answer`, ratings of answers), newest first.

        `cause` (closed-loop step 1) is one of trellis-feedback's CAUSES, or UNDIAGNOSED; only answer items carry
        one, so a cause filter on product items matches nothing."""
        if cause and kind != "answer":
            return []
        # An unknown category matches nothing, as the old SQL filter did; the library refuses it, and
        # curate.py's combined listing passes whatever the Admin filter holds. Answers have no category.
        if category and (kind != "product" or category not in VALID_CATEGORIES):
            return []
        rows = self.store.list(kind=kind, status=triage_status or None,
                               category=category or None, cause=cause or None, limit=limit)
        return [_flatten(r) for r in rows]

    def count(self, kind: str = "product") -> int:
        return self.store.count(kind=kind)

    def cause_counts(self, vote: int | None = None) -> dict[str, int]:
        """Answer items by current cause, every cause listed (zeros included) plus `undiagnosed`."""
        return self.store.cause_counts(vote=vote)

    def get(self, feedback_id: str) -> dict[str, Any] | None:
        item = self.store.get(feedback_id)
        if item is None:
            return None
        row = item.to_dict()
        row["triage"] = self.store.state(feedback_id).to_dict()
        row["history"] = [e.to_dict() for e in self.store.history(feedback_id)]
        return _flatten(row)

    def stats(self) -> dict[str, Any]:
        i = self.store.items
        mine = (i.c.app == APP) & (i.c.kind == "product")
        with self.engine.connect() as conn:
            wants_response = conn.execute(
                select(func.count()).select_from(i).where(mine & i.c.wants_response.is_(True))
            ).scalar_one()
            avg_rating = conn.execute(
                select(func.avg(i.c.rating)).where(mine & i.c.rating.is_not(None))
            ).scalar_one()
        return {
            "total": self.store.count(kind="product"),
            "new": self.store.count_matching(kind="product", status="new"),
            "wants_response": int(wants_response),
            # float(): Postgres AVG() returns Decimal where SQLite returns a float, and plain
            # json.dumps() refuses a Decimal (found on the 2026-08-23 move to Postgres).
            "avg_rating": round(float(avg_rating), 2) if avg_rating is not None else None,
        }

    def update_triage_status(
        self, feedback_id: str, triage_status: str, *, note: str = "", actor: str = "",
        cause: str = "", secondary_cause: str = "",
    ) -> dict[str, Any] | None:
        """Append a triage event, with a diagnosis when `cause` is given (answer items only; trellis-feedback 0.3
        refuses one that contradicts itself, with FeedbackError). Returns the item with its new state, or None if
        there is no such item."""
        if triage_status not in VALID_TRIAGE_STATUSES:
            raise ValueError(f"Invalid triage_status: {triage_status!r}")
        try:
            self.store.triage(feedback_id, triage_status, note=note, actor=actor,
                              cause=cause, secondary_cause=secondary_cause)
        except NotFound:
            return None
        return self.get(feedback_id)

    # ── Curate's resource_feedback, triaged by reference (Q2, Q7) ────────────────────────────────────────────
    #
    # The rows stay in the registry's resource_feedback table, with their own meaning; only their triage events
    # live here, under source "resource_feedback". The library cannot see the registry, so a caller checks that
    # a row exists before triaging it. A row deleted from the registry keeps its events and is reported as
    # "item gone" until an admin clears them; deleting a row never reaches into this store.

    def resource_states(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        """Current triage state of each resource_feedback id (`new`, no events, for an untouched one)."""
        return {k: v.to_dict() for k, v in self.store.states([str(i) for i in ids], source=RESOURCE_SOURCE).items()}

    def triage_resource(self, row_id: str, status: str, *, note: str = "", actor: str = "") -> dict[str, Any]:
        """Append a triage event for a resource_feedback row the caller has checked exists."""
        if status not in VALID_TRIAGE_STATUSES:
            raise ValueError(f"Invalid triage_status: {status!r}")
        self.store.triage(str(row_id), status, source=RESOURCE_SOURCE, note=note, actor=actor)
        return self.resource_triage(row_id)

    def resource_triage(self, row_id: str) -> dict[str, Any]:
        """A resource_feedback row's state and every event, oldest first."""
        return {
            "triage": self.store.state(str(row_id), source=RESOURCE_SOURCE).to_dict(),
            "history": [e.to_dict() for e in self.store.history(str(row_id), source=RESOURCE_SOURCE)],
        }

    def gone_resource_rows(self, present) -> list[dict[str, Any]]:
        """resource_feedback ids that have triage events but no row any more, each with its last state.
        `present(ids)` returns the ids the registry still holds."""
        gone = self.store.gone(RESOURCE_SOURCE, present)
        states = self.resource_states(gone)
        return [{"ref_id": g, "source": RESOURCE_SOURCE, "triage": states[g]} for g in gone]

    def clear_resource_events(self, row_id: str) -> int:
        """Delete the triage events of a resource_feedback row (an admin clearing an "item gone" entry)."""
        return self.store.delete_events(str(row_id), source=RESOURCE_SOURCE)
