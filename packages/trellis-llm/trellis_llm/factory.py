from __future__ import annotations

from typing import Optional

from trellis_llm.config import LLMSettings, check_egress
from trellis_llm.types import ChatModel


def make_chat_model(settings: LLMSettings, slot: str = "default", *, transport=None) -> ChatModel:
    """The ChatModel for one slot of `settings`. Refuses a remote destination without ALLOW_REMOTE (again, so a settings
    object built by hand is held to the same rule), and says which extra to install when a provider's client is missing.
    `transport` (an httpx transport) is for tests and replaces the network."""
    if settings.provider not in ("ollama", "openai_compatible", "anthropic"):
        raise ValueError(f"unknown provider {settings.provider!r}: choose ollama, openai_compatible or anthropic")
    check_egress(settings)
    model = settings.model_for(slot)
    if settings.provider == "ollama":
        from trellis_llm.providers.ollama import OllamaChat
        return OllamaChat(settings, model, transport)
    if settings.provider == "openai_compatible":
        from trellis_llm.providers.openai_compatible import OpenAICompatibleChat
        return OpenAICompatibleChat(settings, model, transport)
    if settings.provider == "anthropic":
        from trellis_llm.providers.anthropic import AnthropicChat
        return AnthropicChat(settings, model, transport)
    raise ValueError(f"unknown provider {settings.provider!r}: choose ollama, openai_compatible or anthropic")
