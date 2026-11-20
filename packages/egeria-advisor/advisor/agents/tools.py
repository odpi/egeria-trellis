"""BeeAI @tool functions shared across Egeria Advisor agents."""
from __future__ import annotations
import contextlib
import contextvars
import csv
import re
from functools import lru_cache
from pathlib import Path
import time
from typing import Optional

from beeai_framework.tools import tool
from loguru import logger


@lru_cache(maxsize=1)
def _load_perspective_families() -> dict[str, list[str]]:
    """Load perspective→template-family mappings from config/perspective_template_families.csv.

    Returns a dict keyed by lowercase role profile (e.g. 'developer', 'any'; see A-P P-1)
    with values being dicts of {normalised_family: boost} where boost is 2 for level='both'
    and 1 for level='basic'.  Comment lines (starting with #) are skipped.
    """
    csv_path = Path(__file__).parent.parent / "configdata" / "perspective_template_families.csv"
    result: dict[str, dict[str, int]] = {}
    # Comment rows ("# ...") are skipped before any other field is read: csv gives them None for
    # the missing columns, and a None.strip() here used to raise into a bare `except: pass`, so this
    # returned {} and no perspective's template families ever applied (found by A-P P-1).
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                perspective = (row.get("perspective") or "").strip()
                if not perspective or perspective.startswith("#"):
                    continue
                family = (row.get("family") or "").strip()
                level = (row.get("level") or "basic").strip().lower()
                if not family:
                    continue
                boost = 2 if level == "both" else 1
                result.setdefault(perspective.lower(), {})[family.lower()] = boost
    except OSError as exc:
        logger.warning(f"Template families unavailable: could not read {csv_path.name}: {exc}")
        return {}
    return result


def _priority_families(perspective: str | None) -> dict[str, int]:
    """Normalised template family -> boost (+2 for level both, +1 for basic) for a perspective.

    The families CSV is keyed by ROLE PROFILE (A-P P-1), so the perspective's role_profile is looked
    up; a "general" perspective gets only the "any" rows. No perspective: no boosts.
    """
    priority: dict[str, int] = {}
    if not perspective:
        return priority
    from advisor.perspective_manager import role_profile
    mappings = _load_perspective_families()
    for key in (role_profile(perspective) or "", "any"):
        for fam, boost in mappings.get(key, {}).items():
            norm = _normalise(fam)
            # Take the higher boost if the same family appears in both "any" and the profile
            priority[norm] = max(priority.get(norm, 0), boost)
    return priority


@tool(description=(
    "Search indexed Egeria content for text relevant to the query. "
    "collections is a comma-separated list of collection names from: "
    "pyegeria, pyegeria_cli, pyegeria_drE, egeria_concepts, egeria_general, egeria_types, "
    "egeria_workspaces, egeria_java, egeria_templates. "
    "Call multiple times with different queries or collections to broaden coverage. "
    "Returns the most relevant chunks with their source file and score."
))
def search_egeria_content(query: str, collections: str) -> str:
    from advisor.multi_collection_store import get_multi_collection_store
    names = [c.strip() for c in collections.split(",") if c.strip()]
    if not names:
        return "Error: no collection names provided."
    store = get_multi_collection_store()
    result = store.search_specific_collections(query=query, collection_names=names, top_k=6)
    if not result.results:
        return "No relevant content found."
    parts = []
    for r in result.results:
        col = r.metadata.get("_collection", r.metadata.get("collection", "?"))
        fp = r.metadata.get("file_path", r.metadata.get("source", ""))
        parts.append(f"[{col} | {fp} | score={r.score:.2f}]\n{r.text}")
    return "\n\n---\n\n".join(parts)


# ── What an answer retrieved (TR-51 c) ──────────────────────────────────────
# An agent's answer built from these searches must report them: its sources, how many, how long they took. The
# agents used to return hard-coded "sources": [], "num_sources": 0, "retrieval_time": 0.0 (absence-as-answer).
# Each search records into the request's log; the agent's handle() reads it back (with_retrieval).
_RETRIEVAL: "contextvars.ContextVar[Optional[dict]]" = contextvars.ContextVar("ea_agent_retrieval", default=None)


@contextlib.contextmanager
def recording_retrieval():
    """`with recording_retrieval() as log:` -- every _search_egeria_content_raw and record_lookup inside records its
    hits and time."""
    log = {"sources": [], "seconds": 0.0, "searches": 0, "methods": []}
    token = _RETRIEVAL.set(log)
    try:
        yield log
    finally:
        _RETRIEVAL.reset(token)


