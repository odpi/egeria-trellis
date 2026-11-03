"""Fetches GitHub statistics and writes them to the project_stats time-series table."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from resource_explorer.github.client import GitHubClient
from resource_explorer.registry import ProjectRegistry

_COMMIT_LOOKBACK_DAYS_DEFAULT = 90


def _percentile(values: list[int], p: int) -> float:
    """Return the p-th percentile of a sorted integer list (linear interpolation)."""
    if not values:
        return 0.0
    sv = sorted(values)
    idx = (len(sv) - 1) * p / 100
    lo, hi = int(idx), min(int(idx) + 1, len(sv) - 1)
    return sv[lo] + (sv[hi] - sv[lo]) * (idx - lo)

# Rough bytes-per-line by language for LOC estimation
_BYTES_PER_LINE: dict[str, int] = {
    "python": 45, "ruby": 42, "go": 45, "javascript": 40, "typescript": 45,
    "java": 52, "c#": 50, "c++": 50, "c": 48, "rust": 50, "kotlin": 48,
    "swift": 46, "shell": 38, "bash": 38, "scala": 50, "r": 40,
    "jupyter notebook": 80, "html": 55, "css": 35, "yaml": 30, "json": 35,
}


class StatsFetcher:
    """
    Fetches project statistics from GitHub API and persists them to SQLite.
    Called on initial add and during scheduled refresh.

    Metrics collected:
    - stars, forks, watchers, open_issues
    - contributors_count
    - commits in last 30 and 90 days
    - release count + latest release + avg release interval
    - primary language + language breakdown (bytes)
    - lines_of_code (estimated from language bytes)
    - file_count (from git tree traversal)
    - repo_size_kb, license (human-readable name), license_spdx_id, topics
    - repo_created_at, last_pushed_at
    - lifecycle/config flags: archived, disabled, is_fork, is_template,
      default_branch, has_issues/wiki/discussions/projects/pages,
      network_count, subscribers_count, visibility, is_private, homepage,
      mirror_url, parent_full_name, allow_*_merge/delete_branch_on_merge
      (all free — same `repo` object, zero extra API calls)
    - security_and_analysis (7 feature-toggle states, e.g. secret scanning)
    - environments + latest deployment (2 extra API calls; best-effort —
      404s for repos without deploy/environment access, common)
    """

    def __init__(self) -> None:
        self.client = GitHubClient()
        self.registry = ProjectRegistry()

    def fetch(
        self, resource_slug: str, lookback_days: int = _COMMIT_LOOKBACK_DAYS_DEFAULT,
        fetch_diff_stats: bool = True,
    ) -> dict:
        project = self.registry.get(resource_slug)
        if not project:
            raise ValueError(f"Project '{resource_slug}' not found")

        slug = project.slug  # always use normalized slug for DB writes

        repo = self.client.get_repo(project.github_url)
        now = datetime.utcnow()

        releases = list(repo.get_releases())
        license_name, license_spdx_id = self._license_info(repo)

        stats = {
            "project_slug": slug,
            "fetched_at": now.isoformat(),
            "stars": repo.stargazers_count,
            "forks": repo.forks_count,
            "watchers": repo.watchers_count,
            "open_issues": repo.open_issues_count,
            "contributors_count": repo.get_contributors().totalCount,
            "commits_30d": self._count_commits(repo, days=30),
            "commits_90d": self._count_commits(repo, days=90),
            "commits_365d": self._count_commits(repo, days=365) if lookback_days >= 365 else None,
            "releases_count": len(releases),
            "latest_release": self._latest_release_tag(releases),
            "latest_release_at": self._latest_release_date(releases),
            "avg_release_interval_days": self._avg_release_interval(releases),
            "primary_language": repo.language or "",
            "language_breakdown": self._language_breakdown(repo),
            "lines_of_code": self._estimate_loc(repo),
            "file_count": self._count_files(repo),
            "repo_size_kb": repo.size,
            "license": license_name,
            "license_spdx_id": license_spdx_id,
            "topics": ",".join(repo.get_topics()),
            "repo_created_at": repo.created_at.isoformat() if repo.created_at else "",
            "last_pushed_at": repo.pushed_at.isoformat() if repo.pushed_at else "",
            "archived": int(bool(repo.archived)),
            "disabled": int(bool(repo.disabled)),
            "is_fork": int(bool(repo.fork)),
            "is_template": int(bool(repo.is_template)),
            "default_branch": repo.default_branch or "",
            "has_issues": int(bool(repo.has_issues)),
            "has_wiki": int(bool(repo.has_wiki)),
            "has_discussions": int(bool(repo.has_discussions)),
            "has_projects": int(bool(repo.has_projects)),
            "has_pages": int(bool(repo.has_pages)),
            "network_count": repo.network_count or 0,
            "subscribers_count": repo.subscribers_count or 0,
            "visibility": repo.visibility or "public",
            "is_private": int(bool(repo.private)),
            "homepage": repo.homepage or "",
            "mirror_url": repo.mirror_url or "",
            "parent_full_name": self._parent_full_name(repo),
            "allow_merge_commit": int(bool(repo.allow_merge_commit)),
            "allow_squash_merge": int(bool(repo.allow_squash_merge)),
            "allow_rebase_merge": int(bool(repo.allow_rebase_merge)),
            "allow_auto_merge": int(bool(repo.allow_auto_merge)),
            "allow_update_branch": int(bool(repo.allow_update_branch)),
            "delete_branch_on_merge": int(bool(repo.delete_branch_on_merge)),
            "security_and_analysis_json": self._security_and_analysis(repo),
        }
        deployment_info = self._latest_deployment(repo)
        stats.update({
            "environments_json": self._environments(repo),
            "deployments_count": deployment_info["count"],
            "latest_deployment_at": deployment_info["at"],
            "latest_deployment_environment": deployment_info["environment"],
            "latest_deployment_ref": deployment_info["ref"],
        })

        # Was a raw sqlite3 connection opened directly against
        # self.registry.db_path — a leftover from before the registry's
        # Postgres cutover. That wrote into an orphaned
        # local SQLite file nobody reads (registry._conn() routes to Postgres
        # now), so every "refresh stats" call silently no-op'd from the UI's
        # point of view — confirmed live: last_pushed_at stayed stale no
        # matter how many times fetch() ran. registry._conn() routes to
        # whichever backend is actually configured, same as every other
        # registry write.
        with self.registry._conn() as conn:
            conn.execute("""
                INSERT INTO project_stats
                (project_slug, fetched_at, stars, forks, watchers, open_issues,
                 contributors_count, commits_30d, commits_90d, commits_365d, releases_count,
                 latest_release, latest_release_at, avg_release_interval_days,
                 primary_language, language_breakdown, lines_of_code, file_count,
                 repo_size_kb, license, license_spdx_id, topics, repo_created_at, last_pushed_at,
                 archived, disabled, is_fork, is_template, default_branch,
                 has_issues, has_wiki, has_discussions, has_projects, has_pages,
                 network_count, subscribers_count, visibility, is_private,
                 homepage, mirror_url, parent_full_name,
                 allow_merge_commit, allow_squash_merge, allow_rebase_merge,
                 allow_auto_merge, allow_update_branch, delete_branch_on_merge,
                 security_and_analysis_json, environments_json, deployments_count,
                 latest_deployment_at, latest_deployment_environment, latest_deployment_ref)
                VALUES (:project_slug, :fetched_at, :stars, :forks, :watchers, :open_issues,
                        :contributors_count, :commits_30d, :commits_90d, :commits_365d, :releases_count,
                        :latest_release, :latest_release_at, :avg_release_interval_days,
                        :primary_language, :language_breakdown, :lines_of_code, :file_count,
                        :repo_size_kb, :license, :license_spdx_id, :topics, :repo_created_at, :last_pushed_at,
                        :archived, :disabled, :is_fork, :is_template, :default_branch,
                        :has_issues, :has_wiki, :has_discussions, :has_projects, :has_pages,
                        :network_count, :subscribers_count, :visibility, :is_private,
                        :homepage, :mirror_url, :parent_full_name,
                        :allow_merge_commit, :allow_squash_merge, :allow_rebase_merge,
                        :allow_auto_merge, :allow_update_branch, :delete_branch_on_merge,
                        :security_and_analysis_json, :environments_json, :deployments_count,
                        :latest_deployment_at, :latest_deployment_environment, :latest_deployment_ref)
            """, stats)
        try:
            count = self._fetch_commits(
                slug, repo, lookback_days=lookback_days, fetch_diff_stats=fetch_diff_stats,
            )
            stats["commits_fetched"] = count
        except Exception as exc:
            stats["commits_fetch_error"] = str(exc)
        # The two contributor lists (12 months and all time, REVIEW-QUESTIONS-AND-ANALYTICS-2026-10-22.md, addendum 1): their own calls, counted
        # and capped (contributors.MAX_CALLS per list), each stored whole or not at all.
        try:
            from resource_explorer import contributors

            stats["contributors_fetch"] = contributors.fetch_contributors(
                self.registry, self.client, slug, repo.full_name)
        except Exception as exc:
            stats["contributors_fetch_error"] = str(exc)
        return stats

    def _fetch_commits(
        self, resource_slug: str, repo, lookback_days: int = _COMMIT_LOOKBACK_DAYS_DEFAULT,
        fetch_diff_stats: bool = True,
    ) -> int:
        """
        Fetch recent commits, store per-commit additions/deletions, and compute contributor stats.
        Returns row count processed. additions/deletions require one extra API call per new commit;
        stops fetching them gracefully if the rate limit is hit or quota is low.

        fetch_diff_stats=False (the Scouting-tier "fast" path — see
        HealthSurveyor.fast) skips the per-commit diff-stats calls entirely,
        regardless of quota — this was a real, confirmed slowness bug: for an
        active repo, "fetch additions/deletions for every commit in the last
        90 days" is easily several hundred sequential API calls with no cap
        on count or elapsed time (only an optimistic "skip if <100 calls
        remain" quota gate), directly contradicting Coarse Scout's whole
        premise of being the fast, cheap tier. Commit SHAs/messages/dates
        (needed for commit counts and contributor tiers) are still fetched —
        those come free from the same paginated commit list, no extra calls.
        """
        since = datetime.utcnow() - timedelta(days=lookback_days)
        commits = repo.get_commits(since=since)  # raises on API failure — caller handles

        # SHAs already stored with non-null additions — skip extra API call for these
        with self.registry._conn() as conn:
            existing_with_stats = {
                row[0] for row in conn.execute(
                    "SELECT sha FROM project_commits WHERE project_slug = ? AND additions IS NOT NULL",
                    (resource_slug,),
                ).fetchall()
            }

        # Pre-check quota: skip diff stats entirely if fewer than 100 calls remain.
        # Each new commit needs one extra REST call; for long histories this depletes the limit fast.
        if fetch_diff_stats:
            try:
                rl = self.client.check_rate_limit()
                if rl["remaining"] < 100:
                    fetch_diff_stats = False
            except Exception:
                pass  # optimistic if rate-limit check itself fails

        rows = []
        diff_calls = 0
        for c in commits:
            commit = c.commit
            author = commit.author
            if author and author.date:
                d = author.date
                if d.tzinfo is not None:
                    d = d.astimezone(timezone.utc).replace(tzinfo=None)
                committed_at = d.isoformat()
            else:
                committed_at = ""
            if not committed_at:
                continue

            additions = deletions = None
            if fetch_diff_stats and c.sha not in existing_with_stats:
                try:
                    additions = c.stats.additions
                    deletions = c.stats.deletions
                    diff_calls += 1
                    # Re-check quota every 50 diff-stat calls to avoid hitting the wall
                    if diff_calls % 50 == 0:
                        try:
                            rl = self.client.check_rate_limit()
                            if rl["remaining"] < 100:
                                fetch_diff_stats = False
                        except Exception:
                            pass
                except Exception as exc:
                    if "rate limit" in str(exc).lower():
                        fetch_diff_stats = False

            rows.append((
                resource_slug,
                c.sha,
                (commit.message or "").split("\n")[0][:200],
                author.name if author else "",
                author.email if author else "",
                committed_at,
                additions,
                deletions,
            ))

        if not rows:
            return 0

        # Use ON CONFLICT DO UPDATE so additions/deletions get backfilled for rows already stored
        with self.registry._conn() as conn:
            conn.executemany(
                """INSERT INTO project_commits
                   (project_slug, sha, message, author_name, author_email, committed_at,
                    additions, deletions)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(project_slug, sha) DO UPDATE SET
                     additions = COALESCE(excluded.additions, project_commits.additions),
                     deletions = COALESCE(excluded.deletions, project_commits.deletions)""",
                rows,
            )

        self._compute_contributor_stats(resource_slug, lookback_days=lookback_days)
        return len(rows)

    def _compute_contributor_stats(self, resource_slug: str, lookback_days: int = _COMMIT_LOOKBACK_DAYS_DEFAULT) -> None:
        """
        Aggregate per-author commits/additions/deletions for 30d, 90d (and 365d when data covers it)
        windows and classify each contributor into a tier (core / regular / occasional).
        Tiers are relative to the project's own distribution, not a global threshold.
        """
        now = datetime.utcnow()
        windows = (30, 90, 365) if lookback_days >= 365 else (30, 90)
        for days in windows:
            cutoff = (now - timedelta(days=days)).isoformat()
            period_start = (now - timedelta(days=days)).date().isoformat()
            period_end = now.date().isoformat()

            with self.registry._conn() as conn:
                raw = conn.execute(
                    """SELECT author_email, author_name,
                              COUNT(*) AS commits,
                              COALESCE(SUM(additions), 0) AS additions,
                              COALESCE(SUM(deletions), 0) AS deletions
                       FROM project_commits
                       WHERE project_slug = ? AND committed_at >= ?
                       GROUP BY author_email, author_name
                       ORDER BY commits DESC""",
                    (resource_slug, cutoff),
                ).fetchall()

            if not raw:
                continue

            commit_counts = [r[2] for r in raw]
            p75 = _percentile(commit_counts, 75)
            p25 = _percentile(commit_counts, 25)

            stat_rows = []
            # registry.py's RowWrapper.__iter__ yields column *names* (dict-
            # like iteration convention, `for key in row`) — unpacking a row
            # directly (`for a, b, c in raw`) silently binds the 5 column
            # name strings instead of values, not the values themselves.
            # Real, pre-existing, previously-undetected bug: this method was
            # never exercised by any test with real iterable commit data
            # (StatsFetcher's own test suite always hit a non-iterable
            # MagicMock for repo.get_commits(), silently swallowed by
            # fetch()'s broad except) — in production this has been raising
            # a TypeError on every real commit-stats fetch, caught by that
            # same broad except and surfaced only as an opaque
            # "commits_fetch_error" string, contributor tiers never actually
            # computed. .values() is the actual fix.
            for email, name, commits, additions, deletions in (r.values() for r in raw):
                if commits >= p75:
                    tier = "core"
                elif commits >= p25:
                    tier = "regular"
                else:
                    tier = "occasional"
                stat_rows.append({
                    "period_start": period_start,
                    "period_end": period_end,
                    "author_email": email or "",
                    "author_name": name or "",
                    "commits": commits,
                    "additions": additions,
                    "deletions": deletions,
                    "tier": tier,
                })
            self.registry.upsert_contributor_stats(resource_slug, stat_rows)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _count_commits(self, repo, days: int) -> int:
        since = datetime.utcnow() - timedelta(days=days)
        return repo.get_commits(since=since).totalCount

    def _latest_release_tag(self, releases: list) -> str:
        return releases[0].tag_name if releases else ""

    def _latest_release_date(self, releases: list) -> str:
        if releases and releases[0].published_at:
            return releases[0].published_at.isoformat()
        return ""

    def _avg_release_interval(self, releases: list) -> int:
        """Average days between releases (0 if fewer than 2 releases)."""
        dated = [r.published_at for r in releases if r.published_at]
        if len(dated) < 2:
            return 0
        dated.sort(reverse=True)
        gaps = [(dated[i] - dated[i + 1]).days for i in range(len(dated) - 1)]
        return round(sum(gaps) / len(gaps))

    def _language_breakdown(self, repo) -> str:
        langs = {k: v for k, v in repo.get_languages().items() if isinstance(v, int)}
        parts = [f"{lang}: {bytes_:,} bytes"
                 for lang, bytes_ in sorted(langs.items(), key=lambda x: -x[1])]
        return "; ".join(parts)

    def _estimate_loc(self, repo) -> int:
        """Estimate lines of code from language byte counts."""
        langs = {k: v for k, v in repo.get_languages().items() if isinstance(v, int)}
        total = 0
        for lang, bytes_ in langs.items():
            bpl = _BYTES_PER_LINE.get(lang.lower(), 45)
            total += bytes_ // bpl
        return total

    def _count_files(self, repo) -> int:
        import logging
        log = logging.getLogger(__name__)
        try:
            tree = repo.get_git_tree(repo.default_branch, recursive=True)
            if not tree.truncated:
                return sum(1 for e in tree.tree if e.type == "blob")
            # GitHub truncates recursive trees for large repos (>100k nodes).
            # The truncated response is cut off mid-traversal so its entry list is incomplete.
            # Fetch the root non-recursively instead — that is never truncated because the
            # root has far fewer than 100k direct children — then walk each subtree.
            log.debug("Git tree truncated for %s, walking subdirectories", repo.full_name)
            root = repo.get_git_tree(repo.default_branch, recursive=False)
            return self._count_files_walk(repo, root)
        except Exception as exc:
            log.warning("Failed to count files for %s: %s", repo.full_name, exc)
            return 0

    def _count_files_walk(self, repo, tree) -> int:
        """Recursively count blobs by fetching each subtree entry individually."""
        count = sum(1 for e in tree.tree if e.type == "blob")
        for entry in tree.tree:
            if entry.type != "tree":
                continue
            try:
                subtree = repo.get_git_tree(entry.sha, recursive=True)
                if not subtree.truncated:
                    count += sum(1 for e in subtree.tree if e.type == "blob")
                else:
                    count += self._count_files_walk(repo, subtree)
            except Exception:
                pass
        return count

    def _license_info(self, repo) -> tuple[str, str]:
        """(name, spdx_id) — one repo.get_license() call feeding both the
        existing human-readable `license` column and the new
        `license_spdx_id` column (LicenseClassifierSurveyor, Assessment
        expansion plan B1). PyGitHub's License object carries both on the
        same object, so capturing spdx_id alongside the name that was
        already being fetched is genuinely free — no second API call."""
        try:
            lic = repo.get_license()
            if lic and lic.license:
                return lic.license.name or "", lic.license.spdx_id or ""
        except Exception:
            pass
        return "", ""

    _SECURITY_FEATURE_NAMES = (
        "advanced_security", "dependabot_security_updates", "secret_scanning",
        "secret_scanning_ai_detection", "secret_scanning_non_provider_patterns",
        "secret_scanning_push_protection", "secret_scanning_validity_checks",
    )

    def _security_and_analysis(self, repo) -> str:
        """JSON dict of GitHub's 7 security/analysis feature toggles'
        *configuration* state (e.g. "enabled"/"disabled") — not findings,
        just whether the feature is on. Best-effort: GitHub only populates
        this for repos you have admin access to; empty for everything
        else, which is most repos being scouted."""
        try:
            sa = repo.security_and_analysis
            if sa is None:
                return "{}"
            features = {}
            for name in self._SECURITY_FEATURE_NAMES:
                feature = getattr(sa, name, None)
                features[name] = feature.status if feature else None
            return json.dumps(features)
        except Exception:
            return "{}"

    def _parent_full_name(self, repo) -> str:
        """Fork source, e.g. "torvalds/linux" — "" if not a fork."""
        try:
            return repo.parent.full_name if repo.fork and repo.parent else ""
        except Exception:
            return ""

    def _environments(self, repo) -> str:
        """JSON list of environment names. Best-effort: 404s for repos
        without the Environments feature/API access — common for repos
        you don't administer."""
        try:
            return json.dumps([e.name for e in repo.get_environments()])
        except Exception:
            return "[]"

    def _latest_deployment(self, repo) -> dict:
        """Most recent deployment's timestamp/environment/ref, plus the
        total deployment count — not full deployment history (this is a
        scouting-tier read, not a deployment-audit trail). Same
        best-effort access caveats as _environments()."""
        empty = {"count": 0, "at": "", "environment": "", "ref": ""}
        try:
            deployments = repo.get_deployments()
            count = deployments.totalCount
            if not count:
                return empty
            latest = deployments[0]
            return {
                "count": count,
                "at": latest.updated_at.isoformat() if latest.updated_at else "",
                "environment": latest.environment or "",
                "ref": latest.ref or "",
            }
        except Exception:
            return empty
