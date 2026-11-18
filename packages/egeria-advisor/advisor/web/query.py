"""
Query API routes for Egeria Advisor.

Extracted from `app.py` (TC-5, BACKLOG.md — router-per-domain refactor,
slice 3). Follows the existing pattern: a bare `APIRouter()` with full
paths on each route. Depends on `advisor.web.shared` for `_get_rag()` and
`QueryRequest`/`_intent_meta` — the RAG singleton and the shared request
model/badge-metadata this shares with the rest of app.py.

Endpoints:
  POST /api/query         → run a query, return the result dict
  POST /api/query/stream  → streaming variant, Server-Sent Events
"""
from __future__ import annotations

import asyncio
import contextvars
import json
from functools import partial
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from loguru import logger

from advisor.web.shared import _get_rag, QueryRequest, _intent_meta

router = APIRouter()


@router.post("/api/query")
async def query_endpoint(request: Request, req: QueryRequest) -> Dict[str, Any]:
    """Process a natural-language query and return the response dict."""
    from advisor.auth import get_current_user, get_egeria_credentials, is_anonymous
    current_user = get_current_user(request)
    # Anonymous mode's stand-in user is not an Egeria identity (A-I1): live paths say "sign in".
    egeria_authenticated = current_user is not None and not is_anonymous(current_user)
    egeria_credentials = get_egeria_credentials(request)

    user_query = req.query.strip()
    # Append search filter tag so the report pipeline can extract it
    if req.search_string and req.search_string.strip() not in ("", "*"):
        user_query += f" filter:'{req.search_string.strip()}'"
    # Append output format tag when explicitly set (e.g. from the report modal dropdown)
    if req.output_format:
        user_query += f" fmt:'{req.output_format.strip()}'"

    try:
        rag = _get_rag()
        # Run the blocking RAG query in a thread-pool executor so FastAPI's
        # event loop is not blocked during MCP / LLM calls.  Inside the
        # executor thread, asyncio.get_event_loop().is_running() is False, so
        # _run_async() inside the pipeline uses asyncio.run() directly —
        # cleaner than the nested-thread approach used when called on-loop.
        loop = asyncio.get_event_loop()
        user_id = current_user.get("sub") if current_user else None
        # copy_context: the worker thread acts for this request's user (request_context.current_user_id), so
        # drafts land in that user's own folder. run_in_executor alone doesn't carry contextvars (F1).
        result = await loop.run_in_executor(
            None,
            contextvars.copy_context().run,
            partial(
                rag.query,
                user_query=user_query,
                include_context=True,
                track_metrics=True,
                query_type_override=req.intent_override or None,
                perspective=req.perspective or None,
                page_size=req.page_size or None,
                draft_id=req.draft_id or None,
                context=req.context or None,
                egeria_authenticated=egeria_authenticated,
                session_id=req.session_id or None,
                user_id=user_id,
                egeria_credentials=egeria_credentials,
            ),
        )
    except Exception as exc:
        logger.error(f"Query failed: {exc}")
        result = {
            "query": req.query,
            "response": f"Sorry, an error occurred: {exc}",
            "query_type": "general",
            "routing_agent": "error",
            "sources": [],
            "num_sources": 0,
            "retrieval_time": 0.0,
            "generation_time": 0.0,
            "avg_relevance_score": 0.0,
            "context_length": 0,
        }

    query_type = result.get("query_type", "general")
    result["intent"] = _intent_meta(query_type)
    return result


@router.post("/api/query/stream")
async def query_stream_endpoint(request: Request, req: QueryRequest) -> StreamingResponse:
    """
    Streaming variant of /api/query — returns Server-Sent Events.

    Event sequence:
      data: {"type":"start","query":"..."}
      data: {"type":"token","text":"..."}   (repeated, only for LLM-generation paths)
      data: {"type":"done","result":{...}}
      data: [DONE]
    """
    from advisor.auth import get_current_user, get_egeria_credentials, is_anonymous
    current_user = get_current_user(request)
    # Anonymous mode's stand-in user is not an Egeria identity (A-I1): live paths say "sign in".
    egeria_authenticated = current_user is not None and not is_anonymous(current_user)
    egeria_credentials = get_egeria_credentials(request)

    user_query = req.query.strip()
    if req.search_string and req.search_string.strip() not in ("", "*"):
        user_query += f" filter:'{req.search_string.strip()}'"
    if req.output_format:
        user_query += f" fmt:'{req.output_format.strip()}'"

    loop = asyncio.get_event_loop()
    rag  = _get_rag()
    # This request's context (its user, set by the user-context middleware), captured now: the producer runs
    # in a worker thread, which run_in_executor doesn't give the caller's contextvars (F1).
    request_ctx = contextvars.copy_context()

    async def event_gen():
        # Bridge sync generator → async generator via asyncio.Queue so the
        # event loop stays unblocked while the worker thread produces tokens.
        q: asyncio.Queue[Optional[str]] = asyncio.Queue(maxsize=256)

        user_id = current_user.get("sub") if current_user else None
        def producer() -> None:
            try:
                for chunk in rag.query_stream(
                    user_query=user_query,
                    include_context=True,
                    query_type_override=req.intent_override or None,
                    perspective=req.perspective or None,
                    page_size=req.page_size or None,
                    draft_id=req.draft_id or None,
                    context=req.context or None,
                    egeria_authenticated=egeria_authenticated,
                    session_id=req.session_id or None,
                    user_id=user_id,
                    egeria_credentials=egeria_credentials,
                ):
                    loop.call_soon_threadsafe(q.put_nowait, chunk)
            except Exception as exc:
                logger.error(f"query_stream producer error: {exc}", exc_info=True)
                err = json.dumps({"type": "error", "message": str(exc)})
                loop.call_soon_threadsafe(q.put_nowait, f"data: {err}\n\n")
            finally:
                loop.call_soon_threadsafe(q.put_nowait, None)  # sentinel

        import concurrent.futures
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = loop.run_in_executor(executor, request_ctx.run, producer)

        while True:
            item = await q.get()
            if item is None:
                break
            yield item

        await future
        executor.shutdown(wait=False)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx buffering
        },
    )
