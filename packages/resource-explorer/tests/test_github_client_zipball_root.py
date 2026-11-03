"""Tests for GitHubClient.zipball_root() — D6.7 (docs/unified-survey-
execution-model-plan.md), the one shared download+tempdir implementation
repo_survey_definition_adapter.py's _acquire_zipball_root and
IngestionPipeline.refresh_profile() both wrap now, instead of each keeping
its own copy of this pattern.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from resource_explorer.github.client import GitHubClient


def _client(sha=None):
    """A client with no __init__ (no network, no config), and with the cache
    key resolution stubbed.

    `sha=None` is the UNCACHEABLE path — the SHA could not be resolved, so
    `zipball_root` falls back to `download_zipball`, which is the behaviour
    these tests were originally written against and which must survive.
    A real SHA takes the cache path; see TestZipballRootCaching.
    """
    c = GitHubClient.__new__(GitHubClient)
    c._default_branch_sha = lambda repo: sha
    return c


class TestZipballRoot:
    def test_yields_download_zipball_result_and_cleans_up_tempdir(self):
        client = _client()
        repo = MagicMock()
        captured_dest = {}

        def _fake_download(repo_arg, dest_dir, subproject_path=None):
            captured_dest["dir"] = dest_dir
            assert dest_dir.is_dir()  # a real, existing tempdir
            return dest_dir / "extracted-root"

        client.download_zipball = _fake_download

        with client.zipball_root(repo) as root:
            assert root == captured_dest["dir"] / "extracted-root"
            assert captured_dest["dir"].is_dir()

        # tempdir removed after the context manager exits
        assert not captured_dest["dir"].exists()

    def test_forwards_subproject_path(self):
        client = _client()
        repo = MagicMock()
        calls = []

        def _fake_download(repo_arg, dest_dir, subproject_path=None):
            calls.append((repo_arg, subproject_path))
            return dest_dir

        client.download_zipball = _fake_download

        with client.zipball_root(repo, "sub/dir"):
            pass

        assert calls == [(repo, "sub/dir")]

    def test_download_error_propagates_and_still_cleans_up(self):
        client = _client()
        repo = MagicMock()
        captured_dest = {}

        def _fake_download(repo_arg, dest_dir, subproject_path=None):
            captured_dest["dir"] = dest_dir
            raise RuntimeError("network error")

        client.download_zipball = _fake_download

        try:
            with client.zipball_root(repo):
                pass
            assert False, "expected RuntimeError to propagate"
        except RuntimeError:
            pass

        assert not captured_dest["dir"].exists()


class TestZipballRootCaching:
    """The cache path: a resolvable SHA routes through SourceCache instead of
    download_zipball directly. Referenced (but not written) by the comment
    in _client() above until now — the exact gap dwolfson-59 flagged in
    review ("the cache path is covered by the new file instead," meaning
    test_source_cache.py exercises SourceCache/local_clone directly, not
    this integration seam). Writing it here surfaced a real regression,
    fixed alongside these tests: the cached path built `root / subproject_path`
    with no existence check, silently handing a caller a non-existent
    directory instead of download_zipball's ValueError.

    `zipball_root()` does `from resource_explorer.github.source_cache import
    SourceCache` as a LOCAL import inside the method, re-resolved on every
    call — patching `SourceCache` on `client.py`'s module namespace is a
    no-op, since that name never lives there. It must be patched where the
    class actually lives, `source_cache.SourceCache`, which the local import
    reads at call time.

    A real zip file (not a mock) so extraction genuinely exercises
    zipfile.ZipFile, matching how the two are wired in production.
    """

    @staticmethod
    def _make_zip(path, root_name="owner-repo-abc123", files=("f.txt",), subdirs=()):
        import zipfile
        with zipfile.ZipFile(path, "w") as zf:
            for f in files:
                zf.writestr(f"{root_name}/{f}", "content")
            for d in subdirs:
                zf.writestr(f"{root_name}/{d}/.keep", "")

    @staticmethod
    def _patch_cache_dir(monkeypatch, tmp_path):
        """Every SourceCache() constructed inside zipball_root() during this
        test lands under tmp_path — isolated from the real, gitignored
        data/source-cache/ a bare SourceCache() would otherwise resolve to."""
        import resource_explorer.github.source_cache as cache_mod

        real_cls = cache_mod.SourceCache
        monkeypatch.setattr(cache_mod, "SourceCache",
                             lambda: real_cls(cache_dir=tmp_path / "cache"))
        return real_cls(cache_dir=tmp_path / "cache")  # a handle for pre-population

    def test_cache_miss_downloads_once_and_extracts(self, tmp_path, monkeypatch):
        self._patch_cache_dir(monkeypatch, tmp_path)

        client = _client(sha="deadbeef")
        repo = MagicMock()
        repo.full_name = "test/myproj"
        calls = []

        def _fake_fetch(repo_arg, zip_path, ref=None):
            calls.append(zip_path)
            self._make_zip(zip_path)
            return zip_path

        client.fetch_zipball = _fake_fetch

        with client.zipball_root(repo) as root:
            assert root.name == "owner-repo-abc123"
            assert (root / "f.txt").exists()

        assert len(calls) == 1, "fetch_zipball must run exactly once on a miss"

    def test_cache_hit_never_calls_fetch(self, tmp_path, monkeypatch):
        cache = self._patch_cache_dir(monkeypatch, tmp_path)

        def _produce(target):
            self._make_zip(target)
        cache.put("zipball", "test/myproj", "deadbeef", _produce)

        client = _client(sha="deadbeef")
        repo = MagicMock()
        repo.full_name = "test/myproj"

        def _fail_if_called(*a, **k):
            raise AssertionError("fetch_zipball must not run on a cache hit")
        client.fetch_zipball = _fail_if_called

        with client.zipball_root(repo) as root:
            assert (root / "f.txt").exists()

    def test_two_calls_share_one_download(self, tmp_path, monkeypatch):
        """The whole point of the cache: a second acquisition of the same
        (repo, sha) does not re-download."""
        self._patch_cache_dir(monkeypatch, tmp_path)

        client = _client(sha="deadbeef")
        repo = MagicMock()
        repo.full_name = "test/myproj"
        calls = []

        def _fake_fetch(repo_arg, zip_path, ref=None):
            calls.append(zip_path)
            self._make_zip(zip_path)
            return zip_path

        client.fetch_zipball = _fake_fetch

        with client.zipball_root(repo):
            pass
        with client.zipball_root(repo):
            pass

        assert len(calls) == 1

    def test_subproject_path_is_joined_on_the_cached_root(self, tmp_path, monkeypatch):
        self._patch_cache_dir(monkeypatch, tmp_path)

        client = _client(sha="deadbeef")
        repo = MagicMock()
        repo.full_name = "test/myproj"

        def _fake_fetch(repo_arg, zip_path, ref=None):
            self._make_zip(zip_path, subdirs=("sub/dir",))
            return zip_path

        client.fetch_zipball = _fake_fetch

        with client.zipball_root(repo, "sub/dir") as root:
            assert root.name == "dir"
            assert root.parent.name == "sub"

    def test_missing_subproject_path_raises_with_available_dirs(self, tmp_path, monkeypatch):
        """The regression this test class exists to catch: the cached path
        used to build `root / subproject_path` with no existence check at
        all, silently handing a caller a directory that does not exist
        instead of download_zipball's ValueError."""
        self._patch_cache_dir(monkeypatch, tmp_path)

        client = _client(sha="deadbeef")
        repo = MagicMock()
        repo.full_name = "test/myproj"

        def _fake_fetch(repo_arg, zip_path, ref=None):
            self._make_zip(zip_path, subdirs=("actual",))
            return zip_path

        client.fetch_zipball = _fake_fetch

        with pytest.raises(ValueError, match="does not exist"):
            with client.zipball_root(repo, "missing"):
                pass
