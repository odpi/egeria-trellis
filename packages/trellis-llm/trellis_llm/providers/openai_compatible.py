"""Any OpenAI-compatible server (LM Studio, vLLM, llama.cpp ...) through the official openai client, with an explicit
base_url. Parameters that are not part of /v1 are named in `ignored`, not sent."""
from __future__ import annotations

from typing import Any

from trellis_llm.errors import LLMError
from trellis_llm.params import plan
from trellis_llm.providers._common import api_error_message, field, import_client, messages
from trellis_llm.types import Completion, Stream, Usage

PROVIDER = "openai_compatible"
WIRE = {"temperature": "temperature", "max_tokens": "max_tokens", "top_p": "top_p", "seed": "seed", "stop": "stop"}
REASONS = {
    "num_ctx": "not part of the OpenAI /v1 API: set the context length where the model is loaded",
    "top_k": "not supported by the OpenAI API",
    "repeat_penalty": "not supported by the OpenAI API",
}
#: the client requires a key even when the server does not check one
PLACEHOLDER_KEY = "not-needed"


class OpenAICompatibleChat:
    provider = PROVIDER

    def __init__(self, settings, model: str, transport=None):
        openai = import_client(PROVIDER, "openai", "openai")
        self._openai = openai
        self.model = model
        self._key = settings.api_key
        self._base_url = settings.base_url
        http = {}
        if transport is not None:
            import httpx
            http = {"http_client": httpx.Client(transport=transport)}
        self._client = openai.OpenAI(base_url=settings.base_url, api_key=self._key.reveal() if self._key else PLACEHOLDER_KEY,
                                     max_retries=0, **http)

    def _create(self, prompt, system, params, stream):
        applied, sent, ignored = plan(PROVIDER, params, WIRE, REASONS)
        extra = {"stream": True, "stream_options": {"include_usage": True}} if stream else {}
        try:
            return applied, ignored, self._client.chat.completions.create(
                model=self.model, messages=messages(prompt, system), **sent, **extra)
        except self._openai.APIStatusError as exc:
            raise LLMError(PROVIDER, api_error_message(getattr(exc, "body", None), str(exc.message), self._key),
                           exc.status_code) from None     # not chained: the SDK exception holds the raw body
        except self._openai.APIConnectionError as exc:
            raise LLMError(PROVIDER, f"cannot connect to {self._base_url}: {exc}") from exc

    @staticmethod
    def _usage(obj) -> Usage:
        return Usage(field(obj, "prompt_tokens"), field(obj, "completion_tokens"))

    def complete(self, prompt: str, *, system: str = "", **params: Any) -> Completion:
        applied, ignored, resp = self._create(prompt, system, params, False)
        choice = resp.choices[0] if resp.choices else None
        return Completion(text=(field(field(choice, "message"), "content") or ""), model=self.model, provider=PROVIDER,
                          usage=self._usage(field(resp, "usage")), applied=applied, ignored=ignored,
                          finish_reason=field(choice, "finish_reason"))

    def stream(self, prompt: str, *, system: str = "", **params: Any) -> Stream:
        applied, ignored, chunks = self._create(prompt, system, params, True)
        seen: dict[str, Any] = {"usage": Usage(), "finish": None}

        def text():
            try:
                for chunk in chunks:
                    if field(chunk, "usage") is not None:           # the last chunk, with no choices, carries it
                        seen["usage"] = self._usage(field(chunk, "usage"))
                    for choice in chunk.choices or []:
                        if field(choice, "finish_reason"):
                            seen["finish"] = field(choice, "finish_reason")
                        piece = field(field(choice, "delta"), "content")
                        if piece:
                            yield piece
            except self._openai.APIError as exc:
                raise LLMError(PROVIDER, api_error_message(getattr(exc, "body", None), str(exc), self._key)) from None

        return Stream(text(), lambda full: Completion(text=full, model=self.model, provider=PROVIDER, usage=seen["usage"],
                                                      applied=applied, ignored=ignored, finish_reason=seen["finish"]))