def _source_of(r) -> dict:
    md = getattr(r, "metadata", None) or {}
    col = md.get("_collection") or md.get("collection", "N/A")
    return {"text": getattr(r, "text", ""), "score": getattr(r, "score", 0.0),
            "file_path": md.get("file_path") or md.get("source", "unknown"), "name": md.get("name", "unnamed"),
            "type": md.get("type", "unknown"), "module": md.get("module", ""), "collection": col, "_collection": col}


def _note(log: dict, method: str, seconds: float, sources) -> None:
    log["seconds"] += seconds
    log["searches"] += 1
    if method not in log["methods"]:
        log["methods"].append(method)
    log["sources"].extend(sources)


# TR-51 (d): the symbol store (RE's project_code_symbols / project_code_relationships), the cached metrics file and
# the Dr.Egeria command index are read too. Each read records here, with its time, as vector searches do. A read
# that returned something is a source; one that found nothing still counts its time (the store was asked).
def lookup_source(collection: str, file_path: str, name: str, text: str = "", kind: str = "symbol",
                  module: str = "") -> dict:
    """A source from an exact lookup. It carries no score: a lookup has no relevance score, and consumers default a
    missing one (none is invented here)."""
    return {"text": text, "file_path": file_path, "name": name, "type": kind, "module": module,
            "collection": collection, "_collection": collection}


def record_lookup(method: str, seconds: float, sources=()) -> None:
    """Record one read by `method` ("symbol_store", "analytics_cache", "command_keyword_index") into the current
    request's log. Outside recording_retrieval() it does nothing."""
    log = _RETRIEVAL.get()
    if log is not None:
        _note(log, method, seconds, list(sources))


def with_retrieval(result: dict, log: Optional[dict]) -> dict:
    """`result` reporting what was retrieved for it: the distinct sources, their number, the search time and their
    mean score, and retrieval_method: the stores read, or "none" when nothing was read. A result that reports its own
    sources keeps them; with nothing retrieved, the zeros are true. With no log, nothing is claimed."""
    if not log or result.get("num_sources"):
        return result
    method = ", ".join(log.get("methods") or []) or "none"
    seen, sources = set(), []
    for src in log["sources"]:
        key = (src["collection"], src["file_path"], src["text"][:200])
        if key not in seen:
            seen.add(key)
            sources.append(src)
    scores = [s["score"] for s in sources if isinstance(s.get("score"), (int, float))]
    return {**result, "sources": sources, "num_sources": len(sources), "retrieval_method": method,
            "retrieval_time": log["seconds"] if log["searches"] else result.get("retrieval_time", 0.0),
            "avg_relevance_score": (sum(scores) / len(scores)) if scores else result.get("avg_relevance_score", 0.0)}


def _search_egeria_content_raw(query: str, collections: list[str], top_k: int = 8) -> str:
    """Direct call to the search logic without going through the BeeAI tool wrapper."""
    from advisor.multi_collection_store import get_multi_collection_store
    if not collections:
        return "No collections specified."
    store = get_multi_collection_store()
    started = time.perf_counter()
    result = store.search_specific_collections(query=query, collection_names=collections, top_k=top_k)
    log = _RETRIEVAL.get()
    if log is not None:
        _note(log, "vector_search", time.perf_counter() - started, [_source_of(r) for r in (result.results or [])])
    if not result.results:
        return "No relevant content found."
    parts = []
    for r in result.results:
        col = r.metadata.get("_collection", r.metadata.get("collection", "?"))
        fp = r.metadata.get("file_path", r.metadata.get("source", ""))
        parts.append(f"[{col} | {fp} | score={r.score:.2f}]\n{r.text}")
    return "\n\n---\n\n".join(parts)


@tool(description=(
    "Look up detailed information about a specific pyegeria class, method, or function by name. "
    "Useful when you know the exact symbol name (e.g. 'ProjectManager', 'create_project', "
    "'get_glossary_terms'). Returns the class/function signature, docstring, and source location."
))
def get_egeria_symbol(name: str) -> str:
    return _get_egeria_symbol_raw(name)


