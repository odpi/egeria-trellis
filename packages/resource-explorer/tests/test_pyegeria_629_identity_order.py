"""pyegeria 6.2.9 (#477, ISSUE-161): Dr.Egeria resolves the user at run time, explicit identity first.

Pins two things against the REAL pyegeria 6.2.9 resolver and RE's real child argv builder:
1. An explicit user id beats EGERIA_USER in the environment, so RE's explicit identity keeps winning.
2. RE's child argv still carries `--userid <user>`, and the child's parser still requires it.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from pyegeria.core.config import resolve_egeria_credentials

import resource_explorer
from resource_explorer.bootstrap import (DrEgeriaIdentity, SOURCE_ADHOC_AS, dr_egeria_command)


def test_explicit_user_wins_over_egeria_user_env(monkeypatch):
    monkeypatch.setenv("EGERIA_USER", "env-user")
    monkeypatch.setenv("EGERIA_USER_PASSWORD", "env-pwd")
    user, pwd = resolve_egeria_credentials("explicit-user", "explicit-pwd")
    assert user == "explicit-user"
    assert pwd == "explicit-pwd"


def test_env_user_used_only_when_no_explicit_user(monkeypatch):
    monkeypatch.setenv("EGERIA_USER", "env-user")
    monkeypatch.setenv("EGERIA_USER_PASSWORD", "env-pwd")
    user, _ = resolve_egeria_credentials()
    assert user == "env-user"


def test_re_child_argv_carries_explicit_userid(monkeypatch):
    monkeypatch.setenv("EGERIA_USER", "env-user")
    identity = DrEgeriaIdentity(user="explicit-user", source=SOURCE_ADHOC_AS, password="pw")
    argv, extra_env = dr_egeria_command("doc.md", identity, server="S", url="http://u")
    i = argv.index("--userid")
    assert argv[i + 1] == "explicit-user"
    assert "pw" not in argv
    assert extra_env


def test_re_child_parser_still_requires_userid():
    src = (Path(resource_explorer.__file__).parent / "dr_egeria_child.py").read_text()
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "add_argument"
                and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "--userid"):
            kw = {k.arg: getattr(k.value, "value", None) for k in node.keywords}
            assert kw.get("required") is True
            found = True
    assert found, "dr_egeria_child.py no longer declares --userid"
    assert list(inspect.signature(resolve_egeria_credentials).parameters) == ["user_id", "user_pwd"]
