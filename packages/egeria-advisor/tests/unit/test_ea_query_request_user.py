"""F1 (ruling RULING-EA-V2-SERVICE-USER-CLIENT-2026-11-17): /api/query and /api/query/stream run the
pipeline in a worker thread, and run_in_executor doesn't carry contextvars. So the thread saw no request user,
and every chat draft landed in the SHARED drafts folder with user_id None. Now the thread runs in the
request's copied context: drafts go to the user's own folder. Back, cancel, discard, save and resume are
owner-checked like continue and generate.

Drafts started before this change stay where they were (the shared folder, no owner); they are not carried
over, and no owner is guessed for them.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from advisor import auth


@pytest.fixture
def client(monkeypatch, tmp_path):
    for var in ("TRELLIS_REQUIRE_LOGIN", "ADVISOR_REQUIRE_LOGIN", "TRELLIS_ANONYMOUS_READ", "ADVISOR_ANONYMOUS_READ"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ADVISOR_DATA_PATH", str(tmp_path))
    auth.reset_policy_cache()
    from advisor.web.app import app
    yield TestClient(app, raise_server_exceptions=False)
    auth.reset_policy_cache()


def _bearer(user: str) -> dict:
    return {"Authorization": f"Bearer {auth.create_access_token(user_id=user, egeria_token=user + '-bearer')}"}


class _Rag:
    """Records the user the worker thread runs as, and makes a draft there as the elicitor would."""

    def __init__(self):
        self.seen = []

    def _here(self):
        from advisor.request_context import current_user_id
        from advisor.report_draft import get_report_draft_manager
        user = current_user_id()
        dm = get_report_draft_manager()
        spec = dm.create(title=f"t_{user}", original_query="q", action_function=None, target_type="Risk",
                         columns=[], answers={})
        self.seen.append({"user": user, "draft_user": spec.get("user_id"), "root": str(dm._root)})
        return user

    def query(self, **kw):
        return {"query": kw["user_query"], "response": f"as {self._here()}", "query_type": "general"}

    def query_stream(self, **kw):
        user = self._here()
        yield f"data: {json.dumps({'type': 'done', 'result': {'response': f'as {user}'}})}\n\n"


@pytest.fixture
def rag(monkeypatch):
    fake = _Rag()
    monkeypatch.setattr("advisor.web.query._get_rag", lambda: fake)
    return fake


def test_the_query_thread_runs_as_the_requests_user(client, rag):
    r = client.post("/api/query", json={"query": "hello"}, headers=_bearer("erinoverview"))
    assert r.status_code == 200 and r.json()["response"] == "as erinoverview"
    assert rag.seen[-1]["user"] == "erinoverview" and rag.seen[-1]["draft_user"] == "erinoverview"


def test_the_stream_thread_runs_as_the_requests_user(client, rag):
    r = client.post("/api/query/stream", json={"query": "hello"}, headers=_bearer("erinoverview"))
    assert r.status_code == 200 and "as erinoverview" in r.text
    assert rag.seen[-1]["user"] == "erinoverview"


def test_two_users_get_their_own_draft_folders(client, rag):
    for user in ("erinoverview", "peterprofile"):
        client.post("/api/query", json={"query": "hello"}, headers=_bearer(user))
    erin, peter = rag.seen[-2], rag.seen[-1]
    assert erin["root"] != peter["root"]
    assert "erinoverview" in erin["root"] and "peterprofile" in peter["root"]


# ── nav commands are owner-checked ───────────────────────────────────────────

@pytest.fixture
def drafts(tmp_path, monkeypatch):
    import advisor.report_draft as rd

    def _drafts():
        p = tmp_path / "drafts"
        p.mkdir(parents=True, exist_ok=True)
        return p
    monkeypatch.setattr(rd, "_drafts_path", _drafts)
    monkeypatch.setattr(rd, "_rdm_by_user", {}, raising=False)
    return tmp_path


def _erins_draft(folder=None):
    """A v2 draft Erin owns: in the shared folder by default (as drafts were before F1), or in `folder`'s."""
    from advisor.report_draft import get_report_draft_manager
    dm = get_report_draft_manager(folder)
    spec = dm.create(title="erins", original_query="q", action_function=None, target_type="Risk",
                     columns=[], answers={})
    spec.update(v2=True, owner="erinoverview", phase="elicit_columns", phase_label="x")
    dm.save(spec)
    path = next(dm._root.glob(f"{spec['draft_id']}*"))
    return spec["draft_id"], path


NAVS = ["back", "cancel", "discard", "save_and_exit", "resume", "restart_qa"]


