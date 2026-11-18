"""A re-run that repeats non-idempotent commands needs its own acknowledgement (2026-11-13).

Re-running a plan (Re-run from outbox, or Retry) repeats every command in it. A Link command run twice adds
the relationship twice, and pyegeria marks some commands non-idempotent (MetadataExpert's Create/Link
Instance, as it adds the flag). So a re-run lists those commands and needs more than "yes":
  - chat "yes, re-run";
  - API "ack_non_idempotent": true (exactly true);
  - in the browser, a ticked checkbox (plan_confirm.test.mjs).
A plain "yes" is refused in words, and the shown plan stays confirmable. First runs are unaffected.

Which commands: pyegeria's compact spec `idempotent` (on the command or its family) when it says; where it
says nothing, any command whose verb is Link, Attach, Initiate or Import (UNSAFE_FALLBACK_VERBS).
"""
from __future__ import annotations

import json

import pytest
from test_dr_egeria_identity import USER, agent  # noqa: F401
from test_plan_execute_confirm_gate import (  # noqa: F401
    ERIN,
    RecordingPlanAgent,
    api,
    docs,
    gpa,
    ran,
)

from advisor import compact_specs
from advisor.agents import governance_plan_agent as gpa_mod
from advisor.agents.governance_plan_agent import (
    PLAN_ACK_REQUIRED,
    non_idempotent_commands,
    plan_command_names,
)

PLAN = ("# Linking plan\n\n## Command Sequence\n\n"
        "## Create Glossary\n\n### Display Name\nTerms\n\n"
        "## Link Term to Term\n\n### Term 1\nA\n\n"
        "## Link Term to Term\n\n### Term 1\nB\n\n"
        "## Create Term\n\n### Display Name\nCustomer\n")
SAFE = ("# Safe plan\n\n## Command Sequence\n\n## Create Glossary\n\n### Display Name\nTerms\n\n"
        "## Update Glossary\n\n### Display Name\nTerms\n")


@pytest.fixture
def outbox_plan(docs):  # noqa: F811
    def make(content=PLAN):
        doc = docs.import_document(content, user_id="erinoverview")
        return docs.move_to_outbox(doc, "")
    return make


# ── which commands ────────────────────────────────────────────────────────────

def test_the_plan_s_commands_in_order():
    sect = PLAN.split("## Command Sequence\n", 1)[1]
    assert plan_command_names(sect) == ["Create Glossary", "Link Term to Term", "Link Term to Term", "Create Term"]


def test_without_a_flag_link_commands_are_unsafe_and_counted():
    assert non_idempotent_commands(["Create Glossary", "Link Term to Term", "Link Term to Term"]) == [
        {"command": "Link Term to Term", "count": 2,
         "why": "a Link command: running it again adds the relationship again"}]


def test_the_verb_decides_not_the_spelling():
    """'Linked Data Field' is pyegeria's verb Link (a relationship); 'Create Glossary' is not a link."""
    out = {u["command"] for u in non_idempotent_commands(["Linked Data Field", "Create Glossary", "Update Glossary"])}
    assert out == {"Linked Data Field"}


@pytest.fixture
def specs(tmp_path, monkeypatch):
    d = tmp_path / "compact"
    d.mkdir()
    monkeypatch.setattr(compact_specs, "compact_dir", lambda: d)
    compact_specs.load.cache_clear()
    yield d
    compact_specs.load.cache_clear()


def test_pyegeria_s_idempotent_flag_decides_when_it_says(specs):
    (specs / "commands_x.json").write_text(json.dumps({"family": "Glossary", "commands": [
        {"display_name": "Create Term", "family": "Glossary", "verb": "Create", "idempotent": False},
        {"display_name": "Link Term to Term", "family": "Glossary", "verb": "Link", "idempotent": True},
    ]}))
    out = non_idempotent_commands(["Create Term", "Link Term to Term", "Create Glossary"])
    assert out == [{"command": "Create Term", "count": 1,
                    "why": "its Dr.Egeria specification marks it non-idempotent"}]


def test_a_family_marked_non_idempotent_covers_its_commands(specs):
    (specs / "commands_me.json").write_text(json.dumps({"family": "Metadata Expert", "idempotent": False,
        "commands": [{"display_name": "Create Instance", "family": "Metadata Expert", "verb": "Create"}]}))
    assert [u["command"] for u in non_idempotent_commands(["Create Instance"])] == ["Create Instance"]


