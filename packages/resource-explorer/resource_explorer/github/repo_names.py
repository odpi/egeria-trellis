"""One derivation of a repository's published displayName: `owner/repo`, from its URL (TR-116).

Before this, a SourceControlLibrary was named from the registry's display name, which depends on how
the repository was registered: the onboarding wizard stores the bare repo name ("egeria-python"),
the discovery and org-import paths store GitHub's `full_name` ("open-policy-agent/opa"). The
same kind of repository got two shapes of name. The URL is the one thing every path agrees on.
"""
from __future__ import annotations

import re

# https://host/owner/repo[.git][/]   ssh://git@host/owner/repo[.git]   git@host:owner/repo[.git]
_PATH = re.compile(r"^(?:[a-z][a-z0-9+.-]*://(?:[^/@]+@)?[^/]+/|[^/@\s]+@[^:/\s]+:)(?P<path>.+)$", re.I)


def repo_display_name(github_url: str) -> str | None:
    """`owner/repo` from a repository URL, `.git` and trailing slashes removed; None when the URL has
    no owner and repo (the caller decides what to fall back to; nothing is guessed here)."""
    m = _PATH.match((github_url or "").strip())
    if not m:
        return None
    parts = [p for p in m.group("path").split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1].removesuffix(".git")
    return f"{owner}/{repo}" if owner and repo else None
