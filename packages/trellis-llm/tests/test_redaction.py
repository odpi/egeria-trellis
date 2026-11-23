"""With a sentinel key, it never appears in the startup line, any log record, repr(config) or an exception message."""
import logging

import pytest

from conftest import Replay, json_reply
from trellis_llm import ConfigError, LLMError, make_chat_model, resolve
from trellis_llm.config import log_startup, startup_line
from trellis_llm.secret import Secret

KEY = "sk-SENTINEL-KEY-9f8e7d6c5b4a"


def settings(**extra):
    env = {"RE_LLM_PROVIDER": "openai_compatible", "RE_LLM_BASE_URL": "http://localhost:1234/v1", "RE_LLM_MODEL": "m",
           "RE_LLM_API_KEY": KEY, **extra}
    return resolve("RE", env, resolver=lambda h: [])


def test_the_key_is_held_as_a_secret_and_never_in_repr_str_or_format():
    s = settings()
    assert isinstance(s.api_key, Secret) and s.api_key.reveal() == KEY
    for text in (repr(s), str(s), repr(s.api_key), str(s.api_key), f"{s.api_key}", f"{s!r}", "%s" % (s,)):
        assert KEY not in text
    assert "***" in repr(s.api_key)


def test_the_startup_line_and_every_log_record_are_free_of_the_key(caplog):
    s = settings()
    with caplog.at_level(logging.DEBUG, logger="trellis_llm"):
        log_startup(s)
        m = make_chat_model(s, transport=Replay(json_reply("lmstudio_chat_complete.json")).transport)
        m.complete("hi", top_k=5)                                 # logs an ignored-parameter warning
    assert KEY not in startup_line(s)
    assert caplog.records and all(KEY not in x.getMessage() and KEY not in str(x.__dict__) for x in caplog.records)


def test_config_errors_never_carry_the_key():
    with pytest.raises(ConfigError) as info:
        resolve("RE", {"RE_LLM_PROVIDER": "openai_compatible", "RE_LLM_API_KEY": KEY})      # no base URL, no model
    assert KEY not in str(info.value) and KEY not in repr(info.value)
    with pytest.raises(ConfigError) as info:
        resolve("RE", {"RE_LLM_PROVIDER": "bedrock", "RE_LLM_API_KEY": KEY})
    assert KEY not in str(info.value)


def test_provider_errors_never_carry_the_key_even_when_the_server_echoes_it():
    echo = {"error": {"message": f"bad key {KEY}", "type": "invalid_request_error"}}
    m = make_chat_model(settings(), transport=Replay(json_reply(echo, 401)).transport)
    with pytest.raises(LLMError) as info:
        m.complete("hi")
    assert KEY not in str(info.value) and KEY not in repr(info.value) and KEY not in repr(info.value.__cause__ or "")


def test_a_key_given_under_the_trellis_or_legacy_names_is_redacted_too():
    for env in ({"TRELLIS_LLM_API_KEY": KEY}, {"RE_LLM_API_KEY": KEY}):
        s = resolve("RE", {"RE_LLM_PROVIDER": "openai_compatible", "RE_LLM_BASE_URL": "http://localhost:1/v1",
                           "RE_LLM_MODEL": "m", **env}, resolver=lambda h: [])
        assert KEY not in repr(s) and s.api_key.reveal() == KEY