@pytest.mark.parametrize("name, verb", [
    ("Link Term to Term", "Link"),
    ("Attach Search Keyword", "Attach"),
    ("Initiate Survey", "Initiate"),
    ("Import Data Product", "Import"),
])
def test_without_a_flag_each_fallback_verb_is_unsafe(name, verb):
    """pyegeria 6.2.40 flags nothing, so the verb decides: one real command of each fallback verb."""
    out = non_idempotent_commands([name, "Create Glossary"])
    assert [u["command"] for u in out] == [name]
    assert out[0]["why"].startswith(f"a{'n' if verb[0] in 'AI' else ''} {verb} command")


def test_the_spec_verb_decides_for_a_name_without_the_verb_word(specs):
    """pyegeria keys 'Initiate Engine Action' as engine_action; a plan naming either is an Initiate command."""
    (specs / "commands_aa.json").write_text(json.dumps({"family": "Action Author", "commands": {
        "Initiate Engine Action": {"display_name": "engine_action", "verb": "Initiate"}}}))
    out = non_idempotent_commands(["engine_action", "Initiate Engine Action"])
    assert [u["command"] for u in out] == ["engine_action", "Initiate Engine Action"]
    assert all(u["why"].startswith("an Initiate command") for u in out)


@pytest.mark.parametrize("name, verb", [
    ("Link Term to Term", "Link"),
    ("Attach Search Keyword", "Attach"),
    ("Initiate Survey", "Initiate"),
    ("Import Data Product", "Import"),
])
def test_idempotent_true_overrides_the_fallback_verb(specs, name, verb):
    """Keyed by name the way pyegeria's dict-shaped files are, so the flag reaches the plan's header name."""
    (specs / "commands_y.json").write_text(json.dumps({"family": "F", "commands": {
        name: {"display_name": name.lower().replace(" ", "_"), "verb": verb, "idempotent": True}}}))
    assert non_idempotent_commands([name]) == []


def test_other_verbs_stay_safe_without_a_flag():
    assert non_idempotent_commands(["Create Glossary", "Update Glossary", "Classify Element", "Detach Term"]) == []


# ── the gate ──────────────────────────────────────────────────────────────────

def test_a_re_run_lists_its_non_idempotent_commands_and_asks_for_more_than_yes(gpa, ran, outbox_plan):  # noqa: F811
    out_id = outbox_plan()
    shown = gpa.prepare_execute(out_id, source_folder="outbox", egeria_credentials=USER)
    assert shown["rerun"] is True and shown["ack_required"] is True
    assert shown["non_idempotent"] == [{"command": "Link Term to Term", "count": 2,
                                        "why": "a Link command: running it again adds the relationship again"}]
    assert "- Link Term to Term (×2)" in shown["response"] and "**yes, re-run**" in shown["response"]
    assert shown["next_context"]["ack_required"] is True
    assert shown["command_names"] == ["Create Glossary", "Link Term to Term", "Link Term to Term", "Create Term"]
    assert ran == []


def test_a_plain_yes_to_such_a_re_run_is_refused_and_the_plan_stays_confirmable(gpa, ran, outbox_plan):  # noqa: F811
    out_id = outbox_plan()
    shown = gpa.prepare_execute(out_id, source_folder="outbox", egeria_credentials=USER)
    no = gpa.confirm(shown["pending_id"], shown["plan_digest"], egeria_credentials=USER)
    assert no["state"] == "refused" and no["response"].startswith(PLAN_ACK_REQUIRED) and ran == []
    assert "Link Term to Term (×2)" in no["response"]
    assert no["next_context"] == {"task": "plan_execute_confirm", "pending_id": shown["pending_id"],
                                  "digest": shown["plan_digest"], "draft_id": None, "ack_required": True}
    yes = gpa.confirm(shown["pending_id"], shown["plan_digest"], egeria_credentials=USER, ack_non_idempotent=True)
    assert yes["response"] == "ran" and len(ran) == 1 and ran[0]["source_folder"] == "outbox"
    again = gpa.confirm(shown["pending_id"], shown["plan_digest"], egeria_credentials=USER, ack_non_idempotent=True)
    assert again["state"] == "already_run" and len(ran) == 1


