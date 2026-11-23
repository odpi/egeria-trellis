# -*- coding: utf-8 -*-
"""Whether RE has a GITHUB_TOKEN, said where it matters (TR-53).

Without a token GitHub allows 60 API requests an hour; one survey of a modest repository spends them in its first calls,
so the commit history, diff stats and contributor lists come back empty or partial. `GitHubConfig.token` defaults to "",
and nothing said so. This reports it: a WARNING at web and worker start-up and one yellow line at the start of the CLI
commands that call GitHub. Not in /health/ready: that is the unauthenticated readiness probe, and a configuration fact
about GitHub is not readiness. The token's VALUE never appears anywhere here.
"""
from __future__ import annotations

ANONYMOUS_REQUESTS_PER_HOUR = 60

_WARNED: set[str] = set()


def _config():
    from resource_explorer.config import get_config

    return get_config().github


def token_is_set() -> bool:
    return bool(_config().token)


def status() -> dict:
    """{token_set, requests_per_hour, words}: booleans and numbers only, never the token."""
    cfg = _config()
    if cfg.token:
        return {"token_set": True, "requests_per_hour": int(getattr(cfg, "requests_per_hour", 5000) or 5000),
                "words": "GITHUB_TOKEN is set"}
    return {"token_set": False, "requests_per_hour": ANONYMOUS_REQUESTS_PER_HOUR,
            "words": (f"GITHUB_TOKEN is not set: GitHub allows {ANONYMOUS_REQUESTS_PER_HOUR} requests an hour without "
                      f"one, which a survey of a modest repository spends in its first calls, so commit history, diff "
                      f"stats and contributors come back empty or partial. Set GITHUB_TOKEN (a read-scope token) in "
                      f"RE's .env.")}


def startup_line() -> str:
    st = status()
    return st["words"] if not st["token_set"] else f"GITHUB_TOKEN is set ({st['requests_per_hour']} requests an hour)"


def log_at_startup(log) -> None:
    """WARNING without a token, INFO with one. Called once by the web lifespan and the worker."""
    if token_is_set():
        log.info("%s", startup_line())
    else:
        log.warning("%s", startup_line())


def warn_on_console(console) -> bool:
    """Print the warning once per process, in yellow, when there is no token. True when it printed."""
    if token_is_set() or "console" in _WARNED:
        return False
    _WARNED.add("console")
    console.print(f"[yellow]Warning:[/yellow] {status()['words']}")
    return True
