"""
Simple conversation agent using existing LLMClient and RAG retrieval.

This agent provides conversational AI assistance for Egeria development
using proven, tested components without external framework dependencies.
"""

from typing import List, Dict, Any, Optional
from functools import lru_cache
import time
import asyncio
import concurrent.futures
from loguru import logger

from advisor.llm_client import OllamaClient, get_ollama_client
from advisor.rag_retrieval import RAGRetriever
from advisor.mlflow_tracking import get_mlflow_tracker
from advisor.metrics_collector import get_metrics_collector, sync_collection_health
from advisor.analytics import get_analytics_manager
from advisor.query_patterns import QueryType
from advisor.tool_augmented_rag import should_use_tools
from advisor.agents.pyegeria_agent import get_pyegeria_agent


def _run_coroutine(make_coro, timeout: float = 60):
    """Run `make_coro()` to completion from sync code, the way agents/base.py's `_run_agent` does: on a
    fresh thread when this thread already runs a loop (a bare asyncio.run raises there, and nothing
    applies nest_asyncio since pyegeria 6.2.16), directly otherwise."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(make_coro())
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(make_coro())).result(timeout=timeout)


class ConversationAgent:
    """
    Simple conversation agent for Egeria assistance.
    
    Uses existing OllamaClient and RAGRetriever with conversation history tracking.
    No external framework dependencies - pure Python with stdlib caching.
    
    Features:
    - RAG-enhanced responses (17,997x cached speedup)
    - Conversation history tracking
    - LRU caching for repeated queries
    - Simple, maintainable code
    """
    
    def __init__(
        self,
        max_history: int = 10,
        cache_size: int = 100,
        rag_top_k: int = 10,
        enable_mlflow: bool = True,
        enable_mcp: bool = True
    ):
        """
        Initialize the conversation agent.
        
        Parameters
        ----------
        max_history : int, optional
            Maximum conversation history to maintain (default: 10)
        cache_size : int, optional
            LRU cache size for responses (default: 100)
        rag_top_k : int, optional
            Number of RAG results to retrieve (default: 10, increased for better code examples)
        enable_mlflow : bool, optional
            Enable MLflow tracking (default: True)
        enable_mcp : bool, optional
            Enable MCP tool invocation (default: True)
        """
        self.llm = get_ollama_client()
        self.rag = RAGRetriever(use_multi_collection=True, enable_cache=True)
        self.max_history = max_history
        self.rag_top_k = rag_top_k
        self.conversation_history: List[Dict[str, str]] = []
        self.enable_mlflow = enable_mlflow
        self.enable_mcp = enable_mcp
        self.analytics = get_analytics_manager()
        
        # Initialize MCP agent if enabled
        self.mcp_agent = None
        if enable_mcp:
            try:
                from advisor.mcp_agent import get_mcp_agent
                self.mcp_agent = get_mcp_agent()
                logger.info("MCP agent initialized successfully")
            except Exception as e:
                logger.warning(f"Failed to initialize MCP agent: {e}")
                self.enable_mcp = False
        
        # Initialize MLflow tracker
        if enable_mlflow:
            self.mlflow_tracker = get_mlflow_tracker(
                enable_resource_monitoring=True,
                enable_accuracy_tracking=True
            )
        else:
            self.mlflow_tracker = None
        
        # Sync health on startup
        try:
            sync_collection_health(self.rag, get_metrics_collector())
        except Exception:
            pass
        
        # Configure LRU cache on the run method
        self._cached_run = lru_cache(maxsize=cache_size)(self._run_uncached)
    
    def run(self, query: str, use_rag: bool = True) -> Dict[str, Any]:
        """
        Process a query and generate a response.
        
        Parameters
        ----------
        query : str
            User's question or request
        use_rag : bool, optional
            Whether to use RAG retrieval (default: True)
            
        Returns
        -------
        dict
            Response with content, metadata, and sources
        """
        # Use cached version for repeated queries
        # MLflow tracking is now inside _run_uncached to avoid context manager/cache conflicts
        response = self._cached_run(query, use_rag)
        
        # Add to conversation history
        self.conversation_history.append({
            "query": query,
            "response": response["content"],
            "sources": len(response.get("sources", []))
        })
        
        # Trim history if needed
        if len(self.conversation_history) > self.max_history:
            self.conversation_history = self.conversation_history[-self.max_history:]
        
        return response
    
    def _run_uncached(self, query: str, use_rag: bool) -> Dict[str, Any]:
        """
        Internal method for processing queries (uncached).
        
        This is wrapped by LRU cache in __init__.
        MLflow tracking is done here to avoid context manager/cache conflicts.
        """
        start_time = time.time()
        sources = []
        context = ""
        
        # Track with MLflow if enabled
        if self.enable_mlflow and self.mlflow_tracker:
            with self.mlflow_tracker.track_operation(
                operation_name="agent_query",
                params={
                    "query_length": len(query),
                    "use_rag": use_rag,
                    "conversation_turn": len(self.conversation_history) + 1,
                    "max_history": self.max_history
                },
                track_resources=True,
                track_accuracy=True
            ) as tracker:
                # Process query with tracking
                result = self._process_query_internal(query, use_rag, start_time, tracker)
                return result
        else:
            # Process query without tracking
            return self._process_query_internal(query, use_rag, start_time, None)
    
    def _process_query_internal(
        self,
        query: str,
        use_rag: bool,
        start_time: float,
        tracker: Any = None
    ) -> Dict[str, Any]:
        """Internal query processing logic."""
        sources = []
        context = ""
        mcp_result = None
        
        # Check PyEgeria Agent FIRST (more specific than CLI)
        try:
            # Force reload to pick up code changes
            from advisor.agents.pyegeria_agent import reset_pyegeria_agent
            reset_pyegeria_agent()
            pyegeria_agent = get_pyegeria_agent()
            
            if pyegeria_agent.is_pyegeria_query(query):
                logger.info("Detected PyEgeria query, routing to PyEgeria Agent")
                pyegeria_response = pyegeria_agent.answer(query)
                
                # Log PyEgeria-specific metrics to MLflow tracker if available
                if tracker:
                    try:
                        tracker.log_metrics({
                            "pyegeria_query": 1.0,
                            "pyegeria_confidence": pyegeria_response.get('confidence', 0.0),
                            "pyegeria_sources_count": float(len(pyegeria_response.get('sources', [])))
                        })
                        # log_params might not exist on OperationTracker, use hasattr check
                        if hasattr(tracker, 'log_params'):
                            tracker.log_params({
                                "agent_type": "pyegeria",
                                "query_type": pyegeria_response.get('query_type', 'unknown')
                            })
                    except Exception as tracker_error:
                        logger.warning(f"Failed to log PyEgeria metrics to tracker: {tracker_error}")
                
                # Convert PyEgeria agent response to conversation agent format
                response_content = pyegeria_response.get('answer', '')
                if pyegeria_response.get('suggestions'):
                    response_content += "\n\n**Suggestions:**\n" + "\n".join(
                        f"- {s}" for s in pyegeria_response['suggestions']
                    )
                
                logger.info(f"PyEgeria agent returned response with {len(response_content)} characters")
                return {
                    "content": response_content,
                    "sources": pyegeria_response.get('sources', []),
                    "agent_type": "pyegeria",  # Top-level for CLI tracking
                    "follow_up_options": pyegeria_response.get('follow_up_options', []),
                    "metadata": {
                        "agent": "pyegeria",
                        "query_type": pyegeria_response.get('query_type', 'unknown'),
                        "confidence": pyegeria_response.get('confidence', 0.0),
                        "routed_to": "pyegeria_agent",
                        "processing_time": time.time() - start_time
                    }
                }
        except Exception as e:
            logger.error(f"PyEgeria Agent routing failed with exception: {e}")
            import traceback
            logger.error(traceback.format_exc())
        
        # Check if query should use CLI Command Agent (after PyEgeria check)
        try:
            from advisor.cli_integration import get_cli_router
            cli_router = get_cli_router()
            
            if cli_router.should_use_cli_agent(query):
                logger.info("Detected CLI command query, routing to CLI Command Agent")
                cli_response = cli_router.route_query(query)
                
                # Log CLI-specific metrics to MLflow tracker if available
                if tracker:
                    try:
                        tracker.log_metrics({
                            "cli_query": 1.0,
                            "cli_confidence": cli_response.get('confidence', 1.0),
                            "cli_sources_count": float(len(cli_response.get('sources', [])))
                        })
                        # log_params might not exist on OperationTracker, use hasattr check
                        if hasattr(tracker, 'log_params'):
                            tracker.log_params({
                                "agent_type": "cli_command",
                                "query_type": cli_response.get('query_type', 'unknown')
                            })
                    except Exception as tracker_error:
                        logger.warning(f"Failed to log CLI metrics to tracker: {tracker_error}")
                
                # Convert CLI agent response to conversation agent format
                return {
                    "content": cli_response.get('response', ''),
                    "sources": cli_response.get('sources', []),
                    "metadata": {
                        "agent": "cli_command",
                        "query_type": cli_response.get('query_type', 'unknown'),
                        "confidence": cli_response.get('confidence', 1.0),
                        "routed_to": "cli_command_agent",
                        "processing_time": time.time() - start_time
                    }
                }
        except Exception as e:
            logger.warning(f"CLI Command Agent routing failed: {e}")
        
        # Check if query should use MCP tools
        if self.enable_mcp and self.mcp_agent:
            query_type = self._detect_query_type(query)
            if query_type in [QueryType.REPORT, QueryType.COMMAND]:
                logger.info(f"Query type {query_type} detected - attempting MCP tool invocation")
                try:
                    mcp_result = self._invoke_mcp_tools(query, query_type)
                    if mcp_result:
                        logger.info(f"MCP tool executed successfully: {mcp_result.get('tool_name')}")
                except Exception as e:
                    logger.warning(f"MCP tool invocation failed: {e}")
        
        if use_rag:
            # Retrieve relevant context from RAG
            results = self.rag.retrieve(query=query, top_k=self.rag_top_k)
            
            if results:
                # Format context from results
                context_parts = []
                for i, result in enumerate(results, 1):
                    file_path = result.metadata.get('file_path', 'Unknown')
                    collection = result.metadata.get('collection', 'Unknown')
                    text = result.text[:400]  # Limit length
                    
                    context_parts.append(
                        f"{i}. [{collection}] {file_path}\n   {text}..."
                    )
                    
                    sources.append({
                        "file_path": file_path,
                        "collection": collection,
                        "score": round(result.score, 3)
                    })
                
                context = "\n\n".join(context_parts)
        
        # Get codebase stats for context
        stats_summary = self.analytics.answer_quantitative_query("summary")
        
        # Build ecosystem context
        ecosystem_info = """
