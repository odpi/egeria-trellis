"""Some Dr.Egeria commands are never composed from a chat message (READBACK-metadata-expert-family-2026-11-13 §2).

EA's action agent indexes pyegeria's packaged templates, the advanced tier included. Once the MetadataExpert
family's templates are there, "delete type def X" in chat would be composable, leaving the confirm gate as the
only guard. advisor.chat_command_policy refuses such a command in words before anything is extracted, composed,
validated or run: by pyegeria's `chat_allowed: false` (on the command or its family's compact file), and, until
that flag ships, by a deny-list of family names.

A real TemplateIndex over a temporary template tree picks the template; test_dr_egeria_identity's fakes record
every directive that reaches the dispatcher.
"""
from __future__ import annotations

import json

import pytest
from test_dr_egeria_identity import USER, agent, fakes  # noqa: F401

from advisor import chat_command_policy as policy
from advisor import compact_specs
from advisor.agents import dr_egeria_agent as mod
from advisor.agents.dr_egeria_agent import TemplateIndex


def _template(path, name):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {name}\n\n## {name}\n\n> {name} for the test.\n")


@pytest.fixture
def tree(tmp_path):
    base = tmp_path / "templates"
    _template(base / "advanced" / "MetadataExpert" / "Delete_Type_Def.md", "Delete Type Def")
    _template(base / "advanced" / "MetadataExpert" / "Delete_Instance.md", "Delete Instance")
    _template(base / "basic" / "Glossary" / "Create_Glossary.md", "Create Glossary")
    _template(base / "basic" / "Feedback" / "Create_Comment.md", "Create Comment")
    return base


@pytest.fixture
def specs(tmp_path, monkeypatch):
    """pyegeria's compact command specs, as a directory this test writes; empty unless a test adds files."""
    d = tmp_path / "compact_commands"
    d.mkdir()
    monkeypatch.setattr(compact_specs, "compact_dir", lambda: d)
    compact_specs.load.cache_clear()
    yield d
    compact_specs.load.cache_clear()


@pytest.fixture
def chat(agent, tree, specs, monkeypatch):  # noqa: F811
    agent._template_index = TemplateIndex([str(tree)])
    extracted = []

    def extract(q, t):
        extracted.append(t["command_name"])
        return {}
    monkeypatch.setattr(agent, "extract_params", extract)
    monkeypatch.setattr(mod, "compose_command", lambda t, p: f"## {t['command_name']}\n")
    mod._reset_pending_for_tests()
    agent.extracted = extracted
    yield agent
    mod._reset_pending_for_tests()


def directives(fakes):  # noqa: F811
    return [d for disp in fakes["dispatchers"] for d in disp.calls]


# ── refused in words ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("query, command", [
    ("delete type def CustomerRecord", "Delete Type Def"),
    ("delete instance 6a7f-guid", "Delete Instance"),
])
def test_a_metadata_expert_command_is_refused_before_anything_is_composed(chat, fakes, query, command):  # noqa: F811
    out = chat.handle(query, egeria_credentials=USER)
    assert out["chat_refused"] is True and out["command_name"] == command and out["family"] == "MetadataExpert"
    assert out["response"].startswith(f"**{command}** can't be composed from a chat message")
    assert "type system and raw instances" in out["response"] and "Nothing was composed or run" in out["response"]
    assert chat.extracted == [] and directives(fakes) == [] and mod._pending == {}
    assert out.get("next_context") is None and "pending_id" not in out


def test_a_dry_run_is_refused_too(chat, fakes):  # noqa: F811
    out = chat.handle("delete type def CustomerRecord", dry_run=True, egeria_credentials=USER)
    assert out["chat_refused"] is True and "markdown" not in out and chat.extracted == []


# ── other commands are unaffected ─────────────────────────────────────────────

def test_another_family_is_composed_and_validated_as_before(chat, fakes):  # noqa: F811
    out = chat.handle("create glossary Terms", egeria_credentials=USER)
    assert not out.get("chat_refused") and out["command_name"] == "Create Glossary"
    assert chat.extracted == ["Create Glossary"] and directives(fakes) == ["validate"]
    assert out["confirm_required"] is True


def test_the_installed_pyegeria_specs_refuse_nothing_outside_the_deny_list():
    """On the real packaged compact specs: no ordinary command is refused, and the deny-list still holds."""
    compact_specs.load.cache_clear()
    for command, family in [("Create Glossary", "Glossary"), ("Create Glossary Term", "Glossary"),
                            ("Create Project", "Project"), ("Link Term to Term", "Glossary")]:
        assert policy.chat_refusal(command, family) is None, command
    assert policy.chat_refusal("Delete Type Def", "MetadataExpert")
    compact_specs.load.cache_clear()


# ── pyegeria's chat_allowed flag ──────────────────────────────────────────────

def test_a_family_marked_not_chat_allowed_is_refused_without_the_deny_list(specs, monkeypatch):
    monkeypatch.setattr(policy, "CHAT_DENIED_FAMILIES", frozenset())
    (specs / "commands_metadata_expert_compact.json").write_text(json.dumps(
        {"family": "Metadata Expert", "chat_allowed": False, "commands": []}))
    out = policy.chat_refusal("Delete Type Def", "MetadataExpert")      # the template folder's spelling
    assert out and "specification marks it as not available from chat" in out
    assert policy.chat_refusal("Create Glossary", "Glossary") is None


def test_one_command_marked_not_chat_allowed_leaves_its_family_alone(specs):
    (specs / "commands_glossary_compact.json").write_text(json.dumps({"family": "Glossary", "commands": [
        {"display_name": "Purge Glossary", "family": "Glossary", "chat_allowed": False},
        {"display_name": "Create Glossary", "family": "Glossary"},
        {"display_name": "Update Glossary", "family": "Glossary", "chat_allowed": True},
    ]}))
    assert policy.chat_refusal("Purge Glossary", "Glossary")
    assert policy.chat_refusal("Create Glossary", "Glossary") is None
    assert policy.chat_refusal("Update Glossary", "Glossary") is None


def test_commands_given_as_a_dict_are_read_too(specs):
    (specs / "commands_x.json").write_text(json.dumps({"family": "X", "commands": {
        "Drop Everything": {"display_name": "Drop Everything", "family": "X", "chat_allowed": False}}}))
    assert policy.chat_refusal("Drop Everything", "X")


def test_an_unreadable_spec_file_flags_nothing_and_the_deny_list_still_holds(specs):
    (specs / "commands_broken.json").write_text("{ not json")
    assert policy.chat_refusal("Create Glossary", "Glossary") is None
    assert policy.chat_refusal("Delete Type Def", "Metadata Expert")


@pytest.mark.parametrize("spelling", ["MetadataExpert", "Metadata Expert", "metadata_expert", "METADATA-EXPERT"])
def test_family_names_compare_without_case_or_separators(spelling, specs):
    assert policy.chat_refusal("Delete Type Def", spelling)
