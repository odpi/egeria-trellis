"""resolve(): precedence <APP>_LLM_* > TRELLIS_LLM_* > the app's legacy names (one warning each) > defaults; slots;
tiers only for ollama; a missing model for a non-ollama provider is a startup error in words."""
import logging

import pytest

from trellis_llm import ConfigError, resolve
from trellis_llm import config as cfg


@pytest.fixture(autouse=True)
def _fresh_warnings():
    cfg._WARNED.clear()
    yield
    cfg._WARNED.clear()


def r(app="RE", env=None, **kw):
    return resolve(app, env or {}, resolver=lambda host: ["127.0.0.1"], **kw)


def test_defaults_are_ollama_on_localhost_with_the_default_model():
    s = r()
    assert (s.provider, s.base_url, s.model_for("default")) == ("ollama", "http://localhost:11434", "llama3.1:8b")
    assert s.tier is None and s.num_ctx is None and s.allow_remote is False


def test_each_precedence_level_beats_the_next():
    legacy = {"LLM__OLLAMA__MODEL": "legacy-model"}
    assert r(env=legacy).model_for("default") == "legacy-model"
    assert r(env={**legacy, "TRELLIS_LLM_MODEL": "shared-model"}).model_for("default") == "shared-model"
    assert r(env={**legacy, "TRELLIS_LLM_MODEL": "shared-model", "RE_LLM_MODEL": "app-model"}).model_for("default") == "app-model"
    # and for another key: the base URL
    assert r(env={"LLM__OLLAMA__BASE_URL": "http://localhost:1"}).base_url == "http://localhost:1"
    assert r(env={"LLM__OLLAMA__BASE_URL": "http://localhost:1", "TRELLIS_LLM_BASE_URL": "http://localhost:2"}).base_url == "http://localhost:2"
    assert r(env={"TRELLIS_LLM_BASE_URL": "http://localhost:2", "RE_LLM_BASE_URL": "http://localhost:3"}).base_url == "http://localhost:3"


def test_the_app_prefix_is_the_apps_own_other_apps_do_not_leak_in():
    assert r("EA", {"RE_LLM_MODEL": "re-model"}).model_for("default") == "llama3.1:8b"
    assert r("EA", {"EA_LLM_MODEL": "ea-model"}).model_for("default") == "ea-model"


def test_legacy_names_warn_once_each(caplog):
    env = {"LLM__OLLAMA__MODEL": "m", "EXPLORER_MODEL_TIER": "demo-gpu"}
    with caplog.at_level(logging.WARNING, logger="trellis_llm"):
        r(env=env)
        r(env=env)
    text = [x.getMessage() for x in caplog.records if "deprecated" in x.getMessage().lower()]
    assert sum("LLM__OLLAMA__MODEL" in t for t in text) == 1 and sum("EXPLORER_MODEL_TIER" in t for t in text) == 1
    assert any("RE_LLM_MODEL" in t for t in text)                      # it names the new name


def test_the_new_names_do_not_warn(caplog):
    with caplog.at_level(logging.WARNING, logger="trellis_llm"):
        r(env={"RE_LLM_MODEL": "m", "TRELLIS_LLM_BASE_URL": "http://localhost:11434"})
    assert not [x for x in caplog.records if "deprecated" in x.getMessage().lower()]


def test_ea_legacy_names_and_yaml_are_read_as_data():
    env = {"OLLAMA_BASE_URL": "http://localhost:9", "OLLAMA_MODEL": "q-model", "OLLAMA_CODE_MODEL": "c-model",
           "ADVISOR_MODEL_TIER": "dev"}
    s = r("EA", env, slots=("query", "conversation", "planning", "code", "maintenance"))
    assert s.base_url == "http://localhost:9"
    assert [s.model_for(x) for x in ("query", "conversation", "code", "maintenance")] == ["q-model", "q-model", "c-model", "c-model"]
    yaml = {"llm": {"provider": "ollama", "base_url": "http://localhost:8", "tier": "demo-cpu",
                    "models": {"planning": "qwen2.5-coder:32b"}}}
    s2 = r("EA", {}, slots=("query", "planning"), legacy_files={"advisor.yaml": yaml})
    assert s2.base_url == "http://localhost:8" and s2.tier == "demo-cpu"
    assert s2.model_for("planning") == "qwen2.5-coder:32b"            # an explicit slot beats the tier's preset
    assert s2.model_for("query") == "llama3.1:8b"                      # the tier's preset
    assert s2.num_ctx == 8192
    # the env legacy name beats the yaml
    assert r("EA", {"OLLAMA_BASE_URL": "http://localhost:9"}, legacy_files={"advisor.yaml": yaml}).base_url == "http://localhost:9"


