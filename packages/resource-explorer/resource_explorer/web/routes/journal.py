"""The journal — resource_explorer/journal.py, over HTTP."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from resource_explorer.auth import get_current_user
from resource_explorer.journal import Journal, derive_turn_entry, write_turn_entry

router = APIRouter()

#: An investigation's journal is the same table keyed by this entity type. Its entries stay in RE until the
#: investigation is promoted (it has no Egeria Project to hold a Note until then).
INVESTIGATION = "investigation"


def _check_investigation(entity_type: str, slug: str) -> None:
    """An investigation journal exists only for an investigation the caller may see. `get_investigation` answers
    None for both "no such slug" and "not yours" on purpose, so a private investigation's journal is a 404 to
    everyone else, never a 403 that would confirm it exists."""
    if entity_type != INVESTIGATION:
        return
    from resource_explorer.registry import ProjectRegistry

    if not ProjectRegistry().get_investigation(slug):
        raise HTTPException(status_code=404, detail=f"Investigation '{slug}' not found")


class JournalWrite(BaseModel):
    body: str
    suggest_to: list[str] = Field(default_factory=list)
    compile_id: str | None = None


@router.get("/{entity_type}/{slug}")
def list_entries(entity_type: str, slug: str) -> dict:
    _check_investigation(entity_type, slug)
    j = Journal()
    return {"entries": j.entries(entity_type, slug), "suggested_to": j.suggested_targets(entity_type, slug)}


@router.post("/{entity_type}/{slug}")
def write_entry(entity_type: str, slug: str, write: JournalWrite, request: Request) -> dict:
    """Append one entry. The author is the signed-in user, stamped here;
    anonymous is refused rather than recorded as nobody's, and the payload
    carries no author field to forge."""
    user = get_current_user(request)
    author = (user or {}).get("user_id") or (user or {}).get("sub") or (user or {}).get("username") or ""
    if not author:
        raise HTTPException(status_code=401, detail="Sign in to write in the journal — an entry needs an author.")
    _check_investigation(entity_type, slug)
    if entity_type == INVESTIGATION and write.suggest_to:
        # Suggestion routes a RESOURCE to an audience's work list; an investigation is not a work-list member.
        raise HTTPException(status_code=422, detail="An investigation's journal entry cannot be suggested to an audience.")
    try:
        return Journal().write(entity_type, slug, author=author, body=write.body, suggest_to=write.suggest_to,
                               compile_id=write.compile_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


class JournalTurn(BaseModel):
    """Which chat turn to add: the session and the stored exchange (the assistant row id the query reply carried).
    Nothing about the turn's text travels from the client; the entry is derived from the STORED exchange, the same
    source the chat's `write_journal` tool reads. The person's only additions are `confirm` and one optional `note`,
    stored apart from the derived text."""
    session_id: str
    exchange_id: int
    note: str = Field(default="", max_length=500)
    confirm: bool = False


def _signed_in_author(request: Request) -> str:
    user = get_current_user(request)
    return (user or {}).get("user_id") or (user or {}).get("sub") or (user or {}).get("username") or ""


@router.post("/investigation/{slug}/turn")
def write_turn(slug: str, turn: JournalTurn, request: Request) -> dict:
    """Add one chat turn to an investigation's journal. Signed-in only, for the preview too: the exchange read is
    the caller's own. With `confirm` false nothing is written: the reply is the derived entry, for the pane to show
    read-only, and `already` says whether this author already has it. With `confirm` true it writes the same row the
    chat's `write_journal` tool writes (journal.write_turn_entry), authored by the signed-in user and timed at this
    call; a turn already in the journal is not written twice (`already: true`, `written: false`)."""
    from resource_explorer.journal import (
        exchange_is_skipped, find_turn_entry, NO_EARLIER_ANSWER, SIGN_IN_WORDS,
    )
    from resource_explorer.registry import ProjectRegistry

    author = _signed_in_author(request)
    if not author:
        raise HTTPException(status_code=401, detail=SIGN_IN_WORDS)
    _check_investigation(INVESTIGATION, slug)
    registry = ProjectRegistry()
    exchanges = registry.load_exchanges(turn.session_id, limit=500, user_id=author)
    at = next((i for i, e in enumerate(exchanges) if e["exchange_id"] == turn.exchange_id), None)
    if at is None:
        raise HTTPException(status_code=404, detail="That chat turn is not on file for you.")
    ex = exchanges[at]
    if exchange_is_skipped(exchanges, at) or not (ex["answer"] or "").strip():
        raise HTTPException(status_code=422, detail=NO_EARLIER_ANSWER)
    common = dict(question=ex["question"], answer=ex["answer"], compile_id=ex.get("compile_id"),
                  resource_slug=ex.get("resource_slug") or None)
    if not turn.confirm:
        derived = derive_turn_entry(registry, investigation=slug, **common)
        existing = find_turn_entry(registry, investigation=slug, author=author, key=derived["turn"]["turn_key"])
        return {"written": False, "already": existing is not None, "body": derived["body"], "turn": derived["turn"]}
    try:
        entry = write_turn_entry(registry, investigation=slug, author=author, note=turn.note, **common)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"written": not entry["already"], **entry}
