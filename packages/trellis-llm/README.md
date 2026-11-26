# trellis-llm

One way for Trellis apps to talk to a chat model (TR-38). **Step 1: the package only.** Nothing imports it yet; RE moves onto
it in step 2 and EA in step 3.

```python
from trellis_llm import resolve, make_chat_model, log_startup

settings = resolve("RE", slots=("default",))    # RE_LLM_* > TRELLIS_LLM_* > the app's legacy names > defaults
log_startup(settings)                           # "LLM (RE): provider=..., host=..., models: ...; prompts go to <host>"
model = make_chat_model(settings)
c = model.complete("...", system="...", temperature=0.1, num_ctx=8192)
c.text, c.model, c.provider, c.usage, c.applied, c.ignored, c.finish_reason
for piece in (s := model.stream("...")): ...    # then s.completion has the usage
```

## Providers (each an optional extra; `import trellis_llm` imports none of them)

| provider | extra | talks to |
|---|---|---|
| `ollama` | `trellis-llm[ollama]` | Ollama's native `/api/chat`; `num_ctx` is always sent (the tier's, unless the call gives one) |
| `openai_compatible` | `trellis-llm[openai]` | any OpenAI-compatible server (LM Studio, vLLM, llama.cpp ...) through the official `openai` client, with an **explicit** `base_url` |
| `anthropic` | `trellis-llm[anthropic]` | the Messages API, as RE's client does it (default system prompt, `max_tokens` 4096) |

Choosing a provider whose extra is missing says which one to install.

## What a call tells you

`Completion.usage` is `prompt_tokens` / `completion_tokens`, **None** when the provider did not report them (never 0).
`applied` is `{parameter: value}` that reached the provider; `ignored` is `{parameter: reason}` the provider dropped
(`num_ctx` on `/v1`; `top_k` and `repeat_penalty` on OpenAI; `num_ctx`, `repeat_penalty` and `seed` on Anthropic). Each drop
is logged once per (provider, parameter). `finish_reason` is the provider's own word (`stop`, `length`, `end_turn`): a reasoning
model cut by `length` returns `text == ""`, and this is how a caller can tell that from an empty answer.

Parameters: `temperature max_tokens top_p top_k repeat_penalty num_ctx seed stop`. A name outside that list raises `ValueError`.

## Config: one resolver

Per setting: `<APP>_LLM_<NAME>` > `TRELLIS_LLM_<NAME>` > the app's legacy names (each warns once) > defaults.
`PROVIDER BASE_URL API_KEY MODEL MODEL_<SLOT> TIER NUM_CTX ALLOW_REMOTE`. Tiers (`dev`, `demo-gpu`, `demo-cpu`) apply to **ollama
only** (elsewhere they are ignored, and `settings.notes` says so). With `openai_compatible` or `anthropic` a missing model is a startup
error in words, never a fall back to an Ollama name; `openai_compatible` has no default `base_url`.

Legacy names, read as data: RE `LLM__BACKEND`, `LLM__OLLAMA__BASE_URL`, `LLM__{OLLAMA,OPENAI,ANTHROPIC}__MODEL`,
`EXPLORER_MODEL_TIER`; EA `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `OLLAMA_CODE_MODEL`, `ADVISOR_MODEL_TIER`, and `advisor.yaml` (pass it as
`legacy_files={"advisor.yaml": {...}}`).

## Data egress

A `base_url` that is not loopback or a private address (10/8, 172.16/12, 192.168/16, link-local, `::1`; a hostname counts only if
every address it resolves to is one, and an unresolvable name fails closed), and the `anthropic` provider, are refused unless
`<APP>_LLM_ALLOW_REMOTE=true` (or `TRELLIS_LLM_ALLOW_REMOTE`). The startup line says where prompts go.

## The API key

Held as a `Secret`: `repr`, `str` and f-strings give `***`. It is never in the startup line, a log record, `repr(settings)`, or an
exception message (a key a server echoes back in an error body is scrubbed, and the SDK exception is not chained).

## Tests

```
uv run --package trellis-llm --all-extras pytest packages/trellis-llm
```

No network: the official clients run for real against recorded HTTP replayed through `httpx.MockTransport`
(`tests/fixtures/README.md` says which fixtures were recorded from a local server and which are hand-written).
