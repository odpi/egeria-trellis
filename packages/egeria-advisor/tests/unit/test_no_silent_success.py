"""Ratchet against the "broad except, log-only body, still returns success"
shape in Egeria Advisor: a failure reported as an ordinary success
(EA Phase 1 Q2, BRIEFS-EA-PHASE1-QUALITY-FLOOR-2026-11-10).

Ported from packages/resource-explorer/tests/test_no_silent_success.py; the
detector (broad `except`, body only logs or `pass`, inside the nearest
function that returns a value) is the same code, run over advisor/**. See
that file's docstring for the two RE bugs that motivated it. EA's own
examples (DESIGN-EA-REVIEW-TO-RE-LEVEL-2026-11-10 §2): the feedback route,
the startup hook, /api/status and /api/egeria/zones all have the
"except: log; return ok" shape, and perspective_manager's live load fails
every time (`client.actor_mgr`) behind an info log, falling back to CSV.

Today's sites are recorded in no_silent_success_baseline.json, keyed by
"<path>::<function>" with a count (never by line number). One difference
from RE's ratchet, by the brief: a key whose count DROPS, or that is gone,
passes and prints "baseline can shrink: ...". Those sites sit in files that
Phase 0 (S1/S2/S4) and Phase 3 are changing in parallel, and their merges
must not turn this test red; whoever sees the line shrinks the baseline.
A NEW key, or a count that GREW, fails.

To regenerate after a deliberate change:
    python tests/unit/test_no_silent_success.py --write-baseline
"""
from __future__ import annotations

import ast
import json
import textwrap
from pathlib import Path

import pytest

PKG_ROOT = Path(__file__).resolve().parents[2] / "advisor"
BASELINE_PATH = Path(__file__).resolve().parent / "no_silent_success_baseline.json"

# Exact match only, on the called function's own name (the attribute name for
# `x.y()`, the bare name otherwise) — not a substring test. A helper like
# `self._log_error(...)` records something beyond a log line by definition
# (it is at minimum a distinct, greppable call site with its own contract),
# so it must NOT be treated as equivalent to `log.error(...)`.
LOG_CALL_NAMES = {
    "debug", "info", "warning", "warn", "error", "exception", "critical", "print",
}


def _call_name(call: ast.Call) -> str | None:
    fn = call.func
    if isinstance(fn, ast.Attribute):
        return fn.attr
    if isinstance(fn, ast.Name):
        return fn.id
    return None


def _is_log_only_body(body: list[ast.stmt]) -> bool:
    """True only if every statement is a no-op `pass` or a call to something
    in LOG_CALL_NAMES. A `return`, `raise`, assignment, or any other call
    (including a differently-named logging helper) means the handler does
    more than log — which is exactly the distinction that matters here."""
    for stmt in body:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            if _call_name(stmt.value) in LOG_CALL_NAMES:
                continue
        return False
    return True


def _is_broad(handler: ast.ExceptHandler) -> bool:
    """Bare `except:` or `except Exception:` only. A named subclass, or a
    tuple of specific exceptions, is a deliberate decision about what's
    expected to fail and is not what hid either bug — those two were both
    `except Exception`, wide enough to swallow a bug wearing a runtime
    error's clothes."""
    if handler.type is None:
        return True
    return isinstance(handler.type, ast.Name) and handler.type.id == "Exception"


def _fn_returns_value(fn: ast.AST) -> bool:
    # A generator's `yield` doesn't count — only an explicit `return <expr>`
    # is the "handed back a success value" contract this test is about.
    return any(isinstance(n, ast.Return) and n.value is not None for n in ast.walk(fn))


def find_silent_success_handlers(tree: ast.AST) -> list[tuple[str, ast.ExceptHandler]]:
    """Yield (enclosing_function_name, handler) for every broad, log-only
    except handler whose nearest enclosing FunctionDef/AsyncFunctionDef
    returns a value somewhere in its body.

    "Nearest enclosing" (not "any enclosing"): a handler inside a nested
    helper function is attributed to that helper, not to whatever function
    the helper is defined in — the helper is what actually owns the return
    contract being violated.
    """
    results: list[tuple[str, ast.ExceptHandler]] = []

    def visit(node: ast.AST, fn_stack: list[ast.AST]) -> None:
        is_fn = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        new_stack = fn_stack + [node] if is_fn else fn_stack
        if isinstance(node, ast.ExceptHandler):
            if _is_broad(node) and _is_log_only_body(node.body) and new_stack:
                enclosing = new_stack[-1]
                if _fn_returns_value(enclosing):
                    results.append((enclosing.name, node))
        for child in ast.iter_child_nodes(node):
            visit(child, new_stack)

    visit(tree, [])
    return results


def _iter_py_files():
    return sorted(PKG_ROOT.rglob("*.py"))


def _current_findings() -> dict[str, int]:
    """Map "<relative_path>::<function_name>" -> count of flagged handlers,
    across the whole package. Stable across line-number churn by design —
    two functions of the same name in different modules stay distinct
    because the path is part of the key."""
    counts: dict[str, int] = {}
    for path in _iter_py_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        rel = path.relative_to(PKG_ROOT.parent).as_posix()
        for fn_name, _handler in find_silent_success_handlers(tree):
            key = f"{rel}::{fn_name}"
            counts[key] = counts.get(key, 0) + 1
    return counts


def _baseline() -> dict[str, int]:
    return json.loads(BASELINE_PATH.read_text())


