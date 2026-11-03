"""The journal entry IS the chat turn: derived from the turn alone, one write function behind both paths.

Covers the derivation, the chat's confirm-gated `write_journal` tool, the pane's route, and that the tool and the
route write the SAME row for the same turn. No live calls: a sqlite registry and fakes that bind real signatures.
"""
from __future__ import annotations

import inspect
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from resource_explorer.agents import journal_tool
from resource_explorer.journal import (
    ALREADY_WORDS, NO_EARLIER_ANSWER, NO_INVESTIGATION, PROPOSAL_WORDS, Journal, derive_turn_entry, write_turn_entry,
)
from resource_explorer.registry import Project, ProjectRegistry

QUESTION = "Who are the main contributors to docling_core?"
ANSWER = "| author | commits |\n|---|---|\n| alice | 120 |\n| bob | 80 |"
MANIFEST = {
    "compile_id": "abcdef1234567890",
    "packed": [
        {"key": "contributor_stats", "role": "evidence", "rung": "FULL", "size": 10,
         "pointer": {"resource_slug": "docling_core", "view": "v", "as_of": "2026-10-20T08:00:00+00:00"}},
        {"key": "commit_activity", "role": "evidence", "rung": "SUMMARY", "size": 5,
         "pointer": {"resource_slug": "docling_core", "view": "v", "as_of": "2026-10-18T08:00:00+00:00"}},
    ],
    "gaps": [{"key": "bus_factor", "reason": "never run"}],
    "coverage": {"kind": "analysis"},
}


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="docling_core", display_name="docling_core", github_url="https://github.com/x/d", description=""))
    r.record_compile("abcdef1234567890", "docling_core", QUESTION, MANIFEST, [], session_id="s1", user_id="alice")
    return r


@pytest.fixture(autouse=True)
def signed_in(monkeypatch):
    monkeypatch.setattr("resource_explorer.registry.current_user_id", lambda: "peterprofile")


@pytest.fixture
def investigation(registry):
    return registry.create_investigation("Contributors study")["slug"]


def _exchange(registry, session="s1", q=QUESTION, a=ANSWER, compile_id="abcdef1234567890"):
    registry.append_turn(session, "user", q, "docling_core", user_id="peterprofile", compile_id=compile_id)
    registry.append_turn(session, "assistant", a, "docling_core", user_id="peterprofile", compile_id=compile_id)


class TestMultiResourceTurn:
    """An integration or comparison turn is compiled by compile_multi_resource_context. Its entry must list what the
    answer was built from: the recorded manifest had no
    top-level `packed`, so the Evidence list was empty and the confidence counted 0 stored results."""

    def test_the_entry_lists_each_resources_evidence_and_a_real_confidence(self, registry):
        from resource_explorer import context_compile
        from resource_explorer.context_compile import (
            CompiledContext,
            compile_multi_resource_context,
        )

        registry.add(Project(slug="openlineage", display_name="openlineage", github_url="https://github.com/x/o",
                             description=""))
        as_of = {"docling_core": "2026-10-20T08:00:00+00:00", "openlineage": "2026-10-21T08:00:00+00:00"}

        def per_resource(registry, slug, question, **kw):
            return CompiledContext(
                text=f"## dependency_analysis\n{slug} deps", compile_id=f"id-{slug}",
                manifest={"used": 20, "gaps": [], "packed": [
                    {"key": "dependency_analysis", "role": "evidence", "rung": "FULL", "size": 20,
                     "pointer": {"resource_slug": slug, "view": "v", "as_of": as_of[slug]}}]},
                derivation=[])

        fake = mock.create_autospec(context_compile.compile_context, side_effect=per_resource)
        with mock.patch.object(context_compile, "compile_context", fake), \
             mock.patch.object(context_compile, "build_epistemological_matrix", return_value=("| m |", [])):
            compiled = compile_multi_resource_context(registry, ["docling_core", "openlineage"],
                                                      "Does docling_core integrate with openlineage?", budget=6000)
        assert compiled.manifest["recorded"] is True
        d = derive_turn_entry(registry, question="Does docling_core integrate with openlineage?", answer="Yes.",
                              compile_id=compiled.compile_id, investigation="s", resource_slug="docling_core")
        assert d["turn"]["evidence"] == ["dependency_analysis (docling_core)", "dependency_analysis (openlineage)"]
        assert d["turn"]["confidence"] == "answered from 2 stored results"
        assert d["turn"]["as_of"] == "2026-10-21T08:00:00+00:00"
        assert "no stored analysis answers" not in d["body"]


