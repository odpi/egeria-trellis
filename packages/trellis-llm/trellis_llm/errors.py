"""The errors trellis_llm raises. Every message is in words, and none ever carries an API key."""
from __future__ import annotations


class TrellisLLMError(Exception):
    """Base class."""


class ConfigError(TrellisLLMError):
    """The LLM settings are wrong or incomplete: raised at startup, naming the setting, never silently defaulted."""


class RemoteNotAllowed(ConfigError):
    """A base URL (or the anthropic provider) would send prompts off this network without <APP>_LLM_ALLOW_REMOTE=true."""


class MissingExtra(TrellisLLMError):
    """The chosen provider's client library is not installed; names the extra to install."""

    def __init__(self, provider: str, extra: str):
        self.provider, self.extra = provider, extra
        super().__init__(f"provider {provider!r} needs the {extra!r} extra: pip install 'trellis-llm[{extra}]' "
                         f"(or: uv sync --extra {extra})")


class LLMError(TrellisLLMError):
    """A call to a provider failed. `status` is the HTTP status when there was one; the message is the provider's own words."""

    def __init__(self, provider: str, message: str, status: int | None = None):
        self.provider, self.status = provider, status
        super().__init__(f"{provider}: {message}" + (f" (HTTP {status})" if status is not None else ""))
