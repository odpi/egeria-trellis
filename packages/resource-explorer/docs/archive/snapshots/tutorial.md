# Building Project Explorer: An Incremental Design Tutorial

**Status:** historical. Narrates how the predecessor Project Explorer was built; do not follow as instructions.

> **HISTORICAL — do not follow this as instructions.** This tutorial narrates how
> **Project Explorer**, Resource Explorer's predecessor, was built. It teaches a
> stack that no longer exists here: Milvus was replaced by pgvector, and the
> package was renamed from `explorer` to `resource_explorer`. Kept for the
> design reasoning, which is still good — the incremental narrative of *why* each
> piece was added is the value, not the code. For how the current system works,
> see `user-guide.md`, `extending-resource-explorer.md` and `Architecture.md`.


This tutorial walks through how Project Explorer was designed and built in stages, explaining the reasoning behind each decision and the open-source tools chosen at each step. The goal is not just to describe what was built, but to show *why* each piece was added when it was, what problem it solved, and what you would have been missing without it.

The project is a multi-agent RAG system that lets you ask natural-language questions about GitHub repositories. By the end of this tutorial you will understand how to build something similar from scratch using the same open-source stack.

---

## The Starting Point: What Are We Actually Building?

Before writing a line of code, it helps to define the problem clearly. We want to answer questions like:

- "How does the authentication module work in this project?"
- "Who are the top contributors in the last 90 days?"
- "Is this project actively maintained?"
- "Compare the architecture of project A versus project B."

These questions span fundamentally different data sources and require different reasoning strategies. A single RAG pipeline cannot handle all of them well — some need vector search over code and docs, some need live database lookups, some need structured comparison logic. This observation shapes the entire design.

