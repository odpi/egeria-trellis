"""TR-101: ruff F821 at scripts/clear_egeria_pointers_after_reset.py: `exc` (bound by `except ... as exc`) was read inside a
lambda. Python unbinds `exc` when the except block ends, so a lambda that is not called inside it, or a linter, sees an
undefined name. The fix reads what the lambda needs into a plain local before the lambda; the wording must not change
(tests/test_egeria_reset_cleanup.py pins "FAILED in group ..."). ruff is not installed in CI, so this checks the same thing
by reading the source."""
from __future__ import annotations

import ast
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "clear_egeria_pointers_after_reset.py"


def _lambdas_reading_the_except_name(tree: ast.AST) -> list[tuple[int, str]]:
    bad = []
    for handler in (n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler) and n.name):
        for stmt in handler.body:
            for lam in (n for n in ast.walk(stmt) if isinstance(n, ast.Lambda)):
                if any(isinstance(n, ast.Name) and n.id == handler.name for n in ast.walk(lam)):
                    bad.append((lam.lineno, handler.name))
    return bad


def test_no_lambda_in_the_cleanup_script_reads_an_except_name():
    tree = ast.parse(SCRIPT.read_text())
    assert _lambdas_reading_the_except_name(tree) == []


def test_the_failure_row_still_names_the_group_and_the_exception_type():
    text = SCRIPT.read_text()
    assert "Egeria reset cleanup FAILED in group {group} after {len(done)} committed: {failed_kind}" in text
