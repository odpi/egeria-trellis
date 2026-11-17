"""The re-based elicitor's answer runs through the service as the request's own user (ruling
RULING-EA-V2-SERVICE-USER-CLIENT-2026-11-17).

Before this, no service user ever reached v2.answer. rag_system started the elicitor without a client, the
client lived in a module dict keyed by draft id (never cleared), and Execute Spec on a v2 document went
through the ordinary executor, without TR-6's re-rank or TR-7's count wording. Now:
- A1: a client per call from the request's own bearer token, user_pwd=NO_PASSWORD, never the service
  account. No token: "sign in", with no Egeria call.
- A2: nothing is kept between calls.
- A3: Execute on a v2 document runs the same v2.answer as the chat.
- A4: only the user who made a draft continues or runs it.

Offline: the recorded C03 LLM reply, temp stores, a fake EgeriaTech that records who it was built for, and
pyegeria's preview faked to answer per client.
"""
from __future__ import annotations

import contextlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

pytest.importorskip("pyegeria.authoring")

from advisor import spec_authoring_v2 as v2  # noqa: E402

QUESTION = "Which risks in the risk register have the highest importance?"
REPLY = {"title": "highest_importance_risks", "heading": "Risks with Highest Importance",
         "description": "Risks by importance.", "target_type": "Risk", "conditions": {},
         "columns": [{"name": "Risk Name", "key": "display_name"},
                     {"name": "Importance Level", "key": "importanceLevel"}], "unmet": None}
#: laz's 12 risks in the order Egeria returned them (31e359c5 quiet run).
C03_ROWS = [("Inconsistent Data Definitions Undermining Cross-Department Reporting", "Medium-High"),
            ("Terrorism-Related Incident at the Annual Conference", "Medium-High"),
            ("Failure to Discharge Martyn's Law Responsible Person Duties", "Medium-High"),
            ("Onboarding of a Fraudulent or Compromised Supplier", "High"),
            ("Loss of Undocumented Governance Knowledge on Staff Departure", "High"),
            ("Accidental Disclosure of Patient Data through Misconfigured Access", "High"),
            ("Insider Misuse of Sensitive Patient Data", "High"),
            ("Manufacturing Batch Records Failing GMP Traceability Requirements", "High"),
            ("Counterfeit or Substandard Materials Entering Manufacturing", "Critical"),
            ("Ransomware Disruption to Manufacturing Operations", "Critical"),
            ("Theft of Personalised Treatment Intellectual Property", "Critical"),
            ("Loss of Clinical Trial Data Integrity", "Critical")]
ERIN = {"user_id": "erinoverview", "password": "", "token": "erin-bearer"}
PETER = {"user_id": "peterprofile", "password": "", "token": "peter-bearer"}


@pytest.fixture
def stores(tmp_path, monkeypatch):
    import advisor.report_draft as rd
    import advisor.report_spec_docs as rsd

    def _drafts():
        p = tmp_path / "drafts"
        p.mkdir(parents=True, exist_ok=True)
        return p
    monkeypatch.setattr(rd, "_drafts_path", _drafts)
    monkeypatch.setattr(rsd, "_load_paths", lambda: {k: tmp_path / k for k in ("inbox", "outbox", "trash", "versions")})
    return tmp_path


@pytest.fixture
def on(monkeypatch):
    for f in (v2.FLAG, v2.FIXES_FLAG, v2.STATUS_MERGE_FLAG):
        monkeypatch.delenv(f, raising=False)      # the defaults: re-base + fixes on, merge off


class FakeClient:
    built = []
    lock = threading.Lock()

    def __init__(self, view_server=None, platform_url=None, user_id=None, user_pwd=None, **kw):
        self.user_id, self.user_pwd, self.token, self.closed = user_id, user_pwd, None, False
        with FakeClient.lock:
            FakeClient.built.append(self)

    def close_session(self):
        self.closed = True


