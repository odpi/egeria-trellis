"""Which commit of Resource Explorer this process runs (2026-11-13).

The Prefect worker container advertises the commit its image was built from, and RE dispatches a step to it
only when that is RE's own commit (DESIGN-PREFECT-ROLE-AND-RE-WORKER-2026-11-13 §1.6, "version parity"): a
worker on other code is a warning and the local fallback, never silent drift.

Where the answer comes from, in order:
  1. TRELLIS_RE_SHA, which the image sets from its build argument (docker/Dockerfile.resource-explorer);
  2. `git rev-parse HEAD` of the checkout this package is imported from (a host process: 8813, a dev box);
  3. nothing: "" — and then RE claims no parity with any worker that advertises a commit.

A commit names committed code only: uncommitted edits in a checkout are not in it.
"""
from __future__ import annotations

import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path

_HEX = re.compile(r"^[0-9a-f]{7,40}$")

#: How many hex digits name a commit in worker, queue and deployment names.
SHORT = 12


@lru_cache(maxsize=1)
def re_sha() -> str:
    """This process's RE commit, lower-case hex, or "" when it cannot be known."""
    env = (os.environ.get("TRELLIS_RE_SHA") or "").strip().lower()
    if _HEX.match(env):
        return env
    try:
        out = subprocess.run(
            ["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=2, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    sha = out.stdout.strip().lower()
    return sha if out.returncode == 0 and _HEX.match(sha) else ""


def re_sha_short() -> str:
    return re_sha()[:SHORT]