The reference implementation we drew from was [lfai/ML_LLM_Ops](https://github.com/lfai/ML_LLM_Ops), which validated the BeeAI + Milvus + Ollama pattern for production-grade agent workflows.

---

## Phase 1: The Core RAG Pipeline

### The Minimum Viable Query

The first thing to get right is the basic retrieval-augmented generation loop:

1. Embed a user query into a vector
2. Search a vector store for similar chunks
3. Pass the chunks as context to an LLM
4. Return the generated answer

This is the foundation everything else builds on. Every other phase either improves this loop or routes around it entirely.

### Choosing the Vector Store: Milvus

We chose [Milvus](https://milvus.io/) (`pymilvus`) over alternatives like Chroma or Qdrant for three reasons:

- **Milvus Lite** runs as a local `.db` file — zero infrastructure for development, identical API for production
- **MilvusClient** provides a simple high-level interface that works against both Lite and standalone
- **COSINE similarity** over 384-dim vectors is fast even with `FLAT` index type on Milvus Lite

The collection schema is intentionally minimal — four fields, no dynamic fields, no complex metadata indexes:

```python
schema = client.create_schema(auto_id=True, enable_dynamic_field=False)
schema.add_field("id",            DataType.INT64,         is_primary=True)
schema.add_field("vector",        DataType.FLOAT_VECTOR,  dim=384)
schema.add_field("text",          DataType.VARCHAR,        max_length=65_535)
schema.add_field("metadata_json", DataType.VARCHAR,        max_length=65_535)
```

Metadata (file path, language, chunk index, etc.) is stored as a JSON string in a single VARCHAR field. This avoids schema migrations when metadata shape changes, at the cost of in-memory filtering when needed. For a read-heavy workload like this, that tradeoff is acceptable.

The `FLAT` index type is critical for Milvus Lite compatibility. When deploying to Milvus standalone at scale, swap it for `HNSW` in `_ensure_collection()`.

### Choosing Embeddings: sentence-transformers

We used [sentence-transformers](https://www.sbert.net/) (`sentence-transformers` package) with the `all-MiniLM-L6-v2` model:

- 384-dimensional vectors — small enough that FLAT search is fast, large enough to capture semantic similarity well
- Runs locally on CPU or MPS (Apple Silicon Metal GPU)
- No API key or rate limits
- Familiar in the LF AI community from prior projects

The model is loaded once per process and reused:

```python
from sentence_transformers import SentenceTransformer

_model: SentenceTransformer | None = None

def get_embedding_model() -> SentenceTransformer:
    global _model
    if _model is None:
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        _model = SentenceTransformer("all-MiniLM-L6-v2", device=device)
    return _model
```

This singleton pattern matters: loading a transformer model takes 2–4 seconds. Re-loading it per query would make the system unusable.

### Choosing the LLM: Ollama

[Ollama](https://ollama.ai/) runs large language models locally with Metal GPU acceleration on Apple Silicon. For development, this means no API costs and no data leaving your machine. For production, the same code paths work with OpenAI or Anthropic backends.

The `LLMBackend` protocol defines two methods:

```python
class LLMBackend(Protocol):
    def complete(self, prompt: str, system: str = "", **kwargs) -> str: ...
    def stream(self, prompt: str, system: str = "", **kwargs) -> Iterator[str]: ...
```

The `stream()` method was added later when we needed token-by-token output for the TUI and web UI. Having a protocol from the start made this a safe addition — the `complete()` callers were unaffected.

The Ollama implementation uses `httpx` to call the local API:

```python
response = httpx.post(
    f"{self.base_url}/api/generate",
    json={"model": self.model, "prompt": prompt, "stream": False},
    timeout=120.0,
)
return response.json()["response"]
```

For streaming, the same endpoint is called with `"stream": True` and each newline-delimited JSON chunk is yielded as it arrives.

### The RAG Loop

With these three pieces in place, the first working query looked like:

```python
# Embed the query
q_vec = embed_one(query)

# Search Milvus
results = client.search(
    collection_name=collection,
    data=[q_vec],
    limit=5,
    output_fields=["text", "metadata_json"],
)

# Build context and call LLM
context = "\n\n---\n\n".join(r["entity"]["text"] for r in results[0])
prompt = build_rag_prompt(query, context)
return llm.complete(prompt)
```

This worked. But it had two immediate problems: it searched only one collection, and it had no way to answer questions about commit counts or project health. These drove the next two phases.

---

## Phase 2: Content-Aware Ingestion

### The Problem With a Single Collection

A single flat collection means code chunks compete with documentation chunks in the same vector space. A query about "how authentication is implemented" might return a README paragraph about authentication instead of the actual implementation code. Worse, different content types benefit from very different chunk sizes:

| Content Type | Ideal Chunk Size | Why |
|---|---|---|
| Source code | 512 tokens, overlap 64 | Functions fit; overlap catches split signatures |
| Markdown docs | 384 tokens, overlap 48 | Paragraphs and sections; smaller than code |
| API specs | 256 tokens, overlap 32 | Dense; small chunks are more precise |
| Notebooks | 1024 tokens, overlap 128 | Code + output pairs need to stay together |

The solution is **namespaced collections** per project per content type: `{project_slug}_{collection_type}`. Unity Catalog gets `unitycatalog_python_code`, `unitycatalog_markdown_docs`, `unitycatalog_api_reference`, and so on. Each collection is configured independently with its own chunk size and file extension list.

### Docling for Document Parsing

For PDF, web pages, and structured documents, we used [Docling](https://github.com/DS4SD/docling) — an open-source document understanding library from IBM Research. Docling extracts clean structured text from PDFs (including tables and figures) and HTML documentation sites, feeding it into the same chunking pipeline as everything else.

Without Docling, PDF ingestion requires Tesseract OCR or ad-hoc `pdfminer` hacks. Docling handles layout analysis, table extraction, and reading order detection out of the box.

### The Onboarding Wizard

Rather than requiring users to configure collections manually, we built `RepoAnalyzer` to inspect a repository and propose a collection plan:

```python
def _build_plan(self, repo) -> list[CollectionType]:
    """Inspect file extensions and propose which collections to create."""
    extensions = self._sample_extensions(repo, max_files=500)
    plan = []
    if any(e in extensions for e in [".py"]):
        plan.append(CollectionType.PYTHON_CODE)
    if any(e in extensions for e in [".js", ".ts"]):
        plan.append(CollectionType.JAVASCRIPT_CODE)
    if any(e in extensions for e in [".md", ".rst"]):
        plan.append(CollectionType.MARKDOWN_DOCS)
    # ... etc
    return plan
```

The `OnboardingWizard` presents this plan to the user and lets them confirm or customize before ingestion begins. This pattern — propose and confirm — is more robust than either fully manual or fully automatic configuration. Users who accept the defaults get a sensible setup; users who know their repo can tune it.

### Zipball Download Instead of Per-File API Calls

The first ingestion implementation used the GitHub API to fetch each file individually. This hit rate limits on large repos (Egeria has thousands of files). The fix was to download the entire repo as a single zipball — one API call regardless of repo size:

```python
def download_zipball(self, repo, tmp_dir: Path) -> Path:
    url = repo.get_archive_link("zipball")
    response = requests.get(url, stream=True)
    zip_path = tmp_dir / "repo.zip"
    with open(zip_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(tmp_dir)
    return next(tmp_dir.iterdir())  # the extracted root dir
```

This is a case where the obvious implementation (per-file API) worked fine in testing but failed in production on large repos. Download-once-parse-locally is more robust and much faster.

---

## Phase 3: Intent Classification Before Retrieval

### The Problem With RAG-for-Everything

After Phase 2, queries about code and documentation worked well. But "How many stars does this project have?" returned hallucinated numbers — the vector store has no star count, and the LLM guessed.

The root cause is that some queries should never touch the vector store at all. Statistical queries have precise answers in structured databases. Sending them through RAG is not just ineffective — it actively degrades quality because the LLM gets permission to fill in gaps with made-up numbers.

The fix is to classify intent *before* touching Milvus.

### QueryProcessor: Regex Patterns Over YAML

We chose a simple regex-based classifier over an LLM-based classifier for two reasons:

1. **Speed** — no LLM call, no embeddings. Classification is a few microseconds.
2. **Predictability** — patterns can be audited, tested, and edited without touching code.

Patterns live in `config/routing.yaml`:

```yaml
intent_patterns:
  statistical:
    priority: CRITICAL
    patterns:
      - "how many (commits|contributors|stars|forks|releases)"
      - "who (are the|have been)? (contribut\w+|committ?\w+)"
      - "show (me )?(a )?(graph|chart|plot|trend)"
      - "(stars|forks|watchers|issues|releases|contributors)"

  health:
    priority: HIGH
    patterns:
      - "(is|how).*(actively|well) maintained"
      - "bus factor"
      - "abandoned|archived|dead"

  code_search:
    priority: NORMAL
    patterns:
      - "how (do I|to|can I) (use|call|implement)"
      - "how is .+ implemented"
```

The classifier tries intents in priority order, first match wins:

```python
def classify(self, query: str) -> QueryIntent:
    q = query.lower()
    priority_order = ["statistical", "comparison", "health", "code_search", "conceptual"]
    for intent_name in priority_order:
        rule = self._rules.get(intent_name, {})
        for pattern in rule.get("patterns", []):
            if re.search(pattern, q, re.IGNORECASE):
                return QueryIntent(intent_name)
    return QueryIntent.GENERAL
```

One important YAML gotcha: regex patterns containing backslashes (`\w`, `\d`) must use single-quoted YAML strings. Double-quoted strings treat `\` as an escape character, making `\w` invalid. This caused silent failures in early testing that were hard to diagnose.

### The RAGSystem Orchestrator

With classification in place, `RAGSystem._route()` became a simple dispatch table:

```python
def _route(self, query, intent, project_slug):
    if intent == QueryIntent.STATISTICAL:
        return StatsAgent().handle(query, project_slug), []
    if intent == QueryIntent.HEALTH:
        return HealthAgent().handle(query, project_slug), []
    if intent == QueryIntent.CODE_SEARCH:
        return CodeAgent().handle(query, project_slug), []
    if intent == QueryIntent.CONCEPTUAL:
        return DocAgent().handle(query, project_slug), []
    if intent == QueryIntent.COMPARISON:
        return CompareAgent().handle(query, project_slug), []
    return self._rag(query, project_slug)  # GENERAL fallback
```

Statistical and health agents read from SQLite. They never touch Milvus. This separation is load-bearing: it's what prevents hallucinated commit counts.

---

## Phase 4: Tool-Using Agents with BeeAI

### Why Agents Instead of Direct Function Calls

After Phase 3, each agent was just a function: `stats_agent(query, project_slug) -> str`. This worked for simple queries but failed on compound ones:

- "Show me the top committers and the commit trend for the last 3 months" — requires two database lookups plus synthesis
- "How does the authentication work and what are the test coverage stats?" — spans code search and a stats lookup

The solution is agents that can call multiple tools iteratively. [BeeAI Framework](https://beeai.dev) provides `RequirementAgent` — a ReAct-style agent that loops between "think, call a tool, observe result, think again" until it has enough information to answer.

### The @tool Pattern

BeeAI tools are ordinary Python functions decorated with `@tool`. The decorator uses the docstring as the tool description (what the LLM sees to decide when to call it) and the function signature to generate a Pydantic schema for argument validation:

```python
from beeai_framework.tools import tool

@tool(description=(
    "Search indexed project content (code, docs, API specs) for text relevant to the query. "
    "collection_names is a comma-separated list of fully-qualified collection names, "
    "e.g. 'unitycatalog_python_code,unitycatalog_markdown_docs'. "
    "Call multiple times with different queries or collections to gather more context."
))
def vector_search(query: str, collection_names: str) -> str:
    collections = [c.strip() for c in collection_names.split(",") if c.strip()]
    results = MultiCollectionStore().search(query, collections)
    if not results:
        return "No relevant content found in the specified collections."
    parts = [f"[{r.collection} | score={r.score:.2f}]\n{r.text}" for r in results]
    return "\n\n---\n\n".join(parts)
```

The description is critical — it is the only thing the LLM uses to decide when and how to call the tool. Vague descriptions lead to incorrect calls; specific ones (including the format of arguments) lead to reliable behavior.

We defined four shared tools:

| Tool | What it does |
|---|---|
| `vector_search(query, collection_names)` | Milvus similarity search across named collections |
| `query_project_stats(project_slug)` | Returns formatted stats from SQLite |
| `query_top_committers(project_slug, limit)` | Ranked contributor list from `project_commits` |
| `query_commit_activity(project_slug)` | Weekly commit bar chart from `project_commits` |

Tools are shared across agents — a tool defined once can appear in the `tools()` list of any agent that needs it. This is cleaner than duplicating retrieval logic per agent.

### BaseExplorerAgent

All agents share a common base class that handles BeeAI setup and the async/sync bridge:

```python
class BaseExplorerAgent(ABC):
    @abstractmethod
    def system_prompt(self) -> str: ...

    @abstractmethod
    def tools(self) -> list: ...

    def _build_agent(self):
        return RequirementAgent(
            llm=self._llm_name(),      # e.g. "ollama:llama3.1:8b"
            tools=self.tools(),
            instructions=self.system_prompt(),
        )

    def _run_agent(self, prompt: str) -> str:
        async def _inner():
            agent = self._build_agent()
            result = await agent.run(prompt)
            return result.output[0].text

        try:
            asyncio.get_running_loop()
            # Inside FastAPI or Textual — use a thread to avoid event loop conflicts
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(lambda: asyncio.run(_inner())).result()
        except RuntimeError:
            # No running loop — call asyncio.run directly
            return asyncio.run(_inner())
```

The async/sync bridge (`asyncio.get_running_loop()` → ThreadPoolExecutor) is boilerplate that every BeeAI integration in a synchronous codebase needs. Centralizing it in `BaseExplorerAgent` means individual agents never think about it.

### Defining an Agent

With the base class in place, a new agent is about 15 lines:

```python
class StatsAgent(BaseExplorerAgent):
    def system_prompt(self) -> str:
        return """You are a data analyst for GitHub project statistics.
Always call the appropriate tool first, then present the results.
Never write code, never describe how to fetch data, never use hypothetical numbers."""

    def tools(self) -> list:
        from explorer.agents.tools import (
            query_project_stats, query_top_committers, query_commit_activity
        )
        return [query_project_stats, query_top_committers, query_commit_activity]

    def handle(self, query: str, project_slug: str | None = None, **kwargs) -> str:
        slug = project_slug or self._infer_project_slug(query)
        if not slug:
            return self._clarification_response(query)
        return self._run_agent(f"Project: {slug}\n\nQuestion: {query}")
```

Notice the system prompt explicitly says "never write code" — this was added after the agent started generating Python tutorials in response to "graph commits per week". LLMs default to describing *how* to do things rather than *doing* them via tools. The system prompt must explicitly prohibit this.

### Project Inference

A key usability feature: you should not have to specify a project slug on every query. `_infer_project_slug()` scans the registry for a project whose name or slug appears in the query text:

```python
def _infer_project_slug(self, query: str) -> str | None:
    q = query.lower()
    for project in ProjectRegistry().list_all():
        if project.slug.lower() in q:
            return project.slug
        # Also match on display name words ("Unity Catalog" → slug "unitycatalog")
        words = project.display_name.lower().split()
        if all(w in q for w in words):
            return project.slug
    return None
```

When inference fails, `_clarification_response()` returns a message that starts with the exact string "Which project are you asking about?" — every UI layer detects this prefix and enters a clarification flow (sidebar selection, typed name, or `project:` prefix).

---

## Phase 5: Multiple Interfaces

With the backend solid, we built three interfaces: a CLI for scripting and automation, a TUI for power users, and a web UI for broader access. All three share the same `RAGSystem` backend.

### CLI with Typer and Rich

[Typer](https://typer.tiangolo.com/) generates a fully typed CLI from Python function signatures. [Rich](https://rich.readthedocs.io/) handles formatting. Together they give you a polished CLI with almost no boilerplate:

```python
import typer
from rich.console import Console

app = typer.Typer()
console = Console()

@app.command()
def ask(
    query: str = typer.Argument(..., help="Question to ask"),
    project: str | None = typer.Option(None, "--project", "-p"),
):
    """Ask a one-shot question."""
    rag = RAGSystem()
    response = rag.query(query, project_slug=project)
    console.print(response)
```

Typer automatically generates `--help`, handles type coercion, and validates arguments. The `chat` command extends this with a REPL loop backed by `ConversationAgent`, giving users an interactive session with cross-turn memory.

### TUI with Textual

[Textual](https://textual.textualize.io/) is a Python framework for building full-screen terminal UIs. It provides a widget system, reactive state, and CSS-like layout — significantly higher-level than curses.

The TUI architecture has three key challenges:

**1. Streaming into a live widget.** Textual runs its own event loop. BeeAI and the LLM run blocking operations. The solution is `@work(thread=True)` — Textual's worker decorator that runs a function in a background thread, with `call_from_thread()` to safely update the UI:

```python
@work(thread=True)
def _run_query(self, query: str) -> None:
    bubble = ChatMessage("Assistant")
    self.call_from_thread(log.mount, bubble)

    for chunk in self._rag.stream(query, project_slug=self.selected_project):
        if isinstance(chunk, dict) and chunk.get("_done"):
            break
        self.call_from_thread(bubble.append_text, str(chunk))
        self.call_from_thread(log.scroll_end, False)
```

**2. Conversation memory across turns.** Each query runs in a fresh worker thread. BeeAI's `TokenMemory` is not thread-safe across `asyncio.run()` calls in separate threads. The solution: stream the response via `RAGSystem`, then feed the completed turn back into the `ConversationAgent`'s BeeAI memory *after* the stream finishes:

```python
async def _add_to_memory():
    mem = self._conv._get_agent().memory
    await mem.add(UserMessage(query))
    await mem.add(AssistantMessage(accumulated))

with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
    ex.submit(lambda: asyncio.run(_add_to_memory())).result(timeout=5)
```

**3. Pre-warming to avoid threading conflicts.** PyTorch and gRPC initialize file descriptors in the main thread. If they initialize inside a Textual worker thread, FD conflicts occur. The `run()` entry point forces model and client initialization in the main thread before Textual starts. Each step is individually fault-tolerant — a missing model or unreachable Milvus does not prevent the TUI from launching; errors surface inline when a query is submitted:

```python
def run() -> None:
    try:
        get_embedding_model()   # force SentenceTransformer init in main thread
    except Exception:
        pass

    try:
        MultiCollectionStore()._get_client()  # force Milvus client init
    except Exception:
        pass

    rag = RAGSystem()           # lightweight wrapper; connections happen lazily
    conv = ConversationAgent(rag_system=rag)
    ProjectExplorerApp(conv, rag).run()
```

### Web UI with FastAPI and Plotly

[FastAPI](https://fastapi.tiangolo.com/) serves both the API and the single-page HTML frontend. The frontend uses Tailwind CSS, marked.js for markdown rendering, and Plotly.js for charts — all from CDN, no build step.

**Server-Sent Events for streaming.** FastAPI's `StreamingResponse` with `media_type="text/event-stream"` streams tokens to the browser as they are generated. The synchronous `RAGSystem.stream()` generator runs in a daemon thread, bridging to the async FastAPI handler via an `asyncio.Queue`:

```python
@router.post("/stream")
async def stream(request: QueryRequest) -> StreamingResponse:
    async def _generate():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def _producer():
            for item in rag.stream(request.query, project_slug=request.project_slug):
                loop.call_soon_threadsafe(queue.put_nowait, item)
            loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=_producer, daemon=True).start()

        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, dict) and item.get("_done"):
                yield f"data: {json.dumps({'t': 'done', 'chart': pick_chart(...)})}\n\n"
            else:
                yield f"data: {json.dumps({'t': 'chunk', 'v': str(item)})}\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")
```

The `_done` sentinel dict carries metadata — intent, query hash, and an optional Plotly figure — alongside the final text event. The browser renders markdown and injects the chart after the stream finishes.

**Plotly charts as JSON.** Plotly figures are serialized with `fig.to_json()` and sent to the browser as plain JSON. The browser calls `Plotly.newPlot()` with the deserialized figure. This means chart logic lives entirely on the server (Python), and the browser is just a renderer. Plotly has a rich Python API for constructing figures; there is no need to duplicate chart logic in JavaScript.

---

## Phase 6: Statistics from Live Data

### The Snapshot vs. Live Data Problem

After ingestion, stats were stored as a snapshot in `project_stats`: `commits_30d`, `commits_90d`, etc. When a user asked "how many commits were there in the last 30 days?", the agent read from this snapshot. When they asked "who are the top committers?", the agent read from `project_commits` (the per-commit table). These two paths could return inconsistent numbers — the snapshot said 0 commits (stale), while the per-commit table had current data.

The fix: treat `project_commits` as the authoritative source for all commit counts. The stats tool now queries `project_commits` directly:

```python
now = datetime.now(timezone.utc)
cutoff_30 = (now - timedelta(days=30)).isoformat()
live_30 = conn.execute(
    "SELECT COUNT(*) FROM project_commits "
    "WHERE project_slug = ? AND committed_at >= ?",
    (slug, cutoff_30),
).fetchone()[0]

# Prefer live count; fall back to snapshot only if project_commits is empty
commits_30d = live_30 if live_30 or live_90 else (d.get("commits_30d") or 0)
```

`project_commits` uses `UNIQUE(project_slug, sha)` with `INSERT OR IGNORE` for idempotent fetches, so refreshes are safe to run repeatedly.

### Weekly Commit Charts

The sidebar "Commits" chart showed snapshots (the 30-day window recorded at each refresh). A user asking "graph commits per week" expects per-week granularity from actual commit data, not a bar chart of snapshot windows.

`weekly_commits_plotly()` reads directly from `project_commits`, buckets commits into 13 weekly bins, and builds a Plotly bar chart:

```python
week_counts: defaultdict = defaultdict(int)
for (ts,) in rows:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    weeks_ago = (now - dt).days // 7
    if 0 <= weeks_ago < 13:
        week_counts[weeks_ago] += 1

week_offsets = list(range(12, -1, -1))   # oldest → newest left-to-right
dates = [(now - timedelta(weeks=w)).strftime("%Y-%m-%d") for w in week_offsets]
counts = [week_counts.get(w, 0) for w in week_offsets]
```

The `_pick_chart()` function selects this chart when the query contains "week", "weekly", or "per week":

```python
elif any(w in q for w in ("week", "weekly", "per week", "week-by-week")):
    fig = graphs.weekly_commits_plotly(project_slug)
```

The chart appears inline in the chat response — not just in the sidebar — so the answer and its visualization arrive together.

`weekly_commits_plotly` is also the source for the sidebar **Commits** tab. The original implementation used `commits_over_time_plotly`, which reads from `project_stats` snapshot rows. With only one snapshot (the common case after a fresh add), it produces a single bar chart that looks flat and carries no trend information. Switching the endpoint to `weekly_commits_plotly` gives 13 weeks of granular data from the first refresh forward.

All bar and line charts now include `yaxis_rangemode="tozero"` in their Plotly layout. Without this, Plotly auto-scales the y-axis to the data range — a chart showing 5000 stars might span 4999–5001, making the line appear flat against the bottom of the frame. Pinning the range to zero makes magnitude visible at a glance.

### Rate-Limit-Aware Commit History

`_fetch_commits` makes one extra REST call per commit to retrieve per-commit `additions` and `deletions`. For active repos with hundreds of commits over a 90-day window, this can deplete GitHub's rate limit (5,000 calls/hour for authenticated users) faster than expected — especially when combined with the other API calls in `StatsFetcher.fetch()`. Extending the window to 365 days multiplies the exposure.

The original code detected rate limit errors per call and stopped gracefully, but it never looked ahead — it entered the loop and blasted the API until it hit the wall. The fix has two layers:

**Pre-check before the loop.** Before iterating over commits, check the remaining quota. If fewer than 100 calls remain, skip diff stats for this run entirely:

```python
fetch_diff_stats = True
try:
    rl = self.client.check_rate_limit()
    if rl["remaining"] < 100:
        fetch_diff_stats = False
except Exception:
    pass  # optimistic if the check itself fails
```

**Periodic re-check inside the loop.** Even with a healthy quota at the start, a long history can exhaust it mid-loop. Re-checking every 50 diff-stat calls catches this before hitting the wall:

```python
diff_calls += 1
if diff_calls % 50 == 0:
    rl = self.client.check_rate_limit()
    if rl["remaining"] < 100:
        fetch_diff_stats = False
```

When diff stats are skipped, commits are stored without `additions`/`deletions`. The next `refresh` picks up where this left off — the existing `existing_with_stats` set skips commits already stored with non-null stats, so partial fetches accumulate correctly across runs. Running `project-explorer refresh <slug>` when the rate limit has reset fills in the missing diff stats without re-fetching commit metadata.

---

## Phase 7: Observability

### Why Observability Matters for AI Systems

For conventional software, logging and metrics are for debugging. For AI systems, they serve an additional purpose: understanding *why* users got the answer they got. Without observability, you cannot answer questions like:

- What fraction of queries hit the cache versus the LLM?
- Which intents have the highest latency?
- Where did retrieval fail (low scores, empty results)?
- What did users give negative feedback on?

We wired up two observability backends: MLflow for experiment tracking and Arize Phoenix for LLM tracing.

### Arize Phoenix for LLM Tracing

[Arize Phoenix](https://phoenix.arize.com/) is an open-source LLM observability platform that captures traces of every LLM call — including the prompt, the retrieved context, the generated output, and latency. It runs locally at `localhost:6006`.

Phoenix integrates with BeeAI via OpenTelemetry instrumentation:

```python
from openinference.instrumentation.beeai import BeeAIInstrumentor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

provider = TracerProvider()
provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint="http://localhost:6006/v1/traces"))
)
BeeAIInstrumentor().instrument(tracer_provider=provider)
```

After this, every BeeAI agent call — including each tool invocation — appears as a span in the Phoenix UI. You can see which tools were called, in what order, and what they returned.

### MLflow for Experiment Tracking

[MLflow](https://mlflow.org/) tracks query-level metrics: intent, latency, cache hit rate, chunk references, and feedback scores. This is logged asynchronously so it never adds latency to the response path:

```python
def _track(self, query, intent, project_slug, response, latency_ms, cache_hit, chunk_refs):
    try:
        self.metrics.record_query(
            query, intent.value, project_slug, response,
            latency_ms=latency_ms, cache_hit=cache_hit, chunk_refs=chunk_refs,
        )
    except Exception:
        pass  # observability must never crash the main path
