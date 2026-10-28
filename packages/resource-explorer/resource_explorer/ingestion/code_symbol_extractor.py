"""Extract class/function/method symbols from source files for the code intelligence index."""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field


@dataclass
class CodeSymbol:
    resource_slug: str
    file_path: str
    language: str
    kind: str            # class | function | method | interface | enum
    name: str            # simple name: "parse"
    qualified_name: str  # Class.method or bare name: "CodeParser.parse"
    signature: str       # typed param list: "(self, x: int) -> str"
    docstring: str       # first line of docstring; "" if none
    start_line: int
    end_line: int
    # Added for AST-ownership-transfer plan Phase 1 — fields Egeria Advisor's
    # code_symbols schema has and RE's didn't, needed for CodeIntelAgent-style
    # queries (inheritance, hierarchy, complexity). Defaulted so JS/Go/interface
    # symbols (not upgraded in this pass) don't need every extractor call site
    # touched.
    parent_class: str = ""      # immediate enclosing class name, for methods and nested classes
    return_type: str = ""       # function/method return type annotation, if any
    is_private: bool = False    # leading underscore, not dunder
    is_async: bool = False
    complexity: int = 0         # cyclomatic complexity; 0 for non-function symbols (classes/interfaces)
    bases: list[str] = field(default_factory=list)  # class-kind only: raw base-class expressions, not FQN-resolved
    #: core | test | generated | vendored | docs (ingestion/file_role.py), stamped by
    #: CodeSymbolExtractor.extract. None = not classified, stored as NULL ("not recorded").
    file_role: str | None = None


@dataclass
class CodeMarker:
    """A registration fact about a symbol — DESIGN-INTERFACE-SURFACE-
    IMPLEMENTED-RUNG.md §3.1. Not a symbol itself: it is a fact ABOUT one
    (which `qualified_name` joins back to in project_code_symbols), and one
    symbol can carry several registrations (`@app.get(...)` and
    `@app.post(...)` on the same handler is ordinary FastAPI) — the reason
    this is a separate table/dataclass rather than columns bolted onto
    CodeSymbol."""

    resource_slug: str
    file_path: str
    start_line: int
    language: str
    marker_kind: str      # route | rpc_method | resolver | message_handler | soap_operation
    framework: str         # fastapi, flask, django, django-rest-framework, celery,
                            # strawberry, graphene, grpc, spring-mvc, kafka, …
    interface_kind: str    # http_api | grpc | graphql | messaging | soap
    detail: str             # "GET /assets/{guid}", topic name, etc. — '' if none
    qualified_name: str     # the decorated/annotated symbol, or '' when there is none
                            # (e.g. a Django urls.py path() call)
    file_role: str | None = None  # as CodeSymbol.file_role; stamped by extract_markers


#: Languages whose marker extraction is real — Python via `ast.decorator_list`
#: (exact, free) and Java via the existing tree-sitter grammar's annotation
#: nodes (`@GetMapping`, `@KafkaListener`, …). JS/TS and Go have no decorator
#: equivalent for their common frameworks (Express registers routes by method
#: call, Go by `mux.HandleFunc`) and would need call-site capture, which is a
#: larger change than this pass — see DESIGN-INTERFACE-SURFACE-IMPLEMENTED-
#: RUNG.md §3.1. Same precedent as `_COMPLEXITY_CAPABLE_LANGUAGES`
#: (api_structure.py) / `_DOCSTRING_CAPABLE_LANGUAGES` (documentation.py): a
#: language absent here must be NAMED as uncovered, never silently reported
#: as a measured zero.
MARKER_CAPABLE_LANGUAGES = frozenset({"python", "java"})


def _stamp_role(file_path: str, rows: list) -> list:
    """Every symbol and marker row carries the role of the file it came from
    (DESIGN-ARTIFACTS-VIEW-AND-DOC-LINKS.md §3). One call per file: the role is
    a fact about the path, decided by the one shared classifier."""
    if rows:
        from resource_explorer.ingestion.file_role import classify_file_role
        role = classify_file_role(file_path)
        for row in rows:
            row.file_role = role
    return rows


