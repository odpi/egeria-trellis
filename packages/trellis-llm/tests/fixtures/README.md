# Fixtures: recorded HTTP, replayed with httpx.MockTransport (no network in CI)

| File | Provenance |
|---|---|
| `lmstudio_chat_complete.json`, `lmstudio_chat_stream.sse` | RECORDED 2026-11-23 from a local LM Studio (`qwen3-coder-next`), `POST /v1/chat/completions`, loopback only |
| `lmstudio_reasoning_cut_by_length.json`, `lmstudio_reasoning_stream.sse` | RECORDED the same way from `laguna-xs-2.1`, a reasoning model: with a small `max_tokens` the answer is in `reasoning_content`, `content` is empty and `finish_reason` is `length` |
| `lmstudio_error_unknown_model.json` | RECORDED: HTTP 400 for an unknown model id |
| `ollama_chat_complete.json`, `ollama_chat_stream.ndjson` | RECORDED from a local Ollama (`llama3.1:8b`), `POST /api/chat` |
| `ollama_error_unknown_model.json` | RECORDED: HTTP 404 for an unknown model |
| `anthropic_*` | HAND-WRITTEN in the documented Messages API wire shape, NOT recorded: calling Anthropic is a remote, paid endpoint, which needs `<APP>_LLM_ALLOW_REMOTE` and the owner's word |

No key was sent to any of these servers, so none is in a file. A fixture with the usage removed is derived in the tests (never edited here).