class TestDerivation:
    def test_the_body_is_the_turn_and_carries_the_evidence_ids(self, registry):
        d = derive_turn_entry(registry, question=QUESTION, answer=ANSWER, compile_id="abcdef1234567890",
                              investigation="study", resource_slug="docling_core")
        assert d["body"] == "\n".join([
            "Per chat", "", f"Question: {QUESTION}", "", "Answer:", "", ANSWER, "",
            "- Scope: investigation study - resource docling_core",
            "- As of: differs by section: contributor_stats 2026-10-20T08:00:00+00:00; commit_activity 2026-10-18T08:00:00+00:00",
            "- Confidence: answered from 2 stored results; 1 not measured (bus_factor)",
            "- Evidence: contributor_stats, commit_activity",
            "- Compile: abcdef12",
        ])
        assert d["turn"]["evidence"] == ["contributor_stats", "commit_activity"]
        assert d["turn"]["scope"] == "investigation study - resource docling_core"

    def test_the_same_turn_always_derives_the_same_body(self, registry):
        kw = dict(question=QUESTION, answer=ANSWER, compile_id="abcdef1234567890", investigation="s", resource_slug="d")
        assert derive_turn_entry(registry, **kw) == derive_turn_entry(registry, **kw)

    def test_no_compile_is_said_not_read_as_confident(self, registry):
        d = derive_turn_entry(registry, question="q", answer="a", compile_id=None, investigation="s", resource_slug=None)
        assert "- Confidence: no compile recorded, so confidence is not established" in d["body"]
        assert "- As of: not recorded" in d["body"] and "- Evidence: none recorded" in d["body"]
        assert "resource" not in d["body"].split("- Scope:")[1].split("\n")[0]

    def test_a_compile_that_is_not_on_file_is_said(self, registry):
        d = derive_turn_entry(registry, question="q", answer="a", compile_id="deadbeef00", investigation="s", resource_slug="x")
        assert "compile deadbeef is not on file" in d["body"]

    def test_the_note_is_stored_apart_from_the_derived_text(self, registry, investigation):
        out = write_turn_entry(registry, investigation=investigation, author="alice", question=QUESTION, answer=ANSWER,
                               compile_id="abcdef1234567890", resource_slug="docling_core", note="check bob's bot account")
        row = Journal(registry).entries("investigation", investigation)[0]
        assert row["note"] == "check bob's bot account" and "bot account" not in row["body"]
        assert row["turn"]["evidence"] == ["contributor_stats", "commit_activity"]
        assert row["suggested_to"] == [] and row["compile_id"] == "abcdef1234567890"
        assert out["note"] == row["note"] and out["already"] is False
        assert row["turn"]["answer"] == ANSWER and row["turn"]["scope"] == f"investigation {investigation} - resource docling_core"
        assert row["turn"]["compile_id"] == "abcdef1234567890"

    def test_a_hand_written_entry_keeps_its_plain_list(self, registry):
        Journal(registry).write("repo", "docling_core", author="a", body="hand", suggest_to=["Steward"])
        row = Journal(registry).entries("repo", "docling_core")[0]
        assert row["suggested_to"] == ["Steward"] and "note" not in row and "turn" not in row

    def test_suggested_to_is_always_a_plain_list(self, registry, investigation):
        write_turn_entry(registry, investigation=investigation, author="alice", question=QUESTION, answer=ANSWER,
                         compile_id="abcdef1234567890", resource_slug="docling_core", note="a note")
        Journal(registry).write("repo", "p", author="a", body="hand", suggest_to=["Steward"])
        with registry._conn() as conn:
            raw = [r[0] for r in conn.execute("SELECT suggested_to FROM resource_journal").fetchall()]
        assert sorted(raw) == ['["Steward"]', "[]"]
        for e in Journal(registry).entries("investigation", investigation) + Journal(registry).entries("repo", "p"):
            assert isinstance(e["suggested_to"], list)

    def test_the_note_and_the_turn_live_in_their_own_columns(self, registry, investigation):
        write_turn_entry(registry, investigation=investigation, author="alice", question=QUESTION, answer=ANSWER,
                         compile_id=None, resource_slug=None, note="mine")
        with registry._conn() as conn:
            r = conn.execute("SELECT note, turn_json, suggested_to FROM resource_journal").fetchone()
        assert r[0] == "mine" and '"turn_key"' in r[1] and r[2] == "[]"

    def test_a_second_add_of_the_same_turn_writes_no_second_row(self, registry, investigation):
        kw = dict(investigation=investigation, author="alice", question=QUESTION, answer=ANSWER,
                  compile_id="abcdef1234567890", resource_slug="docling_core")
        first = write_turn_entry(registry, **kw)
        again = write_turn_entry(registry, **kw, note="later")
        assert first["already"] is False and again["already"] is True and again["id"] == first["id"]
        assert len(Journal(registry).entries("investigation", investigation)) == 1
        # another author, or another answer under the same compile, is a different entry
        write_turn_entry(registry, **{**kw, "author": "bob"})
        write_turn_entry(registry, **{**kw, "answer": "different"})
        assert len(Journal(registry).entries("investigation", investigation)) == 3


