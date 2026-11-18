"""Every pyegeria client EA or Studio builds says whose password it holds (TR-34 read-back R1).

pyegeria's client constructor fills an absent `user_pwd` from its settings (env EGERIA_USER_PASSWORD), and
`user_id` from EGERIA_USER. A client built without an explicit `user_pwd` would silently hold whatever
password a shell or .env put there, beside a user's token or as the "service" identity. So every constructor
call of a pyegeria client class in EA's advisor/ and Studio's trellis_studio/ passes `user_pwd`: the service
account's password, or NO_PASSWORD for a token client.

The check reads the source (AST) and resolves each called name to the class it was imported as. Only real
pyegeria client classes count (subclasses of its base server client), so models such as FormatSet are not
caught (a client is a class whose constructor takes `user_pwd`). Allow-listed: introspection that never signs in.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

pytest.importorskip("pyegeria")

PACKAGES = Path(__file__).resolve().parents[3]
ROOTS = [PACKAGES / "egeria-advisor" / "advisor", PACKAGES / "studio" / "trellis_studio"]
#: (file relative to packages/, the line's text) -- builds a client only to read a signature; never signs in.
ALLOWED = {("egeria-advisor/advisor/spec_authoring_v2.py", 'EgeriaTech("v", "https://localhost", "u")')}


def _is_client(module: str, name: str) -> bool:
    """A pyegeria class whose constructor takes `user_pwd` (the EgeriaTech facade and every sub-client)."""
    import inspect
    try:
        obj = getattr(importlib.import_module(module), name)
    except Exception:  # noqa: BLE001 -- not importable here: not something this check can judge
        return False
    if not isinstance(obj, type):
        return False
    try:
        return "user_pwd" in inspect.signature(obj.__init__).parameters
    except (TypeError, ValueError):
        return False


def _imported_clients(tree: ast.AST) -> Dict[str, str]:
    """Local names bound to pyegeria client classes in this file: {local name: 'module.Class'}."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "pyegeria":
            for a in node.names:
                if _is_client(node.module, a.name):
                    out[a.asname or a.name] = f"{node.module}.{a.name}"
    return out


def _passes_user_pwd(call: ast.Call) -> bool:
    return any(k.arg == "user_pwd" for k in call.keywords) or len(call.args) >= 4 or \
        any(k.arg is None for k in call.keywords)          # **kwargs: the caller's dict decides; judged where built


def unexplicit(roots: List[Path]) -> List[str]:
    bad = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src, filename=str(path))
            clients = _imported_clients(tree)
            if not clients:
                continue
            lines = src.splitlines()
            for call in ast.walk(tree):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id in clients):
                    continue
                if _passes_user_pwd(call):
                    continue
                rel = str(path.relative_to(PACKAGES)) if path.is_relative_to(PACKAGES) else path.name
                if any(rel == f and text in lines[call.lineno - 1] for f, text in ALLOWED):
                    continue
                bad.append(f"{rel}:{call.lineno}: {clients[call.func.id]}(...) without an explicit user_pwd")
    return bad


def test_every_pyegeria_client_is_built_with_an_explicit_user_pwd():
    assert not unexplicit(ROOTS), "\n".join(unexplicit(ROOTS))


def test_the_check_finds_a_bare_token_client(tmp_path):
    (tmp_path / "m.py").write_text(
        "from pyegeria.egeria_tech_client import EgeriaTech\n"
        "def token_client(token):\n"
        "    c = EgeriaTech('s', 'https://x', 'erinoverview')\n"
        "    c.set_bearer_token(token)\n"
        "    return c\n"
        "def fine(token):\n"
        "    from pyegeria.core._no_password import NO_PASSWORD\n"
        "    return EgeriaTech('s', 'https://x', 'erinoverview', user_pwd=NO_PASSWORD)\n")
    found = unexplicit([tmp_path])
    assert len(found) == 1 and "m.py:3:" in found[0]


def test_models_are_not_clients():
    assert not _is_client("pyegeria.view._output_format_models", "FormatSet")
    assert _is_client("pyegeria.egeria_tech_client", "EgeriaTech")
