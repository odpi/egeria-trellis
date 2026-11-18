"""S3b (EA Phase 0): no Egeria call with a user id and neither a token nor a
password.

trellis_auth.apply_token mints from the client's own credentials when the
token is empty, and pyegeria fills an empty password from its configured one,
so such a call would run under the user's id on the shared password. Each site
that resolves credentials and builds a pyegeria client must refuse with a
"sign in" sentence instead, and never reach apply_token / exec_report_spec.
The service-account fallback (password set) is unchanged.

One test class per site, with fakes: no Egeria, no DB.
"""
import asyncio
import types

import pytest
from fastapi import HTTPException

import pyegeria
import advisor.mcp_config as mcp_config
from advisor.egeria_signin import SIGN_IN_TO_READ_EGERIA, SIGN_IN_TO_RUN_REPORTS, lacks_credential

EMPTY = {"user_id": "anonymous", "password": "", "token": ""}
EXPIRED_SESSION = {"user_id": "erinoverview", "password": "", "token": ""}
SIGNED_IN = {"user_id": "erinoverview", "password": "", "token": "user-token"}


@pytest.fixture(autouse=True)
def platform(monkeypatch):
    monkeypatch.setattr(mcp_config, "get_pyegeria_platform_config", lambda *a, **k: {
        "view_server": "qs-view-server", "platform_url": "https://egeria.test:9443",
    })
    from advisor.config import settings
    monkeypatch.setattr(settings, "daemon_user_id", "svc-user", raising=False)
    monkeypatch.setattr(settings, "daemon_user_password", "svc-secret", raising=False)


class FakeClient:
    """Records construction and how it was authenticated."""
    built: list = []

    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs
        # as pyegeria's client: its own user and password are attributes (EA's apply_token reads them, TR-34)
        self.user_id = kwargs.get("user_id", args[2] if len(args) > 2 else None)
        self.user_pwd = kwargs.get("user_pwd", args[3] if len(args) > 3 else None)
        self.bearer, self.minted = [], []
        FakeClient.built.append(self)

    def set_bearer_token(self, token):
        self.bearer.append(token)

    def create_egeria_bearer_token(self, *a, **k):
        self.minted.append((a, k))
        return "minted"

    def get_report_spec_schema(self, **kw):
        return [{"attribute_path": "displayName"}]


@pytest.fixture
def fake_client():
    FakeClient.built = []
    return FakeClient


@pytest.fixture
def exec_calls(monkeypatch):
    calls = []

    def fake_exec(name, **kw):
        calls.append(kw)
        return {"kind": "text", "content": "ran"}

    monkeypatch.setattr(pyegeria, "exec_report_spec", fake_exec)
    return calls


def test_lacks_credential():
    assert lacks_credential("", "") and lacks_credential(None, None)
    assert not lacks_credential("t", "") and not lacks_credential("", "p")


# ── 1. advisor/egeria_context.py: EgeriaContext._make_client ──────────────────

class TestEgeriaContext:
    def _ctx(self, creds):
        from advisor.egeria_context import EgeriaContext
        return EgeriaContext(egeria_credentials=creds)

    @pytest.mark.parametrize("creds", [EMPTY, EXPIRED_SESSION])
    def test_no_token_no_password_stays_offline(self, fake_client, creds):
        assert self._ctx(creds)._make_client(fake_client) is None
        assert fake_client.built == []  # never built, so never minted

    def test_signed_in_uses_token(self, fake_client):
        c = self._ctx(SIGNED_IN)._make_client(fake_client)
        assert c.bearer == ["user-token"] and c.minted == []

    def test_service_fallback_unchanged(self, fake_client):
        c = self._ctx(None)._make_client(fake_client)
        assert c.kwargs["user_id"] == "svc-user" and c.kwargs["user_pwd"] == "svc-secret"
        assert len(c.minted) == 1 and c.bearer == []

    def test_sentence(self):
        assert SIGN_IN_TO_READ_EGERIA.startswith("Sign in")


# ── 2. advisor/web/reports.py: discover_draft_schema_internal ─────────────────

