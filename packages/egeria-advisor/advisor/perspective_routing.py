"""
Perspective-aware query routing engine.
Resolves active perspectives based on prompt context, session history, and user history,
and applies routing policies and feedback loop adjustments from PostgreSQL.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import yaml
from loguru import logger

from advisor.db_consolidated import get_db_manager


class PerspectiveRoutingEngine:
    """Evaluates query intent and perspective context to decide routing actions."""

    # Intents for which the "Show me" format-disambiguation layer (dre/cli/java signal
    # routing + the ambiguous Python-vs-CLI-vs-Dr.Egeria clarify) is even eligible to run.
    # An explicit intent_override — Act ("command"), Run Report ("report"), Inspect
    # ("code_intel"), Explain ("explanation"), Troubleshoot ("debugging"), Create
    # ("plan"/"create"), etc. — is the user unambiguously telling the system what to do;
    # format disambiguation must not second-guess that just because the query text
    # happens to contain "show me" or similar phrasing (e.g. Act + "show me the external
    # references" should look for a runnable report, not offer a Python/CLI/Dr.Egeria
    # code-sample clarify).
    _AMBIGUOUS_ELIGIBLE_INTENTS = {"code_help", "code_search", "example", "general", ""}

    def __init__(self, policy_path: Optional[Path] = None):
        if policy_path is None:
            policy_path = Path(__file__).parent / "configdata" / "routing_policies.yaml"
        self.policy_path = policy_path
        self.policies: Dict[str, Any] = {}
        self.load_policies()

    def load_policies(self) -> None:
        """Load intent-to-agent mapping configuration."""
        try:
            if self.policy_path.exists():
                with open(self.policy_path, "r", encoding="utf-8") as f:
                    self.policies = yaml.safe_load(f) or {}
                logger.info(f"PerspectiveRoutingEngine: loaded routing policies from {self.policy_path}")
            else:
                logger.warning(f"PerspectiveRoutingEngine: routing policies file not found at {self.policy_path}")
        except Exception as e:
            logger.error(f"PerspectiveRoutingEngine: failed to load routing policies: {e}")

    def get_session_perspective_history(self, session_id: str) -> List[str]:
        """Fetch the list of perspectives used in this session, ordered by timestamp desc."""
        if not session_id:
            return []
        try:
            db_manager = get_db_manager()
            sql = """
                SELECT active_perspective 
                FROM query_metrics 
                WHERE session_id = %s AND active_perspective IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT 10
            """
            rows = db_manager.execute_query(sql, (session_id,))
            return [r["active_perspective"] for r in rows if r.get("active_perspective")]
        except Exception as e:
            logger.warning(f"PerspectiveRoutingEngine: failed to fetch session perspective history: {e}")
            return []

    def get_user_perspective_history(self, user_id: str) -> List[str]:
        """Fetch the overall most frequent perspectives for this user."""
        if not user_id:
            return []
        try:
            db_manager = get_db_manager()
            sql = """
                SELECT active_perspective, COUNT(*) as count 
                FROM query_metrics 
                WHERE user_id = %s AND active_perspective IS NOT NULL
                GROUP BY active_perspective
                ORDER BY count DESC
                LIMIT 5
            """
            rows = db_manager.execute_query(sql, (user_id,))
            return [r["active_perspective"] for r in rows if r.get("active_perspective")]
        except Exception as e:
            logger.warning(f"PerspectiveRoutingEngine: failed to fetch user perspective history: {e}")
            return []

    def resolve_active_perspective(
        self,
        explicit_perspective: Optional[str],
        session_id: Optional[str],
        user_id: Optional[str]
    ) -> Tuple[str, List[str]]:
        """
        Resolve the active perspective using explicit input, falling back to session history,
        and then to user history. Returns a tuple of (resolved_perspective, history_list).
        """
        history = []
        if session_id:
            history = self.get_session_perspective_history(session_id)
        
        # Stored history may hold pre-P-1 role keys (developer, ...): resolve_key maps them.
        from advisor.perspective_manager import resolve_key
        if explicit_perspective:
            return resolve_key(explicit_perspective), history

        # Fallback to session history
        if history:
            return resolve_key(history[0]), history

        # Fallback to user history
        if user_id:
            user_hist = self.get_user_perspective_history(user_id)
            if user_hist:
                return resolve_key(user_hist[0]), history + user_hist

        return "", history

    def get_feedback_adjustments(self) -> Dict[str, float]:
        """
        Query Postgres query_metrics to find collections that got negative/positive feedback,
        and return boost adjustments (e.g. collection -> multiplier).
        """
        adjustments: Dict[str, float] = {}
        try:
            db_manager = get_db_manager()
            sql = """
                SELECT collection_name, rating, COUNT(*) as count 
                FROM query_metrics 
                WHERE rating IS NOT NULL AND collection_name IS NOT NULL
                GROUP BY collection_name, rating
            """
            rows = db_manager.execute_query(sql)
            
            coll_stats: Dict[str, Dict[str, int]] = {}
            for r in rows:
                coll = r["collection_name"]
                rating = r["rating"]
                count = int(r["count"])
                if coll not in coll_stats:
                    coll_stats[coll] = {"positive": 0, "negative": 0}
                if rating in ("positive", "thumbs_up"):
                    coll_stats[coll]["positive"] = coll_stats[coll].get("positive", 0) + count
                elif rating in ("negative", "thumbs_down"):
                    coll_stats[coll]["negative"] = coll_stats[coll].get("negative", 0) + count
            
            for coll, stats in coll_stats.items():
                total = stats["positive"] + stats["negative"]
                if total >= 3:
                    pos_ratio = stats["positive"] / total
                    if pos_ratio < 0.4:
                        adjustments[coll] = 0.5  # Low satisfaction de-boost
                    elif pos_ratio > 0.8:
                        adjustments[coll] = 1.5  # High satisfaction boost
        except Exception as e:
            logger.warning(f"PerspectiveRoutingEngine: failed to fetch feedback adjustments: {e}")
        return adjustments

    def route(
        self,
        query: str,
        intent: str,
        perspective: Optional[str],
        session_id: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Evaluate perspective and state, returning a routing action.
        
        Returns:
            Dict containing action detail (route, clarify, or redirect).
        """
        resolved_persp, persp_history = self.resolve_active_perspective(perspective, session_id, user_id)
        logger.info(f"PerspectiveRoutingEngine: Resolved active perspective '{resolved_persp}' (history={len(persp_history)})")

        intent_policies = self.policies.get("intents", {})
        policy = intent_policies.get(intent, {})
        default_agent = policy.get("agent")
        boosts = list(policy.get("boosted_collections", []))

        query_lower = query.lower()

        # Perspective Specific Rules: keyed by role profile (A-P P-1), not perspective key.
        from advisor.perspective_manager import role_profile
        tech_roles = {"developer", "data_engineer"}
        resolved_profile = role_profile(resolved_persp)

        code_signals = any(sig in query_lower for sig in (
            "python", "code example", "code sample", "write python",
            "python code", "pyegeria example", "python snippet",
            "class", "method", "def", "function"
        ))
        example_signals = any(kw in query_lower for kw in ("example", "sample", "show me", "how do i", "how to"))
        # "dr egeria"/"dr. egeria"/"dr_egeria" are multi-word/underscored phrases that don't
        # collide with normal English. Bare "dre" is a real word fragment (e.g. "address",
        # "dressed") so it needs a \b word boundary, not a plain substring check, now that
        # this signal is a hard priority route rather than just an override suppressor.
        dre_signals = (
            any(sig in query_lower for sig in ("dr egeria", "dr. egeria", "dr_egeria"))
            or re.search(r"\bdre\b", query_lower) is not None
        )
        # Explicit CLI phrasing — tight, so it only fires when the user actually names
        # the CLI, not on generic words ("command"/"cli" alone are too easily false
        # positive, e.g. "command" appears in many Dr.Egeria/report phrasings too).
        cli_signals = any(sig in query_lower for sig in (
            "hey_egeria", "hey-egeria", "hey egeria",
            "cli command", "command line", "command-line", "terminal command",
        ))
        # No Java example-generation capability exists anywhere in the system today —
        # recognize the request and redirect rather than silently answering in Python.
        java_signals = any(sig in query_lower for sig in (
            "java example", "java code", "give me java", "java program", "in java",
        ))

        # "Show me" format disambiguation (dre/cli/java signal routing + the ambiguous
        # Python-vs-CLI-vs-Dr.Egeria clarify) only applies when the query is actually in
        # "Show me"/Auto territory. An explicit intent_override for something else (Act,
        # Run Report, Inspect, Explain, Troubleshoot, Create, ...) means the user already
        # said what they want done — the presence of "show me" in the query text (e.g.
        # "show me the external references" typed under Act) must not hijack that choice.
        if intent in self._AMBIGUOUS_ELIGIBLE_INTENTS:
            # Explicit Dr.Egeria request — route directly to DrEgeriaTemplateAgent regardless
            # of role. Checked first: it's the most specific/unambiguous of the format signals,
            # and gives it the same reliability guarantee cli_signals/java_signals already have
            # instead of depending on the base intent classifier happening to land on "command".
            if dre_signals:
                return {
                    "action": "route",
                    "agent": "dre_template_agent",
                    "boosts": ["egeria_templates"],
                    "active_perspective": resolved_persp,
                    "applied_policy_rule": {
                        "rule_name": "explicit_dre_signal",
                        "description": "Explicit Dr.Egeria phrasing routes directly to DrEgeriaTemplateAgent"
                    },
                    "perspective_history": persp_history
                }

            # Explicit CLI request — route directly to CLICommandAgent regardless of role.
            # An explicit signal should always short-circuit ambiguity handling below.
            if cli_signals:
                return {
                    "action": "route",
                    "agent": "cli_command_agent",
                    "boosts": ["pyegeria_cli"],
                    "active_perspective": resolved_persp,
                    "applied_policy_rule": {
                        "rule_name": "explicit_cli_signal",
                        "description": "Explicit hey_egeria/CLI phrasing routes directly to CLICommandAgent"
                    },
                    "perspective_history": persp_history
                }

            # Explicit Java request — no Java example generation exists; offer the formats
            # that do (Python, CLI, Dr.Egeria) instead of silently answering in the wrong
            # language or fabricating Java that was never grounded in anything.
            # (dre_signals already returned above, so no need to re-check it here.)
            if java_signals:
                return {
                    "action": "clarify",
                    "clarification_type": "intent_choice",
                    "clarify_message": (
                        "I can't generate Java examples yet. Here's what I can show you instead:"
                    ),
                    "candidates": [
                        "🐍 Python / pyegeria example",
                        "💻 hey_egeria CLI command",
                        "📋 Dr.Egeria markdown template",
                    ],
                    "candidate_intents": ["code_help", "cli_command", "command"],
                    "active_perspective": resolved_persp,
                    "applied_policy_rule": {
                        "rule_name": "java_not_supported_clarify",
                        "description": "Java example request — not supported; offer the 3 supported formats"
                    },
                    "perspective_history": persp_history
                }

            # Tech perspective + an *explicit* code/python signal — not ambiguous, so still
            # fast-paths straight to Python (or CodeIntelAgent for structural questions) with
            # no clarify. (dre_signals/cli_signals/java_signals already returned above.)
            if resolved_profile in tech_roles and code_signals:
                if any(term in query_lower for term in ("class", "inherit", "method", "parent", "child", "subclass", "superclass", "hierarchy")):
                    return {
                        "action": "route",
                        "agent": "code_intel_agent",
                        "boosts": ["code_elements"],
                        "active_perspective": resolved_persp,
                        "applied_policy_rule": {
                            "rule_name": "tech_code_intel_override",
                            "description": "Tech perspective + code structure keywords routes to CodeIntelAgent"
                        },
                        "perspective_history": persp_history
                    }
                return {
                    "action": "route",
                    "agent": "examples_agent",
                    "boosts": ["examples"],
                    "active_perspective": resolved_persp,
                    "applied_policy_rule": {
                        "rule_name": "tech_examples_override",
                        "description": "Tech perspective + explicit code/python keywords routes to ExamplesAgent"
                    },
                    "perspective_history": persp_history
                }

            # Ambiguous "show me"/example/sample request with no explicit format signal —
            # applies to every role (not just governance): a plain "Show me X" doesn't say
            # which format the user wants, so ask rather than assume Python.
            # (dre_signals/cli_signals/java_signals already returned above; code_signals is
            # excluded here because that's an explicit Python signal, not an ambiguous one.)
            if example_signals and not code_signals:
                return {
                    "action": "clarify",
                    "clarification_type": "intent_choice",
                    "candidates": [
                        "🐍 Python / pyegeria example",
                        "💻 hey_egeria CLI command",
                        "📋 Dr.Egeria markdown template"
                    ],
                    "candidate_intents": ["code_help", "cli_command", "command"],
                    "active_perspective": resolved_persp,
                    "applied_policy_rule": {
                        "rule_name": "ambiguous_example_clarify",
                        "description": "Ambiguous example/sample/'show me' request with no explicit format signal — clarify between Python, CLI, and Dr.Egeria"
                    },
                    "perspective_history": persp_history
                }

        # Default policy routing
        if default_agent:
            return {
                "action": "route",
                "agent": default_agent,
                "boosts": boosts,
                "active_perspective": resolved_persp,
                "applied_policy_rule": {
                    "rule_name": f"default_intent_policy_{intent}",
                    "description": f"Routed to default agent {default_agent} for intent {intent}"
                },
                "perspective_history": persp_history
            }

        # Fallback to RAG
        return {
            "action": "route",
            "agent": "rag_fallback",
            "boosts": [],
            "active_perspective": resolved_persp,
            "applied_policy_rule": {
                "rule_name": "default_rag_fallback",
                "description": "Fallback to standard RAG pipeline"
            },
            "perspective_history": persp_history
        }


_engine: Optional[PerspectiveRoutingEngine] = None


def get_perspective_routing_engine() -> PerspectiveRoutingEngine:
    """Get the singleton PerspectiveRoutingEngine instance."""
    global _engine
    if _engine is None:
        _engine = PerspectiveRoutingEngine()
    return _engine
