"""The chat-action confirm gate (design note rev 4 §1e item 1, 2026-11-12).

Before: rag_system dispatched an action request to DrEgeriaActionAgent.handle(query, dry_run=False), and
handle defaulted to directive="process", so an LLM-composed Dr.Egeria WRITE was processed as the user straight
from a chat message. Now a chat message only validates and shows the script; the script is processed only by
confirm(pending_id, digest): the same sign-in, within the TTL, with the digest of the script that was shown,
once.

No Egeria: test_dr_egeria_identity's fakes record every directive the dispatcher is given, so "never
processed" is checked on what reached the dispatcher, not on a flag.
"""
from __future__ import annotations

import inspect
import time

import pytest
from test_dr_egeria_identity import MARKDOWN, USER, FakeEgeriaTech, agent, fakes  # noqa: F401

from advisor.agents import dr_egeria_agent as mod
from advisor.agents.dr_egeria_agent import (
    CONFIRM_CHANGED,
    CONFIRM_OTHER_SIGN_IN,
    CONFIRM_UNKNOWN,
    SIGN_IN_TO_RUN,
    script_digest,
)

OTHER = {"user_id": "peterprofile", "password": "", "token": "peter-token-xyz"}


@pytest.fixture(autouse=True)
def _clean():
    mod._reset_pending_for_tests()
    yield
    mod._reset_pending_for_tests()


@pytest.fixture
def composed(agent, monkeypatch):  # noqa: F811
    tpl = {"command_name": "Create Glossary", "attributes": []}
    monkeypatch.setattr(agent, "find_template", lambda q: tpl)
    monkeypatch.setattr(agent, "extract_params", lambda q, t: {})
    monkeypatch.setattr(mod, "compose_command", lambda t, p: MARKDOWN)
    return agent


def directives(fakes):  # noqa: F811
    return [d for disp in fakes["dispatchers"] for d in disp.calls]


# ── a chat action never processes without confirm ─────────────────────────────

def test_a_chat_action_validates_shows_the_script_and_writes_nothing(fakes, composed):  # noqa: F811
    out = composed.handle("create a glossary called Terms", egeria_credentials=USER)
    assert directives(fakes) == ["validate"]
    assert out["confirm_required"] is True
    assert MARKDOWN.strip() in out["response"] and "Nothing has been written" in out["response"]
    assert "**Validate:** 1 command(s) checked: 1 succeeded, 0 failed." in out["response"]
    assert out["script_digest"] == script_digest(MARKDOWN)
    assert out["next_context"] == {"task": "dr_egeria_confirm", "pending_id": out["pending_id"],
                                   "digest": script_digest(MARKDOWN)}


def test_handle_defaults_to_validate_and_refuses_process(fakes, composed):  # noqa: F811
    assert inspect.signature(type(composed).handle).parameters["directive"].default == "validate"
    with pytest.raises(ValueError, match="never processes"):
        composed.handle("create a glossary", directive="process", egeria_credentials=USER)
    assert directives(fakes) == []


def test_the_chat_dispatch_does_not_ask_for_process():
    """rag_system's call to handle() names no directive (so it gets validate) and never "process"."""
    import advisor.rag_system as rs
    src = inspect.getsource(rs)
    call = src[src.index("get_dr_egeria_agent().handle("):]
    call = call[:call.index(")") + 1]
    assert "directive" not in call


def test_a_failed_validate_is_shown_and_not_offered(fakes, composed):  # noqa: F811
    fakes["results"] = [{"is_command": True, "status": "failure", "verb": "Create", "object_type": "Glossary",
                         "message": "Display Name is missing", "output": ""}]
    out = composed.handle("create a glossary", egeria_credentials=USER)
    assert out["confirm_required"] is False and out["next_context"] is None and "pending_id" not in out
    assert "Display Name is missing" in out["response"] and "Not offered to run" in out["response"]
    assert directives(fakes) == ["validate"] and mod._pending == {}


def test_no_sign_in_no_validate_no_pending(fakes, composed):  # noqa: F811
    out = composed.handle("create a glossary", egeria_credentials=None)
    assert out["response"] == SIGN_IN_TO_RUN and out["auth_required"] is True
    assert directives(fakes) == [] and mod._pending == {}


# ── confirm with a wrong or stale digest is refused ───────────────────────────

def test_a_wrong_digest_is_refused_and_nothing_is_processed(fakes, composed):  # noqa: F811
    shown = composed.handle("create a glossary", egeria_credentials=USER)
    out = composed.confirm(shown["pending_id"], script_digest(MARKDOWN + "\n### Description\nchanged\n"),
                           egeria_credentials=USER)
    assert out["state"] == "refused" and out["response"] == CONFIRM_CHANGED
    for bad in ("", None, shown["script_digest"][:12]):
        assert composed.confirm(shown["pending_id"], bad, egeria_credentials=USER)["state"] == "refused"
    assert directives(fakes) == ["validate"]


def test_a_stale_confirm_is_refused(fakes, composed, monkeypatch):  # noqa: F811
    shown = composed.handle("create a glossary", egeria_credentials=USER)
    later = time.monotonic() + mod.CONFIRM_TTL_S + 1
    monkeypatch.setattr(time, "monotonic", lambda: later)
    out = composed.confirm(shown["pending_id"], shown["script_digest"], egeria_credentials=USER)
    assert out["state"] == "refused" and out["response"] == CONFIRM_UNKNOWN
    assert directives(fakes) == ["validate"]


