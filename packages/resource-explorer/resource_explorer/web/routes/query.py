"""Query endpoint — POST a question, get a response."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter()
log = logging.getLogger(__name__)

# ── session store ──────────────────────────────────────────────────────────────
# Maps session_id → (ConversationAgent, last_used_timestamp)
# Capped at 50 sessions; idle sessions expire after 30 minutes.
_SESSION_TTL = 1800  # seconds
_SESSION_MAX = 50
_sessions: dict[str, tuple] = {}


def _get_or_create_session(session_id: str, resource_slug: str | None,
                            resource_type: str | None = None):
    """Return a ConversationAgent for this session, creating one if needed."""
    from resource_explorer.agents.conversation_agent import ConversationAgent

    now = time.monotonic()

    # Evict expired sessions
    expired = [sid for sid, (_, ts) in _sessions.items() if now - ts > _SESSION_TTL]
    for sid in expired:
        del _sessions[sid]

    # Evict oldest when at capacity
    if len(_sessions) >= _SESSION_MAX and session_id not in _sessions:
        oldest = min(_sessions, key=lambda sid: _sessions[sid][1])
        del _sessions[oldest]

    if session_id not in _sessions:
        agent = ConversationAgent(resource_slug=resource_slug, resource_type=resource_type or "repo")
        # So the compile it runs can be recorded against this session.
        agent.session_id = session_id
        # Hydrate memory from persisted history so context survives restarts
        try:
            from resource_explorer.registry import ProjectRegistry
            turns = ProjectRegistry().load_turns(session_id)
            if turns:
                agent.load_history(turns)
        except Exception:
            pass
        _sessions[session_id] = (agent, now)
    else:
        agent, _ = _sessions[session_id]
        _sessions[session_id] = (agent, now)
        if resource_slug:
            agent.resource_slug = resource_slug
        if resource_type:
            agent.resource_type = resource_type

    return agent


def _persist_turn(
    session_id: str, query: str, response: str, resource_slug: str | None,
    compile_id: str | None = None,
) -> None:
    """Both turns of an exchange, each pointing at the compile that shaped the
    answer (None when nothing was compiled — see registry's column comment)."""
    try:
        from resource_explorer.registry import ProjectRegistry
        registry = ProjectRegistry()
        registry.append_turn(session_id, "user", query, resource_slug, compile_id=compile_id)
        registry.append_turn(session_id, "assistant", response, resource_slug, compile_id=compile_id)
    except Exception:
        pass


def _compile_id_of(agent) -> str | None:
    compiled = getattr(agent, "_last_compiled", None)
    return getattr(compiled, "compile_id", None) or None


class QueryRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    query: str
    # alias keeps the JSON wire key as `project_slug` while the Python
    # vocabulary moves to `resource_slug`. A Pydantic field name IS the
    # wire key, so this is the one place the identifier rename and the
    # API contract are the same edit. index.html still sends the old
    # key; drop the alias when the wire format moves too.
    resource_slug: str | None = Field(default=None, alias="project_slug")
    database_slug: str | None = None  # NEW: for database-scoped queries
    #: 'repo' | 'database' | 'filesystem' — which resource type `resource_slug`
    #: names. Defaults to "repo" for backward compatibility with callers
    #: (e.g. the classic UI) that don't pass it. Threaded into
    #: ConversationAgent -> compile_context() so a database/filesystem-scoped
    #: chat question pulls that type's own catalog and analyses instead of
    #: always compiling against repo's.
    entity_type: str = "repo"
    #: True when the person has already answered "this question names another
    #: resource": they chose to keep the scoped one. The stream then skips the
    #: names-another-resource check and answers on the scope as asked.
    skip_resource_check: bool = False
    #: Active Perspective chips. Empty means no perspective filter, which is a
    #: real state and not a missing value -- the compile still runs.
    perspectives: list[str] = []
    session_id: str | None = None  # browser-generated UUID for cross-turn memory


class QueryResponse(BaseModel):
    response: str
    intent: str
    query_hash: str
    cached: bool = False
    chart: dict | None = None  # Plotly figure JSON when intent warrants a chart
    #: The CompiledContext (text/manifest/derivation) that shaped this answer,
    #: when a session/slug went through ConversationAgent._compiled_evidence.
    #: Same shape as POST /api/context/compile's response body -- one caller
    #: fewer for the frontend to special-case. None when there was no
    #: resource in scope, or the compile itself produced nothing.
    compiled: dict | None = None
    disambiguation_chips: list[dict] | None = None


def _compiled_payload(agent) -> dict | None:
    """Read the CompiledContext ConversationAgent.handle() just computed.

    Fail-soft and narrow: an agent that never ran _compiled_evidence (the
    RAGSystem fallback path, or the EXAMPLES intent's early return) simply
    has no attribute here, same as one that ran it and got nothing back.
    """
    compiled = getattr(agent, "_last_compiled", None)
    if compiled is None:
        return None
    return {"text": compiled.text, "manifest": compiled.manifest, "derivation": compiled.derivation}


class FeedbackRequest(BaseModel):
    query_hash: str
    #: The compile behind the answer being rated, from the done event's
    #: "compile_id" (or compiled.manifest.compile_id). Optional: a rating
    #: without it still lands, it just cannot say whether the evidence or the
    #: model was at fault (context-compilation-design.md §13).
    compile_id: str | None = None
    # +1 positive, 0 neutral/"partially correct", -1 negative -- same
    # convention EA uses (advisor/web/app.py:131-135). record_feedback()
    # scores these as three explicit states, not a sign test -- see
    # metrics_collector.py's record_feedback docstring.
    vote: int


def _pick_chart(query: str, intent: str, resource_slug: str) -> dict | None:
    """Return a Plotly figure as a JSON-serialisable dict, or None."""
    if not resource_slug:
        return None
    if intent not in ("statistical", "health", "comparison"):
        return None
    try:
        from resource_explorer.dashboard import graphs
        q = query.lower()

        if intent == "comparison":
            # Extract slugs from the query for a cross-project stats chart
            from resource_explorer.agents.compare_agent import CompareAgent
            slugs = CompareAgent()._infer_all_project_slugs(query)
            if len(slugs) >= 2:
                fig = graphs.compare_stats_plotly(slugs)
            else:
                return None

        elif intent == "health":
            fig = graphs.health_radar_plotly(resource_slug)

        elif any(w in q for w in ("committer", "contributor", "who commit", "who contribut", "top commit")):
            fig = graphs.top_committers_plotly(resource_slug)
            if fig is None:
                return None  # no data — don't show an empty chart

        elif any(w in q for w in ("star", "popular", "growth")):
            fig = graphs.stars_over_time_plotly(resource_slug)

        elif any(w in q for w in ("language", "breakdown", "written in", "code in")):
            fig = graphs.language_breakdown_plotly(resource_slug)

        elif any(w in q for w in ("week", "weekly", "per week", "week-by-week")):
            fig = graphs.weekly_commits_plotly(resource_slug)

        elif any(w in q for w in ("file", "files", "file type", "file count", "how many file")):
            fig = graphs.file_types_plotly(resource_slug)

        elif any(w in q for w in ("commit", "activity", "history", "how many commit")):
            fig = graphs.commits_over_time_plotly(resource_slug)

        else:
            return None

        return json.loads(fig.to_json())
    except Exception:
        return None


@router.post("/", response_model=QueryResponse)
async def ask(request: QueryRequest) -> QueryResponse:
    """POST a question, get a response.

    Both branches below are synchronous, potentially-slow code
    (ConversationAgent.handle / RAGSystem.query, each of which can run
    LLM calls and tool calls, including a Postgres vector_search) — they
    run off the event loop via asyncio.to_thread, never awaited directly
    on it. Before 2026-09-13 they were called straight from this `async
    def`, which is exactly how a single stuck vector_search connect froze
    every request the :8810 process was serving: this coroutine runs on
    the one uvicorn event-loop thread, and a synchronous call on that
    thread blocks the whole loop, not just this request.
    """
    from resource_explorer.query_processor import QueryProcessor
    intent = QueryProcessor().classify(request.query)
    query_hash = hashlib.sha256(request.query.encode()).hexdigest()[:16]

    compiled = None
    if request.session_id:
        agent = _get_or_create_session(request.session_id, request.resource_slug,
                                        request.entity_type)
        response = await asyncio.to_thread(
            agent.handle, request.query, resource_slug=request.resource_slug,
            resource_type=request.entity_type, perspectives=request.perspectives,
        )
        _persist_turn(request.session_id, request.query, response, request.resource_slug)
        compiled = _compiled_payload(agent)
    else:
        from resource_explorer.rag_system import RAGSystem
        response = await asyncio.to_thread(
            RAGSystem().query, request.query, resource_slug=request.resource_slug,
        )

    chart = _pick_chart(request.query, intent.value, request.resource_slug or "")
    chips = None
    if intent.value in ("comparison", "integration"):
        chips = _disambiguation_chips(request.query, request.resource_slug or request.database_slug)

    return QueryResponse(
        response=response,
        intent=intent.value,
        query_hash=query_hash,
        chart=chart,
        compiled=compiled,
        disambiguation_chips=chips,
    )


@router.post("/stream")
async def stream(request: QueryRequest) -> StreamingResponse:
    """
    SSE streaming variant of /api/query/.

    Yields newline-delimited JSON events:
      {"t":"chunk","v":"<text>"}   — one or more text chunks
      {"t":"done","intent":"...","hash":"...","chart":...,"compiled":...}  — terminal event
        "compiled" is the same {text, manifest, derivation} shape as
        POST /api/context/compile's response, sourced from the same compile
        ConversationAgent._compiled_evidence already did to shape this
        answer -- None when there was no resource in scope, or nothing
        compiled (the RAGSystem fallback path, no session_id).
    """
    def _sse(obj: dict) -> str:
        return f"data: {json.dumps(obj)}\n\n"

    async def _generate() -> AsyncIterator[str]:
        loop = asyncio.get_event_loop()

        # A scoped question that names a DIFFERENT resource is held, not answered:
        # an LLM answer about the scoped resource would be the wrong answer, and
        # switching silently would be a different wrong one. The person chooses.
        checked: dict | None = None
        if request.resource_slug and not request.skip_resource_check:
            checked = await asyncio.to_thread(
                _named_resource_check, request.query, request.resource_slug)
            if checked and ("resource_question" in checked or "alias_suggestion" in checked):
                if "resource_question" in checked:
                    rq = checked["resource_question"]
                    held_text = (f"This question names {rq['named_name']}, but this chat is on "
                                 f"{rq['scoped_slug']}. Choose which one to ask about.")
                else:
                    a = checked["alias_suggestion"]
                    held_text = (f"“{a['term']}” is not a known name. Did you mean {a['candidate_name']}? "
                                 f"This chat is on {a['scoped_slug']}.")
                yield _sse({"t": "chunk", "v": held_text})
                yield _sse({"t": "done", "intent": "", "hash": "", "cached": False, "chart": None,
                            "compiled": None, "compile_id": None, "held": True, **checked})
                return

        queue: asyncio.Queue = asyncio.Queue()

        def _producer() -> None:
            try:
                if request.session_id:
                    from resource_explorer.query_processor import QueryProcessor
                    intent = QueryProcessor().classify(request.query).value
                    agent = _get_or_create_session(request.session_id, request.resource_slug,
                                                    request.entity_type)
                    text = agent.handle(request.query, resource_slug=request.resource_slug,
                                        resource_type=request.entity_type,
                                        perspectives=request.perspectives)
                    compiled = _compiled_payload(agent)
                    _persist_turn(request.session_id, request.query, text, request.resource_slug,
                                  compile_id=_compile_id_of(agent))
                    loop.call_soon_threadsafe(queue.put_nowait, text)
                    loop.call_soon_threadsafe(queue.put_nowait, {"_done": True, "intent": intent, "hash": hashlib.sha256(request.query.encode()).hexdigest()[:16], "cached": False, "compiled": compiled})
                else:
                    from resource_explorer.rag_system import RAGSystem
                    rag = RAGSystem()
                    for item in rag.stream(request.query, resource_slug=request.resource_slug):
                        loop.call_soon_threadsafe(queue.put_nowait, item)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel

        import contextvars
        import threading
        # Run the producer under a COPY of the request's context, not a bare
        # thread: the signed-in caller lives in a ContextVar, and a plain
        # Thread starts with an empty one — so every conversation turn and
        # every compile recorded from this path was attributed to '' (the
        # service bucket) while the response itself was correctly scoped.
        # Found 2026-09-08 by reading the rows back after a live chat turn.
        ctx = contextvars.copy_context()
        threading.Thread(target=ctx.run, args=(_producer,), daemon=True).start()

        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, dict) and item.get("_done"):
                # Build the done event, attach chart if relevant
                chart = _pick_chart(
                    request.query, item.get("intent", ""), request.resource_slug or ""
                )
                intent_val = item.get("intent", "")
                done: dict = {
                    "t": "done",
                    "intent": intent_val,
                    "hash": item.get("hash", ""),
                    "cached": item.get("cached", False),
                    "chart": chart,
                    "compiled": item.get("compiled"),
                    "compile_id": ((item.get("compiled") or {}).get("manifest") or {}).get("compile_id"),
                }
                # Structured symbol table for code_inventory queries
                if intent_val == "code_inventory" and request.resource_slug:
                    done["symbol_table"] = _code_inventory_table(request.query, request.resource_slug)
                # Side-by-side symbol comparison for comparison queries about API surface
                if intent_val == "comparison":
                    done["compare_symbols"] = _compare_symbols_table(request.query)
                # Disambiguation chips for cross-resource queries with missing or ambiguous targets
                if intent_val in ("comparison", "integration"):
                    chips = _disambiguation_chips(request.query, request.resource_slug or request.database_slug)
                    if chips:
                        done["disambiguation_chips"] = chips
                if checked and "resource_check_error" in checked:
                    done["resource_check_error"] = checked["resource_check_error"]
                # Suggest an alias when no project was resolved but a fuzzy match exists
                if not request.resource_slug:
                    alias_hint = _fuzzy_alias_suggestion(request.query)
                    if alias_hint:
                        done["alias_suggestion"] = alias_hint
                yield _sse(done)
            else:
                yield _sse({"t": "chunk", "v": str(item)})

    return StreamingResponse(_generate(), media_type="text/event-stream")


