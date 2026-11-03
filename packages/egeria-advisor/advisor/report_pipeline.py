"""
Report pipeline for Egeria Advisor.

Handles QueryType.REPORT queries by:
1. Semantic search over question_spec entries (local, no MCP required)
2. Calling pyegeria MCP find_report_specs as a secondary strategy
3. Selecting the best matching spec
4. Calling run_report with extracted parameters
5. Returning formatted output
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
from loguru import logger

import numpy as np


def _run_async(coro, timeout: int = 90) -> Any:
    """Run an async coroutine from sync code safely."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're inside an event loop (e.g., Jupyter) — use a new thread
            result_container: list = []
            exc_container: list = []

            def thread_target():
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                try:
                    result_container.append(new_loop.run_until_complete(coro))
                except Exception as exc:
                    exc_container.append(exc)
                finally:
                    new_loop.close()

            t = threading.Thread(target=thread_target, daemon=True)
            t.start()
            t.join(timeout=timeout)
            if t.is_alive():
                raise TimeoutError(f"MCP operation timed out after {timeout}s")
            if exc_container:
                raise exc_container[0]
            return result_container[0] if result_container else None
    except RuntimeError:
        pass

    return asyncio.run(coro)


class MCPToolError(Exception):
    """An MCP tool returned an error response (e.g. unknown report, unsupported
    output_format, Egeria 4xx). Raised by _unwrap_mcp_content so callers can
    surface the real reason instead of treating it as an empty result."""


def _unwrap_mcp_content(raw: Any) -> Any:
    """
    MCP tool results come back as a list of content blocks:
      [{"type": "text", "text": "<json-or-text>"}, ...]
    Extract and parse the text.  If the text is JSON, return the parsed object.
    If the result is an error string, return None so callers can handle gracefully.
    """
    if raw is None:
        return None

    # Already unwrapped (string or dict)
    if isinstance(raw, (dict, str)) and not isinstance(raw, list):
        return _maybe_parse_json(raw)

    if isinstance(raw, list):
        texts = []
        for item in raw:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))
            elif isinstance(item, str):
                texts.append(item)

        if not texts:
            return raw  # Unknown structure — return as-is

        combined = "\n".join(texts).strip()

        # Check for error responses from the MCP server. Raise (rather than
        # return None) so callers can distinguish a genuine empty result from a
        # tool error and surface the real reason.
        if combined.startswith("Error ") or "Error executing tool" in combined:
            logger.warning(f"MCP tool returned error: {combined[:200]}")
            raise MCPToolError(combined)

        return _maybe_parse_json(combined)

    return raw


def _normalise_spec_list(raw: Any) -> List[Dict[str, Any]]:
    """Normalise whatever find_report_specs returned into a list of spec dicts."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("Matching Report Specs", "specs", "result", "matches", "items"):
            if key in raw and isinstance(raw[key], list):
                return raw[key]
        if "report_spec" in raw or "name" in raw or "spec_name" in raw:
            return [raw]
    return []


def _deduplicate_specs(specs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove duplicate entries for the same report_spec name, keeping the first."""
    seen: set = set()
    result = []
    for spec in specs:
        name = spec.get("report_spec") or spec.get("name") or spec.get("spec_name") or ""
        if name and name not in seen:
            seen.add(name)
            result.append(spec)
    return result


def _maybe_parse_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return value


def _get_qs_field(item: Any, field: str) -> list:
    """Extract a field from a QuestionSpec object or a plain dict."""
    if isinstance(item, dict):
        return item.get(field, []) or []
    return getattr(item, field, []) or []


