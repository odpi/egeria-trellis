"""
User feedback collection system for continuous improvement.

This module captures user feedback on query responses to improve
routing accuracy and response quality over time.

Since Phase 2 (A-F1, 2026-11-11) every vote is an `answer` item in the shared trellis-feedback
store (`advisor.feedback_store`), and the statistics and gap analysis read from it. Nothing is
appended to `user_feedback.jsonl` any more. `scripts/import_feedback_jsonl.py` copies that file in
once. A collector built with an explicit `feedback_file` reads that file for its statistics: the
legacy reports (scripts/generate_collection_usage_report.py) still pass one.
"""

from typing import Optional, Dict, Any, List
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
import json
import os
import threading
from loguru import logger

from advisor.config import resolve_advisor_data_root

# Import sentiment analyzer
try:
    from advisor.sentiment_analysis import get_sentiment_analyzer
    SENTIMENT_AVAILABLE = True
except ImportError:
    SENTIMENT_AVAILABLE = False
    logger.warning("Sentiment analysis not available")


@dataclass
class FeedbackEntry:
    """Single feedback entry."""
    timestamp: str
    query: str
    query_type: str
    collections_searched: List[str]
    response_length: int
    rating: str  # "positive", "negative", "neutral"
    feedback_text: Optional[str] = None  # User's free-text comment
    suggested_collection: Optional[str] = None  # Better collection suggestion
    session_id: Optional[str] = None
    user_comment: Optional[str] = None  # Additional user comment/explanation
    # Phase 1 enhancements
    star_rating: Optional[int] = None  # 1-5 star rating
    category: Optional[str] = None  # accuracy, completeness, clarity, relevance
    # Phase 2 enhancements
    perspective: Optional[str] = None   # active role: developer, data_steward, etc.
    routing_agent: Optional[str] = None # agent that handled: doc_agent, examples_agent, rag_fallback, etc.
    # Phase 0 S4 (2026-11-10): who voted, and the UI's intent selector in its own field -- never in
    # user_comment/feedback_text, where it was sentiment-analysed as if the user had written it.
    user_id: Optional[str] = None       # signed-in user; None when anonymous
    intent_override: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)
    
    def get_normalized_rating(self) -> float:
        """
        Get normalized rating score (0-1).
        
        Returns:
            Normalized rating between 0 and 1
        """
        if self.star_rating is not None:
            return self.star_rating / 5.0
        elif self.rating == "positive":
            return 1.0
        elif self.rating == "negative":
            return 0.0
        else:  # neutral
            return 0.5


