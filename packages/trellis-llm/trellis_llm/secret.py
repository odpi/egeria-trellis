"""A value that must never be printed: repr, str and f-strings all give '***'; only `reveal()` returns it."""
from __future__ import annotations


class Secret:
    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret(***)"

    __str__ = __repr__

    def __format__(self, spec: str) -> str:
        return "***"

    def __bool__(self) -> bool:
        return bool(self._value)

    def __eq__(self, other) -> bool:
        return isinstance(other, Secret) and other._value == self._value

    def __hash__(self) -> int:
        return hash(("Secret", self._value))

    def scrub(self, text: str) -> str:
        """`text` with every occurrence of the value replaced."""
        return text.replace(self._value, "***") if self._value else text


def scrub(text: str, secret: "Secret | None") -> str:
    return secret.scrub(text) if secret else text
