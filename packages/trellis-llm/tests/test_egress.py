"""Data egress: a base URL that is not loopback or a private address, or the anthropic provider, needs
<APP>_LLM_ALLOW_REMOTE=true. One startup line says where prompts go."""
import logging

import pytest

from trellis_llm import ConfigError, RemoteNotAllowed, resolve
from trellis_llm.config import startup_line, log_startup


def go(url, env_extra=None, provider="openai_compatible", resolver=None):
    env = {"RE_LLM_PROVIDER": provider, "RE_LLM_BASE_URL": url, "RE_LLM_MODEL": "m", **(env_extra or {})}
    return resolve("RE", env, resolver=resolver or (lambda host: []))


@pytest.mark.parametrize("url", [
    "http://localhost:1234/v1", "http://127.0.0.1:1234/v1", "http://[::1]:1234/v1",
    "http://10.1.2.3:8000/v1", "http://172.16.0.5/v1", "http://172.31.255.254/v1", "http://192.168.1.20:1234/v1",
])
def test_loopback_and_private_addresses_are_allowed(url):
    assert go(url).allow_remote is False


def test_a_lan_hostname_that_resolves_to_a_private_address_is_allowed():
    assert go("http://mini.lan:1234/v1", resolver=lambda host: ["192.168.1.7"])
    assert go("http://workstation.local:1234/v1", resolver=lambda host: ["10.0.0.9"])


@pytest.mark.parametrize("url", ["https://api.openai.com/v1", "http://8.8.8.8/v1", "http://172.32.0.1/v1",
                                 "http://11.0.0.1/v1", "http://192.169.0.1/v1"])
def test_a_public_address_is_refused_without_allow_remote_in_words(url):
    with pytest.raises(RemoteNotAllowed) as info:
        go(url)
    text = str(info.value)
    assert "RE_LLM_ALLOW_REMOTE=true" in text and "prompts" in text


def test_a_hostname_that_resolves_public_or_not_at_all_is_refused():
    with pytest.raises(RemoteNotAllowed):
        go("http://example.com/v1", resolver=lambda host: ["93.184.216.34"])
    with pytest.raises(RemoteNotAllowed):
        go("http://nowhere.invalid/v1", resolver=lambda host: [])      # cannot tell: fail closed


def test_a_hostname_with_one_private_and_one_public_address_is_refused():
    with pytest.raises(RemoteNotAllowed):
        go("http://split.example/v1", resolver=lambda host: ["10.0.0.1", "93.184.216.34"])


def test_allow_remote_lets_it_through_and_the_startup_line_says_where_prompts_go():
    s = go("https://api.openai.com/v1", {"RE_LLM_ALLOW_REMOTE": "true"})
    assert s.allow_remote is True
    assert "prompts go to api.openai.com" in startup_line(s)


def test_the_app_prefix_decides_which_allow_remote_counts():
    with pytest.raises(RemoteNotAllowed):
        go("https://api.openai.com/v1", {"EA_LLM_ALLOW_REMOTE": "true"})
    assert go("https://api.openai.com/v1", {"TRELLIS_LLM_ALLOW_REMOTE": "true"}).allow_remote is True


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes"])
def test_allow_remote_accepts_the_usual_truthy_words(value):
    assert go("https://api.openai.com/v1", {"RE_LLM_ALLOW_REMOTE": value}).allow_remote is True


def test_a_non_truthy_allow_remote_does_not_count():
    with pytest.raises(RemoteNotAllowed):
        go("https://api.openai.com/v1", {"RE_LLM_ALLOW_REMOTE": "maybe"})


def test_anthropic_always_needs_allow_remote():
    with pytest.raises(RemoteNotAllowed) as info:
        resolve("RE", {"RE_LLM_PROVIDER": "anthropic", "RE_LLM_MODEL": "claude-haiku-4-5-20251001"})
    assert "anthropic" in str(info.value).lower() and "RE_LLM_ALLOW_REMOTE=true" in str(info.value)
    s = resolve("RE", {"RE_LLM_PROVIDER": "anthropic", "RE_LLM_MODEL": "claude-haiku-4-5-20251001",
                       "RE_LLM_ALLOW_REMOTE": "true"})
    assert "prompts go to api.anthropic.com" in startup_line(s)


def test_a_malformed_base_url_is_a_startup_error():
    with pytest.raises(ConfigError, match="RE_LLM_BASE_URL"):
        go("not a url")


def test_the_startup_line_names_provider_host_and_every_slot_model_and_is_logged_once(caplog):
    s = resolve("EA", {"EA_LLM_PROVIDER": "openai_compatible", "EA_LLM_BASE_URL": "http://localhost:1234/v1",
                       "EA_LLM_MODEL": "qwen3-coder-next", "EA_LLM_MODEL_CODE": "laguna-xs-2.1"},
                slots=("query", "code"), resolver=lambda h: [])
    line = startup_line(s)
    assert "openai_compatible" in line and "localhost:1234" in line
    assert "query=qwen3-coder-next" in line and "code=laguna-xs-2.1" in line
    assert "prompts go to localhost:1234" in line
    with caplog.at_level(logging.INFO, logger="trellis_llm"):
        log_startup(s)
    assert [x.getMessage() for x in caplog.records] == [line]
