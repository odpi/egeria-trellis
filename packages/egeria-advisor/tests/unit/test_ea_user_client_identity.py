"""U3 on 8880 (bbc70228), 2026-11-17: every chat answer failed with a 401 for EA's service account. Ruling
RULING-EA-U3-IDENTITY-SWITCH-bbc70228.

`build_user_client` returned `pyegeria.view.egeria_spec_cache.as_client(client)`, which is a CONTEXT MANAGER,
not the client. preview's `_identity()` read user, token, password, server and URL as None from it, and
`exec_report_spec` fell back to `settings.User_Profile`, the configured service account. With that account's
correct password, it would have been a zone bypass: users seeing the service account's view. The earlier
tests used a fake EgeriaTech and a stand-in as_client, so they missed it.

Here: a REAL EgeriaTech, the real apply_token and the real as_client. The platform URL is unroutable, and
any HTTP request fails the test, so nothing reaches an Egeria. The guard patches `exec_report_spec` (the one
place a run gets its identity) and asserts the identity it was handed is the request's user, with their
token: never None, never the service account.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("pyegeria.authoring")

from advisor import spec_authoring_v2 as v2  # noqa: E402

ERIN = {"user_id": "erinoverview", "password": "", "token": "erin-bearer-token"}
SERVICE = "egeriaadvisornpa"
REPLY = {"title": "highest_importance_risks", "heading": "Risks by importance", "description": "d",
         "target_type": "Risk", "conditions": {},
         "columns": [{"name": "Risk Name", "key": "display_name"}], "unmet": None}
ROWS = [("Loss of Clinical Trial Data Integrity", "Critical"), ("Insider Misuse of Sensitive Patient Data", "High")]


@pytest.fixture
def offline(monkeypatch):
    """A real client against an unroutable platform; any HTTP request is a test failure; EGERIA_USER is the
    service account, so a fallback would name it."""
    monkeypatch.setenv("EGERIA_USER", SERVICE)
    monkeypatch.setattr("advisor.mcp_config.get_pyegeria_platform_config",
                        lambda: {"view_server": "qs-view-server", "platform_url": "https://127.0.0.1:9"})
    import httpx

    def no_network(self, request, *a, **k):
        pytest.fail(f"an HTTP request was made: {request.method} {request.url}")
    monkeypatch.setattr(httpx.Client, "send", no_network)
    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    monkeypatch.setattr("advisor.auth.service_account_ids", lambda: frozenset({SERVICE}))


@pytest.fixture
def exec_guard(monkeypatch):
    """exec_report_spec, recording the identity it is called with, answering rows without Egeria."""
    seen = []

    def fake_exec(spec, **kw):
        seen.append({k: kw.get(k) for k in ("user", "token", "user_pass", "view_server", "view_url")})
        return {"kind": "table", "columns": [{"key": "display_name", "label": "Risk Name"},
                                             {"key": "importance", "label": "Importance"}],
                "rows": [{"display_name": n, "importance": i} for n, i in ROWS], "end": "confirmed"}
    monkeypatch.setattr("pyegeria.view.format_set_executor.exec_report_spec", fake_exec)

    monkeypatch.setattr("pyegeria.authoring.inprocess_runner",
                        lambda client: (lambda md, d: {"success": True, "commands_total": 1}))

    # validate's Egeria checks, recording the client they get (Architect's appendix item 1): the type-fit
    # check reads the target's super types; the publication check reads the held spec. Both answer offline.
    async def super_chain(client, type_name):
        seen.append({"check": "type_fit", "client": client})
        return [type_name, "GovernanceDefinition", "Referenceable"]

    async def held_notes(client, draft):
        seen.append({"check": "held_spec", "client": client})
        return [], []
    monkeypatch.setattr("md_processing.md_processing_utils.typed_properties.type_super_chain", super_chain)
    monkeypatch.setattr("pyegeria.authoring.publication.async_publication_notes", held_notes)
    return seen


@pytest.fixture
def egeria_type_fit(monkeypatch):
    """Risk + find_governance_definitions is a registered pair, so pyegeria checks the fit from its registry
    without Egeria. Clear that shortcut so the check reads the super types from Egeria with the client."""
    import sys
    import pyegeria.authoring  # noqa: F401
    monkeypatch.setattr(sys.modules["pyegeria.authoring.validate"], "_registry_pairs", lambda: set())


def _assert_the_requests_identity(seen):
    runs = [s for s in seen if "check" not in s]
    assert runs, "exec_report_spec was never called"
    for s in runs:
        assert s["user"] == "erinoverview", s
        assert s["user"] not in (None, SERVICE)
        assert s["token"] == "erin-bearer-token"
        assert not s["user_pass"]
        assert s["view_url"] == "https://127.0.0.1:9" and s["view_server"] == "qs-view-server"


# ── the real object ──────────────────────────────────────────────────────────

def test_build_user_client_returns_a_real_client_as_the_requests_user(offline):
    import sys
    import pyegeria.authoring  # noqa: F401
    _identity = sys.modules["pyegeria.authoring.preview"]._identity
    from pyegeria.egeria_tech_client import EgeriaTech
    from advisor.egeria_user_client import build_user_client
    client = build_user_client(ERIN)
    assert isinstance(client, EgeriaTech)                          # not as_client's context manager
    assert client.user_id == "erinoverview" and client.token == "erin-bearer-token"
    who = _identity(client)
    assert who["user"] == "erinoverview" and who["token"] == "erin-bearer-token" and not who["user_pass"]
    assert who["view_url"] == "https://127.0.0.1:9"


def test_bound_reads_is_used_as_a_context_and_keeps_the_client(offline):
    from advisor.egeria_user_client import bound_reads, build_user_client
    client = build_user_client(ERIN)
    with bound_reads(client) as cm_value:
        assert cm_value is not client or True                      # whatever it yields, the client is unchanged
        assert client.user_id == "erinoverview" and client.token == "erin-bearer-token"


def test_a_client_without_the_requests_identity_is_refused(offline, monkeypatch):
    from advisor import egeria_user_client as euc
    monkeypatch.setattr("advisor.auth._trellis_apply_token", lambda c, t: None)     # the token never lands
    with pytest.raises(euc.WrongIdentity):
        euc.build_user_client(ERIN)


# ── the e2e guard: the identity a run is handed ──────────────────────────────

def _llm(monkeypatch):
    replies = [json.dumps(REPLY)]

    class LLM:
        def generate(self, prompt, **kw):
            return replies.pop(0)
    monkeypatch.setattr("advisor.agents.report_spec_elicitor_v2.get_planning_llm", lambda: LLM())
    monkeypatch.setattr("advisor.agents.report_spec_elicitor.get_planning_llm", lambda: LLM())


@pytest.fixture
def stores(tmp_path, monkeypatch):
    import advisor.report_draft as rd
    import advisor.report_spec_docs as rsd

    def _drafts():
        p = tmp_path / "drafts"
        p.mkdir(parents=True, exist_ok=True)
        return p
    monkeypatch.setattr(rd, "_drafts_path", _drafts)
    monkeypatch.setattr(rd, "_rdm_by_user", {}, raising=False)
    monkeypatch.setattr(rsd, "_load_paths", lambda: {k: tmp_path / k for k in ("inbox", "outbox", "trash", "versions")})
    for f in (v2.FLAG, v2.FIXES_FLAG, v2.STATUS_MERGE_FLAG):
        monkeypatch.delenv(f, raising=False)
    return tmp_path


def _chat(question="Which risks in the risk register have the highest importance?"):
    from advisor.agents.report_spec_elicitor import ReportSpecElicitor
    el = ReportSpecElicitor()
    r = el.start(question, egeria_credentials=ERIN)
    r = el.process(r["draft_id"], "continue_columns", egeria_credentials=ERIN)
    return el.process(r["draft_id"], "generate now", egeria_credentials=ERIN)


def test_the_chat_answer_runs_as_the_request_user_never_the_service_account(offline, exec_guard, stores,
                                                                              monkeypatch):
    _llm(monkeypatch)
    r = _chat()
    assert r["answer"]["ok"], r.get("response")
    assert r["answer"]["result"]["rows"][0]["importance"] == "Critical"
    _assert_the_requests_identity(exec_guard)


def test_validates_egeria_checks_ran_with_the_users_client(offline, exec_guard, egeria_type_fit, stores,
                                                           monkeypatch):
    """Appendix item 1: with the context-manager "client" these checks silently didn't run (an AttributeError
    swallowed, no network call). With the real client, the type-fit and held-spec checks ran, with that
    client, and a known type leaves no "can't tell"."""
    from pyegeria.egeria_tech_client import EgeriaTech
    _llm(monkeypatch)
    r = _chat()
    checks = {s["check"]: s["client"] for s in exec_guard if "check" in s}
    assert set(checks) == {"type_fit", "held_spec"}
    for c in checks.values():
        assert isinstance(c, EgeriaTech) and c.user_id == "erinoverview" and c.token == "erin-bearer-token"
    v = r["validation"]
    assert not any("Can't tell" in w for w in v["warnings"]), v["warnings"]
    assert not any("super types not read" in n for n in v["not_checked"]), v["not_checked"]
    assert "find method fits the target type" in v["checked"]
    assert "not every check ran" not in r["response"]


def test_a_check_that_couldnt_run_is_said_in_the_reply(offline, exec_guard, egeria_type_fit, stores, monkeypatch):
    """Appendix item 2: when Egeria's super types can't be read, the reply names it, not a bare "validated"."""
    async def unreadable(client, type_name):
        return None
    monkeypatch.setattr("md_processing.md_processing_utils.typed_properties.type_super_chain", unreadable)
    _llm(monkeypatch)
    r = _chat()
    assert "validated with pyegeria, but not every check ran" in r["response"]
    assert "⚠️ **Not every check ran:**" in r["response"]
    assert "super types couldn't be read from Egeria" in r["response"]
    assert "not checked: find method GovernanceOfficer.find_governance_definitions fits target type Risk" \
        in r["response"]


def test_execute_on_the_saved_spec_runs_as_the_request_user_never_the_service_account(offline, exec_guard,
                                                                                    stores, monkeypatch):
    from advisor.agents.report_spec_agent import get_report_spec_agent
    _llm(monkeypatch)
    chat = _chat()
    exec_guard.clear()
    out = get_report_spec_agent().execute(chat["doc_id"], egeria_credentials=ERIN)
    assert out["status"] == "Completed", out.get("response")
    _assert_the_requests_identity(exec_guard)
