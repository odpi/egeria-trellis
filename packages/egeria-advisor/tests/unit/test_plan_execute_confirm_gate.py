"""The plan confirm gate: an LGCI governance plan runs only when shown and confirmed with its digest (2026-11-13).

Before: GovernancePlanAgent.execute(doc_id) processed a plan's Command Sequence in Egeria on "execute plan
<id>", POST /api/plans/{id}/execute (and /retry, /rerun), or "execute"/"go ahead" in a plan_elicitor
context. That needed an explicit execute, but no digest, so whatever the document held at that moment ran.

Now, the same as the chat-action gate (advisor.confirm_gate):
  - prepare_execute SHOWS the plan and registers a pending action (the whole document's SHA-256, who it
    was shown to);
  - execute/retry run only with that pending id and digest, as the same sign-in, within the TTL, and when
    the document is still the text that was shown;
  - each pending action runs once.

The plan run itself (_run_plan: Dr.Egeria, the outcome report, the move to outbox) is replaced by a
recorder, so "nothing ran" is a count of real calls, not a flag.
"""
from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_dr_egeria_identity import USER, agent  # noqa: F401

import advisor.auth as auth
import advisor.governance_docs as gd
from advisor import confirm_gate
from advisor.agents import dr_egeria_agent as dre
from advisor.agents import governance_plan_agent as gpa_mod
from advisor.agents.governance_plan_agent import (
    PLAN_CONFIRM_CHANGED,
    PLAN_CONFIRM_OTHER_PLAN,
    PLAN_CONFIRM_OTHER_SIGN_IN,
    PLAN_CONFIRM_REQUIRED,
    PLAN_CONFIRM_UNKNOWN,
    PLAN_DOCUMENT_CHANGED,
    GovernancePlanAgent,
)

OTHER = {"user_id": "peterprofile", "password": "", "token": "peter-token-xyz"}
PLAN = ("# Erin's plan\n\n## Command Sequence\n\n"
        "## Create Glossary\n\n### Display Name\nTerms\n\n"
        "## Create Term\n\n### Display Name\nCustomer\n")


@pytest.fixture
def docs(tmp_path, monkeypatch):
    paths = {k: tmp_path / k for k in ("inbox", "outbox", "trash", "versions")}
    monkeypatch.setattr(gd, "_load_paths", lambda: paths)
    dm = gd.DocumentManager()
    monkeypatch.setattr(gd, "_doc_manager", dm)
    return dm


@pytest.fixture
def ran(monkeypatch, agent):  # noqa: F811
    """Every real plan run, recorded; who it runs as comes from the identity tests' bare agent."""
    calls = []

    def record(self, doc_id, perspective=None, dry_run=False, source_folder="inbox", draft_id=None,
               egeria_credentials=None):
        calls.append({"doc_id": doc_id, "dry_run": dry_run, "source_folder": source_folder,
                      "draft_id": draft_id, "creds": egeria_credentials})
        return {"query": doc_id, "response": "ran", "query_type": "plan_executed", "doc_id": doc_id}
    monkeypatch.setattr(GovernancePlanAgent, "_run_plan", record)
    monkeypatch.setattr(dre, "get_dr_egeria_agent", lambda: agent)
    gpa_mod._reset_plan_gate_for_tests()
    yield calls
    gpa_mod._reset_plan_gate_for_tests()


@pytest.fixture
def gpa():
    return GovernancePlanAgent.__new__(GovernancePlanAgent)


def _shown(gpa, doc_id, **kw):
    out = gpa.prepare_execute(doc_id, egeria_credentials=kw.pop("creds", USER), **kw)
    assert out["confirm_required"] is True, out
    return out


# ── showing runs nothing ──────────────────────────────────────────────────────