class TestPromotion:
    def test_a_promoted_entry_carries_the_body_and_the_note_apart(self):
        from resource_explorer.feedback_egeria import entry_note_text

        e = {"body": "Per chat\n\nQuestion: q", "note": "check bob", "author": "alice"}
        out = entry_note_text(e)
        assert out.startswith(e["body"]) and out.endswith("\n\nNote (alice): check bob")

    def test_an_entry_without_a_note_promotes_exactly_as_before(self):
        from resource_explorer.feedback_egeria import entry_note_text

        assert entry_note_text({"body": "just the body", "author": "a"}) == "just the body"
        assert entry_note_text({"body": "just the body", "note": "  ", "author": "a"}) == "just the body"

    def test_the_payload_of_a_real_entry(self, registry, investigation):
        from resource_explorer.feedback_egeria import FeedbackItem, _payload_for

        e = write_turn_entry(registry, investigation=investigation, author="alice", question="q", answer="a",
                             compile_id=None, resource_slug=None, note="n1")
        row = {x["id"]: x for x in Journal(registry).entries("investigation", investigation)}
        item = mock.Mock(kind="note", id=e["id"])
        _, _, payload = _payload_for(registry, "investigation", investigation, item, "g", "alice", {}, row)
        assert payload["body"].endswith("Note (alice): n1") and "Question: q" in payload["body"]


