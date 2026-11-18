"""A-P read-back P-1: Egeria's perspectives are the list, and a role profile maps each onto EA's role
behaviour (prompts, routing, Dr.Egeria template families, question-spec boosts).

Before this, none of the built-in CSV's four role names existed among Egeria's 12 perspectives, so
once Egeria answered, every role-keyed behaviour silently stopped applying. The perspectives come
from the live laz capture in fixtures/egeria_live (see its README).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from loguru import logger

from advisor import perspective_manager as pm

PERSPECTIVES = json.loads((Path(__file__).resolve().parent / "fixtures" / "egeria_live"
                           / "find_perspectives.json").read_text())

EXPECTED = {
    "App/AI Builder": "developer",
    "Data Expert": "data_engineer",
    "Steward": "data_steward",
    "Governance": "governance_officer",
    "Privacy": "governance_officer",
    "Security": "governance_officer",
    "Architecture": "general",
    "Owner": "general",
    "Community": "general",
    "Consumer": "general",
    "Administration": "general",
    "Financial": "general",
}


@pytest.fixture
def from_egeria(monkeypatch):
    """The perspective list as a successful Egeria load leaves it."""
    monkeypatch.setattr(pm, "_perspectives", pm.rows_from_egeria(PERSPECTIVES))
    from datetime import datetime, timezone
    monkeypatch.setattr(pm, "_loaded_at", datetime.now(timezone.utc))
    monkeypatch.setattr(pm, "_logged_once", set())
    return pm.get_all()


@pytest.fixture
def logs():
    lines: list = []
    sink = logger.add(lambda m: lines.append(m.record["message"]), level="INFO")
    yield lines
    logger.remove(sink)


def test_each_live_perspective_gets_its_role_profile(from_egeria):
    assert {r["egeria_name"]: r["role_profile"] for r in from_egeria} == EXPECTED
    for row in from_egeria:
        assert pm.role_profile(row["advisor_key"]) == EXPECTED[row["egeria_name"]]


def test_the_built_in_list_is_the_same_twelve():
    built_in = pm._load_from_csv()
    assert {r["egeria_name"]: r["role_profile"] for r in built_in} == EXPECTED
    live = {r["egeria_name"]: r["advisor_key"] for r in pm.rows_from_egeria(PERSPECTIVES)}
    assert {r["egeria_name"]: r["advisor_key"] for r in built_in} == live
    assert {r["advisor_key"] for r in built_in} >= {"app_ai_builder", "data_expert", "steward", "governance"}


def test_general_perspectives_say_so_in_the_tooltip(from_egeria):
    for row in from_egeria:
        if row["role_profile"] == "general":
            assert row["tooltip"] == pm.GENERAL_TOOLTIP


class TestPrompts:
    def _prompt(self, perspective):
        from advisor.prompt_templates import PromptTemplateManager
        return PromptTemplateManager().get_system_prompt(perspective=perspective)

    def test_app_ai_builder_gets_todays_developer_prompt(self, from_egeria):
        from advisor.prompt_templates import PromptTemplateManager
        prompt = self._prompt("app_ai_builder")
        assert PromptTemplateManager._PERSPECTIVE_ADDENDA["developer"] in prompt
        assert prompt == self._prompt("developer")  # the old stored key gives the same prompt

    @pytest.mark.parametrize("key, profile", [("data_expert", "data_engineer"), ("steward", "data_steward"),
                                              ("privacy", "governance_officer"), ("security", "governance_officer")])
    def test_each_mapped_perspective_gets_its_role_addendum(self, from_egeria, key, profile):
        from advisor.prompt_templates import PromptTemplateManager
        assert PromptTemplateManager._PERSPECTIVE_ADDENDA[profile] in self._prompt(key)

    def test_general_has_no_role_addendum(self, from_egeria):
        assert self._prompt("financial") == self._prompt(None)


class TestTemplateFamilies:
    def test_families_follow_the_profile(self, from_egeria):
        from advisor.agents.tools import _priority_families
        assert _priority_families("steward") == _priority_families("data_steward")
        assert "glossary" in _priority_families("steward")
        assert "governanceofficer" in _priority_families("privacy") or "governance officer" in _priority_families("privacy")

    def test_general_gets_only_the_any_rows(self, from_egeria):
        from advisor.agents.tools import _load_perspective_families, _normalise, _priority_families
        any_rows = {_normalise(f): b for f, b in _load_perspective_families()["any"].items()}
        assert _priority_families("financial") == any_rows
        assert _priority_families(None) == {}


class TestRouting:
    def test_an_old_stored_key_resolves_to_its_perspective(self, from_egeria):
        from advisor.perspective_routing import PerspectiveRoutingEngine

        class Engine:
            def get_session_perspective_history(self, sid):
                return ["data_steward"]  # stored before P-1

        resolved, _ = PerspectiveRoutingEngine.resolve_active_perspective(Engine(), None, "s1", None)
        assert resolved == "steward"
        resolved, _ = PerspectiveRoutingEngine.resolve_active_perspective(Engine(), "developer", None, None)
        assert resolved == "app_ai_builder"
        assert pm.role_profile(resolved) == "developer"  # still a tech role for routing


class TestKeys:
    @pytest.mark.parametrize("old, new", sorted(pm.LEGACY_KEYS.items()))
    def test_old_keys_map_once_with_a_log_line(self, from_egeria, logs, old, new):
        assert pm.resolve_key(old) == new
        assert pm.resolve_key(old) == new
        hits = [m for m in logs if f"old perspective key {old!r} resolved to {new!r}" in m]
        assert len(hits) == 1

    def test_unknown_egeria_perspective_is_general_with_a_note(self, logs, monkeypatch):
        monkeypatch.setattr(pm, "_logged_once", set())
        new = json.loads(json.dumps(PERSPECTIVES[0]))
        new["properties"]["displayName"] = "Quality"
        row = pm.rows_from_egeria([new])[0]
        assert row["advisor_key"] == "quality" and row["role_profile"] == "general"
        assert row["tooltip"] == pm.GENERAL_TOOLTIP
        assert any("Egeria perspective 'Quality' is not in perspectives.csv" in m for m in logs)

    def test_a_key_no_perspective_has_is_general_with_a_note(self, from_egeria, logs):
        assert pm.role_profile("no_such_perspective") == "general"
        assert any("no perspective has key 'no_such_perspective'" in m for m in logs)

    def test_no_perspective_is_no_profile(self):
        assert pm.role_profile(None) is None and pm.role_profile("") is None


class TestLabels:
    def test_question_spec_tags_match_through_the_profile(self, from_egeria):
        assert "data steward" in pm.perspective_tags("steward")       # tags are role names
        assert "developer" in pm.perspective_tags("app_ai_builder")
        assert "data steward" in pm.perspective_tags("data_steward")  # old key
        assert pm.perspective_tags("financial") == {"financial"}
        assert pm.perspective_tags(None) == set()

    def test_rag_role_label_is_the_perspective_name(self, from_egeria):
        from advisor.rag_system import RAGSystem
        assert RAGSystem._add_perspective_prefix("q", "app_ai_builder") == "[User role: App/AI Builder]\nq"
        assert RAGSystem._add_perspective_prefix("q", "data_steward") == "[User role: Steward]\nq"


def test_the_families_csv_loads(tmp_path, monkeypatch):
    """It used to return {} (comment rows raised into a bare except), so no family ever applied."""
    from advisor.agents import tools
    tools._load_perspective_families.cache_clear()
    fams = tools._load_perspective_families()
    assert {"any", "developer", "data_engineer", "data_steward", "governance_officer"} <= set(fams)
    assert fams["data_steward"]["glossary"] == 2
