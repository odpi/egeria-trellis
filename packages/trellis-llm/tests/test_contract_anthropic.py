"""anthropic against hand-written Messages API fixtures (see fixtures/README.md: not recorded)."""
import pytest

from conftest import Replay, json_reply, sse_reply, fixture_json
from trellis_llm import LLMError, make_chat_model
from trellis_llm.config import LLMSettings
from trellis_llm.secret import Secret

KEY = "sk-ant-SENTINEL-0123456789"


def _model(replay: Replay):
    settings = LLMSettings(app="RE", provider="anthropic", base_url=None, api_key=Secret(KEY),
                           models={"default": "claude-haiku-4-5-20251001"}, allow_remote=True)
    return make_chat_model(settings, transport=replay.transport)


def test_complete_applies_what_anthropic_takes_and_names_what_it_drops():
    r = Replay(json_reply("anthropic_message.json"))
    out = _model(r).complete("hi", system="Be brief.", temperature=0.2, max_tokens=64, top_p=0.9, top_k=40,
                             stop=["END"], num_ctx=8192, repeat_penalty=1.1, seed=3)
    assert out.text == "pong" and out.provider == "anthropic" and out.finish_reason == "end_turn"
    assert (out.usage.prompt_tokens, out.usage.completion_tokens) == (19, 4)
    assert out.applied == {"temperature": 0.2, "max_tokens": 64, "top_p": 0.9, "top_k": 40, "stop": ["END"]}
    assert set(out.ignored) == {"num_ctx", "repeat_penalty", "seed"}
    assert r.body["system"] == "Be brief." and r.body["stop_sequences"] == ["END"] and r.body["max_tokens"] == 64
    assert not {"num_ctx", "repeat_penalty", "seed"} & set(r.body)


def test_max_tokens_defaults_to_4096_and_a_missing_system_is_the_default_one_as_re_does():
    r = Replay(json_reply("anthropic_message.json"))
    out = _model(r).complete("hi")
    assert r.body["max_tokens"] == 4096 and out.applied == {"max_tokens": 4096}
    assert r.body["system"] == "You are a helpful assistant."


def test_usage_anthropic_does_not_report_is_none():
    body = fixture_json("anthropic_message.json")
    del body["usage"]
    out = _model(Replay(json_reply(body))).complete("hi")
    assert out.usage.prompt_tokens is None and out.usage.completion_tokens is None


def test_stream_reads_usage_from_the_final_message():
    r = Replay(sse_reply("anthropic_stream.sse"))
    s = _model(r).stream("hi")
    assert "".join(s) == "pong"
    assert (s.completion.usage.prompt_tokens, s.completion.usage.completion_tokens) == (19, 4)
    assert r.body["stream"] is True


def test_an_http_error_is_raised_in_anthropics_words():
    with pytest.raises(LLMError) as info:
        _model(Replay(json_reply("anthropic_error.json", 400))).complete("hi")
    assert info.value.status == 400 and "max_tokens: Field required" in str(info.value)


def test_the_key_never_appears_in_an_error():
    echo = {"type": "error", "error": {"type": "authentication_error", "message": f"invalid x-api-key {KEY}"}}
    with pytest.raises(LLMError) as info:
        _model(Replay(json_reply(echo, 401))).complete("hi")
    assert KEY not in str(info.value) and KEY not in repr(info.value)