class TestTool:
    def _ctx(self, session="s1", inv=None):
        journal_tool.set_journal_context(session, inv)

    def test_confirm_false_asks_in_the_exact_words_and_writes_nothing(self, registry, investigation):
        _exchange(registry)
        self._ctx(inv=investigation)
        assert journal_tool.write_journal_raw(confirm=False, registry=registry) == \
            "Add this turn to the investigation journal? [Add] [Not now]"
        assert Journal(registry).entries("investigation", investigation) == []
        prop = journal_tool.turn_proposal()
        assert prop["question"] == QUESTION and prop["answer"] == ANSWER and prop["investigation"] == investigation
        assert isinstance(prop["exchange_id"], int)

    def test_confirm_true_writes_one_row_authored_by_the_signed_in_user(self, registry, investigation):
        _exchange(registry)
        self._ctx(inv=investigation)
        assert journal_tool.write_journal_raw(confirm=True, note="n", registry=registry) == "Added to the investigation journal."
        rows = Journal(registry).entries("investigation", investigation)
        assert len(rows) == 1
        assert rows[0]["author"] == "peterprofile" and rows[0]["compile_id"] == "abcdef1234567890"
        assert rows[0]["note"] == "n"
        assert len(journal_tool.turn_written()) == 1

    def test_a_second_add_says_already_and_writes_no_second_row(self, registry, investigation):
        _exchange(registry)
        self._ctx(inv=investigation)
        journal_tool.write_journal_raw(confirm=True, registry=registry)
        assert journal_tool.write_journal_raw(confirm=True, registry=registry) == ALREADY_WORDS
        assert journal_tool.write_journal_raw(confirm=False, registry=registry) == ALREADY_WORDS
        assert len(Journal(registry).entries("investigation", investigation)) == 1

    def test_anonymous_is_refused_and_nothing_is_written(self, registry, investigation):
        _exchange(registry)
        self._ctx(inv=investigation)
        assert journal_tool.write_journal_raw(confirm=True, registry=registry, author="").startswith("Sign in")
        assert Journal(registry).entries("investigation", investigation) == []

    def test_this_is_the_last_turn_never_the_proposal_or_the_request(self, registry, investigation):
        _exchange(registry, q="older question", a="older answer", compile_id=None)
        _exchange(registry)
        _exchange(registry, q="save this to the journal", a=PROPOSAL_WORDS, compile_id=None)
        self._ctx(inv=investigation)
        journal_tool.write_journal_raw(confirm=True, registry=registry, author="u")
        body = Journal(registry).entries("investigation", investigation)[0]["body"]
        assert f"Question: {QUESTION}" in body and "older question" not in body and "save this" not in body

    def test_the_confirmation_exchange_is_never_this(self, registry, investigation):
        """After the person says yes the model answers in its own words (no service answer); a later request must
        still resolve to the real answer, not to the 'yes' exchange."""
        _exchange(registry)
        _exchange(registry, q="save this to the journal", a=PROPOSAL_WORDS, compile_id=None)
        _exchange(registry, q="yes", a="Done, I have put that in your journal for you.", compile_id=None)
        self._ctx(inv=investigation)
        journal_tool.write_journal_raw(confirm=True, registry=registry)
        body = Journal(registry).entries("investigation", investigation)[0]["body"]
        assert f"Question: {QUESTION}" in body and "Question: yes" not in body

    def test_no_investigation_says_so_and_writes_nothing(self, registry):
        _exchange(registry)
        self._ctx(inv=None)
        for confirm in (False, True):
            assert journal_tool.write_journal_raw(confirm=confirm, registry=registry) == \
                "no investigation is open · choose one on the rail"
        assert NO_INVESTIGATION == "no investigation is open · choose one on the rail"

    def test_no_earlier_answer_says_so(self, registry, investigation):
        self._ctx(inv=investigation)
        assert journal_tool.write_journal_raw(confirm=False, registry=registry) == NO_EARLIER_ANSWER

    def test_another_persons_private_investigation_is_words_and_no_write(self, registry, monkeypatch):
        monkeypatch.setattr("resource_explorer.registry.current_user_id", lambda: "owner-a")
        slug = registry.create_investigation("Secret study", project_classification="PersonalProject")["slug"]
        monkeypatch.setattr("resource_explorer.registry.current_user_id", lambda: "peterprofile")
        assert registry.get_investigation(slug) is None            # control: B cannot see it
        _exchange(registry)
        self._ctx(inv=slug)
        for confirm in (False, True):
            assert journal_tool.write_journal_raw(confirm=confirm, registry=registry) == NO_INVESTIGATION
        assert journal_tool.turn_proposal() is None
        assert Journal(registry).entries("investigation", slug) == []

    def test_the_callers_own_private_investigation_works(self, registry, monkeypatch):
        slug = registry.create_investigation("My study", project_classification="PersonalProject")["slug"]
        _exchange(registry)
        self._ctx(inv=slug)
        assert journal_tool.write_journal_raw(confirm=True, registry=registry) == "Added to the investigation journal."

    def test_a_shared_bucket_exchange_is_not_this_for_a_signed_in_caller(self, registry, investigation):
        registry.append_turn("s9", "user", "anon q", "x", user_id="", compile_id=None)
        registry.append_turn("s9", "assistant", "anon a", "x", user_id="", compile_id=None)
        self._ctx(session="s9", inv=investigation)
        assert journal_tool.write_journal_raw(confirm=False, registry=registry) == NO_EARLIER_ANSWER

    @pytest.mark.parametrize("q", ["save this to the journal", "Add that to the investigation journal", "put it in the journal",
                                   "add this to the journal", "journal this", "note this in the journal",
                                   "Please save this answer to my journal", "save this to the journal, please"])
    def test_journal_verbs_route_to_the_tool(self, q):
        assert journal_tool.is_journal_request(q)

    @pytest.mark.parametrize("q", [
        "Note that the orders table is partitioned; how many rows?",
        "What did I add to the journal yesterday?",
        "Does RE keep a journal of my findings?",
        "How do I log findings in the journal?",
        "add a column named journal",
        "Which columns store journal ids",
        "Can you save this to the journal?",
        "Who wrote the journal module?", "note the dependency counts", "what changed last week"])
    def test_other_questions_do_not(self, q):
        assert not journal_tool.is_journal_request(q)

    def test_the_tool_binds_the_real_signature(self):
        assert list(inspect.signature(journal_tool.write_journal_raw).parameters)[:2] == ["confirm", "note"]
        mock.create_autospec(journal_tool.write_journal_raw, spec_set=True)(confirm=False, note="")