def test_retry_needs_the_acknowledgement_too(gpa, ran, docs, monkeypatch):  # noqa: F811
    doc = docs.import_document(PLAN, user_id="erinoverview")
    monkeypatch.setattr(docs, "load_outbox", lambda d: PLAN if d == "failed" else None)
    monkeypatch.setattr(docs, "move_to_inbox", lambda d: doc)
    shown = gpa.prepare_execute("failed", action="retry", egeria_credentials=USER)
    assert shown["ack_required"] is True
    assert gpa.retry("failed", egeria_credentials=USER, pending_id=shown["pending_id"],
                     digest=shown["plan_digest"])["response"].startswith(PLAN_ACK_REQUIRED)
    assert ran == []
    out = gpa.retry("failed", egeria_credentials=USER, pending_id=shown["pending_id"], digest=shown["plan_digest"],
                    ack_non_idempotent=True)
    assert out["response"] == "ran" and len(ran) == 1


def test_a_first_run_needs_no_acknowledgement(gpa, ran, docs):  # noqa: F811
    doc = docs.import_document(PLAN, user_id="erinoverview")
    shown = gpa.prepare_execute(doc, egeria_credentials=USER)
    assert shown["rerun"] is False and shown["ack_required"] is False and shown["non_idempotent"] == []
    assert "yes, re-run" not in shown["response"]
    assert gpa.confirm(shown["pending_id"], shown["plan_digest"], egeria_credentials=USER)["response"] == "ran"


def test_a_re_run_with_nothing_unsafe_needs_only_yes(gpa, ran, outbox_plan):  # noqa: F811
    out_id = outbox_plan(SAFE)
    shown = gpa.prepare_execute(out_id, source_folder="outbox", egeria_credentials=USER)
    assert shown["rerun"] is True and shown["ack_required"] is False
    assert gpa.confirm(shown["pending_id"], shown["plan_digest"], egeria_credentials=USER)["response"] == "ran"


# ── the API ───────────────────────────────────────────────────────────────────

def test_the_rerun_route_needs_ack_non_idempotent_exactly_true(api, ran, outbox_plan):  # noqa: F811
    out_id = outbox_plan()
    shown = api.post(f"/api/plans/{out_id}/rerun", headers=ERIN).json()
    assert shown["ack_required"] is True and ran == []
    body = {"pending_id": shown["pending_id"], "digest": shown["plan_digest"]}
    for ack in (None, False, "true", 1):
        sent = dict(body) if ack is None else {**body, "ack_non_idempotent": ack}
        out = api.post(f"/api/plans/{out_id}/rerun", headers=ERIN, json=sent).json()
        assert out["state"] == "refused" and out["ack_required"] is True, ack
    assert ran == []
    ok = api.post(f"/api/plans/{out_id}/rerun", headers=ERIN, json={**body, "ack_non_idempotent": True}).json()
    assert ok["response"] == "ran" and len(ran) == 1


# ── chat ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def router(monkeypatch):
    from advisor.rag_system import RAGSystem
    rec = RecordingPlanAgent()
    monkeypatch.setattr(gpa_mod, "get_governance_plan_agent", lambda: rec)
    return RAGSystem.__new__(RAGSystem), rec


CTX = {"task": "plan_execute_confirm", "pending_id": "p1", "digest": "d" * 64, "ack_required": True}


@pytest.mark.parametrize("words, ack", [("yes", False), ("go ahead", False), ("yes, re-run", True),
                                        ("Yes rerun it", True), ("yes, re-run anyway.", True)])
def test_chat_acknowledges_only_with_yes_re_run(router, words, ack):
    rag, rec = router
    rag._process_query(words, include_context=True, context=CTX, egeria_credentials=USER)
    assert rec.confirms == [("p1", "d" * 64, USER)] and rec.acks == [ack]


@pytest.mark.parametrize("words", ["yes, re-run and also delete X", "re-run", "rerun it"])
def test_other_words_are_not_an_acknowledgement(words):
    from advisor.rag_system import _PLAN_RERUN_ACK
    assert not _PLAN_RERUN_ACK.match(words.lower())
