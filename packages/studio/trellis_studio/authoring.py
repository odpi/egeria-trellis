"""Studio's one seam onto `pyegeria.authoring`: every call as the signed-in user, nothing reimplemented.

Each operation builds a fresh client for the request's user (`egeria.user_client`), on a worker thread, and runs
the authoring API's sync wrapper there. pyegeria keeps one HTTP session per thread and event loop, so a
Dr.Egeria document run through `inprocess_runner` (which nests `run_sync`) never blocks Studio's event loop and
never shares a session across users or loops. Nothing is cached: not drafts, not clients, not results.

Absence is an answer (the pager rules; pyegeria does the paging):
  - a drafts list says "at least N" unless pyegeria confirmed the end; with a pyegeria that can't say
    (6.2.41's `async_list` has no `notes`), the list is never presented as complete;
  - a read that fails is a failure in words (`StudioReadFailed`), never an empty list or "not found";
  - an unknown lookup refuses a write (pyegeria's EgeriaDraftStore does this; Studio passes its words on).
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable, Dict, List, Optional

from trellis_studio.config import StudioSettings
from trellis_studio.egeria import user_client

KINDS = ("spec", "report", "sheet")


class StudioReadFailed(RuntimeError):
    """Egeria couldn't be read (or the read errored): the caller says so, never "none"."""


def _authoring():
    import pyegeria.authoring as a
    return a


def _run_sync(coro):
    from pyegeria.core.async_runner import run_sync
    return run_sync(coro)


async def in_user_thread(user: Dict[str, Any], settings: StudioSettings, fn: Callable[[Any], Any]) -> Any:
    """Run `fn(client)` on a worker thread with a client signed in as `user` (built in that thread)."""
    def work():
        client = user_client(user, settings)
        return fn(client)
    return await asyncio.to_thread(work)


def is_refusal(exc: BaseException) -> bool:
    """pyegeria.authoring refused, in words (an unknown lookup refusing a write is one)."""
    return isinstance(exc, _authoring().AuthoringRefused)


def is_auth_failure(exc: BaseException) -> bool:
    """Egeria refused the user's token: the session is over, so Studio answers 401 and the page signs in again."""
    try:
        from pyegeria.core._exceptions import PyegeriaUnauthorizedException
        if isinstance(exc, PyegeriaUnauthorizedException):
            return True
    except Exception:  # noqa: BLE001
        pass
    return "401" in str(exc)[:200] and "nauthori" in str(exc)[:400]


# ---- no Egeria needed ------------------------------------------------------------------------------------

def templates() -> List[Dict[str, str]]:
    a = _authoring()
    return [{"name": name, "kind": kind} for name, (kind, _make) in sorted(a.TEMPLATES.items())]


def as_draft_dict(draft: Any) -> Dict[str, Any]:
    return draft.model_dump(mode="json")


def parse_draft(value: Dict[str, Any]) -> Any:
    """A draft object from its JSON (pydantic does the checking; a bad draft raises ValueError in words)."""
    return _authoring().as_draft(value)


# ---- as the user -----------------------------------------------------------------------------------------

def _store(client):
    a = _authoring()
    return a.EgeriaDraftStore(client, a.inprocess_runner(client))


def list_drafts_supports_notes() -> bool:
    a = _authoring()
    return "notes" in inspect.signature(a.EgeriaDraftStore.async_list).parameters


async def list_drafts(user: Dict[str, Any], settings: StudioSettings, kind: Optional[str]) -> Dict[str, Any]:
    """The user's drafts, read as them: {drafts, notes, complete}. `complete` is True only when pyegeria said
    every kind's read reached a confirmed end."""
    with_notes = list_drafts_supports_notes()

    def fn(client):
        store = _store(client)
        notes: List[str] = []
        uid = client.user_id
        if with_notes:
            drafts = _run_sync(store.async_list(uid, kind, notes=notes))
        else:
            drafts = _run_sync(store.async_list(uid, kind))
        return drafts, notes

    try:
        drafts, notes = await in_user_thread(user, settings, fn)
    except Exception as exc:  # noqa: BLE001
        if is_refusal(exc) or is_auth_failure(exc):
            raise
        raise StudioReadFailed(f"Your drafts couldn't be read from Egeria ({type(exc).__name__}).") from exc
    if not with_notes:
        notes = ["This pyegeria can't say whether the list reached its end, so there may be more drafts than "
                 "shown."]
    return {"drafts": drafts, "notes": notes, "complete": with_notes and not notes, "count": len(drafts)}


async def load_draft(user, settings, kind: str, name: str) -> Optional[Dict[str, Any]]:
    def fn(client):
        d = _run_sync(_store(client).async_load(client.user_id, kind, name))
        return None if d is None else as_draft_dict(d)
    return await in_user_thread(user, settings, fn)


async def save_draft(user, settings, draft: Dict[str, Any]) -> str:
    obj = parse_draft(draft)

    def fn(client):
        return _run_sync(_store(client).async_save(client.user_id, obj))
    return await in_user_thread(user, settings, fn)


async def delete_draft(user, settings, kind: str, name: str) -> bool:
    def fn(client):
        return _run_sync(_store(client).async_delete(client.user_id, kind, name))
    return await in_user_thread(user, settings, fn)


async def new_from(user, settings, *, template: Optional[str], prototype: Optional[Dict[str, str]],
                   new_label: str) -> Dict[str, Any]:
    a = _authoring()
    if template:
        return as_draft_dict(a.new_from(template=template, new_label=new_label))
    proto = (prototype["kind"], prototype["label"])

    def fn(client):
        return as_draft_dict(_run_sync(a.async_new_from(prototype=proto, new_label=new_label, client=client)))
    return await in_user_thread(user, settings, fn)


async def validate(user, settings, draft: Dict[str, Any], *, dr_egeria: bool) -> Dict[str, Any]:
    a = _authoring()

    def fn(client):
        run = a.inprocess_runner(client) if dr_egeria else None
        return _run_sync(a.async_validate(draft, client, run=run)).model_dump(mode="json")
    return await in_user_thread(user, settings, fn)


async def preview(user, settings, draft: Dict[str, Any], *, max_rows: int, params: Optional[Dict[str, Any]],
                  run_placements: bool = False) -> Dict[str, Any]:
    a = _authoring()
    obj = parse_draft(draft)

    def fn(client):
        kw: Dict[str, Any] = {"client": client, "max_rows": max_rows, "params": params}
        if obj.kind == "sheet":
            kw["run"] = run_placements
        return _run_sync(a.async_preview(obj, **kw))
    return await in_user_thread(user, settings, fn)


async def lookup_report(user, settings, ref: str) -> Dict[str, Any]:
    """A Report as the user, by qualified name, else exact display name (pyegeria's three-way read, pager step 6).
    Found: its names and spec. Absent or unknown: pyegeria refuses in words (AuthoringRefused), which the route
    passes on; Studio never turns "couldn't confirm" into "there is none"."""
    a = _authoring()

    def fn(client):
        d = _run_sync(a.async_load_held("report", ref, client))
        qn = d.qualified_name
        by_qn = bool(qn) and ref == qn
        return {"found": True, "qualified_name": qn, "display_name": d.display_name, "spec": d.spec,
                "output_format": d.output_format, "by": "qualified name" if by_qn else "display name",
                "notes": list(d.notes)}
    return await in_user_thread(user, settings, fn)
