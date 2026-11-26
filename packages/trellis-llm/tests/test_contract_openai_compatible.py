"""openai_compatible against recorded LM Studio HTTP: complete, stream, usage present and absent, ignored parameters,
HTTP errors in the provider's words, and a key that never leaks."""
import json
import logging

import pytest

from conftest import Replay, fixture_json, json_reply, sse_reply
from trellis_llm import LLMError, make_chat_model
from trellis_llm.config import LLMSettings
from trellis_llm.secret import Secret


def _model(replay: Replay, *, key=None, model="qwen3-coder-next"):
    settings = LLMSettings(app="RE", provider="openai_compatible", base_url="http://localhost:1234/v1",
                           api_key=Secret(key) if key else None, models={"default": model})
    return make_chat_model(settings, transport=replay.transport)


def test_complete_returns_text_model_provider_usage_and_what_was_applied():
    r = Replay(json_reply("lmstudio_chat_complete.json"))
    out = _model(r).complete("Reply with the single word: pong", system="Be brief.", temperature=0, max_tokens=16)
    assert out.text == "pong" and out.model == "qwen3-coder-next" and out.provider == "openai_compatible"
    assert (out.usage.prompt_tokens, out.usage.completion_tokens) == (23, 2)
    assert out.applied == {"temperature": 0, "max_tokens": 16} and out.ignored == {}
    assert out.finish_reason == "stop"
    assert r.body["model"] == "qwen3-coder-next" and r.body["temperature"] == 0 and r.body["max_tokens"] == 16
    assert r.body["messages"] == [{"role": "system", "content": "Be brief."},
                                  {"role": "user", "content": "Reply with the single word: pong"}]
    assert str(r.requests[-1].url) == "http://localhost:1234/v1/chat/completions"


def test_the_parameters_this_provider_drops_are_named_with_a_reason_and_not_sent():
    r = Replay(json_reply("lmstudio_chat_complete.json"))
    out = _model(r).complete("hi", num_ctx=8192, top_k=40, repeat_penalty=1.2, temperature=0.2)
    assert set(out.ignored) == {"num_ctx", "top_k", "repeat_penalty"}
    assert "context" in out.ignored["num_ctx"].lower() and "/v1" in out.ignored["num_ctx"]
    assert out.applied == {"temperature": 0.2}
    assert not {"num_ctx", "top_k", "repeat_penalty"} & set(r.body)


def test_each_ignored_parameter_is_logged_once_per_provider_and_parameter(caplog):
    r = Replay(json_reply("lmstudio_chat_complete.json"))
    m = _model(r)
    with caplog.at_level(logging.WARNING, logger="trellis_llm"):
        m.complete("a", top_k=1)
        m.complete("b", top_k=2)
        m.complete("c", repeat_penalty=1.1)
    msgs = [x.getMessage() for x in caplog.records]
    assert sum("top_k" in x for x in msgs) == 1 and sum("repeat_penalty" in x for x in msgs) == 1


def test_an_unknown_parameter_is_the_callers_bug_and_is_refused():
    with pytest.raises(ValueError, match="unknown parameter 'temprature'"):
        _model(Replay(json_reply("lmstudio_chat_complete.json"))).complete("hi", temprature=0.1)


def test_usage_the_provider_does_not_report_is_none_never_zero():
    body = fixture_json("lmstudio_chat_complete.json")
    del body["usage"]
    out = _model(Replay(json_reply(body))).complete("hi")
    assert out.usage.prompt_tokens is None and out.usage.completion_tokens is None


def test_a_reasoning_model_cut_by_length_says_so_instead_of_returning_a_bare_empty_string():
    out = _model(Replay(json_reply("lmstudio_reasoning_cut_by_length.json")), model="laguna-xs-2.1").complete("hi")
    assert out.text == "" and out.finish_reason == "length"
    assert (out.usage.prompt_tokens, out.usage.completion_tokens) == (26, 16)


def test_stream_yields_chunks_then_the_completion_with_usage():
    r = Replay(sse_reply("lmstudio_chat_stream.sse"))
    s = _model(r).stream("Reply with the single word: pong", temperature=0, max_tokens=16)
    assert s.completion is None
    assert "".join(s) == "pong"
    assert s.completion.text == "pong" and s.completion.provider == "openai_compatible"
    assert (s.completion.usage.prompt_tokens, s.completion.usage.completion_tokens) == (15, 2)
    assert r.body["stream"] is True and r.body["stream_options"] == {"include_usage": True}


def test_stream_usage_is_none_when_the_server_sends_none():
    import httpx
    lines = [l for l in open(__import__("conftest").FIXTURES / "lmstudio_chat_stream.sse").read().split("\n\n")
             if '"usage"' not in l or '"choices":[]' not in l]
    data = "\n\n".join(lines).encode()
    r = Replay(lambda req: httpx.Response(200, content=data, headers={"content-type": "text/event-stream"}))
    s = _model(r).stream("hi")
    assert "".join(s) == "pong"
    assert s.completion.usage.prompt_tokens is None and s.completion.usage.completion_tokens is None


def test_an_http_error_is_raised_in_the_providers_words_with_its_status():
    r = Replay(json_reply("lmstudio_error_unknown_model.json", 400))
    with pytest.raises(LLMError) as info:
        _model(r, model="no-such-model-xyz").complete("hi")
    assert info.value.status == 400 and info.value.provider == "openai_compatible"
    assert 'Invalid model identifier "no-such-model-xyz"' in str(info.value)


def test_a_key_the_server_echoes_back_never_appears_in_an_error():
    key = "sk-SENTINEL-0123456789"
    echo = {"error": {"message": f"Incorrect API key provided: {key}", "type": "invalid_request_error"}}
    with pytest.raises(LLMError) as info:
        _model(Replay(json_reply(echo, 401)), key=key).complete("hi")
    assert key not in str(info.value) and key not in repr(info.value) and "***" in str(info.value)


def test_the_key_is_sent_as_a_bearer_token_and_no_key_sends_a_placeholder():
    r = Replay(json_reply("lmstudio_chat_complete.json"))
    _model(r, key="sk-abc").complete("hi")
    assert r.requests[-1].headers["authorization"] == "Bearer sk-abc"
    r2 = Replay(json_reply("lmstudio_chat_complete.json"))
    _model(r2).complete("hi")
    assert r2.requests[-1].headers["authorization"].startswith("Bearer ")      # the client needs one; the value is not secret


def test_an_unreachable_server_is_an_error_in_words():
    import httpx

    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)
    with pytest.raises(LLMError) as info:
        _model(Replay(refuse)).complete("hi")
    assert "localhost:1234" in str(info.value) or "connect" in str(info.value).lower()
