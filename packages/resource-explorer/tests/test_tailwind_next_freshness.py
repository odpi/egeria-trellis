"""tailwind-next.css is a committed, pre-built artifact -- the ONLY stylesheet
`/next` loads (no CDN/JIT, index.html links it directly). Twice in two days
(G1, then a dropdown fix on 2026-09-27/28) an agent added new Tailwind class
names to next/*.js or stages/*.js and never re-ran the build, so the new
classes rendered as literally nothing: no error, no visual cue beyond the
broken layout. See docs/Backlog.md, "tailwind-next.css has no build-freshness
check and will silently go stale again", and
TAILWIND-NEXT-FRESHNESS-CHECK-IMPLEMENTED.md.

This test rebuilds the stylesheet from a clean tree with the exact recorded
build command and diffs it byte-for-byte against the committed file. It needs
the Tailwind CLI installed in frontend-build/node_modules (`npm ci`, run
there) -- it does NOT fall back to `npx`'s auto-install-from-registry
behaviour, deliberately: that would make the test's pass/fail depend on
network access and on whatever version npm resolves that day, not on the
pinned devDependency in frontend-build/package-lock.json.

Empirically verified (2026-09-28) that the build is byte-for-byte
reproducible: run twice in a row from the same source tree, the two outputs
are identical. No whitespace/formatting normalization is applied to the diff
for that reason -- adding it would hide a real staleness case, not just
build-order noise that doesn't exist here.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_BUILD = PACKAGE_ROOT / "frontend-build"
COMMITTED_CSS = (
    PACKAGE_ROOT / "resource_explorer" / "web" / "static" / "next" / "tailwind-next.css"
)
BUILD_COMMAND = (
    "cd packages/resource-explorer/frontend-build && npx tailwindcss "
    "-c tailwind-next.config.js -i ./input.css "
    "-o ../resource_explorer/web/static/next/tailwind-next.css --minify"
)


def _tailwind_cli_available() -> bool:
    """True only if the pinned CLI is actually installed locally -- not just
    that `npx`/`node` exist, which would let npx silently fetch some other
    version from the registry instead of skipping."""
    if shutil.which("npx") is None:
        return False
    binary = FRONTEND_BUILD / "node_modules" / ".bin" / "tailwindcss"
    return binary.exists()


@pytest.mark.skipif(
    not _tailwind_cli_available(),
    reason=(
        "tailwindcss CLI not installed in frontend-build/node_modules -- run "
        "`npm ci` in packages/resource-explorer/frontend-build first "
        "(needs Node; `npx` alone is not enough, see module docstring)"
    ),
)
def test_tailwind_next_css_is_not_stale(tmp_path):
    """Rebuild tailwind-next.css from the current next/*.js + stages/*.js
    source and fail loudly if it differs from the committed file."""
    rebuilt = tmp_path / "tailwind-next.css"
    result = subprocess.run(
        [
            "npx",
            "tailwindcss",
            "-c",
            "tailwind-next.config.js",
            "-i",
            "./input.css",
            "-o",
            str(rebuilt),
            "--minify",
        ],
        cwd=FRONTEND_BUILD,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"tailwind build itself failed (exit {result.returncode}):\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    rebuilt_css = rebuilt.read_text(encoding="utf-8")
    committed_css = COMMITTED_CSS.read_text(encoding="utf-8")

    if rebuilt_css != committed_css:
        # A minified single-line file -- a naive full-text diff is useless,
        # so report the first point of divergence with enough surrounding
        # context to spot the missing/changed class.
        first_diff = next(
            (
                i
                for i in range(min(len(rebuilt_css), len(committed_css)))
                if rebuilt_css[i] != committed_css[i]
            ),
            min(len(rebuilt_css), len(committed_css)),
        )
        window = 120
        lo = max(0, first_diff - 40)
        pytest.fail(
            "tailwind-next.css is stale -- rebuild it with:\n"
            f"  {BUILD_COMMAND}\n\n"
            f"rebuilt length={len(rebuilt_css)} committed length={len(committed_css)}, "
            f"first difference at byte {first_diff}:\n"
            f"  rebuilt:   ...{rebuilt_css[lo:first_diff + window]}...\n"
            f"  committed: ...{committed_css[lo:first_diff + window]}..."
        )