def _get_egeria_symbol_raw(name: str) -> str:
    """Direct call to symbol lookup without the BeeAI tool wrapper.

    Reads via ReCodeSymbolReader (resource_explorer.project_code_symbols),
    not the deprecated CodeSymbolStore (code_symbols/code_relationships) —
    nothing has written to those tables since the AST-ownership-transfer
    migration (Phase 8), so the old code path always missed here and fell
    straight through to the vector-search fallback below, silently, for
    every call. analytics.py/re_code_scope.py already made this same switch;
    this was the one remaining call site still on the old store."""
    from advisor.re_code_symbol_reader import get_re_code_symbol_reader
    store = get_re_code_symbol_reader()

    # Try exact match in PostgreSQL first (filter to pyegeria Python SDK symbols)
    symbols = store.search_symbols(name_pattern=name, collection="pyegeria", limit=5)
    
    # Filter for exact name match if possible
    exact_matches = [s for s in symbols if s["name"].lower() == name.lower()]
    targets = exact_matches if exact_matches else symbols
    
    if targets:
        parts = []
        for s in targets:
            desc = f"Name: {s['name']}\nType: {s['kind']}"
            if s['parent_class']:
                desc += f"\nParent Class: {s['parent_class']}"
            if s['signature']:
                desc += f"\nSignature: {s['signature']}"
            if s['docstring']:
                desc += f"\nDocumentation: {s['docstring']}"
            
            # If it's a class, also fetch its methods from DB
            if s['kind'] == 'class':
                methods = store.methods_for_class(s['name'])
                if methods:
                    desc += "\n\nExposed Methods:"
                    for m in methods:
                        m_sig = m['signature'] or "()"
                        desc += f"\n  - {m['name']}{m_sig}"
                        if m['docstring']:
                            desc += f"  # {m['docstring'].strip()}"
                            
            parts.append(f"[{s['collection']} | {s['qualified_name']} ({s['kind']})]\n{desc}")
        return "\n\n---\n\n".join(parts)

    from advisor.multi_collection_store import get_multi_collection_store
    vec_store = get_multi_collection_store()
    # Search pyegeria collection with name as query, filtered to high precision
    result = vec_store.search_specific_collections(
        query=name,
        collection_names=["pyegeria"],
        top_k=5,
    )
    if not result.results:
        return f"No symbol found named '{name}'."
    hits = [r for r in result.results if name.lower() in r.text.lower()]
    targets = hits[:3] if hits else result.results[:3]
    parts = []
    for r in targets:
        fp = r.metadata.get("file_path", r.metadata.get("source", ""))
        parts.append(f"[{fp} | score={r.score:.2f}]\n{r.text}")
    return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# Dr.Egeria markdown command template lookup
# ---------------------------------------------------------------------------

def _templates_root() -> Path | None:
    """
    Return the Dr.Egeria templates root directory (holding basic/ and
    advanced/), or None if not found.

    The installed pyegeria's packaged templates come first (BRIEF
    EA-CATCHUP-AND-DASHBOARDS-2026-11-10 B2): they match the dispatcher that
    executes the commands. Only when that pyegeria predates
    ``md_processing.templates`` does this fall back, with a logged note, to
    ``_legacy_templates_root()``.
    """
    from advisor.dre_templates import installed_templates_root, note_fallback

    root = installed_templates_root()
    if root is not None:
        return root
    legacy = _legacy_templates_root()
    note_fallback("agents.tools._templates_root", legacy)
    return legacy


def _legacy_templates_root() -> Path | None:
    """
    Pre-B2 template discovery; used only when the installed pyegeria has no
    packaged templates. Remove once trellis' pyegeria floor carries them.

    Candidate paths tried in order:
      1. {pyegeria_root}/Templates/Dr-Egeria-Templates   (case-sensitive, workspace layout)
      2. {pyegeria_root}/templates                       (lower-case fallback)
      3. {EGERIA_ROOT_PATH|PYEGERIA_ROOT_PATH}/Templates/Dr-Egeria-Templates
      4. {EGERIA_ROOT_PATH|PYEGERIA_ROOT_PATH}/templates
      5. {repo_root}/config/dr-egeria-templates          (repo-level copy, basic/advanced layout)
    """
    import os

    def _try(root: str | Path) -> Path | None:
        root = Path(root)
        for sub in ("Templates/Dr-Egeria-Templates", "templates"):
            p = root / sub
            if p.is_dir():
                return p
        return None

    try:
        from pyegeria.core.config import get_app_config
        root = get_app_config().Environment.pyegeria_root
        if root:
            found = _try(root)
            if found:
                return found
    except Exception:
        pass

    env_root = os.getenv("EGERIA_ROOT_PATH") or os.getenv("PYEGERIA_ROOT_PATH")
    if env_root:
        found = _try(env_root)
        if found:
            return found

    # Repo-level copy: <repo>/config/dr-egeria-templates (basic/ + advanced/).
    # Moved 2026-09-06 out of packages/egeria-advisor/examples/templates —
    # these are a runtime input to the command keyword index, not an example
    # library, and the sync from egeria-python now targets the repo-level path.
    project_root = Path(__file__).parent.parent.parent
    local = project_root.parent.parent / "config" / "dr-egeria-templates"
    if local.is_dir():
        return local

    return None


