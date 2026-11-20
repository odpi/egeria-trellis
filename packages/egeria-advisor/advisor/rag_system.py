"""
Complete RAG system integrating retrieval, query processing, and LLM generation.

This module provides the main interface for the RAG-based code advisor.
"""

import json
import queue
import re
from typing import Dict, Any, Optional, List, Iterator
from loguru import logger
import threading
import time

from advisor.llm_client import get_ollama_client
from advisor.rag_retrieval import get_rag_retriever
from advisor.query_processor import get_query_processor
from advisor.mlflow_tracking import get_mlflow_tracker
from advisor.metrics_collector import get_metrics_collector, track_query, CollectionHealth, sync_collection_health
from advisor.analytics import get_analytics_manager
from advisor.relationships import get_relationship_query_handler
from advisor.config import get_full_config
from advisor.prompt_templates import get_prompt_manager
from advisor.query_patterns import QueryType


def _egeria_required_response(user_query: str) -> Dict[str, Any]:
    """Return a graceful degradation response when the user is not authenticated."""
    return {
        "query": user_query,
        "response": (
            "This action requires an active Egeria session.\n\n"
            "**Sign in** using the login button in the header to access live reports, "
            "execute Dr. Egeria commands, and run governance plans against Egeria.\n\n"
            "Knowledge questions, code examples, plan *generation*, and explanations "
            "are all available without logging in."
        ),
        "query_type": "general",
        "routing_agent": "auth_gate",
        "sources": [],
        "num_sources": 0,
        "retrieval_time": 0.0,
        "generation_time": 0.0,
        "avg_relevance_score": 0.0,
        "context_length": 0,
    }


