"""ollama against recorded Ollama HTTP (/api/chat)."""
import httpx
import pytest

from conftest import Replay, fixture_bytes, fixture_json, json_reply
from trellis_llm import LLMError, make_chat_model
from trellis_llm.config import LLMSettings


def _model(replay: Replay, *, num_ctx=None):
    settings = LLMSettings(app="RE", provider="ollama", base_url="http://localhost:11434",
                           models={"default": "llama3.1:8b"}, num_ctx=num_ctx)
    return make_chat_model(settings, transport=replay.transport)


def test_complete_maps_parameters_to_options_and_reports_usage():
    r = Replay(json_reply("ollama_chat_complete.json"))
    out = _model(r).complete("Reply with the single word: pong", temperature=0, max_tokens=16, num_ctx=2048,
                             top_k=40, repeat_penalty=1.2, top_p=0.9, seed=7, stop=["\n"])
    assert out.text == "Pong" and out.provider == "ollama" and out.model == "llama3.1:8b"
    assert (out.usage.prompt_tokens, out.usage.completion_tokens) == (17, 3)
    assert out.ignored == {}
    assert out.applied == {"temperature": 0, "max_tokens": 16, "num_ctx": 2048, "top_k": 40, "repeat_penalty": 1.2,
                           "top_p": 0.9, "seed": 7, "stop": ["\n"]}
    assert r.body["options"] == {"temperature": 0, "num_predict": 16, "num_ctx": 2048, "top_k": 40,
                                 "repeat_penalty": 1.2, "top_p": 0.9, "seed": 7, "stop": ["\n"]}
    assert r.body["model"] == "llama3.1:8b" and r.body["stream"] is False
    assert r.requests[-1].url.path == "/api/chat"


def test_the_settings_num_ctx_is_a_default_a_caller_can_override_and_it_shows_in_applied():
    r = Replay(json_reply("ollama_chat_complete.json"))
    m = _model(r, num_ctx=32768)
    assert m.complete("hi").applied == {"num_ctx": 32768}
    assert r.body["options"]["num_ctx"] == 32768
    assert m.complete("hi", num_ctx=4096).applied == {"num_ctx": 4096}
    assert r.body["options"]["num_ctx"] == 4096


def test_the_system_message_goes_first():
    r = Replay(json_reply("ollama_chat_complete.json"))
    _model(r).complete("hi", system="Be brief.")
    assert r.body["messages"] == [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "hi"}]


def test_usage_ollama_does_not_report_is_none_never_zero():
    body = fixture_json("ollama_chat_complete.json")
    body.pop("prompt_eval_count"); body.pop("eval_count")
    out = _model(Replay(json_reply(body))).complete("hi")
    assert out.usage.prompt_tokens is None and out.usage.completion_tokens is None


def test_stream_reads_usage_from_the_final_chunk():
    r = Replay(lambda req: httpx.Response(200, content=fixture_bytes("ollama_chat_stream.ndjson"),
                                          headers={"content-type": "application/x-ndjson"}))
    s = _model(r).stream("hi", temperature=0)
    assert "".join(s) == "Pong"
    assert (s.completion.usage.prompt_tokens, s.completion.usage.completion_tokens) == (17, 3)
    assert r.body["stream"] is True


def test_an_early_stop_still_records_the_completion_with_none_usage():
    r = Replay(lambda req: httpx.Response(200, content=fixture_bytes("ollama_chat_stream.ndjson"),
                                          headers={"content-type": "application/x-ndjson"}))
    s = _model(r).stream("hi")
    it = iter(s)
    next(it)
    it.close()
    assert s.completion is not None and s.completion.usage.prompt_tokens is None


def test_an_http_error_is_raised_in_ollamas_words():
    r = Replay(json_reply("ollama_error_unknown_model.json", 404))
    with pytest.raises(LLMError) as info:
        _model(r).complete("hi")
    assert info.value.status == 404 and "model 'no-such-model-xyz' not found" in str(info.value)


def test_an_unreachable_ollama_is_an_error_in_words():
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)
    with pytest.raises(LLMError) as info:
        _model(Replay(refuse)).complete("hi")
    assert "11434" in str(info.value) or "connect" in str(info.value).lower()