```

The `threading.Thread(target=self._track, ..., daemon=True).start()` pattern in `RAGSystem.query()` fires-and-forgets the tracking call. The `daemon=True` flag ensures the thread does not prevent process exit.

The `try/except: pass` in `_track` is intentional and important: observability infrastructure going down must not take down the query path.

---

## Phase 8: Production Hardening

### Query Caching

The highest-ROI performance optimization is caching repeated queries. `QueryCache` sits at the front of `RAGSystem.query()`, before classification and before any database or LLM calls:

```python
def query(self, query: str, project_slug: str | None = None) -> str:
    intent = self.processor.classify(query)
    cached = self.cache.get(query, project_slug, intent.value)
    if cached:
        threading.Thread(target=self._track, ..., daemon=True).start()
        return cached                # ← returns in microseconds
    # ... full pipeline
```

The default backend is an in-memory LRU (`max_size=1000`, `ttl=3600s`). For multi-process deployments (multiple uvicorn workers, CLI + web running simultaneously), switch to the Redis backend:

```bash
CACHE__BACKEND=redis
CACHE__REDIS_URL=redis://localhost:6379/0
```

The Redis backend uses sets keyed as `pe:project:{slug}:keys` to track all cache entries for a project, enabling efficient per-project cache invalidation on `refresh`.

Cache invalidation is one of two hard problems in computer science, and here it is straightforward: when a project's index is refreshed, invalidate all cache entries tagged to that project slug. Everything else expires by TTL.

### Incremental Indexing

Re-ingesting an entire project on every code change is too slow. `IncrementalIndexer` uses the GitHub commit API to find what changed:

```python
def refresh(self, project: Project) -> None:
    repo = self.client.get_repo(project.github_url)
    latest_sha = self.client.get_latest_commit_sha(repo)
    last_sha = self._get_last_sha(project.slug)

    if last_sha == latest_sha:
        print(f"No changes since last index ({latest_sha[:8]})")
        return

    changed_files = self._get_changed_files(repo, last_sha, latest_sha)
    # Map changed file extensions → affected collection types
    affected_types = self._affected_collections(changed_files)
    # Drop and re-ingest only the affected collections
    for ctype in affected_types:
        self.store.drop_collection(f"{project.slug}_{ctype.name}")
    IngestionPipeline().run(project.slug, project.github_url, affected_types)
