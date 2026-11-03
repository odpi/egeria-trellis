"""
Tests for `python -m advisor.incremental_indexer --collection X`.

Background (running box, 2026-09-10): egeria_concepts, egeria_types and
egeria_general were all empty tables while the Admin panel showed them
freshly indexed. The Admin "Incremental re-index" button runs

    python -m advisor.incremental_indexer --collection <name>

(advisor/web/admin.py) and the module had no __main__ block and no argparse
at all -- so the command imported the module, did nothing, and exited 0.
_run_job() reads exit 0 as success and stamps ingest_log, so a reindex that
ingested nothing looked like a fresh one. Only the --force path, which shells
to scripts/ingest_collections.py, ever worked.

Covers:
  - the module is runnable and rejects an unknown collection instead of
    exiting 0 silently
  - build_indexer() carries the collection's chunking AND exclude_patterns
    across, so an incremental run does not re-add files a full ingest excludes
  - a no-change run exits 0; a failed update exits 1
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest

from advisor import collection_sources, incremental_indexer
from advisor.collection_config import EGERIA_GENERAL_COLLECTION

_DOCS_LAYOUT = [
    "site/docs/index.md",
    "site/docs/guides/getting-started.md",
    "site/docs/concepts/asset.md",
    "site/docs/types/0/0010-base-model.md",
]


@pytest.fixture
def docs_repo(tmp_path, monkeypatch) -> Path:
    repos = tmp_path / "repos"
    repo = repos / "egeria-docs"
    for rel in _DOCS_LAYOUT:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# doc\n")
    monkeypatch.setattr(collection_sources, "get_repos_dir", lambda: repos)
    return repos


class _StubIndexer:
    """Captures construction args; stands in for the real IncrementalIndexer."""

    last: "_StubIndexer | None" = None

    def __init__(self, collection_name, source_paths, file_patterns,
                 chunk_size=1000, chunk_overlap=200, exclude_patterns=None):
        self.collection_name = collection_name
        self.source_paths = source_paths
        self.file_patterns = file_patterns
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.exclude_patterns = list(exclude_patterns or [])
        _StubIndexer.last = self


def test_module_is_runnable_at_all():
    """The regression itself: `-m advisor.incremental_indexer` must have a
    __main__ block. Without one it exits 0 having done nothing."""
    assert hasattr(incremental_indexer, "main")
    source = Path(incremental_indexer.__file__).read_text()
    assert '__name__ == "__main__"' in source


def test_unknown_collection_exits_nonzero(caplog):
    assert incremental_indexer.main(["--collection", "no_such_collection"]) == 1


def test_missing_repo_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.setattr(collection_sources, "get_repos_dir", lambda: tmp_path / "nowhere")
    assert incremental_indexer.main(["--collection", "egeria_general"]) == 1


def test_collection_requires_an_argument():
    with pytest.raises(SystemExit):
        incremental_indexer.main([])


def test_build_indexer_carries_exclusions_and_chunking(docs_repo, monkeypatch):
    monkeypatch.setattr(incremental_indexer, "IncrementalIndexer", _StubIndexer)
    indexer = incremental_indexer.build_indexer("egeria_general")

    assert indexer.collection_name == "egeria_general"
    assert indexer.file_patterns == ["*.md"]
    assert indexer.chunk_size == EGERIA_GENERAL_COLLECTION.chunk_size
    assert indexer.chunk_overlap == EGERIA_GENERAL_COLLECTION.chunk_overlap
    # Without these an incremental run re-adds the concepts/ and types/ trees
    # that the full ingest is configured to exclude.
    assert indexer.exclude_patterns == list(EGERIA_GENERAL_COLLECTION.exclude_patterns)
    assert "**/concepts/**" in indexer.exclude_patterns


def test_find_files_applies_the_exclusions(docs_repo):
    real = incremental_indexer.IncrementalIndexer.__new__(
        incremental_indexer.IncrementalIndexer
    )
    real.source_paths = [docs_repo / "egeria-docs" / "site" / "docs"]
    real.file_patterns = ["*.md"]
    real.exclude_patterns = list(EGERIA_GENERAL_COLLECTION.exclude_patterns)

    names = {p.name for p in real._find_files()}
    assert names == {"index.md", "getting-started.md"}
    assert "asset.md" not in names and "0010-base-model.md" not in names


class _FakeChangeSet:
    def __init__(self, changes: bool):
        self.new_files: list[Path] = [Path("a.md")] if changes else []
        self.modified_files: list[Path] = []
        self.deleted_files: list[Path] = []
        self.unchanged_files: list[Path] = []

    @property
    def has_changes(self) -> bool:
        return bool(self.new_files)


class _FakeResult:
    def __init__(self, success: bool):
        self.success = success
        self.error = None if success else "boom"
        self.chunks_added = 4 if success else 0
        self.chunks_removed = 0
        self.duration = 0.1


def _wire(monkeypatch, changes: bool, success: bool = True):
    class _Indexer:
        def detect_changes(self):
            return _FakeChangeSet(changes)

        def apply_updates(self, changeset, dry_run=False):
            return _FakeResult(success)

    monkeypatch.setattr(incremental_indexer, "build_indexer", lambda name: _Indexer())


def test_no_changes_exits_zero(monkeypatch):
    _wire(monkeypatch, changes=False)
    assert incremental_indexer.main(["--collection", "egeria_general"]) == 0


def test_successful_update_exits_zero(monkeypatch):
    _wire(monkeypatch, changes=True, success=True)
    assert incremental_indexer.main(["--collection", "egeria_general"]) == 0


def test_failed_update_exits_nonzero(monkeypatch):
    """The case that most needs a non-zero exit: _run_job() stamps ingest_log
    on exit 0, so a silent failure reads as a fresh index."""
    _wire(monkeypatch, changes=True, success=False)
    assert incremental_indexer.main(["--collection", "egeria_general"]) == 1


# ---------------------------------------------------------------------------
# The untracked-duplication guard
#
# scripts/ingest_collections.py writes chunks but no FileTracker rows, so a
# freshly full-ingested collection has 0 tracked files. Without this guard the
# now-working entry point would re-add every file: egeria_concepts, at 720
# chunks, reported "179 new, 0 unchanged" on 2026-09-10.
# ---------------------------------------------------------------------------

class _Tracker:
    def __init__(self, tracked):
        self._tracked = tracked

    def get_tracked_files(self, collection_name):
        return self._tracked


class _Store:
    def __init__(self, rows):
        self.rows = rows

    def collection_exists(self, name):
        return name in self.rows

    def get_collection_stats(self, name):
        return {"num_entities": self.rows.get(name, 0)}


class _Probe:
    def __init__(self, tracked, rows):
        self.tracker = _Tracker(tracked)
        self.vector_store = _Store(rows)


def test_guard_fires_for_populated_but_untracked_collection():
    probe = _Probe(tracked={}, rows={"egeria_concepts": 720})
    assert incremental_indexer.would_duplicate_untracked(probe, "egeria_concepts")


def test_guard_silent_when_files_are_tracked():
    probe = _Probe(tracked={"/a.md": {}}, rows={"egeria_concepts": 720})
    assert not incremental_indexer.would_duplicate_untracked(probe, "egeria_concepts")


def test_guard_silent_for_an_empty_collection():
    # Nothing to duplicate: this is the legitimate first incremental run.
    probe = _Probe(tracked={}, rows={"egeria_concepts": 0})
    assert not incremental_indexer.would_duplicate_untracked(probe, "egeria_concepts")


def test_guard_silent_when_indexer_exposes_no_tracker():
    assert not incremental_indexer.would_duplicate_untracked(object(), "egeria_concepts")


def _wire_probe(monkeypatch, tracked, rows):
    class _Indexer(_Probe):
        def __init__(self):
            super().__init__(tracked, rows)
            self.applied = False

        def detect_changes(self):
            return _FakeChangeSet(True)

        def apply_updates(self, changeset, dry_run=False):
            self.applied = True
            return _FakeResult(True)

    made = _Indexer()
    monkeypatch.setattr(incremental_indexer, "build_indexer", lambda name: made)
    return made


def test_cli_refuses_to_duplicate_and_does_not_apply(monkeypatch):
    made = _wire_probe(monkeypatch, tracked={}, rows={"egeria_concepts": 720})
    assert incremental_indexer.main(["--collection", "egeria_concepts"]) == 1
    assert not made.applied, "the guard must stop before apply_updates()"


def test_allow_untracked_overrides_the_guard(monkeypatch):
    made = _wire_probe(monkeypatch, tracked={}, rows={"egeria_concepts": 720})
    assert incremental_indexer.main(
        ["--collection", "egeria_concepts", "--allow-untracked"]
    ) == 0
    assert made.applied


def test_dry_run_is_not_blocked_by_the_guard(monkeypatch):
    made = _wire_probe(monkeypatch, tracked={}, rows={"egeria_concepts": 720})
    assert incremental_indexer.main(
        ["--collection", "egeria_concepts", "--dry-run"]
    ) == 0
    assert made.applied, "dry run reports through apply_updates(), which writes nothing"