def test_showing_a_plan_runs_nothing_and_returns_its_digest(docs, ran, gpa):
    doc = docs.import_document(PLAN, user_id="erinoverview")
    out = _shown(gpa, doc, draft_id="d1")
    assert ran == []
    assert out["query_type"] == "plan_confirm" and out["command_count"] == 2
    assert "## Create Glossary" in out["response"] and "Nothing has been written" in out["response"]
    assert out["plan_digest"] == confirm_gate.text_digest(docs.load(doc))
    assert out["next_context"] == {"task": "plan_execute_confirm", "pending_id": out["pending_id"],
                                   "digest": out["plan_digest"], "draft_id": "d1", "ack_required": False}


def test_no_sign_in_no_plan_shown(docs, ran, gpa):
    doc = docs.import_document(PLAN, user_id="erinoverview")
    out = gpa.prepare_execute(doc, egeria_credentials=None)
    assert out.get("confirm_required") is not True and out["auth_required"] is True
    assert gpa_mod._PLAN_GATE.pending == {} and ran == []


def test_execute_without_a_confirmation_runs_nothing(docs, ran, gpa):
    doc = docs.import_document(PLAN, user_id="erinoverview")
    out = gpa.execute(doc, egeria_credentials=USER)
    assert out["state"] == "refused" and out["response"] == PLAN_CONFIRM_REQUIRED and ran == []


def test_a_dry_run_writes_nothing_and_needs_no_confirm(docs, ran, gpa):
    doc = docs.import_document(PLAN, user_id="erinoverview")
    gpa.execute(doc, dry_run=True, egeria_credentials=USER)
    assert ran == [{"doc_id": doc, "dry_run": True, "source_folder": "inbox", "draft_id": None, "creds": USER}]


# ── stale or changed digest, other sign-in ────────────────────────────────────

def test_a_wrong_or_truncated_digest_is_refused(docs, ran, gpa):
    doc = docs.import_document(PLAN, user_id="erinoverview")
    shown = _shown(gpa, doc)
    for bad in (confirm_gate.text_digest(PLAN + "x"), shown["plan_digest"][:12], "", None):
        out = gpa.execute(doc, egeria_credentials=USER, pending_id=shown["pending_id"], digest=bad)
        assert out["state"] == "refused" and out["response"] == PLAN_CONFIRM_CHANGED
    assert ran == []


def test_a_stale_confirmation_is_refused(docs, ran, gpa, monkeypatch):
    doc = docs.import_document(PLAN, user_id="erinoverview")
    shown = _shown(gpa, doc)
    later = time.monotonic() + gpa_mod.PLAN_CONFIRM_TTL_S + 1
    monkeypatch.setattr(time, "monotonic", lambda: later)
    out = gpa.execute(doc, egeria_credentials=USER, pending_id=shown["pending_id"], digest=shown["plan_digest"])
    assert out["state"] == "refused" and out["response"] == PLAN_CONFIRM_UNKNOWN and ran == []


def test_another_sign_in_cannot_confirm(docs, ran, gpa):
    doc = docs.import_document(PLAN, user_id="erinoverview")
    shown = _shown(gpa, doc)
    out = gpa.execute(doc, egeria_credentials=OTHER, pending_id=shown["pending_id"], digest=shown["plan_digest"])
    assert out["state"] == "refused" and out["response"] == PLAN_CONFIRM_OTHER_SIGN_IN
    out = gpa.execute(doc, egeria_credentials=None, pending_id=shown["pending_id"], digest=shown["plan_digest"])
    assert out["state"] == "refused" and out["auth_required"] is True
    assert ran == []


def test_a_confirmation_for_another_plan_or_action_is_refused(docs, ran, gpa):
    a = docs.import_document(PLAN, user_id="erinoverview")
    b = docs.import_document(PLAN.replace("Terms", "Other"), title="other plan", user_id="erinoverview")
    shown = _shown(gpa, a)
    out = gpa.execute(b, egeria_credentials=USER, pending_id=shown["pending_id"], digest=shown["plan_digest"])
    assert out["state"] == "refused" and out["response"] == PLAN_CONFIRM_OTHER_PLAN
    out = gpa.execute(a, source_folder="outbox", egeria_credentials=USER, pending_id=shown["pending_id"],
                      digest=shown["plan_digest"])
    assert out["response"] == PLAN_CONFIRM_OTHER_PLAN
    out = gpa.retry(a, egeria_credentials=USER, pending_id=shown["pending_id"], digest=shown["plan_digest"])
    assert out["response"] == PLAN_CONFIRM_OTHER_PLAN
    assert ran == []