```

Re-indexing happens at **collection granularity**, not per-file. Milvus does not support efficient delete-by-metadata-filter on this schema, and per-file upsert would require tracking chunk-to-file mappings across schema versions. Dropping and re-ingesting a single collection (typically a few thousand chunks) completes in under 30 seconds — fast enough for a manual `refresh` command.

### Session Memory for the Web UI

The CLI and TUI naturally maintain state across a session (they are long-running processes). The web UI is stateless by default — each HTTP request creates a fresh `RAGSystem` with no memory of prior turns.

The fix is a server-side session store keyed to a browser-generated UUID:

**Frontend (index.html):**
```javascript
// Generate once, persist across page reloads
let sessionId = localStorage.getItem('pe_session_id');
if (!sessionId) {
    sessionId = crypto.randomUUID();
    localStorage.setItem('pe_session_id', sessionId);
}

// Send with every request
fetch('/api/query/stream', {
    method: 'POST',
    body: JSON.stringify({ query, project_slug, session_id: sessionId }),
});
```

**Backend (query.py):**
```python
_SESSION_TTL = 1800   # 30 minutes
_SESSION_MAX = 50     # max concurrent sessions
_sessions: dict[str, tuple] = {}

def _get_or_create_session(session_id: str, project_slug: str | None):
    now = time.monotonic()
    # Evict expired
    expired = [sid for sid, (_, ts) in _sessions.items() if now - ts > _SESSION_TTL]
    for sid in expired:
        del _sessions[sid]
    # Evict oldest when at capacity
    if len(_sessions) >= _SESSION_MAX and session_id not in _sessions:
        oldest = min(_sessions, key=lambda sid: _sessions[sid][1])
        del _sessions[oldest]
    # Create or retrieve
    if session_id not in _sessions:
        _sessions[session_id] = (ConversationAgent(project_slug=project_slug), now)
    else:
        agent, _ = _sessions[session_id]
        _sessions[session_id] = (agent, now)
    return _sessions[session_id][0]
```

`ConversationAgent` wraps a **single persistent `RequirementAgent`** with `TokenMemory(max_tokens=8000)`. The same BeeAI agent object is reused across all requests in a session — BeeAI's `TokenMemory` accumulates the full conversation history automatically.

No login, no cookies, no database for sessions. The UUID in `localStorage` is the session key. This is deliberately simple: the session store lives in a single dict in the FastAPI process. For a multi-worker deployment, move it to Redis (same approach as the query cache).

---

## Phase 9: A2A Agent Interoperability with AgentStack

### What A2A Adds

The interfaces built so far (CLI, TUI, web) are user-facing. A2A (Agent-to-Agent) exposes the same agents as programmatic services that other agents and orchestrators can discover and call via a standard protocol. This is the foundation of multi-agent systems where agents delegate to specialists.

[AgentStack SDK](https://agentstack.sh) provides the A2A `Server` abstraction. Each specialist agent runs as its own server:

```python
from agentstack.sdk import Server

def make_stats_server(port: int) -> Server:
    server = Server(
        name="Project Explorer — Statistics",
        description="GitHub project stats, contributor rankings, commit trends",
        port=port,
    )

    @server.skill(name="project_stats", description="...")
    async def project_stats_skill(input: str, context) -> AsyncIterator[str]:
        agent = StatsAgent()
        slug = agent._infer_project_slug(input)
        if not slug:
            # Pause and ask the client for a project name
            yield TaskStatus(state=TaskState.input_required,
                             message="Which project? Available: " + list_slugs())
            # Resume when client replies
            reply = await context.wait_for_input()
            slug = reply.strip()
        yield agent.handle(input, project_slug=slug)

    return server
```

The `input_required` pattern is the A2A equivalent of the TUI's clarification flow: the agent pauses execution, sends a question to the client, and resumes with the client's reply. This lets the Stats and Health agents maintain a proper request/response cycle without requiring the project slug upfront.

### One Server Per Agent

AgentStack's `Server` supports exactly one agent per instance. Running all six agents requires starting six servers and `asyncio.gather()`-ing them:

```python
async def serve_all(base_port: int = 8100) -> None:
    servers = [
        make_orchestrator_server(base_port),
        make_stats_server(base_port + 1),
        make_code_server(base_port + 2),
        make_doc_server(base_port + 3),
        make_health_server(base_port + 4),
        make_compare_server(base_port + 5),
    ]
    await asyncio.gather(*[s.start() for s in servers])
```

We defaulted to port 8100 rather than 8080 because 8080 is a common default for other local services (Docker, HTTP proxies, other development servers). Shifting to 8100 avoids surprise port conflicts in typical development environments.

---

## Phase 10: Accurate Ingestion Metrics and Sub-Project Indexing

### The Problem With Estimated Statistics

After Phase 6, `project_stats` held file counts and lines-of-code estimates computed from GitHub API data — specifically, a bytes-per-line lookup table that applied different multipliers per file extension. The estimates were consistently wrong for repos with mixed content: a Python project with large test fixtures might show 15 000 lines when it had 3 000, or vice versa for repos with long auto-generated files.

The fix is simple: count the actual files and actual newlines during ingestion, when we already have every file on disk.

### Counting During Ingestion

`IngestionPipeline.run()` now calls `_count_repo_stats()` after processing all collections:

```python
def _count_repo_stats(local_root: Path) -> tuple[int, int]:
    """Walk repo and return (file_count, lines_of_code)."""
    file_count = 0
    loc = 0
    for path in local_root.rglob("*"):
        if path.is_file() and path.suffix in _TEXT_SUFFIXES:
            file_count += 1
            try:
                loc += path.read_bytes().count(b"\n")
            except OSError:
                pass
    return file_count, loc
```

The result is stored in two new columns — `ingestion_file_count` and `ingestion_lines_of_code` — alongside the old GitHub-estimated columns. Agents and the web UI prefer the ingestion-measured values when available, falling back to estimates for projects that have been added but not yet refreshed.

```bash
# Trigger a re-index to populate real counts
project-explorer refresh <slug>

