"""The canonical generation parameters, and the one place a provider's drops are named and logged."""
from __future__ import annotations

import logging
from typing import Any, Mapping

log = logging.getLogger("trellis_llm")

#: What a caller may pass. Each provider maps these to its own names or says it ignores one.
CANONICAL = ("temperature", "max_tokens", "top_p", "top_k", "repeat_penalty", "num_ctx", "seed", "stop")

_LOGGED: set[tuple[str, str]] = set()


def plan(provider: str, params: Mapping[str, Any], wire: Mapping[str, str], reasons: Mapping[str, str]):
    """(applied canonical {name: value}, wire kwargs {wire name: value}, ignored {name: reason}).

    `wire` maps a canonical name to the provider's name; `reasons` says why a canonical name has no wire name. A name that
    is neither raises ValueError (a typo is the caller's bug, not something to drop). None values are not parameters."""
    applied: dict[str, Any] = {}
    sent: dict[str, Any] = {}
    ignored: dict[str, str] = {}
    for name, value in params.items():
        if name not in CANONICAL:
            raise ValueError(f"unknown parameter {name!r}; trellis_llm knows {', '.join(CANONICAL)}")
        if value is None:
            continue
        if name in wire:
            applied[name], sent[wire[name]] = value, value
        else:
            ignored[name] = reasons.get(name, f"{provider} does not use {name}")
            if (provider, name) not in _LOGGED:
                _LOGGED.add((provider, name))
                log.warning("%s ignores %s: %s", provider, name, ignored[name])
    return applied, sent, ignored
