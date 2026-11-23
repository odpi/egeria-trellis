"""Importing trellis_llm with no extras works; choosing a provider whose extra is missing says which extra to install."""
import subprocess
import sys

import pytest

from trellis_llm import MissingExtra, make_chat_model
from trellis_llm.config import LLMSettings


def test_importing_trellis_llm_pulls_in_no_provider_library():
    code = ("import sys, trellis_llm\n"
            "bad = [m for m in ('ollama', 'openai', 'anthropic', 'httpx') if m in sys.modules]\n"
            "print('LOADED:' + ','.join(bad))\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "LOADED:", out


@pytest.mark.parametrize("provider,extra,module", [("ollama", "ollama", "ollama"), ("openai_compatible", "openai", "openai"),
                                                   ("anthropic", "anthropic", "anthropic")])
def test_a_missing_extra_names_the_extra_to_install(monkeypatch, provider, extra, module):
    monkeypatch.setitem(sys.modules, module, None)             # an import of it now raises ImportError
    from trellis_llm.secret import Secret
    s = LLMSettings(app="RE", provider=provider, base_url="http://localhost:1/v1" if provider != "anthropic" else None,
                    api_key=Secret("k") if provider == "anthropic" else None, models={"default": "m"}, allow_remote=True)
    with pytest.raises(MissingExtra) as info:
        make_chat_model(s)
    assert f"trellis-llm[{extra}]" in str(info.value) and provider in str(info.value)
    assert info.value.extra == extra


def test_an_unknown_provider_in_settings_is_refused():
    with pytest.raises(ValueError, match="bedrock"):
        make_chat_model(LLMSettings(app="RE", provider="bedrock", base_url=None, models={"default": "m"}))