class TestConversationAgentRouting:
    def test_save_this_to_the_journal_goes_to_the_tool_not_the_model(self, registry, investigation):
        from resource_explorer.agents.conversation_agent import ConversationAgent

        _exchange(registry)
        agent = ConversationAgent.__new__(ConversationAgent)
        agent.session_id, agent.investigation = "s1", investigation
        agent._last_compiled = None
        agent._run_persistent = mock.Mock(side_effect=AssertionError("the model must not be asked"))
        with mock.patch("resource_explorer.registry.ProjectRegistry", return_value=registry):
            words = agent.handle("save this to the journal")
        assert words == "Add this turn to the investigation journal? [Add] [Not now]"
        assert agent._journal_proposal["investigation"] == investigation

    def test_the_a2a_agent_without_writes_is_not_routed(self):
        from resource_explorer.agents.conversation_agent import ConversationAgent

        a = ConversationAgent.__new__(ConversationAgent)
        a.allow_writes = False
        assert "write_journal" not in [getattr(t, "name", "") for t in a._write_tools()]


class TestRouteAndToolWriteTheSameRow:
    @pytest.fixture
    def client(self, registry, monkeypatch):
        def fake_init(self, db_path=None, database_url=None):
            self.__dict__ = registry.__dict__

        # same parameters as the real constructor
        real = list(inspect.signature(ProjectRegistry.__init__).parameters)[1:]
        assert real == ["db_path", "database_url"]
        monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__", fake_init)
        monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
        monkeypatch.setattr("resource_explorer.web.routes.journal.get_current_user",
                            lambda request: {"user_id": "peterprofile"})
        monkeypatch.setattr("resource_explorer.registry.current_user_id", lambda: "peterprofile")
        from resource_explorer.web.app import app
        return TestClient(app)

    def _eid(self, registry, session="s1"):
        return registry.load_exchanges(session, user_id="peterprofile")[0]["exchange_id"]

    def _body(self, registry, **kw):
        return {"session_id": "s1", "exchange_id": self._eid(registry), **kw}

    def test_preview_derives_from_the_stored_exchange_and_writes_nothing(self, client, registry, investigation):
        _exchange(registry)
        r = client.post(f"/api/journal/investigation/{investigation}/turn", json=self._body(registry))
        assert r.status_code == 200 and r.json()["written"] is False and r.json()["already"] is False
        assert f"Question: {QUESTION}" in r.json()["body"]
        assert Journal(registry).entries("investigation", investigation) == []

    def test_the_client_cannot_supply_the_text(self, client, registry, investigation):
        _exchange(registry)
        r = client.post(f"/api/journal/investigation/{investigation}/turn",
                        json=self._body(registry, question="FORGED", answer="FORGED", confirm=True))
        assert r.status_code == 200
        body = Journal(registry).entries("investigation", investigation)[0]["body"]
        assert "FORGED" not in body and f"Question: {QUESTION}" in body

    def test_the_route_and_the_tool_write_identical_rows(self, client, registry, investigation, monkeypatch):
        _exchange(registry)
        r = client.post(f"/api/journal/investigation/{investigation}/turn",
                        json=self._body(registry, note="same note", confirm=True))
        assert r.status_code == 200 and r.json()["written"] is True
        # the tool by a second author, so the dedupe does not hide the comparison
        journal_tool.set_journal_context("s1", investigation)
        journal_tool.write_journal_raw(confirm=True, note="same note", registry=registry, author="peterprofile2")
        a, b = sorted(Journal(registry).entries("investigation", investigation), key=lambda e: e["author"])
        for key in ("body", "compile_id", "suggested_to", "turn", "note"):
            assert a[key] == b[key], key
        assert a["id"] != b["id"] and a["suggested_to"] == []

    def test_adding_twice_says_already_and_writes_once(self, client, registry, investigation):
        _exchange(registry)
        url = f"/api/journal/investigation/{investigation}/turn"
        body = self._body(registry, confirm=True)
        assert client.post(url, json=body).json()["written"] is True
        second = client.post(url, json=body).json()
        assert second["written"] is False and second["already"] is True
        assert len(Journal(registry).entries("investigation", investigation)) == 1
        assert client.post(url, json={**body, "confirm": False}).json()["already"] is True

    def test_a_proposal_exchange_is_not_journaled(self, client, registry, investigation):
        _exchange(registry)
        _exchange(registry, q="save this to the journal", a=PROPOSAL_WORDS, compile_id=None)
        r = client.post(f"/api/journal/investigation/{investigation}/turn", json=self._body(registry, confirm=True))
        assert r.status_code == 422
        assert Journal(registry).entries("investigation", investigation) == []

    def test_anonymous_is_refused_even_for_the_preview_and_unknowns_are_404(self, client, registry, monkeypatch, investigation):
        _exchange(registry)
        body = self._body(registry, confirm=True)
        assert client.post("/api/journal/investigation/nope/turn", json=body).status_code == 404
        assert client.post(f"/api/journal/investigation/{investigation}/turn",
                           json={**body, "exchange_id": 99999}).status_code == 404
        monkeypatch.setattr("resource_explorer.web.routes.journal.get_current_user", lambda request: None)
        for confirm in (True, False):
            assert client.post(f"/api/journal/investigation/{investigation}/turn",
                               json={**body, "confirm": confirm}).status_code == 401

    def test_another_persons_exchange_is_not_on_file(self, client, registry, investigation):
        registry.append_turn("s2", "user", "theirs", "x", user_id="someone-else")
        registry.append_turn("s2", "assistant", "their answer", "x", user_id="someone-else")
        eid = registry.load_exchanges("s2", user_id="someone-else")[0]["exchange_id"]
        r = client.post(f"/api/journal/investigation/{investigation}/turn",
                        json={"session_id": "s2", "exchange_id": eid, "confirm": True})
        assert r.status_code == 404

    def test_another_persons_private_investigation_is_404(self, client, registry, monkeypatch):
        monkeypatch.setattr("resource_explorer.registry.current_user_id", lambda: "owner-a")
        slug = registry.create_investigation("Secret", project_classification="PersonalProject")["slug"]
        monkeypatch.setattr("resource_explorer.registry.current_user_id", lambda: "peterprofile")
        _exchange(registry)
        assert client.post(f"/api/journal/investigation/{slug}/turn", json=self._body(registry, confirm=True)).status_code == 404


