"""Anthropic's Messages API, behaving as RE's current client does: a default system prompt, max_tokens 4096."""
from __future__ import annotations

from typing import Any

from trellis_llm.errors import LLMError
from trellis_llm.params import plan
from trellis_llm.providers._common import api_error_message, field, import_client
from trellis_llm.types import Completion, Stream, Usage

PROVIDER = "anthropic"
WIRE = {"temperature": "temperature", "max_tokens": "max_tokens", "top_p": "top_p", "top_k": "top_k",
        "stop": "stop_sequences"}
REASONS = {
    "num_ctx": "not an Anthropic parameter: the context window is fixed by the model",
    "repeat_penalty": "not supported by the Anthropic API",
    "seed": "not supported by the Anthropic API",
}
DEFAULT_SYSTEM = "You are a helpful assistant."
DEFAULT_MAX_TOKENS = 4096


class AnthropicChat:
    provider = PROVIDER

    def __init__(self, settings, model: str, transport=None):
        anthropic = import_client(PROVIDER, "anthropic", "anthropic")
        self._anthropic = anthropic
        self.model = model
        self._key = settings.api_key
        http = {}
        if transport is not None:
            import httpx
            http = {"http_client": httpx.Client(transport=transport)}
        base = {"base_url": settings.base_url} if settings.base_url else {}
        self._client = anthropic.Anthropic(api_key=self._key.reveal() if self._key else None, max_retries=0, **base, **http)

    def _args(self, prompt, system, params):
        applied, sent, ignored = plan(PROVIDER, {"max_tokens": DEFAULT_MAX_TOKENS, **params}, WIRE, REASONS)
        return applied, ignored, dict(model=self.model, system=system or DEFAULT_SYSTEM,
                                      messages=[{"role": "user", "content": prompt}], **sent)

    def _error(self, exc) -> LLMError:
        if isinstance(exc, self._anthropic.APIStatusError):
            return LLMError(PROVIDER, api_error_message(getattr(exc, "body", None), str(exc.message), self._key),
                            exc.status_code)
        return LLMError(PROVIDER, f"cannot reach the Anthropic API: {exc}")

    @staticmethod
    def _usage(obj) -> Usage:
        return Usage(field(obj, "input_tokens"), field(obj, "output_tokens"))

    def complete(self, prompt: str, *, system: str = "", **params: Any) -> Completion:
        applied, ignored, args = self._args(prompt, system, params)
        try:
            resp = self._client.messages.create(**args)
        except (self._anthropic.APIStatusError, self._anthropic.APIConnectionError) as exc:
            raise self._error(exc) from None
        text = "".join(field(b, "text") or "" for b in (resp.content or []) if field(b, "type") == "text")
        return Completion(text=text, model=self.model, provider=PROVIDER, usage=self._usage(field(resp, "usage")),
                          applied=applied, ignored=ignored, finish_reason=field(resp, "stop_reason"))

    def stream(self, prompt: str, *, system: str = "", **params: Any) -> Stream:
        applied, ignored, args = self._args(prompt, system, params)
        seen: dict[str, Any] = {"usage": Usage(), "finish": None}

        def text():
            try:
                with self._client.messages.stream(**args) as stream:
                    yield from stream.text_stream
                    final = stream.get_final_message()            # the usage events text_stream does not surface
                    seen["usage"] = self._usage(field(final, "usage"))
                    seen["finish"] = field(final, "stop_reason")
            except (self._anthropic.APIStatusError, self._anthropic.APIConnectionError) as exc:
                raise self._error(exc) from None

        return Stream(text(), lambda full: Completion(text=full, model=self.model, provider=PROVIDER, usage=seen["usage"],
                                                      applied=applied, ignored=ignored, finish_reason=seen["finish"]))