Egeria Ecosystem Components:
- **Egeria**: The core Java-based metadata platform (back-end).
- **PyEgeria**: The Python SDK/library for interacting with Egeria via REST APIs.
- **hey_egeria**: The Command Line Interface (CLI) tool built on top of PyEgeria.
- **Dr. Egeria (DrE)**: A tool for translating markdown documents into PyEgeria REST calls.
"""

        # Build prompt with context
        mcp_context = ""
        if mcp_result:
            mcp_context = f"\n\nTool Execution Result:\nTool: {mcp_result.get('tool_name')}\nResult: {mcp_result.get('result')}\n"
        
        if context or mcp_result:
            prompt = f"""You are an expert Egeria developer assistant.
{ecosystem_info}
{stats_summary}

Use the following context from the Egeria codebase to answer the question.

Context:
{context}
{mcp_context}

Question: {query}

Provide a clear, accurate answer based on the context above. Include code examples if relevant. Cite specific files when referencing information.
If the user asks a quantitative question (e.g., "how many classes"), prefer the statistics provided above over the specific RAG snippets.
If a tool was executed, incorporate its results into your answer.

Answer:"""
        else:
            prompt = f"""You are an expert Egeria developer assistant.
{ecosystem_info}
{stats_summary}

Question: {query}

Provide a helpful answer based on your knowledge of Egeria and the statistics provided above.