def test_a_plan_edited_after_it_was_shown_does_not_run(docs, ran, gpa):
    doc = docs.import_document(PLAN, user_id="erinoverview")
    shown = _shown(gpa, doc)
    assert docs.update(doc, PLAN.replace("Customer", "Supplier"), edited_by="erinoverview")
    out = gpa.execute(doc, egeria_credentials=USER, pending_id=shown["pending_id"], digest=shown["plan_digest"])
    assert out["state"] == "refused" and out["response"] == PLAN_DOCUMENT_CHANGED and ran == []
    # the stale confirmation is gone: showing it again is the way forward
    again = gpa.execute(doc, egeria_credentials=USER, pending_id=shown["pending_id"], digest=shown["plan_digest"])
    assert again["response"] == PLAN_CONFIRM_UNKNOWN and ran == []


def test_a_cancelled_plan_cannot_be_confirmed_and_the_draft_context_returns(docs, ran, gpa):
    doc = docs.import_document(PLAN, user_id="erinoverview")
    shown = _shown(gpa, doc, draft_id="d1")
    out = gpa.cancel_execute(shown["pending_id"], draft_id="d1")
    assert "nothing was written" in out["response"]
    assert out["next_context"] == {"task": "plan_elicitor", "draft_id": "d1"}
    assert gpa.execute(doc, egeria_credentials=USER, pending_id=shown["pending_id"],
                       digest=shown["plan_digest"])["response"] == PLAN_CONFIRM_UNKNOWN
    assert ran == []


# ── the right digest runs once ────────────────────────────────────────────────

def test_the_right_digest_runs_the_shown_plan_once(docs, ran, gpa):
    doc = docs.import_document(PLAN, user_id="erinoverview")
    shown = _shown(gpa, doc, draft_id="d1")
    out = gpa.execute(doc, egeria_credentials=USER, pending_id=shown["pending_id"], digest=shown["plan_digest"])
    assert out["response"] == "ran"
    assert ran == [{"doc_id": doc, "dry_run": False, "source_folder": "inbox", "draft_id": "d1", "creds": USER}]
    again = gpa.execute(doc, egeria_credentials=USER, pending_id=shown["pending_id"], digest=shown["plan_digest"])
    assert again["state"] == "already_run" and "nothing was run again" in again["response"]
    assert len(ran) == 1


def test_confirm_runs_what_the_pending_action_names(docs, ran, gpa):
    """Chat's "yes": confirm() runs the shown plan, in the folder it was shown from (Re-run: outbox)."""
    doc = docs.import_document(PLAN, user_id="erinoverview")
    out_id = docs.move_to_outbox(doc, "")
    assert out_id and docs.load_outbox(out_id)
    shown = _shown(gpa, out_id, source_folder="outbox")
    gpa.confirm(shown["pending_id"], shown["plan_digest"], egeria_credentials=USER)
    assert ran == [{"doc_id": out_id, "dry_run": False, "source_folder": "outbox", "draft_id": None, "creds": USER}]