@pytest.fixture
def egeria(monkeypatch):
    """A fake Egeria: clients record their user and token; preview answers C03's rows to a live token and
    fails as Egeria would for an expired one. Any service-account fallback fails the test."""
    FakeClient.built = []
    calls = []
    monkeypatch.setattr("pyegeria.egeria_tech_client.EgeriaTech", FakeClient)
    monkeypatch.setattr("advisor.mcp_config.get_pyegeria_platform_config",
                        lambda: {"view_server": "qs-view-server", "platform_url": "https://localhost:9443"})
    monkeypatch.setattr("advisor.auth._trellis_apply_token", lambda c, t: setattr(c, "token", t))
    monkeypatch.setattr("pyegeria.view.egeria_spec_cache.as_client", lambda c: contextlib.nullcontext())
    monkeypatch.setattr("advisor.auth.service_account_ids", lambda: frozenset({"svc-account"}))
    monkeypatch.setattr("advisor.auth.resolve_egeria_credentials",
                        lambda *a, **k: pytest.fail("the service-account fallback was asked for"))
    monkeypatch.setattr("pyegeria.authoring.inprocess_runner",
                        lambda client: (lambda md, d: {"success": True, "commands_total": 1}))

    def fake_preview(draft, *, client, max_rows, params=None, **kw):
        with FakeClient.lock:
            calls.append({"user": client.user_id, "token": client.token, "pwd": client.user_pwd,
                          "params": dict(draft.format_set.action.spec_params)})
        if client.token == "expired":
            raise RuntimeError("401 Unauthorized: the bearer token has expired")
        return {"result": {"kind": "table",
                           "columns": [{"key": "display_name", "label": "Risk Name"},
                                       {"key": "importance", "label": "Importance"}],
                           "rows": [{"display_name": n, "importance": v} for n, v in C03_ROWS]},
                "capped": False, "shown": 12, "notes": [], "end": "not_confirmed"}
    monkeypatch.setattr("pyegeria.authoring.preview", fake_preview)
    return calls


def _llm(monkeypatch, n=1):
    # distinct titles: a draft id is timestamp + title, so two same-second, same-title drafts would share one
    # (a separate, existing EA issue, reported with this change)
    replies = [json.dumps({**REPLY, "title": f"{REPLY['title']}_{i}"}) for i in range(n)]

    class LLM:
        def generate(self, prompt, **kw):
            return replies.pop(0)
    monkeypatch.setattr("advisor.agents.report_spec_elicitor_v2.get_planning_llm", lambda: LLM())
    monkeypatch.setattr("advisor.agents.report_spec_elicitor.get_planning_llm", lambda: LLM())


def _chat(creds):
    """What rag_system does for a chat user: start, accept, generate, each call with the request's creds."""
    from advisor.agents.report_spec_elicitor import ReportSpecElicitor
    el = ReportSpecElicitor()
    r = el.start(QUESTION, egeria_credentials=creds)
    r = el.process(r["draft_id"], "continue_columns", egeria_credentials=creds)
    return el, el.process(r["draft_id"], "generate now", egeria_credentials=creds)


# ── A1: whose identity ───────────────────────────────────────────────────────

def test_a_signed_in_chat_user_gets_the_v2_answer_as_themselves(on, stores, monkeypatch, egeria):
    _llm(monkeypatch)
    _, r = _chat(ERIN)
    assert r["phase"] == "refine" and r["answer"]["ok"]
    rows = r["answer"]["result"]["rows"]
    assert [x["importance"] for x in rows] == ["Critical"] * 4 + ["High"] * 5 + ["Medium-High"] * 3
    assert r["answer"]["notes"][0] == "at least 12 rows"
    assert egeria and all(c["user"] == "erinoverview" and c["token"] == "erin-bearer" and not c["pwd"]
                          for c in egeria)
    users = [c for c in FakeClient.built if c.user_id == "erinoverview"]
    assert users and all(c.closed for c in users)                            # built for the call, closed


@pytest.mark.parametrize("creds", [None, {}, {"user_id": "erinoverview", "password": "", "token": ""},
                                   {"user_id": "", "password": "", "token": "t"}])
