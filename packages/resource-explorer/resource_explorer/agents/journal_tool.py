"""The chat's journal tool: add the LAST chat turn to the open investigation's journal.

Same confirm pattern as ``create_rfa_from_finding`` (agents/rfa_tools.py): ``confirm=False`` writes nothing and
returns the question to put to the person; ``confirm=True`` writes. The write is ``journal.write_turn_entry``,
the same function the pane's per-turn control reaches through ``POST /api/journal/investigation/{slug}/turn``,
so the two paths cannot produce different rows for the same turn.

The entry is derived from the turn, never from what the model says about it: "this" is the last exchange of the
session, read from the stored conversation, so the tool takes no question or answer argument a small model could
paraphrase. Only the in-app chat gets this tool (``BaseExplorerAgent.allow_writes``); the A2A surface is read-only.
"""
from __future__ import annotations

from contextvars import ContextVar

from beeai_framework.tools import tool

from resource_explorer.journal import (  # noqa: F401  (re-exported: the conversation agent and the tests import them here)
    ALREADY_WORDS, JOURNAL_VERB, NO_EARLIER_ANSWER, NO_INVESTIGATION, PROPOSAL_WORDS, SAVED_WORDS, SERVICE_ANSWERS,
    SIGN_IN_WORDS, find_turn_entry, is_journal_request, last_turn_exchange, turn_key, write_turn_entry,
)

#: Where the tool finds the session and the open investigation. Set by the conversation agent at the start of a
#: turn; a ContextVar rather than a tool argument because the model must not choose either.
_context: ContextVar[dict] = ContextVar("_journal_context", default={})
#: The proposals made this turn. A list, not a value: BeeAI runs tools in child tasks whose own `set` would not
#: reach the caller, but an append to this one list does (same shape as tools._turn_facts).
_proposals: ContextVar[list | None] = ContextVar("_journal_proposals", default=None)


#: The exchanges the tool put (or found already) in the journal this turn, for the pane to mark as saved.
_written: ContextVar[list | None] = ContextVar("_journal_written", default=None)


def set_journal_context(session_id: str | None, investigation: str | None) -> None:
    _context.set({"session_id": session_id or "", "investigation": (investigation or "").strip()})
    _proposals.set([])
    _written.set([])


def turn_written() -> list:
    """The exchange ids the tool wrote (or found already written) this turn."""
    return list(_written.get() or [])


def turn_proposal() -> dict | None:
    """The proposal made this turn (what the pane draws [Add] [Not now] for), or None."""
    made = _proposals.get()
    return made[-1] if made else None


def last_turn(registry, session_id: str) -> dict | None:
    """The last question/answer pair of the session that was not itself a journal exchange, read from the STORED
    conversation (the caller's own rows only). The same source the pane's route reads."""
    if not session_id:
        return None
    return last_turn_exchange(registry.load_exchanges(session_id))


def investigation_visible(registry, investigation: str) -> bool:
    """The route's `_check_investigation`, for the tool: `get_investigation` answers None both for "no such slug" and
    for "not yours", so another person's private investigation is indistinguishable from none."""
    return bool(investigation) and bool(registry.get_investigation(investigation))


def write_journal_raw(confirm: bool = False, note: str = "", *, registry=None, author: str | None = None) -> str:
    from resource_explorer.registry import ProjectRegistry, current_user_id

    ctx = _context.get()
    investigation = ctx.get("investigation") or ""
    if not investigation:
        return NO_INVESTIGATION
    registry = registry or ProjectRegistry()
    # The client names the open investigation; this checks the caller may see it before anything is proposed or
    # written, so one person's private journal cannot be written into by naming its slug.
    if not investigation_visible(registry, investigation):
        return NO_INVESTIGATION
    ex = last_turn(registry, ctx.get("session_id") or "")
    if ex is None:
        return NO_EARLIER_ANSWER
    who = current_user_id() if author is None else author
    if not confirm:
        if who and find_turn_entry(registry, investigation=investigation, author=who, key=turn_key(ex["question"], ex["answer"])):
            return ALREADY_WORDS
        made = _proposals.get()
        if made is not None:
            made.append({"investigation": investigation, "exchange_id": ex["exchange_id"], "question": ex["question"],
                         "answer": ex["answer"], "compile_id": ex.get("compile_id"),
                         "resource_slug": ex.get("resource_slug")})
        return PROPOSAL_WORDS
    if not who:
        return SIGN_IN_WORDS
    entry = write_turn_entry(registry, investigation=investigation, author=who, question=ex["question"],
                             answer=ex["answer"], compile_id=ex.get("compile_id"),
                             resource_slug=ex.get("resource_slug"), note=note)
    written = _written.get()
    if written is not None:
        written.append(ex["exchange_id"])
    return ALREADY_WORDS if entry.get("already") else SAVED_WORDS


@tool(
    description=(
        "Add the LAST chat turn (the question just answered and its answer, with the evidence it cited) to the "
        "open investigation's journal. Use it when the person asks to save, add, put or note this/that in the "
        "journal; never answer that you cannot. The entry is derived from the stored turn, so you pass no text. "
        "Two-step: with confirm=false (the default) nothing is written and you get the question to put to the "
        "person; call again with confirm=true only after they agree. note is the person's own optional words, "
        "kept apart from the entry."
    )
)
def write_journal(confirm: bool = False, note: str = "") -> str:
    return write_journal_raw(confirm=confirm, note=note)
