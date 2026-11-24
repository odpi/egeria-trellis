# -*- coding: utf-8 -*-
"""The one line the CLI prints after fetching a project's GitHub statistics (TR-63).

`StatsFetcher.fetch` returns `commits_fetched` (a count), or `commits_fetch_error` (why not), or neither. The CLI used to
print `result.get("commits_fetched", 0)`, so a missing count read as "0 commits stored", with no reason. Without
GITHUB_TOKEN GitHub allows 60 requests an hour, which a 59-commit repo's survey spends on its first calls, so the
commit history comes back empty or partial. This says so, and says "not fetched" when there is no count.
"""
from __future__ import annotations

from rich.markup import escape

#: Said wherever the missing token may be why the numbers are empty or partial.
TOKEN_HINT = ("no GITHUB_TOKEN is set: GitHub allows 60 requests an hour without one, so commit history and diff stats "
              "can be empty or partial; set GITHUB_TOKEN in RE's .env")


def token_is_set() -> bool:
    from resource_explorer.config import get_config

    return bool(get_config().github.token)


def summarize(result: dict, *, token_set: bool, lookback_days: int | None = None) -> tuple[str, str]:
    """(rich style, text) for one stats fetch. Never a bare 0 for a count that was not read."""
    hint = "" if token_set else f" ({TOKEN_HINT})"
    if "commits_fetch_error" in result:
        return "yellow", f"commit history could not be fetched: {result['commits_fetch_error']}{hint}"
    if result.get("commits_fetched") is None:
        return "yellow", f"commits not fetched: the fetch returned no commit count{hint}"
    n = result["commits_fetched"]
    where = f"{n} commits, {lookback_days}d lookback" if lookback_days else f"{n} commits stored"
    verb = "Stats updated" if lookback_days else "Stats fetched"
    return ("dim" if token_set else "yellow"), f"{verb} ({where}){hint}"


def print_summary(console, result: dict, *, lookback_days: int | None = None, retry: str | None = None,
                  indent: str = "") -> None:
    """Print `summarize(result)` in its style, with the retry command after an error or a missing count."""
    style, text = summarize(result, token_set=token_is_set(), lookback_days=lookback_days)
    console.print(f"{indent}[{style}]{escape(text)}[/{style}]")
    if retry and style == "yellow" and ("could not be fetched" in text or "not fetched" in text):
        console.print(f"{indent}[dim]Run '{retry}' to retry.[/dim]")