def test_an_unknown_or_cancelled_pending_action_is_refused(fakes, composed):  # noqa: F811
    shown = composed.handle("create a glossary", egeria_credentials=USER)
    assert composed.confirm("made-up", shown["script_digest"], egeria_credentials=USER)["response"] == CONFIRM_UNKNOWN
    composed.cancel(shown["pending_id"])
    out = composed.confirm(shown["pending_id"], shown["script_digest"], egeria_credentials=USER)
    assert out["state"] == "refused" and out["response"] == CONFIRM_UNKNOWN
    assert directives(fakes) == ["validate"]


def test_another_sign_in_cannot_confirm(fakes, composed):  # noqa: F811
    shown = composed.handle("create a glossary", egeria_credentials=USER)
    out = composed.confirm(shown["pending_id"], shown["script_digest"], egeria_credentials=OTHER)
    assert out["state"] == "refused" and out["response"] == CONFIRM_OTHER_SIGN_IN
    assert composed.confirm(shown["pending_id"], shown["script_digest"], egeria_credentials=None)["state"] == "refused"
    assert directives(fakes) == ["validate"]


# ── confirm with the right digest processes once ──────────────────────────────

def test_the_right_digest_processes_the_shown_script_once(fakes, composed):  # noqa: F811
    shown = composed.handle("create a glossary", egeria_credentials=USER)
    out = composed.confirm(shown["pending_id"], shown["script_digest"], egeria_credentials=USER)
    assert out["state"] == "processed" and "processed as **erinoverview**" in out["response"]
    assert out["markdown"] == MARKDOWN and out["next_context"] is None
    assert directives(fakes) == ["validate", "process"]
    # processed as the user: the token, never a password, never minted
    assert FakeEgeriaTech.instances[-1].bearer_tokens == ["user-token-abc"]
    assert FakeEgeriaTech.instances[-1].minted == []
    again = composed.confirm(shown["pending_id"], shown["script_digest"], egeria_credentials=USER)
    assert again["state"] == "already_processed" and "nothing was written again" in again["response"]
    assert directives(fakes) == ["validate", "process"]


def test_a_failed_process_is_still_one_shot(fakes, composed):  # noqa: F811
    """A process that failed may have written part of the script: confirming again must not re-run it."""
    shown = composed.handle("create a glossary", egeria_credentials=USER)
    fakes["results"] = [{"is_command": True, "status": "failure", "verb": "Create", "object_type": "Glossary",
                         "message": "Egeria said no", "output": ""}]
    out = composed.confirm(shown["pending_id"], shown["script_digest"], egeria_credentials=USER)
    assert out["state"] == "failed" and "Egeria said no" in out["response"]
    assert composed.confirm(shown["pending_id"], shown["script_digest"],
                            egeria_credentials=USER)["state"] == "already_processed"
    assert directives(fakes) == ["validate", "process"]


# ── the chat router ───────────────────────────────────────────────────────────

class RecordingAgent:
    def __init__(self):
        self.confirms, self.cancels = [], []

    def confirm(self, pending_id, digest, egeria_credentials=None):
        self.confirms.append((pending_id, digest, egeria_credentials))
        return {"state": "processed", "response": "done", "next_context": None}

    def cancel(self, pending_id):
        self.cancels.append(pending_id)
        return {"response": "Cancelled: nothing was written.", "next_context": None}


@pytest.fixture
def router(monkeypatch):
    from advisor.rag_system import RAGSystem
    rec = RecordingAgent()
    monkeypatch.setattr(mod, "get_dr_egeria_agent", lambda: rec)
    return RAGSystem.__new__(RAGSystem), rec


CTX = {"task": "dr_egeria_confirm", "pending_id": "p1", "digest": "d" * 64}


@pytest.mark.parametrize("words", ["yes", "Yes.", "confirm", "go ahead", "process it", "yes please"])
def test_a_confirmation_in_context_confirms_with_the_shown_digest(router, words):
    rag, rec = router
    out = rag._process_query(words, include_context=True, context=CTX, egeria_credentials=USER)
    assert rec.confirms == [("p1", "d" * 64, USER)] and out["response"] == "done"


def test_a_confirmation_needs_a_sign_in(router):
    rag, rec = router
    out = rag._process_query("yes", include_context=True, context=CTX, egeria_authenticated=False,
                             egeria_credentials=None)
    assert rec.confirms == [] and out.get("query_type") != "command"


@pytest.mark.parametrize("words", ["no", "cancel", "never mind"])
def test_a_cancellation_in_context_cancels(router, words):
    rag, rec = router
    out = rag._process_query(words, include_context=True, context=CTX, egeria_credentials=USER)
    assert rec.cancels == ["p1"] and rec.confirms == [] and "nothing was written" in out["response"]


@pytest.mark.parametrize("words", ["yes and also create a project", "create another glossary",
                                   "ok so what is a glossary", "go"])
def test_anything_else_is_not_a_confirmation(words):
    from advisor.rag_system import _DR_EGERIA_CONFIRM_YES
    assert not _DR_EGERIA_CONFIRM_YES.match(words.lower())


# ── read-only report runs are unaffected ──────────────────────────────────────

def test_a_report_run_confirms_as_before_and_never_reaches_dr_egeria(router, monkeypatch, fakes):  # noqa: F811
    rag, rec = router
    calls = []

    class Pipe:
        def _execute_report(self, query, spec_id, **kw):
            calls.append((spec_id, kw))
            return {"response": "| table |", "query_type": "report"}
    import advisor.report_pipeline as rp
    monkeypatch.setattr(rp, "get_report_pipeline", lambda: Pipe())
    out = rag._process_query("yes", include_context=True,
                             context={"task": "act_confirm", "spec_id": "Collections", "filter": "*", "fmt": "TABLE"},
                             egeria_credentials=USER)
    assert out["query_type"] == "act_report_result" and calls[0][0] == "Collections"
    assert calls[0][1]["egeria_credentials"] is USER
    assert rec.confirms == [] and directives(fakes) == []
