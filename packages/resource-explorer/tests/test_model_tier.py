"""Tests for the model-tier feature (docs/runtime-architecture-plan.md §5):
EXPLORER_MODEL_TIER resolution, num_ctx threaded through every Ollama call
site, and the RAG context token budget applied in build_context().

Mirrors egeria-advisor's tests/unit/test_model_tier.py in shape, adapted to
RE's single Ollama model slot and pydantic-settings-only config (no runtime
YAML loader — see resolve_model_tier()'s docstring in config.py).
"""
from __future__ import annotations

import pytest

from resource_explorer.config import (
    DEFAULT_MODEL_TIER,
    TIER_PRESETS,
    OllamaConfig,
    resolve_llm_tier_config,
    resolve_model_tier,
)
from resource_explorer.prompt_templates import build_context


# ── tier resolution ──────────────────────────────────────────────────────────

class TestResolveModelTier:
    def test_defaults_to_dev_with_no_env_var(self, monkeypatch):
        monkeypatch.delenv("EXPLORER_MODEL_TIER", raising=False)
        assert resolve_model_tier() == "dev"
        assert DEFAULT_MODEL_TIER == "dev"

    @pytest.mark.parametrize("tier", ["dev", "demo-gpu", "demo-cpu"])
    def test_recognised_tier_env_var_wins(self, monkeypatch, tier):
        monkeypatch.setenv("EXPLORER_MODEL_TIER", tier)
        assert resolve_model_tier() == tier

    def test_unknown_tier_falls_back_to_default(self, monkeypatch, caplog):
        monkeypatch.setenv("EXPLORER_MODEL_TIER", "bogus-tier")
        assert resolve_model_tier() == "dev"

    def test_every_preset_has_the_required_keys(self):
        for tier, preset in TIER_PRESETS.items():
            assert set(preset) == {"num_ctx", "rag_context_budget_tokens", "model",
                                   "compile_evidence_budget_chars", "compile_max_sections"}


class TestResolveLLMTierConfig:
    def test_dev_tier_keeps_todays_model_and_no_budget(self, monkeypatch):
        monkeypatch.delenv("EXPLORER_MODEL_TIER", raising=False)
        monkeypatch.delenv("LLM__OLLAMA__MODEL", raising=False)
        cfg = resolve_llm_tier_config()
        assert cfg.tier == "dev"
        assert cfg.model == OllamaConfig.model_fields["model"].default
        assert cfg.num_ctx == 32768
        assert cfg.rag_context_budget_tokens is None

    def test_demo_gpu_tier_sets_model_num_ctx_and_budget(self, monkeypatch):
        monkeypatch.setenv("EXPLORER_MODEL_TIER", "demo-gpu")
        monkeypatch.delenv("LLM__OLLAMA__MODEL", raising=False)
        cfg = resolve_llm_tier_config()
        assert cfg.tier == "demo-gpu"
        assert cfg.model == "llama3.1:8b"
        assert cfg.num_ctx == 8192
        assert cfg.rag_context_budget_tokens == 2000

    def test_demo_cpu_tier_matches_demo_gpu_values(self, monkeypatch):
        monkeypatch.setenv("EXPLORER_MODEL_TIER", "demo-cpu")
        monkeypatch.delenv("LLM__OLLAMA__MODEL", raising=False)
        cfg = resolve_llm_tier_config()
        assert cfg.num_ctx == 8192
        assert cfg.rag_context_budget_tokens == 2000

    def test_explicit_model_override_wins_over_tier_preset(self, monkeypatch):
        """LLM__OLLAMA__MODEL is RE's existing explicit-model env var (see
        config.py's _ENV_FILE_CONFIG / env_nested_delimiter="__") — it must
        still win over whatever model the active tier would otherwise pick,
        matching egeria-advisor's OLLAMA_MODEL/OLLAMA_CODE_MODEL precedence."""
        monkeypatch.setenv("EXPLORER_MODEL_TIER", "demo-gpu")
        monkeypatch.setenv("LLM__OLLAMA__MODEL", "custom-model:latest")
        cfg = resolve_llm_tier_config()
        assert cfg.model == "custom-model:latest"
        # num_ctx/budget still come from the tier — only the model is overridden
        assert cfg.num_ctx == 8192
        assert cfg.rag_context_budget_tokens == 2000

    def test_explicit_model_override_wins_even_on_dev_tier(self, monkeypatch):
        monkeypatch.delenv("EXPLORER_MODEL_TIER", raising=False)
        monkeypatch.setenv("LLM__OLLAMA__MODEL", "custom-model:latest")
        cfg = resolve_llm_tier_config()
        assert cfg.model == "custom-model:latest"
        assert cfg.num_ctx == 32768
        assert cfg.rag_context_budget_tokens is None


