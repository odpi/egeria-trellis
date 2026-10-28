"""The classes, methods and functions a repository defines: one typed reader
over `project_code_symbols`, plus the deterministic question matcher.

Same shape of gap as endpoints.py: `api_structure` stored symbol COUNTS and a
top-ten of names, so "list the methods of TestRunner" or "what classes are in
testgen/commands" had no evidence to answer from. The symbols are in the
registry with line, signature and docstring; nothing read them for the model.

Test files and vendored code are left out and counted (`file_role`, falling
back to `is_test_path` for rows extracted before the column). Public by
default: `is_private` is inferred from naming, so the text says "public".

Question matching is deterministic and uses no model: tokens of the question
are compared, case-insensitively, with class names, function/method names
and module paths in this repository's stored symbols.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from resource_explorer.ingestion.test_paths import is_test_path

log = logging.getLogger(__name__)

TYPE_KINDS = ("class", "interface", "enum")
_EXCLUDED_ROLES = frozenset({"test", "vendored"})


@dataclass
class Symbol:
    kind: str
    qualified_name: str
    name: str
    file_path: str
    start_line: int
    signature: str = ""
    doc: str = ""
    parent_class: str = ""
    is_private: bool = False

    @property
    def location(self) -> str:
        return f"{self.file_path}:{self.start_line}" if self.start_line else self.file_path

    def to_dict(self) -> dict:
        return {"kind": self.kind, "qualified_name": self.qualified_name, "name": self.name,
                "file_path": self.file_path, "start_line": self.start_line, "signature": self.signature,
                "doc": self.doc, "parent_class": self.parent_class, "is_private": self.is_private}


@dataclass
class SymbolSet:
    measured: bool
    rows: list = field(default_factory=list)        # [Symbol], file then line
    excluded_tests: int = 0
    excluded_vendored: int = 0
    private_left_out: int = 0

    @property
    def total(self) -> int:
        return len(self.rows)


def _first_line(doc: str | None) -> str:
    for line in (doc or "").strip().splitlines():
        if line.strip():
            return line.strip()[:160]
    return ""


def read_symbols(registry, slug: str, *, kinds: tuple[str, ...] | None = None, path_prefix: str = "",
                 parent_class: str = "", name_terms: tuple[str, ...] = (),
                 include_private: bool = False) -> SymbolSet | None:
    """Non-test symbols of `slug`, filtered. None when the registry cannot be
    read (a reader that fails is not evidence of absence)."""
    sql = ("SELECT kind, qualified_name, name, file_path, start_line, signature, docstring, parent_class, "
           "COALESCE(is_private, 0) AS is_private, file_role FROM project_code_symbols WHERE project_slug = ?")
    try:
        norm = registry._normalize_slug(slug)
        with registry._conn() as conn:
            raw = [dict(r) for r in conn.execute(sql, (norm,)).fetchall()]
    except Exception:
        log.warning("symbol reader failed on %s", slug, exc_info=True)
        return None
    prefix = path_prefix.strip().strip("/").lower()
    terms = tuple(t.lower() for t in name_terms if t)
    rows: list[Symbol] = []
    tests = vendored = private = 0
    for r in raw:
        path = r.get("file_path") or ""
        role = r.get("file_role")
        if role == "test" or (role is None and is_test_path(path)):
            tests += 1
            continue
        if role in _EXCLUDED_ROLES:
            vendored += 1
            continue
        if kinds and (r.get("kind") or "") not in kinds:
            continue
        if prefix and prefix not in path.lower():
            continue
        if parent_class and (r.get("parent_class") or "") != parent_class:
            continue
        if terms and not any(t in (r.get("name") or "").lower() or t in (r.get("qualified_name") or "").lower()
                             for t in terms):
            continue
        if r.get("is_private") and not include_private:
            private += 1
            continue
        rows.append(Symbol(
            kind=r.get("kind") or "", qualified_name=r.get("qualified_name") or "", name=r.get("name") or "",
            file_path=path, start_line=int(r.get("start_line") or 0),
            signature=(r.get("signature") or "").strip(), doc=_first_line(r.get("docstring")),
            parent_class=r.get("parent_class") or "", is_private=bool(r.get("is_private"))))
    rows.sort(key=lambda s: (s.file_path, s.start_line, s.qualified_name))
    return SymbolSet(measured=bool(raw), rows=rows, excluded_tests=tests, excluded_vendored=vendored,
                     private_left_out=private)


# ── question matching ──────────────────────────────────────────────────────

_KIND_WORDS = re.compile(r"\b(class(?:es)?|methods?|functions?|modules?|packages?|symbols?|defin\w+)\b", re.I)
_STOP = frozenset("""the and for are with that this what which list show give all any from into about does
did how many each every have has not you your our their its them these those there where when who whom whose
please tell describe explain class classes method methods function functions module modules package packages
symbol symbols file files code repo repository public private defined define definition inside under within
are can could would should will used uses using use get set run runs""".split())
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:[./\\-][A-Za-z0-9_]+)*")


def question_tokens(question: str) -> list[str]:
    seen, out = set(), []
    for t in _TOKEN.findall(question or ""):
        t = t.strip("./-\\")
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def _identifier_shaped(t: str) -> bool:
    return "_" in t or bool(re.search(r"[a-z][A-Z]", t)) or bool(re.match(r"^[A-Z][a-z]+[A-Z]", t))


def _path_shaped(t: str) -> bool:
    return "/" in t or "\\" in t or bool(re.search(r"\.(py|js|ts|java|go|kt|scala)$", t))


@dataclass
class QuestionMatch:
    asked: bool                                    # did the question name (or ask about) code things at all
    classes: list = field(default_factory=list)    # [Symbol] matched classes
    callables: list = field(default_factory=list)  # [Symbol] matched functions/methods
    modules: list = field(default_factory=list)    # [str] matched path prefixes
    tokens: list = field(default_factory=list)


def match_question(question: str, symbols: list[Symbol]) -> QuestionMatch:
    """Which stored symbols and modules the question names. Deterministic:
    a class matches on its simple or qualified name (any token of 3+
    characters that is not a stop word), a function or method only on an
    identifier-shaped token (snake_case / camelCase) so the word "token"
    does not match every `token`; a module on a path-shaped token that is
    part of at least one stored file path."""
    toks = question_tokens(question)
    asked = bool(_KIND_WORDS.search(question or "")) or any(
        _identifier_shaped(t) or _path_shaped(t) for t in toks)
    qm = QuestionMatch(asked=asked, tokens=toks)
    by_class: dict[str, list[Symbol]] = {}
    by_call: dict[str, list[Symbol]] = {}
    for s in symbols:
        if s.kind in TYPE_KINDS:
            by_class.setdefault(s.name.lower(), []).append(s)
        else:
            by_call.setdefault(s.name.lower(), []).append(s)
    paths = {s.file_path.lower() for s in symbols}
    for t in toks:
        low = t.lower()
        if _path_shaped(t):
            norm = low.replace("\\", "/").strip("/")
            if any(norm in p for p in paths):
                qm.modules.append(norm)
            continue
        if len(t) < 3 or low in _STOP:
            continue
        if low in by_class:
            qm.classes.extend(by_class[low])
        elif _identifier_shaped(t) and low in by_call:
            qm.callables.extend(by_call[low])
    return qm


# ── rendering ──────────────────────────────────────────────────────────────

def _sym_line(s: Symbol, *, show_kind: bool = False) -> str:
    kind = f"{s.kind} " if show_kind else ""
    sig = s.signature if s.signature and s.signature not in s.name else ""
    doc = f" - {s.doc}" if s.doc else ""
    return f"{kind}{s.qualified_name}{sig} ({s.location}){doc}"


def _left_out(ss: SymbolSet) -> str:
    bits = []
    if ss.excluded_tests:
        bits.append(f"{ss.excluded_tests:,} under test paths")
    if ss.excluded_vendored:
        bits.append(f"{ss.excluded_vendored:,} vendored")
    if ss.private_left_out:
        bits.append(f"{ss.private_left_out:,} private (inferred from naming)")
    return f" · left out: {', '.join(bits)}" if bits else ""


def list_line(label: str, rows: list, limit: int, tail: str = "") -> list[str]:
    n = len(rows)
    if n == 0:
        return [f"- {label}: 0 found{tail}"]
    head = (f"- {label}: {n} found, {min(n, limit)} of {n} shown to the model{tail}" if n > limit
            else f"- {label}: {n} found, all shown{tail}")
    return [head] + [f"  - {_sym_line(s)}" for s in rows[:limit]]


MATCH_CLASSES = 5
MATCH_METHODS = 40
MATCH_MODULE_SYMBOLS = 60


def render_matched(question: str, ss: SymbolSet, universe: list[Symbol]) -> list[str]:
    """The question-matched block, first in the section. `universe` is the
    public non-test symbol set to match against; methods of a matched class
    come from it too."""
    qm = match_question(question, universe)
    if not qm.asked:
        return []
    lines: list[str] = []
    if qm.classes:
        for c in qm.classes[:MATCH_CLASSES]:
            methods = [s for s in universe if s.parent_class == c.name and s.file_path == c.file_path
                       and s.kind not in TYPE_KINDS]
            lines.append(f"- question-matched class {_sym_line(c)}")
            lines += (list_line("methods", methods, MATCH_METHODS) if methods else ["  - methods: none found (public)"])
        if len(qm.classes) > MATCH_CLASSES:
            lines.append(f"- question-matched: {len(qm.classes)} classes share the names asked about, "
                         f"{MATCH_CLASSES} shown")
    if qm.callables:
        lines.append(f"- question-matched functions/methods: {len(qm.callables)} found")
        lines += [f"  - {_sym_line(s, show_kind=True)}" for s in qm.callables[:MATCH_METHODS]]
    for m in qm.modules[:2]:
        under = [s for s in universe if m in s.file_path.lower()]
        classes = [s for s in under if s.kind in TYPE_KINDS]
        funcs = [s for s in under if s.kind not in TYPE_KINDS and not s.parent_class]
        lines.append(f"- question-matched module {m}: {len(under)} public symbols "
                     f"({len(classes)} classes, {len(funcs)} top-level functions)")
        shown = (classes + funcs)[:MATCH_MODULE_SYMBOLS]
        lines += [f"  - {_sym_line(s, show_kind=True)}" for s in shown]
        if len(classes) + len(funcs) > MATCH_MODULE_SYMBOLS:
            lines.append(f"  - ... {MATCH_MODULE_SYMBOLS} of {len(classes) + len(funcs)} classes and functions "
                         "shown to the model")
    if not lines:
        lines = ["- question-matched: no class, function or module in this repository's stored symbols "
                 "matches a name in the question"]
    return lines


def left_out_tail(ss: SymbolSet) -> str:
    return _left_out(ss)
