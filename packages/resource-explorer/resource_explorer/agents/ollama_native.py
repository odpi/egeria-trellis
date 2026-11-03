"""BeeAI chat model that talks to Ollama's native ``/api/chat`` endpoint.

BeeAI's stock ``OllamaChatModel`` goes through litellm's ``openai/`` provider
and Ollama's OpenAI-compatible ``/v1`` endpoint. That endpoint honours
``max_tokens`` but silently ignores ``num_ctx`` in the request body, so the
tier's context ceiling never took effect: measured 2026-10-20 with
``num_ctx=2048``, ``/api/ps`` reported ``llama3.1:8b`` loaded at 32768 (131072
on the A/B runner). litellm's ``ollama_chat`` provider posts to ``/api/chat``
and moves ``num_ctx`` and ``max_tokens`` (as ``num_predict``) into ``options``.
"""
# No `from __future__ import annotations` here: BeeAI's clone() filters constructor
# kwargs by resolving the real `Unpack[ChatModelKwargs]` annotation, and string
# annotations make it drop `settings`, so a clone silently loses num_ctx.
from beeai_framework.adapters.ollama.backend.chat import OllamaChatModel
from beeai_framework.backend.chat import ChatModelKwargs
from litellm import get_supported_openai_params
from typing_extensions import Unpack

_PROVIDER = "ollama_chat"


class NativeOllamaChatModel(OllamaChatModel):
    def __init__(self, model_id: str | None = None, **kwargs: Unpack[ChatModelKwargs]) -> None:
        # clone() passes provider_id back in; this class always owns it.
        kwargs.pop("provider_id", None)  # type: ignore[typeddict-item]
        super().__init__(model_id, **kwargs)
        self._litellm_provider_id = _PROVIDER
        self.supported_params = get_supported_openai_params(model=self.model_id, custom_llm_provider=_PROVIDER) or []
        # The parent appends /v1; the native endpoint lives at the root.
        base_url = self._settings["base_url"]
        self._settings["base_url"] = base_url.removesuffix("/v1")
