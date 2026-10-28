"""Install the Analytic Library's example steps the way pip would, for one test.

Each step is a package outside RE (packages/trellis-analytics/examples/steps/<name>) and is not installed in RE's
environment, which is this suite's normal state. `install(tmp_path, name)` writes a dist-info that carries the entry
points the step's own pyproject declares, and returns the two path entries that make it importable and discoverable.
`staleness_installed` puts database_staleness on sys.path and re-attaches RE's user steps; `library_installed` does
the same for every example step; `install_steps` is the same for any chosen steps. Each puts everything back
afterwards. `pythonpath(tmp_path)` is every example step installed, for a subprocess (the generator's --check).
"""
from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
STEPS = REPO / "packages" / "trellis-analytics" / "examples" / "steps"
EXAMPLE = STEPS / "database_staleness"

#: Every example step, by package directory (= the step's module name).
EXAMPLE_STEPS = tuple(sorted(p.name for p in STEPS.iterdir() if (p / "pyproject.toml").is_file()))


def install(tmp_path: Path, name: str = "database_staleness") -> tuple[str, str]:
    """(dist-info directory's parent, the step's source root). Writes the dist-info once per tmp_path."""
    example = STEPS / name
    project = tomllib.loads((example / "pyproject.toml").read_text())["project"]
    eps = project["entry-points"]["trellis.analytics"]
    version = re.search(r'__version__ = "([^"]+)"', (example / "src" / name / "__init__.py").read_text()).group(1)
    root = tmp_path / "site"
    d = root / f"{project['name'].replace('-', '_')}-{version}.dist-info"
    if not d.exists():
        d.mkdir(parents=True)
        (d / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {project['name']}\nVersion: {version}\n")
        (d / "entry_points.txt").write_text(
            "[trellis.analytics]\n" + "".join(f"{k} = {v}\n" for k, v in eps.items()))
    return str(root), str(example / "src")


def _entries(tmp_path: Path, names) -> list[str]:
    out: list[str] = []
    for name in names:
        for entry in install(tmp_path, name):
            if entry not in out:
                out.append(entry)
    return out


def pythonpath(tmp_path: Path) -> str:
    """A PYTHONPATH value with every example step installed, for a subprocess (the generator's --check)."""
    parts = _entries(tmp_path, EXAMPLE_STEPS) + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    return os.pathsep.join(parts)


def _installing(tmp_path, monkeypatch, names):
    from resource_explorer.surveyors import survey_step_host

    for entry in reversed(_entries(tmp_path, names)):
        monkeypatch.syspath_prepend(entry)
    survey_step_host.refresh("database")


def _put_back(monkeypatch):
    from resource_explorer.surveyors import survey_step_host

    monkeypatch.undo()
    survey_step_host.refresh("database")


@pytest.fixture
def install_steps(tmp_path, monkeypatch):
    """Call with step names: installs them and re-attaches RE's user steps; everything is put back afterwards."""
    yield lambda *names: _installing(tmp_path, monkeypatch, names)
    _put_back(monkeypatch)


@pytest.fixture
def staleness_installed(tmp_path, monkeypatch):
    _installing(tmp_path, monkeypatch, ("database_staleness",))
    yield
    _put_back(monkeypatch)


@pytest.fixture
def library_installed(tmp_path, monkeypatch):
    _installing(tmp_path, monkeypatch, EXAMPLE_STEPS)
    yield
    _put_back(monkeypatch)