class TestSchemaDiscovery:
    @pytest.fixture
    def draft(self, monkeypatch, fake_client):
        import advisor.report_draft as rd
        import advisor.report_spec_parser as rsp
        import advisor.agents.report_spec_elicitor as rse
        import pyegeria.egeria_tech_client as etc
        from advisor.web import reports
        reports._SCHEMA_CACHE.clear()
        monkeypatch.setattr(rd, "resolve_report_draft",
                            lambda *a, **k: ("ns", {"action_function": "f", "answers": {}}))
        monkeypatch.setattr(rse, "get_report_spec_elicitor",
                            lambda: types.SimpleNamespace(_generate_report_spec_md=lambda d: "md"))
        monkeypatch.setattr(rsp, "parse_report_spec_markdown", lambda md: object())
        monkeypatch.setattr(rsp, "register_report_spec", lambda *a, **k: None)
        monkeypatch.setattr(etc, "EgeriaTech", fake_client)
        return reports

    @pytest.mark.parametrize("creds", [EMPTY, EXPIRED_SESSION])
    def test_refused_with_401_sign_in(self, draft, fake_client, creds):
        with pytest.raises(HTTPException) as ei:
            asyncio.run(draft.discover_draft_schema_internal("d1", egeria_credentials=creds))
        assert ei.value.status_code == 401 and ei.value.detail == SIGN_IN_TO_RUN_REPORTS
        assert fake_client.built == []

    def test_signed_in_uses_token(self, draft, fake_client):
        out = asyncio.run(draft.discover_draft_schema_internal("d1", egeria_credentials=SIGNED_IN))
        assert out == [{"attribute_path": "displayName"}]
        [c] = fake_client.built
        assert c.bearer == ["user-token"] and c.minted == []

    def test_service_fallback_unchanged(self, draft, fake_client):
        asyncio.run(draft.discover_draft_schema_internal("d1"))
        [c] = fake_client.built
        assert c.kwargs["user_pwd"] == "svc-secret" and len(c.minted) == 1


# ── 3. advisor/report_pipeline.py: ReportPipeline.run_report ──────────────────

class TestRunReport:
    @pytest.fixture
    def pipeline(self):
        from advisor.report_pipeline import ReportPipeline
        p = ReportPipeline.__new__(ReportPipeline)
        p._config_path = "unused"
        p._default_page_size = 10
        p._last_run_error = None
        return p

    @pytest.mark.parametrize("creds", [EMPTY, EXPIRED_SESSION])
    def test_refused_never_executes(self, pipeline, exec_calls, creds):
        assert pipeline.run_report("Any Report", egeria_credentials=creds) is None
        assert pipeline._last_run_error == SIGN_IN_TO_RUN_REPORTS
        assert exec_calls == []
        assert pipeline._classify_run_error(pipeline._last_run_error, "Any Report", "DICT") \
            == SIGN_IN_TO_RUN_REPORTS

    def test_signed_in_passes_token(self, pipeline, exec_calls):
        assert pipeline.run_report("Any Report", egeria_credentials=SIGNED_IN) == "ran"
        [kw] = exec_calls
        assert kw["token"] == "user-token" and kw["user_pass"] == ""

    def test_service_fallback_unchanged(self, pipeline, exec_calls):
        pipeline.run_report("Any Report")
        [kw] = exec_calls
        assert kw["user"] == "svc-user" and kw["user_pass"] == "svc-secret" and kw["token"] is None


# ── 4. advisor/agents/report_spec_agent.py: ReportSpecAgent.execute ───────────

class TestReportSpecAgent:
    @pytest.fixture
    def agent(self, monkeypatch):
        from advisor.agents import report_spec_agent as m
        from advisor.report_pipeline import ReportPipeline
        p = ReportPipeline.__new__(ReportPipeline)
        p._config_path = "unused"
        spec = types.SimpleNamespace(action=None, formats=[], heading="H")
        monkeypatch.setattr(m, "get_report_spec_doc_manager",
                            lambda: types.SimpleNamespace(load=lambda d: "md"))
        monkeypatch.setattr(m, "parse_report_spec_markdown", lambda c: spec)
        monkeypatch.setattr(m, "register_report_spec", lambda *a, **k: None)
        monkeypatch.setattr(m, "validate_report_spec", lambda s: [])
        monkeypatch.setattr(m, "get_report_pipeline", lambda: p)
        return m.ReportSpecAgent.__new__(m.ReportSpecAgent)

    @pytest.mark.parametrize("creds", [EMPTY, EXPIRED_SESSION])
    def test_refused_never_executes(self, agent, exec_calls, creds):
        out = agent.execute("my_spec", egeria_credentials=creds)
        assert out["response"] == SIGN_IN_TO_RUN_REPORTS and out["status"] == "Error"
        assert exec_calls == []

    def test_signed_in_passes_token(self, agent, exec_calls):
        agent.execute("my_spec", output_format="MD", egeria_credentials=SIGNED_IN)
        [kw] = exec_calls
        assert kw["token"] == "user-token" and kw["user_pass"] == ""

    def test_service_fallback_unchanged(self, agent, exec_calls):
        agent.execute("my_spec", output_format="MD")
        [kw] = exec_calls
        assert kw["user_pass"] == "svc-secret" and kw["token"] is None