# Confirm accurate numbers appear
project-explorer list --details
```

The web stats endpoint (`/api/stats/{slug}`) now returns a `file_count_exact` boolean, so the UI can display "(estimated)" when showing pre-ingestion data.

### GitHub Tree Truncation and the GitHub-Estimated File Count

Before Phase 10, `file_count` in `project_stats` came from `get_git_tree(recursive=True)` — a single GitHub API call that returns every file path in the repo. This appears to work but silently fails for large repos: GitHub truncates the response when the total number of tree nodes (files *plus* directory objects) exceeds roughly 100,000, and sets `tree.truncated = True` in the response. A large Java monorepo like Egeria, with 28,000+ source files and thousands of nested `com/example/deep/package/` directory entries, easily exceeds this limit. Without checking `tree.truncated`, the count stops wherever the truncated response ended — typically 4–6× below the actual file count.

The fix is to detect the truncated flag and, when set, fetch the root **non-recursively** before walking:

```python
tree = repo.get_git_tree(repo.default_branch, recursive=True)
if not tree.truncated:
    return sum(1 for e in tree.tree if e.type == "blob")
# Truncated response is cut off mid-traversal — its entry list is incomplete.
# Fetch root non-recursively to get ALL top-level entries, then walk each subtree.
root = repo.get_git_tree(repo.default_branch, recursive=False)
return _count_files_walk(repo, root)
```

The critical detail is that the truncated recursive tree **cannot be used as the starting point** for the walk. GitHub fills the response in traversal order and stops at the size limit — directories appearing later in the listing are silently absent from `tree.tree`. Passing that partial list to `_count_files_walk` would miss everything after the cutoff. Fetching the root non-recursively always returns a complete list of direct children (the root directory has far fewer than 100k direct entries), giving a reliable starting set. `_count_files_walk` then fetches each top-level directory with `recursive=True`, and those per-directory responses are almost never truncated. For Egeria this means roughly 30 API calls instead of one, yielding an accurate count.

The same fix was applied to `GitHubClient.list_files()`, which `RepoAnalyzer` uses during onboarding to decide which collection types to create.

Note that `ingestion_file_count` (counted directly from the locally-extracted zipball) remains the preferred value for display and is always accurate. The GitHub-estimated `file_count` in `project_stats` serves as the pre-ingestion baseline, and its accuracy for large repos is now greatly improved.

### Sub-Project Indexing for Monorepos

Some repositories contain multiple independent sub-projects in separate subdirectories. Egeria, for example, contains `pyegeria` (the core library), `commands/` (the `hey_egeria` CLI), and `md_processing/` (the `dr_egeria` document tool). A single index of the full repo mixes all three vocabularies, degrading retrieval quality for targeted questions.

The `--subpath` and `--name` flags let you register each sub-project separately:

```bash
# Register the full repo first (optional but provides a parent reference)
project-explorer add https://github.com/odpi/pyegeria

# Register each sub-project as a distinct, independently searchable project
project-explorer add https://github.com/odpi/pyegeria \
    --subpath commands \
    --name hey_egeria

project-explorer add https://github.com/odpi/pyegeria \
    --subpath md_processing \
    --name dr_egeria

# Each sub-project appears independently in the list
project-explorer list --details

# Query each sub-project in isolation
project-explorer ask --project hey_egeria "What CLI commands are available?"
project-explorer ask --project dr_egeria "How does document processing work?"
```

`--name` is required when `--subpath` is given — without it there is no way to derive an unambiguous slug when multiple sub-projects share the same parent URL.

Internally, when only `--subpath` is given (no `--extra-docs-path`), `GitHubClient.download_zipball()` downloads the full repo zipball and returns `repo_root / subproject_path` directly. All downstream ingestion code walks whatever root path it receives, so code collections see only the subproject files.

The `Project` dataclass stores `subproject_path`, `parent_slug`, and `extra_docs_paths` for use by ingestion and refresh.

### Docs and Examples Outside the Code Subpath

Some repositories keep documentation and examples at the repo root, shared across multiple sub-projects. For example, `egeria-python` stores its API guide at `docs/user_programming.md` and its Jupyter notebooks in `examples/`, both outside the `pyegeria/` subdir. Without extra coverage, those files are invisible to the `pyegeria` project index — `--subpath` scopes the download to just that subdirectory.

`--extra-docs-path` specifies additional repo-relative paths to ingest into doc/example collections alongside the code from `--subpath`. Each path can be a file or a directory; repeat the flag for multiple paths:

```bash
project-explorer add https://github.com/odpi/egeria-python \
  --subpath pyegeria \
  --name pyegeria \
  --extra-docs-path docs/user_programming.md \
  --extra-docs-path examples/
```

Internally, when `--extra-docs-path` is set the pipeline downloads the **full** repo (not the subpath-filtered zipball). It then sets `code_root = repo_root / subpath` for code collections (`python_code`, `go_code`, etc.), and additionally walks the extra paths when ingesting doc/example collections (`markdown_docs`, `examples`, `api_reference`, `pdfs`). The paths are stored in the `Project` registry entry and re-applied automatically on every `refresh`.

The flag is silently ignored without `--subpath` — when the full repo is already the code root, every path within it is already covered.

The ingestion pipeline was refactored to support this cleanly. Two helpers were extracted from `run()`:

- `_setup_roots(full_root, subproject_path, extra_docs_paths)` — derives `code_root` and `resolved_extra` (list of `(display_prefix, abs_path)` tuples) from a repo root, whether that root came from a local clone or a download
- `_ingest_from_root(project_slug, repo, collection_types, code_root, resolved_extra, active_collections)` — runs the ingestion loop and returns `(file_count, loc)`

Extracting these helpers also enables the next feature.

### Avoiding Repeated Downloads with --from-local

Each `project-explorer add` call downloads the repo from GitHub — a full-repo zipball when `--extra-docs-path` is set, or a subpath-filtered zipball otherwise. Registering four sub-projects from a large monorepo triggers four separate downloads, which is slow and burns GitHub API bandwidth.

`--from-local` points to an existing local clone and skips the download entirely:

```bash
# Clone once
git clone https://github.com/odpi/egeria-python /tmp/egeria-python

# Register all four sub-projects using the same local clone
project-explorer add https://github.com/odpi/egeria-python \
  --subpath pyegeria --name pyegeria \
  --extra-docs-path docs/user_programming.md \
  --from-local /tmp/egeria-python

project-explorer add https://github.com/odpi/egeria-python \
  --subpath md_processing --name dr_egeria \
  --extra-docs-path docs/dr_egeria_manual.md \
  --from-local /tmp/egeria-python

project-explorer add https://github.com/odpi/egeria-python \
  --subpath commands --name hey_egeria \
  --from-local /tmp/egeria-python

project-explorer add https://github.com/odpi/egeria-python \
  --subpath pyegeria/view --name egeria_views \
  --extra-docs-path docs/output-formats-and-report-specs.md \
  --from-local /tmp/egeria-python
```

The GitHub URL is still stored in the registry for stats fetching, incremental refresh, and webhook events. `--from-local` is purely a download shortcut for the initial `add` — it has no effect on `refresh`, which always re-downloads from GitHub to pick up new commits.

`--from-local` is also useful outside the monorepo context: you can index a work-in-progress branch before pushing it, or index a repo you have locally for offline use.

---

## Phase 11: Event-Driven Refresh via GitHub Webhook

### The Problem With Manual Refresh

After Phase 8, keeping a project index current required running `project-explorer refresh <slug>` by hand. For active repos with dozens of commits per day, this means stale data unless the user remembers to refresh.

The fix is a GitHub Push webhook: GitHub posts a notification to your server every time someone pushes to the repo. The server re-indexes automatically.

### The Webhook Endpoint

`explorer/web/routes/webhook.py` exposes `POST /api/webhook/github`. The implementation has two concerns: security (verify the request actually came from GitHub) and idempotency (trigger refresh exactly once per push, even if GitHub retries).

```python
@router.post("/webhook/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str = Header(default=""),
) -> dict:
    body = await request.body()
    secret = get_config().github.webhook_secret
    if secret:
        if not _verify_signature(body, x_hub_signature_256, secret):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
    if x_github_event != "push":
        return {"status": "ignored", "event": x_github_event}
    data = json.loads(body)
    repo_url = data.get("repository", {}).get("html_url", "")
    project = ProjectRegistry().get_by_github_url(repo_url)
    if not project:
        return {"status": "unregistered", "url": repo_url}
    background_tasks.add_task(_do_refresh, project.slug)
    return {"status": "refresh_scheduled", "slug": project.slug}
```

Signature verification uses HMAC-SHA256:

```python
def _verify_signature(body: bytes, header: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header)
```

When no secret is configured, verification is skipped — this allows unauthenticated testing in development. In production, always set the secret.

For unknown repos, the endpoint returns `{"status": "unregistered"}` with HTTP 200. GitHub retries on non-2xx responses, so returning 404 for an unregistered repo would cause unnecessary retries.

The refresh runs as a FastAPI `BackgroundTask`, which completes after the HTTP response is sent. GitHub does not wait for the re-index to complete before considering the webhook delivered.

### Configuration

Add the webhook secret to your `.env`:

```bash
GITHUB_WEBHOOK_SECRET=your_secret_here
```

In the target repo's GitHub settings (Settings → Webhooks → Add webhook):
- **Payload URL:** `https://your-host/api/webhook/github`
- **Content type:** `application/json`
- **Secret:** same value as `GITHUB_WEBHOOK_SECRET`
- **Which events:** Push events only

**Testing locally:**