def test_retry_is_gated_and_runs_once(docs, ran, gpa, monkeypatch):
    doc = docs.import_document(PLAN, user_id="erinoverview")
    outbox = {"failed-plan": PLAN}
    monkeypatch.setattr(docs, "load_outbox", lambda d: outbox.get(d))
    monkeypatch.setattr(docs, "move_to_inbox", lambda d: doc if outbox.pop(d, None) else None)
    assert gpa.retry("failed-plan", egeria_credentials=USER)["response"] == PLAN_CONFIRM_REQUIRED
    shown = _shown(gpa, "failed-plan", action="retry")
    assert shown["source_folder"] == "outbox" and ran == []
    out = gpa.confirm(shown["pending_id"], shown["plan_digest"], egeria_credentials=USER)
    assert out["response"] == "ran" and ran[0]["doc_id"] == doc
    assert gpa.retry("failed-plan", egeria_credentials=USER, pending_id=shown["pending_id"],
                     digest=shown["plan_digest"])["state"] == "already_run"
    assert len(ran) == 1


# ── the API routes require the digest too ─────────────────────────────────────

@pytest.fixture
def api(docs, ran, monkeypatch):
    def current(request):
        uid = request.headers.get("x-user")
        tok = {"erinoverview": "user-token-abc", "peterprofile": "peter-token-xyz"}.get(uid, "")
        return {"user_id": uid, "sub": uid, "role": "user", "egeria_token": tok} if uid else None
    monkeypatch.setattr(auth, "get_current_user", current)
    monkeypatch.setattr(auth, "is_admin", lambda request: False)
    from advisor.web.plans import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


ERIN = {"x-user": "erinoverview"}


def test_the_execute_route_shows_first_and_runs_only_with_the_digest(api, docs, ran):
    doc = docs.import_document(PLAN, user_id="erinoverview")
    shown = api.post(f"/api/plans/{doc}/execute", headers=ERIN, json={"draft_id": "d1"}).json()
    assert shown["confirm_required"] is True and ran == []
    bad = api.post(f"/api/plans/{doc}/execute", headers=ERIN,
                   json={"pending_id": shown["pending_id"], "digest": "0" * 64}).json()
    assert bad["state"] == "refused" and bad["response"] == PLAN_CONFIRM_CHANGED and ran == []
    ok = api.post(f"/api/plans/{doc}/execute", headers=ERIN,
                  json={"draft_id": "d1", "pending_id": shown["pending_id"], "digest": shown["plan_digest"]}).json()
    assert ok["response"] == "ran" and len(ran) == 1 and ran[0]["draft_id"] == "d1"
    again = api.post(f"/api/plans/{doc}/execute", headers=ERIN,
                     json={"pending_id": shown["pending_id"], "digest": shown["plan_digest"]}).json()
    assert again["state"] == "already_run" and len(ran) == 1


def test_the_execute_route_refuses_a_plan_edited_after_it_was_shown(api, docs, ran):
    doc = docs.import_document(PLAN, user_id="erinoverview")
    shown = api.post(f"/api/plans/{doc}/execute", headers=ERIN, json={}).json()
    assert api.put(f"/api/plans/{doc}", headers=ERIN,
                   json={"content": PLAN.replace("Customer", "Supplier")}).status_code == 200
    out = api.post(f"/api/plans/{doc}/execute", headers=ERIN,
                   json={"pending_id": shown["pending_id"], "digest": shown["plan_digest"]}).json()
    assert out["response"] == PLAN_DOCUMENT_CHANGED and ran == []


@pytest.mark.parametrize("route", ["rerun", "retry"])
def test_rerun_and_retry_routes_show_first_too(api, docs, ran, monkeypatch, route):
    monkeypatch.setattr(docs, "load_outbox", lambda d: PLAN if d == "done-plan" else None)
    monkeypatch.setattr(docs, "move_to_inbox", lambda d: "done-plan-inbox")
    import advisor.web.plans as plans_mod
    monkeypatch.setattr(plans_mod, "_require_visible_plan", lambda request, doc_id: None)
    shown = api.post(f"/api/plans/done-plan/{route}", headers=ERIN).json()
    assert shown["confirm_required"] is True and shown["source_folder"] == "outbox" and ran == []
    assert api.post(f"/api/plans/done-plan/{route}", headers=ERIN,
                    json={"pending_id": shown["pending_id"], "digest": "nope"}).json()["state"] == "refused"
    assert ran == []
    out = api.post(f"/api/plans/done-plan/{route}", headers=ERIN,
                   json={"pending_id": shown["pending_id"], "digest": shown["plan_digest"]}).json()
    assert out["response"] == "ran" and len(ran) == 1


