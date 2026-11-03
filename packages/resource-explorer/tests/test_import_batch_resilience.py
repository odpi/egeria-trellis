"""A batch import must not stop at the first repo.

Observed 2026-08-21: a user selected many repos, the first (milvus) was
registered, and the rest silently were not. The evidence was the absence of
evidence — milvus appeared in `projects` with no activity entry, and no "batch
complete" summary, which every batch writes unconditionally.

The cause was structural, not incidental. `log_scout` sat *inside* the same try
as `import_repo`, so a registry write failure ("server closed the connection
unexpectedly" — the known Egeria-side issue) was caught by the except handler,
which called `log_scout` again. That second call raised too, escaped the loop,
and killed the daemon thread. Nothing was left to report it: a daemon thread's
exception goes nowhere.

Two separate wrongs, both fixed here:
  * The audit trail was allowed to end the work it describes.
  * A logging failure was counted and reported as an *import* failure, for a
    repo that had actually been registered.
"""
from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

import resource_explorer.github.org_importer as oi


class _FakeImporter:
    def __init__(self, registry):
        self.imported: list[str] = []

    def import_repo(self, url, name, description="", group_slug=""):
        self.imported.append(url)
        return object()


@pytest.fixture
def repos():
    return [{"github_url": f"https://github.com/o/r{i}", "display_name": f"r{i}"}
            for i in range(5)]


@pytest.fixture(autouse=True)
def _quiet():
    logging.disable(logging.ERROR)
    yield
    logging.disable(logging.NOTSET)


def _run(repos, log_scout_impl, import_impl=None):
    """Run a batch with logging and importing stubbed, returning what happened."""
    state = {"imported": [], "logged": []}

    class Importer(_FakeImporter):
        def import_repo(self, url, name, description="", group_slug=""):
            if import_impl:
                import_impl(url)
            state["imported"].append(url)
            return object()

    def _log(registry, **kwargs):
        log_scout_impl(kwargs)
        state["logged"].append(kwargs)

    with patch.object(oi, "OrgImporter", Importer), \
         patch.object(oi, "ProjectRegistry", lambda *a, **k: object()), \
         patch.object(oi, "log_scout", _log):
        oi._run_import_batch("test", repos, "")
    return state


class TestLoggingCannotEndTheBatch:

    def test_every_repo_is_attempted_when_all_logging_fails(self, repos):
        """The exact reproduction of the report: total logging failure used to
        leave one repo registered and four never attempted."""
        def always_fail(_kwargs):
            raise RuntimeError("server closed the connection unexpectedly")

        state = _run(repos, always_fail)
        assert len(state["imported"]) == 5

    def test_logging_that_fails_only_after_the_first_repo(self, repos):
        """The real shape: a connection dies partway through, not at the start."""
        seen = {"n": 0}

        def fail_after_first(_kwargs):
            seen["n"] += 1
            if seen["n"] > 1:
                raise RuntimeError("connection closed")

        assert len(_run(repos, fail_after_first)["imported"]) == 5

    def test_the_batch_summary_is_still_attempted(self, repos):
        """Its absence was the clue that the thread had died; it must be written
        (or at least attempted) even when per-repo logging is failing."""
        attempts = []

        def record(kwargs):
            attempts.append(kwargs.get("summary", ""))

        _run(repos, record)
        assert any("batch complete" in s.lower() for s in attempts)


class TestFailuresAreAttributedCorrectly:

    def test_a_logging_failure_is_not_reported_as_an_import_failure(self, repos):
        """It was: import_repo succeeded, log_scout raised, and the except
        handler counted the repo as failed — while it sat registered in the
        database. The batch summary then lied about what had happened."""
        summaries = []

        def fail_success_logs(kwargs):
            summaries.append(kwargs.get("summary", ""))
            if kwargs.get("status") == "ok" and "batch complete" not in kwargs.get("summary", ""):
                raise RuntimeError("connection closed")

        _run(repos, fail_success_logs)
        batch = [s for s in summaries if "batch complete" in s.lower()]
        assert batch and "5 imported, 0 already registered, 0 failed" in batch[0], (
            f"logging failures were miscounted as import failures: {batch}")

    def test_a_real_import_failure_still_counts_and_continues(self, repos):
        """The guard must not swallow genuine failures — one bad repo is
        reported and the rest still run."""
        def fail_third(url):
            if url.endswith("r2"):
                raise RuntimeError("404 repo not found")

        state = _run(repos, lambda k: None, import_impl=fail_third)
        assert len(state["imported"]) == 4
        batch = [k for k in state["logged"] if "batch complete" in k.get("summary", "").lower()]
        assert "4 imported, 0 already registered, 1 failed" in batch[0]["summary"]

    def test_a_failing_repo_does_not_stop_the_ones_after_it(self, repos):
        def fail_first(url):
            if url.endswith("r0"):
                raise RuntimeError("boom")

        assert len(_run(repos, lambda k: None, import_impl=fail_first)["imported"]) == 4