@router.post("/feedback")
async def feedback(request: FeedbackRequest) -> dict:
    from resource_explorer.observability.metrics_collector import MetricsCollector
    if request.compile_id:
        MetricsCollector().record_feedback(request.query_hash, request.vote,
                                           compile_id=request.compile_id)
        return {"recorded": True, "compile_id": request.compile_id}
    MetricsCollector().record_feedback(request.query_hash, request.vote)
    return {"recorded": True}


def _code_inventory_table(query: str, resource_slug: str) -> dict | None:
    """
    Build a structured symbol table payload for code_inventory done events.
    Extracts kind hint from query, returns top 30 matching symbols.
    """
    import re
    import sqlite3
    try:
        from resource_explorer.registry import ProjectRegistry
        registry = ProjectRegistry()
        slug = registry._normalize_slug(resource_slug)

        q = query.lower()
        kind = "all"
        for k in ("class", "method", "function", "interface", "enum"):
            if k in q:
                kind = k
                break

        # Pattern hint: word after "named", "called", or after "show.*<kind>"
        pattern = ""
        m = re.search(r'(?:named?|called?)\s+(\w+)', q)
        if m:
            pattern = m.group(1)

        conn = sqlite3.connect(registry.db_path)
        conn.row_factory = sqlite3.Row
        filters = ["project_slug = ?"]
        params: list = [slug]
        if kind != "all":
            filters.append("kind = ?")
            params.append(kind)
        if pattern:
            filters.append("name LIKE ?")
            params.append(f"%{pattern}%")
        where = " AND ".join(filters)
        rows = conn.execute(
            f"SELECT kind, name, qualified_name, signature, docstring, file_path, start_line "  # noqa: S608
            f"FROM project_code_symbols WHERE {where} ORDER BY file_path, start_line LIMIT 30",
            params,
        ).fetchall()
        total = conn.execute(
            f"SELECT COUNT(*) FROM project_code_symbols WHERE {where}",  # noqa: S608
            params,
        ).fetchone()[0]
        conn.close()

        if not rows:
            return None
        return {
            "kind": kind,
            "project": slug,
            "total": total,
            "items": [
                {
                    "kind": r["kind"],
                    "name": r["qualified_name"],
                    "signature": r["signature"] or "",
                    "doc": (r["docstring"] or "")[:80],
                    "file": r["file_path"],
                    "line": r["start_line"],
                }
                for r in rows
            ],
        }
    except Exception:
        return None


