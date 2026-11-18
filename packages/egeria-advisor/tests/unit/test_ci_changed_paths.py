"""The CI decide script (.github/scripts/changed_paths.py) that lets ea-tests / re-tests be required
checks. It lives outside any package, so its test is here: a change to the script triggers ea-tests
(the script is on EA's include list). GitHub path-filter semantics: `*` stays in one segment, `**`
crosses segments, `**/` may match nothing."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[4] / ".github" / "scripts" / "changed_paths.py"
_spec = importlib.util.spec_from_file_location("changed_paths", SCRIPT)
cp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cp)


@pytest.mark.parametrize("pattern, path, matches", [
    ("docs/*.md", "docs/a.md", True),
    ("docs/*.md", "docs/sub/a.md", False),          # * stays within one segment
    ("docs/**", "docs/sub/deeper/a.md", True),       # ** crosses segments
    ("**/README.md", "README.md", True),             # **/ may match nothing
    ("**/README.md", "a/b/README.md", True),
    ("packages/x/**", "packages/xy/a.py", False),
    ("uv.lock", "uv.lock", True),
    ("uv.lock", "sub/uv.lock", False),
    ("a?c.txt", "abc.txt", True),
    ("a?c.txt", "a/c.txt", False),
])
def test_glob_to_regex(pattern, path, matches):
    assert bool(cp.glob_to_regex(pattern).match(path)) is matches


def test_include_runs_on_any_match_and_ignore_runs_on_any_miss():
    assert cp.decide(["packages/egeria-advisor/a.py", "README.md"], include=["packages/egeria-advisor/**"]) == (
        True, ["packages/egeria-advisor/a.py"])
    assert cp.decide(["packages/resource-explorer/a.py"], include=["packages/egeria-advisor/**"]) == (False, [])
    ign = ["packages/resource-explorer/docs/*.md"]
    assert cp.decide(["packages/resource-explorer/docs/a.md"], ignore=ign) == (False, [])
    assert cp.decide(["packages/resource-explorer/docs/dr-egeria/a.md"], ignore=ign)[0] is True


def test_an_unknown_base_fails_instead_of_skipping(tmp_path, monkeypatch):
    monkeypatch.chdir(SCRIPT.parents[2])
    with pytest.raises(Exception):
        cp.main(["--base", "0" * 40, "--head", "HEAD", "--include", "x"])
