"""Fingerprint of the registry's schema-defining code (TR-112b).

Starts at `ProjectRegistry._init_schema` and follows everything that can change the DDL it sends:
`self.` methods and class constants, module-level assignments, functions and classes, and anything imported
from another `resource_explorer` module (`from x import NAME`, `from pkg import module`, `import x as m`), at
module level or inside a method. Only the definitions actually reached are hashed, not whole modules.
Comments, blank lines and docstrings never change the hash; any SQL, type, helper or constant does.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
from typing import Callable

ROOT_PACKAGE = "resource_explorer"
#: Packages whose definitions are followed. TR-115 adds the three trellis libraries whose stores run their own DDL.
FOLLOWED_PACKAGES = (ROOT_PACKAGE, "trellis_feedback", "trellis_vectorstore", "trellis_artifact_tree")


def default_source(modname: str) -> str | None:
    try:
        spec = importlib.util.find_spec(modname)
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.origin or not spec.origin.endswith(".py"):
        return None
    with open(spec.origin, encoding="utf-8") as fh:
        return fh.read()


class _DropDocstrings(ast.NodeTransformer):
    def _strip(self, node):
        self.generic_visit(node)
        b = node.body
        if b and isinstance(b[0], ast.Expr) and isinstance(getattr(b[0], "value", None), ast.Constant) \
                and isinstance(b[0].value.value, str):
            node.body = b[1:] or [ast.Pass()]
        return node

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _strip


class _Module:
    def __init__(self, name: str, source: str, packages: tuple[str, ...] = (ROOT_PACKAGE,)):
        self.name = name
        self.packages = packages
        self.tree = ast.parse(source)
        self.defs: dict[str, ast.AST] = {}
        self.aliases: dict[str, tuple[str, str | None]] = {}   # local name -> (module, attr or None)
        for node in self.tree.body:
            self._index(node)

    def _index(self, node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            self.defs[node.name] = node
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        self.defs[n.id] = node
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            self.defs[node.target.id] = node
        elif isinstance(node, (ast.If, ast.Try)):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.stmt):
                    self._index(child)
        self.collect_imports(node, self.aliases, self.name, self.packages)

    @staticmethod
    def collect_imports(node, into: dict, current: str, packages: tuple[str, ...] = (ROOT_PACKAGE,)) -> None:
        for sub in ast.walk(node):
            if isinstance(sub, ast.ImportFrom):
                mod = sub.module or ""
                if sub.level:
                    base = current.rsplit(".", sub.level)[0] if "." in current else ""
                    mod = f"{base}.{mod}".strip(".")
                if mod.split(".")[0] not in packages:
                    continue
                for a in sub.names:
                    into[a.asname or a.name] = (mod, a.name)
            elif isinstance(sub, ast.Import):
                for a in sub.names:
                    if a.name.split(".")[0] in packages:
                        into[a.asname or a.name.split(".")[0]] = (a.name, None)


def fingerprint(entry_module: str, class_name: str | None, root: str = "_init_schema",
                source_of: Callable[[str], str | None] = default_source, *,
                roots: tuple[str, ...] | None = None, packages: tuple[str, ...] = (ROOT_PACKAGE,)) -> str:
    """`roots` names several entry points (methods of `class_name`, or module-level functions when `class_name` is None);
    `packages` lists the top-level packages whose imports are followed (default: only resource_explorer, as in TR-112b)."""
    roots = roots or (root,)
    mods: dict[str, _Module] = {}

    def load(name: str) -> _Module | None:
        if name not in mods:
            src = source_of(name)
            if src is None:
                return None
            mods[name] = _Module(name, src, packages)
        return mods[name]

    entry = load(entry_module)
    members: dict[str, ast.AST] = {}
    if class_name is not None:
        klass = entry.defs[class_name]
        members = {n.name: n for n in klass.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        members.update({t.id: n for n in klass.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)})

    parts: dict[str, str] = {}
    todo: list[tuple[str, str]] = [(entry_module, f"{class_name}.{r}" if class_name else r) for r in roots]

    def add(key: str, node: ast.AST) -> None:
        parts[key] = ast.dump(_DropDocstrings().visit(ast.parse(ast.unparse(node)).body[0]))

    while todo:
        modname, ref = todo.pop()
        key = f"{modname}:{ref}"
        if key in parts:
            continue
        m = load(modname)
        if m is None:
            parts[key] = "unreadable"
            continue
        if class_name and ref.startswith(f"{class_name}.") and modname == entry_module:
            node = members.get(ref.split(".", 1)[1])
        else:
            node = m.defs.get(ref)
        if node is None:
            parts[key] = "unresolved"
            continue
        add(key, node)
        local = dict(m.aliases)
        _Module.collect_imports(node, local, modname, packages)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name):
                base = sub.value.id
                if modname == entry_module and base in ("self", "cls", class_name):
                    todo.append((entry_module, f"{class_name}.{sub.attr}"))
                elif base in local and local[base][1] is None:
                    todo.append((local[base][0], sub.attr))
                elif base in local:   # `from pkg import module` then module.NAME
                    pkg, attr = local[base]
                    todo.append((f"{pkg}.{attr}", sub.attr))
            elif isinstance(sub, ast.Name):
                if sub.id in m.defs:
                    todo.append((modname, sub.id))
                if sub.id in local and local[sub.id][1] is not None:
                    pkg, attr = local[sub.id]
                    target = load(pkg)
                    if target is not None and attr in target.defs:
                        todo.append((pkg, attr))
    blob = "\n".join(f"{k}={v}" for k, v in sorted(parts.items()))
    return hashlib.sha256(blob.encode()).hexdigest()