def test_no_token_says_sign_in_and_calls_no_egeria(on, stores, monkeypatch, egeria, creds):
    _llm(monkeypatch)
    _, r = _chat(creds)
    assert r["phase"] == "refine" and "Sign in to see the answer" in r["response"] and "answer" not in r
    assert egeria == [] and FakeClient.built == []


def test_the_service_account_never_gets_a_client(on, stores, monkeypatch, egeria):
    _llm(monkeypatch)
    _, r = _chat({"user_id": "svc-account", "password": "", "token": "svc-bearer"})
    assert "Sign in to see the answer" in r["response"] and egeria == [] and FakeClient.built == []


def test_an_expired_token_fails_as_that_user_with_no_fallback(on, stores, monkeypatch, egeria):
    _llm(monkeypatch)
    _, r = _chat({"user_id": "erinoverview", "password": "", "token": "expired"})
    assert r["answer"]["ok"] is False and "expired" in r["response"]
    assert egeria and all(c["user"] == "erinoverview" and not c["pwd"] for c in egeria)
    assert {c.user_id for c in FakeClient.built} == {"erinoverview"}


# ── A2: nothing kept between calls ───────────────────────────────────────────

def test_no_client_store_survives_a_call(on, stores, monkeypatch, egeria):
    from advisor.agents import report_spec_elicitor_v2 as v2e
    _llm(monkeypatch)
    el, r = _chat(ERIN)
    assert not hasattr(v2e, "_CLIENTS") and v2e._SCOPE.get() is None
    egeria.clear()
    r = el.process(r["draft_id"], "generate now")          # the same draft, a call with no credentials
    assert r["response"] == v2e.NOT_YOUR_DRAFT and egeria == []      # R1: an owned draft needs its owner


def test_two_users_at_once_each_run_as_themselves(on, stores, monkeypatch, egeria):
    _llm(monkeypatch, n=2)
    with ThreadPoolExecutor(2) as pool:
        out = list(pool.map(lambda c: (c["user_id"], _chat(c)[1]), [ERIN, PETER]))
    for user, r in out:
        assert r.get("answer", {}).get("ok"), (user, r.get("response"))
    by_user = {}
    for c in egeria:
        by_user.setdefault(c["user"], set()).add(c["token"])
    assert by_user == {"erinoverview": {"erin-bearer"}, "peterprofile": {"peter-bearer"}}


# ── A4: only the draft's maker ───────────────────────────────────────────────

def test_another_users_draft_is_refused(on, stores, monkeypatch, egeria):
    from advisor.agents import report_spec_elicitor_v2 as v2e
    from advisor.agents.report_spec_elicitor import ReportSpecElicitor
    from advisor.report_draft import get_report_draft_manager
    _llm(monkeypatch)
    el = ReportSpecElicitor()
    r = el.start(QUESTION, egeria_credentials=ERIN)
    draft_id = r["draft_id"]
    assert get_report_draft_manager().load(draft_id)["owner"] == "erinoverview"
    for reply in ("continue_columns", "generate now"):
        out = el.process(draft_id, reply, egeria_credentials=PETER)
        assert out["response"] == v2e.NOT_YOUR_DRAFT
    assert egeria == [] and FakeClient.built == []
    assert get_report_draft_manager().load(draft_id)["phase"] == "confirm_action"     # untouched


@pytest.mark.parametrize("reply", ["continue_columns", "generate now", "add a description column"])
def test_a_call_with_no_user_cannot_touch_an_owned_draft(on, stores, monkeypatch, egeria, reply):
    """Read-back R1: no credentials is not the owner either; the draft file is unchanged."""
    from advisor.agents import report_spec_elicitor_v2 as v2e
    from advisor.agents.report_spec_elicitor import ReportSpecElicitor
    _llm(monkeypatch)
    el = ReportSpecElicitor()
    draft_id = el.start(QUESTION, egeria_credentials=ERIN)["draft_id"]
    from advisor.report_draft import get_report_draft_manager
    path = next(get_report_draft_manager()._root.glob(f"{draft_id}*"))
    before = path.read_bytes()
    assert el.process(draft_id, reply)["response"] == v2e.NOT_YOUR_DRAFT
    assert path.read_bytes() == before and egeria == []