class TestGetConfigAppliesTier:
    def test_get_config_applies_resolved_tier_onto_llm_and_rag(self, monkeypatch):
        import resource_explorer.config as config_mod

        monkeypatch.setenv("EXPLORER_MODEL_TIER", "demo-gpu")
        monkeypatch.delenv("LLM__OLLAMA__MODEL", raising=False)
        # get_config()/get_llm_tier_config() are both memoised at module
        # scope — force a fresh resolution so this test doesn't depend on
        # whichever tier an earlier test (or import-time call) resolved.
        monkeypatch.setattr(config_mod, "_config", None)
        monkeypatch.setattr(config_mod, "_llm_tier_config", None)

        cfg = config_mod.get_config()
        assert cfg.llm.ollama.tier == "demo-gpu"
        assert cfg.llm.ollama.model == "llama3.1:8b"
        assert cfg.llm.ollama.num_ctx == 8192
        assert cfg.rag.budget_tokens == 2000


# ── num_ctx on every Ollama call site ────────────────────────────────────────

class _FakeOllamaClient:
    """Stand-in for ollama.Client — records the kwargs each chat() call got."""

    def __init__(self, host=None):
        self.host = host
        self.calls: list[dict] = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return iter([{"message": {"content": "hi"}}])
        return {"message": {"content": "hi"}}


class TestOllamaBackendSendsNumCtx:
    """resource_explorer/llm_client.py's OllamaBackend — the raw `ollama`
    client's two call sites, complete() and stream()."""

    def _make_backend(self, monkeypatch, num_ctx=8192, model="llama3.1:8b"):
        import ollama as ollama_module
        from resource_explorer.config import ExplorerConfig
        from resource_explorer.llm_client import OllamaBackend

        fake_client = _FakeOllamaClient()
        monkeypatch.setattr(ollama_module, "Client", lambda host=None: fake_client)

        cfg = ExplorerConfig()
        cfg.llm.ollama.model = model
        cfg.llm.ollama.num_ctx = num_ctx
        backend = OllamaBackend(cfg)
        return backend, fake_client

    def test_complete_sends_num_ctx(self, monkeypatch):
        backend, fake_client = self._make_backend(monkeypatch, num_ctx=4096)
        backend.complete("hello")
        assert fake_client.calls[0]["options"]["num_ctx"] == 4096

    def test_stream_sends_num_ctx(self, monkeypatch):
        backend, fake_client = self._make_backend(monkeypatch, num_ctx=4096)
        list(backend.stream("hello"))
        assert fake_client.calls[0]["options"]["num_ctx"] == 4096

    def test_explicit_kwarg_num_ctx_wins_over_configured_default(self, monkeypatch):
        backend, fake_client = self._make_backend(monkeypatch, num_ctx=4096)
        backend.complete("hello", num_ctx=99)
        assert fake_client.calls[0]["options"]["num_ctx"] == 99