```bash
# Start the web server
project-explorer web

# Simulate a push event (replace the HMAC with a real computed value or omit if no secret is set)
curl -X POST http://localhost:8000/api/webhook/github \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: push" \
  -d '{"repository":{"html_url":"https://github.com/owner/repo"}}'
```

---

## Phase 12: Persistent Conversation History

### The Problem With In-Process Session State

Phase 8 stored web UI sessions in a server-side dict. When the FastAPI server restarted, all conversation history was lost — users had to repeat context they had already provided. The CLI `chat` command had no persistence at all: quitting and restarting produced a fresh session with no memory of prior turns.

The fix is to persist each turn to SQLite as it happens, and reload the history when a session is reconnected.

### The SQLite Schema

`conversation_history` stores each turn with a session ID and turn index:

```sql
CREATE TABLE conversation_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    turn_idx   INTEGER NOT NULL,
    role       TEXT NOT NULL,       -- 'user' | 'assistant'
    content    TEXT NOT NULL,
    project_slug TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_conv_session ON conversation_history(session_id, turn_idx);
```

`Registry.append_turn()` writes each turn after it completes. `Registry.load_turns()` retrieves the last N turns for a session, ordered by `turn_idx`. Both are called by every code path that creates or continues a conversation.

### Rehydrating BeeAI Memory

`ConversationAgent.load_history()` takes the list of saved turns and injects them into BeeAI's `TokenMemory`:

```python
async def _load_async(self, turns: list[dict]) -> None:
    agent = await self._ensure_agent()
    for turn in turns:
        if turn["role"] == "user":
            await agent.memory.add(UserMessage(content=turn["content"]))
        else:
            await agent.memory.add(AssistantMessage(content=turn["content"]))
```

This is called once when a session is first created on the server side. Subsequent turns in the session flow normally through BeeAI's `TokenMemory` accumulation — only the reconnect case requires explicit injection.

### CLI Usage

The `chat` command accepts an optional `--session-id` flag:

```bash
# Start a new session (prints the generated session ID at startup)
project-explorer chat
# Session: 7f3a91b2-...

# Ctrl-C to quit, then resume later with the same session ID
project-explorer chat --session-id 7f3a91b2-...
# Resumed session 7f3a91b2-... (12 turns)
```

The session ID is a UUID that acts as the only credential for the session — keep it if you want to resume, discard it to start fresh.

### Web UI

The web UI already generates a session UUID via `crypto.randomUUID()` and stores it in `localStorage`. After this change, the server loads prior turns from SQLite when a new `ConversationAgent` is created for that UUID. A user can clear browser storage to start a fresh session, or return after restarting the server and find their conversation intact.

---

## Phase 13: AST-Based Code Chunking

### The Problem With Fixed-Window Chunking

Phase 2's `_fixed_window()` splits source code into overlapping windows of N tokens. It has no awareness of function or class boundaries — a 512-token window might start in the middle of a method signature and end halfway through the method body. Retrieval then returns partial function definitions that are hard to interpret and embed poorly.

The consequence shows up in code-specific queries: "how is the X method implemented?" returns chunks that contain only part of the method, and the answer is incomplete or contradictory.

### tree-sitter for Boundary-Aware Chunking