@pytest.mark.parametrize("nav", NAVS)
def test_nav_on_an_owned_draft_with_no_user_is_refused_and_the_file_is_unchanged(drafts, nav):
    """The shared folder (where an anonymous call, and every draft made before F1, lives): the owner check."""
    from advisor.agents import report_spec_elicitor_v2 as v2e
    from advisor.agents.report_spec_elicitor import ReportSpecElicitor
    draft_id, path = _erins_draft()
    before = path.read_bytes()
    out = getattr(ReportSpecElicitor(), nav)(draft_id)
    assert out["response"] == v2e.NOT_YOUR_DRAFT
    assert path.exists() and path.read_bytes() == before


@pytest.mark.parametrize("nav", NAVS)
def test_another_signed_in_user_cannot_reach_the_draft_and_the_file_is_unchanged(drafts, nav):
    """Peter's calls resolve to Peter's own folder: Erin's draft isn't there, whatever its id."""
    from advisor.agents.report_spec_elicitor import ReportSpecElicitor
    from advisor.request_context import using_user
    draft_id, path = _erins_draft()
    before = path.read_bytes()
    with using_user("peterprofile"):
        getattr(ReportSpecElicitor(), nav)(draft_id)
    assert path.exists() and path.read_bytes() == before


def test_another_signed_in_user_is_refused_on_a_shared_owned_draft(drafts, monkeypatch):
    """And where both resolve to one folder (no namespacing), the owner check refuses Peter too."""
    import advisor.report_draft as rd
    from advisor.agents import report_spec_elicitor_v2 as v2e
    from advisor.agents.report_spec_elicitor import ReportSpecElicitor
    from advisor.request_context import using_user
    draft_id, path = _erins_draft()
    shared = rd.get_report_draft_manager(None)
    monkeypatch.setattr("advisor.agents.report_spec_elicitor.get_report_draft_manager", lambda *a, **k: shared)
    before = path.read_bytes()
    with using_user("peterprofile"):
        for nav in NAVS:
            assert getattr(ReportSpecElicitor(), nav)(draft_id)["response"] == v2e.NOT_YOUR_DRAFT
    assert path.read_bytes() == before


def test_the_owner_can_still_navigate(drafts):
    from advisor.agents.report_spec_elicitor import ReportSpecElicitor
    from advisor.request_context import using_user
    draft_id, path = _erins_draft("erinoverview")      # where her own chat drafts now land
    with using_user("erinoverview"):
        out = ReportSpecElicitor().cancel(draft_id)
    assert out["response"] != "" and not path.exists()


# ── W1: cancel says "discarded" only when something was ─────────────────────

@pytest.mark.parametrize("nav", ["cancel", "discard"])
def test_cancelling_an_id_not_in_your_drafts_says_nothing_was_discarded(drafts, nav):
    from advisor.agents.report_spec_elicitor import NO_SUCH_DRAFT, ReportSpecElicitor
    from advisor.request_context import using_user
    draft_id, path = _erins_draft("erinoverview")
    before = path.read_bytes()
    with using_user("peterprofile"):
        out = getattr(ReportSpecElicitor(), nav)(draft_id)
    assert out["response"] == NO_SUCH_DRAFT
    assert path.read_bytes() == before


def test_cancelling_your_own_draft_still_says_discarded(drafts):
    from advisor.agents.report_spec_elicitor import ReportSpecElicitor
    from advisor.request_context import using_user
    draft_id, path = _erins_draft("erinoverview")
    with using_user("erinoverview"):
        out = ReportSpecElicitor().cancel(draft_id)
    assert out["response"] == "Report building session cancelled. Draft has been discarded."
    assert not path.exists()


def test_cancelling_a_plan_that_isnt_there_says_nothing_was_discarded(tmp_path, monkeypatch):
    import advisor.agents.plan_elicitor as pe
    from advisor.agents.report_spec_elicitor import NO_SUCH_DRAFT

    class _DM:
        def __init__(self, found):
            self.found = found

        def delete(self, draft_id):
            return self.found
    monkeypatch.setattr("advisor.session_logger.get_session_logger",
                        lambda: type("L", (), {"finalize": lambda *a, **k: None})())
    for found, words in ((False, NO_SUCH_DRAFT), (True, "Planning session cancelled. Your draft has been discarded.")):
        monkeypatch.setattr(pe, "get_draft_manager", lambda f=found: _DM(f))
        out = pe.PlanElicitor().cancel("draft_x")
        assert out["response"].startswith(words)