# ── chat ──────────────────────────────────────────────────────────────────────

class RecordingPlanAgent:
    def __init__(self):
        self.confirms, self.cancels = [], []

    def confirm(self, pending_id, digest, perspective=None, egeria_credentials=None, ack_non_idempotent=False):
        self.confirms.append((pending_id, digest, egeria_credentials))
        self.acks = getattr(self, "acks", []) + [ack_non_idempotent]
        return {"response": "ran", "next_context": None}

    def cancel_execute(self, pending_id, draft_id=None):
        self.cancels.append((pending_id, draft_id))
        return {"response": "Cancelled", "next_context": {"task": "plan_elicitor", "draft_id": draft_id}}


@pytest.fixture
def router(monkeypatch):
    from advisor.rag_system import RAGSystem
    rec = RecordingPlanAgent()
    monkeypatch.setattr(gpa_mod, "get_governance_plan_agent", lambda: rec)
    return RAGSystem.__new__(RAGSystem), rec


CTX = {"task": "plan_execute_confirm", "pending_id": "p1", "digest": "d" * 64, "draft_id": "d1"}


def test_yes_to_a_shown_plan_confirms_with_its_digest(router):
    rag, rec = router
    out = rag._process_query("go ahead", include_context=True, context=CTX, egeria_credentials=USER)
    assert rec.confirms == [("p1", "d" * 64, USER)] and out["response"] == "ran"


def test_no_to_a_shown_plan_cancels_and_returns_to_the_draft(router):
    rag, rec = router
    out = rag._process_query("cancel", include_context=True, context=CTX, egeria_credentials=USER)
    assert rec.cancels == [("p1", "d1")] and rec.confirms == []
    assert out["next_context"] == {"task": "plan_elicitor", "draft_id": "d1"}


def test_no_chat_route_runs_a_plan_directly():
    """Every chat route into plan execution shows the plan (prepare_execute); the only direct execute()
    call left in rag_system is the dry run, which writes nothing."""
    import inspect

    import advisor.rag_system as rs
    src = inspect.getsource(rs)
    assert src.count("prepare_execute(") == 3
    direct = [line for line in src.splitlines() if ".execute(_doc_id" in line or "agent.execute(doc_id" in line
              or "plan_agent().execute(" in line]
    assert all("dry_run=True" in line for line in direct), direct


# ── the CLI asks first ────────────────────────────────────────────────────────

@pytest.mark.parametrize("answer, runs", [("n\n", 0), ("y\n", 1)])
def test_the_cli_shows_the_plan_and_runs_it_only_on_yes(docs, ran, monkeypatch, answer, runs):
    from click.testing import CliRunner

    import advisor.cli.session as cli_session
    from advisor.cli.plans import plans
    monkeypatch.setattr(cli_session, "require_egeria_credentials", lambda console: USER)
    doc = docs.import_document(PLAN, user_id="erinoverview")
    out = CliRunner().invoke(plans, ["execute", doc], input=answer)
    assert out.exit_code == 0, out.output
    assert "Create Glossary" in out.output and len(ran) == runs
    if runs:
        assert ran[0]["creds"] is USER and ran[0]["dry_run"] is False
    else:
        assert "nothing was written" in out.output and gpa_mod._PLAN_GATE.pending == {}


def test_the_drafts_own_cancel_is_still_the_draft_cancel():
    """GovernancePlanAgent.cancel(draft_id) abandons a draft (plan_elicitor's "cancel"); the plan gate's
    cancel is cancel_execute and must not shadow it."""
    import inspect
    assert list(inspect.signature(GovernancePlanAgent.cancel).parameters) == ["self", "draft_id"]
    assert "pending_id" in inspect.signature(GovernancePlanAgent.cancel_execute).parameters