[tree-sitter](https://tree-sitter.github.io/tree-sitter/) is a parser generator that produces concrete syntax trees for dozens of programming languages. We use it to split code at natural boundaries — function definitions, class definitions, method bodies — instead of at arbitrary token counts.

The optional `ast` extra installs tree-sitter with grammars for Python, JavaScript, Go, and Java:

```bash
uv sync --extra ast
```

`ASTChunker.chunk()` parses the source file, walks the top-level AST nodes, and produces boundary-aligned chunks:

1. Collect top-level nodes (function/class definitions for the language)
2. If a node fits within `max_tokens`, emit it as a chunk
3. If a node is too large, split it at inner boundaries (methods within a class)
4. If a segment is too small, merge it with the next one using overlap prefix

Each chunk gets a header comment with the filename and start line:

```python
# explorer/agents/tools.py:42
def vector_search(query: str, collection_names: str) -> str:
    ...
```

This header is embedded with the chunk and appears in retrieval results, letting the model cite the exact location of the code it is referencing.

`CodeParser._split()` tries the AST chunker first and falls back to `_fixed_window()` if tree-sitter is not installed or the language is unsupported:

```python
def _split(self, content: str, language: str, chunk_size: int, overlap: int) -> list[str]:
    try:
        from explorer.ingestion.ast_chunker import ASTChunker
        if ASTChunker.is_available() and language in ASTChunker.SUPPORTED_LANGUAGES:
            return ASTChunker().chunk(content, language, chunk_size, overlap)
    except Exception:
        pass
    return self._fixed_window(content, chunk_size, overlap)
```

This means users without tree-sitter see no change in behavior. Users with it get better chunking transparently after the next `refresh`.

**Verification:**

```bash
# Install AST extras
uv sync --extra ast

# Re-index to apply the new chunker
project-explorer refresh <slug>

# Submit a targeted code question and inspect the chunks in Phoenix traces
# Phoenix: http://localhost:6006 → Traces → expand the vector_search tool call
project-explorer ask --project <slug> "How is the authentication middleware implemented?"
```

---

## Phase 14: Dependency Graph Agent

### Why a Dedicated Dependency Agent

Code, documentation, and statistics each have specialized agents tuned for their data source. Dependency information — which packages a project requires, which version, which type (runtime vs. dev) — falls into none of those categories. Sending "what does pyegeria depend on?" through general RAG would return README snippets mentioning package names, not a structured list of actual declared dependencies.

The dependency agent follows the same pattern as the stats agent: parse structured data during ingestion, store it in SQLite, answer queries directly without touching Milvus.

### Dependency Parsing During Ingestion

`DependencyParser.parse(local_root, project_slug)` scans the repository root for manifest files and returns a list of dependency records:

| File | Ecosystem | Parser |
|---|---|---|
| `pyproject.toml` | Python | `tomllib` (3.11+) or `tomli`; handles PEP 517, Poetry |
| `requirements*.txt` | Python | Line-by-line; infers dev from filename |
| `setup.py` | Python | Regex on `install_requires=[...]` |
| `package.json` | JavaScript | JSON parse; `dependencies`, `devDependencies`, `peerDependencies` |
| `go.mod` | Go | Line-by-line `require` block; marks indirect |
| `pom.xml` | Java | `xml.etree.ElementTree`; `<dependency>` elements |

Each record has: `dep_name`, `dep_version`, `dep_type` (runtime/dev/optional/build), `ecosystem`, and `source_file`. Records are deduplicated by `(dep_name, ecosystem, source_file)` before being stored.

The pipeline calls `DependencyParser` at the end of ingestion, so dependencies are always current after a `refresh`.

### The Dependency Agent

`DependencyAgent` handles the `DEPENDENCY` intent. It queries `project_dependencies` directly via `Registry.query_dependencies()`:

```bash
# After refresh, ask dependency questions
project-explorer ask --project pyegeria "What Python packages does this project require?"
project-explorer ask "What are the runtime dependencies of hey_egeria?"

# Cross-project shared dependency query
project-explorer ask "Which projects use pandas?"
project-explorer ask "What packages do pyegeria and unitycatalog share?"
```

`query_dependencies` is a BeeAI `@tool` that returns results as a markdown table:

```
| Package    | Version  | Type    | Ecosystem |
|------------|----------|---------|-----------|
| requests   | >=2.28   | runtime | python    |
| pytest     | >=7.0    | dev     | python    |
| pandas     | >=2.0    | runtime | python    |
```

For cross-project queries, `Registry.query_shared_dependencies()` does a self-join on `project_dependencies` and returns packages that appear in multiple project indexes.

### Routing

`QueryIntent.DEPENDENCY` was added to the intent enum and placed first in the priority order (above `INTEGRATION`) so that "what packages does X require?" does not fall through to the integration agent. Patterns in `config/routing.yaml` match a wide range of phrasings:

```yaml
dependency:
  priority: HIGH
  patterns:
    - 'go\.mod|pyproject\.toml|package\.json|requirements\.txt'
    - '(list|show|what are).{0,30}(dep|package|librar|requirement)'
    - 'what.{0,40}(use|require|depend on|import).{0,20}(package|librar|dep|module)'
    - '(runtime|dev|optional|indirect)\s+dep'
    - 'which projects?.{0,20}(use|depend on|require|import)'
```

**Verification:**

```bash
# Re-index to parse dependency manifests
project-explorer refresh <slug>

# Confirm dependencies were stored
uv run python -c "
from explorer.registry import ProjectRegistry
deps = ProjectRegistry().query_dependencies('<slug>')
print(f'{len(deps)} deps indexed')
print(deps[:3])
"

# Ask natural-language questions
project-explorer ask --project <slug> "What are the runtime dependencies?"
```

---

## Phase 15: Retrieval Evaluation Harness

### Why a Formal Eval Harness

After every code change, there is a risk of retrieval regressions: a routing pattern that was too broad, a chunker change that degraded semantic similarity, or a prompt change that stopped including relevant context. Without a repeatable quality metric, regressions are invisible until a user notices.

The eval harness provides a `--min-recall` gate: if mean keyword recall drops below threshold, the eval script exits non-zero and the problem is visible immediately.

### The Golden Dataset

`tests/fixtures/golden_qa.json` contains 25 query/answer pairs covering all intent types:

```json
[
  {
    "query": "Who are the top contributors?",
    "intent": "statistical",
    "expected_keywords": ["contributor", "commit"],
    "notes": "Requires project_commits data"
  },
  {
    "query": "What are the runtime dependencies?",
    "intent": "dependency",
    "expected_keywords": ["runtime", "depend", "package"],
    "notes": "Requires dependency data indexed"
  }
]
```

Each entry defines:
- `query` — the natural-language question to ask
- `intent` — the expected classified intent (used to measure `intent_accuracy`)
- `expected_keywords` — words that must appear in the response for it to be considered a hit
- `notes` — which data must be indexed for the query to be answerable

`keyword_recall` is the fraction of expected keywords found in the response (case-insensitive). This is a weak but fast signal: if the right keywords are present, the system at least retrieved something relevant. Strong recall but wrong answer is a synthesis failure, not a retrieval failure — useful to distinguish.

### Running the Eval

```bash
# Run against a specific indexed project
uv run python scripts/eval_retrieval.py --project <slug>

# Tighten the threshold for critical paths
uv run python scripts/eval_retrieval.py --project <slug> --min-recall 0.80

# Skip MLflow logging (useful in CI without an MLflow server)
uv run python scripts/eval_retrieval.py --project <slug> --no-mlflow

# Use a custom dataset
uv run python scripts/eval_retrieval.py --project <slug> --golden path/to/my_qa.json
```

Output:

```
Running 25 eval queries scoped to 'pyegeria'...

   1. [✓] Who are the top contributors?            recall=100%  312ms  intent=statistical
   2. [✓] Show me the commit activity over the ...  recall=100%  287ms  intent=statistical
   3. [✗] How many forks does this project have?   recall= 67%  298ms  intent=statistical
   ...

======================================================================
  Queries:          25
  Mean recall:      82.3%  (threshold: 70.0%)
  Intent accuracy:  96.0%
  Mean latency:     341ms
======================================================================

PASS
```

### MLflow Integration

`MetricsCollector.record_query()` now calls `mlflow_tracking.log_query()` at the end of every query (in the existing daemon thread, so latency is unaffected). The eval script logs a dedicated run to the `project-explorer-eval` MLflow experiment:

```bash
# Start MLflow server (required for logging)
mlflow server --port 5025

# Run eval with logging enabled (default)
uv run python scripts/eval_retrieval.py --project <slug>

# Open the MLflow UI and inspect the eval run
open http://localhost:5025
```

Each eval run records `mean_keyword_recall`, `mean_latency_ms`, and `intent_accuracy` as summary metrics, plus per-query `keyword_recall` and `latency_ms` as step metrics. Comparing runs before and after a routing change makes regressions immediately visible in the MLflow comparison view.

---

## Phase 16: Python Examples Agent

### Why Example Generation Is a Different Problem Than Code Search

The `CodeAgent` (Phase 4) finds existing code in indexed collections — it retrieves and surfaces what is already there. Example generation is the inverse: it produces new, complete, runnable code that does not exist in the index, using retrieved content as grounding. Routing example requests through `CodeAgent` produces partial matches — snippets of real code that aren't complete programs — or through general RAG, which produces prose descriptions of how to write code rather than the code itself.

The distinction matters because:
- An LLM that retrieves a half-implementation of a method will complete the rest by hallucinating plausible but wrong arguments and return types.
- A correctly-scoped system prompt that says "only use methods you found in context" and "never invent an API" changes the failure mode from silent hallucination to explicit uncertainty.
- Example generation needs *breadth* of context (API docs, existing samples, import patterns, README usage) across multiple collections simultaneously, whereas code search needs *depth* in the most relevant single chunk.

### The `build_example_context` Tool

All existing agents call `vector_search(query, collection_names)` with explicit collection names. `ExamplesAgent` needs context from four different collection types simultaneously, and requiring the LLM to issue four separate `vector_search` calls wastes iterations and risks missing collections it doesn't think to try.

`build_example_context(project_slug, topic)` encapsulates this breadth search:

```python
@tool(description="Gather context for generating a Python code example...")
def build_example_context(project_slug: str, topic: str) -> str:
    collection_types = ["examples", "python_code", "api_reference", "markdown_docs"]
    candidate_collections = [f"{slug}_{ctype}" for ctype in collection_types]
    existing = [c for c in candidate_collections if client.has_collection(c)]

    searches = [
        (topic, existing),
        (f"import install setup {slug}", existing),
        ("usage example how to", [c for c in existing if "examples" in c or "markdown" in c]),
    ]
    # Deduplicate by text, threshold at score >= 0.3
    ...
```

Three targeted queries cover: the specific topic, setup/install patterns (so the example starts with correct imports), and narrative usage examples from README-style docs. Results are deduplicated and returned as a single formatted block.

The tool uses `client.has_collection(c)` to silently skip collections that haven't been indexed for this project — not every project has an `examples` collection, and missing ones should not cause errors.

### The System Prompt Approach

The key to reliable example generation is a system prompt with explicit negative constraints, not just positive instructions. The `ExamplesAgent` prompt includes:

- *"Only use class names, method names, and parameters you saw in the retrieved context."*
- *"Never invent API methods. If you cannot find the right API, say so and show the closest available alternative."*
- *"If a detail is unclear, use a clearly labelled placeholder like `YOUR_HOST` or `YOUR_TOKEN`."*

Without these constraints, the LLM completes gaps in its knowledge with plausible-but-wrong API calls, which is worse than an explicit "I'm not sure." LLMs have strong priors about how Python libraries tend to look, and those priors override retrieved evidence when the prompt doesn't explicitly forbid it.

The format requirement — fenced `python` block followed by a short explanation — separates the runnable artifact from the prose, making it easy for UIs and users to copy the code without parsing it out of surrounding text.

### Routing

`QueryIntent.EXAMPLES` sits between `health` and `code_search` in the priority order. Placing it above `code_search` ensures that "show me how to use X in Python" routes to `ExamplesAgent` rather than `CodeAgent`. The patterns are specific enough that ambiguous queries like "show me the code for X" (without language or "example" keywords) still fall through to `code_search`:

```yaml
examples:
  patterns:
    - 'generate (a |an |me )?(python )?(example|sample|snippet|code|demo)'
    - 'write (me )?(a |an )?(python )?(example|code|script|snippet|demo)'
    - '(show me|how do i|how to).{0,40}(in python|using python|with python)'
    - 'code (example|sample|snippet) (for|to|showing|that|using)'
    - 'working (code )?(example|demo|sample)'
    - 'example (of how to|showing how to|that demonstrates|for using)'
```

**Usage:**

```bash
# Generate a complete Python example
project-explorer ask --project egeria_python "write me a python example for connecting to Egeria and listing assets"
project-explorer ask --project hey_egeria "show me how to use the hey_egeria CLI programmatically in Python"

# Works through the web UI and chat too
project-explorer chat --project egeria_python
# > generate a working example that authenticates and queries the glossary
```

### The Fallback Path

When BeeAI fails (LLM timeout, tool error, max iterations reached), or when the agent loop completes but the LLM produces a text description instead of a runnable code block, `ExamplesAgent._fallback()` bypasses the agent loop and calls the underlying retrieval logic directly.

The gate that decides whether to use the BeeAI result or fall back is:

```python
response = self._run_agent(prompt)
if "```python" in response:   # only accept a real fenced code block
    return response
return self._fallback(query, slug)
```

Checking for ` ```python ` specifically (not just ` ``` `) prevents accepting responses where the LLM listed method names with inline backticks instead of writing code.

**BeeAI `FunctionTool` objects have no `.func` attribute** — you cannot call `my_tool.func(...)` to invoke the wrapped function. The correct pattern is to extract the implementation into a private `_raw` helper and have the `@tool` wrapper delegate to it:

```python
def _build_example_context_raw(project_slug: str, topic: str) -> str:
    # ... full implementation ...

@tool(description="...")
def build_example_context(project_slug: str, topic: str) -> str:
    return _build_example_context_raw(project_slug, topic)
```

The fallback imports and calls `_build_example_context_raw` and `_query_code_symbols_raw` directly — plain Python, no BeeAI involved.

### Context Hints

Small models (llama3.1:8b) frequently ignore the exact class names and constructor signatures in retrieved chunks, instead falling back to training-data guesses. Two hints are injected at the top of every context block to counteract this:

**Import hint** — `_build_example_context_raw` scans all retrieved text for capitalised identifiers used as constructors and emits a concrete import line:

```
IMPORT HINT: These classes come from the 'pyegeria' package.
  from pyegeria import GlossaryManager, GlossaryTermProperties, NewElementRequestBody
```

**Constructor hint** — the same function extracts the first concrete multi-argument instantiation for each class and presents the exact signature:

```
CONSTRUCTOR PATTERNS (use these exact signatures):
  GlossaryManager( self.view_server, self.platform_url, user_id=self.user, user_pwd=self.password, )
```

Both hints appear before the retrieved chunks so they are at the top of the LLM's context window. Without them, the LLM invents argument names (`server_url=`, `username=`) that don't match the real API.

### Indexing Test Files for Better Examples

The quality of generated examples depends on what's in the `examples` collection. Source code alone is not enough — it shows *what* the API does but not *how* to use it end-to-end. The functional and scenario tests in a project's test suite are often the best source of correct usage patterns: they show constructor arguments, authentication calls, cleanup, and realistic values.

For pyegeria, `tests/functional-tests/` and `tests/scenario-tests/` are indexed as extra docs paths alongside `examples/`:

```bash
project-explorer add https://github.com/odpi/egeria-python \
  --subpath pyegeria --name pyegeria \
  --extra-docs-path docs/user_programming.md \
  --extra-docs-path examples/ \
  --extra-docs-path tests/functional-tests/ \
  --extra-docs-path tests/scenario-tests/ \
  --from-local /path/to/egeria-python --yes
```

### Intent Routing for Example Queries

`routing.yaml` controls which queries reach `ExamplesAgent`. The `examples` intent must match before `code_search`, which also has patterns that would otherwise absorb "show me an example of X" and "how do I use X".

Patterns that now route to `ExamplesAgent`:

| Query form | Pattern |
|---|---|
| `write me a python example for X` | `write (me )?(a \|an )?(python )?example` |
| `example of X` / `example for X` | `(example\|sample\|demo) (of\|for) \w` |
| `show me an example of X` | `(show\|give) (me )?(an? )?(example\|sample\|demo) (of\|for) \w` |
| `how do I use X` / `how do I call X` | `how (do i\|to\|can i).{0,50}(use\|call\|invoke\|work with)` |
| `X example` (trailing) | `\w.{3,60} (example\|usage example\|demo)(\?)?$` |

The pattern — try the agent, fall back to a direct retrieval+LLM call using `_raw` helpers — applies to any agent where a partial answer is more useful than an exception.

### Extending to Other Languages

The agent is Python-first by design. The path to supporting other languages:

1. Add a `language` parameter to `ExamplesAgent.handle()` and thread it into the prompt.
2. In `build_example_context`, extend `collection_types` to include `javascript_code`, `java_code`, etc. based on the requested language.
3. Add language-detection patterns to `routing.yaml` (e.g., `"in javascript"`, `"using java"`).

No schema changes, no new agent class — the system prompt and collection list are the only language-specific pieces.

---

## Lessons Learned

### Classify Intent Before Retrieving

The most impactful architectural decision was classifying query intent before touching Milvus. Statistical queries with precise answers should never go through a vector similarity search — doing so opens the door to hallucination. The regex-over-YAML classifier is fast, auditable, and tunable without code changes.

### Cache Is the Highest-ROI Optimization

Retrieval + LLM generation takes 2–10 seconds. A cache hit takes microseconds. Implement caching before optimizing retrieval or generation. The key design choice is *where* to put the cache — before classification, so even the classifier is skipped on a hit.

### Data Consistency Requires a Single Source of Truth

The bug where "0 commits" and "5 committers" coexisted in the same response was caused by reading from two different tables. Designate one table as authoritative for each fact. `project_commits` is the source of truth for all commit counts; `project_stats` is a snapshot used only for fields that are not in `project_commits`.

### System Prompts Must Be Explicit About What Not to Do

LLMs will describe how to accomplish a task if they don't have a tool to accomplish it directly. Adding "never write code, never describe how to fetch data, always call the appropriate tool first" to the StatsAgent system prompt was what stopped the agent from generating Python tutorials in response to "graph commits per week."

### Incremental at Collection Granularity, Not File Granularity

Per-file incremental updates require tracking chunk-to-source-file mappings and supporting efficient delete-by-metadata in the vector store. Collection-level invalidation is simpler: detect which collection types were touched by changed files, drop those collections, re-ingest. For typical repos and change frequencies, collection-level granularity is fast enough.

### Suppress Library Noise Early and Explicitly

gRPC, HuggingFace transformers, and tqdm all print to stderr in ways that pollute CLI output and break TUI alternate-screen mode. Suppress them with environment variables at module load time — before any lazy import can pull in the libraries. Use `setdefault` so that a developer who sets `DEBUG=1` (or any of the individual vars) always wins:

```python
if not os.environ.get("DEBUG"):
    os.environ.setdefault("GRPC_VERBOSITY", "NONE")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("TQDM_DISABLE", "1")
```

This is one of those things that seems obvious in retrospect but causes a lot of confusion in testing — "why is the TUI printing 'Loading weights: 100%' to the screen?".

### Pre-Warm Heavy Dependencies in the Main Thread

PyTorch, gRPC (Milvus), and BeeAI all initialize global state (file descriptors, thread pools, event loops) when first used. If they initialize inside a framework thread (Textual worker, FastAPI startup), race conditions and FD conflicts occur. Force initialization in the main thread before starting any framework. Make each pre-warm step individually fault-tolerant — a failed model download should not prevent the TUI from launching.

### Observability Must Be Non-Blocking and Failure-Tolerant

Any synchronous call to an observability backend in the response path is a latency risk. Any unhandled exception from an observability call is a reliability risk. Dispatch observability to daemon threads; wrap `_track()` bodies in `try/except: pass`. The system must continue working if MLflow or Phoenix is unreachable.

### Streaming Requires Two Different Bridges

Synchronous code (LLM token generator) needs to reach asynchronous consumers (FastAPI SSE, Textual worker) through two different bridges:
- **FastAPI**: `asyncio.Queue` + `loop.call_soon_threadsafe()` — push from thread, pull in async generator
- **Textual**: `call_from_thread()` — call UI methods safely from a `@work(thread=True)` worker

Both patterns are short (5–10 lines each) but easy to get wrong. Test them early.

---

## The Full Stack, Summarized

| Problem | Tool | Why This Choice |
|---|---|---|
| Vector search | Milvus (pymilvus) | Lite mode for development, same API for production; COSINE similarity with FLAT index |
| Embeddings | sentence-transformers (all-MiniLM-L6-v2) | Local, no API key, MPS-accelerated, 384-dim is a good size/quality tradeoff |
| LLM inference | Ollama | Local, Metal GPU, no API costs, same interface as cloud |
| Cloud LLM | OpenAI / Anthropic | Pluggable via LLMBackend protocol |
| Agent framework | BeeAI Framework | RequirementAgent + @tool = iterative tool-calling with minimal boilerplate |
| A2A protocol | AgentStack SDK | Standard inter-agent discovery and communication |
| Document parsing | Docling | PDF, HTML, structured docs with layout analysis |
| AST parsing | tree-sitter (optional) | Boundary-aware code chunking; graceful fallback when not installed |
| Dependency parsing | stdlib (tomllib, json, xml, re) | Zero new deps; handles pyproject.toml, package.json, go.mod, pom.xml |
| CLI | Typer + Rich | Type-safe commands, zero boilerplate, beautiful output |
| TUI | Textual | Full-screen terminal UI, widget system, CSS layout |
| Web backend | FastAPI | Async, SSE streaming, typed request/response models, webhook endpoint |
| Web frontend | Tailwind + Plotly.js + marked.js | No build step, dark theme, interactive charts, markdown rendering |
| LLM tracing | Arize Phoenix | Per-call traces via OpenTelemetry, open source, runs locally |
| Experiment tracking | MLflow | Query metrics, latency, feedback logging, eval runs; open source |
| Caching | LRU (in-process) / Redis | LRU for single-process; Redis for multi-process deployments |
| Conversation persistence | SQLite (conversation_history) | Zero infrastructure; survives server restarts; CLI and web UI share the same store |
| Dependency storage | SQLite (project_dependencies) | Structured deps indexed during ingestion; fast cross-project shared-dep queries |
| Retrieval eval | scripts/eval_retrieval.py | Keyword recall scoring against a golden Q&A dataset; exits non-zero on regression |

Every component in this stack is open source. The only proprietary dependency is a GitHub token for API access — and unauthenticated access works for public repos at 60 requests/hour.

---

## Where to Go From Here

The natural next extensions, roughly in order of impact:

1. **Feedback-driven reranking** — the `MultiCollectionStore` already records feedback scores and `chunk_feedback` tracks per-chunk positive/total vote counts; train a lightweight reranker on thumbs-up/down signals to boost relevant chunks in future queries
2. **More collection types** — GitHub Issues, PR bodies, and discussion threads are high-signal content not yet indexed; each would follow the five-step extension process (collection type → file list → chunk config → route pattern → test)
3. **Integration query improvement** — the `IntegrationAgent` already exists; the intent patterns can be expanded and the system prompt tuned to better handle "how do I use X alongside Y?" compound questions that require understanding both projects simultaneously
4. **Async ingestion** — the `add` command blocks until ingestion completes; move it to a background job with progress polling via the web API, similar to how the webhook endpoint triggers a background refresh
5. **Milvus HNSW index** — when collection sizes exceed ~100k chunks, switch from `FLAT` to `HNSW` in `_ensure_collection()` for sub-linear search time; the schema change is isolated to `multi_collection_store.py`
6. **TUI stability** — the Textual TUI has known threading issues: `query_one()` called from worker threads, bare `except Exception` blocks that swallow errors, and asyncio/threading races in the memory update path; these need `call_from_thread()` throughout to match Textual's required threading model
7. **Session browser** — `Registry.list_sessions()` is already implemented; a web UI panel listing prior sessions with timestamps would let users return to a previous conversation without knowing the session UUID

The architecture is designed to support all of these without structural changes. New collection types follow the five-step extension process. New agents extend `BaseExplorerAgent`. New LLM backends implement the two-method `LLMBackend` protocol. New routing rules go in `routing.yaml`.