class TestQueryPersistsTheCompileAndTheExchange:
    def test_persist_turn_returns_the_assistant_row_id_and_keeps_the_compile(self, registry, monkeypatch):
        monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                            lambda self, db_path=None, database_url=None: setattr(self, "__dict__", registry.__dict__) or None)
        from resource_explorer.web.routes.query import _persist_turn

        eid = _persist_turn("s7", "q", "a", "docling_core", compile_id="abcdef1234567890")
        ex = registry.load_exchanges("s7", user_id="peterprofile")[0]
        assert ex["exchange_id"] == eid and ex["compile_id"] == "abcdef1234567890"

    def test_the_blocking_path_persists_the_compile_id(self):
        import re as _re
        from pathlib import Path
        from resource_explorer.web.routes import query

        src = Path(query.__file__).read_text()
        ask_src = src[src.index("async def ask("):src.index("async def stream(")]
        assert _re.search(r"_persist_turn\([^)]*compile_id=_compile_id_of\(agent\)", ask_src, _re.S)


class TestOneNotePerEntry:
    """The owner's rulings: promote the note too, in the SAME Egeria Note as the entry (never a second Note)."""

    def _create(self, payload):
        from resource_explorer import feedback_egeria as fe

        fb = mock.Mock()
        fb.get_notes_for_note_log.return_value = []
        fb.create_note.return_value = "note-guid"
        clients = mock.Mock()
        clients.require.return_value = fb
        with mock.patch.object(fe, "ensure_note_log", return_value="log-guid"):
            assert fe.create_note(clients, payload) == "note-guid"
        return fb

    def _payload(self, registry, investigation, note):
        from resource_explorer.feedback_egeria import _payload_for

        e = write_turn_entry(registry, investigation=investigation, author="alice", question="q", answer="a",
                             compile_id=None, resource_slug=None, note=note)
        rows = {x["id"]: x for x in Journal(registry).entries("investigation", investigation)}
        return _payload_for(registry, "investigation", investigation, mock.Mock(kind="note", id=e["id"]),
                            "g", "alice", {}, rows)[2], rows[e["id"]]

    def test_one_note_is_created_and_its_text_holds_the_entry_then_the_note(self, registry, investigation):
        payload, row = self._payload(registry, investigation, "check bob")
        fb = self._create(payload)
        assert fb.create_note.call_count == 1
        text = fb.create_note.call_args.kwargs["body"]["properties"]["description"]
        assert text.startswith(row["body"]) and "\n\nNote (alice): check bob" in text

    def test_without_a_note_the_one_note_holds_only_the_body(self, registry, investigation):
        payload, row = self._payload(registry, investigation, "")
        fb = self._create(payload)
        assert fb.create_note.call_count == 1
        assert fb.create_note.call_args.kwargs["body"]["properties"]["description"] == row["body"]
