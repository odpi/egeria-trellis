"""Both app images copy the same workspace packages into the runtime stage as
into the builder stage.

Why: the image's ``uv sync`` installs workspace members editable, so the
runtime venv points at ``/app/packages/<name>``. A package whose source is
copied in the builder but not in the runtime stage builds a green image that
fails only at import time in the running container. That happened on
2026-11-11: A-F1 (#1069) added trellis-feedback to the builder stage only,
and ``/api/feedback/v2`` could not import in the EA image (fixed by #1074).
No image build catches this class, and since #1075 PRs build images only
when image inputs change, so this test is the check that runs on every PR.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DOCKERFILES = sorted((REPO / "docker").glob("Dockerfile.*"))

_FROM = re.compile(r"^FROM\s+\S+(?:\s+AS\s+(\S+))?", re.IGNORECASE)
# A whole-package source copy: COPY [--flags] packages/<pkg> packages/<pkg>
# (a pyproject-only copy, used for the lock layer, does not match).
_PKG_COPY = re.compile(
    r"^COPY\s+(?:--\S+\s+)*packages/([A-Za-z0-9_.-]+)/?\s+packages/\1/?\s*$"
)


def _stage_copies(dockerfile: Path) -> dict[str, set[str]]:
    stages: dict[str, set[str]] = {}
    current = None
    for raw in dockerfile.read_text().splitlines():
        line = raw.strip()
        m = _FROM.match(line)
        if m:
            current = (m.group(1) or f"stage{len(stages)}").lower()
            stages[current] = set()
            continue
        m = _PKG_COPY.match(line)
        if m and current is not None:
            stages[current].add(m.group(1))
    return stages


def test_dockerfiles_found():
    names = {p.name for p in DOCKERFILES}
    assert {"Dockerfile.resource-explorer", "Dockerfile.egeria-advisor"} <= names


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: p.name)
def test_runtime_stage_copies_every_package_the_builder_installs(dockerfile: Path):
    stages = _stage_copies(dockerfile)
    assert "builder" in stages and "runtime" in stages, sorted(stages)
    builder, runtime = stages["builder"], stages["runtime"]
    assert builder, "no package source COPY found in the builder stage"
    missing = sorted(builder - runtime)
    extra = sorted(runtime - builder)
    assert not missing, (
        f"{dockerfile.name}: the runtime stage does not copy {missing}; "
        "uv installed them editable in the builder, so they fail at import"
    )
    assert not extra, f"{dockerfile.name}: runtime copies {extra} that the builder never installed"


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: p.name)
def test_every_copied_package_exists(dockerfile: Path):
    for stage, pkgs in _stage_copies(dockerfile).items():
        for pkg in pkgs:
            assert (REPO / "packages" / pkg).is_dir(), f"{dockerfile.name} [{stage}] copies missing packages/{pkg}"