def test_re_legacy_backend_names_map_to_the_providers():
    s = r(env={"LLM__BACKEND": "openai", "LLM__OPENAI__MODEL": "gpt-x", "RE_LLM_BASE_URL": "http://localhost:1234/v1"})
    assert s.provider == "openai_compatible" and s.model_for("default") == "gpt-x"


def test_slots_follow_the_same_precedence():
    env = {"TRELLIS_LLM_MODEL": "shared", "TRELLIS_LLM_MODEL_CODE": "shared-code", "RE_LLM_MODEL_CODE": "app-code"}
    s = r(env=env, slots=("default", "code", "query"))
    assert (s.model_for("code"), s.model_for("query"), s.model_for("default")) == ("app-code", "shared", "shared")
    # a slot value in a lower layer does not beat a generic model in a higher one
    s2 = r(env={"TRELLIS_LLM_MODEL_CODE": "shared-code", "RE_LLM_MODEL": "app-model"}, slots=("default", "code"))
    assert s2.model_for("code") == "app-model"


def test_tiers_apply_to_ollama():
    s = r(env={"RE_LLM_TIER": "demo-gpu"}, slots=("default", "code"))
    assert s.tier == "demo-gpu" and s.num_ctx == 8192
    assert s.model_for("default") == "llama3.1:8b" and s.model_for("code") == "codellama:13b"
    assert r(env={"RE_LLM_TIER": "dev", "RE_LLM_NUM_CTX": "4096"}).num_ctx == 4096
    assert r(env={"RE_LLM_TIER": "dev"}).num_ctx == 32768


def test_an_unknown_tier_is_a_startup_error_naming_the_known_ones():
    with pytest.raises(ConfigError, match="demo-cpu"):
        r(env={"RE_LLM_TIER": "turbo"})


def test_tiers_are_ignored_off_ollama_and_say_so():
    s = r(env={"RE_LLM_PROVIDER": "openai_compatible", "RE_LLM_BASE_URL": "http://localhost:1234/v1",
               "RE_LLM_MODEL": "m", "RE_LLM_TIER": "demo-gpu"})
    assert s.tier is None and s.num_ctx is None
    assert any("tier" in n and "ollama" in n for n in s.notes)


def test_a_missing_model_for_openai_compatible_is_a_startup_error_never_an_ollama_name():
    with pytest.raises(ConfigError) as info:
        r(env={"RE_LLM_PROVIDER": "openai_compatible", "RE_LLM_BASE_URL": "http://localhost:1234/v1"})
    assert "RE_LLM_MODEL" in str(info.value) and "llama" not in str(info.value).lower()


def test_a_missing_model_for_anthropic_is_a_startup_error():
    with pytest.raises(ConfigError, match="RE_LLM_MODEL"):
        r(env={"RE_LLM_PROVIDER": "anthropic", "RE_LLM_ALLOW_REMOTE": "true"})


def test_openai_compatible_needs_an_explicit_base_url():
    with pytest.raises(ConfigError, match="RE_LLM_BASE_URL"):
        r(env={"RE_LLM_PROVIDER": "openai_compatible", "RE_LLM_MODEL": "m"})


def test_an_unknown_provider_is_a_startup_error():
    with pytest.raises(ConfigError, match="ollama, openai_compatible, anthropic"):
        r(env={"RE_LLM_PROVIDER": "bedrock"})