class TestDetectorSemantics:
    """Verify the detector on constructed shapes before trusting it on the
    real tree — including the two bugs that motivated this file."""

    def test_flags_the_database_surveyor_bug_shape(self):
        src = textwrap.dedent('''
            def _catalog_and_survey(self, db_entity, registry):
                server_survey_guid = ""
                if server_guid:
                    try:
                        server_survey_guid = self._initiate_survey("PostgreSQL Server", server_guid)
                        log.info(f"Egeria server survey initiated: {server_survey_guid}")
                    except Exception as exc:
                        log.warning(f"Server survey initiation failed (non-fatal): {exc}")
                return {"server_survey_guid": server_survey_guid}
        ''')
        found = find_silent_success_handlers(ast.parse(src))
        assert [name for name, _ in found] == ["_catalog_and_survey"]

    def test_flags_the_prefect_adapter_bug_shape(self):
        src = textwrap.dedent('''
            def run_prefect_step(entity_type, slug, step_name, runner_kwargs):
                if config.prefect.enabled:
                    try:
                        loop = asyncio.get_running_loop()
                        return asyncio.run(_run_prefect_step_api(entity_type, slug, step_name, runner_kwargs))
                    except Exception as e:
                        logging.warning(
                            "Prefect API dispatch failed (%s), falling back: %s",
                            type(e).__name__, e, exc_info=True,
                        )
                return re_survey_flow(entity_type, slug, step_name, runner_kwargs)
        ''')
        found = find_silent_success_handlers(ast.parse(src))
        assert [name for name, _ in found] == ["run_prefect_step"]

    def test_does_not_flag_a_narrow_except(self):
        """A specific exception class is a deliberate contract, not a net
        cast wide enough to also catch a bug."""
        src = textwrap.dedent('''
            def do_thing():
                try:
                    risky()
                except ConnectionError as exc:
                    log.warning("failed: %s", exc)
                return {"ok": True}
        ''')
        assert find_silent_success_handlers(ast.parse(src)) == []

    def test_does_not_flag_a_handler_that_reraises(self):
        """A re-raise propagates the failure — nothing is hidden."""
        src = textwrap.dedent('''
            def do_thing():
                try:
                    risky()
                except Exception as exc:
                    log.warning("failed: %s", exc)
                    raise
                return {"ok": True}
        ''')
        assert find_silent_success_handlers(ast.parse(src)) == []

    def test_does_not_flag_a_handler_that_returns_a_value(self):
        """Returning an explicit failure value (e.g. an error result) is
        exactly the fix this test wants to see, not the bug it hunts."""
        src = textwrap.dedent('''
            def do_thing():
                try:
                    risky()
                except Exception as exc:
                    log.warning("failed: %s", exc)
                    return {"ok": False, "error": str(exc)}
                return {"ok": True}
        ''')
        assert find_silent_success_handlers(ast.parse(src)) == []

    def test_does_not_flag_log_only_handler_in_a_function_returning_nothing(self):
        """No return value means no success to falsely report — there is no
        caller reading a result and being misled by it."""
        src = textwrap.dedent('''
            def do_thing():
                try:
                    risky()
                except Exception as exc:
                    log.warning("failed: %s", exc)
        ''')
        assert find_silent_success_handlers(ast.parse(src)) == []


class TestRatchet:
    """The baseline may only shrink. A new site, or growth at an existing
    site, fails here rather than in a code review months later."""

    def test_no_new_silent_success_sites(self):
        current = _current_findings()
        baseline = _baseline()

        new_keys = sorted(set(current) - set(baseline))
        grown = sorted(
            k for k in set(current) & set(baseline) if current[k] > baseline[k]
        )

        assert not new_keys and not grown, (
            "New or worsened broad-except/log-only/value-returning site(s) found:\n"
            + "\n".join(f"  NEW  {k}: {current[k]}" for k in new_keys)
            + ("\n" if new_keys and grown else "")
            + "\n".join(
                f"  GREW {k}: {baseline[k]} -> {current[k]}" for k in grown
            )
            + "\n\nFor each site, either: (1) record something observable in the "
            "handler -- an error field on the returned result, a status flag the "
            "caller can branch on, an HTTP error -- so a failure there is "
            "distinguishable from ordinary success; or (2) narrow the `except` to "
            "the specific exception(s) actually expected; or (3), if the site is "
            "genuinely best-effort and that is a deliberate, reviewed decision, "
            "add it to tests/unit/no_silent_success_baseline.json explicitly."
        )

    def test_shrunk_or_gone_entries_pass_and_say_so(self, capsys):
        """A count that dropped, or a key that is gone, is progress: it
        passes, and says the baseline can shrink."""
        current = _current_findings()
        baseline = _baseline()
        shrinkable = sorted(
            k for k in baseline if current.get(k, 0) < baseline[k]
        )
        if shrinkable:
            with capsys.disabled():
                print(
                    "\nbaseline can shrink: "
                    + ", ".join(f"{k} {baseline[k]} -> {current.get(k, 0)}" for k in shrinkable)
                )

    def test_total_does_not_exceed_baseline(self):
        """The one number that should only ever go down."""
        current_total = sum(_current_findings().values())
        baseline_total = sum(_baseline().values())
        assert current_total <= baseline_total, (
            f"Total silent-success sites grew from {baseline_total} to "
            f"{current_total} -- this count must never increase."
        )

    def test_baseline_counts_are_positive(self):
        assert all(isinstance(v, int) and v > 0 for v in _baseline().values())


if __name__ == "__main__":  # python tests/unit/test_no_silent_success.py --write-baseline
    import sys
    if "--write-baseline" in sys.argv:
        BASELINE_PATH.write_text(json.dumps(dict(sorted(_current_findings().items())), indent=2) + "\n")
        print(f"wrote {BASELINE_PATH}")