def test_an_ownerless_legacy_draft_is_unchanged_behaviour(on, stores, monkeypatch, egeria):
    from advisor.agents.report_spec_elicitor import ReportSpecElicitor
    from advisor.report_draft import get_report_draft_manager
    _llm(monkeypatch)
    el = ReportSpecElicitor()
    draft_id = el.start(QUESTION)["draft_id"]                 # no user: no owner, as drafts before A4
    assert get_report_draft_manager().load(draft_id)["owner"] is None
    assert el.process(draft_id, "continue_columns")["phase"] == "elicit_columns"


def test_another_users_draft_cannot_be_executed(on, stores, monkeypatch, egeria):
    from advisor.agents import report_spec_elicitor_v2 as v2e
    from advisor.agents.report_spec_agent import get_report_spec_agent
    _llm(monkeypatch)
    _, r = _chat(ERIN)
    egeria.clear()
    out = get_report_spec_agent().execute(r["draft_id"], egeria_credentials=PETER)
    assert out["response"] == v2e.NOT_YOUR_DRAFT and egeria == []


# ── A3: Execute == chat ──────────────────────────────────────────────────────

def test_execute_on_the_saved_c03_document_matches_the_chat_answer(on, stores, monkeypatch, egeria):
    from advisor.agents.report_spec_agent import get_report_spec_agent
    from advisor.report_spec_docs import get_report_spec_doc_manager
    _llm(monkeypatch)
    _, chat = _chat(ERIN)
    doc = get_report_spec_doc_manager().load(chat["doc_id"])
    saved = v2.spec_from_document(doc, chat["doc_id"])
    assert saved["conditions"] == {"output_sort_order": "PROPERTY_DESCENDING", "order_property_name": "importance"}
    egeria.clear()
    ex = get_report_spec_agent().execute(chat["doc_id"], egeria_credentials=ERIN)
    a, b = chat["answer"], ex["answer"]
    assert ex["status"] == "Completed" and b["ok"]
    assert b["result"]["rows"] == a["result"]["rows"]                       # same rows, same order
    assert b["notes"] == a["notes"] and b["notes"][0] == "at least 12 rows"  # same count note, re-rank note
    assert b["result"]["rows"][0]["importance"] == "Critical"
    assert a["markdown"] in ex["response"]
    assert egeria and all(c["user"] == "erinoverview" and not c["pwd"] for c in egeria)


def test_execute_without_a_token_says_sign_in_and_calls_no_egeria(on, stores, monkeypatch, egeria):
    from advisor.agents.report_spec_agent import get_report_spec_agent
    from advisor.egeria_signin import SIGN_IN_TO_RUN_REPORTS
    _llm(monkeypatch)
    _, chat = _chat(ERIN)
    egeria.clear()
    FakeClient.built = []
    out = get_report_spec_agent().execute(chat["doc_id"], egeria_credentials=None)
    assert out["response"] == SIGN_IN_TO_RUN_REPORTS and egeria == [] and FakeClient.built == []


def test_rag_system_passes_the_requests_credentials_to_the_elicitor():
    """The service's routes: every report-spec elicitor start/process/execute carries egeria_credentials."""
    from pathlib import Path
    import re
    src = (Path(__file__).resolve().parents[2] / "advisor" / "rag_system.py").read_text()
    starts = re.findall(r"get_report_spec_elicitor\(\)\.start\(([^)]*)\)", src)
    assert starts and all("egeria_credentials=egeria_credentials" in s for s in starts)
    for call in ("_rse.process(_ctx_draft_id, user_query", "elicitor.process(draft_id, user_query"):
        i = src.index(call)
        assert "egeria_credentials=egeria_credentials" in src[i:i + 120]
