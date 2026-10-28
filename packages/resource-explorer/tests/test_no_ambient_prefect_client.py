"""Ratchet test against a bare `get_client(` call anywhere in
`resource_explorer` — the shape behind the 2026-09-28 silent-dispatch bug.

`prefect.client.get_client()` (the installed Prefect version, 3.8.1) takes
no `api=` argument. It resolves the server address itself, from
`PREFECT_API_URL.value()` — Prefect's own settings machinery, read fresh at
call time from whatever the process environment/settings context looks like
at that moment, which is NOT the same thing as RE's own `config.prefect.
api_url` (`resource_explorer/config.py`). A live web-server process that had
`PREFECT_API_URL` correctly set had `get_client()` still raise `ValueError:
No Prefect API URL provided`, because nothing in that process had ever
pointed Prefect's OWN settings resolution at RE's configured URL.

`run_prefect_step`'s existing broad `except Exception` (see
`prefect_adapter.py`, and `tests/test_no_silent_success.py` for the sibling
bug this same function already carries) caught that `ValueError` exactly
like a genuinely unreachable server and fell back to local execution — while
`step_runs.executor` had already been written as `'prefect'`, entered before
dispatch even attempted. Every `step_runs` row labelled `executor='prefect'`
from an affected process was therefore not trustworthy evidence that
anything had run through Prefect at all — see
`PREFECT-DISPATCH-HONESTY-IMPLEMENTED.md`.

The fix: every module that needs a Prefect client goes through
`resource_explorer.surveyors.prefect_adapter.re_prefect_client()`, which
constructs `PrefectClient(config.prefect.api_url)` directly — `PrefectClient`
(unlike the `get_client()` convenience wrapper) takes `api` as a required
positional argument and does no ambient-settings resolution of its own.

This test walks `resource_explorer` with `ast` and fails if ANY call to a
function or attribute named `get_client` appears outside
`prefect_adapter.py`'s own `re_prefect_client()` definition — the one place
that name is allowed to appear as a live call (it doesn't; `re_prefect_client`
calls `PrefectClient(...)` directly, never `get_client()` — but the guard
stays function-scoped rather than file-scoped so a future re-introduction of
`get_client()` inside that same file, in a different function, still fails
loudly instead of hiding behind an established exemption).

No baseline file: unlike `test_no_silent_success.py`'s 112 pre-existing
sites, this codebase has zero legitimate call sites for `get_client(` today
— the whole point of `re_prefect_client()` is that nothing should ever call
it directly. A hard zero-tolerance assertion, not a ratchet with debt to
grandfather in.
"""
from __future__ import annotations

import ast
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1] / "resource_explorer"

#: The one function allowed to construct a Prefect client any way it likes —
#: it's the thing every other call site is required to go through instead.
ALLOWED_FILE = "prefect_adapter.py"
ALLOWED_FUNCTION = "re_prefect_client"


def _call_name(node: ast.Call) -> str | None:
    fn = node.func
    if isinstance(fn, ast.Attribute):
        return fn.attr
    if isinstance(fn, ast.Name):
        return fn.id
    return None


def _find_get_client_calls(tree: ast.AST) -> list[tuple[str, int]]:
    """(enclosing_function_name_or_'<module>', lineno) for every call to
    something named `get_client`."""
    results: list[tuple[str, int]] = []

    def visit(node: ast.AST, fn_name: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn_name = node.name
        if isinstance(node, ast.Call) and _call_name(node) == "get_client":
            results.append((fn_name, node.lineno))
        for child in ast.iter_child_nodes(node):
            visit(child, fn_name)

    visit(tree, "<module>")
    return results


def _iter_py_files():
    return sorted(PKG_ROOT.rglob("*.py"))


def test_no_bare_get_client_call_outside_the_designated_helper():
    offenders: list[str] = []
    for path in _iter_py_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for fn_name, lineno in _find_get_client_calls(tree):
            if path.name == ALLOWED_FILE and fn_name == ALLOWED_FUNCTION:
                continue
            rel = path.relative_to(PKG_ROOT.parent).as_posix()
            offenders.append(f"{rel}:{lineno} (in {fn_name})")

    assert not offenders, (
        "Found a bare `get_client(` call outside "
        f"{ALLOWED_FILE}::{ALLOWED_FUNCTION} — this reads Prefect's ambient/"
        "frozen settings instead of RE's own configured PREFECT_API_URL "
        "(see this test's module docstring and "
        "PREFECT-DISPATCH-HONESTY-IMPLEMENTED.md). Use "
        "`resource_explorer.surveyors.prefect_adapter.re_prefect_client()` "
        "instead:\n" + "\n".join(f"  {o}" for o in offenders)
    )


def test_the_designated_helper_itself_never_calls_get_client():
    """`re_prefect_client()` must construct `PrefectClient` directly, not
    delegate to `get_client()` under a different name — that would defeat
    the whole point while still passing the test above (which exempts this
    exact function)."""
    path = PKG_ROOT / "surveyors" / "prefect_adapter.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    calls = _find_get_client_calls(tree)
    assert calls == [], (
        f"{ALLOWED_FILE}::{ALLOWED_FUNCTION} must not call get_client() "
        f"itself: {calls}"
    )