class CodeSymbolExtractor:
    """
    Extract structured symbol information from source files at ingestion time.

    Python uses the stdlib ast module (zero new dependencies, full type annotation
    support). Java, JS/TS, and Go all use tree-sitter (java_symbol_extractor.py,
    js_symbol_extractor.py, go_symbol_extractor.py) — upgraded from regex
    heuristics starting with Java in the AST-ownership-transfer plan Phase 2,
    since multi-line signatures and nested-scope attribution don't hold up well
    under single-line-anchored regex matching. Java's tree-sitter grammar is a
    required base dependency; JS/Go's stay under the optional [ast] extra (used
    today only for pgvector chunk-boundary splitting, ast_chunker.py), so the
    original regex extractors are kept as the fallback when that extra isn't
    installed rather than silently returning nothing.
    """

    def extract(
        self, file_path: str, content: str, resource_slug: str, language: str
    ) -> list[CodeSymbol]:
        return _stamp_role(file_path, self._extract_symbols(file_path, content, resource_slug, language))

    def _extract_symbols(
        self, file_path: str, content: str, resource_slug: str, language: str
    ) -> list[CodeSymbol]:
        try:
            if language == "python":
                return self._extract_python(file_path, content, resource_slug)
            if language in ("javascript", "typescript"):
                return self._extract_js(file_path, content, resource_slug, language)
            if language == "java":
                return self._extract_java_tree_sitter(file_path, content, resource_slug)
            if language == "go":
                return self._extract_go(file_path, content, resource_slug)
        except Exception:
            pass
        return []

    def extract_markers(
        self, file_path: str, content: str, resource_slug: str, language: str
    ) -> list[CodeMarker]:
        """Decorator/annotation registrations — the fourth stored-row input
        `interface_surface` reads (DESIGN-INTERFACE-SURFACE-IMPLEMENTED-RUNG.md
        §3.1). Only languages in MARKER_CAPABLE_LANGUAGES are attempted;
        anything else returns [] and the caller is responsible for recording
        the language as uncovered rather than reading the empty list as a
        measured zero.

        A test file registers nothing the product exposes (its ``patch(...)``
        calls and sample routes are mocks), so it yields no markers."""
        from resource_explorer.ingestion.test_paths import is_test_path
        if is_test_path(file_path):
            return []
        try:
            if language == "python":
                return _stamp_role(file_path, self._extract_python_markers(file_path, content, resource_slug))
            if language == "java":
                return _stamp_role(file_path, self._extract_java_markers(file_path, content, resource_slug))
        except Exception:
            pass
        return []

    def _extract_java_markers(self, file_path: str, content: str, resource_slug: str) -> list[CodeMarker]:
        from resource_explorer.ingestion.java_symbol_extractor import JavaMarkerExtractor
        return JavaMarkerExtractor().extract(file_path, content, resource_slug)

    # ── Java — tree-sitter (see ingestion/java_symbol_extractor.py) ────────

    def _extract_java_tree_sitter(self, file_path: str, content: str, resource_slug: str) -> list[CodeSymbol]:
        from resource_explorer.ingestion.java_symbol_extractor import JavaSymbolExtractor
        return JavaSymbolExtractor().extract(file_path, content, resource_slug)

    # ── Python — AST ─────────────────────────────────────────────────────────

    def _extract_python(self, file_path: str, content: str, resource_slug: str) -> list[CodeSymbol]:
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []
        visitor = _PythonVisitor(file_path, resource_slug)
        visitor.visit(tree)
        return visitor.symbols

    def _extract_python_markers(self, file_path: str, content: str, resource_slug: str) -> list[CodeMarker]:
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []
        visitor = _PythonMarkerVisitor(file_path, resource_slug)
        visitor.visit(tree)
        return visitor.markers

    # ── JavaScript / TypeScript — tree-sitter, regex fallback ──────────────

    def _extract_js(
        self, file_path: str, content: str, resource_slug: str, language: str
    ) -> list[CodeSymbol]:
        from resource_explorer.ingestion.js_symbol_extractor import JsSymbolExtractor
        symbols = JsSymbolExtractor().extract(file_path, content, resource_slug, language)
        if symbols:
            return symbols
        # Empty from tree-sitter means either a genuinely empty file or the
        # [ast] extra isn't installed (JsSymbolExtractor returns [] when its
        # parser is unavailable) — the regex path below still gives partial
        # coverage in that case rather than nothing.
        return self._extract_js_regex(file_path, content, resource_slug, language)

    _JS_CLASS  = re.compile(r'^(?:export\s+)?(?:abstract\s+)?class\s+(\w+)', re.M)
    _JS_IFACE  = re.compile(r'^(?:export\s+)?interface\s+(\w+)', re.M)
    _JS_FUNC   = re.compile(
        r'^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s+(\w+)\s*(\([^)]*\))', re.M
    )
    _JS_ARROW  = re.compile(
        r'^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(([^)]*)\)\s*(?::\s*\S+\s*)?=>',
        re.M,
    )
    _JS_METHOD = re.compile(
        r'^\s{2,}(?:(?:public|private|protected|static|async|override|readonly)\s+)*'
        r'(\w+)\s*(\([^)]*\))\s*(?::\s*[\w<>\[\]|&,\s.]+)?\s*\{',
        re.M,
    )
    _JS_KEYWORDS = frozenset({"if", "for", "while", "switch", "catch", "do", "else"})

    def _extract_js_regex(
        self, file_path: str, content: str, resource_slug: str, language: str
    ) -> list[CodeSymbol]:
        symbols: list[CodeSymbol] = []
        current_class: str | None = None

        def ln(m: re.Match) -> int:
            return content[: m.start()].count("\n") + 1

        for m in self._JS_CLASS.finditer(content):
            current_class = m.group(1)
            symbols.append(CodeSymbol(
                resource_slug=resource_slug, file_path=file_path, language=language,
                kind="class", name=m.group(1), qualified_name=m.group(1),
                signature="", docstring="", start_line=ln(m), end_line=ln(m),
            ))

        for m in self._JS_IFACE.finditer(content):
            symbols.append(CodeSymbol(
                resource_slug=resource_slug, file_path=file_path, language=language,
                kind="interface", name=m.group(1), qualified_name=m.group(1),
                signature="", docstring="", start_line=ln(m), end_line=ln(m),
            ))

        for m in self._JS_FUNC.finditer(content):
            symbols.append(CodeSymbol(
                resource_slug=resource_slug, file_path=file_path, language=language,
                kind="function", name=m.group(1), qualified_name=m.group(1),
                signature=f"({m.group(2)})", docstring="", start_line=ln(m), end_line=ln(m),
            ))

        for m in self._JS_ARROW.finditer(content):
            symbols.append(CodeSymbol(
                resource_slug=resource_slug, file_path=file_path, language=language,
                kind="function", name=m.group(1), qualified_name=m.group(1),
                signature=f"({m.group(2)})", docstring="", start_line=ln(m), end_line=ln(m),
            ))

        for m in self._JS_METHOD.finditer(content):
            name = m.group(1)
            if name in self._JS_KEYWORDS:
                continue
            qname = f"{current_class}.{name}" if current_class else name
            symbols.append(CodeSymbol(
                resource_slug=resource_slug, file_path=file_path, language=language,
                kind="method", name=name, qualified_name=qname,
                signature=f"({m.group(2)})", docstring="", start_line=ln(m), end_line=ln(m),
            ))

        return symbols

    # ── Go — tree-sitter, regex fallback ────────────────────────────────────

    def _extract_go(self, file_path: str, content: str, resource_slug: str) -> list[CodeSymbol]:
        from resource_explorer.ingestion.go_symbol_extractor import GoSymbolExtractor
        symbols = GoSymbolExtractor().extract(file_path, content, resource_slug)
        if symbols:
            return symbols
        # Same fallback reasoning as _extract_js — empty means either a
        # genuinely empty file or the [ast] extra isn't installed.
        return self._extract_go_regex(file_path, content, resource_slug)

    _GO_FUNC   = re.compile(
        r'^func\s+(?:\((\w+\s+\*?\w+)\)\s+)?(\w+)\s*(\([^)]*\))\s*(?:\(([^)]*)\)|([\w*\[\]]+))?',
        re.M,
    )
    _GO_STRUCT = re.compile(r'^type\s+(\w+)\s+struct\b', re.M)
    _GO_IFACE  = re.compile(r'^type\s+(\w+)\s+interface\b', re.M)

    def _extract_go_regex(self, file_path: str, content: str, resource_slug: str) -> list[CodeSymbol]:
        symbols: list[CodeSymbol] = []

        def ln(m: re.Match) -> int:
            return content[: m.start()].count("\n") + 1

        for m in self._GO_STRUCT.finditer(content):
            symbols.append(CodeSymbol(
                resource_slug=resource_slug, file_path=file_path, language="go",
                kind="class", name=m.group(1), qualified_name=m.group(1),
                signature="", docstring="", start_line=ln(m), end_line=ln(m),
            ))

        for m in self._GO_IFACE.finditer(content):
            symbols.append(CodeSymbol(
                resource_slug=resource_slug, file_path=file_path, language="go",
                kind="interface", name=m.group(1), qualified_name=m.group(1),
                signature="", docstring="", start_line=ln(m), end_line=ln(m),
            ))

        for m in self._GO_FUNC.finditer(content):
            receiver = m.group(1)  # e.g. "r *Router"
            name = m.group(2)
            params = m.group(3)
            ret = (m.group(4) or m.group(5) or "").strip()
            receiver_type = receiver.split()[-1].lstrip("*") if receiver else None
            kind = "method" if receiver_type else "function"
            qname = f"{receiver_type}.{name}" if receiver_type else name
            sig = params + (f" -> {ret}" if ret else "")
            symbols.append(CodeSymbol(
                resource_slug=resource_slug, file_path=file_path, language="go",
                kind=kind, name=name, qualified_name=qname,
                signature=sig, docstring="", start_line=ln(m), end_line=ln(m),
            ))

        return symbols