class TestBeeAIAgentThreadsNumCtx:
    """resource_explorer/agents/base.py's BeeAI RequirementAgent path builds
    a NativeOllamaChatModel (Ollama's /api/chat, via litellm's ollama_chat
    provider): Ollama's OpenAI-compatible /v1 endpoint ignores num_ctx, which
    is why the stock OllamaChatModel is not used. See _build_llm()."""

    def test_build_llm_constructs_ollama_chat_model_with_num_ctx(self, monkeypatch):
        from resource_explorer.agents.stats_agent import StatsAgent
        from resource_explorer.config import ExplorerConfig

        cfg = ExplorerConfig()
        cfg.llm.backend = "ollama"
        cfg.llm.ollama.model = "llama3.1:8b"
        cfg.llm.ollama.num_ctx = 4096

        agent = StatsAgent(cfg)
        llm = agent._build_llm()

        from beeai_framework.adapters.ollama.backend.chat import OllamaChatModel

        assert isinstance(llm, OllamaChatModel)
        assert llm._settings.get("num_ctx") == 4096
        # Native endpoint, not /v1 (which ignores num_ctx), and the cap reaches the call.
        assert llm._litellm_provider_id == "ollama_chat"
        assert not llm._settings["base_url"].endswith("/v1")
        assert llm.parameters.max_tokens == cfg.llm.ollama.max_tokens == 4096

    def test_native_model_puts_num_ctx_and_num_predict_in_the_request(self):
        from beeai_framework.backend import ChatModelParameters, UserMessage

        from resource_explorer.agents.ollama_native import NativeOllamaChatModel
        from beeai_framework.backend.chat import ChatModelInput

        llm = NativeOllamaChatModel(
            "llama3.1:8b",
            base_url="http://localhost:11434",
            settings={"num_ctx": 4096},
            parameters=ChatModelParameters(max_tokens=77),
        )
        sent = llm._transform_input(ChatModelInput(messages=[UserMessage("hi")]))
        assert sent["model"] == "ollama_chat/llama3.1:8b"
        assert sent["num_ctx"] == 4096
        assert sent["max_tokens"] == 77
        assert sent["base_url"] == "http://localhost:11434"

    async def test_native_model_survives_clone(self):
        from resource_explorer.agents.ollama_native import NativeOllamaChatModel

        llm = NativeOllamaChatModel("llama3.1:8b", base_url="http://localhost:11434", settings={"num_ctx": 4096})
        cloned = await llm.clone()
        assert isinstance(cloned, NativeOllamaChatModel)
        assert cloned._litellm_provider_id == "ollama_chat"
        assert cloned._settings["num_ctx"] == 4096

    def test_build_llm_leaves_openai_backend_as_a_bare_string(self, monkeypatch):
        from resource_explorer.agents.stats_agent import StatsAgent
        from resource_explorer.config import ExplorerConfig

        cfg = ExplorerConfig()
        cfg.llm.backend = "openai"
        cfg.llm.openai.model = "gpt-4o-mini"

        agent = StatsAgent(cfg)
        assert agent._build_llm() == "openai:gpt-4o-mini"

    def test_build_llm_leaves_anthropic_backend_as_a_bare_string(self, monkeypatch):
        from resource_explorer.agents.stats_agent import StatsAgent
        from resource_explorer.config import ExplorerConfig

        cfg = ExplorerConfig()
        cfg.llm.backend = "anthropic"
        cfg.llm.anthropic.model = "claude-haiku-4-5-20251001"

        agent = StatsAgent(cfg)
        assert agent._build_llm() == "anthropic:claude-haiku-4-5-20251001"


# ── RAG context budget truncation ────────────────────────────────────────────

class _Chunk:
    def __init__(self, text, score=1.0, collection="c"):
        self.text = text
        self.score = score
        self.collection = collection
        self.id = "id"


class TestBuildContext:
    def test_no_budget_joins_every_result_unchanged(self):
        results = [_Chunk("a" * 100), _Chunk("b" * 100), _Chunk("c" * 100)]
        context = build_context(results, budget_tokens=None)
        assert "a" * 100 in context
        assert "b" * 100 in context
        assert "c" * 100 in context

    def test_budget_keeps_highest_ranked_and_drops_the_rest(self):
        # Each chunk is ~100 chars -> ~25 tokens (4 chars/token estimate).
        # A budget of 30 tokens fits exactly one chunk.
        results = [_Chunk("a" * 100, score=0.9), _Chunk("b" * 100, score=0.5)]
        context = build_context(results, budget_tokens=30)
        assert "a" * 100 in context
        assert "b" * 100 not in context

    def test_budget_large_enough_for_all_keeps_all(self):
        results = [_Chunk("a" * 40), _Chunk("b" * 40)]
        context = build_context(results, budget_tokens=1000)
        assert "a" * 40 in context
        assert "b" * 40 in context

    def test_custom_formatter_is_applied(self):
        results = [_Chunk("body", score=0.42, collection="pyegeria")]
        context = build_context(
            results, budget_tokens=None,
            formatter=lambda r: f"[{r.collection} | score={r.score:.2f}]\n{r.text}",
        )
        assert context == "[pyegeria | score=0.42]\nbody"

    def test_empty_results_returns_empty_string(self):
        assert build_context([], budget_tokens=2000) == ""
