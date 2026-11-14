"""S5: "may this user edit X?" as the signed-in user, and opening a held artifact for a new version.

The answer is yes / no / unknown, from pyegeria.authoring's own check (owner decision C: "Edit in Studio" shows for
anyone with edit rights). pyegeria.authoring has no such check yet (sd-dcat adds it as P2); until it does, every
answer is "unknown", with the reason, and is logged. Unknown is never turned into "no", and never into "yes".

Studio checks again when it opens the artifact: the Portal's link is a hint, never the check. Opening refuses on
"no" and on "unknown" (it can't be shown that you may edit it), in words.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from trellis_studio.authoring import _authoring, _run_sync, as_draft_dict, in_user_thread

logger = logging.getLogger("trellis_studio")
ANSWERS = ("yes", "no", "unknown")
NO_CHECK = "pyegeria.authoring has no edit-rights check yet (P2), so Studio can't tell."


def kind_of(ref: str, kind: str | None) -> str | None:
    if kind:
        return kind
    if ref.startswith("ReportSpec::"):
        return "spec"
    if ref.startswith("Dashboard::"):
        return "sheet"
    return None


def label_of(ref: str, kind: str) -> str:
    prefix = {"spec": "ReportSpec::", "sheet": "Dashboard::"}.get(kind, "")
    return ref[len(prefix):] if prefix and ref.startswith(prefix) else ref


async def can_edit(user, settings, kind: str, ref: str) -> Dict[str, Any]:
    a = _authoring()
    check = getattr(a, "async_can_edit", None)
    uid = user.get("user_id") or user.get("sub")
    if check is None:
        logger.info("studio: can-edit %s %r for %s: unknown (%s)", kind, ref, uid, NO_CHECK)
        return {"answer": "unknown", "why": NO_CHECK, "kind": kind, "ref": ref}

    def fn(client):
        return _run_sync(check(kind, ref, client))
    try:
        out = await in_user_thread(user, settings, fn)
    except Exception as exc:  # noqa: BLE001 -- an unanswered check is "unknown", said and logged
        why = f"the check could not run ({type(exc).__name__})"
        logger.info("studio: can-edit %s %r for %s: unknown (%s)", kind, ref, uid, why)
        return {"answer": "unknown", "why": why, "kind": kind, "ref": ref}
    answer = (out or {}).get("answer") if isinstance(out, dict) else None
    if answer not in ANSWERS:
        why = f"pyegeria answered {str(out)[:80]!r}, not yes/no/unknown"
        logger.info("studio: can-edit %s %r for %s: unknown (%s)", kind, ref, uid, why)
        return {"answer": "unknown", "why": why, "kind": kind, "ref": ref}
    if answer == "unknown":
        logger.info("studio: can-edit %s %r for %s: unknown (%s)", kind, ref, uid, out.get("why"))
    return {"answer": answer, "why": out.get("why") or "", "kind": kind, "ref": ref}


class NotPermitted(Exception):
    def __init__(self, answer: str, why: str):
        super().__init__(why)
        self.answer = answer


async def open_held(user, settings, kind: str, label: str) -> Dict[str, Any]:
    """The held artifact as a draft for a new version, only when the check says yes."""
    verdict = await can_edit(user, settings, kind, label)
    if verdict["answer"] != "yes":
        raise NotPermitted(verdict["answer"], verdict["why"])
    a = _authoring()
    draft = await in_user_thread(user, settings, lambda c: as_draft_dict(_run_sync(a.async_load_held(kind, label, c))))
    return {"draft": draft, "can_edit": verdict}
