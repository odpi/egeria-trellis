"""Chat never proposes a command it may not compose, in an LGCI plan either (2026-11-13).

advisor.chat_command_policy (MetadataExpert; pyegeria's `chat_allowed: false`) already keeps such commands out
of chat actions. The plan confirm gate controls RUNNING a plan, not what goes INTO it, so the same policy applies
to plans in three places:
  1. the composer: plan_validator.validate_commands, the choke point after every decomposition, canvas addition
     and draft PATCH, leaves the command out with a warning that names it and says why (never a silent drop);
     the keyword index still RECOGNISES the command, so "create a type def called X" is refused rather than
     mapped to some other command;
  2. /api/actions (the Plan Editor's command picker) and the command listings never offer it: cosmetic;
  3. plan execute (prepare_execute, execute, retry) refuses a plan carrying one before anything is dispatched,
     so a hand-edited document cannot run it: the real guard.

A temporary template tree supplies the MetadataExpert commands (the installed pyegeria does not ship them yet);
pyegeria's compact specs are an empty directory unless a test writes one.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from test_dr_egeria_identity import USER, agent  # noqa: F401
from test_plan_execute_confirm_gate import ERIN, api, docs, gpa, ran  # noqa: F401

import advisor.command_keyword_index as cki
from advisor import compact_specs, confirm_gate
from advisor.agents import governance_plan_agent as gpa_mod
from advisor.plan_validator import validate_commands


def _template(path, name):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {name}\n\n## {name}\n\n> {name} for the test.\n")


@pytest.fixture(autouse=True)
def world(tmp_path, monkeypatch):
    root = tmp_path / "templates" / "basic"
    _template(root / "MetadataExpert" / "Create_Type_Def.md", "Create Type Def")
    _template(root / "MetadataExpert" / "Delete_Type_Def.md", "Delete Type Def")
    _template(root / "MetadataExpert" / "Create_Instance.md", "Create Instance")
    _template(root / "Glossary" / "Create_Glossary.md", "Create Glossary")
    monkeypatch.setattr(cki.CommandKeywordIndex, "_find_template_root", lambda self: root)
    monkeypatch.setattr(cki, "_index", None)
    specs = tmp_path / "compact"
    specs.mkdir()
    monkeypatch.setattr(compact_specs, "compact_dir", lambda: specs)
    compact_specs.load.cache_clear()
    yield specs
    compact_specs.load.cache_clear()


# ── the index: recognised, refused, never offered ─────────────────────────────

def test_the_index_recognises_a_denied_command_and_marks_it_refused():
    m = cki.get_command_keyword_index().lookup("create type def")
    assert m is not None and m.command == "Create Type Def" and m.family == "MetadataExpert"
    assert m.refusal and "type system and raw instances" in m.refusal
    assert cki.get_command_keyword_index().lookup("create glossary").refusal is None


def test_the_picker_and_listings_never_offer_a_denied_command():
    from advisor.web.app import list_actions
    families = asyncio.run(list_actions())["families"]
    names = {c["name"] for cmds in families.values() for c in cmds}
    assert "MetadataExpert" not in families
    assert not names & {"Create Type Def", "Delete Type Def", "Create Instance"}
    assert "Create Glossary" in names
    found = cki.get_command_keyword_index().search_by_keyword("type def")
    assert not any(c["name"].endswith("Type Def") for cmds in found.values() for c in cmds)


def test_a_command_pyegeria_marks_not_chat_allowed_is_left_out_too(world):
    (world / "commands_glossary.json").write_text(json.dumps({"family": "Glossary", "commands": [
        {"display_name": "Create Glossary", "family": "Glossary", "chat_allowed": False}]}))
    compact_specs.load.cache_clear()
    families = cki.get_command_keyword_index().all_commands()
    assert "Create Glossary" not in {c["name"] for cmds in families.values() for c in cmds}


# ── the composer: left out, with words ────────────────────────────────────────

def test_validate_commands_leaves_a_denied_command_out_and_says_why():
    cmds = [{"action": "Create Glossary", "display_name": "Terms", "_answers_key": "Create Glossary:Terms"},
            {"action": "Create Type Def", "display_name": "CustomerRecord", "_answers_key": "Create Type Def:CustomerRecord"}]
    answers = {"Create Type Def:CustomerRecord": {"Display Name": "CustomerRecord"}}
    kept, answers, warnings = validate_commands(cmds, answers)
    assert [c["action"] for c in kept] == ["Create Glossary"]
    assert "Create Type Def:CustomerRecord" not in answers
    refusal = [w for w in warnings if w.startswith("Left out of the plan")]
    assert len(refusal) == 1
    assert "**Create Type Def** can't be part of an Advisor plan" in refusal[0] and "'CustomerRecord'" in refusal[0]


def test_a_composed_plan_never_contains_a_denied_command(gpa, monkeypatch):  # noqa: F811
    """'create a type def called X' resolves through the generic fallback to Create Type Def, which leaves the
    plan with words: the plan holds no denied command and the user is told why."""
    import advisor.egeria_context as ec

    class NoEgeria:                        # context enrichment is best-effort and not under test
        def __init__(self, *a, **k):
            pass

        def enrich_entities(self, entities):
            return None
    monkeypatch.setattr(ec, "EgeriaContext", NoEgeria)
    from advisor.chat_command_policy import plan_step_refusal
    for query in ("create a type def called CustomerRecord", "create a glossary called Terms and a type def called X"):
        out = gpa._decompose_intent(query, None, llm=None)
        actions = [c["action"] for c in out["commands"]]
        assert not [a for a in actions if plan_step_refusal(a)], (query, actions)
    out = gpa._decompose_intent("create a type def called CustomerRecord", None, llm=None)
    assert any("Create Type Def" in w and w.startswith("Left out of the plan") for w in out["validator_warnings"])


# ── plan execute: the real guard ──────────────────────────────────────────────

INJECTED = ("# Hand-edited plan\n\n## Command Sequence\n\n"
            "## Create Glossary\n\n### Display Name\nTerms\n\n"
            "## Delete Type Def\n\n### Type Name\nCustomerRecord\n")


def test_a_plan_with_a_denied_step_is_refused_when_shown(gpa, ran, docs):  # noqa: F811
    doc = docs.import_document(INJECTED, user_id="erinoverview")
    out = gpa.prepare_execute(doc, egeria_credentials=USER)
    assert out["state"] == "refused" and out.get("confirm_required") is not True
    assert "**Delete Type Def** can't be part of an Advisor plan" in out["response"]
    assert gpa_mod._PLAN_GATE.pending == {} and ran == []


def test_a_denied_step_is_refused_at_run_time_before_dispatch(gpa, ran, docs, agent):  # noqa: F811
    """Even with a valid confirmation (shown before the policy knew the command, say), execute refuses before
    _run_plan, where Dr.Egeria is dispatched."""
    doc = docs.import_document(INJECTED, user_id="erinoverview")
    identity = confirm_gate.caller_identity(agent._get_egeria_conn(USER))
    pending = gpa_mod._PLAN_GATE.register(identity, docs.load(doc), {
        "doc_id": doc, "source_folder": "inbox", "draft_id": None, "action": "execute",
        "ack_required": False, "non_idempotent": []})
    out = gpa.execute(doc, egeria_credentials=USER, pending_id=pending.pending_id, digest=pending.digest)
    assert out["state"] == "refused" and "Delete Type Def" in out["response"] and ran == []
    assert pending.pending_id not in gpa_mod._PLAN_GATE.pending


def test_retry_refuses_a_denied_step_too(gpa, ran, docs, agent, monkeypatch):  # noqa: F811
    monkeypatch.setattr(docs, "load_outbox", lambda d: INJECTED if d == "failed" else None)
    moved = []
    monkeypatch.setattr(docs, "move_to_inbox", lambda d: moved.append(d) or "x")
    identity = confirm_gate.caller_identity(agent._get_egeria_conn(USER))
    pending = gpa_mod._PLAN_GATE.register(identity, INJECTED, {
        "doc_id": "failed", "source_folder": "outbox", "draft_id": None, "action": "retry",
        "ack_required": False, "non_idempotent": []})
    out = gpa.retry("failed", egeria_credentials=USER, pending_id=pending.pending_id, digest=pending.digest)
    assert out["state"] == "refused" and ran == [] and moved == []


def test_the_execute_route_refuses_an_injected_denied_step(api, docs, ran):  # noqa: F811
    doc = docs.import_document(INJECTED, user_id="erinoverview")
    out = api.post(f"/api/plans/{doc}/execute", headers=ERIN, json={}).json()
    assert out["state"] == "refused" and "Delete Type Def" in out["response"] and ran == []


def test_a_plan_without_denied_steps_is_shown_as_before(gpa, ran, docs):  # noqa: F811
    doc = docs.import_document(INJECTED.split("## Delete Type Def")[0], user_id="erinoverview")
    assert gpa.prepare_execute(doc, egeria_credentials=USER)["confirm_required"] is True
