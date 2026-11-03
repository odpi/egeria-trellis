"""The compile's evidence budget and section cap are tier settings with env overrides."""
from __future__ import annotations

import pytest

from resource_explorer import config
from resource_explorer.config import TIER_PRESETS, resolve_llm_tier_config

_ENVS = ("EXPLORER_MODEL_TIER", "EXPLORER_COMPILE_EVIDENCE_BUDGET_CHARS", "EXPLORER_COMPILE_MAX_SECTIONS")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in _ENVS:
        monkeypatch.delenv(k, raising=False)


@pytest.mark.parametrize("tier", sorted(TIER_PRESETS))
def test_every_tier_defaults_to_todays_values(tier, monkeypatch):
    monkeypatch.setenv("EXPLORER_MODEL_TIER", tier)
    cfg = resolve_llm_tier_config()
    assert (cfg.compile_evidence_budget_chars, cfg.compile_max_sections) == (6000, 12)


def test_env_overrides_the_tier_preset(monkeypatch):
    monkeypatch.setenv("EXPLORER_MODEL_TIER", "demo-gpu")
    monkeypatch.setenv("EXPLORER_COMPILE_EVIDENCE_BUDGET_CHARS", "4000")
    monkeypatch.setenv("EXPLORER_COMPILE_MAX_SECTIONS", "0")
    cfg = resolve_llm_tier_config()
    assert (cfg.compile_evidence_budget_chars, cfg.compile_max_sections) == (4000, 0)


def test_the_preset_applies_when_the_env_is_unset(monkeypatch):
    monkeypatch.setenv("EXPLORER_MODEL_TIER", "demo-cpu")
    monkeypatch.setitem(TIER_PRESETS["demo-cpu"], "compile_max_sections", 9)
    assert resolve_llm_tier_config().compile_max_sections == 9


@pytest.mark.parametrize("bad", ["lots", "-1", "1.5"])
def test_an_unusable_override_falls_back_to_the_preset(bad, monkeypatch):
    monkeypatch.setenv("EXPLORER_COMPILE_MAX_SECTIONS", bad)
    assert resolve_llm_tier_config().compile_max_sections == 12


def test_a_zero_budget_is_refused(monkeypatch):
    monkeypatch.setenv("EXPLORER_COMPILE_EVIDENCE_BUDGET_CHARS", "0")
    assert resolve_llm_tier_config().compile_evidence_budget_chars == 6000


def test_the_resolved_values_are_logged_with_the_tier_line(monkeypatch, caplog):
    monkeypatch.setenv("EXPLORER_COMPILE_MAX_SECTIONS", "7")
    with caplog.at_level("INFO", logger=config.log.name):
        config.get_llm_tier_config(force_refresh=True)
    line = next(r.getMessage() for r in caplog.records if "LLM tier resolved" in r.getMessage())
    assert "compile_evidence_budget_chars=6000" in line and "compile_max_sections=7" in line
    monkeypatch.delenv("EXPLORER_COMPILE_MAX_SECTIONS")
    config.get_llm_tier_config(force_refresh=True)


def test_the_agents_read_the_budget_from_the_tier(monkeypatch):
    import inspect
    from resource_explorer.agents import base, conversation_agent
    assert "budget=6000" not in inspect.getsource(conversation_agent)
    assert "budget: int = 6000" not in inspect.getsource(base)


def test_the_start_up_line_is_logged_even_when_already_resolved(caplog):
    # The CLI's get_config() resolves the tier before logging is configured, so
    # the lazy first-call line is lost; start-up must log the cached config again.
    config.get_llm_tier_config()
    with caplog.at_level("INFO", logger=config.log.name):
        config.log_llm_tier_config()
    assert any("LLM tier resolved" in r.getMessage() for r in caplog.records)


def _compile_with(monkeypatch, **body):
    import asyncio
    from types import SimpleNamespace
    from resource_explorer import context_compile, registry
    from resource_explorer.surveyors import survey_definition_executor
    from resource_explorer.web.routes import compile_context as route

    seen = {}

    def fake_compile(_reg, _slug, _q, **kw):
        seen.update(kw)
        return SimpleNamespace(text="", manifest={}, derivation=[], compile_id="c")

    monkeypatch.setattr(context_compile, "compile_context", fake_compile)
    monkeypatch.setattr(registry, "ProjectRegistry", lambda: object())
    monkeypatch.setattr(survey_definition_executor, "get_adapter",
                        lambda _t: SimpleNamespace(get_entity=lambda _r, _s: object()))
    asyncio.run(route.compile_endpoint(route.CompileRequest(resource_slug="r", question="q", **body)))
    return seen


def test_a_preview_without_a_budget_compiles_at_the_tier_budget(monkeypatch):
    # Same budget the Ask path uses, so the preview names the evidence the answer gets.
    monkeypatch.setenv("EXPLORER_COMPILE_EVIDENCE_BUDGET_CHARS", "4321")
    config.get_llm_tier_config(force_refresh=True)
    try:
        assert _compile_with(monkeypatch)["budget"] == 4321
        assert _compile_with(monkeypatch, budget=900)["budget"] == 900
    finally:
        monkeypatch.delenv("EXPLORER_COMPILE_EVIDENCE_BUDGET_CHARS")
        config.get_llm_tier_config(force_refresh=True)


def test_the_frontend_preview_does_not_pin_its_own_budget():
    from pathlib import Path
    import resource_explorer
    js = (Path(resource_explorer.__file__).parent / "web/static/re-api.js").read_text()
    assert "budget = 8000" not in js
