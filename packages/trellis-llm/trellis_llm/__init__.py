"""trellis_llm: one way for Trellis apps to talk to a chat model (TR-38).

    from trellis_llm import resolve, make_chat_model, log_startup
    settings = resolve("RE", slots=("default",))         # RE_LLM_* > TRELLIS_LLM_* > legacy names > defaults
    log_startup(settings)                                # provider, host, models: never the API key
    model = make_chat_model(settings)
    answer = model.complete("...", temperature=0.1)      # -> Completion(text, model, provider, usage, applied, ignored)

Importing this module imports no provider library: each provider is an optional extra (ollama, openai, anthropic).
"""
from trellis_llm.config import (LLMSettings, TIER_PRESETS, check_egress, is_local_host, log_startup, resolve,
                                startup_line)
from trellis_llm.errors import (ConfigError, LLMError, MissingExtra, RemoteNotAllowed, TrellisLLMError)
from trellis_llm.factory import make_chat_model
from trellis_llm.secret import Secret
from trellis_llm.types import ChatModel, Completion, Stream, Usage

__all__ = ["ChatModel", "Completion", "ConfigError", "LLMError", "LLMSettings", "MissingExtra", "RemoteNotAllowed", "Secret",
           "Stream", "TIER_PRESETS", "TrellisLLMError", "Usage", "check_egress", "is_local_host", "log_startup",
           "make_chat_model", "resolve", "startup_line"]