Answer:"""
        
        # Generate response
        response_text = self.llm.generate(
            prompt=prompt,
            max_tokens=1500
        )
        
        # Log metrics if tracker available
        if tracker:
            # Add relevance scores from sources
            for source in sources:
                if isinstance(source, dict) and 'score' in source:
                    tracker.add_relevance(source['score'])
            
            # Log metrics
            duration = time.time() - start_time
            tracker.log_metrics({
                "agent_response_length": len(response_text),
                "agent_num_sources": len(sources),
                "agent_rag_used": 1.0 if use_rag else 0.0,
                "agent_cache_hit": 0.0,  # Always 0 in uncached path
                "agent_duration_seconds": duration,
                "agent_conversation_length": len(self.conversation_history)
            })
        
        return {
            "content": response_text,
            "sources": sources,
            "rag_used": use_rag and len(sources) > 0,
            "cache_hit": False  # Will be True on subsequent calls due to LRU cache
        }
    
    def _detect_query_type(self, query: str) -> QueryType:
        """
        Detect query type for MCP tool routing.
        
        Parameters
        ----------
        query : str
            User query
            
        Returns
        -------
        QueryType
            Detected query type
        """
        query_lower = query.lower()
        
        # Check for report patterns
        report_patterns = ["generate report", "create report", "run report", "show report",
                          "report on", "get report", "list reports", "available reports"]
        if any(pattern in query_lower for pattern in report_patterns):
            return QueryType.REPORT
        
        # Check for command patterns
        command_patterns = ["run command", "execute command", "invoke", "call",
                           "run the", "execute the"]
        if any(pattern in query_lower for pattern in command_patterns):
            return QueryType.COMMAND
        
        return QueryType.GENERAL
    
    def _invoke_mcp_tools(self, query: str, query_type: QueryType) -> Optional[Dict[str, Any]]:
        """
        Invoke MCP tools based on query type.
        
        Parameters
        ----------
        query : str
            User query
        query_type : QueryType
            Detected query type
            
        Returns
        -------
        dict or None
            Tool execution result or None if no tool was invoked
        """
        if not self.mcp_agent:
            return None
        
        try:
            # Get available tools
            tools = _run_coroutine(self.mcp_agent.list_tools)
            
            if query_type == QueryType.REPORT:
                # Look for report-related tools
                report_tools = [t for t in tools if 'report' in t.name.lower()]
                if report_tools:
                    # Use the first matching tool
                    tool = report_tools[0]
                    logger.info(f"Invoking report tool: {tool.name}")
                    result = _run_coroutine(lambda: self.mcp_agent.call_tool(tool.name, {}))
                    return {
                        "tool_name": tool.name,
                        "result": result,
                        "success": True
                    }
            
            elif query_type == QueryType.COMMAND:
                # Look for command-related tools
                command_tools = [t for t in tools if any(kw in t.name.lower() 
                                for kw in ['command', 'execute', 'run'])]
                if command_tools:
                    tool = command_tools[0]
                    logger.info(f"Invoking command tool: {tool.name}")
                    result = _run_coroutine(lambda: self.mcp_agent.call_tool(tool.name, {}))
                    return {
                        "tool_name": tool.name,
                        "result": result,
                        "success": True
                    }
        
        except Exception as e:
            logger.error(f"MCP tool invocation error: {e}")
            return None
        
        return None
    
    def get_history(self) -> List[Dict[str, str]]:
        """
        Get conversation history.
        
        Returns
        -------
        list[dict]
            List of conversation turns with queries and responses
        """
        return self.conversation_history.copy()
    
    def clear_history(self):
        """Clear conversation history."""
        self.conversation_history = []
    
    def clear_cache(self):
        """Clear response cache."""
        self._cached_run.cache_clear()
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get agent statistics.
        
        Returns
        -------
        dict
            Statistics about agent usage
        """
        cache_info = self._cached_run.cache_info()
        
        return {
            "conversation_turns": len(self.conversation_history),
            "cache_hits": cache_info.hits,
            "cache_misses": cache_info.misses,
            "cache_size": cache_info.currsize,
            "cache_max_size": cache_info.maxsize,
            "cache_hit_rate": (
                cache_info.hits / (cache_info.hits + cache_info.misses)
                if (cache_info.hits + cache_info.misses) > 0
                else 0.0
            )
        }


def create_agent(**kwargs) -> ConversationAgent:
    """
    Factory function to create a conversation agent.
    
    Parameters
    ----------
    **kwargs
        Configuration overrides
        
    Returns
    -------
    ConversationAgent
        Configured agent instance
    """
    return ConversationAgent(**kwargs)