class FeedbackCollector:
    """Collects and stores user feedback."""
    
    def __init__(self, feedback_file: Optional[Path] = None):
        """
        Initialize feedback collector.

        Args:
            feedback_file: a legacy JSONL file to read statistics from, instead of the
                trellis-feedback store. Votes are never written to it.
        """
        #: Set only when a caller asked for a legacy file; None means "read the library".
        self.legacy_file: Optional[Path] = Path(feedback_file) if feedback_file is not None else None
        #: The old default path, kept for messages and for the one-time import.
        self.feedback_file = self.legacy_file or (resolve_advisor_data_root() / "feedback" / "user_feedback.jsonl")
        #: Lines of the legacy file that could not be read on the last pass (line number, reason).
        self.unreadable_lines: List[Dict[str, Any]] = []
        #: Library votes left out of the last read: an earlier vote on an answer its user voted on again.
        self.superseded = 0
        logger.info("Initialized feedback collector: "
                    + (f"legacy file {self.legacy_file}" if self.legacy_file else "trellis-feedback store"))
    
    def record_feedback(
        self,
        query: str,
        query_type: str,
        collections_searched: List[str],
        response_length: int,
        rating: str,
        feedback_text: Optional[str] = None,
        suggested_collection: Optional[str] = None,
        session_id: Optional[str] = None,
        user_comment: Optional[str] = None,
        star_rating: Optional[int] = None,
        category: Optional[str] = None,
        perspective: Optional[str] = None,
        routing_agent: Optional[str] = None,
        user_id: Optional[str] = None,
        intent_override: Optional[str] = None,
    ) -> bool:
        """
        Record user feedback; True if the primary record was written. For callers that only need a
        yes/no (the CLI). The web route uses record_vote(), which says why a write failed.
        """
        try:
            self.record_vote(
                query=query, query_type=query_type, collections_searched=collections_searched,
                response_length=response_length, rating=rating, feedback_text=feedback_text,
                suggested_collection=suggested_collection, session_id=session_id,
                user_comment=user_comment, star_rating=star_rating, category=category,
                perspective=perspective, routing_agent=routing_agent, user_id=user_id,
                intent_override=intent_override,
            )
            return True
        except FeedbackWriteError as e:
            logger.error(f"Failed to record feedback: {e}")
            return False

    def record_vote(
        self,
        query: str,
        query_type: str,
        collections_searched: List[str],
        response_length: int,
        rating: str,
        feedback_text: Optional[str] = None,
        suggested_collection: Optional[str] = None,
        session_id: Optional[str] = None,
        user_comment: Optional[str] = None,
        star_rating: Optional[int] = None,
        category: Optional[str] = None,
        perspective: Optional[str] = None,
        routing_agent: Optional[str] = None,
        user_id: Optional[str] = None,
        intent_override: Optional[str] = None,
        response_text: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        exchange_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Record one vote.

        - The PRIMARY record is an `answer` item in the trellis-feedback store (A-F1). If it cannot be
          written this raises FeedbackWriteError with the reason; nothing reports success for a vote
          that was not kept.
        - `idempotency_key` (session + exchange + vote, `feedback_store.vote_key`): a retry with the
          same key from the same user writes nothing and returns the first item, `created` False, and
          the secondary sinks are not run again.
        - MLflow is never on the caller's path: it is queued to one background worker, skipped when
          the tracker's reachability probe disabled tracking, with a short timeout and no retries.
          (Before this, a refused MLflow port was retried for ~4 minutes inside the request.)
        - The Postgres query_metrics update is secondary and matches on user_id AND query_text (the
          newest row); with no user id it is skipped rather than guessed, since a text-only match can
          land on another user's row.

        Synchronous: callers on an event loop must run it in a thread (run_in_threadpool).

        Returns {"item_id", "created", "query_metrics": "ok" | "no_match" | "skipped" | "error" |
        "not_run" (a replay: the first submit already did it)}.
        """
        # Validate star rating if provided
        if star_rating is not None and not (1 <= star_rating <= 5):
            logger.warning(f"Invalid star rating {star_rating}, must be 1-5")
            star_rating = None

        # Validate category if provided
        valid_categories = {"accuracy", "completeness", "clarity", "relevance"}
        if category is not None and category not in valid_categories:
            logger.warning(f"Invalid category {category}, must be one of {valid_categories}")
            category = None

        entry = FeedbackEntry(
            timestamp=datetime.utcnow().isoformat(),
            query=query,
            query_type=query_type,
            collections_searched=collections_searched,
            response_length=response_length,
            rating=rating,
            feedback_text=feedback_text,
            suggested_collection=suggested_collection,
            session_id=session_id,
            user_comment=user_comment,
            star_rating=star_rating,
            category=category,
            perspective=perspective,
            routing_agent=routing_agent,
            user_id=user_id,
            intent_override=intent_override,
        )

        # Sentiment only on what the user actually wrote (never on the intent label)
        sentiment_result = None
        if SENTIMENT_AVAILABLE and user_comment:
            try:
                analyzer = get_sentiment_analyzer()
                sentiment_result = analyzer.analyze(user_comment)
                logger.debug(f"Sentiment analysis: {sentiment_result.sentiment} ({sentiment_result.confidence:.2f})")
            except Exception as e:
                logger.warning(f"Sentiment analysis failed: {e}")

        entry_dict = entry.to_dict()
        if sentiment_result:
            entry_dict['sentiment'] = sentiment_result.sentiment
            entry_dict['sentiment_confidence'] = sentiment_result.confidence
            entry_dict['sentiment_emotion'] = sentiment_result.emotion
            entry_dict['sentiment_keywords'] = sentiment_result.keywords_found

        # PRIMARY: the vote is kept only if the library item is written
        item_id, created = self._write_item(entry, entry_dict, response_text=response_text,
                                            idempotency_key=idempotency_key, exchange_id=exchange_id)
        if not created:
            logger.info(f"Feedback replay for item {item_id} (same idempotency key); nothing written")
            return {"item_id": item_id, "created": False, "query_metrics": "not_run"}

        # Secondary: MLflow in the background, never awaited here
        submit_mlflow_feedback(self, entry, sentiment_result)

        # Secondary: query_metrics in Postgres, matched by user and query
        query_metrics = self._update_query_metrics(
            query=query, user_id=user_id, rating=rating, star_rating=star_rating,
            suggested_collection=suggested_collection, feedback_text=feedback_text or user_comment,
        )

        logger.info(f"Recorded {rating} feedback for query: {query[:50]}... "
                    f"(item={item_id}, user={user_id or 'anonymous'}, query_metrics={query_metrics})")
        return {"item_id": item_id, "created": True, "query_metrics": query_metrics}

    @staticmethod
    def _write_item(entry: "FeedbackEntry", entry_dict: Dict[str, Any], *, response_text: Optional[str],
                    idempotency_key: Optional[str], exchange_id: Optional[str]):
        """Write the vote as a library `answer` item; (item id, created). FeedbackWriteError otherwise."""
        from advisor.feedback_store import StoreUnavailable, VOTE_BY_RATING, get_store
        if entry.rating not in VOTE_BY_RATING:
            raise FeedbackWriteError(f"rating must be one of {sorted(VOTE_BY_RATING)}, got {entry.rating!r}")
        # Everything EA's analytics read that the library item has no column for.
        # The routed intent is the item's intent_label and the UI selector is extra.intent_selected
        # (A-F1 N2), so neither is repeated here.
        keep = ("collections_searched", "response_length", "star_rating", "category",
                "suggested_collection", "feedback_text", "user_comment", "sentiment",
                "sentiment_confidence", "sentiment_emotion", "sentiment_keywords")
        extra = {k: entry_dict[k] for k in keep if entry_dict.get(k) not in (None, "", [])}
        if exchange_id:
            extra["exchange_id"] = exchange_id
        extra["intent_selected"] = entry.intent_override or ""
        try:
            from trellis_feedback import FeedbackItem
            store = get_store()
            item = FeedbackItem(
                app=store.app, kind="answer", vote=VOTE_BY_RATING[entry.rating],
                query_text=(entry.query or "").strip(), response_text=response_text or "",
                user_id=entry.user_id or "", session_id=entry.session_id or "",
                intent_label=entry.query_type or "", perspective=entry.perspective or "",
                routing_agent=entry.routing_agent or "", extra=extra,
            )
            stored, created = store.submit(item, idempotency_key=idempotency_key)
        except StoreUnavailable as e:
            raise FeedbackWriteError(str(e)) from e
        except Exception as e:  # noqa: BLE001 -- the vote was not kept: say why, in one line
            from advisor.feedback_store import _words
            raise FeedbackWriteError(f"{type(e).__name__}: {_words(e)}") from e
        return stored.id, created

    # ── reading: the library, or a legacy file a caller named ─────────────────

    def entries(self) -> List[Dict[str, Any]]:
        """Every vote, in the shape the statistics read. From the trellis-feedback store, or from
        `legacy_file` when the collector was built with one. From the store, only the LATEST vote on each
        answer counts (A-F1 N1); `superseded` says how many earlier ones were left out.

        Raises FeedbackReadError when the store cannot be read. A legacy line that is not JSON is
        skipped and listed in `unreadable_lines` (line number and reason), never silently dropped."""
        self.unreadable_lines = []
        self.superseded = 0
        if self.legacy_file is None:
            from advisor.feedback_store import (StoreUnavailable, as_collector_entry, get_store, iter_answer_items,
                                                latest_votes)
            try:
                counted, superseded = latest_votes(list(iter_answer_items(get_store())))
                self.superseded = len(superseded)
                return [as_collector_entry(r) for r in counted]
            except StoreUnavailable as e:
                raise FeedbackReadError(str(e)) from e
            except Exception as e:  # noqa: BLE001 -- a read that failed is reported, not shown as "no votes"
                from advisor.feedback_store import _words
                raise FeedbackReadError(f"feedback store not read: {type(e).__name__}: {_words(e)}") from e
        if not self.legacy_file.exists():
            return []
        out: List[Dict[str, Any]] = []
        with open(self.legacy_file, "r") as f:
            for n, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except ValueError as e:
                    self.unreadable_lines.append({"line": n, "reason": f"not JSON: {e}"})
                    continue
                if not isinstance(rec, dict):
                    self.unreadable_lines.append({"line": n, "reason": "not a JSON object"})
                    continue
                out.append(rec)
        return out

    def analysis(self) -> Dict[str, Any]:
        """Statistics, gaps and routing improvements from ONE read of the votes.
        Raises FeedbackReadError when they cannot be read."""
        entries = self.entries()
        stats = self._stats_from_entries(entries)
        return {
            "stats": stats,
            "gaps": self._gaps_from_entries(entries, stats),
            "improvements": self.get_routing_improvements(stats),
            "count": len(entries),
            # Votes left out because the same user later voted again on the same answer (A-F1 N1).
            "superseded": self.superseded,
            "unreadable_lines": list(self.unreadable_lines),
        }

    def _update_query_metrics(self, *, query: str, user_id: Optional[str], rating: str,
                              star_rating: Optional[int], suggested_collection: Optional[str],
                              feedback_text: Optional[str]) -> str:
        """ok | no_match | skipped (no user id: never guess whose row) | error."""
        if not user_id:
            return "skipped"
        try:
            from advisor.db_consolidated import get_db_manager
            db_manager = get_db_manager()
            update_sql = """
                UPDATE query_metrics
                SET success = %s,
                    rating = %s,
                    star_rating = %s,
                    suggested_collection = %s,
                    feedback_text = %s
                WHERE id = (
                    SELECT id FROM query_metrics
                    WHERE user_id = %s AND query_text = %s
                    ORDER BY timestamp DESC
                    LIMIT 1
                )
            """
            affected = db_manager.execute_update(update_sql, (
                rating != "negative",
                rating,
                star_rating,
                suggested_collection,
                feedback_text,
                user_id,
                query,
            ))
        except Exception as db_err:
            logger.warning(f"Failed to update query_metrics in Postgres with feedback: {db_err}")
            return "error"
        return "ok" if affected else "no_match"

    def get_feedback_stats(self) -> Dict[str, Any]:
        """
        Get feedback statistics. Raises FeedbackReadError when the votes cannot be read.
        """
        return self._stats_from_entries(self.entries())

    @staticmethod
    def _stats_from_entries(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Statistics over `entries` (each in `entries()`'s shape)."""
        stats = {
            "total": 0,
            "positive": 0,
            "negative": 0,
            "neutral": 0,
            "by_query_type": {},
            "by_collection": {},
            "routing_corrections": [],
            # Phase 1 enhancements
            "star_ratings": [],
            "avg_star_rating": 0.0,
            "by_category": {},
            "category_star_ratings": {},
            # Phase 2 enhancements
            "by_perspective": {},
            "by_routing_agent": {},
        }
        
        for entry in entries:
            stats["total"] += 1

            # Count by rating
            rating = entry.get("rating", "neutral")
            stats[rating] = stats.get(rating, 0) + 1

            # Track star ratings
            star_rating = entry.get("star_rating")
            if star_rating is not None:
                stats["star_ratings"].append(star_rating)

            # Track by category
            category = entry.get("category")
            if category:
                if category not in stats["by_category"]:
                    stats["by_category"][category] = {
                        "total": 0, "positive": 0, "neutral": 0, "negative": 0, "star_ratings": []
                    }
                stats["by_category"][category]["total"] += 1
                stats["by_category"][category][rating] = \
                    stats["by_category"][category].get(rating, 0) + 1
                if star_rating is not None:
                    stats["by_category"][category]["star_ratings"].append(star_rating)

            # Count by query type
            query_type = entry.get("query_type", "unknown")
            if query_type not in stats["by_query_type"]:
                stats["by_query_type"][query_type] = {
                    "total": 0, "positive": 0, "neutral": 0, "negative": 0
                }
            stats["by_query_type"][query_type]["total"] += 1
            stats["by_query_type"][query_type][rating] = \
                stats["by_query_type"][query_type].get(rating, 0) + 1

            # Count by collection
            for collection in entry.get("collections_searched", []):
                if collection not in stats["by_collection"]:
                    stats["by_collection"][collection] = {
                        "total": 0, "positive": 0, "neutral": 0, "negative": 0
                    }
                stats["by_collection"][collection]["total"] += 1
                stats["by_collection"][collection][rating] = \
                    stats["by_collection"][collection].get(rating, 0) + 1

            # Track routing corrections
            if entry.get("suggested_collection"):
                stats["routing_corrections"].append({
                    "query": entry.get("query", ""),
                    "searched": entry.get("collections_searched", []),
                    "suggested": entry["suggested_collection"]
                })

            # Phase 2: by_perspective
            persp = entry.get("perspective") or "none"
            if persp not in stats["by_perspective"]:
                stats["by_perspective"][persp] = {"total": 0, "positive": 0, "neutral": 0, "negative": 0}
            stats["by_perspective"][persp]["total"] += 1
            stats["by_perspective"][persp][rating] = \
                stats["by_perspective"][persp].get(rating, 0) + 1

            # Phase 2: by_routing_agent
            agent = entry.get("routing_agent") or "unknown"
            if agent not in stats["by_routing_agent"]:
                stats["by_routing_agent"][agent] = {"total": 0, "positive": 0, "neutral": 0, "negative": 0}
            stats["by_routing_agent"][agent]["total"] += 1
            stats["by_routing_agent"][agent][rating] = \
                stats["by_routing_agent"][agent].get(rating, 0) + 1

        # Calculate satisfaction rate (weighted: positive = 1.0, neutral = 0.5)
        if stats["total"] > 0:
            stats["satisfaction_rate"] = (stats.get("positive", 0) + 0.5 * stats.get("neutral", 0)) / stats["total"]
        else:
            stats["satisfaction_rate"] = 0.0

        # Calculate average star rating
        if stats["star_ratings"]:
            stats["avg_star_rating"] = sum(stats["star_ratings"]) / len(stats["star_ratings"])

        # Calculate average star rating by category
        for category, data in stats["by_category"].items():
            if data["star_ratings"]:
                stats["category_star_ratings"][category] = \
                    sum(data["star_ratings"]) / len(data["star_ratings"])

        return stats

    def get_routing_improvements(self, stats: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Analyze feedback to suggest routing improvements.

        Returns:
            List of suggested routing improvements
        """
        improvements = []
        if stats is None:
            stats = self.get_feedback_stats()
        
        # Analyze routing corrections
        for correction in stats.get("routing_corrections", []):
            improvements.append({
                "type": "routing_correction",
                "query": correction["query"],
                "current_routing": correction["searched"],
                "suggested_routing": correction["suggested"],
                "action": f"Add domain term or boost {correction['suggested']} for similar queries"
            })
        
        # Analyze low-satisfaction collections
        for collection, data in stats.get("by_collection", {}).items():
            if data["total"] >= 5:  # Minimum sample size
                satisfaction = data.get("positive", 0) / data["total"]
                if satisfaction < 0.5:
                    improvements.append({
                        "type": "low_satisfaction",
                        "collection": collection,
                        "satisfaction_rate": satisfaction,
                        "total_queries": data["total"],
                        "action": f"Review prompt template for {collection} collection"
                    })
        
        # Analyze low-satisfaction query types
        for query_type, data in stats.get("by_query_type", {}).items():
            if data["total"] >= 5:
                satisfaction = data.get("positive", 0) / data["total"]
                if satisfaction < 0.5:
                    improvements.append({
                        "type": "low_satisfaction_query_type",
                        "query_type": query_type,
                        "satisfaction_rate": satisfaction,
                        "total_queries": data["total"],
                        "action": f"Review query-type instructions for {query_type}"
                    })
        
        return improvements

    def get_gap_analysis(self) -> Dict[str, Any]:
        """The gap analysis below, from one read. Raises FeedbackReadError when the votes cannot be read."""
        entries = self.entries()
        return self._gaps_from_entries(entries, self._stats_from_entries(entries))

    @staticmethod
    def _gaps_from_entries(entries: List[Dict[str, Any]], stats: Dict[str, Any]) -> Dict[str, Any]:
        """
        Identify queries that are underserved — negative feedback combined with
        RAG fallback routing, or perspectives with consistently low satisfaction.

        Returns a dict with:
          - rag_fallback_negatives: queries that fell to RAG and got thumbs-down
          - low_satisfaction_perspectives: perspectives below 50% satisfaction (≥5 samples)
          - low_satisfaction_agents: routing agents below 50% satisfaction (≥5 samples)
          - unhandled_intents: query types with high negative rate (≥5 samples)
        """
        gap: Dict[str, Any] = {
            "rag_fallback_negatives": [],
            "low_satisfaction_perspectives": [],
            "low_satisfaction_agents": [],
            "unhandled_intents": [],
        }
        rag_agents = {"rag_fallback", "general", "unknown"}
        for entry in entries:
            if entry.get("rating") == "negative":
                agent = entry.get("routing_agent") or "unknown"
                if agent in rag_agents:
                    gap["rag_fallback_negatives"].append({
                        "query": (entry.get("query") or "")[:120],
                        "query_type": entry.get("query_type"),
                        "perspective": entry.get("perspective"),
                        "timestamp": entry.get("timestamp"),
                    })

        for persp, data in stats.get("by_perspective", {}).items():
            if data["total"] >= 5:
                sat = data.get("positive", 0) / data["total"]
                if sat < 0.5:
                    gap["low_satisfaction_perspectives"].append({
                        "perspective": persp,
                        "satisfaction_rate": round(sat, 2),
                        "total": data["total"],
                    })

        for agent, data in stats.get("by_routing_agent", {}).items():
            if data["total"] >= 5:
                sat = data.get("positive", 0) / data["total"]
                if sat < 0.5:
                    gap["low_satisfaction_agents"].append({
                        "routing_agent": agent,
                        "satisfaction_rate": round(sat, 2),
                        "total": data["total"],
                    })

        for qt, data in stats.get("by_query_type", {}).items():
            if data["total"] >= 5:
                neg_rate = data.get("negative", 0) / data["total"]
                if neg_rate > 0.5:
                    gap["unhandled_intents"].append({
                        "query_type": qt,
                        "negative_rate": round(neg_rate, 2),
                        "total": data["total"],
                    })

        return gap

    def log_feedback_to_mlflow(self, entry: FeedbackEntry, sentiment_result=None, raise_errors: bool = False):
        """
        Log feedback entry to MLflow for tracking and analysis.
        
        Args:
            entry: Feedback entry to log
            sentiment_result: Optional sentiment analysis result
        """
        try:
            import mlflow
            from advisor.config import settings
            
            # Only log if MLflow tracking is enabled
            if not settings.mlflow_enable_tracking:
                return
            
            # Check if we're in an active run
            active_run = mlflow.active_run()
            if active_run is None:
                # Start a new run for feedback logging
                with mlflow.start_run(run_name=f"feedback_{entry.timestamp}", nested=True):
                    self._log_feedback_data(entry, sentiment_result)
            else:
                # Log to active run
                self._log_feedback_data(entry, sentiment_result)
                
        except Exception as e:
            if raise_errors:
                raise
            logger.warning(f"Failed to log feedback to MLflow: {e}")
    
    def _log_feedback_data(self, entry: FeedbackEntry, sentiment_result=None):
        """
        Internal method to log feedback data to MLflow.
        
        Args:
            entry: Feedback entry to log
            sentiment_result: Optional sentiment analysis result
        """
        import mlflow
        
        # Log as metrics
        metrics = {
            "feedback_rating_normalized": entry.get_normalized_rating(),
            "feedback_response_length": float(entry.response_length),
        }
        
        if entry.star_rating is not None:
            metrics["feedback_star_rating"] = float(entry.star_rating)
        
        # Add sentiment metrics if available
        if sentiment_result:
            metrics["feedback_sentiment_confidence"] = sentiment_result.confidence
        
        mlflow.log_metrics(metrics)
        
        # Log as parameters
        params = {
            "feedback_query_type": entry.query_type,
            "feedback_rating": entry.rating,
            "feedback_collections_count": str(len(entry.collections_searched)),
        }
        
        if entry.category:
            params["feedback_category"] = entry.category

        if entry.perspective:
            params["feedback_perspective"] = entry.perspective

        if entry.routing_agent:
            params["feedback_routing_agent"] = entry.routing_agent

        if entry.session_id:
            params["feedback_session_id"] = entry.session_id
        
        # Add sentiment parameters if available
        if sentiment_result:
            params["feedback_sentiment"] = sentiment_result.sentiment
            params["feedback_sentiment_confidence"] = f"{sentiment_result.confidence:.2f}"
            if sentiment_result.emotions:
                params["feedback_emotions"] = ", ".join(sentiment_result.emotions)
        
        mlflow.log_params(params)
        
        # Log collections as tags for easy filtering
        for i, collection in enumerate(entry.collections_searched):
            mlflow.set_tag(f"collection_{i}", collection)
        
        # Log the full feedback as a JSON artifact with sentiment data
        feedback_dict = entry.to_dict()
        if sentiment_result:
            feedback_dict["sentiment"] = {
                "sentiment": sentiment_result.sentiment,
                "confidence": sentiment_result.confidence,
                "emotions": sentiment_result.emotions,
                "keywords_found": sentiment_result.keywords_found
            }
        mlflow.log_dict(feedback_dict, f"feedback_{entry.timestamp}.json")
        
        logger.debug(f"Logged feedback to MLflow: {entry.rating} rating for {entry.query_type}")
    
    def export_feedback(self, output_file: Path) -> bool:
        """
        Export feedback to a different format.
        
        Args:
            output_file: Path to output file (JSON or CSV)
            
        Returns:
            True if export was successful
        """
        try:
            feedback_entries = self.entries()
            if not feedback_entries:
                logger.warning("No feedback to export")
                return False

            if output_file.suffix == '.json':
                with open(output_file, 'w') as f:
                    json.dump(feedback_entries, f, indent=2)
            elif output_file.suffix == '.csv':
                import csv
                if feedback_entries:
                    keys = feedback_entries[0].keys()
                    with open(output_file, 'w', newline='') as f:
                        writer = csv.DictWriter(f, fieldnames=keys)
                        writer.writeheader()
                        writer.writerows(feedback_entries)
            else:
                logger.error(f"Unsupported export format: {output_file.suffix}")
                return False
            
            logger.info(f"Exported {len(feedback_entries)} feedback entries to {output_file}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to export feedback: {e}")
    
            return False


class FeedbackWriteError(Exception):
    """The primary feedback record (the trellis-feedback item) could not be written."""


class FeedbackReadError(Exception):
    """The votes could not be read (the trellis-feedback store is unreachable, say). The message says
    why; a caller shows it instead of statistics, never zeros that look like "no votes"."""


# ── MLflow, best effort and off every request path (Phase 0 S4) ─────────────────
#: Seconds an MLflow HTTP call may take before it is abandoned. No retries.
MLFLOW_FEEDBACK_TIMEOUT_S = 2
_mlflow_executor: Optional[ThreadPoolExecutor] = None
_mlflow_lock = threading.Lock()
_mlflow_failure_logged = False
_mlflow_disabled_logged = False


def _mlflow_fail_fast_env() -> None:
    """mlflow reads these per HTTP request. A caller's own setting wins; otherwise fail fast: a
    refused or slow tracking server costs at most MLFLOW_FEEDBACK_TIMEOUT_S, never a backoff loop.
    Process-wide, which is the intent: EA's MLflowTracker already disables tracking rather than wait."""
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_TIMEOUT", str(MLFLOW_FEEDBACK_TIMEOUT_S))
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "0")


def _mlflow_feedback_job(collector: "FeedbackCollector", entry: FeedbackEntry, sentiment_result) -> None:
    global _mlflow_failure_logged, _mlflow_disabled_logged
    try:
        from advisor.mlflow_tracking import get_mlflow_tracker
        # The tracker's one short TCP probe (no retries) decides; built here, in the worker, so even
        # its first construction never runs on a request.
        if not get_mlflow_tracker().enabled:
            if not _mlflow_disabled_logged:
                _mlflow_disabled_logged = True
                logger.info("MLflow tracking is disabled or unreachable; feedback is not logged to MLflow")
            return
        _mlflow_fail_fast_env()
        collector.log_feedback_to_mlflow(entry, sentiment_result, raise_errors=True)
    except Exception as e:  # noqa: BLE001 -- best effort; logged once per process
        if not _mlflow_failure_logged:
            _mlflow_failure_logged = True
            logger.warning(f"Feedback not logged to MLflow (further failures are not logged): {e}")
        else:
            logger.debug(f"Feedback not logged to MLflow: {e}")


def submit_mlflow_feedback(collector: "FeedbackCollector", entry: FeedbackEntry, sentiment_result=None):
    """Queue the MLflow log on one daemon worker and return at once (the Future, for tests)."""
    global _mlflow_executor
    with _mlflow_lock:
        if _mlflow_executor is None:
            _mlflow_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ea-feedback-mlflow")
        executor = _mlflow_executor
    try:
        return executor.submit(_mlflow_feedback_job, collector, entry, sentiment_result)
    except RuntimeError as e:  # interpreter shutting down
        logger.debug(f"MLflow feedback not queued: {e}")
        return None


# Singleton instance
_feedback_collector: Optional[FeedbackCollector] = None


def get_feedback_collector() -> FeedbackCollector:
    """Get singleton feedback collector instance."""
    global _feedback_collector
    if _feedback_collector is None:
        _feedback_collector = FeedbackCollector()
    return _feedback_collector


def record_positive_feedback(
    query: str,
    query_type: str,
    collections_searched: List[str],
    response_length: int,
    feedback_text: Optional[str] = None,
    session_id: Optional[str] = None
) -> bool:
    """Convenience function to record positive feedback."""
    collector = get_feedback_collector()
    return collector.record_feedback(
        query=query,
        query_type=query_type,
        collections_searched=collections_searched,
        response_length=response_length,
        rating="positive",
        feedback_text=feedback_text,
        session_id=session_id
    )


def record_negative_feedback(
    query: str,
    query_type: str,
    collections_searched: List[str],
    response_length: int,
    feedback_text: Optional[str] = None,
    suggested_collection: Optional[str] = None,
    session_id: Optional[str] = None
) -> bool:
    """Convenience function to record negative feedback."""
    collector = get_feedback_collector()
    return collector.record_feedback(
        query=query,
        query_type=query_type,
        collections_searched=collections_searched,
        response_length=response_length,
        rating="negative",
        feedback_text=feedback_text,
        suggested_collection=suggested_collection,
        session_id=session_id
    )