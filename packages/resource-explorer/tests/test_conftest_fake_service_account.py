"""B1 (dreid fix round 1): the conftest's service-account defaults are fake non-person values, and are NOT set when
the live tier is requested (an env var outranks the package .env, so setting them would mask the real account)."""
from __future__ import annotations

import ast
from pathlib import Path

CONFTEST = Path(__file__).with_name("conftest.py")


def test_the_defaults_are_clearly_fake_and_not_a_persona():
    src = CONFTEST.read_text()
    assert "erinoverview" not in src.split("_live_tier_requested")[0].split("EGERIA_USER_ID")[-1]
    assert '"re-test-service-account"' in src and '"re-test-password"' in src


def test_the_defaults_are_set_only_when_the_live_tier_is_off():
    tree = ast.parse(CONFTEST.read_text())
    guarded = [n for n in tree.body if isinstance(n, ast.If)
               and "_live_tier_requested" in ast.dump(n.test)
               and "EGERIA_USER_ID" in ast.dump(n)]
    assert guarded, "the EGERIA_USER_ID default must sit under `if not _live_tier_requested()`"
    top_level_sets = [n for n in tree.body if isinstance(n, ast.Expr) and "EGERIA_USER_ID" in ast.dump(n)]
    assert top_level_sets == []
