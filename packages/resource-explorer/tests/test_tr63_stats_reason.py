"""TR-63: a survey without GITHUB_TOKEN of a 59-commit repo exited 0 with "Stats fetched (0 commits stored)" and no reason.

Three silences in the onboarding wizard (`_post_ingestion`) and the same one in `refresh`:
  * `result.get("commits_fetched", 0)` printed 0 for a key that was missing, as if 0 were what GitHub answered;
  * the latest-SHA fetch was `except Exception: pass`, so an incremental refresh quietly had no baseline;
  * the retry advice named `project-explorer refresh`, a command that no longer exists.
The line now says why (no GITHUB_TOKEN: GitHub allows 60 requests an hour; or the fetch error), says "not fetched" when
there is no count, names the SHA failure, and names `resource-explorer refresh <slug>`.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from resource_explorer.cli.wizard import OnboardingWizard
from resource_explorer.github import stats_summary as ss


def _wizard():
    w = OnboardingWizard.__new__(OnboardingWizard)
    w.console = Console(record=True, width=200, force_terminal=False)
    w.registry = MagicMock()
    return w


def _run(result, *, token: str, sha_error: Exception | None = None, fetch_error: Exception | None = None) -> str:
    w = _wizard()
    client = MagicMock()
    if sha_error:
        client.get_latest_commit_sha.side_effect = sha_error
    fetcher = MagicMock()
    if fetch_error:
        fetcher.fetch.side_effect = fetch_error
    else:
        fetcher.fetch.return_value = result
    with patch("resource_explorer.github.client.GitHubClient", return_value=client), \
         patch("resource_explorer.github.stats_fetcher.StatsFetcher", return_value=fetcher), \
         patch.object(ss, "token_is_set", return_value=bool(token)):
        w._post_ingestion("myproj", "https://github.com/o/r")
    return w.console.export_text()


def test_zero_commits_without_a_token_says_why():
    out = _run({"commits_fetched": 0}, token="")
    assert "GITHUB_TOKEN" in out and "60" in out
    assert "0 commits" in out


def test_a_missing_count_is_not_shown_as_zero():
    out = _run({}, token="tok")
    assert "0 commits" not in out
    assert "not fetched" in out.lower() or "no commit count" in out.lower()


def test_a_missing_count_without_a_token_names_the_token():
    out = _run({}, token="")
    assert "GITHUB_TOKEN" in out and "0 commits" not in out


def test_a_commit_fetch_error_is_shown_with_the_token_hint_when_there_is_none():
    out = _run({"commits_fetch_error": "403 rate limit exceeded"}, token="")
    assert "403 rate limit exceeded" in out and "GITHUB_TOKEN" in out


def test_a_count_with_a_token_is_plain():
    out = _run({"commits_fetched": 59}, token="tok")
    assert "59 commits" in out and "GITHUB_TOKEN" not in out


def test_a_count_without_a_token_still_names_the_limit():
    out = _run({"commits_fetched": 59}, token="")
    assert "59 commits" in out and "GITHUB_TOKEN" in out


def test_the_sha_failure_is_not_swallowed():
    out = _run({"commits_fetched": 3}, token="tok", sha_error=RuntimeError("API rate limit exceeded"))
    assert "API rate limit exceeded" in out and "baseline" in out.lower()


def test_the_retry_advice_names_the_current_command():
    out = _run({"commits_fetch_error": "boom"}, token="tok")
    assert "resource-explorer refresh myproj" in out
    assert "project-explorer" not in out


def test_a_stats_exception_is_shown():
    out = _run({}, token="tok", fetch_error=RuntimeError("repo not found"))
    assert "repo not found" in out


def test_the_summary_line_is_shared_by_refresh():
    style, text = ss.summarize({"commits_fetched": 0}, token_set=False, lookback_days=90)
    assert style == "yellow" and "GITHUB_TOKEN" in text and "90d" in text
    style, text = ss.summarize({"commits_fetched": 12}, token_set=True, lookback_days=90)
    assert style == "dim" and "12 commits" in text and "90d" in text
