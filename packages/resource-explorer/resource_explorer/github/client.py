"""GitHub API wrapper — PyGitHub for REST, httpx for GraphQL batch queries."""
from __future__ import annotations

import logging
from typing import Any
from functools import cached_property

from github import Github, GithubException
from github.Repository import Repository

from resource_explorer.config import get_config

log = logging.getLogger(__name__)


class GitHubClient:
    """
    Rate-limit-aware GitHub API client.

    Uses PyGitHub (REST) for standard metadata and file tree operations.
    GraphQL available via query() for complex stats queries that would
    otherwise require many REST calls (e.g. commit counts per contributor).
    """

    def __init__(self, base_url: str | None = None) -> None:
        cfg = get_config().github
        self._base_url = base_url or cfg.base_url
        self._gh = Github(cfg.token or None, base_url=self._base_url, per_page=100)

    def search_repos(
        self, query: str, sort: str = "stars", order: str = "desc", limit: int = 100,
    ) -> list[dict]:
        """General repo search via GitHub's search API (Github.search_repositories) —
        replaces the old org-only list_org_repos(); an `org:X` qualifier in `query`
        fully replicates that behavior. Returns the same lightweight-dict shape
        list_org_repos() used, plus `license` and `forks`.

        GitHub's search API has a much tighter rate limit than the core API
        (30 req/min authenticated vs. 5000/hr) and caps at 1000 results per
        query — this fetches one bounded batch (`limit`, default 100) per
        call; callers do their own sort/filter within that batch afterward
        rather than re-querying live-as-you-type.

        Raises GithubException on auth/rate-limit/malformed-query errors —
        same division of responsibility as get_repo(), which also lets these
        propagate for the caller to translate.
        """
        results = self._gh.search_repositories(query=query, sort=sort, order=order)
        out = []
        for repo in results:
            if len(out) >= limit:
                break
            out.append(self._repo_to_dict(repo))
        return out

    @staticmethod
    def _repo_to_dict(repo: Repository) -> dict:
        """Same lightweight-dict shape search_repos() has always returned —
        shared with the "list" discovery-source path (get_repo() per URL),
        so both produce results the frontend/discovery.py can render
        identically regardless of which source type found them."""
        return {
            "full_name": repo.full_name,
            "html_url": repo.html_url,
            "description": repo.description or "",
            "stars": repo.stargazers_count,
            "language": repo.language or "",
            "archived": repo.archived,
            "fork": repo.fork,
            "updated_at": repo.updated_at.isoformat() if repo.updated_at else "",
            "license": (repo.license.spdx_id if repo.license else "") or "",
            "forks": repo.forks_count,
        }

    def get_repo(self, github_url: str) -> Repository:
        slug = self._url_to_slug(github_url)
        if "/" not in slug:
            raise ValueError(
                f"'{github_url}' looks like an organization or user URL, not a repository. "
                f"Please provide a full repo URL, e.g. https://github.com/{slug}/{slug}"
            )
        return self._gh.get_repo(slug)

    def download_zipball(
        self,
        repo: Repository,
        dest_dir: "Path",
        subproject_path: str | None = None,
    ) -> "Path":
        """
        Download entire repo as a single zipball (1 API call) and extract it.
        Returns the extracted repo root (or the subproject subdirectory when
        subproject_path is specified).
        """
        import time
        import zipfile
        from pathlib import Path
        import requests
        from requests.exceptions import ConnectionError, SSLError, Timeout

        zip_path = Path(dest_dir) / "_repo.zip"
        self.fetch_zipball(repo, zip_path)

        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(dest_dir)
        zip_path.unlink(missing_ok=True)

        # GitHub zips have a single top-level dir named "owner-repo-sha"
        subdirs = [d for d in Path(dest_dir).iterdir() if d.is_dir()]
        repo_root = subdirs[0] if subdirs else Path(dest_dir)

        if subproject_path:
            sub = repo_root / subproject_path
            if not sub.is_dir():
                raise ValueError(
                    f"Subproject path '{subproject_path}' does not exist in this repository. "
                    f"Available top-level directories: "
                    f"{[d.name for d in repo_root.iterdir() if d.is_dir()]}"
                )
            return sub

        return repo_root

    def fetch_zipball(self, repo: Repository, zip_path: "Path", ref: str | None = None) -> "Path":
        """Download `repo`'s zipball to `zip_path`, with retries. No extraction.

        Split out of `download_zipball` so `SourceCache` can fill an entry with
        the artifact itself rather than downloading and then re-zipping, and so
        the retry/SSL-diagnostics logic has exactly one implementation.
        """
        import time
        from pathlib import Path

        import requests
        from requests.exceptions import ConnectionError, SSLError, Timeout

        cfg = get_config().github
        # `ref` is a commit SHA when the caller has one (zipball_root's cache key): the archive is then
        # exactly that commit, not whatever the branch points at by the time the download starts.
        branch = ref or repo.default_branch
        url = f"{self._base_url}/repos/{repo.full_name}/zipball/{branch}"
        headers = {"Authorization": f"token {cfg.token}"} if cfg.token else {}
        zip_path = Path(zip_path)
        zip_path.parent.mkdir(parents=True, exist_ok=True)

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                resp = requests.get(
                    url, headers=headers, stream=True,
                    timeout=cfg.clone_timeout_seconds,
                    verify=cfg.ssl_verify,
                )
                resp.raise_for_status()
                with open(zip_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        f.write(chunk)
                last_exc = None
                break
            except SSLError as exc:
                raise RuntimeError(
                    f"SSL error downloading repo — {exc}\n"
                    "Fixes:\n"
                    "  • pip install --upgrade certifi\n"
                    "  • set REQUESTS_CA_BUNDLE=/path/to/your-ca-bundle.pem in .env\n"
                    "  • set GITHUB__SSL_VERIFY=false in .env to skip verification (insecure)"
                ) from exc
            except (ConnectionError, Timeout) as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(2 ** attempt)

        if last_exc:
            raise RuntimeError(
                f"Network error downloading repo after 3 attempts — {last_exc}"
            ) from last_exc

        return zip_path

    def _default_branch_sha(self, repo: Repository) -> str | None:
        """The default branch head, or None if it cannot be established.

        This is the cache key, and it is what makes a stale hit impossible: a
        cache keyed on the repo alone would serve yesterday's code silently.
        Measured at 0.49s against a 23.7s download, which is a good trade for
        never being quietly wrong.

        Returns None rather than raising — if GitHub cannot be asked, the
        caller falls back to downloading, which is the old behaviour. A
        correctness mechanism that can take the feature down with it is worse
        than the cost it saves.
        """
        try:
            # Delegates rather than reimplementing: `get_latest_commit_sha` was
            # already here (used by IncrementalIndexer and the CLI wizard) and
            # this method is only its error-handling wrapper. Two ways to ask
            # the same question is how they drift.
            return self.get_latest_commit_sha(repo)
        except Exception as exc:
            log.warning("could not resolve default-branch SHA for %s (%s) — "
                        "downloading uncached", getattr(repo, "full_name", "?"), exc)
            return None

    def zipball_root(self, repo: Repository, subproject_path: str | None = None):
        """Context manager: download `repo`'s zipball into a fresh tempdir
        and yield the extracted root (or subproject_path subdir).

        D6.7 (docs/unified-survey-execution-model-plan.md) — the one shared
        implementation of "download a zipball into a tempdir" that both
        repo_survey_definition_adapter.py's _acquire_zipball_root (D6's
        resolve_resources mechanism) and IngestionPipeline.refresh_profile()
        wrap, instead of each maintaining its own copy of this
        tempfile.TemporaryDirectory()+download_zipball() pattern. Deliberately
        NOT extended to cover IncrementalIndexer.refresh()'s own inline
        download — that one's entangled with a genuinely different decision
        (whole-repo vs. subproject root, driven by extra_docs_paths) that
        doesn't reduce to this same simple shape.
        """
        import tempfile
        import zipfile
        from contextlib import contextmanager
        from pathlib import Path

        from resource_explorer.github.source_cache import SourceCache

        @contextmanager
        def _cm():
            with tempfile.TemporaryDirectory() as tmp:
                # The ZIP is cached; the EXTRACTION is not. Measured
                # 2026-08-30: download 23.74s, extract 0.39s, so caching one
                # layer back removes 98% of the cost while every caller still
                # gets a private directory it can do what it likes with.
                # Handing out a shared extracted tree would let concurrent
                # surveys see each other's mutations.
                sha = self._default_branch_sha(repo)
                # The commit this zipball IS, for the run's step_runs rows: the cache key, and on a miss
                # the download is fetched BY that SHA (fetch_zipball ref=), so a push between the lookup
                # and the download cannot make the two differ. None on the uncached fallback: the SHA
                # could not be established, so it is not recorded rather than guessed.
                self.last_served_sha = sha
                if sha is None:
                    yield self.download_zipball(repo, Path(tmp), subproject_path)
                    return
                cache = SourceCache()
                zip_path = cache.get("zipball", repo.full_name, sha)
                if zip_path is None:
                    def _produce(target: Path) -> None:
                        self.fetch_zipball(repo, target, ref=sha)
                    zip_path = cache.put("zipball", repo.full_name, sha, _produce)
                extract_to = Path(tmp) / "x"
                with zipfile.ZipFile(zip_path) as z:
                    z.extractall(extract_to)
                root = next((d for d in extract_to.iterdir() if d.is_dir()), extract_to)
                if subproject_path:
                    sub = root / subproject_path
                    if not sub.is_dir():
                        # Same validation download_zipball() does on the
                        # uncached path — lost here originally, and silently:
                        # the cache path would have handed a caller a
                        # non-existent directory instead of this message.
                        raise ValueError(
                            f"Subproject path '{subproject_path}' does not exist in this repository. "
                            f"Available top-level directories: "
                            f"{[d.name for d in root.iterdir() if d.is_dir()]}"
                        )
                    yield sub
                else:
                    yield root

        return _cm()

    def clone_git_root(
        self,
        repo: Repository,
        dest_dir: "Path",
        shallow_since: str | None = None,
    ) -> "Path":
        """
        Clone `repo` into `dest_dir` as a **treeless** (`--filter=blob:none`)
        checkout and return the clone root. History is complete unless
        `shallow_since` bounds it; file *contents* are fetched from the
        remote lazily, on demand, rather than up front.

        This exists for co-change coupling (design §5.7 gap 2): a zipball
        (see download_zipball above) has no `.git` at all, and a shallow
        clone (`--depth`) truncates exactly the history co-change needs —
        neither works. Treeless is the one clone mode that is both
        affordable and history-complete: a full non-treeless clone of a
        large repo is the single largest cost this feature could incur.

        `--no-checkout` on top of `--filter=blob:none` is not optional: a
        normal clone checks out HEAD's working tree, which under a treeless
        filter means fetching every blob in HEAD's tree from the promisor
        remote during clone — silently paying for the exact thing
        `--filter=blob:none` exists to avoid. Without a working tree, the
        yielded root has `.git` and full commit/tree/rename metadata but no
        checked-out files; that's the right shape for `git log --name-only`
        (walks commit/tree objects, never a blob) and wrong for anything
        that wants to read file contents. Only `cochange.py` consumes this
        provider today, and it only ever runs `git log --name-only` — so
        the pairing is sound. It is worth restating for whoever adds the
        next consumer: reading file contents out of this root will trigger
        a blob fetch per file touched and erode the whole point of
        treeless.

        Auth mirrors download_zipball(): the same `GITHUB_TOKEN`
        (`cfg.token`), via `GIT_CONFIG_*` env vars rather than embedding the
        token in the clone URL or passing it as a CLI arg — either of those
        would leave it readable in this process's argv (e.g. to `ps`) or in
        a `.git/config` file inside the tempdir. `http.extraHeader` is the
        same "Authorization: token …" shape download_zipball() sends as an
        HTTP header, just handed to git via its config-from-env mechanism
        instead of httpx/requests.

        Time-bound: `cfg.clone_timeout_seconds` (default 300s, same knob
        download_zipball() uses) via subprocess timeout — a hung clone must
        fail the survey step, not wedge it.
        """
        import os
        import subprocess

        cfg = get_config().github
        args = [
            "git", "clone",
            "--filter=blob:none",
            "--no-checkout",
        ]
        if shallow_since:
            args.append(f"--shallow-since={shallow_since}")
        if not cfg.ssl_verify:
            args += ["-c", "http.sslVerify=false"]
        args += [repo.clone_url, str(dest_dir)]

        env = dict(os.environ)
        if cfg.token:
            # GIT_CONFIG_COUNT/_KEY_N/_VALUE_N is git's documented way to
            # inject config without a CLI arg or an on-disk file — see
            # git-config(1) "ENVIRONMENT". base64 per RFC 7617's Basic
            # scheme; "x-access-token" is the user GitHub expects a PAT to
            # authenticate as.
            import base64
            basic = base64.b64encode(f"x-access-token:{cfg.token}".encode()).decode()
            env["GIT_CONFIG_COUNT"] = "1"
            env["GIT_CONFIG_KEY_0"] = "http.extraHeader"
            env["GIT_CONFIG_VALUE_0"] = f"Authorization: Basic {basic}"
        env["GIT_TERMINAL_PROMPT"] = "0"  # never hang waiting for a password prompt

        try:
            proc = subprocess.run(
                args, capture_output=True, env=env,
                timeout=cfg.clone_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"git clone of {repo.full_name} timed out after "
                f"{cfg.clone_timeout_seconds}s"
            ) from exc

        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", "replace")
            raise RuntimeError(f"git clone of {repo.full_name} failed: {stderr}")

        return dest_dir

    def git_clone_root(self, repo: Repository, shallow_since: str | None = None):
        """Context manager: treeless-clone `repo` into a fresh tempdir and
        yield the clone root. Mirrors zipball_root() above — same
        tempfile.TemporaryDirectory() lifetime pattern, same "one shared
        implementation, callers wrap it" role for
        repo_survey_definition_adapter.py's _acquire_git_clone_root (D6's
        resolve_resources mechanism). See clone_git_root()'s docstring for
        why this is treeless+no-checkout rather than shallow or a full
        clone.
        """
        import tempfile
        from contextlib import contextmanager
        from pathlib import Path

        from resource_explorer.github.source_cache import SourceCache, local_clone

        @contextmanager
        def _cm():
            with tempfile.TemporaryDirectory() as tmp:
                dest = Path(tmp) / "repo"
                # Same shape as zipball_root: the CLONE is cached, the copy is
                # not. `git log` writes to `.git` for its own bookkeeping, so a
                # shared clone is a corruption risk between concurrent surveys,
                # not merely a leak. `clone --local` hardlinks — 0.28s measured
                # against 1.6s over the network.
                #
                # A `shallow_since` bound changes what history the clone
                # CONTAINS, so it must not share a key with an unbounded one.
                sha = self._default_branch_sha(repo)
                if sha is None or shallow_since:
                    root = self.clone_git_root(repo, dest, shallow_since=shallow_since)
                    self.last_served_sha = _clone_head(root)
                    yield root
                    return
                cache = SourceCache()
                cached = cache.get("gitclone", repo.full_name, sha)
                if cached is None:
                    def _produce(target: Path) -> None:
                        self.clone_git_root(repo, target, shallow_since=None)
                    cached = cache.put("gitclone", repo.full_name, sha, _produce)
                root = local_clone(cached, dest)
                # The clone's own HEAD, not the cache key: the cached clone was made from the branch, and
                # a push between the SHA lookup and the clone would make the key name a commit the clone
                # does not have checked out as HEAD.
                self.last_served_sha = _clone_head(root)
                yield root

        return _cm()

    def list_files(self, repo: Repository, path: str = "", recursive: bool = True) -> list[str]:
        """Return all file paths via git tree. Handles large repos where the recursive tree is truncated."""
        try:
            tree = repo.get_git_tree(repo.default_branch, recursive=True)
            if not tree.truncated:
                return [e.path for e in tree.tree if e.type == "blob"]
            # Truncated response is cut off mid-traversal — its entry list is incomplete.
            # Fetch root non-recursively to get all top-level entries, then walk each subtree.
            root = repo.get_git_tree(repo.default_branch, recursive=False)
            return self._list_files_walk(repo, root, "")
        except Exception:
            return []

    def _list_files_walk(self, repo: Repository, tree, prefix: str) -> list[str]:
        """Recursively collect file paths by fetching each subtree entry individually."""
        paths = [e.path for e in tree.tree if e.type == "blob"]
        for entry in tree.tree:
            if entry.type != "tree":
                continue
            entry_prefix = f"{prefix}/{entry.name}" if prefix else entry.name
            try:
                subtree = repo.get_git_tree(entry.sha, recursive=True)
                if not subtree.truncated:
                    paths.extend(f"{entry_prefix}/{e.path}" for e in subtree.tree if e.type == "blob")
                else:
                    paths.extend(self._list_files_walk(repo, subtree, entry_prefix))
            except Exception:
                pass
        return paths

    def list_file_modes(self, repo: Repository) -> dict[str, str]:
        """Return {path: mode} for every blob in the repo's git tree —
        "100644" regular, "100755" executable, "120000" symlink. Mirrors
        list_files()'s truncation handling (mode is free on the exact same
        tree entries list_files() already walks, just discarded there) but
        kept as its own method rather than folded into list_files() to
        avoid changing that method's existing, widely-used return shape.
        Best-effort: returns {} on any failure, matching list_files()'s own
        fail-soft behavior — a missing mode should never fail an inventory
        refresh (Assessment sub-resource cataloging plan, D9 Tier 1)."""
        try:
            tree = repo.get_git_tree(repo.default_branch, recursive=True)
            if not tree.truncated:
                return {e.path: e.mode for e in tree.tree if e.type == "blob"}
            root = repo.get_git_tree(repo.default_branch, recursive=False)
            return self._list_file_modes_walk(repo, root, "")
        except Exception:
            return {}

    def _list_file_modes_walk(self, repo: Repository, tree, prefix: str) -> dict[str, str]:
        modes = {e.path: e.mode for e in tree.tree if e.type == "blob"}
        for entry in tree.tree:
            if entry.type != "tree":
                continue
            entry_prefix = f"{prefix}/{entry.name}" if prefix else entry.name
            try:
                subtree = repo.get_git_tree(entry.sha, recursive=True)
                if not subtree.truncated:
                    modes.update({f"{entry_prefix}/{e.path}": e.mode for e in subtree.tree if e.type == "blob"})
                else:
                    modes.update(self._list_file_modes_walk(repo, subtree, entry_prefix))
            except Exception:
                pass
        return modes

    def get_file_content(self, repo: Repository, path: str) -> str | None:
        try:
            return repo.get_contents(path).decoded_content.decode("utf-8", errors="ignore")
        except GithubException:
            return None

    def list_root_file_names(self, repo: Repository) -> list[str]:
        """Names of the files (not directories) at the repo root: ONE contents-API call.

        Scouting uses this to fetch only the manifests and README that actually
        exist, instead of probing for each name and paying a 404 per absent one.
        Raises on API failure; callers must tell "could not list" from "empty".
        """
        return [c.name for c in repo.get_contents("") if getattr(c, "type", "file") == "file"]

    def get_default_branch(self, repo: Repository) -> str:
        return repo.default_branch

    def get_latest_commit_sha(self, repo: Repository) -> str:
        return repo.get_commits()[0].sha

    def rest_get(self, path: str, params: dict | None = None) -> tuple[dict, Any]:
        """ONE GET against the REST API, returned as GitHub sent it: `(headers, json)`.

        For reads that must count their calls (the contributor lists): every call here is exactly one request,
        and no PyGitHub object is built from the answer, so no attribute read can trigger a hidden completion call.
        The requester is built WITHOUT PyGitHub's retry, whose default sleeps until the rate limit resets on a
        403; here a 403 (or any error status) raises `GithubException` at once, with its status and headers, and
        the caller stops cleanly. `json` is None for a 204 (GitHub's answer for an empty repository's
        contributors)."""
        return self._gh_once.requester.requestJsonAndCheck("GET", path, parameters=dict(params or {}))

    @cached_property
    def _gh_once(self) -> Github:
        cfg = get_config().github
        return Github(cfg.token or None, base_url=self._base_url, per_page=100, retry=None)

    def check_rate_limit(self) -> dict:
        rate = self._gh.get_rate_limit()
        core = getattr(rate, "core", None) or rate.rate
        return {
            "remaining": core.remaining,
            "limit": core.limit,
            "reset_at": core.reset.isoformat(),
        }

    @staticmethod
    def _url_to_slug(url: str) -> str:
        url = url.rstrip("/")
        if url.endswith(".git"):
            url = url[:-4]
        if "github.com/" in url:
            return url.split("github.com/")[-1]
        return url


def _clone_head(root) -> str | None:
    """HEAD of a clone RE just made, or None when it cannot be read (then "not recorded")."""
    import subprocess
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                             text=True, timeout=30)
        sha = out.stdout.strip()
        return sha if out.returncode == 0 and len(sha) >= 40 else None
    except Exception:
        return None
