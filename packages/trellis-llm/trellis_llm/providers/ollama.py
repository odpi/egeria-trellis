"""Ollama's native /api/chat, with the context window (num_ctx) always sent (RE's rule: a model is never loaded at its
full default window)."""
from __future__ import annotations

from typing import Any, Optional

from trellis_llm.errors import LLMError
from trellis_llm.params import plan
from trellis_llm.providers._common import field, import_client, messages
from trellis_llm.types import Completion, Stream, Usage

PROVIDER = "ollama"
WIRE = {"temperature": "temperature", "max_tokens": "num_predict", "top_p": "top_p", "top_k": "top_k",
        "repeat_penalty": "repeat_penalty", "num_ctx": "num_ctx", "seed": "seed", "stop": "stop"}


class OllamaChat:
    provider = PROVIDER

    def __init__(self, settings, model: str, transport=None):
        ollama = import_client(PROVIDER, "ollama", "ollama")
        self._ollama = ollama
        self.model = model
        self._base_url = settings.base_url
        self._defaults = {"num_ctx": settings.num_ctx}
        kwargs = {"transport": transport} if transport is not None else {}
        self._client = ollama.Client(host=settings.base_url, **kwargs)

    def _call(self, prompt, system, params, stream):
        applied, options, ignored = plan(PROVIDER, {**self._defaults, **params}, WIRE, {})
        try:
            result = self._client.chat(model=self.model, messages=messages(prompt, system), options=options,
                                       stream=stream)
        except self._ollama.ResponseError as exc:
            raise LLMError(PROVIDER, " ".join(str(exc.error).split()), getattr(exc, "status_code", None)) from None
        except (ConnectionError, OSError) as exc:
            raise LLMError(PROVIDER, f"cannot connect to {self._base_url}: {exc}") from exc
        return applied, ignored, result

    @staticmethod
    def _usage(obj) -> Usage:
        return Usage(field(obj, "prompt_eval_count"), field(obj, "eval_count"))

    def complete(self, prompt: str, *, system: str = "", **params: Any) -> Completion:
        applied, ignored, resp = self._call(prompt, system, params, False)
        return Completion(text=field(field(resp, "message"), "content") or "", model=self.model, provider=PROVIDER,
                          usage=self._usage(resp), applied=applied, ignored=ignored,
                          finish_reason=field(resp, "done_reason"))

    def stream(self, prompt: str, *, system: str = "", **params: Any) -> Stream:
        applied, ignored, chunks = self._call(prompt, system, params, True)
        seen: dict[str, Any] = {"usage": Usage(), "finish": None}

        def text():
            try:
                for chunk in chunks:
                    if field(chunk, "prompt_eval_count") is not None or field(chunk, "eval_count") is not None:
                        seen["usage"] = self._usage(chunk)           # Ollama puts the counts on the final `done` chunk
                    if field(chunk, "done_reason"):
                        seen["finish"] = field(chunk, "done_reason")
                    piece = field(field(chunk, "message"), "content")
                    if piece:
                        yield piece
            except (ConnectionError, OSError) as exc:
                raise LLMError(PROVIDER, f"the stream from {self._base_url} broke: {exc}") from exc

        return Stream(text(), lambda full: Completion(text=full, model=self.model, provider=PROVIDER, usage=seen["usage"],
                                                      applied=applied, ignored=ignored, finish_reason=seen["finish"]))