def _serialise_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Return a JSON-serialisable copy of a result dict (normalises source objects)."""
    sources = []
    for s in result.get("sources", []):
        if isinstance(s, dict):
            sources.append(s)
        else:
            # SearchResult or similar object
            entry: Dict[str, Any] = {}
            if hasattr(s, "score"):
                entry["score"] = s.score
            if hasattr(s, "metadata"):
                entry.update(s.metadata)
            if hasattr(s, "content"):
                entry["content"] = s.content[:200]
            sources.append(entry)
    out = dict(result)
    out["sources"] = sources
    return out



#: A message that is ONLY a confirmation / cancellation of a shown Dr.Egeria script.
_DR_EGERIA_CONFIRM_YES = re.compile(
    r"^(yes|y|confirm(ed)?|process( it)?|run it|do it|go ahead|proceed)( please)?[\s.!]*$")
_DR_EGERIA_CONFIRM_NO = re.compile(r"^(no|n|cancel|stop|never ?mind|don'?t)\b")
#: A message that is ONLY "yes, re-run": confirms a shown plan AND acknowledges that a re-run repeats its
#: non-idempotent commands (GovernancePlanAgent.non_idempotent_commands).
_PLAN_RERUN_ACK = re.compile(r"^(yes|y|confirm(ed)?)[,;:\s]+re-?run( it| anyway| all of it)?( please)?[\s.!]*$")


class RAGSystem:
    """Complete RAG system for code advisory."""

    def __init__(self):
        """Initialize RAG system."""
        from advisor.phoenix_client import init_phoenix
        init_phoenix()

        self.llm_client = get_ollama_client()
        self.retriever = get_rag_retriever()
        self.query_processor = get_query_processor()
        self.mlflow_tracker = get_mlflow_tracker(
            enable_resource_monitoring=True,
            enable_accuracy_tracking=True
        )
        self.metrics_collector = get_metrics_collector()
        self.analytics = get_analytics_manager()
        self.relationships = get_relationship_query_handler()

        config = get_full_config()
        self.rag_config = config.get("rag")

        logger.info("Initialized RAG system")
        
        # Refresh health on startup
        self._refresh_collection_health()

    def _refresh_collection_health(self):
        """Refresh health metrics for all enabled collections."""
        sync_collection_health(self.retriever, self.metrics_collector)

    def query(
        self,
        user_query: str,
        include_context: bool = True,
        track_metrics: bool = True,
        dry_run: bool = False,
        query_type_override: Optional[str] = None,
        perspective: Optional[str] = None,
        page_size: Optional[int] = None,
        draft_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        egeria_authenticated: bool = True,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        egeria_credentials: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Process a user query and generate a response.

        Args:
            user_query: User's question or request
            include_context: Whether to include retrieved context
            track_metrics: Whether to track with MLflow
            dry_run: If True, compose Dr.Egeria commands but do not execute them
            egeria_authenticated: False blocks MCP-dependent paths (report, command execution,
                plan execution) and returns a friendly degradation message instead.
            session_id: Optional session ID for tracking
            user_id: Optional user ID for tracking
            egeria_credentials: the authenticated caller's {user_id, password} for live
                Egeria calls (reports, Dr.Egeria actions, plan execution); falls back to
                the .env-backed service account when None (see advisor.auth).

        Returns:
            Dictionary with response and metadata
        """
        logger.info(f"Processing query: {user_query[:100]}...")

        # Process the query
        result = self._process_query(
            user_query, include_context, dry_run=dry_run,
            query_type_override=query_type_override,
            perspective=perspective,
            page_size=page_size,
            draft_id=draft_id,
            context=context,
            egeria_authenticated=egeria_authenticated,
            session_id=session_id,
            user_id=user_id,
            egeria_credentials=egeria_credentials,
        )
        
        # Always record metrics in local database (for dashboard)
        try:
            self._record_local_metrics(result)
        except Exception as e:
            logger.warning(f"Failed to record local metrics: {e}")
        
        # Track with MLflow in background so the caller gets the result immediately
        if track_metrics:
            threading.Thread(
                target=self._track_mlflow,
                args=(result, include_context),
                daemon=True
            ).start()

        return result

    def _track_mlflow(self, result: Dict[str, Any], include_context: bool):
        """Log query metrics to MLflow in a background thread (non-blocking)."""
        try:
            with self.mlflow_tracker.track_operation(
                operation_name="rag_query",
                params={
                    "query_length": len(result.get("query", "")),
                    "include_context": include_context
                },
                track_resources=True,
                track_accuracy=True
            ) as tracker:
                sources = result.get("sources") or []
                for source in sources:
                    try:
                        if hasattr(source, 'score') and source.score is not None:
                            tracker.add_relevance(source.score)
                        elif isinstance(source, dict) and source.get('score') is not None:
                            tracker.add_relevance(source['score'])
                    except Exception:
                        pass
                tracker.log_metrics({
                    "response_length": len(result.get("response", "")),
                    "num_sources": result.get("num_sources", 0),
                    "retrieval_time": result.get("retrieval_time", 0.0),
                    "generation_time": result.get("generation_time", 0.0),
                    "avg_relevance_score": result.get("avg_relevance_score", 0.0),
                    "context_length": result.get("context_length", 0)
                })
        except Exception as e:
            logger.warning(f"MLflow tracking failed: {e}")

    # Phrases that signal a definitional/conceptual question, NOT a data retrieval query.
    # These go to RAG even when the semantic score is high.
    _DEFINITIONAL_PREFIXES = (
        "what is ", "what's a ", "what's the ", "what are the ",
        "how does ", "how do ", "explain ", "define ", "describe ",
        "tell me about ", "what does ", "what do you mean by ",
        "can you explain", "give me an overview",
    )

    # Interrogative forms that must NEVER reach action agents regardless of intent.
    # These indicate the user wants an explanation, not to create/execute anything.
    _INTERROGATIVE_PREFIXES = (
        "what is ", "what are ", "what's a ", "what's the ", "what's an ",
        "what does ", "what do ",
        "how does ", "how do ", "how is ", "how are ", "how would ",
        "explain ", "define ", "describe ",
        "tell me about ", "tell me what ",
        "can you explain", "could you explain",
        "give me an overview", "give me a summary",
        "what exactly is", "what exactly are",
        "why is ", "why are ", "why does ", "why do ",
        "who is ", "who are ",
        "when is ", "when are ", "when does ",
        "where is ", "where are ", "where does ",
    )

    def _is_interrogative(self, query: str) -> bool:
        """Return True if this query is an informational question that must
        route to DocAgent regardless of intent setting."""
        q = query.strip().lower()
        return any(q.startswith(p) for p in self._INTERROGATIVE_PREFIXES)

    # A bare "create a plan" ask with no real content to decompose yet — offer
    # a choice of how to build it (conversation vs. Plan Editor canvas) instead
    # of starting PlanElicitor's decomposition on an empty/near-empty query,
    # which otherwise hallucinates a plan from whatever stray nouns are left
    # (e.g. "with the canvas" alone got decomposed into a "Create Solution
    # Blueprint named Canvas" plan — confirmed live 2026-07-09).
    #
    # Rather than one anchored regex (too brittle — failed on "create an
    # empty Dr.Egeria plan" and "...with the canvas"), strip every filler/
    # scaffolding word a bare request could plausibly contain and check
    # whether anything is left. Anything with actual substance (an object
    # type, a name, a detail) survives stripping and proceeds to generation
    # as before, per the generate-first principle.
    _PLAN_FILLER_RE = re.compile(
        r'\b(?:i\s+want\s+to|i\'?d\s+like\s+to|i\s+would\s+like\s+to|can\s+you|'
        r'could\s+you|please|let\'?s|lets|i\s+need\s+to|help\s+me)\b',
        re.IGNORECASE,
    )
    # "empty"/"blank"/etc. signal "nothing to decompose" but don't by themselves
    # pick a build method — kept separate from _PLAN_CANVAS_RE below, which is
    # an explicit tool preference and should skip straight to the canvas
    # rather than showing the discuss-vs-canvas choice.
    _PLAN_BLANK_RE = re.compile(
        r'\bempty\b|\bblank\b|\bfrom\s+scratch\b|\bmanually\b|\bmyself\b',
        re.IGNORECASE,
    )
    _PLAN_CANVAS_RE = re.compile(
        r'\b(?:with|using|on|via|in)\s+the\s+canvas\b|\bcanvas\b',
        re.IGNORECASE,
    )
    _PLAN_TYPE_RE = re.compile(r'\bdr\.?\s*egeria\b|\bgovernance\b', re.IGNORECASE)
    _PLAN_VERB_RE = re.compile(r'\b(?:create|make|build|start|generate|new)\b', re.IGNORECASE)
    _PLAN_ARTICLE_RE = re.compile(r'\b(?:a|an|the)\b', re.IGNORECASE)
    _PLAN_NOUN_RE = re.compile(r'\bplans?\b', re.IGNORECASE)
    # "called X" / "named X" / "titled X" — a title is not "real content" to
    # decompose into a command; it's metadata for the plan document itself.
    # Captured separately so the extracted name can be used as the actual
    # plan title instead of being lost (or worse, misread as an object name —
    # "create a plan called Link-Test" previously hallucinated a
    # "Create Solution Blueprint / Campaign named Link-Test", confirmed live
    # 2026-07-09).
    _PLAN_NAME_RE = re.compile(
        r'\b(?:called|named|titled)\s+["\']?([A-Za-z0-9][\w\-]{0,40}'
        r'(?:\s+(?!using\b|with\b|on\b|via\b|in\s+the\b|empty\b|blank\b)[\w\-]{1,40}){0,4})["\']?',
        re.IGNORECASE,
    )

    def _extract_plan_title(self, query: str) -> Optional[str]:
        m = self._PLAN_NAME_RE.search(query)
        return m.group(1).strip() if m else None

    def _wants_canvas_directly(self, query: str) -> bool:
        return bool(self._PLAN_CANVAS_RE.search(query))

    def _is_bare_plan_request(self, query: str) -> bool:
        q = query.strip()
        # Must actually be a plan-creation ask in the first place.
        if not self._PLAN_NOUN_RE.search(q) or not self._PLAN_VERB_RE.search(q):
            return False
        remainder = q
        for pat in (self._PLAN_FILLER_RE, self._PLAN_CANVAS_RE, self._PLAN_BLANK_RE,
                    self._PLAN_NAME_RE, self._PLAN_TYPE_RE, self._PLAN_VERB_RE,
                    self._PLAN_ARTICLE_RE, self._PLAN_NOUN_RE):
            remainder = pat.sub(' ', remainder)
        remainder = re.sub(r'[^\w]', '', remainder)
        return remainder == ''

    # Patterns that indicate "what dr.egeria commands/templates handle X" — answer from catalog.
    _COMMAND_DISCOVERY_RE = re.compile(
        r'(?:'
        # Interrogative openers: "are there (any)", "is there (a)", "what/which/list/show/find"
        r'(?:are\s+there(?:\s+any)?|is\s+there(?:\s+a(?:ny)?)?'
        r'|(?:what|which|list|show|find|search\s+for|do\s+(?:we|you|i)\s+have'
        r'|can\s+you\s+(?:show|list|find))[\w\s]{0,20})'
        # optional "dr.egeria" qualifier
        r'\s+(?:dr\.?\s*egeria\s+|dre\s+)?'
        # "commands" or "templates" (with optional "markdown" prefix)
        r'(?:commands?|templates?|markdown\s+commands?)'
        r')'
        # OR "how do I X with dr.egeria" form
        r'|how\s+(?:do\s+(?:i|we)|can\s+(?:i|we))\s+.{0,60}(?:with|using|in)\s+dr\.?\s*egeria\b',
        re.IGNORECASE,
    )

    def _is_command_discovery(self, query: str) -> bool:
        return bool(self._COMMAND_DISCOVERY_RE.search(query))

    def _handle_command_discovery(self, query: str) -> Optional[Dict[str, Any]]:
        """Answer a command-discovery question directly from CommandKeywordIndex. TR-51 d: the answer reports the
        commands it found as its sources, with the index lookup's time."""
        from advisor.agents.tools import lookup_source, recording_retrieval, record_lookup, with_retrieval
        from advisor.command_keyword_index import get_command_keyword_index
        idx = get_command_keyword_index()

        # Extract the topic keyword — try prepositions first, then verb objects.
        topic_m = re.search(
            r'\b(?:about|for|related\s+to|regarding|on|covering|deal(?:ing)?\s+with)\s+(.+?)[\?\.]*$',
            query, re.IGNORECASE,
        ) or re.search(
            # "how do I create a glossary term with Dr.Egeria" → topic = "glossary term"
            r'\b(?:creat|add|updat|link|classif|manag|defin)\w*\s+(?:a\s+|an\s+)?(.+?)'
            r'(?:\s+(?:with|using|in)\s+dr\.?\s*egeria)?[\?\.]*$',
            query, re.IGNORECASE,
        )
        if topic_m:
            # Use the last captured group that actually matched
            topic = next((g for g in reversed(topic_m.groups()) if g), None)
            topic = topic.strip() if topic else None
        else:
            topic = None

        with recording_retrieval() as log:
            started = time.perf_counter()
            groups = idx.search_by_keyword(topic) if topic else idx.all_commands()
            record_lookup("command_keyword_index", time.perf_counter() - started,
                          [lookup_source("command_keyword_index", "dr-egeria command catalog and templates",
                                         cmd["name"], f"{family}: {cmd['name']}", kind="command",
                                         module=family or "")
                           for family, cmds in (groups or {}).items() for cmd in cmds])

        if not groups:
            if topic:
                return with_retrieval({
                    "query": query,
                    "response": (
                        f"No Dr. Egeria commands found matching **{topic}**. "
                        "Try a broader term, or ask \"what dr.egeria commands are available?\" "
                        "to browse all command families."
                    ),
                    "query_type": "explanation",
                    "sources": [],
                    "routing_agent": "command_keyword_index",
                }, log)
            return None  # no topic and no commands — fall through to DocAgent

        lines = []
        if topic:
            lines.append(f"Dr. Egeria commands related to **{topic}**:\n")
        else:
            lines.append("Available Dr. Egeria command families:\n")

        for family, cmds in groups.items():
            lines.append(f"### {family}")
            for cmd in cmds:
                tag = " *(catalog)*" if cmd["in_catalog"] else ""
                lines.append(f"- **{cmd['name']}**{tag}")
            lines.append("")

        return with_retrieval({
            "query": query,
            "response": "\n".join(lines),
            "query_type": "explanation",
            "sources": [],
            "routing_agent": "command_keyword_index",
        }, log)

    # Structural code queries that should go to the symbol store, not RAG.
    _STRUCTURAL_QUERY_RE = re.compile(
        r'(?:'
        r'(?:list|show|what|which|give\s+me|find|how\s+many)\s+'
        r'(?:are\s+the\s+|are\s+in\s+|is\s+in\s+)?'
        r'(?:all\s+)?'
        r'(?:classes?|methods?\s+(?:in|on|of|for)|functions?|symbols?)'
        r')'
        r'|(?:what\s+methods?\s+does\s+\w+\s+have)'
        r'|(?:methods?\s+(?:on|of|for|available\s+(?:on|in))\s+[A-Z]\w+)'
        r'|(?:(?:most\s+)?complex\s+(?:method|function))'
        r'|(?:largest\s+class)'
        r'|(?:biggest\s+class)'
        r'|(?:class\s+with\s+most\s+method)',
        re.IGNORECASE,
    )

    def _is_structural_query(self, query: str) -> bool:
        return bool(self._STRUCTURAL_QUERY_RE.search(query))

    def _handle_structural_query(self, query: str, path_filter: str | None = None) -> Optional[Dict[str, Any]]:
        """Answer a code structure question directly from the symbol store."""
        try:
            from advisor.analytics import get_analytics_manager
            return {"query": query, "routing_agent": "symbol_store",
                    **self._quantitative_answer(query, path_filter, get_analytics_manager())}
        except Exception as exc:
            logger.warning(f"Structural query handler failed: {exc}")
            return None

    @staticmethod
    def _quantitative_answer(query: str, path_filter: Optional[str], analytics) -> Dict[str, Any]:
        """analytics.answer_quantitative_query returns text only. TR-51 d: what it read (symbol-store queries, or the
        cached metrics file) is recorded while it runs, and the result reports it: its sources, the lookup time and
        retrieval_method ("none" only when nothing was read), not hard-coded zeros."""
        from advisor.agents.tools import recording_retrieval, with_retrieval
        with recording_retrieval() as log:
            response = analytics.answer_quantitative_query(query, path_filter=path_filter)
        return with_retrieval({"response": response, "query_type": "quantitative", "sources": [], "num_sources": 0,
                               "retrieval_time": 0.0, "generation_time": 0.0, "avg_relevance_score": 0.0,
                               "context_length": len(response)}, log)

    # Keywords that signal the user wants Python code, not live Egeria data.
    _CODE_EXAMPLE_SIGNALS = (
        "python", "code example", "code sample", "write python",
        "python code", "pyegeria example", "python snippet",
        # Any mention of Dr.Egeria belongs to the command/template path, never a data report
        "dr.egeria", "dr egeria", "dr. egeria",
    )

    # Phrases that explicitly request a Dr.Egeria template/command, used to
    # redirect "Show Me" (code_search) intent away from ExamplesAgent.
    _DRE_TEMPLATE_SIGNALS = (
        "dr egeria template", "dr. egeria template", "dr.egeria template", "dre template",
        "dr egeria command", "dr. egeria command", "dr.egeria command", "dre command",
        "egeria template", "egeria markdown template", "markdown command",
    )

    def _is_report_query(self, query: str) -> bool:
        """
        Return True if the query is a data-retrieval request that the report
        pipeline can answer by running a report spec.

        Routes when EITHER:
        1. The query strongly matches a known report *name* (so typing a report
           name in chat works), OR
        2. Semantic similarity against question_spec entries is >= 0.45 (questions
           are hints, so the floor is forgiving).
        Guarded so definitional and code-example queries still go to RAG.
        """
        q = query.strip().lower()
        if any(q.startswith(p) for p in self._DEFINITIONAL_PREFIXES):
            return False
        if any(sig in q for sig in self._CODE_EXAMPLE_SIGNALS):
            return False
        try:
            from advisor.report_pipeline import get_report_pipeline, _question_index
            # Name-first: a short, name-like query (e.g. "Collections", "My User MD")
            # routes here even though it isn't phrased as a question. Restricted to
            # short inputs so conceptual questions that merely mention a report name
            # (e.g. "tell me about collections") fall through to semantic matching.
            if len(q.split()) <= 4:
                name_match = get_report_pipeline().match_report_name(query)
                if name_match and name_match[1] >= 0.9:
                    logger.info(f"Report pre-check via name: {name_match[0]!r} (conf={name_match[1]:.2f})")
                    return True
            hits = _question_index.search(query, top_k=1, threshold=0.45)
            if hits:
                logger.info(
                    f"Semantic report pre-check: {hits[0]['report_spec']} "
                    f"(score={hits[0]['score']:.2f})"
                )
                return True
        except Exception as exc:
            logger.debug(f"_is_report_query check failed: {exc}")
        return False

    def _report_alternatives(self, query: str) -> Optional[Dict[str, Any]]:
        """
        When semantic similarity is medium (0.35–0.50), return a clarification
        response offering the matched report spec alongside a RAG alternative.
        Returns None if no medium-confidence hit exists.
        """
        q = query.strip().lower()
        if any(q.startswith(p) for p in self._DEFINITIONAL_PREFIXES):
            return None
        if any(sig in q for sig in self._CODE_EXAMPLE_SIGNALS):
            return None
        try:
            from advisor.report_pipeline import _question_index
            hits = _question_index.search(query, top_k=2, threshold=0.35)
            # Only surface alternatives for medium confidence (below the run threshold)
            medium_hits = [h for h in hits if h["score"] < 0.50]
            if not medium_hits:
                return None
            best = medium_hits[0]
            spec = best["report_spec"]
            score = best["score"]
            logger.info(f"Medium-confidence report match: {spec} ({score:.2f}) — offering alternatives")
            return {
                "query": query,
                "response": (
                    f"Your query could be answered in a couple of ways:\n\n"
                    f"**Option 1 — Run the Egeria report** (recommended if you want current live data):\n"
                    f"I found the **{spec}** report that may match your question "
                    f"(confidence: {score:.0%}). "
                    f"To run it, use Dr.Egeria: `[[{spec}]]`  \n"
                    f"Or ask me: *\"run the {spec} report\"*\n\n"
                    f"**Option 2 — Explain or show code examples**:\n"
                    f"I can also explain how to work with this in pyegeria — just ask "
                    f"*\"how do I...\"* or *\"show me an example of...\"*\n\n"
                    f"Which do you want?"
                ),
                "query_type": "clarification",
                "routing_agent": "clarification",
                "report_name": spec,
                "sources": [],
                "num_sources": 0,
                "retrieval_time": 0.0,
                "generation_time": 0.0,
                "avg_relevance_score": score,
                "context_length": 0,
            }
        except Exception as exc:
            logger.debug(f"_report_alternatives check failed: {exc}")
        return None

    def _process_query(
        self,
        user_query: str,
        include_context: bool,
        dry_run: bool = False,
        query_type_override: Optional[str] = None,
        perspective: Optional[str] = None,
        page_size: Optional[int] = None,
        draft_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        egeria_authenticated: bool = True,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        egeria_credentials: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Internal query processing."""

        # ------------------------------------------------------------------ #
        # Context-based routing — authoritative, no pattern matching needed. #
        # When the frontend sends a context.task the message unambiguously    #
        # belongs to that task's handler regardless of intent button state.  #
        #                                                                     #
        # Exception: an explicit query_type_override='report' (user clicked  #
        # "Run Report" on a specific catalog entry) is itself an unambiguous #
        # mode switch and always wins over a stale elicitor task context —   #
        # otherwise a leftover report-spec/plan Q&A session silently         #
        # swallows the report run instead of executing it. See BACKLOG.md    #
        # SS-1.                                                              #
        # ------------------------------------------------------------------ #
        _ctx_task     = (context or {}).get("task")
        _ctx_draft_id = (context or {}).get("draft_id")
        if query_type_override == "report":
            _ctx_task = None
            _ctx_draft_id = None
            draft_id = None

        if _ctx_task == "report_spec_elicitor" and _ctx_draft_id:
            from advisor.agents.report_spec_elicitor import get_report_spec_elicitor
            from advisor.agents.report_spec_agent import get_report_spec_agent
            _rse = get_report_spec_elicitor()
            _q = user_query.strip().lower()
            if re.match(r'^(go\s+)?back\b', _q):         return _rse.back(_ctx_draft_id)
            if re.match(r'^(save\s+(&|and)\s+exit|save\s+exit)\b', _q): return _rse.save_and_exit(_ctx_draft_id)
            if re.match(r'^(cancel|start\s+over|abandon)\b', _q):  return _rse.cancel(_ctx_draft_id)
            if re.match(r'^(discard)\b', _q):             return _rse.discard(_ctx_draft_id)
            if re.match(r'^(restart|redo\s+(q&a|questions))\b', _q): return _rse.restart_qa(_ctx_draft_id)
            # Bare "run" would also match "run report <other name>" (a request
            # to run a *different*, named report, not this draft's spec) —
            # require an object or "it" so that case falls through instead.
            # See BACKLOG.md SS-2.
            if re.search(r'\b(execute|run\s+(?:the\s+)?spec|run\s+it|go\s+ahead|proceed)\b', _q):
                # Fetch page_size/format override from query payload if available
                custom_params = {}
                if page_size is not None:
                    custom_params["page_size"] = page_size
                _fmt_m = re.search(r"\bfmt:'([^']+)'", user_query, re.IGNORECASE)
                _output_fmt = _fmt_m.group(1).upper() if _fmt_m else "TABLE"
                
                from advisor.report_draft import get_report_draft_manager as _rdm
                spec = _rdm().load(_ctx_draft_id)
                doc_id = spec.get("doc_id") if spec else None
                exec_id = doc_id if doc_id else _ctx_draft_id
                
                logger.info(f"Elicitor draft execute command detected — executing spec/draft {exec_id}")
                try:
                    result = get_report_spec_agent().execute(
                        exec_id,
                        perspective=perspective,
                        output_format=_output_fmt,
                        custom_params=custom_params if custom_params else None,
                        egeria_credentials=egeria_credentials,
                    )
                    # Preserve context so the canvas state is maintained
                    result["next_context"] = context
                    return result
                except Exception as exc:
                    logger.warning(f"Draft execute failed: {exc}")
            return _rse.process(_ctx_draft_id, user_query, egeria_credentials=egeria_credentials)

        elif _ctx_task == "plan_elicitor" and _ctx_draft_id:
            from advisor.agents.governance_plan_agent import get_governance_plan_agent
            _gpa = get_governance_plan_agent()
            _q = user_query.strip().lower()
            if re.match(r'^(go\s+)?back\b', _q):         return _gpa.back(_ctx_draft_id)
            if re.match(r'^(save\s+(&|and)\s+exit|save\s+exit)\b', _q): return _gpa.save_and_exit(_ctx_draft_id)
            if re.match(r'^(cancel|start\s+over|abandon)\b', _q):  return _gpa.cancel(_ctx_draft_id)
            if re.match(r'^(discard)\b', _q):             return _gpa.discard(_ctx_draft_id)
            if re.match(r'^(restart|redo\s+(q&a|questions))\b', _q): return _gpa.restart_qa(_ctx_draft_id)
            _exec_m = re.search(r'\b(execute|run\s+the\s+plan|go\s+ahead|do\s+it|proceed)\b', _q)
            if _exec_m:
                from advisor.governance_docs import get_doc_manager
                _dm = get_doc_manager()
                _spec_d = _dm.load(_ctx_draft_id) if hasattr(_dm, 'load') else None
                if not _spec_d:
                    from advisor.governance_draft import get_draft_manager
                    _spec_d = get_draft_manager().load(_ctx_draft_id)
                if _spec_d and _spec_d.get("doc_id"):
                    try:
                        # the plan confirm gate: show the plan; "yes" with its digest runs it
                        result = get_governance_plan_agent().prepare_execute(
                            _spec_d["doc_id"], draft_id=_ctx_draft_id, egeria_credentials=egeria_credentials,
                        )
                        result.setdefault("routing_agent", "governance_plan_agent")
                        result.setdefault("next_context", None)
                        return result
                    except Exception as exc:
                        logger.error("GovernancePlanAgent.execute failed (ctx): {}", str(exc), exc_info=True)
            return _gpa.continue_draft(_ctx_draft_id, user_query, egeria_credentials=egeria_credentials)

        elif _ctx_task == "plan_execute_confirm":
            # The plan confirm gate: a shown plan runs only on a message that is wholly a confirmation,
            # against the digest of the plan that was shown (GovernancePlanAgent.confirm).
            from advisor.agents.governance_plan_agent import get_governance_plan_agent
            _pid = (context or {}).get("pending_id", "")
            _dig = (context or {}).get("digest", "")
            _q = user_query.strip().lower()
            _ack = bool(_PLAN_RERUN_ACK.match(_q))
            if _ack or _DR_EGERIA_CONFIRM_YES.match(_q):
                if not egeria_authenticated:
                    return _egeria_required_response(user_query)
                # a plain "yes" to a re-run that repeats non-idempotent commands is refused by the agent,
                # which keeps this context so "yes, re-run" can follow
                result = get_governance_plan_agent().confirm(_pid, _dig, perspective=perspective,
                                                             egeria_credentials=egeria_credentials,
                                                             ack_non_idempotent=_ack)
                result.setdefault("next_context", None)
                result.update({"query": user_query, "routing_agent": "governance_plan_agent",
                               "session_id": session_id, "user_id": user_id})
                return result
            if _DR_EGERIA_CONFIRM_NO.match(_q):
                result = get_governance_plan_agent().cancel_execute(_pid, draft_id=(context or {}).get("draft_id"))
                result.update({"query": user_query, "session_id": session_id, "user_id": user_id})
                return result

        elif _ctx_task == "dr_egeria_confirm":
            # The chat-action confirm gate: a shown Dr.Egeria script is processed only on an explicit
            # confirmation, against the digest of the script that was shown (DrEgeriaActionAgent.confirm).
            # Stricter than act_confirm's "yes anywhere" because this path writes: the WHOLE message must
            # be a confirmation. Anything else is a new request (the pending script is left to expire).
            from advisor.agents.dr_egeria_agent import get_dr_egeria_agent
            _pid = (context or {}).get("pending_id", "")
            _dig = (context or {}).get("digest", "")
            _q = user_query.strip().lower()
            if _DR_EGERIA_CONFIRM_YES.match(_q):
                if not egeria_authenticated:
                    return _egeria_required_response(user_query)
                result = get_dr_egeria_agent().confirm(_pid, _dig, egeria_credentials=egeria_credentials)
                result.update({"query": user_query, "routing_agent": "dr_egeria_agent",
                               "session_id": session_id, "user_id": user_id})
                return result
            if _DR_EGERIA_CONFIRM_NO.match(_q):
                result = get_dr_egeria_agent().cancel(_pid)
                result.update({"query": user_query, "session_id": session_id, "user_id": user_id})
                return result

        elif _ctx_task == "act_confirm":
            if not egeria_authenticated:
                return _egeria_required_response(user_query)
            _spec_id = (context or {}).get("spec_id", "")
            _filt    = (context or {}).get("filter", "*") or "*"
            _fmt     = (context or {}).get("fmt", "TABLE") or "TABLE"
            _etype   = ""
            # If the user clicked "Run Report" button, the query is a direct
            # "run report <name> filter:'...' fmt:'...'" command — parse it to get
            # the latest filter/fmt (user may have changed them via format buttons).
            _RUN_DIRECT = re.compile(
                r'^run\s+report\s+(.+?)(?:\s+filter:\'([^\']*)\')?(?:\s+element_type:\'([^\']*)\')?(?:\s+fmt:\'([^\']*)\')?$',
                re.IGNORECASE,
            )
            _m_direct = _RUN_DIRECT.match(user_query.strip())
            if _m_direct:
                _spec_id = _m_direct.group(1).strip()
                _filt    = (_m_direct.group(2) or "").strip() or "*"
                _etype   = (_m_direct.group(3) or "").strip()
                _fmt     = (_m_direct.group(4) or "").strip() or "TABLE"
            _YES = re.compile(r'\b(yes|go|ok|confirm|do\s+it|proceed)\b', re.IGNORECASE)
            _NO  = re.compile(r'\b(no|cancel|stop|never\s+mind|skip)\b', re.IGNORECASE)
            if _m_direct or _YES.search(user_query):
                try:
                    from advisor.report_pipeline import get_report_pipeline
                    _rp = get_report_pipeline()
                    _extra = {"metadata_element_type": _etype} if _etype else None
                    result = _rp._execute_report(user_query, _spec_id, search_string=_filt,
                                                  page_size=page_size, extra_params=_extra,
                                                  output_type=_fmt, egeria_credentials=egeria_credentials)
                    if result:
                        result["query_type"]  = "act_report_result"
                        result["matched_spec_id"] = _spec_id
                        # expose base_doc_id so the "⬇ Save result" button appears
                        if _spec_id and not result.get("base_doc_id"):
                            result["base_doc_id"] = _spec_id
                        result["next_context"] = None
                        return result
                except Exception as exc:
                    logger.warning(f"act_confirm execute failed: {exc}")
            elif _NO.search(user_query):
                return {
                    "query": user_query, "response": "Cancelled.",
                    "query_type": "general", "next_context": None,
                    "sources": [], "num_sources": 0,
                    "retrieval_time": 0.0, "generation_time": 0.0,
                    "avg_relevance_score": 0.0, "context_length": 0,
                }

        elif _ctx_task == "report_disambiguation":
            if not egeria_authenticated:
                return _egeria_required_response(user_query)
            _candidates = (context or {}).get("candidates", [])
            _m = re.match(r'^\s*(\d+)\s*$', user_query.strip())
            chosen = None
            if _m:
                _idx = int(_m.group(1)) - 1
                if 0 <= _idx < len(_candidates):
                    chosen = _candidates[_idx]
            if not chosen:
                for _c in _candidates:
                    if _c.lower() in user_query.lower():
                        chosen = _c
                        break
            if chosen:
                try:
                    from advisor.report_pipeline import get_report_pipeline
                    result = get_report_pipeline()._execute_report(user_query, chosen,
                                                                     search_string="*", page_size=page_size,
                                                                     egeria_credentials=egeria_credentials)
                    if result:
                        result["query_type"]  = "act_report_result"
                        result["matched_spec_id"] = chosen
                        result["next_context"] = None
                        return result
                except Exception as exc:
                    logger.warning(f"report_disambiguation execute failed: {exc}")

        # ------------------------------------------------------------------ #
        # Report Spec Draft navigation (legacy fallback — draft_id without   #
        # context.task, kept for backward compatibility)                      #
        # ------------------------------------------------------------------ #
        if draft_id and draft_id.startswith("draft_report_"):
            from advisor.agents.report_spec_elicitor import get_report_spec_elicitor
            from advisor.agents.report_spec_agent import get_report_spec_agent
            elicitor = get_report_spec_elicitor()
            agent = get_report_spec_agent()
            q = user_query.strip().lower()

            if re.match(r'^(go\s+)?back\b', q):
                return elicitor.back(draft_id)
            if re.match(r'^(save\s+(&|and)\s+exit|save\s+exit)\b', q):
                return elicitor.save_and_exit(draft_id)
            if re.match(r'^(cancel|start\s+over|abandon)\b', q):
                return elicitor.cancel(draft_id)
            if re.match(r'^(restart|redo\s+q&a|redo\s+questions)\b', q):
                return elicitor.restart_qa(draft_id)
            if re.match(r'^discard\b', q):
                return elicitor.discard(draft_id)

            _exec_pattern = re.compile(
                r'^(?:execute|run|run\s+(?:the\s+)?report|go\s+ahead|proceed(?:\s+with\s+execution)?'
                r'|run\s+it|execute\s+(?:the\s+)?report|execute\s+it)\b',
                re.IGNORECASE,
            )
            if _exec_pattern.match(q):
                from advisor.report_draft import get_report_draft_manager as _rdm
                spec = _rdm().load(draft_id)
                doc_id = spec.get("doc_id") if spec else None
                
                # Fetch page_size/format override from query payload if available
                custom_params = {}
                if page_size is not None:
                    custom_params["page_size"] = page_size
                _fmt_m = re.search(r"\bfmt:'([^']+)'", user_query, re.IGNORECASE)
                _output_fmt = _fmt_m.group(1).upper() if _fmt_m else "TABLE"
                
                # Fall back to draft_id to allow previewing adhoc report spec drafts
                exec_id = doc_id if doc_id else draft_id
                logger.info(f"Draft execute command detected — executing spec/draft {exec_id}")
                return agent.execute(
                    exec_id,
                    perspective=perspective,
                    output_format=_output_fmt,
                    custom_params=custom_params if custom_params else None,
                    egeria_credentials=egeria_credentials,
                )

            return elicitor.process(draft_id, user_query, egeria_credentials=egeria_credentials)

        # ------------------------------------------------------------------ #
        # Draft navigation: route to PlanElicitor when a draft is active       #
        # ------------------------------------------------------------------ #
        if draft_id and (not query_type_override or query_type_override == 'plan'):
            from advisor.agents.governance_plan_agent import get_governance_plan_agent
            agent = get_governance_plan_agent()
            q = user_query.strip().lower()

            # Navigation commands: back / save-exit / cancel / restart / discard
            if re.match(r'^(go\s+)?back\b', q):
                return agent.back(draft_id)
            if re.match(r'^(save\s+(&|and)\s+exit|save\s+exit)\b', q):
                return agent.save_and_exit(draft_id)
            if re.match(r'^(cancel|start\s+over|abandon)\b', q):
                return agent.cancel(draft_id)
            if re.match(r'^(restart|redo\s+q&a|redo\s+questions)\b', q):
                return agent.restart_qa(draft_id)
            if re.match(r'^discard\b', q):
                return agent.discard(draft_id)
            # Template save command inside a draft: "save as template <name>"
            _tmpl_m = re.match(r'^save\s+(?:as\s+)?template\s+(.+)', q)
            if _tmpl_m:
                return agent.save_as_template(draft_id, _tmpl_m.group(1).strip())
            # "Open the editor" / "open editor" — UI navigation hint, not a plan edit
            if re.match(r'^open\s+(the\s+)?editor\b', q):
                return {
                    "query": user_query,
                    "response": (
                        "To open the Plan Editor, click the **pencil icon** (✏️) next to "
                        "the plan in the Inbox sidebar, or use the **Edit** button that "
                        "appears on the plan canvas.\n\n"
                        "You can also make changes by describing what you want here — "
                        "for example: *\"Change the project name to X\"* or "
                        "*\"Add a sub-project for Data Quality\"*."
                    ),
                    "query_type": "plan_clarification",
                    "draft_id": draft_id,
                    "can_go_back": True,
                    "navigation": ["back", "cancel"],
                    "sources": [],
                }

            # "execute" / "run" typed in chat while a draft is active — route to
            # actual plan execution rather than letting the LLM treat it as a
            # plan-modification instruction (which produces a truncated document).
            _exec_pattern = re.compile(
                r'^(?:execute|run|run\s+(?:the\s+)?plan|go\s+ahead|proceed(?:\s+with\s+execution)?'
                r'|run\s+it|execute\s+(?:the\s+)?plan|execute\s+it)\b',
                re.IGNORECASE,
            )
            if _exec_pattern.match(q):
                from advisor.governance_draft import get_draft_manager as _gdm
                spec = _gdm().load(draft_id)
                doc_id = spec.get("doc_id") if spec else None
                if doc_id:
                    logger.info(f"Draft execute command detected — executing plan {doc_id}")
                    # draft_id must be threaded through so execute() updates this
                    # draft's stored doc_id to the new post-execution outbox id —
                    # otherwise the draft is left pointing at a doc_id that gets
                    # renamed out from under it (inbox -> outbox always appends a
                    # fresh "_executed_<ts>" suffix), and reopening it later 404s.
                    # the plan confirm gate: show the plan; "yes" with its digest runs it
                    return agent.prepare_execute(doc_id, draft_id=draft_id, egeria_credentials=egeria_credentials)
                else:
                    return {
                        "query": user_query,
                        "response": (
                            "The plan hasn't been generated yet — use **Generate Plan** "
                            "on the canvas first, then **Execute** when ready."
                        ),
                        "query_type": "plan_clarification",
                        "draft_id": draft_id,
                        "can_go_back": False,
                        "navigation": [],
                        "sources": [],
                    }

            # Default: forward user response to active Q&A phase
            return agent.continue_draft(draft_id, user_query, egeria_credentials=egeria_credentials)

        # ------------------------------------------------------------------ #
        # Top-level navigation patterns (no active draft — resume by ID)      #
        # ------------------------------------------------------------------ #
        _resume_m = re.search(
            r'\bresume\s+(?:draft\s+)?(\w+)',
            user_query,
            re.IGNORECASE,
        )
        if _resume_m:
            _did = _resume_m.group(1)
            if _did.startswith("draft_report_"):
                from advisor.agents.report_spec_elicitor import get_report_spec_elicitor
                result = get_report_spec_elicitor().resume(_did)
                result.setdefault("routing_agent", "report_spec_agent")
                return result
            else:
                from advisor.agents.governance_plan_agent import get_governance_plan_agent
                result = get_governance_plan_agent().resume(_did)
                result.setdefault("routing_agent", "governance_plan_agent")
                return result

        # Template selection: "use template <name>" or "start from template <name>"
        _use_tmpl_m = re.search(
            r'\b(?:use|start\s+from|load)\s+template\s+(.+)',
            user_query,
            re.IGNORECASE,
        )
        if _use_tmpl_m:
            _tname = _use_tmpl_m.group(1).strip()
            logger.info(f"Template start requested: {_tname!r}")
            try:
                from advisor.agents.plan_elicitor import get_plan_elicitor
                result = get_plan_elicitor().start(
                    user_query, perspective=perspective, template_name=_tname,
                    egeria_credentials=egeria_credentials,
                )
                result.setdefault("routing_agent", "governance_plan_agent")
                return result
            except Exception as exc:
                logger.warning(f"Template start failed ({exc}), continuing normal routing")

        # Bare "create a plan" request — offer a choice of how to build it
        # (conversation vs. Plan Editor canvas) rather than starting
        # PlanElicitor's decomposition on a query with nothing to decompose.
        # Fires regardless of intent selector (Auto/Create/Plan) since the
        # query content itself is unambiguous; an explicit unrelated
        # selection (Report/Act/Explain/Troubleshoot) is left alone.
        _override_ok = query_type_override in (None, '', 'create', 'plan')
        if _override_ok and self._is_bare_plan_request(user_query):
            extracted_title = self._extract_plan_title(user_query)
            if self._wants_canvas_directly(user_query):
                # Explicit tool preference ("...using the canvas") — the user
                # already told us how they want to build it, so skip the
                # discuss/canvas choice (and the builder's own title modal)
                # and open a blank canvas immediately with whatever title
                # they gave.
                logger.info("Bare plan request with explicit canvas preference — opening canvas")
                from advisor.governance_draft import create_builder_draft
                spec = create_builder_draft(extracted_title or user_query[:50], perspective)
                return {
                    "query": user_query,
                    "response": (
                        f"**{spec['title']}** — plan canvas is open. Use **+ Add step** to "
                        f"browse and add Dr.Egeria commands, or ask me questions in chat."
                    ),
                    "query_type": "plan_canvas_direct",
                    "routing_agent": "create_router",
                    "draft_id": spec["draft_id"],
                    "sources": [], "num_sources": 0,
                    "retrieval_time": 0.0, "generation_time": 0.0,
                    "avg_relevance_score": 0.0, "context_length": 0,
                    "session_id": session_id,
                    "user_id": user_id,
                }

            logger.info("Bare plan request detected — offering discuss/canvas choice")
            return {
                "query": user_query,
                "response": (
                    "Sure — how would you like to build it?\n\n"
                    "- **Discuss it with me** — describe what you need in plain language "
                    "and I'll draft the steps\n"
                    "- **Open the Canvas** — start from a blank plan and add Dr.Egeria "
                    "commands directly"
                ),
                "query_type": "plan_start_choice",
                "routing_agent": "create_router",
                "navigation": ["plan_discuss", "plan_canvas"],
                "draft_id": None,
                "extracted_title": extracted_title,
                "sources": [], "num_sources": 0,
                "retrieval_time": 0.0, "generation_time": 0.0,
                "avg_relevance_score": 0.0, "context_length": 0,
                "session_id": session_id,
                "user_id": user_id,
            }

        # ------------------------------------------------------------------ #
        # Negative routing guard: interrogative forms bypass plan creation.  #
        # "What is X" / "How does Y" must never reach GovernancePlanAgent   #
        # when intent=Plan is selected accidentally.                         #
        # NOTE: 'command' (Dr.Egeria / Act) is intentionally excluded here  #
        # — DrEgeriaTemplateAgent is safe for informational queries and the  #
        # user may have explicitly chosen it via the clarification buttons.  #
        # The draft_id block above already handled active drafts; this guard #
        # fires only for new (draft-free) queries.                           #
        # ------------------------------------------------------------------ #
        if self._is_interrogative(user_query) and query_type_override in ('plan', 'act'):
            logger.info(
                f"Interrogative guard: query is informational; "
                f"overriding intent '{query_type_override}' → explanation"
            )
            query_type_override = 'explanation'

        # Dr.Egeria template guard: "Show me a Dr.Egeria template" must route
        # to DrEgeriaTemplateAgent, not ExamplesAgent, regardless of intent.
        # This fires before the intent override so 'code_search' (Show Me)
        # cannot hijack Dr.Egeria-specific template requests.
        # Process query to understand intent
        query_analysis = self.query_processor.process(user_query)
        logger.info(f"Query type: {query_analysis['query_type']}")

        # 1. Resolve perspective, intent, and route via PerspectiveRoutingEngine
        from advisor.perspective_routing import get_perspective_routing_engine
        routing_engine = get_perspective_routing_engine()
        
        # Determine intent (check override first)
        intent = query_type_override
        if not intent:
            intent_obj = query_analysis["query_type"]
            intent = intent_obj.value if hasattr(intent_obj, "value") else str(intent_obj)
            # LLM refinement fallback
            if intent == "general":
                from advisor.llm_intent_classifier import get_intent_classifier
                refined = get_intent_classifier().classify(user_query)
                if refined != "general":
                    logger.info(f"LLM intent classifier refined 'general' -> '{refined}'")
                    intent = refined
                    query_analysis = dict(query_analysis)
                    query_analysis["query_type"] = intent

        # Get routing action
        routing_action = routing_engine.route(
            query=user_query,
            intent=intent,
            perspective=perspective,
            session_id=session_id,
            user_id=user_id
        )

        logger.info(f"PerspectiveRoutingEngine: action={routing_action['action']}, details={routing_action}")

        # Add audit/context values to result if returning directly
        active_perspective = routing_action.get("active_perspective")
        applied_policy_rule = routing_action.get("applied_policy_rule")
        perspective_history = routing_action.get("perspective_history")

        if routing_action["action"] == "clarify":
            return {
                "query": user_query,
                "response": routing_action.get("clarify_message", "How would you like me to answer?"),
                "query_type": "clarification",
                "clarification_type": routing_action.get("clarification_type", "intent_choice"),
                "candidates": routing_action["candidates"],
                "candidate_intents": routing_action["candidate_intents"],
                "routing_agent": "clarification",
                "active_perspective": active_perspective,
                "applied_policy_rule": applied_policy_rule,
                "perspective_history": perspective_history,
                "sources": [],
                "num_sources": 0,
                "retrieval_time": 0.0,
                "generation_time": 0.0,
                "avg_relevance_score": 0.0,
                "context_length": 0,
                "session_id": session_id,
                "user_id": user_id,
            }

        # Handle direct agent dispatches:
        agent_name = routing_action.get("agent")

        # CLI Command Agent — explicit hey_egeria/CLI requests (see PerspectiveRoutingEngine).
        # Checked first, ahead of every intent-string branch below: the pattern classifier
        # can tag a query like "show me the hey_egeria command to create a glossary" as
        # intent="command" before role-aware routing ever runs, and the Dr.Egeria "command"
        # branch further down (`... or intent == "command"`) would otherwise catch it first
        # and wrongly require login for what's actually just a knowledge/example lookup.
        if agent_name == "cli_command_agent" or intent == "cli_command":
            logger.info(f"Routing query to CLICommandAgent")
            try:
                from advisor.agents.cli_command_agent import get_cli_command_agent
                result = get_cli_command_agent().handle(user_query, perspective=perspective)
                result.setdefault("routing_agent", "cli_command_agent")
                result.update({
                    "active_perspective": active_perspective,
                    "applied_policy_rule": applied_policy_rule,
                    "perspective_history": perspective_history,
                    "session_id": session_id,
                    "user_id": user_id,
                })
                return result
            except Exception as exc:
                logger.warning(f"CLICommandAgent failed ({exc}), falling back to RAG")

        # Egeria type-system structure ("subtypes of Collection", "project types",
        # "classifications of Project"). Checked early and unconditionally, like
        # the CLI Command Agent check above — the underlying phrasing ("project
        # types") doesn't always land on the code_intel pattern classification
        # (e.g. reversed word order isn't covered by routing.yaml substrings), so
        # this can't rely solely on `intent == "code_intel"` downstream. Safe to
        # try unconditionally: EgeriaTypeAgent.handle() itself is the real gate —
        # it returns None (falls through here) unless the query both matches a
        # type/subtype/classification phrasing AND the captured phrase resolves
        # to a real, live-confirmed Egeria type name.
        try:
            from advisor.agents.egeria_type_agent import get_egeria_type_agent
            type_result = get_egeria_type_agent().handle(user_query)
            if type_result is not None:
                logger.info("Routing query to EgeriaTypeAgent")
                type_result.setdefault("routing_agent", "egeria_type_agent")
                type_result.update({
                    "active_perspective": active_perspective,
                    "applied_policy_rule": applied_policy_rule,
                    "perspective_history": perspective_history,
                    "session_id": session_id,
                    "user_id": user_id,
                })
                return type_result
        except Exception as exc:
            logger.warning(f"EgeriaTypeAgent failed ({exc}), falling back")

        # Quantitative query shortcut
        if intent == 'quantitative':
            logger.info("Handling quantitative query with analytics module")
            path_filter = query_analysis.get('path_filter')
            return {
                "query": user_query,
                **self._quantitative_answer(user_query, path_filter, self.analytics),
                "routing_agent": "analytics",
                "active_perspective": active_perspective,
                "applied_policy_rule": applied_policy_rule,
                "perspective_history": perspective_history,
                "path_filter": path_filter,
                "session_id": session_id,
                "user_id": user_id,
            }

        # Relationship query shortcut
        if intent == 'relationship':
            # "show/list X as a table/list/mermaid" is a report-display query, not a
            # graph traversal — reclassify so it reaches the Act read-verb pipeline.
            if (re.search(r'^(?:show|list|get|display)\b', user_query, re.IGNORECASE)
                    and re.search(r'\bas\s+(?:a\s+)?(?:table|list|mermaid|json)\b', user_query, re.IGNORECASE)):
                intent = 'command'
            else:
                logger.info("Handling relationship query with relationship graph")
                response = self.relationships.answer_relationship_query(user_query)
                return {
                    "query": user_query,
                    "response": response,
                    "query_type": "relationship",
                    "routing_agent": "relationship",
                    "active_perspective": active_perspective,
                    "applied_policy_rule": applied_policy_rule,
                    "perspective_history": perspective_history,
                    "sources": [],
                    "num_sources": 0,
                    "retrieval_time": 0.0,
                    "generation_time": 0.0,
                    "avg_relevance_score": 0.0,
                    "context_length": 0,
                    "session_id": session_id,
                    "user_id": user_id,
                }

        # Plan execution match
        _exec_match = re.search(r'\bexecute(?:\s+the)?\s+plan\s+(\w+)', user_query, re.IGNORECASE)
        if _exec_match:
            if not egeria_authenticated:
                return _egeria_required_response(user_query)
            _doc_id = _exec_match.group(1)
            _dry_run = "dry" in user_query.lower()
            try:
                from advisor.agents.governance_plan_agent import get_governance_plan_agent
                _gpa_x = get_governance_plan_agent()
                # a dry run writes nothing; a real run is shown first and runs only on "yes" + digest
                result = (_gpa_x.execute(_doc_id, perspective=perspective, dry_run=True,
                                         egeria_credentials=egeria_credentials) if _dry_run
                          else _gpa_x.prepare_execute(_doc_id, egeria_credentials=egeria_credentials))
                result.setdefault("routing_agent", "governance_plan_agent")
                result.update({
                    "active_perspective": active_perspective,
                    "applied_policy_rule": applied_policy_rule,
                    "perspective_history": perspective_history,
                    "session_id": session_id,
                    "user_id": user_id,
                })
                return result
            except Exception as exc:
                logger.error(f"GovernancePlanAgent.execute failed: {exc}", exc_info=True)

        # Create intent — routes to PlanElicitor or ReportSpecElicitor based on query content
        if intent == 'create':
            from advisor.agents.create_router import route_create
            destination = route_create(user_query)
            _ctx = {
                "active_perspective": active_perspective,
                "applied_policy_rule": applied_policy_rule,
                "perspective_history": perspective_history,
                "session_id": session_id,
                "user_id": user_id,
            }
            if destination == 'plan':
                # Bare-request case is already handled earlier (before intent
                # classification narrowed things to 'create'); reaching here
                # means there's real content for PlanElicitor to decompose.
                logger.info("Create intent → PlanElicitor")
                from advisor.agents.plan_elicitor import get_plan_elicitor
                result = get_plan_elicitor().start(user_query, perspective=perspective,
                                                    egeria_credentials=egeria_credentials)
                result.setdefault("routing_agent", "plan_elicitor")
                result.update(_ctx)
                return result
            elif destination == 'report_spec':
                logger.info("Create intent → ReportSpecElicitor")
                from advisor.agents.report_spec_elicitor import get_report_spec_elicitor
                result = get_report_spec_elicitor().start(user_query, perspective=perspective,
                                                            egeria_credentials=egeria_credentials)
                result.setdefault("routing_agent", "report_spec_agent")
                result.update(_ctx)
                return result
            else:
                # Ambiguous — ask the user
                logger.info("Create intent → disambiguation")
                result = {
                    "query": user_query,
                    "response": (
                        "I can help you create something — which would you like to build?\n\n"
                        "- **Governance Plan** — a multi-step plan for a governance task "
                        "(creating zones, glossaries, policies, assigning stewards, etc.)\n"
                        "- **Report Spec** — a reusable report definition that fetches and "
                        "displays Egeria metadata (glossaries, assets, projects, etc.)"
                    ),
                    "query_type": "create_disambiguation",
                    "routing_agent": "create_router",
                    "navigation": ["create_plan", "create_report_spec"],
                    "draft_id": None,
                    "sources": [], "num_sources": 0,
                    "retrieval_time": 0.0, "generation_time": 0.0,
                    "avg_relevance_score": 0.0, "context_length": 0,
                }
                result.update(_ctx)
                return result

        if agent_name == "governance_plan_agent" or intent == 'plan':
            logger.info("Handling plan query via GovernancePlanAgent")
            try:
                from advisor.agents.governance_plan_agent import get_governance_plan_agent
                result = get_governance_plan_agent().handle(user_query, perspective=perspective,
                                                              egeria_credentials=egeria_credentials)
                result.setdefault("routing_agent", "governance_plan_agent")
                result.update({
                    "active_perspective": active_perspective,
                    "applied_policy_rule": applied_policy_rule,
                    "perspective_history": perspective_history,
                    "session_id": session_id,
                    "user_id": user_id,
                })
                return result
            except Exception as exc:
                logger.warning(f"GovernancePlanAgent failed ({exc}), falling back to RAG")

        # Report Spec execution or build match
        _exec_report_match = re.search(r'\bexecute\s+(?:the\s+)?report\s+spec\s+(\w+)', user_query, re.IGNORECASE)
        if _exec_report_match:
            if not egeria_authenticated:
                return _egeria_required_response(user_query)
            _doc_id = _exec_report_match.group(1)
            _dry_run = "dry" in user_query.lower()
            _fmt_m = re.search(r"\bfmt:'([^']+)'", user_query, re.IGNORECASE)
            _output_fmt = _fmt_m.group(1).upper() if _fmt_m else "TABLE"
            try:
                custom_params = {}
                if page_size is not None:
                    custom_params["page_size"] = page_size
                from advisor.agents.report_spec_agent import get_report_spec_agent
                result = get_report_spec_agent().execute(
                    _doc_id, perspective=perspective, dry_run=_dry_run, output_format=_output_fmt,
                    custom_params=custom_params if custom_params else None,
                    egeria_credentials=egeria_credentials,
                )
                result.setdefault("routing_agent", "report_spec_agent")
                result.update({
                    "active_perspective": active_perspective,
                    "applied_policy_rule": applied_policy_rule,
                    "perspective_history": perspective_history,
                    "session_id": session_id,
                    "user_id": user_id,
                })
                return result
            except Exception as exc:
                logger.error("ReportSpecAgent.execute failed: {}", str(exc), exc_info=True)

        _build_report_spec_pattern = re.compile(
            r'\b(?:(?:create|build|define|make|new|design)\s+(?:a\s+)?report\s+spec'
            r'|(?:define|design)\s+(?:a\s+)?report)\b',
            re.IGNORECASE
        )
        # Also catch natural-language "show/list X with their Y and Z" requests.
        # These ask for specific columns beyond what any pre-built report offers, so
        # they should go to the Report Spec Builder rather than the MCP report pipeline.
        _custom_columns_pattern = re.compile(
            r'\b(?:show|list|get|find|display|give me|fetch)\b'
            r'.{0,60}'
            r'\b(?:with (?:their|the|its)|including|containing)\b'
            r'.{0,60}'
            r'\b(?:and|,)\b',
            re.IGNORECASE | re.DOTALL
        )
        _wants_elicitor = (
            _build_report_spec_pattern.search(user_query)
            or (intent == "report" and "spec" in user_query.lower()
                and any(w in user_query.lower() for w in ("create", "build", "define")))
            or (intent == "report" and _custom_columns_pattern.search(user_query))
        )
        if _wants_elicitor:
            logger.info("Routing to ReportSpecElicitor to build a new report spec")
            from advisor.agents.report_spec_elicitor import get_report_spec_elicitor
            result = get_report_spec_elicitor().start(user_query, perspective=perspective,
                                                            egeria_credentials=egeria_credentials)
            result.setdefault("routing_agent", "report_spec_agent")
            result.update({
                "active_perspective": active_perspective,
                "applied_policy_rule": applied_policy_rule,
                "perspective_history": perspective_history,
                "session_id": session_id,
                "user_id": user_id,
            })
            return result

        # Report Pipeline
        if agent_name == "report_pipeline" or intent == "report":
            if not egeria_authenticated:
                return _egeria_required_response(user_query)
            logger.info("Handling report query via MCP report pipeline")
            try:
                from advisor.report_pipeline import get_report_pipeline
                result = get_report_pipeline().process(user_query, perspective=perspective, page_size=page_size,
                                                         egeria_credentials=egeria_credentials)
                result.setdefault("routing_agent", "report_pipeline")
                result.update({
                    "active_perspective": active_perspective,
                    "applied_policy_rule": applied_policy_rule,
                    "perspective_history": perspective_history,
                    "session_id": session_id,
                    "user_id": user_id,
                })
                return result
            except Exception as e:
                logger.warning(f"Report pipeline failed ({e}), falling back to RAG")

        # Act — "run report <name>" explicit dispatch (from confirm-step buttons).
        # Must be checked BEFORE the read-verb guard so it never falls to DrEgeriaActionAgent.
        _RUN_NOW_RE = re.compile(
            r'^run\s+report\s+(.+?)(?:\s+filter:\'([^\']*)\')?(?:\s+element_type:\'([^\']*)\')?(?:\s+fmt:\'([^\']*)\')?$',
            re.IGNORECASE,
        )
        if intent == "command" and _RUN_NOW_RE.match(user_query.strip()):
            if not egeria_authenticated:
                return _egeria_required_response(user_query)
            from advisor.report_pipeline import get_report_pipeline
            pipeline = get_report_pipeline()
            m = _RUN_NOW_RE.match(user_query.strip())
            rname        = m.group(1).strip()
            filt         = (m.group(2) or "").strip() or "*"
            element_type = (m.group(3) or "").strip()
            fmt_override = (m.group(4) or "").strip() or None
            extra_params = {}
            if element_type:
                extra_params["metadata_element_type"] = element_type
            try:
                result = pipeline._execute_report(
                    user_query, rname,
                    search_string=filt, page_size=page_size,
                    extra_params=extra_params or None,
                    output_type=fmt_override or "TABLE",
                    egeria_credentials=egeria_credentials,
                )
                if result:
                    result["query_type"] = "act_report_result"
                    result["matched_spec_id"] = rname
                    result.setdefault("routing_agent", "act_pipeline")
                    result.update({
                        "active_perspective": active_perspective,
                        "applied_policy_rule": applied_policy_rule,
                        "perspective_history": perspective_history,
                        "session_id": session_id,
                        "user_id": user_id,
                    })
                    return result
            except Exception as e:
                logger.warning(f"Act direct run failed: {e}")

        # Act — read-verb queries show a confirm step before running.
        # Write verbs (create/update/assign/…) fall through to DrEgeriaActionAgent below.
        _ACT_READ_RE = re.compile(
            r'^(?:show|list|get|find|display|view|fetch|print|give\s+me|report\s+on|pull)\b',
            re.IGNORECASE,
        )
        if intent == "command" and _ACT_READ_RE.match(user_query.strip()):
            if not egeria_authenticated:
                return _egeria_required_response(user_query)

            logger.info("Act + read verb — finding best report match (confirm step)")
            try:
                from advisor.report_pipeline import get_report_pipeline
                pipeline = get_report_pipeline()

                # Extract the candidate entity-type token from the NL query.
                # "show all dataHubs" → element_type=dataHub, search_string=*
                # "show glossaries named Inventory" → element_type='', search_string=Inventory
                # Heuristic: the token immediately after the verb/quantifier and before
                # "with/including/named" is the type; tokens after are search filters.
                # Extract output format from natural language, e.g. "as a table", "as mermaid"
                def _extract_format(query: str) -> str:
                    _FMT_RE = re.compile(
                        r'\bas\s+(?:a\s+)?'
                        r'(table|list|mermaid(?:\s+(?:graph|diagram|chart))?|json|report|form|md|markdown)\b',
                        re.IGNORECASE,
                    )
                    m = _FMT_RE.search(query)
                    if not m:
                        return "TABLE"   # default for ad-hoc reports
                    raw = m.group(1).lower().split()[0]  # take first word (e.g. "mermaid" from "mermaid graph")
                    return {"markdown": "REPORT", "md": "REPORT"}.get(raw, raw.upper())

                def _extract_type_and_filter(query: str, report_name: str):
                    _VERB = {"show", "list", "get", "find", "display", "view", "fetch",
                             "me", "all", "the", "a", "an", "my", "our", "their", "its",
                             "active", "draft", "approved", "deprecated", "proposed"}
                    _SEPARATOR = {"with", "named", "called", "where", "that", "which",
                                  "including", "containing", "for", "of", "in", "by"}
                    _STOP = _VERB | _SEPARATOR | {"and", "or", "from", "to"}
                    if report_name:
                        _STOP.update(w.lower() for w in re.split(r'[\s\-_]', report_name))

                    tokens = re.findall(r"[A-Za-z0-9]+", query)

                    # Content words before the first separator — candidate entity-type phrase.
                    content_words = []
                    separator_idx = len(tokens)
                    for i, tok in enumerate(tokens):
                        tl = tok.lower()
                        if tl in _SEPARATOR:
                            separator_idx = i
                            break
                        if tl not in _VERB:
                            content_words.append(tok)

                    # Resolve against the known Egeria type-name registry first, trying the
                    # longest matching phrase ("external reference(s)" -> ExternalReference,
                    # "data product(s)" -> DigitalProduct) so multi-word type names aren't
                    # truncated to their first word — which Egeria's findElements then rejects
                    # as an unrecognized type. Falls back to the bare first word if the
                    # registry has no match (e.g. an ad-hoc type not in the catalog).
                    from advisor.egeria_type_registry import resolve_type_name
                    element_type = resolve_type_name(content_words) or (content_words[0] if content_words else "")

                    # search_string: first meaningful word after the separator.
                    search_string = "*"
                    for tok in tokens[separator_idx + 1:]:
                        tl = tok.lower()
                        if len(tok) > 2 and tl not in _STOP:
                            search_string = tok
                            break
                    return element_type, search_string

                # find_specs → rank → pick best without executing
                specs = pipeline.find_specs(user_query, perspective=perspective)
                if specs:
                    ranked = pipeline._rank_specs(specs)
                    best = ranked[0]
                    report_name = (
                        best.get("report_spec") or best.get("name") or
                        best.get("spec_name") or best.get("report_name") or ""
                    )
                else:
                    report_name = ""

                element_type, search_string = _extract_type_and_filter(user_query, report_name)
                output_fmt = _extract_format(user_query)

                if report_name:
                    parts = [f"I'll run **{report_name}**"]
                    if element_type:
                        parts.append(f"element type: `{element_type}`")
                    if search_string != "*":
                        parts.append(f"filter: `{search_string}`")
                    parts.append(f"format: `{output_fmt}`")
                    response_text = (
                        " — ".join(parts) + "\n\n"
                        "Adjust the fields below before running, or use **Modify spec** "
                        "to edit the report's columns and parameters."
                    )
                else:
                    response_text = (
                        "I couldn't find a matching report spec for that query.\n\n"
                        "Try **Create** to build a new report spec, or rephrase to match a catalog report name."
                    )
                if getattr(specs, "note", ""):          # TR-34 R3 W1: the search stopped at EA's local copy
                    response_text += f"\n\n*{specs.note}*"

                result = {
                    "query": user_query,
                    "response": response_text,
                    "query_type": "act_confirm",
                    "matched_spec_id": report_name or None,
                    "extracted_filter": search_string if search_string != "*" else "",
                    "extracted_element_type": element_type,
                    "extracted_format": output_fmt,
                    "routing_agent": "act_pipeline",
                    "next_context": {
                        "task": "act_confirm",
                        "spec_id": report_name or "",
                        "filter": search_string if search_string != "*" else "*",
                        "fmt": output_fmt,
                    } if report_name else None,
                    "sources": [], "num_sources": 0,
                    "retrieval_time": 0.0, "generation_time": 0.0,
                    "avg_relevance_score": 0.0, "context_length": 0,
                    "active_perspective": active_perspective,
                    "applied_policy_rule": applied_policy_rule,
                    "perspective_history": perspective_history,
                    "session_id": session_id,
                    "user_id": user_id,
                }
                return result
            except Exception as e:
                logger.warning(f"Act ReportPipeline failed ({e})")
                return {
                    "query": user_query,
                    "response": (
                        "I couldn't find a matching report spec for that query.\n\n"
                        "Try **Create** to build a new report spec, or rephrase to match a catalog report name."
                    ),
                    "query_type": "act_confirm",
                    "matched_spec_id": None,
                    "next_context": None,
                    "sources": [], "num_sources": 0,
                    "retrieval_time": 0.0, "generation_time": 0.0,
                    "avg_relevance_score": 0.0, "context_length": 0,
                    "active_perspective": active_perspective,
                    "applied_policy_rule": applied_policy_rule,
                    "perspective_history": perspective_history,
                    "session_id": session_id,
                    "user_id": user_id,
                }

        # Dr.Egeria Action / Template
        if agent_name == "dr_egeria_agent" or agent_name == "dre_template_agent" or intent == "command":
            _template_signals = ("template", "sample", "example", "show me", "give me", "how to", "how do i", "demonstrate", "illustrate")
            wants_template = any(sig in user_query.lower() for sig in _template_signals)
            if wants_template:
                logger.info("Handling Dr.Egeria template request via DrEgeriaTemplateAgent")
                try:
                    from advisor.agents.dre_template_agent import get_dre_template_agent
                    result = get_dre_template_agent().handle(user_query, perspective=perspective)
                    result.setdefault("routing_agent", "dre_template_agent")
                    result.update({
                        "active_perspective": active_perspective,
                        "applied_policy_rule": applied_policy_rule,
                        "perspective_history": perspective_history,
                        "session_id": session_id,
                        "user_id": user_id,
                    })
                    return result
                except Exception as e:
                    logger.warning(f"DrEgeriaTemplateAgent failed ({e}), falling back")
            if not egeria_authenticated:
                return _egeria_required_response(user_query)
            logger.info("Handling command query via DrEgeriaActionAgent")
            try:
                from advisor.agents.dr_egeria_agent import get_dr_egeria_agent
                result = get_dr_egeria_agent().handle(user_query, dry_run=dry_run,
                                                        egeria_credentials=egeria_credentials)
                result.setdefault("routing_agent", "dr_egeria_agent")
                result.update({
                    "active_perspective": active_perspective,
                    "applied_policy_rule": applied_policy_rule,
                    "perspective_history": perspective_history,
                    "session_id": session_id,
                    "user_id": user_id,
                })
                return result
            except Exception as e:
                logger.warning(f"DrEgeriaActionAgent failed ({e}), falling back to RAG")

        # Before falling through to RAG, offer alternatives when there is a medium-confidence
        # report match — prevents silent wrong-route responses.
        if not query_type_override or query_type_override == 'report':
            alt = self._report_alternatives(user_query)
            if alt is not None:
                alt.update({
                    "active_perspective": active_perspective,
                    "applied_policy_rule": applied_policy_rule,
                    "perspective_history": perspective_history,
                    "session_id": session_id,
                    "user_id": user_id,
                })
                return alt

        # Examples Agent / Code Help
        if agent_name == "examples_agent" or intent in ("code_help", "code_search", "example"):
            logger.info(f"Routing query to ExamplesAgent")
            try:
                from advisor.agents.examples_agent import get_examples_agent
                result = get_examples_agent().handle(user_query, perspective=perspective)
                result.setdefault("routing_agent", "examples_agent")
                result.update({
                    "active_perspective": active_perspective,
                    "applied_policy_rule": applied_policy_rule,
                    "perspective_history": perspective_history,
                    "session_id": session_id,
                    "user_id": user_id,
                })
                return result
            except Exception as exc:
                logger.warning(f"ExamplesAgent failed ({exc}), falling back to RAG")

        # Code Intel Agent
        if agent_name == "code_intel_agent" or intent == "code_intel":
            logger.info(f"Routing query to CodeIntelAgent")
            try:
                from advisor.agents.code_intel_agent import get_code_intel_agent
                result = get_code_intel_agent().handle(user_query)
                result.setdefault("routing_agent", "code_intel_agent")
                result.update({
                    "active_perspective": active_perspective,
                    "applied_policy_rule": applied_policy_rule,
                    "perspective_history": perspective_history,
                    "session_id": session_id,
                    "user_id": user_id,
                })
                return result
            except Exception as exc:
                logger.warning(f"CodeIntelAgent failed ({exc}), falling back to RAG")

        # Doc Agent / Explanation
        if agent_name == "doc_agent" or intent in ("explanation", "best_practice", "comparison", "debugging"):
            logger.info(f"Routing query to DocAgent")
            try:
                from advisor.agents.doc_agent import get_doc_agent
                result = get_doc_agent().handle(user_query, mode=intent)
                result.setdefault("routing_agent", "doc_agent")
                result.update({
                    "active_perspective": active_perspective,
                    "applied_policy_rule": applied_policy_rule,
                    "perspective_history": perspective_history,
                    "session_id": session_id,
                    "user_id": user_id,
                })
                return result
            except Exception as exc:
                logger.warning(f"DocAgent failed ({exc}), falling back to RAG")

        # ── Structural code-symbol shortcut ────────────────────────────────
        if intent in ('quantitative', 'code_help', 'code_search', 'general', 'explanation') \
                and not query_type_override \
                and self._is_structural_query(user_query):
            result = self._handle_structural_query(user_query, path_filter=query_analysis.get('path_filter'))
            if result:
                result.update({
                    "active_perspective": active_perspective,
                    "applied_policy_rule": applied_policy_rule,
                    "perspective_history": perspective_history,
                    "session_id": session_id,
                    "user_id": user_id,
                })
                return result

        # ── Command-discovery shortcut ──────────────────────────────────────
        if intent in ('explanation', 'command', 'code_help', 'code_search', 'general') \
                and self._is_command_discovery(user_query):
            result = self._handle_command_discovery(user_query)
            if result:
                result.update({
                    "active_perspective": active_perspective,
                    "applied_policy_rule": applied_policy_rule,
                    "perspective_history": perspective_history,
                    "session_id": session_id,
                    "user_id": user_id,
                })
                return result

        # Fallback to RAG
        feedback_adjustments = routing_engine.get_feedback_adjustments()
        result = self._run_rag_fallback(
            user_query=user_query,
            query_analysis=query_analysis,
            include_context=include_context,
            perspective=perspective,
            boosted_collections=routing_action.get("boosts"),
            feedback_adjustments=feedback_adjustments
        )
        result.update({
            "active_perspective": active_perspective,
            "applied_policy_rule": applied_policy_rule,
            "perspective_history": perspective_history,
            "session_id": session_id,
            "user_id": user_id,
        })
        return result

    # ---------------------------------------------------------------------- #
    # RAG fallback — retrieve context, build prompt, generate response        #
    # ---------------------------------------------------------------------- #

    def _run_rag_fallback(
        self,
        user_query: str,
        query_analysis: Dict[str, Any],
        include_context: bool,
        perspective: Optional[str],
        boosted_collections: Optional[List[str]] = None,
        feedback_adjustments: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """Retrieve context, build prompt, and generate a response (blocking)."""
        search_strategy = query_analysis["search_strategy"]
        prioritize_docs = query_analysis.get("prioritize_docs", False)
        offer_examples  = query_analysis.get("offer_examples", False)

        # Retrieve
        retrieval_start = time.time()
        if include_context:
            context, sources = self.retriever.retrieve_and_build_context(
                query=query_analysis["enhanced_query"],
                top_k=search_strategy["top_k"],
                min_score=search_strategy["min_score"],
                format_style=search_strategy["format_style"],
                prioritize_docs=prioritize_docs,
                intent=query_analysis["query_type"],
                boosted_collections=boosted_collections,
                feedback_adjustments=feedback_adjustments,
            )
        else:
            context, sources = "", []
        retrieval_time = time.time() - retrieval_start

        collections_searched = getattr(self.retriever, "_last_collections_searched", [])

        # TR-49: nothing retrieved and EVERY collection empty is an unbuilt knowledge base, not a model failure.
        # Say so, with the commands that build it, instead of the LLM's "I don't have enough information".
        if include_context and not sources:
            outcome = getattr(self.retriever, "_last_outcome", None)
            if outcome is not None and getattr(outcome, "reason", "") in ("never_ingested", "collection_empty"):
                from advisor import knowledge_base
                words = knowledge_base.not_built(getattr(self.retriever, "vector_store", None))
                if words:
                    return {
                        "query": user_query, "response": words, "query_type": query_analysis["query_type"],
                        "routing_agent": "rag_fallback", "sources": [], "num_sources": 0,
                        "retrieval_time": retrieval_time, "generation_time": 0.0, "avg_relevance_score": 0.0,
                        "context_length": 0,
                        "context_diagnosis": {**outcome.as_dict(), "reason": "knowledge_base_not_built",
                                              "message": words},
                    }

        # Build prompt
        prompt_manager = get_prompt_manager()
        query_type_enum = (
            QueryType(query_analysis["query_type"])
            if isinstance(query_analysis["query_type"], str)
            else query_analysis["query_type"]
        )
        effective_query = self._add_perspective_prefix(user_query, perspective)
        prompt = prompt_manager.build_prompt(
            user_query=effective_query,
            context=context,
            query_type=query_type_enum,
            collections_searched=collections_searched,
            offer_examples=offer_examples,
        )
        primary_collection = collections_searched[0] if collections_searched else None
        system_prompt = prompt_manager.get_system_prompt(
            primary_collection=primary_collection,
            perspective=perspective,
        )

        # Generate (thread-local streaming hook fires automatically if set)
        generation_start = time.time()
        response = self.llm_client.generate(
            prompt=prompt,
            system=system_prompt,
            temperature=self.rag_config.generation.temperature,
            max_tokens=self.rag_config.generation.max_tokens,
        )
        generation_time = time.time() - generation_start

        avg_relevance_score = 0.0
        if sources:
            scores = [
                s.score if hasattr(s, "score") else s.get("score", 0.0)
                for s in sources
            ]
            avg_relevance_score = sum(scores) / len(scores) if scores else 0.0

        result = {
            "query": user_query,
            "response": response,
            "query_type": query_analysis["query_type"],
            "routing_agent": "rag_fallback",
            "sources": sources,
            "num_sources": len(sources),
            "retrieval_time": retrieval_time,
            "generation_time": generation_time,
            "avg_relevance_score": avg_relevance_score,
            "context_length": len(context),
        }
        # Additive: when retrieval found nothing, expose *why* as structured
        # data (not just folded into the prompt text) so a UI or API
        # consumer can render the distinction directly rather than relying
        # on the LLM to have faithfully relayed it. None on the (overwhelmingly
        # common) non-empty path — no existing consumer reads this key today.
        # See advisor/retrieval_outcome.py.
        last_outcome = getattr(self.retriever, "_last_outcome", None)
        if last_outcome is not None:
            result["context_diagnosis"] = last_outcome.as_dict()
        logger.info(f"Generated response: {len(response)} chars from {len(sources)} sources")
        return result

    @staticmethod
    def _add_perspective_prefix(query: str, perspective: Optional[str]) -> str:
        if not perspective:
            return query
        from advisor.perspective_manager import display_name_for_key
        label = display_name_for_key(perspective) or perspective.replace("_", " ").title()
        return f"[User role: {label}]\n{query}"

    # ---------------------------------------------------------------------- #
    # Streaming query                                                          #
    # ---------------------------------------------------------------------- #

    def query_stream(
        self,
        user_query: str,
        include_context: bool = True,
        query_type_override: Optional[str] = None,
        perspective: Optional[str] = None,
        page_size: Optional[int] = None,
        draft_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        egeria_authenticated: bool = True,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        egeria_credentials: Optional[Dict[str, str]] = None,
    ) -> Iterator[str]:
        """
        Run the full pipeline and yield SSE-formatted strings.

        Event sequence:
          data: {"type":"start","query":"..."}          (immediately)
          data: {"type":"token","text":"..."}           (per LLM token — only for streamable paths)
          data: {"type":"done","result":{...}}          (when complete)

        Non-streamable paths (report, plan generation, MCP commands) skip the
        token events and emit start → done with no intermediate events.
        """
        from advisor.llm_client import _stream_local

        token_q: "queue.Queue[Optional[str]]" = queue.Queue()
        result_holder: List[Dict[str, Any]] = []
        error_holder:  List[Exception] = []

        def _on_token(t: str) -> None:
            token_q.put(t)

        def _worker() -> None:
            _stream_local.on_token = _on_token
            try:
                result = self._process_query(
                    user_query=user_query,
                    include_context=include_context,
                    dry_run=False,
                    query_type_override=query_type_override,
                    perspective=perspective,
                    page_size=page_size,
                    draft_id=draft_id,
                    context=context,
                    egeria_authenticated=egeria_authenticated,
                    session_id=session_id,
                    user_id=user_id,
                    egeria_credentials=egeria_credentials,
                )
                result_holder.append(result)
            except Exception as exc:
                logger.error("query_stream worker error: {}", str(exc), exc_info=True)
                error_holder.append(exc)
            finally:
                _stream_local.on_token = None
                token_q.put(None)  # sentinel

        # Emit start immediately so the UI can show a spinner
        yield f"data: {json.dumps({'type': 'start', 'query': user_query})}\n\n"

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

        # Forward tokens as they arrive
        while True:
            token = token_q.get()
            if token is None:
                break
            yield f"data: {json.dumps({'type': 'token', 'text': token})}\n\n"

        t.join()

        if error_holder:
            err_result = {
                "query": user_query,
                "response": f"An error occurred: {error_holder[0]}",
                "query_type": "general",
                "routing_agent": "error",
                "sources": [], "num_sources": 0,
                "retrieval_time": 0.0, "generation_time": 0.0,
                "avg_relevance_score": 0.0, "context_length": 0,
            }
            yield f"data: {json.dumps({'type': 'done', 'result': err_result})}\n\n"
        elif result_holder:
            result = result_holder[0]
            # Sources may contain non-serialisable SearchResult objects — normalise them
            result = _serialise_result(result)
            # Add intent metadata (mirrors what /api/query does synchronously)
            from advisor.web.app import _intent_meta  # lazy import avoids circular dep
            result.setdefault("intent", _intent_meta(result.get("query_type", "general")))
            yield f"data: {json.dumps({'type': 'done', 'result': result})}\n\n"

        yield "data: [DONE]\n\n"

    def _record_local_metrics(self, result: Dict[str, Any]):
        """Record metrics in local terminal dashboard database."""
        try:
            # Extract primary collection name
            collection_name = "N/A"
            if result.get("sources"):
                # Use the actual collection name from the first source
                first_source = result["sources"][0]
                # MultiCollectionStore adds '_collection' to metadata
                if hasattr(first_source, "metadata"):
                    collection_name = first_source.metadata.get("_collection") or first_source.metadata.get("collection", "N/A")
                elif isinstance(first_source, dict):
                    collection_name = first_source.get("_collection") or first_source.get("collection", "N/A")
            
            # Use record_query directly to avoid context manager nesting issues
            from advisor.metrics_collector import QueryMetric
            import json
            
            # Map query_type to string if it's an enum
            query_type = result.get("query_type", "GENERAL")
            if hasattr(query_type, "value"):
                query_type = query_type.value
            
            # Prepare source metadata for storage
            sources_data = []
            for source in result.get("sources", []):
                source_info = {
                    "score": source.score if hasattr(source, "score") else source.get("score", 0.0),
                    "collection": source.metadata.get("_collection") if hasattr(source, "metadata") else source.get("_collection", "unknown"),
                    "file": source.metadata.get("source") if hasattr(source, "metadata") else source.get("source", "unknown")
                }
                sources_data.append(source_info)
            
            metric = QueryMetric(
                timestamp=time.time(),
                query_text=result["query"],
                collection_name=collection_name,
                latency_ms=(result.get("retrieval_time", 0) + result.get("generation_time", 0)) * 1000,
                query_type=str(query_type).upper(),
                cache_hit=result.get("cache_hit", False),
                success=True,
                result_count=result.get("num_sources", 0),
                search_time_ms=result.get("retrieval_time", 0) * 1000,
                llm_time_ms=result.get("generation_time", 0) * 1000,
                avg_relevance_score=result.get("avg_relevance_score", 0.0),
                sources_json=json.dumps(sources_data) if sources_data else None,
                active_perspective=result.get("active_perspective"),
                resolved_intent=result.get("query_type"),
                routing_agent=result.get("routing_agent"),
                applied_policy_rule=result.get("applied_policy_rule"),
                perspective_history=result.get("perspective_history"),
                session_id=result.get("session_id"),
                user_id=result.get("user_id"),
            )
            
            self.metrics_collector.record_query(metric)
            
            # Log sources to MLflow if enabled
            if sources_data and self.mlflow_tracker:
                try:
                    self.mlflow_tracker.log_query_sources(
                        query_text=result["query"],
                        sources=sources_data,
                        avg_relevance_score=result.get("avg_relevance_score", 0.0),
                        collection_name=collection_name
                    )
                except Exception as e:
                    logger.warning(f"Failed to log sources to MLflow: {e}")
                
            # Update collection health synchronously for visibility
            if collection_name != "N/A":
                from advisor.collection_config import get_collection
                coll_config = get_collection(collection_name)
                if coll_config:
                    self.metrics_collector.record_collection_health(CollectionHealth(
                        collection_name=collection_name,
                        last_check=time.time(),
                        entity_count=result.get("num_sources", 0),
                        health_score=1.0,
                        storage_size_mb=0.0,
                        last_update=time.time(),
                        status="healthy"
                    ))
        except Exception as e:
            logger.warning(f"Failed to record local metrics: {e}")

    def _get_system_prompt(self) -> str:
        """Get system prompt for the LLM."""
        return """You are an expert assistant for the Egeria Python library (pyegeria).

CRITICAL RULES - FOLLOW EXACTLY:

1. ONLY use information from the provided code context
2. If the context doesn't contain the answer, say: "I don't have enough information in the provided context to answer that question accurately."
3. ALWAYS cite specific files, classes, and methods from the context
4. Be technical and specific - include class names, method signatures, and parameters
5. When showing code, make it complete and runnable
6. Do NOT make up or infer information not in the context
7. Do NOT use general knowledge about Python or other libraries

RESPONSE FORMAT:
- Start with a direct answer
- Provide specific code examples from the context
- Cite sources: "From [file_path]: [class/method]"
- If showing usage, include imports and setup

Remember: Your knowledge is LIMITED to the provided context. If it's not in the context, you don't know it."""

    def _build_prompt(
        self,
        user_query: str,
        context: str,
        query_type: str,
        offer_examples: bool = False
    ) -> str:
        """Build the complete prompt for the LLM."""
        if context:
            # Build follow-up suggestion if needed
            followup = ""
            if offer_examples:
                followup = """

---

After answering, ask the user if they would like to see:
- A Python code example using pyegeria
- A Java implementation example
- A REST API call example

Format: "Would you like to see an example? I can show you: [Python/Java/REST API]"
"""
            
            prompt = f"""# CODE CONTEXT FROM EGERIA LIBRARY

{context}

# USER QUESTION

{user_query}

# YOUR TASK

Answer the question using ONLY the code context above. Follow these rules:

1. Use ONLY information from the context - do not add external knowledge
2. Cite specific files, classes, and methods from the context
3. If showing code, make it complete with imports
4. If the context doesn't answer the question, say so explicitly
5. Be specific and technical - include parameter names, types, return values
6. Focus on conceptual explanation first, then offer code examples{followup}

Example good response:
"To create a glossary, use the GlossaryManager class from pyegeria/omvs/glossary_manager.py:

```python
from pyegeria import GlossaryManager

glossary_mgr = GlossaryManager(
    view_server="qs-view-server",
    platform_url="https://localhost:9443",
    user_id="garygeeke",
    user_pwd="<password>",
)
glossary_mgr.create_egeria_bearer_token()

glossary_guid = glossary_mgr.create_glossary(
    display_name="My Glossary",
    description="Business vocabulary",
)
```

Source: pyegeria/omvs/glossary_manager.py - GlossaryManager.create_glossary()"

Now answer the user's question following this format."""
        else:
            prompt = f"""# USER QUESTION

{user_query}

# IMPORTANT

No code context is available for this question. You should respond:

"I don't have access to the specific code context needed to answer this question accurately. Please try rephrasing your question or asking about a specific Egeria concept, class, or method."

Do NOT attempt to answer from general knowledge."""

        return prompt

    def chat(
        self,
        messages: List[Dict[str, str]],
        include_context: bool = True
    ) -> Dict[str, Any]:
        """
        Multi-turn chat interface.

        Args:
            messages: List of message dicts with 'role' and 'content'
            include_context: Whether to retrieve context for last message

        Returns:
            Dictionary with response and metadata
        """
        if not messages:
            raise ValueError("Messages list cannot be empty")

        # Get last user message
        last_message = messages[-1]["content"]

        # Process like a regular query
        result = self.query(last_message, include_context=include_context)

        return result

    def explain_code(
        self,
        code_snippet: str,
        context: Optional[str] = None,
        track_metrics: bool = True
    ) -> str:
        """
        Explain a code snippet.

        Args:
            code_snippet: Code to explain
            context: Optional additional context
            track_metrics: Whether to track with MLflow

        Returns:
            Explanation text
        """
        if track_metrics:
            with self.mlflow_tracker.track_operation(
                operation_name="explain_code",
                params={
                    "code_length": len(code_snippet),
                    "has_context": context is not None
                }
            ) as tracker:
                generation_start = time.time()

                prompt = f"""Please explain the following code:

```python
{code_snippet}
```
"""

                if context:
                    prompt += f"\n\nAdditional context: {context}"

                response = self.llm_client.generate(
                    prompt=prompt,
                    system=self._get_system_prompt(),
                    temperature=0.3  # Lower temperature for explanations
                )

                generation_time = time.time() - generation_start

                tracker.log_metrics({
                    "response_length": len(response),
                    "generation_time": generation_time
                })

                return response
        else:
            prompt = f"""Please explain the following code:

```python
{code_snippet}
```
"""

            if context:
                prompt += f"\n\nAdditional context: {context}"

            response = self.llm_client.generate(
                prompt=prompt,
                system=self._get_system_prompt(),
                temperature=0.3  # Lower temperature for explanations
            )

            return response

    def find_similar_code(
        self,
        code_snippet: str,
        top_k: int = 5,
        track_metrics: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Find code similar to a given snippet.

        Args:
            code_snippet: Code to find similar examples for
            top_k: Number of results
            track_metrics: Whether to track with MLflow

        Returns:
            List of similar code snippets
        """
        if track_metrics:
            with self.mlflow_tracker.track_operation(
                operation_name="find_similar_code",
                params={
                    "code_length": len(code_snippet),
                    "top_k": top_k
                }
            ) as tracker:
                results = self.retriever.get_similar_code(
                    code_snippet=code_snippet,
                    top_k=top_k
                )

                # Calculate average similarity score (results are now dictionaries)
                avg_similarity_score = 0.0
                if results:
                    avg_similarity_score = sum(r["score"] for r in results) / len(results)

                tracker.log_metrics({
                    "num_results": len(results),
                    "avg_similarity_score": avg_similarity_score
                })

                return results
        else:
            return self.retriever.get_similar_code(
                code_snippet=code_snippet,
                top_k=top_k
            )

    def get_file_summary(
        self,
        file_path: str,
        track_metrics: bool = True
    ) -> str:
        """
        Get a summary of a file's contents.

        Args:
            file_path: Path to file
            track_metrics: Whether to track with MLflow

        Returns:
            Summary text
        """
        if track_metrics:
            with self.mlflow_tracker.track_operation(
                operation_name="get_file_summary",
                params={
                    "file_path": file_path
                }
            ) as tracker:
                generation_start = time.time()

                # Get file context
                context = self.retriever.get_file_context(file_path)

                # Generate summary
                prompt = f"""Please provide a concise summary of this file's purpose and main components:

{context}

Focus on:
1. Main purpose of the file
2. Key classes/functions
3. Important functionality"""

                response = self.llm_client.generate(
                    prompt=prompt,
                    system=self._get_system_prompt(),
                    temperature=0.3,
                    max_tokens=500
                )

                generation_time = time.time() - generation_start

                # Count code elements (classes, functions, etc.)
                num_code_elements = context.count("class ") + context.count("def ")

                tracker.log_metrics({
                    "response_length": len(response),
                    "num_code_elements": num_code_elements,
                    "generation_time": generation_time
                })

                return response
        else:
            # Get file context
            context = self.retriever.get_file_context(file_path)

            # Generate summary
            prompt = f"""Please provide a concise summary of this file's purpose and main components:

{context}

Focus on:
1. Main purpose of the file
2. Key classes/functions
3. Important functionality"""

            response = self.llm_client.generate(
                prompt=prompt,
                system=self._get_system_prompt(),
                temperature=0.3,
                max_tokens=500
            )

            return response

    def health_check(self) -> Dict[str, bool]:
        """
        Check health of all system components.

        Returns:
            Dictionary with component health status
        """
        # Ensure vector store is connected
        if not self.retriever.vector_store.is_connected():
            try:
                self.retriever.vector_store.connect()
            except Exception as e:
                logger.warning(f"Failed to connect to vector store during health check: {e}")

        health = {
            "llm_available": self.llm_client.is_available(),
            "vector_store_connected": self.retriever.vector_store.is_connected(),
            "embedding_model_loaded": self.retriever.embedding_gen.model is not None
        }

        logger.info(f"Health check: {health}")

        return health


# Global RAG system instance
_rag_system: Optional[RAGSystem] = None


def get_rag_system() -> RAGSystem:
    """Get or create the global RAG system instance."""
    global _rag_system

    if _rag_system is None:
        _rag_system = RAGSystem()

    return _rag_system
