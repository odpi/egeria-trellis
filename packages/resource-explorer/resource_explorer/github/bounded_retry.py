"""TR-62a: no GitHub call sleeps past a bound.

What hedwig hit (2026-11-18, repo_file_inventory idle ~21 min, GitHub socket in CLOSE-WAIT): PyGitHub 2.9.1's
default `GithubRetry` answers a rate-limited 403 by sleeping until X-RateLimit-Reset. Reproduced against a fake
GitHub with a reset an hour away: `Github(None, base_url=..., per_page=100)` (GitHubClient's construction at
7d3d1eba) asked for a 3600 s sleep on the first `get_repo`, and again on the next of its 10 attempts. It reads
nothing while it sleeps, so the pooled connection GitHub had closed sat in CLOSE-WAIT.

`BoundedGithubRetry` keeps PyGitHub's retry decisions (which 403s are rate limits, Retry-After, secondary limits)
but not its patience: any wait longer than `max_wait` raises `GitHubRateLimitWait` at once, with words that name the
reset time in UTC and whether a token is set (a boolean, never any part of it). A shorter wait is taken under the
heartbeat phase "waiting for GitHub rate limit", so the step's heartbeat says what it is doing.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from github.GithubRetry import GithubRetry

#: Attempts in all; PyGitHub's default is 10, each of which could sleep until the reset.
TOTAL_ATTEMPTS = 3


class GitHubRateLimitWait(RuntimeError):
    """GitHub asked us to wait longer than the step may. The step fails now, saying when the limit resets."""

    def __init__(self, wait_seconds: float, *, reset_at: datetime | None, token_set: bool, max_wait: float) -> None:
        self.wait_seconds = wait_seconds
        self.reset_at = reset_at
        self.token_set = token_set
        when = (f"resets at {reset_at.strftime('%H:%M')} UTC" if reset_at
                else f"asked to wait {int(wait_seconds)} s")
        super().__init__(
            f"GitHub rate limit reached · {when} · GITHUB_TOKEN set: {'yes' if token_set else 'no'} · "
            f"not waiting longer than {int(max_wait)} s")


def _reset_at(response: Any) -> datetime | None:
    try:
        value = (getattr(response, "headers", None) or {}).get("X-RateLimit-Reset")
        return datetime.fromtimestamp(int(value), timezone.utc) if value else None
    except (TypeError, ValueError):
        return None


class BoundedGithubRetry(GithubRetry):
    def __init__(self, *, max_wait: float = 60.0, token_set: bool = False, **kwargs: Any) -> None:
        self.max_wait = float(max_wait)
        self.token_set = bool(token_set)
        kwargs.setdefault("total", TOTAL_ATTEMPTS)
        super().__init__(**kwargs)

    def new(self, **kw: Any):
        kw.update(max_wait=self.max_wait, token_set=self.token_set)
        return super().new(**kw)

    def sleep(self, response: Any = None) -> None:  # urllib3 Retry.sleep: called before every retry
        wait = None
        if self.respect_retry_after_header and response is not None:
            wait = self.get_retry_after(response)
        if wait is None:
            wait = self.get_backoff_time()
        wait = float(wait or 0.0)
        if wait > self.max_wait:
            raise GitHubRateLimitWait(wait, reset_at=_reset_at(response), token_set=self.token_set,
                                      max_wait=self.max_wait)
        if wait <= 0:
            return
        from resource_explorer.surveyors import step_heartbeat

        with step_heartbeat.phase("waiting for GitHub rate limit"):
            time.sleep(wait)


def bounded_kwargs(token: str | None) -> dict[str, Any]:
    """The `timeout` and `retry` every Github(...) on a step's path is built with (TR-62a): an explicit per-request
    timeout and a retry that never sleeps past `rate_limit_max_wait_seconds`. Passed to each module's own
    `Github(...)` call, so that name stays the one construction site its tests patch. The contributor reads'
    `_gh_once` keeps retry=None and takes only the timeout."""
    from resource_explorer.config import get_config

    cfg = get_config().github
    return {"timeout": int(cfg.request_timeout_seconds),
            "retry": BoundedGithubRetry(max_wait=cfg.rate_limit_max_wait_seconds, token_set=bool(token))}
