"""Code intelligence agent — answers maintainer-focused queries about codebase structure, inheritance, methods, and stats.

AST-ownership-transfer plan Phase 6: reads Resource Explorer's
resource_explorer.project_code_symbols / project_code_relationships tables
(same Postgres instance, same egeria_advisor database, cross-schema SQL —
no new connection/infra needed) instead of EA's own code_symbols /
code_relationships. RE now owns extraction (real tree-sitter for Java,
extended stdlib ast for Python) for the repos in RE's "egeria" project group
— egeria_git, egeria_docs, egeria_python_git, egeria_workspaces_git — which
is exactly EA's old scripts/clone_repos.py target set.

Two structural changes from the EA-owned version this replaces:
  - Scoped by project_slug (RE's whole-repo unit), not EA's finer `collection`
    subdivision. EA's `pyegeria` collection (fine-grained: excludes tests/
    CLI/dr_egeria markdown processor within the egeria-python repo) is
    reconstructed as a path-prefix filter within one project_slug via
    _SCOPES, rather than being a separate physical collection/table.
  - _relative_path()'s "data/repos/<repo>/" marker-stripping is gone — RE's
    file_path is already relative to the repo root (RE clones into an
    ephemeral tempdir, discarded after ingestion; this agent never reads
    files from disk, only displays the path string, so there was never a
    functional need for an absolute path).
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable, Optional, Dict, List
from loguru import logger

from advisor.agents.base import BaseAdvisorAgent
from advisor.agents.tools import lookup_source, record_lookup, recording_retrieval, with_retrieval
from advisor.db_consolidated import get_db_manager
from advisor.re_code_scope import scope_clause as _scope_clause, SCOPES as _SCOPES, DEFAULT_PROJECT_SLUGS as _DEFAULT_PROJECT_SLUGS

_SYMBOLS_TABLE = "resource_explorer.project_code_symbols"
_RELATIONSHIPS_TABLE = "resource_explorer.project_code_relationships"


def _read(sql: str, params: tuple, sources: Optional[Callable[[List[Dict[str, Any]]], list]] = None) -> List[Dict[str, Any]]:
    """One symbol-store read, recorded with its time in the request's retrieval log (TR-51 d). `sources(rows)` says
    what the answer may cite from rows that came back; a read that found nothing records its time only."""
    db = get_db_manager()
    started = time.perf_counter()
    rows = db.execute_query(sql, params)
    record_lookup("symbol_store", time.perf_counter() - started, sources(rows) if (sources and rows) else ())
    return rows


def _definitions(rows: List[Dict[str, Any]]) -> list:
    """Each definition row is a source: its file, its name and signature."""
    return [lookup_source("symbol_store", r.get("file_path") or "", r.get("name") or "", r.get("signature") or "",
                          kind="definition", module=r.get("project_slug") or "") for r in rows]


def _table_read(table: str, what: str) -> Callable[[List[Dict[str, Any]]], list]:
    """An aggregate read (counts, lists, hierarchies) is one source: the table and what was asked of it."""
    return lambda rows: [lookup_source("symbol_store", table, what, f"{len(rows)} rows", kind="aggregate")]


def _resolve_symbol_name(words: List[str], kinds: tuple = ("class",)) -> str:
    """
    Resolve regex-split query words to an actual indexed symbol name of the given kind(s).

    Class names are stored CamelCase ("AutomatedCuration"); method/function names are
    snake_case ("create_glossary"). Users naturally type either with spaces
    ("Automated Curation", "create glossary"). Try the full no-separator concatenation
    (covers CamelCase) and the full underscore join (covers snake_case) first, then each
    individual word, checking existence case-insensitively against project_code_symbols.
    Falls back to the concatenation (as a readable label for a "not found" message) if
    nothing matches.
    """
    if not words:
        return ""
    concat = "".join(words)
    snake = "_".join(words)
    candidates = []
    for cand in (concat, snake, *words):
        if cand not in candidates:
            candidates.append(cand)
    kind_clause = " OR ".join(["kind = %s"] * len(kinds))
    for cand in candidates:
        rows = _read(f"SELECT name FROM {_SYMBOLS_TABLE} WHERE ({kind_clause}) AND name ILIKE %s LIMIT 1",
                     tuple(kinds) + (cand,))
        if rows:
            return rows[0]["name"]
    return concat


# Tools to be exposed to the LLM / direct executor
def get_class_for_method(method_name: str, collection: Optional[str] = None) -> List[Dict[str, Any]]:
    """Find a method or standalone function's definition: parent class (if any), file path, line number, signature, and docstring."""
    scope_sql, scope_params = _scope_clause(collection)
    sql = f"""
        SELECT name, parent_class, project_slug, file_path, start_line, signature, docstring
        FROM {_SYMBOLS_TABLE}
        WHERE kind IN ('method', 'function') AND name ILIKE %s AND {scope_sql}
        ORDER BY parent_class
    """
    return _read(sql, tuple([method_name] + scope_params), _definitions)