class QuestionSpecIndex:
    """
    Lazy in-memory semantic index over all question_spec entries in base_report_specs.

    Uses sentence-transformers for embedding and numpy cosine similarity for search.
    No external vector store required — the corpus is small enough (~1000
    questions) that in-memory matrix multiply is fast.

    Thread-safe: build is protected by a lock; after first build the object is read-only.
    """

    _EMBED_MODEL = "all-MiniLM-L6-v2"
    # Questions are hints, not exact keys — keep the floor low so real-world
    # phrasing still surfaces candidates (ranking + disambiguation handle precision).
    _DEFAULT_THRESHOLD = 0.28
    _DEFAULT_TOP_K = 5
    # Additive boost applied to a spec whose question_spec is tagged with the
    # selected perspective (or "any"). A hint that re-ranks, never a gate.
    _PERSPECTIVE_BOOST = 0.08

    # Paths relative to the egeria-advisor project root. Report specs moved to
    # the repo-level config/ on 2026-09-06, hence the ../../ — see
    # advisor/mcp_config.py::resolve_report_specs_dir().
    # Add new per-perspective or per-domain JSON files here; no other code change needed.
    _JSON_SOURCES = [
        "../../config/report_specs/report_specs_annotated.json",
        "../../config/report_specs/plain_spec_question_specs_batch1.json",
        "../../config/report_specs/developer_question_specs.json",
    ]

    def __init__(self, project_root: Optional[str] = None) -> None:
        self._embeddings: Optional[np.ndarray] = None  # (N, D) float32, L2-normalised
        self._entries: List[tuple] = []                # (spec_name, perspectives, question)
        self._model = None
        self._lock = threading.Lock()
        # Resolve project root: caller-supplied → .env lookup → parent of advisor package
        if project_root:
            self._root = project_root
        else:
            import os
            self._root = os.environ.get(
                "EGERIA_ADVISOR_ROOT",
                str(Path(__file__).parent.parent),
            )

    def _load_json_sources(self) -> Dict[str, Any]:
        """Load and merge spec entries from all JSON source files.

        When the same spec name appears in multiple files (e.g. TypeDef in batch1
        and in developer_question_specs.json), the question_spec lists are concatenated
        so all perspectives are represented in the index.
        """
        import json as _json
        merged: Dict[str, Any] = {}
        for rel_path in self._JSON_SOURCES:
            full_path = Path(self._root) / rel_path
            if not full_path.exists():
                logger.debug(f"QuestionSpecIndex: {full_path} not found, skipping.")
                continue
            try:
                with open(full_path) as f:
                    data = _json.load(f)
                for spec_name, entry in data.items():
                    new_qs = entry.get("question_spec")
                    if not new_qs:
                        continue
                    if spec_name not in merged:
                        merged[spec_name] = entry
                    else:
                        # Append new perspective blocks; avoid exact duplicates
                        existing = merged[spec_name].setdefault("question_spec", [])
                        existing_persp = {
                            frozenset(i.get("perspectives", []))
                            for i in existing
                        }
                        for item in (new_qs if isinstance(new_qs, list) else [new_qs]):
                            key = frozenset(item.get("perspectives", []))
                            if key not in existing_persp:
                                existing.append(item)
                                existing_persp.add(key)
            except Exception as exc:
                logger.warning(f"QuestionSpecIndex: failed to load {full_path}: {exc}")
        return merged

    def _load_registry_sources(self) -> List[tuple]:
        """Read question_spec entries from the pyegeria runtime registry.

        Returns a flat list of (spec_name, perspectives, question) triples —
        the same shape used by _build.  Specs already indexed from JSON are
        skipped here; the registry supplements, not replaces, the file-based data.
        Called after load_egeria_report_specs() has merged Egeria data in.
        """
        entries: List[tuple] = []
        try:
            from pyegeria.view.base_report_formats import get_report_registry
            registry = get_report_registry()
        except Exception as exc:
            logger.debug(f"QuestionSpecIndex: pyegeria registry unavailable — {exc}")
            return entries

        for label, fs in registry.items():
            qspec = getattr(fs, "question_spec", None)
            if not qspec:
                continue
            for item in qspec:
                perspectives = list(getattr(item, "perspectives", []) or [])
                questions = list(getattr(item, "questions", []) or [])
                for q in questions:
                    if q:
                        entries.append((label, perspectives, q))
        logger.debug(f"QuestionSpecIndex: registry sources yielded {len(entries)} entries")
        return entries

    def invalidate(self) -> None:
        """Clear the built index so the next search triggers a full rebuild.

        Call this after load_egeria_report_specs() has merged new data into
        the pyegeria registry so the rebuild picks up the fresh question_specs.
        """
        with self._lock:
            self._embeddings = None
            self._entries = []
            self._model = None

    def _build(self) -> None:
        """Build the index from JSON files + pyegeria registry. Called once under lock."""
        try:
            # Ensure .env is loaded before huggingface_hub (pulled in by
            # sentence_transformers) reads HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE
            # from os.environ — it only reads them once, at import time.
            from dotenv import load_dotenv
            load_dotenv()
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            logger.warning(f"QuestionSpecIndex: sentence-transformers not available — {exc}. Semantic search disabled.")
            return

        spec_data = self._load_json_sources()
        if not spec_data:
            logger.warning("QuestionSpecIndex: no question_spec source files found. Semantic search disabled.")
            return

        model = SentenceTransformer(self._EMBED_MODEL)

        texts: List[str] = []
        entries: List[tuple] = []

        # Source 1: JSON files
        for spec_name, entry in spec_data.items():
            for item in entry.get("question_spec", []):
                perspectives = item.get("perspectives", []) or []
                questions = item.get("questions", []) or []
                for q in questions:
                    if q:
                        entries.append((spec_name, perspectives, q))
                        texts.append(q)

        # Source 2: pyegeria runtime registry (includes Egeria-sourced specs after
        # load_egeria_report_specs has run).  Deduplicate by (spec, question) pair.
        seen = {(spec, q) for spec, _, q in entries}
        for spec_name, perspectives, q in self._load_registry_sources():
            if (spec_name, q) not in seen:
                entries.append((spec_name, perspectives, q))
                texts.append(q)
                seen.add((spec_name, q))

        if not texts:
            logger.warning("QuestionSpecIndex: question_spec entries found but no questions — index is empty.")
            return

        embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        self._model = model
        self._entries = entries
        self._embeddings = np.array(embeddings, dtype=np.float32)
        logger.info(
            f"QuestionSpecIndex: built index with {len(texts)} questions "
            f"from {len(spec_data)} JSON specs + pyegeria registry."
        )

    def _ensure_built(self) -> None:
        if self._embeddings is not None:
            return
        with self._lock:
            if self._embeddings is not None:
                return
            self._build()

    def search(
        self,
        query: str,
        *,
        top_k: int = _DEFAULT_TOP_K,
        perspective: Optional[str] = None,
        threshold: float = _DEFAULT_THRESHOLD,
    ) -> List[Dict[str, Any]]:
        """
        Find the best-matching report specs for *query*.

        Returns a list of dicts with keys: report_spec, score, perspectives, questions.
        Ordered by descending score; at most *top_k* unique specs returned.
        Returns [] if nothing exceeds *threshold*.
        """
        self._ensure_built()
        with self._lock:
            embeddings = self._embeddings
            model = self._model
            entries = self._entries

        if embeddings is None or model is None or not entries:
            return []

        query_vec = model.encode([query], normalize_embeddings=True, show_progress_bar=False)[0]
        scores: np.ndarray = embeddings @ query_vec  # cosine similarity, shape (N,)

        # Perspectives are *hints*, not gates: when a perspective is given, give a
        # small additive boost to entries tagged with it (or "any"), so a report
        # relevant to the selected role ranks higher — but a report is never hidden
        # just because the role isn't tagged on it.
        if perspective:
            persp_lower = perspective.strip().lower()
            for i, (_, persp_list, _) in enumerate(entries):
                normed = [p.strip().lower() for p in persp_list]
                if persp_lower in normed or "any" in normed:
                    scores[i] = float(scores[i]) + self._PERSPECTIVE_BOOST

        # Collect best score per unique spec name
        best_per_spec: Dict[str, float] = {}
        for idx in range(len(entries)):
            s = float(scores[idx])
            if s < threshold:
                continue
            spec_name = entries[idx][0]
            if s > best_per_spec.get(spec_name, -1.0):
                best_per_spec[spec_name] = s

        if not best_per_spec:
            return []

        ranked = sorted(best_per_spec.items(), key=lambda kv: -kv[1])[:top_k]
        return [
            {"report_spec": name, "score": score, "perspectives": [], "questions": []}
            for name, score in ranked
        ]


# Module-level singleton — shared across all ReportPipeline instances.
_question_index = QuestionSpecIndex()


def _conn_is_complete(conn: Dict[str, Any]) -> bool:
    """A usable pyegeria connection has a server, a platform URL, a user id, and
    either a bearer token (signed-in caller) or a password (service account)."""
    return bool(conn.get("view_server") and conn.get("platform_url") and conn.get("user_id")
                and (conn.get("token") or conn.get("user_pwd")))