def _compare_symbols_table(query: str) -> dict | None:
    """
    Build a side-by-side symbol comparison payload when the comparison query
    is about API surface, classes, or methods. Returns None if fewer than two
    projects have symbol data or the query isn't about code structure.
    """
    import re
    import sqlite3

    _SYMBOL_KEYWORDS = ("class", "method", "function", "interface", "api surface", "symbol", "public")
    q = query.lower()
    if not any(w in q for w in _SYMBOL_KEYWORDS):
        return None

    kind = "all"
    for k in ("class", "method", "function", "interface"):
        if k in q:
            kind = k
            break

    try:
        from resource_explorer.registry import ProjectRegistry
        from resource_explorer.agents.compare_agent import CompareAgent

        registry = ProjectRegistry()
        slugs = CompareAgent()._infer_all_project_slugs(query)
        if len(slugs) < 2:
            return None

        conn = sqlite3.connect(registry.db_path)
        conn.row_factory = sqlite3.Row

        sides = []
        for slug in slugs[:2]:
            filters = ["project_slug = ?"]
            params: list = [slug]
            if kind != "all":
                filters.append("kind = ?")
                params.append(kind)
            where = " AND ".join(filters)

            counts_rows = conn.execute(
                f"SELECT kind, COUNT(*) as cnt FROM project_code_symbols WHERE {where} GROUP BY kind",  # noqa: S608
                params,
            ).fetchall()
            counts = {r["kind"]: r["cnt"] for r in counts_rows}

            items_rows = conn.execute(
                f"SELECT kind, qualified_name, signature, docstring, file_path, start_line "  # noqa: S608
                f"FROM project_code_symbols WHERE {where} ORDER BY kind, file_path, start_line LIMIT 20",
                params,
            ).fetchall()
            total = conn.execute(
                f"SELECT COUNT(*) FROM project_code_symbols WHERE {where}",  # noqa: S608
                params,
            ).fetchone()[0]

            sides.append({
                "slug": slug,
                "total": total,
                "counts": counts,
                "items": [
                    {
                        "kind": r["kind"],
                        "name": r["qualified_name"],
                        "signature": r["signature"] or "",
                        "doc": (r["docstring"] or "")[:80],
                        "file": r["file_path"],
                        "line": r["start_line"],
                    }
                    for r in items_rows
                ],
            })

        conn.close()

        if not any(s["total"] > 0 for s in sides):
            return None

        return {"kind": kind, "sides": sides}
    except Exception:
        return None