# ── Python AST visitor ────────────────────────────────────────────────────────

class _PythonVisitor(ast.NodeVisitor):
    def __init__(self, file_path: str, resource_slug: str) -> None:
        self._file_path = file_path
        self._project_slug = resource_slug
        self.symbols: list[CodeSymbol] = []
        self._class_stack: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._class_stack.append(node.name)
        self.symbols.append(self._make_class(node))
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_func(node)

    def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        kind = "method" if self._class_stack else "function"
        self.symbols.append(self._make_func(node, kind))
        # Don't recurse — nested functions clutter the index

    @staticmethod
    def _is_private(name: str) -> bool:
        """Leading underscore but not a dunder (__init__, __repr__, ...)."""
        return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))

    @staticmethod
    def _calculate_complexity(node: ast.AST) -> int:
        """Simplified cyclomatic complexity — same formula as Egeria Advisor's
        code_parser.py, ported for consistent complexity scores across both
        apps' Python code: base 1, +1 per If/For/While/ExceptHandler, +1 per
        And/Or boolean operator."""
        complexity = 1
        for child in ast.walk(node):
            if isinstance(child, (ast.If, ast.For, ast.While, ast.ExceptHandler)):
                complexity += 1
            elif isinstance(child, ast.BoolOp):
                complexity += len(child.values) - 1
        return complexity

    def _make_class(self, node: ast.ClassDef) -> CodeSymbol:
        doc = (ast.get_docstring(node) or "").strip()
        parent_class = self._class_stack[-2] if len(self._class_stack) > 1 else ""
        bases = [ast.unparse(b) for b in node.bases]
        return CodeSymbol(
            resource_slug=self._project_slug,
            file_path=self._file_path,
            language="python",
            kind="class",
            name=node.name,
            qualified_name=".".join(self._class_stack),  # stack already includes this class
            signature="",
            docstring=doc.split("\n")[0][:200] if doc else "",
            start_line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno),
            parent_class=parent_class,
            is_private=self._is_private(node.name),
            bases=bases,
        )

    def _make_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef, kind: str) -> CodeSymbol:
        doc = (ast.get_docstring(node) or "").strip()
        parent_class = self._class_stack[-1] if self._class_stack else ""
        return_type = ast.unparse(node.returns) if node.returns else ""
        return CodeSymbol(
            resource_slug=self._project_slug,
            file_path=self._file_path,
            language="python",
            kind=kind,
            name=node.name,
            qualified_name=".".join(self._class_stack + [node.name]),
            signature=self._build_sig(node),
            docstring=doc.split("\n")[0][:200] if doc else "",
            start_line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno),
            parent_class=parent_class,
            return_type=return_type,
            is_private=self._is_private(node.name),
            is_async=isinstance(node, ast.AsyncFunctionDef),
            complexity=self._calculate_complexity(node),
        )

    def _build_sig(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        args = node.args
        parts: list[str] = []

        for arg in getattr(args, "posonlyargs", []):
            s = arg.arg
            if arg.annotation:
                s += f": {ast.unparse(arg.annotation)}"
            parts.append(s)
        if getattr(args, "posonlyargs", []):
            parts.append("/")

        for arg in args.args:
            s = arg.arg
            if arg.annotation:
                s += f": {ast.unparse(arg.annotation)}"
            parts.append(s)

        if args.vararg:
            s = f"*{args.vararg.arg}"
            if args.vararg.annotation:
                s += f": {ast.unparse(args.vararg.annotation)}"
            parts.append(s)
        elif args.kwonlyargs:
            parts.append("*")

        for arg in args.kwonlyargs:
            s = arg.arg
            if arg.annotation:
                s += f": {ast.unparse(arg.annotation)}"
            parts.append(s)

        if args.kwarg:
            s = f"**{args.kwarg.arg}"
            if args.kwarg.annotation:
                s += f": {ast.unparse(args.kwarg.annotation)}"
            parts.append(s)

        ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
        return f"({', '.join(parts)}){ret}"


# ── Python AST marker visitor ──────────────────────────────────────────────
#
# Covered (decorator/base-class patterns, checked against real framework
# usage rather than invented):
#   http_api  — @app.get/post/put/delete/patch/options/head(...) (FastAPI/
#               Starlette-style, whatever the receiver object is named);
#               @app.route(...) / @bp.route(...) (Flask, verb from `methods=`
#               or GET by default); @api_view([...]) (DRF); Django's
#               path(...)/re_path(...) calls inside a urls.py-shaped file.
#   messaging — @app.task / @shared_task (Celery).
#   graphql   — @strawberry.field/mutation/subscription; a class deriving
#               from a base containing "ObjectType" (graphene), one marker
#               per resolve_* method.
#   grpc      — a class whose base-class expression contains "Servicer"
#               (the generated *_pb2_grpc.py base), one marker per public
#               method.
#
# Explicitly NOT covered, and not guessed at: Kafka consumer registration
# (aiokafka/kafka-python have no single common decorator or call-site
# convention detectable from decorators alone — would need import- and
# call-site tracking, which is a larger change, see the design doc §3.1);
# SOAP (no Python framework in the surveyed catalog uses one); Django
# middleware-based routing (only the ordinary path()/re_path() shape is
# read).
class _PythonMarkerVisitor(ast.NodeVisitor):
    _HTTP_VERBS = frozenset({"get", "post", "put", "delete", "patch", "options", "head"})

    def __init__(self, file_path: str, resource_slug: str) -> None:
        self._file_path = file_path
        self._project_slug = resource_slug
        self._is_urls_file = file_path.rsplit("/", 1)[-1] == "urls.py"
        self.markers: list[CodeMarker] = []
        self._class_stack: list[str] = []
        # Celery's `@task`/`@shared_task` only means a celery task when the file
        # is in celery's world; invoke's `@task` is a build-script command.
        self._celery_file = False
        #: names bound to a Celery app or imported from a module named *celery*
        self._celery_names: set[str] = set()

    #: Receivers that are mocking helpers, never an app or router.
    _MOCK_RECEIVER_TAILS = frozenset({"mock", "mocker", "monkeypatch", "patch"})

    def visit_Module(self, node: ast.Module) -> None:
        for n in ast.walk(node):
            if isinstance(n, ast.Import) and any(
                    a.name.split(".")[0] == "celery" for a in n.names):
                self._celery_file = True
            elif isinstance(n, ast.ImportFrom):
                mod = n.module or ""
                if mod.split(".")[0] == "celery":
                    self._celery_file = True
                if "celery" in mod.lower():
                    self._celery_names.update(a.asname or a.name for a in n.names)
            elif isinstance(n, ast.Assign) and isinstance(n.value, ast.Call):
                callee = self._dotted(n.value.func).rsplit(".", 1)[-1]
                if callee == "Celery":
                    self._celery_names.update(
                        t.id for t in n.targets if isinstance(t, ast.Name))
        self.generic_visit(node)

    # ── helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _dotted(node: ast.AST) -> str:
        """Best-effort dotted name for a decorator's receiver/callee —
        "app" from `app.get`, "strawberry" from `strawberry.field`."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = _PythonMarkerVisitor._dotted(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        return ""

    @staticmethod
    def _const_str(node: ast.AST | None) -> str:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return ""

    @staticmethod
    def _const_list_str(node: ast.AST | None) -> list[str]:
        if isinstance(node, (ast.List, ast.Tuple)):
            return [_PythonMarkerVisitor._const_str(e) for e in node.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        return []

    def _marker(self, node: ast.AST, marker_kind: str, framework: str,
                interface_kind: str, detail: str, name: str = "") -> CodeMarker:
        qualified_name = ".".join(self._class_stack + ([name] if name else []))
        return CodeMarker(
            resource_slug=self._project_slug, file_path=self._file_path,
            start_line=getattr(node, "lineno", 0), language="python",
            marker_kind=marker_kind, framework=framework,
            interface_kind=interface_kind, detail=detail,
            qualified_name=qualified_name,
        )

    # ── classes: gRPC servicers, graphene ObjectType resolvers ──────────────

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        bases = [ast.unparse(b) for b in node.bases]
        is_servicer = any("Servicer" in b for b in bases)
        is_graphene_type = any("ObjectType" in b for b in bases)

        self._class_stack.append(node.name)
        if is_servicer or is_graphene_type:
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if item.name.startswith("_"):
                    continue
                if is_servicer:
                    self.markers.append(self._marker(
                        item, "rpc_method", "grpc", "grpc", "", item.name))
                elif is_graphene_type and item.name.startswith("resolve_"):
                    self.markers.append(self._marker(
                        item, "resolver", "graphene", "graphql", item.name, item.name))
        self.generic_visit(node)
        self._class_stack.pop()

    # ── functions/methods: decorator-based registrations ────────────────────

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_func(node)

    def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for dec in node.decorator_list:
            marker = self._classify_decorator(dec, node)
            if marker is not None:
                self.markers.append(marker)
        # No recursion into nested functions — matches _PythonVisitor's own
        # choice; a decorated nested function is not a public registration.

    def _classify_decorator(self, dec: ast.expr, node) -> CodeMarker | None:
        call = dec if isinstance(dec, ast.Call) else None
        func = call.func if call is not None else dec
        args = call.args if call is not None else []
        kwargs = {kw.arg: kw.value for kw in (call.keywords if call is not None else [])
                  if kw.arg}

        if isinstance(func, ast.Attribute):
            attr = func.attr
            receiver = self._dotted(func.value)
        elif isinstance(func, ast.Name):
            attr = func.id
            receiver = ""
        else:
            return None

        if attr in self._HTTP_VERBS or attr == "route":
            # A route decorator is a CALL on an app/router, with a path that is
            # absolute (or empty, or computed). `@patch("pkg.mod.name")` and
            # `@mock.patch(...)` are mock patches, not registrations.
            if call is None:
                return None
            if receiver.rsplit(".", 1)[-1].lower() in self._MOCK_RECEIVER_TAILS:
                return None
            first = args[0] if args else kwargs.get("path")
            if isinstance(first, ast.Constant) and isinstance(first.value, str) \
                    and first.value and not first.value.startswith("/"):
                return None
            if isinstance(func, ast.Name) and not (
                    isinstance(first, ast.Constant) and isinstance(first.value, str)):
                return None  # bare `get(...)`/`patch(...)` needs a literal "/path"

        if attr in self._HTTP_VERBS:
            path = self._const_str(args[0] if args else kwargs.get("path"))
            detail = f"{attr.upper()} {path}".strip()
            return self._marker(node, "route", "fastapi", "http_api", detail, node.name)

        if attr == "route":
            path = self._const_str(args[0] if args else kwargs.get("path"))
            methods = self._const_list_str(kwargs.get("methods"))
            verb = methods[0] if methods else "GET"
            detail = f"{verb} {path}".strip()
            return self._marker(node, "route", "flask", "http_api", detail, node.name)

        if attr == "api_view":
            methods = self._const_list_str(args[0]) if args else []
            verb = methods[0] if methods else "GET"
            return self._marker(node, "route", "django-rest-framework",
                                 "http_api", verb, node.name)

        if attr in ("task", "shared_task") and (
                self._celery_file or receiver.split(".")[0] in self._celery_names
                or (not receiver and attr in self._celery_names)):
            return self._marker(node, "message_handler", "celery",
                                 "messaging", "", node.name)

        if attr in ("field", "mutation", "subscription") and receiver.split(".")[0] == "strawberry":
            return self._marker(node, "resolver", "strawberry", "graphql", attr, node.name)

        return None

    # ── Django urls.py: path()/re_path() call sites ─────────────────────────

    def visit_Call(self, node: ast.Call) -> None:
        if self._is_urls_file:
            name = node.func.id if isinstance(node.func, ast.Name) else (
                node.func.attr if isinstance(node.func, ast.Attribute) else "")
            if name in ("path", "re_path"):
                pattern = self._const_str(node.args[0]) if node.args else ""
                self.markers.append(CodeMarker(
                    resource_slug=self._project_slug, file_path=self._file_path,
                    start_line=getattr(node, "lineno", 0), language="python",
                    marker_kind="route", framework="django", interface_kind="http_api",
                    detail=pattern, qualified_name="",
                ))
        self.generic_visit(node)