class ReportPipeline:
    """
    Pipeline for discovering and executing Egeria reports via MCP.

    The pipeline is lazy — MCP agent is only connected on first use.
    All public methods are synchronous to match the existing RAG dispatch pattern.
    """

    def __init__(self, config_path: str = str(Path(__file__).parent / "configdata" / "mcp_servers.json")):
        self._config_path = config_path
        self._agent = None          # lazy MCP agent
        self._egeria_specs_tried = False  # attempt once per process lifetime
        # Read report tuning params from advisor.yaml (with safe fallbacks)
        try:
            import yaml as _yaml
            _cfg_path = Path(__file__).parent / "configdata" / "advisor.yaml"
            with open(_cfg_path) as _f:
                _cfg = _yaml.safe_load(_f)
            _rep = _cfg.get("reports", {})
            self._default_page_size: int = int(_rep.get("page_size", 100))
            self._starts_with_on_filter: bool = bool(_rep.get("starts_with_on_filter", True))
        except Exception:
            self._default_page_size = 100
            self._starts_with_on_filter = True
        # Set by run_report() to the failure reason when execution errors/times out;
        # None means the last run either succeeded or returned a genuine empty result.
        self._last_run_error: Optional[str] = None

    def _read_pyegeria_connection(
        self, egeria_credentials: Optional[Dict[str, str]] = None
    ) -> Dict[str, str]:
        """Extract Egeria connection params for report execution.

        view_server/platform_url are static — same Egeria instance for everyone —
        and come from the pyegeria MCP server config section. user_id/user_pwd
        come from the authenticated caller's own credentials when given (see
        advisor.auth.resolve_egeria_credentials); falls back to the .env-backed
        service account when egeria_credentials is None (anonymous/background use).
        """
        from advisor.auth import resolve_egeria_credentials
        from advisor.mcp_config import get_pyegeria_platform_config
        creds = resolve_egeria_credentials(egeria_credentials)
        try:
            conn = get_pyegeria_platform_config(self._config_path)
            return {
                "view_server": conn["view_server"],
                "platform_url": conn["platform_url"],
                "user_id": creds["user_id"],
                "user_pwd": creds["password"],
                # Signed-in caller: Egeria bearer token from the app JWT (the
                # password is never carried since 2026-09-04); anonymous or
                # background caller: empty, and user_pwd is the service account.
                "token": creds.get("token", ""),
            }
        except Exception:
            return {}

    def _try_refresh_egeria_specs(self) -> None:
        """Attempt to load question specs from Egeria into the pyegeria registry.

        Uses a direct EgeriaTech client (not MCP) so this works even when
        the MCP agent hasn't been started yet.  Silently skips if Egeria is
        unreachable or pyegeria is not installed.  Invalidates _question_index so
        the next search rebuilds from the merged registry.
        """
        if self._egeria_specs_tried:
            return
        self._egeria_specs_tried = True  # don't retry on failure
        conn = self._read_pyegeria_connection()
        if not _conn_is_complete(conn):
            logger.debug("ReportPipeline: incomplete Egeria connection config — skipping spec refresh")
            return
        try:
            from pyegeria.egeria_tech_client import EgeriaTech
            from pyegeria.view.base_report_formats import load_egeria_report_specs
            client = EgeriaTech(
                view_server=conn["view_server"],
                platform_url=conn["platform_url"],
                user_id=conn["user_id"],
                user_pwd=conn["user_pwd"],
            )
            from advisor.auth import apply_token
            apply_token(client, conn.get("token"))
            refreshed = load_egeria_report_specs(client)
            if refreshed:
                _question_index.invalidate()
                logger.info("ReportPipeline: merged Egeria question specs into index")
        except Exception as exc:
            logger.info(f"ReportPipeline: Egeria spec refresh skipped [{type(exc).__name__}] — {exc}")

    def _ensure_agent(self):
        """Connect to MCP servers if not already done. Raises ConnectionError if unreachable."""
        if self._agent is not None and self._agent._initialized:
            return

        from advisor.mcp_agent import initialize_mcp_agent
        try:
            # 30s: MCP init spawns two Python subprocesses and connects to Egeria,
            # which takes ~5–15s on first call.  8s was too tight for cold starts.
            self._agent = _run_async(
                initialize_mcp_agent(config_path=self._config_path), timeout=30
            )
        except (TimeoutError, Exception) as exc:
            self._agent = None
            raise ConnectionError(f"Egeria MCP server not reachable: {exc}") from exc

    def _call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Synchronously call an MCP tool and unwrap MCP content envelope."""
        self._ensure_agent()
        raw = _run_async(self._agent.execute_tool(tool_name, arguments))
        return _unwrap_mcp_content(raw)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def find_specs(self, query: str, perspective: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Find report specs relevant to *query*.

        Strategy:
        1. Semantic search over question_spec entries (local, no MCP, handles paraphrases)
        2. MCP find_report_specs with extracted keywords (confirms spec exists in live server)
        3. Keyword matching against report names (last resort)

        Returns a list of spec dicts with at least a 'report_spec' or 'name' key.
        """
        # Strategy 1: semantic similarity over question_spec entries
        try:
            semantic_hits = _question_index.search(query, perspective=perspective)
            if semantic_hits:
                logger.debug(
                    f"Semantic search found {len(semantic_hits)} specs: "
                    + ", ".join(f"{h['report_spec']}({h['score']:.2f})" for h in semantic_hits)
                )
                return semantic_hits
        except Exception as exc:
            logger.warning(f"Semantic search failed: {exc}")

        # Strategy 2: MCP question-based search with extracted keywords
        stop = {"show", "me", "the", "a", "an", "list", "all", "get", "run",
                "report", "reports", "for", "of", "in", "on", "about", "what",
                "how", "many", "is", "are", "do", "we", "have", "our", "can",
                "i", "see", "find", "give", "tell", "display", "view"}

        keywords = [w.strip("?.,!") for w in query.lower().split()
                    if w.strip("?.,!") not in stop and len(w.strip("?.,!")) > 2]

        search_terms = []
        if keywords:
            search_terms.append(" ".join(keywords))
            search_terms.extend(keywords)

        for term in search_terms:
            try:
                args: Dict[str, Any] = {"question": term}
                if perspective:
                    args["perspective"] = perspective

                raw = self._call_tool("find_report_specs", args)
                if raw is not None:
                    specs = _normalise_spec_list(raw)
                    if specs:
                        # Assign keyword-match score based on name overlap with query keywords
                        for s in specs:
                            if "score" not in s or s["score"] == 0.0:
                                name = (s.get("report_spec") or s.get("name") or "").lower()
                                hits = sum(1 for kw in keywords if kw in name)
                                s["score"] = min(0.5 + 0.1 * hits, 0.75)
                        return _deduplicate_specs(specs)
            except Exception as e:
                logger.warning(f"find_report_specs({term!r}) failed: {e}")
                continue

        # Strategy 3: keyword matching against report names
        return self._find_specs_by_keywords(query)

    def _find_specs_by_keywords(self, query: str) -> List[Dict[str, Any]]:
        """
        Fallback: fetch all report names and return those whose name contains
        any keyword from the query.
        """
        try:
            all_specs = self._call_tool("list_reports", {})
            if not isinstance(all_specs, dict):
                return []

            query_lower = query.lower()
            # Extract meaningful keywords (ignore stop words)
            stop = {"show", "me", "the", "a", "an", "list", "all", "get", "run",
                    "report", "reports", "for", "of", "in", "on", "about", "what"}
            keywords = [w.strip("?.,!") for w in query_lower.split()
                        if w.strip("?.,!") not in stop and len(w) > 2]

            if not keywords:
                return []

            matches = []
            for name in all_specs.keys():
                name_lower = name.lower()
                hits = sum(1 for kw in keywords if kw in name_lower)
                if hits:
                    matches.append({"report_spec": name, "score": min(0.4 + 0.1 * hits, 0.65),
                                    "perspectives": [], "questions": []})

            return sorted(matches, key=lambda d: d["report_spec"])
        except Exception as e:
            logger.warning(f"Keyword fallback for find_specs failed: {e}")
            return []

    def run_report(
        self,
        report_name: str,
        search_string: str = "*",
        output_type: str = "DICT",
        page_size: Optional[int] = None,
        extra_params: Optional[Dict[str, Any]] = None,
        egeria_credentials: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        """
        Execute a named report and return the output string.

        page_size: limits graph nodes Egeria traverses. None → use self._default_page_size.
        egeria_credentials: the authenticated caller's {user_id, password}; falls back
            to the service account (see advisor.auth.resolve_egeria_credentials) when None.
        Returns None on failure.
        """
        effective_page_size = page_size if page_size is not None else self._default_page_size
        is_wildcard = not search_string or search_string.strip() in ("*", "")
        # Record why we return None so the caller can distinguish a genuine
        # "no records" empty result from an execution error/timeout, and never
        # mask a failure behind an unrelated convenience-listing fallback.
        self._last_run_error = None

        # Execute in-process via pyegeria's exec_report_spec (a fresh Egeria client
        # bound to this call's event loop) rather than the MCP run_report tool.
        # The MCP server reuses a single GLOBAL_EGERIA_CLIENT created in its startup
        # loop, which intermittently CLIENT_ERROR_400s when used from its tool-handler
        # loop. exec_report_spec is the same executor pyegeria's CLI uses and is
        # reliable. It raises ValueError for unknown-report / unsupported-format and
        # returns {"kind":"empty"} for no records.
        # Import from the package top level (stable export) — importing the
        # submodule directly can trip a circular import during lazy first load.
        from pyegeria import exec_report_spec

        conn = self._read_pyegeria_connection(egeria_credentials=egeria_credentials)
        if not _conn_is_complete(conn):
            raise ConnectionError("Egeria connection is not configured (config/mcp_servers.json → pyegeria.env)")
        # pyegeria >= 6.1.10 (ISSUE-86): exec_report_spec accepts the caller's
        # bearer token, so a signed-in user's report runs -- and its Egeria
        # provenance -- are theirs, not the service account's.
        bearer = conn.get("token") or None

        params: Dict[str, Any] = {
            "search_string": search_string,
            "page_size": effective_page_size,
            "start_from": 0,
        }
        # Prefix search is more efficient than full regex when a specific term is given
        if not is_wildcard and self._starts_with_on_filter:
            params["starts_with"] = True
        if extra_params:
            params.update(extra_params)
        # Egeria type names are always PascalCase — auto-capitalize first letter
        if "metadata_element_type" in params and isinstance(params["metadata_element_type"], str):
            met = params["metadata_element_type"].strip()
            if met and met[0].islower():
                params["metadata_element_type"] = met[0].upper() + met[1:]

        try:
            raw = exec_report_spec(
                report_name,
                output_format=output_type,
                params=params,
                view_server=conn["view_server"],
                view_url=conn["platform_url"],
                user=conn["user_id"],
                user_pass=conn["user_pwd"],
                token=bearer,
            )
        except ValueError as e:
            # Unknown report, unsupported output_format, or missing action —
            # surface honestly via _classify_run_error.
            self._last_run_error = str(e)
            logger.info(f"run_report({report_name}) report error: {e}")
            return None
        except Exception as e:
            err = type(e).__name__
            msg = f"{err}: {e}"
            if "timeout" in err.lower() or "timeout" in str(e).lower():
                self._last_run_error = "The report timed out (Egeria took too long to respond)."
                logger.error(f"run_report({report_name}) timed out: {e}")
                return None
            # Treat anything else (connection refused, 4xx/5xx transport) as a
            # reachability problem so the caller shows the "Egeria not reachable" path.
            logger.error(f"run_report({report_name}) failed [{err}]: {e}")
            raise ConnectionError(msg) from e

        if raw is None:
            return None
        if isinstance(raw, str):
            return raw
        if isinstance(raw, dict):
            kind = raw.get("kind")
            # pyegeria format_set_executor shapes:
            if kind == "empty":
                return None   # no records — caller shows "no results" message
            if kind == "absent":
                # pyegeria 6.2.17 (#518): not established / does not apply. The sentence IS the answer; any
                # partial numbers under `detail` are not, and must never be rendered as if measured.
                return raw.get("content") or "Not established."
            if kind == "json" and "data" in raw:
                val = raw["data"]
                return val if isinstance(val, str) else json.dumps(val, indent=2)
            if kind == "text" and "content" in raw:
                return raw["content"]
            # Legacy / fallback shapes
            for key in ("data", "output", "result", "content", "report", "text"):
                if key in raw:
                    val = raw[key]
                    return val if isinstance(val, str) else json.dumps(val, indent=2)
            return json.dumps(raw, indent=2)
        return str(raw)

    # ------------------------------------------------------------------
    # Output formatting helpers
    # ------------------------------------------------------------------

    _FORMAT_KEYWORDS: Dict[str, str] = {
        # User asks for table/structured output → DICT + table render
        "as a table": "TABLE",
        "in a table": "TABLE",
        "as table": "TABLE",
        "tabular": "TABLE",
        "structured": "TABLE",
        # User asks for full report narrative
        "full report": "REPORT",
        "as a report": "REPORT",
        "as report": "REPORT",
        "as markdown": "REPORT",
        "in markdown": "REPORT",
        # Editable Dr.Egeria form
        "as a form": "FORM",
        "as form": "FORM",
        "editable form": "FORM",
        # HTML
        "as html": "HTML",
        "in html": "HTML",
        # Raw JSON
        "as json": "JSON",
        "in json": "JSON",
        "as raw": "JSON",
        # Mermaid diagram
        "as a diagram": "MERMAID",
        "as diagram": "MERMAID",
        "mermaid": "MERMAID",
    }

    # Maps the web UI fmt tag values and pyegeria output_type names to internal format codes
    # Maps the fmt:'<tag>' value (lower-cased) set by the web UI / chat to the
    # canonical executor token. pyegeria's MCP run_report now accepts the full
    # set directly, so these are mostly identity; "markdown" is a friendly alias
    # for REPORT.
    _FMT_TAG_MAP: Dict[str, str] = {
        "list":         "LIST",
        "table":        "TABLE",
        "report":       "REPORT",
        "report-graph": "REPORT-GRAPH",
        "form":         "FORM",
        "md":           "MD",
        "markdown":     "REPORT",
        "mermaid":      "MERMAID",
        "html":         "HTML",
        "graph":        "GRAPH",
        "json":         "JSON",
        "dict":         "DICT",
    }

    def _detect_output_format(self, query: str) -> str:
        """
        Detect the requested output format from query text.
        Checks for an explicit fmt:'FORMAT' tag first (set by the web UI modal),
        then falls back to keyword matching, then defaults to DICT (rendered as a
        table). Returns a canonical executor token (LIST, TABLE, REPORT, FORM, MD,
        MERMAID, HTML, DICT, JSON, ...).
        """
        fm = self._FMT_TAG_RE.search(query)
        if fm:
            tag = fm.group(1).strip().lower()
            return self._FMT_TAG_MAP.get(tag, tag.upper())
        q = query.lower()
        for phrase, fmt in self._FORMAT_KEYWORDS.items():
            if phrase in q:
                return fmt
        return "DICT"

    # Formats pyegeria returns already-rendered as text (kind="text"); the advisor
    # passes them through unchanged so the browser renders the Markdown / HTML.
    _TEXT_FORMATS = {"LIST", "REPORT", "REPORT-GRAPH", "FORM", "MD", "MERMAID", "HTML", "GRAPH"}

    @staticmethod
    def _format_output(raw: Any, fmt: str, report_name: str, spec: Optional[Any] = None) -> str:
        """
        Convert raw report output (dict / list / str) to the requested display format.
        """
        fmt = (fmt or "DICT").upper()

        # Text formats are already rendered by pyegeria — return as-is.
        if fmt in ReportPipeline._TEXT_FORMATS and isinstance(raw, str):
            return raw

        # run_report() stringifies structured results — recover the structure for
        # TABLE/DICT/JSON rendering. If it isn't JSON, it's already-rendered text.
        if isinstance(raw, str) and fmt in ("TABLE", "DICT", "JSON"):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                return raw

        if isinstance(raw, str):
            return raw  # any other already-rendered text

        if fmt == "JSON":
            return f"```json\n{json.dumps(raw, indent=2)}\n```"

        # TABLE or DICT — render as a Markdown table.
        return ReportPipeline._dict_to_markdown_table(raw, report_name, spec=spec)

    @staticmethod
    def _dict_to_markdown_table(data: Any, title: str = "", spec: Optional[Any] = None) -> str:
        """Render a dict or list of dicts as a markdown table."""
        if not data:
            return f"*No results returned for {title}.*"

        # If a spec is provided, extract the columns definition
        columns = []
        if spec:
            for fmt_obj in spec.formats:
                if any(t in ("ALL", "TABLE") for t in fmt_obj.types):
                    columns = fmt_obj.attributes
                    break

        if columns:
            # Normalize list vs dictionary of elements to a list of dicts
            elements_list = []
            if isinstance(data, list):
                elements_list = [el for el in data if isinstance(el, dict)]
            elif isinstance(data, dict):
                sample_val = next(iter(data.values()), None)
                if isinstance(sample_val, dict):
                    elements_list = [el for el in data.values() if isinstance(el, dict)]
                else:
                    elements_list = [data]

            if elements_list:
                # Format using columns from the spec
                headers = [col.name for col in columns]
                markdown_lines = []
                markdown_lines.append("| " + " | ".join(headers) + " |")
                markdown_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")

                # Extract _get_element_value and materialize_egeria_summary from pyegeria
                from pyegeria.view.output_formatter import _get_element_value, materialize_egeria_summary

                for raw_el in elements_list:
                    # Materialize headers (like status, versions, created_by, etc.)
                    el_flat = materialize_egeria_summary(raw_el)
                    
                    row_vals = []
                    for col in columns:
                        val = None
                        if col.key in el_flat:
                            val = el_flat[col.key]
                        else:
                            val = _get_element_value(raw_el, col.key)
                            
                        if isinstance(val, list):
                            val = ", ".join(str(x) for x in val)
                        elif isinstance(val, dict):
                            val = json.dumps(val)
                        elif val is None:
                            val = ""
                        else:
                            val = str(val)
                            
                        val = val.replace("|", "\\|")
                        row_vals.append(val)
                        
                    markdown_lines.append("| " + " | ".join(row_vals) + " |")
                    
                return "\n".join(markdown_lines)

        rows: List[Dict[str, Any]] = []
        if isinstance(data, list):
            rows = [r if isinstance(r, dict) else {"Value": r} for r in data]
        elif isinstance(data, dict):
            # Could be {name: {props...}}, {name: [records...]}, or a flat {key: value} record
            sample_val = next(iter(data.values()), None)
            if isinstance(sample_val, dict):
                rows = [{"Name": k, **v} for k, v in data.items()]
            elif isinstance(sample_val, list):
                # Top-level key wraps a list of records — unwrap it
                inner: List[Dict[str, Any]] = []
                for v in data.values():
                    if isinstance(v, list):
                        inner.extend(r if isinstance(r, dict) else {"Value": str(r)} for r in v)
                rows = inner if inner else [{"Property": k, "Value": str(v)} for k, v in data.items()]
            else:
                rows = [{"Property": k, "Value": v} for k, v in data.items()]

        if not rows:
            return json.dumps(data, indent=2)

        # Collect all column names preserving insertion order
        cols: List[str] = []
        for row in rows:
            for k in row:
                if k not in cols:
                    cols.append(k)

        # Cap columns so the table stays readable (drop GUIDs / raw JSON blobs)
        _skip = {"guid", "qualifiedName", "versions", "additionalProperties", "extendedProperties"}
        display_cols = [c for c in cols if c.lower() not in _skip][:8] or cols[:8]

        header = "| " + " | ".join(display_cols) + " |"
        sep = "| " + " | ".join("---" for _ in display_cols) + " |"
        lines = [header, sep]
        for row in rows[:50]:  # safety cap
            cells = [str(row.get(c, "")).replace("|", "\\|")[:80] for c in display_cols]
            lines.append("| " + " | ".join(cells) + " |")

        return "\n".join(lines)

    # Convenience MCP listing tools are ONLY equivalent to the plain "list all X"
    # report specs — never to a sub-type report (e.g. Glossary-Terms, which lists
    # terms, not glossaries). Match the exact spec name, normalised, so a failed
    # sub-type report is never silently answered with an unrelated listing.
    _LISTING_TOOL_BY_SPEC = {
        "glossaries": "egeria_list_glossaries",
        "collections": "egeria_list_collections",
    }

    def _try_listing_tool(self, report_name: str) -> Optional[str]:
        """
        When a plain "list all X" report spec returns *no records*, fall back to the
        equivalent convenience MCP tool. Only fires for the exact all-listing specs
        (Glossaries, Collections) — sub-type reports get None so the caller reports
        the empty/failed result honestly instead of showing irrelevant data.
        """
        key = re.sub(r"[-_\s]", "", report_name).lower()
        tool = self._LISTING_TOOL_BY_SPEC.get(key)
        if tool is None:
            return None

        try:
            raw = self._call_tool(tool, {})
            if raw is None:
                return None
            return raw if isinstance(raw, str) else str(raw)
        except Exception as exc:
            logger.debug(f"_try_listing_tool({tool}) failed: {exc}")
            return None

    # DrE specs are designed for Dr.Egeria operators and contain operator-facing
    # fields (Journal, Term, Search Keywords) irrelevant to general users.
    # Subtract this from their score so plain pyegeria specs win when available.
    # 0.30 is needed because DrE question_spec questions are very literal and
    # score 0.90+ on common user queries like "show me glossaries".
    _DRE_SCORE_PENALTY = 0.30

    # If the top two candidates are within this margin after re-ranking, ask.
    _DISAMBIG_GAP = 0.15

    # Score above which we run the top spec without asking (overwhelming match).
    _AUTO_RUN_SCORE = 0.85

    @staticmethod
    def _is_dre_spec(name: str) -> bool:
        return "-dre-" in name.lower()

    def _rank_specs(self, specs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Re-rank specs: apply a score penalty to DrE specs so that plain pyegeria
        specs are preferred when both match the query equally well.
        """
        ranked = []
        for s in specs:
            name = s.get("report_spec") or s.get("name") or ""
            score = float(s.get("score", 0.0))
            if self._is_dre_spec(name):
                score = max(0.0, score - self._DRE_SCORE_PENALTY)
            ranked.append({**s, "score": score})
        ranked.sort(key=lambda x: -x["score"])
        return ranked

    def _disambiguate(self, query: str, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Return a clarification response listing the top matching report specs."""
        lines = [
            "I found several reports that could match your query. "
            "Which would you like to run?\n"
        ]
        for i, c in enumerate(candidates, 1):
            name = c.get("report_spec") or c.get("name") or ""
            score = c.get("score", 0.0)
            tag = " *(Dr.Egeria operator view)*" if self._is_dre_spec(name) else ""
            # Only show confidence for semantic scores (≥0.70); keyword-match scores
            # are heuristic and displaying them as percentages is misleading.
            score_str = f" — confidence {score:.0%}" if score >= 0.70 else ""
            lines.append(f"{i}. **{name}**{tag}{score_str}")
        lines.append(
            "\nReply with the number or the report name, "
            "or say **\"run report [name]\"** directly."
        )
        _names = [c.get("report_spec") or c.get("name") for c in candidates]
        return {
            "query": query,
            "response": "\n".join(lines),
            "query_type": "clarification",
            "candidates": _names,
            "next_context": {"task": "report_disambiguation", "candidates": _names},
            "sources": [],
            "num_sources": 0,
            "retrieval_time": 0.0,
            "generation_time": 0.0,
            "avg_relevance_score": candidates[0].get("score", 0.0) if candidates else 0.0,
            "context_length": 0,
        }

    # Matches "run report <name>" so a direct selection skips find_specs entirely.
    _RUN_REPORT_RE = re.compile(r"run\s+report\s+(.+)", re.IGNORECASE)
    # Extracts an optional search filter appended by the web UI: filter:'<value>'
    _FILTER_TAG_RE = re.compile(r"\s+filter:'([^']*)'", re.IGNORECASE)
    # Extracts an explicit output format tag appended by the web UI: fmt:'<FORMAT>'
    _FMT_TAG_RE = re.compile(r"\s+fmt:'([^']*)'", re.IGNORECASE)
    # Detects "what/which/are there reports about X" — discovery, not execution.
    _REPORT_DISCOVERY_RE = re.compile(
        r"^(?:what|which|are\s+there|is\s+there|find|list|show|search\s+for"
        r"|do\s+(?:we|you|i)\s+have|can\s+you\s+(?:show|list|find))"
        r"[\w\s]*report[\w\s]*"
        r"(?:about|for|on|covering|related\s+to|that|touching|deal(?:ing)?\s+with|regarding)\b",
        re.IGNORECASE,
    )

    @staticmethod
    def _norm_name(s: str) -> str:
        return re.sub(r"[-_\s]", "", s or "").lower()

    def _all_report_names(self) -> List[str]:
        """All known report spec names: the in-process pyegeria registry (277+
        specs) plus the curated JSON catalog. Cached after first build."""
        cached = getattr(self, "_report_names_cache", None)
        if cached is not None:
            return cached
        names: List[str] = []
        seen: set = set()
        try:
            from pyegeria.view.base_report_formats import get_report_registry
            for label, spec in get_report_registry().items():
                if label not in seen and getattr(spec, "action", None) is not None:
                    seen.add(label)
                    names.append(label)
        except Exception as exc:
            logger.debug(f"_all_report_names: registry unavailable — {exc}")
        try:
            for label in _question_index._load_json_sources():
                if label not in seen:
                    seen.add(label)
                    names.append(label)
        except Exception as exc:
            logger.debug(f"_all_report_names: JSON sources unavailable — {exc}")
        self._report_names_cache = names
        return names

    def match_report_name(self, text: str) -> Optional[tuple]:
        """
        Forgivingly match free text to a known report spec name.

        Returns ``(spec_name, confidence)`` for the best candidate, or ``None`` if
        nothing is even weakly plausible. Confidence: 1.0 exact (normalised),
        0.9 one normalised string contains the other, else a difflib similarity
        ratio. Callers gate on confidence (e.g. ≥0.9 to dispatch directly).
        """
        from difflib import SequenceMatcher

        if not text or not text.strip():
            return None
        t_norm = self._norm_name(text)
        if not t_norm:
            return None

        best_name: Optional[str] = None
        best_score = 0.0
        for spec_name in self._all_report_names():
            s_norm = self._norm_name(spec_name)
            if not s_norm:
                continue
            if s_norm == t_norm:
                return (spec_name, 1.0)
            if len(t_norm) >= 4 and (s_norm in t_norm or t_norm in s_norm):
                score = 0.9
            else:
                score = SequenceMatcher(None, t_norm, s_norm).ratio()
            if score > best_score:
                best_score, best_name = score, spec_name
        if best_name is None:
            return None
        return (best_name, best_score)

    def _resolve_report_name(self, name: str) -> str:
        """
        Resolve a fuzzy or camelCase report name to the exact spec catalog name.
        Used by the explicit "run report <name>" path, where the user has named a
        specific report — so we accept a strong fuzzy match and only fall back to
        the original string when nothing is close.
        """
        match = self.match_report_name(name)
        if match and match[1] >= 0.72:
            if match[0].lower() != name.lower():
                logger.info(f"Resolved report name {name!r} → {match[0]!r} (conf={match[1]:.2f})")
            return match[0]
        return name

    def _parse_report_directive(self, raw: str) -> tuple:
        """
        From the captured group of _RUN_REPORT_RE, extract:
          (report_name, search_string)
        Strips filter tag, fmt tag, and any trailing format keywords.
        """
        # Extract search filter if present
        fm = self._FILTER_TAG_RE.search(raw)
        if fm:
            search_string = fm.group(1).strip() or "*"
            raw = raw[:fm.start()].strip()
        else:
            search_string = "*"

        # Strip explicit fmt tag if present
        ft = self._FMT_TAG_RE.search(raw)
        if ft:
            raw = (raw[:ft.start()] + raw[ft.end():]).strip()

        # Strip format keywords that the web UI may have appended
        for phrase in sorted(self._FORMAT_KEYWORDS, key=len, reverse=True):
            if raw.lower().endswith(phrase):
                raw = raw[:-len(phrase)].strip()
                break

        return raw, search_string

    def _discover_reports(self, query: str, perspective: Optional[str]) -> Dict[str, Any]:
        """Return a formatted list of report specs that match the query topic."""
        try:
            specs = self.find_specs(query, perspective=perspective)
        except Exception:
            specs = []
        if not specs:
            return {
                "query": query,
                "response": (
                    "I couldn't find any report specs matching that topic. "
                    "Try browsing the **Reports** tab in the left sidebar, or ask me to *list all reports*."
                ),
                "query_type": "report",
                "sources": [], "num_sources": 0,
                "retrieval_time": 0.0, "generation_time": 0.0,
                "avg_relevance_score": 0.0, "context_length": 0,
            }
        ranked = self._rank_specs(specs)
        lines = ["Here are the report specs that match:\n"]
        for item in ranked[:10]:
            name = item.get("report_spec") or item.get("name") or item.get("spec_name") or ""
            q = item.get("question", "")
            family = item.get("family", "")
            if name:
                label = f"- **{name}**"
                if family:
                    label += f" *(family: {family})*"
                if q:
                    label += f" — {q}"
                lines.append(label)
        lines.append("\nClick one in the **Reports** sidebar to run it, or say **run report \\<name\\>**.")
        response = "\n".join(lines)
        return {
            "query": query,
            "response": response,
            "query_type": "report",
            "sources": [], "num_sources": len(ranked),
            "retrieval_time": 0.0, "generation_time": 0.0,
            "avg_relevance_score": 0.0, "context_length": len(response),
        }

    def process(
        self, query: str, perspective: Optional[str] = None,
        page_size: Optional[int] = None,
        egeria_credentials: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Full report pipeline: discover spec → run report → return response dict.

        Falls back to a helpful message if no spec is found or Egeria is unreachable.
        """
        # Kick off spec refresh in background on first call — non-blocking so the
        # current query is never delayed.  Subsequent queries benefit once done.
        if not self._egeria_specs_tried:
            import threading
            threading.Thread(target=self._try_refresh_egeria_specs, daemon=True).start()

        # Discovery: "what/which reports are about X" → list matching specs, don't run.
        if self._REPORT_DISCOVERY_RE.match(query.strip()):
            logger.info(f"Report discovery query: {query!r}")
            return self._discover_reports(query, perspective)

        # Direct dispatch: "run report <name>" bypasses find_specs / disambiguation.
        m = self._RUN_REPORT_RE.match(query.strip())
        if m:
            report_name, search_string = self._parse_report_directive(m.group(1).strip())
            # Normalise camelCase / variant spellings to the exact catalog name
            report_name = self._resolve_report_name(report_name)
            logger.info(f"Direct report dispatch: {report_name!r} search={search_string!r}")
            return self._execute_report(query, report_name, search_string=search_string, page_size=page_size,
                                         egeria_credentials=egeria_credentials)

        # Name-first: if the user typed (or selected the Report intent and typed) a
        # report name, match it directly before any semantic question matching.
        # The typed name must be a strong match — a vague chat question won't trip
        # this and falls through to find_specs below.
        core, search_string = self._parse_report_directive(query.strip())
        name_match = self.match_report_name(core)
        if name_match and name_match[1] >= 0.95:
            logger.info(
                f"Name-first dispatch: {core!r} → {name_match[0]!r} "
                f"(conf={name_match[1]:.2f}, search={search_string!r})"
            )
            return self._execute_report(
                query, name_match[0], search_string=search_string, page_size=page_size,
                egeria_credentials=egeria_credentials
            )

        try:
            specs = self.find_specs(query, perspective=perspective)
        except Exception as e:
            logger.error(f"ReportPipeline.find_specs raised: {e}")
            return _no_report_found(query)

        if not specs:
            logger.info("No matching report specs found for query")
            return _no_report_found(query)

        ranked = self._rank_specs(specs)
        top = ranked[0]
        second = ranked[1] if len(ranked) > 1 else None

        top_score = top.get("score", 0.0)
        second_score = second.get("score", 0.0) if second else 0.0

        # When two specs are equally plausible and score is not overwhelming, ask.
        if (second is not None
                and top_score < self._AUTO_RUN_SCORE
                and (top_score - second_score) < self._DISAMBIG_GAP):
            logger.info(
                f"Disambiguation: top={top.get('report_spec')} ({top_score:.2f}), "
                f"second={second.get('report_spec')} ({second_score:.2f})"
            )
            return self._disambiguate(query, ranked[:3])

        best = top
        report_name = (
            best.get("report_spec") or best.get("name") or
            best.get("spec_name") or best.get("report_name") or ""
        )
        if not report_name:
            logger.warning("Spec has no usable name field")
            return _no_report_found(query)

        return self._execute_report(query, report_name, num_specs_found=len(ranked), page_size=page_size,
                                     egeria_credentials=egeria_credentials)

    def _classify_run_error(self, run_error: str, report_name: str, fmt: str) -> str:
        """
        Turn pyegeria's raw execution error into an honest, specific user message.
        pyegeria already distinguishes unknown-report, unsupported-format and
        no-action cases — we surface that distinction instead of a generic failure.
        """
        # Strip a leading "ExceptionType: " prefix for readability.
        detail = re.sub(r"^[A-Za-z_][A-Za-z0-9_]*Error:\s*", "", run_error or "").strip()
        low = detail.lower()

        if "does not support requested output_format" in low or "does not support" in low and "output_format" in low:
            supported = self._spec_supported_formats_safe(report_name)
            sup = f" — supported: {', '.join(supported)}" if supported else ""
            return (
                f"The **{report_name}** report doesn't support the **{fmt}** output format{sup}.\n\n"
                f"> {detail}\n\n"
                "Pick a supported format and run it again."
            )
        if "unknown report spec" in low or "no matching column set" in low:
            # If the user asked for a non-standard format (MERMAID, HTML, …) the spec
            # may exist but simply not declare that format — pyegeria raises "unknown
            # report spec" in both cases.  Probe with DICT to distinguish.
            _rich_fmts = {"MERMAID", "HTML", "GRAPH", "REPORT-GRAPH"}
            if fmt.upper() in _rich_fmts:
                supported = self._spec_supported_formats_safe(report_name)
                if supported:
                    sup = ", ".join(supported)
                    return (
                        f"The **{report_name}** report doesn't support **{fmt}** output.\n\n"
                        f"Supported formats for this report: {sup}\n\n"
                        "Switch to one of those formats and run again."
                    )
                # spec found in registry but supported-format probe returned empty —
                # still likely a format issue, not a missing spec
                return (
                    f"**{report_name}** was found but doesn't appear to support **{fmt}** output.\n\n"
                    "Try **REPORT**, **LIST**, or **TABLE** format instead."
                )
            return (
                f"There's no report named **{report_name}**.\n\n"
                f"> {detail}\n\n"
                "Tips:\n"
                "- Ask me to *list available reports*, or click one from the left sidebar\n"
                "- Check the spelling — I match names forgivingly but this was too far off"
            )
        if "does not have an action property" in low:
            masters = _detail_to_masters().get(report_name, [])
            if masters:
                master_hint = "Run the parent report instead — it includes this as a linked detail section:\n" + \
                              "".join(f"- **{m}**\n" for m in masters)
            else:
                master_hint = "Run the parent report instead — it includes this as a linked detail section."
            return (
                f"**{report_name}** is a detail/sub-view and can't be run on its own.\n\n"
                + master_hint
            )
        if "timeout" in low or "timed out" in low:
            return (
                f"The **{report_name}** report timed out (Egeria took too long).\n\n"
                "Tips:\n"
                "- Try again — transient timeouts often clear on a second run\n"
                "- Add a search filter to reduce the result size\n"
                f"- To run via Dr.Egeria: `[[{report_name}]]`"
            )
        # Generic, but still show the real reason.
        return (
            f"The report **{report_name}** could not be completed.\n\n"
            f"> {detail}\n\n"
            "Tips:\n"
            "- Try again — transient errors can clear on a second run\n"
            "- Narrow the request with a search filter\n"
            f"- To run via Dr.Egeria: `[[{report_name}]]`"
        )

    def _spec_supported_formats_safe(self, report_name: str) -> List[str]:
        """Best-effort list of the browser-renderable formats a spec declares
        (for error hints). Returns [] if the registry is unavailable."""
        try:
            from pyegeria.view.base_report_formats import get_report_registry
            fs = get_report_registry().get(report_name)
            if fs is None:
                return []
            declared = {
                t.upper()
                for f in (getattr(fs, "formats", []) or [])
                for t in (getattr(f, "types", []) or [])
            }
            browser = ["LIST", "TABLE", "REPORT", "FORM", "MERMAID", "HTML", "MD", "DICT", "JSON"]
            if "ALL" in declared:
                return browser
            return [b for b in browser if b in declared]
        except Exception:
            return []

    def _execute_report(
        self, query: str, report_name: str, num_specs_found: int = 1,
        search_string: str = "*",
        page_size: Optional[int] = None,
        extra_params: Optional[Dict[str, Any]] = None,
        output_type: Optional[str] = None,
        egeria_credentials: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Shared execution path: run a named report and format the result."""
        # Normalise to exact catalog name (catches camelCase / hyphen / space variants)
        report_name = self._resolve_report_name(report_name)
        fmt = output_type or self._detect_output_format(query)
        display_fmt = "REPORT" if fmt == "MARKDOWN" else fmt
        # For TABLE display we request DICT from pyegeria (full structured data) and
        # render the markdown table ourselves via _dict_to_markdown_table.  Pyegeria's
        # native TABLE format is terminal-optimised and can omit columns.
        mcp_output_type = "DICT" if display_fmt == "TABLE" else display_fmt

        logger.info(
            f"Running report: {report_name} (output_type={mcp_output_type}, "
            f"display_fmt={fmt}, search_string={search_string!r}, page_size={page_size or self._default_page_size})"
        )
        # Execution is in-process (run_report → exec_report_spec) and does not need
        # the MCP agent; `egeria_reachable` reflects whether that in-process call
        # could reach Egeria. The MCP agent is only needed for the optional
        # listing-tool fallback below, which self-guards.
        egeria_reachable = True
        connection_err: Optional[str] = None
        raw_output = None
        try:
            raw_output = self.run_report(
                report_name, search_string=search_string,
                output_type=mcp_output_type, page_size=page_size,
                extra_params=extra_params,
                egeria_credentials=egeria_credentials,
            )
        except ConnectionError as exc:
            egeria_reachable = False
            connection_err = str(exc)

        # Distinguish a real execution failure/timeout from a genuine empty result.
        run_error = getattr(self, "_last_run_error", None)

        # Only fall back to a convenience listing tool when the report ran cleanly
        # but returned no records — never to paper over an error/timeout, and only
        # for the exact all-listing specs (handled inside _try_listing_tool).
        if raw_output is None and egeria_reachable and not run_error:
            try:
                raw_output = self._try_listing_tool(report_name)
            except Exception as exc:
                logger.debug(f"listing-tool fallback skipped: {exc}")

        output = self._format_output(raw_output, fmt, report_name) if raw_output is not None else None

        if output is None:
            if run_error:
                err_response = self._classify_run_error(run_error, report_name, fmt)
                return {
                    "query": query,
                    "response": err_response,
                    "query_type": "report",
                    "report_name": report_name,
                    "sources": [],
                    "num_sources": 0,
                    "retrieval_time": 0.0,
                    "generation_time": 0.0,
                    "avg_relevance_score": 0.0,
                    "context_length": 0,
                }
            if not egeria_reachable:
                detail = f"\n\n*Connection detail: {connection_err}*" if connection_err else ""
                err_response = (
                    f"I found the **{report_name}** report, but Egeria is not reachable "
                    "right now.\n\n"
                    f"To run this report via Dr.Egeria: `[[{report_name}]]`\n\n"
                    "Make sure the Egeria platform is running before retrying."
                    + detail
                )
            else:
                # Ran cleanly but returned no records — say so plainly.
                filt = "" if search_string in (None, "", "*") else f" matching *{search_string}*"
                err_response = (
                    f"**{report_name}** ran successfully but found no records{filt}.\n\n"
                    "Tips:\n"
                    "- Broaden or clear the search filter\n"
                    "- Confirm there is data of this type in the connected Egeria instance"
                )
            return {
                "query": query,
                "response": err_response,
                "query_type": "report",
                "report_name": report_name,
                "sources": [],
                "num_sources": 0,
                "retrieval_time": 0.0,
                "generation_time": 0.0,
                "avg_relevance_score": 0.0,
                "context_length": 0,
            }

        labeled_output = f"**Report: {report_name}**\n\n{output}"
        return {
            "query": query,
            "response": labeled_output,
            "query_type": "report",
            "report_name": report_name,
            "num_specs_found": num_specs_found,
            "sources": [f"pyegeria MCP → {report_name}"],
            "num_sources": 1,
            "retrieval_time": 0.0,
            "generation_time": 0.0,
            "avg_relevance_score": 0.0,
            "context_length": len(output),
        }


def _detail_to_masters() -> Dict[str, List[str]]:
    """Build reverse map: detail_spec_name → [master_spec_names] from the registry.
    Cached on first call; returns {} if the registry is unavailable."""
    cached = getattr(_detail_to_masters, "_cache", None)
    if cached is not None:
        return cached
    result: Dict[str, List[str]] = {}
    try:
        from pyegeria.view.base_report_formats import get_report_registry
        for name, spec in get_report_registry().items():
            for fmt in (getattr(spec, "formats", []) or []):
                for col in (getattr(fmt, "attributes", []) or []):
                    ds = getattr(col, "detail_spec", None)
                    if ds and name not in result.get(ds, []):
                        result.setdefault(ds, []).append(name)
    except Exception:
        pass
    _detail_to_masters._cache = result  # type: ignore[attr-defined]
    return result


def _no_report_found(query: str) -> Dict[str, Any]:
    return {
        "query": query,
        "response": (
            "I couldn't find a matching Egeria report for that query. "
            "You can ask me to *list available reports* to see what's available, "
            "or rephrase your request."
        ),
        "query_type": "report",
        "sources": [],
        "num_sources": 0,
        "retrieval_time": 0.0,
        "generation_time": 0.0,
        "avg_relevance_score": 0.0,
        "context_length": 0,
    }


# Singleton
_report_pipeline: Optional[ReportPipeline] = None


def get_report_pipeline() -> ReportPipeline:
    global _report_pipeline
    if _report_pipeline is None:
        _report_pipeline = ReportPipeline()
    return _report_pipeline