class _NameProbe:
    """Built lazily: BaseExplorerAgent pulls in the agent stack, and the
    detection reuses its matcher (`_infer_all_project_slugs`) rather than a
    second one. That matcher already covers repos, databases and file systems,
    then falls back to the alias table."""

    @staticmethod
    def make():
        from resource_explorer.agents.base import BaseExplorerAgent

        class _Probe(BaseExplorerAgent):
            def system_prompt(self): return ""
            def tools(self): return []
            def handle(self, *a, **kw): return ""
        return _Probe()


def _resource_identity(registry, slug: str) -> tuple[str, str]:
    """(display_name, type) for a slug in any of the three resource tables."""
    p = registry.get(slug)
    if p:
        return (p.display_name or slug), "repo"
    d = registry.get_database(slug, allow_unreadable=True)
    if d:
        return (d.display_name or slug), "database"
    f = registry.get_filesystem(slug)
    if f:
        return (f.display_name or slug), "filesystem"
    return slug, "repo"


def _named_resource_check(query: str, scoped_slug: str) -> dict | None:
    """For a question asked ON a resource: does it name a different one?

    Returns one of
      {"resource_question": {kind: "other_resource", named_slug, named_name, named_type, scoped_slug}}
        -- the question exactly names a known resource (slug, alias or display name)
           that is not the scoped one;
      {"alias_suggestion": {term, candidate_slug, candidate_name, candidate_type, scoped_slug}}
        -- it names nothing known but a phrase fuzzy-matches a resource that is not the scoped one;
      {"resource_check_error": "<why>"}
        -- the check itself failed (logged, and surfaced to the person rather than read as "names nothing");
      None -- it names nothing, or names the scoped resource.
    """
    try:
        from resource_explorer.registry import ProjectRegistry

        registry = ProjectRegistry()
        scoped = registry._normalize_slug(scoped_slug)
        named = _NameProbe.make()._infer_all_project_slugs(query)
        if named:
            if scoped in named:
                return None
            name, kind = _resource_identity(registry, named[0])
            return {"resource_question": {
                "kind": "other_resource", "named_slug": named[0], "named_name": name,
                "named_type": kind, "scoped_slug": scoped,
            }}
        # Short words match too loosely against a name once every question is scoped.
        result = registry.fuzzy_candidate(query, exclude_slug=scoped, min_term_len=5)
        if not result:
            return None
        term, slug = result
        name, kind = _resource_identity(registry, slug)
        return {"alias_suggestion": {
            "term": term, "candidate_slug": slug, "candidate_name": name,
            "candidate_type": kind, "scoped_slug": scoped,
        }}
    except Exception as exc:
        log.warning("resource-name check failed for scope %r: %s", scoped_slug, exc, exc_info=True)
        return {"resource_check_error": f"{type(exc).__name__}: {exc}"}


