"""The interface: ChatModel.complete() and .stream(), and what they return."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class Usage:
    """Token counts as the provider reported them. None means the provider did not say: never 0 for "unknown"."""
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None


@dataclass
class Completion:
    text: str
    model: str
    provider: str
    usage: Usage = field(default_factory=Usage)
    #: {parameter: value} that reached the provider (including settings defaults such as a tier's num_ctx)
    applied: dict[str, Any] = field(default_factory=dict)
    #: {parameter: reason} that the provider dropped. Never silently lost: also logged once per (provider, parameter).
    ignored: dict[str, str] = field(default_factory=dict)
    #: why generation stopped, as the provider said it ("stop", "length", "end_turn" ...); None when it did not say. A
    #: reasoning model cut by `length` returns text == "": this is how a caller tells that from an empty answer.
    finish_reason: Optional[str] = None


class Stream:
    """Text chunks as they arrive. After the last chunk, `.completion` holds the whole Completion (text, usage, ...)."""

    def __init__(self, chunks: Iterator[str], finish):
        self._chunks, self._finish = chunks, finish
        self.completion: Optional[Completion] = None
        self._parts: list[str] = []

    def __iter__(self) -> Iterator[str]:
        try:
            for chunk in self._chunks:
                self._parts.append(chunk)
                yield chunk
        finally:
            # try/finally: a consumer that stops early closes this generator at the yield; the usage seen so far is
            # still recorded (None where the provider had not said)
            self.completion = self._finish("".join(self._parts))


@runtime_checkable
class ChatModel(Protocol):
    provider: str
    model: str

    def complete(self, prompt: str, *, system: str = "", **params: Any) -> Completion: ...

    def stream(self, prompt: str, *, system: str = "", **params: Any) -> Stream: ...
