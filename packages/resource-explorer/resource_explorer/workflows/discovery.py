"""GitHub discovery — search, list-load, org expansion, disposition, sources.

Moved out of `web/routes/discovery.py` in step 2b. The plan (§3) names this as
one of three things that were "web-only today": there was no core module for
discovery at all, so a `resource-explorer discovery search` was impossible even
though nothing about searching GitHub needs a web server.

Two changes of shape were unavoidable in the move, and both are deliberate:

* **`async` became sync.** The route helpers were `async def` and used
  `asyncio.to_thread` to get the blocking PyGithub calls off the event loop.
  That is a property of *being called from an event loop*, not of the work, so
  the workflow functions are plain synchronous calls and the routes keep the
  `to_thread` hop. A CLI or a queue worker then does not have to start a loop
  to search GitHub.
* **`HTTPException` became `DiscoveryError`.** A workflow must not know about
  status codes. `DiscoveryError` carries the same distinction the route needed
  — `kind` says whether the problem was the caller's query, GitHub's
  auth/rate-limit, or GitHub being unhappy — and `discovery.py` maps that back
  onto 400/502 exactly as before.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

#: One account can hold thousands of repos; a file of five foundation pages must
#: not become an unbounded fetch. Capped and reported as truncated so a partial
#: expansion is never mistaken for the whole account.
ORG_EXPAND_LIMIT = 100

#: undecided -> tracking/investigating -> {recommended, using, abandoned, ignored}.
#: "ignored" = passed on it early/cheaply, never got past scouting; "abandoned" =
#: went further (investigated, maybe surveyed/analyzed) and then decided against
#: it — same hiding-from-sidebar treatment, but the history reads honestly
#: instead of collapsing both into one word. "recommended" = decided for it,
#: worth pursuing. "using" = a step further — the org is already actively using
#: the resource, or knows of its use elsewhere in the org.
VALID_DISPOSITIONS = {
    "undecided", "tracking", "investigating", "recommended", "using",
    "abandoned", "ignored",
}


class DiscoveryError(Exception):
    """A discovery failure the caller is expected to render.

    `kind` is one of "bad_request" (the query itself is unusable) or "upstream"
    (GitHub refused or could not be reached). The route maps those to 400 and
    502; the CLI prints them; the queue records them on the run row.
    """

    def __init__(self, message: str, kind: str = "bad_request") -> None:
        super().__init__(message)
        self.kind = kind


@dataclass
class RepoSearchCriteria:
    """The structured filters, provider-agnostic.

    Kept structured rather than as a query string so a future GitLab client can
    take the same filters and build its own query shape (D4).
    """

    keyword: str = ""
    min_stars: int = 0
    language: str = ""
    license: str = ""
    pushed_after: str = ""  # "YYYY-MM-DD" — GitHub search's pushed:>DATE qualifier
    org: str = ""
    topic: str = ""
    sort: str = "stars"     # stars | forks | updated
    limit: int = 100
    # The old org-only discovery flow (GitHubClient.list_org_repos(), retired)
    # excluded forks and archived repos by default — that default was lost when
    # it generalized into this general search; restored explicitly here rather
    # than relying on GitHub's own (inconsistent) API defaults.
    include_archived: bool = False
    include_forks: bool = False


@dataclass
class EnrichedRepo:
    """A discovered repo plus the local context that decides what to do with it."""

    full_name: str
    html_url: str
    description: str = ""
    stars: int = 0
    language: str = ""
    license: str = ""
    forks: int = 0
    already_registered: bool = False
    # Surfaced from repo_dispositions regardless of already_registered — a
    # previously-ignored candidate should say so even if it was never imported
    # (D10, the actual "remind them what was decided" ask).
    disposition: str = "undecided"
    disposition_reason: str = ""
    disposition_decided_at: str = ""


@dataclass
class OrgExpansion:
    org: str
    urls: list[str] = field(default_factory=list)
    truncated: bool = False
    error: str = ""


def build_query(criteria: RepoSearchCriteria) -> str:
    """Translate structured filter fields into GitHub's qualifier-string query
    language, server-side — keeps the frontend/API contract provider-agnostic."""
    parts = []
    if criteria.keyword:
        parts.append(criteria.keyword)
    if criteria.min_stars:
        parts.append(f"stars:>={criteria.min_stars}")
    if criteria.language:
        parts.append(f"language:{criteria.language}")
    if criteria.license:
        parts.append(f"license:{criteria.license}")
    if criteria.pushed_after:
        parts.append(f"pushed:>{criteria.pushed_after}")
    if criteria.org:
        parts.append(f"org:{criteria.org}")
    if criteria.topic:
        parts.append(f"topic:{criteria.topic}")
    if not parts:
        # Checked before the archived:false/fork:false defaults below are
        # appended — those aren't user-provided filters, so a request with
        # nothing else would otherwise silently produce a valid-looking query
        # and match "everything not archived and not a fork."
        raise DiscoveryError("At least one search filter is required.")
    if not criteria.include_archived:
        parts.append("archived:false")
    if not criteria.include_forks:
        parts.append("fork:false")
    return " ".join(parts)


def enrich_repos(repos: list[dict], registry) -> list[EnrichedRepo]:
    """Shared tail for every discovery path (ad-hoc search, a saved 'search'
    source, or a saved 'list' source) — attaches already_registered/disposition
    context to each raw repo dict."""
    out = []
    for r in repos:
        disp = registry.get_disposition(r["html_url"]) or {}
        out.append(EnrichedRepo(
            full_name=r["full_name"], html_url=r["html_url"],
            description=r["description"], stars=r["stars"], language=r["language"],
            license=r.get("license", ""), forks=r.get("forks", 0),
            already_registered=registry.get_by_github_url(r["html_url"]) is not None,
            disposition=disp.get("disposition", "undecided"),
            disposition_reason=disp.get("reason", ""),
            disposition_decided_at=disp.get("decided_at", ""),
        ))
    return out


def run_search_query(criteria: RepoSearchCriteria, registry) -> list[dict]:
    """Raw repo dicts for a 'search' source (or the ad-hoc form).

    Synchronous and blocking — see the module docstring. Callers on an event
    loop wrap it in `asyncio.to_thread`.
    """
    from github import GithubException

    from resource_explorer.github.client import GitHubClient

    query = build_query(criteria)

    # GITHUB_BASE_URL (config.py GitHubConfig.base_url) is the single source;
    # GitHubClient() applies it itself, the same as surveys and ingestion.
    try:
        return GitHubClient().search_repos(
            query, sort=criteria.sort, order="desc", limit=criteria.limit,
        )
    except GithubException as exc:
        if exc.status in (401, 403):
            raise DiscoveryError(
                "GitHub authentication/rate-limit error — check GITHUB_TOKEN in .env",
                kind="upstream",
            ) from exc
        if exc.status == 422:
            raise DiscoveryError(f"Invalid search query: {query}") from exc
        raise DiscoveryError(f"GitHub API error: {exc}", kind="upstream") from exc


def fetch_list_urls(urls: list[str], registry) -> list[dict]:
    """Raw repo dicts for a 'list' source — a manually-curated set of
    github_urls (Eclipse-style many-orgs foundations, or a user's own enterprise
    repos), best-effort enriched via one GitHubClient.get_repo() call per URL.
    A URL that 404s or otherwise fails is skipped, not fatal to the rest of the
    batch ("Scouting workflow redesign" plan, D1)."""
    from resource_explorer.github.client import GitHubClient

    client = GitHubClient()
    out = []
    for url in urls:
        try:
            out.append(client._repo_to_dict(client.get_repo(url)))
        except Exception:
            continue
    return out


def expand_org(org: str, *, limit: int = ORG_EXPAND_LIMIT) -> OrgExpansion:
    """Repo URLs belonging to a GitHub account, newest-activity first.

    Reuses the same search path as the ad-hoc form (`org:X`), so an expanded
    account and a typed org search return the same repos in the same order.
    Returns the truncation flag rather than only the URLs: "I gave you 3 lines
    and got 214 repos" needs an explanation attached to it, and a capped
    expansion must never read as the whole account.
    """
    from resource_explorer.github.client import GitHubClient

    try:
        repos = GitHubClient().search_repos(
            f"org:{org} fork:false archived:false", sort="updated", limit=limit,
        )
    except Exception as exc:
        log.warning("could not expand organisation %s: %s", org, exc)
        return OrgExpansion(org=org, error=str(exc)[:200])
    urls = [r["html_url"] for r in repos]
    return OrgExpansion(org=org, urls=urls, truncated=len(urls) >= limit)


def set_disposition(registry, github_url: str, disposition: str, reason: str = "") -> dict:
    """Record a triage decision about a repo, registered or not."""
    if disposition not in VALID_DISPOSITIONS:
        raise DiscoveryError(
            f"Invalid disposition '{disposition}' — must be one of "
            f"{sorted(VALID_DISPOSITIONS)}"
        )
    project = registry.get_by_github_url(github_url)
    registry.set_disposition(
        github_url, disposition, reason=reason,
        resource_slug=project.slug if project else "",
    )
    return {"status": "ok", "github_url": github_url, "disposition": disposition}


def run_saved_source(registry, source: dict) -> list[dict]:
    """Raw repo dicts for a named discovery source, whichever shape it has.

    A 'search' source replays its stored criteria; a 'list' source fetches its
    stored URLs. The two are one function here because every caller wants "run
    this source", not "run this source if it happens to be a search".
    """
    config = source.get("config") or {}
    if source.get("source_type") != "search":
        return fetch_list_urls(list(config.get("urls") or []), registry)
    criteria = RepoSearchCriteria(**{
        k: v for k, v in config.items()
        if k in RepoSearchCriteria.__dataclass_fields__
    })
    return run_search_query(criteria, registry)