def _fuzzy_alias_suggestion(query: str) -> dict | None:
    """
    Return {term, candidate_slug, candidate_name} if a fuzzy match exists for the query
    but is not already an exact slug/alias match. Returns None otherwise.
    """
    try:
        from resource_explorer.registry import ProjectRegistry
        from resource_explorer.agents.base import BaseExplorerAgent

        registry = ProjectRegistry()
        # Skip if query already resolves exactly
        class _Probe(BaseExplorerAgent):
            def system_prompt(self): return ""
            def tools(self): return []
            def handle(self, *a, **kw): return ""
        if _Probe()._infer_project_slug(query):
            return None
        result = registry.fuzzy_candidate(query)
        if not result:
            return None
        term, slug = result
        project = registry.get(slug)
        return {
            "term": term,
            "candidate_slug": slug,
            "candidate_name": project.display_name if project else slug,
        }
    except Exception as exc:
        # Observable, not swallowed: a registry error here used to read as "nothing to suggest".
        log.warning("alias suggestion failed: %s", exc, exc_info=True)
        return None


def _disambiguation_chips(query: str, scope_slug: str | None = None) -> list[dict] | None:
    """Candidate resource chips when a cross-resource question names fewer than 2 resources.

    The agents infer their targets from the question text alone, so the resource open in the UI
    does not count as named -- it is only ranked first.
    """
    try:
        from resource_explorer.resource_chips import candidate_chips

        named = _NameProbe.make()._infer_all_project_slugs(query)
        if len(named) >= 2:
            return None
        return candidate_chips(query, named, scope_slug) or None
    except Exception as exc:
        log.warning("disambiguation chips lookup failed: %s", exc, exc_info=True)
        return None
