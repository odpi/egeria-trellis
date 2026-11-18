"""The RE image installs every reference survey step, not resource-explorer alone (2026-11-13).

`uv sync --frozen --no-dev --package resource-explorer` re-resolves the venv down to that one package, so the
library steps under packages/trellis-analytics/examples/steps were never in any RE image, the web image
included (the same hazard the repo CLAUDE.md names for a package-scoped `uv sync`). The Dockerfile now reads
the step list from the tree; these checks keep it that way. The image's own "none missing" proof
(`library_steps_line()` inside a built image, before vs after) is the gate's, since this suite builds no image.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DOCKERFILE = (REPO / "docker" / "Dockerfile.resource-explorer").read_text()
STEPS = sorted((REPO / "packages" / "trellis-analytics" / "examples" / "steps").glob("*/pyproject.toml"))


def test_the_step_list_is_read_from_the_tree_not_hand_kept():
    assert "packages/trellis-analytics/examples/steps/*/pyproject.toml" in DOCKERFILE
    assert "uv sync --frozen --no-dev --package resource-explorer $steps" in DOCKERFILE


def test_no_one_package_sync_is_left():
    assert not re.search(r"uv sync[^\n]*--package resource-explorer\s*$", DOCKERFILE, re.M), \
        "a bare `--package resource-explorer` sync drops every library step"


def test_every_step_has_a_name_the_loop_can_read():
    """The loop takes the first `name = "..."` line of each step's pyproject; a step without one would be
    silently skipped."""
    assert STEPS, "no reference steps found"
    for p in STEPS:
        assert re.search(r'^name = "[^"]+"$', p.read_text(), re.M), p


def test_the_steps_source_reaches_the_runtime_stage():
    """Workspace members install editable: their source must be copied into the runtime stage as well."""
    runtime = DOCKERFILE.split("AS runtime", 1)[1]
    assert "COPY --chown=trellis:trellis packages/trellis-analytics packages/trellis-analytics" in runtime