def get_class_info(class_name: str, collection: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get a class's own definition: its docstring, file path, line numbers, and signature."""
    scope_sql, scope_params = _scope_clause(collection)
    sql = f"""
        SELECT name, project_slug, file_path, start_line, end_line, signature, docstring, parent_class
        FROM {_SYMBOLS_TABLE}
        WHERE kind = 'class' AND name ILIKE %s AND {scope_sql}
        ORDER BY project_slug
    """
    return _read(sql, tuple([class_name] + scope_params), _definitions)

def check_inheritance(class_a: str, class_b: str, collection: Optional[str] = None) -> Dict[str, Any]:
    """Check if class_a inherits from class_b (directly or recursively), returning the path if found."""
    anchor_sql, anchor_params = _scope_clause(collection, table_alias="")
    sql = f"""
        WITH RECURSIVE inheritance_path AS (
            SELECT source_name, target_name, 1 AS depth, project_slug
            FROM {_RELATIONSHIPS_TABLE}
            WHERE relationship_type = 'inherits_from' AND source_name ILIKE %s AND {anchor_sql}

            UNION ALL

            SELECT r.source_name, r.target_name, ip.depth + 1, r.project_slug
            FROM {_RELATIONSHIPS_TABLE} r
            JOIN inheritance_path ip ON r.source_name = ip.target_name AND r.project_slug = ip.project_slug
            WHERE r.relationship_type = 'inherits_from' AND ip.depth < 10
        )
        SELECT source_name, target_name, depth, project_slug FROM inheritance_path WHERE target_name ILIKE %s
    """
    params = [class_a] + anchor_params + [class_b]
    rows = _read(sql, tuple(params), _table_read(_RELATIONSHIPS_TABLE, f"{class_a} inherits from {class_b}"))
    if rows:
        return {
            "inherits": True,
            "path": rows,
            "message": f"Yes, {class_a} inherits from {class_b}."
        }
    else:
        return {
            "inherits": False,
            "path": [],
            "message": f"No, {class_a} does not inherit from {class_b}."
        }

def list_classes(collection: Optional[str] = None) -> List[str]:
    """List all the class names defined in a specific collection (e.g. 'pyegeria' or 'egeria_java')."""
    scope_sql, scope_params = _scope_clause(collection)
    sql = f"""
        SELECT name, file_path
        FROM {_SYMBOLS_TABLE}
        WHERE kind = 'class' AND {scope_sql}
        ORDER BY name
    """
    rows = _read(sql, tuple(scope_params), _table_read(_SYMBOLS_TABLE, f"classes in {collection or 'all projects'}"))
    return [f"{r['name']} (in {r['file_path']})" for r in rows]

def get_class_hierarchy(class_name: str, collection: Optional[str] = None) -> Dict[str, Any]:
    """Get the ancestors (parents) and descendants (children) of a class in the codebase."""
    scope_sql, scope_params = _scope_clause(collection, table_alias="")

    anc_sql = f"""
        WITH RECURSIVE ancestors AS (
            SELECT source_name, target_name, 1 AS depth, project_slug
            FROM {_RELATIONSHIPS_TABLE}
            WHERE relationship_type = 'inherits_from' AND source_name ILIKE %s AND {scope_sql}
            UNION ALL
            SELECT r.source_name, r.target_name, a.depth + 1, r.project_slug
            FROM {_RELATIONSHIPS_TABLE} r
            JOIN ancestors a ON r.source_name = a.target_name AND r.project_slug = a.project_slug
            WHERE r.relationship_type = 'inherits_from' AND a.depth < 10
        )
        SELECT target_name AS class_name, depth, project_slug FROM ancestors
    """
    ancestors = _read(anc_sql, tuple([class_name] + scope_params),
                      _table_read(_RELATIONSHIPS_TABLE, f"ancestors of {class_name}"))

    dec_sql = f"""
        WITH RECURSIVE descendants AS (
            SELECT source_name, target_name, 1 AS depth, project_slug
            FROM {_RELATIONSHIPS_TABLE}
            WHERE relationship_type = 'inherits_from' AND target_name ILIKE %s AND {scope_sql}
            UNION ALL
            SELECT r.source_name, r.target_name, d.depth + 1, r.project_slug
            FROM {_RELATIONSHIPS_TABLE} r
            JOIN descendants d ON r.target_name = d.source_name AND r.project_slug = d.project_slug
            WHERE r.relationship_type = 'inherits_from' AND d.depth < 10
        )
        SELECT source_name AS class_name, depth, project_slug FROM descendants
    """
    descendants = _read(dec_sql, tuple([class_name] + scope_params),
                        _table_read(_RELATIONSHIPS_TABLE, f"descendants of {class_name}"))

    return {
        "class_name": class_name,
        "ancestors": ancestors,
        "descendants": descendants
    }

def get_codebase_stats(collection: Optional[str] = None) -> Dict[str, Any]:
    """Get statistics about the codebase (counts of classes, methods, functions, total lines of code)."""
    scope_sql, scope_params = _scope_clause(collection)
    sql = f"""
        SELECT kind, COUNT(*) AS count, SUM(end_line - start_line + 1) AS total_loc
        FROM {_SYMBOLS_TABLE}
        WHERE {scope_sql}
        GROUP BY kind
    """
    rows = _read(sql, tuple(scope_params), _table_read(_SYMBOLS_TABLE, f"codebase stats for {collection or 'all projects'}"))

    stats = {
        "classes": 0,
        "methods": 0,
        "functions": 0,
        "total_loc": 0
    }
    for r in rows:
        kind = r["kind"]
        count = int(r["count"])
        loc = int(r["total_loc"] or 0)
        if kind == "class":
            stats["classes"] = count
        elif kind == "method":
            stats["methods"] = count
        elif kind == "function":
            stats["functions"] = count
        stats["total_loc"] += loc

    return stats

def _format_class_info(info: List[Dict[str, Any]]) -> str:
    """Render get_class_info() results as clean text (real newlines, not a dict repr)."""
    blocks = []
    for row in info:
        blocks.append(
            f"Class: {row['name']} (project: {row['project_slug']})\n"
            f"File: {row['file_path']} (lines {row['start_line']}-{row['end_line']})\n"
            f"Signature: {row['signature']}\n"
            f"Docstring:\n{row['docstring'] or '(no docstring)'}"
        )
    return "\n\n".join(blocks)

def _format_method_info(rows: List[Dict[str, Any]]) -> str:
    """Render get_class_for_method() results as clean text (real newlines, not a dict repr)."""
    blocks = []
    for row in rows:
        parent = row.get("parent_class") or None
        header = f"Method: {row['name']} (in class {parent})" if parent else f"Function: {row['name']} (module-level, no parent class)"
        blocks.append(
            f"{header}\n"
            f"Project: {row['project_slug']}\n"
            f"File: {row['file_path']} (line {row['start_line']})\n"
            f"Signature: {row['signature']}\n"
            f"Docstring:\n{row['docstring'] or '(no docstring)'}"
        )
    return "\n\n".join(blocks)

def _format_class_hierarchy(hierarchy: Dict[str, Any]) -> str:
    """Render get_class_hierarchy() results as clean text (real newlines, not a dict repr)."""
    lines = [f"Class: {hierarchy['class_name']}"]
    if hierarchy["ancestors"]:
        lines.append("Ancestors (parents):")
        for a in sorted(hierarchy["ancestors"], key=lambda x: x["depth"]):
            lines.append(f"  - {a['class_name']} (depth {a['depth']}, project {a['project_slug']})")
    if hierarchy["descendants"]:
        lines.append("Descendants (children):")
        for d in sorted(hierarchy["descendants"], key=lambda x: x["depth"]):
            lines.append(f"  - {d['class_name']} (depth {d['depth']}, project {d['project_slug']})")
    return "\n".join(lines)

class CodeIntelAgent(BaseAdvisorAgent):
    def system_prompt(self) -> str:
        return (
            "You are an expert Egeria codebase advisor. You answer maintainer queries about the "
            "structural relationships, inheritance, method containment, class listings, and statistics of the codebase.\n\n"
            "Workflow:\n"
            "1. Identify the structural question being asked:\n"
            "   - If the user wants to know what a class is / does (a description or definition), call get_class_info.\n"
            "   - If the user wants to know what a method/function is/does, its signature, docstring, or where "
            "it is defined, call get_class_for_method.\n"
            "   - If the user wants to check if class A inherits from class B, call check_inheritance.\n"
            "   - If the user wants the hierarchy of a class (parents/children), call get_class_hierarchy.\n"
            "   - If the user wants to list all classes in a collection, call list_classes.\n"
            "   - If the user wants overall statistics or line counts, call get_codebase_stats.\n"
            "   - For a general 'what is X' about a class, call BOTH get_class_info and get_class_hierarchy "
            "and combine the docstring with the hierarchy in your answer.\n"
            "2. Use the database results to form a clear, direct, and factual answer.\n\n"
            "Rules:\n"
            "- Ground all your answers strictly in the tool outputs.\n"
            "- Provide the specific file paths and line numbers if returned by the tools.\n"
            "- If a query has no matching elements, state clearly that you could not find those symbols in the indexed codebase.\n\n"
            "CODEBASE ORGANIZATION REFERENCE:\n"
            "- **Egeria** (Java repository): The core Java implementation of the Egeria backend metadata platform, servers, OMAS, OMAG, and OMRS services (collection: 'egeria_java').\n"
            "- **egeria-python** (Python repository): The repository containing Python-based Egeria client code and tooling. It is organized into:\n"
            "  1. `pyegeria` (under `pyegeria/` directory): The Python client SDK API library used to communicate with Egeria backend servers (collection: 'pyegeria').\n"
            "  2. `commands` (under `commands/` directory): Command-line tools (CLI) and interactive commands (like `hey_egeria`) written in Python using the `pyegeria` SDK.\n"
            "  3. `dr_egeria` (under `md_processing/` directory): The Python implementation of the Dr. Egeria markdown processor which is used to parse, draft, and execute governance metadata template plans.\n\n"
            "This structural data is sourced from Resource Explorer's own codebase survey of these repositories, "
            "kept fresh by Resource Explorer on every repo refresh — not extracted separately by this agent."
        )

    def tools(self) -> list:
        return [get_class_info, get_class_for_method, check_inheritance, get_class_hierarchy, get_codebase_stats, list_classes]

    def handle(self, query: str) -> dict:
        """TR-51 d: the answer reports the symbol-store reads it was built from (sources, lookup time,
        retrieval_method), not hard-coded zeros; "none" only when nothing was read."""
        with recording_retrieval() as log:
            result = self._handle(query)
        return with_retrieval(result, log)

    def _handle(self, query: str) -> dict:
        logger.info(f"CodeIntelAgent handling query: {query}")

        from advisor.llm_client import get_ollama_client

        # Identify intent from query text
        q_lower = query.lower()
        context = ""

        try:
            if "stats" in q_lower or "how many" in q_lower or "statistics" in q_lower or "lines of code" in q_lower:
                stats = get_codebase_stats()
                context = f"Codebase Stats:\n{stats}"
            elif "list classes" in q_lower or "what classes" in q_lower or "classes in" in q_lower or "what are the classes" in q_lower:
                col = None
                if "python" in q_lower or "pyegeria" in q_lower:
                    col = "pyegeria"
                elif "java" in q_lower or ("egeria" in q_lower and "python" not in q_lower and "pyegeria" not in q_lower):
                    col = "egeria_java"

                if col is None:
                    response = (
                        "I can list classes for either the **Java Egeria backend** codebase (`egeria_java`) "
                        "or the **egeria-python** client SDK (`pyegeria`).\n\n"
                        "Please clarify which repository codebase you would like to inspect by specifying "
                        "**Java** or **Python** (e.g. 'list classes in python' or 'list classes in java')."
                    )
                    return {
                        "query": query,
                        "response": response,
                        "query_type": "code_intel",
                        "sources": [],
                        "num_sources": 0,
                        "retrieval_time": 0.0,
                        "generation_time": 0.0,
                        "avg_relevance_score": 1.0,
                        "context_length": len(response)
                    }

                classes_list = list_classes(col)
                collection_desc = "the 'egeria-python' (pyegeria) Python SDK codebase" if col == "pyegeria" else "the Egeria Java backend codebase"
                if len(classes_list) > 30:
                    truncated = classes_list[:30]
                    context = (
                        f"Found {len(classes_list)} classes defined in {collection_desc}.\n"
                        f"Due to the large number of classes, only the first 30 are listed below. "
                        f"Please inform the user of the total class count ({len(classes_list)}) "
                        f"and list some notable examples from this list:\n"
                        + "\n".join(f"- {c}" for c in truncated)
                    )
                else:
                    context = f"Found {len(classes_list)} classes defined in {collection_desc}:\n" + "\n".join(f"- {c}" for c in classes_list)
            elif "inherit" in q_lower or "extends" in q_lower:
                # Try to extract class names using simple regex
                words = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]*', query)
                # Filter out python keywords/common query words
                ignore = {"does", "inherit", "from", "class", "extends", "a", "b", "is", "an", "inherits", "subclass", "superclass", "parent"}
                classes = [w for w in words if w.lower() not in ignore]
                if len(classes) >= 2:
                    res = check_inheritance(classes[0], classes[1])
                    context = f"Inheritance Check ({classes[0]} -> {classes[1]}):\n{res}"
                else:
                    context = "Could not identify class names for inheritance check."
            elif "method" in q_lower or "function" in q_lower or "defined in" in q_lower or "what class is" in q_lower or "in which class" in q_lower:
                # Find method/function name — questions specifically about a method or
                # function's own definition (signature, docstring, containing class).
                words = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]*', query)
                ignore = {
                    "what", "class", "is", "method", "defined", "in", "function",
                    "defined_in", "where", "find", "locate", "does", "do", "the",
                    "a", "an", "tell", "me", "about", "describe", "explain"
                }
                methods = [w for w in words if w.lower() not in ignore]
                if methods:
                    method_name = _resolve_symbol_name(methods, ("method", "function"))
                    res = get_class_for_method(method_name)
                    if res:
                        context = _format_method_info(res)
                    else:
                        context = f"No method or function named '{method_name}' found in the indexed codebase."
                else:
                    context = "Could not identify method name for definition lookup."
            else:
                # Default: bare-name questions ("what is X", "describe X", "tell me
                # about X", "class hierarchy for X"). X could be a class or a
                # method/function — try class first (plus its hierarchy), and fall
                # back to a method/function lookup if no class matches.
                words = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]*', query)
                ignore = {
                    "hierarchy", "parents", "children", "ancestors", "descendants",
                    "what", "is", "the", "for", "class", "show", "does", "do", "tell",
                    "me", "about", "describe", "explain"
                }
                names = [w for w in words if w.lower() not in ignore]
                if names:
                    class_name = _resolve_symbol_name(names, ("class",))
                    info = get_class_info(class_name)
                    if info:
                        parts = [_format_class_info(info)]
                        hierarchy = get_class_hierarchy(class_name)
                        if hierarchy["ancestors"] or hierarchy["descendants"]:
                            parts.append(_format_class_hierarchy(hierarchy))
                        context = "\n\n".join(parts)
                    else:
                        method_name = _resolve_symbol_name(names, ("method", "function"))
                        method_info = get_class_for_method(method_name)
                        if method_info:
                            context = _format_method_info(method_info)
                        else:
                            context = f"No class, method, or function named '{class_name}' found in the indexed codebase."
                else:
                    context = "Could not identify a class or method/function name for lookup."
        except Exception as e:
            logger.warning(f"CodeIntelAgent direct tool execution failed: {e}")
            context = f"Error querying codebase relationships: {e}"

        system = (
            "You are an expert Egeria codebase advisor. Answer the structural codebase question based ONLY "
            "on the provided query results. If the results are empty or do not contain the answer, say so "
            "explicitly — do not invent information.\n"
            "CRITICAL: Do NOT output or guess external GitHub repository URLs, website links, or directory paths "
            "unless they are explicitly present in the provided query results. Be very clear and output specific "
            "file paths and line numbers.\n"
            "When a 'signature' field is present in the query results, always include it verbatim (e.g. in a "
            "code block). When a 'docstring' field is present, quote it in full — do not paraphrase or "
            "summarize it down to one sentence; the user wants the actual documented description, not a gloss."
        )

        prompt = (
            f"Query Results:\n{context}\n\n"
            f"Question: {query}\n\n"
            "Provide a maintainer-oriented answer. Include the full signature and full docstring text from the "
            "query results verbatim where present, plus file path and line numbers."
        )

        try:
            response = get_ollama_client().generate(prompt, system=system, max_tokens=1500)
        except Exception as exc:
            response = f"Unable to generate response: {exc}"

        return {
            "query": query,
            "response": response,
            "query_type": "code_intel",
            "sources": [],
            "num_sources": 0,
            "retrieval_time": 0.0,
            "generation_time": 0.0,
            "avg_relevance_score": 1.0,
            "context_length": len(response)
        }

_agent: CodeIntelAgent | None = None

def get_code_intel_agent() -> CodeIntelAgent:
    global _agent
    if _agent is None:
        _agent = CodeIntelAgent()
    return _agent
