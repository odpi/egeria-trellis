"""PI-084: GITHUB_BASE_URL is the single source for the GitHub API base URL.

Discovery used to honour a runtime app_settings override that surveys and
ingestion ignored. These tests pin that discovery and a survey now resolve the
same base URL, and that the stored override is no longer read.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from resource_explorer.workflows.discovery import (
    RepoSearchCriteria,
    fetch_list_urls,
    run_search_query,
)

CONFIG_URL = "https://ghe.config.example.com/api/v3"
STALE_OVERRIDE = "https://stale-override.example.com/api/v3"


class _Registry:
    """Registry stand-in that still carries an old stored override."""

    def get_setting(self, key):
        return STALE_OVERRIDE if key == "github_base_url" else None


def _cfg():
    return SimpleNamespace(github=SimpleNamespace(token="", base_url=CONFIG_URL))


def _bases(mock_github):
    return [c.kwargs["base_url"] for c in mock_github.call_args_list]


def test_discovery_search_uses_config_base_url_not_stored_override():
    with patch("resource_explorer.github.client.get_config", _cfg), \
         patch("resource_explorer.github.client.Github") as gh:
        gh.return_value.search_repositories.return_value = []
        run_search_query(RepoSearchCriteria(keyword="x"), _Registry())
    assert _bases(gh) == [CONFIG_URL]


def test_discovery_list_source_uses_config_base_url_not_stored_override():
    with patch("resource_explorer.github.client.get_config", _cfg), \
         patch("resource_explorer.github.client.Github") as gh:
        fetch_list_urls([], _Registry())
    assert _bases(gh) == [CONFIG_URL]


def test_survey_checkout_resolves_the_same_base_url_as_discovery():
    from resource_explorer.surveyors.repo_survey_definition_adapter import (
        _acquire_zipball_root,
    )

    with patch("resource_explorer.github.client.get_config", _cfg), \
         patch("resource_explorer.github.client.Github") as gh:
        gh.return_value.search_repositories.return_value = []
        run_search_query(RepoSearchCriteria(keyword="x"), _Registry())
        project = SimpleNamespace(github_url="https://github.com/o/r")
        with patch(
            "resource_explorer.github.client.GitHubClient.get_repo", return_value=MagicMock(),
        ), patch(
            "resource_explorer.github.client.GitHubClient.zipball_root",
            return_value=MagicMock(),
        ):
            with _acquire_zipball_root(project, _Registry()):
                pass
    bases = _bases(gh)
    assert len(bases) == 2 and set(bases) == {CONFIG_URL}


def test_override_routes_are_gone():
    from resource_explorer.web.routes import discovery

    paths = {getattr(r, "path", "") for r in discovery.router.routes}
    assert not any(p.endswith("/github-base-url") for p in paths)
