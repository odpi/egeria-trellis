"""Classic UI: deployment_evidence groups evidence by kind (2026-10-01).

Bug (egeria-python Dashboard, Documentation & Conventions, APPLICATION card):
a distribution with ~50 [project.scripts] entries printed ~50 bare
"console_script" strings and no names -- the right count, the kind where each
name belonged. Real node execution of the extracted renderer.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

INDEX = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "index.html"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _fn(html: str, name: str) -> str:
    start = html.index(f"function {name}(")
    depth, i = 1, html.index("{", start) + 1
    while depth:
        depth += {"{": 1, "}": -1}.get(html[i], 0)
        i += 1
    return html[start:i]


def _render(data: dict) -> str:
    html = INDEX.read_text()
    js = (_fn(html, "_esc") + "\n" + _fn(html, "_renderDeploymentEvidenceResults")
          + f"\nprocess.stdout.write(_renderDeploymentEvidenceResults({json.dumps(data)}));")
    return subprocess.run(["node", "-e", js], capture_output=True, text=True,
                          check=True, timeout=30).stdout


def _data(evidence):
    return {"coverage": {"summary": "s"}, "message": "m", "distributions": [
        {"name": "pyegeria", "ecosystem": "python", "verdict": "application", "evidence": evidence}]}


def test_many_console_scripts_and_a_second_kind_are_grouped_with_names():
    ev = [{"kind": "console_script", "path": "pyproject.toml", "detail": f"cmd{i}"} for i in range(50)]
    ev += [{"kind": "dunder_main", "path": "a/__main__.py"},
           {"kind": "dunder_main", "path": "b/__main__.py"},
           {"kind": "dockerfile_present", "path": "Dockerfile"}]
    out = _render(_data(ev))
    assert out.count("console_script") == 1
    assert "console_script · 50: cmd0, cmd1," in out and "cmd49" in out
    assert out.count("dunder_main") == 1 and "dunder_main · 2" in out
    assert out.count("dockerfile_present") == 1


def test_single_kind_single_item_is_unchanged():
    out = _render(_data([{"kind": "console_script", "path": "p", "detail": "only"}]))
    assert 'mono">console_script</div>' in out
