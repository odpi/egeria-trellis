"""The session-level check that a test run leaves the tracked tree alone.

The real check lives in conftest (pytest_sessionstart/finish); these tests prove the
mechanism by making it fail on purpose in a throwaway git repository.
"""
import shutil
import subprocess

import pytest

from tests import tracked_tree_guard as T

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.md").write_text("one")
    (tmp_path / "b.md").write_text("two")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_untouched_tree_reports_nothing(repo):
    assert T.changed(T.snapshot(repo), T.snapshot(repo)) == []


def test_an_edit_to_a_clean_tracked_file_is_seen(repo):
    before = T.snapshot(repo)
    (repo / "a.md").write_text("changed")
    assert T.changed(before, T.snapshot(repo)) == ["a.md"]


def test_a_further_edit_to_an_already_dirty_file_is_seen(repo):
    (repo / "a.md").write_text("dev edit")
    before = T.snapshot(repo)
    (repo / "a.md").write_text("test edit")
    assert T.changed(before, T.snapshot(repo)) == ["a.md"]


def test_a_restored_edit_leaves_no_trace(repo):
    before = T.snapshot(repo)
    (repo / "a.md").write_text("x")
    (repo / "a.md").write_text("one")
    assert T.changed(before, T.snapshot(repo)) == []


def test_a_new_file_and_a_deleted_file_are_seen(repo):
    before = T.snapshot(repo)
    (repo / "new.md").write_text("n")
    (repo / "b.md").unlink()
    assert T.changed(before, T.snapshot(repo)) == ["b.md", "new.md"]


def test_no_git_means_no_check(tmp_path):
    assert T.snapshot(tmp_path) is None
    assert T.changed(None, {"x": "y"}) == []