def _normalise(s: str) -> str:
    """Lower-case, strip punctuation/spaces for fuzzy comparison."""
    return re.sub(r"[\s_\-]+", "", s).lower()


def _find_dre_template_raw(query: str, level: str = "basic", perspective: str | None = None) -> str:
    """
    Search the Dr.Egeria template files for commands matching *query*.

    Templates live at {EGERIA_ROOT_PATH}/templates/{level}/{family}/{command}.md.
    When *perspective* is provided the CSV mapping in config/perspective_template_families.csv
    boosts families that are relevant to that role (+1 score), ensuring perspective-appropriate
    templates surface first.  Falls back gracefully if the CSV is missing.
    Returns up to 3 matching template bodies concatenated, or a "not found" message.
    """
    root = _templates_root()
    if root is None:
        return (
            "Dr.Egeria template directory not found. "
            "Run `generate_md_cmd_templates.py` to generate templates, "
            "or set EGERIA_ROOT_PATH / PYEGERIA_ROOT_PATH to the pyegeria workspace root."
        )

    level_dir = root / level
    if not level_dir.is_dir():
        level_dir = root / "basic"
    if not level_dir.is_dir():
        return f"No templates found at {root}."

    query_norm = _normalise(query)

    priority_families = _priority_families(perspective)

    # Score every template file.
    # Tier 4: exact substring match between full normalised query and stem (or vice-versa).
    # Tier 3: ALL meaningful query words (len > 3) appear in the stem.
    # Tier 2: SOME meaningful query words appear in the stem (count = score within tier).
    # Tier 1: meaningful query words appear in the family name only.
    # Perspective-relevant families get an extra +1 so they surface first within a tier.
    words = [_normalise(w) for w in query.split() if len(w) > 3]
    scored: list[tuple[int, Path]] = []
    for md_file in sorted(level_dir.rglob("*.md")):
        stem_norm = _normalise(md_file.stem)
        family_norm = _normalise(md_file.parent.name)
        score = 0
        if query_norm in stem_norm or stem_norm in query_norm:
            score = 40  # tier 4: exact
        elif words:
            stem_hits = sum(1 for w in words if w in stem_norm)
            if stem_hits == len(words):
                score = 30  # tier 3: all words match stem
            elif stem_hits > 0:
                score = 20 + stem_hits  # tier 2: partial stem match; more hits = higher
            elif any(w in family_norm for w in words):
                score = 10  # tier 1: family match only
        if score > 0:
            score += priority_families.get(family_norm, 0)
            scored.append((score, md_file))

    if not scored:
        # List available families to help the user
        families = sorted({p.parent.name for p in level_dir.rglob("*.md")})
        return (
            f"No Dr.Egeria template found matching '{query}' at {level} level.\n\n"
            f"Available families: {', '.join(families)}\n\n"
            "Try a more specific term (e.g. 'create glossary', 'create term', 'link term')."
        )

    scored.sort(key=lambda x: (-x[0], x[1].stem))
    top = scored[:3]

    parts = []
    for _, md_file in top:
        family = md_file.parent.name
        try:
            content = md_file.read_text(encoding="utf-8")
            parts.append(f"**Family: {family} | Template: {md_file.stem}**\n\n{content}")
        except Exception as exc:
            parts.append(f"[Could not read {md_file}: {exc}]")

    return "\n\n---\n\n".join(parts)


@tool(description=(
    "Find and return Dr.Egeria markdown command samples/examples/templates matching the user's topic. "
    "Use this when the user asks for a Dr.Egeria example, sample, or command — even if they say "
    "'show me how' or 'give me an example' in a Dr.Egeria context. "
    "Templates cover Create, Update, Link, Set, and View operations for Egeria metadata objects "
    "(glossaries, terms, collections, governance definitions, projects, people, reports, etc.). "
    "level should be 'basic' (most users) or 'advanced' (full attribute set). "
    "Returns the raw markdown the user can copy into a Dr.Egeria notebook or file."
))
def find_dre_template(query: str, level: str = "basic") -> str:
    return _find_dre_template_raw(query, level=level)
